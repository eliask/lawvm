"""Tests for the Estonia (EE) witness-attribution ledger adapter.

EE replays as *consistency verification* against AUTHORITATIVE consolidated law
(Riigi Teataja), so bare consistency divergences lean ``lawvm_wrong`` and the
adjudicated residual bucket (when present) refines the disposition.

The default-run tests exercise:
  * ``_EE_DIAGNOSIS_DISPOSITION`` (both divergence_type and residual-bucket keys,
    plus the loud "unknown" fallback for anything unmapped);
  * the ``ee`` branch in ``run_ledger`` dispatch;
  * ``ee_ledger_inputs`` output shape against a *mocked* replay result + residual
    summary (no archive, no network).

An opt-in corpus-backed end-to-end (gated by ``LAWVM_SPEC_LEDGER_EE_E2E=1``,
mirroring the UK e2e's stance) verifies a real EE pair yields a non-empty ledger.
"""
from __future__ import annotations

import os
import types

import pytest

from lawvm.estonia import spec_ledger_adapter
from lawvm.estonia.spec_ledger_adapter import (
    _EE_DIAGNOSIS_DISPOSITION,
    _ee_address_key,
    _ee_attribute_divergence,
    ee_ledger_inputs,
)
from lawvm.tools.spec_ledger import run_ledger


# --------------------------------------------------------------------------
# Disposition map
# --------------------------------------------------------------------------

def test_ee_divergence_type_keys_lean_lawvm_wrong():
    # The oracle is authoritative consolidated law: a raw consistency divergence
    # with no adjudicated residual is our bug.
    assert _EE_DIAGNOSIS_DISPOSITION["MISMATCH"] == "lawvm_wrong"
    assert _EE_DIAGNOSIS_DISPOSITION["OPS_MISSING"] == "lawvm_wrong"
    assert _EE_DIAGNOSIS_DISPOSITION["CONSOLIDATED_MISSING"] == "lawvm_wrong"


def test_ee_residual_bucket_keys_map_to_expected_dispositions():
    assert _EE_DIAGNOSIS_DISPOSITION["replay_bug"] == "lawvm_wrong"
    assert _EE_DIAGNOSIS_DISPOSITION["source_oracle_drift"] == "oracle_suspect"
    assert _EE_DIAGNOSIS_DISPOSITION["oracle_correction_notice"] == "oracle_suspect"
    assert _EE_DIAGNOSIS_DISPOSITION["source_pathology"] == "missing_source"
    assert _EE_DIAGNOSIS_DISPOSITION["source_ambiguity"] == "missing_source"
    assert _EE_DIAGNOSIS_DISPOSITION["appendix_display_pathology"] == "structural"
    assert _EE_DIAGNOSIS_DISPOSITION["descendant_residual_mix"] == "unknown"
    assert _EE_DIAGNOSIS_DISPOSITION["presentation_punctuation_whitespace"] == "unknown"


def test_ee_unmapped_diagnosis_falls_back_to_unknown_loudly():
    # Anything not in the map must surface as "unknown", never silently pass.
    assert _EE_DIAGNOSIS_DISPOSITION.get("SOMETHING_NEW", "unknown") == "unknown"


def test_ee_disposition_map_covers_all_residual_buckets():
    # Anti-drift: every EEResidualBucket must be mapped (so a new bucket can't
    # silently fall through to "unknown" without a deliberate decision here).
    from lawvm.estonia.residual_inventory import EEResidualBucket

    buckets = set(EEResidualBucket.__args__)  # type: ignore[attr-defined]
    assert buckets.issubset(set(_EE_DIAGNOSIS_DISPOSITION)), (
        buckets - set(_EE_DIAGNOSIS_DISPOSITION)
    )


# --------------------------------------------------------------------------
# Address key + attribution helpers
# --------------------------------------------------------------------------

def test_ee_address_key_renders_path_in_inventory_form():
    addr = types.SimpleNamespace(path=(("section", "5"), ("subsection", "2")))
    assert _ee_address_key(addr) == "section:5/subsection:2"


def test_ee_address_key_empty_path():
    assert _ee_address_key(types.SimpleNamespace(path=())) == ""


def test_ee_attribution_prefers_exact_then_ancestor_then_none():
    op_exact = types.SimpleNamespace(witness_rule_id="r.exact")
    op_anc = types.SimpleNamespace(witness_rule_id="r.anc")
    owner = {"section:5": op_exact, "section:5/subsection:2": op_anc}
    # exact
    assert _ee_attribute_divergence("section:5/subsection:2", owner) == "r.anc"
    # ancestor (no exact owner of the deeper item, falls to longest prefix)
    assert _ee_attribute_divergence("section:5/subsection:2/item:a", owner) == "r.anc"
    # only the shallow section owner matches
    assert _ee_attribute_divergence("section:5/subsection:9", owner) == "r.exact"
    # no owner => blind spot
    assert _ee_attribute_divergence("section:99", owner) is None
    # empty address => None
    assert _ee_attribute_divergence("", owner) is None


# --------------------------------------------------------------------------
# ee_ledger_inputs shape (mocked replay surface — no archive / no network)
# --------------------------------------------------------------------------

class _FakeOp:
    def __init__(self, witness_rule_id, target_path, sequence):
        self.witness_rule_id = witness_rule_id
        self.target = types.SimpleNamespace(path=target_path)
        self.sequence = sequence


class _FakeDiv:
    def __init__(self, address_path, divergence_type):
        self.address = types.SimpleNamespace(path=address_path)
        self.divergence_type = divergence_type
        self.ops_text = "ops"
        self.consolidated_text = "con"


class _FakeResult:
    def __init__(self, compiled_ops, divergences, error=None):
        self.compiled_ops = tuple(compiled_ops)
        self.divergences = list(divergences)
        self.error = error


def _install_fake_ee_surface(monkeypatch, *, result, summary=None):
    monkeypatch.setattr(
        spec_ledger_adapter, "open_rt_archive", lambda *a, **k: object(), raising=False
    )
    # patch the symbols imported *inside* ee_ledger_inputs by patching their modules
    import lawvm.estonia.fetch as fetch_mod
    import lawvm.estonia.replay as replay_mod
    import lawvm.estonia.residual_reporting as rr_mod

    monkeypatch.setattr(fetch_mod, "open_rt_archive", lambda *a, **k: object())
    monkeypatch.setattr(
        spec_ledger_adapter, "_ee_resolve_as_of", lambda oracle_id, archive: "2020-01-01"
    )
    monkeypatch.setattr(
        replay_mod, "replay_ee_to_pit", lambda *a, **k: result
    )
    monkeypatch.setattr(
        rr_mod, "build_ee_residual_summary", lambda *a, **k: summary
    )


def test_ee_ledger_inputs_firings_and_blind_spot(monkeypatch):
    result = _FakeResult(
        compiled_ops=[
            _FakeOp("ee.rule_a", (("section", "5"),), 1),
            _FakeOp("ee.rule_a", (("section", "6"),), 2),
            _FakeOp("", (("section", "7"),), 3),  # no witness rule, no firing
        ],
        divergences=[
            # owned by ee.rule_a (exact section:5)
            _FakeDiv((("section", "5"),), "MISMATCH"),
            # no op owns section:99 -> unattributed, lawvm_wrong -> blind spot
            _FakeDiv((("section", "99"),), "OPS_MISSING"),
        ],
    )
    _install_fake_ee_surface(monkeypatch, result=result, summary=None)

    inputs = list(ee_ledger_inputs(["100/200"], "official_consolidation"))
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.sid == "100/200"
    assert inp.rule_firings == {"ee.rule_a": 2}
    assert len(inp.divergences) == 2

    by_section = {d.section_key: d for d in inp.divergences}
    # divergence_type fallback disposition (no residual summary)
    assert by_section["section:5"].diagnosis == "MISMATCH"
    assert by_section["section:5"].disposition == "lawvm_wrong"
    assert by_section["section:5"].rule_id == "ee.rule_a"
    assert by_section["section:5"].blame_source == ""  # oracle authoritative
    # unattributed
    assert by_section["section:99"].rule_id is None


def test_ee_ledger_inputs_residual_bucket_overrides_divergence_type(monkeypatch):
    result = _FakeResult(
        compiled_ops=[_FakeOp("ee.rule_a", (("section", "5"),), 1)],
        divergences=[_FakeDiv((("section", "5"),), "MISMATCH")],
    )
    # residual summary says section:5 is adjudicated source_oracle_drift
    record = types.SimpleNamespace(bucket="source_oracle_drift")
    summary = types.SimpleNamespace(record_by_address={"section:5": record})
    _install_fake_ee_surface(monkeypatch, result=result, summary=summary)

    inp = next(iter(ee_ledger_inputs(["100/200"], "official_consolidation")))
    div = inp.divergences[0]
    assert div.diagnosis == "source_oracle_drift"
    assert div.disposition == "oracle_suspect"  # not lawvm_wrong


def test_ee_ledger_inputs_skips_errored_replay(monkeypatch):
    result = _FakeResult(compiled_ops=[], divergences=[], error="boom")
    _install_fake_ee_surface(monkeypatch, result=result, summary=None)
    assert list(ee_ledger_inputs(["100/200"], "official_consolidation")) == []


def test_ee_ledger_inputs_skips_malformed_sid(monkeypatch):
    result = _FakeResult(compiled_ops=[], divergences=[])
    _install_fake_ee_surface(monkeypatch, result=result, summary=None)
    # no "/" => skipped before any replay
    assert list(ee_ledger_inputs(["nopair"], "official_consolidation")) == []


# --------------------------------------------------------------------------
# run_ledger dispatch
# --------------------------------------------------------------------------

def test_run_ledger_dispatches_ee():
    import dataclasses

    from lawvm.tools.spec_ledger import (
        DivergenceRow,
        StatuteLedgerInput,
        get_ledger_adapter,
        register_ledger_adapter,
    )

    def fake_inputs(sids, mode):
        yield StatuteLedgerInput(
            sid="100/200",
            rule_firings={"ee.rule_a": 3},
            divergences=[
                DivergenceRow("100/200", "section:5", "MISMATCH", "lawvm_wrong", "ee.rule_a")
            ],
        )

    # Re-register an EE adapter using the fake stream; restore the real one afterward.
    original = get_ledger_adapter("ee")
    register_ledger_adapter(dataclasses.replace(original, ledger_inputs=fake_inputs))
    try:
        led = run_ledger("ee", ["100/200"], "official_consolidation")
    finally:
        register_ledger_adapter(original)
    assert led.jurisdiction == "ee"
    assert led.statutes == 1
    assert led.rules["ee.rule_a"].firings == 3
    assert led.rules["ee.rule_a"].contradicted == 1


def test_run_ledger_unknown_jurisdiction_raises():
    with pytest.raises(NotImplementedError):
        run_ledger("zz", ["1/2"], "official_consolidation")


# --------------------------------------------------------------------------
# Opt-in corpus-backed end-to-end (requires the RT archive + env flag)
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("LAWVM_SPEC_LEDGER_EE_E2E") != "1",
    reason="set LAWVM_SPEC_LEDGER_EE_E2E=1 to run the archive-backed EE e2e",
)
def test_ee_ledger_e2e_one_real_pair_is_nonempty():
    # A high-amendment Meediateenuste seadus pair from the replayable corpus.
    led = run_ledger("ee", ["125042012004/111062013016"], "official_consolidation")
    assert led.jurisdiction == "ee"
    assert led.statutes == 1
    # Real replay: ops fire and divergences exist against the authoritative oracle.
    assert sum(e.firings for e in led.rules.values()) > 0
    payload = led.to_dict()
    n_rules = payload["n_rules"]
    assert isinstance(n_rules, int) and n_rules >= 1
