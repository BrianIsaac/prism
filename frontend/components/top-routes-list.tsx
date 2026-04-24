"use client";

import type { ValidatedPlan } from "@/lib/types";

export interface TopRoutesListProps {
  validated: ValidatedPlan[];
  countryFilter: string | null;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onLike?: (id: string) => void;
}

/**
 * Ranked list of validated plans. The component renders every entry the
 * same way regardless of whether the row is auto-pinned (a rank-1 row from
 * a recent race) or hand-validated; the backend materialises the auto pin
 * on first like/feedback so any visual differentiation here would leak the
 * data layer's bookkeeping into the UI.
 *
 * Interactive vs read-only is gated by the presence of `onSelect`/`onLike`.
 * In interactive mode each row's body is a `<button aria-pressed>` so
 * keyboard users get the right semantics.
 */
export function TopRoutesList({
  validated,
  countryFilter,
  selectedId = null,
  onSelect,
  onLike,
}: TopRoutesListProps) {
  const top = validated.slice(0, 10);
  const interactive = Boolean(onSelect || onLike);

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wider text-white/40">
          top validated {countryFilter ? `· ${countryFilter}` : "· singapore"}
        </div>
        <div className="text-xs font-mono text-white/30 tabular-nums">
          {validated.length}
        </div>
      </div>
      {top.length === 0 ? (
        <div className="mt-2 text-xs text-white/30 italic">
          no validated plans yet. launch a race from New Route and pin one.
        </div>
      ) : (
        <ol className="mt-2 space-y-1">
          {top.map((v, i) => {
            const selected = selectedId === v.id;
            const r = v.hitl_rating ?? {
              novelty: 0,
              efficiency: 0,
              vibe: 0,
            };
            const avgRating =
              ((r.novelty ?? 0) + (r.efficiency ?? 0) + (r.vibe ?? 0)) / 3;
            const likes = v.likes ?? 0;
            return (
              <li key={v.id}>
                <div
                  className={`flex items-start gap-2 text-xs px-2 py-1 rounded transition-colors ${
                    selected
                      ? "bg-grab-green/10 border border-grab-green/30"
                      : "border border-transparent hover:bg-white/5"
                  }`}
                >
                  <span className="font-mono tabular-nums text-white/30 w-5 shrink-0 pt-0.5">
                    {i + 1}
                  </span>
                  {interactive && onSelect ? (
                    <button
                      type="button"
                      onClick={() => onSelect(v.id)}
                      aria-pressed={selected}
                      className="flex-1 min-w-0 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-grab-green/60 rounded"
                    >
                      <div className="truncate text-white/85">
                        {v.plan?.narrative || "(unnamed plan)"}
                      </div>
                      <div className="text-[10px] text-white/40 tabular-nums">
                        {v.agent_name ?? "—"} · rating {avgRating.toFixed(1)}/5
                      </div>
                    </button>
                  ) : (
                    <div className="flex-1 min-w-0 text-white/60">
                      <div className="truncate">
                        {v.plan?.narrative || "(unnamed plan)"}
                      </div>
                      <div className="text-[10px] text-white/30 tabular-nums">
                        {v.agent_name ?? "—"} · rating {avgRating.toFixed(1)}/5
                      </div>
                    </div>
                  )}
                  {onLike ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onLike(v.id);
                      }}
                      aria-label={`Like ${v.plan?.narrative ?? "this plan"} (${likes} likes)`}
                      className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] text-white/50 hover:text-grab-green hover:bg-grab-green/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-grab-green/60 transition-colors"
                    >
                      <span aria-hidden="true">♥</span>
                      <span className="font-mono tabular-nums">{likes}</span>
                    </button>
                  ) : likes > 0 ? (
                    <span
                      className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 text-[11px] text-white/40 font-mono tabular-nums"
                      aria-label={`${likes} likes`}
                    >
                      <span aria-hidden="true">♥</span>
                      {likes}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
