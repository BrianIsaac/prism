"""Endpoint + tool coverage for the Prism backend (v2).

Lifts the non-storage rows the example-code suite carried forward plus the
v2 rows Phase 7 is on the hook for: rate-limit, race cache, SSE polling
fallback, GrabMaps proxy, live-feed, and the live-tool stubs.

The ``_isolated_db`` fixture redirects SQLite to a per-test tempfile and
the ``client`` fixture swaps in a fresh ``TestClient`` — lifespan fires so
``init_db()`` runs against the isolated path. Both fixtures clear the
module-level rate-limit and race caches so tests cannot bleed into one
another.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Redirect SQLite to a per-test tempfile and reset live caches.

    Patches both the env var and the already-bound module globals because
    ``from app.config import SQLITE_PATH`` captures the name at import time.
    Also clears the rate-limit dict and the race-cache dict so per-test
    state does not bleed between cases.
    """
    import app.config
    import app.storage

    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setattr(app.config, "SQLITE_PATH", db_path)
    monkeypatch.setattr(app.storage, "_DEFAULT_SQLITE_PATH", db_path, raising=False)

    from app import main as main_mod

    monkeypatch.setattr(
        main_mod, "_rate_tracker", defaultdict(list), raising=True
    )
    monkeypatch.setattr(main_mod, "_race_cache", {}, raising=True)
    monkeypatch.setattr(main_mod, "_race_events", defaultdict(list), raising=True)
    monkeypatch.setattr(
        main_mod, "_race_subscribers", defaultdict(list), raising=True
    )
    monkeypatch.setattr(main_mod, "_race_terminal", {}, raising=True)
    yield


@pytest.fixture
def client():
    """FastAPI TestClient with lifespan triggered so ``init_db`` runs."""
    from app.main import app

    with TestClient(app) as c:
        yield c


# ----------------------------------------------------------------------------
# Health + basic shape
# ----------------------------------------------------------------------------


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["harness_version"]


def test_validated_list_endpoint_empty(client):
    r = client.get("/validated")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["validated_plans"] == []


def test_races_endpoint_empty(client):
    r = client.get("/races")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["races"] == []


def test_feedback_list_endpoint_empty(client):
    r = client.get("/feedback")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["feedback"] == []


def test_admin_weights_shape(client):
    r = client.get("/admin/weights")
    assert r.status_code == 200
    body = r.json()
    assert set(body["frozen_defaults"].keys()) == {"flow", "diversity", "vibe"}
    assert set(body["runtime"].keys()) == {"flow", "diversity", "vibe"}


def test_admin_bug_report_shape(client):
    r = client.get("/admin/bug-report")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 0
    assert body["failed_calls"] == 0
    assert "markdown" in body


def test_admin_feedback_digest_empty(client):
    r = client.get("/admin/feedback-digest")
    assert r.status_code == 200
    body = r.json()
    assert body["digest"] is None
    assert body["history"] == []
    assert body["raw_tail"] == []


def test_admin_feedback_digest_rebuild_empty_corpus(client):
    r = client.post("/admin/feedback-digest/rebuild")
    assert r.status_code == 400


def test_admin_live_feed_shape(client):
    r = client.get("/admin/live-feed")
    assert r.status_code == 200
    body = r.json()
    assert set(body["by_category"].keys()) == {
        "search",
        "routing",
        "traffic",
        "incidents",
        "streetview",
        "other",
    }
    assert body["total_calls"] == 0


def test_cors_header_for_localhost_3000(client):
    r = client.options(
        "/validated",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ----------------------------------------------------------------------------
# Request-validation
# ----------------------------------------------------------------------------


def test_race_query_length_bounded(client):
    r = client.post("/race", json={"query": ""})
    assert r.status_code == 422


def test_race_request_rejects_unknown_top_level_keys(client):
    r = client.post(
        "/race", json={"query": "Geylang half-day", "quick_start": "Raffles"}
    )
    assert r.status_code == 422


def test_spec_override_rejects_unknown_keys(client):
    r = client.post(
        "/race",
        json={"query": "Geylang", "spec_override": {"country_iso3": "USA"}},
    )
    assert r.status_code == 422


def test_spec_override_rejects_bad_types(client):
    r = client.post(
        "/race",
        json={
            "query": "Geylang",
            "spec_override": {"max_duration_minutes": "four hours"},
        },
    )
    assert r.status_code == 422


def test_spec_override_rejects_out_of_range_duration(client):
    r = client.post(
        "/race",
        json={
            "query": "Geylang",
            "spec_override": {"max_duration_minutes": 999_999},
        },
    )
    assert r.status_code == 422


def test_spec_override_rejects_out_of_range_party_size(client):
    r = client.post(
        "/race", json={"query": "Geylang", "spec_override": {"party_size": 999}}
    )
    assert r.status_code == 422


def test_spec_override_rejects_invalid_transport(client):
    r = client.post(
        "/race",
        json={
            "query": "Geylang",
            "spec_override": {"transport_mode": "teleport"},
        },
    )
    assert r.status_code == 422


def test_spec_override_accepts_grabmaps_profile_strings(client):
    """New alignment: the five GrabMaps profile strings must pass validation."""
    for profile in ("driving", "motorcycle", "tricycle", "cycling", "walking"):
        r = client.post(
            "/race",
            json={
                "query": "Chinatown",
                "spec_override": {"transport_mode": profile},
            },
        )
        assert r.status_code != 422, f"profile {profile!r} rejected"


def test_feedback_returns_404_for_missing_plan(client):
    r = client.post(
        "/feedback", json={"plan_id": "nonexistent-plan", "response": "test"}
    )
    assert r.status_code == 404


def test_feedback_rejects_oversize_response(client):
    r = client.post("/feedback", json={"plan_id": "any", "response": "x" * 3000})
    assert r.status_code == 422


def test_feedback_rejects_empty_response(client):
    r = client.post("/feedback", json={"plan_id": "any", "response": ""})
    assert r.status_code == 422


def test_like_returns_404_for_missing_validated_plan(client):
    r = client.post("/validated/nonexistent-id/like")
    assert r.status_code == 404


def test_trace_endpoint_empty_for_unknown_race(client):
    r = client.get("/trace/does-not-exist")
    assert r.status_code == 200
    body = r.json()
    assert body["race_id"] == "does-not-exist"
    assert body["traces"] == []


# ----------------------------------------------------------------------------
# Race + SSE (no LLM: the /race ignition task is fire-and-forget; we only
# assert on the handshake, the cache key, and the polling fallback slice).
# ----------------------------------------------------------------------------


def test_race_handshake_returns_stream_url(client):
    r = client.post("/race", json={"query": "Geylang hawker crawl 4h halal"})
    assert r.status_code == 200
    body = r.json()
    assert "race_id" in body
    assert body["stream_url"] == f"/race/{body['race_id']}/stream"


def test_race_cache_returns_same_race_id(client):
    """Identical (query, spec_override) within TTL reuses the first race_id.

    Asserts the cache hit returns in under 100 ms (the acceptance gate), so
    the second submission cannot be spawning another background race.
    """
    payload = {"query": "Chinatown heritage walk 3h chill photogenic"}
    r1 = client.post("/race", json=payload)
    assert r1.status_code == 200
    first_id = r1.json()["race_id"]

    start = time.perf_counter()
    r2 = client.post("/race", json=payload)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert r2.status_code == 200
    assert r2.json()["race_id"] == first_id
    assert elapsed_ms < 100, f"cached race took {elapsed_ms:.1f}ms (>100)"


def test_race_cache_keys_on_spec_override(client):
    """Two requests with the same text but different overrides must differ."""
    r1 = client.post(
        "/race",
        json={"query": "Sentosa", "spec_override": {"max_duration_minutes": 240}},
    )
    r2 = client.post(
        "/race",
        json={"query": "Sentosa", "spec_override": {"max_duration_minutes": 120}},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["race_id"] != r2.json()["race_id"]


def test_race_events_polling_starts_empty(client):
    r1 = client.post("/race", json={"query": "Orchard window shopping 2h"})
    race_id = r1.json()["race_id"]
    r2 = client.get(f"/race/{race_id}/events?since=0")
    assert r2.status_code == 200
    body = r2.json()
    assert body["race_id"] == race_id
    assert body["since"] == 0
    assert isinstance(body["events"], list)


# ----------------------------------------------------------------------------
# Rate limit
# ----------------------------------------------------------------------------


def test_rate_limit_race_fires_at_eleventh_request(client):
    """10 req/60s on /race — the 11th must surface a 429 with Retry-After."""
    payload = {"query": "ratelimit probe one"}
    for _ in range(10):
        r = client.post("/race", json=payload)
        # Body hashes to the same cache key, so requests 2-10 short-circuit
        # via the cache while still counting against the rate bucket.
        assert r.status_code == 200
    r11 = client.post("/race", json=payload)
    assert r11.status_code == 429
    assert "Retry-After" in r11.headers


def test_rate_limit_default_bucket_is_permissive(client):
    """/health is whitelisted; /validated uses the permissive 200/60s bucket."""
    for _ in range(20):
        r = client.get("/validated")
        assert r.status_code == 200


# ----------------------------------------------------------------------------
# GrabMaps proxy
# ----------------------------------------------------------------------------


def test_grabmaps_style_proxy_returns_upstream_json(client, monkeypatch):
    """Proxy forwards upstream body; browser never sees the Bearer token."""
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "GRABMAPS_API_KEY", "test-key", raising=True)
    upstream_body = {
        "version": 8,
        "sources": {},
        "layers": [],
    }
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{main_mod.GRABMAPS_BASE_URL}/api/style.json").mock(
            return_value=Response(200, json=upstream_body)
        )
        r = client.get("/grabmaps-proxy/style.json?theme=satellite")
        assert r.status_code == 200
        assert r.json()["version"] == 8
        # Request must carry the server-side Bearer header.
        assert route.called
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.params["theme"] == "satellite"


def test_grabmaps_style_proxy_503_without_key(client, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "GRABMAPS_API_KEY", None, raising=True)
    r = client.get("/grabmaps-proxy/style.json")
    assert r.status_code == 503


def test_grabmaps_traffic_tile_proxy_forwards_bytes(client, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "GRABMAPS_API_KEY", "test-key", raising=True)
    tile_body = b'{"type":"FeatureCollection","features":[]}'
    with respx.mock(assert_all_called=False) as router:
        route = router.get(
            f"{main_mod.GRABMAPS_BASE_URL}/api/v1/traffic-tiles/12/3280/2054.json"
        ).mock(
            return_value=Response(
                200, content=tile_body, headers={"Content-Type": "application/json"}
            )
        )
        r = client.get("/grabmaps-proxy/traffic-tile/12/3280/2054.json")
        assert r.status_code == 200
        assert r.content == tile_body
        assert route.called
        assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


def test_grabmaps_incidents_tile_proxy_forwards_bytes(client, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "GRABMAPS_API_KEY", "test-key", raising=True)
    tile_body = b'{"type":"FeatureCollection","features":[]}'
    with respx.mock(assert_all_called=False) as router:
        route = router.get(
            f"{main_mod.GRABMAPS_BASE_URL}/api/v1/traffic/incidents/tile/12/3280/2054"
        ).mock(
            return_value=Response(
                200, content=tile_body, headers={"Content-Type": "application/json"}
            )
        )
        r = client.get("/grabmaps-proxy/incidents-tile/12/3280/2054")
        assert r.status_code == 200
        assert r.content == tile_body
        assert route.called


# ----------------------------------------------------------------------------
# Alternatives + tool wiring
# ----------------------------------------------------------------------------


def test_alternatives_endpoint_requires_category(client):
    r = client.get("/alternatives?near_lat=1.3&near_lng=103.8")
    assert r.status_code == 422


def test_alternatives_endpoint_shape_when_upstream_empty(client, monkeypatch):
    """With GRABMAPS_API_KEY unset the upstream call raises — the handler
    returns an empty alternatives list rather than 500ing the frontend.
    """
    import app.tools.grabmaps as gm

    monkeypatch.setattr(gm, "GRABMAPS_API_KEY", None, raising=True)
    r = client.get(
        "/alternatives",
        params={"category": "food", "near_lat": 1.3, "near_lng": 103.8, "limit": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["alternatives"] == []


# ----------------------------------------------------------------------------
# Live tools (HTTP layer, mocked)
# ----------------------------------------------------------------------------


async def test_get_traffic_calls_upstream_circle(monkeypatch):
    from app.tools import live

    monkeypatch.setattr(live, "GRABMAPS_API_KEY", "test-key", raising=True)
    live._traffic_cache.clear()
    expected = {"segments": [{"segmentId": "a", "congestion": "heavy", "avgSpeedKph": 12}]}
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{live.GRABMAPS_BASE_URL}/api/v1/traffic/real-time/circle").mock(
            return_value=Response(200, json=expected)
        )
        body = await live.get_traffic(lat=1.3521, lng=103.8198, radius_m=500)
        assert body == expected


async def test_get_incidents_calls_upstream_circle(monkeypatch):
    from app.tools import live

    monkeypatch.setattr(live, "GRABMAPS_API_KEY", "test-key", raising=True)
    live._incident_cache.clear()
    expected = {"incidents": [{"type": "accident", "severity": 3}]}
    with respx.mock(assert_all_called=False) as router:
        router.get(
            f"{live.GRABMAPS_BASE_URL}/api/v1/traffic/incidents/circle"
        ).mock(return_value=Response(200, json=expected))
        body = await live.get_incidents(lat=1.3521, lng=103.8198, radius_m=1000)
        assert body == expected


async def test_get_street_view_uses_cache_after_first_fetch(monkeypatch, tmp_path):
    """Second call at the same tile serves from SQLite without hitting upstream."""
    import os

    import app.config
    import app.storage
    from app.tools import live

    db_path = str(tmp_path / "streetview.db")
    os.environ["SQLITE_PATH"] = db_path
    monkeypatch.setattr(app.config, "SQLITE_PATH", db_path)
    monkeypatch.setattr(app.storage, "_DEFAULT_SQLITE_PATH", db_path, raising=False)
    await app.storage.init_db()

    monkeypatch.setattr(live, "GRABMAPS_API_KEY", "test-key", raising=True)
    photo_payload = {
        "photos": [
            {
                "fileUrl": "https://osc.example/photo.jpg",
                "thumbUrl": "https://osc.example/thumb.jpg",
                "heading": 180,
                "projection": "PLANE",
            }
        ]
    }
    with respx.mock(assert_all_called=False) as router:
        upstream = router.get(
            f"{live.GRABMAPS_BASE_URL}/api/v1/openstreetcam-api/2.0/photo/"
        ).mock(return_value=Response(200, json=photo_payload))
        first = await live.get_street_view(lat=1.2821, lng=103.8583, radius_m=100, limit=4)
        second = await live.get_street_view(lat=1.2821, lng=103.8583, radius_m=100, limit=4)
        assert first["cached"] is False
        assert second["cached"] is True
        assert upstream.call_count == 1


# ----------------------------------------------------------------------------
# Storage round-trip (non-duplicate of test_storage.py)
# ----------------------------------------------------------------------------


async def test_rating_drifts_runtime_weights(client):
    """A rating on a real plan drifts weights and appends a snapshot row."""
    from app.storage import (
        fetch_weight_history,
        init_db,
        insert_plan,
        insert_race,
    )

    await init_db()
    race_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    await insert_race(
        race_id=race_id,
        user_query="q",
        spec={"country_iso3": "SGP"},
        harness_version="v1",
        harness_weights={"flow": 0.5, "diversity": 0.2, "vibe": 0.3},
        status="complete",
        duration_seconds=1.0,
    )
    await insert_plan(
        plan_id=plan_id,
        race_id=race_id,
        plan={
            "agent_name": "opus",
            "model": "claude-opus-4-7",
            "pois": [{"id": "p1", "name": "A", "lat": 1.3, "lng": 103.8}],
            "legs": [],
            "hard_pass": True,
            "total_score": 0.6,
            "rank": 1,
        },
        country_iso3="SGP",
    )
    before = await fetch_weight_history(limit=5)
    r = client.post(
        "/rating",
        json={"plan_id": plan_id, "novelty": 5, "efficiency": 4, "vibe": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body["weights"].keys()) == {"flow", "diversity", "vibe"}
    after = await fetch_weight_history(limit=5)
    assert len(after) >= len(before) + 1
