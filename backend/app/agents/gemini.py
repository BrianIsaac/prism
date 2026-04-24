"""Google Gemini 3.1 Pro racer. Phase 2 implementation."""

from __future__ import annotations

from app.agents.base import BaseAgent


class GeminiAgent(BaseAgent):
    """Gemini racer — same prompt as Opus and GPT, different decoder."""

    name = "gemini"
