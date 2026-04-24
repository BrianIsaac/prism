"""Feedback knowledge-base distillation (Karpathy "LLM Wiki" at hackathon scale).

Instead of embedding feedback and retrieving it via cosine similarity (RAG),
the raw corpus is compiled into a small maintained artefact — a short summary
plus a frequency-weighted tag list — that every race reads as ambient prompt
context.

Flow:
    1. Users submit free-text feedback via ``POST /feedback``.
    2. After every Nth row :func:`should_trigger_digest` fires true, the race
       endpoint kicks :func:`build_feedback_digest` off in the background, and
       one Haiku call rewrites the digest.
    3. The new digest is persisted and every subsequent race injects it via
       :func:`format_digest_for_prompt`.

The prompt is strict-JSON to keep parsing deterministic. Temperature is
pinned to 0 so the same corpus re-emits the same digest — reproducibility
matters when a judge asks "why did the room's taste shift?"
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import JUDGE_MODEL
from app.storage import (
    get_latest_feedback_digest,
    insert_feedback_digest,
    list_feedback,
)


log = logging.getLogger("prism")


# Rebuild the digest after every Nth feedback submission. The acceptance suite
# pins this at 3 so should_trigger_digest(3) is true and should_trigger_digest(2)
# is false.
_DIGEST_TRIGGER_EVERY: int = 3
_DIGEST_SOURCE_LIMIT: int = 40
_MAX_SUMMARY_CHARS: int = 1400
_MAX_TAG_COUNT: int = 8


_DIGEST_SYSTEM_PROMPT = """You maintain a rolling "room taste profile" for an agentic city-discovery engine.

Users leave free-text feedback on itineraries they actually tried. Your job is to distil their collective taste into two artefacts:

1. A short prose summary (2-4 sentences, imperative mood) describing what reinforces a good trip in this room. Prefer concrete signals over platitudes.
2. A tag list of the most reinforced themes.

Output ONLY JSON matching this schema:
{
  "summary": string,
  "tags": [{"tag": string, "count": integer}]
}

Rules:
- Never echo user PII. Never copy a full sentence of feedback verbatim.
- Tags must be lowercase kebab-case, one concept per tag (e.g. "hidden-gem", "avoid-queues", "late-evening").
- Include a tag only if it is supported by more than one piece of feedback.
- At most 8 tags. At most 280 words in summary.
- If feedback is too sparse to generalise, say so in one sentence and return an empty tag list."""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _compose_user_prompt(
    existing: dict[str, Any] | None,
    feedback_rows: list[dict[str, Any]],
) -> str:
    """Render the digest-call user prompt from current state + new signals.

    Args:
        existing: Previous digest dict, or None on first rebuild.
        feedback_rows: Newest-first raw feedback rows.

    Returns:
        A multi-line string forwarded as the user message.
    """
    parts: list[str] = []
    if existing:
        parts.append("Current taste profile:")
        parts.append(existing.get("summary", "") or "(empty)")
        tags = existing.get("tags") or []
        if tags:
            tag_line = ", ".join(
                f"{t.get('tag')} x{t.get('count')}" for t in tags if t.get("tag")
            )
            parts.append(f"Current hot tags: {tag_line}")
    parts.append(f"\nFeedback rows ({len(feedback_rows)}, newest first):")
    for row in feedback_rows:
        response = (row.get("response") or "").strip()
        sentiment = row.get("sentiment") or "positive"
        if not response:
            continue
        parts.append(f"- [{sentiment}] {response}")
    parts.append("\nUpdate the taste profile given these signals. Return JSON only.")
    return "\n".join(parts)


def _extract_content(response: Any) -> str:
    """Return the text payload from a provider-normalised LLM response.

    The concrete response shape is defined by Phase 2's :mod:`app.llm_clients`;
    this helper accepts either an object with a ``.content`` attribute or a
    dict with a ``content`` key so the digest builder is decoupled from the
    exact provider-normalisation choice.
    """
    if response is None:
        return ""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
        if content is None:
            # OpenAI-style choices array fallback.
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict):
                    content = message.get("content")
    return content or ""


def _parse_digest_response(content: str) -> dict[str, Any] | None:
    """Parse the LLM's JSON response with defensive fence-cleanup.

    Handles the three shapes Haiku can return even with
    ``response_format={"type": "json_object"}`` set: raw JSON, ``...``-fenced
    JSON, and ``json``-tagged fenced JSON.

    Args:
        content: Raw LLM output text.

    Returns:
        A dict with ``summary`` and ``tags`` keys, or None when the response
        is not parseable as the expected shape.
    """
    content = (content or "").strip()
    content = _FENCE_RE.sub("", content).strip()
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    summary = obj.get("summary")
    tags = obj.get("tags")
    if not isinstance(summary, str):
        return None
    if not isinstance(tags, list):
        tags = []
    clean_tags: list[dict[str, Any]] = []
    for t in tags[:_MAX_TAG_COUNT]:
        if not isinstance(t, dict):
            continue
        tag = t.get("tag")
        count = t.get("count")
        if not isinstance(tag, str) or not tag:
            continue
        if not isinstance(count, (int, float)) or count < 1:
            continue
        clean_tags.append({"tag": tag[:40], "count": int(count)})
    return {
        "summary": summary.strip()[:_MAX_SUMMARY_CHARS],
        "tags": clean_tags,
    }


async def build_feedback_digest(
    scope: str = "global",
    source_limit: int = _DIGEST_SOURCE_LIMIT,
) -> dict[str, Any] | None:
    """Run one distillation pass, persist the result, and return it.

    Imports :func:`app.llm_clients.call_model` lazily so this module stays
    decoupled from Phase 2's LLM dispatch while still being import-safe.

    Args:
        scope: Digest scope (``"global"`` for all-feedback; future: per-country).
        source_limit: Max feedback rows to feed the LLM in a single call.

    Returns:
        A digest dict on success, or None when the corpus is empty or the
        LLM response could not be parsed. Callers treat None as "no digest
        update this cycle" rather than an error.
    """
    rows = await list_feedback(limit=source_limit)
    if not rows:
        return None

    existing = await get_latest_feedback_digest(scope=scope)

    # Local import so feedback_kb has no import-time dependency on Phase 2.
    from app.llm_clients import call_model

    try:
        response = await call_model(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": _DIGEST_SYSTEM_PROMPT},
                {"role": "user", "content": _compose_user_prompt(existing, rows)},
            ],
            tools=None,
            temperature=0.0,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        content = _extract_content(response)
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback digest LLM call failed: %s", exc)
        return None

    parsed = _parse_digest_response(content)
    if parsed is None:
        log.warning("feedback digest response could not be parsed: %r", content[:200])
        return None

    await insert_feedback_digest(
        scope=scope,
        summary=parsed["summary"],
        tags=parsed["tags"],
        source_count=len(rows),
        model=JUDGE_MODEL,
    )
    return {
        "scope": scope,
        "summary": parsed["summary"],
        "tags": parsed["tags"],
        "source_count": len(rows),
        "model": JUDGE_MODEL,
    }


def format_digest_for_prompt(digest: dict[str, Any] | None) -> str:
    """Render a digest into a single ambient-prompt block.

    Args:
        digest: Digest dict as returned by :func:`build_feedback_digest` or
            :func:`app.storage.get_latest_feedback_digest`, or None.

    Returns:
        The prompt block as a string. Returns an empty string when the digest
        is None or empty, so the race endpoint can inject the result
        unconditionally.
    """
    if digest is None:
        return ""
    summary = (digest.get("summary") or "").strip()
    tags = digest.get("tags") or []
    if not summary and not tags:
        return ""
    source_count = digest.get("source_count") or 0
    parts = [f"Room taste profile (distilled from {source_count} validations):"]
    if summary:
        parts.append(summary)
    if tags:
        tag_line = ", ".join(
            f"{t.get('tag')} x{t.get('count')}" for t in tags if t.get("tag")
        )
        parts.append(f"Hot tags: {tag_line}")
    parts.append(
        "These are ambient signal — use them as taste tie-breakers when two candidate POIs score equivalently."
    )
    return "\n".join(parts)


def should_trigger_digest(feedback_count: int) -> bool:
    """Return True when the caller's feedback insert should rebuild the digest.

    Args:
        feedback_count: Total rows in ``feedback`` after the insert.

    Returns:
        True when the rebuild cadence should fire; False otherwise.
    """
    if feedback_count <= 0:
        return False
    return feedback_count % _DIGEST_TRIGGER_EVERY == 0
