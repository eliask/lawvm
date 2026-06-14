"""Roman↔Arabic numbering-scheme canonicalization for UK oracle comparison.

Older enacted Acts number sections/parts in Roman (``section-II``); the modern
consolidated oracle uses Arabic (``section-2``). The bench scored these as zero
common eIds despite identical provisions. ``canonicalize_compare_eid`` (and the
bench's ``_score_eids``, which uses it) make the eId comparison numbering-scheme
invariant — but ONLY at Roman-numbered structural container levels, never at leaf
paragraph labels or lettered schedules, so it cannot manufacture a false match.
"""

from __future__ import annotations

from lawvm.uk_legislation.canonicalize import canonicalize_compare_eid
from lawvm.tools.uk_bench import _score_eids


def test_section_roman_canonicalizes_to_arabic():
    assert canonicalize_compare_eid("section-II") == "section-2"
    assert canonicalize_compare_eid("section-LXXIX") == "section-79"
    assert canonicalize_compare_eid("section-C") == "section-100"
    assert canonicalize_compare_eid("part-IV") == "part-4"
    # nested: section II subsection 3
    assert canonicalize_compare_eid("section-II-3") == "section-2-3"


def test_arabic_eids_are_identity():
    for eid in ("section-2", "section-2-3", "section-2-3-a", "part-4", "schedule-1"):
        assert canonicalize_compare_eid(eid) == eid


def test_leaf_labels_and_letters_preserved():
    # A leaf paragraph label "(iv)" is a genuine printed series, NOT an alias of 4.
    assert canonicalize_compare_eid("section-2-iv") == "section-2-iv"
    assert canonicalize_compare_eid("paragraph-iv") == "paragraph-iv"
    # Lettered schedules: "C" is the letter C, not Roman 100.
    assert canonicalize_compare_eid("schedule-C") == "schedule-C"
    assert canonicalize_compare_eid("schedule-A") == "schedule-A"
    # Non-canonical Roman + letter suffix is left intact.
    assert canonicalize_compare_eid("section-IIA") == "section-IIA"


def test_score_is_numbering_scheme_invariant():
    # The headline fix: a Roman-numbered Act scores common with its Arabic oracle.
    enacted = {"section-I", "section-II", "section-LIX"}
    oracle = {"section-1", "section-2", "section-59"}
    assert _score_eids(enacted, oracle) == 1.0


def test_score_does_not_manufacture_false_matches():
    # A leaf sub-paragraph (iv) must NOT alias subsection 4.
    assert _score_eids({"section-2-iv"}, {"section-2-4"}) == 0.0
    # Distinct sections stay distinct after normalization.
    assert _score_eids({"section-II"}, {"section-3"}) == 0.0
    # Lettered schedule must not collide with Roman-100.
    assert _score_eids({"schedule-C"}, {"schedule-100"}) == 0.0


def test_arabic_only_comparison_unchanged():
    # Canonicalization is identity on an Arabic-only corpus, so a modern statute's
    # score is unaffected (no regression).
    enacted = {"section-1", "section-2", "section-3-a"}
    oracle = {"section-1", "section-2", "section-4"}
    assert _score_eids(enacted, oracle) == 2 / 3
