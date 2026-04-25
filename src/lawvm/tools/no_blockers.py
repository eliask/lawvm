"""lawvm no-blockers -- current Norway laws blocked by contingent commencement."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_blocked_law_report,
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
    if commencement_arg:
        overrides = load_no_commencement_overrides(Path(commencement_arg))
        index = apply_no_commencement_overrides(index, overrides)

    report = build_no_blocked_law_report(
        index,
        base_id=getattr(args, "base_id", None),
        min_blockers=getattr(args, "min_blockers", 1),
    )
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["laws"] = report["laws"][:limit]

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Blocked Laws ===")
    print(f"  data dir          : {report['data_dir']}")
    print(f"  blocked law count : {report['blocked_law_count']}")
    if report.get("index_stale"):
        print("  index stale      : yes")
    if report["base_id_filter"]:
        print(f"  base id filter    : {report['base_id_filter']}")
    print(f"  min blockers      : {report['min_blockers']}")
    laws = report["laws"]
    if laws:
        print("  laws:")
        for item in laws:
            title = item["title"] or "(untitled)"
            print(
                f"    {item['base_id']} | {title} | blockers={item['blocking_count']} | "
                f"blocking_ops={item['blocking_ops']}"
            )
