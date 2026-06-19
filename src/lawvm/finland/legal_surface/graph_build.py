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
from lawvm.finland.legal_surface.annotation_compare import (
    GrammarAnnotationComparePass,
)
from lawvm.finland.legal_surface.lenses.actor_modal import ActorModalLens
from lawvm.finland.legal_surface.lenses.anaphora import AnaphoraLens
from lawvm.finland.legal_surface.lenses.annotation_witness import (
    AnnotationWitnessLens,
)
from lawvm.finland.legal_surface.lenses.definitions import DefinitionLens
from lawvm.finland.legal_surface.lenses.delegated_instrument import (
    DelegatedInstrumentLens,
)
from lawvm.finland.legal_surface.lenses.delegation import DelegationLens
from lawvm.finland.legal_surface.lenses.deontic_core import DeonticCoreLens
from lawvm.finland.legal_surface.lenses.enclosing_anaphora import (
    EnclosingAnaphoraLens,
)
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
from lawvm.finland.legal_surface.norm_composition import (
    condition_attachment_passes,
    delegation_instrument_passes,
    deontic_frame_attachment_passes,
    enclosing_anaphora_passes,
    norm_subject_attachment_passes,
    procedure_governance_passes,
    sanction_reference_passes,
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
    # ADDITIVE strangle: the DENSE construction deontic-core node lens runs
    # ALONGSIDE the sparse production ActorModalLens (never replacing it). It mints
    # one deontic_core node per construction modal core — the substrate Layer-2
    # deontic edges attach to (the actor_modal_frame oracle is too sparse to back
    # most attachments).
    DeonticCoreLens(),
    DelegationLens(),
    # ADDITIVE strangle: the construction delegated-instrument node lens runs
    # ALONGSIDE the recognizer DelegationLens. It mints one delegated_instrument
    # node per construction delegation core (anchored on the instrument anchor span)
    # — the INSTRUMENT-ENTITY substrate the Layer-2 delegation_grants_instrument edge
    # points at (the recognizer frame names the instrument only as a kind string).
    DelegatedInstrumentLens(),
    ProcedureLens(),
    SanctionLens(),
    ExceptionConditionLens(),
    # ADDITIVE enclosing-provision anaphor surface (Layer-2). Mints one
    # enclosing_anaphor_cue node per ``Tätä pykälää / Tätä momenttia ei sovelleta
    # …`` cue — the applicability anaphor whose referent is the section/subsection
    # it sits in. A DISTINCT node kind from exception_condition_cue (it never
    # pollutes the H6 cue census; the H6 recognizer does not key on the ``ei
    # sovelleta`` / ``sovelletaan`` matrix). The enclosing-anaphora edge pass
    # resolves it against the unit's provision_index and joins it to its own
    # provision's deontic cores.
    EnclosingAnaphoraLens(),
    AnaphoraLens(),
    # ADDITIVE annotation-witness surface (grammar7 §13-A). Mints one
    # annotation_reference_witness node per inline <ref> element — the markup
    # surface, explicitly NOT a reference_expr. It is a SEPARATE emitter: the
    # grammar productions (the ReferenceLens above) never consume it, and adding
    # it does not change the existing reference extraction. It runs ALONGSIDE the
    # ReferenceLens so the grammar-induced reference set can be compared against
    # the unmodified <ref> surface (the GrammarAnnotationComparePass below).
    # "delete annotation DEPENDENCE, not annotation USE."
    AnnotationWitnessLens(),
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
    # ADDITIVE grammar-vs-annotation comparison (grammar7 §13-B). Contrasts the
    # grammar-induced reference_expr set against the annotation_reference_witness
    # surface and mints grammar_annotation_compared edges carrying one of the
    # SEVEN NEUTRAL comparison statuses (§14: a contrast, never a conclusion —
    # grammar_only is NOT an "annotation bug", annotation_only is NOT a "parser
    # miss"). A surface_only QA affordance; it adds edges only, never perturbing
    # the existing reference nodes/edges.
    GrammarAnnotationComparePass(),
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

    # ADDITIVE Layer-2 strangle: the construction-derived deontic NORM edge pass
    # runs ALONGSIDE the proximity ExceptionScopesFramePass (which stays in
    # ``edge_passes``), never replacing it. It is built per-statute from the
    # bundle because the construction parse needs the source text. The proximity
    # pass remains the incumbent until the construction edge is proven superior.
    #
    # The deontic-frame attachment pass (delegates_to / sanctioned_by) is spliced
    # in ADDITIVELY too: it joins power cores to co-sentence delegation_frames and
    # prohibition/obligation cores to co-sentence sanction_frames, alongside (never
    # replacing) the proximity FrameActorColocationPass / ExceptionScopesFramePass.
    #
    # The norm-subject pass (norm_has_subject) binds each deontic core to the
    # actor_modal_frame carrying its addressee; the procedure-governance pass
    # (governed_by_procedure) joins obligation/power cores to co-sentence
    # procedure_frames. Both ADDITIVE, surface_only, candidate-not-asserted —
    # alongside (never replacing) the proximity incumbents.
    # The delegation-instrument pass (delegation_grants_instrument) joins each
    # recognizer delegation_frame to the construction delegated_instrument node(s)
    # its span contains — the norm->authorized-instrument link. Spliced in
    # ADDITIVELY, surface_only, candidate-not-asserted.
    #
    # The sanction-reference pass (sanction_defers_to_provision) is the PRINCIPLED
    # sanction attachment: where sanctioned_by can only co-occur (the duty↔
    # consequence link is not surface-recoverable from the modal-core ↔ frame
    # join), the penal-DEFERRAL construction ("rangaistaan … niin kuin §:ssä
    # säädetään") DOES carry a recoverable index — the forward provision reference
    # the penalty defers to, bound by a closed deferral cue. Spliced in ADDITIVELY,
    # surface_only, candidate-not-asserted (one deferral ref "asserted"; several
    # "ambiguous"; none → no edge, a standalone offence correctly left to
    # co-occurrence).
    #
    # The enclosing-section anaphora pass (enclosing_anaphora_passes) resolves
    # ``Tätä pykälää / Tätä momenttia ei sovelleta …`` against the unit's
    # provision_index: the anaphor's enclosing §/momentti IS the referent, so the
    # qualifier attaches (via the SAME condition/exception edge kinds, reason
    # ``resolved_by_enclosing_provision``) to that provision's deontic cores.
    # Spliced in ADDITIVELY, surface_only; a STRICT SUPERSET of the intra/cross-
    # sentence attachments (it fires only on the closed enclosing-anaphor cue
    # shapes those passes do not key on, never altering an existing edge).
    all_edge_passes = (
        edge_passes
        + condition_attachment_passes(bundle)
        + deontic_frame_attachment_passes(bundle)
        + norm_subject_attachment_passes(bundle)
        + procedure_governance_passes(bundle)
        + delegation_instrument_passes(bundle)
        + sanction_reference_passes(bundle)
        + enclosing_anaphora_passes(bundle)
    )

    return assemble_surface_graph(
        subject=bundle.subject,
        source_units=_source_unit_refs(bundle),
        lens_results=tuple(results),
        lens_runs=tuple(lens_runs),
        edge_passes=all_edge_passes,
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
