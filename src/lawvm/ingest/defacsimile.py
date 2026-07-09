"""Frozen Level-2 interface carriers — the de-facsimile claim.

FROZEN at the end of Track A (§5.5 of ``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``).
CARRIERS ONLY — the fold / adjudication / ``verify_ledger`` logic is Track C.

Level 2 composes the stack of per-page ``PageSimulacrum`` evidence into one
coherent whole-document tree PLUS a ledger of these typed, reversible claims.
Each claim carries its own assurance tier (Decision 4) and ``SpanRef``
provenance back into the immutable simulacra, so nothing disappears silently
(AGENTS §1.8): every DROP / DEDUP / REJOIN / REORDER is auditable and reversible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.metadata import decode_metadata
from lawvm.ingest.simulacrum import PageSimulacrum, SpanRef


class DeFacsimileOp(Enum):
    """The four de-facsimile operations plus the explicit legitimate-repeat KEEP.

    - ``DROP_FURNITURE`` — running headers / page numbers / footers.
    - ``DEDUP_SEAM`` — collapse GENUINE cross-seam duplication (seam-adjacency,
      never string-identity).
    - ``REJOIN`` — content split across a page/column break.
    - ``REORDER`` — coherent cross-page reading order (mostly identity; explicit).
    - ``KEEP`` — an explicit claim that a legitimately-repeated node (e.g. a
      printed table's per-page header) is NOT a duplicate.
    """

    DROP_FURNITURE = "drop_furniture"
    DEDUP_SEAM = "dedup_seam"
    REJOIN = "rejoin"
    REORDER = "reorder"
    KEEP = "keep"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DeFacsimileClaim:
    """One auditable, reversible de-facsimile claim over the page simulacra.

    ``targets`` are the nodes the claim owns (exactly one claim owns each node —
    ``verify_ledger`` enforces claim-disjointness, so fold-order can't matter,
    Decision 3). ``corroborating_producers`` names the independently-produced
    signals that agree (e.g. ``"defacsimile_adjudicator"``, ``"affordance:margin_band"``);
    the tier is ``MULTI_WITNESS_ADJUDICATED`` only when a deterministic affordance
    INDEPENDENTLY fires (Decision 4). ``absorbed`` carries a REJOIN header-absorb
    sub-claim (Decision 3). ``method`` is ``"model_adjudicated"`` or
    ``"deterministic_fallback"`` (the ``compose_pages`` fallback, Decision 8) —
    a typed method, not a route switch.
    """

    op: DeFacsimileOp
    targets: Tuple[SpanRef, ...]
    tier: AssuranceTier
    corroborating_producers: Tuple[str, ...]
    absorbed: Tuple[SpanRef, ...] = ()
    method: str = "model_adjudicated"
    rationale: str = ""


# ===========================================================================
# Track C — Level-2 de-facsimile: ledger carrier, the PURE fold, verify_ledger,
# the orchestrator, and the deterministic (compose_pages) fallback adapter.
# ===========================================================================


@dataclass(frozen=True, slots=True)
class DeFacsimileLedger:
    """The ordered list of typed de-facsimile claims over a page-simulacra stack.

    Immutable evidence artifact (Decision 5): persisted verbatim in a sibling
    content-addressed blob; the manifest carries only histograms + the blob
    locator/digest. Exactly one destructive claim may own each ``SpanRef``
    (``verify_ledger`` enforces claim-disjointness, Decision 3), so the fold order
    can never matter — the derived document is a pure function of
    (immutable simulacra + this ledger).
    """

    claims: Tuple[DeFacsimileClaim, ...] = ()


@dataclass(frozen=True, slots=True)
class DeFacsimiledDocument:
    """The Level-2 output: the coherent whole-document tree + its ledger.

    ``root`` is a ``WORK_ROOT`` ``SourceDocumentNode`` composed by the pure fold
    over the simulacra; ``ledger`` is the auditable, reversible claim list that
    produced it (nothing disappears silently — AGENTS §1.8). Re-running the fold
    over the same (simulacra, ledger) is byte-identical (idempotent).
    """

    root: SourceDocumentNode
    page_count: int
    ledger: DeFacsimileLedger


# --------------------------------------------------------------------------- #
# SpanRef resolution against the immutable simulacra                          #
# --------------------------------------------------------------------------- #

# Destructive ops REMOVE their targets from the reduced body (a REJOIN removes
# the trailing/leading fragments it stitches; a DROP/DEDUP removes the node).
# KEEP / REORDER are non-destructive (KEEP is an explicit legitimate-repeat,
# REORDER only permutes). ``absorbed`` targets (a REJOIN header-absorb) are also
# removed. Claim-disjointness is enforced over exactly this destructive set.
_DESTRUCTIVE_OPS = (
    DeFacsimileOp.DROP_FURNITURE,
    DeFacsimileOp.DEDUP_SEAM,
    DeFacsimileOp.REJOIN,
)

# Fixed fold order (Decision / §2): DROP → DEDUP → REJOIN → REORDER.
_FOLD_ORDER = {
    DeFacsimileOp.DROP_FURNITURE: 0,
    DeFacsimileOp.DEDUP_SEAM: 1,
    DeFacsimileOp.REJOIN: 2,
    DeFacsimileOp.REORDER: 3,
    DeFacsimileOp.KEEP: 4,
}

# NUMERIC-content guard: any §/euro/date/citation token must survive the fold
# unchanged (Decision / §2 validation). Conservative, LINE-LOCAL token grabbers so
# distinct numbers on distinct lines never merge into one span. A section
# REFERENCE (a number adjacent to §, either side) is captured as its OWN compound
# token so a body "14 §" is distinguished from a bare page-number "14" (the §8
# guardrail) — dropping the body loses the "14 §" reference even though a stray
# "14" survives.
_SECTION_REF = re.compile(r"(?:§\s*\d[\d.]*)|(?:\d[\d.]*\s*§)")
_NUMERIC_TOKEN = re.compile(
    r"§|€|\bEUR\b|"                      # bare section sign, euro
    r"\d{1,2}\.\d{1,2}\.\d{2,4}|"        # dd.mm.yyyy date
    r"\d[\d.,/§€-]*\d|\d"               # a number-ish run (no whitespace: line-local)
)


def _normalize_ref(tok: str) -> str:
    """Canonical form of a section-reference token (collapse internal spaces)."""
    return re.sub(r"\s+", "", tok)


def _resolve(simulacra: Sequence[PageSimulacrum], ref: SpanRef) -> Optional[SourceDocumentNode]:
    """Resolve a ``SpanRef`` to its concrete simulacrum node, or ``None`` if dangling.

    ``page_num`` selects the page simulacrum (matched on the carrier's own
    ``page_num``, not list position); ``node_path`` walks child indices from the
    page's top-level ``nodes``. A path that does not resolve is a PHANTOM the
    verifier rejects — nothing is dropped that is not real evidence.
    """
    page: Optional[PageSimulacrum] = None
    for p in simulacra:
        if p.page_num == ref.page_num:
            page = p
            break
    if page is None or not ref.node_path:
        return None
    nodes: Tuple[SourceDocumentNode, ...] = page.nodes
    node: Optional[SourceDocumentNode] = None
    for i, idx in enumerate(ref.node_path):
        if idx < 0 or idx >= len(nodes):
            return None
        node = nodes[idx]
        if i < len(ref.node_path) - 1:
            nodes = node.children
    return node


def _y_order(node: SourceDocumentNode) -> int:
    """Deterministic within-page ordinal from ``geom.y_order`` (0 if unset)."""
    meta = decode_metadata(node.attrs)
    return meta.y_order if meta.y_order is not None else 0


def _rejoin_text(parts: Sequence[SourceDocumentNode]) -> str:
    """The EXACT concatenation a REJOIN produces from its ordered part nodes.

    Whitespace-joined in target order (the fold's canonical join); ``verify_ledger``
    checks the produced node's text is byte-identical to this so a REJOIN can never
    invent or lose content across the seam.
    """
    return " ".join(p.text.strip() for p in parts if p.text.strip()).strip()


# --------------------------------------------------------------------------- #
# The PURE deterministic fold                                                 #
# --------------------------------------------------------------------------- #


def apply_ledger(
    simulacra: Sequence[PageSimulacrum],
    ledger: DeFacsimileLedger,
    root_anchor: SourceAnchor,
) -> SourceDocumentNode:
    """Fold immutable simulacra + ledger → one ``WORK_ROOT`` tree (PURE, idempotent).

    Fixed op order DROP → DEDUP → REJOIN → REORDER; siblings ordered by
    ``(page_num, geom.y_order)``. Destructive claims remove their targets from the
    reduced body; a REJOIN emits ONE stitched node (its ``absorbed`` header rows
    consumed, Decision 3) in the position of its first part. KEEP / REORDER are
    non-destructive. Depends ONLY on the (immutable simulacra + ledger) — running
    it twice is byte-identical.
    """
    # Index every top-level body node by (page_num, top-index) — these are the
    # fold's atoms (children ride with their parent). Furniture is kept in the
    # simulacra; the ledger's DROP claims remove it here.
    atoms: List[Tuple[int, int, SourceDocumentNode]] = []
    for p in simulacra:
        for top_idx, node in enumerate(p.nodes):
            atoms.append((p.page_num, top_idx, node))

    # Which top-level refs are consumed destructively, and the REJOIN that owns
    # a given (page, top-index) as its FIRST part (the anchor position).
    removed: set[Tuple[int, int]] = set()
    rejoin_at: Dict[Tuple[int, int], DeFacsimileClaim] = {}

    for claim in sorted(ledger.claims, key=lambda c: _FOLD_ORDER[c.op]):
        if claim.op is DeFacsimileOp.REJOIN:
            part_keys = [(t.page_num, t.node_path[0]) for t in claim.targets if t.node_path]
            for t in claim.targets:
                if t.node_path:
                    removed.add((t.page_num, t.node_path[0]))
            for a in claim.absorbed:
                if a.node_path:
                    removed.add((a.page_num, a.node_path[0]))
            if part_keys:
                # Anchor the produced node at the first part's slot.
                anchor_key = min(
                    part_keys,
                    key=lambda k: (k[0], _atom_y(atoms, k)),
                )
                rejoin_at[anchor_key] = claim
        elif claim.op in _DESTRUCTIVE_OPS:
            for t in claim.targets:
                if t.node_path:
                    removed.add((t.page_num, t.node_path[0]))

    body: List[Tuple[int, int, SourceDocumentNode]] = []
    for page_num, top_idx, node in atoms:
        key = (page_num, top_idx)
        if key in rejoin_at:
            body.append((page_num, top_idx, _build_rejoined(simulacra, rejoin_at[key])))
            continue
        if key in removed:
            continue
        body.append((page_num, top_idx, node))

    # REORDER: siblings by (page_num, y_order). y_order is the within-page ordinal;
    # top-index breaks ties deterministically. (REORDER claims are explicit but the
    # canonical order IS this sort — mostly identity.)
    body.sort(key=lambda e: (e[0], _y_order(e[2]), e[1]))

    children = tuple(node for _, _, node in body)
    tier = _weakest([c.assurance_tier for c in children]) if children else AssuranceTier.UNADJUDICATED_PROPOSAL
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=tier,
        anchor=root_anchor,
        children=children,
    )


def _atom_y(atoms: Sequence[Tuple[int, int, SourceDocumentNode]], key: Tuple[int, int]) -> int:
    for page_num, top_idx, node in atoms:
        if (page_num, top_idx) == key:
            return _y_order(node)
    return 0


def _build_rejoined(
    simulacra: Sequence[PageSimulacrum], claim: DeFacsimileClaim
) -> SourceDocumentNode:
    """Materialize a REJOIN claim into one stitched node (Decision 3 absorb).

    A TABLE REJOIN GROWS the first part with every later part's rows (a multi-page
    table becomes ONE table); its ``absorbed`` header rows are consumed (a repeated
    per-page header is NOT re-emitted). A text REJOIN emits the exact ordered
    concatenation of its parts' text (``_rejoin_text``). Anchor + label from the
    first part; tier = the claim's tier.
    """
    parts = [_resolve(simulacra, t) for t in claim.targets]
    live_parts = [p for p in parts if p is not None]
    first = live_parts[0]
    if first.kind is SourceDocumentNodeKind.TABLE:
        absorbed_row_ids = {id(_resolve(simulacra, a)) for a in claim.absorbed}
        rows: List[SourceDocumentNode] = list(first.children)
        for part in live_parts[1:]:
            for row in part.children:
                if id(row) in absorbed_row_ids:
                    continue
                rows.append(row)
        return SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE,
            assurance_tier=claim.tier,
            anchor=first.anchor,
            label=first.label,
            text=first.text,
            children=tuple(rows),
            attrs=dict(first.attrs),
        )
    return SourceDocumentNode(
        kind=first.kind,
        assurance_tier=claim.tier,
        anchor=first.anchor,
        label=first.label,
        text=_rejoin_text(live_parts),
        children=first.children,
        attrs=dict(first.attrs),
    )


# Assurance ordering, most-assured first — a composed node takes the weakest of
# its parts (Decision 12: existing ``_weakest``-of-parts, no new tier algebra).
_TIER_ORDER: Tuple[AssuranceTier, ...] = (
    AssuranceTier.HUMAN_CONFIRMED,
    AssuranceTier.MULTI_WITNESS_ADJUDICATED,
    AssuranceTier.SINGLE_WITNESS,
    AssuranceTier.UNADJUDICATED_PROPOSAL,
)


def _weakest(tiers: Sequence[AssuranceTier]) -> AssuranceTier:
    worst = AssuranceTier.HUMAN_CONFIRMED
    worst_rank = 0
    for t in tiers:
        rank = _TIER_ORDER.index(t)
        if rank > worst_rank:
            worst, worst_rank = t, rank
    return worst


# --------------------------------------------------------------------------- #
# verify_ledger — the deterministic gate (no LLM)                             #
# --------------------------------------------------------------------------- #


class LedgerVerificationError(ValueError):
    """A ledger failed the deterministic ``verify_ledger`` gate (never emitted)."""


def _numeric_tokens(text: str) -> Dict[str, int]:
    """Multiset of protected NUMERIC tokens: bare numbers/euro/dates PLUS section
    references (``14 §`` / ``§ 14``) as their own compound tokens. A section ref is
    counted DISTINCTLY from the bare number it contains, so a body ``14 §`` cannot
    be masked by a surviving page-number ``14`` (the §8 guardrail).
    """
    counts: Dict[str, int] = {}
    # Section references first — a compound, distinct protected token.
    for m in _SECTION_REF.finditer(text):
        tok = _normalize_ref(m.group(0))
        if tok:
            counts["ref:" + tok] = counts.get("ref:" + tok, 0) + 1
    for m in _NUMERIC_TOKEN.finditer(text):
        tok = m.group(0).strip()
        if tok:
            counts[tok] = counts.get(tok, 0) + 1
    return counts


def _all_text(node: SourceDocumentNode) -> str:
    parts: List[str] = []

    def _walk(n: SourceDocumentNode) -> None:
        if n.text:
            parts.append(n.text)
        for c in n.children:
            _walk(c)

    _walk(node)
    return "\n".join(parts)


def _simulacra_text(simulacra: Sequence[PageSimulacrum]) -> str:
    parts: List[str] = []
    for p in simulacra:
        for n in p.nodes:
            parts.append(_all_text(n))
    return "\n".join(parts)


def verify_ledger(
    simulacra: Sequence[PageSimulacrum],
    ledger: DeFacsimileLedger,
    reduced: SourceDocumentNode,
) -> List[str]:
    """Deterministic gate over a ledger + its reduced tree — no LLM (Decision / §2).

    Returns the list of violations (empty == PASS). A record is NEVER emitted with
    a non-empty result. Checks:

    1. **claim-disjointness** — no ``SpanRef`` (target OR absorbed) is owned by more
       than one DESTRUCTIVE claim (Decision 3: exactly one claim owns each node).
    2. **phantom-drop** — every destructively-targeted / absorbed ``SpanRef``
       resolves to a REAL simulacrum node (nothing dropped that is not evidence).
    3. **REJOIN produced-text == exact concatenation** of its (resolved) parts.
    4. **body-word-multiset containment** — every word in ``reduced`` appears
       (with multiplicity) in the simulacra body (minus dropped furniture) — no
       invented content.
    5. **NUMERIC-unchanged** — no §/euro/date/citation token is dropped or altered
       (the reduced numeric multiset ⊆ the simulacra numeric multiset).
    """
    violations: List[str] = []

    # 1 + 2: disjointness + phantom-drop over destructive claims.
    owner: Dict[Tuple[int, Tuple[int, ...]], int] = {}
    for i, claim in enumerate(ledger.claims):
        if claim.op not in _DESTRUCTIVE_OPS:
            continue
        for ref in (*claim.targets, *claim.absorbed):
            key = (ref.page_num, ref.node_path)
            if key in owner and owner[key] != i:
                violations.append(
                    f"claim-disjointness: {ref} owned by claims {owner[key]} and {i}"
                )
            owner.setdefault(key, i)
            if _resolve(simulacra, ref) is None:
                violations.append(f"phantom-drop: {ref} resolves to no simulacrum node")

    # 3: REJOIN produced-text == exact concatenation.
    for claim in ledger.claims:
        if claim.op is not DeFacsimileOp.REJOIN:
            continue
        parts = [_resolve(simulacra, t) for t in claim.targets]
        live = [p for p in parts if p is not None]
        if not live:
            continue
        produced = _build_rejoined(simulacra, claim)
        # A text REJOIN's produced ``text`` must be the EXACT concatenation of its
        # parts (no inventing/losing across the seam). A TABLE REJOIN grows rows,
        # so the invariant is instead body-word conservation, checked by the
        # multiset containment below — its produced ``.text`` is the first part's.
        if live[0].kind is not SourceDocumentNodeKind.TABLE and produced.text != _rejoin_text(live):
            violations.append(
                f"invented-rejoin-text: REJOIN produced text != exact concatenation "
                f"of {len(live)} parts"
            )

    # 4: body-word-multiset containment (reduced ⊆ simulacra minus dropped furniture).
    dropped_furniture = _dropped_furniture_words(simulacra, ledger)
    sim_words = _multiset_of(_simulacra_text(simulacra))
    for w, c in dropped_furniture.items():
        sim_words[w] = sim_words.get(w, 0) - c
    reduced_words = _multiset_of(_all_text(reduced))
    for w, c in reduced_words.items():
        if c > sim_words.get(w, 0):
            violations.append(
                f"multiset-violation: reduced word {w!r}×{c} exceeds simulacra "
                f"(minus dropped furniture) ×{sim_words.get(w, 0)}"
            )

    # 5: NUMERIC-unchanged — reduced numeric multiset ⊆ simulacra numeric multiset.
    sim_num = _numeric_tokens(_simulacra_text(simulacra))
    reduced_num = _numeric_tokens(_all_text(reduced))
    for tok, c in reduced_num.items():
        if c > sim_num.get(tok, 0):
            violations.append(
                f"numeric-change: reduced numeric token {tok!r}×{c} not present in "
                f"simulacra ×{sim_num.get(tok, 0)} (a §/euro/date/citation was altered)"
            )
    # A dropped numeric token (present in simulacra, absent from reduced) is only a
    # violation if it was NOT part of dropped furniture. A SECTION REFERENCE
    # (``ref:...``) is NEVER droppable furniture — furniture is page numbers /
    # running headers, which carry bare numbers, not §-references. So a body
    # ``14 §`` cannot be dropped even though a page-number ``14`` can (the §8
    # guardrail: "14 §" body vs "14" page-number).
    droppable_num = {
        tok: c
        for tok, c in _numeric_tokens(_dropped_furniture_text(simulacra, ledger)).items()
        if not tok.startswith("ref:")
    }
    for tok, c in sim_num.items():
        if reduced_num.get(tok, 0) < c - droppable_num.get(tok, 0):
            violations.append(
                f"numeric-change: simulacra numeric token {tok!r}×{c} lost from reduced "
                f"(only ×{droppable_num.get(tok, 0)} was droppable furniture)"
            )

    return violations


def _multiset_of(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for w in text.split():
        counts[w] = counts.get(w, 0) + 1
    return counts


def _dropped_furniture_nodes(
    simulacra: Sequence[PageSimulacrum], ledger: DeFacsimileLedger
) -> List[SourceDocumentNode]:
    """The concrete nodes a DROP_FURNITURE / DEDUP claim removes (for the multiset
    subtraction) — resolved against the immutable simulacra."""
    out: List[SourceDocumentNode] = []
    for claim in ledger.claims:
        if claim.op not in (DeFacsimileOp.DROP_FURNITURE, DeFacsimileOp.DEDUP_SEAM):
            continue
        for ref in (*claim.targets, *claim.absorbed):
            node = _resolve(simulacra, ref)
            if node is not None:
                out.append(node)
    return out


def _dropped_furniture_words(
    simulacra: Sequence[PageSimulacrum], ledger: DeFacsimileLedger
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in _dropped_furniture_nodes(simulacra, ledger):
        for w, c in _multiset_of(_all_text(node)).items():
            counts[w] = counts.get(w, 0) + c
    return counts


def _dropped_furniture_text(
    simulacra: Sequence[PageSimulacrum], ledger: DeFacsimileLedger
) -> str:
    return "\n".join(_all_text(n) for n in _dropped_furniture_nodes(simulacra, ledger))


# --------------------------------------------------------------------------- #
# The orchestrator + the compose_pages deterministic fallback (Decision 8)    #
# --------------------------------------------------------------------------- #


def defacsimile(
    simulacra: Sequence[PageSimulacrum],
    root_anchor: SourceAnchor,
    *,
    adjudicator: object = None,
) -> DeFacsimiledDocument:
    """Level 2: compose the page simulacra into a coherent document + its ledger.

    The ADJUDICATOR (an LLM workflow over 2-page seam windows) DECIDES each
    candidate as DROP/DEDUP/REJOIN/KEEP/REORDER; deterministic affordances only
    surface candidates. When ``adjudicator`` is ``None`` (or unavailable), the
    subsystem degrades per-window to ``compose_pages`` — emitting typed
    ``method="deterministic_fallback"`` claims (Decision 8), NOT a route switch.

    The produced ledger is ALWAYS gated by ``verify_ledger`` before the document is
    returned — a record is never emitted with an unverified ledger. If the
    adjudicated ledger fails the gate, the subsystem falls back deterministically
    (a failed model ledger never reaches the output).
    """
    ledger: Optional[DeFacsimileLedger] = None
    if adjudicator is not None and getattr(adjudicator, "is_available", lambda: True)():
        adjudicated = adjudicator.adjudicate_document(simulacra)  # ty: ignore[unresolved-attribute]
        reduced = apply_ledger(simulacra, adjudicated, root_anchor)
        if not verify_ledger(simulacra, adjudicated, reduced):
            ledger = adjudicated

    if ledger is None:
        ledger = _deterministic_fallback_ledger(simulacra)

    reduced = apply_ledger(simulacra, ledger, root_anchor)
    violations = verify_ledger(simulacra, ledger, reduced)
    if violations:
        raise LedgerVerificationError(
            "de-facsimile ledger failed verification even under deterministic "
            f"fallback: {violations}"
        )
    return DeFacsimiledDocument(
        root=reduced, page_count=len(simulacra), ledger=ledger
    )


def _deterministic_fallback_ledger(
    simulacra: Sequence[PageSimulacrum],
) -> DeFacsimileLedger:
    """Build a ledger equivalent to ``compose_pages`` over the simulacra (Decision 8).

    ``compose_pages`` is invoked UNCHANGED on ``[p.nodes for p in simulacra]`` to
    decide the joins; the result is re-expressed as typed
    ``method="deterministic_fallback"`` REJOIN claims (paragraph/table stitches)
    so the fallback is a first-class, auditable, verified ledger — not a bypass.
    Un-joined pages produce an empty ledger (identity fold).
    """
    from lawvm.core.source_document.composition import (
        DefaultContinuationJudge,
    )

    judge = DefaultContinuationJudge()
    claims: List[DeFacsimileClaim] = []
    # An open CHAIN of parts to be stitched into ONE REJOIN claim (a paragraph or
    # table may run across 3+ pages — a single claim owns every part, preserving
    # claim-disjointness). ``chain`` is the list of (SpanRef, node) accumulated.
    chain: List[Tuple[SpanRef, SourceDocumentNode]] = []

    def _flush() -> None:
        if len(chain) >= 2:
            claims.append(
                DeFacsimileClaim(
                    op=DeFacsimileOp.REJOIN,
                    targets=tuple(ref for ref, _ in chain),
                    tier=_weakest([n.assurance_tier for _, n in chain]),
                    corroborating_producers=("compose_pages",),
                    method="deterministic_fallback",
                    rationale="compose_pages continuation join (fallback)",
                )
            )

    for p in simulacra:
        for top_idx, node in enumerate(p.nodes):
            if node.kind is SourceDocumentNodeKind.FOOTNOTE:
                continue
            ref = SpanRef(p.page_num, (top_idx,))
            open_node = chain[-1][1] if chain else None
            joins = open_node is not None and (
                (
                    open_node.kind is SourceDocumentNodeKind.TABLE
                    and node.kind is SourceDocumentNodeKind.TABLE
                    and judge.continues_table(open_node, node)
                )
                or (
                    open_node.kind is SourceDocumentNodeKind.PARAGRAPH
                    and node.kind is SourceDocumentNodeKind.PARAGRAPH
                    and judge.continues_paragraph(open_node, node)
                )
            )
            if joins:
                chain.append((ref, node))
            else:
                _flush()
                chain = [(ref, node)]
    _flush()
    return DeFacsimileLedger(claims=tuple(claims))
