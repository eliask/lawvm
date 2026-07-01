"""Tests for the European Union (EU) witness-attribution ledger adapter.

EU reconstructs a PIT body by applying amending acts' ops to the base and CHECKS
it against the EUR-Lex SECTOR-0 consolidation (never repaired). Per-article
``ArticleDivergence`` kinds map onto the neutral disposition via the frontend's
own ``_KIND_TO_CLASS`` corpus vocabulary; firings come from the compiled ops'
``EU_FMX4.*`` ``witness_rule_id`` (uncataloged -> loud "·" blind-spot frontier)
plus the cataloged ``eu_*`` adjudication reason codes.

Default-run tests exercise (no Cellar / no network):
  * ``_EU_CLASS_DISPOSITION`` + the loud "unknown" fallback;
  * the grammar witness surface offline (``lower_amending_act`` -> ``EU_FMX4.*``
    ops that render uncataloged) and the per-article compare -> disposition map,
    driven through a *mocked* ``replay_statute`` + ``build_consolidation_oracle``;
  * the ``eu`` branch in ``run_ledger`` dispatch;
  * the offline-skip contract (a Cellar failure yields statute_errors, never raises).

An opt-in Cellar-backed end-to-end (gated by ``LAWVM_SPEC_LEDGER_EU_E2E=1``)
verifies a real CELEX yields a ledger.
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from lawvm.eu import spec_ledger_adapter
from lawvm.eu.spec_ledger_adapter import (
    _EU_CLASS_DISPOSITION,
    _kind_to_class,
    eu_ledger_inputs,
)
from lawvm.tools.spec_ledger import run_ledger

_FIXTURES = Path(__file__).resolve().parent / "eu" / "fixtures"


# --------------------------------------------------------------------------
# Disposition map (against the frontend's own corpus divergence classes)
# --------------------------------------------------------------------------

def test_eu_class_dispositions():
    # replay knows an article the consolidation omits -> our replay surplus.
    assert _EU_CLASS_DISPOSITION["deterministic_gap"] == "lawvm_wrong"
    # consolidation carries an article replay has not reconstructed -> missing source.
    assert _EU_CLASS_DISPOSITION["manual_frontier"] == "missing_source"
    assert _EU_CLASS_DISPOSITION["text_diff"] == "unknown"
    assert _EU_CLASS_DISPOSITION["oracle_suspect"] == "oracle_suspect"


def test_eu_disposition_covers_all_corpus_classes():
    """Anti-drift: every corpus divergence class must map to a disposition."""
    kind_to_class = _kind_to_class()
    classes = set(kind_to_class.values()) | {"oracle_suspect"}
    missing = classes - set(_EU_CLASS_DISPOSITION)
    assert not missing, f"unmapped EU corpus divergence classes: {missing}"


def test_eu_unmapped_class_falls_back_to_unknown_loudly():
    from lawvm.tools.spec_ledger import disposition_for

    assert disposition_for("SOMETHING_NEW", _EU_CLASS_DISPOSITION) == "unknown"


# --------------------------------------------------------------------------
# Grammar witness surface offline: EU_FMX4.* ops render uncataloged ("·")
# --------------------------------------------------------------------------

def test_eu_grammar_witness_rule_ids_are_uncataloged_blind_spots():
    from lawvm.eu.fmx4_amendment_grammar import lower_amending_act

    fmx4 = (_FIXTURES / "amending_act_excerpt.fmx4.xml").read_bytes()
    result = lower_amending_act(fmx4, "32020R0001", base_celex="32001R0044")
    assert result.ops, "fixture should lower to at least one grammar op"
    catalog = spec_ledger_adapter._EU_RULE_SPECS
    fmx4_witnesses = {op.witness_rule_id for op in result.ops if op.witness_rule_id}
    assert any(w.startswith("EU_FMX4.") for w in fmx4_witnesses)
    # The grammar-rule witness ids are the loud uncataloged frontier: NOT in the
    # eu_* believed_spec catalog (which holds the typed diagnostics).
    for w in fmx4_witnesses:
        assert w not in catalog, f"{w} unexpectedly cataloged"


# --------------------------------------------------------------------------
# eu_ledger_inputs shape (mocked replay + consolidation oracle — no network)
# --------------------------------------------------------------------------

class _FakeOp:
    def __init__(self, witness_rule_id):
        self.witness_rule_id = witness_rule_id


class _FakeReplay:
    def __init__(self, ops, adjudications, replayed, error=None):
        self.ops = list(ops)
        self.adjudications = list(adjudications)
        self.replayed = replayed
        self.error = error


class _FakeArticleDiv:
    def __init__(self, label, kind):
        self.article_label = label
        self.kind = kind

    @property
    def agrees(self):
        return self.kind == "agreement"


class _FakeComparison:
    def __init__(self, divergences):
        self.divergences = list(divergences)


def _install_fake_eu_surface(monkeypatch, *, replay, comparison, oracle_raises=False):
    class _FakePipeline:
        def __init__(self, *a, **k):
            pass

        def replay_statute(self, celex, cutoff_date=None, temporal_events=()):
            return replay

    monkeypatch.setattr("lawvm.eu.pipeline.EUReplayPipeline", _FakePipeline)

    def _oracle(*a, **k):
        if oracle_raises:
            raise RuntimeError("consolidation unreachable")
        return comparison

    monkeypatch.setattr(
        "lawvm.eu.eu_consolidation_oracle.build_consolidation_oracle", _oracle
    )
    # keep the fetcher a no-op object (never called under the mock)
    monkeypatch.setattr(spec_ledger_adapter, "_make_consolidation_fetcher", lambda: (lambda _c: b""))


def test_eu_ledger_inputs_firings_and_divergences(monkeypatch):
    replayed = types.SimpleNamespace()  # opaque; comparison is mocked
    replay = _FakeReplay(
        ops=[
            _FakeOp("EU_FMX4.WHOLE_ARTICLE_REPLACE"),
            _FakeOp("EU_FMX4.WHOLE_ARTICLE_REPLACE"),
            _FakeOp(""),  # no witness -> no firing
        ],
        adjudications=[types.SimpleNamespace(kind="eu_replay_target_not_found")],
        replayed=replayed,
    )
    comparison = _FakeComparison(
        [
            _FakeArticleDiv("1", "agreement"),  # corroboration, not a divergence
            _FakeArticleDiv("6", "text_divergence"),
            _FakeArticleDiv("7", "present_in_replay_absent_in_oracle"),
            _FakeArticleDiv("8", "present_in_oracle_absent_in_replay"),
        ]
    )
    _install_fake_eu_surface(monkeypatch, replay=replay, comparison=comparison)

    inputs = list(eu_ledger_inputs(["32001R0044"], "official_consolidation"))
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.sid == "32001R0044"
    assert inp.rule_firings == {
        "EU_FMX4.WHOLE_ARTICLE_REPLACE": 2,
        "eu_replay_target_not_found": 1,
    }
    assert len(inp.divergences) == 3
    by_article = {d.section_key: d for d in inp.divergences}
    assert by_article["article:6"].diagnosis == "text_diff"
    assert by_article["article:6"].disposition == "unknown"
    assert by_article["article:7"].diagnosis == "deterministic_gap"
    assert by_article["article:7"].disposition == "lawvm_wrong"
    assert by_article["article:8"].diagnosis == "manual_frontier"
    assert by_article["article:8"].disposition == "missing_source"


def test_eu_ledger_inputs_records_firings_when_oracle_unreachable(monkeypatch):
    """A consolidation acquisition failure keeps the firing account (no divergences),
    rather than dropping the whole statute — the grammar firings are still evidence."""
    replay = _FakeReplay(
        ops=[_FakeOp("EU_FMX4.WHOLE_ARTICLE_REPEAL")],
        adjudications=[],
        replayed=types.SimpleNamespace(),
    )
    _install_fake_eu_surface(monkeypatch, replay=replay, comparison=None, oracle_raises=True)

    inp = next(iter(eu_ledger_inputs(["32001R0044"], "official_consolidation")))
    assert inp.rule_firings == {"EU_FMX4.WHOLE_ARTICLE_REPEAL": 1}
    assert inp.divergences == []


def test_eu_ledger_inputs_skips_errored_replay(monkeypatch):
    replay = _FakeReplay(ops=[], adjudications=[], replayed=None, error="apply raise")
    _install_fake_eu_surface(monkeypatch, replay=replay, comparison=None)
    assert list(eu_ledger_inputs(["32001R0044"], "official_consolidation")) == []


def test_eu_ledger_inputs_skips_when_replay_raises(monkeypatch):
    class _RaisingPipeline:
        def __init__(self, *a, **k):
            pass

        def replay_statute(self, *a, **k):
            raise RuntimeError("Cellar 5xx")

    monkeypatch.setattr("lawvm.eu.pipeline.EUReplayPipeline", _RaisingPipeline)
    monkeypatch.setattr(spec_ledger_adapter, "_make_consolidation_fetcher", lambda: (lambda _c: b""))
    assert list(eu_ledger_inputs(["32001R0044"], "official_consolidation")) == []


# --------------------------------------------------------------------------
# run_ledger dispatch (offline: never raises)
# --------------------------------------------------------------------------

def test_run_ledger_dispatches_eu_offline_yields_errors():
    """Offline, EU dispatch must return a ledger (statute_errors), never raise."""
    led = run_ledger("eu", ["32001R0044"], "official_consolidation")
    assert led.jurisdiction == "eu"
    # No network -> the single statute is skipped and counted as an error.
    assert led.statutes + led.statute_errors == 1


def test_run_ledger_dispatches_eu_with_fake_inputs():
    import dataclasses

    from lawvm.tools.spec_ledger import (
        DivergenceRow,
        StatuteLedgerInput,
        get_ledger_adapter,
        register_ledger_adapter,
    )

    def fake_inputs(sids, mode):
        yield StatuteLedgerInput(
            sid="32001R0044",
            rule_firings={"EU_FMX4.WHOLE_ARTICLE_REPLACE": 2, "eu_replay_target_not_found": 1},
            divergences=[
                DivergenceRow(
                    "32001R0044", "article:7", "deterministic_gap", "lawvm_wrong", None
                )
            ],
        )

    original = get_ledger_adapter("eu")
    register_ledger_adapter(dataclasses.replace(original, ledger_inputs=fake_inputs))
    try:
        led = run_ledger("eu", ["32001R0044"], "official_consolidation")
    finally:
        register_ledger_adapter(original)
    assert led.jurisdiction == "eu"
    assert led.statutes == 1
    # EU_FMX4.* is uncataloged (loud "·"); the eu_* adjudication is cataloged.
    assert not led.rules["EU_FMX4.WHOLE_ARTICLE_REPLACE"].believed_spec
    assert led.rules["eu_replay_target_not_found"].believed_spec
    # the deterministic_gap divergence with no owner is a Gap-A blind spot
    assert led.unattributed


def test_eu_adapter_catalog_nonempty():
    assert spec_ledger_adapter._EU_RULE_SPECS, "EU catalog should be populated"


# --------------------------------------------------------------------------
# Opt-in Cellar-backed end-to-end (requires network + env flag)
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("LAWVM_SPEC_LEDGER_EU_E2E") != "1",
    reason="set LAWVM_SPEC_LEDGER_EU_E2E=1 to run the Cellar-backed EU e2e",
)
def test_eu_ledger_e2e_one_real_celex():
    led = run_ledger("eu", ["32001R0044"], "official_consolidation")
    assert led.jurisdiction == "eu"
    assert led.statutes + led.statute_errors == 1
