"""Operation-witness surface for New Zealand history notes.

NZ consolidated XML exposes provision history notes with operation words,
dates, amending legislation, and amended-provision labels. This module turns
those history witnesses into typed, auditable rows. It does not lower them to
canonical effects or claim replay support.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow, CorpusOperationEvidenceRow, CorpusRowStatus
from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_boundary import TreePath, TreePathStep
from lawvm.core.semantic_types import FacetKind
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
    SCOPE_CONFIDENCE_FALLBACK,
    TARGET_RECOVERED,
    TARGET_REJECTED,
    TargetResolutionCandidate,
    TargetResolutionCoverage,
    TargetResolutionStatus,
)
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work
from lawvm.new_zealand.source_tree import NZHistoryWitness, NZSourceDocument, NZSourceNode, parse_archived_work_latest
from lawvm.core.quirks_disposition import QuirksDisposition, coerce_quirks_disposition


NZ_OPERATION_EFFECT_BLOCKED_RULE_ID = "nz_operation_surface_effect_lowering_not_implemented"

# Per-readiness-bucket distinct effect_blocking_rule_id mapping. AGENTS §1.10:
# every missing-mapping / unknown-key / blocked-state that would otherwise
# fall to a generic "lane not implemented" diagnostic MUST surface its own
# distinct named rule_id so a benchmark reading only effect_blocking_rule_id
# can attribute WHY lowering blocked without re-reading the readiness field.
# The legacy single constant ``NZ_OPERATION_EFFECT_BLOCKED_RULE_ID`` is
# preserved as the forward-compatible fallback for any future readiness
# bucket not in this map -- a new bucket never silently re-derives to a
# generic blocker, and surfaces as an explicitly-typed "not_implemented"
# diagnostic that the catalog already documents.
_EFFECT_BLOCKING_RULE_ID_BY_READINESS: dict[str, str] = {
    "ready_for_amending_act_payload_extraction": (
        "nz_operation_surface_effect_lowering_lane_unimplemented"
    ),
    "blocked_amending_work_resolved_unarchived": (
        "nz_operation_surface_effect_lowering_amending_work_unarchived"
    ),
    "blocked_non_structural_facet": (
        "nz_operation_surface_effect_lowering_non_structural_facet"
    ),
    "blocked_target_address_blocked_non_current_skeleton_node": (
        "nz_operation_surface_effect_lowering_target_address_non_current_skeleton_node"
    ),
    "blocked_target_hint_unparsed": (
        "nz_operation_surface_effect_lowering_target_hint_unparsed"
    ),
    "blocked_target_hint_missing": (
        "nz_operation_surface_effect_lowering_target_hint_missing"
    ),
    "blocked_citation_unparsed": (
        "nz_operation_surface_effect_lowering_citation_unparsed"
    ),
    "blocked_citation_missing": (
        "nz_operation_surface_effect_lowering_citation_missing"
    ),
    "blocked_operation_missing": (
        "nz_operation_surface_effect_lowering_operation_missing"
    ),
    "blocked_operation_unclassified": (
        "nz_operation_surface_effect_lowering_operation_unclassified"
    ),
    "blocked_editorial_change_non_canonical": (
        "nz_operation_surface_effect_lowering_editorial_change_non_canonical"
    ),
    "blocked_duplicate_source_path": (
        "nz_operation_surface_effect_lowering_duplicate_source_path"
    ),
    "blocked_same_label_rebirth_duplicate": (
        "nz_operation_surface_effect_lowering_same_label_rebirth_duplicate"
    ),
}


def _effect_blocking_rule_id_for_readiness(readiness_status: str) -> str:
    """Derive the per-row effect_blocking_rule_id from the lowering readiness.

    A benchmark reading only ``effect_blocking_rule_id`` MUST be able to
    attribute WHY the lowering blocked without re-reading the readiness
    field (AGENTS §1.10 -- distinct named diagnostic, no broad exception
    swallowing). The legacy single constant
    ``NZ_OPERATION_EFFECT_BLOCKED_RULE_ID`` is the forward-compatible
    fallback for an unrecognised readiness bucket so a new bucket never
    silently re-derives to a guessed blocker.
    """
    return _EFFECT_BLOCKING_RULE_ID_BY_READINESS.get(
        readiness_status, NZ_OPERATION_EFFECT_BLOCKED_RULE_ID
    )


def _effect_status_for_readiness(readiness_status: str) -> str:
    """Derive the per-row effect_status from the lowering readiness.

    Two honest families:

    * ``ready_for_amending_act_payload_extraction`` ->
      ``ready_for_lowering`` -- the row IS ready to lower; the lowering
      lane that consumes ready rows is itself unimplemented. Distinguishes
      "I'm ready" (3257 rows on the smoke corpus 2026-06-27) from "I'm
      blocked" (978 rows) -- previously both collapsed under the single
      legacy ``blocked_effect_lowering`` value.
    * ``blocked_*`` -> ``blocked_<reason>`` -- the row IS blocked at the
      readiness stage; the reason is preserved in the suffix.
    * Unknown readiness buckets -> legacy ``blocked_effect_lowering`` so a
      new bucket never silently re-derives a guessed status, surfacing as
      the cataloged legacy status instead.
    """
    if readiness_status == "ready_for_amending_act_payload_extraction":
        return "ready_for_lowering"
    if readiness_status.startswith("blocked_"):
        return readiness_status
    return "blocked_effect_lowering"

# Closed vocabulary for a parsed target hint (the amended-provision label parse).
NZTargetHintStatus = Literal[
    "missing",
    "document_facet",
    "attached_facet",
    "compound_target_unparsed",
    "parsed",
    "unparsed",
]
_KNOWN_OPERATION_FAMILIES = {
    "added",
    "amended",
    "brought into force",
    "editorial change",
    "expired",
    "inserted",
    "repealed",
    "replaced",
    "substituted",
}
_SECTION_TARGET_RE = re.compile(
    r"^sections?\s+(?P<section>[0-9A-Za-z]+)"
    r"(?P<components>(?:\([0-9A-Za-z]+\))*)"
    r"(?:\s+(?P<facet>heading))?$",
    re.IGNORECASE,
)
_COMPOUND_SECTION_TARGET_RE = re.compile(
    r"^sections?\s+(?P<section>[0-9A-Za-z]+)"
    r"(?P<components>(?:\([0-9A-Za-z]+\))*)"
    r"\s*(?:,|and|or|to)\s+.+$",
    re.IGNORECASE,
)
_SCHEDULE_TARGET_RE = re.compile(r"^schedules?\s+(?P<label>[0-9A-Za-z]+)", re.IGNORECASE)
_PART_TARGET_RE = re.compile(r"^parts?\s+(?P<label>[0-9A-Za-z]+)", re.IGNORECASE)


@dataclass(frozen=True)
class NZTargetHint:
    target_hint_status: NZTargetHintStatus
    kind: str = ""
    label: str = ""
    subsection: str = ""
    paragraphs: tuple[str, ...] = ()
    facet: str = ""
    raw: str = ""
    # Defined term carried by a definition-level note; resolves to the exact
    # ``def-para`` node inside the subsection rather than the coarse subsection.
    definition: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "target_hint_status": self.target_hint_status,
            "kind": self.kind,
            "label": self.label,
            "subsection": self.subsection,
            "paragraphs": list(self.paragraphs),
            "facet": self.facet,
            "raw": self.raw,
            "definition": self.definition,
        }


@dataclass(frozen=True)
class NZTargetAddressCandidate:
    target_address_status: str
    address: str = ""
    path: TreePath = ()
    special: str = ""
    blocking_rule_id: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "target_address_status": self.target_address_status,
            "address": self.address,
            "path": [list(part) for part in self.path],
            "special": self.special,
            "blocking_rule_id": self.blocking_rule_id,
        }


@dataclass(frozen=True)
class NZOperationWitnessRow:
    row_id: str
    source_path: tuple[str, ...]
    source_xml_id: str
    source_xml_path: str
    source_zone: str
    source_kind: str
    attached_node_xml_id: str
    attached_node_heading: str
    target_surface_status: str
    target_hint: NZTargetHint
    target_address_candidate: NZTargetAddressCandidate
    dependency_status: str
    lowering_readiness_status: str
    amended_provision: str
    operation_text: str
    operation_family: str
    operation_status: str
    amendment_date: str
    amendment_date_iso: str
    amending_work_id: str
    amending_legislation: str
    amending_provisions: tuple[str, ...]
    amending_provision_hrefs: tuple[str, ...]
    witness_text: str
    effect_status: str = "blocked_effect_lowering"
    effect_blocking_rule_id: str = NZ_OPERATION_EFFECT_BLOCKED_RULE_ID

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "source_path": list(self.source_path),
            "source_xml_id": self.source_xml_id,
            "source_xml_path": self.source_xml_path,
            "source_zone": self.source_zone,
            "source_kind": self.source_kind,
            "attached_node_xml_id": self.attached_node_xml_id,
            "attached_node_heading": self.attached_node_heading,
            "target_surface_status": self.target_surface_status,
            "target_hint": self.target_hint.to_jsonable(),
            "target_address_candidate": self.target_address_candidate.to_jsonable(),
            "dependency_status": self.dependency_status,
            "lowering_readiness_status": self.lowering_readiness_status,
            "amended_provision": self.amended_provision,
            "operation_text": self.operation_text,
            "operation_family": self.operation_family,
            "operation_status": self.operation_status,
            "amendment_date": self.amendment_date,
            "amendment_date_iso": self.amendment_date_iso,
            "amending_work_id": self.amending_work_id,
            "amending_legislation": self.amending_legislation,
            "amending_provisions": list(self.amending_provisions),
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "witness_text": self.witness_text,
            "effect_status": self.effect_status,
            "effect_blocking_rule_id": self.effect_blocking_rule_id,
        }


@dataclass(frozen=True)
class NZOperationSurfaceReport:
    work_id: str
    version_id: str
    xml_locator: str
    rows: tuple[NZOperationWitnessRow, ...]
    findings: tuple[dict[str, Any], ...]

    def summary(self) -> dict[str, Any]:
        status_counts = Counter(row.operation_status for row in self.rows)
        family_counts = Counter(row.operation_family for row in self.rows)
        target_counts = Counter(row.target_surface_status for row in self.rows)
        target_hint_status_counts = Counter(row.target_hint.target_hint_status for row in self.rows)
        target_hint_kind_counts = Counter(row.target_hint.kind or "__none__" for row in self.rows)
        target_address_status_counts = Counter(row.target_address_candidate.target_address_status for row in self.rows)
        dependency_counts = Counter(row.dependency_status for row in self.rows)
        amending_provision_href_counts = Counter(
            "present" if row.amending_provision_hrefs else "missing" for row in self.rows
        )
        readiness_counts = Counter(row.lowering_readiness_status for row in self.rows)
        effect_status_counts = Counter(row.effect_status for row in self.rows)
        effect_blocking_rule_id_counts = Counter(
            row.effect_blocking_rule_id for row in self.rows
        )
        return {
            "work_id": self.work_id,
            "version_id": self.version_id,
            "xml_locator": self.xml_locator,
            "rows": len(self.rows),
            "operation_status_counts": dict(sorted(status_counts.items())),
            "operation_family_counts": dict(sorted(family_counts.items())),
            "target_surface_status_counts": dict(sorted(target_counts.items())),
            "target_hint_status_counts": dict(sorted(target_hint_status_counts.items())),
            "target_hint_kind_counts": dict(sorted(target_hint_kind_counts.items())),
            "target_address_status_counts": dict(sorted(target_address_status_counts.items())),
            "dependency_status_counts": dict(sorted(dependency_counts.items())),
            "amending_provision_href_status_counts": dict(sorted(amending_provision_href_counts.items())),
            "lowering_readiness_status_counts": dict(sorted(readiness_counts.items())),
            # Per-row distinct effect_blocking_rule_id distribution (AGENTS §1.10
            # distinct-named-diagnostic + §2.9 typed-accounting). The legacy
            # single-rule_id pin ``effect_blocking_rule_id`` field (kept for
            # back-compat with consumers reading the work-summary) reflects the
            # DOMINANT rule_id across the work's rows; the per-row distribution
            # in ``effect_blocking_rule_id_counts`` is the new load-bearing
            # signal so a benchmark can distinguish ready-vs-blocked and which
            # readiness bucket each row blocked under.
            "effect_status_counts": dict(sorted(effect_status_counts.items())),
            "effect_blocking_rule_id_counts": dict(
                sorted(effect_blocking_rule_id_counts.items())
            ),
            "effect_lowering_status": "blocked",
            "effect_blocking_rule_id": (
                effect_blocking_rule_id_counts.most_common(1)[0][0]
                if effect_blocking_rule_id_counts
                else NZ_OPERATION_EFFECT_BLOCKED_RULE_ID
            ),
            "findings": len(self.findings),
        }

    def operation_evidence_rows(self) -> tuple[CorpusOperationEvidenceRow, ...]:
        return self.operation_evidence_rows_for(self.rows)

    def operation_evidence_rows_for(
        self,
        rows: tuple[NZOperationWitnessRow, ...],
    ) -> tuple[CorpusOperationEvidenceRow, ...]:
        findings_by_row_id: dict[str, list[dict[str, Any]]] = {}
        for finding in self.findings:
            row_id = str(finding.get("row_id", ""))
            if row_id:
                findings_by_row_id.setdefault(row_id, []).append(finding)
        return tuple(_operation_evidence_row(self, row, findings_by_row_id.get(row.row_id, ())) for row in rows)

    def finding_evidence_rows(self) -> tuple[CorpusFindingEvidenceRow, ...]:
        return tuple(_finding_evidence_row(self, finding) for finding in self.findings)

    def findings_for(self, rows: tuple[NZOperationWitnessRow, ...]) -> tuple[dict[str, Any], ...]:
        row_ids = {row.row_id for row in rows}
        return tuple(finding for finding in self.findings if str(finding.get("row_id", "")) in row_ids)

    def finding_evidence_rows_for(self, rows: tuple[NZOperationWitnessRow, ...]) -> tuple[CorpusFindingEvidenceRow, ...]:
        return tuple(_finding_evidence_row(self, finding) for finding in self.findings_for(rows))

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        include_evidence_rows: bool = False,
        operation_family: str = "",
        target_address_status: str = "",
        dependency_status: str = "",
        lowering_readiness_status: str = "",
        target_hint_status: str = "",
    ) -> dict[str, Any]:
        filtered_rows = self.filtered_rows(
            operation_family=operation_family,
            target_address_status=target_address_status,
            dependency_status=dependency_status,
            lowering_readiness_status=lowering_readiness_status,
            target_hint_status=target_hint_status,
        )
        filtered_findings = self.findings_for(filtered_rows)
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "operation_witness_surface",
            "truth_claim": "source_history_note_operation_witnesses",
            "replay_claims": False,
            "summary": self.summary(),
            "filters": _jsonable_filters(
                operation_family=operation_family,
                target_address_status=target_address_status,
                dependency_status=dependency_status,
                lowering_readiness_status=lowering_readiness_status,
                target_hint_status=target_hint_status,
            ),
            "filtered_summary": NZOperationSurfaceReport(
                work_id=self.work_id,
                version_id=self.version_id,
                xml_locator=self.xml_locator,
                rows=filtered_rows,
                findings=filtered_findings,
            ).summary(),
        }
        if summary_only:
            return payload
        rows = filtered_rows if row_limit is None else filtered_rows[:row_limit]
        payload["rows"] = [row.to_jsonable() for row in rows]
        payload["findings"] = list(self.findings_for(rows))
        if include_evidence_rows:
            payload["evidence"] = {
                "operation_rows": [row.to_dict() for row in self.operation_evidence_rows_for(rows)],
                "finding_rows": [row.to_dict() for row in self.finding_evidence_rows_for(rows)],
            }
        if row_limit is not None and len(filtered_rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(filtered_rows) - row_limit
        return payload

    def filtered_rows(
        self,
        *,
        operation_family: str = "",
        target_address_status: str = "",
        dependency_status: str = "",
        lowering_readiness_status: str = "",
        target_hint_status: str = "",
    ) -> tuple[NZOperationWitnessRow, ...]:
        filtered = self.rows
        if operation_family:
            filtered = tuple(row for row in filtered if row.operation_family == operation_family)
        if target_address_status:
            filtered = tuple(row for row in filtered if row.target_address_candidate.target_address_status == target_address_status)
        if dependency_status:
            filtered = tuple(row for row in filtered if row.dependency_status == dependency_status)
        if lowering_readiness_status:
            filtered = tuple(row for row in filtered if row.lowering_readiness_status == lowering_readiness_status)
        if target_hint_status:
            filtered = tuple(row for row in filtered if row.target_hint.target_hint_status == target_hint_status)
        return filtered


def _jsonable_filters(
    *,
    operation_family: str,
    target_address_status: str,
    dependency_status: str,
    lowering_readiness_status: str,
    target_hint_status: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "operation_family": operation_family,
            "target_address_status": target_address_status,
            "dependency_status": dependency_status,
            "lowering_readiness_status": lowering_readiness_status,
            "target_hint_status": target_hint_status,
        }.items()
        if value
    }


def build_operation_surface(
    document: NZSourceDocument,
    *,
    work_id: str = "",
    archived_dependency_work_ids: frozenset[str] = frozenset(),
) -> NZOperationSurfaceReport:
    rows: list[NZOperationWitnessRow] = []
    findings: list[dict[str, Any]] = []
    duplicate_path_statuses = _duplicate_source_path_statuses(document)
    for index, (node, source_path, source_kind, witness) in enumerate(_iter_history_sources(document), start=1):
        operation_family = classify_operation_family(witness.operation)
        operation_status = _operation_status(operation_family)
        dependency_status = _dependency_status(witness, archived_dependency_work_ids)
        target_status = _target_surface_status(
            witness,
            source_path=source_path,
            source_kind=source_kind,
            source_zone=node.source_zone if node is not None else "document",
            duplicate_path_statuses=duplicate_path_statuses,
        )
        target_hint = parse_target_hint(witness.amended_provision, defined_term=witness.defined_term)
        target_address_candidate = _target_address_candidate(
            target_surface_status=target_status,
            target_hint=target_hint,
            source_path=source_path,
        )
        readiness_status = _lowering_readiness_status(
            operation_status=operation_status,
            operation_family=operation_family,
            target_surface_status=target_status,
            target_hint=target_hint,
            target_address_candidate=target_address_candidate,
            dependency_status=dependency_status,
        )
        row = NZOperationWitnessRow(
            row_id=f"nz-opw-{index}",
            source_path=source_path,
            source_xml_id=witness.xml_id,
            source_xml_path=witness.xml_path,
            source_zone=_source_zone(witness.xml_path),
            source_kind=source_kind,
            attached_node_xml_id=node.xml_id if node is not None else "",
            attached_node_heading=node.heading if node is not None else "",
            target_surface_status=target_status,
            target_hint=target_hint,
            target_address_candidate=target_address_candidate,
            dependency_status=dependency_status,
            lowering_readiness_status=readiness_status,
            amended_provision=witness.amended_provision,
            operation_text=witness.operation,
            operation_family=operation_family,
            operation_status=operation_status,
            amendment_date=witness.amendment_date,
            amendment_date_iso=witness.amendment_date_iso,
            amending_work_id=witness.amending_work_id,
            amending_legislation=witness.amending_legislation,
            amending_provisions=witness.amending_provisions,
            amending_provision_hrefs=witness.amending_provision_hrefs,
            witness_text=witness.text,
            effect_status=_effect_status_for_readiness(readiness_status),
            effect_blocking_rule_id=_effect_blocking_rule_id_for_readiness(
                readiness_status
            ),
        )
        rows.append(row)
        if witness.recovery_rule_id:
            # Legacy verb recovery finding emission (AGENTS §2.1 -- a
            # heuristic that affects op-family classification needs a
            # stable rule_id + finding emission + strict-mode behavior +
            # synthetic+corpus regression). The recovery sourced this
            # witness's operation verb from a non-canonical XML element
            # (legacy double-<amended-provision> Shape A/D or the
            # non-standard <amending-instruction> Shape B); the op_family
            # was classified from the RECOVERED verb, not the canonical
            # ``<amending-operation>`` element. Non-blocking: recovery
            # is honest accounting, never a replay-blocker; downstream
            # consumers dispatch on operation_family (recovered == the
            # canonical family for the recovered verb).
            findings.append(
                _finding(
                    rule_id=witness.recovery_rule_id,
                    phase="P5",
                    family="operation_witness_surface",
                    reason=(
                        "history-note operation verb was sourced from a "
                        "legacy non-canonical XML element (the canonical "
                        "<amending-operation> element was absent); the "
                        "op_family was classified from the recovered verb"
                    ),
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=False,
                )
            )
        if operation_status != "classified":
            findings.append(
                _finding(
                    rule_id=f"nz_operation_surface_{operation_status}",
                    phase="P5",
                    family="operation_witness_surface",
                    reason=f"history-note operation is {operation_status}",
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=True,
                )
            )
        if target_address_candidate.target_address_status != "candidate":
            rule_id = target_address_candidate.blocking_rule_id or f"nz_target_address_{target_address_candidate.target_address_status}"
            reason = f"target address candidate is {target_address_candidate.target_address_status}"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    phase="P6",
                    family="target_address_candidate",
                    reason=reason,
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=True,
                    detail={
                        "target_resolution": _target_resolution_evidence(
                            row,
                            rule_id=rule_id,
                            phase="P6",
                            reason=reason,
                            resolution_status=TARGET_REJECTED,
                            blocking=True,
                            scope_confidence="",
                        )
                    },
                )
            )
        if target_status == "skeleton_duplicate_resolved" and target_address_candidate.target_address_status == "candidate":
            rule_id = "nz_target_address_skeleton_duplicate_resolved"
            reason = "source path duplicate is caused by non-current end skeleton nodes; primary node target kept"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    phase="P6",
                    family="target_resolution_recovery",
                    reason=reason,
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=False,
                    detail={
                        "target_resolution": _target_resolution_evidence(
                            row,
                            rule_id=rule_id,
                            phase="P6",
                            reason=reason,
                            resolution_status=TARGET_RECOVERED,
                            blocking=False,
                            scope_confidence=SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
                            candidate_reason="primary_non_skeleton_source_node",
                        )
                    },
                )
            )
        if target_hint.target_hint_status == "attached_facet" and target_address_candidate.target_address_status == "candidate":
            rule_id = "nz_target_address_attached_heading_from_context"
            reason = "history note targets the attached node heading by local context"
            findings.append(
                _finding(
                    rule_id=rule_id,
                    phase="P6",
                    family="target_resolution_recovery",
                    reason=reason,
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=False,
                    detail={
                        "target_resolution": _target_resolution_evidence(
                            row,
                            rule_id=rule_id,
                            phase="P6",
                            reason=reason,
                            resolution_status=TARGET_RECOVERED,
                            blocking=False,
                            scope_confidence=SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
                            candidate_reason="attached_heading_source_context",
                        )
                    },
                )
            )
        if dependency_status == "amending_work_resolved_unarchived":
            findings.append(
                _finding(
                    rule_id="nz_history_note_dependency_unarchived",
                    phase="acquisition",
                    family="operation_witness_surface",
                    reason="history-note amending work id is resolved but not archived locally",
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=False,
                )
            )
        if readiness_status != "ready_for_amending_act_payload_extraction":
            findings.append(
                _finding(
                    rule_id=f"nz_lowering_readiness_{readiness_status}",
                    phase="P6",
                    family="operation_witness_lowering_readiness",
                    reason=f"operation witness is {readiness_status}",
                    row_id=row.row_id,
                    source_xml_id=witness.xml_id,
                    blocking=True,
                )
            )
    return NZOperationSurfaceReport(
        work_id=work_id,
        version_id=document.version_id,
        xml_locator=document.xml_locator,
        rows=tuple(rows),
        findings=tuple(findings),
    )


def build_archived_work_operation_surface(db_path: Path, work_id: str) -> NZOperationSurfaceReport:
    document = parse_archived_work_latest(db_path, work_id)
    archived_dependency_work_ids = _archived_dependency_work_ids(db_path, document)
    return build_operation_surface(
        document,
        work_id=work_id,
        archived_dependency_work_ids=archived_dependency_work_ids,
    )


def classify_operation_family(operation: str) -> str:
    normalized = " ".join(operation.lower().split())
    if not normalized:
        return "__missing__"
    if normalized == "editorial changes":
        return "editorial change"
    if "brought into force" in normalized:
        return "brought into force"
    # 'revoked' is a NZ editorial synonym for 'repealed' (both surface in
    # `<amending-operation>` and history-note narratives). Downstream
    # consumers (effect_readiness, dry_run, instruction_workqueue) dispatch
    # on operation_family == 'repealed'; admitting the synonym here avoids
    # a __unclassified__ bucket that masks a real amend verb (AGENTS §1.10).
    # Verified on witness act_public_1955_37 @ 2024-06-05 nz-opw-106:
    # `Clause 3(c) : revoked , on 5 June 2024 , by section 209(2) of the
    # Whakatōhea Claims Settlement Act 2024`.
    if normalized == "revoked":
        return "repealed"
    if normalized in _KNOWN_OPERATION_FAMILIES:
        return normalized
    return "__unclassified__"


def parse_target_hint(amended_provision: str, *, defined_term: str = "") -> NZTargetHint:
    raw = " ".join(amended_provision.replace("\ufeff", "").split())
    normalized = raw.lower()
    definition = " ".join(defined_term.replace("\ufeff", "").split())
    if not raw:
        return NZTargetHint(target_hint_status="missing", raw=raw)
    if normalized in {"title", "long title"}:
        return NZTargetHint(target_hint_status="document_facet", kind="document", facet=normalized, raw=raw)
    if normalized == "heading":
        return NZTargetHint(target_hint_status="attached_facet", kind="attached_node", facet="heading", raw=raw)
    compound_section_match = _COMPOUND_SECTION_TARGET_RE.match(raw)
    if compound_section_match is not None:
        components = tuple(re.findall(r"\(([0-9A-Za-z]+)\)", compound_section_match.group("components") or ""))
        subsection = components[0] if components else ""
        paragraphs = components[1:] if len(components) > 1 else ()
        return NZTargetHint(
            target_hint_status="compound_target_unparsed",
            kind="section",
            label=compound_section_match.group("section"),
            subsection=subsection,
            paragraphs=paragraphs,
            raw=raw,
            definition=definition,
        )
    section_match = _SECTION_TARGET_RE.match(raw)
    if section_match is not None:
        facet = (section_match.group("facet") or "").lower()
        components = tuple(re.findall(r"\(([0-9A-Za-z]+)\)", section_match.group("components") or ""))
        subsection = components[0] if components else ""
        paragraphs = components[1:] if len(components) > 1 else ()
        # A definition note carries its defined term in addition to the section
        # reference. The term refines the target to the exact def-para; it is
        # only meaningful when no finer paragraph segment is already present and
        # the note is not a heading facet.
        carried_definition = definition if (definition and not paragraphs and not facet) else ""
        return NZTargetHint(
            target_hint_status="parsed",
            kind="section",
            label=section_match.group("section"),
            subsection=subsection,
            paragraphs=paragraphs,
            facet=facet,
            raw=raw,
            definition=carried_definition,
        )
    schedule_match = _SCHEDULE_TARGET_RE.match(raw)
    if schedule_match is not None:
        return NZTargetHint(target_hint_status="parsed", kind="schedule", label=schedule_match.group("label"), raw=raw)
    part_match = _PART_TARGET_RE.match(raw)
    if part_match is not None:
        return NZTargetHint(target_hint_status="parsed", kind="part", label=part_match.group("label"), raw=raw)
    return NZTargetHint(target_hint_status="unparsed", raw=raw)


def _operation_status(operation_family: str) -> str:
    if operation_family == "__missing__":
        return "missing"
    if operation_family == "__unclassified__":
        return "unclassified"
    return "classified"


def _lowering_readiness_status(
    *,
    operation_status: str,
    operation_family: str,
    target_surface_status: str,
    target_hint: NZTargetHint,
    target_address_candidate: NZTargetAddressCandidate,
    dependency_status: str,
) -> str:
    if operation_status != "classified":
        return f"blocked_operation_{operation_status}"
    if operation_family == "editorial change":
        return "blocked_editorial_change_non_canonical"
    if dependency_status != "amending_work_resolved_archived":
        return f"blocked_{dependency_status}"
    if target_surface_status == "duplicate_source_path":
        return "blocked_duplicate_source_path"
    if target_surface_status == "same_label_rebirth_duplicate":
        return "blocked_same_label_rebirth_duplicate"
    if target_surface_status == "document_level_facet":
        return "blocked_non_structural_facet"
    if target_hint.target_hint_status == "attached_facet" and target_address_candidate.target_address_status != "candidate":
        return "blocked_non_structural_facet"
    if target_hint.target_hint_status != "parsed" and target_address_candidate.target_address_status != "candidate":
        return f"blocked_target_hint_{target_hint.target_hint_status}"
    if target_address_candidate.target_address_status != "candidate":
        return f"blocked_target_address_{target_address_candidate.target_address_status}"
    return "ready_for_amending_act_payload_extraction"


def _target_address_candidate(
    *,
    target_surface_status: str,
    target_hint: NZTargetHint,
    source_path: tuple[str, ...],
) -> NZTargetAddressCandidate:
    if target_surface_status == "duplicate_source_path":
        return NZTargetAddressCandidate(
            target_address_status="blocked_duplicate_source_path",
            blocking_rule_id="nz_target_address_duplicate_source_path",
        )
    if target_surface_status == "same_label_rebirth_duplicate":
        return NZTargetAddressCandidate(
            target_address_status="blocked_same_label_rebirth_duplicate",
            blocking_rule_id="nz_target_address_same_label_rebirth_duplicate",
        )
    if target_surface_status == "non_current_skeleton_node":
        return NZTargetAddressCandidate(
            target_address_status="blocked_non_current_skeleton_node",
            blocking_rule_id="nz_target_address_non_current_skeleton_node",
        )
    if target_surface_status == "document_level_facet":
        return NZTargetAddressCandidate(
            target_address_status="blocked_document_level_facet",
            blocking_rule_id="nz_target_address_document_level_facet",
        )
    if target_hint.target_hint_status == "attached_facet" and target_hint.facet == "heading":
        return _attached_heading_address_candidate(source_path)
    if target_hint.target_hint_status != "parsed":
        return NZTargetAddressCandidate(
            target_address_status=f"blocked_target_hint_{target_hint.target_hint_status}",
            blocking_rule_id=f"nz_target_address_hint_{target_hint.target_hint_status}",
        )
    path: TreePath
    if target_hint.kind == "section":
        path_parts: list[TreePathStep] = [("section", target_hint.label)]
        if target_hint.subsection:
            path_parts.append(("subsection", target_hint.subsection))
        path_parts.extend(("paragraph", paragraph) for paragraph in target_hint.paragraphs)
        # A definition target refines the (sub)section to the exact def-para
        # identified by its defined term.
        if target_hint.definition:
            path_parts.append(("definition", target_hint.definition))
        path = tuple(path_parts)
    elif target_hint.kind == "schedule":
        path = (("schedule", target_hint.label),)
    elif target_hint.kind == "part":
        path = (("part", target_hint.label),)
    else:
        return NZTargetAddressCandidate(
            target_address_status=f"blocked_target_kind_{target_hint.kind or 'missing'}",
            blocking_rule_id="nz_target_address_unsupported_target_kind",
        )
    special = FacetKind.HEADING if target_hint.facet == "heading" else None
    address = LegalAddress(path=path, special=special)
    return NZTargetAddressCandidate(
        target_address_status="candidate",
        address=str(address),
        path=address.path,
        special=str(address.special or ""),
    )


def _attached_heading_address_candidate(source_path: tuple[str, ...]) -> NZTargetAddressCandidate:
    path_parts: list[TreePathStep] = []
    for segment in source_path:
        path_part = _address_part_from_source_segment(segment)
        if path_part is None:
            return NZTargetAddressCandidate(
                target_address_status="blocked_attached_heading_source_path",
                blocking_rule_id="nz_target_address_attached_heading_source_path_unparsed",
            )
        path_parts.append(path_part)
    if not path_parts:
        return NZTargetAddressCandidate(
            target_address_status="blocked_attached_heading_missing_source_path",
            blocking_rule_id="nz_target_address_attached_heading_missing_source_path",
        )
    address = LegalAddress(path=tuple(path_parts), special=FacetKind.HEADING)
    return NZTargetAddressCandidate(
        target_address_status="candidate",
        address=str(address),
        path=address.path,
        special=str(address.special or ""),
    )


def _address_part_from_source_segment(segment: str) -> tuple[str, str] | None:
    if ":" not in segment:
        return None
    kind, label = segment.split(":", 1)
    if not label:
        return None
    if kind == "prov":
        return ("section", label)
    if kind == "subprov":
        return ("subsection", label)
    if kind == "label-para":
        return ("paragraph", label)
    if kind == "def-para":
        return ("definition", label)
    if kind in {"part", "schedule"}:
        return (kind, label)
    return (kind, label)


def _iter_history_sources(
    document: NZSourceDocument,
) -> Iterable[tuple[NZSourceNode | None, tuple[str, ...], str, NZHistoryWitness]]:
    for witness in document.document_history:
        yield None, ("document",), "document", witness
    for node in document.nodes:
        for witness in node.history:
            yield node, node.path, node.kind, witness


def _target_surface_status(
    witness: NZHistoryWitness,
    *,
    source_path: tuple[str, ...],
    source_kind: str,
    source_zone: str,
    duplicate_path_statuses: Mapping[tuple[str, ...], str],
) -> str:
    if not witness.amended_provision:
        return "missing_target_text"
    if source_kind == "document":
        return "document_level_facet"
    if source_zone == "end_skeleton":
        return "non_current_skeleton_node"
    duplicate_status = duplicate_path_statuses.get(source_path, "")
    if duplicate_status == "non_current_skeleton_duplicate":
        return "skeleton_duplicate_resolved"
    if duplicate_status:
        return duplicate_status
    return "attached_structural_node"


def _dependency_status(
    witness: NZHistoryWitness,
    archived_dependency_work_ids: frozenset[str],
) -> str:
    if not witness.amending_work_id:
        return "citation_unparsed" if witness.amending_legislation else "citation_missing"
    if witness.amending_work_id in archived_dependency_work_ids:
        return "amending_work_resolved_archived"
    return "amending_work_resolved_unarchived"


def _source_zone(xml_path: str) -> str:
    if "/skeletons/" in xml_path:
        return "end_skeleton"
    if "/front/" in xml_path:
        return "front_history"
    if "/end/" in xml_path:
        return "end_history"
    if "/schedule" in xml_path:
        return "primary_schedule"
    if "/body/" in xml_path:
        return "primary_body"
    return "unknown"


def _duplicate_source_path_statuses(document: NZSourceDocument) -> Mapping[tuple[str, ...], str]:
    nodes_by_path: dict[tuple[str, ...], list[NZSourceNode]] = {}
    for node in document.nodes:
        nodes_by_path.setdefault(node.path, []).append(node)
    statuses: dict[tuple[str, ...], str] = {}
    for path, nodes in nodes_by_path.items():
        if len(nodes) <= 1:
            continue
        current_nodes = [node for node in nodes if node.source_zone != "end_skeleton"]
        skeleton_nodes = [node for node in nodes if node.source_zone == "end_skeleton"]
        if len(current_nodes) == 1 and skeleton_nodes:
            statuses[path] = "non_current_skeleton_duplicate"
        elif any(node.deletion_status for node in current_nodes) and any(not node.deletion_status for node in current_nodes):
            statuses[path] = "same_label_rebirth_duplicate"
        else:
            statuses[path] = "duplicate_source_path"
    return statuses


def _archived_dependency_work_ids(db_path: Path, document: NZSourceDocument) -> frozenset[str]:
    candidate_ids = {
        witness.amending_work_id
        for _node, _path, _kind, witness in _iter_history_sources(document)
        if witness.amending_work_id
    }
    archive = open_farchive(db_path)
    try:
        return frozenset(
            work_id
            for work_id in candidate_ids
            if latest_xml_locator_for_work(archive, work_id)[1]
        )
    finally:
        archive.close()


def _finding(
    *,
    rule_id: str,
    phase: str,
    family: str,
    reason: str,
    row_id: str,
    source_xml_id: str,
    blocking: bool,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        phase=phase,
        family=family,
        reason=reason,
        blocking=blocking,
        strict_disposition="block" if blocking else "warn",
        quirks_disposition=QuirksDisposition.SKIP_WITH_FINDING if blocking else QuirksDisposition.WARN,
        row_id=row_id,
        source_xml_id=source_xml_id,
        detail=detail,
    )


def _target_resolution_evidence(
    row: NZOperationWitnessRow,
    *,
    rule_id: str,
    phase: str,
    reason: str,
    resolution_status: TargetResolutionStatus,
    blocking: bool,
    scope_confidence: str,
    candidate_reason: str = "",
) -> dict[str, Any]:
    candidate = row.target_address_candidate
    candidates = (
        (
            TargetResolutionCandidate(
                target=candidate.address,
                reason=candidate_reason,
                detail={
                    "source_path": row.source_path,
                    "target_address_status": candidate.target_address_status,
                },
            ),
        )
        if candidate.address
        else ()
    )
    return TargetResolutionCoverage(
        rule_id=rule_id,
        phase=phase,
        reason=reason,
        resolution_status=resolution_status,
        source_target=_source_target_for_resolution(row),
        selected_target=candidate.address if resolution_status == TARGET_RECOVERED else "",
        candidate_count=max(1, len(candidates)) if resolution_status == TARGET_RECOVERED else len(candidates),
        candidates=candidates,
        scope_confidence=scope_confidence or (SCOPE_CONFIDENCE_FALLBACK if resolution_status == TARGET_RECOVERED else ""),
        blocking=blocking,
        strict_disposition="block" if blocking else "warn",
        quirks_disposition=QuirksDisposition.SKIP_WITH_FINDING if blocking else QuirksDisposition.WARN,
        detail={
            "jurisdiction_status": candidate.target_address_status,
            "target_surface_status": row.target_surface_status,
            "target_hint_status": row.target_hint.target_hint_status,
            "target_hint_kind": row.target_hint.kind,
            "source_path": row.source_path,
            "source_xml_path": row.source_xml_path,
        },
    ).to_diagnostic_detail()


def _source_target_for_resolution(row: NZOperationWitnessRow) -> str:
    return row.amended_provision or "/".join(row.source_path) or row.source_xml_path or row.row_id


def _operation_evidence_row(
    report: NZOperationSurfaceReport,
    row: NZOperationWitnessRow,
    row_findings: Iterable[dict[str, Any]],
) -> CorpusOperationEvidenceRow:
    finding_ids = tuple(_finding_id(finding) for finding in row_findings)
    source_artifact_id = report.version_id or report.xml_locator or report.work_id or "new_zealand_operation_surface"
    return CorpusOperationEvidenceRow(
        row_id=row.row_id,
        frontend_id="new_zealand",
        source_artifact_id=source_artifact_id,
        source_unit_id=row.source_xml_id,
        source_locator=row.source_xml_path,
        effect_family=row.operation_family,
        canonical_family="",
        original_target=row.amended_provision,
        resolved_target="/".join(row.source_path),
        evidence_status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD_WITNESS_ONLY,
        finding_ids=finding_ids,
        detail={
            "reason": row.effect_blocking_rule_id,
            "operation_status": row.operation_status,
            "target_surface_status": row.target_surface_status,
            "target_hint_status": row.target_hint.target_hint_status,
            "target_address_status": row.target_address_candidate.target_address_status,
            "target_address": row.target_address_candidate.address,
            "dependency_status": row.dependency_status,
            "lowering_readiness_status": row.lowering_readiness_status,
            "amending_work_id": row.amending_work_id,
            "amendment_date": row.amendment_date,
            "amendment_date_iso": row.amendment_date_iso,
            "amending_provision_hrefs": row.amending_provision_hrefs,
        },
    )


def _finding_evidence_row(report: NZOperationSurfaceReport, finding: dict[str, Any]) -> CorpusFindingEvidenceRow:
    row_id = str(finding.get("row_id", ""))
    source_artifact_id = report.version_id or report.xml_locator or report.work_id or "new_zealand_operation_surface"
    return CorpusFindingEvidenceRow(
        finding_id=_finding_id(finding),
        frontend_id="new_zealand",
        family=str(finding.get("family", "operation_witness_surface")),
        rule_id=str(finding.get("rule_id", "")),
        phase=str(finding.get("phase", "")),
        message=str(finding.get("reason", "")),
        source_artifact_id=source_artifact_id,
        source_unit_id=str(finding.get("source_xml_id", "")),
        related_row_ids=(row_id,) if row_id else (),
        blocking=bool(finding.get("blocking", False)),
        strict_disposition=str(finding.get("strict_disposition", "")),
        quirks_disposition=coerce_quirks_disposition(finding.get("quirks_disposition", QuirksDisposition.UNSET)),
        evidence={
            "row_id": row_id,
            "source_xml_id": str(finding.get("source_xml_id", "")),
        },
    )


def _finding_id(finding: dict[str, Any]) -> str:
    row_id = str(finding.get("row_id", ""))
    rule_id = str(finding.get("rule_id", ""))
    if row_id and rule_id:
        return f"{row_id}:{rule_id}"
    return rule_id or row_id


def write_evidence_jsonl(report: NZOperationSurfaceReport, path: Path) -> int:
    rows = [row.to_dict() for row in report.operation_evidence_rows()]
    rows.extend(row.to_dict() for row in report.finding_evidence_rows())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main(args: Any) -> None:
    report = build_archived_work_operation_surface(Path(args.db), args.work_id)
    filtered_rows = report.filtered_rows(
        operation_family=args.operation_family,
        target_address_status=args.target_address_status,
        dependency_status=args.dependency_status,
        lowering_readiness_status=args.lowering_readiness_status,
        target_hint_status=args.target_hint_status,
    )
    evidence_row_count: int | None = None
    if args.evidence_jsonl:
        evidence_rows = [row.to_dict() for row in report.operation_evidence_rows_for(filtered_rows)]
        evidence_rows.extend(row.to_dict() for row in report.finding_evidence_rows_for(filtered_rows))
        output_path = Path(args.evidence_jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in evidence_rows),
            encoding="utf-8",
        )
        evidence_row_count = len(evidence_rows)
    if args.json:
        row_limit = None if args.summary_only else args.limit
        payload = report.to_jsonable(
            summary_only=args.summary_only,
            row_limit=row_limit,
            include_evidence_rows=args.evidence_rows,
            operation_family=args.operation_family,
            target_address_status=args.target_address_status,
            dependency_status=args.dependency_status,
            lowering_readiness_status=args.lowering_readiness_status,
            target_hint_status=args.target_hint_status,
        )
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
        operation_family=args.operation_family,
        target_address_status=args.target_address_status,
        dependency_status=args.dependency_status,
        lowering_readiness_status=args.lowering_readiness_status,
        target_hint_status=args.target_hint_status,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(filtered_rows)} filters={filters} "
        f"operation_status_counts={summary['operation_status_counts']}"
    )
    print(f"operation_family_counts={summary['operation_family_counts']}")
    print(f"lowering_readiness_status_counts={summary['lowering_readiness_status_counts']}")
    print(f"effect_status_counts={summary['effect_status_counts']}")
    print(f"effect_blocking_rule_id_counts={summary['effect_blocking_rule_id_counts']}")
    print(f"effect_blocking_rule_id={summary['effect_blocking_rule_id']}")
    if args.summary_only:
        return
    for row in filtered_rows[: args.limit]:
        print(
            f"{row.row_id}\t{'/'.join(row.source_path)}\t{row.operation_family}\t"
            f"{row.amended_provision or '-'}\t{row.amending_work_id or '-'}"
        )
    if len(filtered_rows) > args.limit:
        print(f"... {len(filtered_rows) - args.limit} more")
