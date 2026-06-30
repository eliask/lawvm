"""EE coverage-totality ingestion — first REAL frontend extractor feeding the
core ``assert_coverage_totality`` (Wave 3).

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.3/§3.5: the coverage-totality
assertion is the universal half; the unit extraction stays in the frontend. So
far only FI produced the core coverage carriers. This gate proves EE — which has
a genuine op-level coverage surface (``estonia/coverage_audit.audit_amendment_labels``
→ label-drop tiers) — feeds the SAME jurisdiction-neutral
``core/coverage_totality.assert_coverage_totality`` directly, via the
``coverage_units_from_mentioned`` / ``coverage_claims_from_produced`` bridge.

ADDITIVE / OBSERVE-ONLY. The bridge reads EE's already-computed extractor output
(the MENTIONED amendment-target labels as source units; the PRODUCED op labels as
claims) and produces core carriers. It does NOT touch ``apply_ee_ops`` or any
apply output — this is a new audit lane, exactly like the NO/SE receipt lanes.

WHAT THE GATES PROVE.
  (a) TOTALITY — every EE source unit is partitioned into covered / classified /
      unclassified; ``covered ∪ classified ∪ unclassified == input`` (the report
      partitions totally, nothing silently dropped).
  (b) COVERED — a mentioned label with a matching produced op reads as covered
      (no finding), through the same chapter-free ``<level>_<label>`` key the core
      ``_unit_is_covered`` uses.
  (c) DROP SURFACES — an EE label-drop (a mentioned amendment target no produced
      op covers — exactly the drop the EE audit names) reads as UNCOVERED, and
      under a STRICT classifier that refuses to place it, surfaces one
      ``COVERAGE.UNIT_UNCLASSIFIED`` core observation. EE's real coverage gap is
      now expressible through the core totality partition.
"""
from __future__ import annotations

from lawvm.core.coverage import CoverageUnit
from lawvm.core.coverage_totality import (
    COVERAGE_UNIT_UNCLASSIFIED,
    assert_coverage_totality,
    default_gap_classifier,
)
from lawvm.estonia.coverage_audit import (
    audit_amendment_labels,
    coverage_claims_from_produced,
    coverage_units_from_mentioned,
)
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import StructuralAction


def _op(action: StructuralAction, *path: tuple[str, str]) -> LegalOperation:
    return LegalOperation(
        op_id=f"ee-{action.value}-{path[-1][1]}",
        sequence=1,
        action=action,
        target=LegalAddress(path=tuple(path)),
    )


def _strict_classifier(unit: CoverageUnit):
    """A classifier that refuses to place any uncovered unit (returns ``None``).

    This is the path that surfaces ``COVERAGE.UNIT_UNCLASSIFIED`` — the default
    classifier classifies every unit, so an EE drop only becomes a finding under
    a stricter classifier. Modelling the strict EE worklist: a mentioned target
    no op covers is an unaccounted unit, not an auto-placed supplemental.
    """
    return None


def test_ee_mentioned_and_produced_label_reads_covered() -> None:
    """GATE (b): a mentioned ``paragrahvi 5`` whose produced op targets section 5
    reads as COVERED through the core totality partition — no observation, no
    actionable gap."""
    item_texts = ["Paragrahvi 5 muudetakse ja sõnastatakse järgmiselt:"]
    ops = [_op(StructuralAction.REPLACE, ("section", "5"))]

    source_units = coverage_units_from_mentioned(item_texts)
    ledger = coverage_claims_from_produced(ops)
    assert any(u.unit_id == "section_5" for u in source_units)

    observations, report = assert_coverage_totality(
        source_units, ops, source_units, ledger, classify=_strict_classifier
    )
    assert observations == (), "a covered mentioned label must not surface a finding"
    # Total partition: covered units are NOT recorded as gaps; the single source
    # unit is covered, so there are zero gaps.
    assert report.gaps == ()
    assert len(report.units) == len(source_units)


def test_ee_label_drop_surfaces_as_core_unclassified_observation() -> None:
    """GATE (c): an EE label-drop — ``lõike 3`` mentioned but no produced op
    targets subsection 3 — reads as UNCOVERED, and under the strict classifier
    surfaces exactly one ``COVERAGE.UNIT_UNCLASSIFIED`` core observation. The drop
    the EE audit names is now expressible through the core totality lane."""
    # The instruction mentions section 5 AND subsection 3, but the produced op
    # only targets section 5 — subsection 3 is the drop.
    item_texts = ["Paragrahvi 5 lõike 3 muudetakse ja sõnastatakse järgmiselt:"]
    ops = [_op(StructuralAction.REPLACE, ("section", "5"))]

    # The EE audit itself names this drop (cross-check the bridge is faithful).
    ee_cov = audit_amendment_labels(item_texts, ops, sid="drop-test")
    drop_labels = {(d.level, d.label) for d in ee_cov.drops}
    assert ("subsection", "3") in drop_labels, f"EE audit did not name the drop: {ee_cov.drops!r}"

    source_units = coverage_units_from_mentioned(item_texts)
    ledger = coverage_claims_from_produced(ops)
    assert {u.unit_id for u in source_units} == {"section_5", "subsection_3"}

    observations, report = assert_coverage_totality(
        source_units, ops, source_units, ledger, classify=_strict_classifier
    )
    # subsection_3 is the uncovered, strict-unclassified unit; section_5 covered.
    assert len(observations) == 1, f"expected one unclassified observation; got {observations!r}"
    obs = observations[0]
    assert obs.kind == COVERAGE_UNIT_UNCLASSIFIED
    assert obs.detail["unit_id"] == "subsection_3"


def test_ee_coverage_totality_partition_is_total_under_default_classifier() -> None:
    """GATE (a): under the DEFAULT classifier (which places every uncovered unit),
    the EE-extracted partition is total and emits NO unclassified observation —
    every source unit is covered or classified, nothing silently dropped. The
    report's units equal the input source units (the partition is over the full
    extracted set)."""
    item_texts = [
        "Paragrahvi 1 muudetakse ja sõnastatakse järgmiselt:",
        "Paragrahvi 7 lõiget 2 täiendatakse punktiga 4 järgmises sõnastuses:",
    ]
    ops = [
        _op(StructuralAction.REPLACE, ("section", "1")),
        _op(StructuralAction.INSERT, ("section", "7"), ("subsection", "2"), ("item", "4")),
    ]

    source_units = coverage_units_from_mentioned(item_texts)
    ledger = coverage_claims_from_produced(ops)

    observations, report = assert_coverage_totality(
        source_units, ops, source_units, ledger, classify=default_gap_classifier
    )
    assert observations == (), "default classifier never leaves a unit unclassified"
    # Total: covered units + classified gaps == input source units.
    covered_unit_ids = {
        uid for c in ledger for uid in c.covered_unit_ids
    }
    covered = [u for u in source_units if u.unit_id in covered_unit_ids]
    assert len(covered) + len(report.gaps) == len(source_units), (
        "covered + classified must equal the input source units (total partition)"
    )
