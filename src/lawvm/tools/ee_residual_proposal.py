"""lawvm ee-residual-proposal — propose residual inventory entries from a bench run.

Takes a saved EE bench run, finds rows with open unexplained divergences,
runs replay on each, and proposes candidate residual inventory entries
with evidence text.

Usage:
    lawvm ee-residual-proposal --label ee_20260329_clean --top 5
    lawvm ee-residual-proposal --base-id 111112022002 --oracle-id 130062023023
    lawvm ee-residual-proposal --label ee_20260329_clean --format python
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

_BENCH_DIR = Path(__file__).parent.parent.parent.parent / "data" / "ee_bench_runs"


def _find_latest_bench_csv() -> Path:
    matches = sorted(_BENCH_DIR.glob("ee_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No bench run CSVs found in {_BENCH_DIR}")
    return matches[-1]


def _find_bench_csv(label: str) -> Path:
    matches = sorted(_BENCH_DIR.glob(f"*_{label}.csv"))
    if not matches:
        raise FileNotFoundError(f"No bench run CSV found for label '{label}' in {_BENCH_DIR}")
    return matches[-1]


def _load_bench_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _propose_for_pair(base_id: str, oracle_id: str, title: str = "") -> dict:
    """Run replay on one pair and propose residual inventory entries."""
    from lawvm.estonia.replay import replay_ee_to_pit
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
    from lawvm.estonia.residual_inventory import get_ee_residual_inventory

    # Resolve as_of
    archive = open_rt_archive()
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or "9999-12-31"
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    # Check if already inventoried
    existing = get_ee_residual_inventory(base_id, oracle_id)
    if existing is not None:
        return {
            "base_id": base_id,
            "oracle_id": oracle_id,
            "title": title or existing.statute_title,
            "status": "already_inventoried",
            "existing_count": len(existing.residuals),
            "proposals": [],
        }

    # Run replay
    result = replay_ee_to_pit(
        base_id=base_id,
        as_of=as_of,
        oracle_id=oracle_id,
        verbose=False,
    )
    if result.error:
        return {
            "base_id": base_id,
            "oracle_id": oracle_id,
            "title": title,
            "status": "error",
            "error": result.error,
            "proposals": [],
        }

    # Build proposals
    proposals = []
    for d in result.divergences:
        path = getattr(d.address, "path", ())
        address = "/".join(f"{kind}:{label}" for kind, label in path)
        proposals.append(
            {
                "address": address,
                "type": d.divergence_type,
                "replay_text": (d.ops_text or "")[:200],
                "oracle_text": (d.consolidated_text or "")[:200],
                "suggested_bucket": "source_oracle_drift" if d.divergence_type == "MISMATCH" else "source_pathology",
            }
        )

    return {
        "base_id": base_id,
        "oracle_id": oracle_id,
        "title": title or result.base_title,
        "status": "proposed",
        "as_of": as_of,
        "n_ops": result.n_ops,
        "n_divergences": len(result.divergences),
        "proposals": proposals,
    }


def _run_from_label(label: str, top: int = 10) -> list[dict]:
    """Run proposals for top open rows from a bench run."""
    csv_path = _find_bench_csv(label)
    rows = _load_bench_rows(csv_path)

    # Filter to open unexplained rows
    open_rows = []
    for r in rows:
        open_count = int(r.get("open_current_divergence_count", 0))
        if open_count > 0:
            open_rows.append(
                (
                    r.get("grupi_id", ""),
                    r.get("base_id", ""),
                    r.get("oracle_id", ""),
                    r.get("title", ""),
                    open_count,
                )
            )

    open_rows.sort(key=lambda x: -x[4])
    open_rows = open_rows[:top]

    results = []
    for gid, bid, oid, title, open_count in open_rows:
        print(f"  Processing {bid} -> {oid} ({open_count} open divergences)...", file=sys.stderr)
        result = _propose_for_pair(bid, oid, title)
        results.append(result)

    return results


def _run_single_pair(base_id: str, oracle_id: str, title: str = "") -> list[dict]:
    """Run proposal for a single pair."""
    result = _propose_for_pair(base_id, oracle_id, title)
    return [result]


def _format_python(results: list[dict]) -> str:
    """Format proposals as Python code for residual_inventory.py."""
    lines = []
    for r in results:
        if r["status"] != "proposed" or not r["proposals"]:
            continue

        lines.append(f'    ("{r["base_id"]}", "{r["oracle_id"]}"): EEPairResidualInventory(')
        lines.append(f'        base_id="{r["base_id"]}",')
        lines.append(f'        oracle_id="{r["oracle_id"]}",')
        lines.append(f"        statute_title={r['title']!r},")
        lines.append('        comparison_class="commensurable_delta",')
        lines.append("        residuals=(")

        for p in r["proposals"]:
            lines.append("            EEResidualRecord(")
            lines.append(f"                address={p['address']!r},")
            lines.append(f"                bucket={p['suggested_bucket']!r},")
            lines.append("                evidence=(")
            lines.append(f'                    "Replay: {p["replay_text"][:100]}..."')
            lines.append(f'                    "Oracle: {p["oracle_text"][:100]}..."')
            lines.append(f'                    "Type: {p["type"]}. Needs source-chain verification."')
            lines.append("                ),")
            lines.append("            ),")

        lines.append("        ),")
        lines.append("    ),")

    return "\n".join(lines)


def _format_text(results: list[dict]) -> str:
    """Format proposals as human-readable text."""
    lines = []
    for r in results:
        lines.append(f"\n=== {r.get('base_id', '?')} -> {r.get('oracle_id', '?')} ===")
        lines.append(f"  title  : {r.get('title', '')}")
        lines.append(f"  status : {r['status']}")

        if r["status"] == "already_inventoried":
            lines.append(f"  Already has {r['existing_count']} residual entries.")
            continue

        if r["status"] == "error":
            lines.append(f"  Error: {r.get('error', 'unknown')}")
            continue

        lines.append(f"  as_of  : {r.get('as_of', '')}")
        lines.append(f"  ops    : {r.get('n_ops', 0)}")
        lines.append(f"  divs   : {r.get('n_divergences', 0)}")

        for p in r.get("proposals", []):
            lines.append(f"\n  [{p['suggested_bucket']}] {p['address']}")
            lines.append(f"    type  : {p['type']}")
            if p["replay_text"]:
                lines.append(f"    replay: {p['replay_text'][:100]!r}")
            if p["oracle_text"]:
                lines.append(f"    oracle: {p['oracle_text'][:100]!r}")

    return "\n".join(lines)


def main(args: argparse.Namespace) -> None:
    fmt = getattr(args, "format", "text")

    if getattr(args, "base_id", None) and getattr(args, "oracle_id", None):
        results = _run_single_pair(
            args.base_id,
            args.oracle_id,
            getattr(args, "title", "") or "",
        )
    elif getattr(args, "label", None):
        results = _run_from_label(args.label, top=getattr(args, "top", 10))
    else:
        print("ERROR: specify --label or --base-id + --oracle-id", file=sys.stderr)
        sys.exit(1)

    if fmt == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif fmt == "python":
        code = _format_python(results)
        if code:
            print(code)
        else:
            print("No proposals to emit (all rows already inventoried or errored).")
    else:
        print(_format_text(results))


__all__ = ["main", "_propose_for_pair", "_run_from_label", "_run_single_pair"]
