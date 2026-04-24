"use client";

import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import type { Plan, RaceStreamEvent } from "@/lib/types";

// Three racers share one prompt and one harness; the only visible
// differentiator is the model. Tokens resolve against globals.css.
const AGENTS: ReadonlyArray<{
  name: string;
  colour: string;
  label: string;
}> = [
  { name: "opus", colour: "text-agent-opus", label: "Claude Opus 4.7" },
  { name: "gpt", colour: "text-agent-gpt", label: "OpenAI GPT 5.5" },
  { name: "gemini", colour: "text-agent-gemini", label: "Gemini 3.1 Pro" },
];

export interface AgentRacePanelProps {
  inProgress: boolean;
  events: RaceStreamEvent[];
  /** Set when `race_complete` lands — used to freeze the elapsed ticker. */
  finalDurationSeconds?: number | null;
}

/**
 * Minimal race summary strip. The visual race now lives on the Live Canvas;
 * this panel contracts to a per-agent ticker row: elapsed time, latest
 * thought preview, and a checkmark once a `plan_resolved` frame lands.
 */
export function AgentRacePanel({
  inProgress,
  events,
  finalDurationSeconds = null,
}: AgentRacePanelProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!inProgress) {
      if (finalDurationSeconds != null) setElapsed(finalDurationSeconds);
      return;
    }
    const start = Date.now();
    setElapsed(0);
    const interval = setInterval(() => {
      setElapsed((Date.now() - start) / 1000);
    }, 100);
    return () => clearInterval(interval);
  }, [inProgress, finalDurationSeconds]);

  const { resolvedByAgent, latestThought, latestTool } = useMemo(() => {
    const resolved: Record<string, Plan> = {};
    const thought: Record<string, string> = {};
    const tool: Record<string, string> = {};
    for (const ev of events) {
      if (ev.type === "plan_resolved") {
        resolved[ev.payload.agent_name] = ev.payload;
      } else if (ev.type === "thought" && ev.agent) {
        thought[ev.agent] = ev.payload.text;
      } else if (ev.type === "tool_call" && ev.agent) {
        tool[ev.agent] = ev.payload.tool;
      }
    }
    return {
      resolvedByAgent: resolved,
      latestThought: thought,
      latestTool: tool,
    };
  }, [events]);

  if (!inProgress && Object.keys(resolvedByAgent).length === 0) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="space-y-2 rounded border border-grab-green/30 bg-black/70 p-3 backdrop-blur"
    >
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wider text-grab-green">
          {inProgress ? "race in progress" : "race complete"}
        </div>
        <div className="font-mono text-xs tabular-nums text-white/50">
          {elapsed.toFixed(1)}s
        </div>
      </div>
      {AGENTS.map((agent) => {
        const resolved = resolvedByAgent[agent.name];
        const thought = latestThought[agent.name];
        const tool = latestTool[agent.name];
        return (
          <div
            key={agent.name}
            className="flex items-center gap-2 text-[11px]"
          >
            <span
              aria-hidden="true"
              className={cn("font-mono", agent.colour)}
            >
              ●
            </span>
            <span className="text-white/80">{agent.name}</span>
            <span className="truncate text-white/30">— {agent.label}</span>
            <span className="ml-auto max-w-[12rem] truncate font-mono text-[10px] text-white/40">
              {resolved
                ? `resolved · ${resolved.pois.length} stops`
                : thought
                  ? thought
                  : tool
                    ? `→ ${tool}`
                    : "…"}
            </span>
            <span
              aria-label={
                resolved ? "plan resolved" : "waiting on plan"
              }
              className={cn(
                "font-mono text-[11px]",
                resolved ? "text-grab-green" : "text-white/25",
              )}
            >
              {resolved ? "✓" : "·"}
            </span>
          </div>
        );
      })}
    </div>
  );
}
