"""Compat shim (Track A move): re-export ``lawvm.ingest.llm_backends.vision_producer``.

The neutral vision/adjudication backend moved to ``lawvm.ingest.llm_backends``.
This module re-exports its public surface + aliases it in ``sys.modules`` so old
imports and monkeypatch keep working byte-identically. The FI manual-CLAIMS
``qwen_local`` STAYS in this package. Later-step removal.
"""
import sys

from lawvm.ingest.llm_backends import vision_producer as _moved
from lawvm.ingest.llm_backends.vision_producer import *  # noqa: F401,F403

sys.modules[__name__] = _moved
