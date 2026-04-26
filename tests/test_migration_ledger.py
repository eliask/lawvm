"""Tests for the migration ledger and its integration with apply_typed_dispatch.

Covers:
  - MigrationLedger unit operations (record, query, chain following)
  - Integration: apply_op with Relabel intent emits MigrationEvent into ledger
  - Edge cases: no ledger (None), skipped relabels, cycle guard

Run:
    uv run pytest tests/test_migration_ledger.py -v
"""
from __future__ import annotations
from lawvm.core.ir import LegalAddress

import datetime as dt
from types import SimpleNamespace
from typing import Literal, Optional, cast

from lawvm.core.canonical_intent import (
    ExecutionContract,
    IntentKind,
    NodeTarget,
    OccupancyPolicy,
    Relabel,
)
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply import apply_op
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.statute import ReplayState, StatuteContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE = dt.date(2020, 1, 1)


def _addr(*parts: tuple[str, str]) -> LegalAddress:
    return LegalAddress(path=parts)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _chap(label: str, *children: IRNode) -> IRNode:
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=label,
        children=(IRNode(kind=IRNodeKind.NUM, text=f"{label} luku"), *children),
    )


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _ctx(base_ir: Optional[IRNode] = None) -> StatuteContext:
    return cast(StatuteContext, SimpleNamespace(base_ir=base_ir))


def _contract() -> ExecutionContract:
    return ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace())


# ---------------------------------------------------------------------------
# Unit tests: MigrationLedger
# ---------------------------------------------------------------------------


class TestMigrationLedgerUnit:
    def test_empty_ledger(self) -> None:
        ledger = MigrationLedger()
        assert len(ledger) == 0
        assert not ledger
        assert ledger.events == ()

    def test_record_renumber(self) -> None:
        ledger = MigrationLedger()
        from_addr = _addr(("section", "1"))
        to_addr = _addr(("section", "1a"))
        event = ledger.record_renumber(
            from_addr, to_addr,
            effective="2020-01-01",
            source_statute="2020/100",
        )
        assert event.kind == "renumber"
        assert event.from_address == from_addr
        assert event.to_address == to_addr
        assert event.effective == "2020-01-01"
        assert event.source_statute == "2020/100"
        assert event.event_id == f"mig:2020/100:{from_addr}\u2192{to_addr}"
        assert len(ledger) == 1
        assert ledger.events == (event,)

    def test_record_move(self) -> None:
        ledger = MigrationLedger()
        from_addr = _addr(("chapter", "1"), ("section", "5"))
        to_addr = _addr(("chapter", "2"), ("section", "5"))
        event = ledger.record_move(
            from_addr, to_addr,
            effective="2021-06-15",
            source_statute="2021/200",
        )
        assert event.kind == "move"
        assert event.from_address == from_addr
        assert event.to_address == to_addr
        assert len(ledger) == 1

    def test_record_renumber_strips_empty_label_wrappers(self) -> None:
        ledger = MigrationLedger()
        event = ledger.record_renumber(
            _addr(("hcontainer", ""), ("section", "11")),
            _addr(("hcontainer", ""), ("section", "12")),
            effective="1992-10-01",
            source_statute="1992/878",
        )

        assert event.from_address == _addr(("section", "11"))
        assert event.to_address == _addr(("section", "12"))
        assert ledger.current_address_with_prefix_migrations(_addr(("section", "11"))) == _addr(
            ("section", "12")
        )

    def test_query_lineage_both_directions(self) -> None:
        ledger = MigrationLedger()
        addr_a = _addr(("section", "1"))
        addr_b = _addr(("section", "1a"))
        addr_c = _addr(("section", "2"))
        ledger.record_renumber(addr_a, addr_b, source_statute="2020/1")
        ledger.record_renumber(addr_c, addr_a, source_statute="2019/1")

        # addr_a appears in both events
        lineage = ledger.query_lineage(addr_a)
        assert len(lineage) == 2

        # addr_b appears only in the first event
        lineage_b = ledger.query_lineage(addr_b)
        assert len(lineage_b) == 1
        assert lineage_b[0].from_address == addr_a

    def test_query_lineage_no_match(self) -> None:
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("section", "1")), _addr(("section", "2")),
            source_statute="2020/1",
        )
        assert ledger.query_lineage(_addr(("section", "99"))) == []

    def test_current_address_follows_chain(self) -> None:
        ledger = MigrationLedger()
        addr1 = _addr(("section", "1"))
        addr2 = _addr(("section", "1a"))
        addr3 = _addr(("section", "1b"))
        ledger.record_renumber(addr1, addr2, effective="2020-01-01", source_statute="2020/1")
        ledger.record_renumber(addr2, addr3, effective="2021-01-01", source_statute="2021/1")

        assert ledger.current_address(addr1) == addr3
        assert ledger.current_address(addr2) == addr3
        assert ledger.current_address(addr3) == addr3  # no further link

    def test_current_address_respects_as_of_date(self) -> None:
        ledger = MigrationLedger()
        addr1 = _addr(("section", "1"))
        addr2 = _addr(("section", "1a"))
        addr3 = _addr(("section", "1b"))
        ledger.record_renumber(addr1, addr2, effective="2020-01-01", source_statute="2020/1")
        ledger.record_renumber(addr2, addr3, effective="2025-01-01", source_statute="2025/1")

        # As of 2022, only the first renumber applies
        assert ledger.current_address(addr1, as_of_date="2022-01-01") == addr2
        # As of 2026, both apply
        assert ledger.current_address(addr1, as_of_date="2026-01-01") == addr3

    def test_current_address_no_match(self) -> None:
        ledger = MigrationLedger()
        addr = _addr(("section", "42"))
        assert ledger.current_address(addr) == addr

    def test_deterministic_event_id(self) -> None:
        ledger = MigrationLedger()
        from_addr = _addr(("chapter", "3"), ("section", "12"))
        to_addr = _addr(("chapter", "3"), ("section", "12a"))
        event = ledger.record_renumber(from_addr, to_addr, source_statute="2020/55")
        expected_id = f"mig:2020/55:{from_addr}\u2192{to_addr}"
        assert event.event_id == expected_id

    def test_multiple_events_accumulate(self) -> None:
        ledger = MigrationLedger()
        for i in range(5):
            ledger.record_renumber(
                _addr(("section", str(i))),
                _addr(("section", str(i + 10))),
                source_statute=f"2020/{i}",
            )
        assert len(ledger) == 5
        assert len(ledger.events) == 5

    def test_bool_truthiness(self) -> None:
        ledger = MigrationLedger()
        assert not ledger
        ledger.record_renumber(
            _addr(("section", "1")), _addr(("section", "2")),
            source_statute="2020/1",
        )
        assert ledger

    def test_prefix_migrations_do_not_chain_across_same_wave_siblings(self) -> None:
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("part", "III")),
            _addr(("part", "IV")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "IV")),
            _addr(("part", "V")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "VII")),
            _addr(("part", "VIII")),
            effective="2019-04-01",
            source_statute="2019/371",
        )

        addr = _addr(("part", "III"), ("chapter", "2"), ("section", "159"))
        assert ledger.current_address_with_prefix_migrations(addr) == _addr(
            ("part", "4"), ("chapter", "2"), ("section", "159")
        )

    def test_prefix_migrations_combine_specific_and_ancestor_same_wave(self) -> None:
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("part", "III")),
            _addr(("part", "IV")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "III"), ("chapter", "2"), ("section", "5")),
            _addr(("part", "III"), ("chapter", "2"), ("section", "159")),
            effective="2019-04-01",
            source_statute="2019/371",
        )

        addr = _addr(("part", "III"), ("chapter", "2"), ("section", "5"))
        assert ledger.current_address_with_prefix_migrations(addr) == _addr(
            ("part", "4"), ("chapter", "2"), ("section", "159")
        )

    def test_prefix_migrations_follow_specific_destination_source_frame_parent(self) -> None:
        """A same-wave section relabel may target a parent frame also relabeled by the wave."""
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("part", "III")),
            _addr(("part", "IV")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "IV")),
            _addr(("part", "V")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "IV"), ("chapter", "1"), ("section", "10")),
            _addr(("part", "III"), ("chapter", "1"), ("section", "187")),
            effective="2019-04-01",
            source_statute="2019/371",
        )

        addr = _addr(("part", "IV"), ("chapter", "1"), ("section", "10"))
        assert ledger.current_address_with_prefix_migrations(addr) == _addr(
            ("part", "4"), ("chapter", "1"), ("section", "187")
        )

    def test_prefix_migrations_chain_across_later_waves(self) -> None:
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("part", "III")),
            _addr(("part", "IV")),
            effective="2019-04-01",
            source_statute="2019/371",
        )
        ledger.record_renumber(
            _addr(("part", "IV"), ("chapter", "2")),
            _addr(("part", "IV"), ("chapter", "18")),
            effective="2020-12-30",
            source_statute="2020/1256",
        )

        addr = _addr(("part", "III"), ("chapter", "2"), ("section", "159"))
        assert ledger.current_address_with_prefix_migrations(addr) == _addr(
            ("part", "4"), ("chapter", "18"), ("section", "159")
        )

    def test_prefix_migrations_same_wave_section_chain_uses_pre_act_frame(self) -> None:
        ledger = MigrationLedger()
        ledger.record_renumber(
            _addr(("section", "9")),
            _addr(("section", "10")),
            effective="1992-10-01",
            source_statute="1992/878",
        )
        ledger.record_renumber(
            _addr(("section", "10")),
            _addr(("section", "11")),
            effective="1992-10-01",
            source_statute="1992/878",
        )
        ledger.record_renumber(
            _addr(("section", "11")),
            _addr(("section", "12")),
            effective="1992-10-01",
            source_statute="1992/878",
        )

        assert ledger.current_address_with_prefix_migrations(_addr(("section", "9"))) == _addr(
            ("section", "10")
        )
        assert ledger.current_address_with_prefix_migrations(_addr(("section", "10"))) == _addr(
            ("section", "11")
        )
        assert ledger.current_address_with_prefix_migrations(_addr(("section", "11"))) == _addr(
            ("section", "12")
        )


# ---------------------------------------------------------------------------
# Integration test: apply_op with Relabel intent emits into ledger
# ---------------------------------------------------------------------------


def _make_relabel_rop(
    target_section: str,
    dest_section: str,
    target_chapter: Optional[str] = None,
    dest_chapter: Optional[str] = None,
    target_unit_kind: Literal["section", "chapter", "part"] = "section",
) -> tuple[ResolvedOp, Relabel]:
    """Build a ResolvedOp + Relabel intent for a section renumber."""
    src_path: list[tuple[str, str]] = []
    dst_path: list[tuple[str, str]] = []
    if target_chapter:
        src_path.append(("chapter", target_chapter))
    if dest_chapter:
        dst_path.append(("chapter", dest_chapter))
    else:
        dst_path = list(src_path)
    src_path.append(("section", target_section))
    dst_path.append(("section", dest_section))

    src_addr = LegalAddress(path=tuple(src_path))
    dst_addr = LegalAddress(path=tuple(dst_path))

    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(src_addr),
        destination=NodeTarget(dst_addr),
        contract=_contract(),
    )

    op = AmendmentOp(
        op_id="relabel_test",
        op_type="RENUMBER",
        target_section=target_section,
        target_unit_kind=target_unit_kind,
        target_chapter=target_chapter,
        source_statute="2020/500",
        source_issue_date=_DATE,
    )

    from lawvm.core.ir import OperationSource

    source_address = _addr(("chapter", target_chapter), ("section", target_section)) if target_chapter else _addr(("section", target_section))
    destination_address = _addr(("chapter", target_chapter), ("section", dest_section)) if target_chapter else _addr(("section", dest_section))

    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm=target_section,
        _op_type_seed="RENUMBER",
        _target_special_override=(
            op.target_special if op.target_special not in {None, "otsikko", "johd"} else None
        ),
        sec1_body_johto_fallback=op.sec1_body_johto_fallback,
        uncovered_body_recovery=op.uncovered_body_recovery,
        post_repeal_item_shift_label=op.post_repeal_item_shift_label,
        _source_statute_override="2020/500",
        _source_issue_date_override=op.source_issue_date,
        _source_title_override=op.source_title,
        intent=intent,
        _op_source_override=OperationSource(
            statute_id="2020/500",
            effective="2020-06-01",
        ),
        _target_address_override=source_address,
        _destination_address_override=destination_address,
    )
    return rop, intent


class TestMigrationLedgerIntegration:
    def test_section_relabel_emits_migration_event(self) -> None:
        """A successful section relabel through apply_op records a renumber event."""
        body = _body(_sec("5", _content("original")))
        state = ReplayState(ir=body)
        rop, intent = _make_relabel_rop("5", "5a")
        ledger = MigrationLedger()
        mutation_events: list[ApplyMutationEvent] = []

        result = apply_op(
            state, None, _ctx(body), None,
            rop=rop,
            mutation_events_out=mutation_events,
            migration_ledger=ledger,
        )

        # The tree should have been modified
        assert result is not state

        # The ledger should have exactly one renumber event
        assert len(ledger) == 1
        event = ledger.events[0]
        assert event.kind == "renumber"
        assert event.from_address == _addr(("section", "5"))
        assert event.to_address == _addr(("section", "5a"))
        assert event.source_statute == "2020/500"
        assert event.effective == "2020-06-01"

    def test_section_relabel_skipped_does_not_emit(self) -> None:
        """When source section is not found, no migration event is emitted."""
        body = _body(_sec("99", _content("unrelated")))
        state = ReplayState(ir=body)
        # Target section "5" doesn't exist in the tree
        rop, intent = _make_relabel_rop("5", "5a")
        ledger = MigrationLedger()

        result = apply_op(
            state, None, _ctx(body), None,
            rop=rop,
            migration_ledger=ledger,
        )

        # State unchanged
        assert result is state
        # No migration events
        assert len(ledger) == 0

    def test_no_ledger_does_not_raise(self) -> None:
        """When migration_ledger is None, relabel still works without error."""
        body = _body(_sec("5", _content("original")))
        state = ReplayState(ir=body)
        rop, _ = _make_relabel_rop("5", "5a")

        # migration_ledger=None (default) should not raise
        result = apply_op(
            state, None, _ctx(body), None,
            rop=rop,
            migration_ledger=None,
        )
        assert result is not state

    def test_chapter_relabel_emits_migration_event(self) -> None:
        """A successful chapter relabel emits a renumber event."""
        body = _body(_chap("3", _sec("1", _content("text"))))
        state = ReplayState(ir=body)

        src_addr = _addr(("chapter", "3"))
        dst_addr = _addr(("chapter", "3a"))

        intent = Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(src_addr),
            destination=NodeTarget(dst_addr),
            contract=_contract(),
        )

        op = AmendmentOp(
            op_id="ch_relabel",
            op_type="RENUMBER",
            target_section="3",
            target_unit_kind="chapter",
            source_statute="2021/10",
            source_issue_date=_DATE,
        )

        from lawvm.core.ir import OperationSource

        rop = ResolvedOp(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            amend_sub_ir=None,
            op_id=op.op_id,
            target_unit_kind="chapter",
            target_norm="3",
            _op_type_seed="RENUMBER",
            _target_special_override=(
                op.target_special if op.target_special not in {None, "otsikko", "johd"} else None
            ),
            sec1_body_johto_fallback=op.sec1_body_johto_fallback,
            uncovered_body_recovery=op.uncovered_body_recovery,
            post_repeal_item_shift_label=op.post_repeal_item_shift_label,
            _source_statute_override="2021/10",
            _source_issue_date_override=op.source_issue_date,
            _source_title_override=op.source_title,
            intent=intent,
            _op_source_override=OperationSource(statute_id="2021/10", effective="2021-03-01"),
            _target_address_override=src_addr,
            _destination_address_override=dst_addr,
        )

        ledger = MigrationLedger()
        result = apply_op(
            state, None, _ctx(body), None,
            rop=rop,
            migration_ledger=ledger,
        )

        assert result is not state
        assert len(ledger) == 1
        event = ledger.events[0]
        assert event.kind == "renumber"
        assert event.from_address == src_addr
        assert event.to_address == dst_addr
        assert event.source_statute == "2021/10"
        assert event.effective == "2021-03-01"

    def test_lineage_query_after_replay(self) -> None:
        """After multiple relabels, lineage query returns the full chain."""
        # First relabel: section 5 -> 5a
        body1 = _body(_sec("5", _content("v1")))
        state1 = ReplayState(ir=body1)
        rop1, _ = _make_relabel_rop("5", "5a")
        ledger = MigrationLedger()

        state2 = apply_op(
            state1, None, _ctx(body1), None,
            rop=rop1,
            migration_ledger=ledger,
        )

        # Second relabel: section 5a -> 5b (new rop with different addresses)
        src_addr2 = _addr(("section", "5a"))
        dst_addr2 = _addr(("section", "5b"))
        intent2 = Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(src_addr2),
            destination=NodeTarget(dst_addr2),
            contract=_contract(),
        )
        op2 = AmendmentOp(
            op_id="relabel_2",
            op_type="RENUMBER",
            target_section="5a",
            target_unit_kind="section",
            source_statute="2022/300",
            source_issue_date=dt.date(2022, 1, 1),
        )

        from lawvm.core.ir import OperationSource

        rop2 = ResolvedOp(
            op=op2,
            muutos_ir=None,
            cross_ir=None,
            amend_sub_ir=None,
            op_id=op2.op_id,
            target_unit_kind="section",
            target_norm="5a",
            _op_type_seed="RENUMBER",
            _target_special_override=(
                op2.target_special if op2.target_special not in {None, "otsikko", "johd"} else None
            ),
            sec1_body_johto_fallback=op2.sec1_body_johto_fallback,
            uncovered_body_recovery=op2.uncovered_body_recovery,
            post_repeal_item_shift_label=op2.post_repeal_item_shift_label,
            _source_statute_override="2022/300",
            _source_issue_date_override=op2.source_issue_date,
            _source_title_override=op2.source_title,
            intent=intent2,
            _op_source_override=OperationSource(statute_id="2022/300", effective="2022-06-01"),
            _target_address_override=src_addr2,
            _destination_address_override=dst_addr2,
        )

        state3 = apply_op(
            state2, None, _ctx(body1), None,
            rop=rop2,
            migration_ledger=ledger,
        )

        assert state3 is not state2
        assert len(ledger) == 2

        # current_address should follow the chain
        original = _addr(("section", "5"))
        assert ledger.current_address(original) == _addr(("section", "5b"))
