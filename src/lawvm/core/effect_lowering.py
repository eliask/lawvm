"""Generic `EffectIntent` -> `TemporalEvent` bridge helpers.

Frontend-specific amendment-preamble/meta-clause parsing and date extraction
live in jurisdiction lowering modules. This module keeps only the generic
bridge helpers used once parse-layer intents already exist. Lowering is
explicit: callers must invoke a projection helper at the phase boundary;
`PhaseResult` itself does not auto-lower anything. Source provenance may be
attached to the carrier for audit, but executable temporal dates must come from
the explicit temporal carrier itself.

API tier
--------
Internal lowering/adapter surface. This bridges parse-layer `EffectIntent`
material into executable temporal carriers; it is not a top-level public
dossier or transport API.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, List

from lawvm.core.ir import OperationSource, ScopePredicate
from lawvm.core.temporal import (
    FIXED_DATE_KIND,
    IMMEDIATE_KIND,
    PENDING_DECREE_KIND,
    ActivationRule,
    TemporalEvent,
    TemporalScope,
)
from lawvm.core.effect_intent import (
    Applicability,
    Commencement,
    EffectIntent,
    Expiry,
    Revival,
    Suspension,
)


def _temporal_event_source(
    *,
    source_ref: str,
    source_title: str,
    source_issue_date: dt.date | None,
    source_effective_date: dt.date | None,
    source_expires: dt.date | None,
    raw_text: str,
    target_statute: str,
) -> OperationSource | None:
    if not (source_ref or target_statute):
        return None
    enacted = source_issue_date.isoformat() if source_issue_date else ""
    effective = source_effective_date.isoformat() if source_effective_date else ""
    return OperationSource(
        statute_id=source_ref or target_statute,
        title=source_title,
        enacted=enacted,
        effective=effective,
        expires=source_expires.isoformat() if source_expires else "",
        raw_text=raw_text,
    )

def temporal_event_from_effect_intent(
    intent: EffectIntent,
    *,
    event_id: str,
    group_id: str = "",
    source_ref: str = "",
    source_title: str = "",
    source_issue_date: dt.date | None = None,
    source_effective_date: dt.date | None = None,
    target_statute: str = "",
) -> TemporalEvent:
    """Project one parse-layer EffectIntent into an additive TemporalEvent.

    This is a carrier-bridge helper. It materializes executable temporal
    events from parse-layer intents, but it does not own parse or event
    emission.
    """
    source = _temporal_event_source(
        source_ref=source_ref,
        source_title=source_title,
        source_issue_date=source_issue_date,
        source_effective_date=source_effective_date,
        source_expires=None,
        raw_text=intent.raw_text,
        target_statute=target_statute,
    )
    scope = TemporalScope(target_statute=target_statute)
    common_event_id = event_id
    common_group_id = group_id or None
    common_scope = scope
    common_derived = str(intent.kind)
    if isinstance(intent, Commencement):
        if intent.effective_date is not None:
            executable_effective = intent.effective_date.isoformat()
            activation_rule = ActivationRule(
                kind=FIXED_DATE_KIND,
                effective_date=executable_effective,
                raw_text=intent.raw_text,
            )
        elif intent.is_contingent:
            activation_rule = ActivationRule(
                kind=PENDING_DECREE_KIND,
                raw_text=intent.raw_text,
            )
            executable_effective = ""
        else:
            executable_effective = (
                source_effective_date.isoformat()
                if source_effective_date is not None
                else source_issue_date.isoformat()
                if source_issue_date is not None
                else ""
            )
            activation_rule = ActivationRule(
                kind=IMMEDIATE_KIND,
                raw_text=intent.raw_text,
            )
        return TemporalEvent(
            event_id=common_event_id,
            kind="commence",
            scope=common_scope,
            effective=executable_effective,
            source=source,
            activation_rule=activation_rule,
            group_id=common_group_id,
            derived_from_effect_intent=common_derived,
        )
    if isinstance(intent, Expiry):
        return TemporalEvent(
            event_id=common_event_id,
            kind="expire",
            scope=common_scope,
            expires=intent.expiry_date.isoformat() if intent.expiry_date is not None else "",
            source=source,
            group_id=common_group_id,
            derived_from_effect_intent=common_derived,
        )
    if isinstance(intent, Suspension):
        return TemporalEvent(
            event_id=common_event_id,
            kind="suspend",
            scope=common_scope,
            expires=intent.suspended_until.isoformat() if intent.suspended_until is not None else "",
            source=source,
            group_id=common_group_id,
            derived_from_effect_intent=common_derived,
        )
    if isinstance(intent, Revival):
        return TemporalEvent(
            event_id=common_event_id,
            kind="revive",
            scope=common_scope,
            effective=intent.revived_from.isoformat() if intent.revived_from is not None else "",
            source=source,
            group_id=common_group_id,
            derived_from_effect_intent=common_derived,
        )
    if isinstance(intent, Applicability):
        applicability_text = intent.raw_text.strip() or "applicability"
        return TemporalEvent(
            event_id=common_event_id,
            kind="set_applicability",
            effective=(
                source_effective_date.isoformat()
                if source_effective_date is not None
                else source_issue_date.isoformat()
                if source_issue_date is not None
                else ""
            ),
            scope=TemporalScope(
                target_statute=target_statute,
                predicates=(
                    ScopePredicate(
                        dimension="applicability",
                        includes=frozenset({applicability_text}),
                    ),
                ),
            ),
            source=source,
            group_id=common_group_id,
            derived_from_effect_intent=common_derived,
        )
    raise TypeError(f"Unsupported EffectIntent variant: {type(intent)!r} source_ref={source_ref!r}")


def lower_effect_intents_to_temporal_events(
    intents: Iterable[EffectIntent],
    *,
    source_ref: str = "",
    source_title: str = "",
    source_issue_date: dt.date | None = None,
    source_effective_date: dt.date | None = None,
    group_id_prefix: str = "effect-intent",
    target_statute: str = "",
) -> List[TemporalEvent]:
    """Project parse-layer EffectIntents into additive TemporalEvents.

    ``group_id_prefix`` is currently a batch key, not a unique per-effect
    identity. Every lowered event from one lowering batch shares the same
    ``group_id`` so execution can apply the batch coherently.

    Each emitted ``TemporalEvent`` may carry provenance on ``source`` when the
    caller provides a source/ref identity and source date metadata. Temporal
    matching in ``timeline.py`` remains batch-key based.
    """
    events: List[TemporalEvent] = []
    for idx, intent in enumerate(intents, start=1):
        events.append(
            temporal_event_from_effect_intent(
                intent,
                event_id=f"{group_id_prefix}:{idx}",
                group_id=group_id_prefix,
                source_ref=source_ref,
                source_title=source_title,
                source_issue_date=source_issue_date,
                source_effective_date=source_effective_date,
                target_statute=target_statute,
            )
        )
    return events
