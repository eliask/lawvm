"""Interlink projection and rendered-span placement for transition-graph exports.

The viewer consumes precomputed semantic link rows. Citation recognition and
rendered placement both happen in LawVM; JavaScript only paints rows that
already carry rendered address/segment/character coordinates.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable

from lawvm.core.interlinks import INTERLINK_ROW_COLUMNS
from lawvm.core.ir import IRNode


@dataclasses.dataclass(frozen=True, slots=True)
class LawvmInterlinkRow:
    interlink_id: str
    source_jurisdiction: str
    source_work_kind: str
    source_local_id: str
    source_work_id: str
    source_locator: str | None
    surface_text: str
    surface_kind: str
    role: str
    target_jurisdiction: str | None
    target_work_kind: str | None
    target_local_id: str | None
    target_work_id: str | None
    target_locator: str | None
    target_url: str | None
    candidate_work_ids: str | None
    resolution_status: str
    confidence: str
    resolver_id: str
    source_artifact_id: str | None
    source_span_byte_offset: int | None
    source_span_byte_len: int | None
    rendered_statute_id: str | None
    rendered_effective_date: str | None
    rendered_address: str | None
    rendered_segment_index: int | None
    rendered_char_start: int | None
    rendered_char_end: int | None
    valid_at_start: str | None
    valid_at_end: str | None
    detail_json: str

    @classmethod
    def from_mapping(cls, row: dict[str, object]) -> "LawvmInterlinkRow":
        missing = [col for col in INTERLINK_ROW_COLUMNS if col not in row]
        if missing:
            raise ValueError(f"lawvm_interlinks row missing required columns: {missing}")
        return cls(
            interlink_id=_text(row["interlink_id"]),
            source_jurisdiction=_text(row["source_jurisdiction"]),
            source_work_kind=_text(row["source_work_kind"]),
            source_local_id=_text(row["source_local_id"]),
            source_work_id=_text(row["source_work_id"]),
            source_locator=_optional_text(row["source_locator"]),
            surface_text=_text(row["surface_text"]),
            surface_kind=_text(row["surface_kind"]),
            role=_text(row["role"]),
            target_jurisdiction=_optional_text(row["target_jurisdiction"]),
            target_work_kind=_optional_text(row["target_work_kind"]),
            target_local_id=_optional_text(row["target_local_id"]),
            target_work_id=_optional_text(row["target_work_id"]),
            target_locator=_optional_text(row["target_locator"]),
            target_url=_optional_text(row["target_url"]),
            candidate_work_ids=_optional_text(row["candidate_work_ids"]),
            resolution_status=_text(row["resolution_status"]),
            confidence=_text(row["confidence"]),
            resolver_id=_text(row["resolver_id"]),
            source_artifact_id=_optional_text(row["source_artifact_id"]),
            source_span_byte_offset=_optional_int(row["source_span_byte_offset"]),
            source_span_byte_len=_optional_int(row["source_span_byte_len"]),
            rendered_statute_id=_optional_text(row["rendered_statute_id"]),
            rendered_effective_date=_optional_text(row["rendered_effective_date"]),
            rendered_address=_optional_text(row["rendered_address"]),
            rendered_segment_index=_optional_int(row["rendered_segment_index"]),
            rendered_char_start=_optional_int(row["rendered_char_start"]),
            rendered_char_end=_optional_int(row["rendered_char_end"]),
            valid_at_start=_optional_text(row["valid_at_start"]),
            valid_at_end=_optional_text(row["valid_at_end"]),
            detail_json=_text(row["detail_json"] or "{}"),
        )

    def sql_values(self) -> tuple[object, ...]:
        return dataclasses.astuple(self)

    def with_rendered_span(
        self,
        *,
        interlink_id: str,
        statute_id: str,
        effective_date: str,
        address: str,
        segment_index: int,
        char_start: int,
        char_end: int,
    ) -> "LawvmInterlinkRow":
        return dataclasses.replace(
            self,
            interlink_id=interlink_id,
            rendered_statute_id=statute_id,
            rendered_effective_date=effective_date,
            rendered_address=address,
            rendered_segment_index=segment_index,
            rendered_char_start=char_start,
            rendered_char_end=char_end,
        )

    def with_target_enrichment(
        self,
        *,
        target_url: str | None,
        detail_json: str,
    ) -> "LawvmInterlinkRow":
        return dataclasses.replace(
            self,
            target_url=target_url,
            detail_json=detail_json,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class LawvmInterlinkTargetRow:
    target_key: str
    target_jurisdiction: str
    target_work_kind: str
    target_local_id: str
    target_work_id: str | None
    target_locator: str | None
    target_url: str | None
    target_links_json: str
    preview_status: str
    preview_source: str
    title: str
    locator_label: str
    hierarchy_json: str
    preview_text: str
    detail_json: str

    def sql_values(self) -> tuple[object, ...]:
        return dataclasses.astuple(self)


@dataclasses.dataclass(frozen=True, slots=True)
class LawvmInterlinkTargetRef:
    key: str
    jurisdiction: str
    work_kind: str
    local_id: str
    work_id: str | None
    locator: str | None


InterlinkTargetResolver = Callable[[LawvmInterlinkTargetRef], LawvmInterlinkTargetRow]
InterlinkProjector = Callable[[str, object | None], list[LawvmInterlinkRow]]


@dataclasses.dataclass(frozen=True, slots=True)
class InterlinkTargetPreviewContext:
    """Neutral context for jurisdiction-owned target preview resolution."""

    source_statute_id: str
    corpus: object | None


InterlinkTargetResolverWithContext = Callable[
    [LawvmInterlinkTargetRef, InterlinkTargetPreviewContext],
    LawvmInterlinkTargetRow,
]


@dataclasses.dataclass(frozen=True, slots=True)
class LawvmInterlinkExportProvider:
    project_interlinks: InterlinkProjector
    resolve_target: InterlinkTargetResolverWithContext | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RenderedTextSegment:
    date: str
    address: str
    segment_index: int
    text: str


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return int(text)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def enrich_lawvm_interlink_targets(
    rows: list[LawvmInterlinkRow],
    *,
    target_resolver: InterlinkTargetResolver | None = None,
) -> tuple[list[LawvmInterlinkRow], list[LawvmInterlinkTargetRow]]:
    """Attach deduplicated target-preview metadata to interlink rows.

    The viewer row remains lightweight: each mention carries a ``target_key`` in
    ``detail_json`` and shared preview/URL material lives in
    ``lawvm_interlink_targets``. Jurisdiction-specific URL and preview logic is
    supplied by ``target_resolver``; this helper only owns neutral dedup wiring.
    """
    target_refs: dict[str, LawvmInterlinkTargetRef] = {}
    for row in rows:
        target_ref = _target_ref_for_row(row)
        if target_ref is not None:
            target_refs.setdefault(target_ref.key, target_ref)

    target_rows_by_key = {
        target_ref.key: (
            target_resolver(target_ref)
            if target_resolver is not None
            else default_interlink_target_row(target_ref)
        )
        for target_ref in sorted(target_refs.values(), key=lambda ref: ref.key)
    }

    enriched_rows: list[LawvmInterlinkRow] = []
    for row in rows:
        target_ref = _target_ref_for_row(row)
        if target_ref is None:
            enriched_rows.append(row)
            continue
        detail = json.loads(row.detail_json or "{}")
        detail["target_key"] = target_ref.key
        target_row = target_rows_by_key[target_ref.key]
        enriched_rows.append(
            row.with_target_enrichment(
                target_url=row.target_url or target_row.target_url,
                detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True),
            )
        )
    return enriched_rows, list(target_rows_by_key.values())


def default_interlink_target_row(target_ref: LawvmInterlinkTargetRef) -> LawvmInterlinkTargetRow:
    return LawvmInterlinkTargetRow(
        target_key=target_ref.key,
        target_jurisdiction=target_ref.jurisdiction,
        target_work_kind=target_ref.work_kind,
        target_local_id=target_ref.local_id,
        target_work_id=target_ref.work_id,
        target_locator=target_ref.locator,
        target_url=None,
        target_links_json="[]",
        preview_status="unsupported",
        preview_source="",
        title="",
        locator_label=target_ref.locator or "",
        hierarchy_json="[]",
        preview_text="",
        detail_json=json.dumps({"status": "unsupported"}, ensure_ascii=False, sort_keys=True),
    )


def _target_ref_for_row(row: LawvmInterlinkRow) -> LawvmInterlinkTargetRef | None:
    jurisdiction = row.target_jurisdiction or ""
    work_kind = row.target_work_kind or ""
    local_id = row.target_local_id or _local_id_from_work_id(row.target_work_id)
    if not jurisdiction or not work_kind or not local_id:
        return None
    locator = _normalize_interlink_locator(row.target_locator)
    key = "|".join((jurisdiction, work_kind, local_id, locator))
    return LawvmInterlinkTargetRef(
        key=key,
        jurisdiction=jurisdiction,
        work_kind=work_kind,
        local_id=local_id,
        work_id=row.target_work_id,
        locator=locator or None,
    )


def _local_id_from_work_id(work_id: str | None) -> str:
    if not work_id:
        return ""
    parts = work_id.split(":")
    return parts[-1].strip() if parts else ""


_ADDRESSABLE_KINDS = frozenset({
    "part",
    "chapter",
    "section",
    "subsection",
    "paragraph",
    "subparagraph",
})

_PLACEABLE_SURFACE_KINDS = frozenset({
    "prose_ref",
    "xml_ref",
    "preparatory_ref",
    "effect_feed_ref",
    "manual_claim_ref",
})


def _child_text(node: IRNode, kind: str) -> str:
    for child in node.children:
        if str(child.kind) == kind and child.text:
            return child.text.strip()
    return ""


def _node_address_string(path: tuple[tuple[str, str], ...]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _addr_component_for_node(node: IRNode, ordinal: int) -> str:
    label = str(node.label or "").strip()
    if label:
        return re.sub(r"\s+", "", label)
    num = _child_text(node, "num")
    if num:
        cleaned = re.sub(r"[§).]", "", num)
        cleaned = re.sub(r"\s+", "", cleaned.strip())
        if cleaned:
            return cleaned
    return str(ordinal)


def _inline_text_segments_for_node(node: IRNode) -> list[str]:
    segments: list[str] = []
    if node.text and node.text.strip():
        segments.append(node.text.strip())
    for child in node.children:
        kind = str(child.kind)
        if kind in _ADDRESSABLE_KINDS or kind in {"num", "heading"}:
            continue
        if child.text and child.text.strip():
            segments.append(child.text.strip())
    return segments


def rendered_text_segments(
    date: str,
    root: IRNode,
    slice_prefix: str,
) -> list[RenderedTextSegment]:
    segments: list[RenderedTextSegment] = []

    def _in_slice(addr: str) -> bool:
        if not slice_prefix:
            return True
        return addr == slice_prefix or addr.startswith(slice_prefix + "/")

    def _walk(node: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        counts: dict[str, int] = {}
        for child in node.children:
            kind = str(child.kind)
            label = child.label or ""
            if kind in _ADDRESSABLE_KINDS and label:
                counts[kind] = counts.get(kind, 0) + 1
                path = prefix + ((kind, _addr_component_for_node(child, counts[kind])),)
                addr = _node_address_string(path)
                if _in_slice(addr):
                    for segment_index, text in enumerate(_inline_text_segments_for_node(child)):
                        segments.append(
                            RenderedTextSegment(
                                date=date,
                                address=addr,
                                segment_index=segment_index,
                                text=text,
                            )
                        )
                _walk(child, path)
            else:
                _walk(child, prefix)

    _walk(root, ())
    return segments


def _normalize_interlink_locator(locator: str | None) -> str:
    if not locator:
        return ""
    text = locator.strip()
    parts: list[str] = []
    for raw_part in text.split("/"):
        if ":" not in raw_part:
            continue
        kind, value = raw_part.split(":", 1)
        kind = kind.strip()
        if kind == "sec":
            kind = "section"
        normalized_value = re.sub(r"\s+", "", value.strip())
        parts.append(f"{kind}:{normalized_value}")
    return "/".join(parts)


def _has_placeable_surface(row: LawvmInterlinkRow) -> bool:
    """Whether a row's surface text is expected to occur in rendered prose.

    Jurisdiction adapters own sentinel labels such as XML element names or
    metadata edge names. The neutral placer only attempts string placement for
    actual rendered-reference surfaces; other rows remain valid semantic edges
    but are not painted inline unless they already carry explicit rendered
    spans.
    """
    return row.surface_kind in _PLACEABLE_SURFACE_KINDS and bool(row.surface_text.strip())


def _segment_matches_locator(segment_addr: str, locator: str) -> bool:
    if not locator:
        return True
    segment_parts = segment_addr.split("/")
    locator_parts = locator.split("/")
    if len(locator_parts) > len(segment_parts):
        return False
    width = len(locator_parts)
    for index in range(0, len(segment_parts) - width + 1):
        if segment_parts[index:index + width] == locator_parts:
            return True
    return False


def place_surface_text_spans(
    surface_text: str,
    source_locator: str | None,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[tuple[str, RenderedTextSegment, int]]:
    """Locate a surface string in the rendered text, one match per change-date.

    The neutral span-placement primitive shared by every viewer surface that
    paints inline over the rendered body (interlinks AND surface overlays). For
    each date it returns ``(date, segment, char_start)`` only when ``surface_text``
    occurs EXACTLY ONCE among that date's segments whose address matches
    ``source_locator`` (an empty/None locator matches every segment). Ambiguous
    dates (zero or multiple occurrences) yield no placement — the caller keeps the
    row unplaced rather than guessing a span.
    """
    surface = surface_text
    if not surface.strip():
        return []
    locator = _normalize_interlink_locator(source_locator)
    by_date: list[tuple[str, RenderedTextSegment, int]] = []
    for date, segments in segments_by_date.items():
        matches: list[tuple[RenderedTextSegment, int]] = []
        for segment in segments:
            if not _segment_matches_locator(segment.address, locator):
                continue
            start = segment.text.find(surface)
            if start >= 0:
                matches.append((segment, start))
        if len(matches) == 1:
            segment, start = matches[0]
            by_date.append((date, segment, start))
    return by_date


def _placement_candidates(
    row: LawvmInterlinkRow,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[tuple[str, RenderedTextSegment, int]]:
    if not _has_placeable_surface(row):
        return []
    return place_surface_text_spans(row.surface_text, row.source_locator, segments_by_date)


def place_lawvm_interlinks(
    rows: list[LawvmInterlinkRow],
    *,
    statute_id: str,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[LawvmInterlinkRow]:
    placed: list[LawvmInterlinkRow] = []
    for row in rows:
        if row.rendered_address:
            placed.append(row)
            continue
        candidates = _placement_candidates(row, segments_by_date)
        if not candidates:
            placed.append(row)
            continue
        for index, (date, segment, start) in enumerate(candidates):
            placed.append(
                row.with_rendered_span(
                    interlink_id=f"{row.interlink_id}:rendered:{index}",
                    statute_id=statute_id,
                    effective_date=date,
                    address=segment.address,
                    segment_index=segment.segment_index,
                    char_start=start,
                    char_end=start + len(row.surface_text),
                )
            )
    return placed
