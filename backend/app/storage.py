"""SQLite persistence layer (async via ``aiosqlite``).

v2 carries the eight v1 tables verbatim and adds three new ones for the
hackathon's live-canvas surface:

    - ``traffic_snapshots``  — cached ``get_traffic`` responses
    - ``incident_snapshots`` — cached ``get_incidents`` responses
    - ``streetview_cache``   — per-tile OpenStreetCam photo cache

The v1 set (unchanged): ``races``, ``plans``, ``traces``, ``validated_plans``,
``feedback``, ``feedback_digest``, ``weight_history``, ``plan_atoms``.

The connection path is read from ``SQLITE_PATH`` (env var, then config
fallback) on every connection so the test fixtures can isolate per-test
databases without monkey-patching the module-level constant.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from app.config import SQLITE_PATH as _DEFAULT_SQLITE_PATH

# ---------- Schema ----------

_SCHEMA: str = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS races (
    id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    user_query TEXT NOT NULL,
    spec TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    harness_weights TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL REFERENCES races(id),
    agent_name TEXT NOT NULL,
    model TEXT,
    plan TEXT NOT NULL,
    hard_pass INTEGER NOT NULL,
    soft_scores TEXT,
    total_score REAL DEFAULT 0,
    rank INTEGER,
    tool_call_count INTEGER DEFAULT 0,
    country_iso3 TEXT DEFAULT 'SGP',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS validated_plans (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    country_iso3 TEXT NOT NULL,
    anchor_lat REAL,
    anchor_lng REAL,
    hitl_rating TEXT NOT NULL,
    pois_override TEXT,
    likes INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input TEXT,
    output TEXT,
    status TEXT NOT NULL,
    error TEXT,
    latency_ms REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plan_atoms (
    poi_id TEXT PRIMARY KEY,
    country_iso3 TEXT NOT NULL,
    poi TEXT NOT NULL,
    aggregate_score REAL NOT NULL,
    vote_count INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    flow REAL NOT NULL,
    diversity REAL NOT NULL,
    vibe REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    validated_id TEXT,
    plan_id TEXT NOT NULL,
    question TEXT NOT NULL,
    response TEXT NOT NULL,
    sentiment TEXT DEFAULT 'positive'
);

CREATE TABLE IF NOT EXISTS feedback_digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    scope TEXT NOT NULL DEFAULT 'global',
    summary TEXT NOT NULL,
    tags TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    model TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id TEXT PRIMARY KEY,
    race_id TEXT,
    bbox_key TEXT NOT NULL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_snapshots (
    id TEXT PRIMARY KEY,
    race_id TEXT,
    bbox_key TEXT NOT NULL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS streetview_cache (
    cache_key TEXT PRIMARY KEY,
    lat_round REAL NOT NULL,
    lng_round REAL NOT NULL,
    day_bucket TEXT NOT NULL,
    photo_urls TEXT NOT NULL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_validated_country ON validated_plans(country_iso3);
CREATE INDEX IF NOT EXISTS idx_traces_race ON traces(race_id);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_tool_created ON traces(tool_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_races_created ON races(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_atoms_country_score
    ON plan_atoms(country_iso3, aggregate_score DESC);
CREATE INDEX IF NOT EXISTS idx_weight_history_time ON weight_history(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_plan ON feedback(plan_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_validated ON feedback(validated_id);
CREATE INDEX IF NOT EXISTS idx_feedback_digest_scope
    ON feedback_digest(scope, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_bbox_time
    ON traffic_snapshots(bbox_key, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_incident_bbox_time
    ON incident_snapshots(bbox_key, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_sv_time ON streetview_cache(fetched_at DESC);
"""


_TOOL_CATEGORY: dict[str, str] = {
    "places_search": "search",
    "nearby_search": "search",
    "reverse_geocode": "search",
    "route": "routing",
    "route_matrix": "routing",
    "get_traffic": "traffic",
    "get_incidents": "incidents",
    "get_street_view": "streetview",
}


def _db_path() -> str:
    """Return the active SQLite path, honouring per-test ``SQLITE_PATH`` env."""
    return os.environ.get("SQLITE_PATH") or _DEFAULT_SQLITE_PATH


@asynccontextmanager
async def _connect():
    """Yield an aiosqlite connection with foreign keys enabled.

    Each call resolves the path freshly so test fixtures can swap databases
    without a module reload.
    """
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def init_db() -> None:
    """Create every table and index if absent. Idempotent across boots."""
    async with _connect() as db:
        await db.executescript(_SCHEMA)
        await db.commit()


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
    """Insert one race row. Spec and weights are JSON-encoded for storage."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO races
                (id, user_query, spec, harness_version, harness_weights, status, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                user_query,
                json.dumps(spec),
                harness_version,
                json.dumps(harness_weights),
                status,
                duration_seconds,
            ),
        )
        await db.commit()


async def list_races(limit: int = 50) -> list[dict[str, Any]]:
    """Return the newest races with their rank-1 plan attached.

    Each row carries ``race_id``, ``spec`` (decoded), and ``top_plan`` (the
    rank-1 plan dict or ``None`` when the race had no passing plan).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM races ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            race_d = dict(row)
            try:
                race_d["spec"] = json.loads(race_d.get("spec") or "{}")
            except (ValueError, TypeError):
                race_d["spec"] = {}
            try:
                race_d["harness_weights"] = json.loads(
                    race_d.get("harness_weights") or "{}"
                )
            except (ValueError, TypeError):
                race_d["harness_weights"] = {}
            race_d["race_id"] = race_d.pop("id")
            top = await db.execute_fetchall(
                """
                SELECT id, agent_name, plan, total_score, rank, hard_pass
                FROM plans
                WHERE race_id = ? AND hard_pass = 1
                ORDER BY rank ASC
                LIMIT 1
                """,
                (race_d["race_id"],),
            )
            if top:
                plan_d = dict(top[0])
                try:
                    plan_d["plan"] = json.loads(plan_d.get("plan") or "{}")
                except (ValueError, TypeError):
                    plan_d["plan"] = {}
                race_d["top_plan"] = plan_d
            else:
                race_d["top_plan"] = None
            results.append(race_d)
        return results


async def insert_plan(
    *,
    plan_id: str,
    race_id: str,
    plan: dict[str, Any],
    country_iso3: str,
) -> None:
    """Persist a single agent-produced plan, including its scoring metadata."""
    soft_scores = plan.get("soft_scores")
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO plans
                (id, race_id, agent_name, model, plan, hard_pass, soft_scores,
                 total_score, rank, tool_call_count, country_iso3)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                race_id,
                plan.get("agent_name", ""),
                plan.get("model"),
                json.dumps(plan),
                1 if plan.get("hard_pass") else 0,
                json.dumps(soft_scores) if soft_scores is not None else None,
                float(plan.get("total_score") or 0.0),
                plan.get("rank"),
                int(plan.get("tool_call_count") or 0),
                country_iso3,
            ),
        )
        await db.commit()


async def get_plan(plan_id: str) -> dict[str, Any] | None:
    """Return the persisted plan dict, or ``None`` when missing."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM plans WHERE id = ?",
            (plan_id,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        try:
            row["plan"] = json.loads(row.get("plan") or "{}")
        except (ValueError, TypeError):
            row["plan"] = {}
        if row.get("soft_scores"):
            try:
                row["soft_scores"] = json.loads(row["soft_scores"])
            except (ValueError, TypeError):
                row["soft_scores"] = None
        return row


# ---------- Traces ----------


async def insert_traces(traces: list[dict[str, Any]]) -> None:
    """Bulk-insert trace rows. Duplicate ids raise ``IntegrityError``."""
    if not traces:
        return
    rows = [
        (
            t["id"],
            t["race_id"],
            t["agent_name"],
            t["tool_name"],
            json.dumps(t.get("input")) if not isinstance(t.get("input"), str) else t["input"],
            (
                json.dumps(t.get("output"))
                if t.get("output") is not None and not isinstance(t.get("output"), str)
                else t.get("output")
            ),
            t["status"],
            t.get("error"),
            float(t.get("latency_ms") or 0.0),
        )
        for t in traces
    ]
    async with _connect() as db:
        await db.executemany(
            """
            INSERT INTO traces
                (id, race_id, agent_name, tool_name, input, output, status, error, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()


async def fetch_traces_by_race(race_id: str) -> list[dict[str, Any]]:
    """Return every trace row for a race, oldest-first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM traces WHERE race_id = ? ORDER BY created_at",
            (race_id,),
        )
        return [dict(row) for row in rows]


# ---------- Validated plans (HITL pinning + auto-pin synthesis) ----------


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
    """Insert a validated plan row (an explicit HITL pin)."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO validated_plans
                (id, plan_id, country_iso3, anchor_lat, anchor_lng,
                 hitl_rating, pois_override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validated_id,
                plan_id,
                country_iso3,
                anchor_lat,
                anchor_lng,
                json.dumps(hitl_rating),
                json.dumps(pois_override) if pois_override is not None else None,
            ),
        )
        await db.commit()


async def list_validated_plans(
    country_iso3: str | None = None,
    limit: int = 100,
    include_auto: bool = True,
) -> list[dict[str, Any]]:
    """Return validated plans for the globe, with optional auto-pin synthesis.

    Real validated rows always come first. When ``include_auto`` is true the
    remaining budget is filled with synthetic ``auto-<plan_id>`` rows derived
    from rank-1 passing plans of recent races that have not yet been
    explicitly pinned.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if country_iso3:
            real_rows = await db.execute_fetchall(
                """
                SELECT v.*, p.plan AS plan_json, p.total_score, p.agent_name
                FROM validated_plans v
                JOIN plans p ON v.plan_id = p.id
                WHERE v.country_iso3 = ?
                ORDER BY v.created_at DESC
                LIMIT ?
                """,
                (country_iso3, limit),
            )
        else:
            real_rows = await db.execute_fetchall(
                """
                SELECT v.*, p.plan AS plan_json, p.total_score, p.agent_name
                FROM validated_plans v
                JOIN plans p ON v.plan_id = p.id
                ORDER BY v.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        results: list[dict[str, Any]] = []
        seen_plan_ids: set[str] = set()
        for row in real_rows:
            d = dict(row)
            try:
                d["hitl_rating"] = json.loads(d.get("hitl_rating") or "{}")
            except (ValueError, TypeError):
                d["hitl_rating"] = {}
            if d.get("pois_override"):
                try:
                    d["pois_override"] = json.loads(d["pois_override"])
                except (ValueError, TypeError):
                    d["pois_override"] = None
            try:
                d["plan"] = json.loads(d.pop("plan_json") or "{}")
            except (ValueError, TypeError):
                d["plan"] = {}
            d["auto_pinned"] = False
            seen_plan_ids.add(d.get("plan_id", ""))
            results.append(d)

        if not include_auto or len(results) >= limit:
            return results

        remaining = limit - len(results)
        if country_iso3:
            auto_rows = await db.execute_fetchall(
                """
                SELECT p.*
                FROM plans p
                LEFT JOIN validated_plans v ON v.plan_id = p.id
                WHERE p.hard_pass = 1 AND p.rank = 1 AND v.id IS NULL
                  AND p.country_iso3 = ?
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (country_iso3, remaining),
            )
        else:
            auto_rows = await db.execute_fetchall(
                """
                SELECT p.*
                FROM plans p
                LEFT JOIN validated_plans v ON v.plan_id = p.id
                WHERE p.hard_pass = 1 AND p.rank = 1 AND v.id IS NULL
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (remaining,),
            )

        for row in auto_rows:
            plan_id = row["id"]
            if plan_id in seen_plan_ids:
                continue
            try:
                plan = json.loads(row["plan"] or "{}")
            except (ValueError, TypeError):
                plan = {}
            try:
                soft = json.loads(row["soft_scores"] or "{}") if row["soft_scores"] else {}
            except (ValueError, TypeError):
                soft = {}
            synthetic = {
                "novelty": 1 + round(4 * float(soft.get("diversity", 0.0))),
                "efficiency": 1 + round(4 * float(soft.get("flow", 0.0))),
                "vibe": 1 + round(4 * float(soft.get("vibe", 0.0))),
                "comment": None,
            }
            results.append(
                {
                    "id": f"auto-{plan_id}",
                    "plan_id": plan_id,
                    "country_iso3": row["country_iso3"],
                    "anchor_lat": None,
                    "anchor_lng": None,
                    "hitl_rating": synthetic,
                    "pois_override": None,
                    "likes": 0,
                    "created_at": row["created_at"],
                    "plan": plan,
                    "total_score": row["total_score"],
                    "agent_name": row["agent_name"],
                    "auto_pinned": True,
                }
            )

        return results


async def get_validated_plan(validated_id: str) -> dict[str, Any] | None:
    """Return one validated row, decoding the JSON columns lazily."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM validated_plans WHERE id = ?",
            (validated_id,),
        )
        if not rows:
            return None
        d = dict(rows[0])
        try:
            d["hitl_rating"] = json.loads(d.get("hitl_rating") or "{}")
        except (ValueError, TypeError):
            d["hitl_rating"] = {}
        if d.get("pois_override"):
            try:
                d["pois_override"] = json.loads(d["pois_override"])
            except (ValueError, TypeError):
                d["pois_override"] = None
        return d


async def increment_likes(validated_id: str) -> int:
    """Atomically bump the like counter and return the new total.

    Returns ``0`` for a missing row so the caller can branch on absence.
    """
    async with _connect() as db:
        await db.execute(
            "UPDATE validated_plans SET likes = COALESCE(likes, 0) + 1 WHERE id = ?",
            (validated_id,),
        )
        await db.commit()
        rows = await db.execute_fetchall(
            "SELECT likes FROM validated_plans WHERE id = ?",
            (validated_id,),
        )
        if not rows:
            return 0
        return int(rows[0][0] or 0)


async def materialise_auto_pinned(synthetic_id: str) -> str | None:
    """Convert an ``auto-<plan_id>`` synthetic row into a real validated row.

    Returns the new ``validated_plans.id`` (or the existing one if the plan
    has already been pinned), or ``None`` when the synthetic id cannot be
    resolved to an existing plan.
    """
    if not synthetic_id.startswith("auto-"):
        return synthetic_id
    plan_id = synthetic_id[len("auto-") :]
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        existing = await db.execute_fetchall(
            "SELECT id FROM validated_plans WHERE plan_id = ? LIMIT 1",
            (plan_id,),
        )
        if existing:
            return str(existing[0]["id"])
        plan_rows = await db.execute_fetchall(
            "SELECT plan, soft_scores, country_iso3 FROM plans WHERE id = ?",
            (plan_id,),
        )
        if not plan_rows:
            return None
        plan_row = plan_rows[0]
        try:
            soft = json.loads(plan_row["soft_scores"] or "{}") if plan_row["soft_scores"] else {}
        except (ValueError, TypeError):
            soft = {}
        synthetic = {
            "novelty": 1 + round(4 * float(soft.get("diversity", 0.0))),
            "efficiency": 1 + round(4 * float(soft.get("flow", 0.0))),
            "vibe": 1 + round(4 * float(soft.get("vibe", 0.0))),
            "comment": None,
        }
        new_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT INTO validated_plans
                (id, plan_id, country_iso3, anchor_lat, anchor_lng,
                 hitl_rating, pois_override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                plan_id,
                plan_row["country_iso3"],
                None,
                None,
                json.dumps(synthetic),
                None,
            ),
        )
        await db.commit()
        return new_id


# ---------- Feedback ----------


async def insert_feedback(
    *,
    validated_id: str | None,
    plan_id: str,
    question: str,
    response: str,
    sentiment: str,
) -> int:
    """Insert one feedback row and return its primary key."""
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO feedback (validated_id, plan_id, question, response, sentiment)
            VALUES (?, ?, ?, ?, ?)
            """,
            (validated_id, plan_id, question, response, sentiment),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def list_feedback(
    plan_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Return feedback rows newest-first, optionally filtered by ``plan_id``."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if plan_id is not None:
            rows = await db.execute_fetchall(
                "SELECT * FROM feedback WHERE plan_id = ? ORDER BY id DESC LIMIT ?",
                (plan_id, limit),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in rows]


async def count_feedback() -> int:
    """Return the total feedback row count (used for the digest cadence)."""
    async with _connect() as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) FROM feedback")
        return int(rows[0][0] or 0)


async def get_latest_feedback_digest(scope: str = "global") -> dict[str, Any] | None:
    """Return the most recent digest for a scope, or ``None``."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM feedback_digest WHERE scope = ? ORDER BY id DESC LIMIT 1",
            (scope,),
        )
        if not rows:
            return None
        d = dict(rows[0])
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except (ValueError, TypeError):
            d["tags"] = []
        return d


async def list_feedback_digests(
    scope: str = "global", limit: int = 10
) -> list[dict[str, Any]]:
    """Return digest history for a scope, newest first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM feedback_digest WHERE scope = ? ORDER BY id DESC LIMIT ?",
            (scope, limit),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except (ValueError, TypeError):
                d["tags"] = []
            out.append(d)
        return out


async def insert_feedback_digest(
    *,
    scope: str,
    summary: str,
    tags: list[dict[str, Any]],
    source_count: int,
    model: str,
) -> int:
    """Insert a digest row and return its primary key."""
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO feedback_digest (scope, summary, tags, source_count, model)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scope, summary, json.dumps(tags), source_count, model),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


# ---------- Weights ----------


async def insert_weight_snapshot(weights: dict[str, float]) -> None:
    """Append one row to ``weight_history`` for the admin sparkline."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO weight_history (flow, diversity, vibe) VALUES (?, ?, ?)",
            (
                float(weights.get("flow") or 0.0),
                float(weights.get("diversity") or 0.0),
                float(weights.get("vibe") or 0.0),
            ),
        )
        await db.commit()


async def fetch_weight_history(limit: int = 200) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` weight snapshots oldest-first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT * FROM (
                SELECT * FROM weight_history ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (limit,),
        )
        return [dict(row) for row in rows]


# ---------- Plan atoms (shared swarm overlay) ----------


async def upsert_plan_atom(
    *,
    poi_id: str,
    country_iso3: str,
    poi: dict[str, Any],
    score: float,
) -> None:
    """Upsert a plan atom, accumulating a running mean of votes.

    On first insert ``vote_count`` is 1 and ``aggregate_score`` is ``score``;
    on conflict the running mean is updated as
    ``(aggregate * count + score) / (count + 1)`` and ``vote_count`` bumped.
    """
    payload = json.dumps(poi)
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO plan_atoms (poi_id, country_iso3, poi, aggregate_score, vote_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(poi_id) DO UPDATE SET
                aggregate_score = (aggregate_score * vote_count + ?) / (vote_count + 1),
                vote_count = vote_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (poi_id, country_iso3, payload, float(score), float(score)),
        )
        await db.commit()


async def fetch_hot_candidates(
    country_iso3: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Return decoded POI dicts for the swarm overlay, highest score first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT * FROM plan_atoms
            WHERE country_iso3 = ?
            ORDER BY aggregate_score DESC
            LIMIT ?
            """,
            (country_iso3, limit),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                out.append(json.loads(row["poi"]))
            except (ValueError, TypeError):
                continue
        return out


# ---------- v2: traffic snapshots ----------


async def insert_traffic_snapshot(
    *,
    snapshot_id: str,
    bbox: tuple[float, float, float, float],
    payload: dict[str, Any],
    race_id: str | None = None,
) -> None:
    """Cache a ``get_traffic`` response keyed by ``snapshot_id``.

    Args:
        snapshot_id: Caller-supplied uuid for the row.
        bbox: ``(min_lat, min_lng, max_lat, max_lng)`` — hashed to a key for
            lookup so coincident requests can share a single cached payload.
        payload: Raw ``get_traffic`` JSON response.
        race_id: Optional parent race id when the snapshot was fetched
            inside a race.
    """
    bbox_key = _bbox_key(*bbox)
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO traffic_snapshots (id, race_id, bbox_key, payload)
            VALUES (?, ?, ?, ?)
            """,
            (snapshot_id, race_id, bbox_key, json.dumps(payload)),
        )
        await db.commit()


# ---------- v2: incident snapshots ----------


async def insert_incident_snapshot(
    *,
    snapshot_id: str,
    centre: tuple[float, float],
    radius_m: float,
    payload: dict[str, Any],
    race_id: str | None = None,
) -> None:
    """Cache a ``get_incidents`` response keyed by ``snapshot_id``.

    The incident endpoint is a centre/radius search rather than a bbox; the
    derived ``bbox_key`` rounds the centre + radius into the same hash space
    so coincident requests reuse the same row.
    """
    lat, lng = centre
    bbox_key = _bbox_key(
        lat - radius_m / 111_000,
        lng - radius_m / 111_000,
        lat + radius_m / 111_000,
        lng + radius_m / 111_000,
    )
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO incident_snapshots (id, race_id, bbox_key, payload)
            VALUES (?, ?, ?, ?)
            """,
            (snapshot_id, race_id, bbox_key, json.dumps(payload)),
        )
        await db.commit()


# ---------- v2: streetview cache ----------


async def get_streetview_cache(
    lat_round: float,
    lng_round: float,
    day_bucket: str,
) -> list[dict[str, Any]] | None:
    """Return the cached photos for a tile + day, or ``None`` on miss."""
    cache_key = _streetview_key(lat_round, lng_round, day_bucket)
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT photo_urls FROM streetview_cache WHERE cache_key = ?",
            (cache_key,),
        )
        if not rows:
            return None
        try:
            decoded = json.loads(rows[0]["photo_urls"] or "[]")
        except (ValueError, TypeError):
            return None
        if isinstance(decoded, list):
            return decoded
        return None


async def set_streetview_cache(
    lat_round: float,
    lng_round: float,
    day_bucket: str,
    photos: list[dict[str, Any]],
) -> None:
    """Write photos to the streetview cache (idempotent overwrite)."""
    cache_key = _streetview_key(lat_round, lng_round, day_bucket)
    payload = json.dumps(photos)
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO streetview_cache
                (cache_key, lat_round, lng_round, day_bucket, photo_urls)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                photo_urls = excluded.photo_urls,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (cache_key, lat_round, lng_round, day_bucket, payload),
        )
        await db.commit()


# ---------- v2: live-feed counters ----------


async def fetch_live_feed_counts(window_seconds: int = 60) -> dict[str, Any]:
    """Aggregate trace rows by category over a recent time window.

    Categories: ``search``, ``routing``, ``traffic``, ``incidents``,
    ``streetview``, ``other``. Tools not in the canonical map fall into
    ``other`` so the panel surfaces accidental tool additions rather than
    silently dropping them.
    """
    by_category: dict[str, int] = {
        "search": 0,
        "routing": 0,
        "traffic": 0,
        "incidents": 0,
        "streetview": 0,
        "other": 0,
    }
    total = 0
    async with _connect() as db:
        rows = await db.execute_fetchall(
            f"""
            SELECT tool_name, COUNT(*) AS n
            FROM traces
            WHERE created_at >= datetime('now', '-{int(window_seconds)} seconds')
            GROUP BY tool_name
            """,
        )
        for row in rows:
            tool_name = row[0]
            n = int(row[1] or 0)
            category = _TOOL_CATEGORY.get(str(tool_name), "other")
            by_category[category] += n
            total += n
    return {
        "by_category": by_category,
        "total_calls": total,
        "window_seconds": window_seconds,
    }


# ---------- Internal key helpers ----------


def _bbox_key(min_lat: float, min_lng: float, max_lat: float, max_lng: float) -> str:
    """Round a bbox to three decimal places for a hash-friendly cache key."""
    return (
        f"{round(min_lat, 3)}:{round(min_lng, 3)}:"
        f"{round(max_lat, 3)}:{round(max_lng, 3)}"
    )


def _streetview_key(lat_round: float, lng_round: float, day_bucket: str) -> str:
    """Compose the canonical cache key for a streetview tile + day."""
    return f"{lat_round}:{lng_round}:{day_bucket}"


__all__ = [
    "count_feedback",
    "fetch_hot_candidates",
    "fetch_live_feed_counts",
    "fetch_traces_by_race",
    "fetch_weight_history",
    "get_latest_feedback_digest",
    "get_plan",
    "get_streetview_cache",
    "get_validated_plan",
    "increment_likes",
    "init_db",
    "insert_feedback",
    "insert_feedback_digest",
    "insert_incident_snapshot",
    "insert_plan",
    "insert_race",
    "insert_traces",
    "insert_traffic_snapshot",
    "insert_validated_plan",
    "insert_weight_snapshot",
    "list_feedback",
    "list_feedback_digests",
    "list_races",
    "list_validated_plans",
    "materialise_auto_pinned",
    "set_streetview_cache",
    "upsert_plan_atom",
]
