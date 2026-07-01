"""Tests for the Norway (NO) witness-attribution ledger adapter.

NO replays as *consistency verification* against the live Lovdata consolidated
text. Raw structural divergences (``MISMATCH`` / ``CONSOLIDATED_MISSING`` /
``OPS_MISSING`` from ``core.timeline_consistency``) map onto the neutral
disposition; firings come from per-op write-receipt rule ids + replay/parse
adjudication kinds.

The default-run tests exercise (no archive / no network):
  * ``_NO_DIAGNOSIS_DISPOSITION`` and the loud "unknown" fallback;
  * the ``_path_covers`` / ``_attribute_divergence`` receipt-attribution helpers;
  * ``no_ledger_inputs`` output shape against a *mocked* verify result;
  * the ``no`` branch in ``run_ledger`` dispatch;
  * anti-drift: every firing rule id the adapter emits from the mocked surface
    is cataloged (the catalog is the keepable asset).

An opt-in corpus-backed end-to-end (gated by ``LAWVM_SPEC_LEDGER_NO_E2E=1``,
mirroring the EE/UK e2e stance) verifies a real base act yields a non-empty ledger.
"""
from __future__ import annotations

import os
import types

import pytest

from lawvm.norway import spec_ledger_adapter
from lawvm.norway.spec_ledger_adapter import (
    _NO_DIAGNOSIS_DISPOSITION,
    _attribute_divergence,
    _path_covers,
    no_ledger_inputs,
)
from lawvm.tools.spec_ledger import run_ledger


# --------------------------------------------------------------------------
# Disposition map
# --------------------------------------------------------------------------

def test_no_divergence_type_dispositions():
    assert _NO_DIAGNOSIS_DISPOSITION["MISMATCH"] == "lawvm_wrong"
    assert _NO_DIAGNOSIS_DISPOSITION["CONSOLIDATED_MISSING"] == "lawvm_wrong"
    # OPS_MISSING (in replay, not oracle) is a present/absent node mismatch: structural.
    assert _NO_DIAGNOSIS_DISPOSITION["OPS_MISSING"] == "structural"


def test_no_unmapped_diagnosis_falls_back_to_unknown_loudly():
    from lawvm.tools.spec_ledger import disposition_for

    assert disposition_for("SOMETHING_NEW", _NO_DIAGNOSIS_DISPOSITION) == "unknown"


# --------------------------------------------------------------------------
# Receipt-attribution helpers
# --------------------------------------------------------------------------

def test_path_covers_prefix_or_equal():
    sec = (("section", "5"),)
    sub = (("section", "5"), ("subsection", "2"))
    assert _path_covers(sec, sub)          # prefix owns descendant
    assert _path_covers(sec, sec)          # equal owns itself
    assert not _path_covers(sub, sec)      # deeper cannot own shallower
    assert not _path_covers((), sec)       # empty owner owns nothing


def test_attribute_divergence_prefers_longest_owner_then_none():
    index = [
        ((("section", "5"),), "rule.shallow"),
        ((("section", "5"), ("subsection", "2")), "rule.deep"),
    ]
    # deepest covering owner wins
    assert _attribute_divergence(
        (("section", "5"), ("subsection", "2"), ("item", "a")), index
    ) == "rule.deep"
    # only the shallow owner covers this address
    assert _attribute_divergence((("section", "5"), ("subsection", "9")), index) == "rule.shallow"
    # no owner => blind spot
    assert _attribute_divergence((("section", "99"),), index) is None


# --------------------------------------------------------------------------
# no_ledger_inputs shape (mocked verify surface — no archive / no network)
# --------------------------------------------------------------------------

class _FakeReceipt:
    def __init__(self, rule_ids, landed_path, created=()):
        self.named_rule_ids = tuple(rule_ids)
        self.migration_rule_ids = ()
        self.recovery_rule_ids = ()
        self.fallback_rule_ids = ()
        self.landed_primary_path = landed_path
        self.bound_target_path = landed_path
        self.created_paths = tuple(created)
        self.renumbered_paths = ()
        self.removed_paths = ()
        self.consumed_paths = ()


class _FakeAdj:
    def __init__(self, kind):
        self.kind = kind


class _FakeDiv:
    def __init__(self, path, divergence_type):
        self.address = types.SimpleNamespace(path=path)
        self.divergence_type = divergence_type


class _FakeReplay:
    def __init__(self, write_receipts, adjudications):
        self.write_receipts = tuple(write_receipts)
        self.adjudications = list(adjudications)


class _FakeVerifyResult:
    def __init__(self, replay, divergences, error=None):
        self.replay = replay
        self.divergences = list(divergences)
        self.error = error


def _install_fake_no_surface(monkeypatch, *, result):
    import lawvm.norway.verify as verify_mod

    monkeypatch.setattr(verify_mod, "verify_no_against_current", lambda sid, *, as_of: result)


def test_no_ledger_inputs_firings_and_attribution(monkeypatch):
    receipts = [
        _FakeReceipt(["no_section_renumber_relabel"], (("section", "5"),)),
    ]
    adjs = [
        _FakeAdj("no_replay_contingent_commencement_skipped"),
        _FakeAdj("no_replay_contingent_commencement_skipped"),
        _FakeAdj(""),  # empty kind: no firing
    ]
    divs = [
        _FakeDiv((("section", "5"), ("subsection", "1")), "MISMATCH"),   # owned by receipt
        _FakeDiv((("section", "99"),), "CONSOLIDATED_MISSING"),          # unattributed
        _FakeDiv((("section", "7"),), "OPS_MISSING"),                    # structural, unattributed
    ]
    result = _FakeVerifyResult(_FakeReplay(receipts, adjs), divs)
    _install_fake_no_surface(monkeypatch, result=result)

    inputs = list(no_ledger_inputs(["no/lov/2008-05-15-35"], "official_consolidation"))
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.sid == "no/lov/2008-05-15-35"
    assert inp.rule_firings == {
        "no_section_renumber_relabel": 1,
        "no_replay_contingent_commencement_skipped": 2,
    }
    assert len(inp.divergences) == 3

    by_section = {d.section_key: d for d in inp.divergences}
    owned = by_section["section:5/subsection:1"]
    assert owned.diagnosis == "MISMATCH"
    assert owned.disposition == "lawvm_wrong"
    assert owned.rule_id == "no_section_renumber_relabel"  # covered by the receipt path
    assert owned.blame_source == ""  # NO oracle authoritative

    unattributed = by_section["section:99"]
    assert unattributed.rule_id is None
    assert unattributed.disposition == "lawvm_wrong"

    structural = by_section["section:7"]
    assert structural.disposition == "structural"
    assert structural.rule_id is None


def test_no_ledger_inputs_skips_errored_verify(monkeypatch):
    result = _FakeVerifyResult(_FakeReplay([], []), [], error="no source available")
    _install_fake_no_surface(monkeypatch, result=result)
    assert list(no_ledger_inputs(["no/lov/x"], "official_consolidation")) == []


def test_no_ledger_inputs_skips_when_replay_none(monkeypatch):
    result = _FakeVerifyResult(None, [])
    _install_fake_no_surface(monkeypatch, result=result)
    assert list(no_ledger_inputs(["no/lov/x"], "official_consolidation")) == []


def test_no_mocked_firings_are_all_cataloged(monkeypatch):
    """Anti-drift: the cataloged NO rule ids the adapter emits carry a spec."""
    receipts = [_FakeReceipt(["no_section_renumber_relabel"], (("section", "5"),))]
    adjs = [_FakeAdj("no_replay_contingent_commencement_skipped")]
    result = _FakeVerifyResult(_FakeReplay(receipts, adjs), [])
    _install_fake_no_surface(monkeypatch, result=result)

    inp = next(iter(no_ledger_inputs(["no/lov/x"], "official_consolidation")))
    catalog = spec_ledger_adapter._NO_RULE_SPECS
    for rid in inp.rule_firings:
        assert catalog.get(rid), f"firing rule id {rid!r} has no believed_spec catalog entry"


# --------------------------------------------------------------------------
# run_ledger dispatch
# --------------------------------------------------------------------------

def test_run_ledger_dispatches_no():
    import dataclasses

    from lawvm.tools.spec_ledger import (
        DivergenceRow,
        StatuteLedgerInput,
        get_ledger_adapter,
        register_ledger_adapter,
    )

    def fake_inputs(sids, mode):
        yield StatuteLedgerInput(
            sid="no/lov/x",
            rule_firings={"no_section_renumber_relabel": 3},
            divergences=[
                DivergenceRow(
                    "no/lov/x", "section:5", "MISMATCH", "lawvm_wrong",
                    "no_section_renumber_relabel",
                )
            ],
        )

    original = get_ledger_adapter("no")
    register_ledger_adapter(dataclasses.replace(original, ledger_inputs=fake_inputs))
    try:
        led = run_ledger("no", ["no/lov/x"], "official_consolidation")
    finally:
        register_ledger_adapter(original)
    assert led.jurisdiction == "no"
    assert led.statutes == 1
    assert led.rules["no_section_renumber_relabel"].firings == 3
    assert led.rules["no_section_renumber_relabel"].contradicted == 1
    # cataloged rule carries prose
    assert led.rules["no_section_renumber_relabel"].believed_spec


def test_no_adapter_catalog_nonempty():
    assert spec_ledger_adapter._NO_RULE_SPECS, "NO catalog should be populated"


# --------------------------------------------------------------------------
# Opt-in corpus-backed end-to-end (requires norway.farchive + env flag)
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("LAWVM_SPEC_LEDGER_NO_E2E") != "1",
    reason="set LAWVM_SPEC_LEDGER_NO_E2E=1 to run the archive-backed NO e2e",
)
def test_no_ledger_e2e_one_real_base_is_nonempty():
    led = run_ledger("no", ["no/lov/2008-05-15-35"], "official_consolidation")
    assert led.jurisdiction == "no"
    assert led.statutes == 1
    assert sum(e.firings for e in led.rules.values()) > 0
