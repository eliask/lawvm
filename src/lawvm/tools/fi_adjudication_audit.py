"""Finland corpus adjudication audit worker."""
from __future__ import annotations

import warnings
from typing import NamedTuple


class AdjRow(NamedTuple):
    statute_id: str
    adj_kind: str
    message: str
    source_statute: str


class FailureRow(NamedTuple):
    statute_id: str
    failure_kind: str
    description: str
    source_statute: str


class WorkerResult(NamedTuple):
    sid: str
    adj_rows: list[AdjRow]
    failure_rows: list[FailureRow]
    warning_count: int
    error: str


def compile_one_statute(sid: str) -> WorkerResult:
    """Compile one statute; return projected findings, blocking rows, warning count."""
    try:
        from lawvm.core.compile_views import projection_rows_from_findings
        from lawvm.finland.compile import compile_fi_facade

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            facade = compile_fi_facade(sid, replay_mode="legal_pit", compile_mode="quirks")
        projection_rows = projection_rows_from_findings(facade.finding_ledger)

        adj_rows: list[AdjRow] = [
            AdjRow(
                statute_id=sid,
                adj_kind=str(row.get("kind") or ""),
                message=str(row.get("message") or ""),
                source_statute=str(row.get("source") or ""),
            )
            for row in projection_rows
        ]
        failure_rows: list[FailureRow] = [
            FailureRow(
                statute_id=sid,
                failure_kind=str(row.get("kind") or ""),
                description=str(row.get("message") or ""),
                source_statute=str(row.get("source") or ""),
            )
            for row in projection_rows
            if bool(row.get("blocking"))
            or str(row.get("role") or "") in {"obligation", "violation"}
        ]
        return WorkerResult(
            sid=sid,
            adj_rows=adj_rows,
            failure_rows=failure_rows,
            warning_count=len(caught),
            error="",
        )
    except Exception as exc:
        return WorkerResult(
            sid=sid,
            adj_rows=[],
            failure_rows=[],
            warning_count=0,
            error=str(exc),
        )


_compile_one = compile_one_statute

__all__ = [
    "AdjRow",
    "FailureRow",
    "WorkerResult",
    "compile_one_statute",
]
