"""``lawvm fi-parse-compare`` — span-copy (v2) vs full-transcription vs XML gold.

Runs BOTH the v2 structured build-script lanes AND (optionally) the legacy flat
full-transcription lane on the SAME PDF and reports, per page and total:

  * output char count per modality (span should be << full — the ratio is the
    output-sparse economics the LLM guide targets);
  * reconstructed-text word-overlap / recall of span vs full, and — where a
    sibling ``main.xml`` gold exists (a government-proposal HE) — vs the XML text;
  * structural fidelity: node count, tree depth, #tables, #headings, #images per
    modality;
  * assurance-tier histogram per modality;
  * 0x1F terminator-compliance rate (the known small-VLM risk).

The PRIMARY fair comparison is ``struct_span`` vs ``struct_full``: same
build-script grammar, the ONLY difference is how a text leaf is populated
(reading-order span-copy vs inline model transcription). ``struct_patch``
(span-copy + addressed char-span deltas) is reported alongside.

The vision backend is assumed present (localhost:8080); if it is unreachable the
harness FAILS LOUD (``ParseBackendUnavailable``) — there is no deterministic
fallback, consistent with the parsed_store discipline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind

_GOV_FARCHIVE = "data/fi_government_proposal.farchive"


@dataclass(frozen=True, slots=True)
class LaneReport:
    """One modality's structural + economic + assurance summary over a PDF."""

    modality: str
    output_chars: int
    reconstructed_chars: int
    node_count: int
    tree_depth: int
    n_tables: int
    n_headings: int
    n_images: int
    tier_histogram: Dict[str, int]
    terminator_rate: Optional[float]
    words: frozenset = field(default_factory=frozenset)


def _tree_stats(root: SourceDocumentNode) -> Tuple[int, int, int, int, int, Dict[str, int], str]:
    """(node_count, depth, tables, headings, images, tier_hist, reconstructed_text)."""
    node_count = 0
    tables = headings = images = 0
    tiers: Dict[str, int] = {}
    texts: List[str] = []

    def _walk(n: SourceDocumentNode, depth: int) -> int:
        nonlocal node_count, tables, headings, images
        node_count += 1
        tiers[n.assurance_tier.name] = tiers.get(n.assurance_tier.name, 0) + 1
        if n.kind is SourceDocumentNodeKind.TABLE:
            tables += 1
        elif n.kind is SourceDocumentNodeKind.HEADING:
            headings += 1
        elif n.kind is SourceDocumentNodeKind.IMAGE_REGION:
            images += 1
        if n.text:
            texts.append(n.text)
        max_child = depth
        for c in n.children:
            max_child = max(max_child, _walk(c, depth + 1))
        return max_child

    depth = _walk(root, 0)
    return node_count, depth, tables, headings, images, tiers, "\n".join(texts)


def _words(text: str) -> frozenset:
    return frozenset(text.lower().split())


def _overlap(a: frozenset, b: frozenset) -> float:
    """Jaccard word overlap (0..1); recall of ``a`` against ``b`` uses ``a`` as denom."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _recall(candidate: frozenset, gold: frozenset) -> float:
    """Fraction of gold words present in candidate (candidate recall of gold)."""
    if not gold:
        return 0.0
    return len(candidate & gold) / len(gold)


def _struct_lane_report(
    manifestation: SourceManifestation, modality: str, max_pages: int, output_chars: List[int]
) -> LaneReport:
    """Run a v2 struct lane over the whole PDF and summarize (fresh, no cache)."""
    from lawvm.finland.source_document.adjudicated_ingest import struct_document_ingest
    from lawvm.finland.source_document.page_elements import PageElementProducer
    from lawvm.finland.source_document.parsed_store import RASTERIZE_DPI

    from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator

    vision = _CharCountingVision(output_chars, max_tokens=3000)
    adjudicator = LlmWorkflowAdjudicator(verify_pass=False, max_tokens=2000)
    result = struct_document_ingest(
        manifestation,
        vision=vision,
        page_element_producer=PageElementProducer(rasterize_dpi=RASTERIZE_DPI),
        adjudicator=adjudicator if adjudicator.is_available() else None,
        max_pages=max_pages,
        transcription_modality=modality,
    )
    root = result.document.root
    node_count, depth, tables, headings, images, tiers, recon = _tree_stats(root)
    terminated, total = result.terminator_stats
    return LaneReport(
        modality=modality,
        output_chars=sum(output_chars),
        reconstructed_chars=len(recon),
        node_count=node_count,
        tree_depth=depth,
        n_tables=tables,
        n_headings=headings,
        n_images=images,
        tier_histogram=tiers,
        terminator_rate=(terminated / total) if total else None,
        words=_words(recon),
    )


class _CharCountingVision:
    """VisionPageProducer wrapper that tallies the model's raw output chars per call."""

    def __init__(self, sink: List[int], **kw) -> None:
        from lawvm.finland.llm_backends.vision_producer import VisionPageProducer

        self._inner = VisionPageProducer(**kw)
        self._sink = sink

    def is_available(self) -> bool:
        return self._inner.is_available()

    def propose_page_struct(self, manifestation, page_num, page_elements, *, leaf_mode="span"):
        from lawvm.finland.llm_backends.vision_producer import VisionProducerTruncated

        try:
            res = self._inner.propose_page_struct(
                manifestation, page_num, page_elements, leaf_mode=leaf_mode
            )
        except VisionProducerTruncated:
            from lawvm.finland.source_document.struct_wire import StructBuildResult
            from lawvm.finland.llm_backends.vision_producer import StructPageResult

            return StructPageResult(build=StructBuildResult(roots=()), raw_content="")
        self._sink.append(len(res.raw_content))
        return res


def _xml_gold_words(gov_farchive: str, year: int, number: int, lang: str) -> Optional[frozenset]:
    """Reading text of the sibling ``main.xml`` gold (HE), or ``None`` if absent."""
    from farchive import Farchive

    from lawvm.finland.he_acquisition import he_locator

    fa = Farchive(gov_farchive)
    try:
        span = fa.resolve(he_locator(year, number, lang, "main.xml"))
        if span is None:
            return None
        data = fa.read(span.digest)
    finally:
        fa.close()
    if not data:
        return None
    # Strip tags mechanically (guide: HTML/XML stripping is a mechanical layer).
    text = data.decode("utf-8", "replace")
    out: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch == "<":
            depth += 1
            if buf:
                out.append("".join(buf))
                buf = []
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return _words(" ".join(out))


@dataclass(frozen=True, slots=True)
class CompareReport:
    source_locator: str
    lanes: Tuple[LaneReport, ...]
    span_vs_full_char_ratio: Optional[float]
    span_vs_full_overlap: Optional[float]
    xml_recall: Dict[str, Optional[float]]


def run_compare(
    manifestation: SourceManifestation,
    *,
    max_pages: int = 6,
    include_patch: bool = True,
    xml_gold: Optional[frozenset] = None,
) -> CompareReport:
    """Run struct_span, struct_full, and (optionally) struct_patch over one PDF + report."""
    lanes: List[LaneReport] = []
    span_report = _struct_lane_report(manifestation, "struct_span", max_pages, [])
    lanes.append(span_report)
    full_report = _struct_lane_report(manifestation, "struct_full", max_pages, [])
    lanes.append(full_report)
    if include_patch:
        lanes.append(_struct_lane_report(manifestation, "struct_patch", max_pages, []))

    ratio = (
        span_report.output_chars / full_report.output_chars
        if full_report.output_chars
        else None
    )
    overlap = _overlap(span_report.words, full_report.words)
    xml_recall: Dict[str, Optional[float]] = {}
    if xml_gold is not None:
        for lr in lanes:
            xml_recall[lr.modality] = _recall(lr.words, xml_gold)
    return CompareReport(
        source_locator=manifestation.locator,
        lanes=tuple(lanes),
        span_vs_full_char_ratio=ratio,
        span_vs_full_overlap=overlap,
        xml_recall=xml_recall,
    )


def _load_manifestation(
    farchive: str, locator: str, *, source_role: str = "he_draft"
) -> SourceManifestation:
    from farchive import Farchive

    fa = Farchive(farchive)
    try:
        span = fa.resolve(locator)
        if span is None:
            raise SystemExit(f"fi-parse-compare: locator not found in {farchive}: {locator}")
        data = fa.read(span.digest)
    finally:
        fa.close()
    if not data:
        raise SystemExit(f"fi-parse-compare: empty blob for {locator}")
    return SourceManifestation(
        artifact_digest=hashlib.sha256(data).hexdigest(),
        source_bytes=data,
        locator=locator,
        source_role=source_role,
        fetched_at=datetime.now(tz=timezone.utc),
        media_type="application/pdf",
    )


def _print_report(report: CompareReport) -> None:
    print(f"fi-parse-compare  {report.source_locator}")
    print("=" * 78)
    hdr = f"{'modality':<14}{'out_chars':>10}{'recon':>8}{'nodes':>7}{'depth':>6}{'tbl':>5}{'hdr':>5}{'img':>5}{'term%':>7}"
    print(hdr)
    for lr in report.lanes:
        term = f"{lr.terminator_rate*100:.0f}" if lr.terminator_rate is not None else "-"
        print(
            f"{lr.modality:<14}{lr.output_chars:>10}{lr.reconstructed_chars:>8}"
            f"{lr.node_count:>7}{lr.tree_depth:>6}{lr.n_tables:>5}{lr.n_headings:>5}"
            f"{lr.n_images:>5}{term:>7}"
        )
    print("-" * 78)
    if report.span_vs_full_char_ratio is not None:
        print(
            f"span/full output-char ratio: {report.span_vs_full_char_ratio:.3f} "
            f"(struct_span is {1/report.span_vs_full_char_ratio:.1f}x cheaper on output)"
            if report.span_vs_full_char_ratio
            else "span/full ratio: n/a"
        )
    print(f"span-vs-full reconstructed word overlap (Jaccard): {report.span_vs_full_overlap:.3f}")
    if report.xml_recall:
        print("XML-gold word recall:")
        for modality, r in report.xml_recall.items():
            print(f"  {modality:<14}{r:.3f}" if r is not None else f"  {modality:<14}n/a")
    print("assurance-tier histograms:")
    for lr in report.lanes:
        print(f"  {lr.modality:<14}{lr.tier_histogram}")


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-parse-compare``."""
    farchive = args.farchive or _GOV_FARCHIVE
    manifestation = _load_manifestation(farchive, args.locator)
    xml_gold: Optional[frozenset] = None
    if args.he:
        year_s, _, num_s = args.he.partition("/")
        if year_s.isdigit() and num_s.isdigit():
            xml_gold = _xml_gold_words(farchive, int(year_s), int(num_s), args.lang or "fin")
    report = run_compare(
        manifestation,
        max_pages=args.max_pages,
        include_patch=not args.no_patch,
        xml_gold=xml_gold,
    )
    if args.json:
        print(json.dumps(_report_to_json(report), ensure_ascii=False, indent=2))
    else:
        _print_report(report)


def _report_to_json(report: CompareReport) -> dict:
    return {
        "source_locator": report.source_locator,
        "span_vs_full_char_ratio": report.span_vs_full_char_ratio,
        "span_vs_full_overlap": report.span_vs_full_overlap,
        "xml_recall": report.xml_recall,
        "lanes": [
            {
                "modality": lr.modality,
                "output_chars": lr.output_chars,
                "reconstructed_chars": lr.reconstructed_chars,
                "node_count": lr.node_count,
                "tree_depth": lr.tree_depth,
                "n_tables": lr.n_tables,
                "n_headings": lr.n_headings,
                "n_images": lr.n_images,
                "tier_histogram": lr.tier_histogram,
                "terminator_rate": lr.terminator_rate,
            }
            for lr in report.lanes
        ],
    }
