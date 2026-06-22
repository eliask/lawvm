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

import os

from lawvm.core.legal_surface_assembler import (
    SurfaceEdgePass,
    assemble_surface_graph,
)
from lawvm.core.legal_surface_graph import (
    NODE_STATUSES,
    LegalSurfaceGraph,
    SourceUnitRef,
    SurfaceLensRun,
    SurfaceNode,
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
from lawvm.core.stage_result import (
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    Residual,
    StageResult,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle_staged
from lawvm.finland.legal_surface.source_syntax_graph import (
    assemble_source_syntax_graph_staged,
)
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
    forest_structural_attachment_passes,
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


# ── StageResult endgame row #5 (legal-surface waist) ─────────────────────────
#
# The surface graph's per-node resolution-status taxonomy IS a four-class
# coverage partition; this maps it onto the canonical core
# ``CoverageCertificate`` / ``Residual`` account WITHOUT removing the node-status
# field (additive). The status→class mapping is the orchestrator-RESOLVED 2D
# decision: ``broken`` (a reference that named a target which does not exist) is
# the ONLY violation class — ``ambiguous`` is the tag-don't-guess FRONTIER and is
# a NON-blocking residual, never a violation.
#
# Every SurfaceNode carries exactly one status in ``NODE_STATUSES`` (graph-build
# validation enforces it), so the partition is TOTAL over all nodes
# (``is_partition()`` holds). The four classes:
_SURFACE_OWNED_STATUSES: frozenset[str] = frozenset(
    {"resolved", "statute_only", "asserted", "present"}
)
_SURFACE_BENIGN_STATUSES: frozenset[str] = frozenset({"not_applicable"})
_SURFACE_RESIDUAL_STATUSES: frozenset[str] = frozenset(
    {"open", "ambiguous", "unsupported"}
)
_SURFACE_VIOLATION_STATUSES: frozenset[str] = frozenset({"broken"})


def _node_status_str(node: SurfaceNode) -> str:
    """A node's status as a plain string (handles enum-or-str carriers)."""
    raw = node.status
    return str(getattr(raw, "value", raw))


def _surface_graph_stage_account(
    graph: LegalSurfaceGraph,
) -> tuple[CoverageCertificate, tuple[Residual, ...]]:
    """Project the graph's node-status taxonomy onto the typed coverage account.

    Returns the ``CoverageCertificate`` (a total four-class partition over the
    graph's nodes) and the per-non-owned-node ``Residual`` tuple. ``broken`` →
    a blocking ``unowned_violation`` residual (a reference that named a target
    which does not exist — the genuine failure class); ``open`` / ``ambiguous`` /
    ``unsupported`` and every ``surface_residual`` node → a non-blocking
    ``typed_residual`` (the tag-don't-guess frontier). Fail loud on a status
    outside the closed taxonomy rather than silently miscounting the partition.
    """
    owned = benign = residual_n = violation = 0
    residuals: list[Residual] = []
    for node in graph.nodes.values():
        status = _node_status_str(node)
        ref = node.source_ref
        scope = node.node_id
        if status in _SURFACE_OWNED_STATUSES:
            owned += 1
            continue
        if status in _SURFACE_BENIGN_STATUSES:
            benign += 1
            continue
        if status in _SURFACE_VIOLATION_STATUSES:
            violation += 1
            residuals.append(
                Residual(
                    kind="unowned_violation",
                    reason="surface_broken",
                    scope=scope,
                    source_unit_id=ref.source_unit_id if ref else "",
                    char_start=ref.char_start if ref else None,
                    char_end=ref.char_end if ref else None,
                    text=str(node.payload.get("surface_text", "")),
                    blocking=True,
                )
            )
            continue
        is_surface_residual = node.node_kind == "surface_residual"
        if status in _SURFACE_RESIDUAL_STATUSES or is_surface_residual:
            residual_n += 1
            residuals.append(
                Residual(
                    kind="typed_residual",
                    reason="surface_" + status,
                    scope=scope,
                    source_unit_id=ref.source_unit_id if ref else "",
                    char_start=ref.char_start if ref else None,
                    char_end=ref.char_end if ref else None,
                    text=str(node.payload.get("surface_text", "")),
                    blocking=False,
                )
            )
            continue
        # A status outside the closed 2D taxonomy: fail loud (never silently
        # leave it out of the partition — that would break is_partition()).
        raise ValueError(
            f"FI surface graph node {node.node_id!r} (kind={node.node_kind!r}) "
            f"carries status {status!r} which is outside the closed surface "
            f"coverage taxonomy {sorted(NODE_STATUSES)}"
        )
    coverage = CoverageCertificate(
        unit="surface_nodes",
        total=len(graph.nodes),
        owned=owned,
        benign=benign,
        residual=residual_n,
        violation=violation,
        totality_claimed=True,
    )
    return coverage, tuple(residuals)


def build_legal_surface_graph_staged(
    xml_bytes: bytes,
    statute_id: str,
    *,
    statute_registry: object | None = None,
    eu_registry: object | None = None,
    surface_time: str | None = None,
    lenses: tuple[SurfaceLens, ...] = DEFAULT_LENSES,
    edge_passes: tuple[SurfaceEdgePass, ...] = DEFAULT_EDGE_PASSES,
) -> StageResult[LegalSurfaceGraph]:
    """Assemble the Legal Surface Graph as a typed ``StageResult`` (row #5).

    The transform-waist staged form of :func:`build_legal_surface_graph`. The
    ``value`` is the assembled graph (byte-identical to the value-only form); the
    accounts relocate the ad-hoc per-node string statuses into the typed
    canonical account:

      * ``coverage`` — a total four-class :class:`CoverageCertificate` partition
        over the graph nodes (owned/benign/residual/violation; the 2D mapping).
      * ``residuals`` — one ``Residual`` per non-owned node (``broken`` →
        blocking ``unowned_violation``; ``open``/``ambiguous``/``unsupported`` +
        ``surface_residual`` nodes → non-blocking ``typed_residual``).
      * ``evidence`` — re-carries the #2 bundle witness (footing belongs upstream;
        the surface waist mints no new source identity — ESCALATE-1D).
      * ``authority`` — neutral (surface facts are never replay authority).
      * ``findings`` — empty: the broken/open lints stay derivable via
        :func:`lint_surface_graph`, and the load-bearing broken-ref signal rides
        ``residuals`` (the channel the bill consumer branches on). Promoting the
        lints into typed ``Finding`` records would require new governed registry
        codes (registry churn) and is out of this 0-delta waist's scope.
    """
    graph = _assemble_surface_graph_value(
        xml_bytes,
        statute_id,
        statute_registry=statute_registry,
        eu_registry=eu_registry,
        surface_time=surface_time,
        lenses=lenses,
        edge_passes=edge_passes,
    )
    coverage, residuals = _surface_graph_stage_account(graph.value)
    return StageResult(
        value=graph.value,
        evidence=graph.evidence,
        residuals=residuals,
        findings=(),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
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

    Value-only wrapper over :func:`build_legal_surface_graph_staged` (0-delta:
    existing consumers that want just the graph are untouched).
    """
    return build_legal_surface_graph_staged(
        xml_bytes,
        statute_id,
        statute_registry=statute_registry,
        eu_registry=eu_registry,
        surface_time=surface_time,
        lenses=lenses,
        edge_passes=edge_passes,
    ).value


def _gate_forest_coverage(
    bundle: SourceSurfaceBundle, *, statute_id: str
) -> None:
    """Fail loud if any unit's parse FOREST coverage fails the totality contract.

    The source-syntax waist's production gate (StageResult endgame row #4). For
    each bundle unit, assemble the forest as a typed
    :class:`~lawvm.core.stage_result.StageResult` and READ its ``.coverage``
    (the token-partition account) + ``.residuals`` (the typed residue). Two
    branches:

      * a NON-partition coverage (the four classes do not sum to ``total``) is a
        STRUCTURAL leak — always a blocker, raise unconditionally (mirrors the
        bundle gate's ``is_partition`` check above);
      * a signal-bearing ``violation>0`` (a silent-unowned cheap-signal span no
        construction family owned) is the no-silent-drop FRONTIER. Per the
        established forest/census contract
        (``union_ownership_census`` honours ``LAWVM_PARSE_TOTALITY`` as the
        HARD gate for the silent bucket; a non-zero silent count is the surfaced,
        non-blocking frontier in normal operation, NOT a steady-state failure),
        the HARD RAISE here fires ONLY under ``LAWVM_PARSE_TOTALITY``. In normal
        operation the violation is surfaced as a blocking ``unowned_violation``
        residual on the StageResult (always type-carried + read), never silently
        dropped, but does not block the graph build — exactly the same semantics
        the L0 census already applies to the same ``silent_tokens`` bucket.

    The raise embeds the verbatim offending span text taken straight from the
    StageResult's blocking ``unowned_violation`` residual (self-evidencing —
    never an opaque count). ``typed_residual`` (surfaced owned residue) is
    NON-blocking and never raises here.
    """
    totality_mode = bool(os.environ.get("LAWVM_PARSE_TOTALITY"))
    for unit in bundle.units:
        forest_stage = assemble_source_syntax_graph_staged(
            subject=bundle.subject, unit=unit
        )
        cov = forest_stage.coverage
        if not cov.is_partition():
            raise ValueError(
                "FI source-syntax forest coverage is not a total partition of "
                f"unit {unit.source_unit_id} ({statute_id}): owned={cov.owned} "
                f"benign={cov.benign} residual={cov.residual} "
                f"violation={cov.violation} total={cov.total}"
            )
        if totality_mode and cov.violation > 0:
            blockers = [
                r
                for r in forest_stage.residuals
                if r.kind == "unowned_violation" and r.blocking
            ]
            spans = "; ".join(repr(r.text) for r in blockers[:5])
            raise ValueError(
                "FI source-syntax forest coverage carries an unowned violation "
                f"for unit {unit.source_unit_id} ({statute_id}): "
                f"violation={cov.violation} tokens of {cov.total}; "
                f"silent-unowned span(s): {spans}"
            )


def _assemble_surface_graph_value(
    xml_bytes: bytes,
    statute_id: str,
    *,
    statute_registry: object | None = None,
    eu_registry: object | None = None,
    surface_time: str | None = None,
    lenses: tuple[SurfaceLens, ...] = DEFAULT_LENSES,
    edge_passes: tuple[SurfaceEdgePass, ...] = DEFAULT_EDGE_PASSES,
) -> StageResult[LegalSurfaceGraph]:
    """Build the graph + carry the #2 bundle evidence (the value-path core).

    Returns a thin StageResult carrying only ``value`` + ``evidence`` (the
    coverage/residual account is layered on by
    :func:`build_legal_surface_graph_staged`). Split out so the staged producer
    threads the upstream bundle witness without recomputing the build.
    """
    # Token/source-unit waist (StageResult endgame row #2): consume the STAGED
    # bundle builder and READ its returned coverage account. The segmentation
    # partition is no longer embedded-but-unread carrier prose — it is a typed
    # CoverageCertificate this production consumer checks. Fail loud if the body
    # partition is not total or carries any unowned signal-bearing violation: a
    # non-partition / violation here means the body was not fully accounted for,
    # which must never silently flow into the assembled surface graph.
    surface_stage = build_surface_bundle_staged(
        xml_bytes, statute_id, surface_time=surface_time
    )
    coverage = surface_stage.coverage
    if not coverage.is_partition():
        raise ValueError(
            "FI surface bundle coverage is not a total partition of the body "
            f"({statute_id}): owned={coverage.owned} residual={coverage.residual} "
            f"benign={coverage.benign} violation={coverage.violation} "
            f"total={coverage.total}"
        )
    if coverage.violation > 0:
        raise ValueError(
            "FI surface bundle coverage carries an unowned violation for "
            f"{statute_id}: violation={coverage.violation} chars of {coverage.total}"
        )
    bundle = surface_stage.value

    # Source-syntax / parse-forest waist (StageResult endgame row #4): assemble the
    # construction parse FOREST for each bundle unit as a typed StageResult and READ
    # its token-partition coverage. The forest's SyntaxCoverage partition (the L0
    # union-ownership census) is no longer an embedded-but-unread account — it is a
    # returned CoverageCertificate this production consumer checks, EXACTLY parallel
    # to the bundle gate above. A silent-unowned cheap-signal span (the parse
    # ``unowned_violation`` class) is a blocking typed fact that must NEVER flow
    # silently into the assembled surface graph: fail loud, carrying the verbatim
    # offending span text from the StageResult's blocking residual. The forests are
    # the SAME cached objects the downstream forest/edge passes reuse, so this is
    # additive 0-delta plumbing (the GREEN corpus carries 0 violations → no raise).
    _gate_forest_coverage(bundle, statute_id=statute_id)

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
    #
    # The forest-structural attachment pass (forest_structural_attachment_passes)
    # recovers the intra-sentence pass's ``candidate`` residue (a condition/
    # exception cue whose governing deontic core was split into a NEIGHBOURING
    # clause-segmented sentence of the SAME provision). It attaches via the
    # SourceSyntaxGraph forest's structural segment + ``inherits_chapeau`` edges
    # (the structure the clause split drops) — chapeau-inheritance for list-item
    # conditions, the enclosing prose/chapeau segment for in-sentence ones — with a
    # proximity fallback so no qualifier is dropped. A STRICT SUPERSET of the intra/
    # cross-sentence passes (fires only on previously-edgeless candidates), spliced
    # in ADDITIVELY, surface_only, candidate-not-asserted.
    all_edge_passes = (
        edge_passes
        + condition_attachment_passes(bundle)
        + deontic_frame_attachment_passes(bundle)
        + norm_subject_attachment_passes(bundle)
        + procedure_governance_passes(bundle)
        + delegation_instrument_passes(bundle)
        + sanction_reference_passes(bundle)
        + enclosing_anaphora_passes(bundle)
        + forest_structural_attachment_passes(bundle)
    )

    graph = assemble_surface_graph(
        subject=bundle.subject,
        source_units=_source_unit_refs(bundle),
        lens_results=tuple(results),
        lens_runs=tuple(lens_runs),
        edge_passes=all_edge_passes,
    )
    # Re-carry the #2 bundle witness (ESCALATE-1D: footing belongs upstream; the
    # surface waist mints no new source identity). The coverage/residual account
    # is layered on by build_legal_surface_graph_staged.
    return StageResult(value=graph, evidence=surface_stage.evidence)


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
