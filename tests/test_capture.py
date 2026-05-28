from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.tools.capture import build_capture
from lawvm.tools.capture_models import CaptureAmendmentView, CapturePayload, CaptureReplayMetaView, CaptureSourcePathologyView


def test_capture_source_pathology_view_freezes_detail_recursively() -> None:
    detail: dict[str, Any] = {"nested": {"paths": ["section:1"]}}

    view = CaptureSourcePathologyView(
        code="source_shape",
        message="source shape issue",
        source_statute="2020/1",
        target_label="1",
        detail=detail,
    )
    detail["nested"]["paths"].append("mutated")

    assert view.detail == {"nested": {"paths": ("section:1",)}}
    frozen_detail = cast(Any, view.detail)
    with pytest.raises(TypeError, match="immutable"):
        frozen_detail["extra"] = "blocked"


def test_capture_report_views_freeze_nested_rows_and_counts() -> None:
    observation = {"detail": {"slots": ["2:2"]}}
    counts = {"ok": 1}
    replay_meta = CaptureReplayMetaView(
        payload_completeness_kind_counts=counts,
        elaboration_observations=(observation,),
    )
    observation["detail"]["slots"].append("mutated")
    counts["new"] = 2

    assert replay_meta.payload_completeness_kind_counts == {"ok": 1}
    assert replay_meta.elaboration_observations == ({"detail": {"slots": ("2:2",)}},)
    frozen_observation = cast(Any, replay_meta.elaboration_observations[0])
    with pytest.raises(TypeError, match="immutable"):
        frozen_observation["extra"] = "blocked"

    compiled_op = {"target": {"path": ["section:1"]}}
    amendment = CaptureAmendmentView(
        statute_id="2020/1",
        title="Amending Act",
        issue_date="2020-01-01",
        effective_date="2020-01-01",
        included=True,
        source_available=True,
        compiled_ops=(compiled_op,),
    )
    compiled_op["target"]["path"].append("mutated")
    assert amendment.compiled_ops == ({"target": {"path": ("section:1",)}},)

    top_level_row = {"kind": "finding", "claims": ["a"]}
    payload = CapturePayload(
        statute_id="1990/1",
        replay_mode="legal_pit",
        compile_mode="default",
        profile="default",
        top_level_projection_rows=(top_level_row,),
    )
    top_level_row["claims"].append("mutated")
    assert payload.top_level_projection_rows == ({"kind": "finding", "claims": ("a",)},)


def test_build_capture_preserves_replay_meta_observation_streams(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )
    fake_replay_meta = {
        "cutoff_date": "2025-12-31",
        "oracle_version_amendment_id": "2025/999",
        "oracle_suspect": "",
        "elaboration_observations": [
            {
                "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                "stage": "group_payload_normalization",
                "source_statute": "1993/805",
            },
            {
                "kind": "ELAB.PAYLOAD_COMPLETENESS",
                "stage": "group_payload_normalization",
                "source_statute": "1993/805",
                "payload_completeness_kind": "fragmentary",
                "tail_policy": "preserve_unstated_tail",
                "detail": {
                    "payload_completeness_kind": "fragmentary",
                    "tail_policy": "preserve_unstated_tail",
                    "unassigned_payload_slots": ["2:2", "3:(unlabeled)"],
                },
            },
        ],
        "sparse_slot_bindings": [
            {
                "source_statute": "1993/805",
                "target_kind": "P",
                "target_norm": "35",
                "target_chapter": "",
                "op_description": "REPLACE 35 § 2 mom",
                "op_type": "REPLACE",
                "target_paragraph": 2,
                "target_item": "",
                "target_special": "",
                "payload_slot_index": 1,
                "payload_slot_label": "2",
            }
        ],
        "sparse_leftovers": [
            {
                "source_statute": "1993/805",
                "target_kind": "P",
                "target_norm": "35",
                "target_chapter": "",
                "unassigned_slots": ["2:2", "3:(unlabeled)"],
            }
        ],
        "apply_mutation_events": [
            {
                "op_id": "peg_0",
                "source_statute": "1993/805",
                "action": "replace",
                "helper": "_apply_deterministic_subsection_op",
                "outcome": "applied",
                "parent_path": [["chapter", "3"]],
                "created_paths": [[["chapter", "3"], ["section", "35"]]],
            }
        ],
        "apply_mutation_invariant_reports": [
            {
                "op_id": "peg_0",
                "helper": "_apply_deterministic_subsection_op",
                "outcome": "applied",
                "changed_paths": [[["chapter", "3"], ["section", "35"]]],
                "permitted_paths": [[["chapter", "3"], ["section", "35"]]],
                "covered_changed_paths": [[["chapter", "3"], ["section", "35"]]],
                "unexplained_changed_paths": [],
                "path_set_invariant_holds": True,
                "results": [],
            }
        ],
    }

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: (
            replay_meta_out.update(fake_replay_meta) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    replay_meta = payload.replay_meta
    assert replay_meta is not None
    assert replay_meta.elaboration_observations_count == 2
    assert replay_meta.payload_completeness_count == 1
    assert replay_meta.payload_completeness_kind_counts == {"fragmentary": 1}
    assert replay_meta.payload_completeness_tail_policy_counts == {"preserve_unstated_tail": 1}
    assert replay_meta.sparse_slot_bindings_count == 1
    assert replay_meta.sparse_leftovers_count == 1
    assert replay_meta.apply_mutation_events_count == 1
    assert replay_meta.apply_mutation_invariant_reports_count == 1
    assert replay_meta.apply_mutation_invariant_result_code_counts == {}
    assert replay_meta.elaboration_observations[0]["kind"] == ("ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE")
    assert replay_meta.sparse_slot_bindings[0]["payload_slot_label"] == "2"
    assert replay_meta.sparse_leftovers[0]["unassigned_slots"] == ("2:2", "3:(unlabeled)")
    assert replay_meta.apply_mutation_events[0]["helper"] == ("_apply_deterministic_subsection_op")
    assert replay_meta.apply_mutation_events[0]["created_paths"] == ((("chapter", "3"), ("section", "35")),)
    assert replay_meta.apply_mutation_invariant_reports[0]["helper"] == ("_apply_deterministic_subsection_op")
    assert replay_meta.apply_mutation_invariant_reports[0]["permitted_paths"] == (
        (("chapter", "3"), ("section", "35")),
    )


def test_build_capture_summarizes_apply_mutation_invariant_result_codes(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )
    fake_replay_meta = {
        "apply_mutation_invariant_reports": [
            {
                "op_id": "peg_0",
                "helper": "_apply_whole_section_op",
                "outcome": "applied",
                "changed_paths": [[["chapter", "3"], ["section", "35"]]],
                "permitted_paths": [[["chapter", "3"], ["section", "35"]]],
                "covered_changed_paths": [],
                "unexplained_changed_paths": [[["chapter", "3"], ["section", "36"]]],
                "path_set_invariant_holds": False,
                "results": [
                    {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
                    {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
                    {"code": "REPLAY_APPLY_BOUNDARY_UNRESOLVED"},
                ],
            }
        ],
    }

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: (
            replay_meta_out.update(fake_replay_meta) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    replay_meta = payload.replay_meta
    assert replay_meta is not None
    assert replay_meta.apply_mutation_invariant_result_code_counts == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 2,
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED": 1,
    }


def test_build_capture_does_not_publish_strictness_summary_rails(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: fake_master,
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    assert "strict_pass" not in payload.__dict__
    assert "strict_fail_reasons" not in payload.__dict__
    assert "strict_pass" not in payload.to_dict()
    assert "strict_fail_reasons" not in payload.to_dict()


def test_build_capture_projects_source_pathology_target_unit_kind_at_presentation_boundary(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=SimpleNamespace(
            statute_id="1990/1295",
            replay_mode="legal_pit",
            cutoff_date="2025-12-31",
            oracle_version_amendment_id="2025/999",
            oracle_suspect="",
            html_noncommensurable_reason="",
            lineage=[],
        ),
        source_pathology_rows=lambda: (
            {
                "code": "CONTAINER_MEMBERSHIP_MISMATCH",
                "message": "test",
                "source_statute": "1994/1304",
                "target_unit_kind": "chapter",
                "target_label": "4a luku",
                "detail": {},
            },
            {
                "code": "EMPTY_OPERATIVE_BODY",
                "message": "test",
                "source_statute": "1998/102",
                "target_unit_kind": "",
                "target_label": "1998/102",
                "detail": {},
            },
        ),
        projection_rows=lambda: (),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: fake_master,
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    assert payload.source_adjudication is not None
    assert payload.source_adjudication.source_pathologies[0].target_unit_kind == "chapter"
    assert payload.source_adjudication.source_pathologies[1].target_unit_kind == ""
    assert not hasattr(payload.source_adjudication.source_pathologies[0], "target_kind")
    assert payload.source_adjudication.source_pathologies[0].to_dict() == {
        "code": "CONTAINER_MEMBERSHIP_MISMATCH",
        "message": "test",
        "source_statute": "1994/1304",
        "target_unit_kind": "chapter",
        "target_label": "4a luku",
        "detail": {},
    }


def test_build_capture_threads_per_amendment_source_pathologies_and_mutation_reports(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (
            {
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "message": "first",
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_label": "35 §",
                "detail": {"diagnostic_reason": "partial_body_only"},
            },
            {
                "code": "EMPTY_OPERATIVE_BODY",
                "message": "second",
                "source_statute": "1994/900",
                "target_unit_kind": "",
                "target_label": "1994/900",
                "detail": {"diagnostic_reason": "empty_operational_body"},
            },
        ),
        projection_rows=lambda: (),
    )
    fake_replay_meta = {
        "lineage": [
            {
                "statute_id": "1993/805",
                "title": "First Source",
                "issue_date": "",
                "effective_date": "",
                "included": True,
            },
            {
                "statute_id": "1994/900",
                "title": "Second Source",
                "issue_date": "",
                "effective_date": "",
                "included": True,
            },
        ],
        "apply_mutation_invariant_reports": [
            {
                "op_id": "peg_0",
                "source_statute": "1993/805",
                "helper": "_apply_whole_section_op",
                "outcome": "applied",
                "results": [
                    {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
                    {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
                ],
            },
            {
                "op_id": "peg_1",
                "source_statute": "1994/900",
                "helper": "_apply_subsection_replace",
                "outcome": "applied",
                "results": [
                    {"code": "REPLAY_APPLY_BOUNDARY_UNRESOLVED"},
                ],
            },
        ],
    }

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: (
            replay_meta_out.update(fake_replay_meta) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    first = payload.amendments[0]
    second = payload.amendments[1]
    assert first.source_pathologies == (
        {
            "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "message": "first",
            "source_statute": "1993/805",
            "target_unit_kind": "section",
            "target_label": "35 §",
            "detail": {"diagnostic_reason": "partial_body_only"},
        },
    )
    assert second.source_pathologies == (
        {
            "code": "EMPTY_OPERATIVE_BODY",
            "message": "second",
            "source_statute": "1994/900",
            "target_unit_kind": "",
            "target_label": "1994/900",
            "detail": {"diagnostic_reason": "empty_operational_body"},
        },
    )
    assert first.apply_mutation_invariant_reports == (
        {
            "op_id": "peg_0",
            "source_statute": "1993/805",
            "helper": "_apply_whole_section_op",
            "outcome": "applied",
            "results": (
                {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
                {"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"},
            ),
        },
    )
    assert second.apply_mutation_invariant_reports == (
        {
            "op_id": "peg_1",
            "source_statute": "1994/900",
            "helper": "_apply_subsection_replace",
            "outcome": "applied",
            "results": (
                {"code": "REPLAY_APPLY_BOUNDARY_UNRESOLVED"},
            ),
        },
    )
    assert first.apply_mutation_invariant_result_code_counts == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 2,
    }
    assert second.apply_mutation_invariant_result_code_counts == {
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED": 1,
    }


def test_build_capture_serializes_typed_text_patch(monkeypatch) -> None:
    lineage = [
        {
            "statute_id": "1993/805",
            "title": "Test Source",
            "issue_date": "",
            "effective_date": "",
            "included": True,
        }
    ]
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )
    fake_op = LegalOperation(
        op_id="cap_0",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="väärä teksti", occurrence=2),
            replacement="oikea teksti",
        ),
        source=OperationSource(statute_id="1993/805", raw_text="typed replace"),
        provenance_tags=("typed_cap_0",),
    )

    def fake_replay_xml(
        statute_id: str,
        mode: str = "finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
    ):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "source_statute": "1993/805",
                    "op_id": "cap_0",
                    "action": "text_replace",
                }
            )
        if replay_meta_out is not None:
            replay_meta_out.update({"lineage": lineage})
        if lo_ops_out is not None:
            lo_ops_out.append(fake_op)
        return fake_master

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    text_patch = payload.amendments[0].canonical_ops[0]["text_patch"]
    assert text_patch == {
        "kind": "replace",
        "selector": {
            "match_text": "väärä teksti",
            "occurrence": 2,
        },
        "replacement": "oikea teksti",
    }
    assert payload.amendments[0].canonical_ops[0]["provenance_tags"] == ("typed_cap_0",)


def test_build_capture_projects_legacy_text_fields_from_typed_patch(monkeypatch) -> None:
    lineage = [
        {
            "statute_id": "1993/805",
            "title": "Test Source",
            "issue_date": "",
            "effective_date": "",
            "included": True,
        }
    ]
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )
    fake_op = LegalOperation(
        op_id="cap_1",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="typed text", occurrence=3),
            replacement="typed replacement",
        ),
        source=OperationSource(statute_id="1993/805", raw_text="typed replace"),
        provenance_tags=("typed_cap_1",),
    )

    def fake_replay_xml(
        statute_id: str,
        mode: str = "finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None,
    ):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "source_statute": "1993/805",
                    "op_id": "cap_1",
                    "action": "text_replace",
                }
            )
        if replay_meta_out is not None:
            replay_meta_out.update({"lineage": lineage})
        if lo_ops_out is not None:
            lo_ops_out.append(fake_op)
        return fake_master

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    op_payload = payload.amendments[0].canonical_ops[0]
    assert "text_match" not in op_payload
    assert "text_replacement" not in op_payload
    assert "text_occurrence" not in op_payload
    assert op_payload["provenance_tags"] == ("typed_cap_1",)
    assert op_payload["text_patch"] == {
        "kind": "replace",
        "selector": {
            "match_text": "typed text",
            "occurrence": 3,
        },
        "replacement": "typed replacement",
    }


def test_build_capture_uses_projection_row_public_names(monkeypatch) -> None:
    lineage = [
        {
            "statute_id": "1993/805",
            "title": "Test Source",
            "issue_date": "",
            "effective_date": "",
            "included": True,
        }
    ]
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (
            {"kind": "PARSE.TARGET_GUESSING", "message": "note", "source": ""},
            {"kind": "source_pathology", "message": "source-scoped", "source": "1993/805"},
        ),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None: (
            replay_meta_out.update({"lineage": lineage}) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")
    data = payload.to_dict()

    assert payload.counts is not None
    assert payload.counts.projection_rows == 2
    assert payload.top_level_projection_rows == (
        {"kind": "PARSE.TARGET_GUESSING", "message": "note", "source": ""},
    )
    assert payload.amendments[0].counts is not None
    assert payload.amendments[0].counts.projection_rows == 1
    assert payload.amendments[0].projection_rows == (
        {"kind": "source_pathology", "message": "source-scoped", "source": "1993/805"},
    )
    assert not hasattr(payload.counts, "recovered_ops")
    assert not hasattr(payload.amendments[0].counts, "recovered_ops")
    assert "adjudications" not in data["counts"]
    assert "top_level_adjudications" not in data
    assert "adjudications" not in data["amendments"][0]
    assert "recovered_ops" not in data["counts"]
    assert "recovered_ops" not in data["amendments"][0]["counts"]
    assert "recovered_ops" not in data["amendments"][0]
    assert "source_completeness_flags" not in data
    assert data["counts"]["projection_rows"] == 2
    assert data["top_level_projection_rows"] == [
        {"kind": "PARSE.TARGET_GUESSING", "message": "note", "source": ""},
    ]
    assert data["amendments"][0]["counts"]["projection_rows"] == 1


def test_build_capture_hydrates_typed_source_adjudication_from_replay_meta(monkeypatch) -> None:
    lineage = [
        {
            "statute_id": "1993/805",
            "title": "Test Source",
            "issue_date": "",
            "effective_date": "1993-01-01",
            "included": True,
        }
    ]
    fake_master = SimpleNamespace(
        source_adjudication=None,
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: (
            replay_meta_out.update(
                {
                    "lineage": lineage,
                    "oracle_version_amendment_id": "raw-mid",
                    "oracle_suspect": "raw-suspect",
                    "cutoff_date": "2025-12-31",
                }
            ) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    assert payload.source_adjudication is not None
    assert payload.source_completeness is not None
    assert payload.source_adjudication.oracle_version_amendment_id == "raw-mid"
    assert payload.source_adjudication.oracle_suspect == "raw-suspect"
    assert payload.source_completeness.chain_length == 1
    assert payload.source_completeness.source_available == 1
    assert payload.amendments[0].statute_id == "1993/805"


def test_build_capture_prefers_existing_typed_source_adjudication_over_conflicting_replay_meta(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=SimpleNamespace(
            statute_id="1990/1295",
            replay_mode="legal_pit",
            cutoff_date="typed-cutoff",
            oracle_version_amendment_id="typed-mid",
            oracle_suspect="typed-suspect",
            html_noncommensurable_reason="",
            lineage=[
                {
                    "statute_id": "1993/805",
                    "title": "Typed Source",
                    "issue_date": "",
                    "effective_date": "1993-01-01",
                    "included": True,
                }
            ],
        ),
        source_pathology_rows=lambda: (),
        projection_rows=lambda: (),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id,
        mode="finlex_oracle",
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        _adjudications_out=None: (
            replay_meta_out.update(
                {
                    "lineage": [
                        {
                            "statute_id": "2000/999",
                            "title": "Raw Source",
                            "issue_date": "",
                            "effective_date": "",
                            "included": False,
                        }
                    ],
                    "oracle_version_amendment_id": "raw-mid",
                    "oracle_suspect": "raw-suspect",
                }
            ) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter.get_corpus",
        lambda: SimpleNamespace(read_source=lambda mid: None),
    )

    payload = build_capture("1990/1295", replay_mode="legal_pit")

    assert payload.source_adjudication is not None
    assert payload.source_completeness is not None
    assert payload.source_adjudication.oracle_version_amendment_id == "typed-mid"
    assert payload.source_adjudication.oracle_suspect == "typed-suspect"
    assert payload.source_completeness.chain_length == 1
    assert payload.source_completeness.source_available == 1
    assert payload.amendments[0].statute_id == "1993/805"
