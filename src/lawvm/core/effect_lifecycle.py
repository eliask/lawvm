"""Typed source-effect lifecycle carriers.

These records model relationships between source-backed effect declarations.
They sit above replay: executable parent-statute projection still happens via
``LegalOperation`` and ``TemporalEvent``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, get_args

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
EFFECT_RELATION_KINDS = frozenset(get_args(EffectRelationKind))
EFFECT_LIFECYCLE_EVENT_KINDS = frozenset(get_args(EffectLifecycleEventKind))

EffectExpiryConvention = Literal["exclusive_cutoff", "inclusive_valid_until"]
EffectRelationTargetResolutionKind = Literal[
    "target_effect_resolved",
    "target_instrument_only",
    "ambiguous_multiple_effects",
]
EffectDetailWireConverter = Callable[[Mapping[str, Any]], object]


def _normalized_source_ref_string(subject: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{subject} must be a string")
    return value.strip()


def _validate_string_mapping_keys(subject: str, value: object) -> None:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{subject} keys must be strings")
            _validate_string_mapping_keys(f"{subject}.{key}", inner)
    elif isinstance(value, list | tuple | set | frozenset):
        for inner in value:
            _validate_string_mapping_keys(subject, inner)


def _freeze_effect_detail(subject: str, detail: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(detail, Mapping):
        raise TypeError(f"{subject} must be a mapping")
    _validate_string_mapping_keys(subject, detail)
    return freeze_mapping(detail)


def _default_effect_detail_wire(detail: Mapping[str, Any]) -> object:
    return dict(detail)


def _require_effect_ref(subject: str, value: object) -> "EffectRef":
    if not isinstance(value, EffectRef):
        raise TypeError(f"{subject} must contain EffectRef records")
    return value


def _require_effect_relation(subject: str, value: object) -> "EffectRelation":
    if not isinstance(value, EffectRelation):
        raise TypeError(f"{subject} must contain EffectRelation records")
    return value


def _require_effect_lifecycle_event(subject: str, value: object) -> "EffectLifecycleEvent":
    if not isinstance(value, EffectLifecycleEvent):
        raise TypeError(f"{subject} must contain EffectLifecycleEvent records")
    return value


def _expected_temporal_kind_for_lifecycle(kind: EffectLifecycleEventKind) -> str | None:
    if kind in {"commence_effect", "change_effect_commencement"}:
        return "commence"
    if kind in {"expire_effect", "change_effect_expiry", "repeal_effect"}:
        return "expire"
    if kind == "suspend_effect":
        return "suspend"
    if kind == "revive_effect":
        return "revive"
    return None


def _projected_lifecycle_expires(
    *,
    effective: str,
    expires: str,
    expiry_convention: EffectExpiryConvention,
) -> str:
    projected = expires or effective
    if projected and expiry_convention == "inclusive_valid_until":
        return expires_on_from_valid_until(dt.date.fromisoformat(projected)).isoformat()
    return projected


@dataclass(frozen=True, slots=True)
class SourceInstrumentRef:
    """Stable identity for a source instrument that declares or modifies effects."""

    instrument_id: str
    title: str = ""
    enacted: str = ""
    effective: str = ""
    expires: str = ""

    def __post_init__(self) -> None:
        instrument_id = _normalized_source_ref_string(
            "SourceInstrumentRef.instrument_id",
            self.instrument_id,
        )
        if not instrument_id:
            raise ValueError("SourceInstrumentRef.instrument_id must be non-empty")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(
            self,
            "title",
            _normalized_source_ref_string("SourceInstrumentRef.title", self.title),
        )
        object.__setattr__(
            self,
            "enacted",
            _normalized_source_ref_string("SourceInstrumentRef.enacted", self.enacted),
        )
        object.__setattr__(
            self,
            "effective",
            _normalized_source_ref_string("SourceInstrumentRef.effective", self.effective),
        )
        object.__setattr__(
            self,
            "expires",
            _normalized_source_ref_string("SourceInstrumentRef.expires", self.expires),
        )

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
        path_parts: list[str] = []
        for part in self.path:
            if not isinstance(part, str):
                raise TypeError("SourceProvisionRef.path must contain string parts")
            if part.strip():
                path_parts.append(part.strip())
        object.__setattr__(self, "path", tuple(path_parts))
        object.__setattr__(
            self,
            "span_id",
            _normalized_source_ref_string("SourceProvisionRef.span_id", self.span_id),
        )
        if not isinstance(self.text_excerpt, str):
            raise TypeError("SourceProvisionRef.text_excerpt must be a string")
        object.__setattr__(
            self,
            "text_excerpt",
            self.text_excerpt,
        )
        object.__setattr__(
            self,
            "rule_id",
            _normalized_source_ref_string("SourceProvisionRef.rule_id", self.rule_id),
        )

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
        effect_id = _normalized_source_ref_string("EffectRef.effect_id", self.effect_id)
        if not effect_id:
            raise ValueError("EffectRef.effect_id must be non-empty")
        if not isinstance(self.source_instrument, SourceInstrumentRef):
            raise ValueError("EffectRef.source_instrument must be a SourceInstrumentRef")
        object.__setattr__(self, "effect_id", effect_id)
        object.__setattr__(
            self,
            "target_statute",
            _normalized_source_ref_string("EffectRef.target_statute", self.target_statute),
        )
        object.__setattr__(
            self,
            "projection_group_id",
            _normalized_source_ref_string(
                "EffectRef.projection_group_id",
                self.projection_group_id,
            ),
        )
        if self.target_address is not None and not isinstance(self.target_address, LegalAddress):
            raise ValueError("EffectRef.target_address must be a LegalAddress when provided")
        if self.source_provision is not None and not isinstance(self.source_provision, SourceProvisionRef):
            raise ValueError("EffectRef.source_provision must be a SourceProvisionRef when provided")


@dataclass(frozen=True, slots=True)
class EffectRelationTargetResolution:
    """How precisely an effect relation target is bound inside the effect graph."""

    kind: EffectRelationTargetResolutionKind
    matched_effect_count: int = 0
    non_executable_reason: str = ""

    def __post_init__(self) -> None:
        kind = _normalized_source_ref_string("EffectRelationTargetResolution.kind", self.kind)
        if kind not in {
            "target_effect_resolved",
            "target_instrument_only",
            "ambiguous_multiple_effects",
        }:
            raise ValueError(f"unsupported EffectRelationTargetResolution.kind: {self.kind!r}")
        if not isinstance(self.matched_effect_count, int):
            raise TypeError("EffectRelationTargetResolution.matched_effect_count must be an int")
        if self.matched_effect_count < 0:
            raise ValueError("EffectRelationTargetResolution.matched_effect_count must be non-negative")
        if kind == "ambiguous_multiple_effects" and self.matched_effect_count < 2:
            raise ValueError("ambiguous effect target resolution requires at least two matched effects")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "non_executable_reason",
            _normalized_source_ref_string(
                "EffectRelationTargetResolution.non_executable_reason",
                self.non_executable_reason,
            ),
        )


@dataclass(frozen=True, slots=True)
class EffectRelation:
    """A witnessed relation from one effect or instrument to another."""

    relation_id: str
    kind: EffectRelationKind
    source_provision: SourceProvisionRef
    target_effect: Optional[EffectRef] = None
    target_instrument: Optional[SourceInstrumentRef] = None
    source_effect: Optional[EffectRef] = None
    target_resolution: Optional[EffectRelationTargetResolution] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        relation_id = _normalized_source_ref_string(
            "EffectRelation.relation_id",
            self.relation_id,
        )
        if not relation_id:
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
        target_resolution = self.target_resolution
        if target_resolution is None:
            target_resolution = EffectRelationTargetResolution(
                kind=(
                    "target_effect_resolved"
                    if self.target_effect is not None
                    else "target_instrument_only"
                ),
                matched_effect_count=1 if self.target_effect is not None else 0,
            )
        elif not isinstance(target_resolution, EffectRelationTargetResolution):
            raise TypeError(
                "EffectRelation.target_resolution must be an "
                "EffectRelationTargetResolution when provided"
            )
        if self.target_effect is not None and target_resolution.kind != "target_effect_resolved":
            raise ValueError("EffectRelation target_effect requires target_effect_resolved status")
        if self.target_instrument is not None and target_resolution.kind == "target_effect_resolved":
            raise ValueError("EffectRelation target_instrument cannot use target_effect_resolved status")
        if (
            target_resolution.kind == "ambiguous_multiple_effects"
            and target_resolution.matched_effect_count < 2
        ):
            raise ValueError("ambiguous effect target resolution requires at least two matched effects")
        object.__setattr__(self, "relation_id", relation_id)
        object.__setattr__(self, "target_resolution", target_resolution)
        object.__setattr__(
            self,
            "detail",
            _freeze_effect_detail("EffectRelation.detail", self.detail),
        )


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
    intended_lifecycle_kind: Optional[EffectLifecycleEventKind] = None
    intended_relation_kind: Optional[EffectRelationKind] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        lifecycle_event_id = _normalized_source_ref_string(
            "EffectLifecycleEvent.lifecycle_event_id",
            self.lifecycle_event_id,
        )
        if not lifecycle_event_id:
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
        effective = _normalized_source_ref_string(
            "EffectLifecycleEvent.effective",
            self.effective,
        )
        expires = _normalized_source_ref_string("EffectLifecycleEvent.expires", self.expires)
        if self.expiry_convention not in {"exclusive_cutoff", "inclusive_valid_until"}:
            raise ValueError(f"unsupported EffectLifecycleEvent.expiry_convention: {self.expiry_convention!r}")
        if not isinstance(self.executable, bool):
            raise TypeError("EffectLifecycleEvent.executable must be a bool")
        intended_lifecycle_kind = self.intended_lifecycle_kind
        if intended_lifecycle_kind is not None:
            intended_lifecycle_kind = _normalized_source_ref_string(
                "EffectLifecycleEvent.intended_lifecycle_kind",
                intended_lifecycle_kind,
            )
            if intended_lifecycle_kind not in EFFECT_LIFECYCLE_EVENT_KINDS:
                raise ValueError(
                    "unsupported EffectLifecycleEvent.intended_lifecycle_kind: "
                    f"{self.intended_lifecycle_kind!r}"
                )
        intended_relation_kind = self.intended_relation_kind
        if intended_relation_kind is not None:
            intended_relation_kind = _normalized_source_ref_string(
                "EffectLifecycleEvent.intended_relation_kind",
                intended_relation_kind,
            )
            if intended_relation_kind not in EFFECT_RELATION_KINDS:
                raise ValueError(
                    "unsupported EffectLifecycleEvent.intended_relation_kind: "
                    f"{self.intended_relation_kind!r}"
                )
        if self.expiry_convention == "inclusive_valid_until" and expires:
            dt.date.fromisoformat(expires)
        object.__setattr__(self, "lifecycle_event_id", lifecycle_event_id)
        object.__setattr__(self, "effective", effective)
        object.__setattr__(self, "expires", expires)
        object.__setattr__(self, "intended_lifecycle_kind", intended_lifecycle_kind)
        object.__setattr__(self, "intended_relation_kind", intended_relation_kind)
        if self.temporal_event is not None and not self.executable:
            raise ValueError("non-executable EffectLifecycleEvent cannot carry temporal_event")
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
        relation_allowed_kinds = {
            "change_effect_commencement",
            "change_effect_expiry",
            "repeal_effect",
            "unresolved_effect_target",
        }
        if self.relation is not None and self.kind not in relation_allowed_kinds:
            raise ValueError(
                "EffectLifecycleEvent relation is only supported for "
                "effect-modifying or unresolved target events"
            )
        if self.relation is not None and self.relation.source_provision != self.source_provision:
            raise ValueError(
                "EffectLifecycleEvent source_provision must match relation source_provision"
            )
        if self.kind != "unresolved_effect_target" and self.effect is None:
            raise ValueError("resolved EffectLifecycleEvent requires effect")
        if self.executable and self.effect is None:
            raise ValueError("executable EffectLifecycleEvent requires effect")
        if self.temporal_event is not None:
            expected_temporal_kind = _expected_temporal_kind_for_lifecycle(self.kind)
            if expected_temporal_kind is None:
                raise ValueError(
                    "EffectLifecycleEvent kind cannot carry temporal_event: "
                    f"{self.kind!r}"
                )
            if self.temporal_event.kind != expected_temporal_kind:
                raise ValueError(
                    "EffectLifecycleEvent temporal_event kind must match lifecycle kind: "
                    f"expected {expected_temporal_kind!r}, got {self.temporal_event.kind!r}"
                )
            if self.kind in {"commence_effect", "change_effect_commencement"}:
                if self.temporal_event.effective != effective:
                    raise ValueError(
                        "EffectLifecycleEvent temporal_event effective date must "
                        "match lifecycle effective date"
                    )
            elif self.kind in {"expire_effect", "change_effect_expiry", "repeal_effect"}:
                expected_expires = _projected_lifecycle_expires(
                    effective=effective,
                    expires=expires,
                    expiry_convention=self.expiry_convention,
                )
                if self.temporal_event.expires != expected_expires:
                    raise ValueError(
                        "EffectLifecycleEvent temporal_event expires date must "
                        "match lifecycle expiry projection"
                    )
            elif self.temporal_event.effective != effective:
                raise ValueError(
                    "EffectLifecycleEvent temporal_event effective date must "
                    "match lifecycle effective date"
                )
            if self.effect is not None:
                if (
                    self.effect.target_statute
                    and self.temporal_event.scope.target_statute
                    and self.temporal_event.scope.target_statute != self.effect.target_statute
                ):
                    raise ValueError(
                        "EffectLifecycleEvent temporal_event scope target_statute "
                        "must match effect target_statute"
                    )
                if (
                    self.effect.target_address is not None
                    and self.temporal_event.scope.exact_addresses
                    != (self.effect.target_address,)
                ):
                    raise ValueError(
                        "EffectLifecycleEvent temporal_event exact address scope "
                        "must match effect target_address"
                    )
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
        object.__setattr__(
            self,
            "detail",
            _freeze_effect_detail("EffectLifecycleEvent.detail", self.detail),
        )


def append_unique_effect_ref(
    target: list[EffectRef],
    effect: EffectRef,
    *,
    subject: str,
) -> None:
    effect = _require_effect_ref(subject, effect)
    for existing_value in target:
        existing = _require_effect_ref(subject, existing_value)
        if existing.effect_id != effect.effect_id:
            continue
        if existing != effect:
            raise ValueError(
                f"{subject} conflicting duplicate effect_id: {effect.effect_id!r}"
            )
        return
    target.append(effect)


def append_unique_effect_refs(
    target: list[EffectRef],
    effects: Iterable[EffectRef],
    *,
    subject: str,
) -> None:
    by_effect_id: dict[str, EffectRef] = {}
    for existing_value in target:
        existing = _require_effect_ref(subject, existing_value)
        by_effect_id.setdefault(existing.effect_id, existing)
    for effect in effects:
        effect = _require_effect_ref(subject, effect)
        existing = by_effect_id.get(effect.effect_id)
        if existing is not None:
            if existing != effect:
                raise ValueError(
                    f"{subject} conflicting duplicate effect_id: {effect.effect_id!r}"
                )
            continue
        target.append(effect)
        by_effect_id[effect.effect_id] = effect


def merge_unique_effect_refs(
    *lanes: Iterable[EffectRef],
    subject: str,
) -> tuple[EffectRef, ...]:
    merged: list[EffectRef] = []
    for lane in lanes:
        append_unique_effect_refs(merged, lane, subject=subject)
    return tuple(merged)


def append_unique_effect_relation(
    target: list[EffectRelation],
    relation: EffectRelation,
    *,
    subject: str,
) -> None:
    relation = _require_effect_relation(subject, relation)
    for existing_value in target:
        existing = _require_effect_relation(subject, existing_value)
        if existing.relation_id != relation.relation_id:
            continue
        if existing != relation:
            raise ValueError(
                f"{subject} conflicting duplicate relation_id: {relation.relation_id!r}"
            )
        return
    target.append(relation)


def append_unique_effect_relations(
    target: list[EffectRelation],
    relations: Iterable[EffectRelation],
    *,
    subject: str,
) -> None:
    for relation in relations:
        append_unique_effect_relation(target, relation, subject=subject)


def merge_unique_effect_relations(
    *lanes: Iterable[EffectRelation],
    subject: str,
) -> tuple[EffectRelation, ...]:
    merged: list[EffectRelation] = []
    for lane in lanes:
        append_unique_effect_relations(merged, lane, subject=subject)
    return tuple(merged)


def append_unique_effect_lifecycle_event(
    target: list[EffectLifecycleEvent],
    event: EffectLifecycleEvent,
    *,
    subject: str,
) -> None:
    event = _require_effect_lifecycle_event(subject, event)
    for existing_value in target:
        existing = _require_effect_lifecycle_event(subject, existing_value)
        if existing.lifecycle_event_id != event.lifecycle_event_id:
            continue
        if existing != event:
            raise ValueError(
                f"{subject} conflicting duplicate lifecycle_event_id: "
                f"{event.lifecycle_event_id!r}"
            )
        return
    target.append(event)


def append_unique_effect_lifecycle_events(
    target: list[EffectLifecycleEvent],
    events: Iterable[EffectLifecycleEvent],
    *,
    subject: str,
) -> None:
    for event in events:
        append_unique_effect_lifecycle_event(target, event, subject=subject)


def merge_unique_effect_lifecycle_events(
    *lanes: Iterable[EffectLifecycleEvent],
    subject: str,
) -> tuple[EffectLifecycleEvent, ...]:
    merged: list[EffectLifecycleEvent] = []
    for lane in lanes:
        append_unique_effect_lifecycle_events(merged, lane, subject=subject)
    return tuple(merged)


def validate_effect_graph_unique_ids(
    *,
    subject: str,
    source_effects: tuple[EffectRef, ...],
    effect_relations: tuple[EffectRelation, ...],
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...],
) -> None:
    """Validate that graph carriers do not contain duplicate stable IDs."""

    seen_effect_ids: set[str] = set()
    for effect in source_effects:
        if effect.effect_id in seen_effect_ids:
            raise ValueError(
                f"{subject}.source_effects duplicate effect_id: {effect.effect_id!r}"
            )
        seen_effect_ids.add(effect.effect_id)
    seen_relation_ids: set[str] = set()
    for relation in effect_relations:
        if relation.relation_id in seen_relation_ids:
            raise ValueError(
                f"{subject}.effect_relations duplicate relation_id: "
                f"{relation.relation_id!r}"
            )
        seen_relation_ids.add(relation.relation_id)
    seen_lifecycle_event_ids: set[str] = set()
    for event in effect_lifecycle_events:
        if event.lifecycle_event_id in seen_lifecycle_event_ids:
            raise ValueError(
                f"{subject}.effect_lifecycle_events duplicate "
                f"lifecycle_event_id: {event.lifecycle_event_id!r}"
            )
        seen_lifecycle_event_ids.add(event.lifecycle_event_id)


def legal_address_wire(address: Optional[LegalAddress]) -> Optional[dict[str, object]]:
    """Project a typed legal address into the stable effect-graph wire shape."""

    if address is None:
        return None
    payload: dict[str, object] = {
        "path": tuple({"kind": kind, "label": label} for kind, label in address.path),
    }
    if address.special:
        payload["special"] = str(address.special)
    return payload


def source_instrument_wire(instrument: SourceInstrumentRef) -> dict[str, object]:
    """Project a source instrument reference into the effect-graph wire shape."""

    return {
        "instrument_id": instrument.instrument_id,
        "title": instrument.title,
        "enacted": instrument.enacted,
        "effective": instrument.effective,
        "expires": instrument.expires,
    }


def source_provision_wire(
    provision: Optional[SourceProvisionRef],
) -> Optional[dict[str, object]]:
    """Project a source witness reference into the effect-graph wire shape."""

    if provision is None:
        return None
    return {
        "instrument": source_instrument_wire(provision.instrument),
        "path": provision.path,
        "span_id": provision.span_id,
        "text_excerpt": provision.text_excerpt,
        "rule_id": provision.rule_id,
        "witness_id": provision.witness_id,
    }


def effect_ref_wire(effect: EffectRef) -> dict[str, object]:
    """Project one source-backed effect declaration into the stable wire shape."""

    return {
        "effect_id": effect.effect_id,
        "source_instrument": source_instrument_wire(effect.source_instrument),
        "target_statute": effect.target_statute,
        "target_address": legal_address_wire(effect.target_address),
        "projection_group_id": effect.projection_group_id,
        "source_provision": source_provision_wire(effect.source_provision),
    }


def effect_relation_wire(
    relation: EffectRelation,
    *,
    detail_converter: EffectDetailWireConverter = _default_effect_detail_wire,
) -> dict[str, object]:
    """Project one witnessed effect relation into the stable wire shape."""

    target_resolution = relation.target_resolution
    if target_resolution is None:
        raise ValueError("EffectRelation target_resolution was not normalized")
    return {
        "relation_id": relation.relation_id,
        "kind": relation.kind,
        "source_provision": source_provision_wire(relation.source_provision),
        "target_effect_id": (
            relation.target_effect.effect_id if relation.target_effect is not None else ""
        ),
        "target_instrument": (
            source_instrument_wire(relation.target_instrument)
            if relation.target_instrument is not None
            else None
        ),
        "source_effect_id": (
            relation.source_effect.effect_id if relation.source_effect is not None else ""
        ),
        "target_resolution": {
            "kind": target_resolution.kind,
            "matched_effect_count": target_resolution.matched_effect_count,
            "non_executable_reason": target_resolution.non_executable_reason,
        },
        "detail": detail_converter(relation.detail),
    }


def temporal_event_ref_wire(event: Optional[TemporalEvent]) -> Optional[dict[str, object]]:
    """Project a temporal event reference embedded in lifecycle wire rows."""

    if event is None:
        return None
    return {
        "event_id": event.event_id,
        "kind": event.kind,
        "effective": event.effective,
        "expires": event.expires,
        "group_id": event.group_id,
    }


def effect_lifecycle_event_wire(
    event: EffectLifecycleEvent,
    *,
    detail_converter: EffectDetailWireConverter = _default_effect_detail_wire,
) -> dict[str, object]:
    """Project one effect lifecycle event into the stable wire shape."""

    return {
        "lifecycle_event_id": event.lifecycle_event_id,
        "kind": event.kind,
        "source_provision": source_provision_wire(event.source_provision),
        "effect_id": event.effect.effect_id if event.effect is not None else "",
        "relation_id": event.relation.relation_id if event.relation is not None else "",
        "effective": event.effective,
        "expires": event.expires,
        "expiry_convention": event.expiry_convention,
        "temporal_event": temporal_event_ref_wire(event.temporal_event),
        "executable": event.executable,
        "intended_lifecycle_kind": event.intended_lifecycle_kind or "",
        "intended_relation_kind": event.intended_relation_kind or "",
        "detail": detail_converter(event.detail),
    }


def effect_graph_wire(
    *,
    source_effects: tuple[EffectRef, ...],
    effect_relations: tuple[EffectRelation, ...],
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...],
    detail_converter: EffectDetailWireConverter = _default_effect_detail_wire,
) -> dict[str, object]:
    """Project a closed effect graph into core's stable read-model shape."""

    validate_effect_graph_closure(
        subject="effect_graph_wire",
        source_effects=source_effects,
        effect_relations=effect_relations,
        effect_lifecycle_events=effect_lifecycle_events,
    )
    return {
        "source_effects": tuple(effect_ref_wire(effect) for effect in source_effects),
        "effect_relations": tuple(
            effect_relation_wire(relation, detail_converter=detail_converter)
            for relation in effect_relations
        ),
        "effect_lifecycle_events": tuple(
            effect_lifecycle_event_wire(event, detail_converter=detail_converter)
            for event in effect_lifecycle_events
        ),
    }


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
    events: Iterable[EffectLifecycleEvent],
) -> tuple[TemporalEvent, ...]:
    lowered: list[TemporalEvent] = []
    for event in events:
        if not isinstance(event, EffectLifecycleEvent):
            raise TypeError(
                "lower_lifecycle_events_to_temporal_events requires "
                "EffectLifecycleEvent records"
            )
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
