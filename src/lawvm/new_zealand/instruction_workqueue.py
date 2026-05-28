"""Instruction-semantics work queue for New Zealand payload witnesses.

This surface is diagnostic only. It makes the next lowering work explicit
without emitting canonical effects, replaying candidates, or claiming oracle
agreement.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lawvm.core.evidence_contracts import CorpusOperationEvidenceRow, CorpusRowStatus
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
    TARGET_RECOVERED,
    TARGET_RESOLVED,
    TargetResolutionCertificate,
)
from lawvm.new_zealand.effect_readiness import (
    NZEffectReadinessReport,
    build_archived_work_effect_readiness_surface,
    build_effect_readiness_surface,
)
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface
from lawvm.new_zealand.payload_surface import NZPayloadSurfaceReport, build_archived_work_payload_surface
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode, parse_archived_work_latest
from lawvm.new_zealand.text_comparison import (
    normalized_nz_inline_contains,
    normalized_nz_inline_occurrence_count,
)


@dataclass(frozen=True)
class NZInstructionWorkQueueRow:
    row_id: str
    operation_row_id: str
    effect_readiness_row_id: str
    queue_status: str
    operation_family: str
    target_address: str
    effect_readiness_status: str
    blocking_rule_id: str
    amending_work_id: str
    amending_provision_hrefs: tuple[str, ...]
    instruction_semantic_candidate_status: str
    instruction_semantic_candidate_family: str
    instruction_semantic_rule_id: str
    payload_instruction_shape: str
    payload_instruction_safety: str
    payload_match_headings: tuple[str, ...]
    payload_text_snippets: tuple[str, ...]
    instruction_subfamily_status: str = ""
    instruction_subfamily: str = ""
    instruction_subfamily_rule_id: str = ""
    payload_structural_subfamily_status: str = ""
    payload_structural_subfamily: str = ""
    payload_structural_subfamily_rule_id: str = ""
    instruction_clause_count: int = 0
    explicit_target_citation: str = ""
    target_citation_status: str = ""
    old_text: str = ""
    new_text: str = ""
    text_substitution_scope: str = ""
    latest_oracle_text_status: str = ""
    latest_oracle_text_rule_id: str = ""
    latest_oracle_target_resolution_status: str = ""
    latest_oracle_target_resolution_rule_id: str = ""
    latest_oracle_target_source_path: tuple[str, ...] = ()
    latest_oracle_old_text_occurrences: int = 0
    latest_oracle_new_text_occurrences: int = 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "operation_row_id": self.operation_row_id,
            "effect_readiness_row_id": self.effect_readiness_row_id,
            "queue_status": self.queue_status,
            "operation_family": self.operation_family,
            "target_address": self.target_address,
            "effect_readiness_status": self.effect_readiness_status,
            "blocking_rule_id": self.blocking_rule_id,
            "amending_work_id": self.amending_work_id,
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "instruction_semantic_candidate_status": self.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": self.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": self.instruction_semantic_rule_id,
            "payload_instruction_shape": self.payload_instruction_shape,
            "payload_instruction_safety": self.payload_instruction_safety,
            "payload_match_headings": list(self.payload_match_headings),
            "payload_text_snippets": list(self.payload_text_snippets),
            "instruction_subfamily_status": self.instruction_subfamily_status,
            "instruction_subfamily": self.instruction_subfamily,
            "instruction_subfamily_rule_id": self.instruction_subfamily_rule_id,
            "payload_structural_subfamily_status": self.payload_structural_subfamily_status,
            "payload_structural_subfamily": self.payload_structural_subfamily,
            "payload_structural_subfamily_rule_id": self.payload_structural_subfamily_rule_id,
            "instruction_clause_count": self.instruction_clause_count,
            "explicit_target_citation": self.explicit_target_citation,
            "target_citation_status": self.target_citation_status,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "text_substitution_scope": self.text_substitution_scope,
            "latest_oracle_text_status": self.latest_oracle_text_status,
            "latest_oracle_text_rule_id": self.latest_oracle_text_rule_id,
            "latest_oracle_target_resolution_status": self.latest_oracle_target_resolution_status,
            "latest_oracle_target_resolution_rule_id": self.latest_oracle_target_resolution_rule_id,
            "latest_oracle_target_source_path": list(self.latest_oracle_target_source_path),
            "latest_oracle_old_text_occurrences": self.latest_oracle_old_text_occurrences,
            "latest_oracle_new_text_occurrences": self.latest_oracle_new_text_occurrences,
        }


@dataclass(frozen=True)
class NZInstructionWorkQueueReport:
    work_id: str
    rows: tuple[NZInstructionWorkQueueRow, ...]

    def summary(self) -> dict[str, Any]:
        return _summarize_rows(self.work_id, self.rows)

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        queue_status: str = "",
        instruction_family: str = "",
        instruction_shape: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
    ) -> dict[str, Any]:
        rows = _filter_rows(
            self.rows,
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )
        filters = _jsonable_filters(
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "instruction_semantics_workqueue",
            "truth_claim": "diagnostic_instruction_semantics_queue",
            "replay_claims": False,
            "canonical_effect_claims": False,
            "summary": self.summary(),
            "filters": filters,
            "filtered_summary": _summarize_rows(self.work_id, rows),
        }
        if summary_only:
            return payload
        selected_rows = rows if row_limit is None else rows[:row_limit]
        payload["rows"] = [row.to_jsonable() for row in selected_rows]
        if row_limit is not None and len(rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(rows) - row_limit
        return payload

    def operation_evidence_rows(self) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_workqueue_evidence_row(self, row) for row in self.rows)

    def filtered_rows(
        self,
        *,
        queue_status: str = "",
        instruction_family: str = "",
        instruction_shape: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
    ) -> tuple[NZInstructionWorkQueueRow, ...]:
        return _filter_rows(
            self.rows,
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )

    def operation_evidence_rows_for(
        self, rows: Iterable[NZInstructionWorkQueueRow]
    ) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_workqueue_evidence_row(self, row) for row in rows)


def _summarize_rows(work_id: str, rows: tuple[NZInstructionWorkQueueRow, ...]) -> dict[str, Any]:
    queue_status_counts = Counter(row.queue_status for row in rows)
    operation_family_counts = Counter(row.operation_family for row in rows)
    candidate_status_counts = Counter(row.instruction_semantic_candidate_status for row in rows)
    candidate_family_counts = Counter(row.instruction_semantic_candidate_family or "__none__" for row in rows)
    instruction_shape_counts = Counter(row.payload_instruction_shape or "__none__" for row in rows)
    instruction_safety_counts = Counter(row.payload_instruction_safety or "__none__" for row in rows)
    subfamily_status_counts = Counter(row.instruction_subfamily_status or "__none__" for row in rows)
    subfamily_counts = Counter(row.instruction_subfamily or "__none__" for row in rows)
    structural_subfamily_status_counts = Counter(row.payload_structural_subfamily_status or "__none__" for row in rows)
    structural_subfamily_counts = Counter(row.payload_structural_subfamily or "__none__" for row in rows)
    target_citation_status_counts = Counter(row.target_citation_status or "__none__" for row in rows)
    text_substitution_scope_counts = Counter(row.text_substitution_scope or "__none__" for row in rows)
    latest_oracle_text_status_counts = Counter(row.latest_oracle_text_status or "__none__" for row in rows)
    latest_oracle_target_resolution_counts = Counter(row.latest_oracle_target_resolution_status or "__none__" for row in rows)
    return {
        "work_id": work_id,
        "rows": len(rows),
        "queue_status_counts": dict(sorted(queue_status_counts.items())),
        "operation_family_counts": dict(sorted(operation_family_counts.items())),
        "instruction_semantic_candidate_status_counts": dict(sorted(candidate_status_counts.items())),
        "instruction_semantic_candidate_family_counts": dict(sorted(candidate_family_counts.items())),
        "payload_instruction_shape_counts": dict(sorted(instruction_shape_counts.items())),
        "payload_instruction_safety_counts": dict(sorted(instruction_safety_counts.items())),
        "instruction_subfamily_status_counts": dict(sorted(subfamily_status_counts.items())),
        "instruction_subfamily_counts": dict(sorted(subfamily_counts.items())),
        "payload_structural_subfamily_status_counts": dict(sorted(structural_subfamily_status_counts.items())),
        "payload_structural_subfamily_counts": dict(sorted(structural_subfamily_counts.items())),
        "target_citation_status_counts": dict(sorted(target_citation_status_counts.items())),
        "text_substitution_scope_counts": dict(sorted(text_substitution_scope_counts.items())),
        "latest_oracle_text_status_counts": dict(sorted(latest_oracle_text_status_counts.items())),
        "latest_oracle_target_resolution_status_counts": dict(sorted(latest_oracle_target_resolution_counts.items())),
        "direct_single_text_substitution_candidates": subfamily_counts["direct_single_text_substitution"],
        "direct_each_place_text_substitution_candidates": subfamily_counts["direct_each_place_text_substitution"],
        "candidate_instruction_rows": queue_status_counts["candidate"],
        "review_instruction_rows": queue_status_counts["review"],
        "blocked_instruction_rows": queue_status_counts["blocked"],
        "not_required_rows": queue_status_counts["not_required"],
        "replay_claims": False,
        "canonical_effect_claims": False,
    }


def _jsonable_filters(
    *,
    queue_status: str,
    instruction_family: str,
    instruction_shape: str,
    instruction_subfamily_status: str,
    instruction_subfamily: str,
    payload_structural_subfamily_status: str,
    payload_structural_subfamily: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "queue_status": queue_status,
            "instruction_family": instruction_family,
            "instruction_shape": instruction_shape,
            "instruction_subfamily_status": instruction_subfamily_status,
            "instruction_subfamily": instruction_subfamily,
            "payload_structural_subfamily_status": payload_structural_subfamily_status,
            "payload_structural_subfamily": payload_structural_subfamily,
        }.items()
        if value
    }


def build_instruction_workqueue(
    operation_surface: NZOperationSurfaceReport,
    payload_surface: NZPayloadSurfaceReport,
    effect_readiness: NZEffectReadinessReport | None = None,
    target_document: NZSourceDocument | None = None,
) -> NZInstructionWorkQueueReport:
    readiness = effect_readiness or build_effect_readiness_surface(operation_surface, payload_surface)
    operation_by_row_id = {row.row_id: row for row in operation_surface.rows}
    payload_by_row_id = {row.operation_row_id: row for row in payload_surface.rows}
    rows: list[NZInstructionWorkQueueRow] = []
    for index, readiness_row in enumerate(readiness.rows, start=1):
        operation_row = operation_by_row_id.get(readiness_row.operation_row_id)
        payload_row = payload_by_row_id.get(readiness_row.operation_row_id)
        payload_matches = payload_row.matches if payload_row is not None else ()
        payload_texts = tuple(match.text for match in payload_matches)
        payload_text_snippets = tuple(_snippet(match.text) for match in payload_matches)
        text_substitution = _classify_direct_single_text_substitution(
            operation_family=readiness_row.operation_family,
            target_address=readiness_row.target_address,
            payload_instruction_shape=readiness_row.payload_instruction_shape,
            amending_provision_hrefs=operation_row.amending_provision_hrefs if operation_row is not None else (),
            payload_texts=payload_texts,
        )
        structural_subfamily = _classify_report_only_structural_subfamily(
            operation_family=readiness_row.operation_family,
            target_address=readiness_row.target_address,
            payload_instruction_shape=readiness_row.payload_instruction_shape,
            text_substitution_status=text_substitution.status,
            payload_texts=payload_texts,
        )
        oracle_text_witness = _latest_oracle_text_witness(
            text_substitution=text_substitution,
            target_address=readiness_row.target_address,
            target_document=target_document,
        )
        rows.append(
            NZInstructionWorkQueueRow(
                row_id=f"nz-instruction-workqueue-{index}",
                operation_row_id=readiness_row.operation_row_id,
                effect_readiness_row_id=readiness_row.row_id,
                queue_status=_queue_status(readiness_row.instruction_semantic_candidate_status),
                operation_family=readiness_row.operation_family,
                target_address=readiness_row.target_address,
                effect_readiness_status=readiness_row.effect_readiness_status,
                blocking_rule_id=readiness_row.blocking_rule_id,
                amending_work_id=operation_row.amending_work_id if operation_row is not None else "",
                amending_provision_hrefs=operation_row.amending_provision_hrefs if operation_row is not None else (),
                instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
                instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
                instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
                payload_instruction_shape=readiness_row.payload_instruction_shape,
                payload_instruction_safety=readiness_row.payload_instruction_safety,
                payload_match_headings=tuple(match.heading for match in payload_matches),
                payload_text_snippets=payload_text_snippets,
                instruction_subfamily_status=text_substitution.status,
                instruction_subfamily=text_substitution.subfamily,
                instruction_subfamily_rule_id=text_substitution.rule_id,
                payload_structural_subfamily_status=structural_subfamily.status,
                payload_structural_subfamily=structural_subfamily.subfamily,
                payload_structural_subfamily_rule_id=structural_subfamily.rule_id,
                instruction_clause_count=text_substitution.clause_count,
                explicit_target_citation=text_substitution.explicit_target_citation,
                target_citation_status=text_substitution.target_citation_status,
                old_text=text_substitution.old_text,
                new_text=text_substitution.new_text,
                text_substitution_scope=text_substitution.scope,
                latest_oracle_text_status=oracle_text_witness.status,
                latest_oracle_text_rule_id=oracle_text_witness.rule_id,
                latest_oracle_target_resolution_status=oracle_text_witness.target_resolution_status,
                latest_oracle_target_resolution_rule_id=oracle_text_witness.target_resolution_rule_id,
                latest_oracle_target_source_path=oracle_text_witness.target_source_path,
                latest_oracle_old_text_occurrences=oracle_text_witness.old_text_occurrences,
                latest_oracle_new_text_occurrences=oracle_text_witness.new_text_occurrences,
            )
        )
    return NZInstructionWorkQueueReport(work_id=operation_surface.work_id, rows=tuple(rows))


def build_archived_work_instruction_workqueue(db_path: Path, work_id: str) -> NZInstructionWorkQueueReport:
    target_document = parse_archived_work_latest(db_path, work_id)
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    payload_surface = build_archived_work_payload_surface(db_path, work_id)
    effect_readiness = build_archived_work_effect_readiness_surface(db_path, work_id)
    return build_instruction_workqueue(operation_surface, payload_surface, effect_readiness, target_document)


def _queue_status(instruction_status: str) -> str:
    if instruction_status == "candidate_only_instruction_semantics":
        return "candidate"
    if instruction_status == "review_retrospective_incorporated_note":
        return "review"
    if instruction_status == "not_required_for_repeal_candidate":
        return "not_required"
    return "blocked"


def _snippet(text: str, *, limit: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _filter_rows(
    rows: tuple[NZInstructionWorkQueueRow, ...],
    *,
    queue_status: str = "",
    instruction_family: str = "",
    instruction_shape: str = "",
    instruction_subfamily_status: str = "",
    instruction_subfamily: str = "",
    payload_structural_subfamily_status: str = "",
    payload_structural_subfamily: str = "",
) -> tuple[NZInstructionWorkQueueRow, ...]:
    filtered = rows
    if queue_status:
        filtered = tuple(row for row in filtered if row.queue_status == queue_status)
    if instruction_family:
        filtered = tuple(row for row in filtered if row.instruction_semantic_candidate_family == instruction_family)
    if instruction_shape:
        filtered = tuple(row for row in filtered if row.payload_instruction_shape == instruction_shape)
    if instruction_subfamily_status:
        filtered = tuple(row for row in filtered if row.instruction_subfamily_status == instruction_subfamily_status)
    if instruction_subfamily:
        filtered = tuple(row for row in filtered if row.instruction_subfamily == instruction_subfamily)
    if payload_structural_subfamily_status:
        filtered = tuple(
            row
            for row in filtered
            if row.payload_structural_subfamily_status == payload_structural_subfamily_status
        )
    if payload_structural_subfamily:
        filtered = tuple(row for row in filtered if row.payload_structural_subfamily == payload_structural_subfamily)
    return filtered


def _workqueue_evidence_row(
    report: NZInstructionWorkQueueReport,
    row: NZInstructionWorkQueueRow,
) -> CorpusOperationEvidenceRow:
    if row.queue_status == "not_required":
        return CorpusOperationEvidenceRow(
            row_id=row.row_id,
            frontend_id="new_zealand",
            source_artifact_id=report.work_id or "new_zealand_instruction_workqueue",
            source_unit_id=row.operation_row_id,
            effect_family=row.operation_family,
            resolved_target=row.target_address,
            status=CorpusRowStatus.SKIPPED,
            blocking=False,
            strict_disposition="candidate_handled_elsewhere",
            quirks_disposition="candidate_handled_elsewhere",
            detail=_workqueue_evidence_detail(row, reason="repeal candidate is owned by effect-candidates surface"),
        )
    reason = (
        row.instruction_subfamily_rule_id
        or row.instruction_semantic_rule_id
        or row.blocking_rule_id
        or "nz_instruction_workqueue_not_lowered"
    )
    return CorpusOperationEvidenceRow(
        row_id=row.row_id,
        frontend_id="new_zealand",
        source_artifact_id=report.work_id or "new_zealand_instruction_workqueue",
        source_unit_id=row.operation_row_id,
        effect_family=row.operation_family,
        resolved_target=row.target_address,
        status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record_instruction_workqueue",
        detail=_workqueue_evidence_detail(row, reason=reason),
    )


def _workqueue_evidence_detail(row: NZInstructionWorkQueueRow, *, reason: str) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "reason": reason,
        "queue_status": row.queue_status,
        "effect_readiness_row_id": row.effect_readiness_row_id,
        "effect_readiness_status": row.effect_readiness_status,
        "blocking_rule_id": row.blocking_rule_id,
        "amending_work_id": row.amending_work_id,
        "amending_provision_hrefs": row.amending_provision_hrefs,
        "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
        "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
        "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
        "payload_instruction_shape": row.payload_instruction_shape,
        "payload_instruction_safety": row.payload_instruction_safety,
        "payload_match_headings": row.payload_match_headings,
        "payload_text_snippets": row.payload_text_snippets,
        "instruction_subfamily_status": row.instruction_subfamily_status,
        "instruction_subfamily": row.instruction_subfamily,
        "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
        "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
        "payload_structural_subfamily": row.payload_structural_subfamily,
        "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
        "instruction_clause_count": row.instruction_clause_count,
        "explicit_target_citation": row.explicit_target_citation,
        "target_citation_status": row.target_citation_status,
        "old_text": row.old_text,
        "new_text": row.new_text,
        "text_substitution_scope": row.text_substitution_scope,
        "latest_oracle_text_status": row.latest_oracle_text_status,
        "latest_oracle_text_rule_id": row.latest_oracle_text_rule_id,
        "latest_oracle_target_resolution_status": row.latest_oracle_target_resolution_status,
        "latest_oracle_target_resolution_rule_id": row.latest_oracle_target_resolution_rule_id,
        "latest_oracle_target_source_path": row.latest_oracle_target_source_path,
        "latest_oracle_old_text_occurrences": row.latest_oracle_old_text_occurrences,
        "latest_oracle_new_text_occurrences": row.latest_oracle_new_text_occurrences,
        "replay_claims": False,
        "canonical_effect_claims": False,
    }
    target_resolution = _latest_oracle_target_resolution_evidence(row)
    if target_resolution:
        detail["latest_oracle_target_resolution"] = target_resolution
    return detail


def _latest_oracle_target_resolution_evidence(row: NZInstructionWorkQueueRow) -> dict[str, Any]:
    if not row.latest_oracle_target_resolution_status or not row.latest_oracle_target_resolution_rule_id:
        return {}
    if not row.latest_oracle_target_source_path:
        return {}
    status = (
        TARGET_RESOLVED
        if row.latest_oracle_target_resolution_status == "exact_source_path"
        else TARGET_RECOVERED
    )
    scope_confidence = (
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE
        if row.latest_oracle_target_resolution_status == "exact_source_path"
        else ""
    )
    return TargetResolutionCertificate(
        rule_id=row.latest_oracle_target_resolution_rule_id,
        phase="oracle",
        reason="latest oracle source node resolved for instruction text witness",
        status=status,
        source_target=row.target_address,
        selected_target="/".join(row.latest_oracle_target_source_path),
        candidate_count=1,
        scope_confidence=scope_confidence,
        detail={
            "jurisdiction_status": row.latest_oracle_target_resolution_status,
            "source_path": row.latest_oracle_target_source_path,
        },
    ).to_diagnostic_detail()


@dataclass(frozen=True)
class _TextSubstitutionCandidate:
    status: str
    subfamily: str = ""
    rule_id: str = ""
    clause_count: int = 0
    explicit_target_citation: str = ""
    target_citation_status: str = ""
    old_text: str = ""
    new_text: str = ""
    scope: str = ""


@dataclass(frozen=True)
class _StructuralInstructionSubfamily:
    status: str = ""
    subfamily: str = ""
    rule_id: str = ""


@dataclass(frozen=True)
class _LatestOracleTextWitness:
    status: str
    rule_id: str
    target_resolution_status: str = ""
    target_resolution_rule_id: str = ""
    target_source_path: tuple[str, ...] = ()
    old_text_occurrences: int = 0
    new_text_occurrences: int = 0


@dataclass(frozen=True)
class _LatestOracleTargetResolution:
    node: NZSourceNode
    status: str
    rule_id: str


def _classify_direct_single_text_substitution(
    *,
    operation_family: str,
    target_address: str,
    payload_instruction_shape: str,
    amending_provision_hrefs: tuple[str, ...],
    payload_texts: tuple[str, ...],
) -> _TextSubstitutionCandidate:
    if payload_instruction_shape == "direct_amended_by_instruction":
        return _classify_omitting_substituting_text_substitution(
            operation_family=operation_family,
            target_address=target_address,
            amending_provision_hrefs=amending_provision_hrefs,
            payload_texts=payload_texts,
        )
    if payload_instruction_shape != "direct_substitute_replace_instruction":
        return _TextSubstitutionCandidate(
            status="not_text_substitution_shape",
            rule_id="nz_instruction_subfamily_not_text_substitution_shape",
        )
    text = " ".join(payload_texts)
    clause_count = _replacement_clause_count(text)
    if operation_family != "amended":
        return _TextSubstitutionCandidate(
            status="blocked_structural_replacement_payload",
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    if len(amending_provision_hrefs) != 1 or len(payload_texts) != 1:
        return _TextSubstitutionCandidate(
            status="blocked_payload_multiplicity",
            rule_id="nz_instruction_semantics_blocked_payload_multiplicity",
            clause_count=clause_count,
        )
    if clause_count != 1:
        return _classify_multi_clause_direct_text_substitution(
            text=text,
            target_address=target_address,
            clause_count=clause_count,
        )
    if text.lower().startswith("replace with:"):
        return _TextSubstitutionCandidate(
            status="blocked_structural_replacement_payload",
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    pieces = _extract_replace_with_pieces(text)
    if pieces is None:
        return _TextSubstitutionCandidate(
            status="blocked_text_substitution_parse_failed",
            rule_id="nz_instruction_semantics_blocked_text_substitution_parse_failed",
            clause_count=clause_count,
        )
    explicit_target_citation, old_text, new_text = pieces
    cleaned_new_text, occurrence_scope = _text_substitution_scope(new_text)
    if not _target_citation_matches(explicit_target_citation, target_address):
        return _TextSubstitutionCandidate(
            status="blocked_target_citation_mismatch",
            rule_id="nz_instruction_semantics_blocked_target_citation_mismatch",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="mismatch",
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    if occurrence_scope != "inline_text_single_occurrence":
        if occurrence_scope == "inline_text_each_place":
            return _TextSubstitutionCandidate(
                status="candidate_direct_each_place_text_substitution",
                subfamily="direct_each_place_text_substitution",
                rule_id="nz_instruction_semantics_direct_each_place_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status="matched",
                old_text=old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            status="blocked_multiple_occurrence_text_substitution",
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="matched",
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        status="candidate_direct_single_text_substitution",
        subfamily="direct_single_text_substitution",
        rule_id="nz_instruction_semantics_direct_single_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status="matched",
        old_text=old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_omitting_substituting_text_substitution(
    *,
    operation_family: str,
    target_address: str,
    amending_provision_hrefs: tuple[str, ...],
    payload_texts: tuple[str, ...],
) -> _TextSubstitutionCandidate:
    text = " ".join(payload_texts)
    clause_count = _omitting_substituting_clause_count(text)
    if operation_family != "amended":
        if operation_family in {"inserted", "added"}:
            return _TextSubstitutionCandidate(
                status="not_text_substitution_shape",
                rule_id="nz_instruction_subfamily_not_text_substitution_shape",
                clause_count=clause_count,
            )
        return _TextSubstitutionCandidate(
            status="blocked_structural_replacement_payload",
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    if len(amending_provision_hrefs) != 1 or len(payload_texts) != 1:
        return _TextSubstitutionCandidate(
            status="blocked_payload_multiplicity",
            rule_id="nz_instruction_semantics_blocked_payload_multiplicity",
            clause_count=clause_count,
        )
    if clause_count != 1:
        return _classify_multi_clause_omitting_substituting_text_substitution(
            text=text,
            target_address=target_address,
            clause_count=clause_count,
        )
    pieces = _extract_omitting_substituting_pieces(text)
    if pieces is None:
        return _TextSubstitutionCandidate(
            status="blocked_omitting_substituting_parse_failed",
            rule_id="nz_instruction_semantics_blocked_omitting_substituting_parse_failed",
            clause_count=clause_count,
        )
    explicit_target_citation, old_text, new_text = pieces
    if _looks_structural_omitting_substituting_payload(old_text, new_text):
        return _TextSubstitutionCandidate(
            status="blocked_structural_omitting_substituting_payload",
            rule_id="nz_instruction_semantics_blocked_structural_omitting_substituting_payload",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            old_text=old_text,
            new_text=new_text,
        )
    cleaned_old_text, cleaned_new_text, occurrence_scope = _omitting_substitution_scope(old_text, new_text)
    if not _target_citation_matches(explicit_target_citation, target_address):
        return _TextSubstitutionCandidate(
            status="blocked_target_citation_mismatch",
            rule_id="nz_instruction_semantics_blocked_target_citation_mismatch",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="mismatch",
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    if occurrence_scope != "inline_text_single_occurrence":
        if occurrence_scope == "inline_text_each_place":
            return _TextSubstitutionCandidate(
                status="candidate_direct_each_place_omitting_substituting_text_substitution",
                subfamily="direct_each_place_text_substitution",
                rule_id="nz_instruction_semantics_direct_each_place_omitting_substituting_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status="matched",
                old_text=cleaned_old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            status="blocked_multiple_occurrence_text_substitution",
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="matched",
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        status="candidate_direct_omitting_substituting_text_substitution",
        subfamily="direct_single_text_substitution",
        rule_id="nz_instruction_semantics_direct_omitting_substituting_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status="matched",
        old_text=cleaned_old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_report_only_structural_subfamily(
    *,
    operation_family: str,
    target_address: str,
    payload_instruction_shape: str,
    text_substitution_status: str,
    payload_texts: tuple[str, ...],
) -> _StructuralInstructionSubfamily:
    if text_substitution_status.startswith("candidate_direct_"):
        return _StructuralInstructionSubfamily()
    if payload_instruction_shape == "direct_insert_instruction":
        if operation_family in {"amended", "inserted", "added"}:
            return _direct_insert_payload_subfamily(target_address=target_address, payload_texts=payload_texts)
    if payload_instruction_shape == "direct_substitute_replace_instruction":
        if operation_family in {"replaced", "substituted"}:
            return _direct_replace_payload_subfamily(payload_texts)
        if operation_family == "amended":
            return _StructuralInstructionSubfamily(
                status="blocked_ambiguous_amend_replace_payload",
                subfamily="ambiguous_amend_replace_payload",
                rule_id="nz_instruction_structural_subfamily_ambiguous_amend_replace_payload_blocked",
            )
    if payload_instruction_shape == "direct_amended_by_instruction":
        if _looks_mixed_repeal_substitute_payload(payload_texts):
            return _StructuralInstructionSubfamily(
                status="blocked_mixed_repeal_substitute_payload_not_lowered",
                subfamily="mixed_repeal_substitute_payload",
                rule_id="nz_instruction_structural_subfamily_mixed_repeal_substitute_payload_blocked",
            )
        if operation_family in {"inserted", "added"}:
            return _direct_insert_payload_subfamily(target_address=target_address, payload_texts=payload_texts)
        if operation_family in {"replaced", "substituted"}:
            return _direct_replace_payload_subfamily(payload_texts)
        if operation_family == "amended":
            return _direct_amend_payload_subfamily(payload_texts)
    if payload_instruction_shape == "retrospective_incorporated_note":
        return _StructuralInstructionSubfamily(
            status="review_retrospective_incorporated_payload",
            subfamily="retrospective_incorporated_note",
            rule_id="nz_instruction_structural_subfamily_retrospective_incorporated_note_review",
        )
    if payload_instruction_shape == "schedule_indirection":
        return _StructuralInstructionSubfamily(
            status="blocked_schedule_indirection_payload",
            subfamily="schedule_indirection_payload",
            rule_id="nz_instruction_structural_subfamily_schedule_indirection_payload_blocked",
        )
    if payload_instruction_shape == "other_instruction" and _looks_incorporated_amendment_stub_payload(payload_texts):
        return _StructuralInstructionSubfamily(
            status="blocked_incorporated_amendment_stub_payload",
            subfamily="incorporated_amendment_stub_payload",
            rule_id="nz_instruction_structural_subfamily_incorporated_amendment_stub_payload_blocked",
        )
    return _StructuralInstructionSubfamily()


def _direct_replace_payload_subfamily(payload_texts: tuple[str, ...]) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"^replace\s+with:\s+sections?\s+\S+\s+and\s+\S+\b", normalized):
        return _StructuralInstructionSubfamily(
            status="blocked_multi_section_replace_payload_not_lowered",
            subfamily="multi_section_replace_payload",
            rule_id="nz_instruction_structural_subfamily_multi_section_replace_payload_blocked",
        )
    if re.search(r"\brepealed\s+and\s+the\s+following\s+sections?\s+substituted\b", normalized):
        return _StructuralInstructionSubfamily(
            status="blocked_whole_provision_substitution_payload_not_lowered",
            subfamily="whole_provision_substitution_payload",
            rule_id="nz_instruction_structural_subfamily_whole_provision_substitution_payload_blocked",
        )
    return _StructuralInstructionSubfamily(
        status="blocked_structural_replace_payload_not_lowered",
        subfamily="direct_replace_payload",
        rule_id="nz_instruction_structural_subfamily_direct_replace_payload_blocked",
    )


def _direct_amend_payload_subfamily(payload_texts: tuple[str, ...]) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"\b(?:omitting|replace)\b.*\b(?:substitut|with)\b", normalized) and re.search(
        r"\b(?:adding|insert(?:ing)?)\s+the\s+following\s+subsections?\b",
        normalized,
    ):
        return _StructuralInstructionSubfamily(
            status="blocked_mixed_text_and_structural_insert_payload_not_lowered",
            subfamily="mixed_text_and_structural_insert_payload",
            rule_id="nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked",
        )
    if _looks_direct_text_insert_payload(normalized):
        return _direct_text_insert_payload_subfamily()
    return _StructuralInstructionSubfamily(
        status="blocked_structural_amend_payload_not_lowered",
        subfamily="direct_amend_payload",
        rule_id="nz_instruction_structural_subfamily_direct_amend_payload_blocked",
    )


def _looks_mixed_repeal_substitute_payload(payload_texts: tuple[str, ...]) -> bool:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    return bool(
        re.search(
            r"\brepeal(?:ing)?\b.*\bsubstitut(?:e|ing)\s+the\s+following\s+"
            r"(?:paragraphs?|subparagraphs?|subsections?|sections?)\b",
            normalized,
        )
    )


def _looks_incorporated_amendment_stub_payload(payload_texts: tuple[str, ...]) -> bool:
    normalized = " ".join(" ".join(payload_texts).lower().split())
    return "amendment(s) incorporated in the" in normalized and "act(s)" in normalized


def _direct_insert_payload_subfamily(
    *,
    target_address: str,
    payload_texts: tuple[str, ...],
) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"\bthis\s+(?:section|subsection|paragraph|subparagraph)\s+inserted\s+s\s*\.", normalized):
        return _StructuralInstructionSubfamily(
            status="blocked_historical_inserted_note_payload_not_lowered",
            subfamily="historical_inserted_note_payload",
            rule_id="nz_instruction_structural_subfamily_historical_inserted_note_payload_blocked",
        )
    if re.search(r"\b(?:replace|omitting)\b.*\b(?:with|substituting)\b", normalized) and re.search(
        r"\binsert(?:ing)?\b", normalized
    ):
        return _StructuralInstructionSubfamily(
            status="blocked_mixed_text_and_structural_insert_payload_not_lowered",
            subfamily="mixed_text_and_structural_insert_payload",
            rule_id="nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked",
        )
    if target_address.endswith("/heading") and re.search(r"^after\s*,?\s+insert:", normalized):
        return _StructuralInstructionSubfamily(
            status="blocked_cross_heading_insert_payload_not_lowered",
            subfamily="cross_heading_insert_payload",
            rule_id="nz_instruction_structural_subfamily_cross_heading_insert_payload_blocked",
        )
    if (
        "insert in its appropriate alphabetical order" in normalized
        or "inserting the following definition in its appropriate alphabetical order" in normalized
    ):
        return _StructuralInstructionSubfamily(
            status="blocked_definition_alphabetical_insert_payload_not_lowered",
            subfamily="definition_alphabetical_insert_payload",
            rule_id="nz_instruction_structural_subfamily_definition_alphabetical_insert_payload_blocked",
        )
    if re.search(
        r"\bafter\s+paragraph(?:\s+\([^)]+\))?\s*,?\s+(?:insert:|the\s+following\s+paragraph\b)",
        normalized,
    ):
        return _StructuralInstructionSubfamily(
            status="blocked_paragraph_after_insert_payload_not_lowered",
            subfamily="paragraph_after_insert_payload",
            rule_id="nz_instruction_structural_subfamily_paragraph_after_insert_payload_blocked",
        )
    if re.search(r"\binsert(?:ing)?\s+the\s+following\s+subsections?\s+after\s+subsection\b", normalized):
        return _StructuralInstructionSubfamily(
            status="blocked_subsection_after_insert_payload_not_lowered",
            subfamily="subsection_after_insert_payload",
            rule_id="nz_instruction_structural_subfamily_subsection_after_insert_payload_blocked",
        )
    if (
        re.search(r"\binsert(?:ing)?\s+the\s+following\s+sections?\s+after\s+section\b", normalized)
        or re.search(r"\bthe\s+following\s+sections?\s+is\s+inserted\s+after\s*:\s+section\b", normalized)
        or re.search(r"\bthe\s+following\s+sections?\s+are\s+inserted\s+after\s*:\s+section\b", normalized)
    ) or (
        target_address.startswith("section:")
        and "/" not in target_address
        and re.search(r"^after\s*,?\s+insert:\s+section\b", normalized)
    ):
        return _StructuralInstructionSubfamily(
            status="blocked_section_after_insert_payload_not_lowered",
            subfamily="section_after_insert_payload",
            rule_id="nz_instruction_structural_subfamily_section_after_insert_payload_blocked",
        )
    if _looks_direct_text_insert_payload(normalized):
        return _direct_text_insert_payload_subfamily()
    return _StructuralInstructionSubfamily(
        status="blocked_structural_insert_payload_not_lowered",
        subfamily="direct_insert_payload",
        rule_id="nz_instruction_structural_subfamily_direct_insert_payload_blocked",
    )


def _looks_direct_text_insert_payload(normalized_payload_text: str) -> bool:
    return bool(
        re.search(r"\bafter\b.+\binsert(?:ing)?\b", normalized_payload_text)
        or re.search(r"\binsert(?:ing)?\b.+\bafter\b", normalized_payload_text)
    )


def _direct_text_insert_payload_subfamily() -> _StructuralInstructionSubfamily:
    return _StructuralInstructionSubfamily(
        status="blocked_text_insert_payload_not_lowered",
        subfamily="direct_text_insert_payload",
        rule_id="nz_instruction_structural_subfamily_direct_text_insert_payload_blocked",
    )


def _classify_multi_clause_omitting_substituting_text_substitution(
    *,
    text: str,
    target_address: str,
    clause_count: int,
) -> _TextSubstitutionCandidate:
    clauses = _numbered_instruction_clauses(text)
    if not clauses:
        return _TextSubstitutionCandidate(
            status="blocked_omitting_substituting_parse_failed",
            rule_id="nz_instruction_semantics_blocked_omitting_substituting_parse_failed",
            clause_count=clause_count,
        )
    matches: list[tuple[str, str, str]] = []
    for clause in clauses:
        pieces = _extract_omitting_substituting_pieces(clause)
        if pieces is None:
            continue
        explicit_target_citation, _old_text, _new_text = pieces
        if _target_citation_matches(explicit_target_citation, target_address):
            matches.append(pieces)
    if not matches:
        return _TextSubstitutionCandidate(
            status="blocked_multi_clause_no_matching_target",
            rule_id="nz_instruction_semantics_blocked_multi_clause_no_matching_target",
            clause_count=clause_count,
            target_citation_status="no_match",
        )
    if len(matches) != 1:
        return _TextSubstitutionCandidate(
            status="blocked_multi_clause_target_ambiguous",
            rule_id="nz_instruction_semantics_blocked_multi_clause_target_ambiguous",
            clause_count=clause_count,
            target_citation_status="ambiguous",
        )
    explicit_target_citation, old_text, new_text = matches[0]
    if _looks_structural_omitting_substituting_payload(old_text, new_text):
        return _TextSubstitutionCandidate(
            status="blocked_structural_omitting_substituting_payload",
            rule_id="nz_instruction_semantics_blocked_structural_omitting_substituting_payload",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="matched_in_multi_clause_payload",
            old_text=old_text,
            new_text=new_text,
        )
    cleaned_old_text, cleaned_new_text, occurrence_scope = _omitting_substitution_scope(old_text, new_text)
    if occurrence_scope != "inline_text_single_occurrence":
        if occurrence_scope == "inline_text_each_place":
            return _TextSubstitutionCandidate(
                status="candidate_direct_multi_clause_each_place_omitting_substituting_text_substitution",
                subfamily="direct_each_place_text_substitution",
                rule_id=(
                    "nz_instruction_semantics_direct_multi_clause_each_place_omitting_substituting_"
                    "text_substitution_candidate"
                ),
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status="matched_in_multi_clause_payload",
                old_text=cleaned_old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            status="blocked_multiple_occurrence_text_substitution",
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="matched_in_multi_clause_payload",
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        status="candidate_direct_multi_clause_omitting_substituting_text_substitution",
        subfamily="direct_single_text_substitution",
        rule_id="nz_instruction_semantics_direct_multi_clause_omitting_substituting_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status="matched_in_multi_clause_payload",
        old_text=cleaned_old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_multi_clause_direct_text_substitution(
    *,
    text: str,
    target_address: str,
    clause_count: int,
) -> _TextSubstitutionCandidate:
    clauses = _numbered_instruction_clauses(text)
    if not clauses:
        return _TextSubstitutionCandidate(
            status="blocked_multi_clause_payload",
            rule_id="nz_instruction_semantics_blocked_multi_clause_payload",
            clause_count=clause_count,
        )
    matches: list[tuple[str, str, str]] = []
    for clause in clauses:
        pieces = _extract_replace_with_pieces(clause)
        if pieces is None:
            continue
        explicit_target_citation, _old_text, _new_text = pieces
        if _target_citation_matches(explicit_target_citation, target_address):
            matches.append(pieces)
    if not matches:
        return _TextSubstitutionCandidate(
            status="blocked_multi_clause_no_matching_target",
            rule_id="nz_instruction_semantics_blocked_multi_clause_no_matching_target",
            clause_count=clause_count,
            target_citation_status="no_match",
        )
    if len(matches) != 1:
        return _TextSubstitutionCandidate(
            status="blocked_multi_clause_target_ambiguous",
            rule_id="nz_instruction_semantics_blocked_multi_clause_target_ambiguous",
            clause_count=clause_count,
            target_citation_status="ambiguous",
        )
    explicit_target_citation, old_text, new_text = matches[0]
    cleaned_new_text, occurrence_scope = _text_substitution_scope(new_text)
    if occurrence_scope != "inline_text_single_occurrence":
        if occurrence_scope == "inline_text_each_place":
            return _TextSubstitutionCandidate(
                status="candidate_direct_multi_clause_each_place_text_substitution",
                subfamily="direct_each_place_text_substitution",
                rule_id="nz_instruction_semantics_direct_multi_clause_each_place_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status="matched_in_multi_clause_payload",
                old_text=old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            status="blocked_multiple_occurrence_text_substitution",
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status="matched",
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        status="candidate_direct_multi_clause_text_substitution",
        subfamily="direct_single_text_substitution",
        rule_id="nz_instruction_semantics_direct_multi_clause_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status="matched_in_multi_clause_payload",
        old_text=old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _latest_oracle_text_witness(
    *,
    text_substitution: _TextSubstitutionCandidate,
    target_address: str,
    target_document: NZSourceDocument | None,
) -> _LatestOracleTextWitness:
    if text_substitution.subfamily not in {"direct_single_text_substitution", "direct_each_place_text_substitution"}:
        return _LatestOracleTextWitness(
            status="not_applicable_not_direct_text_substitution",
            rule_id="nz_instruction_latest_oracle_text_not_applicable",
        )
    if target_document is None:
        return _LatestOracleTextWitness(
            status="not_run_target_document_unavailable",
            rule_id="nz_instruction_latest_oracle_text_target_document_unavailable",
        )
    target_node = _latest_oracle_target_node(target_document, target_address)
    if isinstance(target_node, _LatestOracleTextWitness):
        return target_node
    old_occurrences = normalized_nz_inline_occurrence_count(target_node.node.text, text_substitution.old_text)
    new_occurrences = normalized_nz_inline_occurrence_count(target_node.node.text, text_substitution.new_text)
    if old_occurrences == 0 and new_occurrences == 1:
        status = "oracle_new_text_only"
    elif (
        text_substitution.scope == "inline_text_each_place"
        and old_occurrences == 0
        and new_occurrences > 1
    ):
        status = "oracle_new_text_only_each_place"
    elif normalized_nz_inline_contains(text_substitution.new_text, text_substitution.old_text) and new_occurrences > 0:
        status = "oracle_new_text_contains_old_text"
    elif old_occurrences == 1 and new_occurrences == 0:
        status = "oracle_old_text_only"
    elif old_occurrences > 0 and new_occurrences > 0:
        status = "oracle_old_and_new_text"
    else:
        status = "oracle_neither_old_nor_new_text"
    return _LatestOracleTextWitness(
        status=status,
        rule_id=f"nz_instruction_latest_oracle_text_{status}",
        target_resolution_status=target_node.status,
        target_resolution_rule_id=target_node.rule_id,
        target_source_path=target_node.node.path,
        old_text_occurrences=old_occurrences,
        new_text_occurrences=new_occurrences,
    )


def _latest_oracle_target_node(
    target_document: NZSourceDocument,
    target_address: str,
) -> _LatestOracleTargetResolution | _LatestOracleTextWitness:
    suffixes = _source_path_suffix_candidates_from_target_address(target_address)
    if not suffixes:
        return _LatestOracleTextWitness(
            status="blocked_target_address_unmapped",
            rule_id="nz_instruction_latest_oracle_text_target_address_unmapped",
        )
    matches = tuple(
        _LatestOracleTargetResolution(
            node=node,
            status="exact_source_path",
            rule_id="nz_instruction_latest_oracle_target_exact_source_path",
        )
        for suffix in suffixes
        for node in target_document.nodes
        if node.path[-len(suffix) :] == suffix
    )
    if not matches:
        matches = tuple(
            _LatestOracleTargetResolution(
                node=node,
                status="via_unlabeled_source_carrier",
                rule_id="nz_instruction_latest_oracle_target_via_unlabeled_source_carrier",
            )
            for suffix in suffixes
            for node in target_document.nodes
            if _path_matches_suffix_with_unlabeled_carrier(node.path, suffix)
        )
    if not matches:
        nearest_node = _nearest_existing_source_node(target_document, suffixes[0])
        if nearest_node is not None:
            return _LatestOracleTextWitness(
                status="blocked_target_granularity_not_indexed",
                rule_id="nz_instruction_latest_oracle_text_target_granularity_not_indexed",
                target_source_path=nearest_node.path,
            )
        return _LatestOracleTextWitness(
            status="blocked_target_source_node_missing",
            rule_id="nz_instruction_latest_oracle_text_target_source_node_missing",
        )
    if len(matches) > 1:
        return _LatestOracleTextWitness(
            status="blocked_target_source_node_not_unique",
            rule_id="nz_instruction_latest_oracle_text_target_source_node_not_unique",
        )
    if matches[0].node.deletion_status:
        return _LatestOracleTextWitness(
            status="blocked_target_source_node_deleted",
            rule_id="nz_instruction_latest_oracle_text_target_source_node_deleted",
            target_source_path=matches[0].node.path,
        )
    return matches[0]


def _nearest_existing_source_node(target_document: NZSourceDocument, suffix: tuple[str, ...]) -> NZSourceNode | None:
    for width in range(len(suffix) - 1, 0, -1):
        parent_suffix = suffix[:width]
        matches = tuple(node for node in target_document.nodes if node.path[-len(parent_suffix) :] == parent_suffix)
        if len(matches) == 1:
            return matches[0]
    return None


def _path_matches_suffix_with_unlabeled_carrier(path: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    path_index = len(path) - 1
    suffix_index = len(suffix) - 1
    used_carrier = False
    while path_index >= 0 and suffix_index >= 0:
        if path[path_index] == suffix[suffix_index]:
            path_index -= 1
            suffix_index -= 1
            continue
        if "#" in path[path_index]:
            used_carrier = True
            path_index -= 1
            continue
        return False
    return used_carrier and suffix_index < 0


def _source_path_suffix_candidates_from_target_address(target_address: str) -> tuple[tuple[str, ...], ...]:
    parts = [part for part in target_address.split("/") if ":" in part]
    suffixes: list[tuple[str, ...]] = [()]
    for part in parts:
        kind, label = part.split(":", 1)
        if not label:
            return ()
        if kind == "section":
            segment_candidates = (f"prov:{label}",)
        elif kind == "subsection":
            segment_candidates = (f"subprov:{label}", f"label-para:{label}")
        elif kind == "paragraph":
            segment_candidates = (f"label-para:{label}",)
        elif kind in {"part", "schedule"}:
            segment_candidates = (f"{kind}:{label}",)
        else:
            return ()
        suffixes = [(*suffix, segment) for suffix in suffixes for segment in segment_candidates]
    return tuple(tuple(suffix) for suffix in suffixes)


def _replacement_clause_count(text: str) -> int:
    normalized = " ".join(text.lower().split())
    return len(re.findall(r"\b(?:replace|substitute)\b", normalized))


def _omitting_substituting_clause_count(text: str) -> int:
    normalized = " ".join(text.lower().split())
    return len(re.findall(r"\bomitting\b", normalized))


def _numbered_instruction_clauses(text: str) -> tuple[str, ...]:
    normalized = " ".join(text.split())
    boundaries = list(
        re.finditer(
            r"(?:^|\s)(?P<number>\d+)\s+(?=(?:[Ii]n\b|[Rr]eplace\s+with:|[Ss]ection\b|[Ii]s\s+amended\b))",
            normalized,
        )
    )
    if len(boundaries) < 2:
        return ()
    clauses: list[str] = []
    for index, boundary in enumerate(boundaries):
        start = boundary.end()
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(normalized)
        clause = normalized[start:end].strip(" ;")
        if clause:
            clauses.append(clause)
    return tuple(clauses)


def _extract_replace_with_pieces(text: str) -> tuple[str, str, str] | None:
    normalized = " ".join(text.split())
    in_match = re.search(
        r"\bin\s+(?P<target>section\s+\S+)\s*,\s*replace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)(?:\s*\.\s*)?$",
        normalized,
        re.IGNORECASE,
    )
    if in_match is not None:
        explicit_target_citation = in_match.group("target").strip(" ,;")
        old_text = in_match.group("old").strip(" ,;")
        new_text = in_match.group("new").strip(" ,;.")
        if old_text and new_text:
            return explicit_target_citation, old_text, new_text
    match = re.search(r"\breplace\s+(?P<body>.+?)\s+with\s+(?P<new>.+?)(?:\s*\.\s*)?$", normalized, re.IGNORECASE)
    if match is None:
        return None
    body = match.group("body").strip(" ,;")
    new_text = match.group("new").strip(" ,;.")
    body_tokens = body.split()
    if len(body_tokens) < 3 or body_tokens[0].lower() != "section":
        return None
    explicit_target_citation = " ".join(body_tokens[:2]).strip()
    old_text = " ".join(body_tokens[2:]).strip(" ,;")
    if not old_text or not new_text:
        return None
    return explicit_target_citation, old_text, new_text


def _extract_omitting_substituting_pieces(text: str) -> tuple[str, str, str] | None:
    normalized = " ".join(text.split())
    match = re.search(
        r"\bomitting\s+(?P<body>.+?)\s+and\s+substituting\s+(?P<new>.+?)(?:\s*\.\s*)?$",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    prefix = normalized[: match.start()].strip(" ,;-")
    body = match.group("body").strip(" ,;")
    new_text = match.group("new").strip(" ,;.")
    if not body or not new_text:
        return None
    prefix_citation = _last_section_citation(prefix)
    if prefix_citation:
        return prefix_citation, body, new_text
    citation_match = re.match(
        r"(?:section\s+)?(?P<citation>\d+[A-Za-z]*(?:\([^)]+\))*)\s+(?P<old>.+)$",
        body,
        re.IGNORECASE,
    )
    if citation_match is None:
        return None
    explicit_target_citation = "section " + citation_match.group("citation")
    old_text = citation_match.group("old").strip(" ,;")
    if not old_text:
        return None
    return explicit_target_citation, old_text, new_text


def _last_section_citation(text: str) -> str:
    matches = tuple(
        re.finditer(
            r"\bsection\s+(?P<citation>\d+[A-Za-z]*(?:\([^)]+\))*)",
            text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return ""
    return "section " + matches[-1].group("citation")


def _looks_structural_omitting_substituting_payload(old_text: str, new_text: str) -> bool:
    normalized_old = " ".join(old_text.lower().split())
    normalized_new = " ".join(new_text.lower().split())
    if normalized_new.startswith(("the following paragraph", "the following subsection", "the following section")):
        return True
    if re.search(r"\b\d+\s+is\s+amended\b", normalized_new):
        return True
    if re.search(r";\s+and\s+[a-z]\s+by\s+(?:omitting|repealing|inserting)\b", normalized_new):
        return True
    if re.search(r"\bby\s+(?:repealing|inserting)\b", normalized_new):
        return True
    if re.search(r"\b(?:subparagraph|subsection|paragraph)\s+\([^)]+\)", normalized_new):
        return True
    return normalized_old.startswith(("paragraph ", "paragraphs ", "subsection ", "subsections "))


def _target_citation_matches(explicit_target_citation: str, target_address: str) -> bool:
    expected = _citation_from_target_address(target_address)
    return bool(expected) and _citation_key(explicit_target_citation) == _citation_key(expected)


def _text_substitution_scope(new_text: str) -> tuple[str, str]:
    normalized = " ".join(new_text.split()).strip(" ,;.")
    suffix = " in each place"
    if normalized.lower().endswith(suffix):
        return normalized[: -len(suffix)].strip(" ,;."), "inline_text_each_place"
    return normalized, "inline_text_single_occurrence"


def _omitting_substitution_scope(old_text: str, new_text: str) -> tuple[str, str, str]:
    cleaned_old = " ".join(old_text.split()).strip(" ,;.")
    cleaned_new = " ".join(new_text.split()).strip(" ,;.")
    if re.search(r"\bin each place\b|\bwherever\b", cleaned_old, re.IGNORECASE):
        cleaned_old = re.sub(r"\s+in each place(?: where it appears)?\b.*$", "", cleaned_old, flags=re.IGNORECASE)
        cleaned_new = re.sub(r"^in each case\s+", "", cleaned_new, flags=re.IGNORECASE)
        return cleaned_old.strip(" ,;."), cleaned_new.strip(" ,;."), "inline_text_each_place"
    return cleaned_old, cleaned_new, "inline_text_single_occurrence"


def _citation_from_target_address(target_address: str) -> str:
    parts = [part for part in target_address.split("/") if ":" in part]
    section = ""
    suffixes: list[str] = []
    for part in parts:
        kind, label = part.split(":", 1)
        if kind == "section":
            section = label
        elif kind in {"subsection", "paragraph"}:
            suffixes.append(label)
    if not section:
        return ""
    return "section " + section + "".join(f"({suffix})" for suffix in suffixes)


def _citation_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def write_evidence_jsonl(report: NZInstructionWorkQueueReport, path: Path) -> int:
    rows = [row.to_dict() for row in report.operation_evidence_rows()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main(args: Any) -> None:
    report = build_archived_work_instruction_workqueue(Path(args.db), args.work_id)
    queue_status = args.queue_status or ""
    if args.candidate_only:
        queue_status = "candidate"
    filtered_rows = report.filtered_rows(
        queue_status=queue_status,
        instruction_family=args.instruction_family,
        instruction_shape=args.instruction_shape,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
    )
    evidence_row_count: int | None = None
    if args.evidence_jsonl:
        evidence_rows = [row.to_dict() for row in report.operation_evidence_rows_for(filtered_rows)]
        output_path = Path(args.evidence_jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in evidence_rows),
            encoding="utf-8",
        )
        evidence_row_count = len(evidence_rows)
    if args.json:
        payload = report.to_jsonable(
            summary_only=args.summary_only,
            row_limit=args.limit,
            queue_status=queue_status,
            instruction_family=args.instruction_family,
            instruction_shape=args.instruction_shape,
            instruction_subfamily_status=args.instruction_subfamily_status,
            instruction_subfamily=args.instruction_subfamily,
            payload_structural_subfamily_status=args.payload_structural_subfamily_status,
            payload_structural_subfamily=args.payload_structural_subfamily,
        )
        if args.evidence_rows and not args.summary_only:
            selected_rows = filtered_rows if args.limit is None else filtered_rows[: args.limit]
            payload["evidence"] = {
                "operation_rows": [row.to_dict() for row in report.operation_evidence_rows_for(selected_rows)],
                "finding_rows": [],
            }
        if evidence_row_count is not None:
            payload["evidence_jsonl"] = {
                "path": args.evidence_jsonl,
                "rows": evidence_row_count,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if evidence_row_count is not None:
        print(f"wrote_evidence_rows={evidence_row_count} path={args.evidence_jsonl}")
    summary = report.summary()
    filters = _jsonable_filters(
        queue_status=queue_status,
        instruction_family=args.instruction_family,
        instruction_shape=args.instruction_shape,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(filtered_rows)} filters={filters} "
        f"queue_status_counts={summary['queue_status_counts']} "
        f"instruction_family_counts={summary['instruction_semantic_candidate_family_counts']} "
        f"instruction_shape_counts={summary['payload_instruction_shape_counts']} "
        f"structural_subfamily_status_counts={summary['payload_structural_subfamily_status_counts']}"
    )
    if args.summary_only:
        return
    for row in filtered_rows[: args.limit]:
        print(
            f"{row.row_id}\t{row.queue_status}\t{row.instruction_semantic_candidate_family or '-'}\t"
            f"{row.operation_family}\t{row.target_address or '-'}\t{row.amending_work_id or '-'}"
        )
    if len(filtered_rows) > args.limit:
        print(f"... {len(filtered_rows) - args.limit} more")
