"""Canonical-effect readiness gates for New Zealand operation witnesses.

This is a pre-lowering evidence surface. It does not emit ``LegalOperation``s
and does not claim replay support.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface
from lawvm.new_zealand.payload_surface import (
    NZPayloadInstructionSafety,
    NZPayloadInstructionShape,
    NZPayloadRole,
    NZPayloadSemanticsStatus,
    NZPayloadStatus,
    NZPayloadSurfaceReport,
    build_archived_work_payload_surface,
)


class NZInstructionSemanticCandidateStatus(StrEnum):
    """Closed instruction-semantic candidate status for an NZ effect-readiness row."""

    BLOCKED_PAYLOAD_WITNESS_NOT_AVAILABLE = "blocked_payload_witness_not_available"
    NOT_REQUIRED_FOR_REPEAL_CANDIDATE = "not_required_for_repeal_candidate"
    REVIEW_RETROSPECTIVE_INCORPORATED_NOTE = "review_retrospective_incorporated_note"
    CANDIDATE_ONLY_INSTRUCTION_SEMANTICS = "candidate_only_instruction_semantics"
    BLOCKED_INSTRUCTION_INDIRECTION = "blocked_instruction_indirection"
    BLOCKED_INSTRUCTION_OPAQUE_OR_UNCLASSIFIED = "blocked_instruction_opaque_or_unclassified"
    BLOCKED_INSTRUCTION_SEMANTICS_UNCLASSIFIED = "blocked_instruction_semantics_unclassified"


class NZInstructionSemanticCandidateFamily(StrEnum):
    """Closed instruction-semantic candidate family. ``NONE`` (``""``) marks absent."""

    NONE = ""
    REPEAL_WITHOUT_ENACTED_PAYLOAD = "repeal_without_enacted_payload"
    SCHEDULE_OR_OMNIBUS_INDIRECTION = "schedule_or_omnibus_indirection"
    INSERT_INSTRUCTION = "insert_instruction"
    REPLACE_INSTRUCTION = "replace_instruction"
    AMEND_INSTRUCTION = "amend_instruction"
    UNMAPPED_INSTRUCTION = "unmapped_instruction"


@dataclass(frozen=True)
class NZEffectReadinessRow:
    row_id: str
    operation_row_id: str
    payload_row_id: str
    operation_family: str
    operation_lowering_readiness_status: str
    operation_target_surface_status: str
    operation_target_hint_status: str
    operation_target_address_status: str
    operation_target_blocking_rule_id: str
    operation_dependency_status: str
    target_address: str
    payload_status: NZPayloadStatus
    payload_role: NZPayloadRole
    payload_semantics_status: NZPayloadSemanticsStatus
    payload_instruction_shape: NZPayloadInstructionShape
    payload_instruction_safety: NZPayloadInstructionSafety
    instruction_semantic_candidate_status: NZInstructionSemanticCandidateStatus
    instruction_semantic_candidate_family: NZInstructionSemanticCandidateFamily
    instruction_semantic_rule_id: str
    # effect_readiness_status forwards the upstream payload-blocked vocabulary
    # (NZPayloadStatus values) when payload extraction did not succeed, in union
    # with this module's own ready/blocked-effect terms; it is therefore a merged
    # serialized vocabulary kept typed-open as ``str`` rather than force-closed.
    effect_readiness_status: str
    canonical_family_candidate: str = ""
    blocking_rule_id: str = ""
    payload_match_count: int = 0
    payload_match_kinds: tuple[str, ...] = ()
    payload_match_headings: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "operation_row_id": self.operation_row_id,
            "payload_row_id": self.payload_row_id,
            "operation_family": self.operation_family,
            "operation_lowering_readiness_status": self.operation_lowering_readiness_status,
            "operation_target_surface_status": self.operation_target_surface_status,
            "operation_target_hint_status": self.operation_target_hint_status,
            "operation_target_address_status": self.operation_target_address_status,
            "operation_target_blocking_rule_id": self.operation_target_blocking_rule_id,
            "operation_dependency_status": self.operation_dependency_status,
            "target_address": self.target_address,
            "payload_status": self.payload_status,
            "payload_role": self.payload_role,
            "payload_semantics_status": self.payload_semantics_status,
            "payload_instruction_shape": self.payload_instruction_shape,
            "payload_instruction_safety": self.payload_instruction_safety,
            "instruction_semantic_candidate_status": self.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": self.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": self.instruction_semantic_rule_id,
            "effect_readiness_status": self.effect_readiness_status,
            "canonical_family_candidate": self.canonical_family_candidate,
            "blocking_rule_id": self.blocking_rule_id,
            "payload_match_count": self.payload_match_count,
            "payload_match_kinds": list(self.payload_match_kinds),
            "payload_match_headings": list(self.payload_match_headings),
        }


@dataclass(frozen=True)
class NZEffectReadinessReport:
    work_id: str
    rows: tuple[NZEffectReadinessRow, ...]

    def summary(self) -> dict[str, Any]:
        readiness_counts = Counter(row.effect_readiness_status for row in self.rows)
        family_counts = Counter(row.operation_family for row in self.rows)
        canonical_counts = Counter(row.canonical_family_candidate or "__none__" for row in self.rows)
        payload_semantics_counts = Counter(row.payload_semantics_status or "__none__" for row in self.rows)
        payload_instruction_shape_counts = Counter(row.payload_instruction_shape or "__none__" for row in self.rows)
        payload_instruction_safety_counts = Counter(row.payload_instruction_safety or "__none__" for row in self.rows)
        instruction_status_counts = Counter(row.instruction_semantic_candidate_status or "__none__" for row in self.rows)
        instruction_family_counts = Counter(row.instruction_semantic_candidate_family or "__none__" for row in self.rows)
        instruction_rule_counts = Counter(row.instruction_semantic_rule_id or "__none__" for row in self.rows)
        return {
            "work_id": self.work_id,
            "rows": len(self.rows),
            "effect_readiness_status_counts": dict(sorted(readiness_counts.items())),
            "operation_family_counts": dict(sorted(family_counts.items())),
            "canonical_family_candidate_counts": dict(sorted(canonical_counts.items())),
            "payload_semantics_status_counts": dict(sorted(payload_semantics_counts.items())),
            "payload_instruction_shape_counts": dict(sorted(payload_instruction_shape_counts.items())),
            "payload_instruction_safety_counts": dict(sorted(payload_instruction_safety_counts.items())),
            "instruction_semantic_candidate_status_counts": dict(sorted(instruction_status_counts.items())),
            "instruction_semantic_candidate_family_counts": dict(sorted(instruction_family_counts.items())),
            "instruction_semantic_rule_id_counts": dict(sorted(instruction_rule_counts.items())),
            "ready_for_canonical_effect_lowering": readiness_counts["ready_for_canonical_effect_lowering"],
            "replay_claims": False,
            "canonical_effect_claims": False,
        }

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        effect_readiness_status: str = "",
        operation_family: str = "",
        payload_status: str = "",
        instruction_semantic_candidate_status: str = "",
        operation_target_address_status: str = "",
    ) -> dict[str, Any]:
        filtered_rows = self.filtered_rows(
            effect_readiness_status=effect_readiness_status,
            operation_family=operation_family,
            payload_status=payload_status,
            instruction_semantic_candidate_status=instruction_semantic_candidate_status,
            operation_target_address_status=operation_target_address_status,
        )
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "canonical_effect_readiness",
            "truth_claim": "pre_lowering_readiness_classification",
            "replay_claims": False,
            "canonical_effect_claims": False,
            "summary": self.summary(),
            "filters": _jsonable_filters(
                effect_readiness_status=effect_readiness_status,
                operation_family=operation_family,
                payload_status=payload_status,
                instruction_semantic_candidate_status=instruction_semantic_candidate_status,
                operation_target_address_status=operation_target_address_status,
            ),
            "filtered_summary": NZEffectReadinessReport(self.work_id, filtered_rows).summary(),
        }
        if summary_only:
            return payload
        rows = filtered_rows if row_limit is None else filtered_rows[:row_limit]
        payload["rows"] = [row.to_jsonable() for row in rows]
        if row_limit is not None and len(filtered_rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(filtered_rows) - row_limit
        return payload

    def filtered_rows(
        self,
        *,
        effect_readiness_status: str = "",
        operation_family: str = "",
        payload_status: str = "",
        instruction_semantic_candidate_status: str = "",
        operation_target_address_status: str = "",
    ) -> tuple[NZEffectReadinessRow, ...]:
        filtered = self.rows
        if effect_readiness_status:
            filtered = tuple(row for row in filtered if row.effect_readiness_status == effect_readiness_status)
        if operation_family:
            filtered = tuple(row for row in filtered if row.operation_family == operation_family)
        if payload_status:
            filtered = tuple(row for row in filtered if row.payload_status == payload_status)
        if instruction_semantic_candidate_status:
            filtered = tuple(
                row
                for row in filtered
                if row.instruction_semantic_candidate_status == instruction_semantic_candidate_status
            )
        if operation_target_address_status:
            filtered = tuple(
                row for row in filtered if row.operation_target_address_status == operation_target_address_status
            )
        return filtered


def _jsonable_filters(
    *,
    effect_readiness_status: str,
    operation_family: str,
    payload_status: str,
    instruction_semantic_candidate_status: str,
    operation_target_address_status: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "effect_readiness_status": effect_readiness_status,
            "operation_family": operation_family,
            "payload_status": payload_status,
            "instruction_semantic_candidate_status": instruction_semantic_candidate_status,
            "operation_target_address_status": operation_target_address_status,
        }.items()
        if value
    }


def build_effect_readiness_surface(
    operation_surface: NZOperationSurfaceReport,
    payload_surface: NZPayloadSurfaceReport,
) -> NZEffectReadinessReport:
    payload_by_operation_row_id = {row.operation_row_id: row for row in payload_surface.rows}
    rows: list[NZEffectReadinessRow] = []
    for index, operation_row in enumerate(operation_surface.rows, start=1):
        payload_row = payload_by_operation_row_id.get(operation_row.row_id)
        payload_status = (
            payload_row.payload_status if payload_row is not None else NZPayloadStatus.BLOCKED_PAYLOAD_SURFACE_MISSING
        )
        payload_role = payload_row.payload_role if payload_row is not None else NZPayloadRole.NONE
        payload_semantics_status = (
            payload_row.payload_semantics_status
            if payload_row is not None
            else NZPayloadSemanticsStatus.PAYLOAD_WITNESS_NOT_AVAILABLE
        )
        payload_instruction_shape = (
            payload_row.payload_instruction_shape if payload_row is not None else NZPayloadInstructionShape.NONE
        )
        payload_instruction_safety = (
            payload_row.payload_instruction_safety if payload_row is not None else NZPayloadInstructionSafety.NONE
        )
        operation_lowering_readiness_status = (
            payload_row.operation_lowering_readiness_status
            if payload_row is not None
            else operation_row.lowering_readiness_status
        )
        operation_target_surface_status = (
            payload_row.operation_target_surface_status if payload_row is not None else operation_row.target_surface_status
        )
        operation_target_hint_status = (
            payload_row.operation_target_hint_status if payload_row is not None else operation_row.target_hint.target_hint_status
        )
        operation_target_address_status = (
            payload_row.operation_target_address_status
            if payload_row is not None
            else operation_row.target_address_candidate.target_address_status
        )
        operation_target_blocking_rule_id = (
            payload_row.operation_target_blocking_rule_id
            if payload_row is not None
            else operation_row.target_address_candidate.blocking_rule_id
        )
        instruction_status, instruction_family, instruction_rule_id = _instruction_semantic_candidate(
            operation_row.operation_family,
            payload_status,
            payload_instruction_shape,
            payload_instruction_safety,
        )
        status, canonical_family, rule_id = _effect_readiness_status(operation_row.operation_family, payload_status)
        payload_matches = payload_row.matches if payload_row is not None else ()
        rows.append(
            NZEffectReadinessRow(
                row_id=f"nz-effect-ready-{index}",
                operation_row_id=operation_row.row_id,
                payload_row_id=payload_row.row_id if payload_row is not None else "",
                operation_family=operation_row.operation_family,
                operation_lowering_readiness_status=operation_lowering_readiness_status,
                operation_target_surface_status=operation_target_surface_status,
                operation_target_hint_status=operation_target_hint_status,
                operation_target_address_status=operation_target_address_status,
                operation_target_blocking_rule_id=operation_target_blocking_rule_id,
                operation_dependency_status=operation_row.dependency_status,
                target_address=operation_row.target_address_candidate.address,
                payload_status=payload_status,
                payload_role=payload_role,
                payload_semantics_status=payload_semantics_status,
                payload_instruction_shape=payload_instruction_shape,
                payload_instruction_safety=payload_instruction_safety,
                instruction_semantic_candidate_status=instruction_status,
                instruction_semantic_candidate_family=instruction_family,
                instruction_semantic_rule_id=instruction_rule_id,
                effect_readiness_status=status,
                canonical_family_candidate=canonical_family,
                blocking_rule_id=rule_id,
                payload_match_count=len(payload_matches),
                payload_match_kinds=tuple(match.kind for match in payload_matches),
                payload_match_headings=tuple(match.heading for match in payload_matches),
            )
        )
    return NZEffectReadinessReport(work_id=operation_surface.work_id, rows=tuple(rows))


def build_archived_work_effect_readiness_surface(db_path: Path, work_id: str) -> NZEffectReadinessReport:
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    payload_surface = build_archived_work_payload_surface(db_path, work_id)
    return build_effect_readiness_surface(operation_surface, payload_surface)


def _effect_readiness_status(operation_family: str, payload_status: NZPayloadStatus) -> tuple[str, str, str]:
    if payload_status != NZPayloadStatus.PAYLOAD_FOUND:
        rule_suffix = payload_status.removeprefix("blocked_")
        return payload_status, "", f"nz_effect_readiness_{rule_suffix}"
    if operation_family == "repealed":
        return "ready_for_canonical_effect_lowering", "repeal", ""
    if operation_family in {"inserted", "added", "replaced", "substituted"}:
        return (
            "blocked_structural_payload_semantics_not_extracted",
            "",
            "nz_effect_readiness_structural_payload_semantics_not_extracted",
        )
    if operation_family == "amended":
        return (
            "blocked_text_or_structural_amendment_semantics_not_extracted",
            "",
            "nz_effect_readiness_amendment_semantics_not_extracted",
        )
    return (
        "blocked_operation_family_not_canonical",
        "",
        "nz_effect_readiness_operation_family_not_canonical",
    )


def _instruction_semantic_candidate(
    operation_family: str,
    payload_status: NZPayloadStatus,
    payload_instruction_shape: NZPayloadInstructionShape,
    payload_instruction_safety: NZPayloadInstructionSafety,
) -> tuple[NZInstructionSemanticCandidateStatus, NZInstructionSemanticCandidateFamily, str]:
    if payload_status != NZPayloadStatus.PAYLOAD_FOUND:
        return (
            NZInstructionSemanticCandidateStatus.BLOCKED_PAYLOAD_WITNESS_NOT_AVAILABLE,
            NZInstructionSemanticCandidateFamily.NONE,
            "nz_instruction_semantics_payload_witness_not_available",
        )
    if operation_family == "repealed":
        return (
            NZInstructionSemanticCandidateStatus.NOT_REQUIRED_FOR_REPEAL_CANDIDATE,
            NZInstructionSemanticCandidateFamily.REPEAL_WITHOUT_ENACTED_PAYLOAD,
            "nz_instruction_semantics_not_required_repeal",
        )
    if payload_instruction_shape == NZPayloadInstructionShape.RETROSPECTIVE_INCORPORATED_NOTE:
        family = _candidate_instruction_family(operation_family)
        return (
            NZInstructionSemanticCandidateStatus.REVIEW_RETROSPECTIVE_INCORPORATED_NOTE,
            family,
            "nz_instruction_semantics_review_retrospective_incorporated_note",
        )
    if payload_instruction_safety == NZPayloadInstructionSafety.CANDIDATE_ONLY_SEMANTIC_CLASSIFICATION:
        family = _candidate_instruction_family(operation_family)
        return (
            NZInstructionSemanticCandidateStatus.CANDIDATE_ONLY_INSTRUCTION_SEMANTICS,
            family,
            "nz_instruction_semantics_candidate_direct_instruction",
        )
    if payload_instruction_safety == NZPayloadInstructionSafety.UNSAFE_SCHEDULE_OR_OMNIBUS_INDIRECTION:
        return (
            NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_INDIRECTION,
            NZInstructionSemanticCandidateFamily.SCHEDULE_OR_OMNIBUS_INDIRECTION,
            "nz_instruction_semantics_blocked_schedule_or_omnibus_indirection",
        )
    if payload_instruction_safety == NZPayloadInstructionSafety.UNSAFE_OPAQUE_OR_UNCLASSIFIED:
        return (
            NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_OPAQUE_OR_UNCLASSIFIED,
            NZInstructionSemanticCandidateFamily.NONE,
            "nz_instruction_semantics_blocked_opaque_or_unclassified",
        )
    return (
        NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_SEMANTICS_UNCLASSIFIED,
        NZInstructionSemanticCandidateFamily.NONE,
        "nz_instruction_semantics_unclassified",
    )


def _candidate_instruction_family(operation_family: str) -> NZInstructionSemanticCandidateFamily:
    if operation_family in {"inserted", "added"}:
        return NZInstructionSemanticCandidateFamily.INSERT_INSTRUCTION
    if operation_family in {"replaced", "substituted"}:
        return NZInstructionSemanticCandidateFamily.REPLACE_INSTRUCTION
    if operation_family == "amended":
        return NZInstructionSemanticCandidateFamily.AMEND_INSTRUCTION
    return NZInstructionSemanticCandidateFamily.UNMAPPED_INSTRUCTION


def main(args: Any) -> None:
    report = build_archived_work_effect_readiness_surface(Path(args.db), args.work_id)
    if args.json:
        print(
            json.dumps(
                report.to_jsonable(
                    summary_only=args.summary_only,
                    row_limit=args.limit,
                    effect_readiness_status=args.effect_readiness_status,
                    operation_family=args.operation_family,
                    payload_status=args.payload_status,
                    instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
                    operation_target_address_status=args.operation_target_address_status,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    summary = report.summary()
    rows = report.filtered_rows(
        effect_readiness_status=args.effect_readiness_status,
        operation_family=args.operation_family,
        payload_status=args.payload_status,
        instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
        operation_target_address_status=args.operation_target_address_status,
    )
    filters = _jsonable_filters(
        effect_readiness_status=args.effect_readiness_status,
        operation_family=args.operation_family,
        payload_status=args.payload_status,
        instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
        operation_target_address_status=args.operation_target_address_status,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(rows)} filters={filters} "
        f"effect_readiness_status_counts={summary['effect_readiness_status_counts']}"
    )
    if args.summary_only:
        return
    for row in rows[: args.limit]:
        print(
            f"{row.row_id}\t{row.operation_row_id}\t{row.operation_family}\t"
            f"{row.effect_readiness_status}\t{row.target_address or '-'}"
        )
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more")
