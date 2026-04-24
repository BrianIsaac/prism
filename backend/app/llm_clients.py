"""Direct provider SDK layer for Prism.

Three frontier providers behind a single :func:`call_llm` dispatch —
Anthropic (Claude Opus 4.7), OpenAI (GPT 5.5), and Google GenAI
(Gemini 3.1 Pro Preview). Each provider has native knobs that benefit
the race:

- Opus 4.7: ``output_config.format`` for structured JSON output, and
  no temperature parameter (4.7 rejects it outright).
- GPT 5.5: ``max_completion_tokens`` on Chat Completions; reasoning
  effort stays at default for tool-use continuity.
- Gemini 3.1 Pro Preview: ``thinking_level="LOW"`` via native
  :class:`genai_types.GenerateContentConfig` (3x faster, no quality loss
  for POI selection; thinking cannot be fully disabled on 3.x Pro).

Every provider call is wrapped in tenacity with exponential backoff so a
transient 5xx, rate limit, or connection error retries up to three times
before surfacing — without the wrapper those errors abort an agent's
entire race contribution.

Internal message format is OpenAI-style (single list of dicts with
``role`` and ``content``, tool calls on assistant messages, ``role=tool``
for tool results). Each provider's converter maps to and from this shape.
Gemini 3.1 Pro in particular requires a ``thought_signature`` to round-
trip through tool-use turns — stored in
:attr:`ToolCallRequest.provider_metadata` and reattached on the next turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from anthropic import (
    APIConnectionError as AnthropicAPIConnectionError,
    APIStatusError as AnthropicAPIStatusError,
    AsyncAnthropic,
    InternalServerError as AnthropicInternalServerError,
    RateLimitError as AnthropicRateLimitError,
)
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from openai import (
    APIConnectionError as OpenAIAPIConnectionError,
    APIStatusError as OpenAIAPIStatusError,
    AsyncOpenAI,
    InternalServerError as OpenAIInternalServerError,
    RateLimitError as OpenAIRateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("prism")

Provider = Literal["anthropic", "openai", "gemini"]


# ---------- Lazy SDK client singletons ----------


_anthropic_client: AsyncAnthropic | None = None
_openai_client: AsyncOpenAI | None = None
_gemini_client: genai.Client | None = None


def _anthropic() -> AsyncAnthropic:
    """Return the process-wide AsyncAnthropic client, creating it lazily."""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _openai() -> AsyncOpenAI:
    """Return the process-wide AsyncOpenAI client, creating it lazily."""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client


def _gemini() -> genai.Client:
    """Return the process-wide Gemini client, creating it lazily."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    return _gemini_client


# ---------- Transient-error predicates for tenacity ----------


def _is_anthropic_transient(exc: BaseException) -> bool:
    """Return True for Anthropic errors worth retrying (network / 5xx / RL)."""
    if isinstance(
        exc,
        (
            AnthropicRateLimitError,
            AnthropicAPIConnectionError,
            AnthropicInternalServerError,
        ),
    ):
        return True
    if isinstance(exc, AnthropicAPIStatusError):
        return 500 <= getattr(exc, "status_code", 0) < 600
    return False


def _is_openai_transient(exc: BaseException) -> bool:
    """Return True for OpenAI errors worth retrying."""
    if isinstance(
        exc,
        (OpenAIRateLimitError, OpenAIAPIConnectionError, OpenAIInternalServerError),
    ):
        return True
    if isinstance(exc, OpenAIAPIStatusError):
        return 500 <= getattr(exc, "status_code", 0) < 600
    return False


def _is_gemini_transient(exc: BaseException) -> bool:
    """Return True for Gemini errors worth retrying.

    The Gemini SDK surfaces a single :class:`errors.APIError` with a numeric
    ``code`` attribute; 429 and 5xx are retryable, 4xx (excluding 429) are
    not.
    """
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", 0)
        return code == 429 or (500 <= code < 600)
    if isinstance(exc, (ConnectionError, asyncio.TimeoutError)):
        return True
    return False


def _retry(predicate):
    """Decorator factory: 3 attempts, 2-30s exponential backoff."""
    return retry(
        retry=retry_if_exception(predicate),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )


# ---------- Unified response + tool-call shapes ----------


@dataclass
class ToolCallRequest:
    """Normalised tool-call record emitted by any provider.

    ``provider_metadata`` carries opaque per-provider blobs that must be
    echoed back in the next turn's message history. Gemini 3.1 Pro in
    particular attaches a ``thought_signature`` to every function_call
    part and errors 400 if the signature is missing on subsequent tool-
    result turns. OpenAI / Anthropic do not currently use this field.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    provider_metadata: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    """Provider-agnostic response shape consumed by the agent runner."""

    content: str
    tool_calls: list[ToolCallRequest]
    stop_reason: str


# ---------- Tool-schema translators ----------


def _openai_tools_to_anthropic(
    openai_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten ``[{type: function, function: {...}}, ...]`` into Anthropic shape.

    Anthropic wants a flat ``[{name, description, input_schema}, ...]`` list.
    """
    out: list[dict[str, Any]] = []
    for t in openai_tools:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {
                    "type": "object",
                    "properties": {},
                },
            }
        )
    return out


def _sanitize_gemini_schema(schema: Any) -> Any:
    """Strip schema fields Gemini's FunctionDeclaration parser rejects.

    Gemini accepts a strict OpenAPI 3.0 subset. Known rejections:
        - ``$ref`` / ``$schema`` (references not supported)
        - ``additionalProperties`` (ignored in most positions, errors in some)
        - ``const`` (use ``enum: [value]`` instead)
    """
    if not isinstance(schema, dict):
        if isinstance(schema, list):
            return [_sanitize_gemini_schema(v) for v in schema]
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("$ref", "$schema", "additionalProperties"):
            continue
        if isinstance(v, (dict, list)):
            out[k] = _sanitize_gemini_schema(v)
        else:
            out[k] = v
    return out


def _openai_tools_to_gemini(
    openai_tools: list[dict[str, Any]],
) -> list[genai_types.Tool]:
    """Build Gemini ``Tool(function_declarations=[...])`` from OpenAI shape."""
    decls: list[genai_types.FunctionDeclaration] = []
    for t in openai_tools:
        fn = t.get("function", t)
        params = _sanitize_gemini_schema(fn.get("parameters") or {"type": "object"})
        decls.append(
            genai_types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=params,
            )
        )
    return [genai_types.Tool(function_declarations=decls)]


# ---------- Message-format converters ----------


def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Extract a leading system message into a (system_text, rest) tuple."""
    if messages and messages[0].get("role") == "system":
        return messages[0].get("content", "") or "", messages[1:]
    return "", list(messages)


def _messages_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI-ish messages to Anthropic system + messages array.

    Anthropic wants ``system`` at the top level (not a message), assistant
    messages with ``tool_use`` blocks, and user messages with ``tool_result``
    blocks following any ``tool_use``.
    """
    system_text, rest = _split_system(messages)
    out: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def _flush() -> None:
        """Flush pending tool_result blocks into a single user message."""
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in rest:
        role = m.get("role")
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
            continue
        _flush()
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = m.get("content", "") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": args,
                    }
                )
            out.append({"role": "assistant", "content": blocks or ""})
        else:
            out.append({"role": "user", "content": m.get("content", "") or ""})
    _flush()
    return system_text, out


def _messages_to_gemini(
    messages: list[dict[str, Any]],
) -> tuple[str, list[genai_types.Content]]:
    """Convert OpenAI-ish messages to Gemini system + contents list.

    Gemini expects ``system_instruction`` at config level, alternating
    user/model :class:`~genai_types.Content` objects, ``functionCall`` parts
    in model turns, and ``functionResponse`` parts in user turns.
    """
    system_text, rest = _split_system(messages)
    contents: list[genai_types.Content] = []
    pending_fn_responses: list[genai_types.Part] = []

    def _flush() -> None:
        """Flush pending function-response parts into a single user Content."""
        if pending_fn_responses:
            contents.append(
                genai_types.Content(role="user", parts=list(pending_fn_responses))
            )
            pending_fn_responses.clear()

    for m in rest:
        role = m.get("role")
        if role == "tool":
            content = m.get("content", "") or ""
            try:
                payload = json.loads(content) if content else {}
            except json.JSONDecodeError:
                payload = {"raw": content}
            if not isinstance(payload, dict):
                payload = {"result": payload}
            pending_fn_responses.append(
                genai_types.Part.from_function_response(
                    name=m.get("name") or "tool",
                    response=payload,
                )
            )
            continue
        _flush()
        if role == "assistant":
            parts: list[genai_types.Part] = []
            text = m.get("content", "") or ""
            if text:
                parts.append(genai_types.Part(text=text))
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                part = genai_types.Part.from_function_call(
                    name=tc["function"]["name"],
                    args=args,
                )
                # Restore Gemini's thought_signature if we captured one from
                # the original response — required by 3.1 Pro on every
                # follow-up turn, else 400 INVALID_ARGUMENT.
                metadata = (tc.get("function") or {}).get("provider_metadata") or {}
                sig = metadata.get("thought_signature")
                if sig is not None:
                    part.thought_signature = sig
                parts.append(part)
            if not parts:
                parts = [genai_types.Part(text="")]
            contents.append(genai_types.Content(role="model", parts=parts))
        else:
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=m.get("content", "") or "")],
                )
            )
    _flush()
    return system_text, contents


# ---------- Provider call adapters ----------


async def _call_anthropic(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 8192,
    tool_choice: str = "auto",
    response_format: dict[str, Any] | None = None,
) -> LLMResponse:
    """Anthropic Messages API call returning a normalised :class:`LLMResponse`."""
    system_text, anthropic_messages = _messages_to_anthropic(messages)
    anthropic_tools = _openai_tools_to_anthropic(tools) if tools else None

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
    }
    if system_text:
        kwargs["system"] = system_text
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
        if tool_choice == "none":
            kwargs["tool_choice"] = {"type": "none"}
        elif tool_choice == "required":
            kwargs["tool_choice"] = {"type": "any"}
        else:
            kwargs["tool_choice"] = {"type": "auto"}
    # Opus 4.7 rejects temperature/top_p/top_k — we never pass them.
    # response_format is accepted but ignored: Anthropic tool-use conflicts
    # with structured-output hints, so we never forward it.
    _ = response_format

    @_retry(_is_anthropic_transient)
    async def _do() -> Any:
        return await _anthropic().messages.create(**kwargs)

    resp = await _do()

    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for block in resp.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCallRequest(
                    id=getattr(block, "id", "") or str(uuid.uuid4()),
                    name=getattr(block, "name", ""),
                    arguments=dict(getattr(block, "input", {}) or {}),
                )
            )
    return LLMResponse(
        content="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=getattr(resp, "stop_reason", "") or "",
    )


async def _call_openai(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 8192,
    tool_choice: str = "auto",
    temperature: float | None = 0.7,
    response_format: dict[str, Any] | None = None,
) -> LLMResponse:
    """OpenAI Chat Completions call returning a normalised :class:`LLMResponse`."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        kwargs["temperature"] = temperature
    if response_format is not None:
        kwargs["response_format"] = response_format

    @_retry(_is_openai_transient)
    async def _do() -> Any:
        return await _openai().chat.completions.create(**kwargs)

    resp = await _do()

    msg = resp.choices[0].message
    raw_calls = getattr(msg, "tool_calls", None) or []
    tool_calls = [
        ToolCallRequest(
            id=tc.id,
            name=tc.function.name,
            arguments=_safe_json_loads(tc.function.arguments),
        )
        for tc in raw_calls
    ]
    return LLMResponse(
        content=msg.content or "",
        tool_calls=tool_calls,
        stop_reason=resp.choices[0].finish_reason or "",
    )


async def _call_gemini(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 8192,
    tool_choice: str = "auto",
    temperature: float | None = 0.7,
    response_format: dict[str, Any] | None = None,
) -> LLMResponse:
    """Gemini GenerateContent call returning a normalised :class:`LLMResponse`."""
    model_id = model.split("/", 1)[1] if model.startswith("gemini/") else model
    system_text, contents = _messages_to_gemini(messages)
    gemini_tools = _openai_tools_to_gemini(tools) if tools else None

    tool_config = None
    if gemini_tools:
        if tool_choice == "none":
            tool_config = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode="NONE")
            )
        elif tool_choice == "required":
            tool_config = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode="ANY")
            )
        else:
            tool_config = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode="AUTO")
            )

    config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_tokens,
    }
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if system_text:
        config_kwargs["system_instruction"] = system_text
    if gemini_tools:
        config_kwargs["tools"] = gemini_tools
    if tool_config is not None:
        config_kwargs["tool_config"] = tool_config
    # Gemini 3.1 Pro Preview defaults ``thinking_level`` to HIGH; LOW is
    # ~3x faster for tool-heavy loops with no meaningful quality loss on
    # POI selection. Thinking cannot be fully disabled on 3.x Pro.
    config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level="LOW")
    if response_format is not None:
        config_kwargs["response_mime_type"] = "application/json"
        if "schema" in response_format:
            config_kwargs["response_schema"] = response_format["schema"]

    config = genai_types.GenerateContentConfig(**config_kwargs)

    @_retry(_is_gemini_transient)
    async def _do() -> Any:
        return await _gemini().aio.models.generate_content(
            model=model_id, contents=contents, config=config
        )

    resp = await _do()

    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    candidate = resp.candidates[0] if resp.candidates else None
    finish_reason = ""
    if candidate is not None:
        finish_reason = str(getattr(candidate, "finish_reason", "") or "")
        for part in candidate.content.parts or []:
            if getattr(part, "function_call", None):
                fc = part.function_call
                metadata: dict[str, Any] = {}
                # Gemini 3.1 Pro REQUIRES ``thought_signature`` to be present
                # on every function_call part in the conversation history —
                # subsequent turns 400 without it. Capture here so the round
                # trip survives.
                sig = getattr(part, "thought_signature", None)
                if sig is not None:
                    metadata["thought_signature"] = sig
                tool_calls.append(
                    ToolCallRequest(
                        id=str(uuid.uuid4()),
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                        provider_metadata=metadata or None,
                    )
                )
            elif getattr(part, "text", None):
                text_parts.append(part.text)
    return LLMResponse(
        content="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=finish_reason,
    )


# ---------- Public dispatch ----------


async def call_llm(
    provider: Provider,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 8192,
    tool_choice: str = "auto",
    temperature: float | None = 0.7,
    response_format: dict[str, Any] | None = None,
) -> LLMResponse:
    """Route to the right provider adapter. Sole call site for agent code.

    Args:
        provider: One of ``"anthropic"``, ``"openai"``, ``"gemini"``.
        model: Provider-native model id (``claude-opus-4-7``, ``gpt-5.5``,
            ``gemini/gemini-3.1-pro-preview-customtools``; the ``gemini/``
            prefix is stripped before the call).
        messages: OpenAI-style message list (system/user/assistant/tool).
        tools: Optional OpenAI function-calling schema; translated natively.
        max_tokens: Hard ceiling on generated tokens.
        tool_choice: ``"auto"`` / ``"required"`` / ``"none"``.
        temperature: Ignored for Anthropic (Opus 4.7 rejects the param).
        response_format: Optional structured-output hint. For OpenAI this is
            the raw ``{"type": "json_schema", "json_schema": {...}}`` dict.
            For Gemini, ``{"schema": ...}`` drives ``response_schema``.
            Ignored for Anthropic tool-use calls (conflicts with tool_use).

    Returns:
        :class:`LLMResponse` with content text, normalised tool calls, and
        the provider's stop reason.

    Raises:
        ValueError: If ``provider`` is not recognised.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools or [],
        "max_tokens": max_tokens,
        "tool_choice": tool_choice,
    }
    if provider == "anthropic":
        return await _call_anthropic(
            **kwargs,
            response_format=response_format,
        )
    if provider == "openai":
        return await _call_openai(
            **kwargs,
            temperature=temperature,
            response_format=response_format,
        )
    if provider == "gemini":
        return await _call_gemini(
            **kwargs,
            temperature=temperature,
            response_format=response_format,
        )
    raise ValueError(f"unknown provider: {provider!r}")


async def call_model(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Legacy kwarg-style facade around :func:`call_llm`.

    Phase 0 scaffold exposed this signature; kept as a compatibility shim so
    sibling shards can import ``call_model`` without rewiring. New code
    should call :func:`call_llm` directly.

    Args:
        model: Provider-native model id.
        messages: OpenAI-style chat messages.
        tools: Tool-use schema list.
        temperature: Sampling temperature; omitted when ``None``.
        **kwargs: Forwarded to :func:`call_llm` (``max_tokens``,
            ``tool_choice``, ``response_format``, ``provider``).

    Returns:
        A dict view of :class:`LLMResponse` with ``content``, ``tool_calls``,
        ``stop_reason`` keys.
    """
    provider: Provider = kwargs.pop("provider", _provider_from_model(model))
    resp = await call_llm(
        provider=provider,
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        **kwargs,
    )
    return {
        "content": resp.content,
        "tool_calls": [
            {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
                "provider_metadata": tc.provider_metadata,
            }
            for tc in resp.tool_calls
        ],
        "stop_reason": resp.stop_reason,
    }


def _provider_from_model(model: str) -> Provider:
    """Infer the provider from the model string prefix.

    Used by :func:`call_model` when the caller omits ``provider``.
    """
    if model.startswith("gemini/") or model.startswith("gemini-"):
        return "gemini"
    if model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3"):
        return "openai"
    return "anthropic"


def _safe_json_loads(raw: Any) -> dict[str, Any]:
    """Decode a JSON string to dict; return empty dict on any failure."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "Provider",
    "LLMResponse",
    "ToolCallRequest",
    "call_llm",
    "call_model",
]
