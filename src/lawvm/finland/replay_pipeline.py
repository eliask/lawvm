"""Explicit replay-plan stages for the Finnish frontend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional

from lawvm.corpus_store import CorpusStore
from lawvm.core.provenance import MigrationEvent
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.replay_contracts import ReplayCheckpoint, ReplayCheckpointCallback
from lawvm.core.tree_ops import resort_children as _resort_children

from lawvm.finland.statute import ReplayState, StatuteContext, _serialize_text_node as _serialize_text


@dataclass(frozen=True)
class ReplayPlan:
    """Typed plan for replaying one Finnish parent statute."""

    parent_id: str
    replay_mode: Literal["finlex_oracle", "legal_pit"]
    replay_profile: Any
    ctx: StatuteContext
    initial_state: ReplayState
    amendment_records: list[dict[str, Any]]
    amendment_ids: list[str]
    cutoff_date: Any
    oracle_version_amendment_id: str
    oracle_suspect: str


def _normalize_stop_before(stop_before: str) -> str:
    if not stop_before:
        return ""
    token = stop_before.replace("-", "/")
    if "/" not in token:
        return token
    parts = token.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts[0]) == 4 else f"{parts[1]}/{parts[0]}"


def _dedupe_consecutive_amendment_records(
    amendment_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse exact consecutive duplicate amendment records.

    Resolver output can occasionally contain the same amendment statute twice in
    a row with only the synthetic ``sequence`` field differing. Replaying both
    copies is structurally dishonest: the second pass can manufacture fake
    failed ops and mutation-boundary violations even though the real source law
    should only execute once.

    We keep this dedupe narrow on purpose:
    - only consecutive duplicates are collapsed
    - only records equal on all substantive fields (everything except
      ``sequence``) are considered duplicates
    """

    def _dedupe_key(record: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
        return tuple(sorted((key, value) for key, value in record.items() if key != "sequence"))

    deduped: list[dict[str, Any]] = []
    previous_key: tuple[tuple[str, Any], ...] | None = None
    for record in amendment_records:
        current_key = _dedupe_key(record)
        if previous_key == current_key:
            continue
        deduped.append(record)
        previous_key = current_key
    return deduped


def prepare_replay_plan(
    parent_id: str,
    *,
    mode: Literal["finlex_oracle", "legal_pit"],
    strict_profile: Any,
    corpus: CorpusStore,
    stop_before: str,
    label_postprocessor: Callable[[str, str], str],
    get_replay_profile: Callable[..., Any],
    resolve_applicable_amendment_records: Callable[..., tuple[list[dict[str, Any]], Any, Any]],
    get_consolidated_oracle_suspect: Callable[..., Optional[str]],
    extract_inline_corrections: Callable[[bytes, str], tuple[list[Any], bytes]],
) -> ReplayPlan:
    """Build the typed replay plan and initial state for one statute."""
    orig_bytes = corpus.read_source(parent_id)
    if orig_bytes is None:
        raise FileNotFoundError(f"Statute {parent_id} not found in corpus")

    corr_gate = strict_profile is None or strict_profile.allows_source_correction_rules
    if corr_gate:
        _, orig_bytes = extract_inline_corrections(orig_bytes, parent_id)
        # Apply Population-B body patches (prose/footnote corrigenda keyed by the
        # statute's own ID) to the base-statute XML.  These are the same patches that
        # patch_source_body_xml applies to amendment bodies, but the base statute is
        # never processed by process_muutoslaki, so we must apply them here.
        from lawvm.finland.corrigendum import get_patch_table as _get_corr_patch_table
        orig_bytes, _ = _get_corr_patch_table().patch_source_body_xml(orig_bytes, parent_id)

    ctx = StatuteContext.from_xml(orig_bytes, label_postprocessor)
    initial_state = ReplayState(ir=ctx.base_ir)
    replay_profile = get_replay_profile(mode)
    amendment_records, cutoff_date, oracle_version_amendment_id = resolve_applicable_amendment_records(
        parent_id,
        mode,
        corpus=corpus,
    )
    amendment_records = _dedupe_consecutive_amendment_records(amendment_records)
    amendment_ids = [str(rec["statute_id"]) for rec in amendment_records]

    stop_before_norm = _normalize_stop_before(stop_before)
    if stop_before_norm:
        try:
            cut = amendment_ids.index(stop_before_norm)
            amendment_ids = amendment_ids[:cut]
        except ValueError:
            pass

    oracle_suspect = get_consolidated_oracle_suspect(parent_id)
    return ReplayPlan(
        parent_id=parent_id,
        replay_mode=mode,
        replay_profile=replay_profile,
        ctx=ctx,
        initial_state=initial_state,
        amendment_records=amendment_records,
        amendment_ids=amendment_ids,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id or "",
        oracle_suspect=oracle_suspect or "",
    )


def populate_replay_meta(plan: ReplayPlan, replay_meta_out: Optional[Dict[str, object]]) -> None:
    """Emit backward-compatible replay metadata from a typed replay plan."""
    if replay_meta_out is None:
        return
    replay_meta_out.clear()
    replay_meta_out.update(
        {
            "cutoff_date": plan.cutoff_date.isoformat() if plan.cutoff_date else "",
            "oracle_version_amendment_id": plan.oracle_version_amendment_id or "",
            "lineage": plan.amendment_records,
            "oracle_suspect": plan.oracle_suspect or "",
        }
    )


def build_tree_invariant_finding(
    *,
    violation: str,
    source_statute: str,
    phase: str,
    message: str,
) -> Finding:
    """Build the replay-time tree invariant finding carried by Finland execution."""
    return Finding(
        kind="APPLY.TREE_INVARIANT_VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": message,
            "phase": phase,
            "violation": violation,
            "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
        },
    )


def execute_replay_plan(
    plan: ReplayPlan,
    *,
    corpus: CorpusStore,
    process_muutoslaki: Callable[..., PhaseResult[ReplayState]],
    seed_missing_chapters: Callable[..., tuple[Any, Any]],
    pre_scan_repeal_targets: Callable[..., Any],
    future_repeals_for_index: Callable[..., Any],
    post_process_tree: Callable[[Any, bool], Any],
    check_tree_invariants: Callable[[Any], list[str]],
    compiled_ops_out: Optional[List[Dict[str, object]]] = None,
    lo_ops_out: Optional[List[Any]] = None,
    failed_ops_out: Optional[List[Any]] = None,
    findings_out: Optional[List[Finding]] = None,
    source_pathologies_out: Optional[List[Any]] = None,
    elaboration_observations_out: Optional[List[Any]] = None,
    sparse_slot_bindings_out: Optional[List[Any]] = None,
    sparse_leftovers_out: Optional[List[Any]] = None,
    commencement_expiry_overrides_out: Optional[List[Any]] = None,
    mutation_events_out: Optional[List[Any]] = None,
    migration_events_out: Optional[List[MigrationEvent]] = None,
    temporal_events_out: Optional[List[Any]] = None,
    strict_profile: Any = None,
    logger: Any = None,
    checkpoint_callback: Optional[ReplayCheckpointCallback] = None,
    restructure_plans_out: Optional[List[Any]] = None,
) -> ReplayState:
    """Execute the replay fold for a prepared plan."""
    state = plan.initial_state

    seeded_ir, chapter_seed_skip = seed_missing_chapters(state.ir, plan.amendment_ids, corpus)
    if seeded_ir is not state.ir:
        state = state.with_ir(seeded_ir)

    repeal_schedule = pre_scan_repeal_targets(
        plan.amendment_ids,
        corpus,
        plan.parent_id,
        cutoff_date=plan.cutoff_date,
    )
    repeal_suffix = future_repeals_for_index(repeal_schedule)
    processed_amendment_titles: dict[str, str] = {}
    effective_migration_events_out: list[MigrationEvent] = (
        migration_events_out if migration_events_out is not None else []
    )
    record_titles = {
        str(record.get("statute_id") or ""): str(record.get("title") or "")
        for record in plan.amendment_records
    }
    for idx, mid in enumerate(plan.amendment_ids):
        future_repeals = repeal_suffix[idx] if idx < len(repeal_suffix) else set()
        _pm_result = process_muutoslaki(
            mid,
            state,
            plan.ctx,
            replay_mode=plan.replay_mode,
            compiled_ops_out=compiled_ops_out,
            lo_ops_out=lo_ops_out,
            parent_id=plan.parent_id,
            failed_ops_out=failed_ops_out,
            strict_profile=strict_profile,
            chapter_seed_skip=chapter_seed_skip,
            corpus=corpus,
            future_repeals=future_repeals if future_repeals else None,
            source_pathologies_out=source_pathologies_out,
            elaboration_observations_out=elaboration_observations_out,
            sparse_slot_bindings_out=sparse_slot_bindings_out,
            sparse_leftovers_out=sparse_leftovers_out,
            commencement_expiry_overrides_out=commencement_expiry_overrides_out,
            mutation_events_out=mutation_events_out,
            migration_events_out=effective_migration_events_out,
            prior_migration_events=tuple(effective_migration_events_out),
            restructure_plans_out=restructure_plans_out,
            processed_amendment_titles=processed_amendment_titles,
        )
        state = _pm_result.output
        processed_amendment_titles[str(mid)] = record_titles.get(str(mid), "")
        phase_findings = _pm_result.findings()
        if checkpoint_callback is not None:
            _cp_state = state  # capture for lazy closure
            checkpoint_callback(ReplayCheckpoint(
                parent_id=plan.parent_id,
                amendment_id=mid,
                step_index=idx,
                total_steps=len(plan.amendment_ids),
                serialize_text=lambda _s=_cp_state: _serialize_text(_s.ir),
            ))
        if temporal_events_out is not None:
            temporal_events_out.extend(_pm_result.temporal_events)
        if findings_out is not None:
            findings_out.extend(phase_findings)
            # Per-amendment invariant checks are expensive for heavily-amended
            # statutes (O(amendments * nodes)).  Skip them when the final
            # post-process check (below) is sufficient — i.e. when the caller
            # did not request a checkpoint callback (diagnostic/explain mode).
            if checkpoint_callback is not None:
                sorted_ir = _resort_children(state.ir)
                for violation in check_tree_invariants(sorted_ir):
                    findings_out.append(
                        build_tree_invariant_finding(
                            violation=violation,
                            source_statute=mid,
                            phase="post_amendment",
                            message="Replay tree invariant violated after amendment application.",
                        )
                    )

    state = state.with_ir(post_process_tree(state.ir, plan.replay_profile.normalize_replay_text))
    if findings_out is not None:
        # Sort before checking so transient sort_order violations from post-processing
        # are not emitted as replay findings.
        sorted_ir = _resort_children(state.ir)
        for violation in check_tree_invariants(sorted_ir):
            findings_out.append(
                build_tree_invariant_finding(
                    violation=violation,
                    source_statute=plan.parent_id,
                    phase="post_process",
                    message="Replay tree invariant violated after replay post-processing.",
                )
            )

    if logger is not None and logger.isEnabledFor(10):  # logging.DEBUG
        for violation in check_tree_invariants(state.ir):
            logger.debug("  INVARIANT: %s", violation)

    return state


__all__ = [
    "ReplayPlan",
    "build_tree_invariant_finding",
    "prepare_replay_plan",
    "populate_replay_meta",
    "execute_replay_plan",
]
