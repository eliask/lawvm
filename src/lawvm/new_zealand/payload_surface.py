"""Payload-witness surface for New Zealand history-note operations.

This module links NZ operation-witness rows to archived amending-act XML using
the source-provided ``amending-provision`` hrefs. It is evidence extraction, not
canonical effect lowering or replay.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode, parse_nz_source_document


@dataclass(frozen=True)
class NZPayloadNodeWitness:
    xml_id: str
    path: tuple[str, ...]
    kind: str
    label: str
    heading: str
    text: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "xml_id": self.xml_id,
            "path": list(self.path),
            "kind": self.kind,
            "label": self.label,
            "heading": self.heading,
            "text": self.text,
        }


@dataclass(frozen=True)
class NZPayloadWitnessRow:
    row_id: str
    operation_row_id: str
    operation_family: str
    operation_lowering_readiness_status: str
    operation_target_surface_status: str
    operation_target_hint_status: str
    operation_target_address_status: str
    operation_target_blocking_rule_id: str
    lowering_readiness_status: str
    amending_work_id: str
    amending_provision_hrefs: tuple[str, ...]
    payload_status: str
    payload_role: str = ""
    payload_semantics_status: str = ""
    payload_instruction_shape: str = ""
    payload_instruction_safety: str = ""
    matches: tuple[NZPayloadNodeWitness, ...] = ()
    blocking_rule_id: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "operation_row_id": self.operation_row_id,
            "operation_family": self.operation_family,
            "operation_lowering_readiness_status": self.operation_lowering_readiness_status,
            "operation_target_surface_status": self.operation_target_surface_status,
            "operation_target_hint_status": self.operation_target_hint_status,
            "operation_target_address_status": self.operation_target_address_status,
            "operation_target_blocking_rule_id": self.operation_target_blocking_rule_id,
            "lowering_readiness_status": self.lowering_readiness_status,
            "amending_work_id": self.amending_work_id,
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "payload_status": self.payload_status,
            "payload_role": self.payload_role,
            "payload_semantics_status": self.payload_semantics_status,
            "payload_instruction_shape": self.payload_instruction_shape,
            "payload_instruction_safety": self.payload_instruction_safety,
            "matches": [match.to_jsonable() for match in self.matches],
            "blocking_rule_id": self.blocking_rule_id,
        }


@dataclass(frozen=True)
class NZPayloadSurfaceReport:
    work_id: str
    operation_version_id: str
    rows: tuple[NZPayloadWitnessRow, ...]

    def summary(self) -> dict[str, Any]:
        status_counts = Counter(row.payload_status for row in self.rows)
        role_counts = Counter(row.payload_role or "__none__" for row in self.rows)
        semantics_counts = Counter(row.payload_semantics_status or "__none__" for row in self.rows)
        instruction_shape_counts = Counter(row.payload_instruction_shape or "__none__" for row in self.rows)
        instruction_safety_counts = Counter(row.payload_instruction_safety or "__none__" for row in self.rows)
        family_counts = Counter(row.operation_family for row in self.rows)
        operation_readiness_counts = Counter(row.operation_lowering_readiness_status for row in self.rows)
        operation_target_address_counts = Counter(row.operation_target_address_status for row in self.rows)
        return {
            "work_id": self.work_id,
            "operation_version_id": self.operation_version_id,
            "rows": len(self.rows),
            "payload_status_counts": dict(sorted(status_counts.items())),
            "payload_role_counts": dict(sorted(role_counts.items())),
            "payload_semantics_status_counts": dict(sorted(semantics_counts.items())),
            "payload_instruction_shape_counts": dict(sorted(instruction_shape_counts.items())),
            "payload_instruction_safety_counts": dict(sorted(instruction_safety_counts.items())),
            "operation_family_counts": dict(sorted(family_counts.items())),
            "operation_lowering_readiness_status_counts": dict(sorted(operation_readiness_counts.items())),
            "operation_target_address_status_counts": dict(sorted(operation_target_address_counts.items())),
            "payload_found": sum(1 for row in self.rows if row.payload_status == "payload_found"),
            "replay_claims": False,
            "effect_lowering_claims": False,
            "enacted_payload_claims": False,
        }

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        payload_status: str = "",
        operation_family: str = "",
        instruction_shape: str = "",
        instruction_safety: str = "",
    ) -> dict[str, Any]:
        filtered_rows = _filter_rows(
            self.rows,
            payload_status=payload_status,
            operation_family=operation_family,
            instruction_shape=instruction_shape,
            instruction_safety=instruction_safety,
        )
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "payload_witness_surface",
            "truth_claim": "archived_amending_act_payload_witnesses",
            "replay_claims": False,
            "effect_lowering_claims": False,
            "enacted_payload_claims": False,
            "summary": self.summary(),
            "filters": _jsonable_filters(
                payload_status=payload_status,
                operation_family=operation_family,
                instruction_shape=instruction_shape,
                instruction_safety=instruction_safety,
            ),
            "filtered_summary": NZPayloadSurfaceReport(
                work_id=self.work_id,
                operation_version_id=self.operation_version_id,
                rows=filtered_rows,
            ).summary(),
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
        payload_status: str = "",
        operation_family: str = "",
        instruction_shape: str = "",
        instruction_safety: str = "",
    ) -> tuple[NZPayloadWitnessRow, ...]:
        return _filter_rows(
            self.rows,
            payload_status=payload_status,
            operation_family=operation_family,
            instruction_shape=instruction_shape,
            instruction_safety=instruction_safety,
        )


def _jsonable_filters(
    *,
    payload_status: str,
    operation_family: str,
    instruction_shape: str,
    instruction_safety: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "payload_status": payload_status,
            "operation_family": operation_family,
            "instruction_shape": instruction_shape,
            "instruction_safety": instruction_safety,
        }.items()
        if value
    }


def build_payload_surface(
    operation_surface: NZOperationSurfaceReport,
    *,
    dependency_documents: Mapping[str, NZSourceDocument],
) -> NZPayloadSurfaceReport:
    rows: list[NZPayloadWitnessRow] = []
    node_indexes = {work_id: _node_index(document) for work_id, document in dependency_documents.items()}
    for index, operation_row in enumerate(operation_surface.rows, start=1):
        status = "payload_found"
        blocking_rule_id = ""
        payload_role = ""
        payload_semantics_status = "payload_witness_not_available"
        payload_instruction_shape = ""
        payload_instruction_safety = ""
        matches: tuple[NZPayloadNodeWitness, ...] = ()
        if operation_row.lowering_readiness_status != "ready_for_amending_act_payload_extraction":
            status = "blocked_operation_not_payload_ready"
            blocking_rule_id = "nz_payload_operation_not_payload_ready"
        elif not operation_row.amending_work_id or operation_row.amending_work_id not in dependency_documents:
            status = "blocked_dependency_unarchived"
            blocking_rule_id = "nz_payload_dependency_unarchived"
        elif not operation_row.amending_provision_hrefs:
            status = "blocked_payload_href_missing"
            blocking_rule_id = "nz_payload_href_missing"
        else:
            index_by_xml_id = node_indexes[operation_row.amending_work_id]
            found = tuple(
                _payload_node_witness(node)
                for href in operation_row.amending_provision_hrefs
                for node in (index_by_xml_id.get(href),)
                if node is not None
            )
            if len(found) != len(operation_row.amending_provision_hrefs):
                status = "blocked_payload_href_not_found"
                blocking_rule_id = "nz_payload_href_not_found"
            matches = found
            if status == "payload_found":
                payload_role = "amending_provision_witness"
                payload_semantics_status = _payload_semantics_status(operation_row.operation_family)
                payload_instruction_shape = _payload_instruction_shape(found)
                payload_instruction_safety = _payload_instruction_safety(payload_instruction_shape)
        rows.append(
            NZPayloadWitnessRow(
                row_id=f"nz-payload-{index}",
                operation_row_id=operation_row.row_id,
                operation_family=operation_row.operation_family,
                operation_lowering_readiness_status=operation_row.lowering_readiness_status,
                operation_target_surface_status=operation_row.target_surface_status,
                operation_target_hint_status=operation_row.target_hint.status,
                operation_target_address_status=operation_row.target_address_candidate.status,
                operation_target_blocking_rule_id=operation_row.target_address_candidate.blocking_rule_id,
                lowering_readiness_status=operation_row.lowering_readiness_status,
                amending_work_id=operation_row.amending_work_id,
                amending_provision_hrefs=operation_row.amending_provision_hrefs,
                payload_status=status,
                payload_role=payload_role,
                payload_semantics_status=payload_semantics_status,
                payload_instruction_shape=payload_instruction_shape,
                payload_instruction_safety=payload_instruction_safety,
                matches=matches,
                blocking_rule_id=blocking_rule_id,
            )
        )
    return NZPayloadSurfaceReport(
        work_id=operation_surface.work_id,
        operation_version_id=operation_surface.version_id,
        rows=tuple(rows),
    )


def build_archived_work_payload_surface(db_path: Path, work_id: str) -> NZPayloadSurfaceReport:
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    dependency_work_ids = {
        row.amending_work_id
        for row in operation_surface.rows
        if row.amending_work_id and row.lowering_readiness_status == "ready_for_amending_act_payload_extraction"
    }
    dependency_documents: dict[str, NZSourceDocument] = {}
    archive = open_farchive(db_path)
    try:
        for dependency_work_id in sorted(dependency_work_ids):
            _version_id, xml_locator = latest_xml_locator_for_work(archive, dependency_work_id)
            if not xml_locator:
                continue
            data = archive.get(xml_locator)
            if data is None:
                continue
            dependency_documents[dependency_work_id] = parse_nz_source_document(
                data,
                xml_locator=xml_locator,
                version_id=_version_id,
            )
    finally:
        archive.close()
    return build_payload_surface(operation_surface, dependency_documents=dependency_documents)


def _node_index(document: NZSourceDocument) -> dict[str, NZSourceNode]:
    return {node.xml_id: node for node in document.nodes if node.xml_id}


def _payload_node_witness(node: NZSourceNode) -> NZPayloadNodeWitness:
    return NZPayloadNodeWitness(
        xml_id=node.xml_id,
        path=node.path,
        kind=node.kind,
        label=node.label,
        heading=node.heading,
        text=_payload_body_text(node),
    )


def _payload_body_text(node: NZSourceNode) -> str:
    text = node.text
    for prefix in (node.label, node.heading):
        if prefix and text == prefix:
            return ""
        if prefix and text.startswith(prefix + " "):
            text = text[len(prefix) + 1 :].lstrip()
    return text


def _filter_rows(
    rows: tuple[NZPayloadWitnessRow, ...],
    *,
    payload_status: str = "",
    operation_family: str = "",
    instruction_shape: str = "",
    instruction_safety: str = "",
) -> tuple[NZPayloadWitnessRow, ...]:
    filtered = rows
    if payload_status:
        filtered = tuple(row for row in filtered if row.payload_status == payload_status)
    if operation_family:
        filtered = tuple(row for row in filtered if row.operation_family == operation_family)
    if instruction_shape:
        filtered = tuple(row for row in filtered if row.payload_instruction_shape == instruction_shape)
    if instruction_safety:
        filtered = tuple(row for row in filtered if row.payload_instruction_safety == instruction_safety)
    return filtered


def _payload_semantics_status(operation_family: str) -> str:
    if operation_family == "repealed":
        return "operation_witness_sufficient_no_enacted_payload_required"
    return "amending_provision_witness_not_enacted_payload"


def _payload_instruction_shape(matches: tuple[NZPayloadNodeWitness, ...]) -> str:
    if not matches:
        return ""
    text = " ".join(match.text for match in matches)
    normalized = " ".join(text.lower().split())
    if not normalized or len(normalized.split()) <= 3:
        return "empty_or_stub"
    if "schedule" in normalized and any(word in normalized for word in ("amend", "set out", "indicated", "specified")):
        return "schedule_indirection"
    if "this section" in normalized and any(
        word in normalized for word in ("amends", "amended", "inserted", "substituted", "repealed")
    ):
        return "retrospective_incorporated_note"
    if "is amended by" in normalized or "are amended by" in normalized:
        return "direct_amended_by_instruction"
    if "insert" in normalized:
        return "direct_insert_instruction"
    if any(word in normalized for word in ("substitut", "replac")):
        return "direct_substitute_replace_instruction"
    if any(word in normalized for word in ("repealing", "repealed", "repeal")):
        return "direct_repeal_replace_instruction"
    return "other_instruction"


def _payload_instruction_safety(instruction_shape: str) -> str:
    if instruction_shape in {
        "direct_amended_by_instruction",
        "direct_insert_instruction",
        "direct_repeal_replace_instruction",
        "direct_substitute_replace_instruction",
    }:
        return "candidate_only_semantic_classification"
    if instruction_shape == "retrospective_incorporated_note":
        return "review_retrospective_incorporated_note"
    if instruction_shape == "schedule_indirection":
        return "unsafe_schedule_or_omnibus_indirection"
    if instruction_shape in {"empty_or_stub", "other_instruction"}:
        return "unsafe_opaque_or_unclassified"
    return ""


def main(args: Any) -> None:
    report = build_archived_work_payload_surface(Path(args.db), args.work_id)
    if args.json:
        print(
            json.dumps(
                report.to_jsonable(
                    summary_only=args.summary_only,
                    row_limit=args.limit,
                    payload_status=args.payload_status,
                    operation_family=args.operation_family,
                    instruction_shape=args.instruction_shape,
                    instruction_safety=args.instruction_safety,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    summary = report.summary()
    rows = report.filtered_rows(
        payload_status=args.payload_status,
        operation_family=args.operation_family,
        instruction_shape=args.instruction_shape,
        instruction_safety=args.instruction_safety,
    )
    filters = _jsonable_filters(
        payload_status=args.payload_status,
        operation_family=args.operation_family,
        instruction_shape=args.instruction_shape,
        instruction_safety=args.instruction_safety,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(rows)} filters={filters} "
        f"payload_status_counts={summary['payload_status_counts']}"
    )
    if args.summary_only:
        return
    for row in rows[: args.limit]:
        print(
            f"{row.row_id}\t{row.operation_row_id}\t{row.payload_status}\t"
            f"{row.amending_work_id or '-'}\t{','.join(row.amending_provision_hrefs) or '-'}"
        )
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more")
