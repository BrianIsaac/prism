"""Shared HTTP helpers for the GrabMaps tool layer.

Every live tool call in :mod:`app.tools.grabmaps` and :mod:`app.tools.live`
goes through :func:`get_json` / :func:`get_bytes`. They provide:

    - **Concurrency cap.** A process-wide :class:`asyncio.Semaphore` limits
      in-flight upstream requests so a 5x5 ``route_matrix`` (25 cells) or
      three agents each firing parallel tool calls cannot overwhelm
      GrabMaps and cause cascading 502s. Default cap is six; override via
      ``GRABMAPS_CONCURRENCY`` env var.
    - **Retries with tenacity.** Up to three attempts with exponential
      backoff (0.5s → 1s → 2s) on network errors, timeouts, and 5xx
      responses. 4xx responses are propagated immediately because
      retrying a bad-input call is wasteful and would still land the
      same error in the Bug Hunter trace.
    - **Auth injection.** The Bearer header is derived lazily per request
      (so a late ``.env`` fix takes effect without restarting the helper).

The concurrency cap is set up front because uncapped ``asyncio.gather`` on
a 5x5 matrix opens 25 TCP connections to ``maps.grab.com`` before the
first response returns — enough to trigger the per-IP rate limit on
Grab's side and surface as the 4xx / 5xx storms we saw during early
live-race runs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import GRABMAPS_API_KEY, GRABMAPS_BASE_URL


_GRABMAPS_CONCURRENCY: int = int(os.environ.get("GRABMAPS_CONCURRENCY", "6"))
_SEM = asyncio.Semaphore(_GRABMAPS_CONCURRENCY)


def _auth_headers() -> dict[str, str]:
    """Return the Bearer header, raising loudly when the key is unset.

    Failing here surfaces the misconfiguration on the first live call,
    before the agent has wasted a tool-budget slot on an anonymous
    request that would silently pass the proxy and hit a real 401.
    """
    if not GRABMAPS_API_KEY:
        raise RuntimeError(
            "GRABMAPS_API_KEY is not set — populate backend/.env before live calls"
        )
    return {"Authorization": f"Bearer {GRABMAPS_API_KEY}"}


def _should_retry(exc: BaseException) -> bool:
    """tenacity predicate — retry transient failures only.

    4xx client errors indicate the request itself is wrong (bad keyword,
    out-of-range bbox, stale tile coordinate). Retrying them burns budget
    and turns one trace row into three. 5xx and network errors usually
    clear on a short delay, so they are worth one or two attempts.
    """
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, (httpx.NetworkError, httpx.ConnectError, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


_retry_policy = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)


@_retry_policy
async def get_json(
    path: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    *,
    timeout: float = 15.0,
) -> Any:
    """GET a JSON endpoint under the shared semaphore + retry policy.

    Args:
        path: URL path appended to :data:`GRABMAPS_BASE_URL`. Must start with ``/``.
        params: Query parameters (dict or list of pairs for repeated keys).
        timeout: Per-attempt timeout in seconds.

    Returns:
        The decoded JSON body.

    Raises:
        httpx.HTTPStatusError: On 4xx (no retry) or persistent 5xx.
        httpx.TimeoutException / httpx.NetworkError: After retries exhaust.
    """
    url = f"{GRABMAPS_BASE_URL}{path}"
    async with _SEM:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=_auth_headers())
            response.raise_for_status()
            return response.json()


@_retry_policy
async def get_bytes(
    path: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    *,
    timeout: float = 10.0,
) -> tuple[bytes, str]:
    """GET a binary endpoint under the shared semaphore + retry policy.

    Used for the raster tile proxies where MapLibre needs raw bytes + the
    upstream content-type rather than a parsed JSON body.

    Returns:
        ``(body, content_type)`` — body as bytes, content-type string from
        the upstream response.
    """
    url = f"{GRABMAPS_BASE_URL}{path}"
    async with _SEM:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=_auth_headers())
            response.raise_for_status()
            return response.content, response.headers.get(
                "content-type", "application/octet-stream"
            )


__all__ = ["get_json", "get_bytes"]
