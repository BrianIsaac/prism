"""Race orchestrator — three agents in parallel under a wall clock with SSE.

The race runner spawns one :func:`~app.agents.base.run_agent` coroutine per
:class:`~app.agents.base.AgentConfig` in
:data:`~app.agents.AGENT_POOL`, waits up to
:data:`~app.config.RACE_DEADLINE_SECONDS` via :func:`asyncio.wait`, cancels
stragglers, scores survivors through :func:`~app.harness.score_and_rank`,
and emits a terminal ``race_complete`` SSE event with the final ranking.

Every agent receives its own ``event_emitter`` closure that stamps the
agent name and ``t_ms`` (milliseconds since race start) onto every event
before forwarding to the caller-supplied emitter. That way
:mod:`app.agents.base` produces bare ``{type, payload}`` events and the
race runner owns the frame chrome — agents cannot accidentally stamp the
wrong colour or reset the clock.

``plan_resolved`` events fire as each agent finishes (preliminary,
pre-ranking); the final ``race_complete`` event carries the ranked list
produced by the harness. ``error`` events fire for crashes, with the
agent name and exception text so the admin UI can show a per-agent pill.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents import AGENT_POOL, run_agent
from app.agents.base import AgentConfig
from app.config import RACE_DEADLINE_SECONDS
from app.harness import HARNESS_WEIGHTS, score_and_rank

EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


async def run_race(
    spec: Any,
    *,
    hot_candidates: list[dict[str, Any]] | None = None,
    feedback_kb: str | None = None,
    weights: dict[str, float] | None = None,
    event_emitter: EventEmitter | None = None,
    race_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run the three-agent race and stream events via ``event_emitter``.

    Each agent task gets a per-agent emitter closure that stamps ``agent``
    and ``t_ms`` before delegating to the caller's emitter. On completion,
    survivors pass through :func:`~app.harness.score_and_rank` and the
    ranked list ships out as a single ``race_complete`` event.

    Args:
        spec: Parsed :class:`~app.models.Spec` or equivalent dict.
        hot_candidates: Plan-atom POI dicts from the swarm overlay (optional).
        feedback_kb: Ambient feedback digest formatted for the prompt (optional).
        weights: Runtime-drifted harness weights for this race (optional;
            defaults to :data:`~app.harness.HARNESS_WEIGHTS`).
        event_emitter: Caller-supplied SSE emitter. Receives one
            :class:`~app.models.RaceStreamEvent`-shaped dict per call.
        race_id: Race identifier for tool-trace attribution (optional;
            upstream callers typically pass the same id that went into the
            ``POST /race`` handshake response).

    Returns:
        Scored plan dicts ready for persistence (the harness output).
    """
    started = time.monotonic()
    deadline_monotonic = started + RACE_DEADLINE_SECONDS
    spec_dict = _spec_to_dict(spec)

    tasks: list[asyncio.Task[dict[str, Any]]] = []
    for agent in AGENT_POOL:
        agent_emitter = _make_agent_emitter(event_emitter, agent, started)
        tasks.append(
            asyncio.create_task(
                run_agent(
                    agent,
                    spec_dict,
                    hot_candidates=hot_candidates,
                    feedback_kb=feedback_kb,
                    deadline_monotonic=deadline_monotonic,
                    race_id=race_id,
                    event_emitter=agent_emitter,
                ),
                name=f"agent-{agent.name}",
            )
        )

    _done, pending = await asyncio.wait(tasks, timeout=RACE_DEADLINE_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        # Drain cancellations so asyncio doesn't leave dangling coroutines
        # and so we can collect them in the plans loop below.
        await asyncio.gather(*pending, return_exceptions=True)

    plans: list[dict[str, Any]] = []
    name_by_task = {task: task.get_name().replace("agent-", "") for task in tasks}
    for task in tasks:
        agent_name = name_by_task[task]
        if task in pending:
            plans.append(_deadline_plan(agent_name))
            await _emit(
                event_emitter,
                "error",
                agent_name,
                {"agent": agent_name, "message": "deadline_exceeded"},
                started,
            )
            continue
        exc = task.exception()
        if exc is not None:
            plans.append(_crash_plan(agent_name, exc))
            await _emit(
                event_emitter,
                "error",
                agent_name,
                {"agent": agent_name, "message": f"{type(exc).__name__}: {exc}"},
                started,
            )
            continue
        plans.append(task.result())

    effective_weights = weights or HARNESS_WEIGHTS
    try:
        scored = await score_and_rank(
            plans,
            spec_dict,
            weights=effective_weights,
            streetview_urls_by_poi=None,
        )
    except NotImplementedError:
        # Phase 1 has not filled in score_and_rank yet (shards run in
        # parallel). Surface the plans unchanged so the race completes
        # rather than 500s; Phase 7 integration reconciles.
        scored = plans
    except Exception as exc:  # noqa: BLE001
        scored = plans
        await _emit(
            event_emitter,
            "error",
            None,
            {"agent": None, "message": f"harness_error: {type(exc).__name__}: {exc}"},
            started,
        )

    # Preliminary plan_resolved events per agent so the frontend can paint
    # a first-pass ranking before the final race_complete.
    for idx, plan in enumerate(scored):
        await _emit(
            event_emitter,
            "plan_resolved",
            plan.get("agent_name"),
            {
                "rank": idx + 1,
                "score": float(plan.get("total_score") or 0.0),
                "plan_id": str(plan.get("plan_id") or plan.get("agent_name") or ""),
            },
            started,
        )

    await _emit(
        event_emitter,
        "race_complete",
        None,
        {"plans": scored},
        started,
    )
    return scored


# ---------- Per-agent emitter closure ----------


def _make_agent_emitter(
    outer: EventEmitter | None,
    agent: AgentConfig,
    started: float,
) -> EventEmitter | None:
    """Return an emitter that stamps ``agent`` + ``t_ms`` before forwarding.

    Returns ``None`` when no outer emitter is wired so :func:`run_agent`
    can cheaply skip emission. The agent colour is stamped into the payload
    (not the envelope) so the frontend can pick it up from any event type.
    """
    if outer is None:
        return None

    async def _emitter(event: dict[str, Any]) -> None:
        """Stamp the race-level fields and forward to the caller's emitter."""
        stamped = dict(event)
        stamped.setdefault("agent", agent.name)
        stamped["t_ms"] = int((time.monotonic() - started) * 1000)
        payload = dict(stamped.get("payload") or {})
        payload.setdefault("colour", agent.colour)
        stamped["payload"] = payload
        await outer(stamped)

    return _emitter


async def _emit(
    emitter: EventEmitter | None,
    event_type: str,
    agent: str | None,
    payload: dict[str, Any],
    started: float,
) -> None:
    """Fire a race-level event (not attributed to any single agent's emitter)."""
    if emitter is None:
        return
    await emitter(
        {
            "type": event_type,
            "agent": agent,
            "t_ms": int((time.monotonic() - started) * 1000),
            "payload": payload,
        }
    )


# ---------- Failure-plan helpers ----------


def _deadline_plan(agent_name: str) -> dict[str, Any]:
    """Placeholder plan for an agent that blew the race deadline."""
    return {
        "agent_name": agent_name,
        "error": "deadline_exceeded",
        "pois": [],
        "legs": [],
        "total_minutes": 0,
        "total_cost_sgd": 0,
        "narrative": "[agent exceeded race deadline]",
    }


def _crash_plan(agent_name: str, exc: BaseException) -> dict[str, Any]:
    """Placeholder plan for an agent that raised an uncaught exception."""
    return {
        "agent_name": agent_name,
        "error": f"{type(exc).__name__}: {exc}",
        "pois": [],
        "legs": [],
        "total_minutes": 0,
        "total_cost_sgd": 0,
        "narrative": "[agent crashed]",
    }


def _spec_to_dict(spec: Any) -> dict[str, Any]:
    """Coerce a :class:`~app.models.Spec` or dict into a plain dict."""
    if isinstance(spec, dict):
        return spec
    dump = getattr(spec, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, dict):
            return result
    return {}


__all__ = ["EventEmitter", "run_race"]
