"""CLI for Finland citation-bearing-sentence grammar differential census."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from typing import Any

from lawvm.finland.legal_surface.sentence_census import (
    SENTENCE_FAMILY,
    SentenceCensusResult,
    format_sentence_census_report,
    run_sentence_census,
)


def _result_to_json(result: SentenceCensusResult) -> dict[str, Any]:
    def _rows(rows: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [
            {
                "statute_id": row.statute_id,
                "bucket": row.bucket,
                "projection_keys": list(row.projection_keys),
                "oracle_keys": list(row.oracle_keys),
                "missing_keys": list(row.missing_keys),
                "extra_keys": list(row.extra_keys),
                "declared_marker": row.declared_marker,
                "parser_lane": row.parser_lane,
                "parser_facade_lane": row.parser_facade_lane,
                "totality_ok": row.totality_ok,
                "text": row.text,
            }
            for row in rows
        ]

    return {
        "catalog_kind": "finland_sentence_census",
        "family": SENTENCE_FAMILY,
        "statutes_scanned": result.statutes_scanned,
        "in_scope_units": result.in_scope_segments,
        "buckets": dict(result.buckets),
        "partition_total": result.partition_total,
        "is_partition": result.is_partition(),
        "totality_violations": result.totality_violations,
        "miss_shape_counts": dict(result.miss_shape_counts),
        "miss_examples": _rows(result.miss_examples),
        "superset_examples": _rows(result.superset_examples),
        "decline_examples": _rows(result.decline_examples),
        "full_oracle": True,
    }


def main(args: Namespace) -> None:
    result = run_sentence_census(
        limit=int(args.limit or 0),
        min_year=int(args.min_year or 0),
        max_examples=int(args.max_examples or 8),
        full_oracle=not getattr(args, "legacy_oracle", False),
    )
    if getattr(args, "json", False):
        payload = _result_to_json(result)
        payload["full_oracle"] = not getattr(args, "legacy_oracle", False)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return

    report = format_sentence_census_report(result)
    sys.stdout.write(report)
    if not report.endswith("\n"):
        sys.stdout.write("\n")
