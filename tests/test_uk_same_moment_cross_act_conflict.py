"""§1.7 same-moment cross-act incompatible-payload ambiguity findings.

Two affecting acts that change the same target at the same effective date with
incompatible whole-target payloads are today resolved by ``affecting_act_id``
lexical order in ``_order_uk_effects_for_replay``'s sort key. That silent pick is
a §1.7 "legal conflict resolved by Python accident." These tests assert that the
ambiguity is now made VISIBLE as an additive finding without changing which op
wins by default.

Real corpus witness (verified against the farchive baseline): SI 2000/1043
reg. 11(3) is substituted at 2005-07-16 by BOTH uksi/2005/894 and wsi/2005/1806;
the order-based pick is uksi/2005/894 and was previously unrecorded.
"""

from __future__ import annotations

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.ordering import _order_uk_effects_for_replay

_CONFLICT_RULE_ID = "uk_same_moment_cross_act_incompatible_payload_ambiguous"


def _effect(
    *,
    effect_id: str,
    affecting_number: str,
    effect_type: str,
    effective_date: str,
    affected_provisions: str = "s. 5",
    affecting_class: str = "UnitedKingdomPublicGeneralAct",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions=affected_provisions,
        affecting_uri=f"/id/ukpga/2010/{affecting_number}",
        affecting_class=affecting_class,
        affecting_year="2010",
        affecting_number=affecting_number,
        affecting_provisions="s. 1",
        affecting_title="Test Affecting Act",
        in_force_dates=[{"date": effective_date, "prospective": "false"}],
    )


def _conflict_findings(diagnostics: list[dict]) -> list[dict]:
    return [d for d in diagnostics if d.get("rule_id") == _CONFLICT_RULE_ID]


def test_same_moment_cross_act_incompatible_payload_emits_finding() -> None:
    # Two distinct acts both substitute the whole of s. 5 at the same date.
    left = _effect(
        effect_id="eA",
        affecting_number="5",
        effect_type="substituted",
        effective_date="2021-06-01",
    )
    right = _effect(
        effect_id="eB",
        affecting_number="9",
        effect_type="substituted",
        effective_date="2021-06-01",
    )

    diagnostics: list[dict] = []
    observations: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [left, right],
        diagnostics_out=diagnostics,
        lowering_observations_out=observations,
    )

    findings = _conflict_findings(diagnostics)
    assert len(findings) == 1
    record = findings[0]
    # Both affecting acts are recorded.
    assert set(record["conflicting_affecting_acts"]) == {"ukpga/2010/5", "ukpga/2010/9"}
    # The target and date are recorded.
    assert record["affected_target"] == "s. 5"
    assert record["effective_date"] == "2021-06-01"
    # The pick is flagged as order-based and unproven.
    assert record["resolution"] == "affecting_act_id_lexical_order_unproven"
    assert record["order_based_winner_affecting_act_id"] == "ukpga/2010/5"
    # The finding is mirrored to lowering observations too.
    assert any(o.get("rule_id") == _CONFLICT_RULE_ID for o in observations)

    # Default materialized order is UNCHANGED: still lexical winner first.
    assert [e.effect_id for e in ordered] == ["eA", "eB"]


def test_repeal_versus_amend_same_moment_is_incompatible() -> None:
    repeal = _effect(
        effect_id="eRep",
        affecting_number="3",
        effect_type="repealed",
        effective_date="2021-06-01",
    )
    substitute = _effect(
        effect_id="eSub",
        affecting_number="7",
        effect_type="substituted",
        effective_date="2021-06-01",
    )
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [repeal, substitute],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert len(_conflict_findings(diagnostics)) == 1


def test_finding_is_blocking_for_strict_mode() -> None:
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="repealed",
                effective_date="2021-06-01",
            ),
            _effect(
                effect_id="eB",
                affecting_number="9",
                effect_type="repealed",
                effective_date="2021-06-01",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    record = _conflict_findings(diagnostics)[0]
    # Strict mode can reject: blocking with a blocking strict disposition (§14).
    assert record["blocking"] is True
    assert record["strict_disposition"] == "block"


def test_different_effective_date_does_not_conflict() -> None:
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="substituted",
                effective_date="2021-01-01",
            ),
            _effect(
                effect_id="eB",
                affecting_number="9",
                effect_type="substituted",
                effective_date="2021-06-01",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_different_target_does_not_conflict() -> None:
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="substituted",
                effective_date="2021-06-01",
                affected_provisions="s. 5",
            ),
            _effect(
                effect_id="eB",
                affecting_number="9",
                effect_type="substituted",
                effective_date="2021-06-01",
                affected_provisions="s. 6",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_same_affecting_act_does_not_conflict() -> None:
    # Same affecting act on the same target: not a cross-act conflict (the
    # existing source-provision-order lane owns intra-act ordering).
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="repealed",
                effective_date="2021-06-01",
            ),
            _effect(
                effect_id="eB",
                affecting_number="5",
                effect_type="substituted",
                effective_date="2021-06-01",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_word_level_changes_do_not_conflict() -> None:
    # Fragment-scoped changes from different acts can legitimately coexist.
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="words inserted",
                effective_date="2021-06-01",
            ),
            _effect(
                effect_id="eB",
                affecting_number="9",
                effect_type="words substituted",
                effective_date="2021-06-01",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_commencement_entry_against_substitution_does_not_conflict() -> None:
    # A non-structural "coming into force" entry on the same target changes no
    # text, so it does not compete with a substitution.
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eSub",
                affecting_number="5",
                effect_type="substituted",
                effective_date="2021-06-01",
            ),
            _effect(
                effect_id="eCif",
                affecting_number="9",
                effect_type="coming into force",
                effective_date="2021-06-01",
            ),
        ],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_undated_effects_do_not_manufacture_conflict() -> None:
    # Two undated effects on the same target are not a same-EFFECTIVE-DATE
    # collision; their shared 9999-99-99 sentinel must not create a finding.
    a = _effect(
        effect_id="eA",
        affecting_number="5",
        effect_type="repealed",
        effective_date="",
    )
    a.in_force_dates = []
    b = _effect(
        effect_id="eB",
        affecting_number="9",
        effect_type="substituted",
        effective_date="",
    )
    b.in_force_dates = []
    diagnostics: list[dict] = []
    _order_uk_effects_for_replay(
        [a, b],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    assert _conflict_findings(diagnostics) == []


def test_no_diagnostics_sink_means_no_finding_overhead() -> None:
    # When neither sink is provided, ordering returns early without computing
    # conflict findings (the additive lane is opt-in via the out-params).
    ordered = _order_uk_effects_for_replay(
        [
            _effect(
                effect_id="eA",
                affecting_number="5",
                effect_type="substituted",
                effective_date="2021-06-01",
            ),
            _effect(
                effect_id="eB",
                affecting_number="9",
                effect_type="substituted",
                effective_date="2021-06-01",
            ),
        ],
    )
    assert [e.effect_id for e in ordered] == ["eA", "eB"]
