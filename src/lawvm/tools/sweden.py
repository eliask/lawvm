"""lawvm sweden — Sweden frontend helpers.

Subcommands:
  compile-official SFS_ID  compile first-pass replace ops from official act JSON
  fetch-current SFS_ID    fetch RK current JSON and archive it
  fetch-official SFS_ID   fetch official doc page + PDF and archive extracted text
  hydrate-bulk            bulk hydrate official/current Sweden artifacts into sweden.farchive
  hydrate-live SFS_ID     fetch RK current JSON and official PDF artifacts end-to-end
  materialize-current     materialize archived RK current JSON at one date
  replay-check SFS_ID     replay compiled official ops against a temporal Sweden base
  diagnose-replay SFS_ID  explain whether replay is feasible from the archived current base
  plan-older-base SFS_ID plan whether an older base can be rebuilt from official-chain inputs
  probe SFS_ID...         refresh/fetch and replay-check a batch of Sweden acts
  probe-base BASE_SFS_ID  discover amending acts from a base statute register and probe them
  show-official SFS_ID    inspect the parsed official SFS act surface
  source-record           build a Sweden SourceRecord from local RK-style JSON
  parse-current           parse current-text IR from local RK-style JSON
  ingest-json             archive local RK-style JSON and derived bundle artifacts
  ingest-scrape-json      archive browser-scraped Sweden doc-page HTML map
  show-archive SFS_ID     inspect archived Sweden bundle/text artifacts
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lawvm.sweden.fetch import (
    analyze_se_official_replay_feasibility,
    archive_se_source_bundle,
    archive_se_backfill_official_history,
    archive_se_backfill_official_checkpoint,
    archive_se_backfill_official_completeness,
    archive_se_backfill_official_gap_report,
    archive_se_backfill_official_chunk_plan,
    archive_se_backfill_official_status,
    attach_official_artifacts_to_bundle,
    build_se_source_bundle,
    check_se_official_replay,
    compile_se_official_ops_to_archive,
    fetch_se_official_artifacts,
    fetch_se_rk_current_json,
    has_valid_se_official_pdf,
    hydrate_se_bundle_live,
    ingest_se_scraped_doc_html_map,
    load_se_bundle_from_archive,
    load_se_current_ir_from_archive,
    load_se_backfill_official_checkpoint_from_archive,
    load_se_backfill_official_completeness_from_archive,
    load_se_backfill_official_chunk_plan_from_archive,
    load_se_backfill_official_history_from_archive,
    load_se_backfill_official_gap_report_from_archive,
    load_se_official_act_from_archive,
    load_se_official_effects_plan_from_archive,
    load_se_official_base_ir_from_archive,
    load_se_official_ops_from_archive,
    load_se_source_record_from_archive,
    open_se_archive,
    plan_se_older_base_rebuild,
    se_bundle_manifest_locator,
    se_backfill_official_checkpoint_locator,
    se_backfill_official_completeness_locator,
    se_backfill_official_chunk_plan_locator,
    se_backfill_official_gap_report_locator,
    se_backfill_official_history_locator,
    se_backfill_official_status_locator,
    se_current_ir_locator,
    se_official_act_locator,
    se_official_doc_locator,
    se_official_ops_locator,
    se_pdf_cleanup_locator,
    se_pdf_text_locator,
    se_rk_current_json_locator,
    se_source_record_locator,
)
from lawvm.sweden.grafter import (
    materialize_se_statute_as_of,
    parse_se_source_record,
    parse_se_statute,
)

if TYPE_CHECKING:
    import argparse


def _read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def _irnode_summary(node: Any, indent: int = 0) -> list[str]:
    label = f" {node.label}" if getattr(node, "label", None) else ""
    text = f" :: {node.text}" if getattr(node, "text", "") else ""
    # Handle both enum and string kinds
    kind_str = node.kind.value if hasattr(node.kind, 'value') else str(node.kind)
    line = f"{'  ' * indent}{kind_str}{label}{text}"
    lines = [line]
    for child in getattr(node, "children", [])[:200]:
        lines.extend(_irnode_summary(child, indent + 1))
    return lines



def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


_SE_DOC_LOCATOR_RE = re.compile(r"^se://sfs/(?P<sfs_id>\d{4}:\d+[a-zA-Z]?)/official\.doc\.html$")


def _se_sort_key(sfs_id: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)(?P<suffix>[a-zA-Z]?)", sfs_id.strip())
    if match is None:
        return (0, 0, sfs_id)
    return (int(match.group("year")), int(match.group("number")), match.group("suffix") or "")


def _se_year_from_sfs_id(sfs_id: str) -> str:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)(?P<suffix>[a-zA-Z]?)", sfs_id.strip())
    if match is None:
        return ""
    return match.group("year")


def _se_backfill_year_buckets(rows: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    year_counts: Counter[str] = Counter()
    year_status_counts: dict[str, Counter[str]] = {}
    for row in rows:
        year = _se_year_from_sfs_id(str(row.get("sfs_id") or ""))
        if not year:
            continue
        year_counts[year] += 1
        status = str(row.get("status") or "")
        if year not in year_status_counts:
            year_status_counts[year] = Counter()
        if status:
            year_status_counts[year][status] += 1
    return dict(sorted(year_counts.items())), {
        year: dict(sorted(counts.items()))
        for year, counts in sorted(year_status_counts.items())
    }


def _se_string_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _se_frontier_signal_count(frontier_counts: dict[str, int]) -> int:
    return sum(int(count or 0) for count in frontier_counts.values())


def _se_gap_range_rows(year_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in year_rows:
        year = int(row["year"])
        remaining_count = int(row["remaining_candidate_count"])
        if remaining_count <= 0:
            if current is not None:
                rows.append(current)
                current = None
            continue
        if current is None:
            current = {
                "start_year": year,
                "end_year": year,
                "year_count": 1,
                "remaining_candidate_count": remaining_count,
                "processed_candidate_count": int(row["processed_candidate_count"]),
                "error_count": int(row["error_count"]),
                "skipped_count": int(row["skipped_count"]),
            }
        else:
            current["end_year"] = year
            current["year_count"] += 1
            current["remaining_candidate_count"] += remaining_count
            current["processed_candidate_count"] += int(row["processed_candidate_count"])
            current["error_count"] += int(row["error_count"])
            current["skipped_count"] += int(row["skipped_count"])
    if current is not None:
        rows.append(current)
    return rows


def _se_backfill_year_range_rows_by_range(
    year_rows: list[dict[str, Any]],
    *,
    start_year: int,
    end_year: int,
) -> list[dict[str, Any]]:
    return [
        row
        for row in year_rows
        if start_year <= int(row.get("year") or 0) <= end_year
    ]


def _se_backfill_chunk_plan_from_gap_report(gap_report: dict[str, Any]) -> dict[str, Any]:
    year_rows = [
        row
        for row in (gap_report.get("year_gap_rows") or [])
        if isinstance(row, dict)
    ]
    year_ranges = [
        row
        for row in (gap_report.get("year_gap_ranges") or [])
        if isinstance(row, dict)
    ]

    range_rows: list[dict[str, Any]] = []
    for range_row in year_ranges:
        start_year = int(range_row.get("start_year") or 0)
        end_year = int(range_row.get("end_year") or 0)
        constituent_rows = _se_backfill_year_range_rows_by_range(
            year_rows,
            start_year=start_year,
            end_year=end_year,
        )
        state_counts: Counter[str] = Counter()
        frontier_counts: Counter[str] = Counter()
        year_names: list[str] = []
        for row in constituent_rows:
            year_name = str(row.get("year") or "")
            if year_name:
                year_names.append(year_name)
            state = str(row.get("state") or "")
            if state:
                state_counts[state] += 1
            frontier_classification_counts = row.get("frontier_classification_counts")
            if isinstance(frontier_classification_counts, dict):
                for frontier_classification, count in frontier_classification_counts.items():
                    frontier_name = str(frontier_classification or "")
                    if frontier_name:
                        frontier_counts[frontier_name] += int(count or 0)
        frontier_signal_count = _se_frontier_signal_count(dict(frontier_counts))
        year_count = int(range_row.get("year_count") or len(constituent_rows) or 0)
        frontier_signal_density = frontier_signal_count / year_count if year_count > 0 else 0.0
        priority_score = int(range_row.get("remaining_candidate_count") or 0) + frontier_signal_count
        range_rows.append(
            {
                "start_year": start_year,
                "end_year": end_year,
                "year_count": int(range_row.get("year_count") or len(constituent_rows)),
                "remaining_candidate_count": int(range_row.get("remaining_candidate_count") or 0),
                "processed_candidate_count": int(range_row.get("processed_candidate_count") or 0),
                "error_count": int(range_row.get("error_count") or 0),
                "skipped_count": int(range_row.get("skipped_count") or 0),
                "years": year_names,
                "state_counts": dict(sorted(state_counts.items())),
                "frontier_classification_counts": dict(sorted(frontier_counts.items())),
                "frontier_signal_count": frontier_signal_count,
                "frontier_signal_density": frontier_signal_density,
                "priority_score": priority_score,
            }
        )

    chronological_ranges = sorted(
        range_rows,
        key=lambda row: (int(row["start_year"]), int(row["end_year"])),
    )
    ranked_ranges = sorted(
        range_rows,
        key=lambda row: (
            -int(row["remaining_candidate_count"]),
            -int(row["error_count"]),
            int(row["start_year"]),
            int(row["end_year"]),
        ),
    )
    priority_ranked_ranges = sorted(
        range_rows,
        key=lambda row: (
            -int(row["priority_score"]),
            -float(row["frontier_signal_density"]),
            -int(row["remaining_candidate_count"]),
            -int(row["error_count"]),
            int(row["start_year"]),
            int(row["end_year"]),
        ),
    )

    recommended_range = chronological_ranges[0] if chronological_ranges else {}
    largest_range = ranked_ranges[0] if ranked_ranges else {}
    priority_range = priority_ranked_ranges[0] if priority_ranked_ranges else {}
    gap_state_counts = dict(sorted((gap_report.get("gap_state_counts") or {}).items()))
    return {
        "jurisdiction": "se",
        "artifact_kind": "sweden_backfill_official_chunk_plan",
        "phase_owner": "P11",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_signature": gap_report.get("run_signature") or {},
        "checkpoint_locator": str(gap_report.get("checkpoint_locator") or ""),
        "status_locator": str(gap_report.get("status_locator") or ""),
        "history_locator": str(gap_report.get("history_locator") or ""),
        "completeness_locator": str(gap_report.get("completeness_locator") or ""),
        "gap_report_locator": se_backfill_official_gap_report_locator(),
        "candidate_universe_count": int(gap_report.get("candidate_universe_count") or 0),
        "processed_candidate_count": int(gap_report.get("processed_candidate_count") or 0),
        "processed_candidate_ratio": float(gap_report.get("processed_candidate_ratio") or 0.0),
        "remaining_candidate_count": max(
            int(gap_report.get("candidate_universe_count") or 0)
            - int(gap_report.get("processed_candidate_count") or 0),
            0,
        ),
        "gap_state_counts": gap_state_counts,
        "recommended_year_range": recommended_range,
        "largest_remaining_year_range": largest_range,
        "ranked_year_ranges": ranked_ranges[:5],
        "priority_year_range": priority_range,
        "priority_ranked_year_ranges": priority_ranked_ranges[:5],
    }


def _store_se_backfill_chunk_plan(
    archive: Any,
) -> None:
    gap_report = load_se_backfill_official_gap_report_from_archive(archive) or {}
    if not isinstance(gap_report, dict):
        gap_report = {}
    archive_se_backfill_official_chunk_plan(
        archive,
        _se_backfill_chunk_plan_from_gap_report(gap_report),
    )


def _se_sfs_ids_from_archive_doc_locators(archive: Any) -> list[str]:
    sfs_ids: set[str] = set()
    for locator in archive.locators("se://sfs/%/official.doc.html"):
        match = _SE_DOC_LOCATOR_RE.fullmatch(str(locator))
        if match is not None:
            sfs_ids.add(match.group("sfs_id"))
    return sorted(sfs_ids, key=_se_sort_key)


def _trim_sfs_ids(sfs_ids: list[str], *, offset: int, limit: int) -> list[str]:
    rows = list(sfs_ids)
    if offset > 0:
        rows = rows[offset:]
    if limit > 0:
        rows = rows[:limit]
    return rows


def _se_generate_candidate_sfs_ids(*, year_start: int, year_end: int, max_number: int) -> list[str]:
    if year_end < year_start:
        return []
    rows: list[str] = []
    for year in range(year_start, year_end + 1):
        for number in range(1, max_number + 1):
            rows.append(f"{year}:{number}")
    return rows


def _se_archive_presence_row(archive: Any, sfs_id: str) -> dict[str, bool]:
    return {
        "doc_html": archive.get(se_official_doc_locator(sfs_id)) is not None,
        "official_pdf": has_valid_se_official_pdf(archive, sfs_id),
        "pdf_text": archive.get(se_pdf_text_locator(sfs_id)) is not None,
        "cleaned_text": archive.get(se_pdf_cleanup_locator(sfs_id)) is not None,
        "official_act": load_se_official_act_from_archive(archive, sfs_id) is not None,
        "official_ops": load_se_official_ops_from_archive(archive, sfs_id) is not None,
        "rk_current": archive.get(se_rk_current_json_locator(sfs_id)) is not None,
        "bundle": load_se_bundle_from_archive(archive, sfs_id) is not None,
    }


def _se_backfill_run_signature(
    *,
    year_start: int,
    year_end: int,
    max_number: int,
    hydrate_current: bool,
    compile_ops: bool,
    force_reextract: bool,
) -> dict[str, Any]:
    return {
        "year_start": year_start,
        "year_end": year_end,
        "max_number": max_number,
        "hydrate_current": hydrate_current,
        "compile_ops": compile_ops,
        "force_reextract": force_reextract,
    }


def _se_backfill_checkpoint_matches(
    checkpoint: dict[str, Any],
    *,
    year_start: int,
    year_end: int,
    max_number: int,
    hydrate_current: bool,
    compile_ops: bool,
    force_reextract: bool,
) -> bool:
    signature = checkpoint.get("run_signature")
    if not isinstance(signature, dict):
        return False
    return signature == _se_backfill_run_signature(
        year_start=year_start,
        year_end=year_end,
        max_number=max_number,
        hydrate_current=hydrate_current,
        compile_ops=compile_ops,
        force_reextract=force_reextract,
    )


def _store_se_backfill_checkpoint(
    archive: Any,
    *,
    run_signature: dict[str, Any],
    total_candidates: int,
    next_index: int,
    last_sfs_id: str,
    last_status: str,
    rows: list[dict[str, Any]],
) -> None:
    status_counts: dict[str, int] = {}
    error_kind_counts: dict[str, int] = {}
    frontier_classification_counts: dict[str, int] = {}
    frontier_detail_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "")
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        error_kind = str(row.get("error_kind") or "")
        if error_kind:
            error_kind_counts[error_kind] = error_kind_counts.get(error_kind, 0) + 1
        frontier_classification = str(row.get("frontier_classification") or "")
        if frontier_classification:
            frontier_classification_counts[frontier_classification] = frontier_classification_counts.get(frontier_classification, 0) + 1
        frontier_detail = str(row.get("frontier_detail") or "")
        if frontier_detail:
            frontier_detail_counts[frontier_detail] = frontier_detail_counts.get(frontier_detail, 0) + 1
    last_error_kind = next(
        (str(row.get("error_kind") or "") for row in reversed(rows) if str(row.get("error_kind") or "")),
        "",
    )
    non_ok_rows = _se_backfill_non_ok_rows(rows)
    archive_se_backfill_official_checkpoint(
        archive,
        {
            "jurisdiction": "se",
            "artifact_kind": "sweden_backfill_official_checkpoint",
            "phase_owner": "P11",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "run_signature": run_signature,
            "total_candidates": total_candidates,
            "next_index": next_index,
            "last_sfs_id": last_sfs_id,
            "last_status": last_status,
            "last_error_kind": last_error_kind,
            "processed_count": len(rows),
            "status_counts": status_counts,
            "error_kind_counts": error_kind_counts,
            "frontier_classification_counts": dict(sorted(frontier_classification_counts.items())),
            "frontier_detail_counts": dict(sorted(frontier_detail_counts.items())),
            "non_ok_count": len(non_ok_rows),
            "non_ok_rows": non_ok_rows,
            "rows": [
                {
                    "sfs_id": str(row.get("sfs_id") or ""),
                    "status": str(row.get("status") or ""),
                    "error_kind": str(row.get("error_kind") or ""),
                    "frontier_classification": str(row.get("frontier_classification") or ""),
                    "frontier_detail": str(row.get("frontier_detail") or ""),
                }
                for row in rows[-10:]
            ],
        },
    )


def _se_backfill_non_ok_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        status = str(row.get("status") or "")
        if status == "ok" or not status:
            continue
        if status == "skipped_complete":
            rule_id = "se_backfill_official_existing_complete_skipped"
            family = "transport_cleanup"
            reason = "Sweden official backfill skipped an already complete source bundle."
        else:
            rule_id = "se_backfill_official_error"
            family = "source_pathology"
            reason = "Sweden official backfill recorded a source acquisition or compilation error."
        result.append(
            {
                "rule_id": rule_id,
                "phase": "acquisition",
                "family": family,
                "reason": reason,
                "sfs_id": str(row.get("sfs_id") or ""),
                "status": status,
                "error_kind": str(row.get("error_kind") or ""),
                "error": str(row.get("error") or ""),
                "frontier_classification": str(row.get("frontier_classification") or ""),
                "frontier_detail": str(row.get("frontier_detail") or ""),
            }
        )
    return result


def _store_se_backfill_status(
    archive: Any,
    *,
    run_signature: dict[str, Any],
    total_candidates: int,
    current_index: int,
    current_sfs_id: str,
    current_stage: str,
    current_stage_state: str,
    last_status: str = "",
    last_error_kind: str = "",
) -> None:
    archive_se_backfill_official_status(
        archive,
        {
            "jurisdiction": "se",
            "artifact_kind": "sweden_backfill_official_status",
            "phase_owner": "P11",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "run_signature": run_signature,
            "total_candidates": total_candidates,
            "current_index": current_index,
            "current_sfs_id": current_sfs_id,
            "current_stage": current_stage,
            "current_stage_state": current_stage_state,
            "last_status": last_status,
            "last_error_kind": last_error_kind,
        },
    )


def _store_se_backfill_history(
    archive: Any,
    *,
    run_record: dict[str, Any],
) -> None:
    history = load_se_backfill_official_history_from_archive(archive) or []
    history = list(history)
    history.append(run_record)
    archive_se_backfill_official_history(archive, history)


def _store_se_backfill_gap_report(
    archive: Any,
    *,
    run_signature: dict[str, Any],
    sweep_candidate_count: int,
    chunk_candidate_count: int,
    checkpoint_locator: str,
    status_locator: str,
    history_locator: str,
    completeness_locator: str,
) -> None:
    completeness = load_se_backfill_official_completeness_from_archive(archive) or {}
    history = load_se_backfill_official_history_from_archive(archive) or []
    matching_history = [
        entry
        for entry in history
        if isinstance(entry, dict) and entry.get("run_signature") == run_signature
    ]

    processed_year_counts: Counter[str] = Counter()
    processed_year_status_counts: dict[str, Counter[str]] = {}
    processed_year_frontier_classification_counts: dict[str, Counter[str]] = {}
    frontier_detail_counts: Counter[str] = Counter()
    for entry in matching_history:
        chunk_year_counts = entry.get("chunk_year_counts")
        if isinstance(chunk_year_counts, dict):
            for year, count in chunk_year_counts.items():
                year_text = str(year or "")
                if year_text:
                    processed_year_counts[year_text] += int(count or 0)
        chunk_year_status_counts = entry.get("chunk_year_status_counts")
        if isinstance(chunk_year_status_counts, dict):
            for year, counts in chunk_year_status_counts.items():
                year_text = str(year or "")
                if not year_text:
                    continue
                if year_text not in processed_year_status_counts:
                    processed_year_status_counts[year_text] = Counter()
                if isinstance(counts, dict):
                    for status, count in counts.items():
                        status_text = str(status or "")
                        if status_text:
                            processed_year_status_counts[year_text][status_text] += int(count or 0)
        chunk_year_frontier_counts = entry.get("chunk_year_frontier_classification_counts")
        if isinstance(chunk_year_frontier_counts, dict):
            for year, counts in chunk_year_frontier_counts.items():
                year_text = str(year or "")
                if not year_text:
                    continue
                if year_text not in processed_year_frontier_classification_counts:
                    processed_year_frontier_classification_counts[year_text] = Counter()
                if isinstance(counts, dict):
                    for frontier_classification, count in counts.items():
                        frontier_text = str(frontier_classification or "")
                        if frontier_text:
                            processed_year_frontier_classification_counts[year_text][frontier_text] += int(count or 0)
        detail_counts = entry.get("frontier_detail_counts")
        if isinstance(detail_counts, dict):
            for detail, count in detail_counts.items():
                detail_text = str(detail or "")
                if detail_text:
                    frontier_detail_counts[detail_text] += int(count or 0)

    sweep_year_counts = {
        str(year): int(count or 0)
        for year, count in (completeness.get("sweep_year_counts") or {}).items()
        if str(year or "")
    }
    year_rows: list[dict[str, Any]] = []
    for year in sorted(set(sweep_year_counts) | set(processed_year_counts) | set(processed_year_status_counts), key=lambda item: int(item)):
        sweep_count = int(sweep_year_counts.get(year, 0))
        processed_count = int(processed_year_counts.get(year, 0))
        status_counts = dict(sorted(processed_year_status_counts.get(year, Counter()).items()))
        frontier_classification_counts = dict(sorted(processed_year_frontier_classification_counts.get(year, Counter()).items()))
        completed_count = int(status_counts.get("ok", 0))
        skipped_count = int(status_counts.get("skipped_complete", 0))
        error_count = int(status_counts.get("error", 0))
        remaining_count = max(sweep_count - processed_count, 0)
        if processed_count <= 0:
            state = "untouched"
        elif remaining_count > 0 and error_count > 0:
            state = "partial_with_errors"
        elif remaining_count > 0:
            state = "partial"
        elif error_count > 0:
            state = "completed_with_errors"
        else:
            state = "complete"
        year_rows.append(
            {
                "year": year,
                "sweep_candidate_count": sweep_count,
                "processed_candidate_count": processed_count,
                "remaining_candidate_count": remaining_count,
                "completed_count": completed_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "status_counts": status_counts,
                "frontier_classification_counts": frontier_classification_counts,
                "state": state,
            }
        )

    archive_se_backfill_official_gap_report(
        archive,
        {
            "jurisdiction": "se",
            "artifact_kind": "sweden_backfill_official_gap_report",
            "phase_owner": "P11",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "run_signature": run_signature,
            "candidate_universe_count": int(completeness.get("candidate_universe_count") or sweep_candidate_count),
            "processed_candidate_count": int(completeness.get("processed_candidate_count") or 0),
            "processed_candidate_ratio": float(completeness.get("processed_candidate_ratio") or 0.0),
            "run_count": int(completeness.get("run_count") or len(matching_history)),
            "checkpoint_locator": checkpoint_locator,
            "status_locator": status_locator,
            "history_locator": history_locator,
            "completeness_locator": completeness_locator,
            "sweep_year_counts": sweep_year_counts,
            "processed_year_counts": dict(sorted(processed_year_counts.items())),
            "remaining_year_counts": {
                row["year"]: row["remaining_candidate_count"]
                for row in year_rows
            },
            "year_gap_rows": year_rows,
            "year_gap_ranges": _se_gap_range_rows(year_rows),
            "gap_state_counts": {
                state: sum(1 for row in year_rows if row["state"] == state)
                for state in sorted({row["state"] for row in year_rows})
            },
            "processed_year_frontier_classification_counts": {
                year: dict(sorted(counts.items()))
                for year, counts in sorted(processed_year_frontier_classification_counts.items())
            },
            "frontier_detail_counts": dict(sorted(frontier_detail_counts.items())),
            "latest_gap_year": next((row["year"] for row in year_rows if row["remaining_candidate_count"] > 0), ""),
        },
    )


def _store_se_backfill_completeness(
    archive: Any,
    *,
    run_signature: dict[str, Any],
    sweep_candidate_count: int,
    chunk_candidate_count: int,
    rows: list[dict[str, Any]],
    checkpoint_locator: str,
    status_locator: str,
    history_locator: str,
) -> None:
    checkpoint = load_se_backfill_official_checkpoint_from_archive(archive) or {}
    history = load_se_backfill_official_history_from_archive(archive) or []
    matching_history = [
        entry
        for entry in history
        if isinstance(entry, dict) and entry.get("run_signature") == run_signature
    ]
    outcome_kind_counts: Counter[str] = Counter()
    error_kind_counts: Counter[str] = Counter()
    frontier_classification_counts: Counter[str] = Counter()
    frontier_detail_counts: Counter[str] = Counter()
    for entry in matching_history:
        outcome_kind = str(entry.get("outcome_kind") or "")
        if outcome_kind:
            outcome_kind_counts[outcome_kind] += 1
        error_counts = entry.get("error_kind_counts")
        if isinstance(error_counts, dict):
            for kind, count in error_counts.items():
                kind_text = str(kind or "")
                if kind_text:
                    error_kind_counts[kind_text] += int(count or 0)
        frontier_counts = entry.get("frontier_classification_counts")
        if isinstance(frontier_counts, dict):
            for kind, count in frontier_counts.items():
                kind_text = str(kind or "")
                if kind_text:
                    frontier_classification_counts[kind_text] += int(count or 0)
        detail_counts = entry.get("frontier_detail_counts")
        if isinstance(detail_counts, dict):
            for kind, count in detail_counts.items():
                kind_text = str(kind or "")
                if kind_text:
                    frontier_detail_counts[kind_text] += int(count or 0)
    latest_history = matching_history[-1] if matching_history else {}
    processed_candidate_count = int(checkpoint.get("next_index") or 0)
    chunk_year_counts: Counter[str] = Counter()
    chunk_year_status_counts: dict[str, Counter[str]] = {}
    chunk_year_frontier_classification_counts: dict[str, Counter[str]] = {}
    for row in rows:
        year = _se_year_from_sfs_id(str(row.get("sfs_id") or ""))
        if not year:
            continue
        chunk_year_counts[year] += 1
        status = str(row.get("status") or "")
        if year not in chunk_year_status_counts:
            chunk_year_status_counts[year] = Counter()
        if status:
            chunk_year_status_counts[year][status] += 1
        frontier_classification = str(row.get("frontier_classification") or "")
        if year not in chunk_year_frontier_classification_counts:
            chunk_year_frontier_classification_counts[year] = Counter()
        if frontier_classification:
            chunk_year_frontier_classification_counts[year][frontier_classification] += 1
    sweep_year_counts: dict[str, int] = {}
    year_start = int(run_signature.get("year_start") or 0)
    year_end = int(run_signature.get("year_end") or 0)
    max_number = int(run_signature.get("max_number") or 0)
    for year in range(year_start, year_end + 1):
        sweep_year_counts[str(year)] = max_number
    archive_se_backfill_official_completeness(
        archive,
        {
            "jurisdiction": "se",
            "artifact_kind": "sweden_backfill_official_completeness",
            "phase_owner": "P11",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "run_signature": run_signature,
            "candidate_universe_count": sweep_candidate_count,
            "chunk_candidate_count": chunk_candidate_count,
            "processed_candidate_count": processed_candidate_count,
            "processed_candidate_ratio": (processed_candidate_count / sweep_candidate_count) if sweep_candidate_count else 0.0,
            "run_count": len(matching_history),
            "sweep_year_counts": sweep_year_counts,
            "chunk_year_counts": dict(sorted(chunk_year_counts.items())),
            "chunk_year_status_counts": {
                year: dict(sorted(counts.items()))
                for year, counts in sorted(chunk_year_status_counts.items())
            },
            "chunk_year_frontier_classification_counts": {
                year: dict(sorted(counts.items()))
                for year, counts in sorted(chunk_year_frontier_classification_counts.items())
            },
            "outcome_kind_counts": dict(sorted(outcome_kind_counts.items())),
            "error_kind_counts": dict(sorted(error_kind_counts.items())),
            "frontier_classification_counts": dict(sorted(frontier_classification_counts.items())),
            "frontier_detail_counts": dict(sorted(frontier_detail_counts.items())),
            "checkpoint_locator": checkpoint_locator,
            "status_locator": status_locator,
            "history_locator": history_locator,
            "checkpoint_next_index": int(checkpoint.get("next_index") or 0),
            "checkpoint_last_sfs_id": str(checkpoint.get("last_sfs_id") or ""),
            "latest_outcome_kind": str(latest_history.get("outcome_kind") or ""),
            "latest_dominant_error_kind": str(latest_history.get("dominant_error_kind") or ""),
            "latest_completion_ratio": float(latest_history.get("completion_ratio") or 0.0),
            "latest_error_rate": float(latest_history.get("error_rate") or 0.0),
            "latest_history_started_at_utc": str(latest_history.get("started_at_utc") or ""),
            "latest_history_finished_at_utc": str(latest_history.get("finished_at_utc") or ""),
        },
    )


def _se_backfill_outcome_kind(*, completed_count: int, skipped_count: int, error_count: int) -> str:
    if error_count > 0 and completed_count > 0 and skipped_count > 0:
        return "mixed_completed_skipped_error"
    if error_count > 0 and completed_count > 0:
        return "mixed_completed_error"
    if error_count > 0 and skipped_count > 0:
        return "mixed_skipped_error"
    if error_count > 0:
        return "error_only"
    if completed_count > 0 and skipped_count > 0:
        return "completed_with_skips"
    if completed_count > 0:
        return "completed_only"
    if skipped_count > 0:
        return "skipped_only"
    return "empty"


def _hydrate_se_bulk(
    archive: Any,
    sfs_ids: list[str],
    *,
    hydrate_current: bool,
    compile_ops: bool,
    force_reextract: bool,
    official_max_age_hours: float,
    current_max_age_hours: float,
    skip_complete: bool,
    progress_callback: Any = None,
    status_callback: Any = None,
    checkpoint_callback: Any = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(sfs_ids)
    for idx, sfs_id in enumerate(sfs_ids, start=1):
        before = _se_archive_presence_row(archive, sfs_id)
        row: dict[str, Any] = {"sfs_id": sfs_id, "before": before}
        if progress_callback is not None:
            progress_callback(f"[{idx}/{total}] {sfs_id} START")
        if status_callback is not None:
            status_callback(idx, total, sfs_id, "START", "running", "", "")
        fully_complete_before = (
            before["doc_html"]
            and before["official_pdf"]
            and before["pdf_text"]
            and before["cleaned_text"]
            and before["official_act"]
            and (before["official_ops"] or not compile_ops)
            and (before["rk_current"] or not hydrate_current)
        )
        if skip_complete and fully_complete_before:
            row["status"] = "skipped_complete"
            row["after"] = before
            rows.append(row)
            if checkpoint_callback is not None:
                checkpoint_callback(idx, total, sfs_id, row, rows)
            if status_callback is not None:
                status_callback(idx, total, sfs_id, "DONE", "completed", str(row.get("status") or ""), str(row.get("error_kind") or ""))
            if progress_callback is not None:
                progress_callback(f"[{idx}/{total}] {sfs_id} SKIP complete")
            continue
        try:
            if progress_callback is not None:
                progress_callback(f"[{idx}/{total}] {sfs_id} FETCH_OFFICIAL")
            if status_callback is not None:
                status_callback(idx, total, sfs_id, "FETCH_OFFICIAL", "running", "", "")
            official = fetch_se_official_artifacts(
                sfs_id,
                archive,
                max_age_hours=official_max_age_hours,
                force_reextract=force_reextract,
            )
            row["official_fetched"] = official is not None
            if official is not None:
                row["doc_url"] = official.doc_url
                row["pdf_url"] = official.pdf_url
            if hydrate_current:
                if progress_callback is not None:
                    progress_callback(f"[{idx}/{total}] {sfs_id} FETCH_CURRENT")
                if status_callback is not None:
                    status_callback(idx, total, sfs_id, "FETCH_CURRENT", "running", "", "")
                current_diagnostics: list[dict[str, Any]] = []
                current_json = fetch_se_rk_current_json(
                    sfs_id,
                    archive,
                    max_age_hours=current_max_age_hours,
                    diagnostics_out=current_diagnostics,
                )
                row["current_fetched"] = current_json is not None
                if current_diagnostics:
                    row["current_diagnostic_count"] = len(current_diagnostics)
                    row["current_diagnostics"] = current_diagnostics
                if current_json is not None:
                    bundle = archive_se_source_bundle(current_json, archive)
                    if official is not None:
                        attach_official_artifacts_to_bundle(bundle, official)
            if compile_ops:
                if progress_callback is not None:
                    progress_callback(f"[{idx}/{total}] {sfs_id} COMPILE_OPS")
                if status_callback is not None:
                    status_callback(idx, total, sfs_id, "COMPILE_OPS", "running", "", "")
                act = load_se_official_act_from_archive(archive, sfs_id)
                if isinstance(act, dict) and act.get("is_amending_act"):
                    ops = compile_se_official_ops_to_archive(archive, sfs_id)
                    row["compiled_ops"] = len(ops)
                else:
                    row["compiled_ops"] = None
            row["after"] = _se_archive_presence_row(archive, sfs_id)
            row["status"] = "ok"
            if progress_callback is not None:
                compiled_ops = row.get("compiled_ops")
                compiled_text = "-"
                if isinstance(compiled_ops, int):
                    compiled_text = str(compiled_ops)
                progress_callback(
                    f"[{idx}/{total}] {sfs_id} OK "
                    f"pdf={'yes' if row.get('after', {}).get('official_pdf') else 'no'} "
                    f"act={'yes' if row.get('after', {}).get('official_act') else 'no'} "
                    f"ops={compiled_text} "
                    f"rk={'yes' if row.get('after', {}).get('rk_current') else 'no'}"
                )
        except Exception as exc:
            row["status"] = "error"
            row["error_kind"] = type(exc).__name__
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["after"] = _se_archive_presence_row(archive, sfs_id)
            row["frontier_classification"] = ""
            row["frontier_detail"] = ""
            if row["error_kind"] == "NotImplementedError":
                effects_plan = load_se_official_effects_plan_from_archive(archive, sfs_id)
                if isinstance(effects_plan, dict):
                    frontier_classification = str(effects_plan.get("frontier_classification") or "")
                    frontier_detail = str(effects_plan.get("frontier_detail") or "")
                    row["frontier_classification"] = frontier_classification
                    row["frontier_detail"] = frontier_detail
                    if frontier_classification:
                        row["error_kind"] = frontier_classification
            if status_callback is not None:
                status_callback(
                    idx,
                    total,
                    sfs_id,
                    "ERROR",
                    "error",
                    str(row.get("status") or ""),
                    str(row.get("error_kind") or ""),
                )
            if progress_callback is not None:
                frontier_text = ""
                if row.get("frontier_classification"):
                    frontier_text = f" frontier={row['frontier_classification']}"
                    if row.get("frontier_detail"):
                        frontier_text += f" detail={row['frontier_detail']}"
                progress_callback(f"[{idx}/{total}] {sfs_id} ERROR {row['error_kind']}{frontier_text} {row['error']}")
        rows.append(row)
        if status_callback is not None and row.get("status") == "ok":
            status_callback(
                idx,
                total,
                sfs_id,
                "DONE",
                "completed",
                str(row.get("status") or ""),
                str(row.get("error_kind") or ""),
            )
        if checkpoint_callback is not None:
            checkpoint_callback(idx, total, sfs_id, row, rows)
    return rows


def _print_hydrate_bulk_rows(rows: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        status = str(row.get("status") or "")
        sfs_id = str(row.get("sfs_id") or "")
        if status == "ok":
            compiled_ops = row.get("compiled_ops")
            compiled_text = "-"
            if isinstance(compiled_ops, int):
                compiled_text = str(compiled_ops)
            print(
                f"{sfs_id}  OK  "
                f"pdf={'yes' if row.get('after', {}).get('official_pdf') else 'no'}  "
                f"act={'yes' if row.get('after', {}).get('official_act') else 'no'}  "
                f"ops={compiled_text}  "
                f"rk={'yes' if row.get('after', {}).get('rk_current') else 'no'}"
                f"{'  current_diag=' + str(row['current_diagnostic_count']) if row.get('current_diagnostic_count') else ''}"
            )
        elif status == "skipped_complete":
            print(f"{sfs_id}  SKIP  complete")
        else:
            frontier_classification = str(row.get("frontier_classification") or "")
            if frontier_classification:
                frontier_detail = str(row.get("frontier_detail") or "")
                detail_text = f" detail={frontier_detail}" if frontier_detail else ""
                print(f"{sfs_id}  ERROR  {row.get('error', '')}  frontier={frontier_classification}{detail_text}")
            else:
                print(f"{sfs_id}  ERROR  {row.get('error', '')}")


def _cmd_hydrate_bulk(args: "argparse.Namespace") -> None:
    payload = _read_bytes(args.scrape_json) if getattr(args, "scrape_json", None) else None
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        ingest_summary: dict[str, Any] | None = None
        if payload is not None:
            ingest_summary = ingest_se_scraped_doc_html_map(payload, archive)
        sfs_ids = list(getattr(args, "sfs_ids", []) or [])
        if not sfs_ids:
            sfs_ids = _se_sfs_ids_from_archive_doc_locators(archive)
        sfs_ids = _trim_sfs_ids(
            sfs_ids,
            offset=int(getattr(args, "offset", 0) or 0),
            limit=int(getattr(args, "limit", 0) or 0),
        )
        rows = _hydrate_se_bulk(
            archive,
            sfs_ids,
            hydrate_current=bool(getattr(args, "hydrate_current", False)),
            compile_ops=bool(getattr(args, "compile_ops", False)),
            force_reextract=bool(getattr(args, "force_reextract", False)),
            official_max_age_hours=(
                float("inf")
                if getattr(args, "official_max_age_hours", None) is None
                else float(args.official_max_age_hours)
            ),
            current_max_age_hours=(
                24.0
                if getattr(args, "current_max_age_hours", None) is None
                else float(args.current_max_age_hours)
            ),
            skip_complete=not bool(getattr(args, "no_skip_complete", False)),
            progress_callback=(
                (lambda msg: print(msg, file=sys.stderr, flush=True))
                if getattr(args, "format", "summary") != "json"
                else None
            ),
        )
    if getattr(args, "format", "summary") == "json":
        print(
            json.dumps(
                {
                    "input_count": len(sfs_ids),
                    "ingest_summary": ingest_summary,
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"Input IDs:          {len(sfs_ids)}")
    if ingest_summary is not None:
        print(f"Scrape imported:    {ingest_summary['imported_count']}")
        print(f"Parsed PDF links:   {ingest_summary['resolved_pdf_link_count']}")
    print(f"Completed:          {sum(1 for row in rows if row.get('status') == 'ok')}")
    print(f"Skipped complete:   {sum(1 for row in rows if row.get('status') == 'skipped_complete')}")
    print(f"Errors:             {sum(1 for row in rows if row.get('status') == 'error')}")
    error_kind_counts: dict[str, int] = {}
    for row in rows:
        error_kind = str(row.get("error_kind") or "")
        if error_kind:
            error_kind_counts[error_kind] = error_kind_counts.get(error_kind, 0) + 1
    if error_kind_counts:
        print(f"Error kinds:        {', '.join(f'{kind}={count}' for kind, count in sorted(error_kind_counts.items()))}")
    print()
    _print_hydrate_bulk_rows(rows, as_json=False)


def _cmd_backfill_official(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        started_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        year_start = int(getattr(args, "year_start", 1999) or 1999)
        year_end = int(getattr(args, "year_end", 2026) or 2026)
        max_number = int(getattr(args, "max_number", 2100) or 2100)
        hydrate_current = bool(getattr(args, "hydrate_current", False))
        compile_ops = bool(getattr(args, "compile_ops", False))
        force_reextract = bool(getattr(args, "force_reextract", False))
        initial_offset = int(getattr(args, "offset", 0) or 0)
        checkpoint = load_se_backfill_official_checkpoint_from_archive(archive) if bool(getattr(args, "resume", False)) else None
        if checkpoint is not None and _se_backfill_checkpoint_matches(
            checkpoint,
            year_start=year_start,
            year_end=year_end,
            max_number=max_number,
            hydrate_current=hydrate_current,
            compile_ops=compile_ops,
            force_reextract=force_reextract,
        ):
            checkpoint_offset = int(checkpoint.get("next_index") or 0)
            if checkpoint_offset > initial_offset:
                initial_offset = checkpoint_offset
        elif checkpoint is not None and bool(getattr(args, "resume", False)):
            print(
                "Checkpoint ignored: run signature does not match current parameters",
                file=sys.stderr,
                flush=True,
            )
        sweep_sfs_ids = _se_generate_candidate_sfs_ids(
            year_start=year_start,
            year_end=year_end,
            max_number=max_number,
        )
        sweep_candidate_count = len(sweep_sfs_ids)
        sfs_ids = _trim_sfs_ids(
            sweep_sfs_ids,
            offset=initial_offset,
            limit=int(getattr(args, "limit", 0) or 0),
        )
        chunk_candidate_count = len(sfs_ids)
        run_signature = _se_backfill_run_signature(
            year_start=year_start,
            year_end=year_end,
            max_number=max_number,
            hydrate_current=hydrate_current,
            compile_ops=compile_ops,
            force_reextract=force_reextract,
        )
        rows = _hydrate_se_bulk(
            archive,
            sfs_ids,
            hydrate_current=hydrate_current,
            compile_ops=compile_ops,
            force_reextract=force_reextract,
            official_max_age_hours=(
                float("inf")
                if getattr(args, "official_max_age_hours", None) is None
                else float(args.official_max_age_hours)
            ),
            current_max_age_hours=(
                24.0
                if getattr(args, "current_max_age_hours", None) is None
                else float(args.current_max_age_hours)
            ),
            skip_complete=not bool(getattr(args, "no_skip_complete", False)),
            progress_callback=(
                (lambda msg: print(msg, file=sys.stderr, flush=True))
                if getattr(args, "format", "summary") != "json"
                else None
            ),
            status_callback=lambda idx, total, sfs_id, stage, stage_state, last_status, last_error_kind: _store_se_backfill_status(
                archive,
                run_signature=run_signature if "run_signature" in locals() else _se_backfill_run_signature(
                    year_start=year_start,
                    year_end=year_end,
                    max_number=max_number,
                    hydrate_current=hydrate_current,
                    compile_ops=compile_ops,
                    force_reextract=force_reextract,
                ),
                total_candidates=total,
                current_index=initial_offset + idx,
                current_sfs_id=sfs_id,
                current_stage=stage,
                current_stage_state=stage_state,
                last_status=last_status,
                last_error_kind=last_error_kind,
            ),
            checkpoint_callback=lambda idx, total, sfs_id, row, rows: _store_se_backfill_checkpoint(
                archive,
                run_signature=run_signature,
                total_candidates=total,
                next_index=initial_offset + idx,
                last_sfs_id=sfs_id,
                last_status=str(row.get("status") or ""),
                rows=rows,
            ),
        )
        checkpoint_locator = se_backfill_official_checkpoint_locator()
        history_locator = se_backfill_official_history_locator()
        completed_count = sum(1 for row in rows if row.get("status") == "ok")
        skipped_count = sum(1 for row in rows if row.get("status") == "skipped_complete")
        error_count = sum(1 for row in rows if row.get("status") == "error")
        error_kind_counts: dict[str, int] = {}
        frontier_classification_counts: dict[str, int] = {}
        frontier_detail_counts: dict[str, int] = {}
        chunk_year_frontier_classification_counts: dict[str, Counter[str]] = {}
        for row in rows:
            error_kind = str(row.get("error_kind") or "")
            if error_kind:
                error_kind_counts[error_kind] = error_kind_counts.get(error_kind, 0) + 1
            frontier_classification = str(row.get("frontier_classification") or "")
            if frontier_classification:
                frontier_classification_counts[frontier_classification] = frontier_classification_counts.get(frontier_classification, 0) + 1
            frontier_detail = str(row.get("frontier_detail") or "")
            if frontier_detail:
                frontier_detail_counts[frontier_detail] = frontier_detail_counts.get(frontier_detail, 0) + 1
            year = _se_year_from_sfs_id(str(row.get("sfs_id") or ""))
            if year:
                if year not in chunk_year_frontier_classification_counts:
                    chunk_year_frontier_classification_counts[year] = Counter()
                if frontier_classification:
                    chunk_year_frontier_classification_counts[year][frontier_classification] += 1
        dominant_error_kind = ""
        dominant_error_kind_count = 0
        if error_kind_counts:
            dominant_error_kind, dominant_error_kind_count = max(
                error_kind_counts.items(),
                key=lambda item: (item[1], item[0]),
            )
        outcome_kind = _se_backfill_outcome_kind(
            completed_count=completed_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )
        last_row = rows[-1] if rows else {}
        non_ok_rows = _se_backfill_non_ok_rows(rows)
        chunk_year_counts, chunk_year_status_counts = _se_backfill_year_buckets(rows)
        _store_se_backfill_history(
            archive,
            run_record={
                "jurisdiction": "se",
                "artifact_kind": "sweden_backfill_official_run_history",
                "phase_owner": "P11",
                "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "started_at_utc": started_at_utc,
                "finished_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "run_signature": run_signature,
                "input_count": chunk_candidate_count,
                "chunk_candidate_count": chunk_candidate_count,
                "sweep_candidate_count": sweep_candidate_count,
                "chunk_year_counts": chunk_year_counts,
                "chunk_year_status_counts": chunk_year_status_counts,
                "chunk_year_frontier_classification_counts": {
                    year: dict(sorted(counts.items()))
                    for year, counts in sorted(chunk_year_frontier_classification_counts.items())
                    if counts
                },
                "completed_count": completed_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "error_kind_counts": error_kind_counts,
                "frontier_classification_counts": frontier_classification_counts,
                "frontier_detail_counts": frontier_detail_counts,
                "outcome_kind": outcome_kind,
                "dominant_error_kind": dominant_error_kind,
                "dominant_error_kind_count": dominant_error_kind_count,
                "completion_ratio": (completed_count / chunk_candidate_count) if chunk_candidate_count else 0.0,
                "error_rate": (error_count / chunk_candidate_count) if chunk_candidate_count else 0.0,
                "non_ok_count": len(non_ok_rows),
                "non_ok_rows": non_ok_rows,
                "checkpoint_locator": checkpoint_locator,
                "status_locator": se_backfill_official_status_locator(),
                "last_sfs_id": str(last_row.get("sfs_id") or ""),
                "last_status": str(last_row.get("status") or ""),
                "last_error_kind": str(last_row.get("error_kind") or ""),
                "rows_tail": [
                    {
                        "sfs_id": str(row.get("sfs_id") or ""),
                        "status": str(row.get("status") or ""),
                        "error_kind": str(row.get("error_kind") or ""),
                        "frontier_classification": str(row.get("frontier_classification") or ""),
                        "frontier_detail": str(row.get("frontier_detail") or ""),
                    }
                    for row in rows[-10:]
                ],
            },
        )
        _store_se_backfill_completeness(
            archive,
            run_signature=run_signature,
            sweep_candidate_count=sweep_candidate_count,
            chunk_candidate_count=chunk_candidate_count,
            rows=rows,
            checkpoint_locator=checkpoint_locator,
            status_locator=se_backfill_official_status_locator(),
            history_locator=history_locator,
        )
        _store_se_backfill_gap_report(
            archive,
            run_signature=run_signature,
            sweep_candidate_count=sweep_candidate_count,
            chunk_candidate_count=chunk_candidate_count,
            checkpoint_locator=checkpoint_locator,
            status_locator=se_backfill_official_status_locator(),
            history_locator=history_locator,
            completeness_locator=se_backfill_official_completeness_locator(),
        )
        _store_se_backfill_chunk_plan(
            archive,
        )
    if getattr(args, "format", "summary") == "json":
        print(
            json.dumps(
                {
                    "input_count": len(sfs_ids),
                    "year_start": int(getattr(args, "year_start", 1999) or 1999),
                    "year_end": int(getattr(args, "year_end", 2026) or 2026),
                    "max_number": int(getattr(args, "max_number", 2100) or 2100),
                    "checkpoint_locator": checkpoint_locator,
                    "completeness_locator": se_backfill_official_completeness_locator(),
                    "gap_report_locator": se_backfill_official_gap_report_locator(),
                    "chunk_plan_locator": se_backfill_official_chunk_plan_locator(),
                    "history_locator": history_locator,
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"Input IDs:          {len(sfs_ids)}")
    print(f"Sweep candidates:   {sweep_candidate_count}")
    print(f"Year range:         {int(getattr(args, 'year_start', 1999) or 1999)}..{int(getattr(args, 'year_end', 2026) or 2026)}")
    print(f"Max number/year:    {int(getattr(args, 'max_number', 2100) or 2100)}")
    print(f"Chunk size:         {len(sfs_ids)}")
    print(f"Completed:          {sum(1 for row in rows if row.get('status') == 'ok')}")
    print(f"Skipped complete:   {sum(1 for row in rows if row.get('status') == 'skipped_complete')}")
    print(f"Errors:             {sum(1 for row in rows if row.get('status') == 'error')}")
    error_kind_counts: dict[str, int] = {}
    frontier_classification_counts: dict[str, int] = {}
    frontier_detail_counts: dict[str, int] = {}
    for row in rows:
        error_kind = str(row.get("error_kind") or "")
        if error_kind:
            error_kind_counts[error_kind] = error_kind_counts.get(error_kind, 0) + 1
        frontier_classification = str(row.get("frontier_classification") or "")
        if frontier_classification:
            frontier_classification_counts[frontier_classification] = frontier_classification_counts.get(frontier_classification, 0) + 1
        frontier_detail = str(row.get("frontier_detail") or "")
        if frontier_detail:
            frontier_detail_counts[frontier_detail] = frontier_detail_counts.get(frontier_detail, 0) + 1
    if rows:
        completed_count = sum(1 for row in rows if row.get("status") == "ok")
        skipped_count = sum(1 for row in rows if row.get("status") == "skipped_complete")
        error_count = sum(1 for row in rows if row.get("status") == "error")
        outcome_kind = _se_backfill_outcome_kind(
            completed_count=completed_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )
        print(f"Run outcome:        {outcome_kind}")
    if error_kind_counts:
        print(f"Error kinds:        {', '.join(f'{kind}={count}' for kind, count in sorted(error_kind_counts.items()))}")
    if frontier_classification_counts:
        print(
            f"Frontier classes:   {', '.join(f'{kind}={count}' for kind, count in sorted(frontier_classification_counts.items()))}"
        )
    if frontier_detail_counts:
        print(
            f"Frontier detail:    {', '.join(f'{kind}={count}' for kind, count in sorted(frontier_detail_counts.items()))}"
        )
    print(f"Checkpoint:         {checkpoint_locator}")
    print(f"Completeness:       {se_backfill_official_completeness_locator()}")
    print(f"Gap report:         {se_backfill_official_gap_report_locator()}")
    print(f"Chunk plan:         {se_backfill_official_chunk_plan_locator()}")
    chunk_plan = load_se_backfill_official_chunk_plan_from_archive(archive) or {}
    if isinstance(chunk_plan, dict):
        recommended_range = chunk_plan.get("recommended_year_range") or {}
        if isinstance(recommended_range, dict) and recommended_range:
            start_year = recommended_range.get("start_year")
            end_year = recommended_range.get("end_year")
            remaining_count = recommended_range.get("remaining_candidate_count")
            print(
                f"Next range:         {start_year}..{end_year} "
                f"({remaining_count} remaining)"
            )
        priority_range = chunk_plan.get("priority_year_range") or {}
        if isinstance(priority_range, dict) and priority_range:
            start_year = priority_range.get("start_year")
            end_year = priority_range.get("end_year")
            remaining_count = priority_range.get("remaining_candidate_count")
            frontier_signal_count = priority_range.get("frontier_signal_count")
            print(
                f"Priority range:     {start_year}..{end_year} "
                f"({remaining_count} remaining, {frontier_signal_count} frontier signals)"
            )
        largest_range = chunk_plan.get("largest_remaining_year_range") or {}
        if isinstance(largest_range, dict) and largest_range:
            start_year = largest_range.get("start_year")
            end_year = largest_range.get("end_year")
            remaining_count = largest_range.get("remaining_candidate_count")
            print(
                f"Largest gap:        {start_year}..{end_year} "
                f"({remaining_count} remaining)"
            )
    print(f"History:            {history_locator}")
    print()
    _print_hydrate_bulk_rows(rows, as_json=False)


def _probe_sfs_ids(
    archive: Any,
    sfs_ids: list[str],
    *,
    force_reextract: bool = False,
    effective_dates: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sfs_id in sfs_ids:
        row: dict[str, Any] = {"sfs_id": sfs_id}
        try:
            fetch_se_official_artifacts(
                sfs_id,
                archive,
                force_reextract=force_reextract,
            )
            act = load_se_official_act_from_archive(archive, sfs_id)
            base_sfs_id = str((act or {}).get("amended_act_sfs_id") or "")
            row["base_sfs_id"] = base_sfs_id
            if base_sfs_id:
                fetch_se_rk_current_json(base_sfs_id, archive)
            ops = compile_se_official_ops_to_archive(archive, sfs_id)
            row["op_count"] = len(ops)
            analysis = analyze_se_official_replay_feasibility(
                archive,
                sfs_id,
                as_of=(effective_dates or {}).get(sfs_id) or None,
            )
            row["effective_date"] = analysis["effective_date"]
            row["contamination_count"] = len(analysis.get("contamination") or [])
            contamination = list(analysis.get("contamination") or [])
            reverse_patchable = sum(
                1 for item in contamination if str(item.get("reverse_patch_candidate") or "") == "yes"
            )
            row["reverse_patchable_count"] = reverse_patchable
            row["self_reverse_feasible"] = bool(analysis.get("self_reverse_feasible"))
            row["self_reverse_residual_count"] = len(analysis.get("self_reverse_residual_contamination") or [])
            row["later_chain_reverse_feasible"] = bool(analysis.get("later_chain_reverse_feasible"))
            row["later_chain_residual_count"] = len(analysis.get("later_chain_residual_contamination") or [])
            row["replay_ready"] = bool(analysis.get("replay_ready"))
            row["replay_precondition_issue_count"] = len(analysis.get("replay_precondition_issues") or [])
            row["recovery_strategy"] = analysis.get("recovery_strategy")
            row["later_chain_hints"] = list(analysis.get("later_chain_hints") or [])
            if not bool(analysis.get("replay_feasible")):
                row["status"] = "historical_blocked"
                row["error"] = "historical base unrecoverable from current surface"
            else:
                replay = check_se_official_replay(
                    archive,
                    sfs_id,
                    as_of=(effective_dates or {}).get(sfs_id) or None,
                )
                row["match_count"] = replay["match_count"]
                row["target_count"] = replay["target_count"]
                row["classifications"] = sorted({str(item["classification"]) for item in replay["rows"]})
                row["status"] = "ok"
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def _print_probe_rows(rows: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        status = str(row.get("status") or "")
        sfs_id = str(row.get("sfs_id") or "")
        if status == "ok":
            print(
                f"{sfs_id}  OK  "
                f"{row.get('match_count', 0)}/{row.get('target_count', 0)}  "
                f"ops={row.get('op_count', 0)}  "
                f"base={row.get('base_sfs_id', '')}  "
                f"classes={','.join(row.get('classifications', []))}"
            )
        elif status == "historical_blocked":
            later_chain_hints = list(row.get("later_chain_hints") or [])
            chain_text = ""
            if later_chain_hints:
                chain_text = "  chain=" + ",".join(
                    f"{item.get('sfs_id', '?')}({'have' if item.get('official_act_available') else 'missing'})"
                    for item in later_chain_hints
                )
            print(
                f"{sfs_id}  HIST  "
                f"ops={row.get('op_count', 0)}  "
                f"base={row.get('base_sfs_id', '')}  "
                f"eff={row.get('effective_date', '')}  "
                f"reverse_patch={row.get('reverse_patchable_count', 0)}/{row.get('contamination_count', 0)}  "
                f"self_reverse={'yes' if row.get('self_reverse_feasible') else 'no'}  "
                f"later_reverse={'yes' if row.get('later_chain_reverse_feasible') else 'no'}  "
                f"ready={'yes' if row.get('replay_ready') else 'no'}  "
                f"strategy={row.get('recovery_strategy', '')}"
                f"{chain_text}"
            )
        else:
            print(f"{sfs_id}  ERROR  {row.get('error', '')}")


def _cmd_fetch_official(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        max_age_hours = float("inf") if getattr(args, "max_age_hours", None) is None else float(args.max_age_hours)
        bundle = fetch_se_official_artifacts(
            args.sfs_id,
            archive,
            max_age_hours=max_age_hours,
            force_reextract=bool(getattr(args, "force_reextract", False)),
            pdf_url_override=getattr(args, "pdf_url", None),
        )
        if bundle is None:
            print(f"error: failed to fetch official artifacts for {args.sfs_id}", file=sys.stderr)
            sys.exit(1)

        print(f"SFS ID:            {bundle.sfs_id}")
        print(f"Doc URL:           {bundle.doc_url}")
        print(f"Doc locator:       {bundle.doc_locator}")
        print(f"PDF URL:           {bundle.pdf_url}")
        print(f"PDF locator:       {bundle.pdf_locator}")
        print(f"PDF text locator:  {bundle.pdf_text_url}")
        print(f"Cleaned locator:   {bundle.pdf_cleaned_text_url}")
        print(f"Official act loc:  {se_official_act_locator(bundle.sfs_id)}")

        if getattr(args, "show_text", False):
            text_url = bundle.pdf_cleaned_text_url if not getattr(args, "raw_text", False) else bundle.pdf_text_url
            text = archive.get(text_url)
            print()
            if text is None:
                print("(no extracted text archived)")
            else:
                print(text.decode("utf-8", errors="replace"))


def _cmd_compile_official(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        try:
            ops = compile_se_official_ops_to_archive(archive, args.sfs_id)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        except NotImplementedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"SFS ID:             {args.sfs_id}")
    print(f"Official ops loc:   {se_official_ops_locator(args.sfs_id)}")
    print(f"Compiled op count:  {len(ops)}")

    if getattr(args, "format", "summary") == "json":
        print()
        print(json.dumps(ops, ensure_ascii=False, indent=2))
        return

    for op in ops:
        target = op.get("target", {})
        path = target.get("path", [])
        target_text = "/".join(f"{kind}:{label}" for kind, label in path)
        print(f"{op.get('sequence', 0)}. {op.get('action', '')} {target_text}")


def _cmd_fetch_current(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        max_age_hours = 24.0 if getattr(args, "max_age_hours", None) is None else float(args.max_age_hours)
        diagnostics: list[dict[str, Any]] = []
        current_json = fetch_se_rk_current_json(
            args.sfs_id,
            archive,
            max_age_hours=max_age_hours,
            diagnostics_out=diagnostics,
        )
        if current_json is None:
            print(f"error: failed to fetch RK current JSON for {args.sfs_id}", file=sys.stderr)
            if diagnostics:
                print(json.dumps(diagnostics[0], ensure_ascii=False, sort_keys=True), file=sys.stderr)
            sys.exit(1)

        print(f"SFS ID:             {args.sfs_id}")
        print(f"RK URL:             https://rkrattsbaser.gov.se/sfst?bet={args.sfs_id}")
        print(f"RK current locator: {se_rk_current_json_locator(args.sfs_id)}")

        if getattr(args, "show_json", False):
            print()
            print(current_json.decode("utf-8", errors="replace"))


def _cmd_hydrate_live(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        current_max_age_hours = 24.0 if getattr(args, "current_max_age_hours", None) is None else float(args.current_max_age_hours)
        official_max_age_hours = float("inf") if getattr(args, "official_max_age_hours", None) is None else float(args.official_max_age_hours)
        diagnostics: list[dict[str, Any]] = []
        bundle = hydrate_se_bundle_live(
            args.sfs_id,
            archive,
            pdf_url_override=getattr(args, "pdf_url", None),
            current_max_age_hours=current_max_age_hours,
            official_max_age_hours=official_max_age_hours,
            force_reextract=bool(getattr(args, "force_reextract", False)),
            diagnostics_out=diagnostics,
        )
        if bundle is None:
            print(f"error: failed to hydrate live Sweden bundle for {args.sfs_id}", file=sys.stderr)
            if diagnostics:
                print(json.dumps(diagnostics[0], ensure_ascii=False, sort_keys=True), file=sys.stderr)
            sys.exit(1)

        print(f"SFS ID:             {bundle.source_record.sfs_id}")
        print(f"RK current locator: {se_rk_current_json_locator(bundle.source_record.sfs_id)}")
        print(f"Source record loc:  {se_source_record_locator(bundle.source_record.sfs_id)}")
        print(f"Current IR locator: {se_current_ir_locator(bundle.source_record.sfs_id)}")
        print(f"Bundle locator:     {se_bundle_manifest_locator(bundle.source_record.sfs_id)}")
        if bundle.official_artifacts is not None:
            print(f"Official doc loc:   {bundle.official_artifacts.doc_locator}")
            print(f"Official PDF loc:   {bundle.official_artifacts.pdf_locator}")
            print(f"Cleaned text loc:   {bundle.official_artifacts.pdf_cleaned_text_url}")
            print(f"Official act loc:   {se_official_act_locator(bundle.source_record.sfs_id)}")

        if getattr(args, "show_text", False) and bundle.official_artifacts is not None:
            print()
            text_url = (
                bundle.official_artifacts.pdf_text_url
                if getattr(args, "raw_text", False)
                else bundle.official_artifacts.pdf_cleaned_text_url
            )
            text = archive.get(text_url)
            if text is None:
                print("(no archived text)")
            else:
                print(text.decode("utf-8", errors="replace"))


def _cmd_show_official(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        act = load_se_official_act_from_archive(archive, args.sfs_id)
    if act is None:
        print(f"error: no archived official act surface for {args.sfs_id}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "format", "summary") == "json":
        _print_json(act)
        return

    print(f"SFS ID:             {act.get('sfs_id', '')}")
    print(f"Title:              {act.get('title', '')}")
    print(f"Act type:           {act.get('act_type', '')}")
    print(f"Amending act:       {'yes' if act.get('is_amending_act') else 'no'}")
    print(f"Amended act:        {act.get('amended_act_sfs_id', '')}")
    print(f"Published date:     {act.get('published_date', '')}")
    print(f"Issued date:        {act.get('issued_date', '')}")
    print(f"Affected sections:  {', '.join(act.get('affected_section_labels', []))}")
    print(f"Provision count:    {len(act.get('provisions', []))}")
    print(f"Heading count:      {len(act.get('inserted_headings', []))}")
    print(f"Appendix count:     {len(act.get('appendices', []))}")
    print(f"Footnote count:     {len(act.get('footnotes', []))}")

    if getattr(args, "show_text", False):
        print()
        enacting_clause = act.get("enacting_clause", "")
        effective_clause = act.get("effective_clause", "")
        if enacting_clause:
            print(enacting_clause)
            print()
        for provision in act.get("provisions", []):
            label = provision.get("label", "")
            text = provision.get("text", "")
            print(f"{label} § {text}".rstrip())
            print()
        for heading in act.get("inserted_headings", []):
            print(f"[heading before {heading.get('before_label', '')}] {heading.get('text', '')}".rstrip())
            print()
        for appendix in act.get("appendices", []):
            label = appendix.get("label", "")
            title = appendix.get("title", "")
            text = appendix.get("text", "")
            prefix = f"Bilaga {label}".strip()
            if title:
                prefix = f"{prefix} {title}".strip()
            print(prefix)
            if text:
                print(text)
            print()
        if effective_clause:
            print(effective_clause)


def _cmd_show_official_ops(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        ops = load_se_official_ops_from_archive(archive, args.sfs_id)
    if ops is None:
        print(f"error: no archived official ops for {args.sfs_id}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "format", "summary") == "json":
        print(json.dumps(ops, ensure_ascii=False, indent=2))
        return
    print(f"SFS ID:             {args.sfs_id}")
    print(f"Official ops loc:   {se_official_ops_locator(args.sfs_id)}")
    print(f"Compiled op count:  {len(ops)}")
    for op in ops:
        target = op.get("target", {})
        path = target.get("path", [])
        target_text = "/".join(f"{kind}:{label}" for kind, label in path)
        if target.get("special"):
            target_text = f"{target_text}/{target.get('special')}"
        print(f"{op.get('sequence', 0)}. {op.get('action', '')} {target_text}")


def _cmd_materialize_current(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        current_json = archive.get(se_rk_current_json_locator(args.sfs_id))
    if current_json is None:
        print(f"error: no archived RK current JSON for {args.sfs_id}", file=sys.stderr)
        sys.exit(1)
    statute = parse_se_statute(current_json, statute_id=args.sfs_id)
    materialized = materialize_se_statute_as_of(statute, args.as_of)
    if getattr(args, "format", "summary") == "json":
        _print_json(materialized.to_jsonable_dict())
        return
    print(f"Statute: {materialized.statute_id}")
    print(f"As of:   {args.as_of}")
    print(f"Title:   {materialized.title}")
    print()
    for line in _irnode_summary(materialized.body):
        print(line)


def _cmd_replay_check(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        try:
            result = check_se_official_replay(
                archive,
                args.sfs_id,
                base_sfs_id=getattr(args, "base_sfs_id", None),
                as_of=getattr(args, "as_of", None),
            )
        except (FileNotFoundError, ValueError, NotImplementedError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    if getattr(args, "format", "summary") == "json":
        _print_json(result)
        return
    print(f"Amending SFS ID:    {result['amending_sfs_id']}")
    print(f"Base SFS ID:        {result['base_sfs_id']}")
    print(f"Effective date:     {result['effective_date']}")
    print(f"Pre date:           {result['pre_date']}")
    print(f"Matched sections:   {result['match_count']}/{result['target_count']}")
    for row in result["rows"]:
        label = row.get("section") or row.get("appendix") or "?"
        kind = row.get("target_kind", "section")
        suffix = " §" if kind in {"section", "heading"} else ""
        print(f"{kind} {label}{suffix} {'MATCH' if row['match'] else 'MISMATCH'} [{row['classification']}]")


def _cmd_diagnose_replay(args: "argparse.Namespace") -> None:
    older_base: dict[str, Any] | None = None
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        try:
            result = analyze_se_official_replay_feasibility(
                archive,
                args.sfs_id,
                base_sfs_id=getattr(args, "base_sfs_id", None),
                as_of=getattr(args, "as_of", None),
            )
            if result.get("recovery_strategy") == "older_base_required":
                older_base = plan_se_older_base_rebuild(
                    archive,
                    args.sfs_id,
                    base_sfs_id=getattr(args, "base_sfs_id", None),
                    as_of=getattr(args, "as_of", None),
                    fetch_missing=bool(getattr(args, "fetch_missing", False)),
                    probe_sources=bool(getattr(args, "probe_sources", False)),
                )
        except (FileNotFoundError, ValueError, NotImplementedError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    if getattr(args, "format", "summary") == "json":
        if older_base is not None:
            result = dict(result)
            result["older_base"] = older_base
        _print_json(result)
        return
    print(f"Amending SFS ID:    {result['amending_sfs_id']}")
    print(f"Base SFS ID:        {result['base_sfs_id']}")
    print(f"Effective date:     {result['effective_date']}")
    print(f"Pre date:           {result['pre_date']}")
    print(f"Replay feasible:    {'yes' if result['replay_feasible'] else 'no'}")
    print(f"Self-reverse:       {'yes' if result.get('self_reverse_feasible') else 'no'}")
    print(f"Later reverse:      {'yes' if result.get('later_chain_reverse_feasible') else 'no'}")
    print(f"Replay ready:       {'yes' if result.get('replay_ready') else 'no'}")
    print(f"Strategy:           {result.get('recovery_strategy', '')}")
    print(f"Compiled op count:  {result['op_count']}")
    contamination = list(result.get("contamination") or [])
    if not contamination:
        print("Contamination:      none")
        return
    print("Contamination:")
    for item in contamination:
        line = (
            f"{item.get('target_kind', '?')} {item.get('label', '?')} "
            f"[{item.get('issue', 'unknown')}] via {item.get('action', 'unknown')}"
        )
        source_sfs_id = str(item.get("source_sfs_id") or "")
        origin_hint = str(item.get("origin_hint") or "")
        reverse_patch_candidate = str(item.get("reverse_patch_candidate") or "")
        if source_sfs_id or origin_hint != "unknown" or reverse_patch_candidate != "unknown":
            line += (
                f" origin={origin_hint or 'unknown'}"
                f" source={source_sfs_id or '?'}"
                f" reverse_patch={reverse_patch_candidate or 'unknown'}"
            )
        print(line)
    residual = list(result.get("self_reverse_residual_contamination") or [])
    print(f"Residual after self-reverse: {len(residual)}")
    later_residual = list(result.get("later_chain_residual_contamination") or [])
    print(f"Residual after later reverse: {len(later_residual)}")
    preconditions = list(result.get("replay_precondition_issues") or [])
    print(f"Replay preconditions: {len(preconditions)}")
    for item in preconditions:
        print(
            f"{item.get('target_kind', '?')} {item.get('label', '?')} "
            f"[{item.get('issue', 'unknown')}] via {item.get('action', 'unknown')}"
        )
    ancestry_hints = list(result.get("replay_precondition_ancestry_hints") or [])
    if ancestry_hints:
        print("Precondition ancestry:")
        for item in ancestry_hints:
            chain_ids = ",".join(str(value) for value in item.get("candidate_chain_sfs_ids", []))
            actions = ",".join(str(value) for value in item.get("direct_later_actions", []))
            suffix = ""
            if actions:
                suffix += f" actions={actions}"
            if item.get("noninvertible_blocker"):
                suffix += " noninvertible=yes"
            print(
                f"{item.get('label', '?')} <- {item.get('derived_from_label', '?') or '?'} "
                f"via {item.get('via_later_source', '?')}"
                + (f" candidates={chain_ids}" if chain_ids else "")
                + suffix
            )
    later_chain_hints = list(result.get("later_chain_hints") or [])
    if later_chain_hints:
        print("Later chain:")
        for item in later_chain_hints:
            print(
                f"{item.get('sfs_id', '?')} "
                f"official_act={'yes' if item.get('official_act_available') else 'no'} "
                f"pdf={'yes' if item.get('pdf_available') else 'no'} "
                f"doc={'yes' if item.get('doc_available') else 'no'}"
            )
    if older_base is not None:
        print(
            "Older-base chain:  "
            f"chain={'yes' if older_base.get('official_chain_ready') else 'no'}  "
            f"rebuild={'yes' if older_base.get('rebuild_ready') else 'no'}  "
            f"prior={older_base.get('prior_amendment_count', 0)}  "
            f"compiled={older_base.get('compiled_count', 0)}  "
            f"missing={older_base.get('missing_official_count', 0)}  "
            f"unsupported={older_base.get('unsupported_count', 0)}"
        )
        blocker_rows = _se_older_base_blocker_rows(older_base)
        if blocker_rows:
            print("Older-base blockers:")
            for item in blocker_rows:
                print(_format_se_older_base_chain_row(item))
        base_seed = older_base.get("base_seed") or {}
        print(
            "Base seed source:  "
            f"official_act={'yes' if base_seed.get('official_act_available') else 'no'}  "
            f"seed_ir={'yes' if base_seed.get('official_base_ir_available') else 'no'}  "
            f"pdf={'yes' if base_seed.get('pdf_available') else 'no'}  "
            f"doc={'yes' if base_seed.get('doc_available') else 'no'}"
        )
        base_probe = base_seed.get("public_source_probe")
        if isinstance(base_probe, dict):
            print(
                "Base public probe: "
                f"doc={base_probe.get('doc_status', '')} "
                f"pdf={base_probe.get('pdf_status', '')}"
            )


def _se_older_base_blocker_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = plan.get("chain") or []
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("ops_status") or "") != "compiled"
    ]


def _format_se_older_base_chain_row(item: dict[str, Any]) -> str:
    suffix = ""
    if item.get("error"):
        suffix = f"  error={item.get('error')}"
    probe = item.get("public_source_probe")
    if isinstance(probe, dict):
        suffix += (
            f"  source_doc={probe.get('doc_status', '')}"
            f" source_pdf={probe.get('pdf_status', '')}"
        )
    return (
        f"{item.get('effective_date', '')}  {item.get('sfs_id', '')}  "
        f"{item.get('ops_status', '')}  ops={item.get('op_count', 0)}"
        f"{suffix}"
    )


def _cmd_plan_older_base(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        try:
            result = plan_se_older_base_rebuild(
                archive,
                args.sfs_id,
                base_sfs_id=getattr(args, "base_sfs_id", None),
                as_of=getattr(args, "as_of", None),
                fetch_missing=bool(getattr(args, "fetch_missing", False)),
                probe_sources=bool(getattr(args, "probe_sources", False)),
            )
        except (FileNotFoundError, ValueError, NotImplementedError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    if getattr(args, "format", "summary") == "json":
        _print_json(result)
        return
    print(f"Amending SFS ID:    {result['amending_sfs_id']}")
    print(f"Base SFS ID:        {result['base_sfs_id']}")
    print(f"Effective date:     {result['effective_date']}")
    print(f"Pre date:           {result['pre_date']}")
    print(f"Strategy:           {result.get('recovery_strategy', '')}")
    print(f"Official chain:     {'ready' if result.get('official_chain_ready') else 'blocked'}")
    print(f"Rebuild ready:      {'yes' if result.get('rebuild_ready') else 'no'}")
    print(f"Prior amendments:   {result.get('prior_amendment_count', 0)}")
    print(
        f"Chain counts:       compiled={result.get('compiled_count', 0)} "
        f"missing={result.get('missing_official_count', 0)} "
        f"unsupported={result.get('unsupported_count', 0)} "
        f"invalid={result.get('invalid_count', 0)}"
    )
    base_seed = result.get("base_seed") or {}
    print(
        "Base seed source:   "
        f"official_act={'yes' if base_seed.get('official_act_available') else 'no'} "
        f"seed_ir={'yes' if base_seed.get('official_base_ir_available') else 'no'} "
        f"pdf={'yes' if base_seed.get('pdf_available') else 'no'} "
        f"doc={'yes' if base_seed.get('doc_available') else 'no'}"
    )
    base_probe = result.get("base_seed", {}).get("public_source_probe") if isinstance(result.get("base_seed"), dict) else None
    if isinstance(base_probe, dict):
        print(
            "Base public probe:  "
            f"doc={base_probe.get('doc_status', '')} "
            f"pdf={base_probe.get('pdf_status', '')}"
        )
    for item in result.get("chain", []):
        print(_format_se_older_base_chain_row(item))


def _cmd_probe(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        rows = _probe_sfs_ids(
            archive,
            list(args.sfs_ids),
            force_reextract=bool(getattr(args, "force_reextract", False)),
        )
    _print_probe_rows(rows, as_json=(getattr(args, "format", "summary") == "json"))


def _cmd_probe_base(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        current_json = fetch_se_rk_current_json(args.base_sfs_id, archive)
        if current_json is None:
            print(f"error: failed to fetch RK current JSON for {args.base_sfs_id}", file=sys.stderr)
            sys.exit(1)
        source_record = parse_se_source_record(current_json)
        amendment_ids = [
            entry.amending_sfs_id
            for entry in source_record.amendment_register
            if entry.amending_sfs_id
        ]
        effective_dates = {
            entry.amending_sfs_id: entry.effective_date
            for entry in source_record.amendment_register
            if entry.amending_sfs_id and entry.effective_date
        }
        limit = int(getattr(args, "limit", 0) or 0)
        if limit > 0:
            amendment_ids = amendment_ids[:limit]
        rows = _probe_sfs_ids(
            archive,
            amendment_ids,
            force_reextract=bool(getattr(args, "force_reextract", False)),
            effective_dates=effective_dates,
        )
    if getattr(args, "format", "summary") == "json":
        print(
            json.dumps(
                {
                    "base_sfs_id": args.base_sfs_id,
                    "amendment_count": len(amendment_ids),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"Base SFS ID:        {args.base_sfs_id}")
    print(f"Amendment count:    {len(amendment_ids)}")
    _print_probe_rows(rows, as_json=False)


def _cmd_source_record(args: "argparse.Namespace") -> None:
    payload = _read_bytes(args.json_path)
    doc_html = _read_bytes(args.doc_html) if getattr(args, "doc_html", None) else None
    bundle = build_se_source_bundle(payload, doc_html=doc_html)
    record = bundle.source_record
    data = {
        "sfs_id": record.sfs_id,
        "title": record.title,
        "act_type": record.act_type,
        "department": record.department,
        "issued_date": record.issued_date,
        "published_date": record.published_date,
        "effective_markers": list(record.effective_markers),
        "amended_through_sfs": record.amended_through_sfs,
        "source_confidence": record.source_confidence.value,
        "source_urls": {
            "official_sfs_doc_url": record.source_urls.official_sfs_doc_url,
            "official_sfs_pdf_url": record.source_urls.official_sfs_pdf_url,
            "rk_sfst_url": record.source_urls.rk_sfst_url,
            "rk_beta_base_url": record.source_urls.rk_beta_base_url,
            "rk_beta_current_url": record.source_urls.rk_beta_current_url,
            "rk_sfsr_url": record.source_urls.rk_sfsr_url,
        },
        "amendment_count": len(record.amendment_register),
        "parliamentary_links": [
            {
                "prop_id": link.prop_id,
                "bet_id": link.bet_id,
                "rskr_id": link.rskr_id,
            }
            for link in record.parliamentary_links
        ],
    }
    _print_json(data)


def _cmd_parse_current(args: "argparse.Namespace") -> None:
    statute = parse_se_statute(_read_bytes(args.json_path))
    if getattr(args, "format", "summary") == "json":
        _print_json(statute.to_jsonable_dict())
        return

    print(f"Statute: {statute.statute_id}")
    print(f"Title:   {statute.title}")
    print(f"Meta:    {json.dumps(statute.metadata, ensure_ascii=False)}")
    print()
    for line in _irnode_summary(statute.body):
        print(line)
    for schedule in statute.supplements:
        print()
        for line in _irnode_summary(schedule):
            print(line)


def _cmd_ingest_json(args: "argparse.Namespace") -> None:
    payload = _read_bytes(args.json_path)
    doc_html = _read_bytes(args.doc_html) if getattr(args, "doc_html", None) else None
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        bundle = archive_se_source_bundle(payload, archive, doc_html=doc_html)
    sfs_id = bundle.source_record.sfs_id
    print(f"SFS ID:             {sfs_id}")
    print(f"RK current locator: {se_rk_current_json_locator(sfs_id)}")
    print(f"Source record loc:  {se_source_record_locator(sfs_id)}")
    print(f"Current IR locator: {se_current_ir_locator(sfs_id)}")
    print(f"Bundle locator:     {se_bundle_manifest_locator(sfs_id)}")


def _cmd_ingest_scrape_json(args: "argparse.Namespace") -> None:
    payload = _read_bytes(args.json_path)
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        result = ingest_se_scraped_doc_html_map(payload, archive)
    print(f"Entries:            {result['entry_count']}")
    print(f"Imported:           {result['imported_count']}")
    print(f"Skipped:            {result['skipped_count']}")
    print(f"PDF links parsed:   {result['resolved_pdf_link_count']}")


def _cmd_show_archive(args: "argparse.Namespace") -> None:
    with open_se_archive(Path(args.db) if getattr(args, "db", None) else None) as archive:
        sfs_id = args.sfs_id
        bundle = load_se_bundle_from_archive(archive, sfs_id)
        source_record = load_se_source_record_from_archive(archive, sfs_id)
        current_ir = load_se_current_ir_from_archive(archive, sfs_id)
        official_act = load_se_official_act_from_archive(archive, sfs_id)
        official_base_ir = load_se_official_base_ir_from_archive(archive, sfs_id)
        official_ops = load_se_official_ops_from_archive(archive, sfs_id)
        current_json = archive.get(se_rk_current_json_locator(sfs_id))
        doc_html = archive.get(se_official_doc_locator(sfs_id))
        raw_text = archive.get(se_pdf_text_locator(sfs_id))
        cleaned_text = archive.get(se_pdf_cleanup_locator(sfs_id))
        pdf_valid = has_valid_se_official_pdf(archive, sfs_id)

    print(f"SFS ID:             {sfs_id}")
    print(f"Bundle locator:     {se_bundle_manifest_locator(sfs_id)}")
    print(f"RK current JSON:    {'yes' if current_json is not None else 'no'}")
    print(f"Bundle present:     {'yes' if bundle is not None else 'no'}")
    print(f"Source record:      {'yes' if source_record is not None else 'no'}")
    print(f"Current IR:         {'yes' if current_ir is not None else 'no'}")
    print(f"Official doc HTML:  {'yes' if doc_html is not None else 'no'}")
    print(f"Official PDF:       {'yes' if pdf_valid else 'no'}")
    print(f"Official act JSON:  {'yes' if official_act is not None else 'no'}")
    print(f"Official base IR:   {'yes' if official_base_ir is not None else 'no'}")
    print(f"Official ops JSON:  {'yes' if official_ops is not None else 'no'}")
    print(f"Raw PDF text:       {'yes' if raw_text is not None else 'no'}")
    print(f"Cleaned PDF text:   {'yes' if cleaned_text is not None else 'no'}")

    if bundle is not None and getattr(args, "format", "summary") == "json":
        _print_json(bundle)
        return

    if bundle is not None:
        record = bundle.get("source_record") if isinstance(bundle, dict) else None
        if isinstance(record, dict):
            print(f"Title:              {record.get('title', '')}")
            print(f"Act type:           {record.get('act_type', '')}")
            print(f"Source confidence:  {record.get('source_confidence', '')}")
            print(f"Amendment count:    {len(record.get('amendment_register', []))}")

    if getattr(args, "show_text", False):
        print()
        text = cleaned_text if not getattr(args, "raw_text", False) else raw_text
        if text is None:
            print("(no archived text)")
        else:
            print(text.decode("utf-8", errors="replace"))


def main(args: "argparse.Namespace") -> None:
    command = getattr(args, "sweden_command", None)
    if command == "compile-official":
        _cmd_compile_official(args)
        return
    if command == "fetch-current":
        _cmd_fetch_current(args)
        return
    if command == "fetch-official":
        _cmd_fetch_official(args)
        return
    if command == "hydrate-bulk":
        _cmd_hydrate_bulk(args)
        return
    if command == "backfill-official":
        _cmd_backfill_official(args)
        return
    if command == "hydrate-live":
        _cmd_hydrate_live(args)
        return
    if command == "materialize-current":
        _cmd_materialize_current(args)
        return
    if command == "replay-check":
        _cmd_replay_check(args)
        return
    if command == "diagnose-replay":
        _cmd_diagnose_replay(args)
        return
    if command == "plan-older-base":
        _cmd_plan_older_base(args)
        return
    if command == "probe":
        _cmd_probe(args)
        return
    if command == "probe-base":
        _cmd_probe_base(args)
        return
    if command == "show-official":
        _cmd_show_official(args)
        return
    if command == "show-official-ops":
        _cmd_show_official_ops(args)
        return
    if command == "source-record":
        _cmd_source_record(args)
        return
    if command == "parse-current":
        _cmd_parse_current(args)
        return
    if command == "ingest-json":
        _cmd_ingest_json(args)
        return
    if command == "ingest-scrape-json":
        _cmd_ingest_scrape_json(args)
        return
    if command == "show-archive":
        _cmd_show_archive(args)
        return

    print("error: missing sweden subcommand", file=sys.stderr)
    sys.exit(1)


def register_cli(sub: Any) -> None:
    """Register the 'sweden' subcommand onto an argparse subparsers object."""
    sweden_p = sub.add_parser(
        "sweden",
        help="Sweden frontend helpers (source records, current-text IR, official PDFs)",
        description=(
            "Helpers for the Sweden frontend: archive official SFS artifacts, "
            "fetch live RK current JSON, inspect SourceRecord metadata from "
            "local RK-style JSON, and parse current-text IR."
        ),
    )
    sweden_sub = sweden_p.add_subparsers(dest="sweden_command", metavar="<subcommand>")

    sw_compile_p = sweden_sub.add_parser(
        "compile-official",
        help="compile first-pass replace ops from archived official act JSON",
    )
    sw_compile_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_compile_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_compile_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_current_p = sweden_sub.add_parser(
        "fetch-current",
        help="fetch RK current JSON and archive it",
    )
    sw_current_p.add_argument("sfs_id", help="SFS ID, e.g. 2025:399")
    sw_current_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_current_p.add_argument(
        "--max-age-hours",
        dest="max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_current_p.add_argument(
        "--show-json",
        action="store_true",
        help="print archived current JSON after fetch",
    )

    sw_fetch_p = sweden_sub.add_parser(
        "fetch-official",
        help="fetch official SFS doc page + PDF, archive raw and extracted text",
    )
    sw_fetch_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_fetch_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_fetch_p.add_argument(
        "--max-age-hours",
        dest="max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override cache max age; default is immutable/no refetch for official sources",
    )
    sw_fetch_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_fetch_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text after fetch",
    )
    sw_fetch_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )

    sw_fetch_p.add_argument(
        "--pdf-url",
        metavar="URL",
        help="explicit direct official PDF URL; used when the doc page is blocked or unavailable",
    )

    sw_hydrate_bulk_p = sweden_sub.add_parser(
        "hydrate-bulk",
        help="bulk hydrate Sweden official/current artifacts into sweden.farchive",
    )
    sw_hydrate_bulk_p.add_argument(
        "sfs_ids",
        nargs="*",
        help="optional explicit SFS IDs; default is all archived official.doc.html locators",
    )
    sw_hydrate_bulk_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_hydrate_bulk_p.add_argument(
        "--scrape-json",
        metavar="PATH",
        help="optional browser-scraped doc-page JSON to ingest before hydrating",
    )
    sw_hydrate_bulk_p.add_argument(
        "--hydrate-current",
        action="store_true",
        help="also fetch RK current JSON and archive source/current bundle artifacts",
    )
    sw_hydrate_bulk_p.add_argument(
        "--compile-ops",
        action="store_true",
        help="compile archived official act JSON into official.ops.json when the act is amending",
    )
    sw_hydrate_bulk_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_hydrate_bulk_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_hydrate_bulk_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_hydrate_bulk_p.add_argument(
        "--no-skip-complete",
        action="store_true",
        help="do not skip SFS IDs that already have the requested archived artifacts",
    )
    sw_hydrate_bulk_p.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="skip the first N input IDs after archive/scrape expansion",
    )
    sw_hydrate_bulk_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="process at most N IDs after offset (default: all)",
    )
    sw_hydrate_bulk_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_backfill_p = sweden_sub.add_parser(
        "backfill-official",
        help="exhaustively probe Sweden SFS IDs and hydrate official artifacts into sweden.farchive",
    )
    sw_backfill_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_backfill_p.add_argument(
        "--year-start",
        type=int,
        default=1999,
        metavar="YEAR",
        help="first SFS year to probe (default: 1999)",
    )
    sw_backfill_p.add_argument(
        "--year-end",
        type=int,
        default=2026,
        metavar="YEAR",
        help="last SFS year to probe (default: 2026)",
    )
    sw_backfill_p.add_argument(
        "--max-number",
        type=int,
        default=2100,
        metavar="N",
        help="maximum SFS number to probe per year (default: 2100)",
    )
    sw_backfill_p.add_argument(
        "--hydrate-current",
        action="store_true",
        help="also fetch RK current JSON and archive source/current bundle artifacts",
    )
    sw_backfill_p.add_argument(
        "--compile-ops",
        action="store_true",
        help="compile archived official act JSON into official.ops.json when the act is amending",
    )
    sw_backfill_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_backfill_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_backfill_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_backfill_p.add_argument(
        "--no-skip-complete",
        action="store_true",
        help="do not skip SFS IDs that already have the requested archived artifacts",
    )
    sw_backfill_p.add_argument(
        "--resume",
        action="store_true",
        help="resume from the archive checkpoint artifact when the run signature matches",
    )
    sw_backfill_p.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="skip the first N candidate IDs after generation",
    )
    sw_backfill_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="process at most N candidate IDs after offset (default: all)",
    )
    sw_backfill_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_hydrate_p = sweden_sub.add_parser(
        "hydrate-live",
        help="fetch RK current JSON and official PDF artifacts, then archive the Sweden bundle",
    )
    sw_hydrate_p.add_argument("sfs_id", help="SFS ID, e.g. 2025:399")
    sw_hydrate_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_hydrate_p.add_argument(
        "--current-max-age-hours",
        dest="current_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="cache max age for RK current JSON (default: 24 hours)",
    )
    sw_hydrate_p.add_argument(
        "--official-max-age-hours",
        dest="official_max_age_hours",
        type=float,
        default=None,
        metavar="HOURS",
        help="override immutable caching for official sources",
    )
    sw_hydrate_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext even if extracted text already exists",
    )
    sw_hydrate_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text after hydration",
    )
    sw_hydrate_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )
    sw_hydrate_p.add_argument(
        "--pdf-url",
        metavar="URL",
        help="explicit direct official PDF URL; used when the doc page is blocked or unavailable",
    )

    sw_materialize_p = sweden_sub.add_parser(
        "materialize-current",
        help="materialize archived RK current JSON at one date",
    )
    sw_materialize_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:106")
    sw_materialize_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_materialize_p.add_argument("--as-of", required=True, metavar="DATE", help="materialization date YYYY-MM-DD")
    sw_materialize_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_replay_check_p = sweden_sub.add_parser(
        "replay-check",
        help="replay compiled official ops against a temporal Sweden base and compare to current",
    )
    sw_replay_check_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2026:286")
    sw_replay_check_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_replay_check_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="base SFS ID; defaults to the amended act recorded in official.act.json",
    )
    sw_replay_check_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to the compiled op source effective date",
    )
    sw_replay_check_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_diagnose_replay_p = sweden_sub.add_parser(
        "diagnose-replay",
        help="analyze whether one Sweden act can be replayed from the archived current base surface",
    )
    sw_diagnose_replay_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2018:1381")
    sw_diagnose_replay_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_diagnose_replay_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="override the base SFS ID if it cannot be inferred from the official act",
    )
    sw_diagnose_replay_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to the compiled op source effective date",
    )
    sw_diagnose_replay_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_diagnose_replay_p.add_argument(
        "--fetch-missing",
        dest="fetch_missing",
        action="store_true",
        help="when printing older-base diagnostics, try to fetch missing official-chain artifacts",
    )
    sw_diagnose_replay_p.add_argument(
        "--probe-sources",
        dest="probe_sources",
        action="store_true",
        help="probe public official-source reachability for older-base blockers",
    )

    sw_plan_older_base_p = sweden_sub.add_parser(
        "plan-older-base",
        help="plan older-base reconstruction from the base act's official chain inputs",
    )
    sw_plan_older_base_p.add_argument("sfs_id", help="amending SFS ID, e.g. 2018:1381")
    sw_plan_older_base_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_plan_older_base_p.add_argument(
        "--base-sfs-id",
        metavar="SFS_ID",
        help="override the base SFS ID if it cannot be inferred from the official act",
    )
    sw_plan_older_base_p.add_argument(
        "--as-of",
        metavar="DATE",
        help="effective date YYYY-MM-DD; defaults to official ops source or the base amendment register",
    )
    sw_plan_older_base_p.add_argument(
        "--fetch-missing",
        dest="fetch_missing",
        action="store_true",
        help="try to fetch missing official-chain artifacts before reporting statuses",
    )
    sw_plan_older_base_p.add_argument(
        "--probe-sources",
        dest="probe_sources",
        action="store_true",
        help="probe public official-source reachability for missing base/chain acts",
    )
    sw_plan_older_base_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_probe_p = sweden_sub.add_parser(
        "probe",
        help="refresh/fetch and replay-check a batch of Sweden acts",
    )
    sw_probe_p.add_argument("sfs_ids", nargs="+", help="amending SFS IDs, e.g. 2026:280 2026:286 2026:290")
    sw_probe_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_probe_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext / official-act parse before probing",
    )
    sw_probe_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_probe_base_p = sweden_sub.add_parser(
        "probe-base",
        help="fetch one base statute, read its amendment register, and probe listed amending acts",
    )
    sw_probe_base_p.add_argument("base_sfs_id", help="base SFS ID, e.g. 2015:284")
    sw_probe_base_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_probe_base_p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="probe only the first N register entries (default: all)",
    )
    sw_probe_base_p.add_argument(
        "--force-reextract",
        dest="force_reextract",
        action="store_true",
        help="rerun pdftotext / official-act parse before probing",
    )
    sw_probe_base_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_show_official_p = sweden_sub.add_parser(
        "show-official",
        help="inspect the parsed official SFS act surface",
    )
    sw_show_official_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_official_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_official_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_show_official_p.add_argument(
        "--show-text",
        action="store_true",
        help="print enacting clause, provisions, and effective clause",
    )

    sw_show_official_ops_p = sweden_sub.add_parser(
        "show-official-ops",
        help="inspect compiled first-pass ops from archived official act JSON",
    )
    sw_show_official_ops_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_official_ops_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_official_ops_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_source_p = sweden_sub.add_parser(
        "source-record",
        help="build a Sweden SourceRecord from local RK-style JSON",
    )
    sw_source_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_source_p.add_argument(
        "--doc-html",
        metavar="PATH",
        help="optional local official SFS doc page HTML to enrich PDF URL",
    )

    sw_parse_p = sweden_sub.add_parser(
        "parse-current",
        help="parse current-text IR from local RK-style JSON",
    )
    sw_parse_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_parse_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )

    sw_ingest_p = sweden_sub.add_parser(
        "ingest-json",
        help="archive local RK-style JSON and derived Sweden bundle artifacts",
    )
    sw_ingest_p.add_argument("--json-path", required=True, metavar="PATH", help="local JSON file")
    sw_ingest_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_ingest_p.add_argument(
        "--doc-html",
        metavar="PATH",
        help="optional local official SFS doc page HTML to archive alongside the bundle",
    )

    sw_ingest_scrape_p = sweden_sub.add_parser(
        "ingest-scrape-json",
        help="archive browser-scraped Sweden doc-page HTML map",
    )
    sw_ingest_scrape_p.add_argument("--json-path", required=True, metavar="PATH", help="local scrape JSON file")
    sw_ingest_scrape_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")

    sw_show_p = sweden_sub.add_parser(
        "show-archive",
        help="inspect archived Sweden bundle and PDF-text artifacts",
    )
    sw_show_p.add_argument("sfs_id", help="SFS ID, e.g. 2026:286")
    sw_show_p.add_argument("--db", metavar="PATH", help="Farchive DB path (default: data/sweden.farchive)")
    sw_show_p.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="output format (default: summary)",
    )
    sw_show_p.add_argument(
        "--show-text",
        action="store_true",
        help="print archived extracted text if available",
    )
    sw_show_p.add_argument(
        "--raw-text",
        action="store_true",
        help="with --show-text, print raw pdftotext output instead of cleaned text",
    )
