"""Tests for the production UK manual-claim store + loader seam.

Covers the four deliverable-4 contracts:

  - the store round-trips (write → read yields the same claim payloads);
  - the loader buckets authored claims by kind onto the ``compile_ops_for_statute``
    opt-in parameters;
  - an empty store ⇒ replay is byte-unchanged (all opt-in kwargs are ``None``);
  - a loaded VALID claim flows through ``compile_ops_for_statute`` and takes
    effect, while an INVALID stored claim is rejected by the validator (recorded,
    never silently applied).

The compile-flow tests monkeypatch the effects/archive surfaces so they are
deterministic and corpus-independent, mirroring
``test_uk_appropriate_place_replay_gate.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.uk_legislation.uk_amendment_replay as replay_mod
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.appropriate_place_claim import (
    APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
    POSITION_ALPHABETICAL_INDEX,
    AppropriatePlaceInsertClaim,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.manual_claim_store import (
    ALL_BUCKETS,
    BUCKET_APPROPRIATE_PLACE,
    BUCKET_SAME_MOMENT,
    buckets_from_rows,
    load_manual_claims_for_statute,
    statute_claim_path,
    uk_manual_claims_enabled,
    write_manual_claims_for_statute,
)
from lawvm.uk_legislation.same_moment_precedence_claim import (
    BASIS_LATER_ENACTMENT,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    SameMomentPrecedenceClaim,
)

_STATUTE = "ukpga/2008/17"
_AP_SNIPPET = "At the appropriate place insert the following entry."
_AP_EFFECT_ID = "e-ap-store"


# ── Round-trip + bucketing (no archive) ──────────────────────────────────────
def _same_moment_dict() -> dict:
    return SameMomentPrecedenceClaim(
        claim_id="sm-1",
        claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        statute_id="ukpga/1988/1",
        effective_date="2010-04-01",
        affected_target="s. 495",
        conflicting_affecting_acts=("ukpga/2010/4", "ukpga/2010/8"),
        winner_affecting_act_id="ukpga/2010/8",
        basis=BASIS_LATER_ENACTMENT,
        basis_note="later chapter wins",
        claimant="test",
    ).to_dict()


def _appropriate_place_dict() -> dict:
    return AppropriatePlaceInsertClaim(
        claim_id="ap-1",
        claim_kind=APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
        statute_id=_STATUTE,
        effect_id=_AP_EFFECT_ID,
        target_list_eid="s31-list",
        entry_label="new-entry",
        entry_text="the inserted entry text",
        source_snippet=_AP_SNIPPET,
        position_kind=POSITION_ALPHABETICAL_INDEX,
        alphabetical_index=0,
        claimant="test",
    ).to_dict()


def test_store_round_trips(tmp_path: Path) -> None:
    claims = [_same_moment_dict(), _appropriate_place_dict()]
    path = write_manual_claims_for_statute(
        "ukpga/1988/1", claims, store_dir=tmp_path
    )
    assert path == statute_claim_path("ukpga/1988/1", store_dir=tmp_path)
    assert path.exists()
    loaded = load_manual_claims_for_statute(
        "ukpga/1988/1", store_dir=tmp_path, enabled=True
    )
    # Round-trip: the two claims survive write→read and re-serialize equal.
    assert loaded.total_claims() == 2
    round_tripped = (
        [c.to_dict() for c in loaded.same_moment_precedence_claims]
        + [c.to_dict() for c in loaded.appropriate_place_claims]
    )
    assert round_tripped == claims


def test_loader_buckets_by_kind(tmp_path: Path) -> None:
    write_manual_claims_for_statute(
        "ukpga/1988/1",
        [_same_moment_dict(), _appropriate_place_dict()],
        store_dir=tmp_path,
    )
    loaded = load_manual_claims_for_statute(
        "ukpga/1988/1", store_dir=tmp_path, enabled=True
    )
    assert len(loaded.same_moment_precedence_claims) == 1
    assert len(loaded.appropriate_place_claims) == 1
    # Every other bucket is empty.
    for bucket in ALL_BUCKETS:
        if bucket in {BUCKET_SAME_MOMENT, BUCKET_APPROPRIATE_PLACE}:
            continue
        assert getattr(loaded, bucket) == []
    # compile_kwargs maps each populated bucket to its opt-in param.
    kwargs = loaded.compile_kwargs()
    assert kwargs[BUCKET_SAME_MOMENT] is loaded.same_moment_precedence_claims
    assert kwargs[BUCKET_APPROPRIATE_PLACE] is loaded.appropriate_place_claims
    assert kwargs["contingent_commencement_claims"] is None


def test_unknown_kind_is_parked_not_dropped() -> None:
    loaded = buckets_from_rows(
        "ukpga/1988/1",
        [{"claim_kind": "not_a_real_kind", "claim_id": "x"}],
    )
    assert loaded.total_claims() == 0
    assert len(loaded.unknown_kind_rows) == 1


def test_empty_store_yields_all_none_kwargs(tmp_path: Path) -> None:
    # No authored file for this statute ⇒ empty load even when enabled.
    loaded = load_manual_claims_for_statute(
        "ukpga/9999/99", store_dir=tmp_path, enabled=True
    )
    assert loaded.is_empty()
    assert loaded.compile_kwargs() == {bucket: None for bucket in ALL_BUCKETS}


def test_loading_is_opt_in(tmp_path: Path, monkeypatch) -> None:
    write_manual_claims_for_statute(
        "ukpga/1988/1", [_same_moment_dict()], store_dir=tmp_path
    )
    # Feature flag off ⇒ no claims loaded even though a file exists.
    monkeypatch.delenv("LAWVM_UK_MANUAL_CLAIMS", raising=False)
    assert uk_manual_claims_enabled() is False
    disabled = load_manual_claims_for_statute(
        "ukpga/1988/1", store_dir=tmp_path
    )
    assert disabled.is_empty()
    # Explicit enable ⇒ loaded.
    enabled = load_manual_claims_for_statute(
        "ukpga/1988/1", store_dir=tmp_path, enabled=True
    )
    assert enabled.total_claims() == 1
    # Flag-driven enable ⇒ loaded.
    monkeypatch.setenv("LAWVM_UK_MANUAL_CLAIMS", "1")
    assert uk_manual_claims_enabled() is True
    flagged = load_manual_claims_for_statute(
        "ukpga/1988/1", store_dir=tmp_path
    )
    assert flagged.total_claims() == 1


# ── Flow through compile_ops_for_statute (monkeypatched effects) ─────────────
def _appropriate_place_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=_AP_EFFECT_ID,
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2018-01-01",
        affected_uri="/id/ukpga/2008/17/section/31",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2008",
        affected_number="17",
        affected_provisions="s. 31(12)",
        affecting_uri="/id/uksi/2018/1040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2018",
        affecting_number="1040",
        affecting_provisions="reg. 2",
        affecting_title="Test Regulations",
        comments=_AP_SNIPPET,
        in_force_dates=[{"date": "2018-10-01", "prospective": "false"}],
    )


@pytest.fixture
def patched_pipeline(monkeypatch) -> replay_mod.UKReplayPipeline:
    monkeypatch.setattr(
        replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *a, **k: [_appropriate_place_effect()],
    )
    monkeypatch.setattr(
        replay_mod,
        "resolve_uk_effective_date_overrides_for_replay",
        lambda *a, **k: {},
    )
    monkeypatch.setattr(
        replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda *a, **k: None,
    )
    return replay_mod.UKReplayPipeline(Path("."))


def _claim_insert_op_ids(ops: list) -> list[str]:
    """Op ids emitted specifically by the appropriate-place claim gate."""
    return [
        op.op_id
        for op in ops
        if op.action is StructuralAction.INSERT
        and "_appropriate_place_" in str(op.op_id or "")
    ]


def test_loaded_valid_claim_flows_through_compile_ops(
    tmp_path: Path, patched_pipeline
) -> None:
    # Author a VALID appropriate-place claim whose source_snippet lives in the
    # effect's comments (the binding surface the validator reads).
    write_manual_claims_for_statute(
        _STATUTE, [_appropriate_place_dict()], store_dir=tmp_path
    )
    loaded = load_manual_claims_for_statute(
        _STATUTE, store_dir=tmp_path, enabled=True
    )
    diags: list[dict] = []
    ops = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        archive=object(),
        effect_diagnostics_out=diags,
        **loaded.compile_kwargs(),
    )
    # The validated claim emits its OWN insert op (claim-gated, tagged with the
    # appropriate-place op-id suffix) through the loader-fed param.
    assert _claim_insert_op_ids(ops), "loaded valid claim should emit a claim insert op"
    validated = [
        d
        for d in diags
        if d.get("rule_id") == "uk_appropriate_place_claim_validated"
    ]
    assert validated, "validator should record a PASS for the loaded claim"


def test_empty_store_compile_is_byte_unchanged(
    tmp_path: Path, patched_pipeline
) -> None:
    # Same statute, NO authored file: the loader yields all-None kwargs and the
    # op stream is identical to a no-claims compile (no insert emitted).
    loaded = load_manual_claims_for_statute(
        _STATUTE, store_dir=tmp_path, enabled=True
    )
    assert loaded.compile_kwargs() == {bucket: None for bucket in ALL_BUCKETS}
    diags: list[dict] = []
    ops_with_loader = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        archive=object(),
        effect_diagnostics_out=diags,
        **loaded.compile_kwargs(),
    )
    ops_baseline = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        archive=object(),
    )
    # Byte-equivalence: the op stream is identical to a no-claims compile, and no
    # claim-gated insert is emitted (the natural lowering ops are unchanged).
    assert [op.op_id for op in ops_with_loader] == [
        op.op_id for op in ops_baseline
    ]
    assert not _claim_insert_op_ids(ops_with_loader)


def test_invalid_stored_claim_rejected_by_validator(
    tmp_path: Path, patched_pipeline
) -> None:
    # An authored claim whose source_snippet is NOT an appropriate-place insert
    # shape: it deserializes and is loaded, but the validator rejects it and the
    # gate never emits an op (the validator, not the loader, is the authority).
    bad = _appropriate_place_dict()
    bad["claim_id"] = "ap-bad"
    bad["source_snippet"] = "this is a free-form instruction, not an insert"
    write_manual_claims_for_statute(_STATUTE, [bad], store_dir=tmp_path)
    loaded = load_manual_claims_for_statute(
        _STATUTE, store_dir=tmp_path, enabled=True
    )
    assert loaded.total_claims() == 1  # loaded (deserialized), not pre-filtered
    diags: list[dict] = []
    ops = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        archive=object(),
        effect_diagnostics_out=diags,
        **loaded.compile_kwargs(),
    )
    # The invalid claim's gate never emits its insert op (validator is the gate).
    assert not _claim_insert_op_ids(ops)
    rejected = [
        d
        for d in diags
        if str(d.get("rule_id", "")).startswith(
            "uk_appropriate_place_claim_rejected"
        )
    ]
    assert rejected, "invalid claim must be recorded as rejected, not applied"
