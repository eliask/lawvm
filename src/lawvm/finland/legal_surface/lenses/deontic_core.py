"""Deontic-core surface lens — the construction modal cores as first-class nodes.

Adapts the construction-grammar modal/deontic-core parse
(:func:`lawvm.finland.legal_surface.modal_parse.parse_modal_sentence`) into
:class:`~lawvm.core.legal_surface_graph.SurfaceNode` ``deontic_core`` nodes — one
per recognised :class:`~lawvm.finland.legal_surface.modal_parse.ModalCore` in each
sentence of the unit. This is the DENSE deontic substrate Layer-2 attaches to.

WHY a SECOND deontic node kind alongside ``actor_modal_frame``
==============================================================
The production ``actor_modal`` lens (:class:`ActorModalLens`) only mints an
``actor_modal_frame`` node when a REGISTERED actor sits within 60 chars before the
modal cue (its weak-oracle gate). A real deontic core whose subject is an
unregistered actor or is impersonal (``säädetään``, ``on tehtävä`` with no overt
subject) yields NO production frame. The construction parse recognises the deontic
core from the CUE alone (addressee underspecified when no registered actor binds),
so it is DENSE where the production frame is SPARSE.

The first Layer-2 edge pass (``norm_composition.ConditionAttachmentPass``) attaches
condition/exception qualifiers to deontic cores, but it can only emit an edge when
the construction core has a BACKING graph node. With only the sparse
``actor_modal_frame`` as a backing node, most construction cores had none — their
attachments produced no edge. This lens mints a node for EVERY construction core,
so the dense attachment set becomes a dense edge set.

This lens does NOT remove or replace the production ``actor_modal_frame`` lens; it
runs ALONGSIDE it (strangle, not big-bang). The two node kinds carry DIFFERENT
identity (different ``node_kind`` + ``lens_id`` + span granularity), so they never
collide: the ``deontic_core`` span is the modal CUE span, the ``actor_modal_frame``
span is the whole actor..object frame.

SAFETY BOUNDARY: SURFACE FACTS ONLY. A node records the deontic SURFACE shape
(``kind`` = obligation/permission/prohibition/power, ``polarity``, ``voice``,
addressee/object spans), never a legal conclusion (no "duty"/"power" as law). The
node defaults to the firewall-safe configuration (``surface_only=True`` /
``replay_authorized=False``); it authorises NO replay.

Coordinate space: the parse runs PER SENTENCE in sentence-local coordinates; this
lens re-derives the sentences via the SHARED clause-segmentation authority
(:func:`build_clause_index`) and translates each core's sentence-local cue/
addressee/object spans back to ``raw_text`` coordinates, anchoring the node on the
``raw_text`` CUE span so the attachment pass (which computes the same cue span)
matches it.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_tokens import ClauseIndex, TokenTape
from lawvm.finland.legal_surface.clause_segment import build_clause_index
from lawvm.finland.legal_surface.modal_parse import ModalCore, parse_modal_sentence

_LENS_ID = "fi.deontic_core.v0"

#: The node kind this lens mints (the dense construction deontic core).
DEONTIC_CORE_NODE_KIND = "deontic_core"


def _span_ref(
    unit_source_unit_id: str,
    unit_source_hash: str,
    unit_work_id: str,
    unit_address: str | None,
    raw_text: str,
    char_start: int,
    char_end: int,
) -> SourceSpanRef:
    """Build a raw_text-relative SourceSpanRef from absolute char offsets."""
    surface = raw_text[char_start:char_end]
    return SourceSpanRef(
        source_unit_id=unit_source_unit_id,
        source_hash=unit_source_hash,
        work_id=unit_work_id,
        address=unit_address,
        char_start=char_start,
        char_end=char_end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )


def _abs_span(base: int, start: int | None, end: int | None) -> list[int] | None:
    """[base+start, base+end] in raw_text coordinates, or None when unset."""
    if start is None or end is None:
        return None
    return [base + start, base + end]


def _core_payload(core: ModalCore, base: int) -> dict[str, object]:
    """The surface deontic-core payload in raw_text coordinates.

    Carries the deontic SURFACE shape only (kind/polarity/voice), the addressee
    span (or the underspecified marker for the impersonal/passive register), and
    the object/complement span — never a legal conclusion.
    """
    return {
        "kind": core.kind,
        "cue": core.cue,
        "polarity": core.polarity,
        "voice": core.voice,
        "cue_span": [base + core.cue_start, base + core.cue_end],
        "addressee_span": _abs_span(base, core.addressee_start, core.addressee_end),
        "addressee_underspecified": core.addressee_underspecified,
        "object_span": _abs_span(base, core.object_start, core.object_end),
        "source": "construction_modal_parse",
        "experimental": True,
    }


def mint_deontic_core_seed(
    unit: SourceSurfaceUnit, core: ModalCore, base: int
) -> SurfaceNodeSeed:
    """Mint ONE deontic_core node seed for a construction core in ``base`` coords.

    THE shared seed-minting authority: both the production lens scan and the
    forest projection (:func:`…modal_projection.project_forest_deontic_core_seeds`)
    mint via this one function, so a forest-projected node is byte-identical to a
    lens-scanned node BY CONSTRUCTION (same cue-span anchor, same local
    discriminator, same payload). ``base`` is the sentence's ``char_start`` in
    raw_text coordinates; ``core`` is sentence-local.
    """
    cue_abs_start = base + core.cue_start
    cue_abs_end = base + core.cue_end
    ref = _span_ref(
        unit.source_unit_id,
        unit.source_hash,
        unit.work_id,
        unit.address,
        unit.raw_text,
        cue_abs_start,
        cue_abs_end,
    )
    return SurfaceNodeSeed(
        node_kind=DEONTIC_CORE_NODE_KIND,
        source_ref=ref,
        # Disambiguate co-located cores (same cue span never recurs within a
        # unit, but key on kind/pol/voice too so a future multi-core-at-one-cue
        # stays distinct).
        local_discriminator=(
            f"{core.kind}|{core.cue}|{core.polarity}|"
            f"{core.voice}|{cue_abs_start}"
        ),
        rule_id=_LENS_ID,
        # Structural NODE_STATUSES value: an owned, present surface fact (NOT a
        # resolution outcome).
        node_status="asserted",
        payload=_core_payload(core, base),
        authority_role="surface_fact",
    )


def deontic_core_seeds_for_unit(unit: SourceSurfaceUnit) -> list[SurfaceNodeSeed]:
    """The INDEPENDENT (golden-reference) per-unit deontic_core seed scan.

    Segments the unit into sentences via the shared clause authority and parses
    each via :func:`parse_modal_sentence`, minting one seed per core through
    :func:`mint_deontic_core_seed`. This is the GOLDEN REFERENCE the production
    flip is differenced against: production now reads the cached forest
    (:func:`…modal_projection.project_forest_deontic_core_seeds`), which projects
    the SAME cores at the SAME granularity, gated by the forest's modal-family
    ownership — proven node-identical to this scan corpus-wide.
    """
    tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
    index = (
        unit.clause_index
        if isinstance(unit.clause_index, ClauseIndex)
        else build_clause_index(unit.source_unit_id, unit.raw_text, token_tape=tape)
    )
    seeds: list[SurfaceNodeSeed] = []
    for sent in index.sentences:
        base = sent.char_start
        seg_text = unit.raw_text[sent.char_start : sent.char_end]
        parse = parse_modal_sentence(seg_text)
        for core in parse.cores:
            seeds.append(mint_deontic_core_seed(unit, core, base))
    return seeds


class DeonticCoreLens:
    """SurfaceLens minting ``deontic_core`` nodes from the cached forest projection.

    One node per construction modal core in each sentence of each unit. Mints NO
    edges. Runs ALONGSIDE the production ``actor_modal_frame`` lens (additive
    strangle).

    PRODUCTION STRANGLE-FLIP (doc-6): the lens's deontic_core node facts now come
    FROM the cached :class:`SourceSyntaxGraph` forest projection
    (:func:`…modal_projection.project_forest_deontic_core_seeds`) rather than an
    independent per-sentence body scan. The forest is the PRODUCER; the projection
    gates each sentence on the forest's modal-family ownership and reconstructs the
    cores via the SAME construction parse, minting through the SAME
    :func:`mint_deontic_core_seed` authority — so production is byte-identical to
    the prior independent scan (proven 0-delta corpus-wide). The independent scan
    survives as the golden reference (:func:`deontic_core_seeds_for_unit`, the
    differential's right side). Requires the populated ``token_tape`` view (reused
    to segment the unit into sentences via the shared clause authority).
    """

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = (DEONTIC_CORE_NODE_KIND,)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("token_tape",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        # Import here to avoid a module import cycle (modal_projection imports the
        # bundle/seed types this lens also uses, and the source_syntax_graph it
        # reads from). The projection reads the CACHED forest, so production mints
        # deontic_core facts FROM the forest (the flip), not an independent scan.
        from lawvm.finland.legal_surface.modal_projection import (
            project_forest_deontic_core_seeds,
        )

        node_seeds = project_forest_deontic_core_seeds(bundle)
        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={
                "units_scanned": len(bundle.units),
                "deontic_cores": len(node_seeds),
            },
        )
