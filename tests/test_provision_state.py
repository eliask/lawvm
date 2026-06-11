from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import MigrationEvent, OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.provision_state import resolve_provision_state
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
            raw_text="Section 1 is replaced with a new duty.",
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
    assert payload["source_locator_status"] == "canonical_document_locator"
    assert payload["source_locator"]["artifact_kind"] == "operation_source_statute_xml"
    assert payload["source_locator"]["document_uri"] == "finlex://sd/2019/1/fin/main.xml"
    assert payload["source_locator"]["structural_path"] == "lawvm-target:chapter:1/section:1"
    assert payload["source_locator"]["detail"]["document_locator_status"] == "canonical_finlex_document_uri"
    assert payload["source_locator"]["detail"]["precision"] == "document_plus_resolved_target_legal_address"
    assert payload["source_locator"]["detail"]["target_legal_address_kind"] == "lawvm_resolved_target"
    assert payload["source_locator"]["detail"]["target_address_authority"] == (
        "resolved_replay_timeline_address"
    )
    assert payload["source_locator"]["detail"]["xpath_status"] == "unavailable_initial_surface"
    assert payload["source_locator"]["detail"]["byte_span_status"] == "unavailable_initial_surface"
    assert payload["source_locator"]["detail"]["hash_role"] == "excluded_from_derived_state_hash"
    assert payload["source_locator"]["quote_hash"]
    assert payload["source_locator"]["detail"]["source_witness_status"] == (
        "operation_source_raw_text_available"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["kind"] == "operation_source_raw_text"
    assert payload["source_locator"]["detail"]["source_witness"]["quote"] == (
        "Section 1 is replaced with a new duty."
    )
    assert payload["source_locator"]["detail"]["source_witness"]["quote_truncated"] is False
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


def test_provision_state_response_uses_base_source_locator_for_sourceless_base_version() -> None:
    address = LegalAddress(path=(("section", "1"),))
    content = _section("Base duty.")
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    content=content,
                    content_hash=irnode_content_hash(content),
                )
            ],
        )
    }

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )

    assert payload["source"] is None
    assert payload["source_locator_status"] == "canonical_document_locator"
    assert payload["source_locator"]["artifact_kind"] == "base_statute_xml"
    assert payload["source_locator"]["document_uri"] == "finlex://sd/2000/1/fin/main.xml"
    assert payload["source_locator"]["structural_path"] == "lawvm-target:section:1"
    assert payload["source_locator"]["detail"]["source_witness_status"] == (
        "unavailable_no_operation_source_raw_text"
    )
    assert "source_witness" not in payload["source_locator"]["detail"]


def test_public_resolve_provision_state_reports_unsupported_jurisdiction_without_replay() -> None:
    payload = resolve_provision_state(
        statute_id="ukpga/2000/1",
        jurisdiction="uk",
        provision="section:1",
        as_of="2024-01-01",
    )

    assert payload["schema"] == "lawvm.provision_state.v1"
    assert payload["status"] == "unsupported_jurisdiction"
    assert payload["supported_jurisdictions"] == ["fi"]


# --- timeline-integrity surfacing ---------------------------------------------
# A replay abort / broken compile fold must never produce clean-looking answers.
# Break evidence is classified by lawvm.tools.timeline_integrity; the seam marks
# affected responses with status="timeline_unverified" plus a typed
# `timeline_broken_at` marker. Responses WITHOUT break evidence stay
# byte-identical (conditional hash member, same discipline as `expiry`).

from lawvm.core.phase_result import Finding
from lawvm.tools.timeline_integrity import (
    TimelineBreak,
    attach_effective_dates,
    timeline_breaks_from_findings,
)


def _occupancy_finding(*, non_primary: bool = False) -> Finding:
    detail: dict[str, object] = {
        "target_label": "29e",
        "current_occupancy": "absent",
        "allowed_from": ["substantive", "tombstone"],
    }
    if non_primary:
        detail["allowed_non_primary"] = True
    return Finding(
        kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
        role="observation",
        stage="apply",
        source_statute="2025/1382",
        detail=detail,
        blocking=False,
    )


def _statute_break(effective: str = "2020-06-01") -> TimelineBreak:
    return TimelineBreak(
        amendment_id="2019/500",
        diagnostic_code="APPLY.OCCUPANCY_POLICY_VIOLATION",
        scope="statute",
        target_unit_kind="section",
        target_section="9",
        effective=effective,
    )


def test_timeline_break_classifier_occupancy_violation_is_statute_scoped() -> None:
    breaks = timeline_breaks_from_findings([_occupancy_finding()])
    assert len(breaks) == 1
    assert breaks[0].scope == "statute"
    assert breaks[0].amendment_id == "2025/1382"
    assert breaks[0].diagnostic_code == "APPLY.OCCUPANCY_POLICY_VIOLATION"
    assert breaks[0].target_section == "29e"


def test_timeline_break_classifier_skips_allowed_non_primary_occupancy_note() -> None:
    assert timeline_breaks_from_findings([_occupancy_finding(non_primary=True)]) == ()


def test_timeline_break_classifier_failed_operation_is_address_scoped() -> None:
    finding = Finding(
        kind="APPLY.FAILED_OPERATION",
        role="obligation",
        stage="process_muutoslaki",
        source_statute="",
        detail={
            "amendment_id": "2022/378",
            "reason_code": "section_not_found",
            "target_unit_kind": "section",
            "target_section": "16",
            "target_chapter": None,
        },
        blocking=True,
    )
    breaks = timeline_breaks_from_findings([finding])
    assert len(breaks) == 1
    assert breaks[0].scope == "address"
    assert breaks[0].amendment_id == "2022/378"
    assert breaks[0].target_section == "16"
    assert breaks[0].reason == "section_not_found"


def test_timeline_break_classifier_timeline_fatal_detail_hook_is_statute_scoped() -> None:
    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="apply",
        source_statute="2024/9",
        detail={"timeline_fatal": True},
        blocking=False,
    )
    breaks = timeline_breaks_from_findings([finding])
    assert len(breaks) == 1
    assert breaks[0].scope == "statute"
    assert breaks[0].amendment_id == "2024/9"


def test_timeline_break_effective_dates_attach_from_lineage() -> None:
    breaks = attach_effective_dates(
        timeline_breaks_from_findings([_occupancy_finding()]),
        [{"statute_id": "2025/1382", "effective_date": "2026-01-01"}],
    )
    assert breaks[0].effective == "2026-01-01"


def test_provision_state_without_breaks_is_byte_identical() -> None:
    base = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    explicit_empty = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(),
    )
    assert base == explicit_empty
    assert "timeline_broken_at" not in base
    assert "timeline_integrity" not in base


def test_statute_scoped_break_blocks_post_break_query() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    assert payload["status"] == "timeline_unverified"
    assert payload["timeline_broken_at"] == {
        "amendment_id": "2019/500",
        "diagnostic_code": "APPLY.OCCUPANCY_POLICY_VIOLATION",
    }
    assert payload["timeline_integrity"]["blocking"] is True
    # Content must be withheld: an unproven timeline cannot assert text-state.
    assert payload["version"] is None
    assert payload["text"]["available"] is False
    assert payload["hashes"]["content_hash"] == ""
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    assert payload["hashes"]["derived_state_hash"] != clean["hashes"]["derived_state_hash"]


def test_statute_scoped_break_after_as_of_serves_with_warning_marker() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2022-01-01"),),
    )
    # The pre-break timeline is proven: serve the answer, keep the marker visible.
    assert payload["status"] == "selected"
    assert payload["text"]["available"] is True
    assert payload["timeline_broken_at"]["amendment_id"] == "2019/500"
    assert payload["timeline_integrity"]["blocking"] is False
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    # The warning is part of the hashed state (a consumer pin must notice it).
    assert payload["hashes"]["derived_state_hash"] != clean["hashes"]["derived_state_hash"]


def test_undated_break_is_conservatively_blocking() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break(""),),
    )
    assert payload["status"] == "timeline_unverified"
    assert payload["timeline_integrity"]["blocking"] is True


def test_address_scoped_break_only_affects_matching_target() -> None:
    failed_op_break = TimelineBreak(
        amendment_id="2022/378",
        diagnostic_code="APPLY.FAILED_OPERATION",
        scope="address",
        target_unit_kind="section",
        target_section="1",
        effective="2020-06-01",
        reason="section_not_found",
    )
    matching = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(failed_op_break,),
    )
    assert matching["status"] == "timeline_unverified"
    assert matching["timeline_broken_at"]["amendment_id"] == "2022/378"

    other_target = TimelineBreak(
        amendment_id="2022/378",
        diagnostic_code="APPLY.FAILED_OPERATION",
        scope="address",
        target_unit_kind="section",
        target_section="99",
        effective="2020-06-01",
        reason="section_not_found",
    )
    unaffected = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(other_target,),
    )
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    # Non-matching targets must leave the response byte-identical.
    assert unaffected == clean


def test_blocking_break_overrides_unresolved_address_status() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:404",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    # "address_not_found" would read as a legal fact ("no such provision"), but
    # the breaking amendment could have created the address: block instead.
    assert payload["status"] == "timeline_unverified"
    assert payload["timeline_integrity"]["resolution_status"] == "address_not_found"
    assert payload["timeline_broken_at"]["amendment_id"] == "2019/500"


def test_timeline_integrity_flag_off_restores_prior_behavior(monkeypatch) -> None:
    monkeypatch.setenv("LAWVM_ENABLE_TIMELINE_INTEGRITY_SURFACING", "0")
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    assert payload["status"] == "selected"
    assert "timeline_broken_at" not in payload


# --- live-corpus specimen regression -------------------------------------------
# Consumer-reported specimen: 2014/1429 replay records an occupancy violation at
# 2025/1382 (timeline break), yet the seam used to serve clean-looking answers
# (status=selected / address_not_found with empty fields). The assertion is
# CONSISTENCY between recorded break evidence and the surfaced marker, so this
# test stays green if/when the underlying replay break is fixed: with evidence
# the response must be marked; without evidence it must be clean.

from pathlib import Path

import pytest

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2014_1429_broken_timeline_is_surfaced_not_clean() -> None:
    from lawvm.finland.grafter import replay_xml
    from lawvm.tools.timeline_integrity import (
        break_governs_as_of,
        sorted_breaks,
    )

    as_of = "2026-06-11"
    replay_meta: dict = {}
    master = replay_xml("2014/1429", quiet=True, replay_meta_out=replay_meta)
    breaks = sorted_breaks(
        attach_effective_dates(
            timeline_breaks_from_findings(master.findings),
            replay_meta.get("lineage") or (),
        )
    )
    governing_statute_breaks = tuple(
        item
        for item in breaks
        if item.scope == "statute" and break_governs_as_of(item, as_of)
    )

    payload = build_provision_state_response(
        timelines=master.timelines,
        statute_id="2014/1429",
        jurisdiction="fi",
        provision="section:30",
        as_of=as_of,
        title=master.title,
        timeline_breaks=breaks,
    )

    if governing_statute_breaks:
        # Broken timeline: the answer must be typed-unprovable, never a clean
        # legal fact. (Live as of 2026-06-11: break at 2025/1382.)
        assert payload["status"] == "timeline_unverified"
        assert payload["timeline_broken_at"]["amendment_id"] == (
            governing_statute_breaks[0].amendment_id
        )
        assert payload["timeline_integrity"]["blocking"] is True
        assert payload["version"] is None
    else:
        # Replay break fixed upstream: the seam must serve cleanly again.
        assert payload["status"] in ("selected", "absent")
        assert "timeline_broken_at" not in payload or (
            payload["timeline_integrity"]["blocking"] is False
        )
