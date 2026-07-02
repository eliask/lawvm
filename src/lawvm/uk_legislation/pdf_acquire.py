"""pdf_acquire.py — UK PDF-only Act acquisition lane (first increment).

Background
----------
The UK ``uk_legislation.farchive`` stores only XML/feed/csv locators; it holds
**zero PDF blobs**.  Yet ~7,547 UK acts (overwhelmingly ``ukpga``) exist upstream
*only* as PDF — their enacted AND current XML are metadata stubs
(``NumberOfProvisions="0"``, no ``<Body>``).  Every such stub, however, names its
own PDF inline, e.g.::

    <ukm:Alternatives>
      <ukm:Alternative Date="..." Size="2296521"
        URI="http://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"/>
    </ukm:Alternatives>

So acquisition needs NO discovery: the PDF URL (and often its ``Size``) is already
in the archive.  This module reads that embedded URL from a stub, fetches the PDF
politely, and stores it into a **new PDF archive lane** keyed by a distinct
``leg://.../pdf`` locator with ``storage_class="pdf"`` — so PDF blobs never
collide with the XML locator scheme and can be selected as a lower-authority
replay base downstream.

Scope of THIS increment
-----------------------
- Validate the fetcher on a small SAMPLE of tier-1 acts (post-1963 ``ukpga``,
  cleanest OCR).  This is NOT a mass download of the full 7,547 — be courteous to
  legislation.gov.uk (sequential, UA header, inter-request delay).
- The corpus-scale crawl (resumability, concurrency budget) is deferred; when it
  lands it should reuse :func:`extract_pdf_url_from_stub` and
  :func:`pdf_lane_locator` verbatim.

Reuse
-----
HTTP + rate-limit + store-if-new semantics mirror ``uk_acquire.py`` (same
``LAWVM_USER_AGENT``, same monotonic-timer delay, same digest-dedup ``store``).
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from lawvm.core.http_identity import LAWVM_USER_AGENT
from lawvm.core.xml_parse import parse_corpus_xml
from lxml import etree

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = LAWVM_USER_AGENT

# Polite default: legislation.gov.uk is a public government site. Sequential,
# one request at a time, with a real inter-request gap. 1.0s is deliberately
# conservative for the validation SAMPLE; the eventual corpus crawler may tune
# this with backoff, but the courteous default belongs here.
_DEFAULT_DELAY = 1.0

# XML namespaces used in UK stubs.
_NS_UKM = "http://www.legislation.gov.uk/namespaces/metadata"
_NS_ATOM = "http://www.w3.org/2005/Atom"


# ---------------------------------------------------------------------------
# PDF URL extraction (no network) + PDF lane locator scheme
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PdfAlternative:
    """The PDF ``ukm:Alternative`` named inline by a UK stub."""

    url: str
    size_bytes: int | None = None
    date: str | None = None


def extract_pdf_url_from_stub(stub_xml: bytes) -> PdfAlternative | None:
    """Return the PDF ``ukm:Alternative`` embedded in a UK metadata stub, or None.

    Prefers ``<ukm:Alternatives><ukm:Alternative URI=".../pdfs/..._en.pdf">`` (it
    carries an authoritative ``Size``), falling back to the
    ``<atom:link rel="alternate" type="application/pdf">`` form.  Pure parse —
    no I/O.
    """
    try:
        root = parse_corpus_xml(stub_xml)
    except etree.XMLSyntaxError:
        return None

    # Preferred: ukm:Alternatives/ukm:Alternative with a PDF URI + Size.
    for alt in root.iter(f"{{{_NS_UKM}}}Alternative"):
        uri = alt.get("URI") or ""
        if "/pdfs/" in uri and uri.endswith(".pdf"):
            size = alt.get("Size")
            return PdfAlternative(
                url=uri,
                size_bytes=int(size) if size and size.isdigit() else None,
                date=alt.get("Date"),
            )

    # Fallback: atom:link rel="alternate" type="application/pdf".
    for link in root.iter(f"{{{_NS_ATOM}}}link"):
        if link.get("type") == "application/pdf":
            href = link.get("href") or ""
            if "/pdfs/" in href and href.endswith(".pdf"):
                return PdfAlternative(url=href)

    return None


def pdf_lane_locator(pdf_url: str) -> str:
    """Map an upstream PDF URL into the net-new PDF archive lane locator.

    The XML lanes use the bare legislation.gov.uk URL as the locator.  To keep
    PDF blobs in a *distinct* lane (never colliding with an XML locator, and
    trivially filterable as a lower-authority source), PDFs are stored under a
    ``leg://pdf/`` scheme keyed by the upstream URL's path::

        http://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf
          -> leg://pdf/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf

    Scheme+host are normalised away so ``http``/``https`` variants map to one
    locator (upstream stubs mix both).
    """
    path = pdf_url
    for prefix in ("https://www.legislation.gov.uk/", "http://www.legislation.gov.uk/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return f"leg://pdf/{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# HTTP helper (mirrors uk_acquire._http_get: UA header, monotonic-timer delay)
# ---------------------------------------------------------------------------


def _http_get(
    url: str,
    *,
    delay: float = _DEFAULT_DELAY,
    last_time: list[float] | None = None,
    timeout: float = 120.0,
) -> tuple[bytes | None, int | None]:
    """Fetch *url* with a courteous inter-request delay.

    Returns ``(data, status_code)``; ``data`` is None on transport/HTTP error.
    """
    if last_time is not None and last_time:
        elapsed = time.monotonic() - last_time[0]
        if elapsed < delay:
            time.sleep(delay - elapsed)

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if last_time is not None:
                last_time[:] = [time.monotonic()]
            return data, getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        if last_time is not None:
            last_time[:] = [time.monotonic()]
        return None, exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        if last_time is not None:
            last_time[:] = [time.monotonic()]
        return None, None


def _looks_like_pdf(data: bytes) -> bool:
    """A real PDF starts with the ``%PDF-`` signature (allowing a small BOM/WS
    lead-in). Guards against storing an HTML error page as if it were a PDF."""
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:16]
    return head.startswith(b"%PDF-")


def _store_pdf_if_new(archive: Any, locator: str, data: bytes) -> bool:
    """Store the PDF in the ``pdf`` storage class only if its digest is new.

    Mirrors ``uk_acquire._store_if_new`` — dedups by content digest, and on an
    unchanged re-fetch calls ``observe`` to refresh the confirmation timestamp.
    """
    digest = hashlib.sha256(data).hexdigest()
    spans = archive.history(locator)
    if spans and spans[-1].digest == digest:
        observe = getattr(archive, "observe", None)
        if callable(observe):
            observe(locator, digest)
        return False
    archive.store(locator, data, storage_class="pdf")
    return True


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class PdfAcquireReport:
    """Result of acquiring the PDF for one UK statute."""

    statute_id: str
    pdf_url: str | None = None
    lane_locator: str | None = None
    declared_size: int | None = None
    fetched_bytes: int | None = None
    stored: bool = False
    already_cached: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "statute_id": self.statute_id,
            "pdf_url": self.pdf_url,
            "lane_locator": self.lane_locator,
            "declared_size": self.declared_size,
            "fetched_bytes": self.fetched_bytes,
            "stored": self.stored,
            "already_cached": self.already_cached,
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class PdfSampleReport:
    """Aggregate of a sample acquisition run."""

    reports: list[PdfAcquireReport] = field(default_factory=list)

    @property
    def ok(self) -> list[PdfAcquireReport]:
        return [r for r in self.reports if r.error is None and r.fetched_bytes]

    def to_dict(self) -> dict[str, Any]:
        oks = self.ok
        sizes = sorted(r.fetched_bytes for r in oks if r.fetched_bytes)
        return {
            "requested": len(self.reports),
            "acquired": len(oks),
            "errors": len([r for r in self.reports if r.error]),
            "already_cached": len([r for r in self.reports if r.already_cached]),
            "min_bytes": sizes[0] if sizes else None,
            "median_bytes": sizes[len(sizes) // 2] if sizes else None,
            "max_bytes": sizes[-1] if sizes else None,
            "total_bytes": sum(sizes) if sizes else 0,
            "reports": [r.to_dict() for r in self.reports],
        }


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


def _parse_statute_id(statute_id: str) -> tuple[str, str, str]:
    parts = statute_id.strip("/").split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"invalid UK statute id: {statute_id!r} (expected act_type/year/number)"
        )
    return parts[0], parts[1], parts[2]


def enacted_stub_url(statute_id: str) -> str:
    """Return the enacted-XML stub URL that carries the embedded PDF locator."""
    act_type, year, number = _parse_statute_id(statute_id)
    return f"{_LEG_BASE}/{act_type}/{year}/{number}/enacted/data.xml"


def acquire_pdf_for_statute(
    statute_id: str,
    archive: Any,
    *,
    delay: float = _DEFAULT_DELAY,
    timer: list[float] | None = None,
    verbose: bool = False,
) -> PdfAcquireReport:
    """Acquire the PDF-only Act's PDF for *statute_id* into the PDF lane.

    Reads the embedded PDF URL from the enacted stub (already in-archive),
    fetches the PDF politely, verifies the ``%PDF-`` signature, and stores it
    under :func:`pdf_lane_locator` with ``storage_class="pdf"``.
    """
    report = PdfAcquireReport(statute_id=statute_id)
    if timer is None:
        timer = [0.0]

    stub_url = enacted_stub_url(statute_id)
    stub = archive.get(stub_url)
    if stub is None:
        report.error = "stub_not_in_archive"
        return report

    alt = extract_pdf_url_from_stub(stub)
    if alt is None:
        report.error = "no_pdf_url_in_stub"
        return report

    report.pdf_url = alt.url
    report.declared_size = alt.size_bytes
    locator = pdf_lane_locator(alt.url)
    report.lane_locator = locator

    if archive.has(locator):
        report.already_cached = True
        cached = archive.get(locator)
        report.fetched_bytes = len(cached) if cached else None
        if verbose:
            print(f"  {statute_id}: cached  {locator}")
        return report

    data, status = _http_get(alt.url, delay=delay, last_time=timer)
    if data is None:
        report.error = f"http_{status}" if status else "transport_error"
        if verbose:
            print(f"  {statute_id}: ERROR {report.error}  {alt.url}")
        return report
    if not _looks_like_pdf(data):
        report.error = "not_a_pdf"
        if verbose:
            print(f"  {statute_id}: ERROR not_a_pdf ({len(data)} bytes)  {alt.url}")
        return report

    report.fetched_bytes = len(data)
    report.stored = _store_pdf_if_new(archive, locator, data)
    if verbose:
        print(f"  {statute_id}: fetched  {len(data):,} bytes  -> {locator}")
    return report


def acquire_pdf_sample(
    statute_ids: list[str],
    archive: Any,
    *,
    delay: float = _DEFAULT_DELAY,
    verbose: bool = False,
) -> PdfSampleReport:
    """Acquire PDFs for a SAMPLE of statutes, sequentially and politely.

    This is the validation entry point for the first increment; it is NOT the
    corpus crawler.  One request at a time with a shared monotonic-timer delay.
    """
    timer: list[float] = [0.0]
    sample = PdfSampleReport()
    for sid in statute_ids:
        sample.reports.append(
            acquire_pdf_for_statute(
                sid, archive, delay=delay, timer=timer, verbose=verbose
            )
        )
    return sample
