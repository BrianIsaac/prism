"""Vibe-judge agent (Haiku).

Phase 2 implementation. Takes real OpenStreetCam photo URLs per POI and
returns a per-POI vibe score. The signature is fixed so the harness can
import it at boot time.
"""

from __future__ import annotations

from typing import Any


async def judge_vibe(
    plan: dict[str, Any],
    spec: Any,
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    """Return a per-POI vibe score from real street-view imagery.

    Phase 2 implementation.

    Args:
        plan: The plan dict being scored.
        spec: The parsed :class:`~app.models.Spec`.
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs. Never prose.

    Returns:
        Mapping of POI id → vibe score in [0, 1].
    """
    raise NotImplementedError
