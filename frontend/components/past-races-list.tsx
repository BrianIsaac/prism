"use client";

import { useEffect, useState } from "react";

import { fetchPastRaces } from "@/lib/api-client";
import type { PastRace } from "@/lib/types";

export interface PastRacesListProps {
  onPick: (race: PastRace) => void;
  /** Bump to force a refetch after a race completes or a rating lands. */
  refreshKey: number;
}

/**
 * Cards for prior races. Clicking one hands the full row to ``onPick`` so the
 * parent can wire it into the structured form as a preset. Three render states
 * are distinguished explicitly — loading (null), empty, populated — so the
 * empty state never flashes while the initial fetch is still in flight.
 */
export function PastRacesList({ onPick, refreshKey }: PastRacesListProps) {
  const [races, setRaces] = useState<PastRace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchPastRaces(20);
        if (!cancelled) setRaces(data);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (error) {
    return (
      <div className="text-xs text-red-400/70" role="alert">
        past races unavailable: {error}
      </div>
    );
  }
  if (races === null) {
    return (
      <div
        className="font-mono text-xs text-white/30"
        role="status"
        aria-live="polite"
      >
        loading past races…
      </div>
    );
  }
  if (races.length === 0) {
    return (
      <div className="text-xs italic text-white/30">
        no past races yet. submit the form to run your first.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wider text-white/40">
          your past queries
        </div>
        <div className="font-mono text-xs text-white/30">{races.length}</div>
      </div>
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {races.map((r) => (
          <li key={r.race_id}>
            <button
              type="button"
              onClick={() => onPick(r)}
              className="w-full rounded border border-white/10 bg-white/[0.02] p-2 text-left transition-colors hover:border-grab-green/30 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60"
            >
              <div className="line-clamp-2 text-xs text-white/85 text-pretty">
                {r.user_query}
              </div>
              <div className="mt-1 flex gap-3 font-mono text-[10px] tabular-nums text-white/40">
                <span>
                  {r.created_at && r.created_at.length >= 16
                    ? r.created_at.slice(0, 16)
                    : "—"}
                </span>
                <span>{r.top_plan?.agent_name ?? "no winner"}</span>
                <span>
                  {typeof r.duration_seconds === "number"
                    ? `${r.duration_seconds.toFixed(1)}s`
                    : "—"}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
