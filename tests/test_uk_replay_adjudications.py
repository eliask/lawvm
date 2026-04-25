"""UK replay adjudication emission tests."""
from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource, TextPatchKindEnum, TextPatchSpec, TextSelector, StructuralAction

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor, replay_uk_ops


def _base_statute() -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section one."),),
        ),
        supplements=(),
    )


def _source() -> OperationSource:
    return OperationSource(
        statute_id="ukpga/2026/1",
        title="Amending Act",
    )


def _duplicate_text_statute() -> IRStatute:
    shared_text = " ".join(["same", "text"] * 45)
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=shared_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text=shared_text),),
        ),
        supplements=(),
    )


def test_executor_records_replay_target_not_found() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_replace_target_missing",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SUBSECTION, label="a", text="Missing replacement"),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_gap"
    assert adjudications[0].detail["target"] == "section:9"
    assert adjudications[0].source_statute == "ukpga/2026/1"


def test_executor_records_text_match_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_no_match",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="does-not-exist", occurrence=0),
                replacement="updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_missing"
    assert adjudications[0].detail["action"] == "text_replace"
    assert adjudications[0].detail["text_match"] == "does-not-exist"


def test_executor_uses_typed_text_patch_without_legacy_text_fields() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha old Beta"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_typed_patch",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )

    assert adjudications == []
    assert executor.statute.body.children[0].text == "Alpha new Beta"


def test_executor_records_unsupported_action() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_unsupported",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_unsupported_action"
    assert adjudications[0].detail["action"] == "renumber"
    assert adjudications[0].op_id == "uk_test_renumber_unsupported"


def test_executor_records_payload_mismatch() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_payload_mismatch",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "9"), ("subsection", "1"))),
            payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Inserted subsection."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_parent_shape_gap"
    assert adjudications[0].detail["target"] == "section:9/subsection:1"


def test_executor_records_payload_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_payload_missing",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_payload_missing"
    assert adjudications[0].detail["action"] == "insert"
    assert adjudications[0].detail["target"] == "section:1/subsection:2"


def test_executor_records_replace_payload_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_replace_payload_missing",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_payload_missing"
    assert adjudications[0].detail["action"] == "replace"
    assert adjudications[0].detail["target"] == "section:1"


def test_executor_records_tree_invariant_violation_after_successful_insert() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_duplicate_section",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Duplicate section."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_existing_target_gap"
    assert adjudications[0].detail["target"] == "section:1"


def test_replay_uk_ops_collects_adjudications() -> None:
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_replay_api_collects",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "9"),)),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="a", text="Missing replacement"),
        source=_source(),
    )

    replay_uk_ops(_base_statute(), [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_gap"
    assert adjudications[0].op_id == "uk_test_replay_api_collects"


def test_replay_uk_ops_collects_text_duplication_warnings() -> None:
    adjudications: list[CompileAdjudication] = []

    replay_uk_ops(_duplicate_text_statute(), [], adjudications_out=adjudications)

    duplicate_phases = [
        adjudication.detail.get("phase")
        for adjudication in adjudications
        if adjudication.kind == "text_duplication_warning"
    ]

    assert duplicate_phases == ["replay_fold"]
