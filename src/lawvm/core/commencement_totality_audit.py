"""``lawvm.core.commencement_totality_audit`` — D7 ``COMMENCEMENT.EFFECT_TOTALITY``.

Per :file:`notes_internal/audit_impl_D7.md` (LS-23 in
:file:`notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md`): every
:class:`~lawvm.core.ir.LegalOperation` reaching compile-timelines must be
*temporally authorized* — either commenced (a matching ``commence`` /
``revive`` :class:`~lawvm.core.temporal.TemporalEvent` exists via ``group_id``
and scope), *or* explicitly typed as pending / unresolved / manual-frontier,
*or* both. The parity assertion
``ops == commenced + unresolved + pending`` is otherwise implicit; this audit
makes it explicit and surfaces the residue as a typed
``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` :class:`~lawvm.core.phase_result.Observation`.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). This module lives in the
evidence-plane audit lane: it inspects passed ``LegalOperation`` and
``TemporalEvent`` carriers, returns :class:`~lawvm.core.phase_result.Observation`
tuples, and **never mutates legal state**. The audit does not choose an
effective date, drop an op, or re-tag a pending classification — it reports
that an op reached timeline compilation without temporal authority or an
owned pending classification. The wire consumer decides whether the
observation becomes a finding (quirks default) or a strict-mode barrier; this
function emits observations only.

IDENTITY MODEL (AGENTS.md §2.8). The audit does NOT treat a blank
``op.source.effective`` as evidence of pending status — blank dates may
indicate missing metadata, not a deliberate deferral (see §9 of the impl
spec). Pending authority is established only by:
  * ``op.provenance_tags`` containing one of :data:`_PENDING_PROVENANCE_TAGS`
    (EE/FI per ``pending_amendment``
    / ``pending_amendment_of_parent`` chains; UK per
    ``manual_frontier_commencement_effect_out_of_scope`` under AGENTS.md §0),
    OR
  * ``op.source.legal_status`` in :data:`_PENDING_LEGAL_STATUSES`
    (``pending_condition`` / ``uncommenced``), AND the frontend emitted an
    unresolved temporal finding (the frontend owns that emission; this audit
    only verifies the owned ``legal_status`` carried through), OR
  * a custom ``pending_classifier`` argument returning True for the op — the
    extension hook for a frontend's own pending-amendment taxonomy without
    mutating the closed vocabulary above.

WHAT THIS DOES **NOT** YET DO:
  * It does not consume ``TemporalEvent.activation_rule`` to gate against
    contingent-activation triggers (a ``pending_decree`` /
    ``pending_condition`` activation on a commence/revive event means the
    commencement has been SCHEDULED but not yet FIRED; that is currently an
    unresolved temporal finding in the frontend). A future strict-profile
    enhancement could surface contingent-but-unfired events as observations
    too; v0 only treats a commence/revive event with a fixed or immediate
    activation as commencement authority.
  * It does not assert universe totality — only present-ops totality. An op
    that never reaches ``compile_timelines`` (e.g. filtered earlier) is
    invisible here; that gap belongs to the upstream filter's receipt
    accounting (AGENTS.md §1.8).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.phase_result import Observation
from lawvm.core.temporal import (
    TemporalEvent,
)
from lawvm.core.timeline_temporal_events import matching_temporal_events_for_op


# Public finding code, also registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION = (
    "COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION"
)

# Audit-stage / owner used in the emitted Observations. Stage mirrors the
# pipeline phase wire point per audit_impl_D7 §3.
_COMMENCEMENT_AUDIT_STAGE = "compile-timelines"
_COMMENCEMENT_AUDIT_OWNER = "commencement_totality_audit"
_COMMENCEMENT_AUDIT_REASON = "no_matching_commencement_event"


# Provenance tags accepted as pending/manual-frontier classification per
# audit_impl_D7 §9. Closed set: a new tag is a typed contract change, not
# silent acceptance. ``pending_amendment``/``pending_amendment_of_parent`` are
# the EE/FI effect-lifecycle precomposition chain signal; the UK manual-
# frontier classification is the AGENTS.md §0 commencement effect frontier.
_PENDING_PROVENANCE_TAGS: frozenset[str] = frozenset(
    {
        "pending_amendment",
        "pending_amendment_of_parent",
        "manual_frontier_commencement_effect_out_of_scope",
    }
)

# ``OperationSource.legal_status`` values that mark the op as commencement-
# deferred rather than commenced. Same closed-set discipline.
_PENDING_LEGAL_STATUSES: frozenset[str] = frozenset({"pending_condition", "uncommenced"})


def _op_is_commenced(
    op: LegalOperation,
    events_by_group_id: dict[str, tuple[TemporalEvent, ...]],
    target_statute: str,
) -> bool:
    """True iff a commence/revive TemporalEvent matches ``op`` via ``group_id`` + scope.

    Mirrors the matching logic that ``compile_timelines`` itself uses (via
    :func:`lawvm.core.timeline_temporal_events.matching_temporal_events_for_op`)
    so the audit and the wire agree on what "temporally authorized" means. Any
    matching ``commence`` / ``revive`` event counts as authority here —
    including the contingent-unfired case (``activation_rule.kind in
    {pending_decree, pending_condition}``). A contingent-but-unfired commence
    is OWNED by the existing ``skipped_contingent_unresolved`` /
    ``TIME.TIMELINE_EXECUTION_ISSUE`` issue lane in ``compile_timelines`` /
    ``materialize_pit_ex`` (the frontend's unresolved-temporal-finding
    territory); D7's lane is the "no commence at ALL" or "no pending
    classification" residue, not the contingent-unfired firing surface.
    Surfactacing contingent-unfired a second time would be duplicate-accounting
    on a path the timeline already owns.
    """
    if not op.group_id:
        return False
    matching = matching_temporal_events_for_op(
        op,
        events_by_group_id.get(op.group_id, ()),
        target_statute=target_statute,
    )
    for event in matching:
        if event.kind in ("commence", "revive"):
            return True
    return False


def _event_activation_kind(event: TemporalEvent) -> Optional[str]:
    """Activation rule ``kind`` if present, else ``None``.

    Carried for diagnostic surface only — D7 treats any matching commence/
    revive as authority (see :func:`_op_is_commenced`). The contingent-unfired
    case is the existing ``skipped_contingent_unresolved`` issue lane, not a
    missing-authority gap.
    """
    activation = event.activation_rule
    if activation is None:
        return None
    return getattr(activation, "kind", None)


def _op_is_pending_or_unresolved(
    op: LegalOperation,
    pending_classifier: Optional[Callable[[LegalOperation], bool]] = None,
) -> bool:
    """True iff the op carries an explicit pending/unresolved/manual-frontier classification.

    Discriminators (closed set per audit_impl_D7 §9):

    * ``op.provenance_tags`` intersect :data:`_PENDING_PROVENANCE_TAGS` — the
      EE/FI effect-lifecycle precomposition chain signal, or the UK manual-
      frontier commencement-effect-out-of-scope classification.
    * ``op.source.legal_status`` in :data:`_PENDING_LEGAL_STATUSES` — when
      the frontend emitted a ``pending_condition`` / ``uncommenced`` typed
      status and (held in the frontend) an unresolved temporal finding.
    * ``pending_classifier(op)`` — the extension hook for a frontend's own
      pending-amendment taxonomy without mutating the closed vocabulary above.

    DOES NOT treat a blank ``op.source.effective`` as evidence of pending
    status (per §9: blank dates may indicate missing metadata, not a
    deliberate deferral — the §0 safe default for a no-authority op is a
    manual-frontier classification, not a guessed effective date).
    """
    if any(tag in _PENDING_PROVENANCE_TAGS for tag in op.provenance_tags):
        return True
    source = op.source
    if source is not None and getattr(source, "legal_status", None) in _PENDING_LEGAL_STATUSES:
        return True
    if pending_classifier is not None:
        # The ``pending_classifier`` is supplied by the consumer (e.g.
        # ``compile_timelines``); it must not raise. Per AGENTS.md §1.10 an
        # exception here is a consumer-side programming bug that fails loud
        # rather than being folded into the audit's not-pending verdict. The
        # §0 over-retention-safe direction is already achieved by the audit
        # emitting the observation when no classification authority exists —
        # the classifier swallowing only risks silently absorbing the gap.
        if pending_classifier(op) is True:
            return True
    return False


def _build_observation(
    op: LegalOperation,
    source_statute: str,
) -> Observation:
    """Build the typed ``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` observation.

    Detail includes the op's identity, target, action, group_id, and source
    provenance so a triager can answer the §3.2 evidence path (which op,
    which group_id, what was the source legal_status / provenance_tags)
    without re-running compilation.
    """
    source_dict: dict[str, Any] = {}
    if op.source is not None:
        # Serialise only the load-bearing provenance fields; byte spans
        # (SourceAnchor) and full raw_text are intentionally NOT carried here
        # to keep the observation's surface stable across source-byte
        # revisions (the audit is about temporal authority, not source span).
        source_dict = {
            "statute_id": getattr(op.source, "statute_id", ""),
            "effective": getattr(op.source, "effective", ""),
            "legal_status": getattr(op.source, "legal_status", ""),
            "commencement_source": getattr(op.source, "commencement_source", ""),
        }
    detail: dict[str, Any] = {
        "op_id": op.op_id,
        "group_id": op.group_id,
        "target": _format_address(op.target),
        "action": str(op.action),
        "provenance_tags": tuple(op.provenance_tags),
        "source": source_dict,
        "reason": _COMMENCEMENT_AUDIT_REASON,
        "owner": _COMMENCEMENT_AUDIT_OWNER,
    }
    return Observation(
        kind=COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
        stage=_COMMENCEMENT_AUDIT_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


def _format_address(address: Optional[LegalAddress]) -> str:
    if address is None:
        return ""
    formatted = str(address)
    if not formatted:
        return repr(address)
    return formatted


def assert_effect_totality(
    ops: Sequence[LegalOperation],
    temporal_events: Sequence[TemporalEvent],
    *,
    source_statute: str = "",
    pending_classifier: Optional[Callable[[LegalOperation], bool]] = None,
) -> tuple[Observation, ...]:
    """One :class:`Observation` per op that is neither commenced nor pending/unresolved.

    Args:
        ops: the executable-ops stream selected by ``compile_timelines``
            (post-:ref:`operation_matches_overlay_context` filter). An op that
            was filtered out earlier is invisible here — that filter owns its
            receipt accounting per AGENTS.md §1.8.
        temporal_events: the executable TemporalEvent stream paired with
            ``ops``. Events without ``group_id`` are ignored by
            :func:`matching_temporal_events_for_op`.
        source_statute: the base statute id of the timeline under audit.
            Carried into the observation so a multi-statute bench run can
            route the finding back to its source statute.
        pending_classifier: optional per-frontend pending-amendment
            discriminator (the extension hook). Kept out of the closed
            vocabulary above so a frontend's own taxonomy doesn't widen the
            audit's universal contract.

    Returns:
        Tuple of Observations, one per violating op, in op-stream order. The
        caller decides whether these become findings (quirks default) or
        strict-mode barriers (a future strict_profile) — this function emits
        observations only, never raises, never mutates legal state.

    Per AGENTS.md §0 ``over-retention is the safe wrong; over-repeal is the
    forbidden one``: an op with no temporal authority surfaces as an
    observation here rather than being silently effective-dated. The audit
    never synthesizes an effective date, drops the op, or re-tags the op's
    classification.
    """
    events_by_group_id: dict[str, tuple[TemporalEvent, ...]] = {}
    for event in temporal_events:
        if not event.group_id:
            continue
        events_by_group_id.setdefault(event.group_id, ())
        events_by_group_id[event.group_id] = events_by_group_id[event.group_id] + (event,)

    findings: list[Observation] = []
    for op in ops:
        if _op_is_commenced(op, events_by_group_id, source_statute):
            continue
        if _op_is_pending_or_unresolved(op, pending_classifier=pending_classifier):
            continue
        findings.append(_build_observation(op, source_statute))
    return tuple(findings)


__all__ = [
    "COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION",
    "assert_effect_totality",
]
