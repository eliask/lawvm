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
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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
)
from lawvm.ingest.metadata import decode_metadata
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

_SYSTEM_PROMPT = (
    "You compose the COHERENT whole document from two consecutive PDF page "
    "simulacra that share a page SEAM. Each page's blocks are listed with a stable "
    "id (e.g. p3n0), the block kind, deterministic HINTS (band=top/body/bottom, "
    "recurs=<n> pages, ends_terminal, starts_lower, furniture_hint), and the text. "
    "The hints are AFFORDANCES ONLY — you decide. For each block that needs an "
    "action, emit EXACTLY ONE line, no JSON, no commentary:\n"
    "  DROP <id> — running header / page number / footer furniture, remove it\n"
    "  DEDUP <id> <id> — the SECOND is a genuine cross-seam duplicate of the FIRST; "
    "drop the second (NEVER for a legitimately-repeated printed table header — that "
    "is KEEP)\n"
    "  REJOIN <id> <id> [absorb=<id>] — the blocks are ONE unit split by the seam "
    "(a mid-sentence paragraph, a table continuing); join in order. absorb=<id> "
    "consumes a repeated table header row that must not re-appear\n"
    "  KEEP <id> — a legitimately-repeated block (a printed table's per-page header, "
    "boilerplate) that is NOT a duplicate\n"
    "  REORDER <id> <id> ... — the correct cross-page reading order of these ids\n"
    "Emit lines ONLY for blocks needing an action; unmentioned blocks stay as-is. "
    "Do NOT repeat a line. Output nothing else."
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


def parse_window_reply(
    content: str, pages: Sequence[PageSimulacrum]
) -> Tuple[List[DeFacsimileClaim], bool]:
    """Parse a window's LINE reply into claims. Returns (claims, pathological).

    Ids resolve back to ``SpanRef``s over the window's pages; an id the model
    invents that is not in this window is IGNORED (it cannot conjure a node — the
    same discipline as the reconcile adjudicator's producer check). A pathological
    repetition loop (ratio ≥ threshold) WITHHOLDS all claims (returns ``([], True)``)
    rather than presenting garbage as an edit.
    """
    if _repetition_ratio(content) >= _REPETITION_THRESHOLD:
        return [], True

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
            tier, producers = _tier_for(refs, sim_by_page)
            claims.append(
                DeFacsimileClaim(
                    op=DeFacsimileOp.DROP_FURNITURE,
                    targets=tuple(refs),
                    tier=tier,
                    corroborating_producers=producers,
                    rationale="model DROP furniture",
                )
            )
        elif op == "DEDUP" and len(refs) >= 2:
            # Keep the FIRST, drop the SECOND (the cross-seam duplicate).
            tier, producers = _tier_for(refs[1:], sim_by_page)
            claims.append(
                DeFacsimileClaim(
                    op=DeFacsimileOp.DEDUP_SEAM,
                    targets=tuple(refs[1:]),
                    tier=tier,
                    corroborating_producers=producers,
                    rationale=f"model DEDUP {body_toks[0]} kept",
                )
            )
        elif op == "REJOIN" and len(refs) >= 2:
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

    def adjudicate_window(self, pages: Sequence[PageSimulacrum]) -> WindowResult:
        """Adjudicate ONE seam window → claims (fallback on truncation / loop)."""
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
        claims, pathological = parse_window_reply(content, pages)
        if pathological:
            # The loop garbage is withheld; no edits from this window (the pure fold
            # keeps the pages as-is). Recorded so the caller can report it.
            return WindowResult(claims=(), pathological=True, truncated=False)
        return WindowResult(claims=tuple(claims), pathological=False, truncated=False)

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
        windows: List[List[PageSimulacrum]]
        if len(pages) <= 1:
            windows = [pages] if pages else []
        else:
            windows = [pages[i : i + 2] for i in range(len(pages) - 1)]

        merged: List[DeFacsimileClaim] = []
        owned: set[Tuple[int, Tuple[int, ...]]] = set()
        for win in windows:
            result = self.adjudicate_window(win)
            for claim in result.claims:
                keys = [(r.page_num, r.node_path) for r in (*claim.targets, *claim.absorbed)]
                if any(k in owned for k in keys):
                    continue  # a prior window already owns one of these nodes
                merged.append(claim)
                for k in keys:
                    owned.add(k)
        return DeFacsimileLedger(claims=tuple(merged))
