"""FI-facing re-export of the op-equivalence quotient.

The inert-encoding fold algebra is jurisdiction-NEUTRAL and now lives in
:mod:`lawvm.core.op_equivalence` (parameterised by a :class:`~lawvm.core.op_equivalence.FoldProfile`
that injects the surface's punctuation/separator glyph sets). It was promoted out of
``finland/`` so EE/EU/US/NZ/UK reuse the SAME fold algebra instead of forking a worse one.

This module re-exports the full public API unchanged, and additionally the private
``_canonicalize_text`` helper, so every existing FI caller and test keeps importing
``from lawvm.finland.op_equivalence import ...`` with no edit. The FI default profile
(:data:`~lawvm.core.op_equivalence.DEFAULT_FOLD_PROFILE`) reproduces the exact historical
Finnish behaviour, so ``text_equivalence(a, b)`` here is byte-identical to before.
"""
from __future__ import annotations

from lawvm.core.op_equivalence import *  # noqa: F401,F403  (re-export the public fold API)
from lawvm.core.op_equivalence import (  # noqa: F401  explicit names (incl. non-public helper)
    DEFAULT_FOLD_PROFILE,
    EncodingFold,
    FoldProfile,
    TextEquivalence,
    _canonicalize_text,
    make_fold_profile,
    text_equivalence,
)

__all__ = [
    "DEFAULT_FOLD_PROFILE",
    "EncodingFold",
    "FoldProfile",
    "TextEquivalence",
    "_canonicalize_text",
    "make_fold_profile",
    "text_equivalence",
]
