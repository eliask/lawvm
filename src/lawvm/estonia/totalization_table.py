"""Estonia's θ totalization table — the §2.3 silent-noop motivating case (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. EE is the
frontend §2.3 names as THE motivating case: "EE's silent noop is then
unrepresentable — which is the point: it is a conservation hole today". Task
#185 (``ee: derive conserved rejected-ness from the seam applied-signal``) closed
that hole — a content-identical no-op now emits ``ee_replay_noop`` and lands in
the REJECTED lane. This table encodes EE's CURRENT (post-#185) off-domain
dispositions as data, and ``estonia/grafter.py`` routes its off-domain sites
through ``EE_TOTALIZATION_TABLE.lookup(action, failure_class)`` so θ is the single
source of the off-domain disposition (mirroring the NO/SE load-bearing routing).

This routing is a PURE REPRESENTATION CHANGE on top of #185: it is BYTE-IDENTICAL
on the EE corpus (same dispositions, codes, receipts, final tree-state), guarded
by ``tests/test_totalization_conformance.py`` which binds each declared cell to
the ACTUAL runtime disposition via the real ``apply_ee_ops_conserved`` path.

EE, like SE, NEVER recovers (rewrites) an off-domain op — every off-domain lane
is a typed REJECT or an idempotent NOOP. So this table is the strict-baseline
shape: explicit ``Reject`` / ``NoopIdempotent`` rows for the exact
``ee_replay_*`` codes ``apply_ee_ops`` emits at each off-domain lane today, plus
the strict ``Reject`` default (``ee_replay_skipped_unspecified`` — the code the
conserved wrapper synthesizes for an op that lands on a skip adjudication with no
recognized reason, ``apply_ee_ops_conserved``).

The off-domain lanes fall in two categories:

* **Precondition failures on a supported, resolved op** (the θ domain proper):
  ``TARGET_ABSENT`` → ``ee_replay_target_not_found``; ``CONTENT_IDENTICAL`` →
  ``ee_replay_noop`` (the #185 I1-strong conservation cell). EE's code for these
  is UNIFORM across the resolving actions (replace/repeal/insert/renumber/
  text_replace), so — as SE's routing does — the canonical ``REPLACE`` cell is
  the source of the code.
* **Action-admissibility failures** (the instruction never routes to a kernel op
  at all): the unparsed-clause, meta-non-body, unsupported-action, and
  statute-title lanes. These key on the additive EE ``FailureClass`` members
  (``core/totalization.py``); the action is ``META`` for the meta / unsupported
  / statute-title-unsupported lanes and ``REPLACE`` for the statute-title-noop
  lane (a title REPLACE that landed no title change).

Grounded in ``estonia/grafter.py`` ``apply_ee_ops`` (verified line-by-line
against the seven ``_EE_SKIP_ADJUDICATION_KINDS`` emit sites):

* ``ee_replay_unparsed_operation_skipped`` (META clause, unparsed source op)
* ``ee_replay_meta_non_body_skipped`` (META, non-body op)
* ``ee_replay_unsupported_statute_title_action`` (statute-title, non-replace/no payload)
* ``ee_replay_statute_title_noop`` (statute-title REPLACE, empty/unchanged title)
* ``ee_replay_unsupported_action`` (action outside the routable set)
* ``ee_replay_target_not_found`` (target address does not resolve)
* ``ee_replay_noop`` (content-identical no-op; #185)
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Reject,
    TotalizationTable,
)

__all__ = ["EE_TOTALIZATION_TABLE", "build_ee_totalization_table"]


def build_ee_totalization_table() -> TotalizationTable:
    """Construct EE's strict θ table (§2.3 default = ``Reject``; no recoveries)."""
    return TotalizationTable(
        jurisdiction="ee",
        rows={
            # ── Action-admissibility lanes (the instruction never routes to a
            # kernel op). All key on the META action; the additive EE
            # FailureClass members carry the specific lane. ────────────────────
            # An unparsed source-operation clause preserved without mutating the
            # body (grafter.py ~:9948).
            (StructuralAction.META, FailureClass.UNPARSED_OPERATION): Reject(
                "ee_replay_unparsed_operation_skipped"
            ),
            # A non-body META op preserved without mutating the body
            # (grafter.py ~:9956).
            (StructuralAction.META, FailureClass.META_NON_BODY): Reject(
                "ee_replay_meta_non_body_skipped"
            ),
            # A statute-title-address op whose action is not a title REPLACE or
            # carries no payload (grafter.py ~:9966).
            (StructuralAction.META, FailureClass.STATUTE_TITLE_UNSUPPORTED): Reject(
                "ee_replay_unsupported_statute_title_action"
            ),
            # An action outside EE's routable action set — replace/repeal/insert/
            # renumber/text_replace (grafter.py ~:9989).
            (StructuralAction.META, FailureClass.UNSUPPORTED_ACTION): Reject(
                "ee_replay_unsupported_action"
            ),
            # A statute-title REPLACE whose new title is empty or equals the live
            # title — a content-identical no-op on the statute-title facet
            # (grafter.py ~:9980). Distinct in intent (idempotent) AND in code
            # from the body content_identical cell, so it needs its own
            # (REPLACE, statute_title_unchanged) row.
            (StructuralAction.REPLACE, FailureClass.STATUTE_TITLE_UNCHANGED): NoopIdempotent(
                "ee_replay_statute_title_noop"
            ),
            # ── Precondition-failure lanes on a supported, resolved op. EE's
            # code is uniform across the resolving actions, so the canonical
            # REPLACE cell is the source of the code (mirrors SE's routing). ────
            # The op's target address does not resolve (grafter.py ~:10182).
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): Reject(
                "ee_replay_target_not_found"
            ),
            # The op resolved and applied but landed NO content write — a
            # byte-identical no-op. The #185 I1-strong conservation cell
            # (grafter.py ~:10192): content-identical no-ops now reject via the
            # content-footprint applied signal.
            (StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL): NoopIdempotent(
                "ee_replay_noop"
            ),
        },
        # §2.3 strict default: any unlisted off-domain cell rejects. EE's own
        # catch-all skip code — the reason_code the conserved wrapper synthesizes
        # for an op that lands on a skip adjudication with no recognized reason
        # (``apply_ee_ops_conserved`` fallback).
        default=Reject("ee_replay_skipped_unspecified"),
    )


#: EE's strict θ totalization table (module-level singleton; the frontend datum).
EE_TOTALIZATION_TABLE: TotalizationTable = build_ee_totalization_table()
