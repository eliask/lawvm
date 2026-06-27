"""Import U.S. federal Public Law USLM XML into a farchive.

Mirrors ``lawvm.tools.import_zip`` for the U.S. amendment-source half: stream a
govinfo bulkdata PLAW zip (one zip per Congress), iterate members, compute the
canonical ``us://plaw/{congress}/publ{N}.xml`` locator, and store each member
with SHA-256 dedup and typed skip records.

Sources may be local zip paths or govinfo HTTPS URLs. Only public laws (``publ``
members) are stored; private-law members (``pvtl``, not present in the public
bulkdata zips) are recorded as typed skips. This is the UNBLOCKED half: the USC
verification oracle (govinfo USCODE via ``api.govinfo.gov``) is out of scope and
left as a reserved namespace in :mod:`lawvm.us_federal.sources`.

Runnable without the global CLI::

    python -m lawvm.us_federal.import_plaw .tmp/us_staging/plaw/PLAW-118-public.zip
    python -m lawvm.us_federal.import_plaw --dry-run PLAW-118-public.zip
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
import tempfile
import urllib.request
import zipfile
import zlib

from lawvm.core.archive_safety import ArchiveMemberTooLarge, safe_zip_read
from lawvm.us_federal.sources import (
    GOVINFO_PLAW_MEMBER_URL,
    content_digest,
    open_us_federal_import_farchive,
    parse_plaw_member_name,
    resolve_us_federal_farchive_path,
)

_HTTP_CHUNK_SIZE = 1024 * 1024
_SPOOLED_MAX_BYTES = 64 * 1024 * 1024
_PROGRESS_INTERVAL = 500


@dataclass
class ImportReport:
    """Aggregated result from one or more PLAW import operations."""

    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    bytes_stored: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)
    congress_counts: dict[int, int] = field(default_factory=dict)


def _record_import_skip(
    report: ImportReport,
    *,
    rule_id: str,
    family: str,
    reason: str,
    source_label: str,
    zip_entry_name: str,
    locator: str | None = None,
    detail: dict[str, str] | None = None,
) -> None:
    record: dict[str, Any] = {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": family,
        "reason": reason,
        "source": source_label,
        "entry_name": zip_entry_name,
    }
    if locator:
        record["locator"] = locator
    if detail:
        record.update(detail)
    report.skipped_entries.append(record)


def _is_http_url(source: Path | str) -> bool:
    return str(source).startswith(("http://", "https://"))


def _zip_source_label(zip_source: Path | str) -> str:
    if _is_http_url(zip_source):
        return str(zip_source).rsplit("/", 1)[-1] or str(zip_source)
    return Path(zip_source).name


@contextmanager
def _open_zip_source(zip_source: Path | str) -> Iterator[Any]:
    """Yield a seekable binary file object for a ZIP source (local or HTTPS)."""
    if not _is_http_url(zip_source):
        with Path(zip_source).open("rb") as fp:
            yield fp
        return

    req = urllib.request.Request(
        str(zip_source),
        headers={
            "User-Agent": "LawVM/0.1 (+https://lawvm.org)",
            "Accept": "application/zip, application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (https only via guard)
        with tempfile.SpooledTemporaryFile(max_size=_SPOOLED_MAX_BYTES, mode="w+b") as tmp:
            while True:
                chunk = resp.read(_HTTP_CHUNK_SIZE)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp.seek(0)
            yield tmp


def _zip_entry_mtime(info: zipfile.ZipInfo) -> datetime | None:
    try:
        return datetime(*info.date_time, tzinfo=timezone.utc)
    except ValueError:
        return None


def import_plaw_zip(
    zip_path_or_url: Path | str,
    farchive: Any,
    *,
    skip_existing: bool = False,
    dry_run: bool = False,
    progress: Callable[[int], None] | None = None,
) -> ImportReport:
    """Import Public Law USLM XML members from one PLAW zip into farchive.

    Each ``PLAW-{c}publ{N}.xml`` member is stored at
    ``us://plaw/{c}/publ{N}.xml`` with storage_class ``xml`` and metadata
    {source_url, congress, law_number, entry_name, zip_entry_mtime}. SHA-256
    dedup skips byte-identical re-imports when ``skip_existing`` is set;
    private-law and unparseable members are typed skips.
    """
    report = ImportReport(sources=[str(zip_path_or_url)])
    zip_label = _zip_source_label(zip_path_or_url)
    source_url = str(zip_path_or_url)
    seen_locators: dict[str, str] = {}

    with _open_zip_source(zip_path_or_url) as zip_fp, zipfile.ZipFile(zip_fp, "r") as zf:
        names = zf.namelist()
        print(f"  PLAW zip: {len(names):,} entries in {zip_label}", file=sys.stderr)

        for i, name in enumerate(names):
            report.total_scanned += 1

            info = zf.getinfo(name)
            if info.is_dir() or info.file_size == 0:
                continue

            identity = parse_plaw_member_name(Path(name).name)
            if identity is None:
                report.total_skipped += 1
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_unrecognized_member",
                    family="source_pathology",
                    reason="zip member name did not match PLAW USLM member convention",
                    source_label=zip_label,
                    zip_entry_name=name,
                )
                continue

            if not identity.is_public:
                report.total_skipped += 1
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_private_law_filtered",
                    family="transport_cleanup",
                    reason="private-law member filtered; only public laws are stored",
                    source_label=zip_label,
                    zip_entry_name=name,
                    locator=identity.locator,
                    detail={
                        "congress": str(identity.congress),
                        "law_number": str(identity.number),
                    },
                )
                continue

            locator = identity.locator

            previous_entry = seen_locators.get(locator)
            if previous_entry is not None and previous_entry != name:
                report.total_skipped += 1
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_duplicate_logical_locator",
                    family="source_pathology",
                    reason="duplicate logical locator in zip; later entry skipped",
                    source_label=zip_label,
                    zip_entry_name=name,
                    locator=locator,
                    detail={"previous_entry_name": previous_entry},
                )
                continue
            seen_locators.setdefault(locator, name)

            try:
                data = safe_zip_read(zf, name, archive_path=zip_label)
            except ArchiveMemberTooLarge as exc:
                # Acquisition lane: never silently drop. Emit a typed
                # rejection receipt (AGENTS.md §1.8/§1.10) carrying the
                # declared vs cap sizes plus the entry name + locator, so
                # the gap is visible in report.skipped_entries rather than
                # a bare stderr print that disappears. Family:
                # transport_cleanup (mechanical IO failure, no legal-
                # ontology implication). Non-blocking: the cap is operator-
                # tunable via LAWVM_MAX_ARCHIVE_MEMBER_BYTES; over-retention
                # (omit rather than fabricate) per AGENTS.md §0.
                report.total_skipped += 1
                entry_locator = locator if locator else ""
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_archive_member_too_large",
                    family="transport_cleanup",
                    reason=exc.diagnostic.render_reason(),
                    source_label=zip_label,
                    zip_entry_name=name,
                    locator=entry_locator or None,
                    detail={
                        "declared_size": str(exc.declared_size),
                        "cap_bytes": str(exc.cap_bytes),
                        "archive_path": exc.archive_path,
                        "blocking": "false",
                    },
                )
                continue
            except (zipfile.BadZipFile, OSError, zlib.error, EOFError) as exc:  # corrupt/truncated zip member
                # Acquisition lane: never silently drop. Emit a typed rejection
                # receipt (AGENTS.md §1.8/§1.10) carrying the entry name, locator,
                # and the underlying exception class/message, so the gap is visible
                # in report.skipped_entries rather than a bare stderr print that
                # disappears. Family: transport_cleanup (mechanical IO failure,
                # no legal-ontology implication).
                report.total_errors += 1
                report.total_skipped += 1
                entry_locator = locator if locator else ""
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_unreadable_zip_member",
                    family="transport_cleanup",
                    reason=(
                        f"zip member unreadable ({type(exc).__name__}: {exc}); "
                        "entry absent from the archive as a visible acquisition gap"
                    ),
                    source_label=zip_label,
                    zip_entry_name=name,
                    locator=entry_locator or None,
                    detail={"exception_type": type(exc).__name__},
                )
                continue

            digest = content_digest(data)
            current = farchive.resolve(locator)
            if current is not None and current.digest == digest and skip_existing:
                report.total_skipped += 1
                _record_import_skip(
                    report,
                    rule_id="us_plaw_import_existing_content_skipped",
                    family="transport_cleanup",
                    reason="archive already contains identical content and skip_existing was enabled",
                    source_label=zip_label,
                    zip_entry_name=name,
                    locator=locator,
                    detail={"digest": digest},
                )
                continue

            if current is not None and current.digest != digest:
                print(
                    f"WARNING: {locator} changed in {zip_label}: "
                    f"{current.digest[:12]}.. -> {digest[:12]}..",
                    file=sys.stderr,
                )

            report.congress_counts[identity.congress] = (
                report.congress_counts.get(identity.congress, 0) + 1
            )

            if dry_run:
                report.total_imported += 1
                report.bytes_raw += len(data)
                report.bytes_stored += len(data)
                continue

            metadata = {
                "source_url": GOVINFO_PLAW_MEMBER_URL.format(
                    congress=identity.congress, number=identity.number
                ),
                "acquisition_source": source_url,
                "congress": str(identity.congress),
                "law_number": str(identity.number),
                "public_law": identity.public_law_label,
                "entry_name": name,
            }
            entry_mtime = _zip_entry_mtime(info)
            if entry_mtime is not None:
                metadata["zip_entry_mtime"] = entry_mtime.isoformat()

            farchive.store(
                locator,
                data,
                storage_class="xml",
                metadata=metadata,
            )
            report.total_imported += 1
            report.bytes_raw += len(data)
            report.bytes_stored += len(data)

            if progress and (i + 1) % _PROGRESS_INTERVAL == 0:
                progress(i + 1)

    return report


def import_plaw_sources(
    sources: list[Path | str],
    *,
    db_path: Path | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> ImportReport:
    """Import several PLAW zips into the canonical (or given) U.S. farchive."""
    overall = ImportReport()

    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    archive = open_us_federal_import_farchive(db_path, dry_run=dry_run)
    try:
        for source in sources:
            print(f"\nImporting PLAW source: {source}", file=sys.stderr)

            def _progress(done: int) -> None:
                print(f"  scanned {done:,} entries...", file=sys.stderr)

            report = import_plaw_zip(
                source,
                archive,
                skip_existing=skip_existing,
                dry_run=dry_run,
                progress=_progress,
            )
            overall.sources.append(str(source))
            overall.total_scanned += report.total_scanned
            overall.total_imported += report.total_imported
            overall.total_skipped += report.total_skipped
            overall.total_errors += report.total_errors
            overall.bytes_raw += report.bytes_raw
            overall.bytes_stored += report.bytes_stored
            overall.skipped_entries.extend(report.skipped_entries)
            for congress, count in report.congress_counts.items():
                overall.congress_counts[congress] = (
                    overall.congress_counts.get(congress, 0) + count
                )
            print(
                f"  imported={report.total_imported:,}  "
                f"skipped={report.total_skipped:,}  errors={report.total_errors:,}",
                file=sys.stderr,
            )
    finally:
        archive.close()

    return overall


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import U.S. federal Public Law USLM XML into a farchive.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="PLAW zip paths or govinfo HTTPS URLs (PLAW-{congress}-public.zip).",
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
        help="Skip members whose identical content is already stored.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without writing to the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    for source in args.sources:
        if not _is_http_url(source) and not Path(source).exists():
            print(f"error: PLAW source not found: {source}", file=sys.stderr)
            return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        print(f"Opening farchive: {args.dest}", file=sys.stderr)

    report = import_plaw_sources(
        list(args.sources),
        db_path=args.dest,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )

    print("\nPLAW import complete:")
    print(f"  Total scanned:  {report.total_scanned:,}")
    print(f"  Total imported: {report.total_imported:,}")
    print(f"  Total skipped:  {report.total_skipped:,}")
    print(f"  Total errors:   {report.total_errors:,}")
    if report.bytes_raw:
        ratio = report.bytes_stored / report.bytes_raw
        print(f"  Raw bytes:      {report.bytes_raw:,}")
        print(f"  Stored bytes:   {report.bytes_stored:,}")
        print(f"  Ratio:          {ratio:.1%}")
    if report.congress_counts:
        per = "  ".join(
            f"{c}:{n}" for c, n in sorted(report.congress_counts.items())
        )
        print(f"  Per Congress:   {per}")

    return 1 if report.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
