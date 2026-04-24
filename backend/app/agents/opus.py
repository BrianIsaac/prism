"""Opus racer — Claude Opus 4.7.

Same shared prompt, same tool belt, same harness as the other two racers —
model diversity is the only differentiator. Opus 4.7 in particular rejects
the ``temperature`` parameter outright (reasoning models deprecated it), so
:attr:`AgentConfig.temperature` is ``None`` here and the Anthropic adapter
in :mod:`app.llm_clients` omits the field from the request body.
"""

from __future__ import annotations

from app.agents.base import AgentConfig
from app.agents.shared_prompt import SHARED_SYSTEM_PROMPT
from app.config import OPUS_MODEL

# temperature=None because Claude Opus 4.7 rejects the parameter. Adaptive
# thinking is off by default on 4.7, so no explicit thinking config is
# needed. The parser in agents/base.py handles occasional prose preambles
# before the final JSON via a regex fallback.
OPUS = AgentConfig(
    name="opus",
    provider="anthropic",
    model=OPUS_MODEL,
    temperature=None,
    system_prompt=SHARED_SYSTEM_PROMPT,
    colour="red",
)
