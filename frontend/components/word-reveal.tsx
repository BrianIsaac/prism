"use client";

import { useMemo } from "react";

export interface WordRevealProps {
  text: string;
  staggerMs?: number;
  className?: string;
}

/**
 * Reveal a line word-by-word with a short opacity + translate fade. The
 * keyframe is defined in `globals.css` (`--animate-fade-in-up`); a `key`
 * tied to the text at the call site retriggers the animation when the
 * source string changes.
 *
 * Honours `prefers-reduced-motion`: under `motion-reduce` the words snap to
 * full opacity instantly.
 */
export function WordReveal({
  text,
  staggerMs = 60,
  className = "",
}: WordRevealProps) {
  const words = useMemo(() => text.split(/\s+/).filter(Boolean), [text]);

  return (
    <p aria-live="polite" className={className}>
      {words.map((word, i) => (
        <span
          key={`${i}-${word}`}
          style={{ animationDelay: `${i * staggerMs}ms` }}
          className="inline-block opacity-0 motion-safe:animate-fade-in-up motion-reduce:opacity-100"
        >
          {word}
          {i < words.length - 1 ? " " : ""}
        </span>
      ))}
    </p>
  );
}
