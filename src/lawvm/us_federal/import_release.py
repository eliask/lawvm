"""Import U.S. Code USLM release-point XML into a farchive.

This is the *release-point* acquisition lane — sibling to
:mod:`lawvm.us_federal.import_usc` (annual-edition htm) and
:mod:`lawvm.us_federal.import_plaw` (PLAW bulkdata USLM).

The OLRC (uscode.house.gov) publishes per-Public-Law USC *release-point* zips:
snapshots of the official consolidated USC as it stood after each Public Law
took effect. Each zip contains one or more USLM XML files (one per USC title
that PL touched), with proper ``<section>``/``<subsection>``/``<paragraph>``
element nesting (richer than the flat-HTML annual editions).

OLRC is geo-blocked from the build host, but the release-point zips are
mirrored on the Wayback Machine::

    https://web.archive.org/web/{timestamp}id_/
        http://uscode.house.gov/download/releasepoints/us/pl/
        {congress}/{pl_number}/xml_usc{title:02d}@{congress}-{pl_number}.zip

The ``id_`` modifier returns the raw archived bytes (no Wayback chrome). The
zip member name pattern is ``xml_usc{title:02d}@{congress}-{pl_number}.xml``.

Storage locator: ``us://usc/release/pl{congress}-{num}/title{N}.xml`` (the
reserved namespace from :func:`lawvm.us_federal.sources.reserved_usc_release_point_locator`).

Per AGENTS.md §0/§1.8/§1.10, every skip/filter/error is a typed receipt:
unarchived titles, missing zip members, HTTP errors, and byte-identical
re-imports all surface in ``ReleaseImportReport.skipped_entries`` rather than
disappearing silently. Acquisition lane only — no replay authority here.

Runnable without the global CLI::

    python -m lawvm.us_federal.import_release --congress 113 --pl 100 --title 10
    python -m lawvm.us_federal.import_release --congress 113 --pl 100 --all-titles
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from lawvm.us_federal.sources import (
    content_digest,
    open_us_federal_import_farchive,
    reserved_usc_release_point_locator,
    resolve_us_federal_farchive_path,
)

# OLRC release-point base URL (note: OLRC serves http://, archived as such).
OLRC_RELEASE_POINT_URL = (
    "http://uscode.house.gov/download/releasepoints/us/pl/"
    "{congress}/{pl_number}/xml_usc{title:02d}@{congress}-{pl_number}.zip"
)

# Wayback Machine raw-bytes (id_ modifier) URL form.
WAYBACK_URL = "https://web.archive.org/web/{timestamp}id_/{url}"

# Default Wayback timestamp: year-level snapshot picks the most recent capture.
DEFAULT_WAYBACK_TIMESTAMP = "2025"

# USLM namespace (USLM 2.0.x; same as PLAW).
USLM_NS = "http://schemas.gpo.gov/xml/uslm"

# USC title bounds (54 titles; title 53 is reserved but rarely has content).
USC_TITLE_MIN = 1
USC_TITLE_MAX = 54

_HTTP_CHUNK_SIZE = 1024 * 1024
# Release-point title zips can be ~50-200 MB for a big title (Title 42 is
# enormous); spool to disk past this in-memory cap.
_SPOOLED_MAX_BYTES = 64 * 1024 * 1024
_HTTP_TIMEOUT = 240
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) LawVM/0.1 Safari/537.36"
)

# Zip member name pattern. The OLRC release-point zip is named
# ``xml_usc{title:02d}@{congress}-{pl_number}.zip``, but the XML member INSIDE
# the zip is named just ``usc{title:02d}.xml`` (the title is the only varying
# component — the PL identity is on the enclosing zip). Both shapes are
# accepted to tolerate forward-compatible OLRC naming and any future mirror
# that rebundles under the full name::
#
#     usc10.xml                  (observed OLRC convention, post-PL 113-100 zip)
#     xml_usc10@113-100.xml      (speculative future-proof form)
#
# A subdirectory prefix (e.g. ``title10/usc10.xml``) is tolerated via
# ``(?:.*/)?``.
_RELEASE_POINT_MEMBER_RE = re.compile(
    r"^(?:.*/)?(?:xml_)?usc(?P<title>\d{2})(?:@\d+-\d+)?\.xml$"
)


class ReleasePointMemberNotFound(Exception):
    """Raised when a release-point zip has no XML matching the requested title.

    Carries the zip's namelist so triage does not require re-fetching the zip
    (AGENTS.md §1.10): the diagnostic embeds what was actually in the archive.
    """

    def __init__(
        self,
        *,
        requested_title: int,
        available: tuple[str, ...],
        zip_bytes_len: int,
    ) -> None:
        avail_str = ", ".join(available[:8]) or "(empty zip)"
        if len(available) > 8:
            avail_str += f", ... ({len(available)} total)"
        super().__init__(
            f"release-point zip has no XML matching title {requested_title:02d}; "
            f"available members: {avail_str} (zip_bytes={zip_bytes_len:,} B)"
        )
        self.requested_title = requested_title
        self.available = available
        self.zip_bytes_len = zip_bytes_len


@dataclass(frozen=True, slots=True)
class ReleasePointIdentity:
    """Identity of one PL release-point title archive.

    One USLM-USC XML document representing the consolidated USC for one title
    as it stood after one Public Law (release point) took effect.
    """

    congress: int
    pl_number: int
    title: int

    @property
    def locator(self) -> str:
        return reserved_usc_release_point_locator(
            self.congress, self.pl_number, self.title
        )

    @property
    def zip_filename(self) -> str:
        return f"xml_usc{self.title:02d}@{self.congress}-{self.pl_number}.zip"

    @property
    def expected_zip_member_name(self) -> str:
        """Speculative member-name form (``xml_uscNN@c-n.xml``).

        The OLRC zip is named like this externally, but the XML member INSIDE
        the zip is observed to be just ``usc{NN}.xml``. This property documents
        the speculative full form; the actually-matched name is embedded in
        storage metadata as ``zip_member_name``.
        """
        return f"xml_usc{self.title:02d}@{self.congress}-{self.pl_number}.xml"

    @property
    def source_url(self) -> str:
        return OLRC_RELEASE_POINT_URL.format(
            congress=self.congress,
            pl_number=self.pl_number,
            title=self.title,
        )

    @property
    def public_law_label(self) -> str:
        return f"Public Law {self.congress}-{self.pl_number}"


def build_wayback_url(
    url: str, *, timestamp: str = DEFAULT_WAYBACK_TIMESTAMP
) -> str:
    """Construct a Wayback Machine raw-bytes URL (``id_`` modifier).

    The timestamp is a Wayback-style ``YYYY[MMDDHHMMSS]`` string. The default
    ``"2025"`` picks the most recent capture in 2025; pass a stricter timestamp
    to pin a specific snapshot.
    """
    return WAYBACK_URL.format(timestamp=timestamp, url=url)


def fetch_release_point_zip(
    congress: int,
    pl_number: int,
    title: int,
    *,
    timestamp: str = DEFAULT_WAYBACK_TIMESTAMP,
) -> bytes:
    """Fetch a USLM-USC release-point zip from the Wayback Machine.

    Returns the raw zip bytes. Raises:
        urllib.error.HTTPError: Wayback returned an HTTP error (404 for
            unarchived release points, 503 for upstream server errors).
        urllib.error.URLError: Network/DNS failure.
        zipfile.BadZipFile: The archived bytes are not a valid zip.
    """
    identity = ReleasePointIdentity(
        congress=congress, pl_number=pl_number, title=title
    )
    url = build_wayback_url(identity.source_url, timestamp=timestamp)
    with _http_stream(url) as fp:
        data = fp.read()
    # Validate the bytes ARE a zip before returning — fail loud here rather
    # than deep in extract_release_point_xml (AGENTS.md §1.10).
    if not _looks_like_zip(data):
        raise zipfile.BadZipFile(
            f"Wayback returned non-zip bytes for {identity.zip_filename} "
            f"({len(data):,} B); possible Wayback HTML error page"
        )
    return data


def _looks_like_zip(data: bytes) -> bool:
    """Cheap sniff for a zip: PK\\x03\\x04 magic at offset 0."""
    return len(data) >= 4 and data[:2] == b"PK" and data[2:3] in (b"\x03", b"\x05", b"\x07")


@contextmanager
def _http_stream(url: str) -> Iterator[Any]:
    """Stream an HTTPS URL into a spooled temporary file.

    Surfaces ``urllib.error.HTTPError`` to the caller for typed handling (404
    for unarchived release points is not swallowed into a silent drop).
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 (https only)
        with tempfile.SpooledTemporaryFile(
            max_size=_SPOOLED_MAX_BYTES, mode="w+b"
        ) as tmp:
            while True:
                chunk = resp.read(_HTTP_CHUNK_SIZE)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp.seek(0)
            yield tmp


def extract_release_point_xml(zip_bytes: bytes, title: int) -> bytes:
    """Extract the USLM XML for the given title from the release-point zip.

    The zip may contain multiple XML files (one per USC title that the PL
    touched). Returns the bytes of the XML whose name matches the OLRC member
    convention for the requested title (:data:`_RELEASE_POINT_MEMBER_RE`).
    If multiple entries match (should not happen but is structurally possible),
    the first lexically-ordered match wins.

    Raises:
        ReleasePointMemberNotFound: No XML matching the requested title was
            found in the zip; the available member names are embedded.
        zipfile.BadZipFile: The bytes are not a valid zip.
    """
    xml_bytes, _member_name = _extract_release_point_xml_with_name(
        zip_bytes, title
    )
    return xml_bytes


def _extract_release_point_xml_with_name(
    zip_bytes: bytes, title: int
) -> tuple[bytes, str]:
    """Like :func:`extract_release_point_xml` but also returns the matched
    member name from the zip (for honest provenance metadata).

    The OLRC naming convention observed in production is ``usc{NN}.xml`` (no
    PL suffix — the enclosing zip carries the PL identity). The matcher
    additionally tolerates the speculative ``xml_usc{NN}@{c}-{n}.xml`` form
    and a subdirectory prefix.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        matching: list[tuple[str, zipfile.ZipInfo]] = []
        all_member_names: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            all_member_names.append(name)
            m = _RELEASE_POINT_MEMBER_RE.match(name)
            if m is None:
                continue
            if int(m.group("title")) == title:
                matching.append((name, info))

        if not matching:
            # Surface every member the zip actually contained — the triager
            # should not need to re-fetch to see what OLRC bundled.
            raise ReleasePointMemberNotFound(
                requested_title=title,
                available=tuple(sorted(all_member_names)),
                zip_bytes_len=len(zip_bytes),
            )

        # Deterministic order: lexical by member name. Multiple matches for the
        # same title is a structural oddity — the chosen name still ends up in
        # the storage metadata so the audited shape stays inspectable.
        matching.sort(key=lambda pair: pair[0])
        chosen_name, _ = matching[0]
        return zf.read(chosen_name), chosen_name


# ---------------------------------------------------------------------------
# Import + report
# ---------------------------------------------------------------------------


@dataclass
class ReleaseImportReport:
    """Aggregated result from one or more release-point import operations."""

    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    bytes_stored: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)
    imported_locators: list[str] = field(default_factory=list)


def _record_skip(
    report: ReleaseImportReport,
    *,
    rule_id: str,
    family: str,
    reason: str,
    source_label: str,
    detail: dict[str, str] | None = None,
    locator: str | None = None,
) -> None:
    record: dict[str, Any] = {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": family,
        "reason": reason,
        "source": source_label,
    }
    if locator:
        record["locator"] = locator
    if detail:
        record.update(detail)
    report.skipped_entries.append(record)


def import_release_point_zip_bytes(
    congress: int,
    pl_number: int,
    title: int,
    zip_bytes: bytes,
    farchive: Any,
    *,
    skip_existing: bool = False,
    dry_run: bool = False,
    source_label: str = "",
) -> ReleaseImportReport:
    """Store one release-point XML (from already-fetched zip bytes) in farchive.

    Accepts raw zip bytes (so tests can drive the storage path without a
    network fetch). ``source_label`` is the provenance string attached to the
    receipt (e.g. the URL or local path the bytes came from).
    """
    identity = ReleasePointIdentity(
        congress=congress, pl_number=pl_number, title=title
    )
    report = ReleaseImportReport(sources=[source_label or identity.zip_filename])
    report.total_scanned += 1
    label = source_label or identity.zip_filename

    try:
        xml_bytes, matched_member_name = _extract_release_point_xml_with_name(
            zip_bytes, title
        )
    except ReleasePointMemberNotFound as exc:
        report.total_errors += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_release_point_member_not_found",
            family="source_pathology",
            reason=str(exc),
            source_label=label,
            locator=identity.locator,
            detail={
                "requested_title": str(title),
                "available_members": ",".join(exc.available[:16]),
                "exception_type": type(exc).__name__,
            },
        )
        return report
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        report.total_errors += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_release_point_zip_unreadable",
            family="transport_cleanup",
            reason=(
                f"release-point zip unreadable ({type(exc).__name__}: {exc}); "
                "title absent from the archive as a visible acquisition gap"
            ),
            source_label=label,
            locator=identity.locator,
            detail={"exception_type": type(exc).__name__},
        )
        return report

    digest = content_digest(xml_bytes)
    current = farchive.resolve(identity.locator)
    if current is not None and current.digest == digest and skip_existing:
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_release_point_existing_content_skipped",
            family="transport_cleanup",
            reason="archive already contains identical content and skip_existing was enabled",
            source_label=label,
            locator=identity.locator,
            detail={"digest": digest},
        )
        return report

    if current is not None and current.digest != digest:
        print(
            f"WARNING: {identity.locator} changed: "
            f"{current.digest[:12]}.. -> {digest[:12]}..",
            file=sys.stderr,
        )

    report.bytes_raw += len(xml_bytes)
    if dry_run:
        report.total_imported += 1
        report.bytes_stored += len(xml_bytes)
        report.imported_locators.append(identity.locator)
        return report

    metadata = {
        "source_url": identity.source_url,
        "acquisition_source": label,
        "congress": str(identity.congress),
        "pl_number": str(identity.pl_number),
        "title": str(identity.title),
        "public_law": identity.public_law_label,
        # The actual matched zip member name (e.g. "usc10.xml" — observed form).
        # Honest about what OLRC actually packaged, not what the URL pattern
        # would suggest (xml_uscNN@c-n.xml). AGENTS.md §1.10: name what was
        # actually extracted so triage does not require re-fetching the zip.
        "zip_member_name": matched_member_name,
        "zip_filename": identity.zip_filename,
        "sha256": digest,
        "acquisition_channel": "usc_release_point_wayback",
    }

    farchive.store(
        identity.locator,
        xml_bytes,
        storage_class="xml",
        metadata=metadata,
    )
    report.total_imported += 1
    report.bytes_stored += len(xml_bytes)
    report.imported_locators.append(identity.locator)
    return report


def import_release_point(
    archive_path: Path | None,
    congress: int,
    pl_number: int,
    title: int,
    *,
    timestamp: str = DEFAULT_WAYBACK_TIMESTAMP,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> ReleaseImportReport:
    """Fetch, extract, and store one USLM-USC release-point XML in the farchive.

    Returns the canonical locator: ``us://usc/release/pl{congress}-{num}/title{N}.xml``.

    HTTP fetch failures (404 unarchived, 503 upstream, etc.) are typed skips
    with the HTTP status embedded — never a silent drop.
    """
    identity = ReleasePointIdentity(
        congress=congress, pl_number=pl_number, title=title
    )
    report = ReleaseImportReport(sources=[identity.zip_filename])
    report.total_scanned += 1
    label = identity.zip_filename

    try:
        zip_bytes = fetch_release_point_zip(
            congress, pl_number, title, timestamp=timestamp
        )
    except urllib.error.HTTPError as exc:
        report.total_errors += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_release_point_http_error",
            family="source_pathology",
            reason=(
                f"Wayback fetch failed: HTTP {exc.code} {exc.reason} "
                f"({identity.zip_filename})"
            ),
            source_label=label,
            locator=identity.locator,
            detail={
                "http_status": str(exc.code),
                "http_reason": str(exc.reason),
                "url": build_wayback_url(identity.source_url, timestamp=timestamp),
                "exception_type": type(exc).__name__,
            },
        )
        return report
    except urllib.error.URLError as exc:
        report.total_errors += 1
        report.total_skipped += 1
        _record_skip(
            report,
            rule_id="us_release_point_network_error",
            family="transport_cleanup",
            reason=(
                f"Wayback fetch network error ({type(exc).__name__}): {exc.reason} "
                f"({identity.zip_filename})"
            ),
            source_label=label,
            locator=identity.locator,
            detail={
                "url": build_wayback_url(identity.source_url, timestamp=timestamp),
                "exception_type": type(exc).__name__,
            },
        )
        return report

    archive = open_us_federal_import_farchive(archive_path, dry_run=dry_run)
    try:
        sub_report = import_release_point_zip_bytes(
            congress,
            pl_number,
            title,
            zip_bytes,
            archive,
            skip_existing=skip_existing,
            dry_run=dry_run,
            source_label=identity.source_url,
        )
    finally:
        archive.close()

    report.total_scanned = sub_report.total_scanned
    report.total_imported = sub_report.total_imported
    report.total_skipped = sub_report.total_skipped
    report.total_errors = sub_report.total_errors
    report.bytes_raw = sub_report.bytes_raw
    report.bytes_stored = sub_report.bytes_stored
    report.skipped_entries.extend(sub_report.skipped_entries)
    report.imported_locators.extend(sub_report.imported_locators)
    return report


def import_release_point_titles(
    archive_path: Path | None,
    congress: int,
    pl_number: int,
    *,
    titles: list[int] | None = None,
    timestamp: str = DEFAULT_WAYBACK_TIMESTAMP,
    skip_existing: bool = False,
    dry_run: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> ReleaseImportReport:
    """Import several titles for one release point (PL).

    When ``titles`` is None, iterates USC titles 1..54 (the canonical set) —
    Wayback returns 404 for titles the PL did not touch; each 404 is a typed
    skip (not a silent drop). Use ``titles`` to restrict.
    """
    if titles is None:
        titles = list(range(USC_TITLE_MIN, USC_TITLE_MAX + 1))

    overall = ReleaseImportReport()
    overall.sources.append(
        f"PL {congress}-{pl_number} ({len(titles)} titles)"
    )
    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    for i, title in enumerate(titles):
        if progress is not None:
            progress(i, len(titles))
        print(
            f"  Importing PL {congress}-{pl_number} title {title}...",
            file=sys.stderr,
        )
        sub = import_release_point(
            archive_path,
            congress,
            pl_number,
            title,
            timestamp=timestamp,
            skip_existing=skip_existing,
            dry_run=dry_run,
        )
        overall.total_scanned += sub.total_scanned
        overall.total_imported += sub.total_imported
        overall.total_skipped += sub.total_skipped
        overall.total_errors += sub.total_errors
        overall.bytes_raw += sub.bytes_raw
        overall.bytes_stored += sub.bytes_stored
        overall.skipped_entries.extend(sub.skipped_entries)
        overall.imported_locators.extend(sub.imported_locators)
    if progress is not None:
        progress(len(titles), len(titles))
    return overall


# ---------------------------------------------------------------------------
# CLI shim (also runnable as ``python -m lawvm.us_federal.import_release``)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import U.S. Code USLM release-point XML (one Public Law × one "
            "USC title) from the OLRC release-point zip via the Wayback "
            "Machine into the canonical U.S. farchive."
        ),
    )
    parser.add_argument(
        "--congress", type=int, required=True, help="Congress number (e.g. 113)"
    )
    parser.add_argument(
        "--pl", type=int, required=True, dest="pl_number",
        help="Public Law number within that Congress (e.g. 100)",
    )
    parser.add_argument(
        "--title", type=int, default=None,
        help="USC title number (e.g. 10). Required unless --all-titles is set.",
    )
    parser.add_argument(
        "--all-titles", action="store_true", dest="all_titles",
        help="Fetch every USC title (1..54) for this PL; 404s are typed skips.",
    )
    parser.add_argument(
        "--timestamp", default=DEFAULT_WAYBACK_TIMESTAMP,
        help=(
            f"Wayback timestamp (YYYY[MMDDHHMMSS]); default "
            f"'{DEFAULT_WAYBACK_TIMESTAMP}' picks the most recent capture."
        ),
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

    if not args.all_titles and args.title is None:
        print(
            "error: --title is required unless --all-titles is set",
            file=sys.stderr,
        )
        return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        dest_path = args.dest
        print(f"Opening farchive: {dest_path}", file=sys.stderr)

    if args.all_titles:
        report = import_release_point_titles(
            dest_path,
            args.congress,
            args.pl_number,
            titles=None,
            timestamp=args.timestamp,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )
    else:
        report = import_release_point(
            dest_path,
            args.congress,
            args.pl_number,
            args.title,
            timestamp=args.timestamp,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )

    print("\nUSC release-point import complete:")
    print(f"  Total scanned:  {report.total_scanned}")
    print(f"  Total imported: {report.total_imported}")
    print(f"  Total skipped:  {report.total_skipped}")
    print(f"  Total errors:   {report.total_errors}")
    if report.bytes_raw:
        ratio = report.bytes_stored / report.bytes_raw if report.bytes_raw else 0.0
        print(f"  Raw bytes:      {report.bytes_raw:,}")
        print(f"  Stored bytes:   {report.bytes_stored:,}")
        print(f"  Ratio:          {ratio:.1%}")
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
