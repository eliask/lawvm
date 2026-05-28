"""lawvm no-op-trace -- inspect Norway amendment ops touching given paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from lawvm.core.ir import LegalOperation


def _parse_path_filter(raw: str) -> tuple[tuple[str, str], ...]:
    parts: list[tuple[str, str]] = []
    for chunk in raw.split("/"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            kind, label = chunk.split(":", 1)
        elif "=" in chunk:
            kind, label = chunk.split("=", 1)
        else:
            raise ValueError(
                f"path filter segments must use kind:label or kind=label syntax: {raw!r}"
            )
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            raise ValueError(f"invalid path filter segment: {chunk!r}")
        parts.append((kind, label))
    if not parts:
        raise ValueError(f"empty path filter: {raw!r}")
    return tuple(parts)


def _format_path(path: tuple[tuple[str, str], ...]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _path_matches(filter_path: tuple[tuple[str, str], ...], target_path: tuple[tuple[str, str], ...]) -> bool:
    return len(filter_path) <= len(target_path) and target_path[: len(filter_path)] == filter_path


def _op_touches_filters(op: "LegalOperation", filters: list[tuple[tuple[str, str], ...]]) -> bool:
    if not filters:
        return True
    target_paths = [tuple(op.target.path)] + [tuple(target.path) for target in getattr(op, "targets", [])]
    destination = getattr(op, "destination", None)
    if destination is not None:
        target_paths.append(tuple(destination.path))
    return any(
        _path_matches(path_filter, candidate)
        for path_filter in filters
        for candidate in target_paths
    )


def _serialize_payload(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "kind": getattr(payload, "kind", ""),
        "label": getattr(payload, "label", ""),
        "text": getattr(payload, "text", ""),
        "child_count": len(getattr(payload, "children", []) or []),
    }


def _serialize_operation(source_id: str, op: "LegalOperation") -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_id": source_id,
        "sequence": op.sequence,
        "action": str(op.action),
        "target": list(op.target.path),
        "target_text": _format_path(tuple(op.target.path)),
        "payload": _serialize_payload(op.payload),
    }
    if op.destination is not None:
        data["destination"] = list(op.destination.path)
        data["destination_text"] = _format_path(tuple(op.destination.path))
    if op.anchor is not None:
        data["anchor"] = list(op.anchor.path)
        data["anchor_text"] = _format_path(tuple(op.anchor.path))
    if op.source is not None:
        data["source"] = {
            "statute_id": op.source.statute_id,
            "title": op.source.title,
            "enacted": op.source.enacted,
            "effective": op.source.effective,
            "expires": op.source.expires,
            "raw_text": op.source.raw_text,
        }
    return data


def _load_index(
    *,
    data_dir: Path | None,
    index_path: Path | None,
):
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index

    if index_path is not None:
        return load_no_amendment_index(index_path)
    return build_no_amendment_index(data_dir)


def build_no_op_trace_report(
    *,
    base_id: str,
    data_dir: Path | None = None,
    index_path: Path | None = None,
    path_filters: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    from lawvm.norway.commencement import build_no_law_report
    from lawvm.norway.grafter import iter_no_document_change_ops
    from lawvm.norway.sources import load_no_amendment_artifact_bytes, resolve_no_source_path

    index = _load_index(data_dir=data_dir, index_path=index_path)
    source_path = resolve_no_source_path(Path(index.data_dir) if index.data_dir else data_dir)
    law_report = build_no_law_report(index, base_id=base_id)

    normalized_filters = [_parse_path_filter(raw) for raw in (path_filters or [])]
    source_summaries: list[dict[str, Any]] = []
    all_ops: list[dict[str, Any]] = []

    for source in law_report["sources"]:
        source_id = str(source["source_id"])
        matched_ops: list[dict[str, Any]] = []
        compiled_op_count = 0
        try:
            html_bytes = load_no_amendment_artifact_bytes(
                source_id,
                str(source.get("archive", "")),
                str(source.get("member_name", "")),
                source_path,
            )
        except Exception as exc:  # pragma: no cover - surfaced in report
            summary = dict(source)
            summary["compiled_op_count"] = 0
            summary["matched_op_count"] = 0
            summary["error"] = str(exc)
            source_summaries.append(summary)
            continue

        if html_bytes is not None:
            for group_base, ops in iter_no_document_change_ops(html_bytes, source_id):
                if group_base != base_id:
                    continue
                compiled_op_count += len(ops)
                for op in ops:
                    if not _op_touches_filters(op, normalized_filters):
                        continue
                    serialized = _serialize_operation(source_id, op)
                    matched_ops.append(serialized)
                    all_ops.append(serialized)

        summary = dict(source)
        summary["compiled_op_count"] = compiled_op_count
        summary["matched_op_count"] = len(matched_ops)
        if matched_ops:
            summary["matched_ops"] = matched_ops[:limit]
        source_summaries.append(summary)

    if normalized_filters:
        matching_sources = [item for item in source_summaries if int(item.get("matched_op_count", 0)) > 0]
    else:
        matching_sources = list(source_summaries)

    displayed_sources = list(matching_sources)
    if isinstance(limit, int) and limit >= 0:
        displayed_sources = displayed_sources[:limit]
        all_ops = all_ops[:limit]

    return {
        "base_id": base_id,
        "path_filters": [raw for raw in (path_filters or [])],
        "path_filter_count": len(normalized_filters),
        "data_dir": str(source_path),
        "title": law_report["title"],
        "replay_status": law_report["replay_status"],
        "executable_replay_status": law_report["executable_replay_status"],
        "amendment_count": law_report["amendment_count"],
        "source_count": len(source_summaries),
        "matched_source_count": len(matching_sources),
        "op_count": len(all_ops),
        "source_truncated": len(displayed_sources) < len(matching_sources),
        "op_truncated": len(all_ops) < sum(int(item.get("matched_op_count", 0)) for item in source_summaries),
        "sources": displayed_sources,
        "ops": all_ops,
    }


def main(args: "argparse.Namespace") -> None:
    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    path_filters = list(getattr(args, "path", []) or [])
    limit = getattr(args, "limit", 20)

    report = build_no_op_trace_report(
        base_id=args.base_id,
        data_dir=data_dir,
        index_path=index_path,
        path_filters=path_filters,
        limit=limit,
    )

    if getattr(args, "json", False):
        from enum import Enum

        def _enum_default(obj: object) -> object:
            if isinstance(obj, Enum):
                return obj.value
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        print(json.dumps(report, ensure_ascii=False, indent=2, default=_enum_default))
        return

    print()
    print("=== Norway Op Trace ===")
    print(f"  base id          : {report['base_id']}")
    if report["title"]:
        print(f"  title            : {report['title']}")
    print(f"  replay status    : {report['replay_status']}")
    print(f"  executable status: {report['executable_replay_status']}")
    print(f"  amendment count  : {report['amendment_count']}")
    if report["path_filters"]:
        print(f"  path filters     : {', '.join(report['path_filters'])}")
    print(
        "  sources/ops      : "
        f"sources={report['matched_source_count']}/{report['source_count']} | "
        f"ops={report['op_count']}"
    )
    if report["source_truncated"] or report["op_truncated"]:
        print("  truncated        : yes")
    if report["sources"]:
        print("  sources:")
        for item in report["sources"]:
            title = item["title"] or "(untitled)"
            print(
                f"    {item['source_id']} | {item['effective_status']} | {title} | "
                f"compiled={item.get('compiled_op_count', 0)} | matched={item.get('matched_op_count', 0)}"
            )
    if report["ops"]:
        print("  ops:")
        for item in report["ops"]:
            payload = item.get("payload") or {}
            payload_text = payload.get("text", "")
            print(
                f"    {item['source_id']}#{item['sequence']} | {item['action']} | "
                f"{item['target_text']}"
            )
            if payload_text:
                print(f"      payload : {payload_text}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lawvm no-op-trace")
    parser.add_argument("base_id")
    parser.add_argument("--data-dir", dest="data_dir")
    parser.add_argument("--index", dest="index")
    parser.add_argument(
        "--path",
        dest="path",
        action="append",
        default=[],
        help="Path filter in kind:label[/kind:label...] form.",
    )
    parser.add_argument("--limit", dest="limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    return parser


def cli(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    main(args)


if __name__ == "__main__":
    cli()
