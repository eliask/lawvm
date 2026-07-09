"""Compat shim (Track A move): re-export ``lawvm.ingest.struct_wire``.

The neutral struct-build wire parser/assembler moved to ``lawvm.ingest`` so
every jurisdiction can use it. This module re-exports its public surface AND
aliases it in ``sys.modules`` so existing ``from lawvm.finland.source_document
import struct_wire`` imports — and ``monkeypatch.setattr`` against this module —
keep working byte-identically. Shim removal is a later step.
"""
import sys

from lawvm.ingest import struct_wire as _moved
from lawvm.ingest.struct_wire import *  # noqa: F401,F403

sys.modules[__name__] = _moved
