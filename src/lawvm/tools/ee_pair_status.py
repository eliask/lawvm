"""lawvm ee-pair-status — deterministic EE pair spot-check summary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
from lawvm.estonia.pair_planning import plan_ee_oracle_pair
from lawvm.tools.ee_bench import _DEFAULT_DB, _score_one_pair
from lawvm.tools.ee_reporting import (
    build_ee_benchmark_reporting_summary,
    build_ee_comparison_policy_summary,
)

if TYPE_CHECKING:
    import argparse


def build_pair_status_payload(base_id: str, oracle_id: str, title: str = "") -> dict:
    """Build one EE pair-status payload using the same scoring path as ee-bench."""
    archive = open_rt_archive(Path(_DEFAULT_DB))
    try:
        result = _score_one_pair("manual", base_id, oracle_id, title or base_id, archive)
        base_xml = fetch_rt_xml(base_id, archive)
        oracle_xml = fetch_rt_xml(oracle_id, archive)
        planning = plan_ee_oracle_pair(
            base_id=base_id,
            as_of=extract_effective_date(oracle_xml) or "2026-03-24",
            base_xml=base_xml,
            archive=archive,
            oracle_id=oracle_id,
        )
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    reporting_summary = build_ee_benchmark_reporting_summary(
        planning.plan.source_basis.value,
        result.comparison_class,
    )
    return {
        "base_id": result.base_id,
        "oracle_id": result.oracle_id,
        "title": result.title,
        "status": result.status,
        "source_basis": planning.plan.source_basis.value,
        "comparison_class": result.comparison_class,
        "benchmark_reporting_stratum": reporting_summary["benchmark_reporting_stratum"],
        "benchmark_reporting_headline_eligible": reporting_summary["benchmark_reporting_headline_eligible"],
        "comparison_policy": build_ee_comparison_policy_summary(),
        "core_benchmark": result.core_benchmark,
        "n_ops": result.n_ops,
        "n_divs": result.n_divs,
        "sec_match": result.sec_match,
        "r_secs": result.r_secs,
        "o_secs": result.o_secs,
        "adjudicated_residual_count": result.adjudicated_residual_count,
        "matched_current_residual_count": result.matched_current_residual_count,
        "adjudicated_bucket_counts": result.adjudicated_bucket_counts,
        "unknown_current_residual_count": result.unknown_current_residual_count,
        "open_current_divergence_count": result.open_current_divergence_count,
    }


def main(args: "argparse.Namespace") -> None:
    payload = build_pair_status_payload(
        base_id=args.base_id,
        oracle_id=args.oracle_id,
        title=getattr(args, "title", "") or "",
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== EE Pair Status ===")
    print(f"  base_id    : {payload['base_id']}")
    print(f"  oracle_id  : {payload['oracle_id']}")
    print(f"  title      : {payload['title']}")
    print(f"  status     : {payload['status']}")
    print(f"  basis      : {payload['source_basis']}")
    print(f"  compare    : {payload['comparison_class']}")
    print(
        "  reporting  : "
        f"{payload['benchmark_reporting_stratum']} "
        f"(headline={'yes' if payload['benchmark_reporting_headline_eligible'] else 'no'})"
    )
    comparison_policy = payload.get("comparison_policy", {}) or {}
    if comparison_policy:
        print(
            "  drift      : "
            f"{comparison_policy.get('non_silent_rule_count', 0)} non-silent comparison rules"
        )
        class_counts = comparison_policy.get("non_silent_rule_counts_by_class", {})
        if class_counts:
            counts = ", ".join(f"{klass}={count}" for klass, count in class_counts.items())
            print(f"    classes   : {counts}")
        rule_names = comparison_policy.get("non_silent_rule_names", [])
        if rule_names:
            print(f"    rules     : {', '.join(rule_names)}")
    print(f"  core       : {'yes' if payload['core_benchmark'] else 'no'}")
    print(f"  ops        : {payload['n_ops']}")
    print(f"  divergences: {payload['n_divs']}")
    print(f"  sec_match  : {payload['sec_match']:.1%}")
    print(f"  replay/oracle sections: {payload['r_secs']} / {payload['o_secs']}")
    print(f"  adjudicated residuals : {payload['adjudicated_residual_count']}")
    print(f"  matched current       : {payload['matched_current_residual_count']}")
    print(f"  open current          : {payload['open_current_divergence_count']}")
    if payload["adjudicated_bucket_counts"]:
        print(f"  buckets               : {payload['adjudicated_bucket_counts']}")


__all__ = ["build_pair_status_payload", "main"]
