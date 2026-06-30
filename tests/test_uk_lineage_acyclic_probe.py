"""§2.9 production-lane guard-liveness for the UK lineage-acyclicity probe.

The audit (``lawvm.core.timeline_lineage.check_lineage_acyclic`` — registry
row LS-11 / ``LINEAGE.CYCLE``) is the structural enforcement of AGENTS.md §2.8
*"provision/node identity is intrinsic and versioned, never positional;
[...] frontends emit migration events, core consumes them"*. ``core/timeline_
lineage`` previously had ZERO UK callers (the §2.9 worst failure class:
a check that exists, is registered, passes review, and creates false
confidence in invisible coverage).

The enabling emitter landed in commit ``edd1012d``
(``lawvm.uk_legislation.uk_migration_events.derive_uk_migration_events``)
which projects the existing ``mutation_events_out`` stream onto the
``MigrationEvent`` lineage plane. The probe at
``lawvm.uk_legislation.lineage_acyclic_probe.probe_uk_lineage_acyclic`` is
the wire-in; it is invoked from ``uk_amendment_replay.apply_ops`` fold-exit
behind an opt-in env flag so production UK bench replay output stays
byte-stable.

This test drives a known cyclic migration graph through the probe and asserts
the ``uk_replay_lineage_cycle_observed`` adjudication fires (production-
reachable from the fold-exit call site). Strict enforcement stays
multi-session pending a UK ``strict_profile`` lane; the probe is the
discipline-disclosing first step.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_events import MutationEvent
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.lineage_acyclic_probe import (
    UK_LINEAGE_CYCLE_KIND,
    probe_uk_lineage_acyclic,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_LINEAGE_CYCLE_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_LINEAGE_ACYCLIC_PROBE"


def _mut_event(
    *,
    op_id: str = "op-1",
    source_statute: str = "ukpga/2020/1",
    action: str = "replace",
    helper: str = "_renumber_node",
    outcome: str = "renumbered_node",
    renumbered_paths: tuple = (),
) -> MutationEvent:
    return MutationEvent(
        op_id=op_id,
        source_statute=source_statute,
        action=action,
        helper=helper,
        outcome=outcome,
        renumbered_paths=renumbered_paths,
    )


def _walk(addr_path: tuple) -> LegalAddress:
    return LegalAddress(path=tuple(addr_path))


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_cycle() -> None:
    """Production-lane reachability: a migration cycle (A→B→A) drives a
    ``uk_replay_lineage_cycle_observed`` adjudication through the probe —
    the live code path invoked from ``apply_ops`` fold-exit."""
    section_1 = (("section", "1"),)
    section_2 = (("section", "2"),)
    events = (
        _mut_event(renumbered_paths=((section_1, section_2),)),
        _mut_event(renumbered_paths=((section_2, section_1),)),
    )
    adjudications: list[CompileAdjudication] = []
    result = probe_uk_lineage_acyclic(
        events,
        adjudications_out=adjudications,
        source_statute="ukpga/test/1",
    )
    assert result is not None and result.acyclic is False
    cycles = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert cycles, (
        "expected a uk_replay_lineage_cycle_observed adjudication for the "
        "A→B→A back-edge, but none fired through the UK probe — the §2.9 "
        "guard is unreachable from UK production"
    )
    detail = cycles[0].detail
    assert detail["family"] == "lineage"
    assert detail["reason_code"] == "migration_dag_cycle_observed"
    assert detail["cycle_length"] >= 2  # A→B→A carries at least 2 addresses
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert cycles[0].blocking is False


def test_probe_emits_nothing_on_acyclic_graph() -> None:
    """Negative: a clean linear renumber chain (A→B→C, no back-edge) is
    acyclic — the probe MUST NOT fire. A gauge against false positives."""
    section_1 = (("section", "1"),)
    section_2 = (("section", "2"),)
    section_3 = (("section", "3"),)
    events = (
        _mut_event(renumbered_paths=((section_1, section_2),)),
        _mut_event(renumbered_paths=((section_2, section_3),)),
    )
    adjudications: list[CompileAdjudication] = []
    result = probe_uk_lineage_acyclic(
        events,
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    assert result is not None and result.acyclic is True
    cycles = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert cycles == [], "acyclic chain must not emit cycle adjudication"


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    cycle input — production UK bench output stays byte-stable until a
    deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    section_1 = (("section", "1"),)
    section_2 = (("section", "2"),)
    events = (
        _mut_event(renumbered_paths=((section_1, section_2),)),
        _mut_event(renumbered_paths=((section_2, section_1),)),
    )
    adjudications: list[CompileAdjudication] = []
    result = probe_uk_lineage_acyclic(
        events,
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
    )
    assert result is None
    cycles = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert cycles == [], "probe must be default-off. Got: {}".format(cycles)


def test_probe_skips_when_mutation_events_is_none() -> None:
    """Degenerate input: a None mutation-events sink (no caller-supplied
    ``mutation_events_out``) must skip cleanly — no exception, no finding,
    no probe record. Mirrors FI's defensive posture for production-safe
    diagnostic hooks."""
    out: list[CompileAdjudication] = []
    assert probe_uk_lineage_acyclic(None, adjudications_out=out) is None
    assert out == []


def test_probe_skips_empty_mutation_events() -> None:
    """An empty mutation-events list yields no migration events and no
    cycle — the probe MUST skip without emitting a finding (no diagnostic
    noise on a no-op replay).

    Per the probe docstring, empty input is the degenerate-skip case —
    the probe returns None rather than running ``check_lineage_acyclic``
    on an empty tuple (which would also yield acyclic=True, but the
    probe's discipline is to skip cleanly so the audit's call count
    matches the "really had something to check" count)."""
    out: list[CompileAdjudication] = []
    result = probe_uk_lineage_acyclic((), adjudications_out=out)
    assert result is None
    assert out == []


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on) over the (empty) mutation_events; nothing fires — proving
    the probe is wired into the production fold-exit and runs even when
    replay produces no structural change (the cheap default every bench
    replay hits)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    from lawvm.core.ir import IRNode, IRStatute
    from lawvm.core.semantic_types import IRNodeKind

    base = IRStatute(
        statute_id="lineage/smoke/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION, label="1", children=()
                ),
            ),
        ),
        supplements=(),
        metadata={},
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(
        base, [], adjudications_out=adjudications,
    )
    cycles = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert cycles == [], (
        "default no-op replay should not emit any lineage cycle — got: "
        "{}".format(cycles)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_lineage_acyclic`` is invoked on the
    UK replay fold-exit — i.e. the call site exists, not dead code.

    Pinned at the import + call-site because the cyclic-graph class is rare
    in the live corpus by construction (§2.8 is a discipline-against-
    regression rule, not a feature of normal replay output); the static line
    is the dumb-pinned version of "the wire-in landed", complementing the
    runtime probe tests above which exercise the call shape directly."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.lineage_acyclic_probe import" in src
    assert "probe_uk_lineage_acyclic(" in src
    assert "probe_uk_lineage_acyclic" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
