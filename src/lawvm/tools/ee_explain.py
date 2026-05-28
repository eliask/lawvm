"""lawvm ee-explain — single-statute deep-dive for Estonia.

Shows all divergences with residual bucket classification, evidence text,
comparison class, and source chain context for one EE base/oracle pair.

Usage:
    lawvm ee-explain --base-id 193936 --oracle-id 13336397
    lawvm ee-explain --base-id 193936 --oracle-id 13336397 --json
    lawvm ee-explain --base-id 193936 --oracle-id 13336397 --verbose
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from lawvm.tools.ee_reporting import (
    build_ee_benchmark_reporting_summary,
    build_ee_comparison_policy_summary,
)

if TYPE_CHECKING:
    import argparse


def _build_ee_explain_payload(base_id: str, oracle_id: str, verbose: bool = False) -> dict:
    """Build a deep-dive explanation payload for one EE pair."""
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.estonia.residual_reporting import build_ee_residual_summary

    # Resolve as_of from the oracle effective date
    try:
        archive = open_rt_archive(readonly=True)
    except Exception:
        archive = None
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or "9999-12-31"
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    # Run replay
    result = replay_ee_to_pit(
        base_id=base_id,
        as_of=as_of,
        oracle_id=oracle_id,
        verbose=verbose,
    )
    if result.error:
        return {"error": result.error}

    # Get oracle title
    consolidated_title = ""
    if result.oracle is not None:
        consolidated_title = result.oracle.title

    # Build residual summary
    divergence_addresses = []
    for d in result.divergences:
        path = getattr(d.address, "path", ())
        divergence_addresses.append("/".join(f"{kind}:{label}" for kind, label in path))

    residual_summary = build_ee_residual_summary(
        base_id=result.base_id,
        oracle_id=result.oracle_id,
        divergence_addresses=divergence_addresses,
    )
    residual_inventory = None
    if residual_summary is not None:
        residual_inventory = {
            "residual_count": residual_summary.residual_count,
            "bucket_counts": residual_summary.bucket_counts,
            "matched_current_divergence_count": residual_summary.matched_current_divergence_count,
            "matched_current_bucket_counts": residual_summary.matched_current_bucket_counts,
            "unknown_current_divergence_count": residual_summary.unknown_current_divergence_count,
            "unknown_current_divergence_addresses": residual_summary.unknown_current_divergence_addresses,
        }

    # Get source amendment info from the pair plan
    applied_amendments = []
    pair_plan = getattr(result, "pair_plan", None)
    if pair_plan is not None:
        for a in getattr(pair_plan, "amendments_to_apply", []):
            applied_amendments.append(
                {
                    "source_id": getattr(a, "source_id", None)
                    or getattr(a, "aktViide", str(a)),
                    "effective_date": getattr(a, "effective_date", None)
                    or getattr(a, "joustumine", ""),
                }
            )

    reporting_summary = build_ee_benchmark_reporting_summary(
        getattr(result, "source_basis", ""),
        result.comparison_class,
    )
    # Build divergence details
    divergences = []
    for divergence, address in zip(result.divergences, divergence_addresses, strict=True):
        bucket = None
        evidence = None
        if residual_summary is not None:
            record = residual_summary.record_by_address.get(address)
            if record is not None:
                bucket = record.bucket
                evidence = record.evidence

        div_entry = {
            "address": address,
            "type": divergence.divergence_type,
            "replay_text": divergence.ops_text,
            "oracle_text": divergence.consolidated_text,
            "residual_bucket": bucket,
            "residual_evidence": evidence,
        }
        if verbose:
            div_entry["replay_text_full"] = divergence.ops_text
            div_entry["oracle_text_full"] = divergence.consolidated_text
        divergences.append(div_entry)

    # Group by residual bucket
    bucket_groups = {}
    for d in divergences:
        b = d.get("residual_bucket") or "unclassified"
        bucket_groups.setdefault(b, []).append(d["address"])

    return {
        "base_id": result.base_id,
        "oracle_id": result.oracle_id,
        "as_of": getattr(result, "as_of", ""),
        "base_title": result.base_title,
        "oracle_title": consolidated_title,
        "source_basis": getattr(result, "source_basis", ""),
        "comparison_class": result.comparison_class,
        "benchmark_reporting_stratum": reporting_summary["benchmark_reporting_stratum"],
        "benchmark_reporting_headline_eligible": reporting_summary["benchmark_reporting_headline_eligible"],
        "comparison_policy": build_ee_comparison_policy_summary(),
        "ops_count": result.n_ops,
        "divergence_count": len(result.divergences),
        "mismatch_count": result.n_mismatch,
        "ops_missing_count": result.n_ops_missing,
        "consolidated_missing_count": result.n_con_missing,
        "applied_amendments": applied_amendments,
        "bucket_groups": bucket_groups,
        "divergences": divergences,
        "residual_inventory": residual_inventory,
    }


def _print_ee_explain(payload: dict, verbose: bool = False) -> None:
    """Print a human-readable explanation report."""
    if "error" in payload:
        print(f"ERROR: {payload['error']}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"=== EE Explain: {payload['base_id']} → {payload['oracle_id']} ===")
    print(f"  statute    : {payload.get('base_title', '')[:60]}")
    print(f"  oracle     : {payload.get('oracle_title', '')[:60]}")
    if payload.get("source_basis"):
        print(f"  basis      : {payload['source_basis']}")
    print(f"  compare    : {payload['comparison_class']}")
    if payload.get("benchmark_reporting_stratum"):
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
    print(f"  ops        : {payload['ops_count']}")
    print(f"  divergences: {payload['divergence_count']}")
    print(f"    MISMATCH         : {payload['mismatch_count']}")
    print(f"    OPS_MISSING      : {payload['ops_missing_count']}")
    print(f"    CONSOLIDATED_MISSING: {payload['consolidated_missing_count']}")

    # Bucket summary
    bucket_groups = payload.get("bucket_groups", {})
    if bucket_groups:
        print("\n  Divergence buckets:")
        for bucket, addresses in sorted(bucket_groups.items()):
            print(f"    {bucket}: {len(addresses)}")
            for addr in addresses[:5]:
                print(f"      - {addr}")
            if len(addresses) > 5:
                print(f"      ... and {len(addresses) - 5} more")

    # Applied amendments
    amendments = payload.get("applied_amendments", [])
    if amendments:
        print(f"\n  Applied amendments ({len(amendments)}):")
        for a in amendments[:10]:
            print(f"    {a['source_id']} (effective: {a['effective_date']})")
        if len(amendments) > 10:
            print(f"    ... and {len(amendments) - 10} more")

    # Divergences
    divergences = payload.get("divergences", [])
    if not divergences:
        print("\n  FULLY CONSISTENT ✓")
        return

    print("\n  All divergences:")
    for d in divergences:
        tail = ""
        if d.get("residual_bucket"):
            tail = f"  [{d['residual_bucket']}]"
        print(f"    [{d['type']:<22}] {d['address']}{tail}")
        if d.get("replay_text"):
            print(f"      replay: {d['replay_text'][:100]!r}")
        if d.get("oracle_text"):
            print(f"      oracle: {d['oracle_text'][:100]!r}")
        if verbose and d.get("residual_evidence"):
            print(f"      note  : {d['residual_evidence']}")


def main(args: argparse.Namespace) -> None:
    payload = _build_ee_explain_payload(
        base_id=args.base_id,
        oracle_id=args.oracle_id,
        verbose=getattr(args, "verbose", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_ee_explain(payload, verbose=getattr(args, "verbose", False))


__all__ = ["_build_ee_explain_payload", "main"]
