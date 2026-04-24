# Prism — agentic city-discovery engine

Three frontier LLMs — Claude Opus 4.7, OpenAI GPT 5.5, Google Gemini 3.1 Pro —
race across a live Singapore map with the same prompt, the same eight-tool
GrabMaps belt, and the same frozen harness. The only variable is the model.
Plans are scored by the harness, HITL-rated by the operator, and pinned to
a shared MapLibre canvas.

Built solo at the GrabMaps API Hackathon, 2026-04-24.

## Run

Backend (Python 3.12 + FastAPI, SSE streaming via `sse-starlette`):

    cd backend
    uv sync
    cp .env.example .env  # fill GRABMAPS_API_KEY + the three LLM keys
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

Frontend (Next.js 15 App Router + MapLibre GL):

    cd frontend
    npm install
    npm run dev  # opens http://localhost:3000

Demo seed (three real races → three pinned plans in ~90 s):

    cd backend
    rm -f prism.db   # clean state
    uv run uvicorn app.main:app --port 8000 &
    uv run python scripts/seed_demo.py

## Further reading

- `docs/hackathon-phases/PRISM-V2-VISION.md` — product narrative
- `docs/hackathon-phases/00-index.md` — sharded build plan
- `docs/demo_script.md` — 3-minute walk
- `docs/grabmaps_api_reference.md` — endpoint surface + tool belt
- `backend/app/harness.py` — the FROZEN CONTRACT

## Live-only

No mocks, no fixtures, no `USE_MOCK_GRABMAPS` toggle. Every value the agents
see is produced by a real GrabMaps endpoint or a real composite of real
calls. If a live call is unavailable, Prism fails loudly rather than
substituting fake data.
