"""Project replay evidence into stable meta rows without duplicate keys."""

from __future__ import annotations

from typing import Any, Mapping


def project_meta_rows(
    rows: list[Mapping[str, Any]],
    *,
    meta_key: str,
    replay_meta_out: dict[str, object] | None,
    dedup_keys: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Project rows into replay_meta_out, deduplicating by *dedup_keys*."""
    projected = [dict(row) for row in rows]
    if replay_meta_out is None:
        return projected

    existing = replay_meta_out.get(meta_key)
    if existing is None:
        replay_meta_out[meta_key] = projected
        return projected

    merged = list(existing) if isinstance(existing, list) else []
    seen = {
        tuple(str(row.get(key) or "") for key in dedup_keys)
        for row in merged
        if isinstance(row, dict)
    }
    for row in projected:
        if dedup_keys:
            key = tuple(str(row.get(field) or "") for field in dedup_keys)
            if key in seen:
                continue
            seen.add(key)
        merged.append(row)
    replay_meta_out[meta_key] = merged
    return projected


def project_finding_details(
    findings: list[object],
    *,
    meta_key: str,
    replay_meta_out: dict[str, object] | None,
    detail_keys: tuple[str, ...] = ("kind", "rule_id", "phase"),
) -> list[dict[str, object]]:
    """Project finding details into replay_meta_out without message duplication."""
    rows: list[dict[str, object]] = []
    for finding in findings:
        detail = getattr(finding, "detail", None) or {}
        if not isinstance(detail, dict):
            continue
        row = {key: detail.get(key) for key in detail_keys if key in detail}
        row["kind"] = getattr(finding, "kind", row.get("kind"))
        row["source_statute"] = getattr(finding, "source_statute", "")
        rows.append(row)
    return project_meta_rows(
        rows,
        meta_key=meta_key,
        replay_meta_out=replay_meta_out,
        dedup_keys=detail_keys,
    )


__all__ = ["project_finding_details", "project_meta_rows"]
