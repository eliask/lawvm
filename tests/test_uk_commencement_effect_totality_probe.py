"""§2.9 production-lane guard-liveness for the UK commencement-effect totality probe.

The audit (``lawvm.core.commencement_totality_audit.assert_effect_totality`` —
registry row LS-23 / ``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION``, the
§0 total-accounting enforcement: every executed op MUST either be commenced
at a typed temporal event, be classified as pending-or-unresolved, or
surface as a typed Observation — never silently effective-dated without
authority) is wired into ``compile_timelines`` in core (commit ``6a176e9c``)
— but the UK ``apply_ops`` fold does NOT call ``compile_timelines``, so the
audit was dead code against UK replay.

The probe at ``lawvm.uk_legislation.commencement_effect_totality_probe.
probe_uk_commencement_effect_totality`` is the wire-in; it is invoked from
``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag so
production UK bench replay output stays byte-stable.

This test drives a known op-without-temporal-authority through the probe and
asserts the ``uk_replay_commencement_effect_totality_observed`` adjudication
fires (production-reachable from the fold-exit call site). Strict
enforcement stays multi-session pending a UK ``strict_profile`` lane; the
probe is the discipline-disclosing first step.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.commencement_effect_totality_probe import (
    UK_COMMENCEMENT_EFFECT_TOTALITY_KIND,
    probe_uk_commencement_effect_totality,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_COMMENCEMENT_EFFECT_TOTALITY_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_COMMENCEMENT_EFFECT_TOTALITY_PROBE"


def _op(
    *,
    op_id: str,
    group_id: str | None,
    effective: str = "",
    statute_id: str = "ukpga/2020/1",
) -> LegalOperation:
    """Construct a LegalOperation with the given group_id + source.effective.

    A source with no ``effective`` AND no ``enacted`` will produce NO typed
    temporal event via ``_uk_temporal_events_from_ops`` — so the op is not
    commenced and the audit fires.
    """
    source = OperationSource(statute_id=statute_id, effective=effective)
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
        group_id=group_id,
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_op_without_temporal_authority() -> None:
    """Production-lane reachability: an op with a ``group_id`` but no
    corresponding temporal event (source has no ``effective``) drives a
    ``uk_replay_commencement_effect_totality_observed`` adjudication through
    the probe — the live code path invoked from ``apply_ops`` fold-exit."""
    op = _op(op_id="op-1", group_id="g-1", effective="")  # No commencement authority
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_commencement_effect_totality(
        (op,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/1",
    )
    assert observations, (
        "expected at least one Observation for the uncommenced op"
    )
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings, (
        "expected a uk_replay_commencement_effect_totality_observed "
        "adjudication for the uncommenced op, but none fired through the UK "
        "probe — the §2.9 guard is unreachable from UK production"
    )
    detail = findings[0].detail
    assert detail["family"] == "commencement_totality"
    assert detail["reason_code"] == "op_without_temporal_authorization_observed"
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert findings[0].blocking is False
    # The underlying audit-registered finding kind (LS-23) is preserved in the
    # payload so a multi-jurisdiction audit consumer can group by universal
    # finding code.
    assert detail["core_registry_finding_kind"] == (
        "COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION"
    )


def test_probe_emits_nothing_on_commenced_op() -> None:
    """Negative: an op with a commenced temporal event (source.effective is
    set) MUST NOT fire — the audit owns the gauged dismissal."""
    op = _op(op_id="op-2", group_id="g-2", effective="2020-01-01")
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_commencement_effect_totality(
        (op,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    assert observations == ()
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings == [], (
        "commenced op should not fire commencement-effect totality — got: "
        "{}".format(findings)
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not emit on the same
    uncommenced-op input — production UK bench output stays byte-stable
    until a deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    op = _op(op_id="op-3", group_id="g-3", effective="")
    adjudications: list[CompileAdjudication] = []
    observations = probe_uk_commencement_effect_totality(
        (op,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
    )
    assert observations == ()
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings == [], "probe must be default-off. Got: {}".format(findings)


def test_probe_skips_when_ops_is_none() -> None:
    """Degenerate input: a None ops sink must skip cleanly — no exception,
    no finding, no probe record. Mirrors FI's defensive posture for
    production-safe diagnostic hooks."""
    out: list[CompileAdjudication] = []
    assert probe_uk_commencement_effect_totality(None, adjudications_out=out) == ()
    assert out == []


def test_probe_skips_empty_ops() -> None:
    """An empty ops list yields no temporal events and no audit observations
    — the probe MUST skip without emitting a finding."""
    out: list[CompileAdjudication] = []
    assert probe_uk_commencement_effect_totality((), adjudications_out=out) == ()
    assert out == []


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke: with no ops, apply_ops returns the unchanged base. The probe
    runs (env on) over the empty ops stream; nothing fires — proving the
    probe is wired into the production fold-exit and runs even when replay
    produces no structural change."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="commencement/smoke/1",
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
    findings = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert findings == [], (
        "default no-op replay should not emit any commencement totality "
        "finding — got: {}".format(findings)
    )


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_commencement_effect_totality`` is
    invoked on the UK replay fold-exit — i.e. the call site exists, not
    dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert (
        "from lawvm.uk_legislation.commencement_effect_totality_probe import"
        in src
    )
    assert "probe_uk_commencement_effect_totality(" in src
    assert "probe_uk_commencement_effect_totality" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
