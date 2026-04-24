"use client";

import nextDynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

// Phase 0's `lib/api-client` eagerly imports `eventsource-polyfill`, which
// touches `window` at module scope. The race shell consumes that module so
// we defer its mount through `next/dynamic` with `ssr: false`: the polyfill
// never lands in the server bundle, which keeps `next build`'s prerender
// worker happy while still hydrating the full SSE orchestrator on the
// client. The shell opens the stream via `openRaceStream` from
// lib/api-client; this page owns the rate-to-Explore navigation by calling
// `router.push('/')` through the shell's `onRated` callback.
const NewRouteShell = nextDynamic(
  () => import("./new-route-shell").then((m) => m.NewRouteShell),
  { ssr: false },
);

export default function NewRoutePage() {
  const router = useRouter();
  const handleRated = useCallback(() => {
    router.push('/');
  }, [router]);
  return <NewRouteShell onRated={handleRated} />;
}
