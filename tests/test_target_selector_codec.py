"""Golden round-trip tests for the FI legacy ↔ TargetSelector codec (Wave 0).

Proves invariants:
- TARGET-03: ``to_legacy(from_legacy(rec)) == rec`` byte-identical for every
  representative real ``AmendmentOp`` target shape (losslessness), and
  ``from_legacy(rec).major_kind == rec.target_unit_kind``.
- TARGET-04: ``UNSPECIFIED`` and ``EXPLICIT_ROOT`` scopes produce DIFFERENT
  selectors.
- ``to_legal_address_if_complete()`` is ``None`` for UNSPECIFIED scope and a
  correct ``LegalAddress`` for a resolved scope.

The fixtures mirror real ``target_*`` field combinations observed in
``tests/test_fi_apply.py`` (section; section+chapter scope; section+chapter+part;
section+momentti; section+momentti+kohta; section+momentti+kohta+alakohta;
section+facet otsikko/johd; chapter; chapter+part; part).
"""

from __future__ import annotations

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.core.target_selector import (
    AddressSegment,
    ScopeStatus,
    TargetScope,
    TargetSelector,
)
from lawvm.finland.target_selector_codec import (
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)


def _rec(
    *,
    target_unit_kind: str = "section",
    target_section: str = "1",
    target_chapter: str | None = None,
    target_part: str | None = None,
    target_paragraph: int | None = None,
    target_item: str | None = None,
    target_subitem: str | None = None,
    target_special: str | None = None,
) -> AmendmentOpV1Record:
    return AmendmentOpV1Record(
        target_unit_kind=target_unit_kind,
        target_section=target_section,
        target_chapter=target_chapter,
        target_part=target_part,
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_subitem=target_subitem,
        target_special=target_special,
    )


# Representative REAL shapes drawn from tests/test_fi_apply.py:
# - plain section (line ~224 target_section="2")
# - section + chapter scope (line ~529 section="73" chapter="7")
# - section + chapter + part scope (line ~652 section="8" chapter="1" part="2")
# - section + momentti (line ~808 target_paragraph=3)
# - section + momentti + kohta (line ~2109 paragraph=1 item="h")
# - section + momentti + kohta (numeric, line ~3657 paragraph=1 item="29")
# - section + facet otsikko (line ~2962 target_special="otsikko")
# - section + momentti + facet johd
# - chapter target (target_unit_kind="chapter")
# - chapter + part scope
# - part target (target_unit_kind="part")
REPRESENTATIVE_RECORDS: list[AmendmentOpV1Record] = [
    _rec(target_section="2"),
    _rec(target_section="73", target_chapter="7"),
    _rec(target_section="8", target_chapter="1", target_part="2"),
    _rec(target_section="1", target_paragraph=3),
    _rec(target_section="32", target_paragraph=1, target_item="h"),
    _rec(target_section="1", target_paragraph=1, target_item="29"),
    # alakohta as its own segment (post-#52 separate-level encoding)
    _rec(target_section="5", target_paragraph=2, target_item="3", target_subitem="a"),
    _rec(target_section="2", target_special="otsikko"),
    _rec(target_section="2", target_special="otsikko_edella"),
    _rec(target_section="1", target_paragraph=1, target_special="johd"),
    _rec(target_unit_kind="chapter", target_section="7"),
    _rec(target_unit_kind="chapter", target_section="5", target_part="2"),
    _rec(target_unit_kind="part", target_section="5"),
    # part op carrying the redundant ``target_part`` column mirroring
    # ``target_section`` — the W2 corpus FINDING (real shape from 1929/234:
    # part III/V/I with target_part == target_section). Must round-trip exactly.
    _rec(target_unit_kind="part", target_section="III", target_part="III"),
]


@pytest.mark.parametrize(
    "rec", REPRESENTATIVE_RECORDS, ids=lambda r: f"{r.target_unit_kind}:{r.target_section}"
)
def test_round_trip_byte_identical(rec: AmendmentOpV1Record) -> None:
    """TARGET-03: legacy → selector → legacy is byte-identical."""
    selector = TargetSelectorCodecV1.from_legacy(rec)
    assert TargetSelectorCodecV1.to_legacy(selector) == rec


@pytest.mark.parametrize(
    "rec", REPRESENTATIVE_RECORDS, ids=lambda r: f"{r.target_unit_kind}:{r.target_section}"
)
def test_major_kind_matches_unit_kind(rec: AmendmentOpV1Record) -> None:
    """TARGET-03: selector.major_kind equals the legacy target_unit_kind."""
    selector = TargetSelectorCodecV1.from_legacy(rec)
    assert selector.major_kind == rec.target_unit_kind


def test_otsikko_vs_otsikko_edella_distinct_round_trip() -> None:
    """otsikko and otsikko_edella collapse to one FacetKind but still round-trip.

    This is the W2 FINDING made safe at W0: FacetKind.HEADING cannot distinguish
    the two source tokens, so ``special_raw`` carries the exact token. The two
    records produce selectors with equal ``special`` but distinct ``special_raw``.
    """
    rec_otsikko = _rec(target_section="2", target_special="otsikko")
    rec_edella = _rec(target_section="2", target_special="otsikko_edella")

    sel_otsikko = TargetSelectorCodecV1.from_legacy(rec_otsikko)
    sel_edella = TargetSelectorCodecV1.from_legacy(rec_edella)

    assert sel_otsikko.special == FacetKind.HEADING
    assert sel_edella.special == FacetKind.HEADING
    assert sel_otsikko.special_raw == "otsikko"
    assert sel_edella.special_raw == "otsikko_edella"
    assert sel_otsikko != sel_edella

    assert TargetSelectorCodecV1.to_legacy(sel_otsikko) == rec_otsikko
    assert TargetSelectorCodecV1.to_legacy(sel_edella) == rec_edella


def test_johd_maps_to_intro() -> None:
    rec = _rec(target_section="1", target_paragraph=1, target_special="johd")
    selector = TargetSelectorCodecV1.from_legacy(rec)
    assert selector.special == FacetKind.INTRO
    assert selector.special_raw == "johd"


def test_unspecified_and_explicit_root_are_distinct() -> None:
    """TARGET-04: UNSPECIFIED scope != EXPLICIT_ROOT scope.

    Both leave chapter/part columns empty in the legacy encoding, but the
    selector types must keep them distinct (the legacy encoding is the lossy
    one — the W2 ledger entry).
    """
    relative = (AddressSegment("section", "5"),)
    unspecified = TargetSelector(
        relative_path=relative,
        scope=TargetScope(status=ScopeStatus.UNSPECIFIED),
    )
    explicit_root = TargetSelector(
        relative_path=relative,
        scope=TargetScope(status=ScopeStatus.EXPLICIT_ROOT),
    )
    assert unspecified != explicit_root
    assert unspecified.scope.status != explicit_root.scope.status


def test_from_legacy_section_scope_is_unspecified_not_root() -> None:
    """The legacy decode of a bare section maps absent scope → UNSPECIFIED."""
    selector = TargetSelectorCodecV1.from_legacy(_rec(target_section="2"))
    assert selector.scope.status == ScopeStatus.UNSPECIFIED


def test_to_legal_address_none_for_unspecified() -> None:
    selector = TargetSelectorCodecV1.from_legacy(_rec(target_section="2"))
    assert selector.to_legal_address_if_complete() is None


def test_to_legal_address_for_explicit_root() -> None:
    selector = TargetSelector(
        relative_path=(AddressSegment("section", "5"),),
        scope=TargetScope(status=ScopeStatus.EXPLICIT_ROOT),
    )
    address = selector.to_legal_address_if_complete()
    assert address == LegalAddress(path=(("section", "5"),))


def test_to_legal_address_for_explicit_scope() -> None:
    """Resolved scope prepends the scope path before the focus."""
    selector = TargetSelectorCodecV1.from_legacy(
        _rec(target_section="8", target_chapter="1", target_part="2")
    )
    address = selector.to_legal_address_if_complete()
    assert address == LegalAddress(
        path=(("part", "2"), ("chapter", "1"), ("section", "8"))
    )


def test_to_legal_address_carries_facet() -> None:
    selector = TargetSelector(
        relative_path=(AddressSegment("section", "5"),),
        scope=TargetScope(status=ScopeStatus.EXPLICIT_ROOT),
        special=FacetKind.HEADING,
    )
    address = selector.to_legal_address_if_complete()
    assert address is not None
    assert address.special == FacetKind.HEADING


def test_chapter_with_part_scope_to_legal_address() -> None:
    selector = TargetSelectorCodecV1.from_legacy(
        _rec(target_unit_kind="chapter", target_section="5", target_part="2")
    )
    assert selector.scope.status == ScopeStatus.EXPLICIT_SCOPE
    address = selector.to_legal_address_if_complete()
    assert address == LegalAddress(path=(("part", "2"), ("chapter", "5")))


def test_descendant_segments_round_trip_in_order() -> None:
    rec = _rec(
        target_section="5", target_paragraph=2, target_item="3", target_subitem="a"
    )
    selector = TargetSelectorCodecV1.from_legacy(rec)
    kinds = [segment.kind for segment in selector.relative_path]
    assert kinds == ["section", "subsection", "item", "subitem"]
    assert TargetSelectorCodecV1.to_legacy(selector) == rec


def test_part_with_redundant_target_part_round_trips() -> None:
    """W2 corpus FINDING: a part op's ``target_part`` mirrors ``target_section``.

    Real shape from 1929/234 (part III/V/I, ``target_part == target_section``).
    The codec carries the redundant column as an EXPLICIT_SCOPE part segment so
    the legacy round-trip is byte-identical, while the resolved address collapses
    the duplicate to a single ``part:<x>``.
    """
    rec = _rec(target_unit_kind="part", target_section="III", target_part="III")
    selector = TargetSelectorCodecV1.from_legacy(rec)
    assert selector.major_kind == "part"
    assert TargetSelectorCodecV1.to_legacy(selector) == rec
    # The redundant scope must not duplicate in the resolved address.
    address = selector.to_legal_address_if_complete()
    assert address == LegalAddress(path=(("part", "III"),))


def test_part_without_target_part_stays_unspecified_scope() -> None:
    """A part op with no ``target_part`` column keeps an UNSPECIFIED scope."""
    selector = TargetSelectorCodecV1.from_legacy(
        _rec(target_unit_kind="part", target_section="5")
    )
    assert selector.scope.status == ScopeStatus.UNSPECIFIED
    assert selector.to_legal_address_if_complete() is None
    assert TargetSelectorCodecV1.to_legacy(selector) == _rec(
        target_unit_kind="part", target_section="5"
    )


def test_scope_invariants_enforced() -> None:
    with pytest.raises(ValueError):
        TargetScope(status=ScopeStatus.EXPLICIT_SCOPE)  # empty path
    with pytest.raises(ValueError):
        TargetScope(
            status=ScopeStatus.EXPLICIT_ROOT,
            path=(AddressSegment("part", "1"),),
        )
    with pytest.raises(ValueError):
        TargetScope(status=ScopeStatus.INFERRED_SCOPE)  # no rule_id
    with pytest.raises(ValueError):
        TargetScope(
            status=ScopeStatus.UNSPECIFIED,
            path=(AddressSegment("part", "1"),),
        )


def test_selector_requires_non_empty_relative_path() -> None:
    with pytest.raises(ValueError):
        TargetSelector(
            relative_path=(),
            scope=TargetScope(status=ScopeStatus.UNSPECIFIED),
        )
