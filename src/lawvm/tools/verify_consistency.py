"""lawvm verify-consistency — compare ops-replay vs consolidated statute text.

For Estonian law: divergences are legal findings (authoritative text differs
from amendment-chain replay → potential legal inconsistency in Riigi Teataja).
For Finnish law: divergences are editorial (Finlex staleness).

Usage:
    lawvm verify-consistency --jurisdiction ee \
        --base 113032019003 --consolidated 113032019005
    lawvm verify-consistency --jurisdiction ee \
        --base .tmp/estonia/113032019003.xml \
        --consolidated .tmp/estonia/113032019005.xml

Arguments:
    --base         Riigi Teataja globaalID or local XML file path (ops-replay source)
    --consolidated Riigi Teataja globaalID or local XML file path (authoritative text)
    --as-of        Date for comparison (default: "0000-00-00" = initial state)
    --jurisdiction Jurisdiction (default: ee; fi support planned)
    --cache-dir    Directory to cache fetched XMLs (default: .tmp/estonia/)
    --verbose      Show per-provision details for all divergences
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from lawvm.tools.ee_reporting import (
    build_ee_benchmark_reporting_summary,
    build_ee_comparison_policy_summary,
)

if TYPE_CHECKING:
    import argparse

_DEFAULT_CACHE_DIR = Path(".tmp/estonia")
_RT_URL = "https://www.riigiteataja.ee/akt/{gid}.xml"


def _address_str(address: object) -> str:
    path = getattr(address, "path", ())
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _normalize_known_gid(id_or_path: str) -> str | None:
    candidate = id_or_path.strip()
    if candidate.isdigit():
        return candidate
    return None


def _resolve_ee_gid_pair_as_of(args: "argparse.Namespace") -> str:
    """Use the oracle XML effective date as the default cutoff for EE ID pairs."""
    explicit = getattr(args, "as_of", None)
    if explicit and explicit != "0000-00-00":
        return explicit

    base_gid = _normalize_known_gid(args.base)
    oracle_gid = _normalize_known_gid(args.consolidated)
    if base_gid is None or oracle_gid is None:
        return "0000-00-00"

    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive

    archive = open_rt_archive()
    try:
        oracle_xml = fetch_rt_xml(oracle_gid, archive=archive)
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()
    return extract_effective_date(oracle_xml) or "0000-00-00"


def _build_ee_replay_consistency_payload(args: "argparse.Namespace") -> dict:
    from lawvm.core.adjudication_evidence import (
        adjudication_finding_evidence_rows,
        adjudication_kind_counts,
    )
    from lawvm.core.timeline_consistency import ingest_consolidated
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    as_of = _resolve_ee_gid_pair_as_of(args)
    result = replay_ee_to_pit(
        base_id=args.base,
        as_of=as_of,
        verbose=getattr(args, "verbose", False),
        oracle_id=args.consolidated,
    )
    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    con_tl = {}
    consolidated_title = ""
    if result.oracle is not None:
        consolidated_title = result.oracle.title
        con_tl = ingest_consolidated(result.oracle, as_of="0000-00-00")

    divergence_addresses = [_address_str(d.address) for d in result.divergences]
    residual_summary = build_ee_residual_summary(
        base_id=result.base_id,
        oracle_id=result.oracle_id,
        divergence_addresses=divergence_addresses,
    )
    adjudications = list(getattr(result, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="estonia",
        base_id=result.base_id,
        as_of=as_of,
    )
    reporting_summary = build_ee_benchmark_reporting_summary(
        getattr(result, "source_basis", ""),
        result.comparison_class,
    )

    payload = {
        "base_id": result.base_id,
        "consolidated_id": result.oracle_id or args.consolidated,
        "as_of": as_of,
        "source_basis": getattr(result, "source_basis", ""),
        "comparison_class": result.comparison_class,
        "benchmark_reporting_stratum": reporting_summary["benchmark_reporting_stratum"],
        "benchmark_reporting_headline_eligible": reporting_summary["benchmark_reporting_headline_eligible"],
        "comparison_policy": build_ee_comparison_policy_summary(),
        "ops_count": result.n_ops,
        "base_title": result.base_title,
        "consolidated_title": consolidated_title,
        "ops_provisions": len(result.timelines or {}),
        "consolidated_provisions": len(con_tl),
        "divergence_count": len(result.divergences),
        "replay_adjudication_count": len(adjudications),
        "replay_adjudication_kinds": sorted({a.kind for a in adjudications}),
        "replay_adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "replay_adjudications": [
            {
                "kind": adjudication.kind,
                "message": adjudication.message,
                "source_statute": adjudication.source_statute,
                "op_id": adjudication.op_id,
                "detail": dict(adjudication.detail),
            }
            for adjudication in adjudications
        ],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "mismatch_count": result.n_mismatch,
        "ops_missing_count": result.n_ops_missing,
        "consolidated_missing_count": result.n_con_missing,
        "divergences": [],
        "residual_inventory": None,
    }
    for divergence, address in zip(result.divergences, divergence_addresses, strict=True):
        bucket = None
        evidence = None
        if residual_summary is not None:
            record = residual_summary.record_by_address.get(address)
            if record is not None:
                bucket = record.bucket
                evidence = record.evidence
        payload["divergences"].append(
            {
                "address": address,
                "divergence_type": divergence.divergence_type,
                "ops_text": divergence.ops_text,
                "consolidated_text": divergence.consolidated_text,
                "residual_bucket": bucket,
                "residual_evidence": evidence,
            }
        )

    if residual_summary is not None:
        payload["residual_inventory"] = {
            "base_id": residual_summary.base_id,
            "oracle_id": residual_summary.oracle_id,
            "statute_title": residual_summary.statute_title,
            "comparison_class": residual_summary.comparison_class,
            "residual_count": residual_summary.residual_count,
            "bucket_counts": residual_summary.bucket_counts,
            "matched_current_divergence_count": residual_summary.matched_current_divergence_count,
            "matched_current_bucket_counts": residual_summary.matched_current_bucket_counts,
            "unknown_current_divergence_count": residual_summary.unknown_current_divergence_count,
            "unknown_current_divergence_addresses": list(
                residual_summary.unknown_current_divergence_addresses
            ),
        }

    return payload


def _build_ee_consistency_payload(args: "argparse.Namespace") -> dict:
    from lawvm.estonia.compare import irnode_to_ee_comparison_text, normalize_ee_comparison_text
    from lawvm.estonia.grafter import parse_ee_statute
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.core.timeline import compile_timelines
    from lawvm.core.timeline_consistency import ingest_consolidated, verify_consistency

    if _normalize_known_gid(args.base) is not None and _normalize_known_gid(args.consolidated) is not None:
        return _build_ee_replay_consistency_payload(args)

    cache_dir = Path(args.cache_dir) if args.cache_dir else _DEFAULT_CACHE_DIR
    as_of = args.as_of or "0000-00-00"

    print(f"Loading base statute: {args.base}", file=sys.stderr)
    base_xml = _load_xml(args.base, cache_dir)
    base = parse_ee_statute(base_xml, f"ee/{args.base}")

    print(f"Loading consolidated: {args.consolidated}", file=sys.stderr)
    con_xml = _load_xml(args.consolidated, cache_dir)
    con = parse_ee_statute(con_xml, f"ee/{args.consolidated}")

    ops_tl = compile_timelines(base, [])
    con_tl = ingest_consolidated(con, as_of=as_of)

    divergences = verify_consistency(
        ops_tl,
        con_tl,
        as_of=as_of,
        irnode_to_text=irnode_to_ee_comparison_text,
        text_normalizer=normalize_ee_comparison_text,
        missing_equals_empty=True,
    )

    ops_only = [d for d in divergences if d.divergence_type == "OPS_MISSING"]
    con_only = [d for d in divergences if d.divergence_type == "CONSOLIDATED_MISSING"]
    mismatches = [d for d in divergences if d.divergence_type == "MISMATCH"]
    divergence_addresses = [_address_str(d.address) for d in divergences]
    residual_summary = build_ee_residual_summary(
        base_id=_normalize_known_gid(args.base),
        oracle_id=_normalize_known_gid(args.consolidated),
        divergence_addresses=divergence_addresses,
    )

    payload = {
        "base_id": args.base,
        "consolidated_id": args.consolidated,
        "as_of": as_of,
        "base_title": base.title,
        "consolidated_title": con.title,
        "ops_provisions": len(ops_tl),
        "consolidated_provisions": len(con_tl),
        "divergence_count": len(divergences),
        "benchmark_reporting_stratum": None,
        "benchmark_reporting_headline_eligible": None,
        "comparison_policy": build_ee_comparison_policy_summary(),
        "mismatch_count": len(mismatches),
        "ops_missing_count": len(ops_only),
        "consolidated_missing_count": len(con_only),
        "divergences": [],
        "residual_inventory": None,
    }
    for divergence, address in zip(divergences, divergence_addresses, strict=True):
        bucket = None
        evidence = None
        if residual_summary is not None:
            record = residual_summary.record_by_address.get(address)
            if record is not None:
                bucket = record.bucket
                evidence = record.evidence
        payload["divergences"].append(
            {
                "address": address,
                "divergence_type": divergence.divergence_type,
                "ops_text": divergence.ops_text,
                "consolidated_text": divergence.consolidated_text,
                "residual_bucket": bucket,
                "residual_evidence": evidence,
            }
        )

    if residual_summary is not None:
        payload["residual_inventory"] = {
            "base_id": residual_summary.base_id,
            "oracle_id": residual_summary.oracle_id,
            "statute_title": residual_summary.statute_title,
            "comparison_class": residual_summary.comparison_class,
            "residual_count": residual_summary.residual_count,
            "bucket_counts": residual_summary.bucket_counts,
            "matched_current_divergence_count": residual_summary.matched_current_divergence_count,
            "matched_current_bucket_counts": residual_summary.matched_current_bucket_counts,
            "unknown_current_divergence_count": residual_summary.unknown_current_divergence_count,
            "unknown_current_divergence_addresses": list(
                residual_summary.unknown_current_divergence_addresses
            ),
        }

    return payload


def _print_ee_consistency_payload(payload: dict, *, verbose: bool) -> None:
    print(
        f"\n=== Consistency Report: {payload['base_id']} → {payload['consolidated_id']} ==="
    )
    print(f"  as_of      : {payload['as_of']}")
    print(f"  base       : {payload['base_title'][:60]}")
    print(f"  consolidated: {payload['consolidated_title'][:60]}")
    if payload.get("source_basis"):
        print(f"  basis      : {payload['source_basis']}")
    if payload.get("comparison_class"):
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
    if payload.get("ops_count") is not None:
        print(f"  ops        : {payload['ops_count']}")
    print(f"  ops provisions    : {payload['ops_provisions']}")
    print(f"  con provisions    : {payload['consolidated_provisions']}")
    print(f"  divergences: {payload['divergence_count']} total")
    print(f"    MISMATCH         : {payload['mismatch_count']}")
    print(
        f"    OPS_MISSING      : {payload['ops_missing_count']}  (in consolidated but not replay)"
    )
    print(
        "    CONSOLIDATED_MISSING: "
        f"{payload['consolidated_missing_count']}  (in replay but not consolidated)"
    )

    residual_inventory = payload.get("residual_inventory")
    if residual_inventory is not None:
        print(
            "  adjudicated residuals: "
            f"{residual_inventory['residual_count']} known for this pair"
        )
        bucket_counts = residual_inventory.get("bucket_counts") or {}
        if bucket_counts:
            counts = ", ".join(
                f"{bucket}={count}" for bucket, count in sorted(bucket_counts.items())
            )
            print(f"    buckets           : {counts}")
        matched_bucket_counts = residual_inventory.get("matched_current_bucket_counts") or {}
        if matched_bucket_counts:
            counts = ", ".join(
                f"{bucket}={count}" for bucket, count in sorted(matched_bucket_counts.items())
            )
            print(f"    matched current   : {counts}")
        print(
            "    unknown current   : "
            f"{residual_inventory['unknown_current_divergence_count']}"
        )

    divergences = payload["divergences"]
    if not divergences:
        print("\n  FULLY CONSISTENT ✓")
        return

    show_n = len(divergences) if verbose else min(20, len(divergences))
    print(f"\n{'All' if verbose else f'Top {show_n}'} divergences:")
    for divergence in divergences[:show_n]:
        tail = ""
        if divergence.get("residual_bucket"):
            tail = f"  [{divergence['residual_bucket']}]"
        print(f"  [{divergence['divergence_type']:<22}] {divergence['address']}{tail}")
        if verbose or divergence["divergence_type"] == "MISMATCH":
            if divergence["ops_text"]:
                print(f"    replay : {divergence['ops_text'][:100]!r}")
            if divergence["consolidated_text"]:
                print(f"    consol : {divergence['consolidated_text'][:100]!r}")
            if verbose and divergence.get("residual_evidence"):
                print(f"    note   : {divergence['residual_evidence']}")

    if not verbose and len(divergences) > show_n:
        print(f"  ... ({len(divergences) - show_n} more — use --verbose to show all)")


def _load_xml(id_or_path: str, cache_dir: Path) -> bytes:
    """Load XML from a local path, cache, or fetch from Riigi Teataja.

    Accepts:
      - Absolute or relative file path ending in .xml
      - Riigi Teataja globaalID (numeric string like 104012019011)
    """
    p = Path(id_or_path)
    if p.suffix == ".xml" or "/" in id_or_path:
        # Treat as path
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)
        return p.read_bytes()

    # Treat as globaalID — check cache first
    gid = id_or_path.strip()
    cache_file = cache_dir / f"{gid}.xml"
    if cache_file.exists():
        return cache_file.read_bytes()

    # Fetch from Riigi Teataja
    url = _RT_URL.format(gid=gid)
    print(f"  Fetching {url}...", file=sys.stderr)
    cache_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["curl", "-s", "-A", "lawvm/1.0 (legal research tool)", "-o", str(cache_file), url],
        capture_output=True,
    )
    if result.returncode != 0 or not cache_file.exists() or cache_file.stat().st_size < 100:
        print(f"ERROR: failed to fetch {url}", file=sys.stderr)
        sys.exit(1)
    return cache_file.read_bytes()


def _run_ee(args: "argparse.Namespace") -> None:
    payload = _build_ee_consistency_payload(args)
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_ee_consistency_payload(payload, verbose=getattr(args, "verbose", False))


def main(args: "argparse.Namespace") -> None:
    jurisdiction = (args.jurisdiction or "ee").lower()
    if jurisdiction == "ee":
        _run_ee(args)
    else:
        print(f"ERROR: unsupported jurisdiction {jurisdiction!r} (supported: ee)", file=sys.stderr)
        sys.exit(1)
