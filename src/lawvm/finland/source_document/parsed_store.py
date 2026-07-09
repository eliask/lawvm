"""Compat shim (Track A move): re-export ``lawvm.ingest.parsed_store``.

See ``struct_wire`` shim. Re-exports the public surface + the two private helpers
consumers reach (``_serialize_parsed_record`` / ``_assurance_summary``) + aliases
the module in ``sys.modules`` so old imports and monkeypatch keep working.
Later-step removal.
"""
import sys

from lawvm.ingest import parsed_store as _moved
from lawvm.ingest.parsed_store import *  # noqa: F401,F403
from lawvm.ingest.parsed_store import (  # noqa: F401
    _assurance_summary,
    _serialize_parsed_record,
)

sys.modules[__name__] = _moved
