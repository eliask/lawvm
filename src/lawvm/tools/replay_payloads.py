"""Shared JSON payload builders for replay-oriented CLI commands."""
from __future__ import annotations

from typing import Any, Iterable

from lawvm.core.adjudication_evidence import (
    adjudication_finding_evidence_rows,
    adjudication_kind_counts,
    text_or_none,
)


def _address_to_str(address: Any) -> str:
    path = getattr(address, "path", None)
    if not path:
        return str(address)
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _text_or_none(value: Any) -> str | None:
    return text_or_none(value)


def _text_field(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _adjudication_to_dict(adjudication: Any) -> dict[str, Any]:
    return {
        "kind": _text_field(getattr(adjudication, "kind", None), default="compile_adjudication"),
        "message": _text_field(getattr(adjudication, "message", None)),
        "source_statute": _text_field(getattr(adjudication, "source_statute", None)),
        "op_id": _text_field(getattr(adjudication, "op_id", None)),
        "detail": dict(getattr(adjudication, "detail", {}) or {}),
    }


def _rejection_rule_counts(rejections: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        rule_id = _text_field(rejection.get("rule_id"), default="unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def build_no_replay_payload(
    result: Any,
    *,
    archive_path: str | None = None,
    index_path: str | None = None,
    commencement_path: str | None = None,
    index_stale: bool = False,
    replayed_text: str | None = None,
) -> dict[str, Any]:
    adjudications = list(getattr(result, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="norway",
        base_id=result.base_id,
        as_of=result.as_of,
    )
    return {
        "jurisdiction": "no",
        "base_id": result.base_id,
        "as_of": result.as_of,
        "title": _text_or_none(getattr(result, "base_title", None)),
        "error": _text_or_none(getattr(result, "error", None)),
        "mode": "replay",
        "ops_count": int(getattr(result, "n_ops", 0) or 0),
        "adjudications_count": len(adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "adjudications": [_adjudication_to_dict(adjudication) for adjudication in adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": archive_path,
            "index": index_path,
            "commencement": commencement_path,
            "index_stale": index_stale,
        },
        "amendment_counts": {
            "total": len(getattr(result, "amendments_scanned", []) or []),
            "matched": len(getattr(result, "amendments_scanned", []) or []),
            "applied": len(getattr(result, "amendments_applied", []) or []),
            "failed": 0,
            "future": len(getattr(result, "amendments_skipped_future", []) or []),
            "contingent": len(getattr(result, "amendments_skipped_contingent", []) or []),
            "unknown_effective": len(getattr(result, "amendments_skipped_unknown_effective", []) or []),
            "missing_source": len(getattr(result, "amendments_skipped_missing_source", []) or []),
        },
        "applied_amendments": list(getattr(result, "amendments_applied", []) or []),
        "failed_amendments": [],
        "skipped_amendments": {
            "future": list(getattr(result, "amendments_skipped_future", []) or []),
            "contingent": list(getattr(result, "amendments_skipped_contingent", []) or []),
            "unknown_effective": list(getattr(result, "amendments_skipped_unknown_effective", []) or []),
            "missing_source": list(getattr(result, "amendments_skipped_missing_source", []) or []),
        },
        "oracle": {
            "available": False,
            "id": None,
            "comparison_class": None,
            "eid_similarity": None,
        },
        "consistency": {
            "consistent": None,
            "divergence_count": None,
            "mismatch_count": None,
            "ops_missing_count": None,
            "consolidated_missing_count": None,
        },
        "timeline": {
            "mode": None,
            "provisions": None,
            "versions": None,
            "pit_materialized_eids": None,
        },
        "divergences": [],
        "replayed_text": replayed_text,
    }


def build_ee_replay_payload(
    result: Any,
    *,
    archive_path: str | None = None,
    replayed_text: str | None = None,
    residual_summary: Any = None,
) -> dict[str, Any]:
    residual_by_address = {}
    if residual_summary is not None:
        residual_by_address = getattr(residual_summary, "record_by_address", {}) or {}
    adjudications = list(getattr(result, "adjudications", []) or [])
    finding_rows = adjudication_finding_evidence_rows(
        adjudications,
        frontend_id="estonia",
        base_id=result.base_id,
        as_of=result.as_of,
    )
    divergences = []
    for divergence in list(getattr(result, "divergences", []) or []):
        address = _address_to_str(getattr(divergence, "address", ""))
        record = residual_by_address.get(address)
        divergences.append(
            {
                "address": address,
                "divergence_type": _text_or_none(getattr(divergence, "divergence_type", None)),
                "replay_text": _text_or_none(getattr(divergence, "ops_text", None)),
                "oracle_text": _text_or_none(getattr(divergence, "consolidated_text", None)),
                "residual_bucket": _text_or_none(getattr(record, "bucket", None)) if record else None,
                "residual_evidence": _text_or_none(getattr(record, "evidence", None)) if record else None,
            }
        )
    return {
        "jurisdiction": "ee",
        "base_id": result.base_id,
        "as_of": result.as_of,
        "title": _text_or_none(getattr(result, "base_title", None)),
        "error": _text_or_none(getattr(result, "error", None)),
        "mode": "replay",
        "ops_count": int(getattr(result, "n_ops", 0) or 0),
        "adjudications_count": len(adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(adjudications),
        "adjudications": [_adjudication_to_dict(adjudication) for adjudication in adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": archive_path,
            "index": None,
            "commencement": None,
            "index_stale": None,
        },
        "amendment_counts": {
            "total": len(getattr(result, "amendments_total", []) or []),
            "matched": len(getattr(result, "amendments_total", []) or []),
            "applied": len(getattr(result, "amendments_applied", []) or []),
            "failed": len(getattr(result, "amendments_failed", []) or []),
            "future": None,
            "contingent": None,
            "unknown_effective": None,
        },
        "applied_amendments": list(getattr(result, "amendments_applied", []) or []),
        "failed_amendments": list(getattr(result, "amendments_failed", []) or []),
        "skipped_amendments": {
            "other": list(getattr(result, "amendments_skipped", []) or []),
        },
        "oracle": {
            "available": getattr(result, "oracle", None) is not None,
            "id": _text_or_none(getattr(result, "oracle_id", None)),
            "comparison_class": _text_or_none(getattr(result, "comparison_class", None)),
            "eid_similarity": None,
        },
        "consistency": {
            "consistent": None if getattr(result, "oracle", None) is None else len(getattr(result, "divergences", []) or []) == 0,
            "divergence_count": len(getattr(result, "divergences", []) or []),
            "mismatch_count": int(getattr(result, "n_mismatch", 0) or 0),
            "ops_missing_count": int(getattr(result, "n_ops_missing", 0) or 0),
            "consolidated_missing_count": int(getattr(result, "n_con_missing", 0) or 0),
        },
        "timeline": {
            "mode": None,
            "provisions": len(getattr(result, "timelines", {}) or {}),
            "versions": None,
            "pit_materialized_eids": None,
        },
        "divergences": divergences,
        "replayed_text": replayed_text,
    }


def build_uk_replay_payload(
    *,
    statute_id: str,
    pit_date: str | None,
    enacted_only: bool,
    db_path: str,
    n_effects: int | None,
    n_ops: int,
    similarity: float | None,
    comparison_class: str | None,
    oracle_available: bool,
    n_provisions: int,
    n_versions: int | None,
    pit_materialized_eids: int | None,
    timeline_mode: str,
    replayed_text: str | None = None,
    adjudications: Iterable[Any] = (),
    effect_feed_parse_rejections: Iterable[dict[str, Any]] = (),
    lowering_rejections: Iterable[dict[str, Any]] = (),
    authority_rejections: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    replay_adjudications = list(adjudications)
    effect_feed_parse_rejection_rows = [dict(item) for item in effect_feed_parse_rejections]
    lowering_rejection_rows = [dict(item) for item in lowering_rejections]
    authority_rejection_rows = [dict(item) for item in authority_rejections]
    compile_rejections = [
        *effect_feed_parse_rejection_rows,
        *lowering_rejection_rows,
        *authority_rejection_rows,
    ]
    finding_rows = adjudication_finding_evidence_rows(
        replay_adjudications,
        frontend_id="uk",
        base_id=statute_id,
        as_of=pit_date or "latest",
    )
    return {
        "jurisdiction": "uk",
        "base_id": statute_id,
        "as_of": pit_date,
        "title": None,
        "error": None,
        "mode": "enacted_only" if enacted_only else "replay",
        "ops_count": int(n_ops),
        "adjudications_count": len(replay_adjudications),
        "adjudication_kind_counts": adjudication_kind_counts(replay_adjudications),
        "adjudications": [
            _adjudication_to_dict(adjudication) for adjudication in replay_adjudications
        ],
        "compile_rejection_count": len(compile_rejections),
        "compile_rejection_rule_counts": _rejection_rule_counts(compile_rejections),
        "compile_rejections": {
            "effect_feed_parse": effect_feed_parse_rejection_rows,
            "lowering": lowering_rejection_rows,
            "authority": authority_rejection_rows,
        },
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "source": {
            "archive": db_path,
            "index": None,
            "commencement": None,
            "index_stale": None,
        },
        "amendment_counts": {
            "total": n_effects,
            "matched": n_effects,
            "applied": None,
            "failed": None,
            "future": None,
            "contingent": None,
            "unknown_effective": None,
        },
        "applied_amendments": [],
        "failed_amendments": [],
        "skipped_amendments": {},
        "oracle": {
            "available": oracle_available,
            "id": None,
            "comparison_class": comparison_class or None,
            "eid_similarity": similarity,
        },
        "consistency": {
            "consistent": None,
            "divergence_count": None,
            "mismatch_count": None,
            "ops_missing_count": None,
            "consolidated_missing_count": None,
        },
        "timeline": {
            "mode": timeline_mode,
            "provisions": n_provisions,
            "versions": n_versions,
            "pit_materialized_eids": pit_materialized_eids,
        },
        "divergences": [],
        "replayed_text": replayed_text,
    }


def replay_text_from_ir(body: Any, *, irnode_to_text: Any) -> str | None:
    if body is None:
        return None
    return irnode_to_text(body)


def replay_text_from_nodes(nodes: Iterable[Any], *, irnode_to_text: Any) -> str | None:
    parts = [irnode_to_text(node) for node in nodes]
    parts = [part for part in parts if part]
    return "\n".join(parts) if parts else None
