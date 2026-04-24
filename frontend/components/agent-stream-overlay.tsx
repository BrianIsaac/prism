"use client";

import { useEffect, useMemo, useState } from "react";
import { useLiveCanvas } from "@/components/live-canvas";
import type { RaceStreamEvent } from "@/lib/types";

// Pulse and thought lifetimes. The parent owns the rolling event window
// (it discards events older than ~8s before passing them in); these constants
// drive only the per-event fade timing inside the SVG renderer.
const PULSE_DURATION_MS = 4_000;
const THOUGHT_DURATION_MS = 4_000;

// Per-agent visual identity. Mirrors the design tokens declared in
// `globals.css` (--color-agent-{opus,gpt,gemini}). Using string literals here
// rather than CSS variables because the SVG paint properties take colours,
// not custom-property bindings, and a tooltip stroke is more useful when
// hard-coded.
const AGENT_COLOURS: Record<string, string> = {
  opus: "#ffffff",
  "claude-opus": "#ffffff",
  "claude-opus-4-7": "#ffffff",
  gpt: "#00b14f",
  "gpt-5-5": "#00b14f",
  openai: "#00b14f",
  gemini: "#27bc6a",
  "gemini-3-1-pro": "#27bc6a",
  google: "#27bc6a",
};
const FALLBACK_COLOUR = "#94a3b8";

function colourForAgent(agent: string | null | undefined): string {
  if (!agent) return FALLBACK_COLOUR;
  const key = agent.toLowerCase();
  if (AGENT_COLOURS[key]) return AGENT_COLOURS[key];
  for (const [prefix, colour] of Object.entries(AGENT_COLOURS)) {
    if (key.includes(prefix)) return colour;
  }
  return FALLBACK_COLOUR;
}

interface ProjectedPoint {
  x: number;
  y: number;
}

interface PulseRender {
  key: string;
  x: number;
  y: number;
  colour: string;
  age: number;
  scale: number;
  thumbnail?: string;
  label?: string;
}

interface CursorRender {
  agent: string;
  x: number;
  y: number;
  colour: string;
}

interface ThoughtRender {
  key: string;
  x: number;
  y: number;
  colour: string;
  text: string;
}

interface ArcRender {
  key: string;
  d: string;
  colour: string;
  emphasised: boolean;
}

interface ToolPayloadInput {
  lat?: number;
  lng?: number;
  origin?: { lat?: number; lng?: number };
  destination?: { lat?: number; lng?: number };
  thumbnail_url?: string;
  thumb_url?: string;
}

interface ToolPayloadOutput {
  lat?: number;
  lng?: number;
  thumbnail_url?: string;
  thumb_url?: string;
  url?: string;
}

function extractLatLng(
  payload: { input?: Record<string, unknown>; output?: unknown } | undefined,
): { lat: number; lng: number } | null {
  const input = payload?.input as ToolPayloadInput | undefined;
  if (
    input &&
    typeof input.lat === "number" &&
    typeof input.lng === "number"
  ) {
    return { lat: input.lat, lng: input.lng };
  }
  const dest = input?.destination;
  if (
    dest &&
    typeof dest.lat === "number" &&
    typeof dest.lng === "number"
  ) {
    return { lat: dest.lat, lng: dest.lng };
  }
  const out = payload?.output as ToolPayloadOutput | undefined;
  if (out && typeof out.lat === "number" && typeof out.lng === "number") {
    return { lat: out.lat, lng: out.lng };
  }
  return null;
}

function extractThumbnail(payload: {
  input?: Record<string, unknown>;
  output?: unknown;
}): string | undefined {
  const out = payload.output as ToolPayloadOutput | undefined;
  return out?.thumbnail_url ?? out?.thumb_url ?? out?.url;
}

function pulseScaleForTool(tool: string): number {
  if (tool.includes("street_view") || tool.includes("streetview")) return 1.6;
  if (tool.includes("route") || tool.includes("routing")) return 1.3;
  if (tool.includes("incident")) return 1.4;
  return 1.0;
}

export interface AgentStreamOverlayProps {
  events: RaceStreamEvent[];
  /** When set, arcs from this agent fatten and pulse (rank-1 promotion). */
  emphasisedAgent?: string | null;
}

/**
 * Stateless renderer of in-flight race events on top of the MapLibre canvas.
 * Subscribes to the map's `move` events so projections update during pan and
 * zoom, but holds no race state itself — the parent owns the event window
 * and slides it on each SSE frame.
 */
export function AgentStreamOverlay({
  events,
  emphasisedAgent = null,
}: AgentStreamOverlayProps) {
  const { map, ready } = useLiveCanvas();
  const [now, setNow] = useState<number>(() => Date.now());
  const [viewportTick, setViewportTick] = useState<number>(0);
  const [size, setSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  useEffect(() => {
    if (!map || !ready) return;
    const onMove = (): void => setViewportTick((v) => v + 1);
    const onResize = (): void => {
      const c = map.getContainer();
      setSize({
        width: c.clientWidth,
        height: c.clientHeight,
      });
      setViewportTick((v) => v + 1);
    };
    onResize();
    map.on("move", onMove);
    map.on("resize", onResize);
    return () => {
      map.off("move", onMove);
      map.off("resize", onResize);
    };
  }, [map, ready]);

  // Coarse 16fps tick drives pulse fade + thought lifetime. Keeping the
  // interval out of the SVG render path avoids requesting browser repaint on
  // every frame when the event stream is idle.
  useEffect(() => {
    if (events.length === 0) return;
    const handle = window.setInterval(() => setNow(Date.now()), 60);
    return () => window.clearInterval(handle);
  }, [events.length]);

  const project = useMemo(() => {
    if (!map) return null;
    return (lat: number, lng: number): ProjectedPoint => {
      const p = map.project([lng, lat]);
      return { x: p.x, y: p.y };
    };
  }, [map, viewportTick]);

  const renders = useMemo(() => {
    const pulses: PulseRender[] = [];
    const thoughts: ThoughtRender[] = [];
    const cursors: Record<string, CursorRender> = {};
    const arcs: ArcRender[] = [];

    if (!project) {
      return { pulses, thoughts, cursors: [], arcs };
    }

    const baseTime = events.length > 0 ? events[events.length - 1].t_ms : 0;

    for (let i = 0; i < events.length; i += 1) {
      const ev = events[i];
      const agent = ev.agent ?? "unknown";
      const colour = colourForAgent(agent);
      const ageMs = baseTime - ev.t_ms;

      if (ev.type === "tool_call" || ev.type === "tool_result") {
        const point = extractLatLng(
          ev.payload as {
            input?: Record<string, unknown>;
            output?: unknown;
          },
        );
        if (!point) continue;
        const proj = project(point.lat, point.lng);
        const tool =
          (ev.payload as { tool?: string }).tool?.toString() ?? "unknown";
        cursors[agent] = { agent, x: proj.x, y: proj.y, colour };
        if (ageMs <= PULSE_DURATION_MS) {
          pulses.push({
            key: `${ev.type}-${i}`,
            x: proj.x,
            y: proj.y,
            colour,
            age: ageMs,
            scale: pulseScaleForTool(tool),
            thumbnail:
              ev.type === "tool_result"
                ? extractThumbnail(
                    ev.payload as {
                      input?: Record<string, unknown>;
                      output?: unknown;
                    },
                  )
                : undefined,
            label: tool,
          });
        }
        continue;
      }

      if (ev.type === "thought" && ageMs <= THOUGHT_DURATION_MS) {
        const cursor = cursors[agent];
        if (!cursor) continue;
        thoughts.push({
          key: `thought-${i}`,
          x: cursor.x,
          y: cursor.y,
          colour,
          text: (ev.payload as { text?: string }).text ?? "",
        });
        continue;
      }

      if (ev.type === "arc") {
        // Arcs are described purely as text in v2; without paired endpoints we
        // skip rendering. Future enhancement: parse `text` for "[lat,lng]
        // -> [lat,lng]" tokens. For now, the arc event still flows through
        // for telemetry but draws nothing.
        continue;
      }

      if (ev.type === "plan_resolved") {
        const plan = ev.payload as { pois?: Array<{ lat: number; lng: number }>; rank?: number | null };
        const pois = plan.pois ?? [];
        if (pois.length < 2) continue;
        const isRankOne = plan.rank === 1;
        for (let j = 0; j < pois.length - 1; j += 1) {
          const a = project(pois[j].lat, pois[j].lng);
          const b = project(pois[j + 1].lat, pois[j + 1].lng);
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2 - Math.hypot(b.x - a.x, b.y - a.y) * 0.18;
          arcs.push({
            key: `arc-${i}-${j}`,
            d: `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`,
            colour,
            emphasised: isRankOne || agent === emphasisedAgent,
          });
        }
        continue;
      }
    }

    const cursorList = Object.values(cursors);
    return { pulses, thoughts, cursors: cursorList, arcs };
  }, [events, project, now, emphasisedAgent]);

  if (!map || !ready) return null;
  if (size.width === 0 || size.height === 0) return null;

  return (
    <svg
      role="presentation"
      aria-hidden="true"
      width={size.width}
      height={size.height}
      viewBox={`0 0 ${size.width} ${size.height}`}
      className="absolute inset-0 pointer-events-none z-20"
    >
      {renders.arcs.map((arc) => (
        <path
          key={arc.key}
          d={arc.d}
          fill="none"
          stroke={arc.colour}
          strokeWidth={arc.emphasised ? 3.5 : 1.8}
          strokeLinecap="round"
          opacity={arc.emphasised ? 0.95 : 0.75}
          style={
            arc.emphasised
              ? { filter: `drop-shadow(0 0 6px ${arc.colour})` }
              : undefined
          }
        />
      ))}
      {renders.pulses.map((pulse) => {
        const t = Math.min(1, pulse.age / PULSE_DURATION_MS);
        const radius = 6 + 28 * t * pulse.scale;
        const opacity = 1 - t;
        return (
          <g key={pulse.key}>
            <circle
              cx={pulse.x}
              cy={pulse.y}
              r={radius}
              fill="none"
              stroke={pulse.colour}
              strokeWidth={1.4}
              opacity={opacity * 0.8}
            />
            <circle
              cx={pulse.x}
              cy={pulse.y}
              r={4 * pulse.scale}
              fill={pulse.colour}
              opacity={opacity}
            />
            {pulse.thumbnail ? (
              <image
                href={pulse.thumbnail}
                x={pulse.x + 8}
                y={pulse.y - 30}
                width={50}
                height={50}
                preserveAspectRatio="xMidYMid slice"
                opacity={opacity}
                style={{ filter: `drop-shadow(0 2px 6px rgba(0,0,0,0.6))` }}
              />
            ) : null}
          </g>
        );
      })}
      {renders.cursors.map((cursor) => (
        <g key={cursor.agent}>
          <circle
            cx={cursor.x}
            cy={cursor.y}
            r={9}
            fill="none"
            stroke="rgba(255,255,255,0.9)"
            strokeWidth={1}
          />
          <circle
            cx={cursor.x}
            cy={cursor.y}
            r={5}
            fill={cursor.colour}
            style={{ filter: `drop-shadow(0 0 6px ${cursor.colour})` }}
          />
        </g>
      ))}
      {renders.thoughts.map((thought) => {
        const tx = Math.min(size.width - 220, Math.max(8, thought.x + 14));
        const ty = Math.max(20, thought.y - 18);
        const trimmed =
          thought.text.length > 80
            ? `${thought.text.slice(0, 78)}…`
            : thought.text;
        return (
          <g key={thought.key}>
            <rect
              x={tx}
              y={ty - 14}
              width={Math.min(220, trimmed.length * 6.5 + 16)}
              height={20}
              rx={4}
              fill="rgba(0,0,0,0.7)"
              stroke={thought.colour}
              strokeOpacity={0.5}
              strokeWidth={1}
            />
            <text
              x={tx + 8}
              y={ty}
              fontSize={11}
              fontFamily="ui-sans-serif, system-ui"
              fill="white"
            >
              {trimmed}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
