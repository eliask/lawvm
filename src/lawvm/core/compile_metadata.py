"""CompileMetadata — reproducibility fingerprints for persisted compile artifacts.

Step 5 of the v3 provenance graph transition
(notes_internal/UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §6 + §13 + §14).

Every artifact persisted by a Step-5-aware emitter carries these fields so
consumers can verify the full (S, P, I, E, R, G_in) → (T, Q, G_out, C, K)
reproducibility contract.

Design rules
------------
* No Pydantic; frozen dataclasses + slots throughout.
* Timestamps come from the caller; no datetime.now() calls inside emitters.
* Fingerprints are sha256 over canonical deterministic serializations.
* to_metadata_dict / from_metadata_dict round-trip exactly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


# ---------------------------------------------------------------------------
# Canonical JSON helper (shared pattern with provenance_graph.py)
# ---------------------------------------------------------------------------


def _canonical_json_value(obj: object) -> object:
    """Recursively normalize an object for deterministic JSON encoding."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str, type(None))):
        return obj
    if isinstance(obj, (tuple, list)):
        return [_canonical_json_value(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _canonical_json_value(v) for k, v in sorted(obj.items())}
    # Fallback: convert to string for any unknown type
    return str(obj)


def _canonical_json(obj: object) -> str:
    return json.dumps(_canonical_json_value(obj), sort_keys=True, ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CompileMetadata
# ---------------------------------------------------------------------------


_REQUIRED_DICT_KEYS: frozenset[str] = frozenset({
    "lawvm.provenance_graph_hash",
    "lawvm.strict_profile_fingerprint",
    "lawvm.evidence_policy_fingerprint",
    "lawvm.source_bundle_hash",
    "lawvm.attestation_kind_registry_hash",
})


@dataclass(frozen=True, slots=True)
class CompileMetadata:
    """Reproducibility fingerprints for a persisted compile artifact.

    Per UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §6 + §14. Any artifact
    persisted by a Step-5-aware emitter carries these fields so consumers
    can verify (S, P, I, E, R, G_in) → (T, Q, G_out, C, K) reproducibility.

    Fingerprint fields
    ------------------
    provenance_graph_hash               sha256 of the ProvenanceGraph snapshot
    strict_profile_fingerprint          sha256(canonical(StrictProfile fields))
    evidence_policy_fingerprint         EvidencePolicyRegistry.registry_hash
    source_bundle_hash                  caller-provided; sha256 of source artifacts
    attestation_kind_registry_hash      from attestation_kind_registry_hash()

    Optional fields
    ---------------
    interpretation_policy_fingerprint   sha256 of the interpretation policy if used
    build_id                            opaque caller-provided build identifier
    build_timestamp                     caller-provided; NOT derived from the clock
                                        inside emitters (reproducibility contract)
    """

    provenance_graph_hash: str
    strict_profile_fingerprint: str
    evidence_policy_fingerprint: str
    source_bundle_hash: str
    attestation_kind_registry_hash: str
    interpretation_policy_fingerprint: Optional[str] = None
    build_id: str = ""
    build_timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.provenance_graph_hash:
            raise ValueError("CompileMetadata.provenance_graph_hash must be non-empty")
        if not self.strict_profile_fingerprint:
            raise ValueError("CompileMetadata.strict_profile_fingerprint must be non-empty")
        if not self.evidence_policy_fingerprint:
            raise ValueError("CompileMetadata.evidence_policy_fingerprint must be non-empty")
        if not self.source_bundle_hash:
            raise ValueError("CompileMetadata.source_bundle_hash must be non-empty")
        if not self.attestation_kind_registry_hash:
            raise ValueError("CompileMetadata.attestation_kind_registry_hash must be non-empty")
        if self.build_timestamp is not None and not isinstance(self.build_timestamp, datetime):
            raise TypeError("CompileMetadata.build_timestamp must be a datetime or None")

    def to_metadata_dict(self) -> dict[str, str]:
        """Render as flat dict suitable for parquet/duckdb metadata storage.

        All values are strings for cross-format portability. Optional fields
        are included only when set.
        """
        d: dict[str, str] = {
            "lawvm.provenance_graph_hash": self.provenance_graph_hash,
            "lawvm.strict_profile_fingerprint": self.strict_profile_fingerprint,
            "lawvm.evidence_policy_fingerprint": self.evidence_policy_fingerprint,
            "lawvm.source_bundle_hash": self.source_bundle_hash,
            "lawvm.attestation_kind_registry_hash": self.attestation_kind_registry_hash,
        }
        if self.interpretation_policy_fingerprint is not None:
            d["lawvm.interpretation_policy_fingerprint"] = self.interpretation_policy_fingerprint
        if self.build_id:
            d["lawvm.build_id"] = self.build_id
        if self.build_timestamp is not None:
            d["lawvm.build_timestamp"] = self.build_timestamp.isoformat()
        return d

    @classmethod
    def from_metadata_dict(cls, d: Mapping[str, str]) -> "CompileMetadata":
        """Round-trip from metadata dict; validates required fields are present.

        Raises ValueError if any required key is missing or empty.
        """
        missing = _REQUIRED_DICT_KEYS - set(d.keys())
        if missing:
            raise ValueError(
                f"CompileMetadata.from_metadata_dict: missing required keys: "
                f"{sorted(missing)!r}"
            )
        for key in _REQUIRED_DICT_KEYS:
            if not d[key]:
                raise ValueError(
                    f"CompileMetadata.from_metadata_dict: key {key!r} must be non-empty"
                )

        build_timestamp: Optional[datetime] = None
        raw_ts = d.get("lawvm.build_timestamp", "")
        if raw_ts:
            build_timestamp = datetime.fromisoformat(raw_ts)

        return cls(
            provenance_graph_hash=d["lawvm.provenance_graph_hash"],
            strict_profile_fingerprint=d["lawvm.strict_profile_fingerprint"],
            evidence_policy_fingerprint=d["lawvm.evidence_policy_fingerprint"],
            source_bundle_hash=d["lawvm.source_bundle_hash"],
            attestation_kind_registry_hash=d["lawvm.attestation_kind_registry_hash"],
            interpretation_policy_fingerprint=d.get("lawvm.interpretation_policy_fingerprint") or None,
            build_id=d.get("lawvm.build_id", ""),
            build_timestamp=build_timestamp,
        )


# ---------------------------------------------------------------------------
# StrictProfile fingerprint
# ---------------------------------------------------------------------------


def compute_strict_profile_fingerprint(profile: "StrictProfile") -> str:  # type: ignore[name-defined]  # noqa: F821  # ty: ignore[unresolved-reference]
    """Return sha256 over canonical serialization of all StrictProfile fields.

    Deterministic field ordering: sorted by field name. Same profile object
    on any process → identical fingerprint.
    """
    import dataclasses  # noqa: PLC0415

    fields_dict = {
        f.name: getattr(profile, f.name)
        for f in dataclasses.fields(profile)
    }
    return _sha256(_canonical_json(fields_dict))


# ---------------------------------------------------------------------------
# build_compile_metadata factory
# ---------------------------------------------------------------------------


def build_compile_metadata(
    *,
    graph: "ProvenanceGraph",  # type: ignore[name-defined]  # noqa: F821  # ty: ignore[unresolved-reference]
    profile: "StrictProfile",  # type: ignore[name-defined]  # noqa: F821  # ty: ignore[unresolved-reference]
    evidence_policy: "EvidencePolicyRegistry",  # type: ignore[name-defined]  # noqa: F821  # ty: ignore[unresolved-reference]
    source_bundle_hash: str,
    interpretation_policy_fingerprint: Optional[str] = None,
    build_id: str = "",
    build_timestamp: Optional[datetime] = None,
) -> CompileMetadata:
    """Construct CompileMetadata from a graph + profile + policy triple.

    Timestamps come from the caller. Do NOT pass datetime.now() from inside
    an emitter — that would break reproducibility (different clock reads for
    the same build inputs).

    Args:
        graph:                            The ProvenanceGraph produced by this build.
        profile:                          The StrictProfile used for this compile.
        evidence_policy:                  The EvidencePolicyRegistry used.
        source_bundle_hash:               Caller-provided hash of the source bundle.
        interpretation_policy_fingerprint: Optional interpretation policy hash.
        build_id:                         Opaque build identifier (e.g. CI run ID).
        build_timestamp:                  Caller-provided build timestamp or None.
    """
    from lawvm.core.provenance_graph import attestation_kind_registry_hash  # noqa: PLC0415

    return CompileMetadata(
        provenance_graph_hash=graph.snapshot_hash,
        strict_profile_fingerprint=compute_strict_profile_fingerprint(profile),
        evidence_policy_fingerprint=evidence_policy.registry_hash,
        source_bundle_hash=source_bundle_hash,
        attestation_kind_registry_hash=attestation_kind_registry_hash(),
        interpretation_policy_fingerprint=interpretation_policy_fingerprint,
        build_id=build_id,
        build_timestamp=build_timestamp,
    )
