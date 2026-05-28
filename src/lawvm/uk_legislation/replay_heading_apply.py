"""UK replay heading-wrapper apply helpers."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.heading_facets import (
    _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_RESOLVED_RULE_ID,
    _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
)
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
)


class _HeadingReplaySelf(Protocol):
    adjudications_out: list[CompileAdjudication]

    def _find_parent_tuple_for_node(
        self,
        target_node: UKMutableNode,
    ) -> tuple[Optional[UKMutableNode], Optional[int]]: ...

    def _remove_node(
        self,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        idx: Optional[int],
    ) -> bool: ...

    def _record_repealed_target(self, target: LegalAddress) -> None: ...


class UKReplayHeadingApplyMixin:

    def _repeal_crossheading_group(
        self: _HeadingReplaySelf,
        target: LegalAddress,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        """Delete a heading wrapper only when source and live shape prove sole ownership."""
        if str(selector.get("selector_mode") or "") != "structural_with_heading_above_repeal":
            reason_code = "invalid_selector"
            detail: dict[str, Any] = {"selector": dict(selector)}
        elif parent is None:
            reason_code = "target_has_no_heading_parent"
            detail = {"selector": dict(selector)}
        else:
            parent_kind = _uk_kind_value(parent.kind).lower()
            structural_children = [
                child
                for child in parent.children
                if _uk_kind_value(child.kind).lower()
                in {"section", "article", "rule", "regulation", "paragraph", "subparagraph", "item"}
            ]
            if parent_kind not in {"crossheading", "p1group", "pgroup", "pblock"}:
                reason_code = "parent_is_not_heading_wrapper"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
            elif not (parent.text or "").strip():
                reason_code = "heading_wrapper_has_no_heading_text"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
            elif len(structural_children) != 1 or structural_children[0] is not node:
                reason_code = "heading_wrapper_does_not_solely_own_target"
                detail = {
                    "parent_kind": parent_kind,
                    "structural_child_count": len(structural_children),
                    "selector": dict(selector),
                }
            else:
                grandparent, parent_idx = self._find_parent_tuple_for_node(parent)
                if self._remove_node(parent, grandparent, parent_idx):
                    self._record_repealed_target(target)
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_RESOLVED_RULE_ID,
                        message=(
                            "UK replay removed a cross-heading wrapper because "
                            "the source explicitly repealed the heading above "
                            "the target and the wrapper owned only that target."
                        ),
                        op=op,
                        detail=uk_replay_action_target_detail(
                            op,
                            target,
                            blocking=False,
                            family="heading_facet_replay",
                            **{
                                "removed_parent_kind": parent_kind,
                                "removed_heading_preview": " ".join((parent.text or "").split())[:200],
                                "selector": dict(selector),
                            },
                        ),
                    )
                    return True
                reason_code = "heading_wrapper_remove_failed"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
            message=(
                "UK replay skipped cross-heading group repeal: source selector "
                "did not prove a unique heading wrapper solely owned by the target."
            ),
            op=op,
            detail=uk_replay_blocking_action_target_detail(
                op,
                target,
                family="heading_facet_replay",
                **{"reason_code": reason_code, **detail},
            ),
        )
        return False
