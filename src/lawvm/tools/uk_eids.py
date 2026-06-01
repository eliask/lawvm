"""lawvm uk-eids -- inspect nearby UK EIDs/text by prefix."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, NamedTuple

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.uk_legislation.source_state import (
    uk_source_parse_observations_from_ir,
    uk_source_xml_parse_rejection,
    uk_source_state_wire_tuple as _source_state,
)

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


class _LimitedEIDRows(NamedTuple):
    rows: list[tuple[str, str]]
    total_matches: int
    truncated: bool


def _iter_prefixed_rows(
    eid_map: dict[str, str],
    text_map: dict[str, str],
    *,
    prefix: str,
) -> Iterable[tuple[str, str]]:
    wanted = prefix.lower()
    seen: set[str] = set()
    for eid in sorted(set(eid_map.values())):
        if eid.lower().startswith(wanted) and eid not in seen:
            seen.add(eid)
            yield eid, text_map.get(eid, "")


def _snippet(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _limit_rows_with_evidence(
    rows: list[tuple[str, str]],
    *,
    limit: int | None,
) -> _LimitedEIDRows:
    total_matches = len(rows)
    if limit is None:
        return _LimitedEIDRows(rows=rows, total_matches=total_matches, truncated=False)
    return _LimitedEIDRows(
        rows=rows[:limit],
        total_matches=total_matches,
        truncated=total_matches > limit,
    )


def _source_sha256(blob: bytes | None) -> str:
    if blob is None:
        return ""
    return hashlib.sha256(blob).hexdigest()


def _rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        rule_id = str(row.get("rule_id") or "unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _blocking_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if is_blocking_compile_record(row)]


def _eid_side_report_jsonable(
    *,
    statute_id: str,
    prefix: str,
    side: str,
    source_url: str,
    rows: list[tuple[str, str]],
    total_matches: int,
    truncated: bool,
    missing: bool,
    source_status: str,
    source_size: int,
    source_sha256: str,
    show_text: bool,
    source_parse_rejections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_parse_observation_rows = source_parse_rejections or []
    source_parse_rejection_rows = _blocking_rows(source_parse_observation_rows)
    row_payload: list[dict[str, str]] = []
    for eid, text in rows:
        row = {"eid": eid}
        if show_text:
            row["text"] = _snippet(text, limit=220)
        row_payload.append(row)
    return {
        "statute_id": statute_id,
        "side": side,
        "source_url": source_url,
        "source_status": source_status,
        "source_size": source_size,
        "source_sha256": source_sha256,
        "prefix": prefix,
        "missing": missing,
        "source_parse_failed": bool(source_parse_rejection_rows),
        "source_parse_observation_count": len(source_parse_observation_rows),
        "source_parse_observation_rule_counts": _rule_counts(source_parse_observation_rows),
        "source_parse_observations": source_parse_observation_rows,
        "source_parse_rejection_count": len(source_parse_rejection_rows),
        "source_parse_rejection_rule_counts": _rule_counts(source_parse_rejection_rows),
        "source_parse_rejections": source_parse_rejection_rows,
        "matches": total_matches,
        "emitted": len(rows),
        "truncated": truncated,
        "rows": row_payload,
    }


def uk_eids_report_jsonable(
    *,
    statute_id: str,
    archive_path: Path | str,
    prefix: str,
    side: str,
    show_text: bool,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    source_status_counts: dict[str, int] = {}
    source_parse_observation_rule_counts: dict[str, int] = {}
    source_parse_rejection_rule_counts: dict[str, int] = {}
    for report in reports:
        source_status = str(report["source_status"])
        source_status_counts[source_status] = source_status_counts.get(source_status, 0) + 1
        for rule_id, count in dict(report["source_parse_observation_rule_counts"]).items():
            rule_key = str(rule_id)
            source_parse_observation_rule_counts[rule_key] = (
                source_parse_observation_rule_counts.get(rule_key, 0) + int(count)
            )
        for rule_id, count in dict(report["source_parse_rejection_rule_counts"]).items():
            rule_key = str(rule_id)
            source_parse_rejection_rule_counts[rule_key] = (
                source_parse_rejection_rule_counts.get(rule_key, 0) + int(count)
            )

    summary = {
        "statute_id": statute_id,
        "side_count": len(reports),
        "missing_side_count": sum(1 for report in reports if report["missing"]),
        "available_side_count": sum(
            1 for report in reports if report["source_status"] == "available"
        ),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "source_parse_observation_count": sum(
            int(report["source_parse_observation_count"]) for report in reports
        ),
        "source_parse_observation_rule_counts": dict(
            sorted(source_parse_observation_rule_counts.items())
        ),
        "source_parse_rejection_count": sum(
            int(report["source_parse_rejection_count"]) for report in reports
        ),
        "source_parse_rejection_rule_counts": dict(
            sorted(source_parse_rejection_rule_counts.items())
        ),
        "match_count": sum(int(report["matches"]) for report in reports),
        "emitted_count": sum(int(report["emitted"]) for report in reports),
        "truncated_side_count": sum(1 for report in reports if report["truncated"]),
    }
    legacy_payload = {
        "report_kind": "uk_eids_report",
        "statute_id": statute_id,
        "archive_path": str(archive_path),
        "prefix": prefix,
        "side": side,
        "show_text": show_text,
        "sides": reports,
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_eids_report",
        schema="lawvm.uk_eids_report.v1",
        truth_claim="uk_eid_source_inspection_evidence_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": statute_id,
            "prefix": prefix,
            "side": side,
            "show_text": show_text,
            "db_path": str(archive_path),
        },
        filtered_summary=summary,
        rows=tuple(reports),
        rows_truncated=any(bool(report["truncated"]) for report in reports),
        detail={
            **legacy_payload,
            "safe_default": "use_only_as_source_eid_inspection_evidence",
            "forbidden_shortcuts": (
                "eid_prefix_match_as_target_authority",
                "eid_inspection_as_replay_authorization",
                "text_preview_as_payload_identity",
            ),
            "next_promotion_requires": (
                "source_instruction_witness",
                "target_identity",
                "payload_identity",
                "mutation_boundary_proof",
            ),
        },
    ).to_dict()


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes, parse_uk_statute_ir_bytes

    statute_id: str = args.statute_id
    prefix: str = args.prefix
    side: str = getattr(args, "side", "both")
    limit: int | None = getattr(args, "limit", None)
    show_text: bool = bool(getattr(args, "show_text", False))
    json_output: bool = bool(getattr(args, "json", False))
    db_arg = getattr(args, "db", None)

    if limit is not None and limit < 0:
        print("error: --limit must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    sides = ["base", "oracle"] if side == "both" else [side]

    reports: list[dict[str, Any]] = []
    with Farchive(db_path) as archive:
        for which in sides:
            enacted = which == "base"
            url = _archive_url_for_statute(statute_id, pit_date=None, enacted=enacted)
            blob = archive.get(url)
            source_status, source_size = _source_state(blob)
            source_sha256 = _source_sha256(blob)
            if source_status != "available":
                reports.append(_eid_side_report_jsonable(
                    statute_id=statute_id,
                    prefix=prefix,
                    side=which,
                    source_url=url,
                    rows=[],
                    total_matches=0,
                    truncated=False,
                    missing=True,
                    source_status=source_status,
                    source_size=source_size,
                    source_sha256=source_sha256,
                    show_text=show_text,
                ))
                continue
            assert blob is not None
            try:
                data = extract_eid_map_bytes(blob)
            except Exception as exc:
                source_parse_rejection = uk_source_xml_parse_rejection(
                    statute_id=statute_id,
                    side="enacted" if which == "base" else "oracle",
                    source_url=url,
                    exc=exc,
                )
                reports.append(_eid_side_report_jsonable(
                    statute_id=statute_id,
                    prefix=prefix,
                    side=which,
                    source_url=url,
                    rows=[],
                    total_matches=0,
                    truncated=False,
                    missing=True,
                    source_status=source_status,
                    source_size=source_size,
                    source_sha256=source_sha256,
                    show_text=show_text,
                    source_parse_rejections=[source_parse_rejection],
                ))
                continue
            source_parse_observations: list[dict[str, Any]] = []
            try:
                parsed_ir = parse_uk_statute_ir_bytes(
                    blob,
                    statute_id=statute_id,
                    version_label="enacted" if which == "base" else "oracle",
                    source_path=url,
                )
            except Exception as exc:
                source_parse_observations.append(
                    uk_source_xml_parse_rejection(
                        statute_id=statute_id,
                        side="enacted" if which == "base" else "oracle",
                        source_url=url,
                        exc=exc,
                    )
                )
            else:
                source_parse_observations.extend(uk_source_parse_observations_from_ir(parsed_ir))
            rows = list(_iter_prefixed_rows(data.get("eid_map", {}), data.get("text_map", {}), prefix=prefix))
            limited_rows = _limit_rows_with_evidence(rows, limit=limit)
            reports.append(_eid_side_report_jsonable(
                statute_id=statute_id,
                prefix=prefix,
                side=which,
                source_url=url,
                rows=limited_rows.rows,
                total_matches=limited_rows.total_matches,
                truncated=limited_rows.truncated,
                missing=False,
                source_status=source_status,
                source_size=source_size,
                source_sha256=source_sha256,
                show_text=show_text,
                source_parse_rejections=source_parse_observations,
            ))

    if json_output:
        print(json.dumps(
            uk_eids_report_jsonable(
                statute_id=statute_id,
                archive_path=db_path,
                prefix=prefix,
                side=side,
                show_text=show_text,
                reports=reports,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return

    print(f"Archive: {db_path}")
    print()
    for report in reports:
        print(f"{str(report['side']).upper()}: {statute_id}")
        print(f"  source: {report['source_url']}")
        print(f"  source_status: {report['source_status']} ({report['source_size']} bytes)")
        print(f"  source_sha256: {report['source_sha256'] or '(none)'}")
        if report["missing"]:
            if report.get("source_parse_failed"):
                print("  (unavailable: source XML parse rejected)")
                print(
                    "  source_parse_observation_rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in dict(
                            report.get("source_parse_observation_rule_counts") or {}
                        ).items()
                    )
                )
                print(
                    "  source_parse_rejection_rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in dict(
                            report.get("source_parse_rejection_rule_counts") or {}
                        ).items()
                    )
                )
                print()
                continue
            print(f"  (unavailable: {report['source_status']})")
            print()
            continue
        print(f"  prefix: {prefix}")
        print(f"  matches: {report['matches']}")
        print(f"  emitted: {report['emitted']}")
        print(f"  truncated: {str(report['truncated']).lower()}")
        for row in report["rows"]:
            print(f"  {row['eid']}")
            if show_text:
                print(f"    {row['text']}")
        print()
