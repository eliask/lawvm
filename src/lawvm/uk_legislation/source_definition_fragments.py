from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.source_definition_context import (
    _source_definition_child_context_from_ancestors,
    _source_definition_term_from_ancestors,
    _source_definition_term_from_local_ancestor_context,
)
from lawvm.uk_legislation.source_fragment_context import (
    _source_lead_text_before_subordinate_rows,
    _source_local_instruction_text_for_carried_payload,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag, _text_content


@dataclass(frozen=True)
class UKDefinitionTextPatchLowering:
    curr_action: str
    content_ir: Optional[dict[str, Any]]
    fragment_subs: Optional[list[dict[str, str]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]


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
_SOURCE_AFTER_DEFINITION_INSERT_RE = re.compile(
    r"\bafter\s+the\s+definition\s+of\s+(?:the\s+)?[“\"'‘](?P<term>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)",
    flags=re.I | re.S,
)
_SOURCE_FOR_DEFINITION_SUBSTITUTE_RE = re.compile(
    r"\bfor\s+the\s+definition\s+of\s+(?:the\s+)?[“\"'‘](?P<term>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+substituted|there\s+shall\s+be\s+substituted|substitute)",
    flags=re.I | re.S,
)
_SOURCE_AFTER_QUOTED_ANCHOR_INSERT_RE = re.compile(
    r"\bafter\s+[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)",
    flags=re.I | re.S,
)
_SOURCE_AFTER_QUOTED_ANCHOR_INLINE_INSERT_RE = re.compile(
    r"\bafter\s+[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+inserted|there\s+are\s+inserted|there\s+shall\s+be\s+inserted|insert)"
    r"\s*(?:[—–-]\s*)?[“\"'‘](?P<inserted>.*?)[”\"'’]",
    flags=re.I | re.S,
)
_SOURCE_FOR_QUOTED_TEXT_SUBSTITUTE_RE = re.compile(
    r"\bfor\s+[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
    r"(?:there\s+is\s+substituted|there\s+shall\s+be\s+substituted|substitute)",
    flags=re.I | re.S,
)


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
        curr_action="text_repeal" if replacement == "" else "text_replace",
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
    instruction_match = re.match(
        r"""
        ^
        (?P<prefix>(?:(?:[ivxlcdm]+|[a-z]{1,3}|\d+[A-Za-z]?)\s+){1,3})
        at\s+the\s+appropriate\s+place\s+insert(?:ed)?
        \s*[—-]\s*
        (?P<body>.+)
        $
        """,
        inserted,
        flags=re.I | re.S | re.X,
    )
    if instruction_match is not None:
        inserted = " ".join(str(instruction_match.group("body") or "").split()).strip()
        payload_rule_ids.append(
            "uk_effect_source_carried_definition_entry_payload_instruction_stripped"
        )
        appropriate_place_without_anchor = True
    normalized_inserted = re.sub(r"\s*,\s*,\s*$", ",", inserted).strip()
    if normalized_inserted != inserted:
        inserted = normalized_inserted
        payload_rule_ids.append(
            "uk_effect_source_carried_definition_entry_payload_punctuation_normalized"
        )
    if not inserted or not re.search(
        r"[“\"'‘].+?[”\"'’](?:\s*\([^;]*?\))*[^;]{0,240}?"
        r"\b(?:means|has\s+the\s+same\s+meaning|has\s+the\s+meaning|"
        r"is\s+to\s+be\s+construed|shall\s+be\s+construed)\b",
        inserted,
        re.I | re.S,
    ):
        return None
    if appropriate_place_without_anchor:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_local_instruction_text_for_carried_payload(ancestor)
        match = _SOURCE_AFTER_DEFINITION_INSERT_RE.search(candidate_text)
        if match is None:
            continue
        anchor_term = " ".join(match.group("term").split()).strip()
        if not anchor_term:
            return None
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
    if not replacement or not re.search(
        r"[“\"'‘].+?[”\"'’](?:\s*\([^;]*?\))*[^;]{0,240}?"
        r"\b(?:means|has\s+the\s+same\s+meaning|has\s+the\s+meaning|"
        r"is\s+to\s+be\s+construed|shall\s+be\s+construed|includes)\b",
        replacement,
        re.I | re.S,
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
    if not original or re.search(r"\b(?:omit|repeal|substitute|insert)\b", original, flags=re.I):
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _instruction_text_before_amendment_container(ancestor)
        if not candidate_text:
            candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            continue
        if not re.search(
            r"\b(?:the\s+)?following\s+words?\s+(?:are\s+)?(?:repealed|omitted)\b",
            candidate_text,
            flags=re.I,
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
