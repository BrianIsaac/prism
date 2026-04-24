"use client";

import nextDynamic from "next/dynamic";

// `lib/api-client.ts` (Phase-0-owned) eagerly imports `eventsource-polyfill`,
// whose top-level statements reference `window`. Any page that pulls the
// client in directly crashes Next's prerender worker during `next build`.
// Phases 5 and 6 already land the same fix via a thin wrapper + a
// `next/dynamic({ ssr: false })` client shell; Phase 7 lifts that pattern
// here so `npm run build` completes.
const ExploreShell = nextDynamic(
  () => import("./explore-shell").then((m) => m.ExploreShell),
  { ssr: false },
);

export default function ExplorePage() {
  return <ExploreShell />;
}
