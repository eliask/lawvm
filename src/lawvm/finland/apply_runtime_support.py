"""Shared runtime-support helpers for Finland replay/apply flows.

These helpers are reused by the executor, grafter compatibility surfaces, and
tests, but they are not themselves dispatch logic. Pulling them out of
``apply.py`` lets the replay kernel shrink while keeping the public helper
surface stable.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, List, Optional, cast

from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir_helpers import _kind_str, irnode_to_text
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import LabelIndex, Path, default_label_sort_key, normalized_label_key

from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.apply_ir_ops import _build_repeal_placeholder_from_label_ir
from lawvm.finland.apply_ir_ops import _relabel_subsection_ir
from lawvm.finland.apply_ir_ops import _shift_lettered_item_labels_after_repeal
from lawvm.finland.apply_payload_ops import _find_amend_paragraph
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johtolause.clause_surface import parse_item_shift_clauses
from lawvm.finland.labels import leaf_label_identity_key
from lawvm.finland.scope import (
    SourceDescendantScopeResult,
    _unique_section_chapter,
    infer_letter_suffix_section_chapter_from_stem_host,
    source_names_descendant_scope_below_section,
)
from lawvm.finland.op_provenance import RecognizerId, has_recognizer
from lawvm.finland.ops import AmendmentOp, ResolvedOp, ResolvedTargetScopeView, temporary_signal_for_op
from lawvm.finland.replay_capture import ReplayLegalOperationCaptureList
from lawvm.finland.standalone_targets import StandaloneSectionTargetsInput
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.source_normalization_kinds import HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR
from lawvm.finland.source_pathology import (
    build_container_replace_target_absent_pathology,
    build_destructive_shape_loss_risk_pathology,
    build_unresolved_descendant_scope_cue_pathology,
)
if TYPE_CHECKING:
    from lawvm.core.compile_result import SourcePathology
    from lawvm.finland.migration_ledger import MigrationLedger
    from lawvm.finland.statute import ReplayState
    from lawvm.finland.payload_normalize import SubsectionSlotMap


@dataclass(frozen=True, slots=True)
class _PendingSubsectionSnapshotPayload:
    target_norm: str
    payload: IRNode
    is_insert_action: bool
    has_item_target: bool
    item_norm: str
    target_already_rebased: bool


@dataclass(frozen=True, slots=True)
class SectionSnapshotIdentity:
    part: str
    chapter: str
    section: str


@dataclass(frozen=True, slots=True)
class _SectionSnapshotIndex:
    by_identity: dict[SectionSnapshotIdentity, list[int]]
    indexed_len: int


@dataclass(frozen=True, slots=True)
class _TimelineExactTargetIndex:
    earliest_effective_by_path: dict[Path, str]
    indexed_len: int


@dataclass(frozen=True, slots=True)
class _TimelineLatestTargetOpIndex:
    latest_by_path: dict[Path, _LegalOperation]
    indexed_len: int


@dataclass(frozen=True, slots=True)
class _TimelinePayloadTargetIndex:
    earliest_effective_by_path: dict[Path, str]
    indexed_len: int


_PROVISION_INDEXED_KINDS = frozenset({"part", "chapter", "section"})


@lru_cache(maxsize=512)
def _paragraph_label_prefix_re(label: str) -> re.Pattern[str]:
    """Compile a paragraph label prefix stripper for one source label."""

    return re.compile(rf"^\s*{re.escape(label)}\s*[\).]\s*")


def _normalize_snapshot_item_label(label: str | None) -> str:
    """Normalize FI item labels without Roman-to-Arabic conversion.

    Finnish ``kohta`` labels can be plain letters (for example ``i kohta``).
    Generic numeric-token normalization would interpret those as Roman numerals
    and silently retarget ``i`` to ``1``.
    """
    return normalized_label_key(label or "")


def _section_source_names_descendant_scope(
    rop: "ResolvedOp", target_norm: str
) -> SourceDescendantScopeResult:
    """Return whether the parsed source formula names descendant scope below a section.

    Routes through scope.py, the canonical owner of the
    ``N §:n ... moment/kohta/alakohta`` descendant-scope grammar, instead of a
    duplicate inline ``raw_text`` regex (AGENTS.md §1.12 reach-back). Returns the
    typed result so the caller witnesses the unparsed-cue residual rather than
    swallowing it as a silent ``False``.
    """
    source = rop.resolved_op_source
    raw_text = source.raw_text if source is not None else ""
    return source_names_descendant_scope_below_section(raw_text, target_norm)


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


def _unique_substantive_section_path(
    state: "ReplayState",
    target_norm: str,
) -> Path | None:
    label_norm = normalized_label_key(target_norm)
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
    lo = getattr(op, "lo", None)
    if lo is not None:
        return lo.source
    return None


def _base_provision_index_for_replay_history(
    replay_history_ops: List[_LegalOperation] | None,
    base_ir: IRNode | None,
) -> LabelIndex | None:
    if base_ir is None:
        return None
    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        return _tops.build_provision_label_index(base_ir, indexed_kinds=_PROVISION_INDEXED_KINDS)
    cache = cast(
        dict[int, tuple[IRNode, LabelIndex]] | None,
        replay_history_ops.base_provision_index_cache,
    )
    if cache is None:
        cache = {}
        replay_history_ops.base_provision_index_cache = cache
    key = id(base_ir)
    cached = cache.get(key)
    if cached is not None and cached[0] is base_ir:
        return cached[1]
    index = _tops.build_provision_label_index(base_ir, indexed_kinds=_PROVISION_INDEXED_KINDS)
    cache[key] = (base_ir, index)
    return index


def _base_target_exists_for_replay_history(
    replay_history_ops: List[_LegalOperation] | None,
    base_ir: IRNode | None,
    target_path: Path,
) -> bool:
    if base_ir is None:
        return False
    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        return _tops.resolve(base_ir, target_path) is not None
    cache = cast(
        dict[int, tuple[IRNode, dict[Path, bool]]] | None,
        replay_history_ops.base_target_exists_cache,
    )
    if cache is None:
        cache = {}
        replay_history_ops.base_target_exists_cache = cache
    key = id(base_ir)
    cached = cache.get(key)
    if cached is None or cached[0] is not base_ir:
        path_cache: dict[Path, bool] = {}
        cache[key] = (base_ir, path_cache)
    else:
        path_cache = cached[1]
    normalized_path = tuple(target_path)
    cached_exists = path_cache.get(normalized_path)
    if cached_exists is not None:
        return cached_exists
    exists = _tops.resolve(base_ir, normalized_path) is not None
    path_cache[normalized_path] = exists
    return exists


def _section_node_from_base_ir(
    base_ir: IRNode | None,
    section_path: Path,
) -> IRNode | None:
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


def _subsection_node_from_base_ir(base_ir: IRNode | None, subsection_path: Path) -> IRNode | None:
    if base_ir is None or not subsection_path or subsection_path[-1][0] != "subsection":
        return None
    subsection_label = subsection_path[-1][1]
    section_path = tuple(part for part in subsection_path[:-1] if part[0] != "subsection")
    section_node = _section_node_from_base_ir(base_ir, section_path)
    if section_node is None:
        return None
    subsection_norm = _norm_num_token(subsection_label)
    for child in section_node.children:
        if child.kind is IRNodeKind.SUBSECTION and child.label:
            if _norm_num_token(child.label) == subsection_norm:
                return child
    return None


@lru_cache(maxsize=8192)
def _section_snapshot_identity(path: Path) -> SectionSnapshotIdentity:
    labels = {kind: label for kind, label in path}
    return SectionSnapshotIdentity(
        part=_norm_num_token(labels.get("part") or ""),
        chapter=_norm_num_token(labels.get("chapter") or ""),
        section=_norm_num_token(labels.get("section") or ""),
    )


def _snapshot_section_los_for_identity(
    replay_history_ops: List[_LegalOperation] | None,
    target_identity: SectionSnapshotIdentity,
) -> list[_LegalOperation]:
    """Return all snapshot_section_ LOs matching *target_identity*, in order.

    This replaces repeated reverse linear scans of replay_history_ops with
    a single indexed pass.  The result is cached on the list object itself
    so that multiple lookups for different identities reuse the same index.
    """
    if replay_history_ops is None:
        return []

    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        idx: dict[SectionSnapshotIdentity, list[int]] = {}
        _add_section_snapshots_to_index(idx, replay_history_ops, start=0, stop=len(replay_history_ops))
        indices = idx.get(target_identity)
        if not indices:
            return []
        return [replay_history_ops[i] for i in indices]

    cur_len = len(replay_history_ops)
    index = cast(_SectionSnapshotIndex | None, replay_history_ops.snapshot_index)
    if index is None or index.indexed_len > cur_len:
        idx = {}
        start = 0
    else:
        idx = index.by_identity
        start = index.indexed_len
    if start < cur_len:
        _add_section_snapshots_to_index(idx, replay_history_ops, start=start, stop=cur_len)
        replay_history_ops.snapshot_index = _SectionSnapshotIndex(
            by_identity=idx,
            indexed_len=cur_len,
        )
    indices = idx.get(target_identity)
    if not indices:
        return []
    return [replay_history_ops[i] for i in indices]


def _add_section_snapshots_to_index(
    idx: dict[SectionSnapshotIdentity, list[int]],
    replay_history_ops: List[_LegalOperation],
    *,
    start: int,
    stop: int,
) -> None:
    for i in range(start, stop):
        lo = replay_history_ops[i]
        if not lo.op_id.startswith("snapshot_section_"):
            continue
        if lo.payload is None or lo.payload.kind is not IRNodeKind.SECTION:
            continue
        ident = _section_snapshot_identity(lo.target.path)
        idx.setdefault(ident, []).append(i)


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
    if not latest_expires or current_effective < latest_expires:
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
        if latest_expires and current_effective >= latest_expires:
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
    if latest_expires and current_effective >= latest_expires:
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


def _expired_temporary_subsection_slot_can_be_consumed(
    *,
    op: AmendmentOp | ResolvedOp,
    section_path: Path,
    subsection_label: str,
    replay_history_ops: List[_LegalOperation] | None,
) -> bool:
    """Return whether a permanent INSERT may replace an expired temp slot.

    Ordinary permanent ``INSERT subsection:N`` shifts an existing live
    subsection:N upward.  The exception is a same-label slot whose latest
    replay-history owner is temporary and expired by this op's effective date.
    In that case the source-authorized operation occupies the expired slot
    rather than preserving the dead temporary text as subsection N+1.
    """
    if replay_history_ops is None or temporary_signal_for_op(op):
        return False
    source = _op_source_for_merge_base(op)
    current_effective = (
        (source.effective if source is not None else "")
        or (source.enacted if source is not None else "")
        or ""
    )
    if not current_effective:
        return False
    subsection_norm = _norm_num_token(subsection_label)
    target_path = tuple(section_path) + (("subsection", subsection_label),)

    def _is_carried_snapshot_without_source_text(lo: _LegalOperation) -> bool:
        if not lo.op_id.startswith("snapshot_subsection_"):
            return False
        if lo.payload is None or lo.source is None:
            return False
        payload_text = " ".join(irnode_to_text(lo.payload).split())
        source_text = " ".join(str(lo.source.raw_text or "").split())
        return bool(payload_text) and payload_text not in source_text

    for lo in reversed(replay_history_ops):
        if lo.target.special is not None:
            continue
        if not lo.target.path or lo.target.path[-1][0] != "subsection":
            continue
        if _norm_num_token(lo.target.path[-1][1]) != subsection_norm:
            continue
        if _section_snapshot_identity(lo.target.path[:-1]) != _section_snapshot_identity(section_path):
            continue
        if lo.source is None:
            return False
        lo_effective = lo.source.effective or lo.source.enacted or ""
        if lo_effective and lo_effective > current_effective:
            continue
        latest_expires = lo.source.expires or ""
        if not latest_expires and _is_carried_snapshot_without_source_text(lo):
            continue
        if not latest_expires:
            return False
        return current_effective >= latest_expires and lo.target.path == target_path
    return False


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


def _stamp_complete_snapshot_owner(payload: IRNode) -> IRNode:
    """Mark a structural snapshot as owning its complete child surface."""
    if payload.kind not in {IRNodeKind.SECTION, IRNodeKind.CHAPTER, IRNodeKind.PART}:
        return payload
    attrs = dict(payload.attrs)
    if (
        attrs.get("lawvm_tail_policy") == "replace_if_target_scope_requires"
        and attrs.get("lawvm_payload_completeness_kind") == "complete"
    ):
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
        label = str(rop.resolved_target_subsection_label or "").strip()
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


def _inherit_parent_snapshot_ownership_attrs(child: IRNode, parent: IRNode) -> IRNode:
    """Carry parent snapshot ownership proof onto emitted child snapshots."""
    inherited: dict[str, str] = {}
    for key in ("lawvm_tail_policy", "lawvm_payload_completeness_kind"):
        value = parent.attrs.get(key)
        if value and key not in child.attrs:
            inherited[key] = str(value)
    if not inherited:
        return child
    attrs = dict(child.attrs)
    attrs.update(inherited)
    return IRNode(
        kind=child.kind,
        label=child.label,
        text=child.text,
        attrs=attrs,
        children=child.children,
    )


def _snapshot_payload_is_complete_owner(payload: IRNode) -> bool:
    """Return True when a snapshot payload can prove omitted siblings absent."""
    return (
        payload.attrs.get("lawvm_tail_policy") == "replace_if_target_scope_requires"
        and payload.attrs.get("lawvm_payload_completeness_kind") == "complete"
    )


def _payload_has_heading_body_subsection_split(payload: IRNode) -> bool:
    for child in payload.children:
        rule = child.attrs.get("lawvm_source_normalization_rule")
        if rule == HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR:
            return True
        if isinstance(rule, tuple) and HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR in rule:
            return True
    return False


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


def _timeline_exact_target_exists_in_history(
    replay_history_ops: List[_LegalOperation],
    target_path: Path,
    *,
    before_effective: str = "",
) -> bool:
    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        for lo in replay_history_ops:
            lo_effective = lo.source.effective if lo.source is not None else ""
            if before_effective and lo_effective and lo_effective >= before_effective:
                continue
            if lo.target.path == target_path:
                return True
        return False

    cur_len = len(replay_history_ops)
    index = cast(_TimelineExactTargetIndex | None, replay_history_ops.timeline_exact_target_index)
    if index is None or index.indexed_len > cur_len:
        earliest_effective_by_path: dict[Path, str] = {}
        start = 0
    else:
        earliest_effective_by_path = index.earliest_effective_by_path
        start = index.indexed_len
    if start < cur_len:
        for idx in range(start, cur_len):
            lo = replay_history_ops[idx]
            effective = lo.source.effective if lo.source is not None else ""
            existing = earliest_effective_by_path.get(lo.target.path)
            if existing is None or not effective or (existing and effective < existing):
                earliest_effective_by_path[lo.target.path] = effective
        replay_history_ops.timeline_exact_target_index = _TimelineExactTargetIndex(
            earliest_effective_by_path=earliest_effective_by_path,
            indexed_len=cur_len,
        )
    earliest_effective = earliest_effective_by_path.get(target_path)
    if earliest_effective is None:
        return False
    if not before_effective:
        return True
    return not earliest_effective or earliest_effective < before_effective


def _latest_target_op_for_path(
    replay_history_ops: List[_LegalOperation],
    target_path: Path,
) -> _LegalOperation | None:
    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        for lo in reversed(replay_history_ops):
            if lo.target.special is not None:
                continue
            if lo.target.path == target_path:
                return lo
        return None

    cur_len = len(replay_history_ops)
    index = cast(_TimelineLatestTargetOpIndex | None, replay_history_ops.timeline_latest_target_op_index)
    if index is None or index.indexed_len > cur_len:
        latest_by_path: dict[Path, _LegalOperation] = {}
        start = 0
    else:
        latest_by_path = index.latest_by_path
        start = index.indexed_len
    if start < cur_len:
        for idx in range(start, cur_len):
            lo = replay_history_ops[idx]
            if lo.target.special is None:
                latest_by_path[lo.target.path] = lo
        replay_history_ops.timeline_latest_target_op_index = _TimelineLatestTargetOpIndex(
            latest_by_path=latest_by_path,
            indexed_len=cur_len,
        )
    return latest_by_path.get(tuple(target_path))


def _record_payload_descendant_paths(
    parent_path: Path,
    payload: IRNode,
    *,
    effective: str,
    earliest_effective_by_path: dict[Path, str],
) -> None:
    """Record labelled descendant paths under a legal-operation payload."""
    stack: list[tuple[Path, IRNode]] = [
        (parent_path, child)
        for child in reversed(payload.children)
        if child.label
    ]
    while stack:
        path, node = stack.pop()
        node_path = path + ((_kind_str(node.kind), cast(str, node.label)),)
        existing = earliest_effective_by_path.get(node_path)
        if existing is None or not effective or (existing and effective < existing):
            earliest_effective_by_path[node_path] = effective
        for child in reversed(node.children):
            if child.label:
                stack.append((node_path, child))


def _timeline_payload_target_exists_in_history(
    replay_history_ops: List[_LegalOperation],
    target_path: Path,
    *,
    before_effective: str = "",
) -> bool:
    """Return True if a prior operation payload contains target_path.

    This is the indexed equivalent of the descendant-payload branch in
    ``_timeline_target_exists``.  Effective-date cutoff semantics intentionally
    mirror that scanner: undated operations remain visible under a cutoff, and
    dated operations at/after ``before_effective`` are ignored.
    """
    if not isinstance(replay_history_ops, ReplayLegalOperationCaptureList):
        return _timeline_target_exists(
            target_path,
            replay_history_ops=replay_history_ops,
            base_ir=None,
            before_effective=before_effective,
        )

    cur_len = len(replay_history_ops)
    index = cast(_TimelinePayloadTargetIndex | None, replay_history_ops.timeline_payload_target_index)
    if index is None or index.indexed_len > cur_len:
        earliest_effective_by_path: dict[Path, str] = {}
        start = 0
    else:
        earliest_effective_by_path = index.earliest_effective_by_path
        start = index.indexed_len
    if start < cur_len:
        for idx in range(start, cur_len):
            lo = replay_history_ops[idx]
            if lo.payload is None:
                continue
            effective = lo.source.effective if lo.source is not None else ""
            _record_payload_descendant_paths(
                lo.target.path,
                lo.payload,
                effective=effective,
                earliest_effective_by_path=earliest_effective_by_path,
            )
        replay_history_ops.timeline_payload_target_index = _TimelinePayloadTargetIndex(
            earliest_effective_by_path=earliest_effective_by_path,
            indexed_len=cur_len,
        )
    earliest_effective = earliest_effective_by_path.get(tuple(target_path))
    if earliest_effective is None:
        return False
    if not before_effective:
        return True
    return not earliest_effective or earliest_effective < before_effective


def _container_replace_prior_child_paths(
    *,
    container_path: Path,
    base_container_payload: Optional[IRNode],
    replay_history_ops: List[_LegalOperation],
    child_kind: IRNodeKind = IRNodeKind.SECTION,
) -> dict[str, Path]:
    """Collect live direct-child paths a container REPLACE may need to retire.

    Combines the most recent non-repeal direct-child snapshot under
    ``container_path`` from prior replay history with the base-statute
    container's direct structural children. Keyed by normalized child label, so
    a container REPLACE can decide which prior children survive its new payload.
    """
    child_path_kind = child_kind.value
    prior_child_paths: dict[str, Path] = {}
    for prev_lo in reversed(replay_history_ops):
        if prev_lo.target.special is not None:
            continue
        prev_path = prev_lo.target.path
        if prev_path[: len(container_path)] != container_path:
            continue
        if len(prev_path) != len(container_path) + 1 or prev_path[-1][0] != child_path_kind:
            continue
        child_norm = _norm_num_token(prev_path[-1][1])
        if child_norm in prior_child_paths:
            continue
        if prev_lo.action is not StructuralAction.REPEAL:
            prior_child_paths[child_norm] = prev_path
    if base_container_payload is not None:
        for child in base_container_payload.children:
            if child.kind is not child_kind or not child.label:
                continue
            child_norm = _norm_num_token(child.label)
            prior_child_paths.setdefault(
                child_norm,
                container_path + ((child_path_kind, child.label),),
            )
    return prior_child_paths


def _group_has_item_scoped_snapshot_mutations(group_rops: list[ResolvedOp]) -> bool:
    """True when an item-scoped op must not promote a sparse whole-section shell."""
    return any(rop.effective_target_item_label is not None for rop in group_rops)


def _group_has_descendant_scoped_snapshot_mutations(group_rops: list[ResolvedOp]) -> bool:
    """True when child-scoped ops must not promote a sparse whole-section shell."""
    return any(
        rop.resolved_target_subsection_label is not None
        or rop.effective_target_paragraph is not None
        or rop.effective_target_item_label is not None
        or rop.effective_target_special is not None
        for rop in group_rops
    )


def _normalized_snapshot_text_len(node: IRNode) -> int:
    return len(" ".join(irnode_to_text(node).split()))


def _payload_has_in_place_merge_child(node: IRNode) -> bool:
    return any(
        child.attrs.get("lawvm_in_place_merge") == "1"
        for child in node.children
        if child.kind is IRNodeKind.SUBSECTION
    )


def _prefer_live_fold_section_snapshot_for_descendant_scoped_group(
    *,
    state: "ReplayState",
    resolved_path: Optional[Path],
    payload: Optional[IRNode],
    payload_from_muutos_ir: bool,
    group_rops: list[ResolvedOp],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    source_pathologies_out: Optional[list["SourcePathology"]],
    source_statute: str,
) -> tuple[Optional[IRNode], bool]:
    """Keep the dense replay fold when item/subsection ops own the mutation."""
    if (
        payload is None
        or resolved_path is None
        or target_unit_kind != "section"
        or not _group_has_descendant_scoped_snapshot_mutations(group_rops)
    ):
        return payload, payload_from_muutos_ir
    if payload.attrs.get("lawvm_consumed_subsection_targets"):
        return payload, payload_from_muutos_ir

    live_path = state.find_section_path(_norm_num_token(target_norm), target_chapter, target_part)
    if live_path is None:
        return payload, payload_from_muutos_ir
    live_payload = _tops.resolve(state.ir, live_path)
    if live_payload is None or live_payload.kind is not IRNodeKind.SECTION:
        return payload, payload_from_muutos_ir

    live_len = _normalized_snapshot_text_len(live_payload)
    payload_len = _normalized_snapshot_text_len(payload)
    has_post_repeal_item_shift = any(
        rop.resolved_post_repeal_item_shift_label
        for rop in group_rops
    ) or any(
        "muuttuvat kohdiksi" in str(
            rop.resolved_op_source.raw_text if rop.resolved_op_source is not None else ""
        ).lower()
        for rop in group_rops
    )
    complete_owner_should_win = (
        _snapshot_payload_is_complete_owner(payload)
        and not _payload_has_in_place_merge_child(payload)
        and not has_post_repeal_item_shift
    )
    if live_len <= payload_len or complete_owner_should_win:
        return payload, payload_from_muutos_ir

    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=source_statute,
                target_unit_kind="section",
                target_label=f"{target_norm} §",
                recovery_kind=RecoveryKind.SECTION_SNAPSHOT_PRESERVE_LIVE_FOLD_FOR_DESCENDANT_SCOPED_ITEM,
                live_sibling_count=live_len,
                payload_sibling_count=payload_len,
            )
        )
    return live_payload, False


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
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    source_pathologies_out: Optional[List["SourcePathology"]] = None,
) -> None:
    """Emit a section/chapter-level snapshot to lo_ops_out after ops are applied."""
    action: StructuralAction = StructuralAction.REPLACE
    normalized_target_norm = _norm_num_token(target_norm)

    op_source = _snapshot_op_source(group_rops, amendment_id, source_title, source_issue_date, source_effective_date)
    base_provision_index = _base_provision_index_for_replay_history(lo_ops_out, base_ir)

    def _timeline_path(tree_path: Path) -> Path:
        return tuple((k, v) for k, v in tree_path if v)

    def _timeline_target_exists_for_snapshot(
        target_path: Path,
        *,
        replay_history_ops: List[_LegalOperation],
        base_ir: IRNode | None,
        before_effective: str = "",
    ) -> bool:
        if not replay_history_ops:
            return _base_target_exists_for_replay_history(lo_ops_out, base_ir, tuple(target_path))
        normalized_target_path = tuple(target_path)
        cache: dict[tuple[object, ...], bool] | None = None
        if isinstance(lo_ops_out, ReplayLegalOperationCaptureList):
            if lo_ops_out.timeline_target_exists_cache is None:
                lo_ops_out.timeline_target_exists_cache = {}
            cache = cast(dict[tuple[object, ...], bool], lo_ops_out.timeline_target_exists_cache)
        history_key = id(replay_history_ops) if replay_history_ops else 0
        true_key = (normalized_target_path, history_key, id(base_ir), before_effective)
        len_key = (normalized_target_path, history_key, len(replay_history_ops), id(base_ir), before_effective)
        if cache is not None:
            if cache.get(true_key) is True:
                return True
            cached = cache.get(len_key)
            if cached is not None:
                return cached
        if _timeline_exact_target_exists_in_history(
            replay_history_ops,
            normalized_target_path,
            before_effective=before_effective,
        ):
            exists = True
        elif _base_target_exists_for_replay_history(lo_ops_out, base_ir, normalized_target_path):
            exists = True
        else:
            exists = _timeline_payload_target_exists_in_history(
                replay_history_ops,
                normalized_target_path,
                before_effective=before_effective,
            )
        if cache is not None:
            if exists:
                cache[true_key] = True
            cache[len_key] = exists
        return exists

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

    snapshot_not_before = source_effective_date.isoformat() if source_effective_date is not None else ""

    def _project_snapshot_path(path: Optional[Path]) -> Optional[Path]:
        if not path:
            return path
        if migration_ledger is not None:
            addr = LegalAddress(path=path)
            migrated = migration_ledger.current_address_with_prefix_migrations(
                addr, not_before=snapshot_not_before
            )
            if migrated != addr and _tops.resolve(state.ir, migrated.path) is not None:
                return migrated.path
            return path
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
        label_norm = normalized_label_key(label)
        if len(idx.get(("section", label_norm), [])) != 1:
            return None
        return _tops._as_path(raw_path)

    def _group_payload_kind() -> Optional[str]:
        return target_unit_kind if target_unit_kind in {"section", "chapter", "part"} else None

    def _whole_target_repeal() -> bool:
        if target_unit_kind in {"section", "chapter", "part"}:
            return any(rop.is_repeal_action and rop.targets_whole_unit(target_unit_kind) for rop in group_rops)
        return False

    def _whole_section_insert_replaces_explicit_child_repeal() -> bool:
        if target_unit_kind != "section":
            return False
        has_whole_insert_payload = any(
            rop.is_insert_action
            and rop.targets_whole_unit("section")
            and rop.muutos_ir is not None
            and rop.muutos_ir.kind is IRNodeKind.SECTION
            for rop in group_rops
        )
        if not has_whole_insert_payload:
            return False
        return any(
            rop.is_repeal_action
            and rop.targets_subsection_only()
            for rop in group_rops
        )

    def _complete_whole_section_source_payload() -> Optional[IRNode]:
        """Return the source-owned section payload for exact whole-section snapshots.

        Snapshot emission normally observes post-apply replay state.  If the
        mutable apply fold failed to hit the target, that state may still carry
        stale descendants.  A complete whole-section source payload is the
        stronger witness: it owns the section child surface and should be the
        timeline snapshot payload.
        """
        if target_unit_kind != "section" or _whole_target_repeal():
            return None
        candidates: list[IRNode] = []
        descendant_scoped_candidates = 0

        def _latest_exact_target_op_before_effective(target_path: Path) -> _LegalOperation | None:
            for prior in reversed(lo_ops_out):
                if prior.target.special is not None or prior.target.path != target_path:
                    continue
                prior_effective = prior.source.effective if prior.source is not None else ""
                if op_source.effective and prior_effective and prior_effective >= op_source.effective:
                    continue
                return prior
            return None

        def _insert_target_is_not_live_before_effective() -> bool:
            if resolved_path is None:
                return False
            latest = _latest_exact_target_op_before_effective(tuple(resolved_path))
            if latest is None:
                return False
            if latest.action is StructuralAction.REPEAL or latest.payload is None:
                return True
            if latest.source is not None:
                latest_expires = latest.source.expires or ""
                if latest_expires and op_source.effective and op_source.effective >= latest_expires:
                    return True
            return False

        def _whole_section_insert_can_own_snapshot(rop: ResolvedOp) -> bool:
            if not rop.is_insert_action:
                return False
            if _whole_section_insert_replaces_explicit_child_repeal():
                return True
            if temporary_signal_for_op(rop):
                # First-time temporary section inserts often need post-apply
                # normalization/rebasing.  If an exact prior section snapshot
                # ended before this insert, the fold may still carry stale
                # background content at that address; the complete source
                # section payload is the stronger overlay/rebirth witness.
                return bool(op_source.expires) and _insert_target_is_not_live_before_effective()
            # A whole-section insert after a prior exact target ended owns the
            # reborn section child surface.  Initial inserts and existing-section
            # insert/merge families still need the post-apply fold snapshot
            # because source payload may need ontology normalization or rebasing.
            return _insert_target_is_not_live_before_effective()

        for rop in group_rops:
            if not rop.targets_whole_unit("section"):
                continue
            if not rop.is_replace_action and not (
                _whole_section_insert_can_own_snapshot(rop)
            ):
                continue
            source_payload = rop.muutos_ir
            if source_payload is None or source_payload.kind is not IRNodeKind.SECTION:
                continue
            source_payload, _normalization_facts = normalize_source_ir(
                source_payload,
                op_source.statute_id or "",
                allow_dotted_paragraph_subsection_promotion=False,
            )
            if source_payload.label and _norm_num_token(source_payload.label) != normalized_target_norm:
                continue
            completeness = rop.payload_completeness
            if completeness is None:
                continue
            if str(completeness.tail_policy or "").strip() != "replace_if_target_scope_requires":
                continue
            descendant_scope = _section_source_names_descendant_scope(rop, normalized_target_norm)
            if descendant_scope.matched:
                descendant_scoped_candidates += 1
            elif descendant_scope.unparsed_cue is not None and source_pathologies_out is not None:
                # The source named a section-genitive descendant-scope cue that did
                # not resolve to this target. Witness it instead of swallowing the
                # silent negative (does not change the snapshot drop decision).
                source_pathologies_out.append(
                    build_unresolved_descendant_scope_cue_pathology(
                        source_statute=op_source.statute_id,
                        target_section=target_norm,
                        target_chapter=target_chapter or "",
                        unparsed_cue=descendant_scope.unparsed_cue,
                    )
                )
            candidates.append(_stamp_exact_section_snapshot_payload(source_payload))
        if len(candidates) == 1 and descendant_scoped_candidates == 1:
            if source_pathologies_out is not None:
                live_child_count = 0
                live_path = state.find_section_path(normalized_target_norm, target_chapter, target_part)
                live_node = _tops.resolve(state.ir, live_path) if live_path is not None else None
                if live_node is not None:
                    live_child_count = len(live_node.children)
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=op_source.statute_id,
                        target_unit_kind="section",
                        target_label=f"{target_norm} §",
                        recovery_kind=RecoveryKind.SECTION_SNAPSHOT_PRESERVE_FOLD_FOR_DESCENDANT_SCOPED_SOURCE,
                        live_sibling_count=live_child_count,
                        payload_sibling_count=len(candidates[0].children),
                    )
                )
            return None
        return candidates[0] if len(candidates) == 1 else None

    def _container_direct_child_kind() -> IRNodeKind | None:
        if target_unit_kind == "chapter":
            return IRNodeKind.SECTION
        if target_unit_kind == "part":
            return IRNodeKind.CHAPTER
        return None

    def _complete_whole_container_source_child_labels() -> Optional[set[str]]:
        """Return the authoritative child-label set of a complete container replace.

        A whole-container REPLACE whose source payload owns its full child surface
        (``tail_policy == replace_if_target_scope_requires``) defines exactly which
        direct children the new container contains. Snapshot emission otherwise observes
        the post-apply replay state, which may still carry sections an earlier
        merge-style apply failed to drop. When this returns a label set, children
        absent from it are stale orphans and must be repealed, not snapshotted
        forward. Returns ``None`` for partial/sparse amendments and for anything
        that is not a single complete whole-container replacement, so those keep
        preserving live children unchanged.
        """
        if target_unit_kind not in {"chapter", "part"} or _whole_target_repeal():
            return None
        direct_child_kind = _container_direct_child_kind()
        if direct_child_kind is None:
            return None
        expected_payload_kind = IRNodeKind.CHAPTER if target_unit_kind == "chapter" else IRNodeKind.PART
        container_replaces = [
            rop
            for rop in group_rops
            if rop.is_replace_action and rop.targets_whole_unit(target_unit_kind)
        ]
        if len(container_replaces) != 1:
            return None
        rop = container_replaces[0]
        source_payload = rop.muutos_ir
        if source_payload is None or source_payload.kind is not expected_payload_kind:
            return None
        if source_payload.label and _norm_num_token(source_payload.label) != normalized_target_norm:
            return None
        completeness = rop.payload_completeness
        if completeness is None:
            return None
        if str(completeness.tail_policy or "").strip() != "replace_if_target_scope_requires":
            return None
        source_child_labels = completeness.detail.get("source_child_labels")
        if isinstance(source_child_labels, tuple):
            labels = {
                _norm_num_token(label)
                for label in source_child_labels
                if str(label or "").strip()
            }
            labels.discard("")
            if labels:
                return labels
        labels = {
            _norm_num_token(child.label)
            for child in source_payload.children
            if child.kind is direct_child_kind and child.label
        }
        if not labels:
            return None
        return labels

    def _container_replace_orphan_child_labels(
        *,
        authoritative_child_labels: Optional[set[str]],
        payload: IRNode,
        container_path: Path,
        base_container_payload: Optional[IRNode],
        action: StructuralAction,
    ) -> set[str]:
        """Return the post-apply direct children this container REPLACE retires.

        Mirrors the drop decision applied below so the snapshot-emission loop can
        skip exactly the children that will be repealed (carrying them forward as
        REPLACE snapshots would re-orphan them).

        A direct child present in the post-apply live tree but absent from the
        authoritative/effective replacement set is a *merge-pollution orphan*: a
        complete whole-container REPLACE that an earlier merge-style apply failed to
        drop. Those are always retired here, even for chapter ranges or re-heading
        combos whose authoritative set is much smaller than the polluted live
        tree. The sparse guard only protects *prior-only* untouched siblings — old
        sections that survive in prior history but were never merged into this
        payload — so genuinely fragmentary chapter amendments keep their live
        sections unchanged.
        """
        if action is not StructuralAction.REPLACE or target_unit_kind not in {"chapter", "part"}:
            return set()
        direct_child_kind = _container_direct_child_kind()
        if direct_child_kind is None:
            return set()
        # PART-level orphan retirement requires an authoritative child-label set
        # (a genuine single complete part REPLACE, tail_policy
        # replace_if_target_scope_requires). Finnish part payloads interpose
        # crossHeading/heading wrappers, so the payload_labels fallback below would
        # treat live chapters (not *direct* children of the wrapped payload) as
        # merge-pollution orphans and spuriously repeal them — the content=None
        # chapter snapshot then masks its own same-wave child sections (regression:
        # 1929/234 part_5 dropped live chapters 1/2, masking sections 110-113).
        # Without an authoritative set, preserve the live part chapters; chapter-
        # level retirement (which has no wrapper interposition) keeps its fallback.
        if target_unit_kind == "part" and authoritative_child_labels is None:
            return set()
        payload_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is direct_child_kind and child.label
        }
        effective_labels = (
            authoritative_child_labels if authoritative_child_labels is not None else payload_labels
        )
        # Merge-pollution orphans: present in the freshly-applied live tree but
        # absent from the authoritative replacement section set. These are never
        # sparse-protected — an untouched sibling never appears in the payload.
        if _whole_chapter_replace_is_sparse(
            effective_labels=effective_labels,
            payload=payload,
            container_path=container_path,
            base_container_payload=base_container_payload,
        ):
            # Genuinely sparse/fragmentary chapter amendment: the payload merged
            # into the live chapter, so live sections absent from the (often
            # misclassified-small) authoritative set are legitimately-merged
            # sections, not orphans. Retire nothing — preserve the live chapter.
            return set()
        return {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is direct_child_kind
            and child.label
            and _norm_num_token(child.label) not in effective_labels
        }

    def _whole_chapter_replace_is_sparse(
        *,
        effective_labels: Optional[set[str]],
        payload: IRNode,
        container_path: Path,
        base_container_payload: Optional[IRNode],
    ) -> bool:
        """Whether this chapter REPLACE is a sparse/fragmentary amendment.

        Sparseness is measured against *prior-only* siblings — old sections that
        live in prior history but were never merged into the post-apply payload.
        Sections that the merge left in the live tree but the source omitted are
        merge-pollution orphans, not sparse-protected siblings, so they are
        excluded from the count. This keeps a complete whole-chapter REPLACE whose
        authoritative section set is smaller than the merge-polluted live tree
        (chapter ranges, re-heading combos) from being misread as sparse.
        """
        if not effective_labels:
            return False
        payload_has_heading = any(child.kind is IRNodeKind.HEADING for child in payload.children)
        if not payload_has_heading:
            return False
        live_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is IRNodeKind.SECTION and child.label
        }
        prior_child_paths = _container_replace_prior_child_paths(
            container_path=container_path,
            base_container_payload=base_container_payload,
            replay_history_ops=lo_ops_out,
        )
        overlapping = {norm for norm in prior_child_paths if norm in effective_labels}
        # Untouched siblings: in prior history, absent from this payload, and not
        # part of the authoritative replacement set.
        prior_only_missing = {
            norm
            for norm in prior_child_paths
            if norm not in effective_labels and norm not in live_labels
        }
        return (
            bool(overlapping)
            and bool(prior_only_missing)
            and len(prior_only_missing) > len(effective_labels)
        )

    def _subsection_child_by_label(section: IRNode, label: str) -> IRNode | None:
        label_norm = _norm_num_token(label)
        for child in section.children:
            if child.kind is IRNodeKind.SUBSECTION and child.label:
                if _norm_num_token(child.label) == label_norm:
                    return child
        return None

    def _snapshot_subsection_target_label(rop: ResolvedOp) -> str:
        label = str(rop.resolved_target_subsection_label or "").strip()
        if label:
            return label
        paragraph = rop.effective_target_paragraph
        return str(paragraph) if paragraph is not None else ""

    def _snapshot_targets_subsection_only(rop: ResolvedOp) -> bool:
        address = rop.resolved_target_address
        if address is not None and address.path and address.path[-1][0] == "subsection":
            return (
                address.special is None
                and rop.effective_target_item_label is None
                and rop.effective_target_special is None
            )
        if rop.effective_target_paragraph is None:
            return False
        if rop.effective_target_item_label is not None or rop.effective_target_special is not None:
            return False
        if address is not None and address.path:
            return address.special is None and address.path[-1][0] == "section"
        return True

    def _rebase_section_payload_on_latest_exact_snapshot(section_path: Path, section_payload: IRNode) -> IRNode | None:
        """Overlay source-owned subsection changes onto the prior exact section.

        If a prior complete whole-section snapshot was emitted but the mutable
        replay fold still contains older descendants, later subsection-level
        snapshots must use the exact prior parent as their merge base.  The only
        overlay allowed here is a same-label subsection payload owned by the
        current typed op.
        """
        if section_payload.kind is not IRNodeKind.SECTION:
            return None
        latest = _latest_section_snapshot_payload(
            section_path=section_path,
            replay_history_ops=lo_ops_out,
        )
        if latest is None or latest.payload is None or latest.payload.kind is not IRNodeKind.SECTION:
            return None
        latest_payload = latest.payload
        latest_expires = latest.source.expires if latest.source is not None else ""
        temporary_parent_snapshot = bool(latest_expires)
        if latest_expires and op_source.effective and op_source.effective >= latest_expires:
            prior_payload = _prior_non_temporary_section_snapshot_payload(
                section_path=section_path,
                replay_history_ops=lo_ops_out,
                current_effective=op_source.effective,
                base_ir=base_ir,
            )
            if prior_payload is not None:
                latest_payload = prior_payload
                temporary_parent_snapshot = True
        if (
            latest_payload.attrs.get("lawvm_tail_policy") != "replace_if_target_scope_requires"
            and not temporary_parent_snapshot
        ):
            return None

        replacements: dict[str, IRNode] = {}
        # Subsection REPEAL ops in the same group must not let the prior whole-
        # section snapshot carry the repealed subsection's stale content forward.
        # Resolve each repealed subsection against the post-apply section payload:
        # under the placeholder profile it survives as a repeal tombstone (which
        # we overlay onto the rebased parent); under the removing profile it is
        # gone and must be dropped from the rebased parent rather than inherited
        # verbatim from the latest exact snapshot.
        repealed_overlay: dict[str, IRNode] = {}
        repealed_dropped: set[str] = set()
        for rop in group_rops:
            if not rop.is_replace_action or not _snapshot_targets_subsection_only(rop):
                continue
            target_label = _snapshot_subsection_target_label(rop)
            if not target_label:
                continue
            replacement = _subsection_child_by_label(section_payload, target_label)
            if replacement is None:
                continue
            replacements[_norm_num_token(target_label)] = replacement
        for rop in group_rops:
            if not rop.is_repeal_action or not _snapshot_targets_subsection_only(rop):
                continue
            target_label = _snapshot_subsection_target_label(rop)
            if not target_label:
                continue
            target_norm_label = _norm_num_token(target_label)
            applied = _subsection_child_by_label(section_payload, target_label)
            if applied is not None and applied.attrs.get("lawvm_repeal_placeholder") == "1":
                repealed_overlay[target_norm_label] = applied
            else:
                repealed_dropped.add(target_norm_label)
        if not replacements and not repealed_overlay and not repealed_dropped:
            # No subsection-level overlay warrants a rebase. A heading-only group
            # is already handled in the replay fold (`_apply_whole_section_op`),
            # so do not rebuild from the prior exact snapshot here — that would
            # drop live subsections absent from the older parent payload.
            return None
        # A same-group section-heading change ("N §:ään uusi otsikko") is owned
        # by the current amendment, so the rebased parent must adopt the current
        # heading rather than inherit the prior exact snapshot's heading.
        heading_overlay: IRNode | None = None
        if any(
            rop.effective_target_special in {"otsikko", "otsikko_edella"}
            for rop in group_rops
        ):
            heading_overlay = next(
                (c for c in section_payload.children if c.kind is IRNodeKind.HEADING),
                None,
            )

        changed = False
        heading_placed = False
        seen: set[str] = set()
        new_children: list[IRNode] = []
        dropped_expired_temporary_children = 0
        for child in latest_payload.children:
            if heading_overlay is not None and child.kind is IRNodeKind.HEADING:
                changed = True
                continue
            if (
                heading_overlay is not None
                and not heading_placed
                and child.kind is IRNodeKind.NUM
            ):
                new_children.append(child)
                new_children.append(heading_overlay)
                heading_placed = True
                changed = True
                continue
            if child.kind is IRNodeKind.SUBSECTION and child.label:
                child_norm = _norm_num_token(child.label)
                replacement = replacements.get(child_norm)
                if replacement is not None:
                    new_children.append(replacement)
                    seen.add(child_norm)
                    changed = changed or replacement != child
                    continue
                repealed_tombstone = repealed_overlay.get(child_norm)
                if repealed_tombstone is not None:
                    new_children.append(repealed_tombstone)
                    seen.add(child_norm)
                    changed = changed or repealed_tombstone != child
                    continue
                if child_norm in repealed_dropped:
                    seen.add(child_norm)
                    changed = True
                    continue
                child_path = section_path + (("subsection", child.label),)
                latest_child_snapshot = next(
                    (
                        lo
                        for lo in reversed(lo_ops_out)
                        if lo.target.special is None and lo.target.path == child_path
                    ),
                    None,
                )
                latest_child_expires = (
                    latest_child_snapshot.source.expires
                    if latest_child_snapshot is not None and latest_child_snapshot.source is not None
                    else ""
                )
                if op_source.effective and latest_child_expires and op_source.effective >= latest_child_expires:
                    prior_child_payload = next(
                        (
                            lo.payload
                            for lo in reversed(lo_ops_out)
                            if (
                                lo.target.special is None
                                and lo.target.path == child_path
                                and lo.source is not None
                                and not lo.source.expires
                                and lo.payload is not None
                                and lo.payload.kind is IRNodeKind.SUBSECTION
                            )
                        ),
                        None,
                    )
                    if prior_child_payload is not None:
                        new_children.append(prior_child_payload)
                    else:
                        base_child_payload = _subsection_node_from_base_ir(base_ir, child_path)
                        if base_child_payload is not None:
                            new_children.append(base_child_payload)
                    dropped_expired_temporary_children += 1
                    seen.add(child_norm)
                    changed = True
                    continue
            new_children.append(child)
        if heading_overlay is not None and not heading_placed:
            # The prior exact snapshot carried no heading; splice the new one in
            # directly behind the num so the order stays "N § Otsikko".
            spliced: list[IRNode] = []
            inserted = False
            for child in new_children:
                spliced.append(child)
                if not inserted and child.kind is IRNodeKind.NUM:
                    spliced.append(heading_overlay)
                    inserted = True
            if not inserted:
                spliced.insert(0, heading_overlay)
            new_children = spliced
            heading_placed = True
            changed = True
        if not changed:
            return None

        missing = sorted(set(replacements) - seen, key=default_label_sort_key)
        for child_norm in missing:
            new_children.append(replacements[child_norm])

        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=normalized_target_norm,
                    recovery_kind=(
                        RecoveryKind.SECTION_SNAPSHOT_DROP_EXPIRED_TEMPORARY_SUBSECTION
                        if dropped_expired_temporary_children
                        else RecoveryKind.SECTION_SNAPSHOT_REBASE_ON_LATEST_EXACT_PARENT
                    ),
                    live_sibling_count=len(
                        [child for child in section_payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                    payload_sibling_count=len(
                        [child for child in latest.payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                )
            )

        return IRNode(
            kind=latest_payload.kind,
            label=latest_payload.label,
            text=latest_payload.text,
            attrs=dict(latest_payload.attrs),
            children=tuple(new_children),
        )

    def _expired_temporary_subsection_payload(
        section_path: Path,
        subsection_label: str,
    ) -> IRNode | None:
        subsection_norm = _norm_num_token(subsection_label)
        if not op_source.effective:
            return None

        def _is_carried_snapshot_without_source_text(lo: _LegalOperation) -> bool:
            if not lo.op_id.startswith("snapshot_subsection_"):
                return False
            if lo.payload is None or lo.source is None:
                return False
            payload_text = " ".join(irnode_to_text(lo.payload).split())
            source_text = " ".join(str(lo.source.raw_text or "").split())
            return bool(payload_text) and payload_text not in source_text

        for lo in reversed(lo_ops_out):
            if lo.target.special is not None:
                continue
            if not lo.target.path or lo.target.path[-1][0] != "subsection":
                continue
            if _section_snapshot_identity(lo.target.path[:-1]) != _section_snapshot_identity(section_path):
                continue
            if _norm_num_token(lo.target.path[-1][1]) != subsection_norm:
                continue
            if lo.source is None:
                return None
            lo_effective = lo.source.effective or lo.source.enacted or ""
            if lo_effective and lo_effective > op_source.effective:
                continue
            latest_expires = lo.source.expires or ""
            if latest_expires and op_source.effective >= latest_expires:
                return lo.payload if lo.payload is not None and lo.payload.kind is IRNodeKind.SUBSECTION else None
            if not latest_expires and _is_carried_snapshot_without_source_text(lo):
                continue
            return None
        return None

    def _drop_shifted_expired_temporary_subsection_payload(
        section_path: Path,
        section_payload: IRNode,
    ) -> IRNode | None:
        if section_payload.kind is not IRNodeKind.SECTION:
            return None
        expired_payloads: list[tuple[str, IRNode]] = []
        for rop in group_rops:
            if not rop.is_insert_action or not _snapshot_targets_subsection_only(rop):
                continue
            target_label = _snapshot_subsection_target_label(rop)
            if not target_label:
                continue
            if not _expired_temporary_subsection_slot_can_be_consumed(
                op=rop,
                section_path=section_path,
                subsection_label=target_label,
                replay_history_ops=lo_ops_out,
            ):
                continue
            expired_payload = _expired_temporary_subsection_payload(section_path, target_label)
            if expired_payload is not None:
                expired_payloads.append((_norm_num_token(target_label), expired_payload))
        if not expired_payloads:
            return None

        def _norm_text(node: IRNode) -> str:
            return " ".join(irnode_to_text(node).split())

        expired_text_by_target = {
            target_norm: _norm_text(expired_payload)
            for target_norm, expired_payload in expired_payloads
        }
        new_children: list[IRNode] = []
        removed = 0
        for child in section_payload.children:
            if child.kind is IRNodeKind.SUBSECTION and child.label:
                child_norm = _norm_num_token(child.label)
                child_text = _norm_text(child)
                if any(
                    child_norm != target_norm and child_text and child_text == expired_text
                    for target_norm, expired_text in expired_text_by_target.items()
                ):
                    removed += 1
                    continue
            new_children.append(child)
        if removed == 0:
            return None

        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_DROP_SHIFTED_EXPIRED_TEMPORARY_SUBSECTION,
                    live_sibling_count=len(
                        [child for child in section_payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                    payload_sibling_count=len(
                        [child for child in new_children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                )
            )

        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(new_children),
        )

    def _drop_expired_temporary_subsection_children(
        section_path: Path,
        section_payload: IRNode,
    ) -> IRNode | None:
        if section_payload.kind is not IRNodeKind.SECTION or not op_source.effective:
            return None
        current_group_targets = {
            _norm_num_token(_snapshot_subsection_target_label(rop))
            for rop in group_rops
            if (
                _snapshot_subsection_target_label(rop)
                and (rop.is_insert_action or rop.is_replace_action or rop.is_repeal_action)
                and _snapshot_targets_subsection_only(rop)
            )
        }
        new_children: list[IRNode] = []
        changed = False
        for child in section_payload.children:
            if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                new_children.append(child)
                continue
            child_norm = _norm_num_token(child.label)
            if child_norm in current_group_targets:
                new_children.append(child)
                continue
            child_path = section_path + (("subsection", child.label),)
            latest_child_snapshot = _latest_snapshot_for_path(child_path)
            latest_child_expires = (
                latest_child_snapshot.source.expires
                if latest_child_snapshot is not None and latest_child_snapshot.source is not None
                else ""
            )
            if not latest_child_expires or op_source.effective < latest_child_expires:
                new_children.append(child)
                continue
            prior_payload = next(
                (
                    lo.payload
                    for lo in reversed(lo_ops_out)
                    if (
                        lo.target.special is None
                        and lo.target.path == child_path
                        and lo.source is not None
                        and not lo.source.expires
                        and lo.payload is not None
                        and lo.payload.kind is IRNodeKind.SUBSECTION
                    )
                ),
                None,
            )
            if prior_payload is not None:
                new_children.append(prior_payload)
            else:
                base_child_payload = _subsection_node_from_base_ir(base_ir, child_path)
                if base_child_payload is not None:
                    new_children.append(base_child_payload)
            changed = True
        if not changed:
            return None
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_DROP_EXPIRED_TEMPORARY_SUBSECTION,
                    live_sibling_count=len(
                        [child for child in section_payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                    payload_sibling_count=len(
                        [child for child in new_children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                )
            )
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(new_children),
        )

    def _drop_absent_carried_snapshot_subsections(
        section_path: Path,
        section_payload: IRNode,
    ) -> IRNode | None:
        """Drop carried subsection fragments absent from the latest section snapshot."""
        if section_payload.kind is not IRNodeKind.SECTION:
            return None
        latest = _latest_section_snapshot_payload(
            section_path=section_path,
            replay_history_ops=lo_ops_out,
        )
        if latest is None or latest.payload is None or latest.payload.kind is not IRNodeKind.SECTION:
            return None
        latest_labels = {
            _norm_num_token(child.label)
            for child in latest.payload.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
        }
        if not latest_labels:
            return None
        source_text = " ".join(str(op_source.raw_text or "").split())

        def _norm_text(node: IRNode) -> str:
            return " ".join(irnode_to_text(node).split())

        new_children: list[IRNode] = []
        removed = 0
        for child in section_payload.children:
            if child.kind is IRNodeKind.SUBSECTION and child.label:
                child_norm = _norm_num_token(child.label)
                child_text = _norm_text(child)
                if (
                    child_norm not in latest_labels
                    and child.attrs.get("lawvm_in_place_merge") == "1"
                    and child_text
                    and child_text not in source_text
                ):
                    removed += 1
                    continue
            new_children.append(child)
        if removed == 0:
            return None
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_DROP_ABSENT_CARRIED_SUBSECTION,
                    live_sibling_count=len(
                        [child for child in section_payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                    payload_sibling_count=len(
                        [child for child in new_children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                )
            )
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(new_children),
        )

    def _latest_snapshot_for_path(target_path: Path) -> _LegalOperation | None:
        return _latest_target_op_for_path(lo_ops_out, target_path)

    def _drop_expired_temporary_paragraph_children(
        child_path: Path,
        subsection_payload: IRNode,
        *,
        preserve_paragraph_labels: set[str] | None = None,
    ) -> IRNode | None:
        if subsection_payload.kind is not IRNodeKind.SUBSECTION:
            return None
        preserve_labels = preserve_paragraph_labels or set()
        subsection_label = _norm_num_token(child_path[-1][1]) if child_path else ""
        targets_this_subsection = False
        for rop in group_rops:
            rop_subsection = _norm_num_token(_snapshot_subsection_target_label(rop))
            rop_targets_subsection = (
                rop_subsection == subsection_label
                and (_snapshot_targets_subsection_only(rop) or bool(rop.resolved_target_item_label))
            )
            if rop_targets_subsection:
                targets_this_subsection = True
            amend_sub = rop.resolved_amend_sub_ir()
            if (
                rop_targets_subsection
                and amend_sub is not None
                and irnode_to_text(amend_sub) == irnode_to_text(subsection_payload)
            ):
                return None
        if not targets_this_subsection:
            return None
        latest = _latest_snapshot_for_path(child_path)
        if latest is None or latest.payload is None or latest.payload.kind is not IRNodeKind.SUBSECTION:
            return None
        if latest.source is None:
            return None
        latest_expires = latest.source.expires or ""
        if op_source.effective and latest_expires and op_source.effective < latest_expires:
            return None

        def _norm_text(node: IRNode) -> str:
            return " ".join(irnode_to_text(node).split())

        def _norm_paragraph_body_text(node: IRNode) -> str:
            text = _norm_text(node)
            if node.kind is not IRNodeKind.PARAGRAPH or not node.label:
                return text
            return _paragraph_label_prefix_re(str(node.label)).sub("", text, count=1)

        if not op_source.effective:
            return None

        prior_payload: IRNode | None = None
        expired_overlay_payloads: list[IRNode] = []
        found_prior_permanent_payload = False
        for prior in reversed(lo_ops_out):
            if prior.target.special is not None or prior.target.path != child_path:
                continue
            if prior.source is None:
                break
            if prior.source.expires:
                if op_source.effective >= prior.source.expires and prior.payload is not None and prior.payload.kind is IRNodeKind.SUBSECTION:
                    expired_overlay_payloads.append(prior.payload)
                continue
            if (
                not found_prior_permanent_payload
                and prior.payload is not None
                and prior.payload.kind is IRNodeKind.SUBSECTION
            ):
                prior_payload = prior.payload
                found_prior_permanent_payload = True
        prior_paragraph_texts = {
            _norm_paragraph_body_text(child)
            for child in (prior_payload.children if prior_payload is not None else ())
            if child.kind is IRNodeKind.PARAGRAPH and _norm_paragraph_body_text(child)
        }
        prior_paragraphs_by_label = {
            leaf_label_identity_key(child.label): child
            for child in (prior_payload.children if prior_payload is not None else ())
            if child.kind is IRNodeKind.PARAGRAPH and child.label
        }
        expired_paragraph_texts = {
            _norm_paragraph_body_text(child)
            for payload in expired_overlay_payloads
            for child in payload.children
            if child.kind is IRNodeKind.PARAGRAPH and _norm_paragraph_body_text(child)
        } - prior_paragraph_texts
        source_text = " ".join(str(op_source.raw_text or "").split())
        if not latest_expires:
            complete_owner = _snapshot_payload_is_complete_owner(latest.payload)
            if not complete_owner and not expired_paragraph_texts:
                return None
            latest_labels = {
                leaf_label_identity_key(child.label)
                for child in latest.payload.children
                if child.kind is IRNodeKind.PARAGRAPH and child.label
            }
            if not latest_labels:
                return None
            new_children: list[IRNode] = []
            removed = 0
            for child in subsection_payload.children:
                if child.kind is IRNodeKind.PARAGRAPH and child.label:
                    if leaf_label_identity_key(child.label) in preserve_labels:
                        new_children.append(child)
                        continue
                    child_text = _norm_paragraph_body_text(child)
                    expired_overlay_child = child_text in expired_paragraph_texts
                    if (
                        leaf_label_identity_key(child.label) not in latest_labels
                        and child_text
                        and child_text not in source_text
                        and (complete_owner or expired_overlay_child)
                    ):
                        removed += 1
                        continue
                new_children.append(child)
            if removed == 0:
                return None
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=op_source.statute_id,
                        target_unit_kind="section",
                        target_label=target_norm,
                        recovery_kind=RecoveryKind.SUBSECTION_SNAPSHOT_DROP_ABSENT_CARRIED_PARAGRAPH,
                        live_sibling_count=len(
                            [child for child in subsection_payload.children if child.kind is IRNodeKind.PARAGRAPH]
                        ),
                        payload_sibling_count=len(
                            [child for child in new_children if child.kind is IRNodeKind.PARAGRAPH]
                        ),
                    )
                )
            return IRNode(
                kind=subsection_payload.kind,
                label=subsection_payload.label,
                text=subsection_payload.text,
                attrs=dict(subsection_payload.attrs),
                children=tuple(new_children),
            )
        if not expired_paragraph_texts:
            return None
        new_children: list[IRNode] = []
        removed = 0
        for child in subsection_payload.children:
            if child.kind is IRNodeKind.PARAGRAPH:
                child_text = _norm_paragraph_body_text(child)
                if child_text in expired_paragraph_texts and child_text not in source_text:
                    if child.label:
                        prior_child = prior_paragraphs_by_label.get(leaf_label_identity_key(child.label))
                        if prior_child is not None:
                            new_children.append(prior_child)
                            removed += 1
                            continue
                    removed += 1
                    continue
            new_children.append(child)
        if removed == 0:
            return None
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=target_norm,
                    recovery_kind=RecoveryKind.SUBSECTION_SNAPSHOT_DROP_EXPIRED_TEMPORARY_PARAGRAPH,
                    live_sibling_count=len(
                        [child for child in subsection_payload.children if child.kind is IRNodeKind.PARAGRAPH]
                    ),
                    payload_sibling_count=len(
                        [child for child in new_children if child.kind is IRNodeKind.PARAGRAPH]
                    ),
                )
            )
        return IRNode(
            kind=subsection_payload.kind,
            label=subsection_payload.label,
            text=subsection_payload.text,
            attrs=dict(subsection_payload.attrs),
            children=tuple(new_children),
        )

    def _explicitly_targeted_paragraph_labels_by_subsection() -> dict[str, set[str]]:
        labels: dict[str, set[str]] = {}
        for rop in group_rops:
            subsection_label = str(rop.resolved_target_subsection_label or "").strip()
            item_label = str(rop.resolved_target_item_label or "").strip()
            if (
                subsection_label
                and item_label
                and (rop.is_insert_action or rop.is_replace_action)
            ):
                labels.setdefault(_norm_num_token(subsection_label), set()).add(
                    leaf_label_identity_key(item_label)
                )
        return labels

    def _sanitize_section_subsection_payloads(
        section_path: Path,
        section_payload: IRNode,
    ) -> IRNode | None:
        if section_payload.kind is not IRNodeKind.SECTION:
            return None
        preserve_labels_by_subsection = _explicitly_targeted_paragraph_labels_by_subsection()
        changed = False
        new_children: list[IRNode] = []
        for child in section_payload.children:
            if child.kind is IRNodeKind.SUBSECTION and child.label:
                child_path = section_path + (("subsection", child.label),)
                child_norm_label = _norm_num_token(child.label)
                sanitized = _drop_expired_temporary_paragraph_children(
                    child_path,
                    child,
                    preserve_paragraph_labels=preserve_labels_by_subsection.get(
                        child_norm_label,
                        set(),
                    ),
                )
                if sanitized is not None:
                    child = sanitized
                    changed = True
            new_children.append(child)
        if not changed:
            return None
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(new_children),
        )

    def _drop_carried_target_subsection_text_from_siblings(
        section_path: Path,
        section_payload: IRNode,
        *,
        include_content_prefix: bool = True,
    ) -> IRNode | None:
        if section_payload.kind is not IRNodeKind.SECTION:
            return None

        def _norm_text(node: IRNode) -> str:
            return " ".join(irnode_to_text(node).split())

        def _signature_prefix(text: str) -> str:
            if len(text) < 96:
                return ""
            return text[:96]

        def _leading_token_signature(text: str) -> tuple[str, ...]:
            tokens: list[str] = []
            for raw in text.split():
                token = raw.strip(" \t\r\n.,;:!?()[]{}\"'“”’`´")
                if token:
                    tokens.append(token.casefold())
                if len(tokens) == 6:
                    break
            return tuple(tokens) if len(tokens) == 6 else ()

        def _rop_targets_this_section(rop: ResolvedOp) -> bool:
            rop_section = _norm_num_token(rop.resolved_target_section_label or rop.target_norm)
            if rop_section != normalized_target_norm:
                return False
            current_chapter = _norm_num_token(target_chapter or "")
            rop_chapter = _norm_num_token(rop.resolved_target_scope_chapter_label or "")
            if current_chapter and rop_chapter and rop_chapter != current_chapter:
                return False
            current_part = _norm_num_token(target_part or "")
            rop_part = _norm_num_token(rop.resolved_target_scope_part_label or "")
            if current_part and rop_part and rop_part != current_part:
                return False
            return True

        if section_payload.attrs.get("lawvm_consumed_subsection_targets"):
            return None

        source_text = " ".join(str(op_source.raw_text or "").split())
        target_prefixes_by_label: dict[str, set[str]] = {}
        target_replacement_children_by_label: dict[str, tuple[IRNode, ...]] = {}
        target_replacement_text_by_label: dict[str, str] = {}
        target_texts_by_label: dict[str, set[str]] = {}
        target_token_signatures_by_label: dict[str, set[tuple[str, ...]]] = {}
        for rop in group_rops:
            if not _rop_targets_this_section(rop):
                continue
            if not rop.is_replace_action or not _snapshot_targets_subsection_only(rop):
                continue
            target_label = _snapshot_subsection_target_label(rop)
            if not target_label:
                continue
            amend_sub = rop.resolved_amend_sub_ir()
            if amend_sub is None or amend_sub.kind is not IRNodeKind.SUBSECTION:
                continue
            target_text = _norm_text(amend_sub)
            target_key = _norm_num_token(target_label)
            target_texts_by_label.setdefault(target_key, set()).add(target_text)
            target_replacement_children_by_label[target_key] = tuple(amend_sub.children)
            target_replacement_text_by_label[target_key] = target_text
            prefix = _signature_prefix(target_text)
            if not prefix:
                token_signature = _leading_token_signature(target_text)
            else:
                token_signature = _leading_token_signature(target_text)
                target_prefixes_by_label.setdefault(target_key, set()).add(prefix)
            if token_signature:
                target_token_signatures_by_label.setdefault(
                    target_key,
                    set(),
                ).add(token_signature)
        if not target_prefixes_by_label and not target_token_signatures_by_label and not target_texts_by_label:
            return None

        changed = False
        new_section_children: list[IRNode] = []
        removed = 0
        consumed_target_labels: set[str] = set()
        target_labels = set(target_prefixes_by_label) | set(target_token_signatures_by_label) | set(target_texts_by_label)
        for subsection in section_payload.children:
            if subsection.kind is not IRNodeKind.SUBSECTION or not subsection.label:
                new_section_children.append(subsection)
                continue
            subsection_key = _norm_num_token(subsection.label)
            if subsection_key in consumed_target_labels:
                changed = True
                continue
            if subsection_key in target_labels:
                new_section_children.append(subsection)
                continue
            target_prefixes = {
                prefix
                for prefixes in target_prefixes_by_label.values()
                for prefix in prefixes
            }
            target_token_signatures = {
                token_signature
                for token_signatures in target_token_signatures_by_label.values()
                for token_signature in token_signatures
            }

            new_subsection_children: list[IRNode] = []
            subsection_changed = False
            for child in subsection.children:
                child_text = _norm_text(child)
                if any(child_text in texts for texts in target_texts_by_label.values()):
                    new_subsection_children.append(child)
                    continue
                carried_child_kind = child.kind in {
                    IRNodeKind.CONTENT,
                    IRNodeKind.PARAGRAPH,
                    IRNodeKind.WRAP_UP,
                }
                strict_prefix_labels = {
                    label
                    for label, texts in target_texts_by_label.items()
                    for target_text in texts
                    if target_text != child_text
                    and len(child_text) >= 40
                    and target_text.startswith(child_text)
                }
                strict_target_prefix = bool(strict_prefix_labels)
                carried_target_text = strict_target_prefix or (
                    include_content_prefix
                    and child.kind is IRNodeKind.CONTENT
                    and any(child_text.startswith(prefix) for prefix in target_prefixes)
                ) or (
                    carried_child_kind
                    and _leading_token_signature(child_text) in target_token_signatures
                )
                if (
                    carried_target_text
                    and child_text
                    and (child_text not in source_text or strict_target_prefix)
                ):
                    removed += 1
                    changed = True
                    subsection_changed = True
                    if strict_target_prefix and child.kind in {IRNodeKind.CONTENT, IRNodeKind.WRAP_UP}:
                        consumed_label = sorted(strict_prefix_labels)[0]
                        replacement_children = target_replacement_children_by_label.get(consumed_label, ())
                        if not replacement_children:
                            replacement_text = target_replacement_text_by_label.get(consumed_label, "")
                            replacement_children = (IRNode(kind=IRNodeKind.CONTENT, text=replacement_text),)
                        new_subsection_children.extend(replacement_children)
                        consumed_target_labels.add(consumed_label)
                    continue
                new_subsection_children.append(child)
            if not subsection_changed:
                new_section_children.append(subsection)
                continue
            new_section_children.append(
                IRNode(
                    kind=subsection.kind,
                    label=subsection.label,
                    text=subsection.text,
                    attrs=dict(subsection.attrs),
                    children=tuple(new_subsection_children),
                )
            )

        if not changed:
            return None
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_DROP_CARRIED_TARGET_SUBSECTION_TEXT,
                    live_sibling_count=len(
                        [child for child in section_payload.children if child.kind is IRNodeKind.SUBSECTION]
                    ),
                    payload_sibling_count=removed,
                )
            )
        attrs = dict(section_payload.attrs)
        if consumed_target_labels:
            attrs["lawvm_consumed_subsection_targets"] = ",".join(sorted(consumed_target_labels))
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=attrs,
            children=tuple(new_section_children),
        )

    def _relabel_subsection_payload(subsection: IRNode, label: str) -> IRNode:
        if subsection.label == label:
            return subsection
        return IRNode(
            kind=subsection.kind,
            label=label,
            text=subsection.text,
            attrs=dict(subsection.attrs),
            children=tuple(subsection.children),
        )

    def _explicit_subsection_group_snapshot_payload(
        section_path: Path,
        section_payload: IRNode,
    ) -> IRNode | None:
        """Materialize explicit subsection ops over the prior section snapshot.

        Sparse Finlex amendment bodies can serialize a section as
        ``subsection / omission / subsection...`` while the johtolause supplies
        the true target labels.  If the mutable replay fold kept the dense body
        labels, timeline export must prefer the elaborated source-owned
        subsection payloads over that lossy section observation.
        """
        if section_payload.kind is not IRNodeKind.SECTION:
            return None
        if section_payload.attrs.get("lawvm_consumed_subsection_targets"):
            return None

        def _targets_current_section(rop: ResolvedOp) -> bool:
            section_label = rop.resolved_target_section_label or rop.target_norm
            if _norm_num_token(str(section_label or "")) != normalized_target_norm:
                return False
            rop_chapter = rop.resolved_target_scope_chapter_label
            if target_chapter and rop_chapter and _norm_num_token(rop_chapter) != _norm_num_token(target_chapter):
                return False
            rop_part = rop.resolved_target_scope_part_label
            if target_part and rop_part and _norm_num_token(rop_part) != _norm_num_token(target_part):
                return False
            return True

        section_group_rops = [
            rop for rop in group_rops if _targets_current_section(rop)
        ]
        if not section_group_rops:
            return None

        subsection_payloads: dict[str, IRNode] = {}
        pending_subsection_payloads: list[_PendingSubsectionSnapshotPayload] = []
        repealed_item_labels_by_subsection: dict[str, set[str]] = {}
        item_target_labels_by_subsection: dict[str, set[str]] = {}
        renumber_destinations: dict[str, str] = {}
        renumber_destination_labels = {
            _norm_num_token(destination_path[-1][1])
            for rop in section_group_rops
            if (destination_path := _resolved_destination_path_for_rop(rop))
            and destination_path[-1][0] == "subsection"
        }
        whole_repealed_source_labels: set[str] = set()
        whole_subsection_targets: set[str] = set()
        has_insert = False
        has_item_scoped_ops = any(
            rop.resolved_target_subsection_label is not None
            and rop.resolved_target_item_label is not None
            for rop in section_group_rops
        )
        for rop in section_group_rops:
            if rop.effective_target_special in {"otsikko", "otsikko_edella"}:
                continue
            if rop.is_renumber_action and rop.targets_subsection_only():
                source_label = _norm_num_token(str(rop.resolved_target_subsection_label or "").strip())
                destination = rop.resolved_destination_address
                destination_label = ""
                if destination is not None:
                    destination_label = _norm_num_token(
                        next((label for kind, label in reversed(destination.path) if kind == "subsection"), "")
                    )
                if not source_label or not destination_label:
                    return None
                renumber_destinations[source_label] = destination_label
                amend_sub = rop.resolved_amend_sub_ir()
                if amend_sub is not None and amend_sub.kind is IRNodeKind.SUBSECTION:
                    relabelled = _relabel_subsection_payload(amend_sub, destination_label)
                    existing = subsection_payloads.get(destination_label)
                    if existing is not None and irnode_to_text(existing) != irnode_to_text(relabelled):
                        return None
                    subsection_payloads[destination_label] = relabelled
                    whole_subsection_targets.add(destination_label)
                continue
            if rop.is_repeal_action:
                if (
                    rop.resolved_target_subsection_label is not None
                    and rop.resolved_target_item_label is not None
                ):
                    target_label = _norm_num_token(
                        str(rop.resolved_target_subsection_label or "").strip()
                    )
                    item_label = _normalize_snapshot_item_label(str(rop.resolved_target_item_label or "").strip())
                    if not target_label or not item_label:
                        return None
                    repealed_item_labels_by_subsection.setdefault(target_label, set()).add(item_label)
                    continue
                if has_item_scoped_ops and rop.targets_subsection_only():
                    # Whole-subsection repeals are emitted as explicit child
                    # tombstones after the section snapshot. They must not make
                    # item-scoped sibling payloads fall back to the stale live
                    # fold for the whole section.
                    continue
                if rop.targets_subsection_only():
                    target_label = _norm_num_token(
                        str(rop.resolved_target_subsection_label or "").strip()
                    )
                    if target_label and target_label in renumber_destination_labels:
                        whole_repealed_source_labels.add(target_label)
                        continue
                else:
                    return None
            if not (rop.is_insert_action or rop.is_replace_action):
                return None
            targets_subsection_payload = rop.targets_subsection_only() or (
                rop.resolved_target_subsection_label is not None
                and rop.resolved_target_item_label is not None
            )
            if not targets_subsection_payload:
                return None
            target_label = str(rop.resolved_target_subsection_label or "").strip()
            target_norm = _norm_num_token(target_label)
            if not target_norm:
                return None
            item_label = str(rop.resolved_target_item_label or "").strip()
            item_norm = _normalize_snapshot_item_label(item_label)
            amend_sub = rop.resolved_amend_sub_ir()
            if amend_sub is None and item_norm:
                amend_item = _find_amend_paragraph(item_norm, None, rop.muutos_ir)
                if amend_item is not None:
                    amend_sub = IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label=target_norm,
                        children=(amend_item,),
                    )
            if amend_sub is None or amend_sub.kind is not IRNodeKind.SUBSECTION:
                return None
            relabelled = _relabel_subsection_payload(amend_sub, target_norm)
            target_already_rebased = has_recognizer(
                rop.provenance, RecognizerId.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE
            )
            pending_subsection_payloads.append(
                _PendingSubsectionSnapshotPayload(
                    target_norm=target_norm,
                    payload=relabelled,
                    is_insert_action=rop.is_insert_action,
                    has_item_target=bool(item_label),
                    item_norm=item_norm,
                    target_already_rebased=target_already_rebased,
                )
            )
            has_insert = has_insert or rop.is_insert_action

        for pending_payload in pending_subsection_payloads:
            effective_norm = pending_payload.target_norm
            relabelled = pending_payload.payload
            if (
                not pending_payload.is_insert_action
                and not pending_payload.target_already_rebased
            ):
                effective_norm = renumber_destinations.get(pending_payload.target_norm, pending_payload.target_norm)
            relabelled = _relabel_subsection_payload(relabelled, effective_norm)
            existing = subsection_payloads.get(effective_norm)
            if existing is not None and irnode_to_text(existing) != irnode_to_text(relabelled):
                return None
            subsection_payloads[effective_norm] = relabelled
            if not pending_payload.has_item_target:
                whole_subsection_targets.add(effective_norm)
            else:
                item_target_labels_by_subsection.setdefault(effective_norm, set()).add(
                    pending_payload.item_norm
                )

        single_sparse_whole_subsection_replace = (
            len(subsection_payloads) == 1
            and not renumber_destinations
            and not repealed_item_labels_by_subsection
            and not item_target_labels_by_subsection
            and len(section_group_rops) == 1
            and section_group_rops[0].is_replace_action
            and section_group_rops[0].targets_subsection_only()
            and section_group_rops[0].payload_completeness is not None
            and str(section_group_rops[0].payload_completeness.tail_policy or "").strip()
            == "preserve_unstated_tail"
        )
        if (
            len(subsection_payloads) < 2
            and not renumber_destinations
            and not single_sparse_whole_subsection_replace
        ):
            has_item_payload = any(rop.resolved_target_item_label is not None for rop in section_group_rops)
            if not has_item_payload or not subsection_payloads:
                return None

        current_by_label = {
            _norm_num_token(child.label): child
            for child in section_payload.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
        }
        if (
            not single_sparse_whole_subsection_replace
            and not repealed_item_labels_by_subsection
            and all(
                label in current_by_label
                and irnode_to_text(current_by_label[label])
                == irnode_to_text(subsection_payload)
                for label, subsection_payload in subsection_payloads.items()
            )
        ):
            return None

        rebased_from_expired_temporary_snapshot = False
        latest = _latest_section_snapshot_payload(
            section_path=section_path,
            replay_history_ops=lo_ops_out,
        )

        def _labeled_descendant_addresses(node: IRNode) -> set[tuple[str, str]]:
            addresses: set[tuple[str, str]] = set()
            stack = list(node.children)
            while stack:
                child = stack.pop()
                if child.label:
                    addresses.add((child.kind.value, _norm_num_token(child.label)))
                stack.extend(child.children)
            return addresses

        def _live_section_is_safe_sparse_merge_base(
            latest_section: IRNode,
            live_section: IRNode,
        ) -> bool:
            touched_labels = set(subsection_payloads)
            latest_children = {
                _norm_num_token(child.label): child
                for child in latest_section.children
                if child.kind is IRNodeKind.SUBSECTION and child.label
            }
            live_children = {
                _norm_num_token(child.label): child
                for child in live_section.children
                if child.kind is IRNodeKind.SUBSECTION and child.label
            }
            if set(live_children) - set(latest_children):
                return False
            untouched_differences = 0
            for label, live_child in live_children.items():
                if label in touched_labels:
                    continue
                latest_child = latest_children.get(label)
                if latest_child is None:
                    return False
                if irnode_to_text(live_child) == irnode_to_text(latest_child):
                    continue
                if _labeled_descendant_addresses(live_child) - _labeled_descendant_addresses(latest_child):
                    return False
                untouched_differences += 1
            if untouched_differences <= 1:
                return True
            current_effective = op_source.effective or op_source.enacted
            if not current_effective:
                return False
            for latest_child in latest_section.children:
                if latest_child.kind is not IRNodeKind.SUBSECTION or not latest_child.label:
                    continue
                child_label = _norm_num_token(latest_child.label)
                child_snapshot = _latest_snapshot_for_path(section_path + (("subsection", child_label),))
                if child_snapshot is None or child_snapshot.source is None:
                    continue
                child_expires = child_snapshot.source.expires or ""
                if not child_expires or current_effective < child_expires:
                    continue
                live_child = live_children.get(child_label)
                if live_child is not None and irnode_to_text(live_child) != irnode_to_text(latest_child):
                    return True
            return False

        if latest is not None and latest.payload is not None and latest.payload.kind is IRNodeKind.SECTION:
            latest_expires = latest.source.expires if latest.source is not None else ""
            if latest_expires and op_source.effective and op_source.effective >= latest_expires:
                prior_payload = _prior_non_temporary_section_snapshot_payload(
                    section_path=section_path,
                    replay_history_ops=lo_ops_out,
                    current_effective=op_source.effective,
                    base_ir=base_ir,
                )
                base_section = prior_payload if prior_payload is not None else latest.payload
                rebased_from_expired_temporary_snapshot = prior_payload is not None
            else:
                base_section = latest.payload
            if (
                single_sparse_whole_subsection_replace
                and latest_expires == ""
                and section_payload != latest.payload
            ):
                live_path = state.find_section_path(
                    normalized_target_norm,
                    target_chapter,
                    target_part,
                )
                live_payload = _tops.resolve(state.ir, live_path) if live_path is not None else None
                if (
                    live_payload is not None
                    and live_payload.kind is IRNodeKind.SECTION
                    and not any(child.kind is IRNodeKind.OMISSION for child in live_payload.children)
                    and _live_section_is_safe_sparse_merge_base(
                        latest.payload,
                        live_payload,
                    )
                ):
                    base_section = live_payload
        else:
            base_section = _section_node_from_base_ir(base_ir, section_path)
        if base_section is None or base_section.kind is not IRNodeKind.SECTION:
            return None

        current_children = list(section_payload.children)
        first_current_subsection = next(
            (idx for idx, child in enumerate(current_children) if child.kind is IRNodeKind.SUBSECTION),
            len(current_children),
        )
        if any(
            child.kind not in {IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}
            for child in current_children[first_current_subsection:]
        ):
            return None
        current_prefix_children = [
            child for child in current_children[:first_current_subsection]
            if child.kind is not IRNodeKind.OMISSION
        ]

        def _subsection_covers_item_payloads(
            current_subsection: IRNode,
            item_payloads: dict[str, IRNode],
        ) -> bool:
            if not item_payloads:
                return False
            current_items = {
                _normalize_snapshot_item_label(child.label): child
                for child in current_subsection.children
                if child.kind is IRNodeKind.PARAGRAPH and child.label
            }
            for item_label, payload_item in item_payloads.items():
                current_item = current_items.get(item_label)
                if current_item is None:
                    return False
                payload_subitems = {
                    _normalize_snapshot_item_label(child.label): irnode_to_text(child)
                    for child in payload_item.children
                    if child.kind is IRNodeKind.SUBPARAGRAPH and child.label
                }
                if not payload_subitems:
                    if irnode_to_text(payload_item) != irnode_to_text(current_item):
                        return False
                    continue
                current_subitems = {
                    _normalize_snapshot_item_label(child.label): irnode_to_text(child)
                    for child in current_item.children
                    if child.kind is IRNodeKind.SUBPARAGRAPH and child.label
                }
                for subitem_label, payload_text in payload_subitems.items():
                    if current_subitems.get(subitem_label) != payload_text:
                        return False
            return True

        base_by_label: dict[str, IRNode] = {}
        for child in base_section.children:
            if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                continue
            source_label = _norm_num_token(child.label)
            if source_label in whole_repealed_source_labels:
                continue
            destination_label = renumber_destinations.get(source_label, source_label)
            base_by_label[destination_label] = _relabel_subsection_payload(child, destination_label)
        final_payloads: dict[str, IRNode] = {}
        used_flattened_item_payload = False
        flattened_item_payload_count = 0
        touched_labels = sorted(
            set(subsection_payloads) | set(repealed_item_labels_by_subsection),
            key=default_label_sort_key,
        )
        for label in touched_labels:
            subsection_payload = subsection_payloads.get(label)
            if subsection_payload is not None and label in whole_subsection_targets:
                final_payloads[label] = subsection_payload
                continue
            base_subsection = base_by_label.get(label)
            if base_subsection is None:
                if subsection_payload is not None:
                    final_payloads[label] = subsection_payload
                continue
            payload_items: dict[str, IRNode] = {}
            if subsection_payload is not None:
                payload_items = {
                    _normalize_snapshot_item_label(child.label): child
                    for child in subsection_payload.children
                    if child.kind is IRNodeKind.PARAGRAPH and child.label
                }
            if not payload_items:
                item_labels = {
                    _normalize_snapshot_item_label(str(rop.resolved_target_item_label or "").strip())
                    for rop in section_group_rops
                    if _norm_num_token(str(rop.resolved_target_subsection_label or "").strip()) == label
                    and rop.resolved_target_item_label
                }
                for item_label in item_labels:
                    flattened_item = _find_amend_paragraph(item_label, subsection_payload, None)
                    if flattened_item is None or not flattened_item.label:
                        continue
                    payload_items[_normalize_snapshot_item_label(flattened_item.label)] = flattened_item
                    used_flattened_item_payload = True
                    flattened_item_payload_count += 1
            repealed_items = repealed_item_labels_by_subsection.get(label, set())
            if not payload_items and not repealed_items:
                assert subsection_payload is not None
                final_payloads[label] = subsection_payload
                continue
            if (
                not repealed_items
                and label in item_target_labels_by_subsection
                and label in current_by_label
            ):
                current_subsection = current_by_label[label]
                if _subsection_covers_item_payloads(current_subsection, payload_items):
                    final_payloads[label] = current_subsection
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=op_source.statute_id,
                                target_unit_kind="section",
                                target_label=normalized_target_norm,
                                recovery_kind=RecoveryKind.SECTION_SNAPSHOT_ITEM_PAYLOAD_FOLD_MERGE,
                                live_sibling_count=sum(
                                    1
                                    for child in current_subsection.children
                                    if child.kind is IRNodeKind.PARAGRAPH
                                ),
                                payload_sibling_count=len(payload_items),
                            )
                        )
                    continue
            first_base_item = next(
                (idx for idx, child in enumerate(base_subsection.children) if child.kind is IRNodeKind.PARAGRAPH),
                len(base_subsection.children),
            )
            if any(child.kind is not IRNodeKind.PARAGRAPH for child in base_subsection.children[first_base_item:]):
                return None
            base_items = {
                _norm_num_token(child.label): child
                for child in base_subsection.children
                if child.kind is IRNodeKind.PARAGRAPH and child.label
            }
            merged_item_labels = sorted(
                (set(base_items) | set(payload_items)) - repealed_items,
                key=default_label_sort_key,
            )
            final_payloads[label] = IRNode(
                kind=base_subsection.kind,
                label=base_subsection.label,
                text=base_subsection.text,
                attrs=dict(base_subsection.attrs),
                children=tuple(
                    list(base_subsection.children[:first_base_item])
                    + [payload_items.get(item_label) or base_items[item_label] for item_label in merged_item_labels]
                ),
            )
        merged_labels = sorted(set(base_by_label) | set(subsection_payloads), key=default_label_sort_key)
        merged_subsections = [
            final_payloads.get(label) or base_by_label[label]
            for label in merged_labels
        ]
        if used_flattened_item_payload and source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=normalized_target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_FLATTENED_ITEM_PAYLOAD_MERGE,
                    live_sibling_count=sum(
                        1
                        for child in base_section.children
                        if child.kind is IRNodeKind.SUBSECTION
                        for grandchild in child.children
                        if grandchild.kind is IRNodeKind.PARAGRAPH
                    ),
                    payload_sibling_count=sum(
                        1
                        for subsection_payload in subsection_payloads.values()
                        for child in subsection_payload.children
                        if child.kind is IRNodeKind.PARAGRAPH
                    )
                    + flattened_item_payload_count,
                )
            )
        if single_sparse_whole_subsection_replace and source_pathologies_out is not None:
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=op_source.statute_id,
                    target_unit_kind="section",
                    target_label=normalized_target_norm,
                    recovery_kind=RecoveryKind.SECTION_SNAPSHOT_SINGLE_SUBSECTION_SPARSE_MERGE,
                    live_sibling_count=sum(
                        1
                        for child in base_section.children
                        if child.kind is IRNodeKind.SUBSECTION
                    ),
                    payload_sibling_count=len(subsection_payloads),
                )
            )
        merged_section = IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(current_prefix_children + merged_subsections),
        )
        if single_sparse_whole_subsection_replace and rebased_from_expired_temporary_snapshot:
            merged_section = _stamp_exact_section_snapshot_payload(merged_section)
        return _drop_shifted_expired_temporary_subsection_payload(section_path, merged_section) or merged_section

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
        label_index = base_provision_index if tree is base_ir else None
        for label in _candidate_lookup_labels():
            raw_path = _tops.find(tree, kind_name, label, label_index=label_index)
            if raw_path:
                return _tops._as_path(raw_path)
        return _find_normalized_container_path_in_tree(tree, kind_name)

    base_resolved_path_cache: tuple[bool, Optional[Path]] = (False, None)

    def _base_resolved_path() -> Optional[Path]:
        nonlocal base_resolved_path_cache
        if base_resolved_path_cache[0]:
            return base_resolved_path_cache[1]
        if base_ir is None:
            base_resolved_path_cache = (True, None)
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
                base_resolved_path_cache = (True, _timeline_path(hinted_path))
                return base_resolved_path_cache[1]
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
                    label_index=base_provision_index,
                )
        elif target_unit_kind == "chapter":
            raw_path = _lookup_container_path_in_tree(base_ir, "chapter")
        elif target_unit_kind == "part":
            raw_path = _lookup_container_path_in_tree(base_ir, "part")
        else:
            raw_path = None
        base_resolved_path_cache = (
            True,
            _timeline_path(_tops._as_path(raw_path)) if raw_path else None,
        )
        return base_resolved_path_cache[1]

    def _base_section_payload_for_complete_replacement() -> Optional[IRNode]:
        if base_ir is None or target_unit_kind != "section":
            return None
        if base_provision_index is None:
            return None
        base_path = _base_resolved_path()
        base_node = _tops.resolve(base_ir, base_path) if base_path is not None else None
        if base_node is not None and base_node.kind is IRNodeKind.SECTION:
            return base_node
        base_matches = base_provision_index.get(
            ("section", normalized_label_key(normalized_target_norm)),
            [],
        )
        if len(base_matches) != 1:
            return None
        unique_base_node = _tops.resolve(base_ir, base_matches[0])
        return unique_base_node if unique_base_node is not None and unique_base_node.kind is IRNodeKind.SECTION else None

    def _latest_scoped_section_snapshot_path() -> Optional[Path]:
        if target_unit_kind != "section" or not target_chapter:
            return None
        candidates: list[Path] = []
        for lo in reversed(lo_ops_out):
            if lo.target.special is not None:
                continue
            path = lo.target.path
            if not path or path[-1][0] != "section":
                continue
            labels = {kind: label for kind, label in path if label}
            if _norm_num_token(labels.get("section", "")) != normalized_target_norm:
                continue
            if _norm_num_token(labels.get("chapter", "")) != _norm_num_token(target_chapter):
                continue
            if target_part and _norm_num_token(labels.get("part", "")) != _norm_num_token(target_part):
                continue
            if path not in candidates:
                candidates.append(path)
        if not candidates:
            return None
        if target_part:
            return candidates[0]
        candidate_parts = {
            next((label for kind, label in candidate if kind == "part"), "")
            for candidate in candidates
        }
        return candidates[0] if len(candidate_parts) == 1 else None

    def _unique_prior_section_snapshot_path() -> Optional[Path]:
        if target_unit_kind != "section" or target_chapter or target_part:
            return None
        if not _group_has_descendant_scoped_snapshot_mutations(group_rops):
            return None
        candidates: list[Path] = []
        for lo in reversed(lo_ops_out):
            if lo.target.special is not None:
                continue
            path = lo.target.path
            if not path or path[-1][0] != "section":
                continue
            if _norm_num_token(path[-1][1]) != normalized_target_norm:
                continue
            if path not in candidates:
                candidates.append(path)
        if len(candidates) != 1:
            return None
        return candidates[0]

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
            and _timeline_target_exists_for_snapshot(
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
    prior_timeline_path = _unique_prior_section_snapshot_path()
    raw_path_from_timeline = False
    if hinted_path is not None:
        emitted_path = prior_timeline_path or _project_snapshot_path(hinted_path)
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
        if prior_timeline_path is not None:
            raw_path = prior_timeline_path
            raw_path_from_timeline = True
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
            raw_path = _latest_scoped_section_snapshot_path()
            raw_path_from_timeline = raw_path is not None
        if not raw_path and target_chapter and not target_part:
            # Explicit chapter scope outranks homonymous global fallback.  The
            # mutable replay fold can lose a base section before a later
            # source-direct subsection replacement, but the timeline address is
            # still the scoped base address, not an unrelated unique live section.
            _is_non_insert = group_rops and all(not rop.is_insert_action for rop in group_rops)
            if _is_non_insert:
                raw_path = _base_resolved_path()
                raw_path_from_timeline = False
        if not raw_path and resolved_path is None and target_chapter and not target_part:
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
                raw_path_from_timeline = False
        if raw_path:
            emitted_path = raw_path if raw_path_from_timeline else (_project_snapshot_path(raw_path) or raw_path)
            payload = _tops.resolve(state.ir, emitted_path)
            if payload is None:
                payload = _tops.resolve(state.ir, raw_path)
            if payload is not None or raw_path_from_timeline:
                resolved_path = _timeline_path(emitted_path)
            elif _whole_target_repeal():
                # Section was already removed from the IR by the REPEAL op.
                # Anchor the tombstone to the path where the section previously
                # lived, even though payload cannot be resolved from current IR.
                resolved_path = _timeline_path(emitted_path)
        if resolved_path is None and target_chapter and not target_part:
            raw_path = _latest_scoped_section_snapshot_path()
            raw_path_from_timeline = raw_path is not None
            if raw_path:
                emitted_path = raw_path
                payload = _tops.resolve(state.ir, emitted_path)
                if payload is None:
                    payload = _tops.resolve(state.ir, raw_path)
                if payload is None and base_ir is not None:
                    payload = _tops.resolve(base_ir, raw_path)
                if payload is not None or raw_path_from_timeline:
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
        if (
            target_unit_kind == "section"
            and _group_has_descendant_scoped_snapshot_mutations(group_rops)
        ):
            live_raw_path = state.find_section_path(
                normalized_target_norm,
                target_chapter,
                target_part,
            )
            if payload is None and live_raw_path is not None:
                live_payload = _tops.resolve(state.ir, live_raw_path)
                if live_payload is not None:
                    payload = live_payload
                    if resolved_path is None:
                        emitted_path = _project_snapshot_path(live_raw_path) or live_raw_path
                        resolved_path = _timeline_path(emitted_path)
            if payload is None and resolved_path is not None:
                for rop in group_rops:
                    source_payload = rop.muutos_ir
                    if source_payload is None or source_payload.kind is not IRNodeKind.SECTION:
                        continue
                    if (
                        source_payload.label
                        and _norm_num_token(source_payload.label) != normalized_target_norm
                    ):
                        continue
                    payload = source_payload
                    payload_from_muutos_ir = True
                    break
        if payload is None:
            expected_kind = _group_payload_kind()
            if expected_kind is not None:
                for rop in group_rops:
                    if rop.muutos_ir is None or _kind_str(rop.muutos_ir.kind) != expected_kind:
                        continue
                    if rop.muutos_ir.label and _norm_num_token(rop.muutos_ir.label) != normalized_target_norm:
                        continue
                    if _group_has_descendant_scoped_snapshot_mutations(group_rops):
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
                raw_path_from_timeline = False
                raw_path = state.find_section_path(label, target_chapter, target_part)
                prior_timeline_path = _unique_prior_section_snapshot_path()
                if prior_timeline_path is not None:
                    raw_path = prior_timeline_path
                    raw_path_from_timeline = True
                if not raw_path and not target_chapter:
                    raw_path = _unique_global_section_path(label)
                if not raw_path and target_chapter and not target_part:
                    raw_path = _latest_scoped_section_snapshot_path()
                    raw_path_from_timeline = raw_path is not None
                if not raw_path and target_chapter and not target_part:
                    _is_non_insert = group_rops and all(not rop.is_insert_action for rop in group_rops)
                    if _is_non_insert:
                        raw_path = _base_resolved_path()
                        raw_path_from_timeline = False
                if not raw_path and target_chapter and not target_part:
                    _is_non_insert = group_rops and all(not rop.is_insert_action for rop in group_rops)
                    if _is_non_insert:
                        raw_path = _unique_global_section_path(label)
                        raw_path_from_timeline = False
                if raw_path:
                    emitted_path = raw_path if raw_path_from_timeline else (_project_snapshot_path(raw_path) or raw_path)
                    payload = _tops.resolve(state.ir, emitted_path)
                    if payload is None:
                        payload = _tops.resolve(state.ir, raw_path)
                    if payload is not None or raw_path_from_timeline:
                        resolved_path = _timeline_path(emitted_path)
                        break
            if resolved_path is None and target_chapter and not target_part:
                raw_path = _latest_scoped_section_snapshot_path()
                raw_path_from_timeline = raw_path is not None
                if raw_path:
                    emitted_path = raw_path
                    payload = _tops.resolve(state.ir, emitted_path)
                    if payload is None:
                        payload = _tops.resolve(state.ir, raw_path)
                    if payload is None and base_ir is not None:
                        payload = _tops.resolve(base_ir, raw_path)
                    if payload is not None or raw_path_from_timeline:
                        resolved_path = _timeline_path(emitted_path)
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
            snapshot_chapter = target_chapter
            if snapshot_chapter is None:
                unique_chapter = _unique_section_chapter(
                    state,
                    normalized_target_norm,
                    part_label=target_part,
                )
                if (
                    unique_chapter is not None
                    and state.find_section_path(
                        normalized_target_norm,
                        unique_chapter,
                        target_part,
                    )
                    is not None
                ):
                    snapshot_chapter = unique_chapter
                if snapshot_chapter is None:
                    snapshot_chapter = infer_letter_suffix_section_chapter_from_stem_host(
                        state,
                        normalized_target_norm,
                        part_label=target_part,
                    )
            if target_part:
                resolved_path = resolved_path + (("part", target_part),)
            if snapshot_chapter and not _use_root_address_for_pseudo_chapter_section():
                resolved_path = resolved_path + (("chapter", snapshot_chapter),)
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
        and not _timeline_target_exists_for_snapshot(
            tuple(resolved_path),
            replay_history_ops=lo_ops_out,
            base_ir=base_ir,
        )
    ):
        action = StructuralAction.INSERT

    if (
        target_unit_kind == "section"
        and target_chapter
        and len(state.provision_index.get(("section", normalized_label_key(normalized_target_norm)), [])) == 1
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
            base_raw_path = _tops.find(
                base_ir,
                "section",
                target_norm,
                label_index=base_provision_index,
            )
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
        and _timeline_target_exists_for_snapshot(
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
    complete_section_replacement_missing_subsections: dict[str, str] = {}
    complete_source_section_payload = _complete_whole_section_source_payload()
    if (
        action in {StructuralAction.REPLACE, StructuralAction.INSERT}
        and resolved_path is not None
        and complete_source_section_payload is not None
    ):
        if (
            action is StructuralAction.REPLACE
            and payload is not None
            and payload.kind is IRNodeKind.SECTION
            and complete_source_section_payload.kind is IRNodeKind.SECTION
        ):
            authoritative_subsection_labels = {
                _norm_num_token(child.label)
                for child in complete_source_section_payload.children
                if child.kind is IRNodeKind.SUBSECTION and child.label
            }
            prior_section_payloads = [payload]
            base_section_payload = (
                None if op_source.expires else _base_section_payload_for_complete_replacement()
            )
            if base_section_payload is not None and base_section_payload is not payload:
                prior_section_payloads.append(base_section_payload)
            for prior_section_payload in prior_section_payloads:
                for child in prior_section_payload.children:
                    if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                        continue
                    child_norm = _norm_num_token(child.label)
                    if child_norm in authoritative_subsection_labels:
                        continue
                    complete_section_replacement_missing_subsections.setdefault(child_norm, child.label)
            if complete_section_replacement_missing_subsections and source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=op_source.statute_id,
                        target_unit_kind="section",
                        target_label=target_norm,
                        recovery_kind=RecoveryKind.SECTION_SNAPSHOT_REPEAL_ABSENT_COMPLETE_REPLACEMENT_SUBSECTION,
                        live_sibling_count=len(
                            {
                                _norm_num_token(child.label)
                                for prior_section_payload in prior_section_payloads
                                for child in prior_section_payload.children
                                if child.kind is IRNodeKind.SUBSECTION and child.label
                            }
                        ),
                        payload_sibling_count=len(authoritative_subsection_labels),
                    )
                )
        payload = complete_source_section_payload
        payload_from_muutos_ir = True
    elif (
        action is StructuralAction.REPLACE
        and resolved_path is not None
        and target_unit_kind == "section"
        and payload is not None
        and payload.kind is IRNodeKind.SECTION
    ):
        rebased_payload = _rebase_section_payload_on_latest_exact_snapshot(tuple(resolved_path), payload)
        if rebased_payload is not None:
            payload = rebased_payload
        pruned_payload = _drop_shifted_expired_temporary_subsection_payload(tuple(resolved_path), payload)
        if pruned_payload is not None:
            payload = pruned_payload
        expired_child_pruned_payload = _drop_expired_temporary_subsection_children(tuple(resolved_path), payload)
        if expired_child_pruned_payload is not None:
            payload = expired_child_pruned_payload
        carried_pruned_payload = _drop_absent_carried_snapshot_subsections(tuple(resolved_path), payload)
        if carried_pruned_payload is not None:
            payload = carried_pruned_payload
        sanitized_payload = _sanitize_section_subsection_payloads(tuple(resolved_path), payload)
        if sanitized_payload is not None:
            payload = sanitized_payload
        carried_target_pruned_payload = _drop_carried_target_subsection_text_from_siblings(tuple(resolved_path), payload)
        if carried_target_pruned_payload is not None:
            payload = carried_target_pruned_payload
        explicit_group_payload = _explicit_subsection_group_snapshot_payload(tuple(resolved_path), payload)
        if explicit_group_payload is not None:
            payload = explicit_group_payload
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
    payload, payload_from_muutos_ir = _prefer_live_fold_section_snapshot_for_descendant_scoped_group(
        state=state,
        resolved_path=tuple(resolved_path) if resolved_path is not None else None,
        payload=payload,
        payload_from_muutos_ir=payload_from_muutos_ir,
        group_rops=group_rops,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
        source_pathologies_out=source_pathologies_out,
        source_statute=op_source.statute_id,
    )
    if (
        action in {StructuralAction.REPLACE, StructuralAction.INSERT}
        and resolved_path is not None
        and target_unit_kind == "section"
        and payload is not None
        and payload.kind is IRNodeKind.SECTION
    ):
        carried_target_pruned_payload = _drop_carried_target_subsection_text_from_siblings(
            tuple(resolved_path),
            payload,
            include_content_prefix=False,
        )
        if carried_target_pruned_payload is not None:
            payload = carried_target_pruned_payload
    if (
        action in {StructuralAction.REPLACE, StructuralAction.INSERT}
        and resolved_path is not None
        and target_unit_kind == "section"
        and payload is not None
        and payload.kind is IRNodeKind.SECTION
    ):
        pruned_payload = _drop_shifted_expired_temporary_subsection_payload(tuple(resolved_path), payload)
        if pruned_payload is not None:
            payload = pruned_payload

    def _source_repealed_subsection_labels_for_snapshot() -> set[str]:
        return {
            _norm_num_token(_snapshot_subsection_target_label(rop))
            for rop in group_rops
            if rop.is_repeal_action
            and _snapshot_targets_subsection_only(rop)
            and _snapshot_subsection_target_label(rop)
        }

    def _renumber_destination_subsection_labels_for_snapshot() -> set[str]:
        return {
            _norm_num_token(destination_path[-1][1])
            for rop in group_rops
            if (destination_path := _resolved_destination_path_for_rop(rop))
            and destination_path[-1][0] == "subsection"
        }

    def _explicitly_repealed_subsection_labels_for_snapshot() -> set[str]:
        if _whole_section_insert_replaces_explicit_child_repeal():
            # A section-level insert can be the source's "in place of the
            # repealed section/subsection" replacement. In that shape a same
            # label in the new source payload is fresh text, not stale carried
            # text to prune or tombstone after insertion.
            return set()
        source_repealed = _source_repealed_subsection_labels_for_snapshot()
        renumber_destinations = _renumber_destination_subsection_labels_for_snapshot()
        return source_repealed - renumber_destinations

    def _shifted_repealed_subsection_labels_for_snapshot() -> set[str]:
        source_repealed = _source_repealed_subsection_labels_for_snapshot()
        if payload_from_muutos_ir:
            return set()
        explicitly_repealed = _explicitly_repealed_subsection_labels_for_snapshot()
        inserted_subsection_labels = sorted(
            {
                int(insert_label)
                for rop in group_rops
                if rop.is_insert_action
                and _snapshot_targets_subsection_only(rop)
                and (insert_label := _norm_num_token(_snapshot_subsection_target_label(rop))).isdigit()
            }
        )
        shifted_repealed: set[str] = set()
        for repealed_label in source_repealed:
            if not repealed_label.isdigit():
                continue
            repealed_num = int(repealed_label)
            shift = sum(1 for insert_num in inserted_subsection_labels if insert_num <= repealed_num)
            if shift:
                shifted_repealed.add(str(repealed_num + shift))
        return shifted_repealed - explicitly_repealed

    def _carried_repealed_subsection_labels_for_snapshot() -> set[str]:
        return (
            _explicitly_repealed_subsection_labels_for_snapshot()
            | _shifted_repealed_subsection_labels_for_snapshot()
        )

    def _payload_pruned_repealed_subsection_labels_for_snapshot() -> set[str]:
        pruned = set(_shifted_repealed_subsection_labels_for_snapshot())
        if payload_from_muutos_ir:
            pruned.update(_explicitly_repealed_subsection_labels_for_snapshot())
        return pruned

    def _drop_carried_repealed_subsections_from_snapshot_payload(
        section_payload: IRNode,
    ) -> IRNode | None:
        carried_repealed = _payload_pruned_repealed_subsection_labels_for_snapshot()
        if not carried_repealed:
            return None
        children: list[IRNode] = []
        changed = False
        for child in section_payload.children:
            if (
                child.kind is IRNodeKind.SUBSECTION
                and child.label
                and _norm_num_token(child.label) in carried_repealed
            ):
                changed = True
                continue
            children.append(child)
        if not changed:
            return None
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(children),
        )

    if (
        payload is not None
        and target_unit_kind == "section"
        and action != StructuralAction.REPEAL
        and payload.kind is IRNodeKind.SECTION
    ):
        carried_pruned_payload = _drop_carried_repealed_subsections_from_snapshot_payload(payload)
        if carried_pruned_payload is not None:
            payload = carried_pruned_payload

    def _restore_renumber_destination_subsections_in_snapshot_payload(
        section_payload: IRNode,
    ) -> IRNode | None:
        if resolved_path is None or section_payload.kind is not IRNodeKind.SECTION:
            return None
        section_path = tuple(resolved_path)
        latest = _latest_section_snapshot_payload(
            section_path=section_path,
            replay_history_ops=lo_ops_out,
        )
        source_section = (
            latest.payload
            if latest is not None and latest.payload is not None and latest.payload.kind is IRNodeKind.SECTION
            else _section_node_from_base_ir(base_ir, section_path)
        )
        if source_section is None or source_section.kind is not IRNodeKind.SECTION:
            return None
        source_by_label = {
            _norm_num_token(child.label): child
            for child in source_section.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
        }
        renumber_pairs: dict[str, str] = {}
        for rop in group_rops:
            if not rop.is_renumber_action or not rop.targets_subsection_only():
                continue
            source_label = _norm_num_token(str(rop.resolved_target_subsection_label or "").strip())
            destination_path = _resolved_destination_path_for_rop(rop)
            if (
                not source_label
                or not destination_path
                or destination_path[-1][0] != "subsection"
            ):
                continue
            destination_label = _norm_num_token(destination_path[-1][1])
            if destination_label:
                renumber_pairs[destination_label] = source_label
        if not renumber_pairs:
            return None
        children: list[IRNode] = []
        changed = False
        for child in section_payload.children:
            if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                children.append(child)
                continue
            destination_label = _norm_num_token(child.label)
            source_label = renumber_pairs.get(destination_label)
            if not source_label:
                children.append(child)
                continue
            child_is_empty_or_placeholder = (
                not irnode_to_text(child).strip()
                or child.attrs.get("lawvm_repeal_placeholder") == "1"
            )
            if not child_is_empty_or_placeholder:
                children.append(child)
                continue
            source_child = source_by_label.get(source_label)
            if source_child is None:
                children.append(child)
                continue
            children.append(_relabel_subsection_ir(source_child, destination_label))
            changed = True
        if not changed:
            return None
        return IRNode(
            kind=section_payload.kind,
            label=section_payload.label,
            text=section_payload.text,
            attrs=dict(section_payload.attrs),
            children=tuple(children),
        )

    if (
        payload is not None
        and target_unit_kind == "section"
        and action != StructuralAction.REPEAL
        and payload.kind is IRNodeKind.SECTION
    ):
        restored_payload = _restore_renumber_destination_subsections_in_snapshot_payload(payload)
        if restored_payload is not None:
            payload = restored_payload
    if (
        payload is not None
        and target_unit_kind == "section"
        and payload.kind is IRNodeKind.SECTION
    ):
        source_text = op_source.raw_text or ""
        if "muuttuvat kohdiksi" in source_text.lower():
            shifted_children: list[IRNode] = []
            shifted = False
            for child in payload.children:
                if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                    shifted_children.append(child)
                    continue
                next_child = child
                child_label = _norm_num_token(child.label)
                for clause in parse_item_shift_clauses(source_text):
                    if _norm_num_token(clause.target_section) != normalized_target_norm:
                        continue
                    if str(clause.target_paragraph) != child_label:
                        continue
                    if not clause.target_items:
                        continue
                    next_child = _shift_lettered_item_labels_after_repeal(
                        next_child,
                        clause.target_items[0],
                    )
                shifted = shifted or next_child is not child
                shifted_children.append(next_child)
            if shifted:
                payload = IRNode(
                    kind=payload.kind,
                    label=payload.label,
                    text=payload.text,
                    attrs=dict(payload.attrs),
                    children=tuple(shifted_children),
                )
    if (
        action is StructuralAction.REPLACE
        and payload is not None
        and target_unit_kind in {"chapter", "part"}
        and _complete_whole_container_source_child_labels() is not None
    ):
        payload = _stamp_complete_snapshot_owner(payload)
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
    ):
        old_raw_path = (
            _tops.find(
                base_ir,
                "section",
                normalized_target_norm,
                scope_kind="chapter",
                scope_label=moved_from_chapter,
            )
            if base_ir is not None
            else None
        )
        old_path = _timeline_path(_tops._as_path(old_raw_path)) if old_raw_path else None
        if old_path is None:
            # The relocated section was introduced into the source chapter by an
            # intermediate amendment, so it is absent from the original base
            # tree. Its live history is still keyed at the source chapter
            # address, which the explicit insert at the new chapter does not
            # tombstone. Build the source-chapter address directly so the move
            # still leaves a tombstone instead of an orphan copy.
            old_path = (("chapter", moved_from_chapter), ("section", normalized_target_norm))
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
        and not (
            payload_from_muutos_ir
            and _payload_has_heading_body_subsection_split(payload)
        )
    ):
        section_path = tuple(resolved_path)
        carried_repealed_subsection_labels = _carried_repealed_subsection_labels_for_snapshot()
        explicitly_repealed_paragraph_labels_by_subsection: dict[str, set[str]] = {}
        explicitly_targeted_paragraph_labels_by_subsection = (
            _explicitly_targeted_paragraph_labels_by_subsection()
        )
        source_text_lower = str(op_source.raw_text or "").lower()
        has_post_repeal_item_shift = "muuttuvat kohdiksi" in source_text_lower
        for rop in group_rops:
            subsection_label = str(rop.resolved_target_subsection_label or "").strip()
            item_label = str(rop.resolved_target_item_label or "").strip()
            if not rop.is_repeal_action:
                continue
            if has_post_repeal_item_shift:
                continue
            if not subsection_label or not item_label:
                continue
            explicitly_repealed_paragraph_labels_by_subsection.setdefault(
                _norm_num_token(subsection_label),
                set(),
            ).add(_normalize_snapshot_item_label(item_label))
        payload_subsection_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
            and _norm_num_token(child.label) not in carried_repealed_subsection_labels
        }

        def _prior_paragraph_labels_for_subsection(child_path: Path) -> set[str]:
            labels: set[str] = set()
            base_child = _subsection_node_from_base_ir(base_ir, child_path)
            if base_child is not None:
                labels.update(
                    _normalize_snapshot_item_label(grandchild.label)
                    for grandchild in base_child.children
                    if grandchild.kind is IRNodeKind.PARAGRAPH and grandchild.label
                )
            for prior in lo_ops_out:
                if prior.target.special is not None or not prior.target.path:
                    continue
                if prior.source is not None:
                    prior_effective = prior.source.effective or prior.source.enacted or ""
                    if op_source.effective and prior_effective and prior_effective > op_source.effective:
                        continue
                if prior.target.path == child_path and prior.payload is not None:
                    if prior.payload.kind is IRNodeKind.SUBSECTION:
                        labels.update(
                            _normalize_snapshot_item_label(grandchild.label)
                            for grandchild in prior.payload.children
                            if grandchild.kind is IRNodeKind.PARAGRAPH and grandchild.label
                        )
                    continue
                if (
                    len(prior.target.path) == len(child_path) + 1
                    and prior.target.path[:-1] == child_path
                    and prior.target.path[-1][0] == "paragraph"
                ):
                    paragraph_label = _normalize_snapshot_item_label(prior.target.path[-1][1])
                    if not paragraph_label:
                        continue
                    if prior.action is StructuralAction.REPEAL:
                        labels.discard(paragraph_label)
                    else:
                        labels.add(paragraph_label)
            return labels

        for child in payload.children:
            if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                continue
            child_norm_label = _norm_num_token(child.label)
            if child_norm_label in carried_repealed_subsection_labels:
                continue
            child_path = section_path + (("subsection", child.label),)
            assert child.label is not None
            child_payload = _drop_expired_temporary_paragraph_children(
                child_path,
                child,
                preserve_paragraph_labels=explicitly_targeted_paragraph_labels_by_subsection.get(
                    child_norm_label,
                    set(),
                ),
            ) or child
            child_payload = _inherit_parent_snapshot_ownership_attrs(child_payload, payload)
            child_source = op_source
            for rop in group_rops:
                rop_subsection = _norm_num_token(_snapshot_subsection_target_label(rop))
                if rop_subsection != child_norm_label:
                    continue
                rop_source = rop.resolved_op_source
                if rop_source is not None:
                    child_source = rop_source
                    break
            child_base_exists = _timeline_target_exists_for_snapshot(
                child_path,
                replay_history_ops=[],
                base_ir=base_ir,
            )
            child_replay_exists = _timeline_target_exists_for_snapshot(
                child_path,
                replay_history_ops=lo_ops_out,
                base_ir=base_ir,
                before_effective=op_source.effective,
            ) or child_norm_label in _renumber_destination_subsection_labels_for_snapshot()
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
                    payload=child_payload,
                    source=child_source,
                    group_id=f"finland-johto:{amendment_id or 'unknown'}",
                )
            )
            if _snapshot_payload_is_complete_owner(child_payload):
                payload_paragraphs = [
                    grandchild
                    for grandchild in child_payload.children
                    if grandchild.kind is IRNodeKind.PARAGRAPH and grandchild.label
                ]
                payload_paragraph_labels = {
                    _normalize_snapshot_item_label(grandchild.label)
                    for grandchild in payload_paragraphs
                }
                explicitly_repealed_paragraph_labels = (
                    explicitly_repealed_paragraph_labels_by_subsection.get(
                        child_norm_label,
                        set(),
                    )
                )
                payload_paragraph_labels -= explicitly_repealed_paragraph_labels
                if payload_paragraphs:
                    prior_paragraph_labels = _prior_paragraph_labels_for_subsection(child_path)
                    for paragraph in payload_paragraphs:
                        assert paragraph.label is not None
                        paragraph_label = _normalize_snapshot_item_label(paragraph.label)
                        if paragraph_label in explicitly_repealed_paragraph_labels:
                            continue
                        lo_ops_out.append(
                            _LegalOperation(
                                op_id=(
                                    "snapshot_paragraph_"
                                    f"{paragraph_label}_from_subsection_{child.label}_section_{target_norm}"
                                ),
                                sequence=0,
                                action=(
                                    StructuralAction.REPLACE
                                    if paragraph_label in prior_paragraph_labels
                                    else StructuralAction.INSERT
                                ),
                                target=LegalAddress(
                                    path=child_path + (("paragraph", paragraph.label),)
                                ),
                                payload=paragraph,
                                source=op_source,
                                group_id=f"finland-johto:{amendment_id or 'unknown'}",
                            )
                        )
                    for paragraph_label in sorted(
                        prior_paragraph_labels - payload_paragraph_labels,
                        key=default_label_sort_key,
                    ):
                        lo_ops_out.append(
                            _LegalOperation(
                                op_id=(
                                    "snapshot_repeal_paragraph_"
                                    f"{paragraph_label}_from_subsection_{child.label}_section_{target_norm}"
                                ),
                                sequence=0,
                                action=StructuralAction.REPEAL,
                                target=LegalAddress(
                                    path=child_path + (("paragraph", paragraph_label),)
                                ),
                                payload=None,
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
            if not _timeline_target_exists_for_snapshot(
                child_path,
                replay_history_ops=lo_ops_out,
                base_ir=base_ir,
                before_effective=op_source.effective,
            ):
                continue
            if target_label not in missing_repealed_subsections:
                missing_repealed_subsections.append(target_label)
        for child_norm, child_label in complete_section_replacement_missing_subsections.items():
            if child_norm in payload_subsection_labels or child_norm in carried_repealed_subsection_labels:
                continue
            if child_label not in missing_repealed_subsections:
                missing_repealed_subsections.append(child_label)
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
        direct_child_kind = _container_direct_child_kind()
        if direct_child_kind is None:
            return
        payload_child_labels = {
            _norm_num_token(child.label)
            for child in payload.children
            if child.kind is direct_child_kind and child.label
        }
        # A whole-container REPLACE whose source payload owns its full child
        # surface makes the replacement container's direct-child set
        # authoritative: children present in the post-apply live tree but absent
        # from the source payload are stale orphans that an earlier merge-style
        # apply failed to drop, and must NOT be snapshotted forward.
        authoritative_child_labels = _complete_whole_container_source_child_labels()
        # Decide up-front which post-apply direct children are stale orphans the
        # whole-container REPLACE retires, so the snapshot-emission loop below
        # skips exactly those.
        orphan_child_labels_to_drop = _container_replace_orphan_child_labels(
            authoritative_child_labels=authoritative_child_labels,
            payload=payload,
            container_path=tuple(resolved_path),
            base_container_payload=base_container_payload,
            action=action,
        )
        for child in payload.children:
            if child.kind is IRNodeKind.SECTION and child.label:
                if _norm_num_token(child.label) in orphan_child_labels_to_drop:
                    continue
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
                            and normalized_label_key(existing_chapter) != normalized_label_key(target_container_chapter)
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
            # PART-level missing-child repeal requires an authoritative child-label
            # set (a genuine single complete part REPLACE). Without one, the
            # payload_child_labels fallback below misclassifies live chapters that
            # Finnish part payloads wrap in crossHeading/heading nodes as "missing"
            # and spuriously repeals them; the resulting content=None chapter
            # snapshot then masks its own same-wave child sections (regression:
            # 1929/234 part_5 repealed live chapters 1/2, dropping sections
            # 110-113). The chapter-target path keeps its payload fallback (no
            # wrapper interposition) and is additionally sparse-guarded below.
            if target_unit_kind == "part" and authoritative_child_labels is None:
                return
            prior_child_paths = _container_replace_prior_child_paths(
                container_path=container_path,
                base_container_payload=base_container_payload,
                replay_history_ops=lo_ops_out,
                child_kind=direct_child_kind,
            )
            # When the source payload is a complete whole-container replacement,
            # its direct-child labels — not the (possibly merge-polluted)
            # post-apply live tree — define which children the new container
            # contains. Drop logic below must repeal prior children absent from
            # that authoritative set.
            effective_child_labels = (
                authoritative_child_labels
                if authoritative_child_labels is not None
                else payload_child_labels
            )
            sparse_fragmentary_container_replace = target_unit_kind == "chapter" and _whole_chapter_replace_is_sparse(
                effective_labels=set(effective_child_labels) if effective_child_labels else set(),
                payload=payload,
                container_path=container_path,
                base_container_payload=base_container_payload,
            )
            if sparse_fragmentary_container_replace:
                # Genuinely sparse/fragmentary chapter amendment: the payload merged
                # into the live chapter, so prior sections absent from the (often
                # misclassified-small) authoritative set are preserved, not retired.
                # Sparseness is now measured against prior-only siblings, so a
                # complete whole-chapter REPLACE whose authoritative section set is
                # smaller than a merge-polluted live tree (chapter ranges,
                # re-heading combos) is NOT treated as sparse and reaches the repeal
                # loop below instead.
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=op_source.statute_id,
                            target_unit_kind=target_unit_kind,
                            target_label=target_norm,
                            recovery_kind=RecoveryKind.CONTAINER_SNAPSHOT_SPARSE_MISSING_CHILD_REPEAL_SKIP,
                            live_sibling_count=len(prior_child_paths),
                            payload_sibling_count=len(effective_child_labels),
                        )
                    )
                return
            for child_norm, child_path in prior_child_paths.items():
                if child_norm in effective_child_labels:
                    continue
                lo_ops_out.append(
                    _LegalOperation(
                        op_id=(
                            f"snapshot_repeal_missing_{direct_child_kind.value}_"
                            f"{child_norm}_from_{target_unit_kind}_{target_norm}"
                        ),
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
        if not chapters or normalized_label_key(chapters[-1][1]) != normalized_label_key(target_chapter):
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


def _with_replaced_provision_subtree_index(
    state: "ReplayState",
    new_ir: IRNode,
    *,
    path: Path,
    old_subtree: IRNode,
    new_subtree: IRNode,
) -> "ReplayState":
    """Reuse the provision-path index after same-path subtree replacement."""
    return state.with_replaced_provision_subtree_index(
        new_ir,
        path=path,
        old_subtree=old_subtree,
        new_subtree=new_subtree,
    )


def _same_norm_label(lhs: Optional[str], rhs: Optional[str]) -> bool:
    return bool(lhs) and bool(rhs) and normalized_label_key(lhs) == normalized_label_key(rhs)


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


def _resolve_parallel_container_path(
    live_ir: IRNode,
    base_ir: IRNode,
    container_path: Path,
) -> Path | None:
    """Resolve a base-XML container path against the live replay tree."""
    if not container_path:
        return ()

    current_live = live_ir
    current_path: Path = ()
    for kind, label in container_path:
        matched: IRNode | None = None
        for child in current_live.children:
            child_kind = _kind_str(child.kind)
            if child_kind != kind:
                continue
            if not label or _same_norm_label(child.label or "", label):
                matched = child
                break
        if matched is None and kind == "hcontainer":
            for child in current_live.children:
                if child.kind is IRNodeKind.HCONTAINER:
                    matched = child
                    break
        if matched is None:
            return None
        current_path = current_path + ((_kind_str(matched.kind), matched.label or ""),)
        current_live = matched
    return _tops._as_path(current_path)


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

    ``part_hint``, when provided, is the source-local target part label as
    recorded in the amendment body (e.g. "IV A OSA" / "iva" / "4a").  It is
    normalized to the canonical Arabic-plus-suffix address label before it
    overrides the positional heuristic, so letter-suffix chapters that cross a
    part boundary route to the correct part.
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
        part_hint = _norm_num_token(part_hint).removesuffix("osasto").removesuffix("osa") or part_hint
        for part in parts:
            if part.label == part_hint:
                return provisions_parent_path + (("part", part_hint),)

    fam_path = _tops.find_family(master_ir, "chapter", chapter_label)
    if fam_path is not None and len(fam_path) >= 2:
        return _tops._as_path(fam_path[:-1])

    new_key = default_label_sort_key(chapter_label)
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
            ch_key = default_label_sort_key(ch.label)
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
