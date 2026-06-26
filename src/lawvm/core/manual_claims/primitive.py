"""Manual compilation claim — four-record primitive (v2.2 design memo §4).

DEPRECATED: v2.2 four-record primitive.  v3 substrate is the ProvenanceGraph
(see src/lawvm/core/provenance_graph.py + manual_claims/native.py).  These
types are retained for one transition release as a compatibility shim.

Four separate records per claim:
  ManualCompilationClaim    — immutable, content-addressed
  ClaimState                — mutable lifecycle state, one row per claim_id
  ClaimStateEvent           — append-only audit log of every transition
  ClaimCompositionDecision  — per-build authorization, derived by composer only

Enums follow § 4 exactly. ClaimKindSpec + the module-level registry live in
kind_registry.py; this module has NO Finland-specific imports.

ProfileTag is DELETED per v3 design (§10: use StrictProfile + fingerprint).
It remains importable to produce a deprecation warning; remove by Step 3.

Design discipline (AGENTS.md §1.9, feedback_frozen_for_fp_not_serialization,
feedback_no_pydantic_until_serialization, feedback_no_phrase_registries):
  - All records are frozen dataclasses with slots=True (FP discipline).
  - Tuple fields for multi-valued data, never lists.
  - No Pydantic; plain Python.
  - No phrase / banned-verb registries; structural enum checks only.
  - claim_precedence.yaml is exempt (operator-authored config boundary).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional, Tuple, TypeAlias


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ClaimLayer(Enum):
    SUBSTRATE = "substrate"
    EXTRACTION = "extraction"
    CORRECTION = "correction"
    ADJUDICATION = "adjudication"


class SourceWitnessType(Enum):
    FINLEX_AKN = "finlex_akn"
    FINLEX_CORRIGENDUM = "finlex_corrigendum"
    EXTERNAL_ARCHIVAL = "external_archival"
    OPERATOR_FILING = "operator_filing"
    LLM_PROPOSAL = "llm_proposal"


class ReviewStatus(Enum):
    PROPOSED = "proposed"
    SECOND_PASS_CORRELATED = "second_pass_correlated"
    HUMAN_REVIEWED = "human_reviewed"


class ValidatorStatus(Enum):
    UNVALIDATED = "unvalidated"
    SPAN_VERIFIED = "span_verified"
    ENTAILMENT_VERIFIED = "entailment_verified"
    MIGRATION_REVALIDATED = "migration_revalidated"


class ClaimStatus(Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"
    ORPHANED = "orphaned"
    NEEDS_REVALIDATION = "needs_revalidation"


class ClaimConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SPECULATIVE = "speculative"


class _ProfileTagDeprecated(Enum):
    """DEPRECATED. Use StrictProfile + profile fingerprint (v3 design §10).

    ProfileTag is deleted from the v3 design.  This alias remains importable
    to emit a deprecation warning during the transition release.  Remove by Step 3.
    """
    DETERMINISTIC_ONLY = "deterministic_only"
    STRICT_WITH_ATTESTED_CLAIMS = "strict_with_attested_claims"
    NON_STRICT_WITH_CLAIMS = "non_strict_with_claims"
    EXPLORATORY = "exploratory"


def __getattr__(name: str) -> "type[_ProfileTagDeprecated]":
    if name == "ProfileTag":
        warnings.warn(
            "ProfileTag is deprecated and will be removed in a future release. "
            "Use StrictProfile + profile fingerprint instead (v3 design §10).",
            DeprecationWarning,
            stacklevel=2,
        )
        return _ProfileTagDeprecated
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    ProfileTag: TypeAlias = _ProfileTagDeprecated


# ---------------------------------------------------------------------------
# Supporting value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Producer:
    """Who or what produced a claim or event."""

    producer_kind: str
    """'operator' | 'llm' | 'tool'"""
    handle: Optional[str]
    """Human operator handle, or None for automated producers."""
    model_id: Optional[str]
    """LLM model id when producer_kind='llm'."""
    timestamp: datetime
    environment: Optional[str]
    """e.g. 'lawvm-cli-v0.1', 'lawvm-propose-claims'"""


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """Points to the source artifact that contains cited evidence."""

    artifact_kind: str
    """'finlex_akn' | 'finlex_pdf' | 'external_archival' | ..."""
    statute_id: Optional[str]
    he_id: Optional[str]
    version_id: Optional[str]


@dataclass(frozen=True, slots=True)
class ClaimScope:
    """Scope of the claim within the statute."""

    statute_id: str
    provision_ref: Optional[str]
    """e.g. 'chapter:2/section:5'"""
    valid_at_start: Optional[date]
    valid_at_end: Optional[date]


# ---------------------------------------------------------------------------
# The four primary records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManualCompilationClaim:
    """Immutable, content-addressed claim record.

    claim_id is SHA-256 over canonical_payload + schema_version + jurisdiction.
    Once filed, claim_id never changes. Lifecycle state lives in ClaimState.

    Critical fields NOT present here (they live in separate records):
      review_status, validator_status, replay_authorized,
      admissibility_profile, consumed_by_builds, affected_projection_rows.
    """

    claim_id: str
    """Full SHA-256 hex over canonical payload. Load-time mismatch → hard fail."""
    schema_version: str
    """e.g. 'v1'. Included in hash domain."""
    jurisdiction: str
    """e.g. 'fi'. Included in hash domain."""

    claim_kind: str
    """Namespaced: 'fi.v1.INLINE_STATUTE_RESOLUTION'. Registry in kind_registry.py."""
    claim_layer: ClaimLayer
    claim_scope: ClaimScope

    target: Tuple[Tuple[str, object], ...]
    """Frozen representation of the typed target dict. Tuple of (key, value) pairs."""
    value: Tuple[Tuple[str, object], ...]
    """Frozen representation of the typed value dict. Tuple of (key, value) pairs."""

    source_witness_type: SourceWitnessType
    producer: Producer

    cited_source_locator: SourceLocator
    cited_source_span: Tuple[int, int]
    """Byte offsets into cited source artifact."""
    cited_source_hash: str
    """Full SHA-256 of the bytes at cited_source_span."""
    dependency_fingerprint: Tuple[Tuple[str, str], ...]
    """(key, value) pairs: target identity + text-range hash + structural parent hash."""

    valid_at: Tuple[date, Optional[date]]
    supersedes: Tuple[str, ...]
    """claim_ids this claim supersedes."""
    supersession_delta_reason: Optional[str]
    """Required when supersedes is non-empty."""
    disputes: Tuple[str, ...]
    """claim_ids this claim disputes."""

    requested_profiles: Tuple[ProfileTag, ...]
    """Profile membership request — NOT assertion. Composer decides eligibility."""
    rationale: str


@dataclass(frozen=True, slots=True)
class ClaimState:
    """Mutable lifecycle state per claim_id.

    Mutation is by writing a NEW state row, not by in-place update.
    One CURRENT row per claim_id in states/current/.
    The event log (ClaimStateEvent) is the source of truth; this is a projection.

    Changing review_status from proposed to human_reviewed updates ClaimState
    but does NOT change ManualCompilationClaim or its claim_id.
    """

    claim_id: str
    claim_state_status: ClaimStatus
    review_status: ReviewStatus
    validator_status: ValidatorStatus
    confidence: ClaimConfidence
    last_updated: datetime


@dataclass(frozen=True, slots=True)
class ClaimStateEvent:
    """Append-only audit log entry for every state transition.

    events.jsonl is the authoritative source of truth.
    ClaimState is a projection of the event log.
    Events are NEVER modified or deleted.

    Consumption events (event_kind='consumed') carry build_id and
    affected_projection_row_hashes in the reason payload.
    """

    claim_id: str
    event_kind: str
    """proposed | accepted | rejected | retracted | superseded |
    revalidated | orphaned | consumed | needs_revalidation"""
    timestamp: datetime
    producer: Producer
    old_status: Optional[str]
    new_status: Optional[str]
    reason: str


@dataclass(frozen=True, slots=True)
class ClaimCompositionDecision:
    """Per-build authorization. Derived by composer, NEVER author-set.

    A claim file asserting authorized=True cannot self-authorize. The composer
    reads ManualCompilationClaim + ClaimState + profile policy and emits this.
    """

    claim_id: str
    build_id: str
    profile: ProfileTag
    authorized: bool
    reason_code: str
    """e.g. 'extraction_layer_null_slot_filled' | 'rejected_replay_authorized_false'"""
    replay_authorized: bool
    """Derived from validator passes per MANUAL_COMPILATION_CLAIMS.md §107."""


# ---------------------------------------------------------------------------
# Frontier authority types (§16 Slice 1 — authority for missing-row proposals)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractionFrontierRow:
    """Typed authority record: deterministic extractor failed here.

    Created by the deterministic extraction pipeline when it produces a NULL
    or UNKNOWN slot. Only a filed ExtractionFrontierRow authorizes LLM
    proposal generation for the corresponding (claim_kind, target) pair.
    """

    frontier_id: str
    """Stable id for this gap. SHA-256 of (claim_kind, statute_id, provision_ref, slot)."""
    claim_kind: str
    """Which claim kind would fill this gap."""
    statute_id: str
    provision_ref: Optional[str]
    slot: str
    """Which specific field is NULL/unknown."""
    severity: str
    """'high' | 'medium' | 'low' — priority for LLM proposal scheduling."""
    detected_at: datetime
    pipeline_run_id: str
    citation_text: Optional[str] = None
    """Literal citation text from the source (raw_text from inline_citations parquet).
    Passed to the LLM backend so it can produce a targeted resolution prompt.
    None for frontier rows produced from fi_refs parquet (no literal citation text available)."""


@dataclass(frozen=True, slots=True)
class GapDiscoveryRow:
    """Typed authority record: expected projection row is missing entirely.

    Differs from ExtractionFrontierRow in that no NULL slot exists —
    the entire row is absent when it should be present.
    Created by lawvm propose-claims --gap-discovery pass.
    """

    gap_id: str
    """Stable id. SHA-256 of (claim_kind, statute_id, expected_target_key)."""
    claim_kind: str
    statute_id: str
    expected_target_key: str
    """Description of the expected projection row that is absent."""
    severity: str
    detected_at: datetime
    pipeline_run_id: str
