"""UK strict-profile presets + UK-specific recovery-pattern carrier.

This module owns the UK-specific strict profile presets so shared core can
stay generic (per AGENTS.md §2.3 core/frontend boundary). Sibling of
:mod:`lawvm.finland.strict_profile`.

V0 SCOPE — TWO LAYERS
~~~~~~~~~~~~~~~~~~~~~

Layer 1 (already landed): the core :class:`~lawvm.core.compile_result.StrictProfile`
preset ``UK_INGESTION_V1`` + :func:`default_uk_strict_profile` factory.
Fields used: only the existing core fields. Landed at commit ``59b7e468``.

Layer 2 (added here): the UK-local :class:`UkStrictProfile` companion
carrier. Composes ``StrictProfile`` (core carrier) with UK-specific
recovery-pattern gates (e.g. ``allows_uk_savings_qualified_repeal``) that
do NOT map to any FI-defined core field — they're jurisdiction-local
drafting idiom carriers per §2.3. Adding these to core's ``StrictProfile``
would be premature until the shape is proven cross-frontend.

The composition pattern (rather than subclass) is the canonical way to
extend a frozen dataclass with jurisdiction-local fields: ``StrictProfile``
is ``@dataclass(frozen=True)`` so subclassing with new fields doesn't
round-trip cleanly; composition (``UkStrictProfile.core_profile: StrictProfile``
+ ``UkStrictProfile.allows_uk_X: bool``) is the right shape.

`UkStrictProfile` IS-A specialization-of-`StrictProfile` semantically — it
adds UK-local gates — so it deliberately provides ``__iter__``-style
delegation via ``__getattr__`` to the wrapped core profile. Consumers that
only check core fields (e.g. ``if profile.allows_target_guessing``) work
transparently through the delegation.

CONSUME-SITE IDIOM
~~~~~~~~~~~~~~~~~~~

FI's pattern at ``apply_subsection_ops.py:1146``::

    strict_profile: Optional[StrictProfile] = None,
    ...
    if strict_profile is not None and not strict_profile.allows_X:
        # fail the recovery (emit a blocking receipt)

UK's pattern (mirrored here)::

    uk_strict_profile: Optional[UkStrictProfile] = None,
    ...
    if uk_strict_profile is None or not uk_strict_profile.allows_uk_X:
        # block the recovery (preserve the existing default-block)
    else:
        # explicit consent from strict-profile — proceed past the default-block

The savings-qualified-repeal consume site (commit arrived with this file)
applies this pattern at ``effect_compiler.py:157``: the recovery branches
already emit blocking receipts by default (the ``§0 over-retention-safe``
default). With ``uk_strict_profile`` loaded AND ``allows_uk_savings_
qualified_repeal=True``, the block is lifted (explicit consent).

UkStrictProfile IS OPTIONAL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Per the §2.10 over-retention-safe direction, UK's default behavior with
NO strict-profile loaded preserves the current behavior at the consume
sites — recoveries that block stay blocking; recoveries that proceed stay
proceeding. ``UkStrictProfile`` is INJECTED by a session-level config (e.g.
via ``LAWVM_UK_STRICT_PROFILE=uk_ingestion_v1`` env var + a construction
shim at the fold-exit) — not baked-in per-statute or per-op.

§2.9 GUARD-LIVENESS — V0 DRAFTING DISCIPLINE
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Per ``uk_dormant_probe_playbook_2026_06_28.md`` (the cross-frontend helper
extraction pattern): the precedent-shape for ``UkStrictProfile`` consumers
is established by wiring ONE consume site + a §2.9 test that exercises the
strict-not-allowed-blocks path via the production lane. Inverse-of-FI:
here the consume site already blocks by default; strict lifts the block
WHEN the recovery pattern is explicitly allowed. The §2.9 test must pin
both directions (None-default-blocks, strict-not-allowed-blocks, strict-
allowed-proceeds) so silent-degradation is structurally impossible.

§1.12 RE-DERIVATION RISK: NONE — ``UkStrictProfile`` is a constant carrier.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from lawvm.core.compile_result import StrictProfile


def default_uk_strict_profile() -> StrictProfile:
    """Current UK ingestion-oriented strict profile (v0).

    The shape mirrors :func:`lawvm.finland.strict_profile.
    default_finland_strict_profile` so the same consume-site idiom
    (``if strict_profile is not None and not allows_X: fail the recovery``
    per ``apply_subsection_ops.py`` in FI) can be reproduced when the
    UK apply-path consumes this preset in a future wire.

    Values match FI where there's no reason to differ; strict-default
    otherwise. See the module docstring "V0 SCOPE — TWO LAYERS" for the
    companion ``UkStrictProfile`` that adds UK-specific recovery-pattern
    gates.
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


# Named constant for the default UK strict profile (core StrictProfile
# only — without the UK-specific recovery-pattern gates).
# Status: stable named preset for future UK strict evaluation — INVALIDATED
# by no production consume site today per the §2.9 GUARD-LIVENESS note in
# the module docstring.
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


@dataclass(frozen=True, slots=True)
class UkStrictProfile:
    """UK-local strict profile that composes core :class:`StrictProfile`
    with UK-specific recovery-pattern gates.

    Adds ~10 UK-local fields that don't map to any FI core field — they
    are jurisdiction-local drafting-idiom carriers per AGENTS.md §2.3
    (UK-specific recovery patterns such as savings-qualified-repeal,
    commencement-feed-replay, crossheading-insert, schedule-note-target,
    heading-only-facet, definition-pseudo-target, devolved-extent-repeal,
    partial-whole-act-repeal, empty-effect-type-whole-act, definition-
    child-structural-insert). None of these have an FI analogue in
    ``core.compile_result.StrictProfile`` — the shapes have not been
    proven cross-frontend.
    """

    core_profile: StrictProfile
    # UK-specific recovery-pattern gates. Default-False: per the §0 over-
    # retention-safe direction, deny the recovery until explicitly allowed
    # (loads via ``UkStrictProfile(uk_ingestion_v1(), allows_uk_X=True)``
    # for the verified-allowed case).
    allows_uk_savings_qualified_repeal: bool = False
    allows_uk_commencement_replay: bool = False
    allows_uk_crossheading_insert: bool = False
    allows_uk_schedule_note_target: bool = False
    allows_uk_heading_only_facet: bool = False
    allows_uk_definition_pseudo_target: bool = False
    allows_uk_devolved_extent_repeal: bool = False
    allows_uk_partial_whole_act_repeal: bool = False
    allows_uk_empty_effect_type_whole_act: bool = False
    allows_uk_definition_child_structural_insert: bool = False

    def __post_init__(self) -> None:
        if self.core_profile is None:
            raise ValueError(
                "UkStrictProfile.core_profile must be a StrictProfile "
                "(got None); build via UkStrictProfile(uk_ingestion_v1)"
            )
        if not isinstance(self.core_profile, StrictProfile):
            raise TypeError(
                "UkStrictProfile.core_profile must be a "
                f"StrictProfile (got {type(self.core_profile).__name__})"
            )
        # §1.10 fail-loud: bool-fields must be bool, not None-or-truthy.
        for fname in [
            "allows_uk_savings_qualified_repeal",
            "allows_uk_commencement_replay",
            "allows_uk_crossheading_insert",
            "allows_uk_schedule_note_target",
            "allows_uk_heading_only_facet",
            "allows_uk_definition_pseudo_target",
            "allows_uk_devolved_extent_repeal",
            "allows_uk_partial_whole_act_repeal",
            "allows_uk_empty_effect_type_whole_act",
            "allows_uk_definition_child_structural_insert",
        ]:
            v = getattr(self, fname)
            if not isinstance(v, bool):
                raise TypeError(
                    f"UkStrictProfile.{fname} must be bool "
                    f"(got {type(v).__name__})"
                )

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attribute lookups to the wrapped core profile.

        Allows consumers to read core fields (``allows_target_guessing``,
        ``allows_omission_expansion``, ...) directly off the
        ``UkStrictProfile`` instance — no need to traverse ``.core_profile``
        by hand. Attempts to read truly absent attributes raise
        ``AttributeError`` as usual.
        """
        # ``__getattr__`` is only called when ``__getattribute__`` doesn't
        # find the name in ``__slots__``/class dict. Since slots'd
        # dataclasses store all declared fields in ``__slots__``, only
        # UNDECLARED names reach here — forward to the core profile.
        core = object.__getattribute__(self, "core_profile")
        return getattr(core, name)


def default_uk_strict_profile_with_uk_gates() -> UkStrictProfile:
    """Current UK-local strict profile with UK-specific recovery-pattern
    gates, all defaulted per the §0 over-retention-safe direction (False
    for every UK gate).

    This is the v0 default UK strict profile: the core preset values match
    ``UK_INGESTION_V1`` (FI's ``FINLAND_INGESTION_V1`` mirror) PLUS the
    UK-specific gates default to False — explicit ``True`` values must be
    set by a future wire to allow any UK-local recovery pattern.
    """
    return UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        # All UK-specific gates default False per §0 over-retention-safe.
    )


# Env-flag name indicating strict-profile should be loaded at session
# startup. Currently the only recognized value is ``uk_ingestion_v1``;
# future wires may add additional named presets.
_STRICT_PROFILE_ENV_FLAG = "LAWVM_UK_STRICT_PROFILE"


def active_uk_strict_profile() -> UkStrictProfile | None:
    """Return the active UK strict-profile (composed carrier) or None when
    no strict-profile is loaded.

    The CLI / caller sets ``LAWVM_UK_STRICT_PROFILE=uk_ingestion_v1`` to
    opt-in. v0 fetches the default ``default_uk_strict_profile_with_
    uk_gates()`` preset when the env var is set; future wires may parse
    multiple named values.
    """
    flag_value = os.environ.get(_STRICT_PROFILE_ENV_FLAG, "").strip()
    if not flag_value:
        return None
    if flag_value == "uk_ingestion_v1":
        return default_uk_strict_profile_with_uk_gates()
    # Future: other named values would construct different UkStrictProfile
    # shapes. Today, only uk_ingestion_v1 is recognized.
    raise ValueError(
        f"Unknown {_STRICT_PROFILE_ENV_FLAG}={flag_value!r}; the only "
        "recognized value today is 'uk_ingestion_v1'."
    )


__all__ = [
    "UkStrictProfile",
    "default_uk_strict_profile",
    "default_uk_strict_profile_with_uk_gates",
    "active_uk_strict_profile",
    "UK_INGESTION_V1",
]
