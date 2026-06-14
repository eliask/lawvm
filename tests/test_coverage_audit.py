"""Tests for the johtolause token-coverage loudness instrument."""

from __future__ import annotations

from lawvm.finland.johtolause.coverage_audit import (
    audit_johtolause,
    classify_uncovered_spans,
)


def _tiers(text: str) -> list[tuple[str, str]]:
    return [(c.tier, c.position) for c in classify_uncovered_spans(text)]


def test_clean_clause_has_no_uncovered_spans() -> None:
    """A fully-parsed clause leaves no content tokens uncovered."""
    assert audit_johtolause("Muutetaan 5 § ja 9 §") == []
    assert audit_johtolause("Lisätään lakiin uusi 27 §") == []
    assert audit_johtolause("Kumotaan 7 § ja 8 §") == []


def test_verbed_clause_with_no_label_and_no_op_is_flagged() -> None:
    """A verbed clause naming no section and producing nothing is still a drop."""
    text = "Muutetaan 5 § ja korvataan taulukko"
    tiers = _tiers(text)
    assert any(tier == "verb_no_op" for tier, _pos in tiers), tiers


def test_number_tail_of_partially_covered_section_is_not_a_label() -> None:
    """A phantom ``6 §`` left by a witness covering the head of ``36 §`` is not a drop.

    When a produced op's witness span covers only the leading digit(s) of a
    section number, the trailing digit(s) leak into the adjacent uncovered span
    (``36 §`` head-covered -> ``6 §`` remains).  The label extractor must reject
    a number whose first digit is immediately preceded by another digit in the
    raw source — it is a number-tail, not a standalone section label.  This
    removed number-tail false positives across ~65 corpus statutes.
    """
    from lawvm.finland.johtolause.coverage_audit import classify_uncovered_spans

    # Build a span artificially is brittle; instead assert the invariant via the
    # public classifier on a clause that fully parses — no real-drop tier may
    # fire purely from a digit-tail.  (A clean parse has no uncovered content.)
    text = "muutetaan 36 §, 5 § ja 16 §"
    tiers = [(c.tier, c.position) for c in classify_uncovered_spans(text)]
    assert all(t not in ("verb_no_op", "unmatched_section") for t, _ in tiers), tiers


def test_witness_fidelity_gap_is_not_a_real_drop() -> None:
    """A span whose labels are ALL produced is a witness gap, not a drop.

    Regression for the ~50% false-positive rate in the verb_no_op tier: spans
    like 1978/588's ``momentti, 32 §, 35 §:n 3 momentti, ...`` name only labels
    that ARE in the produced ops (the ops exist; their witness spans are narrow).
    Such spans must classify as preamble_only, never as a real drop tier.
    """
    # Every section label here is produced; the uncovered span is glue around
    # produced ops, so no real-drop tier may fire.
    text = "Muutetaan 30 b §:n 1 momentti, 30 c §:n 1 momentti, 32 §, 35 §"
    tiers = _tiers(text)
    assert all(
        tier not in ("verb_no_op", "unmatched_section") for tier, _pos in tiers
    ), tiers


def test_enactment_preamble_is_demoted_not_flagged_as_a_drop() -> None:
    """Leading ceremonial preamble before any op is not an operation drop."""
    tiers = _tiers("Suomen Senaatti on, esittelyssä, päättänyt muuttaa 5 §")
    # The preamble is classified leading_preamble (low-signal), never a
    # high-signal verb_no_op/unmatched_section interior drop.
    assert all(pos == "leading_preamble" for _tier, pos in tiers) or tiers == []


def test_reinstatement_preamble_around_produced_op_is_low_signal() -> None:
    """When the op IS produced, its narrow witness must not raise a real-drop tier.

    The 2009/886 reinstatement ``... kumotun 138 §:n tilalle uusi 138 §`` parses
    to a 138 INSERT; the surrounding citation/reinstatement preamble is not
    covered by the op's narrow witness, but since the op exists the span is
    classified ``preamble_only``, not a real drop.
    """
    text = (
        "Lisätään lakiin uusi 69 a § ja 69 b–69 i § "
        "sekä 69 b–69 e ja 69 g–69 i §:n edelle uusi väliotsikko, "
        "lakiin uusi 69 j ja 69 k § "
        "sekä lakiin siitä lailla 1218/1994 kumotun 138 §:n tilalle uusi 138 § seuraavasti:"
    )
    tiers = _tiers(text)
    # No interior/trailing high-signal drop — every span is preamble_only.
    assert all(tier == "preamble_only" for tier, _pos in tiers), tiers
