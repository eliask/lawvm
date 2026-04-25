"""Read-model projections over compile finding ledgers.

This module owns reporting/storage-oriented helper views derived from the
finding ledger. It is intentionally separate from ``compile_result``, which
owns the semantic center (bundle, verdicts, strictness derivation).
"""

from __future__ import annotations

from math import inf
from typing import Any, Iterable

from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import (
    Finding,
    OBSERVATION_ROLE,
    OBLIGATION_ROLE,
    VIOLATION_ROLE,
)

_QUIRKS_OBS_KINDS: frozenset[str] = frozenset({
    "APPLY.LEGACY_DISPATCH_FALLBACK",
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
    "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
    "ELAB.CONTAINER_PRUNED_SHADOWED",
    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
    "ELAB.DROP_ITEM_REPLACES_MISSING",
    "ELAB.UNASSIGNED_SPARSE_SLOTS",
})

_SOURCE_COMPLETENESS_OBS_KINDS: frozenset[str] = frozenset({
    "ELAB.SOURCE_PATHOLOGY",
    "ELAB.MISSING_PAYLOAD_SURFACE",
})

_SOURCE_COMPLETENESS_OBL_KINDS: frozenset[str] = frozenset({
    "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
    "APPLY.SOURCE_CORRECTED_BY_PATCH",
    "APPLY.SOURCE_INCOMPLETE",
    "APPLY.SOURCE_PATHOLOGY_DETECTED",
})

_FINDING_ROLE_ORDER: dict[str, int] = {
    OBSERVATION_ROLE: 0,
    OBLIGATION_ROLE: 1,
    VIOLATION_ROLE: 2,
}


def source_pathology_rows_from_findings(
    findings: Iterable[Finding],
) -> tuple[dict[str, Any], ...]:
    """Project source-pathology finding details into a stable row summary."""

    rows_by_key: dict[tuple[str, str, str, str, str], tuple[int, dict[str, Any]]] = {}

    def _pathology_preference(kind: str) -> int:
        if kind == "APPLY.SOURCE_PATHOLOGY_DETECTED":
            return 3
        if kind == "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY":
            return 2
        if kind == "ELAB.SOURCE_PATHOLOGY":
            return 1
        return 0

    def _projected_kind(finding: Finding) -> str:
        if finding.kind == "RUNTIME.VIOLATION":
            barrier_code = str(finding.detail.get("barrier_code") or "").strip()
            if barrier_code:
                return barrier_code
        return str(finding.kind or "").strip()

    for finding in findings:
        kind = str(finding.kind or "").strip()
        projected_kind = _projected_kind(finding)
        detail = dict(finding.detail)
        nested_detail = detail.get("detail")
        pathology_detail = dict(nested_detail) if isinstance(nested_detail, dict) else {}
        code = str(detail.get("code") or pathology_detail.get("code") or "").strip()
        if not code and kind not in {
            "ELAB.SOURCE_PATHOLOGY",
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            "APPLY.SOURCE_PATHOLOGY_DETECTED",
        }:
            continue
        source_statute = str(finding.source_statute or detail.get("source_statute") or "").strip()
        message = str(detail.get("message") or "").strip()
        target_unit_kind = str(detail.get("target_unit_kind") or "").strip()
        target_label = str(detail.get("target_label") or "").strip()
        key = (
            code,
            source_statute,
            target_unit_kind,
            target_label,
            repr(pathology_detail),
        )
        row = {
            "code": code,
            "message": message,
            "source_statute": source_statute,
            "target_unit_kind": target_unit_kind,
            "target_label": target_label,
            "detail": pathology_detail,
        }
        preference = _pathology_preference(projected_kind)
        current = rows_by_key.get(key)
        if current is None or preference > current[0]:
            rows_by_key[key] = (preference, row)
    return tuple(
        sorted(
            (row for _, row in rows_by_key.values()),
            key=lambda row: (
                str(row.get("code") or ""),
                str(row.get("source_statute") or ""),
                str(row.get("target_unit_kind") or ""),
                str(row.get("target_label") or ""),
                repr(row.get("detail") or {}),
            ),
        )
    )


def projection_rows_from_findings(findings: Iterable[Finding]) -> tuple[dict[str, Any], ...]:
    """Project findings into the runtime row summary shape."""

    def _projected_kind(finding: Finding) -> str:
        if finding.kind == "RUNTIME.VIOLATION":
            barrier_code = str(finding.detail.get("barrier_code") or "").strip()
            spec = get_finding_spec(barrier_code)
            if barrier_code and spec is not None and spec.role in ("barrier", "violation", "obligation"):
                return barrier_code
        return str(finding.kind or "unknown")

    rows: list[dict[str, Any]] = []
    for finding in findings:
        detail = dict(finding.detail)
        source = str(detail.get("source_statute") or finding.source_statute or "")
        rows.append(
            {
                "role": finding.role,
                "kind": _projected_kind(finding),
                "message": str(detail.get("message") or ""),
                "source": source,
                "detail": detail,
                "blocking": bool(finding.blocking),
            }
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                _FINDING_ROLE_ORDER.get(str(row.get("role", "") or ""), inf),
                str(row.get("kind", "") or ""),
                str(row.get("source", "") or ""),
                bool(row.get("blocking", False)),
                repr(row.get("detail", {})),
            ),
        )
    )


def quirks_used_from_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Return finding-ledger entries that indicate quirks-mode recovery."""
    return tuple(
        finding
        for finding in findings
        if finding.kind in _QUIRKS_OBS_KINDS
    )


def source_completeness_issues_from_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Return finding-ledger entries related to source completeness."""

    def _wrapped_kind(finding: Finding) -> str:
        if str(finding.kind or "") != "RUNTIME.VIOLATION":
            return ""
        barrier_code = str(finding.detail.get("barrier_code") or "").strip()
        spec = get_finding_spec(barrier_code)
        if barrier_code and spec is not None and spec.role in ("barrier", "violation", "obligation"):
            return barrier_code
        return ""

    return tuple(
        finding
        for finding in findings
        if finding.kind in _SOURCE_COMPLETENESS_OBS_KINDS
        or finding.kind in _SOURCE_COMPLETENESS_OBL_KINDS
        or _wrapped_kind(finding) in _SOURCE_COMPLETENESS_OBL_KINDS
    )
