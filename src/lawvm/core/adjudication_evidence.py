"""Shared projection from replay adjudications to corpus evidence rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from lawvm.core.diagnostic_records import (
    DIAGNOSTIC_DETAIL_ENVELOPE_KEYS,
    diagnostic_detail,
)
from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass(frozen=True, slots=True)
class AdjudicationEvidenceInput:
    kind: str
    detail: Mapping[str, Any]
    op_id: str = ""
    source_statute: str = ""
    message: str = ""


def _adjudication_input(
    adjudication: Any,
    *,
    default_kind: str,
) -> AdjudicationEvidenceInput:
    if isinstance(adjudication, Mapping):
        raw_kind = adjudication.get("kind")
        raw_detail = adjudication.get("detail")
        raw_op_id = adjudication.get("op_id")
        raw_source_statute = adjudication.get("source_statute")
        raw_message = adjudication.get("message")
    else:
        raw_kind = getattr(adjudication, "kind", None)
        raw_detail = getattr(adjudication, "detail", None)
        raw_op_id = getattr(adjudication, "op_id", None)
        raw_source_statute = getattr(adjudication, "source_statute", None)
        raw_message = getattr(adjudication, "message", None)
    return AdjudicationEvidenceInput(
        kind=text_or_none(raw_kind) or default_kind,
        detail=_mapping_or_empty(raw_detail),
        op_id=text_or_none(raw_op_id) or "",
        source_statute=text_or_none(raw_source_statute) or "",
        message=text_or_none(raw_message) or "",
    )


def adjudication_kind_counts(adjudications: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for adjudication in adjudications:
        record = _adjudication_input(adjudication, default_kind="unknown")
        counts[record.kind] = counts.get(record.kind, 0) + 1
    return dict(sorted(counts.items()))


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _adjudication_phase(kind: str, detail: Mapping[str, Any]) -> str:
    phase = text_or_none(detail.get("phase"))
    if phase is not None:
        return phase
    if kind.startswith("no_parse_"):
        return "parse"
    if "missing_amendment_source" in kind:
        return "acquisition"
    if kind == "text_duplication_warning":
        return "replay_fold"
    if "replay" in kind:
        return "replay"
    return "compile"


def _bool_detail(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def adjudication_record_diagnostic_detail(
    record: Mapping[str, Any],
    *,
    default_blocking: bool = True,
) -> dict[str, Any]:
    """Build the shared diagnostic envelope for a replay adjudication record.

    This is a projection adapter only. It does not replace frontend-local
    adjudication carriers or classify their extra detail payloads.
    """

    kind = text_or_none(record.get("kind")) or "compile_adjudication"
    detail = _mapping_or_empty(record.get("detail"))
    blocking = _bool_detail(detail.get("blocking"), default=default_blocking)
    local_detail = {
        str(key): value
        for key, value in detail.items()
        if str(key) not in DIAGNOSTIC_DETAIL_ENVELOPE_KEYS
    }
    return diagnostic_detail(
        rule_id=text_or_none(detail.get("rule_id")) or kind,
        phase=_adjudication_phase(kind, detail),
        blocking=blocking,
        family=text_or_none(detail.get("family")) or "",
        reason=text_or_none(detail.get("reason")) or "",
        message=text_or_none(detail.get("message")) or "",
        strict_disposition=text_or_none(detail.get("strict_disposition"))
        or ("block" if blocking else "record"),
        quirks_disposition=text_or_none(detail.get("quirks_disposition")) or "record",
        detail=local_detail,
    )


def adjudication_diagnostic_detail(
    adjudication: Any,
    *,
    default_blocking: bool = True,
) -> dict[str, Any]:
    """Build the shared diagnostic envelope for a CompileAdjudication-like object."""

    record = _adjudication_input(adjudication, default_kind="compile_adjudication")
    return adjudication_record_diagnostic_detail(
        {
            "kind": record.kind,
            "detail": record.detail,
        },
        default_blocking=default_blocking,
    )


def _adjudication_finding_id(
    *,
    frontend_id: str,
    base_id: str,
    as_of: str,
    index: int,
    kind: str,
    op_id: str,
) -> str:
    suffix = op_id or f"adjudication-{index + 1}"
    return f"{frontend_id}:{base_id}:{as_of}:{kind}:{suffix}"


def adjudication_finding_evidence_rows(
    adjudications: Iterable[Any],
    *,
    frontend_id: str,
    base_id: str,
    as_of: str,
) -> tuple[CorpusFindingEvidenceRow, ...]:
    """Project replay compile adjudications into shared corpus finding rows."""

    rows: list[CorpusFindingEvidenceRow] = []
    for index, adjudication in enumerate(adjudications):
        record = _adjudication_input(adjudication, default_kind="compile_adjudication")
        detail = adjudication_record_diagnostic_detail(
            {
                "kind": record.kind,
                "detail": record.detail,
            }
        )
        source_statute = record.source_statute or base_id
        rows.append(
            CorpusFindingEvidenceRow(
                finding_id=_adjudication_finding_id(
                    frontend_id=frontend_id,
                    base_id=base_id,
                    as_of=as_of,
                    index=index,
                    kind=record.kind,
                    op_id=record.op_id,
                ),
                frontend_id=frontend_id,
                family=record.kind,
                rule_id=str(detail["rule_id"]),
                phase=str(detail["phase"]),
                message=record.message or record.kind,
                source_artifact_id=source_statute,
                source_unit_id=record.op_id,
                related_row_ids=(record.op_id,) if record.op_id else (),
                blocking=bool(detail["blocking"]),
                strict_disposition=str(detail["strict_disposition"]),
                quirks_disposition=str(detail["quirks_disposition"]),
                evidence={
                    "base_id": base_id,
                    "as_of": as_of,
                    "kind": record.kind,
                    "op_id": record.op_id,
                    "detail": dict(record.detail),
                    "diagnostic_detail": detail,
                },
            )
        )
    return tuple(rows)
