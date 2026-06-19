"""Interlink projection and rendered-span placement for transition-graph exports.

The viewer consumes precomputed semantic link rows. Citation recognition and
rendered placement both happen in LawVM; JavaScript only paints rows that
already carry rendered address/segment/character coordinates.

Placement v0 (grammar9/grammar10 rulings, source-span occurrence grouping):
A single written reference expression — e.g. a range ``69 d-69 g §:ssä`` or a
coordination ``28 tai 69 c §:ssä`` — is extracted as several flattened per-target
rows that all share one source span. The placement layer groups those rows by
*source occurrence* (``surface_occurrence_id``), places the occurrence ONCE onto
the rendered text via a fail-closed normalized ladder, and attaches the whole
``resolution_set`` (all members meant — NOT an ambiguous candidate set) plus
``placement_status`` / ``placement_rule_id`` diagnostics to every member row.
The viewer then paints ONE anchor per occurrence and lists every member.

All v0 grouping/placement/resolution metadata rides inside ``detail_json`` under
the frozen grammar10 §9 names, so the SQLite/parquet column contract and the row
dataclass are unchanged (export-LOCAL grouping; no reference node-identity or
extraction-schema migration).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import unicodedata
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

    def with_detail_json(self, detail_json: str) -> "LawvmInterlinkRow":
        return dataclasses.replace(self, detail_json=detail_json)


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


# ── Placement v0: surface normalization + offset map ───────────────────────
#
# Normalization collapses the differences between a source-extracted surface and
# its rendered image that are not semantically meaningful: NBSP↔space, runs of
# whitespace, and the dash class (hyphen / en-dash / em-dash / minus). We never
# paint normalized coordinates — every normalized run carries an offset map back
# to the original rendered character offsets so the painted span is exact.

_DASH_CHARS = "-‐‑‒–—―−"
_DASH_RE = re.compile(f"[{_DASH_CHARS}]")
_WS_RE = re.compile(r"\s+")
PLACEMENT_NORMALIZATION_ID = "fi.viewer_place.norm.nbsp_ws_dash.v1"


def _normalize_surface_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize a surface for placement and return ``(normalized, offset_map)``.

    ``offset_map[i]`` is the original char offset that normalized char ``i`` came
    from; ``offset_map[len(normalized)]`` is ``len(text)`` (the end sentinel), so a
    normalized match ``[n0:n1]`` maps to original ``[offset_map[n0]:offset_map[n1]]``.
    Whitespace runs collapse to a single space; the dash class normalizes to ``-``.
    """
    norm_chars: list[str] = []
    offset_map: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace() or ch == "\xa0":
            norm_chars.append(" ")
            offset_map.append(i)
            i += 1
            while i < n and (text[i].isspace() or text[i] == "\xa0"):
                i += 1
            continue
        if _DASH_RE.match(ch):
            norm_chars.append("-")
        else:
            norm_chars.append(unicodedata.normalize("NFC", ch))
        offset_map.append(i)
        i += 1
    offset_map.append(n)
    return "".join(norm_chars), offset_map


def _normalize_surface(text: str) -> str:
    normalized, _ = _normalize_surface_with_map(text)
    return normalized


def _find_all(haystack: str, needle: str) -> list[int]:
    starts: list[int] = []
    if not needle:
        return starts
    pos = haystack.find(needle)
    while pos >= 0:
        starts.append(pos)
        pos = haystack.find(needle, pos + 1)
    return starts


# Placement status / rule-id vocabulary (grammar9 §3). placed_* statuses paint;
# unplaced_* do not. ``placed_ordinal_experimental`` is gated behind an explicit
# flag and is NOT the default ladder path.
PLACEMENT_STATUSES = (
    "placed_exact_unique",
    "placed_normalized_unique",
    "placed_context_unique",
    "placed_ordinal_experimental",
    "unplaced_absent",
    "unplaced_ambiguous",
    "unplaced_unsupported",
)

@dataclasses.dataclass(frozen=True, slots=True)
class OccurrencePlacement:
    """A single per-date placement of one surface occurrence."""

    date: str
    address: str
    segment_index: int
    char_start: int
    char_end: int
    status: str
    rule_id: str
    diagnostic: dict[str, object]


def place_occurrence_spans(
    surface_text: str,
    source_locator: str | None,
    segments_by_date: dict[str, list[RenderedTextSegment]],
    *,
    enable_ordinal_experimental: bool = False,
) -> list[OccurrencePlacement]:
    """Place one surface occurrence onto each change-date's rendered text.

    Fail-closed ladder (grammar9 §3): exact_unique -> normalized_unique ->
    normalized_context_unique -> (ordinal only behind an explicit experimental
    flag). >1 normalized match with no context-unique winner -> unplaced_ambiguous
    (not painted). 0 matches on a date -> no placement for that date (per-date
    surface absence is preserved). All normalized matches map back through the
    offset map to exact original rendered coordinates — normalized coordinates are
    never painted.
    """
    if not surface_text.strip():
        return []
    locator = _normalize_interlink_locator(source_locator)
    norm_surface = _normalize_surface(surface_text)
    placements: list[OccurrencePlacement] = []
    for date, segments in segments_by_date.items():
        scoped = [s for s in segments if _segment_matches_locator(s.address, locator)]
        # 1. exact_unique — exact surface occurs exactly once across scoped segments.
        exact_hits: list[tuple[RenderedTextSegment, int]] = []
        for segment in scoped:
            for start in _find_all(segment.text, surface_text):
                exact_hits.append((segment, start))
        if len(exact_hits) == 1:
            segment, start = exact_hits[0]
            placements.append(
                OccurrencePlacement(
                    date=date,
                    address=segment.address,
                    segment_index=segment.segment_index,
                    char_start=start,
                    char_end=start + len(surface_text),
                    status="placed_exact_unique",
                    rule_id="lawvm.viewer_place.exact_unique.v1",
                    diagnostic={"match_count": 1, "candidate_segment_count": len(scoped)},
                )
            )
            continue
        # 2/3. normalized matches with offset map back to exact rendered coords.
        norm_hits: list[tuple[RenderedTextSegment, int, int, str, list[int]]] = []
        for segment in scoped:
            norm_text, offset_map = _normalize_surface_with_map(segment.text)
            for nstart in _find_all(norm_text, norm_surface):
                nend = nstart + len(norm_surface)
                norm_hits.append((segment, nstart, nend, norm_text, offset_map))
        if not norm_hits and not exact_hits:
            # Per-date surface absence: do not paint.
            continue
        if len(norm_hits) == 1:
            segment, nstart, nend, _norm_text, offset_map = norm_hits[0]
            placements.append(
                OccurrencePlacement(
                    date=date,
                    address=segment.address,
                    segment_index=segment.segment_index,
                    char_start=offset_map[nstart],
                    char_end=offset_map[nend],
                    status="placed_normalized_unique",
                    rule_id="lawvm.viewer_place.normalized_unique.v1",
                    diagnostic={
                        "match_count": 1,
                        "candidate_segment_count": len(scoped),
                        "normalization_id": PLACEMENT_NORMALIZATION_ID,
                    },
                )
            )
            continue
        if len(norm_hits) > 1:
            # 3. normalized_context_unique (ladder rung 3) requires a stored
            #    source-side left/right context window to PREFER one candidate; the
            #    extraction schema does not carry one in v0, so we cannot break the
            #    tie by context here (the rendered-side context fingerprint alone is
            #    not a preference signal). Fail-closed: ambiguous unless the caller
            #    explicitly opts into the experimental scoped-ordinal fallback.
            ordinal_used = False
            if enable_ordinal_experimental:
                segment, nstart, nend, _norm_text, offset_map = norm_hits[0]
                placements.append(
                    OccurrencePlacement(
                        date=date,
                        address=segment.address,
                        segment_index=segment.segment_index,
                        char_start=offset_map[nstart],
                        char_end=offset_map[nend],
                        status="placed_ordinal_experimental",
                        rule_id="lawvm.viewer_place.ordinal_experimental.v1",
                        diagnostic={
                            "match_count": len(norm_hits),
                            "candidate_segment_count": len(scoped),
                            "normalization_id": PLACEMENT_NORMALIZATION_ID,
                            "source_occurrence_ordinal": 0,
                        },
                    )
                )
                ordinal_used = True
            if not ordinal_used:
                placements.append(
                    OccurrencePlacement(
                        date=date,
                        address=norm_hits[0][0].address,
                        segment_index=norm_hits[0][0].segment_index,
                        char_start=-1,
                        char_end=-1,
                        status="unplaced_ambiguous",
                        rule_id="lawvm.viewer_place.ambiguous.v1",
                        diagnostic={
                            "match_count": len(norm_hits),
                            "candidate_segment_count": len(scoped),
                            "normalization_id": PLACEMENT_NORMALIZATION_ID,
                        },
                    )
                )
    return placements


# Backward-compat shim: the overlay placer and a few callers still consume the
# simpler ``(date, segment, char_start)`` exactly-once primitive. Express it on
# top of the v0 ladder (only placed_* spans count; ambiguous/unplaced drop).
def place_surface_text_spans(
    surface_text: str,
    source_locator: str | None,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[tuple[str, RenderedTextSegment, int]]:
    """Locate a surface string in the rendered text, one placed span per date.

    The neutral span-placement primitive shared by viewer surfaces that paint
    inline over the rendered body. Returns ``(date, segment, char_start)`` for
    each date the occurrence places onto via the v0 ladder (exact_unique or
    normalized_unique). Ambiguous/absent dates yield no placement — the caller
    keeps the row unplaced rather than guessing a span.
    """
    out: list[tuple[str, RenderedTextSegment, int]] = []
    placements = place_occurrence_spans(surface_text, source_locator, segments_by_date)
    by_addr_seg: dict[tuple[str, str, int], RenderedTextSegment] = {}
    for date, segments in segments_by_date.items():
        for segment in segments:
            by_addr_seg[(date, segment.address, segment.segment_index)] = segment
    for placement in placements:
        if not placement.status.startswith("placed_"):
            continue
        segment = by_addr_seg.get(
            (placement.date, placement.address, placement.segment_index)
        )
        if segment is None:
            continue
        out.append((placement.date, segment, placement.char_start))
    return out


# ── Placement v0: source-occurrence grouping + resolution_set ──────────────


def surface_occurrence_id(
    *,
    work_id: str,
    source_locator: str | None,
    source_span_byte_offset: int | None,
    source_span_byte_len: int | None,
    surface_text: str,
    fallback_ordinal: int | None = None,
) -> str:
    """Stable id for one written reference occurrence (grammar9 §Step1).

    Primary key: ``hash(work_id, source_locator, span_offset, span_len,
    normalized_surface_text)``. Fallback (no source span): substitute the
    extraction ordinal for the span so distinct occurrences of the same string in
    the same locator do not collide.
    """
    normalized = _normalize_surface(surface_text)
    if source_span_byte_offset is not None and source_span_byte_len is not None:
        span_key = f"{source_span_byte_offset}:{source_span_byte_len}"
    else:
        span_key = f"ord:{fallback_ordinal if fallback_ordinal is not None else ''}"
    payload = "␟".join(
        (
            work_id or "",
            _normalize_interlink_locator(source_locator),
            span_key,
            normalized,
        )
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"occ_{digest}"


def _resolution_kind_for_group(rows: list[LawvmInterlinkRow]) -> str:
    """Classify a same-occurrence group's denotation (grammar10 §9).

    ``singleton`` for one member; ``finite_all_members`` for an explicitly
    enumerated set (range / coordination); ``open`` when no member carries a
    target (vague / non-enumerable surface — no fake target is invented).
    """
    members_with_target = [r for r in rows if (r.target_locator or r.target_work_id)]
    if not members_with_target:
        return "open"
    # Distinct member denotations within the occurrence.
    distinct = {(r.target_work_id, r.target_locator) for r in members_with_target}
    if len(distinct) <= 1:
        return "singleton"
    return "finite_all_members"


def _resolution_member(row: LawvmInterlinkRow) -> dict[str, object]:
    member: dict[str, object] = {
        "target_locator": row.target_locator,
        "target_work_id": row.target_work_id,
        "member_status": row.resolution_status,
    }
    detail = json.loads(row.detail_json or "{}")
    target_key = detail.get("target_key")
    if target_key:
        member["target_key"] = target_key
    return member


def _resolution_set_json(rows: list[LawvmInterlinkRow], kind: str) -> str:
    members = [_resolution_member(row) for row in rows if (row.target_locator or row.target_work_id)]
    # Deduplicate members preserving order (a target may repeat across rows).
    seen: set[tuple[object, object]] = set()
    unique_members: list[dict[str, object]] = []
    for member in members:
        key = (member.get("target_work_id"), member.get("target_locator"))
        if key in seen:
            continue
        seen.add(key)
        unique_members.append(member)
    return json.dumps(
        {"kind": kind, "members": unique_members},
        ensure_ascii=False,
        sort_keys=True,
    )


def _merge_detail(
    row: LawvmInterlinkRow,
    extra: dict[str, object],
) -> str:
    detail = json.loads(row.detail_json or "{}")
    detail.update(extra)
    return json.dumps(detail, ensure_ascii=False, sort_keys=True)


def _placement_candidates(
    row: LawvmInterlinkRow,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[OccurrencePlacement]:
    if not _has_placeable_surface(row):
        return []
    return place_occurrence_spans(row.surface_text, row.source_locator, segments_by_date)


def place_lawvm_interlinks(
    rows: list[LawvmInterlinkRow],
    *,
    statute_id: str,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[LawvmInterlinkRow]:
    """Group flattened rows by source occurrence, then place each occurrence once.

    Per grammar9/10 (export-LOCAL v0): rows that share a source occurrence are
    grouped; the occurrence is placed ONCE via the fail-closed ladder; the whole
    ``resolution_set`` (all members meant) plus placement diagnostics are attached
    (inside ``detail_json``), and ONE placed row per (occurrence, date) is emitted
    — collapsing the old N_targets x N_dates fan-out to 1 x N_dates. The viewer
    renders ONE anchor per ``surface_occurrence_id`` and lists every member from
    the resolution_set. When an occurrence does NOT place, its per-target member
    rows pass through (unplaced) so analytics retain every target; pre-placed and
    non-placeable rows likewise pass through, all annotated with grouping metadata.
    """
    # 1. Assign surface_occurrence_id to every row (placeable or not).
    groups: dict[str, list[LawvmInterlinkRow]] = {}
    group_order: list[str] = []
    occ_fields: dict[str, dict[str, object]] = {}
    for ordinal, row in enumerate(rows):
        occ_id = surface_occurrence_id(
            work_id=row.source_work_id,
            source_locator=row.source_locator,
            source_span_byte_offset=row.source_span_byte_offset,
            source_span_byte_len=row.source_span_byte_len,
            surface_text=row.surface_text,
            fallback_ordinal=ordinal,
        )
        if occ_id not in groups:
            groups[occ_id] = []
            group_order.append(occ_id)
        groups[occ_id].append(row)

    for occ_id in group_order:
        members = groups[occ_id]
        kind = _resolution_kind_for_group(members)
        occ_fields[occ_id] = {
            "surface_occurrence_id": occ_id,
            "resolution_kind": kind,
            "resolution_set_json": _resolution_set_json(members, kind),
            "resolution_set_size": len({
                (m.target_work_id, m.target_locator)
                for m in members
                if (m.target_locator or m.target_work_id)
            }),
        }

    placed: list[LawvmInterlinkRow] = []
    for occ_id in group_order:
        members = groups[occ_id]
        base_extra = occ_fields[occ_id]
        representative = members[0]

        # Pre-placed rows: keep them, just annotate with grouping metadata.
        if representative.rendered_address:
            for member in members:
                placed.append(member.with_detail_json(_merge_detail(member, dict(base_extra))))
            continue

        candidates = _placement_candidates(representative, segments_by_date)
        if not candidates:
            # No placement candidate on any date. Distinguish a placeable surface
            # that simply did not appear in the rendered text (unplaced_absent —
            # counted in the summary) from a non-placeable edge (e.g. metadata),
            # which carries no placement_status at all.
            placeable = _has_placeable_surface(representative)
            for member in members:
                extra = dict(base_extra)
                if placeable:
                    extra["placement_status"] = "unplaced_absent"
                placed.append(member.with_detail_json(_merge_detail(member, extra)))
            continue

        # Emit ONE placed row per (occurrence, date) when the occurrence places.
        # The full resolution_set rides in detail_json, so the per-target member
        # rows are NOT duplicated here (that is the fan-out collapse that fixes the
        # range/coordination defect).
        placed_outputs: list[LawvmInterlinkRow] = []
        for index, placement in enumerate(candidates):
            if not placement.status.startswith("placed_"):
                continue
            extra = dict(base_extra)
            extra["placement_status"] = placement.status
            extra["placement_rule_id"] = placement.rule_id
            extra["placement_diagnostic_json"] = json.dumps(
                placement.diagnostic, ensure_ascii=False, sort_keys=True
            )
            placed_outputs.append(
                representative.with_rendered_span(
                    interlink_id=f"{occ_id}:rendered:{index}",
                    statute_id=statute_id,
                    effective_date=placement.date,
                    address=placement.address,
                    segment_index=placement.segment_index,
                    char_start=placement.char_start,
                    char_end=placement.char_end,
                ).with_detail_json(
                    _merge_detail(representative, extra)
                )
            )

        if placed_outputs:
            placed.extend(placed_outputs)
            continue

        # Occurrence did not place on any date: pass through the per-target member
        # rows (unplaced) so analytics retain every target, annotated with the
        # grouping metadata + the (ambiguous/absent) status diagnostic.
        unplaced_status = next(
            (p.status for p in candidates if not p.status.startswith("placed_")),
            "unplaced_absent",
        )
        for member in members:
            extra = dict(base_extra)
            extra["placement_status"] = unplaced_status
            placed.append(member.with_detail_json(_merge_detail(member, extra)))
    return placed


def placement_summary(rows: list[LawvmInterlinkRow]) -> dict[str, int]:
    """Aggregate placement outcomes by status — a regression signal.

    Counts distinct surface occurrences by their best (placed) status, plus the
    total occurrence count. Reads grouping/placement fields from ``detail_json``.
    """
    best_status: dict[str, str] = {}
    for row in rows:
        detail = json.loads(row.detail_json or "{}")
        occ_id = detail.get("surface_occurrence_id")
        if not occ_id:
            continue
        status = str(detail.get("placement_status") or "")
        if not status:
            continue
        current = best_status.get(occ_id)
        if current is None:
            best_status[occ_id] = status
        elif status.startswith("placed_") and not current.startswith("placed_"):
            best_status[occ_id] = status
    summary: dict[str, int] = {status: 0 for status in PLACEMENT_STATUSES}
    summary["total_occurrences"] = len(best_status)
    for status in best_status.values():
        summary[status] = summary.get(status, 0) + 1
    return summary
