"""H2 DEFINITIONS lens: defined-term bindings + term uses as surface seeds.

Pro r5 Phase 4 (``notes_internal/pro_on_fi_theory_grammar5.txt`` §D2 lens
contract, §D8 type sketch). This is the *adapter* that brings the existing H2
recognizers — the BINDER (``defined_terms.py``) and the USE resolver
(``term_use.py``) — into the Legal Surface Graph as typed seeds. It edits
neither recognizer; both are imported READ-ONLY (the §D4 substrate-first rule:
the lens reads only the bundle).

Seeds emitted (§D8 vocabulary):

  * ``definition_binding`` node — one per :class:`DefinedTermBinding`.
  * ``term_use`` node          — one per :class:`TermUse`, status mapped from the
                                 resolver's ``resolved``/``open``/``ambiguous``.
  * ``term_symbol_entity`` node — one per canonical defined term (the entity
                                 handle; ``local_discriminator`` = canonical term
                                 id, so the assembler mints ``entity:<term id>``).
  * ``defines_term`` edge      — definition_binding → term_symbol_entity.
  * ``uses_term`` edge (INTRINSIC) — term_use → definition_binding, emitted only
                                 where the resolver already tied the use to ONE
                                 binding inside this lens's own seeds (§D2: a lens
                                 emits intrinsic edges only; cross-lens resolution
                                 is an edge pass).

Residuals: a binding the binder flagged ``unsupported_morphology`` is still a
real definition site, but its later-use inflection is not owned. It is emitted
as a ``definition_binding`` node AND a :class:`SurfaceResidualSeed` so the
unsupported morphology is fail-loud, never a silent gap (§D2 residual contract).

Source anchoring: every span is located in the bundle's ``raw_text`` via
``locate_span`` (the §D4 coordinate space). The recognizers already report byte
offsets into the same ``<p>``-joined body text, but we re-locate from the
matched surface so the span is anchored in the *bundle's* unit, never a
fabricated offset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
    source_bytes_of,
)
from lawvm.finland.legal_surface.bundle import (
    decode_body_text,
    locate_span,
    span_ref_at,
)
from lawvm.finland.references.defined_terms import (
    STATUS_UNSUPPORTED_MORPHOLOGY,
    DefinedTermBinding,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.term_use import (
    STATUS_AMBIGUOUS,
    STATUS_OPEN,
    STATUS_RESOLVED,
    TermUse,
    resolve_term_uses,
)

LENS_ID = "fi.definitions.v0"
SCHEMA_VERSION = "v0"

# rule_id values recorded on the emitted seeds (documenting which recognizer /
# arm produced each fact). Closed set.
RULE_BINDING = "fi.definitions.binding"
RULE_TERM_USE = "fi.definitions.term_use"
RULE_TERM_SYMBOL = "fi.definitions.term_symbol"
RULE_DEFINES_TERM = "fi.definitions.defines_term"
RULE_USES_TERM = "fi.definitions.uses_term"
RULE_UNSUPPORTED_MORPHOLOGY = "fi.definitions.unsupported_morphology"

# Resolver status → NODE_STATUS for a term_use node. The resolver's vocabulary is
# already a subset of the surface NODE_STATUSES, so this is an explicit identity
# map (documented, not magic) that fails loud on any unexpected status.
_TERM_USE_STATUS: Mapping[str, str] = {
    STATUS_RESOLVED: "resolved",
    STATUS_OPEN: "open",
    STATUS_AMBIGUOUS: "ambiguous",
}


def _canonical_term_id(term: str) -> str:
    """Canonical id for a defined term's symbol entity.

    The discriminator the assembler turns into ``entity:<canonical id>``. Two
    bindings of the SAME surface term share one symbol entity (that is exactly
    what makes ``duplicate_definition`` a graph query over a shared entity).
    """
    return f"fi.term:{term.strip().lower()}"


def _local_binding_id(binding: DefinedTermBinding, index: int) -> str:
    """Lens-local discriminator for a binding node seed.

    Includes the binder span so two bindings of the same surface term get
    distinct ``definition_binding`` nodes (the assembler mints stable ids; the
    discriminator must be unique per distinct binding site).
    """
    span = binding.source_span
    return f"binding:{index}:{span.byte_offset}:{span.byte_len}:{binding.term}"


def _local_use_id(use: TermUse, index: int) -> str:
    span = use.source_span
    return f"use:{index}:{span.byte_offset}:{span.byte_len}:{use.term_surface}"


@dataclass(frozen=True, slots=True)
class _BindingSeed:
    """A binding paired with its minted lens-local node discriminator + term id."""

    binding: DefinedTermBinding
    local_id: str
    term_id: str


class DefinitionLens:
    """SurfaceLens adapter over the H2 binder + term-use resolver.

    Implements the ``lawvm.core.legal_surface_lens.SurfaceLens`` protocol. Emits
    intrinsic edges only; cross-lens resolution is left to a ``SurfaceEdgePass``
    (see ``fi.definition_closure.v0``).
    """

    lens_id: str = LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = SCHEMA_VERSION
    produces_node_kinds: tuple[str, ...] = (
        "definition_binding",
        "term_use",
        "term_symbol_entity",
    )
    produces_edge_kinds: tuple[str, ...] = ("defines_term", "uses_term")
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        edge_seeds: list[SurfaceEdgeSeed] = []
        residuals: list[SurfaceResidualSeed] = []

        binding_count = 0
        use_count = 0
        residual_count = 0
        term_ids_seen: set[str] = set()

        for unit in bundle.units:
            seeds = self._analyze_unit(unit)
            node_seeds.extend(seeds.node_seeds)
            edge_seeds.extend(seeds.edge_seeds)
            residuals.extend(seeds.residuals)
            binding_count += seeds.binding_count
            use_count += seeds.use_count
            residual_count += len(seeds.residuals)
            term_ids_seen.update(seeds.term_ids)

        coverage: dict[str, object] = {
            "definition_bindings": binding_count,
            "term_uses": use_count,
            "term_symbol_entities": len(term_ids_seen),
            "unsupported_morphology_residuals": residual_count,
        }
        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=tuple(edge_seeds),
            residuals=tuple(residuals),
            diagnostics=(),
            coverage=coverage,
        )

    # -- per-unit analysis ----------------------------------------------------

    @dataclass(frozen=True, slots=True)
    class _UnitSeeds:
        node_seeds: tuple[SurfaceNodeSeed, ...]
        edge_seeds: tuple[SurfaceEdgeSeed, ...]
        residuals: tuple[SurfaceResidualSeed, ...]
        binding_count: int
        use_count: int
        term_ids: tuple[str, ...]

    def _analyze_unit(self, unit: SourceSurfaceUnit) -> "DefinitionLens._UnitSeeds":
        # The §D4 bridge carries the raw XML as the typed ``source_bytes`` unit
        # view for adapter lenses; the recognizers here, however, operate on the
        # statute body text, and the bundle's raw_text IS that body text (the same
        # <p>-joined decode of those exact source bytes). We therefore run the
        # recognizers over raw_text — the coordinate space locate_span anchors
        # against — so every minted span stays in the bundle's own coordinate
        # system. We assert the bridge contract holds (raw_text consistent with the
        # bridged source bytes) rather than re-decoding into a divergent space.
        xml_bytes = source_bytes_of(unit)
        body_text = unit.raw_text
        if not body_text:
            return self._UnitSeeds((), (), (), 0, 0, ())
        if isinstance(xml_bytes, (bytes, bytearray)) and decode_body_text(
            bytes(xml_bytes)
        ) != body_text:  # pragma: no cover — defensive bridge-contract guard
            raise ValueError(
                f"{LENS_ID}: bundle raw_text diverges from bridged xml_bytes for "
                f"unit {unit.source_unit_id!r}; spans would not anchor (§D4)"
            )

        source_file = unit.work_id
        bindings = recognize_defined_term_bindings(body_text, source_file=source_file)
        uses = resolve_term_uses(body_text, bindings, source_file=source_file)

        node_seeds: list[SurfaceNodeSeed] = []
        edge_seeds: list[SurfaceEdgeSeed] = []
        residuals: list[SurfaceResidualSeed] = []

        # -- definition_binding nodes + term_symbol entities + defines_term edges
        binding_seeds: list[_BindingSeed] = []
        # binding identity (by object) → lens-local discriminator, so a use can be
        # tied back to the exact binding seed it resolved to (intrinsic edge).
        binding_local_by_id: dict[int, str] = {}
        term_ids_emitted: set[str] = set()

        for index, binding in enumerate(bindings):
            local_id = _local_binding_id(binding, index)
            term_id = _canonical_term_id(binding.term)
            binding_seeds.append(_BindingSeed(binding, local_id, term_id))
            binding_local_by_id[id(binding)] = local_id

            # Anchor the binding on the definiendum surface when it round-trips
            # verbatim (narrowest, points at the term itself). It does NOT always:
            # a recognizer may normalise whitespace in the captured term surface
            # (collapsing the newlines/indentation a multi-word definiendum spans
            # in the body) so ``str.find`` of that surface misses. The binding,
            # however, always carries the EXACT char range of the construct it
            # matched, in this same coordinate space (the lens runs the recognizer
            # over ``unit.raw_text``). Fall back to that real span so a binding is
            # always a properly anchored source fact — never a contract-violating
            # seed with no source_ref, which would abort the whole graph build.
            ref, _ = locate_span(unit, binding.term, cursor=binding.source_span.byte_offset)
            if ref is None:
                ref, _ = locate_span(unit, binding.term)
            if ref is None:
                span = binding.source_span
                ref = span_ref_at(
                    unit, span.byte_offset, span.byte_offset + span.byte_len
                )

            # No span recoverable AT ALL (the recognizer's own construct offsets do
            # not fall inside the bundle's coordinate space) — a source-fact
            # ``definition_binding`` node has no truthful anchor. Per the
            # no-silent-drop contract, emit a typed residual instead of a
            # contract-violating seed (which would abort the whole graph build) and
            # skip the node/entity/edge for this binding — never a fabricated
            # offset, never a crash.
            if ref is None:  # pragma: no cover — defensive; the construct span is in-range in practice
                residual_count = len(residuals)
                residuals.append(
                    SurfaceResidualSeed(
                        residual_kind="definition_unanchorable",
                        source_ref=None,
                        local_discriminator=f"residual:{residual_count}:{local_id}",
                        rule_id=RULE_BINDING,
                        reason_code="unanchorable_binding_span",
                        payload={
                            "term": binding.term,
                            "term_id": term_id,
                            "binding_kind": binding.binding_kind,
                            "binder_status": binding.status,
                            "construct_offset": binding.source_span.byte_offset,
                            "construct_len": binding.source_span.byte_len,
                        },
                    )
                )
                continue

            node_seeds.append(
                SurfaceNodeSeed(
                    node_kind="definition_binding",
                    source_ref=ref,
                    local_discriminator=local_id,
                    rule_id=RULE_BINDING,
                    node_status="asserted",
                    payload={
                        "term": binding.term,
                        "target_ref": binding.target_ref,
                        "expansion": binding.expansion,
                        "scope": binding.scope,
                        "binding_kind": binding.binding_kind,
                        "binder_status": binding.status,
                        "term_id": term_id,
                    },
                )
            )

            # term_symbol_entity: one per canonical term (deduped by the assembler
            # — identical payload for the same term id is fine; divergent fails).
            if term_id not in term_ids_emitted:
                term_ids_emitted.add(term_id)
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind="term_symbol_entity",
                        source_ref=None,
                        local_discriminator=term_id,
                        rule_id=RULE_TERM_SYMBOL,
                        node_status="asserted",
                        payload={"term": binding.term.strip().lower()},
                        authority_role="entity_handle",
                    )
                )

            # defines_term: definition_binding -> term_symbol_entity
            edge_seeds.append(
                SurfaceEdgeSeed(
                    edge_kind="defines_term",
                    src_local=local_id,
                    dst_local=term_id,
                    rule_id=RULE_DEFINES_TERM,
                    surface_edge_status="asserted",
                    payload={"term_id": term_id},
                )
            )

            # Residual: binder flagged unsupported morphology — the definition is
            # real but later-use inflection is not owned. Fail-loud, not dropped.
            if binding.status == STATUS_UNSUPPORTED_MORPHOLOGY:
                residual_count = len(residuals)
                residuals.append(
                    SurfaceResidualSeed(
                        residual_kind="definition_unsupported_morphology",
                        source_ref=ref,
                        local_discriminator=f"residual:{residual_count}:{local_id}",
                        rule_id=RULE_UNSUPPORTED_MORPHOLOGY,
                        reason_code="unsupported_morphology",
                        payload={
                            "term": binding.term,
                            "term_id": term_id,
                            "binding_kind": binding.binding_kind,
                        },
                    )
                )

        # -- term_use nodes + intrinsic uses_term edges ---------------------------
        cursor = 0
        for index, use in enumerate(uses):
            status = _TERM_USE_STATUS.get(use.status)
            if status is None:  # pragma: no cover — fail loud on unexpected status
                raise ValueError(
                    f"{LENS_ID}: term-use resolver returned unknown status "
                    f"{use.status!r} for surface {use.term_surface!r}"
                )
            local_id = _local_use_id(use, index)
            ref, cursor = locate_span(unit, use.term_surface, cursor=cursor)
            if ref is None:
                ref, _ = locate_span(unit, use.term_surface)
            node_seeds.append(
                SurfaceNodeSeed(
                    node_kind="term_use",
                    source_ref=ref,
                    local_discriminator=local_id,
                    rule_id=RULE_TERM_USE,
                    node_status=status,
                    payload={
                        "term_surface": use.term_surface,
                        "lemma": use.lemma,
                        "resolver_status": use.status,
                        "resolver_rule_id": use.rule_id,
                        "candidate_count": len(use.bindings),
                    },
                )
            )

            # Intrinsic uses_term edge: only when the resolver tied the use to
            # EXACTLY ONE binding that lives in THIS lens's seeds.
            if (
                use.status == STATUS_RESOLVED
                and use.binding is not None
                and id(use.binding) in binding_local_by_id
            ):
                binding_local = binding_local_by_id[id(use.binding)]
                edge_seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="uses_term",
                        src_local=local_id,
                        dst_local=binding_local,
                        rule_id=RULE_USES_TERM,
                        surface_edge_status="asserted",
                        payload={
                            "term": use.binding.term,
                            "term_id": _canonical_term_id(use.binding.term),
                        },
                    )
                )

        return self._UnitSeeds(
            node_seeds=tuple(node_seeds),
            edge_seeds=tuple(edge_seeds),
            residuals=tuple(residuals),
            binding_count=len(bindings),
            use_count=len(uses),
            term_ids=tuple(sorted(term_ids_emitted)),
        )


__all__ = [
    "DefinitionLens",
    "LENS_ID",
    "RULE_BINDING",
    "RULE_DEFINES_TERM",
    "RULE_TERM_SYMBOL",
    "RULE_TERM_USE",
    "RULE_UNSUPPORTED_MORPHOLOGY",
    "RULE_USES_TERM",
]
