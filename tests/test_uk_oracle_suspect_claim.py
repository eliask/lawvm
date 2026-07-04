"""D3 (#211/#219): author-owned oracle_suspect claim (feed-vs-consolidation).

The oracle-defect lane (mirrors the EE / NO ``oracle_suspect`` discipline). An
authored claim types specific oracle-only eIds as a publisher self-contradiction:
the official effects feed marks a provision repealed + Applied with a real
commenced date, replay applied it, yet the same publisher's consolidation
retains it live and unannotated (e.g. ukpga/1949/97 s.80(5)/(6) repealed by
uksi/2005/1082 but retained live in the oracle).

The validator binds the claim to REALITY and never infers: a compiled REPEAL op
must target each eId, the bound feed effect must be an applied repeal, and the
oracle must retain each eId live. Any failing leg rejects the whole claim.
"""
from __future__ import annotations

from lawvm.uk_legislation.oracle_suspect_claim import (
    CLAIM_REJECTED_FEED_NOT_APPLIED_RULE_ID,
    CLAIM_REJECTED_NO_REPEAL_OP_RULE_ID,
    CLAIM_REJECTED_ORACLE_ABSENT_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    ORACLE_SUSPECT_CLAIM_KIND,
    OracleSuspectClaim,
    claim_from_dict,
    validate_oracle_suspect_claim,
    validated_oracle_suspect_eids,
)

_SID = "ukpga/1949/97"
_EFFECT = "key-2f041c004682bd263f12bd03ab983845"


def _claim(
    *,
    claim_id: str = "os-1949-97-s80-5-6",
    claim_kind: str = ORACLE_SUSPECT_CLAIM_KIND,
    statute_id: str = _SID,
    effect_id: str = _EFFECT,
    suspect_eids: tuple[str, ...] = ("section-80-5", "section-80-6"),
    affected_target: str = "s. 80(5)(6)",
) -> OracleSuspectClaim:
    return OracleSuspectClaim(
        claim_id=claim_id,
        claim_kind=claim_kind,
        statute_id=statute_id,
        effect_id=effect_id,
        suspect_eids=suspect_eids,
        affected_target=affected_target,
    )


def _validate(
    claim: OracleSuspectClaim,
    *,
    repeal_targets=("section-80-5", "section-80-6"),
    applied=(_EFFECT,),
    oracle=("section-80-5", "section-80-6", "section-1"),
):
    return validate_oracle_suspect_claim(
        claim,
        statute_id=_SID,
        repeal_op_target_eids=repeal_targets,
        repeal_effect_ids_applied=applied,
        oracle_eids=oracle,
    )


def test_validates_real_feed_repeal_oracle_retained_contradiction() -> None:
    v = _validate(_claim())
    assert v.validated is True
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert set(v.suspect_eids) == {"section-80-5", "section-80-6"}


def test_rejects_when_no_repeal_op_targets_eid() -> None:
    """Replay did not apply the repeal → no contradiction to type."""
    v = _validate(_claim(), repeal_targets=("section-1",))
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_NO_REPEAL_OP_RULE_ID


def test_rejects_when_feed_effect_not_applied() -> None:
    v = _validate(_claim(), applied=("key-other",))
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_FEED_NOT_APPLIED_RULE_ID


def test_rejects_when_oracle_does_not_retain_eid() -> None:
    """No publisher self-contradiction if the oracle already dropped the eId."""
    v = _validate(_claim(), oracle=("section-1",))
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_ORACLE_ABSENT_RULE_ID


def test_rejects_malformed_schema() -> None:
    v = _validate(_claim(suspect_eids=()))
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_rejects_wrong_statute() -> None:
    v = validate_oracle_suspect_claim(
        _claim(statute_id="ukpga/2000/8"),
        statute_id=_SID,
        repeal_op_target_eids=("section-80-5", "section-80-6"),
        repeal_effect_ids_applied=(_EFFECT,),
        oracle_eids=("section-80-5", "section-80-6"),
    )
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_partial_repeal_coverage_rejects_whole_claim() -> None:
    """No partial credit: one eId without a repeal op fails the whole claim."""
    v = _validate(_claim(), repeal_targets=("section-80-5",))
    assert v.validated is False
    assert v.rule_id == CLAIM_REJECTED_NO_REPEAL_OP_RULE_ID


def test_eid_normalization_is_case_and_separator_insensitive() -> None:
    """Claims are authored in compare-eid form; the normalizer only lowercases
    and collapses separators (it does not parse address syntax)."""
    v = _validate(
        _claim(suspect_eids=("Section-80-5", "section_80_6")),
    )
    assert v.validated is True
    assert set(v.suspect_eids) == {"section-80-5", "section-80-6"}


def test_validated_union_and_audit_records() -> None:
    out: list[dict] = []
    eids = validated_oracle_suspect_eids(
        [_claim(), _claim(claim_id="os-2", suspect_eids=("section-1",))],
        statute_id=_SID,
        repeal_op_target_eids=("section-80-5", "section-80-6"),
        repeal_effect_ids_applied=(_EFFECT,),
        oracle_eids=("section-80-5", "section-80-6", "section-1"),
        validations_out=out,
    )
    # first claim validates; second (section-1 has no repeal op) rejects
    assert eids == {"section-80-5", "section-80-6"}
    assert len(out) == 2
    assert [r["validated"] for r in out] == [True, False]


def test_claim_from_dict_round_trip() -> None:
    row = _claim().to_dict()
    claim = claim_from_dict(row)
    assert claim.claim_kind == ORACLE_SUSPECT_CLAIM_KIND
    assert claim.suspect_eids == ("section-80-5", "section-80-6")
    assert claim.effect_id == _EFFECT


def test_no_claim_is_empty_union() -> None:
    assert validated_oracle_suspect_eids(
        [],
        statute_id=_SID,
        repeal_op_target_eids=(),
        repeal_effect_ids_applied=(),
        oracle_eids=(),
    ) == set()
