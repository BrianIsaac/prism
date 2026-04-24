"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  fetchValidated,
  likeValidated,
  submitFeedback,
} from "@/lib/api-client";
import type { ValidatedPlan } from "@/lib/types";
import { GlobeIntro } from "@/components/globe-intro";
import { LiveCanvas, useLiveCanvas } from "@/components/live-canvas";
import { TrafficLayer } from "@/components/traffic-layer";
import { IncidentLayer } from "@/components/incident-layer";
import { AgentStreamOverlay } from "@/components/agent-stream-overlay";
import { TopRoutesList } from "@/components/top-routes-list";
import { WordReveal } from "@/components/word-reveal";
import {
  FeedbackDrawer,
  type FeedbackSentiment,
  type FeedbackStage,
} from "@/components/feedback-drawer";

const POLL_INITIAL_MS = 3_000;
const POLL_MAX_MS = 30_000;

const SINGAPORE: [number, number] = [103.8198, 1.3521];

interface RoutePinsProps {
  selected: ValidatedPlan | null;
}

/**
 * When a row is selected, fly the camera to the bounding box of its POIs and
 * mount a small SVG overlay highlighting their pins. Lives inside the
 * `<LiveCanvas>` so it has access to the live MapLibre instance.
 */
function RoutePins({ selected }: RoutePinsProps) {
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

  const pois = useMemo(() => {
    if (!selected?.plan?.pois) return [];
    return selected.plan.pois.filter(
      (p) => Number.isFinite(p.lat) && Number.isFinite(p.lng),
    );
  }, [selected]);

  useEffect(() => {
    if (!map || !ready) return;
    if (pois.length === 0) {
      map.flyTo({ center: SINGAPORE, zoom: 11, duration: 600 });
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
      { padding: 80, duration: 700, maxZoom: 14 },
    );
  }, [map, ready, pois]);

  if (!map || !ready) return null;
  if (size.width === 0 || size.height === 0) return null;
  if (pois.length === 0) return null;

  const points = pois.map((p) => map.project([p.lng, p.lat]));
  const arcs: string[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2 - Math.hypot(b.x - a.x, b.y - a.y) * 0.18;
    arcs.push(`M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`);
  }

  // Eat the unused render dependency cleanly so React keeps re-projecting on
  // pan/zoom even though `tick` itself is not interpolated into JSX.
  void tick;

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
          stroke="#00b14f"
          strokeWidth={2.4}
          opacity={0.85}
          style={{ filter: "drop-shadow(0 0 6px rgba(0,177,79,0.55))" }}
        />
      ))}
      {points.map((p, i) => (
        <g key={i}>
          <circle
            cx={p.x}
            cy={p.y}
            r={9}
            fill="none"
            stroke="rgba(255,255,255,0.85)"
            strokeWidth={1.2}
          />
          <circle cx={p.x} cy={p.y} r={5} fill="#00b14f" />
          <text
            x={p.x + 11}
            y={p.y + 4}
            fontSize={10}
            fontFamily="ui-monospace, monospace"
            fill="rgba(255,255,255,0.85)"
            stroke="rgba(0,0,0,0.7)"
            strokeWidth={2}
            paintOrder="stroke"
          >
            {i + 1}
          </text>
        </g>
      ))}
    </svg>
  );
}

export default function ExplorePage() {
  const [introComplete, setIntroComplete] = useState<boolean>(false);
  const [validated, setValidated] = useState<ValidatedPlan[]>([]);
  const [initialLoaded, setInitialLoaded] = useState<boolean>(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [trafficVisible, setTrafficVisible] = useState<boolean>(false);
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

  const onIntroComplete = useCallback(() => setIntroComplete(true), []);

  // Polling /validated with exponential backoff on failure (3s → 30s) and a
  // reset on success — preserves v1 behaviour and avoids hammering the
  // backend if it is briefly down. Uses a self-rescheduling setTimeout so
  // backoff state lives in a single closure.
  useEffect(() => {
    let cancelled = false;
    let delay = POLL_INITIAL_MS;
    let timer: number | null = null;

    const tick = async (): Promise<void> => {
      try {
        const data = await fetchValidated(null, 100);
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
  }, []);

  const selected = useMemo(
    () => validated.find((v) => v.id === selectedId) ?? null,
    [validated, selectedId],
  );

  const handleSelect = useCallback((id: string) => {
    setSelectedId((prev) => {
      const next = prev === id ? null : id;
      // Reset feedback state on every selection swap so the drawer always
      // opens at "ask" rather than carrying an earlier plan's draft.
      setFeedbackStage("ask");
      setFeedbackText("");
      setFeedbackSentiment("positive");
      setFeedbackSubmitting(false);
      return next;
    });
  }, []);

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
        // Roll back optimistic increment if the call failed.
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
      // Surface the failure inline by leaving the drawer in answer state
      // with the submitting flag cleared. The user can retry without losing
      // their text.
    } finally {
      if (mountedRef.current) setFeedbackSubmitting(false);
    }
  }, [selected, feedbackText, feedbackSentiment]);

  return (
    <main className="relative w-full h-full overflow-hidden">
      {introComplete ? null : <GlobeIntro onComplete={onIntroComplete} />}

      <div
        className="absolute inset-0 transition-opacity duration-500"
        style={{ opacity: introComplete ? 1 : 0 }}
        aria-hidden={!introComplete}
      >
        <LiveCanvas>
          <TrafficLayer visible={trafficVisible} />
          <IncidentLayer />
          <AgentStreamOverlay events={[]} />
          <RoutePins selected={selected} />
        </LiveCanvas>
      </div>

      {introComplete ? (
        <header className="absolute top-3 left-3 z-30 flex items-center gap-2 rounded bg-black/60 backdrop-blur border border-white/10 px-3 py-1.5">
          <span className="text-[10px] uppercase tracking-[0.2em] text-grab-green/80">
            Live · Singapore
          </span>
          <span className="h-3 w-px bg-white/10" aria-hidden="true" />
          <button
            type="button"
            onClick={() => setTrafficVisible((v) => !v)}
            aria-pressed={trafficVisible}
            className="flex items-center gap-1.5 text-[11px] text-white/80 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 rounded px-1"
          >
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-full transition-colors"
              style={{
                backgroundColor: trafficVisible
                  ? "var(--color-grab-green)"
                  : "rgba(255,255,255,0.25)",
                boxShadow: trafficVisible
                  ? "0 0 6px var(--color-grab-green)"
                  : "none",
              }}
            />
            Live traffic
          </button>
        </header>
      ) : null}

      {introComplete && pollError ? (
        <div
          role="status"
          className="absolute top-14 left-3 z-30 rounded bg-black/60 backdrop-blur border border-amber-300/30 px-2 py-1 text-[10px] font-mono text-amber-300/80"
        >
          validated poll: {pollError}
        </div>
      ) : null}

      {introComplete && selected?.plan?.narrative ? (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-30 max-w-2xl px-4 py-2 rounded bg-black/65 backdrop-blur border border-white/10">
          <WordReveal
            key={selected.id}
            text={selected.plan.narrative}
            className="text-sm text-white/85"
          />
        </div>
      ) : null}

      {introComplete ? (
        <aside className="absolute top-3 right-3 bottom-3 z-30 w-[360px] flex flex-col gap-2 rounded-lg bg-black/65 backdrop-blur border border-white/10 p-3 overflow-hidden">
          <div className="text-xs uppercase tracking-[0.2em] text-grab-green/80">
            Prism · Explore
          </div>
          <p className="text-[11px] text-white/45 leading-snug">
            Three frontier models race across this canvas. Pinned plans win a
            home on the map. Pick one to fly in.
          </p>
          <div className="h-px bg-white/10" aria-hidden="true" />
          <div className="flex-1 overflow-y-auto pr-1">
            {initialLoaded ? (
              <TopRoutesList
                validated={validated}
                countryFilter={null}
                selectedId={selectedId}
                onSelect={handleSelect}
                onLike={handleLike}
              />
            ) : (
              <div className="text-xs text-white/30 italic">
                loading validated plans…
              </div>
            )}
          </div>
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
      ) : null}
    </main>
  );
}
