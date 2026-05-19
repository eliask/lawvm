"""Resumable NZ corpus frontier acquisition.

The closure command orchestrates acquisition primitives without inventing a
legal effect graph. It fetches useful source surfaces in this order:

1. target work version graph and XML;
2. latest target XML dependency report;
3. latest XML for amendment-work candidates named by the target XML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import (
    NZSyncOptions,
    NZSyncStats,
    nz_api_key_from_env,
    open_farchive,
    sync_nz_corpus,
)
from lawvm.new_zealand.dependencies import (
    NZDependencyReport,
    extract_dependency_report,
    latest_xml_locator_for_work,
)


@dataclass
class NZClosureState:
    seed_work_ids: list[str] = field(default_factory=list)
    discovered_work_ids: list[str] = field(default_factory=list)
    unresolved_work_ids: list[dict[str, Any]] = field(default_factory=list)
    dependency_reports: list[dict[str, Any]] = field(default_factory=list)
    sync_summaries: list[dict[str, Any]] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "seed_work_ids": self.seed_work_ids,
            "discovered_work_ids": self.discovered_work_ids,
            "unresolved_work_ids": self.unresolved_work_ids,
            "dependency_reports": self.dependency_reports,
            "sync_summaries": self.sync_summaries,
        }


def run_closure(args: Any) -> NZClosureState:
    api_key = nz_api_key_from_env()
    if not api_key:
        raise SystemExit("ERROR: NZ_API_KEY is not set")

    db_path = Path(args.db)
    state = NZClosureState(seed_work_ids=list(args.work_id or ()))

    if args.all_acts:
        stats = _run_sync(
            api_key,
            db_path,
            work_ids=(),
            search_term=args.search_term,
            legislation_type=args.legislation_type or "act",
            publisher=args.publisher or "",
            max_versions_per_work=args.max_versions_per_work,
            args=args,
        )
        state.sync_summaries.append({"phase": "all_acts_latest", **stats.as_summary()})
        _write_state(Path(args.state_json), state)
        return state

    if not args.work_id:
        raise SystemExit("ERROR: pass --work-id or --all-acts")

    seen: set[str] = set()
    frontier = list(dict.fromkeys(args.work_id))
    discovered: list[str] = []

    for depth in range(args.dependency_depth + 1):
        if not frontier:
            break
        max_versions_per_work = 1
        phase = f"dependency_depth_{depth}_latest"
        if depth == 0 and not args.seed_latest_only:
            max_versions_per_work = None
            phase = "seed_full_versions"

        stats = _run_sync(
            api_key,
            db_path,
            work_ids=tuple(frontier),
            search_term="",
            legislation_type="",
            publisher="",
            max_versions_per_work=max_versions_per_work,
            args=args,
        )
        state.sync_summaries.append({"phase": phase, **stats.as_summary()})
        if stats.stopped_reason == "request_budget_exhausted":
            break

        next_frontier: list[str] = []
        for work_id in frontier:
            seen.add(work_id)
            report = _dependency_report_for_work(db_path, work_id, state)
            if report is None:
                continue
            state.dependency_reports.append(report.to_jsonable())
            for ref in report.amending_works:
                if ref.work_id not in seen and ref.work_id not in next_frontier:
                    next_frontier.append(ref.work_id)
                    discovered.append(ref.work_id)
        frontier = next_frontier
        state.discovered_work_ids = list(dict.fromkeys(discovered))
        _write_state(Path(args.state_json), state)

    _write_state(Path(args.state_json), state)
    return state


def _run_sync(
    api_key: str,
    db_path: Path,
    *,
    work_ids: tuple[str, ...],
    search_term: str,
    legislation_type: str,
    publisher: str,
    max_versions_per_work: int | None,
    args: Any,
) -> NZSyncStats:
    archive = open_farchive(db_path)
    try:
        return sync_nz_corpus(
            archive,
            api_key=api_key,
            options=NZSyncOptions(
                db_path=db_path,
                search_term=search_term,
                work_ids=work_ids,
                legislation_type=legislation_type,
                publisher=publisher,
                max_versions_per_work=max_versions_per_work,
                version_sort=args.version_sort,
                per_page=args.per_page,
                max_pages=args.max_pages,
                max_works=args.max_works,
                max_versions=args.max_versions,
                include_xml=not args.no_xml,
                skip_existing=not args.refetch,
                delay=args.delay,
                request_budget=args.request_budget,
                reserve_remaining=args.reserve_remaining,
                sleep_on_rate_limit=args.sleep_on_rate_limit,
                max_sleep_seconds=args.max_sleep_seconds,
                rate_limit_retry_attempts=args.rate_limit_retry_attempts,
                diagnostics_jsonl=Path(args.diagnostics_jsonl) if args.diagnostics_jsonl else None,
                verbose=args.verbose,
            ),
        )
    finally:
        archive.close()


def _dependency_report_for_work(
    db_path: Path,
    work_id: str,
    state: NZClosureState,
) -> NZDependencyReport | None:
    archive = open_farchive(db_path)
    try:
        version_id, xml_locator = latest_xml_locator_for_work(archive, work_id)
        if not xml_locator:
            state.unresolved_work_ids.append(
                {
                    "work_id": work_id,
                    "rule_id": "nz_closure_latest_xml_missing",
                    "reason": "no archived latest XML locator found for dependency extraction",
                }
            )
            return None
        data = archive.get(xml_locator)
    finally:
        archive.close()
    if data is None:
        state.unresolved_work_ids.append(
            {
                "work_id": work_id,
                "rule_id": "nz_closure_latest_xml_locator_unreadable",
                "xml_locator": xml_locator,
            }
        )
        return None
    return extract_dependency_report(
        xml_bytes=data,
        xml_locator=xml_locator,
        work_id=work_id,
        version_id=version_id,
    )


def _write_state(path: Path, state: NZClosureState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_jsonable(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(args: Any) -> None:
    state = run_closure(args)
    print(
        f"seeds={len(state.seed_work_ids)} discovered={len(state.discovered_work_ids)} "
        f"reports={len(state.dependency_reports)} unresolved={len(state.unresolved_work_ids)} "
        f"sync_phases={len(state.sync_summaries)} state_json={args.state_json}"
    )
