"""Frozen scoring harness — v2 signatures only.

FROZEN CONTRACT
---------------
The harness is the one part of Prism that every agent and every race shares
verbatim. Do NOT change:
    - ``HARNESS_VERSION`` string without a matching seed entry in
      :func:`app.storage.insert_weight_snapshot`
    - ``HARNESS_WEIGHTS`` default dict keys (``flow``, ``diversity``, ``vibe``)
    - The ``score_plan`` signature — the judge agent and the race runner both
      import it with the exact kwargs below

Phase 1 fills in the bodies. Phase 0 only provides the contract surface so
:mod:`app.main` and the agent modules can import without circularity.
"""

from __future__ import annotations

from typing import Any

HARNESS_VERSION: str = "v1"
HARNESS_WEIGHTS: dict[str, float] = {"flow": 0.5, "diversity": 0.2, "vibe": 0.3}


async def score_and_rank(
    plans: list[dict[str, Any]],
    spec: Any,
    *,
    weights: dict[str, float] | None = None,
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Score every plan, drop hard failures, rank survivors by aggregate score.

    Phase 1 implementation.

    Args:
        plans: Raw plan dicts emitted by each agent.
        spec: The parsed :class:`~app.models.Spec`.
        weights: Optional override of :data:`HARNESS_WEIGHTS` (used for drifted
            runtime weights).
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs to feed the vibe
            judge. Passed through unchanged from ``run_race``.

    Returns:
        Scored plans with ``hard_pass``, ``soft_scores``, ``total_score``, and
        ``rank`` fields populated. Order is not guaranteed.
    """
    raise NotImplementedError


def score_plan(
    plan: dict[str, Any],
    spec: Any,
    streetview_urls_by_poi: dict[str, list[str]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Score a single plan in isolation (used by the ratchet retry loop).

    Phase 1 implementation.

    Args:
        plan: The plan dict.
        spec: The parsed :class:`~app.models.Spec`.
        streetview_urls_by_poi: Per-POI OpenStreetCam URLs for the vibe judge.
        **kwargs: Forward-compat for future scoring dimensions.

    Returns:
        A dict with ``hard_pass``, ``soft_scores``, ``total_score``, and any
        ``failures`` encountered.
    """
    raise NotImplementedError
