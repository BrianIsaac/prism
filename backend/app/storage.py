"""SQLite persistence layer (async via ``aiosqlite``).

Phase 0 provides import-time shells only. Phase 1 writes the schema DDL and the
CRUD bodies. v2 adds three tables on top of the v1 set:

    - ``traffic_snapshots``  — cached ``get_traffic`` responses
    - ``incident_snapshots`` — cached ``get_incidents`` responses
    - ``streetview_cache``   — per-tile OpenStreetCam photo cache

The v1 set (unchanged): ``races``, ``plans``, ``traces``, ``validated_plans``,
``feedback``, ``feedback_digests``, ``weight_snapshots``, ``plan_atoms``.
"""

from __future__ import annotations

from typing import Any


# ---------- Schema bootstrap ----------


async def init_db() -> None:
    """Create all SQLite tables if they do not already exist.

    Phase 1 implementation.
    """
    return None


# ---------- Races + plans ----------


async def insert_race(
    *,
    race_id: str,
    user_query: str,
    spec: dict[str, Any],
    harness_version: str,
    harness_weights: dict[str, float],
    status: str,
    duration_seconds: float,
) -> None:
    """Insert a race row. Phase 1 implementation."""
    raise NotImplementedError


async def list_races(limit: int = 50) -> list[dict[str, Any]]:
    """List past races newest-first with their top-ranked plan attached."""
    raise NotImplementedError


async def insert_plan(
    *,
    plan_id: str,
    race_id: str,
    plan: dict[str, Any],
    country_iso3: str,
) -> None:
    """Persist a single plan produced by an agent."""
    raise NotImplementedError


async def get_plan(plan_id: str) -> dict[str, Any] | None:
    """Fetch a plan by id (or ``None`` if absent)."""
    raise NotImplementedError


# ---------- Traces ----------


async def insert_traces(traces: list[dict[str, Any]]) -> None:
    """Bulk-insert tool-call trace rows."""
    raise NotImplementedError


async def fetch_traces_by_race(race_id: str) -> list[dict[str, Any]]:
    """Return every trace row for a single race."""
    raise NotImplementedError


# ---------- Validated plans (HITL pinning) ----------


async def insert_validated_plan(
    *,
    validated_id: str,
    plan_id: str,
    country_iso3: str,
    anchor_lat: float | None,
    anchor_lng: float | None,
    hitl_rating: dict[str, Any],
    pois_override: list[dict[str, Any]] | None,
) -> None:
    """Insert a validated plan row (explicit HITL pin)."""
    raise NotImplementedError


async def list_validated_plans(
    country_iso3: str | None = None,
    limit: int = 100,
    include_auto: bool = True,
) -> list[dict[str, Any]]:
    """List validated plans for the frontend globe.

    When ``include_auto`` is true, synthesise ``auto-<plan_id>`` rows for
    rank-1 plans from recent races that have not yet been explicitly pinned.
    """
    raise NotImplementedError


async def get_validated_plan(validated_id: str) -> dict[str, Any] | None:
    """Fetch a validated plan by id (or ``None`` if absent)."""
    raise NotImplementedError


async def increment_likes(validated_id: str) -> int:
    """Atomically bump the like counter and return the new total."""
    raise NotImplementedError


async def materialise_auto_pinned(synthetic_id: str) -> str | None:
    """Materialise an ``auto-<plan_id>`` synthetic row into a real validated_plans row.

    Returns the new validated_id on success, or ``None`` when the synthetic id
    cannot be resolved to a real plan.
    """
    raise NotImplementedError


# ---------- Feedback ----------


async def insert_feedback(
    *,
    validated_id: str | None,
    plan_id: str,
    question: str,
    response: str,
    sentiment: str,
) -> int:
    """Insert a feedback row and return the new primary key."""
    raise NotImplementedError


async def list_feedback(
    plan_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List feedback rows, optionally filtered by plan."""
    raise NotImplementedError


async def count_feedback() -> int:
    """Return the total feedback row count (used by the digest cadence trigger)."""
    raise NotImplementedError


async def get_latest_feedback_digest(scope: str = "global") -> dict[str, Any] | None:
    """Return the most recent digest for a scope (or ``None``)."""
    raise NotImplementedError


async def list_feedback_digests(
    scope: str = "global", limit: int = 10
) -> list[dict[str, Any]]:
    """Return digest history for a scope, newest first."""
    raise NotImplementedError


async def insert_feedback_digest(
    *,
    scope: str,
    summary: str,
    tags: list[dict[str, Any]],
    source_count: int,
    model: str,
) -> int:
    """Insert a digest row and return its primary key."""
    raise NotImplementedError


# ---------- Weights ----------


async def insert_weight_snapshot(weights: dict[str, float]) -> None:
    """Append a weight snapshot for the admin sparkline."""
    raise NotImplementedError


async def fetch_weight_history(limit: int = 200) -> list[dict[str, Any]]:
    """Return chronological weight snapshots (oldest → newest)."""
    raise NotImplementedError


# ---------- Plan atoms (shared swarm overlay) ----------


async def upsert_plan_atom(
    *,
    poi_id: str,
    country_iso3: str,
    poi: dict[str, Any],
    score: float,
) -> None:
    """Upsert a shared plan atom used by the swarm overlay."""
    raise NotImplementedError


async def fetch_hot_candidates(country_iso3: str) -> list[dict[str, Any]]:
    """Return plan atoms with the highest scores for the given country."""
    raise NotImplementedError


# ---------- v2: traffic snapshots ----------


async def insert_traffic_snapshot(
    *,
    snapshot_id: str,
    bbox: tuple[float, float, float, float],
    payload: dict[str, Any],
) -> None:
    """Cache a ``get_traffic`` response keyed by snapshot id."""
    raise NotImplementedError


# ---------- v2: incident snapshots ----------


async def insert_incident_snapshot(
    *,
    snapshot_id: str,
    centre: tuple[float, float],
    radius_m: float,
    payload: dict[str, Any],
) -> None:
    """Cache a ``get_incidents`` response keyed by snapshot id."""
    raise NotImplementedError


# ---------- v2: streetview cache ----------


async def get_streetview_cache(
    lat_round: float,
    lng_round: float,
    day_bucket: str,
) -> list[dict[str, Any]] | None:
    """Return cached OpenStreetCam photos for a tile + day, or ``None``."""
    raise NotImplementedError


async def set_streetview_cache(
    lat_round: float,
    lng_round: float,
    day_bucket: str,
    photos: list[dict[str, Any]],
) -> None:
    """Write OpenStreetCam photos to the per-tile cache."""
    raise NotImplementedError


# ---------- v2: live-feed counters ----------


async def fetch_live_feed_counts(window_seconds: int = 60) -> dict[str, Any]:
    """Aggregate tool-call trace rows by category for the admin live-feed panel.

    Returns a dict shaped as ``{"by_category": {...}, "total_calls": int}`` so
    the frontend can render one bar per category without additional reshaping.
    """
    raise NotImplementedError
