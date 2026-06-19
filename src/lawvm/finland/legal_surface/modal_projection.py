"""Forest → modal projection — L5 of the SourceSyntaxGraph strangle.

The modal half of the L5 lens→SourceSyntaxGraph strangle, following the L3
TEMPLATE (:mod:`lawvm.finland.legal_surface.reference_projection`): make the
:class:`~lawvm.finland.legal_surface.source_syntax_graph.SourceSyntaxGraph`
forest a PRODUCER of actor-modal / deontic-core facts and difference its modal
layer against the converged actor/modal lenses:

    forest modal_predicate leaves  ──(reparse via the modal family's own
        construction parse)──▶  typed deontic-core facts  ──(corpus differential
        vs the lens)──▶  0-delta on the characterised subset.

WHICH FOREST FAMILY BACKS THE LENS — AND TWO ORACLES
====================================================
The forest's ``modal_predicate`` construction leaves come from ONE family — the
**modal-predicate / actor_modal family**
(:func:`…modal_parse.parse_modal_sentence`), the closed-list deontic-core
construction (necessive ``on tehtävä`` / ``tulee`` / ``on velvollinen``;
permission/power ``saa`` / ``voi`` / ``on oikeus``; prohibition ``ei saa`` …;
passive provision verbs ``säädetään`` / ``määrätään`` …). Polarity and voice are
first-class, exactly as the production recognizer records them.

There are TWO converged lenses over this family, with DIFFERENT density:

  * :class:`~…lenses.deontic_core.DeonticCoreLens` — the DENSE lens. It is a thin
    adapter that calls the SAME :func:`parse_modal_sentence` over each sentence
    and mints one ``deontic_core`` node per construction core. So the forest's
    modal projection and this lens are **identical BY CONSTRUCTION** (same
    parser, same per-sentence segmentation, same ``token:polarity:voice``
    identity) — this is the 0-delta subset the rung proves.
  * :class:`~…lenses.actor_modal.ActorModalLens` — the SPARSE lens. It wraps the
    production :func:`recognize_actor_modal_frames`, which emits a frame ONLY when
    a REGISTERED actor surface sits within 60 chars before the modal cue (its
    weak-oracle gate). A deontic core whose subject is unregistered or impersonal
    (``säädetään``, ``on tehtävä`` with no overt subject) yields NO production
    frame. So the forest is a strict **SUPERSET** of the actor_modal lens: every
    actor_modal frame's modal identity is among the forest's cores, but the
    forest carries MANY actor-underspecified cores the actor_modal lens does not.
    Those extras are annotation-/registry-INDEPENDENT recoveries, NOT regressions
    (mirrors L3's ``<ref>``-annotation-boundary residual): the residual worklist.

The shared comparison identity is the production :class:`SurfaceModality` key the
``actor_modal`` recognizer emits — ``token:polarity:voice`` — which is exactly the
identity :func:`…modal_parse.modal_key` projects. The construction-grammar deontic
``kind`` (obligation/permission/prohibition/power) is the family's own enrichment
and is NOT in the comparison key (the production recognizer does not classify it).

So a NAIVE forest "modal" set EQUALS the dense ``DeonticCoreLens`` subset and
SUPERSETS the sparse ``ActorModalLens`` by the registered-actor gate. This
projection canonicalises on ``token:polarity:voice``, proves 0-delta vs the dense
lens, and characterises the actor-gating residual vs the sparse lens (surfaced,
never silently claimed), exactly as L3 does.

The projection is surface-only: it reads ONLY the assembled forest's
``modal_predicate`` leaves (the SET GATE) and reparses each leaf's enclosing
structural segment via the modal family's OWN construction parse. It re-implements
no grammar, makes no deontic-force conclusion, and authorises no replay.
"""
from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_tokens import ClauseIndex, TokenTape
from lawvm.finland.legal_surface.clause_segment import build_clause_index
from lawvm.finland.legal_surface.modal_parse import (
    ModalCore,
    modal_key,
    parse_modal_sentence,
)
from lawvm.finland.legal_surface.source_syntax_graph import (
    SourceSyntaxGraph,
    assemble_source_syntax_graph_for_unit,
)
from lawvm.finland.references.actor_modal import ActorModalFrame

#: The production actor_modal lane density gate the forest does NOT impose — the
#: explicit residual worklist (surfaced, never hidden). The forest recognises a
#: deontic core from the CUE alone; the production actor_modal frame additionally
#: requires a REGISTERED actor surface within 60 chars before the cue. The forest
#: therefore supersets the actor_modal lens on exactly these registry-independent
#: shapes.
FOREST_UNOWNED_ACTOR_MODAL_GATES: tuple[str, ...] = (
    "actor_underspecified",  # impersonal/passive core (säädetään, on tehtävä …)
    "unregistered_actor",  # overt subject not in the institutional registry
)

#: The phrase identifying the DENSE lens the forest reproduces 1:1 (same parser).
FOREST_OWNED_DENSE_LENS = "fi.deontic_core.v0"


@dataclass(frozen=True, slots=True)
class ProjectedModal:
    """One modal segment PROJECTED from a forest ``modal_predicate`` leaf.

    Surface-only and source-anchored: ``[char_start, char_end)`` is the span of
    the structural segment the ``modal_predicate`` leaf sits in (the leaf is only
    the GATE that a modal cue is present; the full deontic core — addressee + cue
    + object — lives in the surrounding segment, so the segment is the unit
    reparsed). Carries the reconstructed modal cores so the projection is directly
    comparable to the deontic/actor-modal lens over the same span.

    Attributes:
        segment_node_id: ``node_id`` of the structural segment reparsed.
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        cores:      The reconstructed modal cores (>=1) the segment carries.
    """

    segment_node_id: str
    char_start: int
    char_end: int
    cores: tuple[ModalCore, ...]


def _canonical_modal_key(core: ModalCore) -> str:
    """Canonical ``token:polarity:voice`` identity for one forest modal core.

    The SAME identity :func:`…modal_parse.modal_key` projects and the production
    :class:`SurfaceModality` emits — robust to the construction-grammar ``kind``
    enrichment, the addressee surface, and the object span the two sides represent
    differently.
    """
    return modal_key(core.cue, core.polarity, core.voice)


def _canonical_actor_modal_frame_key(frame: ActorModalFrame) -> str:
    """Canonical ``token:polarity:voice`` identity for one production actor_modal frame.

    The production recognizer records the surface modality as
    ``(token, polarity, voice)`` on :class:`SurfaceModality`; the production
    polarity vocabulary is ``positive``/``negative`` while the construction family
    uses ``affirmative``/``negative``, so the production ``positive`` is mapped to
    ``affirmative`` to share the key space with the forest projection.
    """
    pol = frame.modal.polarity
    canon_pol = "affirmative" if pol == "positive" else pol
    return modal_key(frame.modal.token, canon_pol, frame.modal.voice)


#: The family id the modal construction leaf carries. The SET GATE keys on FAMILY
#: MEMBERSHIP (``"modal" in leaf.families``), NOT on the leaf KIND: a span owned by
#: several families (e.g. modal + condition_exception + delegation) is minted with
#: the lexicographically-first family's kind (``condition_clause``) by the
#: assembler, yet it is STILL a modal-gated span — the modal owner is preserved on
#: ``leaf.families``. Gating on kind would silently drop every multi-family modal
#: span (the dominant real-corpus shape); gating on family membership is the
#: faithful gate (it matches the L0 union the dense lens effectively uses).
MODAL_FAMILY_ID = "modal"


def _enclosing_segment_id(forest: SourceSyntaxGraph, leaf_node_id: str) -> str | None:
    """The structural segment that ``contains`` this construction leaf, or None.

    Reads the assembler's ``contains`` edge from a leaf's enclosing structural
    segment to the leaf (mirrors
    :func:`reference_projection._enclosing_segment_id`).
    """
    for edge in forest.edges_of_kind("contains"):
        if edge.dst == leaf_node_id and edge.src in forest.syntax_nodes:
            return edge.src
    return None


def _modal_gated_leaf_ids(forest: SourceSyntaxGraph) -> list[str]:
    """Construction-leaf node ids whose family ownership includes the modal family.

    The SET GATE: every leaf carrying ``"modal"`` among its ``families`` (including
    multi-family leaves minted under another family's kind), in span order.
    """
    return [
        n.node_id
        for n in sorted(
            (
                node
                for node in forest.syntax_nodes.values()
                if MODAL_FAMILY_ID in node.families
            ),
            key=lambda node: (node.char_start, node.char_end),
        )
    ]


def project_forest_modal(
    forest: SourceSyntaxGraph,
    body: str,
) -> tuple[ProjectedModal, ...]:
    """Project the forest's modal-bearing segments to reconstructed deontic cores.

    The forest's ``modal_predicate`` leaves are the SET GATE — only structural
    segments the modal family owned a span of project. A leaf is a coalesced union
    sub-span, so the reconstruction reparses the leaf's ENCLOSING structural
    segment via the modal family's OWN construction parse
    (:func:`parse_modal_sentence`) and lifts each recognised core. One
    :class:`ProjectedModal` per gated segment; a segment that reparses to no modal
    core (a spurious coalesced fragment, e.g. a non-necessive ``on`` copula)
    projects nothing.

    Deterministic and surface-only: reads ONLY the assembled forest + the body
    text, makes no deontic-force conclusion, authorises no replay. Segments are
    emitted in span order. NOTE: the modal lenses (and this projection) segment
    the unit into SENTENCES before parsing; the forest groups by STRUCTURAL
    segment. Where a structural segment carries several sentences the segment
    reparse recovers every core in it (``parse_modal_sentence`` scans the whole
    span), so the core SET is identical even when the unit-of-iteration differs.
    """
    gated_segment_ids: list[str] = []
    seen: set[str] = set()
    for leaf_id in _modal_gated_leaf_ids(forest):
        seg_id = _enclosing_segment_id(forest, leaf_id)
        if seg_id is None or seg_id in seen:
            continue
        seen.add(seg_id)
        gated_segment_ids.append(seg_id)

    out: list[ProjectedModal] = []
    for seg_id in gated_segment_ids:
        seg = forest.syntax_nodes[seg_id]
        seg_text = body[seg.char_start : seg.char_end]
        mp = parse_modal_sentence(seg_text)
        if not mp.cores:
            continue
        out.append(
            ProjectedModal(
                segment_node_id=seg_id,
                char_start=seg.char_start,
                char_end=seg.char_end,
                cores=mp.cores,
            )
        )
    out.sort(key=lambda p: (p.char_start, p.char_end))
    return tuple(out)


def _forest_modal_owned_intervals(
    forest: SourceSyntaxGraph,
) -> tuple[tuple[int, int], ...]:
    """The body-coordinate intervals the forest's modal family owns, span-sorted.

    Every construction leaf carrying ``"modal"`` among its ``families`` — the SAME
    family-membership SET GATE :func:`_modal_gated_leaf_ids` uses (it recovers
    multi-family modal spans minted under another family's kind). A sentence whose
    span overlaps any of these is a forest-gated modal sentence.
    """
    return tuple(
        sorted(
            (
                (node.char_start, node.char_end)
                for node in forest.syntax_nodes.values()
                if MODAL_FAMILY_ID in node.families
            )
        )
    )


def _span_overlaps_any(
    start: int, end: int, intervals: tuple[tuple[int, int], ...]
) -> bool:
    """True iff ``[start, end)`` overlaps any of the (sorted) ``intervals``."""
    for s, e in intervals:
        if s >= end:
            break  # intervals sorted by start; no later one can overlap
        if e > start:
            return True
    return False


def project_forest_deontic_core_seeds(
    bundle: SourceSurfaceBundle,
) -> list[SurfaceNodeSeed]:
    """Project the production ``deontic_core`` node seeds FROM the cached forest.

    THE PRODUCTION STRANGLE-FLIP (doc-6): the modal/deontic ``deontic_core`` facts
    the production :class:`DeonticCoreLens` emits now come FROM the cached
    :class:`SourceSyntaxGraph` forest, not an independent body scan. For each unit
    we assemble (or reuse) the cached forest, take its modal-family-owned spans as
    the SET GATE, and for each clause-segmented SENTENCE whose span the forest
    gated as modal we reconstruct the modal cores via the family's OWN construction
    parse (:func:`parse_modal_sentence`) and mint each through the SAME node-minting
    authority the lens uses (:func:`…lenses.deontic_core.mint_deontic_core_seed`).

    0-DELTA BY CONSTRUCTION vs the independent scan
    ===============================================
    The forest's modal ownership is computed by running the SAME
    :func:`parse_modal_sentence` over the SAME per-sentence segmentation
    (:func:`build_clause_index`) the dense lens uses, via
    :func:`…union_ownership_census.union_over_sentence` inside the forest
    assembler. So a sentence carries forest modal ownership IFF its construction
    parse yields a modal core — exactly the sentences the independent scan emits a
    node for. Gate-then-reparse therefore reproduces the independent scan's node
    set node-identically (same cue-span anchor, discriminator, payload), proven
    0-delta corpus-wide by :mod:`tests.test_fi_modal_projection` /
    ``.tmp/modal_flip_diff``. Surface-only; reads the forest + the body; authorises
    no replay.
    """
    # Imported lazily-at-module-top would create a cycle (the lens imports this
    # module); import here keeps the seed-minting authority shared without a cycle.
    from lawvm.finland.legal_surface.lenses.deontic_core import (
        mint_deontic_core_seed,
    )

    seeds: list[SurfaceNodeSeed] = []
    for unit in bundle.units:
        forest = assemble_source_syntax_graph_for_unit(
            subject=bundle.subject,
            unit=unit,
        )
        modal_intervals = _forest_modal_owned_intervals(forest)
        tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
        index = (
            unit.clause_index
            if isinstance(unit.clause_index, ClauseIndex)
            else build_clause_index(unit.source_unit_id, unit.raw_text, token_tape=tape)
        )
        for sent in index.sentences:
            if not _span_overlaps_any(
                sent.char_start, sent.char_end, modal_intervals
            ):
                continue
            base = sent.char_start
            seg_text = unit.raw_text[sent.char_start : sent.char_end]
            parse = parse_modal_sentence(seg_text)
            for core in parse.cores:
                seeds.append(mint_deontic_core_seed(unit, core, base))
    return seeds


def forest_modal_keys(
    forest: SourceSyntaxGraph,
    body: str,
) -> set[str]:
    """The canonical modal key SET the forest's modal layer produces.

    The forest's owned modal projection as a set of canonical
    ``token:polarity:voice`` keys — the identity both the forest and the lens
    canonicalise identically. This is the LEFT side of both differentials.
    """
    keys: set[str] = set()
    for projected in project_forest_modal(forest, body):
        for core in projected.cores:
            keys.add(_canonical_modal_key(core))
    return keys


def dense_lens_modal_keys_for_text(text: str) -> set[str]:
    """The DENSE (``DeonticCoreLens``) modal key SET over a raw text span.

    The dense lens is a thin adapter over the SAME :func:`parse_modal_sentence`
    the forest reparses, segmented per sentence. We reproduce its key set by
    parsing the whole span (``parse_modal_sentence`` scans every sentence's worth
    of cues in the span), keyed identically. This is the 0-delta RIGHT side.
    """
    return {
        modal_key(c.cue, c.polarity, c.voice)
        for c in parse_modal_sentence(text).cores
    }


def actor_modal_lens_subset_keys(frames: list[ActorModalFrame]) -> set[str]:
    """The canonical modal key SET of the SPARSE production actor_modal lens.

    Keys each production :class:`ActorModalFrame` by its
    ``token:polarity:voice`` surface modality. This is the SPARSE RIGHT side —
    the registered-actor-gated subset the forest SUPERSETS. The forest-EXTRA keys
    vs this subset are the registry-independent residual worklist
    (:data:`FOREST_UNOWNED_ACTOR_MODAL_GATES`), not misses.
    """
    return {_canonical_actor_modal_frame_key(f) for f in frames}


@dataclass(frozen=True, slots=True)
class ModalDifferential:
    """A forest-projection vs modal-lens-subset canonical-key differential.

    Attributes:
        identical:      keys both the forest projection AND the lens subset produce.
        forest_missing: keys the lens subset has that the forest projection lacks.
        forest_extra:   keys the forest projection has that the lens subset lacks.
    """

    identical: frozenset[str]
    forest_missing: frozenset[str]
    forest_extra: frozenset[str]

    @property
    def is_zero_delta(self) -> bool:
        return not self.forest_missing and not self.forest_extra


def diff_forest_vs_modal_lens_subset(
    forest_keys: set[str], lens_subset_keys: set[str]
) -> ModalDifferential:
    """Classify forest-projection vs modal-lens-subset canonical keys.

    IDENTICAL / forest-MISSING / forest-EXTRA. Against the DENSE deontic-core lens
    this is 0-delta (``is_zero_delta``, the flip gate); against the SPARSE
    actor_modal lens it is a characterised SUPERSET (``forest_missing`` empty —
    every actor_modal frame is among the forest cores — and ``forest_extra`` =
    the registry-independent residual).
    """
    return ModalDifferential(
        identical=frozenset(forest_keys & lens_subset_keys),
        forest_missing=frozenset(lens_subset_keys - forest_keys),
        forest_extra=frozenset(forest_keys - lens_subset_keys),
    )
