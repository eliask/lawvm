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

import re
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
from lawvm.core.legal_surface_tokens import (
    AMBIGUOUS,
    ClauseIndex,
    ProvisionIndex,
    ProvisionSpan,
    TokenTape,
)
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
from lawvm.finland.legal_surface.source_syntax_graph import (
    SyntaxNode,
    assemble_source_syntax_graph_for_unit,
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

# ── cross-sentence attachment (the Layer-2 cross-sentence gap closure) ─────────
#
# The intra-sentence attachment above can ONLY attach a qualifier to a deontic
# core in its OWN sentence. A back-reference exception qualifier whose excepted
# norm is stated in a DIFFERENT provision —
#   "Sen estämättä, mitä 6 §:n 1 momentissa on säädetty, …"
#   "Poiketen siitä mitä 8 §:n 2 momentissa on säädetty, …"
# — carries no local deontic core (its matrix is the new rule, not the excepted
# one), so it is tagged ``candidate``/``NO_CORE_IN_SENTENCE`` by the intra-sentence
# pass. The PRINCIPLED cross-sentence target is the provision the back-reference
# NAMES: the closed back-reference cue (``sen estämättä`` / ``poiketen siitä mitä``)
# is followed by ``[siitä] mitä <provision-ref> on säädetään/säädetty`` — and that
# provision reference is an INTERNAL ``reference_expr`` node the ReferenceLens
# already minted (``cite_kind == "internal"``, ``target_provision_ref`` = the §/
# momentti the excepted norm lives in, WITHIN this statute). Binding the exception
# cue to that internal reference IS the cross-sentence attachment: it says "this
# exception excepts the norm at provision §N".
#
# This is a STRICT SUPERSET of the intra-sentence behaviour — it fires ONLY for a
# qualifier the intra-sentence pass would tag ``NO_CORE_IN_SENTENCE`` (no local
# core), and NEVER alters an existing intra-sentence (resolved/ambiguous) edge.
#
# Surface-recoverability boundary (verified by corpus differential):
#   * INTRA-statute back-reference (``cite_kind == "internal"``, non-empty
#     ``target_provision_ref``) → resolvable: the excepted norm lives in THIS
#     statute, so the provision pointer is a typed target in THIS graph.
#   * CROSS-statute back-reference (``sen estämättä mitä osuuskuntalain 177 §:ssä
#     säädetään``) → NOT resolvable here: the excepted norm lives in a DIFFERENT
#     statute, not in this graph. Left ``candidate`` + a typed diagnostic.
#   * NO back-reference provision at all → left ``candidate`` (the honest
#     no-target case).
#
# The edge TARGET for a cross-sentence resolution is the internal ``reference_expr``
# node (a provision pointer), not a ``deontic_core`` (the §N cores are not locatable
# in the flattened whole-body coordinate space, which drops the <num> section
# markers — so the reference node is the strongest recoverable typed target). The
# ``attachment=`` reason distinguishes it from intra-sentence resolution.
REFERENCE_EXPR_NODE_KIND = "reference_expr"

#: The closed back-reference EXCEPTION cues whose construction names the excepted
#: norm's provision (mirrors the production EXCEPTION markers that head a
#: ``[siitä] mitä <ref> säädetään`` back-reference). Casefolded to match the
#: ``Qualifier.cue`` surface.
_BACKREF_EXCEPTION_CUES: frozenset[str] = frozenset(
    {"sen estämättä", "poiketen siitä mitä"}
)

#: Window (chars) from the cue END to the internal reference START still read as
#: the SAME back-reference construction. The reference must sit in the short
#: ``mitä <ref> säädetään`` head right after the cue; a reference further than this
#: belongs to the matrix (the new rule), not the excepted-norm back-reference.
_BACKREF_MAX_GAP = 64

#: Attachment reasons (ride in the edge payload's ``attachment`` field), naming HOW
#: the qualifier was attached. The intra-sentence path carries the construction's
#: own ``attachment_status``; the cross-sentence path names its principled signal.
ATTACHMENT_INTRA_SENTENCE = "intra_sentence"
ATTACHMENT_RESOLVED_BY_PROVISION_REF = "resolved_by_provision_ref"
ATTACHMENT_AMBIGUOUS_BY_PROVISION_REF = "ambiguous_by_provision_ref"

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
#: A back-reference exception cue with no LOCAL core whose excepted-norm provision
#: lies in ANOTHER statute (``cite_kind != "internal"``) or whose back-reference
#: names no provision at all — the cross-sentence target is not in THIS graph, so
#: the qualifier stays candidate (never resolved cross-statute / never invented).
NO_INTERNAL_PROVISION_REF = "no_internal_provision_reference_for_backref"


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
    nodes: Mapping[str, SurfaceNode], kind: str | frozenset[str]
) -> dict[str, list[tuple[str, SourceSpanRef]]]:
    """Index source-fact nodes of one (or several) kind(s) by source unit.

    Returns ``{source_unit_id: [(node_id, source_ref), …]}`` for nodes carrying a
    ``source_ref``, sorted by char_start. Entity-handle nodes (no ``source_ref``)
    are skipped — they have no span to match a construction span against.

    ``kind`` may be a single kind string or a frozenset of kinds (used to admit a
    demoted ``*_cue`` alongside its ``*_frame`` sibling: a bare process/sanction
    noun carries the same span + typed sub-kind a frame does, so a span-based
    construction pass treats them identically).
    """
    kinds = frozenset({kind}) if isinstance(kind, str) else kind
    index: dict[str, list[tuple[str, SourceSpanRef]]] = {}
    for nid, node in nodes.items():
        if node.node_kind not in kinds:
            continue
        ref = node.source_ref
        if ref is None:
            continue
        index.setdefault(ref.source_unit_id, []).append((nid, ref))
    for unit_id in index:
        index[unit_id].sort(key=lambda kv: (kv[1].char_start, kv[1].char_end, kv[0]))
    return index


def _internal_reference_index(
    nodes: Mapping[str, SurfaceNode],
) -> dict[str, list[tuple[str, SourceSpanRef]]]:
    """Index INTERNAL ``reference_expr`` nodes by source unit, sorted by char_start.

    The cross-sentence back-reference attachment targets: ``reference_expr`` nodes
    whose ``cite_kind == "internal"`` (a provision in THIS statute). A cross-statute
    reference is excluded here — its excepted norm lives in a different statute, not
    in this graph, so it can never be a cross-sentence target (left candidate).
    """
    index: dict[str, list[tuple[str, SourceSpanRef]]] = {}
    for nid, node in nodes.items():
        if node.node_kind != REFERENCE_EXPR_NODE_KIND:
            continue
        if node.payload.get("cite_kind") != "internal":
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
        # INTERNAL provision-reference nodes — the cross-sentence back-reference
        # attachment targets (the §/momentti the excepted norm lives in, within
        # THIS statute). Filtered to cite_kind == "internal" so a cross-statute
        # back-reference is never resolved against this graph.
        ref_index = _internal_reference_index(graph.nodes)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            cue_nodes = cue_index.get(unit_id, [])
            core_nodes = core_index.get(unit_id, [])
            ref_nodes = ref_index.get(unit_id, [])
            # Reuse the unit's populated token tape when present (the bundle sets
            # it); fall back to building one on demand. The tape view is typed
            # ``object | None`` on the unit, so narrow it before passing.
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = (
                    unit.clause_index
                    if isinstance(unit.clause_index, ClauseIndex)
                    else build_clause_index(unit_id, unit.raw_text, token_tape=tape)
                )
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
                        sent_start=sent.char_start,
                        sent_end=sent.char_end,
                        unit_id=unit_id,
                        cue_nodes=cue_nodes,
                        core_nodes=core_nodes,
                        ref_nodes=ref_nodes,
                        graph=graph,
                    )
                )
        return tuple(seeds)

    def _sentence_edges(
        self,
        parse: ConditionExceptionParse,
        *,
        base: int,
        sent_start: int,
        sent_end: int,
        unit_id: str,
        cue_nodes: list[tuple[str, SourceSpanRef]],
        core_nodes: list[tuple[str, SourceSpanRef]],
        ref_nodes: list[tuple[str, SourceSpanRef]],
        graph: LegalSurfaceGraph,
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
                # No deontic core in THIS sentence. Before giving up, try the
                # CROSS-SENTENCE back-reference attachment: a back-reference
                # exception cue ("sen estämättä mitä N §:ssä säädetään") names the
                # excepted norm's provision, an INTERNAL reference_expr node. This
                # is a STRICT ADDITION — it fires only here (the intra-sentence
                # candidate case), never touching a resolved/ambiguous edge.
                xsent = self._cross_sentence_edges(
                    q,
                    src_id=src_id,
                    cue_abs_start=cue_abs_start,
                    cue_abs_end=cue_abs_end,
                    sent_start=sent_start,
                    sent_end=sent_end,
                    unit_id=unit_id,
                    ref_nodes=ref_nodes,
                    graph=graph,
                )
                out.extend(xsent)
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

    def _cross_sentence_edges(
        self,
        q: Qualifier,
        *,
        src_id: str,
        cue_abs_start: int,
        cue_abs_end: int,
        sent_start: int,
        sent_end: int,
        unit_id: str,
        ref_nodes: list[tuple[str, SourceSpanRef]],
        graph: LegalSurfaceGraph,
    ) -> list[SurfaceEdgeSeed]:
        """Cross-sentence attachment for a back-reference exception qualifier.

        Fires ONLY for the intra-sentence ``candidate`` case (no local deontic
        core) and ONLY for a closed back-reference EXCEPTION cue
        (:data:`_BACKREF_EXCEPTION_CUES`). The cross-sentence target is the
        provision the back-reference NAMES: an INTERNAL ``reference_expr`` node
        (``cite_kind == "internal"``, non-empty ``target_provision_ref``) sitting in
        the short ``mitä <ref> säädetään`` head right after the cue (within
        :data:`_BACKREF_MAX_GAP`, in the SAME sentence). This binds the exception
        to the §/momentti where the excepted norm lives in THIS statute.

        candidate-not-asserted (never a silent pick, never a cross-statute guess):
          * EXACTLY ONE internal reference in the window → ONE edge, status
            ``"asserted"`` (``attachment=resolved_by_provision_ref``);
          * SEVERAL (a coordinated back-reference, ``mitä 12 ja 17-20 §:ssä …``) →
            ONE edge per reference, status ``"ambiguous"``, full candidate-reference
            set in payload;
          * NONE (cross-statute back-reference / no provision named) → NO edge; a
            typed :data:`NO_INTERNAL_PROVISION_REF` diagnostic (left candidate).

        Not a back-reference exception cue → the honest ``NO_CORE_IN_SENTENCE``
        diagnostic (unchanged from the intra-sentence-only behaviour).
        """
        if q.cue not in _BACKREF_EXCEPTION_CUES or q.kind != KIND_EXCEPTION:
            # Not a back-reference exception cue → no cross-sentence signal; keep
            # the original intra-sentence candidate diagnostic.
            self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_CORE_IN_SENTENCE)
            return []

        targets: list[tuple[str, SourceSpanRef, str]] = []
        for nid, ref in ref_nodes:
            # same sentence, forward of the cue, within the back-reference window.
            if ref.char_start < cue_abs_end or ref.char_start >= sent_end:
                continue
            if ref.char_start - cue_abs_end > _BACKREF_MAX_GAP:
                continue
            provision = graph.nodes[nid].payload.get("target_provision_ref")
            if not isinstance(provision, str) or not provision:
                # internal ref node with no resolved provision target → not a
                # usable cross-sentence target (never bind to an empty pointer).
                continue
            targets.append((nid, ref, provision))

        if not targets:
            # cross-statute back-reference (target not in this graph) or no
            # provision named → left candidate, typed diagnostic. Never resolved
            # cross-statute, never invented.
            self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_INTERNAL_PROVISION_REF)
            return []

        targets.sort(key=lambda t: (t[1].char_start, t[1].char_end, t[0]))
        ambiguous = len(targets) > 1
        edge_status = "ambiguous" if ambiguous else "asserted"
        attachment = (
            ATTACHMENT_AMBIGUOUS_BY_PROVISION_REF
            if ambiguous
            else ATTACHMENT_RESOLVED_BY_PROVISION_REF
        )
        candidate_ref_spans = [[r.char_start, r.char_end] for _, r, _ in targets]
        candidate_provisions = [p for _, _, p in targets]
        out: list[SurfaceEdgeSeed] = []
        for nid, ref, provision in targets:
            payload: dict[str, object] = {
                "qualifier_kind": q.kind,
                "cue": q.cue,
                "attachment": attachment,
                "cue_span": [cue_abs_start, cue_abs_end],
                "reference_span": [ref.char_start, ref.char_end],
                "target_provision_ref": provision,
                "source": "construction_cross_sentence_backref",
                "experimental": True,
            }
            if ambiguous:
                # full candidate set so a consumer sees this is one of several
                # back-referenced provisions — never a silent pick of the nearest.
                payload["candidate_reference_spans"] = candidate_ref_spans
                payload["candidate_provisions"] = candidate_provisions
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=EDGE_EXCEPTION_EXCEPTS,
                    src_local=src_id,
                    dst_local=nid,
                    rule_id=RULE_EXCEPTION,
                    surface_edge_status=edge_status,
                    payload=payload,
                )
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
            surface_edge_status=edge_status,
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
#: A bare sanction noun is demoted to ``sanction_cue`` (no target actor / no
#: trigger) but carries the SAME span + ``sanction_kind`` + ``marker_surface`` a
#: ``sanction_frame`` does. The penal-deferral construction ("rangaistaan … niin
#: kuin §:ssä säädetään") is precisely a bare sanction, so these span-based passes
#: must admit BOTH kinds to keep their edge sets identical to before the demote.
SANCTION_FRAME_KINDS: frozenset[str] = frozenset(
    {SANCTION_FRAME_KIND, "sanction_cue"}
)

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
        "sanction_cue",
    )
    emits_edge_kinds: tuple[str, ...] = (EDGE_DELEGATES_TO, EDGE_SANCTIONED_BY)
    # Typed diagnostics for cores that produced no edge. Populated on run(); read by
    # the differential report. NOT a graph element.
    unattached: list[UnattachedCore] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)
        deleg_index = _node_index_by_unit_kind(graph.nodes, DELEGATION_FRAME_KIND)
        sanct_index = _node_index_by_unit_kind(graph.nodes, SANCTION_FRAME_KINDS)

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
                index = (
                    unit.clause_index
                    if isinstance(unit.clause_index, ClauseIndex)
                    else build_clause_index(unit_id, unit.raw_text, token_tape=tape)
                )
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
                            # delegates_to has a PRINCIPLED attachment index, not
                            # mere co-occurrence: the delegating verb that mints the
                            # power core is PART of the delegation_frame span, so a
                            # delegation_frame whose span CONTAINS the core cue is
                            # the one this power grants (see _resolved_target).
                            resolve_by_containment=True,
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
        resolve_by_containment: bool = False,
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
        core_span = [core_ref.char_start, core_ref.char_end]

        # PRINCIPLED ATTACHMENT (delegates_to): the delegating verb that mints a
        # power core is PART of the delegation_frame span, so a frame whose span
        # CONTAINS the core cue is the instrument this very power grants — a
        # structural attachment index, strictly stronger than sentence
        # co-occurrence. When EXACTLY ONE co-sentence frame contains the cue, the
        # attachment is RESOLVED → one edge, status "asserted" (the construction's
        # own confidence rides in payload["attachment"]="resolved_by_containment").
        # When zero or several frames contain it, no principled pick exists; fall
        # back to the co-occurrence candidate/ambiguous discipline below (never a
        # silent pick).
        if resolve_by_containment:
            containing = [
                (fid, fref)
                for fid, fref in targets
                if fref.char_start <= core_ref.char_start
                and core_ref.char_end <= fref.char_end
            ]
            if len(containing) == 1:
                frame_id, frame_ref = containing[0]
                return [
                    SurfaceEdgeSeed(
                        edge_kind=edge_kind,
                        src_local=core_id,
                        dst_local=frame_id,
                        rule_id=rule_id,
                        surface_edge_status="asserted",
                        payload={
                            "core_kind": core_kind,
                            "core_span": core_span,
                            "frame_span": [
                                frame_ref.char_start,
                                frame_ref.char_end,
                            ],
                            "attachment": "resolved_by_containment",
                            "source": "deontic_frame_cue_in_frame",
                            "experimental": True,
                        },
                    )
                ]

        # candidate-not-asserted: one target → "candidate"; several → one edge per
        # candidate, "ambiguous", each carrying the full candidate-frame set.
        ambiguous = len(targets) > 1
        edge_status = "ambiguous" if ambiguous else "candidate"
        candidate_spans = [[fref.char_start, fref.char_end] for _, fref in targets]
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
                    surface_edge_status=edge_status,
                    payload=payload,
                )
            )
        return out


# ── delegation_grants_instrument — frame → the lower instrument it authorizes ──
#
# The north-star "norm → authorized-instrument" Layer-2 link. A delegation grants
# the power to issue a LOWER INSTRUMENT (asetus/määräys/päätös). The production
# ``delegation_frame`` node names the instrument only as a canonical KIND string and
# has no instrument-entity to point at; the ``delegated_instrument`` lens mints that
# entity (anchored on the construction parse's precise instrument anchor span, which
# sits INSIDE the recognizer frame's span). This pass joins each frame to the
# instrument node(s) its span CONTAINS — a STRUCTURAL CONTAINMENT attachment (the
# instrument anchor is part of the delegation frame text), strictly stronger than
# sentence co-occurrence, and bounded to the frame itself (never a body-wide mesh).
#
# Candidate-not-asserted (never a silent pick):
#   * a frame CONTAINING exactly ONE delegated_instrument → ONE edge, status
#     "asserted" (the instrument the frame unambiguously grants);
#   * a frame containing SEVERAL instruments (a coordinated grant: "annetaan
#     asetuksella, X:n päätöksellä …") → ONE edge PER contained instrument, status
#     "ambiguous", each carrying the full contained-instrument set in payload (the
#     graph never commits to the nearest one);
#   * a frame containing NO delegated_instrument node (the recognizer typed an
#     instrument_kind the construction parse did not anchor — a coordinate-bridge
#     miss between the two delegation parsers) → NO edge; a typed UnattachedFrame
#     diagnostic, never an invented edge.

EDGE_DELEGATION_GRANTS_INSTRUMENT = "delegation_grants_instrument"

PASS_ID_DELEG_INSTRUMENT = "fi.norm_composition.delegation_instrument.v0"
RULE_DELEGATION_GRANTS_INSTRUMENT = (
    "fi.norm_composition.delegation_grants_instrument"
)

#: Graph node kinds this pass joins. Source is the recognizer ``delegation_frame``
#: node; target is the construction ``delegated_instrument`` node.
DELEGATED_INSTRUMENT_KIND = "delegated_instrument"

#: Why a delegation frame produced no delegation_grants_instrument edge (typed).
NO_INSTRUMENT_IN_FRAME = "no_delegated_instrument_in_frame"


@dataclass(frozen=True)
class UnattachedFrame:
    """A delegation frame that produced NO instrument edge (tagged, never an edge).

    Carried for the differential report / debugging. The reason distinguishes the
    coordinate-bridge miss (the recognizer typed an instrument_kind but no
    construction ``delegated_instrument`` node sits inside the frame) from any future
    cause; today the only reason is ``NO_INSTRUMENT_IN_FRAME``.
    """

    source_unit_id: str
    instrument_kind: str
    frame_char_start: int
    frame_char_end: int
    reason: str


@dataclass
class DelegationInstrumentPass:
    """Edge pass: delegation_frame → the delegated_instrument node(s) it contains.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`. Joins
    EXISTING ``delegation_frame`` (source) and ``delegated_instrument`` (target)
    nodes; it mints no nodes and re-parses nothing. A frame is joined to the
    instrument node(s) whose span sits INSIDE the frame's span in the same source
    unit (the instrument anchor is part of the frame text).

    One contained instrument → ``"asserted"``; several (coordinated grant) → one
    edge per instrument, ``"ambiguous"`` with the full contained set in payload;
    none → typed :class:`UnattachedFrame` diagnostic.

    Determinism: units in declared order; frames by char_start; contained
    instruments by char_start. The assembler recomputes graph_id over the edge set.
    The pass needs no source text (it reads node spans only) but is built per-statute
    for symmetry with the other Layer-2 passes.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_DELEG_INSTRUMENT
    reads_node_kinds: tuple[str, ...] = (
        DELEGATION_FRAME_KIND,
        DELEGATED_INSTRUMENT_KIND,
    )
    emits_edge_kinds: tuple[str, ...] = (EDGE_DELEGATION_GRANTS_INSTRUMENT,)
    # Typed diagnostics for frames that produced no edge. Populated on run(); read by
    # the differential report. NOT a graph element.
    unattached: list[UnattachedFrame] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        frame_index = _node_index_by_unit_kind(graph.nodes, DELEGATION_FRAME_KIND)
        instr_index = _node_index_by_unit_kind(graph.nodes, DELEGATED_INSTRUMENT_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            frames = frame_index.get(unit_id, [])
            if not frames:
                continue
            instruments = instr_index.get(unit_id, [])
            for fid, fref in frames:
                node = graph.nodes[fid]
                instrument_kind = str(node.payload.get("instrument_kind"))
                contained = [
                    (iid, iref)
                    for iid, iref in instruments
                    if fref.char_start <= iref.char_start
                    and iref.char_end <= fref.char_end
                ]
                seeds.extend(
                    self._frame_edges(
                        unit_id=unit_id,
                        frame_id=fid,
                        frame_ref=fref,
                        instrument_kind=instrument_kind,
                        contained=contained,
                    )
                )
        return tuple(seeds)

    def _frame_edges(
        self,
        *,
        unit_id: str,
        frame_id: str,
        frame_ref: SourceSpanRef,
        instrument_kind: str,
        contained: list[tuple[str, SourceSpanRef]],
    ) -> list[SurfaceEdgeSeed]:
        frame_span = [frame_ref.char_start, frame_ref.char_end]
        if not contained:
            self.unattached.append(
                UnattachedFrame(
                    source_unit_id=unit_id,
                    instrument_kind=instrument_kind,
                    frame_char_start=frame_ref.char_start,
                    frame_char_end=frame_ref.char_end,
                    reason=NO_INSTRUMENT_IN_FRAME,
                )
            )
            return []
        ambiguous = len(contained) > 1
        edge_status = "ambiguous" if ambiguous else "asserted"
        candidate_spans = [[iref.char_start, iref.char_end] for _, iref in contained]
        out: list[SurfaceEdgeSeed] = []
        for instr_id, instr_ref in contained:
            payload: dict[str, object] = {
                "frame_instrument_kind": instrument_kind,
                "frame_span": frame_span,
                "instrument_span": [instr_ref.char_start, instr_ref.char_end],
                "attachment": "resolved_by_containment",
                "source": "delegation_instrument_in_frame",
                "experimental": True,
            }
            if ambiguous:
                # a coordinated grant carries several instruments; surface the full
                # contained set so a consumer sees this is one of several — never a
                # silent pick of the nearest.
                payload["attachment"] = "ambiguous_by_containment"
                payload["candidate_instrument_spans"] = candidate_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=EDGE_DELEGATION_GRANTS_INSTRUMENT,
                    src_local=frame_id,
                    dst_local=instr_id,
                    rule_id=RULE_DELEGATION_GRANTS_INSTRUMENT,
                    surface_edge_status=edge_status,
                    payload=payload,
                )
            )
        return out


def delegation_instrument_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[DelegationInstrumentPass, ...]:
    """Build the per-statute delegation_frame → delegated_instrument edge pass.

    Returns a one-tuple so the caller can splice it into the edge-pass sequence
    ADDITIVELY (alongside the condition/exception, delegates_to/sanctioned_by,
    norm-subject, and procedure passes and the proximity incumbents).
    """
    return (DelegationInstrumentPass(units=bundle.units),)


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


# ── sanction_defers_to_provision — the PRINCIPLED sanction attachment index ────
#
# WHY sanctioned_by (above) stays sentence-local CO-OCCURRENCE, not resolved
# ====================================================================
# delegates_to resolves by CONTAINMENT because the delegating verb that mints the
# power core IS part of the delegation_frame span — they are the same syntactic
# event, so a frame whose span contains the core cue is the very instrument that
# power grants. The same move does NOT transfer to sanctions: a sanction_frame's
# span is NOT built around the sanctioned duty's modal core. In the canonical
# Finnish penal construction "Joka <conduct>, on tuomittava … sakkoon" the
# sanctioned conduct is a FINITE INDICATIVE verb in a joka-relative clause
# (vahingoittaa / rikkoo / laiminlyö) — not a modal deontic_core at all — and the
# modal cores that DO fall inside the frame's span (a stray "on" / "ei saa") were
# verified, over a ~400-statute corpus differential, to be COINCIDENTAL: they
# belong to unrelated duties that merely co-locate ("Tavaraa, joka … tuomitaan, on
# hoidettava…"; "joka ei saa olla kolmeakymmentä päivää pitempi"). Cue-containment
# (whether in the frame source_span, its trigger_span, or a joka-clause) is
# therefore spurious for sanctions — candidate-not-asserted forbids minting it as
# resolved. So sanctioned_by KEEPS its honest sentence-local co-occurrence status:
# the duty↔consequence link is not surface-recoverable from the modal-core ↔
# sanction-frame join.
#
# What IS surface-recoverable
# ===========================
# The penal-DEFERRAL construction: "rangaistaan / tuomitaan … [mukaan / niin kuin /
# siten kuin / säädetään / noudatettakoon] §:ssä / luvussa …" — the penalty does
# not define the offence here, it DEFERS to a named provision where the measure /
# breached duty is set out. That provision is a forward REFERENCE that the
# ReferenceLens already minted as a reference_expr node; the closed deferral cue
# between the sanction marker and the reference binds them. This IS a principled
# attachment index (analogous to delegates_to's containment, here resolve-by-
# penal-reference), verified clean by raw-text adjudication of the resolved set.
#
# Candidate-not-asserted (never a silent pick):
#   * a sanction marker with EXACTLY ONE deferral reference after it → ONE edge,
#     status "asserted" (attachment="resolved_by_penal_reference");
#   * SEVERAL deferral references → ONE edge per reference, status "ambiguous",
#     each carrying the full candidate-reference set in payload;
#   * NONE → NO edge (a standalone offence definition with no back-reference,
#     correctly left as sentence-local co-occurrence) — recorded as a typed
#     UnattachedSanction diagnostic, never an invented edge.

EDGE_SANCTION_DEFERS = "sanction_defers_to_provision"

PASS_ID_SANCTION_REF = "fi.norm_composition.sanction_reference.v0"
RULE_SANCTION_DEFERS = "fi.norm_composition.sanction_defers_to_provision"

#: The reference node kind a deferring sanction binds to.
REFERENCE_EXPR_KIND = "reference_expr"

#: Closed deferral-cue tokens that license a penal-deferral attachment. A penalty
#: clause that names a provision via one of these is deferring its measure/offence
#: to that provision (NOT defining it here). Matched case-insensitively as whole
#: tokens (single-word cues via a word boundary; the multi-word cues verbatim).
_DEFERRAL_CUE_WORDS: tuple[str, ...] = (
    "mukaan",       # "… X §:n mukaan" (as provided in §X)
    "mukaista",     # "… tämän asetuksen mukaista …"
    "nojalla",      # "… X §:n nojalla" (under §X)
    "säädetään",    # "… säädetään X luvussa" (is provided in chapter X)
    "säädetty",     # "… josta on säädetty X §:ssä"
    "noudatetaan",  # "… noudatetaan mitä X §:ssä …"
    "noudatettakoon",
    "noudatettavana",
    "mainitun",     # "… X §:ssä mainitun …"
    "mainitussa",
)
#: Multi-word deferral cues (verbatim, case-insensitive).
_DEFERRAL_CUE_PHRASES: tuple[str, ...] = ("niin kuin", "siten kuin")

_DEFERRAL_CUE_RE = re.compile(
    r"(?:(?<![\wäöåÄÖÅ])(?:"
    + "|".join(re.escape(w) for w in _DEFERRAL_CUE_WORDS)
    + r")(?![\wäöåÄÖÅ]))|"
    + "|".join(re.escape(p) for p in _DEFERRAL_CUE_PHRASES),
    re.IGNORECASE,
)

#: Maximum gap (chars) from the sanction marker END to the deferral reference
#: START still read as the SAME penal-deferral construction. Bounds the join to
#: the marker's own clause-ish span; a reference further than this belongs to a
#: different construction. Sized from the corpus differential (most genuine
#: deferrals sit well within this; the rare far ones risk false positives).
_DEFERRAL_MAX_GAP = 120

#: Why a sanction frame produced no penal-deferral edge (the typed diagnostic).
NO_DEFERRAL_REFERENCE = "no_penal_deferral_reference"


@dataclass(frozen=True)
class UnattachedSanction:
    """A sanction frame that produced NO penal-deferral edge (tagged, never an edge).

    Carried for the differential report / debugging. A sanction with no forward
    deferral reference is a STANDALONE offence definition (the penalty IS defined
    here); it is correctly left as sentence-local co-occurrence, NOT forced into a
    fabricated provision link.
    """

    source_unit_id: str
    sanction_kind: str
    frame_char_start: int
    frame_char_end: int
    reason: str


@dataclass
class SanctionReferencePass:
    """Edge pass: sanction_frame → the provision reference its penalty defers to.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`. Built
    per-statute from the bundle units (it re-derives sentence boundaries via
    :func:`build_clause_index` to bound the deferral to the marker's own sentence,
    and reads the source text to test the deferral cue between marker and
    reference). Mints NO nodes; it joins EXISTING ``sanction_frame`` (source) and
    ``reference_expr`` (target) nodes other lenses produced.

    For each sanction frame, a ``reference_expr`` node is a deferral target iff it
    sits in the SAME sentence, STARTS at/after the sanction marker, within
    :data:`_DEFERRAL_MAX_GAP` chars of the marker end, and a closed deferral cue
    (:data:`_DEFERRAL_CUE_RE`) appears in the text between the marker end and the
    reference start (or in a short window just before it). EXACTLY ONE target →
    ``"asserted"`` (resolved_by_penal_reference); several → one edge per target,
    ``"ambiguous"`` with the full set in payload; none → typed
    :class:`UnattachedSanction` diagnostic.

    Determinism: units in declared order; sentences left-to-right; sanction frames
    by char_start; deferral references by char_start. The assembler recomputes the
    graph_id over the edge set.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_SANCTION_REF
    reads_node_kinds: tuple[str, ...] = (
        SANCTION_FRAME_KIND,
        "sanction_cue",
        REFERENCE_EXPR_KIND,
    )
    emits_edge_kinds: tuple[str, ...] = (EDGE_SANCTION_DEFERS,)
    # Typed diagnostics for sanctions with no deferral reference. Populated on
    # run(); read by the differential report. NOT a graph element.
    unattached: list[UnattachedSanction] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        sanct_index = _node_index_by_unit_kind(graph.nodes, SANCTION_FRAME_KINDS)
        ref_index = _node_index_by_unit_kind(graph.nodes, REFERENCE_EXPR_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            sancts = sanct_index.get(unit_id, [])
            if not sancts:
                continue
            refs = ref_index.get(unit_id, [])
            if not refs:
                # No reference nodes at all → every sanction is standalone.
                for sid_, sref in sancts:
                    self._tag(unit_id, graph.nodes[sid_], sref)
                continue
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = (
                    unit.clause_index
                    if isinstance(unit.clause_index, ClauseIndex)
                    else build_clause_index(unit_id, unit.raw_text, token_tape=tape)
                )
            except Exception:
                continue
            sentences = [(s.char_start, s.char_end) for s in index.sentences]
            if not sentences:
                continue
            for sid_, sref in sancts:
                seeds.extend(
                    self._sanction_edges(
                        raw_text=unit.raw_text,
                        unit_id=unit_id,
                        sanction_id=sid_,
                        sanction_ref=sref,
                        sanction_node=graph.nodes[sid_],
                        refs=refs,
                        sentences=sentences,
                    )
                )
        return tuple(seeds)

    def _sanction_edges(
        self,
        *,
        raw_text: str,
        unit_id: str,
        sanction_id: str,
        sanction_ref: SourceSpanRef,
        sanction_node: SurfaceNode,
        refs: list[tuple[str, SourceSpanRef]],
        sentences: list[tuple[int, int]],
    ) -> list[SurfaceEdgeSeed]:
        # The sanction marker is anchored at the frame node's source_ref. The frame
        # span may include a preceding target actor; the MARKER (where the penalty
        # verb/noun sits, the deferral's left anchor) is the marker_surface at the
        # frame's char_start..char_end OR, when an actor pulled the frame back, the
        # marker token itself. We use the frame's span as the deferral left anchor:
        # the reference must start at/after it. (A reference inside the actor prefix
        # would be a CITED actor, not the penalty's deferral — excluded by the
        # at/after-marker test below using the marker, derived next.)
        marker_lo, marker_hi = self._marker_bounds(raw_text, sanction_node, sanction_ref)
        sent_i = _sentence_of(marker_lo, sentences)
        if sent_i < 0:
            self._tag(unit_id, sanction_node, sanction_ref)
            return []
        sent_lo, sent_hi = sentences[sent_i]

        targets: list[tuple[str, SourceSpanRef]] = []
        for rid, rref in refs:
            # skip degenerate (unlocatable) char anchors — no real span to bind.
            if rref.char_start == rref.char_end:
                continue
            # same sentence, forward of the marker, within the gap window.
            if rref.char_start < marker_hi or rref.char_start >= sent_hi:
                continue
            if rref.char_start - marker_hi > _DEFERRAL_MAX_GAP:
                continue
            # a closed deferral cue must bind the marker to the reference — this is
            # what distinguishes a penal DEFERRAL from a sanction that merely
            # co-occurs with a citation. Two constructions:
            #   * PRE-cue:  "rangaistaan niin kuin / siten kuin / … §:ssä säädetään"
            #     — the cue sits between the marker and the reference.
            #   * POST-cue: "rangaistaan … §:n mukaan / §:n nojalla" — the cue is a
            #     POSTPOSITION immediately AFTER the reference.
            gap_text = raw_text[marker_hi : rref.char_start]
            post_text = raw_text[rref.char_end : min(sent_hi, rref.char_end + 24)]
            if _DEFERRAL_CUE_RE.search(gap_text) or _DEFERRAL_CUE_RE.search(post_text):  # lawvm-regex: owning_parser closed deferral-cue predicate over gap_text/post_text slices of the SanctionReferencePass's OWN bundle unit.raw_text (the §D4 lens surface plane, not the apply/replay legal-state plane); drives the witnessed EDGE_SANCTION_DEFERS edge / typed UnattachedSanction diagnostic
                targets.append((rid, rref))

        if not targets:
            self._tag(unit_id, sanction_node, sanction_ref)
            return []

        targets.sort(key=lambda kv: (kv[1].char_start, kv[1].char_end, kv[0]))
        sanction_kind = str(sanction_node.payload.get("sanction_kind"))
        frame_span = [sanction_ref.char_start, sanction_ref.char_end]
        # PRINCIPLED: exactly one deferral reference → resolved-by-penal-reference
        # ("asserted"); several → ambiguous (one edge per reference, full set).
        ambiguous = len(targets) > 1
        edge_status = "ambiguous" if ambiguous else "asserted"
        candidate_spans = [[r.char_start, r.char_end] for _, r in targets]
        out: list[SurfaceEdgeSeed] = []
        for rid, rref in targets:
            payload: dict[str, object] = {
                "sanction_kind": sanction_kind,
                "frame_span": frame_span,
                "marker_span": [marker_lo, marker_hi],
                "reference_span": [rref.char_start, rref.char_end],
                "attachment": (
                    "ambiguous_by_penal_reference"
                    if ambiguous
                    else "resolved_by_penal_reference"
                ),
                "source": "sanction_penal_deferral",
                "experimental": True,
            }
            if ambiguous:
                payload["candidate_reference_spans"] = candidate_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=EDGE_SANCTION_DEFERS,
                    src_local=sanction_id,
                    dst_local=rid,
                    rule_id=RULE_SANCTION_DEFERS,
                    surface_edge_status=edge_status,
                    payload=payload,
                )
            )
        return out

    def _marker_bounds(
        self, raw_text: str, node: SurfaceNode, ref: SourceSpanRef
    ) -> tuple[int, int]:
        """The sanction MARKER span (the penalty verb/noun) in raw_text coords.

        The sanction_frame source_ref span may start at a PRECEDING target actor
        (when the recognizer found one), so its char_start is not the penalty
        marker. The marker surface is carried verbatim in the node payload; we
        locate that surface within the frame span to recover the marker's own
        offsets — the deferral's left anchor (the reference must come AFTER the
        penalty verb, not inside the cited-actor prefix). Falls back to the frame
        span when the marker surface cannot be located (defensive; never crashes).
        """
        marker = node.payload.get("marker_surface")
        if isinstance(marker, str) and marker:
            found = raw_text.find(marker, ref.char_start, ref.char_end)
            if found >= 0:
                return found, found + len(marker)
        return ref.char_start, ref.char_end

    def _tag(
        self, unit_id: str, node: SurfaceNode, ref: SourceSpanRef
    ) -> None:
        self.unattached.append(
            UnattachedSanction(
                source_unit_id=unit_id,
                sanction_kind=str(node.payload.get("sanction_kind")),
                frame_char_start=ref.char_start,
                frame_char_end=ref.char_end,
                reason=NO_DEFERRAL_REFERENCE,
            )
        )


def sanction_reference_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[SanctionReferencePass, ...]:
    """Build the per-statute sanction_frame → deferral-provision reference pass.

    Returns a one-tuple so the caller can splice it into the edge-pass sequence
    ADDITIVELY (alongside the condition/exception, delegates_to/sanctioned_by,
    norm-subject, procedure, and delegation-instrument passes).
    """
    return (SanctionReferencePass(units=bundle.units),)


# ── norm_has_subject — bind each deontic core to its norm SUBJECT/addressee ────
#
# The highest-EV, most self-contained Layer-2 deontic edge: the modal parse
# already records, per core, the overt SUBJECT NP span (``addressee_span``) and
# whether the addressee is UNDERSPECIFIED (the impersonal/passive register —
# ``säädetään`` / ``on tehtävä`` with no overt subject). This pass binds the core
# to the ACTOR node that owns that subject text — the production
# ``actor_modal_frame`` node whose span COVERS the core's addressee span (the
# production actor recognizer typed that very subject NP). It does NOT re-parse the
# subject; it consumes the addressee span the modal parse already computed and joins
# it to an existing actor node.
#
# Candidate-not-asserted (never a silent pick, never a fabricated subject):
#   * addressee UNDERSPECIFIED (passive/impersonal) → NO edge; a typed
#     ``UnattachedCore`` (``SUBJECT_UNDERSPECIFIED``) diagnostic. The text fixes no
#     subject; the pass invents none.
#   * addressee span present + EXACTLY ONE actor_modal_frame covers it → ONE edge,
#     status ``"candidate"`` (a surface co-reference, not a legal-subject claim);
#   * addressee span present + SEVERAL covering actor frames → ONE edge per frame,
#     status ``"ambiguous"``, full candidate set in payload;
#   * addressee span present + NO covering actor frame (the production actor
#     recognizer emitted no node for this subject NP — a coordinate-bridge miss) →
#     NO edge; a typed ``UnattachedCore`` (``NO_SUBJECT_NODE_FOR_ADDRESSEE``)
#     diagnostic, never an invented subject.

EDGE_NORM_HAS_SUBJECT = "norm_has_subject"

PASS_ID_NORM_SUBJECT = "fi.norm_composition.norm_subject.v0"
RULE_NORM_HAS_SUBJECT = "fi.norm_composition.norm_has_subject"

#: The actor node kind a deontic core's subject binds to.
ACTOR_FRAME_KIND = "actor_modal_frame"

#: Why a deontic core produced no norm_has_subject edge (typed diagnostics).
SUBJECT_UNDERSPECIFIED = "subject_underspecified"
NO_SUBJECT_NODE_FOR_ADDRESSEE = "no_actor_node_for_addressee_span"


def _spans_cover(outer: SourceSpanRef, start: int, end: int) -> bool:
    """True when ``outer`` fully covers ``[start, end)`` (raw_text coordinates)."""
    return outer.char_start <= start and end <= outer.char_end


def _int_pair(value: object) -> tuple[int, int] | None:
    """Narrow an ``object`` payload value to an ``[int, int]`` span pair, or None.

    Node payload spans are typed ``object`` (the payload Mapping is untyped at the
    value level), so this guards the addressee-span read before arithmetic.
    """
    if not isinstance(value, list) or len(value) != 2:
        return None
    first, second = value[0], value[1]
    if isinstance(first, int) and isinstance(second, int):
        return first, second
    return None


@dataclass
class NormSubjectAttachmentPass:
    """Edge pass: deontic core → its norm SUBJECT (actor_modal_frame) NORM edge.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`. Joins
    EXISTING ``deontic_core`` (source) and ``actor_modal_frame`` (target) nodes; it
    mints no nodes and re-parses nothing. The core's ``addressee_span`` (computed by
    the modal parse) is matched to the actor frame that COVERS it.

    Underspecified (impersonal/passive) cores get a typed diagnostic, never a
    fabricated subject; a present addressee with no covering actor node gets a
    typed coordinate-bridge diagnostic. One covering actor → ``"candidate"``;
    several → one edge per actor, ``"ambiguous"`` with the full set in payload.

    Determinism: units in declared order; cores by char_start; covering actor
    frames by char_start. The assembler recomputes graph_id over the edge set. The
    pass needs no source text (it reads node payload spans only), but is built per-
    statute for symmetry with the other Layer-2 passes.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_NORM_SUBJECT
    reads_node_kinds: tuple[str, ...] = (CORE_NODE_KIND, ACTOR_FRAME_KIND)
    emits_edge_kinds: tuple[str, ...] = (EDGE_NORM_HAS_SUBJECT,)
    # Typed diagnostics for cores that produced no subject edge. NOT a graph
    # element; read by the differential report.
    unattached: list[UnattachedCore] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)
        actor_index = _node_index_by_unit_kind(graph.nodes, ACTOR_FRAME_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            cores = core_index.get(unit_id, [])
            if not cores:
                continue
            actors = actor_index.get(unit_id, [])
            for nid, ref in cores:
                node = graph.nodes[nid]
                seeds.extend(
                    self._core_subject_edges(
                        unit_id=unit_id,
                        core_id=nid,
                        core_ref=ref,
                        payload=node.payload,
                        actors=actors,
                    )
                )
        return tuple(seeds)

    def _core_subject_edges(
        self,
        *,
        unit_id: str,
        core_id: str,
        core_ref: SourceSpanRef,
        payload: Mapping[str, object],
        actors: list[tuple[str, SourceSpanRef]],
    ) -> list[SurfaceEdgeSeed]:
        core_kind = str(payload.get("kind"))
        core_span = [core_ref.char_start, core_ref.char_end]

        # Underspecified addressee (impersonal/passive register): the text fixes no
        # subject. Tag it; never invent one.
        if payload.get("addressee_underspecified"):
            self._tag_subject(unit_id, core_kind, core_ref, SUBJECT_UNDERSPECIFIED)
            return []

        addressee = payload.get("addressee_span")
        a_span = _int_pair(addressee)
        if a_span is None:
            # Neither an overt int span nor flagged underspecified — defensive; treat
            # as underspecified (no subject to bind).
            self._tag_subject(unit_id, core_kind, core_ref, SUBJECT_UNDERSPECIFIED)
            return []
        a_start, a_end = a_span

        # The actor node(s) whose span covers the addressee NP — the production
        # recognizer typed THIS subject. Deterministic by char_start.
        covering = [
            (aid, aref) for aid, aref in actors if _spans_cover(aref, a_start, a_end)
        ]
        if not covering:
            # The modal parse found an overt subject NP, but no actor_modal_frame
            # node backs it (coordinate-bridge miss). No asserted subject.
            self._tag_subject(
                unit_id, core_kind, core_ref, NO_SUBJECT_NODE_FOR_ADDRESSEE
            )
            return []

        ambiguous = len(covering) > 1
        edge_status = "ambiguous" if ambiguous else "candidate"
        candidate_spans = [[aref.char_start, aref.char_end] for _, aref in covering]
        out: list[SurfaceEdgeSeed] = []
        for actor_id, actor_ref in covering:
            payload_out: dict[str, object] = {
                "core_kind": core_kind,
                "core_span": core_span,
                "addressee_span": [a_start, a_end],
                "actor_span": [actor_ref.char_start, actor_ref.char_end],
                "source": "deontic_core_addressee",
                "experimental": True,
            }
            if ambiguous:
                payload_out["candidate_actor_spans"] = candidate_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=EDGE_NORM_HAS_SUBJECT,
                    src_local=core_id,
                    dst_local=actor_id,
                    rule_id=RULE_NORM_HAS_SUBJECT,
                    surface_edge_status=edge_status,
                    payload=payload_out,
                )
            )
        return out

    def _tag_subject(
        self,
        unit_id: str,
        core_kind: str,
        core_ref: SourceSpanRef,
        reason: str,
    ) -> None:
        self.unattached.append(
            UnattachedCore(
                source_unit_id=unit_id,
                edge_kind=EDGE_NORM_HAS_SUBJECT,
                core_kind=core_kind,
                core_char_start=core_ref.char_start,
                core_char_end=core_ref.char_end,
                reason=reason,
            )
        )


def norm_subject_attachment_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[NormSubjectAttachmentPass, ...]:
    """Build the per-statute deontic-core → norm-subject (norm_has_subject) pass.

    Returns a one-tuple so the caller can splice it into the edge-pass sequence
    ADDITIVELY (alongside the condition/exception, delegates_to/sanctioned_by, and
    procedure passes and the proximity incumbents).
    """
    return (NormSubjectAttachmentPass(units=bundle.units),)


# ── governed_by_procedure — obligation/power core → co-sentence procedure_frame ─
#
# Same Layer-2 family + discipline as delegates_to / sanctioned_by (sentence-local,
# candidate-not-asserted, additive, surface_only firewall), on the SAME dense
# ``deontic_core`` substrate, joining to the EXISTING ``procedure_frame`` node the
# H5 ProcedureLens already mints (verified to exist). An obligation/power core and a
# procedure_frame in the SAME sentence co-occur in one provision: the process the
# duty/power runs through. There is NO construction parse emitting a (core →
# procedure_frame) attachment index, so — unlike delegates_to — there is no
# principled containment to resolve on; this stays a sentence-local co-occurrence
# candidate (one target "candidate"; several "ambiguous", full set in payload; none
# → typed UnattachedCore diagnostic).

EDGE_GOVERNED_BY_PROCEDURE = "governed_by_procedure"

PASS_ID_PROCEDURE = "fi.norm_composition.procedure.v0"
RULE_GOVERNED_BY_PROCEDURE = "fi.norm_composition.governed_by_procedure"

#: The procedure node kind this pass joins (the H5 ProcedureLens node).
PROCEDURE_FRAME_KIND = "procedure_frame"
#: A bare process noun is demoted to ``procedure_cue`` (no actor / no deadline)
#: but carries the SAME span + ``process_kind`` a ``procedure_frame`` does. This
#: span-based co-occurrence pass admits BOTH so its edge set is unchanged.
PROCEDURE_FRAME_KINDS: frozenset[str] = frozenset(
    {PROCEDURE_FRAME_KIND, "procedure_cue"}
)

#: deontic-core ``kind`` values that license a governed_by_procedure edge.
_PROCEDURE_GOVERNED_KINDS = frozenset({"obligation", "power"})


@dataclass
class ProcedureGovernancePass:
    """Edge pass: obligation/power core → co-sentence procedure_frame NORM edge.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`. Built
    per-statute from the bundle units (re-derives sentence boundaries via
    :func:`build_clause_index` to bound the join to one sentence). Mints NO nodes;
    joins EXISTING ``deontic_core`` (source) and ``procedure_frame`` (target) nodes.
    The same candidate/ambiguous/diagnostic discipline as
    :class:`DeonticFrameAttachmentPass`.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_PROCEDURE
    reads_node_kinds: tuple[str, ...] = (
        CORE_NODE_KIND,
        PROCEDURE_FRAME_KIND,
        "procedure_cue",
    )
    emits_edge_kinds: tuple[str, ...] = (EDGE_GOVERNED_BY_PROCEDURE,)
    unattached: list[UnattachedCore] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)
        proc_index = _node_index_by_unit_kind(graph.nodes, PROCEDURE_FRAME_KINDS)

        seeds: list[SurfaceEdgeSeed] = []
        for unit in self.units:
            unit_id = unit.source_unit_id
            cores = core_index.get(unit_id, [])
            if not cores:
                continue
            procs = proc_index.get(unit_id, [])
            if not procs:
                continue
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = (
                    unit.clause_index
                    if isinstance(unit.clause_index, ClauseIndex)
                    else build_clause_index(unit_id, unit.raw_text, token_tape=tape)
                )
            except Exception:
                continue
            sentences = [(s.char_start, s.char_end) for s in index.sentences]
            if not sentences:
                continue
            proc_by_sent = _bucket_frames_by_sentence(procs, sentences)
            for nid, ref in cores:
                node = graph.nodes[nid]
                core_kind = node.payload.get("kind")
                if core_kind not in _PROCEDURE_GOVERNED_KINDS:
                    continue
                sent_i = _sentence_of(ref.char_start, sentences)
                if sent_i < 0:
                    continue
                seeds.extend(
                    self._core_procedure_edges(
                        unit_id=unit_id,
                        core_id=nid,
                        core_ref=ref,
                        core_kind=str(core_kind),
                        targets=proc_by_sent.get(sent_i, []),
                    )
                )
        return tuple(seeds)

    def _core_procedure_edges(
        self,
        *,
        unit_id: str,
        core_id: str,
        core_ref: SourceSpanRef,
        core_kind: str,
        targets: list[tuple[str, SourceSpanRef]],
    ) -> list[SurfaceEdgeSeed]:
        if not targets:
            self.unattached.append(
                UnattachedCore(
                    source_unit_id=unit_id,
                    edge_kind=EDGE_GOVERNED_BY_PROCEDURE,
                    core_kind=core_kind,
                    core_char_start=core_ref.char_start,
                    core_char_end=core_ref.char_end,
                    reason=NO_FRAME_IN_SENTENCE,
                )
            )
            return []
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
                "source": "deontic_procedure_sentence_local",
                "experimental": True,
            }
            if ambiguous:
                payload["candidate_frame_spans"] = candidate_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=EDGE_GOVERNED_BY_PROCEDURE,
                    src_local=core_id,
                    dst_local=frame_id,
                    rule_id=RULE_GOVERNED_BY_PROCEDURE,
                    surface_edge_status=edge_status,
                    payload=payload,
                )
            )
        return out


def _bucket_frames_by_sentence(
    frames: list[tuple[str, SourceSpanRef]],
    sentences: list[tuple[int, int]],
) -> dict[int, list[tuple[str, SourceSpanRef]]]:
    """Group frame (id, ref) pairs by EVERY sentence their span overlaps.

    Module-level twin of :meth:`DeonticFrameAttachmentPass._bucket_by_sentence`,
    shared by the procedure pass. A frame span (the whole recognised clause) can
    straddle a sentence boundary, so it may land in more than one bucket. Frames
    within each bucket stay sorted by (char_start, char_end, id).
    """
    buckets: dict[int, list[tuple[str, SourceSpanRef]]] = {}
    for fid, fref in frames:
        for si in _sentences_overlapped(fref.char_start, fref.char_end, sentences):
            buckets.setdefault(si, []).append((fid, fref))
    return buckets


def procedure_governance_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[ProcedureGovernancePass, ...]:
    """Build the per-statute obligation/power core → procedure_frame pass.

    Returns a one-tuple so the caller can splice it into the edge-pass sequence
    ADDITIVELY.
    """
    return (ProcedureGovernancePass(units=bundle.units),)


# ── enclosing-section anaphora — Tätä pykälää/momenttia ei sovelleta … ─────────
#
# The intra-sentence pass attaches a qualifier to a deontic core in its OWN
# sentence; the cross-sentence pass attaches a back-reference EXCEPTION cue
# ("sen estämättä mitä N §:ssä …") to the INTERNAL reference it names. NEITHER
# can resolve an ENCLOSING-PROVISION ANAPHOR —
#   "Tätä pykälää ei sovelleta …"      (this SECTION is not applied …)
#   "Tätä momenttia ei kuitenkaan sovelleta …"  (this SUBSECTION …)
#   "Tämän pykälän estämättä …"        (notwithstanding this section …)
# — because the anaphor's referent is the SECTION/SUBSECTION it itself sits in,
# a structural identity the flattened body decode drops (no <num> markers in the
# coordinate space). The provision-boundary substrate now restores it: the unit's
# ``metadata["provision_index"]`` answers ``provision_at(char_start, char_end) ->
# ProvisionSpan`` (the enclosing §/momentti of any char span). The anaphor's
# enclosing provision is THE referent; its deontic cores are the norm(s) the
# qualifier scopes.
#
# This is a STRICT SUPERSET of the intra/cross-sentence behaviour — it fires ONLY
# on the closed enclosing-anaphor cue shapes below (which the condition/exception
# construction does NOT key on, so they were never attached by the prior passes)
# and NEVER alters an existing edge. It runs as its OWN pass (its own pass_id /
# edge kinds reused: condition_attaches_norm / exception_excepts_norm), keyed off
# the provision_index rather than the construction parse.
#
# Scope-granularity (candidate-not-asserted, never over-attach):
#   * ``pykälää`` / ``pykälän`` → SECTION scope: attach to every deontic core whose
#     enclosing provision has the SAME ``section_label`` (any subsection).
#   * ``momenttia`` / ``momentin`` → SUBSECTION scope: attach to every core whose
#     enclosing provision has the same ``section_label`` AND ``subsection_num`` —
#     and ONLY when the anaphor's own enclosing provision carries a
#     ``subsection_num`` (else the target momentti is unidentifiable → diagnostic).
#   * ``lakia`` / ``lain`` → WHOLE-LAW scope: too broad to be a single attachment
#     target (it would attach to every core in the statute). Left a typed
#     diagnostic, never over-attached.
#
# Resolution outcomes (mirroring the family discipline):
#   * the enclosing provision (correctly scoped) has EXACTLY ONE matching core →
#     ONE edge, status "asserted" (``attachment=resolved_by_enclosing_provision``);
#   * SEVERAL matching cores (a multi-subsection section, several cores in the
#     momentti) → ONE edge per core, status "ambiguous", full candidate-core set
#     in payload — the qualifier scopes the whole provision, the consumer sees
#     every core it covers (never a silent pick of one);
#   * the enclosing provision has NO deontic core → NO edge, a typed
#     :data:`NO_CORE_IN_ENCLOSING_PROVISION` diagnostic;
#   * ``provision_at`` returns AMBIGUOUS (the cue span crosses a provision boundary
#     / lands on a between-paragraph gap) → typed
#     :data:`AMBIGUOUS_ENCLOSING_PROVISION` diagnostic;
#   * whole-law anaphor → typed :data:`ENCLOSING_SCOPE_WHOLE_LAW` diagnostic;
#   * momentti anaphor whose enclosing provision has no ``subsection_num`` →
#     typed :data:`NO_SUBSECTION_FOR_MOMENTTI_ANAPHOR` diagnostic.
#
# The cue surface is recorded but it is NOT a legal conclusion — surface_only /
# replay_authorized=False holds (the assembler mints every edge that way).

PASS_ID_ENCLOSING = "fi.norm_composition.enclosing_anaphora.v0"

#: The attachment reason that rides in the edge payload's ``attachment`` field,
#: distinguishing an enclosing-anaphor resolution from intra/cross-sentence ones.
ATTACHMENT_RESOLVED_BY_ENCLOSING = "resolved_by_enclosing_provision"
ATTACHMENT_AMBIGUOUS_BY_ENCLOSING = "ambiguous_by_enclosing_provision"

#: Why an enclosing-anaphor cue produced NO asserted edge (typed diagnostics).
NO_CORE_IN_ENCLOSING_PROVISION = "no_deontic_core_in_enclosing_provision"
AMBIGUOUS_ENCLOSING_PROVISION = "ambiguous_enclosing_provision_for_anaphor"
ENCLOSING_SCOPE_WHOLE_LAW = "enclosing_anaphor_scope_whole_law_too_broad"
NO_SUBSECTION_FOR_MOMENTTI_ANAPHOR = "no_subsection_num_for_momentti_anaphor"
NO_PROVISION_INDEX_FOR_UNIT = "no_provision_index_for_unit"

#: The enclosing-anaphor named scopes (the referent level the cue carries — set by
#: the :class:`~lawvm.finland.legal_surface.lenses.enclosing_anaphora.EnclosingAnaphoraLens`
#: in the node payload's ``anaphor_scope`` field).
_SCOPE_SECTION = "section"
_SCOPE_SUBSECTION = "subsection"
_SCOPE_WHOLE_LAW = "whole_law"

#: The source node this pass joins: the ``enclosing_anaphor_cue`` node the
#: EnclosingAnaphoraLens mints (one per determiner+noun+matrix cue, carrying its
#: named scope + spans in the payload). DISTINCT from the H6 ``exception_condition_cue``
#: node, so the H6 cue census is untouched.
ENCLOSING_CUE_NODE_KIND = "enclosing_anaphor_cue"


@dataclass(frozen=True)
class UnattachedAnaphor:
    """An enclosing-provision anaphor cue that produced NO asserted edge (tagged).

    Carried for the differential report — never an edge. ``scope`` is the named
    referent level (``section`` / ``subsection`` / ``whole_law``); ``reason`` is
    one of the typed enclosing-anaphor diagnostics.
    """

    source_unit_id: str
    kind: str
    cue: str
    scope: str
    cue_char_start: int
    cue_char_end: int
    reason: str


def _cores_in_provision(
    core_nodes: list[tuple[str, SourceSpanRef]],
    pidx: ProvisionIndex,
    target: ProvisionSpan,
    *,
    subsection_scoped: bool,
) -> list[tuple[str, SourceSpanRef]]:
    """The deontic-core nodes whose enclosing provision matches ``target``'s scope.

    SECTION scope (``subsection_scoped=False``): same ``section_label`` (any
    subsection). SUBSECTION scope: same ``section_label`` AND ``subsection_num``.
    A core whose own ``provision_at`` is AMBIGUOUS / unmapped / lacks a section
    label is excluded (never matched on a missing identity — fail by absence).
    Returned in source order (the index already sorts by char_start).
    """
    out: list[tuple[str, SourceSpanRef]] = []
    for nid, ref in core_nodes:
        cres = pidx.provision_at(ref.char_start, ref.char_end)
        if cres is AMBIGUOUS or not isinstance(cres, ProvisionSpan):
            continue
        if not cres.mapped or not cres.section_label:
            continue
        if cres.section_label != target.section_label:
            continue
        if subsection_scoped and cres.subsection_num != target.subsection_num:
            continue
        out.append((nid, ref))
    return out


def _int_span(value: object) -> tuple[int, int] | None:
    """Read a ``[start, end]`` payload span into a typed pair (or None)."""
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        return int(value[0]), int(value[1])
    return None


@dataclass
class EnclosingAnaphoraPass:
    """Edge pass: enclosing-provision anaphor → the cores of its OWN provision.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`. Built
    per-statute from the bundle units (it needs each unit's ``provision_index``
    metadata to locate the anaphor's enclosing provision). Mints NO nodes; it
    joins the EXISTING ``enclosing_anaphor_cue`` (source, minted by the
    EnclosingAnaphoraLens) and ``deontic_core`` (target) nodes, scoping the target
    set to the enclosing provision the :class:`ProvisionIndex` reports for the
    anaphor cue's char span.

    STRICT SUPERSET of the intra/cross-sentence passes: it fires only on the
    closed enclosing-anaphor cue nodes (whose cue shapes those passes do not key
    on) and never alters an existing edge. candidate-not-asserted throughout.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_ENCLOSING
    reads_node_kinds: tuple[str, ...] = (ENCLOSING_CUE_NODE_KIND, CORE_NODE_KIND)
    emits_edge_kinds: tuple[str, ...] = (
        EDGE_CONDITION_ATTACHES,
        EDGE_EXCEPTION_EXCEPTS,
    )
    # Typed diagnostics for anaphor cues that produced no asserted edge. Populated
    # on run(); read by the differential report. NOT a graph element.
    unattached: list[UnattachedAnaphor] = field(default_factory=list)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        self.unattached = []
        anaphor_index = _node_index_by_unit_kind(graph.nodes, ENCLOSING_CUE_NODE_KIND)
        core_index = _node_index_by_unit_kind(graph.nodes, CORE_NODE_KIND)
        pidx_by_unit = {
            u.source_unit_id: u.metadata.get("provision_index") for u in self.units
        }

        seeds: list[SurfaceEdgeSeed] = []
        for unit_id, anaphor_nodes in sorted(anaphor_index.items()):
            pidx = pidx_by_unit.get(unit_id)
            core_nodes = core_index.get(unit_id, [])
            for nid, ref in anaphor_nodes:
                seeds.extend(
                    self._cue_edges(
                        graph.nodes[nid].payload,
                        cue_ref=ref,
                        cue_node_id=nid,
                        unit_id=unit_id,
                        pidx=pidx,
                        core_nodes=core_nodes,
                    )
                )
        return tuple(seeds)

    def _cue_edges(
        self,
        payload: Mapping[str, object],
        *,
        cue_ref: SourceSpanRef,
        cue_node_id: str,
        unit_id: str,
        pidx: object,
        core_nodes: list[tuple[str, SourceSpanRef]],
    ) -> list[SurfaceEdgeSeed]:
        kind = payload.get("qualifier_kind")
        scope = payload.get("anaphor_scope")
        cue = payload.get("cue")
        if not isinstance(kind, str) or not isinstance(scope, str) or not isinstance(cue, str):
            return []
        # Resolve the anaphor cue's OWN char span to query the provision index. The
        # lens carries the determiner+noun span; the cue node's source_ref covers
        # the whole determiner..matrix run. Use the determiner+noun span (where the
        # anaphor sits) for the enclosing-provision query.
        det_noun = _int_span(payload.get("det_noun_span")) or (
            cue_ref.char_start,
            cue_ref.char_end,
        )
        cue_abs_start, cue_abs_end = det_noun

        # Whole-law scope is too broad to be a single attachment target.
        if scope == _SCOPE_WHOLE_LAW:
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      ENCLOSING_SCOPE_WHOLE_LAW)
            return []
        if not isinstance(pidx, ProvisionIndex):
            # No provision substrate for this unit → cannot resolve (never guess
            # without the boundary index).
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      NO_PROVISION_INDEX_FOR_UNIT)
            return []

        target = pidx.provision_at(cue_abs_start, cue_abs_end)
        if target is AMBIGUOUS or not isinstance(target, ProvisionSpan):
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      AMBIGUOUS_ENCLOSING_PROVISION)
            return []
        if not target.mapped or not target.section_label:
            # The enclosing provision carries no §-level identity (the anaphor sits
            # above the section level, e.g. a chapter chapeau). Not a resolvable
            # section/subsection referent.
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      AMBIGUOUS_ENCLOSING_PROVISION)
            return []
        if scope == _SCOPE_SUBSECTION and target.subsection_num is None:
            # "Tätä momenttia" but the enclosing provision span has no momentti
            # number — the target momentti is unidentifiable. Diagnostic, not a
            # section-wide over-attach (never widen the scope to guess).
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      NO_SUBSECTION_FOR_MOMENTTI_ANAPHOR)
            return []

        subsection_scoped = scope == _SCOPE_SUBSECTION
        matched = _cores_in_provision(
            core_nodes, pidx, target, subsection_scoped=subsection_scoped
        )
        if not matched:
            self._tag(unit_id, kind, cue, scope, cue_abs_start, cue_abs_end,
                      NO_CORE_IN_ENCLOSING_PROVISION)
            return []

        ambiguous = len(matched) > 1
        edge_status = "ambiguous" if ambiguous else "asserted"
        attachment = (
            ATTACHMENT_AMBIGUOUS_BY_ENCLOSING
            if ambiguous
            else ATTACHMENT_RESOLVED_BY_ENCLOSING
        )
        edge_kind = _KIND_EDGE[kind]
        rule_id = _KIND_RULE[kind]
        candidate_core_spans = [[r.char_start, r.char_end] for _, r in matched]
        out: list[SurfaceEdgeSeed] = []
        for nid, ref in matched:
            edge_payload: dict[str, object] = {
                "qualifier_kind": kind,
                "cue": cue,
                "anaphor_scope": scope,
                "attachment": attachment,
                "cue_span": [cue_abs_start, cue_abs_end],
                "core_span": [ref.char_start, ref.char_end],
                "enclosing_provision": target.provision_path(),
                "enclosing_provision_eid": target.eid,
                "source": "construction_enclosing_anaphora",
                "experimental": True,
            }
            if ambiguous:
                # full candidate set — the qualifier scopes the whole enclosing
                # provision; the consumer sees every core it covers.
                edge_payload["candidate_core_spans"] = candidate_core_spans
            out.append(
                SurfaceEdgeSeed(
                    edge_kind=edge_kind,
                    src_local=cue_node_id,
                    dst_local=nid,
                    rule_id=rule_id,
                    surface_edge_status=edge_status,
                    payload=edge_payload,
                )
            )
        return out

    def _tag(
        self,
        unit_id: str,
        kind: str,
        cue: str,
        scope: str,
        cue_abs_start: int,
        cue_abs_end: int,
        reason: str,
    ) -> None:
        self.unattached.append(
            UnattachedAnaphor(
                source_unit_id=unit_id,
                kind=kind,
                cue=cue,
                scope=scope,
                cue_char_start=cue_abs_start,
                cue_char_end=cue_abs_end,
                reason=reason,
            )
        )


def enclosing_anaphora_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[EnclosingAnaphoraPass, ...]:
    """Build the per-statute enclosing-section anaphora edge pass(es).

    Needs the source text + each unit's ``provision_index`` metadata, so it is
    constructed from the bundle units. Returns a one-tuple so the caller can
    splice it into the edge-pass sequence ADDITIVELY (alongside the
    condition/exception, cross-sentence, and proximity incumbents).
    """
    return (EnclosingAnaphoraPass(units=bundle.units),)


# ── forest-structural attachment — fix the sentence-local proximity blind spot ─
#
# WHY this pass exists (the proximity / sentence-local mis-attachment it fixes)
# ============================================================================
# The intra-sentence :class:`ConditionAttachmentPass` attaches a qualifier to a
# deontic core in its OWN clause-segmented SENTENCE. Its attachment index comes
# from :func:`parse_condition_exception_sentence`, whose ``_attach`` heuristic is a
# PROXIMITY rule: one core in the sentence → resolved; several → the NEAREST core
# by char-distance flagged ``ambiguous``; ZERO cores → ``candidate`` (no edge, a
# typed ``NO_CORE_IN_SENTENCE`` diagnostic).
#
# The ``candidate`` (zero-core) case is the blind spot. ``build_clause_index``
# splits a provision at every clause boundary (``,;.\n``), so the SENTENCE a cue
# lands in routinely loses the governing norm's modal core to a NEIGHBOURING
# sentence in the SAME provision:
#
#   "Säilytystilassa tulee olla … vastaava henkilö. Tämä ei kuitenkaan ole
#    tarpeen, jos …"
#       — the ``ei kuitenkaan`` exception's governing norm (``tulee olla``) sits in
#         the PREVIOUS sentence; the cue's own sentence carries no modal core, so
#         the intra-sentence pass tags it ``candidate`` and emits NO edge.
#
#   chapeau ``… säädetään:`` + list items ``1) … jollei …`` — a list-item
#   condition/exception whose governing norm is the CHAPEAU's frame, a different
#   structural segment again.
#
# The FOREST (:func:`assemble_source_syntax_graph`) carries the structure the
# clause-segmented sentence loses: the ``prose`` / ``chapeau`` / ``list_item``
# STRUCTURAL SEGMENT a cue sits in, and the ``inherits_chapeau`` edge binding a
# list item to its governing chapeau. The deontic core(s) in the cue's enclosing
# structural segment (or, for a frame-less list item, in its governing chapeau) are
# the SYNTACTIC attachment target the proximity sentence split dropped. This pass
# recovers exactly that.
#
# STRICT ADDITION over the intra/cross-sentence/enclosing passes
# ==============================================================
# It fires ONLY for a qualifier the intra-sentence pass leaves ``candidate`` (zero
# core in its sentence) — never re-deciding a ``resolved`` / ``ambiguous`` intra-
# sentence attachment — and it skips the closed back-reference EXCEPTION cues the
# cross-sentence pass owns (so the two never compete for one cue). Because it only
# attaches PREVIOUSLY-EDGELESS candidates, it is NEW-BETTER with 0 regressions BY
# CONSTRUCTION. Its edges carry a distinct ``rule_id`` and ``attachment`` reason, so
# they never collide with another pass's edge id.
#
# candidate-not-asserted + no-silent-drop + proximity fallback (the §discipline):
#   * EXACTLY ONE deontic core in the cue's enclosing structural segment → ONE
#     edge, status ``"asserted"`` (``attachment=resolved_by_forest_segment``);
#   * a frame-less ``list_item`` with NO in-item core but core(s) in its governing
#     chapeau → the chapeau cores (``attachment=resolved_by_chapeau_inheritance``
#     when one, ``ambiguous_by_chapeau_inheritance`` when several);
#   * SEVERAL cores in the segment → ONE edge per core, status ``"ambiguous"``,
#     full candidate-core set in payload (never a silent pick of the nearest);
#   * NO core in the segment / chapeau but core(s) elsewhere in the UNIT → FALL
#     BACK to PROXIMITY (the nearest core in the unit by char-distance), status
#     ``"ambiguous"`` (a fallback is never asserted), ``attachment=proximity_fallback``
#     — the edge is NEVER dropped (no-silent-drop);
#   * NO deontic core anywhere in the unit → NO edge; a typed
#     :data:`NO_CORE_IN_UNIT` diagnostic (the honest target-less case).
#
# surface_only / replay_authorized=False holds (the assembler mints every edge).

PASS_ID_FOREST_STRUCT = "fi.norm_composition.forest_structural.v0"
RULE_CONDITION_FOREST = "fi.norm_composition.condition_attaches_norm.forest"
RULE_EXCEPTION_FOREST = "fi.norm_composition.exception_excepts_norm.forest"

_KIND_RULE_FOREST: dict[str, str] = {
    KIND_CONDITION: RULE_CONDITION_FOREST,
    KIND_EXCEPTION: RULE_EXCEPTION_FOREST,
}

#: Attachment reasons for the forest-structural path (ride in the edge payload).
ATTACHMENT_RESOLVED_BY_FOREST_SEGMENT = "resolved_by_forest_segment"
ATTACHMENT_AMBIGUOUS_BY_FOREST_SEGMENT = "ambiguous_by_forest_segment"
ATTACHMENT_RESOLVED_BY_CHAPEAU = "resolved_by_chapeau_inheritance"
ATTACHMENT_AMBIGUOUS_BY_CHAPEAU = "ambiguous_by_chapeau_inheritance"
ATTACHMENT_PROXIMITY_FALLBACK = "proximity_fallback"

#: Why a forest-structural candidate produced no asserted/fallback edge (typed).
NO_CORE_IN_UNIT = "no_deontic_core_anywhere_in_unit"

#: The forest STRUCTURAL segment kinds a cue can sit inside (grammar6 §"Phase A").
_FOREST_STRUCTURAL_KINDS: frozenset[str] = frozenset(
    {"heading", "chapeau", "list_item", "quoted_amendment_block", "continuation", "prose"}
)


def _enclosing_structural_segment(
    structural: list[SyntaxNode], char: int
) -> SyntaxNode | None:
    """The SMALLEST forest structural segment whose span contains ``char``.

    The cue's enclosing structural segment is the provision unit (prose paragraph /
    chapeau / list item) the proximity sentence split came from. Smallest-first so a
    list_item nested under a chapeau wins over the chapeau (the item is the cue's
    own segment). Returns ``None`` when the char sits in no structural segment.
    """
    enclosers = [n for n in structural if n.char_start <= char < n.char_end]
    if not enclosers:
        return None
    enclosers.sort(key=lambda n: (n.char_end - n.char_start, n.char_start, n.node_id))
    return enclosers[0]


@dataclass
class ForestStructuralAttachmentPass:
    """Edge pass: attach a sentence-local-CANDIDATE qualifier via FOREST structure.

    Implements :class:`lawvm.core.legal_surface_assembler.SurfaceEdgePass`.
    Constructed per-statute from the bundle units (it re-derives sentences via
    :func:`build_clause_index`, re-runs the construction parse to find the
    ``candidate`` residue, and assembles the per-unit
    :func:`assemble_source_syntax_graph` forest for the structural segments +
    ``inherits_chapeau`` edges). Mints NO nodes; it joins the EXISTING
    ``exception_condition_cue`` (source) and ``deontic_core`` (target) nodes the
    intra-sentence pass could not connect.

    STRICT SUPERSET of :class:`ConditionAttachmentPass`: it fires ONLY for a
    qualifier the intra-sentence pass leaves ``candidate`` (zero core in its
    sentence) and skips the back-reference EXCEPTION cues the cross-sentence pass
    owns. NEW-BETTER with 0 regressions by construction (previously-edgeless
    qualifiers only). candidate-not-asserted, no-silent-drop, proximity fallback.

    Determinism: units in declared order; sentences left-to-right; qualifiers in
    source order; candidate cores by char_start. The assembler recomputes graph_id.
    """

    units: tuple[SourceSurfaceUnit, ...]
    pass_id: str = PASS_ID_FOREST_STRUCT
    reads_node_kinds: tuple[str, ...] = (CUE_NODE_KIND, CORE_NODE_KIND)
    emits_edge_kinds: tuple[str, ...] = (
        EDGE_CONDITION_ATTACHES,
        EDGE_EXCEPTION_EXCEPTS,
    )
    # Typed diagnostics for candidates with no forest/proximity target. Populated on
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
            if not cue_nodes or not core_nodes:
                # No cue node to source from, or no core anywhere → nothing this
                # pass can attach (the intra-sentence pass already tagged the
                # NO_CORE_IN_SENTENCE residue; we add no edge with no target).
                continue
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            try:
                index = (
                    unit.clause_index
                    if isinstance(unit.clause_index, ClauseIndex)
                    else build_clause_index(unit_id, unit.raw_text, token_tape=tape)
                )
            except Exception:
                continue
            # The forest carries the structural segments + inherits_chapeau the
            # clause-segmented sentence drops. Built per-unit, surface-only.
            try:
                forest = assemble_source_syntax_graph_for_unit(
                    subject=graph.subject,
                    unit=unit,
                )
            except Exception:
                continue
            structural = [
                n
                for n in forest.syntax_nodes.values()
                if n.kind in _FOREST_STRUCTURAL_KINDS
            ]
            item_to_chapeau = {
                e.src: e.dst
                for e in forest.syntax_edges
                if e.kind == "inherits_chapeau"
            }

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
                        structural=structural,
                        item_to_chapeau=item_to_chapeau,
                        forest_nodes=forest.syntax_nodes,
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
        structural: list[SyntaxNode],
        item_to_chapeau: dict[str, str],
        forest_nodes: Mapping[str, SyntaxNode],
    ) -> list[SurfaceEdgeSeed]:
        out: list[SurfaceEdgeSeed] = []
        for q in parse.qualifiers:
            # Only the intra-sentence CANDIDATE residue (zero core in its sentence)
            # — never re-decide a resolved/ambiguous intra-sentence attachment.
            if q.attachment_status != ATTACH_CANDIDATE:
                continue
            # The cross-sentence pass owns the back-reference EXCEPTION cues; skip
            # them so the two passes never compete for one cue.
            if q.kind == KIND_EXCEPTION and q.cue in _BACKREF_EXCEPTION_CUES:
                continue

            cue_abs_start = base + q.cue_start
            cue_abs_end = base + q.cue_end
            src_id = _find_node_covering(cue_nodes, cue_abs_start, cue_abs_end)
            if src_id is None:
                # The H6 lens minted no cue node for this construction cue
                # (coordinate-bridge miss). Tag it; never invent a src.
                self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_GRAPH_NODE_FOR_CUE)
                continue

            targets, attachment, edge_status = self._forest_targets(
                cue_abs_start=cue_abs_start,
                core_nodes=core_nodes,
                structural=structural,
                item_to_chapeau=item_to_chapeau,
                forest_nodes=forest_nodes,
            )
            if not targets:
                # No forest segment core AND no core anywhere in the unit reachable
                # by proximity → the honest target-less case (never an invented
                # edge). (core_nodes non-empty is guaranteed by run(), so this is
                # the rare "cue sits in no structural segment and unit has no core
                # the fallback could pick" guard.)
                self._tag(unit_id, q, cue_abs_start, cue_abs_end, NO_CORE_IN_UNIT)
                continue

            candidate_core_spans = [[r.char_start, r.char_end] for _, r in targets]
            for dst_id, dst_ref in targets:
                payload: dict[str, object] = {
                    "qualifier_kind": q.kind,
                    "cue": q.cue,
                    "attachment": attachment,
                    "cue_span": [cue_abs_start, cue_abs_end],
                    "core_span": [dst_ref.char_start, dst_ref.char_end],
                    "source": "construction_forest_structural",
                    "experimental": True,
                }
                if len(targets) > 1:
                    payload["candidate_core_spans"] = candidate_core_spans
                out.append(
                    SurfaceEdgeSeed(
                        edge_kind=_KIND_EDGE[q.kind],
                        src_local=src_id,
                        dst_local=dst_id,
                        rule_id=_KIND_RULE_FOREST[q.kind],
                        surface_edge_status=edge_status,
                        payload=payload,
                    )
                )
        return out

    def _forest_targets(
        self,
        *,
        cue_abs_start: int,
        core_nodes: list[tuple[str, SourceSpanRef]],
        structural: list[SyntaxNode],
        item_to_chapeau: dict[str, str],
        forest_nodes: Mapping[str, SyntaxNode],
    ) -> tuple[list[tuple[str, SourceSpanRef]], str, str]:
        """Resolve a candidate cue's attachment target(s) via FOREST structure.

        Returns ``(targets, attachment_reason, edge_status)``. ``targets`` is the
        list of ``(core_node_id, core_source_ref)`` the cue attaches to (one →
        asserted/resolved; several → ambiguous; empty only when the unit has no
        core at all). The attachment reason names HOW it was resolved
        (segment / chapeau-inheritance / proximity-fallback).
        """
        seg = _enclosing_structural_segment(structural, cue_abs_start)
        if seg is not None:
            in_seg = _cores_within(core_nodes, seg.char_start, seg.char_end)
            if in_seg:
                ambiguous = len(in_seg) > 1
                attachment = (
                    ATTACHMENT_AMBIGUOUS_BY_FOREST_SEGMENT
                    if ambiguous
                    else ATTACHMENT_RESOLVED_BY_FOREST_SEGMENT
                )
                return in_seg, attachment, ("ambiguous" if ambiguous else "asserted")
            # A frame-less list_item: its governing norm is the CHAPEAU's — follow
            # the inherits_chapeau edge and take the chapeau's cores.
            if seg.kind == "list_item":
                chapeau_id = item_to_chapeau.get(seg.node_id)
                chapeau = forest_nodes.get(chapeau_id) if chapeau_id else None
                if chapeau is not None:
                    in_chap = _cores_within(
                        core_nodes, chapeau.char_start, chapeau.char_end
                    )
                    if in_chap:
                        ambiguous = len(in_chap) > 1
                        attachment = (
                            ATTACHMENT_AMBIGUOUS_BY_CHAPEAU
                            if ambiguous
                            else ATTACHMENT_RESOLVED_BY_CHAPEAU
                        )
                        return (
                            in_chap,
                            attachment,
                            "ambiguous" if ambiguous else "asserted",
                        )

        # FALL BACK to proximity: the nearest core in the unit by char-distance to
        # the cue (never dropped; a fallback is never asserted → "ambiguous").
        nearest = min(
            core_nodes,
            key=lambda kv: (
                abs(kv[1].char_start - cue_abs_start),
                kv[1].char_start,
                kv[0],
            ),
            default=None,
        )
        if nearest is None:
            return [], ATTACHMENT_PROXIMITY_FALLBACK, "ambiguous"
        return [nearest], ATTACHMENT_PROXIMITY_FALLBACK, "ambiguous"

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


def _cores_within(
    core_nodes: list[tuple[str, SourceSpanRef]], start: int, end: int
) -> list[tuple[str, SourceSpanRef]]:
    """The ``deontic_core`` nodes whose span sits inside ``[start, end)``.

    A core belongs to a structural segment when its modal-cue span is fully
    contained in the segment span. Returned in source order (the index already
    sorts by char_start).
    """
    return [
        (nid, ref)
        for nid, ref in core_nodes
        if start <= ref.char_start and ref.char_end <= end
    ]


def forest_structural_attachment_passes(
    bundle: SourceSurfaceBundle,
) -> tuple[ForestStructuralAttachmentPass, ...]:
    """Build the per-statute forest-structural condition/exception attachment pass.

    Recovers the intra-sentence pass's ``candidate`` (zero-core-in-sentence) residue
    by attaching via the forest's structural segments + ``inherits_chapeau`` edges
    (the structure the clause-segmented sentence drops), with a proximity fallback
    so no qualifier is silently dropped. Returns a one-tuple so the caller can
    splice it into the edge-pass sequence ADDITIVELY (alongside the intra-sentence,
    cross-sentence, enclosing-anaphora, and proximity incumbents).
    """
    return (ForestStructuralAttachmentPass(units=bundle.units),)


__all__ = [
    "ACTOR_FRAME_KIND",
    "CORE_NODE_KIND",
    "CUE_NODE_KIND",
    "ENCLOSING_CUE_NODE_KIND",
    "DELEGATED_INSTRUMENT_KIND",
    "DELEGATION_FRAME_KIND",
    "PROCEDURE_FRAME_KIND",
    "PROCEDURE_FRAME_KINDS",
    "REFERENCE_EXPR_KIND",
    "SANCTION_FRAME_KIND",
    "SANCTION_FRAME_KINDS",
    "ConditionAttachmentPass",
    "DelegationInstrumentPass",
    "DeonticFrameAttachmentPass",
    "EnclosingAnaphoraPass",
    "ForestStructuralAttachmentPass",
    "NormSubjectAttachmentPass",
    "ProcedureGovernancePass",
    "SanctionReferencePass",
    "EDGE_CONDITION_ATTACHES",
    "EDGE_DELEGATES_TO",
    "EDGE_DELEGATION_GRANTS_INSTRUMENT",
    "EDGE_EXCEPTION_EXCEPTS",
    "EDGE_GOVERNED_BY_PROCEDURE",
    "EDGE_NORM_HAS_SUBJECT",
    "EDGE_SANCTIONED_BY",
    "EDGE_SANCTION_DEFERS",
    "ATTACHMENT_RESOLVED_BY_ENCLOSING",
    "ATTACHMENT_AMBIGUOUS_BY_ENCLOSING",
    "ATTACHMENT_RESOLVED_BY_FOREST_SEGMENT",
    "ATTACHMENT_AMBIGUOUS_BY_FOREST_SEGMENT",
    "ATTACHMENT_RESOLVED_BY_CHAPEAU",
    "ATTACHMENT_AMBIGUOUS_BY_CHAPEAU",
    "ATTACHMENT_PROXIMITY_FALLBACK",
    "NO_CORE_IN_UNIT",
    "PASS_ID_FOREST_STRUCT",
    "RULE_CONDITION_FOREST",
    "RULE_EXCEPTION_FOREST",
    "forest_structural_attachment_passes",
    "AMBIGUOUS_ENCLOSING_PROVISION",
    "ENCLOSING_SCOPE_WHOLE_LAW",
    "NO_CORE_IN_ENCLOSING_PROVISION",
    "NO_CORE_IN_SENTENCE",
    "NO_DEFERRAL_REFERENCE",
    "NO_PROVISION_INDEX_FOR_UNIT",
    "NO_SUBSECTION_FOR_MOMENTTI_ANAPHOR",
    "NO_FRAME_IN_SENTENCE",
    "NO_GRAPH_NODE_FOR_CORE",
    "NO_GRAPH_NODE_FOR_CUE",
    "NO_INSTRUMENT_IN_FRAME",
    "NO_SUBJECT_NODE_FOR_ADDRESSEE",
    "SUBJECT_UNDERSPECIFIED",
    "PASS_ID",
    "PASS_ID_ENCLOSING",
    "PASS_ID_DELEG_INSTRUMENT",
    "PASS_ID_DEONTIC_FRAME",
    "PASS_ID_NORM_SUBJECT",
    "PASS_ID_PROCEDURE",
    "PASS_ID_SANCTION_REF",
    "RULE_CONDITION",
    "RULE_DELEGATES_TO",
    "RULE_DELEGATION_GRANTS_INSTRUMENT",
    "RULE_EXCEPTION",
    "RULE_GOVERNED_BY_PROCEDURE",
    "RULE_NORM_HAS_SUBJECT",
    "RULE_SANCTIONED_BY",
    "RULE_SANCTION_DEFERS",
    "UnattachedAnaphor",
    "UnattachedCore",
    "UnattachedFrame",
    "UnattachedQualifier",
    "UnattachedSanction",
    "condition_attachment_passes",
    "enclosing_anaphora_passes",
    "delegation_instrument_passes",
    "deontic_frame_attachment_passes",
    "norm_subject_attachment_passes",
    "procedure_governance_passes",
    "sanction_reference_passes",
]
