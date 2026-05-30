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


_DEFAULT_ARCHIVE_PATH = Path(__file__).resolve().parents[3] / "data" / "uk_legislation.farchive"


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
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
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
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
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
