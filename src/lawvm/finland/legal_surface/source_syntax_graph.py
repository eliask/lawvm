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
  * the **construction leaves** come from running the six family parsers
    (citation / definition / temporal / modal / condition_exception / delegation)
    AND the four BODY-TEXT reference recognizers (internal bare-§ / by-name /
    treaty / EU-by-nickname) over each sentence via the SAME cross-family union
    the L0 census computes
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
import json
import os
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from lawvm.core.legal_surface_graph import SourceUnitRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import SourceSurfaceUnit
from lawvm.core.legal_surface_tokens import ClauseIndex, SegmentationGraph, TokenTape
from lawvm.core.stage_result import (
    EMPTY_EVIDENCE,
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    Residual,
    StageResult,
)
from lawvm.finland.legal_surface.clause_segment import (
    build_clause_index,
    build_segmentation_graph,
)
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.legal_surface.union_ownership_census import (
    SentenceUnionAnalysis,
    UnownedSignalSpan,
    analyze_body_union,
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
        "reference_np",  # citation family + body-text reference recognizers
        #                  (ref_internal / ref_by_name / ref_treaty / ref_eu)
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

#: The family ids → the construction node kind each projects to. The six
#: FAMILY_PARSERS families plus the four BODY-TEXT reference recognizer families
#: (``ref_internal`` / ``ref_by_name`` / ``ref_treaty`` / ``ref_eu``), which all
#: project to ``reference_np`` exactly like the citation family — they own the
#: bare-§ / kohta / momentti / by-name / treaty / EU body-text references the
#: citation family does not. A family that produces both a condition and an
#: exception is split by the parse's own ``kind`` tag (handled in the assembler);
#: the default here is the condition kind.
_FAMILY_TO_NODE_KIND: dict[str, str] = {
    "citation": "reference_np",
    "definition": "definition_entry",
    "temporal": "temporal_phrase",
    "modal": "modal_predicate",
    "condition_exception": "condition_clause",
    "delegation": "delegation_frame",
    # body-text reference recognizers (union_ownership_census.REFERENCE_RECOGNIZERS)
    "ref_internal": "reference_np",  # bare-§ / kohta / momentti self-references
    "ref_by_name": "reference_np",  # cross-statute by inflected name head
    "ref_treaty": "reference_np",  # SopS NNN/YYYY treaty-series cites
    "ref_eu": "reference_np",  # EU instrument-by-nickname + N artikla
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

#: Per-node status (grammar6 SyntaxNode.node_status). A construction leaf the family
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
        node_status: A member of :data:`SYNTAX_NODE_STATUSES`.
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
    node_status: str
    families: tuple[str, ...] = ()
    residual_reason: str = ""
    residual_text: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SYNTAX_NODE_KINDS:
            raise ValueError(f"unknown syntax node kind: {self.kind!r}")
        if self.node_status not in SYNTAX_NODE_STATUSES:
            raise ValueError(f"unknown syntax node status: {self.node_status!r}")
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
class ListConstruction:
    """A chapeau + its governed list items, with the chapeau's inherited frame.

    grammar6 §"list inheritance": a Finnish provision is often ``chapeau (sets the
    actor/modal/deontic frame) + ":" + numbered/lettered items (each a
    condition/sub-norm that INHERITS the chapeau's frame)``. This is the typed
    grouping the forest carries for ONE such construction: the chapeau structural
    node, the chapeau's GOVERNING FRAME leaf (the ``modal_predicate`` construction
    leaf sitting inside the chapeau span — the actor + necessity/permission the
    items inherit), and the governed ``list_item`` structural nodes.

    The construction is STRUCTURAL (the item membership comes from the
    SegmentationGraph ``parent_index`` links, never guessed) and the frame is taken
    from the chapeau's own parseable modal leaf — NEVER fabricated. When the chapeau
    has NO parseable modal frame, ``frame_node_id`` is ``""`` and the items are left
    frame-unattached (``frame_status == "unsupported"``): the construction is still
    recorded (the items still ``inherits_chapeau`` structurally) but NO frame edge
    is emitted, so no inheritance is invented (fail-loud).

    Attributes:
        chapeau_id:    ``node_id`` of the governing chapeau structural node.
        frame_node_id: ``node_id`` of the chapeau's governing ``modal_predicate``
                       leaf (the inherited actor/modal frame), or ``""`` when the
                       chapeau carries no parseable modal frame.
        item_ids:      ``node_id``s of the governed ``list_item`` nodes, in span
                       order.
        frame_status:  ``"inherited"`` when a frame leaf was found and bound onto
                       the items; ``"unsupported"`` when the chapeau has no
                       parseable frame (items left unattached — no fabrication).
    """

    chapeau_id: str
    frame_node_id: str
    item_ids: tuple[str, ...]
    frame_status: Literal["inherited", "unsupported"]

    @property
    def is_inherited(self) -> bool:
        return self.frame_status == "inherited"


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

    # ── Pro D2 four-class vocabulary (additive aliases) ──────────────────────
    # Pro ruling D2 renames the partition classes so that the fourth class is a
    # FAILURE state, not an accepted bucket. The storage fields keep their L0
    # names (``benign_tokens`` / ``residual_tokens`` / ``silent_tokens``) so no
    # existing consumer breaks; these accessors expose the Pro-named view.
    #   benign_tokens   -> benign_uninterpreted_tokens
    #   residual_tokens -> typed_residual_tokens
    #   silent_tokens   -> unowned_violation_tokens  (a VIOLATION, drive to 0)

    @property
    def benign_uninterpreted_tokens(self) -> int:
        """Pro D2 alias of ``benign_tokens`` (unowned, no cheap legal signal)."""
        return self.benign_tokens

    @property
    def typed_residual_tokens(self) -> int:
        """Pro D2 alias of ``residual_tokens`` (unowned, inside a typed residual)."""
        return self.residual_tokens

    @property
    def unowned_violation_tokens(self) -> int:
        """Pro D2 name for ``silent_tokens``.

        Semantically a VIOLATION (an unowned cheap-legal-signal token the total
        parse did not account for), NOT an accepted bucket. The target invariant
        is ``unowned_violation_tokens == 0``; a non-zero value is the honest
        violation surface to drive down, never a steady state.
        """
        return self.silent_tokens


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
        list_constructions: The chapeau+items :class:`ListConstruction` groupings,
                      each carrying the chapeau's inherited frame (or marked
                      ``unsupported`` when the chapeau has no parseable frame). The
                      ``inherits_chapeau`` / ``has_subject`` / ``has_condition``
                      edges that bind the frame onto the items are in
                      ``syntax_edges``; this is the queryable typed VIEW.
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
    list_constructions: tuple[ListConstruction, ...] = ()

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


# ---------------------------------------------------------------------------
# Per-process forest cache (pure-perf reuse, output-identical).
# ---------------------------------------------------------------------------
#
# ``assemble_source_syntax_graph`` is a PURE, DETERMINISTIC function of its inputs
# — ``(subject, source_units, statute_id, body)`` plus the ``LAWVM_PARSE_TOTALITY``
# env flag (which only flips residual node STATUS open↔unsupported, never spans).
# The body is the sole text input (every parser/segmenter/census reads only
# ``body`` + ``statute_id``), and the result is a frozen, immutable dataclass.
#
# Multiple consumers (the L6 ForestStructuralAttachmentPass, the four
# ``*_projection`` lenses, census/differential harnesses) assemble the SAME unit's
# forest independently and from scratch — each re-running segmentation, the token
# tape, the six family parsers over every sentence, and the L0 census. That is the
# dominant cost inside graph build (~minutes on broad shards). Since the forest is
# a pure function of the inputs, we memoize on a deterministic key over ALL inputs
# that affect the output, and return the SAME cached object while it remains in
# the working set (safe: it is frozen).
#
# The cache is bounded because each value is a full SourceSyntaxGraph. A broad
# corpus worker can see many distinct provisions, and retaining all of them would
# turn a pure performance cache into process-lifetime memory growth. LRU eviction
# is output-identical: an evicted forest is simply rebuilt from the same inputs.
_FOREST_CACHE_DEFAULT_CAP = 4096
_FOREST_CACHE_CAP = _FOREST_CACHE_DEFAULT_CAP
_FOREST_CACHE: OrderedDict[str, SourceSyntaxGraph] = OrderedDict()


def _stable_subject_key(subject: SurfaceGraphSubject) -> str:
    """A deterministic key fragment for a (possibly dict-scoped) subject.

    ``SurfaceGraphSubject.scope`` is a ``Mapping`` (not hashable), so the subject is
    serialised via ``json.dumps(..., sort_keys=True)`` for an order-independent,
    deterministic key. The subject is stored VERBATIM on the result, so it MUST be
    part of the cache key (two calls with the same body but a different subject must
    not share a cached forest).
    """
    return json.dumps(
        {
            "jurisdiction": subject.jurisdiction,
            "work_id": subject.work_id,
            "scope": subject.scope,
            "surface_time": subject.surface_time,
            "source_bundle_hash": subject.source_bundle_hash,
            "language": subject.language,
        },
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )


def _forest_cache_key(
    *,
    subject: SurfaceGraphSubject,
    source_units: tuple[SourceUnitRef, ...],
    statute_id: str,
    body: str,
) -> str:
    """A deterministic cache key over EVERY input that affects the forest output.

    Folds in the subject (stored verbatim), the source_units (stored verbatim), the
    statute_id, the exact body text hash, and the ``LAWVM_PARSE_TOTALITY`` flag (the
    only env input, which flips residual status). Any input difference yields a
    distinct key, so a cache hit is value-identical to a fresh assembly.
    """
    totality = "1" if os.environ.get("LAWVM_PARSE_TOTALITY") else "0"
    parts = (
        _GRAPH_SCHEMA_TAG,
        _stable_subject_key(subject),
        repr(source_units),
        statute_id,
        _sha256_text(body),
        f"totality={totality}",
    )
    return _sha256(parts)


def clear_forest_cache() -> None:
    """Clear the per-process forest cache (tests that toggle env flags use this)."""
    _FOREST_CACHE.clear()


def assemble_source_syntax_graph(
    *,
    subject: SurfaceGraphSubject,
    source_units: tuple[SourceUnitRef, ...],
    statute_id: str,
    body: str,
    segmentation_graph: SegmentationGraph | None = None,
    token_tape: TokenTape | None = None,
    clause_index: ClauseIndex | None = None,
) -> SourceSyntaxGraph:
    """Assemble (or REUSE a cached) per-provision construction parse FOREST.

    Memoizing wrapper over :func:`_assemble_source_syntax_graph` keyed on a
    deterministic hash of ALL inputs that affect the output (subject, source_units,
    statute_id, body, ``LAWVM_PARSE_TOTALITY``). The forest is a pure function of
    these, so a cache hit returns the SAME frozen, immutable object — value-identical
    to a fresh assembly (same nodes, edges, coverage, status). This makes the forest
    assembled ONCE per unit per process; all consumers reuse it.
    """
    key = _forest_cache_key(
        subject=subject,
        source_units=source_units,
        statute_id=statute_id,
        body=body,
    )
    cached = _FOREST_CACHE.get(key)
    if cached is not None:
        _FOREST_CACHE.move_to_end(key)
        return cached
    forest = _assemble_source_syntax_graph(
        subject=subject,
        source_units=source_units,
        statute_id=statute_id,
        body=body,
        segmentation_graph=segmentation_graph,
        token_tape=token_tape,
        clause_index=clause_index,
    )
    _FOREST_CACHE[key] = forest
    if len(_FOREST_CACHE) > _FOREST_CACHE_CAP:
        _FOREST_CACHE.popitem(last=False)
    return forest


def assemble_source_syntax_graph_for_unit(
    *,
    subject: SurfaceGraphSubject,
    unit: SourceSurfaceUnit,
    source_units: tuple[SourceUnitRef, ...] = (),
) -> SourceSyntaxGraph:
    """Assemble a forest for a bundle unit, reusing deterministic substrate views."""
    segmentation_graph = unit.metadata.get("segmentation_graph")
    token_tape = unit.token_tape
    clause_index = unit.clause_index
    return assemble_source_syntax_graph(
        subject=subject,
        source_units=source_units,
        statute_id=unit.source_unit_id,
        body=unit.raw_text,
        segmentation_graph=(
            segmentation_graph
            if isinstance(segmentation_graph, SegmentationGraph)
            else None
        ),
        token_tape=token_tape if isinstance(token_tape, TokenTape) else None,
        clause_index=clause_index if isinstance(clause_index, ClauseIndex) else None,
    )


def _source_syntax_stage_account(
    forest: SourceSyntaxGraph, *, statute_id: str
) -> tuple[CoverageCertificate, tuple[Residual, ...]]:
    """Project a forest's token-partition into the core StageResult account.

    Maps the embedded :class:`TokenPartitionCoverage` (itself a pure projection of
    the forest's :class:`SyntaxCoverage`) field-for-field onto the canonical core
    :class:`~lawvm.core.stage_result.CoverageCertificate` (``unit="tokens"``), and
    surfaces the typed residue:

      * every ``unowned_violation`` token span (a silent, signal-bearing span no
        construction family owned) → a BLOCKING ``unowned_violation`` residual that
        carries the verbatim offending span text (self-evidencing — the
        no-silent-drop witness reaches the StageResult as a typed blocker);
      * every explicit ``SyntaxResidual`` (an owned-but-cheap-signal surfaced span)
        → a NON-blocking ``typed_residual`` residual (it is inside the typed-residual
        coverage bucket, not the violation class).

    Benign uninterpreted (whitespace-class) tokens stay out of ``residuals`` — they
    are the ``benign`` coverage bucket, carry no signal, and must not block a clean
    claim.
    """
    # Local import: ``token_partition_coverage`` imports THIS module, so the
    # projector is consumed lazily to avoid an import cycle.
    from lawvm.finland.legal_surface.token_partition_coverage import (
        build_token_partition_coverage,
    )

    part = build_token_partition_coverage(forest, statute_id=statute_id)
    coverage = CoverageCertificate(
        unit="tokens",
        total=part.total_tokens,
        owned=part.owned,
        benign=part.benign_uninterpreted,
        residual=part.typed_residual,
        violation=part.unowned_violation,
        totality_claimed=True,
    )
    residuals: list[Residual] = []
    for viol in part.violations:
        residuals.append(
            Residual(
                kind="unowned_violation",
                reason="forest_silent_unowned_cheap_signal:" + viol.shape,
                scope=statute_id,
                source_unit_id=statute_id,
                char_start=viol.char_start,
                char_end=viol.char_end,
                text=viol.text,
                blocking=True,
            )
        )
    for res in forest.residuals:
        residuals.append(
            Residual(
                kind="typed_residual",
                reason="forest_typed_residual:" + res.shape,
                scope=statute_id,
                source_unit_id=statute_id,
                char_start=res.char_start,
                char_end=res.char_end,
                text=res.text,
                blocking=False,
            )
        )
    return coverage, tuple(residuals)


def assemble_source_syntax_graph_staged(
    *,
    subject: SurfaceGraphSubject,
    unit: SourceSurfaceUnit,
    source_units: tuple[SourceUnitRef, ...] = (),
) -> StageResult[SourceSyntaxGraph]:
    """Assemble a forest for a bundle unit as a typed ``StageResult`` (endgame row #4).

    Thin sibling of :func:`assemble_source_syntax_graph_for_unit`: the ``value`` is
    the SAME (cached) forest the bare form returns (so the ~3 forest consumers stay
    on ``.value`` untouched — 0-delta), and the four accounts are ADDITIVE:

      * ``coverage`` — the forest token-partition projected onto the core
        :class:`~lawvm.core.stage_result.CoverageCertificate` (``unit="tokens"``).
        ``is_partition()`` holds because the embedded
        :class:`SyntaxCoverage`/:class:`TokenPartitionCoverage` already totalize the
        signal-bearing token space; ``violation>0`` ⟺ a silent-unowned span exists
        (target 0 — the GREEN corpus carries 0).
      * ``residuals`` — one BLOCKING ``unowned_violation`` per silent span (verbatim
        text) + one NON-blocking ``typed_residual`` per surfaced owned-residue span.
      * ``evidence`` = :data:`EMPTY_EVIDENCE` (1D: the forest's source footing is the
        upstream token-bundle witness; the syntax waist mints no new source identity).
      * ``findings`` = ``()`` (the forest emits residual nodes, not registry findings).
      * ``authority`` = :data:`NEUTRAL_AUTHORITY` (surface facts are not replay
        authority; the firewall is in the default).
    """
    forest = assemble_source_syntax_graph_for_unit(
        subject=subject, unit=unit, source_units=source_units
    )
    coverage, residuals = _source_syntax_stage_account(
        forest, statute_id=unit.source_unit_id
    )
    return StageResult(
        value=forest,
        evidence=EMPTY_EVIDENCE,
        residuals=residuals,
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


def _assemble_source_syntax_graph(
    *,
    subject: SurfaceGraphSubject,
    source_units: tuple[SourceUnitRef, ...],
    statute_id: str,
    body: str,
    segmentation_graph: SegmentationGraph | None = None,
    token_tape: TokenTape | None = None,
    clause_index: ClauseIndex | None = None,
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
    seg_graph = segmentation_graph or build_segmentation_graph(statute_id, body)

    nodes: dict[str, SyntaxNode] = {}
    edges: list[SyntaxEdge] = []

    # ── 1. structural skeleton ───────────────────────────────────────────────
    # segment-tuple-index → minted node_id (None for un-materialized residual gaps)
    seg_node_id: list[str | None] = _emit_structural_nodes(
        seg_graph, text_hash=text_hash, nodes=nodes, edges=edges
    )
    tape = token_tape or build_token_tape(statute_id, body)
    index = clause_index or build_clause_index(statute_id, body, token_tape=tape)
    body_union = analyze_body_union(
        statute_id,
        body,
        max_examples_per_shape=10_000,
        token_tape=tape,
        clause_index=index,
    )

    # ── 2. construction leaves (the family union) ────────────────────────────
    _emit_construction_leaves(
        body,
        statute_id=statute_id,
        seg_graph=seg_graph,
        seg_node_id=seg_node_id,
        text_hash=text_hash,
        sentence_unions=body_union.sentence_unions,
        nodes=nodes,
        edges=edges,
    )

    # ── 2b. ListConstruction + chapeau-frame inheritance (the L2 construction) ─
    # Bind each chapeau's governing modal frame onto its governed list items via
    # the RESERVED has_subject / has_condition edges (grammar6 §"list inheritance").
    # Structural only (item membership from parent_index, frame from the chapeau's
    # own parseable modal leaf); a chapeau with no parseable frame leaves its items
    # frame-unattached (no fabrication).
    list_constructions = _emit_list_inheritance(
        seg_graph=seg_graph,
        seg_node_id=seg_node_id,
        nodes=nodes,
        edges=edges,
    )

    # ── 3 + 4. coverage + residuals (REUSE the L0 ruler) ─────────────────────
    bucket_counts = body_union.bucket_counts
    family_counts = body_union.family_counts
    unowned_shape_counts = body_union.unowned_shape_counts
    unowned_examples = body_union.unowned_examples
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
        list_constructions=list_constructions,
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
            node_status=_structural_status(),
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
    sentence_unions: tuple[SentenceUnionAnalysis, ...],
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
    del statute_id
    segment_search_start = 0
    segments = seg_graph.segments

    def _next_segment_index_for_span(start: int, end: int) -> int | None:
        nonlocal segment_search_start
        while (
            segment_search_start < len(segments)
            and segments[segment_search_start].char_end <= start
        ):
            segment_search_start += 1
        for i in range(segment_search_start, len(segments)):
            seg = segments[i]
            if seg.char_start <= start and end <= seg.char_end:
                return i
        return None

    for sentence in sentence_unions:
        off = sentence.char_start
        su = sentence.union
        if not su.owners:
            continue
        # Per-family parse kind tags, so a condexc run can be refined to exception.
        parse_kinds = su.family_kinds
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
                node_status="parsed",
                families=tuple(sorted(families)),
            )
            seg_i = _next_segment_index_for_span(start, end)
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

# ---------------------------------------------------------------------------
# ListConstruction + chapeau-frame inheritance (the L2 construction).
# ---------------------------------------------------------------------------


def _enclosed_modal_leaf(
    chapeau: SyntaxNode, nodes: dict[str, SyntaxNode]
) -> SyntaxNode | None:
    """The chapeau's GOVERNING modal frame leaf, or None when it has no frame.

    The chapeau's governing actor/modal frame is its OWN ``modal_predicate``
    construction leaf — the leaf the modal family already minted inside the chapeau
    span (``Viranomainen voi …`` / ``X ei saa …`` / ``X:n on tehtävä …``). We pick
    the modal leaf fully contained in the chapeau span; when several exist (e.g. a
    coordinated modal pair) the earliest-in-span one is the governing frame (the
    sentence-initial deontic core), kept deterministic by ``(start, end)`` order.

    Returns ``None`` when NO modal leaf sits inside the chapeau — the chapeau then
    carries no parseable frame and its items are left unattached (no fabrication).
    The frame is taken from the chapeau's OWN parse, never guessed from the items.
    """
    candidates = [
        n
        for n in nodes.values()
        if n.kind == "modal_predicate"
        and chapeau.char_start <= n.char_start
        and n.char_end <= chapeau.char_end
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: (n.char_start, n.char_end))
    return candidates[0]


def _emit_list_inheritance(
    *,
    seg_graph: SegmentationGraph,
    seg_node_id: list[str | None],
    nodes: dict[str, SyntaxNode],
    edges: list[SyntaxEdge],
) -> tuple[ListConstruction, ...]:
    """Build the :class:`ListConstruction` groupings + bind each chapeau's frame.

    For every chapeau structural node that governs >=1 ``list_item`` (the
    ``parent_index`` links the structural pass already emitted as
    ``inherits_chapeau``), locate the chapeau's governing modal frame leaf
    (:func:`_enclosed_modal_leaf`). When one is found, bind it onto EACH governed
    item with the RESERVED frame edges (grammar6 §"list inheritance"):

      * ``has_subject``  — item ← the chapeau's modal frame leaf: the item inherits
        the chapeau's actor/modal (the deontic frame the chapeau sets is the
        subject frame each item carries);
      * ``has_condition`` — the chapeau's modal frame leaf → item: the item is a
        condition-set member / sub-norm of the chapeau's norm.

    When the chapeau has NO parseable modal frame, NO frame edge is emitted (the
    items keep only their structural ``inherits_chapeau`` link) and the construction
    is recorded with ``frame_status="unsupported"`` — the fail-loud, never-fabricate
    behaviour. The ``inherits_chapeau`` edges themselves were already emitted by the
    structural pass; this step adds ONLY the frame edges + the typed view.
    """
    # Map each chapeau segment-index → its governed list_item segment-indices,
    # from the structural parent_index links (the same links the structural pass
    # turned into inherits_chapeau edges).
    items_by_chapeau: dict[int, list[int]] = {}
    for i, seg in enumerate(seg_graph.segments):
        if seg.kind != "list_item" or seg.parent_index is None:
            continue
        parent = seg_graph.segments[seg.parent_index]
        if parent.kind != "chapeau":
            continue
        items_by_chapeau.setdefault(seg.parent_index, []).append(i)

    constructions: list[ListConstruction] = []
    for chapeau_seg_i in sorted(items_by_chapeau):
        chapeau_nid = seg_node_id[chapeau_seg_i]
        if chapeau_nid is None:
            continue
        chapeau_node = nodes[chapeau_nid]
        item_nids = tuple(
            nid
            for seg_i in items_by_chapeau[chapeau_seg_i]
            if (nid := seg_node_id[seg_i]) is not None
        )
        if not item_nids:
            continue

        frame_leaf = _enclosed_modal_leaf(chapeau_node, nodes)
        if frame_leaf is None:
            # No parseable frame on the chapeau → leave items unattached. The
            # structural inherits_chapeau edges still hold; we emit NO frame edge
            # (fail-loud: never fabricate an inherited actor/modal).
            constructions.append(
                ListConstruction(
                    chapeau_id=chapeau_nid,
                    frame_node_id="",
                    item_ids=item_nids,
                    frame_status="unsupported",
                )
            )
            continue

        for item_nid in item_nids:
            # item inherits the chapeau's actor/modal (the subject frame) …
            edges.append(
                SyntaxEdge(kind="has_subject", src=item_nid, dst=frame_leaf.node_id)
            )
            # … and is a condition-set member / sub-norm of the chapeau's norm.
            edges.append(
                SyntaxEdge(kind="has_condition", src=frame_leaf.node_id, dst=item_nid)
            )
        constructions.append(
            ListConstruction(
                chapeau_id=chapeau_nid,
                frame_node_id=frame_leaf.node_id,
                item_ids=item_nids,
                frame_status="inherited",
            )
        )
    return tuple(constructions)


def _emit_residual_nodes(
    body: str,
    *,
    unowned_examples: Sequence[UnownedSignalSpan],
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
    node_status = "unsupported" if totality else "open"
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
            node_status=node_status,
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


# ---------------------------------------------------------------------------
# Projection: ListConstruction → per-item inherited norm/condition frames.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InheritedItemFrame:
    """One list item projected as a sub-norm INHERITING its chapeau's frame.

    The projection of a :class:`ListConstruction`: each governed ``list_item`` is
    re-read as a condition-set member / sub-norm of the chapeau's norm, CARRYING
    the chapeau's deontic frame (actor + necessity/permission) instead of being an
    unattached fragment (grammar6 §"list inheritance"). Surface-only — it carries
    the SPANS of the inherited frame leaf (the chapeau's ``modal_predicate``) and
    the item, not a legal conclusion; it authorizes no replay.

    Attributes:
        item_id:         ``node_id`` of the governed ``list_item`` node.
        chapeau_id:      ``node_id`` of the governing chapeau.
        frame_node_id:   ``node_id`` of the chapeau's inherited ``modal_predicate``
                         frame leaf.
        item_span:       ``[char_start, char_end)`` of the item (the sub-norm body).
        frame_span:      ``[char_start, char_end)`` of the inherited frame leaf.
        inherited_families: The family ids on the inherited frame leaf (``modal``,
                         plus any coordinating family — the actor/modal the item
                         inherits).
    """

    item_id: str
    chapeau_id: str
    frame_node_id: str
    item_span: tuple[int, int]
    frame_span: tuple[int, int]
    inherited_families: tuple[str, ...]


def project_list_inheritance(
    forest: SourceSyntaxGraph,
) -> tuple[InheritedItemFrame, ...]:
    """Project each chapeau-governed list item as a frame-inheriting sub-norm.

    The L2 projection (grammar6 §"list inheritance is where half-understanding
    becomes powerful"): for every :class:`ListConstruction` the forest carries with
    a parseable frame (``frame_status == "inherited"``), emit ONE
    :class:`InheritedItemFrame` per governed item — the item re-read as a
    condition-set member / sub-norm CARRYING the chapeau's actor/modal frame. A
    construction with NO parseable frame (``frame_status == "unsupported"``)
    projects NOTHING (the items stay unattached; no inheritance is invented).

    Deterministic and surface-only: it reads ONLY the already-assembled forest
    (the chapeau's modal leaf + the structural item nodes), makes NO new attachment
    decision, and authorizes no replay. Items are emitted in span order.
    """
    out: list[InheritedItemFrame] = []
    for lc in forest.list_constructions:
        if lc.frame_status != "inherited" or not lc.frame_node_id:
            continue
        frame = forest.syntax_nodes[lc.frame_node_id]
        for item_id in lc.item_ids:
            item = forest.syntax_nodes[item_id]
            out.append(
                InheritedItemFrame(
                    item_id=item_id,
                    chapeau_id=lc.chapeau_id,
                    frame_node_id=lc.frame_node_id,
                    item_span=(item.char_start, item.char_end),
                    frame_span=(frame.char_start, frame.char_end),
                    inherited_families=frame.families,
                )
            )
    out.sort(key=lambda f: f.item_span)
    return tuple(out)
