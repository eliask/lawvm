"""CLI for unified Finland construction-grammar differential census."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from typing import Any

from lawvm.finland.legal_surface.grammar_census import (
    GRAMMAR_CENSUS_FAMILIES,
    GrammarCensusResult,
    format_grammar_census_report,
    run_grammar_census,
)


def _result_to_json(result: GrammarCensusResult) -> dict[str, Any]:
    return {
        "catalog_kind": "finland_grammar_census",
        "statutes_scanned": result.statutes_scanned,
        "total_in_scope_units": result.total_in_scope_units,
        "total_miss": result.total_miss,
        "all_partitions_ok": result.all_partitions_ok(),
        "families": [
            {
                "family_id": summary.family_id,
                "statutes_scanned": summary.statutes_scanned,
                "in_scope_units": summary.in_scope_units,
                "buckets": summary.buckets,
                "totality_violations": summary.totality_violations,
                "miss_shape_counts": summary.miss_shape_counts,
                "partition_ok": summary.partition_ok,
                "distance_from_miss_zero": summary.distance_from_miss_zero,
            }
            for summary in result.families
        ],
    }


def main(args: Namespace) -> None:
    families_arg = getattr(args, "families", None)
    families = None
    if families_arg:
        families = [part.strip() for part in families_arg.split(",") if part.strip()]

    result = run_grammar_census(
        families=families,
        limit=int(args.limit or 0),
        min_year=int(args.min_year or 0),
        max_examples=int(args.max_examples or 6),
        full_sentence_oracle=not getattr(args, "legacy_oracle", False),
    )
    if getattr(args, "json", False):
        payload = _result_to_json(result)
        payload["family_catalog"] = list(GRAMMAR_CENSUS_FAMILIES)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return

    report = format_grammar_census_report(result)
    sys.stdout.write(report)
    if not report.endswith("\n"):
        sys.stdout.write("\n")
