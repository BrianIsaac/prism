"use client";

import maplibregl, { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Base map style proxied through the FastAPI backend so the GrabMaps Bearer
// key never leaves the server. Phase 7 mounts the proxy; until it lands the
// browser will receive whatever the backend returns at this path (a real
// style.json or a 404). On 404 we fall back to a minimal blank style so the
// rest of the canvas still mounts and overlays remain testable.
const STYLE_URL = `${API_BASE}/grabmaps-proxy/style.json?theme=satellite`;

// Singapore civic centre — the canonical demo viewport.
const SINGAPORE: [number, number] = [103.8198, 1.3521];

// Initial zoom is intentionally wider than the steady-state target so that
// the page mount completes a `flyTo` to zoom 11 over 800ms — perceived
// continuity with the globe intro's pull-in.
const INITIAL_ZOOM = 6;
const TARGET_ZOOM = 11;
const FLY_TO_MS = 800;

interface LiveCanvasContextValue {
  map: MapLibreMap | null;
  ready: boolean;
}

const LiveCanvasContext = createContext<LiveCanvasContextValue>({
  map: null,
  ready: false,
});

/** Layers/overlays grab the live MapLibre instance via this hook. */
export function useLiveCanvas(): LiveCanvasContextValue {
  return useContext(LiveCanvasContext);
}

export interface LiveCanvasProps {
  children?: ReactNode;
}

const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  name: "prism-fallback",
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#0c1015" },
    },
  ],
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
};

/**
 * The Live Canvas: a MapLibre map locked to Singapore, styled with Grab's
 * branded satellite tiles via a backend proxy. Children mounted inside this
 * component receive the live map instance through `useLiveCanvas()` and are
 * mounted only after the map's `load` event has fired.
 *
 * Mount-once + ref ownership of the imperative map instance is the canonical
 * MapLibre pattern (per `vercel-react-best-practices` mount-once / cleanup).
 */
export function LiveCanvas({ children }: LiveCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState<boolean>(false);
  const [styleError, setStyleError] = useState<string | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;

    let cancelled = false;
    let map: MapLibreMap | null = null;

    const bootstrap = async (): Promise<void> => {
      let style: StyleSpecification | string = STYLE_URL;
      try {
        const res = await fetch(STYLE_URL);
        if (!res.ok) throw new Error(`style ${res.status}`);
        style = (await res.json()) as StyleSpecification;
      } catch (err) {
        if (cancelled) return;
        setStyleError(
          err instanceof Error ? err.message : "style fetch failed",
        );
        style = FALLBACK_STYLE;
      }
      if (cancelled) return;

      map = new maplibregl.Map({
        container: el,
        style,
        center: SINGAPORE,
        zoom: INITIAL_ZOOM,
        attributionControl: { compact: true },
        // The intro's globe atmosphere already pulled the eye in; suppress
        // MapLibre's logo/attribution flash during hand-off by deferring its
        // own fade animations to the natural style/source loads.
        fadeDuration: 200,
      });

      mapRef.current = map;

      map.on("load", () => {
        if (cancelled || !map) return;
        // Animate to the steady-state zoom so the camera reads as the
        // continuation of the globe pull-in rather than a hard cut.
        map.flyTo({
          center: SINGAPORE,
          zoom: TARGET_ZOOM,
          duration: FLY_TO_MS,
          essential: true,
        });
        setReady(true);
      });
    };

    void bootstrap();

    return () => {
      cancelled = true;
      if (map) {
        map.remove();
      }
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  return (
    <LiveCanvasContext.Provider value={{ map: mapRef.current, ready }}>
      <div className="relative w-full h-full">
        <div
          ref={containerRef}
          role="region"
          aria-label="Singapore live map"
          className="absolute inset-0"
        />
        {styleError ? (
          <div
            role="status"
            className="absolute top-2 left-2 z-30 px-2 py-1 rounded text-[10px] font-mono text-amber-300/80 bg-black/60 border border-amber-300/40"
          >
            style proxy unavailable ({styleError}) — fallback dark base mounted
          </div>
        ) : null}
        {ready ? children : null}
      </div>
    </LiveCanvasContext.Provider>
  );
}
