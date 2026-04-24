"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Plan } from "@/lib/types";

// Per-agent border + background tint, keyed to the CSS tokens defined in
// globals.css. Ref-stable at module scope so no memo hook is needed to
// keep downstream renders cheap.
const AGENT_COLOURS: Record<string, string> = {
  opus: "border-agent-opus/40 bg-agent-opus/5",
  gpt: "border-agent-gpt/40 bg-agent-gpt/5",
  gemini: "border-agent-gemini/40 bg-agent-gemini/5",
};

const AGENT_DIM: Record<string, string> = {
  opus: "opacity-40 hover:opacity-70",
  gpt: "opacity-40 hover:opacity-70",
  gemini: "opacity-40 hover:opacity-70",
};

export interface PlanCardProps {
  plan: Plan;
  onSelect: () => void;
  selected: boolean;
  /** When true and `selected=false`, the card is dimmed to defocus it. */
  dimmed?: boolean;
}

export function PlanCard({
  plan,
  onSelect,
  selected,
  dimmed = false,
}: PlanCardProps) {
  const passed = plan.hard_pass ?? false;
  const colour =
    AGENT_COLOURS[plan.agent_name] ?? "border-white/20 bg-white/5";
  const ring = selected
    ? "ring-2 ring-grab-green"
    : dimmed
      ? AGENT_DIM[plan.agent_name] ?? "opacity-40"
      : "";

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "group/plan w-full rounded border p-3 text-left transition-all backdrop-blur-sm",
        "hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60",
        colour,
        ring,
      )}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wider text-white/70">
          {plan.agent_name}
          {plan.rank != null && (
            <span className="ml-2 font-mono tabular-nums text-white/40">
              #{plan.rank}
            </span>
          )}
        </span>
        <span className="font-mono text-xs tabular-nums text-white/40">
          {plan.total_score !== undefined
            ? plan.total_score.toFixed(3)
            : "—"}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 text-sm text-white/80 text-pretty">
        {plan.narrative || plan.error || "(no narrative)"}
      </div>
      <div className="mt-2 flex items-center gap-2 font-mono text-[10px] tabular-nums text-white/50">
        <Badge variant="outline" className="h-4 text-[9px]">
          {plan.pois?.length ?? 0} stops
        </Badge>
        <Badge variant="outline" className="h-4 text-[9px]">
          {Math.round(plan.total_minutes || 0)} min
        </Badge>
        <Badge variant="outline" className="h-4 text-[9px]">
          SGD {(plan.total_cost_sgd || 0).toFixed(0)}
        </Badge>
        {plan.tool_call_count !== undefined && (
          <Badge variant="outline" className="ml-auto h-4 text-[9px]">
            {plan.tool_call_count} tools
          </Badge>
        )}
      </div>
      {!passed && plan.failures && plan.failures.length > 0 && (
        <div className="mt-2 text-xs text-red-400/70">
          failed: {plan.failures[0]}
        </div>
      )}
      {plan.soft_scores && (
        <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px] tabular-nums text-white/40">
          <div>flow {(plan.soft_scores.flow ?? 0).toFixed(2)}</div>
          <div>div {(plan.soft_scores.diversity ?? 0).toFixed(2)}</div>
          <div>vibe {(plan.soft_scores.vibe ?? 0).toFixed(2)}</div>
        </div>
      )}
    </button>
  );
}
