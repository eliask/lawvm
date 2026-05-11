"""Shared JSON payload builders for replay-oriented CLI commands."""
from __future__ import annotations

from typing import Any, Iterable


def _address_to_str(address: Any) -> str:
    path = getattr(address, "path", None)
    if not path:
        return str(address)
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _adjudication_kind_counts(adjudications: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for adjudication in adjudications:
        kind = _text_or_none(getattr(adjudication, "kind", None)) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
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
    return {
        "jurisdiction": "no",
        "base_id": result.base_id,
        "as_of": result.as_of,
        "title": _text_or_none(getattr(result, "base_title", None)),
        "error": _text_or_none(getattr(result, "error", None)),
        "mode": "replay",
        "ops_count": int(getattr(result, "n_ops", 0) or 0),
        "adjudications_count": len(adjudications),
        "adjudication_kind_counts": _adjudication_kind_counts(adjudications),
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
        },
        "applied_amendments": list(getattr(result, "amendments_applied", []) or []),
        "failed_amendments": [],
        "skipped_amendments": {
            "future": list(getattr(result, "amendments_skipped_future", []) or []),
            "contingent": list(getattr(result, "amendments_skipped_contingent", []) or []),
            "unknown_effective": list(getattr(result, "amendments_skipped_unknown_effective", []) or []),
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
        "adjudication_kind_counts": _adjudication_kind_counts(adjudications),
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
) -> dict[str, Any]:
    return {
        "jurisdiction": "uk",
        "base_id": statute_id,
        "as_of": pit_date,
        "title": None,
        "error": None,
        "mode": "enacted_only" if enacted_only else "replay",
        "ops_count": int(n_ops),
        "adjudications_count": 0,
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
