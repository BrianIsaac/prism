"""FastAPI entry point for the Prism backend (v2, live-only).

Endpoint surface:
    GET  /health                                — liveness probe
    POST /race                                  — kick off a three-agent race (async)
    GET  /race/{race_id}/stream                 — SSE feed of race events
    GET  /race/{race_id}/events                 — fast-polling fallback for the same feed
    POST /rating                                — HITL rating, weight drift, plan pin
    POST /feedback                              — free-text feedback on a plan
    GET  /feedback                              — feedback list (optionally filtered)
    GET  /races                                 — past races
    GET  /validated                             — validated plans for the globe
    POST /validated/{id}/like                   — like (materialises auto-pinned rows)
    GET  /alternatives                          — POI candidates for the stop-swap UI
    GET  /trace/{race_id}                       — trace rows for a single race
    GET  /admin/weights                         — frozen vs runtime weights
    GET  /admin/weight-history                  — chronological weight snapshots
    GET  /admin/bug-report                      — aggregated failing-tool-call report
    GET  /admin/feedback-digest                 — latest digest + raw tail
    POST /admin/feedback-digest/rebuild         — manual digest rebuild
    GET  /admin/live-feed                       — API-calls-per-category for the live feed
    GET  /grabmaps-proxy/style.json             — MapLibre style proxy (server-side Bearer)
    GET  /grabmaps-proxy/traffic-tile/{z}/{x}/{y}.json   — traffic GeoJSON tile proxy
    GET  /grabmaps-proxy/incidents-tile/{z}/{x}/{y}      — incident GeoJSON tile proxy

Phase 7 closes out the skeleton: the rate-limit middleware (/race = 10/min,
everything else = 200/min), the 5-minute race-result memoisation cache keyed
on ``(query_normalised, spec_override_hash)``, the GrabMaps style/tile proxy
(so the frontend never sees the Bearer token), and the bodies for every
endpoint previously stubbed by Phase 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sse_starlette.sse import EventSourceResponse

from app.config import (
    CORS_ORIGINS,
    GRABMAPS_API_KEY,
    GRABMAPS_BASE_URL,
)
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
    list_plans_by_race,
    list_races,
    list_validated_plans,
    materialise_auto_pinned,
    upsert_plan_atom,
)
from app.tools.grabmaps import places_search
from app.tools.live import get_street_view
from app.trace_export import export_bug_report

log = logging.getLogger("prism")


# ---------- Rate-limit configuration ----------

_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/race": (10, 60),
}
_DEFAULT_RATE_LIMIT: tuple[int, int] = (200, 60)
_rate_tracker: dict[tuple[str, str], list[float]] = defaultdict(list)


# ---------- Race-result cache + per-race event queues ----------

_RACE_CACHE_TTL_SECONDS: float = 300.0
# cache_key -> (cached_at, race_id)
_race_cache: dict[str, tuple[float, str]] = {}
# race_id -> subscriber queues drained by /race/{id}/stream
_race_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
# race_id -> replayable event buffer for polling + late subscribers
_race_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
# race_id -> True once race_complete or error has been emitted
_race_terminal: dict[str, bool] = {}


def _cache_key(query: str, spec_override: Any | None) -> str:
    """Compose the memoisation cache key for a race request.

    The key combines the whitespace-collapsed lowercase query and a
    deterministic SHA-256 of the spec-override payload. Two requests collide
    only when both components match, so a caller who passes an override gets
    its own cache slot rather than poisoning the plain-query slot.

    Args:
        query: The raw user query.
        spec_override: The parsed :class:`SpecOverride` or ``None``.

    Returns:
        A compact string suitable for use as a dict key.
    """
    normalised = " ".join(query.strip().lower().split())
    if spec_override is None:
        override_hash = "none"
    else:
        override_dict = spec_override.model_dump(exclude_none=True)
        override_blob = json.dumps(override_dict, sort_keys=True)
        override_hash = hashlib.sha256(override_blob.encode("utf-8")).hexdigest()[:16]
    return f"{normalised}|{override_hash}"


# ---------- Background tasks ----------

_background_tasks: set[asyncio.Task[Any]] = set()


def _spawn_background(coro: Any) -> None:
    """Create a background task and retain a strong reference until done.

    Without this guard Python 3.12 may garbage-collect a bare
    ``asyncio.create_task`` reference mid-flight, cancelling an in-progress
    race or digest rebuild. The done-callback clears the strong ref so the
    set does not grow unboundedly.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# ---------- Runtime weights (drift via HITL, seeded from frozen defaults) ----------

_runtime_weights: dict[str, float] = dict(HARNESS_WEIGHTS)


# ---------- Lifespan ----------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI requires the positional arg
    """Initialise the SQLite schema and seed the first weight snapshot.

    Logs a warning when CORS_ORIGINS is still at its dev default but the
    process appears to be running on a non-localhost host — otherwise a
    deployed UI would silently fail every cross-origin request.
    """
    import os

    all_dev = all(
        o.startswith(("http://localhost", "http://127.0.0.1")) for o in CORS_ORIGINS
    )
    deployed_host = os.environ.get("HOSTNAME", "")
    if all_dev and deployed_host and not deployed_host.startswith(("localhost", "127.")):
        log.warning(
            "CORS_ORIGINS is still at the dev default (%s) but HOSTNAME=%s "
            "looks like a deployed environment. Set CORS_ORIGINS to your "
            "frontend origin or cross-origin requests will be blocked.",
            ",".join(CORS_ORIGINS),
            deployed_host,
        )
    await init_db()
    existing = await fetch_weight_history(limit=1)
    if not existing:
        await insert_weight_snapshot(_runtime_weights)
    yield


app = FastAPI(title="Prism", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Per-IP sliding-window rate limit.

    ``/race`` gets the strict bucket (10 requests / 60 s); every other path
    shares the default bucket (200 / 60 s). ``/health`` and CORS preflights
    always bypass so the frontend can still liveness-probe under load.
    ``/race/{id}/stream`` and ``/race/{id}/events`` use the default bucket —
    opening a stream is cheap, only the ignition endpoint is expensive.
    """
    path = request.url.path
    if path == "/health" or request.method == "OPTIONS":
        return await call_next(request)

    if path in _RATE_LIMITS:
        limit, window = _RATE_LIMITS[path]
        bucket = path
    else:
        limit, window = _DEFAULT_RATE_LIMIT
        bucket = "__default__"
    client_host = request.client.host if request.client else "unknown"
    key = (client_host, bucket)

    now = time.time()
    fresh = [t for t in _rate_tracker[key] if t > now - window]
    _rate_tracker[key] = fresh

    if len(fresh) >= limit:
        retry_after = max(1, int(window - (now - fresh[0])) + 1)
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate limit exceeded",
                "limit": limit,
                "window_seconds": window,
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    fresh.append(now)
    _rate_tracker[key] = fresh
    return await call_next(request)


# ---------- Health ----------


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe — never rate limited."""
    return {"status": "ok", "harness_version": HARNESS_VERSION}


# ---------- Race handshake + SSE ----------


async def _emit_race_event(race_id: str, event: dict[str, Any]) -> None:
    """Fan an event out to the replay buffer and every live subscriber.

    The buffer drives the polling fallback and late subscribers — a client
    that opens the SSE stream after a few events have already shipped still
    sees the full race. Queue puts are non-blocking to guard against a slow
    subscriber stalling the race; full queues drop frames silently since
    the buffer is authoritative.
    """
    _race_events[race_id].append(event)
    if event.get("type") in {"race_complete", "error"}:
        # Only a terminal race_complete (not a per-agent error) closes streams.
        if event.get("type") == "race_complete":
            _race_terminal[race_id] = True
    for queue in list(_race_subscribers[race_id]):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _run_race_background(
    race_id: str,
    req: RaceRequest,
) -> None:
    """Background task body for a live race.

    Parses the spec, pulls hot candidates + the latest feedback digest, and
    drives :func:`run_race` with an emitter that dual-writes to the replay
    buffer and every live subscriber. On completion persists the race, each
    plan, and every trace row to SQLite, then flips the terminal flag so
    late subscribers close.
    """
    started = time.monotonic()
    try:
        spec = await parse_spec(req.query)
        if req.spec_override is not None:
            merged = spec.model_dump()
            merged.update(req.spec_override.model_dump(exclude_none=True))
            spec = type(spec)(**merged)

        hot = await fetch_hot_candidates(spec.country_iso3)
        digest = await get_latest_feedback_digest(scope="global")
        kb_block = format_digest_for_prompt(digest) or None

        async def _emitter(event: dict[str, Any]) -> None:
            await _emit_race_event(race_id, event)

        scored = await run_race(
            spec,
            hot_candidates=hot,
            feedback_kb=kb_block,
            weights=_runtime_weights,
            event_emitter=_emitter,
            race_id=race_id,
        )

        duration_seconds = time.monotonic() - started
        await insert_race(
            race_id=race_id,
            user_query=req.query,
            spec=spec.model_dump(),
            harness_version=HARNESS_VERSION,
            harness_weights=_runtime_weights,
            status="complete",
            duration_seconds=duration_seconds,
        )
        traces: list[dict[str, Any]] = []
        for plan in scored:
            plan_id = str(plan.get("plan_id") or uuid.uuid4())
            plan["plan_id"] = plan_id
            await insert_plan(
                plan_id=plan_id,
                race_id=race_id,
                plan=plan,
                country_iso3=spec.country_iso3,
            )
            for row in plan.get("traces") or []:
                row.setdefault("race_id", race_id)
                row.setdefault("id", str(uuid.uuid4()))
                traces.append(row)
        if traces:
            try:
                await insert_traces(traces)
            except Exception as exc:  # noqa: BLE001
                log.warning("insert_traces failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("background race %s failed", race_id)
        await _emit_race_event(
            race_id,
            {
                "type": "error",
                "agent": None,
                "t_ms": int((time.monotonic() - started) * 1000),
                "payload": {"message": f"{type(exc).__name__}: {exc}"},
            },
        )
    finally:
        _race_terminal[race_id] = True


@app.post("/race", response_model=RaceStartResponse)
async def race_start(req: RaceRequest) -> RaceStartResponse:
    """Kick off a three-agent race and return a stream handshake.

    Memoises on ``(query_normalised, spec_override_hash)`` for 5 minutes:
    an identical submission within that window returns the original race's
    handshake, and subscribers re-join the buffered stream immediately.
    """
    key = _cache_key(req.query, req.spec_override)
    cached = _race_cache.get(key)
    if cached is not None:
        cached_at, cached_race_id = cached
        if time.time() - cached_at < _RACE_CACHE_TTL_SECONDS:
            return RaceStartResponse(
                race_id=cached_race_id,
                stream_url=f"/race/{cached_race_id}/stream",
            )
        _race_cache.pop(key, None)

    race_id = str(uuid.uuid4())
    _race_events[race_id] = []
    _race_terminal[race_id] = False
    _race_cache[key] = (time.time(), race_id)
    _spawn_background(_run_race_background(race_id, req))
    return RaceStartResponse(race_id=race_id, stream_url=f"/race/{race_id}/stream")


@app.get("/race/{race_id}/stream")
async def race_stream(race_id: str) -> EventSourceResponse:
    """Subscribe to a race's SSE event stream.

    Replays any events already in the buffer (so a late subscriber catches
    the tool-calls that fired before its EventSource opened) then drains
    the per-subscriber queue until ``race_complete`` or the client closes.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    _race_subscribers[race_id].append(queue)

    async def _event_source():
        try:
            for event in list(_race_events.get(race_id, [])):
                yield {"event": "message", "data": json.dumps(event)}
            if _race_terminal.get(race_id):
                return
            while True:
                event = await queue.get()
                yield {"event": "message", "data": json.dumps(event)}
                if event.get("type") == "race_complete":
                    return
        finally:
            try:
                _race_subscribers[race_id].remove(queue)
            except ValueError:
                pass

    return EventSourceResponse(_event_source())


@app.get("/race/{race_id}/events")
async def race_events(race_id: str, since: int = 0) -> dict[str, Any]:
    """Fast-polling fallback for the same event feed.

    Returns every event with ``index >= since`` from the in-memory buffer.
    """
    events = list(_race_events.get(race_id, []))
    slice_start = max(0, int(since))
    return {
        "race_id": race_id,
        "since": slice_start,
        "events": events[slice_start:],
    }


# ---------- HITL rating ----------


@app.post("/rating")
async def rating_endpoint(rating: Rating) -> dict[str, Any]:
    """Record a HITL rating, pin the plan, drift weights, seed plan atoms."""
    plan_row = await get_plan(rating.plan_id)
    if plan_row is None:
        raise HTTPException(
            status_code=404, detail=f"plan not found: {rating.plan_id}"
        )

    plan_data: dict[str, Any] = plan_row.get("plan") or {}
    original_pois: list[dict[str, Any]] = plan_data.get("pois", []) or []
    effective_pois: list[dict[str, Any]] = (
        rating.pois_override or original_pois
    )
    first_poi: dict[str, Any] = effective_pois[0] if effective_pois else {}
    country_iso3 = plan_row.get("country_iso3", "SGP")

    validated_id = str(uuid.uuid4())
    await insert_validated_plan(
        validated_id=validated_id,
        plan_id=rating.plan_id,
        country_iso3=country_iso3,
        anchor_lat=first_poi.get("lat"),
        anchor_lng=first_poi.get("lng"),
        hitl_rating=rating.model_dump(exclude={"pois_override"}),
        pois_override=rating.pois_override,
    )

    total = rating.novelty + rating.efficiency + rating.vibe
    if total > 0:
        target = {
            "flow": rating.efficiency / total,
            "diversity": rating.novelty / total,
            "vibe": rating.vibe / total,
        }
        alpha = 0.02
        for k in _runtime_weights:
            _runtime_weights[k] = round(
                (1 - alpha) * _runtime_weights[k] + alpha * target.get(k, 0.0), 5
            )
    await insert_weight_snapshot(_runtime_weights)

    avg_score = (rating.novelty + rating.efficiency + rating.vibe) / 15.0
    for poi in effective_pois:
        poi_id = poi.get("id")
        if not poi_id:
            continue
        await upsert_plan_atom(
            poi_id=poi_id,
            country_iso3=country_iso3,
            poi=poi,
            score=avg_score,
        )

    return {
        "ok": True,
        "validated_id": validated_id,
        "weights": _runtime_weights,
    }


# ---------- Validated plans ----------


@app.get("/validated")
async def validated_endpoint(
    country_iso3: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List validated plans (explicit + auto-pinned)."""
    rows = await list_validated_plans(country_iso3=country_iso3, limit=limit)
    return {"validated_plans": rows, "count": len(rows)}


@app.post("/validated/{validated_id}/like")
async def like_validated(validated_id: str) -> dict[str, Any]:
    """Like a validated plan; materialise auto-pinned rows on first like."""
    real_id = validated_id
    if validated_id.startswith("auto-"):
        real_id = await materialise_auto_pinned(validated_id) or validated_id
    existing = await get_validated_plan(real_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"validated plan not found: {validated_id}"
        )
    likes = await increment_likes(real_id)
    return {"ok": True, "validated_id": real_id, "likes": likes}


# ---------- Alternatives (stop-swap UI) ----------


@app.get("/alternatives")
async def alternatives_endpoint(
    category: str,
    near_lat: float,
    near_lng: float,
    exclude: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Return POI alternatives for the stop-swap UI.

    Each returned POI is enriched with a street-view photo array via
    :func:`app.tools.live.get_street_view` so the Phase 5 swap dialog can
    render the same gallery UI it uses on the primary plan detail.
    """
    exclude_ids = {s for s in (exclude or "").split(",") if s}
    over_fetch = max(limit * 3, 10)
    try:
        payload = await places_search(
            keyword=category,
            near_lat=near_lat,
            near_lng=near_lng,
            country="SGP",
            limit=over_fetch,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alternatives places_search failed: %s", exc)
        return {"alternatives": [], "count": 0}

    places = payload.get("places") if isinstance(payload, dict) else None
    candidates: list[dict[str, Any]] = []
    for place in places or []:
        poi_id = place.get("poi_id") or place.get("id")
        if not poi_id or poi_id in exclude_ids:
            continue
        location = place.get("location") or {}
        lat = place.get("lat") or location.get("lat") or location.get("latitude")
        lng = place.get("lng") or location.get("lng") or location.get("longitude")
        if lat is None or lng is None:
            continue
        candidate: dict[str, Any] = {
            "id": str(poi_id),
            "name": place.get("name") or place.get("formatted_address") or str(poi_id),
            "category": category,
            "subcategory": (place.get("categories") or [None])[0],
            "lat": float(lat),
            "lng": float(lng),
            "description": place.get("description"),
            "price_tier": place.get("price_tier"),
            "avg_cost_sgd": place.get("avg_cost_sgd"),
            "dietary_tags": place.get("dietary_tags") or [],
            "tags": place.get("tags") or [],
        }
        try:
            sv = await get_street_view(
                lat=float(lat), lng=float(lng), radius_m=120, limit=4
            )
            photos = sv.get("photos") if isinstance(sv, dict) else None
            if photos:
                candidate["streetview_photos"] = [
                    {
                        "url": p.get("fileUrl") or p.get("url") or "",
                        "thumb_url": p.get("thumbUrl") or p.get("thumb_url"),
                        "heading": p.get("heading"),
                        "projection": p.get("projection") or "PLANE",
                    }
                    for p in photos
                    if isinstance(p, dict) and (p.get("fileUrl") or p.get("url"))
                ]
        except Exception as exc:  # noqa: BLE001
            log.debug("street-view lookup for alternative failed: %s", exc)
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return {"alternatives": candidates, "count": len(candidates)}


# ---------- Trace ----------


@app.get("/trace/{race_id}")
async def trace_endpoint(race_id: str) -> dict[str, Any]:
    """Return every trace row for a single race."""
    rows = await fetch_traces_by_race(race_id)
    return {"race_id": race_id, "traces": rows}


@app.get("/race/{race_id}/plans")
async def race_plans(race_id: str) -> dict[str, Any]:
    """Return every plan produced by the three racers for one race.

    Used by the Explore route-detail view to overlay all three agents'
    itineraries on the MapLibre canvas so the per-agent divergence is
    visible rather than only the winner's path.
    """
    rows = await list_plans_by_race(race_id)
    return {"race_id": race_id, "plans": rows, "count": len(rows)}


# ---------- Feedback ----------


@app.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest) -> dict[str, Any]:
    """Store free-text feedback and bump likes on the parent validated plan."""
    if await get_plan(req.plan_id) is None:
        raise HTTPException(
            status_code=404, detail=f"plan not found: {req.plan_id}"
        )
    effective_validated_id = req.validated_id
    if effective_validated_id is not None:
        if effective_validated_id.startswith("auto-"):
            effective_validated_id = (
                await materialise_auto_pinned(effective_validated_id)
                or effective_validated_id
            )
        if await get_validated_plan(effective_validated_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"validated plan not found: {req.validated_id}",
            )

    feedback_id = await insert_feedback(
        validated_id=effective_validated_id,
        plan_id=req.plan_id,
        question=req.question,
        response=req.response,
        sentiment=req.sentiment,
    )
    likes: int | None = None
    if effective_validated_id is not None:
        likes = await increment_likes(effective_validated_id)

    feedback_count = await count_feedback()
    if should_trigger_digest(feedback_count):
        _spawn_background(build_feedback_digest(scope="global"))

    return {
        "ok": True,
        "feedback_id": feedback_id,
        "likes": likes,
    }


@app.get("/feedback")
async def feedback_list(
    plan_id: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """List feedback rows, optionally filtered by ``plan_id``."""
    rows = await list_feedback(plan_id=plan_id, limit=limit)
    return {"feedback": rows, "count": len(rows)}


# ---------- Past races ----------


@app.get("/races")
async def races_list(limit: int = 50) -> dict[str, Any]:
    """List past races newest-first with their top-ranked plan attached."""
    rows = await list_races(limit=limit)
    return {"races": rows, "count": len(rows)}


# ---------- Admin ----------


@app.get("/admin/weights")
async def admin_weights() -> dict[str, Any]:
    """Return frozen defaults vs runtime-drifted weights."""
    return {
        "harness_version": HARNESS_VERSION,
        "frozen_defaults": HARNESS_WEIGHTS,
        "runtime": _runtime_weights,
    }


@app.get("/admin/weight-history")
async def admin_weight_history(limit: int = 200) -> dict[str, Any]:
    """Return chronological weight snapshots for the admin sparkline."""
    rows = await fetch_weight_history(limit=limit)
    return {"snapshots": rows, "count": len(rows)}


@app.get("/admin/bug-report")
async def admin_bug_report() -> dict[str, Any]:
    """Aggregate failing tool-calls into a structured bug report."""
    report = await export_bug_report()
    return {
        "generated_at": report.generated_at,
        "total_calls": report.total_calls,
        "failed_calls": report.failed_calls,
        "failures_by_tool": report.failures_by_tool,
        "failures_by_status": report.failures_by_status,
        "samples": report.samples,
        "markdown": report.to_markdown(),
    }


@app.get("/admin/feedback-digest")
async def admin_feedback_digest() -> dict[str, Any]:
    """Return the latest digest + digest history + a tail of raw feedback."""
    latest = await get_latest_feedback_digest(scope="global")
    history = await list_feedback_digests(scope="global", limit=10)
    raw_tail = await list_feedback(limit=12)
    return {
        "digest": latest,
        "history": history,
        "raw_tail": raw_tail,
    }


@app.post("/admin/feedback-digest/rebuild")
async def admin_feedback_digest_rebuild() -> dict[str, Any]:
    """Manually trigger a digest rebuild and return the new digest."""
    digest = await build_feedback_digest(scope="global")
    if digest is None:
        raise HTTPException(
            status_code=400,
            detail="digest rebuild produced no output (empty corpus or LLM error)",
        )
    _race_cache.clear()
    return {"ok": True, "digest": digest}


@app.get("/admin/live-feed")
async def admin_live_feed(window_seconds: int = 60) -> dict[str, Any]:
    """API calls per category over the last ``window_seconds`` seconds."""
    return await fetch_live_feed_counts(window_seconds=window_seconds)


# ---------- GrabMaps proxy (server-side Bearer auth) ----------


def _require_grabmaps_key() -> str:
    """Return the configured GrabMaps key, raising 503 when unset.

    The proxy is the single browser entry point to GrabMaps, so a missing key
    must surface as a clear 503 rather than a 401/400 from the upstream.
    """
    if not GRABMAPS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GRABMAPS_API_KEY is not set on the server",
        )
    return GRABMAPS_API_KEY


@app.get("/grabmaps-proxy/style.json")
async def grabmaps_style(theme: str = "satellite") -> JSONResponse:
    """Proxy the GrabMaps style JSON with server-side Bearer auth.

    The browser never sees the API key: it hits this endpoint and we forward
    the request upstream with the server-held Bearer token. The response is
    returned unchanged with a one-hour ``Cache-Control`` so the browser can
    re-use the style across pans.
    """
    key = _require_grabmaps_key()
    url = f"{GRABMAPS_BASE_URL}/api/style.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                params={"theme": theme},
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"grabmaps style upstream error: {exc.response.text[:200]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"grabmaps style fetch failed: {exc}"
        ) from exc
    return JSONResponse(
        body,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/grabmaps-proxy/traffic-tile/{z}/{x}/{y}.json")
async def grabmaps_traffic_tile(z: int, x: int, y: int) -> Response:
    """Proxy a traffic GeoJSON tile so the frontend never sees the API key."""
    key = _require_grabmaps_key()
    url = f"{GRABMAPS_BASE_URL}/api/v1/traffic-tiles/{z}/{x}/{y}.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {key}"}
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"grabmaps traffic tile upstream error: {exc.response.text[:200]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"grabmaps traffic tile fetch failed: {exc}"
        ) from exc
    media_type = response.headers.get("content-type", "application/json")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/grabmaps-proxy/incidents-tile/{z}/{x}/{y}")
async def grabmaps_incidents_tile(z: int, x: int, y: int) -> Response:
    """Proxy an incident tile so the frontend never sees the API key."""
    key = _require_grabmaps_key()
    url = f"{GRABMAPS_BASE_URL}/api/v1/traffic/incidents/tile/{z}/{x}/{y}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {key}"}
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"grabmaps incidents tile upstream error: {exc.response.text[:200]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"grabmaps incidents tile fetch failed: {exc}",
        ) from exc
    media_type = response.headers.get("content-type", "application/json")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/grabmaps-proxy/incidents-circle")
async def grabmaps_incidents_circle(
    lat: float, lng: float, radius: int = 20000
) -> JSONResponse:
    """Proxy incidents for an island-scale marker layer.

    The live GrabMaps surface only exposes ``/incidents/bbox`` (no circle
    variant), and the bbox endpoint rejects requests exceeding ~0.044° per
    side. This handler derives a square bbox centred on ``(lat, lng)`` with
    a half-side of ``radius`` metres, then splits any bbox larger than
    0.044° into a 2x2 grid so the caller's metre radius is still honoured.
    Feature collections from each tile are merged into a single response.
    """
    key = _require_grabmaps_key()
    half_deg_max = 0.022  # 0.044 / 2
    half_deg = max(min(radius / 111_000.0, half_deg_max * 4), 0.001)
    tiles_per_side = max(1, min(4, int((half_deg / half_deg_max) + 0.999)))
    step = (2.0 * half_deg) / tiles_per_side
    clamped_step = min(step, 0.044)

    combined: list[dict[str, Any]] = []
    url = f"{GRABMAPS_BASE_URL}/api/v1/traffic/incidents/bbox"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for ti in range(tiles_per_side):
                for tj in range(tiles_per_side):
                    min_lat = lat - half_deg + ti * clamped_step
                    max_lat = min(min_lat + clamped_step, lat + half_deg)
                    min_lng = lng - half_deg + tj * clamped_step
                    max_lng = min(min_lng + clamped_step, lng + half_deg)
                    params = {
                        "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}",
                        "linkReference": "GRAB_WAY",
                    }
                    try:
                        response = await client.get(
                            url,
                            params=params,
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        if response.status_code >= 400:
                            continue
                        tile = response.json()
                    except (httpx.HTTPError, ValueError):
                        continue
                    entries = (
                        tile.get("incidents")
                        if isinstance(tile, dict)
                        else None
                    ) or (tile if isinstance(tile, list) else [])
                    if isinstance(entries, list):
                        combined.extend(entries)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"grabmaps incidents fetch failed: {exc}",
        ) from exc
    return JSONResponse(
        {"incidents": combined, "count": len(combined)},
        headers={"Cache-Control": "public, max-age=30"},
    )


@app.get("/grabmaps-proxy/traffic-raster-tile/{z}/{x}/{y}")
async def grabmaps_traffic_raster_tile(z: int, x: int, y: int) -> Response:
    """Proxy the real-time raster traffic tile endpoint.

    Kept distinct from ``/grabmaps-proxy/traffic-tile/{z}/{x}/{y}.json``
    (GeoJSON) so the frontend can choose between the raster overlay
    (MapLibre ``type=raster`` source) and the vector tile.
    """
    key = _require_grabmaps_key()
    url = f"{GRABMAPS_BASE_URL}/api/v1/traffic/real-time/tile/{z}/{x}/{y}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {key}"}
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"grabmaps traffic raster tile upstream error: "
            f"{exc.response.text[:200]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"grabmaps traffic raster tile fetch failed: {exc}",
        ) from exc
    media_type = response.headers.get("content-type", "image/png")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=30"},
    )
