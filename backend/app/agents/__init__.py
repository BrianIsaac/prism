"""Agent package — :class:`AgentConfig` instances and the shared runner.

Three racers share one prompt, one tool belt, one harness, one spec. The
only variable is the model — Opus 4.7, GPT 5.5, Gemini 3.1 Pro running
the identical brief produce three genuinely different plans via their own
reasoning styles, with no artificial prior injection.
"""

from __future__ import annotations

from app.agents.base import AgentConfig, EventEmitter, run_agent
from app.agents.gemini import GEMINI
from app.agents.gpt import GPT
from app.agents.opus import OPUS

AGENT_POOL: list[AgentConfig] = [OPUS, GPT, GEMINI]

__all__ = [
    "AGENT_POOL",
    "AgentConfig",
    "EventEmitter",
    "run_agent",
    "OPUS",
    "GPT",
    "GEMINI",
]
