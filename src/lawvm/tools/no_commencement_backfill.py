"""lawvm no-commencement-backfill -- serialized Norway commencement backfill artifact."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_commencement_backfill_artifact,
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

    report = build_no_commencement_backfill_artifact(
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
    print("=== Norway Commencement Backfill ===")
    print(f"  artifact kind    : {report['artifact_kind']}")
    print(f"  source lanes     : local_corpus={report['source_lanes'].get('local_corpus', 0)}, "
          f"statsrad={report['source_lanes'].get('statsrad', 0)}")
    print(f"  work items       : {len(report['backfill_items'])}")
    if report.get("index_stale"):
        print("  index stale      : yes")
    if output_arg:
        print(f"  output           : {output_arg}")
    if report["backfill_items"]:
        print("  backfill items:")
        for item in report["backfill_items"]:
            print(
                f"    {item['source_id']} | lane={item['recommended_lane']} | "
                f"exec_current={item['executable_current_law_count']} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']}"
            )
            counts = item.get("candidate_source_counts", {})
            print(
                "      candidates: "
                f"local_corpus={counts.get('local_corpus', 0)}, "
                f"statsrad={counts.get('statsrad', 0)}"
            )
            action_hint = item.get("action_hint", {})
            if isinstance(action_hint, dict):
                next_steps = action_hint.get("next_steps", [])
                if isinstance(next_steps, list) and next_steps:
                    print(f"      next: {next_steps[0]}")
                    for step in next_steps[1:]:
                        print(f"            {step}")
                snapshots = action_hint.get("candidate_snapshots", [])
                if isinstance(snapshots, list) and snapshots:
                    print("      top candidates:")
                    for snapshot in snapshots[:2]:
                        if not isinstance(snapshot, dict):
                            continue
                        print(
                            f"        {snapshot.get('candidate_source', '')} | "
                            f"{snapshot.get('source_id', '')} | "
                            f"score={snapshot.get('score', 0)} | "
                            f"direct={'yes' if snapshot.get('direct_match') else 'no'}"
                        )
                        excerpt = str(snapshot.get("top_match_excerpt", "")).strip()
                        if excerpt:
                            print(f"          {excerpt}")
            next_source_hint = item.get("next_source_hint", {})
            if isinstance(next_source_hint, dict):
                status = str(next_source_hint.get("status", ""))
                primary = str(next_source_hint.get("primary_source_family", ""))
                if status or primary:
                    print(f"      next source: {primary} ({status})")
                suggested_sources = next_source_hint.get("suggested_sources", [])
                if isinstance(suggested_sources, list):
                    for source in suggested_sources:
                        if source:
                            print(f"        - {source}")
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
    parser.add_argument("--output", metavar="FILE", help="write a serialized commencement backfill artifact")
    parser.add_argument("--json", action="store_true")
    main(parser.parse_args())
