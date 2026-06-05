#!/usr/bin/env python3
"""Rank UK oracle-suspect leads from a broad-baseline evidence report.

This is an investigation surface, not replay authority. It consumes the
EvidenceSurfaceReport emitted by ``scripts/uk_broad_baseline.py --out-report``
and extracts rows where LawVM has enough proof footing to make a useful
consolidation-error lead, or where the row is a source-chain lead that must not
be promoted without further source proof.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_EXCLUDED_TRIAGE_PREFIXES = ("source_frontier:",)
_EXCLUDED_TRIAGE_BUCKETS = frozenset(
    {
        "body_oracle_first_paragraph_sectionization_residual",
        "effect_feed_absent_frontier",
        "manual_compile_frontier_residual",
        "no_compiled_ops_frontier",
        "no_effect_rows_frontier",
        "nonreplay_effect_frontier",
        "zero_oracle_retention",
    }
)
_HIGH_CONFIDENCE = "high"
_MEDIUM_CONFIDENCE = "medium"
_SOURCE_CHAIN_LEAD = "source_chain_lead"
_CONFIDENCE_ORDER = {
    _SOURCE_CHAIN_LEAD: 0,
    _MEDIUM_CONFIDENCE: 1,
    _HIGH_CONFIDENCE: 2,
}


@dataclass(frozen=True)
class UKOracleSuspectCandidate:
    statute_id: str
    candidate_family: str
    confidence: str
    rank: int
    triage_bucket: str
    owner_phase: str
    aligned: float
    replay_eids: int
    oracle_eids: int
    only_in_replayed: int
    only_in_oracle: int
    replay_only_samples: tuple[str, ...]
    oracle_only_samples: tuple[str, ...]
    retained_repeal_targets: tuple[str, ...]
    missing_proofs: tuple[str, ...]
    forbidden_shortcuts: tuple[str, ...]
    safe_default: str
    reason: str


def load_candidates(
    report_path: Path,
    *,
    min_confidence: str = _SOURCE_CHAIN_LEAD,
) -> list[UKOracleSuspectCandidate]:
    """Load and rank candidates from a broad-baseline evidence report."""

    threshold = _CONFIDENCE_ORDER[min_confidence]
    report = json.loads(report_path.read_text())
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{report_path} does not contain a broad-baseline row list")
    candidates = [
        candidate
        for row in rows
        if isinstance(row, Mapping)
        for candidate in _candidate_from_row(row)
        if _CONFIDENCE_ORDER[candidate.confidence] >= threshold
    ]
    return sorted(candidates, key=_candidate_sort_key)


def _candidate_from_row(row: Mapping[str, Any]) -> tuple[UKOracleSuspectCandidate, ...]:
    triage_bucket = str(row.get("triage_bucket") or "")
    if _is_excluded_bucket(triage_bucket):
        return ()

    residual = _mapping(row.get("agreement_residual"))
    missing_proofs = _string_tuple(residual.get("missing_proofs"))
    forbidden_shortcuts = _string_tuple(residual.get("forbidden_shortcuts"))
    owner_phase = str(residual.get("owner_phase") or "")
    safe_default = str(residual.get("safe_default") or "")
    statute_id = str(row.get("statute_id") or residual.get("source_artifact_id") or "")
    aligned = _float(row.get("aligned"))
    replay_eids = _int(row.get("n_replay") or residual.get("replay_count"))
    oracle_eids = _int(row.get("n_oracle") or residual.get("oracle_count"))
    only_in_replayed = _int(row.get("n_only_in_replayed"))
    only_in_oracle = _int(row.get("n_only_in_oracle"))
    replay_only_samples = _string_tuple(row.get("replay_only_eid_samples"))
    oracle_only_samples = _string_tuple(row.get("oracle_only_eid_samples"))
    retained_targets = _string_tuple(row.get("retained_repeal_oracle_targets"))

    if triage_bucket == "retained_repeal_oracle_branch" and retained_targets:
        return (
            UKOracleSuspectCandidate(
                statute_id=statute_id,
                candidate_family="oracle_retains_source_repealed_state",
                confidence=_HIGH_CONFIDENCE if not missing_proofs else _MEDIUM_CONFIDENCE,
                rank=100 if not missing_proofs else 80,
                triage_bucket=triage_bucket,
                owner_phase=owner_phase,
                aligned=aligned,
                replay_eids=replay_eids,
                oracle_eids=oracle_eids,
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
                replay_only_samples=replay_only_samples,
                oracle_only_samples=oracle_only_samples,
                retained_repeal_targets=retained_targets,
                missing_proofs=missing_proofs,
                forbidden_shortcuts=forbidden_shortcuts,
                safe_default=safe_default,
                reason=(
                    "Replay removed or omitted state under a source-backed repeal "
                    "while the current oracle still exposes the target branch."
                ),
            ),
        )

    if triage_bucket == "oracle_expansion_without_effects":
        return (
            UKOracleSuspectCandidate(
                statute_id=statute_id,
                candidate_family="oracle_addition_without_compiled_source_chain",
                confidence=_SOURCE_CHAIN_LEAD,
                rank=30,
                triage_bucket=triage_bucket,
                owner_phase=owner_phase,
                aligned=aligned,
                replay_eids=replay_eids,
                oracle_eids=oracle_eids,
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
                replay_only_samples=replay_only_samples,
                oracle_only_samples=oracle_only_samples,
                retained_repeal_targets=retained_targets,
                missing_proofs=missing_proofs,
                forbidden_shortcuts=forbidden_shortcuts,
                safe_default=safe_default,
                reason=(
                    "The oracle exposes extra EIDs but the report lacks source-chain "
                    "proof; investigate source identity before treating this as an "
                    "oracle error."
                ),
            ),
        )

    if _has_clean_boundary(row) and only_in_replayed > 0 and only_in_oracle == 0:
        return (
            UKOracleSuspectCandidate(
                statute_id=statute_id,
                candidate_family="oracle_missing_source_backed_replay_state",
                confidence=_HIGH_CONFIDENCE,
                rank=90,
                triage_bucket=triage_bucket,
                owner_phase=owner_phase,
                aligned=aligned,
                replay_eids=replay_eids,
                oracle_eids=oracle_eids,
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
                replay_only_samples=replay_only_samples,
                oracle_only_samples=oracle_only_samples,
                retained_repeal_targets=retained_targets,
                missing_proofs=missing_proofs,
                forbidden_shortcuts=forbidden_shortcuts,
                safe_default=safe_default,
                reason=(
                    "Replay produced source-backed EIDs that the current oracle lacks, "
                    "with no blocking compile rejection or unexplained mutation boundary."
                ),
            ),
        )

    if _has_clean_boundary(row) and only_in_oracle > 0 and only_in_replayed == 0:
        return (
            UKOracleSuspectCandidate(
                statute_id=statute_id,
                candidate_family="oracle_extra_state_without_replay_residual",
                confidence=_MEDIUM_CONFIDENCE,
                rank=60,
                triage_bucket=triage_bucket,
                owner_phase=owner_phase,
                aligned=aligned,
                replay_eids=replay_eids,
                oracle_eids=oracle_eids,
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
                replay_only_samples=replay_only_samples,
                oracle_only_samples=oracle_only_samples,
                retained_repeal_targets=retained_targets,
                missing_proofs=missing_proofs,
                forbidden_shortcuts=forbidden_shortcuts,
                safe_default=safe_default,
                reason=(
                    "The current oracle has extra EIDs, while the report has no "
                    "blocking compile rejection or unexplained mutation boundary; "
                    "confirm source-chain completeness before escalation."
                ),
            ),
        )

    return ()


def _is_excluded_bucket(triage_bucket: str) -> bool:
    return (
        triage_bucket in _EXCLUDED_TRIAGE_BUCKETS
        or triage_bucket.startswith(_EXCLUDED_TRIAGE_PREFIXES)
    )


def _has_clean_boundary(row: Mapping[str, Any]) -> bool:
    source_chain_reasons = row.get("source_chain_frontier_reasons") or ()
    return (
        int(row.get("n_blocking_compile_rejections") or 0) == 0
        and int(row.get("n_mutation_boundary_unexplained_reports") or 0) == 0
        and int(row.get("n_mutation_boundary_unexplained_paths") or 0) == 0
        and not source_chain_reasons
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if str(item))


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(float(value))
    return 0


def _float(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return 0.0


def _candidate_sort_key(candidate: UKOracleSuspectCandidate) -> tuple[int, int, float, str]:
    disagreement = candidate.only_in_replayed + candidate.only_in_oracle
    return (-candidate.rank, -disagreement, candidate.aligned, candidate.statute_id)


def _summary(candidates: Sequence[UKOracleSuspectCandidate]) -> dict[str, Any]:
    counts_by_family: dict[str, int] = {}
    counts_by_confidence: dict[str, int] = {}
    for candidate in candidates:
        counts_by_family[candidate.candidate_family] = (
            counts_by_family.get(candidate.candidate_family, 0) + 1
        )
        counts_by_confidence[candidate.confidence] = (
            counts_by_confidence.get(candidate.confidence, 0) + 1
        )
    return {
        "candidate_count": len(candidates),
        "candidate_family_counts": dict(sorted(counts_by_family.items())),
        "confidence_counts": dict(sorted(counts_by_confidence.items())),
        "truth_claim": "oracle_suspect_candidate_report_not_source_truth",
        "forbidden_shortcuts": [
            "oracle_score_as_source_truth",
            "candidate_as_replay_authorization",
            "source_or_target_over_promotion",
        ],
    }


def _emit_json(candidates: Sequence[UKOracleSuspectCandidate]) -> str:
    payload = {
        "report_kind": "uk_oracle_suspect_candidates.v1",
        "summary": _summary(candidates),
        "rows": [asdict(candidate) for candidate in candidates],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _emit_jsonl(candidates: Sequence[UKOracleSuspectCandidate]) -> str:
    return "\n".join(
        json.dumps(asdict(candidate), sort_keys=True) for candidate in candidates
    )


def _emit_tsv(candidates: Sequence[UKOracleSuspectCandidate]) -> str:
    header = (
        "statute_id",
        "confidence",
        "rank",
        "candidate_family",
        "triage_bucket",
        "aligned",
        "only_in_replayed",
        "only_in_oracle",
        "replay_only_samples",
        "oracle_only_samples",
        "missing_proofs",
    )
    rows = ["\t".join(header)]
    for candidate in candidates:
        rows.append(
            "\t".join(
                (
                    candidate.statute_id,
                    candidate.confidence,
                    str(candidate.rank),
                    candidate.candidate_family,
                    candidate.triage_bucket,
                    f"{candidate.aligned:.2f}",
                    str(candidate.only_in_replayed),
                    str(candidate.only_in_oracle),
                    ",".join(candidate.replay_only_samples),
                    ",".join(candidate.oracle_only_samples),
                    ",".join(candidate.missing_proofs),
                )
            )
        )
    return "\n".join(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank UK oracle-suspect leads from a broad-baseline report."
    )
    parser.add_argument("report", type=Path, help="Broad-baseline .report.json path")
    parser.add_argument(
        "--format",
        choices=("json", "jsonl", "tsv"),
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--min-confidence",
        choices=tuple(_CONFIDENCE_ORDER),
        default=_SOURCE_CHAIN_LEAD,
        help="Minimum confidence to emit",
    )
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to emit")
    args = parser.parse_args(argv)

    candidates = load_candidates(args.report, min_confidence=args.min_confidence)
    if args.limit > 0:
        candidates = candidates[: args.limit]
    if args.format == "jsonl":
        print(_emit_jsonl(candidates))
    elif args.format == "tsv":
        print(_emit_tsv(candidates))
    else:
        print(_emit_json(candidates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
