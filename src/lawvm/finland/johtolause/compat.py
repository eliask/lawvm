"""compat — Re-export facade, kept for backward compatibility.

The canonical implementation now lives in api.py.  All existing
``from lawvm.finland.johtolause.compat import X`` import sites continue
to work.  New code should import from api.py directly.
"""

from lawvm.finland.johtolause.api import (  # noqa: F401
    ClauseParseResult,
    derive_features,
    parse_clause,
)
