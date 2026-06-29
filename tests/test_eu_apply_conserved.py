"""Fire-drill tests for the EU conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized baseline statute with at least one skip through
:func:`apply_eu_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_eu_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

import pytest

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.pipeline import apply_eu_ops, apply_eu_ops_conserved, EUApplyResult
from lawvm.replay_adjudication import CompileAdjudication


def _baseline_statute() -> IRStatute:
    return IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="Section 1"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Section 2"),
            ),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, section_label: str, text: str, source_id: str = "2026/1") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=section_label, text=text),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_eu_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    baseline = _baseline_statute()
    ops = [
        # op #1 — succeeds: REPLACE §1 (target found in baseline).
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        # op #2 — skipped: REPLACE with payload=None (eu_replay_text_payload_missing).
        LegalOperation(
            op_id="eu-replace-no-payload",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=None,
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    result = apply_eu_ops_conserved(baseline, ops)

    assert isinstance(result, EUApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §1's children were
    # replaced; §2's op was skipped (payload missing).
    assert result.statute.body is not baseline.body
    assert result.statute.body.children[0].children == ()  # replaced in place

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "eu-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "eu-replace-no-payload" in rejected_by_id
    skipped = rejected_by_id["eu-replace-no-payload"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "eu_replay_text_payload_missing"
    assert skipped.blocking is False  # EU conserved skips are recorded, not blocking

    # Partition is total (no silent drops, no phantoms). Accepted + rejected = input.
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_apply_eu_ops_conserved_statute_identical_to_bare_variant() -> None:
    """The conserved wrapper mirrors the bare variant — same replay output."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        # Skipped: REPLACE section:9 not found in the baseline.
        LegalOperation(
            op_id="eu-replace-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    bare_adjudications: list[CompileAdjudication] = []
    bare_statute = apply_eu_ops(baseline, list(ops), adjudications_out=bare_adjudications)

    conserved = apply_eu_ops_conserved(baseline, ops)

    # The two statutes are byte-identical — same replay semantics, same op order.
    assert bare_statute == conserved.statute
    assert bare_statute.body is not baseline.body  # both produced a new body
    # The conserved wrapper preserves the bare variant's adjudication ledger too.
    bare_kinds = {a.kind for a in bare_adjudications}
    assert "eu_replay_target_not_found" in bare_kinds  # one skip was emitted


def test_apply_eu_ops_conserved_forwards_adjudications_out_when_passed() -> None:
    """When the caller passes an ``adjudications_out`` list, the conserved
    wrapper surfaces the bare variant's adjudications there too."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        LegalOperation(
            op_id="eu-replace-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_eu_ops_conserved(baseline, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "eu_replay_target_not_found"
    assert adjudications[0].op_id == "eu-replace-not-found"
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 1


def test_apply_eu_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """§1.8 conservation: a SKIPPED op with an empty op_id must NOT silently
    land in the accepted lane."""
    baseline = _baseline_statute()
    ops = [
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_eu_ops_conserved(baseline, ops)


def test_apply_eu_ops_conserved_rejects_duplicate_op_ids() -> None:
    """§1.8 conservation: duplicate op_ids mis-partition. The conserved wrapper
    fails loud on duplicate op_ids rather than mis-partitioning."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="dup", sequence=1, section_label="1", text="replacement"),
        LegalOperation(
            op_id="dup",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_eu_ops_conserved(baseline, ops)


def test_replay_statute_routes_apply_through_conserved_wrapper(monkeypatch, tmp_path) -> None:
    """§2.9 guard-liveness fire-drill: the production lane
    ``EUReplayPipeline.replay_statute`` MUST route the apply fold through
    ``apply_eu_ops_conserved`` (not the bare ``apply_eu_ops``) and surface the
    typed ``FilterResult`` on the ``EUReplayResult.apply_filter_result`` field.

    Pre-fix state: the conserved wrapper existed and was well-tested in
    isolation, but the production call site at ``eu/pipeline.py:898``
    invoked the bare ``apply_eu_ops`` directly. That made the conserved
    wrapper UNREACHABLE from production — the §2.9 worst-class silent failure
    (a guard that exists but cannot fire from the production lane).

    Drives a synthesized op set through the FULL ``replay_statute`` path:

    * one REPLACE §1 op (succeeds — target is in the synthesized baseline body)
    * one REPLACE §99 op (skips — target not in the baseline, surfaces as a
      ``RejectedItem`` with ``reason_code == 'eu_replay_target_not_found'``)

    Mirrors ``test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``
    (SE production fire-drill) in shape: drive-through + assertion that the
    typed receipt landed on the production result. Also mirrors the
    ``test_replay_statute_collects_eu_adjudications`` EU pattern for the
    upstream-phase monkeypatches (compile_ops_for_statute / parse / timelines
    / pit) so the synthesized ops reach the apply fold.
    """
    from lawvm.eu.pipeline import EUReplayPipeline, EUReplayResult, apply_eu_ops_conserved

    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    synthesized_ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        LegalOperation(
            op_id="eu-replace-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # not in baseline
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return synthesized_ops

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    # Spy: replace ``apply_eu_ops_conserved`` in the pipeline module with a
    # wrapper that records the call and delegates to the real function. If
    # the production lane regresses to bare ``apply_eu_ops``, the spy is
    # never invoked — the §2.9 worst-class silent failure.
    invocations: list[tuple] = []

    def spy_apply_eu_ops_conserved(base_arg, ops, **kwargs):
        invocations.append((base_arg, list(ops), dict(kwargs)))
        return apply_eu_ops_conserved(base_arg, ops, **kwargs)

    monkeypatch.setattr(
        "lawvm.eu.pipeline.apply_eu_ops_conserved",
        spy_apply_eu_ops_conserved,
    )

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    # The production lane routed through ``apply_eu_ops_conserved`` — the spy
    # was invoked. If this assertion fails, the production call site has
    # regressed to bare ``apply_eu_ops`` (the §2.9 worst-class silent failure).
    assert invocations, (
        "apply_eu_ops_conserved was not invoked by the production lane — "
        "the production call site may have regressed to bare apply_eu_ops "
        "(§2.9 worst-class silent failure: a guard that exists but is "
        "unreachable from production)."
    )

    # The typed ``FilterResult`` landed on the production result's
    # ``apply_filter_result`` field.
    assert result.apply_filter_result is not None, (
        "result.apply_filter_result is None — the conserved wrapper was "
        "invoked but the typed FilterResult was not threaded to the production "
        "result carrier (§2.9 worst-class silent failure)."
    )
    assert isinstance(result.apply_filter_result, FilterResult)

    rejected_items = list(result.apply_filter_result.rejected_items)
    assert len(rejected_items) == 1, [
        (item.item.op_id, item.reason_code) for item in rejected_items
    ]
    rejected = rejected_items[0]
    assert isinstance(rejected, RejectedItem)
    assert rejected.item.op_id == "eu-replace-not-found"
    assert rejected.reason_code == "eu_replay_target_not_found"
    assert rejected.reason  # message forwarded from the bare variant's adjudication
    assert rejected.blocking is False  # EU conserved skips are recorded, not blocking

    # Accepted lane carries the §1 op; conservation partition is total.
    accepted_ids = {op.op_id for op in result.apply_filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.apply_filter_result.rejected_items}
    input_ids = {op.op_id for op in synthesized_ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_replay_statute_propagates_partial_adjudications_on_apply_raise(
    monkeypatch, tmp_path
) -> None:
    """§2.9 + §1.0/§1.8/§1.10 fire-drill (silent-failure review HIGH #2):

    When ``apply_eu_ops_conserved`` raises mid-apply, the production lane
    ``EUReplayPipeline.replay_statute`` MUST:

    * preserve the partial adjudication witnesses emitted BEFORE the raise on
      ``EUReplayResult.adjudications`` (the §1.0 "evidence is not silently
      destroyed" + §1.8 "no unsupported lane disappears" contracts). Pre-fix
      state: the EU production caller had NO try/except at the apply call
      site — bare-apply raised raw, the local ``adjudications`` list
      (pre-populated with pipeline + parser diagnostics) was discarded
      entirely by the propagating exception.
    * append a typed ``eu_replay_apply_raise`` orchestration adjudication per
      §1.10 embed-exception-as-clause-text rule (so a downstream consumer can
      diagnose the apply raise without re-running extraction);
    * return a typed ``EUReplayResult`` with ``replayed``/``timelines`` /
      ``apply_filter_result`` left ``None`` (apply did not produce a tree) and
      ``error`` carrying the exception summary (mirrors the EE/NO on-raise
      shape — ``result.error = f"Failed to apply ops: {e}"``).

    Mirrors ``test_replay_ee_to_pit_propagates_partial_adjudications_on_apply_raise``
    (the EE production-caller fire-drill), the NO precedent
    ``test_replay_no_to_pit_strict_action_family_rejects_recovery`` (end-to-end
    assertion shape), and the upstream-phase monkeypatch pattern of
    ``test_replay_statute_routes_apply_through_conserved_wrapper`` (the
    production-routing spy test for the EU happy path).
    """
    from lawvm.eu.pipeline import EUReplayPipeline, EUReplayResult
    from lawvm.replay_adjudication import CompileAdjudication

    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    synthesized_ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        _replace_op(op_id="eu-replace-then-raise", sequence=2, section_label="9", text="replacement"),
    ]

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return synthesized_ops

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", lambda *a, **kw: "timelines")
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", lambda *a, **kw: baseline)

    raise_message = "synthesized mid-apply raise (e.g. eu strict_action_family=True)"

    # Spy: replace ``apply_eu_ops_conserved`` in the pipeline module with a
    # wrapper that (a) appends a known pre-raise adjudication to
    # ``adjudications_out`` (mirroring what bare apply does when it processes
    # the synthesized skip op BEFORE the §1.10 fail-loud raise), then (b)
    # raises ValueError. Mirrors the NO precedent at
    # ``test_apply_no_ops_conserved_propagates_recovery_adjudication_on_raise``
    # — bare apply first emits the skip then raises; here the skip + raise is
    # wired through the spy so the EU production caller's on-raise handling
    # is reached through the FULL ``replay_statute`` path (the §2.9
    # guard-liveness discipline — every guard needs a test that drives a
    # known-violating input through the full production path).
    def spy_apply_eu_ops_conserved(base_arg, ops, **kwargs):
        adjudications_out = kwargs.get("adjudications_out")
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind="eu_replay_target_not_found",
                    message=(
                        "Synthesized pre-raise skip adjudication — op target "
                        "not in the baseline body (mirrors bare-apply's per-op "
                        "skip emission BEFORE the §1.10 fail-loud raise)."
                    ),
                    source_statute="2026/2",
                    blocking=False,
                    phase="replay",
                    op_id="eu-replace-then-raise",
                    detail={
                        "rule_id": "eu_replay_target_not_found",
                        "phase": "replay",
                        "blocking": False,
                    },
                )
            )
        raise ValueError(raise_message)

    monkeypatch.setattr(
        "lawvm.eu.pipeline.apply_eu_ops_conserved",
        spy_apply_eu_ops_conserved,
    )

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    # The apply raise is surfaced on ``result.error`` (the new field added
    # alongside this fire-drill — silent-failure review HIGH #2). Pre-fix
    # the raw exception propagated to the caller and there was no
    # ``result.error`` field at all.
    assert result.error is not None, (
        "result.error is None despite apply_eu_ops_conserved raising — the "
        "production caller's on-raise handling regressed (§2.9 worst-class "
        "silent failure: a guard that exists but cannot fire)."
    )
    assert "Failed to apply ops" in result.error
    assert raise_message in result.error

    # Apply did not produce a tree — ``replayed`` / ``timelines`` /
    # ``apply_filter_result`` stay ``None`` (mirrors the EE/NO on-raise shape
    # where ``result.replayed`` stays ``None`` when apply raised).
    assert result.replayed is None
    assert result.timelines is None
    assert result.apply_filter_result is None

    # §1.0 / §1.8 partial-witness preservation: the pre-raise skip adjudication
    # emitted by the spy IS on ``result.adjudications``. Pre-fix the local
    # list was discarded by the propagating exception.
    pre_raise = [
        a for a in result.adjudications if a.kind == "eu_replay_target_not_found"
    ]
    assert pre_raise, (
        "result.adjudications does not carry the pre-raise eu_replay_target_"
        "not_found witness — the §1.0/§1.8 partial-loss failure (silent-"
        "failure review HIGH #2: pre-fix the raw exception discarded the "
        "local adjudications list before construction of EUReplayResult)."
    )
    assert pre_raise[0].op_id == "eu-replace-then-raise"

    # §1.10 typed orchestration adjudication: ``eu_replay_apply_raise`` IS on
    # the result with ``exception_type`` / ``exception`` / ``clause_text``
    # fields embedded in its ``detail``.
    orchestration = next(
        (a for a in result.adjudications if a.kind == "eu_replay_apply_raise"),
        None,
    )
    assert orchestration is not None, (
        "result.adjudications does not carry the typed eu_replay_apply_raise "
        "orchestration adjudication — the §1.10 embed-snippet contract is "
        "unmet (silent-failure review HIGH #2)."
    )
    assert orchestration.detail["exception_type"] == "ValueError"
    assert orchestration.detail["exception"] == raise_message
    assert orchestration.detail["clause_text"] == raise_message  # ≤400 chars
    # The orchestration adjudication is non-blocking — it is a WITNESS, not the
    # gate (mirrors the EE conserved-wrapper's ``RejectedItem.blocking=False``
    # pattern). The blocking gate lives on ``result.error`` (the new field on
    # ``EUReplayResult`` carried alongside this fire-drill — silent-failure
    # review HIGH #2; mirrors the EE/NO ``result.error = f"Failed to apply
    # ops: {e}"`` convention).
    assert orchestration.blocking is False
    assert orchestration.phase == "replay"
    assert orchestration.source_statute == "32000R0000"
    assert orchestration.detail["rule_id"] == "eu_replay_apply_raise"
    assert orchestration.detail["family"] == "orchestration_failure"
