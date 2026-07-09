"""Compat shim (Track A move): re-export ``lawvm.ingest.llm_backends.docling_producer``.

See ``struct_wire`` shim. Re-exports the public surface + aliases the module in
``sys.modules`` so old imports and monkeypatch keep working. Later-step removal.
"""
import sys

from lawvm.ingest.llm_backends import docling_producer as _moved
from lawvm.ingest.llm_backends.docling_producer import *  # noqa: F401,F403

sys.modules[__name__] = _moved
