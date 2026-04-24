"""Agent runner — bounded tool-use loop with SSE event emission.

One runner drives all three racers. Only the :class:`AgentConfig` differs
(provider, model, temperature, colour); the tool belt, the prompt, and the
harness ratchet are shared verbatim. This is the v2 shape of the Karpathy
"autoresearch ratchet" compressed into a single agent's lifecycle:

1. The model calls tools (from :data:`~app.tools.GRABMAPS_TOOLS`). Every
   call emits a ``tool_call`` and a ``tool_result`` SSE event via
   :class:`EventEmitter`, and the 40-call budget decrements.
2. ``emit_thought`` is the one instrumentation hook — it emits a
   ``thought`` event and returns ``{"ok": true}`` without touching the
   budget, so agents can narrate key decisions cheaply.
3. When the model stops calling tools and emits JSON, the plan is parsed
   and guarded against hallucinated POI ids. A plan that survives the
   parse is scored against the frozen harness; if below the aggregate
   threshold, the scores are fed back as a user message and the loop
   ratchets up to :data:`~app.config.HARNESS_MAX_RETRIES` times.

The runner is provider-agnostic — Anthropic, OpenAI, and Gemini each
round-trip through :func:`app.llm_clients.call_llm`. Gemini's
``thought_signature`` round-trip is handled inside that module.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.agents.shared_prompt import SHARED_SYSTEM_PROMPT
from app.config import (
    HARNESS_MAX_RETRIES,
    HARNESS_MIN_AGGREGATE,
    TOOL_BUDGET_PER_AGENT,
)
from app.harness import HARNESS_WEIGHTS, score_plan
from app.llm_clients import Provider, ToolCallRequest, call_llm
from app.tools import GRABMAPS_TOOLS, GRABMAPS_TOOL_SCHEMA
from app.tools.base import call_tool_with_budget

# Minimum wall-clock slack (seconds) required before the ratchet fires
# another LLM turn. Gemini 3.1 Pro routinely consumes 90-150s per thinking
# turn; a retry with less headroom than this gets cancelled mid-call and
# the agent returns nothing. 120s fits one more Gemini turn.
_RETRY_TIME_MARGIN_SECONDS: float = 120.0

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

# Serialisation cap per tool-result so the assistant's context window does
# not run away when a places_search returns the full 10-hit payload.
_TOOL_RESULT_MAX_CHARS: int = 8000

# Keep the shared-prompt import live so sibling shards can import it from
# this module path without touching shared_prompt.py directly.
_SHARED_PROMPT_REF: str = SHARED_SYSTEM_PROMPT
assert _SHARED_PROMPT_REF  # validates build_shared_system_prompt() non-empty


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a single racing agent.

    Every racer runs :data:`~app.agents.shared_prompt.SHARED_SYSTEM_PROMPT`
    against :data:`~app.tools.GRABMAPS_TOOL_SCHEMA` on a different model
    family. ``provider`` picks the SDK adapter in
    :mod:`app.llm_clients`; ``model`` is the provider-native model id.

    ``temperature`` is optional because Claude Opus 4.7 rejects the
    parameter outright — ``None`` means "omit it from the request body"
    rather than guess a value that happens to work.

    ``colour`` is a SSE-event stamp. Each racer gets a deterministic
    colour (opus=red, gpt=green, gemini=blue) so the frontend can paint
    cursor pulses, arcs, and status pills per-agent without computing
    colours client-side.
    """

    name: str
    provider: Provider
    model: str
    temperature: float | None
    system_prompt: str
    colour: Literal["red", "green", "blue"]


_FINAL_ANSWER_INSTRUCTION = """When you have enough information to propose a plan, return ONLY a JSON object matching this schema:
{
  "pois": [
    {
      "id": string,
      "name": string,
      "category": string,
      "subcategory": string | null,
      "lat": number,
      "lng": number,
      "address": string | null,
      "description": string,
      "price_tier": integer (1-4),
      "avg_cost_sgd": number,
      "dietary_tags": array of strings,
      "opening_hours": array,
      "tags": array of strings,
      "is_food": boolean,
      "visit_window": [start_iso, end_iso] | null,
      "dwell_minutes": integer
    }
  ],
  "legs": [
    {
      "from": string (poi id),
      "to": string (poi id),
      "mode": "walk" | "drive" | "transit" | "cycle",
      "duration_minutes": number,
      "distance_metres": number,
      "unreachable": boolean
    }
  ],
  "total_minutes": number,
  "total_cost_sgd": number,
  "narrative": string (2-3 sentence pitch for this plan)
}

Output the JSON object and NOTHING else. No markdown fences, no commentary.

CRITICAL: Every POI in your plan MUST have been returned by an actual tool call (places_search, nearby_search, or reverse_geocode). Do not invent POI ids, coordinates, addresses, categories, or prices. A structural hallucination guard rejects plans containing ids you did not observe — smaller honest plans beat larger fabricated ones."""


# ---------- POI-id extraction + utility helpers ----------


def _extract_poi_ids(result: Any) -> list[str]:
    """Pull POI ids out of a GrabMaps tool result dict.

    Recognised shapes:
        - ``places_search`` / ``nearby_search``: ``{"places": [{"poi_id": ...}, ...]}``
          or ``{"results": [{"id": ...}, ...]}`` (legacy)
        - ``reverse_geocode``: ``{"place": {"poi_id": ...}}`` or similar
          single-place payload

    Used by :func:`run_agent` to track which POI ids the agent has observed
    so :func:`_parse_plan` can reject hallucinated ids — the guard must
    cover every tool that returns real POI ids, or an agent that only calls
    ``reverse_geocode`` to confirm a POI could have its plan rejected as
    hallucinated.
    """
    ids: list[str] = []
    if not isinstance(result, dict):
        return ids
    for list_key in ("places", "results"):
        items = result.get(list_key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                for id_key in ("poi_id", "id"):
                    pid = item.get(id_key)
                    if isinstance(pid, str) and pid:
                        ids.append(pid)
                        break
    place = result.get("place")
    if isinstance(place, dict):
        for id_key in ("poi_id", "id"):
            pid = place.get(id_key)
            if isinstance(pid, str) and pid:
                ids.append(pid)
                break
    top_pid = result.get("poi_id")
    if isinstance(top_pid, str) and top_pid and not result.get("error"):
        ids.append(top_pid)
    return ids


def _summarise_tool_result(tool_name: str, result: Any) -> str:
    """Render a one-line summary of a tool result for the SSE ``tool_result`` event.

    Keeps the SSE payload small and human-readable; the raw JSON goes back
    to the model via the ``tool`` message, not the event stream.
    """
    if isinstance(result, dict) and result.get("error"):
        return f"error: {str(result['error'])[:120]}"
    if tool_name in ("places_search", "nearby_search"):
        if isinstance(result, dict):
            items = result.get("places") or result.get("results") or []
            return f"{len(items)} places"
    if tool_name == "route":
        if isinstance(result, dict):
            routes = result.get("routes") or []
            if routes:
                dur = routes[0].get("duration_s") or routes[0].get("duration_seconds") or 0
                dist = routes[0].get("distance_m") or routes[0].get("distance_metres") or 0
                return f"route {int(dist)}m / {int(dur)}s"
    if tool_name == "route_matrix":
        if isinstance(result, dict):
            matrix = result.get("matrix") or result.get("routes") or []
            return f"matrix {len(matrix)} rows"
    if tool_name in ("get_traffic", "get_incidents"):
        if isinstance(result, dict):
            items = result.get("items") or result.get("results") or result.get("incidents") or []
            return f"{len(items)} items"
    if tool_name == "get_street_view":
        if isinstance(result, dict):
            photos = result.get("photos") or result.get("items") or []
            return f"{len(photos)} photos"
    if tool_name == "reverse_geocode":
        if isinstance(result, dict):
            place = result.get("place") or {}
            return str(place.get("formatted_address") or place.get("name") or "")[:120]
    return "ok"


def _extract_coords(args: dict[str, Any]) -> tuple[float | None, float | None]:
    """Pull a best-effort ``(lat, lng)`` out of tool arguments for canvas overlays."""
    lat = args.get("lat") or args.get("near_lat")
    lng = args.get("lng") or args.get("near_lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    origin = args.get("origin")
    if isinstance(origin, (list, tuple)) and len(origin) >= 2:
        try:
            return float(origin[0]), float(origin[1])
        except (TypeError, ValueError):
            return None, None
    return None, None


def _extract_thumb_url(tool_name: str, result: Any) -> str | None:
    """Pull the first photo thumbnail URL out of a ``get_street_view`` result."""
    if tool_name != "get_street_view" or not isinstance(result, dict):
        return None
    photos = result.get("photos") or result.get("items") or []
    if not isinstance(photos, list) or not photos:
        return None
    first = photos[0]
    if not isinstance(first, dict):
        return None
    return first.get("thumb_url") or first.get("thumbUrl") or first.get("fileUrl")


async def _emit(
    emitter: EventEmitter | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Fire an SSE event through the emitter if one is wired."""
    if emitter is None:
        return
    await emitter({"type": event_type, "payload": payload})


# ---------- Agent runner ----------


async def run_agent(
    agent: AgentConfig,
    spec: dict[str, Any],
    *,
    hot_candidates: list[dict[str, Any]] | None = None,
    feedback_kb: str | None = None,
    deadline_monotonic: float | None = None,
    race_id: str | None = None,
    event_emitter: EventEmitter | None = None,
) -> dict[str, Any]:
    """Drive one agent through a bounded tool-use loop and return its plan.

    Args:
        agent: :class:`AgentConfig` for this racer.
        spec: Parsed spec dict to pass to the model.
        hot_candidates: Optional plan-atom POI dicts from the swarm overlay;
            pre-populate ``seen_poi_ids`` so agents can select them legitimately.
        feedback_kb: Optional pre-composed ambient-context block (the
            current feedback digest). Injected verbatim into the user prompt;
            already bounded and sanitised at distillation time.
        deadline_monotonic: Optional ``time.monotonic()`` value marking the
            race deadline. The ratchet consults it to skip a retry when less
            than :data:`_RETRY_TIME_MARGIN_SECONDS` remain — better to
            surface the best plan so far than let asyncio cancel mid-turn.
        race_id: Race identifier for tool-trace attribution.
        event_emitter: Async SSE emitter. Receives ``tool_call``,
            ``tool_result``, ``thought``, and ``arc`` events for this agent.

    Returns:
        Structured plan dict produced by the agent (may carry ``error`` if
        the run failed — the race runner still surfaces it for diagnostics).

    Raises:
        asyncio.CancelledError: If the race deadline cancels this coroutine.
    """
    budget = {"remaining": TOOL_BUDGET_PER_AGENT}
    # Every POI id the agent has seen from real tool results. Used to catch
    # fabricated POIs before they enter the harness.
    seen_poi_ids: set[str] = set()
    if hot_candidates:
        for p in hot_candidates:
            if isinstance(p, dict) and p.get("id"):
                seen_poi_ids.add(str(p["id"]))

    user_prompt = _spec_to_user_prompt(spec, hot_candidates, feedback_kb)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": agent.system_prompt + "\n\n" + _FINAL_ANSWER_INSTRUCTION,
        },
        {"role": "user", "content": user_prompt},
    ]

    # Ratchet budget: after the agent emits a final plan we score it against
    # the frozen harness. If the aggregate score is below
    # HARNESS_MIN_AGGREGATE the per-dimension scores + failures are fed back
    # and the loop resumes, letting the model either call more tools or
    # rewrite the JSON. Karpathy's autoresearch ratchet, compressed inside
    # one agent's lifecycle.
    ratchet_budget = HARNESS_MAX_RETRIES

    while True:
        try:
            response = await call_llm(
                provider=agent.provider,
                model=agent.model,
                messages=messages,
                tools=list(GRABMAPS_TOOL_SCHEMA) + [_EMIT_THOUGHT_SCHEMA],
                tool_choice="auto",
                temperature=agent.temperature,
                max_tokens=8192,
            )
        except Exception as exc:  # noqa: BLE001
            return _failed_plan(agent, f"llm_error: {type(exc).__name__}: {exc}")

        if not response.tool_calls:
            plan = _parse_plan(response.content, agent, seen_poi_ids)
            effective_retries = ratchet_budget
            if deadline_monotonic is not None:
                time_left = deadline_monotonic - time.monotonic()
                if time_left < _RETRY_TIME_MARGIN_SECONDS:
                    effective_retries = 0
            gated_plan, feedback = await _gate_against_harness(
                plan, spec, effective_retries
            )
            if gated_plan is not None:
                return gated_plan
            ratchet_budget -= 1
            messages.append(
                _format_assistant_message(response.content, response.tool_calls)
            )
            messages.append({"role": "user", "content": feedback or ""})
            continue

        messages.append(
            _format_assistant_message(response.content, response.tool_calls)
        )

        for tool_call in response.tool_calls:
            # emit_thought is instrumentation: emits the thought SSE event,
            # returns {"ok": true}, skips the budget decrement.
            if tool_call.name == "emit_thought":
                note = str(tool_call.arguments.get("note", ""))[:200]
                await _emit(event_emitter, "thought", {"note": note})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "emit_thought",
                        "content": json.dumps({"ok": True}),
                    }
                )
                continue

            if budget["remaining"] <= 0:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(
                            {
                                "error": "BUDGET_EXHAUSTED",
                                "message": "Produce a final plan now with the information you have.",
                            }
                        ),
                    }
                )
                return await _force_final_answer(agent, messages, seen_poi_ids)

            tool_fn = GRABMAPS_TOOLS.get(tool_call.name)
            if tool_fn is None:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(
                            {"error": f"unknown tool: {tool_call.name}"}
                        ),
                    }
                )
                continue

            lat, lng = _extract_coords(tool_call.arguments)
            await _emit(
                event_emitter,
                "tool_call",
                {
                    "tool": tool_call.name,
                    "args": tool_call.arguments,
                    **({"lat": lat} if lat is not None else {}),
                    **({"lng": lng} if lng is not None else {}),
                },
            )

            try:
                result, _trace = await call_tool_with_budget(
                    tool_name=tool_call.name,
                    agent_name=agent.name,
                    tool_fn=tool_fn,
                    tool_args=tool_call.arguments,
                    budget_remaining=budget["remaining"],
                    race_id=race_id,
                    sse_emit=event_emitter,
                )
            except Exception as exc:  # noqa: BLE001
                err_payload = {"error": f"{type(exc).__name__}: {exc}"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(err_payload),
                    }
                )
                await _emit(
                    event_emitter,
                    "tool_result",
                    {
                        "tool": tool_call.name,
                        "summary": f"error: {type(exc).__name__}",
                    },
                )
                # The call_tool_with_budget wrapper decrements the budget on
                # success only; on the exception path the decrement is the
                # caller's responsibility so a failing call still counts
                # against the 40-call ceiling (otherwise a flaky tool could
                # spin forever).
                budget["remaining"] -= 1
                continue

            budget["remaining"] -= 1
            for pid in _extract_poi_ids(result):
                seen_poi_ids.add(pid)

            thumb_url = _extract_thumb_url(tool_call.name, result)
            await _emit(
                event_emitter,
                "tool_result",
                {
                    "tool": tool_call.name,
                    "summary": _summarise_tool_result(tool_call.name, result),
                    **({"lat": lat} if lat is not None else {}),
                    **({"lng": lng} if lng is not None else {}),
                    **({"thumb_url": thumb_url} if thumb_url else {}),
                },
            )

            if tool_call.name in ("route",):
                await _emit_arc_from_route(event_emitter, tool_call.arguments, result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": json.dumps(result, default=str)[
                        :_TOOL_RESULT_MAX_CHARS
                    ],
                }
            )

        if budget["remaining"] <= 0:
            return await _force_final_answer(agent, messages, seen_poi_ids)


# ---------- emit_thought tool schema ----------


_EMIT_THOUGHT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "emit_thought",
        "description": (
            "Narrate a decision to the operator. Use sparingly at key "
            "reasoning moments (e.g. 'choosing Joo Chiat over Geylang Rd "
            "because of an incident'). No budget cost. Returns {'ok': true}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "maxLength": 200},
            },
            "required": ["note"],
        },
    },
}


# ---------- Arc-event helper (route tool side-effect) ----------


async def _emit_arc_from_route(
    emitter: EventEmitter | None,
    args: dict[str, Any],
    result: Any,
) -> None:
    """Emit a single ``arc`` event for a ``route`` tool result.

    Arc payload uses ``[lng, lat]`` order (GeoJSON convention) while the
    tool belt speaks ``[lat, lng]``. The emitter normalises on the way out.
    """
    if emitter is None:
        return
    origin = args.get("origin")
    destination = args.get("destination")
    profile = str(args.get("profile", "walking"))
    duration = 0.0
    if isinstance(result, dict):
        routes = result.get("routes") or []
        if isinstance(routes, list) and routes:
            first = routes[0] if isinstance(routes[0], dict) else {}
            duration = float(
                first.get("duration_s")
                or first.get("duration_seconds")
                or 0.0
            )
    if (
        isinstance(origin, (list, tuple))
        and len(origin) >= 2
        and isinstance(destination, (list, tuple))
        and len(destination) >= 2
    ):
        try:
            arc_from = [float(origin[1]), float(origin[0])]
            arc_to = [float(destination[1]), float(destination[0])]
        except (TypeError, ValueError):
            return
        await _emit(
            emitter,
            "arc",
            {
                "from": arc_from,
                "to": arc_to,
                "mode": profile,
                "duration_s": duration,
            },
        )


# ---------- Assistant-message helper (preserves thought_signature) ----------


def _format_assistant_message(
    content: str,
    tool_calls: list[ToolCallRequest],
) -> dict[str, Any]:
    """Build the assistant-turn message in the internal OpenAI-style format.

    ``provider_metadata`` (e.g. Gemini's ``thought_signature``) is tucked
    inside ``function`` so the Gemini converter can reattach it without a
    new top-level key that OpenAI/Anthropic would reject. The provider
    adapters in :mod:`app.llm_clients` handle the wire translation.
    """
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                    **(
                        {"provider_metadata": tc.provider_metadata}
                        if tc.provider_metadata
                        else {}
                    ),
                },
            }
            for tc in tool_calls
        ]
    return msg


# ---------- Prompt composition ----------


def _spec_to_user_prompt(
    spec: dict[str, Any],
    hot_candidates: list[dict[str, Any]] | None,
    feedback_kb: str | None,
) -> str:
    """Render the spec, hot candidates, and feedback digest into a user message.

    ``feedback_kb`` is a pre-composed ambient-context block produced by
    :func:`app.feedback_kb.format_digest_for_prompt`; it is already bounded
    and sanitised, so we append it verbatim rather than re-stripping.
    """
    parts = [
        f"User query: {spec.get('raw_query', '')}",
        f"Area: {spec.get('area') or 'open'}",
        f"Duration budget: {spec.get('max_duration_minutes')} minutes",
        f"Money budget: SGD {spec.get('max_budget_sgd')}",
        f"Transport: {spec.get('transport_mode')}",
        f"Dietary filter: {spec.get('dietary') or 'none'}",
        f"Mood tags: {', '.join(spec.get('mood_tags') or []) or 'none'}",
        f"Party size: {spec.get('party_size') or 1}",
        f"Accessibility required: {'yes — prefer step-free, wheelchair-friendly venues' if spec.get('accessible') else 'no'}",
    ]
    if hot_candidates:
        hot_names = ", ".join(p.get("name", "?") for p in hot_candidates[:5])
        parts.append(
            f"Hot candidates from the room (plans other teams validated here): {hot_names}. "
            "You may freely ignore these; they are hints, not requirements."
        )
    if feedback_kb and feedback_kb.strip():
        parts.append(feedback_kb.strip())
    return "\n".join(parts)


# ---------- Plan parsing + hallucination guard ----------


def _parse_plan(
    content: str,
    agent: AgentConfig,
    seen_poi_ids: set[str],
) -> dict[str, Any]:
    """Parse the model's final JSON output into a plan dict.

    Rejects the plan entirely if any POI id was not observed in a real
    tool-call result — the structural guard against hallucinated POIs that
    system prompts alone cannot enforce.
    """
    content = _FENCE_RE.sub("", (content or "").strip()).strip()
    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        # Reasoning-heavy models (Opus 4.7 in particular) sometimes emit a
        # short prose lead-in before the JSON despite the "JSON only"
        # instruction. Recover by matching the outermost balanced object.
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return _failed_plan(
                agent, f"unparseable final output: {content[:200]}"
            )
        try:
            plan = json.loads(match.group(0))
        except json.JSONDecodeError:
            return _failed_plan(
                agent, f"unparseable final output: {content[:200]}"
            )

    fabricated = [
        poi.get("name", str(poi.get("id", "?")))
        for poi in plan.get("pois", [])
        if not poi.get("id") or poi.get("id") not in seen_poi_ids
    ]
    if fabricated:
        return _failed_plan(
            agent,
            f"hallucinated POIs not returned by tool calls: {fabricated}",
        )

    plan["agent_name"] = agent.name
    plan["model"] = agent.model
    plan["agent_colour"] = agent.colour
    return plan


# ---------- Harness ratchet ----------


async def _gate_against_harness(
    plan: dict[str, Any],
    spec: dict[str, Any],
    retries_remaining: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Score a candidate plan against the frozen harness and decide whether to ratchet.

    The ratchet is an optimisation loop, not error-retry: the agent gets
    the real per-dimension scores and is asked to improve the weakest
    dimension. If the plan is at or above :data:`HARNESS_MIN_AGGREGATE`
    it is accepted. If retries are exhausted the current plan is surfaced
    even below target — better than nothing.

    Plans carrying ``error`` (parse failure, hallucination guard, LLM
    exception) are passed through untouched — those mean the model is
    confused, not that the plan is off-target. Retrying won't fix confusion
    and we don't want to waste a ratchet cycle on it.

    Args:
        plan: The candidate plan dict.
        spec: The parsed spec dict.
        retries_remaining: How many ratchet cycles are still available.

    Returns:
        ``(plan_to_return_now, None)`` if the plan is accepted, or
        ``(None, feedback)`` if the caller should ratchet another pass.
    """
    if plan.get("error"):
        return plan, None

    try:
        scored = score_plan(plan, spec, streetview_urls_by_poi=None)
    except NotImplementedError:
        # Phase 1 has not filled in the harness body yet (shards run in
        # parallel). Surface the plan unchanged rather than force a ratchet
        # against an unscored plan — Phase 7 integration reconciles.
        return plan, None
    except Exception as exc:  # noqa: BLE001
        plan["harness_error"] = f"{type(exc).__name__}: {exc}"
        return plan, None

    hard_pass = bool(scored.get("hard_pass", True))
    failures = scored.get("failures") or []
    soft_scores = scored.get("soft_scores") or {}
    aggregate = float(scored.get("total_score", 0.0))

    # Cache the scores on the plan so the final ranker does not re-score.
    plan["hard_pass"] = hard_pass
    plan["failures"] = failures
    plan["soft_scores"] = soft_scores
    plan["total_score"] = aggregate

    if not hard_pass:
        if retries_remaining <= 0:
            return plan, None
        failure_list = "; ".join(str(f) for f in failures) or "unspecified"
        feedback = (
            "The frozen harness rejected your plan for the following hard-rule failures:\n"
            f"  - {failure_list}\n"
            "A plan that fails any hard rule is disqualified and scores zero. "
            "Revise the JSON fixing each failure specifically. You may call more "
            "tools (route / nearby_search for reachability, places_search to "
            "replace a closed POI). Final answer must be plan JSON only."
        )
        return None, feedback

    if aggregate >= HARNESS_MIN_AGGREGATE or retries_remaining <= 0:
        return plan, None

    if soft_scores:
        weakest = min(soft_scores.items(), key=lambda p: p[1])
        weights_line = ", ".join(
            f"{k}={HARNESS_WEIGHTS.get(k, 0):.2f}" for k in soft_scores
        )
        breakdown = "\n".join(
            f"  - {k} {v:.2f}" for k, v in soft_scores.items()
        )
        feedback = (
            f"Your plan passed hard rules but scored {aggregate:.2f} against "
            f"the {HARNESS_MIN_AGGREGATE:.2f} target.\n"
            f"Dimension breakdown (weights: {weights_line}):\n"
            f"{breakdown}\n"
            f"Weakest dimension: '{weakest[0]}' at {weakest[1]:.2f}. Revise "
            "the plan to improve it specifically — you may call more tools "
            "(get_street_view strengthens vibe; route_matrix strengthens flow; "
            "nearby_search across categories strengthens diversity). Final "
            "answer must be plan JSON only."
        )
    else:
        feedback = (
            f"Your plan passed hard rules but scored {aggregate:.2f} against "
            f"the {HARNESS_MIN_AGGREGATE:.2f} target. Revise the plan to "
            "improve the soft scores. Final answer must be plan JSON only."
        )
    return None, feedback


# ---------- Final-answer + failed-plan helpers ----------


async def _force_final_answer(
    agent: AgentConfig,
    messages: list[dict[str, Any]],
    seen_poi_ids: set[str],
) -> dict[str, Any]:
    """Prompt the agent one last time for a plan after budget or tool error.

    Passes ``tools`` with ``tool_choice="none"`` so the call is API-compliant
    for providers (Anthropic) that require the schema to stay present while
    still preventing further tool invocations.
    """
    messages.append(
        {
            "role": "user",
            "content": (
                "Produce the final plan JSON now with the information you "
                "already have. No more tool calls."
            ),
        }
    )
    try:
        response = await call_llm(
            provider=agent.provider,
            model=agent.model,
            messages=messages,
            tools=list(GRABMAPS_TOOL_SCHEMA) + [_EMIT_THOUGHT_SCHEMA],
            tool_choice="none",
            temperature=agent.temperature,
            max_tokens=8192,
        )
        return _parse_plan(response.content, agent, seen_poi_ids)
    except Exception as exc:  # noqa: BLE001
        return _failed_plan(
            agent, f"force_final_error: {type(exc).__name__}: {exc}"
        )


def _failed_plan(agent: AgentConfig, reason: str) -> dict[str, Any]:
    """Produce a placeholder plan for failed runs so the harness gates cleanly."""
    return {
        "agent_name": agent.name,
        "model": agent.model,
        "agent_colour": agent.colour,
        "pois": [],
        "legs": [],
        "total_minutes": 0,
        "total_cost_sgd": 0,
        "narrative": f"[agent failed: {reason}]",
        "error": reason,
    }


__all__ = [
    "AgentConfig",
    "EventEmitter",
    "run_agent",
]
