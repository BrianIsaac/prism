"use client";

import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { useLiveCanvas } from "@/components/live-canvas";

const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Whole-island radius in metres. The bbox variant is constrained to ~5km/side
// by GrabMaps; the circle variant is the safer call for an island-scale view.
const SINGAPORE_LAT = 1.3521;
const SINGAPORE_LNG = 103.8198;
const RADIUS_METRES = 20000;

const REFRESH_INTERVAL_MS = 30_000;

interface RawIncident {
  id?: string | number;
  type?: string;
  severity?: number;
  description?: string;
  location?: { lat?: number; lng?: number };
  lat?: number;
  lng?: number;
  startedAt?: string;
}

interface NormalisedIncident {
  id: string;
  type: string;
  severity: number;
  description: string;
  lat: number;
  lng: number;
  startedAt: string | null;
}

function normalise(raw: RawIncident, fallbackId: number): NormalisedIncident {
  const lat = raw.location?.lat ?? raw.lat;
  const lng = raw.location?.lng ?? raw.lng;
  return {
    id: String(raw.id ?? `incident-${fallbackId}`),
    type: raw.type ?? "incident",
    severity: typeof raw.severity === "number" ? raw.severity : 1,
    description: raw.description ?? "",
    lat: typeof lat === "number" ? lat : NaN,
    lng: typeof lng === "number" ? lng : NaN,
    startedAt: raw.startedAt ?? null,
  };
}

function severityColour(severity: number): string {
  if (severity >= 4) return "#ef4444"; // red
  if (severity >= 2) return "#f59e0b"; // amber
  return "#facc15"; // yellow
}

function buildMarkerEl(incident: NormalisedIncident): HTMLDivElement {
  const el = document.createElement("div");
  el.setAttribute("role", "img");
  el.setAttribute(
    "aria-label",
    `${incident.type} severity ${incident.severity}`,
  );
  el.style.width = "14px";
  el.style.height = "14px";
  el.style.borderRadius = "50%";
  el.style.backgroundColor = severityColour(incident.severity);
  el.style.border = "2px solid rgba(255,255,255,0.92)";
  el.style.boxShadow = "0 0 0 2px rgba(0,0,0,0.45), 0 0 12px rgba(0,0,0,0.35)";
  el.style.cursor = "pointer";
  return el;
}

function buildPopupHtml(incident: NormalisedIncident): string {
  const safeType = incident.type.replace(/[<>]/g, "");
  const safeDesc = incident.description.replace(/[<>]/g, "");
  return `
    <div style="font-family:ui-sans-serif,system-ui;padding:4px 2px;max-width:220px;">
      <div style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:#888;">incident</div>
      <div style="font-size:13px;font-weight:600;color:#fff;margin-top:2px;">${safeType} · sev ${incident.severity}</div>
      ${safeDesc ? `<div style="font-size:11px;color:#cfcfcf;margin-top:4px;line-height:1.4;">${safeDesc}</div>` : ""}
    </div>
  `;
}

/**
 * Live incident marker group. Polls the backend-proxied GrabMaps incident
 * endpoint every 30s and re-renders the marker layer in place. Markers carry
 * a click popup with type + severity + description.
 */
export function IncidentLayer() {
  const { map, ready } = useLiveCanvas();
  const markersRef = useRef<Map<string, maplibregl.Marker>>(new Map());

  useEffect(() => {
    if (!map || !ready) return;

    let cancelled = false;
    let timer: number | null = null;

    const fetchAndRender = async (): Promise<void> => {
      const url = `${API_BASE}/grabmaps-proxy/incidents-circle?lat=${SINGAPORE_LAT}&lng=${SINGAPORE_LNG}&radius=${RADIUS_METRES}`;
      try {
        const res = await fetch(url);
        if (!res.ok) return;
        const data = (await res.json()) as
          | { incidents?: RawIncident[] }
          | RawIncident[];
        const list: RawIncident[] = Array.isArray(data)
          ? data
          : Array.isArray(data.incidents)
            ? data.incidents
            : [];
        if (cancelled) return;

        const next = new Map<string, NormalisedIncident>();
        list.forEach((raw, idx) => {
          const incident = normalise(raw, idx);
          if (Number.isFinite(incident.lat) && Number.isFinite(incident.lng)) {
            next.set(incident.id, incident);
          }
        });

        const existing = markersRef.current;
        for (const [id, marker] of existing.entries()) {
          if (!next.has(id)) {
            marker.remove();
            existing.delete(id);
          }
        }
        for (const [id, incident] of next.entries()) {
          const current = existing.get(id);
          if (current) {
            current.setLngLat([incident.lng, incident.lat]);
            continue;
          }
          const el = buildMarkerEl(incident);
          const popup = new maplibregl.Popup({
            offset: 12,
            closeButton: false,
            className: "prism-incident-popup",
          }).setHTML(buildPopupHtml(incident));
          const marker = new maplibregl.Marker({ element: el })
            .setLngLat([incident.lng, incident.lat])
            .setPopup(popup)
            .addTo(map);
          existing.set(id, marker);
        }
      } catch {
        // Network error / proxy not yet wired — silently keep the previous
        // marker set and try again on the next tick.
      }
    };

    void fetchAndRender();
    timer = window.setInterval(fetchAndRender, REFRESH_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
      for (const marker of markersRef.current.values()) marker.remove();
      markersRef.current.clear();
    };
  }, [map, ready]);

  return null;
}
