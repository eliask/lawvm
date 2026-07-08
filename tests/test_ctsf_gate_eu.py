"""Tests for the EUROPEAN UNION CTSF residual-set-diff gate corpus (#204 → #221).

The EU analogue of ``test_ctsf_gate.py`` / ``test_ctsf_gate_ee.py`` /
``test_ctsf_gate_uk.py``. Covers:

* the frozen EU corpus membership is content-pinned — the 8 oracle-touch bases
  (published sector-0 consolidations stored offline) plus the explicit
  ``(amender, base)`` conserved-apply fallback chain, all sorted, unique;
* the frozen dated amendment-closure table (the multi-amender PIT closure input)
  is well-formed: dated ``amends`` edges per oracle base, one closure per base;
* the committed EU baseline self-describes (jurisdiction + oracle bases + fallback
  chains) and carries ZERO billable (replay_bug/unknown) residuals — the honest
  0-billable steady state;
* the EU diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI/EE/UK, over the EU baseline);
* the baseline round-trips (write → load → equal);
* FAIL-RED wiring: ``run_eu_gate_report`` PASSes against the committed baseline
  (data-present) and the multi-jurisdiction ``run_gate`` folds the EU verdict into
  the exit code; the EU lane SKIPS clean when the EU Cellar Farchive is absent.

THE #221 FLIP (documented, load-bearing). EU joined the gate (#204) on the WEAK
conserved-apply invariant because the Farchive then stored no consolidation oracle
and no dated amendment DAG. #221 stored the 75 published dated sector-0
consolidations of 8/9 frozen bases plus the frozen dated closure table, and flipped
EU onto the SAME oracle-touch surface FI/EE/UK/NZ use: per stored ``(base, as_of)``
the multi-amender PIT closure is replayed offline, diffed per-article against the
published consolidation, and every divergence is TYPED by Finland's neutral
touch-relation calculus (never repaired toward the editorial consolidation).
Closure gaps (missing amender bytes / unlowered instructions / typed op-skips)
commensurability-mark their anchors (→ ``temporal_mismatch``) and surface as
explicit typed rows. ``32017R1576`` (zero published consolidations) keeps the
conserved-apply fallback lane. See ``lawvm.tools.eu_anchor_manifest`` (#221
section) and the EU section of ``lawvm.core.ctsf_gate``.

The data-present tests score the EU corpus via offline replay over the EU Cellar
Farchive; they SKIP cleanly when it is absent (a corpus-free CI checkout). The unit
surface (diff logic over the committed baseline, round-trip, closure-table shape)
is corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_EU_BASELINE_PATH,
    REAL_ANCHOR_EU_CORPUS_CHAINS,
    REAL_ANCHOR_EU_JURISDICTION,
    GateResult,
    _eu_baseline_payload,
    _repo_root,
    eu_anchor_corpus_available,
    load_eu_baseline,
    residual_set_diff_gate,
    run_eu_gate_report,
    run_gate,
    score_eu_real_corpus,
    write_eu_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES
from lawvm.tools.eu_anchor_manifest import (
    REAL_ANCHOR_EU_AMENDMENT_CLOSURE,
    REAL_ANCHOR_EU_ORACLE_BASES,
    EUAmendmentEdgeRef,
    _typography_commensurable_equal,
)

# The EU corpus is scored via offline replay over the EU Cellar Farchive; skip the
# real-corpus tests cleanly when it is absent. The unit surface is corpus-free and
# always runs.
requires_eu_corpus = pytest.mark.skipif(
    not eu_anchor_corpus_available(),
    reason="EU Cellar Farchive absent; EU #204 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_eu_corpus_chains_are_frozen_and_sorted():
    """The EU corpus membership is content-pinned: the oracle-touch bases and the
    fallback ``(amender, base)`` pairs are explicit, sorted, unique, non-empty
    tuples (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_EU_CORPUS_CHAINS, tuple)
    assert REAL_ANCHOR_EU_CORPUS_CHAINS
    assert list(REAL_ANCHOR_EU_CORPUS_CHAINS) == sorted(REAL_ANCHOR_EU_CORPUS_CHAINS)
    assert len(set(REAL_ANCHOR_EU_CORPUS_CHAINS)) == len(REAL_ANCHOR_EU_CORPUS_CHAINS)
    for chain in REAL_ANCHOR_EU_CORPUS_CHAINS:
        assert isinstance(chain, tuple) and len(chain) == 2
        amender, base = chain
        assert amender and base and amender != base
    assert isinstance(REAL_ANCHOR_EU_ORACLE_BASES, tuple)
    assert REAL_ANCHOR_EU_ORACLE_BASES
    assert list(REAL_ANCHOR_EU_ORACLE_BASES) == sorted(REAL_ANCHOR_EU_ORACLE_BASES)
    assert len(set(REAL_ANCHOR_EU_ORACLE_BASES)) == len(REAL_ANCHOR_EU_ORACLE_BASES)
    # The fallback lane and the oracle lane are DISJOINT: a base with a stored
    # published consolidation is never scored on the weak invariant.
    fallback_bases = {base for _, base in REAL_ANCHOR_EU_CORPUS_CHAINS}
    assert not (fallback_bases & set(REAL_ANCHOR_EU_ORACLE_BASES))


def test_eu_amendment_closure_table_is_frozen_and_well_formed():
    """The dated amendment-closure table (the multi-amender PIT closure input) is
    content-pinned per oracle base: every oracle base has a closure, every edge is
    typed ``amends``/``corrects``, dated edges are ISO, and only dated ``amends``
    edges can enter a closure (``effective_by``)."""
    assert set(REAL_ANCHOR_EU_AMENDMENT_CLOSURE) == set(REAL_ANCHOR_EU_ORACLE_BASES)
    for base, edges in REAL_ANCHOR_EU_AMENDMENT_CLOSURE.items():
        assert edges, base
        for e in edges:
            assert isinstance(e, EUAmendmentEdgeRef)
            assert e.relation_kind in ("amends", "corrects")
            for d in (e.entry_into_force, e.date_of_application):
                assert d == "" or (len(d) == 10 and d[4] == "-" and d[7] == "-")
            # An undated edge can never enter a dated closure.
            if not e.earliest_date:
                assert not e.effective_by("9999-12-31")


def test_typography_commensurable_surface_is_symmetric_and_narrow():
    """The EU commensurable compare surface elides ONLY typography (whitespace
    and list punctuation): point-marker spacing/parenthesization, sign spacing
    and list-separator reflow agree; any WORDING difference stays divergent."""
    assert _typography_commensurable_equal("by: (a) the UN", "by:(a)the UN")
    assert _typography_commensurable_equal("at –7 °C", "at – 7 °C")
    # LIST PUNCTUATION is typography: the Office re-renders an amendment
    # payload's marker/separator style to the base act's house style (the real
    # 32010R0053 → 32009R0754 Article 1 point-add: source ``c)`` and terminal
    # ``.``; consolidation ``(c)`` and ``;``).
    assert _typography_commensurable_equal("rules b. c) the group", "rules b; (c) the group")
    # Symmetric.
    assert _typography_commensurable_equal("a b", "ab") == _typography_commensurable_equal("ab", "a b")
    # Wording differences are NEVER elided (A.TR.1 vs A.TR. stays divergent).
    assert not _typography_commensurable_equal("certificate A.TR.1.", "certificate A.TR.")


def test_committed_eu_baseline_declares_corpus():
    """The committed EU baseline records the jurisdiction + frozen corpus (oracle
    bases + fallback chains)."""
    data = json.loads(
        (_repo_root() / GATE_EU_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_EU_JURISDICTION
    assert tuple(tuple(c) for c in data["corpus_chains"]) == REAL_ANCHOR_EU_CORPUS_CHAINS
    assert tuple(data["corpus_oracle_bases"]) == REAL_ANCHOR_EU_ORACLE_BASES


def test_committed_eu_baseline_is_zero_billable():
    """The committed EU baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance (#221): the oracle-touch flip itself CONVICTED two genuine replay bugs
    the conserved-apply lane had scored clean (32023R0331's omnibus cross-target
    misapplication landing Regulation 356/2010's Article 4 in 32022R2309, and the
    quoted whole-article payload carrying its own heading + the instruction's trailing
    period). Both were fixed at ROOT in ``fmx4_amendment_grammar`` (foreign-target
    guard; heading strip; QUOT.END payload boundary) and the convicting window
    (32022R2309@20230216) now replays byte-clean on the commensurable surface — the
    metric earning its keep, cleared before freezing. If a future corpus/replay change
    surfaces a NEW real billable, that is a preregistered event to attribute (bug
    fix), never to freeze green.
    """
    committed = load_eu_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed EU baseline carries a standing billable residual. If a replay "
        "window legitimately surfaced a NEW real replay bug this is a preregistered "
        "event — investigate + attribute it, do not freeze an un-triaged billable."
    )


def test_committed_eu_baseline_exercises_typed_nonbillable_lane():
    """The EU baseline is not vacuous: it exercises the typed non-billable lanes —
    ``cnf_unsupported`` (lowering/curation capability gaps), ``temporal_mismatch``
    (commensurability-limited closure-gap anchors), and
    ``oracle_editorial_pathology`` (corrigendum-lane consolidation renderings of
    units replay never touched) — so the WARN machinery runs over real rows."""
    committed = load_eu_baseline()
    all_fams = {fam for families in committed.values() for fam in families}
    assert "cnf_unsupported" in all_fams
    assert "temporal_mismatch" in all_fams
    assert "oracle_editorial_pathology" in all_fams
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the EU baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_eu_gate_passes_on_identical_set():
    base = load_eu_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_eu_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed EU baseline
    FAILs the gate — the two lanes the honest metric exists to guard (an EU apply RAISE
    projects to replay_bug, a conservation violation to unknown)."""
    base = load_eu_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic->regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_eu_typed_move_warns():
    """A NEW typed non-billable residual over the EU baseline WARNs, never FAILs."""
    base = load_eu_baseline()
    for fam in ("cnf_unsupported", "oracle_editorial_pathology", "temporal_mismatch"):
        current = {**base, "synthetic->typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_eu_baseline_round_trips(tmp_path):
    """write → load round-trips an EU residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no EU Cellar Farchive)."""
    residuals = {
        "a->b": {"replay_bug": 1, "cnf_unsupported": 2},
        "c->d": {},
        "e->f": {"unknown": 3},
    }
    path = tmp_path / "eu_baseline.json"
    write_eu_baseline(residuals=residuals, path=path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["typed_skip_bucket_counts"] == {}
    assert load_eu_baseline(path) == residuals


def test_eu_baseline_payload_carries_non_gating_typed_skip_buckets(tmp_path):
    """I1 diagnostics persist typed skip buckets without changing gate residuals."""
    residuals = {"32000R0000": {"cnf_unsupported": 2}}
    typed_skip_bucket_counts = {
        "32000R0000": {
            "target_kind_label_absent": 1,
            "empty_target_label": 1,
        }
    }

    payload = _eu_baseline_payload(
        residuals,
        typed_skip_bucket_counts=typed_skip_bucket_counts,
    )

    assert payload["residuals"] == residuals
    assert payload["typed_skip_bucket_counts"] == typed_skip_bucket_counts
    path = tmp_path / "eu_baseline_with_diagnostics.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_eu_baseline(path) == residuals


def test_eu_baseline_path_is_under_tests_data():
    assert GATE_EU_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the EU real corpus scores 0-billable + PASSes; fail-red wiring.
# ---------------------------------------------------------------------------


@requires_eu_corpus
def test_eu_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_eu_real_corpus()
    b = score_eu_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_eu_corpus
def test_committed_eu_baseline_matches_real_corpus():
    """The on-disk EU baseline is not stale: it equals the real EU corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.core.ctsf_gate --update-eu-baseline"""
    assert load_eu_baseline() == score_eu_real_corpus(), (
        "Committed EU CTSF baseline is stale vs the real #204 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.core.ctsf_gate --update-eu-baseline`."
    )


@requires_eu_corpus
def test_eu_real_corpus_is_zero_billable():
    """The real EU corpus scored live carries no billable residual — the honest steady
    state (not just the committed snapshot)."""
    live = score_eu_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_eu_corpus
def test_eu_real_corpus_gate_passes():
    """The EU corpus scored against its committed baseline PASSes (empty diff)."""
    result = run_eu_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_eu_corpus
def test_run_gate_passes_clean_eu_returns_zero():
    """The multi-jurisdiction PRIMARY callable gate returns 0 on the clean EU (and FI/
    EE/UK, if present) baseline."""
    assert run_gate() == 0
