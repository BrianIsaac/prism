"""Natural-language → :class:`~app.models.Spec` parser.

Phase 2 implementation. The shell exists so :mod:`app.main` can import
:func:`parse_spec` at boot time.
"""

from __future__ import annotations

from app.models import Spec


async def parse_spec(query: str) -> Spec:
    """Parse a user query into a structured :class:`~app.models.Spec`.

    Phase 2 implementation — dispatches to the spec-parser LLM.

    Args:
        query: The raw natural-language query.

    Returns:
        A populated :class:`Spec` with defaults for anything the parser could
        not extract.
    """
    raise NotImplementedError
