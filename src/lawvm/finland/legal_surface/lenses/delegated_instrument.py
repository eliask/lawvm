"""Delegated-instrument surface lens — the LOWER INSTRUMENT a delegation grants.

Adapts the single canonical forward-grant parser
(:func:`lawvm.finland.legal_surface.delegation_canonical.parse_delegation_grants`)
into :class:`~lawvm.core.legal_surface_graph.SurfaceNode` ``delegated_instrument``
nodes — one per :class:`~lawvm.finland.legal_surface.delegation_canonical.DelegationGrant`
in the unit. The node represents the LOWER INSTRUMENT the power is granted to issue
(the ``asetus`` / ``määräys`` / ``ohje`` / ``päätös`` the delegation authorizes),
anchored on the grant's INSTRUMENT ANCHOR span (the precise instrument-noun span —
``asetuksella`` / ``määräyksiä``), NOT the whole frame.

WHY a SECOND delegation node kind alongside ``delegation_frame``
===============================================================
The ``delegation_frame`` node (minted by :class:`DelegationLens` from the canonical
parser via the B adapter) carries the instrument as a canonical *kind* STRING
(``instrument_kind`` ∈ {asetus, määräys, ohje, päätös}) but no instrument SPAN — its
``source_ref`` is the whole frame. There is therefore no INSTRUMENT-ENTITY node the
Layer-2 ``delegation_grants_instrument`` edge can point at: the "norm → authorized
instrument" link had no target.

This lens mints that target from the instrument-anchor span the SAME canonical grant
already carries (``instrument_start`` / ``instrument_end``) — it does NOT re-parse
the instrument out of the frame.

ONE CANONICAL PARSER, ONE INVOCATION (DELEGATION-UNIFY-VERDICT step 6)
=====================================================================
Both delegation node kinds now derive from the SAME canonical scan of the SAME unit
tape: :class:`DelegationLens` projects each :class:`DelegationGrant` to a
``delegation_frame`` node (via the B adapter ``recognize_delegation_frames``), and
this lens anchors a ``delegated_instrument`` node on the SAME grant's instrument
span. The canonical parser is the SOLE forward-grant producer; the two lenses are
thin projections of its one grant set, NOT two rival recognizers over divergent
segmentations. A grant's whole-frame span (``frame_start`` / ``frame_end``) CONTAINS
its own instrument-anchor span by construction, so every ``delegated_instrument``
node sits inside the ``delegation_frame`` node minted from the SAME grant — there can
be no ORPHAN instrument (an instrument with no containing frame) and no UNATTACHED
frame (a frame with no contained instrument) on the forward-grant path. The
:class:`~lawvm.finland.legal_surface.norm_composition.DelegationInstrumentPass` joins
them by that structural containment (never a proximity mesh).

FAIL-LOUD RESIDUE: a grant-SHAPED-but-not-a-grant instrument mention the canonical
parser DECLINES (self-/cross-reference, postposition complement, instrument without
a power verb) is surfaced as a ``surface_residual`` seed — never silently dropped.
(The B adapter does NOT re-surface these canonical residuals on the frame side; this
lens surfaces them ONCE here so the declined instrument-shaped span is owned exactly
once on the LSG.)

This lens runs ALONGSIDE :class:`DelegationLens` (additive strangle, never replacing
it). The two node kinds carry different identity (different ``node_kind`` +
``lens_id`` + span granularity): the ``delegated_instrument`` span is the tiny
instrument anchor, the ``delegation_frame`` span is the whole frame.

SAFETY BOUNDARY: SURFACE FACTS ONLY. A node records the instrument's SURFACE form
(``instrument_kind``, the matched anchor surface), never a legal conclusion (no
"valid delegation", no "ultra vires", no "discretion"). It defaults to the
firewall-safe configuration (``surface_only=True`` / ``replay_authorized=False``).

Coordinate space: the canonical parser is TOKEN-NATIVE — it consumes
``unit.token_tape`` (a :class:`TokenTape` over ``unit.raw_text``) and reports
whole-token-aligned char offsets DIRECTLY into that same ``raw_text``. The node is
anchored on those offsets with no per-sentence coordinate translation (the prior
per-sentence ``base + core.instrument_start`` re-baselining is gone; the spans are
identical because both are whole-token offsets into the same text).
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
    SurfaceResidualSeed,
)
from lawvm.core.legal_surface_tokens import TokenTape
from lawvm.finland.legal_surface.delegation_canonical import (
    DelegationGrant,
    GrantResidual,
    parse_delegation_grants,
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


def _opt_span(start: int | None, end: int | None) -> list[int] | None:
    """[start, end], or None when unset."""
    if start is None or end is None:
        return None
    return [start, end]


def _instrument_payload(
    grant: DelegationGrant, instrument_surface: str
) -> dict[str, object]:
    """The surface delegated-instrument payload in ``raw_text`` coordinates.

    Carries the instrument SURFACE form only (instrument kind, the matched anchor
    surface, the issuer kind/holder span the grant binds it to) — never a legal
    conclusion that the delegation is valid. The grant's spans are already
    whole-token-aligned ``raw_text`` offsets (the canonical parser is token-native),
    so no per-sentence re-baselining is needed.
    """
    return {
        "instrument_kind": grant.instrument,
        "instrument_surface": instrument_surface,
        "instrument_span": [grant.instrument_start, grant.instrument_end],
        "issuer_kind": grant.kind,
        "cue": grant.cue,
        "cue_span": [grant.cue_start, grant.cue_end],
        "holder_span": _opt_span(grant.holder_start, grant.holder_end),
        "holder_underspecified": grant.holder_underspecified,
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
        residual_seeds: list[SurfaceResidualSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            tape = unit.token_tape
            if not isinstance(tape, TokenTape):
                # The canonical parser is token-native; a missing tape is a real
                # defect, never a silent fallback to a private re-segmentation.
                raise ValueError(
                    "DelegatedInstrumentLens requires a populated token_tape view "
                    f"(required_views={self.required_views!r}); unit "
                    f"{unit.source_unit_id!r} has token_tape={type(tape).__name__}"
                )
            # One canonical scan of the WHOLE unit tape — the SAME grant set the
            # DelegationLens (via the B adapter) projects to delegation_frame nodes.
            # Each delegated_instrument node and the delegation_frame node it sits
            # inside therefore derive from the SAME DelegationGrant (step 6: one
            # canonical parser, one invocation per unit). Spans are whole-token
            # raw_text offsets already.
            scan = parse_delegation_grants(tape, unit.raw_text)
            for grant in scan.grants:
                instr_abs_start = grant.instrument_start
                instr_abs_end = grant.instrument_end
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
                        # A coordinated clause can carry SEVERAL instrument anchors
                        # sharing one verb (each its own grant); key on the cue span
                        # + instrument kind + anchor start so co-located instruments
                        # stay distinct, never collapsed. Identical to the pre-step-6
                        # discriminator: the canonical grant's cue_start /
                        # instrument_start ARE the raw_text-absolute offsets the old
                        # per-sentence ``base + core.*`` produced, so node identity is
                        # preserved for every instrument node C already minted.
                        local_discriminator=(
                            f"{grant.instrument}|{grant.kind}|{grant.cue}|"
                            f"{grant.cue_start}|{instr_abs_start}"
                        ),
                        rule_id=_LENS_ID,
                        # Structural NODE_STATUSES value: an owned, present surface
                        # fact (NOT a resolution outcome).
                        node_status="asserted",
                        payload=_instrument_payload(grant, instrument_surface),
                        authority_role="surface_fact",
                    )
                )
            for residual in scan.residuals:
                residual_seeds.append(
                    _residual_seed(unit, residual, len(residual_seeds))
                )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=tuple(residual_seeds),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "delegated_instruments": len(node_seeds),
                "residuals": len(residual_seeds),
            },
        )


def _residual_seed(
    unit: SourceSurfaceUnit, residual: GrantResidual, index: int
) -> SurfaceResidualSeed:
    """A typed surface_residual seed for a canonical declined instrument mention.

    Self-evidencing: ``surface_text`` embeds the verbatim declined clause and
    ``reason_code`` names the closed canonical residual class. Never a silent drop.
    """
    surface = unit.raw_text[residual.char_start : residual.char_end]
    ref = SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=residual.char_start,
        char_end=residual.char_end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )
    return SurfaceResidualSeed(
        residual_kind=f"delegated_instrument.{residual.kind}",
        source_ref=ref,
        # A monotonic index keeps the discriminator unique when two residuals share
        # kind+offset (co-located declined mentions); neither is dropped.
        local_discriminator=(
            f"{residual.kind}|{residual.char_start}|{residual.char_end}|{index}"
        ),
        rule_id=_LENS_ID,
        reason_code=residual.kind,
        payload={
            "surface_text": surface,
            "detail": residual.reason,
        },
        residual_status="open",
    )
