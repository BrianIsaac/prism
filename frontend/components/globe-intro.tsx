"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

// react-globe.gl needs `window` for WebGL — dynamic + ssr:false keeps it out
// of the SSR bundle and the initial JS payload (per `bundle-dynamic-imports`).
const Globe = dynamic(
  () => import("react-globe.gl").then((m) => m.default),
  { ssr: false, loading: () => null },
);

// Self-hosted 8K Earth-at-night JPEG (CC-BY 4.0, Solar System Scope). Sits in
// /public/. City-lights are the storytelling cue: "global activity".
const NIGHT_TEXTURE = "/earth-night-8k.jpg";

// Coordinates of the hand-off camera target: Singapore. The hand-off is a
// purely visual cue — the canvas itself recentres on Singapore independently.
const SINGAPORE = { lat: 1.3521, lng: 103.8198 };

const INTRO_DURATION_MS = 2000;
const FADE_MS = 350;

export interface GlobeIntroProps {
  onComplete: () => void;
}

interface Size {
  width: number;
  height: number;
}

/**
 * Two-second cold-open. Auto-rotates an earth-at-night globe, then flies the
 * camera in towards Singapore and fades the canvas out. Calls `onComplete`
 * once the fade finishes so the parent can swap in the live MapLibre canvas.
 *
 * Honours `prefers-reduced-motion`: the globe still mounts (the 2s window is
 * the demo hook) but skips the fly-in pose change and shortens the fade.
 */
export function GlobeIntro({ onComplete }: GlobeIntroProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const globeRef = useRef<unknown>(null);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const [fading, setFading] = useState<boolean>(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = (): void => {
      const rect = el.getBoundingClientRect();
      setSize({
        width: Math.max(0, Math.floor(rect.width)),
        height: Math.max(0, Math.floor(rect.height)),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const flyIn = window.setTimeout(() => {
      const g = globeRef.current as
        | {
            pointOfView?: (
              coords: { lat: number; lng: number; altitude: number },
              ms: number,
            ) => void;
            controls?: () => { autoRotate: boolean; autoRotateSpeed: number };
          }
        | null;
      try {
        if (g?.controls) g.controls().autoRotate = false;
        g?.pointOfView?.(
          { lat: SINGAPORE.lat, lng: SINGAPORE.lng, altitude: 0.6 },
          FADE_MS + 200,
        );
      } catch {
        // Ref torn down before flyIn fired — ignore.
      }
      setFading(true);
    }, INTRO_DURATION_MS - FADE_MS);

    const handoff = window.setTimeout(onComplete, INTRO_DURATION_MS);

    return () => {
      window.clearTimeout(flyIn);
      window.clearTimeout(handoff);
    };
  }, [onComplete]);

  useEffect(() => {
    const g = globeRef.current as
      | { controls?: () => { autoRotate: boolean; autoRotateSpeed: number } }
      | null;
    if (!g?.controls) return;
    try {
      const c = g.controls();
      c.autoRotate = true;
      c.autoRotateSpeed = 1.4;
    } catch {
      // Controls not yet initialised — first paint will retry on next render.
    }
  }, [size.width, size.height]);

  const hasSize = size.width > 0 && size.height > 0;

  return (
    <div
      ref={containerRef}
      aria-hidden="true"
      className="absolute inset-0 transition-opacity"
      style={{
        opacity: fading ? 0 : 1,
        transitionDuration: `${FADE_MS}ms`,
        pointerEvents: "none",
      }}
    >
      {hasSize ? (
        <Globe
          ref={globeRef as never}
          width={size.width}
          height={size.height}
          globeImageUrl={NIGHT_TEXTURE}
          backgroundColor="rgba(0,0,0,0)"
          atmosphereColor="#00B14F"
          atmosphereAltitude={0.22}
          showAtmosphere
          enablePointerInteraction={false}
        />
      ) : null}
    </div>
  );
}
