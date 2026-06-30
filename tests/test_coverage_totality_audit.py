"""Tests for ``core.coverage_totality`` (``COVERAGE.UNIT_UNCLASSIFIED``).

Stream D — coverage totality. Every source unit the frontend extracted must be
owned-or-classified: claimed by an op (covered), classified as a typed gap by the
injected disposition, or recorded as a rejected claim. The audit surfaces a unit
that is NEITHER covered nor classified (a silent drop).

Synthetic regression covers:

* a fully-covered unit set → no finding;
* an unclassified source unit (classifier returns ``None``, no claim) → exactly
  one ``COVERAGE.UNIT_UNCLASSIFIED`` carrying the audited fields;
* a classified gap for each disposition → no finding (owned);
* a rejected claim → carried through verbatim into the report;
* an untouched target asserted-untouched → no finding;
* deterministic ordering over multiple unclassified units;
* empty input → empty output;
* the partition is total: ``covered ∪ classified ∪ unclassified == input``.

Audit-plane-only contract: the function emits observations, never raises on
shape-valid input, never mutates carriers, never fabricates a claim or
disposition. ``Observation.kind`` is the registered FindingSpec code (registry
anti-drift checks in ``tests/test_finding_registry.py`` cover the wire binding).
"""

from __future__ import annotations

from typing import Optional

from lawvm.core.coverage import (
    CoverageClaim,
    CoverageDisposition,
    CoverageRejectedClaim,
    CoverageUnit,
)
from lawvm.core.coverage_totality import (
    COVERAGE_UNIT_UNCLASSIFIED,
    assert_coverage_totality,
    default_gap_classifier,
    target_touch_partition,
)
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import StructuralAction

_ADDR = LegalAddress(path=(("section", "1"),))


def _unit(unit_id: str, *, kind: str = "section", label: str = "1", tags=frozenset()) -> CoverageUnit:
    return CoverageUnit(
        unit_id=unit_id,
        kind=kind,
        observed_label=label,
        parent_label=None,
        payload_ref=None,
        tags=tags,
    )


def _claim(*unit_ids: str) -> CoverageClaim:
    return CoverageClaim(
        claim_kind="explicit",
        target=_ADDR,
        covered_unit_ids=frozenset(unit_ids),
        evidence=(),
    )


def _op(op_id: str = "o1") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_ADDR,
    )


def _reject_none(_unit: CoverageUnit) -> Optional[CoverageDisposition]:
    """A strict classifier that refuses to place any unit (always unclassified)."""
    return None


# --- covered -----------------------------------------------------------------


def test_fully_covered_unit_set_emits_no_finding() -> None:
    unit = _unit("section_6", kind="section", label="6")
    claim = _claim("section_6")
    observations, report = assert_coverage_totality(
        [unit], [_op()], [], [claim], source_statute="fi/1"
    )
    assert observations == ()
    assert report.gaps == ()
    assert report.units == (unit,)
    assert report.claims == (claim,)


def test_chapter_free_claim_covers_unit_by_label_alone() -> None:
    # A 2-part claim id (kind_label, no chapter context) covers any chapter's
    # section of that label — mirrors FI's label-only rule.
    unit = _unit("section_2_17", kind="section", label="17")
    claim = _claim("section_17")  # chapter-free
    observations, _ = assert_coverage_totality([unit], [], [], [claim])
    assert observations == ()


# --- unclassified ------------------------------------------------------------


def test_unclassified_source_unit_emits_one_finding_with_audited_fields() -> None:
    unit = _unit("section_9", kind="section", label="9", tags=frozenset({"weird"}))
    observations, report = assert_coverage_totality(
        [unit], [], [], [], classify=_reject_none, source_statute="fi/2"
    )
    assert len(observations) == 1
    finding = observations[0]
    assert finding.kind == COVERAGE_UNIT_UNCLASSIFIED
    assert finding.source_statute == "fi/2"
    assert finding.detail["unit_id"] == "section_9"
    assert finding.detail["kind"] == "section"
    assert finding.detail["observed_label"] == "9"
    assert finding.detail["parent_label"] is None
    assert finding.detail["tags"] == ("weird",)
    assert finding.detail["reason"] == "source_unit_neither_covered_nor_classified"
    # Still recorded in the report so the partition stays total.
    assert len(report.gaps) == 1
    assert report.gaps[0].disposition == "ambiguous_uncovered"
    assert report.gaps[0].unit is unit


# --- classified gap (each disposition) → owned, no finding -------------------


def test_classified_gap_each_disposition_is_owned_no_finding() -> None:
    dispositions: tuple[CoverageDisposition, ...] = (
        "supplemental_candidate",
        "ignore_nonoperative",
        "covered_by_broad_scope",
        "ambiguous_uncovered",
        "container_overbundle_pathology",
        "duplicate_standalone_and_bundled",
    )
    for disp in dispositions:
        unit = _unit("section_3", label="3")
        observations, report = assert_coverage_totality(
            [unit], [], [], [], classify=lambda _u, d=disp: d
        )
        assert observations == (), f"{disp} should be owned (no finding)"
        assert len(report.gaps) == 1
        assert report.gaps[0].disposition == disp


def test_default_classifier_models_fi_tag_logic() -> None:
    # container-only chapter → covered_by_broad_scope
    chapter = _unit("chapter_2", kind="chapter", label="2", tags=frozenset({"container"}))
    assert default_gap_classifier(chapter) == "covered_by_broad_scope"
    # nonoperative / provenance tag → ignore_nonoperative
    nonop = _unit("section_x", tags=frozenset({"nonoperative"}))
    assert default_gap_classifier(nonop) == "ignore_nonoperative"
    prov = _unit("section_y", tags=frozenset({"provenance"}))
    assert default_gap_classifier(prov) == "ignore_nonoperative"
    # plain operative uncovered → supplemental_candidate
    plain = _unit("section_z")
    assert default_gap_classifier(plain) == "supplemental_candidate"


def test_default_classifier_never_yields_unclassified() -> None:
    # Under the default classifier every uncovered unit is owned (no finding).
    unit = _unit("section_5", label="5")
    observations, _ = assert_coverage_totality([unit], [], [], [])
    assert observations == ()


# --- rejected claims ---------------------------------------------------------


def test_rejected_claim_carried_through_verbatim() -> None:
    rejected = CoverageRejectedClaim(
        reason="missing_target_section",
        target=_op(),
        evidence=("op_id=o1",),
    )
    unit = _unit("section_6", label="6")
    claim = _claim("section_6")
    _, report = assert_coverage_totality(
        [unit], [_op()], [], [claim], rejected_claims=[rejected]
    )
    assert report.rejected_claims == (rejected,)


# --- target symmetry ---------------------------------------------------------


def test_untouched_target_asserted_untouched_no_finding() -> None:
    touched_unit = _unit("section_6", label="6")
    untouched_unit = _unit("section_99", label="99")
    claim = _claim("section_6")
    observations, _ = assert_coverage_totality(
        [touched_unit],
        [],
        [touched_unit, untouched_unit],
        [claim],
    )
    # The untouched target produces NO finding (a base unit no op addresses is a
    # legitimate no-op).
    assert observations == ()
    touched, untouched = target_touch_partition([touched_unit, untouched_unit], [claim])
    assert touched == (touched_unit,)
    assert untouched == (untouched_unit,)


# --- determinism + empty -----------------------------------------------------


def test_deterministic_ordering_over_multiple_unclassified() -> None:
    units = [
        _unit("section_a", label="a"),
        _unit("section_b", label="b"),
        _unit("section_c", label="c"),
    ]
    # 'b' is covered; 'a' and 'c' are unclassified (strict classifier).
    claim = _claim("section_b")
    observations, _ = assert_coverage_totality(
        units, [], [], [claim], classify=_reject_none, source_statute="fi/1"
    )
    assert [o.detail["unit_id"] for o in observations] == ["section_a", "section_c"]
    again, _ = assert_coverage_totality(
        units, [], [], [claim], classify=_reject_none, source_statute="fi/1"
    )
    assert [o.detail for o in observations] == [o.detail for o in again]


def test_empty_input_yields_empty_output() -> None:
    observations, report = assert_coverage_totality([], [], [], [])
    assert observations == ()
    assert report.units == ()
    assert report.claims == ()
    assert report.gaps == ()


# --- partition totality ------------------------------------------------------


def test_partition_is_total_covered_union_classified_union_unclassified() -> None:
    covered = _unit("section_1", label="1")
    classified = _unit("section_2", label="2", tags=frozenset({"nonoperative"}))
    unclassified = _unit("section_3", label="3")
    claim = _claim("section_1")

    def classify(u: CoverageUnit) -> Optional[CoverageDisposition]:
        if "nonoperative" in u.tags:
            return "ignore_nonoperative"
        return None  # section_3 stays unclassified

    units = [covered, classified, unclassified]
    observations, report = assert_coverage_totality(
        units, [], [], [claim], classify=classify
    )
    # Exactly one unclassified finding (section_3).
    assert [o.detail["unit_id"] for o in observations] == ["section_3"]
    # Partition: covered (1) + every gap (classified + unclassified) == input.
    covered_count = len(report.units) - len(report.gaps)
    assert covered_count == 1
    assert covered_count + len(report.gaps) == len(units)
    # The gap set is exactly {classified, unclassified}, in input order.
    assert [g.unit.unit_id for g in report.gaps] == ["section_2", "section_3"]
    assert report.gaps[0].disposition == "ignore_nonoperative"
    assert report.gaps[1].disposition == "ambiguous_uncovered"
