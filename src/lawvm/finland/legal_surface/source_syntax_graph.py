"""The ``SourceSyntaxGraph`` object + per-provision forest assembler — L1 of the
Layer-2 / SourceSyntaxGraph north star.

Authoritative blueprint: ``notes_internal/pro_on_fi_theory_grammar6.txt``
(§"The real target object" / "Minimal shape" — the SyntaxNode kinds, edge kinds,
``parse_status``, residuals, ``SyntaxCoverage``) and ``…grammar7.txt`` (the
annotation-witness stance: ``<ref>`` is a fallible WITNESS, never authority; the
forest is assembled from the deterministic parsers' OWN output, it never consumes
an annotation as truth).

Position in the stack (grammar6 §"Suggested stack")::

    TokenTape + MorphOverlay
      → SegmentationGraph        (structural line shapes: heading/chapeau/list_item/…)
      → SourceSyntaxGraph        (THIS module — typed construction parse FOREST)
      → LegalSurfaceGraph        (the public surface-analysis product — UNCHANGED)
      → lints / viewer / research outputs

This is a NEW layer that sits BELOW :class:`~lawvm.core.legal_surface_graph.LegalSurfaceGraph`.
NOTHING currently consuming the lenses changes. Per the L1 scope discipline this
lane builds the OBJECT + ASSEMBLER + COVERAGE only; it does NOT migrate or replace
any existing lens or production path (that is L3+). Lens migration (references,
definitions, temporal/modal, conditions/exceptions become *projections* of this
forest) and list-item inheritance into the surface graph are deliberately deferred.

What the forest IS
==================
A FAITHFUL ASSEMBLY of existing outputs, not a re-implementation of any grammar:

  * the **structural skeleton** comes from
    :func:`…clause_segment.build_segmentation_graph` — one ``SyntaxNode`` per
    structural segment (heading / chapeau / list_item / quoted_amendment_block /
    continuation / prose / residual), with ``contains`` / ``inherits_chapeau`` /
    ``continues_clause`` edges from the segment ``parent_index`` links;
  * the **construction leaves** come from running the SIX family parsers over each
    sentence (citation / definition / temporal / modal / condition_exception /
    delegation) via the SAME cross-family union the L0 census computes
    (:func:`…union_ownership_census.union_over_sentence`) — one ``SyntaxNode`` per
    contiguous family-owned span, ``contains``-edged under the structural segment
    it sits in. Where families OVERLAP on a span the node records MULTI-FAMILY
    ownership (the spec's coordination/ambiguity handling) rather than picking one,
    and the overlapping leaves are joined by a ``coordinates_with`` edge;
  * an explicit **residual** node per unowned-but-cheap-signal span (the
    no-silent-drop witness, self-evidencing — it embeds the offending span text);
  * a :class:`SyntaxCoverage` that IS the L0 union-ownership partition for this
    provision — computed by REUSING :func:`…union_ownership_census.classify_body`,
    so the forest's coverage equals the ruler's numbers BY CONSTRUCTION.

THE KILLER INVARIANT (grammar6 §"What 'full' should mean"): ``no silent drop``,
NOT ``no residue``. A provision whose cheap-signal spans are all owned is
``parsed``; one with an explicit typed residual is ``partial_with_residuals``; one
with an unowned cheap-signal span is ALSO ``partial_with_residuals`` and that span
is surfaced as an explicit residual node — it is never dropped. ``LAWVM_PARSE_TOTALITY``,
when set, promotes a silent-unowned span from a surfaced residual to a hard
``unsupported`` status (the strict no-silent-drop gate).

FIREWALL (grammar6 §"keep syntax surface-only"; legal_surface_assembler §D7): this
module imports NOTHING from ``apply`` / ``replay``. The forest is surface-only and
authorizes no replay — it is a SOURCE-syntax structure, not legal meaning and not
an executable plan. The firewall is asserted by a test.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from lawvm.core.legal_surface_graph import SourceUnitRef, SurfaceGraphSubject
from lawvm.core.legal_surface_tokens import SegmentationGraph
from lawvm.finland.legal_surface.clause_segment import (
    build_clause_index,
    build_segmentation_graph,
)
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.legal_surface.union_ownership_census import (
    classify_body,
    union_over_sentence,
)

# ---------------------------------------------------------------------------
# Closed taxonomies (grammar6 §"Minimal shape").
# ---------------------------------------------------------------------------

#: Closed set of syntax-node kinds. Two provenances:
#:   * STRUCTURAL kinds (from the SegmentationGraph) — the paragraph/list skeleton;
#:   * CONSTRUCTION kinds (from the six family parsers) — the typed leaves;
#:   * the explicit ``residual_span`` (the no-silent-drop witness).
#: The construction kinds map 1:1 to the family parsers' ``kind`` values, lifted to
#: grammar6's construction names. Adding a kind is a deliberate edit here.
SYNTAX_NODE_KINDS: frozenset[str] = frozenset(
    {
        # structural (SegmentationGraph segment kinds, grammar6 §"Phase A")
        "heading",
        "chapeau",
        "list_item",
        "quoted_amendment_block",
        "continuation",
        "prose",
        # construction leaves (one per family; grammar6 §"The real target object")
        "reference_np",  # citation family  (kind="citation_bearing")
        "definition_entry",  # definition family
        "temporal_phrase",  # temporal family
        "modal_predicate",  # modal family
        "condition_clause",  # condition_exception family (condition shape)
        "exception_clause",  # condition_exception family (exception shape)
        "delegation_frame",  # delegation family
        # the no-silent-drop witness
        "residual_span",
    }
)

#: The six family ids → the construction node kind each projects to. A family that
#: produces both a condition and an exception is split by the parse's own ``kind``
#: tag (handled in the assembler); the default here is the condition kind.
_FAMILY_TO_NODE_KIND: dict[str, str] = {
    "citation": "reference_np",
    "definition": "definition_entry",
    "temporal": "temporal_phrase",
    "modal": "modal_predicate",
    "condition_exception": "condition_clause",
    "delegation": "delegation_frame",
}

#: Closed set of syntax-edge kinds (grammar6 §"Minimal shape" → Edges). Only the
#: subset this assembler actually emits is documented inline; the rest are reserved
#: for L3+ projections (has_subject/has_predicate/has_object/has_condition/…), kept
#: in the closed vocabulary so a later lane adds an edge, never a vocabulary.
SYNTAX_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "contains",  # structural parent → child (segment → segment / segment → leaf)
        "inherits_chapeau",  # list_item → its governing chapeau (list inheritance)
        "continues_clause",  # continuation → the segment it continues
        "coordinates_with",  # two construction leaves overlapping on a span
        "has_subject",  # RESERVED for L3+ (modal frame → actor NP)
        "has_predicate",  # RESERVED for L3+
        "has_object",  # RESERVED for L3+
        "has_modifier",  # RESERVED for L3+
        "has_condition",  # RESERVED for L3+ (norm → condition_clause)
        "has_exception",  # RESERVED for L3+ (norm → exception_clause)
        "anaphora_resolves_to",  # RESERVED for L3+
        "defines_term_surface",  # RESERVED for L3+ (definition_entry → term symbol)
        "contains_reference_surface",  # RESERVED for L3+ (clause → reference_np)
    }
)

#: Per-node status (grammar6 SyntaxNode.status). A construction leaf the family
#: owned is ``parsed``; a structural segment is ``parsed``; a residual span is
#: ``open`` (surfaced, awaiting a grammar that owns it) or ``unsupported`` (under
#: ``LAWVM_PARSE_TOTALITY`` a silent-unowned cheap signal is a hard failure).
SYNTAX_NODE_STATUSES: frozenset[str] = frozenset(
    {"parsed", "ambiguous", "open", "unsupported"}
)

#: Per-provision parse status (grammar6 SourceSyntaxGraph.parse_status).
ParseStatus = Literal[
    "parsed",
    "partial_with_residuals",
    "ambiguous",
    "unsupported",
]


# ---------------------------------------------------------------------------
# The object (grammar6 §"Minimal shape").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyntaxNode:
    """One typed construction (or structural segment, or residual) in the forest.

    Source-anchored: ``[char_start, char_end)`` is an EXACT span into the provision
    body text, so a consumer always recovers the verbatim substring. Surface-only:
    a syntax node is NEVER a legal conclusion (grammar6 §"syntax parse vs surface
    algebra") and never authorizes replay.

    Attributes:
        node_id:    Stable id, ``sha256`` over (graph subject ∥ kind ∥ span ∥
                    discriminator); see :func:`_mint_node_id`.
        kind:       A member of :data:`SYNTAX_NODE_KINDS`.
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        status:     A member of :data:`SYNTAX_NODE_STATUSES`.
        families:   For a construction leaf: the family ids that own this span
                    (>=2 ⇒ multi-family coordination). ``()`` for structural /
                    residual nodes.
        residual_reason: Non-empty self-evidencing reason iff ``kind ==
                    'residual_span'`` (it names what was left unowned), ``""``
                    otherwise. The no-silent-drop witness.
        residual_text:   Verbatim offending span text iff ``kind ==
                    'residual_span'`` (self-evidencing — never an opaque count),
                    ``""`` otherwise.
    """

    node_id: str
    kind: str
    char_start: int
    char_end: int
    status: str
    families: tuple[str, ...] = ()
    residual_reason: str = ""
    residual_text: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SYNTAX_NODE_KINDS:
            raise ValueError(f"unknown syntax node kind: {self.kind!r}")
        if self.status not in SYNTAX_NODE_STATUSES:
            raise ValueError(f"unknown syntax node status: {self.status!r}")
        if self.char_start < 0:
            raise ValueError("SyntaxNode.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("SyntaxNode.char_end must be >= char_start")
        if self.kind == "residual_span":
            if not self.residual_reason:
                raise ValueError(
                    "a residual_span SyntaxNode MUST carry a non-empty "
                    "residual_reason (no silent drop: every residual names what "
                    "was left unowned)"
                )
            if not self.residual_text:
                raise ValueError(
                    "a residual_span SyntaxNode MUST carry its verbatim "
                    "residual_text (self-evidencing residue, not an opaque count)"
                )
        else:
            if self.residual_reason or self.residual_text:
                raise ValueError(
                    "residual_reason/residual_text are only for kind=='residual_span'"
                )


@dataclass(frozen=True, slots=True)
class SyntaxEdge:
    """A directed edge between two :class:`SyntaxNode`s (grammar6 Edges).

    Attributes:
        kind: A member of :data:`SYNTAX_EDGE_KINDS`.
        src:  ``node_id`` of the source node.
        dst:  ``node_id`` of the destination node.
    """

    kind: str
    src: str
    dst: str

    def __post_init__(self) -> None:
        if self.kind not in SYNTAX_EDGE_KINDS:
            raise ValueError(f"unknown syntax edge kind: {self.kind!r}")


@dataclass(frozen=True, slots=True)
class SyntaxResidual:
    """An explicit unowned-but-cheap-signal span surfaced by the forest.

    Mirrors the L0 census's :class:`UnownedSignalSpan`: self-evidencing (carries
    the verbatim offending span text + a context window), so the no-silent-drop
    invariant is auditable.

    Attributes:
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        shape:      The cheap-legal-signal SHAPE (the grammar-growth rank key).
        text:       Verbatim offending span text.
        context:    A short verbatim window around the span.
    """

    char_start: int
    char_end: int
    shape: str
    text: str
    context: str


@dataclass(frozen=True, slots=True)
class SyntaxCoverage:
    """The L0 union-ownership partition for ONE provision (grammar6 SyntaxCoverage).

    These four buckets are exactly the L0 cross-family union token-ownership
    partition (:mod:`…union_ownership_census`) restricted to this provision; the
    forest carries them so a consumer reads coverage off the forest WITHOUT re-running
    the census. ``forest_coverage == L0 numbers on the same provision`` BY
    CONSTRUCTION because they are computed by the SAME :func:`classify_body` call.

    Attributes:
        total_tokens:   Non-whitespace (signal-bearing) tokens classified.
        owned_tokens:   Tokens claimed by >=1 family construction.
        benign_tokens:  Unowned tokens with no cheap legal signal.
        residual_tokens: Unowned tokens inside an explicit typed residual span.
        silent_tokens:  Unowned, non-benign tokens carrying a cheap legal signal
                        (the no-silent-drop frontier — surfaced, never dropped).
        family_token_counts: family_id → tokens it owned (overlaps allowed).
        unowned_shape_counts: cheap-signal SHAPE → unowned-span count.
    """

    total_tokens: int
    owned_tokens: int
    benign_tokens: int
    residual_tokens: int
    silent_tokens: int
    family_token_counts: Mapping[str, int] = field(default_factory=dict)
    unowned_shape_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def partition_total(self) -> int:
        return (
            self.owned_tokens
            + self.benign_tokens
            + self.residual_tokens
            + self.silent_tokens
        )

    def is_partition(self) -> bool:
        """The four buckets sum to the classified-token total (no leak)."""
        return self.partition_total == self.total_tokens


@dataclass(frozen=True, slots=True)
class SourceSyntaxGraph:
    """A construction parse FOREST over ONE provision body (grammar6 minimal shape).

    Sits below :class:`~lawvm.core.legal_surface_graph.LegalSurfaceGraph` (which is
    unchanged). Surface-only; authorizes no replay; assembled from the deterministic
    family parsers + segmentation, never from any annotation.

    Attributes:
        graph_id:     Stable id over the subject + provision text hash.
        subject:      The surface slice this forest is built over (reused core type).
        source_units: The source units the provision body came from.
        text_hash:    sha256 of the exact provision body text (drift anchor).
        text_len:     ``len(body)`` (the span the structural nodes partition).
        syntax_nodes: ``node_id -> SyntaxNode`` (structural + construction +
                      residual).
        syntax_edges: Document-order edges (``contains`` / ``inherits_chapeau`` /
                      ``continues_clause`` / ``coordinates_with``).
        parse_status: A member of :data:`ParseStatus` for the WHOLE provision.
        residuals:    Explicit unowned-but-cheap-signal spans (self-evidencing).
        coverage:     The L0 union-ownership partition for this provision.
    """

    graph_id: str
    subject: SurfaceGraphSubject
    source_units: tuple[SourceUnitRef, ...]
    text_hash: str
    text_len: int
    syntax_nodes: Mapping[str, SyntaxNode]
    syntax_edges: tuple[SyntaxEdge, ...]
    parse_status: ParseStatus
    residuals: tuple[SyntaxResidual, ...]
    coverage: SyntaxCoverage

    def __post_init__(self) -> None:
        for nid, node in self.syntax_nodes.items():
            if node.node_id != nid:
                raise ValueError(
                    "SourceSyntaxGraph key must equal its node's node_id: "
                    f"key={nid!r} but node.node_id={node.node_id!r}"
                )
        for edge in self.syntax_edges:
            if edge.src not in self.syntax_nodes:
                raise ValueError(
                    f"SyntaxEdge src {edge.src!r} does not resolve to a node"
                )
            if edge.dst not in self.syntax_nodes:
                raise ValueError(
                    f"SyntaxEdge dst {edge.dst!r} does not resolve to a node"
                )

    def nodes_of_kind(self, kind: str) -> tuple[SyntaxNode, ...]:
        """All nodes of one kind, in document (span) order."""
        return tuple(
            sorted(
                (n for n in self.syntax_nodes.values() if n.kind == kind),
                key=lambda n: (n.char_start, n.char_end),
            )
        )

    def edges_of_kind(self, kind: str) -> tuple[SyntaxEdge, ...]:
        return tuple(e for e in self.syntax_edges if e.kind == kind)


# ---------------------------------------------------------------------------
# Identity minting (stable, order-independent, surface-only).
# ---------------------------------------------------------------------------

_GRAPH_SCHEMA_TAG = "lawvm.fi.source_syntax_graph.v0"
_NODE_SCHEMA_TAG = "lawvm.fi.source_syntax_node.v0"


def _sha256(parts: tuple[str, ...]) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mint_node_id(
    *, text_hash: str, kind: str, start: int, end: int, discriminator: str
) -> str:
    return _sha256(
        (_NODE_SCHEMA_TAG, text_hash, kind, str(start), str(end), discriminator)
    )


# ---------------------------------------------------------------------------
# Construction-leaf extraction (the family union → leaves).
# ---------------------------------------------------------------------------


def _family_owned_spans(parse_obj: object, *, off: int) -> list[tuple[int, int, str]]:
    """The body-coordinate spans one family parse claims, with the node kind.

    Owned = ``[seg_start, seg_end)`` minus the parse's residual spans. The node
    kind is the family's construction kind, refined to exception vs condition for
    the condition_exception family by the parse's own ``kind`` tag. Returns
    ``(body_start, body_end, node_kind)`` per contiguous owned run.
    """
    seg_start = getattr(parse_obj, "seg_start", 0)
    seg_end = getattr(parse_obj, "seg_end", 0)
    residuals = getattr(parse_obj, "residuals", ())
    blocked = sorted(
        (r.char_start, r.char_end) for r in residuals if r.char_end > r.char_start
    )
    spans: list[tuple[int, int]] = []
    cursor = seg_start
    for r_start, r_end in blocked:
        r_start = max(r_start, seg_start)
        r_end = min(r_end, seg_end)
        if r_start > cursor:
            spans.append((cursor, r_start))
        cursor = max(cursor, r_end)
    if cursor < seg_end:
        spans.append((cursor, seg_end))
    return [(off + s, off + e, "") for (s, e) in spans]


def _coalesce_runs(
    chars_to_families: dict[int, frozenset[str]],
) -> list[tuple[int, int, frozenset[str]]]:
    """Coalesce a per-char owning-family map into contiguous same-owner runs.

    Two adjacent owned chars with the SAME owning-family set merge into one run; a
    change in the family set (or a gap) starts a new run. Returns
    ``(start, end, families)`` runs in char order.
    """
    if not chars_to_families:
        return []
    runs: list[tuple[int, int, frozenset[str]]] = []
    ordered = sorted(chars_to_families)
    run_start = ordered[0]
    run_fams = chars_to_families[ordered[0]]
    prev = ordered[0]
    for i in ordered[1:]:
        if i == prev + 1 and chars_to_families[i] == run_fams:
            prev = i
            continue
        runs.append((run_start, prev + 1, run_fams))
        run_start = i
        run_fams = chars_to_families[i]
        prev = i
    runs.append((run_start, prev + 1, run_fams))
    return runs


def _leaf_kind_for_families(
    families: frozenset[str], parse_kinds: Mapping[str, str]
) -> str:
    """Pick the construction node kind for a (possibly multi-family) owned run.

    Single-family runs map by :data:`_FAMILY_TO_NODE_KIND`, with the
    condition_exception family refined to ``exception_clause`` when its parse's own
    ``kind`` tag is an exception shape. Multi-family runs keep the kind of the
    lexicographically-first family (deterministic); the multi-family ownership is
    preserved on ``SyntaxNode.families`` and recorded by a ``coordinates_with`` edge,
    so no family is silently dropped.
    """
    primary = sorted(families)[0]
    kind = _FAMILY_TO_NODE_KIND[primary]
    if primary == "condition_exception":
        # The condexc family tags each qualifier condition|exception; if THIS
        # segment's parse declared an exception shape, project exception_clause.
        if parse_kinds.get("condition_exception", "") == "exception":
            kind = "exception_clause"
    return kind


# ---------------------------------------------------------------------------
# The per-provision forest assembler.
# ---------------------------------------------------------------------------


def _structural_status() -> str:
    return "parsed"


def assemble_source_syntax_graph(
    *,
    subject: SurfaceGraphSubject,
    source_units: tuple[SourceUnitRef, ...],
    statute_id: str,
    body: str,
) -> SourceSyntaxGraph:
    """Assemble the per-provision construction parse FOREST over ``body``.

    Deterministic, surface-only ASSEMBLY (grammar6 §"What to build first"):

      1. **Structural skeleton** — run :func:`build_segmentation_graph`; emit one
         structural :class:`SyntaxNode` per segment (heading / chapeau / list_item
         / quoted_amendment_block / continuation / prose; residual whitespace
         segments are NOT materialized as nodes — they carry no signal and the
         coverage census already accounts for them). Emit ``inherits_chapeau`` for
         each list_item → its chapeau and ``continues_clause`` for each
         continuation → its parent.
      2. **Construction leaves** — segment ``body`` into sentences
         (:func:`build_clause_index`), run the SIX family parsers over each
         sentence via the SAME cross-family union the L0 census uses
         (:func:`union_over_sentence`), coalesce the per-char owning-family map into
         contiguous owned runs, and emit one construction :class:`SyntaxNode` per
         run. Multi-family runs keep both owners on ``families`` and are joined by a
         ``coordinates_with`` edge (no family dropped). Each leaf is
         ``contains``-edged under the structural segment it sits in (or skipped if
         it straddles a boundary — the leaf still exists, just unparented).
      3. **Residuals** — the L0 census's unowned-but-cheap-signal spans become
         explicit :class:`SyntaxResidual`s AND ``residual_span`` nodes (the
         no-silent-drop witness). Under ``LAWVM_PARSE_TOTALITY`` each is
         ``unsupported`` (hard gate); otherwise ``open`` (surfaced, awaiting grammar).
      4. **Coverage** — the L0 :func:`classify_body` partition for this provision,
         carried verbatim. The forest's coverage EQUALS the ruler's numbers by
         construction (same call).

    The provision ``parse_status`` is: ``parsed`` if every cheap signal is owned and
    there is no explicit typed residual; ``partial_with_residuals`` if any residual
    (typed or silent-unowned) was surfaced; ``unsupported`` under
    ``LAWVM_PARSE_TOTALITY`` when a silent-unowned cheap signal remains.

    This is a faithful ASSEMBLY of existing parser outputs — it re-implements NO
    family grammar and makes NO attachment/composition decision (those are L3+).
    """
    text_hash = _sha256_text(body)
    seg_graph = build_segmentation_graph(statute_id, body)

    nodes: dict[str, SyntaxNode] = {}
    edges: list[SyntaxEdge] = []

    # ── 1. structural skeleton ───────────────────────────────────────────────
    # segment-tuple-index → minted node_id (None for un-materialized residual gaps)
    seg_node_id: list[str | None] = _emit_structural_nodes(
        seg_graph, text_hash=text_hash, nodes=nodes, edges=edges
    )

    # ── 2. construction leaves (the family union) ────────────────────────────
    _emit_construction_leaves(
        body,
        statute_id=statute_id,
        seg_graph=seg_graph,
        seg_node_id=seg_node_id,
        text_hash=text_hash,
        nodes=nodes,
        edges=edges,
    )

    # ── 3 + 4. coverage + residuals (REUSE the L0 ruler) ─────────────────────
    bucket_counts, family_counts, unowned_shape_counts, unowned_examples, _sents = (
        classify_body(statute_id, body, max_examples_per_shape=10_000)
    )
    coverage = SyntaxCoverage(
        total_tokens=sum(bucket_counts.values()),
        owned_tokens=bucket_counts.get("owned", 0),
        benign_tokens=bucket_counts.get("benign", 0),
        residual_tokens=bucket_counts.get("residual", 0),
        silent_tokens=bucket_counts.get("silent", 0),
        family_token_counts=dict(family_counts),
        unowned_shape_counts=dict(unowned_shape_counts),
    )

    totality = bool(os.environ.get("LAWVM_PARSE_TOTALITY"))
    residuals = _emit_residual_nodes(
        body,
        unowned_examples=unowned_examples,
        text_hash=text_hash,
        totality=totality,
        nodes=nodes,
        edges=edges,
        seg_graph=seg_graph,
        seg_node_id=seg_node_id,
    )

    parse_status = _decide_parse_status(
        coverage=coverage, residual_count=len(residuals), totality=totality
    )

    graph_id = _sha256((_GRAPH_SCHEMA_TAG, subject.jurisdiction, statute_id, text_hash))
    return SourceSyntaxGraph(
        graph_id=graph_id,
        subject=subject,
        source_units=source_units,
        text_hash=text_hash,
        text_len=len(body),
        syntax_nodes=nodes,
        syntax_edges=tuple(edges),
        parse_status=parse_status,
        residuals=residuals,
        coverage=coverage,
    )


def _emit_structural_nodes(
    seg_graph: SegmentationGraph,
    *,
    text_hash: str,
    nodes: dict[str, SyntaxNode],
    edges: list[SyntaxEdge],
) -> list[str | None]:
    """Emit one SyntaxNode per non-residual segment + the structural edges.

    Returns a parallel list mapping each segment tuple-index to its minted node_id
    (``None`` for residual-whitespace segments, which are not materialized as
    nodes). ``inherits_chapeau`` / ``continues_clause`` edges come from the segment
    ``parent_index`` links; a ``contains`` edge wraps a list_item under its chapeau.
    """
    seg_node_id: list[str | None] = []
    for seg in seg_graph.segments:
        if seg.kind == "residual":
            seg_node_id.append(None)
            continue
        nid = _mint_node_id(
            text_hash=text_hash,
            kind=seg.kind,
            start=seg.char_start,
            end=seg.char_end,
            discriminator=f"seg:{seg.role}",
        )
        nodes[nid] = SyntaxNode(
            node_id=nid,
            kind=seg.kind,
            char_start=seg.char_start,
            char_end=seg.char_end,
            status=_structural_status(),
        )
        seg_node_id.append(nid)

    # structural edges from parent_index links
    for i, seg in enumerate(seg_graph.segments):
        nid = seg_node_id[i]
        if nid is None or seg.parent_index is None:
            continue
        parent_nid = seg_node_id[seg.parent_index]
        if parent_nid is None:
            continue
        if seg.kind == "list_item":
            edges.append(SyntaxEdge(kind="inherits_chapeau", src=nid, dst=parent_nid))
            edges.append(SyntaxEdge(kind="contains", src=parent_nid, dst=nid))
        elif seg.kind == "continuation":
            edges.append(SyntaxEdge(kind="continues_clause", src=nid, dst=parent_nid))
        elif seg.kind == "quoted_amendment_block":
            edges.append(SyntaxEdge(kind="contains", src=parent_nid, dst=nid))
    return seg_node_id


def _segment_index_for_span(
    seg_graph: SegmentationGraph, start: int, end: int
) -> int | None:
    """The tuple-index of the segment fully containing ``[start, end)``, or None.

    A span that straddles a segment boundary returns ``None`` (the leaf is still
    emitted, just unparented — never silently re-bucketed).
    """
    for i, seg in enumerate(seg_graph.segments):
        if seg.char_start <= start and end <= seg.char_end:
            return i
    return None


def _emit_construction_leaves(
    body: str,
    *,
    statute_id: str,
    seg_graph: SegmentationGraph,
    seg_node_id: list[str | None],
    text_hash: str,
    nodes: dict[str, SyntaxNode],
    edges: list[SyntaxEdge],
) -> None:
    """Run the six families over each sentence and emit construction-leaf nodes.

    Reuses the L0 :func:`union_over_sentence` so the owned spans are IDENTICAL to the
    census's. Coalesces the per-char owning-family map into contiguous runs; each
    run is one leaf node (multi-family runs keep all owners + a coordinates_with
    edge). The leaf is contained under its enclosing structural segment when it sits
    inside one.
    """
    tape = build_token_tape(statute_id, body)
    index = build_clause_index(statute_id, body, token_tape=tape)

    for sent in index.sentences:
        off = sent.char_start
        seg_text = body[sent.char_start : sent.char_end]
        su = union_over_sentence(seg_text)
        if not su.owners:
            continue
        # Per-family parse kind tags, so a condexc run can be refined to exception.
        parse_kinds = _sentence_parse_kinds(seg_text)
        body_owners = {off + i: fams for i, fams in su.owners.items()}
        runs = _coalesce_runs(body_owners)
        # leaf nodes per run; coordinates_with for the multi-family ones
        for start, end, families in runs:
            kind = _leaf_kind_for_families(families, parse_kinds)
            nid = _mint_node_id(
                text_hash=text_hash,
                kind=kind,
                start=start,
                end=end,
                discriminator="|".join(sorted(families)),
            )
            if nid in nodes:
                continue
            nodes[nid] = SyntaxNode(
                node_id=nid,
                kind=kind,
                char_start=start,
                char_end=end,
                status="parsed",
                families=tuple(sorted(families)),
            )
            seg_i = _segment_index_for_span(seg_graph, start, end)
            if seg_i is not None and seg_node_id[seg_i] is not None:
                parent = seg_node_id[seg_i]
                assert parent is not None  # narrowed above; for the type checker
                edges.append(SyntaxEdge(kind="contains", src=parent, dst=nid))
            if len(families) > 1:
                # Record multi-family ownership (the spec's "record multi-family
                # ownership rather than picking one"): the leaf already carries ALL
                # owners on ``families``; a self-loop ``coordinates_with`` edge is the
                # explicit, queryable coordination marker (via :meth:`edges_of_kind`).
                edges.append(SyntaxEdge(kind="coordinates_with", src=nid, dst=nid))


def _sentence_parse_kinds(seg_text: str) -> dict[str, str]:
    """The per-family parse ``kind`` tag for one sentence (for leaf-kind refinement).

    Only the condition_exception family needs its kind disambiguated (condition vs
    exception); we read it from that family's parse. Other families' kind is
    implied by the family id. Returns ``{family_id: kind}`` for the condexc family
    when its first qualifier is an exception shape.
    """
    from lawvm.finland.legal_surface.condition_exception_parse import (
        parse_condition_exception_sentence,
    )

    out: dict[str, str] = {}
    parse = parse_condition_exception_sentence(seg_text)
    qualifiers = getattr(parse, "qualifiers", ())
    for q in qualifiers:
        q_kind = getattr(q, "kind", "")
        if q_kind == "exception":
            out["condition_exception"] = "exception"
            break
    return out


def _emit_residual_nodes(
    body: str,
    *,
    unowned_examples: list,  # list[UnownedSignalSpan]
    text_hash: str,
    totality: bool,
    nodes: dict[str, SyntaxNode],
    edges: list[SyntaxEdge],
    seg_graph: SegmentationGraph,
    seg_node_id: list[str | None],
) -> tuple[SyntaxResidual, ...]:
    """Emit explicit residual_span nodes + SyntaxResiduals for unowned cheap signals.

    Each L0 unowned-signal example becomes a self-evidencing :class:`SyntaxResidual`
    AND a ``residual_span`` node (the no-silent-drop witness). Under
    ``LAWVM_PARSE_TOTALITY`` the node status is ``unsupported`` (hard gate); else
    ``open``. The residual is contained under its enclosing structural segment when
    it sits inside one. The span offsets are recovered from the verbatim text by
    locating it within its context window in ``body``.
    """
    residuals: list[SyntaxResidual] = []
    status = "unsupported" if totality else "open"
    for ex in unowned_examples:
        span = _locate_span(body, ex.text)
        if span is None:
            continue
        start, end = span
        nid = _mint_node_id(
            text_hash=text_hash,
            kind="residual_span",
            start=start,
            end=end,
            discriminator=f"unowned:{ex.shape}",
        )
        residuals.append(
            SyntaxResidual(
                char_start=start,
                char_end=end,
                shape=ex.shape,
                text=ex.text,
                context=ex.context,
            )
        )
        if nid in nodes:
            continue
        nodes[nid] = SyntaxNode(
            node_id=nid,
            kind="residual_span",
            char_start=start,
            char_end=end,
            status=status,
            residual_reason=f"unowned_cheap_signal:{ex.shape}",
            residual_text=ex.text,
        )
        seg_i = _segment_index_for_span(seg_graph, start, end)
        if seg_i is not None and seg_node_id[seg_i] is not None:
            parent = seg_node_id[seg_i]
            assert parent is not None
            edges.append(SyntaxEdge(kind="contains", src=parent, dst=nid))
    return tuple(residuals)


def _locate_span(body: str, text: str) -> tuple[int, int] | None:
    """First occurrence of ``text`` in ``body`` as ``(start, end)``, or None.

    The L0 example carries the verbatim span text; the first occurrence is a safe
    anchor for a single-provision body (the census examples are de-duplicated per
    shape, and the residual node id includes the shape so collisions on identical
    spans coalesce rather than fabricate a wrong offset).
    """
    if not text:
        return None
    pos = body.find(text)
    if pos < 0:
        return None
    return (pos, pos + len(text))


def _decide_parse_status(
    *, coverage: SyntaxCoverage, residual_count: int, totality: bool
) -> ParseStatus:
    """The whole-provision parse_status (grammar6 SourceSyntaxGraph.parse_status).

    ``unsupported`` under totality when a silent-unowned cheap signal remains (the
    hard no-silent-drop gate); ``partial_with_residuals`` when any residual (typed
    or silent) was surfaced OR a silent-unowned span exists; ``parsed`` when every
    cheap signal is owned and no typed residual was surfaced.
    """
    if totality and coverage.silent_tokens:
        return "unsupported"
    if residual_count or coverage.silent_tokens or coverage.residual_tokens:
        return "partial_with_residuals"
    return "parsed"
