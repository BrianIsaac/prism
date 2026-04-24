# Prism

An agentic city-discovery engine that pits three frontier large language
models against the **same prompt**, the **same eight-tool GrabMaps
belt**, and the **same frozen scoring harness**. The only variable is
the model — provider diversity supplies the differentiation.

Built solo for the GrabMaps API Hackathon, 2026-04-24, 3 Media Circle,
Singapore. Submitted to the **Discover the City** track.

> "Three minds, one city, refracted into three coloured plans on the
> same canvas — driven by live Grab traffic, live incidents, real
> street-view, and the room's validation."

---

## Table of contents

1. [Vision](#vision)
2. [How the race works](#how-the-race-works)
3. [Architecture](#architecture)
4. [Tech stack](#tech-stack)
5. [Quick start](#quick-start)
6. [Environment keys](#environment-keys)
7. [Backend surface](#backend-surface)
8. [Frontend surface](#frontend-surface)
9. [The tool belt](#the-tool-belt)
10. [The frozen harness](#the-frozen-harness)
11. [Admin + observability](#admin--observability)
12. [Data model](#data-model)
13. [Testing](#testing)
14. [Troubleshooting](#troubleshooting)
15. [Directory layout](#directory-layout)
16. [Credits + licence](#credits--licence)

---

## Vision

Karpathy's [autoresearch ratchet](https://karpathy.github.io/) compressed
into a single city-discovery loop. Three LLMs play the same role in
parallel. A **frozen harness** measures every plan they produce against
seven hard rules (time, money, reachability, opening hours, dietary,
anchors, feasibility) and three soft rules (flow, diversity, photo-
grounded vibe). Humans rate the winner and the rating **drifts the
harness weights** by α = 0.02 — a slow, auditable Bayesian update of
what *this room's* taste of a good trip looks like.

The product is two things at once:

- **A real-time agentic benchmark.** Opus, GPT, Gemini race across a
  live map for 60–120 seconds. Their tool-call volume, timing, and
  final plan scores are all visible — this is the cleanest per-model
  agentic comparison you can build in one evening.
- **A city-discovery tool.** Every race leaves behind a validated plan
  that pins to a globe. Over time the globe accumulates a taste-weighted
  index of the city.

### Live-only, no mocks

There is no `USE_MOCK_GRABMAPS` toggle, no fixture JSONs, no synthetic
tools. Every value the agents see is produced by a real GrabMaps
endpoint or a real composite of real calls. If a live call is
unavailable Prism fails loudly rather than substituting fake data. The
only JSON fixture in the repo is `backend/tests/fixtures/` — a snapshot
of the live API shape for schema testing, not runtime data.

---

## How the race works

```
                POST /race  {query, spec_override?}
                    │
                    ▼
           parse_spec (Haiku)                  fetch_hot_candidates
           └──────┬──────┘                           │
                  └────────────── run_race ─────────┘
                                  │
              ┌───────────────────┼────────────────────┐
              ▼                   ▼                    ▼
         opus agent          gpt agent           gemini agent
              │                   │                    │
     ┌────────┼────────┐ ┌────────┼────────┐ ┌─────────┼────────┐
     ▼        ▼        ▼ ▼        ▼        ▼ ▼         ▼        ▼
   tool_call  thought   plan  tool_call  plan  tool_call plan (...)
      │                   │      │         │      │         │
      └──── SSE stream ───┴──────┴─────────┴──────┴─────────┘
                              │
                              ▼
                 /race/{id}/stream  →  frontend paints pulses +
                                       cursors + arcs in real time
                              │
                              ▼
                 harness.score_and_rank (flow + diversity + vibe)
                              │
                              ▼
                 race_complete event carries the ranked plans
                              │
                              ▼
                 user picks a winner  →  POST /rating
                              │
                              ▼
                 validated_plans row + weight drift + plan_atoms upsert
```

Each agent runs a bounded tool-use loop (up to 40 tool calls, 10-minute
wall clock). Tool calls go through `call_tool_with_budget` which
enforces the budget, writes a `traces` row per call, and mirrors the
call onto the per-race SSE queue so the frontend sees it at
wire-arrival time. `emit_thought` is a zero-budget instrumentation
call that renders as a thought-bubble tooltip on the agent's cursor.

When the model stops calling tools and emits JSON, the plan passes the
**hallucination guard** (every POI id must have come from a real tool
result) and is scored against the frozen harness. Plans that score below
`HARNESS_MIN_AGGREGATE` get the per-dimension scores fed back as a user
message and the loop ratchets up to `HARNESS_MAX_RETRIES` more times —
each agent gets up to three passes at a good plan.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         frontend (Next.js 15)                    │
│                                                                  │
│  /              Explore globe — react-globe.gl + MapLibre swap   │
│  /new           Structured form + live canvas + race panel       │
│  /admin         Harness drift + live feed + bug report + KB      │
│                                                                  │
│  lib/api-client.ts   fetch + SSE helpers                         │
│  lib/types.ts        shared type surface                         │
└────────────┬─────────────────────────────────────────────────────┘
             │ HTTP + SSE
             ▼
┌──────────────────────────────────────────────────────────────────┐
│                      backend (FastAPI, async)                    │
│                                                                  │
│  app/main.py         routes, rate limit, cache, proxies, lifespan│
│  app/race.py         three-agent orchestrator + SSE fan-out      │
│  app/harness.py      FROZEN CONTRACT — hard rules + soft scores  │
│  app/spec.py         free-text query → structured Spec (Haiku)   │
│  app/agents/         per-racer AgentConfig + shared prompt       │
│  app/llm_clients.py  Anthropic / OpenAI / Google SDK dispatch    │
│  app/tools/          8-tool GrabMaps belt (live HTTP, no mocks)  │
│  app/tools/base.py   budget + trace + SSE emission chokepoint    │
│  app/storage.py      aiosqlite — 11 tables                       │
│  app/feedback_kb.py  LLM-Wiki pattern (Haiku rebuilds digest)    │
│  app/trace_export.py Failing-tool-call Markdown report           │
└────────────┬─────────────────────────────────────────────────────┘
             │ HTTPS
             ▼
┌──────────────────────────────────────────────────────────────────┐
│                          external providers                      │
│                                                                  │
│  maps.grab.com        search · route · traffic · incidents ·     │
│                       OpenStreetCam street-view · style tiles    │
│  api.anthropic.com    Opus 4.7 (race) + Haiku 4.5 (judge, spec)  │
│  api.openai.com       GPT 5.4 (race)                             │
│  generativelanguage   Gemini 3.1 Pro (race)                      │
└──────────────────────────────────────────────────────────────────┘
```

The frontend **never sees the Bearer key**. Every GrabMaps endpoint the
browser needs (style.json, traffic tiles, incident markers) is proxied
through `/grabmaps-proxy/*` on the FastAPI backend with server-side
`Authorization: Bearer <key>` injection.

---

## Tech stack

- **Backend**: Python 3.12 · FastAPI · `sse-starlette` · `httpx` (live
  GrabMaps) · `aiosqlite` · `anthropic` / `openai` / `google-genai`
  SDKs · `tenacity` for retries · `pytest-asyncio` + `respx`. Deps
  managed by **uv**, never pip.
- **Frontend**: Next.js 15 App Router · React 19 · TypeScript strict ·
  Tailwind v4 + shadcn primitives · **MapLibre GL** for Singapore
  canvas · **react-globe.gl** (three.js) for the 3D earth · SSE via
  `eventsource-polyfill`.
- **Storage**: SQLite on disk, 11 tables. WAL mode, no migrations
  framework — schema applied idempotently at boot.

---

## Quick start

### Prerequisites

- Python 3.12
- Node.js 20+
- [`uv`](https://docs.astral.sh/uv/) on `$PATH`
- Real API keys: GrabMaps (from the hackathon workshop drop),
  Anthropic, OpenAI, Google Gemini.

### 1 · Backend

```bash
cd backend
uv sync
cp .env.example .env
# fill:
#   GRABMAPS_API_KEY=bm_...
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-proj-...
#   GEMINI_API_KEY=AIza...
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Backend listens on `http://localhost:8000`, writes to `./prism.db`.

### 2 · Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

The frontend talks to `NEXT_PUBLIC_API_BASE_URL` (default
`http://localhost:8000`). Override via `.env.local` if the backend runs
elsewhere.

### 3 · Seed the demo (optional)

Three live races, pinned to Explore, in ~90 s:

```bash
cd backend
rm -f prism.db                        # clean state
uv run uvicorn app.main:app --port 8000 &
uv run python scripts/seed_demo.py    # idempotent — safe to re-run
```

The seed fires three `POST /race` calls (Geylang hawker crawl, Sentosa
family day, Chinatown heritage walk), drains each SSE stream, picks
the rank-1 plan, and posts a HITL rating.

---

## Environment keys

`backend/.env` (mirrors `backend/.env.example`):

| Key | Purpose | Source |
|---|---|---|
| `GRABMAPS_API_KEY` | GrabMaps Bearer token | Hackathon workshop |
| `GRABMAPS_BASE_URL` | `https://maps.grab.com` | default |
| `ANTHROPIC_API_KEY` | Claude Opus 4.7 + Haiku 4.5 | console.anthropic.com |
| `OPENAI_API_KEY` | GPT (current API flagship: 5.4) | platform.openai.com |
| `GEMINI_API_KEY` | Gemini 3.1 Pro | aistudio.google.com |
| `OPUS_MODEL` | `claude-opus-4-7` | default |
| `GPT_MODEL` | `gpt-5.4` | default — 5.5 not yet in API as of 2026-04-24 |
| `GEMINI_MODEL` | `gemini/gemini-3.1-pro-preview-customtools` | default |
| `JUDGE_MODEL` | `claude-haiku-4-5-20251001` (vibe judge + spec parser) | default |
| `TOOL_BUDGET_PER_AGENT` | `40` | default |
| `RACE_DEADLINE_SECONDS` | `600` | default |
| `HARNESS_MAX_RETRIES` | `2` | default |
| `HARNESS_MIN_AGGREGATE` | `0.5` | default |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | default |

A missing key fails loudly — there is no fallback to synthetic data.

---

## Backend surface

All routes are JSON (except `/race/{id}/stream` which is `text/event-
stream`). OPTIONS preflights + `/health` are always allowed — everything
else is rate-limited.

### Race

| Method | Path | Purpose |
|---|---|---|
| POST | `/race` | Kick off a three-agent race. Returns `{race_id, stream_url}` immediately. Memoised 5 min on `(query, sha256(spec_override))`. |
| GET | `/race/{id}/stream` | SSE event feed. Replays buffered events first so late subscribers catch the full run. |
| GET | `/race/{id}/events?since=N` | Fast-polling fallback. Same events as the stream, sliced. |
| GET | `/race/{id}/plans` | Every agent's plan for a completed race. |
| GET | `/trace/{id}` | All `traces` rows for a race. |

### HITL + validation

| Method | Path | Purpose |
|---|---|---|
| POST | `/rating` | Record a HITL rating, pin the plan, drift weights, seed `plan_atoms`. |
| POST | `/feedback` | Free-text feedback on a plan. Every 3rd row triggers a Haiku digest rebuild. |
| GET | `/feedback?plan_id=…&limit=N` | List feedback. |
| GET | `/validated?country_iso3=…&limit=N` | Validated plans (explicit + auto-pinned rank-1/2/3). |
| POST | `/validated/{id}/like` | Bump likes; materialises auto-pin on first interaction. |
| GET | `/alternatives?category=…&near_lat=…&near_lng=…` | POI candidates for the stop-swap UI (enriched with street-view). |
| GET | `/races?limit=N` | Past races newest-first with rank-1 plan attached. |

### Admin

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/weights` | Frozen defaults vs runtime-drifted weights. |
| GET | `/admin/weight-history?limit=N` | Drift sparkline source. |
| GET | `/admin/bug-report` | Aggregated failing-tool-call report + Markdown. |
| GET | `/admin/live-feed?window_seconds=N` | Per-category + **per-agent** tool-call counts. |
| GET | `/admin/feedback-digest` | Latest digest + history + raw tail. |
| POST | `/admin/feedback-digest/rebuild` | Manual digest rebuild. |

### GrabMaps proxy (server-side Bearer)

| Method | Path | Upstream |
|---|---|---|
| GET | `/grabmaps-proxy/style.json?theme=…` | `/api/style.json` |
| GET | `/grabmaps-proxy/traffic-raster-tile/{z}/{x}/{y}` | `/api/v1/traffic/real-time/tile/{z}/{x}/{y}` |
| GET | `/grabmaps-proxy/traffic-tile/{z}/{x}/{y}.json` | `/api/v1/traffic-tiles/{z}/{x}/{y}.json` (GeoJSON) |
| GET | `/grabmaps-proxy/incidents-tile/{z}/{x}/{y}` | `/api/v1/traffic/incidents/tile/{z}/{x}/{y}` |
| GET | `/grabmaps-proxy/incidents-circle?lat=…&lng=…&radius=…` | bbox-tiles + `linkReference=GRAB_WAY` |

### Health + limits

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe (always 200). |
| — | rate limit | `/race` = 10 req/min/IP. Everything else = 200 req/min/IP. Sliding window, 429 + `Retry-After` on breach. |

---

## Frontend surface

### `/` — **Explore**

Interactive 3D globe (react-globe.gl) with earth-at-night texture, green
atmosphere halo, country polygon outlines. Drag to rotate, scroll to
zoom. The sidebar ranks every validated plan by **likes desc**, then
rating, then recency. Auto-pin surfaces every agent's rank 1–3 plan so
a race produces three entries (opus red, gpt green, gemini blue), each
with its own distinct narrative.

Click a plan → camera flies to the POI bounding box over 1400 ms, day
texture + ESRI satellite tiles swap in. At the end of the tween the
canvas swaps from globe to a MapLibre Singapore map with that agent's
route as numbered green pins + glowing arcs. Narrative reveals
word-by-word on the bottom overlay. `← back to globe` reverses the
transition.

### `/new` — **New Route (live race)**

Ten-field structured launcher: start location, start time, duration,
party size, transport profile, budget ceiling, vibe tags, dietary,
wheelchair-friendly, notes. A live query preview renders under the
form. Past queries list above for one-click prefill.

On launch, the map centres on Singapore, and **`RaceAutoFocus`** flies
the camera to the first tool call's coordinates at zoom 14 — so the
three agent cursors are visible from the start. The right-panel
`AgentRacePanel` shows per-agent elapsed time, current tool name, and
a `✓` when the plan resolves. Three PlanCards slide up at the bottom on
`race_complete` with scores, failures, and "pick" buttons. Selecting
one opens `PlanDetail` with per-stop street-view galleries, stop-swap
dialog, and an HITL rating surface that redirects to `/` on submit.

### `/admin` — **Admin**

- **Harness weights** — frozen defaults vs runtime, explains α = 0.02
- **Drift over time** — three sparklines for flow/diversity/vibe
- **Live feed** — five category sparklines (Search / Routing / Traffic
  / Incidents / Street-view) at 2 s poll, 60 s window, plus a
  **per-agent strip** showing opus / gpt / gemini calls-per-minute
  with a `mostly <category>` label per agent
- **Bug report** — total vs failed calls, failures by tool / status,
  raw reproduction samples, Markdown export
- **Feedback KB** — current digest (summary + tag chips), digest
  history, raw feedback tail, manual rebuild button

---

## The tool belt

Eight live tools. Schemas live in `backend/app/tools/__init__.py`.

| Tool | Upstream | Role |
|---|---|---|
| `places_search` | `GET /api/v1/maps/poi/v1/search` | keyword POI search |
| `nearby_search` | `GET /api/v1/maps/place/v2/nearby` | radius search (km) |
| `reverse_geocode` | `GET /api/v1/maps/poi/v1/reverse-geo` | coord → POI |
| `route` | `GET /api/v1/maps/eta/v1/direction` | single leg (lat_first=true) |
| `route_matrix` | local N × M composite of `route` calls | matrix (no upstream matrix endpoint) |
| `get_traffic` | `GET /api/v1/traffic/real-time/circle` | congestion at a point (`linkReference=GRAB_WAY`) |
| `get_incidents` | `GET /api/v1/traffic/incidents/circle` | live disruptions near a point |
| `get_street_view` | `GET /api/v1/openstreetcam-api/2.0/photo/` | OpenStreetCam photos (default `projection=SPHERE`) |

`emit_thought` is the ninth entry on the agents' tool list but is
**instrumentation**, not a tool — zero budget, emits a `thought` SSE
event, returns `{"ok": true}`.

Every tool call is wrapped by `call_tool_with_budget` which:
1. Enforces the per-agent budget (`Budget.remaining`)
2. Emits `tool_call` with `payload.lat/lng` when extractable
3. Times the call, catches every exception
4. Writes a `ToolTrace` row to SQLite (feeds the admin live feed + failure report)
5. Emits `tool_result` with a short summary + street-view thumbnail

---

## The frozen harness

`backend/app/harness.py` is the **FROZEN CONTRACT**. Agents must not
read, call, or edit it during a race. It has:

**Seven hard rules** (plan rejected if any fails):

1. **Time** — `sum(leg duration + dwell) ≤ max_duration_minutes`
2. **Money** — `sum(leg.route.fee.amount) + poi.avg_cost_sgd ≤ max_budget_sgd`
3. **Reachability** — no `unreachable` leg for the chosen transport profile
4. **Opening hours** — populated schedules must cover each POI's visit window
5. **Dietary** — every food POI matches the user's filter
6. **Anchors** — start/end POIs within 200 m of spec anchors
7. **Feasibility** — no null / placeholder / unresolved POI ids

**Three soft scores** (each ∈ [0, 1]):

- **Flow** — Shannon-like continuity of the leg sequence (revisit penalty + dwell/travel ratio)
- **Diversity** — entropy over `(category, subcategory)` pairs
- **Vibe** — Haiku 4.5 judge reading real OpenStreetCam photos per POI

Aggregate score: `w_flow·flow + w_diversity·diversity + w_vibe·vibe`.
Starting weights: `{flow: 0.5, diversity: 0.2, vibe: 0.3}`. Each HITL
rating drifts the weights toward the user's `(efficiency, novelty,
vibe)` normalisation by α = 0.02 and appends a `weight_history` row.

---

## Admin + observability

Three artefacts per race land on disk:

- `races` — one row with spec, weights, duration, status
- `plans` — one row per agent; `hard_pass`, `rank`, `total_score`
- `traces` — one row per tool call; `status`, `latency_ms`, input/output

The admin dashboard renders:

- `/admin/weights` + `/admin/weight-history` → drift sparklines
- `/admin/bug-report` → grouped by tool + status, Markdown export
- `/admin/live-feed` → per-category + per-agent sparklines (see above)
- `/admin/feedback-digest` → Haiku-rewritten room-taste profile,
  refreshed every 3 feedback submissions

---

## Data model

11 SQLite tables (WAL mode). Schema at `backend/app/storage.py:_SCHEMA`.

```
races              race run envelope (query, spec, weights, duration)
  └─ plans         per-agent plans (hard_pass, rank, total_score, plan JSON)
       └─ traces   per-tool-call rows (input/output JSON, status, latency)

validated_plans    HITL-pinned plans (rating, anchor, pois_override)
plan_atoms         swarm overlay — running mean of POI scores across races
weight_history     chronological weight snapshots for drift sparkline

feedback           free-text user feedback on validated plans
feedback_digest    Haiku-compiled room-taste profile (LLM-Wiki pattern)

traffic_snapshots  cached get_traffic responses (bbox-keyed)
incident_snapshots cached get_incidents responses
streetview_cache   (lat_round, lng_round, day_bucket) → photo URLs
```

The three v2-only cache tables give the vibe judge warm street-view
inputs and make the live-canvas overlays cheap to refresh.

---

## Testing

Backend:

```bash
cd backend
uv run pytest -q          # 109 tests, ~11 s
```

Coverage spans harness rules, storage round-trips, tool stubs against
`respx` mocks, endpoint happy paths + 4xx/422 validation, SSE polling
fallback, `/grabmaps-proxy/*` key-injection, and the rate-limit
sliding window.

Frontend:

```bash
cd frontend
npx tsc --noEmit          # type check
npm run build             # production build
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health` 200 but races produce empty plans | LLM provider auth | `grep _API_KEY backend/.env`; test each key with a direct curl to the provider |
| GPT silent while Opus + Gemini work | `GPT_MODEL=gpt-5.5` | 5.5 is ChatGPT-only as of 2026-04-24; use `gpt-5.4` (API flagship) |
| Traffic layer blank | raster tile endpoint gated | make sure `backend/.env` has the real `GRABMAPS_API_KEY`; confirm `/grabmaps-proxy/traffic-raster-tile/12/3280/2054` returns a PNG |
| Canvas empty on `/` | globe texture didn't load | hard-refresh; check DevTools network for `earth-night-8k.jpg` |
| Cursors don't pulse on `/new` | tool_call payload missing lat/lng | tool args may not carry coords (e.g. `places_search` without proximity bias); race still valid, just no visual |
| SSE stream closes early | backend process restarted mid-race | re-submit; 5-min memoisation cache will short-circuit identical queries |
| 429 on `/race` | rate limit tripped | wait for the `Retry-After` header; default is 10 calls / 60 s / IP |
| `npm run build` fails on `/` | SSR leak through `eventsource-polyfill` | `app/page.tsx` must be a thin `next/dynamic({ ssr: false })` wrapper around `explore-shell.tsx` |

---

## Directory layout

```
prism/
├── README.md                       # ← you are here
├── CLAUDE.md                       # agent build-time instructions
├── INTEGRATION_TODOS.md             # cross-shard deferred items
├── LICENSE
├── .shard-done/                    # phase markers (00..07)
├── backend/
│   ├── pyproject.toml              # uv deps
│   ├── .env.example
│   ├── prism.db                    # SQLite (gitignored)
│   ├── app/
│   │   ├── main.py                 # FastAPI app — routes, rate limit, cache, proxies
│   │   ├── race.py                 # three-agent orchestrator + SSE fan-out
│   │   ├── harness.py              # FROZEN CONTRACT — hard + soft rules
│   │   ├── spec.py                 # free-text → Spec via Haiku
│   │   ├── models.py               # Pydantic domain models
│   │   ├── config.py               # env-driven knobs
│   │   ├── storage.py              # aiosqlite persistence (11 tables)
│   │   ├── feedback_kb.py          # Haiku digest rebuild
│   │   ├── trace_export.py         # failing-tool-call aggregation + Markdown
│   │   ├── llm_clients.py          # Anthropic + OpenAI + Google GenAI
│   │   ├── agents/
│   │   │   ├── base.py             # shared tool-use loop + ratchet
│   │   │   ├── opus.py · gpt.py · gemini.py  # AgentConfig only
│   │   │   ├── judge.py            # photo-grounded vibe Haiku judge
│   │   │   └── shared_prompt.py    # single system prompt for all three
│   │   └── tools/
│   │       ├── base.py             # call_tool_with_budget chokepoint
│   │       ├── grabmaps.py         # classic CRUD tools
│   │       ├── live.py             # traffic / incidents / street-view
│   │       └── __init__.py         # merged schema + dispatch
│   ├── scripts/
│   │   └── seed_demo.py            # 3 real races + ratings in ~90 s
│   └── tests/                      # 109 tests, pytest-asyncio + respx
├── frontend/
│   ├── package.json                # next, react-globe.gl, maplibre-gl
│   ├── next.config.ts              # transpilePackages for three-globe
│   ├── public/
│   │   ├── earth-night-8k.jpg      # globe idle texture
│   │   ├── earth-day-16k.jpg       # globe fly-in texture
│   │   └── countries.geojson       # Natural Earth 110m
│   ├── app/
│   │   ├── layout.tsx              # nav + viewport
│   │   ├── page.tsx                # /  (thin wrapper; next/dynamic)
│   │   ├── explore-shell.tsx       # globe ↔ maplibre + RoutePins
│   │   ├── new/
│   │   │   ├── page.tsx            # /new  (wrapper)
│   │   │   └── new-route-shell.tsx # form + SSE + race panel + PlanCards
│   │   └── admin/
│   │       ├── page.tsx            # /admin  (wrapper)
│   │       └── admin-view.tsx      # drift + live-feed + bug-report + KB
│   ├── components/                 # LiveCanvas, TrafficLayer, IncidentLayer,
│   │                               # PrismGlobe, AgentStreamOverlay,
│   │                               # AgentRacePanel, LiveFeedPanel,
│   │                               # PlanCard, PlanDetail, HitlRating,
│   │                               # TopRoutesList, FeedbackDrawer,
│   │                               # Sparkline, WordReveal, …
│   └── lib/
│       ├── types.ts                # shared type surface
│       └── api-client.ts           # fetch + openRaceStream helpers
└── docs/                            # sits next to prism/
    ├── grabmaps_api_reference.md    # endpoint surface + tool belt
    ├── demo_script.md               # canonical 3-min walk
    └── hackathon-phases/
        ├── PRISM-V2-VISION.md       # product narrative
        ├── 00-index.md              # shard ownership + DAG
        └── phase-00..07-*.md        # sharded build plan
```

---

## Credits + licence

- **GrabMaps** — every `tool_call` you see goes to a live Grab endpoint.
- **Solar System Scope** — `earth-night-8k.jpg` (CC-BY 4.0).
- **NASA Blue Marble** — `earth-day-16k.jpg` (public domain, 16K
  downsample).
- **Natural Earth** — `countries.geojson` (public domain).
- **OpenStreetMap contributors** — base tiles for the Singapore canvas.
- **ESRI World Imagery** — fly-in satellite tiles.
- **Karpathy** — the autoresearch ratchet idea in compressed form.

Licence: see `LICENSE` (MIT).

No mention of Claude Code or "generated by" in any commit or artefact —
Prism is a solo-built entry.
