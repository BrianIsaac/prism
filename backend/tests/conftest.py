"""Shared pytest fixtures for the Prism backend test suite.

Phase 0 provides two fixtures:
    - ``isolated_sqlite`` — a per-test SQLite path so tables from one test
      never leak into another. Schema is created via
      :func:`app.storage.init_db`; Phase 1 fills in the DDL.
    - ``mock_grabmaps`` — a ``respx`` mock router scoped to the GrabMaps base
      URL so integration tests can exercise the full tool belt without
      spending live credentials. This is a *test-only* affordance; production
      calls remain live.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
import respx


@pytest.fixture
async def isolated_sqlite() -> AsyncIterator[str]:
    """Yield a fresh SQLite path for a single test, creating the schema on entry."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "prism.db")
        previous = os.environ.get("SQLITE_PATH")
        os.environ["SQLITE_PATH"] = db_path
        try:
            from app.storage import init_db

            await init_db()
            yield db_path
        finally:
            if previous is None:
                os.environ.pop("SQLITE_PATH", None)
            else:
                os.environ["SQLITE_PATH"] = previous


@pytest.fixture
def mock_grabmaps() -> Iterator[respx.MockRouter]:
    """Yield a ``respx`` router scoped to the GrabMaps base URL.

    Tests opt in per-endpoint; unmatched calls raise so accidental live hits
    are loud rather than silent.
    """
    from app.config import GRABMAPS_BASE_URL

    with respx.mock(base_url=GRABMAPS_BASE_URL, assert_all_called=False) as router:
        yield router
