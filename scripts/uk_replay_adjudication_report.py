#!/usr/bin/env python3
"""Aggregate UK replay-adjudication kinds across a small statute ID set.

A single ``lawvm uk-replay --json`` run already carries the typed
``replay_adjudication`` surface for one statute: ``adjudication_kind_counts``,
``replay_adjudication_bucket_counts``, ``replay_adjudication_owner_phase_counts``
and a per-row ``adjudications`` array that joins each kind to its triage bucket
and owning phase. Outside one run those kinds are under-exposed: an operator
triaging a corpus has to eyeball N JSON blobs to see which adjudication kinds
dominate and which phase owns them.

This report sums the existing per-statute surface across an ID set into one
deterministic ``kind -> count`` table, each kind carried with its triage bucket
(``replay_bug`` / ``source_shape`` / ``text_surface`` /
``nonblocking_observation`` / ``unknown``) and its owning phase. The bucket is
the operator wedge: it separates genuine replay-bug claims from source-shape
gaps and oracle-editorial residue.

This report is **read-only diagnostics**. It changes no replay or compile
behavior, invents no new adjudication vocabulary, and re-uses the buckets/phases
the uk-replay payload already emits. Output is sorted (kinds, buckets, phases,
statute ids) with no timestamps in the body, so re-running over a fixed ID set
diffs empty.

Per-statute payloads are obtained by invoking ``lawvm uk-replay <id> --json``;
the aggregation core (:func:`aggregate_replay_adjudication_report`) is a pure
function over already-parsed payload dicts so it can be unit-tested
synthetically without the archive.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


_REPORT_KIND = "uk_replay_adjudication_aggregation"
_REPORT_SCHEMA = "lawvm.uk_replay_adjudication_aggregation.v1"
_UNKNOWN_BUCKET = "unknown"
_UNKNOWN_PHASE = "unknown"
_FORBIDDEN_SHORTCUTS = (
    "adjudication_count_as_replay_authority",
    "bucket_total_as_legal_state",
    "kind_aggregate_as_oracle_truth",
)


@dataclass(frozen=True, slots=True)
class AdjudicationKindRow:
    """One aggregated adjudication kind across the scanned ID set.

    ``bucket`` and ``owner_phase`` come from the uk-replay payload's own
    classification, not a parallel vocabulary. ``statute_ids`` lists the
    statutes that emitted the kind, sorted, for drill-down.
    """

    kind: str
    bucket: str
    owner_phase: str
    count: int
    statute_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "bucket": self.bucket,
            "owner_phase": self.owner_phase,
            "count": self.count,
            "statute_ids": list(self.statute_ids),
        }


@dataclass(frozen=True, slots=True)
class ReplayAdjudicationReport:
    """Deterministic corpus-level aggregation of UK replay adjudications."""

    report_kind: str
    schema: str
    statute_ids: tuple[str, ...]
    statutes_with_payload: tuple[str, ...]
    statutes_missing_payload: tuple[str, ...]
    total_adjudications: int
    kind_counts: dict[str, int]
    bucket_counts: dict[str, int]
    owner_phase_counts: dict[str, int]
    kind_rows: tuple[AdjudicationKindRow, ...]
    forbidden_shortcuts: tuple[str, ...] = _FORBIDDEN_SHORTCUTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_kind": self.report_kind,
            "schema": self.schema,
            "read_only": True,
            "replay_unchanged": True,
            "statute_ids": list(self.statute_ids),
            "statutes_with_payload": list(self.statutes_with_payload),
            "statutes_missing_payload": list(self.statutes_missing_payload),
            "total_adjudications": self.total_adjudications,
            "kind_counts": dict(self.kind_counts),
            "bucket_counts": dict(self.bucket_counts),
            "owner_phase_counts": dict(self.owner_phase_counts),
            "kind_rows": [row.to_dict() for row in self.kind_rows],
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
        }


@dataclass
class _KindAccumulator:
    count: int = 0
    # bucket/phase are read from the payload's own join; track observed values
    # so divergent classifications across statutes surface rather than hide.
    buckets: set[str] = field(default_factory=set)
    owner_phases: set[str] = field(default_factory=set)
    statute_ids: set[str] = field(default_factory=set)


def _bucket_from_row(row: Mapping[str, Any]) -> str:
    residual = row.get("agreement_residual")
    if isinstance(residual, Mapping):
        detail = residual.get("detail")
        if isinstance(detail, Mapping):
            bucket = detail.get("bucket")
            if isinstance(bucket, str) and bucket.strip():
                return bucket.strip()
    return _UNKNOWN_BUCKET


def _owner_phase_from_row(row: Mapping[str, Any]) -> str:
    owner_phase = row.get("owner_phase")
    if isinstance(owner_phase, str) and owner_phase.strip():
        return owner_phase.strip()
    return _UNKNOWN_PHASE


def _kind_from_row(row: Mapping[str, Any]) -> str:
    kind = row.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    return "unknown"


def _join_label(values: Iterable[str], *, default: str) -> str:
    """Collapse the observed bucket/phase set for a kind into one stable label.

    A kind should classify identically everywhere; if it does not (a payload
    drift), surface every observed value joined by ``|`` rather than silently
    picking one — visible disagreement is the contract (AGENTS §1.8).
    """
    seen = sorted({value for value in values if value})
    if not seen:
        return default
    return "|".join(seen)


def aggregate_replay_adjudication_report(
    payloads: Sequence[tuple[str, Mapping[str, Any] | None]],
) -> ReplayAdjudicationReport:
    """Sum per-statute replay-adjudication surfaces into one report.

    ``payloads`` is a sequence of ``(statute_id, payload_or_None)``. A ``None``
    payload marks a statute whose uk-replay run produced no JSON (acquisition
    miss / error); it is reported under ``statutes_missing_payload`` rather than
    dropped. Each payload is the dict emitted by ``lawvm uk-replay --json`` and
    is treated as authoritative for kind/bucket/phase classification.
    """
    statute_ids = tuple(sorted({statute_id for statute_id, _ in payloads}))
    with_payload: set[str] = set()
    missing_payload: set[str] = set()
    accumulators: dict[str, _KindAccumulator] = {}

    for statute_id, payload in payloads:
        if payload is None:
            missing_payload.add(statute_id)
            continue
        with_payload.add(statute_id)
        rows = payload.get("adjudications")
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            kind = _kind_from_row(row)
            acc = accumulators.setdefault(kind, _KindAccumulator())
            acc.count += 1
            acc.buckets.add(_bucket_from_row(row))
            acc.owner_phases.add(_owner_phase_from_row(row))
            acc.statute_ids.add(statute_id)

    kind_rows: list[AdjudicationKindRow] = []
    kind_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    owner_phase_counts: dict[str, int] = {}
    total = 0
    for kind in sorted(accumulators):
        acc = accumulators[kind]
        bucket = _join_label(acc.buckets, default=_UNKNOWN_BUCKET)
        owner_phase = _join_label(acc.owner_phases, default=_UNKNOWN_PHASE)
        kind_rows.append(
            AdjudicationKindRow(
                kind=kind,
                bucket=bucket,
                owner_phase=owner_phase,
                count=acc.count,
                statute_ids=tuple(sorted(acc.statute_ids)),
            )
        )
        kind_counts[kind] = acc.count
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + acc.count
        owner_phase_counts[owner_phase] = (
            owner_phase_counts.get(owner_phase, 0) + acc.count
        )
        total += acc.count

    return ReplayAdjudicationReport(
        report_kind=_REPORT_KIND,
        schema=_REPORT_SCHEMA,
        statute_ids=statute_ids,
        statutes_with_payload=tuple(sorted(with_payload)),
        statutes_missing_payload=tuple(sorted(missing_payload)),
        total_adjudications=total,
        kind_counts=dict(sorted(kind_counts.items())),
        bucket_counts=dict(sorted(bucket_counts.items())),
        owner_phase_counts=dict(sorted(owner_phase_counts.items())),
        kind_rows=tuple(kind_rows),
    )


def _run_uk_replay_json(
    statute_id: str,
    *,
    db: str | None,
    pit_date: str | None,
    extra_args: Sequence[str],
) -> Mapping[str, Any] | None:
    """Invoke ``lawvm uk-replay <id> --json`` and return the parsed payload.

    Returns ``None`` (recorded as a missing payload, not a crash) when the run
    fails or emits no parseable JSON, so one bad statute never sinks the report.
    """
    cmd = [
        sys.executable,
        "-m",
        "lawvm.tools.cli",
        "uk-replay",
        statute_id,
        "--json",
    ]
    if db:
        cmd += ["--db", db]
    if pit_date:
        cmd += ["--pit-date", pit_date]
    cmd += list(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        return None
    decoded = json.loads(stdout)
    if isinstance(decoded, Mapping):
        return decoded
    return None


def _read_ids_from_file(path: str) -> list[str]:
    ids: list[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            token = line.strip()
            if token and not token.startswith("#"):
                ids.append(token)
    return ids


def _render_text(report: ReplayAdjudicationReport) -> str:
    lines: list[str] = []
    lines.append(f"# {report.report_kind} ({report.schema})")
    lines.append(
        "read-only diagnostics; replay/compile behavior unchanged; "
        "buckets/phases re-use the uk-replay payload surface."
    )
    lines.append(
        f"statutes: {len(report.statute_ids)} "
        f"(with_payload={len(report.statutes_with_payload)}, "
        f"missing_payload={len(report.statutes_missing_payload)})"
    )
    lines.append(f"total_adjudications: {report.total_adjudications}")
    lines.append("")
    lines.append("## bucket_counts")
    if report.bucket_counts:
        for bucket, count in report.bucket_counts.items():
            lines.append(f"  {bucket}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("## owner_phase_counts")
    if report.owner_phase_counts:
        for phase, count in report.owner_phase_counts.items():
            lines.append(f"  {phase}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("## kind -> count [bucket | owner_phase]")
    if report.kind_rows:
        for row in report.kind_rows:
            lines.append(
                f"  {row.kind}: {row.count} "
                f"[{row.bucket} | {row.owner_phase}]"
            )
    else:
        lines.append("  (none)")
    if report.statutes_missing_payload:
        lines.append("")
        lines.append("## statutes_missing_payload")
        for statute_id in report.statutes_missing_payload:
            lines.append(f"  {statute_id}")
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate UK replay-adjudication kinds across a small statute ID "
            "set into a deterministic kind->count report with triage bucket and "
            "owning phase. Read-only; no replay/compile behavior change."
        ),
    )
    parser.add_argument(
        "statute_ids",
        nargs="*",
        help="UK statute IDs, e.g. ukpga/1998/42",
    )
    parser.add_argument(
        "--ids-file",
        dest="ids_file",
        metavar="PATH",
        help="newline-delimited statute IDs ('#' comments allowed)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Farchive DB path forwarded to uk-replay",
    )
    parser.add_argument(
        "--pit-date",
        dest="pit_date",
        metavar="YYYY-MM-DD",
        help="point-in-time date forwarded to uk-replay",
    )
    parser.add_argument(
        "--replay-arg",
        dest="replay_args",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument forwarded verbatim to each uk-replay run (repeatable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    statute_ids = list(args.statute_ids)
    if args.ids_file:
        statute_ids += _read_ids_from_file(args.ids_file)
    # Dedup while keeping a deterministic scan order.
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for statute_id in statute_ids:
        if statute_id and statute_id not in seen:
            seen.add(statute_id)
            ordered_ids.append(statute_id)
    if not ordered_ids:
        parser.error("no statute IDs provided (positional or --ids-file)")

    payloads: list[tuple[str, Mapping[str, Any] | None]] = []
    for statute_id in ordered_ids:
        payload = _run_uk_replay_json(
            statute_id,
            db=args.db,
            pit_date=args.pit_date,
            extra_args=args.replay_args,
        )
        payloads.append((statute_id, payload))

    report = aggregate_replay_adjudication_report(payloads)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
