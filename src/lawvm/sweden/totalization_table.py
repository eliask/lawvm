"""Sweden's θ totalization table — the strict/REJECT baseline (§2.3).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. SE is the
**identity/default** totalization: its off-domain policy is "reject with a typed
code" for essentially every precondition failure — it NEVER recovers (rewrites)
an op (``sweden/grafter.py`` SEApplyResult docstring: "Recovery … none today").
So this table is mostly the strict ``Reject`` default plus the explicit rows for
the exact ``se_replay_*`` codes SE emits at each off-domain lane today.

This is a DECLARED, conformance-tested spec of the *current* grafter behaviour
(``tests/test_totalization_conformance.py`` binds each cell to the runtime
disposition). It is NOT yet routed into ``apply_se_ops`` control flow — that
load-bearing step is the deferred follow-up. The frontend imports the neutral
core θ type; the kernel never imports this module (the registry direction).

The codes are grounded in ``sweden/grafter.py`` (verified line-by-line):
``se_replay_target_not_found`` (REPLACE/REPEAL/RENUMBER-source absent),
``se_replay_unsupported_action`` (INSERT into an occupied section),
``se_replay_renumber_collision`` (RENUMBER destination occupied),
``se_replay_payload_missing`` (structural op with no/wrong payload),
``se_replay_text_replace_no_match`` (TEXT_REPLACE selector miss),
``se_replay_noop`` (content-identical no-op), and the fallback
``se_replay_skipped_unspecified`` (used here as the strict default).
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Reject,
    TotalizationTable,
)

__all__ = ["SE_TOTALIZATION_TABLE", "build_se_totalization_table"]


def build_se_totalization_table() -> TotalizationTable:
    """Construct SE's strict θ table (§2.3 default = ``Reject``)."""
    return TotalizationTable(
        jurisdiction="se",
        rows={
            # REPLACE on a section whose target does not resolve — SE rejects
            # (sweden/grafter.py:4072 se_replay_target_not_found). NO's rich
            # table recovers this cell to INSERT; SE is the strict counterpoint.
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): Reject(
                "se_replay_target_not_found"
            ),
            # REPLACE with a null / wrong-kind payload (grafter.py:4084).
            (StructuralAction.REPLACE, FailureClass.PAYLOAD_MISSING): Reject(
                "se_replay_payload_missing"
            ),
            # INSERT into a section label that already exists — SE refuses the
            # occupied INSERT (grafter.py:4040 se_replay_unsupported_action).
            (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED): Reject(
                "se_replay_unsupported_action"
            ),
            # INSERT with a null / wrong-kind payload (grafter.py:3864/3963).
            (StructuralAction.INSERT, FailureClass.PAYLOAD_MISSING): Reject(
                "se_replay_payload_missing"
            ),
            # REPEAL whose target section does not resolve (grafter.py:3920).
            (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT): Reject(
                "se_replay_target_not_found"
            ),
            # RENUMBER source not found (grafter.py:3901).
            (StructuralAction.RENUMBER, FailureClass.TARGET_ABSENT): Reject(
                "se_replay_target_not_found"
            ),
            # RENUMBER destination label already exists — the collision reject
            # (grafter.py:3910 se_replay_renumber_collision). NO's table
            # recovers this by removing the occupant.
            (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED): Reject(
                "se_replay_renumber_collision"
            ),
            # TEXT_REPLACE selector finds no match in the target subtree
            # (grafter.py:3998 se_replay_text_replace_no_match).
            (StructuralAction.TEXT_REPLACE, FailureClass.SELECTOR_NO_MATCH): Reject(
                "se_replay_text_replace_no_match"
            ),
            # TEXT_REPLACE target not found (grafter.py:3938/3953).
            (StructuralAction.TEXT_REPLACE, FailureClass.TARGET_ABSENT): Reject(
                "se_replay_target_not_found"
            ),
            # TEXT_REPLACE with a null patch / missing old/new text
            # (grafter.py:3963/3978).
            (StructuralAction.TEXT_REPLACE, FailureClass.PAYLOAD_MISSING): Reject(
                "se_replay_payload_missing"
            ),
            # A content-identical REPLACE / TEXT_REPLACE / INSERT — the op
            # resolves but lands no content write (grafter.py:4233
            # se_replay_noop). Distinct in intent (idempotent no-op), same
            # rejected-lane carrier.
            (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL): NoopIdempotent(
                "se_replay_noop"
            ),
            (StructuralAction.TEXT_REPLACE, FailureClass.CONTENT_IDENTICAL): NoopIdempotent(
                "se_replay_noop"
            ),
        },
        # §2.3 strict default: any unlisted off-domain cell rejects. SE's own
        # catch-all skip code is se_replay_skipped_unspecified (grafter.py:4469).
        default=Reject("se_replay_skipped_unspecified"),
    )


#: SE's strict θ totalization table (module-level singleton; the frontend datum).
SE_TOTALIZATION_TABLE: TotalizationTable = build_se_totalization_table()
