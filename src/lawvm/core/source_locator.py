"""Shared source locator read model.

This module separates source-location footing from manual-claim storage. Legacy
manual claims keep their compact locator shape, while core/frontends can project
the same facts into a richer locator with document, structural, byte-span, and
quote-hash fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.provenance_graph import SourceRef


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """Locator for evidence within a source artifact."""

    jurisdiction: str
    artifact_kind: str
    source_id: str
    document_uri: str = ""
    structural_path: str = ""
    xpath: str = ""
    char_span: tuple[int, int] | None = None
    byte_span: tuple[int, int] | None = None
    quote_hash: str = ""
    normalization_policy: str = ""
    statute_id: str = ""
    proposal_id: str = ""
    version_id: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(self, "artifact_kind", _required_string("artifact_kind", self.artifact_kind))
        object.__setattr__(self, "source_id", _required_string("source_id", self.source_id))
        object.__setattr__(self, "document_uri", str(self.document_uri or ""))
        object.__setattr__(self, "structural_path", str(self.structural_path or ""))
        object.__setattr__(self, "xpath", str(self.xpath or ""))
        object.__setattr__(self, "char_span", _optional_span("char_span", self.char_span))
        object.__setattr__(self, "byte_span", _optional_span("byte_span", self.byte_span))
        object.__setattr__(self, "quote_hash", str(self.quote_hash or ""))
        object.__setattr__(self, "normalization_policy", str(self.normalization_policy or ""))
        object.__setattr__(self, "statute_id", str(self.statute_id or ""))
        object.__setattr__(self, "proposal_id", str(self.proposal_id or ""))
        object.__setattr__(self, "version_id", str(self.version_id or ""))
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceLocator.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": self.jurisdiction,
            "artifact_kind": self.artifact_kind,
            "source_id": self.source_id,
            "document_uri": self.document_uri,
            "structural_path": self.structural_path,
            "xpath": self.xpath,
            "char_span": list(self.char_span) if self.char_span is not None else None,
            "byte_span": list(self.byte_span) if self.byte_span is not None else None,
            "quote_hash": self.quote_hash,
            "normalization_policy": self.normalization_policy,
            "statute_id": self.statute_id,
            "proposal_id": self.proposal_id,
            "version_id": self.version_id,
            "detail": _plain_jsonable(self.detail),
        }
        return {key: value for key, value in payload.items() if value not in ("", None, {}, [])}


def source_locator_from_legacy_manual_locator(
    legacy: Mapping[str, Any],
    *,
    jurisdiction: str,
    document_uri: str = "",
    structural_path: str = "",
    xpath: str = "",
    char_span: tuple[int, int] | None = None,
    byte_span: tuple[int, int] | None = None,
    quote_hash: str = "",
    normalization_policy: str = "legacy_manual_claim_source_locator.v1",
) -> SourceLocator:
    """Project a legacy manual-claims source locator into the shared shape."""

    statute_id = str(legacy.get("statute_id") or "")
    proposal_id = str(legacy.get("he_id") or "")
    artifact_kind = str(legacy.get("artifact_kind") or "")
    version_id = str(legacy.get("version_id") or "")
    source_id = _source_id(
        artifact_kind=artifact_kind,
        statute_id=statute_id,
        proposal_id=proposal_id,
        document_uri=document_uri,
    )
    return SourceLocator(
        jurisdiction=jurisdiction,
        artifact_kind=artifact_kind,
        source_id=source_id,
        document_uri=document_uri,
        structural_path=structural_path,
        xpath=xpath,
        char_span=char_span,
        byte_span=byte_span,
        quote_hash=quote_hash,
        normalization_policy=normalization_policy,
        statute_id=statute_id,
        proposal_id=proposal_id,
        version_id=version_id,
        detail={"legacy_manual_claim_locator": dict(legacy)},
    )


def source_ref_from_locator(
    locator: SourceLocator,
    *,
    artifact_digest: str,
    bounded_quote_hash: str = "",
    byte_range: tuple[int, int] | None = None,
) -> SourceRef:
    """Convert a shared locator into provenance-graph ``SourceRef`` footing."""

    return SourceRef(
        artifact_digest=_required_string("artifact_digest", artifact_digest),
        structural_locator=_structural_locator(locator),
        bounded_quote_hash=bounded_quote_hash or locator.quote_hash,
        normalization_policy_id=locator.normalization_policy or "source_locator.v1",
        byte_range=byte_range or locator.byte_span or (0, 0),
    )


def _source_id(
    *,
    artifact_kind: str,
    statute_id: str,
    proposal_id: str,
    document_uri: str,
) -> str:
    for value in (document_uri, statute_id, proposal_id):
        if value:
            return f"{artifact_kind}:{value}"
    return artifact_kind


def _structural_locator(locator: SourceLocator) -> str:
    for value in (locator.structural_path, locator.xpath, locator.document_uri, locator.source_id):
        if value:
            return value
    return locator.source_id


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"SourceLocator.{field_name} is required")
    return text


def _optional_span(field_name: str, value: tuple[int, int] | None) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"SourceLocator.{field_name} must be a 2-tuple")
    start, end = value
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
        raise ValueError(f"SourceLocator.{field_name} must satisfy 0 <= start <= end")
    return (start, end)


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value


__all__ = [
    "SourceLocator",
    "source_locator_from_legacy_manual_locator",
    "source_ref_from_locator",
]
