"""Core IR carriers for legal replay and JSON-safe projections."""

from __future__ import annotations
from typing_extensions import override

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Tuple

from typing import TYPE_CHECKING

from lawvm.core.frozen_values import FrozenDict, _freeze_value, _jsonable_value
from lawvm.core.provenance import OperationSource
from lawvm.core.scope_confidence import ScopeConfidence, coerce_scope_confidence
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum

if TYPE_CHECKING:
    # ``execution_authorization`` transitively imports ``core.ir`` (via
    # ``phase_result`` → ``effect_lifecycle``), so importing it at module scope
    # is a circular import. The ``ExecutionAuthorization`` rider on
    # ``LegalOperation`` is therefore a TYPE_CHECKING-only annotation; the
    # runtime typed-carrier validation in ``__post_init__`` lazy-imports the
    # class (the ``provenance.MigrationEvent`` precedent for the same cycle).
    from lawvm.core.execution_authorization import ExecutionAuthorization


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
    """Typed selector for text-level operations.

    ``occurrence_mode`` disambiguates the *kind* of occurrence the op targets,
    resolving the silent conflation between EACH_PLACE (replace every match,
    str.replace(count=-1) semantics) and LAST/terminal (replace rightmost match
    ONCE — the terminal-punct edits ``RULE_INSERT_END_PUNCT`` /
    ``RULE_STRIKE_INSERT_END_PUNCT`` that name a single "the period at the end"
    anchor). Carried distinct from ``occurrence`` to stay backward-compatible
    with frontends whose ops still express each-place via ``occurrence=-1``
    alone: when ``occurrence_mode`` is the default ``"Auto"``, the materializer
    maps ``occurrence=-1`` to ALL (preserving existing each-place behavior);
    when it is ``"Last"``, the materializer replaces the rightmost match once.
    """

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
    # When provided, overrides the legacy ``occurrence=-1`` ALL interpretation
    # in the materializer. ``"Last"`` forces treating the op as terminal-anchor:
    # replace the W-rightmost occurrence once (the period at the end of the
    # target node, not every period in the section).
    occurrence_mode: Literal["Auto", "Last"] = "Auto"

    def __post_init__(self) -> None:
        if not self.match_text:
            raise ValueError("TextSelector.match_text must be non-empty")
        if self.occurrence < -1:
            raise ValueError("TextSelector.occurrence must be >= -1")
        if self.end_occurrence < 0:
            raise ValueError("TextSelector.end_occurrence must be >= 0")
        if self.occurrence_mode not in ("Auto", "Last", "First"):
            raise ValueError(
                f"TextSelector.occurrence_mode must be 'Auto', 'Last', or 'First', got {self.occurrence_mode!r}"
            )


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
    # interpret jurisdiction-local values; ``scope_confidence`` is typed as
    # ``Optional[ScopeConfidence]`` (a marker protocol, see
    # ``lawvm.core.scope_confidence``) so bare strings fail loud at the
    # ``__post_init__`` boundary (AGENTS.md §1.9 typed carriers over dynamic
    # shape, §1.10 fail loud; §2.3 core does not interpret frontend-local
    # values). Frontends inherit the protocol explicitly so an AST scan can
    # keep producer-set == protocol-implementer-set.
    scope_confidence: Optional[ScopeConfidence] = None
    move_clause_target_unit_kind: Optional[str] = None
    # Per-op verbatim source substring (Option C / lightest source-anchor
    # seam): the literal source-clause text that produced THIS op, set where
    # the parser mints the LegalOperation. Distinct from the
    # amendment-level ``OperationSource.raw_text`` (the whole johtolause)
    # and from the byte-span ``OperationSource.source_anchor``: this field
    # is the per-op ``clause_text`` that ``compute_source_anchor`` looks up
    # verbatim in the raw amendment bytes to produce a per-op ``SourceAnchor``
    # (task #50). Empty by default — populated per-op by the parser's minting
    # sites (e.g. ``finland.johtolause.extract_legal_ops_from_parse_result``
    # / ``extract_law_level_text_patch_los``); downstream threading into
    # ``OperationSource.source_anchor`` is owned by the frontend compile
    # loop. Carries verbatim source text and is **not** replay authority —
    # it is evidence footing (Surface/Source plane, §2.10) for the receipt's
    # per-op anchor witness; replay consumes only the typed
    # ``OperationSource.source_anchor`` (§1.11, §1.12).
    raw_text: str = ""
    # EV-05/FW-01/OV-01 execution-authorization PROOF CARRIER (the firewall
    # waist). Optional, ``None`` default, so EVERY existing construction across
    # all frontends stays valid and byte-identical (no producer sets it today).
    # When a frontend MINTS a proof, the universal apply seam's
    # ``read_op_execution_authorization`` resolver reads it
    # (``core/apply_seam``): a landed op carrying a proof with a non-empty
    # ``authorization_rule_id`` goes QUIET on the EV-05 observe gate; one with no
    # proof emits the non-blocking ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED``
    # firewall-hole witness. This is the missing carrier the audit registry names
    # ("apply has ZERO references to ExecutionAuthorization") and that
    # ``notes/CROSS_JURISDICTION_PARITY.md`` flags EV-05 needs ("a proof carrier
    # on core/ir.LegalOperation — a framework change"). It is read-as-witness:
    # the firewall two-flag promotion lives on ``ExecutionAuthorization`` itself
    # (``executable``/``replay_authorized`` + ``forbidden_shortcuts``); core
    # never branches on this rider's contents beyond resolving its presence +
    # ``authorization_rule_id`` at the apply seam (§2.10 evidence-is-not-authority).
    execution_authorization: Optional["ExecutionAuthorization"] = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, StructuralAction):
            raise TypeError(
                f"LegalOperation.action must be StructuralAction, got {type(self.action).__name__}"
            )
        object.__setattr__(self, "applicability", tuple(self.applicability))
        object.__setattr__(self, "provenance_tags", tuple(self.provenance_tags))
        if not isinstance(self.raw_text, str):
            raise TypeError(
                f"LegalOperation.raw_text must be a str, got {type(self.raw_text).__name__}"
            )
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
        # Fail loud at the core semantic waist: a bare ``str`` here means a
        # frontend bypassed its typed ``ScopeConfidence`` dataclass and is
        # smuggling a free-form rung string (AGENTS.md §1.9, §1.10). ``None``
        # is the legitimate "no witness" sentinel and passes through unchanged.
        object.__setattr__(
            self,
            "scope_confidence",
            coerce_scope_confidence(self.scope_confidence),
        )
        # Typed-carrier validation for the EV-05 proof rider (§1.9 typed carriers
        # over dynamic shape; §1.10 fail loud). A non-``None`` value MUST be a real
        # ``ExecutionAuthorization`` instance — a bare dict / status string here
        # would smuggle an unvalidated "proof" past the firewall waist. ``None``
        # (the no-proof sentinel — the honest ~100% firewall-hole default) passes
        # through unchanged. The class is lazy-imported to break the module cycle
        # (see the TYPE_CHECKING import above), mirroring ``provenance``.
        if self.execution_authorization is not None:
            from lawvm.core.execution_authorization import ExecutionAuthorization

            if not isinstance(self.execution_authorization, ExecutionAuthorization):
                raise TypeError(
                    "LegalOperation.execution_authorization must be an "
                    "ExecutionAuthorization instance (the EV-05 typed proof "
                    f"carrier), got {type(self.execution_authorization).__name__}; "
                    "mint a real ExecutionAuthorization (or leave None for the "
                    "no-proof firewall-hole witness)"
                )

@dataclass(frozen=True, slots=True)
class ProvisionVersion:
    """A single version of a provision in the temporal graph."""

    effective: str
    enacted: str = ""
    expires: str = ""
    variant_kind: Literal["permanent", "temporary"] = "permanent"
    content: Optional["IRNode"] = None
    source: Optional[OperationSource] = None
    # ``Sequence[ScopePredicate]`` (the read-only covariant protocol) rather
    # than ``List[...]`` — matches the ``ProvisionTimeline.versions`` precedent
    # (iter2 H5): declared read-only at the type level, runtime-coerced to a
    # ``tuple`` via ``__post_init__`` so callers may pass either a list literal
    # or a tuple while stored mutation is impossible (§1.9 immutable carriers).
    applicability: Sequence[ScopePredicate] = field(default_factory=list)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.effective:
            raise ValueError("ProvisionVersion requires non-empty effective date")
        if self.variant_kind not in {"permanent", "temporary"}:
            raise ValueError("ProvisionVersion.variant_kind must be one of 'permanent' or 'temporary'")
        if self.expires and self.effective > self.expires:
            raise ValueError(f"ProvisionVersion expires ({self.expires}) before effective ({self.effective})")
        object.__setattr__(self, "applicability", tuple(self.applicability))

@dataclass(frozen=True, slots=True)
class ProvisionTimeline:
    """Complete version history of a single addressable provision.

    ``versions`` is annotated as ``Sequence[ProvisionVersion]`` (the
    read-only covariant protocol) and stored at runtime as a ``tuple``
    via ``__post_init__`` — this satisfies §1.9's immutable-carrier rule
    at both the type level (no ``append``/``sort``/``pop`` on the
    declared protocol) and runtime (tuple raises ``AttributeError`` on
    any mutation attempt). Every append/sort/replace goes through
    ``dataclasses.replace`` so historical state cannot silently mutate
    across a phase seam. Callers may ergonomically pass either a list
    or tuple literal at construction.
    """

    address: LegalAddress
    versions: Sequence[ProvisionVersion] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Coerce any iterable passed at construction (list/tuple) to a tuple
        # so callers may write ``versions=[v1, v2]`` ergonomically while the
        # stored value remains immutable and the declared ``Sequence``
        # contract is satisfied.
        object.__setattr__(self, "versions", tuple(self.versions))


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
