"use client";

// Prism operator dashboard. Renders four surfaces:
//   1. harness weights — frozen_defaults vs runtime (drifts at α = 0.02/rating)
//   2. weight-drift sparklines — flow / diversity / vibe over time
//   3. live-feed panel — five sparklines (search, routing, traffic, incidents, streetview)
//   4. bug-report markdown + feedback KB (digest, tags, raw tail, rebuild)
import nextDynamic from "next/dynamic";

// `lib/api-client.ts` (Phase-0-owned) eagerly imports `eventsource-polyfill`,
// which touches `window` at module scope and crashes Node's prerender worker
// during `next build`. Loading the real admin view via `next/dynamic` with
// `ssr: false` defers that module graph to the browser, so the build can
// still static-generate the shell while the data panels mount client-side.
// The Phase-0 polyfill root cause is filed in `INTEGRATION_TODOS.md`.
const AdminView = nextDynamic(
  () => import("./admin-view").then((m) => m.AdminView),
  { ssr: false },
);

export default function AdminPage() {
  return <AdminView />;
}
