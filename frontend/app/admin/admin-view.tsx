"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { FeedbackPulsePanel } from "@/components/feedback-pulse-panel";
import { LiveFeedPanel } from "@/components/live-feed-panel";
import { Sparkline } from "@/components/sparkline";
import {
  fetchBugReport,
  fetchWeightHistory,
  fetchWeights,
} from "@/lib/api-client";
import type {
  BugReport,
  WeightHistorySnapshot,
  WeightsResponse,
} from "@/lib/types";

// Sparkline stroke per weight dimension. Hoisted so the record is ref-stable
// across renders; matches the Grab-family palette (flow = primary green,
// diversity = white, vibe = lighter green).
const WEIGHT_COLOURS: Record<string, string> = {
  flow: "#00b14f",
  diversity: "#ffffff",
  vibe: "#27bc6a",
};

const POLL_MS = 5000;

export function AdminView() {
  const [weights, setWeights] = useState<WeightsResponse | null>(null);
  const [history, setHistory] = useState<WeightHistorySnapshot[]>([]);
  const [report, setReport] = useState<BugReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      // `Promise.allSettled` so one failing endpoint (bug-report under load,
      // weight-history before any race has completed) does not blank out the
      // healthy panels. Each slot only updates on its own success; failures
      // are concatenated into the operator-visible error banner.
      const [w, h, r] = await Promise.allSettled([
        fetchWeights(),
        fetchWeightHistory(),
        fetchBugReport(),
      ]);
      if (cancelled) return;
      const failures: string[] = [];
      if (w.status === "fulfilled") setWeights(w.value);
      else failures.push(`weights: ${w.reason}`);
      if (h.status === "fulfilled") setHistory(h.value.snapshots);
      else failures.push(`history: ${h.reason}`);
      if (r.status === "fulfilled") setReport(r.value);
      else failures.push(`bug-report: ${r.reason}`);
      setError(failures.length ? failures.join(" · ") : null);
    };
    pull();
    const id = setInterval(pull, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const failureRatePercent = useMemo<number | null>(() => {
    if (!report || report.total_calls <= 0) return null;
    return (report.failed_calls / report.total_calls) * 100;
  }, [report]);

  return (
    <main className="h-full text-white p-6 overflow-y-auto">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-pretty">
            Prism Admin
          </h1>
          <p className="text-xs text-white/40 text-pretty">
            harness drift · tool-call trace aggregation · feedback KB · live
            every {POLL_MS / 1000}s
          </p>
        </div>
        <Link
          href="/"
          className="text-xs text-white/30 hover:text-white/70 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 rounded"
        >
          back to globe
        </Link>
      </header>

      {error && (
        <div
          role="alert"
          aria-live="polite"
          className="mb-4 p-3 bg-red-500/20 border border-red-500/40 rounded text-xs text-red-200"
        >
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <div className="flex flex-col gap-6">
          <WeightsPanel weights={weights} history={history} />
        </div>
        <div className="flex flex-col gap-6">
          <LiveFeedPanel failureRatePercent={failureRatePercent} />
          <BugReportPanel report={report} />
          <FeedbackPulsePanel />
        </div>
      </div>
    </main>
  );
}

function WeightsPanel({
  weights,
  history,
}: {
  weights: WeightsResponse | null;
  history: WeightHistorySnapshot[];
}) {
  // Memoised so each 5s poll does not allocate fresh `values` arrays for
  // the three drift sparklines — defeats the Sparkline's memo() otherwise.
  const dimensions = useMemo<string[]>(
    () => (weights ? Object.keys(weights.frozen_defaults) : []),
    [weights],
  );
  const historyByDim = useMemo<Record<string, number[]>>(
    () => ({
      flow: history.map((h) => h.flow),
      diversity: history.map((h) => h.diversity),
      vibe: history.map((h) => h.vibe),
    }),
    [history],
  );

  return (
    <section
      aria-live="polite"
      className="p-4 border border-white/10 rounded bg-white/[0.02]"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm uppercase tracking-wider text-white/60">
          Harness Weights
        </h2>
        {weights && (
          <span className="text-xs font-mono text-white/40">
            {weights.harness_version}
          </span>
        )}
      </div>
      {!weights ? (
        <div className="text-xs text-white/30 mt-2">loading…</div>
      ) : (
        <>
          <div className="mt-3 flex flex-col gap-2 font-mono text-xs tabular-nums">
            <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 text-[10px] uppercase tracking-wider text-white/30">
              <span>frozen_defaults vs runtime</span>
              <span className="text-right">frozen</span>
              <span className="text-right">runtime</span>
              <span className="text-right">drift</span>
            </div>
            {dimensions.map((key) => {
              const frozen = weights.frozen_defaults[key];
              const runtime = weights.runtime[key];
              const delta = runtime - frozen;
              const drifted = Math.abs(delta) > 0.0005;
              // Both drift directions stay on-brand: up = primary green,
              // down = lighter green. Meaning stays in the arrow glyph,
              // never the hue, so the panel never reads as an error state.
              const deltaClass = drifted
                ? delta > 0
                  ? "text-grab-green"
                  : "text-grab-light"
                : "text-white/30";
              const sign = delta > 0 ? "+" : "";
              const arrow = drifted ? (delta > 0 ? " ↑" : " ↓") : "";
              return (
                <div
                  key={key}
                  className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 items-baseline"
                >
                  <span className="text-white/70">{key}</span>
                  <span className="text-right text-white/50">
                    {frozen.toFixed(3)}
                  </span>
                  <span
                    className="text-right text-white/85"
                    title="α = 0.02 per HITL rating"
                  >
                    {runtime.toFixed(3)}
                  </span>
                  <span className={`text-right ${deltaClass}`}>
                    {sign}
                    {delta.toFixed(3)}
                    {arrow}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-5 flex flex-col gap-2">
            <div className="text-[10px] uppercase tracking-wider text-white/30">
              drift over time · {history.length} snapshots
            </div>
            {dimensions.map((key) => (
              <div key={key} className="flex items-center gap-3">
                <span className="text-xs text-white/70 font-mono w-20">
                  {key}
                </span>
                <Sparkline
                  values={historyByDim[key] ?? []}
                  width={220}
                  height={22}
                  colour={WEIGHT_COLOURS[key] ?? "#00b14f"}
                  label={`${key} weight drift over ${history.length} snapshots`}
                />
              </div>
            ))}
            {history.length === 0 && (
              <p className="text-[10px] text-white/30 italic">
                no ratings yet — baseline flat.
              </p>
            )}
          </div>
        </>
      )}
      <p className="mt-4 text-[10px] text-white/30 leading-relaxed">
        Runtime weights drift from the frozen_defaults at α = 0.02 per HITL
        rating. The frozen column is immutable and stamped into every race
        for reproducibility.
      </p>
    </section>
  );
}

function BugReportPanel({ report }: { report: BugReport | null }) {
  return (
    <section
      aria-live="polite"
      className="p-4 border border-white/10 rounded bg-white/[0.02]"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm uppercase tracking-wider text-white/60">
          Bug Report
        </h2>
        {report && (
          <span className="text-xs font-mono text-white/40 tabular-nums">
            {report.generated_at.slice(11, 19)}Z
          </span>
        )}
      </div>
      {!report ? (
        <div className="text-xs text-white/30 mt-2">loading…</div>
      ) : (
        <div className="mt-3 flex flex-col gap-3 text-xs">
          <div className="flex gap-6 font-mono tabular-nums">
            <span className="text-white/70">
              total <span className="text-white">{report.total_calls}</span>
            </span>
            <span className="text-red-400/70">
              failed{" "}
              <span className="text-red-400">{report.failed_calls}</span>
            </span>
          </div>
          {report.markdown ? (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words p-3 border border-white/5 rounded bg-black/30 font-mono text-[11px] leading-relaxed text-white/75">
              {report.markdown}
            </pre>
          ) : (
            <p className="text-[11px] text-white/40 italic">
              no markdown report yet — failures will render here as they
              accumulate.
            </p>
          )}
          {report.failed_calls === 0 && (
            <div className="text-grab-green/80 text-xs">
              no failed tool calls yet
            </div>
          )}
        </div>
      )}
    </section>
  );
}
