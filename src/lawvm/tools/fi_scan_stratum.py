"""``lawvm fi-scan-stratum`` — census the SCANNED / text-poor stratum of the finlex PDF corpus.

Vision transcription (spec §8/§9 re-read + region-decomposition) only bites on
image-based / scanned pages: a born-digital page just span-copies the pdfium text
layer and barely exercises the VLM. To know where vision fidelity actually
matters, we need the corpus subset whose pages carry a near-empty text layer.

This is a READ-ONLY inventory CLI, ADDITIVE to the ingest pipeline (it never
touches ``parse_attachments_into_store`` / the derived-IR store). For every
``finlex://`` PDF in ``finlex.farchive`` (skipping ``https://`` externals and
``media/corrigenda/``) it measures the pdfium text-layer coverage per page and
classifies the document into a stratum:

- ``born_digital`` — dense text layer (mean chars/page well above the threshold);
  span-copy owns the page, vision barely fires.
- ``mixed`` — partial text layer (some pages dense, some text-poor); the
  region-decomposition seam bites on the poor pages.
- ``scanned`` — near-zero text layer (image-baked / OCR-less scan); every page
  needs a vision or OCR producer.

The ``scanned`` + text-poor list IS the vision-fidelity hard-case test set;
``--stratum scanned`` emits just that subset for downstream harnesses.

Determinism (AGENTS.md §0): the measurement is a pure function of the PDF bytes
(pdfium text-layer char counts), the classification a pure threshold function of
the per-page counts, and the CSV is content-key sorted by locator. Concurrency is
PER-PDF (a ThreadPool like ``fi_parse_attachments``; each worker opens its OWN
Farchive connection — Farchive is SQLite ``check_same_thread`` thread-affine);
pdfium page iteration stays serial within a document. A PDF that cannot be read
is a TYPED ``unreadable`` record, never a crash and never a silently dropped doc.
"""
from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

# pypdfium2 wraps the pdfium C library, whose state is PROCESS-GLOBAL and NOT
# thread-safe: concurrent PdfDocument use across threads segfaults. We keep the
# ThreadPool (so farchive I/O — the SQLite read of each PDF's bytes — overlaps)
# but serialise every pdfium critical section (open → text extract → close)
# under this module lock. The CPU-bound parse runs one-at-a-time; the I/O does
# not. (Rendering-serial-within-a-doc is thus generalised to serial across docs,
# which pdfium requires.)
_PDFIUM_LOCK = threading.Lock()

_FINLEX_DEFAULT = "data/finlex.farchive"

# Same bounded in-flight window rationale as fi_parse_attachments: keep a short
# queue of whole-PDF text-layer reads without overrunning I/O. Distinct PDFs run
# in parallel; a single PDF's pages stay serial.
_DEFAULT_WORKERS = 6

# Classification thresholds (mean stripped text-layer chars per page).
#
# Calibrated on a 200-PDF random sample of the non-corrigenda corpus, whose
# distribution is cleanly tri-modal: a spike at mean == 0 (fully image-baked
# scans), a low band clustered under ~135 chars/page, then a wide natural gap up
# to ~330 before the dense born-digital band (typically 700+ chars/page). The
# thresholds sit in that gap so the boundary is robust to sampling noise.
#
#   mean < SCANNED_MAX              → scanned    (near-zero text layer)
#   SCANNED_MAX <= mean < DIGITAL_MIN → mixed    (partial text layer)
#   mean >= DIGITAL_MIN            → born_digital (dense text layer)
SCANNED_MAX_CHARS_PER_PAGE = 50.0
DIGITAL_MIN_CHARS_PER_PAGE = 300.0

STRATUM_SCANNED = "scanned"
STRATUM_MIXED = "mixed"
STRATUM_BORN_DIGITAL = "born_digital"
STRATUM_UNREADABLE = "unreadable"

# The vision-fidelity HARD CASE set: strata where §8/§9 re-read / region
# decomposition actually fire (some or all pages are text-poor).
HARD_CASE_STRATA = (STRATUM_SCANNED, STRATUM_MIXED)


def iter_finlex_pdf_locators(finlex_path: str = _FINLEX_DEFAULT) -> Iterator[str]:
    """Yield the in-scope ``finlex://`` PDF locators (sorted, deterministic).

    In scope: locators ending ``.pdf`` under the ``finlex://`` scheme. Out of
    scope: ``https://`` externals and ``media/corrigenda/`` (the corrigendum
    attachments — a separate stratum, not statute-body scan candidates). The
    locator scheme is
    ``finlex://sd[-cons]/<year>/<num>/<lang>[@<ver>]/media/<name>.pdf`` (verified
    against the live archive, not invented).
    """
    from farchive import Farchive

    fa = Farchive(finlex_path)
    try:
        locs = [
            loc
            for loc in fa.locators()
            if loc.endswith(".pdf")
            and loc.startswith("finlex://")
            and "media/corrigenda/" not in loc
        ]
    finally:
        fa.close()
    yield from sorted(locs)


def classify(mean_chars_per_page: float) -> str:
    """Pure threshold classifier over the mean stripped text-layer chars/page."""
    if mean_chars_per_page < SCANNED_MAX_CHARS_PER_PAGE:
        return STRATUM_SCANNED
    if mean_chars_per_page < DIGITAL_MIN_CHARS_PER_PAGE:
        return STRATUM_MIXED
    return STRATUM_BORN_DIGITAL


def page_text_char_counts(pdf_bytes: bytes) -> List[int]:
    """Per-page stripped text-layer char counts via the pdfium text layer.

    Deterministic pure function of the PDF bytes. pypdfium2 is imported lazily
    (the ``pdf`` extra) so importing this module never requires the lib; a build
    without it surfaces the ``ImportError`` at call time (the caller types it as
    ``unreadable``). Pages iterate serially within the document.
    """
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    with _PDFIUM_LOCK:  # pdfium C state is process-global + not thread-safe
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            counts: List[int] = []
            for i in range(len(doc)):
                page = doc[i]
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    # Best-effort release; older pypdfium2 auto-closes with the doc.
                    close = getattr(textpage, "close", None)
                    if close is not None:
                        close()
                counts.append(len(text.strip()))
            return counts
        finally:
            doc.close()


@dataclass(frozen=True, slots=True)
class PdfStratumRecord:
    """One PDF's text-layer coverage measurement + stratum verdict."""

    locator: str
    n_pages: int
    mean_text_chars_per_page: float
    min_page_chars: int
    stratum: str

    def as_row(self) -> Tuple[str, int, str, int, str]:
        return (
            self.locator,
            self.n_pages,
            f"{self.mean_text_chars_per_page:.1f}",
            self.min_page_chars,
            self.stratum,
        )


def measure_pdf(locator: str, pdf_bytes: bytes) -> PdfStratumRecord:
    """Measure text-layer coverage of one PDF → a typed stratum record.

    Empty (zero-page) documents are ``scanned`` by construction (no text layer to
    span-copy). Never raises for measurable bytes; unreadable bytes are handled by
    the worker as a typed ``unreadable`` record.
    """
    counts = page_text_char_counts(pdf_bytes)
    if not counts:
        return PdfStratumRecord(
            locator=locator,
            n_pages=0,
            mean_text_chars_per_page=0.0,
            min_page_chars=0,
            stratum=STRATUM_SCANNED,
        )
    mean = sum(counts) / len(counts)
    return PdfStratumRecord(
        locator=locator,
        n_pages=len(counts),
        mean_text_chars_per_page=mean,
        min_page_chars=min(counts),
        stratum=classify(mean),
    )


def _measure_one(locator: str, *, finlex_path: str) -> PdfStratumRecord:
    """Load + measure ONE PDF in a worker thread; never raises.

    Opens its OWN Farchive connection in THIS thread (Farchive is SQLite
    ``check_same_thread`` thread-affine — one connection per thread, not a shared
    handle). A PDF that cannot be resolved / read / parsed becomes a typed
    ``unreadable`` record (AGENTS.md §1.8), never a crash that sinks the pool.
    """
    from farchive import Farchive

    fa = Farchive(finlex_path)
    try:
        span = fa.resolve(locator)
        if span is None:
            raise ValueError("locator not resolvable")
        pdf_bytes = fa.read(span.digest)
        if not pdf_bytes:
            raise ValueError("empty bytes")
        return measure_pdf(locator, pdf_bytes)
    except Exception:  # a bad PDF is a typed record, not a crash (§1.8)
        return PdfStratumRecord(
            locator=locator,
            n_pages=0,
            mean_text_chars_per_page=0.0,
            min_page_chars=0,
            stratum=STRATUM_UNREADABLE,
        )
    finally:
        fa.close()


@dataclass(frozen=True, slots=True)
class ScanStratumReport:
    """Whole-corpus census: per-PDF records (content-key sorted) + per-stratum counts."""

    records: Tuple[PdfStratumRecord, ...]
    counts: Tuple[Tuple[str, int], ...]
    """``(stratum, count)`` in a fixed stratum order (aggregate line)."""

    @property
    def hard_case(self) -> Tuple[PdfStratumRecord, ...]:
        """The vision-fidelity hard-case subset (scanned + mixed)."""
        return tuple(r for r in self.records if r.stratum in HARD_CASE_STRATA)


_STRATUM_ORDER = (
    STRATUM_BORN_DIGITAL,
    STRATUM_MIXED,
    STRATUM_SCANNED,
    STRATUM_UNREADABLE,
)


def _aggregate(records: Sequence[PdfStratumRecord]) -> Tuple[Tuple[str, int], ...]:
    by_stratum = {s: 0 for s in _STRATUM_ORDER}
    for r in records:
        by_stratum[r.stratum] = by_stratum.get(r.stratum, 0) + 1
    return tuple((s, by_stratum[s]) for s in _STRATUM_ORDER)


def census_scan_strata(
    *,
    finlex_path: str = _FINLEX_DEFAULT,
    limit: int | None = None,
    workers: int = _DEFAULT_WORKERS,
    locators: Optional[Sequence[str]] = None,
) -> ScanStratumReport:
    """Census the finlex PDF corpus into text-layer strata (deterministic).

    ``workers`` whole PDFs are measured in parallel (each its own Farchive
    connection); a single PDF's pages stay serial inside ``page_text_char_counts``.
    Records are returned content-key sorted by locator (stable regardless of the
    order futures complete). ``locators`` overrides enumeration (test seam).
    """
    if locators is None:
        locs = list(iter_finlex_pdf_locators(finlex_path))
    else:
        locs = sorted(locators)
    if limit is not None:
        locs = locs[:limit]

    records: List[PdfStratumRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_measure_one, loc, finlex_path=finlex_path): loc for loc in locs
        }
        for fut in as_completed(futures):
            records.append(fut.result())

    records.sort(key=lambda r: r.locator)  # content-key stable order
    return ScanStratumReport(records=tuple(records), counts=_aggregate(records))


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

_CSV_HEADER = "locator,n_pages,mean_text_chars_per_page,min_page_chars,stratum"


def _csv_field(value: str) -> str:
    """Minimal RFC-4180 quoting (locators have no commas today, but be safe)."""
    if any(c in value for c in (",", '"', "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


def render_csv(report: ScanStratumReport, *, stratum: Optional[str] = None) -> str:
    """Deterministic CSV: header, one line per record, then an aggregate line.

    ``stratum`` filters to a single stratum (e.g. ``scanned`` — the hard-case
    subset). The aggregate line always reflects the FULL census counts (an honest
    denominator), prefixed ``#agg`` so it is unambiguous against data rows.
    """
    rows = report.records
    if stratum is not None:
        rows = tuple(r for r in rows if r.stratum == stratum)
    lines = [_CSV_HEADER]
    for r in rows:
        loc, n_pages, mean, min_chars, strat = r.as_row()
        lines.append(f"{_csv_field(loc)},{n_pages},{mean},{min_chars},{strat}")
    agg = ";".join(f"{s}={c}" for s, c in report.counts)
    lines.append(f"#agg,{len(report.records)},{agg},,")
    return "\n".join(lines) + "\n"


def render_json(report: ScanStratumReport, *, stratum: Optional[str] = None) -> str:
    """Deterministic JSON mirror of the CSV (records + aggregate counts)."""
    rows = report.records
    if stratum is not None:
        rows = tuple(r for r in rows if r.stratum == stratum)
    payload = {
        "records": [
            {
                "locator": r.locator,
                "n_pages": r.n_pages,
                "mean_text_chars_per_page": round(r.mean_text_chars_per_page, 1),
                "min_page_chars": r.min_page_chars,
                "stratum": r.stratum,
            }
            for r in rows
        ],
        "counts": {s: c for s, c in report.counts},
        "total": len(report.records),
        "hard_case_count": len(report.hard_case),
        "thresholds": {
            "scanned_max_chars_per_page": SCANNED_MAX_CHARS_PER_PAGE,
            "digital_min_chars_per_page": DIGITAL_MIN_CHARS_PER_PAGE,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-scan-stratum``."""
    report = census_scan_strata(
        finlex_path=args.finlex or _FINLEX_DEFAULT,
        limit=args.limit,
        workers=args.workers if args.workers else _DEFAULT_WORKERS,
    )
    stratum = args.stratum  # None = all
    if args.json:
        out = render_json(report, stratum=stratum)
    else:
        out = render_csv(report, stratum=stratum)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        agg = ";".join(f"{s}={c}" for s, c in report.counts)
        print(f"fi-scan-stratum → {args.out} ({len(report.records)} PDFs; {agg})")
    else:
        print(out, end="")
