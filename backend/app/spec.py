"""Spec parser: free-form user query to structured constraints.

One :func:`call_llm` round-trip (Haiku by default) with a JSON schema
extracts the constraints. Falls back to conservative defaults if the
parse fails — a minimally-specified spec is better than a crashed race.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import SPEC_PARSER_MODEL
from app.llm_clients import call_llm
from app.models import Spec

log = logging.getLogger("prism")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_PARSER_SYSTEM_PROMPT = """You extract structured travel/itinerary constraints from a free-form user query about discovering a city.

Return ONLY a JSON object matching this schema:
{
  "area": string | null (e.g. "Geylang", "Chinatown"; null if not specified),
  "city": string (default "Singapore"),
  "country_iso3": string (ISO 3166-1 alpha-3, default "SGP"),
  "max_duration_minutes": integer (default 240),
  "max_budget_sgd": number (default 50),
  "transport_mode": "walk" | "drive" | "transit" | "cycle" (default "walk"),
  "dietary": "halal" | "vegetarian" | "vegan" | null,
  "mood_tags": array of short lowercase adjectives extracted from the query (e.g. ["photogenic", "quiet"]),
  "start_time_iso": string | null,
  "party_size": integer (1-20, default 1),
  "accessible": boolean (default false; true when user mentions wheelchair, step-free, mobility, stroller, or accessibility)
}

Rules:
- Infer sensible defaults for any unspecified field.
- "half-day" means 240 minutes. "morning" means 180 minutes from 09:00.
- Treat "under $40" as max_budget_sgd=40.
- mood_tags must be normalised to lowercase adjectives, maximum 5.
- "couple / date / 2 people" implies party_size=2; "family of 4" implies party_size=4.
- "wheelchair", "step-free", "with a stroller", "accessibility" all imply accessible=true.
- Never output prose or explanation — JSON only."""


async def parse_spec(raw_query: str) -> Spec:
    """Parse a free-form user query into a structured :class:`~app.models.Spec`.

    Args:
        raw_query: The raw user input string.

    Returns:
        A fully populated :class:`Spec`. Falls back to defaults if LLM
        parsing fails — logs the root cause at WARNING so operators can grep
        the admin bug report when a race returns defaulted plans.
    """
    try:
        response = await call_llm(
            provider="anthropic",
            model=SPEC_PARSER_MODEL,
            messages=[
                {"role": "system", "content": _PARSER_SYSTEM_PROMPT},
                {"role": "user", "content": raw_query},
            ],
            tools=None,
            max_tokens=1024,
        )
        content = _FENCE_RE.sub("", (response.content or "{}").strip()).strip() or "{}"
        parsed: dict[str, Any] = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        log.warning("spec parse LLM call failed: %s", exc)
        parsed = {}

    return Spec(
        raw_query=raw_query,
        area=parsed.get("area"),
        city=parsed.get("city", "Singapore"),
        country_iso3=parsed.get("country_iso3", "SGP"),
        max_duration_minutes=int(parsed.get("max_duration_minutes", 240)),
        max_budget_sgd=float(parsed.get("max_budget_sgd", 50)),
        transport_mode=parsed.get("transport_mode", "walk"),
        dietary=parsed.get("dietary"),
        mood_tags=parsed.get("mood_tags", []) or [],
        start_time_iso=parsed.get("start_time_iso"),
        party_size=max(1, int(parsed.get("party_size") or 1)),
        accessible=bool(parsed.get("accessible") or False),
    )
