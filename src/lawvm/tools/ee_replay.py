"""lawvm ee-replay — Estonia point-in-time amendment replay + consistency check.

Fetches a base act from Riigi Teataja, discovers all amendments via muutmismarge,
applies those with effective date <= as_of, and compares the replayed state to
RT's own consolidated redaction for that date.

Usage:
    lawvm ee-replay <aktViide> --as-of YYYY-MM-DD
    lawvm ee-replay 113032019003 --as-of 2019-11-01 --verbose
    lawvm ee-replay /path/to/act.xml --as-of 2020-01-01
    lawvm ee-replay 113032019003 --as-of 2022-06-01 --show-text
    lawvm ee-replay 113032019003 --as-of 2022-06-01 --archive .tmp/rt.db
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.core.ir_helpers import irnode_to_text
    from pathlib import Path
    from lawvm.tools.replay_payloads import (
        build_ee_replay_payload,
        replay_text_from_nodes,
    )

    archive = None
    if getattr(args, "archive", None):
        archive = open_rt_archive(Path(args.archive))

    verbose = getattr(args, "verbose", False)
    show_text = getattr(args, "show_text", False)

    if not getattr(args, "json", False):
        print(f"Estonia PIT replay: {args.base_id}  as-of: {args.as_of}", file=sys.stderr)

    result = replay_ee_to_pit(
        base_id=args.base_id,
        as_of=args.as_of,
        archive=archive,
        verbose=verbose,
    )
    residual_summary = None
    if result.oracle is not None:
        residual_summary = build_ee_residual_summary(
            base_id=result.base_id,
            oracle_id=result.oracle_id,
            divergence_addresses=(
                "/".join(f"{kind}:{label}" for kind, label in d.address.path)
                for d in result.divergences
            ),
        )
    replayed_text = None
    if show_text and result.replayed is not None:
        replayed_text = replay_text_from_nodes(result.replayed.body.children, irnode_to_text=irnode_to_text)
    payload = build_ee_replay_payload(
        result,
        archive_path=getattr(args, "archive", None),
        replayed_text=replayed_text,
        residual_summary=residual_summary,
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if result.error:
            sys.exit(1)
        return

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print(f"=== EE PIT Replay: {result.base_id}  as-of: {result.as_of} ===")
    print(f"  title     : {result.base_title[:70]}")
    if result.error:
        print(f"  ERROR     : {result.error}")
        sys.exit(1)

    # ── Amendment chain summary ───────────────────────────────────────────────
    print(f"  grupiId   : {result.grupi_id or '(not found)'}")
    print(f"  oracle    : {result.oracle_id or '(none for this date)'}")
    print(f"  compare   : {result.comparison_class or '(unclassified)'}")
    print(f"  amendments: {len(result.amendments_total)} total | "
          f"{len(result.amendments_applied)} applied | "
          f"{len(result.amendments_skipped)} skipped | "
          f"{len(result.amendments_failed)} failed")
    print(f"  ops       : {result.n_ops}")
    adjudications = list(getattr(result, "adjudications", []) or [])
    if adjudications:
        print(f"  adjudications: {len(adjudications)}")

    if result.amendments_applied:
        print("  applied:")
        for aid in result.amendments_applied:
            print(f"    {aid}")
    if result.amendments_failed:
        print("  FAILED:")
        for aid in result.amendments_failed:
            print(f"    {aid}")

    # ── Consistency results ───────────────────────────────────────────────────
    if result.oracle is None:
        print()
        print("  (no oracle available for this date — consistency check skipped)")
    else:
        total_div = len(result.divergences)
        if total_div == 0:
            print()
            print("  FULLY CONSISTENT ✓")
        else:
            print()
            print(f"  divergences: {total_div} total")
            print(f"    MISMATCH              : {result.n_mismatch}")
            print(f"    OPS_MISSING (in oracle, not replay): {result.n_ops_missing}")
            print(f"    CONSOLIDATED_MISSING  : {result.n_con_missing}")
            if residual_summary is not None:
                print(
                    "    adjudicated residuals : "
                    f"{residual_summary.residual_count} known"
                )
                if residual_summary.bucket_counts:
                    counts = ", ".join(
                        f"{bucket}={count}"
                        for bucket, count in sorted(residual_summary.bucket_counts.items())
                    )
                    print(f"      buckets            : {counts}")
                if residual_summary.matched_current_bucket_counts:
                    counts = ", ".join(
                        f"{bucket}={count}"
                        for bucket, count in sorted(
                            residual_summary.matched_current_bucket_counts.items()
                        )
                    )
                    print(f"      matched current    : {counts}")
                print(
                    "      unknown current    : "
                    f"{residual_summary.unknown_current_divergence_count}"
                )
            print()

            show_n = len(result.divergences) if verbose else min(20, len(result.divergences))
            print(f"{'All' if verbose else f'Top {show_n}'} divergences:")
            for d in result.divergences[:show_n]:
                address = "/".join(f"{kind}:{label}" for kind, label in d.address.path)
                tail = ""
                if residual_summary is not None:
                    record = residual_summary.record_by_address.get(address)
                    if record is not None:
                        tail = f"  [{record.bucket}]"
                print(f"  [{d.divergence_type:<22}] {d.address}{tail}")
                if d.divergence_type == "MISMATCH" or verbose:
                    if d.ops_text:
                        print(f"    replay : {d.ops_text[:120]!r}")
                    if d.consolidated_text:
                        print(f"    oracle : {d.consolidated_text[:120]!r}")
                    if verbose and residual_summary is not None:
                        record = residual_summary.record_by_address.get(address)
                        if record is not None:
                            print(f"    note   : {record.evidence}")
            if not verbose and total_div > show_n:
                print(f"  ... ({total_div - show_n} more — use --verbose to show all)")

    # ── Optional: print replayed text ────────────────────────────────────────
    if show_text and result.replayed is not None:
        print()
        print("=== Replayed text ===")
        print(replayed_text or "")
