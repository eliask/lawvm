"""lawvm no-impact -- quantify the effect of a Norway commencement override file."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import build_no_override_impact_report
    from lawvm.norway.inventory import build_no_inventory
    from lawvm.norway.index import load_no_amendment_index
    from lawvm.norway.sources import load_no_current_law_titles, resolve_no_source_path

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    index = load_no_amendment_index(index_path) if index_path else None
    commencement_path = Path(args.commencement)

    before_inventory = build_no_inventory(data_dir, index=index, index_path=index_path)
    after_inventory = build_no_inventory(
        data_dir,
        index=index,
        index_path=index_path,
        commencement_path=commencement_path,
    )
    before = before_inventory.to_dict()
    after = after_inventory.to_dict()
    report = build_no_override_impact_report(before, after)
    titles_dir = resolve_no_source_path(
        data_dir if data_dir is not None else (Path(index.data_dir) if index is not None and index.data_dir else None)
    )
    titles = load_no_current_law_titles(titles_dir)
    before_status = before_inventory.amended_executable_law_status_map()
    after_status = after_inventory.amended_executable_law_status_map()
    unlocked_laws = sorted(
        (
            {"base_id": base_id, "title": titles.get(base_id, "")}
            for base_id in after_status
            if before_status.get(base_id) != "fully_replayable"
            and after_status.get(base_id) == "fully_replayable"
        ),
        key=lambda item: item["base_id"],
    )
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        unlocked_laws = unlocked_laws[:limit]
    report["unlocked_laws"] = unlocked_laws
    if index is not None:
        report.update(index.staleness_report(data_dir))

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Override Impact ===")
    print(
        "  executable replayable amended laws : "
        f"{report['current_laws_with_amendments_fully_replayable_executable_before']} -> "
        f"{report['current_laws_with_amendments_fully_replayable_executable_after']} "
        f"(delta {report['current_laws_with_amendments_fully_replayable_executable_delta']:+d})"
    )
    print(
        "  executable contingent-blocked     : "
        f"{report['current_laws_with_amendments_blocked_contingent_executable_before']} -> "
        f"{report['current_laws_with_amendments_blocked_contingent_executable_after']} "
        f"(delta {report['current_laws_with_amendments_blocked_contingent_executable_delta']:+d})"
    )
    if report.get("index_stale"):
        print("  index stale       : yes")
    if report["unlocked_laws"]:
        print("  unlocked laws:")
        for item in report["unlocked_laws"]:
            title = item["title"] or "(untitled)"
            print(f"    {item['base_id']} | {title}")
