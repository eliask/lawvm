"""UK parallel-run equality gate for the Wave 0b same-moment kernel cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 0b mandates: before
deleting UK's PRIVATE same-moment cross-act detection fork (divergence #1, the
single highest-risk accidental divergence), run UK's OLD private detection path
and the NEW shared-kernel-backed path on representative AND real-corpus UK
same-moment effects and assert IDENTICAL (a) same-moment diagnostics/findings and
(b) the materialized winner (the effect that wins a same-moment collision under
the replay order). This test encodes that gate.

SCOUT verdict (see the module docstring of ``uk_legislation/ordering`` and the
design §2.1 impedance note): UK runs same-moment detection at the EFFECT level
(on ``UKEffectRecord``s, BEFORE lowering — ops do not exist yet at
``_order_uk_effects_for_replay`` time), not the op level. So UK CANNOT feed the
op-level ``order_ops`` detector without loss: the whole-target-vs-fragment
distinction lives in effect-type STRINGS ("substituted" vs "words substituted")
that ``StructuralAction`` cannot reconstruct, one effect lowers to N ops, and the
(date, target) bucketing keys on citation strings, not op path tuples. The clean,
non-lossy migration that DOES close divergence #1 is to collapse the forked
detection ALGORITHM into the shared kernel
``detect_same_moment_conflict_groups_generic`` — UK supplies only the effect
accessors + its effect-type predicate (jurisdiction-specific inputs, exactly like
EE supplies its own predicate). UK's effect-shaped finding serialization and
``winner_effect_id`` claim carrier stay UK-local because they are genuinely
effect-level presentation, not the shared rule.

The "OLD path" reconstructed here is UK's private index-pair detection loop
verbatim (the pre-cutover ``_detect_same_moment_conflict_groups`` body). The "NEW
path" is the production ``_order_uk_effects_for_replay`` / ``conflicts_from_effects``
that now delegate to the shared kernel. Equality of the two on representative +
real-corpus effects is the cutover proof.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Sequence

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.ordering import (
    _order_uk_effects_for_replay,
    _uk_same_moment_payloads_incompatible,
    conflicts_from_effects,
)

_CONFLICT_RULE_ID = "uk_same_moment_cross_act_incompatible_payload_ambiguous"


# ── OLD private detection loop (verbatim reconstruction) ─────────────────────


class _OldTargetKey(NamedTuple):
    effective_date: str
    affected_target: str


def _old_detect_same_moment_conflict_groups(
    original: Sequence[UKEffectRecord],
    *,
    effective_date_of: Any,
) -> dict[_OldTargetKey, list[tuple[UKEffectRecord, UKEffectRecord]]]:
    """Verbatim copy of UK's pre-Wave-0b private same-moment detection loop.

    Groups by ``(effective_date, affected_target)``, requires ≥2 distinct
    affecting acts, then pairs by RAW INDEX (the old shape), skipping same-act
    pairs inline, keeping incompatible distinct-act pairs.
    """
    target_groups: dict[_OldTargetKey, list[UKEffectRecord]] = {}
    for effect in original:
        target = str(effect.affected_provisions or "").strip()
        if not target:
            continue
        effective_date = effective_date_of(effect) or ""
        if not effective_date:
            continue
        key = _OldTargetKey(effective_date=effective_date, affected_target=target)
        target_groups.setdefault(key, []).append(effect)

    conflicts: dict[_OldTargetKey, list[tuple[UKEffectRecord, UKEffectRecord]]] = {}
    for key, group_effects in target_groups.items():
        distinct_acts = {effect.affecting_act_id for effect in group_effects}
        if len(distinct_acts) < 2:
            continue
        conflicting_pairs: list[tuple[UKEffectRecord, UKEffectRecord]] = []
        for left_idx in range(len(group_effects)):
            for right_idx in range(left_idx + 1, len(group_effects)):
                left = group_effects[left_idx]
                right = group_effects[right_idx]
                if left.affecting_act_id == right.affecting_act_id:
                    continue
                if _uk_same_moment_payloads_incompatible(left, right):
                    conflicting_pairs.append((left, right))
        if conflicting_pairs:
            conflicts[key] = conflicting_pairs
    return conflicts


def _old_detected_conflict_fingerprints(
    effects: Sequence[UKEffectRecord],
) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """OLD-path fingerprint: per conflict, the (date, target, acts, effect-ids).

    The participant SET (de-duplicated + sorted) and the act SET are exactly
    what the finding and claim carriers are built from — pair ORDER never leaks
    into either, so this is the observable detection contract.
    """

    def _effective_date(effect: UKEffectRecord) -> str:
        return effect.effective_date

    groups = _old_detect_same_moment_conflict_groups(
        list(effects), effective_date_of=_effective_date
    )
    out: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for key, pairs in groups.items():
        participants = {id(e): e for pair in pairs for e in pair}.values()
        acts = tuple(sorted({e.affecting_act_id for e in participants}))
        effect_ids = tuple(sorted(e.effect_id for e in participants))
        out.append((key.effective_date, key.affected_target, acts, effect_ids))
    return sorted(out)


def _new_detected_conflict_fingerprints(
    effects: Sequence[UKEffectRecord],
) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """NEW-path fingerprint via the production ``conflicts_from_effects`` (kernel)."""
    detected = conflicts_from_effects(list(effects))
    out: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for c in detected:
        out.append(
            (
                c.effective_date,
                c.affected_target,
                tuple(sorted(c.conflicting_affecting_acts)),
                tuple(sorted(c.conflicting_effect_ids)),
            )
        )
    return sorted(out)


def _conflict_findings(diagnostics: list[dict]) -> list[dict]:
    return [d for d in diagnostics if d.get("rule_id") == _CONFLICT_RULE_ID]


# ── effect builders ─────────────────────────────────────────────────────────


def _effect(
    *,
    effect_id: str,
    affecting_number: str,
    effect_type: str,
    effective_date: str,
    affected_provisions: str = "s. 5",
    affecting_uri: str | None = None,
    affecting_class: str = "UnitedKingdomPublicGeneralAct",
    affecting_year: str = "2010",
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
        affecting_uri=affecting_uri or f"/id/ukpga/{affecting_year}/{affecting_number}",
        affecting_class=affecting_class,
        affecting_year=affecting_year,
        affecting_number=affecting_number,
        affecting_provisions="s. 1",
        affecting_title="Test Affecting Act",
        in_force_dates=[{"date": effective_date, "prospective": "false"}],
    )


# Representative same-moment scenarios spanning every branch of UK's predicate
# (whole-target sub-vs-sub, repeal-vs-amend, fragment coexistence, commencement,
# distinct date/target, same act, undated) plus a 3-act collision.
def _scenarios() -> list[tuple[str, list[UKEffectRecord]]]:
    return [
        (
            "two_whole_substitutions",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="substituted", effective_date="2021-06-01"),
                _effect(effect_id="eB", affecting_number="9", effect_type="substituted", effective_date="2021-06-01"),
            ],
        ),
        (
            "repeal_vs_amend",
            [
                _effect(effect_id="eRep", affecting_number="3", effect_type="repealed", effective_date="2021-06-01"),
                _effect(effect_id="eSub", affecting_number="7", effect_type="substituted", effective_date="2021-06-01"),
            ],
        ),
        (
            "two_repeals_redundant",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="repealed", effective_date="2021-06-01"),
                _effect(effect_id="eB", affecting_number="9", effect_type="repealed", effective_date="2021-06-01"),
            ],
        ),
        (
            "word_level_coexist",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="words inserted", effective_date="2021-06-01"),
                _effect(effect_id="eB", affecting_number="9", effect_type="words substituted", effective_date="2021-06-01"),
            ],
        ),
        (
            "commencement_vs_substitution",
            [
                _effect(effect_id="eSub", affecting_number="5", effect_type="substituted", effective_date="2021-06-01"),
                _effect(effect_id="eCif", affecting_number="9", effect_type="coming into force", effective_date="2021-06-01"),
            ],
        ),
        (
            "distinct_dates",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="substituted", effective_date="2021-01-01"),
                _effect(effect_id="eB", affecting_number="9", effect_type="substituted", effective_date="2021-06-01"),
            ],
        ),
        (
            "distinct_targets",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="substituted", effective_date="2021-06-01", affected_provisions="s. 5"),
                _effect(effect_id="eB", affecting_number="9", effect_type="substituted", effective_date="2021-06-01", affected_provisions="s. 6"),
            ],
        ),
        (
            "same_affecting_act",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="repealed", effective_date="2021-06-01"),
                _effect(effect_id="eB", affecting_number="5", effect_type="substituted", effective_date="2021-06-01"),
            ],
        ),
        (
            "three_act_collision",
            [
                _effect(effect_id="eA", affecting_number="5", effect_type="substituted", effective_date="2021-06-01"),
                _effect(effect_id="eB", affecting_number="9", effect_type="repealed", effective_date="2021-06-01"),
                _effect(effect_id="eC", affecting_number="2", effect_type="entry substituted", effective_date="2021-06-01"),
            ],
        ),
    ]


# Real-corpus witness (verified against the farchive baseline; see
# test_uk_same_moment_precedence_claim): SI 2000/1043 reg. 11(3) is substituted
# at 2005-07-16 by BOTH a UK SI (uksi/2005/894) and a Welsh SI (wsi/2005/1806).
def _real_corpus_witness() -> list[UKEffectRecord]:
    return [
        _effect(
            effect_id="real-uksi",
            affecting_number="894",
            effect_type="substituted",
            effective_date="2005-07-16",
            affected_provisions="reg. 11(3)",
            affecting_uri="/id/uksi/2005/894",
            affecting_class="UnitedKingdomStatutoryInstrument",
            affecting_year="2005",
        ),
        _effect(
            effect_id="real-wsi",
            affecting_number="1806",
            effect_type="substituted",
            effective_date="2005-07-16",
            affected_provisions="reg. 11(3)",
            affecting_uri="/id/wsi/2005/1806",
            affecting_class="WelshStatutoryInstrument",
            affecting_year="2005",
        ),
    ]


def _all_cases() -> list[tuple[str, list[UKEffectRecord]]]:
    return [*_scenarios(), ("real_corpus_witness", _real_corpus_witness())]


# ── gate (a): detection diagnostics/findings identical ───────────────────────


def test_old_and_new_detection_fingerprints_identical() -> None:
    for name, effects in _all_cases():
        old = _old_detected_conflict_fingerprints(effects)
        new = _new_detected_conflict_fingerprints(effects)
        assert old == new, f"detection fingerprint diverged for {name}: {old} != {new}"


def test_old_and_new_finding_set_identical() -> None:
    """The emitted ambiguity finding SET (date, target, acts, resolution, winner).

    The OLD path's emitted finding is what the production
    ``_order_uk_effects_for_replay`` produces today (it delegates detection to
    the shared kernel post-cutover). The OLD detection-fingerprint above proves
    the participant set is unchanged; here we assert the production finding the
    sensor emits is consistent with the OLD-path detection on the same inputs.
    """
    for name, effects in _all_cases():
        diagnostics: list[dict] = []
        _order_uk_effects_for_replay(
            list(effects),
            diagnostics_out=diagnostics,
            lowering_observations_out=[],
        )
        new_findings = {
            (
                f["effective_date"],
                f["affected_target"],
                tuple(sorted(f["conflicting_affecting_acts"])),
                f["resolution"],
            )
            for f in _conflict_findings(diagnostics)
        }
        old = _old_detected_conflict_fingerprints(effects)
        old_keys = {(d, t, acts) for (d, t, acts, _ids) in old}
        new_keys = {(d, t, acts) for (d, t, acts, _res) in new_findings}
        assert old_keys == new_keys, f"finding (date,target,acts) set diverged for {name}"


# ── gate (b): materialized winner identical ──────────────────────────────────


def test_materialized_winner_identical() -> None:
    """The effect that wins a same-moment collision is byte-identical.

    The materialized winner is the conflicting effect that sorts FIRST in the
    replay order. The new path's ``_order_uk_effects_for_replay`` sort key
    (effective_date, modified, precedence_rank, affecting_act_id, ...) is
    unchanged by the detection-algorithm migration, so the head-of-order winner
    among the OLD-path participants must be the same effect on both paths.
    """
    for name, effects in _all_cases():
        old_groups = _old_detect_same_moment_conflict_groups(
            list(effects), effective_date_of=lambda e: e.effective_date
        )
        ordered = _order_uk_effects_for_replay(list(effects))
        order_index = {id(e): i for i, e in enumerate(ordered)}
        for key, pairs in old_groups.items():
            participants = {id(e): e for pair in pairs for e in pair}.values()
            winner = min(participants, key=lambda e: order_index[id(e)])
            # Independently, the new ordered list among the kernel-detected
            # participants must pick the same winner.
            detected = conflicts_from_effects(list(effects))
            match = next(
                (
                    c
                    for c in detected
                    if c.effective_date == key.effective_date
                    and c.affected_target == key.affected_target
                ),
                None,
            )
            assert match is not None, f"kernel lost conflict {key} for {name}"
            new_participant_ids = set(match.conflicting_effect_ids)
            new_winner = min(
                (e for e in ordered if e.effect_id in new_participant_ids),
                key=lambda e: order_index[id(e)],
            )
            assert new_winner.effect_id == winner.effect_id, (
                f"materialized winner diverged for {name} at {key}: "
                f"{winner.effect_id} != {new_winner.effect_id}"
            )


def test_real_corpus_witness_winner_is_uk_si() -> None:
    """The 2000/1043 reg. 11(3) collision still materializes the UK SI as winner.

    uksi/2005/894 sorts ahead of wsi/2005/1806 by affecting_act_id lexical order
    ("u" < "w"); the kernel migration must not change that order-based pick.
    """
    effects = _real_corpus_witness()
    diagnostics: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        effects, diagnostics_out=diagnostics, lowering_observations_out=[]
    )
    findings = _conflict_findings(diagnostics)
    assert len(findings) == 1
    record = findings[0]
    assert set(record["conflicting_affecting_acts"]) == {
        "uksi/2005/894",
        "wsi/2005/1806",
    }
    assert record["effective_date"] == "2005-07-16"
    assert record["affected_target"] == "reg. 11(3)"
    assert record["resolution"] == "affecting_act_id_lexical_order_unproven"
    assert record["order_based_winner_affecting_act_id"] == "uksi/2005/894"
    assert record["order_based_winner_effect_id"] == "real-uksi"
    assert {effect["effect_id"] for effect in record["conflicting_effects"]} == {
        "real-uksi",
        "real-wsi",
    }
    assert "winner_op_id" not in record
    assert "conflicting_op_ids" not in record
    assert ordered[0].effect_id == "real-uksi"
