"""Gemini racer — Google Gemini 3.1 Pro Preview.

Same shared prompt, same tool belt, same harness as the other two racers.
``thinking_level="LOW"`` is set inside :mod:`app.llm_clients` (the provider
adapter, not the agent config) — 3x faster for tool-use loops with no
meaningful quality loss on POI selection. ``thinkingBudget`` (the 2.5-era
parameter) is incompatible with 3.1 Pro and must not appear here.

Model string: ``gemini/gemini-3.1-pro-preview-customtools`` — the
tool-tuned variant of 3.1 Pro Preview, required for reliable
function-calling performance on the GrabMaps belt.
"""

from __future__ import annotations

from app.agents.base import AgentConfig
from app.agents.shared_prompt import SHARED_SYSTEM_PROMPT
from app.config import AGENT_TEMPERATURE, GEMINI_MODEL

GEMINI = AgentConfig(
    name="gemini",
    provider="gemini",
    model=GEMINI_MODEL,
    temperature=AGENT_TEMPERATURE,
    system_prompt=SHARED_SYSTEM_PROMPT,
    colour="blue",
)
