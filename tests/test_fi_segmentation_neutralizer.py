"""Tests for the segmentation-displacement neutralizer (amb-style).

When position-based alignment fails, the SAME provision text can surface as
BOTH a ``unit_missing_right`` (a unit present in LawVM but "absent" in the
oracle) AND a ``unit_missing_left`` (present in the oracle but "absent" in
LawVM) within the same section — one unit displaced to a different tree
position, with zero information loss. The neutralizer forgives such a section
ONLY when exact-normalized pairs explain its ENTIRE diff; genuinely missing,
extra, or near-miss text always stays penalized. These tests pin that contract
at the predicate level and verify the bench reconciliation invariant holds on
the resulting unit result.
"""
from __future__ import annotations

from lawvm.core.bench_contract import (
    BenchStatus,
    BenchUnitResult,
    check_residue_reconciliation,
)
from lawvm.finland.oracle_comparison import (
    is_segmentation_displacement_neutralized,
    segmentation_displacement_pairs,
)


def _left_only(text: str, *, unit_kind: str = "item", label: str = "") -> dict:
    """A ``unit_missing_right`` event — a unit present in LawVM, absent in oracle."""
    return {
        "kind": "unit_missing_right",
        "unit_kind": unit_kind,
        "unit_label": label,
        "left_text": text,
    }


def _right_only(text: str, *, unit_kind: str = "item", label: str = "") -> dict:
    """A ``unit_missing_left`` event — a unit present in oracle, absent in LawVM."""
    return {
        "kind": "unit_missing_left",
        "unit_kind": unit_kind,
        "unit_label": label,
        "right_text": text,
    }


# ---------------------------------------------------------------------------
# (a) Displaced unit with an exact-normalized twin -> neutralized + witness.
# ---------------------------------------------------------------------------

def test_exact_displacement_pair_is_neutralized() -> None:
    events = [
        _left_only("rikkoo 37 §:ssä säädetyn anniskelukiellon"),
        _right_only("rikkoo 37 §:ssä säädetyn anniskelukiellon"),
    ]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is True
    pairs = segmentation_displacement_pairs(events)
    assert len(pairs) == 1
    assert pairs[0]["left_text"] == "rikkoo 37 §:ssä säädetyn anniskelukiellon"
    assert pairs[0]["right_text"] == "rikkoo 37 §:ssä säädetyn anniskelukiellon"


def test_pair_matches_across_presentation_normalization() -> None:
    """Exact equality is taken AFTER _normalize_wording_for_diff, so § spacing
    and dash-variant presentation differences between the two tree positions
    still count as the same displaced unit (never a similarity ratio)."""
    events = [
        _left_only("rikkoo 37 §:ssä säädetyn anniskelukiellon"),
        _right_only("rikkoo 37 § :ssä säädetyn anniskelukiellon"),
    ]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is True


def test_multiset_reorder_of_list_items_is_neutralized() -> None:
    """A whole list reordered between tree positions: every left-only item has an
    exact right-only twin and vice versa (the 2009/205 §53 / 2007/366 §39 case)."""
    items = ["säätösalaojitus", "säätökastelu", "kuivatusvesien kierrätys;"]
    events = [_left_only(t) for t in items] + [_right_only(t) for t in reversed(items)]
    sd = {"label": 0, "structural": 3, "text": 0, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is True
    assert len(segmentation_displacement_pairs(events)) == 3


# ---------------------------------------------------------------------------
# (b) Genuinely missing unit (no twin) -> STILL penalized.
# ---------------------------------------------------------------------------

def test_unmatched_missing_unit_stays_penalized() -> None:
    """A left-only unit with NO twin on the other side is genuine extra/missing
    content (C2) — it must keep the section penalized."""
    events = [
        _left_only("Tämä momentti on vain LawVM:ssä eikä oraakkelissa lainkaan."),
    ]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is False
    assert segmentation_displacement_pairs(events) == []


def test_pair_plus_unmatched_unit_stays_penalized() -> None:
    """A section that has one valid displacement pair AND a separate genuinely
    missing unit is NOT neutralized — only collapse provable double-counts."""
    events = [
        _left_only("yhteinen teksti molemmilla puolilla"),
        _right_only("yhteinen teksti molemmilla puolilla"),
        _right_only("vain oraakkelissa oleva kohta, jota LawVM ei tuota"),
    ]
    sd = {"label": 0, "structural": 2, "text": 0, "events": events}
    # A pair exists, but it does not explain the ENTIRE diff -> stay penalized.
    assert len(segmentation_displacement_pairs(events)) == 1
    assert is_segmentation_displacement_neutralized(sd, events) is False


def test_pair_plus_wording_change_stays_penalized() -> None:
    """Displacement pair + a real wording change in the same section -> the
    section carries genuine divergence and must stay penalized."""
    events = [
        _left_only("siirretty kohta"),
        _right_only("siirretty kohta"),
        {
            "kind": "wording_text_changed",
            "unit_kind": "subsection",
            "left_text": "vanha sanamuoto",
            "right_text": "kokonaan eri sanamuoto",
        },
    ]
    sd = {"label": 0, "structural": 1, "text": 1, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is False


def test_label_difference_blocks_neutralization() -> None:
    events = [
        _left_only("siirretty kohta"),
        _right_only("siirretty kohta"),
    ]
    sd = {"label": 1, "structural": 1, "text": 0, "events": events}
    assert is_segmentation_displacement_neutralized(sd, events) is False


def test_facet_events_are_not_paired() -> None:
    """Facet add/remove events (intro/wrapUp) are a different projection family
    and must never be paired as displaced units, even with matching text."""
    events = [
        {"kind": "facet_added", "unit_kind": "intro", "facet_kind": "intro", "right_text": "X"},
        {"kind": "facet_removed", "unit_kind": "intro", "facet_kind": "intro", "left_text": "X"},
    ]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert segmentation_displacement_pairs(events) == []
    assert is_segmentation_displacement_neutralized(sd, events) is False


# ---------------------------------------------------------------------------
# (c) Near-but-not-equal text -> STILL penalized.
# ---------------------------------------------------------------------------

def test_near_miss_text_stays_penalized() -> None:
    """A left-only and right-only unit whose text is similar but NOT exactly
    equal after normalization is a real divergence — never neutralized."""
    events = [
        _left_only("rikkoo 37 §:ssä säädetyn anniskelukiellon"),
        _right_only("rikkoo 38 §:ssä säädetyn anniskelukiellon"),  # 37 vs 38
    ]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert segmentation_displacement_pairs(events) == []
    assert is_segmentation_displacement_neutralized(sd, events) is False


def test_empty_text_is_never_a_pair() -> None:
    events = [_left_only(""), _right_only("")]
    sd = {"label": 0, "structural": 1, "text": 0, "events": events}
    assert segmentation_displacement_pairs(events) == []
    assert is_segmentation_displacement_neutralized(sd, events) is False


# ---------------------------------------------------------------------------
# (d) Reconciliation invariant on the resulting bench unit result.
# ---------------------------------------------------------------------------

def test_reconciliation_holds_for_fully_neutralized_section() -> None:
    """A statute whose only diverging section is a pure displacement scores zero
    structural error with empty residue, and the witness rides the witnesses
    channel (never residue_buckets). This mirrors how amb-neutralized sections
    reconcile."""
    result = BenchUnitResult(
        unit_id="2007/366",
        bench_unit_status=BenchStatus.SCORED,
        structural_err=0.0,
        text_err=0.0,
        residue_buckets={},
        witnesses=(
            "segmentation_displacement_match section=chapter:6/section:39 "
            "text=@deadbeef0000/'säätösalaojitus'",
        ),
    )
    check_residue_reconciliation(result)  # must not raise


def test_reconciliation_holds_when_section_stays_penalized() -> None:
    """When a section keeps a genuine divergence, structural_err stays positive
    and the residue stays non-empty — the displacement pair never strips the
    explaining residue out from under a still-penalized section."""
    result = BenchUnitResult(
        unit_id="1992/994",
        bench_unit_status=BenchStatus.SCORED,
        structural_err=0.5,
        text_err=0.1,
        residue_buckets={"unit_missing_right": 1},
    )
    check_residue_reconciliation(result)  # must not raise
