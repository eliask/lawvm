"""lawvm no-workqueue -- prioritized Norway commencement-resolution work queue."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

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
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_work_queue,
        export_no_work_queue_packets,
        load_no_commencement_overrides,
    )
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    if index_path:
        index = load_no_amendment_index(index_path)
    else:
        index = build_no_amendment_index(data_dir)
    staleness = index.staleness_report(data_dir) if index_path else {"index_stale": False}

    commencement_arg = getattr(args, "commencement", None)
    overrides = None
    if commencement_arg:
        overrides = load_no_commencement_overrides(Path(commencement_arg))
        index = apply_no_commencement_overrides(index, overrides)

    report = build_no_work_queue(
        index,
        current_laws_only=getattr(args, "current_laws_only", True),
        sort_mode=getattr(args, "sort", "unlock"),
        phrase=getattr(args, "phrase", None),
        override_state=getattr(args, "override_state", None),
        overrides=overrides,
        laws_per_source=getattr(args, "laws_per_source", 5),
    )
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["work_items"] = report["work_items"][:limit]
    report["candidate_source_counts"] = _attach_candidate_split(
        report.get("work_items", []),
        data_dir=data_dir,
        index_path=index_path,
    )

    output_dir_arg = getattr(args, "output_dir", None)
    written_paths: list[Path] = []
    if output_dir_arg:
        written_paths = export_no_work_queue_packets(report, Path(output_dir_arg))

    if getattr(args, "json", False):
        payload = dict(report)
        if written_paths:
            payload["written_paths"] = [str(path) for path in written_paths]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Work Queue ===")
    print(f"  unresolved count : {report['unresolved_count']}")
    print(f"  sort mode        : {report['sort_mode']}")
    if report["override_state_counts"]:
        print(
            "  override states  : "
            + ", ".join(f"{k}={v}" for k, v in sorted(report["override_state_counts"].items()))
        )
    if report["override_state_filter"]:
        print(f"  override filter  : {report['override_state_filter']}")
    if report["phrase_filter"]:
        print(f"  phrase filter    : {report['phrase_filter']}")
    if report.get("index_stale"):
        print("  index stale      : yes")
    if written_paths:
        print(f"  output dir       : {output_dir_arg}")
    work_items = report["work_items"]
    if work_items:
        print("  work items:")
        for item in work_items:
            print(
                f"    {item['source_id']} | state={item['override_state']} | "
                f"exec_current={item['executable_current_law_count']} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']} | {item['raw_date_in_force']}"
            )
            if item.get("candidate_source_counts"):
                counts = item["candidate_source_counts"]
                print(
                    "      candidates: "
                    f"local_corpus={counts.get('local_corpus', 0)}, "
                    f"statsrad={counts.get('statsrad', 0)}"
                )
