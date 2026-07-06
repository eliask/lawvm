"""§1.7 same-moment cross-act incompatible-payload ambiguity detection for NZ.

NZ was the LAST frontend still at ZERO same-moment routing (EE/NO/EU/SE/US all
enact it). :func:`lawvm.new_zealand.chain_replay._bucket_ops_into_transitions`
partitions the enumerated chain ops by ``amendment_date_iso`` — one bucket is one
effective moment — and orders within a moment by ``(family, amending_work_id,
row_id)``. When two distinct amending acts effect the SAME target on the SAME
amendment date with incompatible whole-target payloads, that within-moment order
silently decided a §1.7 legal conflict by ``amending_work_id`` accident.

The unified ordering kernel (``order_ops`` + the shared
``detect_cross_act_same_moment_conflicts`` delegate) is now wired into the
bucketing seam via :func:`nz_ordering_profile`. It is ADDITIVE — the NZChainOp
apply order is unchanged (byte-identical transitions) — but it emits a BLOCKING
finding for a genuine same-moment cross-act incompatible-payload collision so the
silent pick is visible and strict-rejectable.

Guard-liveness (AGENTS.md §2.9): these synthesized ops are driven through the
FULL production ``_bucket_ops_into_transitions`` lane (the seam that both orders
the transitions and produces the findings), not the shared detector unit.

Latency note: on today's corpus NZ carries ``LegalOperation``s only on the
repeal (``REPEAL``) and text_replace (``TEXT_PATCH``) families — replace/insert
``NZChainOp``s carry ``operation=None`` and never reach the detector — and two
cross-act ``REPEAL``s are redundant (not order-determining) while ``TEXT_PATCH``
is fragment-level, so NO collision fires on the real corpus. The tests below
synthesize the collision the wire now guarantees to catch (a genuine cross-act
``REPEAL``-vs-``REPLACE`` / ``REPLACE``-vs-``REPLACE`` at one moment), proving the
routing is deterministic rather than order-accidental.
"""
from __future__ import annotations

from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.op_ordering import order_ops
from lawvm.new_zealand.chain_replay import (
    NZChainOp,
    _bucket_ops_into_transitions,
    nz_ordering_profile,
)
from lawvm.replay_adjudication import CompileAdjudication

NZ_SAME_MOMENT_AMBIGUITY_RULE_ID = (
    "nz_same_moment_cross_act_incompatible_payload_ambiguous"
)


def _replace_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
    replacement_text: str,
) -> LegalOperation:
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION, label=section_label, text=replacement_text
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _repeal_op(
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


def _chain_op(
    *,
    family: str,
    row_id: str,
    date: str,
    amending: str,
    operation: LegalOperation | None,
) -> NZChainOp:
    return NZChainOp(
        family=family,
        row_id=row_id,
        amendment_date_iso=date,
        amending_work_id=amending,
        source_path=("section", "5"),
        target_resolution_status="exact_source_path",
        operation=operation,
    )


def _same_moment_findings(
    findings: tuple[CompileAdjudication, ...],
) -> list[CompileAdjudication]:
    return [f for f in findings if f.kind == NZ_SAME_MOMENT_AMBIGUITY_RULE_ID]


def test_two_distinct_acts_replace_same_target_same_moment_emits_ambiguity() -> None:
    """Two REPLACE ops on §5 from distinct acts at the same amendment date."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A new wording.",
            ),
        ),
        _chain_op(
            family="replace",
            row_id="r-b",
            date="2026-01-01",
            amending="nz/act-b/2025",
            operation=_replace_op(
                op_id="nz-replace-B",
                sequence=2,
                section_label="5",
                source_id="nz/act-b/2025",
                effective="2026-01-01",
                replacement_text="Act B new wording.",
            ),
        ),
    ]
    transitions, findings = _bucket_ops_into_transitions(ops)

    moments = _same_moment_findings(findings)
    assert len(moments) == 1, f"expected 1 same-moment finding; got {moments!r}"
    finding = moments[0]
    assert finding.blocking is True
    assert finding.op_id == ""
    detail = finding.detail
    assert detail["rule_id"] == NZ_SAME_MOMENT_AMBIGUITY_RULE_ID
    assert detail["phase"] == "apply"
    assert detail["family"] == "temporal_recovery"
    assert detail["effective_date"] == "2026-01-01"
    assert detail["reason_code"] == "same_moment_cross_act_incompatible_payload"
    assert detail["resolution"] == "sequence_order_unproven"
    assert set(detail["conflicting_affecting_acts"]) == {
        "nz/act-a/2025",
        "nz/act-b/2025",
    }
    conflicting_op_ids = {op["op_id"] for op in detail["conflicting_ops"]}
    assert conflicting_op_ids == {"nz-replace-A", "nz-replace-B"}

    # ADDITIVE: apply order is unchanged — the transition still carries both ops
    # in the deterministic (family, amending_work_id, row_id) order.
    assert len(transitions) == 1
    assert [op.row_id for op in transitions[0].ops] == ["r-a", "r-b"]


def test_repeal_versus_replace_same_moment_is_incompatible() -> None:
    """A REPEAL of §5 against a REPLACE of §5 at the same moment is incompatible
    (you cannot both delete a provision and amend it at the same instant)."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A replacement.",
            ),
        ),
        _chain_op(
            family="repeal",
            row_id="r-b",
            date="2026-01-01",
            amending="nz/act-b/2025",
            operation=_repeal_op(
                op_id="nz-repeal-B",
                sequence=2,
                section_label="5",
                source_id="nz/act-b/2025",
                effective="2026-01-01",
            ),
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)

    moments = _same_moment_findings(findings)
    assert len(moments) == 1
    assert set(moments[0].detail["conflicting_affecting_acts"]) == {
        "nz/act-a/2025",
        "nz/act-b/2025",
    }


def test_two_repeals_same_target_are_not_incompatible() -> None:
    """Two REPEALs of §5 from distinct acts are redundant destructive effects —
    NOT order-determining. No finding (avoids false ambiguity from coexistence).
    This is the real NZ corpus shape (repeal-only same-target collisions)."""
    ops = [
        _chain_op(
            family="repeal",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_repeal_op(
                op_id="nz-repeal-A",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
            ),
        ),
        _chain_op(
            family="repeal",
            row_id="r-b",
            date="2026-01-01",
            amending="nz/act-b/2025",
            operation=_repeal_op(
                op_id="nz-repeal-B",
                sequence=2,
                section_label="5",
                source_id="nz/act-b/2025",
                effective="2026-01-01",
            ),
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)
    assert _same_moment_findings(findings) == []


def test_different_dates_no_ambiguity_finding() -> None:
    """Two REPLACE ops on §5 but DIFFERENT amendment dates are not a same-moment
    collision — they land in separate buckets."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A new wording.",
            ),
        ),
        _chain_op(
            family="replace",
            row_id="r-b",
            date="2027-01-01",
            amending="nz/act-b/2025",
            operation=_replace_op(
                op_id="nz-replace-B",
                sequence=2,
                section_label="5",
                source_id="nz/act-b/2025",
                effective="2027-01-01",
                replacement_text="Act B new wording.",
            ),
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)
    assert _same_moment_findings(findings) == []


def test_same_act_two_ops_no_cross_act_finding() -> None:
    """Two ops from the SAME act on §5 at the same moment are not a cross-act
    §1.7 conflict — within-source ordering is its own lane."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a1",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A1",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A first.",
            ),
        ),
        _chain_op(
            family="replace",
            row_id="r-a2",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A2",
                sequence=2,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A second.",
            ),
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)
    assert _same_moment_findings(findings) == []


def test_ops_without_legal_operation_never_reach_detector() -> None:
    """replace/insert ``NZChainOp``s that carry ``operation=None`` (the real NZ
    replace/insert shape) never enter the detector — no false collision."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=None,
        ),
        _chain_op(
            family="insert",
            row_id="r-b",
            date="2026-01-01",
            amending="nz/act-b/2025",
            operation=None,
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)
    assert _same_moment_findings(findings) == []


def test_single_op_no_ambiguity_finding() -> None:
    """A single op at a moment — no cross-act conflict, no finding."""
    ops = [
        _chain_op(
            family="replace",
            row_id="r-a",
            date="2026-01-01",
            amending="nz/act-a/2025",
            operation=_replace_op(
                op_id="nz-replace-A",
                sequence=1,
                section_label="5",
                source_id="nz/act-a/2025",
                effective="2026-01-01",
                replacement_text="Act A new wording.",
            ),
        ),
    ]
    _transitions, findings = _bucket_ops_into_transitions(ops)
    assert _same_moment_findings(findings) == []


def test_nz_profile_uses_nz_prefix() -> None:
    """The NZ profile stamps the ``nz`` finder prefix so the finding kind and any
    claim rule_ids land on NZ's own audit-trail channel (per-frontend distinct)."""
    profile = nz_ordering_profile()
    assert profile.finder_kind_prefix == "nz"
    # An empty op set is a no-op (empty findings) through the kernel with the NZ
    # profile — the deterministic-ordering contract holds for the degenerate case.
    assert order_ops([], profile).findings == ()
