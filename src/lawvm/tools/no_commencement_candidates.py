"""lawvm no-commencement-candidates -- find likely Norway force-setting source candidates."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse


_SOURCE_ID_RE = re.compile(r"^no/lovtid/(?P<date>\d{4}-\d{2}-\d{2})-(?P<num>\d+)$")
_WS_RE = re.compile(r"\s+")
_COMMENCEMENT_MARKER_RE = re.compile(
    r"\b(?:trer i kraft|trer i verk|trådte i kraft|trådde i kraft|ikrafttred|settes i kraft)\b",
    re.IGNORECASE,
)


def _normalize_text(raw: bytes) -> str:
    from lawvm.norway.sources import repair_mojibake

    text = raw.decode("utf-8", errors="replace")
    return repair_mojibake(text).lstrip("\ufeff")


def _normalize_match_text(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip().lower()


def _source_date(source_id: str) -> str:
    match = _SOURCE_ID_RE.match(source_id.strip())
    if not match:
        return ""
    return match.group("date")


def _source_short_id(source_id: str) -> str:
    return source_id.removeprefix("no/lovtid/")


def _excerpt(text: str, offset: int, needle_len: int, context: int = 140) -> str:
    start = max(0, offset - context)
    end = min(len(text), offset + needle_len + context)
    body = " ".join(text[start:end].replace("\r", " ").replace("\n", " ").split())
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(text) else ""
    return f"{prefix}{body}{suffix}".strip()


def _find_literal(text: str, needle: str) -> dict[str, object] | None:
    if not needle:
        return None
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return None
    return {
        "needle": needle,
        "offset": idx,
        "excerpt": _excerpt(text, idx, len(needle)),
    }


def _candidate_needles(
    *,
    source_id: str,
    source_title: str,
    base_ids: list[str],
    current_titles: dict[str, str],
) -> list[tuple[str, str, int]]:
    needles: list[tuple[str, str, int]] = []
    short_id = _source_short_id(source_id)
    if short_id:
        needles.append(("source_short_id", short_id, 100))
    if source_title:
        needles.append(("source_title", source_title, 40))
    for base_id in base_ids:
        base_short = base_id.removeprefix("no/lov/")
        if base_short:
            needles.append(("base_id", base_short, 18))
        title = current_titles.get(base_id, "").strip()
        if title:
            needles.append(("base_title", title, 12))
    deduped: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for kind, needle, score in needles:
        key = (kind, needle.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((kind, needle, score))
    return deduped


def _is_direct_match(matches: list[dict[str, object]]) -> bool:
    return any(str(match.get("kind", "")) in {"source_short_id", "source_title"} for match in matches)


def build_no_commencement_candidate_report(
    *,
    source_id: str,
    data_dir: Path | None = None,
    index_path: Path | None = None,
    limit: int = 20,
    direct_only: bool = False,
) -> dict[str, Any]:
    from lawvm.norway.index import build_no_amendment_index, load_no_amendment_index
    from lawvm.norway.statsrad import build_no_statsrad_commencement_candidate_scan
    from lawvm.norway.sources import (
        iter_no_amendment_artifacts,
        parse_header_value,
        resolve_no_source_path,
        load_no_current_law_titles,
    )

    data_dir = resolve_no_source_path(data_dir)
    if index_path is not None:
        index = load_no_amendment_index(index_path)
    else:
        index = build_no_amendment_index(data_dir)

    entry = next((item for item in index.entries if item.source_id == source_id), None)
    if entry is None:
        raise KeyError(f"source id not found in Norway amendment index: {source_id}")

    current_titles = load_no_current_law_titles(data_dir)
    source_date = _source_date(source_id)
    needles = _candidate_needles(
        source_id=source_id,
        source_title=entry.title,
        base_ids=list(entry.base_ids),
        current_titles=current_titles,
    )

    local_candidates: list[dict[str, Any]] = []
    for artifact in iter_no_amendment_artifacts(data_dir):
        candidate_id = artifact.logical_id
        if candidate_id == source_id:
            continue
        candidate_date = _source_date(candidate_id)
        if source_date and candidate_date and candidate_date < source_date:
            continue

        text = _normalize_text(artifact.payload)
        title = parse_header_value(artifact.payload, "title") or parse_header_value(
            artifact.payload, "titleShort"
        )
        effective_raw = parse_header_value(artifact.payload, "dateInForce")
        matches: list[dict[str, object]] = []
        score = 0
        for kind, needle, weight in needles:
            hit = _find_literal(text, needle)
            if hit is None and title:
                hit = _find_literal(title, needle)
            if hit is None:
                continue
            matches.append(
                {
                    "kind": kind,
                    "needle": needle,
                    "offset": hit["offset"],
                    "excerpt": hit["excerpt"],
                    "weight": weight,
                }
            )
            score += weight

        if not matches:
            continue

        commencement_marker = bool(_COMMENCEMENT_MARKER_RE.search(text) or _COMMENCEMENT_MARKER_RE.search(title))
        if commencement_marker:
            score += 15
        direct_match = _is_direct_match(matches)
        if direct_only and not direct_match:
            continue

        local_candidates.append(
            {
                "candidate_source": "local_corpus",
                "source_id": candidate_id,
                "title": title,
                "effective_header": effective_raw,
                "candidate_date": candidate_date,
                "commencement_marker": commencement_marker,
                "direct_match": direct_match,
                "match_count": len(matches),
                "score": score,
                "matches": matches[:5],
                "archive": artifact.source_name,
                "member_name": artifact.member_name,
            }
        )

    statsrad_report = build_no_statsrad_commencement_candidate_scan(
        source_id=source_id,
        source_title=entry.title,
        base_ids=list(entry.base_ids),
        current_titles=current_titles,
        data_dir=data_dir,
        source_date=source_date,
        limit=limit,
        direct_only=direct_only,
    )
    statsrad_candidates = list(statsrad_report.get("candidates", []))
    statsrad_event_artifact_diagnostics = list(statsrad_report.get("event_artifact_diagnostics", []))
    # Keep the evidence split explicit: local corpus and statsråd are separate
    # operator buckets even when they feed the same target source.
    candidate_groups = [
        {
            "candidate_source": "local_corpus",
            "candidate_count": len(local_candidates),
            "candidates": local_candidates[:limit],
        },
        {
            "candidate_source": "statsrad",
            "candidate_count": len(statsrad_candidates),
            "candidates": statsrad_candidates[:limit],
            "event_artifact_diagnostic_count": len(statsrad_event_artifact_diagnostics),
        },
    ]

    candidates = local_candidates + statsrad_candidates
    candidate_count = len(local_candidates) + int(statsrad_report.get("candidate_count", 0))

    candidates.sort(
        key=lambda item: (
            not bool(item["direct_match"]),
            -int(item["score"]),
            not bool(item["commencement_marker"]),
            str(item.get("candidate_source", "")) != "statsrad",
            str(item["candidate_date"]),
            str(item["source_id"]),
        )
    )

    return {
        "source_id": source_id,
        "source_title": entry.title,
        "source_effective_status": entry.effective_status,
        "source_raw_date_in_force": entry.raw_date_in_force,
        "source_date": source_date,
        "index_generated_at_utc": str(getattr(index, "generated_at_utc", "") or ""),
        "base_ids": list(entry.base_ids),
        "data_dir": str(data_dir),
        "direct_only": direct_only,
        "candidate_count": candidate_count,
        "candidates": candidates[:limit],
        "local_candidate_count": len(local_candidates),
        "local_candidates": local_candidates[:limit],
        "statsrad_candidate_count": len(statsrad_candidates),
        "statsrad_candidates": statsrad_candidates[:limit],
        "statsrad_event_artifact_diagnostic_count": len(statsrad_event_artifact_diagnostics),
        "statsrad_event_artifact_diagnostics": statsrad_event_artifact_diagnostics,
        "candidate_source_counts": {
            "local_corpus": len(local_candidates),
            "statsrad": len(statsrad_candidates),
        },
        "candidate_groups": candidate_groups,
    }


def main(args: "argparse.Namespace") -> None:
    data_dir_arg = getattr(args, "data_dir", None)
    data_dir = Path(data_dir_arg) if data_dir_arg else None
    index_arg = getattr(args, "index", None)
    index_path = Path(index_arg) if index_arg else None
    from lawvm.norway.commencement import build_no_commencement_candidate_artifact
    report = build_no_commencement_candidate_report(
        source_id=args.source_id,
        data_dir=data_dir,
        index_path=index_path,
        limit=int(getattr(args, "limit", 20) or 20),
        direct_only=bool(getattr(args, "direct_only", False)),
    )

    output_arg = getattr(args, "output", None)
    if output_arg:
        artifact = build_no_commencement_candidate_artifact(report, data_dir=data_dir, index_path=index_path)
        Path(output_arg).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print("=== Norway Commencement Candidates ===")
    print(f"  source id           : {report['source_id']}")
    if report["source_title"]:
        print(f"  title               : {report['source_title']}")
    print(f"  effective status    : {report['source_effective_status']}")
    print(f"  raw date in force   : {report['source_raw_date_in_force']}")
    print(f"  direct only         : {'yes' if report['direct_only'] else 'no'}")
    print(f"  candidates          : {report['candidate_count']}")
    print(f"  local candidates    : {report.get('local_candidate_count', 0)}")
    print(f"  statsrad evidence   : {report.get('statsrad_candidate_count', 0)}")
    if report.get("statsrad_event_artifact_diagnostic_count"):
        print(f"  statsrad diagnostics: {report['statsrad_event_artifact_diagnostic_count']}")
    if report.get("local_candidates"):
        print("  local candidates:")
        for item in report["local_candidates"]:
            marker = "yes" if item["commencement_marker"] else "no"
            direct = "yes" if item["direct_match"] else "no"
            print(
                f"  {item['source_id']} | score={item['score']} | direct={direct} | commencement={marker}"
                f" | {item['title'] or '(untitled)'}"
            )
            for match in item["matches"]:
                print(f"    [{match['kind']}] {match['needle']}")
                print(f"      {match['excerpt']}")
    if report.get("statsrad_candidates"):
        print("  statsrad evidence:")
        for item in report["statsrad_candidates"]:
            marker = "yes" if item["commencement_marker"] else "no"
            direct = "yes" if item["direct_match"] else "no"
            print(
                f"  {item['source_id']} | score={item['score']} | direct={direct} | commencement={marker}"
                f" | {item['title'] or '(untitled)'}"
            )
            for match in item["matches"]:
                print(f"    [{match['kind']}] {match['needle']}")
                print(f"      {match['excerpt']}")
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id")
    parser.add_argument("--data-dir")
    parser.add_argument("--index")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--direct-only", action="store_true")
    parser.add_argument("--output", metavar="FILE", help="write a serialized commencement candidate artifact")
    parser.add_argument("--json", action="store_true")
    main(parser.parse_args())
