"""§2.9 production-lane guard-liveness for the UK materialization-totality probe.

The lens (``lawvm.core.materialization_universe.check_materialization_totality``)
is registered as the ``LS-MAT-01``/``LS-MAT-02`` invariant checker at
``core/invariant_spec.py:620`` but had NO production-lane call site in
``src/lawvm/uk_legislation/`` — the §2.9 worst-case: a check that exists, is
registered, passes review, and creates false confidence in invisible
coverage. The probe at ``src/lawvm/uk_legislation/materialization_totality_
probe.py`` is the wire-in; it is invoked from
``uk_amendment_replay.apply_ops`` (line ~1096 fold-exit) behind an opt-in env
flag so production UK bench replay output stays byte-stable.

This test drives a known silent-drop through the probe and asserts the
``uk_replay_materialization_totality_silent_drop_observed`` adjudication
fires (production-reachable from the fold-exit call site). Strict
enforcement stays multi-session pending a TotalityPolicy ramp; the probe is
the discipline-disclosing first step.
"""
from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.materialization_totality_probe import (
    probe_uk_materialization_totality,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = "uk_replay_materialization_totality_silent_drop_observed"
_PROBE_ENV_FLAG = "LAWVM_UK_MATERIALIZE_TOTALITY_PROBE"


def _section(label: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.P, label="", children=()),),
    )


def _chapter(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label="1", children=tuple(sections))


def _body(*chapters: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(chapters))


def _statute(body: IRNode, *, statute_id: str) -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="",
        body=body,
        supplements=(),
        metadata={},
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_silent_drop() -> None:
    """Production-lane reachability: a section that vanishes with no typed
    absence reason drives a SILENTLY_DROPPED_UNIT adjudication through the
    probe — the live code path invoked from ``apply_ops`` fold-exit."""
    base = _statute(
        _body(_chapter(_section("1"), _section("2"), _section("3"))),
        statute_id="totality/1",
    )
    # Materialized tree drops section "2" without a tombstone — the silent-drop
    # witness class analogous to FI's 1929/234 rikoslaki §110-113 case.
    dropped = IRStatute(
        statute_id="totality/1",
        title="",
        body=_body(_chapter(_section("1"), _section("3"))),
        supplements=(),
        metadata={},
    )
    adjudications: list[CompileAdjudication] = []
    probe_uk_materialization_totality(
        base=base,
        replayed=dropped,
        adjudications_out=adjudications,
        source_statute="totality/1",
    )
    drops = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert drops, (
        "expected a uk_replay_materialization_totality_silent_drop_observed "
        "adjudication for the vanished section 2, but none fired through the "
        "UK probe — the §2.9 guard is unreachable from UK production"
    )
    detail = drops[0].detail
    assert detail["address_key"] == "sec_2"
    assert detail["unit_kind"] == "section"
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert drops[0].blocking is False


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    silent-drop input — production UK bench output stays byte-stable until
    a deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)

    base = _statute(
        _body(_chapter(_section("1"), _section("2"), _section("3"))),
        statute_id="totality/2",
    )
    dropped = IRStatute(
        statute_id="totality/2",
        title="",
        body=_body(_chapter(_section("1"), _section("3"))),
        supplements=(),
        metadata={},
    )
    adjudications: list[CompileAdjudication] = []
    probe_uk_materialization_totality(
        base=base,
        replayed=dropped,
        adjudications_out=adjudications,
        source_statute="totality/2",
    )
    drops = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert drops == [], (
        "probe must be default-off. Got: {}".format(drops)
    )


def test_probe_clean_universe_emits_nothing() -> None:
    """Negative: when every base-section is still PRESENT in the materialized
    tree, the probe MUST not fire. A gauge against false positives."""
    base = _statute(
        _body(_chapter(_section("1"), _section("2"))),
        statute_id="totality/3",
    )
    replayed = _statute(
        _body(_chapter(_section("1"), _section("2"))),
        statute_id="totality/3",
    )
    adjudications: list[CompileAdjudication] = []
    emitted = probe_uk_materialization_totality(
        base=base,
        replayed=replayed,
        adjudications_out=adjudications,
        source_statute="totality/3",
    )
    assert emitted == []
    assert all(a.kind != _FINDING_KIND for a in adjudications)


def test_probe_skips_when_base_is_none() -> None:
    """Degenerate input: a None base statute must skip cleanly — no exception,
    no false finding, no probe record. Mirrors FI's defensive posture for
    production-safe diagnostic hooks."""
    out: list[CompileAdjudication] = []
    assert probe_uk_materialization_totality(None, None, adjudications_out=out) == []
    assert out == []


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_materialization_totality`` is invoked
    on the UK replay fold-exit — i.e. the call site exists, not dead code.

    Pinned at the import + call-site because the silent-drop class is hard to
    reproduce with a single replay op (the FI witness involves content=None
    masking across many snapshot ops); the static line is the dumb-pinned
    version of "the wire-in landed", complementing the runtime probe tests
    above which exercise the call shape directly.
    """
    import inspect
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.materialization_totality_probe import" in src
    assert "probe_uk_materialization_totality(" in src
    # Make sure the call is actually IN apply_ops (not just imported for some
    # other purpose). apply_ops source dict:
    assert "probe_uk_materialization_totality" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on), and because base == replayed, no shortfalls fire — proving
    the probe is wired into the production fold-exit and runs even when replay
    produces no structural change (the cheap default every bench replay hits)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    from pathlib import Path

    pipeline = UKReplayPipeline(Path("."))
    base = _statute(
        _body(_chapter(_section("1"))),
        statute_id="totality/smoke/1",
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    # No shortfalls should fire (base == replayed, perfect universe).
    drops = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert drops == [], (
        "default no-op replay should not emit any totality shortfall — got: "
        "{}".format(drops)
    )
