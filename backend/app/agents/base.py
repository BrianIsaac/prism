"""Base agent: tool-use loop + ``emit_thought`` instrumentation.

Phase 2 implementation. The shell exposes the :class:`BaseAgent` class so
:mod:`app.agents.opus` / ``.gpt`` / ``.gemini`` can subclass at import time.
"""

from __future__ import annotations

from typing import Any


class BaseAgent:
    """Shared tool-use loop. Each provider subclass only swaps the model id."""

    name: str = ""
    model: str = ""

    async def run(
        self,
        *,
        spec: Any,
        hot_candidates: list[dict[str, Any]] | None = None,
        feedback_kb: str | None = None,
        event_emitter: Any = None,
    ) -> dict[str, Any]:
        """Run one agent to completion and return its final plan dict.

        Phase 2 implementation.
        """
        raise NotImplementedError
