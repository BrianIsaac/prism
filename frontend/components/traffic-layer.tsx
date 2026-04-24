"use client";

import { useEffect } from "react";
import { useLiveCanvas } from "@/components/live-canvas";

const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const SOURCE_ID = "prism-traffic";
const LAYER_ID = "prism-traffic-layer";

// Backend proxy for the GrabMaps real-time traffic raster tile endpoint.
// MapLibre interpolates {z}/{x}/{y} per visible viewport tile.
const TRAFFIC_TILE_URL = `${API_BASE}/grabmaps-proxy/traffic-raster-tile/{z}/{x}/{y}`;

export interface TrafficLayerProps {
  visible: boolean;
}

/**
 * Toggleable raster traffic overlay. The source is added once per map load
 * and kept resident; the visibility prop just flips the layer's
 * `visibility` layout property so the toggle is instantaneous and the source
 * stays warm in MapLibre's tile cache.
 */
export function TrafficLayer({ visible }: TrafficLayerProps) {
  const { map, ready } = useLiveCanvas();

  useEffect(() => {
    if (!map || !ready) return;

    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, {
        type: "raster",
        tiles: [TRAFFIC_TILE_URL],
        tileSize: 256,
        attribution: "Traffic © Grab",
      });
    }
    if (!map.getLayer(LAYER_ID)) {
      map.addLayer({
        id: LAYER_ID,
        type: "raster",
        source: SOURCE_ID,
        layout: { visibility: visible ? "visible" : "none" },
        paint: { "raster-opacity": 0.65, "raster-fade-duration": 250 },
      });
    }

    return () => {
      // The parent LiveCanvas destroys the map on unmount; this cleanup can
      // run after that, so guard both the map reference and its lookups.
      try {
        if (!map) return;
        if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      } catch {
        // Map already torn down — nothing to clean up.
      }
    };
  }, [map, ready]);

  useEffect(() => {
    if (!map || !ready) return;
    if (!map.getLayer(LAYER_ID)) return;
    map.setLayoutProperty(
      LAYER_ID,
      "visibility",
      visible ? "visible" : "none",
    );
  }, [map, ready, visible]);

  return null;
}
