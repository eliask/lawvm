"""Tests for StrictProfile v3 channel fields + _PROFILE_GATES extensions.

test_strict_profile_channel_gating_via_PROFILE_GATES (required by spec)
"""
from __future__ import annotations

import pytest

from lawvm.core.compile_result import StrictProfile, _profile_allows, _PROFILE_GATES


def test_attested_channel_fields_have_defaults():
    p = StrictProfile(name="test")
    assert p.allows_attested_reference_resolution is False
    assert p.allows_attested_surface_extraction is False
    assert p.allows_attested_source_correction is False
    assert p.allows_attested_target_selection is False
    assert p.allows_attested_semantic_compilation is False
    assert p.allows_attested_ambiguity_adjudication is False
    assert p.allows_attested_oracle_adjudication is False
    assert p.allows_unreviewed_llm_attestations is False


def test_attested_channel_fields_accept_true():
    p = StrictProfile(
        name="test",
        allows_attested_reference_resolution=True,
        allows_attested_surface_extraction=True,
        allows_attested_source_correction=True,
        allows_attested_target_selection=True,
        allows_attested_semantic_compilation=True,
        allows_attested_ambiguity_adjudication=True,
        allows_attested_oracle_adjudication=True,
        allows_unreviewed_llm_attestations=True,
    )
    assert p.allows_attested_reference_resolution is True
    assert p.allows_attested_oracle_adjudication is True
    assert p.allows_unreviewed_llm_attestations is True


def test_channel_fields_require_bool():
    with pytest.raises(ValueError, match="must be a bool"):
        StrictProfile(
            name="bad",
            allows_attested_reference_resolution="yes",  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        )


def test_profile_gates_contains_all_attestation_resolution_codes():
    codes = [
        "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION",
        "ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION",
        "ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION",
        "ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION",
        "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION",
        "ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION",
        "APPLY.REF_TARGET_CORRECTED_BY_ATTESTATION",
        "APPLY.METADATA_ATTRIBUTION_CORRECTED_BY_ATTESTATION",
        "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION",
        "PARSE.JOHTOLAUSE_FAILED.RESOLVED_BY_ATTESTATION",
        "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION",
        "LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION",
        "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION",
    ]
    for code in codes:
        assert code in _PROFILE_GATES, f"Missing from _PROFILE_GATES: {code}"


def test_strict_profile_channel_gating_via_PROFILE_GATES_suppressed_when_allowed():
    """Finding code RESOLVED_BY_ATTESTATION is suppressed when channel is enabled."""
    code = "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION"

    p_allows = StrictProfile(name="allows", allows_attested_reference_resolution=True)
    p_blocks = StrictProfile(name="blocks", allows_attested_reference_resolution=False)

    # When profile allows the channel, _profile_allows returns True (code suppressed)
    assert _profile_allows(p_allows, code) is True
    # When profile does not allow, _profile_allows returns False (code emitted)
    assert _profile_allows(p_blocks, code) is False


def test_surface_extraction_gate():
    code = "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION"
    p_allows = StrictProfile(name="a", allows_attested_surface_extraction=True)
    p_blocks = StrictProfile(name="b", allows_attested_surface_extraction=False)
    assert _profile_allows(p_allows, code) is True
    assert _profile_allows(p_blocks, code) is False


def test_target_selection_gate():
    code = "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION"
    p_allows = StrictProfile(name="a", allows_attested_target_selection=True)
    p_blocks = StrictProfile(name="b", allows_attested_target_selection=False)
    assert _profile_allows(p_allows, code) is True
    assert _profile_allows(p_blocks, code) is False


def test_ambiguity_adjudication_gate():
    code = "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION"
    p_allows = StrictProfile(name="a", allows_attested_ambiguity_adjudication=True)
    p_blocks = StrictProfile(name="b", allows_attested_ambiguity_adjudication=False)
    assert _profile_allows(p_allows, code) is True
    assert _profile_allows(p_blocks, code) is False


def test_lineage_adjudication_shares_ambiguity_channel():
    code = "LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION"
    p_allows = StrictProfile(name="a", allows_attested_ambiguity_adjudication=True)
    p_blocks = StrictProfile(name="b", allows_attested_ambiguity_adjudication=False)
    assert _profile_allows(p_allows, code) is True
    assert _profile_allows(p_blocks, code) is False


def test_oracle_adjudication_gate():
    code = "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION"
    p_allows = StrictProfile(name="a", allows_attested_oracle_adjudication=True)
    p_blocks = StrictProfile(name="b", allows_attested_oracle_adjudication=False)
    assert _profile_allows(p_allows, code) is True
    assert _profile_allows(p_blocks, code) is False


def test_existing_profile_fields_unchanged():
    """Verify existing fields still work after v3 extension."""
    p = StrictProfile(
        name="quirks",
        allows_target_guessing=True,
        allows_omission_expansion=True,
        allows_estimated_dates=True,
    )
    assert _profile_allows(p, "PARSE.TARGET_GUESSING") is True
    assert _profile_allows(p, "ELAB.OMISSION_EXPANSION") is True
    assert _profile_allows(p, "TIME.ESTIMATED_EFFECTIVE_DATE") is True


def test_profile_gates_all_reference_valid_attr():
    """All _PROFILE_GATES entries reference attributes that exist on StrictProfile."""
    profile = StrictProfile(name="check")
    for code, (attr, _) in _PROFILE_GATES.items():
        assert hasattr(profile, attr), f"_PROFILE_GATES[{code!r}] references unknown attr {attr!r}"
