"""lawvm no-commencement-report -- unresolved Norway commencement cases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_commencement_report,
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

    report = build_no_commencement_report(
        index,
        base_id=getattr(args, "base_id", None),
        phrase=getattr(args, "phrase", None),
        override_state=getattr(args, "override_state", None),
        overrides=overrides,
        current_laws_only=getattr(args, "current_laws_only", False),
        sort_mode=getattr(args, "sort", "source"),
    )
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["entries"] = report["entries"][:limit]

    template_output = getattr(args, "template_output", None)
    if template_output:
        template_path = Path(template_output)
        template: dict[str, dict[str, str]] = {}
        if template_path.exists():
            existing = load_no_commencement_overrides(template_path)
            template.update(existing)
        for item in report["entries"]:
            template.setdefault(
                item["source_id"],
                {
                    "effective_date": "",
                    "note": "",
                    "resolution_kind": "",
                    "evidence_source_id": "",
                    "evidence_excerpt": "",
                },
            )
        Path(template_output).write_text(
            json.dumps({"overrides": template}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Commencement Report ===")
    print(f"  data dir         : {report['data_dir']}")
    print(f"  unresolved count : {report['unresolved_count']}")
    if report.get("index_stale"):
        print("  index stale      : yes")
    print("  by status:")
    for key, value in sorted(report["unresolved_by_status"].items()):
        print(f"    {key}: {value}")
    if report["override_state_counts"]:
        print("  override states:")
        for key, value in sorted(report["override_state_counts"].items()):
            print(f"    {key}: {value}")
    if report["override_state_filter"]:
        print(f"  override filter  : {report['override_state_filter']}")
    if report["phrase_filter"]:
        print(f"  phrase filter    : {report['phrase_filter']}")
    print(f"  sort mode        : {report['sort_mode']}")
    entries = report["entries"]
    if entries:
        print("  entries:")
        for item in entries:
            title = item["title"] or "(untitled)"
            extra = ""
            if item["current_law_count"] or item["sole_blocker_current_law_count"]:
                extra = (
                    f" | current_laws={item['current_law_count']}"
                    f" | sole_blocker={item['sole_blocker_current_law_count']}"
                )
            print(
                f"    {item['source_id']} | {item['effective_status']} | "
                f"{title}{extra} | {item['raw_date_in_force']}"
            )
    if template_output:
        print(f"  template wrote  : {template_output}")
