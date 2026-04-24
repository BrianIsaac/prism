"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import type { Rating } from "@/lib/types";

export interface HitlRatingProps {
  onSubmit: (rating: Rating) => void;
  disabled?: boolean;
}

interface LikertProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}

function Likert({ label, value, onChange, disabled = false }: LikertProps) {
  const id = useId();
  return (
    <div>
      <Label
        htmlFor={id}
        className="mb-1 flex justify-between text-xs text-white/50"
      >
        <span>{label}</span>
        <span className="font-mono tabular-nums">{value}</span>
      </Label>
      <Slider
        id={id}
        disabled={disabled}
        min={1}
        max={5}
        step={1}
        value={[value]}
        onValueChange={(v) => {
          const next = Array.isArray(v) ? v[0] : v;
          if (typeof next === "number") onChange(next);
        }}
        aria-label={`${label} rating, 1 to 5`}
      />
    </div>
  );
}

/**
 * Human-in-the-loop rating surface. Collects three 1–5 Likert scores plus an
 * optional comment. Emits a {@link Rating} through `onSubmit`; the parent is
 * responsible for merging any edited POI list into `pois_override` before
 * POSTing — this component deliberately does not reach into plan state.
 */
export function HitlRating({ onSubmit, disabled = false }: HitlRatingProps) {
  const [novelty, setNovelty] = useState(3);
  const [efficiency, setEfficiency] = useState(3);
  const [vibe, setVibe] = useState(3);
  const [comment, setComment] = useState("");
  const commentId = useId();

  return (
    <div className="space-y-3 rounded border border-white/10 bg-white/[0.02] p-3">
      <div className="text-xs uppercase tracking-wider text-white/40">
        Rate and pin
      </div>
      <Likert
        label="novelty"
        value={novelty}
        onChange={setNovelty}
        disabled={disabled}
      />
      <Likert
        label="efficiency"
        value={efficiency}
        onChange={setEfficiency}
        disabled={disabled}
      />
      <Likert
        label="vibe"
        value={vibe}
        onChange={setVibe}
        disabled={disabled}
      />
      <div>
        <Label
          htmlFor={commentId}
          className="mb-1 block text-xs text-white/50"
        >
          comment (optional)
        </Label>
        <Textarea
          id={commentId}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={2}
          maxLength={500}
          placeholder="what made this plan land or miss?"
          className="resize-none text-xs"
          disabled={disabled}
        />
        <div className="text-right font-mono text-[10px] tabular-nums text-white/30">
          {comment.length}/500
        </div>
      </div>
      <Button
        type="button"
        disabled={disabled}
        onClick={() =>
          onSubmit({
            novelty,
            efficiency,
            vibe,
            comment: comment.trim() || undefined,
          })
        }
        className="w-full bg-grab-green/20 border-grab-green/40 text-grab-green hover:bg-grab-green/30"
      >
        pin to globe
      </Button>
    </div>
  );
}
