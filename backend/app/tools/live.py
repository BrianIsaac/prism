"""Live overlay tools: traffic, incidents, street-view.

Phase 3 implementation. These three tools are new for v2 and push agents to
price routes against real conditions. No mock branches — all three reach
real GrabMaps endpoints.

Endpoints:
    - ``get_traffic``     → ``/traffic/circle`` (flow polylines in a radius)
    - ``get_incidents``   → ``/traffic/incidents/circle`` (radius in **metres**)
    - ``get_street_view`` → OpenStreetCam photos per POI, cached per tile + day
"""

from __future__ import annotations

from typing import Any


async def get_traffic(
    *,
    lat: float,
    lng: float,
    radius_m: float = 500,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return traffic-flow polylines in a radius around a coordinate.

    Phase 3 implementation.
    """
    raise NotImplementedError


async def get_incidents(
    *,
    lat: float,
    lng: float,
    radius_m: float = 1000,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return live incident reports in a radius (radius in **metres**).

    ``/traffic/incidents/bbox`` is capped at 0.044 degrees per side; callers
    that need more reach should prefer this circle variant.

    Phase 3 implementation.
    """
    raise NotImplementedError


async def get_street_view(
    *,
    lat: float,
    lng: float,
    day_bucket: str | None = None,
    limit: int = 4,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Return OpenStreetCam photos near a POI, cached per tile + day.

    OpenStreetCam is the slowest of the live-tool set; the per-tile cache in
    ``streetview_cache`` is mandatory rather than an optimisation. Phase 3
    wires the cache + upstream call.
    """
    raise NotImplementedError
