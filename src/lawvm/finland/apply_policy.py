"""Path-resolution and occupancy-policy helpers for Finland apply flows."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path
from lawvm.core.ir import LegalAddress
from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    OccupancyClass,
    validate_transition,
)

from lawvm.finland.ops import (
    AmendmentOp,
    ResolvedOp,
    SectionPathResolution,
    SectionPathResolutionReason,
    runtime_scope_confidence_for_op,
)
from lawvm.finland.apply_runtime_support import _valid_target_path_hint

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState
    from lawvm.core.canonical_intent import CanonicalIntent

logger = logging.getLogger(__name__)

_OP_TYPE_TO_ACTION: dict[str, str] = {
    "REPLACE": "replace",
    "INSERT": "insert",
    "REPEAL": "repeal",
}


def _chapter_from_section_path(path: Path) -> str | None:
    return next((lbl for kind, lbl in path if kind == "chapter"), None)


def _part_from_section_path(path: Path) -> str | None:
    return next((lbl for kind, lbl in path if kind == "part"), None)


def _resolve_explicit_chapter_unique_global_fallback(
    *,
    global_path: Path,
    global_chapter: str | None,
    target_chapter: str,
    global_part: str | None,
    target_part: str | None,
    ctx_label: str,
    is_special_insert: bool,
) -> tuple[Path | None, str | None]:
    if is_special_insert and global_chapter is None:
        logger.debug(
            "  %s → rejected root-level unique global fallback for chapter-scoped special insert",
            ctx_label,
        )
        return None, None
    if global_chapter == target_chapter:
        if target_part is not None and global_part != target_part:
            logger.debug(
                "  %s → rejected unique global fallback across part boundary (%s != %s)",
                ctx_label,
                global_part,
                target_part,
            )
            return None, None
        logger.debug("  %s → chapter fallback (unique global)", ctx_label)
        return global_path, "inferred_from_live_unique"
    if global_chapter is None:
        logger.debug(
            "  %s → rejected root-level unique global fallback for explicit chapter scope",
            ctx_label,
        )
        return None, None
    logger.debug(
        "  %s → rejected unique global fallback across chapter boundary (%s != %s)",
        ctx_label,
        global_chapter,
        target_chapter,
    )
    return None, None


def _resolve_carried_chapter_unique_global_fallback(
    *,
    global_path: Path,
    global_chapter: str | None,
    target_chapter: str,
    global_part: str | None,
    target_part: str | None,
    ctx_label: str,
    is_special_insert: bool,
    move_clause_target_unit_kind: str | None,
) -> tuple[Path | None, str | None]:
    if global_chapter == target_chapter:
        if target_part is not None and global_part != target_part:
            logger.debug(
                "  %s → rejected unique global fallback across part boundary (%s != %s)",
                ctx_label,
                global_part,
                target_part,
            )
            return None, None
        if move_clause_target_unit_kind is None:
            logger.debug("  %s → chapter fallback (unique global)", ctx_label)
        else:
            logger.debug("  %s → move-tail source fallback (unique global)", ctx_label)
        return global_path, "inferred_from_live_unique"
    if global_chapter is None:
        logger.debug(
            "  %s → rejected root-level unique global fallback for carried chapter scope",
            ctx_label,
        )
        return None, None
    logger.debug(
        "  %s → rejected unique global fallback across chapter boundary (%s != %s)",
        ctx_label,
        global_chapter,
        target_chapter,
    )
    return None, None


def _section_occupancy(state: "ReplayState", sec_path: Path | None) -> OccupancyClass:
    """Determine the current occupancy class of a section slot."""
    if sec_path is None:
        return OccupancyClass.ABSENT
    node = _tops.resolve(state.ir, sec_path)
    if node is None:
        return OccupancyClass.ABSENT
    if node.attrs.get("lawvm_repeal_placeholder") == "1":
        return OccupancyClass.TOMBSTONE
    return OccupancyClass.SUBSTANTIVE


def _observe_occupancy_transition(
    op: AmendmentOp,
    sec_path: Path | None,
    state: "ReplayState",
    ctx_label: str,
) -> None:
    """Observational occupancy check: log a warning for invalid transitions."""
    action = _OP_TYPE_TO_ACTION.get(op.op_type or "")
    if action is None:
        return
    if op.target_unit_kind != "section" or op.target_paragraph or op.target_item:
        return

    current = _section_occupancy(state, sec_path)
    try:
        validate_transition(OccupancyAction(action), current)
    except InvalidOccupancyTransition as exc:
        logger.debug(
            "  %s → occupancy violation: §%s is %s but action is %r — %s",
            ctx_label,
            op.target_section,
            current.value,
            action,
            exc,
        )


def _resolve_unscoped_placeholder_shadowed_by_unique_substantive(
    state: "ReplayState",
    target_norm: str,
) -> tuple[Path | None, str | None]:
    label_norm = _tops._norm(target_norm)
    matches = [
        _tops._as_path(path)
        for path in state.provision_index.get(("section", label_norm), [])
    ]
    if len(matches) < 2:
        return None, None

    substantive_paths: list[Path] = []
    for path in matches:
        node = _tops.resolve(state.ir, path)
        if node is None:
            continue
        if node.attrs.get("lawvm_repeal_placeholder") == "1":
            continue
        substantive_paths.append(path)

    if len(substantive_paths) != 1:
        return None, None
    return substantive_paths[0], "live_unique_substantive_over_placeholder"


def _resolve_section_path_with_fallbacks(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir,
    path_hint: Path | None,
    ctx_label: str,
    migration_ledger=None,
) -> SectionPathResolution:
    """Find section path in state using all fallback strategies.

    Returns:
        Typed resolution result. ``reason_code`` is populated when resolution
        fell back to a live-unique match after the scoped lookup failed.
    """
    del muutos_ir
    target_norm, _target_chapter, _target_part = rop.resolved_section_lookup_scope
    _target_section = rop.resolved_target_label
    _move_clause_target_unit_kind = rop.move_clause_target_unit_kind
    sec_path = _valid_target_path_hint(
        state,
        target_unit_kind=rop.target_unit_kind,
        target_norm=target_norm,
        target_chapter=_target_chapter,
        target_part=_target_part,
        path_hint=path_hint,
    )
    if sec_path is None:
        sec_path = state.find_section_path(target_norm, _target_chapter, _target_part)
    sec_path = _tops._as_path(sec_path) if sec_path is not None else None

    # Same-wave relabels can make later old-address section/subsection ops
    # resolvable only through the migration ledger. Use that exact lineage
    # evidence rather than widening lookup globally.
    target_address = rop.resolved_target_address
    allows_insert_descendant_follow = (
        rop.resolved_action_type == "INSERT"
        and target_address is not None
        and any(kind in {"subsection", "item"} for kind, _label in target_address.path)
    )
    if (
        sec_path is None
        and migration_ledger is not None
        and target_address is not None
        and (rop.resolved_action_type != "INSERT" or allows_insert_descendant_follow)
    ):
        migrated = migration_ledger.current_address_with_prefix_migrations(target_address)
        if migrated != target_address:
            migrated_labels = {kind: label for kind, label in migrated.path}
            migrated_section = migrated_labels.get("section")
            migrated_chapter = migrated_labels.get("chapter")
            migrated_part = migrated_labels.get("part")
            if migrated_section:
                migrated_path = state.find_section_path(
                    migrated_section,
                    migrated_chapter,
                    migrated_part,
                )
                migrated_path = _tops._as_path(migrated_path) if migrated_path is not None else None
                if migrated_path is not None:
                    logger.debug(
                        "  %s → same-wave migration follow (%s -> %s)",
                        ctx_label,
                        target_address,
                        LegalAddress(path=migrated_path),
                    )
                    return SectionPathResolution(
                        path=migrated_path,
                        reason_code="follow_same_wave_migration",
                    )

    if (
        sec_path is not None
        and not _target_chapter
        and rop.targets_whole_unit("section")
    ):
        sec_node = _tops.resolve(state.ir, sec_path)
        if sec_node is not None and sec_node.attrs.get("lawvm_repeal_placeholder") == "1":
            substantive_path, fallback_reason = _resolve_unscoped_placeholder_shadowed_by_unique_substantive(
                state,
                target_norm,
            )
            if substantive_path is not None:
                logger.debug(
                    "  %s → unscoped section fallback prefers unique substantive over repeal placeholder",
                    ctx_label,
                )
                return SectionPathResolution(
                    path=substantive_path,
                    reason_code=cast(SectionPathResolutionReason, fallback_reason),
                )

    # Pattern E guard: when an UNCOVERED BODY RECOVERY op has no chapter
    # context but the section label is ambiguous (exists in multiple chapters),
    # the un-scoped lookup resolves to an arbitrary chapter.  Reject the path
    # to prevent applying one chapter's content to another chapter's
    # identically-numbered section.  PEG-compiled ops are exempt because they
    # are typically correct even without chapter context (the johtolause
    # usually targets the main section).
    _is_uncovered = rop.uses_uncovered_body_recovery
    if (
        sec_path is not None
        and not _target_chapter
        and _target_section
        and _is_uncovered
        and _target_section in state.duplicate_section_labels
    ):
        logger.debug(
            "  %s → rejecting ambiguous un-scoped uncovered-body section %s (duplicate across chapters)",
            ctx_label,
            target_norm,
        )
        sec_path = None

    allow_unique_global_fallback = (
        rop.resolved_action_type != "INSERT"
        or rop.effective_target_special in {"otsikko", "otsikko_edella", "johd"}
        or (
            rop.resolved_action_type == "INSERT"
            and target_address is not None
            and any(kind in {"subsection", "item"} for kind, _label in target_address.path)
        )
    )
    scope_confidence = runtime_scope_confidence_for_op(rop)
    scope_is_explicit = scope_confidence is None or scope_confidence.is_explicit
    if sec_path is None and _target_chapter and allow_unique_global_fallback:
        _idx = state.provision_index
        global_path = _tops.find(state.ir, "section", target_norm, label_index=_idx)
        if global_path is not None:
            label_norm = _tops._norm(target_norm)
            n_matches = len(_idx.get(("section", label_norm), []))
            if n_matches == 1:
                global_path = _tops._as_path(global_path)
                global_chapter = _chapter_from_section_path(global_path)
                global_part = _part_from_section_path(global_path)
                is_special_insert = (
                    rop.resolved_action_type == "INSERT"
                    and rop.effective_target_special in {"otsikko", "otsikko_edella", "johd"}
                )
                is_descendant_insert = (
                    rop.resolved_action_type == "INSERT"
                    and target_address is not None
                    and any(kind in {"subsection", "item"} for kind, _label in target_address.path)
                )
                if is_descendant_insert and (_target_part is None or global_part == _target_part):
                    return SectionPathResolution(
                        path=global_path,
                        reason_code="live_unique_global_fallback",
                    )
                # Cross-chapter and root-level fallbacks are deferred to the
                # move+replace mechanism in _apply_whole_section_op.  Returning
                # a path here would cause the section to be modified in-place at
                # the wrong location instead of being properly moved/created in
                # the target chapter.
                if scope_is_explicit:
                    sec_path, scope_reason = _resolve_explicit_chapter_unique_global_fallback(
                        global_path=global_path,
                        global_chapter=global_chapter,
                        target_chapter=_target_chapter,
                        global_part=global_part,
                        target_part=_target_part,
                        ctx_label=ctx_label,
                        is_special_insert=is_special_insert,
                    )
                else:
                    sec_path, scope_reason = _resolve_carried_chapter_unique_global_fallback(
                        global_path=global_path,
                        global_chapter=global_chapter,
                        target_chapter=_target_chapter,
                        global_part=global_part,
                        target_part=_target_part,
                        ctx_label=ctx_label,
                        is_special_insert=is_special_insert,
                        move_clause_target_unit_kind=_move_clause_target_unit_kind,
                    )
                return SectionPathResolution(
                    path=sec_path,
                    reason_code=(
                        "live_unique_global_fallback"
                        if scope_reason == "inferred_from_live_unique"
                        else None
                    ),
                )

    return SectionPathResolution(path=sec_path)


def _check_occupancy_policy(
    state: "ReplayState",
    rop: ResolvedOp,
    intent: "CanonicalIntent",
    sec_path: Path | None,
    ctx_label: str,
) -> None:
    """Observational occupancy policy check against the typed contract."""
    from lawvm.core.canonical_intent import Replace, Insert, Repeal, NodeTarget

    match intent:
        case (
            Replace(target=NodeTarget(address=addr))
            | Insert(target=NodeTarget(address=addr))
            | Repeal(target=NodeTarget(address=addr))
        ):
            if addr.leaf_kind() != "section":
                return
        case _:
            return

    current = _section_occupancy(state, sec_path)
    policy = intent.contract.occupancy
    if current not in policy.allowed_from:
        logger.warning(
            "  %s → occupancy policy violation: §%s is %s, not in allowed_from %s",
            ctx_label,
            rop.target_norm,
            current.value,
            {c.value for c in policy.allowed_from},
        )
    elif current not in policy.primary_expected_from:
        logger.debug(
            "  %s → occupancy policy note: §%s is %s (allowed but not primary expected)",
            ctx_label,
            rop.target_norm,
            current.value,
        )
