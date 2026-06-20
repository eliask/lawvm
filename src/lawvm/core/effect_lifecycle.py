"""Typed source-effect lifecycle carriers.

These records model relationships between source-backed effect declarations.
They sit above replay: executable parent-statute projection still happens via
``LegalOperation`` and ``TemporalEvent``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.ir import LegalAddress
from lawvm.core.provenance import OperationSource
from lawvm.core.statute_validity import expires_on_from_valid_until
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

EffectExpiryConvention = Literal["exclusive_cutoff", "inclusive_valid_until"]


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
    projection_group_id: str = ""
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
        if self.target_effect is not None and self.target_instrument is not None:
            raise ValueError("EffectRelation requires exactly one target endpoint")
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
    expiry_convention: EffectExpiryConvention = "exclusive_cutoff"
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
        if self.expiry_convention not in {"exclusive_cutoff", "inclusive_valid_until"}:
            raise ValueError(f"unsupported EffectLifecycleEvent.expiry_convention: {self.expiry_convention!r}")
        if self.expiry_convention == "inclusive_valid_until" and self.expires:
            dt.date.fromisoformat(self.expires)
        if self.executable and self.kind == "unresolved_effect_target":
            raise ValueError("unresolved EffectLifecycleEvent cannot be executable")
        if self.kind == "unresolved_effect_target" and self.effect is not None:
            raise ValueError("unresolved EffectLifecycleEvent cannot name effect")
        if (
            self.kind == "unresolved_effect_target"
            and self.relation is not None
            and self.relation.target_effect is not None
        ):
            raise ValueError(
                "unresolved EffectLifecycleEvent relation cannot name target_effect"
            )
        if self.kind != "unresolved_effect_target" and self.effect is None:
            raise ValueError("resolved EffectLifecycleEvent requires effect")
        if self.executable and self.effect is None:
            raise ValueError("executable EffectLifecycleEvent requires effect")
        if self.kind in {
            "change_effect_commencement",
            "change_effect_expiry",
            "repeal_effect",
        } and self.relation is None:
            raise ValueError(
                "effect-modifying EffectLifecycleEvent requires EffectRelation"
            )
        if self.relation is not None and self.kind in {
            "change_effect_commencement",
            "change_effect_expiry",
            "repeal_effect",
        }:
            expected_relation_kind = {
                "change_effect_commencement": "changes_effect_commencement",
                "change_effect_expiry": "extends_effect_expiry",
                "repeal_effect": "repeals_effect",
            }[self.kind]
            if self.relation.kind != expected_relation_kind:
                raise ValueError(
                    "effect-modifying EffectLifecycleEvent relation kind "
                    f"must be {expected_relation_kind!r}"
                )
            if self.relation.target_effect != self.effect:
                raise ValueError(
                    "effect-modifying EffectLifecycleEvent relation target "
                    "must match event effect"
                )
            if self.relation.source_provision != self.source_provision:
                raise ValueError(
                    "effect-modifying EffectLifecycleEvent source_provision "
                    "must match relation source_provision"
                )
        if (
            self.executable
            and self.temporal_event is None
            and self.kind in {"expire_effect", "change_effect_expiry", "repeal_effect"}
            and not (self.expires or self.effective)
        ):
            raise ValueError(
                "executable expiry/repeal EffectLifecycleEvent requires "
                "effective or expires date"
            )
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
    # Expiry changes must bind to the operation-backed version they modify.
    # Relationless commencement events are already direct operation projections
    # and should also bind to the operation group. Relation-backed non-expiry
    # lifecycle events execute independently so they do not contaminate sibling
    # operations in a broad amendment group.
    if (
        event.effect.projection_group_id
        and (
            event.kind == "change_effect_expiry"
            or (
                event.relation is None
                and event.kind in {"commence_effect", "change_effect_commencement"}
            )
        )
    ):
        group_id = event.effect.projection_group_id
    else:
        group_id = event.relation.relation_id if event.relation is not None else event.effect.effect_id
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
            group_id=group_id,
        )
    if event.kind in {"expire_effect", "change_effect_expiry", "repeal_effect"}:
        expires = event.expires or event.effective
        if expires and event.expiry_convention == "inclusive_valid_until":
            expires = expires_on_from_valid_until(dt.date.fromisoformat(expires)).isoformat()
        return TemporalEvent(
            event_id=f"{event.lifecycle_event_id}:temporal",
            kind="expire",
            scope=scope,
            expires=expires,
            source=source,
            group_id=group_id,
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
        group_id=group_id,
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


def validate_effect_graph_closure(
    *,
    subject: str,
    source_effects: tuple[EffectRef, ...],
    effect_relations: tuple[EffectRelation, ...],
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...],
) -> None:
    """Validate that a final effect graph carrier contains referenced nodes.

    Phase outputs may carry partial graph fragments while a pipeline stage is
    still accumulating context. Final semantic products must be closed: every
    relation endpoint and lifecycle-owned relation/effect must be present in
    the same carrier, otherwise the graph is not auditable from the product.
    """

    effects_by_id = {effect.effect_id: effect for effect in source_effects}
    relations_by_id = {relation.relation_id: relation for relation in effect_relations}
    for relation in effect_relations:
        if relation.target_effect is not None:
            target_effect = effects_by_id.get(relation.target_effect.effect_id)
            if target_effect is None:
                raise ValueError(
                    f"{subject}.effect_relations references missing target_effect: "
                    f"{relation.target_effect.effect_id!r}"
                )
            if target_effect != relation.target_effect:
                raise ValueError(
                    f"{subject}.effect_relations target_effect differs from graph effect: "
                    f"{relation.target_effect.effect_id!r}"
                )
        if relation.source_effect is not None:
            source_effect = effects_by_id.get(relation.source_effect.effect_id)
            if source_effect is None:
                raise ValueError(
                    f"{subject}.effect_relations references missing source_effect: "
                    f"{relation.source_effect.effect_id!r}"
                )
            if source_effect != relation.source_effect:
                raise ValueError(
                    f"{subject}.effect_relations source_effect differs from graph effect: "
                    f"{relation.source_effect.effect_id!r}"
                )
    for event in effect_lifecycle_events:
        if event.effect is not None:
            effect = effects_by_id.get(event.effect.effect_id)
            if effect is None:
                raise ValueError(
                    f"{subject}.effect_lifecycle_events references missing effect: "
                    f"{event.effect.effect_id!r}"
                )
            if effect != event.effect:
                raise ValueError(
                    f"{subject}.effect_lifecycle_events effect differs from graph effect: "
                    f"{event.effect.effect_id!r}"
                )
        if event.relation is not None:
            relation = relations_by_id.get(event.relation.relation_id)
            if relation is None:
                raise ValueError(
                    f"{subject}.effect_lifecycle_events references missing relation: "
                    f"{event.relation.relation_id!r}"
                )
            if relation != event.relation:
                raise ValueError(
                    f"{subject}.effect_lifecycle_events relation differs from graph relation: "
                    f"{event.relation.relation_id!r}"
                )
