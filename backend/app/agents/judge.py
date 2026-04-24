"""Vibe judge — photo-grounded Haiku scorer.

Called once per candidate plan by the frozen harness. In v2 the judge sees
the real OpenStreetCam photos each agent fetched via ``get_street_view``,
so a plan whose POIs look photogenic actually earns the vibe score rather
than the model fabricating a narrative match.

Two paths, same contract:

- ``streetview_urls_by_poi`` supplied and non-empty: build an Anthropic
  Messages payload with vision content blocks (``type: image``, url source)
  interleaved with the POI text summary. The judge reasons over the photos.
- ``streetview_urls_by_poi`` absent or empty: prose-only path — Haiku reads
  the POI descriptions, categories, and tags and scores against the mood
  tags alone. Same return contract, slightly lower-fidelity signal.

Return is always a float in ``[0.0, 1.0]`` so the weighted aggregate stays
numerically stable. Any transport or parse failure falls back to ``0.5``
(neutral) and logs at WARNING so the admin bug report can surface drift.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import JUDGE_MODEL
from app.llm_clients import call_llm

log = logging.getLogger("prism")

_JUDGE_SYSTEM_PROMPT = """You are a vibe judge for a city discovery itinerary scoring system.

Given a user query (with mood tags), an itinerary plan, and optionally real OpenStreetCam photos attached to the plan's POIs, return a single score in [0.0, 1.0] measuring how well the plan matches the query's mood — coherent atmosphere, sensory throughline, emotional fit.

Rules:
- 0.0 means completely off-brief. 1.0 means a perfect atmospheric match.
- When photos are supplied, judge primarily on what the photos actually show (street texture, crowd, light, vegetation, signage). The POI descriptions and tags are secondary evidence, not the primary source.
- When no photos are supplied, judge on the POI descriptions, tags, and categories.
- Do not re-evaluate hard rules (time, money, dietary, opening hours — those are already gated).
- Do not reward verbosity or clever narrative prose.
- Ignore the order the POIs are presented in.
- Return ONLY a JSON object: {"score": number, "reason": "one sentence"}. No other text."""

# Cap photos per POI fed to the judge so the vision payload fits in Haiku's
# context window without truncation. 3 photos per POI across 4-6 POIs is at
# most 18 images — well inside the limit.
_MAX_PHOTOS_PER_POI: int = 3


async def judge_vibe(
    plan: dict[str, Any],
    spec: dict[str, Any] | Any,
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
) -> float:
    """Score a single plan on vibe match in ``[0.0, 1.0]``.

    Args:
        plan: Structured plan dict with ``pois`` and ``narrative``.
        spec: Parsed spec (dict or :class:`~app.models.Spec`) with
            ``raw_query`` and ``mood_tags``.
        streetview_urls_by_poi: Mapping of POI id to a list of OpenStreetCam
            URLs collected during the race. When present and non-empty the
            judge receives vision content blocks rather than prose-only
            context. Missing keys are treated as "no photos for that POI".

    Returns:
        A score in ``[0.0, 1.0]``. Returns ``0.5`` (neutral) on any failure.

    Failure modes (network, rate limit, malformed JSON) are logged at
    WARNING so :mod:`app.trace_export` can surface systematic drift; the
    ``0.5`` default keeps ranking deterministic rather than letting a single
    flake propagate through the aggregate score.
    """
    spec_dict = _spec_to_dict(spec)
    # ``isinstance`` filter matches the harness hardening — a stray list
    # element inside ``pois`` (from a hallucinated nested-list schema
    # variant) would otherwise raise ``AttributeError: 'list' object has
    # no attribute 'get'`` and crash the whole race's scoring pass.
    pois_summary = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "category": p.get("category"),
            "description": (p.get("description") or "")[:200],
            "tags": (p.get("tags") or [])[:6],
        }
        for p in (plan.get("pois") or [])
        if isinstance(p, dict)
    ]
    payload_text = json.dumps(
        {
            "query": spec_dict.get("raw_query", ""),
            "mood_tags": spec_dict.get("mood_tags", []),
            "pois": pois_summary,
        }
    )

    user_content = _build_user_content(
        payload_text, pois_summary, streetview_urls_by_poi
    )

    try:
        response = await call_llm(
            provider="anthropic",
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            tools=None,
            max_tokens=200,
        )
        content = (response.content or "").strip()
        # Haiku usually emits a clean JSON object but occasionally wraps it
        # in commentary; regex-recover to the outermost ``{...}``.
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            log.warning("vibe judge returned non-JSON content: %r", content[:200])
            return 0.5
        parsed = json.loads(match.group(0))
        score = float(parsed.get("score", 0.5))
        return max(0.0, min(1.0, score))
    except Exception as exc:  # noqa: BLE001
        log.warning("vibe judge LLM call failed: %s", exc)
        return 0.5


def _build_user_content(
    payload_text: str,
    pois_summary: list[dict[str, Any]],
    streetview_urls_by_poi: dict[str, list[str]] | None,
) -> Any:
    """Build the user-message content for the judge call.

    Returns a plain string on the prose path (no photos supplied) and an
    Anthropic vision content list otherwise. The dispatch layer
    (:mod:`app.llm_clients`) forwards either shape unchanged because the
    Anthropic Messages API accepts both.

    Args:
        payload_text: JSON-serialised summary of query + mood tags + POIs.
        pois_summary: The list of POI summaries (used to look up ids).
        streetview_urls_by_poi: Optional per-POI photo URLs.

    Returns:
        Either a prose string or a list of Anthropic content blocks with
        interleaved ``image`` and ``text`` entries.
    """
    if not streetview_urls_by_poi:
        return payload_text

    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Plan summary (JSON). Below the summary you'll find real "
                "OpenStreetCam photos attached to each POI. Judge primarily "
                "on what the photos show.\n\n" + payload_text
            ),
        }
    ]
    any_photo = False
    for poi in pois_summary:
        poi_id = poi.get("id")
        urls = streetview_urls_by_poi.get(poi_id) if poi_id else None
        if not urls:
            continue
        blocks.append(
            {
                "type": "text",
                "text": f"Photos for {poi.get('name') or poi_id}:",
            }
        )
        for url in urls[:_MAX_PHOTOS_PER_POI]:
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "url", "url": url},
                }
            )
            any_photo = True

    # Empty photo dict or every POI absent from the mapping — fall back to
    # prose rather than sending a degenerate vision payload.
    if not any_photo:
        return payload_text
    return blocks


def _spec_to_dict(spec: Any) -> dict[str, Any]:
    """Coerce a :class:`~app.models.Spec` or dict to a plain dict."""
    if isinstance(spec, dict):
        return spec
    dump = getattr(spec, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, dict):
            return result
    return {}
