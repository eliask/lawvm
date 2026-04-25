"""lawvm no-divergence -- explain Norway replay-vs-current divergences."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from lawvm.tools.report_models import NorwayDivergenceItem, NorwayDivergencePayload

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


def _build_payload(result: Any, max_divergences: int | None) -> NorwayDivergencePayload:
    from lawvm.tools.no_coverage import build_no_coverage_report

    coverage_report = build_no_coverage_report(
        base_id=result.base_id,
        data_dir=None,
        index=None,
        verify_result=result,
        limit=max_divergences if isinstance(max_divergences, int) else 20,
    )
    divergence_items: list[NorwayDivergenceItem] = []
    if result.divergences is None:
        return NorwayDivergencePayload(
            base_id=result.base_id,
            as_of=result.as_of,
            current_title=result.current_title,
            replay_status=result.replay_status,
            consistent=result.consistent,
            overall_hint=_coverage_aware_overall_hint(
                result,
                touched_divergence_count=int(coverage_report["touched_divergence_count"]),
                untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
            ),
            divergence_count=result.divergence_count,
            divergence_counts=dict(result.divergence_counts or {}),
            raw_divergence_count=result.raw_divergence_count,
            raw_divergence_counts=dict(result.raw_divergence_counts or {}),
            indexed_amendment_count=result.indexed_amendment_count,
            applied_amendment_count=result.applied_amendment_count,
            replay_op_count=result.replay_op_count,
            source_signal=result.source_signal or "",
            error=result.error or "",
            touched_divergence_count=int(coverage_report["touched_divergence_count"]),
            untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
        )

    divergences = result.divergences
    if isinstance(max_divergences, int) and max_divergences >= 0:
        divergences = divergences[:max_divergences]
    divergence_items = [
        NorwayDivergenceItem(
            address=tuple(divergence.address.path),
            address_text=_format_address(list(divergence.address.path)),
            divergence_type=divergence.divergence_type,
            hint=_divergence_hint(result, divergence),
            ops_text=divergence.ops_text,
            consolidated_text=divergence.consolidated_text,
        )
        for divergence in divergences
    ]
    return NorwayDivergencePayload(
        base_id=result.base_id,
        as_of=result.as_of,
        current_title=result.current_title,
        replay_status=result.replay_status,
        consistent=result.consistent,
        overall_hint=_coverage_aware_overall_hint(
            result,
            touched_divergence_count=int(coverage_report["touched_divergence_count"]),
            untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
        ),
        divergence_count=result.divergence_count,
        divergence_counts=dict(result.divergence_counts or {}),
        raw_divergence_count=result.raw_divergence_count,
        raw_divergence_counts=dict(result.raw_divergence_counts or {}),
        indexed_amendment_count=result.indexed_amendment_count,
        applied_amendment_count=result.applied_amendment_count,
        replay_op_count=result.replay_op_count,
        source_signal=result.source_signal or "",
        error=result.error or "",
        touched_divergence_count=int(coverage_report["touched_divergence_count"]),
        untouched_divergence_count=int(coverage_report["untouched_divergence_count"]),
        divergences=tuple(divergence_items),
    )


def main(args: "argparse.Namespace") -> None:
    from lawvm.norway.verify import verify_no_against_current

    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None

    result = verify_no_against_current(
        getattr(args, "base_id"),
        as_of=getattr(args, "as_of"),
        data_dir=data_dir,
        index_path=index_path,
        commencement_path=commencement_path,
    )

    max_divergences = getattr(args, "max_divergences", None)
    payload = _build_payload(result, max_divergences)
    payload_dict = payload.to_dict()

    if getattr(args, "json", False):
        print(json.dumps(payload_dict, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Divergence Explainer ===")
    print(f"  base id         : {payload.base_id}")
    print(f"  as of           : {payload.as_of}")
    print(f"  replay status   : {payload.replay_status}")
    if payload.current_title:
        print(f"  current title   : {payload.current_title}")
    if payload.error:
        print(f"  error           : {payload.error}")
        return
    print(f"  consistent      : {'yes' if payload.consistent else 'no'}")
    print(f"  overall hint    : {payload.overall_hint}")
    print(f"  divergence count: {payload.divergence_count}")
    divergence_counts = dict(payload.divergence_counts)
    print(
        "  source coverage : "
        f"indexed={payload.indexed_amendment_count} | "
        f"applied={payload.applied_amendment_count} | "
        f"ops={payload.replay_op_count}"
    )
    if payload.source_signal:
        print(f"  source signal   : {payload.source_signal}")
    if divergence_counts:
        print(
            "  by type         : "
            + ", ".join(f"{k}={v}" for k, v in sorted(divergence_counts.items()))
        )
    if payload.raw_divergence_count != payload.divergence_count:
        print(f"  raw divergences : {payload.raw_divergence_count}")

    divergences = list(payload.divergences)
    if divergences:
        print("  primary divergences:")
        for divergence in divergences:
            print(
                f"    [{divergence.hint}|{divergence.divergence_type}] "
                f"{divergence.address_text}"
            )
            if divergence.ops_text:
                print(f"      ops : {divergence.ops_text}")
            if divergence.consolidated_text:
                print(f"      cur : {divergence.consolidated_text}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lawvm no-divergence")
    parser.add_argument("base_id")
    parser.add_argument("--as-of", dest="as_of", default="2026-03-29")
    parser.add_argument("--data-dir", dest="data_dir")
    parser.add_argument("--index", dest="index")
    parser.add_argument("--commencement", dest="commencement")
    parser.add_argument("--max-divergences", dest="max_divergences", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    return parser


def cli(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    main(args)


if __name__ == "__main__":
    cli()
