"""Level-2 de-facsimile — the stigmergic (blackboard) composer (M3 keystone).

This is the §7 blackboard track. It REPLACES the fixed one-shot 2-page-window
adjudication of ``defacsimile()`` (M1) with a classic BLACKBOARD: a shared,
persisted, provenance-carrying workspace that bounded-context knowledge-source
subagents READ and POST typed marks to, iterating to a fixpoint. It is NOT a chat
agent harness — the subagents coordinate ONLY through the workspace (the marks)
and the deterministic verifier (``verify_ledger`` in ``defacsimile``).

The design (AGENTS §1.8, spec guiding principle "always intelligence, never
mechanical heuristics for the semantic call"): mechanics deterministically
PRE-SEED candidate/epistemic marks from the §3 metadata already on the simulacra;
knowledge sources (the seam adjudicator as the first, others behind the extensible
dispatch table) READ the marks in their bounded region and POST decision marks +
affordance CONTROL lines the harness acts on (PAGE / EXPAND / VIEW / NOTES /
PREFIX). The controller schedules the next source on the highest-value
UNDECIDED/OPEN/CONTESTED region; the loop terminates at a stigmergic fixpoint (a
full sweep adds no marks and leaves no live region) OR on budget exhaustion, at
which point the residue is decided with what is visible — typed
``context_exhausted`` — falling back to the deterministic ``compose_pages`` claim
for that region, NEVER a silent drop.

The journal is CONTENT-ADDRESSED and append-mostly: the same simulacra ⇒ a
byte-identical journal ⇒ a byte-identical ledger (the determinism-firewall
discipline). ``verify_ledger`` remains the hard gate — a failed model ledger never
reaches output; the promoted ledger is re-verified and, on failure, the whole
region degrades to the deterministic fallback.

FROZEN §5.5 carriers (``DeFacsimileClaim`` / ``DeFacsimileOp`` / ``SpanRef``) are
extended ONLY additively — every new type here is a NEW dataclass, never a
field-mutation of a frozen one.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import SourceDocumentNode
from lawvm.ingest.defacsimile import (
    DeFacsimileClaim,
    DeFacsimileLedger,
    DeFacsimileOp,
    DeFacsimiledDocument,
    _deterministic_fallback_ledger,
    apply_ledger,
    verify_ledger,
)
from lawvm.ingest.metadata import decode_metadata
from lawvm.ingest.simulacrum import PageSimulacrum, SpanRef

# --------------------------------------------------------------------------- #
# Mark vocabulary (§7.1). Three families keyed by a SpanRef REGION.           #
# --------------------------------------------------------------------------- #


class MarkKind(Enum):
    """The typed marks a knowledge source may post to the workspace.

    CANDIDATE family (a surfaced possibility, decided later):
      * ``DROP_Q`` / ``DEDUP_Q`` / ``REJOIN_Q`` / ``KEEP_Q`` / ``REORDER_Q`` /
        ``FURNITURE_Q`` — a candidate op the region MIGHT warrant.

    DECISION family (a resolved call → promoted to a ``DeFacsimileClaim``):
      * ``DECIDE_DROP`` / ``DECIDE_DEDUP`` / ``DECIDE_REJOIN`` / ``DECIDE_KEEP`` /
        ``DECIDE_REORDER``.

    EPISTEMIC family (the region's live status):
      * ``OPEN`` — needs more work (e.g. an unresolved continuation chain).
      * ``GARBLE`` — a freeform-flagged region (math / image-baked / garbled).
      * ``CONTESTED`` — two sources posted conflicting decisions.
      * ``DEFER`` — a source asked to be re-scheduled with more context.

    Serialization uses the descriptive ``.value`` label (the LLM-guide rule:
    internal codes never leak; the journal blob carries descriptive labels).
    """

    # candidate
    DROP_Q = "candidate_drop"
    DEDUP_Q = "candidate_dedup"
    REJOIN_Q = "candidate_rejoin"
    KEEP_Q = "candidate_keep"
    REORDER_Q = "candidate_reorder"
    FURNITURE_Q = "candidate_furniture"
    # decision
    DECIDE_DROP = "decide_drop"
    DECIDE_DEDUP = "decide_dedup"
    DECIDE_REJOIN = "decide_rejoin"
    DECIDE_KEEP = "decide_keep"
    DECIDE_REORDER = "decide_reorder"
    # epistemic
    OPEN = "open"
    GARBLE = "garble"
    CONTESTED = "contested"
    DEFER = "defer"

    @override
    def __str__(self) -> str:
        return self.value


_CANDIDATE_KINDS = frozenset(
    {
        MarkKind.DROP_Q,
        MarkKind.DEDUP_Q,
        MarkKind.REJOIN_Q,
        MarkKind.KEEP_Q,
        MarkKind.REORDER_Q,
        MarkKind.FURNITURE_Q,
    }
)
_DECISION_KINDS = frozenset(
    {
        MarkKind.DECIDE_DROP,
        MarkKind.DECIDE_DEDUP,
        MarkKind.DECIDE_REJOIN,
        MarkKind.DECIDE_KEEP,
        MarkKind.DECIDE_REORDER,
    }
)
_EPISTEMIC_KINDS = frozenset(
    {MarkKind.OPEN, MarkKind.GARBLE, MarkKind.CONTESTED, MarkKind.DEFER}
)

# The three epistemic marks that make a region LIVE (the controller keeps
# scheduling until no live region remains and a sweep adds nothing).
_LIVE_EPISTEMIC = frozenset({MarkKind.OPEN, MarkKind.CONTESTED})

# Which decision mark a resolved op promotes from.
_OP_TO_DECISION = {
    DeFacsimileOp.DROP_FURNITURE: MarkKind.DECIDE_DROP,
    DeFacsimileOp.DEDUP_SEAM: MarkKind.DECIDE_DEDUP,
    DeFacsimileOp.REJOIN: MarkKind.DECIDE_REJOIN,
    DeFacsimileOp.KEEP: MarkKind.DECIDE_KEEP,
    DeFacsimileOp.REORDER: MarkKind.DECIDE_REORDER,
}


@dataclass(frozen=True, slots=True)
class Mark:
    """One typed, provenance-carrying mark over a region (§7.1).

    ``region`` is the tuple of ``SpanRef`` the mark is about (a candidate/decision
    over one or more nodes; an epistemic mark over the region it applies to).
    ``producer_id`` + ``round`` + ``evidence_refs`` + ``rationale`` make every mark
    auditable and reversible — the workspace is append-mostly, never a silent
    overwrite. A promoted decision may carry the concrete ``DeFacsimileClaim`` it
    stands for (so the controller can lift the ledger straight off the journal).
    """

    kind: MarkKind
    region: Tuple[SpanRef, ...]
    producer_id: str
    round: int
    evidence_refs: Tuple[SpanRef, ...] = ()
    rationale: str = ""
    claim: Optional[DeFacsimileClaim] = None

    @property
    def region_key(self) -> Tuple[Tuple[int, Tuple[int, ...]], ...]:
        """Order-independent canonical identity of the region (for grouping)."""
        return tuple(sorted((r.page_num, r.node_path) for r in self.region))


# --------------------------------------------------------------------------- #
# Affordance dispatch (§7.2) — the typed, EXTENSIBLE control-line table.       #
# --------------------------------------------------------------------------- #


class AffordanceKind(Enum):
    """The control lines a knowledge source emits and the harness ACTS on.

    READS (bounded by budget):
      * ``PAGE`` — request one page's blocks be rendered into the next prompt.
      * ``EXPAND`` — widen the region window to pages [lo, hi] (``max_context_pages``).
      * ``VIEW`` — request a rasterized crop of a page bbox (``max_views``, consumes
        ``visual.render_region_crop``).
      * ``NOTES`` — read the notes already posted on a region.
      * ``PREFIX`` — read the reduced-so-far document prefix (running context).
    WRITES:
      * ``NOTE`` — post a free note onto a region (shared scratch).
      * ``DEFER`` — ask to be re-scheduled with more context.
    """

    PAGE = "page"
    EXPAND = "expand"
    VIEW = "view"
    NOTES = "notes"
    PREFIX = "prefix"
    NOTE = "note"
    DEFER = "defer"


@dataclass(frozen=True, slots=True)
class AffordanceRequest:
    """A parsed control line: the kind + its positional args + optional bbox.

    ``args`` are the raw string tokens after the verb (page numbers, ids); ``bbox``
    is the optional 4-float region for a ``VIEW``. The harness dispatches on
    ``kind`` through ``AFFORDANCE_DISPATCH`` — an extensible table, so a new
    affordance is one registration, not a control-flow edit.
    """

    kind: AffordanceKind
    args: Tuple[str, ...] = ()
    bbox: Optional[Tuple[float, float, float, float]] = None
    page_num: Optional[int] = None
    note_text: str = ""


@dataclass(frozen=True, slots=True)
class BlackboardBudget:
    """The bounded-context budget (§7.2). Every read affordance is rate-limited.

    Defaults are conservative — the composer is input-heavy / output-sparse; a
    source should rarely need to VIEW or EXPAND. ``max_rounds`` bounds the whole
    controller loop so a pathological non-fixpoint terminates.
    """

    max_context_pages: int = 4
    max_expansions: int = 2
    max_views: int = 3
    max_rounds: int = 8


@dataclass
class BudgetLedger:
    """Mutable per-run tally of consumed budget (checked before each read)."""

    expansions_used: int = 0
    views_used: int = 0
    pages_read: int = 0

    def can_expand(self, budget: BlackboardBudget) -> bool:
        return self.expansions_used < budget.max_expansions

    def can_view(self, budget: BlackboardBudget) -> bool:
        return self.views_used < budget.max_views

    def can_page(self, budget: BlackboardBudget) -> bool:
        return self.pages_read < budget.max_context_pages


# --------------------------------------------------------------------------- #
# The workspace journal (§7.1) — append-mostly, content-addressed.            #
# --------------------------------------------------------------------------- #


@dataclass
class Workspace:
    """The shared blackboard: the ordered mark journal + the notes scratch.

    Append-mostly (a mark is never mutated in place; a decision SUPERSEDES a
    candidate by being posted, and ``resolved_regions`` records which regions are
    closed). ``notes`` is the free-note scratch a NOTE affordance writes / a NOTES
    affordance reads. ``digest`` is content-addressed over the whole journal so the
    same simulacra ⇒ a byte-identical workspace.
    """

    marks: List[Mark] = field(default_factory=list)
    notes: Dict[Tuple[Tuple[int, Tuple[int, ...]], ...], List[str]] = field(
        default_factory=dict
    )
    round: int = 0

    def post(self, mark: Mark) -> bool:
        """Append a mark IF it is new (idempotent — a re-posted mark is a no-op).

        Returns whether the mark was actually added (the fixpoint detector counts
        real additions only). Identity is (kind, region_key, producer_id) — the
        same source re-asserting the same call on the same region adds nothing.
        """
        ident = (mark.kind, mark.region_key, mark.producer_id)
        for existing in self.marks:
            if (existing.kind, existing.region_key, existing.producer_id) == ident:
                return False
        self.marks.append(mark)
        return True

    def add_note(self, region: Tuple[SpanRef, ...], text: str) -> None:
        key = tuple(sorted((r.page_num, r.node_path) for r in region))
        self.notes.setdefault(key, []).append(text)

    def notes_for(self, region: Tuple[SpanRef, ...]) -> Tuple[str, ...]:
        key = tuple(sorted((r.page_num, r.node_path) for r in region))
        return tuple(self.notes.get(key, ()))

    def marks_for_region(
        self, region_key: Tuple[Tuple[int, Tuple[int, ...]], ...]
    ) -> List[Mark]:
        return [m for m in self.marks if m.region_key == region_key]

    def digest(self) -> str:
        """Content-address the whole journal (sorted-keys JSON → SHA-256)."""
        return hashlib.sha256(serialize_workspace(self)).hexdigest()


# --------------------------------------------------------------------------- #
# Deterministic mark pre-seeding (§7.1) from the §3 metadata.                  #
# --------------------------------------------------------------------------- #

# band_count at/above which a node is pre-seeded FURNITURE? (a running header
# recurs across the document — the §3 recurrence affordance).
_FURNITURE_BAND_THETA = 2

# Freeform reasons that pre-seed a GARBLE? epistemic mark.
_GARBLE_REASONS = frozenset({"math", "image_baked", "garbled_source"})

_PRESEED_PRODUCER = "preseed:metadata"


def _open_continuation_crosses_edge(
    prev_page: PageSimulacrum, next_page: PageSimulacrum
) -> Optional[Tuple[SpanRef, SpanRef]]:
    """Does an open continuation cue-chain cross the page edge between two pages?

    The last body node of ``prev_page`` ending WITHOUT terminal punctuation and the
    first body node of ``next_page`` starting lower-case is an OPEN continuation
    across the seam (a mid-sentence paragraph split at the page edge). Returns the
    (tail, head) SpanRef pair, or ``None`` when no such cue-chain exists.
    """
    tail: Optional[Tuple[int, SourceDocumentNode]] = None
    for top_idx, node in enumerate(prev_page.nodes):
        meta = decode_metadata(node.attrs)
        if meta.furniture or not node.text.strip():
            continue
        tail = (top_idx, node)
    if tail is None:
        return None
    head: Optional[Tuple[int, SourceDocumentNode]] = None
    for top_idx, node in enumerate(next_page.nodes):
        meta = decode_metadata(node.attrs)
        if meta.furniture or not node.text.strip():
            continue
        head = (top_idx, node)
        break
    if head is None:
        return None
    tail_meta = decode_metadata(tail[1].attrs)
    head_meta = decode_metadata(head[1].attrs)
    # An open cue-chain: the tail does NOT end on terminal punctuation AND the head
    # starts lower-case (a genuine mid-sentence split). Both cues must fire — a
    # single cue is not enough to call the seam OPEN.
    if not tail_meta.ends_terminal and head_meta.starts_lower:
        return (
            SpanRef(prev_page.page_num, (tail[0],)),
            SpanRef(next_page.page_num, (head[0],)),
        )
    return None


def preseed_workspace(simulacra: Sequence[PageSimulacrum]) -> Workspace:
    """Deterministically pre-seed the workspace from the §3 simulacra metadata.

    Three seed rules (spec §7.1):
      1. ``rec.band_count >= θ`` → ``FURNITURE?`` candidate on that node.
      2. ``freeform.reason ∈ {math, image_baked, garbled_source}`` → ``GARBLE``
         epistemic mark on that node.
      3. an OPEN continuation cue-chain crossing a page edge → ``OPEN`` on the
         (tail, head) seam pair.

    Pure — the same simulacra always seed the same workspace (round 0).
    """
    ws = Workspace()
    for p in simulacra:
        for top_idx, node in enumerate(p.nodes):
            meta = decode_metadata(node.attrs)
            ref = SpanRef(p.page_num, (top_idx,))
            if meta.band_count is not None and meta.band_count >= _FURNITURE_BAND_THETA:
                ws.post(
                    Mark(
                        kind=MarkKind.FURNITURE_Q,
                        region=(ref,),
                        producer_id=_PRESEED_PRODUCER,
                        round=0,
                        rationale=f"rec.band_count={meta.band_count} >= {_FURNITURE_BAND_THETA}",
                    )
                )
            if meta.freeform_reason in _GARBLE_REASONS:
                ws.post(
                    Mark(
                        kind=MarkKind.GARBLE,
                        region=(ref,),
                        producer_id=_PRESEED_PRODUCER,
                        round=0,
                        rationale=f"freeform.reason={meta.freeform_reason}",
                    )
                )
    pages = list(simulacra)
    for prev, nxt in zip(pages, pages[1:], strict=False):
        seam = _open_continuation_crosses_edge(prev, nxt)
        if seam is not None:
            ws.post(
                Mark(
                    kind=MarkKind.OPEN,
                    region=seam,
                    producer_id=_PRESEED_PRODUCER,
                    round=0,
                    rationale="open continuation cue-chain crosses page edge",
                )
            )
    return ws


# --------------------------------------------------------------------------- #
# Knowledge-source protocol (§7.3) — a bounded single-purpose subagent.        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SourceOutput:
    """What a knowledge source returns for ONE scheduled region.

    ``marks`` are the typed marks it posts (candidate / decision / epistemic);
    ``affordances`` are the control lines the harness must act on before the source
    can decide (READs) or that mutate the workspace (WRITEs). A source that fully
    decides its region posts a DECISION mark and no live epistemic mark.
    """

    marks: Tuple[Mark, ...] = ()
    affordances: Tuple[AffordanceRequest, ...] = ()


class KnowledgeSource:
    """Base class for a bounded single-purpose blackboard knowledge source (§7.3).

    A source is scheduled on ONE region; it READs the marks + notes visible for
    that region and POSTs typed marks + affordance control lines. It coordinates
    with other sources ONLY through the workspace — never by direct call. Concrete
    sources: the seam adjudicator (below), plus the furniture classifier / visual
    transcriber / chain-closer / contest-resolver (some DEFERRED, see §7.3).
    """

    source_id: str = "knowledge_source"

    def wants(self, workspace: Workspace, region_key: Tuple[Tuple[int, Tuple[int, ...]], ...]) -> bool:
        """Is this source applicable to the scheduled region? (default: yes)."""
        return True

    def run(
        self,
        workspace: Workspace,
        region: Tuple[SpanRef, ...],
        simulacra: Sequence[PageSimulacrum],
    ) -> SourceOutput:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# The affordance dispatch table (§7.2) — typed, extensible.                    #
# --------------------------------------------------------------------------- #

# A crop function has the FROZEN signature of ``visual.render_region_crop``:
#   (manifestation, page_num, bbox, dpi) -> bytes
CropFn = Callable[[object, int, Tuple[float, float, float, float], int], bytes]


@dataclass
class DispatchContext:
    """Everything an affordance handler needs to act on a control line."""

    workspace: Workspace
    region: Tuple[SpanRef, ...]
    simulacra: Sequence[PageSimulacrum]
    budget: BlackboardBudget
    used: BudgetLedger
    manifestation: object = None
    crop_fn: Optional[CropFn] = None
    view_dpi: int = 200
    # Read results accumulated for the NEXT scheduling of this region.
    view_bytes: List[bytes] = field(default_factory=list)
    expanded_pages: List[int] = field(default_factory=list)


def _handle_view(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """VIEW <n> [bbox] — rasterize a page crop via ``visual.render_region_crop``.

    Bounded by ``max_views``. The crop function is INJECTED (``ctx.crop_fn``) so
    tests fake it; the live path imports ``lawvm.ingest.visual`` LAZILY so the
    harness runs whether or not the (parallel-track-owned) module exists yet.
    """
    if not ctx.used.can_view(ctx.budget):
        return
    page_num = req.page_num if req.page_num is not None else (
        ctx.region[0].page_num if ctx.region else 0
    )
    bbox = req.bbox if req.bbox is not None else (0.0, 0.0, 1.0, 1.0)
    crop = ctx.crop_fn
    if crop is None:
        try:  # live path — module owned by a parallel track, import lazily
            from lawvm.ingest.visual import render_region_crop as crop  # type: ignore
        except Exception:
            return  # no crop capability available; the VIEW is a no-op (typed budget spent below)
    try:
        data = crop(ctx.manifestation, page_num, bbox, ctx.view_dpi)
    except Exception:
        return
    ctx.used.views_used += 1
    ctx.view_bytes.append(data)


def _handle_expand(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """EXPAND <lo> <hi> — widen the region window (bounded by ``max_expansions``)."""
    if not ctx.used.can_expand(ctx.budget):
        return
    try:
        lo = int(req.args[0])
        hi = int(req.args[1])
    except (IndexError, ValueError):
        return
    ctx.used.expansions_used += 1
    for n in range(lo, hi + 1):
        if n not in ctx.expanded_pages:
            ctx.expanded_pages.append(n)


def _handle_page(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """PAGE <n> — request a page's blocks be included (bounded by ``max_context_pages``)."""
    if not ctx.used.can_page(ctx.budget):
        return
    ctx.used.pages_read += 1
    if req.page_num is not None and req.page_num not in ctx.expanded_pages:
        ctx.expanded_pages.append(req.page_num)


def _handle_notes(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """NOTES <region> — a READ; the notes are surfaced into the next prompt.

    A no-op mutation of the workspace (notes are read straight off ``ctx.workspace``
    when the region is next scheduled); recorded here as an explicit dispatch so
    the table stays total.
    """
    return


def _handle_prefix(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """PREFIX — a READ of the reduced-so-far prefix; surfaced by the controller."""
    return


def _handle_note(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """NOTE — WRITE a free note onto the region's shared scratch."""
    if req.note_text:
        ctx.workspace.add_note(ctx.region, req.note_text)


def _handle_defer(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """DEFER — WRITE a DEFER epistemic mark (re-schedule with more context)."""
    ctx.workspace.post(
        Mark(
            kind=MarkKind.DEFER,
            region=ctx.region,
            producer_id="affordance:defer",
            round=ctx.workspace.round,
            rationale=req.note_text or "source requested deferral",
        )
    )


# The EXTENSIBLE dispatch table: a new affordance is one registration here, not a
# control-flow edit. Every handler is (DispatchContext, AffordanceRequest) -> None.
AFFORDANCE_DISPATCH: Dict[
    AffordanceKind, Callable[[DispatchContext, AffordanceRequest], None]
] = {
    AffordanceKind.VIEW: _handle_view,
    AffordanceKind.EXPAND: _handle_expand,
    AffordanceKind.PAGE: _handle_page,
    AffordanceKind.NOTES: _handle_notes,
    AffordanceKind.PREFIX: _handle_prefix,
    AffordanceKind.NOTE: _handle_note,
    AffordanceKind.DEFER: _handle_defer,
}


def dispatch_affordance(ctx: DispatchContext, req: AffordanceRequest) -> None:
    """Route one control line through the extensible table (fail-loud on unknown)."""
    handler = AFFORDANCE_DISPATCH.get(req.kind)
    if handler is None:
        raise KeyError(f"no affordance handler registered for {req.kind!r}")
    handler(ctx, req)


def parse_affordance_line(line: str) -> Optional[AffordanceRequest]:
    """Parse ONE control line into a typed ``AffordanceRequest`` (or ``None``).

    Line grammar (the LLM-guide line-based discipline — never JSON):
      ``PAGE <n>`` · ``EXPAND <lo> <hi>`` · ``VIEW <n> [x0 y0 x1 y1]`` ·
      ``NOTES`` · ``PREFIX`` · ``NOTE <text...>`` · ``DEFER [text...]``
    """
    toks = line.strip().split()
    if not toks:
        return None
    verb = toks[0].upper()
    try:
        kind = AffordanceKind(verb.lower())
    except ValueError:
        return None
    rest = toks[1:]
    if kind is AffordanceKind.VIEW:
        page_num = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else None
        bbox: Optional[Tuple[float, float, float, float]] = None
        if len(rest) >= 5:
            try:
                bbox = (float(rest[1]), float(rest[2]), float(rest[3]), float(rest[4]))
            except ValueError:
                bbox = None
        return AffordanceRequest(kind=kind, args=tuple(rest), page_num=page_num, bbox=bbox)
    if kind is AffordanceKind.PAGE:
        page_num = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else None
        return AffordanceRequest(kind=kind, args=tuple(rest), page_num=page_num)
    if kind in (AffordanceKind.NOTE, AffordanceKind.DEFER):
        return AffordanceRequest(kind=kind, args=tuple(rest), note_text=" ".join(rest))
    return AffordanceRequest(kind=kind, args=tuple(rest))


# --------------------------------------------------------------------------- #
# The controller loop (§7) — deterministic scheduling to a stigmergic fixpoint. #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScheduledRegion:
    """A region the controller schedules, with its scheduling priority."""

    region_key: Tuple[Tuple[int, Tuple[int, ...]], ...]
    region: Tuple[SpanRef, ...]
    priority: int  # lower = scheduled first


# Scheduling priority (chosen default; §7 open decision). CONTESTED is the most
# urgent — an unresolved conflict blocks the fixpoint and must be adjudicated
# first. OPEN (an unresolved continuation chain) is next. A live CANDIDATE with no
# decision yet is last. GARBLE / DEFER are NOT independently scheduled — GARBLE is
# a status carried into whatever region it sits on; DEFER re-opens its region as a
# candidate the next sweep. Ties break on region_key (lexicographic) so the schedule
# is deterministic.
_PRIORITY_CONTESTED = 0
_PRIORITY_OPEN = 1
_PRIORITY_CANDIDATE = 2


def _decided_region_keys(ws: Workspace) -> set:
    """Region keys that already carry a DECISION mark (closed regions)."""
    return {m.region_key for m in ws.marks if m.kind in _DECISION_KINDS}


def _live_regions(ws: Workspace) -> List[ScheduledRegion]:
    """The UNDECIDED / OPEN / CONTESTED regions, in deterministic schedule order.

    A region is LIVE when it carries an OPEN or CONTESTED epistemic mark, OR a
    CANDIDATE mark with no DECISION yet. Decided regions are dropped. The result is
    sorted by (priority, region_key) so scheduling is reproducible.
    """
    decided = _decided_region_keys(ws)
    # A CONTESTED region is LIVE even if it carries decision marks — the conflict
    # is precisely BETWEEN two decisions, so "decided" does not close it. Every
    # other region is closed once any decision owns it.
    contested = {m.region_key for m in ws.marks if m.kind is MarkKind.CONTESTED}
    best: Dict[Tuple[Tuple[int, Tuple[int, ...]], ...], ScheduledRegion] = {}

    def _consider(mark: Mark, priority: int) -> None:
        if mark.region_key in decided and mark.region_key not in contested:
            return
        prior = best.get(mark.region_key)
        if prior is None or priority < prior.priority:
            best[mark.region_key] = ScheduledRegion(
                region_key=mark.region_key, region=mark.region, priority=priority
            )

    for m in ws.marks:
        if m.kind is MarkKind.CONTESTED:
            _consider(m, _PRIORITY_CONTESTED)
        elif m.kind is MarkKind.OPEN:
            _consider(m, _PRIORITY_OPEN)
        elif m.kind in _CANDIDATE_KINDS or m.kind is MarkKind.DEFER:
            _consider(m, _PRIORITY_CANDIDATE)

    return sorted(best.values(), key=lambda s: (s.priority, s.region_key))


def _detect_contests(ws: Workspace) -> bool:
    """Post CONTESTED where two producers posted CONFLICTING decisions on a region.

    A conflict = two DIFFERENT decision kinds on the same region_key from different
    producers (e.g. one DECIDE_DROP, another DECIDE_KEEP). Returns whether any new
    CONTESTED mark was added (feeds the fixpoint detector).
    """
    by_region: Dict[Tuple[Tuple[int, Tuple[int, ...]], ...], set] = {}
    for m in ws.marks:
        if m.kind in _DECISION_KINDS:
            by_region.setdefault(m.region_key, set()).add(m.kind)
    added = False
    for region_key, kinds in by_region.items():
        if len(kinds) > 1:
            region = next(m.region for m in ws.marks if m.region_key == region_key)
            if ws.post(
                Mark(
                    kind=MarkKind.CONTESTED,
                    region=region,
                    producer_id="controller:contest_detect",
                    round=ws.round,
                    rationale=f"conflicting decisions: {sorted(str(k) for k in kinds)}",
                )
            ):
                added = True
    return added


@dataclass
class BlackboardResult:
    """The blackboard run outcome: the promoted ledger + the workspace journal."""

    ledger: DeFacsimileLedger
    workspace: Workspace
    termination: str  # fixpoint | budget_exhausted
    context_exhausted_regions: Tuple[Tuple[Tuple[int, Tuple[int, ...]], ...], ...]


class BlackboardController:
    """The deterministic controller: schedule sources to a stigmergic fixpoint.

    Round loop (§7): (1) detect contests; (2) find the highest-value live region;
    (3) schedule the applicable knowledge source on it; (4) act on its affordance
    control lines (bounded reads / workspace writes); (5) post its marks. Terminate
    when a FULL sweep adds no new marks and no live region remains (fixpoint), OR
    the round budget is exhausted (budget_exhausted) — the residue is then decided
    with what is visible, typed ``context_exhausted``, falling back to the
    deterministic ``compose_pages`` claim for that region (NEVER a silent drop).
    """

    def __init__(
        self,
        sources: Sequence[KnowledgeSource],
        *,
        budget: Optional[BlackboardBudget] = None,
        manifestation: object = None,
        crop_fn: Optional[CropFn] = None,
        view_dpi: int = 200,
    ) -> None:
        self._sources = list(sources)
        self._budget = budget or BlackboardBudget()
        self._manifestation = manifestation
        self._crop_fn = crop_fn
        self._view_dpi = view_dpi

    def run(
        self, simulacra: Sequence[PageSimulacrum], workspace: Workspace
    ) -> BlackboardResult:
        used = BudgetLedger()
        termination = "fixpoint"
        for _ in range(self._budget.max_rounds):
            workspace.round += 1
            added_this_sweep = _detect_contests(workspace)
            live = _live_regions(workspace)
            if not live:
                # No live region: one clean confirming sweep with no additions = fixpoint.
                if not added_this_sweep:
                    break
                continue
            # Schedule the single highest-value region this round (deterministic).
            target = live[0]
            for source in self._sources:
                if not source.wants(workspace, target.region_key):
                    continue
                out = source.run(workspace, target.region, simulacra)
                ctx = DispatchContext(
                    workspace=workspace,
                    region=target.region,
                    simulacra=simulacra,
                    budget=self._budget,
                    used=used,
                    manifestation=self._manifestation,
                    crop_fn=self._crop_fn,
                    view_dpi=self._view_dpi,
                )
                for req in out.affordances:
                    dispatch_affordance(ctx, req)
                for mark in out.marks:
                    if workspace.post(mark):
                        added_this_sweep = True
                break
            if not added_this_sweep:
                # A scheduled region that produced NOTHING new would spin forever —
                # close it by deferring to the deterministic residue below.
                break
        else:
            termination = "budget_exhausted"

        ledger, exhausted = self._promote(simulacra, workspace)
        return BlackboardResult(
            ledger=ledger,
            workspace=workspace,
            termination=termination,
            context_exhausted_regions=tuple(exhausted),
        )

    def _promote(
        self, simulacra: Sequence[PageSimulacrum], workspace: Workspace
    ) -> Tuple[DeFacsimileLedger, List[Tuple[Tuple[int, Tuple[int, ...]], ...]]]:
        """Lift the DECISION marks off the journal into a disjoint ledger.

        A DECISION mark carrying its ``DeFacsimileClaim`` promotes directly; the
        controller keeps the FIRST decision per node (claim-disjointness — exactly
        one claim owns each node, ``verify_ledger`` enforces it downstream). Any
        region still LIVE at termination (no decision reached) is decided with the
        deterministic ``compose_pages`` claim for that region, typed
        ``context_exhausted`` — never a silent drop.
        """
        owned: set = set()
        claims: List[DeFacsimileClaim] = []
        # A CONTESTED region is resolved by the LATEST decision on it (a contest
        # resolver posts last); pre-compute the winning producer/round per contested
        # region so the earlier, superseded decision is skipped.
        contested = {m.region_key for m in workspace.marks if m.kind is MarkKind.CONTESTED}
        latest_on_region: Dict[Tuple[Tuple[int, Tuple[int, ...]], ...], int] = {}
        for i, m in enumerate(workspace.marks):
            if m.kind in _DECISION_KINDS and m.region_key in contested:
                latest_on_region[m.region_key] = i
        # Decisions in journal order (append order = deterministic).
        for i, m in enumerate(workspace.marks):
            if m.kind not in _DECISION_KINDS or m.claim is None:
                continue
            # For a contested region, only the LATEST decision survives.
            if m.region_key in contested and latest_on_region.get(m.region_key) != i:
                continue
            keys = [
                (r.page_num, r.node_path)
                for r in (*m.claim.targets, *m.claim.absorbed)
            ]
            if any(k in owned for k in keys):
                continue
            claims.append(m.claim)
            for k in keys:
                owned.add(k)

        # Residue: any region still live (no decision) → deterministic fallback,
        # typed context_exhausted. Compute the whole-doc fallback ONCE and pick the
        # claims whose targets fall in a still-live, un-owned region.
        exhausted: List[Tuple[Tuple[int, Tuple[int, ...]], ...]] = []
        live = _live_regions(workspace)
        if live:
            fallback = _deterministic_fallback_ledger(simulacra)
            live_node_keys = {
                (r.page_num, r.node_path) for s in live for r in s.region
            }
            for claim in fallback.claims:
                keys = [
                    (r.page_num, r.node_path)
                    for r in (*claim.targets, *claim.absorbed)
                ]
                if any(k in owned for k in keys):
                    continue
                if not any(k in live_node_keys for k in keys):
                    continue
                claims.append(
                    replace(
                        claim,
                        method="context_exhausted",
                        rationale="context_exhausted: deterministic fallback for undecided region",
                    )
                )
                for k in keys:
                    owned.add(k)
            for s in live:
                exhausted.append(s.region_key)
        return DeFacsimileLedger(claims=tuple(claims)), exhausted


# --------------------------------------------------------------------------- #
# The seam adjudicator as a knowledge source (§7.3, the FIRST landed source).  #
# --------------------------------------------------------------------------- #


class SeamAdjudicatorSource(KnowledgeSource):
    """Wrap the (blackboard-aware) ``DeFacsimileAdjudicator`` as a knowledge source.

    Scheduled on a live seam/candidate region, it adjudicates the 2-page window
    containing the region, reads the region's marks/notes, and POSTs one DECISION
    mark per resolved claim + affordance control lines the model emitted. It keeps
    the adjudicator's conservative honor-only-with-evidence discipline unchanged —
    a DROP is honored only against a deterministic chrome witness.
    """

    source_id = "seam_adjudicator"

    def __init__(self, adjudicator: object) -> None:
        self._adj = adjudicator

    def _window_for(
        self, region: Tuple[SpanRef, ...], simulacra: Sequence[PageSimulacrum]
    ) -> List[PageSimulacrum]:
        pages_in_region = sorted({r.page_num for r in region})
        by_num = {p.page_num: p for p in simulacra}
        win: List[PageSimulacrum] = []
        for n in pages_in_region:
            if n in by_num and by_num[n] not in win:
                win.append(by_num[n])
            # include the following page so a seam is a full 2-page window
            if (n + 1) in by_num and by_num[n + 1] not in win:
                win.append(by_num[n + 1])
        if not win:
            win = list(simulacra)
        win.sort(key=lambda p: p.page_num)
        return win

    def run(
        self,
        workspace: Workspace,
        region: Tuple[SpanRef, ...],
        simulacra: Sequence[PageSimulacrum],
    ) -> SourceOutput:
        window = self._window_for(region, simulacra)
        # The adjudicator's blackboard-aware entry returns (claims, affordances).
        result = self._adj.adjudicate_region(  # ty: ignore[unresolved-attribute]
            window, region, workspace.marks_for_region(_region_key(region)), workspace.notes_for(region)
        )
        marks: List[Mark] = []
        for claim in result.claims:
            marks.append(
                Mark(
                    kind=_OP_TO_DECISION[claim.op],
                    region=(*claim.targets, *claim.absorbed),
                    producer_id=self.source_id,
                    round=workspace.round,
                    evidence_refs=claim.targets,
                    rationale=claim.rationale,
                    claim=claim,
                )
            )
        return SourceOutput(marks=tuple(marks), affordances=tuple(result.affordances))


def _region_key(
    region: Tuple[SpanRef, ...]
) -> Tuple[Tuple[int, Tuple[int, ...]], ...]:
    return tuple(sorted((r.page_num, r.node_path) for r in region))


# --------------------------------------------------------------------------- #
# Public entry — the blackboard-mode de-facsimile (§7, additive to M1).        #
# --------------------------------------------------------------------------- #


def defacsimile_blackboard(
    simulacra: Sequence[PageSimulacrum],
    root_anchor: SourceAnchor,
    *,
    adjudicator: object = None,
    budget: Optional[BlackboardBudget] = None,
    manifestation: object = None,
    crop_fn: Optional[CropFn] = None,
) -> Tuple[DeFacsimiledDocument, Workspace]:
    """Level 2 via the stigmergic blackboard (M3) — additive to the M1 single pass.

    Pre-seeds the workspace deterministically from the §3 metadata, then runs the
    controller to a fixpoint (or budget exhaustion → typed ``context_exhausted``
    residue). The promoted ledger is ALWAYS gated by ``verify_ledger`` before the
    document is returned; a failed model ledger degrades to the deterministic
    ``compose_pages`` fallback (a record is NEVER emitted with an unverified
    ledger). Returns the document AND the workspace journal (persist the latter).
    """
    workspace = preseed_workspace(simulacra)

    sources: List[KnowledgeSource] = []
    if adjudicator is not None and getattr(adjudicator, "is_available", lambda: True)():
        sources.append(SeamAdjudicatorSource(adjudicator))

    ledger: Optional[DeFacsimileLedger] = None
    if sources:
        controller = BlackboardController(
            sources,
            budget=budget,
            manifestation=manifestation,
            crop_fn=crop_fn,
        )
        result = controller.run(simulacra, workspace)
        candidate = result.ledger
        reduced = apply_ledger(simulacra, candidate, root_anchor)
        if not verify_ledger(simulacra, candidate, reduced):
            ledger = candidate

    if ledger is None:
        # No adjudicator, or the promoted ledger failed the gate: fall back to the
        # deterministic compose_pages ledger (typed, verified — never a silent drop).
        ledger = _deterministic_fallback_ledger(simulacra)

    reduced = apply_ledger(simulacra, ledger, root_anchor)
    violations = verify_ledger(simulacra, ledger, reduced)
    if violations:
        from lawvm.ingest.defacsimile import LedgerVerificationError

        raise LedgerVerificationError(
            "blackboard de-facsimile ledger failed verification even under "
            f"deterministic fallback: {violations}"
        )
    return (
        DeFacsimiledDocument(root=reduced, page_count=len(simulacra), ledger=ledger),
        workspace,
    )


# --------------------------------------------------------------------------- #
# Workspace serialization (§7 / Decision-5 mirror) — sorted-keys, content-addr. #
# --------------------------------------------------------------------------- #


def _spanref_json(ref: SpanRef) -> Dict[str, object]:
    return {"page_num": ref.page_num, "node_path": list(ref.node_path)}


def _claim_json(claim: DeFacsimileClaim) -> Dict[str, object]:
    return {
        "op": claim.op.value,
        "targets": [_spanref_json(t) for t in claim.targets],
        "tier": claim.tier.value,
        "corroborating_producers": list(claim.corroborating_producers),
        "absorbed": [_spanref_json(a) for a in claim.absorbed],
        "method": claim.method,
        "rationale": claim.rationale,
    }


def _mark_json(mark: Mark) -> Dict[str, object]:
    return {
        "kind": mark.kind.value,
        "region": [_spanref_json(r) for r in mark.region],
        "producer_id": mark.producer_id,
        "round": mark.round,
        "evidence_refs": [_spanref_json(e) for e in mark.evidence_refs],
        "rationale": mark.rationale,
        "claim": _claim_json(mark.claim) if mark.claim is not None else None,
    }


def serialize_workspace(ws: Workspace) -> bytes:
    """Workspace → deterministic sorted-keys JSON bytes (content-addressable).

    The journal (marks, in append order) + the notes scratch. Same simulacra ⇒
    byte-identical bytes ⇒ byte-identical digest — the determinism firewall.
    """
    notes_out = [
        {"region": [list(k) for k in region_key], "notes": list(texts)}
        for region_key, texts in sorted(ws.notes.items())
    ]
    payload = {
        "marks": [_mark_json(m) for m in ws.marks],
        "notes": notes_out,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def deserialize_workspace(data: bytes) -> Workspace:
    """Round-trip a serialized workspace blob back to a ``Workspace``."""
    from lawvm.core.source_document.ir import AssuranceTier as _Tier

    from typing import Any

    payload: Any = json.loads(data.decode("utf-8"))

    def _ref(d: Any) -> SpanRef:
        return SpanRef(page_num=int(d["page_num"]), node_path=tuple(d["node_path"]))

    def _claim(d: Any) -> DeFacsimileClaim:
        return DeFacsimileClaim(
            op=DeFacsimileOp(d["op"]),
            targets=tuple(_ref(t) for t in d["targets"]),
            tier=_Tier(d["tier"]),
            corroborating_producers=tuple(d["corroborating_producers"]),
            absorbed=tuple(_ref(a) for a in d.get("absorbed", ())),
            method=str(d.get("method", "model_adjudicated")),
            rationale=str(d.get("rationale", "")),
        )

    marks: List[Mark] = []
    for md in payload.get("marks", ()):
        marks.append(
            Mark(
                kind=MarkKind(md["kind"]),
                region=tuple(_ref(r) for r in md["region"]),
                producer_id=str(md["producer_id"]),
                round=int(md["round"]),
                evidence_refs=tuple(_ref(e) for e in md.get("evidence_refs", ())),
                rationale=str(md.get("rationale", "")),
                claim=_claim(md["claim"]) if md.get("claim") is not None else None,
            )
        )
    ws = Workspace(marks=marks)
    for entry in payload.get("notes", ()):
        # Each region-key element is [page_num, [child, ...]] → (page_num, (child,...)).
        region_key = tuple(
            (int(k[0]), tuple(k[1])) for k in entry["region"]
        )
        ws.notes[region_key] = list(entry["notes"])  # type: ignore[index]
    return ws
