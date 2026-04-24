"""Race runner — orchestrates the three agents and streams events over SSE.

Phase 2 implementation. The shell exists so :mod:`app.main` can import
:func:`run_race` at boot time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


async def run_race(
    spec: Any,
    *,
    hot_candidates: list[dict[str, Any]] | None = None,
    feedback_kb: str | None = None,
    weights: dict[str, float] | None = None,
    event_emitter: EventEmitter | None = None,
) -> list[dict[str, Any]]:
    """Run the three-agent race and stream events via ``event_emitter``.

    Phase 2 implementation. For now this is a shell that returns an empty list;
    the SSE endpoint in :mod:`app.main` handles the pre-race handshake.

    Args:
        spec: Parsed :class:`~app.models.Spec`.
        hot_candidates: Plan atoms seeded from the shared swarm overlay.
        feedback_kb: Ambient feedback digest formatted for the prompt.
        weights: Runtime-drifted harness weights for this race.
        event_emitter: Async callback that forwards every event onto the
            per-race SSE queue.

    Returns:
        Scored plan dicts ready for persistence.
    """
    return []
