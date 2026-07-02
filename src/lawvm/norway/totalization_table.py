"""Norway's θ totalization table — the RICH recovery table (§2.3).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. NO is the
counterpoint to SE's strict baseline: it carries three explicit RECOVER rows
(op-rewrites) reflecting Lovdata source reality (§2.3: "NO's INSERT→REPLACE
recovery reflects … amendment acts that say 'ny § 4 a skal lyde' for an existing
slot" — a source-noise policy, not Norwegian legal semantics), plus REJECT /
NOOP rows for the rest.

Each RECOVER row uses the EXACT ``rule_id`` string the grafter emits today (the
``detail["rule_id"]`` on the ``no_replay_*`` recovery adjudication), and names
the kernel action it rewrites to. Grounded in ``norway/grafter.py``
(verified line-by-line):

* (INSERT, target_occupied)  → Recover(``no_insert_occupied_target_replace``, REPLACE)
    grafter.py:4293-4305 (adjudication kind ``no_replay_insert_occupied_target_replaced``)
* (REPLACE, target_absent)   → Recover(``no_replace_missing_section_insert``, INSERT)
    grafter.py:4218-4237 (kind ``no_replay_replace_recovered_by_insert``)
* (RENUMBER, dest_occupied)  → Recover(``no_renumber_occupied_destination_removed``, RENUMBER)
    grafter.py:4414-4436 (kind ``no_replay_renumber_occupied_destination_removed``;
    the occupant is removed and the RENUMBER proceeds)
* (REPEAL, target_absent)    → Reject(``replay_unresolved_target``)  grafter.py:4276-4286
* content-identical no-op     → NoopIdempotent(``replay_noop``)  (the I1-strong cell)

This is a DECLARED, conformance-tested spec of the *current* grafter behaviour
(``tests/test_totalization_conformance.py`` binds each cell to the runtime
disposition — for RECOVER, the accepted op + the emitted recovery rule_id). It is
NOT yet routed into ``apply_no_ops`` control flow — the load-bearing routing is
the deferred follow-up. The frontend imports the neutral core θ type; the kernel
never imports this module (the registry direction).
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Recover,
    Reject,
    TotalizationTable,
)

__all__ = ["NO_TOTALIZATION_TABLE", "build_no_totalization_table"]


def build_no_totalization_table() -> TotalizationTable:
    """Construct NO's rich θ table (three RECOVER rows + REJECT/NOOP)."""
    return TotalizationTable(
        jurisdiction="no",
        rows={
            # INSERT into an occupied target — NO recovers by REPLACING the
            # occupant (grafter.py:4293). The rule_id is the exact string the
            # recovery adjudication cites in detail["rule_id"].
            (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED): Recover(
                rule_id="no_insert_occupied_target_replace",
                rewritten_action=StructuralAction.REPLACE,
            ),
            # REPLACE whose target section is absent — NO recovers by INSERTing
            # the section (grafter.py:4223). (A missing-sentence variant uses
            # no_replace_missing_sentence_append_to_resolved_parent; the
            # top-level section lane is the one the conformance test drives.)
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): Recover(
                rule_id="no_replace_missing_section_insert",
                rewritten_action=StructuralAction.INSERT,
            ),
            # RENUMBER destination occupied by a slot not moved by the same
            # renumber group — NO removes the occupant and proceeds with the
            # RENUMBER (grafter.py:4422). The rewritten action is RENUMBER
            # itself (the recovery clears the destination, it does not change
            # the op's kind).
            (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED): Recover(
                rule_id="no_renumber_occupied_destination_removed",
                rewritten_action=StructuralAction.RENUMBER,
            ),
            # REPEAL whose target is absent — NO SKIPS (rejects) it
            # (grafter.py:4280 replay_unresolved_target). REPEAL is NOT
            # recovered; the address is simply unresolvable.
            (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT): Reject(
                "replay_unresolved_target"
            ),
            # A content-identical REPLACE — the op resolves and re-materializes
            # a fresh-but-content-equal subtree, landing NO write. The I1-strong
            # conservation cell: rejected as replay_noop (grafter.py; the #186
            # NO conservation fix — see test_no_apply_conserved.py).
            (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL): NoopIdempotent(
                "replay_noop"
            ),
        },
        # §2.3 strict default: an unlisted off-domain cell rejects as an
        # unresolved target (NO's generic skip code).
        default=Reject("replay_unresolved_target"),
    )


#: NO's rich θ totalization table (module-level singleton; the frontend datum).
NO_TOTALIZATION_TABLE: TotalizationTable = build_no_totalization_table()
