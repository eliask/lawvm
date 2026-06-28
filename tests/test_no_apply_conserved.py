"""Fire-drill tests for the NO conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized statute with at least one skip through
:func:`apply_no_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_no_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

import io
import tarfile

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
from lawvm.norway.grafter import apply_no_ops, apply_no_ops_conserved, NOApplyResult
from lawvm.replay_adjudication import CompileAdjudication


def _statute_with_section(label: str = "2", text: str = "Original text.") -> IRStatute:
    return IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label=label, text=text),),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, label: str, source_id: str = "no/lovtid/2025-02-02-5") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),),
        ),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_no_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        # op #1 — succeeds: REPLACE §2 (target found in the statute body).
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        # op #2 — skipped: HEADING_REPLACE on §2 (NO replay supports only
        # REPLACE / REPEAL / INSERT / RENUMBER, so HEADING_REPLACE falls through
        # to the ``replay_unsupported_action`` skip path).
        LegalOperation(
            op_id="no-skip-unsupported-heading",
            sequence=2,
            action=StructuralAction.HEADING_REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]

    result = apply_no_ops_conserved(statute, ops)

    assert isinstance(result, NOApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §2 was replaced; the
    # HEADING_REPLACE op was skipped (action not supported by NO replay).
    assert result.statute.body is not statute.body

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "no-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "no-skip-unsupported-heading" in rejected_by_id
    skipped = rejected_by_id["no-skip-unsupported-heading"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "replay_unsupported_action"
    assert skipped.blocking is False  # NO conserved skips are recorded, not blocking

    # Partition is total (no silent drops, no phantoms). Accepted + rejected = input.
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_apply_no_ops_conserved_does_not_treat_recovery_as_skip() -> None:
    """§1.8: recovery adjudications (``no_replay_*``) record transformations
    that WERE applied — REPLACE recovered to INSERT, etc. — and must NOT mark
    their op as rejected. The partition uses SKIP_ADJUDICATION_KINDS only
    (``replay_unsupported_action`` / ``replay_unresolved_target`` /
    ``replay_noop``); recovery adjudications record the transformation
    alongside the accepted op, not as a rejection."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        # REPLACE §99 — section does not exist; NO recovers REPLACE→INSERT in
        # the inferred parent (top-level body root when the parent is None).
        # This is the documented ``no_replay_replace_recovered_by_insert``
        # recovery rule (lines ~3780-3821 of the bare variant): the op IS
        # applied, with a recovery adjudication.
        LegalOperation(
            op_id="no-replace-recovered-as-insert",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="Recovered as insert."),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops_conserved(statute, ops, adjudications_out=adjudications)

    # The bare variant emitted a recovery adjudication for the op...
    assert any(a.kind == "no_replay_replace_recovered_by_insert" for a in adjudications)
    # ...but the op was APPLIED (recovered to INSERT), so it is ACCEPTED, NOT
    # REJECTED. The recovery adjudication is part of the evidence ledger, not
    # the per-op skip partition.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "no-replace-recovered-as-insert"
    assert len(result.skipped_items) == 0


def _skip_op(*, op_id: str, sequence: int, label: str = "2", source_id: str = "no/lovtid/2025-02-02-5") -> LegalOperation:
    """Skip-path op: HEADING_REPLACE on a section target (action not in NO's
    supported set {REPLACE, REPEAL, INSERT, RENUMBER}); NO replay emits a
    ``replay_unsupported_action`` adjudication and skips the op."""
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.HEADING_REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_no_ops_conserved_statute_identical_to_bare_variant() -> None:
    """The conserved wrapper mirrors the bare variant — same replay output."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        _skip_op(op_id="no-skip-unsupported-heading", sequence=2),
    ]

    bare_adjudications: list[CompileAdjudication] = []
    bare_statute = apply_no_ops(statute, list(ops), adjudications_out=bare_adjudications)

    conserved = apply_no_ops_conserved(statute, ops)

    # The two statutes are byte-identical — same replay semantics, same op order.
    assert bare_statute == conserved.statute
    assert bare_statute.body is not statute.body  # both produced a new body
    # The conserved wrapper preserves the bare variant's adjudication ledger too.
    bare_kinds = {a.kind for a in bare_adjudications}
    assert "replay_unsupported_action" in bare_kinds  # one skip was emitted


def test_apply_no_ops_conserved_forwards_adjudications_out_when_passed() -> None:
    """When the caller passes an ``adjudications_out`` list, the conserved
    wrapper surfaces the bare variant's adjudications there too."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        _skip_op(op_id="no-skip-unsupported-heading", sequence=2),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops_conserved(statute, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "replay_unsupported_action"
    assert adjudications[0].op_id == "no-skip-unsupported-heading"
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 1


def test_apply_no_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """§1.8 conservation: a SKIPPED op with an empty op_id must NOT silently
    land in the accepted lane."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="99"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_no_ops_conserved(statute, ops)


def test_apply_no_ops_conserved_rejects_duplicate_op_ids() -> None:
    """§1.8 conservation: duplicate op_ids mis-partition. The conserved wrapper
    fails loud on duplicate op_ids rather than mis-partitioning."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="dup", sequence=1, label="2"),
        LegalOperation(
            op_id="dup",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="99"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_no_ops_conserved(statute, ops)


def test_apply_no_ops_conserved_forwarded_strict_flags_to_bare_variant() -> None:
    """The conserved wrapper forwards the bare variant's strict_* flags so
    existing strict-mode behaviour is preserved when production routes through
    the conserved path. The default values match the bare variant's defaults."""
    statute = _statute_with_section("2", "Original.")
    ops = [_replace_op(op_id="no-replace-ok", sequence=1, label="2")]

    result = apply_no_ops_conserved(
        statute,
        ops,
        strict_invariants=True,
        strict_action_family=False,
        strict_recovery=False,
    )

    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 0


# Minimal Norway base LTI XML mirroring ``test_norway_replay._BASE_XML`` — a
# Lovdata <html><body><main class="documentBody">…</main></body></html> shell
# with one chapter containing §1 and §2 (carrying a list of two items); this
# is what ``parse_no_statute`` ingests. Inlined here so this test file can
# drive ``replay_no_to_pit`` end-to-end without importing from a sibling test
# module.
_NO_FIRE_DRILL_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Testlov om data</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="LTI/lov/2025-01-01-1">
      <section class="section" data-name="kap1" data-lovdata-URL="LTI/lov/2025-01-01-1/KAPITTEL_1">
        <h2>Kapittel 1. Innledning</h2>
        <article class="legalArticle" data-name="§1" data-lovdata-URL="LTI/lov/2025-01-01-1/§1">
          <h3 class="legalArticleHeader">§ 1. Formaal</h3>
          <article class="legalP" id="ledd1">Loven gjelder testdata.</article>
        </article>
        <article class="legalArticle" data-name="§2" data-lovdata-URL="LTI/lov/2025-01-01-1/§2">
          <h3 class="legalArticleHeader">§ 2. Krav</h3>
          <article class="legalP" id="ledd1">Kravene er:</article>
        </article>
      </section>
    </main>
  </body>
</html>
""".encode("utf-8")


_NO_FIRE_DRILL_AMENDMENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§2/nummer/1">
        <article class="defaultP">I loven skal nr. 1 endres.</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _no_fire_drill_write_archive(archive_path, members) -> None:
    """Write tar.bz2 members to ``archive_path`` — mirrors
    ``test_norway_replay._write_archive``."""
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_replay_no_to_pit_routes_apply_through_conserved_wrapper(tmp_path, monkeypatch) -> None:
    """§2.9 guard-liveness fire-drill: the production lane ``replay_no_to_pit``
    MUST route the apply fold through ``apply_no_ops_conserved`` (not the bare
    ``apply_no_ops``) and surface the typed ``FilterResult`` on the
    ``NOReplayResult.apply_filter_result`` field.

    Pre-fix state: the conserved wrapper existed and was well-tested in
    isolation, but the production call site at ``norway/replay.py:405``
    invoked the bare ``apply_no_ops`` directly. That made the conserved
    wrapper UNREACHABLE from production — the §2.9 worst-class silent failure
    (a guard that exists but cannot fire from the production lane).

    Drives a synthesized op set through the FULL ``replay_no_to_pit`` path:

    * one REPLACE §2 op (succeeds — target is in the synthesized base body)
    * one HEADING_REPLACE op (skips — NO replay supports only
      REPLACE/REPEAL/INSERT/RENUMBER, so HEADING_REPLACE falls through to
      ``replay_unsupported_action`` and surfaces as a ``RejectedItem``)

    Mirrors ``test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id``
    (SE production fire-drill) in shape: drive-through + assertion that the
    typed receipt landed on the production result.
    """
    from lawvm.norway import replay as no_replay
    from lawvm.norway.grafter import apply_no_ops_conserved as real_apply_no_ops_conserved
    from lawvm.norway.replay import replay_no_to_pit

    archive_path = tmp_path / "lovtidend-avd1-2001-2025.tar.bz2"
    _no_fire_drill_write_archive(
        archive_path,
        [
            ("lti/2025/nl-20250101-001.xml", _NO_FIRE_DRILL_BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _NO_FIRE_DRILL_AMENDMENT_XML),
        ],
    )

    base_norm_id = "no/lov/2025-01-01-1"

    # Synthesized op set: one supported REPLACE on §2 (in the base body) +
    # one unsupported HEADING_REPLACE (NO replay emits a
    # ``replay_unsupported_action`` skip and surfaces it as a RejectedItem).
    synthesized_ops = [
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        LegalOperation(
            op_id="no-skip-unsupported-heading",
            sequence=2,
            action=StructuralAction.HEADING_REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    mocked_groups = [(base_norm_id, synthesized_ops)]

    # Spy: replace ``apply_no_ops_conserved`` in the replay module with a
    # wrapper that records the call and delegates to the real function. If
    # the production lane regresses to bare ``apply_no_ops``, the spy is
    # never invoked — the §2.9 worst-class silent failure.
    invocations: list[tuple] = []

    def spy_apply_no_ops_conserved(statute, ops, **kwargs):
        invocations.append((statute, list(ops), dict(kwargs)))
        return real_apply_no_ops_conserved(statute, ops, **kwargs)

    monkeypatch.setattr(
        no_replay,
        "iter_no_document_change_ops",
        lambda *a, **kw: mocked_groups,
    )
    monkeypatch.setattr(no_replay, "apply_no_ops_conserved", spy_apply_no_ops_conserved)

    result = replay_no_to_pit(base_norm_id, as_of="2025-02-15", data_dir=tmp_path)

    # The production lane routed through ``apply_no_ops_conserved`` — the spy
    # was invoked. If this assertion fails, the production call site has
    # regressed to bare ``apply_no_ops`` (the §2.9 worst-class silent failure).
    assert invocations, (
        "apply_no_ops_conserved was not invoked by the production lane — "
        "the production call site may have regressed to bare apply_no_ops "
        "(§2.9 worst-class silent failure: a guard that exists but is "
        "unreachable from production)."
    )
    assert result.error is None, f"replay_no_to_pit errored: {result.error!r}"

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
    assert rejected.item.op_id == "no-skip-unsupported-heading"
    assert rejected.reason_code == "replay_unsupported_action"
    assert rejected.reason  # message forwarded from the bare variant's adjudication
    assert rejected.blocking is False  # NO conserved skips are recorded, not blocking

    # Accepted lane carries the §2 op; conservation partition is total.
    accepted_ids = {op.op_id for op in result.apply_filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.apply_filter_result.rejected_items}
    input_ids = {op.op_id for op in synthesized_ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def _insert_op_into_existing_section(
    *,
    op_id: str,
    sequence: int,
    label: str,
    source_id: str = "no/lovtid/2025-02-02-5",
) -> LegalOperation:
    """An INSERT op whose target path already resolves to an existing section
    node in the base statute — this is the trigger for the
    ``no_replay_insert_occupied_target_replaced`` action-family recovery in
    the bare :func:`apply_no_ops` (lines ~3877-3895). Under
    ``strict_action_family=True`` the bare variant raises mid-apply AFTER
    appending the recovery adjudication witness; the conserved wrapper must
    preserve that witness on the caller's accumulator (the §2.3 propagation-
    on-raise contract)."""
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text="Okkupert bytte."),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_no_ops_conserved_propagates_recovery_adjudication_on_raise() -> None:
    """§2.9 + §1.0 guard-liveness: when the bare :func:`apply_no_ops` raises
    mid-apply under ``strict_action_family=True`` (the §1.10 fail-loud path
    for the occupied-insert action-family recovery collision), the conserved
    wrapper MUST preserve the recovery adjudication witness that bare apply
    emitted BEFORE the raise on the caller's ``adjudications_out``
    accumulator. Without that propagation the caller's evidence ledger is
    silently destroyed — exactly the §1.0 "evidence is not silently
    destroyed" failure mode that ``test_replay_no_to_pit_strict_action_family_rejects_recovery``
    surfaced when the conserved wrapper used a local-copy pattern that did
    ``adjudications = list(adjudications_out or [])`` then
    ``adjudications_out.clear(); adjudications_out.extend(adjudications)``
    AFTER bare apply returned — the clear/extend round-trip never ran when
    bare apply raised.

    Drives a synthesized INSERT op into an occupied §1 target through
    :func:`apply_no_ops_conserved` with ``strict_action_family=True`` and
    asserts:

    * the wrapper RAISES ``ValueError`` (preserving the §1.10 fail-loud
      contract of the bare variant — strict mode is not silently downgraded
      by the conserved wrapper);
    * the caller's ``adjudications_out`` list retains the
      ``no_replay_insert_occupied_target_replaced`` recovery adjudication
      witness emitted by bare apply BEFORE the raise (the §1.0 evidence is
      not silently destroyed); the witness carries the
      ``no_insert_occupied_target_replace`` rule_id so a downstream consumer
      can diagnose the recovery collision.

    Mirrors ``test_apply_no_ops_conserved_does_not_treat_recovery_as_skip``
    (the non-strict-mode recovery test) in op set shape. Mirrors
    ``test_replay_no_to_pit_strict_action_family_rejects_recovery`` in
    end-to-end assertion shape — the production-lane test pins the same
    propagation contract through ``replay_no_to_pit``; this test pins it at
    the conserved-wrapper layer with a synthesized op set so the contract
    is reachable from a focused unit test too (the §2.9 "guard-liveness"
    discipline — every guard needs a test that drives a known-violating
    input through the full production path; here the wrapper IS the
    production path for the apply fold).
    """
    statute = _statute_with_section("1", "Original §1.")
    ops = [
        _insert_op_into_existing_section(op_id="no-insert-occupied-1", sequence=1, label="1"),
    ]
    adjudications: list[CompileAdjudication] = []

    # The wrapper raises mid-apply — the recovery adjudication witness is
    # emitted by bare apply BEFORE the raise, and the wrapper's propagation
    # contract must NOT silently destroy it.
    with pytest.raises(ValueError, match="action-family recovery"):
        apply_no_ops_conserved(
            statute,
            ops,
            adjudications_out=adjudications,
            strict_action_family=True,
        )

    # §1.0 + §2.9 propagation-on-raise contract: the caller's
    # ``adjudications`` list (the same reference passed in as
    # ``adjudications_out``) retains the recovery adjudication witness
    # emitted by bare apply BEFORE the raise. Pre-fix the list was empty
    # (bare apply raised, the local-copy pattern's clear/extend round-trip
    # never ran, the caller's accumulator stayed at its initial []).
    assert len(adjudications) >= 1, (
        "adjudications_out is empty under strict_action_family=True raise — "
        "the conserved wrapper did NOT propagate the recovery adjudication "
        "witness emitted by bare apply before the raise (§1.0 evidence-loss "
        "failure; the production-lane test_replay_no_to_pit_strict_action_"
        "family_rejects_recovery pinned the same hole end-to-end)."
    )
    recovery = adjudications[0]
    assert recovery.kind == "no_replay_insert_occupied_target_replaced"
    assert recovery.op_id == "no-insert-occupied-1"
    assert recovery.detail["rule_id"] == "no_insert_occupied_target_replace"
    assert recovery.detail["original_action"] == "insert"
    assert recovery.detail["executed_action"] == "replace"
    # Bare apply appended in place via ``_append_no_replay_adjudication``
    # (``.append`` on the caller's list reference); the propagation-on-raise
    # fix routes the caller's list directly through bare apply so a raise
    # does not silently destroy the witness (see the inline comment at
    # ``apply_no_ops_conserved`` lines ~4175-4219).
