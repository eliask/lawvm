"""UK affecting-source context extraction and source-lane recovery helpers."""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional, Sequence

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
    uk_affecting_act_compound_reference_split_fallback,
    uk_affecting_act_current_shell_enacted_source_selected,
    uk_affecting_act_enacted_schedule_table_row_source_extracted,
    uk_affecting_act_implicit_first_subparagraph_context_ignored,
    uk_affecting_act_missing_current_enacted_source_selected,
    uk_affecting_act_nonaddressable_schedule_part_context_ignored,
    uk_affecting_act_outdented_child_source_selected,
    uk_affecting_act_parenthesized_range_source_extracted,
    uk_affecting_act_schedule_part_standalone_split_rejection,
    uk_affecting_act_xml_missing_rejection,
    uk_affecting_act_xml_parse_rejection,
    uk_affecting_act_xml_too_small_rejection,
    uk_source_state_wire_tuple,
)
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag, _text_content


@dataclass(frozen=True)
class UKAffectingSourceContext:
    xml_bytes: Optional[bytes]
    root: Optional[ET.Element]
    parent_map: Optional[dict[ET.Element, ET.Element]]
    exact_id_map: dict[str, ET.Element]
    sequence_map: dict[tuple[str, ...], ET.Element]
    source_status: str
    source_size: int
    locator: str
    authority_layer: str
    provision_extractor: Callable[..., Optional[ET.Element]] = extract_provision_element_from_bytes
    provision_element_cache: dict[str, Optional[ET.Element]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )


def _first_amendment_container(el: Optional[ET.Element]) -> Optional[ET.Element]:
    if el is None:
        return None
    if _tag(el) in ("BlockAmendment", "InlineAmendment"):
        return el
    for child in el.iter():
        if child is not el and _tag(child) in ("BlockAmendment", "InlineAmendment"):
            return child
    return None


@lru_cache(maxsize=1024)
def _source_parent_map(
    source_root: ET.Element,
) -> dict[ET.Element, ET.Element]:
    """Return a cached parent map for source XML ancestor queries."""
    return {child: parent for parent in source_root.iter() for child in parent}


@lru_cache(maxsize=16384)
def _source_ancestor_chain(
    source_root: Optional[ET.Element],
    el: Optional[ET.Element],
) -> tuple[ET.Element, ...]:
    """Return closest-first source ancestors for an extracted source element."""
    if source_root is None or el is None:
        return ()
    if el is source_root:
        return ()
    parent_map = _source_parent_map(source_root)
    if el in parent_map:
        ancestors: list[ET.Element] = []
        parent = parent_map.get(el)
        while parent is not None:
            ancestors.append(parent)
            if parent is source_root:
                break
            parent = parent_map.get(parent)
        return tuple(ancestors)

    target_id = el.get("id")
    path: list[ET.Element] = []

    def _walk(node: ET.Element, ancestors: tuple[ET.Element, ...]) -> bool:
        if node is el or (target_id and node.get("id") == target_id):
            path.extend(reversed(ancestors))
            return True
        for child in node:
            if _walk(child, (*ancestors, node)):
                return True
        return False

    if _walk(source_root, ()):
        return tuple(path)
    return ()


def _unique_source_ancestor_chain_by_tag_text(
    source_root: Optional[ET.Element],
    el: Optional[ET.Element],
) -> tuple[ET.Element, ...]:
    """Reattach a detached extracted fragment to a unique same-text source node."""
    if source_root is None or el is None:
        return ()
    target_tag = _tag(el)
    target_text = " ".join(_text_content(el).split())
    if not target_tag or not target_text:
        return ()
    matches: list[tuple[ET.Element, ...]] = []

    def _walk(node: ET.Element, ancestors: tuple[ET.Element, ...]) -> None:
        if _tag(node) == target_tag and " ".join(_text_content(node).split()) == target_text:
            matches.append(tuple(reversed(ancestors)))
        for child in node:
            _walk(child, (*ancestors, node))

    _walk(source_root, ())
    if len(matches) != 1:
        return ()
    return matches[0]


def _source_text_before_extracted_child(
    parent: ET.Element,
    extracted_el: Optional[ET.Element],
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
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
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
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
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
    provision_extractor: Callable[..., Optional[ET.Element]] = extract_provision_element_from_bytes,
) -> tuple[UKAffectingSourceContext, Optional[ET.ParseError]]:
    source_status, source_size = uk_source_state_wire_tuple(xml_bytes)
    root = None
    parent_map = None
    exact_id_map: dict[str, ET.Element] = {}
    sequence_map: dict[tuple[str, ...], ET.Element] = {}
    parse_error = None
    if xml_bytes and source_status == "available":
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            parse_error = exc
        else:
            parent_map, exact_id_map, sequence_map = _build_extraction_context(root)
    return (
        UKAffectingSourceContext(
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
        parse_error,
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
) -> Optional[ET.Element]:
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
    keyword_pat = re.compile(r"\b(?:Sch(?:edule)?|Part|Pt)\b", re.I)
    matches = list(keyword_pat.finditer(provision_ref))
    if not matches:
        return None
    leading_prefix = provision_ref[: matches[0].start()]
    if re.search(
        r"\b(?:s|section|art|article|rule|reg|regulation)\.?\s*[0-9A-Za-z]+",
        leading_prefix,
        re.I,
    ):
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
    split_el: ET.Element,
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


def _extract_compound_reference_component(
    context: UKAffectingSourceContext,
    component_ref: str,
) -> Optional[ET.Element]:
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
) -> Optional[ET.Element]:
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
    el: ET.Element,
) -> ET.Element:
    if _tag(el) not in {"BlockAmendment", "InlineAmendment"} or context.parent_map is None:
        return el
    parent = context.parent_map.get(el)
    while parent is not None:
        if _tag(parent) in {"P1", "P2", "P3", "P4", "P5", "P6", "Section", "Subsection", "Paragraph"}:
            return parent
        parent = context.parent_map.get(parent)
    return el


def _payload_source_instruction_ancestor(
    context: UKAffectingSourceContext,
    el: ET.Element,
) -> Optional[ET.Element]:
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
    el: Optional[ET.Element],
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


def _unique_root_schedule_payload(context: UKAffectingSourceContext) -> Optional[ET.Element]:
    if context.root is None:
        return None
    schedules = [el for el in context.root.iter() if _tag(el) == "Schedule"]
    if len(schedules) != 1:
        return None
    return schedules[0]


def _extract_article_schedule_payload_source(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
    article_ref = _article_schedule_payload_ref(str(effect.affecting_provisions or ""))
    if article_ref is None:
        return None, ()

    article_el = _extract_from_affecting_source_context(context, article_ref)
    if article_el is None:
        return None, ()

    article_text = _text_content(article_el)
    affecting_prov = str(effect.affecting_provisions or "")
    has_explicit_sch = re.search(r"\bSch(?:edule)?\b", affecting_prov, flags=re.I) is not None
    if not has_explicit_sch and not re.search(r"\bset\s+out\s+in\s+the\s+Schedule\b", article_text, flags=re.I):
        return None, ()

    schedule_el = _unique_root_schedule_payload(context)
    if schedule_el is None:
        return None, ()

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
    return schedule_el, (observation,)


def _has_matching_part_ancestor(
    context: UKAffectingSourceContext,
    el: ET.Element,
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
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
    return _extract_parenthesized_range_source_ref(
        context,
        effect,
        str(effect.affecting_provisions or ""),
    )


def _outdented_child_source_ref(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    provision_ref: str,
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
    if context.parent_map is None:
        return None, ()
    match = re.fullmatch(
        r"\s*s(?:ection)?\.?\s*(?P<section>[0-9]+[A-Za-z]?)"
        r"\s*\(\s*(?P<parent>[0-9]+[A-Za-z]?)\s*\)"
        r"\s*\(\s*(?P<child>[A-Za-z]+)\s*\)\s*",
        provision_ref,
        flags=re.I,
    )
    if match is None:
        return None, ()
    section_label = match.group("section")
    parent_label = match.group("parent")
    child_label = match.group("child")
    requested_parent_id = f"section-{section_label}-{parent_label}"
    selected_child_id = f"section-{section_label}-{child_label.lower()}"
    requested_parent = context.sequence_map.get(_get_id_sequence(requested_parent_id))
    selected_child = context.sequence_map.get(_get_id_sequence(selected_child_id))
    if requested_parent is None or selected_child is None:
        return None, ()
    if context.parent_map.get(requested_parent) is not context.parent_map.get(selected_child):
        return None, ()
    if _clean_num(_direct_structural_num(selected_child)).lower() != child_label.lower():
        return None, ()
    selected_text = _text_content(selected_child)
    parent_pattern = re.compile(
        rf"\bsubsection\s*\(?\s*{re.escape(parent_label)}\s*\)?",
        flags=re.I,
    )
    if parent_pattern.search(selected_text) is None:
        return None, ()
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
    return selected_child, (observation,)


def _extract_parenthesized_range_source_ref(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
    provision_ref: str,
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
    parsed = _parenthesized_range_source_ref(provision_ref)
    if parsed is None:
        return None, ()
    parent_ref, start_label, end_label = parsed
    wanted_labels = _expand_source_child_label_range(start_label, end_label)
    if not wanted_labels:
        return None, ()
    selected: list[ET.Element] = []
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
            return None, ()
        by_label: dict[str, ET.Element] = {}
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
                return None, ()
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
    return wrapper, (observation,)


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


def _source_part_label_from_element(part_el: ET.Element) -> str:
    number_text = _text_content(part_el.find(f"./{{{_LEG_NS}}}Number"))
    match = re.search(r"\bpart\s+(?P<label>[0-9A-Za-zIVXLC]+)\b", number_text, flags=re.I)
    if match is not None:
        return _clean_num(match.group("label"))
    part_id = str(part_el.get("id") or part_el.get("Id") or "")
    tokens = _get_id_sequence(part_id)
    for index, token in enumerate(tokens[:-1]):
        if token == "part":
            return _clean_num(tokens[index + 1])
    return ""


def _direct_table_row_cells(row: ET.Element) -> tuple[ET.Element, ...]:
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
    row: ET.Element,
    cells: Sequence[ET.Element],
) -> ET.Element | None:
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
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
    effect_type = (effect.effect_type or "").strip().lower()
    if effect_type not in {"added", "inserted"}:
        return None, ()
    target_parts = _schedule_paragraph_ref_parts(str(effect.affected_provisions or ""))
    if target_parts is None:
        return None, ()
    target_schedule_label, target_paragraph_label = target_parts
    source_schedule_label = _schedule_ref_label(str(effect.affecting_provisions or ""))
    if source_schedule_label != target_schedule_label:
        return None, ()
    schedule_el = _extract_from_affecting_source_context(context, effect.affecting_provisions)
    if schedule_el is None or _tag(schedule_el) != "Schedule":
        return None, ()

    matches: list[tuple[ET.Element, tuple[ET.Element, ...], str, str]] = []
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
                matches.append((row, cells, part_label, target_paragraph_label.upper()))
    if len(matches) != 1:
        return None, ()

    row, cells, part_label, source_label_text = matches[0]
    synthetic = _synthetic_schedule_table_row_paragraph_source(
        schedule_label=target_schedule_label,
        part_label=part_label,
        target_label=source_label_text,
        row=row,
        cells=cells,
    )
    if synthetic is None:
        return None, ()
    observation = uk_affecting_act_enacted_schedule_table_row_source_extracted(
        effect_id=str(effect.effect_id or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affected_provisions=str(effect.affected_provisions or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        locator=context.locator,
        authority_layer=context.authority_layer,
        schedule_label=target_schedule_label,
        part_label=part_label,
        target_label=target_paragraph_label,
        source_row_text=_text_content(row),
    )
    return synthetic, (observation,)


def _extract_from_affecting_source_context_with_observations(
    context: UKAffectingSourceContext,
    effect: UKEffectRecord,
) -> tuple[Optional[ET.Element], tuple[dict[str, Any], ...]]:
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
                second_el = _extract_compound_reference_component(context, second_part)
                second_observations: tuple[dict[str, Any], ...] = ()
                if second_el is None:
                    second_el, second_observations = _extract_parenthesized_range_source_ref(
                        context,
                        effect,
                        second_part,
                    )
                if second_el is not None and second_el is not el:
                    payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
                        context,
                        effect,
                        second_el,
                    )
                    if payload_descendant_rejection is not None:
                        return None, (payload_descendant_rejection,)
                    return second_el, (
                        _compound_reference_split_observation(
                            context=context,
                            effect=effect,
                            first_part=first_part,
                            second_part=second_part,
                            selected_part="second",
                            split_el=second_el,
                        ),
                        *second_observations,
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
                return outdented_el, outdented_observations
            return None, (payload_descendant_rejection,)
        return el, ()

    range_el, range_observations = _extract_parenthesized_range_source(context, effect)
    if range_el is not None:
        return range_el, range_observations

    article_schedule_el, article_schedule_observations = _extract_article_schedule_payload_source(context, effect)
    if article_schedule_el is not None:
        return article_schedule_el, article_schedule_observations

    normalized = _schedule_part_context_normalized_ref(provision_ref)
    if normalized is not None:
        requested_part_label, normalized_ref = normalized
        normalized_el = _extract_from_affecting_source_context(context, normalized_ref)
        if normalized_el is None or not _has_matching_part_ancestor(
            context,
            normalized_el,
            requested_part_label,
        ):
            return None, ()

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
        return normalized_el, (observation,)

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
            return None, (schedule_part_rejection,)
        split_el = None
        split_selected_part = ""
        split_observations: tuple[dict[str, Any], ...] = ()
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

        if split_el is None and first_part:
            split_el = _extract_compound_reference_component(context, first_part)
            if split_el is not None:
                split_selected_part = "first"

        if split_el is not None:
            payload_descendant_rejection = _block_amendment_payload_descendant_source_rejection(
                context,
                effect,
                split_el,
            )
            if payload_descendant_rejection is not None:
                return None, (payload_descendant_rejection,)
            observation = _compound_reference_split_observation(
                context=context,
                effect=effect,
                first_part=first_part,
                second_part=second_part,
                selected_part=split_selected_part,
                split_el=split_el,
            )
            return split_el, (observation, *split_observations)

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
            return implicit_first_el, (observation,)

    return None, ()


def _extracted_element_text(el: Optional[ET.Element]) -> str:
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


def _looks_like_non_substantive_shell_element(el: Optional[ET.Element]) -> bool:
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
    current_el: Optional[ET.Element],
    enacted_context_cache: dict[str, UKAffectingSourceContext],
    enacted_xml_loader: Callable[[str, Any], Optional[bytes]] = get_affecting_act_enacted_xml_from_archive,
) -> tuple[UKAffectingSourceContext, Optional[ET.Element], tuple[dict[str, Any], ...]]:
    current_missing = current_el is None
    current_shell = _looks_like_non_substantive_shell_element(current_el)
    if not current_missing and not current_shell:
        return current_context, current_el, ()

    act_id = str(effect.affecting_act_id or "")
    if not act_id:
        return current_context, current_el, ()
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
        return enacted_context, schedule_row_el, schedule_row_observations
    if (
        current_missing
        and _schedule_paragraph_ref_parts(str(effect.affected_provisions or "")) is not None
        and _schedule_ref_label(str(effect.affecting_provisions or ""))
    ):
        return current_context, current_el, ()

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
        return current_context, current_el, (enacted_payload_descendant_rejection,)
    enacted_text = _extracted_element_text(enacted_el)
    if enacted_el is None or _looks_like_non_substantive_shell_element(enacted_el):
        return current_context, current_el, ()

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
    return enacted_context, enacted_el, (observation, *enacted_source_observations)
