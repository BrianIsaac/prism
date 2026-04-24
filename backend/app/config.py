"""Runtime configuration for the Prism backend.

Live-only: Prism v2 does not carry a mock toggle. Every GrabMaps call reaches a
real endpoint via the SDK in :mod:`app.tools`, and every LLM call reaches a real
provider via :mod:`app.llm_clients`. Local persistence is SQLite on disk.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# ---------- GrabMaps ----------

GRABMAPS_API_KEY: str | None = os.environ.get("GRABMAPS_API_KEY")
GRABMAPS_BASE_URL: str = os.environ.get("GRABMAPS_BASE_URL", "https://maps.grab.com")
GRABMAPS_MCP_URL: str = os.environ.get(
    "GRABMAPS_MCP_URL", "https://maps.grab.com/api/v1/mcp"
)


# ---------- LLM provider keys ----------

ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY")
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")


# ---------- Models ----------

# Three providers, one prompt. The only variable between agents is the model —
# provider diversity supplies the differentiation.
OPUS_MODEL: str = os.environ.get("OPUS_MODEL", "claude-opus-4-7")
GPT_MODEL: str = os.environ.get("GPT_MODEL", "gpt-5.4")
GEMINI_MODEL: str = os.environ.get(
    "GEMINI_MODEL", "gemini/gemini-3.1-pro-preview-customtools"
)
JUDGE_MODEL: str = os.environ.get("JUDGE_MODEL", "claude-haiku-4-5-20251001")
SPEC_PARSER_MODEL: str = os.environ.get(
    "SPEC_PARSER_MODEL", "claude-haiku-4-5-20251001"
)

AGENT_TEMPERATURE: float = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))


# ---------- Harness ratchet ----------

HARNESS_MAX_RETRIES: int = int(os.environ.get("HARNESS_MAX_RETRIES", "2"))
HARNESS_MIN_AGGREGATE: float = float(os.environ.get("HARNESS_MIN_AGGREGATE", "0.5"))


# ---------- Race configuration ----------

TOOL_BUDGET_PER_AGENT: int = int(os.environ.get("TOOL_BUDGET_PER_AGENT", "40"))
RACE_DEADLINE_SECONDS: float = float(os.environ.get("RACE_DEADLINE_SECONDS", "600"))


# ---------- Local persistence ----------

SQLITE_PATH: str = os.environ.get("SQLITE_PATH", "./prism.db")


# ---------- Live-tool caches ----------

# TTL for the OpenStreetCam photo cache keyed on (lat_round, lng_round, day_bucket).
# The upstream endpoint is the slowest of the live-tool set, so a day-scale cache
# is mandatory rather than an optional optimisation.
STREETVIEW_CACHE_TTL_HOURS: int = int(os.environ.get("STREETVIEW_CACHE_TTL_HOURS", "24"))

# Traffic and incident snapshots are ephemeral; keep them short so the Live Canvas
# reflects current conditions but agents do not re-pull the same call within a race.
TRAFFIC_CACHE_TTL_SECONDS: int = int(os.environ.get("TRAFFIC_CACHE_TTL_SECONDS", "60"))
INCIDENT_CACHE_TTL_SECONDS: int = int(os.environ.get("INCIDENT_CACHE_TTL_SECONDS", "60"))


# ---------- Server ----------

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001"
)
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if o.strip()
]
