"""Level-2 de-facsimile adjudicator — the intelligent holistic composer (Track C).

The mechanical affordances (margin-band from ``geom.band``, cross-page recurrence
from ``rec.band_count``, seam-window near-duplicate shingling) only SURFACE
candidates; the MODEL decides each as DROP/DEDUP/REJOIN(+absorb)/KEEP/REORDER
(spec §2, guiding principle: always intelligence, never mechanical heuristics for
the semantic call). Context is 2-page seam windows with 1-page overlap so each
SEAM is adjudicated exactly once.

LLM hygiene (mirrors ``llm_backends/llm_adjudicator`` + ``tools/fi_parse_compare``):
compact LINE-BASED output, NEVER JSON; ``temperature=0``; ``enable_thinking=False``;
the repetition-guard (``repeat_penalty`` / ``presence_penalty`` +
``_repetition_ratio`` threshold, reused from ``fi_parse_compare``) damps + flags
the pathological loop; ``finish_reason='length'`` → ``AdjudicationTruncated`` →
PER-WINDOW deterministic fallback (``compose_pages``), a typed
``method="deterministic_fallback"`` claim, NOT a route switch (Decision 8). The
HTTP POST is the ``_chat`` seam so the whole thing is testable without a server.

Tier (Decision 4): ``MULTI_WITNESS_ADJUDICATED`` only when a deterministic
affordance INDEPENDENTLY fires on the same node (``corroborating_producers`` names
them); otherwise ``SINGLE_WITNESS``. Absence of contradiction is not corroboration.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.defacsimile import (
    DeFacsimileClaim,
    DeFacsimileLedger,
    DeFacsimileOp,
    _deterministic_fallback_ledger,
    _resolve,
)
from lawvm.ingest.metadata import decode_metadata
from lawvm.ingest.page_elements import line_is_bare_page_number
from lawvm.ingest.simulacrum import PageSimulacrum, SpanRef

DEFAULT_BASE_URL = "http://127.0.0.1:8080"

# --- repetition guard (reused verbatim from tools/fi_parse_compare) ---------
_REPEAT_PENALTY = 1.1
_PRESENCE_PENALTY = 0.5
_MAX_TOKENS = 4000
_REPETITION_THRESHOLD = 0.5

# Recurrence count at/above which the recurrence affordance fires (a running
# header/footer recurs on many pages). Margin-band (top|bottom) fires directly.
_RECURRENCE_THRESHOLD = 2

_ADJUDICATOR_ID = "defacsimile_adjudicator"

# --------------------------------------------------------------------------- #
# Conservatism: a DROP is HONORED only when the target is DETERMINISTICALLY    #
# corroborated as chrome. Dropping real content (→ MISSING) is far costlier    #
# than leaving a furniture line (→ EXTRA), so an UN-corroborated DROP is       #
# DOWNGRADED to KEEP (bias hard toward retention). The model still decides —   #
# but its call must AGREE with an independently-produced deterministic signal, #
# per Decision 4 (absence of contradiction is not corroboration).             #
# --------------------------------------------------------------------------- #

# A cross-page running-header recurs on at least this many pages to count as
# deterministic chrome (a genuine running header/footer, not a lone heading).
_CHROME_RECURRENCE_THRESHOLD = 2

# A chrome line is short — a running header ("HE 1/2015 vp") or a page number.
# A real section heading ("2.1 Laki luottolaitosten ...") carries many words and
# is never treated as droppable chrome on length alone.
_CHROME_MAX_WORDS = 6


def _normalize_line(text: str) -> str:
    """Whitespace-collapsed, case-folded normal form for cross-page identity."""
    return " ".join(text.split()).casefold()


def document_recurrence(simulacra: Sequence[PageSimulacrum]) -> Dict[str, int]:
    """Cross-PAGE count of each normalized top-level node text over the stack.

    A running header ("HE 1/2015 vp") repeats verbatim on page after page; a
    real heading ("2 Ehdotetut muutokset") occurs once. Counted per page (a line
    repeated within one page counts once) so recurrence means CROSS-page — the
    deterministic running-header witness the adjudicator corroborates a DROP
    against when Level-1 band/recurrence metadata is absent.
    """
    counts: Dict[str, int] = {}
    for p in simulacra:
        seen: set[str] = set()
        for node in p.nodes:
            norm = _normalize_line(node.text)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            counts[norm] = counts.get(norm, 0) + 1
    return counts


def _is_deterministic_chrome(
    node: SourceDocumentNode, recurrence: Mapping[str, int]
) -> bool:
    """Is this node deterministically corroborated as droppable chrome?

    TRUE when ANY independent deterministic signal fires:
      * a Level-1 margin-band / recurrence affordance (``_affordances``), OR
      * the node's text is a bare page number (``line_is_bare_page_number``), OR
      * the node's normalized text RECURS across pages (a running header) AND is
        short enough to be chrome (never a multi-word section heading).
    A node the model calls DROP that matches NONE of these is NOT chrome — the
    DROP is downgraded to KEEP so real content (a heading, a body line the model
    mis-labeled furniture) is never silently lost.
    """
    if _affordances(node):
        return True
    text = node.text.strip()
    if not text:
        return False
    if line_is_bare_page_number(text):
        return True
    norm = _normalize_line(text)
    if (
        recurrence.get(norm, 0) >= _CHROME_RECURRENCE_THRESHOLD
        and len(norm.split()) <= _CHROME_MAX_WORDS
    ):
        return True
    # A running header with a glued page number ("4HE 1/2015 vp") does not recur
    # verbatim (the digit varies per page); strip a leading/trailing page-number
    # run and re-test the residual running-header against the recurrence map.
    stripped = _strip_pageno_affix(text)
    if stripped != text and stripped:
        norm_s = _normalize_line(stripped)
        if (
            recurrence.get(norm_s, 0) >= _CHROME_RECURRENCE_THRESHOLD
            and len(norm_s.split()) <= _CHROME_MAX_WORDS
        ):
            return True
    return False


def _strip_pageno_affix(text: str) -> str:
    """Strip a leading OR trailing bare page-number run from a running header.

    ``"4HE 1/2015 vp"`` → ``"HE 1/2015 vp"``; ``"HE 1/2015 vp 4"`` → ``"HE 1/2015
    vp"``. Only a short digit run at the very edge is removed — interior numbers
    (a §-reference, a euro amount) are never touched. Deterministic and reversible.
    """
    s = text.strip()
    m_lead = re.match(r"^\d{1,3}\s*", s)
    if m_lead:
        s = s[m_lead.end():]
    m_trail = re.search(r"\s*\d{1,3}$", s)
    if m_trail:
        s = s[: m_trail.start()]
    return s.strip()

_SYSTEM_PROMPT = (
    "You compose the COHERENT whole document from two consecutive PDF page "
    "simulacra that share a page SEAM. Each page's blocks are listed with a stable "
    "id (e.g. p3n0), the block kind, deterministic HINTS (band=top/body/bottom, "
    "recurs=<n> pages, ends_terminal, starts_lower, furniture_hint), and the text. "
    "The hints are AFFORDANCES ONLY — you decide. For each block that needs an "
    "action, emit EXACTLY ONE line, no JSON, no commentary:\n"
    "  DROP <id> — TRUE chrome ONLY: a running header/footer that recurs across "
    "pages (recurs>=2 or the SAME short text appears on both pages), or a bare page "
    "number, sitting in a top/bottom margin band. NEVER DROP a section heading, a "
    "numbered/lettered heading ('2 Ehdotetut muutokset', '2.1 ...'), or ANYTHING "
    "carrying real words — when unsure, do NOT DROP (leaving a furniture line is far "
    "cheaper than deleting real text)\n"
    "  DEDUP <id> <id> — the SECOND is a GENUINE cross-seam near-duplicate of the "
    "FIRST (nearly the same text, adjacent across the seam); drop the second. NEVER "
    "for a lone occurrence, and NEVER for a legitimately-repeated printed table "
    "header (that is KEEP)\n"
    "  REJOIN <id> <id> [absorb=<id>] — the blocks are ONE unit split by the seam "
    "(a mid-sentence paragraph, a table continuing); join in order. NEVER REJOIN a "
    "heading with a paragraph — a heading is its own line, joining destroys the "
    "section structure. absorb=<id> consumes a repeated table header row that must "
    "not re-appear\n"
    "  KEEP <id> — a legitimately-repeated block (a printed table's per-page header, "
    "boilerplate) that is NOT a duplicate; also use KEEP whenever you are UNSURE "
    "whether a block is furniture — bias toward keeping it\n"
    "  REORDER <id> <id> ... — the correct cross-page reading order of these ids\n"
    "Emit lines ONLY for blocks needing an action; unmentioned blocks stay as-is. "
    "Do NOT repeat a line. Output nothing else. When in doubt, KEEP."
)


def _repetition_ratio(text: str) -> float:
    """Fraction of non-blank lines that duplicate an earlier line (loop detector)."""
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


class AdjudicationTruncated(Exception):
    """The model hit ``max_tokens`` mid-answer (``finish_reason='length'``).

    Raised, not swallowed: the caller falls back to ``compose_pages`` for THIS
    window (a typed ``deterministic_fallback`` claim), never returns a cut-off
    reading as if complete (the LLM guide's Class-2 error rule).
    """

    def __init__(self, *, window: str, detail: str) -> None:
        super().__init__(detail)
        self.window = window
        self.detail = detail


class AdjudicationTransportFailure(Exception):
    """A connection / HTTP / malformed-response failure (typed, never silent)."""

    def __init__(self, *, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


# --------------------------------------------------------------------------- #
# Stable per-window node ids  ↔  SpanRef                                       #
# --------------------------------------------------------------------------- #


def _node_id(page_num: int, top_idx: int) -> str:
    """Stable per-window block id (``p<page>n<top-index>``) → maps back to a SpanRef."""
    return f"p{page_num}n{top_idx}"


def _parse_node_id(token: str) -> Optional[Tuple[int, int]]:
    if not token.startswith("p") or "n" not in token:
        return None
    body = token[1:]
    page_s, _, idx_s = body.partition("n")
    if page_s.isdigit() and idx_s.isdigit():
        return int(page_s), int(idx_s)
    return None


def _affordances(node: SourceDocumentNode) -> Tuple[str, ...]:
    """The deterministic affordances that INDEPENDENTLY fire on a node (Decision 4).

    ``affordance:margin_band`` — the node sits in the top/bottom margin band.
    ``affordance:recurrence`` — it recurs at the same band on ≥ threshold pages.
    Named producers so a claim over this node can be corroborated (→ MULTI_WITNESS).
    """
    meta = decode_metadata(node.attrs)
    fired: List[str] = []
    if meta.band in ("top", "bottom"):
        fired.append("affordance:margin_band")
    if meta.band_count is not None and meta.band_count >= _RECURRENCE_THRESHOLD:
        fired.append("affordance:recurrence")
    return tuple(fired)


def _render_block(page_num: int, top_idx: int, node: SourceDocumentNode) -> str:
    meta = decode_metadata(node.attrs)
    hints: List[str] = [f"kind={node.kind}"]
    if meta.band is not None:
        hints.append(f"band={meta.band}")
    if meta.band_count is not None:
        hints.append(f"recurs={meta.band_count}")
    if meta.ends_terminal:
        hints.append("ends_terminal")
    if meta.starts_lower:
        hints.append("starts_lower")
    if meta.furniture:
        hints.append("furniture_hint")
    text = node.text.strip()
    if not text and node.kind is SourceDocumentNodeKind.TABLE:
        text = " | ".join(
            c.text.strip()
            for r in node.children
            for c in r.children
            if c.text.strip()
        )
    return f"{_node_id(page_num, top_idx)} [{'; '.join(hints)}]: {text}"


def _render_window(pages: Sequence[PageSimulacrum]) -> str:
    parts: List[str] = []
    for p in pages:
        parts.append(f"--- page {p.page_num} ---")
        for top_idx, node in enumerate(p.nodes):
            parts.append(_render_block(p.page_num, top_idx, node))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Line-reply → claims (per window)                                            #
# --------------------------------------------------------------------------- #


def _tier_for(
    refs: Sequence[SpanRef], sim_by_page: Dict[int, PageSimulacrum]
) -> Tuple[AssuranceTier, Tuple[str, ...]]:
    """Tier + corroborating producers for a claim over ``refs`` (Decision 4).

    ``MULTI_WITNESS_ADJUDICATED`` iff a deterministic affordance INDEPENDENTLY
    fires on ANY targeted node (the model + affordance agree); else
    ``SINGLE_WITNESS``. Producers always include the adjudicator itself.
    """
    producers: List[str] = [_ADJUDICATOR_ID]
    fired = False
    for ref in refs:
        page = sim_by_page.get(ref.page_num)
        if page is None or not ref.node_path or ref.node_path[0] >= len(page.nodes):
            continue
        node = page.nodes[ref.node_path[0]]
        for a in _affordances(node):
            fired = True
            if a not in producers:
                producers.append(a)
    tier = (
        AssuranceTier.MULTI_WITNESS_ADJUDICATED if fired else AssuranceTier.SINGLE_WITNESS
    )
    return tier, tuple(producers)


def _resolve_local(
    ref: SpanRef, sim_by_page: Dict[int, PageSimulacrum]
) -> Optional[SourceDocumentNode]:
    """Resolve a top-level ``SpanRef`` to its window node (None if out of range)."""
    page = sim_by_page.get(ref.page_num)
    if page is None or not ref.node_path or ref.node_path[0] >= len(page.nodes):
        return None
    return page.nodes[ref.node_path[0]]


def _rejoin_is_structurally_safe(
    refs: Sequence[SpanRef],
    sim_by_page: Dict[int, PageSimulacrum],
    recurrence: Mapping[str, int],
) -> bool:
    """Is a REJOIN structurally safe — a genuine seam-split, no HEADING/chrome folded in?

    A REJOIN stitches parts split across a seam (a mid-sentence paragraph, a table
    continuing). Three deterministic conservatism guards (bias to KEEP):

    1. **No HEADING** — a heading is a structural boundary; folding it into a
       paragraph DESTROYS the section nesting (a STRUCTURE regression) and loses the
       heading's own line. Headings are never continuation fragments.
    2. **No CHROME part** — a running header / page number spliced BETWEEN two body
       fragments must never be absorbed into the joined text ("poikkeuksien käytHE
       1/2015 vptöä"); a REJOIN whose parts include deterministic chrome is refused.
    3. **Genuine continuation** — consecutive parts must be a real split per the
       deterministic ``DefaultContinuationJudge`` (a paragraph that ends WITHOUT
       terminal punctuation and continues lower-case; a same-width table not
       re-opening a header). Merging two ALREADY-COMPLETE paragraphs is not a
       seam-split — it just flattens structure into a "continuous block".

    Unresolved parts fail safe (not joined).
    """
    from lawvm.core.source_document.composition import DefaultContinuationJudge

    nodes: List[SourceDocumentNode] = []
    for r in refs:
        node = _resolve_local(r, sim_by_page)
        if node is None:
            return False  # unresolved part → fail safe, do not join
        if node.kind is SourceDocumentNodeKind.HEADING:
            return False
        if _is_deterministic_chrome(node, recurrence):
            return False  # a running header / page number is never a join fragment
        nodes.append(node)

    judge = DefaultContinuationJudge()
    for prev, nxt in zip(nodes, nodes[1:], strict=False):
        if (
            prev.kind is SourceDocumentNodeKind.TABLE
            and nxt.kind is SourceDocumentNodeKind.TABLE
        ):
            if not judge.continues_table(prev, nxt):
                return False
        elif (
            prev.kind is SourceDocumentNodeKind.PARAGRAPH
            and nxt.kind is SourceDocumentNodeKind.PARAGRAPH
        ):
            if not judge.continues_paragraph(prev, nxt):
                return False
        else:
            # Mixed / non-continuation kinds are not a seam-split fragment pair.
            return False
    return True


def _near_duplicate(a: str, b: str) -> bool:
    """Are two normalized lines near-duplicates (a genuine cross-seam repeat)?

    Exact match after normalization, or one is a prefix/suffix of the other (an
    OCR-truncated running header). Distinct content never collapses.
    """
    na, nb = _normalize_line(a), _normalize_line(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 4 and (longer.startswith(shorter) or longer.endswith(shorter))


def parse_window_reply(
    content: str,
    pages: Sequence[PageSimulacrum],
    recurrence: Optional[Mapping[str, int]] = None,
) -> Tuple[List[DeFacsimileClaim], bool]:
    """Parse a window's LINE reply into claims. Returns (claims, pathological).

    Ids resolve back to ``SpanRef``s over the window's pages; an id the model
    invents that is not in this window is IGNORED (it cannot conjure a node — the
    same discipline as the reconcile adjudicator's producer check). A pathological
    repetition loop (ratio ≥ threshold) WITHHOLDS all claims (returns ``([], True)``)
    rather than presenting garbage as an edit.

    CONSERVATISM (bias to KEEP): a ``DROP`` is honored only when the target is
    deterministically corroborated as chrome (``_is_deterministic_chrome`` —
    margin-band/recurrence affordance, bare page number, or a short cross-page
    running header per ``recurrence``); otherwise the DROP is DOWNGRADED to a KEEP
    so real content is never silently lost. A ``DEDUP`` is honored only when its
    two targets are genuine near-duplicates (``_near_duplicate``); a spurious DEDUP
    (distinct content) becomes a KEEP of the second. Dropping real content (MISSING)
    is far costlier than leaving a furniture line (EXTRA).
    """
    if _repetition_ratio(content) >= _REPETITION_THRESHOLD:
        return [], True

    rec: Mapping[str, int] = recurrence if recurrence is not None else document_recurrence(pages)
    sim_by_page = {p.page_num: p for p in pages}
    valid_keys = {
        (p.page_num, i) for p in pages for i in range(len(p.nodes))
    }

    def _ref(token: str) -> Optional[SpanRef]:
        key = _parse_node_id(token)
        if key is None or key not in valid_keys:
            return None
        return SpanRef(key[0], (key[1],))

    claims: List[DeFacsimileClaim] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        toks = line.split()
        op = toks[0].upper()
        absorb: List[SpanRef] = []
        body_toks: List[str] = []
        for t in toks[1:]:
            if t.startswith("absorb="):
                a = _ref(t[len("absorb="):])
                if a is not None:
                    absorb.append(a)
            else:
                body_toks.append(t)
        refs = [r for r in (_ref(t) for t in body_toks) if r is not None]
        if op == "DROP" and refs:
            # Honor a DROP only where the target is deterministically corroborated
            # as chrome; an un-corroborated DROP is DOWNGRADED to KEEP (bias to
            # retention — a false DROP loses real content, a missed furniture line
            # merely survives). Split the refs into the honored / downgraded sets.
            drop_refs = [
                r
                for r in refs
                if (n := _resolve_local(r, sim_by_page)) is not None
                and _is_deterministic_chrome(n, rec)
            ]
            kept_refs = [r for r in refs if r not in drop_refs]
            if drop_refs:
                tier, producers = _tier_for(drop_refs, sim_by_page)
                claims.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.DROP_FURNITURE,
                        targets=tuple(drop_refs),
                        tier=tier,
                        corroborating_producers=producers,
                        rationale="model DROP furniture (corroborated chrome)",
                    )
                )
            for r in kept_refs:
                tier, producers = _tier_for([r], sim_by_page)
                claims.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.KEEP,
                        targets=(r,),
                        tier=tier,
                        corroborating_producers=producers,
                        rationale="model DROP downgraded to KEEP (uncorroborated furniture)",
                    )
                )
        elif op == "DEDUP" and len(refs) >= 2:
            # Keep the FIRST, drop the SECOND (the cross-seam duplicate) ONLY when
            # the two are genuine near-duplicates; otherwise KEEP the second (a
            # spurious DEDUP must never delete distinct content).
            first = _resolve_local(refs[0], sim_by_page)
            second = _resolve_local(refs[1], sim_by_page)
            genuine = (
                first is not None
                and second is not None
                and _near_duplicate(first.text, second.text)
            )
            if genuine:
                tier, producers = _tier_for(refs[1:], sim_by_page)
                claims.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.DEDUP_SEAM,
                        targets=tuple(refs[1:]),
                        tier=tier,
                        corroborating_producers=producers,
                        rationale=f"model DEDUP {body_toks[0]} kept (near-duplicate)",
                    )
                )
            else:
                tier, producers = _tier_for(refs[1:2], sim_by_page)
                claims.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.KEEP,
                        targets=(refs[1],),
                        tier=tier,
                        corroborating_producers=producers,
                        rationale="model DEDUP downgraded to KEEP (not a near-duplicate)",
                    )
                )
        elif op == "REJOIN" and len(refs) >= 2:
            if _rejoin_is_structurally_safe(refs, sim_by_page, rec):
                tier, producers = _tier_for(refs, sim_by_page)
                claims.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.REJOIN,
                        targets=tuple(refs),
                        tier=tier,
                        corroborating_producers=producers,
                        absorbed=tuple(absorb),
                        rationale="model REJOIN across seam",
                    )
                )
            else:
                # A REJOIN that would fold a HEADING into other content is refused;
                # keep the parts as separate nodes (bias to retention — the section
                # boundary and the heading's own line survive).
                for r in refs:
                    tier, producers = _tier_for([r], sim_by_page)
                    claims.append(
                        DeFacsimileClaim(
                            op=DeFacsimileOp.KEEP,
                            targets=(r,),
                            tier=tier,
                            corroborating_producers=producers,
                            rationale="model REJOIN refused (would fold a heading); kept",
                        )
                    )
        elif op == "KEEP" and refs:
            tier, producers = _tier_for(refs, sim_by_page)
            claims.append(
                DeFacsimileClaim(
                    op=DeFacsimileOp.KEEP,
                    targets=tuple(refs),
                    tier=tier,
                    corroborating_producers=producers,
                    rationale="model KEEP legitimate repeat",
                )
            )
        elif op == "REORDER" and len(refs) >= 2:
            tier, producers = _tier_for(refs, sim_by_page)
            claims.append(
                DeFacsimileClaim(
                    op=DeFacsimileOp.REORDER,
                    targets=tuple(refs),
                    tier=tier,
                    corroborating_producers=producers,
                    rationale="model REORDER reading order",
                )
            )
    return claims, False


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One seam window's adjudication outcome (claims + provenance flags)."""

    claims: Tuple[DeFacsimileClaim, ...]
    pathological: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class RegionResult:
    """One blackboard region's adjudication: claims + affordance control lines (M3).

    ``affordances`` are the parsed ``blackboard.AffordanceRequest`` control lines
    the model emitted (VIEW / EXPAND / NOTE / …) — typed ``object`` here to avoid a
    circular import with ``blackboard`` (which imports this module's adjudicator).
    """

    claims: Tuple[DeFacsimileClaim, ...]
    affordances: Tuple[object, ...]
    truncated: bool


class DeFacsimileAdjudicator:
    """The de-facsimile adjudicator over 2-page seam windows (1-page overlap).

    ``adjudicate_document`` produces ONE ``DeFacsimileLedger`` for the whole
    simulacra stack; ``defacsimile()`` folds + verifies it. Each seam is
    adjudicated exactly once (windows [p0,p1], [p1,p2], …); a single page becomes a
    lone window. A truncated or pathological window falls back to ``compose_pages``
    for that window (typed ``deterministic_fallback`` claims, Decision 8).
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = _MAX_TOKENS,
        timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._last_finish_reason: Optional[str] = None

    @property
    def adjudicator_id(self) -> str:
        return f"{_ADJUDICATOR_ID}:{self._model or 'qwen'}"

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

    def _payload(self, system: str, user: str) -> Dict[str, object]:
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
            # --- repetition guard (reused from fi_parse_compare) ---
            "repeat_penalty": _REPEAT_PENALTY,
            "presence_penalty": _PRESENCE_PENALTY,
        }

    # -- transport seam (overridable / mockable in tests) -------------------

    def _chat(self, system: str, user: str, *, window: str) -> str:
        """POST one chat turn; return content. Raise on truncation / transport error."""
        data = json.dumps(self._payload(system, user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                out = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise AdjudicationTransportFailure(
                reason_code="adjudicator_http_error",
                detail=f"HTTP {exc.code}",
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise AdjudicationTransportFailure(
                reason_code="adjudicator_unreachable",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        try:
            choice = out["choices"][0]
            content = str(choice["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AdjudicationTransportFailure(
                reason_code="adjudicator_malformed_response",
                detail=f"no choices/message/content: {exc}",
            ) from exc
        self._last_finish_reason = choice.get("finish_reason")
        if choice.get("finish_reason") == "length":
            raise AdjudicationTruncated(
                window=window, detail="finish_reason=length; window reply truncated"
            )
        return content

    # -- per-window adjudication --------------------------------------------

    def adjudicate_window(
        self,
        pages: Sequence[PageSimulacrum],
        recurrence: Optional[Mapping[str, int]] = None,
    ) -> WindowResult:
        """Adjudicate ONE seam window → claims (fallback on truncation / loop).

        ``recurrence`` is the DOCUMENT-wide cross-page text-recurrence map (the
        running-header witness); when ``None`` it is computed from the window's own
        pages (a conservative under-estimate — a full document supplies it).
        """
        window = "+".join(str(p.page_num) for p in pages)
        user = (
            "Compose these consecutive pages, resolving the seam between them:\n\n"
            + _render_window(pages)
        )
        try:
            content = self._chat(_SYSTEM_PROMPT, user, window=window)
        except AdjudicationTruncated:
            # Decision 8: per-window deterministic fallback, a typed claim — NOT a
            # route switch. compose_pages runs UNCHANGED over this window's pages.
            fallback = _deterministic_fallback_ledger(pages)
            return WindowResult(claims=fallback.claims, pathological=False, truncated=True)
        claims, pathological = parse_window_reply(content, pages, recurrence)
        if pathological:
            # The loop garbage is withheld; no edits from this window (the pure fold
            # keeps the pages as-is). Recorded so the caller can report it.
            return WindowResult(claims=(), pathological=True, truncated=False)
        return WindowResult(claims=tuple(claims), pathological=False, truncated=False)

    # -- blackboard-aware region adjudication (M3) --------------------------

    def adjudicate_region(
        self,
        window: Sequence[PageSimulacrum],
        region: Sequence[SpanRef],
        region_marks: Sequence[object],
        region_notes: Sequence[str],
    ) -> "RegionResult":
        """Adjudicate ONE blackboard region → claims + affordance control lines.

        The blackboard entry point: it READS the marks + notes the workspace holds
        for this region (surfaced into the prompt as shared context), adjudicates
        the 2-page window that contains the region, and returns claims PLUS the
        affordance CONTROL LINES the model emitted (VIEW / EXPAND / NOTE / …) — all
        line-based, never JSON. It keeps the SAME conservative discipline as
        ``adjudicate_window`` (``parse_window_reply``: a DROP honored only against a
        deterministic chrome witness, a REJOIN only when structurally safe) — the
        blackboard adds context, it does NOT relax the honor-with-evidence gate.

        A truncated / pathological window degrades to the deterministic
        ``compose_pages`` claims for the window (typed, no silent drop).
        """
        from lawvm.ingest.blackboard import parse_affordance_line

        recurrence = document_recurrence(window)
        win_id = "+".join(str(p.page_num) for p in window)
        context_lines: List[str] = []
        if region_notes:
            context_lines.append("PRIOR NOTES on this region:")
            context_lines.extend(f"  {n}" for n in region_notes)
        if region_marks:
            context_lines.append("PRIOR MARKS on this region (affordances only):")
            for m in region_marks:
                kind = getattr(m, "kind", None)
                rationale = getattr(m, "rationale", "")
                if kind is not None:
                    context_lines.append(f"  {kind}: {rationale}")
        region_ids = " ".join(
            _node_id(r.page_num, r.node_path[0]) for r in region if r.node_path
        )
        user = (
            "Compose these consecutive pages, resolving the seam. FOCUS on the "
            f"region: {region_ids}.\n"
            + ("\n".join(context_lines) + "\n\n" if context_lines else "\n")
            + _render_window(window)
        )
        try:
            content = self._chat(_SYSTEM_PROMPT, user, window=win_id)
        except AdjudicationTruncated:
            fallback = _deterministic_fallback_ledger(window)
            return RegionResult(claims=fallback.claims, affordances=(), truncated=True)

        # Split the reply: affordance CONTROL lines (VIEW/EXPAND/NOTE/…) are lifted
        # out; the residual op lines go to the conservative claim parser. A line is
        # a control line iff its verb is a known affordance verb.
        control: List[object] = []
        op_lines: List[str] = []
        for raw in content.splitlines():
            req = parse_affordance_line(raw)
            if req is not None:
                control.append(req)
            else:
                op_lines.append(raw)
        claims, pathological = parse_window_reply(
            "\n".join(op_lines), window, recurrence
        )
        if pathological:
            return RegionResult(claims=(), affordances=tuple(control), truncated=False)
        return RegionResult(
            claims=tuple(claims), affordances=tuple(control), truncated=False
        )

    def adjudicate_document(
        self, simulacra: Sequence[PageSimulacrum]
    ) -> DeFacsimileLedger:
        """Adjudicate every seam once → one merged, disjoint ledger for the stack.

        Windows are [p0,p1], [p1,p2], … (1-page overlap); a single page is a lone
        window. Claims are merged across windows; if two windows both target the
        SAME node (the overlap), the FIRST claim wins (a node is owned once —
        ``verify_ledger`` enforces this downstream, and the merge keeps it disjoint).
        """
        pages = list(simulacra)
        # The document-wide running-header witness: a line recurring across pages
        # is chrome; a heading occurring once is content. Computed ONCE over the
        # whole stack so a DROP in any window is corroborated against the full
        # document, not just its 2-page window.
        recurrence = document_recurrence(pages)
        windows: List[List[PageSimulacrum]]
        if len(pages) <= 1:
            windows = [pages] if pages else []
        else:
            windows = [pages[i : i + 2] for i in range(len(pages) - 1)]

        merged: List[DeFacsimileClaim] = []
        owned: set[Tuple[int, Tuple[int, ...]]] = set()
        for win in windows:
            result = self.adjudicate_window(win, recurrence)
            for claim in result.claims:
                keys = [(r.page_num, r.node_path) for r in (*claim.targets, *claim.absorbed)]
                if any(k in owned for k in keys):
                    continue  # a prior window already owns one of these nodes
                merged.append(claim)
                for k in keys:
                    owned.add(k)
        merged = self._propagate_confirmed_chrome_drops(pages, merged, recurrence)
        return DeFacsimileLedger(claims=tuple(merged))

    def _propagate_confirmed_chrome_drops(
        self,
        pages: Sequence[PageSimulacrum],
        claims: List[DeFacsimileClaim],
        recurrence: Mapping[str, int],
    ) -> List[DeFacsimileClaim]:
        """Extend the model's OWN chrome drops to every recurrence of that line.

        A running header the model DROPs on some page (e.g. "HE 1/2015 vp" on p2/p3)
        recurs verbatim across the document. Its OTHER occurrences — whether the
        model left them KEPT or simply UN-mentioned — are the IDENTICAL furniture;
        dropping them is corroborated by BOTH the model (its own DROP on a sibling
        page) and the recurrence witness, so it can never lose real content. This
        drops every remaining occurrence of a model-confirmed recurring header,
        closing the residual EXTRA the model's uneven per-page coverage leaves.
        Bare page numbers are NEVER cross-propagated ("2" and "3" are distinct
        lines) — only a recurring multi-token running header.
        """
        dropped_norms: set[str] = set()
        for c in claims:
            if c.op is not DeFacsimileOp.DROP_FURNITURE:
                continue
            node = _resolve(pages, c.targets[0]) if c.targets else None
            if node is None:
                continue
            norm = _normalize_line(node.text)
            if (
                norm
                and len(norm.split()) >= 2
                and recurrence.get(norm, 0) >= _CHROME_RECURRENCE_THRESHOLD
            ):
                dropped_norms.add(norm)
        if not dropped_norms:
            return claims

        def _matches_confirmed_header(node: SourceDocumentNode) -> bool:
            text = node.text.strip()
            if not text:
                return False
            if _normalize_line(text) in dropped_norms:
                return True
            stripped = _strip_pageno_affix(text)
            return bool(stripped) and _normalize_line(stripped) in dropped_norms

        # Every node already owned by a claim (its (page, path) key).
        owned: set[Tuple[int, Tuple[int, ...]]] = set()
        for c in claims:
            for r in (*c.targets, *c.absorbed):
                owned.add((r.page_num, r.node_path))

        out: List[DeFacsimileClaim] = []
        for c in claims:
            # Upgrade a KEEP of a confirmed recurring header to a DROP.
            if (
                c.op is DeFacsimileOp.KEEP
                and len(c.targets) == 1
                and (node := _resolve(pages, c.targets[0])) is not None
                and _matches_confirmed_header(node)
            ):
                out.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.DROP_FURNITURE,
                        targets=c.targets,
                        tier=c.tier,
                        corroborating_producers=c.corroborating_producers,
                        rationale="chrome DROP propagated from model DROP of same recurring line",
                    )
                )
                continue
            out.append(c)

        # Drop UN-mentioned occurrences (no claim owns them) of the confirmed header.
        for p in pages:
            for top_idx, node in enumerate(p.nodes):
                key = (p.page_num, (top_idx,))
                if key in owned or not _matches_confirmed_header(node):
                    continue
                out.append(
                    DeFacsimileClaim(
                        op=DeFacsimileOp.DROP_FURNITURE,
                        targets=(SpanRef(p.page_num, (top_idx,)),),
                        tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
                        corroborating_producers=(_ADJUDICATOR_ID, "affordance:recurrence"),
                        rationale="chrome DROP propagated to un-mentioned recurring header",
                    )
                )
        return out
