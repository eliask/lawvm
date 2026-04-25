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
