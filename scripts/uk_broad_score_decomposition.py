#!/usr/bin/env python3
"""Decompose UK broad-baseline score drag by triage bucket.

This is an agreement/reporting surface, not replay authority. It consumes the
EvidenceSurfaceReport emitted by ``scripts/uk_broad_baseline.py --out-report``
and explains which already-classified row families pull the aggregate mean away
from the high-fidelity lane.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


_DEFAULT_REFERENCE_BUCKET = "high_fidelity_after_grounding"


@dataclass(frozen=True)
class UKBroadScoreBucket:
    triage_bucket: str
    row_count: int
    mean_aligned: float | None
    loss_points_vs_reference: float | None
    n_replay: int
    n_oracle: int
    n_common: int
    n_only_in_replayed: int
    n_only_in_oracle: int
    n_effects: int
    n_ops: int
    n_manual_frontier_records: int
    n_blocking_compile_rejections: int


def load_decomposition(
    report_path: Path,
    *,
    reference_bucket: str = _DEFAULT_REFERENCE_BUCKET,
    reference_score: float | None = None,
) -> dict[str, Any]:
    """Load a report and return score-drag rows grouped by triage bucket."""

    report = json.loads(report_path.read_text())
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{report_path} does not contain a broad-baseline row list")

    scored_rows = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("score_status") or "") == "scored"
        and _float_or_none(row.get("aligned")) is not None
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in scored_rows:
        grouped.setdefault(str(row.get("triage_bucket") or "unknown"), []).append(row)

    resolved_reference_score = reference_score
    if resolved_reference_score is None:
        reference_rows = grouped.get(reference_bucket) or scored_rows
        resolved_reference_score = _mean_aligned(reference_rows) or 0.0

    buckets = [
        _bucket_summary(bucket, bucket_rows, resolved_reference_score, len(scored_rows))
        for bucket, bucket_rows in grouped.items()
    ]
    buckets.sort(key=_bucket_sort_key)

    scored_mean = _mean_aligned(scored_rows)
    source_frontier_count = sum(
        1
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("score_status") or "") == "source_frontier"
    )
    return {
        "report_kind": "uk_broad_score_decomposition.v1",
        "truth_claim": "score_decomposition_report_not_source_truth",
        "safe_default": "use_for_work_selection_not_replay_authorization",
        "forbidden_shortcuts": [
            "score_bucket_as_replay_authorization",
            "oracle_score_as_source_truth",
            "mean_loss_as_manual_compile_claim",
            "source_or_target_over_promotion",
        ],
        "summary": {
            "row_count": len(rows),
            "scored_count": len(scored_rows),
            "source_frontier_count": source_frontier_count,
            "scored_mean_aligned": scored_mean,
            "reference_bucket": reference_bucket,
            "reference_score": resolved_reference_score,
            "loss_points_to_reference": (
                resolved_reference_score - scored_mean
                if scored_mean is not None
                else None
            ),
        },
        "buckets": [asdict(bucket) for bucket in buckets],
    }


def _bucket_summary(
    triage_bucket: str,
    rows: Sequence[Mapping[str, Any]],
    reference_score: float,
    scored_count: int,
) -> UKBroadScoreBucket:
    mean_aligned = _mean_aligned(rows)
    loss_points = None
    if mean_aligned is not None and scored_count > 0:
        loss_points = len(rows) * (reference_score - mean_aligned) / scored_count
    return UKBroadScoreBucket(
        triage_bucket=triage_bucket,
        row_count=len(rows),
        mean_aligned=mean_aligned,
        loss_points_vs_reference=loss_points,
        n_replay=_sum_int(rows, "n_replay"),
        n_oracle=_sum_int(rows, "n_oracle"),
        n_common=_sum_int(rows, "n_common"),
        n_only_in_replayed=_sum_int(rows, "n_only_in_replayed"),
        n_only_in_oracle=_sum_int(rows, "n_only_in_oracle"),
        n_effects=_sum_int(rows, "n_effects"),
        n_ops=_sum_int(rows, "n_ops"),
        n_manual_frontier_records=_sum_int(rows, "n_manual_frontier_records"),
        n_blocking_compile_rejections=_sum_int(
            rows,
            "n_blocking_compile_rejections",
        ),
    )


def _bucket_sort_key(bucket: UKBroadScoreBucket) -> tuple[float, int, str]:
    loss = bucket.loss_points_vs_reference
    return (-(loss or 0.0), -bucket.row_count, bucket.triage_bucket)


def _mean_aligned(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = [
        value
        for row in rows
        for value in [_float_or_none(row.get("aligned"))]
        if value is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _sum_int(rows: Sequence[Mapping[str, Any]], field: str) -> int:
    return sum(_int(row.get(field)) for row in rows)


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


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return None


def _emit_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _emit_tsv(payload: Mapping[str, Any]) -> str:
    header = (
        "triage_bucket",
        "row_count",
        "mean_aligned",
        "loss_points_vs_reference",
        "n_replay",
        "n_oracle",
        "n_only_in_replayed",
        "n_only_in_oracle",
        "n_effects",
        "n_ops",
        "n_manual_frontier_records",
        "n_blocking_compile_rejections",
    )
    lines: list[str] = ["\t".join(header)]
    for bucket in payload["buckets"]:
        mean_aligned = bucket["mean_aligned"]
        loss_points = bucket["loss_points_vs_reference"]
        lines.append(
            "\t".join(
                (
                    str(bucket["triage_bucket"]),
                    str(bucket["row_count"]),
                    "" if mean_aligned is None else f"{mean_aligned:.4f}",
                    "" if loss_points is None else f"{loss_points:.4f}",
                    str(bucket["n_replay"]),
                    str(bucket["n_oracle"]),
                    str(bucket["n_only_in_replayed"]),
                    str(bucket["n_only_in_oracle"]),
                    str(bucket["n_effects"]),
                    str(bucket["n_ops"]),
                    str(bucket["n_manual_frontier_records"]),
                    str(bucket["n_blocking_compile_rejections"]),
                )
            )
        )
    return "\n".join(lines)


def _emit_text(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "UK broad-baseline score decomposition",
        f"  scored={summary['scored_count']} source_frontier={summary['source_frontier_count']}",
        f"  scored_mean_aligned={summary['scored_mean_aligned']:.4f}",
        (
            "  reference="
            f"{summary['reference_bucket']}:{summary['reference_score']:.4f} "
            f"loss={summary['loss_points_to_reference']:.4f}"
        ),
        "  top bucket losses:",
    ]
    for bucket in payload["buckets"][:12]:
        mean_aligned = bucket["mean_aligned"]
        loss_points = bucket["loss_points_vs_reference"]
        mean = "n/a" if mean_aligned is None else f"{mean_aligned:.2f}"
        loss = "n/a" if loss_points is None else f"{loss_points:.2f}"
        lines.append(
            "    "
            f"{bucket['triage_bucket']}: n={bucket['row_count']} "
            f"mean={mean} loss={loss}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decompose UK broad-baseline score drag by triage bucket."
    )
    parser.add_argument("report", type=Path, help="Broad-baseline .report.json path")
    parser.add_argument(
        "--format",
        choices=("json", "tsv", "text"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--reference-bucket",
        default=_DEFAULT_REFERENCE_BUCKET,
        help="Bucket used as the no-drag reference score",
    )
    parser.add_argument(
        "--reference-score",
        type=float,
        default=None,
        help="Explicit no-drag reference score; overrides --reference-bucket",
    )
    args = parser.parse_args(argv)

    payload = load_decomposition(
        args.report,
        reference_bucket=args.reference_bucket,
        reference_score=args.reference_score,
    )
    if args.format == "json":
        print(_emit_json(payload))
    elif args.format == "tsv":
        print(_emit_tsv(payload))
    else:
        print(_emit_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
