"""Construction-derived deontic NORM edges — the first real Layer-2 composition.

This is the FIRST Layer-2 ("middle semantics") composition step of the Finnish
SourceSyntaxGraph: it composes the condition / exception construction parse
(:mod:`lawvm.finland.legal_surface.condition_exception_parse`) into deontic NORM
edges in the :class:`~lawvm.core.legal_surface_graph.LegalSurfaceGraph`, replacing
the over-generating proximity join the EXPERIMENTAL
:class:`~lawvm.finland.legal_surface.frame_relations.ExceptionScopesFramePass`
performs.

Where the proximity pass joins EVERY ``exception_condition_cue`` to EVERY frame
within a 120-char window (a near-complete bipartite mesh — proximity is NOT
attachment), this pass consumes the construction island's already-computed
ATTACHMENT: each :class:`~lawvm.finland.legal_surface.condition_exception_parse.Qualifier`
carries an ``attached_core_index`` + ``attachment_status`` pointing at the modal
core it scopes (from :func:`parse_modal_sentence`). That attachment IS the deontic
edge — this pass wires it into the graph, it does NOT recompute it.

Edge kinds (the Layer-2 ``condition_attachment`` / ``excepted_by`` precursors):

  * ``condition_attaches_norm`` — a CONDITION qualifier → the deontic core node it
    attaches to ("this provision applies WHEN/IF X").
  * ``exception_excepts_norm`` — an EXCEPTION qualifier → the deontic core node it
    attaches to ("this provision does NOT apply in case X").

Attachment status → edge status (tag-don't-guess; never a silent pick):

  * ``resolved`` (exactly one deontic core in the sentence) → ONE edge,
    edge ``status="asserted"`` (the closed ``EDGE_STATUSES`` vocabulary has no
    ``"resolved"`` member; the construction's ``attachment_status="resolved"``
    rides in the payload so the confidence is never lost).
  * ``ambiguous`` (several candidate cores) → ONE edge PER candidate core,
    edge ``status="ambiguous"``, each carrying the full candidate set in payload.
    The construction never silently commits to one of several plausible targets;
    the graph mirrors that by emitting the whole candidate set, not the nearest.
  * ``candidate`` (no deontic core to attach to, OR — now rare — the construction
    core has no corresponding ``deontic_core`` node in the graph) → NO asserted
    edge. The qualifier exists but its target is not a typed graph node; it is
    recorded as a typed diagnostic, never an invented edge.

THE AUTHORITY FIREWALL (§D7) is preserved unconditionally: every edge is minted by
the assembler with ``surface_only=True`` / ``replay_authorized=False``. A NORM edge
is a SURFACE candidate ("the construction grammar attaches this qualifier to that
deontic core"), NOT a legal conclusion that the norm is conditioned/excepted as a
matter of law — that conclusion must LEAVE the graph through a named
authorization/proof object.

Coordinate-space bridge
========================
The construction parse runs PER SENTENCE in sentence-local coordinates; the graph
nodes carry ``raw_text``-relative char spans. This pass therefore needs the source
text to (a) re-derive the sentences (the shared clause-segmentation authority,
:func:`build_clause_index`) and (b) translate each qualifier/core sentence-local
span back to ``raw_text`` coordinates so it can be matched to the EXISTING
``exception_condition_cue`` (cue) / ``deontic_core`` (target) graph nodes by span. Because an
edge pass only receives the assembled graph, the pass is constructed per-statute
from the bundle (see :func:`condition_attachment_passes`), holding the units it
needs. It mints NO new nodes — it only joins nodes other lenses already produced.

The construction's deontic cores come from :func:`parse_modal_sentence`. The
``deontic_core`` lens mints a graph node for EVERY such core (recognising it from
the modal cue alone), so the attachment the construction already computed maps onto
a backing node directly — the DENSE substrate that unblocks this edge set. This is
re-pointed off the production ``actor_modal_frame`` node (which the production lens
mints only when a registered actor sits within 60 chars), which backed only a
sparse minority of construction cores, leaving most attachments edgeless. With the
dense ``deontic_core`` substrate the ``candidate`` (no backing node) case is now
rare. The construction edge set is still far smaller and more precise than the
proximity mesh: it attaches each qualifier to its OWN sentence's core(s).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceEdgeSeed,
)
from lawvm.core.legal_surface_tokens import TokenTape
from lawvm.finland.legal_surface.clause_segment import build_clause_index
from lawvm.finland.legal_surface.condition_exception_parse import (
    ATTACH_AMBIGUOUS,
    ATTACH_CANDIDATE,
    ATTACH_RESOLVED,
    KIND_CONDITION,
    KIND_EXCEPTION,
    ConditionExceptionParse,
    Qualifier,
    parse_condition_exception_sentence,
)

JURISDICTION = "fi"

# Edge kinds — the Layer-2 condition_attachment / excepted_by precursors.
EDGE_CONDITION_ATTACHES = "condition_attaches_norm"
EDGE_EXCEPTION_EXCEPTS = "exception_excepts_norm"

PASS_ID = "fi.norm_composition.v0"
RULE_CONDITION = "fi.norm_composition.condition_attaches_norm"
RULE_EXCEPTION = "fi.norm_composition.exception_excepts_norm"

# The graph node kinds this pass joins. The qualifier (source) is the H6
# exception_condition_cue node; the deontic core (target) is the DENSE
# construction ``deontic_core`` node (minted by the deontic_core lens from
# parse_modal_sentence — one node per modal core). The pass mints NO nodes — it
# only links these.
#
# Re-pointed off the sparse production ``actor_modal_frame`` (which the production
# recognizer mints only when a registered actor sits within 60 chars of the modal):
# the construction attachment already refers to the construction modal core, and
# the deontic_core lens now mints a node for EVERY such core, so the attachment
# index maps onto a backing node directly. This is what unblocks the dense Layer-2
# deontic edge set (previously most construction cores had no backing node, so
# their attachments produced no edge).
CUE_NODE_KIND = "exception_condition_cue"
CORE_NODE_KIND = "deontic_core"

#: Map the qualifier kind to its NORM edge kind.
_KIND_EDGE: dict[str, str] = {
    KIND_CONDITION: EDGE_CONDITION_ATTACHES,
    KIND_EXCEPTION: EDGE_EXCEPTION_EXCEPTS,
}
_KIND_RULE: dict[str, str] = {
    KIND_CONDITION: RULE_CONDITION,
    KIND_EXCEPTION: RULE_EXCEPTION,
}

#: Why a qualifier produced no asserted edge (the typed candidate/diagnostic set).
NO_CORE_IN_SENTENCE = "no_deontic_core_in_sentence"
NO_GRAPH_NODE_FOR_CUE = "no_exception_condition_cue_node_for_qualifier"
NO_GRAPH_NODE_FOR_CORE = "no_deontic_core_node_for_attached_core"


@dataclass(frozen=True)
class UnattachedQualifier:
    """A construction qualifier that produced NO asserted NORM edge (tagged).

    Carried for the differential report / debugging — never an edge. The reason
    distinguishes the genuinely target-less qualifier (no deontic core in the
    sentence) from the coordinate-bridge miss (the construction core or the cue
    has no corresponding graph node — the production recognizer did not emit one).
    """

    source_unit_id: str
    kind: str
    cue: str
    cue_char_start: int
    cue_char_end: int
    reason: str


def _node_index_by_unit_kind(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> dict[str, list[tuple[str, SourceSpanRef]]]:
    """Index source-fact nodes of one kind by source unit, sorted by char_start.

    Returns ``{source_unit_id: [(node_id, source_ref), …]}`` for nodes carrying a
    ``source_ref``. Entity-handle nodes (no ``source_ref``) are skipped — they have
    no span to match a construction span against.
    """
    index: dict[str, list[tuple[str, SourceSpanRef]]] = {}
    for nid, node in nodes.items():
        if node.node_kind != kind:
            continue
        ref = node.source_ref
        if ref is None:
            continue
        index.setdefault(ref.source_unit_id, []).append((nid, ref))
    for unit_id in index:
        index[unit_id].sort(key=lambda kv: (kv[1].char_start, kv[1].char_end, kv[0]))
    return index


def _find_node_covering(
    candidates: list[tuple[str, SourceSpanRef]], start: int, end: int
) -> str | None:
    """Find the graph node whose span best matches ``[start, end)`` (raw_text).

    The construction cue/core span and the lens node span are both anchored in the
    SAME ``raw_text`` coordinate space, but they need not be byte-identical (the
    construction cue span is the matched marker; an ``exception_condition_cue``
    node may differ, and historically the ``actor_modal_frame`` span was the WHOLE
    actor..object frame containing the modal cue — the ``deontic_core`` node span IS
    the cue span, so it matches exactly). A match is the
    node whose span OVERLAPS ``[start, end)`` and whose ``char_start`` is the
    closest at-or-before the query start — i.e. the frame/cue node that owns this
    construction span. Returns the node id, or ``None`` when nothing overlaps
    (fail-loud by absence: no fabricated link).
    """
    best: str | None = None
    best_start = -1
    for nid, ref in candidates:
        # overlap test in the same source unit / coordinate space
        if ref.char_end <= start or end <= ref.char_start:
            continue
        # prefer the node whose span starts closest at-or-before the query start
        if ref.char_start <= end and ref.char_start > best_start:
            best_start = ref.char_start
            best = nid
    return best


def _core_candidate_indices(qualifier: Qualifier, parse: ConditionExceptionParse) -> list[int]:
    """The deontic-core indices a qualifier attaches to, by attachment status.

    * ``resolved`` / ``ambiguous`` carry an ``attached_core_index``. For
      ``ambiguous`` the construction records the NEAREST as that index but flags
      the ambiguity; here we surface the FULL candidate set (every modal core in
      the sentence) so the graph never silently commits to the nearest one.
    * ``candidate`` carries no index (no core) → empty.
    """
    if qualifier.attachment_status == ATTACH_RESOLVED:
        return [] if qualifier.attached_core_index is None else [qualifier.attached_core_index]
    if qualifier.attachment_status == ATTACH_AMBIGUOUS:
        # the whole candidate set — every core in the sentence is a candidate
        return list(range(len(parse.cores)))
    return []


@dataclass
class ConditionAttachmentPass:
    """Edge pass: construction qualifier → attached deontic core NORM edge.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`.
    Constructed per-statute from the bundle units (the construction parse needs the
    source text to re-derive sentences and translate sentence-local spans to
    ``raw_text``). Mints NO nodes; it only joins the EXISTING
    ``exception_condition_cue`` (source) and ``deontic_core`` (target) nodes that
    the H6 / deontic_core lenses already produced.

    Determinism: a single left-to-right pass over the units' sentences (the shared
    :func:`build_clause_index` authority); qualifiers in source order; ambiguous
    candidate cores in core order. The assembler recomputes the graph_id over the
    edge set.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID
    reads_node_kinds: tuple[str, ...] = (CUE_NODE_KIND, CORE_NODE_KIND)
    emits_edge_kinds: tuple[str, ...] = (
        EDGE_CONDITION_ATTACHES,
        EDGE_EXCEPTION_EXCEPTS,
    )
    # Typed diagnostics for qualifiers that produced no asserted edge. Populated on
    # run(); read by the differential report. NOT a graph element.
    unattached: list[UnattachedQualifier] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        cue_index = _node_index_by_unit_kind(graph.nodes, CUE_NODE_KIND)
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            cue_nodes = cue_index.get(unit_id, [])
            core_nodes = core_index.get(unit_id, [])
            # Reuse the unit's populated token tape when present (the bundle sets
            # it); fall back to building one on demand. The tape view is typed
            # ``object | None`` on the unit, so narrow it before passing.
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = build_clause_index(unit_id, unit.raw_text, token_tape=tape)
            except Exception:
                # raw_text unparseable for this unit → no sentences, no edges.
                continue
            for sent in index.sentences:
                base = sent.char_start
                seg_text = unit.raw_text[sent.char_start : sent.char_end]
                parse = parse_condition_exception_sentence(seg_text)
                seeds.extend(
                    self._sentence_edges(
                        parse,
                        base=base,
                        unit_id=unit_id,
                        cue_nodes=cue_nodes,
                        core_nodes=core_nodes,
                    )
                )
        return tuple(seeds)

    def _sentence_edges(
        self,
        parse: ConditionExceptionParse,
        *,
        base: int,
        unit_id: str,
        cue_nodes: list[tuple[str, SourceSpanRef]],
        core_nodes: list[tuple[str, SourceSpanRef]],
    ) -> list[SurfaceEdgeSeed]:
        out: list[SurfaceEdgeSeed] = []
        for q in parse.qualifiers:
            cue_abs_start = base + q.cue_start
            cue_abs_end = base + q.cue_end
            src_id = _find_node_covering(cue_nodes, cue_abs_start, cue_abs_end)
            if src_id is None:
                # The construction matched a cue the H6 lens did not emit a node
                # for (coordinate-bridge miss). Tag it; never invent a src.
                self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_GRAPH_NODE_FOR_CUE)
                continue

            if q.attachment_status == ATTACH_CANDIDATE:
                # No deontic core in the sentence → no asserted edge (tagged).
                self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_CORE_IN_SENTENCE)
                continue

            core_idx_set = _core_candidate_indices(q, parse)
            if not core_idx_set:
                self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_CORE_IN_SENTENCE)
                continue

            # Map the construction attachment confidence onto the CLOSED edge
            # status vocabulary (EDGE_STATUSES has no "resolved" member): a
            # resolved (single-core) attachment is a settled surface fact →
            # "asserted"; a multi-core attachment is "ambiguous". The construction's
            # own ``attachment_status`` ("resolved"/"ambiguous") rides in the
            # payload so the distinction is never lost.
            edge_status = (
                "asserted" if q.attachment_status == ATTACH_RESOLVED else "ambiguous"
            )
            candidate_payload = self._candidate_payload(parse, base, core_idx_set)

            emitted_any = False
            for core_idx in core_idx_set:
                core = parse.cores[core_idx]
                core_abs_start = base + core.cue_start
                core_abs_end = base + core.cue_end
                dst_id = _find_node_covering(core_nodes, core_abs_start, core_abs_end)
                if dst_id is None:
                    # The construction core has no backing deontic_core node
                    # (coordinate-bridge miss — should be rare now the deontic_core
                    # lens mints one per core). No asserted edge for THIS core.
                    continue
                out.append(
                    self._edge_seed(
                        q,
                        src_id=src_id,
                        dst_id=dst_id,
                        edge_status=edge_status,
                        cue_span=[cue_abs_start, cue_abs_end],
                        core_span=[core_abs_start, core_abs_end],
                        attachment_status=q.attachment_status,
                        candidate_payload=candidate_payload,
                    )
                )
                emitted_any = True
            if not emitted_any:
                # The construction attached to a core, but no graph node backs it.
                self._tag(
                    unit_id, q, cue_abs_start, cue_abs_end, NO_GRAPH_NODE_FOR_CORE
                )
        return out

    def _candidate_payload(
        self, parse: ConditionExceptionParse, base: int, core_idx_set: list[int]
    ) -> list[list[int]]:
        """The full candidate-core span set (raw_text), for the ambiguous payload."""
        return [
            [base + parse.cores[i].cue_start, base + parse.cores[i].cue_end]
            for i in core_idx_set
        ]

    def _edge_seed(
        self,
        q: Qualifier,
        *,
        src_id: str,
        dst_id: str,
        edge_status: str,
        cue_span: list[int],
        core_span: list[int],
        attachment_status: str,
        candidate_payload: list[list[int]],
    ) -> SurfaceEdgeSeed:
        edge_kind = _KIND_EDGE[q.kind]
        rule_id = _KIND_RULE[q.kind]
        payload: dict[str, object] = {
            "qualifier_kind": q.kind,
            "cue": q.cue,
            "attachment_status": attachment_status,
            "cue_span": cue_span,
            "core_span": core_span,
            "source": "construction_attachment",
            "experimental": True,
        }
        if attachment_status == ATTACH_AMBIGUOUS:
            # Carry the full candidate set so a consumer sees this edge is one of
            # several plausible attachments — never a silent pick.
            payload["candidate_core_spans"] = candidate_payload
        return SurfaceEdgeSeed(
            edge_kind=edge_kind,
            src_local=src_id,
            dst_local=dst_id,
            rule_id=rule_id,
            status=edge_status,
            payload=payload,
        )

    def _tag(
        self,
        unit_id: str,
        q: Qualifier,
        cue_abs_start: int,
        cue_abs_end: int,
        reason: str,
    ) -> None:
        self.unattached.append(
            UnattachedQualifier(
                source_unit_id=unit_id,
                kind=q.kind,
                cue=q.cue,
                cue_char_start=cue_abs_start,
                cue_char_end=cue_abs_end,
                reason=reason,
            )
        )


def condition_attachment_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[ConditionAttachmentPass, ...]:
    """Build the per-statute condition/exception attachment edge pass(es).

    The pass needs the source text (it runs the construction parse), so it is
    constructed from the bundle units rather than registered as a stateless module
    default. Returns a one-tuple (one pass over all units) so the caller can splice
    it into the edge-pass sequence additively.
    """
    return (ConditionAttachmentPass(units=bundle.units),)


# ── delegates_to / sanctioned_by — the next two Layer-2 deontic NORM edges ────
#
# Same Layer-2 family as the condition/exception attachment above, on the SAME
# dense ``deontic_core`` substrate, with the SAME discipline (per-statute,
# SENTENCE-LOCAL, candidate-not-asserted, additive, surface_only firewall). The
# difference is the join shape: instead of consuming a construction-computed
# attachment index (qualifier → core), this pass joins a deontic core to a
# co-SENTENCE FRAME node another lens already minted:
#
#   * ``delegates_to``  — a ``power``-kind deontic core (the delegating verb
#     register: ``säädetään`` / ``annetaan`` / ``valtuus`` … → ``KIND_POWER``,
#     whose ``object_span`` IS the delegation instrument) → the ``delegation_frame``
#     in the same sentence whose instrument it grants.
#   * ``sanctioned_by`` — a ``prohibition`` / ``obligation`` deontic core (a duty
#     or ban) → the ``sanction_frame`` in the same sentence that backs it.
#
# Why a sentence-local span join, not a construction attachment index: there is no
# construction parse that emits a (core → delegation_frame) or (core →
# sanction_frame) attachment the way ``parse_condition_exception_sentence`` emits
# (qualifier → core). The frame nodes come from independent production recognizers
# (delegation / sanction lenses). The deterministic, conservative join is
# SENTENCE membership: a deontic core and a frame that fall in the SAME sentence
# (the shared ``build_clause_index`` authority) co-occur in one provision. This is
# NOT a proximity window over the whole body (which would mesh across sentences —
# the very over-generation the condition pass was built to replace); it is bound
# to one sentence, the smallest deterministic provision unit.
#
# Candidate-not-asserted (never a silent pick):
#   * a core with exactly ONE co-sentence frame target → ONE edge, status
#     ``"candidate"`` (a sentence-local co-occurrence affordance, never an asserted
#     legal conclusion that the power validly delegates / the norm is enforceably
#     sanctioned — that reading leaves the graph through a named authorization
#     object);
#   * a core with SEVERAL co-sentence frame targets → ONE edge PER candidate
#     frame, status ``"ambiguous"``, each carrying the full candidate-frame set in
#     payload (the graph never commits to the nearest frame);
#   * a core with NO co-sentence frame target → NO edge; recorded as a typed
#     ``UnattachedCore`` diagnostic (``NO_FRAME_IN_SENTENCE``), never an invented
#     edge.

# Edge kinds — the delegates_to / sanctioned_by Layer-2 deontic NORM edges.
EDGE_DELEGATES_TO = "delegates_to"
EDGE_SANCTIONED_BY = "sanctioned_by"

PASS_ID_DEONTIC_FRAME = "fi.norm_composition.deontic_frame.v0"
RULE_DELEGATES_TO = "fi.norm_composition.delegates_to"
RULE_SANCTIONED_BY = "fi.norm_composition.sanctioned_by"

#: Graph node kinds this pass joins. Source is the dense ``deontic_core`` node;
#: targets are the production ``delegation_frame`` / ``sanction_frame`` nodes.
DELEGATION_FRAME_KIND = "delegation_frame"
SANCTION_FRAME_KIND = "sanction_frame"

#: deontic-core ``kind`` (payload) that licenses each edge family.
_POWER_KIND = "power"
_SANCTIONABLE_KINDS = frozenset({"prohibition", "obligation"})

#: Why a deontic core produced no asserted edge (the typed diagnostic set).
NO_FRAME_IN_SENTENCE = "no_target_frame_in_sentence"


@dataclass(frozen=True)
class UnattachedCore:
    """A deontic core that produced NO deontic-frame edge (tagged, never an edge).

    Carried for the differential report / debugging. ``edge_kind`` is the edge
    family the core was eligible for (``delegates_to`` / ``sanctioned_by``);
    ``reason`` is why no edge was minted (no co-sentence frame target).
    """

    source_unit_id: str
    edge_kind: str
    core_kind: str
    core_char_start: int
    core_char_end: int
    reason: str


def _sentence_of(start: int, sentences: list[tuple[int, int]]) -> int:
    """Index of the sentence whose ``[char_start, char_end)`` contains ``start``.

    Returns ``-1`` when ``start`` falls in no sentence (a defensive guard; the
    clause index spans the whole unit, so a core/frame span should always land).
    Used to place a deontic CORE (whose span is the tiny modal-cue span) in its
    sentence.
    """
    for i, (s, e) in enumerate(sentences):
        if s <= start < e:
            return i
    return -1


def _sentences_overlapped(
    span_start: int, span_end: int, sentences: list[tuple[int, int]]
) -> list[int]:
    """Indices of every sentence the ``[span_start, span_end)`` span OVERLAPS.

    A frame span (delegation / sanction) is NOT a tiny cue — it is the whole
    recognised clause (target..trigger), which often starts in an earlier sentence
    than its deontic marker and can straddle a clause boundary. Anchoring a frame
    to the single sentence of its ``char_start`` therefore mis-files it. Instead a
    frame belongs to every sentence its span overlaps, so a core is linked to a
    frame iff the frame's text overlaps the CORE's sentence (true sentence-local
    co-occurrence, not a whole-body proximity mesh).
    """
    out: list[int] = []
    for i, (s, e) in enumerate(sentences):
        if span_end <= s or e <= span_start:
            continue
        out.append(i)
    return out


@dataclass
class DeonticFrameAttachmentPass:
    """Edge pass: deontic core → co-sentence delegation/sanction frame NORM edge.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`.
    Constructed per-statute from the bundle units (it re-derives sentence
    boundaries via :func:`build_clause_index` to bound the join to one sentence).
    Mints NO nodes; it only joins EXISTING ``deontic_core`` (source) and
    ``delegation_frame`` / ``sanction_frame`` (target) nodes other lenses produced.

    For each unit, each sentence:
      * ``power`` cores → each ``delegation_frame`` in the sentence (``delegates_to``);
      * ``prohibition`` / ``obligation`` cores → each ``sanction_frame`` in the
        sentence (``sanctioned_by``).
    One target → ``"candidate"``; several → one edge per target, ``"ambiguous"``,
    full set in payload; none → typed :class:`UnattachedCore` diagnostic.

    Determinism: units in declared order; sentences left-to-right; cores by
    char_start; candidate frames by char_start. The assembler recomputes graph_id.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_DEONTIC_FRAME
    reads_node_kinds: tuple[str, ...] = (
        CORE_NODE_KIND,
        DELEGATION_FRAME_KIND,
        SANCTION_FRAME_KIND,
    )
    emits_edge_kinds: tuple[str, ...] = (EDGE_DELEGATES_TO, EDGE_SANCTIONED_BY)
    # Typed diagnostics for cores that produced no edge. Populated on run(); read by
    # the differential report. NOT a graph element.
    unattached: list[UnattachedCore] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)
        deleg_index = _node_index_by_unit_kind(graph.nodes, DELEGATION_FRAME_KIND)
        sanct_index = _node_index_by_unit_kind(graph.nodes, SANCTION_FRAME_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            cores = core_index.get(unit_id, [])
            if not cores:
                continue
            delegs = deleg_index.get(unit_id, [])
            sancts = sanct_index.get(unit_id, [])
            if not delegs and not sancts:
                continue
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = build_clause_index(unit_id, unit.raw_text, token_tape=tape)
            except Exception:
                continue
            sentences = [(s.char_start, s.char_end) for s in index.sentences]
            if not sentences:
                continue
            # Bucket the target frames by sentence (deterministic, by char_start).
            deleg_by_sent = self._bucket_by_sentence(delegs, sentences)
            sanct_by_sent = self._bucket_by_sentence(sancts, sentences)
            for nid, ref in cores:
                node = graph.nodes[nid]
                core_kind = node.payload.get("kind")
                sent_i = _sentence_of(ref.char_start, sentences)
                if sent_i < 0:
                    continue
                if core_kind == _POWER_KIND:
                    seeds.extend(
                        self._core_edges(
                            unit_id=unit_id,
                            core_id=nid,
                            core_ref=ref,
                            core_kind=str(core_kind),
                            targets=deleg_by_sent.get(sent_i, []),
                            edge_kind=EDGE_DELEGATES_TO,
                            rule_id=RULE_DELEGATES_TO,
                        )
                    )
                elif core_kind in _SANCTIONABLE_KINDS:
                    seeds.extend(
                        self._core_edges(
                            unit_id=unit_id,
                            core_id=nid,
                            core_ref=ref,
                            core_kind=str(core_kind),
                            targets=sanct_by_sent.get(sent_i, []),
                            edge_kind=EDGE_SANCTIONED_BY,
                            rule_id=RULE_SANCTIONED_BY,
                        )
                    )
                # permission cores (and any other kind) license neither edge.
        return tuple(seeds)

    def _bucket_by_sentence(
        self,
        frames: list[tuple[str, SourceSpanRef]],
        sentences: list[tuple[int, int]],
    ) -> dict[int, list[tuple[str, SourceSpanRef]]]:
        """Group frame (id, ref) pairs by EVERY sentence their span overlaps.

        A frame span is the whole recognised clause and can straddle a sentence
        boundary, so it may appear in more than one bucket (see
        :func:`_sentences_overlapped`). Frames within each bucket stay sorted by
        (char_start, char_end, id) — the order ``_node_index_by_unit_kind`` already
        imposes — so the candidate set and per-candidate edges are deterministic.
        """
        buckets: dict[int, list[tuple[str, SourceSpanRef]]] = {}
        for fid, fref in frames:
            for si in _sentences_overlapped(fref.char_start, fref.char_end, sentences):
                buckets.setdefault(si, []).append((fid, fref))
        return buckets

    def _core_edges(
        self,
        *,
        unit_id: str,
        core_id: str,
        core_ref: SourceSpanRef,
        core_kind: str,
        targets: list[tuple[str, SourceSpanRef]],
        edge_kind: str,
        rule_id: str,
    ) -> list[SurfaceEdgeSeed]:
        if not targets:
            self.unattached.append(
                UnattachedCore(
                    source_unit_id=unit_id,
                    edge_kind=edge_kind,
                    core_kind=core_kind,
                    core_char_start=core_ref.char_start,
                    core_char_end=core_ref.char_end,
                    reason=NO_FRAME_IN_SENTENCE,
                )
            )
            return []
        # candidate-not-asserted: one target → "candidate"; several → one edge per
        # candidate, "ambiguous", each carrying the full candidate-frame set.
        ambiguous = len(targets) > 1
        edge_status = "ambiguous" if ambiguous else "candidate"
        candidate_spans = [[fref.char_start, fref.char_end] for _, fref in targets]
        core_span = [core_ref.char_start, core_ref.char_end]
        out: list[SurfaceEdgeSeed] = []
        for frame_id, frame_ref in targets:
            payload: dict[str, object] = {
                "core_kind": core_kind,
                "core_span": core_span,
                "frame_span": [frame_ref.char_start, frame_ref.char_end],
                "source": "deontic_frame_sentence_local",
                "experimental": True,
            }
            if ambiguous:
                # full candidate set so a consumer sees this is one of several
                # plausible co-sentence frames — never a silent pick.
                payload["candidate_frame_spans"] = candidate_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=edge_kind,
                    src_local=core_id,
                    dst_local=frame_id,
                    rule_id=rule_id,
                    status=edge_status,
                    payload=payload,
                )
            )
        return out


def deontic_frame_attachment_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[DeonticFrameAttachmentPass, ...]:
    """Build the per-statute deontic-core → frame (delegates_to / sanctioned_by) pass.

    Constructed from the bundle units (the pass needs the source text to re-derive
    sentence boundaries). Returns a one-tuple so the caller can splice it into the
    edge-pass sequence ADDITIVELY (alongside the condition/exception pass and the
    proximity incumbents).
    """
    return (DeonticFrameAttachmentPass(units=bundle.units),)


__all__ = [
    "CORE_NODE_KIND",
    "CUE_NODE_KIND",
    "DELEGATION_FRAME_KIND",
    "SANCTION_FRAME_KIND",
    "ConditionAttachmentPass",
    "DeonticFrameAttachmentPass",
    "EDGE_CONDITION_ATTACHES",
    "EDGE_DELEGATES_TO",
    "EDGE_EXCEPTION_EXCEPTS",
    "EDGE_SANCTIONED_BY",
    "NO_CORE_IN_SENTENCE",
    "NO_FRAME_IN_SENTENCE",
    "NO_GRAPH_NODE_FOR_CORE",
    "NO_GRAPH_NODE_FOR_CUE",
    "PASS_ID",
    "PASS_ID_DEONTIC_FRAME",
    "RULE_CONDITION",
    "RULE_DELEGATES_TO",
    "RULE_EXCEPTION",
    "RULE_SANCTIONED_BY",
    "UnattachedCore",
    "UnattachedQualifier",
    "condition_attachment_passes",
    "deontic_frame_attachment_passes",
]
