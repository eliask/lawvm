"""Compatibility re-export surface for semantic structure helpers."""

from __future__ import annotations

from lawvm.semantic.align import (
    align_semantic_children,
    align_semantic_facets,
    align_semantic_trees,
)
from lawvm.semantic.contracts import (
    SEMANTIC_CONTRACT_VERSION,
    build_semantic_support,
    semantic_support_projection,
)
from lawvm.semantic.diff import (
    semantic_diff,
    semantic_diff_events,
    semantic_diff_kind,
    semantic_diff_stats,
    semantic_diff_summary,
)
from lawvm.semantic.model import (
    SEMANTIC_STRUCTURE_KINDS,
    AlignedSemanticNode,
    SemanticDiffEvent,
    SemanticDiffResult,
    SemanticDiffStats,
    SemanticPath,
    SemanticPathPart,
    SemanticStructureFacet,
    SemanticStructureNode,
    canonical_structure_kind,
    display_structure_label,
    is_semantic_facet_kind,
    normalize_semantic_label,
    normalize_visible_semantic_label,
    semantic_facet_children,
    semantic_structural_children,
)
from lawvm.semantic.normalize_structure import normalize_structure_for_viewer
from lawvm.semantic.projection import (
    register_inline_repeal_stub_detector,
    semantic_structure_from_ir,
    semantic_structure_from_oracle,
)

__all__ = (
    "SEMANTIC_CONTRACT_VERSION",
    "SEMANTIC_STRUCTURE_KINDS",
    "AlignedSemanticNode",
    "SemanticDiffEvent",
    "SemanticDiffResult",
    "SemanticDiffStats",
    "SemanticPath",
    "SemanticPathPart",
    "SemanticStructureFacet",
    "SemanticStructureNode",
    "align_semantic_children",
    "align_semantic_facets",
    "align_semantic_trees",
    "build_semantic_support",
    "canonical_structure_kind",
    "display_structure_label",
    "is_semantic_facet_kind",
    "normalize_semantic_label",
    "normalize_structure_for_viewer",
    "normalize_visible_semantic_label",
    "register_inline_repeal_stub_detector",
    "semantic_diff",
    "semantic_diff_events",
    "semantic_diff_kind",
    "semantic_diff_stats",
    "semantic_diff_summary",
    "semantic_facet_children",
    "semantic_structural_children",
    "semantic_structure_from_ir",
    "semantic_structure_from_oracle",
    "semantic_support_projection",
)
