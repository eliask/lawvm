"""Tests for the jurisdiction-neutral witness-attribution ledger core.

The aggregation/ranking core is exercised with synthetic ``StatuteLedgerInput``s
(no corpus needed). The Finland adapter's diagnosis->disposition map is checked
directly. The corpus-backed FI path is intentionally not exercised here.
"""
from __future__ import annotations

from lawvm.finland.spec_ledger_adapter import _FI_DIAGNOSIS_DISPOSITION
from lawvm.tools.spec_ledger import (
    DivergenceRow,
    StatuteLedgerInput,
    build_ledger,
    render_markdown,
)


def _div(sid, section, diagnosis, disposition, rule_id, blame=""):
    return DivergenceRow(
        sid=sid,
        section_key=section,
        diagnosis=diagnosis,
        disposition=disposition,
        rule_id=rule_id,
        blame_source=blame,
    )


def _ledger(inputs):
    return build_ledger(
        inputs,
        jurisdiction="fi",
        mode="official_consolidation",
        catalog={"r.alpha": "alpha believed spec"},
    )


def test_firings_are_summed_across_statutes():
    inputs = [
        StatuteLedgerInput("a/1", {"r.alpha": 3, "r.beta": 1}, []),
        StatuteLedgerInput("b/2", {"r.alpha": 2}, []),
    ]
    led = _ledger(inputs)
    assert led.statutes == 2
    assert led.rules["r.alpha"].firings == 5
    assert led.rules["r.beta"].firings == 1


def test_divergence_attributed_to_rule_and_bucketed_by_disposition():
    inputs = [
        StatuteLedgerInput(
            "a/1",
            {"r.alpha": 4},
            [
                _div("a/1", "section:5", "REPLAY_EXTRA", "lawvm_wrong", "r.alpha"),
                _div("a/1", "section:6", "EDITORIAL_CONVENTION", "oracle_suspect", "r.alpha"),
                _div("a/1", "section:7", "MISSING", "structural", "r.alpha"),
            ],
        )
    ]
    led = _ledger(inputs)
    e = led.rules["r.alpha"]
    assert e.by_disposition == {"lawvm_wrong": 1, "oracle_suspect": 1, "structural": 1}
    # contradicted = falsifying evidence = lawvm_wrong + structural (not oracle_suspect)
    assert e.contradicted == 2
    assert e.divergences == 3
    # corroborated estimate = firings - divergences
    assert e.corroborated_est == 1


def test_unattributed_falsifying_divergence_is_a_blind_spot():
    inputs = [
        StatuteLedgerInput(
            "a/1",
            {},
            [
                _div("a/1", "section:1", "REPLAY_EXTRA", "lawvm_wrong", None),
                # oracle_suspect with no rule is NOT a blind spot (not our bug)
                _div("a/1", "section:2", "ORACLE_STALE", "oracle_suspect", None),
            ],
        )
    ]
    led = _ledger(inputs)
    assert len(led.unattributed) == 1
    assert led.unattributed[0]["section"] == "section:1"


def test_ranking_frontier_prefers_uncertain_over_more_contradicted():
    # Post-§8(7): the rank is B × S × EIG, not raw contradicted count. With equal blast
    # radius (both fire once in the same statute), r.high has MORE contradicted (2 vs 1)
    # but its suspicion is already near-certain, so its expected-information-gain (Beta
    # variance) is lower; r.low sits closer to the s≈0.5 frontier and outranks it — the
    # active-learning point of the redesign.
    inputs = [
        StatuteLedgerInput(
            "a/1",
            {"r.low": 1, "r.high": 1},
            [
                _div("a/1", "section:1", "REPLAY_EXTRA", "lawvm_wrong", "r.low"),
                _div("a/1", "section:2", "REPLAY_EXTRA", "lawvm_wrong", "r.high"),
                _div("a/1", "section:3", "MISSING", "structural", "r.high"),
            ],
        )
    ]
    led = _ledger(inputs)
    ranked = led.ranked_entries()
    assert ranked[0].rule_id == "r.low"
    assert ranked[0].expected_information_gain > led.rules["r.high"].expected_information_gain


def test_blast_radius_counts_distinct_statutes_not_firings():
    inputs = [
        # r.wide fires once in each of two statutes; r.deep fires 50× in one statute.
        StatuteLedgerInput("a/1", {"r.wide": 1, "r.deep": 50}, []),
        StatuteLedgerInput("b/2", {"r.wide": 1}, []),
    ]
    led = _ledger(inputs)
    assert led.rules["r.wide"].blast_radius == 2
    assert led.rules["r.deep"].blast_radius == 1
    assert led.rules["r.deep"].firings == 50


def test_catalog_populates_believed_spec_and_cataloged_flag():
    led = _ledger([StatuteLedgerInput("a/1", {"r.alpha": 1, "r.uncat": 1}, [])])
    assert led.rules["r.alpha"].believed_spec == "alpha believed spec"
    assert led.rules["r.alpha"].to_dict()["cataloged"] is True
    # uncataloged rule is loud (cataloged False, empty believed_spec)
    assert led.rules["r.uncat"].to_dict()["cataloged"] is False


def test_render_markdown_is_stable_and_lists_rules():
    led = _ledger([StatuteLedgerInput("a/1", {"r.alpha": 2}, [])])
    out = render_markdown(led)
    assert "Spec-discovery ledger (-j fi" in out
    assert "r.alpha" in out


def test_fi_diagnosis_disposition_map_key_buckets():
    # our-bug diagnoses
    assert _FI_DIAGNOSIS_DISPOSITION["REPLAY_EXTRA"] == "lawvm_wrong"
    assert _FI_DIAGNOSIS_DISPOSITION["REPLAY_MISSING"] == "lawvm_wrong"
    # oracle-fault diagnoses
    assert _FI_DIAGNOSIS_DISPOSITION["ORACLE_STALE"] == "oracle_suspect"
    assert _FI_DIAGNOSIS_DISPOSITION["EDITORIAL_CONVENTION"] == "oracle_suspect"
    # incomplete source
    assert _FI_DIAGNOSIS_DISPOSITION["SOURCE_INCOMPLETE"] == "missing_source"
