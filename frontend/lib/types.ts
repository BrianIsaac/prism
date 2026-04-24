// Types mirroring the FastAPI Pydantic models in backend/app/models.py.
// Hand-maintained for now; switch to openapi-typescript if the schema grows.

// Phase 7 alignment: v1's coarse union is superseded by the five GrabMaps
// route profiles (docs/grabmaps_api_reference.md §Routing). The legacy strings
// stay in the union for past-race prefill compatibility until the next build
// cycle retires them.
export type TransportMode =
  | "driving"
  | "motorcycle"
  | "tricycle"
  | "cycling"
  | "walking"
  | "walk"
  | "drive"
  | "transit"
  | "cycle";

export type DietaryFilter = "halal" | "vegetarian" | "vegan";

// Per-agent colour token. All three agents share one prompt, one tool belt,
// and one harness — the only runtime variable is the model. Colour identifies
// which racer produced a given frame on the Live Canvas.
export type AgentColour = "red" | "green" | "blue";

// ---------- POIs + plan pieces ----------

export interface POI {
  id: string;
  name: string;
  category: string;
  subcategory?: string | null;
  lat: number;
  lng: number;
  address?: string | null;
  description?: string | null;
  price_tier?: number;
  avg_cost_sgd?: number;
  dietary_tags?: string[];
  // GrabMaps serves this as a JSON-encoded string per the reference; the
  // backend parses it into a list. Kept as unknown[] so the frontend does
  // not need to re-validate.
  opening_hours?: unknown[];
  imagery_url?: string | null;
  tags?: string[];
  last_updated?: string | null;
}

// OpenStreetCam photo attached to a POI for vibe judging + plan detail gallery.
// Mirrors backend `StreetviewPhoto`.
export interface StreetviewPhoto {
  url: string;
  thumb_url?: string | null;
  heading?: number | null;
  projection: "PLANE" | "SPHERE";
}

export interface VisitedPOI extends POI {
  visit_window?: [string, string] | null;
  dwell_minutes?: number;
  is_food?: boolean;
}

// Plan-bound POI (v2): carries the street-view photo array that the vibe judge
// scored. Phase 4's Live Canvas and Phase 5's plan detail both read from this.
export interface PlanPOI extends VisitedPOI {
  streetview_photos?: StreetviewPhoto[] | null;
}

export interface Leg {
  from: string;
  to: string;
  mode: TransportMode;
  duration_minutes: number;
  distance_metres: number;
  unreachable?: boolean;
}

// Plan-bound leg (v2): carries the traffic snapshot id + fare amount so the
// harness money rule prices routes off real fares.
export interface PlanLeg extends Leg {
  traffic_snapshot_id?: string | null;
  fee_amount?: number;
}

// ---------- Plans, races, specs ----------

export interface Plan {
  id?: string;
  agent_name: string;
  model: string;
  pois: PlanPOI[];
  legs: PlanLeg[];
  total_minutes: number;
  total_cost_sgd: number;
  narrative: string;
  hard_pass?: boolean;
  soft_scores?: { flow: number; diversity: number; vibe: number } | null;
  total_score?: number;
  rank?: number | null;
  failures?: string[];
  tool_call_count?: number;
  error?: string;
}

export interface Spec {
  raw_query: string;
  area?: string | null;
  city: string;
  country_iso3: string;
  max_duration_minutes: number;
  max_budget_sgd: number;
  transport_mode: TransportMode;
  dietary?: string | null;
  mood_tags: string[];
  start_anchor?: Record<string, unknown> | null;
  end_anchor?: Record<string, unknown> | null;
  start_time_iso?: string | null;
  party_size: number;
  accessible: boolean;
}

export interface SpecOverride {
  area?: string | null;
  max_duration_minutes?: number;
  max_budget_sgd?: number;
  transport_mode?: TransportMode;
  dietary?: DietaryFilter | null;
  mood_tags?: string[];
  start_time_iso?: string | null;
  party_size?: number;
  accessible?: boolean;
}

// Handshake returned by POST /race. Open `stream_url` via openRaceStream().
export interface RaceStartResponse {
  race_id: string;
  stream_url: string;
}

export interface RaceResult {
  race_id: string;
  spec: Spec;
  harness_version: string;
  harness_weights: Record<string, number>;
  plans: Plan[];
  duration_seconds: number;
  hot_candidates_used: number;
}

// ---------- SSE event union ----------

interface RaceStreamEventBase {
  agent?: string | null;
  t_ms: number;
}

export interface ToolCallEvent extends RaceStreamEventBase {
  type: "tool_call";
  payload: { tool: string; input: Record<string, unknown> };
}

export interface ToolResultEvent extends RaceStreamEventBase {
  type: "tool_result";
  payload: {
    tool: string;
    status: "ok" | "error";
    latency_ms: number;
    output?: unknown;
    error?: string | null;
  };
}

// Free-form agent thought emitted via emit_thought. Not counted in the tool
// budget and not registered in the agent-callable tool belt.
export interface ThoughtEvent extends RaceStreamEventBase {
  type: "thought";
  payload: { text: string };
}

export interface ArcEvent extends RaceStreamEventBase {
  type: "arc";
  payload: { text: string };
}

export interface PlanResolvedEvent extends RaceStreamEventBase {
  type: "plan_resolved";
  payload: Plan;
}

export interface RaceCompleteEvent extends RaceStreamEventBase {
  type: "race_complete";
  payload: { duration_seconds: number; ranked_plan_ids: string[] };
}

export interface RaceErrorEvent extends RaceStreamEventBase {
  type: "error";
  payload: { message: string };
}

export type RaceStreamEvent =
  | ToolCallEvent
  | ToolResultEvent
  | ThoughtEvent
  | ArcEvent
  | PlanResolvedEvent
  | RaceCompleteEvent
  | RaceErrorEvent;

// ---------- Admin + live feed ----------

export type LiveFeedCategory =
  | "search"
  | "routing"
  | "traffic"
  | "incidents"
  | "streetview"
  | "other";

export type RaceAgentName = "opus" | "gpt" | "gemini" | "other";

export interface LiveFeedCounts {
  by_category: Record<LiveFeedCategory, number>;
  by_agent?: Record<RaceAgentName, number>;
  by_agent_category?: Record<RaceAgentName, Record<LiveFeedCategory, number>>;
  total_calls: number;
  active_agents?: number;
  per_agent_average?: number;
  window_seconds?: number;
}

// ---------- Validated plans, feedback, alternatives ----------

export interface ValidatedPlan {
  id: string;
  plan_id: string;
  race_id?: string | null;
  country_iso3: string;
  anchor_lat: number | null;
  anchor_lng: number | null;
  hitl_rating: {
    novelty: number;
    efficiency: number;
    vibe: number;
    comment?: string | null;
  };
  pois_override?: POI[] | null;
  plan?: Plan;
  total_score?: number;
  agent_name?: string;
  likes?: number;
  created_at: string;
  // True for rank-1 plans from recent races that no-one has HITL-rated yet.
  // The first like/feedback materialises a real validated_plans row via the
  // backend's materialise_auto_pinned helper and the flag flips to false.
  auto_pinned?: boolean;
}

export interface Feedback {
  id: number;
  created_at: string;
  validated_id: string | null;
  plan_id: string;
  question: string;
  response: string;
  sentiment: "positive" | "neutral" | "negative";
}

export interface FeedbackTag {
  tag: string;
  count: number;
}

export interface FeedbackDigest {
  id?: number;
  scope: string;
  summary: string;
  tags: FeedbackTag[];
  source_count: number;
  model: string;
  created_at?: string;
}

export interface FeedbackDigestResponse {
  digest: FeedbackDigest | null;
  history: FeedbackDigest[];
  raw_tail: Feedback[];
}

export interface FeedbackInput {
  plan_id: string;
  validated_id?: string | null;
  question?: string;
  response: string;
  sentiment?: "positive" | "neutral" | "negative";
}

export interface PastRace {
  race_id: string;
  created_at: string;
  user_query: string;
  spec: Partial<Spec>;
  duration_seconds: number;
  status: string;
  top_plan: (Plan & { id?: string; rank?: number | null }) | null;
}

// A single racer's plan within a race (see /race/{race_id}/plans).
export interface RacePlan {
  plan_id: string;
  race_id: string;
  agent_name: string;
  model?: string | null;
  plan: Plan;
  hard_pass: number;
  soft_scores?: { flow: number; diversity: number; vibe: number } | null;
  total_score?: number | null;
  rank?: number | null;
  country_iso3?: string | null;
  created_at?: string;
}

export interface RacePlansResponse {
  race_id: string;
  plans: RacePlan[];
  count: number;
}

export interface Rating {
  novelty: number;
  efficiency: number;
  vibe: number;
  comment?: string;
  pois_override?: POI[];
}

export interface WeightsResponse {
  harness_version: string;
  frozen_defaults: Record<string, number>;
  runtime: Record<string, number>;
}

export interface WeightHistorySnapshot {
  id: number;
  created_at: string;
  flow: number;
  diversity: number;
  vibe: number;
}

export interface WeightHistoryResponse {
  snapshots: WeightHistorySnapshot[];
  count: number;
}

export interface Alternative {
  id: string;
  name: string;
  category: string;
  subcategory?: string | null;
  lat: number;
  lng: number;
  description?: string;
  price_tier?: number;
  avg_cost_sgd?: number;
  dietary_tags?: string[];
  tags?: string[];
  streetview_photos?: StreetviewPhoto[];
}

export interface AlternativesResponse {
  alternatives: Alternative[];
  count: number;
}

export interface BugReportSample {
  id?: string;
  race_id?: string;
  tool_name: string;
  agent_name: string;
  input?: string | null;
  output?: string | null;
  status: string;
  error?: string | null;
  latency_ms: number;
  created_at?: string;
}

export interface BugReport {
  generated_at: string;
  total_calls: number;
  failed_calls: number;
  failures_by_tool: Record<string, number>;
  failures_by_status: Record<string, number>;
  samples: BugReportSample[];
  markdown?: string;
}
