"""Unit tests for the demo-grade reference-integrity Markdown assembler (pure).

These tests build the three typed reports from SYNTHETIC data and assert the
assembled Markdown carries the headline counts, the cause split with repeal
evidence, and the EU category — with no corpus dependency. The assembler is a
pure function of the three reports.
"""
from __future__ import annotations

from lawvm.tools.dangling_references import (
    REASON_DANGLING_ABSENT,
    STATUS_DANGLING,
    DanglingReferenceReport,
    DanglingReferenceRow,
)
from lawvm.tools.dangling_temporal_cause import (
    CAUSE_REPEALED_TARGET,
    DanglingCauseReport,
    DanglingCauseRow,
)
from lawvm.tools.eu_reference_report import EuReferenceReport
from lawvm.tools.reference_integrity_demo_report import (
    render_reference_integrity_demo_report,
)


def _dangling_report() -> DanglingReferenceReport:
    rows = (
        DanglingReferenceRow(
            source_statute_id="2000/2",
            source_provision_ref_str="2000/2/1",
            target_statute_id="1929/234",
            target_provision_ref_str="1929/234/70",
            cite_confidence="exact",
            cite_kind="cross_statute",
            existence_status=STATUS_DANGLING,
            reason=REASON_DANGLING_ABSENT,
        ),
    )
    return DanglingReferenceReport(
        total_rows=10,
        resolved_checked=5,
        excluded_non_resolved={"open": 5},
        present=3,
        dangling=1,
        existence_unknown=1,
        unknown_by_reason={"target_act_absent_from_corpus": 1},
        dangling_by_reason={REASON_DANGLING_ABSENT: 1},
        dangling_rows=rows,
    )


def _cause_report() -> DanglingCauseReport:
    return DanglingCauseReport(
        total_dangling=1,
        repealed_target=1,
        undetermined=0,
        repealed_rows=(
            DanglingCauseRow(
                source_statute_id="2000/2",
                source_provision_ref_str="2000/2/1",
                target_statute_id="1929/234",
                target_provision_ref_str="1929/234/70",
                cause=CAUSE_REPEALED_TARGET,
                repeal_spec="67–84",
                repeal_unit="§",
                amending_act="16.4.1987/411",
            ),
        ),
        undetermined_targets=(),
    )


def _eu_report() -> EuReferenceReport:
    return EuReferenceReport(
        statutes_scanned=100,
        transposition_acts=2,
        transposition_claims=2,
        transposition_bound=1,
        transposition_unbound=1,
        transposition_by_status={"resolved": 1, "statute_only": 1},
        eu_citation_acts=5,
        eu_citation_spans=12,
        eu_citation_embedded_repeal_spans=1,
        celex_spans=3,
    )


def test_demo_report_carries_all_three_surfaces() -> None:
    md = render_reference_integrity_demo_report(
        _dangling_report(),
        _cause_report(),
        _eu_report(),
        scope_label="synthetic test slice",
    )
    # headline
    assert "# Reference integrity of the Finnish statute corpus" in md
    assert "synthetic test slice" in md
    # DANGLING three-way body (from the existing renderer)
    assert "DANGLING" in md
    assert "EXISTENCE_UNKNOWN" in md
    # cause split with evidence
    assert "DANGLING_REPEALED_TARGET" in md
    assert "DANGLING_CAUSE_UNDETERMINED" in md
    assert "16.4.1987/411" in md  # the repeal evidence is cited
    assert "67–84" in md
    # EU category
    assert "EU-directive / CELEX reference category" in md
    assert "declares" in md  # the transposition honesty boundary


def test_demo_report_states_undetermined_residual_honestly() -> None:
    cause = DanglingCauseReport(
        total_dangling=3,
        repealed_target=1,
        undetermined=2,
        repealed_rows=(
            DanglingCauseRow(
                source_statute_id="a",
                source_provision_ref_str="a/1",
                target_statute_id="b",
                target_provision_ref_str="b/2",
                cause=CAUSE_REPEALED_TARGET,
                repeal_spec="2",
                repeal_unit="§",
                amending_act="2000/2",
            ),
        ),
        undetermined_targets=("c", "d"),
    )
    md = render_reference_integrity_demo_report(
        _dangling_report(), cause, _eu_report(), scope_label="x"
    )
    assert "never" in md.lower()  # never claimed never-existed
    assert "2 distinct target acts" in md
