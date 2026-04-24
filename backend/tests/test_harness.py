"""Tests for the frozen scoring harness (v2).

The harness is the load-bearing piece: a regression here invalidates every
prior race ranking. Lift the v1 thirty-four-test pattern verbatim where the
v2 contract is unchanged, and add coverage for the three v2 deltas:

    - rule #2 reads ``leg.route.fee.amount``
    - rule #4 parses GrabMaps' JSON-string ``opening_hours``
    - ``score_vibe`` accepts ``streetview_urls_by_poi`` and forwards it
      to the photo-grounded judge
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from app.harness import (
    HARNESS_VERSION,
    HARNESS_WEIGHTS,
    check_hard_rules,
    score_and_rank,
    score_diversity,
    score_flow,
    score_plan,
    score_vibe,
)
from app.harness import _window_within_hours  # type: ignore[attr-defined]


# ---------- Builders ----------


def _spec(**overrides: Any) -> dict[str, Any]:
    """Minimal spec dict — overrides shadow individual fields."""
    base: dict[str, Any] = {
        "raw_query": "lazy Sunday in Tiong Bahru",
        "max_duration_minutes": 240,
        "max_budget_sgd": 50.0,
        "transport_mode": "walk",
        "dietary": None,
        "mood_tags": ["lazy", "leafy"],
        "start_anchor": None,
        "end_anchor": None,
        "party_size": 1,
        "accessible": False,
    }
    base.update(overrides)
    return base


def _plan(**overrides: Any) -> dict[str, Any]:
    """Canonical 3-POI / 2-leg plan that passes every default rule."""
    base: dict[str, Any] = {
        "agent_name": "opus",
        "model": "claude-opus-4-7",
        "pois": [
            {
                "id": "poi-a",
                "name": "Bakery",
                "category": "food",
                "subcategory": "bakery",
                "lat": 1.30,
                "lng": 103.80,
                "is_food": True,
                "dietary_tags": ["halal"],
                "opening_hours": [],
                "dwell_minutes": 30,
                "avg_cost_sgd": 8.0,
            },
            {
                "id": "poi-b",
                "name": "Bookshop",
                "category": "shopping",
                "subcategory": "books",
                "lat": 1.305,
                "lng": 103.802,
                "is_food": False,
                "dietary_tags": [],
                "opening_hours": [],
                "dwell_minutes": 45,
                "avg_cost_sgd": 0.0,
            },
            {
                "id": "poi-c",
                "name": "Coffee",
                "category": "food",
                "subcategory": "cafe",
                "lat": 1.31,
                "lng": 103.805,
                "is_food": True,
                "dietary_tags": ["halal"],
                "opening_hours": [],
                "dwell_minutes": 30,
                "avg_cost_sgd": 6.0,
            },
        ],
        "legs": [
            {
                "from": "poi-a",
                "to": "poi-b",
                "mode": "walk",
                "duration_minutes": 12.0,
                "distance_metres": 700,
                "unreachable": False,
            },
            {
                "from": "poi-b",
                "to": "poi-c",
                "mode": "walk",
                "duration_minutes": 10.0,
                "distance_metres": 600,
                "unreachable": False,
            },
        ],
        "total_minutes": 127.0,
        "total_cost_sgd": 14.0,
        "narrative": "A leafy walk with bakery, books, and a coffee stop.",
    }
    base.update(overrides)
    return base


# ---------- Constants ----------


def test_harness_version_is_v1() -> None:
    """v2 the system; v1 the harness contract — the version string proves it."""
    assert HARNESS_VERSION == "v1"


def test_harness_weights_sum_to_one() -> None:
    """Weights should always sum to 1.0 so the aggregate stays in [0, 1]."""
    assert math.isclose(sum(HARNESS_WEIGHTS.values()), 1.0)


def test_harness_weights_have_three_dimensions() -> None:
    """Three soft dimensions: flow, diversity, vibe — no fewer, no more."""
    assert set(HARNESS_WEIGHTS) == {"flow", "diversity", "vibe"}


# ---------- Hard rules ----------


def test_valid_plan_passes() -> None:
    """The canonical plan + spec passes every hard rule."""
    result = check_hard_rules(_plan(), _spec())
    assert result.passed
    assert result.failures == []


def test_empty_pois_fails() -> None:
    """A plan with no POIs cannot pass — guards against vacuous budget wins."""
    result = check_hard_rules(_plan(pois=[]), _spec())
    assert not result.passed
    assert any("no POIs" in f for f in result.failures)


def test_error_plan_fails() -> None:
    """An ``error`` field disqualifies the plan immediately."""
    result = check_hard_rules(_plan(error="llm crashed"), _spec())
    assert not result.passed
    assert any("agent error" in f for f in result.failures)


def test_time_budget_exceeded_fails() -> None:
    """Travel + dwell over the budget triggers a failure."""
    plan = _plan()
    plan["legs"][0]["duration_minutes"] = 400
    result = check_hard_rules(plan, _spec(max_duration_minutes=120))
    assert not result.passed
    assert any("time budget" in f for f in result.failures)


def test_time_budget_at_limit_passes() -> None:
    """A plan exactly at the budget passes (≤, not <)."""
    plan = _plan()
    # Set legs to 0 so dwell totals exactly the cap.
    plan["legs"] = [
        {
            "from": "poi-a",
            "to": "poi-b",
            "mode": "walk",
            "duration_minutes": 0,
            "distance_metres": 0,
            "unreachable": False,
        },
        {
            "from": "poi-b",
            "to": "poi-c",
            "mode": "walk",
            "duration_minutes": 0,
            "distance_metres": 0,
            "unreachable": False,
        },
    ]
    for poi in plan["pois"]:
        poi["dwell_minutes"] = 30
    spec = _spec(max_duration_minutes=90)
    assert check_hard_rules(plan, spec).passed


def test_money_budget_exceeded_fails_with_per_poi_cost() -> None:
    """Per-POI ``avg_cost_sgd`` accumulates to fail the money rule."""
    plan = _plan()
    for poi in plan["pois"]:
        poi["avg_cost_sgd"] = 100.0
    result = check_hard_rules(plan, _spec(max_budget_sgd=50))
    assert not result.passed
    assert any("money budget" in f for f in result.failures)


def test_money_budget_route_fee_amount_exceeds_limit() -> None:
    """A leg with ``route.fee.amount`` over the budget fails the money rule."""
    plan = _plan()
    for poi in plan["pois"]:
        poi["avg_cost_sgd"] = 0.0
    plan["legs"][0]["route"] = {"fee": {"amount": 60.0, "currency": "SGD"}}
    result = check_hard_rules(plan, _spec(max_budget_sgd=50))
    assert not result.passed
    assert any("money budget" in f for f in result.failures)


def test_money_budget_zero_route_fee_passes() -> None:
    """``route.fee.amount`` of 0 is the SG default outside ERP zones — no fail."""
    plan = _plan()
    for poi in plan["pois"]:
        poi["avg_cost_sgd"] = 0.0
    for leg in plan["legs"]:
        leg["route"] = {"fee": {"amount": 0, "currency": ""}}
    assert check_hard_rules(plan, _spec(max_budget_sgd=50)).passed


def test_no_dietary_filter_passes() -> None:
    """``dietary=None`` skips the dietary check entirely."""
    assert check_hard_rules(_plan(), _spec(dietary=None)).passed


def test_matching_dietary_filter_passes() -> None:
    """Every food POI tagged with the dietary preference → pass."""
    assert check_hard_rules(_plan(), _spec(dietary="halal")).passed


def test_nonmatching_dietary_filter_fails() -> None:
    """A food POI without the dietary tag triggers a dietary failure."""
    result = check_hard_rules(_plan(), _spec(dietary="vegan"))
    assert not result.passed
    assert any("dietary" in f for f in result.failures)


def test_dietary_filter_skips_non_food_pois() -> None:
    """Non-food POIs (``is_food=False``) are exempt from the dietary check."""
    plan = _plan()
    for poi in plan["pois"]:
        poi["is_food"] = False
    assert check_hard_rules(plan, _spec(dietary="halal")).passed


def test_unresolved_poi_id_fails() -> None:
    """A null POI id is the placeholder marker → feasibility failure."""
    plan = _plan()
    plan["pois"][1]["id"] = None
    result = check_hard_rules(plan, _spec())
    assert not result.passed
    assert any("unresolved" in f for f in result.failures)


def test_unreachable_leg_fails() -> None:
    """``leg.unreachable=True`` is the explicit reachability failure."""
    plan = _plan()
    plan["legs"][0]["unreachable"] = True
    result = check_hard_rules(plan, _spec())
    assert not result.passed
    assert any("unreachable" in f for f in result.failures)


def test_start_anchor_far_fails() -> None:
    """First POI more than 200m from the start anchor fails the rule."""
    spec = _spec(start_anchor={"lat": 1.50, "lng": 104.00})
    result = check_hard_rules(_plan(), spec)
    assert not result.passed
    assert any("start anchor" in f for f in result.failures)


def test_start_anchor_close_passes() -> None:
    """First POI exactly at the start anchor passes the rule."""
    spec = _spec(start_anchor={"lat": 1.30, "lng": 103.80})
    assert check_hard_rules(_plan(), spec).passed


def test_end_anchor_far_fails() -> None:
    """Last POI more than 200m from the end anchor fails the rule."""
    spec = _spec(end_anchor={"lat": 1.50, "lng": 104.00})
    result = check_hard_rules(_plan(), spec)
    assert not result.passed
    assert any("end anchor" in f for f in result.failures)


# ---------- Opening hours rule (v2: JSON string) ----------


def test_opening_hours_empty_dict_string_is_lenient() -> None:
    """The default ``"{}"`` GrabMaps string is UNKNOWN → plan passes."""
    plan = _plan()
    plan["pois"][0]["opening_hours"] = "{}"
    plan["pois"][0]["visit_window"] = ("2026-04-26T18:00:00", "2026-04-26T19:00:00")
    assert check_hard_rules(plan, _spec()).passed


def test_opening_hours_unparseable_string_is_lenient() -> None:
    """Garbled JSON should not fail the plan — UNKNOWN means pass."""
    plan = _plan()
    plan["pois"][0]["opening_hours"] = "{not json"
    plan["pois"][0]["visit_window"] = ("2026-04-26T18:00:00", "2026-04-26T19:00:00")
    assert check_hard_rules(plan, _spec()).passed


def test_opening_hours_populated_string_inside_window_passes() -> None:
    """A populated JSON-string schedule covering the visit window passes."""
    schedule = json.dumps({"sunday": {"open": "09:00", "close": "17:00"}})
    plan = _plan()
    plan["pois"][0]["opening_hours"] = schedule
    plan["pois"][0]["visit_window"] = ("2026-04-26T10:00:00", "2026-04-26T11:00:00")
    assert check_hard_rules(plan, _spec()).passed


def test_opening_hours_populated_string_outside_window_fails() -> None:
    """A populated JSON-string schedule that closes before the window fails."""
    schedule = json.dumps({"sunday": {"open": "09:00", "close": "11:00"}})
    plan = _plan()
    plan["pois"][0]["opening_hours"] = schedule
    plan["pois"][0]["visit_window"] = ("2026-04-26T18:00:00", "2026-04-26T19:00:00")
    result = check_hard_rules(plan, _spec())
    assert not result.passed
    assert any("closed" in f for f in result.failures)


def test_opening_hours_list_form_still_supported() -> None:
    """Callers that pre-parse hours into ``list[dict]`` keep working."""
    plan = _plan()
    plan["pois"][0]["opening_hours"] = [
        {"day": "sunday", "open": "09:00", "close": "11:00"}
    ]
    plan["pois"][0]["visit_window"] = ("2026-04-26T18:00:00", "2026-04-26T19:00:00")
    result = check_hard_rules(plan, _spec())
    assert not result.passed


# ---------- Soft scores ----------


def test_score_flow_empty_returns_zero() -> None:
    """No legs → no flow signal → zero."""
    assert score_flow({"legs": [], "pois": []}) == 0.0


def test_score_flow_clean_route_in_valid_range() -> None:
    """A clean two-leg route scores in the canonical ``[0, 1]`` band."""
    score = score_flow(_plan())
    assert 0.0 <= score <= 1.0


def test_score_flow_penalises_revisits() -> None:
    """Looping back to a POI lowers flow vs a clean linear route."""
    clean = score_flow(_plan())
    loopy = _plan()
    loopy["legs"] = [
        {
            "from": "poi-a",
            "to": "poi-b",
            "mode": "walk",
            "duration_minutes": 10,
            "distance_metres": 500,
            "unreachable": False,
        },
        {
            "from": "poi-b",
            "to": "poi-a",
            "mode": "walk",
            "duration_minutes": 10,
            "distance_metres": 500,
            "unreachable": False,
        },
    ]
    assert score_flow(loopy) < clean


def test_score_flow_uses_route_duration_seconds() -> None:
    """When ``route.duration`` (seconds) is present it overrides ``duration_minutes``."""
    plan = _plan()
    for leg in plan["legs"]:
        leg["duration_minutes"] = 0
        leg["route"] = {"duration": 600}  # 10 minutes per leg
    score = score_flow(plan)
    assert 0.0 < score <= 1.0


def test_score_diversity_empty_returns_zero() -> None:
    """No POIs → no diversity signal → zero."""
    assert score_diversity({"pois": []}) == 0.0


def test_score_diversity_single_category_returns_zero() -> None:
    """A homogeneous plan has zero diversity by Shannon entropy."""
    plan = _plan()
    for poi in plan["pois"]:
        poi["category"] = "food"
        poi["subcategory"] = "cafe"
    assert score_diversity(plan) == 0.0


def test_score_diversity_two_even_categories_is_maximum() -> None:
    """Two perfectly even pairs → maximum entropy → 1.0."""
    plan = _plan()
    plan["pois"] = [
        {"id": "p1", "category": "food", "subcategory": "cafe"},
        {"id": "p2", "category": "shopping", "subcategory": "books"},
    ]
    assert math.isclose(score_diversity(plan), 1.0)


def test_score_diversity_even_beats_unbalanced() -> None:
    """Three distinct categories beats a 2:1 split."""
    even = _plan()
    even["pois"] = [
        {"id": "p1", "category": "food", "subcategory": "cafe"},
        {"id": "p2", "category": "shopping", "subcategory": "books"},
        {"id": "p3", "category": "park", "subcategory": "garden"},
    ]
    skewed = _plan()
    skewed["pois"] = [
        {"id": "p1", "category": "food", "subcategory": "cafe"},
        {"id": "p2", "category": "food", "subcategory": "cafe"},
        {"id": "p3", "category": "park", "subcategory": "garden"},
    ]
    assert score_diversity(even) > score_diversity(skewed)


# ---------- Window helper ----------


def test_window_unparseable_returns_none() -> None:
    """Non-ISO strings → ``None`` so the caller treats it as UNKNOWN."""
    assert (
        _window_within_hours(
            ("09:00", "11:00"),
            [{"day": "sunday", "open": "09:00", "close": "17:00"}],
        )
        is None
    )


def test_window_inside_returns_true() -> None:
    """ISO window inside the day's hours → True."""
    assert (
        _window_within_hours(
            ("2026-04-26T10:00:00", "2026-04-26T11:00:00"),
            [{"day": "sunday", "open": "09:00", "close": "17:00"}],
        )
        is True
    )


def test_window_outside_returns_false() -> None:
    """ISO window after closing → False."""
    assert (
        _window_within_hours(
            ("2026-04-26T18:00:00", "2026-04-26T19:00:00"),
            [{"day": "sunday", "open": "09:00", "close": "17:00"}],
        )
        is False
    )


def test_window_no_matching_weekday_returns_none() -> None:
    """When the hours don't cover the requested weekday → UNKNOWN."""
    assert (
        _window_within_hours(
            ("2026-04-27T10:00:00", "2026-04-27T11:00:00"),  # monday
            [{"day": "tuesday", "open": "09:00", "close": "17:00"}],
        )
        is None
    )


# ---------- score_vibe (v2: photo grounding) ----------


@pytest.mark.asyncio
async def test_score_vibe_forwards_streetview_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``score_vibe`` must pass ``streetview_urls_by_poi`` through to the judge."""
    captured: dict[str, Any] = {}

    async def fake_judge(
        plan: dict[str, Any],
        spec: Any,
        streetview_urls_by_poi: dict[str, list[str]] | None = None,
    ) -> float:
        captured["urls"] = streetview_urls_by_poi
        return 0.8

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    photos = {"poi-a": ["https://photo1", "https://photo2"]}
    score = await score_vibe(_plan(), _spec(), streetview_urls_by_poi=photos)
    assert math.isclose(score, 0.8)
    assert captured["urls"] == photos


@pytest.mark.asyncio
async def test_score_vibe_no_photos_falls_back_to_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``streetview_urls_by_poi=None`` falls through to the prose-only judge call."""
    captured: dict[str, Any] = {}

    async def fake_judge(
        plan: dict[str, Any],
        spec: Any,
        streetview_urls_by_poi: dict[str, list[str]] | None = None,
    ) -> float:
        captured["urls"] = streetview_urls_by_poi
        return 0.4

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    score = await score_vibe(_plan(), _spec())
    assert math.isclose(score, 0.4)
    assert captured["urls"] is None


# ---------- score_plan (sync ratchet path) ----------


def test_score_plan_returns_zero_total_for_failed_hard_rules() -> None:
    """A plan that fails any hard rule is total_score=0 with ``hard_pass=False``."""
    plan = _plan(pois=[])
    scored = score_plan(plan, _spec())
    assert scored["hard_pass"] is False
    assert scored["total_score"] == 0.0
    assert scored["soft_scores"] is None
    assert scored["failures"]


def test_score_plan_uses_cached_vibe_when_present() -> None:
    """The agent ratchet caches vibe in ``_vibe_cached`` to avoid double-judging."""
    plan = _plan()
    plan["_vibe_cached"] = 0.9
    scored = score_plan(plan, _spec())
    assert scored["hard_pass"] is True
    assert math.isclose(scored["soft_scores"]["vibe"], 0.9)


# ---------- score_and_rank ----------


@pytest.mark.asyncio
async def test_score_and_rank_gates_failed_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed plans land at ``rank=None``; only one passing plan emerges rank-1."""

    async def fake_judge(*args: Any, **kwargs: Any) -> float:
        return 0.7

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    plans = [
        _plan(),
        _plan(pois=[]),
        _plan(error="llm crashed"),
    ]
    ranked = await score_and_rank(plans, _spec())
    passing = [r for r in ranked if r["hard_pass"]]
    failing = [r for r in ranked if not r["hard_pass"]]
    assert len(passing) == 1
    assert len(failing) == 2
    assert passing[0]["rank"] == 1
    assert all(f["rank"] is None for f in failing)


@pytest.mark.asyncio
async def test_score_and_rank_orders_by_total_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three passing plans rank by descending ``total_score``."""
    vibe_scores = iter([0.9, 0.5, 0.1])

    async def fake_judge(*args: Any, **kwargs: Any) -> float:
        return next(vibe_scores)

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    plans = [_plan(agent_name="a"), _plan(agent_name="b"), _plan(agent_name="c")]
    ranked = await score_and_rank(plans, _spec())
    by_rank = sorted(ranked, key=lambda r: r["rank"] or 99)
    assert by_rank[0]["agent_name"] == "a"
    assert by_rank[1]["agent_name"] == "b"
    assert by_rank[2]["agent_name"] == "c"


@pytest.mark.asyncio
async def test_score_and_rank_passes_streetview_urls_to_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``score_and_rank`` forwards ``streetview_urls_by_poi`` to the judge."""
    captured: dict[str, Any] = {}

    async def fake_judge(
        plan: dict[str, Any],
        spec: Any,
        streetview_urls_by_poi: dict[str, list[str]] | None = None,
    ) -> float:
        captured["urls"] = streetview_urls_by_poi
        return 0.6

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    photos = {"poi-a": ["https://photo1"]}
    await score_and_rank([_plan()], _spec(), streetview_urls_by_poi=photos)
    assert captured["urls"] == photos


@pytest.mark.asyncio
async def test_score_and_rank_skips_judge_when_vibe_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan with ``_vibe_cached`` set must not re-call the judge."""
    call_count = {"n": 0}

    async def fake_judge(*args: Any, **kwargs: Any) -> float:
        call_count["n"] += 1
        return 0.0

    monkeypatch.setattr("app.agents.judge.judge_vibe", fake_judge)

    plan = _plan()
    plan["_vibe_cached"] = 0.85
    ranked = await score_and_rank([plan], _spec())
    assert call_count["n"] == 0
    assert math.isclose(ranked[0]["soft_scores"]["vibe"], 0.85)
