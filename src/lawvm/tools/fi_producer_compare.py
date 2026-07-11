"""``lawvm fi-producer-compare`` — Level-1 PRODUCER usefulness A/B vs the XML gold.

A self-contained harness that answers a single operational question: for a finlex
PDF that has a sibling authoritative ``main.xml``, *which Level-1 page producer
(and which 2-producer combination) reconstructs the reading text most faithfully,
and at what token cost*. It is the producer-choice instrument the two-level
pipeline never had — ``fi_parse_compare`` / ``fi_parse_corpus`` A/B the Level-2
de-facsimile against the mechanical struct_span stitch, and ``fi_calibration``
sweeps ONE producer's tiling; NEITHER compares the Level-1 PRODUCERS against each
other on the same faithfulness scale.

Reference (gold). ONE bi-source stratum — HE government proposals (``akn/fi/doc/
government-proposal/<y>/<n>/fin@/main.pdf`` <-> ``.../main.xml`` in
``data/fi_government_proposal.farchive``). These are born-digital PROSE and a clean
same-document match (measured XML-body <-> PDF-text word overlap ~0.98). The
reconstruction target is the XML body reading text — exactly ``fi_parse_compare
.xml_body_text`` (``mainBody`` itertext), de-hyphenated identically on both sides
by the scorers. sd/ original statutes are NOT used: their media PDFs are largely
scanned and their ``main.xml`` is the CURRENT/consolidated text, divergent from the
gazette PDF (overlap 0.0–0.5). A HE pair is kept only when its XML passes
``he_xml_is_valid_gold`` (inline content — NO ``.pdf``/``media`` ref — and a
substantial body), which drops the rare stub → ~8k clean pairs.

Scorers (REUSED verbatim — this module invents NO new heuristic metric):

  * NUMERIC-exact **recall / precision** over the protected §-ref / euro / date /
    number token multiset (``defacsimile._numeric_tokens`` — the SAME grabber the
    production ``verify_ledger`` gate uses, so faithfulness here and the gate agree
    by construction).
  * **WER** over dehyphenated, whitespace-normalized words (``fi_calibration
    .word_error_rate`` — rapidfuzz Levenshtein).
  * **word-coverage** — fraction of the XML body's words present in the
    reconstruction (``fi_parse_compare._words`` / ``._coverage``, de-hyphenated
    both sides).

Cost. Per-producer input / output / total model tokens + measured model wall
seconds are read from the process-wide ``token_meter.METER`` (the single vision
HTTP choke point records one tagged row per call); the harness meters each
producer's run in isolation by ``reset()``-ing the ledger around it. A
deterministic (vision-free) producer records ZERO model tokens — that IS the
finding, and the reason the rollup ranks producers on **faithfulness-per-token**.

Producers (run whatever ``is_available()``; the skip list is ALWAYS printed, never
silently omitted):

  * ``geom``     — the deterministic born-digital geom lane (``ingest.born_digital
    .build_born_digital_simulacra``); no backend, no image tokens.
  * ``vision``   — the Level-1 vision build-script read (``vision_producer
    .propose_page_struct(leaf_mode="span")``) per page.
  * ``docling``  — the Docling learned-layout producer (gated on the ``docling``
    extra being importable — NOT installed here → SKIPPED).
  * ``nemotron`` — the isolated nemotron-parse service (gated on ``LAWVM_NEMOTRON
    _PARSE_CMD`` — inert here → SKIPPED).

Combos (per-page UNION, corroboration accounted through the CORE kernel's
``adjudication.assurance_for`` — NOT a new merge heuristic): for each page take the
PRIMARY producer's text, else the SECONDARY's (this is exactly the complementarity
that matters — ``geom`` reads born-digital pages losslessly and ``vision`` covers
the text-poor pages ``geom`` returns nothing for). At least ``geom+vision`` and
``docling+vision`` are planned; a combo whose members are not both available is
reported SKIPPED. Without the LLM adjudicator the corroboration tier caps at
``SINGLE_WITNESS`` (honest — no witness fusion is invented), but the count of
2-witness pages is reported.

Stratification. Each PDF is assigned a DOMINANT page-kind from the vision
``appraise_page`` verdict (prose / tables / mixed / figure / form / scanned) when
the vision backend is up; ``unappraised`` otherwise. The aggregate rolls producers
up PER (stratum, dominant page-kind) and names the per-group winner on
faithfulness-per-token (the stratum is always ``he`` today, but the rollup stays
general so a second gold stratum could be added without reshaping it).

Typed failures, never a silent empty: a producer that raises on a PDF becomes a
typed ``failed`` score row (detail carried), mirroring ``fi_parse_corpus
._process_one`` — one bad producer never sinks the comparison. The full run is
SEQUENTIAL (PDFs and producers both), because the token ledger is process-global
and concurrent producers would cross-attribute their tokens.

CI exercises this hermetically with a scripted fake producer + ``--dry-run`` (see
``tests/test_fi_producer_compare.py``); the real backend path is operator-run.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from lawvm.core.source_document.adjudication import assurance_for
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.ingest.defacsimile import _numeric_tokens
from lawvm.ingest.llm_backends import token_meter
from lawvm.tools.fi_calibration import word_error_rate
from lawvm.tools.fi_parse_compare import _coverage, _words, xml_body_text

# The ONE bi-source SAME-DOCUMENT stratum: HE government proposals, born-digital
# PROSE — the CLEAN case (measured XML-body<->PDF-text word overlap ~0.98):
#   akn/fi/doc/government-proposal/<year>/<num>/<lang>@/main.pdf <-> .../main.xml
#   in data/fi_government_proposal.farchive (~8.4k fin PDFs).
#
# sd/ ORIGINAL statutes were DROPPED as a gold: their media PDFs are largely scanned
# (no text layer) and the sd/ main.xml is the CURRENT/consolidated text, divergent
# from the original gazette PDF (measured overlap 0.0–0.5, mostly < 0.3). Byte-size
# does not predict a match, so there is no cheap salvage — HE is the only clean gold.
_HE_FARCHIVE_DEFAULT = "data/fi_government_proposal.farchive"
_HE_PDF_SUFFIX = "/main.pdf"

# HE XML-body char floor: drops the rare ~3KB stub (a valid full-prose gold is far
# longer). Below this the XML is not a faithful whole-document reference.
_HE_MIN_XML_CHARS = 2000

# Cheap valid-gold PROXY (confirmed): a GOLD XML carries its content INLINE and has
# NO ``.pdf`` / ``media`` reference; a non-gold XML (a stub that points OUT to source
# media) carries several. HE golds have pdf-refs == 0 — so any ``.pdf``/``media``
# token in the raw XML disqualifies the pair.
_NON_GOLD_XML_MARKERS: Tuple[bytes, ...] = (b".pdf", b"media")

# The producers this harness knows how to build, in a stable order. Combos are
# 2-tuples (primary, secondary) unioned per page. Kept as names so --dry-run can
# report the plan without importing a backend.
_SINGLE_PRODUCERS: Tuple[str, ...] = ("geom", "vision", "docling", "nemotron")
_COMBOS: Tuple[Tuple[str, str], ...] = (("geom", "vision"), ("docling", "vision"))


# --------------------------------------------------------------------------- #
# PDF<->XML same-document pairs + the two-stratum enumerator.                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PdfXmlPair:
    """One same-document (PDF, XML) pair carrying its source archive explicitly.

    ``stratum`` is always ``"he"`` (the one clean bi-source stratum) but is kept as a
    field so the rollup stays general (it groups + wins per (stratum, page-kind))."""

    pdf_locator: str
    xml_locator: str
    pdf_farchive: str
    xml_farchive: str
    stratum: str = "he"


def he_xml_is_valid_gold(
    raw_xml: bytes, *, min_chars: int = _HE_MIN_XML_CHARS
) -> bool:
    """Is this HE ``main.xml`` a faithful whole-document gold for its sibling PDF?

    Two cheap, confirmed proxies: (1) the body reading text is SUBSTANTIAL (clears
    ``min_chars`` — drops the rare stub), and (2) the raw XML carries NO ``.pdf`` /
    ``media`` reference (a gold holds its content INLINE; a stub points OUT to source
    media). Both must hold."""
    if not raw_xml:
        return False
    lowered = raw_xml.lower()
    if any(marker in lowered for marker in _NON_GOLD_XML_MARKERS):
        return False
    return len(xml_body_text(raw_xml)) >= min_chars


def enumerate_he_pairs(
    he_farchive: str = _HE_FARCHIVE_DEFAULT,
    *,
    lang: str = "fin",
    min_xml_chars: int = _HE_MIN_XML_CHARS,
    limit: Optional[int] = None,
) -> List[PdfXmlPair]:
    """HE government-proposal ``main.pdf`` <-> ``main.xml`` pairs (born-digital prose).

    Deterministically sorted by locator; ``lang`` selects the language segment
    (``fin@`` preferred). Each candidate's sibling XML is read and kept ONLY when it
    passes ``he_xml_is_valid_gold`` (inline content, substantial body). ``limit``
    stops after that many VALID pairs (lazy — the XML read stops early under a limit).
    """
    from farchive import Farchive

    fa = Farchive(he_farchive)
    try:
        locs = list(fa.locators())
        present = frozenset(locs)
        seg = f"/{lang}@"
        out: List[PdfXmlPair] = []
        for loc in sorted(locs):
            if not loc.endswith(_HE_PDF_SUFFIX):
                continue
            prefix = loc[: -len(_HE_PDF_SUFFIX)]
            if not prefix.endswith(seg):
                continue
            xml = prefix + "/main.xml"
            if xml not in present:
                continue
            span = fa.resolve(xml)
            raw = (fa.read(span.digest) or b"") if span is not None else b""
            if not he_xml_is_valid_gold(raw, min_chars=min_xml_chars):
                continue
            out.append(PdfXmlPair(loc, xml, he_farchive, he_farchive, "he"))
            if limit is not None and len(out) >= limit:
                break
        return out
    finally:
        fa.close()


def enumerate_pairs(
    *,
    he_farchive: str = _HE_FARCHIVE_DEFAULT,
    lang: str = "fin",
    limit: Optional[int] = None,
) -> List[PdfXmlPair]:
    """The clean-gold pair enumeration (HE only)."""
    return enumerate_he_pairs(he_farchive, lang=lang, limit=limit)


# --------------------------------------------------------------------------- #
# Reused scorers (NUMERIC recall/precision over the existing token grabber).    #
# --------------------------------------------------------------------------- #


def numeric_recall_precision(gold: str, hyp: str) -> Tuple[float, float]:
    """NUMERIC-exact (recall, precision) over the protected token MULTISET.

    Reuses ``defacsimile._numeric_tokens`` (the production §-ref/euro/date grabber).
    ``recall`` = fraction of the gold's protected tokens preserved in the hypothesis;
    ``precision`` = fraction of the hypothesis's protected tokens that are real (in
    the gold). Multiset intersection over counts — recall/precision is a standard
    set measure, NOT a new heuristic. Degenerate cases: no gold tokens → recall 1.0
    (nothing to recover); no hyp tokens → precision 1.0 (nothing invented)."""
    g = _numeric_tokens(gold)
    h = _numeric_tokens(hyp)
    g_total = sum(g.values())
    h_total = sum(h.values())
    inter = sum(min(c, h.get(tok, 0)) for tok, c in g.items())
    recall = inter / g_total if g_total else 1.0
    precision = inter / h_total if h_total else 1.0
    return recall, precision


# --------------------------------------------------------------------------- #
# Producer protocol + concrete Level-1 producer wrappers.                       #
# --------------------------------------------------------------------------- #


class Level1Producer(Protocol):
    """A Level-1 page producer reduced to its usefulness-relevant surface.

    ``reconstruct_pages`` returns ONE reading-text string per page (index 0 =
    page 1), ``""`` for a page the producer had no content for — so a combo can
    union per page and the whole-doc text is a simple join. Raises on a real
    backend failure (the harness turns that into a typed ``failed`` row)."""

    name: str

    def is_available(self) -> bool: ...

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]: ...


def _flatten_node_text(node: Any) -> str:
    """Depth-first reading text of any ``.text``/``.children`` node tree."""
    parts: List[str] = []

    def _walk(n: Any) -> None:
        t = getattr(n, "text", "")
        if t:
            parts.append(str(t))
        for c in getattr(n, "children", ()) or ():
            _walk(c)

    _walk(node)
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class GeomProducer:
    """The deterministic born-digital geom lane (no vision, zero image tokens)."""

    name: str = "geom"

    def is_available(self) -> bool:  # always — pure, no backend
        return True

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]:
        from lawvm.ingest.born_digital import born_digital_page, page_is_born_digital
        from lawvm.ingest.page_level import band_recurrence_map

        recurrence = band_recurrence_map(list(pages))
        total = len(pages)
        out: List[str] = []
        for i, pe in enumerate(pages):
            if not page_is_born_digital(pe):
                out.append("")
                continue
            bd = born_digital_page(
                manifestation, i + 1, pe, recurrence=recurrence, page_count=total
            )
            out.append("\n".join(_flatten_node_text(n) for n in bd.simulacrum.nodes))
        return out


@dataclass(frozen=True, slots=True)
class VisionProducer:
    """The Level-1 vision build-script read (``propose_page_struct``, span leaves)."""

    base_url: str = "http://127.0.0.1:8080"
    name: str = "vision"

    def _producer(self) -> Any:
        from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer

        return VisionPageProducer(base_url=self.base_url)

    def is_available(self) -> bool:
        return bool(self._producer().is_available())

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]:
        from lawvm.ingest.llm_backends.vision_producer import (
            VisionProducerFailure,
            VisionProducerTruncated,
        )

        producer = self._producer()
        out: List[str] = []
        for i, pe in enumerate(pages):
            page_num = i + 1
            # Tag every model call so the ledger attributes it to this pdf/lane/page.
            with token_meter.meter_unit(
                pdf=manifestation.locator, lane=self.name, page=page_num
            ):
                try:
                    res = producer.propose_page_struct(
                        manifestation, page_num, pe, leaf_mode="span"
                    )
                except (VisionProducerTruncated, VisionProducerFailure):
                    # A truncated / failed page read is an empty page (counted MISSING
                    # by the scorers) — an honest cliff signal, never a crash.
                    out.append("")
                    continue
            out.append("\n".join(_flatten_node_text(r) for r in res.build.roots))
        return out


@dataclass(frozen=True, slots=True)
class DoclingProducer:
    """The Docling learned-layout producer (gated on the ``docling`` extra)."""

    name: str = "docling"

    def _producer(self) -> Any:
        from lawvm.ingest.llm_backends.docling_producer import DoclingStructuralProducer

        return DoclingStructuralProducer()

    def is_available(self) -> bool:
        return bool(self._producer().is_available())

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]:
        producer = self._producer()
        out: List[str] = []
        for i in range(len(pages)):
            nodes = producer.propose_page(manifestation, i + 1)
            out.append("\n".join(_flatten_node_text(n) for n in nodes))
        return out


@dataclass(frozen=True, slots=True)
class NemotronProducer:
    """The isolated nemotron-parse service (gated on ``LAWVM_NEMOTRON_PARSE_CMD``)."""

    name: str = "nemotron"

    def _producer(self) -> Any:
        from lawvm.ingest.llm_backends.nemotron_client import NemotronParseClient

        return NemotronParseClient()

    def is_available(self) -> bool:
        return bool(self._producer().is_available())

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]:
        from lawvm.ingest.llm_backends.nemotron_client import NemotronParseFailure

        producer = self._producer()
        out: List[str] = []
        for i in range(len(pages)):
            try:
                assertions = producer.propose_page(manifestation, i + 1)
            except NemotronParseFailure:
                out.append("")
                continue
            out.append("\n".join(a.text for a in assertions if a.text))
        return out


@dataclass(frozen=True, slots=True)
class ComboProducer:
    """A 2-producer per-page UNION (primary else secondary) — complementarity A/B.

    ``reconstruct_pages`` prefers the primary producer's text for a page and falls
    back to the secondary's ONLY where the primary produced nothing — the exact
    born-digital(geom) / text-poor(vision) complementarity. ``corroborating_pages``
    (pages where BOTH produced content) is reported through the core kernel's
    ``assurance_for``; without the LLM adjudicator the tier caps at SINGLE_WITNESS
    (no witness fusion is invented)."""

    primary: Any
    secondary: Any
    name: str

    def is_available(self) -> bool:
        return bool(self.primary.is_available() and self.secondary.is_available())

    def reconstruct_pages(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> List[str]:
        a = self.primary.reconstruct_pages(manifestation, pages)
        b = self.secondary.reconstruct_pages(manifestation, pages)
        out: List[str] = []
        for pa, pb in zip(a, b, strict=False):
            out.append(pa if pa.strip() else pb)
        return out

    def corroboration(
        self, manifestation: SourceManifestation, pages: Sequence[Any]
    ) -> Tuple[List[str], int]:
        """Return (per-page union text, count of 2-witness pages)."""
        a = self.primary.reconstruct_pages(manifestation, pages)
        b = self.secondary.reconstruct_pages(manifestation, pages)
        out: List[str] = []
        both = 0
        for pa, pb in zip(a, b, strict=False):
            if pa.strip() and pb.strip():
                both += 1
            out.append(pa if pa.strip() else pb)
        return out, both


def build_producers(
    *, base_url: str = "http://127.0.0.1:8080"
) -> Dict[str, Level1Producer]:
    """Instantiate every single producer + planned combo, keyed by name."""
    singles: Dict[str, Level1Producer] = {
        "geom": GeomProducer(),
        "vision": VisionProducer(base_url=base_url),
        "docling": DoclingProducer(),
        "nemotron": NemotronProducer(),
    }
    producers: Dict[str, Level1Producer] = dict(singles)
    for pri, sec in _COMBOS:
        name = f"{pri}+{sec}"
        producers[name] = ComboProducer(
            primary=singles[pri], secondary=singles[sec], name=name
        )
    return producers


# --------------------------------------------------------------------------- #
# Per-producer score.                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProducerScore:
    """One producer's usefulness on one PDF (or a typed non-scored status)."""

    producer: str
    score_status: str  # "scored" | "unavailable" | "failed"
    numeric_recall: float = 0.0
    numeric_precision: float = 0.0
    wer: float = 1.0
    word_coverage: float = 0.0
    reconstructed_chars: int = 0
    pages_with_content: int = 0
    corroborating_pages: int = 0
    assurance_tier: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_wall_s: float = 0.0
    producer_wall_s: float = 0.0
    detail: Optional[str] = None

    @property
    def tokens_per_1k(self) -> float:
        return self.total_tokens / 1000.0

    @property
    def coverage_per_1k_tokens(self) -> Optional[float]:
        """Faithfulness (word-coverage) per 1k model tokens — ``None`` if free.

        A deterministic producer spends ZERO tokens, so its efficiency is unbounded
        (``None`` → rendered ``inf(free)``); the rollup ranks a free producer ahead
        of any paid one at equal-or-better coverage — the whole point of the lever."""
        if self.total_tokens <= 0:
            return None
        return self.word_coverage / (self.total_tokens / 1000.0)


def _score_text(gold: str, pages: Sequence[str]) -> Tuple[float, float, float, float, int, int]:
    """(numeric_recall, numeric_precision, wer, word_coverage, chars, pages_with_content)."""
    non_empty = [p for p in pages if p.strip()]
    text = "\n".join(non_empty)
    recall, precision = numeric_recall_precision(gold, text)
    wer = word_error_rate(gold, text)
    coverage = _coverage(_words(text), _words(gold))
    return recall, precision, wer, coverage, len(text), len(non_empty)


def score_producer(
    producer: Level1Producer,
    manifestation: SourceManifestation,
    pages: Sequence[Any],
    xml_text: str,
) -> ProducerScore:
    """Run ONE producer over the loaded pages and score it against the XML gold.

    Isolates the token ledger around the run (``reset`` before, ``reset`` after) so
    the reported tokens/wall are EXACTLY this producer's model calls. A raise is a
    typed ``failed`` row (mirrors ``fi_parse_corpus._process_one``), never a crash."""
    if not producer.is_available():
        return ProducerScore(producer=producer.name, score_status="unavailable")

    token_meter.reset()  # clear any prior producer's rows
    t0 = time.monotonic()
    corroborating = 0
    try:
        with token_meter.meter_unit(pdf=manifestation.locator, lane=producer.name):
            if isinstance(producer, ComboProducer):
                page_texts, corroborating = producer.corroboration(manifestation, pages)
            else:
                page_texts = producer.reconstruct_pages(manifestation, pages)
    except Exception as exc:  # a bad producer is a typed row, never a sink
        token_meter.reset()
        return ProducerScore(
            producer=producer.name,
            score_status="failed",
            detail=f"{type(exc).__name__}: {exc}",
        )
    wall_s = time.monotonic() - t0
    snap = token_meter.reset()
    summ = snap.summary

    recall, precision, wer, coverage, chars, n_content = _score_text(xml_text, page_texts)
    tier = assurance_for(2 if corroborating else 1).name if isinstance(
        producer, ComboProducer
    ) else assurance_for(1).name
    return ProducerScore(
        producer=producer.name,
        score_status="scored",
        numeric_recall=recall,
        numeric_precision=precision,
        wer=wer,
        word_coverage=coverage,
        reconstructed_chars=chars,
        pages_with_content=n_content,
        corroborating_pages=corroborating,
        assurance_tier=tier,
        model_calls=summ.calls,
        input_tokens=summ.input_tokens,
        output_tokens=summ.output_tokens,
        total_tokens=summ.total_tokens,
        model_wall_s=summ.wall_seconds,
        producer_wall_s=wall_s,
    )


# --------------------------------------------------------------------------- #
# Per-PDF comparison.                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PdfProducerReport:
    """All producers' scores on one PDF + its vision-appraised dominant page-kind."""

    pdf_locator: str
    xml_locator: Optional[str]
    stratum: str
    pair_status: str  # "compared" | "failed"
    dominant_kind: str = "unappraised"
    page_kinds: Dict[str, int] = field(default_factory=dict)
    scores: Tuple[ProducerScore, ...] = ()
    detail: Optional[str] = None


def _load_pages(manifestation: SourceManifestation, max_pages: int) -> List[Any]:
    """Per-page ``PageElements`` (single-flighted under the systemic pdfium lock)."""
    import importlib

    from lawvm.ingest.page_elements import PageElementProducer
    from lawvm.ingest.visual import PDFIUM_LOCK

    producer = PageElementProducer()
    pages: List[Any] = []
    with PDFIUM_LOCK:
        pdfium = importlib.import_module("pypdfium2")
        doc = pdfium.PdfDocument(manifestation.source_bytes)
        try:
            n = min(len(doc), max_pages)
        finally:
            doc.close()
        for pn in range(1, n + 1):
            pages.append(producer.page_elements(manifestation.source_bytes, pn))
    return pages


def _appraise_pages(
    manifestation: SourceManifestation, pages: Sequence[Any], base_url: str
) -> Tuple[str, Dict[str, int]]:
    """Vision ``appraise_page`` per page → (dominant_kind, kind histogram).

    Metered under a distinct ``appraise`` lane so its tokens never contaminate a
    producer's cost. A per-page appraisal failure is skipped (best-effort routing
    signal, never fatal). Empty / no-vision → ``("unappraised", {})``."""
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionPageProducer,
        VisionProducerFailure,
        VisionProducerTruncated,
    )

    producer = VisionPageProducer(base_url=base_url)
    if not producer.is_available():
        return "unappraised", {}
    hist: Dict[str, int] = {}
    for i, pe in enumerate(pages):
        with token_meter.meter_unit(pdf=manifestation.locator, lane="appraise", page=i + 1):
            try:
                appraisal = producer.appraise_page(manifestation, i + 1, pe)
            except (VisionProducerTruncated, VisionProducerFailure):
                continue
        hist[appraisal.kind] = hist.get(appraisal.kind, 0) + 1
    if not hist:
        return "unappraised", {}
    dominant = max(sorted(hist), key=lambda k: hist[k])
    return dominant, hist


def compare_pdf(
    pair: PdfXmlPair,
    producers: Dict[str, Level1Producer],
    *,
    base_url: str,
    max_pages: int,
    appraise: bool = True,
) -> PdfProducerReport:
    """Compare every available producer on one PDF against its sibling XML gold.

    Loads the PDF from ``pair.pdf_farchive`` and the XML gold from
    ``pair.xml_farchive`` (the two strata live in different archives). Mirrors
    ``fi_parse_corpus._process_one``'s typed-failure discipline: a load / gold /
    page failure is a typed ``failed`` report, never a raise into the driver.
    Producers run SEQUENTIALLY so the process-global token ledger attributes cleanly."""
    from farchive import Farchive
    from lawvm.finland.source_document.pdf_profiles import load_manifestation_from_farchive

    try:
        manifestation = load_manifestation_from_farchive(
            pair.pdf_locator, farchive_path=pair.pdf_farchive, source_role="attachment"
        )
    except Exception as exc:
        return PdfProducerReport(
            pdf_locator=pair.pdf_locator,
            xml_locator=pair.xml_locator,
            stratum=pair.stratum,
            pair_status="failed",
            detail=f"load: {type(exc).__name__}: {exc}",
        )

    fa = Farchive(pair.xml_farchive)
    try:
        span = fa.resolve(pair.xml_locator)
        xml_bytes = fa.read(span.digest) if span is not None else b""
    except Exception as exc:
        return PdfProducerReport(
            pdf_locator=pair.pdf_locator,
            xml_locator=pair.xml_locator,
            stratum=pair.stratum,
            pair_status="failed",
            detail=f"xml: {type(exc).__name__}: {exc}",
        )
    finally:
        fa.close()
    if not xml_bytes:
        return PdfProducerReport(
            pdf_locator=pair.pdf_locator,
            xml_locator=pair.xml_locator,
            stratum=pair.stratum,
            pair_status="failed",
            detail="xml: empty gold blob",
        )
    xml_text = xml_body_text(xml_bytes)

    try:
        pages = _load_pages(manifestation, max_pages)
    except Exception as exc:
        return PdfProducerReport(
            pdf_locator=pair.pdf_locator,
            xml_locator=pair.xml_locator,
            stratum=pair.stratum,
            pair_status="failed",
            detail=f"pages: {type(exc).__name__}: {exc}",
        )

    dominant, hist = ("unappraised", {})
    if appraise:
        token_meter.reset()
        dominant, hist = _appraise_pages(manifestation, pages, base_url)
        token_meter.reset()

    scores = tuple(
        score_producer(producers[name], manifestation, pages, xml_text)
        for name in producers
    )
    return PdfProducerReport(
        pdf_locator=pair.pdf_locator,
        xml_locator=pair.xml_locator,
        stratum=pair.stratum,
        pair_status="compared",
        dominant_kind=dominant,
        page_kinds=hist,
        scores=scores,
    )


# --------------------------------------------------------------------------- #
# Aggregate rollup — per page-kind winner on faithfulness-per-token.            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class KindProducerAggregate:
    """One (stratum, page-kind, producer) cell of the rollup — means + summed cost."""

    stratum: str
    kind: str
    producer: str
    n_pdfs: int
    mean_coverage: float
    mean_wer: float
    mean_numeric_recall: float
    total_tokens: int
    efficiency: Optional[float]  # mean_coverage per 1k tokens; None = free (0 tokens)


@dataclass(frozen=True, slots=True)
class RollupReport:
    """The full per-PDF reports + the per-(stratum,kind) aggregate + winners."""

    reports: Tuple[PdfProducerReport, ...]
    aggregates: Tuple[KindProducerAggregate, ...]
    # winner keyed ``"<stratum>/<kind>"`` on faithfulness-per-token.
    kind_winners: Dict[str, str]
    skipped: Tuple[str, ...]  # producers reported unavailable (never silently omitted)


def _efficiency(mean_coverage: float, total_tokens: int) -> Optional[float]:
    if total_tokens <= 0:
        return None
    return mean_coverage / (total_tokens / 1000.0)


def build_rollup(
    reports: Sequence[PdfProducerReport], producer_names: Sequence[str]
) -> RollupReport:
    """Group scored rows by (stratum, dominant_kind, producer) → aggregate + winner.

    The strata (``he`` prose vs ``sd`` mixed/tables) differ, so producers are rolled
    up and won SEPARATELY per (stratum, page-kind). Winner = the producer with the
    best faithfulness-per-token: a FREE producer (0 tokens, ``efficiency=None``)
    beats any paid producer whose coverage it matches or exceeds; among free
    producers the higher coverage wins; among paid producers the higher
    coverage-per-1k-tokens wins. Deterministic tie-break by producer name."""
    # (stratum, kind, producer) -> list of scored rows
    cells: Dict[Tuple[str, str, str], List[ProducerScore]] = {}
    groups: set[Tuple[str, str]] = set()
    for rep in reports:
        if rep.pair_status != "compared":
            continue
        groups.add((rep.stratum, rep.dominant_kind))
        for sc in rep.scores:
            if sc.score_status != "scored":
                continue
            cells.setdefault((rep.stratum, rep.dominant_kind, sc.producer), []).append(sc)

    aggregates: List[KindProducerAggregate] = []
    for (stratum, kind, producer), rows in sorted(cells.items()):
        n = len(rows)
        mean_cov = sum(r.word_coverage for r in rows) / n
        mean_wer = sum(r.wer for r in rows) / n
        mean_rec = sum(r.numeric_recall for r in rows) / n
        total_tok = sum(r.total_tokens for r in rows)
        aggregates.append(
            KindProducerAggregate(
                stratum=stratum,
                kind=kind,
                producer=producer,
                n_pdfs=n,
                mean_coverage=mean_cov,
                mean_wer=mean_wer,
                mean_numeric_recall=mean_rec,
                total_tokens=total_tok,
                efficiency=_efficiency(mean_cov, total_tok),
            )
        )

    winners: Dict[str, str] = {}
    for stratum, kind in sorted(groups):
        cells_here = [a for a in aggregates if a.stratum == stratum and a.kind == kind]
        if not cells_here:
            continue

        def _rank(a: KindProducerAggregate) -> Tuple[int, float, float, str]:
            free = a.efficiency is None
            eff = float("inf") if free else a.efficiency  # type: ignore[assignment]
            # free first (1), then higher coverage, then higher efficiency, then name
            return (1 if free else 0, a.mean_coverage, eff, a.producer)

        winners[f"{stratum}/{kind}"] = max(cells_here, key=_rank).producer

    # Producers that were unavailable on EVERY compared PDF → the skip list.
    scored_names = {a.producer for a in aggregates}
    skipped = tuple(n for n in producer_names if n not in scored_names)
    return RollupReport(
        reports=tuple(reports),
        aggregates=tuple(aggregates),
        kind_winners=winners,
        skipped=skipped,
    )


# --------------------------------------------------------------------------- #
# Rendering (deterministic line-based + JSON).                                  #
# --------------------------------------------------------------------------- #

_SCORE_HEADER = (
    "pdf,stratum,kind,producer,status,num_recall,num_prec,wer,coverage,pages,corrob,"
    "tokens,model_calls,cov_per_1k"
)


def _eff_str(v: Optional[float]) -> str:
    return "inf(free)" if v is None else f"{v:.4f}"


def render_report(rollup: RollupReport) -> str:
    """Deterministic line-based render (two runs diff empty)."""
    lines: List[str] = []
    lines.append(
        "# fi-producer-compare — Level-1 producer usefulness vs sibling main.xml "
        "gold (word-coverage / WER / NUMERIC-exact recall+precision, per-producer "
        "token cost)"
    )
    compared = [r for r in rollup.reports if r.pair_status == "compared"]
    failed = [r for r in rollup.reports if r.pair_status != "compared"]
    by_stratum: Dict[str, int] = {}
    for r in compared:
        by_stratum[r.stratum] = by_stratum.get(r.stratum, 0) + 1
    strata_str = "  ".join(f"{s}={n}" for s, n in sorted(by_stratum.items()))
    lines.append(
        f"# pdfs={len(rollup.reports)}  compared={len(compared)}  failed={len(failed)}"
        + (f"  ({strata_str})" if strata_str else "")
    )
    if rollup.skipped:
        lines.append(
            "# SKIPPED (unavailable on every PDF, never silently omitted): "
            + ", ".join(rollup.skipped)
        )
    lines.append("")
    lines.append("## PER-PDF x PER-PRODUCER")
    lines.append(_SCORE_HEADER)
    for rep in sorted(rollup.reports, key=lambda r: (r.stratum, r.pdf_locator)):
        if rep.pair_status != "compared":
            lines.append(f"# FAILED [{rep.stratum}] {rep.pdf_locator}: {rep.detail}")
            continue
        for sc in rep.scores:
            lines.append(
                ",".join(
                    str(v)
                    for v in (
                        rep.pdf_locator,
                        rep.stratum,
                        rep.dominant_kind,
                        sc.producer,
                        sc.score_status,
                        f"{sc.numeric_recall:.4f}",
                        f"{sc.numeric_precision:.4f}",
                        f"{sc.wer:.4f}",
                        f"{sc.word_coverage:.4f}",
                        sc.pages_with_content,
                        sc.corroborating_pages,
                        sc.total_tokens,
                        sc.model_calls,
                        _eff_str(sc.coverage_per_1k_tokens),
                    )
                )
            )
    lines.append("")
    lines.append("## AGGREGATE (per stratum x page-kind x producer)")
    lines.append(
        "stratum,kind,producer,n_pdfs,mean_coverage,mean_wer,mean_num_recall,"
        "total_tokens,efficiency"
    )
    for a in rollup.aggregates:
        lines.append(
            ",".join(
                str(v)
                for v in (
                    a.stratum,
                    a.kind,
                    a.producer,
                    a.n_pdfs,
                    f"{a.mean_coverage:.4f}",
                    f"{a.mean_wer:.4f}",
                    f"{a.mean_numeric_recall:.4f}",
                    a.total_tokens,
                    _eff_str(a.efficiency),
                )
            )
        )
    lines.append("")
    lines.append(
        "## PER (stratum/kind) WINNER (faithfulness-per-token; free lane wins at >= coverage)"
    )
    lines.append("stratum_kind,winner")
    for key in sorted(rollup.kind_winners):
        lines.append(f"{key},{rollup.kind_winners[key]}")
    return "\n".join(lines)


def report_to_json(rollup: RollupReport) -> Dict[str, Any]:
    """JSON form of the rollup (same deterministic order as the render)."""
    return {
        "reports": [
            {
                "pdf_locator": r.pdf_locator,
                "xml_locator": r.xml_locator,
                "stratum": r.stratum,
                "pair_status": r.pair_status,
                "dominant_kind": r.dominant_kind,
                "page_kinds": r.page_kinds,
                "detail": r.detail,
                "scores": [
                    {
                        "producer": s.producer,
                        "score_status": s.score_status,
                        "numeric_recall": s.numeric_recall,
                        "numeric_precision": s.numeric_precision,
                        "wer": s.wer,
                        "word_coverage": s.word_coverage,
                        "reconstructed_chars": s.reconstructed_chars,
                        "pages_with_content": s.pages_with_content,
                        "corroborating_pages": s.corroborating_pages,
                        "assurance_tier": s.assurance_tier,
                        "input_tokens": s.input_tokens,
                        "output_tokens": s.output_tokens,
                        "total_tokens": s.total_tokens,
                        "model_calls": s.model_calls,
                        "model_wall_s": s.model_wall_s,
                        "producer_wall_s": s.producer_wall_s,
                        "coverage_per_1k_tokens": s.coverage_per_1k_tokens,
                        "detail": s.detail,
                    }
                    for s in r.scores
                ],
            }
            for r in sorted(rollup.reports, key=lambda r: (r.stratum, r.pdf_locator))
        ],
        "aggregates": [
            {
                "stratum": a.stratum,
                "kind": a.kind,
                "producer": a.producer,
                "n_pdfs": a.n_pdfs,
                "mean_coverage": a.mean_coverage,
                "mean_wer": a.mean_wer,
                "mean_numeric_recall": a.mean_numeric_recall,
                "total_tokens": a.total_tokens,
                "efficiency": a.efficiency,
            }
            for a in rollup.aggregates
        ],
        "kind_winners": rollup.kind_winners,
        "skipped": list(rollup.skipped),
    }


# --------------------------------------------------------------------------- #
# Planning (--dry-run) + CLI.                                                    #
# --------------------------------------------------------------------------- #


def plan_run(
    pairs: Sequence[PdfXmlPair], producers: Mapping[str, Level1Producer]
) -> str:
    """A --dry-run plan: selected pairs + which producers are available/SKIPPED.

    Probes ``is_available()`` (cheap health checks) but runs NO inference — the
    hermetic proof that availability degrades gracefully and the skip list is
    surfaced BEFORE any GPU work."""
    lines: List[str] = []
    lines.append("# fi-producer-compare DRY-RUN plan (no inference)")
    by_stratum: Dict[str, int] = {}
    for p in pairs:
        by_stratum[p.stratum] = by_stratum.get(p.stratum, 0) + 1
    strata_str = "  ".join(f"{s}={n}" for s, n in sorted(by_stratum.items()))
    lines.append(f"# selected same-document PDF<->XML pairs: {len(pairs)}  ({strata_str})")
    for m in pairs:
        lines.append(f"  [{m.stratum}] PDF  {m.pdf_locator}")
        lines.append(f"  [{m.stratum}] XML  {m.xml_locator}")
    lines.append("")
    lines.append("# producers")
    available: List[str] = []
    skipped: List[str] = []
    for name, prod in producers.items():
        try:
            ok = prod.is_available()
        except Exception as exc:  # a probe hiccup is a SKIP, not a crash
            ok = False
            lines.append(f"  {name:<16} SKIPPED (probe error: {type(exc).__name__})")
            skipped.append(name)
            continue
        lines.append(f"  {name:<16} {'AVAILABLE' if ok else 'SKIPPED (unavailable)'}")
        (available if ok else skipped).append(name)
    lines.append("")
    lines.append(f"# will run: {', '.join(available) or '(none)'}")
    lines.append(f"# skipped:  {', '.join(skipped) or '(none)'}")
    return "\n".join(lines)


def _pair_for_locator(locator: str, *, he_farchive: str) -> PdfXmlPair:
    """Resolve a single HE ``--locator`` (``.../main.pdf``) to its PDF<->XML pair."""
    if not locator.endswith(_HE_PDF_SUFFIX):
        raise SystemExit(
            f"fi-producer-compare: locator must be an HE {_HE_PDF_SUFFIX}: {locator}"
        )
    xml = locator[: -len(_HE_PDF_SUFFIX)] + "/main.xml"
    return PdfXmlPair(locator, xml, he_farchive, he_farchive, "he")


def _select_pairs(
    *, he_farchive: str, locator: Optional[str], lang: str, limit: Optional[int]
) -> List[PdfXmlPair]:
    """A single ``--locator`` pair, or the clean-gold HE enumeration (deterministic)."""
    if locator is not None:
        return [_pair_for_locator(locator, he_farchive=he_farchive)]
    return enumerate_pairs(he_farchive=he_farchive, lang=lang, limit=limit)


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-producer-compare``."""
    he_farchive = args.he_farchive or _HE_FARCHIVE_DEFAULT
    pairs = _select_pairs(
        he_farchive=he_farchive, locator=args.locator, lang=args.lang, limit=args.limit
    )
    producers = build_producers(base_url=args.base_url)

    if args.dry_run:
        print(plan_run(pairs, producers))
        return

    if not pairs:
        raise SystemExit("fi-producer-compare: no PDF<->XML pairs selected")

    reports: List[PdfProducerReport] = []
    for pair in pairs:
        reports.append(
            compare_pdf(
                pair,
                producers,
                base_url=args.base_url,
                max_pages=args.max_pages,
                appraise=not args.no_appraise,
            )
        )
    rollup = build_rollup(reports, list(producers))

    if args.json:
        payload = json.dumps(report_to_json(rollup), ensure_ascii=False, indent=2)
        print(payload)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(payload)
    else:
        print(render_report(rollup))
