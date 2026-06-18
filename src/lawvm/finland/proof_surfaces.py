"""Finland proof-surface projections.

Backward-compatible re-export facade. Implementations live in named projector
modules; this module preserves existing import paths for tools and tests.
"""

from __future__ import annotations

from lawvm.finland.bench_bundle_proof_projector import (
    finland_bench_run_evidence_surface,
    finland_evidence_bundle_evidence_surface,
    finland_frontier_proof_evidence_surface,
)
from lawvm.finland.corrigendum_proof_projector import (
    finland_corrigendum_manual_template_evidence_surface,
    finland_corrigendum_manual_template_frontier_item,
    finland_corrigendum_open_manual_evidence_surface,
    finland_corrigendum_overview_evidence_surface,
    finland_corrigendum_provenance_evidence_surface,
    finland_corrigendum_review_evidence_surface,
    finland_corrigendum_sources_evidence_surface,
    finland_corrigendum_unsupported_patch_evidence_surface,
    finland_corrigendum_unsupported_patch_frontier_item,
)
from lawvm.finland.he_branch_proof_projector import finland_he_branch_evidence_surface
from lawvm.finland.mutation_boundary_proof_projector import mutation_boundary_proof_rows

__all__ = [
    "finland_bench_run_evidence_surface",
    "finland_corrigendum_manual_template_evidence_surface",
    "finland_corrigendum_manual_template_frontier_item",
    "finland_corrigendum_open_manual_evidence_surface",
    "finland_corrigendum_overview_evidence_surface",
    "finland_corrigendum_provenance_evidence_surface",
    "finland_corrigendum_review_evidence_surface",
    "finland_corrigendum_sources_evidence_surface",
    "finland_corrigendum_unsupported_patch_evidence_surface",
    "finland_corrigendum_unsupported_patch_frontier_item",
    "finland_evidence_bundle_evidence_surface",
    "finland_frontier_proof_evidence_surface",
    "finland_he_branch_evidence_surface",
    "mutation_boundary_proof_rows",
]
