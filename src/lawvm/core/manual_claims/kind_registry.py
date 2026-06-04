"""ClaimKind registry — module-level dict mapping claim_kind string → ClaimKindSpec.

Per feedback_no_pydantic_until_serialization + feedback_no_phrase_registries:
  - Plain Python dataclasses, not Pydantic models.
  - Module-level dict, not a YAML file loaded at import time.
  - claim_precedence.yaml is a separate operator-authored config boundary.

ClaimKind values are namespaced strings: 'fi.v1.INLINE_STATUTE_RESOLUTION'.
Core owns the registry structure; frontends register their own kinds.

Frontends call register_claim_kind() at module import time to add their kinds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple


@dataclass(frozen=True, slots=True)
class ClaimKindSpec:
    """Schema and validator references for one ClaimKind."""

    claim_kind: str
    """Namespaced string, e.g. 'fi.v1.INLINE_STATUTE_RESOLUTION'."""
    jurisdiction: str
    """e.g. 'fi'. Inferred from namespace prefix."""
    layer: str
    """'substrate' | 'extraction' | 'correction' | 'adjudication'"""
    description: str

    target_fields: Tuple[str, ...]
    """Required keys in the target dict."""
    value_fields: Tuple[str, ...]
    """Required keys in the value dict."""

    span_validator: Optional[Callable[[object, object], "ValidationResult"]]
    """Deterministic span-existence validator. None if not implemented yet."""
    entailment_validator: Optional[Callable[[object, object], "ValidationResult"]]
    """Deterministic entailment validator. None if not implemented yet."""

    is_semantic_compilation_claim: bool = False
    """True for kinds that imply replay/projection mutation (BRANCH_OP_SET,
    TARGET_SELECTION, substrate body atoms). These require replay_authorized=True
    per §6 of UNIFIED_MANUAL_CLAIMS_DESIGN.md. INLINE_STATUTE_RESOLUTION is False."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of a deterministic validator run."""

    passed: bool
    validator_name: str
    """e.g. 'span_verified' | 'entailment_verified'"""
    reason: str
    """Human-readable reason for failure, or 'ok' on pass."""
    details: Optional[str]
    """Optional structured detail for diagnostics."""


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, ClaimKindSpec] = {}


def register_claim_kind(spec: ClaimKindSpec) -> None:
    """Register a ClaimKindSpec. Raises ValueError on duplicate kind."""
    if spec.claim_kind in _REGISTRY:
        raise ValueError(f"ClaimKind already registered: {spec.claim_kind!r}")
    _REGISTRY[spec.claim_kind] = spec


def get_claim_kind_spec(claim_kind: str) -> Optional[ClaimKindSpec]:
    """Return the ClaimKindSpec for claim_kind, or None if unregistered."""
    return _REGISTRY.get(claim_kind)


def list_registered_kinds() -> Tuple[str, ...]:
    """Return tuple of all registered claim_kind strings."""
    return tuple(sorted(_REGISTRY.keys()))
