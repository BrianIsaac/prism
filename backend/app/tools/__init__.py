"""Agent-callable tool belt.

Merges the five classic CRUD tools from :mod:`app.tools.grabmaps` with the
three live-overlay tools from :mod:`app.tools.live` into a single dispatch
dict and a single schema list. ``emit_thought`` is deliberately NOT in this
dict — it is instrumentation routed through :func:`call_tool_with_budget`
that skips budget accounting and trace persistence.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import (
    Budget,
    ToolBudgetExceeded,
    ToolTrace,
    call_tool_with_budget,
)
from app.tools.grabmaps import (
    GRABMAPS_TOOL_SCHEMA as _GRABMAPS_CLASSIC_SCHEMA,
    GRABMAPS_TOOLS as _GRABMAPS_CLASSIC_TOOLS,
    nearby_search,
    places_search,
    reverse_geocode,
    route,
    route_matrix,
)
from app.tools.live import (
    GRABMAPS_TOOL_SCHEMA as _GRABMAPS_LIVE_SCHEMA,
    GRABMAPS_TOOLS as _GRABMAPS_LIVE_TOOLS,
    get_incidents,
    get_street_view,
    get_traffic,
)


GRABMAPS_TOOLS: dict[str, Any] = {
    **_GRABMAPS_CLASSIC_TOOLS,
    **_GRABMAPS_LIVE_TOOLS,
}


GRABMAPS_TOOL_SCHEMA: list[dict[str, Any]] = [
    *_GRABMAPS_CLASSIC_SCHEMA,
    *_GRABMAPS_LIVE_SCHEMA,
]


__all__ = [
    "Budget",
    "GRABMAPS_TOOLS",
    "GRABMAPS_TOOL_SCHEMA",
    "ToolBudgetExceeded",
    "ToolTrace",
    "call_tool_with_budget",
    "get_incidents",
    "get_street_view",
    "get_traffic",
    "nearby_search",
    "places_search",
    "reverse_geocode",
    "route",
    "route_matrix",
]
