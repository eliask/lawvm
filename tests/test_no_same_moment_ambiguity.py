"""§1.7 same-moment cross-act incompatible-payload ambiguity detection for NO.

Two affecting acts effecting the same target on the same effective date with
incompatible whole-target payloads were, before B1, resolved silently by
group/sequence order in :func:`apply_no_ops` with ZERO same-moment coverage.
The pre-pass wired into :func:`apply_no_ops` (mirroring the EE/UK precedent and
reusing the shared core detector verbatim) emits a BLOCKING finding so the
silent pick is visible and strict-rejectable. Apply order is unchanged — the
finding is additive.

Guard-liveness (AGENTS.md §2.9): drives synthesized conflicting ops through the
FULL ``apply_no_ops`` production lane, not just the detector unit.
"""
from __future__ import annotations

from lawvm.core.cross_act_same_moment import (
    SameMomentPrecedenceClaim,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    BASIS_LATER_ENACTMENT,
)
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.norway.grafter import apply_no_ops
from lawvm.replay_adjudication import CompileAdjudication

NO_SAME_MOMENT_AMBIGUITY_RULE_ID = (
    "no_same_moment_cross_act_incompatible_payload_ambiguous"
)


def _statute_with_section(label: str = "5", text: str = "Original text") -> IRStatute:
    return IRStatute(
        statute_id="no/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label=label, text=text),),
        ),
    )


def _replace_section_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
    replacement_text: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=section_label,
            text=replacement_text,
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _repeal_section_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section_label),)),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _same_moment_findings(
    adjudications: list[CompileAdjudication],
) -> list[CompileAdjudication]:
    return [a for a in adjudications if a.kind == NO_SAME_MOMENT_AMBIGUITY_RULE_ID]


def test_two_distinct_acts_replace_same_target_same_effective_date_emits_ambiguity() -> None:
    """Two REPLACE ops on §5 from distinct acts at the same effective date."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A ny ordlyd.",
        ),
        _replace_section_op(
            op_id="no-replace-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2026-01-01",
            replacement_text="Act B ny ordlyd.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    moments = _same_moment_findings(adjudications)
    assert len(moments) == 1, f"expected 1 same-moment finding; got {moments!r}"
    finding = moments[0]
    assert finding.blocking is True
    assert finding.op_id == ""
    assert finding.source_statute == ""
    detail = finding.detail
    assert detail["rule_id"] == NO_SAME_MOMENT_AMBIGUITY_RULE_ID
    assert detail["phase"] == "apply"
    assert detail["family"] == "temporal_recovery"
    assert detail["blocking"] is True
    assert "section" in detail["affected_target"]
    assert "'5'" in detail["affected_target"]
    assert detail["effective_date"] == "2026-01-01"
    assert detail["reason_code"] == "same_moment_cross_act_incompatible_payload"
    assert detail["resolution"] == "sequence_order_unproven"
    assert set(detail["conflicting_affecting_acts"]) == {
        "no/act-a/2025",
        "no/act-b/2025",
    }
    conflicting_op_ids = {op["op_id"] for op in detail["conflicting_ops"]}
    assert conflicting_op_ids == {"no-replace-A", "no-replace-B"}


def test_repeal_versus_replace_same_moment_is_incompatible() -> None:
    """A REPEAL of §5 against a REPLACE of §5 at the same effective date is
    incompatible (you cannot both delete a provision and amend it)."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A erstatning.",
        ),
        _repeal_section_op(
            op_id="no-repeal-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2026-01-01",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    moments = _same_moment_findings(adjudications)
    assert len(moments) == 1
    detail = moments[0].detail
    assert set(detail["conflicting_affecting_acts"]) == {
        "no/act-a/2025",
        "no/act-b/2025",
    }


def test_two_repeals_same_target_are_not_incompatible() -> None:
    """Two REPEALs of §5 from distinct acts are redundant destructive effects —
    NOT order-determining. The detector excludes them to avoid manufacturing
    false ambiguity from coexistence."""
    statute = _statute_with_section("5")
    ops = [
        _repeal_section_op(
            op_id="no-repeal-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
        ),
        _repeal_section_op(
            op_id="no-repeal-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2026-01-01",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_different_effective_dates_no_ambiguity_finding() -> None:
    """Two REPLACE ops on §5 but DIFFERENT effective dates are not a
    same-EFFECTIVE-DATE collision."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A ny ordlyd.",
        ),
        _replace_section_op(
            op_id="no-replace-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2027-01-01",
            replacement_text="Act B ny ordlyd.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_single_op_no_ambiguity_finding() -> None:
    """Negative (§2.9): a single op on §5 — no cross-act conflict, no finding."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A ny ordlyd.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_same_act_two_ops_no_cross_act_finding() -> None:
    """Two ops from the SAME act on §5 at the same effective date are not a
    cross-act §1.7 conflict — within-source ordering is its own lane."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="no-replace-A1",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A first.",
        ),
        _replace_section_op(
            op_id="no-replace-A2",
            sequence=2,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A second.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_validated_precedence_claim_resolves_ambiguity() -> None:
    """A validated same-moment precedence claim binding the conflict turns the
    finding non-blocking with ``resolution: resolved_by_claim`` — the shared
    rule resolves it (no silent last-wins, no jurisdiction-specific rule).

    The claim is exercised through the shared detector directly (the production
    pre-pass call site passes no claims yet); this pins that NO can consume the
    shared resolution surface verbatim.
    """
    from lawvm.core.cross_act_same_moment import (
        detect_cross_act_same_moment_conflicts,
    )

    ops = [
        _replace_section_op(
            op_id="no-replace-A",
            sequence=1,
            section_label="5",
            source_id="no/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A ny ordlyd.",
        ),
        _replace_section_op(
            op_id="no-replace-B",
            sequence=2,
            section_label="5",
            source_id="no/act-b/2025",
            effective="2026-01-01",
            replacement_text="Act B ny ordlyd.",
        ),
    ]
    target = str((("section", "5"),))
    claim = SameMomentPrecedenceClaim(
        claim_id="no-claim-1",
        claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date="2026-01-01",
        affected_target=target,
        conflicting_affecting_acts=("no/act-a/2025", "no/act-b/2025"),
        winner_affecting_act_id="no/act-b/2025",
        basis=BASIS_LATER_ENACTMENT,
    )
    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="no",
        precedence_claims=(claim,),
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["blocking"] is False
    assert finding["resolution"] == "resolved_by_claim"
    assert finding["resolved_by_claim_winner_affecting_act_id"] == "no/act-b/2025"
