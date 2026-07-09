#!/usr/bin/env python3
"""Standalone CROSS-WITNESS validation harness for the Nemotron-Parse witness.

WHAT IT IS
----------
Nemotron-Parse is one INDEPENDENT vision witness among several. The signal that
makes an independent witness worth anything is CROSS-WITNESS AGREEMENT: where two
producers that share no code agree on a page's text, the read is corroborated;
where they disagree, a garble is localized to a concrete line. This script runs
up to three witnesses over the SAME rendered page and reports that signal.

Witnesses (each yields text fragments for one page):
  * pdfium  — the deterministic pypdfium2 text layer (reading-order lines). Always
              available for a digital PDF; the baseline every model read is scored
              against. (``lawvm.ingest.page_elements.PageElementProducer``)
  * vision  — the local llama.cpp vision producer at :8080, if reachable
              (``lawvm.ingest.llm_backends.vision_producer.VisionPageProducer``).
  * nemotron— the process-isolated Nemotron-Parse service, if reachable
              (``lawvm.ingest.llm_backends.nemotron_client.NemotronParseClient``;
              INERT unless ``LAWVM_NEMOTRON_PARSE_CMD`` is set).

It does NOT wire anything into core adjudication. It only REPORTS the cross-
witness signal (corroboration + localized disagreement), line-based, NEVER JSON.

DESIGN — a PURE core + a thin live wiring
-----------------------------------------
The comparison itself (``compare_page`` / ``render_report``) is a PURE function
over ``PageWitness`` carriers: no PDF, no network, no lawvm import. That makes it
HERMETIC-TESTABLE with FAKE witnesses (see ``tests/test_validate_witness.py``).
The CLI (``main``) is the only part that touches farchive + the real producers,
and it imports lawvm lazily so ``--help`` and the pure core work with nothing
installed. Output is deterministic (sorted, fixed-precision) so a report diff is
a stable regression signal.

USAGE (live; operator runs from the main-repo env)
--------------------------------------------------
    export LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM        # farchive data root
    # optional model witnesses:
    #   llama.cpp vision server on :8080 (the RTX 5090's current Qwen tenant)
    #   export LAWVM_NEMOTRON_PARSE_CMD="uv run --project subprojects/nemotron_parse python -m nemotron_parse.serve"

    # a finlex.farchive locator + page numbers:
    uv run --extra pdf python subprojects/nemotron_parse/validate_witness.py \\
        --farchive data/finlex.farchive \\
        --locator 'finlex://sd-cons/1734/4-000/fin@20180107/media/corrigenda/sk20090135_1.pdf' \\
        --pages 1

    # or a local PDF file:
    uv run --extra pdf python subprojects/nemotron_parse/validate_witness.py --pdf /tmp/he.pdf --pages 1-5

The pdfium witness is always run; ``vision`` / ``nemotron`` are included only
when reachable (a probe failure is reported as ``absent``, never a crash). With
only pdfium present the report degrades to a single-witness inventory (no cross
signal) — honest, not empty.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# PURE CORE — carriers + comparison. No PDF, no network, no lawvm import.       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PageWitness:
    """One witness's read of one page: ordered text fragments + a status.

    ``producer_id`` is the witness name (``pdfium`` / ``vision`` / ``nemotron``).
    ``fragments`` are its text blocks/lines IN READING ORDER (kind is not
    compared across witnesses — only text). ``status`` is ``ok`` when the witness
    ran, ``absent`` when it was not reachable, or ``error: <detail>`` when it was
    reached but failed on this page (typed, never a silent empty).
    """

    producer_id: str
    fragments: Tuple[str, ...] = ()
    status: str = "ok"


# A witness callable: given (pdf_bytes, page_num) -> PageWitness. The CLI binds
# real producers; a hermetic test binds fakes.
WitnessFn = Callable[[bytes, int], PageWitness]


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def normalize_tokens(text: str) -> Tuple[str, ...]:
    """Text -> a canonical bag of comparison tokens (lowercase word tokens).

    Cross-witness text never matches byte-for-byte (whitespace, hyphenation, the
    odd glyph), so agreement is scored on a NORMALIZED token bag: NFKC-folded,
    case-folded, split on non-word chars. Numbers and ``§``-adjacent tokens
    survive as their own tokens (``4`` stays ``4``). Pure + deterministic.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return tuple(_WORD_RE.findall(folded))


def _fragment_key(text: str) -> str:
    """A fragment's comparison key: its normalized tokens joined by single spaces.

    Two witnesses' fragments are "the same line" when their token bags match in
    order. Empty-after-normalization fragments collapse to ``""`` and are ignored.
    """
    return " ".join(normalize_tokens(text))


@dataclass(frozen=True, slots=True)
class PairAgreement:
    """Token-bag agreement between two witnesses on one page (order-free)."""

    a: str
    b: str
    shared_tokens: int
    only_a_tokens: int
    only_b_tokens: int

    @property
    def jaccard(self) -> float:
        union = self.shared_tokens + self.only_a_tokens + self.only_b_tokens
        return (self.shared_tokens / union) if union else 1.0


@dataclass(frozen=True, slots=True)
class PageComparison:
    """The cross-witness signal for ONE page.

    ``witnesses`` are the per-witness statuses (in a stable order). ``pairs`` are
    the token-bag agreements for every pair of OK witnesses. ``corroborated`` are
    fragment keys present in >= 2 OK witnesses (a read multiple independent
    producers back); ``localized`` maps each OK witness to the fragment keys ONLY
    it saw (candidate garbles / hallucinations / drops — the localizer). A key is
    the normalized token-join, so the report points at concrete text.
    """

    page_num: int
    witnesses: Tuple[PageWitness, ...]
    pairs: Tuple[PairAgreement, ...]
    corroborated: Tuple[str, ...]
    localized: Tuple[Tuple[str, Tuple[str, ...]], ...]


def _token_bag(fragments: Sequence[str]) -> Dict[str, int]:
    bag: Dict[str, int] = {}
    for frag in fragments:
        for tok in normalize_tokens(frag):
            bag[tok] = bag.get(tok, 0) + 1
    return bag


def _pair_agreement(a: PageWitness, b: PageWitness) -> PairAgreement:
    bag_a, bag_b = _token_bag(a.fragments), _token_bag(b.fragments)
    keys_a, keys_b = set(bag_a), set(bag_b)
    shared = keys_a & keys_b
    return PairAgreement(
        a=a.producer_id,
        b=b.producer_id,
        shared_tokens=len(shared),
        only_a_tokens=len(keys_a - keys_b),
        only_b_tokens=len(keys_b - keys_a),
    )


def _fragment_keys(w: PageWitness) -> List[str]:
    return [k for k in (_fragment_key(f) for f in w.fragments) if k]


def compare_page(page_num: int, witnesses: Sequence[PageWitness]) -> PageComparison:
    """PURE: cross-witness comparison of one page's witness reads.

    Only ``status == 'ok'`` witnesses contribute to pairs/corroboration; an
    ``absent`` / ``error`` witness is reported but never scored (comparing to a
    witness that did not run would fabricate disagreement). Deterministic: pairs
    are in stable witness order; keys are sorted.
    """
    ok = [w for w in witnesses if w.status == "ok"]
    pairs = tuple(
        _pair_agreement(ok[i], ok[j])
        for i in range(len(ok))
        for j in range(i + 1, len(ok))
    )
    # Corroboration: a fragment key seen in >= 2 OK witnesses.
    key_witnesses: Dict[str, set[str]] = {}
    per_witness_keys: Dict[str, set[str]] = {}
    for w in ok:
        keys = set(_fragment_keys(w))
        per_witness_keys[w.producer_id] = keys
        for k in keys:
            key_witnesses.setdefault(k, set()).add(w.producer_id)
    corroborated = tuple(sorted(k for k, ws in key_witnesses.items() if len(ws) >= 2))
    # Localized: fragment keys UNIQUE to a single OK witness.
    localized = tuple(
        (
            w.producer_id,
            tuple(sorted(k for k in per_witness_keys[w.producer_id] if len(key_witnesses[k]) == 1)),
        )
        for w in ok
    )
    return PageComparison(
        page_num=page_num,
        witnesses=tuple(witnesses),
        pairs=pairs,
        corroborated=corroborated,
        localized=localized,
    )


def _truncate(key: str, width: int = 76) -> str:
    return key if len(key) <= width else key[: width - 1] + "…"


def render_report(comparisons: Sequence[PageComparison], *, source: str) -> str:
    """PURE: deterministic LINE-BASED report (NEVER JSON) of the cross signal.

    One section per page: witness statuses, pairwise token agreement (jaccard +
    shared/only counts), the corroborated-read count, and every DISAGREEMENT
    (a fragment only one witness saw) named by its normalized text. A machine can
    grep it; a human can read it; a diff between two runs is a stable signal.
    """
    lines: List[str] = []
    lines.append(f"# cross-witness validation :: {source}")
    lines.append(f"pages {len(comparisons)}")
    for c in comparisons:
        lines.append("")
        lines.append(f"## page {c.page_num}")
        for w in c.witnesses:
            lines.append(f"witness {w.producer_id} status={w.status} fragments={len(w.fragments)}")
        if len(c.pairs) == 0:
            ok_n = sum(1 for w in c.witnesses if w.status == "ok")
            if ok_n < 2:
                lines.append("agreement none (single witness — no cross signal)")
        for p in c.pairs:
            lines.append(
                f"agreement {p.a} vs {p.b} "
                f"jaccard={p.jaccard:.3f} shared={p.shared_tokens} "
                f"only_{p.a}={p.only_a_tokens} only_{p.b}={p.only_b_tokens}"
            )
        lines.append(f"corroborated {len(c.corroborated)}")
        for w_id, keys in c.localized:
            lines.append(f"disagreement {w_id} unique={len(keys)}")
            for k in keys:
                lines.append(f"  only:{w_id}: {_truncate(k)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# LIVE WIRING — the only part that touches farchive + the real producers.      #
# Everything below imports lawvm/pdf libs LAZILY so the pure core stays clean.  #
# --------------------------------------------------------------------------- #


@dataclass
class _LiveWitnesses:
    """Bind the real producers; each entry is absent when its backend is off.

    Reused across pages so a per-page model reload / probe is not re-paid.
    """

    fns: List[Tuple[str, WitnessFn]] = field(default_factory=list)


def _build_pdfium_witness() -> Tuple[str, WitnessFn]:
    from lawvm.ingest.page_elements import PageElementProducer

    producer = PageElementProducer()

    def run(pdf_bytes: bytes, page_num: int) -> PageWitness:
        try:
            elements = producer.page_elements(pdf_bytes, page_num)
        except Exception as exc:  # noqa: BLE001 — report, never crash the harness
            return PageWitness("pdfium", status=f"error: {type(exc).__name__}: {exc}")
        return PageWitness("pdfium", fragments=tuple(elements.lines))

    return ("pdfium", run)


def _walk_struct_text(node: object) -> List[str]:
    out: List[str] = []
    text = getattr(node, "text", "") or ""
    if text.strip():
        out.append(" ".join(text.split()))
    for child in getattr(node, "children", ()) or ():
        out.extend(_walk_struct_text(child))
    return out


def _build_vision_witness(base_url: str) -> Optional[Tuple[str, WitnessFn]]:
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer
    from lawvm.ingest.page_elements import PageElementProducer

    vp = VisionPageProducer(base_url=base_url)
    if not vp.is_available():
        return None
    elements = PageElementProducer()

    def run(pdf_bytes: bytes, page_num: int) -> PageWitness:
        from datetime import datetime

        from lawvm.core.source_document import SourceManifestation

        m = SourceManifestation(
            artifact_digest=_digest(pdf_bytes),
            source_bytes=pdf_bytes,
            locator="validate_witness",
            source_role="statute",
            fetched_at=datetime(2026, 1, 1),
            media_type="application/pdf",
        )
        try:
            page_elems = elements.page_elements(pdf_bytes, page_num)
            result = vp.propose_page_struct(m, page_num, page_elems, leaf_mode="span")
        except Exception as exc:  # noqa: BLE001 — typed report, never crash
            return PageWitness("vision", status=f"error: {type(exc).__name__}: {exc}")
        frags: List[str] = []
        for root in result.build.roots:
            frags.extend(_walk_struct_text(root))
        return PageWitness("vision", fragments=tuple(frags))

    return ("vision", run)


def _build_nemotron_witness() -> Optional[Tuple[str, WitnessFn]]:
    from lawvm.ingest.llm_backends.nemotron_client import NemotronParseClient

    client = NemotronParseClient()
    if not client.is_available():
        return None

    def run(pdf_bytes: bytes, page_num: int) -> PageWitness:
        from datetime import datetime

        from lawvm.core.source_document import SourceManifestation

        m = SourceManifestation(
            artifact_digest=_digest(pdf_bytes),
            source_bytes=pdf_bytes,
            locator="validate_witness",
            source_role="statute",
            fetched_at=datetime(2026, 1, 1),
            media_type="application/pdf",
        )
        try:
            assertions = client.propose_page(m, page_num)
        except Exception as exc:  # noqa: BLE001 — NemotronParseFailure et al.
            return PageWitness("nemotron", status=f"error: {type(exc).__name__}: {exc}")
        return PageWitness("nemotron", fragments=tuple(a.text for a in assertions))

    return ("nemotron", run)


def _digest(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def build_live_witnesses(*, vision_base_url: str) -> _LiveWitnesses:
    """Bind pdfium (always) + vision/nemotron (when reachable) in a stable order.

    A model witness that is off / not READY is simply omitted (its ``absent``
    status is synthesized per page in ``run_pages`` so it still appears in the
    report). Building the witnesses once amortizes the probe over all pages.
    """
    lw = _LiveWitnesses()
    lw.fns.append(_build_pdfium_witness())
    vision = _build_vision_witness(vision_base_url)
    if vision is not None:
        lw.fns.append(vision)
    nemotron = _build_nemotron_witness()
    if nemotron is not None:
        lw.fns.append(nemotron)
    return lw


def run_pages(
    pdf_bytes: bytes,
    pages: Sequence[int],
    witnesses: Sequence[Tuple[str, WitnessFn]],
    *,
    all_witness_ids: Sequence[str] = ("pdfium", "vision", "nemotron"),
) -> List[PageComparison]:
    """Run every bound witness over each page and compare. Absent witnesses are
    reported as ``absent`` so the report inventory is stable across runs."""
    bound_ids = {wid for wid, _ in witnesses}
    comparisons: List[PageComparison] = []
    for page_num in pages:
        page_witnesses: List[PageWitness] = []
        for wid, fn in witnesses:
            page_witnesses.append(fn(pdf_bytes, page_num))
        for wid in all_witness_ids:
            if wid not in bound_ids:
                page_witnesses.append(PageWitness(wid, status="absent"))
        comparisons.append(compare_page(page_num, page_witnesses))
    return comparisons


def _parse_pages(spec: str) -> List[int]:
    """``1-5`` / ``3`` / ``1,4,7`` -> sorted unique 1-indexed page numbers."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            for p in range(int(lo), int(hi) + 1):
                pages.add(p)
        else:
            pages.add(int(part))
    return sorted(p for p in pages if p >= 1)


def _load_pdf_bytes(args: argparse.Namespace) -> Tuple[bytes, str]:
    if args.pdf:
        from pathlib import Path

        data = Path(args.pdf).read_bytes()
        return data, args.pdf
    if not (args.farchive and args.locator):
        raise SystemExit("provide --pdf FILE, or both --farchive DB and --locator URL")
    from farchive import Farchive

    fa = Farchive(args.farchive)
    try:
        data = fa.get(args.locator)
    finally:
        fa.close()
    if not data:
        raise SystemExit(f"locator not found in {args.farchive}: {args.locator}")
    return data, args.locator


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_witness",
        description="Cross-witness (pdfium / vision / nemotron) page-parse agreement report.",
    )
    src = parser.add_argument_group("source (one of)")
    src.add_argument("--pdf", help="a local PDF file path")
    src.add_argument("--farchive", help="a .farchive DB (e.g. data/finlex.farchive)")
    src.add_argument("--locator", help="the farchive locator URL of a PDF blob")
    parser.add_argument("--pages", default="1", help="page spec: '1-5' / '3' / '1,4,7' (1-indexed)")
    parser.add_argument(
        "--vision-base-url",
        default="http://127.0.0.1:8080",
        help="llama.cpp OpenAI-compat vision server base URL",
    )
    args = parser.parse_args(argv)

    pages = _parse_pages(args.pages)
    if not pages:
        raise SystemExit("no valid pages parsed from --pages")
    pdf_bytes, source = _load_pdf_bytes(args)
    live = build_live_witnesses(vision_base_url=args.vision_base_url)
    comparisons = run_pages(pdf_bytes, pages, live.fns)
    sys.stdout.write(render_report(comparisons, source=source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
