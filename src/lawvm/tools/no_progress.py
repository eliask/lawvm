"""lawvm no-progress -- compact Norway commencement progress summary."""
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
        build_no_progress_report,
        build_no_work_queue,
        export_no_progress_packets,
        load_no_current_law_ids,
        load_no_current_law_titles,
        load_no_commencement_overrides,
    )
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index
    from lawvm.norway.sources import (
        load_available_lti_law_ids,
        load_no_current_law_ids,  # noqa: F811
        load_no_current_law_titles,  # noqa: F811
        resolve_no_source_path,
    )
    from lawvm.tools.no_workqueue import _attach_candidate_split

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

    data_dir = resolve_no_source_path(Path(index.data_dir) if index.data_dir else data_dir)
    current_law_ids = load_no_current_law_ids(data_dir)
    executable_current_law_ids = current_law_ids & load_available_lti_law_ids(data_dir)
    current_law_titles = load_no_current_law_titles(data_dir)

    report = build_no_progress_report(
        index,
        overrides=overrides,
        limit=getattr(args, "limit", 5),
        current_law_ids=current_law_ids,
        executable_current_law_ids=executable_current_law_ids,
        current_law_titles=current_law_titles,
    )
    report["candidate_source_counts"] = _attach_candidate_split(
        report.get("blank_work_items", []) + report.get("untracked_work_items", []),
        data_dir=data_dir,
        index_path=index_path,
    )
    report.update(staleness)

    written_paths: list[Path] = []
    output_dir_arg = getattr(args, "output_dir", None)
    if output_dir_arg:
        blank_report = build_no_work_queue(
            index,
            current_laws_only=True,
            sort_mode="unlock",
            override_state="blank",
            overrides=overrides,
            laws_per_source=3,
            current_law_ids=current_law_ids,
            executable_current_law_ids=executable_current_law_ids,
            current_law_titles=current_law_titles,
        )
        _attach_candidate_split(
            blank_report.get("work_items", []),
            data_dir=data_dir,
            index_path=index_path,
        )
        untracked_report = build_no_work_queue(
            index,
            current_laws_only=True,
            sort_mode="unlock",
            override_state="untracked",
            overrides=overrides,
            laws_per_source=3,
            current_law_ids=current_law_ids,
            executable_current_law_ids=executable_current_law_ids,
            current_law_titles=current_law_titles,
        )
        _attach_candidate_split(
            untracked_report.get("work_items", []),
            data_dir=data_dir,
            index_path=index_path,
        )
        phrase_report = build_no_commencement_phrase_report(
            index,
            current_laws_only=True,
            overrides=overrides,
            sort_mode="unlock",
            current_law_ids=current_law_ids,
            executable_current_law_ids=executable_current_law_ids,
            current_law_titles=current_law_titles,
        )
        written_paths = export_no_progress_packets(
            report,
            blank_report=blank_report,
            untracked_report=untracked_report,
            phrase_report=phrase_report,
            output_dir=Path(output_dir_arg),
        )

    if getattr(args, "json", False):
        payload = dict(report)
        if written_paths:
            payload["written_paths"] = [str(path) for path in written_paths]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Progress ===")
    print(f"  unresolved count : {report['unresolved_count']}")
    print(f"  phrase groups    : {report['phrase_count']}")
    if report["override_state_counts"]:
        print(
            "  override states : "
            + ", ".join(f"{k}={v}" for k, v in sorted(report["override_state_counts"].items()))
        )
    if report.get("index_stale"):
        print("  index stale     : yes")
    if written_paths:
        print(f"  output dir      : {output_dir_arg}")

    if report["blank_work_items"]:
        print("  top blank items:")
        for item in report["blank_work_items"]:
            print(
                f"    {item['source_id']} | exec_current={item['executable_current_law_count']} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']}"
            )
            if item.get("candidate_source_counts"):
                counts = item["candidate_source_counts"]
                print(
                    "      candidates: "
                    f"local_corpus={counts.get('local_corpus', 0)}, "
                    f"statsrad={counts.get('statsrad', 0)}"
                )
    if report["untracked_work_items"]:
        print("  top untracked items:")
        for item in report["untracked_work_items"]:
            print(
                f"    {item['source_id']} | exec_current={item['executable_current_law_count']} | "
                f"sole_exec={item['sole_blocker_executable_current_law_count']}"
            )
            if item.get("candidate_source_counts"):
                counts = item["candidate_source_counts"]
                print(
                    "      candidates: "
                    f"local_corpus={counts.get('local_corpus', 0)}, "
                    f"statsrad={counts.get('statsrad', 0)}"
                )
