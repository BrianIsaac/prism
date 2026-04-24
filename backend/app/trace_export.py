"""Trace aggregation and bug-report export.

Phase 3 implementation. The shell exposes :func:`export_bug_report` so
:mod:`app.main` can register the admin endpoint at boot time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BugReport:
    """Aggregated failing tool-calls, shaped for the admin endpoint response."""

    generated_at: str = ""
    total_calls: int = 0
    failed_calls: int = 0
    failures_by_tool: dict[str, int] = field(default_factory=dict)
    failures_by_status: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the report as a Markdown string for human review.

        Phase 3 implementation.
        """
        raise NotImplementedError


async def export_bug_report() -> BugReport:
    """Aggregate every failing tool-call trace into a :class:`BugReport`.

    Phase 3 implementation.
    """
    raise NotImplementedError
