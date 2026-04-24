"use client";

import { memo } from "react";

export interface SparklineProps {
  values: number[];
  colour: string;
  width?: number;
  height?: number;
  min?: number;
  max?: number;
  label?: string;
}

function SparklineInner({
  values,
  colour,
  width = 120,
  height = 20,
  min,
  max,
  label,
}: SparklineProps) {
  if (values.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label ?? "not enough data yet"}
      >
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="rgba(255,255,255,0.15)"
          strokeDasharray="2 3"
          strokeWidth={1}
        />
      </svg>
    );
  }

  const observedMin = Math.min(...values);
  const observedMax = Math.max(...values);
  const lo = min ?? observedMin;
  const hi = max ?? observedMax;
  // 15% pad so flat or tiny signals still produce a visible trace.
  const padding = (hi - lo) * 0.15 || 0.001;
  const loP = lo - padding;
  const hiP = hi + padding;
  const range = hiP - loP || 1;

  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - loP) / range) * height;
    return [x, y] as const;
  });
  const path = `M ${points
    .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" L ")}`;
  const [lastX, lastY] = points[points.length - 1];

  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={label ?? `sparkline, ${values.length} points`}
    >
      <path
        d={path}
        fill="none"
        stroke={colour}
        strokeWidth={1.2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={lastX} cy={lastY} r={1.8} fill={colour} />
    </svg>
  );
}

// Memoised so the live-feed panel's 2s polling loop only redraws a category
// when its own values array identity actually changes, not when siblings tick.
export const Sparkline = memo(SparklineInner);
