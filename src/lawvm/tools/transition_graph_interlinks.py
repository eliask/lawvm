"""Interlink projection and rendered-span placement for transition-graph exports.

The viewer consumes precomputed semantic link rows. Citation recognition and
rendered placement both happen in LawVM; JavaScript only paints rows that
already carry rendered address/segment/character coordinates.
"""
from __future__ import annotations

import dataclasses
import re

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
    if value is None or value == "":
        return None
    return int(value)


def _lawvm_interlink_rows(rows: list[dict[str, object]] | None) -> list[LawvmInterlinkRow]:
    if not rows:
        return []
    return [LawvmInterlinkRow.from_mapping(row) for row in rows]


def project_lawvm_interlinks(statute_id: str, corpus: object) -> list[LawvmInterlinkRow]:
    """Project neutral LawVM interlinks for the viewer export."""
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    rows, _diagnostics = _project_interlinks_for_statute(statute_id, corpus)
    return _lawvm_interlink_rows(rows)


_INTERLINK_NON_SURFACE_TEXT = frozenset({
    "",
    "ref_element",
    "eu_text_pattern",
    "REPEALS",
    "ISSUED_UNDER",
    "ISSUES",
})

_ADDRESSABLE_KINDS = frozenset({
    "part",
    "chapter",
    "section",
    "subsection",
    "paragraph",
    "subparagraph",
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
        cleaned = re.sub(r"luku", "", cleaned, flags=re.IGNORECASE)
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
    if text.startswith("sec_"):
        return "section:" + re.sub(r"\s+", "", text.removeprefix("sec_").replace("_", ""))
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


def _placement_candidates(
    row: LawvmInterlinkRow,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[tuple[str, RenderedTextSegment, int]]:
    surface = row.surface_text
    if surface in _INTERLINK_NON_SURFACE_TEXT:
        return []
    locator = _normalize_interlink_locator(row.source_locator)
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
