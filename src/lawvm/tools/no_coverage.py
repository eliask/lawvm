"""lawvm no-coverage -- attribute Norway replay coverage by provision path."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _normalize_base_id(base_id: str) -> str:
    if base_id.startswith("no/"):
        return base_id
    if base_id.startswith("lov/"):
        return f"no/{base_id}"
    return base_id


def _format_path(path: tuple[tuple[str, str], ...]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _classification(
    *,
    touched: bool,
    source_signal: str | None,
) -> str:
    if touched:
        return "touched_replay_defect"
    if source_signal:
        return "source_sparse"
    return "untouched_base_current_drift"


def _load_index(index_path: Path | None, data_dir: Path | None):
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index

    if index_path is not None:
        return load_no_amendment_index(index_path)
    return build_no_amendment_index(data_dir)


def build_no_coverage_report(
    *,
    base_id: str,
    data_dir: Path | None = None,
    index_path: Path | None = None,
    commencement_path: Path | None = None,
    as_of: str = "2026-03-29",
    limit: int = 20,
    index: Any | None = None,
    verify_result: Any | None = None,
) -> dict[str, Any]:
    from lawvm.norway.verify import (
        build_no_verify_coverage_summary,
        collect_no_touched_path_counts,
        no_paths_related,
        verify_no_against_current,
    )

    norm_base_id = _normalize_base_id(base_id)
    if index is None:
        index = _load_index(index_path=index_path, data_dir=data_dir)
    if verify_result is None:
        verify_result = verify_no_against_current(
            norm_base_id,
            as_of=as_of,
            data_dir=data_dir,
            index=index,
            index_path=index_path,
            commencement_path=commencement_path,
        )

    entries = list(index.entries_for_base(norm_base_id))
    replayed = getattr(getattr(verify_result, "replay", None), "replayed", None)
    touched_path_counts, touched_source_count, touched_op_count = collect_no_touched_path_counts(
        base_id=norm_base_id,
        index=index,
        data_dir=data_dir,
        replayed_body=replayed.body if replayed is not None else None,
    )

    touched_paths = [
        {
            "path": list(path),
            "path_text": _format_path(path),
            "hit_count": count,
        }
        for path, count in sorted(
            touched_path_counts.items(),
            key=lambda item: (-item[1], _format_path(item[0])),
        )
    ]
    divergences = list(getattr(verify_result, "divergences", None) or [])
    classified_divergences: list[dict[str, Any]] = []
    source_signal = getattr(verify_result, "source_signal", None)
    coverage_summary = build_no_verify_coverage_summary(
        verify_result=verify_result,
        index=index,
        data_dir=data_dir,
    )
    touched_path_set = set(touched_path_counts)
    for divergence in divergences:
        divergence_path = tuple(divergence.address.path)
        touched = any(no_paths_related(path, divergence_path) for path in touched_path_set)
        classified_divergences.append(
            {
                "address": list(divergence.address.path),
                "address_text": _format_path(tuple(divergence.address.path)),
                "divergence_type": divergence.divergence_type,
                "classification": _classification(touched=touched, source_signal=source_signal),
                "touched": touched,
                "ops_text": divergence.ops_text,
                "consolidated_text": divergence.consolidated_text,
            }
        )

    if isinstance(limit, int) and limit >= 0:
        touched_paths = touched_paths[:limit]
        classified_divergences = classified_divergences[:limit]

    return {
        "base_id": norm_base_id,
        "as_of": as_of,
        "title": getattr(verify_result, "current_title", ""),
        "replay_status": getattr(verify_result, "replay_status", ""),
        "consistent": bool(getattr(verify_result, "consistent", False)),
        "source_signal": source_signal or "",
        "source_count": len(entries),
        "touched_source_count": touched_source_count,
        "touched_op_count": touched_op_count,
        "touched_path_count": len(touched_path_counts),
        "touched_paths": touched_paths,
        "divergence_count": int(getattr(verify_result, "divergence_count", 0) or 0),
        "raw_divergence_count": int(getattr(verify_result, "raw_divergence_count", 0) or 0),
        "indexed_amendment_count": int(getattr(verify_result, "indexed_amendment_count", 0) or 0),
        "applied_amendment_count": int(getattr(verify_result, "applied_amendment_count", 0) or 0),
        "replay_op_count": int(getattr(verify_result, "replay_op_count", 0) or 0),
        "touched_divergence_count": coverage_summary["touched_divergence_count"],
        "untouched_divergence_count": coverage_summary["untouched_divergence_count"],
        "divergences": classified_divergences,
    }


def main(args: "argparse.Namespace") -> None:
    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    commencement_arg = getattr(args, "commencement", None)
    commencement_path = Path(commencement_arg) if commencement_arg else None
    limit = int(getattr(args, "limit", 20) or 20)

    report = build_no_coverage_report(
        base_id=args.base_id,
        data_dir=data_dir,
        index_path=index_path,
        commencement_path=commencement_path,
        as_of=getattr(args, "as_of", "2026-03-29"),
        limit=limit,
    )

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Coverage Attribution ===")
    print(f"  base id              : {report['base_id']}")
    print(f"  as of                : {report['as_of']}")
    print(f"  replay status        : {report['replay_status']}")
    if report["title"]:
        print(f"  title                : {report['title']}")
    print(f"  consistent           : {'yes' if report['consistent'] else 'no'}")
    print(f"  source signal        : {report['source_signal'] or '(none)'}")
    print(
        "  touched coverage     : "
        f"sources={report['touched_source_count']}/{report['source_count']} | "
        f"ops={report['touched_op_count']} | paths={report['touched_path_count']}"
    )
    print(
        "  divergences          : "
        f"primary={report['divergence_count']} | touched={report['touched_divergence_count']} | "
        f"untouched={report['untouched_divergence_count']}"
    )
    if report["touched_paths"]:
        print("  touched paths:")
        for item in report["touched_paths"]:
            print(f"    {item['path_text']} | hits={item['hit_count']}")
    if report["divergences"]:
        print("  divergences:")
        for item in report["divergences"]:
            print(
                f"    [{item['classification']}] {item['address_text']} "
                f"({item['divergence_type']})"
            )
            if item["ops_text"]:
                print(f"      ops : {item['ops_text']}")
            if item["consolidated_text"]:
                print(f"      cur : {item['consolidated_text']}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lawvm no-coverage")
    parser.add_argument("base_id")
    parser.add_argument("--as-of", dest="as_of", default="2026-03-29")
    parser.add_argument("--data-dir", dest="data_dir")
    parser.add_argument("--index", dest="index")
    parser.add_argument("--commencement", dest="commencement")
    parser.add_argument("--limit", dest="limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    return parser


def cli(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    main(args)


if __name__ == "__main__":
    cli()
