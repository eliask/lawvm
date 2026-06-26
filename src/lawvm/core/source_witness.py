"""Shared source/digest witness projection contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from collections.abc import Iterable as IterableABC
from typing import Any, Iterable, Mapping, Sequence

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


_SOURCE_WITNESS_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "source_witness_as_replay_authorization",
    "digest_match_as_legal_meaning_proof",
    "preview_digest_as_full_artifact_digest",
)


@dataclass(frozen=True, slots=True)
class DigestWitness:
    """Digest identity for a bounded source artifact or preview."""

    digest_algorithm: str
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "digest_algorithm", str(self.digest_algorithm or ""))
        object.__setattr__(self, "digest", str(self.digest or ""))
        if not self.digest_algorithm:
            raise ValueError("DigestWitness.digest_algorithm is required")
        if not self.digest:
            raise ValueError("DigestWitness.digest is required")

    def to_dict(self) -> dict[str, str]:
        return {
            "digest_algorithm": self.digest_algorithm,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class SourceWitness:
    """Typed source footing for non-executable evidence and proof packets."""

    source_role: str
    artifact_id: str = ""
    source_unit_id: str = ""
    locator: str = ""
    version_id: str = ""
    source_path: str = ""
    digest: DigestWitness | None = None
    bounded_preview: str = ""
    preview_digest: DigestWitness | None = None
    source_lane: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_role", str(self.source_role or ""))
        object.__setattr__(self, "artifact_id", str(self.artifact_id or ""))
        object.__setattr__(self, "source_unit_id", str(self.source_unit_id or ""))
        object.__setattr__(self, "locator", str(self.locator or ""))
        object.__setattr__(self, "version_id", str(self.version_id or ""))
        object.__setattr__(self, "source_path", str(self.source_path or ""))
        object.__setattr__(self, "bounded_preview", str(self.bounded_preview or ""))
        object.__setattr__(self, "source_lane", str(self.source_lane or ""))
        if not self.source_role:
            raise ValueError("SourceWitness.source_role is required")
        if self.digest is not None and not isinstance(self.digest, DigestWitness):
            raise ValueError("SourceWitness.digest must be a DigestWitness")
        if self.preview_digest is not None and not isinstance(
            self.preview_digest, DigestWitness
        ):
            raise ValueError("SourceWitness.preview_digest must be a DigestWitness")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("SourceWitness.metadata must be a mapping")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload = _plain_jsonable(self.metadata)
        payload.update(
            {
                "source_role": self.source_role,
                "artifact_id": self.artifact_id,
                "source_unit_id": self.source_unit_id,
                "locator": self.locator,
                "version_id": self.version_id,
                "source_path": self.source_path,
                "bounded_preview": self.bounded_preview,
                "source_lane": self.source_lane,
            }
        )
        if self.digest is not None:
            payload["digest_witness"] = self.digest.to_dict()
            payload["digest_algorithm"] = self.digest.digest_algorithm
            payload["digest"] = self.digest.digest
        if self.preview_digest is not None:
            payload["preview_digest_witness"] = self.preview_digest.to_dict()
            payload["preview_digest_algorithm"] = self.preview_digest.digest_algorithm
            payload["preview_digest"] = self.preview_digest.digest
        return {key: value for key, value in payload.items() if value not in ("", None)}


def source_witness_from_mapping(
    row: Mapping[str, Any],
    *,
    default_role: str,
    default_artifact_id: str = "",
    default_source_unit_id: str = "",
) -> SourceWitness:
    """Normalize a legacy source-witness mapping without discarding wire fields."""

    digest = _digest_witness(row)
    preview = str(row.get("text_preview") or row.get("bounded_preview") or "")
    preview_digest = _preview_digest_witness(row, preview=preview)
    return SourceWitness(
        source_role=str(row.get("source_role") or default_role),
        artifact_id=str(
            row.get("artifact_id")
            or row.get("affecting_act_id")
            or default_artifact_id
        ),
        source_unit_id=str(
            row.get("source_unit_id")
            or row.get("affecting_provisions")
            or default_source_unit_id
        ),
        locator=str(row.get("locator") or row.get("source_url") or ""),
        version_id=str(row.get("version_id") or ""),
        source_path=str(row.get("source_path") or ""),
        digest=digest,
        bounded_preview=preview,
        preview_digest=preview_digest,
        source_lane=str(row.get("source_lane") or row.get("source_status") or ""),
        metadata=row,
    )


def source_witness_role_key(row: Mapping[str, Any]) -> str:
    """Return a stable reporting key for a source witness role."""

    if not row:
        return "__missing__"
    return str(row.get("source_role") or "unknown")


def source_witness_digest_coverage(row: Mapping[str, Any]) -> str:
    """Classify whether a source witness carries artifact and/or preview identity."""

    if not row:
        return "missing_source_witness"
    has_digest = bool(_mapping_digest(row, ("digest", "source_sha256"), "digest_witness"))
    has_preview_digest = bool(
        _mapping_digest(
            row,
            ("preview_digest", "text_preview_sha256"),
            "preview_digest_witness",
        )
    )
    if has_digest and has_preview_digest:
        return "artifact_and_preview_digest"
    if has_digest:
        return "artifact_digest"
    if has_preview_digest:
        return "preview_digest"
    return "missing_digest"


def source_witness_digest_coverage_counts(
    witnesses: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Count digest coverage classes for direct source-witness mappings."""
    counts: dict[str, int] = {}
    for witness in witnesses:
        coverage = source_witness_digest_coverage(witness)
        counts[coverage] = counts.get(coverage, 0) + 1
    return dict(sorted(counts.items()))


def nested_source_witness_digest_coverage_counts(
    rows: Iterable[Mapping[str, Any]],
    *,
    field: str = "source_witness",
) -> dict[str, int]:
    """Count digest coverage classes for rows that carry a nested witness."""
    counts: dict[str, int] = {}
    for row in rows:
        witness = row.get(field)
        coverage = (
            source_witness_digest_coverage(witness)
            if isinstance(witness, Mapping)
            else "missing_source_witness"
        )
        counts[coverage] = counts.get(coverage, 0) + 1
    return dict(sorted(counts.items()))


def source_witness_evidence_report(
    witnesses: SourceWitness | Mapping[str, Any] | Sequence[SourceWitness | Mapping[str, Any]],
    *,
    jurisdiction: str,
    report_kind: str = "source_witness",
    default_role: str = "source_witness",
) -> EvidenceSurfaceReport:
    """Project source witnesses into a shared passive report envelope."""

    rows = tuple(
        _source_witness_mapping(row, default_role=default_role)
        for row in _witness_sequence(witnesses)
    )
    report_rows = tuple(
        _source_witness_report_row(row, index=index)
        for index, row in enumerate(rows, start=1)
    )
    summary = {
        "source_witness_count": len(rows),
        "source_role_counts": _counts(str(row.get("source_role") or "") for row in rows),
        "source_lane_counts": _counts(
            str(row.get("source_lane") or "__blank__") for row in rows
        ),
        "digest_coverage_counts": source_witness_digest_coverage_counts(rows),
        "artifact_count": len(
            {
                str(row.get("artifact_id") or "")
                for row in rows
                if row.get("artifact_id")
            }
        ),
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.source_witness_report.v1",
        truth_claim="passive source witness identity and digest projections",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=report_rows,
        detail={
            "safe_default": "treat_source_witnesses_as_footing_not_replay_authority",
            "forbidden_shortcuts": _SOURCE_WITNESS_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("source_witness",),
        },
    )


def _witness_sequence(value: Any) -> tuple[SourceWitness | Mapping[str, Any], ...]:
    if isinstance(value, SourceWitness) or isinstance(value, Mapping):
        return (value,)
    if isinstance(value, str | bytes):
        raise ValueError("source witness report requires witness mappings")
    if not isinstance(value, IterableABC):
        raise ValueError("source witness report requires witness mappings")
    items = tuple(value)
    if not all(isinstance(item, SourceWitness) or isinstance(item, Mapping) for item in items):
        raise ValueError("source witness report requires witness mappings")
    return items


def _source_witness_mapping(
    value: SourceWitness | Mapping[str, Any],
    *,
    default_role: str,
) -> Mapping[str, Any]:
    if isinstance(value, SourceWitness):
        return value.to_dict()
    return source_witness_from_mapping(value, default_role=default_role).to_dict()


def _source_witness_report_row(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    row_id = _source_witness_row_id(row, index=index)
    return {
        **dict(row),
        "surface": "source_witness",
        "row_id": row_id,
        "subject_id": str(row.get("artifact_id") or row_id),
        "row_status": source_witness_digest_coverage(row),
        "witness_ref": row_id,
        "forbidden_shortcuts": _SOURCE_WITNESS_REPORT_FORBIDDEN_SHORTCUTS,
    }


def _source_witness_row_id(row: Mapping[str, Any], *, index: int) -> str:
    parts = [
        str(row.get("source_role") or "source_witness"),
        str(row.get("artifact_id") or "unknown_artifact"),
        str(row.get("source_unit_id") or index),
    ]
    digest = _mapping_digest(row, ("digest", "source_sha256"), "digest_witness")
    identity_tail = digest or _source_witness_fallback_identity(row)
    if identity_tail:
        parts.append(identity_tail[:12])
    return ":".join(part.replace(":", "_").replace("/", "_") for part in parts if part)


def _source_witness_fallback_identity(row: Mapping[str, Any]) -> str:
    preview_digest = _mapping_digest(
        row,
        ("preview_digest", "text_preview_sha256"),
        "preview_digest_witness",
    )
    if preview_digest:
        return preview_digest
    identity_parts = tuple(
        str(row.get(key) or "")
        for key in (
            "locator",
            "source_path",
            "version_id",
            "source_lane",
            "bounded_preview",
        )
    )
    if any(identity_parts):
        return hashlib.sha256("|".join(identity_parts).encode("utf-8")).hexdigest()
    return ""


def _digest_witness(row: Mapping[str, Any]) -> DigestWitness | None:
    digest = str(row.get("digest") or row.get("source_sha256") or "")
    if not digest:
        return None
    algorithm = str(row.get("digest_algorithm") or "sha256")
    return DigestWitness(digest_algorithm=algorithm, digest=digest)


def _preview_digest_witness(
    row: Mapping[str, Any],
    *,
    preview: str,
) -> DigestWitness | None:
    digest = str(row.get("preview_digest") or row.get("text_preview_sha256") or "")
    if not digest and preview:
        digest = hashlib.sha256(preview.encode("utf-8")).hexdigest()
    if not digest:
        return None
    algorithm = str(row.get("preview_digest_algorithm") or "sha256")
    return DigestWitness(digest_algorithm=algorithm, digest=digest)


def _mapping_digest(
    row: Mapping[str, Any],
    flat_fields: tuple[str, ...],
    witness_field: str,
) -> str:
    for flat_field in flat_fields:
        digest = str(row.get(flat_field) or "")
        if digest:
            return digest
    witness = row.get(witness_field)
    if isinstance(witness, Mapping):
        return str(witness.get("digest") or "")
    return ""


def _counts(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
