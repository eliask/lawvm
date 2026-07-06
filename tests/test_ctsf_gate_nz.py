"""Tests for the NEW ZEALAND CTSF residual-set-diff gate corpus (task #205).

The NZ analogue of ``test_ctsf_gate_ee.py`` / ``test_ctsf_gate_uk.py``: extends the
now-PRIMARY CTSF gate to a fourth jurisdiction over the RICHEST anchor surface — the
dense DATED point-in-time archived-version chain legislation.govt.nz publishes per
act (the ``nz_anchor_manifest`` engine replays a single evolving tree base→latest and
scores each dated snapshot against its archived oracle). Covers:

* the frozen NZ corpus membership is content-pinned (explicit, sorted, unique);
* the committed NZ baseline self-describes (jurisdiction + corpus sids) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the NZ diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same generic diff logic as FI/EE/UK, over the NZ baseline);
* the baseline round-trips (write → load → equal);
* the ENGINE's FAIL lane is live: a synthetic anchor chain carrying an op-local-
  convicted, replay-touched, persistent divergence yields a ``candidate_replay_bug``
  (so the 0-billable steady state is a real clean signal, not a dead detector);
* data-present: the real NZ corpus scores 0-billable, deterministically, and equals
  the committed baseline; SKIPS clean when the NZ Farchive is absent.

The data-present tests score the NZ corpus via chain replay over the NZ Farchive;
they SKIP cleanly when it is absent (a corpus-free CI checkout). The unit surface
(diff logic over the committed baseline, round-trip, engine synthetic injection) is
corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GateResult,
    _repo_root,
    residual_set_diff_gate,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES
from lawvm.tools.fi_anchor_manifest import AnchorObservation, attribute_divergences
from lawvm.tools.nz_anchor_manifest import (
    GATE_NZ_BASELINE_PATH,
    REAL_ANCHOR_NZ_CORPUS_SIDS,
    REAL_ANCHOR_NZ_JURISDICTION,
    _stable_key,
    nz_anchor_corpus_available,
    observation_to_residual,
    score_nz_real_corpus,
)

# The NZ corpus is scored via chain replay over the NZ Farchive; skip the real-corpus
# tests cleanly when it is absent. The unit surface is corpus-free and always runs.
requires_nz_corpus = pytest.mark.skipif(
    not nz_anchor_corpus_available(),
    reason="NZ legislation Farchive absent; NZ #205 anchor corpus cannot be scored",
)


def _load_nz_baseline() -> dict[str, dict[str, int]]:
    """Load the frozen NZ typed-residual baseline ({sid: {family: count}}).

    Self-contained (the parent's ctsf_gate NZ loaders are added when it wires the
    ``--- NEW ZEALAND ---`` gate section); reads the committed artifact's
    ``residuals`` field exactly as ``load_ee_baseline`` does for EE.
    """
    p = _repo_root() / GATE_NZ_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(data.get("residuals", {}).items())
    }


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_nz_corpus_sids_are_frozen_and_sorted():
    """The NZ corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_NZ_CORPUS_SIDS, tuple)
    assert REAL_ANCHOR_NZ_CORPUS_SIDS
    assert list(REAL_ANCHOR_NZ_CORPUS_SIDS) == sorted(REAL_ANCHOR_NZ_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_NZ_CORPUS_SIDS)) == len(REAL_ANCHOR_NZ_CORPUS_SIDS)


def test_committed_nz_baseline_declares_corpus():
    """The committed NZ baseline records the jurisdiction + frozen corpus sids."""
    data = json.loads(
        (_repo_root() / GATE_NZ_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_NZ_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_NZ_CORPUS_SIDS


def test_committed_nz_baseline_is_zero_billable():
    """The committed NZ baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: NZ chain replay is an EXPLICIT partial-coverage dry-run
    (``replay_claims == False``). A per-anchor oracle-vs-replay disagreement is
    COVERAGE LAG (a skipped/uncovered/pre-2007-baked op the oracle already reflects)
    unless NZ's authoritative op-local divergence detector convicts it. Coverage-lag
    anchors are commensurability-limited (they type to ``temporal_mismatch``), so the
    corpus is 0-billable. NZ acts whose chain replay surfaces a GENUINE op-local
    wrong-op are DELIBERATELY EXCLUDED — defects to fix, not to freeze. So the NZ
    gate's FAIL lane is proven by synthetic injection (``test_nz_new_billable_fails``
    at the residual-set level + ``test_nz_engine_convicts_synthetic_wrong_op`` at the
    engine level), not a standing residual.
    """
    committed = _load_nz_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed NZ baseline carries a standing billable residual. If a NZ "
        "chain legitimately surfaced a NEW real replay bug this is a preregistered "
        "event — investigate + attribute it (bug fix or evidenced oracle_suspect "
        "re-typing), do not freeze an un-triaged billable."
    )


def test_committed_nz_baseline_exercises_typed_nonbillable_lane():
    """The NZ baseline is not vacuous: it exercises a typed non-billable family
    (``temporal_mismatch``) so the WARN lane is over a real fixture, not empty."""
    committed = _load_nz_baseline()
    all_fams = {fam for families in committed.values() for fam in families}
    assert "temporal_mismatch" in all_fams
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


def test_nz_baseline_path_is_under_tests_data():
    assert GATE_NZ_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# The diff gate over the NZ baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_nz_gate_passes_on_identical_set():
    base = _load_nz_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_nz_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed NZ baseline
    FAILs the gate — the two lanes the honest metric exists to guard."""
    base = _load_nz_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic/regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_nz_typed_move_warns():
    """A NEW typed non-billable residual over the NZ baseline WARNs, never FAILs."""
    base = _load_nz_baseline()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**base, "synthetic/typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_nz_baseline_round_trips(tmp_path):
    """A NZ residual set write → load round-trips byte-for-byte (corpus-free: an
    explicit synthetic set, so the round-trip needs no NZ archive)."""
    residuals = {
        "act_public_1/1": {"replay_bug": 1, "temporal_mismatch": 2},
        "act_public_2/2": {},
        "act_public_3/3": {"oracle_editorial_pathology": 3},
    }
    path = tmp_path / "nz_baseline.json"
    payload = {"residuals": residuals}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    loaded = {
        sid: {fam: int(cnt) for fam, cnt in fams.items()}
        for sid, fams in sorted(
            json.loads(path.read_text(encoding="utf-8"))["residuals"].items()
        )
    }
    assert loaded == residuals


# ---------------------------------------------------------------------------
# The commensurability SEAM — op-local conviction keys and penalized keys must
# share ONE key namespace, or the convictor is structurally dead (corpus-free).
# ---------------------------------------------------------------------------


def test_stable_key_drops_bare_unlabeled_part_wrapper():
    """``_stable_key`` drops a leading BARE (unlabeled) ``part`` wrapper.

    NZ's parser falls back to ``part@DLM_xml_id`` (unlabeled ``<part>`` identity),
    which ``_stable_path`` collapses to a bare ``part``. Editorial consolidation
    drops the wrapper entirely on the oracle side, so the replay-side carried tree
    (``part/prov:N``) and the oracle-side (``prov:N``) live in DISJOINT key
    namespaces unless the seam drops the bare wrapper. Dropping it aligns both
    sides so the penalized set and the op-local conviction key can intersect.
    """
    assert _stable_key(("part@DLM44815", "prov:15", "subprov:1")) == (
        "prov:15",
        "subprov:1",
    )
    # A positional/identity churn segment still collapses via _stable_path first,
    # then the bare wrapper is dropped.
    assert _stable_key(("part#0", "prov:22")) == ("prov:22",)
    # The bare-wrapper node ITSELF (no child) keeps its own ``part`` key rather than
    # collapsing to the empty key (which would merge every part wrapper of a doc).
    assert _stable_key(("part@DLM44815",)) == ("part",)


def test_stable_key_preserves_labeled_part_identity():
    """A LABELED part (``part:1``) is preserved — it carries real structural
    identity and appears identically on both the replay and oracle sides, so it is
    NOT a commensurability break and must not be flattened (that would wrongly
    merge distinct parts, e.g. ``part:1/prov:5`` and ``part:2/prov:5``)."""
    assert _stable_key(("part:1", "prov:5")) == ("part:1", "prov:5")
    assert _stable_key(("part:2", "prov:5")) == ("part:2", "prov:5")
    assert _stable_key(("part:1", "prov:5")) != _stable_key(("part:2", "prov:5"))


def test_op_local_conviction_key_is_commensurable_with_penalized_key():
    """The metric-integrity property this seam guarantees: an op-local conviction
    key built from a carried-tree ``target_path`` (which keeps the unlabeled
    ``<part>`` wrapper) lands in the SAME namespace as the penalized key built from
    the oracle-shape path (wrapper dropped). Before the seam fix these two could
    never intersect, forcing every NZ divergence to ``temporal_mismatch``
    (false-clean) and leaving the convictor structurally dead.
    """
    # Op-local side: carried-tree path with the unlabeled part wrapper.
    convicted_key = "/".join(_stable_key(("part@DLM44815", "prov:15", "subprov:1")))
    # Penalized/oracle side: consolidation dropped the wrapper.
    penalized_key = "/".join(_stable_key(("prov:15", "subprov:1")))
    assert convicted_key == penalized_key == "prov:15/subprov:1"


# ---------------------------------------------------------------------------
# The ENGINE's FAIL lane is live — a synthetic op-local-convicted wrong-op is a bug
# ---------------------------------------------------------------------------


def test_nz_engine_convicts_synthetic_wrong_op():
    """The neutral attribution engine, fed a synthetic NZ anchor chain that carries an
    op-local-convicted, replay-TOUCHED, persistent divergence, emits a billable
    ``candidate_replay_bug`` — proving the 0-billable steady state is a real clean
    signal, not a dead detector.

    The chain: unit ``prov:5`` matches at the base anchor (replay text == oracle),
    then a window TOUCHES it (replay's text changes) and it DIVERGES and stays
    diverged. The anchor is NOT ``oracle_suspect`` (its divergence is op-local-
    convicted, not coverage lag), so the wording-level touch relation convicts.
    """
    base = AnchorObservation(
        version_tag="v0",
        amendment_id="act_public_synthetic",
        as_of="2010-01-01",
        struct_sim=1.0,
        n_sections=1,
        n_penalized=0,
        penalized_keys=frozenset(),
        replay_text={"prov:5": "original text of section five"},
        oracle_suspect=None,
        status="BASE",
    )
    # A later anchor where replay TOUCHED prov:5 (its text moved) and it now diverges
    # (penalized) — and the anchor is commensurable (not oracle_suspect), because this
    # divergence is an op-local-convicted genuine wrong-op, not coverage lag.
    diverged = AnchorObservation(
        version_tag="v1",
        amendment_id="act_public_synthetic",
        as_of="2011-01-01",
        struct_sim=0.0,
        n_sections=1,
        n_penalized=1,
        penalized_keys=frozenset({"prov:5"}),
        replay_text={"prov:5": "WRONGLY replayed text of section five"},
        oracle_suspect=None,
        status="OK",
    )
    observations = attribute_divergences("act_public_synthetic", [base, diverged])
    verdicts = {o.section_key: o.verdict for o in observations}
    assert (
        verdicts.get("prov:5") == "candidate_replay_bug_persistent_post_touch"
    ), verdicts
    # And it projects to the billable ``replay_bug`` family via the shared taxonomy.
    residual = observation_to_residual(observations[0])
    assert residual.family == "replay_bug"
    assert residual.jurisdiction == "new_zealand"


def test_nz_coverage_lag_anchor_is_not_billable():
    """A coverage-lag anchor (``oracle_suspect`` set, mirroring the NZ engine's
    per-anchor commensurability witness) types its divergence to a NON-billable
    ``temporal_mismatch`` — the partial-coverage floor never forges a replay bug."""
    base = AnchorObservation(
        version_tag="v0",
        amendment_id="act_public_synthetic",
        as_of="2010-01-01",
        struct_sim=1.0,
        n_sections=1,
        n_penalized=0,
        penalized_keys=frozenset(),
        replay_text={"prov:5": "original text of section five"},
        oracle_suspect=None,
        status="BASE",
    )
    lagging = AnchorObservation(
        version_tag="v1",
        amendment_id="act_public_synthetic",
        as_of="2011-01-01",
        struct_sim=0.0,
        n_sections=1,
        n_penalized=1,
        penalized_keys=frozenset({"prov:5"}),
        replay_text={"prov:5": "stale (skipped-op) text of section five"},
        # The partial-coverage commensurability witness: this anchor's divergence is
        # coverage lag, so the engine stamps oracle_suspect → temporal_mismatch.
        oracle_suspect="nz_partial_coverage_dry_run_commensurability_limited",
        status="OK",
    )
    observations = attribute_divergences("act_public_synthetic", [base, lagging])
    verdicts = {o.section_key: o.verdict for o in observations}
    assert verdicts.get("prov:5") == "temporal_mismatch_commensurability", verdicts
    residual = observation_to_residual(observations[0])
    assert residual.family == "temporal_mismatch"
    assert residual.family not in FAIL_FAMILIES


# ---------------------------------------------------------------------------
# Data-present: the NZ real corpus scores 0-billable + matches the frozen baseline.
# ---------------------------------------------------------------------------


@requires_nz_corpus
def test_nz_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_nz_real_corpus()
    b = score_nz_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_nz_corpus
def test_committed_nz_baseline_matches_real_corpus():
    """The on-disk NZ baseline is not stale: it equals the real NZ corpus's current
    residual set. A move is a preregistered predict-then-compare event."""
    assert _load_nz_baseline() == score_nz_real_corpus(), (
        "Committed NZ CTSF baseline is stale vs the real #205 corpus. Confirm the move "
        "is legitimate, then regenerate the frozen NZ baseline."
    )


@requires_nz_corpus
def test_nz_real_corpus_is_zero_billable():
    """The real NZ corpus scored live carries no billable residual — the honest
    steady state (not just the committed snapshot)."""
    live = score_nz_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_nz_corpus
def test_nz_real_corpus_gate_passes():
    """The NZ corpus scored against its committed baseline PASSes (empty diff)."""
    result = residual_set_diff_gate(score_nz_real_corpus(), _load_nz_baseline())
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"
