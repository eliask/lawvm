from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.source_definition_context import (
    _source_definition_child_context_from_ancestors,
    _source_definition_child_refined_target,
    _source_definition_term_from_ancestors,
    _source_definition_term_from_local_ancestor_context,
)
from lawvm.uk_legislation.source_fragment_context import (
    _source_lead_text_before_subordinate_rows,
    _source_local_instruction_text_for_carried_payload,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag, _text_content


_SOURCE_CARRIED_FRAGMENT_ACTION_RE = re.compile(
    r"\b(?:omit|repeal|substitute|insert)\b",
    flags=re.I,
)


def _has_following_words_repeal_instruction(
    normalized_text: str,
    *,
    lowered_text: Optional[str] = None,
) -> bool:
    """Match the fixed parent instruction without scanning large text by regex."""
    lowered = normalized_text.lower() if lowered_text is None else lowered_text
    start = 0
    while True:
        index = lowered.find("following word", start)
        if index < 0:
            return False
        if index > 0 and lowered[index - 1].isalnum():
            start = index + 1
            continue
        rest = lowered[index + len("following word") :]
        if rest.startswith("s"):
            rest = rest[1:]
        if not rest.startswith(" "):
            start = index + 1
            continue
        rest = rest.lstrip()
        if rest.startswith("are "):
            rest = rest[4:].lstrip()
        if rest.startswith("repealed") or rest.startswith("omitted"):
            return True
        start = index + 1


@dataclass(frozen=True)
class UKDefinitionTextPatchLowering:
    target: LegalAddress
    curr_action: str
    content_ir: Optional[dict[str, Any]]
    fragment_subs: Optional[list[dict[str, str]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]


@dataclass(frozen=True)
class UKPseudoDefinitionChildTextPatch:
    target: LegalAddress
    fragment: dict[str, str]
    op_text_match: str
    op_text_replacement: str


@dataclass(frozen=True)
class UKPseudoDefinitionEntryRangeTextPatches:
    target: LegalAddress
    fragments: tuple[dict[str, str], ...]
    at_end_entries: tuple[dict[str, str], ...] = ()


UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID = (
    "uk_effect_source_range_definition_entry_insert_text_patch"
)
UK_METADATA_PSEUDO_DEFINITION_ENTRY_INSERT_RULE_ID = (
    "uk_effect_metadata_pseudo_definition_entry_insert_text_patch"
)
UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID = (
    "uk_effect_source_range_definition_entry_list_end_schedule_entry_insert"
)
UK_SOURCE_RANGE_DEFINITION_ENTRY_AT_END_REJECTION_RULE_ID = (
    "uk_effect_source_range_definition_entry_at_end_insert_rejected"
)


_SOURCE_RANGE_AFTER_DEFINITION_INSERT_RE = re.compile(
    r"^\s*(?:and\s+)?(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+)?"
    r"after\s+the\s+definition\s+of\s+(?:the\s+)?[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)"
    r"\s*[—–-]?\s*(?P<inserted>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)
_PSEUDO_DEFINITION_ENTRY_INSERT_RE = re.compile(
    r"\b(?P<direction>before|after)\s+the\s+definition\s+of\s+(?:the\s+)?"
    r"[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)"
    r"\s*[—–-]?\s*(?P<inserted>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_RANGE_AT_END_DEFINITION_INSERT_RE = re.compile(
    r"^\s*(?:and\s+)?(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+)?"
    r"at\s+the\s+end\s+(?:there\s+is\s+inserted|there\s+are\s+inserted|"
    r"there\s+shall\s+be\s+inserted|insert)\s*[—–-]?\s*(?P<inserted>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)


def _clean_pseudo_definition_target_label(label: str) -> str:
    return str(label or "").strip().strip("\"'“”‘’").strip()


def _pseudo_definition_base_target(target: LegalAddress) -> Optional[LegalAddress]:
    pseudo_index = next(
        (
            index
            for index, (_kind, label) in enumerate(target.path)
            if str(label).strip().lower() in {"defn", "defns"}
        ),
        -1,
    )
    if pseudo_index < 0:
        return None
    base_target = LegalAddress(path=target.path[:pseudo_index], special=None)
    if not base_target.path:
        return None
    return base_target


def _pseudo_definition_child_target_parts(
    target: LegalAddress,
) -> Optional[tuple[LegalAddress, str, str]]:
    pseudo_index = next(
        (
            index
            for index, (_kind, label) in enumerate(target.path)
            if str(label).strip().lower() == "defn"
        ),
        -1,
    )
    if pseudo_index < 0:
        return None
    raw_parts = [
        _clean_pseudo_definition_target_label(label)
        for _kind, label in target.path[pseudo_index + 1 :]
    ]
    parts = [part for part in raw_parts if part]
    if len(parts) < 2:
        return None
    child_label = _clean_num(parts[-1])
    if not re.fullmatch(r"[0-9A-Za-z]+", child_label):
        return None
    term = " ".join(parts[:-1]).strip()
    if not term:
        return None
    base_target = LegalAddress(path=target.path[:pseudo_index], special=None)
    if not base_target.path:
        return None
    return base_target, term, child_label


def _target_ref_names_pseudo_definition_entries(target_ref: str) -> bool:
    lowered = str(target_ref or "").lower()
    return "defn" in lowered or "defns" in lowered


def _target_ref_names_pseudo_definition_child(target_ref: str) -> bool:
    lowered = str(target_ref or "").lower()
    pseudo_index = max(lowered.rfind("defn"), lowered.rfind("defns"))
    if pseudo_index < 0:
        return False
    pseudo_tail = lowered[pseudo_index:]
    return bool(re.search(r"\bpara(?:graph)?\.?\s*\(", pseudo_tail))


def _quoted_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for match in re.finditer(r"[“\"'‘](.*?)[”\"'’]", str(text or "")):
        term = " ".join(match.group(1).split()).strip()
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)


def _source_range_definition_entry_insert_fragment(
    row_text: str,
    *,
    metadata_terms: tuple[str, ...],
) -> Optional[dict[str, str]]:
    normalized = " ".join(str(row_text or "").split()).strip()
    if not normalized:
        return None
    match = _SOURCE_RANGE_AFTER_DEFINITION_INSERT_RE.match(normalized)
    if match is None:
        return None
    anchor = " ".join(match.group("anchor").split()).strip()
    inserted = " ".join(match.group("inserted").split()).strip()
    inserted = re.sub(r"(?<=;)\s*,\s*(?:and\s*)?$", "", inserted).strip()
    if not anchor or not inserted or not _looks_like_definition_entry_payload(inserted):
        return None
    inserted_terms = _quoted_terms(inserted)
    extra_terms = tuple(term for term in inserted_terms if term not in metadata_terms)
    return {
        "original": f"TEXT_AFTER_DEFINITION_{anchor}",
        "replacement": inserted,
        "source_anchor_definition_term": anchor,
        "source_inserted_definition_terms": US.join(inserted_terms),
        "source_payload_additional_definition_terms": US.join(extra_terms),
        "rule_id": UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID,
    }


def _source_range_definition_entry_list_end_fragment(
    row_text: str,
    *,
    metadata_terms: tuple[str, ...],
    source_row_id: str,
) -> Optional[dict[str, str]]:
    normalized = " ".join(str(row_text or "").split()).strip()
    if not normalized:
        return None
    match = _SOURCE_RANGE_AT_END_DEFINITION_INSERT_RE.match(normalized)
    if match is None:
        return None
    inserted = " ".join(match.group("inserted").split()).strip()
    inserted = re.sub(r"(?<=;)\s*,\s*(?:and\s*)?$", "", inserted).strip()
    if not inserted or not _looks_like_definition_entry_payload(inserted):
        return None
    inserted_terms = _quoted_terms(inserted)
    extra_terms = tuple(term for term in inserted_terms if term not in metadata_terms)
    return {
        "inserted_text": inserted,
        "source_inserted_definition_terms": US.join(inserted_terms),
        "source_payload_additional_definition_terms": US.join(extra_terms),
        "source_row_id": source_row_id,
        "source_row_text": normalized[:500],
        "rule_id": UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
    }


def _metadata_pseudo_definition_entry_insert_fragment(
    row_text: str,
    *,
    metadata_terms: tuple[str, ...],
) -> Optional[dict[str, str]]:
    normalized = " ".join(str(row_text or "").split()).strip()
    if not normalized:
        return None
    match = _PSEUDO_DEFINITION_ENTRY_INSERT_RE.search(normalized)
    if match is None:
        return None
    anchor = " ".join(match.group("anchor").split()).strip()
    inserted = " ".join(match.group("inserted").split()).strip()
    inserted = re.sub(r"(?<=;)\s*,\s*(?:and\s*)?$", "", inserted).strip()
    if not anchor or not inserted or not _looks_like_definition_entry_payload(inserted):
        return None
    direction = match.group("direction").lower()
    selector = "TEXT_BEFORE_DEFINITION" if direction == "before" else "TEXT_AFTER_DEFINITION"
    inserted_terms = _quoted_terms(inserted)
    extra_terms = tuple(term for term in inserted_terms if term not in metadata_terms)
    return {
        "original": f"{selector}_{anchor}",
        "replacement": inserted,
        "source_anchor_definition_term": anchor,
        "source_inserted_definition_terms": US.join(inserted_terms),
        "source_payload_additional_definition_terms": US.join(extra_terms),
        "source_definition_insert_direction": direction,
        "rule_id": UK_METADATA_PSEUDO_DEFINITION_ENTRY_INSERT_RULE_ID,
    }


def lower_metadata_pseudo_definition_entry_insertions(
    *,
    effect: UKEffectRecord,
    action: str,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> Optional[UKPseudoDefinitionEntryRangeTextPatches]:
    """Lower one pseudo-definition entry target with an explicit source anchor."""
    if action != "insert":
        return None
    if not _target_ref_names_pseudo_definition_entries(target_ref):
        return None
    if extracted_el is None or _tag(extracted_el) == "SourceRange":
        return None
    base_target = _pseudo_definition_base_target(target)
    if base_target is None:
        return None
    metadata_terms = _quoted_terms(target_ref)
    fragment = _metadata_pseudo_definition_entry_insert_fragment(
        extracted_text or _text_content(extracted_el),
        metadata_terms=metadata_terms,
    )
    if fragment is None:
        return None
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_METADATA_PSEUDO_DEFINITION_ENTRY_INSERT_RULE_ID,
        family="definition_entry_elaboration",
        reason_code="metadata_definition_insert_anchor_resolved_from_source",
        reason=(
            "UK effect metadata encodes a definition entry as a pseudo "
            "structural target path, while the affecting source gives an "
            "explicit before/after-definition placement anchor and a "
            "definition-entry payload. Lowering strips the pseudo metadata "
            "path and emits a bounded definition-entry text insertion."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "original_target": str(target),
            "target": str(base_target),
            "text_match": str(fragment.get("original") or ""),
            "replacement": str(fragment.get("replacement") or ""),
            "source_anchor_definition_term": str(
                fragment.get("source_anchor_definition_term") or ""
            ),
            "source_definition_insert_direction": str(
                fragment.get("source_definition_insert_direction") or ""
            ),
            "source_inserted_definition_terms": tuple(
                term
                for term in str(
                    fragment.get("source_inserted_definition_terms") or ""
                ).split(US)
                if term
            ),
            "source_payload_additional_definition_terms": tuple(
                term
                for term in str(
                    fragment.get("source_payload_additional_definition_terms") or ""
                ).split(US)
                if term
            ),
        },
    )
    return UKPseudoDefinitionEntryRangeTextPatches(
        target=base_target,
        fragments=(fragment,),
    )


def lower_metadata_pseudo_definition_entry_range_insertions(
    *,
    effect: UKEffectRecord,
    action: str,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> Optional[UKPseudoDefinitionEntryRangeTextPatches]:
    """Lower source-range pseudo-definition inserts with explicit row anchors."""
    if action != "insert":
        return None
    if not _target_ref_names_pseudo_definition_entries(target_ref):
        return None
    if extracted_el is None or _tag(extracted_el) != "SourceRange":
        return None
    base_target = _pseudo_definition_base_target(target)
    if base_target is None:
        return None

    metadata_terms = _quoted_terms(target_ref)
    fragments: list[dict[str, str]] = []
    at_end_entries: list[dict[str, str]] = []
    unsupported_rows: list[dict[str, str]] = []
    for child in list(extracted_el):
        source_row_id = str(child.get("id") or child.get("Id") or "")
        row_text = " ".join(_text_content(child).split()).strip()
        fragment = _source_range_definition_entry_insert_fragment(
            row_text,
            metadata_terms=metadata_terms,
        )
        if fragment is not None:
            fragments.append(fragment)
            continue
        at_end_entry = _source_range_definition_entry_list_end_fragment(
            row_text,
            metadata_terms=metadata_terms,
            source_row_id=source_row_id,
        )
        if at_end_entry is not None:
            at_end_entries.append(at_end_entry)
            continue
        if _SOURCE_RANGE_AT_END_DEFINITION_INSERT_RE.match(row_text):
            unsupported_rows.append(
                {
                    "source_row_id": source_row_id,
                    "source_row_text": row_text[:500],
                }
            )
    if not fragments and not at_end_entries:
        return None

    for fragment in fragments:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID,
            family="definition_entry_elaboration",
            reason_code="source_range_definition_insert_anchor_resolved_from_row",
            reason=(
                "UK effect metadata encodes inserted definition entries as a "
                "pseudo structural target path, while the bounded source range "
                "contains row-local 'after the definition of ...' placement "
                "instructions. Lowering strips the pseudo metadata path and "
                "emits one bounded definition-entry text insertion per explicit "
                "source row."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(base_target),
                "text_match": str(fragment.get("original") or ""),
                "replacement": str(fragment.get("replacement") or ""),
                "source_anchor_definition_term": str(
                    fragment.get("source_anchor_definition_term") or ""
                ),
                "source_inserted_definition_terms": tuple(
                    term
                    for term in str(
                        fragment.get("source_inserted_definition_terms") or ""
                    ).split(US)
                    if term
                ),
                "source_payload_additional_definition_terms": tuple(
                    term
                    for term in str(
                        fragment.get("source_payload_additional_definition_terms") or ""
                    ).split(US)
                    if term
                ),
            },
        )

    for entry in at_end_entries:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
            family="definition_entry_elaboration",
            reason_code="source_range_definition_at_end_insert_structural_list_end",
            reason=(
                "UK effect metadata encodes inserted definition entries as a "
                "pseudo structural target path, while the bounded source range "
                "contains a row-local 'at the end there is inserted' placement "
                "instruction. Lowering strips the pseudo metadata path and "
                "emits a typed schedule-entry insert at the end of the direct "
                "definition-list children; replay must prove that carrier "
                "before mutating structure."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(base_target),
                "inserted_text": str(entry.get("inserted_text") or ""),
                "source_row_id": str(entry.get("source_row_id") or ""),
                "source_row_text": str(entry.get("source_row_text") or ""),
                "source_inserted_definition_terms": tuple(
                    term
                    for term in str(
                        entry.get("source_inserted_definition_terms") or ""
                    ).split(US)
                    if term
                ),
                "source_payload_additional_definition_terms": tuple(
                    term
                    for term in str(
                        entry.get("source_payload_additional_definition_terms") or ""
                    ).split(US)
                    if term
                ),
            },
        )

    if unsupported_rows:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=UK_SOURCE_RANGE_DEFINITION_ENTRY_AT_END_REJECTION_RULE_ID,
            family="definition_entry_elaboration",
            reason_code="source_range_definition_at_end_insert_requires_list_end_compiler",
            reason=(
                "UK source range contains a definition-entry insertion at the "
                "end of the definition list. LawVM does not yet prove a safe "
                "definition-list-end target distinct from arbitrary target text, "
                "so this row is left as an explicit blocked frontier instead of "
                "silently appending text."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(base_target),
                "unsupported_rows": tuple(unsupported_rows),
            },
        )

    return UKPseudoDefinitionEntryRangeTextPatches(
        target=base_target,
        fragments=tuple(fragments),
        at_end_entries=tuple(at_end_entries),
    )


def lower_metadata_pseudo_definition_child_substitution(
    *,
    effect: UKEffectRecord,
    action: str,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> Optional[UKPseudoDefinitionChildTextPatch]:
    """Lower feed pseudo-definition child replacements into bounded text patches."""
    if action != "replace":
        return None
    if not _target_ref_names_pseudo_definition_child(target_ref):
        return None
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    replacement = " ".join((extracted_text or "").split()).strip()
    if not replacement or _SOURCE_CARRIED_FRAGMENT_ACTION_RE.search(replacement):
        return None
    target_parts = _pseudo_definition_child_target_parts(target)
    if target_parts is None:
        return None
    base_target, term, child_label = target_parts
    text_match = f"TEXT_DEFINITION_CHILD_PARAGRAPH_{term}{US}{child_label}"
    fragment = {
        "original": text_match,
        "replacement": replacement,
        "source_definition_term": term,
        "source_child_label": child_label,
        "rule_id": "uk_effect_metadata_pseudo_definition_child_substitution_text_patch",
    }
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_metadata_pseudo_definition_child_substitution_text_patch",
        family="definition_entry_elaboration",
        reason_code="metadata_definition_child_target_with_source_payload",
        reason=(
            "UK effect metadata names a definition entry child paragraph as a "
            "pseudo structural path, while the affecting source supplies only "
            "the replacement child text. Lowering strips the pseudo metadata "
            "segments and emits a bounded definition-child text replacement "
            "instead of replaying the pseudo path as legal structure."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "original_target": str(target),
            "target": str(base_target),
            "source_definition_term": term,
            "source_child_label": child_label,
            "text_match": text_match,
            "replacement": replacement,
        },
    )
    return UKPseudoDefinitionChildTextPatch(
        target=base_target,
        fragment=fragment,
        op_text_match=text_match,
        op_text_replacement=replacement,
    )


def refine_source_definition_child_target(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    fragment: dict[str, str],
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    refined_target = _source_definition_child_refined_target(
        target=target,
        fragment=fragment,
    )
    if refined_target is None:
        return target

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_source_parent_definition_child_target_refined",
        family="source_context_elaboration",
        reason_code="source_parent_definition_child_refines_direct_section_paragraph",
        reason=(
            "UK affected-provision metadata names a direct section paragraph, "
            "while the source parent explicitly says that paragraph is inside "
            "a named definition entry; lowering targets the containing section "
            "and preserves the child paragraph as a scoped text selector."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "original_target": str(target),
            "refined_target": str(refined_target),
            "source_definition_term": str(fragment.get("source_definition_term") or ""),
            "source_child_label": str(fragment.get("source_child_label") or ""),
        },
    )
    return refined_target


def append_source_definition_fragment_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    for source_definition_fragment in fragment_subs or []:
        source_definition_rule_id = str(source_definition_fragment.get("rule_id") or "")
        if source_definition_rule_id not in {
            "uk_effect_source_parent_definition_range_text_patch",
            "uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch",
            "uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch",
            "uk_effect_source_parent_definition_child_substitution_text_patch",
        }:
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=source_definition_rule_id,
            family="source_context_elaboration",
            reason_code="text_patch_scoped_to_source_parent_definition",
            reason=(
                "UK child-row source gives a generic text patch while the parent "
                "instruction explicitly names a definition entry; lowering scopes "
                "the text patch to that definition instead of searching the whole "
                "target subsection."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(source_definition_fragment.get("source_parent_id") or ""),
                "source_definition_term": str(source_definition_fragment.get("source_definition_term") or ""),
                "source_unscoped_match_text": str(
                    source_definition_fragment.get("source_unscoped_match_text") or ""
                ),
                "source_child_label": str(source_definition_fragment.get("source_child_label") or ""),
                "source_child_sublabel": str(source_definition_fragment.get("source_child_sublabel") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
                "end_occurrence": op_text_end_occurrence,
            },
        )

    for definition_entry_context_fragment in fragment_subs or []:
        rule_id = str(definition_entry_context_fragment.get("rule_id") or "")
        if rule_id == "uk_effect_source_carried_definition_entry_insert_text_patch":
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rule_id,
                family="source_context_elaboration",
                reason_code="definition_insert_anchor_resolved_from_parent_source",
                reason=(
                    "UK source payload contains only the inserted definition entry, "
                    "while the parent source instruction names the definition anchor; "
                    "lowering combines those source-local facts instead of guessing "
                    "definition placement from live text."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_parent_id": str(definition_entry_context_fragment.get("source_parent_id") or ""),
                    "source_anchor_definition_term": str(
                        definition_entry_context_fragment.get("source_anchor_definition_term") or ""
                    ),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                    "payload_normalization_rule_ids": tuple(
                        rule_id
                        for rule_id in str(
                            definition_entry_context_fragment.get("payload_normalization_rule_ids") or ""
                        ).split(US)
                        if rule_id
                    ),
                },
            )
        elif rule_id == UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rule_id,
                family="definition_entry_elaboration",
                reason_code="source_range_definition_insert_anchor_resolved_from_row",
                reason=(
                    "UK effect metadata encodes inserted definition entries as "
                    "a pseudo structural target path, while the bounded source "
                    "range contains row-local 'after the definition of ...' "
                    "placement instructions. Lowering strips the pseudo metadata "
                    "path and emits a bounded definition-entry text insertion."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_anchor_definition_term": str(
                        definition_entry_context_fragment.get(
                            "source_anchor_definition_term"
                        )
                        or ""
                    ),
                    "source_inserted_definition_terms": tuple(
                        term
                        for term in str(
                            definition_entry_context_fragment.get(
                                "source_inserted_definition_terms"
                            )
                            or ""
                        ).split(US)
                        if term
                    ),
                    "source_payload_additional_definition_terms": tuple(
                        term
                        for term in str(
                            definition_entry_context_fragment.get(
                                "source_payload_additional_definition_terms"
                            )
                            or ""
                        ).split(US)
                        if term
                    ),
                    "text_match": str(
                        definition_entry_context_fragment.get("original") or op_text_match or ""
                    ),
                    "replacement": str(
                        definition_entry_context_fragment.get("replacement")
                        or op_text_replacement
                        or ""
                    ),
                },
            )
        elif rule_id == "uk_effect_source_carried_definition_entry_substitution_text_patch":
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rule_id,
                family="source_context_elaboration",
                reason_code="definition_substitution_anchor_resolved_from_parent_source",
                reason=(
                    "UK source payload contains only the replacement definition entry, "
                    "while the parent source instruction names the definition being "
                    "substituted; lowering combines those source-local facts instead "
                    "of guessing the old definition term from live text."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_parent_id": str(definition_entry_context_fragment.get("source_parent_id") or ""),
                    "source_original_definition_term": str(
                        definition_entry_context_fragment.get("source_original_definition_term") or ""
                    ),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                },
            )
        elif rule_id == "uk_effect_source_carried_definition_child_text_omission_text_patch":
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rule_id,
                family="source_context_elaboration",
                reason_code="definition_child_text_omission_resolved_from_parent_source",
                reason=(
                    "UK child-row source names only a definition paragraph and quoted "
                    "omitted text, while the parent source instruction names the "
                    "definition term; lowering combines those source-local facts into "
                    "a bounded definition-child text omission instead of deleting the "
                    "quoted word from the whole target subsection."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_parent_id": str(definition_entry_context_fragment.get("source_parent_id") or ""),
                    "source_definition_term": str(
                        definition_entry_context_fragment.get("source_definition_term") or ""
                    ),
                    "source_child_label": str(definition_entry_context_fragment.get("source_child_label") or ""),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                },
            )
        elif rule_id in {
            "uk_effect_source_carried_after_quoted_anchor_insert_text_patch",
            "uk_effect_source_carried_quoted_text_substitution_text_patch",
        }:
            reason_code = (
                "quoted_insert_anchor_resolved_from_parent_source"
                if rule_id == "uk_effect_source_carried_after_quoted_anchor_insert_text_patch"
                else "quoted_substitution_preimage_resolved_from_parent_source"
            )
            reason = (
                "UK source payload contains only the inserted text, while "
                "the parent source instruction names the quoted after-anchor; "
                "lowering combines those source-local facts instead of guessing "
                "the anchor from live text."
                if rule_id == "uk_effect_source_carried_after_quoted_anchor_insert_text_patch"
                else "UK source payload contains only the replacement text, while "
                "the parent source instruction names the quoted preimage; lowering "
                "combines those source-local facts instead of guessing the old text "
                "from live state."
            )
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rule_id,
                family="source_context_elaboration",
                reason_code=reason_code,
                reason=reason,
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_parent_id": str(definition_entry_context_fragment.get("source_parent_id") or ""),
                    "source_definition_term": str(
                        definition_entry_context_fragment.get("source_definition_term") or ""
                    ),
                    "source_inserted_text": str(
                        definition_entry_context_fragment.get("source_inserted_text") or ""
                    ),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                },
            )


_SOURCE_CARRIED_AFTER_THAT_DEFINITION_CHILD_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){1,2}after\s+that\s+paragraph,?\s+"
    r"insert\s*[—-]\s*(?P<inserted>.+?)\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_AT_END_DEFINITION_CHILD_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){1,2}at\s+the\s+end\s+of\s+paragraph\s+"
    r"\((?P<label>[0-9A-Za-z]+)\),?\s+insert\s*[—-]\s*(?P<inserted>.+?)\s*$",
    flags=re.I | re.S,
)
_SOURCE_FOR_DEFINITION_SUBSTITUTE_RE = re.compile(
    r"\bfor\s+the\s+definition\s+of\s+(?:the\s+)?[“\"'‘](?P<term>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+substituted|there\s+shall\s+be\s+substituted|substitute)",
    flags=re.I | re.S,
)
_SOURCE_AFTER_QUOTED_ANCHOR_INSERT_RE = re.compile(
    r"\bafter\s+(?:the\s+)?(?:word|words)?\s*[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)",
    flags=re.I | re.S,
)
_SOURCE_AFTER_QUOTED_ANCHOR_INLINE_INSERT_RE = re.compile(
    r"\bafter\s+(?:the\s+)?(?:word|words)?\s*[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)"
    r"\s*(?:[—–-]\s*)?[“\"'‘](?P<inserted>.*?)[”\"'’]",
    flags=re.I | re.S,
)
_SOURCE_FOR_QUOTED_TEXT_SUBSTITUTE_RE = re.compile(
    r"\bfor\s+(?:the\s+words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+substituted|there\s+shall\s+be\s+substituted|substitute)",
    flags=re.I | re.S,
)

_SOURCE_DEFINITION_QUOTE_CLOSE = {
    "“": "”",
    '"': '"',
    "'": "'",
    "‘": "’",
}
_SOURCE_DEFINITION_INSERT_PHRASES = (
    "there is inserted",
    "there are inserted",
    "there shall be inserted",
    "insert",
)
_SOURCE_DEFINITION_ENTRY_PREDICATES = (
    "means",
    "has the same meaning",
    "have the same meaning",
    "has the meaning",
    "have the meaning",
    "is to be construed",
    "are to be construed",
    "shall be construed",
)


def _is_appropriate_place_instruction_prefix_token(token: str) -> bool:
    return bool(
        re.fullmatch(r"(?:[ivxlcdm]+|[a-z]{1,3}|\d+[A-Za-z]?)", token, flags=re.I)
    )


def _strip_appropriate_place_definition_entry_instruction(text: str) -> Optional[str]:
    """Return body when a child payload embeds an unsupported appropriate-place instruction."""
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return None
    tokens = normalized.split()
    for prefix_len in range(1, min(3, len(tokens) - 4) + 1):
        if not all(
            _is_appropriate_place_instruction_prefix_token(token)
            for token in tokens[:prefix_len]
        ):
            continue
        rest = " ".join(tokens[prefix_len:]).strip()
        lower = rest.lower()
        matched_phrase = ""
        for phrase in ("at the appropriate place inserted", "at the appropriate place insert"):
            if lower.startswith(phrase):
                matched_phrase = phrase
                break
        if not matched_phrase:
            continue
        body_start = len(matched_phrase)
        while body_start < len(rest) and rest[body_start].isspace():
            body_start += 1
        if body_start >= len(rest) or rest[body_start] not in {"-", "—"}:
            continue
        body = rest[body_start + 1 :].strip()
        if body:
            return body
    return None


def _looks_like_definition_entry_payload(text: str, *, include_includes: bool = False) -> bool:
    """Return whether text contains a bounded quoted definition entry payload."""
    normalized = " ".join((text or "").split())
    if not normalized:
        return False
    lower = normalized.lower()
    predicates = _SOURCE_DEFINITION_ENTRY_PREDICATES
    if include_includes:
        predicates = (*predicates, "includes")
    for pos, char in enumerate(normalized):
        close_quote = _SOURCE_DEFINITION_QUOTE_CLOSE.get(char)
        if close_quote is None:
            continue
        close = normalized.find(close_quote, pos + 1)
        if close < 0:
            return False
        if close - pos > 240:
            continue
        semicolon = normalized.find(";", close + 1)
        tail_end = close + 320
        if semicolon >= 0:
            tail_end = min(tail_end, semicolon)
        tail = lower[close + 1 : tail_end]
        if any(predicate in tail for predicate in predicates):
            return True
    return False


def _normalize_trailing_double_comma(text: str) -> str:
    """Normalize source payloads ending in a duplicated comma without regex backtracking."""
    stripped = text.rstrip()
    if not stripped.endswith(","):
        return text
    before_last = stripped[:-1].rstrip()
    if not before_last.endswith(","):
        return text
    return f"{before_last[:-1].rstrip()},"


def _source_after_definition_insert_term(text: str) -> str:
    """Extract the anchor term from a bounded definition-entry insert formula."""
    normalized = " ".join((text or "").split())
    if not normalized:
        return ""
    lower = normalized.lower()
    needle = "after the definition of"
    start = 0
    while True:
        idx = lower.find(needle, start)
        if idx < 0:
            return ""
        pos = idx + len(needle)
        while pos < len(normalized) and normalized[pos].isspace():
            pos += 1
        if lower.startswith("the ", pos):
            pos += len("the ")
            while pos < len(normalized) and normalized[pos].isspace():
                pos += 1
        if pos >= len(normalized):
            return ""
        close_quote = _SOURCE_DEFINITION_QUOTE_CLOSE.get(normalized[pos])
        if close_quote is None:
            start = idx + 1
            continue
        close = normalized.find(close_quote, pos + 1)
        if close < 0:
            return ""
        if close - pos > 240:
            start = pos + 1
            continue
        next_formula = lower.find(needle, close + 1)
        tail_end = close + 220
        if next_formula >= 0:
            tail_end = min(tail_end, next_formula)
        tail = lower[close + 1 : tail_end]
        if any(phrase in tail for phrase in _SOURCE_DEFINITION_INSERT_PHRASES):
            return normalized[pos + 1 : close].strip()
        start = close + 1


def _looks_like_appropriate_place_definition_entry_insert_text(text: str) -> bool:
    norm = " ".join((text or "").split())
    if not re.search(r"\bat\s+(?:an?|the)\s+appropriate\s+places?\b", norm, flags=re.I):
        return False
    if not re.search(r"\binsert(?:ed|ion)?\b", norm, flags=re.I):
        return False
    return bool(
        re.search(
            r"[\"“][^\"”]{1,160}[\"”]\s*(?:,\s*[^;]{1,180})?\s+"
            r"(?:means|has\s+the\s+same\s+meaning|has\s+the\s+meaning|"
            r"is\s+to\s+be\s+construed|shall\s+be\s+construed|includes)\b",
            norm,
            flags=re.I,
        )
    )


def _source_parent_appropriate_place_definition_entry_insert_context(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Detect parent-owned appropriate-place definition entry insertions.

    The extracted payload may be only the definition entry, while the parent
    formula supplies "insert ... at the appropriate place". That is a real
    source instruction, but not a deterministic placement without an explicit
    anchor or claim.
    """
    inserted = " ".join((extracted_text or "").split()).strip()
    if not inserted or not _looks_like_definition_entry_payload(inserted):
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_local_instruction_text_for_carried_payload(ancestor)
        if not _looks_like_appropriate_place_definition_entry_insert_text(
            f"{candidate_text} {inserted}"
        ):
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (
                    str(candidate.get("id"))
                    for candidate in ancestors[ancestor_index + 1 :]
                    if candidate.get("id")
                ),
                "",
            )
        return {
            "source_parent_id": source_parent_id,
            "source_parent_context_preview": candidate_text[:500],
        }
    return None


def _fragment_substitution_source_carried_definition_child_at_end_insert(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve BlockAmendment payloads inserted at the end of a carried definition child."""
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return None
    row_context = _instruction_text_before_amendment_container(ancestors[0])
    if not re.match(r"^\s*at\s+the\s+end\s+there\s+is\s+inserted\b", row_context, flags=re.I | re.S):
        return None
    term, label, sublabel, source_parent_id = _source_definition_child_context_from_ancestors(ancestors)
    if not term or not label:
        return None
    inserted = " ".join((extracted_text or "").split()).strip()
    if not inserted:
        inserted = _text_content(extracted_el)
    inserted = " ".join(inserted.split()).strip()
    if not inserted:
        return None
    return {
        "original": f"TEXT_IN_DEFINITION_CHILD_PARAGRAPH_{term}{US}{label}{US}AT_END",
        "replacement": inserted,
        "source_parent_id": source_parent_id,
        "source_definition_term": term,
        "source_child_label": label,
        "source_child_sublabel": sublabel,
        "source_inserted_text": inserted,
        "rule_id": "uk_effect_source_carried_definition_child_at_end_insert_text_patch",
    }


def _previous_source_sibling_label(
    *,
    parent: ET.Element,
    extracted_el: Optional[ET.Element],
) -> str:
    children = list(parent)
    extracted_id = extracted_el.get("id") if extracted_el is not None else None
    for index, child in enumerate(children):
        if child is extracted_el or (extracted_id and child.get("id") == extracted_id):
            if index == 0:
                return ""
            return _clean_num(_direct_structural_num(children[index - 1]))
    return ""


def _fragment_substitution_source_carried_definition_child_insert(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve definition-child insertions whose child row says only "that paragraph"."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return None
    term = _source_definition_term_from_ancestors(ancestors)
    if not term:
        return None

    parent = ancestors[0]
    after_that_match = _SOURCE_CARRIED_AFTER_THAT_DEFINITION_CHILD_INSERT_RE.match(text)
    if after_that_match is not None:
        anchor_label = _previous_source_sibling_label(parent=parent, extracted_el=extracted_el)
        inserted = after_that_match.group("inserted").strip()
    else:
        at_end_match = _SOURCE_CARRIED_AT_END_DEFINITION_CHILD_INSERT_RE.match(text)
        if at_end_match is None:
            return None
        anchor_label = _clean_num(at_end_match.group("label"))
        inserted = at_end_match.group("inserted").strip()
    if not anchor_label or not inserted:
        return None

    source_parent_id = str(parent.get("id") or "")
    if not source_parent_id:
        source_parent_id = next(
            (str(candidate.get("id")) for candidate in ancestors[1:] if candidate.get("id")),
            "",
        )
    return {
        "original": f"TEXT_AFTER_DEFINITION_PARAGRAPH_{term}_AFTER_{anchor_label}",
        "replacement": inserted,
        "source_parent_id": source_parent_id,
        "source_anchor_child_label": anchor_label,
        "rule_id": "uk_effect_source_carried_definition_child_insert_text_patch",
    }


def _fragment_substitution_source_carried_definition_child_text_omission(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve definition-child word omissions whose parent supplies the term."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = re.match(
        r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
        r"in\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\),?\s+"
        r"omit\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]\s*,?\.?\s*$",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return None
    label = _clean_num(match.group("label"))
    original = " ".join(match.group("original").split()).strip()
    if not label or not original:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return None
    term = _source_definition_term_from_local_ancestor_context(
        ancestors,
        start_index=0,
        extracted_el=extracted_el,
    )
    if not term:
        return None
    parent = ancestors[0]
    source_parent_id = str(parent.get("id") or "")
    if not source_parent_id:
        source_parent_id = next(
            (str(candidate.get("id")) for candidate in ancestors[1:] if candidate.get("id")),
            "",
        )
    return {
        "original": f"TEXT_IN_DEFINITION_CHILD_PARAGRAPH_{term}{US}{label}{US}{original}",
        "replacement": "",
        "source_parent_id": source_parent_id,
        "source_definition_term": term,
        "source_child_label": label,
        "source_child_original_text": original,
        "rule_id": "uk_effect_source_carried_definition_child_text_omission_text_patch",
    }


def lower_source_carried_definition_child_text_omission(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    content_ir: Optional[dict[str, Any]],
    fragment_subs: Optional[list[dict[str, str]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKDefinitionTextPatchLowering:
    detail = (
        _fragment_substitution_source_carried_definition_child_text_omission(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if extracted_text
        else None
    )
    if detail is None:
        return UKDefinitionTextPatchLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
        )

    text_match = detail["original"]
    replacement = detail["replacement"]
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_source_carried_definition_child_text_omission_text_patch",
        family="source_context_elaboration",
        reason_code="definition_child_text_omission_resolved_from_parent_source",
        reason=(
            "UK child-row source names only a definition paragraph and quoted "
            "omitted text, while the parent source instruction names the "
            "definition term; lowering combines those source-local facts into "
            "a bounded definition-child text omission instead of deleting the "
            "quoted word from the whole target subsection."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "source_parent_id": str(detail.get("source_parent_id") or ""),
            "source_definition_term": str(detail.get("source_definition_term") or ""),
            "source_child_label": str(detail.get("source_child_label") or ""),
            "text_match": text_match,
            "replacement": replacement,
        },
    )
    return UKDefinitionTextPatchLowering(
        target=target,
        curr_action="text_repeal" if replacement == "" else "text_replace",
        content_ir=None,
        fragment_subs=[detail],
        op_text_match=text_match,
        op_text_replacement=replacement,
    )


def lower_source_carried_definition_child_at_end_insert(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    content_ir: Optional[dict[str, Any]],
    fragment_subs: Optional[list[dict[str, str]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKDefinitionTextPatchLowering:
    detail = (
        _fragment_substitution_source_carried_definition_child_at_end_insert(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if extracted_text and curr_action == "insert"
        else None
    )
    if detail is None:
        return UKDefinitionTextPatchLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
        )

    text_match = detail["original"]
    replacement = detail["replacement"]
    lowered_target = refine_source_definition_child_target(
        effect=effect,
        target=target,
        fragment=detail,
        target_ref=target_ref,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_source_carried_definition_child_at_end_insert_text_patch",
        family="source_context_elaboration",
        reason_code="definition_child_at_end_insert_resolved_from_parent_source",
        reason=(
            "UK source payload contains only the inserted definition-child tail, "
            "while the parent source instruction names the definition term and "
            "paragraph; lowering combines those source-local facts into a bounded "
            "definition-child text append instead of inserting an unreachable "
            "address-only subparagraph."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(lowered_target),
            "source_parent_id": str(detail.get("source_parent_id") or ""),
            "source_definition_term": str(detail.get("source_definition_term") or ""),
            "source_child_label": str(detail.get("source_child_label") or ""),
            "source_child_sublabel": str(detail.get("source_child_sublabel") or ""),
            "text_match": text_match,
            "replacement": replacement,
        },
    )
    return UKDefinitionTextPatchLowering(
        target=lowered_target,
        curr_action="text_replace",
        content_ir=None,
        fragment_subs=[detail],
        op_text_match=text_match,
        op_text_replacement=replacement,
    )


def _fragment_substitution_source_carried_definition_entry_insert(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve definition-entry insertions whose payload omits the parent anchor."""
    inserted = " ".join((extracted_text or "").split()).strip()
    payload_rule_ids: list[str] = []
    appropriate_place_without_anchor = False
    instruction_body = _strip_appropriate_place_definition_entry_instruction(inserted)
    if instruction_body is not None:
        inserted = instruction_body
        payload_rule_ids.append(
            "uk_effect_source_carried_definition_entry_payload_instruction_stripped"
        )
        appropriate_place_without_anchor = True
    normalized_inserted = _normalize_trailing_double_comma(inserted).strip()
    if normalized_inserted != inserted:
        inserted = normalized_inserted
        payload_rule_ids.append(
            "uk_effect_source_carried_definition_entry_payload_punctuation_normalized"
        )
    if not inserted or not _looks_like_definition_entry_payload(inserted):
        return None
    if appropriate_place_without_anchor:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_local_instruction_text_for_carried_payload(ancestor)
        anchor_term = _source_after_definition_insert_term(candidate_text)
        if not anchor_term:
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "original": f"TEXT_AFTER_DEFINITION_{anchor_term}",
            "replacement": inserted,
            "source_parent_id": source_parent_id,
            "source_anchor_definition_term": anchor_term,
            "payload_normalization_rule_ids": US.join(payload_rule_ids),
            "rule_id": "uk_effect_source_carried_definition_entry_insert_text_patch",
        }
    return None


def _fragment_substitution_source_carried_definition_entry_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve definition substitutions whose block payload omits the old term."""
    replacement = " ".join((extracted_text or "").split()).strip()
    if _looks_like_appropriate_place_definition_entry_insert_text(replacement):
        return None
    if not replacement or not _looks_like_definition_entry_payload(
        replacement,
        include_includes=True,
    ):
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_local_instruction_text_for_carried_payload(ancestor)
        match = _SOURCE_FOR_DEFINITION_SUBSTITUTE_RE.search(candidate_text)
        if match is None:
            continue
        original_term = " ".join(match.group("term").split()).strip()
        if not original_term:
            return None
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "original": f"TEXT_DEFINITION_ENTRY_{original_term}",
            "replacement": replacement,
            "source_parent_id": source_parent_id,
            "source_original_definition_term": original_term,
            "rule_id": "uk_effect_source_carried_definition_entry_substitution_text_patch",
        }
    return None


def _fragment_substitution_source_carried_following_words_repeal(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve block payloads whose parent says the following words are repealed."""
    original = " ".join((extracted_text or "").split()).strip()
    if not original or _SOURCE_CARRIED_FRAGMENT_ACTION_RE.search(original):
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _instruction_text_before_amendment_container(ancestor)
        if not candidate_text:
            candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            continue
        if not _has_following_words_repeal_instruction(
            candidate_text,
            lowered_text=candidate_text.lower(),
        ):
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "original": original,
            "replacement": "",
            "source_parent_id": source_parent_id,
            "rule_id": "uk_effect_source_carried_following_words_repeal_text_patch",
        }
    return None


def _fragment_substitution_source_carried_after_quoted_anchor_insert(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve block payloads whose parent instruction gives the after-anchor."""
    inserted = " ".join((extracted_text or "").split()).strip()
    if not inserted:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _instruction_text_before_amendment_container(ancestor)
        if not candidate_text:
            candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            continue
        inline_match = _SOURCE_AFTER_QUOTED_ANCHOR_INLINE_INSERT_RE.search(candidate_text)
        match = inline_match or _SOURCE_AFTER_QUOTED_ANCHOR_INSERT_RE.search(candidate_text)
        if match is None:
            return None
        anchor = " ".join(match.group("anchor").split()).strip()
        if not anchor:
            return None
        inline_inserted = (
            " ".join(inline_match.group("inserted").split()).strip()
            if inline_match is not None
            else ""
        )
        inserted_text = inline_inserted or inserted
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        joiner = "" if inserted_text.startswith((" ", ",", ".", ";", ":", ")")) else " "
        definition_term = _source_definition_term_from_local_ancestor_context(
            ancestors,
            start_index=ancestor_index,
            extracted_el=extracted_el,
        )
        original = anchor
        if definition_term:
            original = f"TEXT_IN_DEFINITION_{definition_term}{US}AFTER{US}{anchor}"
        return {
            "original": original,
            "replacement": f"{anchor}{joiner}{inserted_text}",
            "source_parent_id": source_parent_id,
            "source_anchor_text": anchor,
            "source_definition_term": definition_term,
            "source_inserted_text": inserted_text,
            "rule_id": "uk_effect_source_carried_after_quoted_anchor_insert_text_patch",
        }
    return None


def _fragment_substitution_source_carried_quoted_text_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve block payloads whose parent instruction gives the quoted preimage."""
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    replacement = " ".join((extracted_text or "").split()).strip()
    if not replacement:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _instruction_text_before_amendment_container(ancestor)
        if not candidate_text:
            candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            continue
        match = _SOURCE_FOR_QUOTED_TEXT_SUBSTITUTE_RE.search(candidate_text)
        if match is None:
            return None
        original = " ".join(match.group("original").split()).strip()
        if not original:
            return None
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": source_parent_id,
            "source_original_text": original,
            "rule_id": "uk_effect_source_carried_quoted_text_substitution_text_patch",
        }
    return None
