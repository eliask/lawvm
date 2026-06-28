"""Tests for the UK strict-profile preset (sibling of FI's
``tests/test_fi_strict_profile.py`` if it exists; mirrors the
``src/lawvm/finland/strict_profile.py`` → :data:`FINLAND_INGESTION_V1`
preset shape).

Mirrors ``src/lawvm/uk_legislation/strict_profile.py``'s §2.9 GUARD-
LIVENESS note: the preset exists as forward-compatible infrastructure —
nothing consumes it today. These tests pin the field values so a future
apply-path consume-site wire can rely on them being the agreed strict-
default posture.
"""
from __future__ import annotations

from lawvm.core.compile_result import StrictProfile
from lawvm.uk_legislation.strict_profile import (
    UK_INGESTION_V1,
    default_uk_strict_profile,
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
    import dataclasses

    assert dataclasses.is_dataclass(UK_INGESTION_V1)
    assert StrictProfile.__dataclass_params__.frozen is True
