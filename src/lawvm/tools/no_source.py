"""lawvm no-source -- inspect one Norway amendment source."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.commencement import (
        apply_no_commencement_overrides,
        build_no_source_report,
        load_no_commencement_overrides,
    )
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index
    from lawvm.tools.no_commencement_candidates import build_no_commencement_candidate_report

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

    report = build_no_source_report(index, source_id=args.source_id, overrides=overrides)
    if report["effective_status"] in {"contingent", "missing", "unknown"}:
        try:
            report["candidate_scan_direct"] = build_no_commencement_candidate_report(
                source_id=args.source_id,
                data_dir=data_dir,
                index_path=index_path,
                limit=5,
                direct_only=True,
            )
            statsrad_candidates = report["candidate_scan_direct"].get("statsrad_candidates")
            if statsrad_candidates:
                report["candidate_scan_statsrad"] = {
                    "candidate_count": report["candidate_scan_direct"].get("statsrad_candidate_count", 0),
                    "candidates": statsrad_candidates,
                }
            report["candidate_scans"] = {
                "local_corpus": {
                    "candidate_count": report["candidate_scan_direct"].get("local_candidate_count", 0),
                    "candidates": report["candidate_scan_direct"].get("local_candidates", []),
                },
                "statsrad": {
                    "candidate_count": report["candidate_scan_direct"].get("statsrad_candidate_count", 0),
                    "candidates": report["candidate_scan_direct"].get("statsrad_candidates", []),
                },
            }
        except Exception as exc:
            report["candidate_scan_direct_error"] = str(exc)
    report.update(staleness)
    limit = getattr(args, "limit", None)
    if isinstance(limit, int):
        report = dict(report)
        report["laws"] = report["laws"][:limit]

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Source Report ===")
    print(f"  source id                        : {report['source_id']}")
    print(f"  title                            : {report['title'] or '(untitled)'}")
    print(f"  effective status                 : {report['effective_status']}")
    if report["effective_date"]:
        print(f"  effective date                   : {report['effective_date']}")
    print(f"  raw date in force                : {report['raw_date_in_force']}")
    print(f"  override state                   : {report['override_state']}")
    if report["override_effective_date"]:
        print(f"  override effective date          : {report['override_effective_date']}")
    if report["override_resolution_kind"]:
        print(f"  override resolution kind         : {report['override_resolution_kind']}")
    if report["override_evidence_source_id"]:
        print(f"  override evidence source         : {report['override_evidence_source_id']}")
    print(f"  current laws                     : {report['current_law_count']}")
    print(f"  executable current laws          : {report['executable_current_law_count']}")
    print(f"  sole blocker current laws        : {report['sole_blocker_current_law_count']}")
    print(
        "  sole blocker executable laws     : "
        f"{report['sole_blocker_executable_current_law_count']}"
    )
    if report.get("index_stale"):
        print("  index stale                      : yes")
    candidate_scan = report.get("candidate_scan_direct")
    candidate_scans = report.get("candidate_scans")
    if isinstance(candidate_scan, dict):
        print(
            "  direct local candidates          : "
            f"{candidate_scan.get('local_candidate_count', candidate_scan.get('candidate_count', 0))}"
        )
    candidate_scan_statsrad = report.get("candidate_scan_statsrad")
    if isinstance(candidate_scan_statsrad, dict):
        print(
            "  direct statsrad evidence        : "
            f"{candidate_scan_statsrad.get('candidate_count', 0)}"
        )
    if isinstance(candidate_scans, dict):
        print("  candidate buckets:")
        for bucket_name in ("local_corpus", "statsrad"):
            bucket = candidate_scans.get(bucket_name, {})
            if not isinstance(bucket, dict):
                continue
            print(
                f"    {bucket_name}: {bucket.get('candidate_count', 0)}"
            )
    laws = report["laws"]
    if laws:
        print("  laws:")
        for item in laws:
            title = item["title"] or "(untitled)"
            print(
                f"    {item['base_id']} | {title} | local_base={'yes' if item['has_local_base_source'] else 'no'} | "
                f"sole_blocker={'yes' if item['sole_blocker'] else 'no'} | blockers={item['blocker_count']}"
            )
    if isinstance(candidate_scan, dict) and candidate_scan.get("candidates"):
        print("  direct local candidates:")
        for item in candidate_scan.get("local_candidates", candidate_scan["candidates"]):
            title = item["title"] or "(untitled)"
            candidate_source = item.get("candidate_source", "local_corpus")
            print(
                f"    {item['source_id']} | source={candidate_source} | score={item['score']} | "
                f"{'commencement' if item['commencement_marker'] else 'no-marker'} | {title}"
            )
    if isinstance(candidate_scan_statsrad, dict) and candidate_scan_statsrad.get("candidates"):
        print("  direct statsrad evidence:")
        for item in candidate_scan_statsrad["candidates"]:
            title = item["title"] or "(untitled)"
            print(
                f"    {item['source_id']} | source=statsrad | score={item['score']} | "
                f"{'commencement' if item['commencement_marker'] else 'no-marker'} | {title}"
            )
