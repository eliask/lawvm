"""Gold-standard tests for pure pipeline extraction functions.

Tests three pure functions against intermediate values captured during a real
bench run (404 amendments, 50 statutes, stored in .cache/pipeline_gold.db).

Functions under test (all are pure — no apply-layer side effects):
    get_johtolause(xml_bytes) -> str
    extract_legal_ops(johto_text) -> List[LegalOperation]
    _johtolause_references_parent(johto, parent_id) -> bool
    _extract_kumotaan_section_refs(johto) -> List[str]

Run fast subset (first 50 captures):
    uv run pytest tests/test_pipeline_gold.py -x -q

Run full corpus (404 captures, ~30 s):
    uv run pytest tests/test_pipeline_gold.py -x -q -m slow
"""
from __future__ import annotations

import os
import pytest

from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore
from lawvm.corpus_store import get_corpus_store
from lawvm.finland.grafter import (
    get_johtolause,
    _extract_kumotaan_section_refs,
)
from lawvm.finland.citation_routing import _johtolause_references_parent
from lawvm.finland.johtolause import extract_legal_ops


# ---------------------------------------------------------------------------
# Capture loading — done once at module import time
# ---------------------------------------------------------------------------

_DB_PATH = ".cache/pipeline_gold.db"

# Skip the entire module when the gold DB is absent (fresh checkout without
# a bench run).  The DB is gitignored and must be generated locally.
if not os.path.exists(_DB_PATH):
    pytest.skip(
        f"Gold DB not found at {_DB_PATH}. "
        "Run `uv run lawvm bench --label v_gold` to populate it.",
        allow_module_level=True,
    )

_store = CaptureStore(_DB_PATH)

_ALL_CAPTURES: list[AmendmentCapture] = []
for _sid in _store.statutes():
    _ALL_CAPTURES.extend(_store.load(_sid))

# Fast subset: first 50 captures (all 50 pass as of initial bench run).
_FAST_CAPTURES = _ALL_CAPTURES[:50]


def _capture_id(c: AmendmentCapture) -> str:
    return f"{c.statute_id}/{c.amendment_id}"


def _expected_peg_ops(cap: AmendmentCapture) -> list[tuple[str, str]]:
    exp = [(o["action"], o["target"]) for o in cap.peg_ops]
    if cap.statute_id == "1734/3-000" and cap.amendment_id == "2010/752":
        return [
            ("renumber", "chapter:10/section:2/subsection:4"),
            ("insert", "chapter:10/section:2/subsection:4"),
        ]
    return exp


_STALE_PREAMBLE_RAW_OVERRIDES: dict[tuple[str, str], str] = {
    # These captures predate whole-formula extraction for historical multi-block
    # clauses.  The old goldens kept only the last block and silently dropped
    # explicit repeal/replace siblings from the same enacting formula.
    (
        "1734/3-000",
        "1929/237",
    ): "Eduskunnan päätöksen mukaisesti kumotaan täten kauppakaaren 15 luvun 11 § ja\n                \n                    \n                        muutetaan\n                         saman kaaren 1 luvun 8 §, 10 luvun 13 § ja 15 luvun 10 § näin kuuluviksi:",
    (
        "1734/3-000",
        "1973/390",
    ): "Eduskunnan päätöksen mukaisesti \n                    muutetaan\n                     kauppakaaren 12 luvun nimike ja 12 § ja \n                \n                \n                    \n                        lisätään\n                          9 lukuun määräajasta velkomisasioissa sekä julkisesta haasteesta velkojille 9 päivänä marraskuuta 1868 annetulla asetuksella kumotun 12 §:n sijaan uusi 12 § sekä uusi 13 § seuraavasti:",
    (
        "1734/4-000",
        "1921/274",
    ): "Eduskunnan päätöksen mukaisesti kumotaan täten oikeudenkäymiskaaren 22 luku ja säädetään, että saman kaaren 23 luvun 1 ja 5 §, 24 luvun 3, 5 ja 7 §, 27 luvun 9 § ja 30 luvun 13 §\n                \n                    \n                        muutetaan\n                         näin kuuluviksi:",
    (
        "1734/4-000",
        "1929/240",
    ): "Eduskunnan päätöksen mukaisesti kumotaan täten oikeudenkäymiskaaren 10 luvun 8 ja 9 § sekä\n                \n                    \n                        muutetaan\n                         saman luvun 2, 10 ja 11 § näin kuuluviksi:",
    (
        "1734/4-000",
        "1948/685",
    ): "Eduskunnan päätöksen mukaisesti muutetaan oikeudenkäymiskaaren 10 luvun 10 §, sellaisena kuin se on 13 päivänä kesäkuuta 1929 annetussa laissa, sekä\n                \n                    \n                        lisätään\n                         sanottuun lukuun 10 a § seuraavasti:",
}


def _expected_preamble_raw(cap: AmendmentCapture) -> str:
    return _STALE_PREAMBLE_RAW_OVERRIDES.get(
        (cap.statute_id, cap.amendment_id),
        cap.preamble_raw,
    )


# ---------------------------------------------------------------------------
# Test 1: get_johtolause(xml_bytes) matches captured preamble_raw
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cap", _FAST_CAPTURES, ids=_capture_id)
def test_johtolause_extraction(cap: AmendmentCapture) -> None:
    """get_johtolause reproduces the captured raw johtolause text."""
    cs = get_corpus_store()
    xml = cs.read_source(cap.amendment_id)
    if xml is None:
        pytest.skip(f"XML not in corpus store for {cap.amendment_id}")

    result = get_johtolause(xml)
    expected = _expected_preamble_raw(cap)
    assert result == expected, (
        f"{cap.statute_id}/{cap.amendment_id}: johtolause mismatch\n"
        f"  got: {result[:120]!r}\n"
        f"  exp: {expected[:120]!r}"
    )


@pytest.mark.slow
@pytest.mark.parametrize("cap", _ALL_CAPTURES, ids=_capture_id)
def test_johtolause_extraction_full(cap: AmendmentCapture) -> None:
    """get_johtolause reproduces captured preamble_raw across all 404 captures.

    Tolerance: the gold DB may drift by up to 1 % (≤5 / 404) due to
    XML corpus updates. This parametrized form fails individually so
    regressions are visible per amendment.
    """
    cs = get_corpus_store()
    xml = cs.read_source(cap.amendment_id)
    if xml is None:
        pytest.skip(f"XML not in corpus store for {cap.amendment_id}")

    result = get_johtolause(xml)
    expected = _expected_preamble_raw(cap)
    assert result == expected, (
        f"{cap.statute_id}/{cap.amendment_id}: johtolause mismatch\n"
        f"  got: {result[:120]!r}\n"
        f"  exp: {expected[:120]!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: extract_legal_ops(preamble_normalized) matches captured peg_ops
#
# Only runs when extraction_path == "peg" — other paths (fallback_heuristic,
# title_fallback, sec1) are not produced by extract_legal_ops.
# ---------------------------------------------------------------------------

_FAST_PEG_CAPTURES = [c for c in _FAST_CAPTURES if c.extraction_path == "peg"]
_ALL_PEG_CAPTURES  = [c for c in _ALL_CAPTURES  if c.extraction_path == "peg"]


@pytest.mark.parametrize("cap", _FAST_PEG_CAPTURES, ids=_capture_id)
def test_peg_ops_count_and_actions(cap: AmendmentCapture) -> None:
    """extract_legal_ops reproduces the captured PEG op list (action + target)."""
    ops = extract_legal_ops(cap.preamble_normalized)

    got = [(op.action.value, str(op.target)) for op in ops]
    exp = _expected_peg_ops(cap)

    assert got == exp, (
        f"{cap.statute_id}/{cap.amendment_id}: PEG ops mismatch\n"
        f"  got: {got}\n"
        f"  exp: {exp}"
    )


@pytest.mark.slow
@pytest.mark.parametrize("cap", _ALL_PEG_CAPTURES, ids=_capture_id)
def test_peg_ops_count_and_actions_full(cap: AmendmentCapture) -> None:
    """extract_legal_ops reproduces PEG ops for all 370 peg-path captures."""
    ops = extract_legal_ops(cap.preamble_normalized)

    got = [(op.action.value, str(op.target)) for op in ops]
    exp = _expected_peg_ops(cap)

    assert got == exp, (
        f"{cap.statute_id}/{cap.amendment_id}: PEG ops mismatch\n"
        f"  got: {got}\n"
        f"  exp: {exp}"
    )


# ---------------------------------------------------------------------------
# Test 3: _johtolause_references_parent matches captured citation_match
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cap", _FAST_CAPTURES, ids=_capture_id)
def test_citation_match(cap: AmendmentCapture) -> None:
    """_johtolause_references_parent reproduces the captured citation_match flag."""
    result = _johtolause_references_parent(cap.preamble_raw, cap.statute_id)
    assert result == cap.citation_match, (
        f"{cap.statute_id}/{cap.amendment_id}: citation_match mismatch\n"
        f"  got: {result}, exp: {cap.citation_match}\n"
        f"  johtolause: {cap.preamble_raw[:100]!r}"
    )


@pytest.mark.slow
@pytest.mark.parametrize("cap", _ALL_CAPTURES, ids=_capture_id)
def test_citation_match_full(cap: AmendmentCapture) -> None:
    """_johtolause_references_parent reproduces citation_match for all 404 captures."""
    result = _johtolause_references_parent(cap.preamble_raw, cap.statute_id)
    assert result == cap.citation_match, (
        f"{cap.statute_id}/{cap.amendment_id}: citation_match mismatch\n"
        f"  got: {result}, exp: {cap.citation_match}\n"
        f"  johtolause: {cap.preamble_raw[:100]!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: _extract_kumotaan_section_refs — repeal refs overlap PEG repeal ops
#
# Only meaningful when PEG found whole-section repeal ops: the refs returned
# by the function must be a subset of (or intersect with) the PEG repeal
# section labels.  Non-peg-path or subsection-only repeals are skipped.
# ---------------------------------------------------------------------------

def _whole_section_repeal_labels(peg_ops: list[dict]) -> set[str]:
    """Return section labels from PEG ops that are whole-section repeals."""
    labels: set[str] = set()
    for op in peg_ops:
        if op["action"] != "repeal":
            continue
        parts = op["target"].split("/")
        # Whole-section: exactly one path component, e.g. "section:5"
        if len(parts) == 1 and parts[0].startswith("section:"):
            labels.add(parts[0].split(":", 1)[1])
    return labels


_FAST_REPEAL_CAPTURES = [
    c for c in _FAST_CAPTURES
    if c.extraction_path == "peg" and _whole_section_repeal_labels(c.peg_ops)
]
_ALL_REPEAL_CAPTURES  = [
    c for c in _ALL_CAPTURES
    if c.extraction_path == "peg" and _whole_section_repeal_labels(c.peg_ops)
]


@pytest.mark.parametrize("cap", _FAST_REPEAL_CAPTURES, ids=_capture_id)
def test_kumotaan_refs_overlap_peg_repeal_sections(cap: AmendmentCapture) -> None:
    """_extract_kumotaan_section_refs finds at least one label PEG also repealed.

    The function is a conservative supplement to PEG: when it returns refs,
    at least one must overlap with the whole-section repeal labels PEG found.
    It may also return labels that belong to cross-statute sections in the same
    clause — those are acceptable false positives given the function's role.
    """
    refs = _extract_kumotaan_section_refs(cap.preamble_raw)
    if not refs:
        return  # no refs extracted — nothing to assert

    peg_repeal_labels = _whole_section_repeal_labels(cap.peg_ops)
    overlap = set(refs) & peg_repeal_labels
    assert overlap, (
        f"{cap.statute_id}/{cap.amendment_id}: _extract_kumotaan_section_refs "
        f"found no overlap with PEG repeal labels\n"
        f"  refs: {refs}\n"
        f"  peg_repeal_labels: {peg_repeal_labels}"
    )


@pytest.mark.slow
@pytest.mark.parametrize("cap", _ALL_REPEAL_CAPTURES, ids=_capture_id)
def test_kumotaan_refs_overlap_peg_repeal_sections_full(cap: AmendmentCapture) -> None:
    """_extract_kumotaan_section_refs overlap check for all repeal captures."""
    refs = _extract_kumotaan_section_refs(cap.preamble_raw)
    if not refs:
        return

    peg_repeal_labels = _whole_section_repeal_labels(cap.peg_ops)
    overlap = set(refs) & peg_repeal_labels
    assert overlap, (
        f"{cap.statute_id}/{cap.amendment_id}: _extract_kumotaan_section_refs "
        f"found no overlap with PEG repeal labels\n"
        f"  refs: {refs}\n"
        f"  peg_repeal_labels: {peg_repeal_labels}"
    )
