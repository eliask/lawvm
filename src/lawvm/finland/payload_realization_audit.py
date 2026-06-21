"""Finland adapter for the generic post-fold payload realization audit."""

from __future__ import annotations

from lawvm.core.coverage import CoverageUnit
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_realization import (
    PayloadRealizationUnit,
    audit_payload_realization,
    payload_realization_gap_findings,
)
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.body_coverage import BodyCoveragePayloadRef
from lawvm.finland.source_model import AmendmentSourceModel


_IGNORED_UNIT_TAGS = frozenset({"container", "nonoperative", "provenance"})


def payload_realization_findings(
    *,
    source_model: AmendmentSourceModel,
    after_ir: IRNode,
    amendment_id: str,
) -> tuple[Finding, ...]:
    """Return audit findings for source payload text absent from ``after_ir``.

    The comparison is intentionally text-realization only.  A failure here says
    "the source payload text did not survive the fold"; it does not infer a
    target address, change action family, or mutate replay output.
    """

    units = _payload_realization_units(source_model)
    gaps = audit_payload_realization(
        units=units,
        after_text=irnode_to_text(after_ir),
    )
    return payload_realization_gap_findings(gaps, source_ref=amendment_id)


def _payload_realization_units(
    source_model: AmendmentSourceModel,
) -> tuple[PayloadRealizationUnit, ...]:
    units: list[PayloadRealizationUnit] = []
    for unit in source_model.body_coverage_units():
        if _skip_unit(unit):
            continue
        payload_ref = unit.payload_ref
        if not isinstance(payload_ref, BodyCoveragePayloadRef):
            continue
        lookup = source_model.lookup_payload_ir_for_coverage_ref(payload_ref)
        if lookup.payload_ir is None:
            continue
        units.append(
            PayloadRealizationUnit(
                unit_id=unit.unit_id,
                unit_kind=unit.kind,
                observed_label=str(unit.observed_label or ""),
                parent_label=str(unit.parent_label or ""),
                text_chunks=_payload_text_chunks(lookup.payload_ir),
            )
        )
    return tuple(units)


def _skip_unit(unit: CoverageUnit) -> bool:
    return bool(_IGNORED_UNIT_TAGS.intersection(unit.tags))


def _payload_text_chunks(node: IRNode) -> tuple[str, ...]:
    chunks: list[str] = []
    _collect_chunks(node, chunks)
    return tuple(dict.fromkeys(chunks))


def _collect_chunks(node: IRNode, chunks: list[str]) -> None:
    if node.kind is IRNodeKind.OMISSION:
        return
    if node.text:
        chunks.append(node.text)
    for child in node.children:
        _collect_chunks(child, chunks)


__all__ = [
    "payload_realization_findings",
]
