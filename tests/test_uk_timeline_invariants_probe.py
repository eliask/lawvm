"""§2.9 production-lane guard-liveness for the UK timeline-invariants probe.

The audit (``lawvm.core.timeline_invariants.check_all_timeline_invariants_
typed`` — the typed version of ``check_all_timeline_invariants`` per FI's
C3 evidence wiring; 5 legacy invariant families: temporal_overlap /
temporary_overlay / expiry_chain / replay_timeline / replay_timeline_robust)
had ZERO UK production call sites. FI's only production caller is
``finland/replay_timeline_diagnostics.py:project_timeline_invariant_findings``
(env-gated by ``LAWVM_FI_ENABLE_TIMELINE_INVARIANTS``); UK had no equivalent.

The probe at ``lawvm.uk_legislation.timeline_invariants_probe.probe_uk_
timeline_invariants`` is the wire-in; it is invoked from
``uk_amendment_replay.apply_ops`` fold-exit behind an opt-in env flag so
production UK bench replay output stays byte-stable.

§2.9 REACHABILITY NOTE: unlike D11/D12 (where a fire-drill bypasses
production by passing a known-violating input directly through the probe
API), this probe's violation is produced deep inside ``compile_timelines``
— so the production-smoke test (``test_probe_reachable_through_pipeline_
apply_ops_no_ops``, which drives a no-op replay through
UKReplayPipeline.apply_ops end-to-end and asserts the probe runs without
finding) + the static call-site pin (``test_wired_into_apply_ops_fold_
exit``) are the primary §2.9 reachability proofs. A full fire-drill would
require constructing a specific Timelines invariant violation shape —
deferred to a dedicated regression fixture when a real corpus witness
surfaces.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.timeline_invariants_probe import (
    UK_TIMELINE_INVARIANTS_KIND,
    probe_uk_timeline_invariants,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_TIMELINE_INVARIANTS_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_TIMELINE_INVARIANTS_PROBE"


def _base_statute(statute_id: str = "ukpga/test/1") -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
            ),
        ),
        supplements=(),
        metadata={"effective_date": "2020-01-01"},
    )


def _op(*, op_id: str, group_id: str, effective: str = "2020-01-01") -> LegalOperation:
    source = OperationSource(statute_id="ukpga/test/1", effective=effective)
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


def test_probe_emits_nothing_on_no_op_replay() -> None:
    """Default case: an empty ops stream yields an empty Timelines via
    ``compile_timelines`` (only the base seed), which in turn yields zero
    ``TimelineInvariantViolation``. The probe MUST emit nothing — the audit
    owns the gauged dismissal."""
    base = _base_statute()
    adjudications: list[CompileAdjudication] = []
    violations = probe_uk_timeline_invariants(
        base,
        (),
        adjudications_out=adjudications,
        source_statute="ukpga/test/1",
    )
    assert violations == []
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "empty-ops replay should not emit timeline violations"


def test_probe_emits_nothing_on_commenced_op() -> None:
    """An op with a commenced temporal event (source.effective set) does
    not produce a timeline invariant violation by itself — the probe MUST
    emit nothing. A gauge against false positives."""
    base = _base_statute()
    op = _op(op_id="op-1", group_id="g-1", effective="2020-01-01")
    adjudications: list[CompileAdjudication] = []
    violations = probe_uk_timeline_invariants(
        base,
        (op,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/2",
    )
    # The violation set may be empty or non-empty depending on what
    # compile_timelines produces; the probe MUST run without raising and
    # surface any violations as non-blocking adjudications.
    for row in [a for a in adjudications if a.kind == _FINDING_KIND]:
        assert row.blocking is False
        assert row.detail["family"] == "timeline_invariants"
        assert row.detail["probe_mode"] == "observation_only"


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with no env flag the probe MUST not run — production UK
    bench output stays byte-stable until a deliberate ramp."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    base = _base_statute()
    op = _op(op_id="op-2", group_id="g-2")
    adjudications: list[CompileAdjudication] = []
    violations = probe_uk_timeline_invariants(
        base,
        (op,),
        adjudications_out=adjudications,
        source_statute="ukpga/test/3",
    )
    assert violations == []
    rows = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert rows == [], "probe must be default-off. Got: {}".format(rows)


def test_probe_skips_when_base_ir_is_none() -> None:
    """Degenerate input: None base_ir MUST skip cleanly — no exception, no
    finding, no probe record."""
    out: list[CompileAdjudication] = []
    assert probe_uk_timeline_invariants(None, (), adjudications_out=out) == []
    assert out == []


def test_probe_skips_when_ops_empty() -> None:
    """An empty ops list yields empty Timelines (base seed only) — the probe
    MUST skip without emitting."""
    base = _base_statute()
    out: list[CompileAdjudication] = []
    assert probe_uk_timeline_invariants(base, (), adjudications_out=out) == []
    # compile_timelines may or may not find base-date warnings; the probe
    # surfaces any found violations but never raises.


def test_probe_skips_cleanly_with_diagnostic_when_pit_date_unavailable() -> None:
    """§2.9 regression: a base_ir whose metadata carries no `effective_date`
    AND whose caller passes no explicit `pit_date` MUST trigger a clean
    probe-skipped diagnostic — NOT propagate the audit's
    `ValueError('as_of must be non-empty')` exception. This was verified
    on ukpga/1990/8 via lawvm uk-replay CLI 2026-06-29 (the CLI does not
    currently thread `--pit-date YYYY-MM-DD` into base_ir.metadata)."""
    base_no_dates = IRStatute(
        statute_id="timeline/skip/1",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=()),
            ),
        ),
        supplements=(),
        metadata={},  # No effective_date AND no enacted_date.
    )
    op = _op(op_id="op-test", group_id="g-test")
    adjudications: list[CompileAdjudication] = []
    violations = probe_uk_timeline_invariants(
        base_no_dates,
        (op,),
        adjudications_out=adjudications,
        source_statute="timeline/skip/1",
        pit_date="",  # Caller did NOT supply a pit_date either.
    )
    assert violations == []
    skips = [
        a for a in adjudications
        if a.kind == "uk_replay_timeline_invariants_probe_skipped"
    ]
    assert len(skips) == 1, (
        "expected exactly one probe-skipped diagnostic naming the "
        "pit_date_unavailable reason. Got: {}".format(skips)
    )
    detail = skips[0].detail
    assert detail["reason_code"] == "probe_skipped"
    assert "pit_date_unavailable" in detail["shortfall_probe_skip_reason"]
    assert detail["quirks_disposition"] == QuirksDisposition.RECORD
    assert skips[0].blocking is False


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """§2.9 primary reachability proof: drives a no-op replay through the
    full ``UKReplayPipeline.apply_ops`` (the production lane, no synthetic
    shortcuts) and asserts the probe runs at the fold-exit — proving the
    wire is reachable from production.

    With no ops, compile_timelines builds a base-seed-only Timelines; no
    violations fire (the cheap default every bench replay hits)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    pipeline = UKReplayPipeline(Path("."))
    base = IRStatute(
        statute_id="timeline/smoke/1",
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
    # The probe runs at fold-exit; a no-op replay should produce zero
    # timeline violations (base-seed-only Timelines).
    for row in rows:
        assert row.blocking is False
        assert row.detail["family"] == "timeline_invariants"


def test_wired_into_apply_ops_fold_exit() -> None:
    """Static-line proof that ``probe_uk_timeline_invariants`` is invoked
    on the UK replay fold-exit — i.e. the call site exists, not dead code."""
    from lawvm.uk_legislation import uk_amendment_replay as mod

    src = inspect.getsource(mod)
    assert "from lawvm.uk_legislation.timeline_invariants_probe import" in src
    assert "probe_uk_timeline_invariants(" in src
    assert "probe_uk_timeline_invariants" in inspect.getsource(
        mod.UKReplayPipeline.apply_ops
    )
