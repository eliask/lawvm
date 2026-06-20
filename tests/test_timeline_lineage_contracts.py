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
