"""Delegated-instrument surface lens — the LOWER INSTRUMENT a delegation grants.

Adapts the construction-grammar delegation parse
(:func:`lawvm.finland.legal_surface.delegation_parse.parse_delegation_sentence`)
into :class:`~lawvm.core.legal_surface_graph.SurfaceNode` ``delegated_instrument``
nodes — one per :class:`~lawvm.finland.legal_surface.delegation_parse.DelegationCore`
in each sentence of the unit. The node represents the LOWER INSTRUMENT the power is
granted to issue (the ``asetus`` / ``määräys`` / ``päätös`` the delegation
authorizes), anchored on the construction parse's INSTRUMENT ANCHOR span (the
precise instrument-noun span — ``asetuksella`` / ``määräyksiä``), NOT the whole
frame.

WHY a SECOND delegation node kind alongside ``delegation_frame``
===============================================================
The production ``delegation_frame`` node (minted by :class:`DelegationLens` from the
recognizer) carries the instrument as a canonical *kind* STRING
(``instrument_kind`` ∈ {asetus, määräys, ohje, päätös}) but no instrument SPAN — its
``source_ref`` is the whole frame. There is therefore no INSTRUMENT-ENTITY node the
Layer-2 ``delegation_grants_instrument`` edge can point at: the "norm → authorized
instrument" link had no target.

This lens mints that target. The CONSTRUCTION delegation parse already computes a
precise instrument anchor span per core (``instrument_start`` / ``instrument_end``),
so this lens reuses that span — it does NOT re-parse the instrument out of the frame.
The instrument anchor sits INSIDE the recognizer frame's span, so the
:class:`~lawvm.finland.legal_surface.norm_composition.DelegationInstrumentPass` joins
a frame to the instrument node(s) its span CONTAINS (a structural containment
attachment, never a proximity mesh).

This lens runs ALONGSIDE :class:`DelegationLens` (additive strangle, never replacing
it). The two node kinds carry different identity (different ``node_kind`` +
``lens_id`` + span granularity): the ``delegated_instrument`` span is the tiny
instrument anchor, the ``delegation_frame`` span is the whole frame.

SAFETY BOUNDARY: SURFACE FACTS ONLY. A node records the instrument's SURFACE form
(``instrument_kind``, the matched anchor surface), never a legal conclusion (no
"valid delegation", no "ultra vires", no "discretion"). It defaults to the
firewall-safe configuration (``surface_only=True`` / ``replay_authorized=False``).

Coordinate space: the parse runs PER SENTENCE in sentence-local coordinates; this
lens re-derives the sentences via the SHARED clause-segmentation authority
(:func:`build_clause_index`) and translates each core's sentence-local instrument
span back to ``raw_text`` coordinates, anchoring the node on that span.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_tokens import TokenTape
from lawvm.finland.legal_surface.clause_segment import build_clause_index
from lawvm.finland.legal_surface.delegation_parse import (
    DelegationCore,
    parse_delegation_sentence,
)

_LENS_ID = "fi.delegated_instrument.v0"

#: The node kind this lens mints (the lower instrument a delegation grants).
DELEGATED_INSTRUMENT_NODE_KIND = "delegated_instrument"


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


def _instrument_payload(
    core: DelegationCore, base: int, instrument_surface: str
) -> dict[str, object]:
    """The surface delegated-instrument payload in raw_text coordinates.

    Carries the instrument SURFACE form only (instrument kind, the matched anchor
    surface, the issuer kind/holder span the grant binds it to) — never a legal
    conclusion that the delegation is valid.
    """
    return {
        "instrument_kind": core.instrument,
        "instrument_surface": instrument_surface,
        "instrument_span": [base + core.instrument_start, base + core.instrument_end],
        "issuer_kind": core.kind,
        "cue": core.cue,
        "cue_span": [base + core.cue_start, base + core.cue_end],
        "holder_span": _abs_span(base, core.holder_start, core.holder_end),
        "holder_underspecified": core.holder_underspecified,
        "source": "construction_delegation_parse",
        "experimental": True,
    }


class DelegatedInstrumentLens:
    """SurfaceLens minting ``delegated_instrument`` nodes from the delegation parse.

    One node per construction delegation core in each sentence of each unit, anchored
    on the instrument anchor span. Mints NO edges. Runs ALONGSIDE the production
    ``delegation_frame`` lens (additive strangle). Requires the populated
    ``token_tape`` view (reused to segment the unit into sentences via the shared
    clause authority).
    """

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = (DELEGATED_INSTRUMENT_NODE_KIND,)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("token_tape",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            tape = unit.token_tape if isinstance(unit.token_tape, TokenTape) else None
            index = unit.clause_index or build_clause_index(
                unit.source_unit_id, unit.raw_text, token_tape=tape
            )
            for sent in index.sentences:
                base = sent.char_start
                seg_text = unit.raw_text[sent.char_start : sent.char_end]
                parse = parse_delegation_sentence(seg_text)
                for core in parse.cores:
                    instr_abs_start = base + core.instrument_start
                    instr_abs_end = base + core.instrument_end
                    instrument_surface = unit.raw_text[instr_abs_start:instr_abs_end]
                    ref = _span_ref(
                        unit.source_unit_id,
                        unit.source_hash,
                        unit.work_id,
                        unit.address,
                        unit.raw_text,
                        instr_abs_start,
                        instr_abs_end,
                    )
                    node_seeds.append(
                        SurfaceNodeSeed(
                            node_kind=DELEGATED_INSTRUMENT_NODE_KIND,
                            source_ref=ref,
                            # A coordinated clause can carry SEVERAL instrument
                            # anchors sharing one verb (each its own core); key on
                            # the cue span + instrument kind + anchor start so
                            # co-located instruments stay distinct, never collapsed.
                            local_discriminator=(
                                f"{core.instrument}|{core.kind}|{core.cue}|"
                                f"{base + core.cue_start}|{instr_abs_start}"
                            ),
                            rule_id=_LENS_ID,
                            # Structural NODE_STATUSES value: an owned, present
                            # surface fact (NOT a resolution outcome).
                            status="asserted",
                            payload=_instrument_payload(
                                core, base, instrument_surface
                            ),
                            authority_role="surface_fact",
                        )
                    )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "delegated_instruments": len(node_seeds),
            },
        )
