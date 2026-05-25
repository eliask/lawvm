"""UK source-text classifiers used during lowering.

These helpers identify when source wording contradicts or refines broad effect
metadata. They return evidence only; the lowering caller owns the typed record
and any action reclassification.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag


@dataclass(frozen=True)
class UKTextReclassificationResult:
    curr_action: str
    content_ir: Optional[dict[str, Any]]
    detail: Optional[dict[str, str]]


@dataclass(frozen=True)
class UKQuoteOnlyOmissionLowering:
    applied: bool
    curr_action: str
    content_ir: Optional[dict[str, Any]]
    fragment_subs: Optional[list[dict[str, str]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]
    op_text_occurrence: Optional[int] = None


def _word_level_structural_subsection_omission(
    *,
    effect_type: str,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Identify word-level feed rows whose source explicitly repeals a subsection."""
    effect_type_norm = (effect_type or "").strip().lower()
    if effect_type_norm not in {"words omitted", "word omitted", "words repealed", "word repealed"}:
        return None
    if _addr_leaf_kind(target) != "subsection":
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bomit\s+(?:the\s+)?subsection\s*\(\s*(?P<label>[0-9A-Za-z]+)\s*\)(?=\W|$)",
        text,
        flags=re.I,
    )
    if match is None:
        match = re.search(
            r"\bsubsection\s*\(\s*(?P<label>[0-9A-Za-z]+)\s*\)\s+is\s+"
            r"(?:omitted|repealed)\b",
            text,
            flags=re.I,
        )
    if match is None:
        return None
    source_label = _clean_num(str(match.group("label") or ""))
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if not source_label or source_label != target_label:
        return None
    return {
        "source_target_kind": "subsection",
        "source_target_label": source_label,
        "matched_instruction": match.group(0),
    }


def reclassify_word_level_structural_subsection_omission(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    content_ir: Optional[dict[str, Any]],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTextReclassificationResult:
    detail = _word_level_structural_subsection_omission(
        effect_type=effect.effect_type,
        extracted_text=extracted_text,
        target=target,
    )
    if detail is None:
        return UKTextReclassificationResult(curr_action=curr_action, content_ir=content_ir, detail=None)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_word_omission_structural_subsection_repeal_reclassified",
        family="lowering_normalization",
        reason_code="word_level_feed_row_explicitly_omits_target_subsection",
        reason=(
            "UK effect feed labels the row as word-level omission, but "
            "the affecting source explicitly omits the exact affected subsection"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            **detail,
        },
    )
    return UKTextReclassificationResult(curr_action="repeal", content_ir=None, detail=detail)


def _empty_effect_type_as_if_words_omitted(text: str) -> bool:
    norm = " ".join(str(text or "").split()).lower()
    if not norm:
        return False
    return (
        "shall have effect" in norm
        and "as if" in norm
        and re.search(r"\bwords?\b", norm) is not None
        and re.search(r"\b(?:were|was)\s+omitted\b", norm) is not None
    )


_DOUBLE_QUOTED_FRAGMENT_RE = re.compile(r'["\u201c]([^"\u201d]+)["\u201d]')
_CHILD_QUALIFIED_WORD_OMISSION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)[.)]?\s+){0,2}"
    r"the\s+words?\s+[\"\u201c](?P<fragment>.*?)[\"\u201d]\s+"
    r"in\s+(?P<child_kind>paragraph|sub-?paragraph|subsection|section)\s+"
    r"\(?(?P<child_label>[0-9A-Za-z]+)\)?\s*,?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_PREFIX_SUBSECTION_PARAGRAPH_WORD_OMISSION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)[.)]?\s+){0,2}"
    r"in\s+(?P<parent_kind>subsection|sub-?paragraph)\s+\(\s*(?P<parent_label>[0-9A-Za-z]+)\s*\)"
    r"\s*\(\s*(?P<child_label>[0-9A-Za-z]+)\s*\)\s*,?\s+"
    r"the\s+words?\s+[\"\u201c](?P<fragment>.*?)[\"\u201d]\s*,?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_PREFIX_CHILD_WORD_OMISSION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)[.)]?\s+){0,2}"
    r"in\s+(?P<child_kind>subsection|paragraph|sub-?paragraph)\s+\(\s*(?P<child_label>[0-9A-Za-z]+)\s*\)"
    r"\s*,?\s+the\s+words?\s+[\"\u201c](?P<fragment>.*?)[\"\u201d]\s*,?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_CHILD_QUALIFIED_FINAL_WORD_OMISSION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)[.)]?\s+){0,2}"
    r"(?:the\s+)?(?:word\s+)?[\"\u201c](?P<fragment>.*?)[\"\u201d]\s+"
    r"at\s+the\s+end\s+of\s+"
    r"(?P<child_kind>paragraph|sub-?paragraph|subsection|section)\s+"
    r"\(?(?P<child_label>[0-9A-Za-z]+)\)?\s*,?\s*(?:and)?\s*;?\s*\.?\s*$",
    flags=re.I | re.S,
)


def _quote_only_omission_payload_match(extracted_text: str) -> Optional[str]:
    """Return a quoted deletion fragment when the payload contains no instruction text."""
    normalized = " ".join((extracted_text or "").split())
    if not normalized:
        return None
    matches = list(_DOUBLE_QUOTED_FRAGMENT_RE.finditer(normalized))
    if len(matches) != 1:
        return None
    match = matches[0]
    residue = (normalized[: match.start()] + normalized[match.end() :]).strip()
    residue = re.sub(r"^(?:[ivxlcdm]+|[a-z]|\d+)[.)]?\s*", "", residue, flags=re.I)
    residue = residue.strip(" \t\r\n,;.")
    if residue.lower() not in {"", "and", "or"}:
        return None
    fragment = " ".join(match.group(1).split()).strip()
    return fragment or None


def _child_qualified_word_omission_payload_match(
    *,
    effect_type: str,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Return a deletion fragment when source names the exact affected child."""
    effect_type_norm = (effect_type or "").strip().lower()
    if effect_type_norm not in {"words omitted", "word omitted", "words repealed", "word repealed"}:
        return None
    normalized = " ".join((extracted_text or "").split()).strip()
    if not normalized:
        return None
    match = _CHILD_QUALIFIED_WORD_OMISSION_RE.match(normalized)
    if match is None:
        match = _PREFIX_SUBSECTION_PARAGRAPH_WORD_OMISSION_RE.match(normalized)
        if match is not None:
            parent_kind = match.group("parent_kind").replace("-", "").lower()
            parent_kind = "subparagraph" if parent_kind == "subparagraph" else parent_kind
            child_kind = "item" if parent_kind == "subparagraph" else "paragraph"
            parent_label = _source_child_label(match.group("parent_label"))
            child_label = _source_child_label(match.group("child_label"))
            target_path = tuple(target.path or ())
            target_parent_kind = target_path[-2][0] if len(target_path) >= 2 else ""
            target_parent_label = _source_child_label(target_path[-2][1]) if len(target_path) >= 2 else ""
            target_kind = _addr_leaf_kind(target)
            target_label = _source_child_label(_addr_leaf_label(target) or "")
            if (
                target_parent_kind != parent_kind
                or target_parent_label != parent_label
                or target_kind != child_kind
                or target_label != child_label
            ):
                return None
            fragment = " ".join(match.group("fragment").split()).strip()
            if not fragment:
                return None
            return {
                "fragment": fragment,
                "source_child_kind": child_kind,
                "source_child_label": child_label,
                "source_parent_kind": parent_kind,
                "source_parent_label": parent_label,
                "matched_instruction": match.group(0),
            }
    if match is None:
        match = _PREFIX_CHILD_WORD_OMISSION_RE.match(normalized)
        if match is not None:
            child_kind = match.group("child_kind").replace("-", "").lower()
            child_kind = "subparagraph" if child_kind == "subparagraph" else child_kind
            child_label = _source_child_label(match.group("child_label"))
            target_kind = _addr_leaf_kind(target)
            target_label = _source_child_label(_addr_leaf_label(target) or "")
            if target_kind != child_kind or target_label != child_label:
                return None
            fragment = " ".join(match.group("fragment").split()).strip()
            if not fragment:
                return None
            return {
                "fragment": fragment,
                "source_child_kind": child_kind,
                "source_child_label": child_label,
                "matched_instruction": match.group(0),
            }
    if match is None:
        return None
    source_kind = match.group("child_kind").replace("-", "").lower()
    source_kind = "subparagraph" if source_kind == "subparagraph" else source_kind
    source_label = _source_child_label(match.group("child_label"))
    target_kind = _addr_leaf_kind(target)
    target_label = _source_child_label(_addr_leaf_label(target) or "")
    if source_kind != target_kind or not source_label or source_label != target_label:
        return None
    fragment = " ".join(match.group("fragment").split()).strip()
    if not fragment:
        return None
    return {
        "fragment": fragment,
        "source_child_kind": source_kind,
        "source_child_label": source_label,
        "matched_instruction": match.group(0),
    }


def _child_qualified_final_word_omission_payload_match(
    *,
    effect_type: str,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Return a final-occurrence deletion when source names the exact affected child."""
    effect_type_norm = (effect_type or "").strip().lower()
    if effect_type_norm not in {"words omitted", "word omitted", "words repealed", "word repealed"}:
        return None
    normalized = " ".join((extracted_text or "").split()).strip()
    if not normalized:
        return None
    match = _CHILD_QUALIFIED_FINAL_WORD_OMISSION_RE.match(normalized)
    if match is None:
        return None
    source_kind = match.group("child_kind").replace("-", "").lower()
    source_kind = "subparagraph" if source_kind == "subparagraph" else source_kind
    source_label = _source_child_label(match.group("child_label"))
    target_kind = _addr_leaf_kind(target)
    target_label = _source_child_label(_addr_leaf_label(target) or "")
    if source_kind != target_kind or not source_label or source_label != target_label:
        return None
    fragment = " ".join(match.group("fragment").split()).strip()
    if not fragment:
        return None
    return {
        "fragment": fragment,
        "source_child_kind": source_kind,
        "source_child_label": source_label,
        "matched_instruction": match.group(0),
    }


def _source_child_label(label: str) -> str:
    return str(label or "").strip().strip("()").lower()


SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+the\s+words\s+(?:following|after)\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"substitute\b",
    flags=re.I | re.S,
)
SOURCE_FROM_ANCHOR_STRUCTURED_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+the\s+words\s+from\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"to\s+the\s+end\b.+?\bsubstitute\b",
    flags=re.I | re.S,
)


def source_following_anchor_structured_substitution_anchor(source_text: str) -> str:
    text = str(source_text or "")
    if not text:
        return ""
    match = SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE.search(text)
    if match is None:
        return ""
    return " ".join(match.group("anchor").split()).strip()


def source_structured_tail_substitution_trim_selector(source_text: str) -> tuple[str, str, str]:
    """Return replay selector, anchor, and range mode for source-carried children."""
    text = str(source_text or "")
    if not text:
        return "", "", ""
    from_match = SOURCE_FROM_ANCHOR_STRUCTURED_SUBSTITUTION_RE.search(text)
    if from_match is not None:
        anchor = " ".join(from_match.group("anchor").split()).strip()
        if anchor:
            return f"TEXT_FROM_{anchor}_TO_END", anchor, "from_quoted_text_to_end"
    following_match = SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE.search(text)
    if following_match is None:
        return "", "", ""
    anchor = " ".join(following_match.group("anchor").split()).strip()
    if not anchor:
        return "", "", ""
    return f"TEXT_AFTER_{anchor}_TO_END", anchor, "after_quoted_text_to_end"


_DEFINITION_LIST_OMISSION_CONTEXT_RE = re.compile(
    r"(?:^|\b)omit\s+(?:the\s+)?definitions?\s+of(?:\b|[\u2014-])",
    flags=re.I,
)


def _quote_only_definition_list_omission_payload_match(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[tuple[str, str]]:
    """Return a definition term inherited from a parent definition-list omission."""
    fragment = _quote_only_omission_payload_match(extracted_text or "")
    if not fragment:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        ancestor_text = _instruction_text_before_amendment_container(ancestor)
        if _DEFINITION_LIST_OMISSION_CONTEXT_RE.search(ancestor_text):
            source_parent_id = str(ancestor.get("id") or "")
            if not source_parent_id:
                source_parent_id = next(
                    (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                    "",
                )
            return fragment, source_parent_id
    return None


def lower_quote_only_word_omission(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    curr_action: str,
    content_ir: Optional[dict[str, Any]],
    is_word_level: bool,
    targets_str: list[str],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKQuoteOnlyOmissionLowering:
    if not (
        is_word_level
        and effect_type in {"words omitted", "word omitted", "words repealed", "word repealed"}
        and len(targets_str) == 1
    ):
        return UKQuoteOnlyOmissionLowering(
            applied=False,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=None,
            op_text_match=None,
            op_text_replacement=None,
        )

    child_qualified_omission = _child_qualified_word_omission_payload_match(
        effect_type=effect_type,
        extracted_text=extracted_text,
        target=target,
    )
    if child_qualified_omission is not None:
        op_text_match = child_qualified_omission["fragment"]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_child_qualified_word_omission_text_patch",
            family="text_rewrite_lowering",
            reason_code="word_omission_payload_names_exact_child_target",
            reason=(
                "UK word-level omission source row quotes the deleted words and "
                "names the exact child already selected by the effect feed; "
                "lowering uses the feed action and target without widening scope."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                **child_qualified_omission,
            },
        )
        return UKQuoteOnlyOmissionLowering(
            applied=True,
            curr_action="text_repeal",
            content_ir=None,
            fragment_subs=[
                {
                    "original": op_text_match,
                    "replacement": "",
                    "rule_id": "uk_effect_child_qualified_word_omission_text_patch",
                }
            ],
            op_text_match=op_text_match,
            op_text_replacement="",
        )

    child_qualified_final_omission = _child_qualified_final_word_omission_payload_match(
        effect_type=effect_type,
        extracted_text=extracted_text,
        target=target,
    )
    if child_qualified_final_omission is not None:
        op_text_match = child_qualified_final_omission["fragment"]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_child_qualified_final_word_omission_text_patch",
            family="text_rewrite_lowering",
            reason_code="word_omission_payload_names_exact_child_target_and_final_occurrence",
            reason=(
                "UK word-level omission source row quotes the deleted final word "
                "and names the exact child already selected by the effect feed; "
                "lowering uses a final-occurrence text patch without widening scope."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "occurrence": -1,
                **child_qualified_final_omission,
            },
        )
        return UKQuoteOnlyOmissionLowering(
            applied=True,
            curr_action="text_repeal",
            content_ir=None,
            fragment_subs=[
                {
                    "original": op_text_match,
                    "replacement": "",
                    "occurrence": "-1",
                    "rule_id": "uk_effect_child_qualified_final_word_omission_text_patch",
                }
            ],
            op_text_match=op_text_match,
            op_text_replacement="",
            op_text_occurrence=-1,
        )

    quote_only_definition_omission = _quote_only_definition_list_omission_payload_match(
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
    )
    if quote_only_definition_omission is not None:
        definition_term, source_parent_id = quote_only_definition_omission
        op_text_match = f"TEXT_DEFINITION_ENTRY_{definition_term}"
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_quote_only_definition_list_omission_text_patch",
            family="text_rewrite_lowering",
            reason_code="quote_only_payload_in_parent_definition_omission_list",
            reason=(
                "UK word-level omission source row contains only a quoted "
                "definition term, and its parent source instruction explicitly "
                "omits definitions; lowering preserves a bounded definition-entry "
                "selector instead of deleting every phrase occurrence."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "definition_term": definition_term,
                "source_parent_id": source_parent_id,
            },
        )
        return UKQuoteOnlyOmissionLowering(
            applied=True,
            curr_action="text_repeal",
            content_ir=None,
            fragment_subs=[
                {
                    "original": op_text_match,
                    "replacement": "",
                    "rule_id": "uk_effect_quote_only_definition_list_omission_text_patch",
                }
            ],
            op_text_match=op_text_match,
            op_text_replacement="",
        )

    quote_only_omission = _quote_only_omission_payload_match(extracted_text or "")
    if not quote_only_omission:
        return UKQuoteOnlyOmissionLowering(
            applied=False,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=None,
            op_text_match=None,
            op_text_replacement=None,
        )
    return UKQuoteOnlyOmissionLowering(
        applied=True,
        curr_action="text_repeal",
        content_ir=None,
        fragment_subs=[
            {
                "original": quote_only_omission,
                "replacement": "",
                "rule_id": "uk_effect_quote_only_omission_payload_text_patch",
            }
        ],
        op_text_match=quote_only_omission,
        op_text_replacement="",
    )


def _source_parent_application_modification_context(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
) -> str:
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return ""
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor in ancestors:
        context_text = _instruction_text_before_amendment_container(ancestor)
        context_norm = " ".join(context_text.split()).strip()
        if not context_norm:
            continue
        if re.search(
            r"\bshall\s+apply\b.*\bsubject\s+to\s+(?:the\s+)?modification\s+that\b",
            context_norm,
            flags=re.I,
        ):
            return context_norm
    return ""


def _empty_effect_type_commencement_source(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip().lower()
    if not normalized:
        return False
    return bool(re.search(r"\bshall\s+come\s+into\s+force\b|\bcomes?\s+into\s+force\b", normalized))


_EXTERNAL_ACT_TARGET_RE = re.compile(
    r"\bto\s+the\s+(?P<title>[A-Z][^.;]*?\bAct\s+(?:1[0-9]{3}|20[0-9]{2}))\b",
    flags=re.I,
)
_PARTIAL_WHOLE_ACT_REPEAL_RE = re.compile(
    r"\b(?:the\s+)?whole\s+Act\s+\(other\s+than\s+(?P<exceptions>[^)]+)\)\s+is\s+repealed\b",
    flags=re.I,
)


def _external_act_target_from_source_text(extracted_text: Optional[str]) -> str:
    """Return an external Act title named as the amendment target, if obvious."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return ""
    match = _EXTERNAL_ACT_TARGET_RE.search(text)
    if match is None:
        return ""
    title = " ".join(match.group("title").split()).strip(" ,")
    if not title or title.lower().startswith("this act"):
        return ""
    return title


def _partial_whole_act_repeal_exceptions(extracted_text: Optional[str]) -> str:
    """Return exception text for unsupported whole-Act partial repeal, if explicit."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return ""
    match = _PARTIAL_WHOLE_ACT_REPEAL_RE.search(text)
    if match is None:
        return ""
    exceptions = " ".join(match.group("exceptions").split()).strip(" ,;")
    return exceptions
