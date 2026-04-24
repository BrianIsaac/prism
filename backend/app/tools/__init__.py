"""Agent-callable tool belt.

Phase 3 populates ``GRABMAPS_TOOLS`` with the eight agent-callable tools and
``GRABMAPS_TOOL_SCHEMA`` with their Anthropic / OpenAI / Gemini-normalised
schemas. ``emit_thought`` is deliberately NOT in this dict — it is
instrumentation that skips budget accounting.
"""

from __future__ import annotations

from typing import Any

GRABMAPS_TOOLS: dict[str, Any] = {}
GRABMAPS_TOOL_SCHEMA: list[dict[str, Any]] = []
