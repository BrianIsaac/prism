"""Live-overlay tools: traffic, incidents, street-view.

Three real-time wrappers that push agents to price plans against the same
conditions the user will experience. Like :mod:`app.tools.grabmaps`, every
call here is a real HTTP request — no mocks, no fixtures.

Street-view is the slowest of the three endpoints (OpenStreetCam sits behind
the proxy), so :func:`get_street_view` consults :mod:`app.storage.streetview_cache`
first and only falls through to the network on a miss. Traffic and incident
responses are cached in-process with a 60-second TTL so an agent cannot
re-pull the same coordinate twice in a single race.

Endpoint mapping:
    - ``get_traffic``     -> GET ``/api/v1/traffic/real-time/bbox``
    - ``get_incidents``   -> GET ``/api/v1/traffic/incidents/bbox``
    - ``get_street_view`` -> GET ``/api/v1/openstreetcam-api/2.0/photo/``

The ``/circle`` variants the API reference mentions are not actually served
by the upstream (confirmed 404 on every probe). Traffic and incidents run
against ``/bbox`` with ``lat1/lat2/lon1/lon2`` params; the radius-based
agent-facing signature is preserved by deriving a square bbox centred on
``(lat, lng)`` with a half-side of ``radius_m`` metres (and clamping to
Grab's 0.044°-per-side limit to avoid a 400 Bad Request).
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from typing import Any

from app import storage
from app.config import (
    INCIDENT_CACHE_TTL_SECONDS,
    TRAFFIC_CACHE_TTL_SECONDS,
)
from app.tools._http import get_json


_TRAFFIC_BBOX_PATH = "/api/v1/traffic/real-time/bbox"
_INCIDENTS_BBOX_PATH = "/api/v1/traffic/incidents/bbox"
_STREETVIEW_PATH = "/api/v1/openstreetcam-api/2.0/photo/"

_DEFAULT_TIMEOUT = 15.0
_STREETVIEW_TIMEOUT = 30.0

# GrabMaps bbox endpoints reject requests wider than ~0.044° per side with
# ``invalid_argument``. Cap any caller-supplied radius (+ the metre-to-
# degree conversion below) so we never exceed that.
_MAX_HALF_DEGREE = 0.022
_METRES_PER_DEGREE = 111_000.0

_VALID_PROJECTIONS = {"PLANE", "SPHERE"}


_traffic_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_incident_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _bbox_from_radius(lat: float, lng: float, radius_m: float) -> tuple[float, float, float, float]:
    """Derive a square bbox centred on ``(lat, lng)`` from a metre radius.

    Returns ``(lat1, lat2, lon1, lon2)`` matching the param names
    GrabMaps' ``/bbox`` endpoints accept. Clamps to the upstream 0.044°
    ceiling so the request never gets rejected for size.
    """
    half_deg = min(_MAX_HALF_DEGREE, max(0.001, float(radius_m) / _METRES_PER_DEGREE))
    return (
        float(lat) - half_deg,
        float(lat) + half_deg,
        float(lng) - half_deg,
        float(lng) + half_deg,
    )


def _bbox_key(lat: float, lng: float, radius_m: float) -> str:
    """Canonical cache key for radius-based traffic/incident lookups."""
    return f"{round(float(lat), 3)}:{round(float(lng), 3)}:{int(radius_m)}"


async def get_traffic(
    *,
    lat: float,
    lng: float,
    radius_m: float = 500.0,
    **_: Any,
) -> dict[str, Any]:
    """Real-time traffic flow inside a circle.

    Args:
        lat: Centre latitude.
        lng: Centre longitude.
        radius_m: Radius in **metres** (the upstream ``/circle`` endpoint's unit).

    Returns:
        The raw JSON body. Typical shape:
        ``{"segments": [{"segmentId": ..., "congestion": "free"|"light"|"moderate"|"heavy", "avgSpeedKph": ...}, ...]}``.
    """
    key = _bbox_key(lat, lng, radius_m)
    now = time.monotonic()
    cached = _traffic_cache.get(key)
    if cached is not None and (now - cached[0]) < TRAFFIC_CACHE_TTL_SECONDS:
        return cached[1]

    lat1, lat2, lon1, lon2 = _bbox_from_radius(lat, lng, radius_m)
    body = await get_json(
        _TRAFFIC_BBOX_PATH,
        params={
            "lat1": lat1,
            "lat2": lat2,
            "lon1": lon1,
            "lon2": lon2,
            "linkReference": "GRAB_WAY",
        },
        timeout=_DEFAULT_TIMEOUT,
    )

    _traffic_cache[key] = (now, body)
    try:
        await storage.insert_traffic_snapshot(
            snapshot_id=str(uuid.uuid4()),
            bbox=(lat1, lon1, lat2, lon2),
            payload=body if isinstance(body, dict) else {"raw": body},
        )
    except (NotImplementedError, Exception):  # noqa: BLE001 — storage may be mid-boot
        pass
    return body


async def get_incidents(
    *,
    lat: float,
    lng: float,
    radius_m: float = 1000.0,
    **_: Any,
) -> dict[str, Any]:
    """Live traffic incidents inside a circle.

    The ``/incidents/bbox`` variant is capped at 0.044 degrees per side; the
    ``/circle`` endpoint lets callers request a larger radius without the 400.

    Args:
        lat: Centre latitude.
        lng: Centre longitude.
        radius_m: Radius in **metres**.

    Returns:
        The raw JSON body. Typical shape:
        ``{"incidents": [{"type": "accident"|..., "severity": 1..5, "location": {...}, "description": ...}, ...]}``.
    """
    key = _bbox_key(lat, lng, radius_m)
    now = time.monotonic()
    cached = _incident_cache.get(key)
    if cached is not None and (now - cached[0]) < INCIDENT_CACHE_TTL_SECONDS:
        return cached[1]

    lat1, lat2, lon1, lon2 = _bbox_from_radius(lat, lng, radius_m)
    body = await get_json(
        _INCIDENTS_BBOX_PATH,
        params={
            "lat1": lat1,
            "lat2": lat2,
            "lon1": lon1,
            "lon2": lon2,
            "linkReference": "GRAB_WAY",
        },
        timeout=_DEFAULT_TIMEOUT,
    )

    _incident_cache[key] = (now, body)
    try:
        await storage.insert_incident_snapshot(
            snapshot_id=str(uuid.uuid4()),
            centre=(float(lat), float(lng)),
            radius_m=float(radius_m),
            payload=body if isinstance(body, dict) else {"raw": body},
        )
    except (NotImplementedError, Exception):  # noqa: BLE001 — storage may be mid-boot
        pass
    return body


async def get_street_view(
    *,
    lat: float,
    lng: float,
    radius_m: float = 100.0,
    limit: int = 4,
    projection: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """OpenStreetCam photos near a coordinate, cached per tile + day.

    Cache strategy:
        - Read: :func:`app.storage.get_streetview_cache` keyed on
          (round(lat, 3), round(lng, 3), today's date).
        - Write: :func:`app.storage.set_streetview_cache` after a successful
          upstream fetch. Cache failures are swallowed so a mid-boot storage
          layer cannot block a live tool call.

    Args:
        lat: Centre latitude.
        lng: Centre longitude.
        radius_m: Search radius in metres.
        limit: Max photos to return.
        projection: Optional filter — ``"PLANE"`` (rectilinear) or ``"SPHERE"`` (360).

    Returns:
        ``{"photos": [{"fileUrl": ..., "thumbUrl": ..., "heading": ..., "projection": ...}, ...]}``.
    """
    if projection is not None and projection not in _VALID_PROJECTIONS:
        raise ValueError(
            f"projection must be one of {sorted(_VALID_PROJECTIONS)}; got {projection!r}"
        )

    lat_round = round(float(lat), 3)
    lng_round = round(float(lng), 3)
    day_bucket = date.today().isoformat()

    try:
        cached_photos = await storage.get_streetview_cache(
            lat_round=lat_round,
            lng_round=lng_round,
            day_bucket=day_bucket,
        )
    except (NotImplementedError, Exception):  # noqa: BLE001 — storage may be mid-boot
        cached_photos = None
    if cached_photos:
        return {"photos": cached_photos, "cached": True}

    # Default to ``SPHERE`` (360° panorama) so the vibe judge gets
    # spherical imagery when the caller does not specify — matches the
    # grabmaps_api_reference.md §OpenStreetCam note that vibe scoring
    # should ground in pano photos where available.
    effective_projection = projection or "SPHERE"
    params: dict[str, Any] = {
        "lat": float(lat),
        "lng": float(lng),
        "radius": int(radius_m),
        "limit": int(limit),
        "projection": effective_projection,
    }
    body = await get_json(
        _STREETVIEW_PATH,
        params=params,
        timeout=_STREETVIEW_TIMEOUT,
    )

    if isinstance(body, dict):
        photos = body.get("photos") or body.get("result") or body.get("data") or []
    elif isinstance(body, list):
        photos = body
    else:
        photos = []

    if not isinstance(photos, list):
        photos = []

    try:
        await storage.set_streetview_cache(
            lat_round=lat_round,
            lng_round=lng_round,
            day_bucket=day_bucket,
            photos=photos,
        )
    except (NotImplementedError, Exception):  # noqa: BLE001 — cache write is best-effort
        pass

    return {"photos": photos, "cached": False}


GRABMAPS_TOOLS: dict[str, Any] = {
    "get_traffic": get_traffic,
    "get_incidents": get_incidents,
    "get_street_view": get_street_view,
}


GRABMAPS_TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_traffic",
            "description": (
                "Real-time traffic flow in a circle around a coordinate. "
                "Returns segments[] with congestion class and avgSpeedKph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "description": "Radius in metres.",
                    },
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incidents",
            "description": (
                "Live traffic incidents in a circle around a coordinate. "
                "Returns incidents[] with type, severity (1-5), and location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "description": "Radius in metres.",
                    },
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_street_view",
            "description": (
                "OpenStreetCam photos near a coordinate. Returns photos[] with "
                "fileUrl, thumbUrl, heading, projection (PLANE or SPHERE). "
                "Cached aggressively per tile + day because the upstream is slow."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "radius_m": {
                        "type": "number",
                        "description": "Radius in metres (default 100).",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Max photos (1-20, default 4).",
                    },
                    "projection": {
                        "type": "string",
                        "enum": ["PLANE", "SPHERE"],
                        "description": "Optional projection filter.",
                    },
                },
                "required": ["lat", "lng"],
            },
        },
    },
]
