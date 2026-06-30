"""Import U.S. Statutes at Large USLM XML (older public laws) into a farchive.

This is the deep-history sibling of :mod:`lawvm.us_federal.import_plaw`. The
govinfo PLAW bulkdata collection only reaches back to the 113th Congress (2013).
For OLDER public laws GPO publishes the Statutes at Large as keyless USLM, one
large ``<statutesAtLarge>`` document per *volume*::

    https://www.govinfo.gov/content/pkg/STATUTE-{volume}/uslm/STATUTE-{volume}.xml

(``application/xml``, USLM 2.0.x). A Statutes-at-Large *volume* is NOT a
Congress: each two-year Congress spans two consecutive volumes (volume 101 =
100th Congress 1987, ..., volume 126 = 112th Congress 2012, volume 127 = 113th
Congress 2013 — where the PLAW corpus already begins). Each enacted law inside a
volume is a self-contained ``<pLaw>`` element whose ``<meta>`` carries
``<dc:type>`` (Public Law / Private Law), ``<docNumber>`` (the law number within
its Congress), ``<congress>``, and ``<publicPrivate>``.

This importer slices each volume into per-law ``<pLaw>`` units, keeps the public
laws, wraps each slice as a standalone well-formed USLM document, and stores it
at the SAME canonical locator scheme as :mod:`import_plaw` so the two channels
share one corpus::

    us://plaw/{congress}/publ{N}.xml

Provenance is distinguished by the ``acquisition_channel`` metadata value
``statutes_at_large_uslm`` (vs ``plaw_bulkdata``). Per the Prime Directive,
unreachable/unparsable sources and private laws are typed skips, never faked.

Runnable without the global CLI::

    python -m lawvm.us_federal.import_statute 115            # one volume by number
    python -m lawvm.us_federal.import_statute 101-126        # an inclusive range
    python -m lawvm.us_federal.import_statute --dry-run 115
    python -m lawvm.us_federal.import_statute path/to/STATUTE-115-uslm.xml
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from lxml import etree

from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.us_federal.sources import (
    GOVINFO_PLAW_MEMBER_URL,
    content_digest,
    open_us_federal_import_farchive,
    plaw_locator,
    resolve_us_federal_farchive_path,
)

# USLM + Dublin Core namespaces present on the statutesAtLarge root.
USLM_NS = "http://schemas.gpo.gov/xml/uslm"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"

# Keyless govinfo content URL for one Statutes-at-Large volume's USLM document.
GOVINFO_STATUTE_USLM_URL = (
    "https://www.govinfo.gov/content/pkg/STATUTE-{volume}/uslm/STATUTE-{volume}.xml"
)
GOVINFO_STATUTE_PACKAGE_URL = "https://www.govinfo.gov/app/details/STATUTE-{volume}"

# Channel tag distinguishing Statutes-at-Large provenance from PLAW bulkdata.
ACQUISITION_CHANNEL = "statutes_at_large_uslm"

_HTTP_TIMEOUT = 240
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) LawVM/0.1 Safari/537.36"
)

# Inclusive volume span GPO publishes as USLM keyless. Volume 1 = 1789. The cap
# is advisory only (we never block on it); it documents the reachable window.
STATUTE_VOLUME_MIN = 1
STATUTE_VOLUME_MAX = 128


def _q(local: str, ns: str = USLM_NS) -> str:
    return f"{{{ns}}}{local}"


# Authoritative citation form inside <citableAs>: "Public Law 111-78" /
# "Private Law 107-1". The Congress-law separator is an en-dash (U+2013) in the
# source but a hyphen is accepted for robustness.
_CITABLE_AS_RE = re.compile(
    r"^\s*(?P<kind>Public|Private)\s+Law\s+(?P<congress>\d+)[–\-](?P<number>\d+)\s*$"
)


@dataclass(frozen=True, slots=True)
class StatutePLaw:
    """One enacted law sliced out of a Statutes-at-Large volume USLM document.

    ``congress``/``number`` are the RECONCILED identity: when the ``<congress>``
    meta element disagrees with the authoritative ``<citableAs>`` citation (a
    known GPO source defect — e.g. PL 111-78 in volume 123 carries a stray
    ``<congress>110</congress>``), the citation wins and ``congress_mismatch``
    records the discarded meta value for a typed acquisition finding.
    """

    congress: int
    number: int
    is_public: bool
    approved_date: str | None
    citable_as: str | None
    congress_mismatch: int | None
    element: Any = field(repr=False, compare=False)

    @property
    def locator(self) -> str:
        # Private laws would be pvtl{N}; acquisition stores only public laws.
        kind = "publ" if self.is_public else "pvtl"
        return plaw_locator(self.congress, self.number, kind=kind)


def _parse_citable_identity(citable_as: str | None) -> tuple[int, int, bool] | None:
    """Parse ``Public Law {C}-{N}`` into ``(congress, number, is_public)``."""
    if citable_as is None:
        return None
    m = _CITABLE_AS_RE.match(citable_as)
    if m is None:
        return None
    return (
        int(m.group("congress")),
        int(m.group("number")),
        m.group("kind") == "Public",
    )


@dataclass
class StatuteImportReport:
    """Aggregated result from one or more Statutes-at-Large volume imports."""

    volumes_scanned: int = 0
    total_plaw_units: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    bytes_raw: int = 0
    bytes_stored: int = 0
    sources: list[str] = field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = field(default_factory=list)
    congress_counts: dict[int, int] = field(default_factory=dict)
    volume_congress: dict[int, list[int]] = field(default_factory=dict)


def _record_skip(
    report: StatuteImportReport,
    *,
    rule_id: str,
    family: str,
    reason: str,
    source_label: str,
    locator: str | None = None,
    detail: dict[str, str] | None = None,
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


def _is_http_url(source: Path | str) -> bool:
    return str(source).startswith(("http://", "https://"))


def _source_label(source: Path | str) -> str:
    if _is_http_url(source):
        return str(source).rsplit("/", 1)[-1] or str(source)
    return Path(source).name


def fetch_statute_volume_bytes(volume: int) -> bytes:
    """Fetch one Statutes-at-Large volume's USLM XML from govinfo (keyless).

    Raises :class:`urllib.error.URLError` (or subclass) on transport failure so
    the caller records a typed acquisition error rather than a silent gap.
    """
    url = GOVINFO_STATUTE_USLM_URL.format(volume=int(volume))
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/xml, text/xml;q=0.9, */*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def _parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


def iter_statute_plaws(volume_xml: bytes) -> Iterator[StatutePLaw]:
    """Yield each ``<pLaw>`` unit in a Statutes-at-Large volume USLM document.

    Each unit's identity is read from its first descendant ``<docNumber>``,
    ``<congress>``, and ``<publicPrivate>`` / ``<dc:type>``. Units missing a
    parseable congress+number are still yielded with ``congress``/``number``
    of ``-1`` so the caller can record a typed skip (never silently dropped).
    """
    root = parse_corpus_xml(volume_xml)
    for pl in root.iter(_q("pLaw")):
        meta_congress = _parse_int(pl.findtext(f".//{_q('congress')}"))
        meta_number = _parse_int(pl.findtext(f".//{_q('docNumber')}"))
        public_private = (pl.findtext(f".//{_q('publicPrivate')}") or "").strip().lower()
        dc_type = (pl.findtext(f".//{_q('type', DC_NS)}") or "").strip().lower()
        citable_as = (pl.findtext(f".//{_q('citableAs')}") or "").strip() or None

        meta_is_public = public_private == "public" or (
            public_private == "" and dc_type == "public law"
        )

        # The <citableAs> "Public Law {C}-{N}" string is the authoritative
        # identity; the <congress> meta element is a known-defective field in a
        # handful of volumes. Reconcile and record any disagreement.
        cited = _parse_citable_identity(citable_as)
        congress = meta_congress if meta_congress is not None else -1
        number = meta_number if meta_number is not None else -1
        is_public = meta_is_public
        congress_mismatch: int | None = None
        if cited is not None:
            cited_congress, cited_number, cited_is_public = cited
            number = cited_number
            is_public = cited_is_public
            if meta_congress is not None and meta_congress != cited_congress:
                congress_mismatch = meta_congress
            congress = cited_congress

        yield StatutePLaw(
            congress=congress,
            number=number,
            is_public=is_public,
            approved_date=(pl.findtext(f".//{_q('approvedDate')}") or "").strip() or None,
            citable_as=citable_as,
            congress_mismatch=congress_mismatch,
            element=pl,
        )


_SLICE_OPEN = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<statuteSlice xmlns="' + USLM_NS.encode("ascii") + b'"'
    b' xmlns:dc="' + DC_NS.encode("ascii") + b'"'
    b' xmlns:dcterms="' + DCTERMS_NS.encode("ascii") + b'">'
)
_SLICE_CLOSE = b"</statuteSlice>"


def _serialize_standalone(plaw: StatutePLaw) -> bytes:
    """Wrap one ``<pLaw>`` element as a standalone well-formed USLM document.

    The serialized ``<pLaw>`` is embedded under a ``<statuteSlice>`` root that
    redeclares the USLM + Dublin Core namespaces, so each stored locator
    round-trips as independent, parseable XML (the volume root and its
    volume-wide meta are intentionally not duplicated into every law). The inner
    serialization carries its own ``xmlns`` declarations, which the wrapper's
    redeclaration harmlessly shadows; re-parsing below proves well-formedness.
    """
    inner = etree.tostring(plaw.element)
    doc = _SLICE_OPEN + inner + _SLICE_CLOSE
    # Re-parse + re-serialize so the stored bytes are guaranteed well-formed and
    # namespace-normalized rather than a textual concatenation we never validate.
    # ``doc`` is LawVM-authored bytes (wrapper + re-serialized <pLaw> element this
    # function just built), parsed here only to prove well-formedness before
    # storage; the original corpus volume already entered through
    # parse_corpus_xml in iter_statute_plaws.
    return etree.tostring(
        # lawvm-xml: own_output_check — re-parse of LawVM-authored slice bytes.
        etree.fromstring(doc),
        xml_declaration=True,
        encoding="UTF-8",
    )


def import_statute_volume(
    source: Path | str,
    farchive: Any,
    *,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> StatuteImportReport:
    """Import the public laws of one Statutes-at-Large volume into ``farchive``.

    ``source`` is a volume number, a ``STATUTE-{v}.xml`` local path, or a govinfo
    HTTPS URL. Each public ``<pLaw>`` is stored at ``us://plaw/{congress}/
    publ{N}.xml`` with storage_class ``xml`` and provenance metadata. Private
    laws and unidentifiable units are typed skips; SHA-256 dedup honours
    ``skip_existing``.
    """
    report = StatuteImportReport()
    label = _source_label(source)

    if isinstance(source, int) or (isinstance(source, str) and source.isdigit()):
        volume = int(source)
        url = GOVINFO_STATUTE_USLM_URL.format(volume=volume)
        report.sources.append(url)
        label = _source_label(url)
        try:
            volume_xml = fetch_statute_volume_bytes(volume)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            report.total_errors += 1
            _record_skip(
                report,
                rule_id="us_statute_import_volume_unreachable",
                family="source_pathology",
                reason=f"Statutes-at-Large volume {volume} USLM not fetchable: {exc}",
                source_label=label,
                detail={"volume": str(volume), "url": url},
            )
            return report
    else:
        report.sources.append(str(source))
        if _is_http_url(source):
            try:
                req = urllib.request.Request(
                    str(source), headers={"User-Agent": _USER_AGENT}
                )
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
                    volume_xml = resp.read()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                report.total_errors += 1
                _record_skip(
                    report,
                    rule_id="us_statute_import_volume_unreachable",
                    family="source_pathology",
                    reason=f"Statutes-at-Large URL not fetchable: {exc}",
                    source_label=label,
                    detail={"url": str(source)},
                )
                return report
        else:
            volume_xml = Path(source).read_bytes()

    report.volumes_scanned += 1

    try:
        units = list(iter_statute_plaws(volume_xml))
    except etree.XMLSyntaxError as exc:
        report.total_errors += 1
        _record_skip(
            report,
            rule_id="us_statute_import_volume_unparsable",
            family="source_pathology",
            reason=f"Statutes-at-Large volume XML did not parse: {exc}",
            source_label=label,
        )
        return report

    print(f"  STATUTE volume {label}: {len(units):,} pLaw units", file=sys.stderr)

    seen_locators: set[str] = set()
    for unit in units:
        report.total_plaw_units += 1

        if unit.congress < 0 or unit.number < 0:
            report.total_skipped += 1
            _record_skip(
                report,
                rule_id="us_statute_import_unidentified_plaw",
                family="source_pathology",
                reason="pLaw unit missing parseable congress/docNumber",
                source_label=label,
                detail={
                    "congress": str(unit.congress),
                    "number": str(unit.number),
                    "citable_as": unit.citable_as or "",
                },
            )
            continue

        if not unit.is_public:
            report.total_skipped += 1
            _record_skip(
                report,
                rule_id="us_statute_import_private_law_filtered",
                family="transport_cleanup",
                reason="private-law unit filtered; only public laws are stored",
                source_label=label,
                locator=unit.locator,
                detail={"congress": str(unit.congress), "law_number": str(unit.number)},
            )
            continue

        if unit.congress_mismatch is not None:
            # Authoritative citation already won; record the defective <congress>
            # meta value so the source pathology is visible, not silently masked.
            _record_skip(
                report,
                rule_id="us_statute_import_congress_meta_mismatch",
                family="source_pathology",
                reason=(
                    "pLaw <congress> meta disagreed with <citableAs> citation; "
                    "citation used for the canonical locator"
                ),
                source_label=label,
                locator=unit.locator,
                detail={
                    "meta_congress": str(unit.congress_mismatch),
                    "citation_congress": str(unit.congress),
                    "law_number": str(unit.number),
                    "citable_as": unit.citable_as or "",
                },
            )

        locator = unit.locator
        if locator in seen_locators:
            report.total_skipped += 1
            _record_skip(
                report,
                rule_id="us_statute_import_duplicate_logical_locator",
                family="source_pathology",
                reason="duplicate public-law locator within volume; later unit skipped",
                source_label=label,
                locator=locator,
            )
            continue
        seen_locators.add(locator)

        data = _serialize_standalone(unit)
        digest = content_digest(data)
        current = farchive.resolve(locator)
        if current is not None and current.digest == digest and skip_existing:
            report.total_skipped += 1
            _record_skip(
                report,
                rule_id="us_statute_import_existing_content_skipped",
                family="transport_cleanup",
                reason="archive already contains identical content and skip_existing set",
                source_label=label,
                locator=locator,
                detail={"digest": digest},
            )
            continue

        if current is not None and current.digest != digest:
            print(
                f"WARNING: {locator} changed: "
                f"{current.digest[:12]}.. -> {digest[:12]}..",
                file=sys.stderr,
            )

        report.congress_counts[unit.congress] = (
            report.congress_counts.get(unit.congress, 0) + 1
        )
        report.volume_congress.setdefault(report.volumes_scanned, [])
        if unit.congress not in report.volume_congress[report.volumes_scanned]:
            report.volume_congress[report.volumes_scanned].append(unit.congress)

        if dry_run:
            report.total_imported += 1
            report.bytes_raw += len(data)
            report.bytes_stored += len(data)
            continue

        metadata = {
            "source_url": GOVINFO_PLAW_MEMBER_URL.format(
                congress=unit.congress, number=unit.number
            ),
            "acquisition_source": report.sources[-1],
            "acquisition_channel": ACQUISITION_CHANNEL,
            "congress": str(unit.congress),
            "law_number": str(unit.number),
            "public_law": f"Public Law {unit.congress}-{unit.number}",
        }
        if unit.approved_date:
            metadata["approved_date"] = unit.approved_date
        if unit.citable_as:
            metadata["citable_as"] = unit.citable_as
        if unit.congress_mismatch is not None:
            metadata["source_congress_meta"] = str(unit.congress_mismatch)

        farchive.store(locator, data, storage_class="xml", metadata=metadata)
        report.total_imported += 1
        report.bytes_raw += len(data)
        report.bytes_stored += len(data)

    return report


def _expand_volume_arg(arg: str) -> list[str]:
    """Expand a CLI source arg: ``N``, ``A-B`` range, or a path/URL (passthrough)."""
    if "-" in arg and not _is_http_url(arg):
        lo_s, _, hi_s = arg.partition("-")
        if lo_s.isdigit() and hi_s.isdigit():
            lo, hi = int(lo_s), int(hi_s)
            if lo <= hi:
                return [str(v) for v in range(lo, hi + 1)]
    return [arg]


def import_statute_sources(
    sources: list[str],
    *,
    db_path: Path | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> StatuteImportReport:
    """Import several Statutes-at-Large volumes into the canonical U.S. farchive."""
    overall = StatuteImportReport()

    if dry_run:
        print("  (--dry-run: no writes will be performed)", file=sys.stderr)

    expanded: list[str] = []
    for s in sources:
        expanded.extend(_expand_volume_arg(s))

    archive = open_us_federal_import_farchive(db_path, dry_run=dry_run)
    try:
        for source in expanded:
            print(f"\nImporting STATUTE source: {source}", file=sys.stderr)
            report = import_statute_volume(
                source, archive, skip_existing=skip_existing, dry_run=dry_run
            )
            overall.sources.extend(report.sources)
            overall.volumes_scanned += report.volumes_scanned
            overall.total_plaw_units += report.total_plaw_units
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
        description=(
            "Import U.S. Statutes at Large USLM XML (older public laws) into a "
            "farchive. Sources are volume numbers (e.g. 115), inclusive ranges "
            "(e.g. 101-126), local STATUTE-{v}.xml paths, or govinfo HTTPS URLs."
        ),
    )
    parser.add_argument("sources", nargs="+", help="Volume numbers, ranges, paths, or URLs.")
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Explicit farchive path (default: canonical data/us_federal.farchive).",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip units whose identical content is already stored.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + slice + report without writing to the archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    for source in args.sources:
        if (
            not source.isdigit()
            and not _is_http_url(source)
            and "-" not in source
            and not Path(source).exists()
        ):
            print(f"error: STATUTE source not found: {source}", file=sys.stderr)
            return 1

    if args.dest is None:
        dest_path, dest_rule = resolve_us_federal_farchive_path()
        print(f"Opening farchive: {dest_path}  ({dest_rule})", file=sys.stderr)
    else:
        print(f"Opening farchive: {args.dest}", file=sys.stderr)

    report = import_statute_sources(
        list(args.sources),
        db_path=args.dest,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )

    print("\nSTATUTE import complete:")
    print(f"  Volumes scanned: {report.volumes_scanned:,}")
    print(f"  pLaw units:      {report.total_plaw_units:,}")
    print(f"  Total imported:  {report.total_imported:,}")
    print(f"  Total skipped:   {report.total_skipped:,}")
    print(f"  Total errors:    {report.total_errors:,}")
    if report.bytes_raw:
        print(f"  Stored bytes:    {report.bytes_stored:,}")
    if report.congress_counts:
        per = "  ".join(f"{c}:{n}" for c, n in sorted(report.congress_counts.items()))
        print(f"  Per Congress:    {per}")

    return 1 if report.total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
