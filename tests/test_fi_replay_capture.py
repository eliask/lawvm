from __future__ import annotations

from dataclasses import replace

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.apply_runtime_support import (
    SectionSnapshotIdentity,
    _prior_paragraph_labels_for_subsection_paths,
    _snapshot_section_los_for_identity,
    _timeline_exact_target_exists_in_history,
    _timeline_payload_target_exists_in_history,
)
from lawvm.finland.replay_capture import (
    ReplayCaptureRequest,
    ReplayLegalOperationCaptureList,
    resolve_replay_capture_sinks,
)


def test_resolve_replay_capture_sinks_allocates_for_full_products() -> None:
    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=None,
            lo_ops_out=None,
            failed_ops_out=None,
            build_full_products=True,
        )
    )

    assert sinks.compiled_ops == []
    assert sinks.legal_operations == []
    assert sinks.failed_ops == []
    assert isinstance(sinks.legal_operations, ReplayLegalOperationCaptureList)


def test_resolve_replay_capture_sinks_preserves_caller_lists() -> None:
    compiled_ops: list[dict[str, object]] = []
    legal_operations: list[object] = []
    failed_ops: list[object] = []

    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=compiled_ops,
            lo_ops_out=legal_operations,
            failed_ops_out=failed_ops,
            build_full_products=True,
        )
    )

    assert sinks.compiled_ops is compiled_ops
    assert sinks.legal_operations is legal_operations
    assert sinks.failed_ops is failed_ops


def test_resolve_replay_capture_sinks_keeps_lightweight_replay_uncaptured() -> None:
    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=None,
            lo_ops_out=None,
            failed_ops_out=None,
            build_full_products=False,
        )
    )

    assert sinks.compiled_ops is None
    assert sinks.legal_operations is None
    assert sinks.failed_ops is None


def test_replay_legal_operation_capture_list_invalidates_snapshot_index_on_rewrite() -> None:
    payload = IRNode(kind=IRNodeKind.SECTION, label="1", text="one")
    op = LegalOperation(
        op_id="snapshot_section_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=payload,
    )
    history = ReplayLegalOperationCaptureList()
    history.append(op)

    section_1 = SectionSnapshotIdentity(part="", chapter="", section="1")
    section_2 = SectionSnapshotIdentity(part="", chapter="", section="2")
    assert _snapshot_section_los_for_identity(history, section_1) == [op]
    history.base_provision_index_cache = {("base",): object()}
    base_target_sentinel = object()
    history.base_target_exists_cache = {("base-target",): base_target_sentinel}
    exact_target_sentinel = object()
    history.timeline_exact_target_index = {("exact",): exact_target_sentinel}
    repeal_placeholder_sentinel = object()
    history.timeline_latest_repeal_placeholder_index = {
        ("repeal-placeholder",): repeal_placeholder_sentinel
    }
    payload_target_sentinel = object()
    history.timeline_payload_target_index = {("payload",): payload_target_sentinel}
    history.timeline_target_exists_cache = {("cached",): True}

    rewritten = replace(op, target=LegalAddress(path=(("section", "2"),)))
    history[0] = rewritten

    assert history.base_provision_index_cache is None
    assert history.base_target_exists_cache == {("base-target",): base_target_sentinel}
    assert history.timeline_exact_target_index is None
    assert history.timeline_latest_repeal_placeholder_index is None
    assert history.timeline_payload_target_index is None
    assert history.timeline_target_exists_cache is None
    assert _snapshot_section_los_for_identity(history, section_1) == []
    assert _snapshot_section_los_for_identity(history, section_2) == [rewritten]


def test_replay_legal_operation_capture_list_preserves_snapshot_index_on_metadata_rewrite() -> None:
    payload = IRNode(kind=IRNodeKind.SECTION, label="1", text="one")
    op = LegalOperation(
        op_id="snapshot_section_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=payload,
        source=OperationSource(statute_id="1999/1", effective="2000-01-01"),
    )
    history = ReplayLegalOperationCaptureList()
    history.append(op)
    section_1 = SectionSnapshotIdentity(part="", chapter="", section="1")
    assert _snapshot_section_los_for_identity(history, section_1) == [op]
    snapshot_index = history.snapshot_index

    rewritten = replace(op, source=OperationSource(statute_id="1999/1", effective="2001-01-01"))
    history[0] = rewritten

    assert history.snapshot_index is snapshot_index
    assert _snapshot_section_los_for_identity(history, section_1) == [rewritten]


def test_replay_legal_operation_capture_list_keeps_indexes_across_append() -> None:
    history = ReplayLegalOperationCaptureList()
    history.base_provision_index_cache = {("base",): object()}
    base_target_sentinel = object()
    history.base_target_exists_cache = {("base-target",): base_target_sentinel}
    exact_target_sentinel = object()
    history.timeline_exact_target_index = {("exact",): exact_target_sentinel}
    repeal_placeholder_sentinel = object()
    history.timeline_latest_repeal_placeholder_index = {
        ("repeal-placeholder",): repeal_placeholder_sentinel
    }
    payload_target_sentinel = object()
    history.timeline_payload_target_index = {("payload",): payload_target_sentinel}
    history.timeline_target_exists_cache = {("cached",): True}

    history.append(
        LegalOperation(
            op_id="snapshot_section_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        )
    )

    assert history.base_provision_index_cache is not None
    assert history.base_target_exists_cache == {("base-target",): base_target_sentinel}
    assert history.timeline_exact_target_index == {("exact",): exact_target_sentinel}
    assert history.timeline_latest_repeal_placeholder_index == {
        ("repeal-placeholder",): repeal_placeholder_sentinel
    }
    assert history.timeline_payload_target_index == {("payload",): payload_target_sentinel}
    assert history.timeline_target_exists_cache == {("cached",): True}


def test_timeline_exact_target_index_preserves_effective_cutoff_semantics() -> None:
    target = LegalAddress(path=(("section", "1"),))
    history = ReplayLegalOperationCaptureList()
    history.append(
        LegalOperation(
            op_id="future",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=target,
            source=OperationSource(statute_id="1999/1", effective="2020-01-01"),
        )
    )

    assert not _timeline_exact_target_exists_in_history(
        history,
        target.path,
        before_effective="2019-01-01",
    )

    history.append(
        LegalOperation(
            op_id="past",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=target,
            source=OperationSource(statute_id="1999/2", effective="2018-01-01"),
        )
    )

    assert _timeline_exact_target_exists_in_history(
        history,
        target.path,
        before_effective="2019-01-01",
    )

    history.append(
        LegalOperation(
            op_id="undated",
            sequence=3,
            action=StructuralAction.REPLACE,
            target=target,
            source=OperationSource(statute_id="1999/3", effective=""),
        )
    )

    assert _timeline_exact_target_exists_in_history(
        history,
        target.path,
        before_effective="2019-01-01",
    )


def test_timeline_payload_target_index_preserves_effective_cutoff_semantics() -> None:
    target_path = (("section", "1"), ("subsection", "1"))
    future_payload = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    history = ReplayLegalOperationCaptureList()
    history.append(
        LegalOperation(
            op_id="future",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=future_payload,
            source=OperationSource(statute_id="1999/1", effective="2020-01-01"),
        )
    )

    assert not _timeline_payload_target_exists_in_history(
        history,
        target_path,
        before_effective="2019-01-01",
    )

    history.append(
        LegalOperation(
            op_id="past",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=future_payload,
            source=OperationSource(statute_id="1999/2", effective="2018-01-01"),
        )
    )

    assert _timeline_payload_target_exists_in_history(
        history,
        target_path,
        before_effective="2019-01-01",
    )

    history.append(
        LegalOperation(
            op_id="undated",
            sequence=3,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=future_payload,
            source=OperationSource(statute_id="1999/3", effective=""),
        )
    )

    assert _timeline_payload_target_exists_in_history(
        history,
        target_path,
        before_effective="2019-01-01",
    )


def test_prior_paragraph_labels_for_subsection_paths_batches_history_scan() -> None:
    subsection_path = (("section", "1"), ("subsection", "1"))
    second_subsection_path = (("section", "1"), ("subsection", "2"))
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="a"),),
                    ),
                ),
            ),
        ),
    )
    history = ReplayLegalOperationCaptureList()
    history.extend(
        [
            LegalOperation(
                op_id="replace_subsection",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=subsection_path),
                payload=IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                        IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    ),
                ),
                source=OperationSource(statute_id="1999/1", effective="2018-01-01"),
            ),
            LegalOperation(
                op_id="insert_paragraph",
                sequence=2,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=subsection_path + (("paragraph", "4"),)),
                source=OperationSource(statute_id="1999/2", effective="2019-01-01"),
            ),
            LegalOperation(
                op_id="repeal_paragraph",
                sequence=3,
                action=StructuralAction.REPEAL,
                target=LegalAddress(path=subsection_path + (("paragraph", "2"),)),
                source=OperationSource(statute_id="1999/3", effective="2019-06-01"),
            ),
            LegalOperation(
                op_id="future_paragraph",
                sequence=4,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=subsection_path + (("paragraph", "5"),)),
                source=OperationSource(statute_id="1999/4", effective="2025-01-01"),
            ),
        ]
    )

    labels_by_path = _prior_paragraph_labels_for_subsection_paths(
        child_paths={subsection_path, second_subsection_path},
        replay_history_ops=history,
        base_ir=base_ir,
        before_effective="2020-01-01",
    )

    assert labels_by_path[subsection_path] == {"1", "3", "4"}
    assert labels_by_path[second_subsection_path] == {"a"}
