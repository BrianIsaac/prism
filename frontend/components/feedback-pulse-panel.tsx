"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchFeedbackDigest,
  rebuildFeedbackDigest,
} from "@/lib/api-client";
import type { Feedback, FeedbackDigest, FeedbackTag } from "@/lib/types";

// Sentiment → Grab-family tone. Hoisted so the record is ref-stable across
// renders; defaulting a non-primitive prop inline re-triggers memoisation.
const SENTIMENT_TINT: Record<Feedback["sentiment"], string> = {
  positive: "text-grab-green",
  neutral: "text-white/60",
  negative: "text-red-400/80",
};

/**
 * Map a tag's count into a pixel size. Proportional sizing ("size ∝ count")
 * is a v2 Phase 6 addition — v1 rendered every chip at the same scale, which
 * masked how reinforced the top tags actually were.
 */
function sizeForTag(count: number, maxCount: number): {
  fontPx: number;
  paddingX: number;
} {
  if (maxCount <= 0) return { fontPx: 11, paddingX: 8 };
  const ratio = Math.min(1, Math.max(0, count / maxCount));
  return {
    fontPx: 11 + ratio * 4,
    paddingX: 8 + ratio * 4,
  };
}

/**
 * Three-section view of the Karpathy-style feedback KB:
 *   1. Room taste — latest digest summary + source count + model.
 *   2. Hot tags   — tag/count chips sized proportional to count (v2).
 *   3. Recent     — compact tail of raw feedback rows.
 *
 * Agents read section 1 + 2 as ambient context at race start; section 3 is
 * there for the operator to sanity-check that the digest actually reflects
 * the signal. The rebuild button triggers the distillation LLM call manually.
 */
export function FeedbackPulsePanel() {
  const [digest, setDigest] = useState<FeedbackDigest | null>(null);
  const [rawTail, setRawTail] = useState<Feedback[]>([]);
  const [historyCount, setHistoryCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const mountedRef = useRef(true);

  const pull = useCallback(async () => {
    try {
      const data = await fetchFeedbackDigest();
      if (!mountedRef.current) return;
      setDigest(data.digest);
      setRawTail(data.raw_tail);
      setHistoryCount(data.history.length);
      setError(null);
    } catch (e) {
      if (!mountedRef.current) return;
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    pull();
    const id = setInterval(pull, 5000);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [pull]);

  const handleRebuild = useCallback(async () => {
    setRebuilding(true);
    setError(null);
    try {
      await rebuildFeedbackDigest();
    } catch (e) {
      if (mountedRef.current) setError((e as Error).message);
    } finally {
      if (mountedRef.current) setRebuilding(false);
    }
    // Refresh even if the rebuild threw — if the corpus is empty the endpoint
    // returns 400, but new raw rows may have arrived mid-rebuild.
    if (mountedRef.current) await pull();
  }, [pull]);

  const maxTagCount = useMemo<number>(() => {
    if (!digest?.tags.length) return 0;
    return digest.tags.reduce(
      (acc: number, t: FeedbackTag) => Math.max(acc, t.count),
      0,
    );
  }, [digest]);

  return (
    <section
      aria-live="polite"
      className="p-4 border border-white/10 rounded bg-white/[0.02]"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm uppercase tracking-wider text-white/60">
          Feedback KB
        </h2>
        <div className="flex items-center gap-2 text-xs font-mono text-white/40 tabular-nums">
          <span>{rawTail.length} raw</span>
          <span>·</span>
          <span>{historyCount} digests</span>
          <button
            type="button"
            onClick={handleRebuild}
            disabled={rebuilding}
            className="ml-2 px-2 py-0.5 bg-grab-green/10 border border-grab-green/30 text-grab-green rounded hover:bg-grab-green/20 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-grab-green/60 transition-colors"
          >
            {rebuilding ? "…" : "rebuild"}
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="mt-2 text-xs text-red-400/70">
          {error}
        </div>
      )}

      <div className="mt-3 flex flex-col gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-white/40 mb-1">
            room taste
          </div>
          {digest ? (
            <>
              <p className="text-xs text-white/85 text-pretty leading-relaxed">
                {digest.summary}
              </p>
              <div className="mt-1 text-[10px] text-white/30 font-mono tabular-nums">
                distilled from {digest.source_count} rows ·{" "}
                {digest.model.split("-")[0] || digest.model} ·{" "}
                {digest.created_at && digest.created_at.length >= 19
                  ? digest.created_at.slice(11, 19)
                  : "—"}
              </div>
            </>
          ) : (
            <p className="text-xs text-white/30 italic">
              first digest builds automatically after three feedback
              submissions.
            </p>
          )}
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-white/40 mb-1">
            hot tags
          </div>
          {digest && digest.tags.length > 0 ? (
            <div className="flex flex-wrap items-baseline gap-1">
              {digest.tags.map((t) => {
                const { fontPx, paddingX } = sizeForTag(t.count, maxTagCount);
                return (
                  <span
                    key={t.tag}
                    className="rounded-full bg-grab-green/15 border border-grab-green/30 text-grab-green font-mono tabular-nums"
                    style={{
                      fontSize: `${fontPx.toFixed(2)}px`,
                      paddingLeft: `${paddingX.toFixed(2)}px`,
                      paddingRight: `${paddingX.toFixed(2)}px`,
                      paddingTop: "2px",
                      paddingBottom: "2px",
                    }}
                  >
                    {t.tag} ×{t.count}
                  </span>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-white/30 italic">
              no reinforced themes yet — more signal needed.
            </p>
          )}
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-white/40 mb-1">
            recent raw
          </div>
          {rawTail.length === 0 ? (
            <p className="text-xs text-white/30 italic">
              no raw feedback yet. Like a validated trip and leave a note to
              seed the corpus.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {rawTail.slice(0, 10).map((r) => (
                <li
                  key={r.id}
                  className="p-2 border border-white/5 rounded bg-white/[0.01]"
                >
                  <div className="text-[11px] text-white/75 text-pretty">
                    {r.response}
                  </div>
                  <div className="mt-1 flex gap-2 text-[10px] font-mono tabular-nums text-white/30">
                    <span className={SENTIMENT_TINT[r.sentiment]}>
                      {r.sentiment}
                    </span>
                    <span>
                      {r.created_at && r.created_at.length >= 16
                        ? r.created_at.slice(0, 16)
                        : "—"}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <p className="mt-4 text-[10px] text-white/30 leading-relaxed">
        Every three feedback entries the KB re-distills via a Claude Haiku
        call (Karpathy &quot;LLM Wiki&quot; pattern): the summary above plus
        the tags are injected as ambient context into every subsequent race,
        not the raw rows. Judges can watch the digest evolve as more trips
        get validated.
      </p>
    </section>
  );
}
