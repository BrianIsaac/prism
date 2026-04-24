"""Tool-call wrapper: per-agent budget accounting + trace row + SSE callback.

The v2 chokepoint every agent uses to reach the GrabMaps tool belt. Responsible
for:

    - Budget enforcement (:class:`Budget`, :class:`ToolBudgetExceeded`).
    - Structured trace rows (:class:`ToolTrace`) persisted into the ``traces``
      table so :func:`app.trace_export.export_bug_report` can aggregate failures.
    - SSE hooks: ``tool_call`` and ``tool_result`` events mirrored onto the
      per-race event queue via an optional ``event_emitter`` callback.

``emit_thought`` is routed through the same chokepoint but short-circuits —
it costs zero budget, writes no trace row, and simply emits a ``thought``
event so the frontend thought bubble can render in-flight narration.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class ToolBudgetExceeded(Exception):
    """Raised when an agent has exhausted its per-race tool-call budget."""


@dataclass
class Budget:
    """Per-agent tool-call budget.

    Attributes:
        remaining: Calls left before the next call must raise
            :class:`ToolBudgetExceeded`. Decremented in-place by
            :func:`call_tool_with_budget`.
    """

    remaining: int

    def decrement(self) -> None:
        """Consume one budget unit."""
        self.remaining -= 1


@dataclass
class ToolTrace:
    """One tool-call event captured for export.

    Shape mirrors the columns of the ``traces`` table so
    :func:`app.storage.insert_traces` can persist rows directly.

    Attributes:
        id: UUID for this trace.
        race_id: Parent race identifier.
        agent_name: Which agent issued the call.
        tool_name: Name of the GrabMaps wrapper.
        input: Arguments passed (JSON-encoded string for storage).
        output: Result payload (JSON-encoded string when available).
        status: ``"ok"`` on success; an exception class name on failure.
        error: Human-readable error text when ``status != "ok"``.
        latency_ms: Wall-clock latency in milliseconds.
    """

    id: str
    race_id: str
    agent_name: str
    tool_name: str
    input: str
    output: str | None = None
    status: str = "ok"
    error: str | None = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        """Return the column-shaped dict :func:`insert_traces` expects."""
        return {
            "id": self.id,
            "race_id": self.race_id,
            "agent_name": self.agent_name,
            "tool_name": self.tool_name,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


def _extract_latlng(args: dict[str, Any]) -> dict[str, float] | None:
    """Best-effort lat/lng extraction for the ``tool_call`` SSE payload.

    Agents call tools with a variety of coordinate keys (``lat``/``lng`` for
    the majority, ``origin_lat``/``origin_lng`` for :func:`route`). This
    helper surfaces a single coordinate for the frontend cursor so the live
    canvas can draw a ping where the agent is looking, without the frontend
    having to learn each tool's argument shape.

    Args:
        args: The tool call's keyword arguments.

    Returns:
        A ``{"lat": float, "lng": float}`` dict, or ``None`` if neither
        coordinate pair is present.
    """
    lat = args.get("lat")
    lng = args.get("lng")
    if lat is None or lng is None:
        lat = args.get("near_lat")
        lng = args.get("near_lng")
    if lat is None or lng is None:
        lat = args.get("origin_lat")
        lng = args.get("origin_lng")
    if lat is None or lng is None:
        return None
    try:
        return {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return None


def _summarise_result(tool_name: str, result: Any) -> dict[str, Any]:
    """Condense a tool result into a compact SSE ``tool_result`` payload.

    Street-view responses are surfaced with an explicit ``thumb_url`` so the
    live canvas can overlay a thumbnail frame on the agent cursor without
    re-fetching the full list.

    Args:
        tool_name: The wrapper that produced ``result``.
        result: The raw return value — typically a dict or list.

    Returns:
        A dict safe to JSON-encode into an SSE frame: includes a ``summary``
        string and optionally a ``thumb_url`` for street-view hits.
    """
    summary: dict[str, Any] = {"summary": ""}
    if isinstance(result, dict):
        photos = result.get("photos")
        places = result.get("places")
        routes = result.get("routes")
        segments = result.get("segments")
        incidents = result.get("incidents")
        matrix = result.get("matrix")
        if photos:
            summary["summary"] = f"{len(photos)} street-view photo(s)"
            first = photos[0] if isinstance(photos, list) else None
            if isinstance(first, dict):
                thumb = first.get("thumbUrl") or first.get("thumb_url")
                if thumb:
                    summary["thumb_url"] = thumb
        elif places is not None:
            summary["summary"] = f"{len(places)} place(s)"
        elif routes:
            summary["summary"] = f"{len(routes)} route(s)"
        elif segments is not None:
            summary["summary"] = f"{len(segments)} traffic segment(s)"
        elif incidents is not None:
            summary["summary"] = f"{len(incidents)} incident(s)"
        elif matrix is not None:
            rows = len(matrix)
            cols = len(matrix[0]) if rows and isinstance(matrix[0], list) else 0
            summary["summary"] = f"{rows}x{cols} route matrix"
        else:
            summary["summary"] = f"{tool_name} ok"
    elif isinstance(result, list):
        summary["summary"] = f"{len(result)} item(s)"
    else:
        summary["summary"] = f"{tool_name} ok"
    return summary


def _json_safe(value: Any) -> str:
    """Serialise a payload to JSON, falling back to ``repr`` on failure."""
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


async def call_tool_with_budget(
    *,
    agent_name: str,
    tool_name: str,
    args: dict[str, Any],
    race_id: str,
    budget: Budget,
    event_emitter: EventEmitter | None = None,
) -> Any:
    """Invoke a tool under the shared budget and mirror the call onto SSE.

    Behaviour follows the Phase 3 contract:

        1. If ``budget.remaining <= 0``, raise :class:`ToolBudgetExceeded`.
        2. Decrement the budget.
        3. Emit ``tool_call`` (with lat/lng when extractable from ``args``).
        4. Dispatch via the belt-level ``GRABMAPS_TOOLS`` dict, timing the call.
        5. Persist a :class:`ToolTrace` row into the ``traces`` table.
        6. Emit ``tool_result`` (with ``thumb_url`` for street-view payloads).
        7. Return the tool's result.

    ``emit_thought`` is a special case: no budget cost, no trace row, emits a
    ``thought`` event and returns ``{"ok": True}``. It is therefore handled
    before any of the steps above.

    Args:
        agent_name: Which racing agent issued the call (``opus`` / ``gpt`` / ``gemini``).
        tool_name: Name of the wrapper (e.g. ``places_search``). ``emit_thought``
            is handled as instrumentation, not a tool.
        args: Keyword arguments forwarded to the tool function.
        race_id: Race identifier used for trace persistence and event fan-out.
        budget: Mutable :class:`Budget` shared across the agent's calls.
        event_emitter: Optional async callback that forwards every SSE frame
            onto the per-race queue.

    Returns:
        The tool's result. ``emit_thought`` returns ``{"ok": True}``.

    Raises:
        ToolBudgetExceeded: When the agent has exhausted its budget.
    """
    if tool_name == "emit_thought":
        note = args.get("note") or args.get("thought") or ""
        if event_emitter is not None:
            await event_emitter(
                {
                    "type": "thought",
                    "agent": agent_name,
                    "payload": {"note": str(note)},
                }
            )
        return {"ok": True}

    if budget.remaining <= 0:
        # Record the budget-exhaustion so the Bug Hunter artefact surfaces the
        # most common agent failure mode. Without this, the exception would
        # pre-empt the trace write and the report would under-count exhaustions.
        from app.storage import insert_traces

        trace = ToolTrace(
            id=str(uuid.uuid4()),
            race_id=race_id,
            agent_name=agent_name,
            tool_name=tool_name,
            input=_json_safe(args),
            status="error",
            error=f"ToolBudgetExceeded: budget exhausted before {tool_name}",
        )
        try:
            await insert_traces([trace.to_row()])
        except Exception:  # noqa: BLE001 — trace is best-effort when storage is mid-boot
            pass
        raise ToolBudgetExceeded(f"tool budget exhausted before {tool_name}")

    budget.decrement()

    if event_emitter is not None:
        call_payload: dict[str, Any] = {"args": args}
        coord = _extract_latlng(args)
        if coord is not None:
            call_payload["lat"] = coord["lat"]
            call_payload["lng"] = coord["lng"]
        await event_emitter(
            {
                "type": "tool_call",
                "agent": agent_name,
                "payload": {"tool": tool_name, **call_payload},
            }
        )

    # Lazy-import the dispatch dict to avoid circular imports between
    # ``tools.base`` and the individual wrapper modules that import ``Budget``
    # and :class:`ToolTrace` from here.
    from app.tools import GRABMAPS_TOOLS

    tool_fn = GRABMAPS_TOOLS.get(tool_name)
    if tool_fn is None:
        # Build an error trace so unknown tool calls show up in the bug report.
        from app.storage import insert_traces

        trace = ToolTrace(
            id=str(uuid.uuid4()),
            race_id=race_id,
            agent_name=agent_name,
            tool_name=tool_name,
            input=_json_safe(args),
            status="error",
            error=f"UnknownTool: {tool_name}",
        )
        try:
            await insert_traces([trace.to_row()])
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"unknown tool {tool_name}")

    t0 = time.monotonic()
    status = "ok"
    error: str | None = None
    result: Any = None
    try:
        result = await tool_fn(**args)
    except Exception as exc:  # noqa: BLE001 — we record everything, then re-raise
        status = type(exc).__name__
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        latency_ms = (time.monotonic() - t0) * 1000
        from app.storage import insert_traces

        trace = ToolTrace(
            id=str(uuid.uuid4()),
            race_id=race_id,
            agent_name=agent_name,
            tool_name=tool_name,
            input=_json_safe(args),
            output=_json_safe(result) if result is not None else None,
            status=status,
            error=error,
            latency_ms=round(latency_ms, 2),
        )
        try:
            await insert_traces([trace.to_row()])
        except Exception:  # noqa: BLE001 — trace row is best-effort
            pass

        if event_emitter is not None:
            payload: dict[str, Any] = {
                "tool": tool_name,
                "status": status,
                "latency_ms": round(latency_ms, 2),
            }
            if status == "ok":
                payload.update(_summarise_result(tool_name, result))
            else:
                payload["error"] = error
            try:
                await event_emitter(
                    {
                        "type": "tool_result",
                        "agent": agent_name,
                        "payload": payload,
                    }
                )
            except Exception:  # noqa: BLE001 — SSE mirror is best-effort
                pass

    return result
