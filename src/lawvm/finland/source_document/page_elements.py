"""Compat shim (Track A move): re-export ``lawvm.ingest.page_elements``.

See ``struct_wire`` shim. Re-exports the public surface + aliases the module in
``sys.modules`` so old imports and monkeypatch keep working. Later-step removal.
"""
import sys

from lawvm.ingest import page_elements as _moved
from lawvm.ingest.page_elements import *  # noqa: F401,F403

sys.modules[__name__] = _moved
