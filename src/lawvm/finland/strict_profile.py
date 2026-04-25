"""Finland strict-profile presets.

This module owns the Finland-specific strict profile presets so shared core
can stay generic.

API tier
--------
Finland-local compatibility/config surface. Import these presets from here
instead of `lawvm.core.compile_result` so the shared kernel does not own
jurisdiction-specific defaults.
"""

from __future__ import annotations

from lawvm.core.compile_result import StrictProfile


def default_finland_strict_profile() -> StrictProfile:
    """Current Finland ingestion-oriented strict profile."""

    return StrictProfile(
        name="finland_ingestion_v1",
        requires_explicit_effective_date=False,
        allows_target_guessing=False,
        allows_omission_expansion=False,
        allows_uncovered_body_recovery=False,
        allows_fallback_whole_section_replace=False,
        allows_estimated_dates=True,
        allows_context_dependent_anchor_resolution=False,
        # Finland uses text_replace for range expansions and text corrections.
        allows_word_substitution=True,
        allows_source_correction_rules=True,
    )


# Named constant for the default Finland strict profile.
# Status: stable named preset for current Finland strict evaluation.
FINLAND_INGESTION_V1: StrictProfile = StrictProfile(
    name="finland_ingestion_v1",
    requires_explicit_effective_date=False,
    allows_target_guessing=False,
    allows_omission_expansion=False,
    allows_uncovered_body_recovery=False,
    allows_fallback_whole_section_replace=False,
    allows_estimated_dates=True,
    allows_context_dependent_anchor_resolution=False,
    allows_word_substitution=True,
    allows_source_correction_rules=True,
)
