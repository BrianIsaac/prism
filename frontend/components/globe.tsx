"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// react-globe.gl must be dynamic because WebGL needs `window`.
// Per `bundle-dynamic-imports`: heavy WebGL scene is only loaded client-side,
// keeps the initial bundle small and avoids SSR crashes on `window`.
const Globe = dynamic(
  () => import("react-globe.gl").then((m) => m.default),
  {
    ssr: false,
    loading: () => (
      <div className="text-white/30 p-8" role="status" aria-live="polite">
        loading globe…
      </div>
    ),
  },
);

// Natural Earth 110m admin_0_countries (~840 KB), self-hosted in public/.
const COUNTRIES_URL = "/countries.geojson";

// Earth-at-night texture for idle view: self-hosted 8K variant (~3 MB,
// 8192×4096) from Solar System Scope (CC-BY 4.0). Night earth is the
// default aesthetic — city lights sell the "global activity" story.
const NIGHT_TEXTURE = "/earth-night-8k.jpg";

// Blue Marble daytime, downsampled to 16K (16384×8192, ~15 MB) from NASA
// 21K source. 16K is WebGL's MAX_TEXTURE_SIZE on most desktop GPUs — any
// larger fails to upload. Used when zoomed into a route as the fallback
// base while satellite tiles stream in.
const DAY_TEXTURE = "/earth-day-16k.jpg";

// ESRI World Imagery tiles. URL schema is `{z}/{y}/{x}` (y/x swapped vs
// OSM). react-globe.gl's tile engine drives camera-based LOD — it calls
// this function with (x, y, level) and fetches only what the current zoom
// needs, giving city-block resolution when zoomed into a route. The tile
// engine was silently broken by two copies of three.js in the dep tree
// (three@0.170 vs three@0.183 had incompatible Camera class identities);
// `overrides` in package.json now pins every copy to one version.
const TILE_ENGINE_URL = (x: number, y: number, l: number): string =>
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${l}/${y}/${x}`;


// Hoisted accessor/colour functions — stable refs per
// `rerender-memo-with-default-value`: non-primitive prop values should not be
// re-allocated on every parent render. react-globe.gl reads these on every
// data item, so ref stability matters.
const POLYGON_CAP_COLOUR = () => "rgba(255,255,255,0.02)";
const POLYGON_SIDE_COLOUR = () => "rgba(0,0,0,0)";
const POLYGON_STROKE_COLOUR = () => "rgba(255,255,255,0.12)";
// White ripples pop against the Grab-green atmosphere without washing out.
const RING_COLOUR = () => (t: number) => `rgba(255, 255, 255, ${1 - t})`;

export interface GlobePoint {
  lat: number;
  lng: number;
  colour: string;
  label?: string;
}

export interface GlobeArc {
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  colour: string;
}

export interface GlobeRing {
  lat: number;
  lng: number;
}

export interface PrismGlobeProps {
  pointsData: GlobePoint[];
  arcsData: GlobeArc[];
  ringsData: GlobeRing[];
  onPolygonClick?: (iso3: string) => void;
  // When non-null / non-empty the camera flies to the bounding box of these
  // points with altitude fitted to the span. Passing null or an empty array
  // restores auto-rotate. Collapsing to a single point uses a tight default.
  focusPoints?: ReadonlyArray<{ lat: number; lng: number }> | null;
}

interface CountryFeature {
  type: "Feature";
  properties: { ISO_A3?: string; ADM0_A3?: string; NAME?: string };
  geometry: unknown;
}

interface Size {
  width: number;
  height: number;
}

/** Fit a set of lat/lng points to a camera pose.
 *
 * Returns `{lat, lng, altitude}` where ``lat/lng`` is the bounding-box centre
 * and ``altitude`` is calibrated so the route — not the whole city — fills
 * the frame. react-globe.gl's altitude is in globe-radii: altitude ≈ span
 * in degrees gives a snug frame with minimal margin. The floor 0.008 stops
 * a single-point plan from asking for sub-pixel altitudes that some GPUs
 * dislike; the ceiling 0.35 keeps continent-spanning plans (unlikely) on
 * screen.
 */
function fitFocus(
  points: ReadonlyArray<{ lat: number; lng: number }>,
): { lat: number; lng: number; altitude: number } | null {
  if (points.length === 0) return null;
  let minLat = points[0].lat;
  let maxLat = points[0].lat;
  let minLng = points[0].lng;
  let maxLng = points[0].lng;
  for (const p of points) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lng < minLng) minLng = p.lng;
    if (p.lng > maxLng) maxLng = p.lng;
  }
  const spanLat = maxLat - minLat;
  const spanLng = maxLng - minLng;
  const span = Math.max(spanLat, spanLng, 0.003);
  // Empirical calibration: react-globe.gl renders with a wide FOV (~70°), so
  // altitude ≈ span × 0.08 frames a route with roughly 4× the bbox as
  // visible margin. A 3 km route (span 0.026°) lands at altitude ~0.002
  // showing a ~15 km frame — route + immediate neighbourhood, no wider.
  // Floor at 0.0015 to avoid the camera clipping into the surface.
  const altitude = Math.min(0.35, Math.max(0.0015, span * 0.08));
  return {
    lat: (minLat + maxLat) / 2,
    lng: (minLng + maxLng) / 2,
    altitude,
  };
}

export function PrismGlobe({
  pointsData,
  arcsData,
  ringsData,
  onPolygonClick,
  focusPoints = null,
}: PrismGlobeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const globeRef = useRef<unknown>(null);
  const [countries, setCountries] = useState<CountryFeature[]>([]);
  // `size` seeds at {0,0} and is populated on the first ResizeObserver tick.
  // The globe is only mounted once size is non-zero so we never ask
  // react-globe.gl to render a 0×0 canvas. A `useState` object is fine here
  // because width and height always update together — per
  // `rerender-split-combined-hooks` we only split when dependencies diverge.
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });

  // Measure the container so the globe canvas is constrained to its parent
  // instead of spilling across the full viewport (react-globe.gl's default
  // when width/height are omitted is `window.innerWidth/innerHeight`, which
  // was overpainting the right panel).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      setSize({
        width: Math.max(0, Math.floor(rect.width)),
        height: Math.max(0, Math.floor(rect.height)),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    // Cleanup on unmount so we do not leak the observer (single-instance
    // component today, but the discipline matters for future hot reloads).
    return () => observer.disconnect();
  }, []);

  // Fetch the country polygons once on mount.
  useEffect(() => {
    let cancelled = false;
    fetch(COUNTRIES_URL)
      .then((r) => r.json())
      .then((d: { features: CountryFeature[] }) => {
        if (!cancelled) setCountries(d.features || []);
      })
      .catch(() => {
        if (!cancelled) setCountries([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const hasCountries = countries.length > 0;

  // Single source of truth for camera motion: one effect owns both
  // auto-rotate and pointOfView so neither can race the other.
  //
  // Idle (no focus): auto-rotate at 0.25 rad/s.
  // Focused:         disable auto-rotate and fly in to the bbox-fitted
  //                  camera pose (see `fitFocus`). Transition is 1400ms.
  //
  // Deps are derived primitives (lat/lng/altitude) per `rerender-dependencies`
  // so the effect does not re-run on unrelated parent re-renders.
  // `hasCountries` ensures the first rotate or fly waits until the globe
  // ref is usable.
  const focus = useMemo(
    () => (focusPoints && focusPoints.length > 0 ? fitFocus(focusPoints) : null),
    [focusPoints],
  );
  const focusLat = focus?.lat ?? null;
  const focusLng = focus?.lng ?? null;
  const focusAltitude = focus?.altitude ?? null;
  // Pin + arc geometry all scale in globe-radius units, so a fixed value
  // that's a fine dot at idle altitude ~2.5 balloons into a skyscraper-
  // scale cylinder at focus altitude ~0.002. Scale every dimension
  // proportional to focus altitude, with floors tuned so a city-level
  // zoom still renders clickable markers rather than sub-pixel noise.
  const pointRadius = focusAltitude
    ? Math.max(0.0005, focusAltitude * 0.05)
    : 0.35;
  const pointAltitude = focusAltitude
    ? Math.max(0.00005, focusAltitude * 0.02)
    : 0.015;
  const arcStroke = focusAltitude
    ? Math.max(0.002, focusAltitude * 0.3)
    : 0.7;
  const arcAltitudeAutoScale = focusAltitude
    ? Math.max(0.003, focusAltitude * 0.3)
    : 0.4;
  useEffect(() => {
    const g = globeRef.current as
      | {
          pointOfView: (
            coords: { lat: number; lng: number; altitude: number },
            ms: number,
          ) => void;
          controls: () => { autoRotate: boolean; autoRotateSpeed: number };
        }
      | null;
    if (!g || !g.controls || !g.pointOfView) return;
    try {
      const controls = g.controls();
      if (focusLat === null || focusLng === null || focusAltitude === null) {
        // Deselect: fly back out to the default full-earth altitude. Keep
        // auto-rotate OFF during the 1200ms tween, then re-enable it once
        // the camera has settled — otherwise OrbitControls fights the
        // pointOfView interpolation and the globe visibly wobbles.
        controls.autoRotate = false;
        g.pointOfView({ lat: 0, lng: 0, altitude: 2.5 }, 1200);
        const handle = window.setTimeout(() => {
          try {
            const c = g.controls();
            c.autoRotate = true;
            c.autoRotateSpeed = 0.25;
          } catch {
            // ref torn down before timeout fired — no-op
          }
        }, 1250);
        return () => window.clearTimeout(handle);
      }
      controls.autoRotate = false;
      g.pointOfView(
        { lat: focusLat, lng: focusLng, altitude: focusAltitude },
        1400,
      );
    } catch {
      // controls not yet initialised — next re-render will retry
    }
  }, [focusLat, focusLng, focusAltitude, hasCountries]);

  // Stable callback ref so react-globe.gl does not re-initialise its handler
  // on every parent re-render.
  const handlePolygonClick = useCallback(
    (feat: object) => {
      const f = feat as CountryFeature;
      const iso3 = f.properties?.ISO_A3 || f.properties?.ADM0_A3;
      if (iso3 && onPolygonClick) onPolygonClick(iso3);
    },
    [onPolygonClick],
  );

  const hasSize = size.width > 0 && size.height > 0;

  return (
    <div ref={containerRef} className="w-full h-full relative">
      {hasSize ? (
        <Globe
          ref={globeRef as never}
          width={size.width}
          height={size.height}
          globeImageUrl={focus ? DAY_TEXTURE : NIGHT_TEXTURE}
          globeTileEngineUrl={focus ? TILE_ENGINE_URL : undefined}
          backgroundColor="rgba(0,0,0,0)"
          atmosphereColor="#00B14F"
          atmosphereAltitude={0.2}
          pointsData={pointsData}
          pointLat="lat"
          pointLng="lng"
          pointColor="colour"
          pointAltitude={pointAltitude}
          pointRadius={pointRadius}
          pointLabel="label"
          arcsData={arcsData}
          arcStartLat="startLat"
          arcStartLng="startLng"
          arcEndLat="endLat"
          arcEndLng="endLng"
          arcColor="colour"
          arcAltitudeAutoScale={arcAltitudeAutoScale}
          arcStroke={arcStroke}
          arcDashLength={0.4}
          arcDashGap={0.2}
          arcDashAnimateTime={1500}
          ringsData={ringsData}
          ringLat="lat"
          ringLng="lng"
          ringColor={RING_COLOUR}
          ringMaxRadius={5}
          ringPropagationSpeed={2}
          ringRepeatPeriod={700}
          polygonsData={focus ? [] : (countries as unknown as object[])}
          polygonCapColor={POLYGON_CAP_COLOUR}
          polygonSideColor={POLYGON_SIDE_COLOUR}
          polygonStrokeColor={POLYGON_STROKE_COLOUR}
          polygonAltitude={0.005}
          onPolygonClick={handlePolygonClick}
        />
      ) : null}
    </div>
  );
}
