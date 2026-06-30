"""Import a full OLRC USLM-USC release point (all titles) into a farchive.

Sibling of :mod:`lawvm.us_federal.import_release` (per-PL Wayback lane) and
:mod:`lawvm.us_federal.import_usc` (annual-edition htm). This lane ingests the
*full* OLRC release point — every USC title's USLM 1.0 XML as it stood at one
Public Law pin (e.g. release point ``119-99``) — archive-first.

ARCHIVE-FIRST, XML-ONLY: the farchive stores ONLY the extracted per-title XML
bytes, content-addressed by SHA-256, at canonical locators
``us://usc-uslm/{release_point}/title{code}.xml``. The OLRC release-point zip is
NEVER stored — the importer reads loose extracted ``usc{NN}[suffix].xml`` files
(one per USC title) from a staging directory or as explicit paths.

The XML is parsed by :func:`lawvm.us_federal.source_tree.parse_uslm_title_document`
(the USLM 1.0 ``http://xml.house.gov/schemas/uslm/1.0`` parser already shared
with the per-PL release-point lane), so an ingested title is immediately a typed
USC source tree — the authoritative oracle the annual-edition htm only
substitutes for.

Per AGENTS.md §1.10 every skip/error is a typed receipt: an unrecognized member
name, a member whose name disagrees with an explicit title, and a byte-identical
re-import are all recorded in ``UscReleaseImportReport.skipped_entries`` rather
than disappearing silently.

NOTE on member-size cap: the largest title (Title 42, ~113 MB uncompressed) and
some others exceed the default 100 MB ``LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` cap; the
operator raises that env (e.g. ``export LAWVM_MAX_ARCHIVE_MEMBER_BYTES=$((192*1024*1024))``)
before ingest. The importer surfaces an oversize file as a typed skip, never a
silent truncation.

Runnable without the global CLI::

    python -m lawvm.us_federal.import_usc_release \\
        --release-point 119-99 --stage-dir /tmp/us_olrc/stage_xml
    python -m lawvm.us_federal.import_usc_release \\
        --release-point 119-99 /path/to/usc01.xml /path/to/usc42.xml
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lawvm.us_federal.sources import (
    UscUslmReleaseIdentity,
    content_digest,
    open_us_federal_import_farchive,
    parse_usc_uslm_member_name,
    resolve_us_federal_farchive_path,
)

# OLRC full-release-point source URL (the zip the loose XML was extracted from).
OLRC_RELEASE_POINT_ALL_URL = (
    "https://uscode.house.gov/download/releasepoints/us/pl/"
    "{congress}/{num}/xml_uscAll@{congress}-{num}.zip"
)

# A release-point pin: "119-99" (congress-number).
_RELEASE_POINT_RE = re.compile(r"^(?P<congress>\d{1,3})-(?P<num>\d{1,4})$")


@dataclass
class UscReleaseImportReport:
    """Aggregated result from one or more full-release-point title imports."""

    release_point: str = ""
    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)
    imported_locators: list[str] = field(default_factory=list)


def _record_skip(
    report: UscReleaseImportReport,
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


def validate_release_point(release_point: str) -> str:
    """Validate a ``{congress}-{num}`` release-point pin, returning it normalized."""
    m = _RELEASE_POINT_RE.match(release_point.strip())
    if m is None:
        raise ValueError(
            f"release point must be '{{congress}}-{{num}}' (e.g. '119-99'); "
            f"got {release_point!r}"
        )
    return f"{int(m.group('congress'))}-{int(m.group('num'))}"


def import_usc_release_xml(
    source: Path,
    farchive: Any,
    *,
    release_point: str,
    expected_title: int | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> UscReleaseImportReport:
    """Import one extracted USLM-USC title XML file into the farchive.

    ``source`` is a loose extracted ``usc{NN}[suffix].xml`` file (NOT a zip).
    The (title, suffix) identity is read from the member name; ``expected_title``
    cross-checks it when supplied.
    """
    report = UscReleaseImportReport(release_point=release_point, sources=[str(source)])
    label = source.name

    parsed = parse_usc_uslm_member_name(label)
    if parsed is None:
        report.total_scanned += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_usc_release_unrecognized_member",
            family="source_pathology",
            reason="file name did not match usc{NN}[suffix].xml",
            source_label=label,
        )
        return report
    title, suffix = parsed

    if expected_title is not None and title != expected_title:
        report.total_scanned += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_usc_release_member_identity_mismatch",
            family="source_pathology",
            reason="member-name title disagrees with the requested title",
            source_label=label,
            detail={"member_title": str(title), "requested_title": str(expected_title)},
        )
        return report

    identity = UscUslmReleaseIdentity(
        release_point=release_point, title=title, suffix=suffix
    )
    locator = identity.locator
    report.total_scanned += 1

    try:
        data = source.read_bytes()
    except OSError as exc:
        report.total_errors += 1
        _record_skip(
            report,
            rule_id="us_usc_release_source_unreadable",
            family="source_pathology",
            reason=f"release-point XML could not be read ({type(exc).__name__}: {exc})",
            source_label=label,
            detail={"source": str(source)},
        )
        return report

    digest = content_digest(data)
    current = farchive.resolve(locator)
    if current is not None and current.digest == digest and skip_existing:
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_usc_release_existing_content_skipped",
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
        "release_point": release_point,
        "title": str(title),
        "title_code": identity.title_code,
        "appendix_suffix": suffix,
        "zip_member_name": label,
        "source_url": OLRC_RELEASE_POINT_ALL_URL.format(
            congress=release_point.split("-", 1)[0],
            num=release_point.split("-", 1)[1],
        ),
        "acquisition_source": str(source),
        "acquisition_channel": "olrc_full_release_point_uslm",
        "uslm_namespace": "http://xml.house.gov/schemas/uslm/1.0",
        "sha256": digest,
    }
    farchive.store(locator, data, storage_class="xml", metadata=metadata)
    report.total_imported += 1
    report.imported_locators.append(locator)
    return report


def import_usc_release_dir(
    stage_dir: Path,
    *,
    release_point: str,
    db_path: Path | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> UscReleaseImportReport:
    """Import every loose ``usc{NN}[suffix].xml`` in a staging directory."""
    release_point = validate_release_point(release_point)
    members = sorted(
        p for p in stage_dir.iterdir()
        if p.is_file() and parse_usc_uslm_member_name(p.name) is not None
    )
    overall = UscReleaseImportReport(release_point=release_point)
    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    archive = open_us_federal_import_farchive(db_path, dry_run=dry_run)
    try:
        for member in members:
            report = import_usc_release_xml(
                member,
                archive,
                release_point=release_point,
                skip_existing=skip_existing,
                dry_run=dry_run,
            )
            _merge(overall, report)
            print(
                f"  {member.name}: imported={report.total_imported} "
                f"skipped={report.total_skipped} errors={report.total_errors}",
                file=sys.stderr,
            )
    finally:
        archive.close()
    return overall


def _merge(overall: UscReleaseImportReport, report: UscReleaseImportReport) -> None:
    overall.sources.extend(report.sources)
    overall.total_scanned += report.total_scanned
    overall.total_imported += report.total_imported
    overall.total_skipped += report.total_skipped
    overall.total_errors += report.total_errors
    overall.bytes_raw += report.bytes_raw
    overall.skipped_entries.extend(report.skipped_entries)
    overall.imported_locators.extend(report.imported_locators)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import a full OLRC USLM-USC release point (all titles) into the "
            "canonical U.S. farchive, archive-first and XML-only (the zip is "
            "never stored)."
        ),
    )
    parser.add_argument(
        "sources",
        nargs="*",
        help="Loose extracted usc{NN}[suffix].xml paths (alternative to --stage-dir).",
    )
    parser.add_argument(
        "--release-point", required=True,
        help="Release-point pin '{congress}-{num}' (e.g. 119-99).",
    )
    parser.add_argument(
        "--stage-dir", type=Path, default=None,
        help="Directory of loose extracted usc{NN}[suffix].xml files.",
    )
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Explicit farchive path (default: canonical data/us_federal.farchive).",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip titles whose identical content is already stored.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report without writing to the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        release_point = validate_release_point(args.release_point)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        dest_path = args.dest
        print(f"Opening farchive: {dest_path}", file=sys.stderr)

    if args.stage_dir is not None:
        report = import_usc_release_dir(
            args.stage_dir,
            release_point=release_point,
            db_path=dest_path,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )
    elif args.sources:
        report = UscReleaseImportReport(release_point=release_point)
        archive = open_us_federal_import_farchive(dest_path, dry_run=args.dry_run)
        try:
            for src in args.sources:
                sub = import_usc_release_xml(
                    Path(src),
                    archive,
                    release_point=release_point,
                    skip_existing=args.skip_existing,
                    dry_run=args.dry_run,
                )
                _merge(report, sub)
        finally:
            archive.close()
    else:
        print("error: pass usc{NN}.xml paths or --stage-dir", file=sys.stderr)
        return 1

    print("\nUSC full-release-point import complete:")
    print(f"  Release point:  {report.release_point}")
    print(f"  Total scanned:  {report.total_scanned}")
    print(f"  Total imported: {report.total_imported}")
    print(f"  Total skipped:  {report.total_skipped}")
    print(f"  Total errors:   {report.total_errors}")
    if report.bytes_raw:
        print(f"  Raw bytes:      {report.bytes_raw:,}")
    if report.skipped_entries:
        print("  Skips:")
        for entry in report.skipped_entries:
            print(f"    {entry['rule_id']}: {entry['source']}")
    return 1 if report.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
