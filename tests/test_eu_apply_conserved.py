"""Fire-drill tests for the EU conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized baseline statute with at least one skip through
:func:`apply_eu_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_eu_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

from typing import cast

import pytest
from typing import cast

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind

# EU annex nodes carry the raw string kind "annex": no IRNodeKind.ANNEX enum
# member exists yet, so production's grafter builds them with
# ``cast(IRNodeKind, kind)`` (kind == "annex"). Mirror that idiom here so the
# fixtures match real annex-node typing rather than tripping ty.
_ANNEX_KIND = cast(IRNodeKind, "annex")
from lawvm.core.write_receipt import WriteReceipt
from lawvm.eu.pipeline import apply_eu_ops, apply_eu_ops_conserved, EUApplyResult
from lawvm.replay_adjudication import CompileAdjudication


_ANNEX_KIND = cast(IRNodeKind, "annex")


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


def test_apply_eu_ops_conserved_empty_target_label_skips_not_raises() -> None:
    """A REPLACE/REPEAL op whose target has an EMPTY label (the sole-annex
    indirect-amendment form ``annex:`` the FMX4 grammar lowers with no
    coordinate) must be SKIPPED as a typed ``eu_replay_target_not_found``
    witness — NOT crash the whole conserved fold.

    Pre-fix state: ``apply_eu_ops`` passed the empty label straight to
    ``tree_ops.find``, whose first-match contract fail-louds on an empty label
    (``ValueError: label must be non-empty``). Unguarded that ValueError
    escaped the conserved fold and discarded EVERY op in the set (a §1.8
    conservation violation — one un-coordinated annex op lost the entire
    replay). Observed live on 32016R0044 (the op-id-fix headline base): its 33
    lowered annex ops included two bare ``annex:`` sole-annex ops that crashed
    the fold. The guard turns those into rejected witnesses so the other ops
    still apply/reject normally and a materialized state is produced.
    """
    baseline = _baseline_statute()
    empty_label_replace = LegalOperation(
        op_id="eu-annex-empty-label",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("annex", ""),)),  # sole-annex bare form
        payload=IRNode(kind=IRNodeKind.SECTION, label="", text="replacement annex"),
        source=OperationSource(statute_id="2026/1"),
    )
    good_replace = _replace_op(
        op_id="eu-replace-ok", sequence=2, section_label="1", text="replacement"
    )
    ops = [empty_label_replace, good_replace]

    # Does not raise — the empty-label op is a typed skip, not a crash.
    result = apply_eu_ops_conserved(baseline, ops)

    assert isinstance(result, EUApplyResult)
    # Conservation partition is total: the good op applied, the empty-label op
    # is a rejected witness (NOT silently dropped, NOT a crash).
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    assert accepted_ids == {"eu-replace-ok"}
    assert rejected_ids == {"eu-annex-empty-label"}
    rejected = {item.item.op_id: item for item in result.filter_result.rejected_items}[
        "eu-annex-empty-label"
    ]
    assert rejected.reason_code == "eu_replay_target_not_found"
    assert rejected.blocking is False
    # The good op still landed a materialized state (the whole set is NOT lost
    # to the one empty-label op — the pre-fix crash signature).
    assert result.statute.body is not baseline.body


def test_apply_eu_ops_conserved_empty_label_repeal_skips_not_raises() -> None:
    """The REPEAL branch has the same empty-target-label guard as REPLACE."""
    baseline = _baseline_statute()
    empty_label_repeal = LegalOperation(
        op_id="eu-annex-empty-repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("annex", ""),)),
        payload=None,
        source=OperationSource(statute_id="2026/1"),
    )
    good_replace = _replace_op(
        op_id="eu-replace-ok", sequence=2, section_label="1", text="replacement"
    )
    result = apply_eu_ops_conserved(baseline, [empty_label_repeal, good_replace])

    rejected = {item.item.op_id: item for item in result.filter_result.rejected_items}
    assert "eu-annex-empty-repeal" in rejected
    assert rejected["eu-annex-empty-repeal"].reason_code == "eu_replay_target_not_found"
    assert {op.op_id for op in result.filter_result.accepted_items} == {"eu-replace-ok"}


# ---------------------------------------------------------------------------
# Annex-in-supplements resolution (EU_ANNEX_RESOLUTION — assessment blocker #2).
#
# The EU grafter places annexes in ``IRStatute.supplements`` (a flat list of
# top-level ``annex`` IRNodes), NOT in ``body``. Before the fix, ``apply_eu_ops``
# searched only ``body`` for op targets, so EVERY ``annex:N`` op resolved as
# ``eu_replay_target_not_found`` even when the annex WAS parsed. The fix resolves
# annex-rooted targets against a synthetic container wrapping ``supplements`` and
# threads the mutated supplements onto the returned statute.
# ---------------------------------------------------------------------------


def _statute_with_annexes() -> IRStatute:
    """Baseline whose annexes live in ``supplements`` (grafter placement)."""
    return IRStatute(
        statute_id="32016R0044",
        title="baseline with annexes",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Article 1"),),
        ),
        supplements=(
            IRNode(kind=_ANNEX_KIND, label="II", text="Original Annex II"),
            IRNode(kind=_ANNEX_KIND, label="III", text="Original Annex III"),
        ),
    )


def _annex_replace_op(*, op_id: str, sequence: int, annex_label: str, text: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("annex", annex_label),)),
        payload=IRNode(kind=_ANNEX_KIND, label=annex_label, text=text),
        source=OperationSource(statute_id="2026/1"),
    )


def test_annex_replace_resolves_against_supplements_and_applies() -> None:
    """An ``annex:II`` REPLACE resolves against the supplements-hosted annex and
    APPLIES (pre-fix it resolved as ``eu_replay_target_not_found`` because the
    apply searched only ``body``, where EU annexes never live)."""
    baseline = _statute_with_annexes()
    op = _annex_replace_op(op_id="eu-annex-II", sequence=1, annex_label="II", text="Replacement Annex II")

    result = apply_eu_ops_conserved(baseline, [op])

    # The annex op is ACCEPTED (resolved + applied), not rejected.
    assert {o.op_id for o in result.filter_result.accepted_items} == {"eu-annex-II"}
    assert list(result.filter_result.rejected_items) == []
    # Annex II's text was replaced in supplements; Annex III is untouched.
    supp = {s.label: s.text for s in result.statute.supplements}
    assert supp["II"] == "Replacement Annex II"
    assert supp["III"] == "Original Annex III"
    # CoW: the baseline's supplements are NOT mutated.
    assert {s.label: s.text for s in baseline.supplements} == {
        "II": "Original Annex II",
        "III": "Original Annex III",
    }


def test_annex_repeal_resolves_against_supplements_and_removes() -> None:
    """An ``annex:III`` REPEAL resolves against supplements and removes it."""
    baseline = _statute_with_annexes()
    op = LegalOperation(
        op_id="eu-annex-III-repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("annex", "III"),)),
        payload=None,
        source=OperationSource(statute_id="2026/1"),
    )

    result = apply_eu_ops_conserved(baseline, [op])

    assert {o.op_id for o in result.filter_result.accepted_items} == {"eu-annex-III-repeal"}
    assert [s.label for s in result.statute.supplements] == ["II"]
    # Baseline unchanged (CoW).
    assert [s.label for s in baseline.supplements] == ["II", "III"]


def test_annex_op_not_found_still_rejects_when_annex_absent() -> None:
    """Conservation is preserved: an ``annex:ZZ`` op against a base without that
    annex still REJECTS as ``eu_replay_target_not_found`` (the fix widens the
    search scope, it does not invent targets)."""
    baseline = _statute_with_annexes()
    op = _annex_replace_op(op_id="eu-annex-ZZ", sequence=1, annex_label="ZZ", text="x")

    result = apply_eu_ops_conserved(baseline, [op])

    assert list(result.filter_result.accepted_items) == []
    rejected = {i.item.op_id: i for i in result.filter_result.rejected_items}
    assert rejected["eu-annex-ZZ"].reason_code == "eu_replay_target_not_found"


def test_mixed_annex_and_body_ops_partition_correctly() -> None:
    """A mixed op set (annex REPLACE + body REPLACE + missing-annex REJECT)
    partitions correctly and applies to BOTH supplements and body — the fix does
    not disturb the non-annex ``body`` path (byte-safe)."""
    baseline = _statute_with_annexes()
    ops = [
        _annex_replace_op(op_id="eu-annex-II", sequence=1, annex_label="II", text="New II"),
        _replace_op(op_id="eu-sec-1", sequence=2, section_label="1", text="New Article 1"),
        _annex_replace_op(op_id="eu-annex-missing", sequence=3, annex_label="ZZ", text="x"),
    ]

    result = apply_eu_ops_conserved(baseline, ops)

    accepted = {o.op_id for o in result.filter_result.accepted_items}
    rejected = {i.item.op_id for i in result.filter_result.rejected_items}
    assert accepted == {"eu-annex-II", "eu-sec-1"}
    assert rejected == {"eu-annex-missing"}
    # Both lanes landed: annex II in supplements, section 1 in body.
    assert {s.label: s.text for s in result.statute.supplements}["II"] == "New II"
    assert result.statute.body.children[0].text == "New Article 1"
    # Conservation partition is total and disjoint.
    input_ids = {op.op_id for op in ops}
    assert accepted | rejected == input_ids
    assert accepted & rejected == set()


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


# ---------------------------------------------------------------------------
# Per-op WriteReceipt fire-drills (AGENTS.md §2.3 + §2.9 — receipt contract).
#
# Mirrors the SE shape at ``tests/test_sweden_fetch.py::
# test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``
# and the NO shape at ``tests/test_no_renumber_migration.py::
# test_no_replay_production_lane_emits_renumber_write_receipt_with_migration_rule_id``.
# Two test layers per §2.9:
#   (1) production-lane reachability: drive a synthesized op through
#       ``apply_eu_ops_conserved(emit_receipts=True)`` and assert the receipt
#       lands on ``result.write_receipts`` — without this assertion the
#       receipt helper exists but is unreachable from the conserved wrapper
#       (the §2.9 worst-class silent failure).
#   (2) family isolation: drive the helper directly with synthesized
#       before/after IR trees to assert the RENUMBER-specific
#       ``migration_rule_ids == ("eu_renumber_relabel",)`` stamp + the
#       bound→landed divergence is named-and-witnessed (§1.6 unstated-
#       migration invariant). EU's bare apply lists RENUMBER in its
#       unsupported-action set today, so the family-isolation test
#       synthesizes the before/after bodies directly rather than going
#       through ``apply_eu_ops`` (the SE/NO precedent runs through bare
#       apply because SE/NO bare apply supports RENUMBER — see
#       ``sweden/grafter.py:3862`` / ``norway/grafter.py:4142``).
# ---------------------------------------------------------------------------


def test_apply_eu_ops_conserved_emit_receipts_writes_receipt_for_supported_action() -> None:
    """§2.9 guard-liveness (production-lane reachability fire-drill): when
    ``emit_receipts=True`` is passed, ``apply_eu_ops_conserved`` re-applies
    the ops one at a time via ``eu_replay_write_receipts`` and surfaces the
    resulting :class:`WriteReceipt` records on ``EUApplyResult.write_receipts``.

    Pre-fix state (the deferred B2 task): the ``apply_eu_ops_conserved``
    wrapper exists but emits NO per-op receipts — the SE/NO precedent at
    ``sweden/grafter.py:3974`` / ``norway/grafter.py:4268`` is unreachable
    from the EU conserved wrapper. A guard that exists but is unreachable
    from production is the §2.9 worst-class silent failure.

    Drives a synthesized REPLACE op (EU bare apply's supported-action set)
    through ``apply_eu_ops_conserved(emit_receipts=True)`` and asserts the
    receipt lands on the result. The REPLACE action stamps
    ``migration_rule_ids == ()`` (RENUMBER is the only action that mints a
    migration rule id — the bound→landed divergence for REPLACE/INSERT/REPEAL
    is equality, not a relabel), so this test also serves as the negative
    case for the ``migration_rule_ids`` stamping behavior.

    Mirrors ``test_se_replay_write_receipts_emits_typed_receipt_per_applied_op``
    in shape (the SE production fire-drill for the receipt helper).
    """
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
    ]

    result = apply_eu_ops_conserved(baseline, ops, emit_receipts=True)

    assert isinstance(result, EUApplyResult)
    # §2.9 reachability: the receipt helper fired from the production-lane
    # conserved wrapper. If this assertion fails, the receipt helper exists
    # but is unreachable — the §2.9 worst-class silent failure.
    assert result.write_receipts, (
        "apply_eu_ops_conserved(emit_receipts=True) did not emit any WriteReceipts — "
        "the eu_replay_write_receipts helper is unreachable from the conserved wrapper "
        "(§2.9 worst-class silent failure: a guard that exists but cannot fire)."
    )
    assert len(result.write_receipts) == 1, [r.op_id for r in result.write_receipts]
    receipt = result.write_receipts[0]
    assert isinstance(receipt, WriteReceipt)
    assert receipt.op_id == "eu-replace-ok"
    assert receipt.action == "replace"
    assert receipt.helper == "apply_eu_ops::replace::section"
    # bound == landed for REPLACE (no relabel), so divergence_explained is
    # True via the equality short-circuit without a named migration rule.
    assert receipt.bound_target_path == (("section", "1"),)
    assert receipt.landed_primary_path == (("section", "1"),)
    assert receipt.divergence_explained is True
    # Negative case for the migration_rule_ids stamp: REPLACE is NOT a
    # RENUMBER, so no migration_rule_id applies — bound==landed already.
    assert receipt.migration_rule_ids == ()
    assert receipt.recovery_rule_ids == ()
    assert receipt.fallback_rule_ids == ()
    # The REPLACE footprint lands in replaced_paths (sourced from the diff).
    assert receipt.replaced_paths, receipt.replaced_paths
    assert () not in receipt.replaced_paths  # no bogus empty-path tuple
    # pre/post hashes are populated at the covering region (section:1).
    assert "section:1" in receipt.pre_hashes, receipt.pre_hashes
    assert "section:1" in receipt.post_hashes, receipt.post_hashes
    assert receipt.pre_hashes["section:1"] != ""
    assert receipt.post_hashes["section:1"] != ""
    assert receipt.pre_hashes["section:1"] != receipt.post_hashes["section:1"]
    assert receipt.renumbered_paths == ()
    assert receipt.created_paths == ()
    assert receipt.removed_paths == ()

    # Backward-compat: emit_receipts=False (default) produces NO receipts.
    result_default = apply_eu_ops_conserved(baseline, ops)
    assert result_default.write_receipts == ()


def test_eu_emit_one_op_receipt_stamps_eu_renumber_relabel_for_renumber_op() -> None:
    """§2.9 family-isolation (synthetic) + §1.6 unstated-migration fire-drill.

    Drives a synthesized RENUMBER op with synthesized before/after IR trees
    directly through ``_eu_emit_one_op_receipt`` (mirrors SE's shape at
    ``sweden/grafter.py::_se_emit_one_op_receipt`` line 4116) and asserts:

    * The receipt's ``migration_rule_ids == ("eu_renumber_relabel",)`` —
      the named migration rule that explains the bound (source label §1)
      → landed (destination label §2) divergence. Without this stamp the
      receipt audits as ``violation`` in ``build_observed_write_audit`` (a
      §1.6 unstated-migration violation that strict mode must reject).
    * ``divergence_explained is True`` — the §4 receipt-contract property
      (bound != landed + non-empty ``named_rule_ids`` → True via the
      ``WriteReceipt.divergence_explained`` short-circuit).
    * ``bound_target_path`` (source §1) and ``landed_primary_path``
      (destination §2) are populated and diverge (the relabel IS the
      migration).
    * ``renumbered_paths == ((from, to),)`` — the typed (from_path, to_path)
      footprint mirroring SE at ``sweden/grafter.py:4198``.
    * ``pre_hashes["section:2"] == ""`` (destination absent before) and
      ``post_hashes["section:2"] != ""`` (destination present after) —
      the canonical RENUMBER hash recipe from
      CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2.

    EU's bare apply lists RENUMBER in its unsupported-action set today (see
    ``eu/pipeline.py`` line ~405), so the receipt CANNOT be exercised through
    the production-lane ``apply_eu_ops_conserved(emit_receipts=True)`` path
    yet (the bare apply would skip the op and emit no receipt — the
    family-isolation test bypasses the skip by synthesizing the before/after
    bodies directly). When EU lands RENUMBER apply support, the production-
    lane §2.9 fire-drill for RENUMBER (mirroring SE's
    ``test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``
    and NO's
    ``test_no_replay_production_lane_emits_renumber_write_receipt_with_migration_rule_id``)
    becomes possible; until then, the family-isolation test owns the
    ``eu_renumber_relabel`` receipt-side stamping guard.
    """
    from lawvm.eu.pipeline import _eu_emit_one_op_receipt

    # Synthesized RENUMBER before/after bodies: §1 renumbered to §2 (the
    # section's text content is the same; only the label changed). §5 stays
    # unchanged as a stable sibling so the diff is non-empty on the §1/§2
    # pair.
    before_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="Section 1"),
            IRNode(kind=IRNodeKind.SECTION, label="5", text="Anchored sibling"),
        ),
    )
    after_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="2", text="Section 1"),
            IRNode(kind=IRNodeKind.SECTION, label="5", text="Anchored sibling"),
        ),
    )
    op = LegalOperation(
        op_id="eu-renumber-1-to-2",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", "1"),)),
        destination=LegalAddress(path=(("section", "2"),)),
        source=OperationSource(statute_id="2026/1"),
    )

    receipt = _eu_emit_one_op_receipt(before_body, after_body, op)
    assert receipt is not None, (
        "_eu_emit_one_op_receipt returned None for a synthesized RENUMBER "
        "with before/after bodies that DO diverge — the diff-emptiness guard "
        "fired incorrectly (§2.9 family-isolation regression)."
    )

    # The action's landing footprint.
    assert receipt.action == "renumber"
    assert receipt.op_id == "eu-renumber-1-to-2"
    assert receipt.helper == "apply_eu_ops::renumber::section"

    # bound == source §1, landed == destination §2 (divergence by construction).
    assert receipt.bound_target_path == (("section", "1"),), receipt.bound_target_path
    assert receipt.landed_primary_path == (("section", "2"),), receipt.landed_primary_path
    assert receipt.bound_target_path != receipt.landed_primary_path

    # The typed (from, to) RENUMBER footprint.
    assert receipt.renumbered_paths == (
        ((("section", "1"),), (("section", "2"),)),
    ), receipt.renumbered_paths
    # The bogus empty-path tuple must NOT be in any footprint slot.
    assert () not in receipt.replaced_paths
    assert () not in [leg for pair in receipt.renumbered_paths for leg in pair]

    # §1.6 unstated-migration invariant's named owner — the §4 receipt-contract
    # divergence-explained witness (mirrors SE's ``("se_renumber_relabel",)``
    # at sweden/grafter.py:4157 and NO's ``("no_section_renumber_relabel",)``
    # at norway/grafter.py:4448). Without this stamp, divergence_explained
    # returns False and the receipt audits as ``violation``.
    assert receipt.migration_rule_ids == ("eu_renumber_relabel",), (
        f"Expected migration_rule_ids=('eu_renumber_relabel',), "
        f"got {receipt.migration_rule_ids!r}. The §1.6 unstated-migration "
        "invariant's identity migration has no named owner on the EU receipt — "
        "the receipt audits as `violation` in build_observed_write_audit and "
        "strict mode must reject it."
    )
    assert receipt.recovery_rule_ids == ()
    assert receipt.fallback_rule_ids == ()
    assert receipt.divergence_explained is True, (
        "RENUMBER receipt with bound != landed should have divergence_explained=True "
        "via the migration_rule_ids stamp — the §4 receipt-contract property."
    )

    # The receipt's pre/post hashes resolve at the destination coordinate
    # (where the section landed): §2 was ABSENT before, present after.
    assert list(receipt.pre_hashes.keys()) == ["section:2"], receipt.pre_hashes
    assert receipt.pre_hashes["section:2"] == "", receipt.pre_hashes
    assert receipt.post_hashes["section:2"] != "", receipt.post_hashes

    # The created/removed/replaced footprints stay empty — the typed
    # RENUMBER footprint is the (from, to) pair in ``renumbered_paths``,
    # NOT a bogus empty-path tuple in ``replaced_paths`` (the bug signature
    # that ``test_se_replay_write_receipts_renumber_receipt_is_well_formed``
    # pins for SE).
    assert receipt.created_paths == ()
    assert receipt.removed_paths == ()
    assert receipt.replaced_paths == ()


def test_eu_emit_one_op_receipt_carries_empty_migration_rule_ids_for_replace() -> None:
    """§2.9 family-isolation (synthetic) — negative test for the
    ``migration_rule_ids`` stamping: a non-RENUMBER action (REPLACE) carries
    ``migration_rule_ids == ()`` because bound==landed for REPLACE/INSERT/
    REPEAL (no relabel migration), so ``divergence_explained`` is True via
    the equality short-circuit without a named rule.

    Drives a synthesized REPLACE op through ``apply_eu_ops`` once (to obtain
    the after-tree), then synthesizes the receipt via
    ``_eu_emit_one_op_receipt`` directly. Mirrors the SE/NO precedent's
    negative-test shape.
    """
    from lawvm.eu.pipeline import _eu_emit_one_op_receipt

    baseline = _baseline_statute()
    op = _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement")

    # Apply the single REPLACE op against the baseline to obtain the
    # after-tree.
    after_statute = apply_eu_ops(baseline, [op])
    after_body = after_statute.body

    receipt = _eu_emit_one_op_receipt(baseline.body, after_body, op)
    assert receipt is not None, (
        "_eu_emit_one_op_receipt returned None for an applied REPLACE op — "
        "the §2.9 family-isolation guard regressed (skip-path false-firing)."
    )

    assert receipt.action == "replace"
    assert receipt.op_id == "eu-replace-ok"
    # bound == landed for REPLACE → migration_rule_ids is ().
    assert receipt.bound_target_path == (("section", "1"),)
    assert receipt.landed_primary_path == (("section", "1"),)
    assert receipt.migration_rule_ids == (), receipt.migration_rule_ids
    assert receipt.divergence_explained is True  # via equality short-circuit
    # The REPLACE footprint lands in replaced_paths.
    assert receipt.replaced_paths, receipt.replaced_paths
    assert receipt.renumbered_paths == ()
    assert receipt.created_paths == ()
    assert receipt.removed_paths == ()
