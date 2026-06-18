"""H3 — shared audit channel runner for corpus-wide Finland compiles."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from lawvm.tools.corpus_io import (
    deduplicate_parent_ids,
    load_statute_ids,
    resolve_finland_bench_source,
    resolve_line_list_source,
)
from lawvm.tools.corpus_sweep import SweepResult, sweep_corpus_ordered


class AuditChannel(str, Enum):
    ADJUDICATIONS = "adjudications"
    INVARIANTS = "invariants"
    WARNINGS = "warnings"


@dataclass(frozen=True, slots=True)
class AuditChannelSpec:
    channel: AuditChannel
    worker: Callable[[str], Any]
    description: str


def resolve_audit_corpus(path: Path | None = None) -> list[str]:
    if path is None:
        return load_statute_ids(resolve_finland_bench_source())
    if path.suffix == ".csv":
        return load_statute_ids(resolve_line_list_source(path))
    source = resolve_line_list_source(path)
    return deduplicate_parent_ids(load_statute_ids(source))


def run_audit_channel(
    spec: AuditChannelSpec,
    statute_ids: list[str],
    *,
    workers: int = 4,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> SweepResult[Any]:
    return sweep_corpus_ordered(
        statute_ids,
        spec.worker,
        workers=workers,
        on_progress=on_progress,
    )


_NORMALIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{4}/\d+\b"), "<SID>"),
    (re.compile(r"§+\s*\d[\d\-]*|\d[\d\-]*\s*§+"), "<SEC>"),
    (re.compile(r"\b\d{5,}\b"), "<NUM>"),
    (re.compile(r"<[^>]{0,120} object at 0x[0-9a-fA-F]+>"), "<OBJ>"),
    (re.compile(r"0x[0-9a-fA-F]{4,}"), "<ADDR>"),
    (re.compile(r"\s+"), " "),
]


def normalize_warning_message(msg: str) -> str:
    """Normalize warning text for corpus-wide pattern aggregation."""
    for pattern, replacement in _NORMALIZE_PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg.strip()


def _compile_with_warnings_worker(sid: str) -> tuple[str, list[dict[str, object]]]:
    """Compile one statute and capture replay warnings (picklable worker)."""
    import warnings

    from lawvm.finland.compile import compile_fi_facade

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            compile_fi_facade(sid)
        except Exception as exc:
            return sid, [
                {
                    "category": "ERROR",
                    "message": str(exc),
                    "filename": "",
                    "lineno": 0,
                }
            ]

    return sid, [
        {
            "category": warning.category.__name__,
            "message": str(warning.message),
            "filename": str(warning.filename),
            "lineno": int(warning.lineno),
        }
        for warning in caught
    ]


def warnings_channel_spec() -> AuditChannelSpec:
    return AuditChannelSpec(
        channel=AuditChannel.WARNINGS,
        worker=_compile_with_warnings_worker,
        description="Corpus-wide replay Python warning capture",
    )


def _audit_invariants_worker(sid: str) -> tuple[str, list[dict[str, str]]]:
    from lawvm.tools.fi_invariant_audit import audit_one_statute

    return sid, audit_one_statute(sid)


def invariants_channel_spec() -> AuditChannelSpec:
    return AuditChannelSpec(
        channel=AuditChannel.INVARIANTS,
        worker=_audit_invariants_worker,
        description="Corpus-wide replay/product invariant audit",
    )


def _audit_adjudications_worker(sid: str) -> tuple[str, object]:
    from lawvm.tools.fi_adjudication_audit import compile_one_statute

    return sid, compile_one_statute(sid)


def adjudications_channel_spec() -> AuditChannelSpec:
    return AuditChannelSpec(
        channel=AuditChannel.ADJUDICATIONS,
        worker=_audit_adjudications_worker,
        description="Corpus-wide finding-ledger adjudication audit",
    )
