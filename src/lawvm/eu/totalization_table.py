"""The EU's θ totalization table — reject-skip off-domain dispositions (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. This table
extends the θ ``TotalizationTable`` to the EU, mirroring the NO/SE/EE/UK work
already merged. It encodes the EU's CURRENT off-domain dispositions as data
(``EU_TOTALIZATION_TABLE``), and ``eu/pipeline.py`` routes its off-domain sites
through ``EU_TOTALIZATION_TABLE.lookup(action, failure_class)`` so θ is the single
source of the off-domain disposition (mirroring the NO/SE/EE load-bearing
routing).

The EU is a ✓enum I1 frontend running the CONSERVED apply seam
(``apply_eu_ops_conserved`` over ``apply_op``). Its off-domain stance is UNIFORM:
every off-domain lane is a REJECT-SKIP — the §2.3 EU note "EU is reject-skip for
target_absent". The EU NEVER recovers (a missing-leaf REPLACE is skipped, not
rewritten to INSERT) and NEVER emits a distinct no-op disposition (a resolved op
that lands no write is simply the applied path — the EU bare fold has no separate
content-identical reject lane), so this table is a pure ``Reject`` shape with the
§2.3 strict ``Reject`` default. There are no ``NoopIdempotent`` and no ``Recover``
rows.

This routing is BYTE-IDENTICAL on the EU corpus (same dispositions, codes,
receipts, final tree-state), guarded by ``tests/test_totalization_conformance.py``
which binds each declared cell to the ACTUAL runtime disposition via the real
``apply_eu_ops_conserved`` path.

Grounded in ``eu/pipeline.py`` ``apply_eu_ops`` (verified line-by-line against
the six ``_EU_SKIP_ADJUDICATION_KINDS`` emit sites), the off-domain lanes are:

* ``eu_replay_text_payload_missing`` — a REPLACE / INSERT carries no payload
  (pipeline.py ~:492 REPLACE, ~:606 INSERT). Keyed on ``payload_missing`` for
  both the REPLACE and INSERT actions.
* ``eu_replay_target_not_found`` — a REPLACE / REPEAL target address does not
  resolve (empty target label OR ``tree_ops.find`` miss; pipeline.py ~:513/~:541
  REPLACE, ~:564/~:589 REPEAL). Keyed on ``target_absent`` for both actions.
* ``eu_replay_insert_parent_scope_unresolved`` — an INSERT whose SCOPED parent
  path did not resolve while unscoped lookalike parent candidates exist
  (pipeline.py ~:624). Keyed on the additive ``parent_scope_unresolved`` class
  (distinct code from the plain parent miss).
* ``eu_replay_parent_not_found`` — an INSERT's parent chain does not resolve at
  all (pipeline.py ~:646). Keyed on ``parent_unresolved``.
* ``eu_replay_unsupported_action`` — a recognized action outside the routable set
  (text_replace / text_repeal / renumber; pipeline.py ~:683). Keyed on the
  additive ``unsupported_action`` class, ``META`` action (the instruction never
  routes to a supported kernel op — it is an action-admissibility failure).
* ``eu_replay_unknown_action`` — an UNKNOWN / unrecognized action (pipeline.py
  ~:694/~:705). Keyed on the additive ``unknown_action`` class, ``META`` action.

The strict ``Reject`` default (``eu_replay_skipped_unspecified``) is the
reason_code the conserved wrapper synthesizes for an op that lands on a skip
adjudication with no recognized reason (``apply_eu_ops_conserved`` fallback,
pipeline.py ~:981).
"""

from __future__ import annotations

from lawvm.core.ir import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    Reject,
    TotalizationTable,
)

__all__ = ["EU_TOTALIZATION_TABLE", "build_eu_totalization_table"]


def build_eu_totalization_table() -> TotalizationTable:
    """Construct the EU's strict θ table (§2.3 default = ``Reject``; no recoveries)."""
    return TotalizationTable(
        jurisdiction="eu",
        rows={
            # ── Precondition-failure lanes on a supported, resolved action. ─────
            # A REPLACE / INSERT that carries no payload (pipeline.py ~:492/~:606).
            (StructuralAction.REPLACE, FailureClass.PAYLOAD_MISSING): Reject(
                "eu_replay_text_payload_missing"
            ),
            (StructuralAction.INSERT, FailureClass.PAYLOAD_MISSING): Reject(
                "eu_replay_text_payload_missing"
            ),
            # A REPLACE / REPEAL whose target address does not resolve — empty
            # target label OR ``tree_ops.find`` miss (pipeline.py ~:513/~:541
            # REPLACE, ~:564/~:589 REPEAL). §2.3: "EU is reject-skip for
            # target_absent" (the EU does NOT recover a missing-leaf REPLACE).
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): Reject(
                "eu_replay_target_not_found"
            ),
            (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT): Reject(
                "eu_replay_target_not_found"
            ),
            # An INSERT whose SCOPED parent path did not resolve while unscoped
            # lookalike parent candidates exist — a scope miss, distinct code from
            # the plain parent miss below (pipeline.py ~:624).
            (StructuralAction.INSERT, FailureClass.PARENT_SCOPE_UNRESOLVED): Reject(
                "eu_replay_insert_parent_scope_unresolved"
            ),
            # An INSERT's parent chain does not resolve at all (pipeline.py ~:646).
            (StructuralAction.INSERT, FailureClass.PARENT_UNRESOLVED): Reject(
                "eu_replay_parent_not_found"
            ),
            # ── Action-admissibility lanes (the instruction never routes to a
            # supported kernel op). Keyed on the META action; the additive EU
            # FailureClass members carry the specific lane. ─────────────────────
            # A recognized action outside the routable set — text_replace /
            # text_repeal / renumber (pipeline.py ~:683).
            (StructuralAction.META, FailureClass.UNSUPPORTED_ACTION): Reject(
                "eu_replay_unsupported_action"
            ),
            # An UNKNOWN / unrecognized action (pipeline.py ~:694/~:705).
            (StructuralAction.META, FailureClass.UNKNOWN_ACTION): Reject(
                "eu_replay_unknown_action"
            ),
        },
        # §2.3 strict default: any unlisted off-domain cell rejects. The EU's own
        # catch-all skip code — the reason_code the conserved wrapper synthesizes
        # for an op that lands on a skip adjudication with no recognized reason
        # (``apply_eu_ops_conserved`` fallback).
        default=Reject("eu_replay_skipped_unspecified"),
    )


#: The EU's strict θ totalization table (module-level singleton; the frontend datum).
EU_TOTALIZATION_TABLE: TotalizationTable = build_eu_totalization_table()
