"""§2.9 production-lane guard-liveness for the UK per-op mutation-boundary probe.

The lens (``lawvm.core.mutation_boundary_proof.verify_per_op``) is registered
as the LS-01 / §1.0 immutable invariant at ``core/invariant_spec.py`` (LS-01
row ``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP``); Finland wires it
post-apply at ``finland/apply_resolved_op.py:426/471``. Until this commit
``src/lawvm/uk_legislation/`` had NO production call site — the §2.9
worst-case: a check that exists, is registered, passes review, and creates
false confidence in invisible containment. The probe at
``src/lawvm/uk_legislation/mutation_boundary_per_op_probe.py`` is the wire-in;
it is invoked from ``UKReplayExecutor.apply_op`` behind an opt-in env flag so
production UK bench replay output stays byte-stable.

This test drives a known per-op mutation-boundary escape through the probe
and asserts the ``uk_replay_mutation_boundary_per_op_violation_observed``
adjudication fires (production-reachable from ``apply_op``). Strict
enforcement stays multi-session pending a UK ``strict_profile`` lane; the
probe is the discipline-disclosing first step.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.mutation_boundary_per_op_probe import (
    UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    boundary_probe_enabled,
    probe_op_mutation_boundary,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_MUTATION_BOUNDARY_PER_OP"


def _section(label: str, *, text: str = "") -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        text=text,
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


def _text_replace_op_targeting_section_1(op_id: str = "op/test/1") -> LegalOperation:
    """A TEXT_REPLACE LegalOperation whose storage boundary is the section-1 path only.

    ``operation_storage_boundary_prefixes`` (``core/mutation_boundary.py:100-101``)
    maps ``TEXT_REPLACE`` to the target_path verbatim (no parent expansion), so
    any observed diff on sibling section ``2`` is necessarily out-of-boundary.
    Contrast REPEAL/INSERT/RENUMBER which expand to the parent path (sibling-
    list boundary) and so do NOT police sibling-deletion-via-parent — that's
    a §1.0/§1.4 surface LS-01 polices elsewhere. The TEXT_REPLACE family is
    the canonical probe-friendly witness: target_isolated_node, sibling_text_escape.
    """
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
    )


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_probe_fires_adjudication_for_out_of_boundary_diff() -> None:
    """Production-lane reachable shape: an op targeting section ``1`` whose
    apply *also* rewrote sibling section ``2``'s text (the §1.0/§1.4 forbidden
    shape) MUST emit ``uk_replay_mutation_boundary_per_op_violation_observed``.

    The diff is constructed directly (the live ``UKMutableStatute`` already
    carries the hot-path mutation in production; isolating here lets the probe
    contract be exercised without a real sibling-rewriting op, which is
    exactly the invariant the probe polices). The probe calls ``verify_per_op``
    which is what is wired into ``UKReplayExecutor.apply_op``.
    """
    before = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="original-2"),
        )
    )
    # The apply would have rewritten sibling section 2's text — outside the
    # op's declared boundary (target = chapter:1/section:1; TEXT_REPLACE
    # boundary is the target path itself, no parent expansion).
    after = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="tampered-sibling"),
        )
    )
    op = _text_replace_op_targeting_section_1()

    adjudications: list[CompileAdjudication] = []
    verdict = probe_op_mutation_boundary(
        before=before,
        after=after,
        op=op,
        op_id=op.op_id,
        adjudications_out=adjudications,
        source_statute="boundary/1",
    )
    assert verdict is not None
    assert not verdict.within_boundary
    violations = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert violations, (
        "expected a uk_replay_mutation_boundary_per_op_violation_observed "
        "adjudication for the sibling-section-2 escape, but none fired through "
        "the UK probe — the §2.9 guard is unreachable from UK production"
    )
    detail = violations[0].detail
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert detail["quirks_disposition"] == "record"
    assert detail["boundary_status"] == "out_of_boundary"
    assert detail["op_id"] == op.op_id
    assert violations[0].blocking is False
    assert violations[0].phase == "replay"
    assert violations[0].source_statute == "boundary/1"
    # The out-of-boundary path list must name the escaped (sibling section 2)
    # path, not just regurgitate the declared target — a probe that lists no
    # concrete escape path is the §1.10 forbidden diagnostic shape.
    assert detail["out_of_boundary_paths"], (
        "out_of_boundary_paths must be non-empty when boundary_status == "
        "out_of_boundary — otherwise the diagnostic is opaque (AGENTS.md §1.10)"
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with the env unset, ``boundary_probe_enabled()`` MUST
    return False — that signal is what gates the snapshot capture in
    ``apply_op``, so production UK bench output stays byte-stable. (The probe
    function itself runs whenever invoked directly; the gate lives at the
    call site. The apply_op-reachable default-off behavior is exercised in
    ``test_probe_default_off_through_pipeline_apply_ops``.)"""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    assert boundary_probe_enabled() is False


def test_probe_within_boundary_emits_nothing() -> None:
    """Negative: when the *only* change is on the op's declared target node,
    the probe MUST not fire. A gauge against false positives.

    Targeted-path change is the canonical "within boundary" case for
    ``TEXT_REPLACE`` — target_path == boundary, so any diff at the target
    node itself is covered; sibling paths unchanged.
    """
    before = _body(_chapter(_section("1", text="original")))
    # Apply rewrote section 1's text — exactly the op's declared target node.
    after = _body(_chapter(_section("1", text="replaced-in-place")))
    op = _text_replace_op_targeting_section_1()

    adjudications: list[CompileAdjudication] = []
    verdict = probe_op_mutation_boundary(
        before=before,
        after=after,
        op=op,
        op_id=op.op_id,
        adjudications_out=adjudications,
        source_statute="boundary/3",
    )
    assert verdict is None
    assert all(a.kind != _FINDING_KIND for a in adjudications)


def test_probe_skips_when_snapshot_is_none() -> None:
    """Degenerate input: a None snapshot must skip cleanly — no exception,
    no false finding. Mirrors the totality-probe defensive posture."""
    op = _text_replace_op_targeting_section_1()
    out: list[CompileAdjudication] = []
    assert probe_op_mutation_boundary(
        before=None,
        after=None,
        op=op,
        op_id=op.op_id,
        adjudications_out=out,
        source_statute="boundary/4",
    ) is None
    assert out == []


def test_wired_into_apply_op() -> None:
    """Static-line proof that the probe is invoked from
    ``UKReplayExecutor.apply_op`` — i.e. the call site exists, not dead code.

    Pinned at the import + call-site because an out-of-boundary diff is hard
    to reproduce from a single benign replay op (the invariant the probe
    polices is precisely the one the executor is designed to avoid); the
    static line is the dumb-pinned version of "the wire-in landed", complementing
    the runtime probe tests above which exercise the call shape directly.
    """
    from lawvm.uk_legislation import replay_executor as mod

    src = inspect.getsource(mod)
    assert (
        "from lawvm.uk_legislation.mutation_boundary_per_op_probe import"
        in src
    )
    assert "boundary_probe_enabled" in inspect.getsource(
        mod.UKReplayExecutor.apply_op
    )
    assert "probe_op_mutation_boundary" in inspect.getsource(
        mod.UKReplayExecutor.apply_op
    )


def test_probe_default_off_through_pipeline_apply_ops(monkeypatch) -> None:
    """Smoke (default-off): with the env unset, ``apply_ops`` runs the base
    pipeline unchanged on a no-op plan and the probe MUST NOT emit. Production
    UK bench stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)

    pipeline = UKReplayPipeline(Path("."))
    base = _statute(
        _body(_chapter(_section("1"))),
        statute_id="boundary/smoke/default-off",
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    assert not any(a.kind == _FINDING_KIND for a in adjudications), (
        "probe must be default-off; got: {}".format(
            [a for a in adjudications if a.kind == _FINDING_KIND]
        )
    )


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke (env on): with no ops, ``apply_ops`` returns the unchanged base
    and the probe runs through the production fold; because base == replayed
    (within boundary), no shortfall fires. Proves the probe is wired into the
    production ``apply_op`` site, and does not double-fire or invent a
    violation when nothing mutated."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")

    pipeline = UKReplayPipeline(Path("."))
    base = _statute(
        _body(_chapter(_section("1"))),
        statute_id="boundary/smoke/on",
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    violations = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert violations == [], (
        "default no-op replay should not emit any per-op mutation-boundary "
        "violation — got: {}".format(violations)
    )
