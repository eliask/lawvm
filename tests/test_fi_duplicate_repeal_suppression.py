"""Regression for duplicate pure-kumotaan REPEAL mint via covered_chap_secs
resolved-tuple mismatch (AGENTS.md §0 + §1.12).

Background:
    Statute 2002/1248 silently produced DUPLICATE permanent-content=None
    REPEAL tombstones for §34–38 + §27 + §32 on the per-section timeline
    when the source amendment (2014/1291) had a kumotaan clause naming
    these sections. The two REPEAL ops came from two passes:

    1. ``_rewrite_kumotaan_snapshot_replaces_to_repeal`` runs first,
       converts each snapshot_section_N REPLACE op into a REPEAL. The
       REPEAL target path RESOLVES the chapter — e.g. ``chapter:5/
       section:34`` — and enters ``covered_chap_secs`` as the tuple
       ``("5", "34")``.

    2. ``_inject_pure_kumotaan_repeal_ops`` runs next, sees
       ``chap_map_sets is None`` (the kumotaan clause has no
       ``N luku`` markers), computes ``target_chapters = [None]``, and
       checks the cover for ``(None, "34")`` — never finds ``("5",
       "34")``. Mints a DUPLICATE ``pure_repeal_34_2014/1291`` REPEAL at
       the exact same target.

The bisect observed the duplicate permanent-content=None v[1]/v[2]
tombstones; the §32 verdict subagent confirmed §32 was LEGITIMATELY
repealed (oracle absent), so §34-38 §-repeal IS source-faithful — only
the duplicate op was the bug. Suppressing the duplicate preserves
correctness while eliminating the redundant timeline entries.

This test pins:
- The duplicate op is suppressed when ``covered_chap_secs`` already
  covers the (resolved_chap, label) tuple while the new iteration has
  ``chap_key=None``.
- The witness ``PureKumotaanInjectedRepeal`` is STILL appended to
  ``injected`` so evidence plane stays complete
  (§2.10 monotone evidence). Without this the
  ``PARSE.PURE_REPEAL_CLAUSE_RECONSTRUCTED`` finding would never fire
  when the duplicate is suppressed, breaking the witness contract.
"""
from __future__ import annotations

import datetime as dt

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.kumotaan_replay import (
    FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID,
    _inject_pure_kumotaan_repeal_ops,
)
from lawvm.finland.statute import ReplayState


def _state_with_chapter_5_section_34() -> ReplayState:
    """Build a small ReplayState so ``state.find_section_path('34')`` resolves
    under chapter 5 — same chapter-5-resolved shape as 2002/1248 ch5 §34."""
    return ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    attrs={"eId": "chp_5"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="34",
                            attrs={"eId": "chp_5__sec_34"},
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


def _pre_existing_snapshot_repeal_op() -> LegalOperation:
    """The first-pass _rewrite_kumotaan_snapshot_replaces_to_repeal output:
    a REPEAL at the RESOLVED ``chapter:5/section:34`` target."""
    return LegalOperation(
        op_id="snapshot_replace_to_repeal_2014_1291_section_34",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(
            path=(("chapter", "5"), ("section", "34"))
        ),
        source=OperationSource(
            statute_id="2014/1291",
            enacted="2014-12-18",
        ),
        group_id="finland-johto:2014/1291",
        witness_rule_id=FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID,
    )


def test_duplicate_repeal_op_is_suppressed_when_resolved_chapter_covers_label() -> None:
    """When ``covered_chap_secs`` already has the resolved-chapter tuple
    (``("5", "34")``), a subsequent ``_inject_pure_kumotaan_repeal_ops``
    call with ``chap_map_sets=None`` must NOT mint a duplicate REPEAL op
    at the same target."""
    state = _state_with_chapter_5_section_34()
    lo_ops: list[LegalOperation] = [_pre_existing_snapshot_repeal_op()]

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2014/1291",
        source_title="Laki",
        amendment_issue_date=dt.date(2014, 12, 18),
        kumotaan_labels=["34"],
        chap_map_sets=None,  # no N luku markers in kumotaan clause
        amendment_effective_date=dt.date(2015, 1, 1),
        state=state,
        source_raw_text="kumotaan 34 §",
    )

    # Exactly ONE REPEAL op for chapter:5/section:34 — the pre-existing
    # snapshot-rewrite one. The duplicate is suppressed.
    section_34_repeals = [
        op for op in lo_ops
        if op.action is StructuralAction.REPEAL
        and op.target.path[-1] == ("section", "34")
    ]
    assert len(section_34_repeals) == 1, (
        f"Expected 1 REPEAL op for section:34 after duplicate suppression; "
        f"got {len(section_34_repeals)}"
    )
    assert section_34_repeals[0].op_id == "snapshot_replace_to_repeal_2014_1291_section_34"


def test_suppression_still_emits_injected_witness_record() -> None:
    """Suppressing the duplicate legal-state op MUST NOT lose the evidence
    — the witness ``PureKumotaanInjectedRepeal`` is still appended to
    ``injected`` so ``PARSE.PURE_REPEAL_CLAUSE_RECONSTRUCTED`` findings
    fire in ``_emit_pure_kumotaan_injection_findings``. (§2.10 monotone
    evidence — findings are never silently dropped even when ops are
    deduplicated.)"""
    state = _state_with_chapter_5_section_34()
    lo_ops: list[LegalOperation] = [_pre_existing_snapshot_repeal_op()]

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2014/1291",
        source_title="Laki",
        amendment_issue_date=dt.date(2014, 12, 18),
        kumotaan_labels=["34"],
        chap_map_sets=None,
        amendment_effective_date=dt.date(2015, 1, 1),
        state=state,
        source_raw_text="kumotaan 34 §",
    )

    # Off-cover injection with no snapshot-rewrite pre-existing op would
    # normally mint one record (existing pattern). With cover-check ON,
    # the duplicate op is suppressed but the witness record still fires
    # — that's the §2.10 monotone-evidence guarantee.
    assert len(result.injected) == 1, (
        f"Expected 1 PureKumotaanInjectedRepeal witness record; "
        f"got {len(result.injected)}"
    )
    assert result.injected[0].target_norm == "34"
    assert result.injected[0].rule_id == FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID
    # op_id carries the _suppressed_duplicate suffix so a downstream
    # consumer cannot mistake the witness record for an actual op.
    assert "_suppressed_duplicate" in result.injected[0].op_id


def test_no_suppression_when_chapter_resolves_correctly() -> None:
    """When ``chap_map_sets`` resolves (e.g. ``{"5": {"34"}}``), the cover
    check finds the existing ``("5", "34")`` tuple — same shape as the
    pre-existing op. The duplicate suppression is not triggered (the
    cover-check at line 406 already short-circuits).

    This pins the no-regression behaviour: when the chapter-resolved
    cover-check already works (chapter markers present in the kumotaan
    clause), the new flat-section-check does not duplicate-suppress
    anything new — the short-circuit at the old check still runs.
    """
    state = _state_with_chapter_5_section_34()
    lo_ops: list[LegalOperation] = [_pre_existing_snapshot_repeal_op()]

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2014/1291",
        source_title="Laki",
        amendment_issue_date=dt.date(2014, 12, 18),
        kumotaan_labels=["34"],
        chap_map_sets={"5": {"34"}},  # ← resolved: chapter 5 explicitly
        amendment_effective_date=dt.date(2015, 1, 1),
        state=state,
        source_raw_text="5 luvun 34 § kumotaan",
    )

    # Already covered by snapshot-rewrite path → no new op emitted, no
    # new witness record minted (flat-check is a no-op when
    # chap_map_sets is not None — the resolved cover check at line 406
    # returns continue).
    section_34_repeals = [
        op for op in lo_ops
        if op.action is StructuralAction.REPEAL
        and op.target.path[-1] == ("section", "34")
    ]
    assert len(section_34_repeals) == 1, (
        f"Expected 1 REPEAL op (no duplicate); got {len(section_34_repeals)}"
    )


def test_no_suppression_when_no_existing_cover() -> None:
    """When ``covered_chap_secs`` is empty and ``chap_map_sets=None``, the
    flat-section-check does NOT short-circuit — the pure_repeal op is
    emitted as the legitimate one-and-only REPEAL (the existing expected
    behaviour for an amendment that genuinely needs the pure-kumotaan
    injection path)."""
    state = _state_with_chapter_5_section_34()
    lo_ops: list[LegalOperation] = []  # empty — no pre-existing REPEAL

    result = _inject_pure_kumotaan_repeal_ops(
        lo_ops,
        amendment_id="2025/900",
        source_title="Laki",
        amendment_issue_date=dt.date(2025, 1, 1),
        kumotaan_labels=["34"],
        chap_map_sets=None,
        amendment_effective_date=dt.date(2025, 6, 1),
        state=state,
        source_raw_text="kumotaan 34 §",
    )

    section_34_repeals = [
        op for op in lo_ops
        if op.action is StructuralAction.REPEAL
        and op.target.path[-1] == ("section", "34")
    ]
    assert len(section_34_repeals) == 1
    assert result.injected[0].op_id == "pure_repeal_34_2025/900"
    assert "_suppressed_duplicate" not in result.injected[0].op_id
