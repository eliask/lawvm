"""UK affecting-source context extraction and source-lane recovery helpers."""
from __future__ import annotations

import copy
import re
from lxml import etree as ET
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Optional, Sequence

from lawvm.roman import (
    arabic_to_roman as _shared_arabic_to_roman,
    roman_to_arabic as _shared_roman_to_arabic,
)
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    _LEG_BASE,
    get_affecting_act_enacted_xml_from_archive,
)
from lawvm.uk_legislation.provision_extractor import (
    _EXTRACTION_CONTEXT_CACHE,
    _build_extraction_context,
    _get_id_sequence,
    _match_node,
    _sequence_tokens,
    extract_provision_element_from_bytes,
)
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
from lawvm.uk_legislation.source_state import (
    uk_affecting_act_article_schedule_payload_source_extracted,
    uk_affecting_act_block_amendment_payload_descendant_ref_rejection,
    uk_affecting_act_compound_payload_only_block_amendment_selected,
    uk_affecting_act_compound_reference_split_fallback,
    uk_affecting_act_current_shell_enacted_source_selected,
    uk_affecting_act_enacted_schedule_table_row_source_extracted,
    uk_affecting_act_implicit_first_subparagraph_context_ignored,
    uk_affecting_act_missing_current_enacted_source_selected,
    uk_affecting_act_nonaddressable_schedule_part_context_ignored,
    uk_affecting_act_outdented_child_source_selected,
    uk_affecting_act_parenthesized_range_source_extracted,
    uk_affecting_act_single_unnumbered_schedule_context_ignored,
    uk_affecting_act_schedule_part_standalone_split_rejection,
    uk_affecting_act_class_unmapped_rejection,
    uk_affecting_act_single_amendment_child_source_selected,
    uk_affecting_act_xml_missing_rejection,
    uk_affecting_act_xml_parse_rejection,
    uk_affecting_act_xml_too_small_rejection,
    uk_source_state_wire_tuple,
)
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag, _text_content

_COMPOUND_REFERENCE_KEYWORD_RE = re.compile(r"\b(?:Sch(?:edule)?|Part|Pt)\b", re.I)
_COMPOUND_REFERENCE_LEADING_BODY_RE = re.compile(
    r"\b(?:s|section|art|article|rule|reg|regulation)\.?\s*[0-9A-Za-z]+",
    re.I,
)
_SINGLE_UNNUMBERED_SCHEDULE_REF_RE = re.compile(
    r"^sch(?:edule)?\.?\s+(?P<schedule>[0-9A-Za-z]+)\s+"
    r"(?P<suffix>para(?:graph)?\.?\s+[0-9A-Za-z]+(?:\s*\([0-9A-Za-z]+\))*)$",
    re.I,
)


@dataclass(frozen=True)
class UKAffectingSourceContext:
    xml_bytes: Optional[bytes]
    root: Optional[ET._Element]
    parent_map: Optional[dict[ET._Element, ET._Element]]
    exact_id_map: dict[str, ET._Element]
    sequence_map: dict[tuple[str, ...], ET._Element]
    source_status: str
    source_size: int
    locator: str
    authority_layer: str
    provision_extractor: Callable[..., Optional[ET._Element]] = extract_provision_element_from_bytes
    provision_element_cache: dict[str, Optional[ET._Element]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )


class UKAffectingSourceContextBuild(NamedTuple):
    source_context: UKAffectingSourceContext
    parse_error: Optional[ET.ParseError]


class UKSelectedAffectingSource(NamedTuple):
    source_context: UKAffectingSourceContext
    extracted_element: Optional[ET._Element]
    observations: tuple[dict[str, Any], ...]


class UKSourceExtractionResult(NamedTuple):
    extracted_element: Optional[ET._Element]
    observations: tuple[dict[str, Any], ...]


_NO_SOURCE_EXTRACTION = UKSourceExtractionResult(extracted_element=None, observations=())


@dataclass(frozen=True, slots=True)
class UKEnactedScheduleTableRowMatch:
    row: ET._Element
    cells: tuple[ET._Element, ...]
    part_label: str
    source_label_text: str


def _first_amendment_container(el: Optional[ET._Element]) -> Optional[ET._Element]:
    if el is None:
        return None
    if _tag(el) in ("BlockAmendment", "InlineAmendment"):
        return el
    for child in el.iter():
        if child is not el and _tag(child) in ("BlockAmendment", "InlineAmendment"):
            return child
    return None


# ---------------------------------------------------------------------------
# §source_root_lifecycle: Plain-dict-backed parent-map and ancestor-chain
# caches with explicit eviction.
#
# Originally these were WeakKeyDictionary-backed, but lxml _Element objects do
# not support weak references, so plain dicts are used instead.  Memory safety
# is maintained entirely by explicit eviction via evict_source_root_caches()
# which the compile loop calls at each root's last-occurrence point.
#
# With 229 unique affecting-act roots for ukpga/1970/9 (~6 MB raw XML), all
# roots would accumulate if not evicted.  Explicit eviction keeps at most a
# handful of live roots at any point during compilation.
#
# IMPORTANT: These caches create reference cycles (parent_map values include
# root as a parent element, ancestor tuples include root as a terminal ancestor).
# Explicit eviction via evict_source_root_caches(root) must be called at the
# last-occurrence point to break the cycles immediately.  Without it, GC may
# not reclaim the memory promptly.
#
# _source_parent_map_cache:
#   dict[source_root → dict[child, parent]]
#   Values contain root as a parent element (creates cycle).
#
# _source_ancestor_chain_cache:
#   dict[source_root → dict[id(el), tuple[ET._Element, ...]]]
#   Inner tuples may contain root as terminal ancestor (creates cycle).
#   id(el) inner key is safe: el is alive while source_root is alive.
# ---------------------------------------------------------------------------

_source_parent_map_cache: dict[ET._Element, dict[ET._Element, ET._Element]] = {}

_source_ancestor_chain_cache: dict[ET._Element, dict[int, tuple[ET._Element, ...]]] = {}


def evict_source_root_caches(root: Optional[ET._Element]) -> None:
    """Explicitly release module-level cache entries for root.

    Call this after the last effect for a source root has been processed.
    Plain-dict caches (lxml _Element objects do not support weak references)
    retain root until explicit eviction.  Cycles exist because cache values
    include root as a parent element or terminal ancestor element:
      - _source_parent_map_cache: values contain root as a parent element
      - _source_ancestor_chain_cache: inner tuples contain root as terminal ancestor
      - _EXTRACTION_CONTEXT_CACHE: UKExtractionContext.parent_map contains root
      - table_sources caches: fee-table index and repeal-extent table hold root
    Explicit removal breaks these cycles immediately, making root eligible for
    reference-count GC.
    """
    if root is None:
        return
    _source_parent_map_cache.pop(root, None)
    _source_ancestor_chain_cache.pop(root, None)
    _EXTRACTION_CONTEXT_CACHE.pop(root, None)
    # Lazy import to avoid circular dependency: table_sources → uk_grafter → ...
    # does not import source_context, so this is safe.
    from lawvm.uk_legislation.table_sources import (  # noqa: PLC0415
        _REPEAL_EXTENT_TABLE_CACHE,
        _UK_FEE_TABLE_INDEX_CACHE,
    )
    _REPEAL_EXTENT_TABLE_CACHE.pop(root, None)
    _UK_FEE_TABLE_INDEX_CACHE.pop(root, None)


def _source_parent_map(
    source_root: ET._Element,
) -> dict[ET._Element, ET._Element]:
    """Return a cached parent map for source XML ancestor queries."""
    cached = _source_parent_map_cache.get(source_root)
    if cached is not None:
        return cached
    result: dict[ET._Element, ET._Element] = {
        child: parent for parent in source_root.iter() for child in parent
    }
    _source_parent_map_cache[source_root] = result
    return result


def _source_ancestor_chain(
    source_root: Optional[ET._Element],
    el: Optional[ET._Element],
) -> tuple[ET._Element, ...]:
    """Return closest-first source ancestors for an extracted source element."""
    if source_root is None or el is None:
        return ()
    if el is source_root:
        return ()
    inner = _source_ancestor_chain_cache.get(source_root)
    if inner is None:
        inner = {}
        _source_ancestor_chain_cache[source_root] = inner
    el_key = id(el)
    if el_key in inner:
        return inner[el_key]
    parent_map = _source_parent_map(source_root)
    if el in parent_map:
        ancestors: list[ET._Element] = []
        parent = parent_map.get(el)
        while parent is not None:
            ancestors.append(parent)
            if parent is source_root:
                break
            parent = parent_map.get(parent)
        result = tuple(ancestors)
        inner[el_key] = result
        return result

    target_id = el.get("id")
    path: list[ET._Element] = []

    def _walk(node: ET._Element, ancestors: tuple[ET._Element, ...]) -> bool:
        if node is el or (target_id and node.get("id") == target_id):
            path.extend(reversed(ancestors))
            return True
        for child in node:
            if _walk(child, (*ancestors, node)):
                return True
        return False

    if _walk(source_root, ()):
        result = tuple(path)
        inner[el_key] = result
        return result
    inner[el_key] = ()
    return ()


def _unique_source_ancestor_chain_by_tag_text(
    source_root: Optional[ET._Element],
    el: Optional[ET._Element],
) -> tuple[ET._Element, ...]:
    """Reattach a detached extracted fragment to a unique same-text source node."""
    if source_root is None or el is None:
        return ()
    target_tag = _tag(el)
    target_text = " ".join(_text_content(el).split())
    if not target_tag or not target_text:
        return ()
    matches: list[tuple[ET._Element, ...]] = []

    def _walk(node: ET._Element, ancestors: tuple[ET._Element, ...]) -> None:
        if _tag(node) == target_tag and " ".join(_text_content(node).split()) == target_text:
            matches.append(tuple(reversed(ancestors)))
        for child in node:
            _walk(child, (*ancestors, node))

    _walk(source_root, ())
    if len(matches) != 1:
        return ()
    return matches[0]


def _source_text_before_extracted_child(
    parent: ET._Element,
    extracted_el: Optional[ET._Element],
) -> str:
    """Return source text in an immediate parent before the extracted child row."""
    extracted_id = extracted_el.get("id") if extracted_el is not None else None
    parts: list[str] = []
    if parent.text:
        parts.append(parent.text)
    for child in parent:
        if child is extracted_el or (extracted_id and child.get("id") == extracted_id):
            break
        parts.append(_text_content(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(" ".join(parts).split())


def _source_previous_table_entry_label_context(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    rule_id: str,
) -> dict[str, str]:
    """Return an explicit table entry label from a previous sibling source row."""
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return {}
    parent = ancestors[0]
    children = list(parent)
    extracted_id = extracted_el.get("id") if extracted_el is not None else None
    extracted_index = -1
    for index, child in enumerate(children):
        if child is extracted_el or (extracted_id and child.get("id") == extracted_id):
            extracted_index = index
            break
    if extracted_index <= 0:
        return {}
    for sibling in reversed(children[:extracted_index]):
        sibling_text = " ".join(_text_content(sibling).split())
        match = re.search(r"\bin\s+entry\s+(?P<label>[0-9A-Z]+)\b", sibling_text, flags=re.I)
        if match is None:
            continue
        entry_label = _clean_num(match.group("label"))
        if not entry_label:
            continue
        return {
            "entry_label": entry_label,
            "source_context_rule_id": rule_id,
            "source_context": "previous_source_sibling_entry_label",
            "source_sibling_label": _clean_num(_direct_structural_num(sibling)),
            "source_sibling_id": str(sibling.get("id") or sibling.get("Id") or ""),
        }
    return {}


def _source_previous_table_entry_relating_context(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    rule_id: str,
) -> dict[str, Any]:
    """Return a source-owned table-entry relation from a previous sibling row."""
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return {}
    parent = ancestors[0]
    children = list(parent)
    extracted_id = extracted_el.get("id") if extracted_el is not None else None
    extracted_index = -1
    for index, child in enumerate(children):
        if child is extracted_el or (extracted_id and child.get("id") == extracted_id):
            extracted_index = index
            break
    if extracted_index <= 0:
        return {}
    for sibling in reversed(children[:extracted_index]):
        sibling_text = " ".join(_text_content(sibling).split())
        match = re.search(
            r"\bin\s+the\s+entry\s+(?:relating\s+to|for)\s+(?:the\s+)?(?P<relating>.*?)(?:,\s+(?:for|after|omit|insert|substitute)\b|$)",
            sibling_text,
            flags=re.I,
        )
        if match is None:
            continue
        relating_text = " ".join(match.group("relating").split()).strip(" ,;.")
        if not relating_text:
            continue
        row_anchor_texts: list[str] = []
        for fragment in parse_fragment_substitution(sibling_text):
            original = " ".join(str(fragment.get("original") or "").split()).strip(" ,;.")
            replacement = " ".join(str(fragment.get("replacement") or "").split()).strip(" ,;.")
            for candidate in (original, replacement):
                if candidate and candidate not in row_anchor_texts:
                    row_anchor_texts.append(candidate)
        return {
            "relating_text": relating_text,
            "row_anchor_texts": tuple(row_anchor_texts),
            "source_context_rule_id": rule_id,
            "source_context": "previous_source_sibling_entry_relating_text",
            "source_sibling_label": _clean_num(_direct_structural_num(sibling)),
            "source_sibling_id": str(sibling.get("id") or sibling.get("Id") or ""),
        }
    return {}


def _source_parent_range_label(label: str | None) -> str:
    raw = str(label or "").strip().strip("()").lower()
    if re.fullmatch(r"[a-z]", raw):
        return raw
    return _clean_num(raw)


def _source_preview(text: str, *, limit: int = 160) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _build_affecting_source_context(
    *,
    xml_bytes: Optional[bytes],
    locator: str,
    authority_layer: str,
    provision_extractor: Callable[..., Optional[ET._Element]] = extract_provision_element_from_bytes,
) -> UKAffectingSourceContextBuild:
    source_status, source_size = uk_source_state_wire_tuple(xml_bytes)
    root = None
    parent_map = None
    exact_id_map: dict[str, ET._Element] = {}
    sequence_map: dict[tuple[str, ...], ET._Element] = {}
    parse_error = None
    if xml_bytes and source_status == "available":
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            parse_error = exc
        else:
            parent_map, exact_id_map, sequence_map = _build_extraction_context(root)
    return UKAffectingSourceContextBuild(
        source_context=UKAffectingSourceContext(
            xml_bytes=xml_bytes,
            root=root,
            parent_map=parent_map,
            exact_id_map=exact_id_map,
            sequence_map=sequence_map,
            source_status=source_status,
            source_size=source_size,
            locator=locator,
            authority_layer=authority_layer,
            provision_extractor=provision_extractor,
        ),
        parse_error=parse_error,
    )


def _append_affecting_source_context_diagnostic(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    source_context: UKAffectingSourceContext,
    parse_error: Optional[Exception],
) -> None:
    if diagnostics_out is None or not effect.affecting_act_id:
        return
    if source_context.source_status == "absent":
        if str(effect.affecting_class or "") and not effect.affecting_class_is_recognized:
            diagnostics_out.append(
                uk_affecting_act_class_unmapped_rejection(
                    effect_id=str(effect.effect_id or ""),
                    affecting_act_id=str(effect.affecting_act_id or ""),
                    locator=source_context.locator,
                    affecting_class=str(effect.affecting_class or ""),
                )
            )
        else:
            diagnostics_out.append(
                uk_affecting_act_xml_missing_rejection(
                    effect_id=str(effect.effect_id or ""),
                    affecting_act_id=str(effect.affecting_act_id or ""),
                    locator=source_context.locator,
                )
            )
    elif source_context.source_status == "too_small":
        diagnostics_out.append(
            uk_affecting_act_xml_too_small_rejection(
                effect_id=str(effect.effect_id or ""),
                affecting_act_id=str(effect.affecting_act_id or ""),
                locator=source_context.locator,
                source_size=source_context.source_size,
            )
        )
    elif parse_error is not None:
        diagnostics_out.append(
            uk_affecting_act_xml_parse_rejection(
                effect_id=str(effect.effect_id or ""),
                affecting_act_id=str(effect.affecting_act_id or ""),
                locator=source_context.locator,
                exc=parse_error,
            )
        )


def _extract_from_affecting_source_context(
    context: UKAffectingSourceContext,
    provision_ref: str,
) -> Optional[ET._Element]:
    if context.xml_bytes is None or context.root is None:
        return None
    cached = context.provision_element_cache.get(provision_ref)
    if cached is not None or provision_ref in context.provision_element_cache:
        return cached
    extracted = context.provision_extractor(
        context.xml_bytes,
        provision_ref,
        root=context.root,
        parent_map=context.parent_map,
        exact_id_map=context.exact_id_map,
        sequence_map=context.sequence_map,
    )
    context.provision_element_cache[provision_ref] = extracted
    return extracted


def _compound_reference_parts(provision_ref: str) -> tuple[str, str] | None:
    matches = list(_COMPOUND_REFERENCE_KEYWORD_RE.finditer(provision_ref))
    if not matches:
        return None
    leading_prefix = provision_ref[: matches[0].start()]
    if _COMPOUND_REFERENCE_LEADING_BODY_RE.search(leading_prefix):
        idx = matches[0].start()
    elif len(matches) >= 2:
        idx = matches[1].start()
    else:
        return None
    first_part = provision_ref[:idx].strip()
    second_part = provision_ref[idx:].strip()
    if not first_part or not second_part:
        return None
    return first_part, second_part


def _schedule_part_standalone_split_rejection(
    *,
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    first_part: str,
    second_part: str,
) -> dict[str, Any] | None:
    if not re.fullmatch(r"Sch(?:edule)?\.?\s+\S+", first_part.strip(), flags=re.I):
        return None
    if not re.fullmatch(r"(?:Part|Pt)\.?\s+[0-9A-Za-zIVXLCivxlc]+", second_part.strip(), flags=re.I):
        return None
    schedule_component = _extract_from_affecting_source_context(context, first_part)
    standalone_part_candidate = _extract_from_affecting_source_context(context, second_part)
    return uk_affecting_act_schedule_part_standalone_split_rejection(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        split_first_part=first_part,
        split_second_part=second_part,
        schedule_component_tag=_tag(schedule_component) if schedule_component is not None else "",
        schedule_component_id=str(
            schedule_component.get("id") or schedule_component.get("Id") or ""
        )
        if schedule_component is not None
        else "",
        schedule_component_label=_clean_num(_direct_structural_num(schedule_component))
        if schedule_component is not None
        else "",
        standalone_part_candidate_tag=_tag(standalone_part_candidate)
        if standalone_part_candidate is not None
        else "",
        standalone_part_candidate_id=str(
            standalone_part_candidate.get("id") or standalone_part_candidate.get("Id") or ""
        )
        if standalone_part_candidate is not None
        else "",
        standalone_part_candidate_label=_clean_num(_direct_structural_num(standalone_part_candidate))
        if standalone_part_candidate is not None
        else "",
    )


def _compound_reference_split_observation(
    *,
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    first_part: str,
    second_part: str,
    selected_part: str,
    split_el: ET._Element,
) -> dict[str, Any]:
    return uk_affecting_act_compound_reference_split_fallback(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        split_first_part=first_part,
        split_second_part=second_part,
        split_selected_part=selected_part,
        extracted_element_id=str(split_el.get("id") or split_el.get("Id") or ""),
    )


def _source_child_has_parent_table_column_omission(
    context: UKAffectingSourceContext,
    el: ET._Element,
) -> bool:
    for ancestor in _source_ancestor_chain(context.root, el)[:3]:
        text = " ".join(_text_content(ancestor).split())
        if re.search(
            r"\bomit\s+from\s+(?:the\s+)?"
            r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)"
            r"\s+column\s+of\s+(?:the\s+)?table\s+(?:the\s+)?entries\s+relating\s+to\b",
            text,
            flags=re.I,
        ):
            return True
    return False


def _source_is_broad_repeal_extent_part(el: ET._Element) -> bool:
    if _tag(el) not in {"Part", "Schedule"}:
        return False
    text = " ".join(_text_content(el).split()).lower()
    return "extent of repeal" in text[:500] or "repeals and revocations" in text[:500]


def _compound_first_instruction_overrides_broad_repeal_part(
    context: UKAffectingSourceContext,
    *,
    first_el: Optional[ET._Element],
    second_el: Optional[ET._Element],
) -> bool:
    if first_el is None or second_el is None:
        return False
    return (
        _source_child_has_parent_table_column_omission(context, first_el)
        and _source_is_broad_repeal_extent_part(second_el)
    )


def _extract_compound_reference_component(
    context: UKAffectingSourceContext,
    component_ref: str,
) -> Optional[ET._Element]:
    component_el = _extract_from_affecting_source_context(context, component_ref)
    if component_el is not None:
        return _source_range_child_with_context(context, component_el)
    normalized = _schedule_part_context_normalized_ref(component_ref)
    if normalized is None:
        return None
    requested_part_label, normalized_ref = normalized
    normalized_el = _extract_from_affecting_source_context(context, normalized_ref)
    if normalized_el is None or not _has_matching_part_ancestor(
        context,
        normalized_el,
        requested_part_label,
    ):
        return None
    return _source_range_child_with_context(context, normalized_el)


def _extract_source_ref_with_schedule_part_context(
    context: UKAffectingSourceContext,
    provision_ref: str,
) -> Optional[ET._Element]:
    el = _extract_from_affecting_source_context(context, provision_ref)
    if el is not None:
        return el
    normalized = _schedule_part_context_normalized_ref(provision_ref)
    if normalized is None:
        return None
    requested_part_label, normalized_ref = normalized
    normalized_el = _extract_from_affecting_source_context(context, normalized_ref)
    if normalized_el is None or not _has_matching_part_ancestor(
        context,
        normalized_el,
        requested_part_label,
    ):
        return None
    return normalized_el


def _source_range_child_with_context(
    context: UKAffectingSourceContext,
    el: ET._Element,
) -> ET._Element:
    if _tag(el) not in {"BlockAmendment", "InlineAmendment"} or context.parent_map is None:
        return el
    parent = context.parent_map.get(el)
    while parent is not None:
        if _tag(parent) in {"P1", "P2", "P3", "P4", "P5", "P6", "Section", "Subsection", "Paragraph"}:
            return parent
        parent = context.parent_map.get(parent)
    return el


def _compound_payload_only_amendment_container(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    el: ET._Element,
) -> UKSourceExtractionResult:
    """Select a nested amendment payload when the source row is only a carrier label."""
    if _tag(el) not in {"P1", "P2", "P3", "P4", "P5", "P6", "Paragraph"}:
        return UKSourceExtractionResult(el, ())
    amendment_containers: list[ET._Element] = []
    outside_text: list[str] = []

    def _walk(node: ET._Element, *, inside_amendment: bool) -> None:
        tag = _tag(node)
        next_inside_amendment = inside_amendment or tag in {"BlockAmendment", "InlineAmendment"}
        if tag in {"BlockAmendment", "InlineAmendment"}:
            amendment_containers.append(node)
        if not next_inside_amendment and tag not in {"Pnumber", "Number"} and node.text:
            outside_text.append(node.text)
        for child in node:
            _walk(child, inside_amendment=next_inside_amendment)
            if not next_inside_amendment and child.tail:
                outside_text.append(child.tail)

    _walk(el, inside_amendment=False)
    if len(amendment_containers) != 1:
        return UKSourceExtractionResult(el, ())
    if re.search(r"[0-9A-Za-z]", " ".join(outside_text)):
        return UKSourceExtractionResult(el, ())
    payload = amendment_containers[0]
    observation = uk_affecting_act_compound_payload_only_block_amendment_selected(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        source_row_tag=_tag(el),
        source_row_id=str(el.get("id") or el.get("Id") or ""),
        source_row_label=_clean_num(_direct_structural_num(el)),
        payload_container_tag=_tag(payload),
        payload_text_preview=_source_preview(_text_content(payload)),
    )
    return UKSourceExtractionResult(payload, (observation,))


def _has_amendment_payload_descendant(el: ET._Element) -> bool:
    return any(
        child is not el and _tag(child) in {"BlockAmendment", "InlineAmendment"}
        for child in el.iter()
    )


def _direct_structural_children(el: ET._Element) -> tuple[ET._Element, ...]:
    child_tags = {"P1", "P2", "P3", "P4", "P5", "P6", "Paragraph"}
    return tuple(
        descendant
        for descendant in el
        if _tag(descendant) in child_tags
    ) + tuple(
        grandchild
        for child in el
        for grandchild in child
        if _tag(grandchild) in child_tags
    )


def _single_amendment_child_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    el: Optional[ET._Element],
) -> UKSourceExtractionResult:
    if el is None or _tag(el) not in {"P1", "P2", "P3", "P4", "P5", "P6", "Paragraph"}:
        return UKSourceExtractionResult(el, ())
    candidates = tuple(
        child
        for child in _direct_structural_children(el)
        if _has_amendment_payload_descendant(child)
    )
    if len(candidates) != 1:
        return UKSourceExtractionResult(el, ())
    selected = candidates[0]
    observation = uk_affecting_act_single_amendment_child_source_selected(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        source_container_id=str(el.get("id") or el.get("Id") or ""),
        selected_child_id=str(selected.get("id") or selected.get("Id") or ""),
        selected_child_label=_clean_num(_direct_structural_num(selected)),
        selected_child_text_preview=_source_preview(_text_content(selected)),
    )
    return UKSourceExtractionResult(selected, (observation,))


def _payload_source_instruction_ancestor(
    context: UKAffectingSourceContext,
    el: ET._Element,
) -> Optional[ET._Element]:
    if context.parent_map is None:
        return None
    passed_amendment_payload = False
    parent = context.parent_map.get(el)
    source_instruction_tags = {
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "Paragraph",
        "Section",
        "Subsection",
        "Article",
        "Rule",
        "Regulation",
        "Schedule",
        "Part",
    }
    while parent is not None:
        parent_tag = _tag(parent)
        if parent_tag in {"BlockAmendment", "InlineAmendment"}:
            passed_amendment_payload = True
        elif passed_amendment_payload and parent_tag in source_instruction_tags:
            return parent
        parent = context.parent_map.get(parent)
    return None


def _block_amendment_payload_descendant_source_rejection(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    el: Optional[ET._Element],
) -> dict[str, Any] | None:
    if el is None or context.parent_map is None:
        return None
    if _tag(el) in {"BlockAmendment", "InlineAmendment"}:
        return None
    if el.get("id") or el.get("Id"):
        return None

    amendment_container_tag = ""
    parent = context.parent_map.get(el)
    while parent is not None:
        parent_tag = _tag(parent)
        if parent_tag in {"BlockAmendment", "InlineAmendment"}:
            amendment_container_tag = parent_tag
            break
        parent = context.parent_map.get(parent)
    if not amendment_container_tag:
        return None

    source_instruction_ancestor = _payload_source_instruction_ancestor(context, el)
    return uk_affecting_act_block_amendment_payload_descendant_ref_rejection(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        extracted_tag=_tag(el),
        extracted_label=_clean_num(_direct_structural_num(el)),
        extracted_text_preview=_source_preview(_text_content(el)),
        amendment_container_tag=amendment_container_tag,
        source_instruction_ancestor_tag=_tag(source_instruction_ancestor)
        if source_instruction_ancestor is not None
        else "",
        source_instruction_ancestor_id=str(
            source_instruction_ancestor.get("id") or source_instruction_ancestor.get("Id") or ""
        )
        if source_instruction_ancestor is not None
        else "",
        source_instruction_ancestor_label=_clean_num(_direct_structural_num(source_instruction_ancestor))
        if source_instruction_ancestor is not None
        else "",
        source_instruction_ancestor_text_preview=_source_preview(_text_content(source_instruction_ancestor))
        if source_instruction_ancestor is not None
        else "",
    )


def _schedule_part_context_normalized_ref(provision_ref: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(Sch(?:edule)?\.?\s+\S+)\s+Pt\.?\s+([0-9A-Za-zIVXLCivxlc]+)\s+(.+)",
        provision_ref,
        flags=re.I,
    )
    if match is None:
        return None
    suffix = match.group(3).strip()
    if not re.search(r"\bpara(?:graph)?\.?\b", suffix, flags=re.I):
        return None
    return match.group(2), f"{match.group(1)} {suffix}"


def _implicit_first_subparagraph_context_normalized_ref(provision_ref: str) -> str | None:
    normalized = " ".join((provision_ref or "").split()).strip()
    if not re.search(r"\bSch(?:edule)?\.?\b", normalized, flags=re.I):
        return None
    match = re.match(
        r"^(?P<prefix>.+?\bpara(?:graph)?\.?\s+[0-9A-Za-z]+)\s*"
        r"\(\s*1\s*\)\s*\(\s*(?P<label>[A-Za-z])\s*\)$",
        normalized,
        flags=re.I,
    )
    if match is None:
        return None
    return f"{match.group('prefix').strip()}({match.group('label').strip()})"


def _single_unnumbered_schedule_context_normalized_ref(
    provision_ref: str,
) -> tuple[str, str] | None:
    normalized = " ".join((provision_ref or "").split()).strip()
    match = _SINGLE_UNNUMBERED_SCHEDULE_REF_RE.match(normalized)
    if match is None:
        return None
    requested_schedule_label = _clean_num(match.group("schedule"))
    if requested_schedule_label != "1":
        return None
    return requested_schedule_label, f"Sch. {match.group('suffix').strip()}"


def _unique_unnumbered_root_schedule(context: UKAffectingSourceContext) -> Optional[ET._Element]:
    if context.root is None:
        return None
    unnumbered_schedules: list[ET._Element] = []
    for schedule in context.root.iter():
        if _tag(schedule) != "Schedule":
            continue
        schedule_id = str(schedule.get("id") or schedule.get("Id") or "")
        direct_label = _clean_num(_direct_structural_num(schedule))
        if _get_id_sequence(schedule_id) in {(), ("schedule",)} and direct_label in {
            "",
            "schedule",
        }:
            unnumbered_schedules.append(schedule)
    if len(unnumbered_schedules) != 1:
        return None
    return unnumbered_schedules[0]


def _source_instruction_id_for_extracted(
    context: UKAffectingSourceContext,
    el: ET._Element,
) -> str:
    if context.parent_map is None:
        return ""
    source_instruction_tags = {"P1", "P2", "P3", "P4", "P5", "P6", "Paragraph"}
    parent = el
    while parent is not None:
        if _tag(parent) in source_instruction_tags:
            return str(parent.get("id") or parent.get("Id") or "")
        parent = context.parent_map.get(parent)
    return ""


def _extract_single_unnumbered_schedule_context_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> UKSourceExtractionResult:
    normalized = _single_unnumbered_schedule_context_normalized_ref(
        str(effect.affecting_provisions or "")
    )
    if normalized is None:
        return _NO_SOURCE_EXTRACTION
    requested_schedule_label, normalized_ref = normalized
    schedule_el = _unique_unnumbered_root_schedule(context)
    if schedule_el is None:
        return _NO_SOURCE_EXTRACTION
    normalized_el = _extract_from_affecting_source_context(context, normalized_ref)
    if normalized_el is None:
        return _NO_SOURCE_EXTRACTION
    observation = uk_affecting_act_single_unnumbered_schedule_context_ignored(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        requested_schedule_label=requested_schedule_label,
        normalized_affecting_provisions=normalized_ref,
        schedule_element_id=str(schedule_el.get("id") or schedule_el.get("Id") or ""),
        source_instruction_id=_source_instruction_id_for_extracted(context, normalized_el),
        extracted_element_id=str(normalized_el.get("id") or normalized_el.get("Id") or ""),
    )
    return UKSourceExtractionResult(normalized_el, (observation,))


def _article_schedule_payload_ref(provision_ref: str) -> str | None:
    normalized = " ".join((provision_ref or "").split()).strip()
    match = re.fullmatch(
        r"(?P<kind>art(?:icle)?|reg(?:ulation)?|rule)\.?\s+"
        r"(?P<label>[0-9A-Za-z]+)\s+Sch(?:edule)?\.?",
        normalized,
        flags=re.I,
    )
    if match is None:
        return None
    kind = match.group("kind").lower()
    label = match.group("label")
    if kind.startswith("art"):
        return f"art. {label}"
    if kind.startswith("reg"):
        return f"reg. {label}"
    return f"rule {label}"


def _unique_root_schedule_payload(context: UKAffectingSourceContext) -> Optional[ET._Element]:
    if context.root is None:
        return None
    schedules = [el for el in context.root.iter() if _tag(el) == "Schedule"]
    if len(schedules) != 1:
        return None
    return schedules[0]


def _extract_article_schedule_payload_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> UKSourceExtractionResult:
    article_ref = _article_schedule_payload_ref(str(effect.affecting_provisions or ""))
    if article_ref is None:
        return _NO_SOURCE_EXTRACTION

    article_el = _extract_from_affecting_source_context(context, article_ref)
    if article_el is None:
        return _NO_SOURCE_EXTRACTION

    article_text = _text_content(article_el)
    affecting_prov = str(effect.affecting_provisions or "")
    has_explicit_sch = re.search(r"\bSch(?:edule)?\b", affecting_prov, flags=re.I) is not None
    if not has_explicit_sch and not re.search(r"\bset\s+out\s+in\s+the\s+Schedule\b", article_text, flags=re.I):
        return _NO_SOURCE_EXTRACTION

    schedule_el = _unique_root_schedule_payload(context)
    if schedule_el is None:
        return _NO_SOURCE_EXTRACTION

    observation = uk_affecting_act_article_schedule_payload_source_extracted(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        article_ref=article_ref,
        article_element_id=str(article_el.get("id") or article_el.get("Id") or ""),
        schedule_element_id=str(schedule_el.get("id") or schedule_el.get("Id") or ""),
        article_text_preview=_source_preview(article_text),
    )
    return UKSourceExtractionResult(schedule_el, (observation,))


def _has_matching_part_ancestor(
    context: UKAffectingSourceContext,
    el: ET._Element,
    requested_part_label: str,
) -> bool:
    if context.parent_map is None:
        return False
    parent = context.parent_map.get(el)
    while parent is not None:
        tag = _tag(parent).lower()
        if tag == "schedule":
            return False
        if tag == "part":
            part_id = str(parent.get("id") or parent.get("Id") or "")
            if part_id:
                part_tokens = _get_id_sequence(part_id)
                requested_tokens = _sequence_tokens((requested_part_label,))
                for idx, token in enumerate(part_tokens[:-1]):
                    if token == "part" and part_tokens[idx + 1 : idx + 2] == requested_tokens:
                        return True
            return _match_node(parent, "part", requested_part_label)
        parent = context.parent_map.get(parent)
    return False


def _parenthesized_range_source_ref(provision_ref: str) -> tuple[str, str, str] | None:
    match = re.match(
        r"^(?P<parent>.+?)\((?P<start>[0-9A-Za-zivxlcdm]+)\)\s*-\s*\((?P<end>[0-9A-Za-zivxlcdm]+)\)$",
        " ".join((provision_ref or "").split()).strip(),
        flags=re.I,
    )
    if match is None:
        return None
    parent_ref = match.group("parent").strip()
    start = match.group("start").strip()
    end = match.group("end").strip()
    if not parent_ref or not start or not end:
        return None
    return parent_ref, start, end


def _expand_source_child_label_range(start: str, end: str) -> tuple[str, ...]:
    start_clean = _source_parent_range_label(start)
    end_clean = _source_parent_range_label(end)
    if not start_clean or not end_clean:
        return ()
    if len(start_clean) == 1 and len(end_clean) == 1 and start_clean.isalpha() and end_clean.isalpha():
        if ord(end_clean) < ord(start_clean) or ord(end_clean) - ord(start_clean) > 100:
            return ()
        return tuple(chr(code) for code in range(ord(start_clean), ord(end_clean) + 1))
    if start_clean.isdigit() and end_clean.isdigit():
        start_int = int(start_clean)
        end_int = int(end_clean)
        if end_int < start_int or end_int - start_int > 100:
            return ()
        return tuple(str(value) for value in range(start_int, end_int + 1))
    start_roman = _shared_roman_to_arabic(start_clean)
    end_roman = _shared_roman_to_arabic(end_clean)
    if start_roman is not None and end_roman is not None and end_roman >= start_roman and end_roman - start_roman <= 100:
        return tuple(_shared_arabic_to_roman(value).lower() for value in range(start_roman, end_roman + 1))
    return ()


def _extract_parenthesized_range_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> UKSourceExtractionResult:
    return _extract_parenthesized_range_source_ref(
        context,
        effect,
        str(effect.affecting_provisions or ""),
    )


def _outdented_child_source_ref(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    provision_ref: str,
) -> UKSourceExtractionResult:
    if context.parent_map is None:
        return _NO_SOURCE_EXTRACTION
    match = re.fullmatch(
        r"\s*s(?:ection)?\.?\s*(?P<section>[0-9]+[A-Za-z]?)"
        r"\s*\(\s*(?P<parent>[0-9]+[A-Za-z]?)\s*\)"
        r"\s*\(\s*(?P<child>[A-Za-z]+)\s*\)\s*",
        provision_ref,
        flags=re.I,
    )
    if match is None:
        return _NO_SOURCE_EXTRACTION
    section_label = match.group("section")
    parent_label = match.group("parent")
    child_label = match.group("child")
    requested_parent_id = f"section-{section_label}-{parent_label}"
    selected_child_id = f"section-{section_label}-{child_label.lower()}"
    requested_parent = context.sequence_map.get(_get_id_sequence(requested_parent_id))
    selected_child = context.sequence_map.get(_get_id_sequence(selected_child_id))
    if requested_parent is None or selected_child is None:
        return _NO_SOURCE_EXTRACTION
    if context.parent_map.get(requested_parent) is not context.parent_map.get(selected_child):
        return _NO_SOURCE_EXTRACTION
    if _clean_num(_direct_structural_num(selected_child)).lower() != child_label.lower():
        return _NO_SOURCE_EXTRACTION
    selected_text = _text_content(selected_child)
    parent_pattern = re.compile(
        rf"\bsubsection\s*\(?\s*{re.escape(parent_label)}\s*\)?",
        flags=re.I,
    )
    if parent_pattern.search(selected_text) is None:
        return _NO_SOURCE_EXTRACTION
    observation = uk_affecting_act_outdented_child_source_selected(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        requested_parent_id=requested_parent_id,
        selected_child_id=selected_child_id,
        selected_child_label=_clean_num(_direct_structural_num(selected_child)),
        selected_child_text_preview=_source_preview(selected_text),
        carried_parent_label=parent_label,
    )
    return UKSourceExtractionResult(selected_child, (observation,))


def _extract_parenthesized_range_source_ref(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    provision_ref: str,
) -> UKSourceExtractionResult:
    parsed = _parenthesized_range_source_ref(provision_ref)
    if parsed is None:
        return _NO_SOURCE_EXTRACTION
    parent_ref, start_label, end_label = parsed
    wanted_labels = _expand_source_child_label_range(start_label, end_label)
    if not wanted_labels:
        return _NO_SOURCE_EXTRACTION
    selected: list[ET._Element] = []
    for label in wanted_labels:
        child = _extract_source_ref_with_schedule_part_context(
            context,
            f"{parent_ref}({label})",
        )
        if child is None:
            break
        selected.append(_source_range_child_with_context(context, child))
    if len(selected) != len(wanted_labels):
        parent_el = _extract_source_ref_with_schedule_part_context(context, parent_ref)
        if parent_el is None:
            return _NO_SOURCE_EXTRACTION
        by_label: dict[str, ET._Element] = {}
        for child in parent_el.iter():
            if child is parent_el:
                continue
            if _tag(child) not in {"P1", "P2", "P3", "P4", "P5", "P6", "Section", "Subsection", "Paragraph"}:
                continue
            label = _source_parent_range_label(_direct_structural_num(child))
            if label and label not in by_label:
                by_label[label] = child
        selected = []
        for label in wanted_labels:
            child = by_label.get(label)
            if child is None:
                return _NO_SOURCE_EXTRACTION
            selected.append(child)
    wrapper = ET.Element("SourceRange")
    wrapper.set("rule_id", "uk_affecting_act_parenthesized_range_source_extracted")
    wrapper.set("source_ref", str(effect.affecting_provisions or ""))
    wrapper.set("parent_ref", parent_ref)
    wrapper.set("start_label", wanted_labels[0])
    wrapper.set("end_label", wanted_labels[-1])
    for child in selected:
        wrapper.append(copy.deepcopy(child))
    observation = uk_affecting_act_parenthesized_range_source_extracted(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        normalized_parent_ref=parent_ref,
        requested_start_label=wanted_labels[0],
        requested_end_label=wanted_labels[-1],
        extracted_element_ids=[str(child.get("id") or child.get("Id") or "") for child in selected],
    )
    return UKSourceExtractionResult(wrapper, (observation,))


def _schedule_paragraph_ref_parts(ref: str) -> tuple[str, str] | None:
    match = re.match(
        r"^\s*sch(?:edule)?\.?\s+(?P<schedule>[0-9A-Za-z]+)\s+"
        r"para(?:graph)?\.?\s+(?P<paragraph>[0-9A-Za-z]+)\s*$",
        " ".join((ref or "").split()),
        flags=re.I,
    )
    if match is None:
        return None
    schedule = _clean_num(match.group("schedule"))
    paragraph = _clean_num(match.group("paragraph"))
    if not schedule or not paragraph:
        return None
    return schedule, paragraph


def _schedule_ref_label(ref: str) -> str:
    match = re.match(
        r"^\s*sch(?:edule)?\.?\s+(?P<schedule>[0-9A-Za-z]+)\s*$",
        " ".join((ref or "").split()),
        flags=re.I,
    )
    if match is None:
        return ""
    return _clean_num(match.group("schedule"))


def _schedule_table_row_label_key(label: str) -> str:
    return re.sub(r"\s+", "", _clean_num(label))


def _source_part_label_from_element(part_el: ET._Element) -> str:
    number_el = part_el.find(f"./{{{_LEG_NS}}}Number")
    number_text = _text_content(number_el) if number_el is not None else ""
    match = re.search(r"\bpart\s+(?P<label>[0-9A-Za-zIVXLC]+)\b", number_text, flags=re.I)
    if match is not None:
        return _clean_num(match.group("label"))
    part_id = str(part_el.get("id") or part_el.get("Id") or "")
    tokens = _get_id_sequence(part_id)
    for index, token in enumerate(tokens[:-1]):
        if token == "part":
            return _clean_num(tokens[index + 1])
    return ""


def _direct_table_row_cells(row: ET._Element) -> tuple[ET._Element, ...]:
    return tuple(
        child
        for child in list(row)
        if _tag(child).lower() in {"td", "entry", "cell"}
    )


def _synthetic_schedule_table_row_paragraph_source(
    *,
    schedule_label: str,
    part_label: str,
    target_label: str,
    row: ET._Element,
    cells: Sequence[ET._Element],
) -> ET._Element | None:
    if len(cells) < 2:
        return None
    payload_text = " ".join(_text_content(cell) for cell in cells[1:]).strip()
    if not payload_text:
        return None
    p1 = ET.Element(f"{{{_LEG_NS}}}P1")
    p1.set("id", f"schedule-{schedule_label}-paragraph-{target_label}")
    p1.set("source_rule_id", "uk_affecting_act_enacted_schedule_table_row_source_extracted")
    p1.set("source_part_label", part_label)
    p1.set("source_row_text", _text_content(row))
    pnumber = ET.SubElement(p1, f"{{{_LEG_NS}}}Pnumber")
    pnumber.text = target_label
    text = ET.SubElement(p1, f"{{{_LEG_NS}}}Text")
    text.text = payload_text
    return p1


def _extract_enacted_schedule_table_row_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> UKSourceExtractionResult:
    effect_type = (effect.effect_type or "").strip().lower()
    if effect_type not in {"added", "inserted"}:
        return _NO_SOURCE_EXTRACTION
    target_parts = _schedule_paragraph_ref_parts(str(effect.affected_provisions or ""))
    if target_parts is None:
        return _NO_SOURCE_EXTRACTION
    target_schedule_label, target_paragraph_label = target_parts
    source_schedule_label = _schedule_ref_label(str(effect.affecting_provisions or ""))
    if source_schedule_label != target_schedule_label:
        return _NO_SOURCE_EXTRACTION
    schedule_el = _extract_from_affecting_source_context(context, effect.affecting_provisions)
    if schedule_el is None or _tag(schedule_el) != "Schedule":
        return _NO_SOURCE_EXTRACTION

    matches: list[UKEnactedScheduleTableRowMatch] = []
    for part in schedule_el.iter():
        if _tag(part) != "Part":
            continue
        part_label = _source_part_label_from_element(part)
        if not part_label:
            continue
        for row in part.iter():
            if _tag(row).lower() != "tr":
                continue
            cells = _direct_table_row_cells(row)
            if len(cells) < 2:
                continue
            row_label_text = _text_content(cells[0]).strip().rstrip(".")
            row_label = _schedule_table_row_label_key(row_label_text)
            if row_label == target_paragraph_label:
                matches.append(
                    UKEnactedScheduleTableRowMatch(
                        row=row,
                        cells=cells,
                        part_label=part_label,
                        source_label_text=target_paragraph_label.upper(),
                    )
                )
    if len(matches) != 1:
        return _NO_SOURCE_EXTRACTION

    match = matches[0]
    synthetic = _synthetic_schedule_table_row_paragraph_source(
        schedule_label=target_schedule_label,
        part_label=match.part_label,
        target_label=match.source_label_text,
        row=match.row,
        cells=match.cells,
    )
    if synthetic is None:
        return _NO_SOURCE_EXTRACTION
    observation = uk_affecting_act_enacted_schedule_table_row_source_extracted(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affected_provisions=str(effect.affected_provisions or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        schedule_label=target_schedule_label,
        part_label=match.part_label,
        target_label=target_paragraph_label,
        source_row_text=_text_content(match.row),
    )
    return UKSourceExtractionResult(synthetic, (observation,))


def _extract_from_affecting_source_context_with_observations(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> UKSourceExtractionResult:
    provision_ref = str(effect.affecting_provisions or "")
    el = _extract_from_affecting_source_context(context, provision_ref)
    if el is not None:
        compound_parts = _compound_reference_parts(provision_ref)
        if compound_parts is not None:
            first_part, second_part = compound_parts
            unsafe_schedule_part_split = _schedule_part_standalone_split_rejection(
                context=context,
                effect=effect,
                first_part=first_part,
                second_part=second_part,
            )
            if unsafe_schedule_part_split is None:
                first_el = _extract_compound_reference_component(context, first_part)
                second_el = _extract_compound_reference_component(context, second_part)
                second_observations: tuple[dict[str, Any], ...] = ()
                if second_el is None:
                    second_el, second_observations = _extract_parenthesized_range_source_ref(
                        context,
                        effect,
                        second_part,
                    )
                if first_el is not None and _compound_first_instruction_overrides_broad_repeal_part(
                    context,
                    first_el=first_el,
                    second_el=second_el,
                ):
                    return UKSourceExtractionResult(
                        first_el,
                        (
                            _compound_reference_split_observation(
                                context=context,
                                effect=effect,
                                first_part=first_part,
                                second_part=second_part,
                                selected_part="first",
                                split_el=first_el,
                            ),
                        ),
                    )
                if second_el is not None and second_el is not el:
                    second_el, payload_only_observations = _compound_payload_only_amendment_container(
                        context,
                        effect,
                        second_el,
                    )
                    if second_el is None:
                        return _NO_SOURCE_EXTRACTION
                    payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
                        context,
                        effect,
                        second_el,
                    )
                    if payload_descendant_rejection is not None:
                        return UKSourceExtractionResult(None, (payload_descendant_rejection,))
                    return UKSourceExtractionResult(
                        second_el,
                        (
                            _compound_reference_split_observation(
                                context=context,
                                effect=effect,
                                first_part=first_part,
                                second_part=second_part,
                                selected_part="second",
                                split_el=second_el,
                            ),
                            *second_observations,
                            *payload_only_observations,
                        ),
                    )
        payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
            context,
            effect,
            el,
        )
        if payload_descendant_rejection is not None:
            outdented_el, outdented_observations = _outdented_child_source_ref(
                context,
                effect,
                provision_ref,
            )
            if outdented_el is not None:
                return UKSourceExtractionResult(outdented_el, outdented_observations)
            return UKSourceExtractionResult(None, (payload_descendant_rejection,))
        return UKSourceExtractionResult(el, ())

    range_el, range_observations = _extract_parenthesized_range_source(context, effect)
    if range_el is not None:
        return UKSourceExtractionResult(range_el, range_observations)

    single_schedule_el, single_schedule_observations = (
        _extract_single_unnumbered_schedule_context_source(context, effect)
    )
    if single_schedule_el is not None:
        return UKSourceExtractionResult(single_schedule_el, single_schedule_observations)

    article_schedule_el, article_schedule_observations = _extract_article_schedule_payload_source(context, effect)
    if article_schedule_el is not None:
        return UKSourceExtractionResult(article_schedule_el, article_schedule_observations)

    normalized = _schedule_part_context_normalized_ref(provision_ref)
    if normalized is not None:
        requested_part_label, normalized_ref = normalized
        normalized_el = _extract_from_affecting_source_context(context, normalized_ref)
        if normalized_el is None or not _has_matching_part_ancestor(
            context,
            normalized_el,
            requested_part_label,
        ):
            return UKSourceExtractionResult(None, ())

        observation = uk_affecting_act_nonaddressable_schedule_part_context_ignored(
            effect_id=str(effect.effect_id or ""),
            affecting_act_id=str(effect.affecting_act_id or ""),
            affecting_provisions=provision_ref,
            locator=context.locator,
            authority_layer=context.authority_layer,
            requested_part_label=requested_part_label,
            normalized_affecting_provisions=normalized_ref,
            extracted_element_id=str(normalized_el.get("id") or normalized_el.get("Id") or ""),
        )
        return UKSourceExtractionResult(normalized_el, (observation,))

    compound_parts = _compound_reference_parts(provision_ref)
    if compound_parts is not None:
        first_part, second_part = compound_parts
        schedule_part_rejection = _schedule_part_standalone_split_rejection(
            context=context,
            effect=effect,
            first_part=first_part,
            second_part=second_part,
        )
        if schedule_part_rejection is not None:
            return UKSourceExtractionResult(None, (schedule_part_rejection,))
        split_el = None
        split_selected_part = ""
        split_observations: tuple[dict[str, Any], ...] = ()
        first_el = _extract_compound_reference_component(context, first_part) if first_part else None
        if second_part:
            split_el = _extract_compound_reference_component(context, second_part)
            if split_el is not None:
                split_selected_part = "second"
            else:
                split_el, split_observations = _extract_parenthesized_range_source_ref(
                    context,
                    effect,
                    second_part,
                )
                if split_el is not None:
                    split_selected_part = "second"

        if _compound_first_instruction_overrides_broad_repeal_part(
            context,
            first_el=first_el,
            second_el=split_el,
        ):
            split_el = first_el
            split_selected_part = "first"
            split_observations = ()

        if split_el is None and first_el is not None:
            split_el = first_el
            if split_el is not None:
                split_selected_part = "first"

        if split_el is not None:
            split_el, payload_only_observations = _compound_payload_only_amendment_container(
                context,
                effect,
                split_el,
            )
            if split_el is None:
                return _NO_SOURCE_EXTRACTION
            payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
                context,
                effect,
                split_el,
            )
            if payload_descendant_rejection is not None:
                return UKSourceExtractionResult(None, (payload_descendant_rejection,))
            observation = _compound_reference_split_observation(
                context=context,
                effect=effect,
                first_part=first_part,
                second_part=second_part,
                selected_part=split_selected_part,
                split_el=split_el,
            )
            return UKSourceExtractionResult(
                split_el,
                (observation, *split_observations, *payload_only_observations),
            )

    implicit_first_ref = _implicit_first_subparagraph_context_normalized_ref(provision_ref)
    if implicit_first_ref is not None:
        implicit_first_el = _extract_from_affecting_source_context(context, implicit_first_ref)
        if implicit_first_el is not None:
            observation = uk_affecting_act_implicit_first_subparagraph_context_ignored(
                effect_id=str(effect.effect_id or ""),
                affecting_act_id=str(effect.affecting_act_id or ""),
                affecting_provisions=provision_ref,
                locator=context.locator,
                authority_layer=context.authority_layer,
                normalized_affecting_provisions=implicit_first_ref,
                extracted_element_id=str(
                    implicit_first_el.get("id") or implicit_first_el.get("Id") or ""
                ),
            )
            return UKSourceExtractionResult(implicit_first_el, (observation,))

    return UKSourceExtractionResult(None, ())


def _extracted_element_text(el: Optional[ET._Element]) -> str:
    return _text_content(el) if el is not None else ""


def _preview_source_text(text: str, *, limit: int = 160) -> str:
    return _source_preview(text, limit=limit)


def _looks_like_non_substantive_shell_text(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False
    normalized = re.sub(r"^[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?\s+", "", normalized)
    if re.search(r"[A-Za-z]", normalized):
        return False
    return normalized.count(".") >= 4


def _looks_like_non_substantive_shell_element(el: Optional[ET._Element]) -> bool:
    if el is None:
        return False
    if _tag(el) == "SourceRange":
        children = list(el)
        return bool(children) and all(
            _looks_like_non_substantive_shell_text(_text_content(child))
            for child in children
        )
    return _looks_like_non_substantive_shell_text(_extracted_element_text(el))


def _select_enacted_source_for_current_shell(
    *,
    effect: UKEffectRecord,
    archive: Any,
    current_context: UKAffectingSourceContext,
    current_el: Optional[ET._Element],
    enacted_context_cache: dict[str, UKAffectingSourceContext],
    enacted_xml_loader: Callable[[str, Any], Optional[bytes]] = get_affecting_act_enacted_xml_from_archive,
) -> UKSelectedAffectingSource:
    current_missing = current_el is None
    current_shell = _looks_like_non_substantive_shell_element(current_el)
    if not current_missing and not current_shell:
        return UKSelectedAffectingSource(current_context, current_el, ())

    act_id = str(effect.affecting_act_id or "")
    if not act_id:
        return UKSelectedAffectingSource(current_context, current_el, ())
    if act_id in enacted_context_cache:
        enacted_context = enacted_context_cache[act_id]
    else:
        enacted_locator = f"{_LEG_BASE}/{act_id}/enacted/data.xml"
        enacted_context, _parse_error = _build_affecting_source_context(
            xml_bytes=enacted_xml_loader(act_id, archive),
            locator=enacted_locator,
            authority_layer="AFFECTING_ACT_ENACTED_TEXT",
            provision_extractor=current_context.provision_extractor,
        )
        enacted_context_cache[act_id] = enacted_context

    schedule_row_el, schedule_row_observations = _extract_enacted_schedule_table_row_source(
        enacted_context,
        effect,
    )
    if schedule_row_el is not None:
        return UKSelectedAffectingSource(
            enacted_context,
            schedule_row_el,
            schedule_row_observations,
        )
    if (
        current_missing
        and _schedule_paragraph_ref_parts(str(effect.affected_provisions or "")) is not None
        and _schedule_ref_label(str(effect.affecting_provisions or ""))
    ):
        return UKSelectedAffectingSource(current_context, current_el, ())

    provision_ref = str(effect.affecting_provisions or "")
    enacted_source_observations: tuple[dict[str, Any], ...] = ()
    if (
        _compound_reference_parts(provision_ref) is not None
        or _parenthesized_range_source_ref(provision_ref) is not None
    ):
        enacted_el, enacted_source_observations = (
            _extract_from_affecting_source_context_with_observations(
                enacted_context,
                effect,
            )
        )
    else:
        enacted_el = _extract_from_affecting_source_context(
            enacted_context,
            effect.affecting_provisions,
        )
    enacted_payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
        enacted_context,
        effect,
        enacted_el,
    )
    if enacted_payload_descendant_rejection is not None:
        return UKSelectedAffectingSource(
            current_context,
            current_el,
            (enacted_payload_descendant_rejection,),
        )
    narrowed_enacted = _single_amendment_child_source(enacted_context, effect, enacted_el)
    enacted_el = narrowed_enacted.extracted_element
    enacted_source_observations = (
        *enacted_source_observations,
        *narrowed_enacted.observations,
    )
    enacted_text = _extracted_element_text(enacted_el)
    if enacted_el is None or _looks_like_non_substantive_shell_element(enacted_el):
        return UKSelectedAffectingSource(current_context, current_el, ())

    if current_missing:
        observation = uk_affecting_act_missing_current_enacted_source_selected(
            effect_id=str(effect.effect_id or ""),
            affecting_act_id=act_id,
            affecting_provisions=str(effect.affecting_provisions or ""),
            current_locator=current_context.locator,
            enacted_locator=enacted_context.locator,
            current_source_size=current_context.source_size,
            enacted_source_size=enacted_context.source_size,
            enacted_text_preview=_preview_source_text(enacted_text),
        )
    else:
        current_text = _extracted_element_text(current_el)
        observation = uk_affecting_act_current_shell_enacted_source_selected(
            effect_id=str(effect.effect_id or ""),
            affecting_act_id=act_id,
            affecting_provisions=str(effect.affecting_provisions or ""),
            current_locator=current_context.locator,
            enacted_locator=enacted_context.locator,
            current_source_size=current_context.source_size,
            enacted_source_size=enacted_context.source_size,
            current_text_preview=_preview_source_text(current_text),
            enacted_text_preview=_preview_source_text(enacted_text),
        )
    return UKSelectedAffectingSource(
        enacted_context,
        enacted_el,
        (observation, *enacted_source_observations),
    )
