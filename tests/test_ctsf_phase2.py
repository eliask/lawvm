"""Tests for CTSF Phase 2 (task #197):

* the STATE_INDEX commensurability layer — each typed residual on a fixture
  (commensurable vs each incommensurable case);
* the commensurability-FIRST short-circuit ordering (an incommensurable pair
  NEVER reaches the content comparator);
* the two newly-migrated CTSF rules' four-part admission gates (+ a negative
  test that removing their control pairs is rejected);
* the parallel residual-set report on fixtures;
* a byte-identity guard that default bench output is unchanged by importing
  the Phase-2 modules.
"""

from __future__ import annotations

import pytest

from lawvm.core.ctsf import collect_elisions, ctsf_equal, to_ctsf
from lawvm.core.ctsf_admission_gate import check_rule_admission
from lawvm.core.ctsf_rules import registered_ctsf_rules
from lawvm.core.ctsf_state_index import (
    STATE_INDEX_VERSION,
    CommensurabilityOutcome,
    StateIndex,
    StateIndexResidual,
    classify_commensurability,
    commensurability_first,
    is_commensurable,
)
from lawvm.core.ctsf_residual_report import (
    RESIDUAL_VERDICT_FAMILIES,
    residual_set_report,
)
from lawvm.semantic.model import SemanticStructureFacet, SemanticStructureNode


def _wf(text: str) -> tuple[SemanticStructureFacet, ...]:
    return (SemanticStructureFacet(kind="wording", text=text),)


def _node(kind: str, label: str, *, text: str = "") -> SemanticStructureNode:
    return SemanticStructureNode(kind=kind, label=label, facets=_wf(text) if text else ())


def _sec(label: str, *, text: str = "") -> SemanticStructureNode:
    return _node("section", label, text=text)


# ---------------------------------------------------------------------------
# STATE_INDEX commensurability layer — each typed residual
# ---------------------------------------------------------------------------


def test_commensurable_pair_emits_no_state_index_residual():
    r = classify_commensurability(
        StateIndex(as_of="2020-01-01"), StateIndex(as_of="2020-01-01"), address="s"
    )
    assert r == ()
    assert is_commensurable(StateIndex(as_of="2020-01-01"), StateIndex(as_of="2020-01-01"))


def test_state_index_oracle_newer_unit():
    # replay PIT precedes the oracle version's effective date, but the oracle's
    # OWN cutoff already covers it ⇒ oracle rendered a newer unit than replay asks.
    r = classify_commensurability(
        StateIndex(as_of="2020-01-01"),
        StateIndex(as_of="2021-12-01", effective_date="2021-06-01", version_amendment_id="2021/5"),
        address="section:5",
    )
    assert [x.kind for x in r] == ["STATE_INDEX.ORACLE_NEWER_UNIT"]
    assert r[0].family == "temporal_mismatch"
    assert "2021/5" in r[0].evidence


def test_state_index_future_effective_embedded():
    # the oracle embedded a version whose effective date postdates the oracle's
    # OWN cutoff too — a future-effective embedding.
    r = classify_commensurability(
        StateIndex(as_of="2019-01-01"),
        StateIndex(as_of="2020-01-01", effective_date="2021-06-01"),
        address="section:5",
    )
    assert [x.kind for x in r] == ["STATE_INDEX.FUTURE_EFFECTIVE_EMBEDDED"]


def test_state_index_expiry_cutoff_mismatch():
    r = classify_commensurability(
        StateIndex(as_of="2020-01-01"),
        StateIndex(as_of="2020-06-01", expiry_date="2020-01-01", version_amendment_id="2018/1"),
        address="section:5",
    )
    assert [x.kind for x in r] == ["STATE_INDEX.EXPIRY_CUTOFF_MISMATCH"]


def test_state_index_extent_branch_mismatch():
    r = classify_commensurability(
        StateIndex(extent_branch="mainland"),
        StateIndex(extent_branch="aland"),
        address="section:5",
    )
    assert [x.kind for x in r] == ["STATE_INDEX.EXTENT_BRANCH_MISMATCH"]
    assert r[0].family == "extent_branch_mismatch"


def test_state_index_unit_version_ambiguous():
    r = classify_commensurability(
        StateIndex(),
        StateIndex(version_selection="matched genuine version 2019/3"),
        address="section:5",
    )
    assert [x.kind for x in r] == ["STATE_INDEX.UNIT_VERSION_AMBIGUOUS"]


def test_state_index_unknown_coordinate_fails_open():
    # Unknown coordinates never manufacture an incommensurability (fail open).
    assert classify_commensurability(StateIndex(), StateIndex()) == ()


def test_state_index_residual_projects_into_agreement_residual_sink():
    res = StateIndexResidual(
        kind="STATE_INDEX.ORACLE_NEWER_UNIT", address="section:5", evidence="e"
    )
    ar = res.to_agreement_residual()
    assert ar.family == "temporal_mismatch"
    assert ar.rule_id == "STATE_INDEX.ORACLE_NEWER_UNIT"
    assert ar.agreement_residual_status == "blocked"


def test_state_index_residual_rejects_bad_kind():
    with pytest.raises(ValueError):
        StateIndexResidual(kind="STATE_INDEX.NOT_A_KIND", address="s", evidence="e")  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# The commensurability-FIRST short-circuit ordering (load-bearing)
# ---------------------------------------------------------------------------


def test_incommensurable_pair_never_reaches_content_compare():
    calls: list[int] = []

    def content() -> bool:
        calls.append(1)
        return True

    outcome = commensurability_first(
        StateIndex(as_of="2019-01-01"),
        StateIndex(as_of="2020-01-01", effective_date="2021-06-01"),
        content,
        address="section:5",
    )
    assert isinstance(outcome, CommensurabilityOutcome)
    assert outcome.commensurable is False
    assert outcome.content_compared is False
    assert outcome.content_equal is None
    assert calls == [], "content comparator ran on an incommensurable pair"
    assert outcome.state_index_residuals
    assert outcome.to_dict()["state_index_version"] == STATE_INDEX_VERSION


def test_commensurable_pair_reaches_content_compare():
    calls: list[int] = []

    def content() -> bool:
        calls.append(1)
        return False

    outcome = commensurability_first(
        StateIndex(as_of="2020-01-01"), StateIndex(as_of="2020-01-01"), content
    )
    assert outcome.commensurable is True
    assert outcome.content_compared is True
    assert outcome.content_equal is False
    assert calls == [1]


# ---------------------------------------------------------------------------
# The two newly-migrated rules: behavior + four-part gate
# ---------------------------------------------------------------------------


def test_migrated_momentti_ordinal_elision_equates_label_redundant_ordinal():
    clean = to_ctsf(_node("subsection", "2", text="momentin teksti"))
    ordinal = to_ctsf(_node("subsection", "2", text="2. momentin teksti"))
    assert ctsf_equal(clean, ordinal)
    assert any(
        e.rule_id == "ctsf.text.momentti_ordinal_elision" for e in collect_elisions(ordinal)
    )


def test_migrated_digit_item_renesting_equates_flat_and_merged():
    merged = to_ctsf(_node("item", "3", text="kohdan teksti"))
    flat = to_ctsf(_node("item", "3", text="3) kohdan teksti"))
    assert ctsf_equal(merged, flat)
    assert any(
        e.rule_id == "ctsf.structure.digit_item_renesting_elision"
        for e in collect_elisions(flat)
    )


def test_label_redundant_elision_is_label_aware_not_over_merging():
    # A leading number that does NOT equal the label is genuine content — the
    # elision must NOT fire (falsifier guard).
    a = to_ctsf(_node("subsection", "2", text="1. tammikuuta alkaen"))
    b = to_ctsf(_node("subsection", "2", text="tammikuuta alkaen"))
    assert not ctsf_equal(a, b)
    # and a mismatched digit-item prefix is preserved too
    c = to_ctsf(_node("item", "3", text="5) muu asia"))
    d = to_ctsf(_node("item", "3", text="muu asia"))
    assert not ctsf_equal(c, d)


def test_two_phase2_rules_pass_four_part_admission_gate():
    by_id = {r.rule_id: r for r in registered_ctsf_rules()}
    for rule_id in (
        "ctsf.text.momentti_ordinal_elision",
        "ctsf.structure.digit_item_renesting_elision",
    ):
        res = check_rule_admission(by_id[rule_id])
        assert res.admitted, f"{rule_id} rejected: {res.failures}"


def test_phase2_rule_without_control_pairs_is_rejected():
    """Negative: strip a Phase-2 rule's obligations → the gate REJECTS it."""
    from dataclasses import replace

    by_id = {r.rule_id: r for r in registered_ctsf_rules()}
    stripped = replace(
        by_id["ctsf.structure.digit_item_renesting_elision"],
        unamended_control_pairs=(),
        quoted_payload_control_pairs=(),
        congruence_cases=(),
        witness_cases=(),
    )
    res = check_rule_admission(stripped)
    assert not res.admitted
    joined = " | ".join(res.failures)
    assert "missing obligation (a)" in joined
    assert "missing obligation (c)" in joined
    assert "missing obligation (d)" in joined


# ---------------------------------------------------------------------------
# Parallel residual-set report (READ-ONLY, non-gating)
# ---------------------------------------------------------------------------


def test_residual_report_verdict_zero_when_commensurable_and_ctsf_equal():
    rep = residual_set_report(_sec("5", text="maksu 20"), _sec("5", text="maksu.......... 20"), sid="x")
    assert rep.commensurable is True
    assert rep.ctsf_equal is True
    assert set(rep.verdict) == set(RESIDUAL_VERDICT_FAMILIES)
    assert all(v == 0 for v in rep.verdict.values())
    assert rep.has_replay_bug_or_unknown is False


def test_residual_report_unknown_when_commensurable_and_ctsf_unequal():
    rep = residual_set_report(_sec("5", text="maksu 20"), _sec("5", text="maksu 30"), sid="x")
    assert rep.commensurable is True
    assert rep.ctsf_equal is False
    assert rep.verdict["unknown"] == 1
    assert rep.has_replay_bug_or_unknown is True


def test_residual_report_incommensurable_short_circuits_no_content_residual():
    # The johtolause-episode fix in miniature: a state-index-incommensurable pair
    # that is ALSO CTSF-unequal contributes NO content residual — the whole
    # divergence is attributed to state_index, and the gate stays clean.
    rep = residual_set_report(
        _sec("5", text="maksu 20"),
        _sec("5", text="maksu 30"),
        sid="x",
        replay_index=StateIndex(as_of="2020-01-01"),
        oracle_index=StateIndex(as_of="2020-06-01", effective_date="2021-06-01"),
    )
    assert rep.commensurable is False
    assert rep.ctsf_equal is None  # content comparison never ran
    assert rep.verdict["state_index"] == 1
    assert rep.verdict["unknown"] == 0
    assert rep.has_replay_bug_or_unknown is False


def test_residual_report_counts_cnf_unsupported():
    from lawvm.core.table_model import TableBody

    oracle = SemanticStructureNode(
        kind="section",
        label="9",
        facets=(
            SemanticStructureFacet(
                kind="wording", text="t", tables=(TableBody(table_id="t1", caption="", columns=(), rows=()),)
            ),
        ),
    )
    rep = residual_set_report(_sec("9", text="t"), oracle, sid="x")
    assert rep.verdict["cnf_unsupported"] == 1


# ---------------------------------------------------------------------------
# Byte-identity guard: importing Phase-2 modules must not perturb bench output.
# ---------------------------------------------------------------------------


def test_bench_neutralizer_unchanged_by_phase2_import():
    from lawvm.tools import bench

    sd = {"label": 0, "structural": 0}
    events = [
        {
            "kind": "wording_text_changed",
            "left_text": "kuolemansyyn selvittämiseksi",
            "right_text": "kuolemansyynselvittämiseksi",
        }
    ]
    before = bench._section_diff_is_bench_neutralized(sd, events)

    import lawvm.core.ctsf_state_index  # noqa: F401
    import lawvm.core.ctsf_residual_report  # noqa: F401

    after = bench._section_diff_is_bench_neutralized(sd, events)
    assert before == after is True


def test_digit_renesting_neutralizer_unchanged_by_phase2_import():
    """The rule Phase-2 migrates (digit-renesting) still behaves identically in
    bench — CTSF is a PARALLEL surface, it does not alter the bench predicate."""
    from lawvm.tools import bench

    sd = {"label": 0}
    events = [
        {"kind": "facet_removed", "facet_kind": "intro", "left_text": "Johdanto:"},
        {"kind": "unit_missing_right", "unit_kind": "item", "left_text": "eka"},
        {"kind": "unit_missing_left", "unit_kind": "subsection", "right_text": "1) eka"},
    ]
    before = bench._section_diff_is_bench_neutralized(sd, events)

    import lawvm.core.ctsf_state_index  # noqa: F401
    import lawvm.core.ctsf_residual_report  # noqa: F401

    after = bench._section_diff_is_bench_neutralized(sd, events)
    assert before == after
