"""UK strict-profile presets.

This module owns the UK-specific strict profile presets so shared core can
stay generic. Sibling of :mod:`lawvm.finland.strict_profile`.

API tier
--------
UK-local compatibility/config surface. Import these presets from here
instead of ``lawvm.core.compile_result`` so the shared kernel does not own
jurisdiction-specific defaults (per AGENTS.md §2.3 core/frontend boundary).

V0 SCOPE — HONESTY BOUNDARY
This v0 preset uses ONLY the existing core :class:`StrictProfile` fields.
It does NOT yet carry UK-specific recovery-pattern gates (savings_
qualified_repeal, commencement_feed_replay, crossheading_insert, schedule_
note_target, heading_only_facet, definition_pseudo_target, devolved_extent_
repeal, partial_whole_act_repeal, empty_effect_type_whole_act, etc.) —
those UK-local recovery patterns need a frontend-side companion carrier
that wraps :class:`StrictProfile` + UK fields per AGENTS.md §2.3
jurisdiction-local idiom. Building that companion carrier + wiring the
30+ apply-path recovery-branch consume sites is multi-session (Tier C PR2).

What this v0 lands is the canonically-shaped FI mirror:
- :func:`default_uk_strict_profile` returns the UK preset for callers that
  want a strict-profile-shaped object today (e.g. serializing into the
  certificate bundle's ``policy/strict_profile.json`` manifest per
  :func:`lawvm.tools.certificate_bundle`).
- :data:`UK_INGESTION_V1` is the named constant.

The values mirror FI where there's no reason to differ (UK estimated dates
are common; UK word-level text substitution is standard source-patch
idiom; UK source-correction patches are standard). All other recovery
patterns default to ``False`` (strict-disable posture, per §0
over-retention-safe direction — deny the recovery until explicitly allowed).

§5.1 FORWARD-COMPAT HOOK
The 8 env-gated observation-only probes wired at the UK replay fold-exit
(per memory ``uk_tier_a_complete_reality_check_2026_06_28.md``) are ready
to be UPGRADED when a strict-profile lane lands in UK. They currently emit
non-blocking ``uk_replay_*_observed`` adjudications; the upgrade path is
documented in the no-strict-profile AssumptionRegister entry
(``uk_replay_materialization_totality_silent_drop_observed`` witness) at
:mod:`lawvm.uk_legislation.uk_assumptions`.

§2.9 GUARD-LIVENESS — V0 DRAFTING DISCIPLINE
This preset exists as a typed carrier; nothing consumes it today. The
§2.9 worst failure class for *probes* is "exists but unreachable from
production" — that concern doesn't apply to a data-class carrier.
Instead the analogous discipline here is: when a future wire passes this
preset INTO an apply-site consume site, the consume site MUST fire under
the right `allows_X` gate. A future fire-drill (per the
``tests/test_fi_guard_liveness.py`` §2.9 production-lane pattern) will
prove the gate fires when the preset is injected + the recovery-pattern
trigger condition is set. Until then this preset is forward-compatible
infrastructure (mirrors the D11/D12 forward-compat no-op audit pattern —
the carrier exists so the future wire can drop it in).

§1.12 RE-DERIVATION RISK: NONE — this module defines a constant;
no semantic reach-back into rendered text.
"""
from __future__ import annotations

from lawvm.core.compile_result import StrictProfile


def default_uk_strict_profile() -> StrictProfile:
    """Current UK ingestion-oriented strict profile (v0).

    The shape mirrors :func:`lawvm.finland.strict_profile.
    default_finland_strict_profile` so the same consume-site idiom
    (``if strict_profile is not None and not allows_X: fail the recovery``
    per ``apply_subsection_ops.py`` in FI) can be reproduced when the
    UK apply-path consumes this preset in a future wire.

    Values match FI where there's no reason to differ; strict-default
    otherwise. See the module docstring for the V0 SCOPE — HONESTY BOUNDARY
    regarding UK-specific recovery-pattern fields.
    """
    return StrictProfile(
        name="uk_ingestion_v1",
        requires_explicit_effective_date=False,
        allows_target_guessing=False,
        allows_omission_expansion=False,
        allows_uncovered_body_recovery=False,
        allows_fallback_whole_section_replace=False,
        allows_estimated_dates=True,
        allows_context_dependent_anchor_resolution=False,
        # UK uses text_replace for range expansions and word-level patches
        # (per the standard ``effect_text_fragment_lowering.py`` source
        # patch idiom).
        allows_word_substitution=True,
        allows_source_correction_rules=True,
    )


# Named constant for the default UK strict profile.
# Status: stable named preset for future UK strict evaluation — INVALIDATED
# by no production consume site today per the §2.9 GUARD-LIVENESS note in the
# module docstring.
UK_INGESTION_V1: StrictProfile = StrictProfile(
    name="uk_ingestion_v1",
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


__all__ = [
    "default_uk_strict_profile",
    "UK_INGESTION_V1",
]
