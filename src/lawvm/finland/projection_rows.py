"""Finland compatibility projection from findings to row read models."""

from __future__ import annotations

from collections.abc import Sequence

from lawvm.core.phase_result import Finding


def projection_row_from_finding(finding: Finding) -> dict[str, object]:
    """Project a finding into the preferred compatibility read-model row."""
    detail = dict(finding.detail)
    source = str(detail.get("source_statute") or finding.source_statute or "")
    return {
        "role": finding.role,
        "kind": str(finding.kind or "unknown"),
        "message": str(detail.get("message") or ""),
        "source": source,
        "detail": detail,
        "blocking": bool(finding.blocking),
    }


def projection_rows(findings: Sequence[Finding]) -> tuple[dict[str, object], ...]:
    """Project Finland-owned findings into compatibility read-model rows."""
    return tuple(projection_row_from_finding(finding) for finding in findings)
