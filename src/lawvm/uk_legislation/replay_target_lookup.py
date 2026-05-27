"""UK replay target lookup helpers."""

from __future__ import annotations

import re
from typing import NamedTuple, Optional, cast

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name, _addr_container, _addr_leaf_kind, _addr_leaf_label, _uk_kind_value
from lawvm.uk_legislation.canonicalize import (
    canonicalize_uk_address,
    uk_compound_subsection_candidate,
    uk_recursive_kind_match,
    uk_schedule_ordinal_paragraph_matches,
    uk_schedule_root_candidates,
)
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_recovery_action_target_detail,
)
from lawvm.uk_legislation.replay_target_gaps import (
    uk_existing_target_insert_gap,
    uk_is_explicit_direct_section_paragraph_target,
)
from lawvm.uk_legislation.source_context import _source_parent_range_label
from lawvm.uk_legislation.source_parent_payloads import UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
from lawvm.uk_legislation.target_anchors import uk_match_kind_label
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_SCHEDULE_ITEM_TARGET_FROM_PARENT_SUBSTITUTION_RULE_ID = (
    "uk_replay_schedule_item_target_from_parent_substitution_resolved"
)
_UK_REPLAY_SCHEDULE_P1GROUP_PARAGRAPH_WRAPPER_RESOLVED_RULE_ID = (
    "uk_replay_schedule_p1group_paragraph_wrapper_resolved"
)


class _ExistingInsertTargetResolution(NamedTuple):
    node: Optional[UKMutableNode]
    parent: Optional[UKMutableNode]
    index: Optional[int]
    reason: str


class UKReplayTargetLookupMixin:
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]

    def _find_existing_insert_target_by_explicit_parent_leaf(
        self,
        target: LegalAddress,
        op: LegalOperation,
    ) -> _ExistingInsertTargetResolution:
        if _action_name(op.action) != "insert" or op.payload is None:
            return _ExistingInsertTargetResolution(None, None, None, "")
        parent_addr = target.parent() if len(target.path) > 1 else None
        leaf_kind = _addr_leaf_kind(target)
        leaf_label = _addr_leaf_label(target)
        if parent_addr is None or not leaf_kind or not leaf_label:
            return _ExistingInsertTargetResolution(None, None, None, "")
        parent_candidate: Optional[UKMutableNode] = None
        parent_eid = self._derive_target_eid(parent_addr)
        if parent_eid:
            parent_candidate, _, _ = self._find_node_and_parent_statute(
                parent_eid,
                allow_sequence_match=False,
            )
            if parent_candidate is not None and not self._eid_candidate_matches_target_leaf(
                parent_candidate,
                parent_addr,
            ):
                parent_candidate = None
        if parent_candidate is None:
            parent_candidate, _, _ = self._find_node_by_target(
                parent_addr,
                allow_recursive_match=False,
            )
        if parent_candidate is None:
            return _ExistingInsertTargetResolution(None, None, None, "")
        for child_idx, child in enumerate(parent_candidate.children):
            if uk_match_kind_label(child, leaf_kind, leaf_label) and uk_existing_target_insert_gap(
                target,
                child,
                op,
            ):
                return _ExistingInsertTargetResolution(
                    child,
                    parent_candidate,
                    child_idx,
                    "explicit_parent_leaf_same_kind_label",
                )
        return _ExistingInsertTargetResolution(None, None, None, "")

    def _find_compound_subsection_candidate(
        self,
        curr_node: UKMutableNode,
        label: str,
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        """Match malformed UK shapes like legal subsection 8A stored as 8 -> a."""
        return uk_compound_subsection_candidate(
            cast(IRNode, curr_node),
            label,
            match_kind_label=uk_match_kind_label,
        )

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        """Find a node and its parent by LegalAddress path."""
        cache_key = None
        if target_resolution_op is None:
            cache_key = self._target_lookup_cache_key(
                target,
                allow_compound_subsection_alias=allow_compound_subsection_alias,
                allow_recursive_match=allow_recursive_match,
            )
            cached = self._cached_target_lookup(cache_key)
            if cached is not None:
                return cached
            target_eid = self._derive_target_eid(target)
            if target_eid:
                node, parent, idx = self._find_node_and_parent_statute(
                    target_eid,
                    allow_sequence_match=False,
                )
                if node is not None and self._eid_candidate_matches_target_leaf(node, target):
                    result = (node, parent, idx)
                    self._store_target_lookup_cache(cache_key, result)
                    return result

        def _find(address: LegalAddress) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
            path = list(address.path)
            container = _addr_container(address)

            # 1. Resolve top-level container
            roots: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
            if container == "schedule":
                # First path segment is ("schedule", label)
                sched_label = path[0][1] if path else None
                remaining = path[1:]
                roots = uk_schedule_root_candidates(
                    cast(list[IRNode], self.statute.supplements),
                    sched_label=sched_label,
                    remaining_path=tuple(remaining),
                    match_kind_label=uk_match_kind_label,
                )
                if sched_label and roots and not remaining:
                    sch, _, idx = roots[0]
                    return cast(UKMutableNode, sch), None, idx
                if not sched_label and len(roots) == 1 and not remaining:
                    sch, _, idx = roots[0]
                    return cast(UKMutableNode, sch), None, idx
                path = remaining
            else:
                roots = [(cast(IRNode, self.statute.body), None, None)]
            if not roots:
                return None, None, None

            is_eur = bool(self.statute.metadata.get("is_eur", False))
            curr_cands = roots
            for p_kind, p_label in path:
                next_cands: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
                for curr_node, _, _ in curr_cands:
                    for i, child in enumerate(curr_node.children):
                        if is_eur:
                            nk = _uk_kind_value(child.kind).lower()
                            tk = str(p_kind).lower()
                            if nk == "paragraph" and tk == "subsection":
                                continue
                            if nk == "subsection" and tk == "paragraph":
                                continue
                        if uk_match_kind_label(child, p_kind, p_label):
                            next_cands.append((child, curr_node, i))
                    if not next_cands and allow_compound_subsection_alias and p_kind.lower() == "subsection" and p_label:
                        compound = self._find_compound_subsection_candidate(cast(UKMutableNode, curr_node), p_label)
                        if compound[0] is not None:
                            next_cands.append(cast(tuple[IRNode, Optional[IRNode], Optional[int]], compound))
                if not next_cands:
                    if container == "schedule":
                        ordinal_matches = uk_schedule_ordinal_paragraph_matches(
                            curr_cands,
                            p_kind=p_kind,
                            p_label=p_label,
                        )
                        if ordinal_matches:
                            if target_resolution_op is not None:
                                for resolved_node, resolved_parent, _resolved_idx in ordinal_matches:
                                    if (
                                        _uk_kind_value(resolved_node.kind) == "paragraph"
                                        and resolved_parent is not None
                                        and _uk_kind_value(resolved_parent.kind) == "p1group"
                                        and not _clean_num(str(resolved_parent.label or ""))
                                    ):
                                        _append_uk_replay_adjudication(
                                            self.adjudications_out,
                                            kind=_UK_REPLAY_SCHEDULE_P1GROUP_PARAGRAPH_WRAPPER_RESOLVED_RULE_ID,
                                            message=(
                                                "UK replay resolved an explicit schedule paragraph "
                                                "target through an unlabeled p1group wrapper with a "
                                                "single exactly labelled paragraph child."
                                            ),
                                            op=target_resolution_op,
                                            detail=uk_replay_action_target_detail(
                                                target_resolution_op,
                                                target,
                                                blocking=False,
                                                paragraph_label=str(p_label),
                                                wrapper_kind="p1group",
                                                family="target_resolution_recovery",
                                                quirks_disposition="apply",
                                            ),
                                        )
                                        break
                            next_cands = ordinal_matches
                    if not next_cands:
                        for curr_node, _, _ in curr_cands:
                            if allow_recursive_match:
                                for child in curr_node.children:
                                    res_node, res_p, res_i = self._find_recursive_match(
                                        cast(UKMutableNode, child), p_kind, p_label
                                    )
                                    if res_node:
                                        next_cands.append((res_node, res_p, res_i))
                if not next_cands:
                    return None, None, None
                curr_cands = next_cands
            return (
                cast(tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]], curr_cands[0])
                if curr_cands
                else (None, None, None)
            )

        if uk_is_explicit_direct_section_paragraph_target(target):
            raw_node = _find(target)
            if raw_node[0] is not None:
                if cache_key is not None:
                    self._store_target_lookup_cache(cache_key, raw_node)
                return raw_node
        result = _find(canonicalize_uk_address(target))
        if cache_key is not None:
            self._store_target_lookup_cache(cache_key, result)
        return result

    def _find_unique_schedule_item_for_source_parent_substitution_range_target(
        self,
        target: LegalAddress,
        op: LegalOperation,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        """Resolve feed `Sch. N para. (d)` shape to a unique schedule item.

        This recovery is available only for ops whose lowering witness proved a
        source-parent sibling-range substitution. It does not authorize general
        schedule paragraph-to-item fallback.
        """
        if op.witness_rule_id != _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID:
            return None, None, None
        if _addr_container(target) != "schedule" or len(tuple(target.path)) != 2:
            return None, None, None
        schedule_label = target.path[0][1]
        target_kind, target_label_raw = target.path[1]
        target_label = _source_parent_range_label(target_label_raw)
        if target_kind != "paragraph" or not re.fullmatch(r"[a-z]", target_label, re.I):
            return None, None, None
        if op.payload is not None:
            payload_kind = _uk_kind_value(op.payload.kind).lower()
            payload_label = _source_parent_range_label(op.payload.label or "")
            if payload_kind != "item" or payload_label != target_label:
                return None, None, None

        roots = uk_schedule_root_candidates(
            cast(list[IRNode], self.statute.supplements),
            sched_label=schedule_label,
            remaining_path=(),
            match_kind_label=uk_match_kind_label,
        )
        candidates: list[tuple[UKMutableNode, UKMutableNode, int]] = []

        def _walk(parent: UKMutableNode) -> None:
            for child_idx, child in enumerate(parent.children):
                if (
                    _uk_kind_value(child.kind).lower() == "item"
                    and _source_parent_range_label(child.label or "") == target_label
                ):
                    candidates.append((child, parent, child_idx))
                _walk(child)

        for root, _root_parent, _root_idx in roots:
            _walk(cast(UKMutableNode, root))
        if len(candidates) != 1:
            return None, None, None
        recovered_node, recovered_parent, recovered_idx = candidates[0]
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_ITEM_TARGET_FROM_PARENT_SUBSTITUTION_RULE_ID,
            message=(
                "UK replay resolved a source-parent substitution-range target "
                "whose effect feed names a schedule item as a schedule paragraph."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="target_resolution_recovery",
                recovered_kind=_uk_kind_value(recovered_node.kind),
                recovered_label=recovered_node.label or "",
                source_rule_id=_UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
            ),
        )
        return recovered_node, recovered_parent, recovered_idx

    def _find_recursive_match(
        self, node: UKMutableNode, kind: str, label: str
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        cache_key = self._recursive_match_cache_key(node, kind=kind, label=label)
        cached = self._cached_recursive_match(cache_key)
        if cached is not None:
            return cached
        result = uk_recursive_kind_match(
            cast(IRNode, node),
            kind=str(kind),
            label=label,
            match_kind_label=uk_match_kind_label,
        )
        typed_result = cast(tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]], result)
        self._store_recursive_match_cache(cache_key, typed_result)
        return result

    def _empty_schedule_root_shape_gap(self, target: LegalAddress) -> bool:
        """Return True when a descendant target lands under an empty schedule root."""
        if _addr_container(target) != "schedule" or len(target.path) <= 1:
            return False
        sched_label = target.path[0][1] if target.path else None
        if not sched_label:
            return False
        for sch in self.statute.supplements:
            if uk_match_kind_label(sch, "schedule", sched_label):
                return len(sch.children) == 0
        return False
