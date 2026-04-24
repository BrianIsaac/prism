"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Sparkline } from "@/components/sparkline";
import { fetchLiveFeed } from "@/lib/api-client";
import type {
  LiveFeedCategory,
  LiveFeedCounts,
  RaceAgentName,
} from "@/lib/types";

// 2s tick × 450 samples = 15 minutes of live feed on screen.
const POLL_MS = 2000;
const MAX_SAMPLES = 450;

// The five judge-facing categories in fixed stacking order. "other" is also
// returned by `/admin/live-feed` but is not shown — every sparkline on this
// panel must map back to a named Grab endpoint for the demo narration to
// hold ("every bar is a real Grab endpoint"). Any drift into "other" is
// visible via the total-calls line going up without the five bars reacting.
const CATEGORIES: ReadonlyArray<{
  key: Exclude<LiveFeedCategory, "other">;
  label: string;
  colour: string;
  hint: string;
}> = [
  {
    key: "search",
    label: "Search",
    colour: "#60a5fa",
    hint: "places · nearby · reverse-geo",
  },
  {
    key: "routing",
    label: "Routing",
    colour: "#00b14f",
    hint: "direction · route_matrix",
  },
  {
    key: "traffic",
    label: "Traffic",
    colour: "#f59e0b",
    hint: "get_traffic",
  },
  {
    key: "incidents",
    label: "Incidents",
    colour: "#ef4444",
    hint: "get_incidents",
  },
  {
    key: "streetview",
    label: "Street-view",
    colour: "#a78bfa",
    hint: "get_street_view",
  },
];

type CategoryKey = (typeof CATEGORIES)[number]["key"];

type Buffer = Record<CategoryKey, number[]>;

// Three named racers, colour-matched to `agent-race-panel.tsx`. "other"
// is intentionally hidden — any drift into it bumps the total line without
// a matching agent bar, which is visible by comparison.
const AGENT_KEYS: ReadonlyArray<{
  key: Exclude<RaceAgentName, "other">;
  label: string;
  model: string;
  colour: string;
}> = [
  {
    key: "opus",
    label: "opus",
    model: "Claude Opus 4.7",
    colour: "#ef4444",
  },
  {
    key: "gpt",
    label: "gpt",
    model: "OpenAI GPT 5.5",
    colour: "#00b14f",
  },
  {
    key: "gemini",
    label: "gemini",
    model: "Gemini 3.1 Pro",
    colour: "#60a5fa",
  },
];

type AgentKey = (typeof AGENT_KEYS)[number]["key"];

function emptyBuffer(): Buffer {
  return {
    search: [],
    routing: [],
    traffic: [],
    incidents: [],
    streetview: [],
  };
}

function emptyAgentBuffer(): Record<AgentKey, number[]> {
  return { opus: [], gpt: [], gemini: [] };
}

export interface LiveFeedPanelProps {
  /**
   * Optional failure percentage computed by the parent from the bug-report
   * endpoint. Rendered in the summary line when available; the live-feed
   * endpoint alone does not carry failure counts.
   */
  failureRatePercent?: number | null;
}

/**
 * Live-feed panel — polls `/admin/live-feed` every two seconds, retains a
 * rolling 15-minute window per category, and renders five stacked sparklines
 * so the operator can point at them during the demo.
 */
export function LiveFeedPanel({
  failureRatePercent = null,
}: LiveFeedPanelProps) {
  const [buffer, setBuffer] = useState<Buffer>(emptyBuffer);
  const [agentBuffer, setAgentBuffer] =
    useState<Record<AgentKey, number[]>>(emptyAgentBuffer);
  const [latest, setLatest] = useState<LiveFeedCounts | null>(null);
  const [error, setError] = useState<string | null>(null);

  // `mountedRef` guards setState against racing the unmount — React 19 strict
  // mode remounts effects during dev, which can otherwise schedule a writeback
  // on a dead component.
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    const pull = async () => {
      try {
        const data = await fetchLiveFeed(60);
        if (!mountedRef.current) return;
        setLatest(data);
        setError(null);
        setBuffer((prev) => {
          const next = emptyBuffer();
          for (const { key } of CATEGORIES) {
            const sample = data.by_category[key] ?? 0;
            const series = prev[key];
            const appended =
              series.length >= MAX_SAMPLES
                ? [...series.slice(series.length - MAX_SAMPLES + 1), sample]
                : [...series, sample];
            next[key] = appended;
          }
          return next;
        });
        setAgentBuffer((prev) => {
          const next = emptyAgentBuffer();
          for (const { key } of AGENT_KEYS) {
            const sample = data.by_agent?.[key] ?? 0;
            const series = prev[key];
            const appended =
              series.length >= MAX_SAMPLES
                ? [...series.slice(series.length - MAX_SAMPLES + 1), sample]
                : [...series, sample];
            next[key] = appended;
          }
          return next;
        });
      } catch (e) {
        if (!mountedRef.current) return;
        setError((e as Error).message);
      }
    };

    pull();
    timer = setInterval(pull, POLL_MS);
    return () => {
      mountedRef.current = false;
      if (timer) clearInterval(timer);
    };
  }, []);

  const summary = useMemo(() => {
    const totalNow = latest?.total_calls ?? 0;
    const failureText =
      failureRatePercent === null || Number.isNaN(failureRatePercent)
        ? null
        : `${failureRatePercent.toFixed(1)}% failure rate`;
    return { totalNow, failureText };
  }, [latest, failureRatePercent]);

  // Current-rate readout per category, pulled off the latest sample so the
  // sparkline and the numeric label never disagree.
  const latestRates = useMemo<Record<CategoryKey, number>>(() => {
    const out = {
      search: 0,
      routing: 0,
      traffic: 0,
      incidents: 0,
      streetview: 0,
    } as Record<CategoryKey, number>;
    if (!latest) return out;
    for (const { key } of CATEGORIES) {
      out[key] = latest.by_category[key] ?? 0;
    }
    return out;
  }, [latest]);

  return (
    <section
      aria-live="polite"
      className="p-4 border border-white/10 rounded bg-white/[0.02]"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm uppercase tracking-wider text-white/60">
          Live Feed
        </h2>
        <span className="text-xs font-mono text-white/40 tabular-nums">
          every 2s · 60s window
        </span>
      </div>

      <p className="mt-2 text-xs text-white/70 font-mono tabular-nums">
        <span className="text-white">{summary.totalNow}</span> calls/min total ·
        last 15 minutes
        {summary.failureText ? (
          <>
            {" · "}
            <span className="text-red-400/80">{summary.failureText}</span>
          </>
        ) : null}
      </p>

      {error && (
        <div role="alert" className="mt-2 text-xs text-red-400/70">
          {error}
        </div>
      )}

      <div className="mt-4 flex flex-col gap-3">
        {CATEGORIES.map((cat) => {
          const series = buffer[cat.key];
          const current = latestRates[cat.key];
          return (
            <div
              key={cat.key}
              className="grid grid-cols-[7rem_1fr_auto] items-center gap-3"
            >
              <div className="flex flex-col">
                <span
                  className="text-xs font-medium"
                  style={{ color: cat.colour }}
                >
                  {cat.label}
                </span>
                <span className="text-[10px] text-white/30 font-mono">
                  {cat.hint}
                </span>
              </div>
              <Sparkline
                values={series}
                colour={cat.colour}
                width={260}
                height={28}
                label={`${cat.label} calls per minute over last ${series.length * (POLL_MS / 1000)} seconds`}
              />
              <span
                className="text-xs font-mono tabular-nums text-white/85"
                style={{ color: cat.colour }}
                aria-label={`${cat.label} current rate ${current} calls per minute`}
              >
                {current}
                <span className="text-[9px] text-white/30 ml-1">/min</span>
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-6 pt-4 border-t border-white/5">
        <div className="flex items-baseline justify-between">
          <h3 className="text-xs uppercase tracking-wider text-white/60">
            By agent
          </h3>
          <span className="text-[10px] font-mono tabular-nums text-white/40">
            avg {(latest?.per_agent_average ?? 0).toFixed(1)}/min ·{" "}
            {latest?.active_agents ?? 0}/3 active
          </span>
        </div>
        <div className="mt-3 flex flex-col gap-3">
          {AGENT_KEYS.map((agent) => {
            const series = agentBuffer[agent.key];
            const current = latest?.by_agent?.[agent.key] ?? 0;
            const mix = latest?.by_agent_category?.[agent.key];
            const topCategory = mix
              ? (Object.entries(mix) as [CategoryKey | "other", number][])
                  .filter(([k]) => k !== "other")
                  .sort((a, b) => b[1] - a[1])[0]
              : null;
            return (
              <div
                key={agent.key}
                className="grid grid-cols-[7rem_1fr_auto] items-center gap-3"
              >
                <div className="flex flex-col">
                  <span
                    className="text-xs font-medium"
                    style={{ color: agent.colour }}
                  >
                    {agent.label}
                  </span>
                  <span className="text-[10px] text-white/30 font-mono">
                    {agent.model}
                  </span>
                </div>
                <Sparkline
                  values={series}
                  colour={agent.colour}
                  width={260}
                  height={28}
                  label={`${agent.label} tool calls per minute`}
                />
                <span
                  className="text-xs font-mono tabular-nums text-right"
                  style={{ color: agent.colour }}
                  aria-label={`${agent.label} current rate ${current} calls per minute`}
                >
                  {current}
                  <span className="text-[9px] text-white/30 ml-1">/min</span>
                  {topCategory && topCategory[1] > 0 ? (
                    <div className="text-[9px] text-white/30 font-mono mt-0.5">
                      mostly {topCategory[0]}
                    </div>
                  ) : null}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <p className="mt-4 text-[10px] text-white/30 leading-relaxed">
        Each bar above is a live count of a real Grab endpoint: places search,
        direction, traffic circle, incidents circle, and OpenStreetCam. No
        mocks, no fixtures — three models, one shared tool belt.
      </p>
    </section>
  );
}
