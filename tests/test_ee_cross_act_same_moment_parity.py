"""§2.5 retirement parity gate for the EE same-moment cross-act detector.

Issue: The standalone EE detector ``detect_ee_same_moment_cross_act_conflicts``
(formerly at ``estonia/ordering.py:168``) has been retired in favour of the
shared detector at :mod:`lawvm.core.cross_act_same_moment`
(``detect_cross_act_same_moment_conflicts``), called from
:func:`lawvm.estonia.grafter.apply_ee_ops` with ``finder_kind_prefix="ee"`` and
EE's own compatibility predicate ``ee_same_moment_payloads_incompatible``.

Parity contract (AGENTS.md §2.5: parity criteria / deletion plan): **byte-identical
findings** on the EE-specific edge cases. These snapshots were captured from the
pre-retirement standalone EE detector with revision ``714ebbb1`` and are
re-asserted here against the shared-module-backed production call. The
``rule_id`` (``ee_same_moment_cross_act_incompatible_payload_ambiguous``)
must equal :data:`EE_SAME_MOMENT_AMBIGUITY_RULE_ID`; the detail tuples and
``strict_disposition``/``quirks_disposition`` envelope must be byte-equal.

The shared module also accepts an optional ``precedence_claims`` parameter for
validated ``SameMomentPrecedenceClaim`` resolution; EE ships none today, so all
findings remain ``resolution: "sequence_order_unproven"`` and ``blocking=True``.
This parity test pins EE's no-claims shape; the precedence-claim surface has its
own coverage in :mod:`tests.test_core_cross_act_same_moment`.
"""
from __future__ import annotations

from typing import Any

from lawvm.core.cross_act_same_moment import detect_cross_act_same_moment_conflicts
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.ordering import EE_SAME_MOMENT_AMBIGUITY_RULE_ID, ee_same_moment_payloads_incompatible
from lawvm.replay_adjudication import CompileAdjudication


def _replace_op(
    op_id: str,
    seq: int,
    section: str,
    src_id: str,
    effective: str,
    text: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=seq,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=section, text=text),
        source=OperationSource(statute_id=src_id, effective=effective),
    )


def _repeal_op(
    op_id: str,
    seq: int,
    section: str,
    src_id: str,
    effective: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=seq,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section),)),
        source=OperationSource(statute_id=src_id, effective=effective),
    )


def _text_replace_op(
    op_id: str,
    seq: int,
    section: str,
    src_id: str,
    effective: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=seq,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", section),)),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="x", attrs={"old_text": "y"}),
        source=OperationSource(statute_id=src_id, effective=effective),
    )


def _detect_ee(ops: list[LegalOperation]) -> list[dict[str, Any]]:
    """Invoke the production-lane shared-module-backed EE detector call."""
    return detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        incompatible_payload_predicate=ee_same_moment_payloads_incompatible,
    )


# Pre-retirement snapshot of the EE standalone detector's finding for the
# REPLACE+REPLACE / REPLACE+REPEAL / REPEAL+TEXT_REPLACE parity cases. Captured
# from revision ``714ebbb1`` of ``estonia/ordering.py:detect_ee_same_moment_cross_act_conflicts``;
# reproduced byte-identically by the shared module when called via
# ``_detect_ee`` above. When the standalone detector is INTENTIONALLY retired
# (§2.5 retirement-plan), these snapshots become the regression guard: the
# shared module must keep producing this exact shape so downstream consumers
# (adjudication lane, conserved-wrapper partition) see no drift. Do not loosen
# these assertions without recording a documented migration in
# ``notes/CROSS_ACT_SAME_MOMENT_MIGRATION_PLAN.md``.

_REPLACE_REPLACE_EXPECTED: dict[str, Any] = {
    "rule_id": "ee_same_moment_cross_act_incompatible_payload_ambiguous",
    "phase": "apply",
    "blocking": True,
    "strict_disposition": "block",
    "quirks_disposition": "record",
    "family": "temporal_recovery",
    "reason": (
        "Two or more affecting acts change the same target at the same "
        "effective date with incompatible whole-target payloads. The "
        "materialized winner is currently chosen by op.sequence with no "
        "precedence rule; this is a §1.7 ambiguity until a precedence "
        "claim proves which act prevails. Apply order is unchanged; the "
        "finding makes the silent pick visible and strict-rejectable."
    ),
    "affected_target": "(('section', '5'),)",
    "effective_date": "2026-01-01",
    "reason_code": "same_moment_cross_act_incompatible_payload",
    "resolution": "sequence_order_unproven",
    "conflicting_affecting_acts": ("ee/act-a/2025", "ee/act-b/2025"),
    "conflicting_ops": (
        {
            "op_id": "ee-A",
            "affecting_act_id": "ee/act-a/2025",
            "action": "replace",
            "sequence": 1,
            "target": "section:5",
        },
        {
            "op_id": "ee-B",
            "affecting_act_id": "ee/act-b/2025",
            "action": "replace",
            "sequence": 2,
            "target": "section:5",
        },
    ),
}

_REPLACE_REPEAL_EXPECTED: dict[str, Any] = {
    **_REPLACE_REPLACE_EXPECTED,
    "conflicting_ops": (
        {
            "op_id": "ee-A",
            "affecting_act_id": "ee/act-a/2025",
            "action": "replace",
            "sequence": 1,
            "target": "section:5",
        },
        {
            "op_id": "ee-B",
            "affecting_act_id": "ee/act-b/2025",
            "action": "repeal",
            "sequence": 2,
            "target": "section:5",
        },
    ),
}

_REPEAL_TEXT_REPLACE_EXPECTED: dict[str, Any] = {
    **_REPLACE_REPLACE_EXPECTED,
    "conflicting_ops": (
        {
            "op_id": "ee-A",
            "affecting_act_id": "ee/act-a/2025",
            "action": "repeal",
            "sequence": 1,
            "target": "section:5",
        },
        {
            "op_id": "ee-B",
            "affecting_act_id": "ee/act-b/2025",
            "action": "text_replace",
            "sequence": 2,
            "target": "section:5",
        },
    ),
}


def _assert_byte_identical_finding(
    actual: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Assert the finding envelope and payload are byte-identical."""
    # Envelope keys (per ``diagnostic_detail`` contract):
    assert actual["rule_id"] == expected["rule_id"]
    assert actual["rule_id"] == EE_SAME_MOMENT_AMBIGUITY_RULE_ID
    assert actual["phase"] == expected["phase"]
    assert actual["blocking"] is expected["blocking"]
    assert actual["strict_disposition"] == expected["strict_disposition"]
    assert actual["quirks_disposition"] == expected["quirks_disposition"]
    assert actual["family"] == expected["family"]
    assert actual["reason"] == expected["reason"]
    # Detail payload keys:
    assert actual["affected_target"] == expected["affected_target"]
    assert actual["effective_date"] == expected["effective_date"]
    assert actual["reason_code"] == expected["reason_code"]
    assert actual["resolution"] == expected["resolution"]
    assert actual["conflicting_affecting_acts"] == expected["conflicting_affecting_acts"]
    assert actual["conflicting_ops"] == expected["conflicting_ops"]


def test_parity_replace_replace_same_date_byte_identical() -> None:
    """Two REPLACEs on §5 from distinct acts at the same date -> one finding,
    byte-identical to the pre-retirement standalone EE detector."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
        _replace_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01", "BBB."),
    ]
    findings = _detect_ee(ops)
    assert len(findings) == 1
    _assert_byte_identical_finding(findings[0], _REPLACE_REPLACE_EXPECTED)


def test_parity_replace_repeal_same_date_byte_identical() -> None:
    """REPLACE+REPEAL on §5 at the same date -> one finding, byte-identical
    (incompatible: cannot both delete and amend)."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
        _repeal_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01"),
    ]
    findings = _detect_ee(ops)
    assert len(findings) == 1
    _assert_byte_identical_finding(findings[0], _REPLACE_REPEAL_EXPECTED)


def test_parity_repeal_text_replace_same_date_byte_identical() -> None:
    """REPEAL+TEXT_REPLACE on §5 at the same date -> one finding,
    byte-identical. This is the parity case where EE's predicate diverges from
    the shared module's default conservative predicate (EE treats the
    whole-target REPEAL as incompatible with any other structural change;
    default would treat the fragment operand as non-structural). Supplying the
    EE predicate explicitly preserves the pre-retirement behaviour."""
    ops = [
        _repeal_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01"),
        _text_replace_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01"),
    ]
    findings = _detect_ee(ops)
    assert len(findings) == 1
    _assert_byte_identical_finding(findings[0], _REPEAL_TEXT_REPLACE_EXPECTED)


def test_parity_two_repeals_same_target_not_incompatible() -> None:
    """Two REPEALs of §5 from distinct acts are redundant destructive effects
    with the same outcome — NOT order-determining; no finding."""
    ops = [
        _repeal_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01"),
        _repeal_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01"),
    ]
    assert _detect_ee(ops) == []


def test_parity_two_text_replaces_not_incompatible() -> None:
    """Two TEXT_REPLACEs on §5 at the same date are fragment-level and not
    flagged as incompatible."""
    ops = [
        _text_replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01"),
        _text_replace_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01"),
    ]
    assert _detect_ee(ops) == []


def test_parity_different_effective_dates_not_a_same_moment_conflict() -> None:
    """Two REPLACEs on §5 from distinct acts but DIFFERENT effective dates
    are not a same-EFFECTIVE-DATE collision."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
        _replace_op("ee-B", 2, "5", "ee/act-b/2025", "2027-01-01", "BBB."),
    ]
    assert _detect_ee(ops) == []


def test_parity_undated_ops_not_bucketed() -> None:
    """Two REPLACEs with no effective date provenance are not bucketed
    together — undated ops cannot participate in a same-moment conflict."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "", "AAA."),
        _replace_op("ee-B", 2, "5", "ee/act-b/2025", "", "BBB."),
    ]
    assert _detect_ee(ops) == []


def test_parity_same_act_two_ops_not_cross_act() -> None:
    """Two ops from the SAME act on §5 at the same date are not a cross-act
    §1.7 conflict — within-source ordering/scope is its own lane."""
    ops = [
        _replace_op("ee-A1", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA1."),
        _replace_op("ee-A2", 2, "5", "ee/act-a/2025", "2026-01-01", "AAA2."),
    ]
    assert _detect_ee(ops) == []


def test_parity_single_op_no_finding() -> None:
    """Negative: a single op on §5 — no cross-act conflict, no finding."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
    ]
    assert _detect_ee(ops) == []


def test_parity_adjudication_surface_byte_identical() -> None:
    """The blocking ``CompileAdjudication`` append surface produces the same
    shape as the finding detail dict (cross-act finding at empty ``op_id``,
    empty ``source_statute``, blocking=True)."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
        _replace_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01", "BBB."),
    ]
    adjudications: list[CompileAdjudication] = []
    findings = detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        incompatible_payload_predicate=ee_same_moment_payloads_incompatible,
        adjudications_out=adjudications,
    )
    assert len(adjudications) == 1
    assert len(findings) == 1
    adj = adjudications[0]
    # The finding detail dict is mirrored into the adjudication's ``detail``;
    # the ``message`` is the record's ``reason``.
    assert adj.kind == EE_SAME_MOMENT_AMBIGUITY_RULE_ID
    assert adj.blocking is True
    assert adj.op_id == ""
    assert adj.source_statute == ""
    assert adj.phase == "apply"
    assert adj.detail == findings[0]
    assert adj.message == findings[0]["reason"]


def test_parity_producer_set_equals_consumer_set_for_ee_kind_prefix() -> None:
    """Producer-set == consumer-set (§2.5/§2.6): the EE-specific compatibility
    predicate is the only EE-local tail consumed by the shared module. The
    shared module's API contract requires ``finder_kind_prefix="ee"`` to be
    passed at the call site; this test pins that the constant imported as
    :data:`EE_SAME_MOMENT_AMBIGUITY_RULE_ID` equals the shared module's
    emitted ``rule_id`` when ``finder_kind_prefix="ee"`` — i.e. there is no
    drift between EE's stable rule id surface and the shared module's prefix
    contract."""
    ops = [
        _replace_op("ee-A", 1, "5", "ee/act-a/2025", "2026-01-01", "AAA."),
        _replace_op("ee-B", 2, "5", "ee/act-b/2025", "2026-01-01", "BBB."),
    ]
    findings = _detect_ee(ops)
    assert findings[0]["rule_id"] == EE_SAME_MOMENT_AMBIGUITY_RULE_ID
    # Sanity: the prefix is used literally in the rule id (no hyphens or
    # unexpected casing — the shared module's ``_validate_finder_kind_prefix``
    # enforces lowercase ASCII identifier; EE passes "ee").
    assert findings[0]["rule_id"].startswith("ee_same_moment_cross_act_incompatible_payload_ambiguous")
