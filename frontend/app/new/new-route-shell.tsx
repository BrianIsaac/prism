"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentRacePanel } from "@/components/agent-race-panel";
import { AgentStreamOverlay } from "@/components/agent-stream-overlay";
import { HitlRating } from "@/components/hitl-rating";
import { LiveCanvas } from "@/components/live-canvas";
import { PastRacesList } from "@/components/past-races-list";
import { PlanCard } from "@/components/plan-card";
import { PlanDetail } from "@/components/plan-detail";
import {
  StructuredRaceForm,
  type RaceFormState,
  type TransportProfile,
} from "@/components/structured-race-form";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { openRaceStream, ratePlan, startRace } from "@/lib/api-client";
import type {
  PastRace,
  Plan,
  PlanPOI,
  RaceStreamEvent,
  Rating,
  SpecOverride,
} from "@/lib/types";

// Sliding window for the stream overlay. 200 events cover the Singapore
// demo comfortably (~1.5 events/agent/second for 60s) without blowing the
// canvas GPU budget on hover pulses.
const EVENT_WINDOW = 200;

// Legacy-to-profile mapping for past-race prefill. Phase 0's `TransportMode`
// still uses walk/drive/transit/cycle, whereas the route profiles in the
// GrabMaps API are driving/motorcycle/tricycle/cycling/walking. Unknown
// inputs fall back to the form's default so the preset never crashes.
const TRANSPORT_FROM_LEGACY: Record<string, TransportProfile> = {
  walk: "walking",
  walking: "walking",
  drive: "driving",
  driving: "driving",
  transit: "driving",
  cycle: "cycling",
  cycling: "cycling",
  motorcycle: "motorcycle",
  tricycle: "tricycle",
};

export interface NewRouteShellProps {
  /** Invoked after a successful rating submission; drives the redirect back
   *  to Explore so the caller can own the navigation. */
  onRated: () => void;
}

/**
 * Race launcher shell: mounts the Phase 4 live canvas in the background,
 * overlays the structured form + past-races list, drives the agent race
 * panel from the SSE feed, and renders three plan cards at the bottom of
 * the viewport on `race_complete`. Selecting a card opens a right-side
 * drawer with per-stop street-view galleries and the HITL rating surface.
 */
export function NewRouteShell({ onRated }: NewRouteShellProps) {
  const [preset, setPreset] = useState<Partial<RaceFormState> | null>(null);
  const [events, setEvents] = useState<RaceStreamEvent[]>([]);
  const [raceInProgress, setRaceInProgress] = useState(false);
  const [resolvedPlans, setResolvedPlans] = useState<Plan[]>([]);
  const [rankedIds, setRankedIds] = useState<string[] | null>(null);
  const [finalDuration, setFinalDuration] = useState<number | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [editedPlans, setEditedPlans] = useState<Record<string, Plan>>({});
  const [overrideMap, setOverrideMap] = useState<Record<string, PlanPOI[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [pastRacesKey, setPastRacesKey] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const streamCloseRef = useRef<(() => void) | null>(null);

  useEffect(
    () => () => {
      streamCloseRef.current?.();
      streamCloseRef.current = null;
    },
    [],
  );

  const finalPlans: Plan[] | null = useMemo(() => {
    if (!rankedIds) return null;
    const byId = new Map<string, Plan>();
    for (const p of resolvedPlans) {
      if (p.id) byId.set(p.id, p);
    }
    const ordered = rankedIds
      .map((id) => byId.get(id))
      .filter((p): p is Plan => p !== undefined);
    return ordered.length > 0 ? ordered : resolvedPlans;
  }, [resolvedPlans, rankedIds]);

  const selectedPlan = useMemo(() => {
    if (!selectedPlanId || !finalPlans) return null;
    const base = finalPlans.find((p) => p.id === selectedPlanId) ?? null;
    if (!base) return null;
    return editedPlans[selectedPlanId] ?? base;
  }, [editedPlans, finalPlans, selectedPlanId]);

  const resetRaceState = useCallback(() => {
    streamCloseRef.current?.();
    streamCloseRef.current = null;
    setEvents([]);
    setResolvedPlans([]);
    setRankedIds(null);
    setFinalDuration(null);
    setSelectedPlanId(null);
    setEditedPlans({});
    setOverrideMap({});
    setError(null);
  }, []);

  const handleLaunch = useCallback(
    async (query: string, spec_override: SpecOverride) => {
      resetRaceState();
      setRaceInProgress(true);
      try {
        const { race_id } = await startRace(query, spec_override);
        const close = openRaceStream(
          race_id,
          (ev) => {
            setEvents((prev) => {
              const next = prev.concat(ev);
              return next.length > EVENT_WINDOW
                ? next.slice(next.length - EVENT_WINDOW)
                : next;
            });
            if (ev.type === "plan_resolved") {
              setResolvedPlans((prev) => {
                const plan = ev.payload;
                if (plan.id && prev.some((p) => p.id === plan.id)) return prev;
                return prev.concat(plan);
              });
            } else if (ev.type === "race_complete") {
              setRankedIds(ev.payload.ranked_plan_ids);
              setFinalDuration(ev.payload.duration_seconds);
              setRaceInProgress(false);
              setPastRacesKey((k) => k + 1);
              streamCloseRef.current?.();
              streamCloseRef.current = null;
            } else if (ev.type === "error") {
              setError(`race error: ${ev.payload.message}`);
              setRaceInProgress(false);
              streamCloseRef.current?.();
              streamCloseRef.current = null;
            }
          },
          () => {
            setError("stream connection lost");
            setRaceInProgress(false);
          },
        );
        streamCloseRef.current = close;
      } catch (e) {
        setError(`race failed to start: ${(e as Error).message}`);
        setRaceInProgress(false);
      }
    },
    [resetRaceState],
  );

  const handleEditPlan = useCallback(
    (planId: string, edited: Plan, pois_override: PlanPOI[]) => {
      setEditedPlans((prev) => ({ ...prev, [planId]: edited }));
      setOverrideMap((prev) => ({ ...prev, [planId]: pois_override }));
    },
    [],
  );

  const handleRate = useCallback(
    async (planId: string, rating: Rating) => {
      setSubmitting(true);
      setError(null);
      try {
        const override = overrideMap[planId];
        await ratePlan(planId, {
          ...rating,
          pois_override: override,
        });
        onRated();
      } catch (e) {
        setError(`rating failed: ${(e as Error).message}`);
      } finally {
        setSubmitting(false);
      }
    },
    [onRated, overrideMap],
  );

  const pickPast = useCallback((race: PastRace) => {
    const spec = race.spec ?? {};
    const next: Partial<RaceFormState> = { notes: race.user_query };
    if (spec.area) next.area = spec.area;
    if (
      typeof spec.max_duration_minutes === "number" &&
      spec.max_duration_minutes > 0
    ) {
      next.durationHours = spec.max_duration_minutes / 60;
    }
    if (typeof spec.max_budget_sgd === "number" && spec.max_budget_sgd > 0) {
      next.budgetSgd = spec.max_budget_sgd;
    }
    if (spec.transport_mode) {
      const legacy = spec.transport_mode as unknown as string;
      const mapped = TRANSPORT_FROM_LEGACY[legacy];
      if (mapped) next.mode = mapped;
    }
    if (spec.dietary) next.dietary = spec.dietary;
    if (Array.isArray(spec.mood_tags) && spec.mood_tags.length > 0) {
      next.vibe = spec.mood_tags;
    }
    if (spec.start_time_iso) next.startTime = spec.start_time_iso;
    if (typeof spec.party_size === "number" && spec.party_size > 0) {
      next.partySize = spec.party_size;
    }
    if (typeof spec.accessible === "boolean") next.accessible = spec.accessible;
    setPreset(next);
  }, []);

  const showResults = finalPlans !== null && finalPlans.length > 0;

  return (
    <div className="relative h-full w-full overflow-hidden text-white">
      <div className="absolute inset-0">
        <LiveCanvas>
          <AgentStreamOverlay events={events} />
        </LiveCanvas>
      </div>

      <aside
        aria-label="Race launcher"
        className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-full max-w-md flex-col"
      >
        <ScrollArea className="pointer-events-auto h-full">
          <div className="space-y-4 p-4">
            <header>
              <h1 className="text-lg font-semibold tracking-tight text-white">
                New Route
              </h1>
              <p className="mt-1 text-[11px] text-white/50 text-pretty">
                Structured inputs compose a query for the three-agent race.
                Watch the canvas light up, then rate the winner to pin it.
              </p>
            </header>

            <section className="rounded border border-white/10 bg-black/60 p-3 backdrop-blur">
              <PastRacesList onPick={pickPast} refreshKey={pastRacesKey} />
            </section>

            <section className="rounded border border-white/10 bg-black/60 p-3 backdrop-blur">
              <StructuredRaceForm
                onLaunch={handleLaunch}
                disabled={raceInProgress}
                preset={preset}
              />
            </section>

            {error && (
              <div
                role="alert"
                aria-live="polite"
                className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200"
              >
                {error}
              </div>
            )}
          </div>
        </ScrollArea>
      </aside>

      {(raceInProgress || resolvedPlans.length > 0) && (
        <div className="pointer-events-auto absolute right-4 top-4 z-10 w-72">
          <AgentRacePanel
            inProgress={raceInProgress}
            events={events}
            finalDurationSeconds={finalDuration}
          />
        </div>
      )}

      {showResults && finalPlans && (
        <div
          aria-live="polite"
          className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center px-4"
        >
          <div className="pointer-events-auto grid w-full max-w-5xl grid-cols-1 gap-3 md:grid-cols-3">
            {finalPlans.map((plan) => {
              const planId = plan.id ?? plan.agent_name;
              const effectivePlan =
                plan.id && editedPlans[plan.id] ? editedPlans[plan.id] : plan;
              const selected = planId === selectedPlanId;
              const anySelected = selectedPlanId !== null;
              return (
                <PlanCard
                  key={planId}
                  plan={effectivePlan}
                  selected={selected}
                  dimmed={anySelected && !selected}
                  onSelect={() =>
                    setSelectedPlanId(selected ? null : plan.id ?? null)
                  }
                />
              );
            })}
          </div>
        </div>
      )}

      {selectedPlan && selectedPlan.id && (
        <aside
          aria-label="Plan detail"
          className="pointer-events-auto absolute inset-y-0 right-0 z-20 flex w-full max-w-md flex-col border-l border-white/10 bg-black/85 backdrop-blur"
        >
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-2">
            <span className="text-[10px] uppercase tracking-wider text-white/40">
              plan · {selectedPlan.agent_name}
            </span>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setSelectedPlanId(null)}
              aria-label="Close plan detail"
            >
              close
            </Button>
          </div>
          <div className="flex-1 overflow-hidden">
            <PlanDetail
              plan={selectedPlan}
              onEdit={(edited, pois_override) =>
                handleEditPlan(
                  selectedPlan.id as string,
                  edited,
                  pois_override,
                )
              }
            />
          </div>
          <div className="border-t border-white/10 p-3">
            <HitlRating
              disabled={submitting}
              onSubmit={(rating) =>
                handleRate(selectedPlan.id as string, rating)
              }
            />
          </div>
        </aside>
      )}
    </div>
  );
}
