"""``lawvm fi-parse-corpus`` — corpus-scale end-to-end A/B over finlex PDFs.

A bulk driver that runs the FULL de-facsimile parse lane over the real PDFs in the
immutable ``finlex.farchive`` and emits a ranked, diffable A/B table: per-PDF
EXTRA / STRUCTURE / MISSING / NUMERIC deltas of the Level-2 de-facsimile
reconstruction against the mechanical ``struct_span`` stitch, both adjudicated by
the local model against the PDF's sibling authoritative ``main.xml`` body. It
reuses ``fi_parse_compare``'s adjudicator + acceptance predicate verbatim — this
CLI only ENUMERATES, SCHEDULES (per-PDF concurrency), and RANKS.

Corpus layout discovered in ``finlex.farchive`` (VERIFIED, not invented):

  * a PDF attachment is  ``finlex://sd[-cons]/<year>/<num>/<lang>[@<ver>]/media/<name>.pdf``
  * its sibling authoritative XML is the SAME manifestation prefix with
    ``main.xml`` in place of ``media/<name>.pdf``:
    ``finlex://sd[-cons]/<year>/<num>/<lang>[@<ver>]/main.xml``
  * ``.../media/corrigenda/<name>.pdf`` locators are versioned corrigenda repeats,
    SKIPPED by default (they are not distinct body content).
  * ``https://`` external members are SKIPPED (earlier operator directive).

At the time of writing: 10 663 ``.pdf`` locators; 6 232 non-corrigenda
``finlex://`` PDFs, ALL 6 232 of which pair with a sibling ``main.xml``.

Success criterion (printed in the table header, spec §2): across the corpus
EXTRA + STRUCTURE strictly DOWN, **MISSING not up**, **NUMERIC unchanged
(=0 delta)**.

Concurrency: PER-PDF, never per-page (a page needs running context to build
structure). ``workers`` whole PDFs are kept in flight to saturate the single
localhost:8080 inference server (the HTTP server owns the request queue); each
PDF's pages stay sequential-with-context inside the parse lane, and pdfium page
rendering stays serial WITHIN a doc. Each worker opens its OWN Farchive +
``ParsedIrStore`` connection (SQLite ``check_same_thread``, WAL, ``busy_timeout``)
— the exact pattern from ``fi_parse_attachments._parse_one``.

The vision + de-facsimile backends are probed ONCE up front (``resolve_pipeline``
raises ``ParseBackendUnavailable`` if the vision server is down); a single bad PDF
is a TYPED per-row failure, never a crash that sinks the pool.
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from lawvm.finland.source_document import FI_PARSED_STORE
from lawvm.tools.fi_parse_compare import (
    DeFacsimileABReport,
    _cat_count,
    evaluate_defacsimile_ab,
    xml_body_text,
)

_FINLEX_DEFAULT = "data/finlex.farchive"
PARSED_STORE_DEFAULT = FI_PARSED_STORE

# Default bounded in-flight window (whole PDFs, per the single-inference-server
# saturation model). Mirrors fi_parse_attachments._DEFAULT_WORKERS.
_DEFAULT_WORKERS = 6

# ``.../media/<name>.pdf`` → the manifestation prefix; the sibling gold is that
# prefix + ``/main.xml``. Corrigenda live under ``.../media/corrigenda/...`` and
# are excluded by the ``[^/]+`` media-leaf constraint.
_MEDIA_PDF_RE = re.compile(r"^(?P<prefix>.+)/media/[^/]+\.pdf$")


@dataclass(frozen=True, slots=True)
class CorpusMember:
    """One enumerated finlex PDF and the sibling XML gold it pairs with (if any)."""

    pdf_locator: str
    xml_locator: Optional[str]

    @property
    def has_xml(self) -> bool:
        return self.xml_locator is not None


def _xml_sibling(pdf_locator: str, present: frozenset) -> Optional[str]:
    """Sibling ``main.xml`` locator for a media PDF, iff it exists in the archive."""
    # lawvm-regex: diagnostic — farchive LOCATOR path transform (.../media/X.pdf → sibling .../main.xml) for corpus A/B gold pairing; a source-plane path derivation, never post-parse legal semantics.
    m = _MEDIA_PDF_RE.match(pdf_locator)
    if m is None:
        return None
    cand = m.group("prefix") + "/main.xml"
    return cand if cand in present else None


def enumerate_corpus(
    finlex_path: str = _FINLEX_DEFAULT,
    *,
    include_corrigenda: bool = False,
) -> List[CorpusMember]:
    """Enumerate finlex PDF members paired with their sibling XML gold.

    SKIPS ``https://`` external members (operator directive) and, by default,
    ``media/corrigenda/`` repeats. Returns a DETERMINISTICALLY sorted list (by PDF
    locator) so scheduling and output ordering are reproducible. The pairing is
    resolved against the archive's own locator set — a PDF whose sibling
    ``main.xml`` is absent is kept as an ``xml_locator=None`` coverage-only member.
    """
    from farchive import Farchive

    fa = Farchive(finlex_path)
    try:
        all_locs = list(fa.locators())
    finally:
        fa.close()
    present = frozenset(all_locs)

    members: List[CorpusMember] = []
    for loc in all_locs:
        if not loc.endswith(".pdf"):
            continue
        if loc.startswith("https://"):
            continue  # external member — skip (operator directive)
        if not include_corrigenda and "/media/corrigenda/" in loc:
            continue
        members.append(CorpusMember(pdf_locator=loc, xml_locator=_xml_sibling(loc, present)))
    members.sort(key=lambda m: m.pdf_locator)
    return members


# --------------------------------------------------------------------------- #
# Per-PDF worker (own Farchive + ParsedIrStore connection, never raises)        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RowResult:
    """One PDF's A/B row — a typed value that outlives the worker's connections.

    ``status`` is ``"ab"`` (full baseline-vs-defacsimile A/B computed),
    ``"coverage_only"`` (no sibling XML → parsed but not adjudicated), or
    ``"failed"`` (a typed per-row failure, detail carried). The count/delta fields
    are populated only for ``"ab"`` rows.
    """

    pdf_locator: str
    xml_locator: Optional[str]
    status: str
    baseline_extra: int = 0
    baseline_structure: int = 0
    baseline_missing: int = 0
    baseline_numeric: int = 0
    extra_delta: int = 0
    structure_delta: int = 0
    missing_delta: int = 0
    numeric_delta: int = 0
    accepted: bool = False
    detail: Optional[str] = None

    @property
    def baseline_findings(self) -> int:
        """EXTRA + STRUCTURE on the baseline stitch — the worst-first sort key."""
        return self.baseline_extra + self.baseline_structure


def _row_from_ab(member: CorpusMember, ab: DeFacsimileABReport) -> RowResult:
    return RowResult(
        pdf_locator=member.pdf_locator,
        xml_locator=member.xml_locator,
        status="ab",
        baseline_extra=_cat_count(ab.baseline, "EXTRA"),
        baseline_structure=_cat_count(ab.baseline, "STRUCTURE"),
        baseline_missing=_cat_count(ab.baseline, "MISSING"),
        baseline_numeric=_cat_count(ab.baseline, "NUMERIC"),
        extra_delta=ab.extra_delta,
        structure_delta=ab.structure_delta,
        missing_delta=ab.missing_delta,
        numeric_delta=ab.numeric_delta,
        accepted=ab.accepted,
    )


def _process_one(
    member: CorpusMember,
    *,
    finlex_path: str,
    store_path: str,
    modality: str,
    max_pages: int,
) -> RowResult:
    """Parse ONE PDF (baseline stitch + de-facsimile) and A/B it → ``RowResult``.

    Opens its OWN Farchive-backed ``ParsedIrStore`` in THIS worker thread (SQLite
    is not thread-safe across a shared connection; the model is one connection per
    thread over a WAL DB). Never raises — a bad PDF or a model hiccup is a typed
    ``"failed"`` row (AGENTS §1.8), so the pool is never sunk by one member.

    Both reconstructions are recovered through the cached ``ParsedIrStore`` (the
    same path ``fi_parse_compare`` uses), then adjudicated against the sibling
    ``main.xml`` gold by ``evaluate_defacsimile_ab`` (which de-hyphenates both PDF
    sides identically before the model sees them). No sibling XML → a parsed-only
    ``"coverage_only"`` row (still exercises the lane; just not adjudicable).
    """
    from farchive import Farchive

    from lawvm.finland.source_document.pdf_profiles import (
        load_manifestation_from_farchive,
    )
    from lawvm.tools.fi_parse_compare import (
        _defacsimile_reconstructed_text,
        _lane_reconstructed_text,
    )

    try:
        manifestation = load_manifestation_from_farchive(
            member.pdf_locator, farchive_path=finlex_path, source_role="attachment"
        )
    except Exception as exc:  # a bad attachment is a typed failure, not a crash
        return RowResult(
            pdf_locator=member.pdf_locator,
            xml_locator=member.xml_locator,
            status="failed",
            detail=f"load: {type(exc).__name__}: {exc}",
        )

    if member.xml_locator is None:
        # Coverage-only: run the de-facsimile lane so the parse is exercised +
        # cached, but there is no gold to adjudicate against.
        try:
            _defacsimile_reconstructed_text(manifestation, max_pages)
        except Exception as exc:
            return RowResult(
                pdf_locator=member.pdf_locator,
                xml_locator=None,
                status="failed",
                detail=f"parse: {type(exc).__name__}: {exc}",
            )
        return RowResult(
            pdf_locator=member.pdf_locator, xml_locator=None, status="coverage_only"
        )

    # Full A/B: read the sibling XML gold, build both reconstructions, adjudicate.
    fa = Farchive(finlex_path)
    try:
        span = fa.resolve(member.xml_locator)
        xml_bytes = fa.read(span.digest) if span is not None else b""
    except Exception as exc:
        return RowResult(
            pdf_locator=member.pdf_locator,
            xml_locator=member.xml_locator,
            status="failed",
            detail=f"xml: {type(exc).__name__}: {exc}",
        )
    finally:
        fa.close()
    if not xml_bytes:
        return RowResult(
            pdf_locator=member.pdf_locator,
            xml_locator=member.xml_locator,
            status="failed",
            detail="xml: empty gold blob",
        )

    try:
        xml_text = xml_body_text(xml_bytes)
        baseline_text = _lane_reconstructed_text(manifestation, max_pages)
        defac_text = _defacsimile_reconstructed_text(manifestation, max_pages)
        ab = evaluate_defacsimile_ab(xml_text, baseline_text, defac_text)
    except Exception as exc:
        return RowResult(
            pdf_locator=member.pdf_locator,
            xml_locator=member.xml_locator,
            status="failed",
            detail=f"adjudicate: {type(exc).__name__}: {exc}",
        )
    return _row_from_ab(member, ab)


# --------------------------------------------------------------------------- #
# Corpus run + aggregate                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CorpusReport:
    """The ranked per-PDF rows + the corpus aggregate over the A/B rows."""

    modality: str
    rows: Tuple[RowResult, ...]  # DETERMINISTIC worst-first order
    n_members: int
    n_ab: int
    n_coverage_only: int
    n_failed: int
    total_extra_delta: int
    total_structure_delta: int
    total_missing_delta: int
    total_numeric_delta: int
    n_accepted: int
    corpus_accepted: bool


def _rank_key(r: RowResult) -> Tuple[int, int, int, str]:
    """Worst-first, deterministic. Primary: most baseline EXTRA+STRUCTURE findings.

    Ties broken by baseline MISSING then baseline NUMERIC (also descending), then
    the PDF locator ascending — a total order over a content key, never dict/set
    iteration order, so two runs sort byte-identically.
    """
    return (-r.baseline_findings, -r.baseline_missing, -r.baseline_numeric, r.pdf_locator)


def _sorted_rows(rows: List[RowResult]) -> Tuple[RowResult, ...]:
    """A/B rows worst-first; then coverage-only and failed rows (locator-sorted).

    Non-A/B rows carry no findings; they sit AFTER the ranked A/B block in a stable,
    content-keyed order (status bucket, then locator) so the whole table is
    deterministic.
    """
    ab = sorted((r for r in rows if r.status == "ab"), key=_rank_key)
    rest = sorted(
        (r for r in rows if r.status != "ab"),
        key=lambda r: (r.status, r.pdf_locator),
    )
    return tuple(ab) + tuple(rest)


def run_corpus(
    members: List[CorpusMember],
    *,
    finlex_path: str = _FINLEX_DEFAULT,
    store_path: str = PARSED_STORE_DEFAULT,
    modality: str = "struct_span",
    max_pages: int = 5000,
    workers: int = _DEFAULT_WORKERS,
    processor: Any = None,
    verbose: bool = False,
) -> CorpusReport:
    """Run the per-PDF A/B over ``members`` with bounded whole-PDF concurrency.

    ``processor`` is the per-member function (defaults to ``_process_one``); tests
    inject a hermetic stub so no real archive / model is needed. Results are
    collected as they complete, then DETERMINISTICALLY ranked worst-first — the
    completion order (nondeterministic under a thread pool) never leaks into the
    table.
    """
    proc = processor or (
        lambda m: _process_one(
            m,
            finlex_path=finlex_path,
            store_path=store_path,
            modality=modality,
            max_pages=max_pages,
        )
    )
    results: List[RowResult] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(proc, m): m for m in members}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if verbose and done % 25 == 0:
                print(f"  ...{done}/{len(members)}", flush=True)

    rows = _sorted_rows(results)
    ab_rows = [r for r in rows if r.status == "ab"]
    total_extra = sum(r.extra_delta for r in ab_rows)
    total_structure = sum(r.structure_delta for r in ab_rows)
    total_missing = sum(r.missing_delta for r in ab_rows)
    total_numeric = sum(r.numeric_delta for r in ab_rows)
    corpus_accepted = (
        bool(ab_rows)
        and (total_extra + total_structure) < 0
        and total_missing <= 0
        and total_numeric == 0
    )
    return CorpusReport(
        modality=modality,
        rows=rows,
        n_members=len(members),
        n_ab=len(ab_rows),
        n_coverage_only=sum(1 for r in rows if r.status == "coverage_only"),
        n_failed=sum(1 for r in rows if r.status == "failed"),
        total_extra_delta=total_extra,
        total_structure_delta=total_structure,
        total_missing_delta=total_missing,
        total_numeric_delta=total_numeric,
        n_accepted=sum(1 for r in ab_rows if r.accepted),
        corpus_accepted=corpus_accepted,
    )


# --------------------------------------------------------------------------- #
# Rendering (deterministic line-based / CSV / JSON)                             #
# --------------------------------------------------------------------------- #

_CSV_HEADER = (
    "rank,pdf_locator,status,base_extra,base_structure,base_missing,base_numeric,"
    "extra_delta,structure_delta,missing_delta,numeric_delta,accepted"
)


def render_table(report: CorpusReport) -> str:
    """Render the ranked A/B table as deterministic CSV (two runs diff empty).

    Header states the success criterion; body is one CSV row per PDF, worst-first;
    a trailing ``AGGREGATE`` row carries the corpus totals + the corpus verdict.
    """
    lines: List[str] = []
    lines.append(
        "# fi-parse-corpus A/B (Level-2 de-facsimile vs mechanical struct_span "
        "stitch, adjudicated against sibling main.xml)"
    )
    lines.append(
        f"# modality={report.modality}  members={report.n_members}  ab={report.n_ab}  "
        f"coverage_only={report.n_coverage_only}  failed={report.n_failed}"
    )
    lines.append(
        "# SUCCESS = EXTRA+STRUCTURE strictly DOWN, MISSING not up, NUMERIC "
        "unchanged (=0). Rows worst-first by baseline EXTRA+STRUCTURE."
    )
    lines.append(_CSV_HEADER)
    for rank, r in enumerate(report.rows, start=1):
        lines.append(
            ",".join(
                str(v)
                for v in (
                    rank,
                    r.pdf_locator,
                    r.status,
                    r.baseline_extra,
                    r.baseline_structure,
                    r.baseline_missing,
                    r.baseline_numeric,
                    r.extra_delta,
                    r.structure_delta,
                    r.missing_delta,
                    r.numeric_delta,
                    int(r.accepted),
                )
            )
        )
    lines.append(
        ",".join(
            str(v)
            for v in (
                "AGGREGATE",
                f"n_accepted={report.n_accepted}/{report.n_ab}",
                "corpus",
                "",
                "",
                "",
                "",
                report.total_extra_delta,
                report.total_structure_delta,
                report.total_missing_delta,
                report.total_numeric_delta,
                int(report.corpus_accepted),
            )
        )
    )
    return "\n".join(lines)


def report_to_json(report: CorpusReport) -> Dict[str, Any]:
    """JSON form of the report — same deterministic row order as the table."""
    return {
        "modality": report.modality,
        "success_criterion": (
            "EXTRA+STRUCTURE strictly down, MISSING not up, NUMERIC unchanged (=0)"
        ),
        "n_members": report.n_members,
        "n_ab": report.n_ab,
        "n_coverage_only": report.n_coverage_only,
        "n_failed": report.n_failed,
        "aggregate": {
            "extra_delta": report.total_extra_delta,
            "structure_delta": report.total_structure_delta,
            "missing_delta": report.total_missing_delta,
            "numeric_delta": report.total_numeric_delta,
            "n_accepted": report.n_accepted,
            "corpus_accepted": report.corpus_accepted,
        },
        "rows": [
            {
                "rank": rank,
                "pdf_locator": r.pdf_locator,
                "xml_locator": r.xml_locator,
                "status": r.status,
                "baseline_extra": r.baseline_extra,
                "baseline_structure": r.baseline_structure,
                "baseline_missing": r.baseline_missing,
                "baseline_numeric": r.baseline_numeric,
                "extra_delta": r.extra_delta,
                "structure_delta": r.structure_delta,
                "missing_delta": r.missing_delta,
                "numeric_delta": r.numeric_delta,
                "accepted": r.accepted,
                "detail": r.detail,
            }
            for rank, r in enumerate(report.rows, start=1)
        ],
    }


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def _select_members(
    members: List[CorpusMember], *, only_with_xml: bool, limit: Optional[int]
) -> List[CorpusMember]:
    """Apply ``--only-with-xml`` then ``--limit`` over the deterministic ordering.

    Limit is applied AFTER the has-xml filter and AFTER the deterministic sort, so
    ``--limit N`` is a stable prefix of the corpus, not a nondeterministic sample.
    """
    pool = [m for m in members if m.has_xml] if only_with_xml else list(members)
    if limit is not None:
        pool = pool[:limit]
    return pool


def _iter_sample(finlex_path: str) -> Iterator[CorpusMember]:  # pragma: no cover
    """Reserved hook for a fixed CI sample; the real driver uses ``enumerate_corpus``."""
    yield from enumerate_corpus(finlex_path)


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-parse-corpus``."""
    from lawvm.ingest.parsed_store import ParseBackendUnavailable, resolve_pipeline

    finlex_path = args.finlex or _FINLEX_DEFAULT
    store_path = args.store or PARSED_STORE_DEFAULT
    modality = args.modality or "struct_span"
    workers = args.workers if args.workers else _DEFAULT_WORKERS

    members = enumerate_corpus(finlex_path, include_corrigenda=bool(args.corrigenda))
    selected = _select_members(
        members, only_with_xml=bool(args.only_with_xml), limit=args.limit
    )
    if not selected:
        raise SystemExit(
            "fi-parse-corpus: no PDFs selected "
            f"(enumerated {len(members)}; --only-with-xml={bool(args.only_with_xml)})"
        )

    # Probe the backend ONCE, up front — fail loud if the vision server is down
    # rather than turning every row into a typed failure (matches the
    # fi-parse-attachments discipline).
    try:
        resolve_pipeline(transcription_modality=modality)
    except ParseBackendUnavailable as exc:
        raise SystemExit(f"fi-parse-corpus: {exc}") from exc

    report = run_corpus(
        selected,
        finlex_path=finlex_path,
        store_path=store_path,
        modality=modality,
        max_pages=args.max_pages,
        workers=workers,
        verbose=bool(args.verbose),
    )

    if args.json:
        payload = json.dumps(report_to_json(report), ensure_ascii=False, indent=2)
        print(payload)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(payload)
    else:
        print(render_table(report))
