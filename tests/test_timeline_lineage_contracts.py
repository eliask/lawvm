from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_lineage import (
    LineageSegment,
    MaterializationLineageBridgeClassification,
    PrefixMigrationEventSignature,
    ScopeMigrationClassification,
    current_address_with_prefix_migrations_from_event_signatures,
    rekey_timelines_with_migration_events,
)


def _address(label: str = "1") -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _migration_event() -> MigrationEvent:
    return MigrationEvent(
        event_id="mig:test:1",
        kind="renumber",
        from_address=_address("1"),
        to_address=_address("2"),
        effective="2024-01-01",
    )


def test_lineage_segment_accepts_typed_addresses_and_event() -> None:
    segment = LineageSegment(
        from_address=_address("1"),
        to_address=_address("2"),
        event=_migration_event(),
    )

    assert segment.to_address == _address("2")


def test_lineage_segment_rejects_string_addresses() -> None:
    with pytest.raises(ValueError, match="from_address"):
        LineageSegment(
            from_address=cast(Any, "section:1"),
            to_address=_address("2"),
        )


def test_lineage_segment_rejects_untyped_event() -> None:
    with pytest.raises(ValueError, match="event"):
        LineageSegment(
            from_address=_address("1"),
            to_address=_address("2"),
            event=cast(Any, object()),
        )


def test_scope_migration_classification_rejects_non_boolean_flags() -> None:
    with pytest.raises(ValueError, match="noncolliding"):
        ScopeMigrationClassification(
            active_scope_changing=True,
            noncolliding=cast(Any, "yes"),
            destination_occupancy_collision=False,
        )


def test_materialization_lineage_bridge_classification_rejects_non_boolean_flags() -> None:
    with pytest.raises(ValueError, match="native_rebirth_after_renumber"):
        MaterializationLineageBridgeClassification(
            native_rebirth_after_renumber=cast(Any, "true"),
        )


def test_rekey_timelines_uses_precomputed_prefix_migration_signatures() -> None:
    source = _address("1")
    target = _address("2")
    event = MigrationEvent(
        event_id="mig:test:signature",
        kind="renumber",
        from_address=source,
        to_address=target,
        effective="2020-01-01",
    )
    timeline = ProvisionTimeline(
        address=source,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="one"),
            )
        ],
    )
    signature_calls = 0

    def event_resolver_should_not_run(
        _address: LegalAddress,
        _events: tuple[MigrationEvent, ...],
        _as_of_date: str,
    ) -> LegalAddress:
        raise AssertionError("rekey should use the precomputed signature resolver")

    def signature_resolver(
        address: LegalAddress,
        event_signatures: tuple[PrefixMigrationEventSignature, ...],
        as_of_date: str,
        not_before: str,
    ) -> LegalAddress:
        nonlocal signature_calls
        signature_calls += 1
        return current_address_with_prefix_migrations_from_event_signatures(
            address,
            event_signatures,
            as_of_date=as_of_date,
            not_before=not_before,
        )

    rekeyed = rekey_timelines_with_migration_events(
        {source: timeline},
        (event,),
        as_of_date="2021-01-01",
        current_address_with_prefix_migrations_fn=event_resolver_should_not_run,
        current_address_with_prefix_migration_signatures_fn=signature_resolver,
        address_prefix_matches=lambda address, prefix: address.has_path_prefix(prefix),
    )

    assert signature_calls == 1
    assert tuple(str(address) for address in rekeyed) == ("section:2",)


def test_rekey_timelines_uses_frontend_renumber_source_prefilter_for_native_split() -> None:
    address = _address("9")
    event = MigrationEvent(
        event_id="mig:test:unrelated",
        kind="renumber",
        from_address=_address("1"),
        to_address=_address("2"),
        effective="2020-01-01",
    )
    timeline = ProvisionTimeline(
        address=address,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="9", text="nine"),
            )
        ],
    )
    prefilter_calls = 0

    def identity_resolver(
        original: LegalAddress,
        _events: tuple[MigrationEvent, ...],
        _as_of_date: str,
    ) -> LegalAddress:
        return original

    def prefix_match_should_not_run(_address: LegalAddress, _prefix: LegalAddress) -> bool:
        raise AssertionError("native split should trust the frontend prefilter miss")

    def renumber_source_prefix_may_match(_address: LegalAddress) -> bool:
        nonlocal prefilter_calls
        prefilter_calls += 1
        return False

    rekeyed = rekey_timelines_with_migration_events(
        {address: timeline},
        (event,),
        as_of_date="2021-01-01",
        current_address_with_prefix_migrations_fn=identity_resolver,
        address_prefix_matches=prefix_match_should_not_run,
        renumber_source_prefix_may_match_fn=renumber_source_prefix_may_match,
    )

    assert prefilter_calls == 1
    assert tuple(rekeyed) == (address,)


# ---------------------------------------------------------------------------
# LS-11: lineage/migration DAG acyclicity
# ---------------------------------------------------------------------------


def _renumber_event(from_label: str, to_label: str, *, event_id: str = "") -> MigrationEvent:
    return MigrationEvent(
        event_id=event_id or f"mig:test:{from_label}->{to_label}",
        kind="renumber",
        from_address=_address(from_label),
        to_address=_address(to_label),
        effective="2024-01-01",
    )


def test_check_lineage_acyclic_empty_is_acyclic() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    result = check_lineage_acyclic(())
    assert result.acyclic is True
    assert result.cycle == ()


def test_check_lineage_acyclic_linear_chain_is_acyclic() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    events = (
        _renumber_event("1", "2"),
        _renumber_event("2", "3"),
        _renumber_event("3", "4"),
    )
    assert check_lineage_acyclic(events).acyclic is True


def test_check_lineage_acyclic_self_edge_is_not_a_cycle() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    # from == to is an identity no-op the resolvers skip, not a cycle.
    assert check_lineage_acyclic((_renumber_event("1", "1"),)).acyclic is True


def test_check_lineage_acyclic_detects_two_node_cycle() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    events = (
        _renumber_event("1", "2"),
        _renumber_event("2", "1"),
    )
    result = check_lineage_acyclic(events)
    assert result.acyclic is False
    # Cycle witness is a closed loop of addresses.
    assert result.cycle[0] == result.cycle[-1]
    assert {str(a) for a in result.cycle} == {str(_address("1")), str(_address("2"))}


def test_check_lineage_acyclic_detects_three_node_cycle() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    events = (
        _renumber_event("1", "2"),
        _renumber_event("2", "3"),
        _renumber_event("3", "1"),
    )
    result = check_lineage_acyclic(events)
    assert result.acyclic is False
    assert result.cycle[0] == result.cycle[-1]


def test_check_lineage_acyclic_is_deterministic() -> None:
    from lawvm.core.timeline_lineage import check_lineage_acyclic

    events = (
        _renumber_event("2", "1"),
        _renumber_event("1", "2"),
    )
    first = check_lineage_acyclic(events)
    second = check_lineage_acyclic(events)
    assert first.cycle == second.cycle


def test_assert_acyclic_passes_on_dag() -> None:
    from lawvm.core.timeline_lineage import assert_acyclic

    assert_acyclic((_renumber_event("1", "2"), _renumber_event("2", "3")))


def test_assert_acyclic_raises_lineage_cycle_error_on_cycle() -> None:
    from lawvm.core.timeline_lineage import LineageCycleError, assert_acyclic

    events = (
        _renumber_event("1", "2"),
        _renumber_event("2", "1"),
    )
    with pytest.raises(LineageCycleError, match="LINEAGE.CYCLE"):
        assert_acyclic(events)


def test_lineage_acyclicity_result_rejects_inconsistent_state() -> None:
    from lawvm.core.timeline_lineage import LineageAcyclicityResult

    with pytest.raises(ValueError):
        LineageAcyclicityResult(acyclic=True, cycle=(_address("1"),))
    with pytest.raises(ValueError):
        LineageAcyclicityResult(acyclic=False, cycle=())
