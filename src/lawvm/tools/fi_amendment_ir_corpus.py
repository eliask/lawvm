"""``lawvm fi-amendment-ir-corpus`` — corpus driver for the amendment-IR op-diff.

:mod:`lawvm.tools.fi_amendment_ir_compare` answers the product-level accuracy
question for ONE statute: does the PDF→IR path reproduce the trusted XML→IR
amendment *operation* set EXACTLY?  This module DRIVES that comparison over the
whole amendment corpus (the ``sd/fin`` ORIGINAL statutes that are amendment acts
— XML carries an operative johtolause — and have a media PDF) and folds the
per-statute results into the first corpus-wide GENUINE-divergence distribution.

Two design commitments carry the whole thing:

  1. **Value-of-Information lane routing (minimise image tokens).**  The vision
     (``defacsimile``) lane is EXPENSIVE (image tokens); the deterministic geom
     lane is FREE.  For each statute we load its media PDF pages ONCE with the
     cheap pdfium text-layer census (no vision) and decide born-digital vs
     scanned via :func:`lawvm.ingest.born_digital.page_is_born_digital`.  A
     born-digital PDF is diffed with a GEOM ``text_fn`` (zero tokens, reusing the
     already-loaded pages); a scanned PDF routes to the default vision lane.  To
     BOUND the cost of this first pass the vision-lane statutes are CAPPED
     (default 50) — born-digital ones run freely, uncapped.  The cap is LOGGED
     (``cap_skipped``), never a silent truncation.

  2. **Persist the residual queue.**  Every statute's result is written as one
     JSONL row (sid, ``compare_status``, ``exact_equivalent``,
     ``typed_divergence_count``, ``counts``, per-divergence
     ``{kind, target_ref, detail}``, ``lane_used``) — this is the residual queue
     the downstream T1 adjudicator + image-escalation stage consumes.

The orchestration (:func:`run_corpus_diff`) is DEPENDENCY-INJECTED: it takes a
``router`` (sid → lane decision + optional geom text_fn) and a ``comparer``
(sid, lane, text_fn → :class:`CompareResult`), so CI exercises the driver
HERMETICALLY with fakes (no vision backend, no farchive) while the real CLI binds
the farchive-backed router/comparer.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.tools.fi_amendment_ir_compare import (
    CompareResult,
    OpDivergence,
    StatuteLocator,
    compare_statute,
)

_FINLEX_FARCHIVE = "data/finlex.farchive"
_DEFAULT_SIDS = "/home/elias/.claude/jobs/b8ab91c2/tmp/amend_sids.json"

#: A PDF whose born-digital page FRACTION clears this is diffed with the FREE geom
#: lane; below it we treat the PDF as scanned and route it to the vision lane.
_BORN_DIGITAL_FRACTION = 0.5

#: Default ceiling on EXPENSIVE vision-lane statutes for a first corpus pass.
_DEFAULT_VISION_CAP = 50

# Lane tags recorded on each row (namespaced away from the compare_status vocab).
LANE_GEOM = "geom"
LANE_VISION = "vision"
LANE_LOAD_ERROR = "load_error"
LANE_CAP_SKIPPED = "cap_skipped"


# --------------------------------------------------------------------------- #
# Lane routing (cheap pdfium census; NO vision) + the geom text_fn.            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Route:
    """The cheap lane decision for one statute's media PDF (no vision spent).

    ``lane`` is one of :data:`LANE_GEOM` / :data:`LANE_VISION` /
    :data:`LANE_LOAD_ERROR`.  ``text_fn`` is the FREE geom reading-text closure
    (present iff ``lane == geom``); it reuses the pages already loaded for the
    born-digital census, so choosing geom spends zero further work.
    """

    sid: str
    lane: str
    text_fn: Optional[Callable[[], str]]
    born_digital_fraction: float
    page_count: int
    detail: str = ""


def make_router(
    farchive: str = _FINLEX_FARCHIVE,
    *,
    max_pages: int = 20,
    born_digital_fraction: float = _BORN_DIGITAL_FRACTION,
) -> Callable[[str], Route]:
    """Build the farchive-backed ``router`` (sid → :class:`Route`).

    Loads the media PDF pages ONCE via the cheap pdfium text-layer producer (no
    vision), scores born-digital page fraction, and — for born-digital PDFs —
    binds a geom ``text_fn`` closure over those same pages so the free lane spends
    no further work.  A load/resolve failure is a typed ``load_error`` route
    (never a crash), so one bad PDF cannot sink the corpus run.
    """
    from lawvm.ingest.born_digital import page_is_born_digital
    from lawvm.tools.fi_amendment_ir_compare import (
        _manifestation,
        _read_farchive,
        resolve_media_locator,
    )
    from lawvm.tools.fi_producer_compare import GeomProducer, _load_pages

    def router(sid: str) -> Route:
        loc = StatuteLocator(sid=sid, lang="fin")
        try:
            media_locator = resolve_media_locator(loc, farchive)
            data = _read_farchive(farchive, media_locator)
            if not data:
                return Route(sid, LANE_LOAD_ERROR, None, 0.0, 0, "media PDF empty")
            man = _manifestation(data, media_locator)
            pages = _load_pages(man, max_pages)
        except Exception as exc:  # a bad/unreadable PDF is a typed route, never a sink
            return Route(sid, LANE_LOAD_ERROR, None, 0.0, 0, f"{type(exc).__name__}: {exc}")

        n = len(pages)
        if not n:
            return Route(sid, LANE_LOAD_ERROR, None, 0.0, 0, "no pages")
        bd = sum(1 for p in pages if page_is_born_digital(p))
        frac = bd / n
        if frac >= born_digital_fraction:
            captured_man = man
            captured_pages = pages

            def text_fn() -> str:
                texts = GeomProducer().reconstruct_pages(captured_man, captured_pages)
                return "\n".join(texts)

            return Route(sid, LANE_GEOM, text_fn, frac, n)
        return Route(sid, LANE_VISION, None, frac, n)

    return router


def make_comparer(
    farchive: str = _FINLEX_FARCHIVE, *, max_pages: int = 20
) -> Callable[[str, str, Optional[Callable[[], str]]], CompareResult]:
    """Build the farchive-backed ``comparer`` (sid, lane, text_fn → result).

    Geom-routed statutes inject the free reading text via ``pdf_text_fn`` (the
    ``lane`` argument to :func:`compare_statute` is then inert); vision-routed
    statutes run the real ``defacsimile`` lane against the backend.
    """

    def comparer(
        sid: str, lane: str, text_fn: Optional[Callable[[], str]]
    ) -> CompareResult:
        loc = StatuteLocator(sid=sid, lang="fin")
        if lane == LANE_GEOM:
            return compare_statute(
                loc, farchive, lane="struct_span", max_pages=max_pages, pdf_text_fn=text_fn
            )
        return compare_statute(loc, farchive, lane="defacsimile", max_pages=max_pages)

    return comparer


# --------------------------------------------------------------------------- #
# Per-statute row + the corpus aggregate.                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StatuteDiffRow:
    """One statute's corpus-driver result: the compare outcome + its lane tag."""

    sid: str
    lang: str
    lane_used: str
    compare_status: str
    exact_equivalent: bool
    typed_divergence_count: int
    counts: Dict[str, int]
    xml_op_count: int
    pdf_op_count: int
    born_digital_fraction: float
    page_count: int
    detail: str
    divergences: Tuple[OpDivergence, ...]
    #: Payload-stage census over the MATCHED ops (0 unless compared): body actually
    #: compared on both witnesses / type-deferred (body absent on ≥1 witness) / REPEAL
    #: tombstone (no payload). Carried so the corpus report can surface the PAYLOAD
    #: coverage, not just the op-structure distribution.
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0

    def to_json(self) -> Dict[str, object]:
        return {
            "sid": self.sid,
            "lang": self.lang,
            "lane_used": self.lane_used,
            "compare_status": self.compare_status,
            "exact_equivalent": self.exact_equivalent,
            "typed_divergence_count": self.typed_divergence_count,
            "counts": self.counts,
            "xml_op_count": self.xml_op_count,
            "pdf_op_count": self.pdf_op_count,
            "born_digital_fraction": round(self.born_digital_fraction, 4),
            "page_count": self.page_count,
            "detail": self.detail,
            "payload_compared": self.payload_compared,
            "payload_deferred": self.payload_deferred,
            "payload_skipped": self.payload_skipped,
            "divergences": [
                {"kind": d.kind, "target_ref": d.target_ref, "detail": d.detail}
                for d in self.divergences
            ],
        }


def _row_from_result(result: CompareResult, route: Route) -> StatuteDiffRow:
    return StatuteDiffRow(
        sid=result.sid,
        lang=result.lang,
        lane_used=route.lane,
        compare_status=result.compare_status,
        exact_equivalent=result.exact_equivalent,
        typed_divergence_count=result.typed_divergence_count,
        counts=result.counts,
        xml_op_count=result.xml_op_count,
        pdf_op_count=result.pdf_op_count,
        born_digital_fraction=route.born_digital_fraction,
        page_count=route.page_count,
        detail=result.detail,
        divergences=result.divergences,
        payload_compared=result.payload_compared,
        payload_deferred=result.payload_deferred,
        payload_skipped=result.payload_skipped,
    )


def _synthetic_row(route: Route, compare_status: str) -> StatuteDiffRow:
    """A row for statutes that never reached ``compare_statute`` (load / cap)."""
    return StatuteDiffRow(
        sid=route.sid,
        lang="fin",
        lane_used=route.lane if compare_status == LANE_LOAD_ERROR else LANE_CAP_SKIPPED,
        compare_status=compare_status,
        exact_equivalent=False,
        typed_divergence_count=0,
        counts={},
        xml_op_count=0,
        pdf_op_count=0,
        born_digital_fraction=route.born_digital_fraction,
        page_count=route.page_count,
        detail=route.detail,
        divergences=(),
    )


# The benign / non-compared terminal strata (never a genuine defect).
_NON_COMPARED_STATUSES = ("xml_frame_only", "pdf_annex_only", "appendix_only", "error")


@dataclass(frozen=True, slots=True)
class CorpusDiffReport:
    """The folded corpus-wide amendment-IR divergence distribution."""

    n_attempted: int
    n_geom: int
    n_vision: int
    n_cap_skipped: int
    n_load_error: int
    status_counts: Dict[str, int]
    n_compared: int
    n_exact: int
    total_typed_divergences: int
    bucket_counts: Dict[str, int]
    worst: Tuple[StatuteDiffRow, ...]
    #: Corpus-wide payload-stage coverage over the compared set (folded from rows):
    #: matched-op bodies actually compared / type-deferred / REPEAL-skipped.
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0

    @property
    def exact_match_rate(self) -> float:
        return (self.n_exact / self.n_compared) if self.n_compared else 0.0


def _rank_worst(rows: Sequence[StatuteDiffRow], limit: int) -> Tuple[StatuteDiffRow, ...]:
    """Compared rows with typed divergences, worst-first (deterministic)."""
    hot = [
        r
        for r in rows
        if r.compare_status == "compared" and r.typed_divergence_count > 0
    ]
    hot.sort(key=lambda r: (-r.typed_divergence_count, r.sid))
    return tuple(hot[:limit])


def aggregate_rows(
    rows: Sequence[StatuteDiffRow], *, worst_limit: int = 15
) -> CorpusDiffReport:
    """Fold the per-statute rows into the corpus divergence distribution."""
    status_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {
        "op_missing_in_pdf": 0,
        "op_extra_in_pdf": 0,
        "kind_mismatch": 0,
        "payload_mismatch": 0,
    }
    n_geom = n_vision = n_cap_skipped = n_load_error = 0
    n_compared = n_exact = total_typed = 0
    payload_compared = payload_deferred = payload_skipped = 0

    for r in rows:
        status_counts[r.compare_status] = status_counts.get(r.compare_status, 0) + 1
        if r.lane_used == LANE_GEOM:
            n_geom += 1
        elif r.lane_used == LANE_VISION:
            n_vision += 1
        elif r.lane_used == LANE_CAP_SKIPPED:
            n_cap_skipped += 1
        elif r.lane_used == LANE_LOAD_ERROR:
            n_load_error += 1
        if r.compare_status == "compared":
            n_compared += 1
            if r.exact_equivalent:
                n_exact += 1
            total_typed += r.typed_divergence_count
            for k, v in r.counts.items():
                if k in bucket_counts:
                    bucket_counts[k] += v
            payload_compared += r.payload_compared
            payload_deferred += r.payload_deferred
            payload_skipped += r.payload_skipped

    return CorpusDiffReport(
        n_attempted=len(rows),
        n_geom=n_geom,
        n_vision=n_vision,
        n_cap_skipped=n_cap_skipped,
        n_load_error=n_load_error,
        status_counts=status_counts,
        n_compared=n_compared,
        n_exact=n_exact,
        total_typed_divergences=total_typed,
        bucket_counts=bucket_counts,
        worst=_rank_worst(rows, worst_limit),
        payload_compared=payload_compared,
        payload_deferred=payload_deferred,
        payload_skipped=payload_skipped,
    )


# --------------------------------------------------------------------------- #
# The driver (dependency-injected; hermetic under test).                       #
# --------------------------------------------------------------------------- #


@dataclass
class _JsonlSink:
    """Append-only JSONL sink; a ``None`` path buffers rows in memory (tests)."""

    path: Optional[str]
    rows: List[Dict[str, object]] = field(default_factory=list)

    def write(self, row: StatuteDiffRow) -> None:
        payload = row.to_json()
        self.rows.append(payload)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_corpus_diff(
    sids: Sequence[str],
    router: Callable[[str], Route],
    comparer: Callable[[str, str, Optional[Callable[[], str]]], CompareResult],
    *,
    vision_cap: int = _DEFAULT_VISION_CAP,
    out_path: Optional[str] = None,
    worst_limit: int = 15,
    progress: Optional[Callable[[int, int, StatuteDiffRow], None]] = None,
) -> CorpusDiffReport:
    """Drive the op-diff over ``sids``, VoI-routing lanes and capping vision cost.

    For each statute the ``router`` makes the cheap lane decision; born-digital
    statutes run the FREE geom lane uncapped, scanned statutes route to the
    expensive vision lane which is CAPPED at ``vision_cap`` (excess statutes are
    recorded ``cap_skipped``, never silently dropped).  Each result is persisted
    as a JSONL row and folded into the returned :class:`CorpusDiffReport`.
    """
    sink = _JsonlSink(out_path)
    rows: List[StatuteDiffRow] = []
    vision_used = 0
    total = len(sids)

    for i, sid in enumerate(sids):
        route = router(sid)
        if route.lane == LANE_LOAD_ERROR:
            row = _synthetic_row(route, LANE_LOAD_ERROR)
        elif route.lane == LANE_VISION and vision_used >= vision_cap:
            row = _synthetic_row(route, LANE_CAP_SKIPPED)
        else:
            if route.lane == LANE_VISION:
                vision_used += 1
            result = comparer(sid, route.lane, route.text_fn)
            row = _row_from_result(result, route)
        rows.append(row)
        sink.write(row)
        if progress is not None:
            progress(i + 1, total, row)

    return aggregate_rows(rows, worst_limit=worst_limit)


# --------------------------------------------------------------------------- #
# Corpus enumeration + rendering.                                              #
# --------------------------------------------------------------------------- #


def load_corpus_sids(path: str) -> List[str]:
    """Read the precomputed amendment-corpus sid list (a JSON array of strings)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"fi-amendment-ir-corpus: {path} is not a JSON list of sids")
    return [str(s) for s in data]


def render_report(report: CorpusDiffReport) -> str:
    """Deterministic text render of the corpus divergence distribution."""
    lines: List[str] = []
    lines.append("# fi-amendment-ir-corpus — corpus-wide amendment-IR op-diff distribution")
    lines.append(
        f"# attempted={report.n_attempted}  geom(free)={report.n_geom}  "
        f"vision={report.n_vision}  cap_skipped={report.n_cap_skipped}  "
        f"load_error={report.n_load_error}"
    )
    lines.append("")
    lines.append("## STATUS STRATA")
    for status in sorted(report.status_counts):
        lines.append(f"  {status:<16} {report.status_counts[status]}")
    lines.append("")
    lines.append("## OVER THE 'compared' SET")
    lines.append(
        f"  compared={report.n_compared}  exact_equivalent={report.n_exact}  "
        f"exact_match_rate={report.exact_match_rate:.4f}"
    )
    lines.append(f"  total_genuine_typed_divergences={report.total_typed_divergences}")
    lines.append(
        f"  buckets: op_missing_in_pdf={report.bucket_counts['op_missing_in_pdf']}  "
        f"op_extra_in_pdf={report.bucket_counts['op_extra_in_pdf']}  "
        f"kind_mismatch={report.bucket_counts['kind_mismatch']}  "
        f"payload_mismatch={report.bucket_counts.get('payload_mismatch', 0)}"
    )
    lines.append(
        f"  payload stage: compared={report.payload_compared}  "
        f"deferred={report.payload_deferred}  repeal_skipped={report.payload_skipped}"
    )
    lines.append("")
    lines.append("## RANKED WORST (compared, by typed_divergence_count)")
    lines.append("rank,sid,lane,typed,xml_ops,pdf_ops,kinds")
    for rank, r in enumerate(report.worst, start=1):
        kinds = "|".join(
            f"{d.kind}:{d.target_ref}" for d in r.divergences if d.kind != "matched"
        )
        lines.append(
            f"{rank},{r.sid},{r.lane_used},{r.typed_divergence_count},"
            f"{r.xml_op_count},{r.pdf_op_count},{kinds}"
        )
    return "\n".join(lines)


def report_to_json(report: CorpusDiffReport) -> Dict[str, object]:
    return {
        "n_attempted": report.n_attempted,
        "n_geom": report.n_geom,
        "n_vision": report.n_vision,
        "n_cap_skipped": report.n_cap_skipped,
        "n_load_error": report.n_load_error,
        "status_counts": report.status_counts,
        "n_compared": report.n_compared,
        "n_exact": report.n_exact,
        "exact_match_rate": report.exact_match_rate,
        "total_typed_divergences": report.total_typed_divergences,
        "bucket_counts": report.bucket_counts,
        "payload_compared": report.payload_compared,
        "payload_deferred": report.payload_deferred,
        "payload_skipped": report.payload_skipped,
        "worst": [
            {
                "sid": r.sid,
                "lane_used": r.lane_used,
                "typed_divergence_count": r.typed_divergence_count,
                "xml_op_count": r.xml_op_count,
                "pdf_op_count": r.pdf_op_count,
                "divergences": [
                    {"kind": d.kind, "target_ref": d.target_ref, "detail": d.detail}
                    for d in r.divergences
                    if d.kind != "matched"
                ],
            }
            for r in report.worst
        ],
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-amendment-ir-corpus``."""
    farchive = args.farchive or _FINLEX_FARCHIVE
    sids = load_corpus_sids(args.sids or _DEFAULT_SIDS)
    if args.limit:
        sids = sids[: args.limit]
    out_path = args.out
    # Truncate the residual queue up front (the driver APPENDS per row).
    if out_path:
        open(out_path, "w", encoding="utf-8").close()

    router = make_router(farchive, max_pages=args.max_pages)
    comparer = make_comparer(farchive, max_pages=args.max_pages)

    def progress(done: int, total: int, row: StatuteDiffRow) -> None:
        print(
            f"[{done}/{total}] {row.sid} lane={row.lane_used} "
            f"status={row.compare_status} typed={row.typed_divergence_count}",
            flush=True,
        )

    report = run_corpus_diff(
        sids,
        router,
        comparer,
        vision_cap=args.vision_cap,
        out_path=out_path,
        progress=progress if args.verbose else None,
    )

    if args.json:
        print(json.dumps(report_to_json(report), ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
