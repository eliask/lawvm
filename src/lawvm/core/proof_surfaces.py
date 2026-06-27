"""Shared proof-surface read model.

``EvidenceSurfaceReport`` declares what a report claims. ``ProofSurface`` is a
queryable row relation over proof/evidence/frontier/residual objects. It is a
read model only: it does not authorize replay and does not replace phase-local
compiler decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True, slots=True)
class ProofSurfaceRow:
    """One queryable proof-surface row."""

    row_id: str
    subject_id: str
    row_kind: str
    proof_status: str
    source_refs: tuple[str, ...] = ()
    witness_refs: tuple[str, ...] = ()
    assertion_refs: tuple[str, ...] = ()
    proof_refs: tuple[str, ...] = ()
    authorization_ref: str = ""
    residual_refs: tuple[str, ...] = ()
    frontier_ref: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "row_id", _required_string("row_id", self.row_id))
        object.__setattr__(self, "subject_id", _required_string("subject_id", self.subject_id))
        object.__setattr__(self, "row_kind", _required_string("row_kind", self.row_kind))
        object.__setattr__(self, "proof_status", _required_string("proof_status", self.proof_status))
        object.__setattr__(self, "source_refs", _string_tuple(self.source_refs))
        object.__setattr__(self, "witness_refs", _string_tuple(self.witness_refs))
        object.__setattr__(self, "assertion_refs", _string_tuple(self.assertion_refs))
        object.__setattr__(self, "proof_refs", _string_tuple(self.proof_refs))
        object.__setattr__(self, "authorization_ref", str(self.authorization_ref or ""))
        object.__setattr__(self, "residual_refs", _string_tuple(self.residual_refs))
        object.__setattr__(self, "frontier_ref", str(self.frontier_ref or ""))
        if not isinstance(self.detail, Mapping):
            raise ValueError("ProofSurfaceRow.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "row_id": self.row_id,
            "subject_id": self.subject_id,
            "row_kind": self.row_kind,
            "proof_status": self.proof_status,
            "source_refs": list(self.source_refs),
            "witness_refs": list(self.witness_refs),
            "assertion_refs": list(self.assertion_refs),
            "proof_refs": list(self.proof_refs),
            "residual_refs": list(self.residual_refs),
            "detail": _plain_jsonable(self.detail),
        }
        if self.authorization_ref:
            payload["authorization_ref"] = self.authorization_ref
        if self.frontier_ref:
            payload["frontier_ref"] = self.frontier_ref
        return payload


@dataclass(frozen=True, slots=True)
class ProofSurface:
    """Typed queryable surface over proof rows."""

    surface_id: str
    surface_kind: str
    jurisdiction: str
    source_bundle_hash: str = ""
    profile_id: str = ""
    graph_snapshot_hash: str = ""
    generated_at: str = ""
    claim_flags: Mapping[str, bool] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    rows: tuple[ProofSurfaceRow, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_string("surface_id", self.surface_id))
        object.__setattr__(
            self,
            "surface_kind",
            _required_string("surface_kind", self.surface_kind),
        )
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("jurisdiction", self.jurisdiction),
        )
        object.__setattr__(self, "source_bundle_hash", str(self.source_bundle_hash or ""))
        object.__setattr__(self, "profile_id", str(self.profile_id or ""))
        object.__setattr__(self, "graph_snapshot_hash", str(self.graph_snapshot_hash or ""))
        object.__setattr__(self, "generated_at", str(self.generated_at or ""))
        if not isinstance(self.claim_flags, Mapping):
            raise ValueError("ProofSurface.claim_flags must be a mapping")
        object.__setattr__(self, "claim_flags", freeze_mapping(_claim_flags(self.claim_flags)))
        if not isinstance(self.summary, Mapping):
            raise ValueError("ProofSurface.summary must be a mapping")
        rows = tuple(self.rows)
        if not all(isinstance(row, ProofSurfaceRow) for row in rows):
            raise ValueError("ProofSurface.rows must contain ProofSurfaceRow objects")
        duplicate_row_ids = _duplicate_values(row.row_id for row in rows)
        if duplicate_row_ids:
            duplicates = ", ".join(duplicate_row_ids)
            raise ValueError(f"ProofSurface.rows must have unique row_id values: {duplicates}")
        object.__setattr__(self, "summary", freeze_mapping(self.summary))
        object.__setattr__(self, "rows", rows)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "surface_id": self.surface_id,
            "surface_kind": self.surface_kind,
            "jurisdiction": self.jurisdiction,
            "source_bundle_hash": self.source_bundle_hash,
            "profile_id": self.profile_id,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "generated_at": self.generated_at,
            "claim_flags": _plain_jsonable(self.claim_flags),
            "summary": _plain_jsonable(self.summary),
            "rows": [row.to_dict() for row in self.rows],
        }
        return {key: value for key, value in payload.items() if value not in ("", None)}


def proof_surface_from_evidence_report(
    report: EvidenceSurfaceReport | Mapping[str, Any],
    *,
    surface_id: str = "",
    surface_kind: str = "",
    source_bundle_hash: str = "",
    profile_id: str = "",
    graph_snapshot_hash: str = "",
    generated_at: str = "",
) -> ProofSurface:
    """Build a queryable proof surface from an evidence report envelope."""
    data = report.to_dict() if isinstance(report, EvidenceSurfaceReport) else dict(report)
    rows = tuple(
        _proof_surface_row_from_mapping(
            row,
            report_kind=str(data.get("report_kind") or "evidence_surface_report"),
            index=index,
        )
        for index, row in enumerate(_mapping_rows(data.get("rows")), start=1)
    )
    return ProofSurface(
        surface_id=surface_id or _default_surface_id(data),
        surface_kind=surface_kind or str(data.get("report_kind") or "evidence_surface_report"),
        jurisdiction=str(data.get("jurisdiction") or ""),
        source_bundle_hash=source_bundle_hash,
        profile_id=profile_id or str(_mapping(data.get("filters")).get("profile") or ""),
        graph_snapshot_hash=graph_snapshot_hash,
        generated_at=generated_at,
        claim_flags=_report_claim_flags(data),
        summary=_mapping(data.get("summary")),
        rows=rows,
    )


def _proof_surface_row_from_mapping(
    row: Mapping[str, Any],
    *,
    report_kind: str,
    index: int,
) -> ProofSurfaceRow:
    row_kind = str(row.get("surface") or row.get("row_kind") or report_kind)
    row_id = _first_present(
        row,
        (
            "row_id",
            "proof_id",
            "work_item_id",
            "residual_id",
        ),
    ) or _candidate_set_scoped_row_id(row) or _first_present(
        row,
        (
            "authorization_rule_id",
            "candidate_set_kind",
        ),
    ) or _stable_row_id(report_kind, index, row)
    return ProofSurfaceRow(
        row_id=row_id,
        subject_id=_row_subject_id(row, row_id=row_id),
        row_kind=row_kind,
        proof_status=_row_status(row),
        source_refs=_source_refs(row),
        witness_refs=_refs(row, ("witness_ref", "witness_refs")),
        assertion_refs=_refs(row, ("assertion_ref", "assertion_refs", "assertion_id")),
        proof_refs=_refs(row, ("proof_ref", "proof_refs", "proof_id")),
        authorization_ref=str(
            row.get("authorization_ref")
            or row.get("authorization_rule_id")
            or row.get("policy_id")
            or ""
        ),
        residual_refs=_refs(row, ("residual_ref", "residual_refs", "residual_id")),
        frontier_ref=str(row.get("frontier_ref") or row.get("work_item_id") or ""),
        detail=row,
    )


def _row_subject_id(row: Mapping[str, Any], *, row_id: str) -> str:
    return _first_present(
        row,
        (
            "subject_id",
            "source_artifact_id",
            "statute_id",
            "source_statute",
            "artifact_id",
            "source",
        ),
    ) or row_id


def _row_status(row: Mapping[str, Any]) -> str:
    return _first_present(
        row,
        (
            "row_status",
            "status",
            "boundary_proof_status",
            "proof_status",
            "projection_status",
            "binding_status",
            "elaboration_status",
            "acquisition_status",
            "attestation_status",
            "admission_status",
            "evidence_status",
            "phase_status",
            "capability_status",
            "parse_status",
            "closure_status",
            "authorization_status",
            "frontier_status",
            "completeness_status",
            "strict_disposition",
        ),
    ) or "reported"


def _candidate_set_scoped_row_id(row: Mapping[str, Any]) -> str:
    candidate_set_kind = str(row.get("candidate_set_kind") or "")
    scope_id = str(row.get("scope_id") or "")
    if not candidate_set_kind or not scope_id:
        return ""
    completeness_status = str(row.get("completeness_status") or "")
    digest_payload = {
        "candidate_set_kind": candidate_set_kind,
        "scope_id": scope_id,
        "completeness_status": completeness_status,
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"candidate-set:{digest}"


def _source_refs(row: Mapping[str, Any]) -> tuple[str, ...]:
    refs = list(_refs(row, ("source_ref", "source_refs", "source", "source_statute")))
    if _is_source_witness_mapping(row):
        refs.extend(_source_witness_refs(row))
    for witness in _nested_source_witnesses(row):
        refs.extend(_source_witness_refs(witness))
    return tuple(dict.fromkeys(refs))


def _nested_source_witnesses(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return nested source witnesses carried by common report-edge row shapes."""

    witnesses: list[Mapping[str, Any]] = []
    for key, value in row.items():
        if key.endswith("source_witness") and isinstance(value, Mapping):
            witnesses.append(value)
    source_witnesses = row.get("source_witnesses")
    if isinstance(source_witnesses, (list, tuple)):
        witnesses.extend(item for item in source_witnesses if isinstance(item, Mapping))
    return tuple(witnesses)


def _is_source_witness_mapping(row: Mapping[str, Any]) -> bool:
    """Identify rows that are already flattened ``SourceWitness`` mappings."""

    if row.get("source_role"):
        return True
    surface = str(row.get("surface") or row.get("row_kind") or "")
    return surface.endswith("source_witness") or surface == "source_witness"


def _source_witness_refs(witness: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("artifact_id", "source_unit_id", "locator", "source_path"):
        value = str(witness.get(key) or "")
        if value:
            refs.append(value)
    return tuple(dict.fromkeys(refs))


def _refs(row: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, str):
            if value:
                refs.append(value)
        elif isinstance(value, (list, tuple)):
            refs.extend(str(item) for item in value if str(item))
    return tuple(dict.fromkeys(refs))


def _first_present(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _default_surface_id(data: Mapping[str, Any]) -> str:
    jurisdiction = str(data.get("jurisdiction") or "unknown")
    report_kind = str(data.get("report_kind") or "evidence_surface_report")
    filters = _mapping(data.get("filters"))
    filter_suffix = json.dumps(filters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(filter_suffix.encode("utf-8")).hexdigest()[:16]
    return f"{jurisdiction}:{report_kind}:{digest}"


def _stable_row_id(report_kind: str, index: int, row: Mapping[str, Any]) -> str:
    payload = json.dumps(_plain_jsonable(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{report_kind}:row:{index}:{digest}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _report_claim_flags(data: Mapping[str, Any]) -> Mapping[str, bool]:
    return {
        "replay_claims": bool(data.get("replay_claims")),
        "canonical_effect_claims": bool(data.get("canonical_effect_claims")),
        "candidate_effect_claims": bool(data.get("candidate_effect_claims")),
        "dry_run_claims": bool(data.get("dry_run_claims")),
        "agreement_claims": bool(data.get("agreement_claims")),
    }


def _claim_flags(values: Mapping[str, Any]) -> Mapping[str, bool]:
    return {
        "replay_claims": bool(values.get("replay_claims")),
        "canonical_effect_claims": bool(values.get("canonical_effect_claims")),
        "candidate_effect_claims": bool(values.get("candidate_effect_claims")),
        "dry_run_claims": bool(values.get("dry_run_claims")),
        "agreement_claims": bool(values.get("agreement_claims")),
    }


def _duplicate_values(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        text = str(value)
        if text in seen:
            duplicates.add(text)
        seen.add(text)
    return tuple(sorted(duplicates))


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"ProofSurface.{field_name} is required")
    return text


def _string_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError("proof surface ref fields must be tuples")
    return tuple(str(value) for value in values if str(value))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
