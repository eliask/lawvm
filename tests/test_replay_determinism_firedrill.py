"""Fire-drill for F ``REPLAY.NONDETERMINISM`` (guard-liveness, task #104).

A guard-liveness fire-drill drives the DECIDING guard into its firing state from
a production-representative path and proves the guard EMITS its registered
finding — not a hand-built ``Finding``/``Observation``. For F the deciding guard
is ``core.replay_determinism_audit.assert_replay_deterministic``: the universal
"materialize the same ``(base, ops, pit)`` twice and prove byte-identity" audit.

The audit's natural production call-site is the LS-30 replay-twice corpus gate
(``tests/test_replay_determinism.py``), which actually runs the production replay
twice over a frozen corpus triple. The clean-corpus arm there
(``test_f_replay_determinism_audit_fires_clean_over_corpus``) DRIVES the audit
and asserts it stays silent on the deterministic corpus. This module is the
firing-state half of that liveness contract:

  * RED ARM (production-representative): inject a run-to-run-varying value onto
    the REAL replay/serialization spine (a stand-in for a clock / set-order /
    unseeded-random leak) and feed the production fingerprint thunk into the
    audit. The audit must EMIT exactly one ``REPLAY.NONDETERMINISM`` finding
    carrying the fixed-shape evidence (first-divergence address, run-A vs run-B
    content hashes, run indices). This proves the LS-30 wire is live: if the
    audit were silently disconnected, this drill goes red.

  * REPORTED-HASH ARM: a frontend whose structure is byte-stable but whose OWN
    reported content hash churns run-to-run still drifts — the audit asserts the
    self-reported hash too, not only its own content hash.

The clean-corpus pass lives next to the production gate; here we prove the guard
genuinely fires, closing the guard-liveness loop (NO_FIRE_DRILL_YET -> drilled).
"""
from __future__ import annotations

import itertools
from typing import Any

import pytest

from lawvm.core.replay_determinism_audit import (
    REPLAY_NONDETERMINISM,
    assert_replay_deterministic,
)

from tests.test_replay_determinism import SAMPLE_STATUTES, compute_fingerprint


def test_replay_determinism_audit_emits_finding_on_perturbed_production_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The F audit FIRES from the production replay spine when it leaks state.

    Production-representative: we drive the REAL ``compute_fingerprint`` thunk
    (``pinned_replay`` -> ``replay_xml`` -> materialize over a frozen corpus
    triple) but monkeypatch ``ReplayResult.serialize_text`` to append a per-call
    counter — exactly the run-to-run-varying value a clock / set-order / unseeded
    random leak would inject onto the materialization/serialization spine. The
    deciding guard (``assert_replay_deterministic``), fed the production thunk,
    must EMIT one ``REPLAY.NONDETERMINISM`` finding.
    """
    statute_id = SAMPLE_STATUTES[0]

    # Sanity: with NO perturbation the audit is silent over the real replay (the
    # corpus is deterministic), so the finding below is caused by the injected
    # leak, not a flaky corpus.
    clean = assert_replay_deterministic(
        lambda: compute_fingerprint(statute_id).as_json(),
        (statute_id,),
        source_statute=statute_id,
    )
    assert clean == (), (
        "baseline production replay is not deterministic; cannot attribute the "
        f"drill's finding to the injected leak: {[f.detail for f in clean]}"
    )

    # Inject nondeterminism onto the real replay spine: a counter that increments
    # on every serialize_text call, so the two replays the audit drives diverge.
    from lawvm.finland import statute as statute_module

    counter = itertools.count(1)
    original_serialize = statute_module.ReplayResult.serialize_text

    def _nondeterministic_serialize(self: Any) -> str:
        return original_serialize(self) + f"\n<<perturb:{next(counter)}>>"

    monkeypatch.setattr(
        statute_module.ReplayResult, "serialize_text", _nondeterministic_serialize
    )

    findings = assert_replay_deterministic(
        lambda: compute_fingerprint(statute_id).as_json(),
        (statute_id,),
        source_statute=statute_id,
    )

    assert len(findings) == 1, (
        "the F replay-determinism audit must emit exactly one finding when the "
        f"production replay spine leaks a run-to-run-varying value; got "
        f"{len(findings)}"
    )
    finding = findings[0]
    assert finding.kind == REPLAY_NONDETERMINISM
    assert finding.source_statute == statute_id
    detail = finding.detail
    # Fixed-shape evidence a triager follows.
    assert detail["content_hash_diverged"] is True
    assert detail["content_hash_a"] != detail["content_hash_b"]
    assert detail["diverging_run_a"] == 0
    assert detail["diverging_run_b"] == 1
    assert detail["inputs"] == (statute_id,)
    assert detail["reason"] == "replay_output_diverges_across_identical_runs"

    # The perturbation is fully scoped: after teardown the audit is silent again.
    monkeypatch.undo()
    restored = assert_replay_deterministic(
        lambda: compute_fingerprint(statute_id).as_json(),
        (statute_id,),
        source_statute=statute_id,
    )
    assert restored == (), (
        "determinism not restored after undoing the perturbation; the leak "
        "escaped the drill's scope"
    )


def test_replay_determinism_audit_emits_on_reported_hash_churn() -> None:
    """The F audit FIRES when a frontend's OWN reported content hash churns.

    A materialize callable whose STRUCTURE is byte-stable but whose self-reported
    ``content_hash`` member changes run-to-run still drifts: the audit asserts the
    reported hash too. This drives the reported-hash arm of the deciding guard,
    proving it is not satisfied by structural byte-identity alone.
    """
    reported = itertools.count(1)

    def materialize() -> dict:
        # Byte-stable structure, but a self-reported content hash that churns —
        # the frontend's own certificate hash is leaking hidden state even though
        # the visible tree is stable.
        return {
            "versions": [{"address": "section:1", "text": "alpha"}],
            "content_hash": f"sha256:{next(reported):064d}",
        }

    findings = assert_replay_deterministic(
        materialize, ("synthetic/reported-hash-churn",), source_statute="synthetic/1"
    )
    assert len(findings) == 1
    detail = findings[0].detail
    assert detail["reported_hash_diverged"] is True
    assert detail["reported_hash_a"] != detail["reported_hash_b"]
