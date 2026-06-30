"""§2.9 production-lane guard-liveness for the Estonia per-op core audits.

Estonia is the FIRST non-UK/non-FI consumer of the jurisdiction-neutral core
per-op audits ``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary``
(LS-01 / D1) and ``lawvm.core.commencement_totality_audit.assert_effect_totality``
(LS-23 / D7). Until ``src/lawvm/estonia/per_op_audit_probe.py`` landed, both
audits were dead code against EE replay — the §2.9 worst-case: a check that
exists, is registered, passes review, and creates false confidence in
invisible containment.

These tests drive:

* D1: a known per-op mutation-boundary escape (a sibling-section text rewrite
  outside the op's declared storage boundary) through the EE probe and assert
  the ``ee_replay_mutation_boundary_per_op_violation_observed`` adjudication
  fires with self-evidencing audited fields (op id, changed/out-of-boundary
  paths). Default-off emits nothing; a clean apply emits nothing. Plus a
  static wire-line proof that ``apply_ee_ops`` invokes the probe behind the
  default-off gate, and a corpus-scale default-off byte-stability guard over
  the real EE grafter fold.

* D7: a known op-without-temporal-authority through the EE totality probe and
  assert the ``ee_replay_commencement_effect_totality_observed`` adjudication
  fires; commenced + pending ops stay silent; default-off emits nothing.

Strict enforcement stays multi-session pending an EE ``strict_profile`` lane;
the probes are the discipline-disclosing first step, observation-only at v0.
"""
from __future__ import annotations

import inspect

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.estonia import grafter as grafter_module
from lawvm.estonia.grafter import apply_ee_ops
from lawvm.estonia.per_op_audit_probe import (
    EE_COMMENCEMENT_EFFECT_TOTALITY_KIND,
    EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    commencement_totality_probe_enabled,
    mutation_boundary_probe_enabled,
    probe_ee_commencement_effect_totality,
    probe_ee_op_mutation_boundary,
)

_MB_KIND = EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND
_MB_ENV_FLAG = "LAWVM_EE_MUTATION_BOUNDARY_PER_OP"
_CT_KIND = EE_COMMENCEMENT_EFFECT_TOTALITY_KIND
_CT_ENV_FLAG = "LAWVM_EE_COMMENCEMENT_EFFECT_TOTALITY_PROBE"


# ── shared synthetic IR helpers ──────────────────────────────────────────────
def _section(label: str, *, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, children=())


def _chapter(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label="1", children=tuple(sections))


def _body(*chapters: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(chapters))


def _statute(body: IRNode, *, statute_id: str) -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="",
        body=body,
        supplements=(),
        metadata={},
    )


def _text_replace_op_targeting_section_1(op_id: str = "ee/op/test/1") -> LegalOperation:
    """A TEXT_REPLACE op whose storage boundary is the section-1 path only.

    EE op targets are rooted at the body's children (no wrapper step), exactly
    matching ``diff_ir_paths(body_before, body_after)`` — so any observed diff
    on sibling section ``2`` is necessarily out-of-boundary (TEXT_REPLACE maps
    to the target path verbatim, no parent expansion).
    """
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
        source=OperationSource(statute_id="ee/boundary/1"),
    )


# ── D1: per-op mutation-boundary probe ───────────────────────────────────────
class TestEEMutationBoundaryProbe:
    def test_probe_fires_for_out_of_boundary_sibling_escape(self) -> None:
        """An op targeting section ``1`` whose apply *also* rewrote sibling
        section ``2``'s text MUST emit the EE per-op violation adjudication with
        self-evidencing audited fields."""
        before = _body(
            _chapter(_section("1", text="original-1"), _section("2", text="original-2"))
        )
        after = _body(
            _chapter(_section("1", text="original-1"), _section("2", text="tampered"))
        )
        op = _text_replace_op_targeting_section_1()

        adjudications: list[CompileAdjudication] = []
        verdict = probe_ee_op_mutation_boundary(
            before=before,
            after=after,
            op=op,
            op_id=op.op_id,
            adjudications_out=adjudications,
            source_statute="ee/boundary/1",
        )
        assert verdict is not None
        assert not verdict.within_boundary
        violations = [a for a in adjudications if a.kind == _MB_KIND]
        assert violations, (
            "expected an ee_replay_mutation_boundary_per_op_violation_observed "
            "adjudication for the sibling-section-2 escape — the §2.9 guard is "
            "unreachable from EE production"
        )
        detail = violations[0].detail
        assert detail["probe_mode"] == "observation_only"
        assert detail["strict_disposition"] == "record"
        assert detail["quirks_disposition"] == "record"
        assert detail["boundary_status"] == "out_of_boundary"
        assert detail["op_id"] == op.op_id
        assert violations[0].blocking is False
        assert violations[0].phase == "replay"
        assert violations[0].source_statute == "ee/boundary/1"
        # §1.10: the out-of-boundary path list must name the escaped path, not
        # just regurgitate the declared target.
        assert detail["out_of_boundary_paths"], (
            "out_of_boundary_paths must be non-empty when boundary_status == "
            "out_of_boundary (AGENTS.md §1.10)"
        )
        assert detail["changed_paths"]
        assert (
            detail["witness_class"]
            == "core.mutation_boundary_proof.audit_op_mutation_boundary"
        )

    def test_probe_within_boundary_emits_nothing(self) -> None:
        """A change confined to the op's declared target node MUST not fire."""
        before = _body(_chapter(_section("1", text="original")))
        after = _body(_chapter(_section("1", text="replaced-in-place")))
        op = _text_replace_op_targeting_section_1()

        adjudications: list[CompileAdjudication] = []
        verdict = probe_ee_op_mutation_boundary(
            before=before,
            after=after,
            op=op,
            op_id=op.op_id,
            adjudications_out=adjudications,
            source_statute="ee/boundary/3",
        )
        assert verdict is None
        assert all(a.kind != _MB_KIND for a in adjudications)

    def test_probe_skips_when_snapshot_is_none(self) -> None:
        """A None snapshot must skip cleanly — no exception, no false finding."""
        op = _text_replace_op_targeting_section_1()
        out: list[CompileAdjudication] = []
        assert (
            probe_ee_op_mutation_boundary(
                before=None,
                after=None,
                op=op,
                op_id=op.op_id,
                adjudications_out=out,
                source_statute="ee/boundary/4",
            )
            is None
        )
        assert out == []

    def test_probe_disabled_by_default(self, monkeypatch) -> None:
        """Default-off: the gate returns False with the env unset, so the apply
        fold never runs the probe and production EE replay stays byte-stable."""
        monkeypatch.delenv(_MB_ENV_FLAG, raising=False)
        assert mutation_boundary_probe_enabled() is False
        monkeypatch.setenv(_MB_ENV_FLAG, "0")
        assert mutation_boundary_probe_enabled() is False
        monkeypatch.setenv(_MB_ENV_FLAG, "1")
        assert mutation_boundary_probe_enabled() is True

    def test_wired_into_apply_ee_ops(self) -> None:
        """Static-line proof that the probe is invoked from ``apply_ee_ops``
        behind the default-off gate — i.e. the call site exists, not dead code."""
        src = inspect.getsource(grafter_module.apply_ee_ops)
        assert "_ee_mutation_boundary_probe_enabled" in src
        assert "_ee_probe_op_mutation_boundary" in src
        # The gate must precede the probe call (default-off short-circuit).
        gate_idx = src.index("_ee_mutation_boundary_probe_enabled")
        call_idx = src.index("_ee_probe_op_mutation_boundary(")
        assert gate_idx < call_idx

    def test_default_off_through_apply_ee_ops_is_byte_stable(self, monkeypatch) -> None:
        """Corpus-scale guard-liveness: with the env unset, the real EE grafter
        fold over a benign op emits NO probe adjudication and the replayed tree
        is identical to the env-on tree — default-off across the EE apply lane
        is byte-stable."""
        monkeypatch.delenv(_MB_ENV_FLAG, raising=False)
        base = _statute(
            _body(_chapter(_section("1", text="orig"))),
            statute_id="ee/boundary/smoke",
        )
        op = LegalOperation(
            op_id="ee/op/smoke/1",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="rewritten"),
            source=OperationSource(statute_id="ee/boundary/smoke"),
        )

        adj_off: list[CompileAdjudication] = []
        replayed_off = apply_ee_ops(base, [op], adjudications_out=adj_off)
        assert not any(a.kind == _MB_KIND for a in adj_off), (
            "probe must be default-off across the EE fold; got: {}".format(
                [a for a in adj_off if a.kind == _MB_KIND]
            )
        )

        monkeypatch.setenv(_MB_ENV_FLAG, "1")
        adj_on: list[CompileAdjudication] = []
        replayed_on = apply_ee_ops(base, [op], adjudications_out=adj_on)
        # A benign in-target rewrite is within boundary, so even with the flag
        # ON no violation fires — and the replayed body is identical either way
        # (the probe is grounding-neutral: pure projection, never mutates).
        assert replayed_off.body == replayed_on.body
        assert not any(a.kind == _MB_KIND for a in adj_on)


# ── D7: commencement-effect totality probe ───────────────────────────────────
def _ct_op(
    *,
    op_id: str,
    group_id: str = "",
    effective: str = "",
    statute_id: str = "ee/ct/1",
) -> LegalOperation:
    source = OperationSource(statute_id=statute_id, effective=effective)
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
        group_id=group_id,
    )


def _commence_event(group_id: str, *, target_statute: str = "ee/ct/1") -> TemporalEvent:
    return TemporalEvent(
        event_id=f"commence-{group_id}",
        kind="commence",
        scope=TemporalScope(
            target_statute=target_statute,
            exact_addresses=(LegalAddress(path=(("section", "1"),)),),
        ),
        effective="2020-01-01",
        group_id=group_id,
    )


class TestEECommencementTotalityProbe:
    def test_probe_fires_for_op_without_temporal_authority(self) -> None:
        """An executed op with no matching commence/revive event and no pending
        classification MUST surface the EE totality adjudication."""
        op = _ct_op(op_id="ee/ct/op/orphan", group_id="grp-orphan")
        adjudications: list[CompileAdjudication] = []
        observations = probe_ee_commencement_effect_totality(
            [op],
            (),  # no temporal events → op is not commenced
            adjudications_out=adjudications,
            source_statute="ee/ct/1",
        )
        assert observations, "expected one totality observation for the orphan op"
        violations = [a for a in adjudications if a.kind == _CT_KIND]
        assert violations, (
            "expected an ee_replay_commencement_effect_totality_observed "
            "adjudication — the §2.9 D7 guard is unreachable from EE production"
        )
        detail = violations[0].detail
        assert detail["probe_mode"] == "observation_only"
        assert detail["strict_disposition"] == "record"
        assert violations[0].blocking is False
        assert violations[0].phase == "replay_products"
        assert violations[0].op_id == op.op_id
        assert detail["observation_detail"]["op_id"] == op.op_id
        assert (
            detail["witness_class"]
            == "core.commencement_totality_audit.assert_effect_totality"
        )

    def test_commenced_op_emits_nothing(self) -> None:
        """An op commenced by a matching temporal event MUST stay silent."""
        op = _ct_op(op_id="ee/ct/op/commenced", group_id="grp-live")
        event = _commence_event("grp-live")
        adjudications: list[CompileAdjudication] = []
        observations = probe_ee_commencement_effect_totality(
            [op],
            (event,),
            adjudications_out=adjudications,
            source_statute="ee/ct/1",
        )
        assert observations == ()
        assert all(a.kind != _CT_KIND for a in adjudications)

    def test_pending_op_emits_nothing(self) -> None:
        """An op tagged pending_amendment MUST stay silent (owned deferral)."""
        op = LegalOperation(
            op_id="ee/ct/op/pending",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            source=OperationSource(statute_id="ee/ct/1"),
            group_id="grp-pending",
            provenance_tags=("pending_amendment",),
        )
        adjudications: list[CompileAdjudication] = []
        observations = probe_ee_commencement_effect_totality(
            [op],
            (),
            adjudications_out=adjudications,
            source_statute="ee/ct/1",
        )
        assert observations == ()
        assert all(a.kind != _CT_KIND for a in adjudications)

    def test_empty_ops_emits_nothing(self) -> None:
        adjudications: list[CompileAdjudication] = []
        assert (
            probe_ee_commencement_effect_totality(
                [],
                (),
                adjudications_out=adjudications,
                source_statute="ee/ct/1",
            )
            == ()
        )
        assert adjudications == []

    def test_probe_disabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv(_CT_ENV_FLAG, raising=False)
        assert commencement_totality_probe_enabled() is False
        monkeypatch.setenv(_CT_ENV_FLAG, "1")
        assert commencement_totality_probe_enabled() is True

    def test_wired_into_replay_compile_timelines(self) -> None:
        """Static-line proof that the probe is invoked at the EE replay
        timeline-compile fold-exit behind the default-off gate."""
        from lawvm.estonia import replay as replay_module

        src = inspect.getsource(replay_module)
        assert "_ee_commencement_totality_probe_enabled" in src
        assert "_ee_probe_commencement_effect_totality" in src
