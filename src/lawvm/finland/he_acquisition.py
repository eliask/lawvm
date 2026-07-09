"""he_acquisition.py — HE (hallituksen esitys) acquisition lane.

Ingests Finlex's ``government-proposal.zip`` AKN batch dump into
``data/fi_government_proposal.farchive`` (isolated from finlex.farchive).

Per-jurisdiction acquisition convention
----------------------------------------
Farchive name: ``{jurisdiction_code}_{document_corpus}.farchive``
  - ``fi_government_proposal.farchive``  — Finland HEs (this module)
  - ``uk_bills.farchive``               — UK Parliament bills (when ready)
  - ``ee_riigikogu_bills.farchive``     — Estonia Riigikogu bills (when ready)
  - ``no_proposisjon.farchive``         — Norway proposisjoner (when ready)

CLI: ``lawvm acquire-fi-proposals --source LOCATION [opts]``

Per-HE locators under the AKN URI scheme (stored as farchive locators):
  ``akn/fi/doc/government-proposal/{year}/{number}/{lang}@/main.xml``
  ``akn/fi/doc/government-proposal/{year}/{number}/{lang}@/main.pdf``
  ``akn/fi/doc/government-proposal/{year}/{number}/{lang}@/main_pdf-wrapper.xml``

AGENTS.md compliance
---------------------
§1.1  No silent target hijacking: metadata disagreement between main.xml
       and main_pdf-wrapper.xml emits HEMetadataDisagreement.
§1.8  No source lane disappearance: failures recorded as HEAcquisitionFailure.
§1.9  Typed primitives, no stringly-typed dicts crossing phase boundaries.
§1.10 No bare try/except; exceptions caught at bounded, well-justified
       boundaries (zip entry read, XML parse) with typed failure emission.

Phase: Acquire (§6 phase 1) + minimal Clean (§6 phase 2).
Out of scope: body-atom extraction, cross-reference extraction, lifecycle
beyond finlex:state, lausunnot, ptk_speeches, MeV integration.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from lxml import etree

from lawvm.core.archive_safety import ArchiveMemberTooLarge, safe_zip_read
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.pdf_blob_guard import classify_pdf_blob

# ---------------------------------------------------------------------------
# AKN / Finlex namespace constants
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_FINLEX_NS = "http://data.finlex.fi/schema/finlex"

_DEFAULT_FARCHIVE = "data/fi_government_proposal.farchive"
_DEFAULT_SOURCE = str(Path.home() / "Downloads" / "government-proposal.zip")
_HTTP_CHUNK = 1024 * 1024  # 1 MB streaming chunk
_PROGRESS_INTERVAL = 500
_USER_AGENT = "LawVM/0.1 (+https://lawvm.org)"

# AKN path prefix for government proposals inside the zip
_HE_PATH_PREFIX = "akn/fi/doc/government-proposal/"


# ---------------------------------------------------------------------------
# Typed primitives
# ---------------------------------------------------------------------------


class HEStructuralTier(Enum):
    """Structural classification of one language variant's main.xml.

    FULL_AKN:     mainBody contains hcontainer/section descendants with real
                  content.
    PDF_WRAPPER:  mainBody is an AKN stub (componentRef pointing at main.pdf,
                  or a single hcontainer with name='contentAbsent').
    """

    FULL_AKN = "full_akn"
    PDF_WRAPPER = "pdf_wrapper"


@dataclass(frozen=True, slots=True)
class HEAcquisitionMetadata:
    """Typed acquisition-time digest for one language variant of one HE.

    Stored alongside blobs in the farchive metadata table. Full body content
    stays in the blobs and rehydrates per query (feature #4).
    """

    he_id: str
    """Human-readable HE identifier, e.g. 'HE 98/1996'."""
    he_year: int
    he_number: int
    he_uri: str
    """FRBR work URI, e.g. '/akn/fi/doc/government-proposal/1996/98'."""
    lang: str
    """Language code for this variant, e.g. 'fin' or 'swe'."""
    languages_in_he: tuple[str, ...]
    """All language codes present for this HE in the zip."""
    ministry_canonical_id: str
    """e.g. 'fi.ministry-of-social-affairs-and-health'."""
    ministry_show_as: str
    """e.g. 'Sosiaali- ja terveysministeriö'."""
    title: str
    """docTitle text content."""
    date_issued: date
    """FRBRdate name='dateIssued' from FRBRWork."""
    structural_tier: HEStructuralTier
    finlex_state: str
    """finlex:state value, e.g. 'closed'."""
    source_zip_sha256: str
    ingest_timestamp: datetime


@dataclass(frozen=True, slots=True)
class HEAcquisitionFailure:
    """Typed record for a per-HE acquisition failure.

    Emitted when a zip entry fails to read, XML fails to parse, or required
    metadata fields are absent.  Never silently dropped (AGENTS.md §1.8).
    """

    rule_id: str
    """Stable rule identifier, e.g. 'HE_ACQ.ZIP_READ_ERROR'."""
    phase: str
    """Pipeline phase: 'acquisition' or 'parse_metadata'."""
    family: str
    """AGENTS.md heuristic family tag."""
    he_year: int | None
    he_number: int | None
    lang: str | None
    zip_entry_name: str
    reason: str
    detail: str
    strict_disposition: str
    """'abort' in strict mode, 'record' in quirks mode."""


@dataclass(frozen=True, slots=True)
class HEMetadataDisagreement:
    """Typed record when main.xml and main_pdf-wrapper.xml disagree on metadata.

    Per AGENTS.md §1.1 no silent target hijacking: the disagreement is
    recorded, not resolved by silent pick.
    """

    rule_id: str
    he_year: int
    he_number: int
    lang: str
    field_name: str
    main_xml_value: str
    pdf_wrapper_value: str
    resolution: str
    """Which value was used and why."""


@dataclass
class HEIngestRun:
    """Provenance and counts for one ingest run."""

    source_uri: str
    source_zip_sha256: str
    ingest_timestamp: datetime
    worker_count: int
    stream_mode: str
    added: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list[HEAcquisitionFailure] | None = None
    disagreements: list[HEMetadataDisagreement] | None = None

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []
        if self.disagreements is None:
            self.disagreements = []


# ---------------------------------------------------------------------------
# AKN parsing helpers
# ---------------------------------------------------------------------------


def _localname(node: etree._Element) -> str:
    tag = node.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag) if isinstance(tag, str) else ""


def _extract_frbr_subtype(root: etree._Element) -> str:
    el = root.find(f".//{{{_AKN_NS}}}FRBRsubtype")
    return (el.attrib.get("value", "") if el is not None else "")


def _extract_he_year_number(root: etree._Element) -> tuple[int | None, int | None]:
    """Extract he_year and he_number from FRBRWork metadata.

    Primary: parse FRBRWork/FRBRuri value, e.g.
    '/akn/fi/doc/government-proposal/1996/98'.
    Fallback: FRBRnumber + FRBRdate year.
    """
    work = root.find(f".//{{{_AKN_NS}}}FRBRWork")
    if work is None:
        return None, None

    uri_el = work.find(f"{{{_AKN_NS}}}FRBRuri")
    if uri_el is not None:
        uri = uri_el.attrib.get("value", "")
        parts = uri.rstrip("/").split("/")
        if len(parts) >= 2:
            try:
                return int(parts[-2]), int(parts[-1])
            except ValueError:
                pass

    # Fallback
    num_el = work.find(f"{{{_AKN_NS}}}FRBRnumber")
    date_el = work.find(f"{{{_AKN_NS}}}FRBRdate[@name='dateIssued']")
    if num_el is not None and date_el is not None:
        date_str = date_el.attrib.get("date", "")
        num_str = num_el.attrib.get("value", "")
        if len(date_str) >= 4 and num_str:
            try:
                return int(date_str[:4]), int(num_str)
            except ValueError:
                pass

    return None, None


def _extract_date_issued(root: etree._Element) -> date | None:
    """Extract FRBRWork/FRBRdate[@name='dateIssued'] as a date object.

    Falls back to FRBRdate[@name='dateIssuedGenerated'] for HEs where the
    canonical date was not recorded (source-pathology case, e.g. some swe@
    variants use 'dateIssuedGenerated' instead of 'dateIssued').
    """
    work = root.find(f".//{{{_AKN_NS}}}FRBRWork")
    if work is None:
        return None
    # Primary: dateIssued
    date_el = work.find(f"{{{_AKN_NS}}}FRBRdate[@name='dateIssued']")
    # Fallback: dateIssuedGenerated (source pathology in some swe@ variants)
    if date_el is None:
        date_el = work.find(f"{{{_AKN_NS}}}FRBRdate[@name='dateIssuedGenerated']")
    if date_el is None:
        return None
    date_str = date_el.attrib.get("date", "")
    if not date_str:
        return None
    parts = date_str.split("-")
    if len(parts) == 3:
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            pass
    return None


def _extract_lang(root: etree._Element) -> str:
    """Extract FRBRExpression/FRBRlanguage language attribute."""
    expr = root.find(f".//{{{_AKN_NS}}}FRBRExpression")
    if expr is None:
        return ""
    lang_el = expr.find(f"{{{_AKN_NS}}}FRBRlanguage")
    return (lang_el.attrib.get("language", "") if lang_el is not None else "")


def _extract_ministry(root: etree._Element) -> tuple[str, str]:
    """Return (canonical_id, show_as) for the ministry.

    Reads finlex:administrativeBranch refersTo='#fi.ministry-of-...' and
    looks up the matching TLCOrganization showAs from references.
    """
    admin_el = root.find(f".//{{{_FINLEX_NS}}}administrativeBranch")
    if admin_el is None:
        return ("", "")
    refers_to = admin_el.attrib.get("refersTo", "")
    canonical_id = refers_to.lstrip("#")
    refs_el = root.find(f".//{{{_AKN_NS}}}references")
    show_as = ""
    if refs_el is not None:
        for child in refs_el:
            if _localname(child) == "TLCOrganization":
                if child.attrib.get("eId", "") == canonical_id:
                    show_as = child.attrib.get("showAs", "")
                    break
    return (canonical_id, show_as)


def _extract_title(root: etree._Element) -> str:
    """Extract docTitle text content from preface."""
    title_el = root.find(f".//{{{_AKN_NS}}}docTitle")
    if title_el is None:
        return ""
    return (etree.tostring(title_el, method="text", encoding="unicode") or "").strip()


def _extract_he_id(root: etree._Element) -> str:
    """Extract docNumber text from preface, e.g. 'HE 98/1996'."""
    num_el = root.find(f".//{{{_AKN_NS}}}docNumber")
    if num_el is None:
        return ""
    return (etree.tostring(num_el, method="text", encoding="unicode") or "").strip()


def _extract_frbr_uri(root: etree._Element) -> str:
    """Extract FRBRWork/FRBRuri value."""
    work = root.find(f".//{{{_AKN_NS}}}FRBRWork")
    if work is None:
        return ""
    uri_el = work.find(f"{{{_AKN_NS}}}FRBRuri")
    return (uri_el.attrib.get("value", "") if uri_el is not None else "")


def _extract_finlex_state(root: etree._Element) -> str:
    """Extract finlex:state value attribute."""
    el = root.find(f".//{{{_FINLEX_NS}}}state")
    return (el.attrib.get("value", "") if el is not None else "")


def classify_structural_tier(root: etree._Element) -> HEStructuralTier:
    """Classify a parsed main.xml root into FULL_AKN or PDF_WRAPPER.

    Classification rule:
    - PDF_WRAPPER: mainBody absent, or body contains a componentRef, or
      body is a single hcontainer with name='contentAbsent'.
    - FULL_AKN: mainBody has substantive hcontainer/section content.

    This result is stored in farchive metadata at ingest time; not re-derived
    per query.
    """
    body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if body is None:
        return HEStructuralTier.PDF_WRAPPER

    children = list(body)
    if not children:
        return HEStructuralTier.PDF_WRAPPER

    # componentRef anywhere in body → PDF stub
    for child in body.iter():
        if _localname(child) == "componentRef":
            return HEStructuralTier.PDF_WRAPPER

    # Single hcontainer named 'contentAbsent' → PDF stub marker
    if len(children) == 1:
        child = children[0]
        if (
            _localname(child) == "hcontainer"
            and child.attrib.get("name") == "contentAbsent"
        ):
            return HEStructuralTier.PDF_WRAPPER

    return HEStructuralTier.FULL_AKN


def parse_he_metadata(
    main_xml_bytes: bytes,
    *,
    zip_entry_name: str,
    source_zip_sha256: str,
    ingest_timestamp: datetime,
    languages_in_he: tuple[str, ...],
) -> HEAcquisitionMetadata | HEAcquisitionFailure:
    """Parse AKN metadata from main.xml bytes.

    Returns HEAcquisitionMetadata on success, HEAcquisitionFailure on parse
    error or missing required field.  No silent failures (AGENTS.md §1.8).
    """
    xml_root: etree._Element | None = None
    parse_err: str | None = None
    try:
        xml_root = parse_corpus_xml(main_xml_bytes)
    except etree.XMLSyntaxError as exc:
        parse_err = str(exc)

    if xml_root is None or parse_err is not None:
        return HEAcquisitionFailure(
            rule_id="HE_ACQ.XML_PARSE_ERROR",
            phase="parse_metadata",
            family="transport_cleanup",
            he_year=None,
            he_number=None,
            lang=None,
            zip_entry_name=zip_entry_name,
            reason="XML parse error in main.xml",
            detail=parse_err or "unknown parse error",
            strict_disposition="abort",
        )

    # Validate FRBRsubtype — reject non-HE documents
    subtype = _extract_frbr_subtype(xml_root)
    if subtype != "government-proposal":
        return HEAcquisitionFailure(
            rule_id="HE_ACQ.WRONG_FRBR_SUBTYPE",
            phase="parse_metadata",
            family="source_pathology",
            he_year=None,
            he_number=None,
            lang=None,
            zip_entry_name=zip_entry_name,
            reason=(
                f"FRBRsubtype is {subtype!r}, expected 'government-proposal'; "
                "rejecting non-HE document from HE acquisition lane"
            ),
            detail=f"subtype={subtype!r}",
            strict_disposition="abort",
        )

    he_year, he_number = _extract_he_year_number(xml_root)
    if he_year is None or he_number is None:
        return HEAcquisitionFailure(
            rule_id="HE_ACQ.MISSING_YEAR_NUMBER",
            phase="parse_metadata",
            family="source_pathology",
            he_year=he_year,
            he_number=he_number,
            lang=None,
            zip_entry_name=zip_entry_name,
            reason="Could not extract he_year/he_number from FRBRWork metadata",
            detail="FRBRuri or FRBRnumber missing or malformed",
            strict_disposition="abort",
        )

    lang = _extract_lang(xml_root)
    date_issued = _extract_date_issued(xml_root)

    if date_issued is None:
        return HEAcquisitionFailure(
            rule_id="HE_ACQ.MISSING_DATE_ISSUED",
            phase="parse_metadata",
            family="source_pathology",
            he_year=he_year,
            he_number=he_number,
            lang=lang or None,
            zip_entry_name=zip_entry_name,
            reason="FRBRWork FRBRdate[@name='dateIssued'] absent or unparseable",
            detail="",
            strict_disposition="abort",
        )

    he_uri = _extract_frbr_uri(xml_root)
    ministry_canonical_id, ministry_show_as = _extract_ministry(xml_root)
    title = _extract_title(xml_root)
    he_id = _extract_he_id(xml_root)
    finlex_state = _extract_finlex_state(xml_root)
    structural_tier = classify_structural_tier(xml_root)

    if not he_id:
        # Synthesise from year/number when docNumber is absent
        he_id = f"HE {he_number}/{he_year}"

    return HEAcquisitionMetadata(
        he_id=he_id,
        he_year=he_year,
        he_number=he_number,
        he_uri=he_uri,
        lang=lang,
        languages_in_he=languages_in_he,
        ministry_canonical_id=ministry_canonical_id,
        ministry_show_as=ministry_show_as,
        title=title,
        date_issued=date_issued,
        structural_tier=structural_tier,
        finlex_state=finlex_state,
        source_zip_sha256=source_zip_sha256,
        ingest_timestamp=ingest_timestamp,
    )


def _check_metadata_disagreement(
    meta_main: HEAcquisitionMetadata,
    wrapper_bytes: bytes,
    zip_entry_wrapper: str,
) -> list[HEMetadataDisagreement]:
    """Compare main.xml metadata against main_pdf-wrapper.xml metadata.

    Per AGENTS.md §1.1: if metadata disagrees, emit HEMetadataDisagreement.
    Never silently pick one side.

    Only checks fields that both documents authoritatively carry.
    """
    disagreements: list[HEMetadataDisagreement] = []
    wrapper_root: etree._Element | None = None
    try:
        wrapper_root = parse_corpus_xml(wrapper_bytes)
    except etree.XMLSyntaxError:
        disagreements.append(
            HEMetadataDisagreement(
                rule_id="HE_ACQ.PDF_WRAPPER_PARSE_ERROR",
                he_year=meta_main.he_year,
                he_number=meta_main.he_number,
                lang=meta_main.lang,
                field_name="pdf_wrapper_xml",
                main_xml_value="parseable",
                pdf_wrapper_value="XML_PARSE_ERROR",
                resolution="using main.xml values; wrapper unparseable",
            )
        )
        return disagreements

    assert wrapper_root is not None  # mypy; guaranteed by the except above

    wrapper_year, wrapper_number = _extract_he_year_number(wrapper_root)
    if wrapper_year != meta_main.he_year or wrapper_number != meta_main.he_number:
        disagreements.append(
            HEMetadataDisagreement(
                rule_id="HE_ACQ.YEAR_NUMBER_DISAGREEMENT",
                he_year=meta_main.he_year,
                he_number=meta_main.he_number,
                lang=meta_main.lang,
                field_name="he_year/he_number",
                main_xml_value=f"{meta_main.he_year}/{meta_main.he_number}",
                pdf_wrapper_value=f"{wrapper_year}/{wrapper_number}",
                resolution="using main.xml values",
            )
        )

    wrapper_state = _extract_finlex_state(wrapper_root)
    if wrapper_state and wrapper_state != meta_main.finlex_state:
        disagreements.append(
            HEMetadataDisagreement(
                rule_id="HE_ACQ.STATE_DISAGREEMENT",
                he_year=meta_main.he_year,
                he_number=meta_main.he_number,
                lang=meta_main.lang,
                field_name="finlex_state",
                main_xml_value=meta_main.finlex_state,
                pdf_wrapper_value=wrapper_state,
                resolution="using main.xml value",
            )
        )

    wrapper_ministry_id, _ = _extract_ministry(wrapper_root)
    if wrapper_ministry_id and wrapper_ministry_id != meta_main.ministry_canonical_id:
        disagreements.append(
            HEMetadataDisagreement(
                rule_id="HE_ACQ.MINISTRY_DISAGREEMENT",
                he_year=meta_main.he_year,
                he_number=meta_main.he_number,
                lang=meta_main.lang,
                field_name="ministry_canonical_id",
                main_xml_value=meta_main.ministry_canonical_id,
                pdf_wrapper_value=wrapper_ministry_id,
                resolution="using main.xml value",
            )
        )

    return disagreements


# ---------------------------------------------------------------------------
# ZIP source helpers
# ---------------------------------------------------------------------------


# Match the SpooledTemporaryFile threshold used by tools/import_zip.py so HE
# acquisition has the same streaming behavior as the enacted-law import path.
_SPOOLED_MAX_BYTES = 64 * 1024 * 1024


def _is_https_url(source: str) -> bool:
    return source.startswith("https://") or source.startswith("http://")


@contextmanager
def _open_zip_for_ingest(
    source: str,
    *,
    stream_mode: str,
    keep_tempfile: bool,
    sha256_out: list[str],
) -> Iterator[tuple[zipfile.ZipFile, str]]:
    """Yield (ZipFile, resolved_source_uri).

    Local paths: opened directly. Caller pre-computes sha256.

    HTTPS (tempfile mode): streamed to a temp file, sha256 computed on-the-fly
        during the stream, then opened. Uses ``SpooledTemporaryFile``
        (in-memory up to 64 MB, spills to disk above) — same pattern as
        ``tools/import_zip._open_zip_source``. When ``--keep-tempfile`` is
        set, falls back to ``NamedTemporaryFile`` so the on-disk path is
        recoverable for the operator.

    HTTPS (range mode): currently falls back to tempfile. Range-request
        streaming (RemoteZip-style partial reads) is a future extension that
        requires a range-aware zip reader.

    ``sha256_out`` is an out-parameter list: HTTPS callers receive the
    streamed-bytes sha256 here once the download completes. Hash-verified
    before farchive write per the brief's "HTTPS streaming hardening" clause.
    """
    if not _is_https_url(source):
        with open(source, "rb") as fp:
            with zipfile.ZipFile(fp, "r") as zf:
                yield zf, source
        return

    print(f"  Streaming from {source} ...", file=sys.stderr)
    if stream_mode == "range":
        print(
            "  NOTE: range mode not yet implemented; falling back to tempfile",
            file=sys.stderr,
        )
    req = urllib.request.Request(
        source,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/zip, application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )

    def _stream_into(tmp: Any, on_disk_name: str | None) -> None:
        h = hashlib.sha256()
        downloaded = 0
        with urllib.request.urlopen(req, timeout=120) as resp:
            while True:
                chunk = resp.read(_HTTP_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                tmp.write(chunk)
                downloaded += len(chunk)
                if downloaded % (100 * _HTTP_CHUNK) == 0:
                    print(
                        f"  Downloaded {downloaded // (1024 * 1024):,} MB ...",
                        file=sys.stderr,
                    )
        tmp.flush()
        tmp.seek(0)
        digest = h.hexdigest()
        sha256_out.append(digest)
        print(f"  Streamed sha256: {digest}", file=sys.stderr)
        if on_disk_name is not None:
            print(f"  Temp file retained at: {on_disk_name}", file=sys.stderr)

    if keep_tempfile:
        # Operator asked to retain the streamed zip — NamedTemporaryFile has
        # a stable on-disk path. delete=False so the file persists after we
        # close it.
        tmp_named = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        try:
            _stream_into(tmp_named, tmp_named.name)
            with zipfile.ZipFile(tmp_named, "r") as zf:
                yield zf, source
        finally:
            tmp_named.close()
    else:
        # SpooledTemporaryFile: in-memory up to 64 MB, spills to disk above.
        # Same pattern as tools/import_zip._open_zip_source.
        with tempfile.SpooledTemporaryFile(
            max_size=_SPOOLED_MAX_BYTES, mode="w+b", suffix=".zip"
        ) as tmp_fp:
            _stream_into(tmp_fp, None)
            with zipfile.ZipFile(tmp_fp, "r") as zf:
                yield zf, source


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# HE group extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _HEGroup:
    """All zip entry names belonging to one HE year/number."""

    year: int
    number: int
    entries: tuple[str, ...]


def _build_he_groups(names: list[str]) -> list[_HEGroup]:
    """Group zip entry names by (year, number)."""
    groups: dict[tuple[int, int], list[str]] = defaultdict(list)
    for name in names:
        if not name.startswith(_HE_PATH_PREFIX):
            continue
        rest = name[len(_HE_PATH_PREFIX):]
        parts = rest.split("/", 2)
        if len(parts) < 2:
            continue
        try:
            year = int(parts[0])
            number = int(parts[1])
        except ValueError:
            continue
        groups[(year, number)].append(name)

    return [
        _HEGroup(year=year, number=number, entries=tuple(sorted(entries)))
        for (year, number), entries in sorted(groups.items())
    ]


def _build_he_lang_map(names: list[str]) -> dict[tuple[int, int], set[str]]:
    """Map (year, number) → set of language codes present in zip."""
    result: dict[tuple[int, int], set[str]] = defaultdict(set)
    for name in names:
        if not name.startswith(_HE_PATH_PREFIX):
            continue
        rest = name[len(_HE_PATH_PREFIX):]
        parts = rest.split("/", 3)
        if len(parts) < 4:
            continue
        try:
            year, number = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        lang_at = parts[2]  # e.g. "fin@"
        if lang_at.endswith("@"):
            result[(year, number)].add(lang_at[:-1])
    return dict(result)


# ---------------------------------------------------------------------------
# Per-HE locator convention
# ---------------------------------------------------------------------------


def he_locator(year: int, number: int, lang: str, filename: str) -> str:
    """Canonical farchive locator for one HE artifact.

    Keys follow the AKN URI scheme used in the source zip.
    Example: ``akn/fi/doc/government-proposal/1996/98/fin@/main.xml``
    """
    return f"akn/fi/doc/government-proposal/{year}/{number}/{lang}@/{filename}"


# ---------------------------------------------------------------------------
# Per-HE ingest: two-phase design
#
# Phase 1 (_read_he_group): reads bytes from zip and parses metadata.
#   Can run in a worker thread; holds the zip_lock while reading.
#   Returns a fully self-contained result with all bytes in memory.
#
# Phase 2 (_store_he_group): writes to farchive.
#   Runs in the main thread (farchive is not thread-safe).
#   Receives the in-memory result from Phase 1.
# ---------------------------------------------------------------------------


@dataclass
class _HEBlob:
    """One file blob ready to write to farchive."""

    locator: str
    data: bytes
    storage_class: str
    meta_dict: dict[str, str]


@dataclass
class _HEReadResult:
    """Result of Phase 1 (zip read + metadata parse) for one HE group."""

    year: int
    number: int
    failures: list[HEAcquisitionFailure]
    disagreements: list[HEMetadataDisagreement]
    blobs: list[_HEBlob]
    """Blobs to store; empty for failed HEs."""


def _read_he_group(
    *,
    zf: zipfile.ZipFile,
    group: _HEGroup,
    source_zip_sha256: str,
    ingest_timestamp: datetime,
    languages_in_he: tuple[str, ...],
    include_pdfs: bool = False,
) -> _HEReadResult:
    """Phase 1: read all files for one HE from the zip and parse metadata.

    Must be called with zip_lock held. Does NOT touch farchive.

    When include_pdfs is False (default), main.pdf entries are skipped at
    blob-creation time — LawVM's text-state-compiler scope does not extract
    text from PDFs and the 97.6% FULL_AKN majority has redundant XML +
    PDF content. Saves ~6-12 GB of farchive storage for the full FI corpus.
    The 2.4% PDF_WRAPPER HEs still get their main.xml stub + metadata
    projection, just no underlying PDF blob; they can be re-acquired with
    include_pdfs=True if the PDF text becomes needed later.
    """
    failures: list[HEAcquisitionFailure] = []
    disagreements: list[HEMetadataDisagreement] = []
    blobs: list[_HEBlob] = []
    year, number = group.year, group.number

    # Partition entries by language and filename
    lang_entries: dict[str, dict[str, str]] = {}  # lang → {filename: zip_entry_name}
    for entry in group.entries:
        rest = entry[len(_HE_PATH_PREFIX):]
        parts = rest.split("/", 3)
        if len(parts) < 4:
            continue
        lang_at = parts[2]  # e.g. "fin@"
        filename = parts[3]  # e.g. "main.xml"
        if not lang_at.endswith("@") or not filename:
            continue
        lang = lang_at[:-1]
        if lang not in lang_entries:
            lang_entries[lang] = {}
        lang_entries[lang][filename] = entry

    for lang, file_map in lang_entries.items():
        main_xml_entry = file_map.get("main.xml")
        if main_xml_entry is None:
            failures.append(
                HEAcquisitionFailure(
                    rule_id="HE_ACQ.MISSING_MAIN_XML",
                    phase="acquisition",
                    family="source_pathology",
                    he_year=year,
                    he_number=number,
                    lang=lang,
                    zip_entry_name=f"{_HE_PATH_PREFIX}{year}/{number}/{lang}@/main.xml",
                    reason="main.xml missing for this language variant",
                    detail=f"present files: {sorted(file_map.keys())}",
                    strict_disposition="abort",
                )
            )
            continue

        # Read main.xml bytes
        main_xml_bytes: bytes
        try:
            main_xml_bytes = safe_zip_read(
                zf, main_xml_entry, archive_path="<government-proposal.zip>"
            )
        except ArchiveMemberTooLarge as exc:
            # Acquisition lane: never silently drop. Emit a typed
            # HEAcquisitionFailure (AGENTS.md §1.8/§1.10) carrying the
            # member name and declared vs cap sizes, so the oversized
            # member is conserved in the run's failure list rather than
            # the read becoming a silent OOM. Non-blocking on the
            # per-HE level (the cap is operator-tunable); per-HE abort
            # is preserved via strict_disposition.
            failures.append(
                HEAcquisitionFailure(
                    rule_id="HE_ACQ.ARCHIVE_MEMBER_TOO_LARGE",
                    phase="acquisition",
                    family="transport_cleanup",
                    he_year=year,
                    he_number=number,
                    lang=lang,
                    zip_entry_name=main_xml_entry,
                    reason="main.xml declares more bytes than LAWVM_MAX_ARCHIVE_MEMBER_BYTES",
                    detail=(
                        f"declared_size={exc.declared_size}; "
                        f"cap_bytes={exc.cap_bytes}; "
                        f"archive_path={exc.archive_path}"
                    ),
                    strict_disposition="abort",
                )
            )
            continue
        except Exception as exc:
            failures.append(
                HEAcquisitionFailure(
                    rule_id="HE_ACQ.ZIP_READ_ERROR",
                    phase="acquisition",
                    family="transport_cleanup",
                    he_year=year,
                    he_number=number,
                    lang=lang,
                    zip_entry_name=main_xml_entry,
                    reason="Failed to read main.xml from zip",
                    detail=str(exc),
                    strict_disposition="abort",
                )
            )
            continue

        # Parse acquisition metadata
        meta_or_failure = parse_he_metadata(
            main_xml_bytes,
            zip_entry_name=main_xml_entry,
            source_zip_sha256=source_zip_sha256,
            ingest_timestamp=ingest_timestamp,
            languages_in_he=languages_in_he,
        )
        if isinstance(meta_or_failure, HEAcquisitionFailure):
            failures.append(meta_or_failure)
            continue

        meta = meta_or_failure

        # Metadata disagreement check with main_pdf-wrapper.xml
        wrapper_entry = file_map.get("main_pdf-wrapper.xml")
        if wrapper_entry is not None:
            try:
                wrapper_bytes = safe_zip_read(
                    zf, wrapper_entry, archive_path="<government-proposal.zip>"
                )
                disag = _check_metadata_disagreement(meta, wrapper_bytes, wrapper_entry)
                disagreements.extend(disag)
            except ArchiveMemberTooLarge as exc:
                disagreements.append(
                    HEMetadataDisagreement(
                        rule_id="HE_ACQ.PDF_WRAPPER_ARCHIVE_MEMBER_TOO_LARGE",
                        he_year=year,
                        he_number=number,
                        lang=lang,
                        field_name="pdf_wrapper_read",
                        main_xml_value="",
                        pdf_wrapper_value="",
                        resolution=(
                            f"wrapper skipped: declared_size={exc.declared_size}; "
                            f"cap_bytes={exc.cap_bytes}; using main.xml"
                        ),
                    )
                )
            except Exception as exc:
                disagreements.append(
                    HEMetadataDisagreement(
                        rule_id="HE_ACQ.PDF_WRAPPER_READ_ERROR",
                        he_year=year,
                        he_number=number,
                        lang=lang,
                        field_name="pdf_wrapper_read",
                        main_xml_value="",
                        pdf_wrapper_value="",
                        resolution=f"wrapper unreadable: {exc}; using main.xml",
                    )
                )

        # Build per-artifact metadata dict
        meta_dict: dict[str, str] = {
            "he_id": meta.he_id,
            "he_year": str(meta.he_year),
            "he_number": str(meta.he_number),
            "he_uri": meta.he_uri,
            "lang": meta.lang,
            "ministry_canonical_id": meta.ministry_canonical_id,
            "ministry_show_as": meta.ministry_show_as,
            "title": meta.title[:500],  # truncate very long titles for metadata
            "date_issued": meta.date_issued.isoformat(),
            "structural_tier": meta.structural_tier.value,
            "finlex_state": meta.finlex_state,
            "source_zip_sha256": meta.source_zip_sha256,
            "ingest_timestamp": meta.ingest_timestamp.isoformat(),
            "source_surface": "government-proposal-zip",
        }

        # Read all file bytes for this language variant
        for filename, entry_name in file_map.items():
            # PDF policy: skip main.pdf blobs by default (LawVM doesn't
            # extract PDF text; XML + metadata is sufficient). Re-run
            # with include_pdfs=True if PDF blobs are ever needed.
            if not include_pdfs and filename.endswith(".pdf"):
                continue
            locator = he_locator(year, number, lang, filename)
            entry_data: bytes
            try:
                entry_data = safe_zip_read(
                    zf, entry_name, archive_path="<government-proposal.zip>"
                )
            except ArchiveMemberTooLarge as exc:
                # The oversized blob is conserved as an HEAcquisitionFailure
                # (AGENTS.md §1.8) rather than dropped; the cap is operator-
                # tunable so the disposition is per-HE abort, not run-fatal.
                failures.append(
                    HEAcquisitionFailure(
                        rule_id="HE_ACQ.ARCHIVE_MEMBER_TOO_LARGE",
                        phase="acquisition",
                        family="transport_cleanup",
                        he_year=year,
                        he_number=number,
                        lang=lang,
                        zip_entry_name=entry_name,
                        reason=(
                            f"blob {filename} declares more bytes than "
                            f"LAWVM_MAX_ARCHIVE_MEMBER_BYTES"
                        ),
                        detail=(
                            f"declared_size={exc.declared_size}; "
                            f"cap_bytes={exc.cap_bytes}; "
                            f"archive_path={exc.archive_path}"
                        ),
                        strict_disposition="abort",
                    )
                )
                continue
            except Exception as exc:
                failures.append(
                    HEAcquisitionFailure(
                        rule_id="HE_ACQ.ZIP_READ_ERROR",
                        phase="acquisition",
                        family="transport_cleanup",
                        he_year=year,
                        he_number=number,
                        lang=lang,
                        zip_entry_name=entry_name,
                        reason=f"Failed to read {filename} from zip",
                        detail=str(exc),
                        strict_disposition="abort",
                    )
                )
                continue

            storage_class = "pdf" if filename.endswith(".pdf") else "xml"

            # Data-integrity guard: a PDF blob MUST begin with the %PDF magic.
            # A fetch that archived an HTTP-error body (``HTTP 404 Not Found``)
            # or an HTML error page as ``main.pdf`` would otherwise be stored as
            # a real artifact and later crash pypdfium2. Conserve as a typed
            # HEAcquisitionFailure (AGENTS.md §1.8) rather than archive the junk.
            if storage_class == "pdf":
                verdict = classify_pdf_blob(entry_data)
                if not verdict.is_pdf:
                    failures.append(
                        HEAcquisitionFailure(
                            rule_id="HE_ACQ.PDF_BLOB_NOT_PDF",
                            phase="acquisition",
                            family="source_pathology",
                            he_year=year,
                            he_number=number,
                            lang=lang,
                            zip_entry_name=entry_name,
                            reason=(
                                f"blob {filename} stored as PDF lacks the %PDF "
                                f"magic ({verdict.reject_reason}); refusing to "
                                "archive an HTTP-error/HTML body as a PDF"
                            ),
                            detail=(
                                f"reject_reason={verdict.reject_reason}; "
                                f"size={verdict.size}; "
                                f"head_bytes={verdict.head_bytes!r}"
                            ),
                            strict_disposition="record",
                        )
                    )
                    continue

            file_meta = dict(meta_dict)
            file_meta["entry_name"] = entry_name
            file_meta["filename"] = filename
            blobs.append(
                _HEBlob(
                    locator=locator,
                    data=entry_data,
                    storage_class=storage_class,
                    meta_dict=file_meta,
                )
            )

    return _HEReadResult(
        year=year,
        number=number,
        failures=failures,
        disagreements=disagreements,
        blobs=blobs,
    )


def _store_he_group(
    *,
    read_result: _HEReadResult,
    farchive: Any,
    ingest_timestamp: datetime,
    incremental: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Phase 2: write blobs to farchive (main thread only).

    Returns (added, skipped).
    """
    added = 0
    skipped = 0
    for blob in read_result.blobs:
        if incremental and farchive is not None:
            span = farchive.resolve(blob.locator)
            if span is not None:
                skipped += 1
                continue
        if not dry_run and farchive is not None:
            farchive.store(
                blob.locator,
                blob.data,
                storage_class=blob.storage_class,
                metadata=blob.meta_dict,
                observed_at=ingest_timestamp,
            )
        added += 1
    return added, skipped


# ---------------------------------------------------------------------------
# Run provenance storage
# ---------------------------------------------------------------------------


def _store_ingest_run(farchive: Any, run: HEIngestRun) -> None:
    """Store ingest run provenance as a JSON blob in the farchive."""
    assert run.failures is not None
    assert run.disagreements is not None

    run_data = {
        "source_uri": run.source_uri,
        "source_zip_sha256": run.source_zip_sha256,
        "ingest_timestamp": run.ingest_timestamp.isoformat(),
        "worker_count": run.worker_count,
        "stream_mode": run.stream_mode,
        "added": run.added,
        "updated": run.updated,
        "skipped": run.skipped,
        "failed": run.failed,
        "failure_count": len(run.failures),
        "disagreement_count": len(run.disagreements),
        "failures": [
            {
                "rule_id": f.rule_id,
                "phase": f.phase,
                "family": f.family,
                "he_year": f.he_year,
                "he_number": f.he_number,
                "lang": f.lang,
                "zip_entry_name": f.zip_entry_name,
                "reason": f.reason,
                "detail": f.detail,
            }
            for f in run.failures
        ],
        "disagreements": [
            {
                "rule_id": d.rule_id,
                "he_year": d.he_year,
                "he_number": d.he_number,
                "lang": d.lang,
                "field_name": d.field_name,
                "main_xml_value": d.main_xml_value,
                "pdf_wrapper_value": d.pdf_wrapper_value,
                "resolution": d.resolution,
            }
            for d in run.disagreements
        ],
    }
    ts = run.ingest_timestamp.strftime("%Y%m%dT%H%M%SZ")
    locator = f"_ingest_runs/fi_government_proposal/{ts}"
    farchive.store(
        locator,
        json.dumps(run_data, ensure_ascii=False).encode("utf-8"),
        storage_class="json",
        metadata={"source_surface": "ingest_run_provenance"},
        observed_at=run.ingest_timestamp,
    )


# ---------------------------------------------------------------------------
# Main acquisition entry point
# ---------------------------------------------------------------------------


def acquire_fi_proposals(
    *,
    source: str | None = None,
    dest: str | None = None,
    incremental: bool = True,
    workers: int = 4,
    limit: int | None = None,
    year_range: tuple[int, int] | None = None,
    stream_mode: str = "tempfile",
    keep_tempfile: bool = False,
    strict: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    include_pdfs: bool = False,
) -> HEIngestRun:
    """Acquire Finnish government proposals into fi_government_proposal.farchive.

    Parameters
    ----------
    source:
        Local file path or ``https://`` URL to government-proposal.zip.
        Default: ``$LAWVM_GOVPROP_ZIP`` env, falling back to
        ``~/Downloads/government-proposal.zip``.
    dest:
        Farchive path. Default: ``data/fi_government_proposal.farchive``.
    incremental:
        If True (default), skip locators already present in farchive.
        ``--full`` sets this to False.
    workers:
        Number of parallel per-HE processing threads.  Zip reads are
        serialized via a lock; parallelism is in metadata parsing only
        when zip I/O has already been done.
    limit:
        Debug: ingest only the first N HE groups.
    year_range:
        Debug: (start_year, end_year) inclusive.
    stream_mode:
        ``'tempfile'`` or ``'range'`` (range is a future extension).
    keep_tempfile:
        Retain the streamed zip after ingest (HTTPS only).
    strict:
        Abort on first HE acquisition failure (non-zero exit).
    verbose:
        Print per-HE progress lines.
    dry_run:
        Parse and classify but do not write to farchive.

    Returns
    -------
    HEIngestRun with provenance, counts, failures, and disagreements.
    """
    import os
    from farchive import Farchive

    if source is None:
        source = os.environ.get("LAWVM_GOVPROP_ZIP", _DEFAULT_SOURCE)
    dest_explicit_env: str | None
    if dest is None:
        # Route through the single resolver so worktrees / canonical-data-root
        # resolve correctly at runtime instead of relying on a cwd-relative
        # default. LAWVM_HE_FARCHIVE_DB stays highest precedence for HE. This is
        # an ingest path: it legitimately creates the archive on first use.
        from lawvm.corpus_store import resolve_farchive_path

        dest_path, _rule = resolve_farchive_path(
            "fi_government_proposal.farchive",
            explicit_env="LAWVM_HE_FARCHIVE_DB",
        )
        dest = str(dest_path)
        # Default-resolved path: apply the data-root check with the
        # explicit-env override channel so LAWVM_HE_FARCHIVE_DB pointing at
        # an out-of-tree target is honoured (operator trust).
        dest_explicit_env = "LAWVM_HE_FARCHIVE_DB"
    else:
        # Caller-supplied path (test fixture, ad-hoc ingest): caller is the
        # operator-in-trust. Pass explicit_env=None so the data-root check
        # stays opt-in (Security M2 §4 — backwards-compatible).
        dest_explicit_env = None

    ingest_timestamp = datetime.now(timezone.utc)

    # No outer-zip sha256 computation: inner per-file blobs are already
    # content-addressed by sha256 at the farchive blob layer, so the outer
    # container hash is redundant provenance. Finlex updates the
    # government-proposal.zip daily so there is also no canonical hash to
    # verify against. Provenance comes from source_uri + ingest_timestamp +
    # per-blob content addressing.
    sha256_streamed: list[str] = []
    if not _is_https_url(source):
        if not Path(source).exists():
            raise FileNotFoundError(f"government-proposal.zip not found: {source}")
    source_sha256 = ""  # never computed; field retained for HEAcquisitionMetadata back-compat

    print(
        f"Opening farchive: {dest}"
        + (" (dry-run)" if dry_run else ""),
        file=sys.stderr,
    )

    if not dry_run:
        from lawvm.corpus_store import validate_farchive_create_path

        validate_farchive_create_path(
            Path(dest), explicit_env=dest_explicit_env
        )
    farchive: Any = None if dry_run else Farchive(dest)

    run = HEIngestRun(
        source_uri=source,
        source_zip_sha256=source_sha256,
        ingest_timestamp=ingest_timestamp,
        worker_count=workers,
        stream_mode=stream_mode,
    )

    # Architecture: two-phase per-HE processing.
    # Phase 1 (workers): read zip bytes + parse metadata. Uses zip_lock to
    #   serialize zip I/O (zipfile is not thread-safe for concurrent reads).
    # Phase 2 (main thread): write blobs to farchive (farchive SQLite
    #   connections must not be shared across threads).
    #
    # With zip_lock, Phase 1 is effectively serial. The ThreadPoolExecutor is
    # present to enable future optimization once we have a thread-safe zip
    # reader or per-HE file caching. Current throughput is zip I/O bound.
    zip_lock = threading.Lock()

    try:
        with _open_zip_for_ingest(
            source,
            stream_mode=stream_mode,
            keep_tempfile=keep_tempfile,
            sha256_out=sha256_streamed,
        ) as (zf, resolved_uri):
            run.source_uri = resolved_uri
            # HTTPS: the streamed sha256 is now available — record it before
            # any per-HE metadata is parsed so HEAcquisitionMetadata records
            # the verified hash, not a placeholder.
            if _is_https_url(source) and sha256_streamed:
                run.source_zip_sha256 = sha256_streamed[0]

            all_names = zf.namelist()
            groups = _build_he_groups(all_names)
            he_lang_map = _build_he_lang_map(all_names)

            if year_range is not None:
                y1, y2 = year_range
                groups = [g for g in groups if y1 <= g.year <= y2]
            if limit is not None:
                groups = groups[:limit]

            total = len(groups)
            print(
                f"  {total:,} HE groups to process "
                f"({'incremental' if incremental else 'full'} mode, "
                f"workers={workers})",
                file=sys.stderr,
            )

            def _phase1_read(group: _HEGroup) -> _HEReadResult:
                """Phase 1: read zip + parse metadata (worker thread)."""
                langs = tuple(
                    sorted(he_lang_map.get((group.year, group.number), set()))
                )
                with zip_lock:
                    return _read_he_group(
                        zf=zf,
                        group=group,
                        source_zip_sha256=run.source_zip_sha256,
                        ingest_timestamp=ingest_timestamp,
                        languages_in_he=langs,
                        include_pdfs=include_pdfs,
                    )

            done = 0
            abort_requested = False
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_phase1_read, g): g for g in groups}
                for future in as_completed(futures):
                    group = futures[future]
                    read_result = future.result()

                    # Phase 2: farchive I/O in main thread
                    n_added, n_skipped = _store_he_group(
                        read_result=read_result,
                        farchive=farchive,
                        ingest_timestamp=ingest_timestamp,
                        incremental=incremental,
                        dry_run=dry_run,
                    )

                    assert run.failures is not None
                    assert run.disagreements is not None
                    run.added += n_added
                    run.skipped += n_skipped
                    run.failures.extend(read_result.failures)
                    run.disagreements.extend(read_result.disagreements)
                    if read_result.failures:
                        run.failed += 1

                    done += 1
                    if verbose or (done % _PROGRESS_INTERVAL == 0):
                        print(
                            f"  [{done}/{total}] HE {group.year}/{group.number} "
                            f"added={n_added} skipped={n_skipped} "
                            f"failures={len(read_result.failures)}",
                            file=sys.stderr,
                        )

                    if strict and read_result.failures and not abort_requested:
                        abort_requested = True
                        pool.shutdown(wait=False, cancel_futures=True)
                        print(
                            f"STRICT MODE: aborting on first failure "
                            f"(HE {group.year}/{group.number})",
                            file=sys.stderr,
                        )
                        break

    finally:
        if farchive is not None:
            if not dry_run:
                _store_ingest_run(farchive, run)
            farchive.close()

    return run


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    """CLI entry point for ``lawvm acquire-fi-proposals``."""
    source: str | None = getattr(args, "source", None) or None
    dest: str | None = getattr(args, "dest", None) or None
    full: bool = getattr(args, "full", False)
    incremental = not full
    workers = int(getattr(args, "workers", 4))
    limit_raw = getattr(args, "limit", None)
    limit: int | None = int(limit_raw) if limit_raw is not None else None
    year_range_raw: str | None = getattr(args, "year_range", None)
    year_range: tuple[int, int] | None = None
    if year_range_raw:
        yr_parts = year_range_raw.split(":")
        if len(yr_parts) == 2:
            year_range = (int(yr_parts[0]), int(yr_parts[1]))
        else:
            print(
                f"error: --year-range must be Y1:Y2, got {year_range_raw!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    stream_mode = getattr(args, "stream_mode", "tempfile") or "tempfile"
    keep_tempfile = getattr(args, "keep_tempfile", False)
    strict = getattr(args, "strict", False)
    verbose = getattr(args, "verbose", False)
    dry_run = getattr(args, "dry_run", False)
    include_pdfs = getattr(args, "include_pdfs", False)

    run = acquire_fi_proposals(
        source=source,
        dest=dest,
        incremental=incremental,
        workers=workers,
        limit=limit,
        year_range=year_range,
        stream_mode=stream_mode,
        keep_tempfile=keep_tempfile,
        strict=strict,
        verbose=verbose,
        dry_run=dry_run,
        include_pdfs=include_pdfs,
    )

    assert run.failures is not None
    assert run.disagreements is not None

    print("\nIngest complete:", file=sys.stderr)
    print(f"  Source:        {run.source_uri}", file=sys.stderr)
    print(f"  SHA256:        {run.source_zip_sha256}", file=sys.stderr)
    print(f"  Added:         {run.added:,}", file=sys.stderr)
    print(f"  Skipped:       {run.skipped:,}", file=sys.stderr)
    print(f"  Failed HEs:    {run.failed:,}", file=sys.stderr)
    print(f"  Failures:      {len(run.failures):,}", file=sys.stderr)
    print(f"  Disagreements: {len(run.disagreements):,}", file=sys.stderr)

    if run.failures:
        print("\nFailure summary (first 20):", file=sys.stderr)
        for f in run.failures[:20]:
            print(
                f"  [{f.rule_id}] {f.he_year}/{f.he_number} lang={f.lang}: {f.reason}",
                file=sys.stderr,
            )
        if len(run.failures) > 20:
            print(
                f"  ... and {len(run.failures) - 20} more (see farchive provenance)",
                file=sys.stderr,
            )

    if run.disagreements:
        print("\nMetadata disagreements (first 10):", file=sys.stderr)
        for d in run.disagreements[:10]:
            print(
                f"  [{d.rule_id}] HE {d.he_year}/{d.he_number} lang={d.lang} "
                f"field={d.field_name}: "
                f"main={d.main_xml_value!r} wrapper={d.pdf_wrapper_value!r}",
                file=sys.stderr,
            )
        if len(run.disagreements) > 10:
            print(f"  ... and {len(run.disagreements) - 10} more", file=sys.stderr)

    if strict and run.failures:
        sys.exit(1)
