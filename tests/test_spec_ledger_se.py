"""Tests for the Sweden (SE) witness-attribution ledger adapter.

SE replays each amending SFS act against the single-version (latest) SFS
consolidated-text oracle. Each compared row's ``classification`` string is mapped
through the closed residual family table
(:mod:`lawvm.sweden.se_agreement_residuals`) into a witness disposition; the
dominant divergence family is ``temporal_mismatch`` -> ``oracle_suspect`` (the
oracle folds strictly-later amendments, replay coherent).

Default-run tests exercise (no archive / no network):
  * ``_SE_FAMILY_DISPOSITION`` + the loud "unknown" fallback;
  * ``_row_is_divergence`` (frontier/residual = divergence; agrees = corroboration);
  * ``se_ledger_inputs`` output shape against a *mocked* ``check_se_official_replay``;
  * the ``se`` branch in ``run_ledger`` dispatch;
  * anti-drift: the residual family table's families are all mapped, and the
    firing rule id the adapter emits is cataloged.

An opt-in corpus-backed end-to-end (gated by ``LAWVM_SPEC_LEDGER_SE_E2E=1``)
verifies a real SFS act yields a non-empty ledger.
"""
from __future__ import annotations

import os

import pytest

from lawvm.sweden import spec_ledger_adapter
from lawvm.sweden.spec_ledger_adapter import (
    _SE_CLASSIFICATION_RULE_ID,
    _SE_FAMILY_DISPOSITION,
    _row_is_divergence,
    _se_classification_family_table,
    se_ledger_inputs,
)
from lawvm.tools.spec_ledger import run_ledger


# --------------------------------------------------------------------------
# Disposition map + family-table coverage
# --------------------------------------------------------------------------

def test_se_family_dispositions():
    assert _SE_FAMILY_DISPOSITION["temporal_mismatch"] == "oracle_suspect"
    assert _SE_FAMILY_DISPOSITION["oracle_editorial_pathology"] == "oracle_suspect"
    assert _SE_FAMILY_DISPOSITION["replay_bug"] == "lawvm_wrong"
    assert _SE_FAMILY_DISPOSITION["unknown"] == "unknown"


def test_se_unmapped_family_falls_back_to_unknown_loudly():
    from lawvm.tools.spec_ledger import disposition_for

    assert disposition_for("SOMETHING_NEW", _SE_FAMILY_DISPOSITION) == "unknown"


def test_se_all_residual_families_are_mapped():
    """Anti-drift: every family the closed table can emit must have a disposition."""
    table = _se_classification_family_table()
    assert table, "SE classification family table should be available"
    families = {entry[0] for entry in table.values()}
    missing = families - set(_SE_FAMILY_DISPOSITION)
    assert not missing, f"unmapped SE residual families: {missing}"


# --------------------------------------------------------------------------
# _row_is_divergence
# --------------------------------------------------------------------------

def test_row_is_divergence_by_residual_status():
    table = _se_classification_family_table()
    # agrees status -> not a divergence (corroboration)
    assert not _row_is_divergence("exact", True, table)
    assert not _row_is_divergence("editorial_attribution_only", True, table)
    # frontier status -> divergence
    assert _row_is_divergence("official_oracle_version_mismatch", True, table)
    # residual status -> divergence
    assert _row_is_divergence("official_oracle_match_current_surface_drift", False, table)


def test_row_is_divergence_unknown_class_falls_back_to_match_flag():
    table = _se_classification_family_table()
    # class not in the table: an unmatched row is a divergence, a matched one is not
    assert _row_is_divergence("brand_new_class", False, table)
    assert not _row_is_divergence("brand_new_class", True, table)


# --------------------------------------------------------------------------
# se_ledger_inputs shape (mocked replay surface — no archive / no network)
# --------------------------------------------------------------------------

def _install_fake_se_surface(monkeypatch, *, result):
    import lawvm.sweden.fetch as fetch_mod

    monkeypatch.setattr(spec_ledger_adapter, "_se_archive_path", lambda: object())
    monkeypatch.setattr(fetch_mod, "open_se_archive", lambda *a, **k: object())
    monkeypatch.setattr(fetch_mod, "check_se_official_replay", lambda *a, **k: result)


def test_se_ledger_inputs_firings_and_divergences(monkeypatch):
    result = {
        "amending_sfs_id": "1999:280",
        "base_sfs_id": "1997:734",
        "rows": [
            # agrees editorial -> firing only, no divergence
            {"classification": "editorial_attribution_only", "match": True, "section": "1"},
            # temporal mismatch -> divergence, oracle_suspect
            {"classification": "official_oracle_version_mismatch", "match": True, "section": "3"},
            # replay bug -> divergence, lawvm_wrong
            {"classification": "official_oracle_match_current_surface_drift", "match": False, "section": "5"},
        ],
        "adjudications": [
            {"reason_code": "se_replay_target_not_found"},
            {"kind": "se_replay_unsupported_action"},
        ],
    }
    _install_fake_se_surface(monkeypatch, result=result)

    inputs = list(se_ledger_inputs(["1999:280"], "official_consolidation"))
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.sid == "1999:280"
    # classification firing per row (3) + 2 adjudication reason codes
    assert inp.rule_firings[_SE_CLASSIFICATION_RULE_ID] == 3
    assert inp.rule_firings["se_replay_target_not_found"] == 1
    assert inp.rule_firings["se_replay_unsupported_action"] == 1

    assert len(inp.divergences) == 2
    by_section = {d.section_key: d for d in inp.divergences}
    assert by_section["3"].disposition == "oracle_suspect"
    assert by_section["3"].diagnosis == "official_oracle_version_mismatch"
    assert by_section["3"].rule_id == _SE_CLASSIFICATION_RULE_ID
    assert by_section["3"].blame_source == "1997:734"
    assert by_section["5"].disposition == "lawvm_wrong"


def test_se_ledger_inputs_skips_unfeasible_act(monkeypatch):
    import lawvm.sweden.fetch as fetch_mod

    monkeypatch.setattr(spec_ledger_adapter, "_se_archive_path", lambda: object())
    monkeypatch.setattr(fetch_mod, "open_se_archive", lambda *a, **k: object())

    def _raise(*a, **k):
        raise FileNotFoundError("no archived RK current JSON")

    monkeypatch.setattr(fetch_mod, "check_se_official_replay", _raise)
    assert list(se_ledger_inputs(["1999:280"], "official_consolidation")) == []


def test_se_mocked_firings_are_all_cataloged(monkeypatch):
    result = {
        "amending_sfs_id": "1999:280",
        "base_sfs_id": "1997:734",
        "rows": [{"classification": "exact", "match": True, "section": "1"}],
        "adjudications": [{"reason_code": "se_replay_target_not_found"}],
    }
    _install_fake_se_surface(monkeypatch, result=result)
    inp = next(iter(se_ledger_inputs(["1999:280"], "official_consolidation")))
    catalog = spec_ledger_adapter._SE_RULE_SPECS
    for rid in inp.rule_firings:
        assert catalog.get(rid), f"firing rule id {rid!r} has no believed_spec catalog entry"


# --------------------------------------------------------------------------
# run_ledger dispatch
# --------------------------------------------------------------------------

def test_run_ledger_dispatches_se():
    import dataclasses

    from lawvm.tools.spec_ledger import (
        DivergenceRow,
        StatuteLedgerInput,
        get_ledger_adapter,
        register_ledger_adapter,
    )

    def fake_inputs(sids, mode):
        yield StatuteLedgerInput(
            sid="1999:280",
            rule_firings={_SE_CLASSIFICATION_RULE_ID: 4},
            divergences=[
                DivergenceRow(
                    "1999:280", "3", "official_oracle_version_mismatch",
                    "oracle_suspect", _SE_CLASSIFICATION_RULE_ID,
                )
            ],
        )

    original = get_ledger_adapter("se")
    register_ledger_adapter(dataclasses.replace(original, ledger_inputs=fake_inputs))
    try:
        led = run_ledger("se", ["1999:280"], "official_consolidation")
    finally:
        register_ledger_adapter(original)
    assert led.jurisdiction == "se"
    assert led.statutes == 1
    assert led.rules[_SE_CLASSIFICATION_RULE_ID].firings == 4
    # oracle_suspect is NOT a falsifying disposition
    assert led.rules[_SE_CLASSIFICATION_RULE_ID].contradicted == 0
    assert led.rules[_SE_CLASSIFICATION_RULE_ID].believed_spec


def test_se_adapter_catalog_nonempty():
    assert spec_ledger_adapter._SE_RULE_SPECS, "SE catalog should be populated"


# --------------------------------------------------------------------------
# Opt-in corpus-backed end-to-end (requires sweden.farchive + env flag)
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("LAWVM_SPEC_LEDGER_SE_E2E") != "1",
    reason="set LAWVM_SPEC_LEDGER_SE_E2E=1 to run the archive-backed SE e2e",
)
def test_se_ledger_e2e_one_real_act_is_nonempty():
    led = run_ledger("se", ["1999:280"], "official_consolidation")
    assert led.jurisdiction == "se"
    assert led.statutes == 1
    assert sum(e.firings for e in led.rules.values()) > 0
