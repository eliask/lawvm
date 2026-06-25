"""Path-resolution and occupancy-policy helpers for Finland apply flows."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path, normalized_label_key
from lawvm.core.ir import LegalAddress
from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    OccupancyClass,
    validate_transition,
)
from lawvm.core.phase_result import Finding
from lawvm.core.resolver_binding import (
    RUNG_MIGRATION_LEDGER_FOLLOW,
    RUNG_PATH_HINT_VALIDATED,
    RUNG_PLACEHOLDER_SHADOW_FALLBACK,
    RUNG_SCOPED_FIND,
    RUNG_UNCOVERED_BODY_AMBIGUITY,
    RUNG_UNIQUE_GLOBAL_FALLBACK,
    WIDENING_RUNG_IDS,
    ResolverBinding,
    binding_id_for,
)

from lawvm.finland.ops import (
    AmendmentOp,
    ContainerPathResolution,
    ResolvedOp,
    SectionPathResolution,
    SectionPathResolutionReason,
    runtime_scope_confidence_for_op,
)
from lawvm.finland.apply_runtime_support import _valid_target_path_hint
from lawvm.finland.migration_ledger import migration_lower_bound_for_op
from lawvm.finland.replay_notices import replay_verbose_enabled

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
    if op.target_cols.target_unit_kind != "section" or op.target_cols.target_paragraph or op.target_cols.target_item:
        return

    current = _section_occupancy(state, sec_path)
    try:
        validate_transition(OccupancyAction(action), current)
    except InvalidOccupancyTransition as exc:
        logger.debug(
            "  %s → occupancy violation: §%s is %s but action is %r — %s",
            ctx_label,
            op.target_cols.target_section,
            current.value,
            action,
            exc,
        )


def _resolve_unscoped_placeholder_shadowed_by_unique_substantive(
    state: "ReplayState",
    target_norm: str,
) -> tuple[Path | None, SectionPathResolutionReason | None]:
    label_norm = normalized_label_key(target_norm)
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
    return substantive_paths[0], SectionPathResolutionReason.LIVE_UNIQUE_SUBSTANTIVE_OVER_PLACEHOLDER


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
    lookup_scope = rop.resolved_section_lookup_scope_view
    target_norm = lookup_scope.target_norm
    _target_chapter = lookup_scope.target_chapter
    _target_part = lookup_scope.target_part
    _target_section = rop.resolved_target_label
    _move_clause_target_unit_kind = rop.move_clause_target_unit_kind
    taken_rung: str | None = None
    sec_path = _valid_target_path_hint(
        state,
        target_unit_kind=rop.target_unit_kind,
        target_norm=lookup_scope.target_norm,
        target_chapter=lookup_scope.target_chapter,
        target_part=lookup_scope.target_part,
        path_hint=path_hint,
    )
    if sec_path is not None:
        taken_rung = RUNG_PATH_HINT_VALIDATED
    else:
        sec_path = state.find_section_path(
            lookup_scope.target_norm,
            lookup_scope.target_chapter,
            lookup_scope.target_part,
        )
        if sec_path is not None:
            taken_rung = RUNG_SCOPED_FIND
    sec_path = _tops._as_path(sec_path) if sec_path is not None else None

    # Same-wave relabels can make later old-address section/subsection ops
    # resolvable only through the migration ledger. Use that exact lineage
    # evidence rather than widening lookup globally.
    target_address = rop.resolved_target_address
    if (
        sec_path is None
        and migration_ledger is not None
        and target_address is not None
        and same_wave_migration_follow_is_allowed(rop)
    ):
        op_effective = migration_lower_bound_for_op(rop)
        migrated = migration_ledger.current_address_with_prefix_migrations(
            target_address, not_before=op_effective
        )
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
                        reason_code=SectionPathResolutionReason.FOLLOW_SAME_WAVE_MIGRATION,
                        rung_id=RUNG_MIGRATION_LEDGER_FOLLOW,
                    )

    target_address_has_descendant = target_address is not None and any(
        kind in {"subsection", "item"} for kind, _label in target_address.path
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
                    reason_code=fallback_reason,
                    rung_id=RUNG_PLACEHOLDER_SHADOW_FALLBACK,
                    global_candidate_count=len(
                        state.provision_index.get(
                            ("section", normalized_label_key(target_norm)), []
                        )
                    ),
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
        taken_rung = RUNG_UNCOVERED_BODY_AMBIGUITY

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
    if sec_path is None and not _target_chapter and target_address_has_descendant:
        _idx = state.provision_index
        global_path = _tops.find(state.ir, "section", target_norm, label_index=_idx)
        if global_path is not None:
            label_norm = normalized_label_key(target_norm)
            n_matches = len(_idx.get(("section", label_norm), []))
            if n_matches == 1:
                return SectionPathResolution(
                    path=_tops._as_path(global_path),
                    reason_code=SectionPathResolutionReason.LIVE_UNIQUE_GLOBAL_FALLBACK,
                    rung_id=RUNG_UNIQUE_GLOBAL_FALLBACK,
                    global_candidate_count=n_matches,
                )
    if sec_path is None and _target_chapter and allow_unique_global_fallback:
        _idx = state.provision_index
        global_path = _tops.find(state.ir, "section", target_norm, label_index=_idx)
        if global_path is not None:
            label_norm = normalized_label_key(target_norm)
            n_matches = len(_idx.get(("section", label_norm), []))
            if n_matches == 1:
                global_path = _tops._as_path(global_path)
                global_chapter = _chapter_from_section_path(global_path)
                global_part = _part_from_section_path(global_path)
                is_special_insert = (
                    rop.resolved_action_type == "INSERT"
                    and rop.effective_target_special in {"otsikko", "otsikko_edella", "johd"}
                )
                is_descendant_insert = rop.resolved_action_type == "INSERT" and target_address_has_descendant
                if is_descendant_insert and (_target_part is None or global_part == _target_part):
                    return SectionPathResolution(
                        path=global_path,
                        reason_code=SectionPathResolutionReason.LIVE_UNIQUE_GLOBAL_FALLBACK,
                        rung_id=RUNG_UNIQUE_GLOBAL_FALLBACK,
                        global_candidate_count=n_matches,
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
                        SectionPathResolutionReason.LIVE_UNIQUE_GLOBAL_FALLBACK
                        if scope_reason == "inferred_from_live_unique"
                        else None
                    ),
                    rung_id=RUNG_UNIQUE_GLOBAL_FALLBACK if sec_path is not None else taken_rung,
                    global_candidate_count=n_matches,
                )

    return SectionPathResolution(path=sec_path, rung_id=taken_rung)


SECTION_LADDER_POLICY_ID = "fi.section_ladder.v0"


def same_wave_migration_follow_is_allowed(rop: ResolvedOp) -> bool:
    """Whether a later op may follow a same-wave relabel migration.

    Whole-section INSERTs are the counterexample: in phrases like
    ``lisätään uusi 24 e §, jolloin nykyinen 24 e § siirtyy 24 f §:ksi``,
    the relabel explicitly vacates ``24e`` so the inserted section can occupy
    that old label. Following the migration would insert the new payload at the
    destination and overwrite/mislabel the moved old section. Descendant INSERTs
    may still follow the moved section because they amend the continuing
    provision rather than claim the vacated whole-section slot.

    Replacement ops that have already been explicitly rebased from a same-wave
    renumber source to its destination are another counterexample: following the
    migration again targets the provision that moved away from that destination.
    """
    if any(
        tag in rop.target_guessing_provenance_tags
        for tag in ("rebase_duplicate_target_shifted_replace", "rebase_replaced_renumber_source")
    ):
        return False
    if rop.resolved_action_type != "INSERT":
        return True
    target_address = rop.resolved_target_address
    return target_address is not None and any(
        kind in {"subsection", "item"} for kind, _label in target_address.path
    )


def section_resolver_binding(
    rop: ResolvedOp,
    resolution: SectionPathResolution,
    ctx_label: str,
) -> ResolverBinding:
    """Project the section ladder's outcome into a passive ResolverBinding.

    Binding provenance only: the binding records which rung produced the
    path the legacy ladder returned; it does not (yet) drive the write.
    Promotion to the binding-as-authority step happens per the apply
    contract's vertical rollout, not here.
    """
    target_address = rop.resolved_target_address
    target_text = (
        str(target_address) if target_address is not None else (rop.target_norm or "")
    )
    rung_id = resolution.rung_id
    if resolution.path is not None:
        status = "resolved"
    elif rung_id == RUNG_UNCOVERED_BODY_AMBIGUITY:
        status = "ambiguous"
    else:
        status = "not_found"
    widening = resolution.path is not None and rung_id in WIDENING_RUNG_IDS
    fallback_rule_id = (
        (resolution.reason_code or rung_id) if widening else None
    )
    return ResolverBinding(
        binding_id=binding_id_for(
            op_label=ctx_label,
            target_text=target_text,
            rung_id=rung_id,
            target_path=resolution.path,
        ),
        op_label=ctx_label,
        target_text=target_text,
        target_path=resolution.path,
        status=status,
        policy_id=SECTION_LADDER_POLICY_ID,
        rung_id=rung_id,
        candidate_count=resolution.global_candidate_count,
        fallback_used=widening,
        fallback_rule_id=fallback_rule_id,
        rejection_reasons=(
            ("duplicate_section_label_across_chapters",)
            if rung_id == RUNG_UNCOVERED_BODY_AMBIGUITY
            else ()
        ),
    )


CONTAINER_TARGET_POLICY_ID = "fi.container_target.v0"


def container_resolver_binding(
    *,
    kind: str,
    label: str,
    target_part: str | None,
    resolution: ContainerPathResolution,
    ctx_label: str,
) -> ResolverBinding:
    """Project a container (chapter/part) target resolution into its binding.

    Unlike the section ladder's passive projection, the container family
    CONSUMES this binding: ``_apply_container_op`` takes its target path from
    ``binding.target_path`` (apply contract §3 step 3). The container ladder
    has a single scoped-find rung — there is no widening fallback: a declared
    part scope either resolves within that part or the binding is not_found.
    """
    scope_prefix = f"part:{target_part}/" if target_part else ""
    target_text = f"{scope_prefix}{kind}:{label}"
    rung_id = resolution.rung_id
    status = "resolved" if resolution.path is not None else "not_found"
    return ResolverBinding(
        binding_id=binding_id_for(
            op_label=ctx_label,
            target_text=target_text,
            rung_id=rung_id,
            target_path=resolution.path,
        ),
        op_label=ctx_label,
        target_text=target_text,
        target_path=resolution.path,
        status=status,
        policy_id=CONTAINER_TARGET_POLICY_ID,
        rung_id=rung_id,
        candidate_count=resolution.candidate_count,
        fallback_used=False,
        fallback_rule_id=None,
    )
def _move_rider_origin_path(
    state: "ReplayState",
    rop: ResolvedOp,
) -> Path | None:
    """Resolve the unique move ORIGIN slot for a destination-scoped move rider.

    A johtolause move rider ("29 e §, joka samalla siirretään 5 b lukuun")
    resolves the target scope to the DESTINATION chapter/part at parse time,
    so the destination slot is legitimately absent before the move lands.
    The slot the op consumes is the ORIGIN: the unique live same-label
    section in a different chapter (or part). Mirrors the candidate
    selection of the section move+replace recovery in
    ``_apply_whole_section_op`` (recovery rule
    ``section_move_replace_destination_rebind``).
    """
    lookup_scope = rop.resolved_section_lookup_scope_view
    if not lookup_scope.target_norm:
        return None
    label_norm = normalized_label_key(lookup_scope.target_norm)
    matches = [
        _tops._as_path(path)
        for path in state.provision_index.get(("section", label_norm), [])
    ]
    if len(matches) != 1:
        return None
    origin = matches[0]
    origin_chapter = _chapter_from_section_path(origin)
    origin_part = _part_from_section_path(origin)
    if rop.move_clause_target_unit_kind == "chapter":
        if not lookup_scope.target_chapter or origin_chapter == lookup_scope.target_chapter:
            return None
    elif rop.move_clause_target_unit_kind == "part":
        if not lookup_scope.target_part or origin_part == lookup_scope.target_part:
            return None
    else:
        return None
    return origin


def _occupant_installer_effective(
    replay_history_ops,
    target_address: LegalAddress | None,
) -> tuple[str, str] | None:
    """Return (effective, source_statute) of the latest prior write to the slot.

    Scans the fold-order replay history for the most recent INSERT/REPLACE
    LegalOperation whose target is the same exact slot. This is the typed
    evidence for the staggered twin-law family: the document-order fold can
    place a LATER-commencing occupant in the slot before an earlier-window
    temporary insert is applied.
    """
    if not replay_history_ops or target_address is None:
        return None
    target_key = tuple(
        (kind, normalized_label_key(label)) for kind, label in target_address.path
    )
    if not target_key or target_key[-1][0] != "section":
        return None
    for lo in reversed(replay_history_ops):
        action = getattr(lo.action, "value", lo.action)
        if action not in ("insert", "replace"):
            continue
        if lo.target is None or not lo.target.path:
            continue
        if lo.target.special is not None:
            continue
        lo_key = tuple(
            (kind, normalized_label_key(label)) for kind, label in lo.target.path
        )
        if lo_key != target_key:
            continue
        source = lo.source
        if source is None or not source.effective:
            return None
        return source.effective, source.statute_id
    return None


def _replace_installs_base_frame_section(rop: ResolvedOp) -> bool:
    """True when a whole-section REPLACE legitimately installs into an empty base frame.

    Historical codes (e.g. 1734/4-000, 1868/31-000) carry sparse base text:
    a section the amendment REPLACE-targets simply does not exist in the base
    IR yet, so the slot resolves ABSENT and the apply turns the REPLACE into a
    create that installs the carried section body. That is the intended outcome
    for these codes, not a contradicted occupancy precondition — so it is not an
    occupancy violation. Recognised by: a whole-section REPLACE whose payload is
    a substantive section IR (carries more than a heading/num shell). A REPLACE
    that resolves ABSENT with no substantive payload (a genuine dropped-create)
    is NOT gated here and remains a violation.
    """
    from lawvm.core.semantic_types import IRNodeKind

    if rop.resolved_action_type != "REPLACE":
        return False
    if not rop.targets_whole_unit("section"):
        return False
    if (
        rop.effective_target_paragraph is not None
        or rop.effective_target_item_label is not None
        or rop.effective_target_special is not None
    ):
        return False
    muutos_ir = rop.muutos_ir
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return False
    return any(
        child.kind not in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}
        for child in muutos_ir.children
    )


def _check_occupancy_policy(
    state: "ReplayState",
    rop: ResolvedOp,
    intent: "CanonicalIntent",
    sec_path: Path | None,
    ctx_label: str,
    *,
    findings_out: list[Finding] | None = None,
    replay_history_ops=None,
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
    if (
        current is OccupancyClass.ABSENT
        and sec_path is None
        and isinstance(intent, Replace)
        and rop.resolved_action_type == "REPLACE"
        and rop.move_clause_target_unit_kind in ("chapter", "part")
    ):
        # Typed move-rider lane: the REPLACE targets the move DESTINATION
        # (absent by definition until the move lands); the slot it consumes
        # is the unique live origin. Evaluate occupancy against the origin
        # so a legitimate move arrival is not reported as REPLACE-on-absent.
        origin_path = _move_rider_origin_path(state, rop)
        if origin_path is not None:
            current = _section_occupancy(state, origin_path)
            logger.debug(
                "  %s → occupancy evaluated at move-rider origin %s (rule: section_move_replace_destination_rebind)",
                ctx_label,
                LegalAddress(path=origin_path),
            )
    if (
        current is OccupancyClass.ABSENT
        and sec_path is None
        and isinstance(intent, Replace)
        and _replace_installs_base_frame_section(rop)
    ):
        # Base-frame-empty install lane: a whole-section REPLACE whose target
        # slot never existed in the (sparse) base frame and which the apply
        # turns into a create that installs the carried section body. The slot
        # is legitimately absent before the op, so the same_slot_replace
        # SUBSTANTIVE precondition is not contradicted — recording it as an
        # occupancy violation is a false positive on historical codes.
        logger.debug(
            "  %s → occupancy skipped: base-frame-empty whole-section REPLACE install "
            "(rule: replace_installs_base_frame_section)",
            ctx_label,
        )
        return
    policy = intent.contract.occupancy
    if (
        current is OccupancyClass.SUBSTANTIVE
        and isinstance(intent, Insert)
        and rop.resolved_action_type == "INSERT"
        and current not in policy.allowed_from
    ):
        # Typed staggered-twin lane: a temporary gap-filler INSERT
        # ("lisätään väliaikaisesti uusi X §", in force until D) whose slot
        # is occupied in the document-order fold by a deferred-commencement
        # twin that only enters force ON or AFTER the gap-filler expires
        # ("X § tulee kuitenkin voimaan vasta ..."). The two occupancies are
        # disjoint in legal time; the collision exists only in fold order.
        # incoming.expires is the kernel's EXCLUSIVE cutoff (first day NOT in
        # force), so the canonical hand-off — temporary law valid through
        # June 30 (expires == 2023-07-01), permanent twin effective July 1 —
        # gives expires == occupant_effective and is disjoint: hence <=.
        incoming = rop.resolved_op_source
        occupant = _occupant_installer_effective(
            replay_history_ops, rop.resolved_target_address
        )
        if (
            incoming is not None
            and incoming.effective
            and incoming.expires
            and occupant is not None
            and (
                (
                    incoming.effective < occupant[0]
                    and incoming.expires <= occupant[0]
                )
                or (
                    incoming.effective == occupant[0]
                    and incoming.expires > incoming.effective
                )
            )
        ):
            occupant_effective, occupant_statute = occupant
            rule_id = (
                "temporally_bounded_overlay_insert"
                if incoming.effective == occupant_effective
                else "temporally_disjoint_twin_insert"
            )
            logger.debug(
                "  %s → %s: window %s..%s coexists with "
                "occupant %s effective %s",
                ctx_label,
                rule_id,
                incoming.effective,
                incoming.expires,
                occupant_statute,
                occupant_effective,
            )
            if findings_out is not None:
                findings_out.append(
                    Finding(
                        kind="APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT",
                        role="observation",
                        stage="apply",
                        source_statute=rop.resolved_source_statute,
                        detail={
                            "ctx_label": ctx_label,
                            "op_id": rop.op_id,
                            "legacy_action": rop.resolved_action_type,
                            "target_label": rop.target_norm,
                            "incoming_effective": incoming.effective,
                            "incoming_expires": incoming.expires,
                            "occupant_effective": occupant_effective,
                            "occupant_source_statute": occupant_statute,
                            "rule_id": rule_id,
                        },
                        blocking=False,
                    )
                )
            return
    if current not in policy.allowed_from:
        allowed_from = sorted(c.value for c in policy.allowed_from)
        if replay_verbose_enabled():
            logger.warning(
                "  %s → occupancy policy violation: §%s is %s, not in allowed_from %s",
                ctx_label,
                rop.target_norm,
                current.value,
                set(allowed_from),
            )
        if findings_out is not None:
            findings_out.append(
                Finding(
                    kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
                    role="observation",
                    stage="apply",
                    source_statute=rop.resolved_source_statute,
                    detail={
                        "ctx_label": ctx_label,
                        "op_id": rop.op_id,
                        "legacy_action": rop.resolved_action_type,
                        "target_label": rop.target_norm,
                        "current_occupancy": current.value,
                        "allowed_from": allowed_from,
                        "primary_expected_from": sorted(c.value for c in policy.primary_expected_from),
                        "strict_disposition": "record",
                    },
                    blocking=False,
                )
            )
    elif current not in policy.primary_expected_from:
        # Allowed but non-primary: e.g. a REPLACE landing on a tombstone
        # (the legitimate reenactment lane) or an INSERT/REPEAL touching a
        # slot that is not in its primary expected class. Recorded as an
        # observation for triage; it is not a rejection.
        logger.debug(
            "  %s → occupancy policy note: §%s is %s (allowed but not primary expected)",
            ctx_label,
            rop.target_norm,
            current.value,
        )
        if findings_out is not None:
            findings_out.append(
                Finding(
                    kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
                    role="observation",
                    stage="apply",
                    source_statute=rop.resolved_source_statute,
                    detail={
                        "ctx_label": ctx_label,
                        "op_id": rop.op_id,
                        "legacy_action": rop.resolved_action_type,
                        "target_label": rop.target_norm,
                        "current_occupancy": current.value,
                        "allowed_from": sorted(c.value for c in policy.allowed_from),
                        "primary_expected_from": sorted(
                            c.value for c in policy.primary_expected_from
                        ),
                        "strict_disposition": "record",
                        "allowed_non_primary": True,
                    },
                    blocking=False,
                )
            )
