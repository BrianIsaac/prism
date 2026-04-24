"""FastAPI entry point for the Prism backend (v2, live-only).

Endpoint surface:
    GET  /health                           — liveness probe
    POST /race                             — kick off a three-agent race (async)
    GET  /race/{race_id}/stream            — SSE feed of race events
    GET  /race/{race_id}/events            — fast-polling fallback for the same feed
    POST /rating                           — HITL rating, weight drift, plan pin
    POST /feedback                         — free-text feedback on a plan
    GET  /feedback                         — feedback list (optionally filtered)
    GET  /races                            — past races
    GET  /validated                        — validated plans for the globe
    POST /validated/{id}/like              — like (materialises auto-pinned rows)
    GET  /alternatives                     — POI candidates for the stop-swap UI
    GET  /trace/{race_id}                  — trace rows for a single race
    GET  /admin/weights                    — frozen vs runtime weights
    GET  /admin/weight-history             — chronological weight snapshots
    GET  /admin/bug-report                 — aggregated failing-tool-call report
    GET  /admin/feedback-digest            — latest digest + raw tail
    POST /admin/feedback-digest/rebuild    — manual digest rebuild
    GET  /admin/live-feed                  — API-calls-per-category for the live feed

Phase 0 stubs every body except the race handshake (``POST /race`` and its two
stream endpoints) so the SSE backbone is exercisable end-to-end. Every other
endpoint raises :class:`NotImplementedError` until the owning phase fills it in.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.config import CORS_ORIGINS
from app.feedback_kb import (
    build_feedback_digest,
    format_digest_for_prompt,
    should_trigger_digest,
)
from app.harness import HARNESS_VERSION, HARNESS_WEIGHTS
from app.models import (
    FeedbackRequest,
    RaceRequest,
    RaceStartResponse,
    Rating,
)
from app.race import run_race
from app.spec import parse_spec
from app.storage import (
    count_feedback,
    fetch_hot_candidates,
    fetch_live_feed_counts,
    fetch_traces_by_race,
    fetch_weight_history,
    get_latest_feedback_digest,
    get_plan,
    get_validated_plan,
    increment_likes,
    init_db,
    insert_feedback,
    insert_plan,
    insert_race,
    insert_traces,
    insert_validated_plan,
    insert_weight_snapshot,
    list_feedback,
    list_feedback_digests,
    list_races,
    list_validated_plans,
    materialise_auto_pinned,
    upsert_plan_atom,
)
from app.tools.grabmaps import places_search
from app.trace_export import export_bug_report


# ---------- Runtime weights (drift via HITL, seeded from frozen defaults) ----------

_runtime_weights: dict[str, float] = dict(HARNESS_WEIGHTS)


# ---------- Lifespan ----------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI requires the positional arg
    """Initialise the SQLite schema on boot.

    Phase 7 layers the rate limiter and the 5-minute race cache on top. Phase 0
    only creates the schema so the endpoint skeleton can be exercised.
    """
    await init_db()
    yield


app = FastAPI(title="Prism", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------- Health ----------


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe — never rate limited."""
    return {"status": "ok", "harness_version": HARNESS_VERSION}


# ---------- Race handshake + SSE ----------


@app.post("/race", response_model=RaceStartResponse)
async def race_start(req: RaceRequest) -> RaceStartResponse:
    """Kick off a three-agent race and return a stream handshake.

    The race runs asynchronously; clients subscribe to ``stream_url`` for the
    event feed or poll ``GET /race/{id}/events?since=`` as a fallback. Phase 2
    wires the in-memory per-race event queue.
    """
    # Touch the request payload so static analysers acknowledge the field.
    # Phase 2 uses ``req.query`` + ``req.spec_override`` when seeding the race.
    _ = req.query
    race_id = str(uuid.uuid4())
    return RaceStartResponse(race_id=race_id, stream_url=f"/race/{race_id}/stream")


@app.get("/race/{race_id}/stream")
async def race_stream(race_id: str) -> EventSourceResponse:
    """Subscribe to a race's SSE event stream.

    Phase 0 yields a single placeholder frame and closes. Phase 2 hooks this
    up to a per-race ``asyncio.Queue`` populated by the race runner.
    """

    async def _event_source():
        yield {
            "event": "message",
            "data": (
                '{"type": "placeholder", "race_id": "' + race_id + '", "t_ms": 0}'
            ),
        }

    return EventSourceResponse(_event_source())


@app.get("/race/{race_id}/events")
async def race_events(race_id: str, since: int = 0) -> dict[str, Any]:
    """Fast-polling fallback for the same event feed.

    Returns every event with ``index >= since``. Phase 2 reads from the same
    queue :func:`race_stream` drains.
    """
    # Phase 2 populates the slice from the in-memory queue.
    _ = since
    return {"race_id": race_id, "since": since, "events": []}


# ---------- HITL rating ----------


@app.post("/rating")
async def rating_endpoint(rating: Rating) -> dict[str, Any]:
    """Record a HITL rating, pin the plan, drift weights, seed plan atoms."""
    _ = (
        rating,
        get_plan,
        insert_validated_plan,
        insert_weight_snapshot,
        upsert_plan_atom,
        _runtime_weights,
    )
    raise NotImplementedError


# ---------- Validated plans ----------


@app.get("/validated")
async def validated_endpoint(
    country_iso3: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List validated plans (explicit + auto-pinned)."""
    _ = (country_iso3, limit, list_validated_plans)
    raise NotImplementedError


@app.post("/validated/{validated_id}/like")
async def like_validated(validated_id: str) -> dict[str, Any]:
    """Like a validated plan; materialise auto-pinned rows on first like."""
    _ = (validated_id, materialise_auto_pinned, get_validated_plan, increment_likes)
    raise NotImplementedError


# ---------- Alternatives (stop-swap UI) ----------


@app.get("/alternatives")
async def alternatives_endpoint(
    category: str,
    near_lat: float,
    near_lng: float,
    exclude: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Return POI alternatives for the stop-swap UI."""
    _ = (category, near_lat, near_lng, exclude, limit, places_search)
    raise NotImplementedError


# ---------- Trace ----------


@app.get("/trace/{race_id}")
async def trace_endpoint(race_id: str) -> dict[str, Any]:
    """Return every trace row for a single race."""
    _ = (race_id, fetch_traces_by_race)
    raise NotImplementedError


# ---------- Feedback ----------


@app.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest) -> dict[str, Any]:
    """Store free-text feedback and bump likes on the parent validated plan."""
    _ = (
        req,
        get_plan,
        get_validated_plan,
        insert_feedback,
        increment_likes,
        count_feedback,
        should_trigger_digest,
        build_feedback_digest,
        materialise_auto_pinned,
    )
    raise NotImplementedError


@app.get("/feedback")
async def feedback_list(
    plan_id: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """List feedback rows, optionally filtered by ``plan_id``."""
    _ = (plan_id, limit, list_feedback)
    raise NotImplementedError


# ---------- Past races ----------


@app.get("/races")
async def races_list(limit: int = 50) -> dict[str, Any]:
    """List past races newest-first with their top-ranked plan attached."""
    _ = (limit, list_races)
    raise NotImplementedError


# ---------- Admin ----------


@app.get("/admin/weights")
async def admin_weights() -> dict[str, Any]:
    """Return frozen defaults vs runtime-drifted weights."""
    _ = (_runtime_weights,)
    raise NotImplementedError


@app.get("/admin/weight-history")
async def admin_weight_history(limit: int = 200) -> dict[str, Any]:
    """Return chronological weight snapshots for the admin sparkline."""
    _ = (limit, fetch_weight_history)
    raise NotImplementedError


@app.get("/admin/bug-report")
async def admin_bug_report() -> dict[str, Any]:
    """Aggregate failing tool-calls into a structured bug report."""
    _ = (export_bug_report,)
    raise NotImplementedError


@app.get("/admin/feedback-digest")
async def admin_feedback_digest() -> dict[str, Any]:
    """Return the latest digest + digest history + a tail of raw feedback."""
    _ = (
        get_latest_feedback_digest,
        list_feedback_digests,
        list_feedback,
        format_digest_for_prompt,
    )
    raise NotImplementedError


@app.post("/admin/feedback-digest/rebuild")
async def admin_feedback_digest_rebuild() -> dict[str, Any]:
    """Manually trigger a digest rebuild and return the new digest."""
    _ = (build_feedback_digest,)
    raise NotImplementedError


@app.get("/admin/live-feed")
async def admin_live_feed(window_seconds: int = 60) -> dict[str, Any]:
    """API calls per category over the last window_seconds seconds.

    Returns the bucket shape the frontend Live Feed panel expects, seeded with
    zeroes so the panel can render before Phase 3 wires the aggregator.
    """
    _ = (window_seconds, fetch_live_feed_counts)
    return {
        "by_category": {
            "search": 0,
            "routing": 0,
            "traffic": 0,
            "incidents": 0,
            "streetview": 0,
            "other": 0,
        },
        "total_calls": 0,
    }


# ---------- Cross-phase import touch ----------

# Prevent the unused-import linter from flushing Phase-owned symbols that
# ``main.py`` will wire into live bodies in later phases (run_race, parse_spec,
# fetch_hot_candidates, insert_race, insert_plan, insert_traces). Keeping the
# import surface here means no sibling shard needs to edit this file.
_PHASE_IMPORTS: tuple[Any, ...] = (
    run_race,
    parse_spec,
    fetch_hot_candidates,
    insert_race,
    insert_plan,
    insert_traces,
)
