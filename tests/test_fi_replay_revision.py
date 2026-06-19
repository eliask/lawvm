from __future__ import annotations

from typing import Any, cast

from lawvm.core.elaboration_context import snapshot_replay_lookups
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.statute import ReplayState


def test_replay_state_with_ir_increments_revision() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))

    next_state = state.with_ir(IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)))

    assert state.revision == 0
    assert next_state.revision == 1
    assert next_state.snapshot_rev == 1


def test_snapshot_replay_lookups_uses_replay_revision() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    next_state = state.with_ir(IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)))

    lookups = snapshot_replay_lookups(cast(Any, next_state))

    assert lookups.snapshot_rev == 1


def test_snapshot_replay_lookups_reuses_provision_facts_with_current_revision() -> None:
    section = IRNode(kind=IRNodeKind.SECTION, label="1", text="old")
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(section,)))
    first = snapshot_replay_lookups(cast(Any, state))

    changed_section = IRNode(kind=IRNodeKind.SECTION, label="1", text="new")
    next_state = state.with_ir(
        IRNode(kind=IRNodeKind.BODY, children=(changed_section,)),
        preserve_provision_index=True,
    )

    second = snapshot_replay_lookups(cast(Any, next_state))

    assert second.snapshot_rev == 1
    assert second.unique_section_paths is first.unique_section_paths
    assert second.chapter_members is first.chapter_members
    assert second.part_members is first.part_members
    assert second.all_section_labels is first.all_section_labels
