"""Conformance test for the θ (theta) TotalizationTable (#186, §2.3).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3 + §7 delta #2.

WHAT THIS GUARDS. The θ tables in ``sweden/totalization_table.py`` (strict) and
``norway/totalization_table.py`` (rich recovery) are a DECLARED SPEC of the
grafters' current off-domain behaviour. This test makes θ *first-class* WITHOUT a
control-flow refactor: for each declared ``(action, failure_class)`` cell, it
constructs a minimal op that hits that precondition failure, runs it through the
REAL conserved apply path (``apply_se_ops_conserved`` / ``apply_no_ops_conserved``
— the same path production replay uses), and asserts the OBSERVED runtime
disposition equals what the table declares:

* ``Reject(code)``          → the op lands in the REJECTED lane with ``reason_code == code``.
* ``NoopIdempotent(code)``  → same (a no-op is rejected-lane today; ``code`` matches).
* ``Recover(rule_id, act)`` → the op is ACCEPTED (it landed a write) AND the
  recovery adjudication carrying ``detail["rule_id"] == rule_id`` was emitted.

If a grafter's off-domain behaviour later drifts from the declared table (a code
renamed, a recovery removed, a reject flipped to a recover), THIS test FAILS —
which is exactly what makes the parallel-first table a faithful spec rather than
dead documentation. It also pins the two core-type invariants: construction
rejects an empty recovery rule_id, and ``lookup`` falls back to ``default``.

PARALLEL-FIRST. The grafter control flow is NOT yet routed through the table;
the load-bearing routing (making θ the single source of the off-domain
disposition) is the deferred follow-up. This test is the guardrail that keeps
the declared table honest until that routing lands.
"""

from __future__ import annotations

import pytest

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Recover,
    Reject,
    TotalizationTable,
)
from lawvm.core.statute_facets import statute_title_address
from lawvm.estonia.grafter import apply_ee_ops_conserved
from lawvm.estonia.totalization_table import EE_TOTALIZATION_TABLE
from lawvm.norway.grafter import apply_no_ops_conserved
from lawvm.norway.totalization_table import NO_TOTALIZATION_TABLE
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.grafter import apply_se_ops_conserved
from lawvm.sweden.totalization_table import SE_TOTALIZATION_TABLE
from lawvm.uk_legislation.replay_conserved import replay_uk_ops_conserved
from lawvm.uk_legislation.totalization_table import UK_TOTALIZATION_TABLE


# ---------------------------------------------------------------------------
# Statute + op builders (minimal, hitting one off-domain cell each)
# ---------------------------------------------------------------------------


def _section(label: str, text: str = "Original.") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _statute(*, statute_id: str, sections: tuple[IRNode, ...]) -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="Conformance base",
        body=IRNode(kind=IRNodeKind.BODY, children=sections),
    )


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _op(
    *,
    op_id: str,
    action: StructuralAction,
    label: str,
    payload: IRNode | None = None,
    destination: LegalAddress | None = None,
    source_id: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=action,
        target=_section_addr(label),
        payload=payload,
        destination=destination,
        source=OperationSource(statute_id=source_id),
    )


# A frozen carrier for the observed runtime disposition of one op, so the two
# frontend drivers report the SAME shape the assertions compare against.
def _observe_se(statute: IRStatute, op: LegalOperation) -> tuple[str, list[CompileAdjudication]]:
    """Run one op through SE's real conserved apply path; return
    ("accepted"/reason_code, adjudications)."""
    adjudications: list[CompileAdjudication] = []
    result = apply_se_ops_conserved(statute, [op], adjudications_out=adjudications)
    if result.skipped_items:
        return result.skipped_items[0].reason_code, adjudications
    return "accepted", adjudications


def _observe_no(statute: IRStatute, op: LegalOperation) -> tuple[str, list[CompileAdjudication]]:
    """Run one op through NO's real conserved apply path; return
    ("accepted"/reason_code, adjudications)."""
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops_conserved(statute, [op], adjudications_out=adjudications)
    if result.skipped_items:
        return result.skipped_items[0].reason_code, adjudications
    return "accepted", adjudications


# ---------------------------------------------------------------------------
# SE — the strict/REJECT baseline: each declared cell rejects with its code.
# ---------------------------------------------------------------------------

_SE_ID = "se/1999:999"
_SE_AMEND = "se/amend"


def test_se_replace_target_absent_rejects_as_declared() -> None:
    """(REPLACE, target_absent) → Reject(se_replay_target_not_found)."""
    cell = (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_target_not_found"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"),))
    op = _op(
        op_id="se-replace-absent",
        action=StructuralAction.REPLACE,
        label="99",  # not present in the body → target absent
        payload=_section("99", "Ny."),
        source_id=_SE_AMEND,
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_insert_target_occupied_rejects_as_declared() -> None:
    """(INSERT, target_occupied) → Reject(se_replay_unsupported_action)."""
    cell = (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_unsupported_action"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"),))
    op = _op(
        op_id="se-insert-occupied",
        action=StructuralAction.INSERT,
        label="5",  # already occupied
        payload=_section("5", "Duplicate."),
        source_id=_SE_AMEND,
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_repeal_target_absent_rejects_as_declared() -> None:
    """(REPEAL, target_absent) → Reject(se_replay_target_not_found)."""
    cell = (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_target_not_found"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"),))
    op = LegalOperation(
        op_id="se-repeal-absent",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=_section_addr("99"),  # not present
        source=OperationSource(statute_id=_SE_AMEND),
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_renumber_dest_occupied_rejects_as_declared() -> None:
    """(RENUMBER, dest_occupied) → Reject(se_replay_renumber_collision)."""
    cell = (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_renumber_collision"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"), _section("6")))
    op = LegalOperation(
        op_id="se-renumber-collision",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=_section_addr("5"),
        destination=_section_addr("6"),  # 6 already occupied
        source=OperationSource(statute_id=_SE_AMEND),
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_renumber_source_absent_rejects_as_declared() -> None:
    """(RENUMBER, target_absent) → Reject(se_replay_target_not_found)."""
    cell = (StructuralAction.RENUMBER, FailureClass.TARGET_ABSENT)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_target_not_found"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"),))
    op = LegalOperation(
        op_id="se-renumber-src-absent",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=_section_addr("99"),  # source not present
        destination=_section_addr("8"),
        source=OperationSource(statute_id=_SE_AMEND),
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_replace_payload_missing_rejects_as_declared() -> None:
    """(REPLACE, payload_missing) → Reject(se_replay_payload_missing)."""
    cell = (StructuralAction.REPLACE, FailureClass.PAYLOAD_MISSING)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "se_replay_payload_missing"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5"),))
    op = LegalOperation(
        op_id="se-replace-no-payload",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_section_addr("5"),
        payload=None,  # missing payload
        source=OperationSource(statute_id=_SE_AMEND),
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_replace_content_identical_is_noop_as_declared() -> None:
    """(REPLACE, content_identical) → NoopIdempotent(se_replay_noop)."""
    cell = (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL)
    declared = SE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, NoopIdempotent) and declared.code == "se_replay_noop"

    statute = _statute(statute_id=_SE_ID, sections=(_section("5", "Unchanged."),))
    op = _op(
        op_id="se-replace-noop",
        action=StructuralAction.REPLACE,
        label="5",
        payload=_section("5", "Unchanged."),  # byte-identical → no write
        source_id=_SE_AMEND,
    )
    observed, _ = _observe_se(statute, op)
    assert observed == declared.code


def test_se_default_is_strict_reject() -> None:
    """§2.3 default: SE's unlisted cells reject (the strict default)."""
    assert isinstance(SE_TOTALIZATION_TABLE.default, Reject)
    assert SE_TOTALIZATION_TABLE.default.code == "se_replay_skipped_unspecified"
    # An unlisted cell (e.g. REPEAL, dest_occupied is not a real SE lane) falls
    # back to the strict default rather than raising.
    fallback = SE_TOTALIZATION_TABLE.lookup(
        StructuralAction.REPEAL, FailureClass.DEST_OCCUPIED
    )
    assert fallback is SE_TOTALIZATION_TABLE.default


# ---------------------------------------------------------------------------
# NO — the rich recovery table: recover / reject / noop cells all bound.
# ---------------------------------------------------------------------------

_NO_ID = "no/lov/2025-01-01-1"
_NO_AMEND = "no/lovtid/2025-02-02-5"


def _no_recovery_rule_ids(adjudications: list[CompileAdjudication]) -> set[str]:
    return {
        str(a.detail.get("rule_id", ""))
        for a in adjudications
        if a.kind.startswith("no_replay_")
    }


def test_no_insert_target_occupied_recovers_to_replace_as_declared() -> None:
    """(INSERT, target_occupied) → Recover(no_insert_occupied_target_replace, REPLACE)."""
    cell = (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED)
    declared = NO_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Recover)
    assert declared.rule_id == "no_insert_occupied_target_replace"
    assert declared.rewritten_action is StructuralAction.REPLACE

    statute = _statute(statute_id=_NO_ID, sections=(_section("1", "Original §1."),))
    op = _op(
        op_id="no-insert-occupied",
        action=StructuralAction.INSERT,
        label="1",  # occupied → NO recovers by REPLACE
        payload=_section("1", "Okkupert bytte."),
        source_id=_NO_AMEND,
    )
    observed, adjudications = _observe_no(statute, op)
    # RECOVER: the op is ACCEPTED (it landed a write) and the recovery rule_id
    # was witnessed on the adjudication ledger.
    assert observed == "accepted"
    assert declared.rule_id in _no_recovery_rule_ids(adjudications)


def test_no_replace_target_absent_recovers_to_insert_as_declared() -> None:
    """(REPLACE, target_absent) → Recover(no_replace_missing_section_insert, INSERT)."""
    cell = (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT)
    declared = NO_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Recover)
    assert declared.rule_id == "no_replace_missing_section_insert"
    assert declared.rewritten_action is StructuralAction.INSERT

    statute = _statute(statute_id=_NO_ID, sections=(_section("2", "Original §2."),))
    op = _op(
        op_id="no-replace-absent",
        action=StructuralAction.REPLACE,
        label="99",  # absent → NO recovers by INSERT at the body root
        payload=_section("99", "Recovered as insert."),
        source_id=_NO_AMEND,
    )
    observed, adjudications = _observe_no(statute, op)
    assert observed == "accepted"
    assert declared.rule_id in _no_recovery_rule_ids(adjudications)


def test_no_renumber_dest_occupied_recovers_by_removing_occupant_as_declared() -> None:
    """(RENUMBER, dest_occupied) → Recover(no_renumber_occupied_destination_removed, RENUMBER)."""
    cell = (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED)
    declared = NO_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Recover)
    assert declared.rule_id == "no_renumber_occupied_destination_removed"
    assert declared.rewritten_action is StructuralAction.RENUMBER

    statute = _statute(
        statute_id=_NO_ID, sections=(_section("5", "Kilde."), _section("6", "Okkupant."))
    )
    op = LegalOperation(
        op_id="no-renumber-occupied-dest",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=_section_addr("5"),
        destination=_section_addr("6"),  # 6 occupied → NO removes occupant, proceeds
        source=OperationSource(statute_id=_NO_AMEND),
    )
    observed, adjudications = _observe_no(statute, op)
    assert observed == "accepted"
    assert declared.rule_id in _no_recovery_rule_ids(adjudications)


def test_no_repeal_target_absent_rejects_as_declared() -> None:
    """(REPEAL, target_absent) → Reject(replay_unresolved_target). NO does NOT
    recover REPEAL — the address is simply unresolvable."""
    cell = (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT)
    declared = NO_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject) and declared.code == "replay_unresolved_target"

    statute = _statute(statute_id=_NO_ID, sections=(_section("2", "Original §2."),))
    op = LegalOperation(
        op_id="no-repeal-absent",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=_section_addr("99"),  # absent
        source=OperationSource(statute_id=_NO_AMEND),
    )
    observed, _ = _observe_no(statute, op)
    assert observed == declared.code


def test_no_replace_content_identical_is_noop_as_declared() -> None:
    """(REPLACE, content_identical) → NoopIdempotent(replay_noop). The I1-strong
    conservation cell: a content-identical REPLACE lands no write and is
    rejected as replay_noop (the #186 NO conservation fix)."""
    cell = (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL)
    declared = NO_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, NoopIdempotent) and declared.code == "replay_noop"

    live = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Uendret tittel"),
            IRNode(kind=IRNodeKind.PARAGRAPH, text="Uendret tekst."),
        ),
    )
    statute = _statute(statute_id=_NO_ID, sections=(live,))
    op = LegalOperation(
        op_id="no-replace-noop",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_section_addr("5"),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="5",
            children=(
                IRNode(kind=IRNodeKind.HEADING, text="Uendret tittel"),
                IRNode(kind=IRNodeKind.PARAGRAPH, text="Uendret tekst."),
            ),
        ),  # byte-identical → no write
        source=OperationSource(statute_id=_NO_AMEND),
    )
    observed, _ = _observe_no(statute, op)
    assert observed == declared.code


def test_no_default_is_strict_reject() -> None:
    """§2.3 default: NO's unlisted cells reject (the strict default)."""
    assert isinstance(NO_TOTALIZATION_TABLE.default, Reject)
    assert NO_TOTALIZATION_TABLE.default.code == "replay_unresolved_target"
    fallback = NO_TOTALIZATION_TABLE.lookup(
        StructuralAction.META, FailureClass.PARENT_UNRESOLVED
    )
    assert fallback is NO_TOTALIZATION_TABLE.default


# ---------------------------------------------------------------------------
# EE — the §2.3 silent-noop motivating case, now the strict θ table (#186).
#
# EE, like SE, NEVER recovers: every off-domain lane is a typed Reject or an
# idempotent Noop. Each declared cell is driven through the REAL conserved apply
# path (``apply_ee_ops_conserved``) and asserted to reject with its declared
# code — the load-bearing routing (``estonia/grafter.py`` dispatches on
# ``EE_TOTALIZATION_TABLE.lookup``) is guarded byte-identical by these cases.
# ---------------------------------------------------------------------------

_EE_ID = "ee/RT-I-2020-01"
_EE_AMEND = "ee/RT-I-2020-02"
_EE_UNPARSED_OPERATION_CLAUSE_RULE = "ee_unparsed_operation_clause"


def _observe_ee(
    statute: IRStatute, op: LegalOperation
) -> tuple[str, list[CompileAdjudication]]:
    """Run one op through EE's real conserved apply path; return
    ("accepted"/reason_code, adjudications)."""
    adjudications: list[CompileAdjudication] = []
    result = apply_ee_ops_conserved(statute, [op], adjudications_out=adjudications)
    if result.skipped_items:
        return result.skipped_items[0].reason_code, adjudications
    return "accepted", adjudications


def _ee_meta_op(
    *, op_id: str, source_family: str | None = None
) -> LegalOperation:
    """A META op (non-body) — optionally tagged with a source_family so it hits
    the unparsed-operation-clause lane."""
    attrs = {"source_family": source_family} if source_family else {}
    payload = IRNode(kind=IRNodeKind.PARAGRAPH, text="Meta.", attrs=attrs)
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.META,
        target=LegalAddress(path=(("note", "x"),)),
        payload=payload,
        source=OperationSource(statute_id=_EE_AMEND),
    )


def test_ee_unparsed_operation_rejects_as_declared() -> None:
    """(META, unparsed_operation) → Reject(ee_replay_unparsed_operation_skipped)."""
    cell = (StructuralAction.META, FailureClass.UNPARSED_OPERATION)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "ee_replay_unparsed_operation_skipped"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1"),))
    op = _ee_meta_op(
        op_id="ee-unparsed",
        source_family=_EE_UNPARSED_OPERATION_CLAUSE_RULE,
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_meta_non_body_rejects_as_declared() -> None:
    """(META, meta_non_body) → Reject(ee_replay_meta_non_body_skipped)."""
    cell = (StructuralAction.META, FailureClass.META_NON_BODY)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "ee_replay_meta_non_body_skipped"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1"),))
    op = _ee_meta_op(op_id="ee-meta-non-body")
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_unsupported_statute_title_action_rejects_as_declared() -> None:
    """(META, statute_title_unsupported) → Reject(ee_replay_unsupported_statute_title_action)."""
    cell = (StructuralAction.META, FailureClass.STATUTE_TITLE_UNSUPPORTED)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "ee_replay_unsupported_statute_title_action"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1"),))
    # A statute-title-address op whose action is REPEAL (not a title REPLACE):
    # the grafter's ``action != "replace"`` branch fires the unsupported lane.
    op = LegalOperation(
        op_id="ee-title-unsupported",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=statute_title_address(),
        source=OperationSource(statute_id=_EE_AMEND),
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_statute_title_noop_is_noop_as_declared() -> None:
    """(REPLACE, statute_title_unchanged) → NoopIdempotent(ee_replay_statute_title_noop)."""
    cell = (StructuralAction.REPLACE, FailureClass.STATUTE_TITLE_UNCHANGED)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, NoopIdempotent)
    assert declared.code == "ee_replay_statute_title_noop"

    statute = IRStatute(
        statute_id=_EE_ID,
        title="Unchanged title",
        body=IRNode(kind=IRNodeKind.BODY, children=(_section("1"),)),
    )
    # A title REPLACE whose payload text equals the live title → no title change.
    op = LegalOperation(
        op_id="ee-title-noop",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=statute_title_address(),
        payload=IRNode(kind=IRNodeKind.HEADING, text="Unchanged title"),
        source=OperationSource(statute_id=_EE_AMEND),
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_unsupported_action_rejects_as_declared() -> None:
    """(META, unsupported_action) → Reject(ee_replay_unsupported_action)."""
    cell = (StructuralAction.META, FailureClass.UNSUPPORTED_ACTION)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "ee_replay_unsupported_action"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1"),))
    # HEADING_REPLACE is not in EE's routable action set
    # (replace/repeal/insert/renumber/text_replace) and is not a META /
    # statute-title op, so the grafter's unsupported-action lane fires.
    op = LegalOperation(
        op_id="ee-unsupported-action",
        sequence=1,
        action=StructuralAction.HEADING_REPLACE,
        target=_section_addr("1"),
        source=OperationSource(statute_id=_EE_AMEND),
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_target_absent_rejects_as_declared() -> None:
    """(REPLACE, target_absent) → Reject(ee_replay_target_not_found)."""
    cell = (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "ee_replay_target_not_found"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1"),))
    op = _op(
        op_id="ee-replace-absent",
        action=StructuralAction.REPLACE,
        label="99",  # absent → target not found
        payload=_section("99", "Uus."),
        source_id=_EE_AMEND,
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_content_identical_is_noop_as_declared() -> None:
    """(REPLACE, content_identical) → NoopIdempotent(ee_replay_noop). The #185
    I1-strong conservation cell: a content-identical REPLACE lands no write and
    is rejected as ee_replay_noop via the content-footprint applied signal."""
    cell = (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL)
    declared = EE_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, NoopIdempotent)
    assert declared.code == "ee_replay_noop"

    statute = _statute(statute_id=_EE_ID, sections=(_section("1", "Muutmata."),))
    op = _op(
        op_id="ee-replace-noop",
        action=StructuralAction.REPLACE,
        label="1",
        payload=_section("1", "Muutmata."),  # content-identical → no write
        source_id=_EE_AMEND,
    )
    observed, _ = _observe_ee(statute, op)
    assert observed == declared.code


def test_ee_default_is_strict_reject() -> None:
    """§2.3 default: EE's unlisted cells reject (the strict default)."""
    assert isinstance(EE_TOTALIZATION_TABLE.default, Reject)
    assert EE_TOTALIZATION_TABLE.default.code == "ee_replay_skipped_unspecified"
    # An unlisted cell falls back to the strict default rather than raising.
    fallback = EE_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED
    )
    assert fallback is EE_TOTALIZATION_TABLE.default


# ---------------------------------------------------------------------------
# UK — the seam-sourced I1 ✓seam frontend, now the strict θ table (#186).
#
# Unlike NO/SE/EE, the UK's conservation partition is SEAM-SOURCED, not
# adjudication-keyed: a prepared op that landed no write is conserved under the
# UNIFORM reason code ``uk_apply_no_write`` (the table DEFAULT), regardless of
# which descriptive ``uk_replay_*`` adjudication narrated the miss; and the UK
# RECOVERS aggressively (a missing-leaf REPLACE materializes as an INSERT), so
# ``TARGET_ABSENT`` on a REPLACE is *accepted*, never a reject cell. The one
# explicit reject cell is the whole-act action-admissibility filter. Each cell is
# driven through the REAL conserved apply path (``replay_uk_ops_conserved`` — the
# path production replay uses) and asserted to conserve with its declared code.
# ---------------------------------------------------------------------------

_UK_ID = "ukpga/2020/1"
_UK_AMEND = "ukpga/2020/2"


def _uk_section(label: str, text: str = "Original.") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _uk_statute(*, statute_id: str = _UK_ID) -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="UK conformance base",
        body=IRNode(kind=IRNodeKind.BODY, children=(_uk_section("1"),)),
    )


def _observe_uk(
    statute: IRStatute, op: LegalOperation
) -> tuple[str, list[CompileAdjudication]]:
    """Run one op through the UK's real conserved apply path; return
    ("accepted"/reason_code, adjudications). The reason_code is the SEAM-SOURCED
    conserved ``RejectedItem.reason_code`` for an apply-skip, or the prepare
    adjudication kind for a prepare-filtered op."""
    adjudications: list[CompileAdjudication] = []
    result = replay_uk_ops_conserved(statute, [op], adjudications_out=adjudications)
    if result.skipped_items:
        return result.skipped_items[0].reason_code, adjudications
    return "accepted", adjudications


def test_uk_whole_act_unsupported_action_rejects_as_declared() -> None:
    """(META, unsupported_action) → Reject(uk_replay_unsupported_action).

    A whole-act-facet op whose action is neither a whole-act REPEAL nor the
    recognized whole-act text substitution is filtered at prepare time and
    conserved under the declared code (``replay_prepare.py`` routes through the
    table's action-admissibility cell)."""
    cell = (StructuralAction.META, FailureClass.UNSUPPORTED_ACTION)
    declared = UK_TOTALIZATION_TABLE.lookup(*cell)
    assert isinstance(declared, Reject)
    assert declared.code == "uk_replay_unsupported_action"

    op = LegalOperation(
        op_id="uk-whole-act-unsupported",
        sequence=1,
        action=StructuralAction.REPLACE,  # not a whole-act repeal / text sub
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        payload=_uk_section("1", "New."),
        source=OperationSource(statute_id=_UK_AMEND),
    )
    observed, adjudications = _observe_uk(_uk_statute(), op)
    assert observed == declared.code
    # The prepare filter emits the same owned adjudication kind.
    assert declared.code in {a.kind for a in adjudications}


def test_uk_apply_no_write_default_is_seam_sourced() -> None:
    """§2.3 strict default: a prepared op that lands no write (and is not
    recovered) is conserved under the UNIFORM seam reason code — the table
    DEFAULT — regardless of the descriptive adjudication that narrated it. Here a
    REPEAL of an absent target lands no write and conserves as ``uk_apply_no_write``
    while ``adjudications_out`` narrates it as ``uk_replay_target_not_found``."""
    assert isinstance(UK_TOTALIZATION_TABLE.default, Reject)
    assert UK_TOTALIZATION_TABLE.default.code == "uk_apply_no_write"

    op = LegalOperation(
        op_id="uk-repeal-absent",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "99"),)),  # absent → no write
        source=OperationSource(statute_id=_UK_AMEND),
    )
    observed, adjudications = _observe_uk(_uk_statute(), op)
    assert observed == UK_TOTALIZATION_TABLE.default.code
    # The seam-sourced default is uniform across the descriptive misses: the
    # narrating adjudication kind is distinct from the conserved reason code.
    assert "uk_replay_target_not_found" in {a.kind for a in adjudications}


def test_uk_default_lookup_falls_back() -> None:
    """§2.3: any unlisted cell falls back to the strict (seam-sourced) default."""
    fallback = UK_TOTALIZATION_TABLE.lookup(
        StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED
    )
    assert fallback is UK_TOTALIZATION_TABLE.default


def test_uk_recovers_missing_leaf_replace_not_a_reject_cell() -> None:
    """The UK RECOVERS a missing-leaf REPLACE (materializes as INSERT), so
    (REPLACE, target_absent) is ACCEPTED — NOT a reject cell. This pins the UK's
    recovering character: it is why the table has no ``TARGET_ABSENT`` reject row
    (contrast SE/EE, which reject it)."""
    op = LegalOperation(
        op_id="uk-replace-absent-recovered",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "99"),)),
        payload=_uk_section("99", "New."),
        source=OperationSource(statute_id=_UK_AMEND),
    )
    observed, _ = _observe_uk(_uk_statute(), op)
    assert observed == "accepted"


# ---------------------------------------------------------------------------
# Core θ type invariants (jurisdiction-neutral)
# ---------------------------------------------------------------------------


def test_recover_requires_non_empty_rule_id() -> None:
    """A recovery must be witnessed by a non-empty rule_id (§2.3)."""
    with pytest.raises(ValueError, match="non-empty recovery rule_id"):
        Recover(rule_id="", rewritten_action=StructuralAction.REPLACE)


def test_table_construction_rejects_empty_recovery_rule_id() -> None:
    """Construction validates every RECOVER row's rule_id is non-empty.

    Even if a caller smuggled a rule-id-less recovery in through
    ``object.__setattr__`` (bypassing ``Recover.__post_init__``), the table
    constructor re-asserts the invariant on every row and the default."""
    smuggled = Recover.__new__(Recover)
    object.__setattr__(smuggled, "rule_id", "")
    object.__setattr__(smuggled, "rewritten_action", StructuralAction.INSERT)
    with pytest.raises(ValueError, match="empty rule_id"):
        TotalizationTable(
            jurisdiction="x",
            rows={(StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): smuggled},
        )


def test_reject_requires_non_empty_code() -> None:
    with pytest.raises(ValueError, match="non-empty rejection code"):
        Reject("")


def test_lookup_falls_back_to_default_for_unlisted_cell() -> None:
    """An unlisted cell resolves to ``default`` (the table is total)."""
    table = TotalizationTable(
        jurisdiction="x",
        rows={
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): Reject("x_absent"),
        },
        default=Reject("x_default"),
    )
    # Declared cell → its row.
    listed = table.lookup(StructuralAction.REPLACE, FailureClass.TARGET_ABSENT)
    assert isinstance(listed, Reject) and listed.code == "x_absent"
    # Unlisted cell → the default.
    unlisted = table.lookup(StructuralAction.INSERT, FailureClass.DEST_OCCUPIED)
    assert unlisted is table.default


def test_table_rejects_empty_jurisdiction() -> None:
    with pytest.raises(ValueError, match="non-empty jurisdiction"):
        TotalizationTable(jurisdiction="", rows={})
