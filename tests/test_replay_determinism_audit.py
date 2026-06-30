"""Synthetic tests for F ``REPLAY.NONDETERMINISM`` (replay determinism harness).

Covers the acceptance cases plus the fail-loud input contract:

* a deterministic materialize_fn -> no finding (replay is a pure function);
* an artificially nondeterministic fn (set-ordered / counter output) -> exactly
  one ``REPLAY.NONDETERMINISM`` finding carrying the audited fields (inputs,
  diverging run indices, first-divergence address, run-A vs run-B hashes);
* a frontend whose OWN reported content hash churns while the structure is byte
  stable still drifts (the reported-hash arm);
* ``runs=N`` respected (the harness materializes N times and cross-compares);
* deterministic finding shape / ordering;
* ``runs < 2`` fails loud as ``ReplayDeterminismInputError``;
* a callable that raises propagates (not swallowed into a clean verdict);
* the AST leakage scan flags the four nondeterminism families and is read-only.

These are PURE-CARRIER tests: the materialize callables are hand-built thunks, so
the harness's "deterministic" verdict is grounded in its real ``leaf_hash``
content hash, not a stand-in.
"""

from __future__ import annotations

import itertools

import pytest

from lawvm.core.replay_determinism_audit import (
    REPLAY_NONDETERMINISM,
    LeakageCandidate,
    ReplayDeterminismInputError,
    assert_replay_deterministic,
    scan_source_for_nondeterminism_sources,
)


# --- the harness: deterministic vs nondeterministic materialize_fn ----------


def test_deterministic_fn_no_finding() -> None:
    def materialize() -> dict:
        return {"versions": [{"address": "section:1", "text": "alpha"}], "pit": "2024-01-01"}

    findings = assert_replay_deterministic(materialize, ("482/2024", "2024-01-01"))
    assert findings == ()


def test_deterministic_bytes_output_no_finding() -> None:
    def materialize() -> bytes:
        return b"<statute>byte-identical</statute>"

    assert assert_replay_deterministic(materialize, "S") == ()


def test_deterministic_str_output_no_finding() -> None:
    def materialize() -> str:
        return "stable-string-output"

    assert assert_replay_deterministic(materialize, "S") == ()


def test_nondeterministic_fn_emits_one_finding_with_audited_fields() -> None:
    counter = itertools.count()

    def materialize() -> dict:
        # A hidden-state leak: a per-call counter bleeds into the output.
        return {"versions": [{"address": "section:1", "seq": next(counter)}]}

    findings = assert_replay_deterministic(
        materialize, ("482/2024", "2024-01-01"), source_statute="482/2024"
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == REPLAY_NONDETERMINISM
    assert f.stage == "replay-determinism"
    assert f.source_statute == "482/2024"
    detail = f.detail
    assert detail["reason"] == "replay_output_diverges_across_identical_runs"
    assert detail["owner"] == "replay_determinism_audit"
    assert tuple(detail["inputs"]) == ("482/2024", "2024-01-01")
    assert detail["runs"] == 2
    assert detail["diverging_run_a"] == 0
    assert detail["diverging_run_b"] == 1
    # Fixed-shape evidence: the run-A vs run-B content hashes disagree.
    assert detail["content_hash_a"] != detail["content_hash_b"]
    assert detail["content_hash_a"].startswith("sha256:")
    assert detail["content_hash_b"].startswith("sha256:")
    assert detail["content_hash_diverged"] is True
    # First-divergence address points at the leaking field.
    assert detail["first_divergence_address"] == "versions[0].seq"


def test_set_ordered_output_can_diverge() -> None:
    # Mirror the spec's "returns set-ordered output" nondeterminism: emit a list
    # whose order is drawn from set iteration over distinct unhashable-free keys
    # across calls. We force divergence by alternating element order per call.
    state = {"flip": False}

    def materialize() -> dict:
        state["flip"] = not state["flip"]
        ordered = ["a", "b"] if state["flip"] else ["b", "a"]
        return {"keys": ordered}

    findings = assert_replay_deterministic(materialize, "S")
    assert len(findings) == 1
    assert findings[0].detail["first_divergence_address"] == "keys[0]"


def test_reported_hash_churn_drifts_even_when_structure_stable() -> None:
    # A frontend that returns its own content hash: if that reported hash churns
    # (e.g. recomputed with a wall-clock-tainted profile) the harness flags it
    # even though the structural content hash would match.
    counter = itertools.count()

    def materialize() -> dict:
        return {"versions": ["stable"], "certificate_hash": f"sha256:{next(counter):064x}"}

    findings = assert_replay_deterministic(materialize, "S")
    assert len(findings) == 1
    detail = findings[0].detail
    assert detail["reported_hash_diverged"] is True
    assert detail["reported_hash_a"] != detail["reported_hash_b"]


def test_reported_hash_stable_with_stable_structure_no_finding() -> None:
    def materialize() -> dict:
        return {"versions": ["stable"], "certificate_hash": "sha256:" + "a" * 64}

    assert assert_replay_deterministic(materialize, "S") == ()


def test_runs_n_respected() -> None:
    # Diverge only on the 3rd run: runs=2 misses it, runs=3 catches it.
    counter = itertools.count()

    def materialize() -> dict:
        n = next(counter)
        # runs 0 and 1 identical, run 2 differs.
        return {"v": 0 if n < 2 else 1}

    assert assert_replay_deterministic(materialize, "S", runs=2) == ()
    counter2 = itertools.count()

    def materialize2() -> dict:
        n = next(counter2)
        return {"v": 0 if n < 2 else 1}

    findings = assert_replay_deterministic(materialize2, "S", runs=3)
    assert len(findings) == 1
    assert findings[0].detail["runs"] == 3
    assert findings[0].detail["diverging_run_b"] == 2


def test_finding_detail_is_deterministic_across_invocations() -> None:
    def make_thunk():
        counter = itertools.count()

        def materialize() -> dict:
            return {"seq": next(counter)}

        return materialize

    a = assert_replay_deterministic(make_thunk(), "S")
    b = assert_replay_deterministic(make_thunk(), "S")
    assert len(a) == len(b) == 1
    # Same input shape -> identical content hashes and detail shape.
    assert a[0].detail["content_hash_a"] == b[0].detail["content_hash_a"]
    assert a[0].detail["content_hash_b"] == b[0].detail["content_hash_b"]
    assert a[0].detail["first_divergence_address"] == b[0].detail["first_divergence_address"]


# --- fail-loud input contract ------------------------------------------------


def test_runs_below_two_fails_loud() -> None:
    with pytest.raises(ReplayDeterminismInputError, match="runs >= 2"):
        assert_replay_deterministic(lambda: {}, "S", runs=1)


def test_callable_exception_propagates_not_swallowed() -> None:
    def materialize() -> dict:
        raise RuntimeError("replay crashed")

    with pytest.raises(RuntimeError, match="replay crashed"):
        assert_replay_deterministic(materialize, "S")


# --- leakage scan (AST) ------------------------------------------------------


def test_scan_flags_wall_clock() -> None:
    src = (
        "import datetime\n"
        "def f():\n"
        "    return datetime.now()\n"
    )
    cands = scan_source_for_nondeterminism_sources(src, module_path="m.py")
    assert any(c.kind == "wall_clock" for c in cands)
    wc = next(c for c in cands if c.kind == "wall_clock")
    assert wc.location == "m.py:3:11"


def test_scan_flags_time_calls() -> None:
    src = "import time\ndef f():\n    return time.time()\n"
    cands = scan_source_for_nondeterminism_sources(src, module_path="m.py")
    assert any(c.kind == "wall_clock" for c in cands)


def test_scan_flags_unseeded_random() -> None:
    src = "import random\ndef f():\n    return random.choice([1, 2, 3])\n"
    cands = scan_source_for_nondeterminism_sources(src, module_path="m.py")
    assert any(c.kind == "unseeded_random" for c in cands)


def test_scan_flags_set_iteration_in_for_and_comprehension() -> None:
    src = (
        "def f(items):\n"
        "    out = []\n"
        "    for x in {1, 2, 3}:\n"
        "        out.append(x)\n"
        "    more = [y for y in set(items)]\n"
        "    return out, more\n"
    )
    cands = scan_source_for_nondeterminism_sources(src, module_path="m.py")
    set_hits = [c for c in cands if c.kind == "set_iteration"]
    assert len(set_hits) == 2


def test_scan_clean_module_no_candidates() -> None:
    src = (
        "def f(items):\n"
        "    return sorted(items)\n"
    )
    assert scan_source_for_nondeterminism_sources(src, module_path="m.py") == ()


def test_scan_results_deterministically_ordered() -> None:
    src = (
        "import random\n"
        "import datetime\n"
        "def f():\n"
        "    a = random.random()\n"
        "    b = datetime.now()\n"
        "    return a, b\n"
    )
    cands = scan_source_for_nondeterminism_sources(src, module_path="m.py")
    keys = [(c.lineno, c.col_offset, c.kind) for c in cands]
    assert keys == sorted(keys)


def test_leakage_candidate_location_shape() -> None:
    c = LeakageCandidate(
        kind="wall_clock", module_path="x.py", lineno=7, col_offset=4, snippet="x"
    )
    assert c.location == "x.py:7:4"


def test_scan_bad_source_fails_loud() -> None:
    with pytest.raises(SyntaxError):
        scan_source_for_nondeterminism_sources("def (:\n", module_path="bad.py")
