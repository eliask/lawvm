"""The θ (theta) TotalizationTable — a first-class, typed totalization policy.

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3
("Totalization: the real per-jurisdiction semantic parameter") and §7 delta #2
("θ TotalizationTable on the profile … the biggest semantic unification").

WHAT THIS IS. Every kernel operation ``⟦op⟧ : Σ ⇀ Σ`` is a **partial** function:
its precondition defines its domain (§2.1), and §2.3 defines what happens
*outside* the domain. Each frontend independently decided its off-domain
behaviour, and that — NOT the shared eight-member action vocabulary — is where
the grafters diverge hardest. Formally, a frontend supplies a **totalization**::

    θ : (StructuralAction, FailureClass) → Disposition

    Disposition ∈ { Reject(code)
                  , NoopIdempotent(code)
                  , Recover(rule_id, rewritten_action) }

with the §2.3 STRICT DEFAULT being ``Reject`` (SE's stance), recoveries opt-in
and each RECOVER row naming a ``recovery_rule_id`` that the WriteReceipt /
adjudication ledger cites (NO/SE receipts already carry
``recovery_rule_ids`` / ``migration_rule_ids``).

WHAT THIS IS NOT (this increment). This module is a DECLARED, CONFORMANCE-TESTED
SPEC of the grafters' *current* off-domain behaviour. It is **parallel-first**:
the grafter control flow is NOT yet routed through the table — that load-bearing
step (making θ the single source of the off-domain disposition, replacing the
scattered implicit control flow across five grafters) is an explicit follow-up,
the same discipline CTSF used (``core/ctsf_residual_report``). The conformance
test (``tests/test_totalization_conformance.py``) binds each declared cell to the
ACTUAL runtime disposition, so the table is a *faithful* spec and will FAIL if a
grafter's off-domain behaviour later drifts from the declaration.

PLANE & DISCIPLINE (AGENTS.md §0-§2). Jurisdiction-neutral: this core module
imports NO jurisdiction package — the per-frontend θ tables live in the frontend
packages (``sweden/``, ``norway/``) and import this neutral type, mirroring the
spec-ledger's lazy self-registration registry (the kernel must not import
frontends). Typed, frozen, deterministic; fail-loud on shape-invalid input
(a RECOVER row with an empty ``rule_id`` raises at construction).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from lawvm.core.semantic_types import StructuralAction

__all__ = [
    "FailureClass",
    "Reject",
    "NoopIdempotent",
    "Recover",
    "Disposition",
    "TotalizationTable",
]


class FailureClass(str, Enum):
    """The closed vocabulary of precondition-failure classes (§2.3).

    A ``FailureClass`` names WHY an op fell outside its precondition domain
    (§2.1). The members enumerated here are exactly the classes the real
    grafters exhibit today (grounded in ``norway/grafter.py`` /
    ``sweden/grafter.py`` off-domain lanes), not a speculative superset:

    * ``target_absent`` — the op's target address does not resolve
      (NO REPLACE→INSERT recovery / REPEAL skip; SE ``se_replay_target_not_found``).
    * ``target_occupied`` — INSERT's target already resolves to a live slot
      (NO INSERT→REPLACE recovery; SE ``se_replay_unsupported_action`` on an
      occupied INSERT).
    * ``dest_occupied`` — RENUMBER's destination label is already occupied by a
      slot not vacated by the same renumber group (NO remove-occupant recovery;
      SE ``se_replay_renumber_collision``).
    * ``payload_missing`` — a structural op that needs a payload carries none, or
      the wrong kind (SE ``se_replay_payload_missing``).
    * ``selector_no_match`` — a TEXT_REPLACE selector finds no match in the
      target subtree (SE ``se_replay_text_replace_no_match``).
    * ``parent_unresolved`` — an INSERT's parent chain does not resolve (the
      container-chain miss lane).
    * ``content_identical`` — the op resolves and applies but lands NO content
      write (a byte-identical no-op; NO/SE ``*_noop`` — the I1-strong "accepted
      ⟺ landed a write" conservation cell).

    The §2.3 note names the vocabulary as ``{target_absent, target_occupied,
    dest_occupied, payload_missing, selector_no_match, parent_unresolved, …}``
    — universal but explicitly *extensible* (the trailing ``…``). EE's off-domain
    lanes (routed through the θ table in #186) exhibit precondition failures that
    are not a supported action's precondition miss but an ACTION-ADMISSIBILITY
    failure (the instruction never routes to a kernel op at all). These are the
    additive EE members — purely additive; the SE/NO tables and their dispositions
    are untouched:

    * ``unparsed_operation`` — a preserved but unparsed source-operation clause
      (EE ``ee_replay_unparsed_operation_skipped``; a META op the parser could
      not lower).
    * ``meta_non_body`` — a non-body META op preserved without mutating the body
      (EE ``ee_replay_meta_non_body_skipped``).
    * ``unsupported_action`` — the op's action is outside the frontend's routable
      action set (EE ``ee_replay_unsupported_action``).
    * ``statute_title_unsupported`` — a statute-title-address op whose action is
      not a title REPLACE / carries no payload (EE
      ``ee_replay_unsupported_statute_title_action``).
    * ``statute_title_unchanged`` — a statute-title REPLACE whose new title is
      empty or equals the live title (EE ``ee_replay_statute_title_noop``; a
      content-identical no-op on the statute-title facet, distinct in code from
      the body ``content_identical`` cell).

    The value is the boundary/adjudication string so a resolver reading a
    frontend adjudication code can round-trip to a ``FailureClass`` without a
    jurisdiction-local map.
    """

    TARGET_ABSENT = "target_absent"
    TARGET_OCCUPIED = "target_occupied"
    DEST_OCCUPIED = "dest_occupied"
    PAYLOAD_MISSING = "payload_missing"
    SELECTOR_NO_MATCH = "selector_no_match"
    PARENT_UNRESOLVED = "parent_unresolved"
    CONTENT_IDENTICAL = "content_identical"
    # ── EE action-admissibility failure classes (#186, additive). ──────────────
    UNPARSED_OPERATION = "unparsed_operation"
    META_NON_BODY = "meta_non_body"
    UNSUPPORTED_ACTION = "unsupported_action"
    STATUTE_TITLE_UNSUPPORTED = "statute_title_unsupported"
    STATUTE_TITLE_UNCHANGED = "statute_title_unchanged"


@dataclass(frozen=True, slots=True)
class Reject:
    """θ disposition: refuse the op with a typed rejection ``code`` (§2.3).

    The strict/default stance: the off-domain op lands in the conserved
    FilterResult's REJECTED lane carrying ``code`` (a frontend
    ``*_replay_*`` / ``replay_*`` code). ``code`` must be non-empty (a rejection
    without a diagnosable code is the boundary leak the algebra closes).
    """

    code: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("Reject requires a non-empty rejection code")


@dataclass(frozen=True, slots=True)
class NoopIdempotent:
    """θ disposition: the op is a well-formed idempotent no-op (§2.3).

    Distinct from ``Reject`` in *intent* (the precondition is satisfied but the
    op lands no write — e.g. REPEAL on an already-tombstoned slot, or a
    content-identical REPLACE), though the frontends today surface both through
    the same rejected-lane carrier. ``code`` names the no-op adjudication
    (``*_noop``); it must be non-empty.
    """

    code: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("NoopIdempotent requires a non-empty code")


@dataclass(frozen=True, slots=True)
class Recover:
    """θ disposition: rewrite the failed op into another kernel action (§2.3).

    Recovery is **op rewriting inside the algebra**, never bespoke tree code: the
    failed op is re-expressed as ``rewritten_action`` (e.g. NO's INSERT into an
    occupied target rewrites to REPLACE). Each RECOVER row MUST name a non-empty
    ``rule_id`` — the ``recovery_rule_id`` the WriteReceipt / adjudication ledger
    cites so the recovery is witnessed, never silent (§2.3: "every RECOVER row
    must name a recovery_rule_id that the WriteReceipt cites"). The constructor
    fails loud on an empty ``rule_id`` (a silent, unwitnessed recovery is
    unrepresentable — the point of the algebra).
    """

    rule_id: str
    rewritten_action: StructuralAction

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError(
                "Recover requires a non-empty recovery rule_id (a recovery must "
                "be witnessed by a rule the WriteReceipt/adjudication cites)"
            )


#: A θ disposition sum type: what the totalization does off-domain for one
#: ``(action, failure_class)`` cell.
Disposition = Union[Reject, NoopIdempotent, Recover]


@dataclass(frozen=True, slots=True)
class TotalizationTable:
    """A frozen θ totalization: ``(StructuralAction, FailureClass) → Disposition``.

    Constructed from an explicit ``rows`` mapping (the declared cells the
    frontend actually exhibits) plus a ``default`` for every unlisted cell — the
    §2.3 strict default is ``Reject`` (SE's stance). ``lookup`` falls back to
    ``default`` for an unlisted cell, so the table is TOTAL over the whole
    ``(action, failure_class)`` grid by construction.

    Construction validates every RECOVER row (in ``rows`` and the ``default``)
    carries a non-empty ``rule_id`` — the ``Recover.__post_init__`` invariant is
    re-asserted here so a table can never hold an unwitnessed recovery even if a
    caller constructed the ``Recover`` through an unusual path.

    ``jurisdiction`` is a free-form tag (``"se"`` / ``"no"``) so a registry /
    the spec-ledger can attribute the table without importing the frontend.
    """

    jurisdiction: str
    rows: dict[tuple[StructuralAction, FailureClass], Disposition]
    default: Disposition = Reject("totalization_default_reject")

    def __post_init__(self) -> None:
        if not self.jurisdiction:
            raise ValueError("TotalizationTable requires a non-empty jurisdiction tag")
        # Freeze the rows mapping to an immutable snapshot (the dataclass is
        # frozen but a plain dict field is still mutable through aliasing).
        object.__setattr__(self, "rows", dict(self.rows))
        for cell, disposition in self.rows.items():
            self._validate_disposition(cell, disposition)
        self._validate_disposition(None, self.default)

    @staticmethod
    def _validate_disposition(
        cell: tuple[StructuralAction, FailureClass] | None,
        disposition: Disposition,
    ) -> None:
        if isinstance(disposition, Recover) and not disposition.rule_id:
            where = "default" if cell is None else f"cell {cell!r}"
            raise ValueError(
                f"TotalizationTable {where} has a Recover with an empty rule_id; "
                "every recovery row must name the rule its WriteReceipt cites"
            )

    def lookup(
        self, action: StructuralAction, failure_class: FailureClass
    ) -> Disposition:
        """The θ disposition for ``(action, failure_class)``.

        Returns the declared row for the cell, or ``default`` (the §2.3 strict
        default) for any unlisted cell — the table is total over the grid.
        """
        return self.rows.get((action, failure_class), self.default)
