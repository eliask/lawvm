"""Consumer-side verification of CompileMetadata on persisted artifacts.

Step 5 of the v3 provenance graph transition.

Usage pattern
-------------
Read artifact metadata (parquet schema metadata, duckdb lawvm_meta row,
JSON lawvm_metadata key), then call verify_artifact_metadata to confirm:
  - required keys are present
  - fingerprints match expected values (when provided)
  - the provenance graph snapshot exists in storage (when GraphStore provided)

Design rules
------------
* Pure function: no mutations, no side effects.
* Returns a typed result; callers decide whether to raise or warn.
* Errors are accumulated (not short-circuited) so the caller gets the full picture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, TYPE_CHECKING

from lawvm.core.compile_metadata import CompileMetadata

if TYPE_CHECKING:
    from lawvm.core.provenance_graph_storage import GraphStore


@dataclass(frozen=True, slots=True)
class CompileMetadataVerification:
    """Result of verifying a persisted artifact's CompileMetadata.

    Attributes
    ----------
    has_metadata
        True if the artifact carried any CompileMetadata fields.
    metadata
        Parsed CompileMetadata if parsing succeeded; None otherwise.
    graph_snapshot_exists
        True if the graph_store confirmed the snapshot exists.
        None if no graph_store was provided.
    matches_expected_profile
        True if the artifact's strict_profile_fingerprint matches the expected
        value. None if no expected value was provided.
    matches_expected_policy
        True if the artifact's evidence_policy_fingerprint matches the expected
        value. None if no expected value was provided.
    errors
        Accumulated error descriptions. Empty tuple = no errors.
    """

    has_metadata: bool
    metadata: Optional[CompileMetadata]
    graph_snapshot_exists: Optional[bool]
    matches_expected_profile: Optional[bool]
    matches_expected_policy: Optional[bool]
    errors: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        """True when no errors were accumulated."""
        return len(self.errors) == 0


def verify_artifact_metadata(
    metadata: Mapping[str, str],
    *,
    graph_store: "Optional[GraphStore]" = None,
    expected_strict_profile_fingerprint: Optional[str] = None,
    expected_evidence_policy_fingerprint: Optional[str] = None,
) -> CompileMetadataVerification:
    """Verify a persisted artifact's metadata fields.

    If graph_store is provided, additionally verify that
    metadata['lawvm.provenance_graph_hash'] resolves to an actual stored
    snapshot.

    Args:
        metadata:
            Flat string→string dict read from the artifact (parquet schema
            metadata, duckdb lawvm_meta row, JSON lawvm_metadata key, etc.).
        graph_store:
            Optional GraphStore to confirm snapshot existence.
        expected_strict_profile_fingerprint:
            When provided, the artifact's fingerprint must equal this value.
        expected_evidence_policy_fingerprint:
            When provided, the artifact's fingerprint must equal this value.

    Returns:
        CompileMetadataVerification with all findings accumulated.
    """
    errors: list[str] = []

    # Check whether any lawvm.* keys are present at all
    has_metadata = any(k.startswith("lawvm.") for k in metadata)

    if not has_metadata:
        return CompileMetadataVerification(
            has_metadata=False,
            metadata=None,
            graph_snapshot_exists=None,
            matches_expected_profile=None,
            matches_expected_policy=None,
            errors=("artifact carries no lawvm.* metadata keys",),
        )

    # Parse into CompileMetadata
    parsed: Optional[CompileMetadata] = None
    try:
        parsed = CompileMetadata.from_metadata_dict(metadata)
    except (ValueError, TypeError) as exc:
        errors.append(f"CompileMetadata parse error: {exc}")
        return CompileMetadataVerification(
            has_metadata=True,
            metadata=None,
            graph_snapshot_exists=None,
            matches_expected_profile=None,
            matches_expected_policy=None,
            errors=tuple(errors),
        )

    # Graph snapshot existence check
    graph_snapshot_exists: Optional[bool] = None
    if graph_store is not None and parsed is not None:
        graph_snapshot_exists = graph_store.snapshot_exists(parsed.provenance_graph_hash)
        if not graph_snapshot_exists:
            errors.append(
                f"provenance graph snapshot {parsed.provenance_graph_hash!r} "
                "not found in graph_store"
            )

    # Profile fingerprint check
    matches_expected_profile: Optional[bool] = None
    if expected_strict_profile_fingerprint is not None and parsed is not None:
        matches_expected_profile = (
            parsed.strict_profile_fingerprint == expected_strict_profile_fingerprint
        )
        if not matches_expected_profile:
            errors.append(
                f"strict_profile_fingerprint mismatch: artifact has "
                f"{parsed.strict_profile_fingerprint!r}, expected "
                f"{expected_strict_profile_fingerprint!r}"
            )

    # Evidence policy fingerprint check
    matches_expected_policy: Optional[bool] = None
    if expected_evidence_policy_fingerprint is not None and parsed is not None:
        matches_expected_policy = (
            parsed.evidence_policy_fingerprint == expected_evidence_policy_fingerprint
        )
        if not matches_expected_policy:
            errors.append(
                f"evidence_policy_fingerprint mismatch: artifact has "
                f"{parsed.evidence_policy_fingerprint!r}, expected "
                f"{expected_evidence_policy_fingerprint!r}"
            )

    return CompileMetadataVerification(
        has_metadata=True,
        metadata=parsed,
        graph_snapshot_exists=graph_snapshot_exists,
        matches_expected_profile=matches_expected_profile,
        matches_expected_policy=matches_expected_policy,
        errors=tuple(errors),
    )
