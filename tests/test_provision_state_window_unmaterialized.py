"""Temporary-twin window scheduler and fallback guard.

Finnish twin laws can insert the same section twice: a permanent law with a
deferred commencement and a temporary gap-filler for the interim window. Stage 0
classified the apply observation as ``TEMPORAL.WINDOW_UNMATERIALIZED`` and
blocked in-window reads. Stage 1 keeps that as the fallback when proof is
missing, but materializes a temporary ``ProvisionVersion`` when the compiled op,
window bounds, and deferred occupant agree exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal_scheduler import materialize_temporal_write_windows
from lawvm.finland.process_temporal_postprocessing import (
    _temporal_occupancy_reconciliation_findings,
)
from lawvm.core.ir import LegalOperation
from lawvm.tools.provision_state import build_provision_state_response
from lawvm.tools.timeline_integrity import (
    WINDOW_UNMATERIALIZED_CODE,
    TimelineBreak,
    attach_effective_dates,
    break_governs_as_of,
    timeline_breaks_from_findings,
)


def _timeline() -> dict[LegalAddress, ProvisionTimeline]:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text="A provision duty.")
    version = ProvisionVersion(
        effective="2020-01-01",
        enacted="2019-12-01",
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


def _timeline_with_deferred_occupant() -> dict[LegalAddress, ProvisionTimeline]:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text="Permanent provision duty.")
    version = ProvisionVersion(
        effective="2021-07-01",
        enacted="2020-12-01",
        content=content,
        source=OperationSource(
            statute_id="2020/901",
            title="Permanent Amending Act",
            enacted="2020-12-01",
            effective="2021-07-01",
            raw_text="Section 1 is inserted permanently.",
        ),
        content_hash=irnode_content_hash(content),
    )
    return {address: ProvisionTimeline(address=address, versions=[version])}


def _temporary_window_op() -> LegalOperation:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text="Temporary provision duty.")
    return LegalOperation(
        op_id="op_0",
        sequence=12,
        action=StructuralAction.INSERT,
        target=address,
        payload=content,
        source=OperationSource(
            statute_id="2020/900",
            title="Temporary Amending Act",
            enacted="2020-12-01",
            effective="2021-01-01",
            expires="2021-07-01",
            raw_text="Section 1 is temporarily inserted.",
        ),
    )


def _permanent_occupant_op(
    *,
    source_statute: str = "2020/901",
    effective: str = "2021-01-01",
) -> LegalOperation:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text="Permanent provision duty.")
    return LegalOperation(
        op_id="op_perm",
        sequence=11,
        action=StructuralAction.INSERT,
        target=address,
        payload=content,
        source=OperationSource(
            statute_id=source_statute,
            title="Permanent Amending Act",
            enacted="2020-12-01",
            effective=effective,
            raw_text="Section 1 is inserted permanently.",
        ),
    )


def _disjoint_finding(
    *,
    target_label: str = "1",
    incoming_effective: str = "2021-01-01",
    incoming_expires: str = "2021-07-01",
    source_statute: str = "2020/900",
    target_chapter: str = "",
    occupant_effective: str = "2021-07-01",
    occupant_source_statute: str = "2020/901",
    reconciles: bool = False,
) -> Finding:
    detail = {
        "ctx_label": f"[{source_statute}] INSERT {target_label} §",
        "op_id": "op_0",
        "legacy_action": "INSERT",
        "target_label": target_label,
        "target_chapter": target_chapter,
        "incoming_effective": incoming_effective,
        "incoming_expires": incoming_expires,
        "occupant_effective": occupant_effective,
        "occupant_source_statute": occupant_source_statute,
        "rule_id": "temporally_disjoint_twin_insert",
    }
    if reconciles:
        detail["reconciles_finding"] = "APPLY.OCCUPANCY_POLICY_VIOLATION"
    return Finding(
        kind="APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT",
        role="observation",
        stage="apply",
        source_statute=source_statute,
        detail=detail,
        blocking=False,
    )


def _occupancy_violation_finding(
    *,
    target_label: str = "1",
    source_statute: str = "2020/900",
) -> Finding:
    return Finding(
        kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
        role="observation",
        stage="apply",
        source_statute=source_statute,
        detail={
            "ctx_label": f"[{source_statute}] INSERT 1 luku {target_label} §",
            "legacy_action": "INSERT",
            "target_label": target_label,
            "target_chapter": "1",
            "current_occupancy": "substantive",
            "allowed_from": ("absent", "scaffold", "tombstone"),
            "primary_expected_from": ("absent",),
            "strict_disposition": "record",
        },
        blocking=False,
    )


def _window_break() -> TimelineBreak:
    (item,) = timeline_breaks_from_findings([_disjoint_finding()])
    return item


# --- classifier ------------------------------------------------------------


def test_disjoint_insert_is_classified_window_scoped_and_self_evidencing() -> None:
    breaks = timeline_breaks_from_findings([_disjoint_finding()])
    assert len(breaks) == 1
    item = breaks[0]
    assert item.scope == "window"
    assert item.diagnostic_code == WINDOW_UNMATERIALIZED_CODE
    # The temporary act whose window is unmaterialized.
    assert item.amendment_id == "2020/900"
    assert item.target_section == "1"
    assert item.window_start == "2021-01-01"
    assert item.window_end == "2021-07-01"
    assert item.occupant_source_statute == "2020/901"
    assert item.occupant_effective == "2021-07-01"
    assert item.rule_id == "temporally_disjoint_twin_insert"
    # Wire form carries the full window so a consumer needs no source access.
    wire = item.to_wire()
    assert wire["window"] == {
        "start": "2021-01-01",
        "end": "2021-07-01",
        "bounds": "start_inclusive_end_exclusive",
        "source_statute": "2020/900",
        "occupant_source_statute": "2020/901",
        "occupant_effective": "2021-07-01",
        "rule_id": "temporally_disjoint_twin_insert",
    }


def test_window_break_governs_only_inside_half_open_interval() -> None:
    item = _window_break()
    assert break_governs_as_of(item, "2020-12-31") is False  # day before
    assert break_governs_as_of(item, "2021-01-01") is True  # start (inclusive)
    assert break_governs_as_of(item, "2021-03-15") is True  # interior
    assert break_governs_as_of(item, "2021-06-30") is True  # last in-force day
    assert break_governs_as_of(item, "2021-07-01") is False  # exclusive cutoff = twin commencement


def test_window_break_with_missing_bound_is_conservatively_governing() -> None:
    (item,) = timeline_breaks_from_findings(
        [_disjoint_finding(incoming_expires="")]
    )
    assert break_governs_as_of(item, "2099-01-01") is True


def test_attach_effective_dates_preserves_window_start() -> None:
    breaks = attach_effective_dates(
        timeline_breaks_from_findings([_disjoint_finding()]),
        [{"statute_id": "2020/900", "effective_date": "9999-12-31"}],
    )
    # The window start is already seeded as effective; lineage must not stomp it.
    assert breaks[0].effective == "2021-01-01"


def test_reconciled_window_finding_consumes_matching_occupancy_break() -> None:
    breaks = timeline_breaks_from_findings(
        [
            _occupancy_violation_finding(),
            _disjoint_finding(target_chapter="1", reconciles=True),
        ]
    )
    assert [item.scope for item in breaks] == ["window"]
    assert breaks[0].diagnostic_code == WINDOW_UNMATERIALIZED_CODE


def test_post_temporal_reconciliation_emits_bounded_window_observation() -> None:
    findings = [_occupancy_violation_finding()]
    notes = _temporal_occupancy_reconciliation_findings(
        [_permanent_occupant_op(), _temporary_window_op()],
        findings,
        amendment_id="2020/900",
    )
    assert len(notes) == 1
    assert notes[0].kind == "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT"
    assert notes[0].detail["target_label"] == "1"
    assert notes[0].detail["incoming_effective"] == "2021-01-01"
    assert notes[0].detail["incoming_expires"] == "2021-07-01"
    assert notes[0].detail["occupant_source_statute"] == "2020/901"
    assert notes[0].detail["rule_id"] == "temporally_bounded_overlay_insert"
    assert notes[0].detail["reconciles_finding"] == "APPLY.OCCUPANCY_POLICY_VIOLATION"


# --- seam blocking ---------------------------------------------------------


def test_in_window_query_on_target_address_is_blocked() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-03-15",
        timeline_breaks=(_window_break(),),
    )
    assert payload["provision_status"] == "timeline_unverified"
    assert payload["timeline_broken_at"] == {
        "amendment_id": "2020/900",
        "diagnostic_code": WINDOW_UNMATERIALIZED_CODE,
    }
    block = payload["timeline_integrity"]
    assert block["blocking"] is True
    assert block["broken_at"]["window"]["start"] == "2021-01-01"
    assert block["broken_at"]["window"]["end"] == "2021-07-01"
    # Content withheld: neither presence nor absence is asserted in the window.
    assert payload["version"] is None
    assert payload["text"]["available"] is False
    assert payload["hashes"]["content_hash"] == ""


def test_temporal_scheduler_materializes_proved_window() -> None:
    scheduled = materialize_temporal_write_windows(
        _timeline_with_deferred_occupant(),
        [_temporary_window_op()],
        [_window_break()],
    )
    assert scheduled.unresolved_breaks == ()
    assert len(scheduled.deltas) == 1

    payload = build_provision_state_response(
        timelines=scheduled.timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-03-15",
        timeline_breaks=scheduled.unresolved_breaks,
        temporal_schedule_deltas=scheduled.deltas,
    )
    assert payload["provision_status"] == "selected"
    assert payload["version"]["variant_kind"] == "temporary"
    assert payload["text"]["rendered"] == "Temporary provision duty."
    assert "timeline_integrity" not in payload
    assert payload["temporal_schedule"]["scheduler"] == "temporal_write_interval_stage_1"
    assert payload["temporal_schedule"]["deltas"][0]["interval"]["source_work_id"] == "2020/900"


def test_temporal_scheduler_keeps_break_when_payload_is_missing() -> None:
    scheduled = materialize_temporal_write_windows(
        _timeline_with_deferred_occupant(),
        [],
        [_window_break()],
    )
    assert scheduled.deltas == ()
    assert scheduled.unresolved_breaks == (_window_break(),)


def test_window_blocks_start_day_and_last_in_force_day() -> None:
    for as_of in ("2021-01-01", "2021-06-30"):
        payload = build_provision_state_response(
            timelines=_timeline(),
            statute_id="2000/1",
            jurisdiction="fi",
            provision="chapter:1/section:1",
            as_of=as_of,
            timeline_breaks=(_window_break(),),
        )
        assert payload["provision_status"] == "timeline_unverified", as_of


def test_outside_window_is_byte_identical_to_no_break_baseline() -> None:
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-07-01",  # exclusive cutoff: twin commencement day, not blocked
    )
    # A window break is a localized claim: outside the window it must not even
    # leave a (non-blocking) warning marker — unlike statute/address breaks.
    for as_of in ("2020-12-31", "2021-07-01", "2030-01-01"):
        with_break = build_provision_state_response(
            timelines=_timeline(),
            statute_id="2000/1",
            jurisdiction="fi",
            provision="chapter:1/section:1",
            as_of=as_of,
            timeline_breaks=(_window_break(),),
        )
        baseline = build_provision_state_response(
            timelines=_timeline(),
            statute_id="2000/1",
            jurisdiction="fi",
            provision="chapter:1/section:1",
            as_of=as_of,
        )
        assert with_break == baseline, as_of
        assert "timeline_integrity" not in with_break, as_of
    # sanity: the clean baseline is itself a normal selected/absent answer
    assert "timeline_integrity" not in clean


def test_window_break_does_not_affect_other_address() -> None:
    # Window targets section "1"; query a different section -> byte-identical.
    other_break = timeline_breaks_from_findings(
        [_disjoint_finding(target_label="99")]
    )
    with_break = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-03-15",  # inside the window, but wrong address
        timeline_breaks=tuple(other_break),
    )
    baseline = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-03-15",
    )
    assert with_break == baseline


def test_window_break_flag_off_restores_prior_behavior(monkeypatch) -> None:
    monkeypatch.setenv("LAWVM_ENABLE_TIMELINE_INTEGRITY_SURFACING", "0")
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-03-15",
        timeline_breaks=(_window_break(),),
    )
    assert payload["provision_status"] == "selected"
    assert "timeline_broken_at" not in payload


# --- live-corpus canary ----------------------------------------------------

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


@pytest.fixture(scope="module")
def live_2010_1326_runtime():
    from lawvm.provision_state import compile_provision_state_runtime

    return compile_provision_state_runtime(statute_id="2010/1326")


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.parametrize(
    "provision,as_of,window_source",
    [
        ("section:78c", "2023-03-01", "2022/1282"),
        ("section:51a", "2024-01-01", "2023/117"),
        ("section:51b", "2024-01-01", "2023/117"),
    ],
)
def test_live_twin_window_query_is_materialized(
    live_2010_1326_runtime,
    provision,
    as_of,
    window_source,
) -> None:
    payload = live_2010_1326_runtime.resolve(
        provision=provision,
        as_of=as_of,
        query_type="in_force",
    )
    assert payload["provision_status"] == "selected", (provision, as_of)
    assert payload["version"]["variant_kind"] == "temporary"
    assert "timeline_integrity" not in payload
    schedule = payload["temporal_schedule"]
    assert schedule["scheduler"] == "temporal_write_interval_stage_1"
    assert schedule["deltas"][0]["diagnostic_code"] == WINDOW_UNMATERIALIZED_CODE
    assert schedule["deltas"][0]["interval"]["source_work_id"] == window_source


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_live_78c_twin_window_boundaries(live_2010_1326_runtime) -> None:
    cases = [
        ("2022-12-31", "absent", None, None, False),
        ("2023-01-01", "selected", "temporary", "2022/1282", True),
        ("2023-06-30", "selected", "temporary", "2022/1282", True),
        ("2023-07-01", "selected", "permanent", "2022/1281", False),
    ]
    for as_of, status, variant_kind, source_statute, has_schedule in cases:
        payload = live_2010_1326_runtime.resolve(
            provision="section:78c",
            as_of=as_of,
            query_type="in_force",
        )
        assert payload["provision_status"] == status, as_of
        if variant_kind is None:
            assert payload["version"] is None
            assert payload["source"] is None
        else:
            assert payload["version"]["variant_kind"] == variant_kind, as_of
            assert payload["source"]["statute_id"] == source_statute, as_of
        assert ("temporal_schedule" in payload) is has_schedule, as_of
        assert "timeline_integrity" not in payload, as_of


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_live_twin_window_is_scoped_off_window_and_off_address(live_2010_1326_runtime) -> None:
    # current date: well outside every window -> not blocked
    after = live_2010_1326_runtime.resolve(
        provision="section:78c",
        as_of="2026-06-11",
        query_type="in_force",
    )
    assert after["provision_status"] != "timeline_unverified"
    assert "timeline_integrity" not in after

    # an unaffected address of the same statute -> not blocked
    other = live_2010_1326_runtime.resolve(
        provision="section:22",
        as_of="2023-03-01",
        query_type="in_force",
    )
    assert other["provision_status"] != "timeline_unverified"
    assert "timeline_integrity" not in other
