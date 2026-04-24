"""Classic GrabMaps CRUD tools (live only).

Phase 3 implementation. The shell exposes the five function names :mod:`app.main`
imports for the ``/alternatives`` endpoint and the agent tool-belt registration.
No mock branches, no fixture fallbacks — Prism v2 is live-only.

Endpoints covered:
    - ``places_search``  → ``/maps/place/v2/text``
    - ``nearby_search``  → ``/maps/place/v2/nearby`` (radius in km)
    - ``reverse_geocode``→ ``/maps/geocode/v2/reverse``
    - ``route``          → ``/direction`` (coordinate order controlled via ``lat_first``)
    - ``route_matrix``   → local composite of N×M ``route`` calls; not a real endpoint
"""

from __future__ import annotations

from typing import Any


async def places_search(
    *,
    query: str,
    near: dict[str, float] | None = None,
    category: str | None = None,
    limit: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Text search over GrabMaps POIs (live).

    Phase 3 implementation.
    """
    raise NotImplementedError


async def nearby_search(
    *,
    lat: float,
    lng: float,
    radius_km: float,
    category: str | None = None,
    limit: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Radius search (radius in **km**). Phase 3 implementation."""
    raise NotImplementedError


async def reverse_geocode(
    *,
    lat: float,
    lng: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """Reverse geocode a single coordinate. Phase 3 implementation."""
    raise NotImplementedError


async def route(
    *,
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str = "walk",
    lat_first: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Single-leg routing (``/direction``).

    GrabMaps accepts ``lng,lat`` by default and ``lat,lng`` when ``lat_first``
    is set; agents commonly pass pairs in library order ``[lat, lng]`` so most
    call sites should pass ``lat_first=True``.

    Phase 3 implementation.
    """
    raise NotImplementedError


async def route_matrix(
    *,
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
    mode: str = "walk",
    lat_first: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Local composite — N×M calls to :func:`route`. Not a real endpoint.

    Phase 3 implementation.
    """
    raise NotImplementedError
