"use client";

import { useEffect, useRef, useState } from "react";

// Self-hosted 8K Earth-at-night JPEG (CC-BY 4.0, Solar System Scope). Sits in
// /public/. City-lights are the storytelling cue: "global activity".
const NIGHT_TEXTURE = "/earth-night-8k.jpg";

const INTRO_DURATION_MS = 12000;
const FADE_MS = 600;

export interface GlobeIntroProps {
  onComplete: () => void;
}

/**
 * Demo cold-open: a rotating night-side Earth with a Grab-green atmosphere
 * halo. Auto-dismisses after 12 seconds or on first click; callers can also
 * replay by remounting (pass a changing React key).
 *
 * This is a deliberately lightweight CSS + image composition rather than a
 * WebGL globe — it hits zero-frame-budget on cold start, has no third-party
 * library dependencies, and its auto-rotation reads identically to the
 * three.js variant at the 12-second demo cadence.
 */
export function GlobeIntro({ onComplete }: GlobeIntroProps) {
  const [fading, setFading] = useState<boolean>(false);
  const completedRef = useRef<boolean>(false);

  const dismiss = (): void => {
    if (completedRef.current) return;
    completedRef.current = true;
    setFading(true);
    window.setTimeout(onComplete, FADE_MS);
  };

  useEffect(() => {
    const auto = window.setTimeout(dismiss, INTRO_DURATION_MS);
    return () => window.clearTimeout(auto);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Globe intro — click to skip"
      onClick={dismiss}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") dismiss();
      }}
      className="absolute inset-0 grid place-items-center cursor-pointer bg-black transition-opacity overflow-hidden"
      style={{
        opacity: fading ? 0 : 1,
        transitionDuration: `${FADE_MS}ms`,
      }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at center, rgba(0,177,79,0.18) 0%, rgba(0,0,0,0) 55%)",
        }}
      />
      <div
        aria-hidden="true"
        className="relative rounded-full"
        style={{
          width: "min(70vmin, 560px)",
          height: "min(70vmin, 560px)",
          backgroundImage: `url(${NIGHT_TEXTURE})`,
          backgroundSize: "200% 100%",
          backgroundRepeat: "repeat-x",
          animation: "prism-globe-spin 40s linear infinite",
          boxShadow:
            "0 0 0 1px rgba(255,255,255,0.08), 0 0 80px 10px rgba(0,177,79,0.35), inset -30px -30px 80px 20px rgba(0,0,0,0.85)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute rounded-full"
        style={{
          width: "min(72vmin, 580px)",
          height: "min(72vmin, 580px)",
          boxShadow:
            "0 0 60px 8px rgba(0,177,79,0.45), inset 0 0 80px 10px rgba(0,177,79,0.15)",
          border: "1px solid rgba(0,177,79,0.4)",
        }}
      />
      <div
        className="absolute bottom-8 left-1/2 -translate-x-1/2 text-[11px] tracking-[0.3em] uppercase text-white/60 pointer-events-none select-none"
        aria-hidden="true"
      >
        Prism · click anywhere to enter
      </div>
      <style jsx>{`
        @keyframes prism-globe-spin {
          0% {
            background-position: 0% 50%;
          }
          100% {
            background-position: -200% 50%;
          }
        }
      `}</style>
    </div>
  );
}
