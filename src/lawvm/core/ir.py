"""Core IR carriers for legal replay and JSON-safe projections."""

from __future__ import annotations
from typing_extensions import override

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Tuple

from lawvm.core.frozen_values import FrozenDict, _freeze_value, _jsonable_value
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum


@dataclass(frozen=True, slots=True)
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

    def has_prefix(self, prefix: "LegalAddress") -> bool:
        """Return True when ``prefix`` matches this address path and facet."""

        if not self.has_path_prefix(prefix):
            return False
        if prefix.special:
            return prefix.special == self.special
        return True

    def has_path_prefix(self, prefix: "LegalAddress | Tuple[Tuple[str, str], ...]") -> bool:
        """Return True when ``prefix`` matches this address path, ignoring facets."""

        prefix_path = prefix.path if isinstance(prefix, LegalAddress) else tuple(prefix)
        if len(prefix_path) > len(self.path):
            return False
        return self.path[: len(prefix_path)] == prefix_path

    def leaf_kind(self) -> str:
        return self.path[-1][0] if self.path else ""

    def leaf_label(self) -> str:
        return self.path[-1][1] if self.path else ""

    @override
    def __str__(self) -> str:
        parts = "/".join(f"{k}:{lbl}" for k, lbl in self.path)
        if self.special:
            parts += f"/{self.special}"
        return parts


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class TextSelector:
    """Typed selector for text-level operations."""

    match_text: str
    occurrence: int = 0
    end_occurrence: int = 0
    # Optional second text anchor that BOUNDS the deletion on the right when the
    # op deletes from `match_text` THROUGH `end_match_text` (inclusive), rather
    # than from `match_text` to the end of the target node. Used by the US
    # bounded through-tail strike family: "striking 'OLD' and all that follows
    # through 'END' and inserting 'NEW'" deletes [OLD..END] then inserts NEW.
    # When ``None``, the existing single-anchor / last-occurrence semantics
    # (``occurrence``) apply unchanged, so this field is purely additive for
    # frontends that do not need it.
    end_match_text: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.match_text:
            raise ValueError("TextSelector.match_text must be non-empty")
        if self.occurrence < -1:
            raise ValueError("TextSelector.occurrence must be >= -1")
        if self.end_occurrence < 0:
            raise ValueError("TextSelector.end_occurrence must be >= 0")


@dataclass(frozen=True, slots=True)
class TextPatchSpec:
    """Typed text-patch payload carried by text-level operations."""

    kind: TextPatchKindEnum
    selector: TextSelector
    replacement: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.APPEND} and self.replacement is None:
            raise ValueError(f"TextPatchSpec(kind={self.kind.value!r}) requires replacement")
        if self.kind is TextPatchKindEnum.DELETE and self.replacement is not None:
            raise ValueError("TextPatchSpec(kind='delete') must not set replacement")


class LegalOperationPayloadActionError(ValueError):
    """A ``LegalOperation`` payload contradicts its structural action.

    Raised when an op's ``payload`` shape is incompatible with its ``action``:
    a repeal action carrying a non-tombstone content payload (a payload that is
    NOT a ``lawvm_repeal_placeholder`` tombstone). Mirrors the kind↔payload
    closure that ``core.canonical_intent.Repeal`` enforces one layer up, applied
    on the lower-level carrier that has 400+ construction sites.
    """


# Structural actions whose semantics are "remove the target". A repeal carries
# either NO payload, or a repeal-placeholder tombstone (the apply path treats
# REPEAL and a placeholder payload as the same "repeal snapshot" class — see
# finland/apply_runtime_support.py: ``_is_repeal_snapshot``). Any OTHER content
# payload contradicts the action and is rejected.
_REPEAL_ACTIONS: FrozenSet[StructuralAction] = frozenset(
    {StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL}
)


def _is_repeal_placeholder_payload(payload: "IRNode") -> bool:
    """True when ``payload`` is a repeal-placeholder tombstone (not real content).

    A repeal may legitimately carry the tombstone IR it leaves behind; that
    payload is identified by the authoritative ``lawvm_repeal_placeholder`` attr
    (finland/apply_ir_ops.py: ``_build_repeal_placeholder*``).
    """

    return payload.attrs.get("lawvm_repeal_placeholder") == "1"


def _payload_has_substantive_content(payload: "IRNode") -> bool:
    """True iff a payload carries real replacement content — non-empty text or
    any children — as opposed to an empty metadata/selection carrier.

    A repeal must not carry *substantive* content (that contradicts removing the
    target), but an otherwise-empty CONTENT node used purely to carry attrs is
    legitimate: e.g. Estonia attaches ``subsection_selection_meta`` in ``attrs``
    on an empty-text/zero-child node to encode a repeal RANGE
    (tests/test_ee_apply_semantics.py). That carries no replacement content, so
    it is permitted; only real content is forbidden.
    """

    return bool((payload.text or "").strip()) or bool(payload.children)


@dataclass(frozen=True, slots=True)
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
    # Frontend-owned typed riders. Core stores these carriers but does not
    # interpret jurisdiction-local values.
    scope_confidence: Any = None
    move_clause_target_unit_kind: Optional[str] = None

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
        # payload↔action closure: a repeal action that carries SUBSTANTIVE
        # replacement content contradicts itself. A repeal may carry NO payload,
        # the repeal-placeholder tombstone it leaves behind, or an empty
        # metadata/selection carrier (empty text + no children) — but never real
        # replacement content.
        if (
            self.payload is not None
            and self.action in _REPEAL_ACTIONS
            and not _is_repeal_placeholder_payload(self.payload)
            and _payload_has_substantive_content(self.payload)
        ):
            raise LegalOperationPayloadActionError(
                f"LegalOperation(action={self.action!r}) must not carry a substantive content "
                f"payload: a repeal removes its target. Got payload kind={self.payload.kind!r} "
                f"label={self.payload.label!r} (children={len(self.payload.children)}); "
                "only None, a lawvm_repeal_placeholder tombstone, or an empty "
                "metadata carrier is permitted "
                f"(op_id={self.op_id!r}, target={self.target!s})."
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

@dataclass
class ProvisionTimeline:
    """Complete version history of a single addressable provision."""

    address: LegalAddress
    versions: List[ProvisionVersion] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
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
