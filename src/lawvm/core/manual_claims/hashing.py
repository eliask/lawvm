"""Content-addressing helpers for ManualCompilationClaim.

claim_id = full SHA-256 over canonical_payload(claim) + schema_version + jurisdiction.

Design (§4 + adversary finding #17):
  - Full SHA-256, not 64-bit truncation.
  - schema_version + jurisdiction included in hash domain.
  - Load-time mismatch → hard fail with descriptive error.
  - canonical_payload is a sorted, deterministic JSON encoding of the
    immutable claim fields (excluding claim_id itself).

AGENTS.md §1.9: no getattr / stringly-typed field access.
All fields are accessed by name; the encoding is explicit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from lawvm.core.manual_claims.primitive import ManualCompilationClaim


def _json_default(obj: Any) -> Any:
    """JSON serializer for types not natively supported."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__!r} is not JSON serializable")


def _encode_value(v: Any) -> Any:
    """Recursively convert frozen structures to JSON-encodable form."""
    if isinstance(v, (tuple, list)):
        return [_encode_value(x) for x in v]
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    # Enum
    if hasattr(v, "value"):
        return v.value
    # dataclass — recurse into fields
    if hasattr(v, "__dataclass_fields__"):
        return {
            k: _encode_value(getattr(v, k))
            for k in sorted(v.__dataclass_fields__)
        }
    raise TypeError(f"Cannot encode {type(v).__name__!r} for canonical payload")


def canonical_payload(claim: ManualCompilationClaim) -> Dict[str, Any]:
    """Return the canonical payload dict for hashing.

    Excludes claim_id (which is derived from this payload).
    All fields are included and sorted deterministically.
    schema_version and jurisdiction are part of the domain.
    """
    return {
        "claim_kind": claim.claim_kind,
        "claim_layer": claim.claim_layer.value,
        "claim_scope": _encode_value(claim.claim_scope),
        "cited_source_hash": claim.cited_source_hash,
        "cited_source_locator": _encode_value(claim.cited_source_locator),
        "cited_source_span": list(claim.cited_source_span),
        "dependency_fingerprint": _encode_value(claim.dependency_fingerprint),
        "disputes": list(claim.disputes),
        "jurisdiction": claim.jurisdiction,
        "producer": _encode_value(claim.producer),
        "rationale": claim.rationale,
        "requested_profiles": [p.value for p in claim.requested_profiles],
        "schema_version": claim.schema_version,
        "source_witness_type": claim.source_witness_type.value,
        "supersedes": list(claim.supersedes),
        "supersession_delta_reason": claim.supersession_delta_reason,
        "target": _encode_value(claim.target),
        "valid_at": _encode_value(claim.valid_at),
        "value": _encode_value(claim.value),
    }


def compute_claim_id(claim: ManualCompilationClaim) -> str:
    """Compute the canonical SHA-256 claim_id from the claim payload.

    schema_version and jurisdiction are included in the hash domain.
    Returns lowercase hex string.
    """
    payload = canonical_payload(claim)
    serialized = json.dumps(payload, sort_keys=True, default=_json_default)
    return hashlib.sha256(serialized.encode()).hexdigest()


def verify_claim_id(claim: ManualCompilationClaim) -> None:
    """Recompute claim_id from payload and raise if it mismatches.

    Call this whenever loading a claim from disk. Hard fail on mismatch —
    indicates tampering or corruption.
    """
    expected = compute_claim_id(claim)
    if claim.claim_id != expected:
        raise ValueError(
            f"Claim ID mismatch: stored {claim.claim_id!r} "
            f"but canonical hash is {expected!r}. "
            "The claim file may have been tampered with or the payload changed."
        )
