"""lawvm no-missing-base -- amended current Norway laws missing local base source."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.inventory import build_no_inventory, build_no_missing_base_report
    from lawvm.norway.index import load_no_amendment_index

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    index = load_no_amendment_index(index_path) if index_path else None
    if data_dir is None and index is not None and index.data_dir:
        data_dir = Path(index.data_dir)

    inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
    )
    report = build_no_missing_base_report(
        inventory,
        base_id=getattr(args, "base_id", None),
        min_amendments=getattr(args, "min_amendments", 1),
    )
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["laws"] = report["laws"][:limit]
    if index is not None:
        report.update(index.staleness_report(data_dir))

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Missing Base Source ===")
    print(f"  data dir               : {report['data_dir']}")
    print(f"  missing base law count : {report['missing_base_source_law_count']}")
    if report.get("index_stale"):
        print("  index stale            : yes")
    if report["base_id_filter"]:
        print(f"  base id filter         : {report['base_id_filter']}")
    print(f"  min amendments         : {report['min_amendments']}")
    laws = report["laws"]
    if laws:
        print("  laws:")
        for item in laws:
            title = item["title"] or "(untitled)"
            print(f"    {item['base_id']} | {title} | amendments={item['amendments']}")
