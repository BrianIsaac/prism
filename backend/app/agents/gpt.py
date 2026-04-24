"""GPT racer — OpenAI GPT 5.5.

Same shared prompt, same tool belt, same harness as the other two racers.
GPT 5.5 on Chat Completions accepts the standard OpenAI tool schema;
``reasoning={"effort": "low"}`` is Responses-API-only, so we stay on Chat
Completions for tool-use continuity and leave reasoning at default.
"""

from __future__ import annotations

from app.agents.base import AgentConfig
from app.agents.shared_prompt import SHARED_SYSTEM_PROMPT
from app.config import AGENT_TEMPERATURE, GPT_MODEL

GPT = AgentConfig(
    name="gpt",
    provider="openai",
    model=GPT_MODEL,
    temperature=AGENT_TEMPERATURE,
    system_prompt=SHARED_SYSTEM_PROMPT,
    colour="green",
)
