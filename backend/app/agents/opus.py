"""Claude Opus 4.7 racer. Phase 2 implementation."""

from __future__ import annotations

from app.agents.base import BaseAgent


class OpusAgent(BaseAgent):
    """Opus racer — no temperature; Claude 4.7 rejects the param."""

    name = "opus"
