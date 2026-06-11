"""Window-unmaterialized seam guard (TEMPORAL.WINDOW_UNMATERIALIZED).

Interim fail-loud guard for known-but-unmaterialized temporary-twin windows.
A Finnish twin-law split inserts the SAME section twice: a permanent law with a
deferred-commencement and a temporary gap-filler for the gap window. The
document-order compile fold never materializes the temporary twin's text inside
its own window (the deferred twin holds the slot), so an in-window PIT query
would otherwise serve silently-wrong text. The apply layer records this as a
non-blocking ``APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT`` finding; the seam
classifies it into a ``scope="window"`` TimelineBreak and blocks queries that
land on the affected address INSIDE the closed window with status
``timeline_unverified``.

This guard is expected to be SHORT-LIVED (replaced by real window
materialization); consumers must not build logic on its permanence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
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


def _disjoint_finding(
    *,
    target_label: str = "1",
    incoming_effective: str = "2021-01-01",
    incoming_expires: str = "2021-06-30",
    source_statute: str = "2020/900",
    occupant_effective: str = "2021-07-01",
    occupant_source_statute: str = "2020/901",
) -> Finding:
    return Finding(
        kind="APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT",
        role="observation",
        stage="apply",
        source_statute=source_statute,
        detail={
            "ctx_label": f"[{source_statute}] INSERT {target_label} §",
            "op_id": "op_0",
            "legacy_action": "INSERT",
            "target_label": target_label,
            "incoming_effective": incoming_effective,
            "incoming_expires": incoming_expires,
            "occupant_effective": occupant_effective,
            "occupant_source_statute": occupant_source_statute,
            "rule_id": "temporally_disjoint_twin_insert",
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
    assert item.window_end == "2021-06-30"
    assert item.occupant_source_statute == "2020/901"
    assert item.occupant_effective == "2021-07-01"
    assert item.rule_id == "temporally_disjoint_twin_insert"
    # Wire form carries the full window so a consumer needs no source access.
    wire = item.to_wire()
    assert wire["window"] == {
        "start": "2021-01-01",
        "end": "2021-06-30",
        "bounds": "inclusive",
        "source_statute": "2020/900",
        "occupant_source_statute": "2020/901",
        "occupant_effective": "2021-07-01",
        "rule_id": "temporally_disjoint_twin_insert",
    }


def test_window_break_governs_only_inside_closed_interval() -> None:
    item = _window_break()
    assert break_governs_as_of(item, "2020-12-31") is False  # day before
    assert break_governs_as_of(item, "2021-01-01") is True  # start (inclusive)
    assert break_governs_as_of(item, "2021-03-15") is True  # interior
    assert break_governs_as_of(item, "2021-06-30") is True  # end (inclusive)
    assert break_governs_as_of(item, "2021-07-01") is False  # day after


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
    assert payload["status"] == "timeline_unverified"
    assert payload["timeline_broken_at"] == {
        "amendment_id": "2020/900",
        "diagnostic_code": WINDOW_UNMATERIALIZED_CODE,
    }
    block = payload["timeline_integrity"]
    assert block["blocking"] is True
    assert block["broken_at"]["window"]["start"] == "2021-01-01"
    assert block["broken_at"]["window"]["end"] == "2021-06-30"
    # Content withheld: neither presence nor absence is asserted in the window.
    assert payload["version"] is None
    assert payload["text"]["available"] is False
    assert payload["hashes"]["content_hash"] == ""


def test_window_boundaries_are_inclusive_both_ends() -> None:
    for as_of in ("2021-01-01", "2021-06-30"):
        payload = build_provision_state_response(
            timelines=_timeline(),
            statute_id="2000/1",
            jurisdiction="fi",
            provision="chapter:1/section:1",
            as_of=as_of,
            timeline_breaks=(_window_break(),),
        )
        assert payload["status"] == "timeline_unverified", as_of


def test_outside_window_is_byte_identical_to_no_break_baseline() -> None:
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-07-01",  # day after window end
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
    assert payload["status"] == "selected"
    assert "timeline_broken_at" not in payload


# --- live-corpus canary ----------------------------------------------------

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.parametrize(
    "provision,as_of,window_source",
    [
        ("section:78c", "2023-03-01", "2022/1282"),
        ("section:51a", "2024-01-01", "2023/117"),
        ("section:51b", "2024-01-01", "2023/117"),
    ],
)
def test_live_twin_window_query_is_blocked(provision, as_of, window_source) -> None:
    from lawvm.provision_state import resolve_provision_state

    payload = resolve_provision_state(
        statute_id="2010/1326",
        provision=provision,
        as_of=as_of,
        query_type="in_force",
    )
    assert payload["status"] == "timeline_unverified", (provision, as_of)
    assert payload["timeline_broken_at"]["diagnostic_code"] == WINDOW_UNMATERIALIZED_CODE
    assert payload["timeline_broken_at"]["amendment_id"] == window_source
    block = payload["timeline_integrity"]
    assert block["blocking"] is True
    assert block["broken_at"]["scope"] == "window"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_live_twin_window_is_scoped_off_window_and_off_address() -> None:
    from lawvm.provision_state import resolve_provision_state

    # current date: well outside every window -> not blocked
    after = resolve_provision_state(
        statute_id="2010/1326",
        provision="section:78c",
        as_of="2026-06-11",
        query_type="in_force",
    )
    assert after["status"] != "timeline_unverified"
    assert "timeline_integrity" not in after

    # an unaffected address of the same statute -> not blocked
    other = resolve_provision_state(
        statute_id="2010/1326",
        provision="section:22",
        as_of="2023-03-01",
        query_type="in_force",
    )
    assert other["status"] != "timeline_unverified"
    assert "timeline_integrity" not in other
