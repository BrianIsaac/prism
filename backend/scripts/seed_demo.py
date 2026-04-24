"""Demo-seed script: three REAL races, three HITL ratings, no mocks.

Fires three live ``POST /race`` requests against a running backend at
``http://localhost:8000`` (override via ``PRISM_API_BASE``), drains each
SSE stream until ``race_complete``, grabs the rank-1 plan, and POSTs a
pre-baked rating so ``validated_plans``, ``plan_atoms``,
``streetview_cache``, ``traffic_snapshots``, and ``incident_snapshots`` all
populate organically. The rating drifts ``_runtime_weights`` and appends a
``weight_history`` row per submission so the admin drift panel has
something non-flat to render.

Idempotent. Safe to re-run. Checks ``/validated`` for existing explicit
pins; if at least three explicit pins already exist the script exits
silently.

Usage:
    uv run python scripts/seed_demo.py
    PRISM_API_BASE=http://localhost:8001 uv run python scripts/seed_demo.py

Requirements:
    - Backend running on ``PRISM_API_BASE``.
    - ``backend/.env`` populated with real ``GRABMAPS_API_KEY``,
      ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``GEMINI_API_KEY`` (the
      race spawns all three provider agents).

Logs are emitted to stderr so the output can be piped without contaminating
the seed data. Failure on any single race is logged and the script moves
to the next query — so a transient provider 429 on agent 1 does not block
seeding the other two.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx

log = logging.getLogger("prism.seed")


API_BASE: str = os.environ.get("PRISM_API_BASE", "http://localhost:8000").rstrip("/")

# Three queries chosen to exercise distinct Singapore neighbourhoods so the
# seeded pins spread across the globe rather than clustering in one area.
QUERIES: list[dict[str, Any]] = [
    {
        "query": (
            "Geylang hawker crawl, 4 hours, halal, photogenic, "
            "end near an MRT, budget SGD 40"
        ),
        "rating": {"novelty": 4, "efficiency": 3, "vibe": 5},
    },
    {
        "query": (
            "Sentosa family day, 6 hours, family of four, accessible, "
            "budget SGD 200"
        ),
        "rating": {"novelty": 3, "efficiency": 4, "vibe": 4},
    },
    {
        "query": (
            "Chinatown heritage walk, 3 hours, chill, cultural, photogenic"
        ),
        "rating": {"novelty": 5, "efficiency": 3, "vibe": 5},
    },
]

# The race runner defaults to 600 s per the config, but the SSE drain is
# capped at 10 minutes here as well so a hung agent cannot stall the script.
STREAM_TIMEOUT_SECONDS: float = 600.0


async def _already_seeded(client: httpx.AsyncClient) -> bool:
    """Return ``True`` when three or more explicit validated plans exist.

    ``auto-`` prefixed ids are synthetic rank-1 synthesis, not explicit
    HITL pins — the acceptance check (``id NOT LIKE 'auto-%'``) counts the
    real rows only, so the idempotency guard matches.
    """
    r = await client.get(f"{API_BASE}/validated?limit=200")
    r.raise_for_status()
    rows = r.json().get("validated_plans", [])
    explicit = [row for row in rows if not str(row.get("id", "")).startswith("auto-")]
    log.info("found %d explicit + %d synthetic validated plans", len(explicit), len(rows) - len(explicit))
    return len(explicit) >= 3


async def _run_one(client: httpx.AsyncClient, query: dict[str, Any]) -> bool:
    """Fire one race, drain the stream, post a rating for the rank-1 plan.

    Args:
        client: Shared HTTP client.
        query: ``{"query": str, "rating": {"novelty", "efficiency", "vibe"}}``.

    Returns:
        ``True`` if a rank-1 plan was rated, ``False`` otherwise (so the
        caller can surface the failure count at the end).
    """
    log.info("POST /race for: %s", query["query"][:80])
    try:
        handshake = await client.post(f"{API_BASE}/race", json={"query": query["query"]})
        handshake.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("POST /race failed: %s", exc)
        return False
    race_id = handshake.json()["race_id"]
    log.info("race_id=%s — draining stream", race_id)

    ranked_plan_ids: list[str] = []
    try:
        async with client.stream(
            "GET",
            f"{API_BASE}/race/{race_id}/stream",
            timeout=STREAM_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_lines():
                line = chunk.strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[len("data:") :].strip()
                if not payload_str:
                    continue
                try:
                    event = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type in {"tool_call", "tool_result", "thought", "arc"}:
                    continue
                if event_type == "plan_resolved":
                    log.info(
                        "plan_resolved agent=%s rank=%s score=%.3f",
                        event.get("agent"),
                        (event.get("payload") or {}).get("rank"),
                        (event.get("payload") or {}).get("score") or 0.0,
                    )
                    continue
                if event_type == "error":
                    log.warning("race error: %s", event.get("payload"))
                    continue
                if event_type == "race_complete":
                    plans = (event.get("payload") or {}).get("plans") or []
                    ranked_plan_ids = [
                        p.get("plan_id") or p.get("id") or ""
                        for p in plans
                        if p.get("hard_pass") and p.get("rank")
                    ]
                    break
    except httpx.HTTPError as exc:
        log.error("stream drain failed: %s", exc)
        return False

    if not ranked_plan_ids:
        log.warning("no passing rank-1 plan for race=%s — skipping rating", race_id)
        return False
    rank1 = ranked_plan_ids[0]
    log.info("rating rank-1 plan_id=%s", rank1)
    try:
        r = await client.post(
            f"{API_BASE}/rating",
            json={
                "plan_id": rank1,
                **query["rating"],
                "comment": f"seed_demo: {query['query'][:60]}",
            },
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("POST /rating failed: %s", exc)
        return False
    log.info("rated plan=%s", rank1)
    return True


async def main() -> int:
    """Entry point. Returns process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="[seed_demo] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            probe = await client.get(f"{API_BASE}/health")
            probe.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("backend at %s not reachable: %s", API_BASE, exc)
            return 2
        if await _already_seeded(client):
            log.info("already seeded (>= 3 explicit validated plans) — nothing to do")
            return 0
        successes = 0
        for query in QUERIES:
            if await _run_one(client, query):
                successes += 1
        log.info(
            "seeding complete: %d/%d races rated", successes, len(QUERIES)
        )
        return 0 if successes == len(QUERIES) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
