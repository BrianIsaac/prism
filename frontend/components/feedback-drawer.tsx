"use client";

import { useId } from "react";
import type { ValidatedPlan } from "@/lib/types";

export type FeedbackStage = "ask" | "answer" | "done" | "dismissed";
export type FeedbackSentiment = "positive" | "neutral" | "negative";

const SENTIMENT_OPTIONS: ReadonlyArray<{
  value: FeedbackSentiment;
  label: string;
  glyph: string;
}> = [
  { value: "positive", label: "loved it", glyph: "♥" },
  { value: "neutral", label: "mixed", glyph: "·" },
  { value: "negative", label: "let-down", glyph: "✕" },
];

export interface FeedbackDrawerProps {
  selected: ValidatedPlan;
  stage: FeedbackStage;
  text: string;
  sentiment: FeedbackSentiment;
  submitting: boolean;
  onStage: (stage: FeedbackStage) => void;
  onText: (text: string) => void;
  onSentiment: (sentiment: FeedbackSentiment) => void;
  onSubmit: () => void;
}

/**
 * Two-step "did you explore this trip?" feedback flow attached to a
 * selected validated plan. Fully controlled — every stage transition flows
 * back to the parent so a fresh row selection can reset the drawer.
 *
 * Stages:
 *   1. ask       — Yes / Not yet
 *   2. answer    — sentiment + textarea
 *   3. done      — thank-you
 *   4. dismissed — "come back later"
 *
 * Only the answer → done path fires `onSubmit`; the dismissed shortcut
 * resolves the drawer without persisting anything.
 */
export function FeedbackDrawer({
  selected,
  stage,
  text,
  sentiment,
  submitting,
  onStage,
  onText,
  onSentiment,
  onSubmit,
}: FeedbackDrawerProps) {
  const textareaId = useId();

  return (
    <div
      className="p-3 border border-white/10 rounded bg-white/[0.02] space-y-2"
      aria-live="polite"
    >
      <div className="text-xs uppercase tracking-wider text-white/40">
        Feedback · {selected.agent_name ?? "—"}
      </div>
      {stage === "ask" ? (
        <>
          <p className="text-xs text-white/70">Did you explore this trip?</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => onStage("answer")}
              className="flex-1 py-1 bg-grab-green/20 border border-grab-green/40 text-grab-green rounded text-xs hover:bg-grab-green/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 transition-colors"
            >
              Yes
            </button>
            <button
              type="button"
              onClick={() => onStage("dismissed")}
              className="flex-1 py-1 bg-white/5 border border-white/20 text-white/70 rounded text-xs hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 transition-colors"
            >
              Not yet
            </button>
          </div>
        </>
      ) : null}
      {stage === "answer" ? (
        <>
          <div role="radiogroup" aria-label="how was it" className="flex gap-1">
            {SENTIMENT_OPTIONS.map((opt) => {
              const active = sentiment === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => onSentiment(opt.value)}
                  className={`flex-1 py-1 rounded text-[11px] border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 ${
                    active
                      ? "bg-grab-green/20 border-grab-green/40 text-grab-green"
                      : "bg-white/5 border-white/10 text-white/60 hover:bg-white/10"
                  }`}
                >
                  <span aria-hidden="true">{opt.glyph}</span>{" "}
                  <span>{opt.label}</span>
                </button>
              );
            })}
          </div>
          <label htmlFor={textareaId} className="block text-xs text-white/70">
            What did you enjoy about this trip?
          </label>
          <textarea
            id={textareaId}
            value={text}
            onChange={(e) => onText(e.target.value)}
            rows={3}
            maxLength={500}
            autoFocus
            className="w-full p-2 bg-white/5 border border-white/10 rounded text-xs resize-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 focus-visible:border-white/30"
          />
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-white/30 tabular-nums">
              {text.length}/500
            </span>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onSubmit}
              disabled={!text.trim() || submitting}
              className="flex-1 py-1 bg-grab-green/20 border border-grab-green/40 text-grab-green rounded text-xs hover:bg-grab-green/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? "saving…" : "submit"}
            </button>
            <button
              type="button"
              onClick={() => onStage("ask")}
              className="px-3 py-1 bg-white/5 border border-white/20 text-white/60 rounded text-xs hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 transition-colors"
            >
              back
            </button>
          </div>
        </>
      ) : null}
      {stage === "done" ? (
        <p className="text-xs text-grab-green/80">
          Thanks — saved to the harness KB. Future races read recent feedback
          as context.
        </p>
      ) : null}
      {stage === "dismissed" ? (
        <p className="text-xs text-white/60">
          No worries — come back once you have tried it and leave a note.
        </p>
      ) : null}
    </div>
  );
}
