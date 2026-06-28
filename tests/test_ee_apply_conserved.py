"""Fire-drill tests for the EE conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized statute with at least one skip through
:func:`apply_ee_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_ee_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

import pytest

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.fetch import AmendmentRef
from lawvm.estonia.grafter import apply_ee_ops, apply_ee_ops_conserved, EEApplyResult
from lawvm.replay_adjudication import CompileAdjudication


def _statute_with_section(label: str = "5", text: str = "Original text") -> IRStatute:
    return IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label=label, text=text),),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, label: str, text: str, source_id: str = "ee/amend") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_ee_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    statute = _statute_with_section("5", "Original.")
    ops = [
        # op #1 — succeeds: REPLACE §5 with a valid payload.
        _replace_op(op_id="ee-replace-ok", sequence=1, label="5", text="Ny lydelse."),
        # op #2 — skipped: REPLACE §99 (target not found in the body).
        _replace_op(op_id="ee-replace-missing", sequence=2, label="99", text="Nytt."),
    ]

    result = apply_ee_ops_conserved(statute, ops)

    assert isinstance(result, EEApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §5's text was replaced,
    # but §99's failed op did not change anything.
    assert result.statute.body is not statute.body
    assert result.statute.body.children[0].text == "Ny lydelse."

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "ee-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "ee-replace-missing" in rejected_by_id
    skipped = rejected_by_id["ee-replace-missing"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "ee_replay_target_not_found"
    assert skipped.blocking is False  # EE conserved skips are recorded, not blocking (mirrors SE)

    # Partition is total (no silent drops, no phantoms). Accepted + rejected = input.
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_apply_ee_ops_conserved_statute_identical_to_bare_variant() -> None:
    """The conserved wrapper mirrors the bare variant — same replay output.

    Both the conserved result's statute field and the bare variant's return
    value are the same replay semantics on the same input; the only difference
    is the typed FilterResult receipt the conserved wrapper adds.
    """
    statute = _statute_with_section("5", "Original.")
    ops = [
        _replace_op(op_id="ee-replace-ok", sequence=1, label="5", text="Ny lydelse."),
        _replace_op(op_id="ee-replace-missing", sequence=2, label="99", text="Nytt."),
    ]

    bare_adjudications: list[CompileAdjudication] = []
    bare_statute = apply_ee_ops(statute, list(ops), adjudications_out=bare_adjudications)

    conserved = apply_ee_ops_conserved(statute, ops)

    # The two statutes are byte-identical — same replay semantics, same op order.
    assert bare_statute == conserved.statute
    assert bare_statute.body is not statute.body  # both produced a new body
    # The conserved wrapper preserves the bare variant's adjudication ledger too.
    assert len(bare_adjudications) == 1  # one skip (ee-replace-missing)


def test_apply_ee_ops_conserved_forwards_adjudications_out_when_passed() -> None:
    """When the caller passes an ``adjudications_out`` list, the conserved
    wrapper surfaces the bare variant's adjudications there too — the typed
    carrier does NOT replace the existing descriptive adjudication path.
    Both share the same evidence ledger (mirrors the SE wrapper)."""
    statute = _statute_with_section("5", "Original.")
    ops = [
        _replace_op(op_id="ee-replace-ok", sequence=1, label="5", text="Ny lydelse."),
        _replace_op(op_id="ee-replace-missing", sequence=2, label="99", text="Nytt."),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_ee_ops_conserved(statute, ops, adjudications_out=adjudications)

    # The conserved wrapper surfaces the bare variant's adjudication ledger.
    assert len(adjudications) == 1
    assert adjudications[0].kind == "ee_replay_target_not_found"
    assert adjudications[0].op_id == "ee-replace-missing"
    # The conserved result still carries the typed partition.
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 1


def test_apply_ee_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """§1.8 conservation: a SKIPPED op with an empty op_id must NOT silently
    land in the accepted lane. The op_id keying would drop empty op_ids from
    the skipped set; the conserved wrapper fails loud instead of mis-partitioning.
    (Mirrors the SE conserved wrapper's guard for the same invariant.)"""
    statute = _statute_with_section("5", "Original.")
    ops = [
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped (not found)
            payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="Nytt."),
            source=OperationSource(statute_id="ee/amend"),
        ),
    ]
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_ee_ops_conserved(statute, ops)


def test_apply_ee_ops_conserved_rejects_duplicate_op_ids() -> None:
    """§1.8 conservation: duplicate op_ids mis-partition. The conserved wrapper
    fails loud on duplicate op_ids rather than mis-partitioning."""
    statute = _statute_with_section("5", "Original.")
    ops = [
        _replace_op(op_id="dup", sequence=1, label="5", text="Ny 5."),
        LegalOperation(
            op_id="dup",  # shared id with op #1 — not a robust identity
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped (not found)
            payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="Nytt."),
            source=OperationSource(statute_id="ee/amend"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_ee_ops_conserved(statute, ops)


def test_apply_ee_ops_conserved_supports_blame_map_and_lo_ops_out() -> None:
    """The conserved wrapper forwards the bare variant's out-parameters
    (``blame_map``, ``lo_ops_out``) so existing call-site expectations still
    apply when production routes through the conserved path."""
    statute = _statute_with_section("5", "Original.")
    ops = [_replace_op(op_id="ee-replace-ok", sequence=1, label="5", text="Ny lydelse.")]
    blame_map: dict[str, LegalOperation] = {}
    lo_ops: list[LegalOperation] = []

    result = apply_ee_ops_conserved(
        statute,
        ops,
        blame_map=blame_map,
        lo_ops_out=lo_ops,
    )

    # The conserved result still carries the partition...
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 0
    # ...and the bare variant's out-parameters are still populated.
    assert "section:5" in blame_map or any("section" in k for k in blame_map)
    # lo_ops_out accumulates a snapshot of the post-op section-level node tree.
    assert len(lo_ops) >= 1
    assert all(lo.action.value == "replace" or hasattr(lo.action, "value") for lo in lo_ops)


def test_replay_ee_to_pit_routes_apply_through_conserved_wrapper(monkeypatch) -> None:
    """§2.9 guard-liveness fire-drill: the production lane ``replay_ee_to_pit``
    MUST route the apply fold through ``apply_ee_ops_conserved`` (not the bare
    ``apply_ee_ops``) and surface the typed ``FilterResult`` on the
    ``EEPitResult.apply_filter_result`` field.

    Pre-fix state: the conserved wrapper existed and was well-tested in
    isolation, but the production call site at ``estonia/replay.py:1831``
    invoked the bare ``apply_ee_ops`` directly. That made the conserved
    wrapper UNREACHABLE from production — the §2.9 worst-class silent failure
    (a guard that exists but cannot fire from the production lane).

    Drives a synthesized op set through the FULL ``replay_ee_to_pit`` path:

    * one REPLACE §5 op (succeeds — target is in the synthesized base body)
    * one REPLACE §99 op (skips — target not in the base body, surfaces as a
      ``RejectedItem`` with ``reason_code == 'ee_replay_target_not_found'``)

    Mirrors ``test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``
    (SE production fire-drill) in shape: drive-through + assertion that the
    typed receipt landed on the production result. Mirrors the
    ``_patch_replay_for_crash_drill`` helper in ``test_ee_guard_liveness.py``
    for the upstream-phase monkeypatches (parse / fetch / plan / filter / pit
    mocks) so the synthesized ops reach the apply fold.
    """
    from types import SimpleNamespace

    from lawvm.estonia import replay as ee_replay
    from lawvm.estonia.grafter import apply_ee_ops_conserved as real_apply_ee_ops_conserved
    from lawvm.estonia.replay import replay_ee_to_pit

    base = IRStatute(
        statute_id="ee/base",
        title="Test statute",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="5", text="Original."),),
        ),
    )
    synthesized_ops = [
        _replace_op(op_id="ee-replace-ok", sequence=1, label="5", text="Ny lydelse."),
        _replace_op(op_id="ee-replace-missing", sequence=2, label="99", text="Nytt."),
    ]

    amendment_ref = AmendmentRef(
        aktViide="118122025003",
        passed="2025-12-03",
        joustumine="2026-01-01",
    )
    pair_plan = SimpleNamespace(
        grupi_id="g1",
        oracle_id=None,
        source_basis=SimpleNamespace(value="pairwise_terviktekst_delta"),
        comparison_class="commensurable_delta",
        source_adjudication=None,
        oracle_is_base=True,
        oracle_refs=[],
        amendments_to_apply=[amendment_ref],
        base_is_consolidated=True,
        base_refs=[amendment_ref],
    )

    # Spy: replace ``apply_ee_ops_conserved`` in the replay module with a
    # wrapper that records the call and delegates to the real function. If the
    # production lane regresses to bare ``apply_ee_ops``, the spy is never
    # invoked — the §2.9 worst-class silent failure.
    invocations: list[tuple] = []

    def spy_apply_ee_ops_conserved(statute, ops, **kwargs):
        invocations.append((statute, list(ops), dict(kwargs)))
        return real_apply_ee_ops_conserved(statute, ops, **kwargs)

    # Upstream-phase mocks mirror ``_patch_replay_for_crash_drill`` so the
    # synthesized ops reach the apply fold. The bare ``apply_ee_ops`` is NOT
    # patched in the grafter module — the conserved wrapper resolves it via
    # its own module globals, which remain the real implementation. Patching
    # ``ee_replay.apply_ee_ops`` here would only replace the (now-unused)
    # local reference in the replay module that the bare call site no longer
    # touches.
    monkeypatch.setattr(ee_replay, "parse_ee_statute", lambda xml, statute_id: base)
    monkeypatch.setattr(ee_replay, "fetch_rt_xml", lambda akt_viide, archive: b"<base-xml/>")
    monkeypatch.setattr(
        ee_replay,
        "plan_ee_oracle_pair",
        lambda **kw: SimpleNamespace(plan=pair_plan, oracle_xml=b"<oracle-xml/>"),
    )
    monkeypatch.setattr(ee_replay, "_ee_filter_cancelled_pending_refs", lambda refs, **kw: refs)
    monkeypatch.setattr(
        ee_replay,
        "_ee_precompose_pending_source_act_commencements",
        lambda refs, **kw: (tuple(refs), ()),
    )
    monkeypatch.setattr(ee_replay, "parse_ee_amendment_ops", lambda *a, **kw: synthesized_ops)
    # ``_ee_precompose_pending_amendment_text_patches`` parses real amendment
    # XML for cross-act title-matching; pass through unchanged because the
    # synthesized ops already represent the executable surface.
    monkeypatch.setattr(
        ee_replay,
        "_ee_precompose_pending_amendment_text_patches",
        lambda ops, *, refs, amendment_xml_by_ref: (ops, ()),
    )
    monkeypatch.setattr(ee_replay, "apply_ee_ops_conserved", spy_apply_ee_ops_conserved)
    monkeypatch.setattr(ee_replay, "compile_timelines", lambda base_ir, lo_ops_out, temporal_events=(): {})
    monkeypatch.setattr(ee_replay, "materialize_pit", lambda timelines, as_of, base: base)
    monkeypatch.setattr(ee_replay, "ingest_consolidated", lambda oracle, as_of: oracle)
    monkeypatch.setattr(ee_replay, "verify_consistency", lambda *a, **kw: [])

    result = replay_ee_to_pit("base", "2025-01-01", archive=object())

    # The production lane routed through ``apply_ee_ops_conserved`` — the spy
    # was invoked. If this assertion fails, the production call site has
    # regressed to bare ``apply_ee_ops`` (the §2.9 worst-class silent failure).
    assert invocations, (
        "apply_ee_ops_conserved was not invoked by the production lane — "
        "the production call site may have regressed to bare apply_ee_ops "
        "(§2.9 worst-class silent failure: a guard that exists but is "
        "unreachable from production)."
    )
    assert result.error is None, f"replay_ee_to_pit errored: {result.error!r}"

    # The typed ``FilterResult`` landed on the production result's
    # ``apply_filter_result`` field. Without this assignment, the conserved
    # wrapper's receipt is computed and then silently dropped — same §2.9
    # worst-class silent failure (field exists, but is never populated from
    # production).
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
    assert rejected.item.op_id == "ee-replace-missing"
    assert rejected.reason_code == "ee_replay_target_not_found"
    assert rejected.reason  # message forwarded from the bare variant's adjudication
    assert rejected.blocking is False  # EE conserved skips are recorded, not blocking

    # Accepted lane carries the §5 op; conservation partition is total.
    accepted_ids = {op.op_id for op in result.apply_filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.apply_filter_result.rejected_items}
    input_ids = {op.op_id for op in synthesized_ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint
