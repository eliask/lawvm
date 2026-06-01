"""uk_acquire.py — CLI tool for ``lawvm uk-acquire``.

Wraps the per-statute UK acquisition logic from
``lawvm.uk_legislation.uk_acquire`` as a lawvm subcommand.

Usage examples:
    lawvm uk-acquire ukpga/2020/17
    lawvm uk-acquire ukpga/2020/17 --enacted-only
    lawvm uk-acquire ukpga/2020/17 --affecting
    lawvm uk-acquire ukpga/2020/17 --dry-run
    lawvm uk-acquire ukpga/2020/17 --json
    lawvm uk-acquire ukpga/2020/17 --force-refresh --db /path/to/uk.farchive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport


_DEFAULT_ARCHIVE_PATH = Path(__file__).resolve().parents[3] / "data" / "uk_legislation.farchive"


def uk_acquire_plan_report_jsonable(
    *,
    plan: Any,
    db_path: Path,
    enacted_only: bool,
    affecting: bool,
) -> dict[str, Any]:
    legacy_payload = dict(plan.to_dict())
    would_fetch = tuple(str(url) for url in plan.would_fetch())
    summary = {
        "statute_id": str(plan.statute_id),
        "dry_run": True,
        "would_fetch_count": len(would_fetch),
        "enacted_already_cached": bool(plan.enacted_already_cached),
        "current_stale": bool(plan.current_stale),
        "effects_stale": bool(plan.effects_stale),
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_acquire_plan_report",
        schema="lawvm.uk_acquire_plan_report.v1",
        truth_claim="uk_acquisition_plan_source_cache_evidence_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": str(plan.statute_id),
            "db_path": str(db_path),
            "enacted_only": enacted_only,
            "affecting": affecting,
            "dry_run": True,
        },
        filtered_summary=summary,
        rows=(),
        rows_truncated=False,
        detail={
            **legacy_payload,
            "archive_path": str(db_path),
            "would_fetch": would_fetch,
            "safe_default": "use_acquisition_plan_only_as_source_cache_precondition",
            "forbidden_shortcuts": (
                "cache_presence_as_source_semantics",
                "acquisition_plan_as_replay_authorization",
                "would_fetch_as_source_completeness_proof",
            ),
            "next_promotion_requires": (
                "source_fetch_success",
                "source_identity",
                "source_parse_observation",
            ),
        },
    ).to_dict()


def uk_acquire_report_jsonable(
    *,
    report: Any,
    db_path: Path,
    enacted_only: bool,
    affecting: bool,
    force_refresh: bool,
) -> dict[str, Any]:
    legacy_payload = dict(report.to_dict())
    affecting_events = tuple(dict(row) for row in legacy_payload.get("affecting_events", ()))
    error_count = sum(
        1
        for key in ("enacted_error", "current_error", "effects_error")
        if legacy_payload.get(key)
    ) + int(legacy_payload.get("affecting_errors") or 0)
    summary = {
        "statute_id": str(report.statute_id),
        "dry_run": False,
        "has_errors": bool(report.has_errors),
        "error_count": error_count,
        "enacted_fetched": bool(report.enacted_fetched),
        "enacted_already_cached": bool(report.enacted_already_cached),
        "current_fetched": bool(report.current_fetched),
        "current_already_cached": bool(report.current_already_cached),
        "effects_pages_fetched": int(report.effects_pages_fetched),
        "effects_already_cached": bool(report.effects_already_cached),
        "affecting_fetched": int(report.affecting_fetched),
        "affecting_cached": int(report.affecting_cached),
        "affecting_errors": int(report.affecting_errors),
        "affecting_event_count": len(affecting_events),
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_acquire_report",
        schema="lawvm.uk_acquire_report.v1",
        truth_claim="uk_acquisition_materialization_report_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": str(report.statute_id),
            "db_path": str(db_path),
            "enacted_only": enacted_only,
            "affecting": affecting,
            "force_refresh": force_refresh,
            "dry_run": False,
        },
        filtered_summary=summary,
        rows=affecting_events,
        rows_truncated=False,
        detail={
            **legacy_payload,
            "archive_path": str(db_path),
            "safe_default": "use_acquisition_report_as_source_availability_evidence_only",
            "forbidden_shortcuts": (
                "fetched_source_as_parsed_source_semantics",
                "cached_source_as_current_legal_truth",
                "acquisition_success_as_replay_authorization",
            ),
            "next_promotion_requires": (
                "source_identity",
                "source_parse_observation",
                "effect_metadata_parse",
                "affecting_source_extraction",
            ),
        },
    ).to_dict()


def main(args: argparse.Namespace) -> None:
    from lawvm.uk_legislation.uk_acquire import (
        UKAcquirePlan,
        acquire_statute,
        build_acquire_plan,
    )

    statute_id: str = args.statute_id
    db_path = Path(args.db) if getattr(args, "db", None) else _DEFAULT_ARCHIVE_PATH
    dry_run: bool = getattr(args, "dry_run", False)
    emit_json: bool = getattr(args, "json", False)
    enacted_only: bool = getattr(args, "enacted_only", False)
    affecting: bool = getattr(args, "affecting", False)
    force_refresh: bool = getattr(args, "force_refresh", False)
    verbose: bool = getattr(args, "verbose", False)
    delay: float = getattr(args, "delay", 0.5)

    if dry_run:
        # Dry-run: open archive read-only to check what would be fetched.
        if not db_path.exists():
            # Archive absent — everything would be fetched.
            plan = UKAcquirePlan(
                statute_id=statute_id,
                enacted_url=f"https://www.legislation.gov.uk/{statute_id}/enacted/data.xml",
                enacted_already_cached=False,
                current_url=f"https://www.legislation.gov.uk/{statute_id}/data.xml",
                current_stale=True,
                effects_base_url=(
                    f"https://www.legislation.gov.uk/changes/affected/{statute_id}"
                    "/data.feed?results-count=50&sort=modified"
                ),
                effects_stale=True,
            )
        else:
            from farchive import Farchive

            archive = Farchive(db_path)
            try:
                plan = build_acquire_plan(statute_id, archive)
            finally:
                archive.close()

        if emit_json:
            print(json.dumps(
                uk_acquire_plan_report_jsonable(
                    plan=plan,
                    db_path=db_path,
                    enacted_only=enacted_only,
                    affecting=affecting,
                ),
                ensure_ascii=False,
                indent=2,
            ))
        else:
            would = plan.would_fetch()
            print(f"DRY-RUN: statute={statute_id}  would_fetch={len(would)}")
            for url in would:
                print(f"  WOULD FETCH: {url}")
            if not would:
                if enacted_only:
                    if plan.enacted_already_cached:
                        print("  enacted: already cached")
                else:
                    print("  all resources already cached / fresh")
        return

    # Live run: archive must exist.
    if not db_path.exists():
        print(f"error: archive DB not found: {db_path}", file=sys.stderr)
        print("Run: uv run lawvm uk-corpus all", file=sys.stderr)
        sys.exit(1)

    from farchive import Farchive

    archive = Farchive(db_path)
    try:
        report = acquire_statute(
            statute_id,
            archive,
            enacted_only=enacted_only,
            affecting=affecting,
            force_refresh=force_refresh,
            delay=delay,
            verbose=verbose,
        )
    finally:
        archive.close()

    if emit_json:
        print(json.dumps(
            uk_acquire_report_jsonable(
                report=report,
                db_path=db_path,
                enacted_only=enacted_only,
                affecting=affecting,
                force_refresh=force_refresh,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    else:
        parts = []
        if report.enacted_fetched:
            parts.append("enacted=fetched")
        elif report.enacted_already_cached:
            parts.append("enacted=cached")
        elif report.enacted_error:
            parts.append(f"enacted=ERROR:{report.enacted_error}")

        if not enacted_only:
            if report.current_fetched:
                parts.append("current=fetched")
            elif report.current_already_cached:
                parts.append("current=cached")
            elif report.current_error:
                parts.append(f"current=ERROR:{report.current_error}")

            if report.effects_pages_fetched > 0:
                parts.append(f"effects=fetched:{report.effects_pages_fetched}p")
            elif report.effects_already_cached:
                parts.append("effects=cached")
            elif report.effects_error:
                parts.append(f"effects=ERROR:{report.effects_error}")

        if affecting:
            parts.append(
                f"affecting(fetched={report.affecting_fetched}"
                f" cached={report.affecting_cached}"
                f" errors={report.affecting_errors})"
            )

        print("  ".join(parts) if parts else "no-op")

    if report.has_errors:
        sys.exit(1)
