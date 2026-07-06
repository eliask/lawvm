"""Frontier-ranking (B × S × EIG) tests for the witness-attribution ledger.

FABLE_SPEC_RECONSTRUCTION §8(7): the ledger's rank was upgraded from raw firing count
to ``score = blast_radius × suspicion × expected_information_gain``. These tests pin the
score math (with the documented Beta(1,1) prior) and the *reshuffle* it produces on a
fixed synthetic ledger — a rare, wide-blast, plausibly-defective rule rises over a
frequent-benign one that the old firing-count rank would have topped. All fixtures are
synthetic; no corpus is run.
"""
from __future__ import annotations

import math

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    StatuteLedgerInput,
    blast_radius,
    build_ledger,
    expected_information_gain,
    frontier_score,
    suspicion,
)


def _div(sid, section, disposition, rule_id):
    return DivergenceRow(
        sid=sid,
        section_key=section,
        diagnosis=disposition,
        disposition=disposition,
        rule_id=rule_id,
    )


# ---------------------------------------------------------------------------
# Factor math (documented Beta(1,1) prior)
# ---------------------------------------------------------------------------

def test_blast_radius_is_log_damped_distinct_statute_count():
    assert blast_radius(0) == 1.0
    assert blast_radius(1) == 1.0 + math.log1p(1)
    # log damping: 100× the statutes is far from 100× the blast radius.
    assert blast_radius(100) < 6.0
    assert blast_radius(2) > blast_radius(1)  # monotone increasing


def test_suspicion_is_beta_posterior_mean_with_uniform_prior():
    # No evidence => maximally uncertain 0.5 (the Beta(1,1) mean).
    assert suspicion(0, 0) == 0.5
    # 1000 firings all clean => posterior mean shrinks toward 0.
    assert suspicion(0, 1000) < 0.01
    # 10 firings, 6 contradicted / 4 clean => (6+1)/(10+2) = 7/12.
    assert abs(suspicion(6, 4) - 7 / 12) < 1e-12


def test_eig_is_beta_variance_peaking_at_uncertainty():
    # Variance is highest when uncertain and low-sample; it collapses with evidence.
    uncertain = expected_information_gain(0, 0)          # α=β=1 => 1/12
    assert abs(uncertain - 1 / 12) < 1e-12
    confident_clean = expected_information_gain(0, 1000)
    confident_broken = expected_information_gain(1000, 0)
    assert confident_clean < uncertain
    assert confident_broken < uncertain
    # A 50/50 split at LOW sample count still carries more EIG than the same rate at
    # high sample count (the active-learning point).
    assert expected_information_gain(1, 1) > expected_information_gain(50, 50)


def test_frontier_score_is_product_of_the_three_factors():
    b, c, k = 5, 3, 7
    assert abs(
        frontier_score(b, c, k)
        - blast_radius(b) * suspicion(c, k) * expected_information_gain(c, k)
    ) < 1e-15


# ---------------------------------------------------------------------------
# The reshuffle: old firing-count rank vs new B × S × EIG rank
# ---------------------------------------------------------------------------

def _reshuffle_ledger():
    """Fixed synthetic ledger built to expose the reshuffle.

    * ``r.frequent_benign`` — fires 1000× in ONE statute, never contradicted. Tops the
      old firing-count rank; near-zero suspicion + collapsed EIG sink it in the new rank.
    * ``r.rare_wide_suspect`` — fires once in each of 6 statutes, contradicted 3×. Low
      firing count (buried by the old rank) but wide blast radius + mid suspicion + high
      EIG lift it to the top of the new rank.
    * ``r.mid`` — a middling rule between them.
    """
    inputs = [
        StatuteLedgerInput("big/1", {"r.frequent_benign": 1000, "r.mid": 4}, [
            _div("big/1", "s:1", "lawvm_wrong", "r.mid"),
        ]),
    ]
    # r.rare_wide_suspect fires once in each of six distinct statutes; 3 are contradicted.
    for i in range(6):
        sid = f"w/{i}"
        divs = []
        if i < 3:
            divs = [_div(sid, "s:1", "lawvm_wrong", "r.rare_wide_suspect")]
        inputs.append(StatuteLedgerInput(sid, {"r.rare_wide_suspect": 1}, divs))
    return build_ledger(
        inputs,
        jurisdiction="fi",
        mode="official_consolidation",
        catalog={
            "r.frequent_benign": "a benign frequent rule",
            "r.rare_wide_suspect": "a rare wide suspicious rule",
            "r.mid": "a middling rule",
        },
    )


def test_old_firing_rank_tops_the_frequent_benign_rule():
    led = _reshuffle_ledger()
    old_rank = sorted(led.rules.values(), key=lambda e: e.firings, reverse=True)
    assert old_rank[0].rule_id == "r.frequent_benign"


def test_new_frontier_rank_lifts_rare_wide_suspect_over_frequent_benign():
    led = _reshuffle_ledger()
    new_rank = [e.rule_id for e in led.ranked_entries()]
    # The rare, wide-blast, suspicious rule now tops the queue...
    assert new_rank[0] == "r.rare_wide_suspect"
    # ...and the frequent-benign rule (old #1) is demoted below it.
    assert new_rank.index("r.rare_wide_suspect") < new_rank.index("r.frequent_benign")
    # Full deterministic ordering pinned:
    assert new_rank == ["r.rare_wide_suspect", "r.mid", "r.frequent_benign"]


def test_frontier_factors_surface_in_to_dict():
    led = _reshuffle_ledger()
    d = led.rules["r.rare_wide_suspect"].to_dict()
    assert d["blast_radius"] == 6
    assert d["contradicted"] == 3
    # firings retained for side-by-side comparison with the legacy rank.
    assert d["firings"] == 6
    assert 0.0 < d["suspicion"] < 1.0
    assert d["expected_information_gain"] > 0.0
    assert d["frontier_score"] > led.rules["r.frequent_benign"].to_dict()["frontier_score"]


def test_ranking_is_deterministic_across_repeated_builds():
    r1 = [e.rule_id for e in _reshuffle_ledger().ranked_entries()]
    r2 = [e.rule_id for e in _reshuffle_ledger().ranked_entries()]
    assert r1 == r2
