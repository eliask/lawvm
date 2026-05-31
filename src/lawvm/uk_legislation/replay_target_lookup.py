"""UK replay target lookup helpers."""

from __future__ import annotations

import re
from typing import NamedTuple, Optional, Protocol, cast

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_FALLBACK,
    TARGET_AMBIGUOUS,
    TARGET_RECOVERED,
    TargetResolutionCandidate,
    TargetResolutionCertificate,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name, _addr_container, _addr_leaf_kind, _addr_leaf_label, _uk_kind_value
from lawvm.uk_legislation.canonicalize import (
    UKCanonicalNodeMatch,
    canonicalize_uk_address,
    uk_compound_subsection_candidate,
    uk_recursive_kind_match,
    uk_recursive_kind_match_all,
    uk_schedule_ordinal_paragraph_matches,
    uk_schedule_root_candidates,
)
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_recovery_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult, TargetLookupKey, _RecursiveMatchAllKey
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
UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID = (
    "uk_replay_target_resolved_by_recursive_descent"
)
UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID = (
    "uk_replay_target_ambiguous_recursive_descent"
)


class _ExistingInsertTargetResolution(NamedTuple):
    node: Optional[UKMutableNode]
    parent: Optional[UKMutableNode]
    index: Optional[int]
    reason: str


class _ScheduleItemTargetCandidate(NamedTuple):
    node: UKMutableNode
    parent: UKMutableNode
    index: int


def _target_resolution_address_for_node(
    statute: UKMutableStatute,
    target_node: IRNode,
) -> str:
    """Return a diagnostic legal-address string for a mutable replay node."""

    def _walk(node: IRNode, path: tuple[tuple[str, str], ...]) -> str:
        if node is target_node:
            return str(LegalAddress(path=path))
        for child in node.children:
            child_path = path
            child_kind = _uk_kind_value(child.kind)
            if child_kind.lower() != "body":
                child_path = (*path, (child_kind, str(child.label or "")))
            found = _walk(child, child_path)
            if found:
                return found
        return ""

    for root in (statute.body, *statute.supplements):
        root_kind = _uk_kind_value(root.kind)
        root_path = ()
        if root_kind.lower() != "body":
            root_path = ((root_kind, str(root.label or "")),)
        found = _walk(root, root_path)
        if found:
            return found
    return ""


class _TargetLookupSelf(Protocol):
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]

    def _derive_target_eid(self, addr: LegalAddress) -> str: ...

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> NodeLookupResult: ...

    def _eid_candidate_matches_target_leaf(self, node: UKMutableNode, target: LegalAddress) -> bool: ...

    def _target_lookup_cache_key(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool,
        allow_recursive_match: bool,
    ) -> TargetLookupKey: ...

    def _cached_target_lookup(self, key: TargetLookupKey) -> NodeLookupResult | None: ...

    def _store_target_lookup_cache(self, key: TargetLookupKey, result: NodeLookupResult) -> None: ...

    def _recursive_match_cache_key(
        self,
        node: UKMutableNode,
        *,
        kind: str,
        label: str,
    ) -> tuple[int, str, str]: ...

    def _cached_recursive_match(self, key: tuple[int, str, str]) -> NodeLookupResult | None: ...

    def _store_recursive_match_cache(self, key: tuple[int, str, str], result: NodeLookupResult) -> None: ...

    def _cached_recursive_match_all(self, key: _RecursiveMatchAllKey) -> tuple[UKCanonicalNodeMatch, ...] | None: ...

    def _store_recursive_match_all_cache(self, key: _RecursiveMatchAllKey, matches: tuple[UKCanonicalNodeMatch, ...]) -> None: ...

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...

    def _find_compound_subsection_candidate(
        self,
        curr_node: UKMutableNode,
        label: str,
    ) -> UKCanonicalNodeMatch: ...

    def _find_recursive_match(
        self,
        node: UKMutableNode,
        kind: str,
        label: str,
    ) -> NodeLookupResult: ...


class UKReplayTargetLookupMixin:
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]

    def _find_existing_insert_target_by_explicit_parent_leaf(
        self: _TargetLookupSelf,
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
    ) -> UKCanonicalNodeMatch:
        """Match malformed UK shapes like legal subsection 8A stored as 8 -> a."""
        return uk_compound_subsection_candidate(
            cast(IRNode, curr_node),
            label,
            match_kind_label=uk_match_kind_label,
        )

    def _find_node_by_target(
        self: _TargetLookupSelf,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult:
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
                    result = NodeLookupResult(node=node, parent=parent, index=idx)
                    self._store_target_lookup_cache(cache_key, result)
                    return result

        def _find(address: LegalAddress) -> NodeLookupResult:
            path = list(address.path)
            container = _addr_container(address)

            # 1. Resolve top-level container
            roots: list[UKCanonicalNodeMatch] = []
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
                    return NodeLookupResult(node=cast(UKMutableNode, sch), parent=None, index=idx)
                if not sched_label and len(roots) == 1 and not remaining:
                    sch, _, idx = roots[0]
                    return NodeLookupResult(node=cast(UKMutableNode, sch), parent=None, index=idx)
                path = remaining
            else:
                roots = [UKCanonicalNodeMatch(cast(IRNode, self.statute.body), None, None)]
            if not roots:
                return NodeLookupResult(node=None, parent=None, index=None)

            is_eur = bool(self.statute.metadata.get("is_eur", False))
            curr_cands = roots
            for p_kind, p_label in path:
                next_cands: list[UKCanonicalNodeMatch] = []
                for curr_node, _, _ in curr_cands:
                    if curr_node is None:
                        continue
                    for i, child in enumerate(curr_node.children):
                        if is_eur:
                            nk = _uk_kind_value(child.kind).lower()
                            tk = str(p_kind).lower()
                            if nk == "paragraph" and tk == "subsection":
                                continue
                            if nk == "subsection" and tk == "paragraph":
                                continue
                        if uk_match_kind_label(child, p_kind, p_label):
                            next_cands.append(UKCanonicalNodeMatch(child, curr_node, i))
                    if not next_cands and allow_compound_subsection_alias and p_kind.lower() == "subsection" and p_label:
                        compound = self._find_compound_subsection_candidate(cast(UKMutableNode, curr_node), p_label)
                        if compound[0] is not None:
                            next_cands.append(compound)
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
                                    if resolved_node is None:
                                        continue
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
                        if allow_recursive_match:
                            all_recursive: list[UKCanonicalNodeMatch] = []
                            for curr_node, _, _ in curr_cands:
                                if curr_node is None:
                                    continue
                                # Short-circuit: once we have ≥2 matches we
                                # already know the result is ambiguous.
                                if len(all_recursive) >= 2:
                                    break
                                rma_key: _RecursiveMatchAllKey = (
                                    id(curr_node),
                                    str(p_kind),
                                    str(p_label),
                                )
                                cached_all = self._cached_recursive_match_all(rma_key)
                                if cached_all is not None:
                                    all_recursive.extend(cached_all)
                                else:
                                    per_node: list[UKCanonicalNodeMatch] = []
                                    uk_recursive_kind_match_all(
                                        cast(IRNode, curr_node),
                                        kind=str(p_kind),
                                        label=str(p_label),
                                        match_kind_label=uk_match_kind_label,
                                        out=per_node,
                                    )
                                    self._store_recursive_match_all_cache(
                                        rma_key, tuple(per_node)
                                    )
                                    all_recursive.extend(per_node)
                            if len(all_recursive) == 1:
                                res_node, res_p, res_i = all_recursive[0]
                                if target_resolution_op is not None:
                                    recovered_target = _target_resolution_address_for_node(
                                        self.statute,
                                        cast(IRNode, res_node),
                                    ) or str(target)
                                    _append_uk_replay_adjudication(
                                        self.adjudications_out,
                                        kind=UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID,
                                        message=(
                                            "UK replay resolved a target step by recursive "
                                            "descent because the direct path failed but exactly "
                                            "one deeper descendant matched the expected "
                                            "kind/label."
                                        ),
                                        op=target_resolution_op,
                                        detail=uk_replay_recovery_action_target_detail(
                                            target_resolution_op,
                                            target,
                                            family="target_resolution_recovery",
                                            recovered_kind=str(res_node.kind) if res_node is not None else "",
                                            recovered_label=str(res_node.label or "") if res_node is not None else "",
                                            original_target_path=str(target),
                                            recovered_path_step_kind=str(p_kind),
                                            recovered_path_step_label=str(p_label),
                                            recovered_target=recovered_target,
                                            target_resolution=TargetResolutionCertificate(
                                                rule_id=UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID,
                                                phase="replay",
                                                reason="unique_recursive_descendant_matched_failed_target_step",
                                                status=TARGET_RECOVERED,
                                                source_target=str(target),
                                                candidate_count=1,
                                                candidates=(
                                                    TargetResolutionCandidate(
                                                        target=recovered_target,
                                                        reason="recursive_descendant_kind_label_match",
                                                        detail={
                                                            "recovered_kind": (
                                                                str(res_node.kind) if res_node is not None else ""
                                                            ),
                                                            "recovered_label": (
                                                                str(res_node.label or "")
                                                                if res_node is not None
                                                                else ""
                                                            ),
                                                        },
                                                    ),
                                                ),
                                                selected_target=recovered_target,
                                                scope_confidence=SCOPE_CONFIDENCE_FALLBACK,
                                                blocking=False,
                                                strict_disposition="block",
                                                quirks_disposition="apply",
                                                detail={
                                                    "action": _action_name(target_resolution_op.action),
                                                    "op_id": target_resolution_op.op_id,
                                                    "recovery_family": "target_resolution_recovery",
                                                },
                                            ).to_diagnostic_detail(),
                                        ),
                                    )
                                next_cands.append(
                                    UKCanonicalNodeMatch(
                                        cast(IRNode, res_node),
                                        cast(Optional[IRNode], res_p),
                                        res_i,
                                    )
                                )
                            elif len(all_recursive) > 1:
                                candidate_paths = tuple(
                                    f"{str(m[0].kind)}:{str(m[0].label or '')}" if m[0] is not None else "?"
                                    for m in all_recursive
                                )
                                target_resolution_candidates = tuple(
                                    TargetResolutionCandidate(
                                        target=(
                                            _target_resolution_address_for_node(
                                                self.statute,
                                                cast(IRNode, match[0]),
                                            )
                                            or (
                                                f"{str(match[0].kind)}:{str(match[0].label or '')}"
                                                if match[0] is not None
                                                else "?"
                                            )
                                            if match[0] is not None
                                            else "?"
                                        ),
                                        reason="recursive_descendant_kind_label_match",
                                        detail={
                                            "recovered_kind": (
                                                str(match[0].kind) if match[0] is not None else ""
                                            ),
                                            "recovered_label": (
                                                str(match[0].label or "")
                                                if match[0] is not None
                                                else ""
                                            ),
                                        },
                                    )
                                    for match in all_recursive
                                )
                                if target_resolution_op is not None:
                                    _append_uk_replay_adjudication(
                                        self.adjudications_out,
                                        kind=UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID,
                                        message=(
                                            "UK replay refused recursive-descent target "
                                            "recovery: multiple descendants matched the "
                                            "expected kind/label — ambiguous, not applied."
                                        ),
                                        op=target_resolution_op,
                                        detail=uk_replay_action_target_detail(
                                            target_resolution_op,
                                            target,
                                            blocking=True,
                                            family="target_resolution_recovery",
                                            original_target_path=str(target),
                                            recovered_path_step_kind=str(p_kind),
                                            recovered_path_step_label=str(p_label),
                                            candidate_count=len(all_recursive),
                                            candidate_paths=candidate_paths,
                                            target_resolution=TargetResolutionCertificate(
                                                rule_id=UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID,
                                                phase="replay",
                                                reason="multiple_recursive_descendants_matched_failed_target_step",
                                                status=TARGET_AMBIGUOUS,
                                                source_target=str(target),
                                                candidate_count=len(all_recursive),
                                                candidates=target_resolution_candidates,
                                                scope_confidence=SCOPE_CONFIDENCE_FALLBACK,
                                                blocking=True,
                                                strict_disposition="block",
                                                quirks_disposition="record",
                                                detail={
                                                    "action": _action_name(target_resolution_op.action),
                                                    "op_id": target_resolution_op.op_id,
                                                    "recovery_family": "target_resolution_recovery",
                                                },
                                            ).to_diagnostic_detail(),
                                        ),
                                    )
                                # Do not populate next_cands — ambiguity means no resolution
                if not next_cands:
                    return NodeLookupResult(node=None, parent=None, index=None)
                curr_cands = next_cands
            if not curr_cands:
                return NodeLookupResult(node=None, parent=None, index=None)
            node, parent, idx = curr_cands[0]
            if node is None:
                return NodeLookupResult(node=None, parent=None, index=None)
            return NodeLookupResult(
                node=cast(UKMutableNode, node),
                parent=cast(Optional[UKMutableNode], parent),
                index=idx,
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
        self: _TargetLookupSelf,
        target: LegalAddress,
        op: LegalOperation,
    ) -> NodeLookupResult:
        """Resolve feed `Sch. N para. (d)` shape to a unique schedule item.

        This recovery is available only for ops whose lowering witness proved a
        source-parent sibling-range substitution. It does not authorize general
        schedule paragraph-to-item fallback.
        """
        if op.witness_rule_id != _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID:
            return NodeLookupResult(node=None, parent=None, index=None)
        if _addr_container(target) != "schedule" or len(tuple(target.path)) != 2:
            return NodeLookupResult(node=None, parent=None, index=None)
        schedule_label = target.path[0][1]
        target_kind, target_label_raw = target.path[1]
        target_label = _source_parent_range_label(target_label_raw)
        if target_kind != "paragraph" or not re.fullmatch(r"[a-z]", target_label, re.I):
            return NodeLookupResult(node=None, parent=None, index=None)
        if op.payload is not None:
            payload_kind = _uk_kind_value(op.payload.kind).lower()
            payload_label = _source_parent_range_label(op.payload.label or "")
            if payload_kind != "item" or payload_label != target_label:
                return NodeLookupResult(node=None, parent=None, index=None)

        roots = uk_schedule_root_candidates(
            cast(list[IRNode], self.statute.supplements),
            sched_label=schedule_label,
            remaining_path=(),
            match_kind_label=uk_match_kind_label,
        )
        candidates: list[_ScheduleItemTargetCandidate] = []

        def _walk(parent: UKMutableNode) -> None:
            for child_idx, child in enumerate(parent.children):
                if (
                    _uk_kind_value(child.kind).lower() == "item"
                    and _source_parent_range_label(child.label or "") == target_label
                ):
                    candidates.append(_ScheduleItemTargetCandidate(child, parent, child_idx))
                _walk(child)

        for root, _root_parent, _root_idx in roots:
            _walk(cast(UKMutableNode, root))
        if len(candidates) != 1:
            return NodeLookupResult(node=None, parent=None, index=None)
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
        return NodeLookupResult(
            node=recovered_node,
            parent=recovered_parent,
            index=recovered_idx,
        )

    def _find_recursive_match(
        self: _TargetLookupSelf, node: UKMutableNode, kind: str, label: str
    ) -> NodeLookupResult:
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
        typed_result = NodeLookupResult(
            node=cast(Optional[UKMutableNode], result[0]),
            parent=cast(Optional[UKMutableNode], result[1]),
            index=result[2],
        )
        self._store_recursive_match_cache(cache_key, typed_result)
        return typed_result

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
