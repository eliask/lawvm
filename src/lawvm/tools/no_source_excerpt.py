"""lawvm no-source-excerpt -- bounded Norway source excerpts by literal needle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    import argparse


_VALID_MODES = {"auto", "current", "original", "amendment"}


def _normalize_text(raw: bytes) -> str:
    from lawvm.norway.sources import repair_mojibake

    text = raw.decode("utf-8", errors="replace")
    return repair_mojibake(text).lstrip("\ufeff")


def _format_excerpt(text: str, start: int, end: int, context: int) -> str:
    excerpt = text[start:end].replace("\r", " ").replace("\n", " ")
    excerpt = " ".join(excerpt.split())
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(text) else ""
    return f"{prefix}{excerpt}{suffix}".strip()


def _find_hits(text: str, needle: str, context: int, max_hits: int) -> list[dict[str, object]]:
    if not needle:
        raise ValueError("needle must not be empty")
    hits: list[dict[str, object]] = []
    pos = 0
    while True:
        idx = text.find(needle, pos)
        if idx < 0:
            break
        start = max(0, idx - context)
        end = min(len(text), idx + len(needle) + context)
        hits.append(
            {
                "offset": idx,
                "start": start,
                "end": end,
                "excerpt": _format_excerpt(text, start, end, context),
            }
        )
        if len(hits) >= max_hits:
            break
        pos = idx + max(1, len(needle))
    return hits


def _source_candidates(source_id: str, mode: str) -> list[tuple[str, Callable[[str, Path | None], bytes | None]]]:
    from lawvm.norway.sources import (
        load_no_amendment_bytes,
        load_no_current_bytes,
        load_no_original_lti_bytes,
    )

    if mode not in _VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "current":
        return [("current", load_no_current_bytes)]
    if mode == "original":
        return [("original", load_no_original_lti_bytes)]
    if mode == "amendment":
        return [("amendment", load_no_amendment_bytes)]
    if source_id.startswith("no/lovtid/"):
        return [("amendment", load_no_amendment_bytes)]
    if source_id.startswith("no/lov/"):
        return [
            ("current", load_no_current_bytes),
            ("original", load_no_original_lti_bytes),
        ]
    return [
        ("current", load_no_current_bytes),
        ("original", load_no_original_lti_bytes),
        ("amendment", load_no_amendment_bytes),
    ]


def _load_source_text(source_id: str, data_dir: Path | None, mode: str) -> tuple[str, str, str, str]:
    from lawvm.norway.sources import (
        no_amendment_locator,
        no_current_locator,
        no_original_locator,
        resolve_no_source_path,
    )

    data_dir = resolve_no_source_path(data_dir)
    for kind, loader in _source_candidates(source_id, mode):
        payload = loader(source_id, data_dir)
        if payload is None:
            continue
        if kind == "current":
            locator = no_current_locator(source_id) if source_id.startswith("no/lov/") else source_id
        elif kind == "original":
            locator = no_original_locator(source_id) if source_id.startswith("no/lov/") else source_id
        else:
            locator = no_amendment_locator(source_id) if source_id.startswith("no/lovtid/") else source_id
        return kind, locator, data_dir.as_posix(), _normalize_text(payload)
    raise FileNotFoundError(f"no Norway source content found for {source_id!r} in {data_dir}")


def _build_report(
    source_id: str,
    needles: list[str],
    *,
    data_dir: Path | None = None,
    mode: str = "auto",
    context: int = 160,
    max_hits: int = 5,
) -> dict[str, object]:
    resolved_kind, locator, resolved_data_dir, text = _load_source_text(source_id, data_dir, mode)
    needle_reports: list[dict[str, object]] = []
    for needle in needles:
        hits = _find_hits(text, needle, context, max_hits)
        needle_reports.append(
            {
                "needle": needle,
                "match_count": len(hits),
                "hits": hits,
            }
        )
    return {
        "source_id": source_id,
        "resolved_source_kind": resolved_kind,
        "resolved_locator": locator,
        "data_dir": resolved_data_dir,
        "mode": mode,
        "context": context,
        "max_hits": max_hits,
        "needle_count": len(needles),
        "needles": needle_reports,
    }


def main(args: "argparse.Namespace") -> None:
    source_id = getattr(args, "source_id", None)
    if not source_id:
        raise ValueError("source_id is required")
    needles = list(getattr(args, "needles", None) or [])
    if not needles:
        raise ValueError("at least one needle is required")
    context = int(getattr(args, "context", 160) or 160)
    max_hits = int(getattr(args, "max_hits", 5) or 5)
    mode = str(getattr(args, "mode", "auto") or "auto")
    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None

    report = _build_report(
        source_id,
        needles,
        data_dir=data_dir,
        mode=mode,
        context=context,
        max_hits=max_hits,
    )

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Source Excerpt ===")
    print(f"  source id           : {report['source_id']}")
    print(f"  resolved kind       : {report['resolved_source_kind']}")
    print(f"  resolved locator    : {report['resolved_locator']}")
    print(f"  data dir            : {report['data_dir']}")
    print(f"  mode                : {report['mode']}")
    print(f"  context             : {report['context']}")
    print(f"  max hits / needle   : {report['max_hits']}")
    needles_report = cast(list[dict[str, object]], report["needles"])
    for item in needles_report:
        print(f"  needle: {item['needle']} (matches={item['match_count']})")
        hits = cast(list[dict[str, object]], item["hits"])
        if not hits:
            print("    (no matches)")
            continue
        for hit in hits:
            print(f"    @{hit['offset']}  {hit['excerpt']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id")
    parser.add_argument("needles", nargs="+")
    parser.add_argument("--data-dir")
    parser.add_argument("--mode", choices=sorted(_VALID_MODES), default="auto")
    parser.add_argument("--context", type=int, default=160)
    parser.add_argument("--max-hits", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    main(parser.parse_args())
