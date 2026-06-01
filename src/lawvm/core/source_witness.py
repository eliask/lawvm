"""Shared source/digest witness projection contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


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


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
