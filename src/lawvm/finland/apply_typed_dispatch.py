"""Typed dispatch layer for Finland apply.

This module owns the CanonicalIntent-driven section/container dispatch and the
typed action routing helpers. Lane selection lives in
:mod:`lawvm.finland.apply_intent_facade`; ``apply.py`` keeps the public
``apply_op`` compatibility entrypoint.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, List, Optional, cast

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.recovery_kind import RecoveryKind, coerce_recovery_kind
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.mutation_boundary import (
    diff_ir_paths_identity_pruned,
    path_has_prefix,
    path_is_strict_prefix,
)
from lawvm.core.observed_write_audit import ObservedWriteAudit, build_observed_write_audit
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path, default_label_sort_key, normalized_label_key
from lawvm.finland.ops import (
    FailedOp,
    ReplayProfile,
    ResolvedOp,
    SectionPathResolutionReason,
    _assert_intent_compat,
)
from lawvm.finland.standalone_targets import StandaloneSectionTargetsInput
from lawvm.finland.apply_policy import (
    _check_occupancy_policy,
    _resolve_section_path_with_fallbacks,
    section_resolver_binding,
    same_wave_migration_follow_is_allowed,
)
from lawvm.finland.scoped_section_resolver import (
    find_scoped_section_insert_parent_path as _find_shared_scoped_section_insert_parent_path,
    section_paths_for_label,
    unique_root_or_only_section_path,
    unique_same_part_different_chapter_section_path,
)
from lawvm.finland.apply_structure_ops import (
    _structure_apply_view_for_op,
    _apply_container_op,
    _apply_whole_section_op,
    _apply_materialization,
)
from lawvm.finland.apply_subsection_dispatch import (
    _apply_deterministic_subsection_op,
    classify_subsection_dispatch_failure,
    _normalize_subsection_dispatch_inputs,
)
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    DeclaredMutationAllowance,
    TreePath,
    TreePaths,
    _emit_apply_mutation_event_for_rop,
    _emit_apply_mutation_event_from_receipt,
    _path_to_tuple,
    _resolved_target_path_for_rop_event,
    _target_address_path_for_rop_event,
    landed_section_event_path,
)


# ---------------------------------------------------------------------------
# §1.8 (replay conservation) APPLY.OP_SKIPPED_WITNESSED emission helper
# ---------------------------------------------------------------------------
# An applyResolvedOp path that emits ``outcome="skipped"`` to its
# mutation_events_out ledger without a typed Finding OR FailedOp consumes the
# op silently — the audit mutation_event carries the failure_reason but the
# apply disposition stays APPLIED. This helper is called at each of the 13
# ``outcome="skipped"`` sites in apply_typed_dispatch.py so the findings ledger
# ALSO carries a non-blocking observation with rule_id=reason_code naming the
# specific skip site (source_not_found, destination_exists,
# container_op_returned_none, source_address_empty, source_resolved_none,
# destination_parent_not_found, source_container_missing,
# resolver_binding_contract_error, unhandled_insert_target /
# unhandled_repeal_target / unhandled_replace_target,
# idempotent_repeal_parent_section_absent, ...). Non-blocking so quirks mode
# continues; the existing FailedOp path for typed-target skips is preserved.
_APPLY_OP_SKIPPED_WITNESSED_KIND = "APPLY.OP_SKIPPED_WITNESSED"


def _emit_apply_op_skipped_witness(
    findings_out: Optional[List[Finding]],
    *,
    rop: ResolvedOp,
    reason_code: str,
    failure_reason: str,
    clause_text: str = "",
) -> None:
    """Witness one ``outcome="skipped"`` applyResolvedOp path (§1.8).

    Append a :class:`Finding` with kind ``APPLY.OP_SKIPPED_WITNESSED`` carrying
    ``rule_id`` = ``reason_code`` (distinguishes the 13 known skip sites),
    ``failure_reason`` prose, ``op_id``, ``clause_text``, and the rop's
    source_statute. Non-blocking so quirks mode continues (audit total). The
    caller may always pass ``findings_out=None`` (no sink wired); in that case
    the witness is silently not emitted, mirroring how other optional
    findings_out sinks behave when not plumbed at the call site.
    """
    if findings_out is None:
        return
    findings_out.append(
        Finding(
            kind=_APPLY_OP_SKIPPED_WITNESSED_KIND,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=rop.resolved_source_statute or "",
            detail={
                "message": (
                    f"applyResolvedOp outcome='skipped' at reason_code="
                    f"{reason_code!r}. The mutation-events-out ledger carries "
                    f"the skip (failure_reason={failure_reason!r}); this "
                    f"finding is the §1.8 witness so the findings-ledger audit "
                    f"is total — the disposition tracking applies but the op "
                    f"did not produce a write."
                ),
                "rule_id": reason_code,
                "reason_code": reason_code,
                "failure_reason": failure_reason,
                "op_id": rop.op_id or "",
                "clause_text": (clause_text or failure_reason)[:400],
            },
        )
    )
from lawvm.finland.apply_ir_ops import (
    _rebuild_section_with_subsections_ir,
    _rebuild_subsection_with_items_ir,
    _relabel_chapter_ir,
    _relabel_item_ir,
    _relabel_section_ir,
    _relabel_subsection_ir,
)
from lawvm.finland.apply_runtime_support import _find_insert_parent_path, _with_preserved_provision_index
from lawvm.finland.migration_ledger import MigrationLedger, migration_lower_bound_for_op
from lawvm.finland.replay_notices import replay_print

if TYPE_CHECKING:  # pragma: no cover
    from lawvm.core.canonical_intent import CanonicalIntent, Insert, Repeal, Relabel, Replace
    from lawvm.core.canonical_intent import Move
    from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)


_MOVE_SKIP_REASON_CODES = {
    "source_address_empty": "source_address_empty",
    "source_not_found": "source_not_found",
    "source_resolved_none": "source_resolved_none",
    "destination_parent_not_found": "destination_parent_not_found",
    "destination_exists": "destination_exists",
}

_SECTION_RELABEL_MIGRATION_RULE_ID = "section_relabel_renumber"
_SUBSECTION_RELABEL_MIGRATION_RULE_ID = "subsection_relabel_renumber"
_ITEM_RELABEL_MIGRATION_RULE_ID = "item_relabel_renumber"
_CHAPTER_RELABEL_MIGRATION_RULE_ID = "chapter_relabel_renumber"
_PART_RELABEL_MIGRATION_RULE_ID = "part_relabel_renumber"
_MOVE_REPARENT_MIGRATION_RULE_ID = "move_reparent"
_INTRO_LIST_MOMENT_SHAPE_RULE_ID = RecoveryKind.INTRO_LIST_MOMENT_SHAPE
_MISSING_EXACT_SUBSECTION_LABEL_RULE_ID = RecoveryKind.MISSING_EXACT_SUBSECTION_LABEL
_SPARSE_ALAKOHTA_INSERT_MERGE_RULE_ID = RecoveryKind.SPARSE_ALAKOHTA_INSERT_MERGE
_SPARSE_ALAKOHTA_REPLACE_MERGE_RULE_ID = RecoveryKind.SPARSE_ALAKOHTA_REPLACE_MERGE
_SPARSE_ITEM_TAIL_SUBSECTION_PRUNE_RULE_ID = RecoveryKind.SPARSE_ITEM_TAIL_SUBSECTION_PRUNE
_SUBSECTION_REPLACE_SPARSE_GAP_INSERT_RULE_ID = RecoveryKind.SUBSECTION_REPLACE_SPARSE_GAP_INSERT
_SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE_RULE_ID = (
    RecoveryKind.SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE
)
_SUBSECTION_DISPATCH_LANDED_RECOVERY_RULE_IDS: tuple[RecoveryKind, ...] = (
    _INTRO_LIST_MOMENT_SHAPE_RULE_ID,
    _MISSING_EXACT_SUBSECTION_LABEL_RULE_ID,
    _SPARSE_ALAKOHTA_REPLACE_MERGE_RULE_ID,
    _SPARSE_ALAKOHTA_INSERT_MERGE_RULE_ID,
    _SPARSE_ITEM_TAIL_SUBSECTION_PRUNE_RULE_ID,
    _SUBSECTION_REPLACE_SPARSE_GAP_INSERT_RULE_ID,
)


def _address_leaf_kind(address) -> str:
    return address.leaf_kind() if address is not None else ""


def _is_container_facet_replace(intent: "Replace") -> bool:
    from lawvm.core.canonical_intent import FacetTarget

    return isinstance(intent.target, FacetTarget) and intent.target.facet in {FacetKind.HEADING, FacetKind.INTRO} and (
        _address_leaf_kind(intent.target.host) in {"chapter", "part"}
    )


def _relabel_source_unit_kind(intent: "Relabel") -> str:
    return _address_leaf_kind(intent.source.address)


def _required_tree_path(path: Path) -> TreePath:
    tree_path = _path_to_tuple(path)
    if tree_path is None:
        raise ValueError("non-empty apply path unexpectedly converted to None")
    return tree_path


def _build_relabel_write_receipt(
    *,
    rop: ResolvedOp,
    src_path: Path,
    landed_path: Path,
    source_node: IRNode,
    landed_node: IRNode,
    migration_rule_id: str,
) -> WriteReceipt:
    """Record the landed write for a relabel/renumber operation.

    The source path is the resolver-bound old address; the landed path is where
    the renamed node exists after the write. Their divergence is the legal
    migration itself and must be named on the receipt.
    """
    source_tree_path = _required_tree_path(src_path)
    landed_tree_path = _required_tree_path(landed_path)
    source_addr = receipt_address_string(source_tree_path)
    landed_addr = receipt_address_string(landed_tree_path)
    return WriteReceipt(
        op_id=rop.op_id or "",
        helper="_apply_intent_relabel",
        action=rop.resolved_action_type.lower(),
        bound_target_path=source_tree_path,
        landed_primary_path=landed_tree_path,
        renumbered_paths=((source_tree_path, landed_tree_path),),
        migration_rule_ids=(migration_rule_id,),
        pre_hashes={
            source_addr: structural_subtree_hash(source_node),
            landed_addr: "",
        },
        post_hashes={
            source_addr: "",
            landed_addr: structural_subtree_hash(landed_node),
        },
    )


def _append_observed_write_audit(
    write_audits_out: Optional[List[ObservedWriteAudit]],
    *,
    before_ir: IRNode,
    after_ir: IRNode,
    receipt: WriteReceipt,
) -> None:
    if write_audits_out is None:
        return
    write_audits_out.append(build_observed_write_audit(before_ir, after_ir, receipt))


def _container_relabel_migration_rule_id(kind: str) -> str:
    if kind == "chapter":
        return _CHAPTER_RELABEL_MIGRATION_RULE_ID
    if kind == "part":
        return _PART_RELABEL_MIGRATION_RULE_ID
    raise ValueError(f"unsupported container relabel kind: {kind!r}")


def _materialization_root_move_allowances(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[DeclaredMutationAllowance, ...]:
    root_move_paths = _materialization_root_move_paths(state, rop, muutos_ir, sec_path)
    if not root_move_paths:
        return ()
    return (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=root_move_paths,
            rule_id="section_materialization_root_move_destination_rebind",
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=root_move_paths,
            rule_id="section_materialization_root_move_destination_rebind",
        ),
    )


def _materialization_root_move_paths(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> TreePaths:
    if sec_path is not None or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return ()
    target_chapter = rop.resolved_target_scope_chapter_label
    target_norm = str(rop.resolved_target_label or "").strip()
    payload_label = str(muutos_ir.label or "").strip()
    if not target_chapter or not target_norm or not payload_label:
        return ()
    if normalized_label_key(payload_label) != normalized_label_key(target_norm):
        return ()
    matches = state.provision_index.get(("section", normalized_label_key(target_norm)), [])
    root_matches = [
        _tops._as_path(path)
        for path in matches
        if not any(kind == "chapter" for kind, _label in _tops._as_path(path))
    ]
    if len(root_matches) != 1:
        return ()
    return (root_matches[0],)


def _whole_section_move_rebind_paths(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> TreePaths:
    if sec_path is not None or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return ()
    target_chapter = rop.resolved_target_scope_chapter_label
    target_norm = str(rop.resolved_target_label or "").strip()
    payload_label = str(muutos_ir.label or "").strip()
    if not target_chapter or not target_norm or not payload_label:
        return ()
    if normalized_label_key(payload_label) != normalized_label_key(target_norm):
        return ()
    matches = section_paths_for_label(state.provision_index, target_norm)
    existing_path = unique_root_or_only_section_path(matches)
    if existing_path is None:
        # Same-labeled sections in several parts make the unscoped lookup
        # ambiguous, but the op's part scope can still single out the move
        # source: the only same-labeled section in the target part that sits
        # under a different chapter is the node this write vacates. Mirrors
        # the part-scoped disambiguation in
        # _find_scoped_section_insert_parent_path.
        existing_path = unique_same_part_different_chapter_section_path(
            matches,
            target_part=str(rop.resolved_target_scope_part_label or "").strip() or None,
            target_chapter=target_chapter,
        )
    if existing_path is None:
        return ()
    existing_chapter = next((label for kind, label in existing_path if kind == "chapter"), None)
    if rop.resolved_action_type == "REPLACE":
        if not existing_chapter or existing_chapter != target_chapter:
            return (existing_path,)
        return ()
    if rop.resolved_action_type == "INSERT":
        if not existing_chapter:
            return (existing_path,)
        existing_node = _tops.resolve(state.ir, existing_path)
        is_placeholder = existing_node is not None and existing_node.attrs.get("lawvm_repeal_placeholder") == "1"
        if is_placeholder:
            return (existing_path,)
        if re.fullmatch(rf"{re.escape(existing_chapter)}[a-z]+", target_chapter, re.I) is not None:
            return (existing_path,)
    return ()


def _whole_section_move_rebind_allowances(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[DeclaredMutationAllowance, ...]:
    rebind_paths = _whole_section_move_rebind_paths(state, rop, muutos_ir, sec_path)
    if not rebind_paths:
        return ()
    rule_id = ""
    if rop.resolved_action_type == "REPLACE":
        rule_id = "section_move_replace_destination_rebind"
    elif rop.resolved_action_type == "INSERT":
        rule_id = "section_move_insert_destination_rebind"
    if not rule_id:
        return ()
    return (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=rebind_paths,
            rule_id=rule_id,
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=rebind_paths,
            rule_id=rule_id,
        ),
    )


def _intent_targets_section(intent: "CanonicalIntent") -> bool:
    from lawvm.core.canonical_intent import FacetTarget, Insert, NodeTarget, Relabel, Repeal, Replace

    match intent:
        case Replace(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Replace(target=FacetTarget(host=host)) if _address_leaf_kind(host) == "section":
            return True
        case Insert(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Repeal(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Relabel(source=source) if _address_leaf_kind(source.address) == "section":
            return True
        case _:
            return False


def _parent_path(path: TreePath | None) -> TreePath | None:
    if path is None or not path:
        return None
    return path[:-1]


def _section_heading_node(sec_node: Optional[IRNode]) -> Optional[IRNode]:
    if sec_node is None:
        return None
    return next((c for c in sec_node.children if c.kind is IRNodeKind.HEADING), None)


def _section_heading_touch_paths(
    before_sec: Optional[IRNode],
    after_state: "ReplayState",
    sec_path: Path,
) -> tuple[TreePaths, TreePaths]:
    """Return (created, replaced) heading paths the subsection dispatch touched.

    The subsection handlers rewrite a section's ``heading`` sibling as a
    side-effect when the amendment payload carries a differing heading. The
    diff observes that change at ``<sec_path>/heading:`` but the op's declared
    paths only cover the subsection/item target. Compare the section's heading
    node before and after dispatch and declare what actually moved: ``created``
    when no heading existed before, ``replaced`` when its text/attrs changed.
    """
    after_sec = _tops.resolve(after_state.ir, sec_path)
    before_heading = _section_heading_node(before_sec)
    after_heading = _section_heading_node(after_sec)
    if after_heading is None:
        return ((), ())
    base_path = _path_to_tuple(sec_path)
    if base_path is None:
        return ((), ())
    if before_heading is None:
        heading_path = base_path + (("heading", after_heading.label or ""),)
        return ((heading_path,), ())
    if (
        before_heading.text == after_heading.text
        and dict(before_heading.attrs) == dict(after_heading.attrs)
        and (before_heading.label or "") == (after_heading.label or "")
    ):
        return ((), ())
    heading_path = base_path + (("heading", after_heading.label or ""),)
    return ((), (heading_path,))


def _section_subtree_landed_touch_paths(
    before_sec: Optional[IRNode],
    after_state: "ReplayState",
    sec_path: Path,
) -> TreePaths:
    """Return the section-subtree paths the subsection dispatch actually changed.

    The op's nominal resolved address counts list items as ``subsection:N/item:M``,
    but the elaboration rails (sparse-slot rebase, single-subsection item
    fallback) legitimately land the write at a different live subsection index:
    intro subsections, single-subsection paragraph lists, and leading OMISSION
    nodes all shift the subsection axis relative to the nominal count. A
    declaration built from the nominal address alone therefore misses the landed
    node. Diff the section subtree before vs after dispatch and declare the
    descendant paths that actually changed — the same before/after treatment as
    ``_section_heading_touch_paths``, generalised to all section children.

    Scoped on purpose: only paths inside the resolved section are declared, so
    the observed-vs-declared cross-check stays live for any touch outside the
    section the op resolved to. The section root itself is excluded — its
    child-shape change is already explained by the declared children via the
    container-ancestor rule, and declaring it would widen the declared region
    to the whole section.
    """
    after_sec = _tops.resolve(after_state.ir, sec_path)
    if before_sec is None or after_sec is None:
        return ()
    base_path = _path_to_tuple(sec_path)
    if base_path is None:
        return ()
    return tuple(
        base_path + rel_path
        for rel_path in diff_ir_paths_identity_pruned(before_sec, after_sec)
        if rel_path
    )


def _path_explained_by_effect_roots(path: TreePath, roots: TreePaths) -> bool:
    """Mirror mutation-accounting target coverage for local allowance pruning."""
    return path_has_prefix(path, roots) or any(path_is_strict_prefix(path, root) for root in roots)


def _new_pathologies_include_recovery_kind(
    pathologies: tuple[SourcePathology, ...],
    recovery_kind: RecoveryKind,
) -> bool:
    for pathology in pathologies:
        raw = pathology.detail.get("recovery_kind") or pathology.detail.get("rebound_kind")
        if raw is None or raw == "":
            continue
        # Fail loud (UnregisteredRecoveryKind) rather than silently no-matching a
        # typoed/unregistered kind: the allowance authorization keys on this value.
        if coerce_recovery_kind(raw) == recovery_kind:
            return True
    return False


def _sparse_item_tail_prune_recovery_paths(
    *,
    new_pathologies: tuple[SourcePathology, ...],
    landed_paths: TreePaths,
    resolved_target_path: TreePath | None,
    parent_path: TreePath | None,
) -> TreePaths:
    """Return non-target paths owned by sparse item tail-subsection pruning.

    The item merge helper emits a source pathology when it removes the adjacent
    duplicate tail subsection. Mutation events must still keep the nominal item
    target narrow; this helper only declares the concrete landed paths that are
    outside the target/parent effect region and only when that exact recovery
    fired during the dispatch call.
    """
    if not landed_paths or not _new_pathologies_include_recovery_kind(
        new_pathologies,
        _SPARSE_ITEM_TAIL_SUBSECTION_PRUNE_RULE_ID,
    ):
        return ()
    effect_roots: TreePaths = tuple(path for path in (resolved_target_path, parent_path) if path)
    return tuple(path for path in landed_paths if not _path_explained_by_effect_roots(path, effect_roots))


def _subsection_dispatch_landed_recovery_allowances(
    *,
    new_pathologies: tuple[SourcePathology, ...],
    landed_paths: TreePaths,
    resolved_target_path: TreePath | None,
    parent_path: TreePath | None,
) -> tuple[DeclaredMutationAllowance, ...]:
    """Declare known sparse-item recoveries that legitimately landed off-axis."""
    if not landed_paths:
        return ()
    effect_roots: TreePaths = tuple(path for path in (resolved_target_path, parent_path) if path)
    recovery_paths = tuple(path for path in landed_paths if not _path_explained_by_effect_roots(path, effect_roots))
    if not recovery_paths:
        return ()
    return tuple(
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=recovery_paths,
            rule_id=rule_id,
        )
        for rule_id in _SUBSECTION_DISPATCH_LANDED_RECOVERY_RULE_IDS
        if _new_pathologies_include_recovery_kind(new_pathologies, rule_id)
    )


def _whole_section_unscoped_duplicate_consumed_paths(
    *,
    before_state: "ReplayState",
    after_state: "ReplayState",
    new_pathologies: tuple[SourcePathology, ...],
    rop: ResolvedOp,
    sec_path: Path | None,
) -> TreePaths:
    if sec_path is None:
        return ()
    if not _new_pathologies_include_recovery_kind(
        new_pathologies,
        _SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE_RULE_ID,
    ):
        return ()
    target_label = rop.resolved_target_label or (str(sec_path[-1][1]) if sec_path else "")
    if not target_label:
        return ()
    consumed: list[TreePath] = []
    for candidate in section_paths_for_label(before_state.provision_index, target_label):
        path = tuple(candidate)
        if path == tuple(sec_path):
            continue
        if any(kind in {"chapter", "part"} for kind, _value in path[:-1]):
            continue
        if _tops.resolve(before_state.ir, path) is None:
            continue
        if _tops.resolve(after_state.ir, path) is not None:
            continue
        consumed_path = _path_to_tuple(path)
        if consumed_path is not None:
            consumed.append(consumed_path)
    return tuple(consumed)


def _find_scoped_section_insert_parent_path(
    ir: IRNode,
    *,
    chapter_label: str | None,
    part_label: str | None,
) -> TreePath:
    """Resolve a section parent path without dropping part scope.

    Bare chapter-label lookup is unsafe when multiple parts contain the same
    chapter label, as in `2017/320 <- 2019/371`. Prefer the explicitly scoped
    part/chapter parent when available.
    """
    parent_path = _find_shared_scoped_section_insert_parent_path(
        ir,
        chapter_label=chapter_label,
        part_label=part_label,
        find_part_path=lambda label: _tops.find(ir, "part", label),
        find_insert_parent_path=lambda chapter: _find_insert_parent_path(ir, chapter),
        missing_part_policy="fallback",
        missing_chapter_in_part_policy="part",
    )
    assert parent_path is not None
    return parent_path


def _post_apply_section_path(result_state: "ReplayState", rop: ResolvedOp) -> TreePath | None:
    """Resolve the rop's target section in the post-apply tree, for declaration."""
    return landed_section_event_path(
        result_state,
        section_label=rop.resolved_target_label,
        chapter_label=rop.resolved_target_scope_chapter_label,
        part_label=rop.resolved_target_scope_part_label,
    )


def _event_path_explains_observed(candidate: TreePath | None, observed_paths: TreePaths) -> bool:
    if candidate is None:
        return not observed_paths
    declared = (candidate,)
    return all(
        path_has_prefix(path, declared) or path_is_strict_prefix(path, candidate)
        for path in observed_paths
    )


def _observed_single_write_event_path(
    before_state: "ReplayState",
    after_state: "ReplayState",
    candidate: TreePath | None,
) -> TreePath | None:
    """Return the single observed landed path when the nominal event path is stale.

    Section materialization can temporarily make a chapter/section lookup
    ambiguous. If ``landed_section_event_path`` then falls back to an unrelated
    same-numbered section, the mutation event under-declares the actual write.
    For a single observed write, prefer the observed landed container/path.
    """
    if before_state.ir is after_state.ir:
        return candidate
    observed_paths = diff_ir_paths_identity_pruned(before_state.ir, after_state.ir)
    if not observed_paths or _event_path_explains_observed(candidate, observed_paths):
        return candidate
    if len(observed_paths) == 1:
        return observed_paths[0]
    return candidate


def _apply_intent_section_level(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    muutos_ir: Optional[IRNode],
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    used_fallback_tags: tuple[str, ...] = ()
    base_ir = ctx.base_ir

    def _fail(reason: str, *, reason_code: str = "") -> None:
        replay_print(f"  {ctx_label} → FAILED ({reason})")
        if failed_ops_out is not None:
            failed_ops_out.append(
                FailedOp.from_scope(
                    amendment_id=rop.resolved_source_statute,
                    description=rop_description,
                    reason=reason,
                    reason_code=reason_code,
                    target_section=rop.resolved_target_label,
                    target_chapter=rop.resolved_target_scope_chapter_label,
                    target_part=rop.resolved_target_scope_part_label,
                    target_subsection=rop.resolved_target_subsection_label,
                    target_item=rop.resolved_target_item_label,
                    target_unit_kind=rop.target_unit_kind,
                )
            )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="apply_op",
            outcome="failed",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            used_fallback_tags=used_fallback_tags,
            failure_reason=reason,
            reason_code=reason_code,
        )

    section_resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        muutos_ir,
        path_hint,
        ctx_label,
        migration_ledger=migration_ledger,
    )
    # Passive binding provenance (apply contract vertical rollout, step 1-2):
    # record which ladder rung bound the target. A contract violation here is
    # a mapping bug in our instrumentation, never a replay failure — surface
    # it loudly under its own tag instead of crashing the apply lane.
    try:
        _binding = section_resolver_binding(rop, section_resolution, ctx_label)
        logger.debug(
            "  %s → resolver binding %s rung=%s status=%s candidates=%s",
            ctx_label,
            _binding.binding_id,
            _binding.rung_id,
            _binding.binding_status,
            _binding.candidate_count,
        )
    except ValueError as exc:
        logger.warning(
            "  %s → APPLY.RESOLVER_BINDING_CONTRACT_ERROR: %s", ctx_label, exc
        )
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code="resolver_binding_contract_error",
            failure_reason=str(exc),
            clause_text=f"resolver binding contract error: {exc}",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="section_resolver_binding",
            outcome="skipped",
            resolved_target_path=_resolved_target_path_for_rop_event(
                rop,
                section_resolution.path,
            ),
            used_fallback_tags=(
                "APPLY.RESOLVER_BINDING_CONTRACT_ERROR",
                "resolver_binding_contract_error",
            ),
            failure_reason=str(exc),
            reason_code="resolver_binding_contract_error",
        )
    sec_path = section_resolution.path
    if section_resolution.used_live_unique_global_fallback:
        used_fallback_tags = (
            "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
            str(section_resolution.reason_code or SectionPathResolutionReason.LIVE_UNIQUE_GLOBAL_FALLBACK),
        )
    elif section_resolution.reason_code is SectionPathResolutionReason.FOLLOW_SAME_WAVE_MIGRATION:
        used_fallback_tags = (
            "APPLY.SAME_WAVE_MIGRATION_REBASE",
            str(SectionPathResolutionReason.FOLLOW_SAME_WAVE_MIGRATION),
        )
    mixed_sparse_insert = (
        rop.slot_assignment is not None
        and rop.effective_target_paragraph is None
        and rop.effective_target_item_label is None
        and rop.effective_target_special is None
        and any(binding.op_type == "INSERT" for binding in rop.slot_assignment.sparse_slot_bindings)
    )
    descendant_scoped_target = (
        rop.resolved_target_subsection_label is not None
        or rop.effective_target_paragraph is not None
        or rop.effective_target_item_label is not None
        or rop.effective_target_special is not None
    )
    migration_rebased_target_path: TreePath | None = None
    migration_rebase_source_path: TreePath | None = None
    if (
        rop.resolved_action_type in {"INSERT", "REPLACE"}
        and migration_ledger is not None
        and same_wave_migration_follow_is_allowed(rop)
    ):
        rop_lo = getattr(rop, "lo", None)
        source_address = rop.resolved_target_address or (rop_lo.target if rop_lo is not None else None)
        if source_address is not None:
            op_effective = migration_lower_bound_for_op(rop)
            migrated = migration_ledger.current_address_with_prefix_migrations(
                source_address, not_before=op_effective
            )
            if migrated != source_address and migrated.path and migrated.path[-1][0] == "section":
                migration_rebased_target_path = _path_to_tuple(migrated.path)
                source_labels = {kind: label for kind, label in source_address.path}
                source_section = source_labels.get("section")
                if source_section:
                    migration_rebase_source_path = _path_to_tuple(
                        state.find_section_path(
                            source_section,
                            source_labels.get("chapter"),
                            source_labels.get("part"),
                        )
                    )
                if descendant_scoped_target:
                    sec_path = migration_rebased_target_path
                else:
                    sec_path = None

    whole_pathology_cursor = len(source_pathologies_out) if source_pathologies_out is not None else 0
    whole_result = None
    if not descendant_scoped_target:
        whole_result = _apply_whole_section_op(
            state,
            rop,
            sec_path,
            muutos_ir,
            cross_ir,
            profile,
            ctx_label,
            base_ir=base_ir,
            replay_history_ops=replay_history_ops,
            source_pathologies_out=source_pathologies_out,
            mixed_sparse_insert=mixed_sparse_insert,
            migration_ledger=migration_ledger,
        )
    if whole_result is not None:
        whole_new_pathologies: tuple[SourcePathology, ...] = ()
        if source_pathologies_out is not None:
            whole_new_pathologies = tuple(source_pathologies_out[whole_pathology_cursor:])
        resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
        if migration_rebased_target_path is not None:
            resolved_target_path = migration_rebased_target_path
        elif sec_path is None and whole_result is not state:
            post_path = _post_apply_section_path(whole_result, rop)
            if post_path is not None:
                resolved_target_path = post_path
            resolved_target_path = _observed_single_write_event_path(
                state,
                whole_result,
                resolved_target_path,
            )
        parent_path = _parent_path(resolved_target_path)
        rebind_paths = _whole_section_move_rebind_paths(state, rop, muutos_ir, sec_path)
        declared_allowances = _whole_section_move_rebind_allowances(state, rop, muutos_ir, sec_path)
        if migration_rebase_source_path is not None:
            declared_allowances = declared_allowances + (
                DeclaredMutationAllowance(
                    kind="migration_path",
                    paths=(migration_rebase_source_path,),
                    rule_id="pending_source_chain_insert_rebase",
                ),
            )
        consumed_unscoped_duplicate_paths = _whole_section_unscoped_duplicate_consumed_paths(
            before_state=state,
            after_state=whole_result,
            new_pathologies=whole_new_pathologies,
            rop=rop,
            sec_path=sec_path,
        )
        if consumed_unscoped_duplicate_paths:
            declared_allowances = declared_allowances + (
                DeclaredMutationAllowance(
                    kind="recovery_path",
                    paths=consumed_unscoped_duplicate_paths,
                    rule_id=_SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE_RULE_ID,
                ),
            )
        created_paths: TreePaths = ()
        replaced_paths: TreePaths = ()
        removed_paths: TreePaths = ()
        placeholder_created_paths: TreePaths = ()
        if rop.resolved_action_type == "INSERT":
            if resolved_target_path is not None:
                created_paths = (resolved_target_path,)
            if rebind_paths:
                removed_paths = rebind_paths
            if migration_rebase_source_path is not None:
                removed_paths = tuple(dict.fromkeys((*removed_paths, migration_rebase_source_path)))
        elif rop.resolved_action_type == "REPLACE":
            if resolved_target_path is not None:
                if sec_path is None:
                    if resolved_target_path[-1][0] == "section":
                        created_paths = (resolved_target_path,)
                    else:
                        replaced_paths = (resolved_target_path,)
                else:
                    replaced_paths = (resolved_target_path,)
                if rebind_paths:
                    removed_paths = rebind_paths
                if migration_rebase_source_path is not None:
                    removed_paths = tuple(dict.fromkeys((*removed_paths, migration_rebase_source_path)))
                if consumed_unscoped_duplicate_paths:
                    removed_paths = tuple(
                        dict.fromkeys((*removed_paths, *consumed_unscoped_duplicate_paths))
                    )
        elif rop.resolved_action_type == "REPEAL":
            if profile.synthesize_repeal_placeholders:
                if resolved_target_path is not None:
                    placeholder_created_paths = (resolved_target_path,)
            else:
                if resolved_target_path is not None:
                    removed_paths = (resolved_target_path,)
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_whole_section_op",
            outcome="applied" if whole_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=declared_allowances,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
            removed_paths=removed_paths,
            placeholder_created_paths=placeholder_created_paths,
            used_fallback_tags=used_fallback_tags,
        )
        return whole_result

    if sec_path is None:
        mat_landed_paths: List[Path] = []
        mat_result = _apply_materialization(
            state,
            rop,
            muutos_ir,
            ctx_label,
            migration_ledger=migration_ledger,
            source_pathologies_out=source_pathologies_out,
            landed_paths_out=mat_landed_paths,
        )
        if mat_result is not None:
            resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
            if mat_result is not state:
                # Declare the path the materialization actually wrote to. The
                # nominal target label can be a pre-renumber label under a
                # restructure plan, and re-resolving it post-apply can bind an
                # unrelated same-labeled section via the global fallback.
                landed_path = _path_to_tuple(mat_landed_paths[0]) if mat_landed_paths else None
                if landed_path is not None:
                    resolved_target_path = landed_path
                else:
                    post_path = _post_apply_section_path(mat_result, rop)
                    if post_path is not None:
                        resolved_target_path = post_path
                    resolved_target_path = _observed_single_write_event_path(
                        state,
                        mat_result,
                        resolved_target_path,
                    )
            root_move_paths = _materialization_root_move_paths(state, rop, muutos_ir, sec_path)
            materialized_created_paths: TreePaths = ()
            materialized_replaced_paths: TreePaths = ()
            if resolved_target_path is not None:
                if resolved_target_path[-1][0] == "section":
                    materialized_created_paths = (resolved_target_path,)
                else:
                    materialized_replaced_paths = (resolved_target_path,)
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_materialization",
                outcome="applied" if mat_result is not state else "failed",
                resolved_target_path=resolved_target_path,
                parent_path=_parent_path(resolved_target_path),
                declared_allowances=_materialization_root_move_allowances(state, rop, muutos_ir, sec_path),
                created_paths=materialized_created_paths,
                replaced_paths=materialized_replaced_paths,
                removed_paths=root_move_paths,
                used_fallback_tags=used_fallback_tags,
            )
            return mat_result

    if sec_path is None:
        if rop.is_repeal_action and rop.targets_subsection_only():
            logger.debug(
                "  %s → subsection repeal skipped (parent section §%s already absent)",
                ctx_label,
                rop.resolved_target_label,
            )
            _emit_apply_op_skipped_witness(
                findings_out,
                rop=rop,
                reason_code="idempotent_repeal_parent_section_absent",
                failure_reason="parent section already absent (idempotent repeal)",
                clause_text=f"repeal target subsection label={rop.resolved_target_subsection_label}",
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="apply_op",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop),
                used_fallback_tags=used_fallback_tags,
                failure_reason="parent section already absent (idempotent repeal)",
            )
            return state
        _fail(
            f"master §{rop.resolved_target_label} not found",
            reason_code="section_not_found",
        )
        return state

    sec_node = _tops.resolve(state.ir, sec_path)
    assert sec_node is not None, f"resolve failed for {sec_path}"
    master_subsecs_ir = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    resolved_amend_sub_ir = rop.resolved_amend_sub_ir()
    subsection_dispatch_op, subsection_rop = _normalize_subsection_dispatch_inputs(
        dispatch_op=rop,
        rop=rop,
        master_subsecs=master_subsecs_ir,
        amend_sub_ir=resolved_amend_sub_ir,
        ctx_label=ctx_label,
        source_pathologies_out=source_pathologies_out,
        strict_profile=strict_profile,
    )
    assert subsection_rop is not None, "typed subsection normalization must preserve the late-waist op"

    pathology_cursor = len(source_pathologies_out) if source_pathologies_out is not None else 0
    subsection_result = _apply_deterministic_subsection_op(
        state,
        subsection_dispatch_op,
        sec_path,
        muutos_ir,
        resolved_amend_sub_ir,
        rop.slot_assignment,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
        cross_ir=cross_ir,
        rop=subsection_rop,
        replay_history_ops=replay_history_ops,
        base_ir=base_ir,
        migration_ledger=migration_ledger,
    )
    if subsection_result is not None:
        resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
        parent_path = _parent_path(resolved_target_path)
        new_pathologies: tuple[SourcePathology, ...] = ()
        if source_pathologies_out is not None:
            new_pathologies = tuple(source_pathologies_out[pathology_cursor:])
        consumed_paths: TreePaths = ()
        created_paths: TreePaths = ()
        replaced_paths: TreePaths = ()
        declared_allowances: tuple[DeclaredMutationAllowance, ...] = ()
        if subsection_result is not state and resolved_target_path is not None:
            action = rop.resolved_action_type.lower()
            if action == "insert":
                created_paths = (resolved_target_path,)
            elif action == "replace":
                replaced_paths = (resolved_target_path,)
            else:
                consumed_paths = (resolved_target_path,)
        # The subsection dispatch updates the section heading as a side-effect
        # when the amendment payload carries a differing heading
        # (``_maybe_update_section_heading``). Declare the heading sibling the
        # helper actually touched so the change is accounted, not undeclared.
        heading_created, heading_replaced = _section_heading_touch_paths(
            sec_node, subsection_result, sec_path
        )
        created_paths = created_paths + heading_created
        replaced_paths = replaced_paths + heading_replaced
        # The nominal subsection:N/item:M suffix can disagree with the live
        # subsection index the elaboration rails landed on; declare the landed
        # section-subtree touches so the declaration reflects reality, not the
        # address' subsection count.
        already_declared = set(created_paths + replaced_paths + consumed_paths)
        landed_paths = tuple(
            path
            for path in _section_subtree_landed_touch_paths(sec_node, subsection_result, sec_path)
            if path not in already_declared
        )
        declared_allowances = _subsection_dispatch_landed_recovery_allowances(
            new_pathologies=new_pathologies,
            landed_paths=landed_paths,
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
        )
        replaced_paths = replaced_paths + landed_paths
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_deterministic_subsection_op",
            outcome="applied" if subsection_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=declared_allowances,
            used_fallback_tags=used_fallback_tags,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
        )
        return subsection_result

    failure_reason = classify_subsection_dispatch_failure(subsection_dispatch_op, sec_node)
    _fail(failure_reason.reason, reason_code=failure_reason.reason_code)
    return state


def _record_unhandled_typed_target_failed_op(
    failed_ops_out: Optional[List[FailedOp]],
    *,
    rop: ResolvedOp,
    rop_description: str,
    reason: str,
    reason_code: str,
) -> None:
    if failed_ops_out is None:
        return
    failed_ops_out.append(
        FailedOp.from_scope(
            amendment_id=rop.resolved_source_statute,
            description=rop_description,
            reason=reason,
            reason_code=reason_code,
            target_section=rop.resolved_target_label,
            target_chapter=rop.resolved_target_scope_chapter_label,
            target_part=rop.resolved_target_scope_part_label,
            target_subsection=rop.resolved_target_subsection_label,
            target_item=rop.resolved_target_item_label,
            target_unit_kind=rop.target_unit_kind,
        )
    )


def _apply_intent_container(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    muutos_ir: Optional[IRNode],
    *,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    strict_profile: Optional[StrictProfile] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    base_ir = ctx.base_ir
    structure_view = _structure_apply_view_for_op(rop)
    mixed_sparse_insert = (
        rop.slot_assignment is not None
        and structure_view.target_paragraph is None
        and structure_view.target_item is None
        and structure_view.target_special is None
        and any(binding.op_type == "INSERT" for binding in rop.slot_assignment.sparse_slot_bindings)
    )
    write_receipts: List[WriteReceipt] = []
    container_result = _apply_container_op(
        state,
        structure_view,
        muutos_ir,
        profile,
        ctx_label,
        base_ir=base_ir,
        standalone_section_targets=standalone_section_targets,
        mixed_sparse_insert=mixed_sparse_insert,
        source_pathologies_out=source_pathologies_out,
        migration_ledger=migration_ledger,
        write_receipts_out=write_receipts,
        replay_history_ops=replay_history_ops,
        findings_out=findings_out,
        strict_profile=strict_profile,
    )
    if container_result is not None:
        # Receipt-first declaration (apply contract §4): when the container
        # helper produced a WriteReceipt — currently the chapter/part INSERT
        # family — the mutation event is DERIVED from the landed footprint,
        # never re-assembled from the nominal target address.
        if write_receipts and container_result is not state:
            receipt = write_receipts[-1]
            _append_observed_write_audit(
                write_audits_out,
                before_ir=state.ir,
                after_ir=container_result.ir,
                receipt=receipt,
            )
            _emit_apply_mutation_event_from_receipt(
                mutation_events_out,
                rop=rop,
                receipt=receipt,
                outcome="applied",
            )
            return container_result
        container_applied = container_result is not state
        resolved_target_path = (
            _target_address_path_for_rop_event(rop, path_hint)
            if container_applied
            else None
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_container_op",
            outcome="applied" if container_applied else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=_parent_path(resolved_target_path),
            created_paths=(resolved_target_path,) if container_applied and rop.resolved_action_type == "INSERT" and resolved_target_path is not None else (),
            removed_paths=(resolved_target_path,) if container_applied and rop.resolved_action_type == "REPEAL" and resolved_target_path is not None and not profile.synthesize_repeal_placeholders else (),
            replaced_paths=(resolved_target_path,) if container_applied and rop.resolved_action_type == "REPLACE" and resolved_target_path is not None else (),
            placeholder_created_paths=(
                (resolved_target_path,) if container_applied and rop.resolved_action_type == "REPEAL" and profile.synthesize_repeal_placeholders and resolved_target_path is not None else ()
            ),
        )
        return container_result

    logger.warning("  %s → container intent dispatch: _apply_container_op returned None", ctx_label)
    _emit_apply_op_skipped_witness(
        findings_out,
        rop=rop,
        reason_code="container_op_returned_none",
        failure_reason="_apply_container_op returned None for container intent",
        clause_text=f"container intent target={rop.target_norm}",
    )
    _emit_apply_mutation_event_for_rop(
        mutation_events_out,
        rop=rop,
        helper="_apply_intent_container",
        outcome="skipped",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        failure_reason="_apply_container_op returned None for container intent",
        reason_code="container_op_returned_none",
    )
    return state


def _apply_intent_replace(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Replace",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import FacetTarget, NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case FacetTarget(facet=FacetKind.HEADING | FacetKind.INTRO) if _is_container_facet_replace(intent):
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case FacetTarget(facet=FacetKind.HEADING | FacetKind.INTRO):
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "subitem", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case _:
            reason = f"unhandled Replace target: {type(intent.target).__name__}"
            reason_code = "unhandled_replace_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Replace target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_op_skipped_witness(
                findings_out,
                rop=rop,
                reason_code=reason_code,
                failure_reason=reason,
                clause_text=f"unhandled Replace target type={type(intent.target).__name__}",
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_replace",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_insert(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Insert",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "subitem", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case _:
            reason = f"unhandled Insert target: {type(intent.target).__name__}"
            reason_code = "unhandled_insert_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Insert target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_op_skipped_witness(
                findings_out,
                rop=rop,
                reason_code=reason_code,
                failure_reason=reason,
                clause_text=f"unhandled Insert target type={type(intent.target).__name__}",
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_insert",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_repeal(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Repeal",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "subitem", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case _:
            reason = f"unhandled Repeal target: {type(intent.target).__name__}"
            reason_code = "unhandled_repeal_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Repeal target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_op_skipped_witness(
                findings_out,
                rop=rop,
                reason_code=reason_code,
                failure_reason=reason,
                clause_text=f"unhandled Repeal target type={type(intent.target).__name__}",
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_repeal",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_relabel(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Relabel",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    def _emit_relabel_skip(
        *,
        reason_tag: str,
        failure_reason: str,
        resolved_target_path: TreePath | None,
    ) -> None:
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=reason_tag,
            failure_reason=failure_reason,
            clause_text=f"relabel source={rop.target_norm} reason_tag={reason_tag}",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_relabel",
            outcome="skipped",
            resolved_target_path=resolved_target_path,
            used_fallback_tags=("APPLY.RELABEL_SKIPPED", reason_tag),
            failure_reason=failure_reason,
            reason_code=reason_tag,
        )

    dest_label = (
        intent.destination.address.leaf_label()
        if intent.destination is not None and intent.destination.address.path
        else None
    )

    source_unit_kind = _relabel_source_unit_kind(intent)

    if dest_label and source_unit_kind in {"chapter", "part"}:
        kind = source_unit_kind
        src_path = None
        scoped_prefix: Path | None = None
        if source_unit_kind == "chapter":
            part_label = rop.resolved_target_scope_part_label
            if part_label:
                scoped_prefix = (("part", part_label),)
            if rop.target_norm:
                scoped_prefix = (scoped_prefix or ()) + (("chapter", rop.target_norm),)
        elif source_unit_kind == "part" and rop.target_norm:
            scoped_prefix = (("part", rop.target_norm),)
        if scoped_prefix is not None and _tops.resolve(state.ir, scoped_prefix) is not None:
            src_path = scoped_prefix
        for candidate in (
            rop.resolved_target_address.path if rop.resolved_target_address is not None else None,
            path_hint,
        ):
            if not candidate:
                continue
            prefix: Path = ()
            for step_kind, step_label in candidate:
                prefix = prefix + ((step_kind, step_label),)
                if step_kind != kind:
                    continue
                if normalized_label_key(step_label) != normalized_label_key(rop.target_norm):
                    continue
                if _tops.resolve(state.ir, prefix) is not None:
                    src_path = prefix
                    break
            if src_path is not None:
                break
        if src_path is None:
            src_path = state.find(kind, rop.target_norm)
        if src_path is not None:
            node = _tops.resolve(state.ir, src_path)
            if node is not None:
                renamed = (
                    _relabel_chapter_ir(node, dest_label)
                    if source_unit_kind == "chapter"
                    else IRNode(
                        kind=node.kind,
                        label=dest_label,
                        text=node.text,
                        attrs=dict(node.attrs),
                        children=tuple(node.children),
                    )
                )
                logger.debug("  %s → Relabel container %s → %s", ctx_label, rop.target_norm, dest_label)
                landed_path = src_path[:-1] + ((source_unit_kind, dest_label),)
                new_ir = _tops.replace_at(state.ir, src_path, renamed)
                landed_node = _tops.resolve(new_ir, landed_path)
                if landed_node is None:
                    raise RuntimeError(f"container relabel landed path disappeared: {landed_path!r}")
                receipt = _build_relabel_write_receipt(
                    rop=rop,
                    src_path=src_path,
                    landed_path=landed_path,
                    source_node=node,
                    landed_node=landed_node,
                    migration_rule_id=_container_relabel_migration_rule_id(source_unit_kind),
                )
                _append_observed_write_audit(
                    write_audits_out,
                    before_ir=state.ir,
                    after_ir=new_ir,
                    receipt=receipt,
                )
                _emit_apply_mutation_event_from_receipt(
                    mutation_events_out,
                    receipt=receipt,
                    rop=rop,
                    outcome="applied",
                )
                if migration_ledger is not None:
                    source = rop.resolved_op_source
                    effective = (source.effective or source.enacted) if source is not None else ""
                    migration_ledger.record_renumber(
                        LegalAddress(path=src_path),
                        LegalAddress(path=landed_path),
                        effective=effective,
                        source_statute=rop.resolved_source_statute,
                    )
                return state.with_ir(new_ir)
        logger.debug(
            "  %s → Relabel container %s not found (absent — may have been renamed already)", ctx_label, rop.target_norm
        )
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code="source_container_missing",
            failure_reason=f"source container {source_unit_kind}:{rop.target_norm} not found",
            clause_text=f"relabel source_container_missing kind={source_unit_kind} label={rop.target_norm}",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_relabel",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            used_fallback_tags=("APPLY.RELABEL_SKIPPED", "source_container_missing"),
            failure_reason=f"source container {source_unit_kind}:{rop.target_norm} not found",
            reason_code="source_container_missing",
        )
        return state

    if dest_label and source_unit_kind == "section":
        dest_path = intent.destination.address.path if intent.destination is not None else ()
        lookup_scope = rop.resolved_section_lookup_scope_view
        source_target_chapter = lookup_scope.target_chapter
        source_target_part = lookup_scope.target_part
        dest_chapter = next((lbl for kind, lbl in dest_path if kind == "chapter"), None) or rop.resolved_target_scope_chapter_label
        dest_part = next((lbl for kind, lbl in dest_path if kind == "part"), None) or lookup_scope.target_part
        src_path = state.find_section_path(
            lookup_scope.target_norm,
            lookup_scope.target_chapter,
            lookup_scope.target_part,
        )
        if src_path is not None:
            node = _tops.resolve(state.ir, src_path)
            if node is not None:
                without_source = _tops.remove_at(state.ir, src_path)
                parent_path = (
                    src_path[:-1]
                    if dest_chapter == source_target_chapter and dest_part == source_target_part
                    else _find_scoped_section_insert_parent_path(
                        without_source,
                        chapter_label=dest_chapter,
                        part_label=dest_part,
                    )
                )
                parent_node = _tops.resolve(without_source, parent_path)
                existing_dest = _tops.find(parent_node, "section", dest_label) if parent_node is not None else None
                if existing_dest is not None:
                    logger.debug(
                        "  %s → Relabel section %s -> %s skipped (destination already exists)",
                        ctx_label,
                        rop.target_norm,
                        dest_label,
                    )
                    _emit_relabel_skip(
                        reason_tag="destination_exists",
                        failure_reason=f"destination section {dest_label} already exists",
                        resolved_target_path=cast(TreePath, _path_to_tuple(src_path)),
                    )
                    return state
                relabelled = _relabel_section_ir(node, dest_label)
                logger.debug(
                    "  %s → Relabel section %s -> %s%s",
                    ctx_label,
                    rop.target_norm,
                    dest_label,
                    f" in chapter {dest_chapter}" if dest_chapter else "",
                )
                landed_path = parent_path + (("section", dest_label),)
                new_ir = _tops.insert_sorted(without_source, parent_path, relabelled)
                landed_node = _tops.resolve(new_ir, landed_path)
                if landed_node is None:
                    raise RuntimeError(f"section relabel landed path disappeared: {landed_path!r}")
                receipt = _build_relabel_write_receipt(
                    rop=rop,
                    src_path=src_path,
                    landed_path=landed_path,
                    source_node=node,
                    landed_node=landed_node,
                    migration_rule_id=_SECTION_RELABEL_MIGRATION_RULE_ID,
                )
                _append_observed_write_audit(
                    write_audits_out,
                    before_ir=state.ir,
                    after_ir=new_ir,
                    receipt=receipt,
                )
                _emit_apply_mutation_event_from_receipt(
                    mutation_events_out,
                    receipt=receipt,
                    rop=rop,
                    outcome="applied",
                )
                if migration_ledger is not None:
                    source = rop.resolved_op_source
                    effective = (source.effective or source.enacted) if source is not None else ""
                    migration_ledger.record_renumber(
                        LegalAddress(path=src_path),
                        LegalAddress(path=landed_path),
                        effective=effective,
                        source_statute=rop.resolved_source_statute,
                    )
                return state.with_ir(new_ir)
        logger.debug(
            "  %s → Relabel section %s not found (absent — may have been renamed already)", ctx_label, rop.target_norm
        )
        _emit_relabel_skip(
            reason_tag="source_section_missing",
            failure_reason=f"source section {rop.target_norm} not found",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        )
        return state

    if dest_label and source_unit_kind == "subsection":
        source_path = intent.source.address.path
        source_section = next((lbl for kind, lbl in source_path if kind == "section"), None)
        source_chapter = next((lbl for kind, lbl in source_path if kind == "chapter"), None)
        source_part = next((lbl for kind, lbl in source_path if kind == "part"), None)
        source_subsection = intent.source.address.leaf_label()

        dest_path = intent.destination.address.path if intent.destination is not None else ()
        dest_section = next((lbl for kind, lbl in dest_path if kind == "section"), None) or source_section
        dest_chapter = next((lbl for kind, lbl in dest_path if kind == "chapter"), None) or source_chapter
        dest_part = next((lbl for kind, lbl in dest_path if kind == "part"), None) or source_part

        if source_section is None or dest_section != source_section or dest_chapter != source_chapter or dest_part != source_part:
            logger.warning(
                "RELABEL_UNHANDLED: %s %s — subsection relabel across parent boundaries not yet implemented",
                rop.resolved_action_type,
                rop.target_norm,
            )
            _emit_relabel_skip(
                reason_tag="cross_parent_unimplemented",
                failure_reason="subsection relabel across parent boundaries not yet implemented",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state

        section_path = state.find_section_path(source_section, source_chapter, source_part)
        if section_path is None:
            _emit_relabel_skip(
                reason_tag="source_section_missing",
                failure_reason=f"source section {source_section} not found for subsection relabel",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state
        section_node = _tops.resolve(state.ir, section_path)
        if section_node is None:
            return state

        subsections = [child for child in section_node.children if child.kind is IRNodeKind.SUBSECTION]
        source_idx = next((idx for idx, child in enumerate(subsections) if child.label == source_subsection), None)
        if source_idx is None:
            _emit_relabel_skip(
                reason_tag="source_subsection_missing",
                failure_reason=f"source subsection {source_subsection} not found",
                resolved_target_path=cast(TreePath, _path_to_tuple(section_path)),
            )
            return state
        if any(child.label == dest_label for idx, child in enumerate(subsections) if idx != source_idx):
            _emit_relabel_skip(
                reason_tag="destination_exists",
                failure_reason=f"destination subsection {dest_label} already exists",
                resolved_target_path=cast(TreePath, _path_to_tuple(section_path)),
            )
            return state

        rebuilt_subsections = list(subsections)
        source_subsection_node = rebuilt_subsections[source_idx]
        rebuilt_subsections[source_idx] = _relabel_subsection_ir(source_subsection_node, dest_label)
        rebuilt_subsections.sort(key=lambda child: default_label_sort_key(child.label))
        rebuilt_section = _rebuild_section_with_subsections_ir(section_node, rebuilt_subsections)

        source_subsection_path = section_path + (("subsection", source_subsection),)
        landed_subsection_path = section_path + (("subsection", dest_label),)
        landed_subsection_node = next(
            child for child in rebuilt_subsections if child.kind is IRNodeKind.SUBSECTION and child.label == dest_label
        )
        receipt = _build_relabel_write_receipt(
            rop=rop,
            src_path=source_subsection_path,
            landed_path=landed_subsection_path,
            source_node=source_subsection_node,
            landed_node=landed_subsection_node,
            migration_rule_id=_SUBSECTION_RELABEL_MIGRATION_RULE_ID,
        )
        new_ir = _tops.replace_at(state.ir, section_path, rebuilt_section)
        _append_observed_write_audit(
            write_audits_out,
            before_ir=state.ir,
            after_ir=new_ir,
            receipt=receipt,
        )
        _emit_apply_mutation_event_from_receipt(
            mutation_events_out,
            receipt=receipt,
            rop=rop,
            outcome="applied",
        )
        if migration_ledger is not None:
            source = rop.resolved_op_source
            effective = (source.effective or source.enacted) if source is not None else ""
            migration_ledger.record_renumber(
                LegalAddress(path=source_subsection_path),
                LegalAddress(path=landed_subsection_path),
                effective=effective,
                source_statute=rop.resolved_source_statute,
            )
        return _with_preserved_provision_index(state, new_ir)

    if dest_label and source_unit_kind == "item":
        source_path = intent.source.address.path
        source_section = next((lbl for kind, lbl in source_path if kind == "section"), None)
        source_chapter = next((lbl for kind, lbl in source_path if kind == "chapter"), None)
        source_part = next((lbl for kind, lbl in source_path if kind == "part"), None)
        source_subsection = next((lbl for kind, lbl in source_path if kind == "subsection"), None)
        source_item = intent.source.address.leaf_label()

        dest_path = intent.destination.address.path if intent.destination is not None else ()
        dest_section = next((lbl for kind, lbl in dest_path if kind == "section"), None) or source_section
        dest_chapter = next((lbl for kind, lbl in dest_path if kind == "chapter"), None) or source_chapter
        dest_part = next((lbl for kind, lbl in dest_path if kind == "part"), None) or source_part
        dest_subsection = next((lbl for kind, lbl in dest_path if kind == "subsection"), None) or source_subsection

        if (
            source_section is None
            or source_subsection is None
            or dest_section != source_section
            or dest_chapter != source_chapter
            or dest_part != source_part
            or dest_subsection != source_subsection
        ):
            logger.warning(
                "RELABEL_UNHANDLED: %s %s — item relabel across parent boundaries not yet implemented",
                rop.resolved_action_type,
                rop.target_norm,
            )
            _emit_relabel_skip(
                reason_tag="cross_parent_unimplemented",
                failure_reason="item relabel across parent boundaries not yet implemented",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state

        section_path = state.find_section_path(source_section, source_chapter, source_part)
        if section_path is None:
            _emit_relabel_skip(
                reason_tag="source_section_missing",
                failure_reason=f"source section {source_section} not found for item relabel",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state
        section_node = _tops.resolve(state.ir, section_path)
        if section_node is None:
            return state

        subsections = [child for child in section_node.children if child.kind is IRNodeKind.SUBSECTION]
        sub_idx = next(
            (idx for idx, child in enumerate(subsections) if child.label == source_subsection),
            None,
        )
        if sub_idx is None:
            _emit_relabel_skip(
                reason_tag="source_subsection_missing",
                failure_reason=f"source subsection {source_subsection} not found for item relabel",
                resolved_target_path=cast(TreePath, _path_to_tuple(section_path)),
            )
            return state
        subsection_node = subsections[sub_idx]
        subsection_path = section_path + (("subsection", source_subsection),)

        items = [child for child in subsection_node.children if child.kind is IRNodeKind.PARAGRAPH]
        source_item_idx = next(
            (idx for idx, child in enumerate(items) if child.label == source_item),
            None,
        )
        if source_item_idx is None:
            _emit_relabel_skip(
                reason_tag="source_item_missing",
                failure_reason=f"source item {source_item} not found",
                resolved_target_path=cast(TreePath, _path_to_tuple(subsection_path)),
            )
            return state
        if any(child.label == dest_label for idx, child in enumerate(items) if idx != source_item_idx):
            _emit_relabel_skip(
                reason_tag="destination_exists",
                failure_reason=f"destination item {dest_label} already exists",
                resolved_target_path=cast(TreePath, _path_to_tuple(subsection_path)),
            )
            return state

        rebuilt_items = list(items)
        source_item_node = rebuilt_items[source_item_idx]
        rebuilt_items[source_item_idx] = _relabel_item_ir(source_item_node, dest_label)
        rebuilt_items.sort(key=lambda child: default_label_sort_key(child.label))
        rebuilt_subsection = _rebuild_subsection_with_items_ir(subsection_node, rebuilt_items)

        rebuilt_subsections = list(subsections)
        rebuilt_subsections[sub_idx] = rebuilt_subsection
        rebuilt_section = _rebuild_section_with_subsections_ir(section_node, rebuilt_subsections)

        source_item_path = subsection_path + (("item", source_item),)
        landed_item_path = subsection_path + (("item", dest_label),)
        landed_item_node = next(
            child for child in rebuilt_items if child.kind is IRNodeKind.PARAGRAPH and child.label == dest_label
        )
        receipt = _build_relabel_write_receipt(
            rop=rop,
            src_path=source_item_path,
            landed_path=landed_item_path,
            source_node=source_item_node,
            landed_node=landed_item_node,
            migration_rule_id=_ITEM_RELABEL_MIGRATION_RULE_ID,
        )
        new_ir = _tops.replace_at(state.ir, section_path, rebuilt_section)
        _append_observed_write_audit(
            write_audits_out,
            before_ir=state.ir,
            after_ir=new_ir,
            receipt=receipt,
        )
        _emit_apply_mutation_event_from_receipt(
            mutation_events_out,
            receipt=receipt,
            rop=rop,
            outcome="applied",
        )
        if migration_ledger is not None:
            source = rop.resolved_op_source
            effective = (source.effective or source.enacted) if source is not None else ""
            migration_ledger.record_renumber(
                LegalAddress(path=source_item_path),
                LegalAddress(path=landed_item_path),
                effective=effective,
                source_statute=rop.resolved_source_statute,
            )
        return _with_preserved_provision_index(state, new_ir)

    logger.warning(
        "RELABEL_UNHANDLED: %s %s — Relabel target kind %r not yet implemented",
        rop.resolved_action_type,
        rop.target_norm,
        source_unit_kind,
    )
    _emit_relabel_skip(
        reason_tag="target_kind_unimplemented",
        failure_reason=f"Relabel target kind {source_unit_kind!r} not yet implemented",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
    )
    return state


def _apply_intent_move(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Move",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    source_addr = intent.source.address
    dest_parent_path = intent.destination_parent.path

    source_leaf_kind = source_addr.leaf_kind()
    source_leaf_label = source_addr.leaf_label()
    if not source_leaf_kind or not source_leaf_label:
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=_MOVE_SKIP_REASON_CODES["source_address_empty"],
            failure_reason="Move source address is empty",
            clause_text=f"move source address path={source_addr.path}",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason="Move source address is empty",
            reason_code=_MOVE_SKIP_REASON_CODES["source_address_empty"],
        )
        return state

    source_part = next((lbl for kind, lbl in source_addr.path if kind == "part"), None)
    source_chapter = next((lbl for kind, lbl in source_addr.path if kind == "chapter"), None)
    if source_leaf_kind == "section":
        src_path = state.find_section_path(source_leaf_label, source_chapter, source_part)
    else:
        parent = source_addr.parent()
        scope_kind = parent.leaf_kind() if parent is not None else None
        scope_label = parent.leaf_label() if parent is not None else None
        src_path = state.find(
            source_leaf_kind,
            source_leaf_label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )

    if src_path is None:
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=_MOVE_SKIP_REASON_CODES["source_not_found"],
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} not found",
            clause_text=f"move source {source_leaf_kind}:{source_leaf_label} not found in tree",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} not found",
            reason_code=_MOVE_SKIP_REASON_CODES["source_not_found"],
        )
        return state

    node = _tops.resolve(state.ir, src_path)
    if node is None:
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=_MOVE_SKIP_REASON_CODES["source_resolved_none"],
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} could not be resolved",
            clause_text=f"move source {source_leaf_kind}:{source_leaf_label} path-resolved to None",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(TreePath, _path_to_tuple(src_path)),
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} could not be resolved",
            reason_code=_MOVE_SKIP_REASON_CODES["source_resolved_none"],
        )
        return state

    destination_node = _tops.resolve(state.ir, dest_parent_path)
    if destination_node is None:
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=_MOVE_SKIP_REASON_CODES["destination_parent_not_found"],
            failure_reason=f"destination parent {'/'.join(f'{k}:{v}' for k, v in dest_parent_path) or '<root>'} not found",
            clause_text=f"move destination parent path={dest_parent_path} not found",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(TreePath, _path_to_tuple(src_path)),
            failure_reason=f"destination parent {'/'.join(f'{k}:{v}' for k, v in dest_parent_path) or '<root>'} not found",
            reason_code=_MOVE_SKIP_REASON_CODES["destination_parent_not_found"],
        )
        return state

    if any(child.kind == node.kind and child.label == node.label for child in destination_node.children):
        _emit_apply_op_skipped_witness(
            findings_out,
            rop=rop,
            reason_code=_MOVE_SKIP_REASON_CODES["destination_exists"],
            failure_reason=f"destination already contains {node.kind.value}:{node.label}",
            clause_text=f"move destination already contains kind={node.kind.value} label={node.label}",
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(TreePath, _path_to_tuple(src_path)),
            failure_reason=f"destination already contains {node.kind.value}:{node.label}",
            reason_code=_MOVE_SKIP_REASON_CODES["destination_exists"],
        )
        return state

    destination_path = dest_parent_path + ((source_leaf_kind, source_leaf_label),)
    moved_ir = _tops.remove_at(state.ir, src_path)
    moved_ir = _tops.insert_sorted(moved_ir, dest_parent_path, node)

    if migration_ledger is not None:
        source = rop.resolved_op_source
        effective = (source.effective or source.enacted) if source is not None else ""
        destination_address = LegalAddress(path=destination_path, special=source_addr.special)
        migration_ledger.record_move(
            source_addr,
            destination_address,
            effective=effective,
            source_statute=rop.resolved_source_statute,
        )

    source_tree_path = _required_tree_path(src_path)
    destination_tree_path = _required_tree_path(destination_path)
    source_receipt_addr = receipt_address_string(source_tree_path)
    destination_receipt_addr = receipt_address_string(destination_tree_path)
    landed_node = _tops.resolve(moved_ir, destination_path)
    receipt = WriteReceipt(
        op_id=rop.op_id or "",
        helper="_apply_intent_move",
        action=rop.resolved_action_type.lower(),
        bound_target_path=source_tree_path,
        landed_primary_path=destination_tree_path,
        renumbered_paths=((source_tree_path, destination_tree_path),),
        migration_rule_ids=(_MOVE_REPARENT_MIGRATION_RULE_ID,),
        pre_hashes={
            source_receipt_addr: structural_subtree_hash(node),
            destination_receipt_addr: structural_subtree_hash(_tops.resolve(state.ir, destination_path)),
        },
        post_hashes={
            source_receipt_addr: "",
            destination_receipt_addr: structural_subtree_hash(landed_node),
        },
    )
    _append_observed_write_audit(
        write_audits_out,
        before_ir=state.ir,
        after_ir=moved_ir,
        receipt=receipt,
    )
    _emit_apply_mutation_event_from_receipt(
        mutation_events_out,
        receipt=receipt,
        outcome="applied",
        rop=rop,
    )
    return state.with_ir(moved_ir)


def _apply_canonical_intent(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "CanonicalIntent",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    migration_ledger: Optional[MigrationLedger] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    strict_profile: Optional[StrictProfile] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import Replace, Insert, Repeal, Relabel, Move, TextPatch

    def _fail(reason: str, *, reason_code: str = "") -> None:
        replay_print(f"  {ctx_label} → FAILED ({reason})")
        if failed_ops_out is not None:
            failed_ops_out.append(
                FailedOp.from_scope(
                    amendment_id=rop.resolved_source_statute,
                    description=rop_description,
                    reason=reason,
                    reason_code=reason_code,
                    target_section=rop.resolved_target_label,
                    target_chapter=rop.resolved_target_scope_chapter_label,
                    target_part=rop.resolved_target_scope_part_label,
                    target_subsection=rop.resolved_target_subsection_label,
                    target_item=rop.resolved_target_item_label,
                    target_unit_kind=rop.target_unit_kind,
                )
            )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_canonical_intent",
            outcome="failed",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason=reason,
            reason_code=reason_code,
        )

    if cross_ir is None:
        cross_ir = rop.cross_ir
    # Occupancy is observational and must read the SAME slot the apply lands on.
    # Resolving the section here through the narrow scoped find_section_path
    # diverged from the apply's real binding (the full ladder in
    # _apply_intent_section_level), so the check reported "absent" on part-nested
    # / live-unique-global slots the op actually resolves and writes — producing
    # large numbers of false occupancy_violation findings. Use the same ladder so
    # the observation tracks the binding instead of a separate, narrower lookup.
    occupancy_resolution = (
        _resolve_section_path_with_fallbacks(
            state,
            rop,
            rop.muutos_ir,
            path_hint,
            ctx_label,
            migration_ledger=migration_ledger,
        )
        if _intent_targets_section(intent)
        else None
    )
    sec_path = occupancy_resolution.path if occupancy_resolution is not None else None
    _check_occupancy_policy(
        state,
        rop,
        intent,
        sec_path,
        ctx_label,
        findings_out=findings_out,
        replay_history_ops=replay_history_ops,
    )
    _assert_intent_compat(rop, intent, ctx_label, findings_out=findings_out)

    match intent:
        case Replace() as it:
            logger.debug("  %s → canonical dispatch: Replace(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_replace(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case Insert() as it:
            logger.debug("  %s → canonical dispatch: Insert(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_insert(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
                write_audits_out=write_audits_out,
            )
        case Repeal() as it:
            logger.debug("  %s → canonical dispatch: Repeal(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_repeal(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                write_audits_out=write_audits_out,
            )
        case Relabel() as it:
            logger.debug("  %s → canonical dispatch: Relabel(%s)", ctx_label, type(it.source).__name__)
            return _apply_intent_relabel(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                migration_ledger=migration_ledger,
                write_audits_out=write_audits_out,
            )
        case Move() as move_intent:
            logger.debug(
                "  %s → canonical dispatch: Move(%s -> %s)",
                ctx_label,
                type(move_intent.source).__name__,
                type(move_intent.destination_parent).__name__,
            )
            return _apply_intent_move(
                state,
                rop,
                rop_description,
                move_intent,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                path_hint=path_hint,
                migration_ledger=migration_ledger,
                write_audits_out=write_audits_out,
            )
        case TextPatch():
            logger.warning(
                "TEXTPATCH_UNSUPPORTED: %s %s — TextPatch is UK-only; failing closed in Finland apply",
                rop.resolved_action_type,
                rop.target_norm,
            )
            _fail(
                "TextPatch is UK-only and unsupported in Finland apply",
                reason_code="textpatch_unsupported",
            )
            return state
        case _:
            logger.warning(
                "UNKNOWN_TYPED_INTENT: %s %s — unknown intent type %r, failing closed",
                rop.resolved_action_type,
                rop.target_norm,
                type(intent).__name__,
            )
            _fail(
                f"unhandled intent type: {type(intent).__name__}",
                reason_code="unhandled_intent_type",
            )
            return state
