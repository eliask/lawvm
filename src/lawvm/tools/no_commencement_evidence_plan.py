"""lawvm no-commencement-evidence-plan -- unresolved Norway external evidence plan."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_commencement_external_evidence_plan_artifact,
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

    report = build_no_commencement_external_evidence_plan_artifact(
        index,
        data_dir=data_dir,
        index_path=index_path,
        current_laws_only=getattr(args, "current_laws_only", True),
        sort_mode=getattr(args, "sort", "unlock"),
        phrase=getattr(args, "phrase", None),
        override_state=getattr(args, "override_state", None),
        overrides=overrides,
        laws_per_source=getattr(args, "laws_per_source", 5),
        limit=getattr(args, "limit", 10),
    )
    report.update(staleness)

    output_arg = getattr(args, "output", None)
    if output_arg:
        Path(output_arg).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Commencement External Evidence Plan ===")
    print(f"  artifact kind    : {report['artifact_kind']}")
    print(
        "  source families  : "
        f"{', '.join(f'{k}={v}' for k, v in sorted(report.get('external_source_family_counts', {}).items())) or '(none)'}"
    )
    print(f"  plan items       : {len(report['plan_items'])}")
    if report.get("index_stale"):
        print("  index stale      : yes")
    if output_arg:
        print(f"  output           : {output_arg}")
    if report["plan_items"]:
        print("  plan items:")
        for item in report["plan_items"]:
            print(
                f"    {item['source_id']} | next={item['next_source_hint'].get('primary_source_family', '')} | "
                f"exec_current={item['executable_current_law_count']} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']}"
            )
            source_packets = item.get("source_packets", [])
            if isinstance(source_packets, list) and source_packets:
                print("      source packets:")
                for packet in source_packets[:3]:
                    if not isinstance(packet, dict):
                        continue
                    display_name = str(packet.get("display_name", ""))
                    priority = packet.get("priority", "")
                    mode = str(packet.get("mode", ""))
                    note = str(packet.get("packet_note", ""))
                    print(f"        {priority}. {display_name} [{mode}]")
                    if note:
                        print(f"           {note}")
                    search_targets = packet.get("search_targets", [])
                    if isinstance(search_targets, list):
                        for target in search_targets[:2]:
                            if target:
                                print(f"           - {target}")
            next_source_plan = item.get("next_source_plan", [])
            if isinstance(next_source_plan, list) and next_source_plan:
                print("      source plan:")
                for plan_item in next_source_plan[:3]:
                    if not isinstance(plan_item, dict):
                        continue
                    display_name = str(plan_item.get("display_name", ""))
                    priority = plan_item.get("priority", "")
                    mode = str(plan_item.get("mode", ""))
                    why = str(plan_item.get("why", ""))
                    print(f"        {priority}. {display_name} [{mode}]")
                    if why:
                        print(f"           {why}")
                    search_targets = plan_item.get("search_targets", [])
                    if isinstance(search_targets, list):
                        for target in search_targets[:2]:
                            if target:
                                print(f"           - {target}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir")
    parser.add_argument("--index")
    parser.add_argument("--commencement")
    parser.add_argument("--current-laws-only", action="store_true", default=True)
    parser.add_argument("--sort", default="unlock")
    parser.add_argument("--phrase")
    parser.add_argument("--override-state")
    parser.add_argument("--laws-per-source", type=int, default=5)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", metavar="FILE", help="write a serialized external evidence plan artifact")
    parser.add_argument("--json", action="store_true")
    main(parser.parse_args())
