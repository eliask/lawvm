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
    RecognizerId,
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
        recognizer_ids=frozenset({RecognizerId.EXTRACTION_FALLBACK_HEURISTIC}),
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
        (RecoverySurface.BODY, RecognizerId.EXTRACTION_FALLBACK_HEURISTIC),
        (RecoverySurface.TITLE, RecognizerId.EXTRACTION_FALLBACK_HEURISTIC),
        (RecoverySurface.SCOPE, RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION),
        (RecoverySurface.PAYLOAD, RecognizerId.NORMALIZE_ITEM_LIKE_TARGET),
    ):
        prov = Recovered(
            surface=surface,
            recognizer_ids=frozenset({recognizer}),
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
            recognizer_ids=frozenset({RecognizerId.EXTRACTION_FALLBACK_HEURISTIC}),
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
    rids = frozenset({RecognizerId.EXTRACTION_FALLBACK_HEURISTIC})
    body = Recovered(surface=RecoverySurface.BODY, recognizer_ids=rids, tier=ConfidenceTier.HEURISTIC)
    scope = Recovered(surface=RecoverySurface.SCOPE, recognizer_ids=rids, tier=ConfidenceTier.HEURISTIC)
    payload = Recovered(surface=RecoverySurface.PAYLOAD, recognizer_ids=rids, tier=ConfidenceTier.HEURISTIC)
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


def test_recognizer_id_values_are_distinct_strings() -> None:
    # Closed namespace: every member is a distinct string-valued recognizer id.
    values = [r.value for r in RecognizerId]
    for v in values:
        assert isinstance(v, str)
    assert len(set(values)) == len(values)


def test_recognizer_id_values_match_existing_literals() -> None:
    # Each member's value is the literal string used at the apply/compile site it
    # will replace in Phase 2, so the rekey is mechanical and round-trippable.
    assert RecognizerId.SEC1_BODY_JOHTO.value == "sec1_body_johto_fallback"
    assert RecognizerId.BODY_ROOT_REPLACE.value == "body_root_replace_fallback"
    assert RecognizerId.UNCOVERED_BODY.value == "uncovered_body_recovery"
    assert RecognizerId.EXTRACTION_FALLBACK_HEURISTIC.value == "extraction_fallback_heuristic"
    assert (
        RecognizerId.JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT.value == "jolloin_moment_renumber_supplement"
    )
    assert (
        RecognizerId.UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK.value
        == "unique_item_label_subsection_fallback"
    )
    assert RecognizerId.NORMALIZE_ITEM_LIKE_TARGET.value == "normalize_item_like_target"
    assert (
        RecognizerId.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE.value
        == "rebase_duplicate_target_shifted_replace"
    )
    assert RecognizerId.REBASE_REPLACED_RENUMBER_SOURCE.value == "rebase_replaced_renumber_source"
    assert (
        RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION.value
        == "chapter_scope_from_unique_live_section"
    )
    assert RecognizerId.JOLLOIN_RENUMBER.value == "fi.jolloin_renumber"
    assert RecognizerId.REPEAL_VTS_VOIMAANTULO.value == "fi.repeal_vts_voimaantulo"


def test_recovered_recognizer_ids_are_composable() -> None:
    # Co-occurring recovery markers all land in the set; membership is the
    # Phase-2 apply-site query shape.
    prov = Recovered(
        surface=RecoverySurface.BODY,
        recognizer_ids=frozenset(
            {RecognizerId.SEC1_BODY_JOHTO, RecognizerId.UNCOVERED_BODY}
        ),
        tier=ConfidenceTier.HEURISTIC,
    )
    assert RecognizerId.SEC1_BODY_JOHTO in prov.recognizer_ids
    assert RecognizerId.UNCOVERED_BODY in prov.recognizer_ids
    assert RecognizerId.BODY_ROOT_REPLACE not in prov.recognizer_ids
    # Equality / hashing is order-independent for the set.
    other = Recovered(
        surface=RecoverySurface.BODY,
        recognizer_ids=frozenset(
            {RecognizerId.UNCOVERED_BODY, RecognizerId.SEC1_BODY_JOHTO}
        ),
        tier=ConfidenceTier.HEURISTIC,
    )
    assert prov == other
    assert hash(prov) == hash(other)


def test_blocks_in_strict_typed_method_matches_disposition() -> None:
    # Typed arbiter method replaces the stringly `strict_disposition == "block"`.
    for rule in RECOVERY_AUTHORIZATION_RULES.values():
        assert rule.blocks_in_strict() == (rule.strict_disposition == "block")
    # A known blocking rule and a known non-blocking rule.
    blocking = recovery_authorization_rule("ELAB.STRICT_REJECTED_OPERATION")
    assert blocking is not None and blocking.blocks_in_strict() is True
    nonblocking = recovery_authorization_rule("APPLY.LEGACY_DISPATCH_FALLBACK")
    assert nonblocking is not None and nonblocking.blocks_in_strict() is False


def test_recognizer_id_namespace_is_exhaustive_over_serialized_tag_bags() -> None:
    """Pin the full closed ``RecognizerId`` namespace (Step A census).

    Every tag string a whole-corpus census (59,574 statutes,
    ``official_consolidation`` replay) observed in the three serialized
    provenance bags — unioned with the static write-site literals that are
    written-then-stripped before serialization — has a typed ``RecognizerId``
    member whose ``.value`` is the exact literal. This guard fails loudly if a
    new tag string is added to a bag without a typed home (silent string growth)
    OR a member is renamed/dropped (serialized-identity drift).
    """
    expected_values = {
        # Boolean recovery flags on AmendmentOp.
        "sec1_body_johto_fallback",
        "body_root_replace_fallback",
        "uncovered_body_recovery",
        # extraction_provenance_tags serialized + write-site set.
        "extraction_fallback_heuristic",
        "extraction_body_root_replace",
        "extraction_enacting_formula_body_replace",
        "extraction_enacting_formula_body_insert",
        "extraction_ceremonial_body_only",
        "extraction_act_wide_body_section_replace",
        "extraction_title_fallback",
        "extraction_preamble_body",
        "jolloin_moment_renumber_supplement",
        "repeal_reenact_normalized",
        "numbered_table_target",
        "item_and_moment_target_supplement",
        "mixed_explicit_target_supplement",
        "sparse_osalta_row_omission_repeal",
        "fi.historical_top_level_kohta_as_subsection",
        # target_guessing_provenance_tags serialized + write-site set.
        "unique_item_label_subsection_fallback",
        "normalize_item_like_target",
        "rebase_duplicate_target_shifted_replace",
        "rebase_replaced_renumber_source",
        "rebase_sparse_stale_predecessor",
        "numbered_table_xml_subsection_offset",
        "follow_same_wave_migration",
        # scope_provenance_tags serialized + closed read/write set.
        "chapter_scope_from_unique_live_section",
        "chapter_scope_carry_forward",
        "chapter_scope_from_explicit_chunk",
        "chapter_scope_from_preamble",
        "chapter_scope_from_same_amendment_stem",
        "grouped_chapter_scope",
        "grouped_part_scope",
        "chapter_seed",
        "mixed_scope_group_merge",
        "identity_renumber_absent_target_to_insert",
        # Branched witness_rule_id values.
        "fi.jolloin_renumber",
        "fi.repeal_vts_voimaantulo",
    }
    actual_values = {m.value for m in RecognizerId}
    assert actual_values == expected_values
    # No enum aliasing collapsed two members onto one value.
    assert len(actual_values) == len(list(RecognizerId))
