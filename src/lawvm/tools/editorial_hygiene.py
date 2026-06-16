"""Compatibility shim for editorial hygiene / oracle comparison.

All Finland-specific oracle comparison normalization logic now lives in
lawvm.finland.oracle_comparison (to keep jurisdiction-specific presentation
quirks out of shared tools/).

This module re-exports the public names for backward compatibility with
existing callers in tools/, semantic/, etc.
"""

from __future__ import annotations

# Importing the finland module triggers registration of its normalizer and
# detector into the shared registries in semantic/projection.py.
# This way, even code that only knows about "editorial_hygiene" gets the
# jurisdiction behavior without direct "finland" calls in tools.
import lawvm.finland.oracle_comparison  # noqa: F401  (side-effect registration)

from lawvm.finland.oracle_comparison import (
    count_kumottu_bytes,
    is_presentation_structural_diff,
    normalize_finlex_oracle_comparison_text,
    normalize_kumottu_stubs,
    strip_aiempi_sanamuoto_blocks,
    strip_editorial_annotations,
    strip_figure_legend_paragraphs,
    strip_kumottu_attribution,
    strip_temporary_residue_annotations,
)

# Re-export the main ones that were previously defined here.
__all__ = [
    "count_kumottu_bytes",
    "is_presentation_structural_diff",
    "normalize_finlex_oracle_comparison_text",
    "normalize_kumottu_stubs",
    "strip_aiempi_sanamuoto_blocks",
    "strip_editorial_annotations",
    "strip_figure_legend_paragraphs",
    "strip_kumottu_attribution",
    "strip_temporary_residue_annotations",
]
