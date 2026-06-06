"""Manual compilation claims.

v3 graph-native substrate: src/lawvm/core/manual_claims/native.py
v2.2 compatibility shim: .primitive / .state / .storage / .composer (deprecated)

The v2.2 four-record types (ManualCompilationClaim, ClaimState, ClaimStateEvent,
ClaimCompositionDecision) are retained for one transition release.  New code
should use the graph-native API in native.py.

ProfileTag is DELETED per v3 design §10.  Importing it emits a DeprecationWarning.
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

# Graph-native operations (v3)
from lawvm.core.manual_claims.native import (
    attest,
    build_claim_subgraph,
    manual_claim_authorization_evidence_report,
    manual_claim_authorization_projection,
    query_retraction_taint,
    query_state,
    query_state_from_store,
    submit_assertion,
)

__all__ = [
    # v2.2 compat (deprecated)
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
    # v3 graph-native
    "attest",
    "build_claim_subgraph",
    "manual_claim_authorization_evidence_report",
    "manual_claim_authorization_projection",
    "query_retraction_taint",
    "query_state",
    "query_state_from_store",
    "submit_assertion",
]
