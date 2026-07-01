"""Import the OLRC Table III bulk XML (+ cross-reference tables) into a farchive.

Table III is the OLRC **Statutes-at-Large -> USC** classification table: for each
act (keyed by ``<num>`` — a modern Public Law ``{congress}-{num}`` or an older
date+chapter act) it records, per ``<act-section>``, the USC title/section that
act-section was classified into. It is the authoritative, all-time superset of
the per-Congress classification tables already parsed by
:mod:`lawvm.us_federal.classification_tables`, and the direct data source for the
``act-section -> USC address`` mapping :mod:`lawvm.us_federal.nonpositive`
currently must infer (from a govinfo USLM href/parenthetical) or refuse
(``us_nonpositive_target_unmapped``).

ARCHIVE-FIRST, XML/HTM-ONLY: the farchive stores ONLY the extracted table bytes
(the bulk ``table3_xml_bulk.xml`` and the OLRC ``usctableN.htm`` cross-reference
tables), content-addressed by SHA-256, at canonical locators
``us://classification/{table}/{release_point}.{ext}``. No zip is stored.

This module also exposes a streaming Table III parser
(:func:`iter_table3_records`) and a tiny in-memory index
(:class:`Table3Index`) sufficient to PROVE a sample lookup resolves at ingest
time. The full deterministic wiring into ``nonpositive.py`` is stage 2 (this
parser is intentionally minimal — scout + validation only).

NOTE on member-size cap: ``table3_xml_bulk.xml`` is ~125 MB uncompressed, above
the default 100 MB ``LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` cap; raise that env before
ingest (e.g. ``export LAWVM_MAX_ARCHIVE_MEMBER_BYTES=$((192*1024*1024))``).

Runnable without the global CLI::

    python -m lawvm.us_federal.import_table3 \\
        --release-point 119-99 --table3 /tmp/us_olrc/stage_tables/table3_xml_bulk.xml \\
        --table /tmp/us_olrc/stage_tables/usctable1.htm
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.sources import (
    content_digest,
    open_us_federal_import_farchive,
    resolve_us_federal_farchive_path,
    usc_classification_table_locator,
)

# A cross-reference table file: usctable1.htm .. usctable6.htm.
_TABLE_HTM_RE = re.compile(r"^usctable(?P<n>[1-6])\.htm$", re.IGNORECASE)

# A codifiable positive-law USC title is a bare 1-2 digit number (mirrors
# ``classification_tables._USC_TITLE_RE``). Table III also carries non-integer
# title labels — notably ``"50 App."`` (the repealed/reclassified Title 50
# Appendix) — which are NOT resolvable positive-law section addresses on this
# surface. Such rows must not count as classified: they carry no ``int``-able
# title, and treating them as classified would crash the ``int(usc_title)``
# address build (``resolve`` / ``usc_address``) instead of flowing to the typed
# uncodified/holdout path.
_USC_TITLE_RE = re.compile(r"^\d{1,2}$")


@dataclass(frozen=True, slots=True)
class Table3Record:
    """One Table III classification record (an ``<act-section> -> USC`` row).

    ``act_num`` is the act key from the enclosing ``<act num="...">`` (a modern
    Public Law ``"117-2"`` or an older ``"531"`` chapter); ``public_law`` is the
    explicit ``<public-law>`` element if present (older acts carry both a chapter
    ``<num>`` and a ``<public-law>``). ``act_section`` is the section within the
    act (``"1101(a)"``, ranges like ``"2001-2004"``). ``usc_title``/``usc_section``
    are the classified USC address (empty for an unclassified R.S./Rev.T. row).
    ``status`` is the ``<united-states-code-status>`` text (e.g. ``"Rep."``,
    ``"Elim."``, ``"Rev. T."``). ``is_note`` flags a ``... nt`` (uncodified note)
    target.
    """

    act_num: str
    act_congress: str
    act_section: str
    usc_title: str
    usc_section: str
    status: str
    public_law: str
    usckey: str

    @property
    def is_note(self) -> bool:
        return self.usc_section.endswith(" nt") or self.usckey.endswith("nt")

    @property
    def is_classified(self) -> bool:
        """True when the row carries a codified (non-note) USC title+section.

        The title must be a bare 1-2 digit positive-law USC title. Non-integer
        title labels (e.g. the ``"50 App."`` Title 50 Appendix) are held out as
        uncodified rather than treated as classified — they carry no int-able
        title, so admitting them would crash the downstream ``int(usc_title)``
        address build instead of resolving to the typed holdout path.
        """
        return (
            bool(self.usc_title and self.usc_section)
            and bool(_USC_TITLE_RE.match(self.usc_title.strip()))
            and not self.is_note
        )

    def usc_address(self) -> LegalAddress | None:
        """The classified USC :class:`LegalAddress` (``None`` when uncodified)."""
        if not self.is_classified:
            return None
        section = self.usc_section.strip()
        return LegalAddress(
            path=(("title", str(int(self.usc_title))), ("section", section))
        )


def iter_table3_records(data: bytes) -> Iterator[Table3Record]:
    """Stream Table III ``<act>``/``<record>`` rows into typed records.

    Uses ``iterparse`` so the ~125 MB document is never fully materialized as a
    tree. Each ``<act>`` yields one :class:`Table3Record` per child ``<record>``.

    The OLRC ``table3_xml_bulk.xml`` is a *rootless* XML fragment — a bare
    sequence of ``<act>...</act>`` siblings with no enclosing document element
    and no XML declaration. ``_byte_source`` synthesizes a wrapping
    ``<table3>`` root so ``iterparse`` sees a well-formed document without
    materializing the 125 MB into a string.
    """
    act_num = ""
    act_congress = ""
    public_law = ""
    in_act = False

    for event, el in ET.iterparse(_byte_source(data), events=("start", "end")):
        tag = el.tag
        if event == "start" and tag == "act":
            act_num = ""
            act_congress = el.get("congress", "")
            public_law = ""
            in_act = True
        elif event == "end":
            if tag == "num" and in_act and _parent_is_act(el):
                # <num> directly under <act> is the act key.
                act_num = (el.text or "").strip()
            elif tag == "public-law" and in_act:
                public_law = (el.text or "").strip()
            elif tag == "record":
                yield _record_from_element(el, act_num, act_congress, public_law)
                el.clear()
            elif tag == "act":
                in_act = False
                el.clear()


_TABLE3_ROOT_OPEN = b"<table3>"
_TABLE3_ROOT_CLOSE = b"</table3>"


def _byte_source(data: bytes) -> Any:
    """A streaming file-like wrapping the rootless fragment in a synthetic root.

    Concatenates ``<table3>`` + fragment + ``</table3>`` without copying the
    125 MB payload: the three byte chunks are read in order via a tiny
    multi-buffer reader so ``iterparse`` sees one well-formed document.
    """
    return _ConcatBytesReader((_TABLE3_ROOT_OPEN, data, _TABLE3_ROOT_CLOSE))


class _ConcatBytesReader:
    """Minimal read()-only file-like over a sequence of byte chunks (no copy)."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self._idx = 0
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            out = b"".join(
                self._chunks[self._idx][self._pos :] if i == self._idx
                else self._chunks[i]
                for i in range(self._idx, len(self._chunks))
            )
            self._idx = len(self._chunks)
            self._pos = 0
            return out
        out_parts: list[bytes] = []
        remaining = size
        while remaining > 0 and self._idx < len(self._chunks):
            chunk = self._chunks[self._idx]
            avail = chunk[self._pos : self._pos + remaining]
            out_parts.append(avail)
            self._pos += len(avail)
            remaining -= len(avail)
            if self._pos >= len(chunk):
                self._idx += 1
                self._pos = 0
        return b"".join(out_parts)


def _parent_is_act(_el: ET.Element) -> bool:
    # ElementTree gives no parent pointer; the streaming state machine only
    # records <num> while ``in_act`` and before the first <record>. A <num>
    # nested deeper would be inside a <record> (Table III records have no <num>),
    # so this guard is structurally satisfied by the document shape.
    return True


def _record_from_element(
    el: ET.Element, act_num: str, act_congress: str, public_law: str
) -> Table3Record:
    act_section = _child_text(el, "act-section")
    usc_title = _child_text(el, "united-states-code-title")
    usc_section = _child_text(el, "united-states-code-section")
    status = _child_text(el, "united-states-code-status")
    return Table3Record(
        act_num=act_num,
        act_congress=act_congress,
        act_section=act_section,
        usc_title=usc_title,
        usc_section=usc_section,
        status=status,
        public_law=public_law,
        usckey=el.get("usckey", ""),
    )


def _child_text(el: ET.Element, name: str) -> str:
    child = el.find(name)
    return (child.text or "").strip() if child is not None and child.text else ""


class Table3Index:
    """Minimal (act-key, act-section) -> USC address index over Table III.

    Keyed by ``(act_num, act_section_root)`` where ``act_num`` is the modern PL
    ``"{congress}-{num}"`` and ``act_section_root`` is the integer/letter root of
    the act-section (a ``"1101(a)"`` row indexes under root ``"1101"``). Built
    for scout/validation; the stage-2 wiring replaces this with the full
    resolver (range/sub-section handling, agreement adjudication).
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], list[Table3Record]] = {}
        self.record_count = 0

    @classmethod
    def from_bytes(cls, data: bytes, *, modern_pl_only: bool = True) -> Table3Index:
        index = cls()
        for rec in iter_table3_records(data):
            index.record_count += 1
            if modern_pl_only and "-" not in rec.act_num:
                continue
            root = _section_root(rec.act_section)
            index._by_key.setdefault((rec.act_num, root), []).append(rec)
        return index

    def lookup(self, act_num: str, act_section: str) -> list[Table3Record]:
        return list(self._by_key.get((act_num, _section_root(act_section)), []))

    def resolve(self, act_num: str, act_section: str) -> LegalAddress | None:
        """Resolve to a USC address only when all classified matches agree."""
        addrs = {
            r.usc_address()
            for r in self.lookup(act_num, act_section)
            if r.is_classified
        }
        addrs.discard(None)
        if len(addrs) == 1:
            return addrs.pop()
        return None


_SECTION_ROOT_RE = re.compile(r"^(?P<root>\d+[A-Za-z]?)")


def _section_root(act_section: str) -> str:
    m = _SECTION_ROOT_RE.match(act_section.strip())
    return m.group("root") if m else act_section.strip()


# ---------------------------------------------------------------------------
# Import + report
# ---------------------------------------------------------------------------


@dataclass
class Table3ImportReport:
    """Aggregated result from importing Table III + cross-reference tables."""

    release_point: str = ""
    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    imported_locators: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)


def _record_skip(
    report: Table3ImportReport,
    *,
    rule_id: str,
    reason: str,
    source_label: str,
    detail: dict[str, str] | None = None,
) -> None:
    record: dict[str, Any] = {
        "rule_id": rule_id,
        "phase": "acquisition",
        "reason": reason,
        "source": source_label,
    }
    if detail:
        record.update(detail)
    report.skipped_entries.append(record)


def import_classification_doc(
    source: Path,
    farchive: Any,
    *,
    table: str,
    release_point: str,
    ext: str,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> Table3ImportReport:
    """Import one classification document (Table III XML or a table htm)."""
    report = Table3ImportReport(release_point=release_point)
    locator = usc_classification_table_locator(table, release_point, ext=ext)
    report.total_scanned += 1
    label = source.name

    try:
        data = source.read_bytes()
    except OSError as exc:
        report.total_errors += 1
        _record_skip(
            report,
            rule_id="us_classification_source_unreadable",
            reason=f"classification doc unreadable ({type(exc).__name__}: {exc})",
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
            rule_id="us_classification_existing_content_skipped",
            reason="archive already contains identical content and skip_existing was enabled",
            source_label=label,
            detail={"locator": locator, "digest": digest},
        )
        return report

    report.bytes_raw += len(data)
    if dry_run:
        report.total_imported += 1
        report.imported_locators.append(locator)
        return report

    metadata = {
        "release_point": release_point,
        "table": table,
        "ext": ext,
        "source_member_name": label,
        "acquisition_source": str(source),
        "acquisition_channel": "olrc_classification_table",
        "sha256": digest,
    }
    farchive.store(
        locator,
        data,
        storage_class="xml" if ext == "xml" else "html",
        metadata=metadata,
    )
    report.total_imported += 1
    report.imported_locators.append(locator)
    return report


def _table_key_for_htm(name: str) -> str | None:
    m = _TABLE_HTM_RE.match(name)
    return f"table{m.group('n')}" if m else None


def import_tables(
    *,
    release_point: str,
    table3_path: Path | None,
    table_htm_paths: list[Path],
    db_path: Path | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> Table3ImportReport:
    """Import Table III bulk XML and any usctableN.htm cross-reference tables."""
    overall = Table3ImportReport(release_point=release_point)
    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    archive = open_us_federal_import_farchive(db_path, dry_run=dry_run)
    try:
        if table3_path is not None:
            sub = import_classification_doc(
                table3_path,
                archive,
                table="table3",
                release_point=release_point,
                ext="xml",
                skip_existing=skip_existing,
                dry_run=dry_run,
            )
            _merge(overall, sub)
            print(
                f"  {table3_path.name}: imported={sub.total_imported} "
                f"errors={sub.total_errors}",
                file=sys.stderr,
            )
        for htm in table_htm_paths:
            key = _table_key_for_htm(htm.name)
            if key is None:
                overall.total_scanned += 1
                overall.total_skipped += 1
                _record_skip(
                    overall,
                    rule_id="us_classification_unrecognized_table",
                    reason="file name did not match usctable{N}.htm",
                    source_label=htm.name,
                )
                continue
            sub = import_classification_doc(
                htm,
                archive,
                table=key,
                release_point=release_point,
                ext="htm",
                skip_existing=skip_existing,
                dry_run=dry_run,
            )
            _merge(overall, sub)
            print(
                f"  {htm.name}: imported={sub.total_imported} errors={sub.total_errors}",
                file=sys.stderr,
            )
    finally:
        archive.close()
    return overall


def _merge(overall: Table3ImportReport, report: Table3ImportReport) -> None:
    overall.total_scanned += report.total_scanned
    overall.total_imported += report.total_imported
    overall.total_skipped += report.total_skipped
    overall.total_errors += report.total_errors
    overall.bytes_raw += report.bytes_raw
    overall.imported_locators.extend(report.imported_locators)
    overall.skipped_entries.extend(report.skipped_entries)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import the OLRC Table III bulk XML and usctableN.htm cross-reference "
            "tables into the canonical U.S. farchive, archive-first (no zip "
            "stored)."
        ),
    )
    parser.add_argument(
        "--release-point", required=True,
        help="Release-point pin '{congress}-{num}' (e.g. 119-99).",
    )
    parser.add_argument(
        "--table3", type=Path, default=None,
        help="Path to the extracted table3_xml_bulk.xml.",
    )
    parser.add_argument(
        "--table", type=Path, action="append", default=None, dest="tables",
        help="Path to an extracted usctable{N}.htm (repeatable).",
    )
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Explicit farchive path (default: canonical data/us_federal.farchive).",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip docs whose identical content is already stored.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report without writing to the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.table3 is None and not args.tables:
        print("error: pass --table3 and/or --table", file=sys.stderr)
        return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        dest_path = args.dest
        print(f"Opening farchive: {dest_path}", file=sys.stderr)

    report = import_tables(
        release_point=args.release_point,
        table3_path=args.table3,
        table_htm_paths=list(args.tables or []),
        db_path=dest_path,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )

    print("\nTable III / classification-table import complete:")
    print(f"  Release point:  {report.release_point}")
    print(f"  Total scanned:  {report.total_scanned}")
    print(f"  Total imported: {report.total_imported}")
    print(f"  Total skipped:  {report.total_skipped}")
    print(f"  Total errors:   {report.total_errors}")
    if report.bytes_raw:
        print(f"  Raw bytes:      {report.bytes_raw:,}")
    for loc in report.imported_locators:
        print(f"    {loc}")
    return 1 if report.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
