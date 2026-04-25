"""Small evidence helpers shared by core proof types.

These helpers are intentionally tool-agnostic so core modules can depend on
them without importing the tools layer.

API tier
--------
Internal shared helper surface. Depend on this from core modules freely, but do
not treat it as a public stable product contract.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

import Levenshtein

# Status: internal shared diagnostic vocabulary, not a persisted public
# contract.
REPLAY_BUG_DIAGNOSES = frozenset({
    "REPLAY_EXTRA",
    "REPLAY_MISSING",
    "UNKNOWN",
    "EXTRA",
    "MISSING",
})

# Status: internal shared diagnostic vocabulary, not a persisted public
# contract.
ORACLE_INCORRECT_DIAGNOSES = frozenset({
    "ORACLE_STALE",
    "CORRIGENDUM_APPLIED",
    "EDITORIAL_CONVENTION",
    "LIITE_DIFF",
})

_PREEXISTING_LOW_BASELINE_SCORE = 0.75
_NEGLIGIBLE_BLAME_DROP_EPS = 0.01


def _clean_similarity_text(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def section_similarity(replay_text: str, oracle_text: str) -> float:
    lhs = _clean_similarity_text(replay_text or "")
    rhs = _clean_similarity_text(oracle_text or "")
    if not lhs and not rhs:
        return 1.0
    if not lhs or not rhs:
        return 0.0
    return Levenshtein.ratio(lhs, rhs)


def has_negligible_blame_drop_on_preexisting_residue(support: Mapping[str, Any]) -> bool:
    baseline_score = float(support.get("baseline_score") or 0.0)
    first_bad_source = str(support.get("first_bad_source") or "")
    blame_source = str(support.get("blame_source") or "")
    before_score = support.get("blame_before_score")
    after_score = support.get("blame_after_score")
    if not first_bad_source or not blame_source or first_bad_source == blame_source:
        return False
    if baseline_score > _PREEXISTING_LOW_BASELINE_SCORE:
        return False
    if before_score is None or after_score is None:
        return False
    try:
        delta = float(before_score) - float(after_score)
    except (TypeError, ValueError):
        return False
    return 0.0 <= delta <= _NEGLIGIBLE_BLAME_DROP_EPS
