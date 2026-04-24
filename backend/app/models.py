"""Pydantic models for Prism's domain objects (v2).

Changes from v1:
    - ``PlanPOI`` carries an optional ``streetview_photos`` list so the vibe judge
      and plan-detail gallery can share the same payload.
    - ``PlanLeg`` carries ``traffic_snapshot_id`` and ``fee_amount`` so the harness
      money rule can price routes off live fare estimates.
    - ``StreetviewPhoto`` and ``RaceStreamEvent`` are new types introduced by the
      SSE race endpoint.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TransportMode = Literal[
    "driving",
    "motorcycle",
    "tricycle",
    "cycling",
    "walking",
    "walk",
    "drive",
    "transit",
    "cycle",
]


class POI(BaseModel):
    """A point of interest, independent of any plan."""

    id: str
    name: str
    category: str
    subcategory: str | None = None
    lat: float
    lng: float
    address: str | None = None
    description: str | None = None
    price_tier: int = Field(ge=1, le=4, default=2)
    avg_cost_sgd: float = 0.0
    dietary_tags: list[str] = Field(default_factory=list)
    # GrabMaps serves this as a JSON-encoded string; the harness parses it
    # lazily. Keeping the type as list[dict] here means a pre-parsed caller
    # can still validate.
    opening_hours: list[dict[str, Any]] = Field(default_factory=list)
    imagery_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    last_updated: str | None = None


class StreetviewPhoto(BaseModel):
    """An OpenStreetCam photo attached to a POI for vibe judging + plan detail.

    ``projection`` discriminates between standard rectilinear (``PLANE``) and
    equirectangular 360 (``SPHERE``) panoramas; the gallery renders the two
    cases with different viewers.
    """

    url: str
    thumb_url: str | None = None
    heading: float | None = None
    projection: Literal["PLANE", "SPHERE"] = "PLANE"


class Leg(BaseModel):
    """A single routing leg between two POIs."""

    model_config = ConfigDict(populate_by_name=True)

    from_poi: str = Field(alias="from")
    to_poi: str = Field(alias="to")
    mode: TransportMode = "walk"
    duration_minutes: float
    distance_metres: float
    unreachable: bool = False


class PlanLeg(Leg):
    """A plan-bound leg with v2 traffic + fare attribution.

    ``traffic_snapshot_id`` links the leg to the traffic snapshot that produced
    its duration estimate; ``fee_amount`` is the SGD fare for the mode (0 for
    walking, GrabCar / GrabBike fare estimate otherwise).
    """

    traffic_snapshot_id: str | None = None
    fee_amount: float = 0.0


class VisitedPOI(POI):
    """A POI scheduled within a plan with a visit window and dwell time."""

    visit_window: tuple[str, str] | None = None
    dwell_minutes: int = 30
    is_food: bool = False


class PlanPOI(VisitedPOI):
    """A POI in a plan with attached street-view photos for vibe judging.

    The street-view URLs are cached per (lat_round, lng_round, day_bucket) in
    ``streetview_cache`` — :func:`app.tools.live.get_street_view` populates this
    list before the harness scores the plan.
    """

    streetview_photos: list[StreetviewPhoto] | None = None


class Plan(BaseModel):
    """A full itinerary produced by one agent."""

    agent_name: str
    model: str
    pois: list[PlanPOI]
    legs: list[PlanLeg]
    total_minutes: float
    total_cost_sgd: float
    narrative: str = ""


class Spec(BaseModel):
    """Parsed user query as structured constraints."""

    raw_query: str
    area: str | None = None
    city: str = "Singapore"
    country_iso3: str = "SGP"
    max_duration_minutes: int = 240
    max_budget_sgd: float = 50.0
    transport_mode: TransportMode = "walk"
    dietary: str | None = None
    mood_tags: list[str] = Field(default_factory=list)
    start_anchor: dict[str, Any] | None = None
    end_anchor: dict[str, Any] | None = None
    start_time_iso: str | None = None
    party_size: int = Field(default=1, ge=1, le=20)
    accessible: bool = False


class Rating(BaseModel):
    """HITL rating submitted by the user after picking a plan."""

    plan_id: str
    novelty: int = Field(ge=1, le=5)
    efficiency: int = Field(ge=1, le=5)
    vibe: int = Field(ge=1, le=5)
    comment: str | None = None
    pois_override: list[dict[str, Any]] | None = None


class SpecOverride(BaseModel):
    """Strict-typed subset of :class:`Spec` fields the client is allowed to override.

    Only these fields pass through the /race merge — any other key triggers a
    422 via Pydantic's ``extra="forbid"``. ``country_iso3``, ``city``, and the
    anchor fields are deliberately excluded so a caller cannot redirect the
    hot-candidate lookup or inject unrelated anchors.
    """

    model_config = ConfigDict(extra="forbid")

    area: str | None = Field(None, max_length=120)
    max_duration_minutes: int | None = Field(None, ge=30, le=720)
    max_budget_sgd: float | None = Field(None, ge=1, le=10_000)
    transport_mode: TransportMode | None = None
    dietary: Literal["halal", "vegetarian", "vegan"] | None = None
    mood_tags: list[str] | None = Field(None, max_length=12)
    start_time_iso: str | None = Field(None, max_length=64)
    party_size: int | None = Field(None, ge=1, le=20)
    accessible: bool | None = None

    @field_validator("mood_tags")
    @classmethod
    def _mood_tag_length(cls, value: list[str] | None) -> list[str] | None:
        """Bound each individual tag so a caller cannot pass a 50 kB string."""
        if value is None:
            return None
        for tag in value:
            if len(tag) > 40:
                raise ValueError("mood_tag too long (max 40 chars)")
        return value


class RaceRequest(BaseModel):
    """HTTP payload for ``POST /race``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    spec_override: SpecOverride | None = None


class FeedbackRequest(BaseModel):
    """HTTP payload for ``POST /feedback``."""

    plan_id: str = Field(min_length=1, max_length=64)
    validated_id: str | None = Field(None, max_length=64)
    question: str = Field("What did you enjoy about this trip?", max_length=240)
    response: str = Field(min_length=1, max_length=2000)
    sentiment: Literal["positive", "neutral", "negative"] = "positive"


class RaceStartResponse(BaseModel):
    """Handshake returned by ``POST /race``.

    The race runs asynchronously; the caller opens an ``EventSource`` against
    ``stream_url`` (or polls ``/race/{id}/events?since=`` as a fallback) to
    consume :class:`RaceStreamEvent` payloads as they are produced.
    """

    race_id: str
    stream_url: str


class RaceStreamEvent(BaseModel):
    """A single SSE frame produced during a race.

    ``type`` discriminates the payload shape:
        - ``tool_call`` / ``tool_result``: tool-belt instrumentation
        - ``thought``: a free-form agent thought emitted via ``emit_thought``
        - ``arc``: the agent arc overlay (streaming narrative)
        - ``plan_resolved``: a scored plan is ready
        - ``race_complete``: terminal frame, followed by stream close
        - ``error``: a fatal error for the race or a single agent
    """

    type: Literal[
        "tool_call",
        "tool_result",
        "thought",
        "arc",
        "plan_resolved",
        "race_complete",
        "error",
    ]
    agent: str | None = None
    t_ms: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class RaceResult(BaseModel):
    """Final scored plans for a race (persisted; returned by fallback polling)."""

    race_id: str
    spec: Spec
    harness_version: str
    harness_weights: dict[str, float]
    plans: list[dict[str, Any]]
    duration_seconds: float
    hot_candidates_used: int = 0
