"""Manual compilation claims — core typed primitives.

Four-record split (§4 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2):
  ManualCompilationClaim    — immutable, content-addressed via SHA-256
  ClaimState                — mutable lifecycle (one current row per claim_id)
  ClaimStateEvent           — append-only audit log
  ClaimCompositionDecision  — per-build authorization derived by composer

Enums and supporting types live in .primitive.
Content-addressing and hashing helpers live in .hashing.
ClaimKind registry lives in .kind_registry.
Storage (objects/sha256/, events.jsonl, states/current/) lives in .storage.
Event-log → current-state projection lives in .state.
"""
from lawvm.core.manual_claims.primitive import (
    ClaimCompositionDecision,
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ExtractionFrontierRow,
    GapDiscoveryRow,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)
from lawvm.core.manual_claims.kind_registry import (
    ClaimKindSpec,
    ValidationResult,
    get_claim_kind_spec,
    list_registered_kinds,
    register_claim_kind,
)


from lawvm.core.manual_claims.composer import derive_composition_decision
from lawvm.core.manual_claims.precedence import (
    AmbiguousClaimSet,
    LayerPrecedenceRule,
    PrecedenceRegistry,
    load_precedence_registry,
    resolve_precedence,
)
__all__ = [
    "ClaimCompositionDecision",
    "ClaimConfidence",
    "ClaimKindSpec",
    "ClaimLayer",
    "ClaimScope",
    "ClaimState",
    "ClaimStateEvent",
    "ClaimStatus",
    "ExtractionFrontierRow",
    "GapDiscoveryRow",
    "ManualCompilationClaim",
    "Producer",
    "ProfileTag",
    "ReviewStatus",
    "SourceLocator",
    "SourceWitnessType",
    "ValidatorStatus",
    "ValidationResult",
    "get_claim_kind_spec",
    "list_registered_kinds",
    "register_claim_kind",
    "AmbiguousClaimSet",
    "LayerPrecedenceRule",
    "PrecedenceRegistry",
    "derive_composition_decision",
    "load_precedence_registry",
    "resolve_precedence",
]
