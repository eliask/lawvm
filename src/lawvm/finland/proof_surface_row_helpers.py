"""Shared coercion and witness helpers for Finland proof-surface projections."""

from __future__ import annotations

import hashlib
import importlib
from typing import Any, Mapping

from lawvm.core.frontier_work_item import FrontierWorkItem, frontier_work_item_with_claim_template
from lawvm.core.source_witness import DigestWitness


def with_finland_claim_template(item: FrontierWorkItem) -> FrontierWorkItem:
    importlib.import_module("lawvm.finland.claim_kinds")
    return frontier_work_item_with_claim_template(item)

def kind_slug(kind: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(kind or "")).strip("_") or "unknown"

def positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def mapping_str_str(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key) and str(item)}


def mapping_str_int(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out[str(key)] = item
            continue
        text = str(item or "").strip()
        if text.isdigit():
            out[str(key)] = int(text)
    return out


def mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def authorization_rows_with_report(
    original_rows: tuple[Mapping[str, Any], ...],
    report_rows: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    """Preserve FI-local evidence fields while shared rows own control fields."""

    rows: list[Mapping[str, Any]] = []
    for index, report_row in enumerate(report_rows):
        original = original_rows[index] if index < len(original_rows) else {}
        rows.append({**dict(original), **dict(report_row)})
    return tuple(rows)


def count_by_field(rows: tuple[Mapping[str, Any], ...], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field_name) or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def count_values(values: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value or "__none__"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))

def object_sequence(value: Any) -> tuple[Mapping[str, Any] | object, ...]:
    if value is None:
        return ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return (value,)

def preview_digest_witness(text: str) -> DigestWitness | None:
    if not text:
        return None
    return DigestWitness(
        digest_algorithm="sha256",
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def bounded_bytes_preview(data: bytes, *, limit: int = 512) -> str:
    return data[:limit].decode("utf-8", errors="replace")


def field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def frontier_claim_template_status_counts(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    return count_values(
        tuple(
            str(item.get("suggested_claim_template_status") or "__none__")
            for item in frontier_items
        )
    )


def frontier_claim_template_kind_counts(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    kinds: list[str] = []
    for item in frontier_items:
        template = item.get("suggested_claim_template") or {}
        if isinstance(template, Mapping):
            kinds.append(str(template.get("claim_kind") or "__none__"))
        else:
            kinds.append("__none__")
    return count_values(tuple(kinds))


__all__ = [
    "authorization_rows_with_report",
    "bounded_bytes_preview",
    "count_by_field",
    "count_values",
    "field",
    "frontier_claim_template_kind_counts",
    "frontier_claim_template_status_counts",
    "kind_slug",
    "mapping_or_empty",
    "mapping_sequence",
    "mapping_str_int",
    "mapping_str_str",
    "object_sequence",
    "positive_int",
    "preview_digest_witness",
    "string_sequence",
    "with_finland_claim_template",
]
