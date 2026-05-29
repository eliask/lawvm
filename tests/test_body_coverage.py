"""Tests for lawvm.finland.body_coverage.

Uses synthetic XML — no corpus data required.

AKN namespace: http://docs.oasis-open.org/legaldocml/ns/akn/3.0
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
from lxml import etree

from lawvm.core.coverage import CoverageClaim, CoverageGap, CoverageReport, CoverageUnit
from lawvm.finland.body_coverage import (
    extract_body_coverage,
    collect_coverage_claims,
    analyze_coverage,
)
from lawvm.finland.ops import AmendmentOp, OpType, TargetKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(fragment: str) -> etree._Element:
    """Parse an AKN XML fragment with the default namespace pre-set."""
    if 'xmlns' not in fragment:
        fragment = fragment.replace('<', '<', 1)
        # Wrap in root with namespace if not already present
    return etree.fromstring(fragment)


def _body(inner: str) -> etree._Element:
    """Wrap *inner* in a minimal amendment root with an AKN <body>."""
    return etree.fromstring(
        f'<act xmlns="{_NS}">'
        f'  <body>{inner}</body>'
        f'</act>'
    )


def _op(
    op_id: str,
    op_type: Any = "REPLACE",
    target_section: str = "",
    target_kind: Any = TargetKind.SECTION,
    target_chapter: str | None = None,
    fallback_provenance: bool = False,
    body_root_replace_fallback: bool = False,
) -> AmendmentOp:
    return AmendmentOp(
        op_id=op_id,
        op_type=cast(OpType, op_type),
        target_section=target_section,
        target_kind=cast(TargetKind, target_kind),
        target_chapter=target_chapter,
        fallback_provenance=fallback_provenance,
        body_root_replace_fallback=body_root_replace_fallback,
    )


@dataclass
class _CoverageOpShim:
    op_id: str
    op_type: str
    target_section: str
    target_unit_kind: str
    target_kind: Any
    target_chapter: str | None = None
    fallback_provenance: bool = False
    body_root_replace_fallback: bool = False


# ---------------------------------------------------------------------------
# Test 1: Simple amendment body with 2 sections → 2 CoverageUnits
# ---------------------------------------------------------------------------

def test_extract_two_sections() -> None:
    tree = _body(
        """
        <section>
          <num>6 §</num>
          <subsection><content><p>Text of section 6.</p></content></subsection>
        </section>
        <section>
          <num>7 §</num>
          <subsection><content><p>Text of section 7.</p></content></subsection>
        </section>
        """
    )
    units = extract_body_coverage(tree)
    assert len(units) == 2
    labels = {u.observed_label for u in units}
    assert labels == {"6", "7"}
    kinds = {u.kind for u in units}
    assert kinds == {"section"}
    # No nonoperative tags on plain sections
    for u in units:
        assert "nonoperative" not in u.tags


# ---------------------------------------------------------------------------
# Test 2: One section claimed, one not → 1 CoverageGap
# ---------------------------------------------------------------------------

def test_analyze_coverage_one_gap() -> None:
    tree = _body(
        """
        <section>
          <num>3 §</num>
          <subsection><content><p>A.</p></content></subsection>
        </section>
        <section>
          <num>4 §</num>
          <subsection><content><p>B.</p></content></subsection>
        </section>
        """
    )
    units = extract_body_coverage(tree)
    # Only op for section 3
    ops = [_op("op_0", target_section="3")]
    claims = collect_coverage_claims(ops)
    report = analyze_coverage(units, claims)

    assert len(report.units) == 2
    assert len(report.claims) == 1
    assert len(report.gaps) == 1

    gap = report.gaps[0]
    assert gap.unit.observed_label == "4"
    assert gap.disposition == "supplemental_candidate"
    assert report.uncovered_count == 1
    assert len(report.supplemental_candidates) == 1


# ---------------------------------------------------------------------------
# Test 3: Voimaantulo section → tagged as nonoperative, gap = ignore
# ---------------------------------------------------------------------------

def test_voimaantulo_section_ignore() -> None:
    tree = _body(
        """
        <section>
          <num>5 §</num>
          <subsection><content><p>Operative text.</p></content></subsection>
        </section>
        <section>
          <num>6 §</num>
          <heading>Voimaantulo</heading>
          <subsection><content><p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.</p></content></subsection>
        </section>
        """
    )
    units = extract_body_coverage(tree)
    assert len(units) == 2

    voimaantulo_units = [u for u in units if "nonoperative" in u.tags]
    assert len(voimaantulo_units) == 1
    assert voimaantulo_units[0].observed_label == "6"

    # No ops at all — section 5 is a gap, section 6 is nonoperative
    report = analyze_coverage(units, [])

    gap_labels = {g.unit.observed_label: g.disposition for g in report.gaps}
    assert gap_labels["5"] == "supplemental_candidate"
    assert gap_labels["6"] == "ignore_nonoperative"

    # uncovered_count excludes nonoperative
    assert report.uncovered_count == 1


def test_uncovered_count_excludes_broad_scope_coverage() -> None:
    unit = CoverageUnit(
        unit_id="section_9",
        kind="section",
        observed_label="9",
        parent_label=None,
        payload_ref=None,
        tags=frozenset(),
    )
    report = CoverageReport(
        units=(unit,),
        claims=(
            CoverageClaim(
                claim_kind="broad",
                target=None,
                covered_unit_ids=frozenset({"section_1"}),
                evidence=(),
            ),
        ),
        gaps=(
            CoverageGap(
                unit=unit,
                disposition="covered_by_broad_scope",
                suggested_target=None,
                evidence=("broad_scope",),
            ),
        ),
    )

    assert report.uncovered_count == 0


def test_coverage_report_partitions_actionable_and_obligation_gaps() -> None:
    unit = CoverageUnit(
        unit_id="section_9",
        kind="section",
        observed_label="9",
        parent_label=None,
        payload_ref=None,
        tags=frozenset(),
    )
    report = CoverageReport(
        units=(unit,),
        claims=(),
        gaps=(
            CoverageGap(
                unit=unit,
                disposition="supplemental_candidate",
                suggested_target=None,
                evidence=("supplemental",),
            ),
            CoverageGap(
                unit=unit,
                disposition="ambiguous_uncovered",
                suggested_target=None,
                evidence=("ambiguous",),
            ),
            CoverageGap(
                unit=unit,
                disposition="container_overbundle_pathology",
                suggested_target=None,
                evidence=("bundle",),
            ),
            CoverageGap(
                unit=unit,
                disposition="duplicate_standalone_and_bundled",
                suggested_target=None,
                evidence=("duplicate",),
            ),
        ),
    )

    assert report.uncovered_count == 4
    assert [gap.disposition for gap in report.supplemental_candidates] == [
        "supplemental_candidate"
    ]
    assert [gap.disposition for gap in report.obligations] == [
        "ambiguous_uncovered",
        "container_overbundle_pathology",
        "duplicate_standalone_and_bundled",
    ]


def test_coverage_carriers_normalize_collections_and_preserve_evidence() -> None:
    tags = ["nonoperative"]
    evidence = ["manual"]
    covered_unit_ids = ["section_1"]

    unit = CoverageUnit(
        unit_id="section_1",
        kind="section",
        observed_label="1",
        parent_label=None,
        payload_ref=None,
        tags=cast(Any, tags),
    )
    claim = CoverageClaim(
        claim_kind="explicit",
        target=None,
        covered_unit_ids=cast(Any, covered_unit_ids),
        evidence=cast(Any, evidence),
    )
    gap = CoverageGap(
        unit=unit,
        disposition="ignore_nonoperative",
        suggested_target=None,
        evidence=cast(Any, evidence),
    )
    report = CoverageReport(
        units=cast(Any, [unit]),
        claims=cast(Any, [claim]),
        gaps=cast(Any, [gap]),
    )
    tags.append("later")
    evidence.append("later")
    covered_unit_ids.append("section_2")

    assert unit.tags == frozenset({"nonoperative"})
    assert claim.covered_unit_ids == frozenset({"section_1"})
    assert claim.evidence == ("manual",)
    assert gap.evidence == ("manual",)
    assert report.units == (unit,)
    assert report.claims == (claim,)
    assert report.gaps == (gap,)


def test_coverage_carriers_reject_unknown_claim_kind_and_gap_disposition() -> None:
    unit = CoverageUnit(
        unit_id="section_1",
        kind="section",
        observed_label="1",
        parent_label=None,
        payload_ref=None,
    )

    with pytest.raises(ValueError, match="unsupported CoverageClaim.claim_kind"):
        CoverageClaim(
            claim_kind=cast(Any, "implicit"),
            target=None,
            covered_unit_ids=frozenset({"section_1"}),
        )
    with pytest.raises(ValueError, match="unsupported CoverageGap.disposition"):
        CoverageGap(
            unit=unit,
            disposition=cast(Any, "vanished"),
            suggested_target=None,
        )


# ---------------------------------------------------------------------------
# Test 4: Chapter + nested section, chapter op covers chapter but not section
# ---------------------------------------------------------------------------

def test_chapter_and_nested_section() -> None:
    tree = _body(
        """
        <chapter>
          <num>2 luku</num>
          <heading>Menettelysäännökset</heading>
          <section>
            <num>8 §</num>
            <subsection><content><p>Foo.</p></content></subsection>
          </section>
        </chapter>
        """
    )
    units = extract_body_coverage(tree)

    # Should have chapter AND section units
    kinds = {u.kind for u in units}
    assert "chapter" in kinds
    assert "section" in kinds

    chapter_unit = next(u for u in units if u.kind == "chapter")
    section_unit = next(u for u in units if u.kind == "section")
    assert chapter_unit.observed_label == "2"
    assert section_unit.observed_label == "8"
    # Section should have parent_label pointing to chapter 2
    assert section_unit.parent_label == "2"

    # Claim only the chapter — section 8 should be covered by fallback label-only
    # (since chapter op targets the chapter, not its members)
    ops = [_op("op_ch", target_section="2", target_kind=TargetKind.CHAPTER)]
    claims = collect_coverage_claims(ops)
    report = analyze_coverage(units, claims)

    # Chapter is claimed; section inside is not
    chapter_gap = [g for g in report.gaps if g.unit.kind == "chapter"]
    section_gap = [g for g in report.gaps if g.unit.kind == "section"]
    assert len(chapter_gap) == 0
    assert len(section_gap) == 1
    assert section_gap[0].disposition == "supplemental_candidate"


def test_extract_body_coverage_reanchors_sections_after_malformed_chapter_marker() -> None:
    tree = _body(
        """
        <chapter>
          <num>16 a luku</num>
          <section>
            <num>1 §</num>
            <subsection><content><p>chapter 16a sec1</p></content></subsection>
          </section>
          <section>
            <num>16 b luku</num>
            <heading>Jakautuminen</heading>
          </section>
          <section>
            <num>1 §</num>
            <subsection><content><p>chapter 16b sec1</p></content></subsection>
          </section>
          <section>
            <num>2 §</num>
            <subsection><content><p>chapter 16b sec2</p></content></subsection>
          </section>
        </chapter>
        """
    )
    units = extract_body_coverage(tree)

    chapters = [u for u in units if u.kind == "chapter"]
    sections = [u for u in units if u.kind == "section"]

    assert [u.observed_label for u in chapters] == ["16a", "16b"]
    assert [(u.observed_label, u.parent_label) for u in sections] == [
        ("1", "16a"),
        ("1", "16b"),
        ("2", "16b"),
    ]


# ---------------------------------------------------------------------------
# Test 5: Fallback-hinted op → claim_kind = 'fallback'
# ---------------------------------------------------------------------------

def test_collect_claims_typed_fallback_provenance() -> None:
    ops = [
        _op("op_explicit", target_section="1"),
        _op("op_fallback", target_section="2", body_root_replace_fallback=True),
    ]
    claims = collect_coverage_claims(ops)
    assert len(claims) == 2

    by_target_section = {
        next(
            ev.split("=")[1] for ev in c.evidence if ev.startswith("op_id=")
        ): c
        for c in claims
    }
    assert by_target_section["op_explicit"].claim_kind == "explicit"
    assert by_target_section["op_fallback"].claim_kind == "fallback"


def test_collect_claims_prefers_target_unit_kind_over_target_kind() -> None:
    ops = [
        _CoverageOpShim(
            op_id="op_chapter",
            op_type="REPLACE",
            target_section="2",
            target_unit_kind="chapter",
            target_kind=TargetKind.SECTION,
        ),
        _CoverageOpShim(
            op_id="op_part",
            op_type="REPLACE",
            target_section="III",
            target_unit_kind="part",
            target_kind=TargetKind.SECTION,
        ),
    ]

    claims = collect_coverage_claims(cast(list[AmendmentOp], ops))

    assert {next(ev.split("=")[1] for ev in c.evidence if ev.startswith("op_id=")): c for c in claims}[
        "op_chapter"
    ].covered_unit_ids == frozenset({"chapter_2"})
    assert {next(ev.split("=")[1] for ev in c.evidence if ev.startswith("op_id=")): c for c in claims}[
        "op_part"
    ].covered_unit_ids == frozenset({"part_3"})


def test_extract_body_coverage_records_ignored_units_for_missing_or_unusable_num() -> None:
    tree = _body(
        """
        <chapter>
          <heading>Missing num chapter</heading>
        </chapter>
        <section>
          <heading>Missing num section</heading>
        </section>
        <section>
          <num>§</num>
        </section>
        """
    )

    ignored_units = []
    units = extract_body_coverage(tree, ignored_units_out=ignored_units)

    assert units == []
    assert [(issue.unit_kind, issue.reason) for issue in ignored_units] == [
        ("chapter", "missing_num"),
        ("section", "missing_num"),
        ("section", "unusable_num"),
    ]


def test_collect_coverage_claims_records_rejected_claims() -> None:
    rejected_claims = []
    claims = collect_coverage_claims(
        cast(
            list[AmendmentOp],
            [
                _CoverageOpShim(
                    op_id="no_target",
                    op_type="REPLACE",
                    target_section="",
                    target_unit_kind="section",
                    target_kind=TargetKind.SECTION,
                ),
                _CoverageOpShim(
                    op_id="unsupported_target_kind",
                    op_type="REPLACE",
                    target_section="3",
                    target_unit_kind="item",
                    target_kind=TargetKind.SECTION,
                ),
            ],
        ),
        rejected_claims_out=rejected_claims,
    )

    assert claims == []
    assert [issue.reason for issue in rejected_claims] == [
        "missing_target_section",
        "unsupported_target_unit_kind",
    ]
