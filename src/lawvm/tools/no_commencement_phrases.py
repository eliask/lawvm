"""lawvm no-commencement-phrases -- grouped Norway contingent commencement phrases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_commencement_phrase_report,
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

    report = build_no_commencement_phrase_report(
        index,
        current_laws_only=getattr(args, "current_laws_only", True),
        phrase=getattr(args, "phrase", None),
        override_state=getattr(args, "override_state", None),
        overrides=overrides,
        sort_mode=getattr(args, "sort", "unlock"),
    )
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["groups"] = report["groups"][:limit]

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Commencement Phrases ===")
    print(f"  phrase count      : {report['phrase_count']}")
    print(f"  sort mode         : {report['sort_mode']}")
    if report["override_state_filter"]:
        print(f"  override filter   : {report['override_state_filter']}")
    if report["phrase_filter"]:
        print(f"  phrase filter     : {report['phrase_filter']}")
    if report.get("index_stale"):
        print("  index stale       : yes")
    groups = report["groups"]
    if groups:
        print("  groups:")
        for item in groups:
            print(
                f"    {item['phrase']} | sources={item['source_count']} | "
                f"exec_current={item['executable_current_law_count']} | "
                f"resolved={item['override_state_counts'].get('resolved', 0)} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']}"
            )
