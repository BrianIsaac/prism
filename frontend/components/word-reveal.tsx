"use client";

import { Fragment, useMemo } from "react";

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
 *
 * The inter-word space is rendered outside each ``inline-block`` span so
 * the browser's whitespace collapser does not strip it — trailing
 * whitespace inside an ``inline-block`` is treated as edge whitespace and
 * removed, which concatenates words into one run ("Aphotogenichalf-day").
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
        <Fragment key={`${i}-${word}`}>
          <span
            style={{ animationDelay: `${i * staggerMs}ms` }}
            className="inline-block opacity-0 motion-safe:animate-fade-in-up motion-reduce:opacity-100"
          >
            {word}
          </span>
          {i < words.length - 1 ? " " : null}
        </Fragment>
      ))}
    </p>
  );
}
