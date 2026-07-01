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
from typing import Any, Iterable, Mapping

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
_SIMILARITY_NON_WORD_RE = re.compile(r"[^\w]")


def clean_similarity_text(text: str) -> str:
    """Normalize text for evidence-only similarity scoring."""

    return _SIMILARITY_NON_WORD_RE.sub("", text.lower())


def section_similarity_cleaned(lhs: str, rhs: str) -> float:
    """Return section similarity for strings already normalized by this module."""

    if not lhs and not rhs:
        return 1.0
    if not lhs or not rhs:
        return 0.0
    return Levenshtein.ratio(lhs, rhs)


def section_similarity(replay_text: str, oracle_text: str) -> float:
    lhs = clean_similarity_text(replay_text or "")
    rhs = clean_similarity_text(oracle_text or "")
    return section_similarity_cleaned(lhs, rhs)


def best_section_similarity_cleaned(
    cleaned_replay: Iterable[str], cleaned_oracle: Iterable[str]
) -> float:
    """Return ``max(section_similarity_cleaned(a, b))`` over cleaned text."""

    replay_values = list(cleaned_replay)
    oracle_values = list(cleaned_oracle)
    best = -1.0
    for lhs in replay_values:
        lhs_len = len(lhs)
        for rhs in oracle_values:
            rhs_len = len(rhs)
            # Reproduce section_similarity's empty-text special cases exactly.
            if not lhs_len and not rhs_len:
                score = 1.0
            elif not lhs_len or not rhs_len:
                score = 0.0
            else:
                # Cheap length upper bound on the indel ratio; skip pairs that
                # provably cannot beat the current best (the ratio would be
                # discarded by max anyway).
                shorter = lhs_len if lhs_len < rhs_len else rhs_len
                length_bound = (2.0 * shorter) / (lhs_len + rhs_len)
                if length_bound < best:
                    continue
                cutoff = best if best > 0.0 else None
                score = Levenshtein.ratio(lhs, rhs, score_cutoff=cutoff)
            if score > best:
                best = score
                if best >= 1.0:
                    # 1.0 is the global maximum; nothing can beat it.
                    return 1.0
    return best


def best_section_similarity(
    replay_texts: Iterable[str], oracle_texts: Iterable[str]
) -> float:
    """Return ``max(section_similarity(a, b))`` over the raw-text cross product.

    Byte-identical to::

        max(section_similarity(a, b) for a in replay_texts for b in oracle_texts)

    but pruned so the underlying ``Levenshtein.ratio`` runs only on pairs that can
    still beat the running best. The cross product is the NZ chain-replay O(N^2)
    hotspot (~1.6M ratio calls per act). Two behavior-preserving prunes:

    - Each raw text is cleaned once (``clean_similarity_text`` is not re-run per
      pair as the naive genexpr does).
    - The indel ratio has the length upper bound ``2*min(la, lb) / (la + lb)``
      (the indel distance is at least ``|la - lb|``). When that bound is below the
      running best the true ratio cannot beat it, so the pair is skipped without a
      ratio call. Otherwise ``Levenshtein.ratio`` is called with ``score_cutoff``
      set to the running best: it returns the exact ratio when it is >= the best
      and 0.0 otherwise, neither of which can lower the max. The seeded max is thus
      identical to the unpruned reduction.

    The empty inputs raise ``ValueError`` from the naive ``max`` too, so the caller
    guards them; this mirrors that (an empty product yields no candidates).
    """

    cleaned_replay = [clean_similarity_text(text or "") for text in replay_texts]
    cleaned_oracle = [clean_similarity_text(text or "") for text in oracle_texts]
    return best_section_similarity_cleaned(cleaned_replay, cleaned_oracle)


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
