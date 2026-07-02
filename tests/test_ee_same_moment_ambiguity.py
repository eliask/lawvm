"""§1.7 same-moment cross-act incompatible-payload ambiguity detection for EE.

Two affecting acts effecting the same target on the same effective date with
incompatible whole-target payloads are today resolved silently by
``op.sequence`` in :func:`apply_ee_ops`. The pre-pass wired into
:func:`apply_ee_ops` (mirroring the UK precedent
``uk_same_moment_cross_act_incompatible_payload_ambiguous``) emits a BLOCKING
finding so the silent pick is visible and strict-rejectable. Apply order is
unchanged — the finding is additive.

Guard-liveness (AGENTS.md §2.9): drives synthesized conflicting ops through the
FULL ``apply_ee_ops`` path (the production lane), not just the detector unit.
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.grafter import apply_ee_ops
from lawvm.estonia.ordering import EE_SAME_MOMENT_AMBIGUITY_RULE_ID
from lawvm.replay_adjudication import CompileAdjudication


def _statute_with_section(label: str = "5", text: str = "Original text") -> IRStatute:
    return IRStatute(
        statute_id="ee/test",
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


def _same_moment_findings(adjudications: list[CompileAdjudication]) -> list[CompileAdjudication]:
    return [a for a in adjudications if a.kind == EE_SAME_MOMENT_AMBIGUITY_RULE_ID]


def test_two_distinct_acts_replace_same_target_same_effective_date_emits_ambiguity_finding() -> None:
    """Two REPLACE ops on §5 from distinct acts at the same effective date."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
        _replace_section_op(
            op_id="ee-replace-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
            replacement_text="Act B lydelse.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    moments = _same_moment_findings(adjudications)
    assert len(moments) == 1, f"expected 1 same-moment finding; got {moments!r}"
    finding = moments[0]
    assert finding.blocking is True
    # Cross-act finding carries an empty op_id (no single op owns the conflict)
    # so it does NOT pollute the per-op conserved-wrapper partition.
    assert finding.op_id == ""
    assert finding.source_statute == ""
    detail = finding.detail
    assert detail["rule_id"] == EE_SAME_MOMENT_AMBIGUITY_RULE_ID
    assert detail["phase"] == "apply"
    assert detail["family"] == "temporal_recovery"
    assert detail["blocking"] is True
    assert "section" in detail["affected_target"]
    assert "'5'" in detail["affected_target"]
    assert detail["effective_date"] == "2026-01-01"
    assert detail["reason_code"] == "same_moment_cross_act_incompatible_payload"
    assert detail["resolution"] == "sequence_order_unproven"
    assert set(detail["conflicting_affecting_acts"]) == {"ee/act-a/2025", "ee/act-b/2025"}
    conflicting_op_ids = {op["op_id"] for op in detail["conflicting_ops"]}
    assert conflicting_op_ids == {"ee-replace-A", "ee-replace-B"}
    # Each conflicting op carries its action, sequence, and source provenance.
    by_id = {op["op_id"]: op for op in detail["conflicting_ops"]}
    assert by_id["ee-replace-A"]["action"] == "replace"
    assert by_id["ee-replace-A"]["affecting_act_id"] == "ee/act-a/2025"
    assert by_id["ee-replace-A"]["sequence"] == 1
    assert by_id["ee-replace-B"]["action"] == "replace"
    assert by_id["ee-replace-B"]["affecting_act_id"] == "ee/act-b/2025"
    assert by_id["ee-replace-B"]["sequence"] == 2


def test_repeal_versus_replace_same_moment_is_incompatible() -> None:
    """A REPEAL of §5 against a REPLACE of §5 at the same effective date is
    incompatible (you cannot both delete a provision and amend it)."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A replacement text.",
        ),
        _repeal_section_op(
            op_id="ee-repeal-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    moments = _same_moment_findings(adjudications)
    assert len(moments) == 1
    detail = moments[0].detail
    assert set(detail["conflicting_affecting_acts"]) == {"ee/act-a/2025", "ee/act-b/2025"}


def test_two_repeals_same_target_are_not_incompatible() -> None:
    """Two REPEALs of §5 from distinct acts are redundant destructive effects
    with the same outcome — NOT order-determining. The detector excludes them
    to avoid manufacturing false ambiguity from coexistence."""
    statute = _statute_with_section("5")
    ops = [
        _repeal_section_op(
            op_id="ee-repeal-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
        ),
        _repeal_section_op(
            op_id="ee-repeal-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2026-01-01",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_different_effective_dates_no_ambiguity_finding() -> None:
    """Two REPLACE ops on §5 from distinct acts but DIFFERENT effective dates
    are not a same-EFFECTIVE-DATE collision."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
        _replace_section_op(
            op_id="ee-replace-B",
            sequence=2,
            section_label="5",
            source_id="ee/act-b/2025",
            effective="2027-01-01",
            replacement_text="Act B lydelse.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_single_op_no_ambiguity_finding() -> None:
    """Negative (§2.9): a single op on §5 — no cross-act conflict, no finding."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="ee-replace-A",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A lydelse.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_same_act_two_ops_no_cross_act_finding() -> None:
    """Two ops from the SAME act on §5 at the same effective date are not a
    cross-act §1.7 conflict — within-source ordering/scope is its own lane."""
    statute = _statute_with_section("5")
    ops = [
        _replace_section_op(
            op_id="ee-replace-A1",
            sequence=1,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A first replacement.",
        ),
        _replace_section_op(
            op_id="ee-replace-A2",
            sequence=2,
            section_label="5",
            source_id="ee/act-a/2025",
            effective="2026-01-01",
            replacement_text="Act A second replacement.",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_text_replace_does_not_trigger_incompatible_payload_finding() -> None:
    """Two TEXT_REPLACE ops on §5 at the same date are fragment-level and not
    flagged as incompatible — mirrors the UK detector's exclusion of
    word/fragment-level effects (they can legitimately coexist at the same
    instant)."""
    statute = _statute_with_section("5", text="Original text fragment.")
    ops = [
        LegalOperation(
            op_id="ee-textreplace-A",
            sequence=1,
            action=StructuralAction.TEXT_PATCH,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="replaced fragment A",
                attrs={"old_text": "Original"},
            ),
            source=OperationSource(statute_id="ee/act-a/2025", effective="2026-01-01"),
        ),
        LegalOperation(
            op_id="ee-textreplace-B",
            sequence=2,
            action=StructuralAction.TEXT_PATCH,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="replaced fragment B",
                attrs={"old_text": "Original"},
            ),
            source=OperationSource(statute_id="ee/act-b/2025", effective="2026-01-01"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []


def test_undated_ops_do_not_trigger_ambiguity_finding() -> None:
    """Two REPLACE ops with no effective date provenance are not a
    same-EFFECTIVE-DATE collision — bucketing undated ops together would
    manufacture false ambiguity from the absence of a date."""
    statute = _statute_with_section("5")
    ops = [
        LegalOperation(
            op_id="ee-replace-A",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="Act A lydelse."),
            source=OperationSource(statute_id="ee/act-a/2025", effective=""),
        ),
        LegalOperation(
            op_id="ee-replace-B",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="Act B lydelse."),
            source=OperationSource(statute_id="ee/act-b/2025", effective=""),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert _same_moment_findings(adjudications) == []
