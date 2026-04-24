// Ensure the EventSource polyfill is registered before any native lookup.
// Needed for older Safari + anywhere EventSource is feature-detected before
// the race page mounts. The polyfill is idempotent: if the native constructor
// already exists, the import is a no-op.
import "eventsource-polyfill";

import type {
  AlternativesResponse,
  BugReport,
  FeedbackDigest,
  FeedbackDigestResponse,
  FeedbackInput,
  LiveFeedCounts,
  PastRace,
  RacePlansResponse,
  RaceStartResponse,
  RaceStreamEvent,
  Rating,
  SpecOverride,
  ValidatedPlan,
  WeightHistoryResponse,
  WeightsResponse,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

// ---------- Race handshake + SSE ----------

export async function startRace(
  query: string,
  specOverride?: SpecOverride,
): Promise<RaceStartResponse> {
  const body: Record<string, unknown> = { query };
  if (specOverride) body.spec_override = specOverride;
  const res = await fetch(`${API_BASE}/race`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return json<RaceStartResponse>(res);
}

/**
 * Open the SSE stream for a running race and forward every frame to `onEvent`.
 * Returns a cleanup function that closes the underlying EventSource; callers
 * should invoke it in a useEffect teardown so tab-switches do not leak sockets.
 */
export function openRaceStream(
  race_id: string,
  onEvent: (event: RaceStreamEvent) => void,
  onError?: (error: Event) => void,
): () => void {
  const url = `${API_BASE}/race/${encodeURIComponent(race_id)}/stream`;
  const source = new EventSource(url);
  source.onmessage = (raw) => {
    try {
      const parsed = JSON.parse(raw.data) as RaceStreamEvent;
      onEvent(parsed);
    } catch {
      // Malformed frames are dropped silently; Phase 2 enforces JSON on the
      // emit side, so any parse failure is a genuine upstream bug worth
      // surfacing via the browser console rather than erroring the stream.
      console.warn("openRaceStream: dropped malformed frame", raw.data);
    }
  };
  if (onError) {
    source.onerror = onError;
  }
  return () => source.close();
}

/**
 * Fallback polling path. Returns every event with `index >= since`; the caller
 * is responsible for advancing `since` on each tick.
 */
export async function pollRaceEvents(
  race_id: string,
  since: number = 0,
): Promise<{ race_id: string; since: number; events: RaceStreamEvent[] }> {
  const res = await fetch(
    `${API_BASE}/race/${encodeURIComponent(race_id)}/events?since=${since}`,
  );
  return json<{ race_id: string; since: number; events: RaceStreamEvent[] }>(
    res,
  );
}

// ---------- HITL rating + feedback ----------

export async function ratePlan(
  plan_id: string,
  rating: Rating,
): Promise<{ ok: boolean; weights: Record<string, number> }> {
  const res = await fetch(`${API_BASE}/rating`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_id, ...rating }),
  });
  return json<{ ok: boolean; weights: Record<string, number> }>(res);
}

export async function submitFeedback(
  input: FeedbackInput,
): Promise<{ ok: boolean; feedback_id: number; likes: number | null }> {
  const res = await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return json<{ ok: boolean; feedback_id: number; likes: number | null }>(res);
}

// ---------- Validated plans ----------

export async function fetchValidated(
  countryIso3: string | null = null,
  limit: number = 100,
): Promise<ValidatedPlan[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (countryIso3) params.set("country_iso3", countryIso3);
  const res = await fetch(`${API_BASE}/validated?${params}`);
  const data = await json<{ validated_plans: ValidatedPlan[] }>(res);
  return data.validated_plans;
}

export async function likeValidated(
  validatedId: string,
): Promise<{ ok: boolean; likes: number; validated_id: string }> {
  const res = await fetch(
    `${API_BASE}/validated/${encodeURIComponent(validatedId)}/like`,
    { method: "POST" },
  );
  return json<{ ok: boolean; likes: number; validated_id: string }>(res);
}

// ---------- Alternatives (stop-swap) ----------

export async function fetchAlternatives(
  category: string,
  near: { lat: number; lng: number },
  excludeIds: string[] = [],
  limit: number = 5,
): Promise<AlternativesResponse> {
  const params = new URLSearchParams({
    category,
    near_lat: near.lat.toString(),
    near_lng: near.lng.toString(),
    limit: limit.toString(),
  });
  if (excludeIds.length) params.set("exclude", excludeIds.join(","));
  const res = await fetch(`${API_BASE}/alternatives?${params.toString()}`);
  return json<AlternativesResponse>(res);
}

// ---------- Past races ----------

export async function fetchPastRaces(limit: number = 20): Promise<PastRace[]> {
  const res = await fetch(`${API_BASE}/races?limit=${limit}`);
  const data = await json<{ races: PastRace[] }>(res);
  return data.races;
}

/**
 * All three racers' plans for a given race. Used by Explore to overlay
 * every agent's itinerary on the MapLibre canvas so the per-agent
 * divergence is visible.
 */
export async function fetchRacePlans(
  race_id: string,
): Promise<RacePlansResponse> {
  const res = await fetch(
    `${API_BASE}/race/${encodeURIComponent(race_id)}/plans`,
  );
  return json<RacePlansResponse>(res);
}

// ---------- Admin ----------

export async function fetchWeights(): Promise<WeightsResponse> {
  const res = await fetch(`${API_BASE}/admin/weights`);
  return json<WeightsResponse>(res);
}

export async function fetchWeightHistory(
  limit: number = 200,
): Promise<WeightHistoryResponse> {
  const res = await fetch(`${API_BASE}/admin/weight-history?limit=${limit}`);
  return json<WeightHistoryResponse>(res);
}

export async function fetchBugReport(): Promise<BugReport> {
  const res = await fetch(`${API_BASE}/admin/bug-report`);
  return json<BugReport>(res);
}

export async function fetchFeedbackDigest(): Promise<FeedbackDigestResponse> {
  const res = await fetch(`${API_BASE}/admin/feedback-digest`);
  return json<FeedbackDigestResponse>(res);
}

export async function rebuildFeedbackDigest(): Promise<{
  ok: boolean;
  digest: FeedbackDigest;
}> {
  const res = await fetch(`${API_BASE}/admin/feedback-digest/rebuild`, {
    method: "POST",
  });
  return json<{ ok: boolean; digest: FeedbackDigest }>(res);
}

export async function fetchLiveFeed(
  windowSeconds: number = 60,
): Promise<LiveFeedCounts> {
  const res = await fetch(
    `${API_BASE}/admin/live-feed?window_seconds=${windowSeconds}`,
  );
  return json<LiveFeedCounts>(res);
}

// ---------- Trace ----------

export async function fetchTrace(
  race_id: string,
): Promise<{ race_id: string; traces: unknown[] }> {
  const res = await fetch(
    `${API_BASE}/trace/${encodeURIComponent(race_id)}`,
  );
  return json<{ race_id: string; traces: unknown[] }>(res);
}
