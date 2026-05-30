"""import_zip.py - Bulk import Finlex ZIP files into farchive.

Imports statute source XMLs and consolidated oracle XMLs (including media)
from the Finlex Open Data ZIP distribution into a content-addressed farchive DB.
Consolidated imports write canonical versioned ``finlex://sd-cons/...@YYYYNNNN``
locators only.

Handles large ZIPs without loading all content into memory at once. ZIP
sources may be local files or HTTPS URLs; remote archives are streamed into a
seekable temporary file before iteration.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sys
import tempfile
import urllib.request
import zipfile
from typing import Any, Callable

from lawvm.corpus_store import akn_path_to_url
from lawvm.finland.consolidated_artifacts import (
    build_canonical_consolidated_locator,
    extract_consolidated_xml_identity,
)

_DEFAULT_FARCHIVE = "data/finlex.farchive"
_DEFAULT_BATCH_SIZE = 2000
_PROGRESS_INTERVAL = 1000
_HTTP_CHUNK_SIZE = 1024 * 1024
_SPOOLED_MAX_BYTES = 64 * 1024 * 1024
_AKN_CONSOL_PATH_RE = re.compile(
    r"akn/fi/act/statute-consolidated/(\d{4}/[^/]+)/([^/@]+)@([^/]*)/(.+)"
)
# Corrigenda in the consolidated ZIP have no lang@version segment.
_AKN_CONSOL_CORRIGENDUM_PATH_RE = re.compile(
    r"akn/fi/act/statute-consolidated/(\d{4}/[^/]+)/media/corrigenda/([^/]+\.pdf)"
)
_CORRIGENDUM_LANG: dict[str, str] = {"sk": "fin", "fs": "swe"}


@dataclass
class ImportReport:
    """Aggregated result from one or more import operations."""

    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    bytes_stored: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)


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
    record = {
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
def _open_zip_source(zip_source: Path | str):
    """Yield a seekable binary file object for a ZIP source."""
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
    with urllib.request.urlopen(req, timeout=120) as resp:
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


def _metadata_for_zip_entry(
    *,
    source_url: str,
    source_surface: str,
    entry_name: str,
    entry_mtime: datetime | None,
    pit_version: str | None = None,
    path_pit_version: str | None = None,
) -> dict[str, str]:
    meta: dict[str, str] = {
        "source_url": source_url,
        "source_surface": source_surface,
        "entry_name": entry_name,
    }
    if entry_mtime is not None:
        meta["zip_entry_mtime"] = entry_mtime.isoformat()
    if pit_version:
        meta["pit_version"] = pit_version
    if path_pit_version:
        meta["path_pit_version"] = path_pit_version
    return meta


def _store_zip_entry(
    *,
    farchive: Any,
    locator: str,
    data: bytes,
    observed_at: datetime | None,
    storage_class: str,
    metadata: dict[str, str],
    source_label: str,
    zip_entry_name: str,
    seen_locators: dict[str, str],
    skip_existing: bool,
    dry_run: bool,
    report: ImportReport,
) -> None:
    previous_entry = seen_locators.get(locator)
    if previous_entry is not None and previous_entry != zip_entry_name:
        print(
            f"WARNING: duplicate logical locator {locator} in {source_label}: "
            f"{previous_entry} -> {zip_entry_name}; skipping later entry",
            file=sys.stderr,
        )
        report.total_skipped += 1
        _record_import_skip(
            report,
            rule_id="finlex_import_duplicate_logical_locator",
            family="source_pathology",
            reason="duplicate logical locator in ZIP; later entry skipped",
            source_label=source_label,
            zip_entry_name=zip_entry_name,
            locator=locator,
            detail={"previous_entry_name": previous_entry},
        )
        return

    seen_locators.setdefault(locator, zip_entry_name)

    digest = hashlib.sha256(data).hexdigest()
    current = farchive.resolve(locator)
    if current is not None and current.digest == digest and skip_existing:
        report.total_skipped += 1
        _record_import_skip(
            report,
            rule_id="finlex_import_existing_content_skipped",
            family="transport_cleanup",
            reason="archive already contains identical content and skip_existing was enabled",
            source_label=source_label,
            zip_entry_name=zip_entry_name,
            locator=locator,
            detail={"digest": digest},
        )
        return

    if current is not None and current.digest != digest:
        print(
            f"WARNING: {locator} changed in {source_label}: "
            f"{current.digest[:12]}.. -> {digest[:12]}..",
            file=sys.stderr,
        )

    if dry_run:
        report.total_imported += 1
        report.bytes_raw += len(data)
        report.bytes_stored += len(data)
        return

    store_kwargs: dict[str, Any] = {
        "storage_class": storage_class,
        "metadata": metadata,
    }
    if observed_at is not None:
        store_kwargs["observed_at"] = observed_at
    farchive.store(locator, data, **store_kwargs)
    report.total_imported += 1
    report.bytes_raw += len(data)
    report.bytes_stored += len(data)


def _parse_akn_consolidated_path(akn_path: str) -> tuple[str, str, str, str] | None:
    """Return (sid, lang, path_version, rest) for an AKN consolidated path."""
    match = _AKN_CONSOL_PATH_RE.search(akn_path)
    if match is None:
        return None
    sid, lang, version, rest = match.groups()
    return sid, lang, version, rest


def import_statute_zip(
    zip_path: Path | str,
    farchive: Any,
    skip_existing: bool = False,
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    progress: Callable[[int], None] | None = None,
) -> ImportReport:
    """Import source XMLs from a statute ZIP into farchive."""
    del batch_size
    report = ImportReport(sources=[str(zip_path)])
    zip_label = _zip_source_label(zip_path)
    source_url = str(zip_path)
    observed_at = None
    seen_locators: dict[str, str] = {}

    with _open_zip_source(zip_path) as zip_fp, zipfile.ZipFile(zip_fp, "r") as zf:
        names = zf.namelist()
        total_names = len(names)
        print(f"  statute ZIP: {total_names:,} entries in {zip_label}", file=sys.stderr)

        for i, name in enumerate(names):
            report.total_scanned += 1

            info = zf.getinfo(name)
            if info.file_size == 0:
                continue
            if "/fin@" not in name and "/fin/" not in name:
                continue
            if name.endswith("/main.pdf"):
                continue
            if "pdf-wrapper" in name:
                continue
            if "statute-consolidated" in name:
                continue

            locator = akn_path_to_url(name)
            if locator is None:
                continue

            try:
                data = zf.read(name)
            except Exception as exc:
                print(f"  ERROR reading {name}: {exc}", file=sys.stderr)
                report.total_errors += 1
                continue

            meta = _metadata_for_zip_entry(
                source_url=source_url,
                source_surface="statute-zip",
                entry_name=name,
                entry_mtime=_zip_entry_mtime(info),
            )
            _store_zip_entry(
                farchive=farchive,
                locator=locator,
                data=data,
                observed_at=observed_at,
                storage_class="xml",
                metadata=meta,
                source_label=zip_label,
                zip_entry_name=name,
                seen_locators=seen_locators,
                skip_existing=skip_existing,
                dry_run=dry_run,
                report=report,
            )

            if progress and (i + 1) % _PROGRESS_INTERVAL == 0:
                progress(i + 1)

    return report


def import_consolidated_zip(
    zip_path: Path | str,
    farchive: Any,
    skip_existing: bool = False,
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    progress: Callable[[int], None] | None = None,
) -> ImportReport:
    """Import oracle XMLs and media into canonical versioned sd-cons locators."""
    del batch_size
    report = ImportReport(sources=[str(zip_path)])
    zip_label = _zip_source_label(zip_path)
    source_url = str(zip_path)
    observed_at = None
    seen_locators: dict[str, str] = {}
    pit_versions_by_family: dict[tuple[str, str, str], str] = {}

    with _open_zip_source(zip_path) as zip_fp, zipfile.ZipFile(zip_fp, "r") as zf:
        names = zf.namelist()
        total_names = len(names)
        print(f"  consolidated ZIP: {total_names:,} entries in {zip_label}", file=sys.stderr)

        for name in names:
            if "statute-consolidated" not in name or not name.endswith("/main.xml"):
                continue
            parsed = _parse_akn_consolidated_path(name)
            if parsed is None:
                continue
            sid, lang, path_version, _rest = parsed
            family_key = (sid, lang, path_version)
            try:
                data = zf.read(name)
            except Exception as exc:
                print(f"  ERROR reading {name}: {exc}", file=sys.stderr)
                report.total_errors += 1
                continue
            pit_version = extract_consolidated_xml_identity(data).embedded_version_tag
            if not pit_version:
                continue
            prev = pit_versions_by_family.get(family_key)
            if prev is None:
                pit_versions_by_family[family_key] = pit_version
            elif prev != pit_version:
                print(
                    f"WARNING: {name} XML PIT {pit_version} disagrees with prior "
                    f"family PIT {prev}; keeping existing family identity",
                    file=sys.stderr,
                )

        for i, name in enumerate(names):
            report.total_scanned += 1

            info = zf.getinfo(name)
            if info.file_size == 0:
                continue
            if "/fin@" not in name and "/fin/" not in name:
                continue
            if "statute-consolidated" not in name:
                continue
            if name.endswith("/main.pdf"):
                continue
            if "pdf-wrapper" in name:
                continue

            parsed = _parse_akn_consolidated_path(name)
            if parsed is None:
                continue
            sid, lang, path_version_raw, rest = parsed
            path_pit_version = path_version_raw or None
            family_key = (sid, lang, path_version_raw)
            pit_version = pit_versions_by_family.get(family_key) or path_pit_version
            if not pit_version:
                print(
                    f"  ERROR {name}: no consolidated PIT version available from path or XML",
                    file=sys.stderr,
                )
                report.total_errors += 1
                continue
            if path_pit_version is not None and path_pit_version != pit_version:
                print(
                    f"WARNING: {name} path PIT {path_pit_version} disagrees with XML PIT {pit_version}; "
                    "using family XML-derived version",
                    file=sys.stderr,
                )
            locator = build_canonical_consolidated_locator(
                sid=sid,
                lang=lang,
                version_tag=pit_version,
                rest=rest,
            )

            suffix = Path(name).suffix.lower()
            storage_class = "gif" if suffix == ".gif" else "xml"

            try:
                data = zf.read(name)
            except Exception as exc:
                print(f"  ERROR reading {name}: {exc}", file=sys.stderr)
                report.total_errors += 1
                continue

            meta = _metadata_for_zip_entry(
                source_url=source_url,
                source_surface="statute-consolidated-zip",
                entry_name=name,
                entry_mtime=_zip_entry_mtime(info),
                pit_version=pit_version,
                path_pit_version=path_pit_version,
            )
            _store_zip_entry(
                farchive=farchive,
                locator=locator,
                data=data,
                observed_at=observed_at,
                storage_class=storage_class,
                metadata=meta,
                source_label=zip_label,
                zip_entry_name=name,
                seen_locators=seen_locators,
                skip_existing=skip_existing,
                dry_run=dry_run,
                report=report,
            )

            if progress and (i + 1) % _PROGRESS_INTERVAL == 0:
                progress(i + 1)

        # Second pass B: version-agnostic corrigendum PDFs.
        # These live at akn/fi/act/statute-consolidated/{sid}/media/corrigenda/{file}
        # with no lang@version segment — skipped by the main loop's language guard.
        # We look up the PIT version from pit_versions_by_family (populated above).
        for name in names:
            if "statute-consolidated" not in name:
                continue
            m = _AKN_CONSOL_CORRIGENDUM_PATH_RE.search(name)
            if m is None:
                continue
            sid, filename = m.groups()
            lang = _CORRIGENDUM_LANG.get(filename[:2].lower())
            if lang is None:
                continue  # unknown prefix (not sk/fs), skip

            report.total_scanned += 1
            info = zf.getinfo(name)
            if info.file_size == 0:
                continue

            # Find the PIT version for this (sid, lang) family.
            pit_version = None
            for (fam_sid, fam_lang, _fam_ver), ver in pit_versions_by_family.items():
                if fam_sid == sid and fam_lang == lang:
                    pit_version = ver
                    break
            if not pit_version:
                print(
                    f"  WARNING {name}: no PIT version found for {sid}/{lang}; skipping corrigendum",
                    file=sys.stderr,
                )
                report.total_skipped += 1
                _record_import_skip(
                    report,
                    rule_id="finlex_import_corrigendum_pit_missing",
                    family="source_pathology",
                    reason="version-agnostic consolidated corrigendum had no family PIT version",
                    source_label=zip_label,
                    zip_entry_name=name,
                    detail={"sid": sid, "lang": lang, "filename": filename},
                )
                continue

            locator = build_canonical_consolidated_locator(
                sid=sid,
                lang=lang,
                version_tag=pit_version,
                rest=f"media/corrigenda/{filename}",
            )

            try:
                data = zf.read(name)
            except Exception as exc:
                print(f"  ERROR reading {name}: {exc}", file=sys.stderr)
                report.total_errors += 1
                continue

            meta = _metadata_for_zip_entry(
                source_url=source_url,
                source_surface="statute-consolidated-zip",
                entry_name=name,
                entry_mtime=_zip_entry_mtime(info),
                pit_version=pit_version,
            )
            _store_zip_entry(
                farchive=farchive,
                locator=locator,
                data=data,
                observed_at=observed_at,
                storage_class="pdf",
                metadata=meta,
                source_label=zip_label,
                zip_entry_name=name,
                seen_locators=seen_locators,
                skip_existing=skip_existing,
                dry_run=dry_run,
                report=report,
            )

    return report


def main(args: object) -> None:
    """CLI entry point for lawvm import-zip."""
    from farchive import Farchive

    _statute_zip_raw = getattr(args, "statute_zip", None)
    _consolidated_zip_raw = getattr(args, "consolidated_zip", None)
    statute_zip = _statute_zip_raw if _statute_zip_raw else None
    consolidated_zip = _consolidated_zip_raw if _consolidated_zip_raw else None
    dest = Path(getattr(args, "dest", None) or _DEFAULT_FARCHIVE)
    skip_existing: bool = getattr(args, "skip_existing", False)
    dry_run: bool = getattr(args, "dry_run", False)
    batch_size: int = getattr(args, "batch_size", _DEFAULT_BATCH_SIZE)

    if statute_zip is None and consolidated_zip is None:
        print(
            "error: at least one of --statute-zip or --consolidated-zip is required",
            file=sys.stderr,
        )
        sys.exit(1)

    if statute_zip is not None and not _is_http_url(statute_zip):
        statute_zip_path = Path(statute_zip)
        if not statute_zip_path.exists():
            print(f"error: statute ZIP not found: {statute_zip_path}", file=sys.stderr)
            sys.exit(1)

    if consolidated_zip is not None and not _is_http_url(consolidated_zip):
        consolidated_zip_path = Path(consolidated_zip)
        if not consolidated_zip_path.exists():
            print(f"error: consolidated ZIP not found: {consolidated_zip_path}", file=sys.stderr)
            sys.exit(1)

    print(f"Opening farchive: {dest}", file=sys.stderr)
    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)
    archive = Farchive(dest)

    overall = ImportReport()

    try:
        if statute_zip is not None:
            print(f"\nImporting statute ZIP: {statute_zip}", file=sys.stderr)
            if skip_existing:
                print("  (--skip-existing: checking farchive before reading each entry)", file=sys.stderr)

            def _statute_progress(done: int) -> None:
                print(f"  scanned {done:,} entries...", file=sys.stderr)

            report = import_statute_zip(
                statute_zip,
                archive,
                skip_existing=skip_existing,
                dry_run=dry_run,
                batch_size=batch_size,
                progress=_statute_progress,
            )
            overall.total_scanned += report.total_scanned
            overall.total_imported += report.total_imported
            overall.total_skipped += report.total_skipped
            overall.total_errors += report.total_errors
            overall.bytes_raw += report.bytes_raw
            overall.bytes_stored += report.bytes_stored
            overall.skipped_entries.extend(report.skipped_entries)
            print(
                f"  statute: scanned={report.total_scanned:,}  "
                f"imported={report.total_imported:,}  "
                f"skipped={report.total_skipped:,}  "
                f"errors={report.total_errors:,}",
                file=sys.stderr,
            )

        if consolidated_zip is not None:
            print(f"\nImporting consolidated ZIP: {consolidated_zip}", file=sys.stderr)
            if skip_existing:
                print("  (--skip-existing: checking farchive before reading each entry)", file=sys.stderr)

            def _cons_progress(done: int) -> None:
                print(f"  scanned {done:,} entries...", file=sys.stderr)

            report = import_consolidated_zip(
                consolidated_zip,
                archive,
                skip_existing=skip_existing,
                dry_run=dry_run,
                batch_size=batch_size,
                progress=_cons_progress,
            )
            overall.total_scanned += report.total_scanned
            overall.total_imported += report.total_imported
            overall.total_skipped += report.total_skipped
            overall.total_errors += report.total_errors
            overall.bytes_raw += report.bytes_raw
            overall.bytes_stored += report.bytes_stored
            overall.skipped_entries.extend(report.skipped_entries)
            print(
                f"  consolidated: scanned={report.total_scanned:,}  "
                f"imported={report.total_imported:,}  "
                f"skipped={report.total_skipped:,}  "
                f"errors={report.total_errors:,}",
                file=sys.stderr,
            )
    finally:
        archive.close()

    print("\nImport complete:", file=sys.stderr)
    print(f"  Total scanned:  {overall.total_scanned:,}")
    print(f"  Total imported: {overall.total_imported:,}")
    print(f"  Total skipped:  {overall.total_skipped:,}")
    print(f"  Total errors:   {overall.total_errors:,}")
    if overall.bytes_raw:
        ratio = overall.bytes_stored / overall.bytes_raw
        print(f"  Raw bytes:      {overall.bytes_raw:,}")
        print(f"  Stored bytes:   {overall.bytes_stored:,}")
        print(f"  Ratio:          {ratio:.1%}")

    if overall.total_errors:
        sys.exit(1)
