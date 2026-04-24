"""Provider-agnostic LLM dispatch (Anthropic / OpenAI / Google) via LiteLLM-style wiring.

Phase 2 implementation. The shell exposes the import surface expected by
:mod:`app.agents` and the spec / judge modules.
"""

from __future__ import annotations

from typing import Any


async def call_model(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch a single LLM call to the provider that owns ``model``.

    Phase 2 implementation.

    Args:
        model: Provider-prefixed model identifier (e.g. ``claude-opus-4-7``).
        messages: OpenAI-style chat messages.
        tools: Tool-use schema list, normalised for the provider.
        temperature: Sampling temperature. Omitted when None so Opus 4.7
            (which rejects the param) can be called without a wrapper.
        **kwargs: Provider-specific extras (e.g. ``thinking`` for Claude).

    Returns:
        The normalised provider response shape.
    """
    raise NotImplementedError
