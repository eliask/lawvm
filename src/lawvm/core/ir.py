"""Core IR carriers for legal replay and JSON-safe projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Tuple

from lawvm.core.frozen_values import FrozenDict, _freeze_value, _jsonable_value
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum


@dataclass(frozen=True)
class LegalAddress:
    """Jurisdiction-agnostic address for a legal structure element."""

    path: Tuple[Tuple[str, str], ...]
    special: Optional[FacetKind] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", tuple(self.path))
        for i, (kind, _label) in enumerate(self.path):
            if not kind:
                raise ValueError(f"LegalAddress path element {i} has empty kind: {self.path!r}")

    def depth(self) -> int:
        return len(self.path)

    def parent(self) -> Optional[LegalAddress]:
        if len(self.path) <= 1:
            return None
        return LegalAddress(path=self.path[:-1])

    def leaf_kind(self) -> str:
        return self.path[-1][0] if self.path else ""

    def leaf_label(self) -> str:
        return self.path[-1][1] if self.path else ""

    def __str__(self) -> str:
        parts = "/".join(f"{k}:{lbl}" for k, lbl in self.path)
        if self.special:
            parts += f"/{self.special}"
        return parts


@dataclass(frozen=True)
class ScopePredicate:
    """A condition on when or where a provision version or operation applies."""

    dimension: str
    includes: FrozenSet[str]

    def __post_init__(self) -> None:
        if not self.dimension:
            raise ValueError("ScopePredicate.dimension must be non-empty")
        object.__setattr__(
            self,
            "includes",
            frozenset(str(value) for value in self.includes if str(value)),
        )


@dataclass(frozen=True)
class TextSelector:
    """Typed selector for text-level operations."""

    match_text: str
    occurrence: int = 0

    def __post_init__(self) -> None:
        if not self.match_text:
            raise ValueError("TextSelector.match_text must be non-empty")
        if self.occurrence < 0:
            raise ValueError("TextSelector.occurrence must be >= 0")


@dataclass(frozen=True)
class TextPatchSpec:
    """Typed text-patch payload carried by text-level operations."""

    kind: TextPatchKindEnum
    selector: TextSelector
    replacement: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind is TextPatchKindEnum.REPLACE and self.replacement is None:
            raise ValueError("TextPatchSpec(kind='replace') requires replacement")
        if self.kind is TextPatchKindEnum.DELETE and self.replacement is not None:
            raise ValueError("TextPatchSpec(kind='delete') must not set replacement")


@dataclass(frozen=True)
class LegalOperation:
    """A single compiled legal state change."""

    op_id: str
    sequence: int
    action: StructuralAction
    target: LegalAddress
    payload: Optional["IRNode"] = None
    anchor: Optional[LegalAddress] = None
    destination: Optional[LegalAddress] = None
    source: Optional[OperationSource] = None
    applicability: Tuple[ScopePredicate, ...] = ()
    provenance_tags: Tuple[str, ...] = ()
    text_patch: Optional[TextPatchSpec] = None
    group_id: Optional[str] = None
    witness_rule_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, StructuralAction):
            raise TypeError(
                f"LegalOperation.action must be StructuralAction, got {type(self.action).__name__}"
            )
        object.__setattr__(self, "applicability", tuple(self.applicability))
        object.__setattr__(self, "provenance_tags", tuple(self.provenance_tags))
        if self.anchor is not None and self.action is not StructuralAction.INSERT:
            raise ValueError(f"LegalOperation anchor is only valid for insert; got action={self.action!r}")
        if self.destination is not None and self.action is not StructuralAction.RENUMBER:
            raise ValueError(f"LegalOperation destination is only valid for renumber; got action={self.action!r}")
        if self.text_patch is not None and self.action not in {
            StructuralAction.TEXT_REPLACE,
            StructuralAction.TEXT_REPEAL,
            StructuralAction.REPLACE,
        }:
            raise ValueError(
                "LegalOperation text_patch is only valid for text_replace/text_repeal/replace "
                f"got action={self.action!r}"
            )

@dataclass
class ProvisionVersion:
    """A single version of a provision in the temporal graph."""

    effective: str
    enacted: str = ""
    expires: str = ""
    variant_kind: Literal["permanent", "temporary"] = "permanent"
    content: Optional["IRNode"] = None
    source: Optional[OperationSource] = None
    applicability: List[ScopePredicate] = field(default_factory=list)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.effective:
            raise ValueError("ProvisionVersion requires non-empty effective date")
        if self.variant_kind not in {"permanent", "temporary"}:
            raise ValueError("ProvisionVersion.variant_kind must be one of 'permanent' or 'temporary'")
        if self.expires and self.effective > self.expires:
            raise ValueError(f"ProvisionVersion expires ({self.expires}) before effective ({self.effective})")
        object.__setattr__(self, "applicability", tuple(self.applicability))
        if self.expires and self.effective == self.expires:
            import warnings

            warnings.warn(
                f"ProvisionVersion effective == expires ({self.effective}) — "
                f"empty same-day temporal interval "
                f"(source={self.source.statute_id if self.source and self.source.statute_id else '?'})",
                stacklevel=2,
            )


@dataclass
class ProvisionTimeline:
    """Complete version history of a single addressable provision."""

    address: LegalAddress
    versions: List[ProvisionVersion] = field(default_factory=list)


@dataclass(frozen=True)
class IRNode:
    """Immutable tree node used across the replay-facing IR."""

    kind: IRNodeKind
    label: Optional[str] = None
    text: str = ""
    attrs: Mapping[str, Any] = field(default_factory=FrozenDict)
    children: Tuple["IRNode", ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("IRNode.kind must be non-empty")
        object.__setattr__(
            self, "attrs", FrozenDict({key: _freeze_value(value) for key, value in dict(self.attrs).items()})
        )
        object.__setattr__(self, "children", tuple(self.children))

    def to_jsonable_dict(self) -> Dict[str, Any]:
        return {
            "kind": str(self.kind),
            "label": self.label,
            "text": self.text,
            "attrs": _jsonable_value(self.attrs, path="IRNode.attrs"),
            "children": [child.to_jsonable_dict() for child in self.children],
        }


@dataclass(frozen=True, init=False)
class IRStatute:
    """Immutable statute container with authoritative supplements."""

    statute_id: str
    title: str
    body: IRNode
    supplements: Tuple[IRNode, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __init__(
        self,
        *,
        statute_id: str,
        title: str,
        body: IRNode,
        supplements: Optional[List[IRNode] | Tuple[IRNode, ...]] = None,
        metadata: Optional[Dict[str, Any] | Mapping[str, Any]] = None,
    ) -> None:
        object.__setattr__(self, "statute_id", statute_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "supplements", tuple(supplements or ()))
        object.__setattr__(
            self,
            "metadata",
            FrozenDict({key: _freeze_value(value) for key, value in dict(metadata or {}).items()}),
        )

    def to_jsonable_dict(self) -> Dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "title": self.title,
            "body": self.body.to_jsonable_dict(),
            "supplements": [s.to_jsonable_dict() for s in self.supplements],
            "metadata": _jsonable_value(self.metadata, path="IRStatute.metadata"),
        }
