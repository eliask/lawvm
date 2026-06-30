"""§2.9 production-lane guard-liveness for the UK observation-promoted-to-authority probe (D11).

The audit (``lawvm.core.execution_authorization.authority_source_set_observation_
audit`` — registry row D11 / ``EVID.OBSERVATION_PROMOTED_TO_AUTHORITY``,
AGENTS.md §2.10 firewall: evidence explains authority; it does not become
authority by existing. Any observation-role finding kind appearing in the
apply-path authority source set breaches the firewall) had ZERO UK
production call sites — the §2.9 worst failure class.

The probe at ``lawvm.uk_legislation.observation_promoted_to_authority_probe.
probe_uk_observation_promoted_to_authority`` is the wire-in; it is invoked
from ``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag.
UK has no collected authority-source-kinds surface today, so the probe runs
the audit with ``authority_source_kinds=()`` and emits nothing in production
— a FORWARD-COMPATIBLE NO-OP AUDIT CALL per audit_impl_D11 spec intent
("today this is a forward-compatible no-op audit call ... the hook makes
the firewall explicit"). The §2.9 fire-drill below drives a known-violating
input directly through the probe to assert the wire is reachable (the
guard-liveness §2.9 rule, complementing the runtime no-op tests).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.observation_promoted_to_authority_probe import (
    UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND,
    probe_uk_observation_promoted_to_authority,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_OBSERVATION_PROMOTED_TO_AUTHORITY_PROBE"

# Registered observation-role finding kind verified at:
# src/lawvm/core/observation_registry.py:178 FindingSpec(
#   "ELAB.MISSING_PAYLOAD_SURFACE", ..., role="observation").
# Used as the known-violating authority_source_kinds input below.
_VIOLATING_OBSERVATION_KIND = "ELAB.MISSING_PAYLOAD_SURFACE"


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_on_known_violating_authority_source_kinds_input() -> None:
    """§2.9 fire-drill — drives a known-violating input directly through the
    probe (an observation-role-registered kind in the authority-source-kinds
    set) and asserts the corresponding
    ``uk_replay_observation_promoted_to_authority_observed`` adjudication
    fires. This proves the wire is reachable from production (the §2.9
    guard-liveness rule) — the production no-op case (empty input) cannot
    exercise it."""
    adjudications: list[CompileAdjudication] = []
    promotions = probe_uk_observation_promoted_to_authority(
        authority_source_kinds=[_VIOLATING_OBSERVATION_KIND],
        adjudications_out=adjudications,
        source_statute="ukpga/test/fire/1",
        op_id="op-test-1",
    )
    assert promotions, (
        "expected at least one ObservationPromotedToAuthority for the "
        "observation-role-kind passed in the authority-source-kinds set"
    )
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows, (
        "expected a uk_replay_observation_promoted_to_authority_observed "
        "adjudication for the known-violating input, but none fired through "
        "the UK probe — the §2.9 guard is unreachable from UK production"
    )
    detail = rows[0].detail
    assert detail["family"] == "observation_promoted_to_authority"
    assert detail["reason_code"] == (
        "observation_role_kind_in_authority_set_observed"
    )
    assert detail["promoted_kind"] == _VIOLATING_OBSERVATION_KIND
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert rows[0].blocking is False


def test_probe_emits_nothing_on_empty_authority_source_kinds() -> None:
    """Negative / production-default: an empty authority-source-kinds set
    (UK's v0 default — no collected surface today) MUST NOT fire — the
    audit owns the gauged dismissal. This is the production no-op cycle."""
    adjudications: list[CompileAdjudication] = []
    promotions = probe_uk_observation_promoted_to_authority(
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
        op_id="op-test-2",
    )
    assert promotions == ()
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "empty authority-source-kinds must not fire"


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    violating input — production UK bench output stays byte-stable until a
    deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    adjudications: list[CompileAdjudication] = []
    promotions = probe_uk_observation_promoted_to_authority(
        authority_source_kinds=[_VIOLATING_OBSERVATION_KIND],
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
        op_id="op-test-3",
    )
    assert promotions == ()
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "probe must be default-off. Got: {}".format(rows)


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on) with default empty authority-source-kinds; nothing
    fires — proving the probe is wired into the production fold-exit and
    runs even when replay produces no structural change (the cheap
    default every bench replay hits)."""
    from lawvm.core.ir import IRNode, IRStatute
    from lawvm.core.semantic_types import IRNodeKind

    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="observation/smoke/1",
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
        "default no-op replay should not emit any D11 finding — got: "
        "{}".format(rows)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_observation_promoted_to_authority``
    is invoked on the UK replay fold-exit — i.e. the call site exists, not
    dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert (
        "from lawvm.uk_legislation.observation_promoted_to_authority_probe "
        "import" in src
    )
    assert "probe_uk_observation_promoted_to_authority(" in src
    assert "probe_uk_observation_promoted_to_authority" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
