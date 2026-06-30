"""Synthetic coverage for the unified ordering kernel ``core/op_ordering.py``.

Wave 0 (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.2). Exercises the v1
algebra composition in isolation (no frontend): temporal+sequence ordering, the
delegated same-moment detection (blocking finding + ``sequence_order_unproven``
order on an unresolved collision), validated-claim resolution, the lex-posterior
tiebreak when enabled, and the empty -> empty identity. The same-moment step is
asserted to DELEGATE to the shared detector (it is not reimplemented here).
"""
from __future__ import annotations

from lawvm.core.cross_act_same_moment import (
    BASIS_LATER_ENACTMENT,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    SameMomentPrecedenceClaim,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from dataclasses import replace

from lawvm.core.op_ordering import (
    OrderedOps,
    OrderingProfile,
    default_temporal_key,
    order_ops,
)
from lawvm.core.semantic_types import IRNodeKind

SAME_MOMENT_KIND = "se_same_moment_cross_act_incompatible_payload_ambiguous"


def _replace_op(
    *,
    op_id: str,
    sequence: int,
    section_label: str,
    source_id: str,
    effective: str,
    replacement_text: str = "ny lydelse",
) -> LegalOperation:
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
    *, op_id: str, sequence: int, section_label: str, source_id: str, effective: str
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section_label),)),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _se_profile(**overrides: object) -> OrderingProfile:
    base = OrderingProfile(finder_kind_prefix="se")
    if not overrides:
        return base
    return replace(base, **overrides)  # type: ignore[arg-type]


# ── empty -> empty ───────────────────────────────────────────────────────────


def test_empty_ops_returns_empty_result() -> None:
    result = order_ops([], _se_profile())
    assert result == OrderedOps(ops=(), justification=(), findings=())


# ── default temporal key preserves input order; justification is total ───────


def test_default_temporal_key_is_input_order_and_justification_total() -> None:
    """default_temporal_key (sequence-identity) -> the ops come back in input
    order, and the justification has one decision per op at its position."""
    ops = [
        _replace_op(
            op_id="a", sequence=3, section_label="5", source_id="act-a", effective=""
        ),
        _replace_op(
            op_id="b", sequence=1, section_label="6", source_id="act-b", effective=""
        ),
        _replace_op(
            op_id="c", sequence=2, section_label="7", source_id="act-c", effective=""
        ),
    ]
    result = order_ops(ops, _se_profile())
    # Sorted by (sequence, sequence): b(1), c(2), a(3).
    assert [op.op_id for op in result.ops] == ["b", "c", "a"]
    assert [d.op_id for d in result.justification] == ["b", "c", "a"]
    assert [d.position for d in result.justification] == [0, 1, 2]
    assert all(d.stage == "temporal_sequence_stable" for d in result.justification)
    assert result.findings == ()


def test_default_temporal_key_value_is_sequence() -> None:
    op = _replace_op(
        op_id="x", sequence=7, section_label="1", source_id="act", effective=""
    )
    assert default_temporal_key(op) == 7


# ── custom temporal key drives the order ─────────────────────────────────────


def test_custom_temporal_key_orders_by_effective_date_then_sequence() -> None:
    """A frontend supplying an effective-date temporal key orders by date first,
    sequence as the stable secondary tiebreak."""
    ops = [
        _replace_op(
            op_id="late",
            sequence=1,
            section_label="1",
            source_id="act-a",
            effective="2027-01-01",
        ),
        _replace_op(
            op_id="early-2",
            sequence=5,
            section_label="2",
            source_id="act-b",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="early-1",
            sequence=9,
            section_label="3",
            source_id="act-c",
            effective="2026-01-01",
        ),
    ]
    profile = _se_profile(
        temporal_key=lambda op: (op.source.effective if op.source else "",)
    )
    result = order_ops(ops, profile)
    # 2026-01-01 group first, ordered by sequence (5 < 9), then 2027.
    assert [op.op_id for op in result.ops] == ["early-2", "early-1", "late"]


# ── same-moment detection delegates -> blocking finding + unproven order ──────


def test_same_moment_incompatible_pair_yields_blocking_finding() -> None:
    """Two same-moment incompatible REPLACE ops from distinct acts -> one
    blocking finding (delegated to the shared detector) and a deterministic
    sequence_order_unproven order (apply order unchanged)."""
    ops = [
        _replace_op(
            op_id="A",
            sequence=1,
            section_label="5",
            source_id="act-a/2025",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="B",
            sequence=2,
            section_label="5",
            source_id="act-b/2025",
            effective="2026-01-01",
        ),
    ]
    result = order_ops(ops, _se_profile())
    # Apply order is unchanged (additive detector).
    assert [op.op_id for op in result.ops] == ["A", "B"]
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.kind == SAME_MOMENT_KIND
    assert finding.blocking is True
    assert finding.op_id == ""
    assert finding.detail["resolution"] == "sequence_order_unproven"
    assert set(finding.detail["conflicting_affecting_acts"]) == {
        "act-a/2025",
        "act-b/2025",
    }


def test_two_repeals_same_target_not_incompatible_no_finding() -> None:
    """Two REPEALs of the same target are redundant (same outcome), not
    order-determining -> the delegated detector emits no finding."""
    ops = [
        _repeal_op(
            op_id="A",
            sequence=1,
            section_label="5",
            source_id="act-a",
            effective="2026-01-01",
        ),
        _repeal_op(
            op_id="B",
            sequence=2,
            section_label="5",
            source_id="act-b",
            effective="2026-01-01",
        ),
    ]
    result = order_ops(ops, _se_profile())
    assert result.findings == ()


def test_different_effective_dates_no_same_moment_finding() -> None:
    ops = [
        _replace_op(
            op_id="A",
            sequence=1,
            section_label="5",
            source_id="act-a",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="B",
            sequence=2,
            section_label="5",
            source_id="act-b",
            effective="2027-01-01",
        ),
    ]
    result = order_ops(ops, _se_profile())
    assert result.findings == ()


# ── validated precedence claim resolves the collision ────────────────────────


def test_validated_precedence_claim_resolves_collision() -> None:
    """A validated same-moment precedence claim turns the finding non-blocking
    (resolution=resolved_by_claim) — proving the profile's precedence_claims are
    threaded into the delegated detector."""
    ops = [
        _replace_op(
            op_id="A",
            sequence=1,
            section_label="5",
            source_id="act-a/2025",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="B",
            sequence=2,
            section_label="5",
            source_id="act-b/2025",
            effective="2026-01-01",
        ),
    ]
    target = str((("section", "5"),))
    claim = SameMomentPrecedenceClaim(
        claim_id="claim-1",
        claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date="2026-01-01",
        affected_target=target,
        conflicting_affecting_acts=("act-a/2025", "act-b/2025"),
        winner_affecting_act_id="act-b/2025",
        basis=BASIS_LATER_ENACTMENT,
    )
    result = order_ops(ops, _se_profile(precedence_claims=(claim,)))
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.blocking is False
    assert finding.detail["resolution"] == "resolved_by_claim"
    assert (
        finding.detail["resolved_by_claim_winner_affecting_act_id"] == "act-b/2025"
    )


# ── lex-posterior tiebreak only when enabled ─────────────────────────────────


def test_lex_posterior_off_keeps_sequence_order() -> None:
    """With lex_posterior off (SE default) ties keep sequence order regardless of
    affecting act id."""
    ops = [
        _replace_op(
            op_id="z-first",
            sequence=1,
            section_label="1",
            source_id="zzz-act",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="a-second",
            sequence=2,
            section_label="2",
            source_id="aaa-act",
            effective="2026-01-01",
        ),
    ]
    # Same temporal key (effective date) for both -> genuine tie.
    profile = _se_profile(
        temporal_key=lambda op: (op.source.effective if op.source else "",),
        lex_posterior=False,
    )
    result = order_ops(ops, profile)
    assert [op.op_id for op in result.ops] == ["z-first", "a-second"]


def test_lex_posterior_on_breaks_ties_by_affecting_act() -> None:
    """With lex_posterior on, a same-temporal-key tie is broken by affecting act
    id lexical order (aaa-act before zzz-act), overriding sequence."""
    ops = [
        _replace_op(
            op_id="z-first",
            sequence=1,
            section_label="1",
            source_id="zzz-act",
            effective="2026-01-01",
        ),
        _replace_op(
            op_id="a-second",
            sequence=2,
            section_label="2",
            source_id="aaa-act",
            effective="2026-01-01",
        ),
    ]
    profile = _se_profile(
        temporal_key=lambda op: (op.source.effective if op.source else "",),
        lex_posterior=True,
    )
    result = order_ops(ops, profile)
    # aaa-act < zzz-act -> a-second moves ahead of z-first.
    assert [op.op_id for op in result.ops] == ["a-second", "z-first"]


# ── structural vacate ordering (renumber_vacate=True; NO/UK-shared stage) ─────


def _renumber_op(
    *, op_id: str, sequence: int, frm: str, to: str, source_id: str, effective: str
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", frm),)),
        destination=LegalAddress(path=(("section", to),)),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def test_renumber_vacate_orders_destination_vacated_before_occupied() -> None:
    """``renumber_vacate=True`` reorders a renumber chain so a destination is
    vacated before it is occupied: 5->6 needs §6 free, so 6->7 must come first.

    This proves the structural-vacate stage is a SHARED, frontend-neutral kernel
    capability (no NO import) — UK can reuse it by setting the same flag.
    """
    ops = [
        _renumber_op(op_id="r1", sequence=1, frm="5", to="6", source_id="act-a", effective="2026-01-01"),
        _renumber_op(op_id="r2", sequence=2, frm="6", to="7", source_id="act-a", effective="2026-01-01"),
    ]
    profile = _se_profile(renumber_vacate=True)
    result = order_ops(ops, profile)
    assert [op.op_id for op in result.ops] == ["r2", "r1"]


def test_renumber_vacate_repeal_first_then_renumber_then_rest() -> None:
    """The within-group order is REPEAL-first, then topological RENUMBER, then
    the rest by sequence — independent of input order."""
    ops = [
        _replace_op(op_id="rep", sequence=4, section_label="9", source_id="act-a", effective="2026-01-01"),
        _renumber_op(op_id="rn1", sequence=2, frm="5", to="6", source_id="act-a", effective="2026-01-01"),
        _repeal_op(op_id="rpl", sequence=1, section_label="1", source_id="act-a", effective="2026-01-01"),
        _renumber_op(op_id="rn2", sequence=3, frm="6", to="7", source_id="act-a", effective="2026-01-01"),
    ]
    profile = _se_profile(renumber_vacate=True)
    result = order_ops(ops, profile)
    # REPEAL (rpl), then topological renumbers (rn2 vacates §6 before rn1 takes
    # it), then the replace.
    assert [op.op_id for op in result.ops] == ["rpl", "rn2", "rn1", "rep"]


def test_renumber_vacate_groups_by_renumber_group_key() -> None:
    """With a group key, the structural-vacate stage operates per group while
    preserving the temporal order between groups."""
    ops = [
        _renumber_op(op_id="a1", sequence=1, frm="5", to="6", source_id="act-a", effective="2026-01-01"),
        _renumber_op(op_id="a2", sequence=2, frm="6", to="7", source_id="act-a", effective="2026-01-01"),
        _renumber_op(op_id="b1", sequence=3, frm="2", to="3", source_id="act-b", effective="2027-01-01"),
        _renumber_op(op_id="b2", sequence=4, frm="3", to="4", source_id="act-b", effective="2027-01-01"),
    ]
    profile = _se_profile(
        temporal_key=lambda op: (
            op.source.effective if op.source else "",
            op.source.statute_id if op.source else "",
            op.sequence,
        ),
        renumber_vacate=True,
        renumber_group_key=lambda op: (
            op.source.effective if op.source else "",
            op.source.statute_id if op.source else "",
        ),
    )
    result = order_ops(ops, profile)
    # Each act-group is independently topologically ordered (vacate-first), and
    # the 2026 group precedes the 2027 group.
    assert [op.op_id for op in result.ops] == ["a2", "a1", "b2", "b1"]
