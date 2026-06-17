"""Pure-Python, vendored, generation-first Finnish morphology rule engine (M1).

No external dependency (no Voikko/Omorfi/HFST/neural).  The engine generates the
``reference_v1`` case forms from a :class:`MorphEntry` (lemma + morph_class +
flags), and :func:`classify` assigns a morph_class from a surface where the rule
is categorical --- failing loud (typed status) on the genuine walls.

See ``notes_internal/FI_MORPHOLOGY_DESIGN_DECISION.md`` for the full spec.
"""

from __future__ import annotations

from .api import (
    REFERENCE_V1_PL,
    REFERENCE_V1_SG,
    MorphCase,
    MorphEntry,
    MorphForm,
    MorphNumber,
)
from .classify import Classification, classify
from .generate import generate_forms
from .heads import head_entry, is_known_head
from .lemma_index import LemmaIndex, build_lemma_index

__all__ = [
    "REFERENCE_V1_PL",
    "REFERENCE_V1_SG",
    "Classification",
    "LemmaIndex",
    "MorphCase",
    "MorphEntry",
    "MorphForm",
    "MorphNumber",
    "build_lemma_index",
    "classify",
    "generate_forms",
    "head_entry",
    "is_known_head",
]
