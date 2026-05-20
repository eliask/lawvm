from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.oracle_align import (
    UK_ORACLE_ALIGNMENT_RULE_ID,
    align_uk_replay_to_oracle_with_report,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline


def test_align_uk_replay_to_oracle_reports_eid_grounding() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="A section.",
                    attrs={"eId": "local-section-one"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="A subsection.",
                            attrs={"eId": "local-subsection-one"},
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    text="Wrapper",
                    attrs={"eId": "local-wrapper"},
                ),
            ),
        ),
    )

    result = align_uk_replay_to_oracle_with_report(
        statute,
        eid_map={"body:section-1": "section-1"},
        text_map={},
    )

    aligned_section = result.statute.body.children[0]
    assert aligned_section.attrs["eId"] == "section-1"
    aligned_subsection = aligned_section.children[0]
    assert aligned_subsection.attrs["eId"] == "section-1-1"
    aligned_wrapper = result.statute.body.children[1]
    assert "eId" not in aligned_wrapper.attrs
    assert result.report.rule_id == UK_ORACLE_ALIGNMENT_RULE_ID
    assert result.report.phase == "oracle_alignment"
    assert result.report.family == "oracle_alignment_adapter"
    assert result.report.before_node_count == 4
    assert result.report.after_node_count == 4
    assert result.report.node_count_mismatch is False
    assert result.report.changed_count == 3
    assert result.report.cleared_count == 1
    assert result.report.oracle_assigned_count == 1
    assert result.report.local_fallback_count == 1
    assert all(isinstance(change.after_eid, str) for change in result.report.changes if change.after_eid is not None)
    assert result.report.transparent_wrapper_cleared_count == 1
    assert result.report.match_method_counts == {
        "flat": 1,
        "local_fallback": 1,
        "transparent_wrapper_cleared": 1,
    }
    assert result.report.strict_disposition == "block"
    assert result.report.quirks_disposition == "record"
    assert result.report.changes[0].before_eid == "local-section-one"
    assert result.report.changes[0].after_eid == "section-1"
    assert result.report.changes[0].match_method == "flat"
    assert result.report.changes[0].match_key == "flat:body:section-1"


def test_pipeline_apply_ops_runs_oracle_alignment_when_enabled(tmp_path) -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="A section.",
                    attrs={"eId": "local-section-one"},
                ),
            ),
        ),
    )
    events: list[dict] = []

    result = UKReplayPipeline(tmp_path).apply_ops(
        statute,
        [],
        eid_map={"body:section-1": "section-1"},
        text_map={},
        allow_oracle_alignment=True,
        oracle_alignment_events_out=events,
    )

    assert result.body.children[0].attrs["eId"] == "section-1"
    assert events == [
        {
            "rule_id": UK_ORACLE_ALIGNMENT_RULE_ID,
            "phase": "oracle_alignment",
            "family": "oracle_alignment_adapter",
            "kind": "section",
            "label": "1",
            "before_eid": "section-1",
            "after_eid": "section-1",
            "match_method": "flat",
            "match_key": "flat:body:section-1",
        }
    ]


def test_align_uk_replay_to_oracle_disabled_without_eid_map() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="A section.",
                    attrs={"eId": "local-section-one"},
                ),
            ),
        ),
    )

    result = align_uk_replay_to_oracle_with_report(statute, eid_map=None, text_map={})

    assert result.statute is statute
    assert result.report.enabled is False
    assert result.report.stage == "none"
    assert result.report.node_count_mismatch is False
    assert result.report.changed_count == 0
    assert result.report.changes == ()


def test_align_uk_replay_to_oracle_local_schedule_fallback_uses_string_eids() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        text="Schedule paragraph.",
                    ),
                ),
            ),
        ),
    )

    result = align_uk_replay_to_oracle_with_report(
        statute,
        eid_map={"body:section-99": "section-99"},
        text_map={},
    )

    schedule = result.statute.supplements[0]
    paragraph = schedule.children[0]
    assert schedule.attrs["eId"] == "schedule"
    assert paragraph.attrs["eId"] == "schedule-paragraph-1"
    assert isinstance(schedule.attrs["eId"], str)
    assert isinstance(paragraph.attrs["eId"], str)
    assert result.report.local_fallback_count == 2
    assert all(isinstance(change.after_eid, str) for change in result.report.changes if change.after_eid is not None)


def test_align_uk_replay_to_oracle_does_not_synthesize_unlabeled_item_eids() -> None:
    statute = IRStatute(
        statute_id="asp/2001/14",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    attrs={"eId": "section-7"},
                    children=(
                        IRNode(kind=IRNodeKind.ITEM, text="first unlabeled limb"),
                        IRNode(kind=IRNodeKind.ITEM, text="second unlabeled limb"),
                    ),
                ),
            ),
        ),
    )

    result = align_uk_replay_to_oracle_with_report(
        statute,
        eid_map={"body:section-7": "section-7"},
        text_map={},
    )

    section = result.statute.body.children[0]
    assert section.attrs["eId"] == "section-7"
    assert [child.attrs for child in section.children] == [{}, {}]
    assert result.report.local_fallback_count == 0
    assert all(change.after_eid != "section-7-item" for change in result.report.changes)
