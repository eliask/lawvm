"""Typed Finland effect-lifecycle signals crossing process/replay phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from lawvm.core.ir import LegalAddress

EffectLifecycleOverrideScopeKind = Literal["instrument", "section", "address", "mixed"]


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
        labels = tuple(str(label).strip() for label in self.labels if str(label).strip())
        addresses = tuple(self.addresses)
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
    def sections(cls, labels: Iterable[object]) -> "EffectLifecycleOverrideScope":
        cleaned = tuple(str(label).strip() for label in labels if str(label).strip())
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
        labels: Iterable[object],
        addresses: Iterable[LegalAddress],
    ) -> "EffectLifecycleOverrideScope":
        cleaned_labels = tuple(str(label).strip() for label in labels if str(label).strip())
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
        source_statute = str(self.source_statute).strip()
        target_statute = str(self.target_statute).strip()
        context = str(self.context).strip()
        effective = str(self.effective).strip()
        expiry = str(self.expiry).strip()
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
