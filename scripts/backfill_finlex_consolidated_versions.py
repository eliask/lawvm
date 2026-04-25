"""Backfill legacy consolidated locators from sd-cons-old to canonical sd-cons.

This script owns the legacy migration lane only. It scans ``sd-cons-old``
family roots, derives the embedded amendment-id tag from each family's XML
``FRBRthis`` / ``FRBRversionNumber`` and writes the canonical versioned
locator family under ``finlex://sd-cons/.../lang@YYYYNNNN``.

Existing canonical versioned locators are left untouched.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from farchive import Farchive

from lawvm.finland.consolidated_artifacts import (
    build_canonical_consolidated_locator,
    build_consolidated_family_glob,
    extract_consolidated_xml_identity,
    parse_consolidated_locator,
)


@dataclass
class BackfillStats:
    families_seen: int = 0
    families_versioned: int = 0
    locators_examined: int = 0
    locators_backfilled: int = 0
    locators_skipped_existing: int = 0
    locators_unversionable: int = 0
    errors: int = 0


def _storage_class_for_locator(locator: str) -> str:
    suffix = Path(locator).suffix.lower()
    if suffix == ".gif":
        return "gif"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".txt":
        return "text"
    return "xml"


def _legacy_family_root(locator: str) -> str | None:
    parts = parse_consolidated_locator(locator)
    if parts is None or parts.namespace != "sd-cons-old" or parts.rest != "main.xml":
        return None
    if parts.version:
        return f"finlex://sd-cons-old/{parts.sid}/{parts.lang}@{parts.version}"
    return f"finlex://sd-cons-old/{parts.sid}/{parts.lang}"


def _legacy_family_lang(locator: str) -> str | None:
    parts = parse_consolidated_locator(locator)
    if parts is None or parts.namespace != "sd-cons-old" or parts.rest != "main.xml":
        return None
    return parts.lang


def _canonical_locator_from_legacy(locator: str, version_tag: str) -> str | None:
    parts = parse_consolidated_locator(locator)
    if parts is None or parts.namespace != "sd-cons-old":
        return None
    return build_canonical_consolidated_locator(
        sid=parts.sid,
        lang=parts.lang,
        version_tag=version_tag,
        rest=parts.rest,
    )


def _iter_legacy_main_locators(archive: Farchive) -> Iterable[str]:
    for locator in archive.locators(build_consolidated_family_glob(namespace="sd-cons-old")):
        parts = parse_consolidated_locator(locator)
        if parts is None or parts.namespace != "sd-cons-old" or parts.rest != "main.xml":
            continue
        yield locator


def run(*, db: Path, dry_run: bool = False, verbose: bool = False) -> BackfillStats:
    archive = Farchive(db)
    stats = BackfillStats()
    versions_by_family: dict[str, str] = {}
    written_locators: set[str] = set()

    try:
        legacy_mains = list(_iter_legacy_main_locators(archive))
        stats.families_seen = len(legacy_mains)

        for locator in legacy_mains:
            family_root = _legacy_family_root(locator)
            if family_root is None:
                continue
            family_lang = _legacy_family_lang(locator)
            data = archive.get(locator)
            if data is None:
                stats.errors += 1
                if verbose:
                    print(f"[migrate-cons] {locator}: missing payload", file=sys.stderr)
                continue
            version_tag = extract_consolidated_xml_identity(
                data,
                preferred_lang=family_lang,
            ).embedded_version_tag
            if not version_tag:
                stats.locators_unversionable += 1
                if verbose:
                    print(f"[migrate-cons] {locator}: no embedded version identity", file=sys.stderr)
                continue
            versions_by_family[family_root] = version_tag

        for family_root, version_tag in sorted(versions_by_family.items()):
            stats.families_versioned += 1
            for locator in archive.locators(f"{family_root}/%"):
                stats.locators_examined += 1
                canonical = _canonical_locator_from_legacy(locator, version_tag)
                if canonical is None:
                    continue
                if canonical in written_locators or archive.has(canonical):
                    stats.locators_skipped_existing += 1
                    if verbose:
                        print(f"[migrate-cons] {locator} -> {canonical}: exists", file=sys.stderr)
                    continue
                payload = archive.get(locator)
                if payload is None:
                    stats.errors += 1
                    if verbose:
                        print(f"[migrate-cons] {locator}: missing payload", file=sys.stderr)
                    continue
                if dry_run:
                    stats.locators_backfilled += 1
                    if verbose:
                        print(f"[migrate-cons] {locator} -> {canonical}: dry-run", file=sys.stderr)
                    continue
                archive.store(canonical, payload, storage_class=_storage_class_for_locator(locator))
                written_locators.add(canonical)
                stats.locators_backfilled += 1
                if verbose:
                    print(f"[migrate-cons] {locator} -> {canonical}: stored", file=sys.stderr)

        return stats
    finally:
        archive.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill finlex://sd-cons-old/... consolidated locators into "
            "canonical versioned finlex://sd-cons/... locators."
        )
    )
    parser.add_argument(
        "--db",
        default="data/finlex.farchive",
        metavar="PATH",
        help="farchive DB path (default: data/finlex.farchive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written without storing anything",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print each migrated locator to stderr",
    )
    args = parser.parse_args()

    stats = run(db=Path(args.db), dry_run=args.dry_run, verbose=args.verbose)
    print(
        f"families_seen={stats.families_seen} "
        f"families_versioned={stats.families_versioned} "
        f"examined={stats.locators_examined} "
        f"backfilled={stats.locators_backfilled} "
        f"skipped_existing={stats.locators_skipped_existing} "
        f"unversionable={stats.locators_unversionable} "
        f"errors={stats.errors}"
    )
    if stats.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
