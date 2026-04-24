"""OpenAI GPT 5.5 racer. Phase 2 implementation."""

from __future__ import annotations

from app.agents.base import BaseAgent


class GPTAgent(BaseAgent):
    """GPT racer — shared temperature, shared prompt, shared tool belt."""

    name = "gpt"
