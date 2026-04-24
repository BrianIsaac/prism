"""Trace aggregation and failing-tool-call report.

Groups every failing tool-call row by ``tool_name`` and ``status``, samples
a handful of reproduction cases, and renders the result as both a
structured :class:`BugReport` dataclass and a one-page Markdown string.
Powers the admin dashboard's ``/admin/bug-report`` surface so the
operator can see which GrabMaps endpoints are regressing in real time
and hand the Markdown export to an engineer for reproduction.

Reads directly from the ``traces`` SQLite table via :mod:`aiosqlite`
instead of going through :mod:`app.storage` — a single
``SELECT * FROM traces`` here is cheaper than cross-shard churn.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import SQLITE_PATH


_TRACE_COLUMNS = (
    "id",
    "race_id",
    "agent_name",
    "tool_name",
    "input",
    "output",
    "status",
    "error",
    "latency_ms",
)

_SAMPLE_BODY_LIMIT = 50
_MARKDOWN_SAMPLE_LIMIT = 20


@dataclass
class BugReport:
    """Aggregated failing tool-calls, shaped for the admin endpoint response.

    Attributes:
        generated_at: ISO-8601 UTC timestamp of aggregation.
        total_calls: Every trace row encountered.
        failed_calls: Rows whose ``status`` is not ``"ok"``.
        failures_by_tool: Per-tool failure counts, descending.
        failures_by_status: Per-status failure counts, descending.
        samples: Raw failing rows for reproduction, capped at 50.
    """

    generated_at: str = ""
    total_calls: int = 0
    failed_calls: int = 0
    failures_by_tool: dict[str, int] = field(default_factory=dict)
    failures_by_status: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the report as a one-page Markdown string for human reviewers.

        Returns:
            Markdown with a summary header, per-tool and per-status tables,
            and up to 20 failing samples.
        """
        lines = [
            "# GrabMaps Beta Bug Report",
            f"Generated: {self.generated_at}",
            "",
            f"- Total tool calls: {self.total_calls}",
            f"- Failed calls: {self.failed_calls}",
            "",
            "## Failures by tool",
        ]
        for tool, count in sorted(self.failures_by_tool.items(), key=lambda t: -t[1]):
            lines.append(f"- `{tool}`: {count}")
        lines += ["", "## Failures by status"]
        for status, count in sorted(
            self.failures_by_status.items(), key=lambda t: -t[1]
        ):
            lines.append(f"- `{status}`: {count}")
        lines += ["", "## Reproduction samples", ""]
        for i, sample in enumerate(self.samples[:_MARKDOWN_SAMPLE_LIMIT], 1):
            lines += [
                f"### {i}. `{sample.get('tool_name')}` ({sample.get('status')})",
                f"- Agent: `{sample.get('agent_name')}`",
                f"- Latency: {sample.get('latency_ms')} ms",
                f"- Input: `{sample.get('input')}`",
                f"- Error: {sample.get('error') or '-'}",
                "",
            ]
        return "\n".join(lines)


async def _fetch_all_traces() -> list[dict[str, Any]]:
    """Return every trace row currently persisted as a list of dicts.

    Uses a direct connection to :data:`app.config.SQLITE_PATH` rather than
    going through :mod:`app.storage` — see the module docstring for why.

    Returns:
        A list of trace rows. Returns an empty list if the DB file or the
        ``traces`` table does not yet exist (e.g. on a fresh boot before
        the first race has run).
    """
    columns = ", ".join(_TRACE_COLUMNS)
    try:
        async with aiosqlite.connect(SQLITE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(f"SELECT {columns} FROM traces") as cursor:
                raw = await cursor.fetchall()
    except aiosqlite.OperationalError:
        # Fresh DB without the schema yet — return an empty report rather
        # than 500-ing the admin endpoint.
        return []
    return [dict(row) for row in raw]


async def export_bug_report() -> BugReport:
    """Aggregate every failing tool-call trace into a :class:`BugReport`.

    Returns:
        A populated :class:`BugReport`. When no failures exist, the counters
        are zero and ``samples`` is empty.
    """
    rows = await _fetch_all_traces()
    failed = [r for r in rows if r.get("status") not in ("ok", None)]

    by_tool: defaultdict[str, int] = defaultdict(int)
    by_status: defaultdict[str, int] = defaultdict(int)
    for r in failed:
        by_tool[r.get("tool_name", "unknown")] += 1
        by_status[r.get("status", "unknown")] += 1

    return BugReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_calls=len(rows),
        failed_calls=len(failed),
        failures_by_tool=dict(by_tool),
        failures_by_status=dict(by_status),
        samples=failed[:_SAMPLE_BODY_LIMIT],
    )
