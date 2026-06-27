"""Typed Finland replay-apply executor.

This module owns the resolved-op apply orchestration for one amendment.  The
boundary is still ``ApplyOpsRequest``/``ApplyOpsSinks``; ``grafter.py`` re-exports
``_apply_ops_to_tree_typed`` while callers migrate.
"""
from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.amendment_chapter_precreate import (
    FI_CHAPTER_MEMBERSHIP_MIGRATION_RULE_ID,
)
from lawvm.finland.apply_group_replay import (
    ApplyGroupSnapshotRequest as _ApplyGroupSnapshotRequest,
    ApplyGroupSnapshotSinks as _ApplyGroupSnapshotSinks,
    RefreshGroupPathHintRequest as _RefreshGroupPathHintRequest,
    emit_apply_group_snapshot_if_allowed as _emit_apply_group_snapshot_if_allowed,
    refresh_group_path_hint as _refresh_group_path_hint,
)
from lawvm.finland.apply_loop_state import ApplyGroupState
from lawvm.finland.apply_ops_boundary import ApplyOpsRequest, ApplyOpsSinks
from lawvm.finland.apply_resolved_op import (
    ApplyResolvedOpRequest,
    ApplyResolvedOpSinks,
    apply_resolved_op_with_audit,
)
from lawvm.finland.apply_runtime_support import _resolved_destination_path_for_rop
from lawvm.finland.apply_supplemental_recovery import (
    ApplySupplementalRecoveryRequest,
    ApplySupplementalRecoverySinks,
    run_apply_supplemental_recovery,
)
from lawvm.finland.relabel_identity import (
    stabilize_same_parent_relabel_order as _stabilize_same_parent_relabel_order,
)
from lawvm.finland.restructure_plan import (
    OwnedRelabelSignature,
    resolved_op_is_owned_by_restructure_plan as _resolved_op_is_owned_by_restructure_plan,
    restructure_plan_owned_renumber_signatures as _restructure_plan_owned_renumber_signatures,
)
from lawvm.finland.restructure_plan_replay import (
    ExecuteRestructurePlanRequest as _ExecuteRestructurePlanRequest,
    ExecuteRestructurePlanSinks as _ExecuteRestructurePlanSinks,
    execute_restructure_plan_with_evidence as _execute_restructure_plan_with_evidence,
    source_destination_relabel_snapshot_payload as _source_destination_relabel_snapshot_payload,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.standalone_targets import (
    build_standalone_section_targets as _build_standalone_section_targets,
)
from lawvm.finland.target_selector_facades import replace_target
from lawvm.finland.helpers import _norm_num_token

if TYPE_CHECKING:
    from lawvm.core.tree_ops import Path
    from lawvm.finland.ops import ResolvedOp
    from lawvm.finland.statute import ReplayState


_SAME_WAVE_SHIFTED_SUBSECTION_REPEAL_RULE_ID = "same_wave_shifted_subsection_repeal_target"


def _replace_is_owned_by_restructure_destination_payload(
    rop: "ResolvedOp",
    owned_relabels: set[OwnedRelabelSignature],
    source_model: AmendmentSourceModel,
) -> bool:
    """True when a same-wave relabel snapshot owns a whole-section replacement.

    Finnish clauses may say that a changed source section moves to a destination
    label while a new section occupies the vacated source label. The body then
    prints the changed continuing provision under the destination label. In
    that case the ordinary ``REPLACE source`` op must not also follow the same
    wave migration and write its source-label payload at the destination.
    """
    if not rop.is_replace_action or not rop.targets_whole_unit("section"):
        return False
    source_address = rop.resolved_target_address
    if source_address is None:
        return False
    source_path = tuple(source_address.path)
    for owned_source, owned_destination in owned_relabels:
        if len(source_path) < len(owned_source) or source_path[-len(owned_source) :] != owned_source:
            continue
        if owned_source == owned_destination:
            continue
        destination_path = owned_destination
        if len(owned_destination) < len(source_path):
            destination_path = source_path[: -len(owned_destination)] + owned_destination
        payload = _source_destination_relabel_snapshot_payload(
            source_model,
            destination_path,
        )
        if payload is not None:
            return True
    return False


def _replace_last_subsection_label(path: "Path", new_label: str) -> "Path":
    replaced = False
    steps: list[tuple[str, str]] = []
    for kind, label in reversed(path):
        if not replaced and kind == "subsection":
            steps.append((kind, new_label))
            replaced = True
            continue
        steps.append((kind, label))
    return tuple(reversed(steps))


def _retarget_same_wave_shifted_subsection_repeals(
    resolved: list["ResolvedOp"],
    *,
    amendment_id: str,
    findings_out: list[Finding] | None,
) -> list["ResolvedOp"]:
    """Retarget repeals that name the pre-insert subsection label.

    Finnish drafting can say: add a new 2 mom, current 2 mom becomes 3 mom,
    and current 3 mom is repealed. Runtime insertion shifts the old 3 mom to 4,
    so the repeal must follow the source-time label through that same-wave
    shift. This is not a fallback to a convenient live node: it requires the
    insert, renumber, and repeal to all be present in the same target group.
    """
    by_group: dict[object, list["ResolvedOp"]] = {}
    for rop in resolved:
        by_group.setdefault(rop.resolved_group_key_view, []).append(rop)

    retarget_by_op_id: dict[str, tuple[str, str]] = {}
    for group_rops in by_group.values():
        inserted_labels = sorted(
            int(label)
            for rop in group_rops
            if rop.is_insert_action
            and rop.targets_subsection_only()
            and (label := _norm_num_token(rop.resolved_target_subsection_label or "")).isdigit()
        )
        if not inserted_labels:
            continue
        renumber_destination_labels: set[str] = set()
        for rop in group_rops:
            if not rop.is_renumber_action or not rop.targets_subsection_only():
                continue
            destination_path = _resolved_destination_path_for_rop(rop)
            if not destination_path or destination_path[-1][0] != "subsection":
                continue
            renumber_destination_labels.add(_norm_num_token(destination_path[-1][1]))
        if not renumber_destination_labels:
            continue
        for rop in group_rops:
            if not rop.is_repeal_action or not rop.targets_subsection_only():
                continue
            old_label = _norm_num_token(rop.resolved_target_subsection_label or "")
            if not old_label.isdigit() or old_label not in renumber_destination_labels:
                continue
            old_num = int(old_label)
            shift = sum(1 for insert_num in inserted_labels if insert_num <= old_num)
            if not shift:
                continue
            retarget_by_op_id[rop.op_id] = (old_label, str(old_num + shift))

    if not retarget_by_op_id:
        return resolved

    next_resolved: list["ResolvedOp"] = []
    for rop in resolved:
        retarget = retarget_by_op_id.get(rop.op_id)
        address = rop.resolved_target_address
        if retarget is None or address is None:
            next_resolved.append(rop)
            continue
        old_label, new_label = retarget
        target_path = _replace_last_subsection_label(address.path, new_label)
        op_target_paragraph = int(new_label) if new_label.isdigit() else rop.op.target_cols.target_paragraph
        next_resolved.append(
            dc_replace(
                rop,
                op=dc_replace(rop.op, **replace_target(rop.op, target_paragraph=op_target_paragraph)),
                _target_address_override=LegalAddress(path=target_path, special=address.special),
                scope_provenance_tags=tuple(
                    dict.fromkeys(
                        (
                            *rop.scope_provenance_tags,
                            _SAME_WAVE_SHIFTED_SUBSECTION_REPEAL_RULE_ID,
                        )
                    )
                ),
            )
        )
        if findings_out is not None:
            findings_out.append(
                Finding(
                    kind="APPLY.SAME_WAVE_SHIFTED_SUBSECTION_REPEAL_TARGET",
                    role="observation",
                    stage="replay_apply",
                    detail={
                        "rule_id": _SAME_WAVE_SHIFTED_SUBSECTION_REPEAL_RULE_ID,
                        "message": (
                            "Subsection repeal target retargeted from the source-time "
                            "label to the label produced by a same-amendment insert shift."
                        ),
                        "op_id": rop.op_id,
                        "old_subsection": old_label,
                        "retargeted_subsection": new_label,
                    },
                    source_statute=amendment_id,
                    blocking=False,
                )
            )
    return next_resolved


def _apply_ops_to_tree_typed(
    request: ApplyOpsRequest,
    sinks: ApplyOpsSinks,
) -> "ReplayState":
    """Step 6: Apply resolved operations to IR tree as a pure fold.

    Accepts immutable ``ctx`` and current ``state``.  Returns the updated
    ``ReplayState`` after applying all ops, uncovered-body recovery, and
    kumotaan heuristics.  The input ``state`` is never modified.

    ``ctx`` is used by ``apply_op`` to resolve base-IR queries
    (e.g. find_base_section for kumotaan placeholder decisions) and by the
    ``_apply_uncovered_*`` heuristics.
    """
    state = request.state
    ctx = request.ctx
    resolved = request.resolved
    ops = request.ops
    source_model = request.source_model
    johto = request.johto
    amendment_id = request.amendment_id
    source_title = request.source_title
    amendment_issue_date = request.amendment_issue_date
    amendment_effective_date = request.amendment_effective_date
    amendment_expiry_date = request.amendment_expiry_date
    replay_mode = request.replay_mode
    strict_profile = request.strict_profile
    _vts_ops_enrich_done = request.vts_ops_enrich_done
    future_repeals = request.future_repeals

    compiled_ops_out = sinks.compiled_ops_out
    lo_ops_out = sinks.lo_ops_out
    failed_ops_out = sinks.failed_ops_out
    source_pathologies_out = sinks.source_pathologies_out
    mutation_events_out = sinks.mutation_events_out
    migration_ledger = sinks.migration_ledger
    restructure_plans_out = sinks.restructure_plans_out
    observations_out = sinks.observations_out
    findings_out = sinks.findings_out
    observed_touch_results_out = sinks.observed_touch_results_out
    # write_receipts_out / write_audits_out are non-Optional on ApplyOpsSinks,
    # but legacy callers may still pass an explicit None; coerce to a concrete
    # accumulator so the apply path ALWAYS collects a conservation receipt per
    # landed write (the contract is "you always get the receipts", not "you may
    # opt out with None").
    write_receipts_out = sinks.write_receipts_out if sinks.write_receipts_out is not None else []
    write_audits_out = sinks.write_audits_out if sinks.write_audits_out is not None else []

    # Group-boundary bookkeeping for the resolved-op apply fold, threaded as one
    # typed state machine (ApplyGroupState) rather than four bare locals mutated
    # inline. The fold accumulates ops into same-target groups and emits one
    # timeline snapshot per group at each boundary; the typed object owns the
    # current group key, accumulated ops, live path hint, and the
    # failed-apply replay barrier, with explicit transition methods.
    grp = ApplyGroupState()

    # Pre-compute standalone section targets as (chapter, label) tuples for
    # container dedup/retention guards. When a section op was retargeted away
    # from a stale body chapter to the unique live chapter, also record the
    # original body chapter as an alias so chapter REPLACE payloads do not keep
    # the stale child shell around.
    _standalone_section_targets = _build_standalone_section_targets(ops)
    base_ir = ctx.base_ir

    # Stabilize same-parent RELABEL order: reverse forward chains so consumers
    # run before producers. Prevents both chapter chains like "10→11 then 11→12"
    # and section chains like "9→10, 10→11, 11→12" from consuming a label
    # created by a just-applied earlier relabel.
    resolved = _stabilize_same_parent_relabel_order(resolved)
    resolved = _retarget_same_wave_shifted_subsection_repeals(
        resolved,
        amendment_id=amendment_id,
        findings_out=findings_out,
    )

    active_restructure_plan = None
    if restructure_plans_out:
        for _rp in restructure_plans_out:
            if _rp.amendment_id == amendment_id and _rp.has_unexecuted_ops:
                active_restructure_plan = _rp
                break
    executed_restructure_plan_ids: set[str] = set()
    if active_restructure_plan is not None:
        # Restructure-plan ownership must be singular. When a relabel plan is
        # active for this amendment, the main resolved-op loop must not also
        # mutate the exact same relabel chain or emit stale old-address
        # snapshots. Descendant renumbers outside the plan stay on the ordinary
        # typed/apply path.
        owned_relabels = _restructure_plan_owned_renumber_signatures(active_restructure_plan)
        resolved = [
            rop
            for rop in resolved
            if not _resolved_op_is_owned_by_restructure_plan(rop, owned_relabels)
            and not _replace_is_owned_by_restructure_destination_payload(
                rop,
                owned_relabels,
                source_model,
            )
        ]
        # Execute the pre-seeded relabel plan before the ordinary resolved-op
        # fold. Large renumber waves like 2019/371 can move containers later in
        # the same amendment; if the plan waits until uncovered-body recovery,
        # its old-address section relabels chase a tree that has already moved.
        _restructure_result = _execute_restructure_plan_with_evidence(
            _ExecuteRestructurePlanRequest(
                state=state,
                plan=active_restructure_plan,
                amendment_id=amendment_id,
                source_title=source_title,
                amendment_issue_date=amendment_issue_date,
                amendment_effective_date=amendment_effective_date,
                migration_ledger=migration_ledger,
                log_label="early restructure_plan",
                source_model=source_model,
            ),
            _ExecuteRestructurePlanSinks(
                lo_ops_out=lo_ops_out,
                findings_out=findings_out,
            ),
        )
        state = _restructure_result.state
        if _restructure_result.executed:
            executed_restructure_plan_ids.add(active_restructure_plan.amendment_id)

    # Pre-create chapters introduced by the amendment body before the main
    # apply loop. Section INSERT ops can target both real new chapters and
    # pseudo-marker chapters in the same amendment, and both need their
    # chapter shell to exist before the section-level apply path runs.
    # Not run for VTS (cross-statute body) amendments.
    _precreate_chapters = source_model.precreate_apply_chapters(
        state=state,
        resolved=resolved,
        amendment_id=amendment_id,
        vts_ops_enrich_done=_vts_ops_enrich_done,
        johto=johto,
    )
    state = _precreate_chapters.state
    _pre_real_chapter_refs = _precreate_chapters.real_chapter_refs
    _pre_pseudo_chapter_refs = _precreate_chapters.pseudo_chapter_refs
    if _precreate_chapters.membership_migrations:
        effective = amendment_effective_date.isoformat() if amendment_effective_date is not None else ""
        for migration in _precreate_chapters.membership_migrations:
            if migration_ledger is not None:
                migration_ledger.record_move(
                    LegalAddress(path=migration.from_legal_path),
                    LegalAddress(path=migration.to_legal_path),
                    effective=effective,
                    source_statute=amendment_id,
                    witness={
                        "rule_id": FI_CHAPTER_MEMBERSHIP_MIGRATION_RULE_ID,
                        "section_label": migration.section_label,
                        "chapter_label": migration.chapter_label,
                    },
                )
            if findings_out is not None:
                findings_out.append(
                    Finding(
                        kind="APPLY.CHAPTER_MEMBERSHIP_MIGRATION",
                        role="observation",
                        stage="replay_apply",
                        detail={
                            "message": (
                                "Existing flat section moved into a source-declared "
                                "chapter introduced by this amendment."
                            ),
                            **migration.as_detail(),
                        },
                        source_statute=amendment_id,
                        blocking=False,
                    )
                )

    # Snapshot chapter-to-part mapping before the main apply loop.
    # Used after the loop to detect chapters that moved to a genuinely NEW part,
    # so we can emit tombstone+insert LO ops that keep the materialized PIT
    # consistent.  Only genuine part-creation moves are captured; part relabels
    # (where the old part label disappears) are excluded.
    _ch_to_part_before: dict[str, str] = {}
    _parts_before: set[str] = set()
    if lo_ops_out is not None:
        _pp_snap = _tops.find_provisions_parent(state.ir)
        _pp_snap_node = _tops.resolve(state.ir, _pp_snap) if _pp_snap else state.ir
        if _pp_snap_node is not None:
            for _snap_part in _pp_snap_node.children:
                if _snap_part.kind is IRNodeKind.PART and _snap_part.label:
                    _parts_before.add(_snap_part.label)
                    for _snap_ch in _snap_part.children:
                        if _snap_ch.kind is IRNodeKind.CHAPTER and _snap_ch.label:
                            _ch_to_part_before[_snap_ch.label] = _snap_part.label

    for rop in resolved:
        group_key = rop.resolved_group_key_view
        if grp.is_group_boundary(group_key):
            # Emit snapshot for previous group (if any). A failed apply in the
            # group is a replay barrier: its payload must not be promoted into
            # timeline/materialized state by the snapshot lane.
            _emit_apply_group_snapshot_if_allowed(
                _ApplyGroupSnapshotRequest(
                    state=state,
                    group=grp,
                    amendment_id=amendment_id,
                    source_title=source_title,
                    amendment_issue_date=amendment_issue_date,
                    amendment_effective_date=amendment_effective_date,
                    base_ir=base_ir,
                    migration_ledger=migration_ledger,
                    standalone_section_targets=_standalone_section_targets,
                ),
                _ApplyGroupSnapshotSinks(
                    lo_ops_out=lo_ops_out,
                    source_pathologies_out=source_pathologies_out,
                ),
            )
            grp.start_group(group_key)
        # Apply. One typed disposition per op records whether it was applied,
        # whether the apply failed, or whether no apply pass was required.
        _apply_result = apply_resolved_op_with_audit(
            ApplyResolvedOpRequest(
                state=state,
                ctx=ctx,
                rop=rop,
                amendment_id=amendment_id,
                replay_mode=replay_mode,
                path_hint=grp.group_path_hint,
                standalone_section_targets=_standalone_section_targets,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
            ),
            ApplyResolvedOpSinks(
                write_receipts_out=write_receipts_out,
                write_audits_out=write_audits_out,
                lo_ops_out=lo_ops_out,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                findings_out=findings_out,
                observed_touch_results_out=observed_touch_results_out,
            ),
        )
        state = _apply_result.state
        _disposition = _apply_result.disposition
        if observations_out is not None:
            observations_out.append(_apply_result.audit.to_observation())
        if _disposition == "APPLY_FAILED":
            grp.mark_failed_apply()
        if _disposition != "NO_APPLY_PASS":
            rop_group = rop.resolved_group_key_view
            grp.set_path_hint(
                _refresh_group_path_hint(
                    _RefreshGroupPathHintRequest(
                        state=state,
                        target_unit_kind=rop_group.unit_kind,
                        target_norm=rop_group.target_norm,
                        target_chapter=rop_group.target_chapter,
                        target_part=rop_group.target_part,
                        path_hint=grp.group_path_hint,
                        rop=rop,
                        migration_ledger=migration_ledger,
                    )
                )
            )
        grp.append_rop(rop, disposition=_disposition)

    # Emit snapshot for the last group
    _emit_apply_group_snapshot_if_allowed(
        _ApplyGroupSnapshotRequest(
            state=state,
            group=grp,
            amendment_id=amendment_id,
            source_title=source_title,
            amendment_issue_date=amendment_issue_date,
            amendment_effective_date=amendment_effective_date,
            base_ir=base_ir,
            migration_ledger=migration_ledger,
            standalone_section_targets=_standalone_section_targets,
        ),
        _ApplyGroupSnapshotSinks(
            lo_ops_out=lo_ops_out,
            source_pathologies_out=source_pathologies_out,
        ),
    )

    supplemental_result = run_apply_supplemental_recovery(
        ApplySupplementalRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=ops,
            source_model=source_model,
            johto=johto,
            amendment_id=amendment_id,
            source_title=source_title,
            amendment_issue_date=amendment_issue_date,
            amendment_effective_date=amendment_effective_date,
            amendment_expiry_date=amendment_expiry_date,
            replay_mode=replay_mode,
            strict_profile=strict_profile,
            vts_ops_enrich_done=_vts_ops_enrich_done,
            future_repeals=future_repeals,
            base_ir=base_ir,
            pre_real_chapter_refs=_pre_real_chapter_refs,
            pre_pseudo_chapter_refs=_pre_pseudo_chapter_refs,
            ch_to_part_before=_ch_to_part_before,
            parts_before=_parts_before,
            executed_restructure_plan_ids=executed_restructure_plan_ids,
            standalone_section_targets=_standalone_section_targets,
            migration_ledger=migration_ledger,
        ),
        ApplySupplementalRecoverySinks(
            compiled_ops_out=compiled_ops_out,
            lo_ops_out=lo_ops_out,
            failed_ops_out=failed_ops_out,
            source_pathologies_out=source_pathologies_out,
            mutation_events_out=mutation_events_out,
            restructure_plans_out=restructure_plans_out,
            observations_out=observations_out,
            findings_out=findings_out,
            observed_touch_results_out=observed_touch_results_out,
            write_audits_out=write_audits_out,
            write_receipts_out=write_receipts_out,
        ),
    )
    state = supplemental_result.state

    return state
