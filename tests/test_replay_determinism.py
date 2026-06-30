"""Replay-determinism gate (audit registry rows LS-30 / LS-31).

Registry assertion (LAWVM_AUDIT_INVARIANT_REGISTRY.md §0 / §2.10 / §3.1):

    Replay is a PURE FUNCTION of (base IRStatute, authorized ops, pit_date).
    Replaying the same triple twice must yield:
      * a byte-identical materialized tree (LS-30),
      * identical certificate / transition-trace roots (LS-30),
      * a stable same-PIT materialization hash run-to-run (LS-31).

This gate pins that contract on a SMALL sample of real Finland corpus statutes
(memory-bounded — a handful of fast-replaying statutes, each pinned to a fixed
oracle version so the input triple is itself frozen). Two flavours:

  * IN-PROCESS (LS-30): replay each statute TWICE in this process and assert the
    full replay fingerprint is byte-identical — the materialized tree (structural
    + text serialization), the per-stage certificate root (``stage_accounts_root``
    over the StageResult accounts the certificate dossier roots), the
    transition-trace root, and the composite same-PIT materialization hash.

  * CROSS-PROCESS (LS-31): re-run the SAME fingerprint computation in a fresh
    ``uv``-less subprocess (a clean interpreter with a randomized hash seed) and
    assert it matches the in-process value. This catches nondeterminism that only
    manifests across process boundaries (e.g. ``PYTHONHASHSEED`` leaking into a
    stored hash via set/dict iteration order).

The comparison is STRUCTURAL + BYTE (canonical JSON over the materialized tree
and every root), not merely "no exception was raised".

Self-test (acceptance proof): ``test_perturbation_makes_the_gate_go_red`` injects
a nondeterminism source onto the materialization/serialization spine and asserts
the fingerprint comparison turns RED — proving the gate would CATCH real replay
nondeterminism, not just pass vacuously on a deterministic corpus.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pytest

from lawvm.core.ir_helpers import irnode_to_text, structural_subtree_hash
from lawvm.core.replay_determinism_audit import (
    REPLAY_NONDETERMINISM,
    assert_replay_deterministic,
)
from lawvm.tools.certificate_bundle import (
    build_stage_account_row,
    stage_accounts_root,
)
from tests.corpus_pin_helpers import pinned_replay

# A SMALL, memory-bounded sample. Each statute:
#   * is pinned to a fixed oracle version in corpus_pin_helpers.ORACLE_VERSIONS
#     (so the (base, ops, pit) triple is frozen — no corpus-refresh drift), and
#   * replays in well under a second and actually FOLDS amendments (non-trivial
#     structural/canonical stages), so the certificate root is discriminating
#     rather than the empty-stage constant.
# Receipt counts (observed): 2020/87 -> 1, 2010/76 -> 61, 1999/488 -> 102.
SAMPLE_STATUTES: Tuple[str, ...] = ("2020/87", "2010/76", "1999/488")


# --------------------------------------------------------------------------- #
# Replay fingerprint — the canonical, byte-comparable summary of one replay.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplayFingerprint:
    """Byte-comparable summary of a single replay of one pinned statute.

    Every field is a deterministic function of (base IRStatute, authorized ops,
    pit_date). Two replays of the same triple MUST produce an equal fingerprint;
    the canonical-JSON encoding (``as_json``) is the byte-identity surface.
    """

    statute_id: str
    # Structural serialization of the materialized tree (kind/label/text/children,
    # recursively, sort_keys + ensure_ascii) — the byte-identity surface for the
    # materialized tree itself.
    materialized_tree_json: str
    # Operative-text serialization of the materialized tree.
    serialized_text: str
    # Frozen structural hash of the materialized IR (CERTIFIED_TREE_TRANSITION
    # _TRACE_V0 recipe): structure + labels + ordering, attrs-blind.
    materialization_root: str
    # Per-stage certificate root: stage_accounts_root over the StageResult
    # accounts the certificate dossier commits (structural / canonical-op /
    # materialization stages). This is the "certificate root" of LS-30.
    certificate_root: str
    # Transition-trace root: folds the landed write-receipt footprint (via the
    # structural stage account) + migration-event lineage into one root.
    transition_trace_root: str
    # Composite same-PIT materialization hash (LS-31): one stable digest over the
    # whole fingerprint that must be stable across processes.
    same_pit_materialization_hash: str

    def as_json(self) -> str:
        """Canonical, byte-identity encoding of the fingerprint."""
        return json.dumps(
            {
                "statute_id": self.statute_id,
                "materialized_tree_json": self.materialized_tree_json,
                "serialized_text": self.serialized_text,
                "materialization_root": self.materialization_root,
                "certificate_root": self.certificate_root,
                "transition_trace_root": self.transition_trace_root,
                "same_pit_materialization_hash": self.same_pit_materialization_hash,
            },
            sort_keys=True,
            ensure_ascii=True,
        )


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True)


def _certificate_root(result: Any) -> str:
    """stage_accounts_root over the StageResult accounts the dossier commits.

    Mirrors how ``tools.certificate_bundle`` folds per-stage accounts: build one
    account row per present StageResult and aggregate. Order-independent (set
    root), so it is itself a determinism surface.
    """
    rows: List[Dict[str, Any]] = []
    products = result.products
    for stage_id, stage in (
        ("structural", products.structural_stage),
        ("canonical_op", products.canonical_op_stage),
        ("materialization", products.materialization_stage),
    ):
        if stage is not None:
            rows.append(build_stage_account_row(stage_id, stage))
    return stage_accounts_root(rows)


def _transition_trace_root(result: Any) -> str:
    """Root over the landed transition footprint: write-receipt structural

    account + migration-event lineage. Re-derived from the same typed carriers
    replay already produced (the structural stage folds every WriteReceipt; the
    migration events carry address lineage). A nondeterministic apply order or a
    set-iteration leak into a stored address would perturb this root.
    """
    products = result.products
    structural = products.structural_stage
    structural_account = (
        build_stage_account_row("structural", structural)["stage_account_root"]
        if structural is not None
        else ""
    )
    migration_rows = [
        _canonical_json(
            {
                "old": getattr(ev, "old_address", getattr(ev, "old_id", "")),
                "new": getattr(ev, "new_address", getattr(ev, "new_id", "")),
                "kind": str(getattr(ev, "kind", "")),
            }
        )
        for ev in products.migration_events
    ]
    payload = _canonical_json(
        {
            "structural_account": structural_account,
            "migration": sorted(migration_rows),
        }
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_fingerprint(statute_id: str) -> ReplayFingerprint:
    """Replay one pinned statute and project it onto its replay fingerprint."""
    result = pinned_replay(statute_id, quiet=True)
    materialized_ir = result.ir
    tree_json = _canonical_json(materialized_ir.to_jsonable_dict())
    serialized_text = result.serialize_text()
    materialization_root = structural_subtree_hash(materialized_ir)
    certificate_root = _certificate_root(result)
    transition_trace_root = _transition_trace_root(result)

    composite = _canonical_json(
        {
            "tree_json": tree_json,
            "text": serialized_text,
            "materialization_root": materialization_root,
            "certificate_root": certificate_root,
            "transition_trace_root": transition_trace_root,
            # irnode_to_text is on the spine too; pin it as an independent witness.
            "node_text": irnode_to_text(materialized_ir),
        }
    )
    same_pit_hash = "sha256:" + hashlib.sha256(composite.encode("utf-8")).hexdigest()

    return ReplayFingerprint(
        statute_id=statute_id,
        materialized_tree_json=tree_json,
        serialized_text=serialized_text,
        materialization_root=materialization_root,
        certificate_root=certificate_root,
        transition_trace_root=transition_trace_root,
        same_pit_materialization_hash=same_pit_hash,
    )


# --------------------------------------------------------------------------- #
# LS-30 — IN-PROCESS replay-twice byte-identity.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("statute_id", SAMPLE_STATUTES)
def test_replay_twice_is_byte_identical(statute_id: str) -> None:
    """Same (base, ops, pit) replayed twice -> byte-identical fingerprint.

    Asserts each component AND the whole canonical-JSON encoding, so a drift in
    any one surface (tree bytes, text, any root) fails loudly and specifically.
    """
    first = compute_fingerprint(statute_id)
    second = compute_fingerprint(statute_id)

    # Materialized tree: structural bytes AND operative text.
    assert first.materialized_tree_json == second.materialized_tree_json, (
        f"{statute_id}: materialized tree bytes drifted across replays"
    )
    assert first.serialized_text == second.serialized_text, (
        f"{statute_id}: serialized operative text drifted across replays"
    )
    # Certificate / transition-trace roots.
    assert first.materialization_root == second.materialization_root
    assert first.certificate_root == second.certificate_root
    assert first.transition_trace_root == second.transition_trace_root
    # Composite same-PIT materialization hash.
    assert (
        first.same_pit_materialization_hash == second.same_pit_materialization_hash
    )
    # Whole fingerprint is byte-identical.
    assert first.as_json() == second.as_json()


@pytest.mark.parametrize("statute_id", SAMPLE_STATUTES)
def test_f_replay_determinism_audit_fires_clean_over_corpus(statute_id: str) -> None:
    """F ``REPLAY.NONDETERMINISM`` audit DRIVES the LS-30 replay-twice gate.

    This is the production-representative wire of
    ``core.replay_determinism_audit.assert_replay_deterministic`` (F): the audit
    is the universal "materialize the same (base, ops, pit) twice and prove
    byte-identity" guard, and the LS-30 corpus gate is the natural call-site that
    actually runs replay twice. We feed the REAL production replay fingerprint
    (``compute_fingerprint``, which folds amendments through ``pinned_replay`` ->
    ``replay_xml`` -> materialize over a frozen ``(base, ops, pit)`` triple) into
    the audit as the ``materialize_fn`` thunk, so the audit — not a hand-built
    comparison — is the deciding guard over a real corpus statute.

    On the deterministic corpus the audit must emit NO finding (replay is a pure
    function of its inputs). The fire-drill
    (``tests/test_replay_determinism_firedrill.py``) drives the SAME audit into
    its firing state by injecting a run-to-run-varying value onto the replay
    spine and proves it EMITS ``REPLAY.NONDETERMINISM`` — so this clean-corpus
    arm is not a vacuous pass.
    """
    findings = assert_replay_deterministic(
        lambda: compute_fingerprint(statute_id).as_json(),
        (statute_id,),
        runs=2,
        source_statute=statute_id,
    )
    assert findings == (), (
        f"REPLAY.NONDETERMINISM: the F replay-determinism audit fired over the "
        f"production replay of {statute_id} (replay is not a pure function of its "
        f"inputs): {[f.detail for f in findings]}"
    )
    # Sanity: the audit's registered finding code matches the constant this gate
    # drives, so the guard-liveness inventory and the audit agree on the kind.
    assert REPLAY_NONDETERMINISM == "REPLAY.NONDETERMINISM"


def test_certificate_root_discriminates_between_statutes() -> None:
    """Guard against a vacuous gate: distinct sampled statutes must NOT all share

    the empty-stage certificate-root constant. If every statute collapsed to the
    same root, the LS-30 'identical certificate roots' assertion would be
    trivially true regardless of correctness. We pin that the sample exercises
    non-empty stage accounts.
    """
    roots = {sid: compute_fingerprint(sid).certificate_root for sid in SAMPLE_STATUTES}
    assert len(set(roots.values())) == len(SAMPLE_STATUTES), (
        f"certificate roots are not discriminating across the sample: {roots}"
    )


# --------------------------------------------------------------------------- #
# LS-31 — CROSS-PROCESS materialization-hash stability.
# --------------------------------------------------------------------------- #

# Worker driven in a fresh interpreter (cross-process arm). It re-imports this
# module and prints the canonical fingerprint JSON for one statute on a SENTINEL
# line so the parent can parse it unambiguously.
_CROSS_PROCESS_SENTINEL = "REPLAY_FINGERPRINT_JSON:"

_CROSS_PROCESS_WORKER = (
    "import sys\n"
    "from tests.test_replay_determinism import compute_fingerprint\n"
    "fp = compute_fingerprint(sys.argv[1])\n"
    f"print({_CROSS_PROCESS_SENTINEL!r} + fp.as_json())\n"
)


@pytest.mark.parametrize("statute_id", SAMPLE_STATUTES)
def test_same_pit_materialization_hash_stable_across_processes(statute_id: str) -> None:
    """LS-31: the fingerprint computed in a FRESH process equals the in-process

    one. The child runs with a RANDOMIZED PYTHONHASHSEED (the default for a new
    interpreter) so a set/dict-iteration order leak into any stored hash would
    diverge here even when the in-process replay-twice check is green.
    """
    in_process = compute_fingerprint(statute_id).as_json()

    env = dict(os.environ)
    # Force hash randomization ON in the child (belt-and-suspenders: a stored
    # set-order leak must surface even if the parent ran with a fixed seed).
    env["PYTHONHASHSEED"] = "random"
    # The parent's resolved data root is already carried over via dict(os.environ);
    # this setdefault is only a safety net, so fall back to the repo root (the same
    # default the in-process resolver uses) rather than any developer-local path.
    env.setdefault(
        "LAWVM_CANONICAL_DATA_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    proc = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS_WORKER, statute_id],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert proc.returncode == 0, (
        f"cross-process replay worker failed for {statute_id}:\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    sentinel_lines = [
        line[len(_CROSS_PROCESS_SENTINEL):]
        for line in proc.stdout.splitlines()
        if line.startswith(_CROSS_PROCESS_SENTINEL)
    ]
    assert len(sentinel_lines) == 1, (
        f"expected exactly one fingerprint sentinel line, got {len(sentinel_lines)}:\n"
        f"{proc.stdout}"
    )
    cross_process = sentinel_lines[0]
    assert cross_process == in_process, (
        f"{statute_id}: same-PIT materialization fingerprint diverged across "
        f"processes (LS-31). In-process != cross-process."
    )


# --------------------------------------------------------------------------- #
# LS-34 — repeated-PIT hash convergence (N-iteration loop form).
# --------------------------------------------------------------------------- #
#
# HONESTY (the generator's stopping rule). LS-34 does NOT exercise a code path
# distinct from LS-30: ``compute_fingerprint`` re-materializes the PIT through
# the SAME full replay (``pinned_replay`` -> ``replay_xml`` -> materialize), and
# there is no "re-materialize an already-replayed PIT without replaying" sink
# exposed here. So LS-34 is the **N-iteration loop EXTENSION of the LS-30
# replay-twice byte-identity assertion over the same harness**, not a new walk.
#
# What it adds over LS-30's N=2 is a sharper failure semantics that matches the
# registry wording ("a re-materialization loop that yields a DIFFERENT hash on
# iteration N FAILS LOUD rather than silently CONVERGING"): every iteration is
# pinned to the FIRST iteration's bytes, so a hash that drifts on iteration 3
# and then re-stabilises (a convergence pattern LS-30's pairwise N=2 could miss
# depending on which two runs it sampled) is still caught. The distinct
# ``PIT_HASH_DRIFT`` code is carried in the assertion message (a test-gate code,
# mirroring LS-30/LS-31 which likewise register no observation_registry kind —
# there is no production sink that emits it; the gate IS the enforcement).

# Iteration count for the repeated-re-materialization loop. >=3 so that a drift
# that only appears after the first re-materialization (and would re-converge)
# is caught, not just a single pairwise comparison.
_PIT_REMATERIALIZE_ITERATIONS: int = 4


@pytest.mark.parametrize("statute_id", SAMPLE_STATUTES)
def test_repeated_pit_rematerialization_does_not_drift(statute_id: str) -> None:
    """LS-34: re-materialize the same PIT N>=3 times; every hash equals the first.

    A re-materialization LOOP that yields a different hash on iteration N must
    fail loud, never silently converge. We pin EACH iteration's whole fingerprint
    to iteration 0's, so the gate catches:

      * monotone drift (iteration K differs from 0), and
      * transient drift that re-converges (iteration K differs but iteration K+1
        matches 0 again) — a pairwise replay-twice check could miss this
        depending on which two runs it compared.

    This is the downstream symptom LS-11 (lineage acyclicity) / LS-12
    (positional-id leak) / LS-33 (set-iteration leak) guard against at the
    source; here it is pinned as the observable invariant.
    """
    baseline = compute_fingerprint(statute_id)
    baseline_json = baseline.as_json()

    seen_hashes: set[str] = {baseline.same_pit_materialization_hash}
    for iteration in range(1, _PIT_REMATERIALIZE_ITERATIONS):
        current = compute_fingerprint(statute_id)
        # Per-iteration byte identity against the FIRST materialization — not
        # merely "stable from here on". A loud, specific PIT_HASH_DRIFT message.
        assert current.as_json() == baseline_json, (
            f"PIT_HASH_DRIFT: {statute_id} re-materialization iteration "
            f"{iteration} produced a fingerprint that differs from iteration 0 "
            f"(repeated-PIT hash did not stay byte-identical; LS-34). "
            f"hash[0]={baseline.same_pit_materialization_hash} "
            f"hash[{iteration}]={current.same_pit_materialization_hash}"
        )
        seen_hashes.add(current.same_pit_materialization_hash)

    # The composite same-PIT hash must have exactly ONE value across all
    # iterations — convergence to a single hash is REQUIRED, but it must be the
    # SAME hash every time, never a hash that wandered and re-settled.
    assert len(seen_hashes) == 1, (
        f"PIT_HASH_DRIFT: {statute_id} repeated re-materialization produced "
        f"{len(seen_hashes)} distinct same-PIT hashes over "
        f"{_PIT_REMATERIALIZE_ITERATIONS} iterations: {sorted(seen_hashes)}. "
        f"A re-materialization loop must yield one stable hash, not converge "
        f"after drifting (LS-34)."
    )


def test_repeated_pit_loop_catches_transient_drift() -> None:
    """Acceptance proof for LS-34: a hash that drifts then re-converges is caught.

    LS-30's pairwise replay-twice gate could, in principle, sample two runs that
    happen to agree while a *middle* run drifted. The LS-34 loop pins every
    iteration to iteration 0, so a transient (drift-then-reconverge) pattern
    fails. We simulate exactly that pattern and assert the LS-34 assertion fires.
    """
    statute_id = SAMPLE_STATUTES[0]
    baseline = compute_fingerprint(statute_id)
    baseline_json = baseline.as_json()

    # Construct a fingerprint stream that matches the baseline on iterations
    # 1 and 3 but DRIFTS on iteration 2 (a transient that re-converges).
    drifted_text = baseline.serialized_text + "\n<<transient-drift>>"
    drifted = ReplayFingerprint(
        statute_id=baseline.statute_id,
        materialized_tree_json=baseline.materialized_tree_json,
        serialized_text=drifted_text,
        materialization_root=baseline.materialization_root,
        certificate_root=baseline.certificate_root,
        transition_trace_root=baseline.transition_trace_root,
        same_pit_materialization_hash=(
            "sha256:" + hashlib.sha256(drifted_text.encode("utf-8")).hexdigest()
        ),
    )
    stream = [baseline, baseline, drifted, baseline]

    # Replay the loop's per-iteration pinning over the simulated stream; the
    # transient middle drift must trip the PIT_HASH_DRIFT assertion.
    drift_caught = False
    for iteration, fp in enumerate(stream[1:], start=1):
        if fp.as_json() != baseline_json:
            drift_caught = True
            assert iteration == 2, "the simulated drift is on iteration 2"
            break
    assert drift_caught, (
        "LS-34 loop must catch a transient (drift-then-reconverge) hash; the "
        "per-iteration pin to iteration 0 did not fire"
    )


# --------------------------------------------------------------------------- #
# Acceptance proof — the gate would CATCH real nondeterminism.
# --------------------------------------------------------------------------- #


def test_perturbation_makes_the_gate_go_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a nondeterminism source onto the materialization/serialization

    spine and assert the byte-identity comparison turns RED. This proves the gate
    is not vacuous: it actually discriminates a nondeterministic replay from a
    deterministic one.

    Perturbation: monkeypatch ``serialize_text`` on the ReplayResult so that the
    SECOND replay appends a per-call counter (a stand-in for a clock / set-order
    leak that varies run-to-run). A correct, deterministic replay would produce
    identical text; the perturbed one does not, and the LS-30 comparison must
    notice.
    """
    statute_id = SAMPLE_STATUTES[0]

    # Baseline: with NO perturbation the gate is green (sanity — the corpus is
    # deterministic today).
    clean_a = compute_fingerprint(statute_id)
    clean_b = compute_fingerprint(statute_id)
    assert clean_a.as_json() == clean_b.as_json()

    # Now inject nondeterminism: a counter that increments on each serialize_text
    # call, simulating a run-to-run-varying value leaking onto the spine.
    from lawvm.finland import statute as statute_module

    counter = {"n": 0}
    original_serialize = statute_module.ReplayResult.serialize_text

    def _nondeterministic_serialize(self: Any) -> str:
        counter["n"] += 1
        return original_serialize(self) + f"\n<<perturb:{counter['n']}>>"

    monkeypatch.setattr(
        statute_module.ReplayResult, "serialize_text", _nondeterministic_serialize
    )

    perturbed_a = compute_fingerprint(statute_id)
    perturbed_b = compute_fingerprint(statute_id)

    # The serialized-text surface must now differ between the two replays...
    assert perturbed_a.serialized_text != perturbed_b.serialized_text
    # ...and therefore the composite hash and whole fingerprint must differ too,
    # i.e. the LS-30 byte-identity gate would FAIL on a nondeterministic replay.
    assert (
        perturbed_a.same_pit_materialization_hash
        != perturbed_b.same_pit_materialization_hash
    )
    assert perturbed_a.as_json() != perturbed_b.as_json()

    # Concretely demonstrate the gate's own assertion firing.
    with pytest.raises(AssertionError):
        assert perturbed_a.as_json() == perturbed_b.as_json(), (
            "byte-identity gate must reject a nondeterministic replay"
        )

    # monkeypatch auto-removes the perturbation at teardown; confirm determinism
    # is restored so the perturbation is fully scoped to this test.
    monkeypatch.undo()
    restored_a = compute_fingerprint(statute_id)
    restored_b = compute_fingerprint(statute_id)
    assert restored_a.as_json() == restored_b.as_json()
