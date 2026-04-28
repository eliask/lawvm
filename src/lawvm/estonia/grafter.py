"""Estonia (Riigi Teataja) frontend for LawVM.

Three entry points:

  parse_ee_statute(xml_bytes, statute_id) -> IRStatute
      Parse a tyviseadus (base act) XML document into the canonical IRStatute
      with a properly typed IRNode body tree.

  parse_ee_amendment_ops(xml_bytes, source_id) -> List[LegalOperation]
      Parse a muutmisseadus (amendment act) XML document, extracting each
      numbered amendment operation and mapping it to a LegalOperation with
      a LegalAddress target.

  apply_ee_ops(statute, ops) -> IRStatute
      Apply a list of LegalOperations to an IRStatute, producing an updated
      IRStatute. Used for ops-first replay; compare against ingest_consolidated()
      result via verify_consistency() to find legal divergences.

Both parse functions emit target types (IRNode, LegalAddress, LegalOperation)
directly — no intermediate jurisdiction-specific types.

XML schemas:
  tyviseadus_1_10.02.2010   — base acts
  muutmisseadus_1_10.02.2010 — amendment acts

Data source:
  https://www.riigiteataja.ee/akt/<id>.xml
  (No auth; Cloudflare present — use a realistic User-Agent)

See also: docs/estonia-pilot.md for full recon.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, replace
from typing import AbstractSet, Any, List, Literal, Optional, cast
import xml.etree.ElementTree as ET

from lawvm.core.ir import (
    TextPatchKindEnum,
    StructuralAction,
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.core import tree_ops
from lawvm.estonia.act_identity_registry import lookup_ee_act_identity
from lawvm.estonia.peg import _expand_ee_numeric_list, _instruction_preamble, _normalize_num, extract_ee_ops
from lawvm.estonia.ee_instruction_waist import read_payload_rewrite_meta
from lawvm.estonia.ee_instruction_waist import to_ee_parsed_instructions
from lawvm.estonia.target_resolution import (
    collect_embedded_target_sections as _tr_collect_embedded_target_sections,
    direct_target_clause_matches_registry as _tr_direct_target_clause_matches_registry,
    extract_intro_statute_fragment as _tr_extract_intro_statute_fragment,
    filter_direct_target_clause_op_texts as _tr_filter_direct_target_clause_op_texts,
    is_omnibus_amendment as _tr_is_omnibus_amendment,
    is_specific_direct_target_fragment as _tr_is_specific_direct_target_fragment,
    old_format_collect_all_ops as _tr_old_format_collect_all_ops,
    old_format_collect_nested_direct_target_ops as _tr_old_format_collect_nested_direct_target_ops,
    old_format_section_matches_target as _tr_old_format_section_matches_target,
    old_format_has_section_ref as _tr_old_format_has_section_ref,
    old_format_section_from_header_text as _tr_old_format_section_from_header_text,
    old_format_section_from_ops as _tr_old_format_section_from_ops,
    new_format_collect_all_ops as _tr_new_format_collect_all_ops,
    new_format_collect_op_texts as _tr_new_format_collect_op_texts,
    new_format_lower_op_texts as _tr_new_format_lower_op_texts,
    prepare_new_format_gate_flags as _tr_prepare_new_format_gate_flags,
    paragrahv_to_act_id as _tr_paragrahv_to_act_id,
    prepare_new_format_paragraph_context as _tr_prepare_new_format_paragraph_context,
    looks_like_self_referential_amendment_act_para as _tr_looks_like_self_referential_amendment_act_para,
    matches_target_statute_header as _tr_matches_target_statute_header,
    parse_constitutional_review_ops as _tr_parse_constitutional_review_ops,
    parse_preambul_single_target_ops as _tr_parse_preambul_single_target_ops,
    para_contains_direct_target_clause as _tr_para_contains_direct_target_clause,
    should_admit_new_format_paragraph as _tr_should_admit_new_format_paragraph,
    split_old_format_wrapper_blocks as _tr_split_old_format_wrapper_blocks,
    strict_title_match_para as _tr_strict_title_match_para,
    title_matches_para as _tr_title_matches_para,
)
from lawvm.estonia.text_morphology import (
    case_preserved_replacement as _tm_case_preserved_replacement,
    insert_sentence_after as _tm_insert_sentence_after,
    insert_sentence_before as _tm_insert_sentence_before,
    replace_first_sentence as _tm_replace_first_sentence,
    replace_case_preserving as _tm_replace_case_preserving,
    replace_sentence as _tm_replace_sentence,
    sentence_index_from_notes as _tm_sentence_index_from_notes,
    sentence_indexes_from_notes as _tm_sentence_indexes_from_notes,
    surface_pattern as _tm_surface_pattern,
    split_ee_sentences as _tm_split_ee_sentences,
    wrap_word_boundaries as _tm_wrap_word_boundaries,
)


# ---------------------------------------------------------------------------
# Context-inheritance helpers for amendment item loops
# ---------------------------------------------------------------------------


def _to_structural_action(action: str | StructuralAction) -> StructuralAction:
    """Map action to StructuralAction, preserving text-level variants."""
    if isinstance(action, StructuralAction):
        return action
    if action == "replace":
        return StructuralAction.REPLACE
    if action == "text_replace":
        return StructuralAction.TEXT_REPLACE
    if action == "repeal":
        return StructuralAction.REPEAL
    if action == "text_repeal":
        return StructuralAction.TEXT_REPEAL
    if action == "insert":
        return StructuralAction.INSERT
    if action == "renumber":
        return StructuralAction.RENUMBER
    # Fallback for unknown actions - should not happen in normal operation
    return StructuralAction.META


def _try_parse_int(s: str) -> Optional[int]:
    """Parse a string as int, returning None if not purely numeric."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _rewrite_item_terminal(text: str, terminal: str) -> str:
    """Rewrite a terminal ';' or '.' on an item text when list shape changes."""
    stripped = (text or "").rstrip()
    if not stripped:
        return text
    if re.search(r"\b(?:ja|või)$", stripped):
        return stripped
    if stripped.endswith((".", ";")):
        return stripped[:-1] + terminal
    if stripped.endswith(":"):
        return stripped
    return stripped + terminal


def _item_terminal_for_position(children: list[IRNode], item_index: int) -> str:
    """Return the RT-style terminal for a nonempty item at ``item_index``."""
    later_items = [child for child in children[item_index + 1 :] if child.kind == IRNodeKind.ITEM]
    if not later_items:
        return "."
    if any(bool((child.text or "").strip()) for child in later_items):
        return ";"
    # RT tends to finalize the last surviving live item when only a short
    # repeal-tail of empty placeholder items remains, but preserves the
    # semicolon for longer editorial placeholder runs.
    return ";" if len(later_items) > 2 else "."


def _normalize_item_list_terminals(children: list[IRNode]) -> list[IRNode]:
    """Normalize item terminals against the structural tail of the item list."""
    if not any(child.kind == IRNodeKind.ITEM for child in children):
        return children

    normalized: list[IRNode] = []
    for idx, child in enumerate(children):
        if child.kind != IRNodeKind.ITEM or not child.text:
            normalized.append(child)
            continue
        terminal = _item_terminal_for_position(children, idx)
        new_text = _rewrite_item_terminal(child.text, terminal)
        if new_text == child.text:
            normalized.append(child)
            continue
        normalized.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=new_text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        )
    return normalized


def _is_inserted_numbered_label(label: str | None) -> bool:
    """Return whether an EE numeric label is an inserted/superscript label."""
    return bool(label and "_" in label)


def _text_merge_signature(op: LegalOperation) -> tuple[object | None, object | None, int]:
    """Return the text-level part of the Estonia merge key.

    Typed ``text_patch`` is authoritative.
    """
    text_patch = op.text_patch
    if text_patch is None:
        return (None, None, 0)
    return (
        text_patch.selector.match_text,
        text_patch.replacement,
        text_patch.selector.occurrence,
    )


def _typed_text_replace_patch(old_text: str, new_text: str) -> TextPatchSpec:
    """Build a typed text-patch for a simple literal text replacement."""
    return TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text=old_text),
        replacement=new_text,
    )


def _replace_first_sentence(text: str, replacement: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_replace_first_sentence(text, replacement)


def _split_ee_sentences(text: str) -> list[str]:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_split_ee_sentences(text)


def _replace_sentence(text: str, replacement: str, sentence_index: int) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_replace_sentence(text, replacement, sentence_index)


def _sentence_indexes_from_notes(note_text: str) -> list[int]:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_sentence_indexes_from_notes(note_text)


def _sentence_index_from_notes(note_text: str) -> int | None:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_sentence_index_from_notes(note_text)


def _insert_sentence_after(text: str, inserted: str, sentence_index: int) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_insert_sentence_after(text, inserted, sentence_index)


def _insert_sentence_before(text: str, inserted: str, sentence_index: int) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_insert_sentence_before(text, inserted, sentence_index)


_EE_APPENDIX_TABLE_CATEGORY_ORDER = [
    "A",
    "B",
    "BE",
    "C",
    "CE",
    "D",
    "DE",
    "R",
    "T",
    "A1",
    "B1",
    "C1",
    "C1E",
    "D1",
    "D1E",
]
_EE_ITEM_START_PATTERN = r"(?:(?<=^)|(?<=[\s;:]))(?P<label>\d[\d\s]*)\)\s"


def _replace_appendix_table_rows(text: str, row_labels: list[str], replacement: str) -> str:
    """Replace a contiguous run of appendix table rows in linearized text."""
    if not text or not row_labels or not replacement:
        return text

    start_label = row_labels[0]
    start_match = re.search(r"(?<!\S)" + re.escape(start_label) + r"\s", text)
    if start_match is None:
        return text

    next_label = None
    if row_labels[-1] in _EE_APPENDIX_TABLE_CATEGORY_ORDER:
        idx = _EE_APPENDIX_TABLE_CATEGORY_ORDER.index(row_labels[-1])
        for candidate in _EE_APPENDIX_TABLE_CATEGORY_ORDER[idx + 1 :]:
            if candidate not in row_labels:
                next_label = candidate
                break

    end_pos = len(text)
    if next_label is not None:
        end_match = re.search(r"(?<!\S)" + re.escape(next_label) + r"\s", text[start_match.end() :])
        if end_match is not None:
            end_pos = start_match.end() + end_match.start()

    new_text = (
        text[: start_match.start()].rstrip() + " " + replacement.strip() + " " + text[end_pos:].lstrip()
    ).strip()
    return re.sub(r"\s+", " ", new_text)


def _parse_inline_item_children(
    raw_text: str,
    *,
    require_first_label_one: bool = True,
) -> tuple[str, List[IRNode]]:
    """Split inline numbered item lists without breaking compound labels like ``8 1)``."""
    matches = list(re.finditer(_EE_ITEM_START_PATTERN, raw_text))
    if not matches:
        return _strip_rt_editorial_parentheticals(raw_text.strip()), []

    intro_text = _strip_rt_editorial_parentheticals(raw_text[: matches[0].start()].strip())
    item_children: List[IRNode] = []
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        item_text = _strip_rt_editorial_parentheticals(raw_text[match.end() : next_start].strip())
        raw_label = re.sub(r"\s+", "_", match.group("label").strip()).rstrip("_")
        item_children.append(
            IRNode(
                kind=IRNodeKind.ITEM,
                label=raw_label,
                text=item_text,
            )
        )

    if require_first_label_one and item_children and item_children[0].label != "1":
        # Real inline item lists often follow a colon-introducer ("on muu hulgas:").
        # Without that cue, a lone citation suffix like "60–61)" is more likely.
        if not intro_text.endswith(":"):
            return _strip_rt_editorial_parentheticals(raw_text.strip()), []

    return intro_text, item_children


def _has_section_ref(text: str) -> bool:
    """True if the instruction preamble already contains an explicit structural reference."""
    return _tr_old_format_has_section_ref(text)


def _section_from_ops(ops: List[LegalOperation]) -> Optional[str]:
    """Return the section label from the first op that has one, else None."""
    return _tr_old_format_section_from_ops(ops)


def _section_from_header_text(header_text: str) -> Optional[str]:
    """Extract an initial target section label from an old-format section header."""
    return _tr_old_format_section_from_header_text(header_text)


def _first_tavatekst_text(para: ET.Element, ns_str: str) -> str:
    """Return the first tavatekst block from a paragrahv, normalized to plain text."""
    for st in para.iter(_ns(ns_str, "sisuTekst")):
        for t in st.findall(_ns(ns_str, "tavatekst")):
            txt = " ".join(str(_t) for _t in t.itertext()).replace("\xa0", " ")
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                return txt
    return ""


def _extract_intro_statute_fragment(text: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.target_resolution``."""
    return _tr_extract_intro_statute_fragment(text)


def _is_specific_direct_target_fragment(fragment: str) -> bool:
    """Compatibility wrapper; migrated to ``lawvm.estonia.target_resolution``."""
    return _tr_is_specific_direct_target_fragment(fragment)


def _direct_target_clause_matches_registry(
    *,
    fragment: str,
    target_title: str,
) -> bool:
    """Compatibility wrapper; migrated to ``lawvm.estonia.target_resolution``."""
    return _tr_direct_target_clause_matches_registry(fragment=fragment, target_title=target_title)


# ---------------------------------------------------------------------------
# XML namespace helpers
# ---------------------------------------------------------------------------

NS_BASE = "tyviseadus_1_10.02.2010"
NS_AMEND = "muutmisseadus_1_10.02.2010"


def _ns(ns_str: str, tag: str) -> str:
    return f"{{{ns_str}}}{tag}"


def _find(el: ET.Element, ns_str: str, *tags: str) -> Optional[ET.Element]:
    """Traverse a sequence of namespace-qualified tags from el."""
    cur = el
    for tag in tags:
        cur = cur.find(_ns(ns_str, tag))
        if cur is None:
            return None
    return cur


def _text(el: Optional[ET.Element]) -> str:
    """Return stripped text content of an element, or empty string.

    Normalizes non-breaking spaces (\xa0) to regular spaces so that
    oracle text (which uses \xa0 in cross-references like "§-s\xa03")
    compares equal to replay text extracted from amendment payloads.
    """
    if el is None:
        return ""
    return (el.text or "").replace("\xa0", " ").strip()


def _title_text(el: Optional[ET.Element]) -> str:
    """Extract full section title text, including text inside inline child tags.

    <paragrahvPealkiri> elements often contain <sup>/<sub> children for
    superscript section numbers (e.g. §-s 93<sup>1</sup> sätestatud...).
    Plain _text() only reads el.text (before the first child), truncating
    the title.  This function collects el.text + each inline child's text +
    its tail, separated by a space, matching the style used by amendment
    payload extraction (which replaces <tags> with spaces).

    Normalizes \xa0 → space and collapses multiple spaces.
    """
    if el is None:
        return ""
    parts: List[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local in _INLINE_TAGS:
            child_text = "".join(str(_t) for _t in child.itertext())
            if child_text:
                parts.append(child_text)
            if child.tail:
                parts.append(child.tail)
        # Non-inline children (structural) are skipped
    result = " ".join(parts)
    result = result.replace("\xa0", " ")
    result = re.sub(r"\s+", " ", result).strip()
    return result


# ---------------------------------------------------------------------------
# Base act parser (tyviseadus)
# ---------------------------------------------------------------------------

_INLINE_TAGS = frozenset({"i", "b", "em", "u", "strong", "span", "sub", "sup"})


def _looks_like_reavahetus_item_tail(text: str) -> bool:
    """Return True when a reavahetus tail starts a numbered item list entry."""
    return bool(re.match(r"^\s*\d[\d\s]*\)\s*", text or ""))


def _normalize_ee_statute_surface_text(text: str) -> str:
    """Normalize RT surface artifacts in parsed base-statute text."""
    if not text:
        return text
    text = re.sub(r"\s*\(RT\s+[IVX]+[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r" +([.,;:!?)])", r"\1", text)
    return text


def _tavatekst_text(t: ET.Element, ns_str: str) -> str:
    """Extract text from a tavatekst element, including inline formatting children.

    Captures text from inline elements (<i>, <b>, <em>, <u>, <sup>, <sub>) which
    wrap formatted text.

    For structural/line-break elements like <reavahetus>, keep the tail only when
    it is ordinary continuation text. Numbered-item tails such as "1) item one"
    still belong to the item extractor and must not be concatenated into the
    parent subsection text.

    Without this, plain t.text misses text inside inline tags (e.g. italicised
    terms like "kaugtõestamine" in "<i>kaugtõestamine</i>").
    With a naive itertext(), all reavahetus tails get concatenated into the parent
    subsection text, breaking comparisons with oracle where numbered item lists
    are stored as separate structural nodes.
    """
    parts: list[str] = []
    if t.text:
        parts.append(t.text)
    for child in t:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local in _INLINE_TAGS:
            # Inline: include its full text recursively.
            # For <sup>/<sub> elements (superscript section numbers like
            # 93<sup>1</sup>), insert a space before the child text so that
            # the result matches amendment payload extraction which replaces
            # all HTML tags with a space (93 1, not 931).
            child_text = "".join(str(_t) for _t in child.itertext())
            if child_text:
                if local in ("sup", "sub"):
                    parts.append(" ")
                parts.append(child_text)
            # Include tail (text after the closing inline tag but before next sibling)
            if child.tail:
                parts.append(child.tail)
        else:
            # Structural (reavahetus, etc.): keep the tail only when it is
            # continuation prose, not a numbered item marker handled elsewhere.
            if child.tail and not _looks_like_reavahetus_item_tail(child.tail):
                if (
                    parts
                    and not parts[-1].endswith((" ", "\n", "\t"))
                    and not child.tail.startswith((" ", ".", ",", ";", ":", ")"))
                ):
                    parts.append(" ")
                parts.append(child.tail)
    result = re.sub(r"\s+", " ", "".join(parts)).replace("\xa0", " ").strip()
    result = _normalize_ee_statute_surface_text(result)
    # RT sometimes prepends a kehtetu editorial annotation directly into the
    # materialized subsection text while the oracle presents only the live text.
    # Treat this bracketed marker as non-substantive presentation metadata.
    result = re.sub(r"^\[Kehtetu\s*-\s*[^\]]+\]\s*", "", result, flags=re.I)
    # RT editorial convention: standalone hyphen marks a repealed section.
    # New tervikteksts use en-dash (–); old tyviseadus XML uses plain hyphen (-).
    # Normalize for consistent comparison with oracle.
    if result == "-":
        result = "–"
    # Older tervikteksts omit the trailing period from the standard repealed-section
    # placeholder; newer ones include it.  Normalize to the canonical form.
    if result == "[Käesolevast tekstist välja jäetud]":
        result = "[Käesolevast tekstist välja jäetud.]"
    return result


def _sisuTekst_text(st: ET.Element, ns_str: str) -> str:
    """Extract concatenated text from a sisuTekst element in document order.

    Handles two text-bearing child types:
      - <tavatekst>: plain text (possibly with inline formatting children)
      - <viide><kuvatavTekst>: hyperlink display text (must be included inline)

    viide elements are hyperlinks embedded in the legal text; their kuvatavTekst
    is display text that forms part of the sentence, not metadata.

    reavahetus children inside tavatekst separate list items — their tails
    are skipped by _tavatekst_text (list items are parsed separately).
    """
    parts: list[str] = []
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "tavatekst":
            txt = _tavatekst_text(child, ns_str)
            if txt:
                parts.append(txt)
        elif local == "viide":
            # Extract kuvatavTekst display text from hyperlink element
            kvt = child.find(_ns(ns_str, "kuvatavTekst"))
            if kvt is not None and kvt.text:
                parts.append(kvt.text.replace("\xa0", " ").strip())
        # muutmismarge, avaldamismarge, etc. — metadata, skip
    result = " ".join(p for p in parts if p)
    result = _normalize_ee_statute_surface_text(result)
    # Some RT tervikteksts encode a kehtetu editorial annotation across
    # tavatekst + viide + tavatekst. Strip that presentation marker after the
    # full sisuTekst has been linearized.
    result = re.sub(r"^\[Kehtetu\s*-\s*[^\]]+\]\s*", "", result, flags=re.I)
    return result


def _html_to_plain_text(fragment: str) -> str:
    """Strip HTML markup to normalized plain text."""
    import html as _html

    text = _html.unescape(fragment or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _appendix_html_payload_text(fragment: str) -> str:
    """Linearize appendix HTML payloads such as RT tables into plain text."""
    plain = _html_to_plain_text(fragment)
    marker = _extract_appendix_marker(fragment)
    if marker and plain == marker:
        return ""
    return plain


def _extract_appendix_marker(fragment: str) -> str:
    """Extract a plain appendix marker like ``Lisa 1`` from HTMLKonteiner text."""
    plain = _html_to_plain_text(fragment)
    match = re.search(r"\bLisa\s+\d+\b", plain, re.IGNORECASE)
    return match.group(0) if match else ""


def _leading_appendix_marker(st: ET.Element) -> str:
    """Return the leading appendix marker for a sisuTekst, if it starts one."""
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "muutmismarge":
            continue
        if local == "HTMLKonteiner" and child.text:
            return _extract_appendix_marker(child.text)
        if local == "tavatekst":
            txt = _tavatekst_text(child, "")
            if txt:
                return ""
        if local == "viide":
            if "".join(str(_t) for _t in child.itertext()).strip():
                return ""
    return ""


def _subsection_uses_appendix_html(el: ET.Element, ns_str: str) -> bool:
    """True when subsection text should preserve appendix-style HTML payload text."""
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for child in st:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local != "HTMLKonteiner" or not child.text:
                continue
            if _extract_appendix_marker(child.text):
                return True
            if re.search(r"<table\b", child.text, re.IGNORECASE):
                return True
    return False


_EE_DROP_ORPHAN_APPENDIX_MARKER_RULE = "ee_drop_orphan_appendix_marker_html"
_EE_DROP_REPEALED_RANGE_RESIDUE_RULE = "ee_drop_repealed_range_residue"


def _element_has_kehtetu_marker(el: ET.Element, ns_str: str) -> bool:
    for mm in el.findall(_ns(ns_str, "muutmismarge")):
        for text_el in mm.findall(_ns(ns_str, "tavatekst")):
            marker = " ".join(str(part) for part in text_el.itertext()).strip().lower()
            if marker.startswith(("kehtetu", "kehtetud")):
                return True
    return False


def _is_repealed_range_residue_text(text: str) -> bool:
    cleaned = _ee_normalize_text_replace_surface(text).strip()
    if not cleaned:
        return False
    return bool(
        re.fullmatch(r"[–‒-]+\s*\(?\d+\)?", cleaned)
        or re.fullmatch(r"§-d\s+\d[\d_]*\s*[–‒-]\s*\d[\d_]*", cleaned)
    )


def _sisuTekst_text_with_appendix_markers(
    st: ET.Element,
    ns_str: str,
    *,
    drop_first_appendix_marker: bool = False,
) -> str:
    """Extract sisuTekst text while preserving simple appendix markers from HTML."""
    parts: list[str] = []
    first_marker_dropped = False
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "tavatekst":
            txt = _tavatekst_text(child, ns_str)
            if txt:
                parts.append(txt)
        elif local == "viide":
            kvt = child.find(_ns(ns_str, "kuvatavTekst"))
            if kvt is not None and kvt.text:
                parts.append(kvt.text.replace("\xa0", " ").strip())
        elif local == "HTMLKonteiner" and child.text:
            marker = _extract_appendix_marker(child.text)
            if marker:
                if drop_first_appendix_marker and not first_marker_dropped:
                    first_marker_dropped = True
                    continue
                parts.append(marker)
                continue
            html_txt = _appendix_html_payload_text(child.text)
            if html_txt:
                parts.append(html_txt)
    result = " ".join(p for p in parts if p)
    result = re.sub(r" +([.,;:!?])", r"\1", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _collect_text(el: ET.Element, ns_str: str) -> str:
    """Concatenate all tavatekst descendants of el.

    Normalizes \xa0 (non-breaking space) to regular space for consistency
    with amendment payload text which goes through HTML unescape + normalize.
    """
    parts = []
    for t in el.iter(_ns(ns_str, "tavatekst")):
        txt = _tavatekst_text(t, ns_str)
        if txt:
            parts.append(txt)
    return " ".join(parts)


def _parse_item(el: ET.Element, ns_str: str) -> IRNode:
    """Parse an alampunkt (item) element → IRNode(kind=IRNodeKind.ITEM)."""
    nr = (
        _extract_superscript_label(el, ns_str)
        or _text(_find(el, ns_str, "alampunktNr"))
        or _text(_find(el, ns_str, "kuvatavNr"))
    )
    # Normalize kuvatavNr like "1)" → "1"
    nr = re.sub(r"[^\w_¹²³⁴⁵⁶⁷⁸⁹⁰]", "", nr) if nr else ""

    # Gather direct text from sisuTekst (not from nested alampunkt).
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    text_parts = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        txt = _sisuTekst_text(st, ns_str)
        if txt:
            text_parts.append(txt)
    item_text = " ".join(text_parts)
    if _element_has_kehtetu_marker(el, ns_str) and _is_repealed_range_residue_text(item_text):
        return IRNode(
            kind=IRNodeKind.ITEM,
            label="",
            text="",
            attrs={
                "source_cleanup_rule": _EE_DROP_REPEALED_RANGE_RESIDUE_RULE,
                "dropped_repealed_residue": item_text,
            },
        )

    # Sub-items (alampunkt nested inside alampunkt — rare)
    children = [_parse_item(sub, ns_str) for sub in el.findall(_ns(ns_str, "alampunkt"))]
    return IRNode(kind=IRNodeKind.ITEM, label=nr, text=item_text, children=tuple(children))


def _extract_reavahetus_items(el: ET.Element, ns_str: str) -> List[IRNode]:
    """Extract list items from <tavatekst> elements whose items are separated
    by <reavahetus/> line-break elements (old tyviseadus format).

    Pattern (tavatekst-only):
        <tavatekst>Intro:<reavahetus/>1) item one;<reavahetus/>2) item two.</tavatekst>

    Pattern (with sibling viide for last item):
        <sisuTekst>
          <tavatekst>Intro:<reavahetus/>1) one;<reavahetus/>3) </tavatekst>
          <viide><kuvatavTekst>linked text</kuvatavTekst></viide>
          <tavatekst>.</tavatekst>
        </sisuTekst>

    Processes items from <reavahetus/> tails (INSIDE tavatekst) and continues
    collecting into the current item from sibling viide/tavatekst nodes in the
    SAME sisuTekst (for items that span the tavatekst boundary).

    Returns an empty list if no <reavahetus/> children with numbered-item tails
    are found (i.e., the subsection uses <alampunkt> XML items, handled elsewhere).
    """
    items: List[IRNode] = []
    _ITEM_RE = re.compile(r"^(\d[\d\s]*)\)\s*(.*)", re.DOTALL)

    for st in el.findall(_ns(ns_str, "sisuTekst")):
        cur_label: Optional[str] = None
        cur_parts: List[str] = []
        in_items = False  # True once we've seen the first numbered reavahetus item

        for child in st:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag

            if local == "tavatekst":
                # Process reavahetus children inside this tavatekst
                for t_child in child:
                    t_local = t_child.tag.split("}")[1] if "}" in t_child.tag else t_child.tag
                    if t_local == "reavahetus":
                        in_items = True
                        tail = (t_child.tail or "").replace("\xa0", " ").strip()
                        if tail:
                            m = _ITEM_RE.match(tail)
                            if m:
                                if cur_label is not None:
                                    items.append(
                                        IRNode(
                                            kind=IRNodeKind.ITEM,
                                            label=cur_label,
                                            text=" ".join(p for p in cur_parts if p).strip(),
                                        )
                                    )
                                cur_label = re.sub(r"\s+", "_", m.group(1).strip()).rstrip("_")
                                cur_parts = [m.group(2).strip()] if m.group(2).strip() else []
                            elif cur_label is not None:
                                cur_parts.append(tail)
                    elif in_items and cur_label is not None and t_local in _INLINE_TAGS:
                        txt = "".join(str(_t) for _t in t_child.itertext()).replace("\xa0", " ").strip()
                        if txt:
                            cur_parts.append(txt)
                        if t_child.tail:
                            cur_parts.append(t_child.tail.replace("\xa0", " ").strip())
                # A standalone <tavatekst> with no reavahetus (e.g. "." after a viide)
                # contributes its text to the current item if we're in item context
                if (
                    in_items
                    and cur_label is not None
                    and not any((tc.tag.split("}")[1] if "}" in tc.tag else tc.tag) == "reavahetus" for tc in child)
                    and child.text
                ):
                    txt = child.text.replace("\xa0", " ").strip()
                    if txt:
                        cur_parts.append(txt)

            elif local == "viide" and in_items and cur_label is not None:
                # Sibling <viide> element contributing to current item
                kvt = child.find(_ns(ns_str, "kuvatavTekst"))
                if kvt is not None and kvt.text:
                    cur_parts.append(kvt.text.replace("\xa0", " ").strip())
                if child.tail:
                    cur_parts.append(child.tail.replace("\xa0", " ").strip())

        if cur_label is not None:
            items.append(
                IRNode(kind=IRNodeKind.ITEM, label=cur_label, text=" ".join(p for p in cur_parts if p).strip())
            )

    return items


def _parse_subsection(el: ET.Element, ns_str: str, default_nr: int = 1) -> IRNode:
    """Parse a loige (subsection) element → IRNode(kind=IRNodeKind.SUBSECTION)."""
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "loigeNr")) or str(default_nr)

    # Direct intro text (sisuTekst at this level, not under alampunkt).
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    text_parts = []
    use_appendix_html = _subsection_uses_appendix_html(el, ns_str)
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        txt = _sisuTekst_text_with_appendix_markers(st, ns_str) if use_appendix_html else _sisuTekst_text(st, ns_str)
        if txt:
            text_parts.append(txt)
    sub_text = " ".join(text_parts)
    if _element_has_kehtetu_marker(el, ns_str) and _is_repealed_range_residue_text(sub_text):
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="",
            text="",
            attrs={
                "source_cleanup_rule": _EE_DROP_REPEALED_RANGE_RESIDUE_RULE,
                "dropped_repealed_residue": sub_text,
            },
        )

    # Prefer explicit <alampunkt> XML items; fall back to <reavahetus>-separated items.
    parsed_children = [_parse_item(item_el, ns_str) for item_el in el.findall(_ns(ns_str, "alampunkt"))]
    dropped_repealed_residues = [
        str(child.attrs["dropped_repealed_residue"])
        for child in parsed_children
        if child.attrs.get("source_cleanup_rule") == _EE_DROP_REPEALED_RANGE_RESIDUE_RULE
    ]
    children = [child for child in parsed_children if child.label or child.text or child.children]
    if not children:
        # Old tyviseadus format: items encoded as "N) text" in <tavatekst>
        # tails of <reavahetus> elements rather than as <alampunkt> XML nodes.
        children = _extract_reavahetus_items(el, ns_str)
        if children:
            # Rebuild sub_text as intro-only: the tavatekst.text before the first
            # <reavahetus/> separator, not the full _sisuTekst_text which also
            # captures sibling <viide> content that belongs to reavahetus items.
            intro_parts = []
            for st in el.findall(_ns(ns_str, "sisuTekst")):
                for t in st.findall(_ns(ns_str, "tavatekst")):
                    has_reavahetus = any((c.tag.split("}")[1] if "}" in c.tag else c.tag) == "reavahetus" for c in t)
                    if has_reavahetus and t.text:
                        intro_parts.append(t.text.replace("\xa0", " ").strip())
            if intro_parts:
                sub_text = " ".join(intro_parts)

    attrs = {}
    if dropped_repealed_residues:
        attrs["source_cleanup_rules"] = (_EE_DROP_REPEALED_RANGE_RESIDUE_RULE,)
        attrs["dropped_repealed_residues"] = tuple(dropped_repealed_residues)

    return IRNode(kind=IRNodeKind.SUBSECTION, label=nr, text=sub_text, attrs=attrs, children=tuple(children))


def _parse_subsection_nodes(el: ET.Element, ns_str: str, default_nr: int = 1) -> List[IRNode]:
    """Parse one loige into one or more subsection nodes.

    Old EE source sometimes embeds appendix material inside a single subsection:
    ordinary subsection text first, then a later sisuTekst whose leading
    HTMLKonteiner is just ``Lisa N`` and whose remaining text is the appendix
    body. RT consolidated oracles materialize that as follow-on subsections.
    """
    base = _parse_subsection(el, ns_str, default_nr=default_nr)
    base_num = _try_parse_int(base.label) if base.label is not None else None
    if base_num is None:
        return [base]

    sisu_blocks = el.findall(_ns(ns_str, "sisuTekst"))
    appendix_start: Optional[int] = None
    appendix_marker = ""
    for idx, st in enumerate(sisu_blocks[1:], start=1):
        marker = _leading_appendix_marker(st)
        if marker:
            appendix_start = idx
            appendix_marker = marker
            break
    if appendix_start is None or not appendix_marker:
        return [base]

    intro_parts = [_sisuTekst_text(st, ns_str) for st in sisu_blocks[:appendix_start] if _sisuTekst_text(st, ns_str)]
    appendix_parts: list[str] = []
    for rel_idx, st in enumerate(sisu_blocks[appendix_start:]):
        txt = _sisuTekst_text_with_appendix_markers(
            st,
            ns_str,
            drop_first_appendix_marker=(rel_idx == 0),
        )
        if txt:
            appendix_parts.append(txt)
    appendix_text = " ".join(appendix_parts).strip()

    intro_text = " ".join(intro_parts).strip() or base.text
    if not appendix_text:
        return [
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=base.label,
                text=intro_text,
                attrs={
                    **dict(base.attrs),
                    "source_cleanup_rule": _EE_DROP_ORPHAN_APPENDIX_MARKER_RULE,
                    "dropped_appendix_marker": appendix_marker,
                },
                children=tuple(base.children),
            ),
        ]
    return [
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=base.label,
            text=intro_text,
            attrs=dict(base.attrs),
            children=tuple(base.children),
        ),
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(base_num + 1), text=appendix_marker),
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(base_num + 2), text=appendix_text),
    ]


def _extract_superscript_label(el: ET.Element, ns_str: str) -> Optional[str]:
    """Extract label from kuvatavNr, handling <sup> superscript suffixes.

    Works for sections, subsections, and items.
    kuvatavNr patterns:
        "§ 1<sup>2</sup>."   → "1_2"  (section)
        "(3<sup>1</sup>)"    → "3_1"  (subsection)
        "1<sup>2</sup>)"     → "1_2"  (item)
    If no superscript is present, returns None (caller should use regular Nr).
    """
    knr = _find(el, ns_str, "kuvatavNr")
    if knr is None:
        return None
    # kuvatavNr is inside CDATA — get the raw text content
    raw = knr.text or ""
    if "<sup>" not in raw:
        return None
    # Extract: "PREFIX N<sup>M</sup> SUFFIX" → "N_M"
    m = re.search(r"(\d+)\s*<sup>(\d+)</sup>", raw)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return None


def _build_phantom_set(sisu_el: ET.Element, ns_str: str) -> "set[ET.Element]":
    """Return the set of paragrahv elements that are phantom placeholder sections.

    A paragrahv is a phantom placeholder iff:
    1. Its kuvatavNr is blank (empty or whitespace-only).
    2. It has no loige children and no non-empty sisuTekst.
    3. At least one OTHER paragrahv with the same paragrahvNr text has a
       non-blank kuvatavNr — i.e. the real section exists alongside it.

    Condition 3 prevents us from dropping genuine empty sections that happen
    to lack a kuvatavNr (e.g. base sections that receive content in later
    amendments).  RT only generates duplicate-nr placeholders when the statute
    has been renumbered and the old slot must be preserved for display.

    Returns a set of lxml element objects (strong references) so callers can
    test `child_el in phantom_set` using lxml element identity (not Python id()).
    """
    # First pass: collect all nr → list[el] and note which have visible nr
    nr_to_els: dict = {}
    for para in sisu_el.iter(_ns(ns_str, "paragrahv")):
        nr_el = para.find(_ns(ns_str, "paragrahvNr"))
        nr = (nr_el.text or "").strip() if nr_el is not None else ""
        if not nr:
            continue
        nr_to_els.setdefault(nr, []).append(para)

    phantoms: list = []
    for nr, els in nr_to_els.items():
        if len(els) < 2:
            continue  # only one element with this nr — keep it regardless
        # Multiple elements share this nr.  Mark blank-kuvatavNr + empty ones.
        has_real = any(
            ((e.find(_ns(ns_str, "kuvatavNr")) is not None) and ((e.find(_ns(ns_str, "kuvatavNr")).text or "").strip()))
            for e in els
        )
        if not has_real:
            continue  # all are blank-nr — keep them all (ambiguous)
        for e in els:
            kn = e.find(_ns(ns_str, "kuvatavNr"))
            if kn is None or not (kn.text or "").strip():
                # Check it's also empty (no loige, no content)
                if e.findall(_ns(ns_str, "loige")):
                    continue  # has content — keep
                has_content = False
                for st in e.findall(_ns(ns_str, "sisuTekst")):
                    for tt in st.iter():
                        if tt.text and tt.text.strip():
                            has_content = True
                            break
                if not has_content:
                    phantoms.append(e)

    # Return as a list-backed set using lxml element identity (el == other_el
    # is True for the same XML node, even across separate Python wrapper objects).
    # We keep strong references so the wrappers aren't GC'd before the caller
    # finishes its structural parse.
    return set(phantoms)


def _parse_section(el: ET.Element, ns_str: str) -> IRNode:
    """Parse a paragrahv (section §) element → IRNode(kind=IRNodeKind.SECTION)."""
    # Use kuvatavNr with <sup> suffix when available (paragrahvNr loses
    # superscript, causing label collisions: §1, §1¹, §1² all become "1").
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "paragrahvNr"))
    title = _title_text(_find(el, ns_str, "paragrahvPealkiri"))
    # "Paragrahvi pealkiri" is an RT placeholder meaning "no title assigned yet".
    # Oracle tervikteksts use an empty element; base tyviseadus may have this literal.
    if title == "Paragrahvi pealkiri":
        title = ""

    children: List[IRNode] = []
    dropped_repealed_residues: list[str] = []
    loige_els = el.findall(_ns(ns_str, "loige"))
    for i, loige_el in enumerate(loige_els, start=1):
        for node in _parse_subsection_nodes(loige_el, ns_str, default_nr=i):
            if node.label or node.text or node.children:
                children.append(node)
            elif node.attrs.get("source_cleanup_rule") == _EE_DROP_REPEALED_RANGE_RESIDUE_RULE:
                dropped_repealed_residues.append(str(node.attrs["dropped_repealed_residue"]))

    # Section with no loige children — capture sisuTekst directly as single subsection.
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    if not children:
        text_parts = []
        for st in el.findall(_ns(ns_str, "sisuTekst")):
            txt = _sisuTekst_text(st, ns_str)
            if txt:
                text_parts.append(txt)
        if text_parts:
            children.append(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=" ".join(text_parts)))

    # Detect already-repealed sections: muutmismarge says "Kehtetu" and there is
    # no body content.  RT tervikteksts preserve the original title of such sections
    # without applying subsequent global text-replacements to it.  We mark these
    # with attrs={'kehtetu': True} so _ee_global_text_replace can skip their title.
    attrs: dict = {}
    if dropped_repealed_residues:
        attrs["source_cleanup_rules"] = (_EE_DROP_REPEALED_RANGE_RESIDUE_RULE,)
        attrs["dropped_repealed_residues"] = tuple(dropped_repealed_residues)
    if not children:
        mm = el.find(_ns(ns_str, "muutmismarge"))
        if mm is not None:
            tt = mm.find(_ns(ns_str, "tavatekst"))
            if tt is not None and (tt.text or "").strip().startswith("Kehtetu"):
                attrs["kehtetu"] = True

    return IRNode(kind=IRNodeKind.SECTION, label=nr, text=title, attrs=attrs, children=tuple(children))


def _parse_division(el: ET.Element, ns_str: str, phantoms: AbstractSet = frozenset()) -> IRNode:
    """Parse a jagu (division) element → IRNode(kind=IRNodeKind.DIVISION)."""
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "jaguNr"))
    title = _text(_find(el, ns_str, "jaguPealkiri"))
    children: List[IRNode] = []

    def _append_section(para_el: ET.Element, *, jaotis_label: str = "", alljaotis_label: str = "") -> None:
        if para_el in phantoms:
            return
        section = _parse_section(para_el, ns_str)
        attrs = dict(section.attrs)
        if jaotis_label:
            attrs["jaotis"] = _normalize_num(jaotis_label)
        if alljaotis_label:
            attrs["alljaotis"] = _normalize_num(alljaotis_label)
        if attrs != section.attrs:
            section = replace(section, attrs=attrs)
        children.append(section)

    for child in el:
        local_tag = child.tag.split("}")[-1]
        if local_tag == "paragrahv":
            _append_section(child)
        elif local_tag == "jaotis":
            # EE jaotis sits below jagu, but the current shared IR has no
            # dedicated subdivision layer. Flatten jaotis-contained sections
            # under the parent division, preserving document order and the
            # section labels/titles that the oracle exposes (e.g. § 97^1, § 97^2).
            jaotis_label = _extract_superscript_label(child, ns_str) or _text(_find(child, ns_str, "jaotisNr"))
            for para_el in child.findall(_ns(ns_str, "paragrahv")):
                _append_section(para_el, jaotis_label=jaotis_label)
            for alljaotis_el in child.findall(_ns(ns_str, "alljaotis")):
                alljaotis_label = _extract_superscript_label(alljaotis_el, ns_str) or _text(
                    _find(alljaotis_el, ns_str, "alljaotisNr")
                )
                for para_el in alljaotis_el.findall(_ns(ns_str, "paragrahv")):
                    _append_section(
                        para_el,
                        jaotis_label=jaotis_label,
                        alljaotis_label=alljaotis_label,
                    )
    return IRNode(kind=IRNodeKind.DIVISION, label=nr, text=title, children=tuple(children))


def _parse_chapter(el: ET.Element, ns_str: str, phantoms: AbstractSet = frozenset()) -> IRNode:
    """Parse a peatykk (chapter) element → IRNode(kind=IRNodeKind.CHAPTER)."""
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "peatykkNr"))
    title = _title_text(_find(el, ns_str, "peatykkPealkiri"))

    children: List[IRNode] = []
    for child in el:
        local_tag = child.tag.split("}")[-1]
        if local_tag == "jagu":
            children.append(_parse_division(child, ns_str, phantoms))
        elif local_tag == "paragrahv":
            if child not in phantoms:
                children.append(_parse_section(child, ns_str))
        # Skip peatykkNr, peatykkPealkiri, kuvatavNr (metadata, not structure)

    return IRNode(kind=IRNodeKind.CHAPTER, label=nr, text=title, children=tuple(children))


_RANGE_LABEL_RE = re.compile(r"^(\d+)–(\d+)$")


def _expand_range_sections(children: List[IRNode]) -> List[IRNode]:
    """Expand range-label sections (e.g. section:3–4) into individual sections.

    Old tyviseadus base statutes sometimes encode repealed section ranges as a
    single element with label "3–4" (en-dash separated). The oracle terviktekst
    splits them into individual sections §3, §4 with the same content. Expanding
    here ensures the replay output matches the oracle structure.

    Only operates on top-level section nodes; chapter/division children are
    handled recursively via _parse_chapter.
    """
    result: List[IRNode] = []
    for node in children:
        if node.kind == IRNodeKind.SECTION and node.label is not None:
            m = _RANGE_LABEL_RE.match(node.label)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                if 0 < (end - start) <= 20:  # sanity: expand only reasonable ranges
                    for n in range(start, end + 1):
                        result.append(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label=str(n),
                                text=node.text,
                                children=tuple(node.children),
                                attrs=dict(node.attrs),
                            )
                        )
                    continue
        result.append(node)
    return result


def _detect_ns(root: ET.Element) -> str:
    """Detect namespace from root element tag. Handles tyviseadus, maarus, juurakt."""
    if "}" in root.tag:
        return root.tag.split("}")[0].lstrip("{")
    return NS_BASE


def parse_ee_statute(xml_bytes: bytes, statute_id: str = "") -> IRStatute:
    """Parse a tyviseadus or maarus XML document → IRStatute.

    Handles tyviseadus_1_10.02.2010, maarus_1_10.02.2010, and juurakt_1_10.02.2010
    schemas — all share the same structural elements (peatykk, paragrahv, loige).

    statute_id: e.g. "ee/104012019011". If empty, extracted from globaalID metadata.
    """
    root = ET.fromstring(xml_bytes)
    ns_str = _detect_ns(root)

    # Statute ID from metadata if not provided
    if not statute_id:
        gid = root.find(f".//{_ns(ns_str, 'globaalID')}")
        statute_id = f"ee/{_text(gid)}" if gid is not None else "ee/unknown"

    # Title from aktinimi/nimi/pealkiri
    title = ""
    aktinimi = root.find(_ns(ns_str, "aktinimi"))
    if aktinimi is not None:
        nimi = aktinimi.find(_ns(ns_str, "nimi"))
        if nimi is not None:
            pealkiri = nimi.find(_ns(ns_str, "pealkiri"))
            title = _text(pealkiri)

    # Body: sisu → peatykk (chapters), osa (parts), or flat paragrahv (decrees)
    sisu = root.find(_ns(ns_str, "sisu"))
    body_children: List[IRNode] = []
    if sisu is not None:
        # Pre-scan: identify phantom placeholder sections (empty duplicate-nr rows).
        # Must be done before structural parsing so _parse_chapter/_parse_division
        # can skip them consistently.
        phantoms = _build_phantom_set(sisu, ns_str)

        for child in sisu:
            local_tag = child.tag.split("}")[-1]
            if local_tag == "osa":
                # Part level (above chapter) — rare
                osa_nr = _text(_find(child, ns_str, "osaNr"))
                osa_title = _text(_find(child, ns_str, "osaPealkiri"))
                part_children = [
                    _parse_chapter(peat_el, ns_str, phantoms) for peat_el in child.findall(_ns(ns_str, "peatykk"))
                ]
                body_children.append(
                    IRNode(kind=IRNodeKind.PART, label=osa_nr, text=osa_title, children=tuple(part_children))
                )
            elif local_tag == "peatykk":
                body_children.append(_parse_chapter(child, ns_str, phantoms))
            elif local_tag == "paragrahv":
                # Flat body (common in decrees): paragrahv directly under sisu
                if child not in phantoms:
                    body_children.append(_parse_section(child, ns_str))

    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(_expand_range_sections(body_children)))

    # Metadata
    meta_el = root.find(_ns(ns_str, "metaandmed"))
    metadata: dict = {}
    if meta_el is not None:
        for tag_name in ("lyhend", "dokumentLiik", "tekstiliik", "metaandmedVersioon"):
            el = meta_el.find(_ns(ns_str, tag_name))
            if el is not None and el.text:
                metadata[tag_name] = el.text.strip()
    metadata["schema"] = ns_str

    return IRStatute(
        statute_id=statute_id,
        title=title,
        body=body,
        supplements=[],
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Amendment act parser (muutmisseadus)
# ---------------------------------------------------------------------------


def _paragrahv_to_act_id(title: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.target_resolution``."""
    return _tr_paragrahv_to_act_id(title)


def parse_ee_amendment_ops(
    xml_bytes: bytes,
    source_id: str = "",
    target_title: str = "",
    ref_effective: str = "",
    has_earlier_same_act_slice: bool = False,
) -> List[LegalOperation]:
    """Parse an amendment XML document → List[LegalOperation].

    Handles two schemas:
    - muutmisseadus_1_10.02.2010: post-~2009 format, one paragrahv per target act.
    - tyviseadus_1_10.02.2010 with CDATA HTML body: pre-2009 format, multiple
      acts amended in one document, each as ``§ N. [Act name] (RT ref)`` section.

    source_id:    Amending statute's canonical ID, e.g. "ee/261378".
    target_title: Optional title of the target (base) statute for old-format
                  disambiguation, e.g. "Kohtute seadus".  Without this, old-
                  format acts will produce ops for ALL base acts mentioned.
    """
    root = ET.fromstring(xml_bytes)

    # Detect schema from root namespace
    root_ns = ""
    if "}" in root.tag:
        root_ns = root.tag.split("}")[0].lstrip("{")
    elif root.get("xmlns"):
        root_ns = root.get("xmlns", "")
    else:
        # Check xmlns attribute in raw XML
        import re as _re

        m = _re.search(rb'xmlns\s*=\s*["\']([^"\']+)["\']', xml_bytes[:500])
        if m:
            root_ns = m.group(1).decode("utf-8", errors="replace")

    if not source_id:
        # Try root namespace first, then both base schemas
        for ns in (root_ns, NS_BASE, NS_AMEND):
            if not ns:
                continue
            gid = root.find(f".//{_ns(ns, 'globaalID')}")
            if gid is not None and gid.text:
                source_id = f"ee/{gid.text.strip()}"
                break
        if not source_id:
            source_id = "ee/unknown"

    generic_minister_ops = _parse_generic_minister_rename_ops(
        xml_bytes,
        source_id=source_id,
        target_title=target_title,
    )
    generic_ministry_ops = _parse_generic_ministry_reorganization_ops(
        xml_bytes,
        source_id=source_id,
        target_title=target_title,
    )
    constitutional_review_ops = _parse_constitutional_review_ops(
        xml_bytes,
        source_id=source_id,
        target_title=target_title,
    )

    def _op_merge_key(op: LegalOperation) -> tuple[object, ...]:
        payload_text = op.payload.text if op.payload is not None else None

        def _freeze(value: object) -> object:
            if isinstance(value, list):
                return tuple(_freeze(item) for item in value)
            if isinstance(value, tuple):
                return tuple(_freeze(item) for item in value)
            if isinstance(value, dict):
                return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
            return value

        payload_attrs = (
            tuple(sorted((str(k), _freeze(v)) for k, v in op.payload.attrs.items())) if op.payload is not None else ()
        )
        return (
            op.action,
            op.target.path,
            (op.target.path,),
            op.anchor.path if op.anchor is not None else None,
            op.destination.path if op.destination is not None else None,
            payload_text,
            payload_attrs,
            *_text_merge_signature(op),
        )

    def _augment_global_text_replace_exclusions(ops: List[LegalOperation]) -> None:
        global_text_ops = [
            op
            for op in ops
            if op.action == "text_replace"
            and not op.target.path
            and op.payload is not None
            and read_payload_rewrite_meta(op.payload).rewrite is not None
        ]
        if not global_text_ops:
            return
        for global_op in global_text_ops:
            payload = global_op.payload
            if payload is None:
                continue
            payload_meta = read_payload_rewrite_meta(payload)
            rewrite = payload_meta.rewrite
            if rewrite is None:
                continue
            old_text = rewrite.old_surface.strip()
            if not old_text:
                continue
            excluded_paths = [
                tuple((str(kind), str(label)) for kind, label in raw_path)
                for raw_path in rewrite.exclude_paths
                if isinstance(raw_path, (list, tuple))
            ]
            seen_paths = set(excluded_paths)
            for op in ops:
                if op is global_op or op.action != "text_replace" or not op.target.path or op.payload is None:
                    continue
                other_meta = read_payload_rewrite_meta(op.payload)
                other_rewrite = other_meta.rewrite
                other_old_text = other_rewrite.old_surface.strip() if other_rewrite is not None else ""
                if not other_old_text or len(other_old_text) <= len(old_text):
                    continue
                if old_text not in other_old_text:
                    continue
                path = tuple((str(kind), str(label)) for kind, label in op.target.path)
                if path and path not in seen_paths:
                    excluded_paths.append(path)
                    seen_paths.add(path)
            if excluded_paths:
                updated_op = replace(
                    global_op,
                    payload=replace(
                        payload,
                        attrs={
                            **payload.attrs,
                            "exclude_paths": excluded_paths,
                        },
                    ),
                )
                for idx, op in enumerate(ops):
                    if op is global_op:
                        ops[idx] = updated_op
                        break

    def _merge_frontloaded_ops(
        leading_ops: List[LegalOperation],
        parsed_ops: List[LegalOperation],
    ) -> List[LegalOperation]:
        combined: List[LegalOperation] = []
        seen: set[tuple[object, ...]] = set()
        for op in [*leading_ops, *parsed_ops]:
            key = _op_merge_key(op)
            if key in seen:
                continue
            seen.add(key)
            combined.append(op)
        return [replace(op, sequence=seq) for seq, op in enumerate(combined, start=1)]

    # Detect format by content: muutmisseadus uses paragrahv-per-target-act,
    # everything else (old tyviseadus, muutmismaarus, maarus) uses flat HTMLKonteiner
    # NOTE: deep search via .iter() — chapter-nested paragrahvs (sisu>peatykk>paragrahv)
    # were missed by the original shallow sisu-direct-children check.
    has_paragrahv = any(
        (el.tag.split("}")[-1] if "}" in el.tag else el.tag) == "paragrahv"
        for sisu in root.iter()
        if (sisu.tag.split("}")[-1] if "}" in sisu.tag else sisu.tag) == "sisu"
        for el in sisu.iter()
    )

    parsed_ops = (
        constitutional_review_ops or _parse_muutmisseadus_ops(root, source_id, root_ns, target_title=target_title)
        if has_paragrahv
        else constitutional_review_ops
        or _parse_preambul_single_target_ops(root, source_id, root_ns, target_title)
        or _parse_old_format_amendment_ops(
            root,
            source_id,
            target_title,
            ref_effective=ref_effective,
            has_earlier_same_act_slice=has_earlier_same_act_slice,
        )
    )
    if not parsed_ops and has_paragrahv and target_title:
        html_blocks: list[str] = []
        for el in root.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "HTMLKonteiner" and el.text:
                html_blocks.append(el.text)
        if html_blocks:
            parsed_ops, _ = _tr_old_format_collect_nested_direct_target_ops(
                full_html="\n".join(html_blocks),
                source_id=source_id,
                target_title=target_title,
                seq_start=1,
            )
    target_section_labels = _extract_old_format_target_section_labels(root, target_title)
    item_effects, section_effects = _extract_old_format_commencement_effects(
        root,
        fallback_effective=ref_effective,
    )
    has_old_format_provenance = any(
        any(tag.startswith("old_format_amendment_section:") for tag in op.provenance_tags)
        for op in parsed_ops
    )
    if (
        has_old_format_provenance
        and has_earlier_same_act_slice
        and not _old_format_target_has_ref_owned_slice(
            target_section_labels=target_section_labels,
            item_effects=item_effects,
            section_effects=section_effects,
            ref_effective=ref_effective,
        )
    ):
        parsed_ops = []
    parsed_ops = _apply_old_format_commencement_effects(
        root,
        parsed_ops,
        target_section_labels=target_section_labels,
        fallback_effective=ref_effective,
    )
    if has_old_format_provenance:
        parsed_ops = _apply_new_format_default_slice_effects(
            root,
            parsed_ops,
            fallback_effective=ref_effective,
        )
    _augment_global_text_replace_exclusions(parsed_ops)
    leading_ops = [*generic_minister_ops, *generic_ministry_ops]
    if leading_ops:
        return _merge_frontloaded_ops(leading_ops, parsed_ops)
    return parsed_ops


def _parse_constitutional_review_ops(
    xml_bytes: bytes,
    *,
    source_id: str,
    target_title: str,
) -> List[LegalOperation]:
    """Handle Riigikohus constitutional-review judgments that invalidate provisions."""
    return _tr_parse_constitutional_review_ops(
        xml_bytes,
        source_id=source_id,
        target_title=target_title,
        lookup_act_identity=lookup_ee_act_identity,
        title_matcher=_title_matches_para,
        normalize_num=_normalize_num,
        extract_ops=extract_ee_ops,
    )


def _parse_preambul_single_target_ops(
    root: ET.Element,
    source_id: str,
    ns_str: str,
    target_title: str,
) -> List[LegalOperation]:
    """Handle single-target amendment acts expressed as preambul + one content block."""
    return _tr_parse_preambul_single_target_ops(
        root,
        source_id,
        ns_str,
        target_title,
        lookup_act_identity=lookup_ee_act_identity,
        title_matcher=_title_matches_para,
        tavatekst_text=_tavatekst_text,
        parse_muutmisseadus_ops=_parse_muutmisseadus_ops,
    )


_GENERIC_MINISTER_TITLES: tuple[str, ...] = (
    "haridus- ja teadusminister",
    "justiitsminister",
    "kaitseminister",
    "keskkonnaminister",
    "kultuuriminister",
    "majandus- ja kommunikatsiooniminister",
    "põllumajandusminister",
    "rahandusminister",
    "regionaalminister",
    "siseminister",
    "sotsiaalminister",
    "välisminister",
)

_GENERIC_MINISTRY_RENAMES: tuple[tuple[str, str], ...] = (
    ("Keskkonnaministeerium", "Kliimaministeerium"),
    ("Justiitsministeerium", "Justiits- ja Digiministeerium"),
    ("Põllumajandusministeerium", "Maaeluministeerium"),
    ("Maaeluministeerium", "Regionaal- ja Põllumajandusministeerium"),
)


def _parse_generic_minister_rename_ops(
    xml_bytes: bytes,
    *,
    source_id: str,
    target_title: str,
) -> List[LegalOperation]:
    """Materialize the 2014 all-laws minister-title substitution as global ops."""
    if not target_title:
        return []
    xml_text = xml_bytes.decode("utf-8", errors="ignore")
    xml_lower = xml_text.lower()
    if "ministrite ametinimetuste asendamine" not in xml_lower:
        return []
    if "valdkonna eest vastutav minister" not in xml_lower:
        return []

    source = OperationSource(
        statute_id=source_id,
        title="ministrite ametinimetuste asendamine",
        raw_text="ministrite ametinimetuste asendamine",
    )
    ops: List[LegalOperation] = []
    plural_payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text="valdkondade eest vastutavad ministrid",
        attrs={
            "generic_minister_plural": True,
            "old_titles": list(_GENERIC_MINISTER_TITLES),
            "persistent_postpass": True,
        },
    )
    ops.append(
        LegalOperation(
            op_id=f"ee-generic-minister-rename-plural-{source_id}",
            sequence=1,
            action=_to_structural_action("text_replace"),
            target=LegalAddress(path=()),
            payload=plural_payload,
            text_patch=_typed_text_replace_patch(
                "valdkonna eest vastutav minister",
                "valdkondade eest vastutavad ministrid",
            ),
            source=source,
            provenance_tags=("§ 107^3 ministrite ametinimetuste asendamine", "plural list collapse"),
        )
    )
    for seq, old_title in enumerate(_GENERIC_MINISTER_TITLES, start=2):
        payload = IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkonna eest vastutav minister",
            attrs={
                "old_text": old_title,
                "case_inflected": True,
                "persistent_postpass": True,
            },
        )
        ops.append(
            LegalOperation(
                op_id=f"ee-generic-minister-rename-{seq}-{source_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=()),
                payload=payload,
                text_patch=_typed_text_replace_patch(old_title, "valdkonna eest vastutav minister"),
                source=source,
                provenance_tags=("§ 107^3 ministrite ametinimetuste asendamine",),
            )
        )
    return ops


def _parse_generic_ministry_reorganization_ops(
    xml_bytes: bytes,
    *,
    source_id: str,
    target_title: str,
) -> List[LegalOperation]:
    """Materialize generic ministry reorganization acts as global text-replace ops."""
    if not target_title:
        return []
    xml_text = xml_bytes.decode("utf-8", errors="ignore")
    xml_lower = xml_text.lower()
    has_generic_reorg_heading = "ministeeriumide ja nende valitsemisalade ümberkorraldamine" in xml_lower
    has_reorg_clause = "korraldatakse ümber" in xml_lower
    has_future_law_substitution = (
        "kehtivates ja tulevikus jõustuvates õigusaktides loetakse sõna" in xml_lower
        and "asendatuks sõnadega" in xml_lower
    )
    has_name_substitution_clause = (
        "nime asendamine" in xml_lower
        and "kehtivates seadustes loetakse sõna" in xml_lower
        and ("asendatuks sõnaga" in xml_lower or "asendatuks sõnadega" in xml_lower)
    )
    if not (
        has_generic_reorg_heading or (has_reorg_clause and has_future_law_substitution) or has_name_substitution_clause
    ):
        return []

    generic_exclusions_by_old_title = _extract_generic_ministry_exclusions_by_old_title(
        xml_text,
        target_title=target_title,
    )
    source = OperationSource(
        statute_id=source_id,
        title="ministeeriumide ja nende valitsemisalade ümberkorraldamine",
        raw_text="ministeeriumide ja nende valitsemisalade ümberkorraldamine",
    )
    ops: List[LegalOperation] = []
    for seq, (old_title, new_title) in enumerate(_GENERIC_MINISTRY_RENAMES, start=1):
        if old_title.lower() not in xml_lower or new_title.lower() not in xml_lower:
            continue
        payload = IRNode(
            kind=IRNodeKind.CONTENT,
            text=new_title,
            attrs={
                "old_text": old_title,
                "case_inflected": True,
                "source_family": "generic_ministry_reorganization",
            },
        )
        excluded_paths = generic_exclusions_by_old_title.get(old_title, ())
        if excluded_paths:
            payload = replace(
                payload,
                attrs={
                    **payload.attrs,
                    "exclude_paths": excluded_paths,
                    "exclusion_rule": "ee_generic_ministry_reorganization_explicit_exceptions",
                },
            )
        ops.append(
            LegalOperation(
                op_id=f"ee-generic-ministry-reorg-{seq}-{source_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=()),
                payload=payload,
                text_patch=_typed_text_replace_patch(old_title, new_title),
                source=source,
                    provenance_tags=("§ 105^19 ministeeriumide ümberkorraldamine",),
            )
        )
    return ops


def _extract_generic_ministry_exclusions_by_old_title(
    xml_text: str,
    *,
    target_title: str,
) -> dict[str, tuple[tuple[tuple[str, str], ...], ...]]:
    """Extract target-statute exceptions from generic ministry reorganization clauses."""
    visible_text = re.sub(r"<\s*sup\s*>(.*?)<\s*/\s*sup\s*>", r" \1", xml_text, flags=re.IGNORECASE | re.DOTALL)
    visible_text = re.sub(r"<[^>]+>", " ", visible_text)
    visible_text = _html.unescape(visible_text)
    visible_text = re.sub(r"\s+", " ", visible_text)
    by_old_title: dict[str, list[tuple[tuple[str, str], ...]]] = {}
    quote = r"[„“”\"']"
    pattern = re.compile(
        rf"\bvälja\s+arvatud\s+(?P<excluded>.+?),\s*loetakse\b"
        rf"(?:(?!\bvälja\s+arvatud\b).)*?\bsõna\s+{quote}(?P<old>[^„“”\"']+){quote}\s+asendatuks\b",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(visible_text):
        old_title = match.group("old").strip()
        paths = _extract_targeted_generic_ministry_exclusion_paths(
            match.group("excluded"),
            target_title=target_title,
        )
        if not paths:
            continue
        bucket = by_old_title.setdefault(old_title, [])
        for path in paths:
            if path not in bucket:
                bucket.append(path)
    return {old: tuple(paths) for old, paths in by_old_title.items()}


def _extract_targeted_generic_ministry_exclusion_paths(
    excluded_clause: str,
    *,
    target_title: str,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Return only exception paths that name the target statute."""
    if not target_title:
        return ()
    paths: list[tuple[tuple[str, str], ...]] = []
    section_ref = re.compile(
        r"(?P<title>[A-Za-zÀ-ž0-9 .–-]+?)\s+§(?:-s|-des)?\s*"
        r"(?P<section>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)"
        r"(?:\s+(?:lõikes?|lõigetes)\s+(?P<subsections>.+?))?"
        r"(?=(?:\s*,\s*[A-Za-zÀ-ž0-9 .–-]+\s+§|\s+ja\s+\d{4}[.]|\s+ja\s+[A-Za-zÀ-ž0-9 .–-]+\s+§|$))",
        re.IGNORECASE,
    )
    for match in section_ref.finditer(excluded_clause):
        title_fragment = match.group("title").strip(" ,;")
        if not _title_matches_para(target_title, title_fragment):
            continue
        section_label = _normalize_num(match.group("section"))
        subsection_text = (match.group("subsections") or "").strip(" ,;")
        if subsection_text:
            for subsection_label in _expand_ee_numeric_list(subsection_text):
                paths.append((("section", section_label), ("subsection", subsection_label)))
        else:
            paths.append((("section", section_label),))
    deduped: list[tuple[tuple[str, str], ...]] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return tuple(deduped)


def _title_matches_para(target_title: str, para_title: str) -> bool:
    """Return True if para_title refers to the same statute as target_title.

    Handles genitive inflection: "Kohtute seaduse muutmine" → "Kohtute seadus".
    """
    return _tr_title_matches_para(target_title, para_title)


def _strict_title_match_para(target: str, para: str) -> bool:
    """Strict title match for wrapper headers that must name the same statute."""
    return _tr_strict_title_match_para(target, para)


def _looks_like_self_referential_amendment_act_para(
    target_title: str,
    para_title: str,
    first_tava: str,
) -> bool:
    """Return True when a para title only contains the target as part of a longer act title."""
    return _tr_looks_like_self_referential_amendment_act_para(
        target_title,
        para_title,
        first_tava,
        lookup_act_identity=lookup_ee_act_identity,
    )


def _matches_target_statute_header(target_title: str, para_title: str) -> bool:
    """Match statute-targeting paragraph headers without overfitting to nominative form."""
    return _tr_matches_target_statute_header(target_title, para_title)


def _para_contains_direct_target_clause(
    para: ET.Element,
    ns_str: str,
    target_title: str,
) -> bool:
    """Detect direct target-statute clauses embedded inside a non-target paragraph."""
    return _tr_para_contains_direct_target_clause(
        para,
        ns_str,
        target_title,
        lookup_act_identity=lookup_ee_act_identity,
        title_matcher=_title_matches_para,
    )


def _is_omnibus_amendment(root: ET.Element, ns_str: str, target_title: str) -> bool:
    """Return True if this amendment act contains paragrahvs targeting DIFFERENT statutes.

    An omnibus act has paragrahvs like "Kohtute seaduse muutmine", "Notariaadiseaduse
    muutmine" etc. — each one amending a different statute. We need to filter to only
    the paragrahv(s) that match target_title.
    """
    return _tr_is_omnibus_amendment(
        root,
        ns_str,
        target_title,
        lookup_act_identity=lookup_ee_act_identity,
        strict_title_matcher=_strict_title_match_para,
    )


def _parse_muutmisseadus_ops(
    root: ET.Element, source_id: str, ns_str: str = "", target_title: str = ""
) -> List[LegalOperation]:
    """Parse muutmisseadus/muutmismaarus schema amendment XML.

    When target_title is provided and this is an omnibus act (amending multiple
    statutes), only paragrahvs targeting the given statute are parsed.
    """
    ns_str = ns_str or NS_AMEND
    all_ops, _ = _tr_new_format_collect_all_ops(
        root=root,
        ns_str=ns_str,
        source_id=source_id,
        target_title=target_title,
        seq_start=1,
        prepare_new_format_gate_flags=_tr_prepare_new_format_gate_flags,
        prepare_new_format_paragraph_context=_tr_prepare_new_format_paragraph_context,
        should_admit_new_format_paragraph=_tr_should_admit_new_format_paragraph,
        new_format_collect_op_texts=_tr_new_format_collect_op_texts,
        filter_direct_target_clause_op_texts=_tr_filter_direct_target_clause_op_texts,
        new_format_lower_op_texts=_tr_new_format_lower_op_texts,
        first_tavatekst_text=_first_tavatekst_text,
        text_finder=_text,
        find_child=_find,
        is_omnibus_amendment=_is_omnibus_amendment,
        para_contains_direct_target_clause=_para_contains_direct_target_clause,
        collect_embedded_target_sections=_tr_collect_embedded_target_sections,
        normalize_act_id=_paragrahv_to_act_id,
        title_matcher=_title_matches_para,
        lookup_act_identity=lookup_ee_act_identity,
        extract_ops=extract_ee_ops,
        has_section_ref=_has_section_ref,
        section_from_ops=_section_from_ops,
    )
    return all_ops


def _parse_old_format_amendment_ops(
    root: ET.Element,
    source_id: str,
    target_title: str = "",
    ref_effective: str = "",
    has_earlier_same_act_slice: bool = False,
) -> List[LegalOperation]:
    """Parse tyviseadus-schema amendment acts (pre-~2009 format).

    These acts amend multiple statutes in a single document. The CDATA HTML
    body has the structure::

        § 1. Eesti Panga seaduses (RT I 1993, 28, 498; ...) tehakse muudatused:
        1) paragrahvi 7 täiendatakse ...

        § 2. Riigikontrolli seaduses (RT I 2002, 21, 117) tehakse ...

        § 3. Kohtute seaduses (RT I 2002, 64, 390) tehakse järgmised muudatused:
        1) paragrahvi 27 lõiget 5 täiendatakse ...

    If target_title is provided, only the section matching that act is parsed.
    Otherwise, all sections are parsed (base_act set from section header).
    """
    target_section_labels = _extract_old_format_target_section_labels(root, target_title)
    item_effects, section_effects = _extract_old_format_commencement_effects(
        root,
        fallback_effective=ref_effective,
    )

    def _parse_plaintext_old_format_sections() -> List[LegalOperation]:
        local_ns = _detect_ns(root)
        plain_blocks: list[str] = []
        for el in root.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "preambul":
                plain = " ".join(part for part in el.itertext())
                plain = plain.replace("\xa0", " ")
                plain = re.sub(r"\s+", " ", plain).strip()
                if plain:
                    plain_blocks.append(plain)
        raw_sections: list[str] = []
        if not plain_blocks:
            for para in root.iter():
                tag = para.tag.split("}")[-1] if "}" in para.tag else para.tag
                if tag != "paragrahv":
                    continue
                nr = ""
                title = ""
                content_parts: list[str] = []
                for child in para:
                    child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child_tag == "paragrahvNr":
                        nr = "".join(str(text) for text in child.itertext()).strip()
                    elif child_tag == "paragrahvPealkiri":
                        title = "".join(str(text) for text in child.itertext()).strip()
                    elif child_tag == "sisuTekst":
                        txt = _sisuTekst_text(child, "")
                        if txt:
                            content_parts.append(txt)
                if not nr or not title or not content_parts:
                    continue
                raw_sections.append(f"§ {nr}. {title} {' '.join(content_parts)}".strip())
        if not plain_blocks and not raw_sections:
            for el in root.iter():
                tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if tag != "tavatekst":
                    continue
                plain = " ".join(part for part in el.itertext())
                plain = plain.replace("\xa0", " ")
                plain = re.sub(r"\s+", " ", plain).strip()
                if "§ 1." in plain and "muutmine" in plain.lower():
                    plain_blocks.append(plain)
        if not plain_blocks and not raw_sections and target_title:
            act_title = ""
            aktinimi = root.find(_ns(local_ns, "aktinimi"))
            if aktinimi is not None:
                nimi = aktinimi.find(_ns(local_ns, "nimi"))
                if nimi is not None:
                    pealkiri = nimi.find(_ns(local_ns, "pealkiri"))
                    act_title = _text(pealkiri)
            if act_title and (
                _title_matches_para(target_title, act_title)
                or _tr_old_format_section_matches_target(target_title, act_title)
            ):
                direct_texts: list[str] = []
                sisu = root.find(_ns(local_ns, "sisu"))
                direct_children = list(sisu) if sisu is not None else []
                for child in direct_children:
                    child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child_tag != "sisuTekst":
                        continue
                    for text_el in child.iter():
                        text_tag = text_el.tag.split("}")[-1] if "}" in text_el.tag else text_el.tag
                        if text_tag != "tavatekst":
                            continue
                        plain = " ".join(part for part in text_el.itertext())
                        plain = plain.replace("\xa0", " ")
                        plain = re.sub(r"\s+", " ", plain).strip()
                        if plain:
                            direct_texts.append(plain)
                direct_text = " ".join(direct_texts).strip()
                if direct_text and (
                    any(
                        kw in direct_text.lower()
                        for kw in (
                            "paragrahvi",
                            "paragrahvist",
                            "lõiget",
                            "lõikest",
                            "lõikes",
                            "muudetakse",
                            "täiendatakse",
                            "tunnistatakse",
                            "asendatakse",
                            "jäetakse välja",
                            "lisatakse",
                        )
                    )
                    or re.search(r"§\s*\d", direct_text)
                ):
                    source = OperationSource(
                        statute_id=source_id,
                        title=target_title,
                        raw_text=direct_text[:200],
                    )
                    direct_instruction = direct_text
                    if target_title.lower() in direct_text.lower():
                        first_structural_target = re.search(r"§\s*\d", direct_text)
                        if first_structural_target is not None:
                            direct_instruction = direct_text[first_structural_target.start():].strip()
                    ops = extract_ee_ops(direct_instruction, source, seq_start=1)
                    return [
                        replace(
                            op,
                            provenance_tags=(
                                *op.provenance_tags,
                                "ee_unstructured_single_clause_amendment_body",
                                f"base_act: {_tr_paragrahv_to_act_id(act_title)}",
                            ),
                        )
                        for op in ops
                    ]
        if not plain_blocks and not raw_sections:
            return []

        if not raw_sections:
            full_plain = "\n".join(plain_blocks)
            raw_sections = [
                section.strip()
                for section in re.split(r"(?=§\s*\d+\.\s+)", full_plain)
                if section.strip()
            ]
        all_ops: list[LegalOperation] = []
        seq = 1

        for section in raw_sections:
            header_match = re.match(
                r"^(§\s*\d+\.\s*[^§]+?(?:muutmine|täiendamine|kehtetuks tunnistamine))\s+(.*)$",
                section,
                re.IGNORECASE | re.DOTALL,
            )
            if header_match is None:
                continue
            header_text = re.sub(r"\s+", " ", header_match.group(1)).strip()
            content_text = re.sub(r"\s+", " ", header_match.group(2)).strip()
            if target_title and not _tr_old_format_section_matches_target(target_title, header_text):
                continue
            if not content_text:
                continue
            source = OperationSource(statute_id=source_id, title=target_title or "")
            section_ops = extract_ee_ops(content_text, source, seq_start=seq)
            all_ops.extend(section_ops)
            seq += len(section_ops)
        if not all_ops and target_title:
            act_title = ""
            aktinimi = root.find(_ns(local_ns, "aktinimi"))
            if aktinimi is not None:
                nimi = aktinimi.find(_ns(local_ns, "nimi"))
                if nimi is not None:
                    pealkiri = nimi.find(_ns(local_ns, "pealkiri"))
                    act_title = _text(pealkiri)
            if act_title and (
                _title_matches_para(target_title, act_title)
                or _tr_old_format_section_matches_target(target_title, act_title)
            ):
                direct_texts: list[str] = []
                sisu = root.find(_ns(local_ns, "sisu"))
                direct_children = list(sisu) if sisu is not None else []
                for child in direct_children:
                    child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child_tag != "sisuTekst":
                        continue
                    for text_el in child.iter():
                        text_tag = text_el.tag.split("}")[-1] if "}" in text_el.tag else text_el.tag
                        if text_tag != "tavatekst":
                            continue
                        plain = " ".join(part for part in text_el.itertext())
                        plain = plain.replace("\xa0", " ")
                        plain = re.sub(r"\s+", " ", plain).strip()
                        if plain:
                            direct_texts.append(plain)
                direct_text = " ".join(direct_texts).strip()
                if direct_text:
                    direct_instruction = direct_text
                    if target_title.lower() in direct_text.lower():
                        first_structural_target = re.search(r"§\s*\d", direct_text)
                        if first_structural_target is not None:
                            direct_instruction = direct_text[first_structural_target.start():].strip()
                    source = OperationSource(
                        statute_id=source_id,
                        title=target_title,
                        raw_text=direct_text[:200],
                    )
                    all_ops = [
                        replace(
                            op,
                            provenance_tags=(
                                *op.provenance_tags,
                                "ee_unstructured_single_clause_amendment_body",
                                f"base_act: {_tr_paragrahv_to_act_id(act_title)}",
                            ),
                        )
                        for op in extract_ee_ops(direct_instruction, source, seq_start=seq)
                    ]
        if has_earlier_same_act_slice and not _old_format_target_has_ref_owned_slice(
            target_section_labels=target_section_labels,
            item_effects=item_effects,
            section_effects=section_effects,
            ref_effective=ref_effective,
        ):
            return []
        return _apply_old_format_commencement_effects(
            root,
            all_ops,
            target_section_labels=target_section_labels,
            fallback_effective=ref_effective,
        )

    def _parse_paragraph_scoped_old_format_html_sections() -> List[LegalOperation]:
        if not target_title:
            return []

        def _candidate_title_from_para(para: ET.Element) -> tuple[str, str, str]:
            para_nr = ""
            para_title = ""
            first_tava = ""
            for child in para:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag == "paragrahvNr":
                    para_nr = "".join(str(text) for text in child.itertext()).strip()
                elif child_tag == "paragrahvPealkiri":
                    para_title = "".join(str(text) for text in child.itertext()).strip()
                elif child_tag in {"loige", "sisuTekst"} and not first_tava:
                    for sub in child.iter():
                        sub_tag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                        if sub_tag != "tavatekst":
                            continue
                        first_tava = " ".join(str(text) for text in sub.itertext()).replace("\xa0", " ")
                        first_tava = re.sub(r"\s+", " ", first_tava).strip()
                        if first_tava:
                            break
            candidate_title = para_title
            if not candidate_title and first_tava:
                candidate_title = first_tava.split("tehakse järgmised muudatused", 1)[0].strip(" :")
            return para_nr, candidate_title, first_tava

        def _normalize_candidate(title: str) -> str:
            normalized = re.sub(r"\s+muutmine\s*$", "", title.strip(), flags=re.IGNORECASE)
            return _tr_paragrahv_to_act_id(normalized)

        normalized_target = _normalize_candidate(target_title)
        all_ops: list[LegalOperation] = []
        global_seq = 1

        for para in root.iter():
            tag = para.tag.split("}")[-1] if "}" in para.tag else para.tag
            if tag != "paragrahv":
                continue
            para_nr, candidate_title, _first_tava = _candidate_title_from_para(para)
            if not candidate_title:
                continue
            if not (
                _normalize_candidate(candidate_title) == normalized_target
                or _tr_old_format_section_matches_target(target_title, candidate_title)
            ):
                continue

            html_parts: list[str] = []
            for child in para.iter():
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag == "HTMLKonteiner" and child.text:
                    html_parts.append(child.text)
            if not html_parts:
                continue

            para_ops = _tr_old_format_collect_all_ops(
                full_html="\n".join(html_parts),
                source_id=source_id,
                target_title=target_title,
                lookup_act_identity=lookup_ee_act_identity,
                split_wrapper_blocks=_tr_split_old_format_wrapper_blocks,
            )
            if not para_ops:
                continue

            amendment_section_label = _normalize_num(para_nr) if para_nr else ""
            base_act_name = _normalize_candidate(candidate_title)
            tagged_ops: list[LegalOperation] = []
            for op in para_ops:
                tags = list(op.provenance_tags)
                if amendment_section_label:
                    tags.append(f"old_format_amendment_section:{amendment_section_label}")
                if base_act_name:
                    tags.append(f"base_act: {base_act_name}")
                tagged_ops.append(
                    replace(
                        op,
                        sequence=global_seq,
                        provenance_tags=tuple(tags),
                    )
                )
                global_seq += 1
            all_ops.extend(tagged_ops)

        if not all_ops:
            return []
        if has_earlier_same_act_slice and not _old_format_target_has_ref_owned_slice(
            target_section_labels=target_section_labels,
            item_effects=item_effects,
            section_effects=section_effects,
            ref_effective=ref_effective,
        ):
            return []
        return _apply_old_format_commencement_effects(
            root,
            all_ops,
            target_section_labels=target_section_labels,
            fallback_effective=ref_effective,
        )

    # Extract all CDATA / HTMLKonteiner blocks
    html_blocks: List[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "HTMLKonteiner" and el.text:
            html_blocks.append(el.text)

    paragraph_scoped_ops = _parse_paragraph_scoped_old_format_html_sections()
    if paragraph_scoped_ops:
        return paragraph_scoped_ops

    if not html_blocks:
        return _parse_plaintext_old_format_sections()

    full_html = "\n".join(html_blocks)
    ops = _tr_old_format_collect_all_ops(
        full_html=full_html,
        source_id=source_id,
        target_title=target_title,
        lookup_act_identity=lookup_ee_act_identity,
        split_wrapper_blocks=_tr_split_old_format_wrapper_blocks,
    )
    if has_earlier_same_act_slice and not _old_format_target_has_ref_owned_slice(
        target_section_labels=target_section_labels,
        item_effects=item_effects,
        section_effects=section_effects,
        ref_effective=ref_effective,
    ):
        return []
    return _apply_old_format_commencement_effects(
        root,
        ops,
        target_section_labels=target_section_labels,
        fallback_effective=ref_effective,
    )


def _old_format_provenance_value(op: LegalOperation, prefix: str) -> str:
    """Return the first tagged old-format provenance value with the given prefix."""
    for tag in op.provenance_tags:
        if tag.startswith(prefix):
            return tag.removeprefix(prefix)
    return ""


def _op_instruction_note_text(op: LegalOperation) -> str:
    """Return only the operative preamble text from provenance tags.

    Many Estonia ops store the full clause, including quoted replacement payload,
    in their first provenance tag. Sentence-note recovery must not read timing or
    placement cues like ``teises lauses`` out of that payload.
    """
    if not op.provenance_tags:
        return ""
    first_tag = op.provenance_tags[0]
    return _instruction_preamble(first_tag).lower()


def _op_sentence_indexes(payload: IRNode, note_text: str) -> list[int]:
    """Resolve sentence targets, preferring explicit note coverage when broader."""
    from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

    if payload.attrs.get("suppress_sentence_target_meta"):
        return []
    sentence_meta = read_sentence_target_meta(payload)
    if sentence_meta is not None:
        return list(sentence_meta.sentence_indexes)
    return _sentence_indexes_from_notes(note_text)


def _parse_commencement_item_labels(raw: str) -> tuple[str, ...]:
    """Expand old-format commencement item references like ``3 ja 6`` or ``9-12``."""
    if not raw:
        return ()
    cleaned = (
        raw.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ja ", ",")
        .replace(" ning ", ",")
    )
    labels: list[str] = []
    for chunk in cleaned.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "§" in part:
            continue
        part = re.sub(r"[.;:]+$", "", part).strip()
        if not part:
            continue
        range_match = re.fullmatch(
            r"(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*-\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)",
            part,
        )
        if range_match:
            start = int(_normalize_num(range_match.group(1)).replace("_", ""))
            end = int(_normalize_num(range_match.group(2)).replace("_", ""))
            if start <= end:
                labels.extend(str(value) for value in range(start, end + 1))
                continue
        labels.append(_normalize_num(part))
    return tuple(dict.fromkeys(labels))


def _parse_commencement_section_labels(raw: str) -> tuple[str, ...]:
    """Expand old-format commencement section references like ``1 ja 2`` or ``9-12``."""
    if not raw:
        return ()
    cleaned = re.sub(r"\b(?:ja|ning)\s*$", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"[.;:]+$", "", cleaned).strip()
    return _parse_commencement_item_labels(cleaned)


def _iter_commencement_section_label_groups(sentence: str) -> tuple[str, ...]:
    """Return section labels from each ``§``/``§-d`` group in a commencement sentence."""
    labels: list[str] = []
    for match in re.finditer(
        r"§(?:-d)?\s+(.+?)(?=(?:\s*(?:,|\bja\b|\bning\b)?\s*§(?:-d)?\s+"
        r"|\s+jõustu(?:b|vad)\b|$))",
        sentence,
        re.IGNORECASE | re.DOTALL,
    ):
        for section_label in _parse_commencement_section_labels(match.group(1)):
            labels.append(section_label)
    return tuple(dict.fromkeys(labels))


def _old_format_commencement_date(text: str) -> str:
    """Extract ``YYYY-MM-DD`` from a sentence like ``jõustub 2019. aasta 1. jaanuaril``."""
    match = re.search(
        r"(\d{4})\.\s*aasta\s+(\d{1,2})\.\s*([A-Za-zÕÄÖÜŠŽõäöüšž]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    year = match.group(1)
    day = int(match.group(2))
    month_token = match.group(3).lower()
    month_prefixes = (
        ("jaanuar", "01"),
        ("veebruar", "02"),
        ("märts", "03"),
        ("aprill", "04"),
        ("mai", "05"),
        ("juuni", "06"),
        ("juuli", "07"),
        ("august", "08"),
        ("septembr", "09"),
        ("oktoobr", "10"),
        ("novembr", "11"),
        ("detsembr", "12"),
    )
    for prefix, month in month_prefixes:
        if month_token.startswith(prefix):
            return f"{year}-{month}-{day:02d}"
    return ""


def _strip_ee_quoted_payload_spans(text: str) -> str:
    """Remove quoted payload spans before scanning a clause for operative timing."""
    stripped = text
    for pattern in (
        r"\u201e.*?(?:\u201d|\")",
        r"\u02ee.*?\u02ee",
        r"\u00ab.*?\u00bb",
        r"\".*?\"",
    ):
        stripped = re.sub(pattern, " ", stripped, flags=re.DOTALL)
    return re.sub(r"\s+", " ", stripped).strip()


def _extract_old_format_commencement_effects(
    root: ET.Element,
    *,
    fallback_effective: str = "",
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Read commencement clauses that assign dates to amendment-act sections/items."""
    item_effects: dict[tuple[str, str], str] = {}
    section_effects: dict[str, str] = {}

    def _plain_html_text(raw_html: str) -> str:
        text = _html.unescape(raw_html).replace("\xa0", " ")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _record_commencement_clause(sentence: str, whole_act_effective: str = "") -> str:
        if "jõustu" not in sentence.lower():
            return whole_act_effective
        effective = _old_format_commencement_date(sentence)
        if (
            effective
            and re.search(r"\bKäesolev\s+(?:seadus|määrus)\s+jõustub\b", sentence, re.IGNORECASE)
            and "§" not in sentence
        ):
            whole_act_effective = effective
        if not effective and "üldises korras" in sentence.lower():
            effective = whole_act_effective
        if not effective:
            effective = fallback_effective
        if not effective:
            return whole_act_effective
        item_spans: list[tuple[int, int]] = []
        for match in re.finditer(
            r"§\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+"
            r"punkt(?:id|i)?\s+(.+?)"
            r"(?=(?:\s+(?:ning|ja)\s+§|\s*,\s*§|\s+jõustu(?:b|vad)\b|$))",
            sentence,
            re.IGNORECASE | re.DOTALL,
        ):
            section_label = _normalize_num(match.group(1))
            item_labels = _parse_commencement_item_labels(match.group(2))
            for item_label in item_labels:
                item_effects[(section_label, item_label)] = effective
            item_spans.append(match.span())
        section_sentence = sentence
        for start, end in reversed(item_spans):
            section_sentence = section_sentence[:start] + section_sentence[end:]
        for section_label in _iter_commencement_section_label_groups(section_sentence):
            section_effects[section_label] = effective
        return whole_act_effective

    def _whole_act_effective_from_clauses(clauses: list[str]) -> str:
        for sentence in clauses:
            explicit_date = _old_format_commencement_date(sentence)
            if (
                explicit_date
                and re.search(r"\bKäesolev\s+(?:seadus|määrus)\s+jõustub\b", sentence, re.IGNORECASE)
                and "§" not in sentence
            ):
                return explicit_date
        return ""

    saw_structured_commencement = False
    for para in root.iter():
        tag = para.tag.split("}")[-1] if "}" in para.tag else para.tag
        if tag != "paragrahv":
            continue
        title = ""
        for child in para:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "paragrahvPealkiri":
                title = "".join(str(text) for text in child.itertext()).strip()
                break
        para_text = " ".join(text.strip() for text in para.itertext() if text and text.strip())
        para_text = _html.unescape(para_text).replace("\xa0", " ")
        para_text = re.sub(r"<[^>]+>", " ", para_text)
        para_text = re.sub(r"\s+", " ", para_text).strip()
        para_text = _strip_ee_quoted_payload_spans(para_text)
        if "jõustum" not in title.lower() and "jõustu" not in para_text.lower():
            continue
        saw_structured_commencement = True
        clauses = re.findall(
            r"((?:Käesolev\s+(?:seadus|määrus)|Käesoleva\s+(?:seaduse|määruse)|Määruse)\b.+?"
            r"jõustu(?:b|vad)\b.+?)"
            r"(?=(?:\s+(?:Käesolev\s+(?:seadus|määrus)|Käesoleva\s+(?:seaduse|määruse)|Määruse)\b|$))",
            para_text,
            re.IGNORECASE | re.DOTALL,
        )
        whole_act_effective = _whole_act_effective_from_clauses([clause.strip() for clause in clauses])
        for sentence in clauses:
            whole_act_effective = _record_commencement_clause(sentence.strip(), whole_act_effective)

    if saw_structured_commencement:
        return item_effects, section_effects

    html_texts: list[str] = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag != "HTMLKonteiner" or not el.text:
            continue
        plain = _plain_html_text(el.text)
        if "jõustu" in plain.lower():
            html_texts.append(_strip_ee_quoted_payload_spans(plain))
    if not html_texts:
        return item_effects, section_effects
    html_text = " ".join(html_texts)
    clauses = re.findall(
        r"((?:Käesolev\s+(?:seadus|määrus)|Käesoleva\s+(?:seaduse|määruse)|Määruse)\b.+?"
        r"jõustu(?:b|vad)\b.+?)"
        r"(?=(?:\s+(?:Käesolev\s+(?:seadus|määrus)|Käesoleva\s+(?:seaduse|määruse)|Määruse)\b|$))",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    whole_act_effective = _whole_act_effective_from_clauses([clause.strip() for clause in clauses])
    for sentence in clauses:
        whole_act_effective = _record_commencement_clause(sentence.strip(), whole_act_effective)
    return item_effects, section_effects


def _old_format_target_has_ref_owned_slice(
    *,
    target_section_labels: tuple[str, ...],
    item_effects: dict[tuple[str, str], str],
    section_effects: dict[str, str],
    ref_effective: str,
) -> bool:
    """Return whether the current ref slice owns any delayed content for the target."""
    if not ref_effective or not target_section_labels:
        return True
    act_has_slice = any(effective == ref_effective for effective in section_effects.values()) or any(
        effective == ref_effective for effective in item_effects.values()
    )
    if not act_has_slice:
        return True
    target_has_slice = any(
        section_effects.get(section_label) == ref_effective
        for section_label in target_section_labels
    ) or any(
        section_label in target_section_labels and effective == ref_effective
        for (section_label, _item_label), effective in item_effects.items()
    )
    return target_has_slice


def _extract_old_format_target_section_labels(
    root: ET.Element,
    target_title: str,
) -> tuple[str, ...]:
    """Find old-format amendment-act paragraph numbers targeting ``target_title``."""
    if not target_title:
        return ()
    def _normalize_title(title: str) -> str:
        normalized = re.sub(r"\s+muutmine\s*$", "", title.strip(), flags=re.IGNORECASE)
        return _paragrahv_to_act_id(normalized)

    normalized_target = _normalize_title(target_title)
    labels: list[str] = []
    for para in root.iter():
        tag = para.tag.split("}")[-1] if "}" in para.tag else para.tag
        if tag != "paragrahv":
            continue
        para_nr = ""
        para_title = ""
        para_text = ""
        for child in para:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "paragrahvNr":
                para_nr = "".join(str(text) for text in child.itertext()).strip()
            elif child_tag == "paragrahvPealkiri":
                para_title = "".join(str(text) for text in child.itertext()).strip()
            elif child_tag in {"loige", "sisuTekst"} and not para_text:
                para_text = " ".join(str(text) for text in child.itertext())
                para_text = para_text.replace("\xa0", " ")
                para_text = re.sub(r"\s+", " ", para_text).strip()
        candidate_title = para_title
        if not candidate_title and para_text:
            intro = para_text.split("tehakse järgmised muudatused", 1)[0].strip(" :")
            candidate_title = intro
        if para_nr and candidate_title and (
            _normalize_title(candidate_title) == normalized_target
            or _tr_old_format_section_matches_target(target_title, candidate_title)
        ):
            labels.append(_normalize_num(para_nr))
    return tuple(dict.fromkeys(labels))


def _apply_old_format_commencement_effects(
    root: ET.Element,
    ops: List[LegalOperation],
    *,
    target_section_labels: tuple[str, ...] = (),
    fallback_effective: str = "",
) -> List[LegalOperation]:
    """Stamp old-format item-local commencement dates onto tagged operations."""
    item_effects, section_effects = _extract_old_format_commencement_effects(
        root,
        fallback_effective=fallback_effective,
    )
    if not item_effects and not section_effects:
        return ops

    updated_ops: list[LegalOperation] = []
    for op in ops:
        source = op.source
        if source is None or source.effective:
            updated_ops.append(op)
            continue
        amendment_section = _old_format_provenance_value(op, "old_format_amendment_section:")
        if not amendment_section and len(target_section_labels) == 1:
            amendment_section = target_section_labels[0]
        amendment_item = _old_format_provenance_value(op, "old_format_amendment_item:")
        effective = item_effects.get((amendment_section, amendment_item)) or section_effects.get(
            amendment_section,
            "",
        )
        if not effective:
            updated_ops.append(op)
            continue
        updated_ops.append(replace(op, source=replace(source, effective=effective)))
    return updated_ops


def _extract_new_format_default_slice_ownership(
    root: ET.Element,
) -> tuple[str, set[tuple[str, str]], set[str]]:
    """Read new-format whole-act default commencement plus general-order exceptions."""
    whole_act_effective = ""
    general_order_items: set[tuple[str, str]] = set()
    general_order_sections: set[str] = set()
    for para in root.iter():
        tag = para.tag.split("}")[-1] if "}" in para.tag else para.tag
        if tag != "paragrahv":
            continue
        title = ""
        for child in para:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "paragrahvPealkiri":
                title = "".join(str(text) for text in child.itertext()).strip()
                break
        para_text = " ".join(text.strip() for text in para.itertext() if text and text.strip())
        para_text = _html.unescape(para_text).replace("\xa0", " ")
        para_text = re.sub(r"<[^>]+>", " ", para_text)
        para_text = re.sub(r"\s+", " ", para_text).strip()
        para_text = _strip_ee_quoted_payload_spans(para_text)
        if "jõustum" not in title.lower() and "jõustu" not in para_text.lower():
            continue
        clauses = re.findall(
            r"((?:Käesolev seadus|Käesoleva seaduse)\b.+?jõustu(?:b|vad)\b.+?)"
            r"(?=(?:\s+(?:Käesolev seadus|Käesoleva seaduse)\b|$))",
            para_text,
            re.IGNORECASE | re.DOTALL,
        )
        for sentence in clauses:
            sentence = sentence.strip()
            if "jõustu" not in sentence.lower():
                continue
            explicit_date = _old_format_commencement_date(sentence)
            if (
                explicit_date
                and re.search(r"\bKäesolev seadus jõustub\b", sentence, re.IGNORECASE)
                and "§" not in sentence
            ):
                whole_act_effective = explicit_date
            if "üldises korras" not in sentence.lower():
                continue
            item_spans: list[tuple[int, int]] = []
            for match in re.finditer(
                r"§\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+"
                r"punkt(?:id|i)?\s+(.+?)"
                r"(?=(?:\s+(?:ning|ja)\s+§|\s*,\s*§|\s+jõustu(?:b|vad)\b|$))",
                sentence,
                re.IGNORECASE | re.DOTALL,
            ):
                section_label = _normalize_num(match.group(1))
                item_labels = _parse_commencement_item_labels(match.group(2))
                for item_label in item_labels:
                    general_order_items.add((section_label, item_label))
                item_spans.append(match.span())
            section_sentence = sentence
            for start, end in reversed(item_spans):
                section_sentence = section_sentence[:start] + section_sentence[end:]
            for section_label in _iter_commencement_section_label_groups(section_sentence):
                general_order_sections.add(section_label)
    return whole_act_effective, general_order_items, general_order_sections


def _apply_new_format_default_slice_effects(
    root: ET.Element,
    ops: List[LegalOperation],
    *,
    fallback_effective: str = "",
) -> List[LegalOperation]:
    """Assign new-format later-slice default ownership without mis-tagging general-order exceptions."""
    if not fallback_effective:
        return ops
    whole_act_effective, general_order_items, general_order_sections = (
        _extract_new_format_default_slice_ownership(root)
    )
    if not whole_act_effective and not general_order_items and not general_order_sections:
        return ops

    def _stamp_empty_ops(effective: str) -> List[LegalOperation]:
        if not effective:
            return ops
        updated_ops: list[LegalOperation] = []
        for op in ops:
            source = op.source
            if source is None or source.effective:
                updated_ops.append(op)
                continue
            updated_ops.append(replace(op, source=replace(source, effective=effective)))
        return updated_ops

    target_sections = {
        _old_format_provenance_value(op, "old_format_amendment_section:")
        for op in ops
        if _old_format_provenance_value(op, "old_format_amendment_section:")
    }
    has_same_section_general_order = bool(
        {section_label for section_label, _item_label in general_order_items}.intersection(target_sections)
        or general_order_sections.intersection(target_sections)
    )
    if not has_same_section_general_order:
        return _stamp_empty_ops(whole_act_effective)

    updated_ops: list[LegalOperation] = []
    current_is_whole_act_default = whole_act_effective == fallback_effective
    for op in ops:
        source = op.source
        if source is None:
            updated_ops.append(op)
            continue
        has_old_format_provenance = bool(
            _old_format_provenance_value(op, "old_format_amendment_section:")
        )
        amendment_section = _old_format_provenance_value(op, "old_format_amendment_section:")
        amendment_item = _old_format_provenance_value(op, "old_format_amendment_item:")
        explicit_general_order = (
            (amendment_section, amendment_item) in general_order_items
            or amendment_section in general_order_sections
        )
        if explicit_general_order:
            if current_is_whole_act_default:
                if has_old_format_provenance:
                    updated_ops.append(op)
                    continue
                if source.effective == fallback_effective:
                    updated_ops.append(replace(op, source=replace(source, effective="")))
                    continue
            elif not source.effective:
                updated_ops.append(replace(op, source=replace(source, effective=fallback_effective)))
                continue
        else:
            if current_is_whole_act_default:
                if not source.effective:
                    updated_ops.append(replace(op, source=replace(source, effective=fallback_effective)))
                    continue
            elif not source.effective and whole_act_effective:
                updated_ops.append(replace(op, source=replace(source, effective=whole_act_effective)))
                continue
        updated_ops.append(op)
    return updated_ops


# ---------------------------------------------------------------------------
# IRNode apply helpers (Estonia-specific; pure functional via tree_ops)
# ---------------------------------------------------------------------------


def _parse_section_blocks(content: str) -> List[IRNode]:
    """Parse section blocks from content into a list of section IRNodes.

    Helper shared by _parse_chapter_payload for both chapter-level and
    division-level section parsing.
    """
    # Split on real section starts like "§ 52 1 . Title" or "§ 3. (1) ...".
    # Do not split on in-text citations such as "raamatupidamise seaduse § 13 2.
    # lõikest", where the lowercase continuation is citation prose, not a new
    # section heading/body.
    sect_blocks = re.split(
        r"(?=§\s*\d[\d\s]*\.\s*(?:[A-ZÕÄÖÜ0-9„«(]))",
        content,
    )
    section_nodes: List[IRNode] = []
    for block in sect_blocks:
        block = block.strip()
        if not block or not block.startswith("§"):
            continue
        # Extract section number: "§ 11 1 ." → "11_1"
        m_num = re.match(
            r"§\s*(\d[\d\s]*)\.\s*(?=[A-ZÕÄÖÜ0-9„«(])",
            block,
        )
        if not m_num:
            continue
        sec_label = _normalize_num(m_num.group(1).strip())
        parsed_sec = _parse_section_payload(block, kind=IRNodeKind.SECTION)
        section_nodes.append(
            IRNode(
                kind=IRNodeKind.SECTION,
                label=sec_label,
                text=parsed_sec.text,
                children=parsed_sec.children,
            )
        )
    return section_nodes


def _parse_chapter_payload(content: str, chapter_label: str) -> IRNode:
    """Parse a whole-chapter insert payload into a structured chapter IRNode.

    Input is the quoted content from "seadust täiendatakse N. peatükiga järgmises
    sõnastuses: „N. peatükk Title [N. jagu DivTitle] § N1. Sect1Title\x01 (1) ..."

    Handles two structures:
    1. Flat: chapter → sections directly
    2. With divisions: chapter → division → sections

    Produces:
        chapter(label=chapter_label, text=chapter_title,
            children=[section(...), ...])       # flat
        OR
        chapter(label=chapter_label, text=chapter_title,
            children=[division(label="1", children=[section, ...]), ...])
    """
    # Extract chapter title: text between "peatükk" and the first "§" or "N. jagu"
    m_ch = re.match(r"^\s*\d[\d\s_]*[.]\s*peatükk\s+(.*?)(?=§\s*\d|\d[\d\s]*[.]\s*jagu\b)", content, re.DOTALL)
    ch_title = m_ch.group(1).strip() if m_ch else ""

    # Check for divisions: "N. jagu DivTitle" before sections
    # Pattern: "\d+. jagu" marks a division start
    div_pattern = r"(?=\b\d+[.]\s*jagu\b)"
    if re.search(div_pattern, content):
        # Split on division boundaries
        div_blocks = re.split(div_pattern, content)
        children: List[IRNode] = []
        for dblock in div_blocks:
            dblock = dblock.strip()
            m_div = re.match(r"^(\d+)[.]\s*jagu\s+(.*?)(?=§\s*\d|$)", dblock, re.DOTALL)
            if not m_div:
                continue
            div_label = m_div.group(1).strip()
            div_text_and_sects = dblock[m_div.start() :]
            # Extract division title (text before first §)
            m_div_title = re.match(r"^\d+[.]\s*jagu\s+(.*?)(?=§\s*\d)", div_text_and_sects, re.DOTALL)
            div_title = m_div_title.group(1).strip() if m_div_title else ""
            # Parse sections within this division
            sects = _parse_section_blocks(dblock)
            children.append(
                IRNode(
                    kind=IRNodeKind.DIVISION,
                    label=div_label,
                    text=div_title,
                    children=tuple(sects),
                )
            )
        return IRNode(kind=IRNodeKind.CHAPTER, label=chapter_label, text=ch_title, children=tuple(children))

    # No divisions — flat chapter with sections directly.
    section_nodes = _parse_section_blocks(content)
    return IRNode(kind=IRNodeKind.CHAPTER, label=chapter_label, text=ch_title, children=tuple(section_nodes))


def _parse_division_payload(content: str, division_label: str) -> IRNode:
    """Parse a whole-division payload into a structured division IRNode."""
    m_div_title = re.match(
        r"^\s*\d[\d\s_]*[.]\s*jagu\s+(.*?)(?=§\s*\d|$)",
        content,
        re.DOTALL,
    )
    div_title = m_div_title.group(1).strip() if m_div_title else ""
    return IRNode(
        kind=IRNodeKind.DIVISION,
        label=division_label,
        text=div_title,
        children=tuple(_parse_section_blocks(content)),
    )


def _strip_rt_editorial_parentheticals(text: str) -> str:
    """Strip inline RT publication-reference parentheticals from payload text.

    Amendment payloads sometimes include source-side references like
    ``autoveoseaduse (RT I 2000, 54, 346) kohase ...`` while the consolidated
    oracle materializes only ``autoveoseaduse kohase ...``. Treat these
    parentheticals as editorial citation residue, but only inside payload-derived
    replacement text.
    """
    if not text:
        return text
    stripped = re.sub(r"\s*\(RT\s+[IVX]+[^)]*\)", "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"(?<=\d)\s*[–-]\s*(?=\d)", "–", stripped)
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(r" +([.,;:!?)])", r"\1", stripped)
    return stripped.strip()


def _parse_section_payload(text: str, kind: IRNodeKind = IRNodeKind.SECTION) -> IRNode:
    """Parse flat payload text into a structured section IRNode.

    Amendment replace payloads come as a single string like:
        "§ 5. Title text (1) First subsection. (2) Second subsection."

    The oracle has structured IRNodes:
        section(text="Title text", children=[sub(1,"First..."), sub(2,"Second...")])

    This function:
        1. Strips the leading "§ N." marker (redundant with label)
        2. Splits at subsection markers "(N)" to extract title + body
        3. Returns a structured IRNode matching the oracle format

    If no subsection markers found, returns the full text as section.text
    with no children (same as before for heading-only replacements).
    """
    # Strip leading section-number markers from whole-section payloads.
    stripped = re.sub(r"^(?:§\s*)?\d[\d\s_]*(?:\.\s*|\s+)", "", text.strip(), count=1)

    # Split at subsection markers: (N) at start of subsection
    # Pattern: split on "(N)" where N is 1+ digits, possibly with superscript
    parts = re.split(r"(?=\(\d[\d\s_]*\)\s)", stripped)

    title_part = parts[0].strip() if parts else stripped.strip()
    subsection_parts = parts[1:] if len(parts) > 1 else []

    if not subsection_parts:
        # No explicit (N) subsection markers found.
        # Check for \x01 boundary marker inserted by parse_html_op_items:
        # "<b>§ N. Title</b><p>Body</p>" → "§ N. Title\x01 Body" after
        # HTML-stripping with bold boundary preservation.  Split at \x01
        # to produce title="Title" + subsection:1="Body".
        if "\x01" in title_part:
            title_raw, body_raw = title_part.split("\x01", 1)
            title_raw = _strip_rt_editorial_parentheticals(title_raw.strip())
            body_raw = _strip_rt_editorial_parentheticals(body_raw.strip())
            if body_raw:
                # Split item markers "1) ... 2) ..." into item children, same
                # as the (N) subsection path below.
                # Guard: only treat as item list if first item has label "1"
                # (prevents false splits on "60–61)" citation suffixes).
                intro_x, item_children_x = _parse_inline_item_children(body_raw)
                sub1 = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=intro_x, children=tuple(item_children_x))
                return IRNode(kind=kind, label="", text=title_raw, children=(sub1,))
            return IRNode(kind=kind, label="", text=title_raw, children=())
        # No boundary marker — return as flat text (heading-only replacement).
        return IRNode(
            kind=kind,
            label="",
            text=_strip_rt_editorial_parentheticals(title_part),
            children=(),
        )

    # Strip \x01 from the title part (bold-boundary sentinel may be there
    # when the text has "§ N. Title\x01 (1) Body..." form).
    title_part = _strip_rt_editorial_parentheticals(title_part.replace("\x01", "").strip())

    # Build children from subsection parts
    children: List[IRNode] = []
    for sp in subsection_parts:
        m = re.match(r"\((\d[\d\s_]*)\)\s*(.*)", sp, re.DOTALL)
        if m:
            sub_label = re.sub(r"\s+", "_", m.group(1).strip())
            sub_text = _strip_rt_editorial_parentheticals(m.group(2).strip())
            # Split item markers "1) ... 2) ... N) ..." into item children.
            # Estonian list items use pattern: "N) text" where N is an integer,
            # or "N N ) text" where the superscript gives a compound label like 4_1.
            intro_text, item_children = _parse_inline_item_children(sub_text)
            if item_children:
                children.append(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label=sub_label,
                        text=intro_text,
                        children=tuple(item_children),
                    )
                )
            else:
                children.append(IRNode(kind=IRNodeKind.SUBSECTION, label=sub_label, text=sub_text))

    return IRNode(kind=kind, label="", text=title_part, children=tuple(children))


def _replace_text_in_subtree(
    node: IRNode,
    old: str,
    new: str,
    *,
    case_inflected: bool = False,
) -> tuple[IRNode, bool]:
    """Replace textual occurrences throughout a subtree."""
    if node.text is not None:
        replaced = (
            _ee_apply_text_replace_value(
                node.text,
                old,
                new,
                case_inflected=case_inflected,
                capitalize_sentence_start=node.kind != IRNodeKind.ITEM,
            )
            or ""
        )
        text_changed = replaced != node.text
    else:
        replaced = node.text or ""
        text_changed = False

    new_children: list[IRNode] = []
    changed = False
    for child in node.children:
        new_child, child_changed = _replace_text_in_subtree(
            child,
            old,
            new,
            case_inflected=case_inflected,
        )
        new_children.append(new_child)
        changed = changed or child_changed

    if text_changed or changed:
        return (
            IRNode(
                kind=node.kind,
                label=node.label,
                text=replaced,
                attrs=dict(node.attrs),
                children=tuple(new_children),
            ),
            True,
        )
    return node, False


def _replace_text_in_subtree_with_spec(
    node: IRNode,
    spec: EETextRewriteSpec,
    *,
    case_inflected: bool | None = None,
    capitalize_sentence_start: bool | None = None,
) -> tuple[IRNode, bool]:
    """Replace text in a subtree using a typed rewrite spec."""
    actual_case_inflected = spec.case_inflected if case_inflected is None else case_inflected
    actual_capitalize_sentence_start = (
        node.kind != IRNodeKind.ITEM if capitalize_sentence_start is None else capitalize_sentence_start
    )

    replaced = node.text
    if node.text is not None:
        replaced = _ee_apply_text_replace_spec(
            node.text,
            spec,
            case_inflected=actual_case_inflected,
            capitalize_sentence_start=actual_capitalize_sentence_start,
        )
        if replaced is None:
            replaced = node.text
    text_changed = replaced != node.text

    new_children: list[IRNode] = []
    changed = False
    for child in node.children:
        new_child, child_changed = _replace_text_in_subtree_with_spec(
            child,
            spec,
            case_inflected=actual_case_inflected,
            capitalize_sentence_start=capitalize_sentence_start,
        )
        new_children.append(new_child)
        changed = changed or child_changed

    if text_changed or changed:
        return (
            IRNode(
                kind=node.kind,
                label=node.label,
                text=replaced,
                attrs=dict(node.attrs),
                children=tuple(new_children),
            ),
            True,
        )
    return node, False


def _ee_text_replace_match_spans(text: str | None, spec: EETextRewriteSpec) -> tuple[tuple[int, int], ...]:
    """Return unique live-text spans matching a rewrite's source surface."""
    if not text or not spec.old_text:
        return ()
    spans: list[tuple[int, int]] = []
    variants = _ee_text_replace_variants(
        spec.old_text,
        spec.new_text,
        case_inflected=spec.case_inflected,
    )
    for old_variant, _new_variant in variants:
        pattern = re.compile(
            _ee_wrap_word_boundaries(_ee_surface_pattern(old_variant), old_variant),
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            span = match.span()
            if span not in spans:
                spans.append(span)
    return tuple(sorted(spans))


def _ee_repeated_single_occurrence_rewrite_match_count(node: IRNode, spec: EETextRewriteSpec) -> int:
    """Count matches where a single-occurrence insert rewrite would need source disambiguation."""
    if spec.all_occurrences or spec.mode not in {"insert_after", "insert_before"}:
        return 0
    count = len(_ee_text_replace_match_spans(node.text, spec))
    for child in node.children:
        count += _ee_repeated_single_occurrence_rewrite_match_count(child, spec)
    return count


_EE_SOURCE_TYPO_TEXT_REPLACE_RULE = "ee_source_typo_text_replace_near_match"
_EE_AMBIGUOUS_SINGLE_OCCURRENCE_TEXT_REPLACE_RULE = "ee_ambiguous_single_occurrence_text_replace"


def _ee_levenshtein_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    mismatch_seen = False
    i = 0
    j = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        if mismatch_seen:
            return False
        mismatch_seen = True
        j += 1
    return True


def _ee_typo_tolerant_text_replace(
    node: IRNode,
    spec: EETextRewriteSpec,
) -> tuple[IRNode, bool, str]:
    """Recover a one-character typo in source old-text only under the exact target."""
    old = _ee_normalize_text_replace_surface(spec.old_text)
    new = _ee_normalize_text_replace_surface(spec.new_text)
    if len(old) < 8 or not old.isalpha() or not new:
        return node, False, ""

    candidate_pattern = re.compile(r"[A-Za-zÄÖÕÜäöõüŠŽšž-]+")
    matches: list[tuple[tuple[int, ...], str]] = []

    def _collect(current: IRNode, path: tuple[int, ...] = ()) -> None:
        for match in candidate_pattern.finditer(current.text or ""):
            candidate = match.group(0)
            candidate_norm = _ee_normalize_text_replace_surface(candidate)
            if candidate_norm.lower() == old.lower():
                continue
            if abs(len(candidate_norm) - len(old)) > 1:
                continue
            if _ee_levenshtein_distance_at_most_one(candidate_norm.lower(), old.lower()):
                matches.append((path, candidate))
        for idx, child in enumerate(current.children):
            _collect(child, (*path, idx))

    _collect(node)
    unique_candidates = {candidate for _, candidate in matches}
    if len(matches) != 1 or len(unique_candidates) != 1:
        return node, False, ""
    target_path, actual_old = matches[0]

    def _replace(current: IRNode, path: tuple[int, ...] = ()) -> IRNode:
        if path == target_path:
            pattern = re.compile(
                _ee_wrap_word_boundaries(_ee_surface_pattern(actual_old), actual_old),
                re.IGNORECASE,
            )
            replaced_text = pattern.sub(new, current.text or "", count=1)
            return IRNode(
                kind=current.kind,
                label=current.label,
                text=replaced_text,
                attrs=dict(current.attrs),
                children=tuple(current.children),
            )
        children = tuple(_replace(child, (*path, idx)) for idx, child in enumerate(current.children))
        if children == current.children:
            return current
        return IRNode(
            kind=current.kind,
            label=current.label,
            text=current.text,
            attrs=dict(current.attrs),
            children=children,
        )

    return _replace(node), True, actual_old


def _extract_subsection_text(payload_text: str, label: str) -> str:
    """Extract the text for a specific subsection from a multi-subsection payload.

    When `lõigetega N ja M` / `lõigetega N–M` generates multiple insert ops
    that all share the same full payload, this function selects just the text
    that belongs to the subsection with the given label.

    Labels like "13_1" correspond to the marker "(13 1)" in the text (the
    normalized form used by _normalize_num replaces spaces with underscores
    for superscript suffixes, e.g. "13 1" → "13_1").

    If the label is not found as a marker, falls back to stripping the leading
    "(N)" prefix (original behaviour, handles single-subsection payloads).
    """
    # Convert label back to the numeric marker form: "13_1" → "13 1", "3" → "3"
    marker_num = label.replace("_", " ")

    # Split on all subsection markers
    parts = re.split(r"(?=\(\d[\d\s_]*\)\s)", payload_text.strip())

    # Find the part that starts with the marker matching our label
    for part in parts:
        m = re.match(r"\((\d[\d\s]*)\)\s*(.*)", part.strip(), re.DOTALL)
        if m:
            # Normalize the captured number the same way _normalize_num does
            part_num = re.sub(r"\s+", " ", m.group(1).strip())
            if part_num == marker_num:
                return m.group(2).strip()

    # Fallback: strip the leading "(N)" if present
    return re.sub(r"^\(\d[\d\s_]*\)\s*", "", payload_text.strip())


def _parse_subsection_item_payload(
    raw_text: str,
    *,
    require_first_label_one: bool = True,
) -> tuple[str, List[IRNode]]:
    """Split a subsection payload into intro text plus numbered item children."""
    return _parse_inline_item_children(
        raw_text,
        require_first_label_one=require_first_label_one,
    )


def _parse_inline_subsection_payload_nodes(raw_text: str) -> List[IRNode]:
    """Parse payload text that inlines one or more numbered subsections.

    Some EE subsection-level ``replace`` ops carry the full replacement for the
    targeted subsection plus one or more immediately following subsections, for
    example ``(2) ... 1) ... 5) ... (2 1) ...``.  RT consolidated oracles
    materialize those later blocks as real subsection nodes, so replay must do
    the same instead of discarding everything after the first label.
    """
    parts = re.split(r"(?=\(\d[\d\s_]*\)\s)", raw_text.strip())
    nodes: List[IRNode] = []
    for part in parts:
        match = re.match(r"^\((\d[\d\s]*)\)\s*(.*)$", part.strip(), re.DOTALL)
        if match is None:
            continue
        label = _normalize_num(match.group(1))
        body_text = _strip_rt_editorial_parentheticals(match.group(2).strip())
        intro_text, item_children = _parse_subsection_item_payload(
            body_text,
            require_first_label_one=False,
        )
        nodes.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=label,
                text=intro_text,
                children=tuple(item_children),
            )
        )
    return nodes


def _subsection_labels_implied_by_plain_range_repeal(
    parent_node: IRNode,
    note_text: str,
) -> list[str]:
    """Return existing subsection labels covered by plain numeric repeal ranges.

    Source clauses like ``lõiked 2–4 tunnistatakse kehtetuks`` are expected to
    clear intervening inserted siblings such as ``2_1`` and ``2_2`` as well.
    Parser-side expansion cannot know which superscript siblings actually exist,
    so replay resolves this against the current parent subsection list.
    """
    implied: set[str] = set()
    for match in re.finditer(r"l[oõ]iked\s+([^;,.]+)", note_text, re.IGNORECASE):
        range_text = match.group(1)
        for start_raw, end_raw in re.findall(r"(\d+)\s*[–‒-]\s*(\d+)", range_text):
            start_num = int(start_raw)
            end_num = int(end_raw)
            if end_num < start_num:
                continue
            for child in parent_node.children:
                if child.kind != IRNodeKind.SUBSECTION:
                    continue
                if child.label is None:
                    continue
                base_raw = child.label.split("_", 1)[0]
                if not base_raw.isdigit():
                    continue
                base_num = int(base_raw)
                if start_num <= base_num <= end_num:
                    implied.add(child.label)
    return sorted(implied, key=tree_ops._default_sort_key)


def _section_labels_implied_by_plain_range_repeal(
    parent_node: IRNode,
    *,
    explicit_labels: tuple[str, ...] = (),
    plain_numeric_ranges: tuple[tuple[str, str], ...] = (),
) -> list[str]:
    """Return existing section labels covered by a plain numeric repeal range.

    Source clauses like ``§-d 1–25 tunnistatakse kehtetuks`` are expected to
    cover intervening inserted siblings such as ``§ 8^1``. Parser-side
    expansion cannot know which superscript siblings actually exist, so replay
    resolves this against the current chapter/part child list.
    """
    implied: set[str] = {label for label in explicit_labels if label}
    for start_raw, end_raw in plain_numeric_ranges:
        if not (start_raw.isdigit() and end_raw.isdigit()):
            continue
        start_num = int(start_raw)
        end_num = int(end_raw)
        if end_num < start_num:
            continue
        for child in parent_node.children:
            if child.kind != IRNodeKind.SECTION or child.label is None:
                continue
            base_raw = child.label.split("_", 1)[0]
            if not base_raw.isdigit():
                continue
            base_num = int(base_raw)
            if start_num <= base_num <= end_num:
                implied.add(child.label)
    return sorted(implied, key=tree_ops._default_sort_key)


def _address_to_path(target) -> tree_ops.Path:
    """Convert LegalAddress path tuples to tree_ops Path format."""
    return tuple((kind, label) for kind, label in target.path)


EETextReplaceMode = Literal["replace", "delete", "insert_before", "insert_after", "unknown"]
_EE_REWRITE_MODE_TYPES = ("replace", "delete", "insert_before", "insert_after", "unknown")


@dataclass(frozen=True)
class EETextRewriteSpec:
    old_text: str
    new_text: str
    mode: EETextReplaceMode = "replace"
    case_inflected: bool = False
    all_occurrences: bool = False


def _ee_text_replace_mode(value: object) -> EETextReplaceMode:
    if value not in _EE_REWRITE_MODE_TYPES:
        return "unknown"
    return cast(EETextReplaceMode, value)


def _ee_read_text_replace_spec(payload: IRNode | None) -> EETextRewriteSpec | None:
    if payload is None:
        return None
    payload_meta = read_payload_rewrite_meta(payload)
    rewrite = payload_meta.rewrite
    if rewrite is None:
        return None
    return EETextRewriteSpec(
        old_text=rewrite.old_surface.replace("\x01", ""),
        new_text=str(payload.text or "").replace("\x01", ""),
        mode=_ee_text_replace_mode(rewrite.mode.value),
        case_inflected=rewrite.case_inflected,
        all_occurrences=bool(payload.attrs.get("all_occurrences")),
    )


def _ee_apply_text_replace_spec(
    text: str | None,
    spec: EETextRewriteSpec | None,
    *,
    case_inflected: bool | None = None,
    capitalize_sentence_start: bool = True,
) -> str | None:
    """Apply a typed rewrite spec to text using existing replacement engine."""
    if spec is None:
        return text
    old_text = spec.old_text.replace("\x01", "")
    if not old_text:
        return text
    new_text = spec.new_text.replace("\x01", "")
    inflected = bool(spec.case_inflected if case_inflected is None else case_inflected)
    mode = spec.mode
    if mode == "delete":
        new_text = ""
    elif mode == "insert_before":
        old_norm = _ee_normalize_text_replace_surface(old_text)
        new_norm = _ee_normalize_text_replace_surface(new_text)
        if old_norm and old_norm not in new_norm:
            before_sep = " "
            if new_text.endswith(" ") or re.match(r"^[\s,.;:!?)\-–‒]", old_text):
                before_sep = ""
            new_text = f"{new_text}{before_sep}{old_text}"
    elif mode == "insert_after":
        old_norm = _ee_normalize_text_replace_surface(old_text)
        new_norm = _ee_normalize_text_replace_surface(new_text)
        if old_norm and old_norm not in new_norm:
            after_sep = " "
            if not new_text or re.match(r"^[\s,.;:!?)\-–‒]", new_text):
                after_sep = ""
            new_text = f"{old_text}{after_sep}{new_text}"

    return _ee_apply_text_replace_value(
        text,
        old_text,
        new_text,
        mode=mode,
        case_inflected=inflected,
        all_occurrences=spec.all_occurrences,
        capitalize_sentence_start=capitalize_sentence_start,
    )


def _ee_path_is_excluded(
    current_path: tuple[tuple[str, str], ...],
    excluded_paths: Any,
) -> bool:
    """Return True when the current structural path falls under an excluded path."""
    if not isinstance(excluded_paths, (list, tuple, set)):
        return False
    for raw_path in excluded_paths:
        if not isinstance(raw_path, (list, tuple)):
            continue
        ex_path = tuple(
            (str(kind), str(label)) for kind, label in raw_path if isinstance(kind, str) and label is not None
        )
        if ex_path and len(current_path) >= len(ex_path) and current_path[-len(ex_path) :] == ex_path:
            return True
    return False


def _ee_global_text_replace(
    body: IRNode,
    old: str,
    new: str,
    *,
    excluded_paths: object = None,
) -> IRNode:
    """Replace `old` with `new` in every text node of the body tree.

    Used for statute-wide text replacements like
    "seaduse kogu tekstis asendatakse sõna X sõnaga Y".

    Pure functional — returns a new tree. Preserves object identity for
    unchanged subtrees so `new_body is not body` works for change detection.

    Sections marked attrs['kehtetu']=True (already repealed in the base) are
    skipped for title replacement: RT tervikteksts preserve the original title
    of a repealed section without applying subsequent global renames to it.
    """

    def _walk(node: IRNode, current_path: tuple[tuple[str, str], ...] = ()) -> IRNode:
        node_path = current_path
        if node.label is not None:
            node_path = current_path + ((str(node.kind), node.label),)
        if _ee_path_is_excluded(node_path, excluded_paths):
            return node
        # Already-repealed sections: skip replacing their title (node.text).
        # RT tervikteksts preserve the original title of repealed sections
        # without applying subsequent statute-wide renames.
        skip_title = node.kind == IRNodeKind.SECTION and bool(node.attrs.get("kehtetu"))
        if node.text and not skip_title:
            replaced = node.text.replace(old, new)
            if replaced != node.text:
                # Replacing with empty string (or shorter text) can leave
                # double spaces ("on  haiguse"), a space before punctuation
                # ("arv ."), or consecutive commas ("andmeid,, koos").
                replaced = re.sub(r"  +", " ", replaced)
                replaced = re.sub(r" +([.,;:!?)])", r"\1", replaced)
                replaced = re.sub(r",\s*,", ",", replaced)
                new_text: Optional[str] = replaced.strip()
            else:
                new_text = node.text
        else:
            new_text = node.text
        new_children = [_walk(c, node_path) for c in node.children]
        text_changed = new_text != node.text
        children_changed = any(nc is not oc for nc, oc in zip(new_children, node.children))
        if not text_changed and not children_changed:
            return node  # no change — preserve identity for change detection
        return IRNode(
            kind=node.kind,
            label=node.label,
            text=new_text,
            attrs=dict(node.attrs),
            children=tuple(new_children),
        )

    return _walk(body)


def _ee_global_generic_minister_plural_replace(
    body: IRNode,
    *,
    singular_text: str,
    plural_text: str,
    old_titles: list[str] | tuple[str, ...],
    excluded_paths: object = None,
) -> IRNode:
    """Collapse coordinated minister-title lists to the generic plural form."""
    singular_pattern = _ee_wrap_word_boundaries(
        _ee_surface_pattern(singular_text),
        singular_text,
    )
    repeated_pattern = re.compile(
        rf"{singular_pattern}(?:\s*,\s*{singular_pattern})+\s*(?:ja|ning)\s*{singular_pattern}"
        rf"|{singular_pattern}(?:\s*,\s*{singular_pattern})+"
        rf"|{singular_pattern}\s+(?:ja|ning|või)\s+{singular_pattern}",
        re.IGNORECASE,
    )
    title_stems: list[str] = cast(
        list[str],
        sorted(
            {title[: -len("minister")].rstrip() for title in old_titles if title.endswith("minister")},
            key=len,
            reverse=True,
        ),
    )
    shared_head_pattern = None
    if title_stems:
        stem_pattern = "|".join(_ee_surface_pattern(stem) for stem in title_stems)
        shared_head_pattern = re.compile(
            rf"(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])(?:{stem_pattern})\s*-\s*(?:ja|või)\s*{singular_pattern}",
            re.IGNORECASE,
        )
    singular_forms = _ee_phrase_forms(singular_text) or {}
    singular_nom = singular_forms.get("sg_nom") or singular_text
    singular_gen = singular_forms.get("sg_gen") or singular_text
    redundant_tail_pattern = re.compile(
        rf"({re.escape(plural_text)})\s*,\s*"
        rf"(?:{_ee_surface_pattern(singular_nom)}|{_ee_surface_pattern(singular_gen)})\s+"
        rf"(ja|ning|või)\s+",
        re.IGNORECASE,
    )

    def _replace(text: str) -> str:
        updated = repeated_pattern.sub(
            lambda match: _ee_case_preserved_replacement(match, plural_text),
            text,
        )
        if shared_head_pattern is not None:
            updated = shared_head_pattern.sub(
                lambda match: _ee_case_preserved_replacement(match, plural_text),
                updated,
            )
        updated = re.sub(
            rf",\s+({re.escape(plural_text)})(?=[.!?;])",
            r" ja \1",
            updated,
        )
        updated = redundant_tail_pattern.sub(r"\1 \2 ", updated)
        return updated

    def _walk(node: IRNode, current_path: tuple[tuple[str, str], ...] = ()) -> IRNode:
        node_path = current_path
        if node.label is not None:
            node_path = current_path + ((str(node.kind), node.label),)
        if _ee_path_is_excluded(node_path, excluded_paths):
            return node
        skip_title = node.kind == IRNodeKind.SECTION and bool(node.attrs.get("kehtetu"))
        if node.text and not skip_title:
            new_text = _replace(node.text)
        else:
            new_text = node.text
        new_children = [_walk(c, node_path) for c in node.children]
        text_changed = new_text != node.text
        children_changed = any(nc is not oc for nc, oc in zip(new_children, node.children))
        if not text_changed and not children_changed:
            return node
        return IRNode(
            kind=node.kind,
            label=node.label,
            text=new_text,
            attrs=dict(node.attrs),
            children=tuple(new_children),
        )

    return _walk(body)


def _ee_declension_forms(word: str) -> dict[str, str] | None:
    """Infer a small set of Estonian case forms for bounded text-replace use."""
    if not word:
        return None
    lower = word.lower()
    if lower == "vorm":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower == "nimistu":
        stem = word
        plural_stem = word + "t"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": plural_stem + "e",
            "pl_part": stem + "id",
            "pl_ine": plural_stem + "es",
            "pl_ela": plural_stem + "est",
            "pl_all": plural_stem + "ele",
            "pl_ade": plural_stem + "el",
            "pl_abl": plural_stem + "elt",
            "pl_trn": plural_stem + "eks",
        }
    if lower == "meri":
        stem = word[:-1] + "e"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-1] + "d",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word[:-2] + "rre",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower.endswith("vägi"):
        prefix = word[:-4]
        gen_stem = prefix + "väe"
        part_stem = prefix + "väge"
        pl_stem = prefix + "vägede"
        return {
            "sg_nom": word,
            "sg_gen": gen_stem,
            "sg_part": part_stem,
            "sg_ine": gen_stem + "s",
            "sg_ela": gen_stem + "st",
            "sg_ill": prefix + "väkke",
            "sg_all": gen_stem + "le",
            "sg_ade": gen_stem + "l",
            "sg_abl": gen_stem + "lt",
            "sg_trn": gen_stem + "ks",
            "sg_ter": gen_stem + "ni",
            "sg_ess": gen_stem + "na",
            "sg_abe": gen_stem + "ta",
            "sg_com": gen_stem + "ga",
            "pl_nom": prefix + "väed",
            "pl_gen": pl_stem,
            "pl_part": prefix + "vägesid",
            "pl_ine": pl_stem + "s",
            "pl_ela": pl_stem + "st",
            "pl_all": pl_stem + "le",
            "pl_ade": pl_stem + "l",
            "pl_abl": pl_stem + "lt",
            "pl_trn": pl_stem + "ks",
        }
    if lower == "ärakiri":
        stem = word[:-1] + "ja"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-1] + "a",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "veekogu":
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "koht":
        stem = word[:-2] + "ha"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "a",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word + "a",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
        }
    if lower == "merematke":
        base = word[:-1]
        return {
            "sg_nom": base,
            "sg_gen": word,
            "sg_part": base + "et",
            "sg_ine": base + "es",
            "sg_ela": base + "est",
            "sg_all": base + "ele",
            "sg_ade": base + "el",
            "sg_abl": base + "elt",
            "sg_trn": base + "eks",
            "sg_ter": base + "eni",
            "sg_ess": base + "ena",
            "sg_abe": base + "eta",
            "sg_com": base + "ega",
            "pl_nom": base + "ed",
            "pl_gen": base + "ete",
            "pl_part": base + "eid",
        }
    if lower == "puksiir":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
        }
    if lower == "pukser":
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "eid",
        }
    if lower.endswith("jad"):
        stem = word[:-1]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("id"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "ide",
            "pl_part": stem + "e",
            "pl_ine": stem + "ides",
            "pl_ela": stem + "idest",
            "pl_all": stem + "idele",
            "pl_ade": stem + "idel",
            "pl_abl": stem + "idelt",
            "pl_trn": stem + "ideks",
        }
    if lower.endswith("used"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "i",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("ed"):
        stem = word[:-2]
        return {
            "pl_nom": word,
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("õpe"):
        stem = word[:-3] + "õppe"
        part = word[:-3] + "õpet"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": part,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("mine"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower == "segu":
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("olu"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("is"):
        stem = word[:-2] + "ise"
        plural_stem = word[:-2] + "is"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "ist",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("us"):
        stem = word + "e"
        plural_stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("ioon"):
        return {
            "sg_nom": word,
            "sg_gen": word + "i",
            "sg_part": word + "i",
            "sg_ine": word + "is",
            "sg_ela": word + "ist",
            "sg_all": word + "ile",
            "sg_ade": word + "il",
            "sg_abl": word + "ilt",
            "sg_trn": word + "iks",
            "sg_ter": word + "ini",
            "sg_ess": word + "ina",
            "sg_abe": word + "ita",
            "sg_com": word + "iga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("ist"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("amet"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "it",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("juht"):
        stem = word[:-2] + "hi"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": word + "ide",
        }
    if lower.endswith("direktor"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("ministeerium"):
        stem = word + "i"
        plural_stem = word[:-2] + "e"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "id",
            "pl_gen": plural_stem + "ide",
        }
    if lower.endswith("line"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("lane"):
        stem = word[:-2] + "se"
        plural_stem = word[:-2] + "s"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": plural_stem + "ed",
            "pl_gen": plural_stem + "te",
            "pl_part": plural_stem + "i",
            "pl_ine": plural_stem + "tes",
            "pl_ela": plural_stem + "test",
            "pl_all": plural_stem + "tele",
            "pl_ade": plural_stem + "tel",
            "pl_abl": plural_stem + "telt",
            "pl_trn": plural_stem + "teks",
        }
    if lower.endswith("minister"):
        stem = word[:-2] + "ri"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("arst"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("vanem"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "at",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("relv"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ad",
            "pl_gen": word + "ade",
        }
    if lower.endswith("ane"):
        stem = word[:-2] + "se"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word[:-2] + "st",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("süsteem"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
        }
    if lower.endswith("moon"):
        stem = word + "a"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ad",
            "pl_gen": word + "ade",
        }
    if lower.endswith("riik"):
        stem = word[:-1] + "gi"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "i",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "i",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
        }
    if lower.endswith("lik"):
        # -lik adjectives: riiklik, avalik-like — strong grade gemination in oblique
        # sg_nom=riiklik, sg_gen=riikliku, sg_part=riiklikku (NOT riiklikut)
        stem = word + "u"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "ku",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": word + "ute",
            "pl_part": word + "uid",
            "pl_ine": word + "utes",
            "pl_ela": word + "utest",
            "pl_all": word + "utele",
            "pl_ade": word + "utel",
            "pl_abl": word + "utelt",
            "pl_trn": word + "uteks",
        }
    if lower.endswith("line"):
        stem = word[:-4] + "se"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": stem + "sse",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
        }
    if lower.endswith("ik"):
        stem = word + "u"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "ud",
            "pl_gen": word + "ute",
            "pl_part": word + "uid",
            "pl_ine": word + "utes",
            "pl_ela": word + "utest",
            "pl_all": word + "utele",
            "pl_ade": word + "utel",
            "pl_abl": word + "utelt",
            "pl_trn": word + "uteks",
        }
    if lower.endswith("uk"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("ladu"):
        stem = word[:-4] + "lao"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_ill": word[:-2] + "ttu",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "d",
            "pl_gen": stem + "de",
        }
    if lower.endswith("ve"):
        # e.g. järelevalve, haldusjärelevalve: gen=X, part=Xt, ine=Xs
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("an"):
        # e.g. järelevalveorgan: gen=Xani->Xi, part=Xit, ine=Xis
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
        }
    if lower.endswith("al"):
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("oll"):
        # protocol/kontroll-family compounds: protokoll -> protokolli,
        # transfusiooniprotokoll -> transfusiooniprotokolli.
        stem = word + "i"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ide",
            "pl_part": word + "e",
            "pl_ine": word + "ides",
            "pl_ela": word + "idest",
            "pl_all": word + "idele",
            "pl_ade": word + "idel",
            "pl_abl": word + "idelt",
            "pl_trn": word + "ideks",
        }
    if lower.endswith("register"):
        # register-family compounds: täitemenetlusregister -> täitemenetlusregistri.
        stem = word[:-2] + "ri"
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": word + "it",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": word + "id",
            "pl_gen": word + "ite",
            "pl_part": word + "eid",
            "pl_ine": word + "ites",
            "pl_ela": word + "itest",
            "pl_all": word + "itele",
            "pl_ade": word + "itel",
            "pl_abl": word + "itelt",
            "pl_trn": word + "iteks",
        }
    if lower.endswith("i"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem,
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    if lower.endswith("a"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "te",
            "pl_part": stem + "id",
            "pl_ine": stem + "tes",
            "pl_ela": stem + "test",
            "pl_all": stem + "tele",
            "pl_ade": stem + "tel",
            "pl_abl": stem + "telt",
            "pl_trn": stem + "teks",
        }
    if lower.endswith("o"):
        stem = word
        return {
            "sg_nom": word,
            "sg_gen": stem,
            "sg_part": stem + "t",
            "sg_ine": stem + "s",
            "sg_ela": stem + "st",
            "sg_all": stem + "le",
            "sg_ade": stem + "l",
            "sg_abl": stem + "lt",
            "sg_trn": stem + "ks",
            "sg_ter": stem + "ni",
            "sg_ess": stem + "na",
            "sg_abe": stem + "ta",
            "sg_com": stem + "ga",
            "pl_nom": stem + "d",
            "pl_gen": stem + "de",
            "pl_part": stem + "sid",
            "pl_ine": stem + "des",
            "pl_ela": stem + "dest",
            "pl_all": stem + "dele",
            "pl_ade": stem + "del",
            "pl_abl": stem + "delt",
            "pl_trn": stem + "deks",
        }
    return None


def _ee_phrase_forms(text: str) -> dict[str, str] | None:
    """Infer bounded case forms for a word or a phrase whose last token inflects."""
    stripped = text.strip()
    leading_prefix_match = re.match(r"^((?:või|ja|ning|koos)\s+)(.+)$", stripped, re.IGNORECASE)
    if leading_prefix_match is not None:
        prefix = leading_prefix_match.group(1)
        core_forms = _ee_phrase_forms(leading_prefix_match.group(2).strip())
        if core_forms is not None:
            return {
                key: f"{prefix}{value}"
                for key, value in core_forms.items()
            }
    trailing_punct_match = re.match(r"^(.*?)([,:;])$", stripped)
    if trailing_punct_match is not None:
        core_forms = _ee_phrase_forms(trailing_punct_match.group(1).strip())
        if core_forms is not None:
            punctuation = trailing_punct_match.group(2)
            return {
                key: f"{value}{punctuation}"
                for key, value in core_forms.items()
            }
    for conjunction in (" ja", " ning", " või"):
        if stripped.endswith(conjunction):
            core_forms = _ee_phrase_forms(stripped[: -len(conjunction)].strip())
            if core_forms is not None:
                return {
                    key: f"{value}{conjunction}"
                    for key, value in core_forms.items()
                }
    if "," in text:
        segments = [segment.strip() for segment in text.split(",") if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: ", ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
    if " või " in text:
        segments = [segment.strip() for segment in re.split(r"\s+või\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " või ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
    if " ning " in text:
        segments = [segment.strip() for segment in re.split(r"\s+ning\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " ning ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
    if " ja " in text:
        segments = [segment.strip() for segment in re.split(r"\s+ja\s+", text) if segment.strip()]
        if len(segments) >= 2:
            segment_forms = [_ee_phrase_forms(segment) for segment in segments]
            if all(forms is not None for forms in segment_forms):
                shared_keys = set.intersection(*(set(forms.keys()) for forms in segment_forms if forms is not None))
                if shared_keys:
                    return {
                        key: " ja ".join(forms[key] for forms in segment_forms if forms is not None)
                        for key in shared_keys
                    }
    if " " not in text:
        return _ee_declension_forms(text)

    def _ee_modifier_forms(token: str) -> dict[str, str] | None:
        if token.endswith("ikud"):
            base = token[:-2]
            return {
                "pl_nom": token,
                "pl_gen": base + "e",
                "pl_part": base + "ke",
                "pl_ine": base + "es",
                "pl_ela": base + "est",
                "pl_all": base + "ele",
                "pl_ade": base + "el",
                "pl_abl": base + "elt",
                "pl_trn": base + "eks",
            }
        if token.endswith("tev"):
            stem = token[:-2]
            return {
                "sg_nom": token,
                "sg_gen": stem + "va",
                "sg_part": stem + "vat",
                "sg_ine": stem + "vas",
                "sg_ela": stem + "vast",
                "sg_all": stem + "vale",
                "sg_ade": stem + "val",
                "sg_abl": stem + "valt",
                "sg_trn": stem + "vaks",
                "sg_ter": stem + "vani",
                "sg_ess": stem + "vana",
                "sg_abe": stem + "vata",
                "sg_com": stem + "vaga",
                "pl_nom": stem + "vad",
                "pl_gen": stem + "vate",
                "pl_part": stem + "vaid",
                "pl_ine": stem + "vates",
                "pl_ela": stem + "vatest",
                "pl_all": stem + "vatele",
                "pl_ade": stem + "vatel",
                "pl_abl": stem + "vatelt",
                "pl_trn": stem + "vateks",
            }
        if token.endswith("tud") or token.endswith("dud"):
            return {
                key: token
                for key in (
                    "sg_nom",
                    "sg_gen",
                    "sg_part",
                    "sg_ine",
                    "sg_ela",
                    "sg_all",
                    "sg_ade",
                    "sg_abl",
                    "sg_trn",
                    "sg_ter",
                    "sg_ess",
                    "sg_abe",
                    "sg_com",
                    "pl_nom",
                    "pl_gen",
                    "pl_part",
                    "pl_ine",
                    "pl_ela",
                    "pl_all",
                    "pl_ade",
                    "pl_abl",
                    "pl_trn",
                )
            }
        if token.endswith("v"):
            return {
                "sg_nom": token,
                "sg_gen": token + "a",
                "sg_part": token + "at",
                "sg_ine": token + "as",
                "sg_ela": token + "ast",
                "sg_all": token + "ale",
                "sg_ade": token + "al",
                "sg_abl": token + "alt",
                "sg_trn": token + "aks",
                "sg_ter": token + "ani",
                "sg_ess": token + "ana",
                "sg_abe": token + "ata",
                "sg_com": token + "aga",
                "pl_nom": token + "ad",
                "pl_gen": token + "ate",
                "pl_part": token + "aid",
                "pl_ine": token + "ates",
                "pl_ela": token + "atest",
                "pl_all": token + "atele",
                "pl_ade": token + "atel",
                "pl_abl": token + "atelt",
                "pl_trn": token + "ateks",
            }
        if token.endswith("ik") or token.endswith("line"):
            return _ee_declension_forms(token)
        return None

    parts = text.split()
    head_forms = _ee_declension_forms(parts[-1])
    if head_forms is None:
        return None
    if len(parts) == 1:
        return head_forms

    token_forms: list[dict[str, str]] = []
    for token in parts[:-1]:
        forms = _ee_modifier_forms(token)
        if forms is None:
            forms = {key: token for key in head_forms.keys()}
        token_forms.append(forms)

    shared_keys = set(head_forms.keys())
    for forms in token_forms:
        shared_keys &= set(forms.keys())
    if not shared_keys:
        return None

    combined = {key: " ".join([*(forms[key] for forms in token_forms), head_forms[key]]) for key in shared_keys}
    if token_forms and "sg_gen" in token_forms[-1] and "sg_com" in head_forms:
        prefix_parts = [
            forms["sg_gen"] if idx == len(token_forms) - 1 else forms.get("sg_com", forms.get("sg_gen", ""))
            for idx, forms in enumerate(token_forms)
        ]
        if all(prefix_parts):
            combined["sg_com"] = " ".join([*prefix_parts, head_forms["sg_com"]])
    return combined


def _ee_law_reference_l6ige_forms(text: str) -> dict[str, str] | None:
    """Return common inflected forms for ``§ ... lõige/lõiked ...`` references."""
    cleaned = _ee_normalize_text_replace_surface(text)
    match = re.fullmatch(r"(§\s*[\d\s_]+)\s+(lõige|lõiked)\s+(.+)", cleaned)
    if match is None:
        return None
    prefix, head, tail = match.groups()
    if head == "lõige":
        return {
            "nom": f"{prefix} lõige {tail}",
            "gen": f"{prefix} lõike {tail}",
            "part": f"{prefix} lõiget {tail}",
            "ine": f"{prefix} lõikes {tail}",
            "ela": f"{prefix} lõikest {tail}",
            "ill": f"{prefix} lõikesse {tail}",
            "all": f"{prefix} lõikele {tail}",
            "ade": f"{prefix} lõikel {tail}",
            "abl": f"{prefix} lõikelt {tail}",
            "trn": f"{prefix} lõikeks {tail}",
            "ter": f"{prefix} lõikeni {tail}",
            "ess": f"{prefix} lõikena {tail}",
            "abe": f"{prefix} lõiketa {tail}",
            "com": f"{prefix} lõikega {tail}",
        }
    return {
        "nom": f"{prefix} lõiked {tail}",
        "gen": f"{prefix} lõigete {tail}",
        "part": f"{prefix} lõikeid {tail}",
        "ine": f"{prefix} lõigetes {tail}",
        "ela": f"{prefix} lõigetest {tail}",
        "ill": f"{prefix} lõigetesse {tail}",
        "all": f"{prefix} lõigetele {tail}",
        "ade": f"{prefix} lõigetel {tail}",
        "abl": f"{prefix} lõigetelt {tail}",
        "trn": f"{prefix} lõigeteks {tail}",
    }


def _ee_normalize_text_replace_surface(text: str) -> str:
    """Normalize RT spacing artifacts for text-replace matching/output."""
    normalized = re.sub(r"(?<=\d)\s*[–-]\s*(?=\d)", "–", text)
    normalized = re.sub(
        r"(?<=[A-Za-zÄÖÕÜäöõüŠŽšž])\s*-\s*(?=[A-Za-zÄÖÕÜäöõüŠŽšž])",
        "-",
        normalized,
    )
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r" +([.,;:!?)])", r"\1", normalized)
    return normalized.strip()


def _ee_text_replace_variants(old: str, new: str, *, case_inflected: bool) -> list[tuple[str, str]]:
    """Build replacement pairs, longest-first, for bounded case-aware rewrites."""
    variants: dict[str, str] = {}
    if old:
        variants[old] = new
        old_norm = _ee_normalize_text_replace_surface(old)
        new_norm = _ee_normalize_text_replace_surface(new)
        if old_norm and old_norm not in variants:
            variants[old_norm] = new_norm
        if any(char in old for char in "„“”"):
            guillemet_old = old.replace("„", "«").replace("“", "«").replace("”", "»")
            guillemet_new = new.replace("„", "«").replace("“", "«").replace("”", "»")
            if guillemet_old and guillemet_old not in variants:
                variants[guillemet_old] = guillemet_new
    citation_match = re.fullmatch(r"§\s+(.+)", old.strip())
    new_citation_match = re.fullmatch(r"§\s+(.+)", new.strip())
    if citation_match is not None and new_citation_match is not None:
        old_ref = citation_match.group(1).strip()
        new_ref = new_citation_match.group(1).strip()
        for suffix in ("s", "st", "le", "l", "lt", "ni", "na", "ta", "ga"):
            old_variant = f"§-{suffix} {old_ref}"
            if old_variant not in variants:
                variants[old_variant] = f"§-{suffix} {new_ref}"
    if old == "teabevaldajale" and new == "töötlevale üksusele ja juurdepääsuõigusega füüsilisele isikule":
        special_pairs = {
            "töötlevale üksusele": new,
        }
        for old_form, new_form in special_pairs.items():
            if old_form not in variants:
                variants[old_form] = new_form
    if old == "teabevaldaja" and new == "töötlev üksus ja juurdepääsuõigusega füüsiline isik":
        special_pairs = {
            "töötlev üksus": new,
        }
        for old_form, new_form in special_pairs.items():
            if old_form not in variants:
                variants[old_form] = new_form
    if case_inflected:
        old_l6ige_forms = _ee_law_reference_l6ige_forms(old)
        new_l6ige_forms = _ee_law_reference_l6ige_forms(new)
        if old_l6ige_forms is not None and new_l6ige_forms is not None:
            shared_case_keys = set(old_l6ige_forms) & set(new_l6ige_forms)
            for key in shared_case_keys:
                old_form = old_l6ige_forms[key]
                new_form = new_l6ige_forms[key]
                if old_form and new_form and old_form not in variants:
                    variants[old_form] = new_form
        old_forms = _ee_phrase_forms(old)
        new_forms = _ee_phrase_forms(new)
        if new == "":
            stripped_old = old.strip()
            for conj in (" või", " ja"):
                if stripped_old.endswith(conj):
                    base_forms = _ee_phrase_forms(stripped_old[: -len(conj)].strip())
                    if base_forms is not None:
                        for old_form in base_forms.values():
                            candidate = f"{old_form}{conj}"
                            if candidate and candidate not in variants:
                                variants[candidate] = ""
            if old_forms is not None:
                for old_form in old_forms.values():
                    if old_form and old_form not in variants:
                        variants[old_form] = ""
            tail_match = re.search(
                r"^(.*?)([A-Za-zÄÖÕÜäöõüŠŽšž-]+)$",
                old,
            )
            if tail_match is not None:
                prefix = tail_match.group(1)
                head = tail_match.group(2)
                head_forms = _ee_declension_forms(head)
                if head_forms is not None:
                    for head_form in head_forms.values():
                        candidate = f"{prefix}{head_form}"
                        if candidate and candidate not in variants:
                            variants[candidate] = ""
        elif old_forms is not None and new_forms is not None:
            preferred_keys = (
                "sg_nom",
                "sg_gen",
                "sg_part",
                "sg_ine",
                "sg_ela",
                "sg_ill",
                "sg_all",
                "sg_ade",
                "sg_abl",
                "sg_trn",
                "sg_ter",
                "sg_ess",
                "sg_abe",
                "sg_com",
                "pl_nom",
                "pl_gen",
                "pl_part",
                "pl_ine",
                "pl_ela",
                "pl_all",
                "pl_ade",
                "pl_abl",
                "pl_trn",
            )
            for key in preferred_keys:
                old_form = old_forms.get(key)
                new_form = new_forms.get(key)
                if old_form and new_form and old_form not in variants:
                    variants[old_form] = new_form
                if old_form and new_form:
                    old_form_norm = _ee_normalize_text_replace_surface(old_form)
                    new_form_norm = _ee_normalize_text_replace_surface(new_form)
                    if old_form_norm and old_form_norm not in variants:
                        variants[old_form_norm] = new_form_norm
        if (
            old == "rahvusvaheline konventsioon tsiviilvastutusest naftareostuskahjude eest, 1969"
            and new
            == "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvaheline konventsioon"
        ):
            special_pairs = {
                "rahvusvahelise konventsiooni tsiviilvastutusest naftareostuskahjude eest, 1969": (
                    "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvahelise konventsiooni"
                ),
                "rahvusvahelisest konventsioonist tsiviilvastutusest naftareostuskahjude eest, 1969": (
                    "naftareostusest põhjustatud kahju korral kehtiva tsiviilvastutuse 1992. aasta rahvusvahelisest konventsioonist"
                ),
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "laeva omanik," and new == "":
            for old_form in ("laeva omanikul,", "laeva omaniku,", "laeva omanik,"):
                if old_form not in variants:
                    variants[old_form] = ""
        if old == "sõjarelvad, laskemoon" and new == "sõjarelv, relvasüsteem, sõjarelva laskemoon":
            special_pairs = {
                "sõjarelvade, laskemoona": "sõjarelvade, relvasüsteemi, sõjarelva laskemoona",
                "sõjarelvi, laskemoona": "sõjarelvi, relvasüsteemi, sõjarelva laskemoona",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if (
            old
            in {
                "asutus, põhiseaduslik institutsioon või juriidiline isik",
                "asutus, põhiseaduslik institutsioon ja juriidiline isik",
            }
            and new == "töötlev üksus"
        ):
            joiner = " või " if " või " in old else " ja "
            special_pairs = {
                f"asutuse, põhiseadusliku institutsiooni{joiner}juriidilise isiku": "töötleva üksuse",
                f"asutusele, põhiseaduslikule institutsioonile{joiner}juriidilisele isikule": "töötlevale üksusele",
                f"asutusel, põhiseaduslikul institutsioonil{joiner}juriidilisel isikul": "töötleval üksusel",
                f"asutuses, põhiseaduslikus institutsioonis{joiner}juriidilises isikus": "töötlevas üksuses",
                "asutuste, põhiseaduslike institutsioonide ning füüsiliste ja juriidiliste isikute": "töötlevate üksuste",
                "asutusi, põhiseaduslikke institutsioone ning füüsilisi ja juriidilisi isikuid": "töötlevaid üksusi",
                "asutustele, põhiseaduslikele institutsioonidele ning füüsilistele ja juriidilistele isikutele": "töötlevatele üksustele",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "teabevaldaja" and new == "töötlev üksus":
            special_pairs = {
                "teabevaldaja turvaala": "töötleva üksuse turvaala",
                "teabevaldaja turvaalal": "töötleva üksuse turvaalal",
                "teabevaldaja arhiivis": "töötleva üksuse arhiivis",
                "teabevaldaja seadusest": "töötleva üksuse seadusest",
                "teabevaldaja kohustused": "töötleva üksuse kohustused",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "teabevaldaja" and new == "töötlev üksus ja juurdepääsuõigusega füüsiline isik":
            special_pairs = {
                "töötlev üksus": new,
                "töötleva üksuse": "töötleva üksuse ja juurdepääsuõigusega füüsilise isiku",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "abikaasa" and new == "abikaasa või registreeritud elukaaslane":
            special_pairs = {
                "teise abikaasa": "teise abikaasa või registreeritud elukaaslase",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "ametikoht, millel töötamise" and new == "töö- või ametikoht, mille ülesannete täitmise":
            special_pairs = {
                "ametikohad, millel töötamise": "töö- või ametikohad, mille ülesannete täitmise",
                "ametikohal, millel töötamise": "töö- või ametikohal, mille ülesannete täitmise",
                "ametikohale, millel töötamise": "töö- või ametikohale, mille ülesannete täitmise",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "Kaitsevägi" and new == "Kaitseministeeriumi valitsemisala valitsusasutus":
            special_pairs = {
                "Kaitseväe kaudu": "Kaitseministeeriumi valitsemisala valitsusasutuse kaudu",
                "Kaitseväge": "Kaitseministeeriumi valitsemisala valitsusasutust",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
        if old == "veekogu" and new == "meri":
            special_pairs = {
                "süvendatakse veekogu": "süvendatakse merd",
                "paigutatakse veekogu põhja": "paigutatakse mere põhja",
            }
            for old_form, new_form in special_pairs.items():
                if old_form not in variants:
                    variants[old_form] = new_form
    return sorted(variants.items(), key=lambda item: len(item[0]), reverse=True)


def _ee_insert_matches_existing_node(target_node: IRNode, new_node: IRNode) -> bool:
    """Return True when an insert payload is already fully present at the target slot."""
    if target_node.kind != new_node.kind or target_node.label != new_node.label:
        return False
    if _ee_normalize_text_replace_surface(target_node.text) != _ee_normalize_text_replace_surface(new_node.text):
        return False
    if not new_node.children:
        return True
    for child in new_node.children:
        if child not in target_node.children:
            return False
    return True


def _ee_surface_pattern(text: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_surface_pattern(text)


def _ee_wrap_word_boundaries(pattern: str, text: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_wrap_word_boundaries(pattern, text)


def _ee_replace_case_preserving(
    text: str,
    old: str,
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    return _tm_replace_case_preserving(
        text,
        old,
        new,
        capitalize_sentence_start=capitalize_sentence_start,
        preserve_match_capital=preserve_match_capital,
    )


def _ee_case_preserved_replacement(
    match: re.Match[str],
    new: str,
    *,
    capitalize_sentence_start: bool = True,
    preserve_match_capital: bool = False,
) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.text_morphology``."""
    replacement = _tm_case_preserved_replacement(
        match,
        new,
        capitalize_sentence_start=capitalize_sentence_start,
        preserve_match_capital=preserve_match_capital,
    )
    matched = match.group(0)
    if (
        replacement
        and match.start() > 0
        and matched
        and matched[0] in ",;:"
        and not match.string[match.start() - 1].isspace()
        and replacement[0].isalnum()
    ):
        return f" {replacement.lstrip()}"
    return replacement


def _ee_replace_ambiguous_genitive_phrase(text: str, old: str, new: str) -> str:
    """Handle bounded sg_nom/sg_gen ambiguity before narrow modifier contexts."""
    pattern = re.compile(
        r"(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])"
        + _ee_surface_pattern(old)
        + r"(?![A-Za-zÄÖÕÜäöõüŠŽšž-])",
        re.IGNORECASE,
    )

    genitive_prefix_cues = {
        "iga",
        "kelle",
        "mille",
        "oma",
        "selle",
        "teise",
        "tema",
        "uue",
        "ühe",
        "uhe",
        "üks",
        "uks",
        "ühe",
        "üks",
    }
    nonfinite_suffixes = ("takse", "dakse")
    likely_verb_words = {
        "ei",
        "oli",
        "olnud",
        "on",
        "saab",
        "soovib",
        "teeb",
        "võib",
    }

    def _preceding_word(prefix_text: str) -> str:
        match = re.search(r"([A-Za-zÄÖÕÜäöõüŠŽšž-]+)\s*$", prefix_text)
        if match is None:
            return ""
        return match.group(1).lower()

    def _next_word_and_prefix(suffix_text: str) -> tuple[str, str]:
        words = _next_words(suffix_text)
        if not words:
            return "", ""
        if words[0] in {"või", "ja", "ning"} and len(words) >= 2:
            return words[0], words[1]
        return "", words[0]

    def _next_words(suffix_text: str) -> tuple[str, ...]:
        return tuple(
            match.group(1).lower()
            for match in re.finditer(r"([A-Za-zÄÖÕÜäöõüŠŽšž-]+)", suffix_text)
        )

    def _looks_like_finite_verb(word: str) -> bool:
        if not word:
            return False
        if word in likely_verb_words:
            return True
        if word.endswith(nonfinite_suffixes):
            return False
        if word.endswith("vad"):
            return True
        if word.endswith("b"):
            return True
        return False

    def _has_active_finite_verb_since_sentence_start(prefix_text: str) -> bool:
        clause = re.split(r"[.!?;:]", prefix_text)[-1]
        return any(_looks_like_finite_verb(word) for word in _next_words(clause))

    def _genitive_context(prefix_text: str, suffix_text: str) -> bool:
        joiner, next_word = _next_word_and_prefix(suffix_text)
        if next_word in {"poolt", "taotlusel"}:
            return True
        if suffix_text.lstrip().startswith(","):
            return False

        preceding_word = _preceding_word(prefix_text)
        next_words = _next_words(suffix_text)
        semantic_next_words = next_words[1:] if joiner and next_words else next_words
        if preceding_word == "on":
            return False
        if preceding_word == "arvates":
            return False
        if preceding_word.endswith(nonfinite_suffixes):
            return False
        if _looks_like_finite_verb(preceding_word):
            return False
        if re.match(
            r"\s*käesoleva\s+(?:seaduse|paragrahvi)\s+§",
            suffix_text,
            re.IGNORECASE,
        ):
            return False

        if not next_word:
            return False
        if next_word == "kohustatud":
            return False
        if joiner and not (
            len(semantic_next_words) >= 2
            and re.fullmatch(r"[A-Za-zÄÖÕÜäöõüŠŽšž-]+se", semantic_next_words[0])
            and semantic_next_words[1] not in {"ja", "ning", "või"}
            and not _looks_like_finite_verb(semantic_next_words[1])
        ):
            return False
        if _looks_like_finite_verb(next_word):
            return False
        if not prefix_text.strip() and len(next_words) == 1:
            return True
        if (
            len(semantic_next_words) >= 2
            and re.fullmatch(r"[A-Za-zÄÖÕÜäöõüŠŽšž-]+se", semantic_next_words[0])
            and semantic_next_words[1] not in {"ja", "ning", "või"}
            and not _looks_like_finite_verb(semantic_next_words[1])
        ):
            return True
        if re.match(r"[A-Za-zÄÖÕÜäöõüŠŽšž-]*(?:v|va|vas|vast|vate|vaks|vasse|tud|dud)$", next_word):
            return True

        stripped_prefix = prefix_text.rstrip()
        if joiner == "ja" and stripped_prefix.endswith((",", ";", ":")):
            return False
        if (
            stripped_prefix.endswith(",")
            and next_word == "käskkirjaga"
            and _has_active_finite_verb_since_sentence_start(prefix_text)
        ):
            return False
        if stripped_prefix.endswith((",", ";", ":")):
            return True
        if preceding_word in genitive_prefix_cues:
            return True
        if joiner and preceding_word in genitive_prefix_cues:
            return True
        return False

    def _repl(match: re.Match[str]) -> str:
        prefix_text = text[:match.start()]
        suffix_text = text[match.end():]
        if not _genitive_context(prefix_text, suffix_text):
            return match.group(0)
        matched = match.group(0)
        if matched and matched[0].isupper() and new:
            return new[0].upper() + new[1:]
        return new

    return pattern.sub(_repl, text)


def _ee_replace_ambiguous_partitive_object(text: str, genitive: str, partitive: str) -> str:
    """Handle bounded object-position fixes when old sg_gen and sg_part coincide."""
    if not genitive or not partitive or genitive == partitive:
        return text
    pattern = re.compile(
        _ee_wrap_word_boundaries(_ee_surface_pattern(genitive), genitive),
        re.IGNORECASE,
    )

    def _repl(match: re.Match[str]) -> str:
        prefix_text = text[:match.start()]
        clause_prefix = re.split(r"[.!?;:]", prefix_text)[-1]
        if not (
            re.search(r"\bteavitab\s*$", clause_prefix, re.IGNORECASE)
            or re.search(r"\bhoiule\b", clause_prefix, re.IGNORECASE)
        ):
            return match.group(0)
        noun = match.group(0)
        replacement = partitive
        if noun and noun[0].isupper() and replacement:
            replacement = replacement[0].upper() + replacement[1:]
        return replacement

    return pattern.sub(_repl, text)


def _ee_is_coordination_expansion_of_old(old: str, new: str) -> bool:
    """Return True when ``new`` starts by reusing ``old`` as a coordination head."""
    if not old or not new:
        return False
    return re.match(
        _ee_wrap_word_boundaries(_ee_surface_pattern(old), old) + r"\s+(?:või|ja|ning)\b",
        new,
        re.IGNORECASE,
    ) is not None


def _ee_should_preserve_match_capital(old: str, new: str, *, case_inflected: bool) -> bool:
    """Return True when case-aware rewrites should mirror a capitalized match surface."""
    if not case_inflected:
        return False
    new_norm = _ee_normalize_text_replace_surface(new)
    if new_norm[:1].isupper():
        return True
    old_norm = _ee_normalize_text_replace_surface(old)
    if re.fullmatch(r"[A-ZÄÖÕÜŠŽ][A-Za-zÄÖÕÜäöõüŠŽšž-]+", old_norm) and re.search(
        r"(?:amet|inspektsioon|keskus|koda|komisjon|ministeerium|teenistus)$",
        old_norm,
        re.IGNORECASE,
    ):
        return False
    return True


def _ee_match_inside_existing_replacement(
    text: str,
    *,
    match_start: int,
    match_end: int,
    replacement: str,
) -> bool:
    """Return True when the matched span already sits inside an existing replacement."""
    replacement_norm = _ee_normalize_text_replace_surface(replacement)
    if not replacement_norm:
        return False
    candidate = text[match_start:match_end]
    if candidate.lower() == replacement_norm.lower() and candidate != replacement_norm:
        return False
    lowered_text = text.lower()
    lowered_replacement = replacement_norm.lower()
    start = 0
    while True:
        found = lowered_text.find(lowered_replacement, start)
        if found == -1:
            return False
        if found <= match_start and match_end <= found + len(lowered_replacement):
            return True
        start = found + 1


def _ee_trim_overlapping_replacement_tail(
    replacement: str,
    following_text: str,
) -> str:
    """Trim a replacement tail already present immediately after the match."""
    if not replacement or not following_text:
        return replacement
    max_overlap = min(len(replacement), len(following_text))
    for overlap in range(max_overlap, 0, -1):
        if replacement[-overlap:].lower() == following_text[:overlap].lower():
            return replacement[:-overlap]
    return replacement


def _ee_apply_text_replace_value(
    text: str | None,
    old: str,
    new: str,
    *,
    mode: str = "replace",
    case_inflected: bool,
    all_occurrences: bool = False,
    capitalize_sentence_start: bool = True,
) -> str | None:
    """Apply one EE text replacement to a string value."""
    if text is None or not old:
        return text
    replaced = text
    insert_after_style = mode == "insert_after"
    preserve_match_capital = _ee_should_preserve_match_capital(
        old,
        new,
        case_inflected=case_inflected,
    )
    if case_inflected:
        old_forms = _ee_phrase_forms(old)
        new_forms = _ee_phrase_forms(new)
        if (
            not insert_after_style
            and old_forms is not None
            and new_forms is not None
            and old_forms.get("sg_nom")
            and old_forms.get("sg_nom") == old_forms.get("sg_gen")
            and new_forms.get("sg_nom")
            and new_forms.get("sg_gen")
            and new_forms.get("sg_nom") != new_forms.get("sg_gen")
            and not _ee_is_coordination_expansion_of_old(
                old_forms["sg_nom"],
                new_forms["sg_nom"],
            )
            and not (old == "veekogu" and new == "meri")
        ):
            replaced = _ee_replace_ambiguous_genitive_phrase(
                replaced,
                old_forms["sg_nom"],
                new_forms["sg_gen"],
            )
        if (
            old_forms is not None
            and new_forms is not None
            and old_forms.get("sg_gen")
            and old_forms.get("sg_part")
            and old_forms["sg_gen"] == old_forms["sg_part"]
            and new_forms.get("sg_gen")
            and new_forms.get("sg_part")
            and new_forms["sg_gen"] != new_forms["sg_part"]
        ):
            pass
    variants = _ee_text_replace_variants(
        old,
        new,
        case_inflected=case_inflected,
    )
    if insert_after_style:
        working = replaced
        placeholders: dict[str, str] = {}
        replaced_once = False
        for idx, (old_variant, new_variant) in enumerate(variants):
            pattern = re.compile(
                _ee_wrap_word_boundaries(_ee_surface_pattern(old_variant), old_variant),
                re.IGNORECASE,
            )
            if all_occurrences:
                def _repl(match: re.Match[str], *, idx: int = idx, new_variant: str = new_variant) -> str:
                    token = f"\x00ee-after-{idx}-{len(placeholders)}\x00"
                    normalized_new_variant = _ee_normalize_text_replace_surface(new_variant)
                    normalized_old_variant = _ee_normalize_text_replace_surface(old_variant)
                    replacement = (
                        match.group(0) + normalized_new_variant[len(normalized_old_variant):]
                        if normalized_new_variant.lower().startswith(normalized_old_variant.lower())
                        else _ee_case_preserved_replacement(
                            match,
                            normalized_new_variant,
                            capitalize_sentence_start=capitalize_sentence_start,
                            preserve_match_capital=preserve_match_capital,
                        )
                    )
                    replacement = _ee_trim_overlapping_replacement_tail(
                        replacement,
                        match.string[match.end():],
                    )
                    placeholders[token] = replacement if replacement else match.group(0)
                    return token

                working, count = pattern.subn(_repl, working)
                replaced_once = replaced_once or count > 0
                continue
            match = pattern.search(working)
            if match is None:
                continue
            normalized_new_variant = _ee_normalize_text_replace_surface(new_variant)
            normalized_old_variant = _ee_normalize_text_replace_surface(old_variant)
            replacement = (
                match.group(0) + normalized_new_variant[len(normalized_old_variant):]
                if normalized_new_variant.lower().startswith(normalized_old_variant.lower())
                else _ee_case_preserved_replacement(
                    match,
                    normalized_new_variant,
                    capitalize_sentence_start=capitalize_sentence_start,
                    preserve_match_capital=preserve_match_capital,
                )
            )
            replacement = _ee_trim_overlapping_replacement_tail(
                replacement,
                working[match.end():],
            )
            if not replacement:
                replacement = match.group(0)
            working = working[:match.start()] + replacement + working[match.end():]
            replaced_once = True
            break
        for token, value in placeholders.items():
            working = working.replace(token, value)
        if replaced_once:
            replaced = working
    else:
        placeholders: dict[str, str] = {}
        working = replaced
        for idx, (old_variant, new_variant) in enumerate(variants):
            pattern = re.compile(
                _ee_wrap_word_boundaries(_ee_surface_pattern(old_variant), old_variant),
                re.IGNORECASE,
            )

            def _repl(match: re.Match[str], *, idx: int = idx, new_variant: str = new_variant) -> str:
                if mode == "replace" and _ee_match_inside_existing_replacement(
                    working,
                    match_start=match.start(),
                    match_end=match.end(),
                    replacement=new_variant,
                ):
                    return match.group(0)
                token = f"\x00ee-repl-{idx}-{len(placeholders)}\x00"
                replacement = _ee_case_preserved_replacement(
                    match,
                    new_variant,
                    capitalize_sentence_start=capitalize_sentence_start,
                    preserve_match_capital=preserve_match_capital,
                )
                if (
                    mode == "replace"
                    and new_variant.lower().startswith(old_variant.lower())
                ):
                    replacement = _ee_trim_overlapping_replacement_tail(
                        replacement,
                        working[match.end():],
                    )
                placeholders[token] = replacement
                return token

            working = pattern.sub(_repl, working)
        for token, value in placeholders.items():
            working = working.replace(token, value)
        replaced = working
    if case_inflected:
        old_forms = _ee_phrase_forms(old)
        new_forms = _ee_phrase_forms(new)
        if (
            new_forms is not None
            and new_forms.get("sg_nom")
            and new_forms.get("sg_gen")
            and new_forms["sg_nom"] != new_forms["sg_gen"]
        ):
            replaced = _ee_replace_ambiguous_genitive_phrase(
                replaced,
                new_forms["sg_nom"],
                new_forms["sg_gen"],
            )
        if (
            old_forms is not None
            and new_forms is not None
            and old_forms.get("sg_gen")
            and old_forms.get("sg_part")
            and old_forms["sg_gen"] == old_forms["sg_part"]
            and new_forms.get("sg_gen")
            and new_forms.get("sg_part")
            and new_forms["sg_gen"] != new_forms["sg_part"]
        ):
            replaced = _ee_replace_ambiguous_partitive_object(
                replaced,
                new_forms["sg_gen"],
                new_forms["sg_part"],
            )
    if new == "" and text and text[:1].isupper() and replaced:
        replaced = re.sub(
            r"^(\s*)([a-zäöõüšž])",
            lambda m: m.group(1) + m.group(2).upper(),
            replaced,
            count=1,
        )
        replaced = re.sub(
            r"([A-Za-zÄÖÕÜäöõüŠŽšž][.!?]\s+)([a-zäöõüšž])",
            lambda m: m.group(1) + m.group(2).upper(),
            replaced,
        )
    if new == "" and old.lstrip().startswith(","):
        replaced = re.sub(
            r"\b([A-Za-zÄÖÕÜäöõüŠŽšž-]+)\s+\1\b",
            r"\1",
            replaced,
        )
    if replaced != text:
        replaced = re.sub(r"  +", " ", replaced)
        replaced = re.sub(r" +([.,;:!?)])", r"\1", replaced)
        replaced = re.sub(r"\.\.(?=\s|$)", ".", replaced)
        replaced = re.sub(r",\s*,", ",", replaced)
        return replaced.strip()
    return text


def _ee_chapter_in_scope(chapter_label: str | None, scope_chapters: object) -> bool:
    """Return True when a current chapter label falls inside a scoped rewrite."""
    if chapter_label is None:
        return False
    if not isinstance(scope_chapters, (list, tuple, set)):
        return False
    return chapter_label in {str(label) for label in scope_chapters}


def _shift_numbered_subsections(parent: IRNode, from_label: str) -> IRNode:
    """Shift numeric subsection labels upward starting from ``from_label``."""
    start = _try_parse_int(from_label)
    if start is None:
        return parent

    new_children: list[IRNode] = []
    changed = False
    for child in parent.children:
        if child.kind != IRNodeKind.SUBSECTION:
            new_children.append(child)
            continue
        child_num = _try_parse_int(child.label) if child.label is not None else None
        if child_num is not None and child_num >= start:
            changed = True
            new_children.append(
                IRNode(
                    kind=child.kind,
                    label=str(child_num + 1),
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
        else:
            new_children.append(child)
    if not changed:
        return parent
    return IRNode(
        kind=parent.kind,
        label=parent.label,
        text=parent.text,
        attrs=dict(parent.attrs),
        children=tuple(new_children),
    )


def _adjust_insert_subsection_label_for_repealed_ranges(parent: IRNode, label: str) -> str:
    """Shift insert labels down after collapsed repealed subsection ranges."""
    if not re.fullmatch(r"\d+", label):
        return label
    target_num = int(label)
    if target_num is None:
        return label

    existing_labels = {child.label for child in parent.children if child.kind == IRNodeKind.SUBSECTION}
    if label in existing_labels:
        return label

    hidden_count = 0
    for child in parent.children:
        if child.kind != IRNodeKind.SUBSECTION:
            continue
        if child.label is None or not re.fullmatch(r"\d+", child.label):
            continue
        child_num = int(child.label)
        if child_num >= target_num:
            continue
        match = re.fullmatch(r"–\((\d+)\)", (child.text or "").strip())
        if not match:
            continue
        range_end = int(match.group(1))
        if range_end < child_num:
            continue
        hidden_count += range_end - child_num

    adjusted_num = target_num - hidden_count
    adjusted = str(adjusted_num)
    if adjusted_num > 0 and adjusted not in existing_labels:
        return adjusted
    return label


def _ee_resolve_full_path(body: IRNode, path: tree_ops.Path) -> Optional[tree_ops.Path]:
    """Resolve a (possibly flat) op path to a full path in the body tree.

    Estonian PEG ops emit flat paths like [('section', '29')] even when the
    statute has chapters (sections nested under chapters).  tree_ops.resolve()
    does strict prefix matching and fails for these flat paths.

    Strategy:
      1. Try exact resolve (works when path is already fully qualified).
      2. If that fails and the path has >= 1 element, use tree_ops.find() on
         the leaf (kind, label) to discover the full path, then append any
         tail elements below the leaf (subsection, item, …).

    Returns the resolved full path, or None if the node cannot be found.
    """
    if tree_ops.resolve(body, path) is not None:
        return path  # exact match

    if not path:
        return None

    # Try to locate the first element via deep find, then resolve the rest
    # relative to that found node.
    for split in range(1, len(path) + 1):
        prefix = path[:split]
        leaf_kind, leaf_label = prefix[-1]
        # Build scope from the part of the path we already know (before split)
        scope_kind = prefix[-2][0] if split > 1 else None
        scope_label = prefix[-2][1] if split > 1 else None
        found_path = tree_ops.find(
            body,
            leaf_kind,
            leaf_label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )
        if found_path is not None:
            # Append any remaining tail elements
            tail = path[split:]
            candidate = found_path + tail
            if tree_ops.resolve(body, candidate) is not None:
                return candidate
            if tail and tail[0][0] == "item" and split == len(path) - 1:
                found_node = tree_ops.resolve(body, found_path)
                if (
                    found_node is not None
                    and found_node.kind == IRNodeKind.SECTION
                    and len(found_node.children) == 1
                    and found_node.children[0].kind == IRNodeKind.SUBSECTION
                ):
                    subsection = found_node.children[0]
                    subsection_candidate = found_path + (("subsection", subsection.label or ""),) + tail
                    if tree_ops.resolve(body, subsection_candidate) is not None:
                        return subsection_candidate
            # tail not resolvable yet — keep trying with longer prefix
            if not tail:
                return found_path

    return None


def _ee_normalize_heading_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _ee_resolve_full_path_by_heading(
    body: IRNode,
    path: tree_ops.Path,
    *,
    heading: str,
) -> Optional[tree_ops.Path]:
    """Resolve duplicate-labeled containers by exact heading/title witness."""
    if not path or not heading:
        return None
    target_kind, target_label = path[-1]
    wanted_heading = _ee_normalize_heading_text(heading)

    def _walk(node: IRNode, prefix: tree_ops.Path) -> Optional[tree_ops.Path]:
        for child in node.children:
            if child.label is None:
                child_path = prefix
            else:
                child_path = prefix + ((str(child.kind), child.label),)
            if (
                str(child.kind) == target_kind
                and child.label == target_label
                and _ee_normalize_heading_text(child.text) == wanted_heading
            ):
                tail = path[1:] if len(path) > 1 and path[0] == (target_kind, target_label) else ()
                candidate = child_path + tail
                if tree_ops.resolve(body, candidate) is not None:
                    return candidate
            found = _walk(child, child_path)
            if found is not None:
                return found
        return None

    return _walk(body, ())


def _ee_remove_full_path_by_heading(
    body: IRNode,
    path: tree_ops.Path,
    *,
    heading: str,
) -> tuple[IRNode | None, IRNode | None]:
    """Remove and return a duplicate-labeled node using exact heading evidence."""
    if not path or not heading:
        return None, None
    target_kind, target_label = path[-1]
    wanted_heading = _ee_normalize_heading_text(heading)

    def _walk(node: IRNode) -> tuple[IRNode | None, IRNode]:
        new_children: list[IRNode] = []
        removed: IRNode | None = None
        for child in node.children:
            if (
                removed is None
                and str(child.kind) == target_kind
                and child.label == target_label
                and _ee_normalize_heading_text(child.text) == wanted_heading
            ):
                removed = child
                continue
            if removed is None:
                child_removed, updated_child = _walk(child)
                if child_removed is not None:
                    removed = child_removed
                    new_children.append(updated_child)
                    continue
            new_children.append(child)
        if removed is None:
            return None, node
        return removed, IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=dict(node.attrs),
            children=tuple(new_children),
        )

    return _walk(body)


def _ee_iter_section_parent_candidates(
    node: IRNode,
    prefix: tree_ops.Path | None = None,
) -> list[tuple[str, tree_ops.Path]]:
    """Collect every labeled section with the full path to its parent container."""
    if prefix is None:
        prefix = ()

    candidates: list[tuple[str, tree_ops.Path]] = []
    for child in node.children:
        child_path = prefix
        if child.label is not None:
            child_path = prefix + ((str(child.kind), child.label),)
        if child.kind == IRNodeKind.SECTION and child.label:
            candidates.append((child.label, prefix))
        candidates.extend(_ee_iter_section_parent_candidates(child, child_path))
    return candidates


def _ee_resolve_parent_path(body: IRNode, path: tree_ops.Path) -> Optional[tree_ops.Path]:
    """Resolve the parent path for an insert op.

    For a flat path like [('section', '29')] the parent is [] (body root),
    which is valid for insert only if sections appear directly under body.
    For chapter-nested statutes, we need to find which chapter would contain
    the sibling section nearest to the target label.

    Returns the full parent path, or None if unresolvable.
    """
    parent_path = path[:-1]
    if not parent_path:
        has_direct_sections = any(c.kind == IRNodeKind.SECTION for c in body.children)

        insert_kind, insert_label = path[-1]
        if insert_kind == "section":
            # Mixed statutes can have both direct body sections and later nested
            # part/chapter/division section families. Resolve against the
            # nearest existing section family before defaulting to body root.
            insert_match = re.match(r"(\d+)", insert_label)
            insert_base = int(insert_match.group(1)) if insert_match else 0
            insert_suffix_str = insert_label.split("_", 1)[1] if "_" in insert_label else ""
            insert_suffix_int = int(insert_suffix_str) if insert_suffix_str.isdigit() else -1

            def _predecessor_rank(sec_label: str) -> int:
                """Rank same-base predecessor sections for superscript inserts."""
                sec_match = re.match(r"(\d+)", sec_label)
                sec_base = int(sec_match.group(1)) if sec_match else -1
                if sec_base != insert_base or insert_suffix_int < 0:
                    return -1
                sec_suffix_str = sec_label.split("_", 1)[1] if "_" in sec_label else ""
                sec_suffix_int = int(sec_suffix_str) if sec_suffix_str.isdigit() else 0
                if sec_suffix_int < insert_suffix_int:
                    return sec_suffix_int
                return -1

            candidates = _ee_iter_section_parent_candidates(body)

            same_base_predecessors: list[tuple[int, tree_ops.Path]] = []
            best_path: Optional[tree_ops.Path] = None
            best_dist = float("inf")
            best_predecessor_rank = -2

            for sec_label, candidate_parent_path in candidates:
                pred_rank = _predecessor_rank(sec_label)
                if pred_rank >= 0:
                    same_base_predecessors.append((pred_rank, candidate_parent_path))

                sec_match = re.match(r"(\d+)", sec_label)
                sec_base = int(sec_match.group(1)) if sec_match else 0
                dist = abs(sec_base - insert_base)
                if dist < best_dist or (dist == best_dist and pred_rank > best_predecessor_rank):
                    best_dist = dist
                    best_predecessor_rank = pred_rank
                    best_path = candidate_parent_path

            if same_base_predecessors:
                same_base_predecessors.sort(key=lambda item: item[0], reverse=True)
                return same_base_predecessors[0][1]
            if best_path is not None:
                return best_path
            if has_direct_sections:
                return ()
            return best_path
        if has_direct_sections:
            return ()
        return ()

    # Non-empty parent path — try full resolution
    full = _ee_resolve_full_path(body, parent_path)
    if full is not None and path and path[-1][0] == "section":
        insert_label = path[-1][1]
        insert_match = re.match(r"(\d+)", insert_label)
        insert_base = int(insert_match.group(1)) if insert_match else 0
        insert_suffix_str = insert_label.split("_", 1)[1] if "_" in insert_label else ""
        insert_suffix_int = int(insert_suffix_str) if insert_suffix_str.isdigit() else -1

        if insert_suffix_int >= 0:
            parent_kinds = {kind for kind, _label in parent_path}
            explicit_structural_parent = bool(
                full and len(parent_path) > 1 and parent_kinds.intersection({"division", "subdivision", "part"})
            )

            def _is_split_continuation_of_explicit(
                explicit_parent: tree_ops.Path,
                candidate_parent: tree_ops.Path,
            ) -> bool:
                if len(explicit_parent) != 1 or len(candidate_parent) != 1:
                    return False
                explicit_kind, explicit_label = explicit_parent[0]
                candidate_kind, candidate_label = candidate_parent[0]
                if explicit_kind != "chapter" or candidate_kind != "chapter":
                    return False
                return candidate_label.startswith(f"{explicit_label}_")

            def _predecessor_rank(sec_label: str) -> int:
                sec_match = re.match(r"(\d+)", sec_label)
                sec_base = int(sec_match.group(1)) if sec_match else -1
                if sec_base != insert_base:
                    return -1
                sec_suffix_str = sec_label.split("_", 1)[1] if "_" in sec_label else ""
                sec_suffix_int = int(sec_suffix_str) if sec_suffix_str.isdigit() else 0
                if sec_suffix_int < insert_suffix_int:
                    return sec_suffix_int
                return -1

            explicit_best = -1
            global_best_rank = -1
            global_best_path: Optional[tree_ops.Path] = None
            for sec_label, candidate_parent_path in _ee_iter_section_parent_candidates(body):
                pred_rank = _predecessor_rank(sec_label)
                if pred_rank < 0:
                    continue
                candidate_parent = list(candidate_parent_path)
                if candidate_parent == list(full):
                    explicit_best = max(explicit_best, pred_rank)
                if pred_rank > global_best_rank:
                    global_best_rank = pred_rank
                    global_best_path = candidate_parent_path

            if (
                not explicit_structural_parent
                and global_best_path is not None
                and global_best_rank > explicit_best
                and (
                    list(full) != list(parent_path)
                    or _is_split_continuation_of_explicit(full, global_best_path)
                )
            ):
                return global_best_path

    if full is not None and path and path[-1][0] == "item":
        parent_node = tree_ops.resolve(body, full)
        if (
            parent_node is not None
            and parent_node.kind == IRNodeKind.SECTION
            and len(parent_node.children) == 1
            and parent_node.children[0].kind == IRNodeKind.SUBSECTION
        ):
            subsection = parent_node.children[0]
            if any(child.kind == IRNodeKind.ITEM for child in subsection.children):
                return full + (("subsection", subsection.label or ""),)
    return full


def _ee_apply_op(
    body: IRNode,
    op: LegalOperation,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> IRNode:
    """Apply one LegalOperation to the body IRNode, returning an updated tree.

    Pure functional — returns a new body; the input is not mutated.
    Handles: replace, repeal, insert, text_replace at section/subsection/item level,
    plus chapter heading rename. Unresolvable or unsupported ops return body unchanged.

    Path resolution: Estonian PEG ops emit flat paths (e.g. section:29) even
    when the statute has chapters.  _ee_resolve_full_path() uses tree_ops.find()
    to locate nodes at any depth, making ops work on chapter-nested statutes.
    """
    path = _address_to_path(op.target)
    action = op.action.value if isinstance(op.action, StructuralAction) else op.action
    payload = op.payload
    special = op.target.special.value if hasattr(op.target.special, "value") else op.target.special
    path_dict = dict(op.target.path)

    # ── Global text_replace: empty path → apply to ALL text nodes ──────────
    if not path and action == "text_replace" and payload is not None:
        parsed_instructions = to_ee_parsed_instructions(
            [op],
            source_rule="estonia/grafter:_ee_apply_op",
        )
        parsed_instruction = parsed_instructions[0] if parsed_instructions else None
        parsed_rewrite = None
        if parsed_instruction is not None:
            parsed_rewrite = (
                parsed_instruction.rewrite_witness.rewrite
                if parsed_instruction.rewrite_witness is not None
                else parsed_instruction.rewrite
            )
        if parsed_rewrite is not None and parsed_rewrite.generic_minister_plural:
            excluded_paths = parsed_rewrite.exclude_paths or None
            return _ee_global_generic_minister_plural_replace(
                body,
                singular_text="valdkonna eest vastutav minister",
                plural_text=(
                    parsed_instruction.payload_text
                    if parsed_instruction is not None and parsed_instruction.payload_text is not None
                    else payload.text or ""
                ).replace("\x01", ""),
                old_titles=parsed_rewrite.old_titles,
                excluded_paths=excluded_paths,
            )
        if parsed_rewrite is None:
            return body
        rewrite_spec = EETextRewriteSpec(
            old_text=parsed_rewrite.old_surface,
            new_text=parsed_rewrite.new_surface,
            mode=parsed_rewrite.mode.value,
            case_inflected=parsed_rewrite.case_inflected,
        )
        old = rewrite_spec.old_text
        if old:
            scope_chapters = parsed_rewrite.scope_chapters or None
            excluded_paths = parsed_rewrite.exclude_paths or None
            if rewrite_spec.case_inflected:

                def _walk(
                    node: IRNode,
                    current_chapter: str | None = None,
                    current_path: tuple[tuple[str, str], ...] = (),
                ) -> IRNode:
                    chapter_label = current_chapter
                    if node.kind == IRNodeKind.CHAPTER:
                        chapter_label = node.label
                    node_path = current_path
                    if node.label is not None:
                        node_path = current_path + ((str(node.kind), node.label),)
                    if _ee_path_is_excluded(node_path, excluded_paths):
                        return node
                    skip_title = node.kind == IRNodeKind.SECTION and bool(node.attrs.get("kehtetu"))
                    in_scope = scope_chapters is None or _ee_chapter_in_scope(chapter_label, scope_chapters)
                    if node.text and not skip_title and in_scope:
                        new_text = _ee_apply_text_replace_spec(
                            node.text,
                            rewrite_spec,
                            capitalize_sentence_start=node.kind != IRNodeKind.ITEM,
                        )
                    else:
                        new_text = node.text
                    new_children = [_walk(c, chapter_label, node_path) for c in node.children]
                    text_changed = new_text != node.text
                    children_changed = any(nc is not oc for nc, oc in zip(new_children, node.children))
                    if not text_changed and not children_changed:
                        return node
                    return IRNode(
                        kind=node.kind,
                        label=node.label,
                        text=new_text or "",
                        attrs=dict(node.attrs),
                        children=tuple(new_children),
                    )

                return _walk(body)
            if scope_chapters is not None or excluded_paths is not None:

                def _walk(
                    node: IRNode,
                    current_chapter: str | None = None,
                    current_path: tuple[tuple[str, str], ...] = (),
                ) -> IRNode:
                    chapter_label = current_chapter
                    if node.kind == IRNodeKind.CHAPTER:
                        chapter_label = node.label
                    node_path = current_path
                    if node.label is not None:
                        node_path = current_path + ((str(node.kind), node.label),)
                    skip_title = node.kind == IRNodeKind.SECTION and bool(node.attrs.get("kehtetu"))
                    in_scope = _ee_chapter_in_scope(chapter_label, scope_chapters)
                    if scope_chapters is None:
                        in_scope = True
                    if _ee_path_is_excluded(node_path, excluded_paths):
                        return node
                    if node.text and not skip_title and in_scope:
                        replaced = _ee_apply_text_replace_spec(
                            node.text,
                            rewrite_spec,
                            capitalize_sentence_start=node.kind != IRNodeKind.ITEM,
                        )
                        if replaced is None:
                            replaced = node.text
                        if replaced != node.text:
                            replaced = re.sub(r"  +", " ", replaced)
                            replaced = re.sub(r" +([.,;:!?)])", r"\1", replaced)
                            replaced = re.sub(r",\s*,", ",", replaced)
                            new_text: Optional[str] = replaced.strip()
                        else:
                            new_text = node.text
                    else:
                        new_text = node.text
                    new_children = [_walk(c, chapter_label, node_path) for c in node.children]
                    text_changed = new_text != node.text
                    children_changed = any(nc is not oc for nc, oc in zip(new_children, node.children))
                    if not text_changed and not children_changed:
                        return node
                    return IRNode(
                        kind=node.kind,
                        label=node.label,
                        text=new_text,
                        attrs=dict(node.attrs),
                        children=tuple(new_children),
                    )

                return _walk(body)
            return _ee_global_text_replace(
                body,
                old,
                rewrite_spec.new_text.replace("\x01", ""),
                excluded_paths=excluded_paths,
            )
        return body

    if not path and not special:
        return body  # statute-level or unresolved target — skip

    if action == "renumber":
        if not path or op.destination is None or not op.destination.path:
            return body
        old_heading = ""
        new_heading = ""
        allow_occupied_destination = False
        if op.payload is not None:
            old_heading = str(op.payload.attrs.get("old_heading", ""))
            new_heading = str(op.payload.attrs.get("new_heading", ""))
            allow_occupied_destination = bool(op.payload.attrs.get("allow_occupied_destination"))
        heading_source_node: IRNode | None = None
        heading_without_source: IRNode | None = None
        if old_heading:
            heading_source_node, heading_without_source = _ee_remove_full_path_by_heading(
                body,
                path,
                heading=old_heading,
            )
        full_path = _ee_resolve_full_path_by_heading(body, path, heading=old_heading) or _ee_resolve_full_path(body, path)
        if full_path is None and heading_source_node is None:
            return body
        source_node = heading_source_node or tree_ops.resolve(body, full_path or ())
        if source_node is None:
            return body

        dest_path = _address_to_path(op.destination)
        dest_full = _ee_resolve_full_path(body, dest_path)
        dest_parent = dest_full[:-1] if dest_full is not None else _ee_resolve_parent_path(body, dest_path)
        if dest_full is None and dest_parent is None:
            return body

        moved_node = IRNode(
            kind=source_node.kind,
            label=dest_path[-1][1],
            text=new_heading or source_node.text,
            attrs=dict(source_node.attrs),
            children=tuple(source_node.children),
        )
        without_source = heading_without_source or tree_ops.remove_at(body, full_path or ())
        if allow_occupied_destination and dest_full is not None:
            return tree_ops.insert_sorted(
                without_source,
                dest_parent or [],
                moved_node,
                sort_key_fn=tree_ops._default_sort_key,
            )
        if dest_full is not None:
            return tree_ops.replace_at(without_source, dest_full, moved_node)
        return tree_ops.insert_sorted(
            without_source,
            dest_parent or [],
            moved_node,
            sort_key_fn=tree_ops._default_sort_key,
        )

    # ── Heading rename (chapter or section) ─────────────────────────────────
    if special == "heading" and payload is not None:
        rewrite_spec = None
        if action == "text_replace":
            parsed_instructions = to_ee_parsed_instructions(
                [op],
                source_rule="estonia/grafter:_ee_apply_op",
            )
            parsed_instruction = parsed_instructions[0] if parsed_instructions else None
            parsed_rewrite = None
            if parsed_instruction is not None:
                parsed_rewrite = (
                    parsed_instruction.rewrite_witness.rewrite
                    if parsed_instruction.rewrite_witness is not None
                    else parsed_instruction.rewrite
                )
            if parsed_rewrite is not None:
                rewrite_spec = EETextRewriteSpec(
                    old_text=parsed_rewrite.old_surface,
                    new_text=parsed_rewrite.new_surface,
                    mode=parsed_rewrite.mode.value,
                    case_inflected=parsed_rewrite.case_inflected,
                )
        if "chapter" in path_dict and "division" in path_dict:
            target_path: list[tuple[str, str]] = []
            if "part" in path_dict:
                target_path.append(("part", path_dict["part"]))
            target_path.append(("chapter", path_dict["chapter"]))
            target_path.append(("division", path_dict["division"]))
            div_path = _ee_resolve_full_path(body, tuple(target_path))
            if div_path is None:
                return body
            div_node = tree_ops.resolve(body, div_path)
            if div_node is None:
                return body
            if action == "text_replace":
                if rewrite_spec is None or not rewrite_spec.old_text:
                    return body
                new_title = _ee_apply_text_replace_spec(
                    div_node.text,
                    rewrite_spec,
                )
                if new_title is None:
                    new_title = div_node.text
            else:
                new_title = payload.text.replace("\x01", "").strip()
                new_title = re.sub(r"^\d[\d\s_]*[.]\s*jagu\s*", "", new_title).strip()
            new_div = IRNode(
                kind=div_node.kind,
                label=div_node.label,
                text=new_title,
                attrs=dict(div_node.attrs),
                children=tuple(div_node.children),
            )
            return tree_ops.replace_at(body, list(div_path), new_div)
        elif "chapter" in path_dict:
            target_path: list[tuple[str, str]] = []
            if "part" in path_dict:
                target_path.append(("part", path_dict["part"]))
            target_path.append(("chapter", path_dict["chapter"]))
            ch_path = _ee_resolve_full_path(body, tuple(target_path))
            if ch_path is None:
                return body
            ch_node = tree_ops.resolve(body, ch_path)
            if ch_node is None:
                return body
            if action == "text_replace":
                if rewrite_spec is None or not rewrite_spec.old_text:
                    return body
                new_title = _ee_apply_text_replace_spec(
                    ch_node.text,
                    rewrite_spec,
                )
                if new_title is None:
                    new_title = ch_node.text
            else:
                # Strip \x01 sentinel and leading "N. peatükk " prefix.
                # The oracle stores only peatykkPealkiri text (without the chapter
                # number); amendment payloads often include it as context.
                new_title = payload.text.replace("\x01", "").strip()
                new_title = re.sub(
                    r"^.*?\b\d[\d\s_]*[.]\s*peatükk\s*",
                    "",
                    new_title,
                    count=1,
                ).strip()
            new_ch = IRNode(
                kind=ch_node.kind,
                label=ch_node.label,
                text=new_title,
                attrs=dict(ch_node.attrs),
                children=tuple(ch_node.children),
            )
            return tree_ops.replace_at(body, list(ch_path), new_ch)
        elif "section" in path_dict:
            sec_label = path_dict["section"]
            sec_path = tree_ops.find(body, "section", sec_label)
            if sec_path is None:
                return body
            sec_node = tree_ops.resolve(body, sec_path)
            if sec_node is None:
                return body
            if action == "text_replace":
                if rewrite_spec is None or not rewrite_spec.old_text:
                    return body
                new_title = _ee_apply_text_replace_spec(
                    sec_node.text,
                    rewrite_spec,
                )
                if new_title is None:
                    new_title = sec_node.text
            else:
                # Strip § N. prefix and \x01 sentinel from payload text
                new_title = re.sub(r"^§\s*\d[\d\s_]*\.\s*", "", payload.text.strip())
                new_title = new_title.replace("\x01", "").strip()
            new_sec = IRNode(
                kind=sec_node.kind,
                label=sec_node.label,
                text=new_title,
                attrs=dict(sec_node.attrs),
                children=tuple(sec_node.children),
            )
            return tree_ops.replace_at(body, sec_path, new_sec)
        else:
            # heading special requires chapter or section in path — warn and skip
            import warnings

            warnings.warn(
                f"_ee_apply_op: special='heading' but path has neither 'chapter' nor 'section': "
                f"path_dict={path_dict!r} op={op.op_id!r}",
                stacklevel=2,
            )

    # ── Section-level and below — path must be non-empty ─────────────────────
    if not path:
        return body

    if action == "repeal":
        if "subdivision" in path_dict and "chapter" in path_dict and "division" in path_dict:
            div_path = _ee_resolve_full_path(
                body,
                (("chapter", path_dict["chapter"]), ("division", path_dict["division"])),
            )
            if div_path is not None:
                division_node = tree_ops.resolve(body, div_path)
                if division_node is not None and division_node.kind == IRNodeKind.DIVISION:
                    subdivision_label = path_dict["subdivision"]
                    matched = False
                    new_children: list[IRNode] = []
                    for child in division_node.children:
                        if child.kind == IRNodeKind.SECTION and child.attrs.get("jaotis") == subdivision_label:
                            matched = True
                            new_children.append(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label=child.label,
                                    text=child.text,
                                    attrs={**dict(child.attrs), "kehtetu": True},
                                    children=(),
                                )
                            )
                        else:
                            new_children.append(child)
                    if matched:
                        new_division = IRNode(
                            kind=IRNodeKind.DIVISION,
                            label=division_node.label,
                            text=division_node.text,
                            attrs=dict(division_node.attrs),
                            children=tuple(new_children),
                        )
                        return tree_ops.replace_at(body, div_path, new_division)
        full_path = _ee_resolve_full_path(body, path)
        if full_path is not None:
            target_node = tree_ops.resolve(body, full_path)
            if target_node is not None and target_node.kind == IRNodeKind.CHAPTER:
                # Chapter-level repeal: "N. peatükk tunnistatakse kehtetuks".
                # RT keeps chapter/division boundary headings as presentation
                # stubs.  Provision bodies under the repealed chapter become
                # empty kehtetu stubs, but container hierarchy remains visible
                # for tree-structured publication diffs.
                def _repealed_container_stub(node: IRNode) -> IRNode:
                    stub_children: list[IRNode] = []
                    for descendant in node.children:
                        if descendant.kind == IRNodeKind.SECTION:
                            stub_children.append(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label=descendant.label,
                                    text=descendant.text,
                                    attrs={**dict(descendant.attrs), "kehtetu": True},
                                    children=(),
                                )
                            )
                        elif descendant.kind in (
                            IRNodeKind.PART,
                            IRNodeKind.CHAPTER,
                            IRNodeKind.DIVISION,
                        ):
                            stub_children.append(_repealed_container_stub(descendant))
                    return IRNode(
                        kind=node.kind,
                        label=node.label,
                        text=node.text,
                        attrs=dict(node.attrs),
                        children=tuple(stub_children),
                    )

                new_children = []
                for child in target_node.children:
                    if child.kind == IRNodeKind.SECTION:
                        new_children.append(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label=child.label,
                                text=child.text,
                                attrs={**dict(child.attrs), "kehtetu": True},
                                children=(),
                            )
                        )
                    elif child.kind in (IRNodeKind.DIVISION, IRNodeKind.PART):
                        new_children.append(_repealed_container_stub(child))
                new_chapter = IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label=target_node.label,
                    text=target_node.text,
                    attrs=dict(target_node.attrs),
                    children=tuple(new_children),
                )
                return tree_ops.replace_at(body, full_path, new_chapter)
            if target_node is not None and target_node.kind == IRNodeKind.DIVISION:
                # Division-level repeal: RT keeps the division heading and
                # preserves each child section as a bare kehtetu stub with its
                # title surface intact, but without subsection content.
                sections = [child for child in target_node.children if child.kind == IRNodeKind.SECTION]
                if not sections:
                    return body
                new_children = [
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label=section.label,
                        text=section.text,
                        attrs={**dict(section.attrs), "kehtetu": True},
                        children=(),
                    )
                    for section in sections
                ]
                new_division = IRNode(
                    kind=IRNodeKind.DIVISION,
                    label=target_node.label,
                    text=target_node.text,
                    attrs=dict(target_node.attrs),
                    children=tuple(new_children),
                )
                return tree_ops.replace_at(body, full_path, new_division)
            if target_node is not None and target_node.kind == IRNodeKind.SECTION:
                parent_path = full_path[:-1]
                parent_node = tree_ops.resolve(body, parent_path) if parent_path else None
                if parent_node is not None:
                    from lawvm.estonia.ee_instruction_waist import read_section_selection_meta

                    selection_meta = read_section_selection_meta(op.payload)
                    if (
                        selection_meta is not None
                        and selection_meta.plain_numeric_ranges
                    ):
                        implied_labels = _section_labels_implied_by_plain_range_repeal(
                            parent_node,
                            explicit_labels=selection_meta.explicit_labels,
                            plain_numeric_ranges=selection_meta.plain_numeric_ranges,
                        )
                        if len(implied_labels) > 1 and target_node.label in implied_labels:
                            new_children: list[IRNode] = []
                            for child in parent_node.children:
                                if child.kind == IRNodeKind.SECTION and child.label in implied_labels:
                                    new_children.append(
                                        IRNode(
                                            kind=IRNodeKind.SECTION,
                                            label=child.label,
                                            text="",
                                            attrs={"kehtetu": True},
                                            children=(),
                                        )
                                    )
                                else:
                                    new_children.append(child)
                            new_parent = IRNode(
                                kind=parent_node.kind,
                                label=parent_node.label,
                                text=parent_node.text,
                                attrs=dict(parent_node.attrs),
                                children=tuple(new_children),
                            )
                            return tree_ops.replace_at(body, parent_path, new_parent)
                if (
                    target_node.label is not None
                    and "_" in target_node.label
                    and parent_node is not None
                    and any(
                        sibling.kind == IRNodeKind.SECTION
                        and sibling.label == target_node.label.split("_", 1)[0]
                        and bool(sibling.attrs.get("kehtetu"))
                        for sibling in parent_node.children
                    )
                ):
                    return tree_ops.remove_at(body, full_path)
                # Estonian replay keeps repealed sections as empty-body stubs.
                # Title-surface differences on kehtetu stubs are handled in
                # EE comparison normalization, not replay semantics.
                placeholder = IRNode(
                    kind=IRNodeKind.SECTION,
                    label=target_node.label,
                    text="",
                    attrs={"kehtetu": True},
                    children=(),
                )
                return tree_ops.replace_at(body, full_path, placeholder)
            if target_node is not None and target_node.kind == IRNodeKind.SUBSECTION:
                # Estonian oracle also keeps repealed subsections as empty loige elements
                # (loigeNr preserved, sisuTekst absent).  Match that structure.
                parent_path = full_path[:-1]
                parent_node = tree_ops.resolve(body, parent_path) if parent_path else None
                if parent_node is not None:
                    from lawvm.estonia.ee_instruction_waist import read_subsection_selection_meta

                    selection_meta = read_subsection_selection_meta(op.payload)
                    if selection_meta is not None and selection_meta.explicit_labels:
                        implied_labels_set: set[str] = {
                            label
                            for label in selection_meta.explicit_labels
                            if label
                        }
                        live_subsection_labels = [
                            child.label
                            for child in parent_node.children
                            if child.kind == IRNodeKind.SUBSECTION and child.label is not None
                        ]
                        for start, end in selection_meta.label_ranges:
                            if start not in live_subsection_labels or end not in live_subsection_labels:
                                continue
                            start_key = tree_ops._default_sort_key(start)
                            end_key = tree_ops._default_sort_key(end)
                            if start_key > end_key:
                                start_key, end_key = end_key, start_key
                            for label in live_subsection_labels:
                                label_key = tree_ops._default_sort_key(label)
                                if start_key <= label_key <= end_key:
                                    implied_labels_set.add(label)
                        for start, end in selection_meta.plain_numeric_ranges:
                            if not (str(start).isdigit() and str(end).isdigit()):
                                continue
                            start_key = tree_ops._default_sort_key(str(start))
                            end_key = tree_ops._default_sort_key(str(end))
                            if start_key > end_key:
                                start_key, end_key = end_key, start_key
                            for label in live_subsection_labels:
                                if label in implied_labels_set:
                                    continue
                                label_key = tree_ops._default_sort_key(label)
                                if start_key <= label_key <= end_key:
                                    implied_labels_set.add(label)
                        implied_labels = sorted(implied_labels_set, key=tree_ops._default_sort_key)
                    else:
                        implied_labels = []
                    if len(implied_labels) > 1 and target_node.label in implied_labels:
                        new_children: list[IRNode] = []
                        for child in parent_node.children:
                            if child.kind == IRNodeKind.SUBSECTION and child.label in implied_labels:
                                new_children.append(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label=child.label,
                                        text="",
                                        attrs=dict(child.attrs),
                                        children=(),
                                    )
                                )
                            else:
                                new_children.append(child)
                        new_parent = IRNode(
                            kind=parent_node.kind,
                            label=parent_node.label,
                            text=parent_node.text,
                            attrs=dict(parent_node.attrs),
                            children=tuple(new_children),
                        )
                        return tree_ops.replace_at(body, parent_path, new_parent)
                placeholder = IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label=target_node.label,
                    text="",
                    children=(),
                )
                return tree_ops.replace_at(body, full_path, placeholder)
            if target_node is not None and target_node.kind == IRNodeKind.ITEM:
                parent_path = full_path[:-1]
                parent_node = tree_ops.resolve(body, parent_path) if parent_path else None
                if parent_node is not None:
                    target_index = next(
                        (
                            idx
                            for idx, child in enumerate(parent_node.children)
                            if child.kind == IRNodeKind.ITEM and child.label == target_node.label
                        ),
                        None,
                    )
                    if target_index is not None:
                        original_nonempty_indexes = [
                            idx
                            for idx, child in enumerate(parent_node.children)
                            if child.kind == IRNodeKind.ITEM and bool((child.text or "").strip())
                        ]
                        original_last_nonempty_index = (
                            original_nonempty_indexes[-1] if original_nonempty_indexes else None
                        )
                        had_trailing_empty_placeholders = original_last_nonempty_index is not None and any(
                            child.kind == IRNodeKind.ITEM and not bool((child.text or "").strip())
                            for child in parent_node.children[original_last_nonempty_index + 1 :]
                        )
                        new_children: list[IRNode] = []
                        for idx, child in enumerate(parent_node.children):
                            if idx == target_index:
                                new_children.append(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label=child.label,
                                        text="",
                                        attrs=dict(child.attrs),
                                        children=(),
                                    )
                                )
                                continue
                            new_children.append(child)
                        new_parent = IRNode(
                            kind=parent_node.kind,
                            label=parent_node.label,
                            text=(
                                (parent_node.text or "").rstrip()[:-1] + "."
                                if (
                                    parent_node.kind == IRNodeKind.SUBSECTION
                                    and isinstance(parent_node.text, str)
                                    and parent_node.text.rstrip().endswith(":")
                                    and not any(
                                        child.kind == IRNodeKind.ITEM and bool((child.text or "").strip())
                                        for child in new_children
                                    )
                                )
                                else parent_node.text
                            ),
                            attrs=dict(parent_node.attrs),
                            children=tuple(
                                new_children
                                if (
                                    _is_inserted_numbered_label(target_node.label)
                                    or (
                                        had_trailing_empty_placeholders
                                        and original_last_nonempty_index is not None
                                        and target_index < original_last_nonempty_index
                                    )
                                )
                                else _normalize_item_list_terminals(new_children)
                            ),
                        )
                        return tree_ops.replace_at(body, parent_path, new_parent)
            # Items, subitems, etc.: remove entirely
            return tree_ops.remove_at(body, full_path)

    elif action == "replace":
        if payload is not None:
            full_path = _ee_resolve_full_path(body, path)
            if full_path is not None:
                target_node = tree_ops.resolve(body, full_path)
                if target_node is None:
                    # Duplicate container label (e.g. two 'division 6' blocks) causes
                    # resolve to fail even though find() returned a path.  Skip op.
                    return body
                if target_node.kind == IRNodeKind.SECTION:
                    payload_meta = read_payload_rewrite_meta(payload)
                    if (
                        payload_meta.rewrite is not None
                        and payload_meta.rewrite.appendix_table_update
                        and payload_meta.rewrite_witness is None
                    ):
                        return body
                    typed_appendix_inputs_present = payload_meta.rewrite_witness is not None
                    if typed_appendix_inputs_present:
                        parsed_instructions = to_ee_parsed_instructions(
                            [op],
                            source_rule="estonia/grafter:_ee_apply_op",
                        )
                        parsed_instruction = parsed_instructions[0] if parsed_instructions else None
                        parsed_appendix = None
                        if parsed_instruction is not None:
                            parsed_appendix = (
                                parsed_instruction.rewrite_witness.rewrite
                                if parsed_instruction.rewrite_witness is not None
                                else parsed_instruction.rewrite
                            )
                        appendix_rewrite = (
                            parsed_appendix
                            if parsed_appendix is not None and parsed_appendix.appendix_table_update
                            else None
                        )
                        if appendix_rewrite is not None:
                            marker = appendix_rewrite.appendix_marker.strip()
                            row_labels = list(appendix_rewrite.appendix_table_categories)
                            replacement_text = appendix_rewrite.new_surface.replace("\x01", "")
                        elif payload_meta.rewrite is not None and payload_meta.rewrite.appendix_table_update:
                            marker = payload_meta.rewrite.appendix_marker.strip()
                            row_labels = list(payload_meta.rewrite.appendix_table_categories)
                            replacement_text = payload_meta.rewrite.new_surface.replace("\x01", "")
                        else:
                            marker = ""
                            row_labels = []
                            replacement_text = ""
                        if marker and row_labels:
                            children = list(target_node.children)
                            for idx, child in enumerate(children[:-1]):
                                if (
                                    child.kind == IRNodeKind.SUBSECTION
                                    and (child.text or "").strip() == marker
                                    and children[idx + 1].kind == IRNodeKind.SUBSECTION
                                ):
                                    body_child = children[idx + 1]
                                    replaced = _replace_appendix_table_rows(
                                        body_child.text,
                                        row_labels,
                                        replacement_text,
                                    )
                                    if replaced == body_child.text:
                                        return body
                                    children[idx + 1] = IRNode(
                                        kind=body_child.kind,
                                        label=body_child.label,
                                        text=replaced,
                                        attrs=dict(body_child.attrs),
                                        children=tuple(body_child.children),
                                    )
                                    new_sec = IRNode(
                                        kind=target_node.kind,
                                        label=target_node.label,
                                        text=target_node.text,
                                        attrs=dict(target_node.attrs),
                                        children=tuple(children),
                                    )
                                    return tree_ops.replace_at(body, full_path, new_sec)
                    elif payload_meta.rewrite is not None and payload_meta.rewrite.appendix_table_update:
                        return body
                if target_node.kind == IRNodeKind.CHAPTER:
                    new_node = _parse_chapter_payload(payload.text, target_node.label or "")
                    return tree_ops.replace_at(body, full_path, new_node)
                if target_node.kind == IRNodeKind.DIVISION:
                    new_node = _parse_division_payload(payload.text, target_node.label or "")
                    return tree_ops.replace_at(body, full_path, new_node)
                # For section-level replacements, parse the payload into
                # structured title + subsections to match oracle format.
                if target_node.kind == IRNodeKind.SECTION:
                    note_text = _op_instruction_note_text(op)
                    from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

                    sentence_meta = read_sentence_target_meta(payload)
                    sentence_indexes = (
                        list(sentence_meta.sentence_indexes)
                        if sentence_meta is not None and sentence_meta.sentence_indexes
                        else _sentence_indexes_from_notes(note_text)
                    )
                    sentence_index = sentence_indexes[0] if sentence_indexes else None
                    if sentence_index is not None:
                        target_subsection = next(
                            (
                                child
                                for child in target_node.children
                                if child.kind == IRNodeKind.SUBSECTION and child.label == "1" and child.text
                            ),
                            None,
                        )
                        replacement_text = _strip_rt_editorial_parentheticals(payload.text.replace("\x01", ""))
                        if target_subsection is not None:
                            updated_text = _replace_sentence(
                                target_subsection.text,
                                replacement_text,
                                sentence_index,
                            )
                            new_children = [
                                IRNode(
                                    kind=child.kind,
                                    label=child.label,
                                    text=updated_text,
                                    attrs=dict(child.attrs),
                                    children=tuple(child.children),
                                )
                                if child.kind == IRNodeKind.SUBSECTION and child.label == target_subsection.label
                                else child
                                for child in target_node.children
                            ]
                            new_section = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=target_node.text,
                                attrs=dict(target_node.attrs),
                                children=tuple(new_children),
                            )
                            return tree_ops.replace_at(body, full_path, new_section)
                        if target_node.text:
                            new_section = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=_replace_sentence(target_node.text, replacement_text, sentence_index),
                                attrs=dict(target_node.attrs),
                                children=tuple(target_node.children),
                            )
                            return tree_ops.replace_at(body, full_path, new_section)
                    parsed = _parse_section_payload(payload.text, kind=IRNodeKind.SECTION)
                    # When the parsed payload has no title (starts with (1)
                    # directly), preserve the existing section's heading.
                    title = parsed.text if parsed.text else target_node.text
                    children = parsed.children
                    # Special case: payload has no § prefix and no (N) markers,
                    # producing a flat text node with no children.  If the
                    # parsed text is sentence-like (longer than a typical heading),
                    # treat it as the new subsection:1 body and preserve the
                    # existing section title.  This handles the Estonian pattern
                    # "paragrahvi N muudetakse ning sõnastatakse järgmiselt: „Body.""
                    # where the title was not included in the amendment payload.
                    if (
                        not parsed.children
                        and parsed.text
                        and not payload.text.lstrip().startswith("§")
                        and not re.match(r"^\(\d", payload.text.lstrip())
                        and "\x01" not in payload.text
                        and len(parsed.text) > 40
                    ):
                        # Payload looks like body text, not a title.
                        # Wrap as subsection:1 and preserve existing title.
                        intro_text, item_children = _parse_subsection_item_payload(parsed.text)
                        title = target_node.text
                        children = [
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=intro_text,
                                children=tuple(item_children),
                            )
                        ]
                    new_node = IRNode(
                        kind=target_node.kind,
                        label=target_node.label,
                        text=title,
                        children=tuple(children),
                    )
                else:
                    # For subsection/item-level replacements, the raw payload
                    # text often starts with "(N) " (the subsection number
                    # copied from the amendment text), e.g. "(1) Käesolev...".
                    # The oracle stores only the body text without that prefix.
                    # Strip it so the stored text matches the oracle.
                    # Also strip \x01 bold-boundary sentinel from heading-only
                    # bold blocks that leaked through parse_html_op_items.
                    raw_text = payload.text.replace("\x01", "")
                    sibling_subsection_nodes: list[IRNode] = []
                    inline_subsection_item_children: list[IRNode] = []
                    # Combined "pealkiri ja lõige N muudetakse" — payload starts
                    # with "§ N. NewTitle (N) Body".  The op targets a subsection
                    # but the payload also contains a new section heading.
                    # Promote to a section-level replace: update the title
                    # and all subsections that appear in the payload.
                    if raw_text.lstrip().startswith("§") and target_node.kind == IRNodeKind.SUBSECTION:
                        parsed_sec = _parse_section_payload(raw_text, kind=IRNodeKind.SECTION)
                        # Locate the parent section in the tree
                        sec_path = full_path[:-1]  # drop the subsection step
                        if sec_path:
                            sec_node = tree_ops.resolve(body, sec_path)
                            if sec_node is not None and sec_node.kind == IRNodeKind.SECTION:
                                # Preserve existing subsections not covered by the
                                # payload (only those explicitly replaced are updated).
                                updated_labels = {c.label for c in parsed_sec.children}
                                kept_children = [c for c in sec_node.children if c.label not in updated_labels]
                                # Merge: payload children first (they carry new text),
                                # then kept originals — then re-sort by label numerically.
                                merged = list(parsed_sec.children) + kept_children

                                def _sort_key(c: IRNode) -> tuple[int, str]:
                                    lbl = c.label or ""
                                    m = re.match(r"(\d+)", lbl)
                                    return (int(m.group(1)) if m else 0, lbl)

                                merged.sort(key=_sort_key)
                                # Use the new title from the payload when present.
                                # Previously we required the new title to start with
                                # the same words as the old (extension guard), but that
                                # incorrectly blocked genuine renames like
                                # "Ametkondlikust välislepingust teatamine" →
                                # "Ametkondliku välislepingu registreerimine".
                                # Cross-statute contamination is already prevented by
                                # _title_matches_para filtering in parse_ee_amendment_ops.
                                new_title = parsed_sec.text if parsed_sec.text else sec_node.text
                                new_sec = IRNode(
                                    kind=sec_node.kind,
                                    label=sec_node.label,
                                    text=new_title,
                                    attrs=dict(sec_node.attrs),
                                    children=tuple(merged),
                                )
                                return tree_ops.replace_at(body, sec_path, new_sec)
                        # Fallback: just extract the target subsection's body text
                        tgt_label = target_node.label
                        matched = next(
                            (c for c in parsed_sec.children if c.label == tgt_label),
                            None,
                        )
                        raw_text = (
                            matched.text
                            if matched is not None
                            else re.sub(
                                r"^\(\d[\d\s_]*\)\s*", "", re.sub(r"^§\s*\d+\.[^§]*?(?=\()", "", raw_text).strip()
                            )
                        )
                    else:
                        if target_node.kind == IRNodeKind.SUBSECTION:
                            inline_subsections = _parse_inline_subsection_payload_nodes(raw_text)
                            if inline_subsections:
                                matched_subsection = next(
                                    (node for node in inline_subsections if node.label == target_node.label),
                                    None,
                                )
                                if matched_subsection is not None:
                                    raw_text = matched_subsection.text or ""
                                    inline_subsection_item_children = list(matched_subsection.children)
                                    sibling_subsection_nodes = [
                                        node for node in inline_subsections if node.label != target_node.label
                                    ]
                                else:
                                    raw_text = _extract_subsection_text(raw_text, target_node.label or "")
                            else:
                                raw_text = _extract_subsection_text(raw_text, target_node.label or "")
                            # When payload contains multiple subsection markers
                            # "(1) ... (2) ... (3) ..." (e.g. from "lõiked 1–3
                            # muudetakse"), use _extract_subsection_text to select
                            # only the text for this subsection label.
                        else:
                            # Strip subsection prefix "(N) "
                            raw_text = re.sub(r"^\(\d[\d\s_]*\)\s*", "", raw_text)
                        if target_node.kind == IRNodeKind.ITEM:
                            # Item payloads often include "N) " or "N N ) " prefix
                            # (regular items: "1) tekst"; superscript items: "4 1 ) tekst").
                            # Strip it: the label already encodes N.
                            raw_text = re.sub(r"^\d[\d\s]*\)\s*", "", raw_text)
                    raw_text = _strip_rt_editorial_parentheticals(raw_text)
                    note_text = _op_instruction_note_text(op)
                    from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

                    sentence_meta = read_sentence_target_meta(payload)
                    sentence_indexes = (
                        list(sentence_meta.sentence_indexes)
                        if sentence_meta is not None and sentence_meta.sentence_indexes
                        else _sentence_indexes_from_notes(note_text)
                    )
                    sentence_index = sentence_indexes[0] if sentence_indexes else None
                    # "sissejuhatav lauseosa muudetakse" — payload replaces only
                    # the introductory sentence of a subsection; items are
                    # unchanged.  Detect: target is a subsection with children
                    # and the payload text contains no item markers ("N) ").
                    # In that case preserve the original children.
                    parsed_item_children: list[IRNode] = []
                    if target_node.kind == IRNodeKind.SUBSECTION:
                        if inline_subsection_item_children:
                            parsed_item_children = inline_subsection_item_children
                        else:
                            raw_text, parsed_item_children = _parse_subsection_item_payload(raw_text)
                    if parsed_item_children:
                        preserved_children = parsed_item_children
                    elif (
                        target_node.kind == IRNodeKind.SUBSECTION
                        and target_node.children
                        and not re.search(r"\b\d+\)\s", raw_text)
                        and (raw_text.rstrip().endswith(":") or sentence_index is not None)
                    ):
                        preserved_children = list(target_node.children)
                    else:
                        preserved_children = []
                    if target_node.kind in (IRNodeKind.SUBSECTION, IRNodeKind.ITEM) and target_node.text:
                        if sentence_indexes and not raw_text:
                            updated_text = target_node.text
                            for idx in sorted(sentence_indexes, reverse=True):
                                updated_text = _replace_sentence(updated_text, "", idx)
                            raw_text = updated_text
                        elif sentence_index is not None and target_node.kind == IRNodeKind.SUBSECTION:
                            raw_text = _replace_sentence(target_node.text, raw_text, sentence_index)
                    elif target_node.kind == IRNodeKind.SECTION and sentence_indexes and not raw_text:
                        subsection_one = next(
                            (
                                child
                                for child in target_node.children
                                if child.kind == IRNodeKind.SUBSECTION and child.label == "1" and child.text
                            ),
                            None,
                        )
                        if subsection_one is not None:
                            updated_sub_text = subsection_one.text
                            for idx in sorted(sentence_indexes, reverse=True):
                                updated_sub_text = _replace_sentence(updated_sub_text, "", idx)
                            updated_children = tuple(
                                IRNode(
                                    kind=child.kind,
                                    label=child.label,
                                    text=updated_sub_text,
                                    attrs=dict(child.attrs),
                                    children=tuple(child.children),
                                )
                                if child.kind == IRNodeKind.SUBSECTION and child.label == "1"
                                else child
                                for child in target_node.children
                            )
                            new_target = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=target_node.text,
                                attrs=dict(target_node.attrs),
                                children=updated_children,
                            )
                            return tree_ops.replace_at(body, full_path, new_target)
                    new_node = IRNode(
                        kind=target_node.kind,
                        label=target_node.label,
                        text=raw_text,
                        children=tuple(preserved_children),
                    )
                    if target_node.kind == IRNodeKind.ITEM:
                        parent_path = full_path[:-1]
                        parent_node = tree_ops.resolve(body, parent_path) if parent_path else None
                        if parent_node is not None:
                            target_index = next(
                                idx
                                for idx, child in enumerate(parent_node.children)
                                if child.kind == IRNodeKind.ITEM and child.label == target_node.label
                            )
                            terminal = _item_terminal_for_position(list(parent_node.children), target_index)
                            new_node = IRNode(
                                kind=new_node.kind,
                                label=new_node.label,
                                text=_rewrite_item_terminal(new_node.text, terminal),
                                children=tuple(new_node.children),
                            )
                updated_body = tree_ops.replace_at(body, full_path, new_node)
                if target_node.kind == IRNodeKind.SUBSECTION and sibling_subsection_nodes:
                    parent_path = full_path[:-1]
                    for sibling in sibling_subsection_nodes:
                        sibling_path = parent_path + ((str(sibling.kind), sibling.label or ""),)
                        sibling_full_path = _ee_resolve_full_path(updated_body, sibling_path)
                        if sibling_full_path is not None:
                            updated_body = tree_ops.replace_at(updated_body, sibling_full_path, sibling)
                        else:
                            updated_body = tree_ops.insert_sorted(
                                updated_body,
                                parent_path,
                                sibling,
                                sort_key_fn=tree_ops._default_sort_key,
                            )
                return updated_body

    elif action == "insert":
        if payload is not None:
            kind, label = path[-1]
            kind = cast(IRNodeKind, kind)
            # For whole-chapter inserts, parse the full chapter content into a
            # chapter IRNode with structured section children.
            if kind == "part":
                # "seadust täiendatakse III 1. osaga järgmises sõnastuses: „III 1. osa Title § N ..."
                # The oracle stores parts as childless title markers at body level, with their
                # sections also at body level (not nested inside the part). The peg emits
                # separate section-insert ops for each § in the part payload.
                # Here we insert just the title-only part node.
                part_title = payload.text.strip()
                new_node = IRNode(kind=IRNodeKind.PART, label=label, text=part_title)
                parent_path = _ee_resolve_parent_path(body, path)
                if parent_path is not None:
                    return tree_ops.insert_sorted(
                        body,
                        parent_path,
                        new_node,
                        sort_key_fn=tree_ops._default_sort_key,
                    )
                return body
            elif kind == "chapter":
                new_node = _parse_chapter_payload(payload.text, label)
                parent_path = _ee_resolve_parent_path(body, path)
                if parent_path is not None:
                    return tree_ops.insert_sorted(
                        body,
                        parent_path,
                        new_node,
                        sort_key_fn=tree_ops._default_sort_key,
                    )
                return body
            # For whole-division inserts (e.g. "3. peatükki täiendatakse 2. jaoga"),
            # parse the quoted content into a division IRNode with section children
            # and insert it into the parent chapter.
            elif kind == "division":
                # Parse section blocks from the division content
                sects = _parse_section_blocks(payload.text)
                # Extract division title: text of "N. jagu Title" before first §
                m_div_title = re.search(r"\b\d[\d\s]*[.]\s*jagu\s+(.*?)(?=§\s*\d|$)", payload.text, re.DOTALL)
                div_title = m_div_title.group(1).strip() if m_div_title else ""
                new_node = IRNode(kind=IRNodeKind.DIVISION, label=label, text=div_title, children=tuple(sects))
                parent_path = _ee_resolve_parent_path(body, path)
                if parent_path is not None:
                    parent_node = tree_ops.resolve(body, parent_path)
                    # "seaduse N. peatüki tekst loetakse 1. jaoks ..." reclassifies
                    # the chapter's existing flat sections under division 1 and sets
                    # the new division heading. Treat division 1 insert as that wrap.
                    if label == "1":
                        if (
                            parent_node is not None
                            and all(ch.kind == IRNodeKind.SECTION for ch in parent_node.children)
                            and parent_node.children
                        ):
                            div1 = IRNode(
                                kind=IRNodeKind.DIVISION,
                                label="1",
                                text=div_title,
                                children=tuple(parent_node.children),
                            )
                            new_parent = IRNode(
                                kind=parent_node.kind,
                                label=parent_node.label,
                                text=parent_node.text,
                                attrs=dict(parent_node.attrs),
                                children=(div1,),
                            )
                            return tree_ops.replace_at(body, parent_path, new_parent)
                    # If inserting division N>1 into a chapter whose children
                    # are all flat sections (no existing divisions), wrap those
                    # flat sections into division:1 first.  This handles the
                    # pattern where an amendment adds "2. jagu" and the oracle
                    # implicitly reorganizes the existing sections into "1. jagu".
                    int_label = _try_parse_int(label)
                    if int_label is not None and int_label > 1:
                        if (
                            parent_node is not None
                            and all(ch.kind == IRNodeKind.SECTION for ch in parent_node.children)
                            and parent_node.children
                        ):
                            div1 = IRNode(
                                kind=IRNodeKind.DIVISION,
                                label="1",
                                text="",
                                children=tuple(parent_node.children),
                            )
                            new_parent = IRNode(
                                kind=parent_node.kind,
                                label=parent_node.label,
                                text=parent_node.text,
                                attrs=dict(parent_node.attrs),
                                children=(div1,),
                            )
                            body = tree_ops.replace_at(body, parent_path, new_parent)
                        else:
                            body = _ee_relabel_duplicate_division_suffix_before_insert(
                                body,
                                parent_path,
                                insert_label=label,
                                op=op,
                                adjudications_out=adjudications_out,
                            )
                    return tree_ops.insert_sorted(
                        body,
                        parent_path,
                        new_node,
                        sort_key_fn=tree_ops._default_sort_key,
                    )
                return body
            # For section-level inserts, parse payload into structured form.
            # When multiple sections share the same payload (e.g., §-dega 15 3 –15 5
            # all get the full "§ 15 3 . ... § 15 4 . ... § 15 5 . ..." blob),
            # extract only the block for *this* section label.
            elif kind == "section":
                full_path = _ee_resolve_full_path(body, path)
                target_node = tree_ops.resolve(body, full_path) if full_path is not None else None
                note_text = _op_instruction_note_text(op)
                sentence_indexes = _op_sentence_indexes(payload, note_text)
                if full_path is not None and target_node is not None and sentence_indexes:
                    from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

                    sentence_meta = read_sentence_target_meta(payload)
                    sentence_mode = sentence_meta.mode if sentence_meta is not None else ""
                    inserted_text = _strip_rt_editorial_parentheticals(payload.text.replace("\x01", ""))
                    target_subsection = next(
                        (
                            child
                            for child in target_node.children
                            if child.kind == IRNodeKind.SUBSECTION and child.label == "1" and child.text
                        ),
                        None,
                    )
                    if target_subsection is not None and inserted_text:
                        updated_text = target_subsection.text
                        for sentence_index in sentence_indexes:
                            if (
                                sentence_mode == "insert_after"
                                or (
                                    not sentence_mode
                                    and re.search(r"\bpärast\b.*\blausega\b", note_text)
                                )
                            ):
                                updated_text = _insert_sentence_after(
                                    updated_text,
                                    inserted_text,
                                    sentence_index,
                                )
                            else:
                                updated_text = _insert_sentence_before(
                                    updated_text,
                                    inserted_text,
                                    sentence_index,
                                )
                        new_children = [
                            IRNode(
                                kind=child.kind,
                                label=child.label,
                                text=updated_text,
                                attrs=dict(child.attrs),
                                children=tuple(child.children),
                            )
                            if child.kind == IRNodeKind.SUBSECTION and child.label == target_subsection.label
                            else child
                            for child in target_node.children
                        ]
                        new_section = IRNode(
                            kind=target_node.kind,
                            label=target_node.label,
                            text=target_node.text,
                            attrs=dict(target_node.attrs),
                            children=tuple(new_children),
                        )
                        return tree_ops.replace_at(body, full_path, new_section)
                    if target_node.text and inserted_text:
                        updated_text = target_node.text
                        for sentence_index in sentence_indexes:
                            if (
                                sentence_mode == "insert_after"
                                or (
                                    not sentence_mode
                                    and re.search(r"\bpärast\b.*\blausega\b", note_text)
                                )
                            ):
                                updated_text = _insert_sentence_after(
                                    updated_text,
                                    inserted_text,
                                    sentence_index,
                                )
                            else:
                                updated_text = _insert_sentence_before(
                                    updated_text,
                                    inserted_text,
                                    sentence_index,
                                )
                        new_section = IRNode(
                            kind=target_node.kind,
                            label=target_node.label,
                            text=updated_text,
                            attrs=dict(target_node.attrs),
                            children=tuple(target_node.children),
                        )
                        return tree_ops.replace_at(body, full_path, new_section)
                if re.search(r"§\s*\d[\d\s]*[.\x01]", payload.text):
                    # Multi-section payload: split by § boundaries and find our label
                    all_sects = _parse_section_blocks(payload.text)
                    matched_sect = next((s for s in all_sects if s.label == label), None)
                    if matched_sect is not None:
                        parsed_text = matched_sect.text
                        parsed_children = matched_sect.children
                    else:
                        # Fallback to full payload parse (single-section case)
                        parsed = _parse_section_payload(payload.text, kind=IRNodeKind.SECTION)
                        parsed_text = parsed.text
                        parsed_children = parsed.children
                else:
                    parsed = _parse_section_payload(payload.text, kind=IRNodeKind.SECTION)
                    parsed_text = parsed.text
                    parsed_children = parsed.children
                new_node = IRNode(kind=IRNodeKind.SECTION, label=label, text=parsed_text, children=tuple(parsed_children))
                if target_node is not None and _ee_insert_matches_existing_node(target_node, new_node):
                    return body
            else:
                # Strip (N) prefix and \x01 sentinel from non-section inserts.
                # When the payload contains multiple subsection markers "(N)"
                # (e.g. lõigetega N ja M gives both ops the same full payload),
                # extract only the text belonging to *this* subsection label.
                raw_ins = payload.text.replace("\x01", "")
                if kind == "subsection":
                    parent_path = _ee_resolve_parent_path(body, path)
                    parent_node = tree_ops.resolve(body, parent_path) if parent_path is not None else None
                    insert_label = (
                        _adjust_insert_subsection_label_for_repealed_ranges(parent_node, label)
                        if parent_node is not None
                        else label
                    )
                    raw_ins = _extract_subsection_text(raw_ins, label)
                    raw_ins = _strip_rt_editorial_parentheticals(raw_ins)
                    intro_text_ins, item_children_ins = _parse_subsection_item_payload(
                        raw_ins,
                        require_first_label_one=False,
                    )
                    new_node = IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label=insert_label,
                        text=intro_text_ins,
                        children=tuple(item_children_ins),
                    )
                else:
                    raw_ins = re.sub(r"^\(\d[\d\s_]*\)\s*", "", raw_ins)
                    if kind == "item":
                        # Strip item-label prefix "N) " or "N N ) " (superscript items)
                        raw_ins = re.sub(r"^\d[\d\s]*\)\s*", "", raw_ins)
                    raw_ins = _strip_rt_editorial_parentheticals(raw_ins)
                    new_node = IRNode(kind=IRNodeKind.ITEM, label=label, text=raw_ins)
                if raw_ins and re.match(r"^[,;:.)]", raw_ins):
                    full_path = _ee_resolve_full_path(body, path)
                    target_node = tree_ops.resolve(body, full_path) if full_path is not None else None
                    if full_path is not None and target_node is not None:
                        if raw_ins.strip() and raw_ins.strip() in (target_node.text or ""):
                            return body
                        base_text = target_node.text.rstrip()
                        if base_text.endswith("."):
                            base_text = base_text[:-1].rstrip()
                            appended = f"{base_text}{raw_ins}."
                        else:
                            appended = f"{base_text}{raw_ins}"
                        new_target = IRNode(
                            kind=target_node.kind,
                            label=target_node.label,
                            text=appended,
                            attrs=dict(target_node.attrs),
                            children=tuple(target_node.children),
                        )
                        return tree_ops.replace_at(body, full_path, new_target)
                if kind == "item":
                    full_path = _ee_resolve_full_path(body, path)
                    target_node = tree_ops.resolve(body, full_path) if full_path is not None else None
                    if full_path is not None and target_node is not None and raw_ins:
                        if _ee_insert_matches_existing_node(target_node, new_node):
                            return body
                        from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

                        sentence_meta = read_sentence_target_meta(payload)
                        base_text = (target_node.text or "").rstrip()
                        if base_text:
                            if sentence_meta is not None and sentence_meta.mode == "prepend_item":
                                prepended = f"{raw_ins.strip()} {base_text.lstrip()}".strip()
                                new_target = IRNode(
                                    kind=target_node.kind,
                                    label=target_node.label,
                                    text=prepended,
                                    attrs=dict(target_node.attrs),
                                    children=tuple(target_node.children),
                                )
                                return tree_ops.replace_at(body, full_path, new_target)
                            terminal = ";"
                            if base_text.endswith(";"):
                                base_text = base_text[:-1].rstrip()
                                if base_text and not base_text.endswith("."):
                                    base_text = f"{base_text}."
                            elif not base_text.endswith("."):
                                terminal = ""
                            else:
                                terminal = "."
                            appended = f"{base_text} {raw_ins.lstrip()}".strip()
                            if terminal == ";" and appended.endswith("."):
                                appended = appended[:-1] + ";"
                            elif terminal == ";" and not appended.endswith(";"):
                                appended = appended + ";"
                            new_target = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=appended,
                                attrs=dict(target_node.attrs),
                                children=tuple(target_node.children),
                            )
                            return tree_ops.replace_at(body, full_path, new_target)
                if kind == "subsection":
                    full_path = _ee_resolve_full_path(body, path)
                    target_node = tree_ops.resolve(body, full_path) if full_path is not None else None
                    if full_path is not None and target_node is not None and raw_ins:
                        if _ee_insert_matches_existing_node(target_node, new_node):
                            return body
                        from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta

                        note_text = _op_instruction_note_text(op)
                        sentence_meta = read_sentence_target_meta(payload)
                        sentence_index = (
                            sentence_meta.sentence_indexes[0]
                            if sentence_meta is not None and sentence_meta.sentence_indexes
                            else _sentence_index_from_notes(note_text)
                        )
                        sentence_mode = sentence_meta.mode if sentence_meta is not None else ""
                        if (
                            sentence_index is not None
                            and (
                                sentence_mode == "insert_before"
                                or (
                                    not sentence_mode
                                    and "loetakse teiseks lauseks" in note_text
                                    and "esimese lausega" in note_text
                                )
                            )
                            and target_node.text
                        ):
                            inserted_text = _strip_rt_editorial_parentheticals(raw_ins)
                            updated_text = _insert_sentence_before(
                                target_node.text,
                                inserted_text,
                                sentence_index,
                            )
                            new_target = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=updated_text,
                                attrs=dict(target_node.attrs),
                                children=tuple(target_node.children),
                            )
                            return tree_ops.replace_at(body, full_path, new_target)
                        if (
                            sentence_index is not None
                            and (
                                sentence_mode == "insert_after"
                                or (
                                    not sentence_mode
                                    and re.search(r"\bpärast\b.*\blausega\b", note_text)
                                )
                            )
                            and target_node.text
                        ):
                            inserted_text = _strip_rt_editorial_parentheticals(raw_ins)
                            updated_text = _insert_sentence_after(
                                target_node.text,
                                inserted_text,
                                sentence_index,
                            )
                            new_target = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=updated_text,
                                attrs=dict(target_node.attrs),
                                children=tuple(target_node.children),
                            )
                            return tree_ops.replace_at(body, full_path, new_target)
                        parent_path = _ee_resolve_parent_path(body, path)
                        parent_node = tree_ops.resolve(body, parent_path) if parent_path is not None else None
                        target_num = _try_parse_int(label)
                        has_later_numeric_subsections = bool(
                            parent_node is not None
                            and any(
                                child.kind == IRNodeKind.SUBSECTION
                                and child.label != label
                                and (child_num := _try_parse_int(child.label or "")) is not None
                                and target_num is not None
                                and child_num >= target_num
                                for child in parent_node.children
                            )
                        )
                        if (
                            target_num is not None
                            and parent_path is not None
                            and parent_node is not None
                            and has_later_numeric_subsections
                            and (target_node.text or "").strip().startswith("Lisa ")
                        ):
                            shifted_parent = _shift_numbered_subsections(parent_node, label)
                            body = tree_ops.replace_at(body, parent_path, shifted_parent)
                            return tree_ops.insert_sorted(
                                body, parent_path, new_node, sort_key_fn=tree_ops._default_sort_key
                            )
                        if new_node.children:
                            merged_children = list(target_node.children)
                            for child in new_node.children:
                                if not any(
                                    existing.kind == child.kind and existing.label == child.label
                                    for existing in merged_children
                                ):
                                    merged_children.append(child)
                            merged_children.sort(
                                key=lambda child: (
                                    tree_ops._default_sort_key(child.label),
                                    child.kind,
                                )
                            )
                            inserted_labels = {child.label for child in new_node.children if child.kind == IRNodeKind.ITEM}
                            if inserted_labels:
                                ordered_items = [child for child in merged_children if child.kind == IRNodeKind.ITEM]
                                first_insert_idx = next(
                                    (idx for idx, child in enumerate(ordered_items) if child.label in inserted_labels),
                                    None,
                                )
                                if first_insert_idx is not None and first_insert_idx > 0:
                                    prev_label = ordered_items[first_insert_idx - 1].label
                                    merged_children = [
                                        IRNode(
                                            kind=child.kind,
                                            label=child.label,
                                            text=_rewrite_item_terminal(child.text, ";"),
                                            attrs=dict(child.attrs),
                                            children=tuple(child.children),
                                        )
                                        if child.kind == IRNodeKind.ITEM and child.label == prev_label
                                        else child
                                        for child in merged_children
                                    ]
                            updated_text = target_node.text
                            if new_node.text:
                                updated_text = (
                                    f"{target_node.text.rstrip()} {new_node.text.lstrip()}".strip()
                                    if target_node.text
                                    else new_node.text.strip()
                                )
                            new_target = IRNode(
                                kind=target_node.kind,
                                label=target_node.label,
                                text=updated_text,
                                attrs=dict(target_node.attrs),
                                children=tuple(merged_children),
                            )
                            return tree_ops.replace_at(body, full_path, new_target)
                        if target_node.text:
                            appended = f"{target_node.text.rstrip()} {raw_ins.lstrip()}".strip()
                        else:
                            appended = raw_ins.strip()
                        new_target = IRNode(
                            kind=target_node.kind,
                            label=target_node.label,
                            text=appended,
                            attrs=dict(target_node.attrs),
                            children=tuple(target_node.children) or tuple(new_node.children),
                        )
                        return tree_ops.replace_at(body, full_path, new_target)
            parent_path = _ee_resolve_parent_path(body, path)
            if parent_path is not None:
                if kind == "item":
                    parent_node = tree_ops.resolve(body, parent_path)
                    if parent_node is not None:
                        existing_items = [child for child in parent_node.children if child.kind == IRNodeKind.ITEM]
                        if existing_items:
                            ordered = sorted(
                                existing_items + [new_node],
                                key=lambda child: tree_ops._default_sort_key(child.label),
                            )
                            insert_idx = next(
                                (
                                    idx
                                    for idx, child in enumerate(ordered)
                                if child.kind == IRNodeKind.ITEM
                                    and child.label == new_node.label
                                    and child.text == new_node.text
                                ),
                                None,
                            )
                            if insert_idx is not None and insert_idx > 0:
                                prev_item = ordered[insert_idx - 1]
                                if (
                                    prev_item.kind == IRNodeKind.ITEM
                                    and prev_item.text
                                    and prev_item.text.rstrip().endswith(".")
                                ):
                                    new_children: list[IRNode] = []
                                    for child in parent_node.children:
                                        if (
                                            child.kind == IRNodeKind.ITEM
                                            and child.label == prev_item.label
                                            and child.text == prev_item.text
                                        ):
                                            new_children.append(
                                                IRNode(
                                                    kind=child.kind,
                                                    label=child.label,
                                                    text=_rewrite_item_terminal(child.text, ";"),
                                                    attrs=dict(child.attrs),
                                                    children=tuple(child.children),
                                                )
                                            )
                                        else:
                                            new_children.append(child)
                                    body = tree_ops.replace_at(
                                        body,
                                        parent_path,
                                        IRNode(
                                            kind=parent_node.kind,
                                            label=parent_node.label,
                                            text=parent_node.text,
                                            attrs=dict(parent_node.attrs),
                                            children=tuple(new_children),
                                        ),
                                    )
                return tree_ops.insert_sorted(body, parent_path, new_node, sort_key_fn=tree_ops._default_sort_key)

    elif action == "text_replace":
        full_path = _ee_resolve_full_path(body, path)
        if full_path is not None:
            node = tree_ops.resolve(body, full_path)
            if node is not None and payload is not None:
                rewrite_spec = _ee_read_text_replace_spec(payload)
                if rewrite_spec is None:
                    payload_meta = read_payload_rewrite_meta(payload)
                    rewrite = payload_meta.rewrite
                    old = rewrite.old_surface.replace("\x01", "") if rewrite is not None else ""
                    if not old:
                        return body
                    rewrite_spec = EETextRewriteSpec(
                        old_text=old,
                        new_text=payload.text.replace("\x01", ""),
                        case_inflected=bool(rewrite.case_inflected) if rewrite is not None else False,
                    )
                if rewrite_spec.old_text:
                    note_text = _op_instruction_note_text(op)
                    from lawvm.estonia.ee_instruction_waist import (
                        read_sentence_target_meta,
                        read_subsection_text_scope_meta,
                    )

                    sentence_meta = read_sentence_target_meta(payload)
                    subsection_text_scope_meta = read_subsection_text_scope_meta(payload)
                    sentence_indexes = _op_sentence_indexes(payload, note_text)
                    sentence_index = sentence_indexes[0] if sentence_indexes else None
                    if (
                        node.kind == IRNodeKind.SUBSECTION
                        and subsection_text_scope_meta is not None
                        and subsection_text_scope_meta.intro_only
                        and node.text
                    ):
                        replaced_intro = _ee_apply_text_replace_spec(
                            node.text,
                            rewrite_spec,
                            case_inflected=rewrite_spec.case_inflected,
                            capitalize_sentence_start=True,
                        )
                        if replaced_intro is not None and replaced_intro != node.text:
                            replaced_node = IRNode(
                                kind=node.kind,
                                label=node.label,
                                text=replaced_intro,
                                attrs=dict(node.attrs),
                                children=tuple(node.children),
                            )
                            return tree_ops.replace_at(body, full_path, replaced_node)
                    if (
                        node.kind == IRNodeKind.SECTION
                        and subsection_text_scope_meta is not None
                        and subsection_text_scope_meta.intro_only
                    ):
                        replaced_children: list[IRNode] = []
                        changed_intro = False
                        for child in node.children:
                            if not changed_intro and child.kind == IRNodeKind.SUBSECTION and child.text:
                                replaced_intro = _ee_apply_text_replace_spec(
                                    child.text,
                                    rewrite_spec,
                                    case_inflected=rewrite_spec.case_inflected,
                                    capitalize_sentence_start=True,
                                )
                                if replaced_intro is not None and replaced_intro != child.text:
                                    replaced_children.append(
                                        IRNode(
                                            kind=child.kind,
                                            label=child.label,
                                            text=replaced_intro,
                                            attrs=dict(child.attrs),
                                            children=tuple(child.children),
                                        )
                                    )
                                    changed_intro = True
                                    continue
                            replaced_children.append(child)
                        if changed_intro:
                            replaced_node = IRNode(
                                kind=node.kind,
                                label=node.label,
                                text=node.text,
                                attrs=dict(node.attrs),
                                children=tuple(replaced_children),
                            )
                            return tree_ops.replace_at(body, full_path, replaced_node)
                    if (
                        sentence_indexes
                        and node.kind in {IRNodeKind.SUBSECTION, IRNodeKind.ITEM}
                        and node.text
                    ):
                        parsed_instructions = to_ee_parsed_instructions(
                            [op],
                            source_rule="estonia/grafter:_ee_apply_op",
                        )
                        parsed_instruction = parsed_instructions[0] if parsed_instructions else None
                        parsed_rewrite = None
                        if parsed_instruction is not None:
                            parsed_rewrite = (
                                parsed_instruction.rewrite_witness.rewrite
                                if parsed_instruction.rewrite_witness is not None
                                else parsed_instruction.rewrite
                            )
                        sentence_rewrite_spec = rewrite_spec
                        if parsed_rewrite is not None:
                            sentence_rewrite_spec = EETextRewriteSpec(
                                old_text=parsed_rewrite.old_surface,
                                new_text=parsed_rewrite.new_surface,
                                mode=parsed_rewrite.mode.value,
                                case_inflected=parsed_rewrite.case_inflected,
                            )
                        sentences = _split_ee_sentences(node.text)
                        sentence_changed = False
                        for sentence_index in sentence_indexes:
                            if sentence_index == 1_000_000 and sentences:
                                sentence_index = len(sentences) - 1
                            if sentence_index >= len(sentences):
                                continue
                            replaced_sentence = _ee_apply_text_replace_spec(
                                sentences[sentence_index],
                                sentence_rewrite_spec,
                                case_inflected=sentence_rewrite_spec.case_inflected,
                            )
                            if replaced_sentence != sentences[sentence_index]:
                                sentences[sentence_index] = replaced_sentence or sentences[sentence_index]
                                sentence_changed = True
                        if sentence_changed:
                            replaced_node = IRNode(
                                kind=node.kind,
                                label=node.label,
                                text=" ".join(sentences).strip(),
                                attrs=dict(node.attrs),
                                children=tuple(node.children),
                            )
                            return tree_ops.replace_at(body, full_path, replaced_node)
                        return body
                    repeated_match_count = _ee_repeated_single_occurrence_rewrite_match_count(node, rewrite_spec)
                    if repeated_match_count > 1:
                        _append_ee_replay_adjudication(
                            adjudications_out,
                            kind=_EE_AMBIGUOUS_SINGLE_OCCURRENCE_TEXT_REPLACE_RULE,
                            message=(
                                "EE replay blocked a single-occurrence text insertion because "
                                "the source surface matched the exact target more than once."
                            ),
                            op=op,
                            detail={
                                "target": str(op.target),
                                "mode": rewrite_spec.mode,
                                "source_old_text": rewrite_spec.old_text,
                                "replacement": rewrite_spec.new_text,
                                "match_count": str(repeated_match_count),
                            },
                        )
                        return body
                    replaced_node, changed = _replace_text_in_subtree_with_spec(
                        node,
                        rewrite_spec,
                        case_inflected=rewrite_spec.case_inflected,
                        capitalize_sentence_start=node.kind != IRNodeKind.ITEM,
                    )
                    if changed:
                        return tree_ops.replace_at(body, full_path, replaced_node)
                    typo_node, typo_changed, actual_old = _ee_typo_tolerant_text_replace(
                        node,
                        rewrite_spec,
                    )
                    if typo_changed:
                        _append_ee_replay_adjudication(
                            adjudications_out,
                            kind=_EE_SOURCE_TYPO_TEXT_REPLACE_RULE,
                            message=(
                                "EE replay applied an exact-target one-character source typo "
                                "recovery for a text replacement."
                            ),
                            op=op,
                            detail={
                                "target": str(op.target),
                                "source_old_text": rewrite_spec.old_text,
                                "matched_live_text": actual_old,
                                "replacement": rewrite_spec.new_text,
                            },
                        )
                        return tree_ops.replace_at(body, full_path, typo_node)
                    return body
                else:
                    return body

    elif action not in ("repeal", "replace", "insert", "renumber", "text_replace"):
        # Every action type must be explicitly handled.  A new action added to
        # the IR without a handler here would silently produce wrong law.
        raise ValueError(
            f"_ee_apply_op: unhandled action {action!r} on op {op.op_id}. "
            "Add an explicit handler or a skip+warn clause."
        )

    return body


def _append_ee_replay_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, str]] = None,
) -> None:
    """Append an Estonia replay adjudication when sink list is available."""
    if adjudications_out is None:
        return
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            detail=detail or {},
        )
    )


def _ee_relabel_duplicate_division_suffix_before_insert(
    body: IRNode,
    parent_path: tree_ops.Path,
    *,
    insert_label: str,
    op: LegalOperation,
    adjudications_out: Optional[list[CompileAdjudication]],
) -> IRNode:
    """Repair an old-format duplicate jagu run before a high-numbered insert.

    Some RT old-format bases carry duplicate division labels, then a later source
    inserts a high division number and the consolidated text presents the
    intervening duplicate/run as a monotone sequence. This recovery is only
    accepted when the live topology proves a unique suffix shift.
    """
    insert_int = _try_parse_int(insert_label)
    if insert_int is None or insert_int <= 1:
        return body
    parent_node = tree_ops.resolve(body, parent_path)
    if parent_node is None or parent_node.kind != IRNodeKind.CHAPTER:
        return body
    if not parent_node.children or any(child.kind != IRNodeKind.DIVISION for child in parent_node.children):
        return body
    labels = [_try_parse_int(child.label or "") for child in parent_node.children]
    if any(label is None for label in labels):
        return body
    int_labels = [label for label in labels if label is not None]
    duplicate_indexes = [
        idx
        for idx in range(1, len(int_labels))
        if int_labels[idx] <= int_labels[idx - 1]
    ]
    if len(duplicate_indexes) != 1:
        return body
    suffix_start = duplicate_indexes[0]
    suffix_old = int_labels[suffix_start:]
    expected_old = list(range(suffix_old[0], suffix_old[0] + len(suffix_old)))
    if suffix_old != expected_old:
        return body
    new_start = int_labels[suffix_start - 1] + 1
    suffix_new = list(range(new_start, new_start + len(suffix_old)))
    if suffix_new[-1] != insert_int - 1:
        return body
    prefix_labels = set(int_labels[:suffix_start])
    if prefix_labels.intersection(suffix_new):
        return body

    relabeled_children: list[IRNode] = []
    for idx, child in enumerate(parent_node.children):
        if idx < suffix_start:
            relabeled_children.append(child)
            continue
        new_label = str(suffix_new[idx - suffix_start])
        relabeled_children.append(
            IRNode(
                kind=child.kind,
                label=new_label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        )
    new_parent = IRNode(
        kind=parent_node.kind,
        label=parent_node.label,
        text=parent_node.text,
        attrs=dict(parent_node.attrs),
        children=tuple(relabeled_children),
    )
    _append_ee_replay_adjudication(
        adjudications_out,
        kind="ee_implicit_division_sequence_relabel_after_high_jagu_insert",
        message="EE replay relabeled a duplicate division suffix before applying a source-backed high-numbered division insert.",
        op=op,
        detail={
            "parent": "/".join(f"{kind}:{label}" for kind, label in parent_path),
            "insert_division": insert_label,
            "old_labels": ",".join(str(label) for label in int_labels),
            "new_labels": ",".join(
                str(label)
                for label in (
                    *int_labels[:suffix_start],
                    *suffix_new,
                )
            ),
        },
    )
    return tree_ops.replace_at(body, parent_path, new_parent)


def _ee_section_snapshot_path(full_path: tree_ops.Path) -> Optional[tree_ops.Path]:
    """Return the section-level prefix of a fully-resolved op path.

    Given a full path like [('chapter','4'), ('section','29'), ('subsection','1')),
    returns [('chapter','4'), ('section','29')] — the finest structural unit that
    compile_timelines can use as an addressable snapshot.

    Preference order (most preferred first): section > division > chapter > part.
    Only fall back to chapter/part when no section exists in the path.
    Returns None if path is empty.
    """
    if not full_path:
        return None
    # First pass: look for 'section' (finest preferred unit)
    for i, (kind, _label) in enumerate(full_path):
        if kind == "section":
            return full_path[: i + 1]
    # Second pass: fall back to division/chapter/part
    for i, (kind, _label) in enumerate(full_path):
        if kind in ("division", "chapter", "part"):
            return full_path[: i + 1]
    # Fallback: subsection-only or item-only path with no structural ancestor
    return full_path


def _ee_text_replace_run_sort_key(op: LegalOperation) -> tuple[int, int, int, int]:
    """Sort same-source text_replace runs: longer old_text first, then by scope.

    Primary key: longer old_text (more specific) runs first.  This ensures
    that a section-scoped op targeting "rahvatervisele või -ohutusele" (28
    chars) preempts a global op targeting "rahvatervis" (11 chars) that would
    partially consume the longer match target.

    Secondary key: scope_rank — within the same length tier, global ops (0)
    run before section-scoped ops (1).  This keeps global chain ops in the
    right relative order (A→B global before B→C global) when both have the
    same old_text length.

    Tertiary key: explicit target-law replacements before synthetic generic
    ministry-reorganization replacements for the same old_text.  This prevents
    an inferred generic all-laws rename from consuming text before a source
    paragraph for the target statute can apply its own scoped exception.

    Final key: original sequence number as tiebreaker.
    """
    old_text = ""
    generic_ministry_rank = 0
    if op.payload is not None:
        payload_meta = read_payload_rewrite_meta(op.payload)
        old_text = payload_meta.rewrite.old_surface if payload_meta.rewrite is not None else ""
        if op.payload.attrs.get("source_family") == "generic_ministry_reorganization":
            generic_ministry_rank = 1
    scope_rank = 0 if not op.target.path else 1  # global=0, scoped=1
    return (-len(old_text), scope_rank, generic_ministry_rank, op.sequence)


def apply_ee_ops(
    statute: IRStatute,
    ops: List[LegalOperation],
    blame_map: Optional[dict] = None,
    lo_ops_out: Optional[list] = None,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> IRStatute:
    """Apply LegalOperations to an Estonian IRStatute, returning an updated copy.

    Ops are applied in sequence order (ascending sequence number). Operations
    with unresolvable targets or unknown actions are silently skipped.

    This implements the ops-first replay path for Estonia. Compare the result
    against `ingest_consolidated()` output via `verify_consistency()` to find
    legal divergences (which are binding legal findings, unlike Finnish editorial
    conventions).

    Args:
        statute:     Base tyviseadus parsed by parse_ee_statute().
        ops:         Amendment operations from parse_ee_amendment_ops() for one
                     or more amendment acts, in chronological order.
        blame_map:   Optional dict filled with {addr_key -> last LegalOperation}.
        lo_ops_out:  Optional list; if provided, a LegalOperation snapshot is
                     appended for each successfully applied op.  The snapshot
                     captures the section-level node *after* the op is applied,
                     so compile_timelines() can build a dated version history.
                     Entry format mirrors the Finnish grafter: action=_to_structural_action("replace")
                     (or "repeal" for repeal tombstones), target=section-level
                     address, payload=section IRNode (None for tombstones),
                     source copied from op.source (carries effective/enacted).

    Returns:
        New IRStatute with all ops applied. Original is not modified.
    """
    # The shared IR is frozen; replay can use the baseline body directly and
    # let tree_ops return new nodes for each change.
    body = statute.body
    sorted_ops = sorted(ops, key=lambda o: o.sequence)
    reordered_ops: list[LegalOperation] = []
    idx = 0
    while idx < len(sorted_ops):
        op = sorted_ops[idx]
        action = op.action.value if hasattr(op.action, "value") else op.action
        source_id = op.source.statute_id if op.source is not None else ""
        if action == "text_replace":
            run_end = idx + 1
            while (
                run_end < len(sorted_ops)
                and (sorted_ops[run_end].action.value if hasattr(sorted_ops[run_end].action, "value") else sorted_ops[run_end].action)
                == "text_replace"
                and (getattr(sorted_ops[run_end].source, "statute_id", "") == source_id)
            ):
                run_end += 1
            reordered_ops.extend(sorted(sorted_ops[idx:run_end], key=_ee_text_replace_run_sort_key))
            idx = run_end
            continue
        reordered_ops.append(op)
        idx += 1
    persistent_postpass_ops = [
        op
        for op in reordered_ops
        if (op.action.value if hasattr(op.action, "value") else op.action) == "text_replace"
        and op.payload is not None
        and read_payload_rewrite_meta(op.payload).persistent_postpass
    ]
    if persistent_postpass_ops:
        reordered_ops.extend(persistent_postpass_ops)
    for op in reordered_ops:
        action = op.action.value if hasattr(op.action, "value") else op.action
        if action not in ("replace", "repeal", "insert", "renumber", "text_replace"):
            _append_ee_replay_adjudication(
                adjudications_out,
                kind="ee_replay_unsupported_action",
                message="EE replay skipped unsupported action.",
                op=op,
                detail={"action": action, "target": str(op.target)},
            )
            continue

        pre_op_body = body
        new_body = _ee_apply_op(body, op, adjudications_out=adjudications_out)
        changed = new_body is not body
        body = new_body

        target_resolved: bool = True
        if op.target.path:
            if action == "insert":
                target_resolved = _ee_resolve_parent_path(pre_op_body, tuple(op.target.path)) is not None
            elif action == "renumber":
                destination_path = tuple(op.destination.path) if op.destination is not None else ()
                destination_resolved = False
                if destination_path:
                    destination_resolved = (
                        _ee_resolve_full_path(pre_op_body, destination_path) is not None
                        or _ee_resolve_parent_path(pre_op_body, destination_path) is not None
                    )
                target_resolved = (
                    _ee_resolve_full_path(pre_op_body, tuple(op.target.path)) is not None and destination_resolved
                )
            else:
                target_resolved = _ee_resolve_full_path(pre_op_body, tuple(op.target.path)) is not None
        if not target_resolved:
            _append_ee_replay_adjudication(
                adjudications_out,
                kind="ee_replay_target_not_found",
                message="EE replay skipped operation: target not found.",
                op=op,
                detail={"action": action, "target": str(op.target)},
            )
            continue

        if not changed:
            _append_ee_replay_adjudication(
                adjudications_out,
                kind="ee_replay_noop",
                message="EE replay emitted no-op for operation.",
                op=op,
                detail={"action": action, "target": str(op.target)},
            )

        blame_path = op.target.path
        if action == "renumber" and op.destination is not None and op.destination.path:
            blame_path = op.destination.path
        if blame_map is not None and blame_path:
            addr_key = "/".join(f"{k}:{v}" for k, v in blame_path)
            blame_map[addr_key] = op

        if lo_ops_out is not None and changed:
            # Determine the full path to the affected node in the updated body.
            # For repeal, the node is gone — use a deep find on the pre-op path
            # elements to locate the section-level ancestor that still exists.
            # For others, find the leaf in the updated body.
            op_target_path = list(op.target.path)
            if action == "repeal":
                # Node removed — find section-level ancestor that still exists.
                # Walk up the op path to find the nearest surviving ancestor.
                full_ancestor: Optional[tree_ops.Path] = None
                for depth in range(len(op_target_path), 0, -1):
                    ancestor_path = _ee_resolve_full_path(body, tuple(op_target_path[:depth]))
                    if ancestor_path is not None:
                        full_ancestor = ancestor_path
                        break
                if full_ancestor is not None:
                    snap_path = _ee_section_snapshot_path(full_ancestor)
                    if snap_path is not None:
                        snap_node = tree_ops.resolve(body, list(snap_path))
                        if snap_node is not None:
                            lo_ops_out.append(
                                LegalOperation(
                                    op_id=f"ee_snap_{op.sequence}",
                                    sequence=op.sequence,
                                    action=_to_structural_action("replace"),
                                    target=LegalAddress(path=tuple(snap_path)),
                                    payload=snap_node,
                                    source=op.source,
                                )
                            )
                        else:
                            # Entire section repealed — tombstone
                            lo_ops_out.append(
                                LegalOperation(
                                    op_id=f"ee_snap_{op.sequence}",
                                    sequence=op.sequence,
                                    action=_to_structural_action("repeal"),
                                    target=LegalAddress(path=tuple(snap_path)),
                                    payload=None,
                                    source=op.source,
                                )
                            )
            else:
                # For replace/insert/text_replace: find the leaf in the
                # updated body, then truncate to section level.
                if not op_target_path:
                    # Global text_replace (empty path) — snapshot only sections
                    # that actually changed. Walk pre- and post-op body in
                    # parallel, emitting snapshots for sections whose identity
                    # differs (i.e., the text_replace actually hit them).
                    def _find_sections(node, path=()):
                        results = []
                        for ch in node.children:
                            ch_path = path + ((ch.kind, ch.label),)
                            if ch.kind == IRNodeKind.SECTION:
                                results.append((ch_path, ch))
                            elif ch.kind in (IRNodeKind.CHAPTER, IRNodeKind.PART, IRNodeKind.DIVISION):
                                results.extend(_find_sections(ch, ch_path))
                        return results

                    pre_secs = {p: n for p, n in _find_sections(pre_op_body)}
                    for sec_path_tuple, sec_node in _find_sections(body):
                        pre_node = pre_secs.get(sec_path_tuple)
                        if pre_node is None or pre_node is not sec_node:
                            lo_ops_out.append(
                                LegalOperation(
                                    op_id=f"ee_snap_{op.sequence}_{sec_node.label}",
                                    sequence=op.sequence,
                                    action=_to_structural_action("replace"),
                                    target=LegalAddress(path=tuple(sec_path_tuple)),
                                    payload=sec_node,
                                    source=op.source,
                                )
                            )
                    continue  # skip the leaf-based path below
                if op.action == "renumber" and op.destination is not None and op.destination.path:
                    op_target_path = list(op.destination.path)
                leaf_kind, leaf_label = op_target_path[-1]
                full_path = tree_ops.find(body, leaf_kind, leaf_label)
                if full_path is not None:
                    snap_path = _ee_section_snapshot_path(full_path)
                    if snap_path is not None:
                        snap_node = tree_ops.resolve(body, list(snap_path))
                        if snap_node is not None:
                            lo_ops_out.append(
                                LegalOperation(
                                    op_id=f"ee_snap_{op.sequence}",
                                    sequence=op.sequence,
                                    action=_to_structural_action("replace"),
                                    target=LegalAddress(path=tuple(snap_path)),
                                    payload=snap_node,
                                    source=op.source,
                                )
                            )

    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=list(statute.supplements),
        metadata=dict(statute.metadata),
    )
