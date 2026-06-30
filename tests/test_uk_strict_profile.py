"""Tests for the UK strict-profile presets + :class:`UkStrictProfile`
companion carrier.

Mirrors ``src/lawvm/uk_legislation/strict_profile.py``'s §2.9 GUARD-
LIVENESS note: the preset exists as forward-compatible infrastructure —
the consume sites wired at v0 (savings-qualified-repeal) behave the same
with or without an active strict-profile (because all UK gates default
False). These tests pin the field values + the composition shape so a
future apply-path consume-site wire can rely on them being the agreed
strict-default posture.
"""
from __future__ import annotations

import dataclasses

import pytest

from lawvm.core.compile_result import StrictProfile
from lawvm.uk_legislation.strict_profile import (
    UK_INGESTION_V1,
    UkStrictProfile,
    active_uk_strict_profile,
    default_uk_strict_profile,
    default_uk_strict_profile_with_uk_gates,
)


def test_uk_strict_profile_default_is_uk_ingestion_v1() -> None:
    """The default-strict profile returned by
    :func:`default_uk_strict_profile` matches the named constant
    :data:`UK_INGESTION_V1` — pinning the canonical v0 strict-default
    shape so future consume sites don't drift from the agreed preset."""
    assert default_uk_strict_profile() == UK_INGESTION_V1


def test_uk_strict_profile_is_strict_profile_instance() -> None:
    """Type safety — the preset is a :class:`StrictProfile` carrier
    (per AGENTS.md §1.9 no dynamic-shape leakage across semantic phase
    boundaries)."""
    assert isinstance(UK_INGESTION_V1, StrictProfile)


def test_uk_strict_profile_name_is_uk_scoped() -> None:
    """The preset name MUST be ``uk_ingestion_v1`` (not ``finland_…`` or
    ``default_…``) so cross-jurisdictional certificate-bundle / finding routing
    can group by jurisdiction."""
    assert UK_INGESTION_V1.name == "uk_ingestion_v1"


def test_uk_strict_profile_strict_defaults_match_fi_v1() -> None:
    """Strict-default posture matches FI's FINLAND_INGESTION_V1 field-by-field
    for the cross-jurisdiction-shared recovery patterns — there's no reason
    to differ at v0 per the module docstring. Mirrors
    ``src/lawvm/finland/strict_profile.py:FINLAND_INGESTION_V1``."""
    from lawvm.finland.strict_profile import FINLAND_INGESTION_V1

    fields = (
        "requires_explicit_effective_date",
        "allows_target_guessing",
        "allows_omission_expansion",
        "allows_uncovered_body_recovery",
        "allows_fallback_whole_section_replace",
        "allows_estimated_dates",
        "allows_context_dependent_anchor_resolution",
        "allows_word_substitution",
        "allows_source_correction_rules",
    )
    for field in fields:
        assert getattr(UK_INGESTION_V1, field) == getattr(
            FINLAND_INGESTION_V1, field
        ), f"field {field!r} should match FI's FINLAND_INGESTION_V1 at v0"


def test_uk_strict_profile_is_frozen() -> None:
    """``StrictProfile`` is ``@dataclass(frozen=True)`` — verify via the
    dataclass params metadata (B010-clean: doesn't trip linters with a
    setattr-with-constant attribute, doesn't trip ty's read-only property
    check). Pins immutability so a future mutation-via-direct-assignment
    is structurally impossible."""
    assert dataclasses.is_dataclass(UK_INGESTION_V1)
    assert StrictProfile.__dataclass_params__.frozen is True


# ---- UkStrictProfile (companion carrier) tests below ----


def test_uk_strict_profile_with_uk_gates_wraps_uk_ingestion_v1() -> None:
    """``default_uk_strict_profile_with_uk_gates`` wraps the core
    ``UK_INGESTION_V1`` preset + adds the UK-specific recovery-pattern
    gates (all defaulted False per the §0 over-retention-safe direction)."""
    p = default_uk_strict_profile_with_uk_gates()
    assert isinstance(p, UkStrictProfile)
    assert p.core_profile == UK_INGESTION_V1
    # All UK-specific gates default False (§0 over-retention-safe).
    uk_fields = (
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
    )
    for fname in uk_fields:
        assert getattr(p, fname) is False, (
            f"v0 UK gate {fname!r} must default False per §0 over-retention-"
            "safe direction"
        )


def test_uk_strict_profile_delegates_core_fields() -> None:
    """Reads of core fields (e.g. ``allows_target_guessing``) on a
    ``UkStrictProfile`` instance transparently delegate to the wrapped
    ``core_profile`` — consumers don't need to navigate ``.core_profile``
    by hand."""
    p = default_uk_strict_profile_with_uk_gates()
    assert p.allows_target_guessing is False
    assert p.allows_estimated_dates is True
    assert p.allows_word_substitution is True
    assert p.allows_source_correction_rules is True


def test_uk_strict_profile_rejects_non_strict_profile_core() -> None:
    """fail-loud (§1.10) — passing a non-StrictProfile as ``core_profile``
    raises TypeError so a future wire can't accidentally construct a
    half-typed ``UkStrictProfile``."""
    from typing import cast
    bad = cast(StrictProfile, "not_a_strict_profile")
    with pytest.raises(TypeError, match="must be a StrictProfile"):
        UkStrictProfile(core_profile=bad)


def test_active_uk_strict_profile_returns_none_when_env_unset(monkeypatch) -> None:
    """``LAWVM_UK_STRICT_PROFILE`` env var unset → None (no strict profile
    active). This is the default production posture: all consume sites
    treat None as ``proceed with the existing default-block-or-allow``."""
    monkeypatch.delenv("LAWVM_UK_STRICT_PROFILE", raising=False)
    assert active_uk_strict_profile() is None


def test_active_uk_strict_profile_returns_default_when_env_set(monkeypatch) -> None:
    """``LAWVM_UK_STRICT_PROFILE=uk_ingestion_v1`` →
    :func:`default_uk_strict_profile_with_uk_gates` preset."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    p = active_uk_strict_profile()
    assert p is not None
    assert p == default_uk_strict_profile_with_uk_gates()


def test_active_uk_strict_profile_rejects_unknown_preset_name(monkeypatch) -> None:
    """``LAWVM_UK_STRICT_PROFILE=unknown`` raises ValueError (§1.10 fail-
    loud) — the only recognized v0 preset is ``uk_ingestion_v1``."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "unknown_preset")
    with pytest.raises(ValueError, match="Unknown"):
        active_uk_strict_profile()


def test_uk_strict_profile_companion_is_frozen() -> None:
    """``UkStrictProfile`` is ``@dataclass(frozen=True, slots=True)`` —
    immutability is load-bearing (the carrier is shared across apply-path
    consume sites in a single replay pass)."""
    p = default_uk_strict_profile_with_uk_gates()
    assert dataclasses.is_dataclass(p)
    assert p.__dataclass_params__.frozen is True
    # Slots load-bearing: UK-specific gate field at __slots__ is load-bearing
    # per §1.9 (typed carriers over dynamic shape).
    assert hasattr(UkStrictProfile, "__slots__")
