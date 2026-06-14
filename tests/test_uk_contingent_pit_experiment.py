"""EXPERIMENT: realize the UK contingent-commencement claim at a real PIT.

The M1 contingent-commencement claim owns a conditional-temporal repeal — a
repeal whose firing depends on an out-of-band commencement trigger + deadline.
The claim, validator and ``gate_contingent_repeal_at_pit`` exist, but the broad
baseline runs ``pit_date=None`` so the gate never fires. This test pins the
*PIT consumer seam* this experiment wires:

    on-disk authored claim  (data/uk/manual_claims/<statute_id>.json)
        → manual_claim_store.load_manual_claims_for_statute  (flag LAWVM_UK_MANUAL_CLAIMS)
        → LoadedManualClaims.compile_kwargs()
        → UKReplayPipeline.compile_ops_for_statute(pit_date=..., contingent_commencement_claims=...)
        → gate_contingent_repeal_at_pit

and demonstrates the mechanism on one (constructed-but-real-shaped) case:

  - at a PIT BEFORE the deadline the conditional repeal is GATED OUT → no repeal
    op for the bound effect;
  - at a PIT AFTER the deadline, with a ``did_not_commence`` resolution, the
    repeal FIRES → a repeal op is emitted;
  - so the materialized op set DIFFERS across the two PITs exactly as the claim's
    resolution dictates;
  - with the flag OFF (or no authored claim) the store yields empty buckets and
    the PIT compile path is byte-identical (no claim ⇒ PIT path unchanged).

NB (experiment finding): a sweep of the whole archive (all 32 450 enacted XMLs,
both the source-pathology compile loop and a direct tag-stripped text search)
found ZERO effects carrying the conditional-temporal-repeal source shape, so no
genuine corpus statute exercises this gate today. The case here uses a
demonstration statute id (``ukpga/9999/1``) and a monkeypatched effect source so
the seam is exercised end-to-end deterministically; the authored claim itself
lives in the real on-disk store and is loaded by the real loader.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import lawvm.uk_legislation.uk_amendment_replay as replay_mod
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.manual_claim_store import (
    load_manual_claims_for_statute,
    statute_claim_path,
    write_manual_claims_for_statute,
)

_SNIPPET = (
    "Section 12 is repealed at the end of 2026 if it has not been brought "
    "into force before the end of 2026."
)
_STATUTE = "ukpga/9999/1"
_EFFECT_ID = "e-exp-77"
_DEADLINE = "2026-12-31"
_PIT_BEFORE = "2026-06-30"
_PIT_AFTER = "2027-06-30"


def _conditional_repeal_effect() -> UKEffectRecord:
    # A real-shaped "repealed" effect whose source prose carries the
    # conditional-temporal-repeal shape (in ``comments`` so the synthetic fixture
    # binds without an affecting-XML fetch — see _effect_conditional_repeal_source_text).
    return UKEffectRecord(
        effect_id=_EFFECT_ID,
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2026-01-01",
        affected_uri="/id/ukpga/9999/1/section/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="9999",
        affected_number="1",
        affected_provisions="s. 12",
        affecting_uri="/id/ukpga/9999/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="9999",
        affecting_number="1",
        affecting_provisions="s. 1",
        affecting_title="Experiment Act",
        comments=_SNIPPET,
        in_force_dates=[{"date": _DEADLINE, "prospective": "false"}],
    )


@pytest.fixture
def patched_pipeline(monkeypatch) -> replay_mod.UKReplayPipeline:
    """Pipeline with the fixture effect substituted for archive loading.

    Only the *effect source* is monkeypatched (the demonstration statute is not in
    the archive). The claims still flow through the real on-disk store + loader.
    """
    monkeypatch.setattr(
        replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *a, **k: [_conditional_repeal_effect()],
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


def _repeal_op_count(ops) -> int:
    return sum(
        1
        for op in ops
        if op.action == StructuralAction.REPEAL
        and ("section" in str(op.target) and "12" in str(op.target))
    )


def _compile(pipeline, *, pit_date, contingent_claims):
    diagnostics: list[dict] = []
    ops = pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date=pit_date,
        archive=object(),  # archive presence is required; loading is monkeypatched
        effect_diagnostics_out=diagnostics,
        contingent_commencement_claims=contingent_claims,
    )
    return ops, diagnostics


# ── Store round-trip: the authored claim loads from disk via the real loader ──
def test_authored_claim_loads_from_store(monkeypatch):
    monkeypatch.setenv("LAWVM_UK_MANUAL_CLAIMS", "1")
    loaded = load_manual_claims_for_statute(_STATUTE)
    assert not loaded.is_empty(), (
        "the authored data/uk/manual_claims/ukpga__9999__1.json must load when "
        "the flag is on"
    )
    claims = loaded.contingent_commencement_claims
    assert len(claims) == 1
    claim = claims[0]
    assert claim.statute_id == _STATUTE
    assert claim.effect_id == _EFFECT_ID
    assert claim.resolution == "did_not_commence"
    # The flag-off path yields no claims ⇒ replay/score byte-unchanged.
    monkeypatch.delenv("LAWVM_UK_MANUAL_CLAIMS", raising=False)
    assert load_manual_claims_for_statute(_STATUTE).is_empty()


# ── The gate flips across the deadline through the real consumer seam ─────────
def test_gate_withholds_before_deadline_and_fires_after(patched_pipeline, monkeypatch):
    monkeypatch.setenv("LAWVM_UK_MANUAL_CLAIMS", "1")
    loaded = load_manual_claims_for_statute(_STATUTE)
    contingent = loaded.compile_kwargs()["contingent_commencement_claims"]
    assert contingent, "authored contingent claim must be present"

    ops_before, diag_before = _compile(
        patched_pipeline, pit_date=_PIT_BEFORE, contingent_claims=contingent
    )
    ops_after, diag_after = _compile(
        patched_pipeline, pit_date=_PIT_AFTER, contingent_claims=contingent
    )

    # Before the deadline: the conditional repeal is withheld → no repeal op.
    assert _repeal_op_count(ops_before) == 0
    # After the deadline with a did_not_commence resolution: the repeal fires.
    assert _repeal_op_count(ops_after) == 1
    # The materialized op sets DIFFER exactly as the claim dictates.
    assert _repeal_op_count(ops_after) != _repeal_op_count(ops_before)

    # The gate decision is recorded in diagnostics at each PIT.
    before_rules = {
        str(r.get("rule_id") or "")
        for r in diag_before
        if r.get("effect_id") == _EFFECT_ID
    }
    after_rules = {
        str(r.get("rule_id") or "")
        for r in diag_after
        if r.get("effect_id") == _EFFECT_ID
    }
    assert "uk_contingent_commencement_repeal_withheld_pre_deadline" in before_rules
    assert "uk_contingent_commencement_repeal_applied_at_pit" in after_rules


# ── Safety: no claim ⇒ PIT path unchanged across the deadline ─────────────────
def test_no_claim_pit_path_unchanged(patched_pipeline):
    # With no contingent claim threaded, the gate never fires; the effect is just
    # an ordinary repeal filtered by its effective date (the deadline). So the
    # before-PIT (pre effective-date) yields no op and the after-PIT yields the
    # repeal — identical to a plain repeal, NOT driven by the contingent gate.
    ops_before, diag_before = _compile(
        patched_pipeline, pit_date=_PIT_BEFORE, contingent_claims=None
    )
    ops_after, _ = _compile(
        patched_pipeline, pit_date=_PIT_AFTER, contingent_claims=None
    )
    # No contingent-gate diagnostics are emitted when no claim is authored.
    gate_rules = {
        str(r.get("rule_id") or "")
        for r in diag_before
        if str(r.get("rule_id") or "").startswith("uk_contingent_commencement_repeal_")
    }
    assert not gate_rules, "no claim ⇒ contingent gate must never fire"
    # The repeal still appears after its effective date (ordinary PIT filtering),
    # but this is the default behaviour, not the contingent gate.
    assert _repeal_op_count(ops_after) == 1


# ── The flag-off store path is empty-by-default (byte-unchanged) ──────────────
def test_flag_off_store_is_empty(monkeypatch):
    monkeypatch.delenv("LAWVM_UK_MANUAL_CLAIMS", raising=False)
    loaded = load_manual_claims_for_statute(_STATUTE)
    assert loaded.is_empty()
    assert all(v is None for v in loaded.compile_kwargs().values())


# ── The authored file is well-formed and on disk where the loader expects ─────
def test_authored_claim_file_exists_and_parses():
    path = statute_claim_path(_STATUTE)
    assert path.exists(), f"authored claim file missing at {path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["statute_id"] == _STATUTE
    assert any(
        c.get("claim_kind") == "contingent_commencement"
        and c.get("effect_id") == _EFFECT_ID
        for c in payload["claims"]
    )
    # Round-trip writer is stable (sorted keys) so authored claims diff cleanly.
    rewritten = write_manual_claims_for_statute(
        _STATUTE, payload["claims"], store_dir=path.parent
    )
    assert rewritten == path
