"""Blocked P5 runtime scaffold for new jurisdiction frontends.

This module is intentionally small and non-executing. It gives a starter
frontend a machine-readable P5 artifact before clause parsing exists, while
making every row an explicit non-claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


P5_BLOCKED_STATUS = "blocked"
P5_BLOCKED_RULE_FAMILY = "blocked_clause_surface"
P5_BLOCKED_RULE_SUFFIX = "clause_surface.blocked_runtime_scaffold.v1"
P5_BLOCKED_REASON = "P5 clause surface runtime scaffold is present, but this frontend has not proved clause lowering."


@dataclass(frozen=True)
class StarterP5SourceUnit:
    """Source unit discovered before a real P5 clause parser exists."""

    source_artifact_id: str
    source_unit_id: str
    source_locator: str
    raw_text: str


@dataclass(frozen=True)
class StarterP5RuntimeScaffold:
    """Serializable blocked P5 artifact plus findings and non-claim summary."""

    clause_surface: dict[str, Any]
    findings: tuple[dict[str, Any], ...]
    evidence_pack_summary: dict[str, Any]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "clause_surface": self.clause_surface,
            "findings": list(self.findings),
            "evidence_pack_summary": self.evidence_pack_summary,
        }


def build_blocked_p5_runtime_scaffold(
    *,
    frontend_id: str,
    run_id: str,
    source_id: str,
    base_id: str | None,
    source_units: Iterable[StarterP5SourceUnit],
    blocked_reason: str = P5_BLOCKED_REASON,
) -> StarterP5RuntimeScaffold:
    """Build explicit blocked P5 rows for inventoried amendment source units.

    The output makes no replay, payload, or canonical-effect claim. It exists so
    a starter runtime can preserve discovered operative-looking source units
    without silently dropping them or pretending P5 is implemented.
    """

    _require_frontend_id(frontend_id)
    _require_non_empty("run_id", run_id)
    _require_non_empty("source_id", source_id)
    _require_non_empty("blocked_reason", blocked_reason)

    units = tuple(source_units)
    _require_unique_source_units(units)

    clauses: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    clause_ids: set[str] = set()
    for unit in units:
        _require_source_unit(unit)
        clause_id = f"p5-blocked-{_slug(unit.source_unit_id)}"
        if clause_id in clause_ids:
            raise ValueError(f"duplicate generated P5 clause id: {clause_id}")
        clause_ids.add(clause_id)
        finding_id = f"finding-{clause_id}"
        clauses.append(
            {
                "clause_id": clause_id,
                "source_artifact_id": unit.source_artifact_id,
                "source_unit_id": unit.source_unit_id,
                "source_locator": unit.source_locator,
                "instruction_family": "unproved_clause_surface",
                "raw_text": unit.raw_text,
                "target_hints": [],
                "confidence": "unresolved",
                "status": P5_BLOCKED_STATUS,
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "skip_with_finding",
                "rejection_reason": blocked_reason,
                "finding_ids": [finding_id],
            }
        )
        findings.append(
            {
                "finding_id": finding_id,
                "run_id": run_id,
                "frontend_id": frontend_id,
                "severity": "warning",
                "family": P5_BLOCKED_RULE_FAMILY,
                "rule_id": f"{frontend_id}.{P5_BLOCKED_RULE_SUFFIX}",
                "phase": "P5",
                "source_artifact_id": unit.source_artifact_id,
                "source_unit_id": unit.source_unit_id,
                "related_operation_effect_row_ids": [],
                "related_replay_row_ids": [],
                "related_audit_row_ids": [],
                "message": blocked_reason,
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "skip_with_finding",
                "evidence": {
                    "source_locator": unit.source_locator,
                    "raw_text": unit.raw_text,
                },
            }
        )

    clause_surface = {
        "kind": f"{frontend_id}_blocked_p5_clause_surface",
        "source_id": source_id,
        "base_id": base_id,
        "status": P5_BLOCKED_STATUS,
        "claim": "no_clause_surface_support",
        "clauses": clauses,
    }
    summary = {
        "kind": f"{frontend_id}_blocked_p5_runtime_scaffold_summary",
        "run_id": run_id,
        "frontend_id": frontend_id,
        "claim_summary": {
            "p5_clause_rows": len(clauses),
            "accepted_p5_clause_rows": 0,
            "operation_effect_rows": 0,
            "accepted_operation_effect_rows": 0,
            "replay_attempts": 0,
            "replay_successes": 0,
        },
        "non_claim_summary": {
            "blocked_p5_clause_rows": len(clauses),
            "blocking_findings": len(findings),
        },
        "disposition_counts": {
            "strict_block": len(findings),
            "quirks_skip_with_finding": len(findings),
        },
    }
    return StarterP5RuntimeScaffold(
        clause_surface=clause_surface,
        findings=tuple(findings),
        evidence_pack_summary=summary,
    )


def _require_frontend_id(frontend_id: str) -> None:
    _require_non_empty("frontend_id", frontend_id)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", frontend_id):
        raise ValueError("frontend_id must be lower snake_case and start with a letter")


def _require_non_empty(name: str, value: str) -> None:
    if value == "":
        raise ValueError(f"{name} must be non-empty")


def _require_source_unit(unit: StarterP5SourceUnit) -> None:
    _require_non_empty("source_artifact_id", unit.source_artifact_id)
    _require_non_empty("source_unit_id", unit.source_unit_id)
    _require_non_empty("source_locator", unit.source_locator)
    _require_non_empty("raw_text", unit.raw_text)


def _require_unique_source_units(units: tuple[StarterP5SourceUnit, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    for unit in units:
        key = (unit.source_artifact_id, unit.source_unit_id)
        if key in seen:
            raise ValueError(f"duplicate P5 source unit: {unit.source_artifact_id}:{unit.source_unit_id}")
        seen.add(key)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if slug == "":
        raise ValueError("source_unit_id must contain at least one ASCII letter or digit")
    return slug
