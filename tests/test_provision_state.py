from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import MigrationEvent, OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.provision_state import build_provision_state_response, resolve_address


def _section(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="1", text=text)


def _timeline(*, expires: str = "") -> dict[LegalAddress, ProvisionTimeline]:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = _section("A provision duty.")
    version = ProvisionVersion(
        effective="2020-01-01",
        enacted="2019-12-01",
        expires=expires,
        content=content,
        source=OperationSource(
            statute_id="2019/1",
            title="Amending Act",
            enacted="2019-12-01",
            effective="2020-01-01",
        ),
        content_hash=irnode_content_hash(content),
    )
    return {address: ProvisionTimeline(address=address, versions=[version])}


def test_provision_state_response_exposes_text_hash_and_temporal_pin() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )

    assert payload["schema"] == "lawvm.provision_state.v1"
    assert payload["status"] == "selected"
    assert payload["resolved_address"]["text"] == "chapter:1/section:1"
    assert payload["address_match"]["mode"] == "unique_suffix"
    assert payload["text"]["rendered"] == "A provision duty."
    assert payload["hashes"]["content_hash"] == irnode_content_hash(_section("A provision duty."))
    assert len(payload["hashes"]["derived_state_hash"]) == 64
    assert payload["version"]["effective"] == "2020-01-01"
    assert payload["version"]["enacted"] == "2019-12-01"
    assert payload["source"]["statute_id"] == "2019/1"
    assert payload["lineage"]["status"] == "self_only"
    assert payload["lineage"]["address_chain"] == [payload["resolved_address"]]
    assert payload["engine"]["producer"] == "lawvm"
    assert payload["engine"]["interface"] == "lawvm provision-state"
    assert {"build_id", "git_commit", "git_dirty", "repository"} <= set(payload["engine"])


def test_derived_state_hash_changes_when_temporal_metadata_changes_without_text_change() -> None:
    without_expiry = build_provision_state_response(
        timelines=_timeline(expires=""),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    with_expiry = build_provision_state_response(
        timelines=_timeline(expires="2025-01-01"),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )

    assert without_expiry["hashes"]["content_hash"] == with_expiry["hashes"]["content_hash"]
    assert without_expiry["hashes"]["derived_state_hash"] != with_expiry["hashes"]["derived_state_hash"]


def test_address_resolution_reports_ambiguous_suffix_without_order_dependent_choice() -> None:
    first = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    second = LegalAddress(path=(("chapter", "2"), ("section", "1")))
    timelines = {
        first: ProvisionTimeline(address=first),
        second: ProvisionTimeline(address=second),
    }

    resolution = resolve_address(timelines, "section:1")

    assert resolution.status == "ambiguous_address"
    assert resolution.address is None
    assert tuple(str(candidate) for candidate in resolution.candidates) == (
        "chapter:1/section:1",
        "chapter:2/section:1",
    )


def test_provision_state_response_exposes_lineage_chain_from_migration_events() -> None:
    migration = MigrationEvent(
        event_id="renumber-1",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
        to_address=LegalAddress(path=(("chapter", "1"), ("section", "2"))),
        effective="2020-06-01",
        source_statute="2020/2",
    )

    payload = build_provision_state_response(
        timelines=_timeline(),
        migration_events=(migration,),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )

    assert payload["lineage"]["status"] == "migration_chain"
    assert [entry["text"] for entry in payload["lineage"]["address_chain"]] == [
        "chapter:1/section:1",
        "chapter:1/section:2",
    ]
    assert payload["lineage"]["migration_event_count_considered"] == 1
