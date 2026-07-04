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

# Tier-1 bounded acquisition batch (explicit small list, NOT the full 7,547).
# The three C19 acts whose marginal-note segmentation was proven in #177, plus a
# handful of further clean regnal-year Victorian public acts to exercise the
# relaxed 4-part id parser end-to-end.  The corpus-scale crawl is deferred.
TIER1_REGNAL_BATCH: tuple[str, ...] = (
    "ukpga/Vict/45-46/61",  # Bills of Exchange Act 1882
    "ukpga/Vict/53-54/39",  # Partnership Act 1890
    "ukpga/Vict/38-39/90",  # Public Health Act 1875
    "ukpga/Vict/56-57/71",  # Sale of Goods Act 1893
    "ukpga/Vict/57-58/60",  # Merchant Shipping Act 1894
)

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


# A regnal-year citation names the reign by a monarch abbreviation
# (``Vict``, ``Geo5``, ``Edw7``, ``Will4``, ``Cha2``, ``Eliz2``, ``Ann``, …)
# followed by a regnal-year component that is either a single year (``45``) or a
# hyphenated span across a regnal boundary (``45-46``).  Pre-1963 UK Acts are
# cited this way (e.g. Bills of Exchange Act 1882 = ``ukpga/Vict/45-46/61``)
# rather than by calendar year.  These are structural shape checks (no
# semantic-plane parsing), so plain string predicates suffice — no regex.


def _is_regnal_monarch(seg: str) -> bool:
    """A monarch abbreviation: leading uppercase letter, then alnum (e.g. ``Vict``,
    ``Geo5``, ``Edw7``, ``Will4``)."""
    return bool(seg) and seg[0].isupper() and seg.isalnum()


def _is_regnal_years(seg: str) -> bool:
    """A regnal-year component: ``45`` or a hyphenated span ``45-46`` (digits only,
    at most one hyphen, non-empty on both sides)."""
    if not seg:
        return False
    halves = seg.split("-")
    if len(halves) > 2:
        return False
    return all(h.isdigit() for h in halves)


def _parse_statute_id(statute_id: str) -> tuple[str, str, str]:
    """Parse a UK statute id into URL-path components ``(act_type, year, number)``.

    Accepts two forms, returning a 3-tuple whose middle element is the full
    ``year``-position path (so ``enacted_stub_url`` can reconstruct the URL by a
    simple ``{act_type}/{year}/{number}`` join in both cases):

    - Modern calendar-year: ``ukpga/2020/17`` → ``("ukpga", "2020", "17")``.
    - Regnal-year (pre-1963): ``ukpga/Vict/45-46/61`` →
      ``("ukpga", "Vict/45-46", "61")``.  The two regnal segments (monarch
      abbreviation + regnal-year span) collapse into the middle element; the
      trailing chapter number is the ``number``.

    Rejects anything else so a malformed id fails loudly rather than silently
    building a wrong URL.
    """
    parts = statute_id.strip("/").split("/")
    if not all(parts):
        raise ValueError(
            f"invalid UK statute id: {statute_id!r} "
            "(empty path segment; expected act_type/year/number "
            "or act_type/monarch/regnal-years/chapter)"
        )
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 4:
        act_type, monarch, regnal_years, chapter = parts
        if _is_regnal_monarch(monarch) and _is_regnal_years(regnal_years):
            return act_type, f"{monarch}/{regnal_years}", chapter
        raise ValueError(
            f"invalid UK statute id: {statute_id!r} "
            "(4-part id is not a regnal citation: expected "
            "act_type/monarch/regnal-years/chapter, e.g. ukpga/Vict/45-46/61)"
        )
    raise ValueError(
        f"invalid UK statute id: {statute_id!r} "
        "(expected act_type/year/number or act_type/monarch/regnal-years/chapter)"
    )


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
    fetch_stub_if_missing: bool = False,
    verbose: bool = False,
) -> PdfAcquireReport:
    """Acquire the PDF-only Act's PDF for *statute_id* into the PDF lane.

    Reads the embedded PDF URL from the enacted stub, fetches the PDF politely,
    verifies the ``%PDF-`` signature, and stores it under
    :func:`pdf_lane_locator` with ``storage_class="pdf"``.

    By default the enacted stub must already be in-archive (fetched by the XML
    acquisition lane).  With ``fetch_stub_if_missing=True`` the stub is fetched
    over the network when absent and stored in the XML lane, so a genuinely
    cold statute id (e.g. a PDF-only regnal Act whose stub was never cached)
    can be acquired end-to-end.  The stub fetch shares the same courteous timer.
    """
    report = PdfAcquireReport(statute_id=statute_id)
    if timer is None:
        timer = [0.0]

    stub_url = enacted_stub_url(statute_id)
    stub = archive.get(stub_url)
    if stub is None and fetch_stub_if_missing:
        stub_data, stub_status = _http_get(stub_url, delay=delay, last_time=timer)
        if stub_data is None:
            report.error = (
                f"stub_http_{stub_status}" if stub_status else "stub_transport_error"
            )
            if verbose:
                print(f"  {statute_id}: ERROR {report.error}  {stub_url}")
            return report
        archive.store(stub_url, stub_data, storage_class="xml")
        stub = stub_data
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
    fetch_stub_if_missing: bool = False,
    verbose: bool = False,
) -> PdfSampleReport:
    """Acquire PDFs for a SAMPLE of statutes, sequentially and politely.

    This is the validation entry point for the first increment; it is NOT the
    corpus crawler.  One request at a time with a shared monotonic-timer delay.
    Idempotent/resumable: PDFs (and, with ``fetch_stub_if_missing``, stubs)
    already in-archive are skipped, so a re-run only fetches what is missing.
    """
    timer: list[float] = [0.0]
    sample = PdfSampleReport()
    for sid in statute_ids:
        sample.reports.append(
            acquire_pdf_for_statute(
                sid,
                archive,
                delay=delay,
                timer=timer,
                fetch_stub_if_missing=fetch_stub_if_missing,
                verbose=verbose,
            )
        )
    return sample


# ---------------------------------------------------------------------------
# Corpus-scale worklist enumeration + resumable crawl
# ---------------------------------------------------------------------------
#
# The worklist is NOT a discovered list — every candidate is an enacted stub
# ALREADY in the XML lane that names its own PDF inline (see the module
# docstring).  So enumeration is a pure in-archive scan: no network, fully
# deterministic, and resumable *by construction* — a stub whose PDF blob is
# already in the ``leg://pdf/`` lane is skipped, so re-running the crawl only
# fetches what is still missing.

# An enacted stub locator is the bare legislation.gov.uk enacted-XML URL.
_ENACTED_SUFFIX = "/enacted/data.xml"
_LEG_URL_PREFIX = "https://www.legislation.gov.uk/"

# A metadata-only ("PDF-only") stub declares no provisions: its enacted AND
# current XML are stubs, so the PDF is the *only* text source.  These are the
# ~7,547 acts the PDF lane exists to recover.  The marker is inlined in the
# stub's ``<ukm:...NumberOfProvisions="0">`` attribute.
_PDF_ONLY_MARKER = b'NumberOfProvisions="0"'


def _statute_id_from_enacted_locator(locator: str) -> str | None:
    """Recover ``act_type/year/number`` from an enacted-stub locator, or None.

    ``https://www.legislation.gov.uk/ukpga/1983/38/enacted/data.xml`` ->
    ``ukpga/1983/38``.  Returns None for any locator that is not a bare
    legislation.gov.uk enacted-XML stub (so the scan ignores current/effects
    feeds, PDF-lane blobs, negative-cache markers, etc.).
    """
    if not locator.startswith(_LEG_URL_PREFIX) or not locator.endswith(_ENACTED_SUFFIX):
        return None
    return locator[len(_LEG_URL_PREFIX) : -len(_ENACTED_SUFFIX)]


@dataclass(frozen=True, slots=True)
class PdfWorklistItem:
    """One acquisition-eligible act: its id plus the PDF it names inline."""

    statute_id: str
    alternative: PdfAlternative
    pdf_only: bool


def iter_pdf_worklist(
    archive: Any,
    *,
    pdf_only: bool = True,
    include_cached: bool = False,
) -> "list[PdfWorklistItem]":
    """Scan the archive for acts whose inline-named PDF is not yet in the lane.

    Pure in-archive scan (no network).  For every enacted stub already stored in
    the XML lane, extract its inline PDF ``ukm:Alternative``; an act is on the
    worklist iff it names a PDF whose ``leg://pdf/`` blob is not already stored
    (``include_cached=True`` overrides this, yielding every PDF-named act).

    ``pdf_only=True`` (the default, and the corpus-scale target) restricts to
    metadata-only stubs (``NumberOfProvisions="0"``) — the ~7,547 acts that exist
    upstream *only* as PDF.  ``pdf_only=False`` yields every PDF-named act
    (including those that also have a real XML body).

    Resumable by construction: because it skips PDFs already in the lane, a
    re-run after a partial crawl returns only the still-missing remainder.
    """
    items: list[PdfWorklistItem] = []
    for locator in archive.locators():
        sid = _statute_id_from_enacted_locator(locator)
        if sid is None:
            continue
        stub = archive.get(locator)
        if stub is None:
            continue
        is_pdf_only = _PDF_ONLY_MARKER in stub
        if pdf_only and not is_pdf_only:
            continue
        alt = extract_pdf_url_from_stub(stub)
        if alt is None:
            continue
        if not include_cached and archive.has(pdf_lane_locator(alt.url)):
            continue
        items.append(
            PdfWorklistItem(statute_id=sid, alternative=alt, pdf_only=is_pdf_only)
        )
    # Deterministic order so a bounded (``limit``) crawl is reproducible and a
    # resumed run continues where the previous left off.
    items.sort(key=lambda it: it.statute_id)
    return items


@dataclass
class PdfCrawlReport:
    """Bounded summary of a corpus-scale (resumable) PDF crawl.

    Mirrors the acquire-summary discipline: aggregate counts + a bounded list of
    per-act reports (only the failures are retained in full so the output never
    grows unbounded with corpus size).
    """

    worklist_total: int = 0
    attempted: int = 0
    acquired: int = 0
    already_cached: int = 0
    errors: int = 0
    total_bytes: int = 0
    remaining: int = 0
    error_reports: list[PdfAcquireReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worklist_total": self.worklist_total,
            "attempted": self.attempted,
            "acquired": self.acquired,
            "already_cached": self.already_cached,
            "errors": self.errors,
            "total_bytes": self.total_bytes,
            "remaining": self.remaining,
            "error_reports": [r.to_dict() for r in self.error_reports],
        }


def crawl_pdf_worklist(
    archive: Any,
    *,
    limit: int = 0,
    pdf_only: bool = True,
    delay: float = _DEFAULT_DELAY,
    verbose: bool = False,
    progress_every: int = 25,
) -> PdfCrawlReport:
    """Fetch PDFs for the in-archive worklist, resumable and bounded.

    Enumerates :func:`iter_pdf_worklist` (already-acquired PDFs skipped), then
    fetches up to ``limit`` of them sequentially with the courteous inter-request
    delay.  ``limit=0`` means the whole remaining worklist (for the durable
    full-corpus run in a persistent session — NOT a transient scope).

    Output is bounded: only aggregate counts are printed periodically, and only
    failing per-act reports are retained.  ``remaining`` in the returned report
    is the worklist size minus what this run acquired, so the caller knows how
    much a subsequent resumed run still has to do.
    """
    worklist = iter_pdf_worklist(archive, pdf_only=pdf_only)
    report = PdfCrawlReport(worklist_total=len(worklist))
    batch = worklist if limit <= 0 else worklist[:limit]

    timer: list[float] = [0.0]
    for idx, item in enumerate(batch, start=1):
        r = acquire_pdf_for_statute(
            item.statute_id,
            archive,
            delay=delay,
            timer=timer,
            fetch_stub_if_missing=False,
            verbose=False,
        )
        report.attempted += 1
        if r.error is not None:
            report.errors += 1
            report.error_reports.append(r)
        elif r.already_cached:
            report.already_cached += 1
            report.total_bytes += r.fetched_bytes or 0
        else:
            report.acquired += 1
            report.total_bytes += r.fetched_bytes or 0
        if verbose and (idx % progress_every == 0 or idx == len(batch)):
            print(
                f"  [{idx}/{len(batch)}] acquired={report.acquired} "
                f"cached={report.already_cached} errors={report.errors} "
                f"bytes={report.total_bytes:,}",
                flush=True,
            )

    report.remaining = report.worklist_total - report.acquired
    return report
