"""UK source-text classifiers used during lowering.

These helpers identify when source wording contradicts or refines broad effect
metadata. They return evidence only; the lowering caller owns the typed record
and any action reclassification.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag


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


SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+the\s+words\s+(?:following|after)\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"substitute\b",
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
