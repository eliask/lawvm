#!/usr/bin/env python3
"""Farchive-native broad UK replay-vs-oracle baseline.

The 9-statute gate (``scripts/uk_regression_test.py``) is too narrow to detect
regressions in oracle grounding, which touches *every* statute's score. This
tool scores replay-vs-oracle EID-set similarity for an arbitrary sample of UK
statutes drawn straight from the farchive (no on-disk raw XML required), so a
grounding change can be checked against a broad baseline before it ships.

Two scoring lanes per statute:
  - ``aligned``   : apply_ops with oracle EID alignment (the production score).
  - ``unaligned`` : apply_ops with ``allow_oracle_alignment=False`` (structural
                    replay only). The aligned/unaligned gap is the #53 signal —
                    when grounding is unstable the aligned score moves under node
                    removal while the unaligned score does not.

Each statute is scored in its OWN subprocess (``--one ID``) so peak RSS stays
bounded under WSL2 (per the source-root-lifecycle note); the driver forks one
child per statute and aggregates a JSON snapshot.

Usage:
  # score an explicit list, write a snapshot
  uv run python scripts/uk_broad_baseline.py --ids ukpga/1978/30 ukpga/1985/6 \
      --out .tmp/uk_baseline.json

  # sample N statutes that have BOTH enacted+current in the archive
  uv run python scripts/uk_broad_baseline.py --sample 150 --seed 7 \
      --out .tmp/uk_baseline.json

  # score one statute (subprocess unit; prints one JSON line)
  uv run python scripts/uk_broad_baseline.py --one ukpga/1978/30

  # compare two snapshots (regression gate)
  uv run python scripts/uk_broad_baseline.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"

# A statute is flagged a regression if its aligned score drops by more than this
# many percentage points versus the baseline snapshot.
_REGRESSION_TOL = 0.1
_HIGH_FIDELITY_AFTER_GROUNDING_THRESHOLD = 95.0
_GROUNDING_DOMINATED_DELTA_THRESHOLD = 20.0
_STRUCTURAL_MATCH_THRESHOLD = 99.5
_COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS = 25
_LOW_VOLUME_RESIDUAL_MAX_MISSES = 25
_LOW_VOLUME_RESIDUAL_MIN_SCORE = 85.0
_MANUAL_FRONTIER_BLOCKING_RULES = frozenset(
    {
        "uk_effect_repeal_table_replacement_payload_rejected",
        "uk_effect_repeal_table_structural_repeal_unresolved",
        "uk_effect_source_payload_without_instruction_context_rejected",
        "uk_effect_table_entry_instruction_rejected",
        "uk_effect_whole_act_word_level_text_patch_rejected",
    }
)
_MANUAL_FRONTIER_ACTIONABLE_STATUSES = frozenset(
    {
        "manual_compile_candidate",
        "deterministic_frontend_candidate",
        "source_insufficient",
    }
)
_ACTIVE_UNCLASSIFIED_RESIDUAL_BUCKETS = frozenset(
    {
        "bounded_low_volume_residual",
        "compile_rejection_dominated_residual",
        "grounding_dominated_residual",
        "residual_after_grounding",
        "retained_eu_mixed_representation_residual",
        "structural_match_eid_scheme_residual",
    }
)


def _eids(nodes: list[Any], pit_date: Optional[str] = None) -> set[str]:
    from lawvm.core.ir_helpers import is_zombie

    out: set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            out.add(eid)
        out.update(_eids(n.children, pit_date=pit_date))
    return out


def _similarity(replay_eids: set[str], oracle_eids: set[str]) -> float:
    from lawvm.uk_legislation.grounding_collateral import eid_set_similarity

    return eid_set_similarity(replay_eids, oracle_eids)


def _normalized_compare_eids(
    replay_eids: set[str],
    oracle_eids: set[str],
    *,
    oracle_physical_eid_aliases: dict[str, str],
    oracle_visible_number_eid_aliases: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Normalize broad-gate EID comparison through the same lens as uk-misses."""
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids

    return normalize_uk_replay_compare_eids(
        replay_eids,
        oracle_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )


def score_one(statute_id: str) -> dict[str, Any]:
    """Score one statute from the farchive. Returns a result dict (never raises)."""
    from farchive import Farchive
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.source_state import classify_uk_statute_xml_content
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline
    from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes, parse_uk_statute_ir_bytes

    result: dict[str, Any] = {"statute_id": statute_id}
    archive = Farchive(DB_PATH)
    try:
        enacted = archive.get(f"{_LEG_BASE}/{statute_id}/enacted/data.xml")
        current = archive.get(f"{_LEG_BASE}/{statute_id}/data.xml")
        if not enacted:
            return {
                **result,
                "base_source_status": "absent",
                "oracle_source_status": "unknown",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_absent",
            }
        if not current:
            return {
                **result,
                "base_source_status": "unknown",
                "oracle_source_status": "absent",
                "score_status": "source_frontier",
                "source_frontier_reason": "oracle_absent",
            }
        base_source = classify_uk_statute_xml_content(enacted)
        current_source = classify_uk_statute_xml_content(current)
        result.update(_source_state_fields("base", base_source))
        result.update(_source_state_fields("oracle", current_source))
        if base_source.status.value in {"too_small", "parse_error"}:
            return {
                **result,
                "score_status": "source_frontier",
                "source_frontier_reason": f"base_{base_source.status.value}",
            }
        if current_source.status.value in {"too_small", "parse_error", "metadata_only"}:
            return {
                **result,
                "score_status": "source_frontier",
                "source_frontier_reason": f"oracle_{current_source.status.value}",
            }

        oracle_data = extract_eid_map_bytes(current)
        eid_map = oracle_data.get("eid_map", {})
        text_map = oracle_data.get("text_map", {})
        oracle_eids = {str(eid) for eid in eid_map.values() if eid}
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )

        pipeline = UKReplayPipeline(REPO_ROOT)
        effect_rows = load_effects_for_statute_from_archive(statute_id, archive)
        result["n_effects"] = len(effect_rows)
        ops = pipeline.compile_ops_for_statute(statute_id, archive=archive)
        result["n_ops"] = len(ops)

        # The UK compiler still has a few list-present-sensitive diagnostic paths.
        # Keep scoring on the historical no-output compile, then run a separate
        # diagnostic compile so evidence collection cannot perturb replay.
        effect_feed_parse_rejections: list[dict[str, Any]] = []
        lowering_rejections: list[dict[str, Any]] = []
        authority_rejections: list[dict[str, Any]] = []
        effect_diagnostics: list[dict[str, Any]] = []
        pipeline.compile_ops_for_statute(
            statute_id,
            archive=archive,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
            effect_diagnostics_out=effect_diagnostics,
        )
        compile_rejections = [
            *effect_feed_parse_rejections,
            *lowering_rejections,
            *authority_rejections,
        ]
        blocking_compile_rejections = _blocking_records(compile_rejections)
        result["n_compile_rejections"] = len(compile_rejections)
        result["compile_rejection_rule_counts"] = _rule_counts(compile_rejections)
        result["n_blocking_compile_rejections"] = len(blocking_compile_rejections)
        result["blocking_compile_rejection_rule_counts"] = _rule_counts(
            blocking_compile_rejections
        )
        manual_frontier_records = [
            row
            for row in effect_diagnostics
            if row.get("rule_id") == "uk_manual_compile_frontier_classified"
        ]
        result["n_manual_frontier_records"] = len(manual_frontier_records)
        result["manual_frontier_status_counts"] = _manual_frontier_status_counts(
            manual_frontier_records
        )
        result["manual_frontier_rule_counts"] = _manual_frontier_rule_counts(
            manual_frontier_records
        )
        result["manual_frontier_template_status_counts"] = (
            _manual_frontier_template_status_counts(manual_frontier_records)
        )

        lanes: dict[str, float] = {}
        for lane, aligned in (("aligned", True), ("unaligned", False)):
            base_ir = parse_uk_statute_ir_bytes(enacted, statute_id=statute_id)
            alignment_events: list[dict[str, Any]] = []
            replayed = pipeline.apply_ops(
                base_ir,
                ops,
                eid_map=eid_map,
                text_map=text_map,
                allow_oracle_alignment=aligned,
                oracle_alignment_events_out=alignment_events if aligned else None,
            )
            replay_eids = _eids([replayed.body]) | {
                e for s in replayed.supplements for e in _eids([s])
            }
            replay_compare_eids, oracle_compare_eids = _normalized_compare_eids(
                replay_eids,
                oracle_eids,
                oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
            )
            lanes[lane] = round(
                100.0 * _similarity(replay_compare_eids, oracle_compare_eids),
                2,
            )
            if lane == "aligned":
                from lawvm.uk_legislation.grounding_collateral import (
                    score_with_grounding_collateral_excluded,
                )

                common_eids = replay_compare_eids & oracle_compare_eids
                collateral_score = score_with_grounding_collateral_excluded(
                    replay_compare_eids,
                    oracle_compare_eids,
                    alignment_events,
                )
                result["n_common"] = len(common_eids)
                result["n_only_in_oracle"] = len(oracle_compare_eids - replay_compare_eids)
                result["n_only_in_replayed"] = len(replay_compare_eids - oracle_compare_eids)
                result["n_replay"] = len(replay_compare_eids)
                result["n_oracle"] = len(oracle_compare_eids)
                result["n_grounding_collateral"] = len(collateral_score.collateral_eids)
                result["n_zero_oracle_retention_eids"] = (
                    len(replay_compare_eids) if not oracle_compare_eids else 0
                )
                result["aligned_excluding_grounding_collateral"] = round(
                    100.0 * collateral_score.collateral_excluded_similarity,
                    2,
                )
        result["score_status"] = "scored"
        result["aligned"] = lanes["aligned"]
        result["unaligned"] = lanes["unaligned"]
        return result
    except Exception as exc:  # noqa: BLE001 — a broken statute must not abort the sweep
        return {**result, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        archive.close()


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize broad-baseline row diagnostics without reclassifying rows."""
    scored = [
        r for r in results if "error" not in r and r.get("score_status") != "source_frontier"
    ]
    errored = [r for r in results if "error" in r]
    source_frontier = [
        r for r in results if "error" not in r and r.get("score_status") == "source_frontier"
    ]
    source_frontier_reasons = Counter(
        str(r.get("source_frontier_reason") or "unknown") for r in source_frontier
    )
    source_chain_frontier_reasons = Counter(
        reason
        for r in results
        if (reason := _source_chain_frontier_reason_for_row(r))
    )
    source_chain_frontier_statutes: dict[str, list[str]] = {}
    for row in results:
        reason = _source_chain_frontier_reason_for_row(row)
        if not reason:
            continue
        statute_id = str(row.get("statute_id") or "")
        if not statute_id:
            continue
        source_chain_frontier_statutes.setdefault(reason, []).append(statute_id)
    source_chain_frontier_statutes = {
        reason: sorted(statute_ids)
        for reason, statute_ids in sorted(source_chain_frontier_statutes.items())
    }
    zero_oracle_retention = [
        r
        for r in scored
        if int(r.get("n_oracle") or 0) == 0 and int(r.get("n_replay") or 0) > 0
    ]
    triage_buckets = Counter(_triage_bucket_for_row(r) for r in results)
    active_unclassified_residuals = [
        r
        for r in results
        if _triage_bucket_for_row(r) in _ACTIVE_UNCLASSIFIED_RESIDUAL_BUCKETS
    ]
    return {
        "scored": scored,
        "errored": errored,
        "source_frontier": source_frontier,
        "source_frontier_reasons": dict(sorted(source_frontier_reasons.items())),
        "source_chain_frontier_reasons": dict(
            sorted(source_chain_frontier_reasons.items())
        ),
        "source_chain_frontier_statutes": source_chain_frontier_statutes,
        "triage_buckets": dict(sorted(triage_buckets.items())),
        "active_unclassified_residual_count": len(active_unclassified_residuals),
        "active_unclassified_residual_statutes": sorted(
            str(r.get("statute_id") or "") for r in active_unclassified_residuals
        ),
        "zero_oracle_retention_count": len(zero_oracle_retention),
        "zero_oracle_retention_eids": sum(
            int(r.get("n_zero_oracle_retention_eids") or r.get("n_replay") or 0)
            for r in zero_oracle_retention
        ),
    }


def _triage_bucket_for_row(row: dict[str, Any]) -> str:
    """Classify a broad-baseline row for work selection, not scoring."""
    if "error" in row:
        return "error"
    if row.get("score_status") == "source_frontier":
        reason = str(row.get("source_frontier_reason") or "unknown")
        return f"source_frontier:{reason}"
    n_oracle = int(row.get("n_oracle") or 0)
    n_replay = int(row.get("n_replay") or 0)
    if n_oracle == 0 and n_replay > 0:
        return "zero_oracle_retention"
    if row.get("base_source_status") == "metadata_only":
        return "base_metadata_only_frontier"
    aligned = float(row.get("aligned") or 0.0)
    aligned_no_gc = float(row.get("aligned_excluding_grounding_collateral", aligned) or 0.0)
    unaligned = float(row.get("unaligned") or 0.0)
    if aligned_no_gc >= _HIGH_FIDELITY_AFTER_GROUNDING_THRESHOLD:
        return "high_fidelity_after_grounding"
    if unaligned >= _STRUCTURAL_MATCH_THRESHOLD:
        return "structural_match_eid_scheme_residual"
    if row.get("n_ops") is not None and int(row.get("n_ops") or 0) == 0:
        if _has_effect_feed_absent_record(row):
            return "effect_feed_absent_frontier"
        if int(row.get("n_effects") or 0) == 0:
            return "no_effect_rows_frontier"
        if int(row.get("n_compile_rejections") or 0) > 0:
            return "nonreplay_effect_frontier"
        return "no_compiled_ops_frontier"
    if (
        int(row.get("n_grounding_collateral") or 0) > 0
        and aligned_no_gc - aligned >= _GROUNDING_DOMINATED_DELTA_THRESHOLD
    ):
        return "grounding_dominated_residual"
    if _is_manual_compile_frontier_residual(row):
        return "manual_compile_frontier_residual"
    if _is_compile_rejection_dominated_residual(row):
        return "compile_rejection_dominated_residual"
    if _is_retained_eu_mixed_representation_residual(row):
        return "retained_eu_mixed_representation_residual"
    if _is_bounded_low_volume_residual(row):
        return "bounded_low_volume_residual"
    return "residual_after_grounding"


def _source_chain_frontier_reason_for_row(row: dict[str, Any]) -> str:
    """Classify acquisition/source-chain rows without changing score buckets."""
    bucket = _triage_bucket_for_row(row)
    if bucket.startswith("source_frontier:"):
        return bucket.removeprefix("source_frontier:")
    if bucket == "effect_feed_absent_frontier":
        return "effect_feed_pages_absent"
    if bucket == "no_effect_rows_frontier":
        return "effect_rows_absent_or_unpublished"
    if bucket != "nonreplay_effect_frontier":
        return ""
    if _has_missing_structural_payload_record(row):
        return "effect_rows_missing_structural_payload"
    return "effect_rows_nonreplayable"


def _is_compile_rejection_dominated_residual(row: dict[str, Any]) -> bool:
    """Classify rows where explicit compile rejections dominate missing oracle state."""
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections < _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    return n_only_in_oracle >= max(1, n_only_in_replayed)


def _has_effect_feed_absent_record(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("uk_effect_feed_pages_absent_recorded") or 0) > 0


def _has_missing_structural_payload_record(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("uk_effect_missing_structural_payload_rejected") or 0) > 0


def _is_retained_eu_mixed_representation_residual(row: dict[str, Any]) -> bool:
    """Classify retained-EU rows with unresolved source and replay-only shape noise."""
    statute_id = str(row.get("statute_id") or "")
    if not statute_id.startswith("eur/"):
        return False
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections < _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    return n_only_in_oracle > 0 and n_only_in_replayed > 0


def _is_bounded_low_volume_residual(row: dict[str, Any]) -> bool:
    """Keep tiny residual miss sets visible without treating them as family bugs."""
    aligned = float(row.get("aligned_excluding_grounding_collateral") or row.get("aligned") or 0.0)
    if aligned < _LOW_VOLUME_RESIDUAL_MIN_SCORE:
        return False
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections >= _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_misses = int(row.get("n_only_in_oracle") or 0) + int(row.get("n_only_in_replayed") or 0)
    return n_misses <= _LOW_VOLUME_RESIDUAL_MAX_MISSES


def _is_manual_compile_frontier_residual(row: dict[str, Any]) -> bool:
    """Classify residuals with explicit manual/source-frontier workqueue evidence."""
    aligned = float(row.get("aligned_excluding_grounding_collateral") or row.get("aligned") or 0.0)
    if aligned < _LOW_VOLUME_RESIDUAL_MIN_SCORE:
        status_counts = row.get("manual_frontier_status_counts") or {}
        if not _has_actionable_manual_frontier_status(status_counts):
            return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    if n_only_in_oracle < max(1, n_only_in_replayed):
        return False
    status_counts = row.get("manual_frontier_status_counts") or {}
    if _has_actionable_manual_frontier_status(status_counts):
        return True
    blocking_counts = row.get("blocking_compile_rejection_rule_counts") or {}
    if not isinstance(blocking_counts, dict):
        return False
    return any(
        int(blocking_counts.get(rule_id) or 0) > 0
        for rule_id in _MANUAL_FRONTIER_BLOCKING_RULES
    )


def _has_actionable_manual_frontier_status(status_counts: Any) -> bool:
    if not isinstance(status_counts, dict):
        return False
    return any(
        int(status_counts.get(status) or 0) > 0
        for status in _MANUAL_FRONTIER_ACTIONABLE_STATUSES
    )


def _manual_frontier_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("manual_compile_status") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _manual_frontier_rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("manual_compile_rule_id") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _manual_frontier_template_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(row.get("suggested_claim_template_status") or "none")
        for row in rows
    )
    return dict(sorted(counts.items()))


def _blocking_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from lawvm.core.compile_records import is_blocking_compile_record

    return [row for row in rows if is_blocking_compile_record(row)]


def _rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("rule_id") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _source_state_fields(prefix: str, state: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        f"{prefix}_source_status": state.status.value,
        f"{prefix}_source_number_of_provisions": state.number_of_provisions,
        f"{prefix}_source_has_body": state.has_body,
        f"{prefix}_source_has_schedules": state.has_schedules,
        f"{prefix}_source_size": state.size,
    }
    if state.parse_error:
        fields[f"{prefix}_source_parse_error"] = state.parse_error
    return fields


def sample_statutes(n: int, seed: int, classes: Optional[list[str]]) -> list[str]:
    """Sample n statute IDs that have BOTH enacted and current XML in the archive."""
    from farchive import Farchive

    archive = Farchive(DB_PATH)
    try:
        enacted = set()
        current = set()
        suffix_enacted = "/enacted/data.xml"
        suffix_current = "/data.xml"
        for loc in archive.locators(f"{_LEG_BASE}/%/enacted/data.xml"):
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_enacted)]
            enacted.add(sid)
        for loc in archive.locators(f"{_LEG_BASE}/%/data.xml"):
            if loc.endswith(suffix_enacted):
                continue
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_current)]
            # only act-level ids (act_type/year/number), not affecting/changes URLs
            if sid.count("/") == 2 and "/changes/" not in loc and "/affecting/" not in loc:
                current.add(sid)
    finally:
        archive.close()

    both = sorted(enacted & current)
    if classes:
        both = [s for s in both if s.split("/", 1)[0] in classes]
    rng = random.Random(seed)
    rng.shuffle(both)
    return both[:n]


def run_driver(ids: list[str], out: Optional[Path]) -> int:
    results: list[dict[str, Any]] = []
    for i, sid in enumerate(ids, 1):
        proc = subprocess.run(
            [sys.executable, __file__, "--one", sid],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        row: dict[str, Any]
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, IndexError):
            row = {"statute_id": sid, "error": f"subprocess_exit_{proc.returncode}"}
            if proc.stderr.strip():
                row["stderr_tail"] = proc.stderr.strip().splitlines()[-1][:200]
        row["triage_bucket"] = _triage_bucket_for_row(row)
        results.append(row)
        if "error" in row:
            print(f"[{i}/{len(ids)}] {sid:24s} ERROR {row['error']}", flush=True)
        elif row.get("score_status") == "source_frontier":
            reason = str(row.get("source_frontier_reason") or "unknown")
            print(f"[{i}/{len(ids)}] {sid:24s} SOURCE-FRONTIER {reason}", flush=True)
        else:
            base_status = str(row.get("base_source_status") or "unknown")
            base_suffix = "" if base_status == "available" else f" base={base_status}"
            zero_oracle_suffix = (
                " zero_oracle_retention"
                if int(row.get("n_oracle") or 0) == 0 and int(row.get("n_replay") or 0) > 0
                else ""
            )
            print(
                f"[{i}/{len(ids)}] {sid:24s} aligned={row['aligned']:5.1f}% "
                f"aligned_no_gc={row.get('aligned_excluding_grounding_collateral', row['aligned']):5.1f}% "
                f"unaligned={row['unaligned']:5.1f}% "
                f"gc={row.get('n_grounding_collateral', 0)} "
                f"(replay={row.get('n_replay')} oracle={row.get('n_oracle')})"
                f"{base_suffix}{zero_oracle_suffix}",
                flush=True,
            )

    snapshot = {r["statute_id"]: r for r in results}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
        print(f"\nWrote {len(snapshot)} rows -> {out}")

    summary = summarize_results(results)
    scored = summary["scored"]
    errored = summary["errored"]
    source_frontier = summary["source_frontier"]
    if scored:
        avg = sum(r["aligned"] for r in scored) / len(scored)
        avg_no_gc = sum(
            r.get("aligned_excluding_grounding_collateral", r["aligned"])
            for r in scored
        ) / len(scored)
        gc_total = sum(r.get("n_grounding_collateral", 0) for r in scored)
        metadata_only_base_total = sum(
            1 for r in scored if r.get("base_source_status") == "metadata_only"
        )
        print(
            f"\nScored {len(scored)} / {len(results)}  "
            f"mean aligned={avg:.2f}%  mean aligned_no_gc={avg_no_gc:.2f}%  "
            f"grounding_collateral={gc_total}  "
            f"metadata_only_base={metadata_only_base_total}  errors={len(errored)}"
            f"  source_frontier={len(source_frontier)}"
        )
        if summary["zero_oracle_retention_count"]:
            print(
                "  zero_oracle_retention="
                f"{summary['zero_oracle_retention_count']} rows / "
                f"{summary['zero_oracle_retention_eids']} replay eIds"
            )
    else:
        print(
            f"\nScored 0 / {len(results)}  source_frontier={len(source_frontier)}  "
            f"errors={len(errored)}"
        )
    if summary["source_frontier_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in summary["source_frontier_reasons"].items()
        )
        print(f"  source_frontier_reasons: {reasons}")
    if summary["source_chain_frontier_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in summary["source_chain_frontier_reasons"].items()
        )
        print(f"  source_chain_frontier_reasons: {reasons}")
    if summary["source_chain_frontier_statutes"]:
        for reason, statute_ids in summary["source_chain_frontier_statutes"].items():
            print(f"  source_chain_frontier[{reason}]: {', '.join(statute_ids)}")
    if summary["triage_buckets"]:
        buckets = ", ".join(
            f"{bucket}={count}"
            for bucket, count in summary["triage_buckets"].items()
        )
        print(f"  triage_buckets: {buckets}")
    if summary["active_unclassified_residual_count"]:
        print(
            "  active_unclassified_residuals="
            f"{summary['active_unclassified_residual_count']}: "
            f"{', '.join(summary['active_unclassified_residual_statutes'])}"
        )
    else:
        print("  active_unclassified_residuals=0")
    return 0


def run_compare(before_path: Path, after_path: Path) -> int:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    regressions: list[tuple[str, float, float]] = []
    improvements: list[tuple[str, float, float]] = []
    for sid, a in after.items():
        b = before.get(sid)
        if not b or "aligned" not in a or "aligned" not in b:
            continue
        delta = a["aligned"] - b["aligned"]
        if delta < -_REGRESSION_TOL:
            regressions.append((sid, b["aligned"], a["aligned"]))
        elif delta > _REGRESSION_TOL:
            improvements.append((sid, b["aligned"], a["aligned"]))

    for sid, b, a in sorted(improvements, key=lambda x: x[2] - x[1], reverse=True):
        print(f"  IMPROVED   {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")
    for sid, b, a in sorted(regressions, key=lambda x: x[2] - x[1]):
        print(f"  REGRESSION {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")

    print(f"\n{len(improvements)} improved, {len(regressions)} regressed")
    return 1 if regressions else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--one", metavar="ID", help="Score a single statute (subprocess unit; prints one JSON line)")
    ap.add_argument("--ids", nargs="+", help="Explicit statute IDs to score")
    ap.add_argument("--sample", type=int, help="Sample N statutes with both enacted+current in the archive")
    ap.add_argument("--seed", type=int, default=0, help="Sample RNG seed (default 0)")
    ap.add_argument("--classes", nargs="+", help="Restrict sample to these act-type classes (e.g. ukpga uksi)")
    ap.add_argument("--out", type=Path, help="Write JSON snapshot here")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two snapshots")
    args = ap.parse_args(argv)

    if args.one:
        row = score_one(args.one)
        row["triage_bucket"] = _triage_bucket_for_row(row)
        print(json.dumps(row))
        return 0
    if args.compare:
        return run_compare(Path(args.compare[0]), Path(args.compare[1]))

    ids: list[str] = []
    if args.ids:
        ids.extend(args.ids)
    if args.sample:
        ids.extend(sample_statutes(args.sample, args.seed, args.classes))
    if not ids:
        ap.error("nothing to do: pass --one, --ids, --sample, or --compare")
    return run_driver(ids, args.out)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
