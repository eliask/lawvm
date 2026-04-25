"""lawvm no-verify-partition -- classify Norway verify sample into defect buckets."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.verify import build_no_verify_partition

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None
    output_arg = getattr(args, "output", None)
    output_path = Path(output_arg) if output_arg else None

    report = build_no_verify_partition(
        as_of=getattr(args, "as_of"),
        data_dir=data_dir,
        index_path=index_path,
        commencement_path=commencement_path,
        limit=getattr(args, "limit", 10),
        base_ids=list(getattr(args, "base_id", []) or []),
        progress_callback=(lambda msg: print(msg, file=sys.stderr)) if getattr(args, "progress", False) else None,
    )
    if output_path is not None:
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Verify Partition ===")
    print(f"  as of           : {report['as_of']}")
    print(f"  candidate count : {report['candidate_count']}")
    print(f"  scanned count   : {report['scanned_count']}")
    print(
        "  summary         : "
        + ", ".join(f"{k}={v}" for k, v in sorted(report["summary"].items()))
    )
    signal_counts = report.get("source_signal_counts", {})
    if signal_counts:
        print(
            "  source signals  : "
            + ", ".join(f"{k}={v}" for k, v in sorted(signal_counts.items()))
        )
    if output_path is not None:
        print(f"  output          : {output_path}")

    partitions = report["partitions"]
    for key, label in [
        ("replay_defect", "Replay Defects"),
        ("untouched_drift", "Untouched Drift"),
        ("source_sparse", "Sparse Source Cases"),
        ("consistent", "Consistent"),
        ("error", "Errors"),
    ]:
        items = partitions[key]
        if not items:
            continue
        print(f"  {label} ({len(items)}):")
        for item in items:
            tail = f" | source_signal={item['source_signal']}" if item["source_signal"] else ""
            err = f" | error={item['error']}" if item["error"] else ""
            print(
                f"    {item['base_id']} | divergences={item['divergence_count']} | "
                f"ops={item['replay_op_count']}{tail}{err}"
            )
