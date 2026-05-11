"""Shared projection from replay adjudications to corpus evidence rows."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def adjudication_kind_counts(adjudications: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for adjudication in adjudications:
        kind = text_or_none(getattr(adjudication, "kind", None)) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
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
    if "replay" in kind:
        return "replay"
    return "compile"


def _bool_detail(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


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
        kind = text_or_none(getattr(adjudication, "kind", None)) or "compile_adjudication"
        detail = _mapping_or_empty(getattr(adjudication, "detail", None))
        op_id = text_or_none(getattr(adjudication, "op_id", None)) or ""
        source_statute = text_or_none(getattr(adjudication, "source_statute", None)) or base_id
        rule_id = text_or_none(detail.get("rule_id")) or kind
        blocking = _bool_detail(detail.get("blocking"), default=True)
        strict_disposition = text_or_none(detail.get("strict_disposition")) or (
            "block" if blocking else "record"
        )
        quirks_disposition = text_or_none(detail.get("quirks_disposition")) or "record"
        rows.append(
            CorpusFindingEvidenceRow(
                finding_id=_adjudication_finding_id(
                    frontend_id=frontend_id,
                    base_id=base_id,
                    as_of=as_of,
                    index=index,
                    kind=kind,
                    op_id=op_id,
                ),
                frontend_id=frontend_id,
                family=kind,
                rule_id=rule_id,
                phase=_adjudication_phase(kind, detail),
                message=text_or_none(getattr(adjudication, "message", None)) or kind,
                source_artifact_id=source_statute,
                source_unit_id=op_id,
                related_row_ids=(op_id,) if op_id else (),
                blocking=blocking,
                strict_disposition=strict_disposition,
                quirks_disposition=quirks_disposition,
                evidence={
                    "base_id": base_id,
                    "as_of": as_of,
                    "kind": kind,
                    "op_id": op_id,
                    "detail": dict(detail),
                },
            )
        )
    return tuple(rows)
