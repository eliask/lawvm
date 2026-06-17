"""Build a LegalSurfaceGraph for a Finnish statute (the finland integration).

Registers the Finnish surface lenses, builds the SourceSurfaceBundle, runs each
lens, and assembles the graph via the core assembler (Pro r5). This is the
finland top-level entry: ``build_legal_surface_graph(xml_bytes, statute_id)``.

It registers all merged Finnish surface lenses: references, definitions,
temporal, actor/modal (Phase 4–6), and the H5/H6 frame families — delegation,
procedure, sanction, and exception/condition (Phase 8, nodes only; frame
edges/lints deferred). Resolution of by-name / EU-nickname placeholders is
OPT-IN: pass registries and they flow to the reference lens via the analysis
context.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import (
    SurfaceEdgePass,
    assemble_surface_graph,
)
from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceUnitRef,
    SurfaceLensRun,
)
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SurfaceAnalysisContext,
    SurfaceLens,
    SurfaceLensResult,
)
from lawvm.core.legal_surface_lints import (
    LintRunReport,
    SurfaceLintPass,
    run_lint_passes,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.actor_modal import ActorModalLens
from lawvm.finland.legal_surface.lenses.anaphora import AnaphoraLens
from lawvm.finland.legal_surface.lenses.definitions import DefinitionLens
from lawvm.finland.legal_surface.lenses.delegation import DelegationLens
from lawvm.finland.legal_surface.lenses.exception_condition import (
    ExceptionConditionLens,
)
from lawvm.finland.legal_surface.lenses.procedure import ProcedureLens
from lawvm.finland.legal_surface.lenses.references import ReferenceLens
from lawvm.finland.legal_surface.lenses.sanction import SanctionLens
from lawvm.finland.legal_surface.lenses.temporal import TemporalLens
from lawvm.finland.legal_surface.lints import (
    AmbiguousTermUseLintPass,
    DeadDefinitionLintPass,
    DuplicateDefinitionLintPass,
    UnboundTermLintPass,
    UsedBeforeDefinitionLintPass,
)
from lawvm.finland.legal_surface.frame_lints import (
    DelegationWithoutInstrumentLintPass,
)
from lawvm.finland.legal_surface.frame_passes import ActorTemporalColocationPass
from lawvm.finland.legal_surface.frame_relations import (
    ExceptionScopesFramePass,
    FrameActorColocationPass,
    SanctionConditionLintPass,
)
from lawvm.finland.legal_surface.cross_lens_passes import (
    FrameReferenceColocationPass,
    FrameTemporalColocationPass,
)
from lawvm.finland.legal_surface.passes import DefinitionClosurePass
from lawvm.finland.legal_surface.ref_lints import (
    AmbiguousReferenceLintPass,
    BrokenReferenceLintPass,
    OpenReferenceLintPass,
    StatuteOnlyMissLintPass,
)

# The registered Finnish surface lenses (explicit registration, not a hidden
# global plugin scan — Pro r5 §D2). The H5/H6 frame lenses joined at Phase 8
# (nodes only; their cross-frame edge/lint passes are deferred).
DEFAULT_LENSES: tuple[SurfaceLens, ...] = (
    ReferenceLens(),
    DefinitionLens(),
    TemporalLens(),
    ActorModalLens(),
    DelegationLens(),
    ProcedureLens(),
    SanctionLens(),
    ExceptionConditionLens(),
    AnaphoraLens(),
)

# Cross-lens edge passes, run in declared order after assembly (Pro r5 §D5).
# ActorTemporalColocationPass is an EXPERIMENTAL candidate-affordance pass
# (H5/H6): it surfaces candidate-status actor_modal ↔ temporal colocation edges,
# never asserted facts.
DEFAULT_EDGE_PASSES: tuple[SurfaceEdgePass, ...] = (
    DefinitionClosurePass(),
    ActorTemporalColocationPass(),
    # Cross-lens interlinks — the fabric that ties the 8 lenses' node sets
    # together. All candidate-status, surface-colocation affordances (never
    # asserted facts); each edge carries char_distance so a strict consumer can
    # filter to containment-only.
    FrameReferenceColocationPass(),
    FrameTemporalColocationPass(),
    ExceptionScopesFramePass(),
    FrameActorColocationPass(),
)

# Lint passes (graph queries; Pro r5 §D6). Not run by build_legal_surface_graph
# itself — call lint_surface_graph(graph) to derive them.
DEFAULT_LINT_PASSES: tuple[SurfaceLintPass, ...] = (
    # H2 definition lints (Phase 4).
    UnboundTermLintPass(),
    DeadDefinitionLintPass(),
    DuplicateDefinitionLintPass(),
    UsedBeforeDefinitionLintPass(),
    AmbiguousTermUseLintPass(),
    # H1 reference lints (Phase 5).
    BrokenReferenceLintPass(),
    OpenReferenceLintPass(),
    StatuteOnlyMissLintPass(),
    AmbiguousReferenceLintPass(),
    # EXPERIMENTAL H5/H6 frame affordance lints (candidate, never a legal claim).
    DelegationWithoutInstrumentLintPass(),
    SanctionConditionLintPass(),
)


def _source_unit_refs(bundle: SourceSurfaceBundle) -> tuple[SourceUnitRef, ...]:
    return tuple(
        SourceUnitRef(
            source_unit_id=u.source_unit_id,
            work_id=u.work_id,
            address=u.address,
            source_hash=u.source_hash,
        )
        for u in bundle.units
    )


def _lens_run(lens: SurfaceLens, result: SurfaceLensResult) -> SurfaceLensRun:
    return SurfaceLensRun(
        lens_id=lens.lens_id,
        schema_version=lens.schema_version,
        jurisdiction=lens.jurisdiction,
        produced_node_kinds=lens.produces_node_kinds,
        produced_edge_kinds=lens.produces_edge_kinds,
        coverage=result.coverage,
    )


def build_legal_surface_graph(
    xml_bytes: bytes,
    statute_id: str,
    *,
    statute_registry: object | None = None,
    eu_registry: object | None = None,
    surface_time: str | None = None,
    lenses: tuple[SurfaceLens, ...] = DEFAULT_LENSES,
    edge_passes: tuple[SurfaceEdgePass, ...] = DEFAULT_EDGE_PASSES,
) -> LegalSurfaceGraph:
    """Assemble the Legal Surface Graph for one Finnish statute.

    Builds the whole-body bundle, runs each registered lens over it, and
    assembles the graph (minting + firewall + ordered edge passes). Pass
    ``statute_registry`` / ``eu_registry`` to enable reference resolution
    (by-name / EU-nickname placeholders → resolved/ambiguous/statute_only).
    """
    bundle = build_surface_bundle(xml_bytes, statute_id, surface_time=surface_time)
    context = SurfaceAnalysisContext(
        surface_time=surface_time,
        options={
            "statute_registry": statute_registry,
            "eu_registry": eu_registry,
        },
    )

    results: list[SurfaceLensResult] = []
    lens_runs: list[SurfaceLensRun] = []
    for lens in lenses:
        result = lens.analyze(bundle, context=context)
        results.append(result)
        lens_runs.append(_lens_run(lens, result))

    return assemble_surface_graph(
        subject=bundle.subject,
        source_units=_source_unit_refs(bundle),
        lens_results=tuple(results),
        lens_runs=tuple(lens_runs),
        edge_passes=edge_passes,
    )


def lint_surface_graph(
    graph: LegalSurfaceGraph,
    *,
    lint_passes: tuple[SurfaceLintPass, ...] = DEFAULT_LINT_PASSES,
) -> LintRunReport:
    """Derive lints from an assembled graph (Pro r5 §D6: lints are graph queries).

    Kept separate from graph assembly: the graph is the source of truth; lints
    are a derived projection over it. Surface-only by construction (the runner
    refuses any lint pass not declaring ``surface_only=True``).
    """
    return run_lint_passes(graph, lint_passes)
