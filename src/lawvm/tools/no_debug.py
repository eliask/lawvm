"""lawvm no-debug -- compact Norway combined replay/source/op debug report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast

from lawvm.tools.report_models import (
    NorwayDivergenceItem,
    NorwayDebugPayload,
    NorwayTraceOpRow,
    NorwayTraceSourceRow,
)

if TYPE_CHECKING:
    from lawvm.core.timeline_consistency import ConsistencyDivergence


def _format_address(path: list[tuple[str, str]]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _overall_hint(result: Any) -> str:
    if getattr(result, "source_signal", None):
        return str(result.source_signal)
    counts = dict(getattr(result, "divergence_counts", None) or {})
    if counts.get("OPS_MISSING", 0) and counts.get("MISMATCH", 0):
        return "mixed_replay_and_text_drift"
    if counts.get("OPS_MISSING", 0):
        return "replay_lowering_gap"
    if counts.get("CONSOLIDATED_MISSING", 0):
        return "current_text_missing"
    if counts.get("MISMATCH", 0):
        return "text_drift"
    return "consistent"


def _coverage_aware_overall_hint(
    result: Any,
    *,
    touched_divergence_count: int,
    untouched_divergence_count: int,
) -> str:
    if getattr(result, "source_signal", None):
        return str(result.source_signal)
    if touched_divergence_count == 0 and untouched_divergence_count > 0:
        return "untouched_base_current_drift"
    return _overall_hint(result)


def _divergence_hint(result: Any, divergence: "ConsistencyDivergence") -> str:
    if getattr(result, "source_signal", None):
        return "source_sparse"
    if divergence.divergence_type == "OPS_MISSING":
        return "replay_lowering_gap"
    if divergence.divergence_type == "CONSOLIDATED_MISSING":
        return "current_text_missing"
    if divergence.divergence_type == "MISMATCH":
        return "text_drift"
    return divergence.divergence_type.lower()


def _serialize_divergences(result: Any, limit: int) -> list[dict[str, object]]:
    divergences = result.divergences or []
    if isinstance(limit, int) and limit >= 0:
        divergences = divergences[:limit]
    return [
        {
            "address": list(divergence.address.path),
            "address_text": _format_address(list(divergence.address.path)),
            "divergence_type": divergence.divergence_type,
            "hint": _divergence_hint(result, divergence),
            "ops_text": divergence.ops_text,
            "consolidated_text": divergence.consolidated_text,
        }
        for divergence in divergences
    ]


def _build_report(
    *,
    base_id: str,
    as_of: str,
    data_dir: Path | None,
    index_path: Path | None,
    commencement_path: Path | None,
    path_filters: list[str],
    limit: int,
    ) -> NorwayDebugPayload:
    from lawvm.norway.commencement import build_no_law_report
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index
    from lawvm.norway.verify import verify_no_against_current
    from lawvm.norway.sources import resolve_no_source_path
    from lawvm.tools.no_coverage import build_no_coverage_report
    from lawvm.tools.no_op_trace import build_no_op_trace_report

    if index_path is not None:
        index = load_no_amendment_index(index_path)
    else:
        index = build_no_amendment_index(data_dir)

    source_path = resolve_no_source_path(Path(index.data_dir) if index.data_dir else data_dir)
    verify_result = verify_no_against_current(
        base_id,
        as_of=as_of,
        data_dir=source_path,
        index=index,
        commencement_path=commencement_path,
    )
    law_report = build_no_law_report(index, base_id=base_id)
    coverage_report = build_no_coverage_report(
        base_id=base_id,
        data_dir=source_path,
        index=index,
        verify_result=verify_result,
        limit=limit,
    )
    trace_report = build_no_op_trace_report(
        base_id=base_id,
        data_dir=source_path,
        index_path=index_path,
        path_filters=path_filters,
        limit=limit,
    )

    sources = tuple(
        NorwayTraceSourceRow(
            source_id=str(item.get("source_id") or ""),
            effective_status=str(item.get("effective_status") or ""),
            title=str(item.get("title") or ""),
            compiled_op_count=int(item.get("compiled_op_count", 0) or 0),
            matched_op_count=int(item.get("matched_op_count", 0) or 0),
        )
        for item in cast(list[dict[str, Any]], trace_report.get("sources", []))[:limit]
        if isinstance(item, dict)
    )
    ops = tuple(
        NorwayTraceOpRow(
            source_id=str(item.get("source_id") or ""),
            sequence=int(item.get("sequence", 0) or 0),
            action=str(item.get("action") or ""),
            target_text=str(item.get("target_text") or ""),
        )
        for item in cast(list[dict[str, Any]], trace_report.get("ops", []))[:limit]
        if isinstance(item, dict)
    )
    divergences = tuple(
        NorwayDivergenceItem(
            address=tuple(divergence.address.path),
            address_text=_format_address(list(divergence.address.path)),
            divergence_type=divergence.divergence_type,
            hint=_divergence_hint(verify_result, divergence),
            ops_text=str(divergence.ops_text or ""),
            consolidated_text=str(divergence.consolidated_text or ""),
        )
        for divergence in (
            verify_result.divergences[:limit]
            if verify_result.divergences is not None and isinstance(limit, int) and limit >= 0
            else (verify_result.divergences or ())
        )
    )
    return NorwayDebugPayload(
        base_id=base_id,
        as_of=as_of,
        title=cast(str, law_report["title"]),
        replay_status=verify_result.replay_status,
        executable_replay_status=cast(str, law_report["executable_replay_status"]),
        consistent=verify_result.consistent,
        overall_hint=_coverage_aware_overall_hint(
            verify_result,
            touched_divergence_count=int(coverage_report["touched_divergence_count"]),
            untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
        ),
        divergence_count=verify_result.divergence_count,
        divergence_counts=dict(verify_result.divergence_counts or {}),
        raw_divergence_count=verify_result.raw_divergence_count,
        raw_divergence_counts=dict(verify_result.raw_divergence_counts or {}),
        indexed_amendment_count=verify_result.indexed_amendment_count,
        applied_amendment_count=verify_result.applied_amendment_count,
        replay_op_count=verify_result.replay_op_count,
        source_signal=verify_result.source_signal or "",
        error=verify_result.error or "",
        amendment_count=cast(int, law_report["amendment_count"]),
        blocking_count=cast(int, law_report["blocking_count"]),
        blocking_ops=cast(int, law_report["blocking_ops"]),
        source_count=int(trace_report["source_count"]),
        matched_source_count=int(trace_report["matched_source_count"]),
        op_count=int(trace_report["op_count"]),
        touched_divergence_count=int(coverage_report["touched_divergence_count"]),
        untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
        path_filters=tuple(path_filters),
        sources=sources,
        ops=ops,
        divergences=divergences,
    )


def main(args: "argparse.Namespace") -> None:
    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None
    path_filters = list(getattr(args, "path", []) or [])
    limit = int(getattr(args, "limit", 5) or 5)
    report = _build_report(
        base_id=getattr(args, "base_id"),
        as_of=getattr(args, "as_of"),
        data_dir=data_dir,
        index_path=index_path,
        commencement_path=commencement_path,
        path_filters=path_filters,
        limit=limit,
    )

    report_dict = report.to_dict()

    if getattr(args, "json", False):
        print(json.dumps(report_dict, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Debug ===")
    print(f"  base id            : {report.base_id}")
    print(f"  as of              : {report.as_of}")
    if report.title:
        print(f"  title              : {report.title}")
    print(f"  replay status      : {report.replay_status}")
    print(f"  executable status   : {report.executable_replay_status}")
    print(f"  consistent         : {'yes' if report.consistent else 'no'}")
    print(f"  overall hint       : {report.overall_hint}")
    print(
        "  source coverage    : "
        f"indexed={report.indexed_amendment_count} | "
        f"applied={report.applied_amendment_count} | "
        f"ops={report.replay_op_count}"
    )
    print(
        "  divergence split   : "
        f"touched={report.touched_divergence_count} | "
        f"untouched={report.untouched_divergence_count}"
    )
    print(
        "  law coverage       : "
        f"amendments={report.amendment_count} | "
        f"blockers={report.blocking_count} | "
        f"blocking_ops={report.blocking_ops}"
    )
    print(
        "  trace coverage     : "
        f"sources={report.matched_source_count}/{report.source_count} | "
        f"ops={report.op_count}"
    )
    if report.source_signal:
        print(f"  source signal      : {report.source_signal}")
    if report.error:
        print(f"  error              : {report.error}")
        return
    if report.divergence_counts:
        print(
            "  by type            : "
            + ", ".join(f"{k}={v}" for k, v in sorted(report.divergence_counts.items()))
        )
    if report.raw_divergence_count != report.divergence_count:
        print(f"  raw divergences    : {report.raw_divergence_count}")
    if report.path_filters:
        print(f"  path filters       : {', '.join(report.path_filters)}")
    if report.sources:
        print("  sources:")
        for item in report.sources:
            print(
                f"    {item.source_id} | {item.effective_status} | "
                f"{item.title or '(untitled)'} | "
                f"compiled={item.compiled_op_count} | "
                f"matched={item.matched_op_count}"
            )
    if report.ops:
        print("  ops:")
        for item in report.ops:
            print(f"    {item.source_id}#{item.sequence} | {item.action} | {item.target_text}")
    if report.divergences:
        print("  divergences:")
        for item in report.divergences:
            print(f"    [{item.hint}|{item.divergence_type}] {item.address_text}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lawvm no-debug")
    parser.add_argument("base_id")
    parser.add_argument("--as-of", dest="as_of", default="2026-03-29")
    parser.add_argument("--data-dir", dest="data_dir")
    parser.add_argument("--index", dest="index")
    parser.add_argument("--commencement", dest="commencement")
    parser.add_argument(
        "--path",
        dest="path",
        action="append",
        default=[],
        help="Optional path filter(s) for the op-trace portion.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Bound divergences, sources, ops, and trace results (default: 5).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def cli(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    main(args)


if __name__ == "__main__":
    cli()
