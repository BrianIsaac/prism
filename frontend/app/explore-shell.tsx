"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  fetchRacePlans,
  fetchValidated,
  likeValidated,
  submitFeedback,
} from "@/lib/api-client";
import type { RacePlan, ValidatedPlan } from "@/lib/types";
import {
  PrismGlobe,
  type GlobeArc,
  type GlobePoint,
  type GlobeRing,
} from "@/components/globe";
import { LiveCanvas, useLiveCanvas } from "@/components/live-canvas";
import { TopRoutesList } from "@/components/top-routes-list";
import { WordReveal } from "@/components/word-reveal";
import {
  FeedbackDrawer,
  type FeedbackSentiment,
  type FeedbackStage,
} from "@/components/feedback-drawer";

const POLL_INITIAL_MS = 3_000;
const POLL_MAX_MS = 30_000;
// The globe's camera tween is 1400 ms (see PrismGlobe). Wait for it to
// settle before swapping to MapLibre so the viewer feels one continuous
// zoom rather than a hard cut mid-flight.
const GLOBE_TO_MAP_MS = 1400;

// Per-agent colour — matches the admin "By agent" panel and the agent-race
// overlay so the same mental mapping (red=opus, green=gpt, blue=gemini)
// holds across every surface.
const AGENT_COLOURS: Record<string, string> = {
  opus: "#ef4444",
  gpt: "#00b14f",
  gemini: "#60a5fa",
};
const AGENT_FALLBACK_COLOUR = "#00b14f";

const NO_RINGS: GlobeRing[] = [];
const SINGAPORE: [number, number] = [103.8198, 1.3521];

interface AgentRoute {
  agentName: string;
  colour: string;
  pois: ReadonlyArray<{ id: string; name: string; lat: number; lng: number }>;
}

interface RoutePinsProps {
  route: AgentRoute | null;
}

/**
 * MapLibre overlay for a single agent's route: numbered pins connected by
 * curved SVG arcs, coloured by the owning agent (opus=red, gpt=green,
 * gemini=blue). Rendered only when the user picks a specific entry in the
 * sidebar; the sibling agents' paths stay hidden so the view stays focused
 * on the chosen plan.
 */
function RoutePins({ route }: RoutePinsProps) {
  const { map, ready } = useLiveCanvas();
  const [size, setSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  const [tick, setTick] = useState<number>(0);

  useEffect(() => {
    if (!map || !ready) return;
    const onMove = (): void => setTick((t) => t + 1);
    const onResize = (): void => {
      const c = map.getContainer();
      setSize({ width: c.clientWidth, height: c.clientHeight });
      setTick((t) => t + 1);
    };
    onResize();
    map.on("move", onMove);
    map.on("resize", onResize);
    return () => {
      map.off("move", onMove);
      map.off("resize", onResize);
    };
  }, [map, ready]);

  const pois = route?.pois ?? [];

  useEffect(() => {
    if (!map || !ready) return;
    if (pois.length === 0) {
      map.flyTo({ center: SINGAPORE, zoom: 12, duration: 800 });
      return;
    }
    let minLat = pois[0].lat;
    let maxLat = pois[0].lat;
    let minLng = pois[0].lng;
    let maxLng = pois[0].lng;
    for (const p of pois) {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lng < minLng) minLng = p.lng;
      if (p.lng > maxLng) maxLng = p.lng;
    }
    map.fitBounds(
      [
        [minLng, minLat],
        [maxLng, maxLat],
      ],
      { padding: 120, duration: 800, maxZoom: 16 },
    );
  }, [map, ready, pois]);

  if (!map || !ready || !route) return null;
  if (size.width === 0 || size.height === 0) return null;
  if (pois.length === 0) return null;
  void tick;

  const points = pois.map((p) => map.project([p.lng, p.lat]));
  const arcs: string[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2 - Math.hypot(b.x - a.x, b.y - a.y) * 0.18;
    arcs.push(`M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`);
  }

  return (
    <svg
      role="presentation"
      aria-hidden="true"
      width={size.width}
      height={size.height}
      viewBox={`0 0 ${size.width} ${size.height}`}
      className="absolute inset-0 pointer-events-none z-10"
    >
      {arcs.map((d, i) => (
        <path
          key={i}
          d={d}
          fill="none"
          stroke={route.colour}
          strokeWidth={3.2}
          opacity={0.96}
          style={{ filter: `drop-shadow(0 0 8px ${route.colour}aa)` }}
        />
      ))}
      {points.map((p, i) => (
        <g key={i}>
          <circle
            cx={p.x}
            cy={p.y}
            r={12}
            fill="rgba(0,0,0,0.75)"
            stroke={route.colour}
            strokeWidth={2}
          />
          <circle cx={p.x} cy={p.y} r={5} fill={route.colour} />
          <text
            x={p.x + 14}
            y={p.y + 4}
            fontSize={11}
            fontFamily="ui-monospace, monospace"
            fill="rgba(255,255,255,0.95)"
            stroke="rgba(0,0,0,0.85)"
            strokeWidth={2.5}
            paintOrder="stroke"
          >
            {i + 1} · {pois[i].name}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function ExploreShell() {
  const [validated, setValidated] = useState<ValidatedPlan[]>([]);
  const [initialLoaded, setInitialLoaded] = useState<boolean>(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [countryFilter, setCountryFilter] = useState<string | null>(null);
  // Fires after the globe's flyTo animation settles so we swap from globe
  // to MapLibre one continuous zoom rather than mid-flight. Resetting to
  // false on deselect flips back to the globe.
  const [mapMode, setMapMode] = useState<boolean>(false);
  // Sibling plans (all three agents) for the currently selected race. Fetched
  // once per race_id so we can overlay every agent's route on the map.
  const [racePlans, setRacePlans] = useState<RacePlan[]>([]);
  const [feedbackStage, setFeedbackStage] = useState<FeedbackStage>("ask");
  const [feedbackText, setFeedbackText] = useState<string>("");
  const [feedbackSentiment, setFeedbackSentiment] =
    useState<FeedbackSentiment>("positive");
  const [feedbackSubmitting, setFeedbackSubmitting] =
    useState<boolean>(false);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let delay = POLL_INITIAL_MS;
    let timer: number | null = null;

    const tick = async (): Promise<void> => {
      try {
        const data = await fetchValidated(countryFilter, 100);
        if (cancelled || !mountedRef.current) return;
        setValidated(data);
        setPollError(null);
        setInitialLoaded(true);
        delay = POLL_INITIAL_MS;
      } catch (err) {
        if (cancelled || !mountedRef.current) return;
        setPollError(
          err instanceof Error ? err.message : "validated poll failed",
        );
        delay = Math.min(POLL_MAX_MS, delay * 2);
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(tick, delay);
        }
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [countryFilter]);

  // Most-liked first, then rating desc, then newest; matches the user's
  // expectation that Explore surfaces the community's favourite routes at
  // the top of the sidebar.
  const sortedValidated = useMemo(() => {
    const copy = [...validated];
    copy.sort((a, b) => {
      const likeDelta = (b.likes ?? 0) - (a.likes ?? 0);
      if (likeDelta !== 0) return likeDelta;
      const ratingA =
        ((a.hitl_rating?.novelty ?? 0) +
          (a.hitl_rating?.efficiency ?? 0) +
          (a.hitl_rating?.vibe ?? 0)) /
        3;
      const ratingB =
        ((b.hitl_rating?.novelty ?? 0) +
          (b.hitl_rating?.efficiency ?? 0) +
          (b.hitl_rating?.vibe ?? 0)) /
        3;
      if (ratingA !== ratingB) return ratingB - ratingA;
      return (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });
    return copy;
  }, [validated]);

  const selected = useMemo(
    () => sortedValidated.find((v) => v.id === selectedId) ?? null,
    [sortedValidated, selectedId],
  );

  // Fetch every racer's plan whenever the selected race_id changes so the
  // MapLibre overlay renders all three agents' routes. Stale responses for
  // a since-abandoned race are ignored.
  useEffect(() => {
    const raceId = selected?.race_id ?? null;
    if (!raceId) {
      setRacePlans([]);
      return;
    }
    let cancelled = false;
    fetchRacePlans(raceId)
      .then((resp) => {
        if (cancelled || !mountedRef.current) return;
        setRacePlans(resp.plans);
      })
      .catch(() => {
        if (!cancelled && mountedRef.current) setRacePlans([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.race_id]);

  // Only the selected row's agent plan draws on the map. We still fetch
  // siblings via /race/{id}/plans so we know each row maps to a distinct
  // route (and so the user can hop between agents by clicking their rows
  // in the sidebar), but we never overlay more than one at a time.
  const agentRoute = useMemo<AgentRoute | null>(() => {
    if (!selected) return null;
    const agent = (selected.agent_name ?? "").toLowerCase();
    const colour = AGENT_COLOURS[agent] ?? AGENT_FALLBACK_COLOUR;
    const match = racePlans.find(
      (rp) => (rp.agent_name || "").toLowerCase() === agent,
    );
    const plan = match?.plan ?? selected.plan;
    if (!plan) return null;
    const pois = (plan.pois ?? [])
      .filter(
        (p) => Number.isFinite(p.lat) && Number.isFinite(p.lng) && Boolean(p.id),
      )
      .map((p) => ({
        id: p.id,
        name: p.name || p.id,
        lat: p.lat,
        lng: p.lng,
      }));
    if (pois.length === 0) return null;
    return { agentName: agent, colour, pois };
  }, [racePlans, selected]);

  // Chain the zoom: globe flyTo for 1400 ms, then swap to MapLibre. On
  // deselect, swap back to globe immediately so it can tween out to world.
  // The effect depends on ``selectedId`` (stable string) rather than the
  // derived ``selected`` object — the 3s validated-plans poll refreshes
  // the ``validated`` array on every tick, which would otherwise produce
  // a new ``selected`` reference and re-fire the swap mid-view.
  useEffect(() => {
    if (!selectedId) {
      setMapMode(false);
      return;
    }
    setMapMode(false);
    const t = window.setTimeout(() => {
      if (mountedRef.current) setMapMode(true);
    }, GLOBE_TO_MAP_MS);
    return () => window.clearTimeout(t);
  }, [selectedId]);

  const arcs: GlobeArc[] = useMemo(() => {
    if (!selected?.plan) return [];
    const plan = selected.plan;
    const byId = new Map(plan.pois.map((p) => [p.id, p]));
    const colour =
      AGENT_COLOURS[(selected.agent_name ?? "").toLowerCase()] ??
      AGENT_FALLBACK_COLOUR;
    const out: GlobeArc[] = [];
    for (const leg of plan.legs ?? []) {
      const from = byId.get(leg.from);
      const to = byId.get(leg.to);
      if (!from || !to) continue;
      out.push({
        startLat: from.lat,
        startLng: from.lng,
        endLat: to.lat,
        endLng: to.lng,
        colour,
      });
    }
    return out;
  }, [selected]);

  const points: GlobePoint[] = useMemo(
    () =>
      sortedValidated
        .filter((v) => v.anchor_lat !== null && v.anchor_lng !== null)
        .map((v) => ({
          lat: v.anchor_lat as number,
          lng: v.anchor_lng as number,
          colour: v.id === selectedId ? "#00B14F" : "#FFFFFF",
          label: v.plan?.narrative ?? "validated plan",
        })),
    [sortedValidated, selectedId],
  );

  const focusPoints = useMemo(() => {
    if (!selected) return null;
    const pois = selected.plan?.pois ?? [];
    const live = pois
      .filter((p) => typeof p.lat === "number" && typeof p.lng === "number")
      .map((p) => ({ lat: p.lat, lng: p.lng }));
    if (live.length > 0) return live;
    if (selected.anchor_lat != null && selected.anchor_lng != null) {
      return [{ lat: selected.anchor_lat, lng: selected.anchor_lng }];
    }
    return null;
  }, [selected]);

  const handleSelect = useCallback((id: string) => {
    setSelectedId((prev) => (prev === id ? null : id));
    setFeedbackStage("ask");
    setFeedbackText("");
    setFeedbackSentiment("positive");
    setFeedbackSubmitting(false);
  }, []);

  const handlePolygonClick = useCallback((iso3: string) => {
    setCountryFilter(iso3);
  }, []);

  const clearCountryFilter = useCallback(() => setCountryFilter(null), []);

  const handleLike = useCallback(
    async (id: string) => {
      const previous = validated;
      setValidated((rows) =>
        rows.map((row) =>
          row.id === id ? { ...row, likes: (row.likes ?? 0) + 1 } : row,
        ),
      );
      try {
        await likeValidated(id);
      } catch {
        if (mountedRef.current) setValidated(previous);
      }
    },
    [validated],
  );

  const handleFeedbackSubmit = useCallback(async () => {
    if (!selected) return;
    setFeedbackSubmitting(true);
    try {
      await submitFeedback({
        plan_id: selected.plan_id,
        validated_id: selected.id,
        question: "What did you enjoy about this trip?",
        response: feedbackText,
        sentiment: feedbackSentiment,
      });
      if (!mountedRef.current) return;
      setFeedbackStage("done");
    } catch {
      // Leave the drawer in answer state so the user can retry.
    } finally {
      if (mountedRef.current) setFeedbackSubmitting(false);
    }
  }, [selected, feedbackText, feedbackSentiment]);

  const narrative = selected?.plan?.narrative ?? "";
  const averageRating = selected
    ? (selected.hitl_rating.novelty +
        selected.hitl_rating.efficiency +
        selected.hitl_rating.vibe) /
      3
    : 0;
  const showMap = Boolean(selected && mapMode);

  return (
    <main className="flex h-full text-white overflow-hidden">
      <div className="flex-1 relative min-w-0">
        {/* Both surfaces render, z-index layering hides the inactive one.
            Leaving the globe mounted while the map is primary keeps its
            camera state warm for the back-transition and avoids a flash
            during the deselect animation. */}
        <div
          className="absolute inset-0 transition-opacity duration-500"
          style={{ opacity: showMap ? 0 : 1, zIndex: showMap ? 0 : 1 }}
          aria-hidden={showMap}
        >
          <PrismGlobe
            pointsData={points}
            arcsData={arcs}
            ringsData={NO_RINGS}
            onPolygonClick={handlePolygonClick}
            focusPoints={focusPoints}
          />
        </div>
        <div
          className="absolute inset-0 transition-opacity duration-500"
          style={{ opacity: showMap ? 1 : 0, zIndex: showMap ? 1 : 0 }}
          aria-hidden={!showMap}
        >
          {selected ? (
            <LiveCanvas>
              <RoutePins route={agentRoute} />
            </LiveCanvas>
          ) : null}
        </div>

        <div className="absolute top-4 left-4 flex flex-col gap-2 z-30">
          {countryFilter ? (
            <button
              type="button"
              onClick={clearCountryFilter}
              className="px-3 py-1 bg-white/10 border border-white/20 rounded text-xs hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60"
            >
              clear filter · {countryFilter}
            </button>
          ) : null}
          {selected ? (
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="px-3 py-1 bg-black/60 backdrop-blur border border-white/20 rounded text-xs hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60"
            >
              ← back to globe
            </button>
          ) : null}
          {pollError ? (
            <div
              role="status"
              className="px-3 py-1 bg-black/60 backdrop-blur border border-amber-300/30 rounded text-[10px] font-mono text-amber-300/80"
            >
              validated poll: {pollError}
            </div>
          ) : null}
        </div>

        {selected ? (
          <div className="absolute bottom-2 right-3 text-[10px] text-white/50 font-mono pointer-events-none z-10">
            {showMap
              ? "© OpenMapTiles · © OpenStreetMap contributors"
              : "imagery © Esri · NASA Blue Marble"}
          </div>
        ) : null}

        {selected && narrative ? (
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 w-[min(90%,680px)] px-5 py-3 bg-black/75 backdrop-blur border border-white/10 rounded text-sm text-white/90 text-pretty z-30">
            <WordReveal key={selected.id} text={narrative} />
            <div className="mt-2 text-[10px] text-white/40 tabular-nums">
              {selected.agent_name ?? "—"} · rating{" "}
              {averageRating.toFixed(1)}/5 · {selected.likes ?? 0} ♥
            </div>
          </div>
        ) : null}
      </div>

      <aside className="w-[380px] h-full border-l border-white/10 bg-black/60 backdrop-blur overflow-y-auto p-4 space-y-4 shrink-0">
        <header>
          <h1 className="text-lg font-semibold tracking-tight">
            Prism · Explore
          </h1>
          <p className="text-xs text-white/45 text-pretty">
            Validated plans. Click a country to filter. Click a route — the
            globe flies in and the live Singapore map takes over with the
            full itinerary.
          </p>
        </header>
        {initialLoaded ? (
          <TopRoutesList
            validated={sortedValidated}
            countryFilter={countryFilter}
            selectedId={selectedId}
            onSelect={handleSelect}
            onLike={handleLike}
          />
        ) : (
          <div
            aria-live="polite"
            className="p-3 border border-white/10 rounded bg-white/[0.02] text-xs text-white/50"
          >
            loading validated plans…
          </div>
        )}
        {selected ? (
          <FeedbackDrawer
            selected={selected}
            stage={feedbackStage}
            text={feedbackText}
            sentiment={feedbackSentiment}
            submitting={feedbackSubmitting}
            onStage={setFeedbackStage}
            onText={setFeedbackText}
            onSentiment={setFeedbackSentiment}
            onSubmit={handleFeedbackSubmit}
          />
        ) : null}
      </aside>
    </main>
  );
}
