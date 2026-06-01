"""lawvm uk-replay -- UK archive-backed amendment replay with timeline integration.

Replays UK legislation amendments against an enacted base loaded from the
Farchive DB, compares against an archive-backed oracle (current or PIT
when present in the archive), and optionally compiles provision timelines.

Usage:
    lawvm uk-replay ukpga/1998/42
    lawvm uk-replay ukpga/1998/42 --pit-date 2020-01-01
    lawvm uk-replay ukpga/1998/42 --enacted-only
    lawvm uk-replay ukpga/1998/42 --verbose
    lawvm uk-replay ukpga/1998/42 --fetch-missing   # pre-fetch missing affecting act XMLs
    lawvm uk-replay ukpga/1998/42 --timeline        # compile ops-first timelines + show summary
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence, Set

if TYPE_CHECKING:
    import argparse
    from lawvm.core.ir import IRStatute

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import is_zombie
from lawvm.uk_legislation.source_state import (
    is_uk_affecting_act_xml_source_observation,
    uk_source_parse_observations_from_ir,
    uk_source_xml_parse_rejection,
    uk_source_state_wire_tuple as _source_state,
)
from lawvm.uk_legislation.witness_builders import _uk_temporal_events_from_ops
from lawvm.core.compile_records import is_blocking_compile_record

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"


def _get_all_eids(
    nodes: Sequence[IRNode],
    pit_date: Optional[str] = None,
) -> Set[str]:
    """Collect all eId/id attributes from a list of IRNode trees."""
    eids: Set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            eids.add(eid)
        eids.update(_get_all_eids(n.children, pit_date=pit_date))
    return eids


def _archive_url_for_statute(statute_id: str, *, pit_date: Optional[str], enacted: bool) -> str:
    if enacted:
        return f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
    if pit_date:
        return f"{_LEG_BASE}/{statute_id}/{pit_date}/data.xml"
    return f"{_LEG_BASE}/{statute_id}/data.xml"


def _source_sha256(blob: bytes | None) -> str | None:
    if blob is None:
        return None
    return hashlib.sha256(blob).hexdigest()


def _score_eids(left_eids: Set[str], right_eids: Set[str]) -> float:
    common = left_eids & right_eids
    denom = max(len(left_eids), len(right_eids), 1)
    return len(common) / denom


def _score_commenced_eids(left_eids: Set[str], right_eids: Set[str]) -> float:
    if not left_eids:
        return -1.0
    return _score_eids(left_eids, right_eids)


def _commenced_oracle_eids(oracle_eids: Set[str], commenced_eids: Set[str]) -> Set[str]:
    if not commenced_eids:
        return set()
    return oracle_eids & commenced_eids


def _uk_commencement_score_summary(
    *,
    enabled: bool,
    applicability_mode: str,
    observations: Sequence[dict[str, Any]] = (),
    unavailable_reason: str = "",
    commenced_eids: Set[str] | None = None,
    commenced_enacted_eids: Set[str] | None = None,
    commenced_replayed_eids: Set[str] | None = None,
    commenced_oracle_eids: Set[str] | None = None,
    replay_commencement_oracle_eids: Set[str] | None = None,
) -> dict[str, object]:
    commenced_eids = set(commenced_eids or set())
    commenced_enacted_eids = set(commenced_enacted_eids or set())
    commenced_replayed_eids = set(commenced_replayed_eids or set())
    commenced_oracle_eids = set(commenced_oracle_eids or set())
    replay_commencement_oracle_eids = set(replay_commencement_oracle_eids or commenced_oracle_eids)
    observation_rows = [dict(row) for row in observations]
    return {
        "enabled": enabled,
        "rule_id": "uk_replay_commencement_score_lane",
        "phase": "oracle_comparison",
        "family": "temporal_comparison_lane",
        "comparison_scope": "commencement",
        "score_formula": "common/max(left,right)",
        "applicability_mode": applicability_mode,
        "evidence_available": bool(enabled and not unavailable_reason),
        "unavailable_reason": unavailable_reason,
        "commenced_eid_count": len(commenced_eids) if enabled and not unavailable_reason else None,
        "commenced_enacted_eid_count": (
            len(commenced_enacted_eids) if enabled and not unavailable_reason else None
        ),
        "commenced_replayed_eid_count": (
            len(commenced_replayed_eids) if enabled and not unavailable_reason else None
        ),
        "commenced_oracle_eid_count": (
            len(commenced_oracle_eids) if enabled and not unavailable_reason else None
        ),
        "commenced_enacted_common_count": (
            len(commenced_enacted_eids & commenced_oracle_eids)
            if enabled and not unavailable_reason
            else None
        ),
        "commenced_replayed_common_count": (
            len(commenced_replayed_eids & replay_commencement_oracle_eids)
            if enabled and not unavailable_reason
            else None
        ),
        "commencement_score": (
            _score_commenced_eids(commenced_enacted_eids, commenced_oracle_eids)
            if enabled and not unavailable_reason
            else None
        ),
        "replay_commencement_score": (
            _score_commenced_eids(commenced_replayed_eids, replay_commencement_oracle_eids)
            if enabled and not unavailable_reason
            else None
        ),
        "observation_count": len(observation_rows),
        "observation_rule_counts": dict(
            sorted(Counter(str(row.get("rule_id") or "unknown") for row in observation_rows).items())
        ),
        "observations": observation_rows,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }


def _uk_commencement_score_text_lines(summary: dict[str, object]) -> list[str]:
    if not summary.get("enabled"):
        return []
    reason = str(summary.get("unavailable_reason") or "")
    if reason:
        return [f"Commencement EID score: unavailable reason={reason}"]
    commencement_score = summary.get("commencement_score")
    replay_commencement_score = summary.get("replay_commencement_score")
    if not isinstance(commencement_score, (int, float)) or commencement_score < 0:
        enacted_text = "not computed"
    else:
        enacted_text = f"{commencement_score:.1%}"
    if not isinstance(replay_commencement_score, (int, float)) or replay_commencement_score < 0:
        replay_text = "not computed"
    else:
        replay_text = f"{replay_commencement_score:.1%}"
    lines = [
        "Commencement EID score: "
        f"enacted={enacted_text} "
        f"replay={replay_text} "
        f"commenced={summary['commenced_eid_count']} "
        f"oracle={summary['commenced_oracle_eid_count']}"
    ]
    rule_counts = summary.get("observation_rule_counts")
    if isinstance(rule_counts, dict) and rule_counts:
        rule_text = ", ".join(f"{rule}={count}" for rule, count in sorted(rule_counts.items()))
        lines.append(f"Commencement observations: {rule_text}")
    return lines


def _uk_replay_executor_oracle_alignment_summary(
    *,
    enabled: bool,
    events: Sequence[dict[str, Any]],
    oracle_eids: Set[str],
    unavailable_reason: str = "",
    sample_limit: int = 20,
) -> dict[str, object]:
    event_rows = [dict(event) for event in events]
    event_samples = [
        {
            str(key): value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
            for key, value in event.items()
        }
        for event in event_rows[: max(0, sample_limit)]
    ]
    match_method_counts = Counter(str(event.get("match_method") or "unknown") for event in event_rows)
    cleared_count = sum(1 for event in event_rows if event.get("before_eid") and event.get("after_eid") is None)
    oracle_assigned_count = sum(
        1
        for event in event_rows
        if event.get("after_eid") is not None and str(event.get("after_eid")) in oracle_eids
    )
    local_fallback_count = sum(
        1 for event in event_rows if str(event.get("match_method") or "") == "local_fallback"
    )
    local_fallback_suppressed_count = sum(
        1
        for event in event_rows
        if str(event.get("match_method") or "") == "local_fallback_suppressed"
    )
    transparent_wrapper_cleared_count = sum(
        1
        for event in event_rows
        if str(event.get("match_method") or "") == "transparent_wrapper_cleared"
    )
    return {
        "enabled": enabled,
        "stage": "replay_executor_inputs" if enabled else "none",
        "rule_id": "uk_oracle_eid_alignment_adapter",
        "phase": "oracle_alignment",
        "family": "oracle_alignment_adapter",
        "evidence_available": enabled,
        "changed_count": len(event_rows) if enabled else None,
        "cleared_count": cleared_count if enabled else None,
        "oracle_assigned_count": oracle_assigned_count if enabled else None,
        "local_fallback_count": local_fallback_count if enabled else None,
        "local_fallback_suppressed_count": (
            local_fallback_suppressed_count if enabled else None
        ),
        "transparent_wrapper_cleared_count": transparent_wrapper_cleared_count if enabled else None,
        "match_method_counts": dict(sorted(match_method_counts.items())) if enabled else {},
        "event_sample_limit": max(0, sample_limit),
        "event_sample_count": len(event_samples) if enabled else 0,
        "event_samples": event_samples if enabled else [],
        "unavailable_reason": unavailable_reason,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def _uk_oracle_alignment_text_lines(summary: dict[str, object]) -> list[str]:
    """Render executor-input oracle alignment evidence for text output."""

    lines = [
        "Oracle alignment: "
        f"enabled={str(summary['enabled']).lower()} "
        f"changed={summary['changed_count']} "
        f"cleared={summary['cleared_count']} "
        f"oracle_assigned={summary['oracle_assigned_count']} "
        f"local_fallback={summary['local_fallback_count']} "
        f"local_fallback_suppressed={summary['local_fallback_suppressed_count']} "
        f"transparent_wrapper_cleared={summary['transparent_wrapper_cleared_count']} "
        f"samples={summary['event_sample_count']} "
        f"reason={summary['unavailable_reason'] or 'none'}"
    ]
    method_counts = summary.get("match_method_counts")
    if isinstance(method_counts, dict) and method_counts:
        method_text = ", ".join(
            f"{method}={count}"
            for method, count in sorted(method_counts.items())
        )
        lines.append(f"Oracle alignment methods: {method_text}")
    return lines


def _uk_metadata_backfill_op_count(ops: Sequence[object]) -> int:
    count = 0
    for op in ops:
        witness = getattr(op, "witness", None)
        extraction_witness = getattr(witness, "extraction_witness", None)
        if bool(getattr(extraction_witness, "metadata_fallback_used", False)):
            count += 1
    return count


def _uk_replay_regime_payload(
    *,
    enacted_only: bool,
    oracle_alignment_enabled: bool,
    metadata_backfill_op_count: int,
    allow_metadata_backfill: bool,
    allow_metadata_only_effects: bool,
    applicability_mode: str,
    authority_mode: str,
    source_unavailable_reason: str = "",
) -> dict[str, object]:
    if source_unavailable_reason:
        semantic_replay_lane = "not_run_source_unavailable"
        oracle_alignment_lane = "not_run_source_unavailable"
        source_purity_lane = "not_run_source_unavailable"
    else:
        semantic_replay_lane = (
            "source_first_enacted_base"
            if enacted_only
            else "metadata_backfilled_replay"
            if metadata_backfill_op_count
            else "effects_assisted_replay"
        )
        oracle_alignment_lane = "oracle_alignment_adapter" if oracle_alignment_enabled else "none"
        source_purity_lane = (
            "metadata_backfilled_with_oracle_adapter"
            if metadata_backfill_op_count and oracle_alignment_lane != "none"
            else "metadata_backfilled_source_semantics"
            if metadata_backfill_op_count
            else "source_backed_with_oracle_adapter"
            if oracle_alignment_lane != "none"
            else "source_backed_effects_assisted"
        )
    source_first_candidate_reasons: list[str] = []
    if source_unavailable_reason:
        source_first_candidate_reasons.append("source_unavailable")
    else:
        if enacted_only:
            source_first_candidate_reasons.append("enacted_only_baseline")
        if metadata_backfill_op_count:
            source_first_candidate_reasons.append("metadata_backfill_ops_present")
        if oracle_alignment_lane != "none":
            source_first_candidate_reasons.append("oracle_alignment_adapter_active")
        if allow_metadata_only_effects:
            source_first_candidate_reasons.append("metadata_only_effects_enabled")
        if applicability_mode != "effective_date_plus_feed_applied":
            source_first_candidate_reasons.append("applicability_selection_not_feed_applied")
        if authority_mode != "source_text_only":
            source_first_candidate_reasons.append("authority_mode_not_source_text_only")
    return {
        "semantic_replay_lane": semantic_replay_lane,
        "oracle_alignment_lane": oracle_alignment_lane,
        "source_purity_lane": source_purity_lane,
        "source_semantics_clean": bool(
            not source_unavailable_reason
            and not enacted_only
            and authority_mode == "source_text_only"
            and not metadata_backfill_op_count
            and not allow_metadata_only_effects
            and oracle_alignment_lane == "none"
        ),
        "source_first_candidate": not source_first_candidate_reasons,
        "source_first_candidate_reasons": source_first_candidate_reasons,
        "oracle_alignment_stage": "post_replay_adapter" if oracle_alignment_enabled else "none",
        "oracle_alignment_enabled": oracle_alignment_enabled,
        "metadata_backfill_enabled": allow_metadata_backfill,
        "metadata_only_effects_enabled": allow_metadata_only_effects,
        "applicability_mode": applicability_mode,
        "authority_mode": authority_mode,
    }


def _uk_compile_rejection_text_lines(
    *,
    source_parse_rejections: Sequence[dict[str, object]] = (),
    effect_feed_parse_rejections: Sequence[dict[str, object]],
    effect_source_pathology_observations: Sequence[dict[str, object]] = (),
    manual_compile_frontier_observations: Sequence[dict[str, object]] = (),
    source_acquisition_rejections: Sequence[dict[str, object]] = (),
    lowering_rejections: Sequence[dict[str, object]],
    authority_rejections: Sequence[dict[str, object]],
) -> list[str]:
    def _counts(rows: Sequence[dict[str, object]]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in rows:
            counts[str(row.get("rule_id") or "unknown")] += 1
        return dict(sorted(counts.items()))

    def _field_counts(rows: Sequence[dict[str, object]], field: str) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in rows:
            counts[str(row.get(field) or "__none__")] += 1
        return dict(sorted(counts.items()))

    def _format_counts(counts: dict[str, int]) -> str:
        return ", ".join(f"{rule_id}={count}" for rule_id, count in counts.items())

    compile_rejections = [
        *source_parse_rejections,
        *effect_feed_parse_rejections,
        *effect_source_pathology_observations,
        *manual_compile_frontier_observations,
        *source_acquisition_rejections,
        *lowering_rejections,
        *authority_rejections,
    ]
    blocking_rejections = [row for row in compile_rejections if is_blocking_compile_record(row)]
    blocking_by_lane = {
        "source_parse": [row for row in source_parse_rejections if is_blocking_compile_record(row)],
        "feed_parse": [row for row in effect_feed_parse_rejections if is_blocking_compile_record(row)],
        "effect_source_pathology": [
            row for row in effect_source_pathology_observations if is_blocking_compile_record(row)
        ],
        "manual_compile_frontier": [
            row for row in manual_compile_frontier_observations if is_blocking_compile_record(row)
        ],
        "source_acquisition": [
            row for row in source_acquisition_rejections if is_blocking_compile_record(row)
        ],
        "lowering": [row for row in lowering_rejections if is_blocking_compile_record(row)],
        "authority": [row for row in authority_rejections if is_blocking_compile_record(row)],
    }
    total_count = (
        len(source_parse_rejections)
        + len(effect_feed_parse_rejections)
        + len(effect_source_pathology_observations)
        + len(manual_compile_frontier_observations)
        + len(source_acquisition_rejections)
        + len(lowering_rejections)
        + len(authority_rejections)
    )
    if not total_count:
        return []
    lines = [
        "Compile observations: "
        f"source_parse={len(source_parse_rejections)} "
        f"feed_parse={len(effect_feed_parse_rejections)} "
        f"effect_source_pathology={len(effect_source_pathology_observations)} "
        f"manual_compile_frontier={len(manual_compile_frontier_observations)} "
        f"source_acquisition={len(source_acquisition_rejections)} "
        f"lowering={len(lowering_rejections)} "
        f"authority={len(authority_rejections)} "
        f"total={total_count}",
        "Compile rejections: "
        f"source_parse={len(blocking_by_lane['source_parse'])} "
        f"feed_parse={len(blocking_by_lane['feed_parse'])} "
        f"effect_source_pathology={len(blocking_by_lane['effect_source_pathology'])} "
        f"manual_compile_frontier={len(blocking_by_lane['manual_compile_frontier'])} "
        f"source_acquisition={len(blocking_by_lane['source_acquisition'])} "
        f"lowering={len(blocking_by_lane['lowering'])} "
        f"authority={len(blocking_by_lane['authority'])} "
        f"blocking={len(blocking_rejections)}"
    ]
    for label, rows in (
        ("source_parse", source_parse_rejections),
        ("feed_parse", effect_feed_parse_rejections),
        ("effect_source_pathology", effect_source_pathology_observations),
        ("manual_compile_frontier", manual_compile_frontier_observations),
        ("source_acquisition", source_acquisition_rejections),
        ("lowering", lowering_rejections),
        ("authority", authority_rejections),
    ):
        counts = _counts(rows)
        if counts:
            rules_label = "lowering observation" if label == "lowering" else label
            lines.append(f"{rules_label} rules: {_format_counts(counts)}")
    manual_compile_status_counts = _field_counts(
        manual_compile_frontier_observations,
        "manual_compile_status",
    )
    if manual_compile_status_counts:
        lines.append(
            "manual_compile_frontier statuses: "
            f"{_format_counts(manual_compile_status_counts)}"
        )
    manual_compile_rule_counts = _field_counts(
        manual_compile_frontier_observations,
        "manual_compile_rule_id",
    )
    if manual_compile_rule_counts:
        lines.append(
            "manual_compile_frontier manual rules: "
            f"{_format_counts(manual_compile_rule_counts)}"
        )
    blocking_counts = _counts(blocking_rejections)
    if blocking_counts:
        lines.append(
            "Compile blocking rejections: "
            f"source_parse={len(blocking_by_lane['source_parse'])} "
            f"feed_parse={len(blocking_by_lane['feed_parse'])} "
            f"effect_source_pathology={len(blocking_by_lane['effect_source_pathology'])} "
            f"manual_compile_frontier={len(blocking_by_lane['manual_compile_frontier'])} "
            f"source_acquisition={len(blocking_by_lane['source_acquisition'])} "
            f"lowering={len(blocking_by_lane['lowering'])} "
            f"authority={len(blocking_by_lane['authority'])}"
        )
        lines.append(f"blocking rules: {_format_counts(blocking_counts)}")
        for label, rows in blocking_by_lane.items():
            counts = _counts(rows)
            if counts:
                lines.append(f"blocking {label} rules: {_format_counts(counts)}")
    return lines


def _uk_prefetch_text_lines(report: dict[str, Any]) -> list[str]:
    if not bool(report.get("enabled", False)):
        return []

    def _format_counts(raw_counts: object) -> str:
        if not isinstance(raw_counts, dict):
            return ""
        counts = {
            str(rule_id): count
            for rule_id, count in raw_counts.items()
            if str(rule_id) and isinstance(count, int) and count
        }
        return ", ".join(f"{rule_id}={count}" for rule_id, count in sorted(counts.items()))

    lines = [
        "Prefetch:   "
        f"fetched={int(report.get('fetched_count') or 0)} "
        f"cached={int(report.get('already_cached_count') or 0)} "
        f"errors={int(report.get('error_count') or 0)} "
        f"events={int(report.get('event_count') or 0)} "
        f"blocking={int(report.get('blocking_event_count') or 0)}"
    ]
    event_rules = _format_counts(report.get("event_rule_counts"))
    if event_rules:
        lines.append(f"Prefetch rules: {event_rules}")
    event_owner_phases = _format_counts(report.get("event_owner_phase_counts"))
    if event_owner_phases:
        lines.append(f"Prefetch owner phases: {event_owner_phases}")
    blocking_event_rules = _format_counts(report.get("blocking_event_rule_counts"))
    if blocking_event_rules:
        lines.append(f"Prefetch blocking rules: {blocking_event_rules}")
    blocking_event_owner_phases = _format_counts(
        report.get("blocking_event_owner_phase_counts")
    )
    if blocking_event_owner_phases:
        lines.append(f"Prefetch blocking owner phases: {blocking_event_owner_phases}")
    return lines


def _short_replay_adjudication_sample_value(value: object, *, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _uk_replay_adjudication_sample_lines(
    adjudications: Sequence[object],
    *,
    kinds: Sequence[str],
    limit: int,
) -> list[str]:
    wanted = {str(kind).strip() for kind in kinds if str(kind).strip()}
    if not wanted or limit <= 0:
        return []
    total_by_kind: Counter[str] = Counter()
    samples: list[object] = []
    for adjudication in adjudications:
        kind = str(getattr(adjudication, "kind", "") or "unknown")
        if kind not in wanted:
            continue
        total_by_kind[kind] += 1
        if len(samples) < limit:
            samples.append(adjudication)
    if not total_by_kind:
        return []

    lines = ["Replay adjudication samples:"]
    for kind in sorted(wanted):
        total = total_by_kind.get(kind, 0)
        if not total:
            continue
        shown = sum(1 for sample in samples if str(getattr(sample, "kind", "") or "unknown") == kind)
        lines.append(f"  {kind}: shown={shown} total={total} omitted={max(0, total - shown)}")

    for adjudication in samples:
        detail = getattr(adjudication, "detail", {}) or {}
        if not isinstance(detail, dict):
            detail = {}
        parts = [
            f"kind={str(getattr(adjudication, 'kind', '') or 'unknown')}",
            f"source={str(getattr(adjudication, 'source_statute', '') or '')}",
            f"op={str(getattr(adjudication, 'op_id', '') or '')}",
        ]
        target = _short_replay_adjudication_sample_value(detail.get("target"))
        if target:
            parts.append(f"target={target}")
        text_match = _short_replay_adjudication_sample_value(detail.get("text_match"))
        if text_match:
            parts.append(f"text_match={text_match}")
        replacement = _short_replay_adjudication_sample_value(detail.get("replacement_text"))
        if replacement:
            parts.append(f"replacement={replacement}")
        source_shape = _short_replay_adjudication_sample_value(detail.get("source_shape"))
        if source_shape:
            parts.append(f"source_shape={source_shape}")
        lines.append("    " + " ".join(parts))
    return lines


def _uk_replay_adjudication_text_lines(
    adjudications: Sequence[object],
    *,
    sample_kinds: Sequence[str] = (),
    sample_limit: int = 5,
) -> list[str]:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_replay_adjudication_bucket,
    )
    from lawvm.uk_legislation.phase_discipline import (
        uk_phase_owner_counts_for_replay_adjudications,
    )

    counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    for adjudication in adjudications:
        kind = str(getattr(adjudication, "kind", "") or "unknown")
        counts[kind] += 1
        bucket_counts[classify_uk_replay_adjudication_bucket(kind)] += 1
    if not counts:
        return []
    kind_text = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    bucket_text = ", ".join(
        f"{bucket}={count}" for bucket, count in sorted(bucket_counts.items())
    )
    owner_phase_text = ", ".join(
        f"{phase}={count}"
        for phase, count in uk_phase_owner_counts_for_replay_adjudications(
            adjudications
        ).items()
    )
    lines = [
        f"Replay adjudications: {sum(counts.values())}",
        f"Replay adjudication buckets: {bucket_text}",
        f"Replay adjudication owner phases: {owner_phase_text}",
        f"Replay adjudication kinds: {kind_text}",
    ]
    lines.extend(
        _uk_replay_adjudication_sample_lines(
            adjudications,
            kinds=sample_kinds,
            limit=sample_limit,
        )
    )
    return lines


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_bench_comparison,
        classify_uk_commencement_current_projection,
        classify_uk_current_projection_eid_shape,
        is_core_uk_comparison,
        normalize_uk_replay_compare_eids,
    )
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.core.timeline import compile_timelines, materialize_pit
    from lawvm.core.timeline_consistency import ingest_uk_snapshots
    from lawvm.tools.replay_payloads import build_uk_replay_payload
    from lawvm.tools.uk_replay_regime import normalize_uk_replay_regime

    statute_id: str = args.statute_id
    pit_date: Optional[str] = getattr(args, "pit_date", None)
    enacted_only: bool = getattr(args, "enacted_only", False)
    verbose: bool = getattr(args, "verbose", False)
    fetch_missing: bool = getattr(args, "fetch_missing", False)
    include_enacted_affecting: bool = getattr(args, "include_enacted_affecting", False)
    replay_adjudication_sample_kinds = tuple(
        str(kind).strip()
        for kind in (getattr(args, "replay_adjudication_samples", None) or ())
        if str(kind).strip()
    )
    replay_adjudication_sample_limit = int(
        getattr(args, "replay_adjudication_sample_limit", 5) or 0
    )
    if replay_adjudication_sample_limit < 0:
        print(
            "error: --replay-adjudication-sample-limit must be zero or a positive integer",
            file=sys.stderr,
        )
        sys.exit(2)
    as_json: bool = getattr(args, "json", False)
    db_arg: Optional[str] = getattr(args, "db", None)
    use_timeline: bool = getattr(args, "timeline", False)
    score_commencement: bool = getattr(args, "commencement", False)
    _out = (lambda *a, **k: None) if as_json else print

    # ── 0. Pre-fetch missing affecting act XMLs (optional) ─────────────────
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    if fetch_missing:
        from lawvm.uk_legislation.uk_prefetch import fetch_missing_for_statute

    n_provisions = 0
    timelines = {}
    n_ops = 0
    similarity: Optional[float] = None
    replay_compare_eid_count: int | None = None
    oracle_compare_eid_count: int | None = None
    common_eid_count: int | None = None
    only_in_replayed_count: int | None = None
    only_in_oracle_count: int | None = None
    only_in_replayed_sample: list[str] = []
    only_in_oracle_sample: list[str] = []
    replay_compare_eids: set[str] = set()
    oracle_compare_eids: set[str] = set()
    commenced_replayed: set[str] = set()
    commenced_oracle_for_replay: set[str] = set()
    replay_adjudications: list = []
    oracle_alignment_events: list[dict[str, Any]] = []
    source_parse_rejections: list[dict[str, object]] = []
    effect_feed_parse_rejections: list[dict[str, object]] = []
    effect_source_pathology_observations: list[dict[str, object]] = []
    manual_compile_frontier_observations: list[dict[str, object]] = []
    source_acquisition_rejections: list[dict[str, object]] = []
    effect_diagnostics: list[dict[str, object]] = []
    lowering_rejections: list[dict[str, object]] = []
    authority_rejections: list[dict[str, object]] = []
    uk_prefetch_report: dict[str, Any] = {"enabled": bool(fetch_missing)}
    uk_commencement_summary: dict[str, object] = _uk_commencement_score_summary(
        enabled=score_commencement,
        applicability_mode="",
        unavailable_reason="not_requested" if not score_commencement else "",
    )
    replay_regime = normalize_uk_replay_regime(args)
    allow_oracle_alignment = replay_regime.allow_oracle_alignment
    applicability_mode = replay_regime.applicability_mode
    authority_mode = replay_regime.authority_mode
    allow_metadata_backfill = replay_regime.allow_metadata_backfill
    allow_metadata_only_effects = replay_regime.allow_metadata_only_effects
    uk_commencement_summary = _uk_commencement_score_summary(
        enabled=score_commencement,
        applicability_mode=applicability_mode,
        unavailable_reason="not_requested" if not score_commencement else "",
    )
    if not as_json:
        _out(
            "Replay regime: "
            f"metadata_backfill={allow_metadata_backfill} "
            f"oracle_alignment={allow_oracle_alignment} "
            f"metadata_only_effects={allow_metadata_only_effects} "
            f"applicability={applicability_mode} "
            f"authority={authority_mode}"
        )

    with Farchive(db_path) as archive:
        if fetch_missing:
            prefetch_report = fetch_missing_for_statute(
                statute_id,
                archive,
                delay=0.8,
                verbose=verbose,
                include_enacted=include_enacted_affecting,
            )
            fetched, cached, errors = prefetch_report
            report_to_dict = getattr(prefetch_report, "to_dict", None)
            if callable(report_to_dict):
                uk_prefetch_report = report_to_dict()
                uk_prefetch_report["enabled"] = True
            else:
                uk_prefetch_report = {
                    "enabled": True,
                    "fetched_count": fetched,
                    "already_cached_count": cached,
                    "error_count": errors,
                    "events": [],
                }
            print(
                f"Pre-fetch: {fetched} fetched, {cached} already cached, {errors} errors",
                file=sys.stderr,
            )

        enacted_url = _archive_url_for_statute(statute_id, pit_date=pit_date, enacted=True)
        enacted_bytes = archive.get(enacted_url)
        enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
        enacted_source_sha256 = _source_sha256(enacted_bytes)
        oracle_url = _archive_url_for_statute(statute_id, pit_date=pit_date, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
        oracle_source_sha256 = _source_sha256(oracle_bytes)
        if enacted_source_status != "available":
            error = f"enacted XML missing from archive for {enacted_url}"
            if as_json:
                payload = build_uk_replay_payload(
                    statute_id=statute_id,
                    pit_date=pit_date,
                    enacted_only=enacted_only,
                    db_path=str(db_path),
                    n_effects=None,
                    n_ops=0,
                    similarity=None,
                    comparison_class=None,
                    oracle_available=False,
                    n_provisions=0,
                    n_versions=None,
                    pit_materialized_eids=None,
                    timeline_mode="states_first",
                    enacted_url=enacted_url,
                    oracle_url=oracle_url,
                    enacted_source_status=enacted_source_status,
                    oracle_source_status=oracle_source_status,
                    enacted_source_size=enacted_source_size,
                    oracle_source_size=oracle_source_size,
                    enacted_source_sha256=enacted_source_sha256,
                    oracle_source_sha256=oracle_source_sha256,
                    uk_replay_regime=_uk_replay_regime_payload(
                        enacted_only=enacted_only,
                        oracle_alignment_enabled=False,
                        metadata_backfill_op_count=0,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_metadata_only_effects=allow_metadata_only_effects,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                        source_unavailable_reason="enacted_xml_unavailable",
                    ),
                    uk_oracle_alignment_summary=_uk_replay_executor_oracle_alignment_summary(
                        enabled=False,
                        events=(),
                        oracle_eids=set(),
                        unavailable_reason="enacted_xml_unavailable",
                    ),
                    uk_prefetch_report=uk_prefetch_report,
                    error=error,
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"error: {error}", file=sys.stderr)
            sys.exit(1)
        assert enacted_bytes is not None

        if verbose:
            print(f"Loading base IR from archive: {enacted_url}", file=sys.stderr)
        try:
            base_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=statute_id,
                version_label="enacted",
                pit_date=pit_date,
                source_path=enacted_url,
            )
            source_parse_rejections.extend(uk_source_parse_observations_from_ir(base_ir))
        except Exception as exc:
            source_parse_rejections.append(
                uk_source_xml_parse_rejection(
                    statute_id=statute_id,
                    side="enacted",
                    source_url=enacted_url,
                    exc=exc,
                )
            )
            error = f"enacted XML parse failed for {enacted_url}"
            if as_json:
                payload = build_uk_replay_payload(
                    statute_id=statute_id,
                    pit_date=pit_date,
                    enacted_only=enacted_only,
                    db_path=str(db_path),
                    n_effects=None,
                    n_ops=0,
                    similarity=None,
                    comparison_class=None,
                    oracle_available=False,
                    n_provisions=0,
                    n_versions=None,
                    pit_materialized_eids=None,
                    timeline_mode="states_first",
                    enacted_url=enacted_url,
                    oracle_url=oracle_url,
                    enacted_source_status=enacted_source_status,
                    oracle_source_status=oracle_source_status,
                    enacted_source_size=enacted_source_size,
                    oracle_source_size=oracle_source_size,
                    enacted_source_sha256=enacted_source_sha256,
                    oracle_source_sha256=oracle_source_sha256,
                    source_parse_rejections=source_parse_rejections,
                    uk_replay_regime=_uk_replay_regime_payload(
                        enacted_only=enacted_only,
                        oracle_alignment_enabled=False,
                        metadata_backfill_op_count=0,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_metadata_only_effects=allow_metadata_only_effects,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                        source_unavailable_reason="enacted_xml_parse_rejected",
                    ),
                    uk_oracle_alignment_summary=_uk_replay_executor_oracle_alignment_summary(
                        enabled=False,
                        events=(),
                        oracle_eids=set(),
                        unavailable_reason="enacted_xml_parse_rejected",
                    ),
                    uk_prefetch_report=uk_prefetch_report,
                    error=error,
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"error: {error}", file=sys.stderr)
            sys.exit(1)
        base_eids = _get_all_eids([base_ir.body], pit_date=pit_date)
        for schedule in base_ir.supplements:
            base_eids.update(_get_all_eids([schedule], pit_date=pit_date))
        _out(f"Base EIDs: {len(base_eids)}")

        eid_map: dict[str, str] = {}
        text_map: dict[str, str] = {}
        oracle_physical_eid_aliases: dict[str, str] = {}
        oracle_visible_number_eid_aliases: dict[str, str] = {}
        current_ir = None
        current_eids: Set[str] = set()
        effect_count_parse_rejections: list[dict[str, object]] = []
        n_effects = len(
            load_effects_for_statute_from_archive(
                statute_id,
                archive,
                parse_rejections_out=effect_count_parse_rejections,
            )
        )
        if enacted_only:
            effect_feed_parse_rejections.extend(effect_count_parse_rejections)
        comparison_class = ""
        core_benchmark = False

        if oracle_source_status == "available":
            assert oracle_bytes is not None
            if verbose:
                print(
                    f"Extracting oracle EID map from archive: {oracle_url} (PIT: {pit_date or 'latest'})",
                    file=sys.stderr,
                )
            try:
                oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=pit_date)
                current_ir = parse_uk_statute_ir_bytes(
                    oracle_bytes,
                    statute_id=statute_id,
                    version_label="oracle",
                    pit_date=pit_date,
                    source_path=oracle_url,
                )
                source_parse_rejections.extend(uk_source_parse_observations_from_ir(current_ir))
            except Exception as exc:
                source_parse_rejections.append(
                    uk_source_xml_parse_rejection(
                        statute_id=statute_id,
                        side="oracle",
                        source_url=oracle_url,
                        exc=exc,
                    )
                )
            else:
                eid_map = oracle_data.get("eid_map", {})
                text_map = oracle_data.get("text_map", {})
                oracle_physical_eid_aliases = oracle_data.get("physical_eid_aliases", {})
                oracle_visible_number_eid_aliases = oracle_data.get("visible_number_eid_aliases", {})
                current_eids = set(eid_map.values())
                if verbose:
                    print(f"Oracle EID map entries: {len(eid_map)}", file=sys.stderr)
                enacted_oracle_score = len(base_eids & current_eids) / max(
                    len(base_eids),
                    len(current_eids),
                    1,
                )
                comparison_class = classify_uk_bench_comparison(
                    n_enacted_eids=len(base_eids),
                    n_oracle_eids=len(current_eids),
                    n_effects=n_effects,
                    raw_score=enacted_oracle_score,
                )
                current_projection_shape = classify_uk_current_projection_eid_shape(
                    enacted_eids=base_eids,
                    oracle_eids=current_eids,
                )
                if (
                    current_projection_shape
                    and comparison_class == "commensurable"
                    and enacted_oracle_score < 1.0
                ):
                    comparison_class = current_projection_shape
                core_benchmark = is_core_uk_comparison(comparison_class)

        # ── 3. Replay ─────────────────────────────────────────────────────
        if enacted_only:
            _out("\n--- Baseline mode: enacted vs enacted ---")
            replayed_ir = base_ir
            lo_ops_out = None
        else:
            pipeline_cls = uk_replay_module.UKReplayPipeline
            pipeline = pipeline_cls(_REPO_ROOT)
            ops = pipeline.compile_ops_for_statute(
                statute_id,
                pit_date=pit_date,
                archive=archive,
                allow_metadata_backfill=allow_metadata_backfill,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
                allow_metadata_only_effects=allow_metadata_only_effects,
                effect_feed_parse_rejections_out=effect_feed_parse_rejections,
                effect_diagnostics_out=effect_diagnostics,
                lowering_rejections_out=lowering_rejections,
                authority_rejections_out=authority_rejections,
            )
            effect_source_pathology_observations = [
                row
                for row in effect_diagnostics
                if str(row.get("rule_id") or "") == "uk_effect_source_pathology_classified"
            ]
            manual_compile_frontier_observations = [
                row
                for row in effect_diagnostics
                if str(row.get("rule_id") or "") == "uk_manual_compile_frontier_classified"
            ]
            source_acquisition_rejections = [
                row
                for row in effect_diagnostics
                if is_uk_affecting_act_xml_source_observation(row)
            ]
            n_ops = len(ops)
            _out(f"Compiled {n_ops} operations")
            for line in _uk_compile_rejection_text_lines(
                effect_feed_parse_rejections=effect_feed_parse_rejections,
                effect_source_pathology_observations=effect_source_pathology_observations,
                manual_compile_frontier_observations=manual_compile_frontier_observations,
                source_acquisition_rejections=source_acquisition_rejections,
                lowering_rejections=lowering_rejections,
                authority_rejections=authority_rejections,
            ):
                _out(line)
            if verbose:
                for op in ops:
                    kind = op.payload.kind if op.payload is not None else "none"
                    print(f"  Op {op.op_id}: {op.action} {op.target} -> IR kind: {kind}", file=sys.stderr)

            lo_ops_out = [] if use_timeline else None
            replayed_ir = pipeline.apply_ops(
                base_ir,
                ops,
                eid_map=eid_map,
                text_map=text_map,
                allow_oracle_alignment=allow_oracle_alignment,
                verbose=verbose,
                lo_ops_out=lo_ops_out,
                adjudications_out=replay_adjudications,
                oracle_alignment_events_out=oracle_alignment_events,
            )

        # ── 4. EID similarity score ───────────────────────────────────────
        replayed_eids = _get_all_eids([replayed_ir.body], pit_date=pit_date)
        for schedule in replayed_ir.supplements:
            replayed_eids.update(_get_all_eids([schedule], pit_date=pit_date))
        _out(f"Replayed EIDs: {len(replayed_eids)}")

        if current_ir is not None:
            _out(f"Oracle EIDs: {len(current_eids)}")
            replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                replayed_eids,
                current_eids,
                oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
            )
            common = replay_compare_eids & oracle_compare_eids
            similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)
            replay_compare_eid_count = len(replay_compare_eids)
            oracle_compare_eid_count = len(oracle_compare_eids)
            common_eid_count = len(common)
            _out(f"Full EID Similarity: {similarity:.1%}")
            only_in_replayed = replay_compare_eids - oracle_compare_eids
            only_in_oracle = oracle_compare_eids - replay_compare_eids
            only_in_replayed_count = len(only_in_replayed)
            only_in_oracle_count = len(only_in_oracle)
            if only_in_replayed:
                only_in_replayed_sample = sorted(only_in_replayed)[:10]
                _out(f"Only in replayed ({only_in_replayed_count}): {only_in_replayed_sample}")
            if only_in_oracle:
                only_in_oracle_sample = sorted(only_in_oracle)[:10]
                _out(f"Only in oracle ({only_in_oracle_count}): {only_in_oracle_sample}")
            effect_source_pathology_counts = dict(
                Counter(
                    str(row.get("source_pathology") or "__none__")
                    for row in effect_source_pathology_observations
                    if row.get("rule_id") == "uk_effect_source_pathology_classified"
                )
            )
            comparison_class = classify_uk_bench_comparison(
                n_enacted_eids=len(base_eids),
                n_oracle_eids=len(current_eids),
                n_effects=n_effects,
                raw_score=similarity,
                effect_source_pathology_counts=effect_source_pathology_counts,
            )
            current_projection_shape = classify_uk_current_projection_eid_shape(
                enacted_eids=base_eids,
                oracle_eids=current_eids,
            )
            if (
                current_projection_shape
                and comparison_class == "commensurable"
                and similarity < 1.0
            ):
                comparison_class = current_projection_shape
            core_benchmark = is_core_uk_comparison(comparison_class)
        else:
            _out(f"Note: no oracle XML in archive for {oracle_url}.")

        if score_commencement:
            if current_ir is None:
                uk_commencement_summary = _uk_commencement_score_summary(
                    enabled=True,
                    applicability_mode=applicability_mode,
                    unavailable_reason="oracle_xml_unavailable_or_parse_rejected",
                )
            else:
                commencement_observations: list[dict[str, Any]] = []
                all_effects = load_effects_for_statute_from_archive(
                    statute_id,
                    archive,
                    parse_rejections_out=commencement_observations,
                )
                commenced = uk_replay_module.commencement_eid_set(
                    all_effects,
                    base_ir,
                    applicability_mode=applicability_mode,
                    observations_out=commencement_observations,
                )
                commenced_enacted = base_eids & commenced
                commenced_replayed_raw = replayed_eids & commenced
                commenced_oracle = _commenced_oracle_eids(current_eids, commenced)
                commenced_replayed, commenced_oracle_for_replay = normalize_uk_replay_compare_eids(
                    commenced_replayed_raw,
                    commenced_oracle,
                    oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                    oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
                )
                uk_commencement_summary = _uk_commencement_score_summary(
                    enabled=True,
                    applicability_mode=applicability_mode,
                    observations=commencement_observations,
                    commenced_eids=commenced,
                    commenced_enacted_eids=commenced_enacted,
                    commenced_replayed_eids=commenced_replayed,
                    commenced_oracle_eids=commenced_oracle,
                    replay_commencement_oracle_eids=commenced_oracle_for_replay,
                )
                commencement_projection_shape = classify_uk_commencement_current_projection(
                    replay_compare_eids=replay_compare_eids,
                    oracle_compare_eids=oracle_compare_eids,
                    commenced_replay_eids=commenced_replayed,
                    commenced_oracle_eids=commenced_oracle_for_replay,
                )
                if commencement_projection_shape and comparison_class == "commensurable":
                    comparison_class = commencement_projection_shape
                    core_benchmark = is_core_uk_comparison(comparison_class)

        if current_ir is not None and comparison_class:
            _out(f"Comparison class: {comparison_class}  core={'yes' if core_benchmark else 'no'}")

    # ── 5. Timeline compilation ───────────────────────────────────────────
    # Two paths:
    #
    # Default (states-first / ingest_uk_snapshots):
    #   Build timelines from enacted + replayed snapshots. Simple structural
    #   diff — any provision that changed between the two snapshots gets a new
    #   version. Accurate for "what changed overall" but loses per-op granularity.
    #
    # --timeline (ops-first / compile_timelines):
    #   Use the lo_ops_out snapshots collected during apply_ops. Each structural
    #   op emits a top-section snapshot immediately after application, so
    #   compile_timelines sees fine-grained per-op versions with proper source
    #   provenance (affecting act ID + effective date). Mirrors the Finland path.

    parts = statute_id.split("/")
    enacted_year = parts[1] if len(parts) >= 3 else "1900"
    enacted_date = f"{enacted_year}-01-01"

    if use_timeline and lo_ops_out is not None and not enacted_only:
        # Ops-first path: compile_timelines from section snapshots
        temporal_events = _uk_temporal_events_from_ops(
            lo_ops_out,
            target_statute=statute_id,
        )
        timelines = compile_timelines(
            base_ir,
            lo_ops_out,
            base_date=enacted_date,
            temporal_events=temporal_events,
        )
        n_provisions = len(timelines)
        n_versions = sum(len(tl.versions) for tl in timelines.values())
        n_snapshots = len(lo_ops_out)
        _out(f"\n[ops-first] Snapshots collected: {n_snapshots}")
        _out(f"[ops-first] Timelines: {n_provisions} provisions, {n_versions} total versions")

        # Per-provision version count summary
        multi_version = {addr: len(tl.versions) for addr, tl in timelines.items() if len(tl.versions) > 1}
        if multi_version:
            # Sort by version count descending, print top 10
            top = sorted(multi_version.items(), key=lambda kv: -kv[1])[:10]
            _out(f"[ops-first] Provisions with multiple versions (top {len(top)}):")
            for addr, count in top:
                addr_str = "/".join(f"{k}:{lbl}" for k, lbl in addr.path)
                _out(f"  {addr_str}: {count} versions")
    else:
        # States-first path (default): ingest_uk_snapshots
        snapshots: dict[str, "IRStatute"] = {}
        snapshots[enacted_date] = base_ir

        if not enacted_only:
            # Use today or pit_date as the replayed date
            if pit_date:
                replay_date = pit_date
            else:
                import datetime

                replay_date = datetime.date.today().isoformat()
            snapshots[replay_date] = replayed_ir

        timelines = ingest_uk_snapshots(statute_id, snapshots)
        n_provisions = len(timelines)
        n_versions = sum(len(tl.versions) for tl in timelines.values())
        _out(f"\nTimelines: {n_provisions} provisions, {n_versions} total versions")

    # Materialize PIT if date given
    if pit_date and timelines:
        pit_statute = materialize_pit(timelines, pit_date, base=base_ir)
        pit_eids = _get_all_eids([pit_statute.body], pit_date=pit_date)
        _out(f"Materialized PIT ({pit_date}): {len(pit_eids)} EIDs in body")
    else:
        pit_eids = None

    oracle_alignment_enabled = bool(not enacted_only and current_ir is not None and allow_oracle_alignment)
    uk_replay_regime = _uk_replay_regime_payload(
        enacted_only=enacted_only,
        oracle_alignment_enabled=oracle_alignment_enabled,
        metadata_backfill_op_count=_uk_metadata_backfill_op_count(ops if not enacted_only else ()),
        allow_metadata_backfill=allow_metadata_backfill,
        allow_metadata_only_effects=allow_metadata_only_effects,
        applicability_mode=applicability_mode,
        authority_mode=authority_mode,
    )
    unavailable_reason = ""
    if enacted_only:
        unavailable_reason = "enacted_only_baseline"
    elif not allow_oracle_alignment:
        unavailable_reason = "oracle_alignment_disabled_by_regime"
    elif current_ir is None:
        oracle_parse_failed = any(
            str(row.get("side") or "") == "oracle"
            for row in source_parse_rejections
        )
        unavailable_reason = "oracle_xml_parse_rejected" if oracle_parse_failed else "oracle_xml_unavailable"
    uk_oracle_alignment_summary = _uk_replay_executor_oracle_alignment_summary(
        enabled=oracle_alignment_enabled,
        events=tuple(oracle_alignment_events),
        oracle_eids=current_eids,
        unavailable_reason=unavailable_reason,
    )

    if as_json:
        payload = build_uk_replay_payload(
            statute_id=statute_id,
            pit_date=pit_date,
            enacted_only=enacted_only,
            db_path=str(db_path),
            n_effects=n_effects,
            n_ops=n_ops,
            similarity=similarity,
            comparison_class=comparison_class or None,
            oracle_available=current_ir is not None,
            n_provisions=n_provisions,
            n_versions=n_versions if timelines else None,
            pit_materialized_eids=len(pit_eids) if pit_eids is not None else None,
            timeline_mode="ops_first" if use_timeline and not enacted_only else "states_first",
            enacted_url=enacted_url,
            oracle_url=oracle_url,
            enacted_source_status=enacted_source_status,
            oracle_source_status=oracle_source_status,
            enacted_source_size=enacted_source_size,
            oracle_source_size=oracle_source_size,
            enacted_source_sha256=enacted_source_sha256,
            oracle_source_sha256=oracle_source_sha256,
            base_eid_count=len(base_eids),
            replayed_eid_count=len(replayed_eids),
            oracle_eid_count=len(current_eids) if current_ir is not None else None,
            replay_compare_eid_count=replay_compare_eid_count,
            oracle_compare_eid_count=oracle_compare_eid_count,
            common_eid_count=common_eid_count,
            only_in_replayed_count=only_in_replayed_count,
            only_in_oracle_count=only_in_oracle_count,
            only_in_replayed_sample=only_in_replayed_sample,
            only_in_oracle_sample=only_in_oracle_sample,
            core_benchmark=core_benchmark if comparison_class else None,
            adjudications=replay_adjudications,
            source_parse_rejections=source_parse_rejections,
            effect_feed_parse_rejections=effect_feed_parse_rejections,
            effect_source_pathology_observations=effect_source_pathology_observations,
            manual_compile_frontier_observations=manual_compile_frontier_observations,
            source_acquisition_rejections=source_acquisition_rejections,
            lowering_rejections=lowering_rejections,
            authority_rejections=authority_rejections,
            uk_replay_regime=uk_replay_regime,
            uk_oracle_alignment_summary=uk_oracle_alignment_summary,
            uk_commencement_summary=uk_commencement_summary,
            uk_prefetch_report=uk_prefetch_report,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # ── 6. Summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"Statute:    {statute_id}")
    print(f"Mode:       {'enacted-only' if enacted_only else 'full replay'}")
    if pit_date:
        print(f"PIT date:   {pit_date}")
    print(f"Ops:        {n_ops}")
    if similarity is not None:
        print(f"EID score:  {similarity:.1%}")
    print(f"Timelines:  {n_provisions} provisions")
    print(
        "Source:     "
        f"enacted={enacted_source_status} ({enacted_source_size} bytes) "
        f"oracle={oracle_source_status} ({oracle_source_size} bytes)"
    )
    print(f"Enacted URL: {enacted_url}")
    print(f"Oracle URL: {oracle_url}")
    print(f"Enacted SHA-256: {enacted_source_sha256 or '(none)'}")
    print(f"Oracle SHA-256: {oracle_source_sha256 or '(none)'}")
    print(
        "Regime:     "
        f"metadata_backfill={allow_metadata_backfill} "
        f"oracle_alignment={allow_oracle_alignment} "
        f"metadata_only_effects={allow_metadata_only_effects} "
        f"applicability={applicability_mode} "
        f"authority={authority_mode}"
    )
    for line in _uk_prefetch_text_lines(uk_prefetch_report):
        print(line)
    for line in _uk_compile_rejection_text_lines(
        source_parse_rejections=source_parse_rejections,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        effect_source_pathology_observations=effect_source_pathology_observations,
        manual_compile_frontier_observations=manual_compile_frontier_observations,
        source_acquisition_rejections=source_acquisition_rejections,
        lowering_rejections=lowering_rejections,
        authority_rejections=authority_rejections,
    ):
        print(line)
    for line in _uk_replay_adjudication_text_lines(
        replay_adjudications,
        sample_kinds=replay_adjudication_sample_kinds,
        sample_limit=replay_adjudication_sample_limit,
    ):
        print(line)
    for line in _uk_oracle_alignment_text_lines(uk_oracle_alignment_summary):
        print(line)
    for line in _uk_commencement_score_text_lines(uk_commencement_summary):
        print(line)
    if replay_compare_eid_count is not None:
        print(
            "EID compare: "
            f"replay={replay_compare_eid_count} "
            f"oracle={oracle_compare_eid_count} "
            f"common={common_eid_count} "
            f"only_replay={only_in_replayed_count} "
            f"only_oracle={only_in_oracle_count}"
        )
    print(f"{'=' * 60}")
