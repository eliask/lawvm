"""Typed semantic bundle, verdict, and strict-profile support for frontend compilers.

This module makes the current implicit contract explicit:

- frontends compile into canonical operations
- some operations or outcomes depend on heuristic recovery
- some operations fail deterministically
- strictness is evaluated from the compilation path, not only the outcome

API tier
--------
Semantic center only. Bundle, temporal carriers, strictness derivation, and
verdicts live here. Reporting/storage projections over the finding ledger live
in ``lawvm.core.compile_views``. The old ``CompileResult`` envelope has been
removed; top-level dossier consumers should use ``lawvm.core.compile_facade``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Mapping, Optional, cast

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.event_summaries import (
    count_events_with_activation_rules,
    count_events_with_source,
    distinct_activation_rule_kinds,
    distinct_event_kinds,
)
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE, VIOLATION_ROLE
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.target_scope import (
    matching_sections_for_scope,
    normalize_target_unit_kind,
    resolve_internal_target_scope,
    TargetUnitKind,
)
from lawvm.core.provenance import MigrationEvent, migration_event_sort_key
from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline_results import MaterializationLineagePlan

# Compatibility re-exports while temporal carriers migrate to core.temporal.
_TEMPORAL_COMPAT_EXPORTS = (ActivationRule, TemporalEvent, TemporalScope)

if TYPE_CHECKING:
    from lawvm.core.ir import IRStatute, ProvisionTimeline, ProvisionVersion
    from lawvm.core.timeline_results import MaterializationResult


StrictMode = Literal["strict", "quirks"]

def _canonical_migration_events(events: Iterable["MigrationEvent"]) -> tuple["MigrationEvent", ...]:
    """Return migration events in deterministic canonical order."""
    return tuple(sorted(tuple(events), key=migration_event_sort_key))

@dataclass(frozen=True)
class StrictProfile:
    """A jurisdiction- or pipeline-specific strictness contract."""

    name: str
    requires_explicit_effective_date: bool = False
    allows_target_guessing: bool = False
    allows_omission_expansion: bool = False
    allows_uncovered_body_recovery: bool = False
    allows_fallback_whole_section_replace: bool = False
    allows_estimated_dates: bool = True
    allows_context_dependent_anchor_resolution: bool = False
    allows_word_substitution: bool = False
    # Whether source-corrective patches for malformed amendment/oracle artifacts
    # are allowed.
    allows_source_correction_rules: bool = False

    def __post_init__(self):
        if not self.name:
            raise ValueError("StrictProfile.name must be non-empty")


@dataclass(frozen=True)
class SourceCompletenessInfo:
    """Factual triplet: how complete is the amendment chain?

    Expressed as counts, not verdicts. Downstream consumers apply thresholds.
    """

    chain_length: int  # total amendments in parent chain
    source_available: int  # amendments with fetchable XML
    dates_available: int  # amendments with explicit effective date


@dataclass(frozen=True)
class SourcePathology:
    """Typed replay-time source-pathology finding."""

    code: str
    message: str
    source_statute: str = ""
    target_unit_kind: TargetUnitKind | Literal[""] = ""
    target_label: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target_unit_kind:
            if any(
                key in self.detail
                for key in ("target_section", "target_chapter", "target_part", "target_paragraph", "target_item")
            ):
                raise ValueError(
                    "SourcePathology with structural detail requires explicit neutral target_unit_kind"
                )
            return
        normalized = normalize_target_unit_kind(self.target_unit_kind)
        if str(normalized) != str(self.target_unit_kind):
            raise ValueError(
                f"SourcePathology.target_unit_kind must be explicit neutral scope, got {self.target_unit_kind!r}"
            )

    def scope_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "target_unit_kind": str(self.target_unit_kind),
        }
        if self.target_label:
            detail["target_label"] = self.target_label
        return detail

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            **self.scope_detail(),
            **dict(self.detail),
        }

    @classmethod
    def from_internal_detail(
        cls,
        *,
        source_statute: str,
        detail: dict[str, Any],
    ) -> "SourcePathology":
        code = str(detail.get("code") or "")
        message = str(detail.get("message") or "")
        target_unit_kind = str(detail.get("target_unit_kind") or "")
        target_label = str(detail.get("target_label") or "")
        detail_payload = {
            k: v
            for k, v in detail.items()
            if k not in ("code", "message", "target_unit_kind", "target_label")
        }
        if target_unit_kind:
            return cls.from_scope(
                code=code,
                message=message,
                source_statute=source_statute,
                target_unit_kind=target_unit_kind,
                target_label=target_label,
                detail=detail_payload,
            )
        if code == "EMPTY_OPERATIVE_BODY":
            return cls(
                code=code,
                message=message,
                source_statute=source_statute,
                target_label=target_label,
                detail=detail_payload,
            )
        raise ValueError(
            "SourcePathology.from_internal_detail requires explicit neutral target_unit_kind "
            "for structural pathologies"
        )

    @classmethod
    def from_scope(
        cls,
        *,
        code: str,
        message: str,
        source_statute: str = "",
        target_unit_kind: str,
        target_label: str = "",
        detail: Optional[Mapping[str, Any]] = None,
    ) -> "SourcePathology":
        """Build a source pathology from neutral structural scope."""
        normalized_target_unit_kind = normalize_target_unit_kind(target_unit_kind) if target_unit_kind else ""
        normalized_target_unit_kind_text = str(normalized_target_unit_kind) if normalized_target_unit_kind else ""
        if normalized_target_unit_kind_text and normalized_target_unit_kind_text not in {"section", "chapter", "part"}:
            raise ValueError(
                "SourcePathology.from_scope only accepts neutral structural scope kinds "
                "section/chapter/part"
            )
        return cls(
            code=code,
            message=message,
            source_statute=source_statute,
            target_unit_kind=cast(TargetUnitKind | Literal[""], normalized_target_unit_kind_text),
            target_label=target_label,
            detail=dict(detail or {}),
        )


@dataclass(frozen=True)
class CompiledOpProvenanceTags:
    """Immutable provenance tag bundle extracted from compiled-op rows."""

    extraction_tags: frozenset[str] = field(default_factory=frozenset)
    target_guessing_tags: frozenset[str] = field(default_factory=frozenset)
    scope_tags: frozenset[str] = field(default_factory=frozenset)
    scope_sources: frozenset[str] = field(default_factory=frozenset)
    scope_confidences: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CompiledOpScopeWitness:
    """Normalized scope witness derived from a compiled-op transport row."""

    kind: str
    source: str
    confidence: str
    tag: str = ""
    used_legacy_tag_fallback: bool = False


@dataclass(frozen=True)
class AdmissibleBindingCertificate:
    """Certificate that a subsection slot binding was deterministic."""

    slot_id: int
    amendment_id: str
    candidate_count: int  # 1 = single admissible, >1 = ambiguous
    admissibility: Literal["single", "ambiguous", "fallback"]


@dataclass(frozen=True)
class CompileFailure:
    """Frontend-agnostic failure record."""

    source_statute: str
    description: str
    reason: str
    target_section: str
    target_unit_kind: TargetUnitKind
    reason_code: str = ""
    target_chapter: str = ""

    def __post_init__(self) -> None:
        if self.target_unit_kind not in {"section", "chapter", "part"}:
            raise ValueError(f"CompileFailure.target_unit_kind must be explicit neutral scope, got {self.target_unit_kind!r}")

    def scope_detail(self) -> dict[str, Any]:
        return {
            "target_unit_kind": self.target_unit_kind,
            "target_section": self.target_section,
            "target_chapter": self.target_chapter,
        }

    def as_detail(self) -> dict[str, Any]:
        return {
            "source_statute": self.source_statute,
            "description": self.description,
            "reason": self.reason,
            "reason_code": self.reason_code,
            **self.scope_detail(),
        }

    @classmethod
    def from_scope(
        cls,
        *,
        source_statute: str,
        description: str,
        reason: str,
        target_section: str,
        target_unit_kind: TargetUnitKind,
        reason_code: str = "",
        target_chapter: str = "",
    ) -> "CompileFailure":
        """Build a compile failure from neutral structural scope."""
        return cls(
            source_statute=source_statute,
            description=description,
            reason=reason,
            target_section=target_section,
            target_chapter=target_chapter,
            target_unit_kind=target_unit_kind,
            reason_code=reason_code,
        )


# ---------------------------------------------------------------------------
# StrictBarrier taxonomy
# ---------------------------------------------------------------------------
# A typed inventory of every reason a compilation might fail strict mode.
# Organized by family (Pro recommendation: "vector of barrier kinds, not a
# scalar"). Each barrier is a compiler diagnostic, not a score.

BarrierFamily = Literal[
    "recovery",  # heuristic recovery was needed
    "source",  # source data incomplete or pathological
    "extraction",  # extraction fallback or heuristic parse
    "resolution",  # target/anchor resolution required context
    "temporal",  # date/lifecycle ambiguity
    "invariant",  # structural invariant violated
    "text_level",  # word-level substitution (strict may forbid)
]


_SOURCE_INCOMPLETE_CODES = {
    "APPLY.SOURCE_INCOMPLETE",
    "APPLY.SOURCE_PATHOLOGY_DETECTED",
    "APPLY.SOURCE_CORRECTED_BY_PATCH",
}


# ---------------------------------------------------------------------------
# Registry-driven barrier family
# ---------------------------------------------------------------------------
# FindingFamily (observation_registry) uses a semantic taxonomy; BarrierFamily
# (compile_result) uses a coarser operational taxonomy. This mapping is the
# surviving core projection for turning governed finding codes into barrier
# families.
#
# Cascade: (1) code-specific exceptions inside barrier_family_from_registry(),
# (2) FindingFamily default from _FINDING_FAMILY_TO_BARRIER_FAMILY,
# (3) fallback "recovery". The inline exceptions exist because FindingFamily
# "recovery" maps to multiple BarrierFamily values depending on pipeline
# phase (parse recoveries → "extraction", anchor resolution →
# "resolution", word substitution → "text_level").

_FINDING_FAMILY_TO_BARRIER_FAMILY: dict[str, BarrierFamily] = {
    VIOLATION_ROLE: "invariant",
    "ambiguity": "temporal",  # ambiguity codes are date/lifecycle
    "recovery": "recovery",  # default; parse recoveries override to "extraction"
    "source_pathology": "source",
    "external_drift": "source",  # no current codes, conservative default
    "projection_drift": "source",  # no current codes, conservative default
    "audit": "recovery",  # audit signals are non-blocking, fallback
}


def barrier_family_from_registry(code: str) -> BarrierFamily:
    """Derive BarrierFamily for a finding code using the registry.

    Priority: (1) registry-projected narrow families if needed,
    (2) FindingSpec.family mapped through _FINDING_FAMILY_TO_BARRIER_FAMILY,
    (3) default "recovery".
    """
    spec = get_finding_spec(code)
    if spec is not None:
        if spec.family == "source_pathology":
            return "source"
        if spec.family == "ambiguity":
            return "temporal"
        if spec.family == "violation":
            return "invariant"
        if spec.family == "recovery":
            if code.startswith("PARSE.EXTRACTION_") or code == "PARSE.TARGET_GUESSING":
                return "extraction"
            if code == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION":
                return "resolution"
            if code == "APPLY.WORD_SUBSTITUTION":
                return "text_level"
            return "recovery"
        return _FINDING_FAMILY_TO_BARRIER_FAMILY.get(spec.family, "recovery")
    return "recovery"

# ---------------------------------------------------------------------------
# Canonical Effect Bundle — the semantic center
# ---------------------------------------------------------------------------
# Per Pro review: the canonical bundle is the semantic center, not
# top-level dossier wrappers and not timelines. Three effect rails:
# structural, text, lifecycle.

EffectFamily = Literal["structural", "text", "lifecycle"]

TextAction = Literal["text_patch"]
LifecycleAction = Literal["commence", "expire", "suspend", "revive", "applicability"]


@dataclass(frozen=True)
class CanonicalEffect:
    """One typed effect in the canonical bundle.

    Each effect belongs to exactly one rail (structural, text, lifecycle)
    and carries stable IDs, target address, and provenance witness. The
    ``action`` field is rail-specific: structural actions use
    ``StructuralAction`` values, text actions use ``TextAction`` values, and
    lifecycle actions use ``LifecycleAction`` values.
    """

    effect_id: str
    family: EffectFamily
    action: StructuralAction | TextAction | LifecycleAction
    target: "LegalAddress"
    group_id: str = ""  # groups related effects from one clause
    payload: Optional[Any] = None  # IRNode for structural, patch spec for text
    witness_ref: str = ""  # construction rule or source span
    source: Optional["OperationSource"] = None

    def __post_init__(self) -> None:
        if self.family == "structural":
            if not isinstance(self.action, StructuralAction):
                raise TypeError("CanonicalEffect family='structural' requires StructuralAction action")
            return
        if self.family == "text":
            if self.action != "text_patch":
                raise TypeError("CanonicalEffect family='text' requires action='text_patch'")
            return
        if self.family == "lifecycle" and self.action not in {
            "commence",
            "expire",
            "suspend",
            "revive",
            "applicability",
        }:
            raise TypeError("CanonicalEffect family='lifecycle' requires lifecycle action")


@dataclass(frozen=True)
class EffectGroup:
    """A group of related effects from one amendment clause."""

    group_id: str
    source_statute: str = ""
    clause_ref: str = ""  # source clause location in amendment


def _validate_bundle_purity(
    structural_ops: tuple[object, ...],
    *,
    caller: str = "CanonicalBundle",
) -> list[str]:
    """Check that every structural_op is a shared-kernel LegalOperation.

    Returns a list of violation descriptions (empty means pure). Does NOT
    raise — callers decide whether to warn or hard-fail.

    This guard exists because CanonicalBundle is the cross-jurisdiction
    semantic center and must not carry frontend-local waist types as
    first-class bundle payload.
    """
    from lawvm.core.ir import LegalOperation  # noqa: PLC0415 (avoid circular at module level)

    violations: list[str] = []
    for i, op in enumerate(structural_ops):
        if not isinstance(op, LegalOperation):
            violations.append(
                f"{caller}.structural_ops[{i}] is {type(op).__qualname__!r}, "
                "expected LegalOperation; frontend-local types must be lowered "
                "before entering the shared canonical bundle"
            )
    return violations


@dataclass(frozen=True)
class CanonicalBundle:
    """The semantic output of compilation.

    Contains typed, witnessed, grouped effects. Timelines and PIT
    materializations are derived views of this bundle. Structural inputs live
    in `structural_ops`; temporal execution lives in `temporal_events`.

    Construction raises ``TypeError`` if any ``structural_ops`` element is not
    a shared-kernel ``LegalOperation``.  Frontend-local types must be lowered
    before reaching this boundary.
    """

    source_statute: str = ""  # the amendment act
    target_statute: str = ""  # the statute being amended
    structural_ops: tuple["LegalOperation", ...] = ()
    temporal_events: tuple[TemporalEvent, ...] = ()
    migration_events: tuple["MigrationEvent", ...] = ()
    effects: tuple[CanonicalEffect, ...] = ()
    groups: tuple[EffectGroup, ...] = ()
    source: Optional["OperationSource"] = None

    def __post_init__(self) -> None:
        violations = _validate_bundle_purity(self.structural_ops)
        if violations:
            raise TypeError(
                "CanonicalBundle received non-LegalOperation items in structural_ops; "
                "frontend-local types must be lowered before the shared canonical "
                "boundary. Violations:\n" + "\n".join(f"  - {v}" for v in violations)
            )
        canonical_migration_events = _canonical_migration_events(self.migration_events)
        if canonical_migration_events != self.migration_events:
            object.__setattr__(self, "migration_events", canonical_migration_events)

    def validate_purity(self) -> list[str]:
        """Return a list of purity violations (empty means pure).

        Each entry describes one item in ``structural_ops`` that is not a
        shared-kernel ``LegalOperation``.  Callers may call this at any
        point to audit the bundle's type integrity.
        """
        return _validate_bundle_purity(self.structural_ops, caller="CanonicalBundle")

    @property
    def migration_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct migration-event kinds carried by this bundle."""
        return distinct_event_kinds(self.migration_events)

    @property
    def temporal_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct temporal-event kinds carried by this bundle."""
        return distinct_event_kinds(self.temporal_events)

    @property
    def temporal_events_with_activation_rules(self) -> int:
        """Return the number of temporal events carrying an embedded activation rule."""
        return count_events_with_activation_rules(self.temporal_events)

    @property
    def temporal_events_with_source(self) -> int:
        """Return the number of temporal events carrying provenance source data."""
        return count_events_with_source(self.temporal_events)

    @property
    def temporal_event_activation_rule_kinds(self) -> tuple[str, ...]:
        """Return the distinct activation-rule kinds carried by this bundle."""
        return distinct_activation_rule_kinds(self.temporal_events)

    def provision_lineage(
        self,
        timelines: "dict[LegalAddress, ProvisionTimeline]",
        address: "LegalAddress",
        *,
        as_of_date: str = "",
    ) -> list["ProvisionVersion"]:
        """Return lineage using the bundle's emitted migration chain.

        Core consumes the emitted chain; producer frontends remain the
        emission site.
        """
        from lawvm.core.timeline import provision_lineage  # noqa: PLC0415

        return provision_lineage(
            timelines,
            address,
            migration_events=self.migration_events,
            as_of_date=as_of_date,
        )

    def materialize_pit(
        self,
        timelines: "dict[LegalAddress, ProvisionTimeline]",
        as_of: str,
        *,
        base: "IRStatute | None" = None,
        territory: str | None = None,
        query_type: Literal["governing", "in_force"] = "governing",
        label_norm: Optional[Callable[[str], str]] = None,
        expires_as_of: str = "",
    ) -> "IRStatute":
        """Materialize PIT using the bundle's emitted lineage migrations."""
        from lawvm.core.timeline import materialize_pit  # noqa: PLC0415

        return materialize_pit(
            timelines,
            as_of,
            base=base,
            territory=territory,
            query_type=query_type,
            label_norm=label_norm,
            expires_as_of=expires_as_of,
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=self.migration_events,
            ),
        )

    def materialize_pit_ex(
        self,
        timelines: "dict[LegalAddress, ProvisionTimeline]",
        as_of: str,
        *,
        base: "IRStatute | None" = None,
        territory: str | None = None,
        query_type: Literal["governing", "in_force"] = "governing",
        label_norm: Optional[Callable[[str], str]] = None,
        expires_as_of: str = "",
    ) -> "MaterializationResult":
        """Materialize PIT with explicit degradation metadata and lineage migrations."""
        from lawvm.core.timeline import materialize_pit_ex  # noqa: PLC0415

        return materialize_pit_ex(
            timelines,
            as_of,
            base=base,
            territory=territory,
            query_type=query_type,
            label_norm=label_norm,
            expires_as_of=expires_as_of,
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=self.migration_events,
            ),
        )


# ---------------------------------------------------------------------------
# Compile Verdict — one compiler, two verdicts
# ---------------------------------------------------------------------------

CompileStatus = Literal[
    "strict_clean",
    "strict_blocked_by_recovery",
    "source_incomplete",
    "internal_failure",
]


@dataclass(frozen=True)
class CompileVerdict:
    """Strict-mode verdict computed from the compile audit.

    Every compile produces a verdict regardless of mode. Quirks mode
    always succeeds for materialization; the verdict records whether
    strict criteria were met. ``barrier_codes`` are the strict-barrier
    truth rail; runtime finding rows do not carry barrier kinds.
    """

    mode: StrictMode
    profile: str
    status: CompileStatus
    barrier_codes: tuple[str, ...] = ()

    @property
    def is_strict_clean(self) -> bool:
        return self.status == "strict_clean"

    @property
    def barrier_families(self) -> tuple[BarrierFamily, ...]:
        return tuple(dict.fromkeys(barrier_family_from_registry(code) for code in self.barrier_codes))

    @property
    def barrier_messages(self) -> tuple[str, ...]:
        messages: list[str] = []
        for code in self.barrier_codes:
            spec = get_finding_spec(code)
            messages.append(spec.description if spec is not None else code.replace("_", " "))
        return tuple(messages)


def _compiled_op_provenance_tag_sets(
    compiled_ops: Iterable[dict[str, Any]],
) -> CompiledOpProvenanceTags:
    """Collect normalized typed provenance tags from compiled-op rows.

    This is the shared evidence-plane seam for row-level provenance emitted by
    frontends. Callers should consume the typed carriers here rather than
    rebuild ad hoc scans over compiled-op dicts.
    """

    compiled_extraction_tags: set[str] = set()
    compiled_target_guessing_tags: set[str] = set()
    compiled_scope_tags: set[str] = set()
    compiled_scope_sources: set[str] = set()
    compiled_scope_confidences: set[str] = set()

    for row in compiled_ops:
        extraction_tags = row.get("extraction_provenance_tags")
        if isinstance(extraction_tags, list):
            compiled_extraction_tags.update(str(part).strip() for part in extraction_tags if str(part).strip())
        target_guessing_tags = row.get("target_guessing_provenance_tags")
        if isinstance(target_guessing_tags, list):
            compiled_target_guessing_tags.update(
                str(part).strip() for part in target_guessing_tags if str(part).strip()
            )
        scope_tags = row.get("scope_provenance_tags")
        if isinstance(scope_tags, list):
            compiled_scope_tags.update(str(part).strip() for part in scope_tags if str(part).strip())
        scope_source = row.get("scope_source")
        if isinstance(scope_source, str) and scope_source.strip():
            compiled_scope_sources.add(scope_source.strip())
        scope_confidence = row.get("scope_confidence")
        if isinstance(scope_confidence, str) and scope_confidence.strip():
            compiled_scope_confidences.add(scope_confidence.strip())

    return CompiledOpProvenanceTags(
        extraction_tags=frozenset(compiled_extraction_tags),
        target_guessing_tags=frozenset(compiled_target_guessing_tags),
        scope_tags=frozenset(compiled_scope_tags),
        scope_sources=frozenset(compiled_scope_sources),
        scope_confidences=frozenset(compiled_scope_confidences),
    )


def _compiled_op_source_statute(op: dict[str, Any]) -> str:
    """Return the amendment id associated with a compiled-op row, if any."""

    for key in ("source_statute", "amendment_id", "source"):
        value = op.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = op.get("source")
    if isinstance(source, OperationSource):
        return str(source.statute_id or "").strip()
    return ""


def _compiled_op_scope_witness(row: Mapping[str, Any]) -> CompiledOpScopeWitness | None:
    """Return the normalized scope witness carried by a compiled-op row.

    Structured `scope_source` / `scope_confidence` is authoritative. Raw
    `scope_provenance_tags` are retained only as explicit compatibility for
    legacy rows that predate the structured carrier.
    """

    scope_source = row.get("scope_source")
    scope_confidence = row.get("scope_confidence")
    scope_tags = row.get("scope_provenance_tags")
    scope_tag_list = (
        [str(part).strip() for part in scope_tags if str(part).strip()]
        if isinstance(scope_tags, list)
        else []
    )

    source_value = str(scope_source).strip() if isinstance(scope_source, str) else ""
    confidence_value = str(scope_confidence).strip() if isinstance(scope_confidence, str) else ""
    if source_value and confidence_value:
        if source_value in {"carry_forward", "johtolause", "grouped_part", "grouped_chapter"}:
            scope_kind = "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"
        elif source_value == "explicit_chunk":
            scope_kind = "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED"
        elif source_value == "explicit_scope_rewrite":
            scope_kind = "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED"
        else:
            return None
        return CompiledOpScopeWitness(
            kind=scope_kind,
            source=source_value,
            confidence=confidence_value,
            tag=next(iter(scope_tag_list), ""),
            used_legacy_tag_fallback=False,
        )

    if "chapter_scope_from_explicit_chunk" in scope_tag_list:
        return CompiledOpScopeWitness(
            kind="LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED",
            source="explicit_chunk",
            confidence="explicit",
            tag="chapter_scope_from_explicit_chunk",
            used_legacy_tag_fallback=True,
        )

    for tag in (
        "chapter_scope_stripped_subsection_insert",
        "chapter_scope_stripped_section_facet_insert",
        "chapter_scope_stripped_unique_section",
        "chapter_scope_stripped_duplicate_label_outside_stated_chapter",
    ):
        if tag in scope_tag_list:
            return CompiledOpScopeWitness(
                kind="LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
                source="explicit_scope_rewrite",
                confidence="rewritten",
                tag=tag,
                used_legacy_tag_fallback=True,
            )

    for tag, source_value in (
        ("chapter_scope_carry_forward", "carry_forward"),
        ("chapter_scope_from_johtolause", "johtolause"),
        ("grouped_part_scope", "grouped_part"),
        ("grouped_chapter_scope", "grouped_chapter"),
    ):
        if tag in scope_tag_list:
            return CompiledOpScopeWitness(
                kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                source=source_value,
                confidence="inferred",
                tag=tag,
                used_legacy_tag_fallback=True,
            )

    return None


def _operation_section_labels(op: LegalOperation) -> set[str]:
    """Return section labels referenced by an operation's addresses."""
    labels: set[str] = set()
    addresses = [op.target]
    if op.destination is not None:
        addresses.append(op.destination)
    for address in addresses:
        for kind, label in address.path:
            if normalize_target_unit_kind(kind) == "section" and label:
                labels.add(label)
    return labels


def _operation_scope_from_address(address: LegalAddress) -> dict[str, str]:
    """Build a neutral target-scope mapping from a concrete LegalAddress."""
    target_unit_kind = normalize_target_unit_kind(address.leaf_kind())
    if not target_unit_kind:
        return {}
    target_label = address.leaf_label()
    if not target_label:
        return {}

    scope: dict[str, str] = {
        "target_unit_kind": str(target_unit_kind),
        "target_norm": target_label,
    }
    for kind, label in address.path:
        normalized_kind = normalize_target_unit_kind(kind)
        if normalized_kind == "section" and label:
            scope["target_section"] = label
        elif normalized_kind == "chapter" and label:
            scope["target_chapter"] = label
        elif normalized_kind == "part" and label:
            scope["target_part"] = label
    if target_unit_kind == "chapter":
        scope.setdefault("target_chapter", target_label)
    elif target_unit_kind == "part":
        scope.setdefault("target_part", target_label)
    elif target_unit_kind == "section":
        scope.setdefault("target_section", target_label)
    return scope


def _compiled_op_matches_section(op: dict[str, Any], section_label: str) -> bool:
    if section_label in matching_sections_for_scope(
        scope=resolve_internal_target_scope(op),
        section_labels=[section_label],
    ):
        return True
    return False


def _operation_matches_section(op: LegalOperation, section_label: str) -> bool:
    section_labels = _operation_section_labels(op)
    if section_label in section_labels:
        return True
    for address in (op.target, op.destination):
        if address is None:
            continue
        if section_label in matching_sections_for_scope(
            scope=resolve_internal_target_scope(_operation_scope_from_address(address)),
            section_labels=[section_label],
        ):
            return True
    return False


def _finding_matches_section(
    finding: Finding,
    section_label: str,
    section_op_ids: set[str],
) -> bool:
    """Return True when a finding can be safely attributed to a section."""
    detail = finding.detail if isinstance(finding.detail, dict) else {}
    op_id = str(detail.get("op_id") or "")
    if op_id and op_id in section_op_ids:
        return True
    target_unit_kind = str(detail.get("target_unit_kind") or "").strip()
    if target_unit_kind in {"chapter", "part", "appendix", "document"}:
        return True
    for key in ("target_section", "section_label", "target_label"):
        if str(detail.get(key) or "") == section_label:
            return True
    scope = resolve_internal_target_scope(detail)
    if section_label in matching_sections_for_scope(scope=scope, section_labels=[section_label]):
        return True
    return False


# ---------------------------------------------------------------------------
# Registry-driven strict fail reasons (Phase 8)
# ---------------------------------------------------------------------------
# Profile-gate map: which governed strict finding code a profile can suppress.
# A code is only emitted if the profile does NOT allow the recovery it
# represents.  Codes not in this map are always emitted when triggered.
#
# True  = the profile field that PERMITS the recovery (not emitted when True)
# False = the profile field that REQUIRES the condition (emitted when True)

_PROFILE_GATES: dict[str, tuple[str, bool]] = {
    # (profile_attr, gate_is_allows)
    # gate_is_allows=True: code suppressed when profile.attr is True
    # gate_is_allows=False: code emitted only when profile.attr is True
    "PARSE.TARGET_GUESSING": ("allows_target_guessing", True),
    "ELAB.OMISSION_EXPANSION": ("allows_omission_expansion", True),
    "APPLY.UNCOVERED_BODY_RECOVERY": ("allows_uncovered_body_recovery", True),
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": ("allows_fallback_whole_section_replace", True),
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED": ("allows_uncovered_body_recovery", True),
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": ("allows_context_dependent_anchor_resolution", True),
    "ELAB.SEC1_PRE_ROUTING_FALLBACK": ("allows_context_dependent_anchor_resolution", True),
    "APPLY.WORD_SUBSTITUTION": ("allows_word_substitution", True),
    "APPLY.SOURCE_CORRECTED_BY_PATCH": ("allows_source_correction_rules", True),
    "TIME.MISSING_EFFECTIVE_DATE": ("requires_explicit_effective_date", False),
    "TIME.ESTIMATED_EFFECTIVE_DATE": ("allows_estimated_dates", True),
}


def _profile_allows(profile: StrictProfile, code: str) -> bool:
    """Return True if the profile explicitly allows (suppresses) this code."""
    gate = _PROFILE_GATES.get(code)
    if gate is None:
        return False  # no gate → always strict-fail when triggered
    attr, is_allows = gate
    profile_gates = {
        "allows_target_guessing": profile.allows_target_guessing,
        "allows_omission_expansion": profile.allows_omission_expansion,
        "allows_uncovered_body_recovery": profile.allows_uncovered_body_recovery,
        "allows_fallback_whole_section_replace": profile.allows_fallback_whole_section_replace,
        "allows_context_dependent_anchor_resolution": profile.allows_context_dependent_anchor_resolution,
        "allows_word_substitution": profile.allows_word_substitution,
        "allows_source_correction_rules": profile.allows_source_correction_rules,
        "requires_explicit_effective_date": profile.requires_explicit_effective_date,
        "allows_estimated_dates": profile.allows_estimated_dates,
    }
    val = profile_gates[attr]
    if is_allows:
        return bool(val)  # allows_X=True → suppressed
    else:
        return not bool(val)  # requires_X=False → suppressed


def strict_fail_reasons_from_finding_ledger(
    profile: StrictProfile,
    *,
    compiled_ops: Iterable[dict[str, Any]],
    canonical_ops: Iterable[LegalOperation],
    failures: Iterable[CompileFailure],
    findings: Iterable[Finding],
) -> list[str]:
    """Derive strict-fail reasons using finding-ledger inputs instead of adjudication bags.

    Temporal strictness is driven by explicit findings or stored verdicts, not
    by absence of provenance dates on ``LegalOperation`` carriers.
    """

    triggered: set[str] = set()
    finding_list = list(findings)

    if any(True for _ in failures):
        triggered.add("APPLY.FAILED_OPERATION")

    canonical_ops_list = list(canonical_ops)

    def _as_canonical_action_value(raw_action: Any) -> str:
        if isinstance(raw_action, StructuralAction):
            return raw_action.value
        if isinstance(raw_action, str):
            return raw_action.strip()
        if isinstance(raw_action, bytes):
            return raw_action.decode().strip()
        value = getattr(raw_action, "value", None)
        if value is not None:
            if isinstance(value, str):
                return value.strip()
            return str(value)
        return str(raw_action)

    def _is_word_substitution_action(action_value: str) -> bool:
        normalized_action = action_value.strip().replace("-", "_").lower()
        return normalized_action in {"text_replace", "text_repeal"}

    if any(_is_word_substitution_action(_as_canonical_action_value(op.action)) for op in canonical_ops_list):
        triggered.add("APPLY.WORD_SUBSTITUTION")

    compiled_provenance_tags = _compiled_op_provenance_tag_sets(compiled_ops)

    if compiled_provenance_tags.target_guessing_tags:
        triggered.add("PARSE.TARGET_GUESSING")

    extraction_fallback_tags = {
        "extraction_fallback_heuristic",
        "extraction_title_fallback",
        "extraction_sec1_body_johto",
        "repeal_reenact_normalized",
        "fallback_insert_supplement",
        "fallback_insert_supplement_shadowed",
        "fallback_replace_supplement",
        "fallback_replace_supplement_shadowed",
        "root_insert_supplement",
    }
    if compiled_provenance_tags.extraction_tags & extraction_fallback_tags:
        triggered.add("PARSE.EXTRACTION_FALLBACK")

    for row in compiled_ops:
        scope_witness = _compiled_op_scope_witness(row)
        if scope_witness is not None:
            triggered.add(scope_witness.kind)

    _runtime_finding_to_strict_code = {
        "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY": "APPLY.SOURCE_PATHOLOGY_DETECTED",
    }
    for finding in finding_list:
        finding_code = str(finding.kind or "").strip()
        if not finding_code:
            continue
        strict_code = _runtime_finding_to_strict_code.get(finding_code, finding_code)
        if strict_code == "RUNTIME.VIOLATION":
            barrier_code = str(finding.detail.get("barrier_code") or "").strip()
            if barrier_code:
                barrier_spec = get_finding_spec(barrier_code)
                if barrier_spec is not None and barrier_spec.role in ("barrier", "violation", "obligation"):
                    strict_code = barrier_code
        spec = get_finding_spec(strict_code)
        if spec is None:
            continue
        if finding_code in _runtime_finding_to_strict_code:
            triggered.add(spec.code)
            continue
        if spec.role == "barrier":
            triggered.add(spec.code)
            continue
        if spec.code in {
            "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            "PARSE.UNOWNED_BODY_SECTION",
        }:
            triggered.add(spec.code)
            continue
        if finding.blocking and spec.default_enforcement in ("strict_fail", "hard_fail"):
            triggered.add(spec.code)

    reasons: set[str] = set()
    for code in triggered:
        spec = get_finding_spec(code)
        if spec is None:
            if not _profile_allows(profile, code):
                reasons.add(code)
            continue
        if spec.default_enforcement not in ("strict_fail", "hard_fail"):
            continue
        if _profile_allows(profile, code):
            continue
        reasons.add(spec.code)

    return sorted(reasons)


def strict_fail_reasons_from_findings_and_verdict(
    findings: Iterable[Finding],
    *,
    verdict: CompileVerdict | None = None,
) -> tuple[str, ...]:
    """Project strict-fail reasons from the stored strict verdict and finding ledger.

    This is the read-time strict summary projection for compile dossiers that
    already carry a finding ledger and, optionally, a precomputed verdict.
    When a verdict is present, its barrier rail is authoritative; findings
    contribute only runtime kinds already carried in the ledger.
    """
    def _is_runtime_wrapped_registry_code(code: str) -> bool:
        spec = get_finding_spec(code)
        return spec is not None and spec.role in ("barrier", "violation", "obligation")

    def _is_direct_registry_strict_code(code: str) -> bool:
        spec = get_finding_spec(code)
        return spec is not None and spec.role in ("barrier", "violation")

    def _runtime_violation_barrier_code(finding: Finding) -> str:
        barrier_code = str(finding.detail.get("barrier_code") or "").strip()
        if barrier_code and _is_runtime_wrapped_registry_code(barrier_code):
            return barrier_code
        return ""

    def _is_direct_registry_strict_kind(finding: Finding) -> bool:
        finding_kind = str(finding.kind or "")
        return finding_kind != "RUNTIME.VIOLATION" and _is_direct_registry_strict_code(finding_kind)

    if verdict is not None:
        reasons = {str(code) for code in verdict.barrier_codes if str(code)}
    else:
        reasons = {
            _runtime_violation_barrier_code(finding) or str(finding.kind)
            for finding in findings
            if finding.role == OBLIGATION_ROLE
            and finding.blocking
            and not _is_direct_registry_strict_kind(finding)
            and (str(finding.kind) or _runtime_violation_barrier_code(finding))
        }
    reasons.update(
        _runtime_violation_barrier_code(finding) or str(finding.kind)
        for finding in findings
        if (
            finding.role == VIOLATION_ROLE
            and not _is_direct_registry_strict_kind(finding)
            and (str(finding.kind) or _runtime_violation_barrier_code(finding))
        )
    )
    return tuple(sorted(reasons))


def compute_verdict_from_registry(
    profile: StrictProfile,
    finding_codes: list[str],
    *,
    has_internal_failure: bool = False,
) -> CompileVerdict:
    """Build a CompileVerdict from the governed registry-backed barrier rail."""
    if has_internal_failure:
        status: CompileStatus = "internal_failure"
    elif not finding_codes:
        status = "strict_clean"
    elif any(r in _SOURCE_INCOMPLETE_CODES for r in finding_codes):
        status = "source_incomplete"
    else:
        status = "strict_blocked_by_recovery"

    return CompileVerdict(
        mode="strict",
        profile=profile.name,
        status=status,
        barrier_codes=tuple(finding_codes),
    )


@dataclass(frozen=True)
class SectionStrictVerdict:
    """Per-section strict lineage from a specific amendment (C1).

    Attributes blame-chain-attributed strict barriers to individual
    sections instead of statute-wide aggregation.  Evidence consumes
    these to refine proof claims at section granularity.
    """

    section_label: str
    amendment_id: str
    barrier_codes: tuple[str, ...] = ()
    status: CompileStatus = "strict_clean"

    @property
    def is_strict_clean(self) -> bool:
        return self.status == "strict_clean"

    @property
    def barrier_families(self) -> set[BarrierFamily]:
        return {barrier_family_from_registry(kind) for kind in self.barrier_codes}

    @property
    def barrier_kinds(self) -> set[str]:
        return set(self.barrier_codes)


def compute_section_strict_verdicts(
    profile: StrictProfile,
    *,
    compiled_ops: list[dict[str, Any]],
    canonical_ops: list[LegalOperation],
    failed_ops: list[CompileFailure],
    findings: list[Finding],
    section_blame: dict[str, str],
) -> dict[str, SectionStrictVerdict]:
    """Compute per-section strict verdicts via blame chain (C1).

    For each section in section_blame, filters the compile artifacts to
    ops/failures/findings from the blamed amendment targeting that
    section, then computes a section-local strict verdict.

    Parameters
    ----------
    section_blame : dict mapping section_label → amendment_id (blamed source)
    """
    verdicts: dict[str, SectionStrictVerdict] = {}

    for section_label, amendment_id in section_blame.items():
        # Filter compiled_ops to this section
        section_compiled = [
            op
            for op in compiled_ops
            if _compiled_op_matches_section(op, section_label)
            and _compiled_op_source_statute(op) == amendment_id
        ]
        section_op_ids = {str(op.get("op_id") or "") for op in section_compiled if str(op.get("op_id") or "")}

        # Filter canonical ops to this amendment AND this section.
        section_canonical = [
            op
            for op in canonical_ops
            if op.source is not None
            and op.source.statute_id == amendment_id
            and _operation_matches_section(op, section_label)
        ]

        # Filter failures to this section
        section_failures = [
            f
            for f in failed_ops
            if f.target_section == section_label and str(f.source_statute or "") == amendment_id
        ]

        # Filter findings to this amendment and section-local evidence only.
        section_findings = [
            finding
            for finding in findings
            if finding.source_statute == amendment_id
            and _finding_matches_section(finding, section_label, section_op_ids)
        ]

        # Compute section-local strict fail reasons
        section_reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=section_compiled,
            canonical_ops=section_canonical,
            failures=section_failures,
            findings=section_findings,
        )

        # Determine status
        _source_codes = {
            "APPLY.SOURCE_INCOMPLETE",
            "APPLY.SOURCE_PATHOLOGY_DETECTED",
            "APPLY.SOURCE_CORRECTED_BY_PATCH",
        }
        if not section_reasons:
            status: CompileStatus = "strict_clean"
        elif any(r in _source_codes for r in section_reasons):
            status = "source_incomplete"
        else:
            status = "strict_blocked_by_recovery"

        verdicts[section_label] = SectionStrictVerdict(
            section_label=section_label,
            amendment_id=amendment_id,
            barrier_codes=tuple(section_reasons),
            status=status,
        )

    return verdicts
