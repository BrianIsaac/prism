"""Feedback knowledge base: digest builder + prompt formatter.

Phase 3 implementation. The shell exposes the three symbols :mod:`app.main`
imports.
"""

from __future__ import annotations

from typing import Any

# Rebuild the digest every N feedback rows. The trigger counts actual row
# totals rather than ``lastrowid`` so cadence stays correct across test resets.
_DIGEST_EVERY_N: int = 5


def should_trigger_digest(feedback_count: int) -> bool:
    """Return True when a digest rebuild should fire for this row count."""
    return feedback_count > 0 and feedback_count % _DIGEST_EVERY_N == 0


async def build_feedback_digest(scope: str = "global") -> dict[str, Any] | None:
    """Distil the raw feedback corpus into a single ambient-prompt block.

    Phase 3 implementation.

    Args:
        scope: Digest scope (``"global"`` for all-feedback; future: per-country).

    Returns:
        A digest dict ready to persist, or ``None`` if the corpus is empty.
    """
    raise NotImplementedError


def format_digest_for_prompt(digest: dict[str, Any] | None) -> str:
    """Render a digest row as a prompt-ready Markdown block.

    Phase 3 implementation. Returns an empty string when ``digest`` is None so
    the race endpoint can inject the result unconditionally.
    """
    raise NotImplementedError
