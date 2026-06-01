"""UK oracle-check — 3-bucket divergence classifier for UK statutes.

For each EID divergence between replay and oracle, classifies into:

  deterministic-gap   — replay missing a node that an amendment should have
                        produced; cross-ref compile rejections / unwarranted ops
  manual-frontier     — commencement-gated, appropriate-place, span/range, savings
                        or other source-insufficient effects; reuses source_adjudication
                        classifiers and effect_diagnostics_out from compile_ops_for_statute
  oracle-suspect      — replay coherent + source-faithful but oracle differs;
                        includes oracle-only EIDs that have no corresponding
                        apply op and no source warrant

Usage (via CLI):
    lawvm -j uk oracle-check ukpga/1978/30
    lawvm -j uk oracle-check nia/2000/1
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

from lawvm.core.mutation_accounting import build_mutation_invariant_reports
from lawvm.uk_legislation.grounding_collateral import (
    grounding_collateral_eids as _shared_grounding_collateral_eids,
    score_with_grounding_collateral_excluded,
)
from lawvm.uk_legislation.phase_discipline import uk_phase_owner_for_diagnostic

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"

# ── manual-frontier rule_id prefixes ────────────────────────────────────────
# Any lowering rejection whose rule_id starts with one of these is a
# manual-frontier classification, not a deterministic-gap.
_MANUAL_FRONTIER_RULE_PREFIX = "uk_manual_frontier_"

# Out-of-scope rule IDs map directly to manual-frontier
_OUT_OF_SCOPE_RULE_IDS = frozenset(
    {
        "uk_manual_frontier_application_by_reference_out_of_scope",
        "uk_manual_frontier_as_if_application_modification_out_of_scope",
        "uk_manual_frontier_commencement_effect_out_of_scope",
        "uk_manual_frontier_conditional_temporal_repeal_out_of_scope",
    }
)

# Repeal source warrant rule
_REPEAL_NOT_WARRANTED_RULE_ID = "uk_repeal_target_not_source_warranted"


def _is_manual_frontier_rule(rule_id: str) -> bool:
    return rule_id.startswith(_MANUAL_FRONTIER_RULE_PREFIX)


def _grounding_collateral_eids(
    replayed_eids: set[str],
    oracle_eids: set[str],
    alignment_events: list[dict[str, Any]],
) -> list[str]:
    """Compatibility wrapper for the shared UK grounding-collateral helper."""

    return list(
        _shared_grounding_collateral_eids(
            replayed_eids,
            oracle_eids,
            alignment_events,
        )
    )


def _owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(uk_phase_owner_for_diagnostic(row) for row in rows)
    return dict(sorted(counts.items()))


def _format_owner_phase_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    return ", ".join(f"{phase}={count}" for phase, count in counts.items())


def _collect_replay_eids(replayed_ir: Any) -> set[str]:
    """Collect all non-zombie EIDs from the replayed IR."""
    from lawvm.core.ir_helpers import is_zombie

    eids: set[str] = set()

    def _walk(node: Any) -> None:
        if is_zombie(node, pit_date=None):
            return
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid:
            eids.add(eid)
        for child in node.children:
            _walk(child)

    _walk(replayed_ir.body)
    for schedule in replayed_ir.supplements:
        _walk(schedule)
    return eids


def _classify_divergences(
    *,
    only_replay: set[str],
    only_oracle: set[str],
    text_diff: set[str],
    lowering_rejections: list[dict[str, Any]],
    effect_diagnostics: list[dict[str, Any]],
    effect_feed_parse_rejections: list[dict[str, Any]],
    authority_rejections: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Assign each divergent EID to one of the three AGENTS.md §2.1 buckets.

    Returns dict with keys:
      "deterministic_gap"  — replay should have produced this node
      "manual_frontier"    — needs owned claim; source is ambiguous/out-of-scope
      "oracle_suspect"     — replay coherent; oracle is stale/editorial/wrong
      "text_diff"          — both sides have the EID but text differs (unclassified further)

    Classification logic:
      only_oracle  → deterministic_gap by default (oracle has it, replay missed it);
                     promoted to manual_frontier if the rule_ids explaining the
                     miss are all manual-frontier or out-of-scope;
                     promoted to oracle_suspect if there are NO compile rejections
                     at all covering the bucket (oracle-extra with no source ops)
      only_replay  → oracle_suspect by default (replay produced something oracle lacks;
                     the not-source-warranted repeal rule is a strong signal here)
      text_diff    → reported as text_diff (requires deeper per-text analysis)
    """
    # Build a set of affected EIDs implied by manual-frontier rejections
    manual_frontier_eids: set[str] = set()
    deterministic_gap_eids: set[str] = set()

    all_rejections = (
        lowering_rejections
        + effect_feed_parse_rejections
        + authority_rejections
    )
    for rejection in all_rejections:
        rule_id = str(rejection.get("rule_id") or "")
        # affected_provisions is a comma/space-separated list or a single EID fragment
        ap = str(rejection.get("affected_provisions") or "")
        if _is_manual_frontier_rule(rule_id):
            # Manual-frontier rejections: their affected provisions are MF
            if ap:
                manual_frontier_eids.add(ap)
        elif rule_id and rule_id != _REPEAL_NOT_WARRANTED_RULE_ID:
            # Any other blocking rejection that is NOT a warranted repeal
            # is a deterministic gap signal
            if ap:
                deterministic_gap_eids.add(ap)

    # Diagnostics from effect_diagnostics_out carry repeal-not-warranted
    # observations which make only_replay EIDs oracle_suspect (replay correctly
    # retained an EID that the source tried to repeal without warrant)
    repeal_not_warranted_affected: set[str] = set()
    for diag in effect_diagnostics:
        rule_id = str(diag.get("rule_id") or "")
        if rule_id == _REPEAL_NOT_WARRANTED_RULE_ID:
            ap = str(diag.get("affected_provisions") or "")
            if ap:
                repeal_not_warranted_affected.add(ap)

    result: dict[str, list[str]] = {
        "deterministic_gap": [],
        "manual_frontier": [],
        "oracle_suspect": [],
        "text_diff": [],
    }

    # Classify only_oracle EIDs
    for eid in sorted(only_oracle):
        # If any manual-frontier rejection covers a provision that looks like
        # this EID, treat it as manual-frontier
        eid_lower = eid.lower()
        covered_by_mf = any(
            mf_ap and (mf_ap.lower() in eid_lower or eid_lower in mf_ap.lower())
            for mf_ap in manual_frontier_eids
        )
        covered_by_det = any(
            det_ap and (det_ap.lower() in eid_lower or eid_lower in det_ap.lower())
            for det_ap in deterministic_gap_eids
        )
        if covered_by_mf and not covered_by_det:
            result["manual_frontier"].append(eid)
        elif covered_by_det:
            result["deterministic_gap"].append(eid)
        else:
            # Default: oracle has it, replay does not, no clear rejection reason
            # → deterministic gap (the most actionable classification)
            result["deterministic_gap"].append(eid)

    # Classify only_replay EIDs
    for eid in sorted(only_replay):
        eid_lower = eid.lower()
        # If covered by repeal-not-warranted, the replay held the EID correctly
        # while oracle removed it without source warrant → oracle_suspect
        covered_by_rnw = any(
            ap and (ap.lower() in eid_lower or eid_lower in ap.lower())
            for ap in repeal_not_warranted_affected
        )
        if covered_by_rnw:
            result["oracle_suspect"].append(eid)
        else:
            # Replay produced an EID the oracle lacks: likely oracle_suspect
            # (oracle not yet updated) but could be a replay overshoot
            result["oracle_suspect"].append(eid)

    # Text-diff EIDs: report as separate bucket for further investigation
    for eid in sorted(text_diff):
        result["text_diff"].append(eid)

    return result


def oracle_check_uk_statute(
    statute_id: str,
    *,
    db_path: Path | None = None,
    max_sample: int = 5,
) -> str:
    """Run UK oracle-check for one statute. Returns a human-readable string.

    Three-bucket output:
      deterministic_gap   — replay should have produced these EIDs
      manual_frontier     — requires owned claims (commencement / appropriate-place / etc.)
      oracle_suspect      — replay coherent; oracle appears stale or wrong
      text_diff           — both have the EID but text differs (investigate further)
    """
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.uk_legislation.source_state import (
        UKStatuteXmlContentStatus,
        classify_uk_statute_xml_content,
    )
    from lawvm.tools.uk_structural_review import (
        _collect_replay_eid_texts,
        _build_norm_to_raw,
        _build_oracle_norm_text_map,
        _classify_eids,
        _CLASS_ONLY_REPLAY,
        _CLASS_ONLY_ORACLE,
        _CLASS_TEXT_DIFF,
        _CLASS_SAME,
    )

    resolved_db = db_path if db_path is not None else _DEFAULT_DB
    if not resolved_db.exists():
        return (
            f"=== {statute_id} — UK oracle-check ERROR ===\n"
            f"Archive not found at {resolved_db}\n"
        )

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(resolved_db) as archive:
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            return (
                f"=== {statute_id} — UK oracle-check ERROR ===\n"
                f"Enacted XML missing from archive: {enacted_url}\n"
            )
        base_source = classify_uk_statute_xml_content(base_bytes)
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            return (
                f"=== {statute_id} — UK oracle-check ERROR ===\n"
                f"Oracle XML missing from archive: {oracle_url}\n"
            )
        oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map: dict[str, str] = oracle_data.get("eid_map", {})
        text_map: dict[str, str] = oracle_data.get("text_map", {})
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )
        current_eids: set[str] = set(eid_map.values())

        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        mutation_events: list[Any] = []
        alignment_events: list[dict[str, Any]] = []
        replayed_ir = pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=True,
            oracle_alignment_events_out=alignment_events,
            mutation_events_out=mutation_events,
        )

    # Collect replay EID texts + leaf EIDs
    replay_eid_texts, replay_leaf_eids = _collect_replay_eid_texts(replayed_ir)
    replayed_eids: set[str] = set(replay_eid_texts)

    # Normalize both EID sets
    replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
        replayed_eids,
        current_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )

    replay_norm_to_raw = _build_norm_to_raw(replayed_eids)
    oracle_norm_text_map = _build_oracle_norm_text_map(text_map)

    classified = _classify_eids(
        replay_eid_texts,
        oracle_norm_text_map,
        replay_norm_set=frozenset(replay_compare_eids),
        oracle_norm_set=frozenset(oracle_compare_eids),
        replay_norm_to_raw=replay_norm_to_raw,
        replay_leaf_eids=frozenset(replay_leaf_eids),
    )

    only_replay_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_REPLAY}
    only_oracle_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_ORACLE}

    grounding_collateral_eids = _grounding_collateral_eids(
        replayed_eids, current_eids, alignment_events
    )
    collateral_score = score_with_grounding_collateral_excluded(
        replay_compare_eids,
        oracle_compare_eids,
        [
            {
                **event,
                "after_eid": str(event.get("after_eid") or "").lower(),
            }
            for event in alignment_events
        ],
    )
    text_diff_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_TEXT_DIFF}
    same_count = sum(1 for v in classified.values() if v["kind"] == _CLASS_SAME)

    buckets = _classify_divergences(
        only_replay=only_replay_eids,
        only_oracle=only_oracle_eids,
        text_diff=text_diff_eids,
        lowering_rejections=lowering_rejections,
        effect_diagnostics=effect_diagnostics,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        authority_rejections=authority_rejections,
    )

    compile_rejection_rows = lowering_rejections + effect_feed_parse_rejections + authority_rejections
    manual_frontier_rejection_rows = [
        r for r in lowering_rejections if _is_manual_frontier_rule(str(r.get("rule_id") or ""))
    ]
    deterministic_rejection_rows = [
        r
        for r in compile_rejection_rows
        if not _is_manual_frontier_rule(str(r.get("rule_id") or ""))
        and str(r.get("rule_id") or "") != _REPEAL_NOT_WARRANTED_RULE_ID
    ]
    compile_rejection_owner_phase_counts = _owner_phase_counts(compile_rejection_rows)
    manual_frontier_owner_phase_counts = _owner_phase_counts(manual_frontier_rejection_rows)
    deterministic_rejection_owner_phase_counts = _owner_phase_counts(deterministic_rejection_rows)

    # Count compile rejections by category
    n_mf_rejections = sum(
        1 for r in lowering_rejections
        if _is_manual_frontier_rule(str(r.get("rule_id") or ""))
    )
    n_det_rejections = sum(
        1 for r in deterministic_rejection_rows
    )
    n_rnw_diagnostics = sum(
        1 for d in effect_diagnostics
        if str(d.get("rule_id") or "") == _REPEAL_NOT_WARRANTED_RULE_ID
    )
    mutation_reports = build_mutation_invariant_reports(mutation_events)
    mutation_unexplained_reports = [
        report
        for report in mutation_reports
        if report.unexplained_changed_paths or not report.path_set_invariant_holds
    ]
    mutation_unexplained_path_count = sum(
        len(report.unexplained_changed_paths)
        for report in mutation_unexplained_reports
    )

    common = replay_compare_eids & oracle_compare_eids
    similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)

    lines: list[str] = [
        f"=== {statute_id} — UK oracle-check ===",
        (
            f"Similarity: {similarity:.1%}  "
            f"replay={len(replay_compare_eids)}  oracle={len(oracle_compare_eids)}  "
            f"common={len(common)}  same={same_count}"
        ),
        (
            "Similarity excluding grounding collateral: "
            f"{collateral_score.collateral_excluded_similarity:.1%}  "
            f"excluded={len(grounding_collateral_eids)}"
        ),
        f"Ops compiled: {len(ops)}  "
        f"Rejections: det={n_det_rejections} mf={n_mf_rejections}  "
        f"repeal-not-warranted diagnostics={n_rnw_diagnostics}",
        (
            "Rejection owner phases: "
            f"all={_format_owner_phase_counts(compile_rejection_owner_phase_counts)}  "
            f"det={_format_owner_phase_counts(deterministic_rejection_owner_phase_counts)}  "
            f"mf={_format_owner_phase_counts(manual_frontier_owner_phase_counts)}"
        ),
        (
            f"Mutation boundary: events={len(mutation_events)}  "
            f"reports={len(mutation_reports)}  "
            f"unexplained_reports={len(mutation_unexplained_reports)}  "
            f"unexplained_paths={mutation_unexplained_path_count}"
        ),
        (
            "Base source: "
            f"{base_source.status.value}  "
            f"bytes={base_source.size}  "
            f"NumberOfProvisions={base_source.number_of_provisions or '<unknown>'}  "
            f"body={base_source.has_body}  schedules={base_source.has_schedules}"
        ),
        "",
        "DIVERGENCE BUCKET SUMMARY:",
        f"  deterministic_gap  : {len(buckets['deterministic_gap'])}  "
        "(replay should have produced these EIDs; investigate compile rejections)",
        f"  manual_frontier    : {len(buckets['manual_frontier'])}  "
        "(needs owned claim: commencement/appropriate-place/span/savings)",
        f"  oracle_suspect     : {len(buckets['oracle_suspect'])}  "
        "(replay coherent; oracle may be stale or wrong)",
        f"  text_diff          : {len(buckets['text_diff'])}  "
        "(both sides have the EID but text differs; investigate per-EID)",
        f"  grounding_collateral: {len(grounding_collateral_eids)}  "
        "(subset of only-replay EIDs minted by oracle-alignment local_fallback, not a source op)",
        "",
    ]

    if base_source.status is UKStatuteXmlContentStatus.METADATA_ONLY:
        lines.extend(
            [
                "BASE_SOURCE_FRONTIER:",
                (
                    "  Enacted XML is a metadata-only legal-source envelope. "
                    "Oracle-only original provisions are source-acquisition frontier "
                    "evidence here, not proof that replay should synthesize the base "
                    "from current text."
                ),
                "",
            ]
        )

    for bucket_name, bucket_eids in buckets.items():
        if not bucket_eids:
            continue
        sample = bucket_eids[:max_sample]
        lines.append(f"{bucket_name.upper()} ({len(bucket_eids)} EIDs):")
        for eid in sample:
            lines.append(f"  {eid}")
        if len(bucket_eids) > max_sample:
            lines.append(f"  ... ({len(bucket_eids) - max_sample} more)")
        lines.append("")

    if grounding_collateral_eids:
        lines.append(
            f"GROUNDING_COLLATERAL ({len(grounding_collateral_eids)} EIDs minted by "
            "oracle-alignment local_fallback, no source op):"
        )
        for eid in grounding_collateral_eids[:max_sample]:
            lines.append(f"  {eid}")
        if len(grounding_collateral_eids) > max_sample:
            lines.append(f"  ... ({len(grounding_collateral_eids) - max_sample} more)")
        lines.append("")

    if mutation_unexplained_reports:
        lines.append(
            f"MUTATION_BOUNDARY_UNEXPLAINED ({len(mutation_unexplained_reports)} reports):"
        )
        for report in mutation_unexplained_reports[:max_sample]:
            lines.append(
                "  "
                f"op_id={report.op_id or '<missing>'} helper={report.helper} "
                f"outcome={report.outcome} "
                f"unexplained_paths={len(report.unexplained_changed_paths)}"
            )
        if len(mutation_unexplained_reports) > max_sample:
            lines.append(f"  ... ({len(mutation_unexplained_reports) - max_sample} more)")
        lines.append("")

    if n_mf_rejections > 0:
        lines.append(f"TOP MANUAL-FRONTIER REJECTION RULES ({n_mf_rejections} total):")
        rule_counter: Counter[str] = Counter()
        for r in lowering_rejections:
            rule_id = str(r.get("rule_id") or "")
            if _is_manual_frontier_rule(rule_id):
                rule_counter[rule_id] += 1
        for rule_id, count in rule_counter.most_common(5):
            lines.append(f"  {count:4d}  {rule_id}")
        lines.append("")

    if n_det_rejections > 0:
        lines.append(f"TOP DETERMINISTIC-GAP REJECTION RULES ({n_det_rejections} total):")
        rule_counter_det: Counter[str] = Counter()
        for r in compile_rejection_rows:
            rule_id = str(r.get("rule_id") or "")
            if (
                rule_id
                and not _is_manual_frontier_rule(rule_id)
                and rule_id != _REPEAL_NOT_WARRANTED_RULE_ID
            ):
                rule_counter_det[rule_id] += 1
        for rule_id, count in rule_counter_det.most_common(5):
            lines.append(f"  {count:4d}  {rule_id}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main(args: Any) -> None:
    """Entry point for ``lawvm -j uk oracle-check <statute_id>``."""
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else None
    sid = getattr(args, "statute_id", None)

    if not sid:
        print("ERROR: provide <statute_id>", file=sys.stderr)
        raise SystemExit(1)

    result = oracle_check_uk_statute(sid, db_path=db_path)
    print(result, end="")
