"""Classic GrabMaps CRUD tools: search, nearby, reverse-geocode, route, matrix.

All five wrappers issue **real** HTTP requests against the GrabMaps proxy at
:data:`app.config.GRABMAPS_BASE_URL` with a ``Bearer`` token. There is no
mock branch and no fixture fallback — Prism v2 is live-only and every
failure surfaces in the admin ``/admin/bug-report`` aggregate so the
operator can see which endpoints are regressing in real time.

Endpoint mapping:
    - ``places_search``   -> GET ``/api/v1/maps/poi/v1/search``
    - ``nearby_search``   -> GET ``/api/v1/maps/place/v2/nearby`` (radius in **km**)
    - ``reverse_geocode`` -> GET ``/api/v1/maps/poi/v1/reverse-geo``
    - ``route``           -> GET ``/api/v1/maps/eta/v1/direction`` (lat_first=true)
    - ``route_matrix``    -> local N x M composite of real ``route`` calls

See :mod:`app.tools.__init__` for the agent-facing dispatch and schema list.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.tools._http import get_json


_PLACES_SEARCH_PATH = "/api/v1/maps/poi/v1/search"
_NEARBY_PATH = "/api/v1/maps/place/v2/nearby"
_REVERSE_GEO_PATH = "/api/v1/maps/poi/v1/reverse-geo"
_DIRECTION_PATH = "/api/v1/maps/eta/v1/direction"

_DEFAULT_TIMEOUT = 15.0

_VALID_PROFILES = {"driving", "motorcycle", "tricycle", "cycling", "walking"}
_VALID_RANK_BY = {"distance", "popularity"}


def _as_location(lat: float, lng: float) -> str:
    """Format a coordinate pair as the ``lat,lng`` string every endpoint expects."""
    return f"{float(lat)},{float(lng)}"


async def places_search(
    *,
    keyword: str,
    country: str | None = None,
    near_lat: float | None = None,
    near_lng: float | None = None,
    limit: int = 10,
    **_: Any,
) -> dict[str, Any]:
    """Keyword POI search against the GrabMaps proxy.

    Args:
        keyword: Free-text query passed through to the upstream ``keyword`` param.
        country: Optional ISO3 country filter (e.g. ``"SGP"``).
        near_lat: Optional proximity-bias latitude.
        near_lng: Optional proximity-bias longitude.
        limit: Maximum number of places to return (1-25). Clamped by the proxy.

    Returns:
        The raw JSON body of the upstream response, typically
        ``{"places": [{"poi_id": ..., "location": ..., ...}, ...]}``.
    """
    if not (keyword or "").strip():
        raise ValueError("places_search requires a non-empty keyword")
    params: dict[str, Any] = {"keyword": keyword, "limit": int(limit)}
    if country:
        params["country"] = country
    if near_lat is not None and near_lng is not None:
        params["location"] = _as_location(near_lat, near_lng)
    return await get_json(_PLACES_SEARCH_PATH, params=params, timeout=_DEFAULT_TIMEOUT)


async def nearby_search(
    *,
    lat: float,
    lng: float,
    radius_km: float,
    rank_by: str | None = None,
    limit: int = 10,
    language: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Radius nearby-places search (``radius`` in **kilometres**).

    Args:
        lat: Latitude of the search centre.
        lng: Longitude of the search centre.
        radius_km: Radius in kilometres. The upstream ``/place/v2/nearby`` takes km.
        rank_by: Optional ranking hint: ``"distance"`` or ``"popularity"``.
        limit: Maximum results (1-25).
        language: Optional language code (e.g. ``"en"``).

    Returns:
        The raw JSON body, typically ``{"places": [...]}`` with the same entry
        shape as :func:`places_search`.
    """
    params: dict[str, Any] = {
        "location": _as_location(lat, lng),
        "radius": float(radius_km),
        "limit": int(limit),
    }
    if rank_by:
        if rank_by not in _VALID_RANK_BY:
            raise ValueError(
                f"rank_by must be one of {sorted(_VALID_RANK_BY)}; got {rank_by!r}"
            )
        params["rankBy"] = rank_by
    if language:
        params["language"] = language
    return await get_json(_NEARBY_PATH, params=params, timeout=_DEFAULT_TIMEOUT)


async def reverse_geocode(
    *,
    lat: float,
    lng: float,
    **_: Any,
) -> dict[str, Any]:
    """Reverse-geocode a single coordinate to its nearest POI.

    Args:
        lat: Latitude.
        lng: Longitude.

    Returns:
        The raw JSON body, typically ``{"places": [{"poi_id": ..., ...}]}``
        with a single entry.
    """
    params = {"location": _as_location(lat, lng)}
    return await get_json(_REVERSE_GEO_PATH, params=params, timeout=_DEFAULT_TIMEOUT)


async def route(
    *,
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    profile: str = "driving",
    alternatives: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Single-leg routing between two coordinates.

    ``lat_first=true`` is passed so agents can use the natural library
    ``[lat, lng]`` order (GrabMaps' default is ``lng, lat``).

    Args:
        origin_lat: Origin latitude.
        origin_lng: Origin longitude.
        dest_lat: Destination latitude.
        dest_lng: Destination longitude.
        profile: One of ``driving`` / ``motorcycle`` / ``tricycle`` /
            ``cycling`` / ``walking``.
        alternatives: When true, request alternate routes.

    Returns:
        The raw JSON body, typically ``{"code": "ok", "routes": [{...}]}``
        with ``distance`` (metres), ``duration`` (seconds), ``geometry``
        (polyline6), ``legs``, and ``fee``.
    """
    if profile not in _VALID_PROFILES:
        raise ValueError(
            f"profile must be one of {sorted(_VALID_PROFILES)}; got {profile!r}"
        )
    # httpx serialises repeated keys when the value is a list, which is what
    # ``/direction`` expects for coordinates.
    params: list[tuple[str, Any]] = [
        ("coordinates", _as_location(origin_lat, origin_lng)),
        ("coordinates", _as_location(dest_lat, dest_lng)),
        ("profile", profile),
        ("lat_first", "true"),
        ("overview", "full"),
        ("geometries", "polyline6"),
    ]
    if alternatives:
        params.append(("alternatives", "true"))
    return await get_json(_DIRECTION_PATH, params=params, timeout=_DEFAULT_TIMEOUT)


async def route_matrix(
    *,
    origins: list[dict[str, float] | tuple[float, float]],
    destinations: list[dict[str, float] | tuple[float, float]],
    profile: str = "driving",
    **_: Any,
) -> dict[str, Any]:
    """Composite N x M routing built from real ``/direction`` calls.

    No upstream matrix endpoint exists; this wrapper fans out the origin x
    destination grid as concurrent :func:`route` calls. A 5x5 matrix is 25
    real requests — agents should build only the cells they need, and the
    shared prompt reminds them to do so.

    Args:
        origins: List of ``[lat, lng]`` pairs (or ``{"lat", "lng"}`` dicts).
        destinations: Same shape as ``origins``.
        profile: Upstream routing profile passed through to each leg.

    Returns:
        ``{"matrix": [[{"distance": ..., "duration": ..., "fee": ...}, ...]]}``
        sized origins x destinations. Per-cell errors are reported as
        ``{"error": str}`` so the caller can still read the successful cells.
    """

    def _coerce(point: Any) -> tuple[float, float]:
        if isinstance(point, dict):
            lat = point.get("lat", point.get("latitude"))
            lng = point.get("lng", point.get("lon", point.get("longitude")))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            lat, lng = point[0], point[1]
        else:
            raise ValueError(f"route_matrix point must have lat/lng: {point!r}")
        if lat is None or lng is None:
            raise ValueError(f"route_matrix point missing lat/lng: {point!r}")
        return float(lat), float(lng)

    origin_pts = [_coerce(p) for p in origins]
    dest_pts = [_coerce(p) for p in destinations]

    async def _cell(
        origin: tuple[float, float], dest: tuple[float, float]
    ) -> dict[str, Any]:
        try:
            payload = await route(
                origin_lat=origin[0],
                origin_lng=origin[1],
                dest_lat=dest[0],
                dest_lng=dest[1],
                profile=profile,
            )
        except Exception as exc:  # noqa: BLE001 — per-cell failure is recorded, not raised
            return {"error": f"{type(exc).__name__}: {exc}"}
        routes = payload.get("routes") if isinstance(payload, dict) else None
        if not routes:
            return {"error": "no route"}
        first = routes[0]
        return {
            "distance": first.get("distance"),
            "duration": first.get("duration"),
            "fee": first.get("fee"),
        }

    tasks = [[_cell(o, d) for d in dest_pts] for o in origin_pts]
    matrix = [await asyncio.gather(*row) for row in tasks]
    return {"matrix": matrix, "profile": profile}


GRABMAPS_TOOLS: dict[str, Any] = {
    "places_search": places_search,
    "nearby_search": nearby_search,
    "reverse_geocode": reverse_geocode,
    "route": route,
    "route_matrix": route_matrix,
}


GRABMAPS_TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "places_search",
            "description": (
                "Keyword search for points of interest against live GrabMaps. "
                "Returns places[] with poi_id, location, name, formatted_address, "
                "business_type, categories, opening_hours (JSON-encoded string), "
                "and time_zone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Free-text query. Non-empty.",
                    },
                    "country": {
                        "type": "string",
                        "description": "Optional ISO3 country filter, e.g. 'SGP'.",
                    },
                    "near_lat": {
                        "type": "number",
                        "description": "Optional proximity-bias latitude.",
                    },
                    "near_lng": {
                        "type": "number",
                        "description": "Optional proximity-bias longitude.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "description": "Max results (1-25, default 10).",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nearby_search",
            "description": (
                "Radius search for places near a coordinate. radius_km is in "
                "kilometres. rank_by ranks results by 'distance' or 'popularity'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Centre latitude."},
                    "lng": {"type": "number", "description": "Centre longitude."},
                    "radius_km": {
                        "type": "number",
                        "description": "Search radius in kilometres.",
                    },
                    "rank_by": {
                        "type": "string",
                        "enum": ["distance", "popularity"],
                        "description": "Optional ranking hint.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "description": "Max results (1-25, default 10).",
                    },
                },
                "required": ["lat", "lng", "radius_km"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reverse_geocode",
            "description": (
                "Reverse-geocode a coordinate to its nearest POI. Returns "
                "places[0] with the same shape as places_search entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitude."},
                    "lng": {"type": "number", "description": "Longitude."},
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route",
            "description": (
                "Compute a single-leg route between two coordinates. Returns "
                "routes[0] with distance (metres), duration (seconds), "
                "geometry (polyline6), legs, and fee (amount + currency)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "dest_lat": {"type": "number"},
                    "dest_lng": {"type": "number"},
                    "profile": {
                        "type": "string",
                        "enum": [
                            "driving",
                            "motorcycle",
                            "tricycle",
                            "cycling",
                            "walking",
                        ],
                        "description": "Routing profile.",
                    },
                    "alternatives": {
                        "type": "boolean",
                        "description": "Return alternate routes when available.",
                    },
                },
                "required": [
                    "origin_lat",
                    "origin_lng",
                    "dest_lat",
                    "dest_lng",
                    "profile",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_matrix",
            "description": (
                "Pairwise durations and distances between N origins and M "
                "destinations. Composite of N*M real /direction calls — use "
                "sparingly. Returns matrix[i][j] = {distance, duration, fee}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origins": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "lat": {"type": "number"},
                                "lng": {"type": "number"},
                            },
                            "required": ["lat", "lng"],
                        },
                    },
                    "destinations": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "lat": {"type": "number"},
                                "lng": {"type": "number"},
                            },
                            "required": ["lat", "lng"],
                        },
                    },
                    "profile": {
                        "type": "string",
                        "enum": [
                            "driving",
                            "motorcycle",
                            "tricycle",
                            "cycling",
                            "walking",
                        ],
                    },
                },
                "required": ["origins", "destinations", "profile"],
            },
        },
    },
]
