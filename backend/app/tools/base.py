"""Tool-call wrapper: per-agent budget accounting + trace row + SSE callback.

Phase 3 implementation. The shell exposes :class:`ToolTrace` and
:func:`call_tool_with_budget` so :mod:`app.agents.base` can import the
wiring at boot time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolTrace:
    """A single tool-call event captured for export.

    ``input`` and ``output`` are stored as JSON strings so a later
    :func:`app.trace_export.export_bug_report` can grep across them without
    rehydrating full tool payloads.
    """

    tool_name: str
    agent_name: str
    status: str = "ok"
    input: str | None = None
    output: str | None = None
    error: str | None = None
    latency_ms: int = 0
    race_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


async def call_tool_with_budget(
    *,
    tool_name: str,
    agent_name: str,
    tool_fn: Any,
    tool_args: dict[str, Any],
    budget_remaining: int,
    race_id: str | None = None,
    sse_emit: Any = None,
) -> tuple[Any, ToolTrace]:
    """Invoke a tool while charging the agent's budget and emitting SSE frames.

    Phase 3 implementation.
    """
    raise NotImplementedError
