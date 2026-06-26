"""Machine-readable Finland frontend periodic table of abstractions.

Maps architectural axes (phase, structure, time, provenance, evidence, …) to
the modules that own instrumentation, registries, and proof projections.
Read-only catalog — no replay semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PeriodicAxis = Literal[
    "phase",
    "structure",
    "identity",
    "time",
    "operative",
    "lexical",
    "provenance",
    "evidence",
    "instrumentation",
]

CellStatus = Literal["filled", "partial", "hole", "closed"]


@dataclass(frozen=True, slots=True)
class PeriodicTableCell:
    """One abstraction cell on the Finland periodic table."""

    cell_id: str
    axis: PeriodicAxis
    cell_status: CellStatus
    module: str
    symbol: str = ""
    notes: str = ""


def finland_periodic_table_cells() -> tuple[PeriodicTableCell, ...]:
    """Return the current Finland periodic-table cell catalog."""

    return (
        PeriodicTableCell(
            cell_id="invariant_surface_matrix",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.core.invariant_surface_matrix",
            symbol="FI_REPLAY_FOLD_SURFACE",
            notes="Shared replay-fold tree + lint profile; audit and replay_fold agree.",
        ),
        PeriodicTableCell(
            cell_id="replay_fold_flattened_sublist",
            axis="structure",
            cell_status="filled",
            module="lawvm.finland.replay_fold_projection",
            symbol="project_replay_fold",
            notes="FI replay fold emits flattened_sublist_family findings (UK parity).",
        ),
        PeriodicTableCell(
            cell_id="elaboration_rule_registry",
            axis="phase",
            cell_status="filled",
            module="lawvm.finland.elaboration_rule_registry",
            symbol="ELABORATION_RULE_REGISTRY",
            notes="Uncovered-body and process_amendment rule families (25+ rules).",
        ),
        PeriodicTableCell(
            cell_id="elaboration_rule_dispatch",
            axis="phase",
            cell_status="filled",
            module="lawvm.finland.elaboration_rule_dispatch",
            symbol="run_registered_elaboration_stage",
            notes="Runtime stage dispatch + ELAB.REGISTRY_PIPELINE/STAGE observations for uncovered-body recovery.",
        ),
        PeriodicTableCell(
            cell_id="recovery_rule_registry",
            axis="provenance",
            cell_status="filled",
            module="lawvm.finland.recovery_rule_registry",
            symbol="recovery_rule_ids",
            notes="Named recovery rule IDs shared by proof surfaces and strict report.",
        ),
        PeriodicTableCell(
            cell_id="recovery_authorization_registry",
            axis="provenance",
            cell_status="filled",
            module="lawvm.finland.recovery_authorization_registry",
            symbol="recovery_authorization_kinds",
            notes="APPLY.* recovery finding kinds → authorization specs.",
        ),
        PeriodicTableCell(
            cell_id="source_pathology_proof_registry",
            axis="provenance",
            cell_status="filled",
            module="lawvm.finland.source_pathology_proof_registry",
            symbol="source_pathology_proof_rule",
            notes="Pathology codes → required claim kinds for strict-report proof rows.",
        ),
        PeriodicTableCell(
            cell_id="evidence_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.evidence_projector",
            symbol="project_evidence",
            notes="Unified findings + meta + proof_rows projection with deduped replay_meta channels.",
        ),
        PeriodicTableCell(
            cell_id="proof_surface_row_helpers",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.proof_surface_row_helpers",
            symbol="kind_slug",
            notes="Shared coercion, digest witnesses, and Finland claim templates for proof rows.",
        ),
        PeriodicTableCell(
            cell_id="pathology_failed_op_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.pathology_failed_op_projector",
            symbol="source_pathology_proof_surface_rows",
            notes="Source-pathology + failed-op execution-authorization proof rows.",
        ),
        PeriodicTableCell(
            cell_id="recovery_temporal_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.recovery_temporal_proof_projector",
            symbol="recovery_execution_authorization_rows_from_projection_rows",
            notes="TIME.* temporal events and recovery authorization proof rows.",
        ),
        PeriodicTableCell(
            cell_id="sparse_slot_certificate_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.sparse_slot_certificate_projector",
            symbol="sparse_slot_candidate_set_coverage_rows",
            notes="Sparse-slot elaboration candidate-set certificates.",
        ),
        PeriodicTableCell(
            cell_id="agreement_residual_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.agreement_residual_proof_projector",
            symbol="finlex_editorial_witness_agreement_residual_rows",
            notes="Finlex editorial witness + adjudication agreement residual rows.",
        ),
        PeriodicTableCell(
            cell_id="source_witness_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.source_witness_proof_projector",
            symbol="corrigendum_source_witness",
            notes="Consolidated/corrigendum/finlex/lineage source-witness proof rows.",
        ),
        PeriodicTableCell(
            cell_id="strict_report_evidence_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.strict_report_evidence_projector",
            symbol="finland_strict_report_evidence_surface",
            notes="Strict-report evidence envelope (report_kind + replay_claims gate).",
        ),
        PeriodicTableCell(
            cell_id="strict_report_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.strict_report_proof_projector",
            symbol="finland_strict_report_candidate_set_coverages",
            notes="Candidate-set certs, ownership closure, potential ops, execution authorizations.",
        ),
        PeriodicTableCell(
            cell_id="parser_facade",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.parser_facade",
            symbol="parse_tokens_production",
            notes="Grammar-primary johtolause lane with legacy fallback; census column parser_facade_lane.",
        ),
        PeriodicTableCell(
            cell_id="family_census",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.family_census",
            symbol="run_family_census",
            notes="Four-bucket differential census engine (match/superset/miss/decline); grammar migration scoreboard.",
        ),
        PeriodicTableCell(
            cell_id="clause_segment",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.clause_segment",
            symbol="build_clause_index",
            notes="Finnish sentence/clause segmentation substrate; SegmentationGraph total ownership.",
        ),
        PeriodicTableCell(
            cell_id="temporal_parse",
            axis="time",
            cell_status="filled",
            module="lawvm.finland.legal_surface.temporal_parse",
            symbol="parse_temporal_sentence",
            notes="Temporal/applicability construction parse; mirrors meta_parse cue precedence.",
        ),
        PeriodicTableCell(
            cell_id="temporal_census",
            axis="time",
            cell_status="filled",
            module="lawvm.finland.legal_surface.temporal_census",
            symbol="run_temporal_census",
            notes="Temporal family plug-in; CLI: lawvm fi-temporal-census [--json].",
        ),
        PeriodicTableCell(
            cell_id="sentence_parse",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.sentence_parse",
            symbol="parse_citation_sentence",
            notes="Citation-bearing sentence construction parse (Pilot A); total-ownership IR.",
        ),
        PeriodicTableCell(
            cell_id="sentence_census",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.sentence_census",
            symbol="run_sentence_census",
            notes="Citation-sentence plug-in; CLI: lawvm fi-sentence-census [--json].",
        ),
        PeriodicTableCell(
            cell_id="definition_parse",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.definition_parse",
            symbol="parse_definition_block",
            notes="Definition-entry construction parse (Pilot B); mirrors defined_terms binder.",
        ),
        PeriodicTableCell(
            cell_id="definition_census",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.definition_census",
            symbol="run_definition_census",
            notes="Definition-entry plug-in; CLI: lawvm fi-definition-census [--json].",
        ),
        PeriodicTableCell(
            cell_id="apply_intent_facade",
            axis="operative",
            cell_status="filled",
            module="lawvm.finland.apply_intent_facade",
            symbol="dispatch_apply_intent",
            notes="Typed CanonicalIntent router + legacy strict-only branch; 17 apply_* lanes in APPLY_INTENT_LANES.",
        ),
        PeriodicTableCell(
            cell_id="timeline_invariants_hook",
            axis="time",
            cell_status="filled",
            module="lawvm.finland.replay_timeline_diagnostics",
            symbol="fi_timeline_invariants_opt_in_enabled",
            notes="Opt-in via LAWVM_FI_ENABLE_TIMELINE_INVARIANTS; diagnostic bench enables by default.",
        ),
        PeriodicTableCell(
            cell_id="merge_invariant_findings",
            axis="phase",
            cell_status="filled",
            module="lawvm.finland.merge",
            symbol="build_merge_invariant_findings",
            notes="Merge invariant violations → typed Finding + merge_invariant_violation.",
        ),
        PeriodicTableCell(
            cell_id="invariant_harvest",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.invariant_harvest",
            symbol="harvest_replay_invariants",
            notes="Shared tree/product invariant + lint harvest for audit_invariants and self-consistency.",
        ),
        PeriodicTableCell(
            cell_id="fi_invariant_audit",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.fi_invariant_audit",
            symbol="audit_one_statute",
            notes="Corpus invariant audit channel built on invariant_harvest + audit_channels.",
        ),
        PeriodicTableCell(
            cell_id="audit_channels",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.audit_channels",
            symbol="run_audit_channel",
            notes="Warnings / invariants / adjudications trilogy with picklable workers.",
        ),
        PeriodicTableCell(
            cell_id="divergence_core",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.divergence_core",
            symbol="diagnose_section_divergence",
            notes="Shared explain + oracle_check replay-vs-oracle comparison core.",
        ),
        PeriodicTableCell(
            cell_id="corpus_io",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.corpus_io",
            symbol="load_statute_ids",
            notes="Shared corpus path resolution for bench and audit scripts.",
        ),
        PeriodicTableCell(
            cell_id="export_persistence",
            axis="instrumentation",
            cell_status="filled",
            module="lawvm.tools.export_persistence",
            symbol="export_projection_tail",
            notes="jsonl/parquet tail for export_fi_* and HE multi-table exports.",
        ),
        PeriodicTableCell(
            cell_id="identity_ledger",
            axis="identity",
            cell_status="filled",
            module="lawvm.core.identity_ledger",
            symbol="IdentityLedger",
            notes="Frozen read-only migration-event snapshot; ReplayProducts.identity_ledger + finland.identity_ledger prefix queries.",
        ),
        PeriodicTableCell(
            cell_id="grammar_census",
            axis="lexical",
            cell_status="filled",
            module="lawvm.finland.legal_surface.grammar_census",
            symbol="run_grammar_census",
            notes="Unified scope/temporal/sentence/definition census; CLI: lawvm fi-grammar-census [--json].",
        ),
        PeriodicTableCell(
            cell_id="chapter_part_inactive_tombstone_mask",
            axis="time",
            cell_status="filled",
            module="lawvm.core.timeline",
            notes="Inactive-descendant tombstone masking for section/chapter/part parents in materialize_pit Step 1b.",
        ),
        PeriodicTableCell(
            cell_id="corrigendum_proof_surfaces",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.corrigendum_proof_projector",
            symbol="finland_corrigendum_review_evidence_surface",
            notes="Corrigendum review/provenance/overview/manual/source evidence adapters.",
        ),
        PeriodicTableCell(
            cell_id="bench_bundle_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.bench_bundle_proof_projector",
            symbol="finland_bench_run_evidence_surface",
            notes="Bench run, evidence-bundle, and frontier proof evidence envelopes.",
        ),
        PeriodicTableCell(
            cell_id="he_branch_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.he_branch_proof_projector",
            symbol="finland_he_branch_evidence_surface",
            notes="Government-proposal branch parse diagnostics; non-enacted authority surface.",
        ),
        PeriodicTableCell(
            cell_id="mutation_boundary_proof_projector",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.mutation_boundary_proof_projector",
            symbol="mutation_boundary_proof_rows",
            notes="Apply mutation-invariant reports → shared mutation-boundary proof rows.",
        ),
        PeriodicTableCell(
            cell_id="proof_surfaces_facade",
            axis="evidence",
            cell_status="filled",
            module="lawvm.finland.proof_surfaces",
            symbol="__all__",
            notes="Backward-compatible re-export facade over named projector modules.",
        ),
    )


def cells_by_axis(axis: PeriodicAxis) -> tuple[PeriodicTableCell, ...]:
    return tuple(cell for cell in finland_periodic_table_cells() if cell.axis == axis)


def cell_by_id(cell_id: str) -> PeriodicTableCell | None:
    for cell in finland_periodic_table_cells():
        if cell.cell_id == cell_id:
            return cell
    return None


def periodic_table_summary() -> dict[str, object]:
    """Machine-readable summary grouped by axis for CLI and audit tooling."""

    cells = finland_periodic_table_cells()
    by_axis: dict[str, list[dict[str, object]]] = {}
    for cell in cells:
        by_axis.setdefault(cell.axis, []).append(
            {
                "cell_id": cell.cell_id,
                "cell_status": cell.cell_status,
                "module": cell.module,
                "symbol": cell.symbol,
                "notes": cell.notes,
            }
        )
    status_counts: dict[str, int] = {}
    for cell in cells:
        status_counts[cell.cell_status] = status_counts.get(cell.cell_status, 0) + 1
    return {
        "catalog_kind": "finland_periodic_table",
        "cell_count": len(cells),
        "status_counts": status_counts,
        "axes": by_axis,
    }


def render_finland_periodic_table_markdown() -> str:
    """Render the periodic table as deterministic Markdown."""

    lines = [
        "# Finland Periodic Table (machine catalog)",
        "",
        "| Axis | Cell | Status | Module | Symbol |",
        "|------|------|--------|--------|--------|",
    ]
    for cell in finland_periodic_table_cells():
        module = cell.module or "—"
        symbol = cell.symbol or "—"
        lines.append(
            f"| {cell.axis} | {cell.cell_id} | {cell.cell_status} | `{module}` | `{symbol}` |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "CellStatus",
    "PeriodicAxis",
    "PeriodicTableCell",
    "cell_by_id",
    "cells_by_axis",
    "finland_periodic_table_cells",
    "periodic_table_summary",
    "render_finland_periodic_table_markdown",
]
