"""Import U.S. Code annual-edition title htm into a farchive.

Mirrors :mod:`lawvm.us_federal.import_plaw` for the USC verification-oracle half.
Each staged ``USCODE-{year}-title{N}.htm`` (govinfo annual edition,
``application/xhtml+xml``) is stored at the canonical locator
``us://usc/{year}/title{N}.htm`` with storage_class ``html`` and metadata
{year, title, source_url, sha256, edition currency markers}.

Sources may be local htm paths or the keyless govinfo ``/content/pkg/`` HTTPS
URL. SHA-256 dedup skips byte-identical re-imports when ``--skip-existing`` is
set; member names that do not match the USCODE convention are typed skips
(``us_usc_import_unrecognized_member``); the requested (year, title) and a
member-name (year, title) that disagree are a typed skip
(``us_usc_import_member_identity_mismatch``) rather than a silent guess.

Runnable without the global CLI::

    python -m lawvm.us_federal.import_usc .tmp/us_staging/usc/USCODE-2023-title11.htm
    python -m lawvm.us_federal.import_usc --dry-run --stage-dir .tmp/us_staging/usc \\
        --year 2022 --year 2023 --year 2024 --title 11 --title 35 --title 18
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from lawvm.us_federal.sources import (
    GOVINFO_USCODE_HTM_URL,
    UscAnnualIdentity,
    content_digest,
    extract_usc_edition_currency,
    open_us_federal_farchive,
    parse_usc_member_name,
    resolve_us_federal_farchive_path,
)

_HTTP_CHUNK_SIZE = 1024 * 1024


@dataclass
class UscImportReport:
    """Aggregated result from one or more USC htm import operations."""

    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)
    imported_locators: list[str] = field(default_factory=list)


def _record_skip(
    report: UscImportReport,
    *,
    rule_id: str,
    family: str,
    reason: str,
    source_label: str,
    detail: dict[str, str] | None = None,
) -> None:
    record: dict[str, Any] = {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": family,
        "reason": reason,
        "source": source_label,
    }
    if detail:
        record.update(detail)
    report.skipped_entries.append(record)


def _is_http_url(source: Path | str) -> bool:
    return str(source).startswith(("http://", "https://"))


def _source_label(source: Path | str) -> str:
    if _is_http_url(source):
        return str(source).rsplit("/", 1)[-1] or str(source)
    return Path(source).name


@contextmanager
def _open_htm_source(source: Path | str) -> Iterator[bytes]:
    """Yield the bytes of one USCODE htm source (local path or HTTPS URL)."""
    if not _is_http_url(source):
        with Path(source).open("rb") as fp:
            yield fp.read()
        return

    req = urllib.request.Request(
        str(source),
        headers={
            "User-Agent": "LawVM/0.1 (+https://lawvm.org)",
            "Accept": "application/xhtml+xml, text/html;q=0.9, */*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (https only via guard)
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b") as tmp:
            while True:
                chunk = resp.read(_HTTP_CHUNK_SIZE)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp.seek(0)
            yield tmp.read()


def import_usc_source(
    source: Path | str,
    farchive: Any,
    *,
    expected: UscAnnualIdentity | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> UscImportReport:
    """Import one USCODE annual-edition title htm into farchive.

    ``expected`` pins the (year, title) when the source URL/path does not carry a
    parseable member name (or to cross-check one that does). When both the member
    name and ``expected`` are present and disagree, the source is a typed skip.
    """
    report = UscImportReport(sources=[str(source)])
    label = _source_label(source)
    source_url = str(source)

    name_identity = parse_usc_member_name(label)
    identity = name_identity or expected
    if identity is None:
        report.total_scanned += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_usc_import_unrecognized_member",
            family="source_pathology",
            reason=(
                "source name did not match USCODE-{year}-title{N}.htm and no "
                "explicit (year, title) was supplied"
            ),
            source_label=label,
        )
        return report

    if (
        name_identity is not None
        and expected is not None
        and (name_identity.year, name_identity.title)
        != (expected.year, expected.title)
    ):
        report.total_scanned += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_usc_import_member_identity_mismatch",
            family="source_pathology",
            reason="member name (year, title) disagrees with requested (year, title)",
            source_label=label,
            detail={
                "member": f"{name_identity.year}/title{name_identity.title}",
                "requested": f"{expected.year}/title{expected.title}",
            },
        )
        return report

    report.total_scanned += 1
    locator = identity.locator

    try:
        with _open_htm_source(source) as data:
            digest = content_digest(data)
            current = farchive.resolve(locator)
            if current is not None and current.digest == digest and skip_existing:
                report.total_skipped += 1
                _record_skip(
                    report,
                    rule_id="us_usc_import_existing_content_skipped",
                    family="transport_cleanup",
                    reason="archive already contains identical content and skip_existing was enabled",
                    source_label=label,
                    detail={"locator": locator, "digest": digest},
                )
                return report

            if current is not None and current.digest != digest:
                print(
                    f"WARNING: {locator} changed: "
                    f"{current.digest[:12]}.. -> {digest[:12]}..",
                    file=sys.stderr,
                )

            report.bytes_raw += len(data)
            if dry_run:
                report.total_imported += 1
                report.imported_locators.append(locator)
                return report

            metadata = {
                "year": str(identity.year),
                "title": str(identity.title),
                "source_url": GOVINFO_USCODE_HTM_URL.format(
                    year=identity.year, title=identity.title
                ),
                "acquisition_source": source_url,
                "sha256": digest,
            }
            metadata.update(extract_usc_edition_currency(data))

            farchive.store(
                locator,
                data,
                storage_class="html",
                metadata=metadata,
            )
            report.total_imported += 1
            report.imported_locators.append(locator)
    except FileNotFoundError:
        report.total_errors += 1
        _record_skip(
            report,
            rule_id="us_usc_import_source_unreadable",
            family="source_pathology",
            reason="USC htm source could not be opened",
            source_label=label,
            detail={"source": source_url},
        )

    return report


def import_usc_sources(
    sources: list[tuple[Path | str, UscAnnualIdentity | None]],
    *,
    db_path: Path | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> UscImportReport:
    """Import several USC htm documents into the canonical (or given) farchive."""
    overall = UscImportReport()

    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        for source, expected in sources:
            print(f"\nImporting USC source: {source}", file=sys.stderr)
            report = import_usc_source(
                source,
                archive,
                expected=expected,
                skip_existing=skip_existing,
                dry_run=dry_run,
            )
            overall.sources.append(str(source))
            overall.total_scanned += report.total_scanned
            overall.total_imported += report.total_imported
            overall.total_skipped += report.total_skipped
            overall.total_errors += report.total_errors
            overall.bytes_raw += report.bytes_raw
            overall.skipped_entries.extend(report.skipped_entries)
            overall.imported_locators.extend(report.imported_locators)
            print(
                f"  imported={report.total_imported}  "
                f"skipped={report.total_skipped}  errors={report.total_errors}",
                file=sys.stderr,
            )
    finally:
        archive.close()

    return overall


def _resolve_stage_sources(
    stage_dir: Path, years: list[int], titles: list[int]
) -> list[tuple[Path | str, UscAnnualIdentity | None]]:
    """Build (path, identity) pairs for staged USCODE htm in a directory."""
    sources: list[tuple[Path | str, UscAnnualIdentity | None]] = []
    for year in years:
        for title in titles:
            identity = UscAnnualIdentity(year=year, title=title)
            path = stage_dir / identity.member_name
            sources.append((path, identity))
    return sources


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import U.S. Code annual-edition title htm into a farchive.",
    )
    parser.add_argument(
        "sources",
        nargs="*",
        help="USCODE htm paths or keyless govinfo /content/pkg/ HTTPS URLs.",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=None,
        help="Directory of staged USCODE-{year}-title{N}.htm files (with --year/--title).",
    )
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        default=None,
        help="Edition year (repeatable); used with --stage-dir.",
    )
    parser.add_argument(
        "--title",
        type=int,
        action="append",
        default=None,
        help="USC title number (repeatable); used with --stage-dir.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Explicit farchive path (default: canonical data/us_federal.farchive).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip sources whose identical content is already stored.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without writing to the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    sources: list[tuple[Path | str, UscAnnualIdentity | None]] = []

    if args.stage_dir is not None:
        if not args.year or not args.title:
            print("error: --stage-dir requires at least one --year and one --title", file=sys.stderr)
            return 1
        sources.extend(_resolve_stage_sources(args.stage_dir, args.year, args.title))

    for source in args.sources:
        sources.append((source, None))

    if not sources:
        print("error: no sources (pass htm paths/URLs or --stage-dir + --year/--title)", file=sys.stderr)
        return 1

    missing = [
        str(src)
        for src, _ in sources
        if not _is_http_url(src) and not Path(src).exists()
    ]
    if missing:
        for m in missing:
            print(f"error: USC source not found: {m}", file=sys.stderr)
        return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        print(f"Opening farchive: {args.dest}", file=sys.stderr)

    report = import_usc_sources(
        sources,
        db_path=args.dest,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )

    print("\nUSC import complete:")
    print(f"  Total scanned:  {report.total_scanned}")
    print(f"  Total imported: {report.total_imported}")
    print(f"  Total skipped:  {report.total_skipped}")
    print(f"  Total errors:   {report.total_errors}")
    if report.bytes_raw:
        print(f"  Raw bytes:      {report.bytes_raw:,}")
    if report.imported_locators:
        print("  Locators:")
        for loc in report.imported_locators:
            print(f"    {loc}")
    if report.skipped_entries:
        print("  Skips:")
        for entry in report.skipped_entries:
            print(f"    {entry['rule_id']}: {entry['source']}")

    return 1 if report.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
