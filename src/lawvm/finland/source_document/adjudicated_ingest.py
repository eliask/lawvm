"""Compat shim (Track A move): re-export ``lawvm.ingest.adjudicated_ingest``.

See ``struct_wire`` shim. The ``sys.modules`` alias is load-bearing: tests
``monkeypatch.setattr`` this module's ``reading_order_pages_from_pdf`` and expect
``struct_document_ingest`` (module-global lookup) to see it. Later-step removal.
"""
import sys

from lawvm.ingest import adjudicated_ingest as _moved
from lawvm.ingest.adjudicated_ingest import *  # noqa: F401,F403

sys.modules[__name__] = _moved
