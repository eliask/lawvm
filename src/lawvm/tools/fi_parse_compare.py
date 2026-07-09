"""``lawvm fi-parse-compare`` — full-doc PDF→IR reconstruction vs authoritative XML.

The comparison is WHOLE-DOCUMENT-vs-WHOLE-DOCUMENT, both sides the SAME document:

  * the PDF is parsed (all pages) through the v2 build-script lane into LawVM IR
    (cached in a derived ParsedIrStore), and its reading text is recovered from
    the IR tree;
  * the authoritative XML's body text is recovered from its ``mainBody``;
  * both texts are de-hyphenated IDENTICALLY (soft-hyphen line breaks are not real
    differences) before any comparison.

Two layers of comparison are produced:

  1. a cheap DETERMINISTIC structural summary — per-lane node/table/heading/image
     counts, tree depth, assurance-tier histogram, 0x1F terminator-compliance, the
     span/full output-char economics, and (full-doc-vs-full-doc, de-hyphenated on
     BOTH sides) a word-overlap coverage figure. This is honest: it is the SAME
     document on both sides, so coverage near 1.0 means the reconstruction is
     faithful. (The old tool reported word "recall" of a FEW parsed pages against
     the WHOLE XML — structurally guaranteed to look terrible even for a perfect
     parse. That metric is gone.)

  2. an opt-in INTELLIGENT adjudication (``--adjudicate``): both full texts are
     handed to the local model, which finds and CATEGORISES the genuine contextual
     differences (MISSING / EXTRA / OCR / NUMERIC / STRUCTURE) — emphasising
     legally-significant numeric / citation / § discrepancies — and returns a
     VERDICT. This is the real value: it distinguishes a faithful reconstruction
     from one that dropped a section or misread a euro amount, which word counting
     never could.

The vision backend is assumed present (localhost:8080); if it is unreachable the
harness FAILS LOUD (``ParseBackendUnavailable``) — there is no deterministic
fallback, consistent with the parsed_store discipline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind
from lawvm.finland.source_document.page_elements import dehyphenate

_GOV_FARCHIVE = "data/fi_government_proposal.farchive"


# --------------------------------------------------------------------------- #
# Full-doc text extraction (both witnesses)                                    #
# --------------------------------------------------------------------------- #


def _ir_dict_text(node: Dict[str, Any]) -> str:
    """Recover the reading text of a parsed-IR record (``to_jsonable_dict`` shape).

    Depth-first concatenation of every node's ``text`` — the reconstruction of the
    PDF the parse produced, from the cached IR dict.
    """
    parts: List[str] = []

    def _walk(n: Dict[str, Any]) -> None:
        t = n.get("text")
        if t:
            parts.append(str(t))
        for c in n.get("children") or ():
            _walk(c)

    _walk(node)
    return "\n".join(parts)


def _source_node_text(root: SourceDocumentNode) -> str:
    """Recover the reading text of a live ``SourceDocumentNode`` tree."""
    parts: List[str] = []

    def _walk(n: SourceDocumentNode) -> None:
        if n.text:
            parts.append(n.text)
        for c in n.children:
            _walk(c)

    _walk(root)
    return "\n".join(parts)


def xml_body_text(xml_bytes: bytes) -> str:
    """Authoritative body text from an AKN/HE XML: ``mainBody`` itertext, joined.

    ``xml_to_ir_node`` rejects a ``mainBody`` root (it is not an IRNodeKind); the
    robust route to the authoritative text is ``mainBody``'s ``itertext`` (as the
    intel-diff prototype does), falling back to the whole document body if no
    ``mainBody`` element is present (namespace-agnostic ``{*}`` match).
    """
    from lxml import etree

    root = etree.fromstring(xml_bytes)
    body = root.find(".//{*}mainBody")
    if body is None:
        body = root.find(".//{*}body")
    if body is None:
        body = root
    chunks: List[str] = []
    for t in body.itertext():
        if t is None:
            continue
        stripped = str(t).strip()
        if stripped:
            chunks.append(stripped)
    return "\n".join(chunks)


# --------------------------------------------------------------------------- #
# Deterministic structural summary                                             #
# --------------------------------------------------------------------------- #


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
    """De-hyphenated, lower-cased word set (soft-hyphen line breaks are not words)."""
    return frozenset(dehyphenate(text).lower().split())


def _coverage(candidate: frozenset, authoritative: frozenset) -> float:
    """Fraction of the AUTHORITATIVE words present in the candidate reconstruction.

    Full-doc vs full-doc, both de-hyphenated: this is honest coverage, not the old
    few-pages-vs-whole-doc "recall". 1.0 means every XML body word appears in the
    PDF reconstruction.
    """
    if not authoritative:
        return 0.0
    return len(candidate & authoritative) / len(authoritative)


def _struct_lane_report(
    manifestation: SourceManifestation, modality: str, max_pages: int, output_chars: List[int]
) -> LaneReport:
    """Run a v2 struct lane over the whole PDF and summarize (fresh, no cache)."""
    from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator
    from lawvm.finland.source_document.adjudicated_ingest import struct_document_ingest
    from lawvm.finland.source_document.page_elements import PageElementProducer
    from lawvm.finland.source_document.parsed_store import RASTERIZE_DPI

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

    def __init__(self, sink: List[int], **kw: Any) -> None:
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
            from lawvm.finland.llm_backends.vision_producer import StructPageResult
            from lawvm.finland.source_document.struct_wire import StructBuildResult

            return StructPageResult(build=StructBuildResult(roots=()), raw_content="")
        self._sink.append(len(res.raw_content))
        return res


# --------------------------------------------------------------------------- #
# INTELLIGENT XML-vs-PDF adjudication                                          #
# --------------------------------------------------------------------------- #

_DIFF_SYSTEM_PROMPT = (
    "You compare TWO transcriptions of the SAME Finnish legal document (a government "
    "proposal, HE). TEXT A is the AUTHORITATIVE content from the official XML. TEXT B "
    "is reconstructed from the scanned PDF by a vision model. Find every GENUINE "
    "contextual difference and CATEGORISE each on its own line, prefixed with the "
    "category label and a colon, as:\n"
    "  MISSING:  substantive content in A that is absent or garbled in B\n"
    "  EXTRA:    text in B that is not real body content (page numbers, running "
    "headers, artifacts)\n"
    "  OCR:      a word B misread/garbled vs A\n"
    "  NUMERIC:  a number, date, euro amount, section (§), or citation that DIFFERS "
    "between A and B — these are LEGALLY SIGNIFICANT, list every one first\n"
    "  STRUCTURE: ordering or nesting that differs materially\n"
    "IGNORE pure hyphenation (soft-hyphen line breaks), whitespace, and tokenisation "
    "differences — they are not real differences. Be comprehensive but list ONLY real "
    "differences, most legally-significant first. If a category has none, skip it. Do "
    "NOT repeat a difference; state each once and move on. End with exactly one line: "
    "VERDICT: <faithful|minor-issues|material-issues> — <one clause>."
)

_DIFF_CATEGORIES = ("MISSING", "EXTRA", "OCR", "NUMERIC", "STRUCTURE")
_VERDICT_MARKER = "VERDICT:"

# Repetition guard: the local VLM (Qwen3.x) was observed dumping ~30 repeated
# "No, TEXT B says…" lines when uncertain about a formula. ``repeat_penalty`` +
# ``presence_penalty`` damp that loop; ``max_tokens`` caps the blast radius; and
# ``_repetition_ratio`` flags any residual pathological repetition so we report it
# instead of presenting the garbage as a real diff.
_REPEAT_PENALTY = 1.1
_PRESENCE_PENALTY = 0.5
_DIFF_MAX_TOKENS = 4000
# Fraction of non-blank output lines that may be duplicates before we call it
# pathological (a healthy diff has few or no exact-duplicate lines).
_REPETITION_THRESHOLD = 0.5


def _repetition_ratio(text: str) -> float:
    """Fraction of non-blank lines that are exact duplicates of an earlier line.

    A pathological repetition-loop reply (the guarded VLM failure mode) is almost
    all duplicate lines → ratio near 1.0; a healthy categorized diff → near 0.0.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        return 0.0
    seen: set[str] = set()
    dup = 0
    for ln in lines:
        if ln in seen:
            dup += 1
        else:
            seen.add(ln)
    return dup / len(lines)


@dataclass(frozen=True, slots=True)
class AdjudicatedDiff:
    """The categorized XML-vs-PDF difference list + verdict from the model."""

    categorized: Dict[str, Tuple[str, ...]]
    verdict: str
    raw: str
    repetition_ratio: float
    pathological_repetition: bool
    finish_reason: Optional[str]


def parse_categorized_diff(content: str) -> Tuple[Dict[str, Tuple[str, ...]], str]:
    """Parse the model reply into ({CATEGORY: (line, …)}, verdict).

    Each output line is bucketed by its leading ``CATEGORY:`` label; the trailing
    ``VERDICT:`` line is extracted separately. Lines with no recognised prefix are
    ignored (commentary the model may add despite the prompt).
    """
    buckets: Dict[str, List[str]] = {c: [] for c in _DIFF_CATEGORIES}
    verdict = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(_VERDICT_MARKER):
            verdict = line[len(_VERDICT_MARKER):].strip()
            continue
        for cat in _DIFF_CATEGORIES:
            if upper.startswith(cat + ":") or upper.startswith(cat + " :"):
                buckets[cat].append(line.split(":", 1)[1].strip())
                break
    categorized = {c: tuple(v) for c, v in buckets.items() if v}
    return categorized, verdict


class XmlPdfDiffAdjudicator:
    """Intelligent full-text XML-vs-PDF diff over the local OpenAI-compat server.

    Reuses the ``LlmWorkflowAdjudicator`` transport shape: the HTTP POST is the
    ``_chat`` seam so the categorisation + repetition-guard logic is testable
    without a server. Truncation and transport failures surface, never silently
    return garbage; a pathological repetition loop is FLAGGED, not presented as a
    diff.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = _DIFF_MAX_TOKENS,
        timeout: float = 300.0,
    ) -> None:
        from lawvm.finland.llm_backends.llm_adjudicator import DEFAULT_BASE_URL

        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._last_finish_reason: Optional[str] = None

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._base_url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        try:
            with urllib.request.urlopen(f"{self._base_url}/v1/models", timeout=5) as resp:
                payload = json.loads(resp.read())
            models = payload.get("models") or payload.get("data") or []
            if models and (models[0].get("model") or models[0].get("id")):
                return str(models[0].get("model") or models[0].get("id"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            pass
        return "qwen"

    def _payload(self, system: str, user: str) -> Dict[str, Any]:
        """Build the chat payload WITH the repetition-guard fields (guarded seam)."""
        return {
            "model": self._resolve_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            # --- repetition guard ---
            "repeat_penalty": _REPEAT_PENALTY,
            "presence_penalty": _PRESENCE_PENALTY,
        }

    # -- transport seam (overridable / mockable in tests) -------------------

    def _chat(self, system: str, user: str) -> str:
        """POST one chat turn; return content. Record the finish_reason."""
        payload = self._payload(system, user)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            out = json.loads(resp.read())
        choice = out["choices"][0]
        self._last_finish_reason = choice.get("finish_reason")
        return str(choice["message"]["content"])

    def adjudicate(self, xml_text: str, pdf_text: str) -> AdjudicatedDiff:
        """Diff the two full witnesses → categorized list + verdict + repetition flag."""
        user = (
            f"TEXT A (authoritative XML):\n{xml_text}\n\n=====\n\n"
            f"TEXT B (PDF reconstruction):\n{pdf_text}\n\n"
            "List the genuine differences."
        )
        content = self._chat(_DIFF_SYSTEM_PROMPT, user)
        ratio = _repetition_ratio(content)
        pathological = ratio >= _REPETITION_THRESHOLD
        if pathological:
            # Do not present the loop garbage as a real diff; flag it.
            return AdjudicatedDiff(
                categorized={},
                verdict="(withheld — model produced a pathological repetition loop)",
                raw=content,
                repetition_ratio=ratio,
                pathological_repetition=True,
                finish_reason=self._last_finish_reason,
            )
        categorized, verdict = parse_categorized_diff(content)
        return AdjudicatedDiff(
            categorized=categorized,
            verdict=verdict,
            raw=content,
            repetition_ratio=ratio,
            pathological_repetition=False,
            finish_reason=self._last_finish_reason,
        )


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CompareReport:
    source_locator: str
    lanes: Tuple[LaneReport, ...]
    span_vs_full_char_ratio: Optional[float]
    span_vs_full_overlap: Optional[float]
    # full-doc-vs-full-doc coverage of the authoritative XML words per lane.
    xml_coverage: Dict[str, Optional[float]]
    adjudicated_diff: Optional[AdjudicatedDiff] = None


def _overlap(a: frozenset, b: frozenset) -> float:
    """Jaccard word overlap (0..1) — symmetric span-vs-full agreement."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def run_compare(
    manifestation: SourceManifestation,
    *,
    max_pages: int = 5000,
    include_patch: bool = True,
    xml_gold_text: Optional[str] = None,
    adjudicate: bool = False,
    adjudicator: Optional[XmlPdfDiffAdjudicator] = None,
) -> CompareReport:
    """Full-doc struct lanes over one PDF + (opt-in) intelligent XML-vs-PDF diff."""
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

    xml_coverage: Dict[str, Optional[float]] = {}
    xml_words: Optional[frozenset] = None
    if xml_gold_text is not None:
        xml_words = _words(xml_gold_text)
        for lr in lanes:
            xml_coverage[lr.modality] = _coverage(lr.words, xml_words)

    diff: Optional[AdjudicatedDiff] = None
    if adjudicate and xml_gold_text is not None:
        adj = adjudicator or XmlPdfDiffAdjudicator()
        # Adjudicate the primary (span) full-doc reconstruction against the XML
        # gold; both sides de-hyphenated identically before the model sees them.
        diff = adj.adjudicate(
            dehyphenate(xml_gold_text),
            dehyphenate(_lane_reconstructed_text(manifestation, max_pages)),
        )

    return CompareReport(
        source_locator=manifestation.locator,
        lanes=tuple(lanes),
        span_vs_full_char_ratio=ratio,
        span_vs_full_overlap=overlap,
        xml_coverage=xml_coverage,
        adjudicated_diff=diff,
    )


def _lane_reconstructed_text(manifestation: SourceManifestation, max_pages: int) -> str:
    """Full-doc struct_span reconstruction text via the cached ParsedIrStore.

    The adjudicator wants the ACTUAL reading text (not a word set); parse (cached)
    the span lane and recover it from the IR tree — the same path the intel-diff
    prototype uses.
    """
    from lawvm.finland.source_document.parsed_store import (
        ParsedIrStore,
        parse_struct_and_cache,
        resolve_pipeline,
    )

    spec = resolve_pipeline(transcription_modality="struct_span", vision_max_tokens=3000)
    store = ParsedIrStore()
    try:
        rec = parse_struct_and_cache(
            manifestation, store, spec=spec, max_pages=max_pages
        )
    finally:
        store.close()
    return _ir_dict_text(rec.ir)


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


def _xml_gold_text(gov_farchive: str, year: int, number: int, lang: str) -> Optional[str]:
    """Authoritative body text of the sibling ``main.xml`` gold (HE), or ``None``."""
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
    return xml_body_text(data)


def _print_report(report: CompareReport) -> None:
    print(f"fi-parse-compare  {report.source_locator}")
    print("=" * 78)
    hdr = (
        f"{'modality':<14}{'out_chars':>10}{'recon':>8}{'nodes':>7}{'depth':>6}"
        f"{'tbl':>5}{'hdr':>5}{'img':>5}{'term%':>7}"
    )
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
    if report.xml_coverage:
        print("XML-gold word coverage (full-doc vs full-doc, de-hyphenated both sides):")
        for modality, c in report.xml_coverage.items():
            print(f"  {modality:<14}{c:.3f}" if c is not None else f"  {modality:<14}n/a")
    print("assurance-tier histograms:")
    for lr in report.lanes:
        print(f"  {lr.modality:<14}{lr.tier_histogram}")
    if report.adjudicated_diff is not None:
        _print_adjudicated_diff(report.adjudicated_diff)


def _print_adjudicated_diff(diff: AdjudicatedDiff) -> None:
    print("-" * 78)
    print("INTELLIGENT XML-vs-PDF DIFF (categorized)")
    if diff.finish_reason:
        print(f"  finish_reason={diff.finish_reason}  repetition_ratio={diff.repetition_ratio:.2f}")
    if diff.pathological_repetition:
        print("  !! pathological repetition loop detected — diff withheld (see raw)")
        print(f"  {diff.verdict}")
        return
    for cat in _DIFF_CATEGORIES:
        items = diff.categorized.get(cat)
        if not items:
            continue
        print(f"  {cat}:")
        for it in items:
            print(f"    - {it}")
    print(f"  VERDICT: {diff.verdict}")


def _report_to_json(report: CompareReport) -> dict:
    out: Dict[str, Any] = {
        "source_locator": report.source_locator,
        "span_vs_full_char_ratio": report.span_vs_full_char_ratio,
        "span_vs_full_overlap": report.span_vs_full_overlap,
        "xml_coverage": report.xml_coverage,
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
    if report.adjudicated_diff is not None:
        d = report.adjudicated_diff
        out["adjudicated_diff"] = {
            "categorized": {k: list(v) for k, v in d.categorized.items()},
            "verdict": d.verdict,
            "repetition_ratio": d.repetition_ratio,
            "pathological_repetition": d.pathological_repetition,
            "finish_reason": d.finish_reason,
        }
    return out


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-parse-compare``."""
    farchive = args.farchive or _GOV_FARCHIVE
    manifestation = _load_manifestation(farchive, args.locator)
    xml_gold_text: Optional[str] = None
    if args.he:
        year_s, _, num_s = args.he.partition("/")
        if year_s.isdigit() and num_s.isdigit():
            xml_gold_text = _xml_gold_text(farchive, int(year_s), int(num_s), args.lang or "fin")
    adjudicate = bool(getattr(args, "adjudicate", False))
    if adjudicate and xml_gold_text is None:
        raise SystemExit(
            "fi-parse-compare: --adjudicate needs the XML gold; pass --he YEAR/NUM"
        )
    report = run_compare(
        manifestation,
        max_pages=args.max_pages,
        include_patch=not args.no_patch,
        xml_gold_text=xml_gold_text,
        adjudicate=adjudicate,
    )
    if args.json:
        print(json.dumps(_report_to_json(report), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
