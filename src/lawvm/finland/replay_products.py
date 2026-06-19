"""Typed replay/materialization products for the Finnish frontend."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from typing import TYPE_CHECKING, Callable, Literal, Optional, cast

from lawvm.core.identity_ledger import IdentityLedger
from lawvm.core.provenance import MigrationEvent
from lawvm.core.ir import IRNode, IRStatute, LegalAddress
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.ir import LegalOperation
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
from lawvm.core.invariant_profiles import TreeInvariantProfile
from lawvm.core.invariant_profiles import collect_tree_invariant_violations
from lawvm.core.invariant_profiles import project_tree_invariant_dicts
from lawvm.core.invariant_detectors import run_label_normalization_collision_detector
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal import FIXED_DATE_KIND, ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline_lineage import (
    MaterializationLineageBridgeClassification,
    classify_materialization_lineage_bridge,
    choose_materialization_lineage_decision,
    rekey_timelines_with_migration_events as _core_rekey_timelines_with_migration_events,
)
from lawvm.core.timeline_results import (
    MaterializationLineageDecision,
    MaterializationLineagePlan,
    Timelines,
)
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.tree_ops import (
    TreeInvariantViolation,
    _kind_str,
    check_invariants,
    default_label_sort_key,
    find_provisions_parent as _find_provisions_parent,
    insert_sorted as _insert_sorted,
    remove_at as _remove_at,
    resolve as _tops_resolve,
    resort_children as _resort_children,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.finland.apply_ir_ops import (
    _strip_redundant_paragraph_label_prefixes_ir,
    _strip_standalone_subsection_item_prefixes_ir,
)
from lawvm.finland.helpers import _norm_num_token

if TYPE_CHECKING:
    from lawvm.finland.replay_fold_timeline_backfill import FoldTimelineBackfillRecord
    from lawvm.finland.timeline_version_dedupe import TimelineVersionDedupeRecord
    from lawvm.finland.statute import ReplayState, StatuteContext


_FI_LABEL_NORMALIZER_NAME = "fi_label_norm_v1"
_FI_SLOT_IDENTITY_NORMALIZER_NAME = "fi_slot_identity_norm_v1"
_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS = "rekeyed_with_migrations"
_FI_LINEAGE_MODE_REKEYED_ONLY = "rekeyed_only"
_FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS = "raw_with_migrations"
_FI_LINEAGE_REASON_DEFAULT = "default_migration_projection"
_FI_LINEAGE_REASON_NATIVE_REBIRTH = "native_rebirth_after_renumber"
_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER = "leaf_stable_scope_renumber"
_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY = "destination_occupancy_collision"
_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK = "scope_changing_migration_fallback"
_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE = "fi_sourceless_base_merge_cleanup_v1"
_FI_LABEL_TRAILING_DECORATION_RE = re.compile(r"[^a-zA-Z0-9äöå]+$")
_TIMELINE_SECTION_MARK_SPACING_RE = re.compile(r"^(\d+[a-z]?)\s*§")
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR = (
    "lawvm_materialize_as_absent_under_detached_horizon"
)
_FI_REPLAY_FOLD_MIXED_HIERARCHY_PROFILE = TreeInvariantProfile(
    surface="replay_fold_tree",
    families=("mixed_hierarchy_child",),
    profile_id="fi_product_mixed_hierarchy",
)
_FI_MATERIALIZED_MIXED_HIERARCHY_PROFILE = TreeInvariantProfile(
    surface="materialized_tree",
    families=("mixed_hierarchy_child",),
    profile_id="fi_product_mixed_hierarchy",
)


FinlandLineageBridgeClassification = MaterializationLineageBridgeClassification


@dataclass(frozen=True)
class MaterializationSpec:
    """Typed description of how PIT materialization was derived."""

    as_of: str
    query_type: Literal["governing", "in_force"] = "governing"
    label_normalizer: str = _FI_LABEL_NORMALIZER_NAME
    bridge_classification: FinlandLineageBridgeClassification = FinlandLineageBridgeClassification()
    lineage_plan: MaterializationLineagePlan = MaterializationLineagePlan(
        mode=_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS
    )
    lineage_reason: Literal[
        "default_migration_projection",
        "native_rebirth_after_renumber",
        "leaf_stable_scope_renumber",
        "destination_occupancy_collision",
        "scope_changing_migration_fallback",
    ] = _FI_LINEAGE_REASON_DEFAULT

    @property
    def lineage_mode(self) -> Literal[
        "rekeyed_with_migrations",
        "rekeyed_only",
        "raw_with_migrations",
    ]:
        return self.lineage_plan.mode


@dataclass
class ReplayProducts:
    """Replay artifacts after folding and PIT materialization."""

    replay_fold_state: "ReplayState"
    materialized_state: "ReplayState"
    timelines: Optional[Timelines]
    temporal_events: tuple[TemporalEvent, ...] = ()
    migration_events: tuple[MigrationEvent, ...] = ()
    materialization_spec: Optional[MaterializationSpec] = None
    source_adjudication: Optional[SourceAdjudication] = None
    fold_timeline_backfills: tuple["FoldTimelineBackfillRecord", ...] = ()
    timeline_version_dedupes: tuple["TimelineVersionDedupeRecord", ...] = ()

    @property
    def identity_ledger(self) -> IdentityLedger:
        """Frozen read-only lineage snapshot over replay migration events."""
        return IdentityLedger.from_events(self.migration_events)


def _assert_finland_timeline_safe_ops(lo_ops_out: list[LegalOperation]) -> None:
    """Reject Finland replay ops that still depend on core tombstone quirks.

    Finland should not rely on payload-less ``replace`` semantics in
    ``compile_timelines()``. If a replay path still emits that shape, the fix
    belongs upstream in Finland replay emission, not as a replay-products shim.
    """
    for op in lo_ops_out:
        if op.action is not StructuralAction.REPLACE:
            continue
        if op.payload is not None:
            continue
        if op.op_id.startswith("snapshot_"):
            continue
        raise RuntimeError(
            "FI_TIMELINE_PAYLOADLESS_REPLACE: Finland replay emitted "
            f"payload-less replace for {op.target} (op_id={op.op_id or '<missing-op-id>'}). "
            "Emit explicit repeal semantics or a real replacement payload before "
            "timeline compilation."
        )


def fi_label_norm(label: str) -> str:
    """Normalize Finnish legacy labels for timeline materialization."""
    return _FI_LABEL_TRAILING_DECORATION_RE.sub("", label).strip() or label


def fi_slot_identity_norm(label: str) -> str:
    """Normalize Finnish labels for sibling slot-collision diagnostics."""
    return _norm_num_token(label)


def _fi_label_collision_invariant_messages(tree: IRNode, *, surface: str) -> tuple[str, ...]:
    return tuple(
        f"{surface}:{result.message}"
        for result in run_label_normalization_collision_detector(
            tree,
            fi_slot_identity_norm,
            detector=_FI_SLOT_IDENTITY_NORMALIZER_NAME,
        )
    )


def _fi_root_num_text(kind: IRNodeKind, label: str) -> str | None:
    """Return Finnish-facing NUM child text for migrated roots."""
    kind_value = str(kind)
    if kind_value == IRNodeKind.SECTION.value:
        return f"{label} §"
    if kind_value == IRNodeKind.CHAPTER.value:
        return f"{label} luku"
    return None


def _content_is_repeal_placeholder(node: IRNode) -> bool:
    return node.attrs.get("lawvm_repeal_placeholder") == "1"


def _kind_value(node: IRNode) -> str:
    return node.kind.value if isinstance(node.kind, IRNodeKind) else str(node.kind)


def _resolve_invariant_path(tree: IRNode, path: tuple[tuple[str, str | None], ...]) -> IRNode | None:
    """Resolve a core tree-invariant path against a Finland IR tree."""
    if not path:
        return None
    first_kind, first_label = path[0]
    if _kind_value(tree) != first_kind or tree.label != first_label:
        return None
    node = tree
    for kind, label in path[1:]:
        match = next(
            (
                child
                for child in node.children
                if _kind_value(child) == kind and child.label == label
            ),
            None,
        )
        if match is None:
            return None
        node = match
    return node


def _has_fi_commencement_heading(section: IRNode) -> bool:
    for child in section.children:
        if child.kind is IRNodeKind.HEADING and "voimaantulo" in irnode_to_text(child).casefold():
            return True
    prefix = irnode_to_text(section)[:240].casefold()
    return "voimaantulo" in prefix or "tulee voimaan" in prefix


def _is_terminal_fi_commencement_section_violation(
    tree: IRNode,
    violation: TreeInvariantViolation,
) -> bool:
    """Allow source-authored final FI commencement sections outside chapters.

    Some Finnish base statutes are chaptered but keep the entry-into-force
    section as a root-level final-provisions section. PIT materialization can
    interleave that root section with containers, so the safe allowance is:
    commencement text, at least one container sibling, and no following direct
    section. Ordinary mixed direct sections remain flagged.
    """
    if (
        violation.kind != "mixed_hierarchy_child"
        or violation.child_kind != IRNodeKind.SECTION.value
        or violation.label is None
    ):
        return False
    parent = _resolve_invariant_path(tree, violation.path)
    if parent is None:
        return False
    labeled_children = [
        child
        for child in parent.children
        if child.label is not None and _kind_value(child) in {"part", "chapter", "section"}
    ]
    for index, child in enumerate(labeled_children):
        if child.kind is not IRNodeKind.SECTION or child.label != violation.label:
            continue
        has_container_sibling = any(
            _kind_value(sibling) in {"part", "chapter"}
            for sibling in (*labeled_children[:index], *labeled_children[index + 1 :])
        )
        has_following_direct_section = any(
            right.kind is IRNodeKind.SECTION for right in labeled_children[index + 1 :]
        )
        return has_container_sibling and not has_following_direct_section and _has_fi_commencement_heading(child)
    return False


def fi_product_tree_invariant_violations(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[TreeInvariantViolation, ...]:
    """Collect FI product tree invariants after FI source-shape allowances."""
    return tuple(
        violation
        for violation in collect_tree_invariant_violations(tree, profile)
        if not _is_terminal_fi_commencement_section_violation(tree, violation)
    )


def fi_product_tree_invariant_messages(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[str, ...]:
    return tuple(
        f"{profile.surface}:{violation.message}"
        for violation in fi_product_tree_invariant_violations(tree, profile)
    )


def fi_product_tree_invariant_dicts(
    tree: IRNode,
    profile: TreeInvariantProfile,
) -> tuple[dict[str, object], ...]:
    return project_tree_invariant_dicts(
        fi_product_tree_invariant_violations(tree, profile),
        profile,
    )


def _fold_hcontainer_direct_sections(fold: IRNode) -> tuple[IRNode, ...]:
    """Return section nodes that live directly under the fold provisions wrapper."""
    provisions_node = _fold_provisions_node(fold)
    if provisions_node is None:
        return ()
    return tuple(
        child
        for child in provisions_node.children
        if child.kind is IRNodeKind.SECTION and child.label
    )


def _fold_provisions_node(fold: IRNode) -> IRNode | None:
    if (
        fold.kind is IRNodeKind.BODY
        and len(fold.children) == 1
        and fold.children[0].kind is IRNodeKind.HCONTAINER
        and fold.children[0].attrs.get("name") == "statuteProvisionsWrapper"
    ):
        return fold.children[0]

    provisions_parent = _find_provisions_parent(fold)
    if not provisions_parent:
        return None
    return _tops_resolve(fold, provisions_parent)


def _fold_provisions_has_hierarchical_roots(fold: IRNode) -> bool:
    provisions_node = _fold_provisions_node(fold)
    if provisions_node is None:
        return False
    return any(child.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER} for child in provisions_node.children)


_FI_PROVISIONS_WRAPPER_NAME = "statuteProvisionsWrapper"
_FI_CHAPTER_SECTION_EID_RE = re.compile(r"^chp_(?P<chapter>[^_]+)__sec_")


def _ensure_body_hcontainer(ir: IRNode) -> tuple[IRNode, tuple[tuple[str, str], ...]]:
    """Return body IR with an hcontainer child and that container's path."""
    for child in ir.children:
        if child.kind is IRNodeKind.HCONTAINER:
            return ir, (("hcontainer", child.label or ""),)
    new_hcontainer = IRNode(kind=IRNodeKind.HCONTAINER, children=())
    return (
        IRNode(
            kind=ir.kind,
            label=ir.label,
            text=ir.text,
            attrs=dict(ir.attrs),
            children=ir.children + (new_hcontainer,),
        ),
        (("hcontainer", ""),),
    )


def _iter_sections(node: IRNode) -> tuple[IRNode, ...]:
    sections: list[IRNode] = []

    def _walk(current: IRNode) -> None:
        if current.kind is IRNodeKind.SECTION:
            sections.append(current)
        for child in current.children:
            _walk(child)

    _walk(node)
    return tuple(sections)


def _chapter_label_from_section_eid(node: IRNode) -> str:
    e_id = str(node.attrs.get("eId") or "")
    match = _FI_CHAPTER_SECTION_EID_RE.match(e_id)
    if match is None:
        return ""
    return match.group("chapter").replace("_", " ")


def _is_materialized_provisions_wrapper_candidate(node: IRNode, replay_fold: IRNode) -> bool:
    if node.kind is not IRNodeKind.HCONTAINER:
        return False
    if node.attrs.get("name") == "attachments":
        return False
    if node.attrs.get("name") not in (None, "", _FI_PROVISIONS_WRAPPER_NAME):
        return False
    fold_labels = {
        section.label for section in _fold_hcontainer_direct_sections(replay_fold) if section.label
    }
    if not fold_labels:
        return False
    candidate_labels = {
        child.label for child in node.children if child.kind is IRNodeKind.SECTION and child.label
    }
    return bool(candidate_labels & fold_labels)


def project_materialized_provisions_wrapper(materialized: IRNode, replay_fold: IRNode) -> IRNode:
    """Project fold-owned provisions-wrapper children into materialized legal topology.

    Core PIT materialization preserves unlabeled hcontainer path shape but loses
    the Finland-local ``statuteProvisionsWrapper`` attribute.  For materialized
    products, that wrapper is only a source/editorial carrier: direct sections
    either belong directly under the body, or, when the materialized product has
    chapter shells and the section eId says ``chp_N__sec_X``, under that chapter.
    """
    if materialized.kind is not IRNodeKind.BODY:
        return materialized

    wrapper_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if _is_materialized_provisions_wrapper_candidate(child, replay_fold)
        ),
        None,
    )
    if wrapper_index is None:
        return materialized
    wrapper = materialized.children[wrapper_index]

    has_hierarchical_roots = any(
        child.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER}
        for index, child in enumerate(materialized.children)
        if index != wrapper_index
    )
    if not has_hierarchical_roots:
        rebuilt = tuple(
            grandchild
            for index, child in enumerate(materialized.children)
            for grandchild in ((child.children) if index == wrapper_index else (child,))
        )
        return dc_replace(materialized, children=rebuilt)

    chapter_indices = {
        child.label: index
        for index, child in enumerate(materialized.children)
        if child.kind is IRNodeKind.CHAPTER and child.label
    }
    if not chapter_indices:
        return materialized

    children = list(materialized.children)
    wrapper_children: list[IRNode] = []
    moved_by_chapter: dict[str, list[IRNode]] = {}
    for child in wrapper.children:
        if child.kind is not IRNodeKind.SECTION or not child.label:
            wrapper_children.append(child)
            continue
        chapter_label = _chapter_label_from_section_eid(child)
        if not chapter_label or chapter_label not in chapter_indices:
            wrapper_children.append(child)
            continue
        moved_by_chapter.setdefault(chapter_label, []).append(child)

    if not moved_by_chapter:
        return materialized

    for chapter_label, moved in moved_by_chapter.items():
        chapter_index = chapter_indices[chapter_label]
        chapter = children[chapter_index]
        existing_labels = {
            child.label
            for child in chapter.children
            if child.kind is IRNodeKind.SECTION and child.label
        }
        chapter_children = list(chapter.children)
        for moved_section in moved:
            if moved_section.label in existing_labels:
                continue
            target_key = default_label_sort_key(moved_section.label)
            insert_at = len(chapter_children)
            for index, existing in enumerate(chapter_children):
                if existing.kind is not IRNodeKind.SECTION or existing.label is None:
                    continue
                if default_label_sort_key(existing.label) > target_key:
                    insert_at = index
                    break
            chapter_children.insert(insert_at, moved_section)
            if moved_section.label is not None:
                existing_labels.add(moved_section.label)
        children[chapter_index] = dc_replace(chapter, children=tuple(chapter_children))

    if wrapper_children:
        children[wrapper_index] = dc_replace(wrapper, children=tuple(wrapper_children))
    else:
        del children[wrapper_index]
    return dc_replace(materialized, children=tuple(children))


def _split_operatives_from_attachments_wrapper(materialized: IRNode, replay_fold: IRNode) -> IRNode:
    """Move misplaced operative sections out of a direct attachments wrapper.

    Finland AKN often represents all top-level legal provisions inside an
    unlabeled ``hcontainer``.  In a malformed PIT product, core timeline
    materialization can restore fold-owned direct sections into the direct
    ``name="attachments"`` hcontainer because unlabeled hcontainer paths do not
    carry attrs.  Split only direct section children whose labels are witnessed
    by the replay fold's provisions wrapper; actual appendix children remain in
    ``attachments``.
    """
    if materialized.kind is not IRNodeKind.BODY:
        return materialized

    attachments_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if child.kind is IRNodeKind.HCONTAINER and child.attrs.get("name") == "attachments"
        ),
        None,
    )
    if attachments_index is None:
        return materialized
    attachments = materialized.children[attachments_index]

    fold_labels = {section.label for section in _fold_hcontainer_direct_sections(replay_fold) if section.label}
    if not fold_labels:
        return materialized
    if _fold_provisions_has_hierarchical_roots(replay_fold):
        return materialized

    labels_outside_attachments = {
        node.label
        for index, sibling in enumerate(materialized.children)
        if index != attachments_index
        for node in _iter_sections(sibling)
        if node.label
    }

    moved: list[IRNode] = []
    kept: list[IRNode] = []
    for child in attachments.children:
        if (
            child.kind is IRNodeKind.SECTION
            and child.label in fold_labels
            and child.label not in labels_outside_attachments
        ):
            moved.append(child)
        else:
            kept.append(child)
    if not moved:
        return materialized

    provisions_index = next(
        (
            index
            for index, child in enumerate(materialized.children)
            if child.kind is IRNodeKind.HCONTAINER and child.attrs.get("name") == _FI_PROVISIONS_WRAPPER_NAME
        ),
        None,
    )
    if provisions_index is None:
        provisions = IRNode(
            kind=IRNodeKind.HCONTAINER,
            attrs={"name": _FI_PROVISIONS_WRAPPER_NAME},
            children=tuple(moved),
        )
    else:
        existing = materialized.children[provisions_index]
        existing_labels = {
            child.label for child in existing.children if child.kind is IRNodeKind.SECTION and child.label
        }
        provisions = dc_replace(
            existing,
            children=existing.children
            + tuple(child for child in moved if child.label not in existing_labels),
        )

    repaired_attachments = dc_replace(attachments, children=tuple(kept))
    rebuilt: list[IRNode] = []
    if provisions_index is None:
        for index, child in enumerate(materialized.children):
            if index == attachments_index:
                rebuilt.append(provisions)
                rebuilt.append(repaired_attachments)
            else:
                rebuilt.append(child)
    else:
        for index, child in enumerate(materialized.children):
            if index == provisions_index:
                rebuilt.append(provisions)
            elif index == attachments_index:
                rebuilt.append(repaired_attachments)
            else:
                rebuilt.append(child)
    return dc_replace(materialized, children=tuple(rebuilt))


def _all_section_paths(tree: IRNode, label: str) -> list[tuple[tuple[str, str], ...]]:
    """Return all section paths using the same root-relative format as ``find()``."""
    paths: list[tuple[tuple[str, str], ...]] = []

    def _walk(node: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        for child in node.children:
            child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
            if child.kind is IRNodeKind.SECTION and child.label == label:
                paths.append(child_path)
            _walk(child, child_path)

    _walk(tree, ())
    return paths


def _reconcile_materialized_fold_hcontainer_sections(
    materialized: IRNode,
    replay_fold: IRNode,
) -> IRNode:
    """Restore fold-owned hcontainer-direct sections lost during PIT export.

    Timeline materialization can flatten the provisions wrapper and/or misplace
    orphan sections under inferred chapters.  When replay fold keeps a section
    as a direct child of the provisions hcontainer, export must preserve that
    editorial placement instead of hoisting it beside parts or rebinding it to
    a chapter container.
    """
    if materialized.kind is not replay_fold.kind:
        return materialized

    direct_fold_sections = _fold_hcontainer_direct_sections(replay_fold)
    if not direct_fold_sections:
        return materialized

    result = _split_operatives_from_attachments_wrapper(materialized, replay_fold)
    fold_has_hierarchical_roots = _fold_provisions_has_hierarchical_roots(replay_fold)
    synthesized_parent_paths: set[tuple[tuple[str, str], ...]] = set()
    if fold_has_hierarchical_roots:
        for fold_section in direct_fold_sections:
            label = fold_section.label or ""
            if not label:
                continue
            for section_path in _all_section_paths(result, label):
                parent_path = section_path[:-1]
                parent_node = _tops_resolve(result, parent_path) if parent_path else result
                if (
                    parent_node is not None
                    and parent_node.attrs.get("lawvm_synthesized_container") == "active_descendant"
                ):
                    synthesized_parent_paths.add(parent_path)
    allowed_synthesized_parent_paths = (
        synthesized_parent_paths if len(synthesized_parent_paths) == 1 else set()
    )
    for fold_section in direct_fold_sections:
        label = fold_section.label or ""
        section_paths = _all_section_paths(result, label)
        if not section_paths:
            continue

        hcontainer_paths: list[tuple[tuple[str, str], ...]] = []
        misplaced_paths: list[tuple[tuple[str, str], ...]] = []
        for section_path in section_paths:
            parent_path = section_path[:-1]
            parent_node = _tops_resolve(result, parent_path) if parent_path else result
            if parent_node is not None and parent_node.kind is IRNodeKind.HCONTAINER:
                hcontainer_paths.append(section_path)
            elif (
                parent_path in allowed_synthesized_parent_paths
                and parent_node is not None
                and parent_node.attrs.get("lawvm_synthesized_container") == "active_descendant"
            ):
                # Core PIT synthesis creates this ancestor because the active
                # timeline address requires it.  Treating the child as a
                # misplaced fold-wrapper section would destroy the materialized
                # legal address and move the text to the end of the body.
                continue
            else:
                misplaced_paths.append(section_path)

        if not misplaced_paths:
            continue

        canonical_node = _tops_resolve(result, misplaced_paths[0])
        if canonical_node is None:
            continue

        for misplaced_path in reversed(misplaced_paths):
            result = _remove_at(result, misplaced_path)

        if hcontainer_paths:
            continue

        result, hcontainer_path = _ensure_body_hcontainer(result)
        result = _insert_sorted(result, hcontainer_path, canonical_node)

    return result


def _should_restore_repeal_placeholder(node: IRNode) -> bool:
    """Return whether a replay-only placeholder is visible in FI export.

    Repealed whole provisions stay absent in the materialized state. The visible
    dotted-text convention is currently owned only for child slots inside a live
    provision, where Finlex preserves the numbering gap.
    """
    return node.kind is IRNodeKind.SUBSECTION and _content_is_repeal_placeholder(node)


def _restore_replay_fold_repeal_placeholders(materialized: IRNode, replay_fold: IRNode) -> IRNode:
    """Carry replay-owned dotted-text placeholders through PIT export.

    Core materialization treats tombstones as absence. Finland's official
    consolidation export profile intentionally keeps repeal placeholders as
    visible dotted-text slots. This pass is Finland-local and copies only nodes
    that replay already marked as repeal placeholders.
    """
    if materialized.kind is not replay_fold.kind or materialized.label != replay_fold.label:
        return materialized
    if not replay_fold.children:
        return materialized

    replay_children = replay_fold.children
    if (
        materialized.kind is IRNodeKind.BODY
        and len(replay_children) == 1
        and replay_children[0].kind is IRNodeKind.HCONTAINER
        and replay_children[0].attrs.get("name") == "statuteProvisionsWrapper"
    ):
        replay_children = replay_children[0].children

    def _insert_missing_placeholder(children: list[IRNode], placeholder: IRNode) -> None:
        target_key = default_label_sort_key(placeholder.label)
        insert_at: int | None = None
        last_same_kind: int | None = None
        for index, child in enumerate(children):
            if child.kind is not placeholder.kind or child.label is None:
                continue
            last_same_kind = index
            if default_label_sort_key(child.label) > target_key:
                insert_at = index
                break
        if insert_at is None and last_same_kind is not None:
            insert_at = last_same_kind + 1
        if insert_at is None:
            children.append(placeholder)
            return
        children.insert(insert_at, placeholder)

    source_by_key: dict[tuple[IRNodeKind, str], IRNode] = {}
    for child in replay_children:
        if child.label is None:
            continue
        source_by_key.setdefault((child.kind, child.label), child)

    changed = False
    new_children: list[IRNode] = []
    existing_keys: set[tuple[IRNodeKind, str]] = set()
    for child in materialized.children:
        new_child = child
        if child.label is not None:
            key = (child.kind, child.label)
            existing_keys.add(key)
            source_child = source_by_key.get(key)
            if source_child is not None:
                new_child = _restore_replay_fold_repeal_placeholders(child, source_child)
                changed = changed or new_child is not child
        new_children.append(new_child)

    for child in replay_children:
        if child.label is None or not _should_restore_repeal_placeholder(child):
            continue
        key = (child.kind, child.label)
        if key in existing_keys:
            continue
        _insert_missing_placeholder(new_children, child)
        existing_keys.add(key)
        changed = True

    if not changed:
        return materialized
    return IRNode(
        kind=materialized.kind,
        label=materialized.label,
        text=materialized.text,
        attrs=dict(materialized.attrs),
        children=tuple(new_children),
    )


def _temporal_events_from_lo_ops(
    lo_ops: list[LegalOperation],
    *,
    target_statute: str,
    covered_commence_group_ids: frozenset[str] = frozenset(),
    covered_expiry_signatures: frozenset[tuple[str, str, str]] = frozenset(),
) -> tuple[TemporalEvent, ...]:
    """Project replay ops into explicit temporal authority for timeline mode.

    Finland replay still carries bounded fallback synthesis for replay-owned
    structural groups whose executable temporal authority has not yet been
    emitted earlier in the pipeline. Frontend-supplied temporal events remain
    authoritative; this shim only preserves existing replay behavior while the
    producer path finishes migrating fully onto explicit carriers.
    """
    events: list[TemporalEvent] = []
    seen_group_ids: set[str] = set()
    seen_expiry_keys: set[tuple[str, str, str]] = set()
    for op in lo_ops:
        group_id = str(getattr(op, "group_id", "") or "")
        if not group_id:
            continue
        source = getattr(op, "source", None)
        if source is None:
            continue
        effective_from = str(getattr(source, "effective", "") or "")
        if (
            effective_from
            and group_id not in seen_group_ids
            and group_id not in covered_commence_group_ids
        ):
            seen_group_ids.add(group_id)
            scope = TemporalScope(target_statute=target_statute)
            events.append(
                TemporalEvent(
                    event_id=f"fi-temporal:{group_id}:commence",
                    kind="commence",
                    scope=scope,
                    effective=effective_from,
                    source=source,
                    activation_rule=ActivationRule(
                        kind=FIXED_DATE_KIND,
                        effective_date=effective_from,
                        raw_text=str(getattr(source, "raw_text", "") or ""),
                    ),
                    group_id=group_id,
                )
            )
        expires = str(getattr(source, "expires", "") or "")
        if not expires:
            continue
        target_address = getattr(op, "target", None)
        target_key = str(target_address) if target_address is not None else ""
        expiry_key = (group_id, target_key, expires)
        if expiry_key in seen_expiry_keys:
            continue
        if expiry_key in covered_expiry_signatures:
            continue
        seen_expiry_keys.add(expiry_key)
        expire_scope = TemporalScope(
            target_statute=target_statute,
            exact_addresses=(target_address,) if target_address is not None else (),
        )
        events.append(
            TemporalEvent(
                event_id=f"fi-temporal:{group_id}:expire:{target_key or 'target'}",
                kind="expire",
                scope=expire_scope,
                expires=expires,
                source=source,
                group_id=group_id,
            )
        )
    return tuple(events)


def _merge_temporal_events(
    existing: tuple[TemporalEvent, ...],
    synthesized: tuple[TemporalEvent, ...],
) -> tuple[TemporalEvent, ...]:
    """Merge temporal events without dropping pre-existing executable carriers."""
    merged = list(existing)

    def _signature(event: TemporalEvent) -> tuple[object, ...]:
        if event.kind == "expire":
            exact_addresses = tuple(
                str(address)
                for address in getattr(event.scope, "exact_addresses", ()) or ()
            )
            return (
                event.kind,
                event.group_id,
                event.expires,
                exact_addresses,
            )
        return (
            event.kind,
            event.group_id,
        )

    seen = {_signature(event) for event in merged}
    for event in synthesized:
        signature = _signature(event)
        if signature in seen:
            continue
        merged.append(event)
        seen.add(signature)
    return tuple(merged)


def _normalize_repeal_op_sources(lo_ops: list[LegalOperation]) -> list[LegalOperation]:
    """Keep repeal placeholders/tombstones from inheriting a temporary expiry.

    Whole-section repeal semantics should remain visible after the repeal date.
    If we keep the source expiry on a tombstone-like op, PIT materialization can
    fall back to the pre-repeal permanent version once the temporary horizon
    passes. That revives text that should stay suppressed.

    This normalization is intentionally narrow: only explicit repeal ops and
    ops that already carry a repeal placeholder payload lose their source
    expiry. Other temporary amendments still keep their sunset behavior.
    """
    normalized: list[LegalOperation] = []
    for op in lo_ops:
        payload = getattr(op, "payload", None)
        is_repeal_placeholder = bool(
            payload is not None and getattr(payload, "attrs", {}).get("lawvm_repeal_placeholder") == "1"
        )
        if (
            op.source is not None
            and op.source.expires
            and (op.action is StructuralAction.REPEAL or is_repeal_placeholder)
        ):
            normalized_payload = op.payload
            if (
                normalized_payload is not None
                and is_repeal_placeholder
                and op.source.expires == op.source.effective
            ):
                normalized_payload = IRNode(
                    kind=normalized_payload.kind,
                    label=normalized_payload.label,
                    text=normalized_payload.text,
                    attrs={
                        **dict(normalized_payload.attrs),
                        _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                    },
                    children=normalized_payload.children,
                )
            normalized.append(
                dc_replace(
                    op,
                    payload=normalized_payload,
                    source=dc_replace(op.source, expires=""),
                )
            )
            continue
        normalized.append(op)
    return normalized


def _drop_explicitly_repealed_source_move_events(
    timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
) -> tuple[MigrationEvent, ...]:
    """Drop ``move`` events whose source slot is already repealed by the same act.

    A section relocated into a newly created sibling chapter (for example
    ``5 luku §41`` moved under a freshly inserted ``5 a luku`` by the same
    amendment) is expressed by replay as two explicit lowered ops: a repeal of
    the section at its old chapter address and an insert of the section at the
    new chapter address. That repeal terminates the old-address timeline in a
    tombstone, so materialization correctly drops the base content there.

    The same amendment also records a ``move`` migration event for lineage. If
    that move event is allowed to rekey timelines, it relocates the entire
    old-address bucket — tombstone included — onto the destination address,
    where it collides with the destination's own insert lineage and, fatally,
    leaves the old chapter slot with no tombstone. The base content then
    survives as an orphan copy in the old chapter.

    When the old-address timeline already carries a tombstone authored by the
    same source statute as the move, the relocation is fully expressed by the
    explicit repeal/insert ops; keeping the move event for rekey is redundant
    and destructive. Drop it (lineage consumers still see the event elsewhere).
    Genuine cross-parent moves with no explicit source repeal keep their event.
    """
    if not migration_events:
        return migration_events

    def _source_repealed_by(event: MigrationEvent) -> bool:
        if event.kind != "move":
            return False
        source_timeline = timelines.get(event.from_address)
        if source_timeline is None:
            return False
        move_source_statute = (
            event.source_statute if isinstance(event.source_statute, str) else ""
        )
        if not move_source_statute:
            return False
        return any(
            version.content is None
            and version.source is not None
            and version.source.statute_id == move_source_statute
            for version in source_timeline.versions
        )

    filtered = tuple(
        event for event in migration_events if not _source_repealed_by(event)
    )
    return filtered if len(filtered) != len(migration_events) else migration_events


def _rekey_timelines_with_migration_events(
    timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> dict["LegalAddress", ProvisionTimeline]:
    """Project Finland timelines onto migrated addresses for the requested PIT.

    Finland replay emits historical snapshots at the address valid when the
    amendment was applied. For PIT materialization, later container renumber
    waves can move those snapshots onto a different current address. Shared
    core timelines do not yet consume migration events directly, so Finland
    rekeys its replay-owned timelines here before materialization.
    """
    from lawvm.core.timeline import _address_prefix_matches
    from lawvm.finland.migration_ledger import current_address_with_prefix_migrations_from_events

    migration_events = _drop_explicitly_repealed_source_move_events(
        timelines, migration_events
    )

    return _core_rekey_timelines_with_migration_events(
        timelines,
        migration_events,
        as_of_date=as_of,
        current_address_with_prefix_migrations_fn=current_address_with_prefix_migrations_from_events,
        address_prefix_matches=_address_prefix_matches,
        retarget_version_content_fn=lambda version, address: _retarget_version_content(
            version,
            address,
            root_num_text_fn=_fi_root_num_text,
        ),
        merge_bucket_cleanup_fn=_cleanup_sourceless_base_merge_conflicts,
    )


def _cleanup_sourceless_base_merge_conflicts(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Prune replay-bucket collisions between base snapshots and newer lineage.

    This is a temporary Finland-local cleanup policy. Some rekeyed buckets can
    contain a source-less base snapshot plus later lineage versions that are
    not semantically additive. Until core owns a better non-textual rule for
    that identity/materialization family, Finland keeps the base snapshot and
    only the later versions that clearly extend beyond the base wording span.

    The rule name is stable on purpose:
    `_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE`.
    """
    if not any(existing_version.source is None for existing_version in versions):
        return versions

    def _title_prefix_len(node: IRNode | None) -> int:
        if node is None:
            return 0
        text = irnode_to_text(node)
        prefix = text.split(" Tässä", 1)[0]
        return len(prefix)

    base_title_lengths = [
        _title_prefix_len(existing_version.content)
        for existing_version in versions
        if existing_version.source is None and existing_version.content is not None
    ]
    if not base_title_lengths:
        return versions
    base_effective = max(
        existing_version.effective
        for existing_version in versions
        if existing_version.source is None
    )
    base_title_len = max(base_title_lengths)
    cleaned = [
        existing_version
        for existing_version in versions
        if existing_version.source is None
        or (
            existing_version.content is not None
            and (
                existing_version.effective > base_effective
                or _title_prefix_len(existing_version.content) > base_title_len
            )
        )
    ]
    return _dedupe_same_source_semantic_versions(cleaned)


def _timeline_version_semantic_text_key(node: IRNode | None) -> str:
    if node is None:
        return ""
    text = " ".join(irnode_to_text(node).split())
    return _TIMELINE_SECTION_MARK_SPACING_RE.sub(r"\1 §", text)


def _dedupe_same_source_semantic_versions(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Collapse same-source timeline duplicates created by lineage projection.

    A whole-container replacement can emit a child snapshot while a migration
    event for the same source/effective date retargets the old child lineage to
    that same address. If the resulting texts are semantically identical, keep
    one version so PIT selection has a single source-backed state transition.
    """
    deduped: list[ProvisionVersion] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id or version.content is None:
            deduped.append(version)
            continue
        key = (
            source_id,
            version.effective,
            version.enacted,
            version.expires,
            version.variant_kind,
            tuple(version.applicability),
            _timeline_version_semantic_text_key(version.content),
        )
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(version)
            continue
        deduped[existing_index] = version
    return deduped


def _classify_finland_lineage_bridge(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> FinlandLineageBridgeClassification:
    from lawvm.core.timeline import _address_prefix_matches

    return classify_materialization_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of_date=as_of,
        address_prefix_matches=_address_prefix_matches,
    )


def _select_pit_lineage_inputs(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    rekeyed_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
    bridge_classification: FinlandLineageBridgeClassification | None = None,
) -> MaterializationLineageDecision:
    """Choose the canonical PIT lineage inputs for Finland replay products.

    Native rebirth must outrank the scope-changing migration fallback. Once a
    same-label native provision is born on the renumber date, replay products
    need the rekeyed split lineage and must stop forwarding the migration
    events into PIT materialization for that case. Otherwise the old lineage
    and the reborn native lineage compete across two authority surfaces:
    Finland's rekey shim and core migration materialization.
    """
    classification = bridge_classification or _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    return choose_materialization_lineage_decision(
        raw_timelines=raw_timelines,
        rekeyed_timelines=rekeyed_timelines,
        migration_events=migration_events,
        native_rebirth_after_renumber=classification.native_rebirth_after_renumber,
        leaf_stable_scope_renumber=classification.leaf_stable_scope_renumber,
        noncolliding_scope_migrations=classification.noncolliding_scope_migrations,
        destination_occupancy_collision=classification.destination_occupancy_collision,
        scope_changing_migration_fallback=classification.active_scope_changing,
        default_reason=_FI_LINEAGE_REASON_DEFAULT,
        native_rebirth_reason=_FI_LINEAGE_REASON_NATIVE_REBIRTH,
        leaf_stable_reason=_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER,
        destination_occupancy_reason=_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY,
        scope_changing_fallback_reason=_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK,
    )


def build_replay_products(
    *,
    ctx: "StatuteContext",
    statute_id: str,
    replay_fold_state: "ReplayState",
    lo_ops_out: Optional[list[LegalOperation]],
    source_adjudication: Optional[SourceAdjudication] = None,
    as_of: str = "9999-12-31",
    query_type: Literal["governing", "in_force"] = "governing",
    synthesize_repeal_placeholders: bool = False,
    repeal_placeholder_normalizer: Optional[Callable[[object], object]] = None,
    build_full_products: bool = True,
    temporal_events: tuple[TemporalEvent, ...] = (),
    strict_johto_temporal: bool = True,
    migration_events: tuple[MigrationEvent, ...] = (),
    expires_as_of: str = "",
) -> ReplayProducts:
    """Build typed PIT materialization artifacts from a replay fold state.

    Callers must perform explicit temporal lowering before calling this
    function. Use ``lawvm.core.effect_lowering.lower_effect_intents_to_temporal_events``
    to convert parse-layer ``EffectIntent`` objects into executable
    ``TemporalEvent`` instances and pass the result as ``temporal_events``.

    Finland replay/materialization prefers explicit ``TemporalEvent`` carriers,
    but replay products still preserve a bounded fallback synthesis from
    replay-owned structural ops until the producer path is fully migrated.
    """
    resolved_temporal_events = tuple(temporal_events)
    if not build_full_products:
        return ReplayProducts(
            replay_fold_state=replay_fold_state,
            materialized_state=replay_fold_state,
            timelines=None,
            temporal_events=resolved_temporal_events,
            migration_events=migration_events,
            materialization_spec=None,
            source_adjudication=source_adjudication,
        )

    from lawvm.core.timeline import compile_timelines, materialize_pit
    import lxml.etree as _etree
    from lawvm.finland.metadata import _statute_issue_date as _fi_statute_issue_date

    base_ir = IRStatute(
        statute_id=statute_id,
        title=ctx.title,
        body=ctx.base_ir,
    )
    lo_ops = list(lo_ops_out or [])
    lo_ops = _normalize_repeal_op_sources(lo_ops)
    covered_commence_group_ids = frozenset(
        group_id
        for event in resolved_temporal_events
        if event.kind == "commence"
        and isinstance((group_id := getattr(event, "group_id", "")), str)
        and group_id
    )
    covered_expiry_signatures = frozenset(
        (
            str(getattr(event, "group_id", "") or ""),
            str(next(iter(getattr(event.scope, "exact_addresses", ()) or ()), "") or ""),
            str(getattr(event, "expires", "") or ""),
        )
        for event in resolved_temporal_events
        if event.kind == "expire"
        and isinstance(getattr(event, "group_id", ""), str)
        and getattr(event, "group_id", "")
        and getattr(event, "expires", "")
    )
    synthesized_temporal_events = _temporal_events_from_lo_ops(
        lo_ops,
        target_statute=base_ir.statute_id,
        covered_commence_group_ids=covered_commence_group_ids,
        covered_expiry_signatures=covered_expiry_signatures,
    )
    if synthesized_temporal_events:
        resolved_temporal_events = _merge_temporal_events(
            resolved_temporal_events,
            synthesized_temporal_events,
        )
    # Extract the base statute's issue date (FRBR dateIssued / signature date) so
    # that compile_timelines can set the correct `enacted` date on base provisions.
    # This fixes --query-type in_force for pre-enactment as_of dates: the
    # `eligible()` check (enacted <= as_of) correctly excludes base provisions
    # when as_of < statute issue date.  The `effective` date of base provisions
    # remains "0000-00-00" so --query-type governing is completely unaffected
    # (governing only checks v.effective, not v.enacted).
    _base_tree = _etree.fromstring(ctx.base_xml_bytes)
    _base_issue_date = _fi_statute_issue_date(_base_tree)
    _base_enacted_date: str = _base_issue_date.isoformat() if _base_issue_date is not None else ""
    from lawvm.finland.replay_fold_timeline_backfill import append_fold_timeline_backfill_ops

    fold_timeline_backfills = append_fold_timeline_backfill_ops(
        lo_ops=lo_ops,
        replay_fold_ir=replay_fold_state.ir,
        base_ir=ctx.base_ir,
        base_statute_id=statute_id,
        base_title=ctx.title,
        migration_events=migration_events,
        as_of=as_of,
        temporal_events=resolved_temporal_events,
        base_enacted_date=_base_enacted_date,
    )
    if fold_timeline_backfills.records:
        backfill_temporal_events = _temporal_events_from_lo_ops(
            lo_ops,
            target_statute=base_ir.statute_id,
            covered_commence_group_ids=covered_commence_group_ids,
            covered_expiry_signatures=covered_expiry_signatures,
        )
        if backfill_temporal_events:
            resolved_temporal_events = _merge_temporal_events(
                resolved_temporal_events,
                backfill_temporal_events,
            )
    _assert_finland_timeline_safe_ops(lo_ops)
    if fold_timeline_backfills.records:
        raw_timelines = compile_timelines(
            base_ir,
            lo_ops,
            base_enacted_date=_base_enacted_date,
            label_norm=fi_label_norm,
            temporal_events=resolved_temporal_events,
        )
        timelines = _rekey_timelines_with_migration_events(
            raw_timelines,
            migration_events,
            as_of=as_of,
        )
    else:
        raw_timelines = fold_timeline_backfills.raw_timelines
        timelines = fold_timeline_backfills.rekeyed_timelines
    from lawvm.finland.timeline_version_dedupe import dedupe_finland_timelines

    timelines, timeline_version_dedupes = dedupe_finland_timelines(timelines)
    bridge_classification = _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        timelines,
        migration_events,
        as_of=as_of,
        bridge_classification=bridge_classification,
    )
    pit = materialize_pit(
        lineage_decision.timelines,
        as_of=as_of,
        base=base_ir,
        query_type=query_type,
        label_norm=fi_label_norm,
        expires_as_of=expires_as_of,
        lineage_plan=lineage_decision.lineage_plan,
    )
    materialized_state = replay_fold_state.with_ir(pit.body)
    materialized_state = materialized_state.with_ir(
        _strip_redundant_paragraph_label_prefixes_ir(
            _strip_standalone_subsection_item_prefixes_ir(materialized_state.ir)
        )
    )
    if synthesize_repeal_placeholders:
        materialized_state = materialized_state.with_ir(
            _restore_replay_fold_repeal_placeholders(materialized_state.ir, replay_fold_state.ir)
        )
    materialized_state = materialized_state.with_ir(
        _reconcile_materialized_fold_hcontainer_sections(
            materialized_state.ir,
            replay_fold_state.ir,
        )
    )
    # Sort labeled children back into canonical order.  PIT materialization can
    # produce out-of-order siblings (e.g. paragraphs within a subsection) for
    # the same reason the replay fold can — amendment ops insert at arbitrary
    # positions and materialize_pit preserves that order.
    materialized_state = materialized_state.with_ir(
        _resort_children(materialized_state.ir)
    )
    if synthesize_repeal_placeholders and repeal_placeholder_normalizer is not None:
        materialized_state = materialized_state.with_ir(
            cast(IRNode, repeal_placeholder_normalizer(materialized_state.ir))
        )

    return ReplayProducts(
        replay_fold_state=replay_fold_state,
        materialized_state=materialized_state,
        timelines=timelines,
        temporal_events=resolved_temporal_events,
        migration_events=migration_events,
        fold_timeline_backfills=fold_timeline_backfills.records,
        timeline_version_dedupes=timeline_version_dedupes,
        materialization_spec=MaterializationSpec(
            as_of=as_of,
            query_type=query_type,
            label_normalizer=_FI_LABEL_NORMALIZER_NAME,
            bridge_classification=bridge_classification,
            lineage_plan=lineage_decision.lineage_plan,
            lineage_reason=cast(
                Literal[
                    "default_migration_projection",
                    "native_rebirth_after_renumber",
                    "leaf_stable_scope_renumber",
                    "destination_occupancy_collision",
                    "scope_changing_migration_fallback",
                ],
                lineage_decision.reason,
            ),
        ),
        source_adjudication=source_adjudication,
    )


def validate_replay_products(
    ctx: "StatuteContext",
    products: ReplayProducts,
    *,
    deep_materialization_check: bool = False,
) -> list[str]:
    """Return replay/materialization product invariant violations."""
    violations: list[str] = []

    if products.timelines is None and products.materialization_spec is not None:
        violations.append("materialization_spec_without_timelines")
    if products.timelines is not None and products.materialization_spec is None:
        violations.append("timelines_without_materialization_spec")

    if products.replay_fold_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"replay_fold_not_body:{products.replay_fold_state.ir.kind}")
    if products.materialized_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"materialized_not_body:{products.materialized_state.ir.kind}")

    for violation in check_invariants(products.replay_fold_state.ir):
        violations.append(f"replay_fold_tree:{violation}")
    for violation in check_invariants(products.materialized_state.ir):
        violations.append(f"materialized_tree:{violation}")
    violations.extend(
        fi_product_tree_invariant_messages(
            products.replay_fold_state.ir,
            _FI_REPLAY_FOLD_MIXED_HIERARCHY_PROFILE,
        )
    )
    violations.extend(
        fi_product_tree_invariant_messages(
            products.materialized_state.ir,
            _FI_MATERIALIZED_MIXED_HIERARCHY_PROFILE,
        )
    )
    violations.extend(
        _fi_label_collision_invariant_messages(
            products.replay_fold_state.ir,
            surface="replay_fold_tree",
        )
    )
    violations.extend(
        _fi_label_collision_invariant_messages(
            products.materialized_state.ir,
            surface="materialized_tree",
        )
    )

    # Check for temporary_unresolved versions — these represent VÄLIAIKAINEN
    # amendments with no parseable expiry date and are a product-level degradation
    # signal worth surfacing to callers.
    if products.timelines is not None:
        for tl in products.timelines.values():
            for ver in tl.versions:
                if ver.variant_kind == "temporary_unresolved":
                    violations.append("temporal_unresolved_temporary_expiry")
                    break
            else:
                continue
            break

    if deep_materialization_check and products.timelines is not None:
        from lawvm.core.timeline import materialize_pit

        base_ir = IRStatute(
            statute_id=ctx.id,
            title=ctx.title,
            body=ctx.base_ir,
        )
        spec = products.materialization_spec
        if spec is None:
            violations.append("deep_materialization_check_without_spec")
        elif spec.lineage_plan.mode == _FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS:
            # Finland exposes current-address timelines after replay-owned
            # migrations are projected. Re-materializing from those already-
            # rekeyed timelines would double-apply scope-changing move
            # semantics and drift from the canonical PIT path, which instead
            # materializes from raw lineage plus explicit migration events.
            pass
        else:
            remat = materialize_pit(
                products.timelines,
                as_of=spec.as_of,
                base=base_ir,
                query_type=spec.query_type,
                label_norm=fi_label_norm,
                lineage_plan=spec.lineage_plan,
            )
            remat = dc_replace(remat, body=_resort_children(remat.body))
            lhs = irnode_to_text(remat.body)
            rhs = irnode_to_text(products.materialized_state.ir)
            if lhs != rhs:
                violations.append("materialized_state_drift_from_timelines")

    return violations


__all__ = [
    "MaterializationSpec",
    "ReplayProducts",
    "build_replay_products",
    "validate_replay_products",
    "fi_label_norm",
    "fi_slot_identity_norm",
    "_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR",
]
