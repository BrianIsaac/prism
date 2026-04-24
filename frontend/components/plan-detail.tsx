"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StreetViewGallery } from "@/components/street-view-gallery";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchAlternatives } from "@/lib/api-client";
import type { Alternative, Plan, PlanPOI, StreetviewPhoto } from "@/lib/types";

export interface PlanDetailProps {
  plan: Plan;
  /**
   * Fires with the mutated plan and the effective `pois_override` list the
   * parent can forward to `/rating`. Only invoked after a real swap; the
   * parent tracks edits by plan id keyed on `plan.id`.
   */
  onEdit: (editedPlan: Plan, pois_override: PlanPOI[]) => void;
}

function extractPhotos(
  source: unknown,
): StreetviewPhoto[] | undefined {
  // Alternative rows may carry a future `streetview_photos` payload when the
  // backend wires the tool into `/alternatives`. Until that lands we feature-
  // detect rather than cast blindly so a null response renders the empty
  // state cleanly.
  if (!source || typeof source !== "object") return undefined;
  const maybe = (source as { streetview_photos?: unknown }).streetview_photos;
  return Array.isArray(maybe) ? (maybe as StreetviewPhoto[]) : undefined;
}

/**
 * Right-side plan detail drawer. Renders the selected plan's POI list as a
 * vertical carousel of street-view galleries, each with a per-stop swap
 * control that opens a dialog of alternatives. Picking an alternative
 * rebuilds the POI + leg lists and emits the mutated plan plus the derived
 * `pois_override` array so the parent can forward both to `/rating`.
 */
export function PlanDetail({ plan, onEdit }: PlanDetailProps) {
  const pois = plan.pois;
  const [swappingIndex, setSwappingIndex] = useState<number | null>(null);
  const [alternatives, setAlternatives] = useState<Alternative[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestSeqRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const closeSwap = useCallback(() => {
    requestSeqRef.current += 1;
    setSwappingIndex(null);
    setAlternatives([]);
    setError(null);
  }, []);

  const openSwap = useCallback(
    async (index: number) => {
      const target = pois[index];
      if (!target) return;
      const requestId = ++requestSeqRef.current;
      setSwappingIndex(index);
      setLoading(true);
      setAlternatives([]);
      setError(null);
      try {
        const res = await fetchAlternatives(
          target.category,
          { lat: target.lat, lng: target.lng },
          pois.map((p) => p.id),
          5,
        );
        if (!mountedRef.current || requestSeqRef.current !== requestId) return;
        setAlternatives(res.alternatives);
      } catch (e) {
        if (!mountedRef.current || requestSeqRef.current !== requestId) return;
        setError((e as Error).message);
      } finally {
        if (mountedRef.current && requestSeqRef.current === requestId) {
          setLoading(false);
        }
      }
    },
    [pois],
  );

  const handlePickAlternative = useCallback(
    (alt: Alternative) => {
      if (swappingIndex === null) return;
      const oldId = pois[swappingIndex].id;
      const altPhotos = extractPhotos(alt);
      const replacement: PlanPOI = {
        ...pois[swappingIndex],
        id: alt.id,
        name: alt.name,
        category: alt.category,
        subcategory: alt.subcategory ?? null,
        lat: alt.lat,
        lng: alt.lng,
        description: alt.description ?? null,
        price_tier: alt.price_tier,
        avg_cost_sgd: alt.avg_cost_sgd,
        dietary_tags: alt.dietary_tags,
        tags: alt.tags,
        streetview_photos: altPhotos ?? null,
      };
      const pois_override: PlanPOI[] = pois.map((p, i) =>
        i === swappingIndex ? replacement : p,
      );
      const newLegs = (plan.legs || []).map((leg) => ({
        ...leg,
        from: leg.from === oldId ? alt.id : leg.from,
        to: leg.to === oldId ? alt.id : leg.to,
      }));
      onEdit({ ...plan, pois: pois_override, legs: newLegs }, pois_override);
      closeSwap();
    },
    [closeSwap, onEdit, plan, pois, swappingIndex],
  );

  const scores = plan.soft_scores ?? null;
  const totalScore = plan.total_score;
  const rank = plan.rank ?? null;
  const failures = plan.failures ?? [];

  const activeAltTarget = useMemo(
    () => (swappingIndex !== null ? pois[swappingIndex] : null),
    [pois, swappingIndex],
  );

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-white/10 p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-white/70">
            {plan.agent_name}
            {rank != null && (
              <span className="ml-2 font-mono tabular-nums text-white/40">
                #{rank}
              </span>
            )}
          </h2>
          <span className="font-mono text-xs tabular-nums text-white/40">
            {typeof totalScore === "number" ? totalScore.toFixed(3) : "—"}
          </span>
        </div>
        <p className="mt-1 text-xs text-white/60 text-pretty">
          {plan.narrative || plan.error || "(no narrative)"}
        </p>
        <div className="mt-2 flex items-center gap-2 font-mono text-[10px] tabular-nums text-white/40">
          <Badge variant="outline" className="h-4 text-[9px]">
            {plan.pois.length} stops
          </Badge>
          <Badge variant="outline" className="h-4 text-[9px]">
            {Math.round(plan.total_minutes || 0)} min
          </Badge>
          <Badge variant="outline" className="h-4 text-[9px]">
            SGD {(plan.total_cost_sgd || 0).toFixed(0)}
          </Badge>
        </div>
        {scores && (
          <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px] tabular-nums text-white/40">
            <span>flow {scores.flow.toFixed(2)}</span>
            <span>diversity {scores.diversity.toFixed(2)}</span>
            <span>vibe {scores.vibe.toFixed(2)}</span>
          </div>
        )}
        {plan.hard_pass === false && failures.length > 0 && (
          <ul
            className="mt-2 list-inside list-disc space-y-0.5 text-[10px] text-red-400/80"
            aria-label="harness failures"
          >
            {failures.slice(0, 4).map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        )}
      </header>

      <ScrollArea className="flex-1">
        <ol className="space-y-4 p-4">
          {pois.map((poi, index) => {
            const photos = poi.streetview_photos ?? null;
            return (
              <li
                key={`${poi.id}-${index}`}
                className="rounded border border-white/10 bg-white/[0.02] p-3"
              >
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 shrink-0 rounded-full border border-white/15 bg-black/40 px-2 py-0.5 font-mono text-[10px] tabular-nums text-white/70">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-white/90">
                      {poi.name}
                    </div>
                    <div className="mt-0.5 text-[10px] text-white/40">
                      {poi.category}
                      {poi.subcategory ? ` · ${poi.subcategory}` : ""}
                    </div>
                    {poi.address && (
                      <div className="mt-0.5 truncate text-[10px] text-white/50">
                        {poi.address}
                      </div>
                    )}
                  </div>
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    onClick={() => openSwap(index)}
                    aria-label={`Swap stop ${index + 1}: ${poi.name}`}
                  >
                    swap
                  </Button>
                </div>
                <div className="mt-3">
                  <StreetViewGallery photos={photos} label={poi.name} />
                </div>
              </li>
            );
          })}
        </ol>
      </ScrollArea>

      <Dialog
        open={swappingIndex !== null}
        onOpenChange={(open) => {
          if (!open) closeSwap();
        }}
      >
        <DialogContent className="max-w-lg gap-3 bg-black/95 ring-white/10">
          <DialogHeader>
            <DialogTitle className="text-sm">
              Swap stop
              {swappingIndex !== null ? ` ${swappingIndex + 1}` : ""}
              {activeAltTarget ? `: ${activeAltTarget.name}` : ""}
            </DialogTitle>
            <DialogDescription>
              Pick a nearby alternative in the same category. The plan&rsquo;s
              legs will re-draw on the canvas.
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            {loading && (
              <div className="text-center text-xs text-white/40">
                finding alternatives…
              </div>
            )}
            {error && (
              <div role="alert" className="text-xs text-red-400/80">
                {error}
              </div>
            )}
            {!loading && !error && alternatives.length === 0 && (
              <div className="text-center text-xs text-white/40">
                no alternatives found in this category
              </div>
            )}
            {alternatives.map((alt) => {
              const altPhotos = extractPhotos(alt);
              return (
                <div
                  key={alt.id}
                  className="rounded border border-white/10 bg-white/[0.02] p-2"
                >
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs text-white/85">
                        {alt.name}
                      </div>
                      <div className="text-[10px] text-white/40">
                        {alt.category}
                        {alt.subcategory ? ` · ${alt.subcategory}` : ""}
                      </div>
                      {alt.description && (
                        <div className="mt-0.5 line-clamp-2 text-[10px] text-white/50 text-pretty">
                          {alt.description}
                        </div>
                      )}
                    </div>
                    <Button
                      type="button"
                      size="xs"
                      variant="default"
                      className="bg-grab-green/20 border-grab-green/40 text-grab-green hover:bg-grab-green/30"
                      onClick={() => handlePickAlternative(alt)}
                    >
                      pick
                    </Button>
                  </div>
                  <div className="mt-2">
                    <StreetViewGallery
                      photos={altPhotos}
                      label={alt.name}
                      emptyLabel="preview not available"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
