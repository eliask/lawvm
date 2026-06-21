"""Audit C — filter conservation: lossy/convention-bridged filters now return
conserving carriers (``FilterResult``/``PartitionResult``).

Each of the five Lane-C filters must:
  * keep an accepted lane byte-identical to its previous output, and
  * route every dropped item to a typed rejected/residual lane (nothing silently
    dropped), read by a production consumer.

These tests pin the conservation contract directly on the carriers and the
in-process consumers (no replay/corpus fixtures needed).
"""
from __future__ import annotations

from typing import cast

import lawvm.finland.amendment_selection as sel
from lawvm.corpus_store import CorpusStore
from lawvm.finland.body_coverage import (
    CoverageClaimPartition,
    collect_coverage_claims,
    collect_coverage_claims_partition,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.process_structural_prepare import (
    FI_CHAPTER_SEED_SKIP_RULE_ID,
    ProcessStructuralPrepareContext,
)
from lawvm.finland.vts import (
    VtsRepealPartition,
    extract_voimaantulo_repeals,
    extract_voimaantulo_repeals_partition,
)


# ---------------------------------------------------------------------------
# #2 body_coverage.collect_coverage_claims -> CoverageClaimPartition
# ---------------------------------------------------------------------------


def _section_op(op_id: str, section: str) -> AmendmentOp:
    return AmendmentOp(
        op_id=op_id, op_type="REPEAL", target_section=section, target_unit_kind="section"
    )


def test_coverage_claims_partition_conserves_rejected() -> None:
    good = _section_op("good", "1")
    no_target = AmendmentOp(
        op_id="no_target", op_type="REPEAL", target_section="", target_unit_kind="section"
    )

    partition = collect_coverage_claims_partition([good, no_target])
    assert isinstance(partition, CoverageClaimPartition)

    # Accepted lane: only the well-formed op produced a claim.
    accepted_ids = [cast(AmendmentOp, claim.target).op_id for claim in partition.accepted]
    assert accepted_ids == ["good"]

    # Rejected lane: the malformed op (no target section) is conserved with a
    # typed reason rather than silently skipped.
    reasons = {rejected.reason for rejected in partition.rejected_claims}
    assert reasons == {"missing_target_section"}
    # Core-contract residual mirror, non-blocking out_of_scope.
    assert len(partition.residuals) == 1
    assert all(r.kind == "out_of_scope" and not r.blocking for r in partition.residuals)


def test_coverage_claims_shim_drains_into_out_param() -> None:
    good = _section_op("good", "1")
    no_target = AmendmentOp(
        op_id="no_target", op_type="REPEAL", target_section="", target_unit_kind="section"
    )
    rejected_out: list = []
    claims = collect_coverage_claims([good, no_target], rejected_claims_out=rejected_out)

    # Accepted set is byte-identical to the legacy return.
    assert [cast(AmendmentOp, c.target).op_id for c in claims] == ["good"]
    # The legacy out-param sink (read by uncovered_recovery_prepare) gets the same
    # rich CoverageRejectedClaim records — forwarded from the partition, not
    # re-derived (single emission path).
    assert [r.reason for r in rejected_out] == ["missing_target_section"]


# ---------------------------------------------------------------------------
# #1 vts.extract_voimaantulo_repeals -> VtsRepealPartition
# ---------------------------------------------------------------------------


_VTS_NO_TRIGGER = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<act><preface><docTitle>Laki esimerkista</docTitle></preface>"
    b"<body><section><num>1 \xc2\xa7</num><content><p>ei laukaisinta</p></content>"
    b"</section></body></act></akomaNtoso>"
)


def test_vts_partition_no_trigger_returns_clean_partition() -> None:
    partition = extract_voimaantulo_repeals_partition(_VTS_NO_TRIGGER, "2000/1")
    assert isinstance(partition, VtsRepealPartition)
    assert partition.accepted == ()
    # Conserving fields exist even when empty (total-accounting identity).
    assert partition.skipped_targets == ()


def test_vts_shim_drains_partition_into_out_params() -> None:
    skipped: list = []
    diagnostics: list = []
    ops = extract_voimaantulo_repeals(
        _VTS_NO_TRIGGER,
        "2000/1",
        skipped_targets_out=skipped,
        source_diagnostics_out=diagnostics,
    )
    # Accepted set byte-identical to legacy list return.
    partition = extract_voimaantulo_repeals_partition(_VTS_NO_TRIGGER, "2000/1")
    assert ops == list(partition.accepted)
    # The shim forwards the same typed records the partition holds (single path).
    assert skipped == list(partition.skipped_targets)
    assert diagnostics == list(partition.source_diagnostics)


def test_vts_source_model_adapter_is_partition_consumer() -> None:
    import lxml.etree as etree

    from lawvm.finland.source_model import AmendmentSourceModel

    tree = etree.fromstring(_VTS_NO_TRIGGER)
    model = AmendmentSourceModel.from_tree(tree, source_bytes=_VTS_NO_TRIGGER)
    skipped: list = []
    ops = model.extract_vts_cross_statute_repeals(
        parent_id="2000/1",
        parent_title="Laki esimerkista",
        strict_profile=None,
        skipped_targets_out=skipped,
    )
    # Adapter returns the accepted ops (empty here, no trigger) and drains the
    # rejected lane into the out-param sink.
    assert ops == []
    assert skipped == []

    # Falsy parent_id short-circuits to None, mirroring the free-function gate.
    assert (
        model.extract_vts_cross_statute_repeals(
            parent_id="", parent_title="", strict_profile=None
        )
        is None
    )


# ---------------------------------------------------------------------------
# #3 amendment_selection._filter_candidates -> PartitionResult
# ---------------------------------------------------------------------------


def test_filter_candidates_partition_conserves_out_of_scope(monkeypatch) -> None:
    import datetime as dt

    monkeypatch.setattr(
        sel,
        "get_consolidated_oracle_reflected_source_vts_children",
        lambda *_a, **_k: (),
    )
    in_scope = sel.AmendmentSelectionCandidate(
        amendment_id="2001/1",
        effective_date=dt.date(2001, 1, 1),
        issue_date=None,
        title="a",
    )
    out_of_scope = sel.AmendmentSelectionCandidate(
        amendment_id="2005/1",
        effective_date=dt.date(2005, 1, 1),
        issue_date=None,
        title="b",
    )

    partition, _cutoff, _basis = sel._filter_candidates(
        parent_id="2000/1",
        mode="legal_pit",
        candidates=(in_scope, out_of_scope),
        cutoff_date=dt.date(2002, 1, 1),
        oracle_version_amendment_id=None,
        corpus=cast(CorpusStore, object()),
        selector=None,
    )

    # Accepted lane = the in-scope candidate only (acceptance unchanged).
    assert [c.amendment_id for c in partition.accepted] == ["2001/1"]
    # Rejected lane conserves the out-of-scope candidate with a typed reason.
    assert [r.item.amendment_id for r in partition.rejected] == ["2005/1"]
    assert all(
        r.reason_code == sel.AMENDMENT_OUT_OF_SCOPE_REASON_CODE
        for r in partition.rejected
    )


def test_select_applicable_surfaces_out_of_scope(monkeypatch) -> None:
    import datetime as dt

    parent_id = "2000/9"
    in_id = "2001/5"
    out_id = "2009/5"

    def _xml(effective: str) -> bytes:
        return (
            '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            "<act><meta><lifecycle>"
            f'<eventRef date="{effective}" />'
            f'<dateEntryIntoForce date="{effective}" />'
            "</lifecycle></meta>"
            "<preface><docTitle>Laki</docTitle></preface>"
            "<body><section><num>1 §</num></section></body></act></akomaNtoso>"
        ).encode("utf-8")

    class _C:
        def read_source(self, amendment_id: str) -> bytes | None:
            if amendment_id == in_id:
                return _xml("2001-01-01")
            return _xml("2009-01-01")

    monkeypatch.setattr(
        sel, "amendment_children_by_parent", lambda: {parent_id: [in_id, out_id]}
    )
    monkeypatch.setattr(
        sel, "get_consolidated_meta", lambda *_a, **_k: (dt.date(2002, 1, 1), None)
    )
    monkeypatch.setattr(
        sel,
        "get_consolidated_oracle_reflected_source_vts_children",
        lambda *_a, **_k: (),
    )

    selection = sel.select_applicable_amendments(
        parent_id, "legal_pit", corpus=cast(CorpusStore, _C())
    )
    # Selected (accepted) records exclude the out-of-scope amendment.
    assert [str(rec["statute_id"]) for rec in selection.records] == [in_id]
    # ...and it is surfaced on the result's out_of_scope lane, not dropped.
    assert [r.item.amendment_id for r in selection.out_of_scope] == [out_id]


# ---------------------------------------------------------------------------
# #5 process_structural_prepare._drop_seeded_chapter_ops -> PartitionResult
# ---------------------------------------------------------------------------


def test_seed_skip_partition_conserves_dropped_ops() -> None:
    from lawvm.finland.chapter_seed_targets import ChapterSeedSkip

    chapter_op = AmendmentOp(
        op_id="chap2",
        op_type="REPLACE",
        target_section="2",
        target_unit_kind="chapter",
    )
    other_op = _section_op("sec1", "1")
    elaboration: list = []
    ctx = ProcessStructuralPrepareContext(
        amendment_id="2003/1",
        target_statute="2000/1",
        ops=[chapter_op, other_op],
        chapter_seed_skip={
            ChapterSeedSkip(amendment_id="2003/1", chapter_label="2")
        },
        restructure_plans=[],
        elaboration_observations=elaboration,
        replay_print=lambda _msg: None,
    )

    partition = ctx._drop_seeded_chapter_ops(ctx.ops)
    # Accepted = kept ops; rejected = the seeded-chapter op (typed reason code).
    assert [op.op_id for op in partition.accepted] == ["sec1"]
    assert [r.item.op_id for r in partition.rejected] == ["chap2"]
    assert all(
        r.reason_code == FI_CHAPTER_SEED_SKIP_RULE_ID for r in partition.rejected
    )

    # prepare() is the production consumer: it reads the rejected lane and
    # surfaces it onto the elaboration-observation ledger.
    kept = ctx.prepare()
    assert [op.op_id for op in kept] == ["sec1"]
    assert len(elaboration) == 1
    obs = elaboration[0]
    assert obs["kind"] == "ELAB.CHAPTER_SEED_SKIP"
    assert obs["dropped_count"] == 1
    assert obs["seeded_chapters"] == ["2"]
    assert len(obs["dropped_op_records"]) == 1


def test_seed_skip_no_match_returns_all_accepted() -> None:
    op = _section_op("sec1", "1")
    elaboration: list = []
    ctx = ProcessStructuralPrepareContext(
        amendment_id="2003/2",
        target_statute="2000/1",
        ops=[op],
        chapter_seed_skip=None,
        restructure_plans=[],
        elaboration_observations=elaboration,
        replay_print=lambda _msg: None,
    )
    partition = ctx._drop_seeded_chapter_ops(ctx.ops)
    assert [o.op_id for o in partition.accepted] == ["sec1"]
    assert partition.rejected == ()
    # No observation emitted when nothing is dropped.
    ctx.prepare()
    assert elaboration == []
