"""Frozen scoring harness for Prism.

FROZEN CONTRACT. Agents must not read, call, or edit this file during a race.
Version bumps require re-running prior plans to preserve comparability.

The harness is the single load-bearing component of the autoresearch analogy:
it is the immovable evaluation that makes agents' outputs rankable. The
weights can drift via the HITL flywheel, but the *shape* of the evaluation
is fixed for the duration of the event.

v2 deltas vs v1:
    - ``score_vibe`` and ``score_and_rank`` accept ``streetview_urls_by_poi``
      (dict[poi_id, list[url]]). When non-empty, the vibe judge grounds its
      scoring in real OpenStreetCam photos rather than agent prose.
    - The money rule reads ``leg.route.fee.amount`` (raw GrabMaps route fee
      response, often 0 outside ERP zones) in addition to any flattened
      ``leg.fee_amount`` field — both forms pass through the same accumulator.
    - The opening-hours rule treats the GrabMaps ``opening_hours`` JSON string
      as the source of truth: empty / ``"{}"`` / unparseable → UNKNOWN → pass;
      populated schedule → enforced for the POI's ``visit_window``.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

HARNESS_VERSION: str = "v1"
HARNESS_WEIGHTS: dict[str, float] = {"flow": 0.5, "diversity": 0.2, "vibe": 0.3}


@dataclass(frozen=True)
class HardRuleResult:
    """Aggregate result of all seven hard-rule checks for a single plan."""

    passed: bool
    failures: list[str]


@dataclass(frozen=True)
class SoftScores:
    """Per-dimension soft scores, aggregable into a single weighted total."""

    flow: float
    diversity: float
    vibe: float

    def aggregate(self, weights: dict[str, float]) -> float:
        """Combine the three dimensions into a single weighted score.

        Args:
            weights: Mapping with ``flow``, ``diversity``, and ``vibe`` keys.

        Returns:
            The weighted sum, in the same numeric range as the inputs.
        """
        return (
            weights["flow"] * self.flow
            + weights["diversity"] * self.diversity
            + weights["vibe"] * self.vibe
        )


# ---------- Hard rules ----------


def check_hard_rules(plan: dict[str, Any], spec: dict[str, Any]) -> HardRuleResult:
    """Run every hard rule against a plan and collect failures.

    Order matches the v2 numbering in ``docs/hackathon-phases/phase-01``:
        1. Time budget — sum (leg duration + dwell) ≤ ``max_duration_minutes``.
        2. Money budget — sum ``leg.route.fee.amount`` + per-POI cost.
        3. Transport reachability — every leg routed (no ``unreachable`` flag).
        4. Opening hours — populated schedule must cover the visit window.
        5. Dietary constraint — every food POI matches the user's filter.
        6. Anchor endpoints — start/end POIs within 200 m of the anchors.
        7. Feasibility — no null / placeholder / unresolved POI.

    Args:
        plan: A plan dict produced by an agent.
        spec: A parsed Spec dict.

    Returns:
        :class:`HardRuleResult` with ``passed=True`` and an empty list when
        every rule clears, otherwise ``passed=False`` and one human-readable
        message per failed rule.
    """
    failures: list[str] = []

    # Agent outputs are LLM-produced JSON; defensively filter to dicts so a
    # malformed element (a stray list/string from a hallucinated schema
    # variant) does not raise ``AttributeError: 'list' object has no
    # attribute 'get'`` and burn the whole race's scoring pass.
    pois = [p for p in (plan.get("pois") or []) if isinstance(p, dict)]
    legs = [leg for leg in (plan.get("legs") or []) if isinstance(leg, dict)]

    if plan.get("error"):
        failures.append(f"agent error: {plan.get('error')}")
        return HardRuleResult(passed=False, failures=failures)

    if not pois:
        failures.append("no POIs in plan")
        return HardRuleResult(passed=False, failures=failures)

    # Rule 1 — time budget (leg durations + dwell)
    max_minutes = float(spec.get("max_duration_minutes") or 0)
    if max_minutes > 0:
        total_minutes = _total_duration_minutes(plan)
        if total_minutes > max_minutes:
            failures.append(
                f"time budget: {total_minutes:.1f}m > {max_minutes:.0f}m"
            )

    # Rule 2 — money budget (sum leg.route.fee.amount across legs + POI costs)
    max_budget = float(spec.get("max_budget_sgd") or 0)
    if max_budget > 0:
        total_cost = _total_cost_sgd(plan)
        if total_cost > max_budget:
            failures.append(
                f"money budget: SGD {total_cost:.2f} > {max_budget:.2f}"
            )

    # Rule 3 — transport reachability (no unreachable legs)
    transport = spec.get("transport_mode") or "walk"
    for leg in legs:
        if leg.get("unreachable"):
            failures.append(
                f"unreachable leg via {transport}: "
                f"{leg.get('from')} -> {leg.get('to')}"
            )

    # Rule 4 — opening hours (parse JSON string lazily; UNKNOWN passes)
    for poi in pois:
        window = poi.get("visit_window")
        hours = _parse_opening_hours(poi.get("opening_hours"))
        if not window or not hours:
            continue
        verdict = _window_within_hours(tuple(window), hours)
        if verdict is False:
            failures.append(
                f"closed at visit window: {poi.get('name', poi.get('id'))}"
            )

    # Rule 5 — dietary filter (every food POI must match)
    dietary = spec.get("dietary")
    if dietary:
        for poi in pois:
            if not poi.get("is_food"):
                continue
            tags = poi.get("dietary_tags") or []
            if dietary not in tags:
                failures.append(
                    f"dietary {dietary} not satisfied by "
                    f"{poi.get('name', poi.get('id'))}"
                )

    # Rule 6 — anchor endpoints (within 200 m of start/end)
    start_anchor = spec.get("start_anchor")
    end_anchor = spec.get("end_anchor")
    if start_anchor and pois and not _within_tolerance(pois[0], start_anchor, 200):
        failures.append("start anchor: first POI is more than 200m away")
    if end_anchor and pois and not _within_tolerance(pois[-1], end_anchor, 200):
        failures.append("end anchor: last POI is more than 200m away")

    # Rule 7 — feasibility (no null / unresolved POI ids)
    for poi in pois:
        if not poi.get("id"):
            failures.append(
                f"unresolved POI placeholder: {poi.get('name', '?')}"
            )

    return HardRuleResult(passed=not failures, failures=failures)


# ---------- Soft scores ----------


def score_flow(plan: dict[str, Any]) -> float:
    """Score how cohesive the plan's routing is in ``[0, 1]``.

    Penalises revisits to the same POI in the leg sequence, and rewards a
    healthy ratio of dwell time to travel time.
    """
    legs = [leg for leg in (plan.get("legs") or []) if isinstance(leg, dict)]
    pois = [p for p in (plan.get("pois") or []) if isinstance(p, dict)]
    if not legs:
        return 0.0
    total = sum(_leg_duration_minutes(leg) for leg in legs)
    visited = [leg.get("from") for leg in legs] + [legs[-1].get("to")]
    counts = Counter(v for v in visited if v is not None)
    revisits = sum(c - 1 for c in counts.values() if c > 1)
    penalty = min(0.5, 0.1 * revisits)
    baseline = 1.0 - penalty
    dwell = sum(int(poi.get("dwell_minutes") or 30) for poi in pois)
    if dwell + total > 0:
        ratio = dwell / max(dwell + total, 1)
        return max(0.0, min(1.0, baseline * (0.5 + 0.5 * ratio)))
    return baseline


def score_diversity(plan: dict[str, Any]) -> float:
    """Score POI category diversity via Shannon entropy in ``[0, 1]``.

    Pairs are formed from ``(category, subcategory)`` so two coffee shops in
    different subcategories still count as distinct.
    """
    pois = [p for p in (plan.get("pois") or []) if isinstance(p, dict)]
    pairs = [
        (
            poi.get("category", "other"),
            poi.get("subcategory") or poi.get("category", "other"),
        )
        for poi in pois
    ]
    if not pairs:
        return 0.0
    counts = Counter(pairs)
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    max_entropy = math.log2(len(counts)) if len(counts) > 1 else 1.0
    return entropy / max_entropy if max_entropy > 0 else 0.0


async def score_vibe(
    plan: dict[str, Any],
    spec: dict[str, Any],
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
) -> float:
    """Score atmospheric match via the photo-grounded Haiku judge.

    Args:
        plan: The plan dict to score.
        spec: The parsed spec dict (carries ``raw_query`` and ``mood_tags``).
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs collected during
            the race. When non-empty, the judge embeds the URLs as vision
            content blocks and scores the actual photos. When ``None`` or
            empty, the judge falls back to prose-only scoring.

    Returns:
        A score in ``[0.0, 1.0]``; the judge handles its own failure paths
        by returning ``0.5`` (neutral).
    """
    from app.agents.judge import judge_vibe

    return await judge_vibe(plan, spec, streetview_urls_by_poi)


# ---------- Plan-level scoring ----------


def score_plan(
    plan: dict[str, Any],
    spec: dict[str, Any],
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Score a single plan in isolation (used by the agent ratchet loop).

    Synchronous so the agent runner can call it without awaiting; the vibe
    score is taken from ``plan["_vibe_cached"]`` when available (the runner
    caches that value after a successful judge call) and otherwise defaults
    to the neutral ``0.5`` so a plan that has not yet been judged still
    yields a ranking signal.

    Args:
        plan: The plan dict.
        spec: The parsed spec dict.
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs (unused in the
            sync path; kept on the signature so the ratchet call site does
            not need a separate branch).
        weights: Optional override of :data:`HARNESS_WEIGHTS`.
        **kwargs: Forward-compat slot for future scoring dimensions.

    Returns:
        A dict shaped ``{"hard_pass", "failures", "soft_scores",
        "total_score"}`` ready to merge into the caller's plan dict.
    """
    _ = (streetview_urls_by_poi, kwargs)
    effective_weights = weights or HARNESS_WEIGHTS

    hr = check_hard_rules(plan, spec)
    if not hr.passed:
        return {
            "hard_pass": False,
            "failures": hr.failures,
            "soft_scores": None,
            "total_score": 0.0,
        }

    flow = score_flow(plan)
    diversity = score_diversity(plan)
    vibe = float(plan.get("_vibe_cached", 0.5))
    scores = SoftScores(flow=flow, diversity=diversity, vibe=vibe)
    return {
        "hard_pass": True,
        "failures": [],
        "soft_scores": {"flow": flow, "diversity": diversity, "vibe": vibe},
        "total_score": scores.aggregate(effective_weights),
    }


async def score_and_rank(
    plans: list[dict[str, Any]],
    spec: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Score every plan, gate on hard rules, rank survivors by total score.

    Plans with ``_vibe_cached`` set are not re-judged — the agent runner
    populates that key during the ratchet loop so a passing plan does not
    pay for the vibe judge twice.

    Args:
        plans: Raw plan dicts emitted by each agent.
        spec: The parsed spec dict.
        weights: Optional override of :data:`HARNESS_WEIGHTS`.
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs forwarded to the
            vibe judge. ``None`` / empty falls back to prose-only judging.

    Returns:
        Every plan with ``hard_pass``, ``failures``, ``soft_scores``,
        ``total_score``, and ``rank`` populated. Failed plans keep
        ``rank=None`` and a zero ``total_score``.
    """
    effective_weights = weights or HARNESS_WEIGHTS

    hard_results = [check_hard_rules(p, spec) for p in plans]
    passing_pairs = [
        (idx, p) for idx, (p, hr) in enumerate(zip(plans, hard_results)) if hr.passed
    ]
    vibe_tasks = [
        _vibe_for(plan, spec, streetview_urls_by_poi) for _, plan in passing_pairs
    ]
    vibe_scores = await asyncio.gather(*vibe_tasks) if vibe_tasks else []
    vibe_by_idx: dict[int, float] = {
        passing_pairs[i][0]: float(score) for i, score in enumerate(vibe_scores)
    }

    results: list[dict[str, Any]] = []
    for idx, (plan, hr) in enumerate(zip(plans, hard_results)):
        if not hr.passed:
            results.append(
                {
                    **plan,
                    "hard_pass": False,
                    "failures": hr.failures,
                    "soft_scores": None,
                    "total_score": 0.0,
                    "rank": None,
                }
            )
            continue
        flow = score_flow(plan)
        diversity = score_diversity(plan)
        vibe = vibe_by_idx[idx]
        scores = SoftScores(flow=flow, diversity=diversity, vibe=vibe)
        total = scores.aggregate(effective_weights)
        results.append(
            {
                **plan,
                "hard_pass": True,
                "failures": [],
                "soft_scores": {
                    "flow": flow,
                    "diversity": diversity,
                    "vibe": vibe,
                },
                "total_score": total,
                "rank": None,
                "_vibe_cached": vibe,
            }
        )

    passing = [r for r in results if r["hard_pass"]]
    passing.sort(key=lambda r: r["total_score"], reverse=True)
    for rank, scored in enumerate(passing, start=1):
        scored["rank"] = rank

    return results


# ---------- Internal helpers ----------


async def _vibe_for(
    plan: dict[str, Any],
    spec: dict[str, Any],
    streetview_urls_by_poi: dict[str, list[str]] | None,
) -> float:
    """Return the cached vibe score on a plan, or call the judge once."""
    cached = plan.get("_vibe_cached")
    if cached is not None:
        return float(cached)
    return await score_vibe(plan, spec, streetview_urls_by_poi)


def _total_duration_minutes(plan: dict[str, Any]) -> float:
    """Sum leg durations (route.duration in seconds, or duration_minutes) + dwell."""
    legs = [leg for leg in (plan.get("legs") or []) if isinstance(leg, dict)]
    pois = [p for p in (plan.get("pois") or []) if isinstance(p, dict)]
    travel = sum(_leg_duration_minutes(leg) for leg in legs)
    dwell = sum(float(poi.get("dwell_minutes") or 0) for poi in pois)
    if not legs and not dwell:
        explicit = plan.get("total_minutes")
        if explicit is not None:
            return float(explicit)
    return travel + dwell


def _leg_duration_minutes(leg: dict[str, Any]) -> float:
    """Per-leg duration in minutes, prefering raw ``route.duration`` (seconds)."""
    route = leg.get("route") or {}
    if isinstance(route, dict) and route.get("duration") is not None:
        return float(route["duration"]) / 60.0
    return float(leg.get("duration_minutes") or 0.0)


def _total_cost_sgd(plan: dict[str, Any]) -> float:
    """Sum ``leg.route.fee.amount`` (or ``leg.fee_amount``) + per-POI costs."""
    legs = [leg for leg in (plan.get("legs") or []) if isinstance(leg, dict)]
    pois = [p for p in (plan.get("pois") or []) if isinstance(p, dict)]
    leg_cost = sum(_leg_fee_amount(leg) for leg in legs)
    poi_cost = sum(float(poi.get("avg_cost_sgd") or 0.0) for poi in pois)
    explicit = plan.get("total_cost_sgd")
    if leg_cost == 0.0 and poi_cost == 0.0 and explicit is not None:
        return float(explicit)
    return leg_cost + poi_cost


def _leg_fee_amount(leg: dict[str, Any]) -> float:
    """Per-leg fee. Prefer raw ``route.fee.amount``; fall back to ``fee_amount``.

    GrabMaps' route response carries ``fee.amount`` for ERP / toll surcharges
    (often 0 for routes that miss any pricing zone). Plans that haven't been
    enriched with the raw route response fall through to ``fee_amount`` (the
    flattened field on :class:`~app.models.PlanLeg`) and finally to ``0.0``.
    """
    route = leg.get("route") or {}
    fee = route.get("fee") if isinstance(route, dict) else None
    if isinstance(fee, dict) and fee.get("amount") is not None:
        return float(fee["amount"])
    if "fee_amount" in leg and leg["fee_amount"] is not None:
        return float(leg["fee_amount"])
    return 0.0


def _parse_opening_hours(raw: Any) -> list[dict[str, Any]] | None:
    """Coerce GrabMaps' ``opening_hours`` (JSON string or list) to a list.

    Returns ``None`` when the field is unknown / unparseable / empty so the
    caller treats it as UNKNOWN and the plan passes the hours rule.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw or None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text == "{}":
            return None
        try:
            decoded = json.loads(text)
        except (ValueError, TypeError):
            return None
        if isinstance(decoded, list):
            return decoded or None
        if isinstance(decoded, dict):
            if not decoded:
                return None
            entries: list[dict[str, Any]] = []
            for day, slot in decoded.items():
                if isinstance(slot, dict):
                    entries.append({"day": day, **slot})
                elif isinstance(slot, list) and slot and isinstance(slot[0], dict):
                    for s in slot:
                        entries.append({"day": day, **s})
            return entries or None
    return None


def _window_within_hours(
    window: tuple[str, str],
    opening_hours: list[dict[str, Any]],
) -> bool | None:
    """Return whether a visit window falls inside the POI's hours.

    Returns ``True`` / ``False`` when the verdict is unambiguous, or ``None``
    when the inputs are not parseable as ISO datetimes — the caller should
    treat ``None`` as UNKNOWN and let the plan pass the hours rule.
    """
    try:
        start = datetime.fromisoformat(window[0])
        end = datetime.fromisoformat(window[1])
    except (TypeError, ValueError):
        return None

    weekday = start.strftime("%A").lower()
    for entry in opening_hours:
        if not isinstance(entry, dict):
            continue
        day = str(entry.get("day", "")).lower()
        if day != weekday:
            continue
        try:
            open_t = datetime.strptime(str(entry.get("open", "")), "%H:%M").time()
            close_t = datetime.strptime(str(entry.get("close", "")), "%H:%M").time()
        except (TypeError, ValueError):
            continue
        if open_t <= start.time() and end.time() <= close_t:
            return True
        return False
    return None


def _within_tolerance(
    poi: dict[str, Any],
    anchor: dict[str, Any],
    metres: float,
) -> bool:
    """Haversine check: is ``poi`` within ``metres`` of ``anchor``?

    Returns ``True`` if any coordinate is missing — the harness errs on the
    side of accepting plans whose endpoints we can't measure rather than
    failing them on missing metadata.
    """
    raw_lat1 = poi.get("lat")
    raw_lng1 = poi.get("lng")
    raw_lat2 = anchor.get("lat")
    raw_lng2 = anchor.get("lng")
    if raw_lat1 is None or raw_lng1 is None or raw_lat2 is None or raw_lng2 is None:
        return True
    lat1, lng1, lat2, lng2 = (
        float(raw_lat1),
        float(raw_lng1),
        float(raw_lat2),
        float(raw_lng2),
    )
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    distance = 2 * radius_m * math.asin(min(1.0, math.sqrt(a)))
    return distance <= metres


__all__ = [
    "HARNESS_VERSION",
    "HARNESS_WEIGHTS",
    "HardRuleResult",
    "SoftScores",
    "check_hard_rules",
    "score_and_rank",
    "score_diversity",
    "score_flow",
    "score_plan",
    "score_vibe",
]
