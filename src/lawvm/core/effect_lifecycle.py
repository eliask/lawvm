"""Typed source-effect lifecycle carriers.

These records model relationships between source-backed effect declarations.
They sit above replay: executable parent-statute projection still happens via
``LegalOperation`` and ``TemporalEvent``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.ir import LegalAddress
from lawvm.core.provenance import OperationSource
from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope


EffectRelationKind = Literal[
    "modifies_effect",
    "repeals_effect",
    "extends_effect_expiry",
    "changes_effect_commencement",
    "supersedes_effect",
    "clarifies_effect_scope",
]

EffectLifecycleEventKind = Literal[
    "commence_effect",
    "expire_effect",
    "suspend_effect",
    "revive_effect",
    "change_effect_commencement",
    "change_effect_expiry",
    "repeal_effect",
    "unresolved_effect_target",
]


@dataclass(frozen=True, slots=True)
class SourceInstrumentRef:
    """Stable identity for a source instrument that declares or modifies effects."""

    instrument_id: str
    title: str = ""
    enacted: str = ""
    effective: str = ""
    expires: str = ""

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("SourceInstrumentRef.instrument_id must be non-empty")

    @classmethod
    def from_operation_source(cls, source: OperationSource) -> "SourceInstrumentRef":
        return cls(
            instrument_id=source.statute_id,
            title=source.title,
            enacted=source.enacted,
            effective=source.effective,
            expires=source.expires,
        )

    def to_operation_source(self, *, raw_text: str = "") -> OperationSource:
        return OperationSource(
            statute_id=self.instrument_id,
            title=self.title,
            enacted=self.enacted,
            effective=self.effective,
            expires=self.expires,
            raw_text=raw_text,
        )


@dataclass(frozen=True, slots=True)
class SourceProvisionRef:
    """Source location where an effect or lifecycle relation was declared."""

    instrument: SourceInstrumentRef
    path: tuple[str, ...] = ()
    span_id: str = ""
    text_excerpt: str = ""
    rule_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SourceInstrumentRef):
            raise ValueError("SourceProvisionRef.instrument must be a SourceInstrumentRef")
        object.__setattr__(self, "path", tuple(str(part) for part in self.path if str(part)))

    @property
    def witness_id(self) -> str:
        path = "/".join(self.path)
        if path:
            return f"{self.instrument.instrument_id}:{path}"
        if self.span_id:
            return f"{self.instrument.instrument_id}:{self.span_id}"
        return self.instrument.instrument_id


@dataclass(frozen=True, slots=True)
class EffectRef:
    """Stable identity for one source-backed effect declaration."""

    effect_id: str
    source_instrument: SourceInstrumentRef
    target_statute: str = ""
    target_address: Optional[LegalAddress] = None
    source_provision: Optional[SourceProvisionRef] = None

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ValueError("EffectRef.effect_id must be non-empty")
        if not isinstance(self.source_instrument, SourceInstrumentRef):
            raise ValueError("EffectRef.source_instrument must be a SourceInstrumentRef")
        if self.target_address is not None and not isinstance(self.target_address, LegalAddress):
            raise ValueError("EffectRef.target_address must be a LegalAddress when provided")
        if self.source_provision is not None and not isinstance(self.source_provision, SourceProvisionRef):
            raise ValueError("EffectRef.source_provision must be a SourceProvisionRef when provided")


@dataclass(frozen=True, slots=True)
class EffectRelation:
    """A witnessed relation from one effect or instrument to another."""

    relation_id: str
    kind: EffectRelationKind
    source_provision: SourceProvisionRef
    target_effect: Optional[EffectRef] = None
    target_instrument: Optional[SourceInstrumentRef] = None
    source_effect: Optional[EffectRef] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.relation_id:
            raise ValueError("EffectRelation.relation_id must be non-empty")
        if self.kind not in {
            "modifies_effect",
            "repeals_effect",
            "extends_effect_expiry",
            "changes_effect_commencement",
            "supersedes_effect",
            "clarifies_effect_scope",
        }:
            raise ValueError(f"unsupported EffectRelation.kind: {self.kind!r}")
        if not isinstance(self.source_provision, SourceProvisionRef):
            raise ValueError("EffectRelation.source_provision must be a SourceProvisionRef")
        if self.target_effect is None and self.target_instrument is None:
            raise ValueError("EffectRelation requires target_effect or target_instrument")
        if self.target_effect is not None and not isinstance(self.target_effect, EffectRef):
            raise ValueError("EffectRelation.target_effect must be an EffectRef when provided")
        if self.target_instrument is not None and not isinstance(self.target_instrument, SourceInstrumentRef):
            raise ValueError("EffectRelation.target_instrument must be a SourceInstrumentRef when provided")
        if self.source_effect is not None and not isinstance(self.source_effect, EffectRef):
            raise ValueError("EffectRelation.source_effect must be an EffectRef when provided")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))


@dataclass(frozen=True, slots=True)
class EffectLifecycleEvent:
    """Executable or unresolved lifecycle event over an effect declaration."""

    lifecycle_event_id: str
    kind: EffectLifecycleEventKind
    source_provision: SourceProvisionRef
    effect: Optional[EffectRef] = None
    relation: Optional[EffectRelation] = None
    effective: str = ""
    expires: str = ""
    temporal_event: Optional[TemporalEvent] = None
    executable: bool = True
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.lifecycle_event_id:
            raise ValueError("EffectLifecycleEvent.lifecycle_event_id must be non-empty")
        if self.kind not in {
            "commence_effect",
            "expire_effect",
            "suspend_effect",
            "revive_effect",
            "change_effect_commencement",
            "change_effect_expiry",
            "repeal_effect",
            "unresolved_effect_target",
        }:
            raise ValueError(f"unsupported EffectLifecycleEvent.kind: {self.kind!r}")
        if not isinstance(self.source_provision, SourceProvisionRef):
            raise ValueError("EffectLifecycleEvent.source_provision must be a SourceProvisionRef")
        if self.effect is not None and not isinstance(self.effect, EffectRef):
            raise ValueError("EffectLifecycleEvent.effect must be an EffectRef when provided")
        if self.relation is not None and not isinstance(self.relation, EffectRelation):
            raise ValueError("EffectLifecycleEvent.relation must be an EffectRelation when provided")
        if self.temporal_event is not None and not isinstance(self.temporal_event, TemporalEvent):
            raise ValueError("EffectLifecycleEvent.temporal_event must be a TemporalEvent when provided")
        if self.executable and self.kind == "unresolved_effect_target":
            raise ValueError("unresolved EffectLifecycleEvent cannot be executable")
        if self.kind != "unresolved_effect_target" and self.effect is None:
            raise ValueError("resolved EffectLifecycleEvent requires effect")
        if self.executable and self.effect is None:
            raise ValueError("executable EffectLifecycleEvent requires effect")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))


def lower_lifecycle_event_to_temporal_event(
    event: EffectLifecycleEvent,
) -> TemporalEvent | None:
    """Lower one resolved lifecycle event to the executable temporal projection."""

    if not event.executable:
        return None
    if event.temporal_event is not None:
        return event.temporal_event
    if event.effect is None:
        return None

    source_text = event.source_provision.text_excerpt
    source = event.source_provision.instrument.to_operation_source(raw_text=source_text)
    scope = TemporalScope(
        target_statute=event.effect.target_statute,
        exact_addresses=(event.effect.target_address,) if event.effect.target_address is not None else (),
    )
    if event.kind in {"commence_effect", "change_effect_commencement"}:
        return TemporalEvent(
            event_id=f"{event.lifecycle_event_id}:temporal",
            kind="commence",
            scope=scope,
            effective=event.effective,
            source=source,
            activation_rule=(
                ActivationRule(kind="fixed_date", effective_date=event.effective, raw_text=source_text)
                if event.effective
                else ActivationRule(kind="immediate", raw_text=source_text)
            ),
            group_id=event.relation.relation_id if event.relation is not None else event.effect.effect_id,
        )
    if event.kind in {"expire_effect", "change_effect_expiry", "repeal_effect"}:
        return TemporalEvent(
            event_id=f"{event.lifecycle_event_id}:temporal",
            kind="expire",
            scope=scope,
            expires=event.expires or event.effective,
            source=source,
            group_id=event.relation.relation_id if event.relation is not None else event.effect.effect_id,
        )
    if event.kind == "suspend_effect":
        temporal_kind = "suspend"
    elif event.kind == "revive_effect":
        temporal_kind = "revive"
    else:
        return None
    return TemporalEvent(
        event_id=f"{event.lifecycle_event_id}:temporal",
        kind=temporal_kind,
        scope=scope,
        effective=event.effective,
        source=source,
        group_id=event.relation.relation_id if event.relation is not None else event.effect.effect_id,
    )


def lower_lifecycle_events_to_temporal_events(
    events: tuple[EffectLifecycleEvent, ...],
) -> tuple[TemporalEvent, ...]:
    lowered: list[TemporalEvent] = []
    for event in events:
        temporal_event = lower_lifecycle_event_to_temporal_event(event)
        if temporal_event is not None:
            lowered.append(temporal_event)
    return tuple(lowered)
