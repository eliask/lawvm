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
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Mapping, Optional, cast

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    lower_lifecycle_events_to_temporal_events,
    validate_effect_graph_closure,
    validate_effect_graph_unique_ids,
)
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
from lawvm.core.temporal import TemporalEvent
from lawvm.core.timeline_results import MaterializationLineagePlan
from lawvm.core.timeline import (
    materialize_pit as _materialize_pit,
    materialize_pit_ex as _materialize_pit_ex,
    provision_lineage as _provision_lineage,
)

if TYPE_CHECKING:
    from lawvm.core.ir import IRStatute, ProvisionTimeline, ProvisionVersion
    from lawvm.core.timeline_results import MaterializationResult


StrictMode = Literal["strict", "quirks"]


def _require_bool_field(carrier_name: str, field_name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{carrier_name}.{field_name} must be a bool")


def _require_nonnegative_int(carrier_name: str, field_name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{carrier_name}.{field_name} must be a non-negative int")


def _string_frozenset(carrier_name: str, field_name: str, values: Iterable[object]) -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"{carrier_name}.{field_name} must be an iterable of strings, not a string")
    normalized = frozenset(values)
    if not all(isinstance(value, str) for value in normalized):
        raise ValueError(f"{carrier_name}.{field_name} must contain strings")
    return cast(frozenset[str], normalized)


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
    # v3 provenance graph attestation channels (§3 of UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md)
    allows_attested_reference_resolution: bool = False
    allows_attested_surface_extraction: bool = False
    allows_attested_source_correction: bool = False
    allows_attested_target_selection: bool = False
    allows_attested_semantic_compilation: bool = False
    allows_attested_ambiguity_adjudication: bool = False
    allows_attested_oracle_adjudication: bool = False
    # When False, attested LLM claims still need human review even if the
    # relevant attested channel is enabled. This keeps strict review policy in
    # StrictProfile instead of the legacy manual-claim ProfileTag.
    allows_unreviewed_llm_attestations: bool = False

    def __post_init__(self):
        if not self.name:
            raise ValueError("StrictProfile.name must be non-empty")
        _require_bool_field("StrictProfile", "requires_explicit_effective_date", self.requires_explicit_effective_date)
        _require_bool_field("StrictProfile", "allows_target_guessing", self.allows_target_guessing)
        _require_bool_field("StrictProfile", "allows_omission_expansion", self.allows_omission_expansion)
        _require_bool_field(
            "StrictProfile",
            "allows_uncovered_body_recovery",
            self.allows_uncovered_body_recovery,
        )
        _require_bool_field(
            "StrictProfile",
            "allows_fallback_whole_section_replace",
            self.allows_fallback_whole_section_replace,
        )
        _require_bool_field("StrictProfile", "allows_estimated_dates", self.allows_estimated_dates)
        _require_bool_field(
            "StrictProfile",
            "allows_context_dependent_anchor_resolution",
            self.allows_context_dependent_anchor_resolution,
        )
        _require_bool_field("StrictProfile", "allows_word_substitution", self.allows_word_substitution)
        _require_bool_field(
            "StrictProfile",
            "allows_source_correction_rules",
            self.allows_source_correction_rules,
        )
        _require_bool_field("StrictProfile", "allows_attested_reference_resolution", self.allows_attested_reference_resolution)
        _require_bool_field("StrictProfile", "allows_attested_surface_extraction", self.allows_attested_surface_extraction)
        _require_bool_field("StrictProfile", "allows_attested_source_correction", self.allows_attested_source_correction)
        _require_bool_field("StrictProfile", "allows_attested_target_selection", self.allows_attested_target_selection)
        _require_bool_field("StrictProfile", "allows_attested_semantic_compilation", self.allows_attested_semantic_compilation)
        _require_bool_field("StrictProfile", "allows_attested_ambiguity_adjudication", self.allows_attested_ambiguity_adjudication)
        _require_bool_field("StrictProfile", "allows_attested_oracle_adjudication", self.allows_attested_oracle_adjudication)
        _require_bool_field("StrictProfile", "allows_unreviewed_llm_attestations", self.allows_unreviewed_llm_attestations)


@dataclass(frozen=True)
class SourceCompletenessInfo:
    """Factual triplet: how complete is the amendment chain?

    Expressed as counts, not verdicts. Downstream consumers apply thresholds.
    """

    chain_length: int  # total amendments in parent chain
    source_available: int  # amendments with fetchable XML
    dates_available: int  # amendments with explicit effective date

    def __post_init__(self) -> None:
        _require_nonnegative_int("SourceCompletenessInfo", "chain_length", self.chain_length)
        _require_nonnegative_int("SourceCompletenessInfo", "source_available", self.source_available)
        _require_nonnegative_int("SourceCompletenessInfo", "dates_available", self.dates_available)
        if self.source_available > self.chain_length:
            raise ValueError("SourceCompletenessInfo.source_available cannot exceed chain_length")
        if self.dates_available > self.chain_length:
            raise ValueError("SourceCompletenessInfo.dates_available cannot exceed chain_length")


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
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
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
        if code in {"EMPTY_OPERATIVE_BODY", "fi_amendment_selection_source_artifact_missing"}:
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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extraction_tags",
            _string_frozenset("CompiledOpProvenanceTags", "extraction_tags", self.extraction_tags),
        )
        object.__setattr__(
            self,
            "target_guessing_tags",
            _string_frozenset("CompiledOpProvenanceTags", "target_guessing_tags", self.target_guessing_tags),
        )
        object.__setattr__(
            self,
            "scope_tags",
            _string_frozenset("CompiledOpProvenanceTags", "scope_tags", self.scope_tags),
        )
        object.__setattr__(
            self,
            "scope_sources",
            _string_frozenset("CompiledOpProvenanceTags", "scope_sources", self.scope_sources),
        )
        object.__setattr__(
            self,
            "scope_confidences",
            _string_frozenset("CompiledOpProvenanceTags", "scope_confidences", self.scope_confidences),
        )


class CompiledOpScopeSource(StrEnum):
    """Scope-source rail carried by a compiled-op transport row.

    Mirrors ``lawvm.finland.ops.ScopeResolutionSource`` by value. It is
    redeclared here (rather than imported) because ``finland`` depends on this
    core module; the values are the transport contract between the two layers.
    """

    PREAMBLE = "preamble"
    EXPLICIT_CHUNK = "explicit_chunk"
    CARRY_FORWARD = "carry_forward"
    GROUPED_PART = "grouped_part"
    GROUPED_CHAPTER = "grouped_chapter"
    EXPLICIT_SCOPE_REWRITE = "explicit_scope_rewrite"


class CompiledOpScopeWitnessKind(StrEnum):
    """Finding-code rail a compiled-op scope source resolves to."""

    CONTEXT_DEPENDENT_ANCHOR_RESOLUTION = "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"
    EXPLICIT_CHUNK_SCOPE_REQUIRED = "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED"
    EXPLICIT_SCOPE_REWRITE_REQUIRED = "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED"


# Single source-rail → witness-kind table. Replaces the prior string if/elif
# chain so the mapping is enum-keyed and a new source value is a typed miss
# (None) instead of a silent string fall-through.
_COMPILED_OP_SCOPE_WITNESS_KIND_BY_SOURCE: Mapping[
    CompiledOpScopeSource, CompiledOpScopeWitnessKind
] = {
    CompiledOpScopeSource.CARRY_FORWARD: CompiledOpScopeWitnessKind.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION,
    CompiledOpScopeSource.PREAMBLE: CompiledOpScopeWitnessKind.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION,
    CompiledOpScopeSource.GROUPED_PART: CompiledOpScopeWitnessKind.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION,
    CompiledOpScopeSource.GROUPED_CHAPTER: CompiledOpScopeWitnessKind.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION,
    CompiledOpScopeSource.EXPLICIT_CHUNK: CompiledOpScopeWitnessKind.EXPLICIT_CHUNK_SCOPE_REQUIRED,
    CompiledOpScopeSource.EXPLICIT_SCOPE_REWRITE: CompiledOpScopeWitnessKind.EXPLICIT_SCOPE_REWRITE_REQUIRED,
}


@dataclass(frozen=True)
class CompiledOpScopeWitness:
    """Normalized scope witness derived from a compiled-op transport row."""

    kind: str
    source: str
    confidence: str
    tag: str = ""
    used_legacy_tag_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("CompiledOpScopeWitness.kind must be non-empty")
        if not self.source:
            raise ValueError("CompiledOpScopeWitness.source must be non-empty")
        if not self.confidence:
            raise ValueError("CompiledOpScopeWitness.confidence must be non-empty")
        _require_bool_field(
            "CompiledOpScopeWitness",
            "used_legacy_tag_fallback",
            self.used_legacy_tag_fallback,
        )


@dataclass(frozen=True)
class CompiledOpEvidenceRow:
    """Typed strict-check evidence normalized from a compiled-op transport row."""

    source_statute: str = ""
    provenance_tags: CompiledOpProvenanceTags = field(default_factory=CompiledOpProvenanceTags)
    scope_witness: CompiledOpScopeWitness | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_statute", str(self.source_statute or "").strip())
        if not isinstance(self.provenance_tags, CompiledOpProvenanceTags):
            raise ValueError("CompiledOpEvidenceRow.provenance_tags must be CompiledOpProvenanceTags")
        if self.scope_witness is not None and not isinstance(self.scope_witness, CompiledOpScopeWitness):
            raise ValueError("CompiledOpEvidenceRow.scope_witness must be CompiledOpScopeWitness when provided")


@dataclass(frozen=True)
class AdmissibleBindingCoverage:
    """Certificate that a subsection slot binding was deterministic."""

    slot_id: int
    amendment_id: str
    candidate_count: int  # 1 = single admissible, >1 = ambiguous
    admissibility: Literal["single", "ambiguous", "fallback"]

    def __post_init__(self) -> None:
        _require_nonnegative_int("AdmissibleBindingCoverage", "slot_id", self.slot_id)
        _require_nonnegative_int("AdmissibleBindingCoverage", "candidate_count", self.candidate_count)
        if self.admissibility not in {"single", "ambiguous", "fallback"}:
            raise ValueError("AdmissibleBindingCoverage.admissibility must be single, ambiguous, or fallback")
        if self.admissibility == "single" and self.candidate_count != 1:
            raise ValueError("AdmissibleBindingCoverage single admissibility requires candidate_count=1")
        if self.admissibility == "ambiguous" and self.candidate_count <= 1:
            raise ValueError("AdmissibleBindingCoverage ambiguous admissibility requires candidate_count > 1")


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
        elif self.family == "text":
            if self.action != "text_patch":
                raise TypeError("CanonicalEffect family='text' requires action='text_patch'")
        elif self.family == "lifecycle":
            if self.action not in {
                "commence",
                "expire",
                "suspend",
                "revive",
                "applicability",
            }:
                raise TypeError("CanonicalEffect family='lifecycle' requires lifecycle action")
        else:
            raise TypeError("CanonicalEffect.family must be structural, text, or lifecycle")
        if not isinstance(self.target, LegalAddress):
            raise TypeError("CanonicalEffect.target must be a LegalAddress")


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
    violations: list[str] = []
    for i, op in enumerate(structural_ops):
        if not isinstance(op, LegalOperation):
            violations.append(
                f"{caller}.structural_ops[{i}] is {type(op).__qualname__!r}, "
                "expected LegalOperation; frontend-local types must be lowered "
                "before entering the shared canonical bundle"
            )
    return violations


def _merge_executable_temporal_events(
    direct_events: tuple[TemporalEvent, ...],
    lifecycle_events: tuple[EffectLifecycleEvent, ...],
) -> tuple[TemporalEvent, ...]:
    events_by_id: dict[str, TemporalEvent] = {}
    for event in direct_events + lower_lifecycle_events_to_temporal_events(lifecycle_events):
        previous = events_by_id.get(event.event_id)
        if previous is None:
            events_by_id[event.event_id] = event
        elif previous != event:
            raise ValueError(
                "CanonicalBundle.executable_temporal_events conflicting "
                f"duplicate event_id: {event.event_id!r}"
            )
    return tuple(events_by_id.values())


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
    source_effects: tuple[EffectRef, ...] = ()
    effect_relations: tuple[EffectRelation, ...] = ()
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...] = ()
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
        object.__setattr__(self, "temporal_events", tuple(self.temporal_events))
        object.__setattr__(self, "source_effects", tuple(self.source_effects))
        object.__setattr__(self, "effect_relations", tuple(self.effect_relations))
        object.__setattr__(self, "effect_lifecycle_events", tuple(self.effect_lifecycle_events))
        if not all(isinstance(event, TemporalEvent) for event in self.temporal_events):
            raise TypeError("CanonicalBundle.temporal_events must contain TemporalEvent records")
        if not all(isinstance(effect, EffectRef) for effect in self.source_effects):
            raise TypeError("CanonicalBundle.source_effects must contain EffectRef records")
        if not all(isinstance(relation, EffectRelation) for relation in self.effect_relations):
            raise TypeError("CanonicalBundle.effect_relations must contain EffectRelation records")
        if not all(isinstance(event, EffectLifecycleEvent) for event in self.effect_lifecycle_events):
            raise TypeError(
                "CanonicalBundle.effect_lifecycle_events must contain EffectLifecycleEvent records"
            )
        validate_effect_graph_unique_ids(
            subject="CanonicalBundle",
            source_effects=self.source_effects,
            effect_relations=self.effect_relations,
            effect_lifecycle_events=self.effect_lifecycle_events,
        )
        validate_effect_graph_closure(
            subject="CanonicalBundle",
            source_effects=self.source_effects,
            effect_relations=self.effect_relations,
            effect_lifecycle_events=self.effect_lifecycle_events,
        )
        _merge_executable_temporal_events(
            self.temporal_events,
            self.effect_lifecycle_events,
        )

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
        """Return the distinct executable temporal-event kinds for this bundle."""
        return distinct_event_kinds(self.executable_temporal_events)

    @property
    def temporal_events_with_activation_rules(self) -> int:
        """Return the number of executable temporal events carrying an activation rule."""
        return count_events_with_activation_rules(self.executable_temporal_events)

    @property
    def temporal_events_with_source(self) -> int:
        """Return the number of executable temporal events carrying source data."""
        return count_events_with_source(self.executable_temporal_events)

    @property
    def temporal_event_activation_rule_kinds(self) -> tuple[str, ...]:
        """Return executable temporal activation-rule kinds for this bundle."""
        return distinct_activation_rule_kinds(self.executable_temporal_events)

    @property
    def lifecycle_projected_temporal_events(self) -> tuple[TemporalEvent, ...]:
        """Return executable temporal projections derived from lifecycle events."""
        return lower_lifecycle_events_to_temporal_events(self.effect_lifecycle_events)

    @property
    def executable_temporal_events(self) -> tuple[TemporalEvent, ...]:
        """Return direct temporal events plus lifecycle-derived projections."""
        return _merge_executable_temporal_events(
            self.temporal_events,
            self.effect_lifecycle_events,
        )

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
        return _provision_lineage(
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
        return _materialize_pit(
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
        return _materialize_pit_ex(
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
    verdict_status: CompileStatus
    barrier_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"strict", "quirks"}:
            raise ValueError("CompileVerdict.mode must be strict or quirks")
        if not self.profile:
            raise ValueError("CompileVerdict.profile must be non-empty")
        if self.verdict_status not in {
            "strict_clean",
            "strict_blocked_by_recovery",
            "source_incomplete",
            "internal_failure",
        }:
            raise ValueError("CompileVerdict.verdict_status is not a known compile status")
        object.__setattr__(self, "barrier_codes", tuple(self.barrier_codes))
        if not all(isinstance(code, str) and code for code in self.barrier_codes):
            raise ValueError("CompileVerdict.barrier_codes must contain non-empty strings")
        if self.verdict_status == "strict_clean" and self.barrier_codes:
            raise ValueError("CompileVerdict strict_clean status cannot carry barrier_codes")

    @property
    def is_strict_clean(self) -> bool:
        return self.verdict_status == "strict_clean"

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
    compiled_ops: Iterable[CompiledOpEvidenceRow | Mapping[str, Any]],
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
        evidence = row if isinstance(row, CompiledOpEvidenceRow) else _compiled_op_evidence_row(row)
        compiled_extraction_tags.update(evidence.provenance_tags.extraction_tags)
        compiled_target_guessing_tags.update(evidence.provenance_tags.target_guessing_tags)
        compiled_scope_tags.update(evidence.provenance_tags.scope_tags)
        compiled_scope_sources.update(evidence.provenance_tags.scope_sources)
        compiled_scope_confidences.update(evidence.provenance_tags.scope_confidences)

    return CompiledOpProvenanceTags(
        extraction_tags=frozenset(compiled_extraction_tags),
        target_guessing_tags=frozenset(compiled_target_guessing_tags),
        scope_tags=frozenset(compiled_scope_tags),
        scope_sources=frozenset(compiled_scope_sources),
        scope_confidences=frozenset(compiled_scope_confidences),
    )


def _compiled_op_source_statute(op: Mapping[str, Any]) -> str:
    """Return the amendment id associated with a compiled-op row, if any."""

    for key in ("source_statute", "amendment_id", "source"):
        value = op.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = op.get("source")
    if isinstance(source, OperationSource):
        return str(source.statute_id or "").strip()
    return ""


def _string_set_from_row_list(row: Mapping[str, Any], key: str) -> frozenset[str]:
    value = row.get(key)
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(part).strip() for part in value if str(part).strip())


def _provenance_bag_view(row: Mapping[str, Any], bag_key: str) -> list[str]:
    """Return a compiled-op row's per-bag provenance tag view.

    The serialized schema carries a single typed ``provenance`` field instead of
    the three raw bag columns; the per-bag tag lists are derived (by the FI codec
    at serialize time) under ``provenance["bags"]``. This reader stays in ``core``
    (no jurisdiction import) by reading those pre-derived views, so the
    extraction / target-guessing / scope tag sets a strict-finding consumer keys
    on are reconstructed from the typed field.
    """
    provenance = row.get("provenance")
    if not isinstance(provenance, Mapping):
        return []
    bags = provenance.get("bags")
    if not isinstance(bags, Mapping):
        return []
    view = bags.get(bag_key)
    if not isinstance(view, (list, tuple)):
        return []
    return [str(part).strip() for part in view if str(part).strip()]


def _provenance_bag_set(row: Mapping[str, Any], bag_key: str) -> frozenset[str]:
    return frozenset(_provenance_bag_view(row, bag_key))


def _compiled_op_evidence_row(row: Mapping[str, Any]) -> CompiledOpEvidenceRow:
    scope_source = row.get("scope_source")
    scope_confidence = row.get("scope_confidence")
    return CompiledOpEvidenceRow(
        source_statute=_compiled_op_source_statute(row),
        provenance_tags=CompiledOpProvenanceTags(
            extraction_tags=_provenance_bag_set(row, "extraction_provenance_tags"),
            target_guessing_tags=_provenance_bag_set(row, "target_guessing_provenance_tags"),
            scope_tags=_provenance_bag_set(row, "scope_provenance_tags"),
            scope_sources=(
                frozenset({scope_source.strip()})
                if isinstance(scope_source, str) and scope_source.strip()
                else frozenset()
            ),
            scope_confidences=(
                frozenset({scope_confidence.strip()})
                if isinstance(scope_confidence, str) and scope_confidence.strip()
                else frozenset()
            ),
        ),
        scope_witness=_compiled_op_scope_witness(row),
    )


def _compiled_op_scope_witness(row: Mapping[str, Any]) -> CompiledOpScopeWitness | None:
    """Return the normalized scope witness carried by a compiled-op row.

    Structured `scope_source` / `scope_confidence` is authoritative. Raw
    `scope_provenance_tags` are retained only as explicit compatibility for
    legacy rows that predate the structured carrier.
    """

    scope_source = row.get("scope_source")
    scope_confidence = row.get("scope_confidence")
    scope_tag_list = _provenance_bag_view(row, "scope_provenance_tags")

    source_value = str(scope_source).strip() if isinstance(scope_source, str) else ""
    confidence_value = str(scope_confidence).strip() if isinstance(scope_confidence, str) else ""
    if source_value and confidence_value:
        scope_source_member = _compiled_op_scope_source_member(source_value)
        if scope_source_member is None:
            return None
        scope_kind = _COMPILED_OP_SCOPE_WITNESS_KIND_BY_SOURCE[scope_source_member]
        return CompiledOpScopeWitness(
            kind=str(scope_kind),
            source=str(scope_source_member),
            confidence=confidence_value,
            tag=next(iter(scope_tag_list), ""),
            used_legacy_tag_fallback=False,
        )

    for tag, source_member, confidence in _COMPILED_OP_SCOPE_LEGACY_TAG_WITNESSES:
        if tag in scope_tag_list:
            return CompiledOpScopeWitness(
                kind=str(_COMPILED_OP_SCOPE_WITNESS_KIND_BY_SOURCE[source_member]),
                source=str(source_member),
                confidence=confidence,
                tag=tag,
                used_legacy_tag_fallback=True,
            )

    return None


def _compiled_op_scope_source_member(value: str) -> CompiledOpScopeSource | None:
    """Return the typed scope-source member for a transport-row source string."""
    try:
        return CompiledOpScopeSource(value)
    except ValueError:
        return None


# Legacy provenance-tag fallback, evaluated in priority order. Each entry binds
# a scope tag to its typed (source, confidence); the witness kind is derived
# from the same source→kind table used by the structured carrier path so the
# two rails cannot drift.
_COMPILED_OP_SCOPE_LEGACY_TAG_WITNESSES: tuple[
    tuple[str, CompiledOpScopeSource, str], ...
] = (
    ("chapter_scope_from_explicit_chunk", CompiledOpScopeSource.EXPLICIT_CHUNK, "explicit"),
    ("chapter_scope_stripped_subsection_insert", CompiledOpScopeSource.EXPLICIT_SCOPE_REWRITE, "rewritten"),
    ("chapter_scope_stripped_section_facet_insert", CompiledOpScopeSource.EXPLICIT_SCOPE_REWRITE, "rewritten"),
    ("chapter_scope_stripped_unique_section", CompiledOpScopeSource.EXPLICIT_SCOPE_REWRITE, "rewritten"),
    (
        "chapter_scope_stripped_duplicate_label_outside_stated_chapter",
        CompiledOpScopeSource.EXPLICIT_SCOPE_REWRITE,
        "rewritten",
    ),
    ("chapter_scope_carry_forward", CompiledOpScopeSource.CARRY_FORWARD, "inferred"),
    ("chapter_scope_from_preamble", CompiledOpScopeSource.PREAMBLE, "inferred"),
    ("grouped_part_scope", CompiledOpScopeSource.GROUPED_PART, "inferred"),
    ("grouped_chapter_scope", CompiledOpScopeSource.GROUPED_CHAPTER, "inferred"),
)


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
    "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK": ("allows_context_dependent_anchor_resolution", True),
    "APPLY.WORD_SUBSTITUTION": ("allows_word_substitution", True),
    "APPLY.SOURCE_CORRECTED_BY_PATCH": ("allows_source_correction_rules", True),
    "TIME.MISSING_EFFECTIVE_DATE": ("requires_explicit_effective_date", False),
    "TIME.ESTIMATED_EFFECTIVE_DATE": ("allows_estimated_dates", True),
    # v3 attestation-resolution channels (§8.1 of UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md)
    "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION":
        ("allows_attested_reference_resolution", True),
    "ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION":
        ("allows_attested_reference_resolution", True),
    "ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION":
        ("allows_attested_reference_resolution", True),
    "ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION":
        ("allows_attested_reference_resolution", True),
    "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION":
        ("allows_attested_surface_extraction", True),
    "ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION":
        ("allows_attested_surface_extraction", True),
    "APPLY.REF_TARGET_CORRECTED_BY_ATTESTATION":
        ("allows_attested_source_correction", True),
    "APPLY.METADATA_ATTRIBUTION_CORRECTED_BY_ATTESTATION":
        ("allows_attested_source_correction", True),
    "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION":
        ("allows_attested_target_selection", True),
    "PARSE.PREAMBLE_CLAUSE_FAILED.RESOLVED_BY_ATTESTATION":
        ("allows_attested_semantic_compilation", True),
    "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION":
        ("allows_attested_ambiguity_adjudication", True),
    "LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION":
        ("allows_attested_ambiguity_adjudication", True),
    "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION":
        ("allows_attested_oracle_adjudication", True),
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
        "allows_attested_reference_resolution": profile.allows_attested_reference_resolution,
        "allows_attested_surface_extraction": profile.allows_attested_surface_extraction,
        "allows_attested_source_correction": profile.allows_attested_source_correction,
        "allows_attested_target_selection": profile.allows_attested_target_selection,
        "allows_attested_semantic_compilation": profile.allows_attested_semantic_compilation,
        "allows_attested_ambiguity_adjudication": profile.allows_attested_ambiguity_adjudication,
        "allows_attested_oracle_adjudication": profile.allows_attested_oracle_adjudication,
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
    effect_lifecycle_events: Iterable[EffectLifecycleEvent] = (),
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
    compiled_evidence_rows = tuple(_compiled_op_evidence_row(row) for row in compiled_ops)

    for event in effect_lifecycle_events:
        if event.kind != "unresolved_effect_target":
            continue
        source_finding = str(event.detail.get("source_finding") or "").strip()
        if source_finding:
            triggered.add(source_finding)
        else:
            triggered.add("APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED")

    if any(
        op.action in {StructuralAction.TEXT_REPLACE, StructuralAction.TEXT_REPEAL}
        for op in canonical_ops_list
    ):
        triggered.add("APPLY.WORD_SUBSTITUTION")

    compiled_provenance_tags = _compiled_op_provenance_tag_sets(compiled_evidence_rows)

    if compiled_provenance_tags.target_guessing_tags:
        triggered.add("PARSE.TARGET_GUESSING")

    extraction_fallback_tags = {
        "extraction_fallback_heuristic",
        "extraction_title_fallback",
        "extraction_preamble_body",
        "repeal_reenact_normalized",
        "fallback_insert_supplement",
        "fallback_insert_supplement_shadowed",
        "fallback_replace_supplement",
        "fallback_replace_supplement_shadowed",
        "root_insert_supplement",
    }
    if compiled_provenance_tags.extraction_tags & extraction_fallback_tags:
        triggered.add("PARSE.EXTRACTION_FALLBACK")

    for row in compiled_evidence_rows:
        if row.scope_witness is not None:
            triggered.add(row.scope_witness.kind)

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
        verdict_status: CompileStatus = "internal_failure"
    elif not finding_codes:
        verdict_status = "strict_clean"
    elif any(r in _SOURCE_INCOMPLETE_CODES for r in finding_codes):
        verdict_status = "source_incomplete"
    else:
        verdict_status = "strict_blocked_by_recovery"

    return CompileVerdict(
        mode="strict",
        profile=profile.name,
        verdict_status=verdict_status,
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
    verdict_status: CompileStatus = "strict_clean"

    def __post_init__(self) -> None:
        if not self.section_label:
            raise ValueError("SectionStrictVerdict.section_label must be non-empty")
        if not self.amendment_id:
            raise ValueError("SectionStrictVerdict.amendment_id must be non-empty")
        if self.verdict_status not in {
            "strict_clean",
            "strict_blocked_by_recovery",
            "source_incomplete",
            "internal_failure",
        }:
            raise ValueError("SectionStrictVerdict.verdict_status is not a known compile status")
        object.__setattr__(self, "barrier_codes", tuple(self.barrier_codes))
        if not all(isinstance(code, str) and code for code in self.barrier_codes):
            raise ValueError("SectionStrictVerdict.barrier_codes must contain non-empty strings")
        if self.verdict_status == "strict_clean" and self.barrier_codes:
            raise ValueError("SectionStrictVerdict strict_clean status cannot carry barrier_codes")

    @property
    def is_strict_clean(self) -> bool:
        return self.verdict_status == "strict_clean"

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
            verdict_status: CompileStatus = "strict_clean"
        elif any(r in _source_codes for r in section_reasons):
            verdict_status = "source_incomplete"
        else:
            verdict_status = "strict_blocked_by_recovery"

        verdicts[section_label] = SectionStrictVerdict(
            section_label=section_label,
            amendment_id=amendment_id,
            barrier_codes=tuple(section_reasons),
            verdict_status=verdict_status,
        )

    return verdicts
