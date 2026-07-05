"""Finland semantic-interlink target previews for viewer exports.

This module is jurisdiction-owned enrichment for neutral ``lawvm_interlinks``.
The shared viewer/export substrate owns row shape and deduplication; Finland
owns Finlex URLs, Finnish locator labels, and local corpus preview extraction.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import xml.etree.ElementTree as ET
from typing import Protocol, cast

from lawvm.core.filter_result import FilterResult
from lawvm.core.stage_result import CoverageCertificate, PartitionResult
from lawvm.finland.section_text_extractor import (
    SectionTextExtractionResult,
    extract_sections_text,
)
from lawvm.finland.statute_id import engine_statute_id
from lawvm.tools.transition_graph_interlinks import (
    InterlinkTargetPreviewContext,
    LawvmInterlinkExportProvider,
    LawvmInterlinkRow,
    LawvmInterlinkTargetRef,
    LawvmInterlinkTargetRow,
    default_interlink_target_row,
)
from lawvm.tools.transition_graph_overlays import LawvmSurfaceOverlayExportProvider


class _PreviewCorpus(Protocol):
    def read_source(self, statute_id: str, /) -> bytes | None: ...
    def read_oracle(self, statute_id: str, /) -> bytes | None: ...


_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_CHAPTER_EID_RE = re.compile(r"chp_(\d{1,6})(?:__|$)", re.IGNORECASE)
_SECTION_LOCATOR_RE = re.compile(r"(?:^|/)section:([^/]+)")
_SUBSECTION_LOCATOR_RE = re.compile(r"(?:^|/)subsection:([^/]+)")
_PARAGRAPH_LOCATOR_RE = re.compile(r"(?:^|/)paragraph:([^/]+)")


@dataclasses.dataclass(frozen=True, slots=True)
class _ConsolidatedPreviewHeader:
    identity: dict[str, str]
    title: str
    chapter_titles: dict[str, str]


def build_fi_interlink_target_row(
    target_ref: LawvmInterlinkTargetRef,
    *,
    corpus: _PreviewCorpus,
    preview_cache: dict[str, object] | None = None,
) -> LawvmInterlinkTargetRow:
    """Build a Finnish preview row for one neutral interlink target."""
    if target_ref.jurisdiction != "fi" or target_ref.work_kind != "normative_act":
        return default_interlink_target_row(target_ref)

    engine_id = engine_statute_id(target_ref.local_id)
    if not _looks_like_engine_statute_id(engine_id):
        return _unsupported_fi_target_row(target_ref, preview_status="unsupported_fi_target_id")
    preview = _target_preview_payload(
        target_ref,
        engine_id=engine_id,
        corpus=corpus,
        preview_cache=preview_cache,
    )
    target_url = _finlex_lainsaadanto_url(
        engine_id,
        fragment=str(preview.get("target_fragment") or ""),
    )
    source_publication_url = _finlex_saadoskokoelma_url(engine_id)
    links = _target_links(target_url=target_url, source_publication_url=source_publication_url)
    preview["links"] = links
    return LawvmInterlinkTargetRow(
        target_key=target_ref.key,
        target_jurisdiction=target_ref.jurisdiction,
        target_work_kind=target_ref.work_kind,
        target_local_id=target_ref.local_id,
        target_work_id=target_ref.work_id,
        target_locator=target_ref.locator,
        target_url=target_url,
        target_links_json=json.dumps(links, ensure_ascii=False, sort_keys=True),
        preview_status=str(preview.get("preview_status") or ""),
        preview_source=str(preview.get("source") or ""),
        title=str(preview.get("title") or ""),
        locator_label=str(preview.get("locator_label") or ""),
        hierarchy_json=json.dumps(preview.get("hierarchy") or [], ensure_ascii=False, sort_keys=True),
        preview_text=str(preview.get("preview_text") or ""),
        detail_json=json.dumps(preview, ensure_ascii=False, sort_keys=True),
    )


@dataclasses.dataclass(frozen=True)
class InterlinkProjection(PartitionResult[LawvmInterlinkRow]):
    """Conserving carrier for transition-graph interlink projection (Audit C).

    Composes the canonical :class:`PartitionResult` (accepted = projected rows,
    plus typed core ``residuals`` and a ``coverage`` account) over the interlink
    projection. The reference/preparatory/inline diagnostics the projector emits
    were previously discarded here; they are now carried as typed ``residuals``
    so nothing is silently dropped. ``rows`` is a convenience alias for the
    accepted lane.
    """

    @property
    def rows(self) -> tuple[LawvmInterlinkRow, ...]:
        return self.accepted


def project_fi_interlinks_partition(
    statute_id: str,
    corpus: object,
) -> InterlinkProjection:
    """Project Finnish interlink rows as a conserving partition.

    Conservation (Audit C): the underlying projector returns ``(rows,
    diagnostics)``; the diagnostics were previously discarded by
    ``project_fi_interlinks_for_transition_graph``. They are now carried as typed
    ``residuals`` (blocking iff the diagnostic was blocking) plus a coverage
    account over rows + residuals, so the projection plane no longer silently
    drops its accounting.
    """
    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute(
        statute_id, cast(_PreviewCorpus, corpus)
    )
    interlink_rows = tuple(
        LawvmInterlinkRow.from_mapping(row) for row in projection.rows
    )
    coverage = CoverageCertificate(
        unit="interlink_rows",
        total=projection.coverage.total,
        owned=len(interlink_rows),
        residual=projection.coverage.residual,
        violation=projection.coverage.violation,
        totality_claimed=projection.coverage.totality_claimed,
    )
    return InterlinkProjection(
        FilterResult(accepted_items=interlink_rows),
        residuals=projection.residuals,
        coverage=coverage,
    )


def project_fi_interlinks_for_transition_graph(
    statute_id: str,
    corpus: object,
) -> list[LawvmInterlinkRow]:
    """Project Finnish citation/interlink rows for transition-graph export.

    Production consumer of :func:`project_fi_interlinks_partition`: it reads the
    projection's ``residuals`` (the diagnostics that were previously discarded)
    and surfaces any blocking residue on the export console (the projection-plane
    visibility surface, mirroring the export collision-warning convention) so the
    drop is no longer silent. The provider protocol fixes the return to the row
    list, so the accepted lane is returned to the caller.
    """
    projection = project_fi_interlinks_partition(statute_id, corpus)
    blocking = [residual for residual in projection.residuals if residual.blocking]
    if blocking:
        sample = "; ".join(residual.reason for residual in blocking[:5])
        print(
            f"[export] WARNING: fi interlinks for {statute_id} carry "
            f"{len(blocking)} blocking projection residual(s) that are not "
            f"emitted as interlink rows: {sample}",
            file=sys.stderr,
            flush=True,
        )
    return list(projection.rows)


def project_fi_surface_overlays_for_transition_graph(
    statute_id: str,
    corpus: object,
) -> list[dict[str, object]]:
    """Project Finnish Legal Surface Graph overlay rows for transition-graph export.

    Reuses the SAME projector the standalone ``export-fi-interlinks`` tool runs
    (``_project_overlays_for_statute``), so the row shape stays identical; the
    transition-graph exporter then places these whole-body rows onto its per-date
    rendered segments.
    """
    from lawvm.tools.export_fi_interlinks import _project_overlays_for_statute

    projection = _project_overlays_for_statute(statute_id, corpus)
    return list(projection.rows)


def fi_transition_graph_overlay_provider() -> LawvmSurfaceOverlayExportProvider:
    return LawvmSurfaceOverlayExportProvider(
        project_overlays=project_fi_surface_overlays_for_transition_graph,
    )


def resolve_fi_interlink_target_row(
    target_ref: LawvmInterlinkTargetRef,
    context: InterlinkTargetPreviewContext,
) -> LawvmInterlinkTargetRow:
    if context.corpus is None:
        return _unsupported_fi_target_row(target_ref, preview_status="missing_local_corpus")
    return build_fi_interlink_target_row(
        target_ref,
        corpus=cast(_PreviewCorpus, context.corpus),
        preview_cache=context.preview_cache,
    )


def fi_transition_graph_interlink_provider() -> LawvmInterlinkExportProvider:
    return LawvmInterlinkExportProvider(
        project_interlinks=project_fi_interlinks_for_transition_graph,
        resolve_target=resolve_fi_interlink_target_row,
    )


def _looks_like_engine_statute_id(engine_id: str) -> bool:
    year, sep, num = engine_id.partition("/")
    return bool(sep and year.isdigit() and num)


def _unsupported_fi_target_row(
    target_ref: LawvmInterlinkTargetRef,
    *,
    preview_status: str,
) -> LawvmInterlinkTargetRow:
    return LawvmInterlinkTargetRow(
        target_key=target_ref.key,
        target_jurisdiction=target_ref.jurisdiction,
        target_work_kind=target_ref.work_kind,
        target_local_id=target_ref.local_id,
        target_work_id=target_ref.work_id,
        target_locator=target_ref.locator,
        target_url=None,
        target_links_json="[]",
        preview_status=preview_status,
        preview_source="",
        title="",
        locator_label=_locator_label(target_ref.locator),
        hierarchy_json="[]",
        preview_text="",
        detail_json=json.dumps({"preview_status": preview_status}, ensure_ascii=False, sort_keys=True),
    )


def _finlex_lainsaadanto_url(engine_id: str, *, fragment: str = "") -> str | None:
    year, sep, num = engine_id.partition("/")
    if not sep or not year.isdigit() or not num:
        return None
    url = f"https://www.finlex.fi/fi/lainsaadanto/{year}/{num}"
    if fragment:
        url = f"{url}#{fragment}"
    return url


def _finlex_saadoskokoelma_url(engine_id: str) -> str | None:
    year, sep, num = engine_id.partition("/")
    if not sep or not year.isdigit() or not num:
        return None
    return f"https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/{year}/{num}"


def _target_links(
    *,
    target_url: str | None,
    source_publication_url: str | None,
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if target_url:
        links.append({
            "rel": "canonical",
            "label": "Finlex",
            "url": target_url,
        })
    if source_publication_url:
        links.append({
            "rel": "source_publication",
            "label": "Säädöskokoelma",
            "url": source_publication_url,
        })
    return links


def _target_preview_payload(
    target_ref: LawvmInterlinkTargetRef,
    *,
    engine_id: str,
    corpus: _PreviewCorpus,
    preview_cache: dict[str, object] | None = None,
) -> dict[str, object]:
    locator_label = _locator_label(target_ref.locator)
    base_payload: dict[str, object] = {
        "preview_status": "unsupported",
        "source": "",
        "title": "",
        "locator_label": locator_label,
        "hierarchy": [],
        "preview_text": "",
        "preview_date_consolidated": "",
        "preview_version_tag": "",
        "target_fragment": _finlex_fragment_from_locator(
            target_ref.locator,
            allow_bare_section=False,
        ),
    }
    xml_bytes = corpus.read_oracle(engine_id)
    if xml_bytes is None:
        source_xml = corpus.read_source(engine_id)
        if source_xml is None:
            return {**base_payload, "preview_status": "missing_local_corpus"}
        title = _doc_title(source_xml)
        return {
            **base_payload,
            "preview_status": "law_title_from_source_only",
            "source": "fi.read_source",
            "title": title,
        }

    header = _consolidated_preview_header(
        engine_id,
        xml_bytes,
        preview_cache=preview_cache,
    )
    identity = header.identity
    title = header.title
    chapter_titles = header.chapter_titles
    payload = {
        **base_payload,
        "preview_status": "law_title_only",
        "source": "fi.read_oracle.latest_consolidated",
        "title": title,
        **identity,
    }
    section_label = _section_label_from_locator(target_ref.locator)
    if not section_label:
        return payload
    sections = _consolidated_section_text_result(
        engine_id,
        xml_bytes,
        preview_cache=preview_cache,
    )
    matched_section = _matching_section_preview(
        target_ref.locator,
        sections,
        section_label,
    )
    if matched_section is None:
        return payload

    hierarchy: list[dict[str, str]] = []
    chapter_label = _chapter_label_from_section_key(matched_section.section_key)
    if chapter_label:
        hierarchy.append({
            "kind": "chapter",
            "label": chapter_label,
            "title": chapter_titles.get(chapter_label, ""),
        })
    hierarchy.append({
        "kind": "section",
        "label": matched_section.section_label,
        "title": matched_section.heading_text,
    })
    subsection_label = _subsection_label_from_locator(target_ref.locator)
    paragraph_label = _paragraph_label_from_locator(target_ref.locator)
    if subsection_label:
        hierarchy.append({"kind": "subsection", "label": subsection_label, "title": ""})
    if paragraph_label:
        hierarchy.append({"kind": "paragraph", "label": paragraph_label, "title": ""})
    section_fragment = _finlex_fragment_from_locator(
        matched_section.section_key,
        allow_bare_section=True,
    ) or str(payload.get("target_fragment") or "")
    target_fragment = _extend_fragment_with_provision_tail(
        section_fragment,
        subsection_label=subsection_label,
        paragraph_label=paragraph_label,
    )
    narrowed_preview = _narrow_preview_to_provision(
        matched_section.body_text,
        xml_bytes=xml_bytes,
        target_fragment=target_fragment if (subsection_label or paragraph_label) else "",
    )
    return {
        **payload,
        "preview_status": "resolved_latest_local_oracle_preview",
        "locator_label": locator_label or matched_section.section_label,
        "hierarchy": hierarchy,
        "preview_text": _short_preview(narrowed_preview),
        "preview_date_consolidated": (
            matched_section.valid_at_start.isoformat()
            if matched_section.valid_at_start is not None
            else str(identity.get("preview_date_consolidated") or "")
        ),
        "target_fragment": target_fragment,
    }


def _consolidated_preview_header(
    engine_id: str,
    xml_bytes: bytes,
    *,
    preview_cache: dict[str, object] | None,
) -> _ConsolidatedPreviewHeader:
    cache_key = f"fi.consolidated_preview_header:{engine_id}"
    if preview_cache is not None:
        cached = preview_cache.get(cache_key)
        if isinstance(cached, _ConsolidatedPreviewHeader):
            return cached
    root = ET.fromstring(xml_bytes)
    title, chapter_titles = _title_and_chapter_titles_from_root(root)
    header = _ConsolidatedPreviewHeader(
        identity=_consolidated_preview_identity_from_root(root),
        title=title,
        chapter_titles=chapter_titles,
    )
    if preview_cache is not None:
        preview_cache[cache_key] = header
    return header


def _consolidated_section_text_result(
    engine_id: str,
    xml_bytes: bytes,
    *,
    preview_cache: dict[str, object] | None,
) -> SectionTextExtractionResult:
    cache_key = f"fi.consolidated_section_text:{engine_id}"
    if preview_cache is not None:
        cached = preview_cache.get(cache_key)
        if isinstance(cached, SectionTextExtractionResult):
            return cached
    result = extract_sections_text(xml_bytes, engine_id)
    if preview_cache is not None:
        preview_cache[cache_key] = result
    return result


def _consolidated_preview_identity(xml_bytes: bytes) -> dict[str, str]:
    return _consolidated_preview_identity_from_root(ET.fromstring(xml_bytes))


def _consolidated_preview_identity_from_root(root: ET.Element) -> dict[str, str]:
    date_consolidated = ""
    version_tag = ""
    for date_el in root.iter(f"{{{_AKN_NS}}}FRBRdate"):
        if date_el.get("name") == "dateConsolidated":
            date_consolidated = date_el.get("date", "")
            break
    version_el = root.find(f".//{{{_AKN_NS}}}FRBRversionNumber")
    if version_el is not None:
        version_tag = version_el.get("value", "")
    return {
        "preview_date_consolidated": date_consolidated,
        "preview_version_tag": version_tag,
    }


def _doc_title(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    title_el = root.find(f".//{{{_AKN_NS}}}docTitle")
    if title_el is None:
        return ""
    return " ".join("".join(title_el.itertext()).split())


def _title_and_chapter_titles(xml_bytes: bytes) -> tuple[str, dict[str, str]]:
    return _title_and_chapter_titles_from_root(ET.fromstring(xml_bytes))


def _title_and_chapter_titles_from_root(root: ET.Element) -> tuple[str, dict[str, str]]:
    title_el = root.find(f".//{{{_AKN_NS}}}docTitle")
    title = " ".join("".join(title_el.itertext()).split()) if title_el is not None else ""
    chapter_titles: dict[str, str] = {}
    for chapter_el in root.iter(f"{{{_AKN_NS}}}chapter"):
        label = _chapter_label_from_eid(chapter_el.get("eId", ""))
        if not label:
            num_el = chapter_el.find(f"{{{_AKN_NS}}}num")
            num_text = " ".join("".join(num_el.itertext()).split()) if num_el is not None else ""
            label = re.sub(r"\s*luku\s*$", "", num_text, flags=re.IGNORECASE).strip()
        heading_el = chapter_el.find(f"{{{_AKN_NS}}}heading")
        heading = " ".join("".join(heading_el.itertext()).split()) if heading_el is not None else ""
        if label:
            chapter_titles[label] = heading
    return title, chapter_titles


def _chapter_label_from_eid(eid: str) -> str:
    match = _CHAPTER_EID_RE.search(eid or "")
    return match.group(1) if match else ""


def _matching_section_preview(
    locator: str | None,
    result: SectionTextExtractionResult,
    section_label: str,
):
    matches = [
        section
        for section in result.sections
        if _section_key_matches_locator(section.section_key, section_label, locator)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _section_key_matches_locator(section_key: str, section_label: str, locator: str | None) -> bool:
    normalized_locator = locator or ""
    if normalized_locator.startswith("chapter:"):
        chapter_section = "/".join(
            part
            for part in normalized_locator.split("/")
            if part.startswith("chapter:") or part.startswith("section:")
        )
        if chapter_section:
            return section_key == chapter_section
    return section_key == f"section:{section_label}" or section_key.endswith(f"/section:{section_label}")


def _section_label_from_locator(locator: str | None) -> str:
    match = _SECTION_LOCATOR_RE.search(locator or "")
    return match.group(1) if match else ""


def _subsection_label_from_locator(locator: str | None) -> str:
    match = _SUBSECTION_LOCATOR_RE.search(locator or "")
    return match.group(1) if match else ""


def _paragraph_label_from_locator(locator: str | None) -> str:
    match = _PARAGRAPH_LOCATOR_RE.search(locator or "")
    return match.group(1) if match else ""


def _extend_fragment_with_provision_tail(
    section_fragment: str,
    *,
    subsection_label: str,
    paragraph_label: str,
) -> str:
    """Append momentti/kohta anchor components to a section Finlex fragment.

    Finlex AKN eIds nest a section's momentti (subsection) as ``subsec_N`` and a
    kohta (item) as ``para_N`` under it, joined by ``__``
    (e.g. ``chp_1__sec_5__subsec_2__para_3`` — see the eId convention in
    ``finland/inline_repeal_stub.py`` and the oracle bodies). We therefore extend
    the resolved section fragment with those components in AKN document order.

    Returns ``section_fragment`` unchanged when there is no subsection/paragraph
    (byte-identical to the section-only path) or when the base fragment is empty.
    """
    if not section_fragment:
        return section_fragment
    fragment = section_fragment
    if subsection_label:
        prefix = _FINLEX_FRAGMENT_KINDS["subsection"]
        fragment = f"{fragment}__{prefix}_{subsection_label}"
    if paragraph_label:
        prefix = _FINLEX_FRAGMENT_KINDS["paragraph"]
        fragment = f"{fragment}__{prefix}_{paragraph_label}"
    return fragment


def _narrow_preview_to_provision(
    section_body_text: str,
    *,
    xml_bytes: bytes,
    target_fragment: str,
) -> str:
    """Narrow the section preview to the momentti/kohta element when resolvable.

    ``target_fragment`` is the full Finlex anchor of the cited provision
    (e.g. ``chp_1__sec_5__subsec_2``); when it names a momentti/kohta element
    present in the oracle body under an ``eId`` that starts with that anchor
    (version suffixes such as ``v20211030`` are tolerated), its inner text is
    returned instead of the whole section. Falls back to the full section body
    when the narrower is empty (no fragment, element absent, or element carries
    no visible text), preserving the section-only preview byte-for-byte.
    """
    if not target_fragment:
        return section_body_text
    root = ET.fromstring(xml_bytes)
    for el in root.iter():
        eid = el.get("eId", "")
        if not eid:
            continue
        # Tolerate an amendment version suffix on the leaf component
        # (e.g. ``…__subsec_2v20211030``): match the anchor as a prefix that
        # ends at a component boundary (``__``, a version marker, or end).
        if eid == target_fragment or eid.startswith(f"{target_fragment}__") or eid.startswith(f"{target_fragment}v"):
            narrowed = _element_visible_text(el)
            if narrowed:
                return narrowed
    return section_body_text


def _element_visible_text(el: ET.Element) -> str:
    """Visible inner text of an AKN element, skipping its own num/heading.

    Mirrors ``section_text_extractor._element_body_text``: the immediate
    ``<num>``/``<heading>`` children carry the label/title (surfaced elsewhere as
    ``locator_label``/``hierarchy``), not body content, so they are omitted from
    the preview. Nested structure (kohta, alakohta, …) is recursed into.
    """
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local in ("num", "heading"):
            if child.tail:
                parts.append(child.tail)
            continue
        parts.append("".join(child.itertext()))
        if child.tail:
            parts.append(child.tail)
    return " ".join("".join(parts).split())


_FINLEX_FRAGMENT_KINDS = {
    "part": "part",
    "chapter": "chp",
    "section": "sec",
    "subsection": "subsec",
    "paragraph": "para",
    "subparagraph": "subpara",
}


def _finlex_fragment_from_locator(locator: str | None, *, allow_bare_section: bool = False) -> str:
    if not locator:
        return ""
    if not allow_bare_section and locator.startswith("section:") and "/" not in locator:
        return ""
    parts: list[str] = []
    for raw_part in locator.split("/"):
        if ":" not in raw_part:
            return ""
        kind, value = raw_part.split(":", 1)
        prefix = _FINLEX_FRAGMENT_KINDS.get(kind)
        if prefix is None or not value:
            return ""
        parts.append(f"{prefix}_{value}")
    return "__".join(parts)


def _chapter_label_from_section_key(section_key: str) -> str:
    for part in section_key.split("/"):
        if part.startswith("chapter:"):
            return part.removeprefix("chapter:")
    return ""


def _locator_label(locator: str | None) -> str:
    if not locator:
        return ""
    labels: list[str] = []
    for part in locator.split("/"):
        if ":" not in part:
            continue
        kind, value = part.split(":", 1)
        if kind == "chapter":
            labels.append(f"{value} luku")
        elif kind == "section":
            labels.append(f"{value} §")
        elif kind == "subsection":
            labels.append(f"{value} mom.")
        elif kind == "paragraph":
            labels.append(f"{value} kohta")
        elif kind == "subparagraph":
            labels.append(f"{value} alakohta")
    return " › ".join(labels)


def _short_preview(text: str, limit: int = 420) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "..."
