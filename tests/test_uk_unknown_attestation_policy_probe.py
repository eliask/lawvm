"""§2.9 production-lane guard-liveness for the UK unknown-attestation-policy probe (D12).

The audit (``lawvm.core.evidence_policy.audit_attestation_policy_gap``
— registry row D12 / ``EVID.UNKNOWN_ATTESTATION_POLICY``, AGENTS.md §0/§2.10
firewall: a cited-by-unknown predicate_id is a FORGED policy cite, not a
soft mismatch) had ZERO UK production call sites — the §2.9 worst failure
class. Per memory ``uk_d1_d7_childtail_findings.md``: D12 wire staged as
multi-session via tools/certificate_bundle.py.

The probe at ``lawvm.uk_legislation.unknown_attestation_policy_probe.
probe_uk_unknown_attestation_policy`` is the wire-in; it is invoked from
``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag. UK
has no loaded EvidencePolicyRegistry or collected proof_rows surface today,
so the probe runs the audit with an empty registry + empty proof_rows and
emits nothing in production — a FORWARD-COMPATIBLE NO-OP AUDIT per
audit_impl_D12 spec intent, mirroring D11. The §2.9 fire-drill below drives
a known-violating proof_row (forged authorization_rule_id not in the
registry's known set) directly through the probe to assert the wire is
reachable.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.unknown_attestation_policy_probe import (
    UK_UNKNOWN_ATTESTATION_POLICY_KIND,
    probe_uk_unknown_attestation_policy,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_UNKNOWN_ATTESTATION_POLICY_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_UNKNOWN_ATTESTATION_POLICY_PROBE"

# A "forged" policy_id not in the empty v0 registry's known set — the audit
# must surface this as one AttestationPolicyGap per proof row citing it.
_FORGED_POLICY_ID = "forged:ukpga-2020-1:fake_pred"


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_on_known_violating_proof_row_with_unknown_policy_id() -> None:
    """§2.9 fire-drill — drives a known-violating proof_row directly through
    the probe (a forged authorization_rule_id not in the empty v0 registry's
    known-predicate set) and asserts the corresponding
    ``uk_replay_unknown_attestation_policy_observed`` adjudication fires.
    This proves the wire is reachable from production (§2.9 guard-liveness
    rule); the production no-op case (empty proof_rows) cannot exercise it.
    """
    proof_rows = [
        {"authorization_rule_id": _FORGED_POLICY_ID, "row_id": "row-1"},
    ]
    adjudications: list[CompileAdjudication] = []
    gaps = probe_uk_unknown_attestation_policy(
        adjudications_out=adjudications,
        proof_rows=proof_rows,
        source_statute="ukpga/test/fire/1",
    )
    assert gaps, (
        "expected at least one AttestationPolicyGap for the forged policy "
        "cite passed in proof_rows"
    )
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows, (
        "expected a uk_replay_unknown_attestation_policy_observed "
        "adjudication for the known-violating proof_row, but none fired "
        "through the UK probe — the §2.9 guard is unreachable from UK production"
    )
    detail = rows[0].detail
    assert detail["family"] == "unknown_attestation_policy"
    assert detail["reason_code"] == "forged_policy_cite_observed"
    assert detail["cited_policy_id"] == _FORGED_POLICY_ID
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert rows[0].blocking is False
    assert detail["core_registry_finding_kind"] == "EVID.UNKNOWN_ATTESTATION_POLICY"


def test_probe_emits_nothing_on_empty_proof_rows() -> None:
    """Negative / production-default: empty proof_rows (UK's v0 default —
    no collected surface today) MUST NOT fire — the audit owns the gauged
    dismissal. This is the production no-op cycle."""
    adjudications: list[CompileAdjudication] = []
    gaps = probe_uk_unknown_attestation_policy(
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    assert gaps == ()
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "empty proof_rows must not fire"


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    violating input — production UK bench output stays byte-stable until a
    deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    proof_rows = [
        {"authorization_rule_id": _FORGED_POLICY_ID, "row_id": "row-1"},
    ]
    adjudications: list[CompileAdjudication] = []
    gaps = probe_uk_unknown_attestation_policy(
        adjudications_out=adjudications,
        proof_rows=proof_rows,
        source_statute="ukpga/test/3",
    )
    assert gaps == ()
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "probe must be default-off. Got: {}".format(rows)


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on) with default empty inputs; nothing fires — proving the
    probe is wired into the production fold-exit and runs even when replay
    produces no structural change."""
    from lawvm.core.ir import IRNode, IRStatute
    from lawvm.core.semantic_types import IRNodeKind

    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="attestation/smoke/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
            ),
        ),
        supplements=(),
        metadata={},
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], (
        "default no-op replay should not emit any D12 finding — got: "
        "{}".format(rows)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_unknown_attestation_policy`` is
    invoked on the UK replay fold-exit — i.e. the call site exists, not
    dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.unknown_attestation_policy_probe import" in src
    assert "probe_uk_unknown_attestation_policy(" in src
    assert "probe_uk_unknown_attestation_policy" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
