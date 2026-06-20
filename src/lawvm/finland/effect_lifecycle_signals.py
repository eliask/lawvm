"""Typed Finland effect-lifecycle signals crossing process/replay phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from lawvm.core.ir import LegalAddress
from lawvm.finland.helpers import _norm_num_token

EffectLifecycleOverrideScopeKind = Literal["instrument", "section", "address", "mixed"]
EffectRelationSignalKind = Literal["pending_amendment", "meta_repeal"]
EffectRelationSignalRelationKind = Literal["modifies_effect", "repeals_effect"]


def _normalized_signal_string(subject: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{subject} must be a string")
    return value.strip()


def _normalized_section_labels(labels: Iterable[object]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for label in labels:
        if not isinstance(label, str):
            raise TypeError("lifecycle override scope section labels must be strings")
        if not label.strip():
            continue
        cleaned.append(_norm_num_token(label))
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class EffectLifecycleOverrideScope:
    """Typed target scope for source-backed lifecycle overrides.

    A bare legal label is not enough to identify its legal-address level. Section
    labels, exact addresses, and whole-instrument scope stay separate here so
    the effect graph never decodes a plain string by contextual guesswork.
    """

    kind: EffectLifecycleOverrideScopeKind
    labels: tuple[str, ...] = ()
    addresses: tuple[LegalAddress, ...] = ()

    def __post_init__(self) -> None:
        labels = _normalized_section_labels(self.labels)
        addresses = tuple(self.addresses)
        if not all(isinstance(address, LegalAddress) for address in addresses):
            raise TypeError("lifecycle override scope addresses must contain LegalAddress rows")
        if self.kind == "instrument":
            labels = ()
            addresses = ()
        elif self.kind == "section":
            if not labels:
                raise ValueError("section lifecycle override scope requires labels")
            addresses = ()
        elif self.kind == "address":
            if not addresses:
                raise ValueError("address lifecycle override scope requires addresses")
            labels = ()
        elif self.kind == "mixed":
            if not labels or not addresses:
                raise ValueError("mixed lifecycle override scope requires labels and addresses")
        else:
            raise ValueError(f"unknown lifecycle override scope kind: {self.kind!r}")
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "addresses", addresses)

    @classmethod
    def instrument(cls) -> "EffectLifecycleOverrideScope":
        return cls(kind="instrument")

    @classmethod
    def sections(cls, labels: Iterable[str]) -> "EffectLifecycleOverrideScope":
        cleaned = _normalized_section_labels(labels)
        if not cleaned or cleaned == ("*",):
            return cls.instrument()
        return cls(kind="section", labels=tuple(sorted(cleaned)))

    @classmethod
    def exact_addresses(
        cls, addresses: Iterable[LegalAddress]
    ) -> "EffectLifecycleOverrideScope":
        cleaned = tuple(sorted(tuple(addresses), key=str))
        if not cleaned:
            return cls.instrument()
        return cls(kind="address", addresses=cleaned)

    @classmethod
    def mixed(
        cls,
        *,
        labels: Iterable[str],
        addresses: Iterable[LegalAddress],
    ) -> "EffectLifecycleOverrideScope":
        cleaned_labels = _normalized_section_labels(labels)
        cleaned_addresses = tuple(sorted(tuple(addresses), key=str))
        if cleaned_labels and cleaned_addresses:
            return cls(kind="mixed", labels=tuple(sorted(cleaned_labels)), addresses=cleaned_addresses)
        if cleaned_addresses:
            return cls.exact_addresses(cleaned_addresses)
        return cls.sections(cleaned_labels)

    @property
    def key(self) -> str:
        if self.kind == "instrument":
            return "instrument:*"
        if self.kind == "section":
            return ",".join(f"section:{label}" for label in self.labels)
        if self.kind == "address":
            return ",".join(f"address:{address}" for address in self.addresses)
        section_part = ",".join(f"section:{label}" for label in self.labels)
        address_part = ",".join(f"address:{address}" for address in self.addresses)
        return f"mixed:{section_part}|{address_part}"

    @property
    def exact_target_address(self) -> LegalAddress | None:
        if self.kind == "address" and len(self.addresses) == 1:
            return self.addresses[0]
        return None

    def to_meta(self) -> dict[str, object]:
        row: dict[str, object] = {
            "scope_kind": self.kind,
            "scope_key": self.key,
        }
        if self.labels:
            row["scope_labels"] = list(self.labels)
        if self.addresses:
            row["scope_addresses"] = [str(address) for address in self.addresses]
        return row


@dataclass(frozen=True, slots=True)
class EffectLifecycleOverride:
    """Source instruction that changes another amendment effect's lifecycle."""

    source_statute: str
    target_statute: str
    scope: EffectLifecycleOverrideScope
    context: str
    effective: str = ""
    expiry: str = ""

    def __post_init__(self) -> None:
        source_statute = _normalized_signal_string(
            "EffectLifecycleOverride.source_statute",
            self.source_statute,
        )
        target_statute = _normalized_signal_string(
            "EffectLifecycleOverride.target_statute",
            self.target_statute,
        )
        context = _normalized_signal_string("EffectLifecycleOverride.context", self.context)
        effective = _normalized_signal_string(
            "EffectLifecycleOverride.effective",
            self.effective,
        )
        expiry = _normalized_signal_string("EffectLifecycleOverride.expiry", self.expiry)
        if not source_statute:
            raise ValueError("lifecycle override source_statute must be non-empty")
        if not target_statute:
            raise ValueError("lifecycle override target_statute must be non-empty")
        if not context:
            raise ValueError("lifecycle override context must be non-empty")
        if not effective and not expiry:
            raise ValueError("lifecycle override requires effective or expiry")
        object.__setattr__(self, "source_statute", source_statute)
        object.__setattr__(self, "target_statute", target_statute)
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "effective", effective)
        object.__setattr__(self, "expiry", expiry)

    def to_meta_row(self) -> dict[str, object]:
        row = {
            "source_statute": self.source_statute,
            "target_statute": self.target_statute,
            "context": self.context,
            **self.scope.to_meta(),
        }
        if self.effective:
            row["effective"] = self.effective
        if self.expiry:
            row["expiry"] = self.expiry
        return row


@dataclass(frozen=True, slots=True)
class EffectRelationSignal:
    """Typed source instruction relating one amendment instrument/effect to another."""

    signal_kind: EffectRelationSignalKind
    relation_kind: EffectRelationSignalRelationKind
    source_statute: str
    target_statute: str = ""
    target_title: str = ""
    base_parent_id: str = ""
    route_reason: str = ""
    message: str = ""
    source_finding: str = ""
    resolved: bool = False

    def __post_init__(self) -> None:
        signal_kind = _normalized_signal_string(
            "EffectRelationSignal.signal_kind",
            self.signal_kind,
        )
        relation_kind = _normalized_signal_string(
            "EffectRelationSignal.relation_kind",
            self.relation_kind,
        )
        source_statute = _normalized_signal_string(
            "EffectRelationSignal.source_statute",
            self.source_statute,
        )
        target_statute = _normalized_signal_string(
            "EffectRelationSignal.target_statute",
            self.target_statute,
        )
        target_title = _normalized_signal_string(
            "EffectRelationSignal.target_title",
            self.target_title,
        )
        base_parent_id = _normalized_signal_string(
            "EffectRelationSignal.base_parent_id",
            self.base_parent_id,
        )
        route_reason = _normalized_signal_string(
            "EffectRelationSignal.route_reason",
            self.route_reason,
        )
        message = _normalized_signal_string("EffectRelationSignal.message", self.message)
        source_finding = _normalized_signal_string(
            "EffectRelationSignal.source_finding",
            self.source_finding,
        )
        if signal_kind not in {"pending_amendment", "meta_repeal"}:
            raise ValueError(f"unknown effect relation signal kind: {self.signal_kind!r}")
        if relation_kind not in {"modifies_effect", "repeals_effect"}:
            raise ValueError(f"unknown effect relation signal relation kind: {self.relation_kind!r}")
        if not source_statute:
            raise ValueError("effect relation signal source_statute must be non-empty")
        if signal_kind == "pending_amendment" and relation_kind != "modifies_effect":
            raise ValueError("pending amendment relation signal must modify an effect")
        if signal_kind == "meta_repeal" and relation_kind != "repeals_effect":
            raise ValueError("meta-repeal relation signal must repeal an effect")
        if not isinstance(self.resolved, bool):
            raise ValueError("effect relation signal resolved must be a bool")
        if self.resolved and not target_statute:
            raise ValueError("resolved effect relation signal requires target_statute")
        object.__setattr__(self, "signal_kind", signal_kind)
        object.__setattr__(self, "relation_kind", relation_kind)
        object.__setattr__(self, "source_statute", source_statute)
        object.__setattr__(self, "target_statute", target_statute)
        object.__setattr__(self, "target_title", target_title)
        object.__setattr__(self, "base_parent_id", base_parent_id)
        object.__setattr__(self, "route_reason", route_reason)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "source_finding", source_finding)

    @classmethod
    def pending_amendment(
        cls,
        *,
        source_statute: str,
        target_statute: str,
        target_title: str = "",
        base_parent_id: str = "",
        message: str = "",
        source_finding: str = "",
        resolved: bool,
    ) -> "EffectRelationSignal":
        return cls(
            signal_kind="pending_amendment",
            relation_kind="modifies_effect",
            source_statute=source_statute,
            target_statute=target_statute,
            target_title=target_title,
            base_parent_id=base_parent_id,
            message=message,
            source_finding=source_finding,
            resolved=resolved,
        )

    @classmethod
    def meta_repeal(
        cls,
        *,
        source_statute: str,
        target_statute: str,
        route_reason: str = "",
        message: str = "",
        source_finding: str = "",
        resolved: bool,
    ) -> "EffectRelationSignal":
        return cls(
            signal_kind="meta_repeal",
            relation_kind="repeals_effect",
            source_statute=source_statute,
            target_statute=target_statute,
            route_reason=route_reason,
            message=message,
            source_finding=source_finding,
            resolved=resolved,
        )

    def to_meta_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "signal_kind": self.signal_kind,
            "relation_kind": self.relation_kind,
            "source_statute": self.source_statute,
            "resolved": self.resolved,
        }
        if self.target_statute:
            row["target_amendment_id"] = self.target_statute
        if self.target_title:
            row["target_amendment_title"] = self.target_title
        if self.base_parent_id:
            row["base_parent_id"] = self.base_parent_id
        if self.route_reason:
            row["route_reason"] = self.route_reason
        if self.message:
            row["message"] = self.message
        if self.source_finding:
            row["source_finding"] = self.source_finding
        return row
