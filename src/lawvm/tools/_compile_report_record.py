"""Shared facade-backed compile report record projection for tool readers."""
from __future__ import annotations

from typing import Any, Sequence

from lawvm.core.compile_result import strict_fail_reasons_from_findings_and_verdict
from lawvm.core.compile_views import (
    projection_rows_from_findings,
    source_pathology_rows_from_findings,
)


def compile_summary_record_from_facade(
    *,
    facade: Any,
    failed_ops: Sequence[Any],
) -> dict[str, Any]:
    """Build the minimal compile-summary read model from core facade authority."""
    findings = tuple(getattr(facade, "finding_ledger", ()) or ())
    projection_rows = tuple(projection_rows_from_findings(findings))
    if not projection_rows:
        projection_rows_fn = getattr(facade, "projection_rows", None)
        if callable(projection_rows_fn):
            projection_rows = tuple(projection_rows_fn() or ())
    source_pathologies = tuple(source_pathology_rows_from_findings(findings))
    if not source_pathologies:
        source_pathologies_fn = getattr(facade, "source_pathology_rows", None)
        if callable(source_pathologies_fn):
            source_pathologies = tuple(source_pathologies_fn() or ())
    return {
        "projection_rows": projection_rows,
        "canonical_ops": tuple(facade.bundle.structural_ops),
        "failed_ops": tuple(failed_ops),
        "strict_fail_reasons": tuple(
            strict_fail_reasons_from_findings_and_verdict(
                findings,
                verdict=getattr(facade, "verdict", None),
            )
        ),
        "source_pathologies": source_pathologies,
    }


def report_record_from_facade(
    *,
    statute_id: str,
    facade: Any,
    compiled_ops: list[dict[str, object]],
    failed_ops: list[Any],
    source_adjudication: Any = None,
) -> dict[str, Any]:
    """Build a tool/read-model record from core facade authority plus replay extras."""
    record = compile_summary_record_from_facade(
        facade=facade,
        failed_ops=failed_ops,
    )
    html_noncommensurable_reason = (
        str(source_adjudication.html_noncommensurable_reason or "")
        if source_adjudication is not None
        else ""
    )
    record.update(
        {
            "statute_id": statute_id,
            "profile": str(getattr(facade, "strict_profile_name", "") or ""),
            "compiled_ops": tuple(compiled_ops),
            "source_adjudication": source_adjudication,
            "html_noncommensurable_reason": html_noncommensurable_reason,
        }
    )
    return record
