"""Tests for the SQLite persistence layer (v2).

Lifts the v1 storage-row coverage from
``example-code/backend/tests/test_endpoints_and_tools.py`` and adds the v2
deltas: traffic + incident snapshots, streetview cache hit/miss, and the
live-feed counter aggregator that the admin panel depends on.
"""

from __future__ import annotations

import asyncio
import uuid

import aiosqlite
import pytest

from app.storage import (
    count_feedback,
    fetch_hot_candidates,
    fetch_live_feed_counts,
    fetch_traces_by_race,
    fetch_weight_history,
    get_latest_feedback_digest,
    get_plan,
    get_streetview_cache,
    get_validated_plan,
    increment_likes,
    insert_feedback,
    insert_feedback_digest,
    insert_incident_snapshot,
    insert_plan,
    insert_race,
    insert_traces,
    insert_traffic_snapshot,
    insert_validated_plan,
    insert_weight_snapshot,
    list_feedback,
    list_feedback_digests,
    list_races,
    list_validated_plans,
    materialise_auto_pinned,
    set_streetview_cache,
    upsert_plan_atom,
)


# ---------- Helpers ----------


async def _seed_race_and_plan(
    *,
    race_id: str | None = None,
    plan_id: str | None = None,
    agent_name: str = "opus",
    hard_pass: bool = True,
    rank: int | None = 1,
    country_iso3: str = "SGP",
) -> tuple[str, str]:
    """Insert a race + a single plan and return their ids."""
    race_id = race_id or str(uuid.uuid4())
    plan_id = plan_id or str(uuid.uuid4())
    await insert_race(
        race_id=race_id,
        user_query="lazy sunday",
        spec={"raw_query": "lazy sunday"},
        harness_version="v1",
        harness_weights={"flow": 0.5, "diversity": 0.2, "vibe": 0.3},
        status="completed",
        duration_seconds=42.0,
    )
    await insert_plan(
        plan_id=plan_id,
        race_id=race_id,
        plan={
            "agent_name": agent_name,
            "model": "claude-opus-4-7",
            "pois": [{"id": "poi-a", "name": "Bakery"}],
            "legs": [],
            "hard_pass": hard_pass,
            "rank": rank,
            "soft_scores": {"flow": 0.7, "diversity": 0.6, "vibe": 0.8},
            "total_score": 0.72,
        },
        country_iso3=country_iso3,
    )
    return race_id, plan_id


# ---------- Schema bootstrap ----------


@pytest.mark.asyncio
async def test_init_db_creates_eleven_tables(isolated_sqlite: str) -> None:
    """The schema script materialises every v1 + v2 table."""
    async with aiosqlite.connect(isolated_sqlite) as db:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {row[0] for row in rows}
    expected = {
        "races",
        "plans",
        "validated_plans",
        "traces",
        "plan_atoms",
        "weight_history",
        "feedback",
        "feedback_digest",
        "traffic_snapshots",
        "incident_snapshots",
        "streetview_cache",
    }
    assert expected.issubset(names)
    assert len(expected) == 11


# ---------- Races + plans ----------


@pytest.mark.asyncio
async def test_insert_and_get_plan_roundtrip(isolated_sqlite: str) -> None:
    """A persisted plan is decoded back into the same dict shape."""
    _, plan_id = await _seed_race_and_plan()
    plan_row = await get_plan(plan_id)
    assert plan_row is not None
    assert plan_row["plan"]["agent_name"] == "opus"
    assert plan_row["soft_scores"]["vibe"] == 0.8


@pytest.mark.asyncio
async def test_get_plan_returns_none_for_unknown(isolated_sqlite: str) -> None:
    """Unknown plan id → ``None`` rather than an exception."""
    assert await get_plan("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_races_with_top_plan(isolated_sqlite: str) -> None:
    """``list_races`` attaches the rank-1 plan when one exists."""
    await _seed_race_and_plan(agent_name="gpt")
    rows = await list_races()
    assert len(rows) == 1
    assert rows[0]["top_plan"]["agent_name"] == "gpt"


@pytest.mark.asyncio
async def test_list_races_returns_none_top_plan_when_all_fail(
    isolated_sqlite: str,
) -> None:
    """A race with only failing plans surfaces ``top_plan=None``."""
    await _seed_race_and_plan(hard_pass=False, rank=None)
    rows = await list_races()
    assert rows[0]["top_plan"] is None


# ---------- Traces ----------


@pytest.mark.asyncio
async def test_insert_traces_raises_on_duplicate_id(isolated_sqlite: str) -> None:
    """Trace insert is intentionally non-idempotent; duplicates raise."""
    race_id, _ = await _seed_race_and_plan()
    trace = {
        "id": "trace-1",
        "race_id": race_id,
        "agent_name": "opus",
        "tool_name": "places_search",
        "input": {"query": "tiong bahru"},
        "output": {"places": []},
        "status": "ok",
        "error": None,
        "latency_ms": 120.0,
    }
    await insert_traces([trace])
    with pytest.raises(aiosqlite.IntegrityError):
        await insert_traces([trace])


@pytest.mark.asyncio
async def test_fetch_traces_by_race_returns_oldest_first(
    isolated_sqlite: str,
) -> None:
    """Traces come back in insertion order (``ORDER BY created_at``)."""
    race_id, _ = await _seed_race_and_plan()
    await insert_traces(
        [
            {
                "id": f"t-{i}",
                "race_id": race_id,
                "agent_name": "opus",
                "tool_name": "route",
                "input": {"i": i},
                "output": None,
                "status": "ok",
                "error": None,
                "latency_ms": 30.0,
            }
            for i in range(3)
        ]
    )
    rows = await fetch_traces_by_race(race_id)
    assert len(rows) == 3
    assert [r["id"] for r in rows] == ["t-0", "t-1", "t-2"]


# ---------- Validated plans + likes ----------


@pytest.mark.asyncio
async def test_insert_and_get_validated_plan(isolated_sqlite: str) -> None:
    """A validated plan round-trips with decoded ``hitl_rating``."""
    _, plan_id = await _seed_race_and_plan()
    validated_id = str(uuid.uuid4())
    await insert_validated_plan(
        validated_id=validated_id,
        plan_id=plan_id,
        country_iso3="SGP",
        anchor_lat=1.30,
        anchor_lng=103.80,
        hitl_rating={"novelty": 5, "efficiency": 4, "vibe": 5, "comment": "love it"},
        pois_override=None,
    )
    row = await get_validated_plan(validated_id)
    assert row is not None
    assert row["hitl_rating"]["novelty"] == 5
    assert row["pois_override"] is None


@pytest.mark.asyncio
async def test_increment_likes_returns_zero_for_missing_row(
    isolated_sqlite: str,
) -> None:
    """``increment_likes`` on a missing row returns 0, never raises."""
    assert await increment_likes("does-not-exist") == 0


@pytest.mark.asyncio
async def test_likes_and_feedback_roundtrip(isolated_sqlite: str) -> None:
    """Likes accumulate; feedback rows persist and list back."""
    _, plan_id = await _seed_race_and_plan()
    validated_id = str(uuid.uuid4())
    await insert_validated_plan(
        validated_id=validated_id,
        plan_id=plan_id,
        country_iso3="SGP",
        anchor_lat=None,
        anchor_lng=None,
        hitl_rating={"novelty": 4, "efficiency": 4, "vibe": 4},
        pois_override=None,
    )
    assert await increment_likes(validated_id) == 1
    assert await increment_likes(validated_id) == 2
    fid = await insert_feedback(
        validated_id=validated_id,
        plan_id=plan_id,
        question="What did you enjoy?",
        response="The bakery!",
        sentiment="positive",
    )
    assert fid > 0
    rows = await list_feedback(plan_id=plan_id)
    assert len(rows) == 1
    assert rows[0]["response"] == "The bakery!"


# ---------- Auto-pinned synthesis ----------


@pytest.mark.asyncio
async def test_list_validated_plans_synthesises_auto_pinned(
    isolated_sqlite: str,
) -> None:
    """A rank-1 unpinned plan surfaces as ``auto-<plan_id>`` with a synthetic rating."""
    _, plan_id = await _seed_race_and_plan()
    rows = await list_validated_plans(country_iso3="SGP", include_auto=True)
    assert len(rows) == 1
    assert rows[0]["id"] == f"auto-{plan_id}"
    assert rows[0]["auto_pinned"] is True
    # Synthetic rating derives 1 + round(4 * dimension), so vibe=0.8 → 4.
    assert rows[0]["hitl_rating"]["vibe"] == 4


@pytest.mark.asyncio
async def test_list_validated_plans_excludes_auto_when_disabled(
    isolated_sqlite: str,
) -> None:
    """``include_auto=False`` returns only explicit pins."""
    await _seed_race_and_plan()
    rows = await list_validated_plans(country_iso3="SGP", include_auto=False)
    assert rows == []


@pytest.mark.asyncio
async def test_materialise_auto_pinned_creates_real_row(isolated_sqlite: str) -> None:
    """The auto id materialises into a real validated_plans row exactly once."""
    _, plan_id = await _seed_race_and_plan()
    new_id = await materialise_auto_pinned(f"auto-{plan_id}")
    assert new_id is not None
    second_id = await materialise_auto_pinned(f"auto-{plan_id}")
    assert second_id == new_id


@pytest.mark.asyncio
async def test_materialise_auto_pinned_returns_none_for_missing_plan(
    isolated_sqlite: str,
) -> None:
    """Materialising against a non-existent plan returns ``None``."""
    assert await materialise_auto_pinned("auto-nope") is None


# ---------- Feedback + digest ----------


@pytest.mark.asyncio
async def test_count_feedback_matches_inserts(isolated_sqlite: str) -> None:
    """``count_feedback`` reflects every insert."""
    _, plan_id = await _seed_race_and_plan()
    assert await count_feedback() == 0
    for i in range(3):
        await insert_feedback(
            validated_id=None,
            plan_id=plan_id,
            question="q",
            response=f"r{i}",
            sentiment="positive",
        )
    assert await count_feedback() == 3


@pytest.mark.asyncio
async def test_feedback_digest_storage_roundtrip(isolated_sqlite: str) -> None:
    """Latest digest returns the newest row; tags decode to a list."""
    await insert_feedback_digest(
        scope="global",
        summary="early cafés trending",
        tags=[{"tag": "café", "count": 4}],
        source_count=4,
        model="claude-haiku-4-5-20251001",
    )
    await insert_feedback_digest(
        scope="global",
        summary="leafy walks dominate",
        tags=[{"tag": "leafy", "count": 6}],
        source_count=6,
        model="claude-haiku-4-5-20251001",
    )
    latest = await get_latest_feedback_digest("global")
    assert latest is not None
    assert latest["summary"] == "leafy walks dominate"
    history = await list_feedback_digests("global", limit=10)
    assert len(history) == 2
    assert history[0]["tags"][0]["tag"] == "leafy"


# ---------- Weights ----------


@pytest.mark.asyncio
async def test_weight_history_roundtrip(isolated_sqlite: str) -> None:
    """Weight snapshots come back oldest-first via the subquery."""
    for w in [
        {"flow": 0.5, "diversity": 0.2, "vibe": 0.3},
        {"flow": 0.4, "diversity": 0.3, "vibe": 0.3},
    ]:
        await insert_weight_snapshot(w)
    rows = await fetch_weight_history(limit=10)
    assert len(rows) == 2
    assert rows[0]["flow"] == 0.5
    assert rows[1]["flow"] == 0.4


# ---------- Plan atoms (swarm overlay) ----------


@pytest.mark.asyncio
async def test_upsert_plan_atom_running_mean(isolated_sqlite: str) -> None:
    """A second upsert at the same poi_id averages the score in."""
    poi = {"id": "poi-x", "name": "Bakery"}
    await upsert_plan_atom(poi_id="poi-x", country_iso3="SGP", poi=poi, score=0.6)
    await upsert_plan_atom(poi_id="poi-x", country_iso3="SGP", poi=poi, score=0.8)
    candidates = await fetch_hot_candidates("SGP")
    assert len(candidates) == 1
    assert candidates[0]["id"] == "poi-x"


# ---------- v2: traffic snapshots ----------


@pytest.mark.asyncio
async def test_insert_traffic_snapshot_and_read(isolated_sqlite: str) -> None:
    """Traffic snapshot row is persisted for the bbox key."""
    snapshot_id = str(uuid.uuid4())
    await insert_traffic_snapshot(
        snapshot_id=snapshot_id,
        bbox=(1.30, 103.80, 1.31, 103.81),
        payload={"speed_band": [1, 2, 3]},
    )
    async with aiosqlite.connect(isolated_sqlite) as db:
        rows = await db.execute_fetchall(
            "SELECT id, payload FROM traffic_snapshots WHERE id = ?",
            (snapshot_id,),
        )
    assert len(rows) == 1
    assert "speed_band" in rows[0][1]


@pytest.mark.asyncio
async def test_insert_incident_snapshot_persists_payload(
    isolated_sqlite: str,
) -> None:
    """Incident snapshot row is persisted with its centre/radius hash."""
    snapshot_id = str(uuid.uuid4())
    await insert_incident_snapshot(
        snapshot_id=snapshot_id,
        centre=(1.30, 103.80),
        radius_m=500.0,
        payload={"incidents": [{"type": "accident"}]},
    )
    async with aiosqlite.connect(isolated_sqlite) as db:
        rows = await db.execute_fetchall(
            "SELECT id, payload FROM incident_snapshots WHERE id = ?",
            (snapshot_id,),
        )
    assert len(rows) == 1
    assert "accident" in rows[0][1]


# ---------- v2: streetview cache ----------


@pytest.mark.asyncio
async def test_streetview_cache_hit_and_miss(isolated_sqlite: str) -> None:
    """A miss returns ``None``; a write then read returns the same payload."""
    miss = await get_streetview_cache(1.300, 103.800, "2026-04-24")
    assert miss is None

    photos = [
        {
            "url": "https://photo1.jpg",
            "thumb_url": "https://photo1-thumb.jpg",
            "heading": 90.0,
            "projection": "PLANE",
        }
    ]
    await set_streetview_cache(1.300, 103.800, "2026-04-24", photos)
    hit = await get_streetview_cache(1.300, 103.800, "2026-04-24")
    assert hit == photos


@pytest.mark.asyncio
async def test_streetview_cache_overwrite_updates_payload(
    isolated_sqlite: str,
) -> None:
    """A second write to the same key replaces the first."""
    await set_streetview_cache(
        1.300, 103.800, "2026-04-24", [{"url": "https://old"}]
    )
    await set_streetview_cache(
        1.300, 103.800, "2026-04-24", [{"url": "https://new"}]
    )
    hit = await get_streetview_cache(1.300, 103.800, "2026-04-24")
    assert hit == [{"url": "https://new"}]


# ---------- v2: live-feed counts ----------


@pytest.mark.asyncio
async def test_fetch_live_feed_counts_categorises_tool_names(
    isolated_sqlite: str,
) -> None:
    """Tool-name → category mapping aggregates counts per bucket."""
    race_id, _ = await _seed_race_and_plan()
    samples = [
        ("places_search", "search"),
        ("places_search", "search"),
        ("nearby_search", "search"),
        ("route", "routing"),
        ("route_matrix", "routing"),
        ("get_traffic", "traffic"),
        ("get_incidents", "incidents"),
        ("get_street_view", "streetview"),
        ("emit_thought", "other"),
    ]
    await insert_traces(
        [
            {
                "id": str(uuid.uuid4()),
                "race_id": race_id,
                "agent_name": "opus",
                "tool_name": tool,
                "input": {},
                "output": None,
                "status": "ok",
                "error": None,
                "latency_ms": 5.0,
            }
            for tool, _ in samples
        ]
    )
    counts = await fetch_live_feed_counts(window_seconds=300)
    assert counts["total_calls"] == 9
    assert counts["by_category"]["search"] == 3
    assert counts["by_category"]["routing"] == 2
    assert counts["by_category"]["traffic"] == 1
    assert counts["by_category"]["incidents"] == 1
    assert counts["by_category"]["streetview"] == 1
    assert counts["by_category"]["other"] == 1


@pytest.mark.asyncio
async def test_fetch_live_feed_counts_returns_zeros_when_empty(
    isolated_sqlite: str,
) -> None:
    """No traces in window → every bucket is zero, total is zero."""
    counts = await fetch_live_feed_counts(window_seconds=60)
    assert counts["total_calls"] == 0
    assert counts["by_category"] == {
        "search": 0,
        "routing": 0,
        "traffic": 0,
        "incidents": 0,
        "streetview": 0,
        "other": 0,
    }
    assert counts["window_seconds"] == 60


# ---------- Concurrency sanity ----------


@pytest.mark.asyncio
async def test_concurrent_writes_do_not_corrupt(isolated_sqlite: str) -> None:
    """A handful of concurrent inserts all land — WAL plus per-call connect is safe."""
    _, plan_id = await _seed_race_and_plan()
    await asyncio.gather(
        *(
            insert_feedback(
                validated_id=None,
                plan_id=plan_id,
                question="q",
                response=f"r{i}",
                sentiment="positive",
            )
            for i in range(5)
        )
    )
    assert await count_feedback() == 5
