"""Payload-witness surface for New Zealand history-note operations.

This module links NZ operation-witness rows to archived amending-act XML using
the source-provided ``amending-provision`` hrefs. It is evidence extraction, not
canonical effect lowering or replay.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, assert_never

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import latest_xml_locator_for_work
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface
from lawvm.new_zealand.source_tree import (
    NZAmendInstruction,
    NZSourceDocument,
    NZSourceNode,
    parse_nz_source_document,
)


class NZPayloadStatus(StrEnum):
    """Closed payload-witness extraction status for an NZ operation row.

    A ``StrEnum`` (not a bare ``str``) so status comparisons are member-vs-member
    and survive renames as a type error rather than a silent string mismatch.
    Members subclass ``str`` and their ``value`` equals the legacy wire string,
    so ``NZPayloadStatus.PAYLOAD_FOUND == "payload_found"`` is ``True`` and JSON
    serialization (including ``Counter`` keys) stays byte-identical.

    ``BLOCKED_PAYLOAD_SURFACE_MISSING`` is produced downstream in
    ``effect_readiness`` when no payload row exists for an operation row; it is
    included here so the vocabulary has a single closed home.
    """

    PAYLOAD_FOUND = "payload_found"
    BLOCKED_OPERATION_NOT_PAYLOAD_READY = "blocked_operation_not_payload_ready"
    BLOCKED_DEPENDENCY_UNARCHIVED = "blocked_dependency_unarchived"
    BLOCKED_PAYLOAD_HREF_MISSING = "blocked_payload_href_missing"
    BLOCKED_PAYLOAD_HREF_NOT_FOUND = "blocked_payload_href_not_found"
    BLOCKED_PAYLOAD_SURFACE_MISSING = "blocked_payload_surface_missing"


class NZPayloadRole(StrEnum):
    """Closed payload-witness role. ``NONE`` (``""``) marks an absent role.

    The empty member is falsy, so the ``role or "__none__"`` summary idiom and
    ``""`` JSON serialization stay byte-identical.
    """

    NONE = ""
    AMENDING_PROVISION_WITNESS = "amending_provision_witness"


class NZPayloadSemanticsStatus(StrEnum):
    """Closed payload-semantics status for an NZ operation row."""

    PAYLOAD_WITNESS_NOT_AVAILABLE = "payload_witness_not_available"
    OPERATION_WITNESS_SUFFICIENT_NO_ENACTED_PAYLOAD_REQUIRED = (
        "operation_witness_sufficient_no_enacted_payload_required"
    )
    AMENDING_PROVISION_WITNESS_NOT_ENACTED_PAYLOAD = "amending_provision_witness_not_enacted_payload"


class NZPayloadInstructionShape(StrEnum):
    """Closed instruction-shape classification of a payload witness.

    ``NONE`` (``""``) marks "no shape" (e.g. no matches). The empty member is
    falsy so the ``shape or "__none__"`` summary idiom stays byte-identical.
    """

    NONE = ""
    EMPTY_OR_STUB = "empty_or_stub"
    SCHEDULE_INDIRECTION = "schedule_indirection"
    RETROSPECTIVE_INCORPORATED_NOTE = "retrospective_incorporated_note"
    DIRECT_AMENDED_BY_INSTRUCTION = "direct_amended_by_instruction"
    DIRECT_INSERT_INSTRUCTION = "direct_insert_instruction"
    DIRECT_SUBSTITUTE_REPLACE_INSTRUCTION = "direct_substitute_replace_instruction"
    DIRECT_REPEAL_REPLACE_INSTRUCTION = "direct_repeal_replace_instruction"
    OTHER_INSTRUCTION = "other_instruction"


class NZPayloadInstructionSafety(StrEnum):
    """Closed instruction-safety classification, dispatched from shape.

    ``NONE`` (``""``) marks "no safety class" (no shape).
    """

    NONE = ""
    CANDIDATE_ONLY_SEMANTIC_CLASSIFICATION = "candidate_only_semantic_classification"
    REVIEW_RETROSPECTIVE_INCORPORATED_NOTE = "review_retrospective_incorporated_note"
    UNSAFE_SCHEDULE_OR_OMNIBUS_INDIRECTION = "unsafe_schedule_or_omnibus_indirection"
    UNSAFE_OPAQUE_OR_UNCLASSIFIED = "unsafe_opaque_or_unclassified"


@dataclass(frozen=True)
class NZPayloadNodeWitness:
    xml_id: str
    path: tuple[str, ...]
    kind: str
    label: str
    heading: str
    text: str
    # Typed amending instructions read from this node's ``<amend.in>``/citation
    # payload. Preferred over the flattened ``text`` prose by the instruction
    # workqueue so multi-instruction provisions split into N keyed candidates.
    amend_instructions: tuple[NZAmendInstruction, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "xml_id": self.xml_id,
            "path": list(self.path),
            "kind": self.kind,
            "label": self.label,
            "heading": self.heading,
            "text": self.text,
            "amend_instructions": [row.to_jsonable() for row in self.amend_instructions],
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
    payload_status: NZPayloadStatus
    payload_role: NZPayloadRole = NZPayloadRole.NONE
    payload_semantics_status: NZPayloadSemanticsStatus = NZPayloadSemanticsStatus.PAYLOAD_WITNESS_NOT_AVAILABLE
    payload_instruction_shape: NZPayloadInstructionShape = NZPayloadInstructionShape.NONE
    payload_instruction_safety: NZPayloadInstructionSafety = NZPayloadInstructionSafety.NONE
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
            "payload_found": sum(1 for row in self.rows if row.payload_status == NZPayloadStatus.PAYLOAD_FOUND),
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
        status = NZPayloadStatus.PAYLOAD_FOUND
        blocking_rule_id = ""
        payload_role = NZPayloadRole.NONE
        payload_semantics_status = NZPayloadSemanticsStatus.PAYLOAD_WITNESS_NOT_AVAILABLE
        payload_instruction_shape = NZPayloadInstructionShape.NONE
        payload_instruction_safety = NZPayloadInstructionSafety.NONE
        matches: tuple[NZPayloadNodeWitness, ...] = ()
        if operation_row.lowering_readiness_status != "ready_for_amending_act_payload_extraction":
            status = NZPayloadStatus.BLOCKED_OPERATION_NOT_PAYLOAD_READY
            blocking_rule_id = "nz_payload_operation_not_payload_ready"
        elif not operation_row.amending_work_id or operation_row.amending_work_id not in dependency_documents:
            status = NZPayloadStatus.BLOCKED_DEPENDENCY_UNARCHIVED
            blocking_rule_id = "nz_payload_dependency_unarchived"
        elif not operation_row.amending_provision_hrefs:
            status = NZPayloadStatus.BLOCKED_PAYLOAD_HREF_MISSING
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
                status = NZPayloadStatus.BLOCKED_PAYLOAD_HREF_NOT_FOUND
                blocking_rule_id = "nz_payload_href_not_found"
            matches = found
            if status == NZPayloadStatus.PAYLOAD_FOUND:
                payload_role = NZPayloadRole.AMENDING_PROVISION_WITNESS
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
                operation_target_hint_status=operation_row.target_hint.target_hint_status,
                operation_target_address_status=operation_row.target_address_candidate.target_address_status,
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


def build_archived_work_payload_surface(
    db_path: Path,
    work_id: str,
    *,
    operation_surface: NZOperationSurfaceReport | None = None,
) -> NZPayloadSurfaceReport:
    if operation_surface is None:
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
        amend_instructions=node.amend_instructions,
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


def _payload_semantics_status(operation_family: str) -> NZPayloadSemanticsStatus:
    if operation_family == "repealed":
        return NZPayloadSemanticsStatus.OPERATION_WITNESS_SUFFICIENT_NO_ENACTED_PAYLOAD_REQUIRED
    return NZPayloadSemanticsStatus.AMENDING_PROVISION_WITNESS_NOT_ENACTED_PAYLOAD


def _payload_instruction_shape(matches: tuple[NZPayloadNodeWitness, ...]) -> NZPayloadInstructionShape:
    if not matches:
        return NZPayloadInstructionShape.NONE
    text = " ".join(match.text for match in matches)
    normalized = " ".join(text.lower().split())
    if not normalized or len(normalized.split()) <= 3:
        return NZPayloadInstructionShape.EMPTY_OR_STUB
    if "schedule" in normalized and any(word in normalized for word in ("amend", "set out", "indicated", "specified")):
        return NZPayloadInstructionShape.SCHEDULE_INDIRECTION
    if "this section" in normalized and any(
        word in normalized for word in ("amends", "amended", "inserted", "substituted", "repealed")
    ):
        return NZPayloadInstructionShape.RETROSPECTIVE_INCORPORATED_NOTE
    if "is amended by" in normalized or "are amended by" in normalized:
        return NZPayloadInstructionShape.DIRECT_AMENDED_BY_INSTRUCTION
    if "insert" in normalized:
        return NZPayloadInstructionShape.DIRECT_INSERT_INSTRUCTION
    if any(word in normalized for word in ("substitut", "replac")):
        return NZPayloadInstructionShape.DIRECT_SUBSTITUTE_REPLACE_INSTRUCTION
    if any(word in normalized for word in ("repealing", "repealed", "repeal")):
        return NZPayloadInstructionShape.DIRECT_REPEAL_REPLACE_INSTRUCTION
    return NZPayloadInstructionShape.OTHER_INSTRUCTION


def _payload_instruction_safety(instruction_shape: NZPayloadInstructionShape) -> NZPayloadInstructionSafety:
    match instruction_shape:
        case (
            NZPayloadInstructionShape.DIRECT_AMENDED_BY_INSTRUCTION
            | NZPayloadInstructionShape.DIRECT_INSERT_INSTRUCTION
            | NZPayloadInstructionShape.DIRECT_REPEAL_REPLACE_INSTRUCTION
            | NZPayloadInstructionShape.DIRECT_SUBSTITUTE_REPLACE_INSTRUCTION
        ):
            return NZPayloadInstructionSafety.CANDIDATE_ONLY_SEMANTIC_CLASSIFICATION
        case NZPayloadInstructionShape.RETROSPECTIVE_INCORPORATED_NOTE:
            return NZPayloadInstructionSafety.REVIEW_RETROSPECTIVE_INCORPORATED_NOTE
        case NZPayloadInstructionShape.SCHEDULE_INDIRECTION:
            return NZPayloadInstructionSafety.UNSAFE_SCHEDULE_OR_OMNIBUS_INDIRECTION
        case NZPayloadInstructionShape.EMPTY_OR_STUB | NZPayloadInstructionShape.OTHER_INSTRUCTION:
            return NZPayloadInstructionSafety.UNSAFE_OPAQUE_OR_UNCLASSIFIED
        case NZPayloadInstructionShape.NONE:
            return NZPayloadInstructionSafety.NONE
        case _ as unreachable:
            assert_never(unreachable)


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
