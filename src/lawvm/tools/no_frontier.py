"""lawvm no-frontier -- compact Norway frontier summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse


def _attach_candidate_split(
    work_items: list[dict[str, object]],
    *,
    data_dir: Path | None,
    index_path: Path | None,
) -> dict[str, int]:
    from lawvm.tools.no_commencement_candidates import build_no_commencement_candidate_report

    candidate_source_counts = {"local_corpus": 0, "statsrad": 0}
    for item in work_items:
        try:
            candidate_report = build_no_commencement_candidate_report(
                source_id=str(item["source_id"]),
                data_dir=data_dir,
                index_path=index_path,
                limit=3,
                direct_only=False,
            )
        except Exception as exc:
            item["candidate_scan_error"] = str(exc)
            item["candidate_source_counts"] = {"local_corpus": 0, "statsrad": 0}
            item["candidate_groups"] = []
            item["candidate_count"] = 0
            continue
        item["candidate_source_counts"] = dict(candidate_report.get("candidate_source_counts", {}))
        item["candidate_groups"] = list(candidate_report.get("candidate_groups", []))
        item["candidate_count"] = int(candidate_report.get("candidate_count", 0))
        candidate_source_counts["local_corpus"] += int(
            item["candidate_source_counts"].get("local_corpus", 0)
        )
        candidate_source_counts["statsrad"] += int(item["candidate_source_counts"].get("statsrad", 0))
    return candidate_source_counts


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import build_no_commencement_report, build_no_blocked_law_report
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index
    from lawvm.norway.inventory import build_no_inventory, build_no_missing_base_report
    from lawvm.norway.verify import build_no_verify_partition, build_no_verify_scan

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    index = load_no_amendment_index(index_path) if index_path else build_no_amendment_index(data_dir)
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None

    inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    inventory_data = inventory.to_dict()
    staleness = index.staleness_report(data_dir) if index is not None else {"index_stale": False}

    limit = getattr(args, "limit", 5)
    unlock_report = build_no_commencement_report(
        index,
        current_laws_only=True,
        sort_mode="unlock",
    )
    unlock_report = dict(unlock_report)
    unlock_report["entries"] = unlock_report["entries"][:limit]
    unlock_report["candidate_source_counts"] = _attach_candidate_split(
        unlock_report["entries"],
        data_dir=data_dir,
        index_path=index_path,
    )

    blocked_report = build_no_blocked_law_report(
        index,
        min_blockers=getattr(args, "min_blockers", 3),
    )
    blocked_report = dict(blocked_report)
    blocked_report["laws"] = blocked_report["laws"][:limit]

    missing_base_report = build_no_missing_base_report(
        inventory,
        min_amendments=getattr(args, "min_amendments", 1),
    )
    missing_base_report = dict(missing_base_report)
    missing_base_report["laws"] = missing_base_report["laws"][:limit]

    verify_report = build_no_verify_scan(
        as_of=getattr(args, "as_of", "2026-03-29"),
        data_dir=data_dir,
        index=index,
        commencement_path=commencement_path,
        limit=limit,
        base_ids=list(getattr(args, "base_id", []) or []),
        progress_callback=(lambda msg: print(msg, file=sys.stderr)) if getattr(args, "progress", False) else None,
    )
    verify_partition = build_no_verify_partition(
        as_of=getattr(args, "as_of", "2026-03-29"),
        data_dir=data_dir,
        index=index,
        commencement_path=commencement_path,
        limit=limit,
        base_ids=list(getattr(args, "base_id", []) or []),
        progress_callback=(lambda msg: print(msg, file=sys.stderr)) if getattr(args, "progress", False) else None,
    )
    partitions = verify_partition["partitions"]
    active_lane = "consistent"
    active_lane_count = len(partitions["consistent"])
    for lane in ("replay_defect", "untouched_drift", "source_sparse", "error"):
        lane_count = len(partitions[lane])
        if lane_count:
            active_lane = lane
            active_lane_count = lane_count
            break
    lane_label_map = {
        "replay_defect": "Replay Defects",
        "untouched_drift": "Untouched Drift",
        "source_sparse": "Sparse Source Cases",
        "consistent": "Consistent",
        "error": "Errors",
    }

    report: dict[str, Any] = {
        "inventory": inventory_data,
        "unlock_queue": unlock_report,
        "commencement_candidate_source_counts": unlock_report["candidate_source_counts"],
        "executable_blockers": blocked_report,
        "missing_base_source": missing_base_report,
        "consistency_sample": verify_report,
        "consistency_partition": verify_partition,
        "active_consistency_lane": active_lane,
        "active_consistency_lane_label": lane_label_map[active_lane],
        "active_consistency_lane_count": active_lane_count,
    }
    report.update(staleness)

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Frontier Summary ===")
    print(
        "  executable amended replayable : "
        f"{inventory_data['current_laws_with_amendments_fully_replayable_executable']}"
    )
    print(
        "  executable amended contingent : "
        f"{inventory_data['current_laws_with_amendments_blocked_contingent_executable']}"
    )
    print(
        "  amended laws missing base     : "
        f"{inventory_data['current_laws_with_amendments_missing_base_source']}"
    )
    if report.get("index_stale"):
        print("  index stale                   : yes")
    print(
        "  consistency sample           : "
        + ", ".join(f"{k}={v}" for k, v in sorted(verify_report["summary"].items()))
    )
    signal_counts = verify_report.get("source_signal_counts", {})
    if signal_counts:
        print(
            "  consistency signals         : "
            + ", ".join(f"{k}={v}" for k, v in sorted(signal_counts.items()))
        )
    print(
        "  consistency partition       : "
        f"replay_defect={len(partitions['replay_defect'])}, "
        f"untouched_drift={len(partitions['untouched_drift'])}, "
        f"source_sparse={len(partitions['source_sparse'])}, "
        f"consistent={len(partitions['consistent'])}, "
        f"error={len(partitions['error'])}"
    )
    candidate_counts = report.get("commencement_candidate_source_counts", {})
    if candidate_counts:
        print(
            "  commencement candidate lanes : "
            + ", ".join(f"{k}={v}" for k, v in sorted(candidate_counts.items()))
        )
    print(
        "  active consistency lane    : "
        f"{lane_label_map[active_lane]} ({active_lane_count})"
    )
    if partitions["replay_defect"]:
        print("  top replay defects:")
        for item in partitions["replay_defect"][: min(3, limit)]:
            print(
                f"    {item['base_id']} | divergences={item['divergence_count']} | "
                f"ops={item['replay_op_count']}"
            )
    if partitions["source_sparse"]:
        print("  top sparse-source cases:")
        for item in partitions["source_sparse"][: min(3, limit)]:
            print(
                f"    {item['base_id']} | divergences={item['divergence_count']} | "
                f"ops={item['replay_op_count']}"
            )
    if partitions["untouched_drift"]:
        print("  top untouched-drift cases:")
        for item in partitions["untouched_drift"][: min(3, limit)]:
            print(
                f"    {item['base_id']} | divergences={item['divergence_count']} | "
                f"ops={item['replay_op_count']}"
            )

    entries = unlock_report["entries"]
    if entries:
        print("  top unlock queue:")
        for item in entries:
            print(
                f"    {item['source_id']} | current_laws={item['current_law_count']} | "
                f"sole_blocker={item['sole_blocker_current_law_count']}"
            )
            if item.get("candidate_source_counts"):
                counts = item["candidate_source_counts"]
                print(
                    "      candidates: "
                    f"local_corpus={counts.get('local_corpus', 0)}, "
                    f"statsrad={counts.get('statsrad', 0)}"
                )

    laws = blocked_report["laws"]
    if laws:
        print("  top executable blockers:")
        for item in laws:
            print(f"    {item['base_id']} | blockers={item['blocking_count']}")

    missing = missing_base_report["laws"]
    if missing:
        print("  top missing base:")
        for item in missing:
            print(f"    {item['base_id']} | amendments={item['amendments']}")
