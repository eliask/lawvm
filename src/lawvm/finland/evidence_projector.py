"""Project replay evidence into stable meta rows, findings, and proof rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class MetaProjection:
    """One deduped replay_meta channel projection."""

    meta_key: str
    rows: tuple[Mapping[str, Any], ...]
    dedup_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceProjectionRequest:
    """Unified evidence projection inputs for one replay evidence pass."""

    findings: tuple[object, ...] = ()
    meta_projections: tuple[MetaProjection, ...] = ()
    proof_rows: tuple[Mapping[str, Any], ...] = ()
    replay_meta_out: dict[str, object] | None = None
    finding_meta_key: str = "replay_finding_details"
    finding_detail_keys: tuple[str, ...] = ("kind", "rule_id", "phase")


@dataclass(frozen=True, slots=True)
class EvidenceProjectionResult:
    """Summary of one unified evidence projection pass."""

    meta_keys: tuple[str, ...]
    finding_row_count: int
    proof_row_count: int


def project_meta_rows(
    rows: list[Mapping[str, Any]],
    *,
    meta_key: str,
    replay_meta_out: dict[str, object] | None,
    dedup_keys: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Project rows into replay_meta_out, deduplicating by *dedup_keys*."""
    projected = [dict(row) for row in rows]
    if replay_meta_out is None:
        return projected

    existing = replay_meta_out.get(meta_key)
    if existing is None:
        replay_meta_out[meta_key] = projected
        return projected

    merged = list(existing) if isinstance(existing, list) else []
    seen = {
        tuple(str(row.get(key) or "") for key in dedup_keys)
        for row in merged
        if isinstance(row, dict)
    }
    for row in projected:
        if dedup_keys:
            key = tuple(str(row.get(field) or "") for field in dedup_keys)
            if key in seen:
                continue
            seen.add(key)
        merged.append(row)
    replay_meta_out[meta_key] = merged
    return projected


def project_finding_details(
    findings: list[object],
    *,
    meta_key: str,
    replay_meta_out: dict[str, object] | None,
    detail_keys: tuple[str, ...] = ("kind", "rule_id", "phase"),
) -> list[dict[str, object]]:
    """Project finding details into replay_meta_out without message duplication."""
    rows: list[dict[str, object]] = []
    for finding in findings:
        detail = getattr(finding, "detail", None) or {}
        if not isinstance(detail, dict):
            continue
        row = {key: detail.get(key) for key in detail_keys if key in detail}
        row["kind"] = getattr(finding, "kind", row.get("kind"))
        row["source_statute"] = getattr(finding, "source_statute", "")
        rows.append(row)
    return project_meta_rows(
        rows,
        meta_key=meta_key,
        replay_meta_out=replay_meta_out,
        dedup_keys=detail_keys,
    )


def project_evidence(request: EvidenceProjectionRequest) -> EvidenceProjectionResult:
    """Project findings, meta channels, and proof rows in one owned pass."""

    meta_keys: list[str] = []
    finding_row_count = 0
    proof_row_count = 0

    if request.replay_meta_out is not None:
        for projection in request.meta_projections:
            if not projection.rows:
                continue
            project_meta_rows(
                list(projection.rows),
                meta_key=projection.meta_key,
                replay_meta_out=request.replay_meta_out,
                dedup_keys=projection.dedup_keys,
            )
            meta_keys.append(projection.meta_key)

        if request.findings:
            rows = project_finding_details(
                list(request.findings),
                meta_key=request.finding_meta_key,
                replay_meta_out=request.replay_meta_out,
                detail_keys=request.finding_detail_keys,
            )
            finding_row_count = len(rows)
            if request.finding_meta_key not in meta_keys:
                meta_keys.append(request.finding_meta_key)

        if request.proof_rows:
            rows = project_meta_rows(
                list(request.proof_rows),
                meta_key="proof_rows",
                replay_meta_out=request.replay_meta_out,
                dedup_keys=("proof_id",),
            )
            proof_row_count = len(rows)
            if "proof_rows" not in meta_keys:
                meta_keys.append("proof_rows")

    return EvidenceProjectionResult(
        meta_keys=tuple(meta_keys),
        finding_row_count=finding_row_count,
        proof_row_count=proof_row_count,
    )


__all__ = [
    "EvidenceProjectionRequest",
    "EvidenceProjectionResult",
    "MetaProjection",
    "project_evidence",
    "project_finding_details",
    "project_meta_rows",
]
