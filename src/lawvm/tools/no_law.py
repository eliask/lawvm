"""lawvm no-law -- inspect one Norway law across indexed amendment sources."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_law_report,
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

    report = build_no_law_report(index, base_id=args.base_id)
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["sources"] = report["sources"][:limit]

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Law Report ===")
    print(f"  base id                  : {report['base_id']}")
    print(f"  title                    : {report['title'] or '(untitled)'}")
    print(f"  current law              : {'yes' if report['is_current_law'] else 'no'}")
    print(f"  local base source        : {'yes' if report['has_local_base_source'] else 'no'}")
    print(f"  replay status            : {report['replay_status']}")
    print(f"  executable replay status : {report['executable_replay_status']}")
    print(f"  amendment count          : {report['amendment_count']}")
    print(f"  blocking count           : {report['blocking_count']}")
    print(f"  blocking ops             : {report['blocking_ops']}")
    if report.get("index_stale"):
        print("  index stale              : yes")
    sources = report["sources"]
    if sources:
        print("  sources:")
        for item in sources:
            title = item["title"] or "(untitled)"
            print(
                f"    {item['source_id']} | {item['effective_status']} | {title} | ops={item['n_ops']} | "
                f"{item['raw_date_in_force']}"
            )
