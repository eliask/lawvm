"""Unit tests for the typed Finland op-provenance / acceptance-mode model.

These lock the Phase-1 contract of ``lawvm.finland.op_provenance`` (the
consolidation target spec'd in ``notes/FI_OP_PROVENANCE_CONSOLIDATION_SPEC.md``)
and the typed ``blocks_in_strict()`` arbiter method on the recovery
authorization registry. Phase 1 does not wire the provenance type into
``AmendmentOp`` (that touches ``ops.py`` and is deferred to Phase 2), so these
tests cover the type's own semantics and the strict-disposition retirement.
"""

from __future__ import annotations

from lawvm.core.compile_result import StrictProfile
from lawvm.finland.op_provenance import (
    AcceptanceMode,
    ConfidenceTier,
    Parsed,
    RecognitionCoverage,
    Recovered,
    RecoverySurface,
    admits,
    mode_for,
)
from lawvm.finland.recovery_authorization_registry import (
    RECOVERY_AUTHORIZATION_RULES,
    recovery_authorization_rule,
)
from lawvm.finland.strict_profile import default_finland_strict_profile


def _body_recovered() -> Recovered:
    return Recovered(
        surface=RecoverySurface.BODY,
        recognizer_id="PARSE.EXTRACTION_FALLBACK",
        tier=ConfidenceTier.HEURISTIC,
    )


def test_admits_strict_rejects_recovered_admits_parsed() -> None:
    parsed = Parsed(grammar_rule_id="fi.body.section_replace")
    recovered = _body_recovered()

    assert admits(AcceptanceMode.STRICT, parsed) is True
    assert admits(AcceptanceMode.STRICT, recovered) is False
    assert admits(AcceptanceMode.QUIRKS, parsed) is True
    assert admits(AcceptanceMode.QUIRKS, recovered) is True


def test_mode_for_none_profile_is_quirks() -> None:
    assert mode_for(None, _body_recovered()) is AcceptanceMode.QUIRKS
    assert mode_for(None, Parsed(grammar_rule_id="x")) is AcceptanceMode.QUIRKS


def test_mode_for_parsed_is_always_quirks_equivalent() -> None:
    # A Parsed op is never recovered, so the strictest profile still admits it.
    parsed = Parsed(grammar_rule_id="fi.body.section_replace")
    assert mode_for(default_finland_strict_profile(), parsed) is AcceptanceMode.QUIRKS


def test_mode_for_default_finland_profile_blocks_recovery_surfaces() -> None:
    # default_finland_strict_profile forbids target guessing, anchor resolution,
    # and omission expansion -> every recovery surface is STRICT.
    profile = default_finland_strict_profile()
    for surface, recognizer in (
        (RecoverySurface.BODY, "PARSE.EXTRACTION_FALLBACK"),
        (RecoverySurface.TITLE, "PARSE.EXTRACTION_FALLBACK"),
        (RecoverySurface.SCOPE, "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"),
        (RecoverySurface.PAYLOAD, "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"),
    ):
        prov = Recovered(
            surface=surface,
            recognizer_id=recognizer,
            tier=ConfidenceTier.HEURISTIC,
        )
        assert mode_for(profile, prov) is AcceptanceMode.STRICT, surface


def test_mode_for_lenient_profile_admits_recovery() -> None:
    lenient = StrictProfile(
        name="lenient_test",
        allows_target_guessing=True,
        allows_context_dependent_anchor_resolution=True,
        allows_omission_expansion=True,
    )
    for surface in (
        RecoverySurface.BODY,
        RecoverySurface.TITLE,
        RecoverySurface.SCOPE,
        RecoverySurface.PAYLOAD,
    ):
        prov = Recovered(
            surface=surface,
            recognizer_id="r",
            tier=ConfidenceTier.HEURISTIC,
        )
        assert mode_for(lenient, prov) is AcceptanceMode.QUIRKS, surface


def test_mode_for_is_keyed_per_surface() -> None:
    # Profile forbids target guessing only: body/title STRICT, scope/payload QUIRKS.
    profile = StrictProfile(
        name="body_only_strict",
        allows_target_guessing=False,
        allows_context_dependent_anchor_resolution=True,
        allows_omission_expansion=True,
    )
    body = Recovered(surface=RecoverySurface.BODY, recognizer_id="r", tier=ConfidenceTier.HEURISTIC)
    scope = Recovered(surface=RecoverySurface.SCOPE, recognizer_id="r", tier=ConfidenceTier.HEURISTIC)
    payload = Recovered(surface=RecoverySurface.PAYLOAD, recognizer_id="r", tier=ConfidenceTier.HEURISTIC)
    assert mode_for(profile, body) is AcceptanceMode.STRICT
    assert mode_for(profile, scope) is AcceptanceMode.QUIRKS
    assert mode_for(profile, payload) is AcceptanceMode.QUIRKS


def test_confidence_tiers_are_discrete_string_enum() -> None:
    # No floats: every tier is a string-valued enum member.
    for tier in ConfidenceTier:
        assert isinstance(tier.value, str)
    # Title is the weakest surface by construction; the tiers are distinct.
    assert len({t.value for t in ConfidenceTier}) == len(list(ConfidenceTier))


def test_recognition_coverage_totality() -> None:
    assert RecognitionCoverage().is_total is True
    assert RecognitionCoverage(recognized_spans=((0, 5),)).is_total is True
    assert RecognitionCoverage(skipped_spans=((5, 9),)).is_total is False


def test_provenance_records_are_frozen_and_hashable() -> None:
    a = _body_recovered()
    b = _body_recovered()
    assert a == b
    assert hash(a) == hash(b)
    assert Parsed("x") == Parsed("x")


def test_blocks_in_strict_typed_method_matches_disposition() -> None:
    # Typed arbiter method replaces the stringly `strict_disposition == "block"`.
    for rule in RECOVERY_AUTHORIZATION_RULES.values():
        assert rule.blocks_in_strict() == (rule.strict_disposition == "block")
    # A known blocking rule and a known non-blocking rule.
    blocking = recovery_authorization_rule("ELAB.STRICT_REJECTED_OPERATION")
    assert blocking is not None and blocking.blocks_in_strict() is True
    nonblocking = recovery_authorization_rule("APPLY.LEGACY_DISPATCH_FALLBACK")
    assert nonblocking is not None and nonblocking.blocks_in_strict() is False
