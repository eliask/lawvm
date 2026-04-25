"""Shared runtime-support helpers for Finland replay/apply flows.

These helpers are reused by the executor, grafter compatibility surfaces, and
tests, but they are not themselves dispatch logic. Pulling them out of
``apply.py`` lets the replay kernel shrink while keeping the public helper
surface stable.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, FrozenSet, List, Optional, cast

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path

from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.apply_ir_ops import _build_repeal_placeholder_from_label_ir
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, ResolvedOp, ResolvedTargetScopeView, temporary_signal_for_op
from lawvm.finland.source_pathology import (
    build_container_replace_target_absent_pathology,
    build_destructive_shape_loss_risk_pathology,
)
if TYPE_CHECKING:
    from lawvm.core.compile_result import SourcePathology
    from lawvm.finland.migration_ledger import MigrationLedger
    from lawvm.finland.statute import ReplayState
    from lawvm.finland.payload_normalize import SubsectionSlotMap


def _legacy_target_section_for_scope(scope: "ResolvedTargetScopeView", unit_kind: TargetUnitKind) -> str:
    if unit_kind == "part":
        return str(scope.target_part or scope.target_norm)
    return scope.target_norm


def _legacy_target_special_for_scope(
    scope: "ResolvedTargetScopeView",
    effective_target_special: str | None,
) -> str | None:
    if scope.target_special == "otsikko":
        return effective_target_special
    return scope.target_special or effective_target_special


def _container_kind_for_name(kind_name: str) -> IRNodeKind | None:
    if kind_name == "section":
        return IRNodeKind.SECTION
    if kind_name == "chapter":
        return IRNodeKind.CHAPTER
    if kind_name == "part":
        return IRNodeKind.PART
    return None


def _legacy_dispatch_shell_for_rop(rop: "ResolvedOp") -> "AmendmentOp":
    """Project a late-waist op onto the narrow legacy apply shell.

    Keep this compatibility projection owned by the apply/runtime boundary,
    not by generic `ResolvedOp` consumers.
    """
    from lawvm.finland.ops import AmendmentOp, OpType

    scope = rop.resolved_target_scope_view
    # Mirror the computation in apply_subsection_ops._subsection_apply_view_for_op
    # so that subsection apply paths reading op.has_exact_bound_payload (legacy
    # AmendmentOp branch) see the same value as paths that compute it directly
    # from the ResolvedOp.  Without this the legacy shell projection silently
    # leaves the field at its dataclass default (False), creating an asymmetric
    # wiring gap between the two _subsection_apply_view_for_op input branches.
    mapped = rop.slot_assignment.for_stable_op_id(rop.op_id) if rop.slot_assignment is not None else None
    has_exact_bound_payload = (
        rop.slot_assignment is not None
        and rop.slot_assignment.has_owned_bound_payload_for_stable_op_id(rop.op_id)
    ) or (
        mapped is not None
        and scope.target_paragraph is not None
        and mapped.label is not None
        and _tops._norm(mapped.label) == str(scope.target_paragraph)
    )

    return AmendmentOp(
        op_id=rop.op_id,
        op_type=cast(OpType, rop.resolved_action_type),
        target_section=_legacy_target_section_for_scope(scope, rop.target_unit_kind),
        target_unit_kind=rop.target_unit_kind,
        target_chapter=scope.target_chapter,
        target_part=scope.target_part,
        target_paragraph=scope.target_paragraph,
        target_item=scope.target_item,
        target_special=_legacy_target_special_for_scope(scope, rop.effective_target_special),
        named_row_targets=rop.named_row_targets,
        body_root_replace_fallback=rop.body_root_replace_fallback,
        fallback_provenance=rop.fallback_provenance,
        source_statute=rop.resolved_source_statute,
        source_issue_date=rop.resolved_source_issue_date,
        source_title=rop.resolved_source_title,
        sec1_body_johto_fallback=rop.uses_sec1_body_johto_fallback,
        move_clause_target_unit_kind=rop.move_clause_target_unit_kind,
        uncovered_body_recovery=rop.uses_uncovered_body_recovery,
        voimaantulo_repeal=rop.voimaantulo_repeal,
        extraction_provenance_tags=rop.extraction_provenance_tags,
        target_guessing_provenance_tags=rop.target_guessing_provenance_tags,
        scope_provenance_tags=rop.scope_provenance_tags,
        scope_confidence=rop.scope_confidence,
        post_repeal_item_shift_label=rop.resolved_post_repeal_item_shift_label,
        body_chapter_move_from=rop.body_chapter_move_from,
        # Runtime dispatch shells must not carry parser-shell target authority.
        lo=None,
        is_temporary=temporary_signal_for_op(rop),
        has_exact_bound_payload=has_exact_bound_payload,
        temporal_activation=rop.temporal_activation,
        witness_rule_id=rop.witness_rule_id,
    )


def _unique_substantive_section_path(
    state: "ReplayState",
    target_norm: str,
) -> Path | None:
    label_norm = _tops._norm(target_norm)
    matches = [
        _tops._as_path(path)
        for path in state.provision_index.get(("section", label_norm), [])
    ]
    if len(matches) < 2:
        return None

    substantive_paths: list[Path] = []
    for path in matches:
        node = _tops.resolve(state.ir, path)
        if node is None:
            continue
        if node.attrs.get("lawvm_repeal_placeholder") == "1":
            continue
        substantive_paths.append(path)
    if len(substantive_paths) != 1:
        return None
    return substantive_paths[0]


def _prefer_unique_substantive_section_path_over_placeholder(
    state: "ReplayState",
    *,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    raw_path: Path | None,
) -> Path | None:
    """Prefer the lone live substantive same-label section over a tombstone slot.

    This is intentionally narrow: only unscoped section lookup/hints may be
    redirected, and only when the current candidate resolves to a repeal
    placeholder while exactly one substantive same-label section exists.
    """
    if raw_path is None:
        return None
    if target_chapter or target_part:
        return raw_path
    raw_node = _tops.resolve(state.ir, raw_path)
    if raw_node is None or raw_node.attrs.get("lawvm_repeal_placeholder") != "1":
        return raw_path
    substantive_path = _unique_substantive_section_path(state, target_norm)
    if substantive_path is None:
        return raw_path
    return substantive_path


def _snapshot_op_source(
    group_rops: List[ResolvedOp],
    amendment_id: str,
    source_title: str,
    source_issue_date: Optional[dt.date],
    source_effective_date: Optional[dt.date],
) -> OperationSource:
    for rop in group_rops:
        source = rop.resolved_op_source
        if source is not None:
            enacted = source.enacted or (source_issue_date.isoformat() if source_issue_date else "")
            effective = source.effective or (source_effective_date.isoformat() if source_effective_date else enacted)
            if enacted != source.enacted or effective != source.effective:
                return OperationSource(
                    statute_id=source.statute_id,
                    title=source.title,
                    enacted=enacted,
                    effective=effective,
                    expires=source.expires,
                    expires_original=source.expires_original,
                    expiry_chain=source.expiry_chain,
                    raw_text=source.raw_text,
                    corrected_by=source.corrected_by,
                    commencement_source=source.commencement_source,
                    commencement_title=source.commencement_title,
                )
            return source
    return OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=source_issue_date.isoformat() if source_issue_date else "",
        effective=source_effective_date.isoformat() if source_effective_date else (source_issue_date.isoformat() if source_issue_date else ""),
    )


def _op_source_for_merge_base(op: AmendmentOp | ResolvedOp) -> OperationSource | None:
    if isinstance(op, ResolvedOp):
        return op.resolved_op_source
    if op.lo is not None:
        return op.lo.source
    return None


def _section_node_from_base_ir(base_ir: IRNode | None, section_path: Path) -> IRNode | None:
    if base_ir is None:
        return None
    section_node = _tops.resolve(base_ir, section_path)
    if section_node is not None and section_node.kind is IRNodeKind.SECTION:
        return section_node
    labels = {kind: label for kind, label in section_path}
    section_label = labels.get("section")
    chapter_label = labels.get("chapter")
    if not section_label:
        return None
    resolved = _tops.find(
        base_ir,
        "section",
        section_label,
        scope_kind="chapter" if chapter_label else None,
        scope_label=chapter_label,
    )
    if resolved is None:
        return None
    section_node = _tops.resolve(base_ir, resolved)
    if section_node is not None and section_node.kind is IRNodeKind.SECTION:
        return section_node
    return None


def _section_snapshot_identity(path: Path) -> tuple[str, str, str]:
    labels = {kind: label for kind, label in path}
    return (
        _norm_num_token(labels.get("part") or ""),
        _norm_num_token(labels.get("chapter") or ""),
        _norm_num_token(labels.get("section") or ""),
    )


def _snapshot_section_los_for_identity(
    replay_history_ops: List[_LegalOperation] | None,
    target_identity: tuple[str, str, str],
) -> list[_LegalOperation]:
    """Return all snapshot_section_ LOs matching *target_identity*, in order.

    This replaces repeated reverse linear scans of replay_history_ops with
    a single indexed pass.  The result is cached on the list object itself
    so that multiple lookups for different identities reuse the same index.
    """
    if replay_history_ops is None:
        return []
    # Build or retrieve the per-identity index.
    # Stored as a hidden attribute on the list — lives as long as the list does,
    # automatically invalidated when a new list is created.
    _IDX_ATTR = "_snapshot_section_index"
    _LEN_ATTR = "_snapshot_section_index_len"
    idx: dict[tuple[str, str, str], list[int]] | None = getattr(
        replay_history_ops, _IDX_ATTR, None
    )
    idx_len: int = getattr(replay_history_ops, _LEN_ATTR, 0)
    cur_len = len(replay_history_ops)
    if idx is None:
        # First build — scan everything
        idx = {}
        start = 0
    elif idx_len < cur_len:
        # Incremental update — only scan new entries
        start = idx_len
    else:
        start = cur_len  # no scan needed
    if start < cur_len:
        for i in range(start, cur_len):
            lo = replay_history_ops[i]
            if not lo.op_id.startswith("snapshot_section_"):
                continue
            if lo.payload is None or lo.payload.kind is not IRNodeKind.SECTION:
                continue
            ident = _section_snapshot_identity(lo.target.path)
            idx.setdefault(ident, []).append(i)
        try:
            object.__setattr__(replay_history_ops, _IDX_ATTR, idx)
            object.__setattr__(replay_history_ops, _LEN_ATTR, cur_len)
        except (TypeError, AttributeError):
            pass
    indices = idx.get(target_identity)
    if not indices:
        return []
    return [replay_history_ops[i] for i in indices]


def _prior_non_temporary_section_snapshot_payload(
    *,
    section_path: Path,
    replay_history_ops: List[_LegalOperation] | None,
    current_effective: str,
    base_ir: IRNode | None,
) -> IRNode | None:
    """Return the permanent section payload that predates an expired temp snapshot."""
    if replay_history_ops is None or not current_effective:
        return None

    target_identity = _section_snapshot_identity(section_path)
    matches = _snapshot_section_los_for_identity(replay_history_ops, target_identity)
    if not matches:
        return None

    latest_snapshot = matches[-1]

    if latest_snapshot.source is None:
        return None
    latest_expires = latest_snapshot.source.expires or ""
    if not latest_expires or current_effective <= latest_expires:
        return None

    # Walk backwards from second-to-last looking for a non-temporary snapshot
    for lo in reversed(matches[:-1]):
        if lo.source is None or not (lo.source.expires or ""):
            return lo.payload

    return _section_node_from_base_ir(base_ir, section_path)


def _latest_section_snapshot_payload(
    *,
    section_path: Path,
    replay_history_ops: List[_LegalOperation] | None,
) -> _LegalOperation | None:
    if replay_history_ops is None:
        return None
    target_identity = _section_snapshot_identity(section_path)
    matches = _snapshot_section_los_for_identity(replay_history_ops, target_identity)
    return matches[-1] if matches else None


def _previous_section_snapshot_payload(
    *,
    section_path: Path,
    replay_history_ops: List[_LegalOperation] | None,
) -> _LegalOperation | None:
    if replay_history_ops is None:
        return None
    target_identity = _section_snapshot_identity(section_path)
    matches = _snapshot_section_los_for_identity(replay_history_ops, target_identity)
    return matches[-2] if len(matches) >= 2 else None


def _expired_temporary_section_merge_base(
    *,
    op: AmendmentOp | ResolvedOp,
    section_path: Path,
    replay_history_ops: List[_LegalOperation] | None,
    base_ir: IRNode | None,
    current_live_section: IRNode | None = None,
) -> IRNode | None:
    """Return a safer structural merge base for expired temporary section state.

    Finland replay folds a single mutable tree through the amendment chain.
    When the latest snapshot for a section is temporary but already expired by
    the current permanent op's effective date, sparse merges must not build on
    that contaminated live section. In that case we fall back to the latest
    earlier non-temporary snapshot for the same section, or to the base statute
    section if no permanent snapshot exists yet.
    """
    if replay_history_ops is None or temporary_signal_for_op(op):
        return None
    source = _op_source_for_merge_base(op)
    current_effective = ((source.effective if source is not None else "") or (source.enacted if source is not None else "") or "")
    latest_snapshot = _latest_section_snapshot_payload(
        section_path=section_path,
        replay_history_ops=replay_history_ops,
    )
    if latest_snapshot is not None and latest_snapshot.source is not None:
        latest_expires = latest_snapshot.source.expires or ""
        if latest_expires and current_effective > latest_expires:
            if current_live_section is not None and latest_snapshot.payload != current_live_section:
                return current_live_section
            return _prior_non_temporary_section_snapshot_payload(
                section_path=section_path,
                replay_history_ops=replay_history_ops,
                current_effective=current_effective,
                base_ir=base_ir,
            )
        if current_live_section is not None and latest_snapshot.payload != current_live_section:
            previous_snapshot = _previous_section_snapshot_payload(
                section_path=section_path,
                replay_history_ops=replay_history_ops,
            )
            if previous_snapshot is not None and previous_snapshot.source is not None:
                if previous_snapshot.source.expires:
                    # Only rebase to the latest permanent snapshot when the live
                    # section IS the expired-temp state.  If current_live has
                    # diverged beyond previous_snapshot.payload it was legitimately
                    # modified by current-wave ops in the same amendment group and
                    # must not be overwritten.
                    if current_live_section == previous_snapshot.payload:
                        return latest_snapshot.payload
    return _prior_non_temporary_section_snapshot_payload(
        section_path=section_path,
        replay_history_ops=replay_history_ops,
        current_effective=current_effective,
        base_ir=base_ir,
    )


def _expired_temporary_section_merge_base_rebase_info(
    *,
    op: AmendmentOp | ResolvedOp,
    section_path: Path,
    replay_history_ops: List[_LegalOperation] | None,
    current_live_section: IRNode | None = None,
) -> tuple[str | None, str | None]:
    """Classify whether the temporary merge-base fallback rebased to a safe live snapshot."""
    if replay_history_ops is None or temporary_signal_for_op(op):
        return None, None
    source = _op_source_for_merge_base(op)
    current_effective = ((source.effective if source is not None else "") or (source.enacted if source is not None else "") or "")
    latest_snapshot = _latest_section_snapshot_payload(
        section_path=section_path,
        replay_history_ops=replay_history_ops,
    )
    if latest_snapshot is None or latest_snapshot.source is None:
        return None, None
    latest_expires = latest_snapshot.source.expires or ""
    if latest_expires and current_effective > latest_expires:
        if current_live_section is not None and latest_snapshot.payload != current_live_section:
            return "expired_latest_snapshot_current_live_section", latest_expires
        return "expired_latest_snapshot_prior_non_temporary_snapshot", latest_expires
    if current_live_section is not None and latest_snapshot.payload != current_live_section:
        previous_snapshot = _previous_section_snapshot_payload(
            section_path=section_path,
            replay_history_ops=replay_history_ops,
        )
        if previous_snapshot is not None and previous_snapshot.source is not None:
            if previous_snapshot.source.expires:
                if current_live_section == previous_snapshot.payload:
                    return "temporary_previous_snapshot_latest_snapshot", latest_expires
    return None, None


def _resolved_destination_path_for_rop(rop: ResolvedOp) -> Optional[Path]:
    """Best-effort full destination path for a renumbered late-waist op."""
    if not rop.is_renumber_action:
        return None
    destination_address = rop.resolved_destination_address
    if destination_address is None:
        return None
    source_address = rop.resolved_target_address
    source_path = source_address.path if source_address is not None else ()
    if source_path:
        dest_leaf_kind = source_path[-1][0]
        return source_path[:-1] + ((dest_leaf_kind, destination_address.leaf_label()),)
    if destination_address.path:
        return destination_address.path
    return None


def _snapshot_op_id(target_unit_kind: TargetUnitKind, target_norm: str) -> str:
    """Return the neutral snapshot op id for one structural target."""
    return f"snapshot_{target_unit_kind}_{target_norm}"


def _container_child_snapshot_op_id(
    child_label: str,
    *,
    parent_unit_kind: TargetUnitKind,
    parent_norm: str,
) -> str:
    """Return the neutral child snapshot op id emitted from a container snapshot."""
    return f"snapshot_section_{child_label}_from_{parent_unit_kind}_{parent_norm}"


def _stamp_exact_section_snapshot_payload(payload: IRNode) -> IRNode:
    """Mark a section snapshot as owning its full child surface exactly.

    Container-derived section snapshots are emitted as standalone section
    timeline entries. When a chapter/part replacement projects one child
    section into its own snapshot rail, that section snapshot must carry the
    same exact-tail ownership semantics as a direct whole-section replace.
    Otherwise PIT may silently graft stale base descendants back underneath
    the newer section root.
    """
    if payload.kind is not IRNodeKind.SECTION:
        return payload
    attrs = dict(payload.attrs)
    if attrs.get("lawvm_tail_policy") == "replace_if_target_scope_requires":
        return payload
    attrs["lawvm_tail_policy"] = "replace_if_target_scope_requires"
    attrs["lawvm_payload_completeness_kind"] = "complete"
    return IRNode(
        kind=payload.kind,
        label=payload.label,
        text=payload.text,
        attrs=attrs,
        children=payload.children,
    )


def _is_rebased_sparse_subsection_surface_exact(payload: IRNode, group_rops: List[ResolvedOp]) -> bool:
    """Return True when sparse omission alignment produced an exact section surface.

    Historical absorbed-moment cases can replace a first and final subsection
    while the omission-expanded payload owns the whole contiguous subsection
    range between them. In that family, a section snapshot must mask older child
    timelines outside the rebased range; otherwise PIT grafts stale tail
    subsections back under the correct post-apply section root.
    """
    if payload.kind is not IRNodeKind.SECTION:
        return False
    target_labels: set[int] = set()
    for rop in group_rops:
        if not rop.is_replace_action or not rop.targets_subsection_only():
            return False
        label = str(rop.resolved_target_subsection_label or rop.target_paragraph or "").strip()
        if not label.isdigit():
            return False
        target_labels.add(int(label))
    if len(target_labels) < 2:
        return False

    payload_labels: list[int] = []
    for child in payload.children:
        if child.kind is not IRNodeKind.SUBSECTION:
            continue
        label = str(child.label or "").strip()
        if not label.isdigit():
            return False
        payload_labels.append(int(label))
    if not payload_labels:
        return False
    max_label = max(payload_labels)
    if payload_labels != list(range(1, max_label + 1)):
        return False
    return 1 in target_labels and max_label in target_labels


def _section_child_snapshot_op_id(
    child_label: str,
    *,
    parent_norm: str,
) -> str:
    """Return the neutral child snapshot op id emitted from a section snapshot."""
    return f"snapshot_subsection_{child_label}_from_section_{parent_norm}"


def _payload_contains_relative_target(payload: IRNode, relative_path: Path) -> bool:
    node = payload
    for kind_name, label in relative_path:
        child = next(
            (
                candidate
                for candidate in node.children
                if _kind_str(candidate.kind) == kind_name and _same_norm_label(candidate.label, label)
            ),
            None,
        )
        if child is None:
            return False
        node = child
    return True


def _timeline_target_exists(
    target_path: Path,
    *,
    replay_history_ops: List[_LegalOperation],
    base_ir: IRNode | None,
    before_effective: str = "",
) -> bool:
    """Return True if target_path already exists in base or prior emitted replay history."""
    if base_ir is not None and _tops.resolve(base_ir, target_path) is not None:
        return True
    for lo in replay_history_ops:
        lo_effective = lo.source.effective if lo.source is not None else ""
        if before_effective and lo_effective and lo_effective >= before_effective:
            continue
        if lo.target.path == target_path:
            return True
        if (
            lo.payload is not None
            and len(lo.target.path) < len(target_path)
            and target_path[: len(lo.target.path)] == lo.target.path
            and _payload_contains_relative_target(lo.payload, target_path[len(lo.target.path) :])
        ):
            return True
    return False


def _emit_section_snapshot(
    state: "ReplayState",
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    group_rops: List[ResolvedOp],
    lo_ops_out: List[_LegalOperation],
    amendment_id: str,
    source_title: str,
    source_issue_date: Optional[dt.date],
    source_effective_date: Optional[dt.date],
    base_ir: Optional[IRNode] = None,
    path_hint: Optional[Path] = None,
    migration_ledger: Optional["MigrationLedger"] = None,
    standalone_section_targets: "FrozenSet[tuple[str | None, str]] | None" = None,
    source_pathologies_out: Optional[List["SourcePathology"]] = None,
) -> None:
    """Emit a section/chapter-level snapshot to lo_ops_out after ops are applied."""
    action: StructuralAction = StructuralAction.REPLACE
    normalized_target_norm = _norm_num_token(target_norm)

    op_source = _snapshot_op_source(group_rops, amendment_id, source_title, source_issue_date, source_effective_date)

    def _timeline_path(tree_path: Path) -> Path:
        return tuple((k, v) for k, v in tree_path if v)

    resolved_path: Optional[Path] = None
    payload: Optional[IRNode] = None
    payload_from_muutos_ir = False

    def _use_root_address_for_pseudo_chapter_section() -> bool:
        if target_unit_kind != "section" or not target_chapter or migration_ledger is not None:
            return False
        scoped_path = state.find_section_path(normalized_target_norm, target_chapter, target_part)
        if scoped_path is not None:
            return False
        if base_ir is not None:
            base_chapter_path = _tops.find(base_ir, "chapter", target_chapter)
            if base_chapter_path is not None:
                return False
        chapter_path = state.find("chapter", target_chapter)
        return chapter_path is not None and any(kind == "hcontainer" for kind, _label in _tops._as_path(chapter_path))

    def _project_snapshot_path(path: Optional[Path]) -> Optional[Path]:
        if not path:
            return path
        if migration_ledger is not None:
            addr = LegalAddress(path=path)
            migrated = migration_ledger.current_address_with_prefix_migrations(addr)
            return migrated.path
        if _use_root_address_for_pseudo_chapter_section():
                return tuple(
                    (kind, label)
                    for kind, label in path
                    if kind in {"section", "subsection", "item"} and label
                )
        return path

    for rop in group_rops:
        dest_path = _resolved_destination_path_for_rop(rop)
        if dest_path is None:
            continue
        emitted_path = _project_snapshot_path(dest_path)
        if emitted_path is None:
            continue
        payload = _tops.resolve(state.ir, emitted_path)
        if payload is None:
            payload = _tops.resolve(state.ir, dest_path)
        if payload is not None:
            resolved_path = _timeline_path(emitted_path)
            break

    def _candidate_lookup_labels() -> list[str]:
        labels: list[str] = []
        if target_unit_kind in {"chapter", "part"}:
            for rop in group_rops:
                raw_label = rop.target_norm or ""
                if raw_label and raw_label not in labels:
                    labels.append(raw_label)
        for label in (target_norm, normalized_target_norm):
            if label and label not in labels:
                labels.append(label)
        return labels

    def _unique_global_section_path(label: str) -> Optional[Path]:
        idx = state.provision_index
        raw_path = _tops.find(state.ir, "section", label, label_index=idx)
        if raw_path is None:
            return None
        label_norm = _tops._norm(label)
        if len(idx.get(("section", label_norm), [])) != 1:
            return None
        return _tops._as_path(raw_path)

    def _group_payload_kind() -> Optional[str]:
        return target_unit_kind if target_unit_kind in {"section", "chapter", "part"} else None

    def _whole_target_repeal() -> bool:
        if target_unit_kind in {"section", "chapter", "part"}:
            return any(rop.is_repeal_action and rop.targets_whole_unit(target_unit_kind) for rop in group_rops)
        return False

    def _whole_target_renumber_without_payload() -> bool:
        if payload is not None:
            return False
        if target_unit_kind not in {"section", "chapter", "part"}:
            return False
        return any(rop.is_renumber_action and rop.targets_whole_unit(target_unit_kind) for rop in group_rops)

    def _all_group_ops_are_repeal() -> bool:
        return bool(group_rops) and all(rop.is_repeal_action for rop in group_rops)

    def _moved_from_chapter() -> str | None:
        seen: list[str] = []
        for rop in group_rops:
            typed_chapter = str(getattr(rop, "body_chapter_move_from", "") or "").strip()
            if typed_chapter and typed_chapter not in seen:
                seen.append(typed_chapter)
        return seen[0] if len(seen) == 1 else None

    def _empty_container_insert_without_payload() -> bool:
        return (
            payload is None
            and target_unit_kind in {"chapter", "part"}
            and bool(group_rops)
            and all(rop.is_insert_action for rop in group_rops)
        )

    def _find_normalized_container_path_in_tree(tree: IRNode, kind_name: str) -> Optional[Path]:
        kind_enum = _container_kind_for_name(kind_name)

        def _search(node: IRNode, prefix: Path) -> Optional[Path]:
            for child in node.children:
                child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
                kind_matches = (
                    kind_enum is not None
                    and child.kind is kind_enum
                    or kind_enum is None
                    and _kind_str(child.kind) == kind_name
                )
                if (
                    kind_matches
                    and child.label
                    and _norm_num_token(child.label) == normalized_target_norm
                ):
                    return child_path
                found = _search(child, child_path)
                if found is not None:
                    return found
            return None

        return _search(tree, ())

    def _lookup_container_path_in_tree(tree: IRNode, kind_name: str) -> Optional[Path]:
        for label in _candidate_lookup_labels():
            raw_path = _tops.find(tree, kind_name, label)
            if raw_path:
                return _tops._as_path(raw_path)
        return _find_normalized_container_path_in_tree(tree, kind_name)

    def _base_resolved_path() -> Optional[Path]:
        if base_ir is None:
            return None
        if hinted_path is not None:
            hinted_node = _tops.resolve(base_ir, hinted_path)
            expected_kind = (
                IRNodeKind.SECTION
                if target_unit_kind == "section"
                else IRNodeKind.CHAPTER
                if target_unit_kind == "chapter"
                else IRNodeKind.PART
                if target_unit_kind == "part"
                else None
            )
            if hinted_node is not None and (expected_kind is None or hinted_node.kind is expected_kind):
                return _timeline_path(hinted_path)
        if target_unit_kind == "section":
            if target_part:
                part_path = _tops.find(base_ir, "part", target_part)
                part_node = _tops.resolve(base_ir, part_path) if part_path is not None else None
                if part_path is not None and part_node is not None:
                    if target_chapter:
                        chapter_path = _tops.find(part_node, "chapter", target_chapter)
                        chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                        if chapter_path is not None and chapter_node is not None:
                            section_path = _tops.find(chapter_node, "section", normalized_target_norm)
                            raw_path = (
                                _tops._as_path(part_path)
                                + _tops._as_path(chapter_path)
                                + _tops._as_path(section_path)
                                if section_path is not None
                                else None
                            )
                        else:
                            raw_path = None
                    else:
                        section_path = _tops.find(part_node, "section", normalized_target_norm)
                        raw_path = _tops._as_path(part_path) + _tops._as_path(section_path) if section_path is not None else None
                else:
                    raw_path = None
            else:
                raw_path = _tops.find(
                    base_ir,
                    "section",
                    normalized_target_norm,
                    scope_kind="chapter" if target_chapter else None,
                    scope_label=target_chapter,
                )
        elif target_unit_kind == "chapter":
            raw_path = _lookup_container_path_in_tree(base_ir, "chapter")
        elif target_unit_kind == "part":
            raw_path = _lookup_container_path_in_tree(base_ir, "part")
        else:
            raw_path = None
        return _timeline_path(_tops._as_path(raw_path)) if raw_path else None

    def _base_container_payload() -> Optional[IRNode]:
        if base_ir is None or target_unit_kind not in {"chapter", "part"}:
            return None
        kind_name = "chapter" if target_unit_kind == "chapter" else "part"
        raw_path = _lookup_container_path_in_tree(base_ir, kind_name)
        if raw_path is None:
            return None
        return _tops.resolve(base_ir, raw_path)

    def _current_container_payload() -> Optional[IRNode]:
        if target_unit_kind not in {"chapter", "part"}:
            return None
        kind_name = "chapter" if target_unit_kind == "chapter" else "part"
        raw_path = _lookup_container_path_in_tree(state.ir, kind_name)
        if raw_path is None:
            return None
        return _tops.resolve(state.ir, raw_path)

    def _scoped_commencement_replay_owned_address() -> bool:
        """True when a scoped commencement updates a replay-introduced address.

        Finland scoped section commencements rewrite the effective date on the
        emitted snapshot op to the section-specific start date while the
        amendment itself still has an earlier statute-level effective date.
        When such an update targets a section/subsection that does not exist in
        the original base statute but does already exist in replay history,
        timeline compilation must keep the snapshot on the INSERT rail. A
        REPLACE at an address introduced only by earlier replay snapshots can be
        dropped from the exact-address timeline lane.
        """
        if target_unit_kind != "section":
            return False
        if source_effective_date is None:
            return False
        effective_iso = source_effective_date.isoformat()
        if not op_source.effective or op_source.effective == effective_iso:
            return False
        if not resolved_path:
            return False
        return (
            _base_resolved_path() is None
            and _timeline_target_exists(
                tuple(resolved_path),
                replay_history_ops=lo_ops_out,
                base_ir=base_ir,
                before_effective=op_source.effective,
            )
        )

    hinted_path = _valid_target_group_path_hint(
        state,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
        path_hint,
    )
    if hinted_path is not None:
        emitted_path = _project_snapshot_path(hinted_path)
        if emitted_path is None:
            emitted_path = path_hint
        if emitted_path is not None:
            payload = _tops.resolve(state.ir, emitted_path)
            if payload is None:
                payload = _tops.resolve(state.ir, hinted_path)
        else:
            payload = None
        if payload is not None and emitted_path is not None:
            resolved_path = _timeline_path(emitted_path)
    elif target_unit_kind == "section":
        raw_path = state.find_section_path(normalized_target_norm, target_chapter, target_part)
        if raw_path and not target_chapter:
            raw_node = _tops.resolve(state.ir, raw_path)
            if raw_node is not None and raw_node.attrs.get("lawvm_repeal_placeholder") == "1":
                substantive_path = _unique_substantive_section_path(state, normalized_target_norm)
                if substantive_path is not None:
                    raw_path = substantive_path
        if not raw_path and _whole_target_repeal():
            # The REPEAL op already removed the section from the IR before this
            # snapshot is called.  Scan the accumulated lo_ops_out in reverse to
            # find the most-recent snapshot for this section in the correct
            # chapter scope — that path IS the canonical timeline address for
            # the tombstone.  Avoids misrouting to a homonymous section in a
            # different chapter (e.g. part:6/chapter:21/section:8a when the
            # repeal targets part:1/chapter:1/section:8a).
            for _prev_lo in reversed(lo_ops_out):
                if _prev_lo.target.special is not None:
                    continue
                if not _prev_lo.target.path or _prev_lo.target.path[-1][0] != "section":
                    continue
                if _norm_num_token(_prev_lo.target.path[-1][1]) != normalized_target_norm:
                    continue
                if target_chapter:
                    _prev_chapters = [seg[1] for seg in _prev_lo.target.path if seg[0] == "chapter"]
                    if _prev_chapters and _prev_chapters[-1] != target_chapter:
                        continue
                raw_path = _prev_lo.target.path
                break
        if not raw_path and not target_chapter:
            raw_path = _unique_global_section_path(normalized_target_norm)
        if not raw_path and target_chapter and not target_part:
            # Cross-chapter/root-level unique global fallback: Finnish amendments
            # sometimes group sections under a chapter heading (e.g. "5 luku") that
            # differs from where the section actually lives in the live statute
            # (e.g. root hcontainer level). For REPLACE/REPEAL ops, apply the same
            # cross-chapter fallback as apply_policy.py.
            # Guard: skip if target_part is set — a part mismatch is an authoritative
            # scoping signal that must not be bypassed.
            _is_non_insert = group_rops and all(not rop.is_insert_action for rop in group_rops)
            if _is_non_insert:
                raw_path = _unique_global_section_path(normalized_target_norm)
        if raw_path:
            emitted_path = _project_snapshot_path(raw_path) or raw_path
            payload = _tops.resolve(state.ir, emitted_path)
            if payload is None:
                payload = _tops.resolve(state.ir, raw_path)
            if payload is not None:
                resolved_path = _timeline_path(emitted_path)
            elif _whole_target_repeal():
                # Section was already removed from the IR by the REPEAL op.
                # Anchor the tombstone to the path where the section previously
                # lived, even though payload cannot be resolved from current IR.
                resolved_path = _timeline_path(emitted_path)
    elif target_unit_kind == "chapter":
        raw_path = _lookup_container_path_in_tree(state.ir, "chapter")
        if raw_path:
            emitted_path = _project_snapshot_path(raw_path) or raw_path
            payload = _tops.resolve(state.ir, emitted_path)
            if payload is None:
                payload = _tops.resolve(state.ir, raw_path)
            if payload is not None:
                resolved_path = _timeline_path(emitted_path)
    elif target_unit_kind == "part":
        raw_path = _lookup_container_path_in_tree(state.ir, "part")
        if raw_path:
            emitted_path = _project_snapshot_path(raw_path) or raw_path
            payload = _tops.resolve(state.ir, emitted_path)
            if payload is None:
                payload = _tops.resolve(state.ir, raw_path)
            if payload is not None:
                resolved_path = _timeline_path(emitted_path)

    if payload is None and not _all_group_ops_are_repeal():
        expected_kind = _group_payload_kind()
        if expected_kind is not None:
            for rop in group_rops:
                if rop.muutos_ir is None or _kind_str(rop.muutos_ir.kind) != expected_kind:
                    continue
                if rop.muutos_ir.label and _norm_num_token(rop.muutos_ir.label) != normalized_target_norm:
                    continue
                payload = rop.muutos_ir
                payload_from_muutos_ir = True
                break

    if resolved_path is None:
        if target_unit_kind == "section":
            raw_candidates = [normalized_target_norm]
            if target_norm not in raw_candidates:
                raw_candidates.insert(0, target_norm)
            for label in raw_candidates:
                raw_path = state.find_section_path(label, target_chapter, target_part)
                if not raw_path and not target_chapter:
                    raw_path = _unique_global_section_path(label)
                if not raw_path and target_chapter and not target_part:
                    _is_non_insert = group_rops and all(not rop.is_insert_action for rop in group_rops)
                    if _is_non_insert:
                        raw_path = _unique_global_section_path(label)
                if raw_path:
                    emitted_path = _project_snapshot_path(raw_path) or raw_path
                    payload = _tops.resolve(state.ir, emitted_path)
                    if payload is None:
                        payload = _tops.resolve(state.ir, raw_path)
                    if payload is not None:
                        resolved_path = _timeline_path(emitted_path)
                        break
        else:
            kind_name = "chapter" if target_unit_kind == "chapter" else "part"
            for label in _candidate_lookup_labels():
                raw_path = state.find(kind_name, label)
                if raw_path:
                    emitted_path = _project_snapshot_path(raw_path) or raw_path
                    payload = _tops.resolve(state.ir, emitted_path)
                    if payload is None:
                        payload = _tops.resolve(state.ir, raw_path)
                    if payload is not None:
                        resolved_path = _timeline_path(emitted_path)
                        break
            if resolved_path is None:
                resolved_path = _base_resolved_path()

    if resolved_path is None:
        if (
            payload is not None
            and payload_from_muutos_ir
            and target_unit_kind == "section"
            and action is StructuralAction.REPLACE
            and group_rops
            and all(rop.is_replace_action for rop in group_rops)
            and not _whole_target_repeal()
        ):
            return
        resolved_path = ()
        if target_unit_kind == "section":
            if target_part:
                resolved_path = resolved_path + (("part", target_part),)
            if target_chapter and not _use_root_address_for_pseudo_chapter_section():
                resolved_path = resolved_path + (("chapter", target_chapter),)
            resolved_path = resolved_path + (("section", normalized_target_norm),)
        elif target_unit_kind == "chapter":
            resolved_path = resolved_path + (("chapter", normalized_target_norm),)
        elif target_unit_kind == "part":
            resolved_path = resolved_path + (("part", normalized_target_norm),)
        else:
            return

    if payload is None and _whole_target_repeal():
        if target_unit_kind == "section":
            sec1_fallback_repeal = any(rop.uses_sec1_body_johto_fallback for rop in group_rops)
            base_path = None
            if base_ir is not None:
                base_path = _tops.find(
                    base_ir,
                    "section",
                    target_norm,
                    scope_kind="chapter" if target_chapter else None,
                    scope_label=target_chapter,
                )
            if base_path is not None:
                resolved_path = _timeline_path(_tops._as_path(base_path))
                payload = _build_repeal_placeholder_from_label_ir(
                    target_norm,
                    op_source.statute_id,
                    source_issue_date,
                    op_source.title,
                )
            elif sec1_fallback_repeal:
                action = StructuralAction.REPEAL
            else:
                payload = _build_repeal_placeholder_from_label_ir(
                    target_norm,
                    op_source.statute_id,
                    source_issue_date,
                    op_source.title,
                )
        elif target_unit_kind in {"chapter", "part"}:
            action = StructuralAction.REPEAL

    if payload is None and _whole_target_renumber_without_payload():
        action = StructuralAction.REPEAL

    if payload is None and _all_group_ops_are_repeal():
        action = StructuralAction.REPEAL

    if _empty_container_insert_without_payload():
        if resolved_path:
            payload = _tops.resolve(state.ir, resolved_path)
            if payload is None and base_ir is not None:
                payload = _tops.resolve(base_ir, resolved_path)
        if payload is None:
            return


    if payload is None and action == StructuralAction.REPLACE and target_unit_kind in {"chapter", "part"}:
        _base_path = _base_resolved_path()
        if _base_path is None:
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_container_replace_target_absent_pathology(
                        source_statute=op_source.statute_id,
                        target_unit_kind=target_unit_kind,
                        target_section=target_norm,
                        target_chapter=target_chapter or "",
                        has_payload=False,
                    )
                )
            return

    # When a chapter/part snapshot is creating a brand-new container relative
    # to the base statute, timeline compilation must see an INSERT so the
    # container becomes a real executable address instead of being dropped by
    # the "replace target must already exist" guard.
    #
    # Guard: if replay history already owns this container address, keep the
    # snapshot as REPLACE. Historically-added chapters can later be wholly
    # replaced, and downgrading those waves to INSERT suppresses the child
    # repeal snapshots needed to retire stale descendants from PIT products.
    if (
        payload is not None
        and action is StructuralAction.REPLACE
        and target_unit_kind in {"chapter", "part"}
        and _base_resolved_path() is None
        and not _timeline_target_exists(
            tuple(resolved_path),
            replay_history_ops=lo_ops_out,
            base_ir=base_ir,
        )
    ):
        action = StructuralAction.INSERT

    if (
        target_unit_kind == "section"
        and target_chapter
        and len(state.provision_index.get(("section", _tops._norm(normalized_target_norm)), [])) == 1
    ):
        current_path = tuple(resolved_path)
        prior_path: tuple[tuple[str, str], ...] | None = None
        for lo in reversed(lo_ops_out):
            if lo.target.special is not None:
                continue
            if not lo.target.path or lo.target.path[-1] != ("section", target_norm):
                continue
            if lo.target.path == current_path:
                continue
            prior_path = lo.target.path
            break
        if prior_path is None and base_ir is not None:
            base_raw_path = _tops.find(base_ir, "section", target_norm)
            if base_raw_path is not None:
                base_path = tuple(_timeline_path(base_raw_path))
                if base_path != current_path:
                    prior_path = base_path
        if prior_path is not None and _tops.resolve(state.ir, prior_path) is not None:
            prior_path = None
        prior_is_root = prior_path is not None and not any(kind == "chapter" for kind, _label in prior_path)
        should_emit_move_repeal = prior_path is not None and (
            prior_is_root or not (group_rops and all(rop.is_insert_action for rop in group_rops))
        )
        if should_emit_move_repeal:
            assert prior_path is not None
            lo_ops_out.append(
                _LegalOperation(
                    op_id=f"snapshot_move_repeal_{target_norm}",
                    sequence=0,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=prior_path),
                    payload=_build_repeal_placeholder_from_label_ir(
                        target_norm,
                        op_source.statute_id,
                        source_issue_date,
                        op_source.title,
                    ),
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id or 'unknown'}",
                )
            )

    _is_repeal_snapshot = action == StructuralAction.REPEAL or (
        payload is not None and payload.attrs.get("lawvm_repeal_placeholder") == "1"
    )
    if _is_repeal_snapshot and op_source.expires:
        op_source = OperationSource(
            statute_id=op_source.statute_id,
            title=op_source.title,
            enacted=op_source.enacted,
            effective=op_source.effective,
            expires="",
            raw_text=op_source.raw_text,
            corrected_by=op_source.corrected_by,
            commencement_source=op_source.commencement_source,
            commencement_title=op_source.commencement_title,
        )

    base_path = _base_resolved_path()
    if (
        action is StructuralAction.REPLACE
        and payload is not None
        and base_path is None
        and _timeline_target_exists(
            tuple(resolved_path),
            replay_history_ops=lo_ops_out,
            base_ir=base_ir,
        )
    ):
        base_path = tuple(resolved_path)
    if (
        action is StructuralAction.REPLACE
        and payload is not None
        and _scoped_commencement_replay_owned_address()
    ):
        action = StructuralAction.INSERT
    if action is StructuralAction.REPLACE and payload is not None and base_path is None:
        # A snapshot with real payload but no base path is a newly introduced
        # structural node. Emit it as INSERT so timeline materialization can
        # seed a version for the node instead of silently dropping it.
        action = StructuralAction.INSERT
    if (
        action is StructuralAction.REPLACE
        and payload is not None
        and target_unit_kind == "section"
        and payload.kind is IRNodeKind.SECTION
        and not payload_from_muutos_ir
        and (
            payload.attrs.get("lawvm_tail_policy") == "replace_if_target_scope_requires"
            or _is_rebased_sparse_subsection_surface_exact(payload, group_rops)
        )
    ):
        payload = _stamp_exact_section_snapshot_payload(payload)

    lo_ops_out.append(
        _LegalOperation(
            op_id=_snapshot_op_id(target_unit_kind, target_norm),
            sequence=0,
            action=action,
            target=LegalAddress(path=tuple(resolved_path)),
            payload=payload,
            source=op_source,
            group_id=f"finland-johto:{amendment_id or 'unknown'}",
        )
    )

    moved_from_chapter = _moved_from_chapter()
    if (
        payload is not None
        and target_unit_kind == "section"
        and action is StructuralAction.INSERT
        and moved_from_chapter
        and base_ir is not None
    ):
        old_raw_path = _tops.find(
            base_ir,
            "section",
            normalized_target_norm,
            scope_kind="chapter",
            scope_label=moved_from_chapter,
        )
        old_path = _timeline_path(_tops._as_path(old_raw_path)) if old_raw_path else None
        if old_path is not None:
            lo_ops_out.append(
                _LegalOperation(
                    op_id=f"snapshot_repeal_old_section_{normalized_target_norm}_from_{moved_from_chapter}",
                    sequence=0,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=old_path),
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id or 'unknown'}",
                )
            )

    if (
        payload is not None
        and target_unit_kind == "section"
        and action != StructuralAction.REPEAL
        and payload.kind is IRNodeKind.SECTION
    ):
        section_path = tuple(resolved_path)
        explicitly_repealed_subsection_labels = {
            _norm_num_token(str(rop.resolved_target_subsection_label or ""))
            for rop in group_rops
            if rop.is_repeal_action and rop.targets_subsection_only() and rop.resolved_target_subsection_label
        }
        payload_subsection_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
            and _norm_num_token(child.label) not in explicitly_repealed_subsection_labels
        }
        for child in payload.children:
            if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                continue
            if _norm_num_token(child.label) in explicitly_repealed_subsection_labels:
                continue
            child_path = section_path + (("subsection", child.label),)
            child_base_exists = _timeline_target_exists(
                child_path,
                replay_history_ops=[],
                base_ir=base_ir,
            )
            child_replay_exists = _timeline_target_exists(
                child_path,
                replay_history_ops=lo_ops_out,
                base_ir=base_ir,
                before_effective=op_source.effective,
            )
            lo_ops_out.append(
                _LegalOperation(
                    op_id=_section_child_snapshot_op_id(
                        child.label,
                        parent_norm=target_norm,
                    ),
                    sequence=0,
                    action=(
                        StructuralAction.INSERT
                        if (
                            action is StructuralAction.INSERT
                            and not child_base_exists
                            and child_replay_exists
                        )
                        else (
                            StructuralAction.REPLACE
                            if child_replay_exists
                            else StructuralAction.INSERT
                        )
                    ),
                    target=LegalAddress(path=child_path),
                    payload=child,
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id or 'unknown'}",
                )
            )
        missing_repealed_subsections: list[str] = []
        for rop in group_rops:
            if not rop.is_repeal_action or not rop.targets_subsection_only():
                continue
            target_label = rop.resolved_target_subsection_label
            if not target_label:
                continue
            if _norm_num_token(target_label) in payload_subsection_labels:
                continue
            child_path = section_path + (("subsection", target_label),)
            if not _timeline_target_exists(
                child_path,
                replay_history_ops=lo_ops_out,
                base_ir=base_ir,
                before_effective=op_source.effective,
            ):
                continue
            if target_label not in missing_repealed_subsections:
                missing_repealed_subsections.append(target_label)
        for target_label in missing_repealed_subsections:
            lo_ops_out.append(
                _LegalOperation(
                    op_id=f"snapshot_repeal_subsection_{target_label}_from_section_{target_norm}",
                    sequence=0,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=section_path + (("subsection", target_label),)),
                    payload=None,
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id or 'unknown'}",
                )
            )

    if payload is not None and target_unit_kind in {"chapter", "part"} and action != StructuralAction.REPEAL:
        heading_only_container_group = bool(group_rops) and all(
            rop.effective_target_special in {"otsikko", "otsikko_edella"} for rop in group_rops
        )
        if heading_only_container_group:
            return
        container_path = tuple(resolved_path)
        target_container_chapter = next((lbl for kind, lbl in container_path if kind == "chapter"), None)
        current_container_payload = _current_container_payload()
        base_container_payload = _base_container_payload()
        payload_child_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is IRNodeKind.SECTION and child.label
        }
        for child in payload.children:
            if child.kind is IRNodeKind.SECTION and child.label:
                container_has_child_here = any(
                    candidate.kind is IRNodeKind.SECTION
                    and candidate.label
                    and _norm_num_token(candidate.label) == _norm_num_token(child.label)
                    for candidate in (current_container_payload.children if current_container_payload is not None else ())
                )
                if not container_has_child_here and base_container_payload is not None:
                    container_has_child_here = any(
                        candidate.kind is IRNodeKind.SECTION
                        and candidate.label
                        and _norm_num_token(candidate.label) == _norm_num_token(child.label)
                        for candidate in base_container_payload.children
                    )

                if not container_has_child_here:
                    unique_elsewhere_path = _tops.find(state.ir, "section", child.label)
                    if unique_elsewhere_path is None and base_ir is not None:
                        unique_elsewhere_path = _tops.find(base_ir, "section", child.label)
                    if unique_elsewhere_path is not None:
                        unique_elsewhere_path = _tops._as_path(unique_elsewhere_path)
                        existing_chapter = next((lbl for kind, lbl in unique_elsewhere_path if kind == "chapter"), None)
                        if (
                            existing_chapter
                            and target_container_chapter
                            and _tops._norm(existing_chapter) != _tops._norm(target_container_chapter)
                        ):
                            continue
                sec_path = container_path + (("section", child.label),)
                child_payload = _prior_non_temporary_section_snapshot_payload(
                    section_path=sec_path,
                    replay_history_ops=lo_ops_out,
                    current_effective=op_source.effective or op_source.enacted or "",
                    base_ir=base_ir,
                ) or child
                if action is StructuralAction.REPLACE:
                    child_payload = _stamp_exact_section_snapshot_payload(child_payload)
                lo_ops_out.append(
                    _LegalOperation(
                        op_id=_container_child_snapshot_op_id(
                            child.label,
                            parent_unit_kind=target_unit_kind,
                            parent_norm=target_norm,
                        ),
                        sequence=0,
                        action=StructuralAction.REPLACE,
                        target=LegalAddress(path=sec_path),
                        payload=child_payload,
                        source=op_source,
                        group_id=f"finland-johto:{amendment_id or 'unknown'}",
                    )
                )
        if action is StructuralAction.REPLACE:
            prior_child_paths: dict[str, Path] = {}
            for prev_lo in reversed(lo_ops_out):
                if prev_lo.target.special is not None:
                    continue
                prev_path = prev_lo.target.path
                if prev_path[: len(container_path)] != container_path:
                    continue
                if len(prev_path) != len(container_path) + 1 or prev_path[-1][0] != "section":
                    continue
                child_norm = _norm_num_token(prev_path[-1][1])
                if child_norm in prior_child_paths:
                    continue
                if prev_lo.action is not StructuralAction.REPEAL:
                    prior_child_paths[child_norm] = prev_path
            if base_container_payload is not None:
                for child in base_container_payload.children:
                    if child.kind is not IRNodeKind.SECTION or not child.label:
                        continue
                    child_norm = _norm_num_token(child.label)
                    prior_child_paths.setdefault(
                        child_norm,
                        container_path + (("section", child.label),),
                    )
            missing_child_labels = [
                child_norm for child_norm in prior_child_paths if child_norm not in payload_child_labels
            ]
            overlapping_child_labels = [
                child_norm for child_norm in prior_child_paths if child_norm in payload_child_labels
            ]
            payload_has_heading = any(child.kind is IRNodeKind.HEADING for child in payload.children)
            sparse_fragmentary_container_replace = (
                target_unit_kind == "chapter"
                and payload_has_heading
                and bool(payload_child_labels)
                and bool(overlapping_child_labels)
                and bool(missing_child_labels)
                and len(missing_child_labels) > len(payload_child_labels)
            )
            if sparse_fragmentary_container_replace:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=op_source.statute_id,
                            target_unit_kind=target_unit_kind,
                            target_label=target_norm,
                            recovery_kind="container_snapshot_sparse_missing_child_repeal_skip",
                            live_sibling_count=len(prior_child_paths),
                            payload_sibling_count=len(payload_child_labels),
                        )
                    )
                return
            for child_norm, child_path in prior_child_paths.items():
                if child_norm in payload_child_labels:
                    continue
                lo_ops_out.append(
                    _LegalOperation(
                        op_id=f"snapshot_repeal_missing_section_{child_norm}_from_{target_unit_kind}_{target_norm}",
                        sequence=0,
                        action=StructuralAction.REPEAL,
                        target=LegalAddress(path=child_path),
                        source=op_source,
                        group_id=f"finland-johto:{amendment_id or 'unknown'}",
                    )
                )


def _valid_target_group_path_hint(
    state: "ReplayState",
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    path_hint: Optional[Path],
) -> Optional[Path]:
    if not path_hint:
        return None
    node = _tops.resolve(state.ir, path_hint)
    if node is None:
        return None
    if not node.label or _norm_num_token(node.label) != _norm_num_token(target_norm):
        return None
    if (
        (target_unit_kind == "section" and node.kind is not IRNodeKind.SECTION)
        or (target_unit_kind == "chapter" and node.kind is not IRNodeKind.CHAPTER)
        or (target_unit_kind == "part" and node.kind is not IRNodeKind.PART)
    ):
        return None
    if target_unit_kind == "section" and target_chapter:
        chapters = [step for step in path_hint if step[0] == "chapter" and step[1]]
        if not chapters or _tops._norm(chapters[-1][1]) != _tops._norm(target_chapter):
            return None
    if target_unit_kind == "section" and target_part:
        parts = [step for step in path_hint if step[0] == "part" and step[1]]
        if not parts or _norm_num_token(parts[-1][1]) != _norm_num_token(target_part):
            return None
    if target_unit_kind == "section":
        return _prefer_unique_substantive_section_path_over_placeholder(
            state,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            raw_path=path_hint,
        )
    return path_hint


def _valid_target_path_hint(
    state: "ReplayState",
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    path_hint: Optional[Path],
) -> Optional[Path]:
    return _valid_target_group_path_hint(
        state=state,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
        path_hint=path_hint,
    )


def _with_preserved_provision_index(state: "ReplayState", new_ir: IRNode) -> "ReplayState":
    """Reuse the provision-path index when section/chapter/part paths stay stable."""
    return state.with_ir(new_ir, preserve_provision_index=True)


def _same_norm_label(lhs: Optional[str], rhs: Optional[str]) -> bool:
    return bool(lhs) and bool(rhs) and _tops._norm(lhs) == _tops._norm(rhs)


def _parent_has_direct_child_with_same_label(
    master_ir: IRNode,
    parent_path: Path,
    *,
    kind: IRNodeKind,
    label: str,
) -> bool:
    return _parent_direct_child_path_with_same_label(
        master_ir,
        parent_path,
        kind=kind,
        label=label,
    ) is not None


def _parent_direct_child_path_with_same_label(
    master_ir: IRNode,
    parent_path: Path,
    *,
    kind: IRNodeKind,
    label: str,
) -> Path | None:
    parent = _tops.resolve(master_ir, parent_path)
    if parent is None:
        return None
    target_label = _norm_num_token(label)
    for child in parent.children:
        if child.kind is not kind or not child.label:
            continue
        if _norm_num_token(child.label) == target_label:
            return parent_path + ((_kind_str(child.kind), child.label),)
    return None


def _find_insert_parent_path(
    master_ir: IRNode, chapter_label: Optional[str], label_index: Optional[_tops.LabelIndex] = None
) -> Path:
    """Find the parent path for inserting a section or container."""
    if chapter_label:
        path = _tops.find(master_ir, "chapter", chapter_label, label_index=label_index)
        if path is None:
            normalized_chapter = _norm_num_token(chapter_label).removesuffix("luku")
            if normalized_chapter and normalized_chapter != chapter_label:
                path = _tops.find(master_ir, "chapter", f"{normalized_chapter}luku", label_index=label_index)
        if path is None:
            normalized_chapter = _norm_num_token(chapter_label).removesuffix("luku")
            if normalized_chapter:
                def _search(node: IRNode, prefix: Path) -> Path | None:
                    for child in node.children:
                        child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
                        if (
                            child.kind is IRNodeKind.CHAPTER
                            and child.label
                            and _norm_num_token(child.label).removesuffix("luku") == normalized_chapter
                        ):
                            return child_path
                        found = _search(child, child_path)
                        if found is not None:
                            return found
                    return None

                path = _search(master_ir, ())
        if path is not None:
            return _tops._as_path(path)
    pp = _tops.find_provisions_parent(master_ir)
    return _tops._as_path(pp) if pp else ()


def _find_chapter_insert_parent_path(
    master_ir: IRNode, chapter_label: str, part_hint: Optional[str] = None
) -> Path:
    """Find the parent path for inserting a new chapter in a part-structured statute.

    ``part_hint``, when provided, is an Arabic string label for the target part
    as recorded in the amendment body (e.g. "4" for "IV OSA").  It overrides
    the positional heuristic so that letter-suffix chapters that cross a part
    boundary are routed to the correct part.
    """
    provisions_parent_path = _tops.find_provisions_parent(master_ir) or ()
    parent_node = _tops.resolve(master_ir, provisions_parent_path) if provisions_parent_path else master_ir
    if parent_node is None:
        parent_node = master_ir

    parts = [c for c in parent_node.children if c.kind is IRNodeKind.PART]
    if not parts:
        return provisions_parent_path

    # If the amendment body explicitly placed this chapter in a named part,
    # use that as the authoritative routing target.
    if part_hint is not None:
        for part in parts:
            if part.label == part_hint:
                return provisions_parent_path + (("part", part_hint),)

    fam_path = _tops.find_family(master_ir, "chapter", chapter_label)
    if fam_path is not None and len(fam_path) >= 2:
        return _tops._as_path(fam_path[:-1])

    new_key = _tops._default_sort_key(chapter_label)
    best_part_path: Optional[Path] = None
    best_chapter_key = (-1, "", 0)

    # Track first part (by lowest chapter key) for fallback when new chapter
    # is lower than all existing chapters.
    first_part_path: Optional[Path] = None
    first_part_min_key = (999999, "", 0)

    for part in parts:
        part_path = provisions_parent_path + (("part", part.label or ""),)
        part_min_key = (999999, "", 0)
        for ch in part.children:
            if ch.kind is not IRNodeKind.CHAPTER or not ch.label:
                continue
            ch_key = _tops._default_sort_key(ch.label)
            if ch_key < part_min_key:
                part_min_key = ch_key
            if ch_key < new_key and ch_key > best_chapter_key:
                best_chapter_key = ch_key
                best_part_path = part_path
        if part_min_key < first_part_min_key:
            first_part_min_key = part_min_key
            first_part_path = part_path

    if best_part_path is not None:
        return best_part_path

    # New chapter is lower than all existing → insert into first part
    if first_part_path is not None:
        return first_part_path

    return provisions_parent_path


def _build_subsection_override_map(
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> "SubsectionSlotMap":
    """DEPRECATED: backward-compat wrapper. No production callers remain."""
    from lawvm.finland.payload_normalize import _build_subsection_override_map as _impl

    return _impl(muutos_ir, group_ops)


def _build_subsection_slot_assignment(
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
):
    """Backward-compat wrapper for the typed payload-normalization assignment builder."""
    from lawvm.finland.payload_normalize import _build_subsection_slot_assignment as _impl

    return _impl(muutos_ir, group_ops)
