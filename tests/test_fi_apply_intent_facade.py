"""Tests for Finland ApplyIntentFacade dispatch and lane catalog."""

from __future__ import annotations

from typing import Any, cast

from lawvm.core.canonical_intent import (
    ExecutionContract,
    IntentKind,
    NodeTarget,
    OccupancyPolicy,
    Replace,
)
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_intent_facade import (
    APPLY_INTENT_LANES,
    LEGACY_DISPATCH_FALLBACK_KIND,
    apply_intent_lane_summary,
    classify_apply_dispatch_lane,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp


def _sec(label: str, text: str = "body") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _rop(
    *,
    op_type: str = "REPLACE",
    intent: object | None = None,
) -> ResolvedOp:
    op = AmendmentOp(
        op_id="test_op",
        op_type=cast(Any, op_type),
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("1", "new"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    rop.intent = None
    if intent is not None:
        rop.intent = cast(Any, intent)
    return rop


def test_apply_intent_lanes_catalog_covers_sixteen_slices() -> None:
    # The legacy_dispatch lane was removed: the legacy field dispatcher was
    # corpus-cold (0/147 body statements executed) and deleted, so the typed
    # canonical lane is the sole live apply dispatcher.
    assert len(APPLY_INTENT_LANES) == 16
    lane_ids = {lane.lane_id for lane in APPLY_INTENT_LANES}
    assert "typed_dispatch" in lane_ids
    assert "legacy_dispatch" not in lane_ids
    assert "ops_executor" in lane_ids


def test_apply_intent_lane_summary_machine_shape() -> None:
    summary = apply_intent_lane_summary()
    assert summary["catalog_kind"] == "finland_apply_intent_lanes"
    assert summary["lane_count"] == 16
    assert summary["legacy_dispatch_fallback_kind"] == LEGACY_DISPATCH_FALLBACK_KIND
    lanes_by_granularity = cast(dict[str, object], summary["lanes_by_granularity"])
    assert "dispatch" in lanes_by_granularity


def test_classify_apply_dispatch_lane_typed_canonical() -> None:
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(address=LegalAddress(path=(("section", "1"),))),
        payload=cast(Any, _sec("1", "new")),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    assert classify_apply_dispatch_lane(_rop(intent=intent)) == "typed_canonical"


def test_classify_apply_dispatch_lane_legacy_for_move_without_intent() -> None:
    assert classify_apply_dispatch_lane(_rop(op_type="MOVE")) == "legacy_strict_only"


def test_classify_apply_dispatch_lane_blocks_required_without_intent() -> None:
    assert classify_apply_dispatch_lane(_rop(op_type="REPLACE", intent=None)) is None


def test_classify_apply_dispatch_lane_legacy_when_no_rop() -> None:
    assert classify_apply_dispatch_lane(None) == "legacy_strict_only"
