"""Estonian amendment text parser → LegalOperation list.

Estonian uses agglutinative case inflection for provision references.
The amendment text format is:
    paragrahvi N lõike M punkt K <verb> [content]

Key genitive/locative forms used in amendment targets:
    paragrahv N → paragrahvi N   (section)
    lõige M     → lõike M / lõikes M   (subsection)
    punkt K     → punkti K / punktis K  (item)
    alampunkt K → alampunkti K          (subitem)

The verb determines the action type. New content (for replace/insert) follows
"järgmiselt:" or "järgmises sõnastuses:" and is typically wrapped in
Estonian quotation marks „...".

Reference: docs/estonia-pilot.md §4 — Amendment Language

Architectural observations
--------------------------
- EE currently has a strong direct parser -> LegalOperation path, but it mostly
  bypasses the shared clause-surface waist. That makes it productive, but it
  also means cross-jurisdiction convergence is happening after the fact.
- Text replacement semantics are still carried heavily in payload attrs
  (`old_text`, case flags, special postpasses). That is workable locally but it
  weakens coherence with the shared `text_match` / `text_replacement` contract.
- This module should be treated as a serious frontend, not as merely another
  extractor. Its choices will constrain the shared kernel if left implicit.

TODO
----
- Decide whether EE will emit native ClauseAST / ClauseSurface or explicitly
  remain a direct-LegalOperation frontend with a documented waiver.
- Move shared text-replace semantics onto first-class LegalOperation fields.

Actionables
-----------
- Prefer source-local parsing facts here; push live-state recovery downward into
  elaboration/replay-specific layers rather than encoding replay assumptions in
  the parser output.
- When adding new op families, check whether the data belongs in shared fields
  first and only then in EE-specific payload attrs.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata
from dataclasses import replace
from typing import List, Optional, Tuple

from lawvm.core.ir import (
    TextPatchKindEnum,
    IRNodeKind,
    StructuralAction,
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import FacetKind


_EE_SUPERSCRIPT_DIGITS = "".join(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if "SUPERSCRIPT" in unicodedata.name(chr(cp), "")
    and unicodedata.digit(chr(cp), None) is not None
)
_EE_SUPERSCRIPT_DIGIT_CLASS = re.escape(_EE_SUPERSCRIPT_DIGITS)
_EE_SUPERSCRIPT_DIGIT_TRANSLATION = {
    ord(ch): str(unicodedata.digit(ch)) for ch in _EE_SUPERSCRIPT_DIGITS
}
_EE_DASH_CHARS = "".join(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if unicodedata.category(chr(cp)) == "Pd"
)
_EE_DASH_CLASS = re.escape(_EE_DASH_CHARS)
_EE_NUM_ATOM = r"\d+(?:\s+\d+|[" + _EE_SUPERSCRIPT_DIGIT_CLASS + r"]+)?"
_EE_ZS_NON_ASCII_SPACES = frozenset(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if cp != 0x20 and unicodedata.category(chr(cp)) == "Zs"
)
_EE_CF_FORMAT_CHARS = frozenset(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if unicodedata.category(chr(cp)) == "Cf"
)
_EE_PARSE_TRANSLATION_TABLE = {
    **{ord(ch): " " for ch in _EE_ZS_NON_ASCII_SPACES},
    **{ord(ch): "\u2013" for ch in _EE_DASH_CHARS},
    **{ord(ch): "" for ch in _EE_CF_FORMAT_CHARS},
}


def _normalize_ee_parse_text(text: str) -> str:
    """Normalize Estonian text for structural parsing only."""
    return text.translate(_EE_PARSE_TRANSLATION_TABLE)



def _to_structural_action(action: str) -> StructuralAction:
    """Map string action to StructuralAction, preserving text-level variants."""
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

# ---------------------------------------------------------------------------
# Target reference extraction
# ---------------------------------------------------------------------------

# Superscript number suffixes appear as plain digits after a space in stripped
# HTML (e.g. "§71¹" → "71 1" after HTML stripping).  We normalise them to
# "71_1" so they're a usable string label.
def _normalize_num(raw: str) -> str:
    """Collapse superscript digit sequences: '71 1' → '71_1', '1' → '1'."""
    raw = _normalize_ee_parse_text(raw)
    normalized = re.sub(
        r"(?<=\d)([" + _EE_SUPERSCRIPT_DIGIT_CLASS + r"]+)",
        lambda match: "_" + match.group(1).translate(_EE_SUPERSCRIPT_DIGIT_TRANSLATION),
        raw.strip(),
    ).translate(_EE_SUPERSCRIPT_DIGIT_TRANSLATION)
    # Handle "N digits" patterns (superscript encodings)
    return re.sub(r'(\d)\s+(\d)', r'\1_\2', normalized)


def _instruction_preamble(text: str) -> str:
    """Return the instruction part before quoted replacement payload begins."""
    text = _normalize_ee_parse_text(text)
    preamble_end = len(text)
    for marker in ('\u201e', '\u02ee', '\u00ab', 'järgmises sõnastuses:', 'järgmiselt:'):
        idx = text.find(marker)
        if 0 <= idx < preamble_end:
            preamble_end = idx
    return text[:preamble_end]


def _strip_embedded_reference_wrapper(text: str) -> str:
    """Drop leading amendment-act wrappers before the real target reference.

    Some cross-act amendments point back into an earlier amending statute:
    ``paragrahvi 1 punktis 11 esitatud Eesti Vabariigi haridusseaduse § 36 6 ...``.
    For target extraction we want the inner statute reference, not the wrapper's
    ``paragrahvi 1 punktis 11`` path.
    """
    return re.sub(
        r'^\s*paragrahvi\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*'
        r'(?:\s+lõike(?:s|st|ga|t)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)?'
        r'\s+punkti(?:s|ga)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+esitatud\s+',
        '',
        text,
        flags=re.IGNORECASE,
    )


def parse_target(text: str) -> Optional[LegalAddress]:
    """Extract a LegalAddress from an Estonian amendment target reference string.

    Handles the genitive/locative case forms of provision hierarchy terms.
    Returns None if no recognised provision reference found.

    Only the preamble (text before the quoted new content „…") is searched for
    subsection and item references, to prevent references inside quoted payload
    content (e.g. "§ 8 lõikes 2" in a cross-reference) from contaminating the
    target address.

    Examples:
        "paragrahvi 26 lõike 4" → LegalAddress([("section","26"),("subsection","4")])
        "paragrahvi 37 lõike 1 punkt 3" → LegalAddress([("section","37"),("subsection","1"),("item","3")])
        "paragrahvi 63" → LegalAddress([("section","63")])
        "seadustikku" → None (statute-level op, caller handles)
    """
    path: list[tuple[str, str]] = []

    # Part + chapter title: "N. osa M. peatüki pealkiri"
    m_part_ch = re.search(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*osa\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i\s+pealkir(?:i|ja(?:s|st)?)',
        text,
        re.IGNORECASE,
    )
    if m_part_ch:
        part_num = _normalize_num(m_part_ch.group(1))
        ch_num = _normalize_num(m_part_ch.group(2))
        return LegalAddress(path=(("part", part_num), ("chapter", ch_num)), special=FacetKind.HEADING)

    # Division title: "N. peatüki M. jao pealkiri"
    m_div = re.search(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*jao\s+pealkir(?:i|ja(?:s|st)?)',
        text,
        re.IGNORECASE,
    )
    if m_div:
        ch_num = _normalize_num(m_div.group(1))
        div_num = _normalize_num(m_div.group(2))
        return LegalAddress(path=(("chapter", ch_num), ("division", div_num)), special=FacetKind.HEADING)

    # Chapter title: "N¹. peatüki pealkiri" or "peatüki N pealkiri"
    # Pattern: the chapter number appears before "peatüki" or as "N . peatüki"
    m_ch = re.search(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i\s+pealkir(?:i|ja(?:s|st)?)'
        r'|peatük[k]?i\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*pealkir(?:i|ja(?:s|st)?)',
        text, re.IGNORECASE
    )
    if m_ch:
        ch_num = _normalize_num(m_ch.group(1) or m_ch.group(2))
        return LegalAddress(path=(("chapter", ch_num),), special=FacetKind.HEADING)

    # For section and sub-provision refs: only search the preamble (before the
    # first Estonian open-quote „ or "järgmises sõnastuses:" / "järgmiselt:").
    # This prevents body cross-references like "käesoleva paragrahvi 1. lõikes"
    # inside quoted replacement text from contaminating the target address.
    preamble = _strip_embedded_reference_wrapper(_instruction_preamble(text))

    # Section: paragrahvi N (genitive), paragrahvis N (inessive),
    #          paragrahvist N (elative, "jäetakse välja" constructions),
    #          paragrahv N (nominative).
    # Search preamble first; fall back to full text so that short clauses like
    # "paragrahv 45 muudetakse" with no quote marker are still parsed.
    section_context = preamble
    m_sect = re.search(
        r'\bparagrahvi(?:s|st)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
        preamble, re.IGNORECASE
    )
    if not m_sect:
        m_sect = re.search(
            r'\bparagrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            preamble, re.IGNORECASE
        )
    if not m_sect:
        m_sect = re.search(
            r'\bparagrahviga\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            preamble,
            re.IGNORECASE,
        )
    if not m_sect:
        # Also accept "§ N" and inessive shorthand "§-s N" that appear in
        # mixed target lists such as "paragrahvi 11 lõikes 2, §-s 78 ning § 80 ...".
        # Also accept insert-form "§-ga N" so statute-level section inserts are
        # anchored from the instruction preamble rather than falling through to
        # the quoted payload and picking up cross-references from the body text.
        m_sect = re.search(r'§(?:-s|-ga)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)', preamble)
    if not m_sect:
        # Final fallback: search full text (covers cases where no preamble
        # marker is present and the clause has no quoted content at all)
        section_context = _strip_embedded_reference_wrapper(text)
        m_sect = re.search(
            r'\bparagrahvi(?:s|st)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            section_context, re.IGNORECASE
        )
    if not m_sect:
        m_sect = re.search(
            r'\bparagrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            section_context, re.IGNORECASE
        )
    if not m_sect:
        m_sect = re.search(
            r'\bparagrahviga\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            section_context,
            re.IGNORECASE,
        )
    if not m_sect:
        m_sect = re.search(r'§(?:-s|-ga)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)', section_context)
    if not m_sect:
        return None
    path.append(("section", _normalize_num(m_sect.group(1))))

    # Subsection/item qualifiers belong only to the local span of the matched
    # section reference. Mixed clauses like "§ 87^2, § 100^3 lõige 3 ..."
    # must not leak the later subsection onto the leading plain section target.
    section_tail = section_context[m_sect.end():]
    next_section = re.search(
        r'(?:\bparagrahvi(?:s|st)?\s+\d|\bparagrahv\s+\d|§(?:-s)?\s*\d)',
        section_tail,
        re.IGNORECASE,
    )
    local_scope = section_tail[:next_section.start()] if next_section else section_tail

    # Subsection forms (consonant gradation: lõige → lõike/lõiket):
    #   nominative: lõige, genitive: lõike, inessive: lõikes, partitive: lõiget
    #   elative: lõikest (used in "lõikest N jäetakse välja" = delete from subsection N)
    #   instrumental: lõikega (used in "täiendatakse lõikega N" = insert subsection N)
    #   plural instrumental: lõigetega (handled separately in extract_ee_ops)
    m_sub = re.search(
        r'\b(?:lõikest|lõike[s]?|lõiget|lõige|lõikega)\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
        local_scope, re.IGNORECASE
    )
    if m_sub:
        path.append(("subsection", _normalize_num(m_sub.group(1))))

    # Item: punkt(i/is/ist/iga) K — the XML uses `alampunkt` but legal text says `punkt`.
    # Items can appear under a subsection OR directly under a section (no lõige in
    # between), so we search for punkt regardless of whether a subsection was found.
    # "punktiga N" (instrumental) appears in "täiendatakse punktiga N" (insert item N).
    # "punktiga N 1" / "punktiga N 1" — superscript suffix is space-separated (→ N_1).
    m_item = re.search(
        r'\bpunkt(?:i|is|ist|iga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*|[a-z][\d]*)',
        local_scope, re.IGNORECASE
    )
    if m_item:
        path.append(("item", _normalize_num(m_item.group(1))))

        # Subitem: alampunkt(i/is) K  (rarely targeted directly)
        m_subitem = re.search(
            r'\balampunkt[i|is]?\s+(\d[\d¹²³]*|[a-z][\d]*)',
            local_scope, re.IGNORECASE
        )
        if m_subitem:
            path.append(("subitem", _normalize_num(m_subitem.group(1))))

    # Section heading rename: "paragrahvi N pealkiri muudetakse" (no lõige/tekst/punkt)
    # Set special=FacetKind.HEADING so apply handler only updates the title, not the body.
    # Must NOT match combined ops like "pealkiri ning lõiked 1 ja 2 muudetakse".
    t_lower = preamble.lower()
    special = None
    if (len(path) == 1 and path[0][0] == "section"
        and re.search(r'\bpealkir(?:i|ja(?:s|st)?)\b', t_lower)
        and (
            'muudetakse' in t_lower
            or 'asendatakse' in t_lower
            or 'jäetakse' in t_lower
            or 'täiendatakse' in t_lower
        )
        and 'lõik' not in t_lower    # covers lõige, lõiked, lõiget, lõikes
        and 'tekst ' not in t_lower
        and 'punkt' not in t_lower):
        special = FacetKind.HEADING

    return LegalAddress(path=tuple(path), special=special)


def _extract_multiple_explicit_targets(text: str) -> List[LegalAddress]:
    """Extract multiple explicit provision targets from one instruction preamble.

    This is narrower than full target parsing and is used for shared
    ``text_replace`` clauses like:

    - ``paragrahvi 20 lõikes 6 ning § 60 lõikes 2 asendatakse ...``
    - ``paragrahvi 36 lõike 1 punktis 3 ja § 142 lõike 3 esimeses lauses
      asendatakse ...``

    The quoted payload is ignored; only explicit provision references in the
    instruction preamble are considered.
    """
    preamble = text
    preamble = re.sub(
        r'\btekstiosa\s+\u201e\u201e[^\u201d]+[\u201c\u201d][^\u201d]*[\u201c\u201d]',
        'tekstiosa ',
        preamble,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for pat in (
        r'\u201e.*?\u201d',
        r'\u02ee.*?\u02ee',
        r'\u00ab.*?\u00bb',
        r'".*?"',
    ):
        preamble = re.sub(pat, ' ', preamble, flags=re.DOTALL)
    preamble = _strip_embedded_reference_wrapper(preamble)
    preamble = re.sub(r"\bl[oõ]igetest\b", "lõigetes", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r'\s+', ' ', preamble).strip()
    chunks = re.split(
        r'(?:,\s*|\s+(?:ning|ja)\s+)'
        r'(?=(?:§(?:-s)?\s*\d|\bparagrahvi(?:s|st)?\s+\d))',
        preamble,
        flags=re.IGNORECASE,
    )
    targets: List[LegalAddress] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for chunk in chunks:
        m_plural_sections = re.search(
            r'^(?:\bparagrahve\s+|§-d?\s*)'
            r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)+)',
            chunk,
            re.IGNORECASE,
        )
        if m_plural_sections:
            for raw_sect_label in _expand_ee_numeric_list(m_plural_sections.group(1).strip()):
                path_tuple = (("section", _normalize_num(raw_sect_label)),)
                if path_tuple in seen:
                    continue
                seen.add(path_tuple)
                targets.append(LegalAddress(path=path_tuple))
            continue

        m_same_section_mixed = re.search(
            r'(?:\bparagrahvi(?:s|st)?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+',
            chunk,
            re.IGNORECASE,
        )
        if m_same_section_mixed:
            sect_label = _normalize_num(m_same_section_mixed.group(1))
            remainder = chunk[m_same_section_mixed.end():]
            mixed_targets: List[LegalAddress] = []
            mixed_seen: set[tuple[tuple[str, str], ...]] = set()

            for intro_item_ref in re.finditer(
                r'lõike(?:te|tes|st|s|t|ga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                r'sissejuhatava(?:t\s+lauseosa|s\s+lauseosas|st\s+lauseosast)(?:\s*,\s*|\s+(?:ning|ja)\s+)'
                r'punkt(?:id|e|ide|ides|i|is|ist|iga)?\s+'
                r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                remainder,
                re.IGNORECASE,
            ):
                sub_label = _normalize_num(intro_item_ref.group(1))
                subsection_path = (("section", sect_label), ("subsection", sub_label))
                if subsection_path not in mixed_seen:
                    mixed_seen.add(subsection_path)
                    mixed_targets.append(LegalAddress(path=subsection_path))
                for item_label in _expand_ee_numeric_list(intro_item_ref.group(2).strip()):
                    item_path = (
                        ("section", sect_label),
                        ("subsection", sub_label),
                        ("item", _normalize_num(item_label)),
                    )
                    if item_path in mixed_seen:
                        continue
                    mixed_seen.add(item_path)
                    mixed_targets.append(LegalAddress(path=item_path))

            m_sub_and_item = re.search(
                r'lõike(?:s|st|ga|t)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+ja\s+'
                r'lõike(?:s|st|ga|t)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                r'punkt(?:id|ide|ides|i|is)?\s+'
                r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                remainder,
                re.IGNORECASE,
            )
            if m_sub_and_item:
                first_sub = _normalize_num(m_sub_and_item.group(1))
                second_sub = _normalize_num(m_sub_and_item.group(2))
                first_path = (("section", sect_label), ("subsection", first_sub))
                if first_path not in mixed_seen:
                    mixed_seen.add(first_path)
                    mixed_targets.append(LegalAddress(path=first_path))
                for item_label in _expand_ee_numeric_list(m_sub_and_item.group(3).strip()):
                    item_path = (
                        ("section", sect_label),
                        ("subsection", second_sub),
                        ("item", _normalize_num(item_label)),
                    )
                    if item_path in mixed_seen:
                        continue
                    mixed_seen.add(item_path)
                    mixed_targets.append(LegalAddress(path=item_path))

            if m_sub_and_item is None:
                for item_pair_ref in re.finditer(
                    r'lõike(?:te|tes|st|s|t|ga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                    r'punkt(?:id|ide|ides|idest|i|is|ist|iga)?\s+'
                    r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                    r'(?:ning|ja)\s+punkt(?:i|is|ist)?\s+'
                    r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                    r'(?:esime(?:sest|ne)|tei(?:sest|ne)|kolma(?:ndast|s)|nelja(?:ndast|s))\s+lausest',
                    remainder,
                    re.IGNORECASE,
                ):
                    sub_label = _normalize_num(item_pair_ref.group(1))
                    for item_label in (item_pair_ref.group(2), item_pair_ref.group(3)):
                        path_tuple = (
                            ("section", sect_label),
                            ("subsection", sub_label),
                            ("item", _normalize_num(item_label)),
                        )
                        if path_tuple in mixed_seen:
                            continue
                        mixed_seen.add(path_tuple)
                        mixed_targets.append(LegalAddress(path=path_tuple))
                for item_ref in re.finditer(
                    r'lõike(?:te|tes|st|s|t|ga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                    r'punkt(?:id|ide|ides|idest|i|is|ist|iga)?\s+'
                    r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                    remainder,
                    re.IGNORECASE,
                ):
                    sub_label = _normalize_num(item_ref.group(1))
                    for item_label in _expand_ee_numeric_list(item_ref.group(2).strip()):
                        path_tuple = (
                            ("section", sect_label),
                            ("subsection", sub_label),
                            ("item", _normalize_num(item_label)),
                        )
                        if path_tuple in mixed_seen:
                            continue
                        mixed_seen.add(path_tuple)
                        mixed_targets.append(LegalAddress(path=path_tuple))

            plain_item_remainder = re.sub(
                r'lõike(?:te|tes|st|s|t|ga)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+'
                r'punkt(?:id|ide|ides|idest|i|is|ist|iga)?\s+'
                r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*',
                ' ',
                remainder,
                flags=re.IGNORECASE,
            )
            plain_item_remainder = re.sub(
                r'(?:ning|ja)\s+punkt(?:i|is|ist)?\s+'
                r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+'
                r'(?:esime(?:sest|ne)|tei(?:sest|ne)|kolma(?:ndast|s)|nelja(?:ndast|s))\s+lausest',
                ' ',
                plain_item_remainder,
                flags=re.IGNORECASE,
            )
            if not re.search(
                r'\blõike(?:te|tes|st|s|t|ga)?\b',
                plain_item_remainder,
                re.IGNORECASE,
            ):
                for item_ref in re.finditer(
                    r'punkt(?:id|ide|ides|e|i|is|ist|iga)?\s+'
                    r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                    plain_item_remainder,
                    re.IGNORECASE,
                ):
                    for raw_item_label in _expand_ee_numeric_list(item_ref.group(1).strip()):
                        path_tuple = (
                            ("section", sect_label),
                            ("item", _normalize_num(raw_item_label)),
                        )
                        if path_tuple in mixed_seen:
                            continue
                        mixed_seen.add(path_tuple)
                        mixed_targets.append(LegalAddress(path=path_tuple))

            plain_remainder = re.sub(
                r'lõike(?:te|tes|st|s|t|ga)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+'
                r'punkt(?:id|ide|ides|i|is|ist|iga)?\s+'
                r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*',
                ' ',
                remainder,
                flags=re.IGNORECASE,
            )
            for sub_ref in re.finditer(
                r'lõike(?:te|tes|st|s|t|ga)?\s+'
                r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                plain_remainder,
                re.IGNORECASE,
            ):
                for raw_sub_label in _expand_ee_numeric_list(sub_ref.group(1).strip()):
                    path_tuple = (
                        ("section", sect_label),
                        ("subsection", _normalize_num(raw_sub_label)),
                    )
                    if path_tuple in mixed_seen:
                        continue
                    mixed_seen.add(path_tuple)
                    mixed_targets.append(LegalAddress(path=path_tuple))

            if len(mixed_targets) >= 2:
                for target in mixed_targets:
                    if target.path in seen:
                        continue
                    seen.add(target.path)
                    targets.append(target)
                continue

        m_plural_sub = re.search(
            r'(?:\bparagrahvi(?:s|st)?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
            r'(?:l[oõ]iked|l[oõ]igetes)\s+'
            r'(\d+(?:\s+\d+)?(?:[,–\-]\s*\d+(?:\s+\d+)?)*(?:\s+ja\s+\d+(?:\s+\d+)?)*)',
            chunk,
            re.IGNORECASE,
        )
        if m_plural_sub:
            sect_label = _normalize_num(m_plural_sub.group(1))
            for sub_label in _expand_ee_numeric_list(m_plural_sub.group(2).strip()):
                path_tuple = (
                    ("section", sect_label),
                    ("subsection", sub_label),
                )
                if path_tuple in seen:
                    continue
                seen.add(path_tuple)
                targets.append(LegalAddress(path=path_tuple))
            continue
        target = parse_target(chunk)
        if target is None or not target.path:
            continue
        path_tuple = target.path
        if path_tuple in seen:
            continue
        seen.add(path_tuple)
        targets.append(target)

    # Same-section fanout: ``paragrahvi 83 52 lõiget 2 ning lõike 3 esimest
    # lauset ...`` only repeats the later subsection reference, not the
    # section label. Recover all subsection targets under that same section.
    section_refs = list(
        re.finditer(
            r'(?:\bparagrahvi(?:s|st)?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            preamble,
            re.IGNORECASE,
        )
    )
    _same_section_item_pat = (
        r'lõike(?:te|tes|st|s|t|ga)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+'
        r'punkt(?:id|ide|ides|idest|i|is|ist|iga)?\s+'
        r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*'
    )
    for idx, section_ref in enumerate(section_refs):
        sect_label = _normalize_num(section_ref.group(1))
        span_start = section_ref.end()
        span_end = section_refs[idx + 1].start() if idx + 1 < len(section_refs) else len(preamble)
        section_span = preamble[span_start:span_end]
        if (
            re.search(r'\bpealkirja(?:s|st)?\b', section_span, re.IGNORECASE)
            and not re.search(r'\b(?:peatük|jagu|jaotis)\w*\b', section_span, re.IGNORECASE)
        ):
            heading_path = (("section", sect_label),)
            if heading_path not in seen:
                seen.add(heading_path)
                targets.append(LegalAddress(path=heading_path, special=FacetKind.HEADING))
        plain_section_span = re.sub(_same_section_item_pat, ' ', section_span, flags=re.IGNORECASE).strip()
        for sub_ref in re.finditer(
            r'(?:,\s*|\b(?:ning|ja)\b\s+)?(?:lõige|lõike|lõiget|lõikeid|lõikes|lõikest|lõikega|lõiked|lõigete|lõigetes)\s+'
            r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
            plain_section_span,
            re.IGNORECASE,
        ):
            for raw_sub_label in _expand_ee_numeric_list(sub_ref.group(1)):
                path_tuple = (
                    ("section", sect_label),
                    ("subsection", _normalize_num(raw_sub_label)),
                )
                if path_tuple in seen:
                    continue
                seen.add(path_tuple)
                targets.append(LegalAddress(path=path_tuple))
    # If an explicit target list already names subsection/item children under a
    # section, do not also keep the bare section target for the same clause.
    # Otherwise a shared text_replace like "paragrahvi 14 lõikeid 1 ja 2 ..."
    # will apply once to the whole section and then again to each subsection.
    child_sections = {
        path[0][1]
        for path in (target.path for target in targets)
        if len(path) >= 2 and path[0][0] == "section"
    }
    child_subsections = {
        path[:2]
        for path in (target.path for target in targets)
        if len(path) >= 3
        and path[0][0] == "section"
        and path[1][0] == "subsection"
        and path[2][0] == "item"
    }
    intro_only_subsections = _extract_intro_only_subsection_paths(text)
    filtered = [
        target
        for target in targets
        if not (
            len(target.path) == 1
            and target.special is not FacetKind.HEADING
            and target.path[0][0] == "section"
            and target.path[0][1] in child_sections
        )
        and not (
            len(target.path) == 2
            and target.path in child_subsections
            and target.path not in intro_only_subsections
        )
    ]
    deduped: list[LegalAddress] = []
    dedup_seen: set[tuple[tuple[tuple[str, str], ...], FacetKind | None]] = set()
    for target in filtered:
        key = (target.path, target.special)
        if key in dedup_seen:
            continue
        dedup_seen.add(key)
        deduped.append(target)
    return deduped


def _extract_intro_only_subsection_paths(text: str) -> set[tuple[tuple[str, str], ...]]:
    """Return subsection paths whose text rewrite scope is intro-only."""
    preamble = _strip_embedded_reference_wrapper(_instruction_preamble(text))
    section_refs = list(
        re.finditer(
            r'(?:\bparagrahvi(?:s|st)?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            preamble,
            re.IGNORECASE,
        )
    )
    intro_only_paths: set[tuple[tuple[str, str], ...]] = set()
    for idx, section_ref in enumerate(section_refs):
        sect_label = _normalize_num(section_ref.group(1))
        span_start = section_ref.end()
        span_end = section_refs[idx + 1].start() if idx + 1 < len(section_refs) else len(preamble)
        section_span = preamble[span_start:span_end]
        for sub_ref in re.finditer(
            r'lõike(?:te|tes|st|s|t|ga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
            r'sissejuhatava(?:t\s+lauseosa|s\s+lauseosas|st\s+lauseosast)',
            section_span,
            re.IGNORECASE,
        ):
            intro_only_paths.add(
                (
                    ("section", sect_label),
                    ("subsection", _normalize_num(sub_ref.group(1))),
                )
            )
        if re.search(r'\bteksti\s+sissejuhatavas\s+lauseosas\b', section_span, re.IGNORECASE):
            intro_only_paths.add((("section", sect_label),))
    return intro_only_paths


def _attach_subsection_text_scope_meta(
    payload: IRNode,
    clean: str,
    target: LegalAddress,
) -> IRNode:
    if not (
        (len(target.path) == 1 and target.path[0][0] == "section")
        or (len(target.path) == 2 and target.path[0][0] == "section" and target.path[1][0] == "subsection")
    ):
        return payload
    if target.path not in _extract_intro_only_subsection_paths(clean):
        return payload
    from lawvm.estonia.ee_instruction_waist import make_subsection_text_scope_meta

    attrs = dict(payload.attrs)
    attrs["subsection_text_scope_meta"] = make_subsection_text_scope_meta(intro_only=True)
    return replace(payload, attrs=attrs)


def _extract_explicit_heading_targets(text: str) -> List[LegalAddress]:
    """Extract explicit heading targets from one instruction preamble."""
    preamble = _strip_embedded_reference_wrapper(_instruction_preamble(text))
    heading_match = re.search(r'\bpealkir(?:i|ja(?:s|st)?)\b', preamble, re.IGNORECASE)
    if heading_match is None:
        return []
    heading_scope = preamble[:heading_match.start()]
    targets: list[LegalAddress] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    consumed_spans: list[tuple[int, int]] = []

    for match in re.finditer(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*osa\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i',
        heading_scope,
        re.IGNORECASE,
    ):
        path = (
            ("part", _normalize_num(match.group(1))),
            ("chapter", _normalize_num(match.group(2))),
        )
        if path in seen:
            continue
        consumed_spans.append(match.span())
        seen.add(path)
        targets.append(LegalAddress(path=path, special=FacetKind.HEADING))

    for match in re.finditer(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*jao',
        heading_scope,
        re.IGNORECASE,
    ):
        path = (
            ("chapter", _normalize_num(match.group(1))),
            ("division", _normalize_num(match.group(2))),
        )
        if path in seen:
            continue
        consumed_spans.append(match.span())
        seen.add(path)
        targets.append(LegalAddress(path=path, special=FacetKind.HEADING))

    for match in re.finditer(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i'
        r'|peatük[k]?i\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
        heading_scope,
        re.IGNORECASE,
    ):
        start, end = match.span()
        if any(start < consumed_end and end > consumed_start for consumed_start, consumed_end in consumed_spans):
            continue
        ch_num = _normalize_num(match.group(1) or match.group(2))
        path = (("chapter", ch_num),)
        if path in seen:
            continue
        seen.add(path)
        targets.append(LegalAddress(path=path, special=FacetKind.HEADING))

    return sorted(targets, key=lambda target: (len(target.path), target.path))


# ---------------------------------------------------------------------------
# Verb / action extraction
# ---------------------------------------------------------------------------

def _classify_verb(text: str) -> str:
    """Return a LegalOperation action from the amendment verb in text.

    Returns: "replace", "repeal", "insert", "text_replace", or "unknown".
    """
    # Only examine the preamble — text BEFORE the quoted new content starts.
    # Quoted content (new law text) often contains verbs like "tunnistatakse
    # kehtetuks" that describe actions in OTHER laws, not the op itself.
    # Split at the first Estonian open-quote „ or at "järgmises sõnastuses:"
    # or "järgmiselt:" to isolate the instruction from the payload.
    preamble = _instruction_preamble(text)
    t = preamble.lower()

    # Text-level replacement: asendatakse ... sõna/arv/tekstiosa/lauseosa
    # Check BEFORE repeal — payload text often contains "tunnistatakse kehtetuks" for
    # EU regulation titles (e.g. "millega tunnistatakse kehtetuks määrus (EÜ) nr 854/2004"),
    # which would trigger repeal if checked first.  "asendatakse sõnad" in the instruction
    # preamble unambiguously identifies a text-replace regardless of payload content.
    # Covers: asendatakse sõna/sõnad/sõnu/arv/tekstiosa/lauseosa/number
    # Also: asendatakse läbivalt sõna (läbivalt = throughout, intervenes before noun)
    # and targeted forms where the provision list sits between the verb and the
    # replaced word, e.g. "seaduses asendatakse § 8 lõike 4 punktis 2 ja lõikes 5
    # ... sõna „X” sõnaga „Y”".
    if re.search(
        r'asendatakse\b.{0,240}?\b(?:läbivalt\s+)?'
        r'(?:sõna[a-z]*|arv[a-z]*|aastaarv[a-z]*|tekstiosa[a-z]*|lauseosa[a-z]*|number[a-z]*)',
        t,
        re.DOTALL,
    ):
        return "text_replace"

    # Repeal: explicit kehtetuks / kehtivus termination phrases
    if any(p in t for p in (
        'tunnistatakse kehtetuks',
        'loetakse kehtetuks',
        'lõpetatakse kehtivus',
    )):
        return "repeal"

    # "jäetakse ... välja" — check whether it's word-level or structural
    # Word-level: "jäetakse pärast sõna X välja sõnad Y" → text_replace
    # Structural: "paragrahvi 12 lõige 3 jäetakse välja" → repeal
    if re.search(r'\bjäetakse\b', t) and re.search(r'\bvälja\b', t):
        if re.search(r'\bvälja\s+(?:sõna[a-z]*|lauseosa|tekstiosa|arv[a-z]*|number)', t):
            return "text_replace"
        return "repeal"

    # Structural replace. _instruction_preamble() strips "järgmiselt:" before
    # payload parsing, so bare "sõnastatakse" in the preamble is the operative
    # replacement verb.
    if 'sõnastatakse' in t:
        return "replace"

    # Simple amend with no explicit new text phrasing → still a replace
    if 'muudetakse' in t:
        return "replace"

    # Insert / supplement: täiendatakse
    if 'täiendatakse' in t:
        # täiendatakse pärast sõna X sõnadega Y → text-level insert
        if (
            'pärast sõna' in t
            or 'pärast sõnu' in t
            or 'pärast tekstiosa' in t
            or 'pärast lauseosa' in t
            or 'pärast arvu' in t
            or 'enne sõna' in t
            or 'enne sõnu' in t
            or 'enne tekstiosa' in t
            or 'enne lauseosa' in t
            or 'enne arvu' in t
        ):
            return "text_replace"
        # täiendatakse lausega / lõigetega / §-dega → structural insert
        return "insert"

    # "lisatakse" → insert
    if 'lisatakse' in t:
        return "insert"

    return "unknown"


_EE_MONTHS = {
    "jaanuar": "01",
    "veebruar": "02",
    "märts": "03",
    "aprill": "04",
    "mai": "05",
    "juuni": "06",
    "juuli": "07",
    "august": "08",
    "september": "09",
    "oktoober": "10",
    "november": "11",
    "detsember": "12",
}


def _extract_clause_local_effective_date(text: str) -> str:
    """Extract one explicit clause-local effective date when the clause says `alates ...`."""
    m = re.search(
        r"\balates\s+(\d{4})\.\s*aasta\s+(\d{1,2})\.\s*([A-Za-zÕÄÖÜŠŽõäöüšž]+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return ""
    year = m.group(1)
    day = int(m.group(2))
    month_token = m.group(3).lower()
    for month_name, month_num in _EE_MONTHS.items():
        if month_token.startswith(month_name):
            return f"{year}-{month_num}-{day:02d}"
    return ""


# ---------------------------------------------------------------------------
# Content extraction (payload for replace/insert)
# ---------------------------------------------------------------------------

# Estonian quotation marks: „ (U+201E opening) and " (U+201C closing)
# Also occasionally " " (standard) or « »
_EE_OPEN_QUOTE = '\u201e'   # „
_EE_CLOSE_QUOTE = '\u201c'  # "

def _extract_balanced_quoted_contents(text: str, open_quote: str, close_quote: str) -> List[str]:
    """Extract outermost balanced quoted spans for an asymmetric quote pair."""
    contents: List[str] = []
    i = 0
    while i < len(text):
        start = text.find(open_quote, i)
        if start < 0:
            break
        depth = 1
        j = start + len(open_quote)
        while j < len(text):
            if text.startswith(open_quote, j):
                depth += 1
                j += len(open_quote)
                continue
            if text.startswith(close_quote, j):
                depth -= 1
                if depth == 0:
                    content = text[start + len(open_quote):j].strip()
                    if content:
                        contents.append(content)
                    i = j + len(close_quote)
                    break
                j += len(close_quote)
                continue
            j += 1
        else:
            break
    return contents


def _extract_quoted_contents(text: str) -> List[str]:
    """Extract one or more payload blocks between quotation marks."""
    balanced_estonian = _extract_balanced_quoted_contents(text, '\u201e', '\u201d')
    if balanced_estonian:
        return balanced_estonian
    balanced_french = _extract_balanced_quoted_contents(text, '\u00ab', '\u00bb')
    if balanced_french:
        return balanced_french
    for pat in (
        r'\u201e(.*?)\u201d',
        r'\u201d(.*?)\u201d',
        r'\u02ee(.*?)\u02ee',
        r'\u00ab(.*?)\u00bb',
        r'"(.*?)"',
    ):
        matches = [m.strip() for m in re.findall(pat, text, re.DOTALL) if m.strip()]
        if matches:
            return matches
    return []


def _extract_payload_after_marker(text: str) -> Optional[str]:
    """Fallback payload extraction when RT nesting leaves an unbalanced open quote."""
    m = re.search(
        r'(?:järgmises\s+sõnastuses|järgmiselt)\s*:\s*(.+)$',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    payload = m.group(1).strip()
    payload = re.sub(r'^[\u201c\u201e\u201d"\u00ab\u00bb]\s*', '', payload)
    payload = re.sub(r'\s*[\u201c\u201e\u201d"\u00ab\u00bb]\s*[.;]?\s*$', '', payload)
    return payload.strip() or None


def _extract_quoted_content(text: str) -> Optional[str]:
    """Extract quoted payload text, joining multiple payload blocks when present."""
    matches = _extract_quoted_contents(text)
    if not matches:
        return _extract_payload_after_marker(text)
    return " ".join(matches)


def _unwrap_nested_statute_insert_payload(content: str) -> str:
    """Strip an amendment-point wrapper around an inner section-insert payload."""
    if not re.search(
        r'^\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰_]*\)\s+'
        r'(?:seadust|seadustikku|määrust)\s+täiendatakse\s+§[‑–‒-]ga\b',
        content,
        re.IGNORECASE,
    ):
        return content
    inner = _extract_quoted_content(content)
    if inner and re.match(r'^\s*§\s*\d', inner):
        return inner
    return content


def _split_plural_subsection_replace_payload(content: str) -> Optional[dict[str, str]]:
    """Split a shared replace payload into subsection-specific payloads.

    Example:
        "(1) Esimene. (4) Neljas." ->
            {"1": "(1) Esimene.", "4": "(4) Neljas."}

        "§ 15. Pealkiri (1) Esimene. (2) Teine." ->
            {"1": "§ 15. Pealkiri (1) Esimene.", "2": "(2) Teine."}

    Returns None when the content does not clearly contain multiple subsection
    blocks, so callers can safely fall back to the original payload.
    """
    stripped = content.strip()
    if not stripped:
        return None

    matches = list(re.finditer(r'\((\d[\d\s_]*)\)\s', stripped))
    if len(matches) < 2:
        return None

    prefix = stripped[:matches[0].start()].strip()
    chunks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        raw_label = match.group(1).strip()
        norm_label = re.sub(r'\s+', '_', raw_label)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(stripped)
        body = stripped[match.end():end].strip()
        piece = f"({raw_label}) {body}".strip()
        if idx == 0 and prefix:
            piece = f"{prefix} {piece}".strip()
        chunks[norm_label] = piece

    return chunks or None


def _split_plural_section_replace_payload(content: str) -> Optional[dict[str, str]]:
    """Split a shared replace payload into section-specific payloads."""
    stripped = content.strip()
    if not stripped:
        return None

    matches = list(re.finditer(r'§\s*(\d[\d\s]*)\s*[.]', stripped))
    if len(matches) < 2:
        return None

    chunks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        label = _normalize_num(match.group(1).strip())
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(stripped)
        piece = stripped[match.start():end].strip()
        if piece:
            chunks[label] = piece
    return chunks or None


def _split_plural_item_payload(content: str) -> Optional[dict[str, str]]:
    """Split a shared payload into item-specific payloads by item label."""
    stripped = content.strip()
    if not stripped:
        return None

    matches = list(re.finditer(r'(\d[\d\s_]*)\)\s', stripped))
    if len(matches) < 2:
        return None

    prefix = stripped[:matches[0].start()].strip()
    chunks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        raw_label = match.group(1).strip()
        norm_label = re.sub(r'\s+', '_', raw_label)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(stripped)
        body = stripped[match.end():end].strip()
        piece = f"{raw_label}) {body}".strip()
        if idx == 0 and prefix:
            piece = f"{prefix} {piece}".strip()
        chunks[norm_label] = piece

    return chunks or None


def _extract_text_replace_args(text: str) -> Tuple[Optional[str], Optional[str]]:
    """For text_replace, extract (old_text, new_text) from the op text.

    Pattern: asendatakse sõnad „OLD" sõnadega „NEW"
             asendatakse sõna «OLD» sõnadega «NEW»

    RT XML sometimes uses ASCII " as the closing quote for a „…" pair
    (U+201E open, U+0022 close) — so we match both „…" and „…" variants.
    """
    # Try all quote styles, most-specific first.
    # Mixed: Estonian „ (U+201E) opening, either " (U+201D) or plain " (U+0022) close.
    # This handles both "„OLD" „NEW"" and "„OLD" „NEW"" patterns.
    # RT HTML (CDATA) sometimes uses " (U+201D) for BOTH opening and closing
    # (non-standard pairing), so we also try " " (U+201D...U+201D).
    text = html.unescape(text)
    nested_delete = re.search(
        r"\bj[aä]etakse\s+v[aä]lja\s+tekstiosa\s+\u201e(.+)[\u201c\u201d\"]\s*[.;]?\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if nested_delete is not None:
        return nested_delete.group(1).strip(), ""
    for pat in (
        r'\u201e(.*?)(?:\u201c|\u201d|")',   # Estonian „ open, common RT closes, or ASCII close
        r'\u201d(.*?)\u201d',          # RT HTML CDATA: ”…” (both U+201D, right double quote)
        r'\u201c(.*?)\u201c',          # RT HTML CDATA: “…” (both U+201C, left double quote)
        r'\u02ee(.*?)\u02ee',          # RT quote prime: ˮ…ˮ
        r'\u00ab(.*?)\u00bb',          # French «…»
        r'"(.*?)"',                    # plain ASCII "…"
    ):
        quotes = re.findall(pat, text, re.DOTALL)
        if len(quotes) >= 2:
            return quotes[0].strip(), quotes[1].strip()
        if len(quotes) == 1:
            return None, quotes[0].strip()
    return None, None


def _extract_text_replace_pairs(text: str) -> List[Tuple[str, str]]:
    """Extract all quoted OLD→NEW pairs from a text_replace clause."""
    text = html.unescape(text)
    for pat in (
        r'\u201e(.*?)(?:\u201c|\u201d|")',
        r'\u201d(.*?)\u201d',
        r'\u201c(.*?)\u201c',
        r'\u02ee(.*?)\u02ee',
        r'\u00ab(.*?)\u00bb',
        r'"(.*?)"',
    ):
        quotes = [q.strip() for q in re.findall(pat, text, re.DOTALL) if q.strip()]
        if len(quotes) >= 2:
            if len(quotes) >= 4 and len(quotes) % 2 == 0 and re.search(r'\bvastavalt\b', text, re.IGNORECASE):
                mid = len(quotes) // 2
                return [
                    (quotes[i], quotes[mid + i])
                    for i in range(mid)
                    if quotes[i] and quotes[mid + i]
                ]
            if len(quotes) == 3:
                return [
                    (quotes[0], quotes[2]),
                    (quotes[1], quotes[2]),
                ]
            return [
                (quotes[i], quotes[i + 1])
                for i in range(0, len(quotes) - 1, 2)
                if quotes[i] and quotes[i + 1]
            ]
    return []


def _extract_mixed_delete_replace_segments(text: str) -> List[tuple[str, str, str]]:
    """Extract segment-local pairs from clauses that mix delete and replace verbs."""
    if not (
        re.search(r'\bj[aä]etakse\s+v[aä]lja\b', text, re.IGNORECASE)
        and re.search(r'\basendatakse\b', text, re.IGNORECASE)
    ):
        return []

    segments: List[tuple[str, str, str]] = []
    parts = re.split(
        r'\s+ja\s+(?=[^.;]*\b(?:asendatakse|j[aä]etakse\s+v[aä]lja)\b)',
        text,
        flags=re.IGNORECASE,
    )
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        if not (
            re.search(r'\basendatakse\b', segment, re.IGNORECASE)
            or re.search(r'\bj[aä]etakse\s+v[aä]lja\b', segment, re.IGNORECASE)
        ):
            continue
        old_text, new_text = _extract_text_replace_args(segment)
        old_text, new_text = _normalize_text_replace_args(
            segment,
            old_text,
            new_text,
        )
        if old_text is None and new_text is None:
            continue
        segments.append((segment, old_text or "", new_text or ""))

    return segments


def _is_case_inflected_text_replace(text: str) -> bool:
    """Return True when the amendment clause says replacement is case-aware."""
    return bool(re.search(r'\b(?:vastavas|nõutavas)\s+kään', text, re.IGNORECASE))


def _should_case_inflect_text_replace(
    text: str,
    old_text: str | None,
    new_text: str | None,
) -> bool:
    """Skip inflection for citation-style replacements like ``§ 84 -> § 47 7``."""
    if not _is_case_inflected_text_replace(text):
        return False
    sample = f"{old_text or ''} {new_text or ''}"
    if "§" in sample and not re.search(r"\blõige(?:d)?\b", sample):
        return False
    return True


def _extract_global_text_replace_chapter_scope(text: str) -> List[str]:
    """Extract a chapter scope for statute-wide text_replace clauses when present."""
    num_pat = r'\d+(?:\s+\d+)?'
    m_range = re.search(
        r'\b(' + num_pat + r')\s*[.]?\s*[–\-]\s*(' + num_pat + r')\s*[.]\s*peatüki[s]?\b',
        text,
        re.IGNORECASE,
    )
    if m_range:
        start = _normalize_num(m_range.group(1))
        end = _normalize_num(m_range.group(2))
        if start.isdigit() and end.isdigit():
            return [str(n) for n in range(int(start), int(end) + 1)]
        return [start, end]

    m_list = re.search(
        r'\b((?:' + num_pat + r'\s*[.]\s*(?:,\s*|\s+ja\s+)?)+)peatüki[s]?\b',
        text,
        re.IGNORECASE,
    )
    if m_list:
        labels = []
        for raw in re.findall(num_pat, m_list.group(1)):
            labels.append(_normalize_num(raw))
        return labels

    m_single = re.search(
        r'\b(' + num_pat + r')\s*[.]\s*peatüki[s]?\b',
        text,
        re.IGNORECASE,
    )
    if m_single:
        return [_normalize_num(m_single.group(1))]
    return []


# ---------------------------------------------------------------------------
# Helper: convert Roman numeral string to integer
# ---------------------------------------------------------------------------
# Delegates to ``lawvm.roman``.  The shared parser rejects non-canonical
# spellings like ``"IIII"`` via round-trip canonicalization.

from lawvm.roman import roman_to_arabic as _roman_to_int  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: extract §-d section numbers from compound repeal clauses
# ---------------------------------------------------------------------------

def _extract_sd_section_nums(clean: str) -> List[str]:
    """Extract section numbers from secondary repeal patterns after conjunctions.

    In compound repeal clauses like "paragrahvi 7 lõige 3 ning §-d 7 1 ja 33
    tunnistatakse kehtetuks", the "§-d 7 1 ja 33" part refers to sections
    7_1 and 33. It also covers the singular form "ning § 85 7".
    This function returns normalized labels for those secondary sections only.
    """
    clean = _normalize_ee_parse_text(clean)
    _NUM_PAT = _EE_NUM_ATOM
    result: List[str] = []
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§[' + _EE_DASH_CLASS + r']d\s+(.+?)(?=(?:\s+(?:\bning\b|\bja\b)\s+|,\s*)§|\s+tunnistatakse\b|;|$)',
        clean,
        re.IGNORECASE,
    ):
        raw_group = match.group(1).strip(" ,;")
        if re.search(r'\bl[oõ]ik', raw_group, re.IGNORECASE):
            continue
        for raw in _expand_ee_numeric_list(raw_group):
            if raw and raw not in result:
                result.append(raw)
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s+('
        + _NUM_PAT
        + r')\s+(?=(?:\bning\b|\bja\b)\s+§\s+'
        + _NUM_PAT
        + r'\s+l[oõ]iked\b)',
        clean,
        re.IGNORECASE,
    ):
        label = _normalize_num(match.group(1).strip())
        if label not in result:
            result.append(label)
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s+(' + _NUM_PAT + r')'
        r'(?!\s+l[oõ]ik)(?=\s*(?:\bning\b|\bja\b|,|;|tunnistatakse\b|$))',
        clean,
        re.IGNORECASE,
    ):
        label = _normalize_num(match.group(1).strip())
        if label not in result:
            result.append(label)
    return result


def _expand_ee_numeric_list(raw_group: str) -> List[str]:
    """Expand a bounded numeric list with commas, `ja`, and en-dash ranges."""
    raw_group = _normalize_ee_parse_text(raw_group)
    _NUM_PAT = _EE_NUM_ATOM
    expanded: List[str] = []

    def _range_labels(start: str, end: str) -> list[str]:
        if start == end:
            return [start]
        if start.isdigit() and end.isdigit():
            return [str(num) for num in range(int(start), int(end) + 1)]
        if "_" in start and end.isdigit():
            start_base, _start_suffix = start.split("_", 1)
            if start_base.isdigit() and int(start_base) < int(end):
                return [start, *[str(num) for num in range(int(start_base) + 1, int(end) + 1)]]
            return [start, end]
        if start.isdigit() and "_" in end:
            end_base, end_suffix = end.split("_", 1)
            if end_base.isdigit() and end_suffix.isdigit():
                labels = [str(num) for num in range(int(start), int(end_base) + 1)]
                if start == end_base:
                    labels = [start]
                labels.extend(f"{end_base}_{suffix}" for suffix in range(1, int(end_suffix) + 1))
                return labels
            return [start, end]
        if "_" in start and "_" in end:
            start_base, start_suffix = start.split("_", 1)
            end_base, end_suffix = end.split("_", 1)
            if (
                start_base.isdigit()
                and start_suffix.isdigit()
                and end_base.isdigit()
                and end_suffix.isdigit()
            ):
                if start_base == end_base:
                    return [
                        f"{start_base}_{suffix}"
                        for suffix in range(int(start_suffix), int(end_suffix) + 1)
                    ]
                if int(start_base) < int(end_base):
                    labels = [start]
                    labels.extend(str(num) for num in range(int(start_base) + 1, int(end_base) + 1))
                    labels.extend(f"{end_base}_{suffix}" for suffix in range(1, int(end_suffix) + 1))
                    return labels
            return [start, end]
        return [start, end]

    for raw_part in re.split(r'\s*,\s*|\s+(?:ja|ning)\s+', raw_group.strip()):
        raw_part = raw_part.strip().strip(";")
        if not raw_part:
            continue
        m_range = re.match(
            r'^(' + _NUM_PAT + r')\s*[.]?\s*[' + _EE_DASH_CLASS + r']\s*(' + _NUM_PAT + r')\s*[.]?$',
            raw_part,
        )
        if m_range:
            start = _normalize_num(m_range.group(1).strip())
            end = _normalize_num(m_range.group(2).strip())
            expanded.extend(_range_labels(start, end))
            continue
        expanded.append(_normalize_num(raw_part.strip(".")))
    return expanded


def _plain_numeric_ranges(raw_group: str) -> tuple[tuple[str, str], ...]:
    """Return plain integer ranges from a section-list witness string."""
    raw_group = _normalize_ee_parse_text(raw_group)
    _NUM_PAT = _EE_NUM_ATOM
    ranges: list[tuple[str, str]] = []
    for raw_part in re.split(r'\s*,\s*|\s+(?:ja|ning)\s+', raw_group.strip()):
        raw_part = raw_part.strip()
        if not raw_part:
            continue
        m_range = re.match(
            r'^(' + _NUM_PAT + r')\s*[' + _EE_DASH_CLASS + r']\s*(' + _NUM_PAT + r')$',
            raw_part,
        )
        if not m_range:
            continue
        start = _normalize_num(m_range.group(1).strip())
        end = _normalize_num(m_range.group(2).strip())
        if start.isdigit() and end.isdigit():
            ranges.append((start, end))
    return tuple(ranges)


def _ee_label_ranges(raw_group: str) -> tuple[tuple[str, str], ...]:
    """Return normalized start/end labels from any explicit numeric range."""
    raw_group = _normalize_ee_parse_text(raw_group)
    ranges: list[tuple[str, str]] = []
    for raw_part in re.split(r'\s*,\s*|\s+(?:ja|ning)\s+', raw_group.strip()):
        raw_part = raw_part.strip().strip(";")
        if not raw_part:
            continue
        m_range = re.match(
            r'^(' + _EE_NUM_ATOM + r')\s*[.]?\s*[' + _EE_DASH_CLASS + r']\s*(' + _EE_NUM_ATOM + r')\s*[.]?$',
            raw_part,
        )
        if not m_range:
            continue
        ranges.append((
            _normalize_num(m_range.group(1).strip()),
            _normalize_num(m_range.group(2).strip()),
        ))
    return tuple(ranges)


def _strip_leading_clause_wrapper(text: str) -> str:
    """Drop an outer old-format clause heading like ``§ 10. ...`` when present."""
    return re.sub(r'^§\s*\d[\d\s_]*\.\s+(?=[A-ZÕÄÖÜŠŽ])', '', text)


def _extract_secondary_subsection_repeals(clean: str) -> List[tuple[str, str]]:
    """Extract subsection repeals that appear after a leading section list.

    Example:
      ``paragrahvid 39 ja 40, § 41 lõiked 1–2 ja lõige 8, §-d 41 1, 43 ja 44
      tunnistatakse kehtetuks``
    """
    clean = _normalize_ee_parse_text(clean)
    _NUM_PAT = _EE_NUM_ATOM
    _SUB_LIST_PAT = (
        _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?(?:\s*,\s*'
        + _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?)*(?:\s+ja\s+'
        + _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?)?'
    )
    m = re.search(
        r'(?:\bning\b|\bja\b|,)\s+§\s+(' + _NUM_PAT + r')\s+'
        r'l[oõ]iked\s+(' + _SUB_LIST_PAT + r')'
        r'(?:\s+ja\s+l[oõ]ige\s+(' + _NUM_PAT + r'))?',
        clean,
        re.IGNORECASE,
    )
    if not m:
        return []
    sect_label = _normalize_num(m.group(1).strip())
    labels = _expand_ee_numeric_list(m.group(2).strip())
    if m.group(3):
        labels.append(_normalize_num(m.group(3).strip()))
    deduped: list[str] = []
    for label in labels:
        if label not in deduped:
            deduped.append(label)
    return [(sect_label, label) for label in deduped]


def _extract_trailing_section_subsection_repeals(clean: str) -> List[tuple[str, str]]:
    """Extract mixed repeal tails like ``§ 27 ja § 28 lõige 2``."""
    clean = _normalize_ee_parse_text(clean)
    _NUM_PAT = _EE_NUM_ATOM
    results: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s+(' + _NUM_PAT + r')\s+'
        r'l[oõ]ige\s+(' + _NUM_PAT + r')'
        r'(?=\s*(?:\bning\b|\bja\b|,|;|tunnistatakse\b|$))',
        clean,
        re.IGNORECASE,
    ):
        item = (
            _normalize_num(match.group(1).strip()),
            _normalize_num(match.group(2).strip()),
        )
        if item not in seen:
            seen.add(item)
            results.append(item)
    return results


def _extract_trailing_section_item_repeals(clean: str) -> List[tuple[str, str, str]]:
    """Extract mixed repeal tails like ``§ 37 lõike 1 punkt 4`` after a subsection list."""
    _NUM_PAT = r'\d+(?:\s+\d+)?'
    results: List[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s+(' + _NUM_PAT + r')\s+'
        r'l[oõ]ike(?:te|tes|st|s|t|ga)?\s+(' + _NUM_PAT + r')\s+'
        r'punkt(?:id|ide|ides|i|is)?\s+(' + _NUM_PAT + r')'
        r'(?=\s*(?:\bning\b|\bja\b|,|;|tunnistatakse\b|$))',
        clean,
        re.IGNORECASE,
    ):
        item = (
            _normalize_num(match.group(1).strip()),
            _normalize_num(match.group(2).strip()),
            _normalize_num(match.group(3).strip()),
        )
        if item not in seen:
            seen.add(item)
            results.append(item)
    return results


def _extract_trailing_section_item_companion_subsection_repeals(
    clean: str,
) -> List[tuple[str, str]]:
    """Extract subsection tails owned by later explicit section-item segments.

    Example:
      ``paragrahvi 90 lõike 3 punkt 2 ja lõige 4 ning § 121 lõike 3 punkt 2
      ja lõige 4 tunnistatakse kehtetuks``

    The first companion subsection is handled by the same-section helper. This
    helper recovers the repeated companion subsection for later explicit
    section-item segments so the tail is not silently dropped.
    """
    _NUM_PAT = r'\d+(?:\s+\d+)?'
    results: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s*(' + _NUM_PAT + r')',
        clean,
        re.IGNORECASE,
    ):
        sect_label = _normalize_num(match.group(1).strip())
        local_tail = clean[match.start():]
        next_section = re.search(r'(?:\bning\b|\bja\b|,)\s+§(?:-d)?\s+\d', local_tail[1:], re.IGNORECASE)
        if next_section:
            local_tail = local_tail[: next_section.start() + 1]
        local_tail = re.sub(
            r'^(?:\bning\b|\bja\b|,)\s+§\s*' + _NUM_PAT + r'\s+',
            '',
            local_tail,
            count=1,
            flags=re.IGNORECASE,
        )
        if not re.search(
            r'l[oõ]ike(?:te|tes|st|s|t|ga)?\s+' + _NUM_PAT + r'\s+'
            r'punkt(?:id|ide|ides|i|is)?\s+'
            r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*',
            local_tail,
            re.IGNORECASE,
        ):
            continue
        for item in _extract_same_section_extra_subsection_repeals_after_items(local_tail, sect_label):
            if item not in seen:
                seen.add(item)
                results.append(item)
    return results


def _extract_same_section_extra_subsection_repeals_after_items(
    clean: str,
    sect_label: str,
) -> List[tuple[str, str]]:
    """Extract subsection repeals that trail a leading plural-item repeal.

    Example:
      ``paragrahvi 21 lõike 1 punktid 5, 6 1 ja lõige 1 1 ... tunnistatakse kehtetuks``
      ``paragrahvi 14 lõike 1 punktid 3 1, 4, 5 1–8 ja lõiked 2–4 ...``
    """
    _NUM_PAT = r'\d+(?:\s+\d+)?'
    next_section = re.search(r'(?:\bning\b|\bja\b|,)\s+§(?:-d)?\s+\d', clean, re.IGNORECASE)
    local_clean = clean[: next_section.start()] if next_section else clean
    results: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+l[oõ]ige\s+(' + _NUM_PAT + r')'
        + r'(?=\s*(?:\bning\b|\bja\b|,|§|;|tunnistatakse\b|$))',
        local_clean,
        re.IGNORECASE,
    ):
        item = (sect_label, _normalize_num(match.group(1).strip()))
        if item not in seen:
            results.append(item)
            seen.add(item)
    _SUB_LIST_PAT = (
        _NUM_PAT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT
        + r')?(?:\s*,\s*'
        + _NUM_PAT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT
        + r')?)*(?:\s+ja\s+'
        + _NUM_PAT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT
        + r')?)?'
    )
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+l[oõ]iked\s+('
        + _SUB_LIST_PAT
        + r')'
        + r'(?=\s*(?:\bning\b|\bja\b|,|§|;|tunnistatakse\b|$))',
        local_clean,
        re.IGNORECASE,
    ):
        for raw_sub in _expand_ee_numeric_list(match.group(1).strip()):
            item = (sect_label, raw_sub)
            if item not in seen:
                results.append(item)
                seen.add(item)
    return results


def _extract_same_section_extra_subsection_label_ranges_after_items(
    clean: str,
    sect_label: str,
) -> tuple[tuple[str, str, str], ...]:
    """Extract label ranges from subsection repeals after a plural-item repeal."""
    _NUM_PAT = _EE_NUM_ATOM
    clean = _normalize_ee_parse_text(clean)
    next_section = re.search(r'(?:\bning\b|\bja\b|,)\s+§(?:[' + _EE_DASH_CLASS + r']d)?\s+\d', clean, re.IGNORECASE)
    local_clean = clean[: next_section.start()] if next_section else clean
    ranges: list[tuple[str, str, str]] = []
    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+l[oõ]iked\s+('
        + _NUM_PAT
        + r'(?:\s*[.]?\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?(?:\s*,\s*'
        + _NUM_PAT
        + r'(?:\s*[.]?\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?)*(?:\s+ja\s+'
        + _NUM_PAT
        + r'(?:\s*[.]?\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?)?'
        + r')(?=\s*(?:\bning\b|\bja\b|,|§|;|tunnistatakse\b|$))',
        local_clean,
        re.IGNORECASE,
    ):
        for start, end in _ee_label_ranges(match.group(1).strip()):
            ranges.append((sect_label, start, end))
    return tuple(ranges)


def _extract_secondary_sentence_and_subsection_repeals(
    clean: str,
) -> tuple[List[tuple[str, str, str]], List[tuple[str, str]]]:
    """Extract later sentence/subsection repeals from a mixed compound clause.

    Example:
      ``..., § 27 lõike 1 teine lause, lõike 3 teine lause ja lõige 4
      ning §-d 27 1–29 tunnistatakse kehtetuks``
    """
    _NUM_PAT = r'\d+(?:\s+\d+)?'
    sentence_repeals: List[tuple[str, str, str]] = []
    subsection_repeals: List[tuple[str, str]] = []
    seen_sentences: set[tuple[str, str, str]] = set()
    seen_subsections: set[tuple[str, str]] = set()

    for match in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s*(' + _NUM_PAT + r')\s+'
        r'l[oõ]ike\s+(' + _NUM_PAT + r')\s+'
        r'(esimene|teine|kolmas|neljas)\s+lause',
        clean,
        re.IGNORECASE,
    ):
        sect_label = _normalize_num(match.group(1).strip())
        first = (
            sect_label,
            _normalize_num(match.group(2).strip()),
            match.group(3).lower(),
        )
        if first not in seen_sentences:
            sentence_repeals.append(first)
            seen_sentences.add(first)

        tail = clean[match.end():]
        next_section = re.search(r'(?:\bning\b|\bja\b|,)\s+§(?:-d)?\s+\d', tail, re.IGNORECASE)
        local_tail = tail[: next_section.start()] if next_section else tail

        for local in re.finditer(
            r'(?:\s*,\s*|\s+\bja\b\s+)l[oõ]ike\s+(' + _NUM_PAT + r')\s+'
            r'(esimene|teine|kolmas|neljas)\s+lause',
            local_tail,
            re.IGNORECASE,
        ):
            item = (
                sect_label,
                _normalize_num(local.group(1).strip()),
                local.group(2).lower(),
            )
            if item not in seen_sentences:
                sentence_repeals.append(item)
                seen_sentences.add(item)

        for local in re.finditer(
            r'(?:\s*,\s*|\s+\bja\b\s+)l[oõ]ige\s+(' + _NUM_PAT + r')'
            r'(?=\s*(?:\bning\b|\bja\b|,|;|tunnistatakse\b|$))',
            local_tail,
            re.IGNORECASE,
        ):
            item = (sect_label, _normalize_num(local.group(1).strip()))
            if item not in seen_subsections:
                subsection_repeals.append(item)
                seen_subsections.add(item)

    return sentence_repeals, subsection_repeals


def _extract_sentence_repeal_note(clean: str) -> Optional[str]:
    """Extract a normalized sentence-repeal note from one clause when present.

    Supports both phrase orders:
      - ``teine lause tunnistatakse kehtetuks``
      - ``jäetakse välja teine lause``
    and coordinated variants like ``teine ja kolmas lause ...``.
    """
    sentence_pat = (
        r'((?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda))'
        r'(?:\s+ja\s+(?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda)))?\s+lause)'
    )
    m = re.search(
        sentence_pat + r'\s+(tunnistatakse\s+kehtetuks|j[aä]etakse\s+v[aä]lja)',
        clean,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(1).strip().lower()} {m.group(2).strip().lower()}"
    m = re.search(
        r'(j[aä]etakse\s+v[aä]lja)\s+' + sentence_pat,
        clean,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(2).strip().lower()} {m.group(1).strip().lower()}"
    return None


def _extract_division_repeals(clean: str) -> List[tuple[str, str]]:
    """Extract repealed chapter/division pairs from repeal clauses."""
    clean = _normalize_ee_parse_text(clean)
    _NUM_PAT = _EE_NUM_ATOM
    _LIST_PAT = (
        _NUM_PAT
        + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?(?:\s*,\s*'
        + _NUM_PAT
        + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?)*(?:\s+(?:ja|ning)\s+'
        + _NUM_PAT
        + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r'\s*[.]?)?)*'
    )
    seen: list[tuple[str, str]] = []
    for match in re.finditer(
        r'\b(?:seaduse\s+)?(' + _NUM_PAT + r')\s*[.]\s*peatüki\s+(' + _LIST_PAT + r')\s*jagu\b',
        clean,
        re.IGNORECASE,
    ):
        ch_label = _normalize_num(match.group(1).strip())
        for div_label in _expand_ee_numeric_list(match.group(2).strip()):
            pair = (ch_label, div_label)
            if pair not in seen:
                seen.append(pair)
    return seen


def _extract_appendix_table_categories(text: str) -> List[str]:
    """Extract appendix table category labels from a clause like B- ja BE-kategooria."""
    m = re.search(r'\bmuudetakse\s+(.+?)\s+veerg\b', text, re.IGNORECASE)
    if not m:
        return []
    cats = re.findall(r'\b([A-Z][A-Z]?\d?)\b', m.group(1))
    deduped: List[str] = []
    for cat in cats:
        if cat not in deduped:
            deduped.append(cat)
    return deduped


def _extract_global_text_replace_exclusions(text: str) -> List[tuple[tuple[str, str], ...]]:
    """Extract explicit provision exclusions from statute-wide replace clauses."""
    m = re.search(r'\bvälja\s+arvatud\s+(.+?)\s*,\s*asendatakse\b', text, re.IGNORECASE)
    if not m:
        return []

    exclusions: List[tuple[tuple[str, str], ...]] = []
    excluded_clause = m.group(1).strip()
    consumed_spans: list[tuple[int, int]] = []

    for subsection_match in re.finditer(
        r'§(?:-s|-des)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+(?:lõikes?|lõigetes)\s+(.+?)'
        r'(?=(?:\s*,\s*§|\s+(?:ja|ning)\s+§|$))',
        excluded_clause,
        re.IGNORECASE,
    ):
        sec_label = _normalize_num(subsection_match.group(1))
        subsection_labels = _expand_ee_numeric_list(subsection_match.group(2))
        for sub_label in subsection_labels:
            exclusions.append((
                ("section", sec_label),
                ("subsection", sub_label),
            ))
        consumed_spans.append(subsection_match.span())

    if consumed_spans:
        parts_source: list[str] = []
        last = 0
        for start, end in consumed_spans:
            parts_source.append(excluded_clause[last:start])
            last = end
        parts_source.append(excluded_clause[last:])
        excluded_clause = " ".join(parts_source)
        excluded_clause = re.sub(r'(?:\s*,\s*){2,}', ', ', excluded_clause)
        excluded_clause = re.sub(r'^\s*(?:,\s*)+', '', excluded_clause)
        excluded_clause = re.sub(r'(?:,\s*)?(?:ja|ning)\s*$', '', excluded_clause, flags=re.IGNORECASE)
        excluded_clause = excluded_clause.strip(" ,")

    for section_match in re.finditer(
        r'§(?:-s|-des)?\s*((?:\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)(?:\s*(?:,|ja|ning)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)'
        r'(?=(?:\s*,\s*§|\s+(?:ja|ning)\s+§|$))',
        excluded_clause,
        re.IGNORECASE,
    ):
        for sec_label in _expand_ee_numeric_list(section_match.group(1)):
            exclusions.append((("section", sec_label),))

    seen: set[tuple[tuple[str, str], ...]] = set()
    deduped: list[tuple[tuple[str, str], ...]] = []
    for path in exclusions:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return sorted(deduped, key=lambda path: (len(path), path))


def _normalize_text_replace_args(
    text: str,
    old_text: str | None,
    new_text: str | None,
) -> tuple[str | None, str | None]:
    """Normalize EE text_replace args for delete and insert-after-word clauses."""
    if (old_text is None and new_text
            and re.search(r'\bjäetakse\b.*\bvälja\b', text, re.IGNORECASE)):
        return new_text, ""
    if (old_text is not None and new_text is not None
            and re.search(r'\benne\s+arvu\b', text, re.IGNORECASE)):
        return old_text, f"{new_text}{old_text}"
    if (old_text is not None and new_text is not None
            and re.search(r'\bpärast\s+arvu\b', text, re.IGNORECASE)):
        separator = '' if re.match(r'^[\s–‒\-.,;:)]', new_text) else ' '
        return old_text, f"{old_text}{separator}{new_text}"
    if (old_text is not None and new_text is not None
            and re.search(r'\benne\s+(?:sõn[au]|tekstiosa|lauseosa)\b', text, re.IGNORECASE)):
        return old_text, f"{new_text} {old_text}"
    if (old_text is not None and new_text is not None
            and re.search(r'\bpärast\s+(?:sõn[au]|tekstiosa|lauseosa)\b', text, re.IGNORECASE)):
        return old_text, f"{old_text} {new_text}"
    return old_text, new_text


def _infer_text_replace_mode(
    text: str,
    old_text: str | None,
    new_text: str | None,
) -> str:
    """Infer an explicit replace mode from textual cues."""
    if old_text and not new_text:
        return "delete"
    if old_text and new_text:
        if re.search(r'\benne\s+(?:sõn[au][a-z]*|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE):
            return "insert_before"
        if re.search(r'\bpärast\s+(?:sõn[au][a-z]*|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE):
            return "insert_after"
    return "replace"


def _set_text_replace_payload_attrs(
    payload: IRNode,
    clean: str,
    old_text: str | None,
    new_text: str | None,
    *,
    scope_chapters: tuple[str, ...] = (),
    exclude_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    generic_minister_plural: bool = False,
    old_titles: tuple[str, ...] = (),
    source_family: str = "",
) -> tuple[IRNode, object | None]:
    """Populate text-replace payload attrs and a typed rewrite witness."""
    from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
    from lawvm.estonia.ee_instruction_waist import make_text_rewrite_witness
    from lawvm.estonia.text_morphology import sentence_indexes_from_notes

    attrs = dict(payload.attrs)
    if old_text:
        attrs["old_text"] = old_text
    rewrite_mode = _infer_text_replace_mode(clean, old_text, new_text)
    attrs["rewrite_mode"] = rewrite_mode
    case_inflected = _should_case_inflect_text_replace(clean, old_text, new_text)
    if "läbivalt" in _instruction_preamble(clean).lower() or (
        rewrite_mode == "insert_after" and case_inflected
    ):
        attrs["all_occurrences"] = True
    if case_inflected:
        attrs["case_inflected"] = True
    if scope_chapters:
        attrs["scope_chapters"] = list(scope_chapters)
    if exclude_paths:
        attrs["exclude_paths"] = [tuple(path) for path in exclude_paths]
    if generic_minister_plural:
        attrs["generic_minister_plural"] = True
    if old_titles:
        attrs["old_titles"] = list(old_titles)
    if source_family:
        attrs["source_family"] = source_family
    if not old_text and not new_text and not scope_chapters and not exclude_paths and not generic_minister_plural and not old_titles and not source_family and not case_inflected:
        return replace(payload, attrs=attrs), None
    witness = make_text_rewrite_witness(
        clean,
        old_surface=old_text or "",
        new_surface=new_text or "",
        mode=rewrite_mode,
        case_inflected=case_inflected,
        scope_chapters=scope_chapters,
        exclude_paths=exclude_paths,
        generic_minister_plural=generic_minister_plural,
        old_titles=old_titles,
        source_family=source_family,
    )
    attrs["rewrite_witness"] = witness
    sentence_note_scope = _instruction_preamble(clean).lower()
    sentence_indexes = tuple(sentence_indexes_from_notes(sentence_note_scope))
    if sentence_indexes:
        attrs["sentence_target_meta"] = make_sentence_target_meta(sentence_indexes=sentence_indexes)
    return replace(payload, attrs=attrs), witness


def _sentence_scoped_text_replace_payload_for_target(
    payload: IRNode,
    clean: str,
    target: LegalAddress,
    *,
    target_count: int,
) -> IRNode:
    """Keep sentence scope only when the sentence phrase belongs to this target."""
    if target_count <= 1 or "sentence_target_meta" not in payload.attrs:
        return payload
    attrs = dict(payload.attrs)
    sentence_indexes = _target_local_sentence_indexes(clean, target)
    if sentence_indexes:
        from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta

        attrs["sentence_target_meta"] = make_sentence_target_meta(sentence_indexes=sentence_indexes)
        attrs.pop("suppress_sentence_target_meta", None)
    else:
        attrs.pop("sentence_target_meta", None)
        attrs["suppress_sentence_target_meta"] = True
    return replace(payload, attrs=attrs)


def _target_local_sentence_indexes(clean: str, target: LegalAddress) -> tuple[int, ...]:
    """Return sentence indexes carried by this target's own text span."""
    from lawvm.estonia.text_morphology import sentence_indexes_from_notes

    if not target.path or target.path[0][0] != "section":
        return ()
    target_section_label = target.path[0][1]
    section_label = re.escape(target_section_label.replace("_", " "))
    leaf_kind, leaf_label_raw = target.path[-1]
    leaf_label = re.escape(leaf_label_raw.replace("_", " "))
    preamble = _normalize_ee_parse_text(_instruction_preamble(clean)).lower()
    section_spans = _target_section_instruction_spans(preamble, target_section_label)
    search_spans = section_spans or (preamble,)
    if leaf_kind == "section":
        mention_patterns = (rf"(?:paragrahvi|§)\s+{section_label}\b",)
    elif leaf_kind == "subsection" and len(target.path) >= 2:
        subsection_label = re.escape(target.path[1][1].replace("_", " "))
        for span in search_spans:
            for group_match in re.finditer(
                r"\bl[oõ]igete\w*\s+"
                r"(?P<labels>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|ning|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)"
                r"\s+(?P<tail>[^,;§]{0,80})",
                span,
                re.IGNORECASE,
            ):
                labels = tuple(_normalize_num(label) for label in _expand_ee_numeric_list(group_match.group("labels")))
                if target.path[1][1] not in labels:
                    continue
                indexes = sentence_indexes_from_notes(group_match.group("tail"))
                if indexes:
                    return tuple(indexes)
        mention_patterns = (
            rf"(?:paragrahvi|§)\s+{section_label}\s+l[oõ]ike\w*\s+{subsection_label}\b",
            rf"\bl[oõ]ike\w*\s+{subsection_label}\b",
        )
    elif leaf_kind == "item":
        mention_patterns = (
            rf"\bpunkt\w*\s+{leaf_label}\b",
        )
    else:
        return ()
    for span in search_spans:
        for pattern in mention_patterns:
            for match in re.finditer(pattern, span, re.IGNORECASE):
                local_tail = span[match.end(): match.end() + 100]
                local_tail = re.split(
                    r"\s*(?:,\s*|ning\s+|ja\s+)(?=(?:§|paragrahvi|l[oõ]ike|punkt))",
                    local_tail,
                    maxsplit=1,
                )[0]
                indexes = sentence_indexes_from_notes(local_tail)
                if indexes:
                    return tuple(indexes)
    return ()


def _target_section_instruction_spans(preamble: str, target_section_label: str) -> tuple[str, ...]:
    """Return preamble spans governed by this section before the next section ref."""
    section_refs = list(re.finditer(r"(?:paragrahvi|§)\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\b", preamble))
    if not section_refs:
        return ()
    spans: list[str] = []
    for index, match in enumerate(section_refs):
        if _normalize_num(match.group(1)) != target_section_label:
            continue
        end = section_refs[index + 1].start() if index + 1 < len(section_refs) else len(preamble)
        spans.append(preamble[match.start():end])
    return tuple(spans)


def _set_sentence_insert_payload_attrs(payload: IRNode, clean: str) -> IRNode:
    from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
    from lawvm.estonia.text_morphology import sentence_index_from_notes

    clean_lower = _instruction_preamble(clean).lower()
    if "algust täiendatakse" in clean_lower:
        attrs = dict(payload.attrs)
        attrs["sentence_target_meta"] = make_sentence_target_meta(
            sentence_indexes=(),
            mode="prepend_item",
        )
        return replace(payload, attrs=attrs)

    if (
        "teine lause loetakse kolmandaks lauseks" in clean_lower
        and "täiendatakse teise lausega" in clean_lower
    ):
        attrs = dict(payload.attrs)
        attrs["sentence_target_meta"] = make_sentence_target_meta(
            sentence_indexes=(1,),
            mode="insert_before",
        )
        return replace(payload, attrs=attrs)

    sentence_index = sentence_index_from_notes(clean_lower)
    if sentence_index is None:
        return payload
    mode = ""
    if "loetakse teiseks lauseks" in clean_lower and "esimese lausega" in clean_lower:
        mode = "insert_before"
    elif re.search(r"\bpärast\b.*\blause(?:te)?ga\b", clean_lower):
        mode = "insert_after"
    if not mode:
        return payload
    attrs = dict(payload.attrs)
    attrs["sentence_target_meta"] = make_sentence_target_meta(
        sentence_indexes=(sentence_index,),
        mode=mode,
    )
    return replace(payload, attrs=attrs)


def _set_sentence_replace_payload_attrs(payload: IRNode, clean: str) -> IRNode:
    from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
    from lawvm.estonia.text_morphology import sentence_indexes_from_notes

    sentence_indexes = sentence_indexes_from_notes(_instruction_preamble(clean).lower())
    if not sentence_indexes:
        return payload
    attrs = dict(payload.attrs)
    attrs["sentence_target_meta"] = make_sentence_target_meta(
        sentence_indexes=sentence_indexes,
    )
    return replace(payload, attrs=attrs)


def _typed_text_replace_patch(old_text: str | None, new_text: str | None) -> TextPatchSpec | None:
    if not old_text:
        return None
    return TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text=old_text),
        replacement=new_text or "",
    )


def _set_appendix_table_payload_attrs(
    payload: IRNode,
    source_text: str,
    *,
    marker: str,
    categories: tuple[str, ...],
) -> tuple[IRNode, object | None]:
    """Populate appendix-table attrs and a typed appendix witness."""
    from lawvm.estonia.ee_instruction_waist import make_text_rewrite_witness

    attrs = dict(payload.attrs)
    attrs["appendix_table_update"] = True
    attrs["appendix_marker"] = marker
    attrs["appendix_table_categories"] = list(categories)
    witness = make_text_rewrite_witness(
        source_text,
        new_surface=payload.text or "",
        source_family="appendix_table_update",
        appendix_table_update=True,
        appendix_marker=marker,
        appendix_table_categories=categories,
    )
    attrs["rewrite_witness"] = witness
    return replace(payload, attrs=attrs), witness


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_ee_ops(
    op_text: str,
    source: OperationSource,
    seq_start: int = 1,
) -> List[LegalOperation]:
    """Parse one Estonian amendment op text → List[LegalOperation].

    `op_text` is the stripped text of a single numbered item (e.g. "1) paragrahvi 26...").
    Multiple LegalOperations may result from a single text when multiple provisions
    are targeted (e.g. "paragrahvi 12 täiendatakse lõigetega 4 ja 5").

    Callers iterate op texts and call this function per item.
    """
    ops: List[LegalOperation] = []
    seq = seq_start

    # Normalize non-breaking spaces to regular spaces.  \xa0 in amendment
    # text (e.g. from <tavatekst> fallback extraction) would cause mismatches
    # against the oracle which uses _tavatekst_text (already normalizes \xa0).
    op_text = op_text.replace('\xa0', ' ')

    # Strip leading "N) " item marker
    clean = re.sub(r'^\(?\d+\)\s*', '', op_text.strip())
    instruction_scope = _instruction_preamble(clean)
    local_effective = _extract_clause_local_effective_date(instruction_scope)
    if local_effective:
        source = replace(source, effective=local_effective)

    action = _classify_verb(clean)

    # Statute-wide text replacement: "seaduse kogu tekstis asendatakse sõna X sõnadega Y"
    # Also: "seaduses asendatakse läbivalt number X numbriga Y"
    # Also: "seaduse tekstis asendatakse ..." (without "kogu")
    # Also: "seaduse N.–M. peatükis asendatakse ..." (chapter-range text replace)
    # Also: "seaduses asendatakse sõna X sõnaga Y" (inessive directly + asendatakse + noun)
    # Target is the whole statute (empty path); may generate multiple text_replace
    # ops when the clause contains several quoted OLD→NEW pairs.
    statute_ref = (
        r'(?:'
        r'[\wÕÄÖÜŠŽõäöüšž-]*'
        r'(?:seadus|seadustik|koodeks|määrus)[a-z]*'
        r')'
    )
    if re.search(
        rf'\b{statute_ref}\s+kogu\s+teksti[s]?\s+asendatakse'
        rf'|\b{statute_ref}\s+asendatakse\s+läbivalt'
        rf'|\b{statute_ref}\s+teksti[s]?\s+asendatakse'
        rf'|\b{statute_ref}\s*,\s*välja\s+arvatud\s+[^.]+?\s+asendatakse\s+(?:sõna[a-z]*|sõnu|arv[a-z]*|aastaarv[a-z]*|tekstiosa[a-z]*|lauseosa[a-z]*|number[a-z]*)'
        rf'|\b{statute_ref}\s+\d+[^§]*peatüki[s]?\s+(?:pealkirjas\s+)?asendatakse'
        rf'|\b{statute_ref}\s+asendatakse\s+(?:sõna[a-z]*|arv[a-z]*|aastaarv[a-z]*|tekstiosa[a-z]*|lauseosa[a-z]*|number[a-z]*)',
        clean, re.IGNORECASE,
    ):
        heading_targets = _extract_explicit_heading_targets(clean)
        if (
            heading_targets
            and all(target.path and target.path[0][0] == "chapter" for target in heading_targets)
            and re.search(r'\bpeatüki(?:\s+\d+[.]?\s*jao)?\s+pealkirja(?:s|st)\b', clean, re.IGNORECASE)
        ):
            pairs = _extract_text_replace_pairs(clean)
            if not pairs:
                old_t, new_t = _extract_text_replace_args(clean)
                if old_t is not None or new_t is not None:
                    pairs = [(old_t or "", new_t or "")]
            for heading_target in heading_targets:
                for old_t, new_t in pairs:
                    payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
                    payload, _rewrite_witness = _set_text_replace_payload_attrs(
                        payload,
                        clean,
                        old_t,
                        new_t,
                    )
                    ops.append(LegalOperation(
                        op_id=f"ee-chapter-heading-text_replace-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=heading_target,
                        payload=payload,
                        text_patch=_typed_text_replace_patch(old_t, new_t),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
            return ops
        pairs = _extract_text_replace_pairs(clean)
        if not pairs:
            old_t, new_t = _extract_text_replace_args(clean)
            if old_t is not None or new_t is not None:
                pairs = [(old_t or "", new_t or "")]
        for old_t, new_t in pairs:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
            scope_chapters = _extract_global_text_replace_chapter_scope(clean)
            exclusions = _extract_global_text_replace_exclusions(clean)
            payload, _rewrite_witness = _set_text_replace_payload_attrs(
                payload,
                clean,
                old_t,
                new_t,
                scope_chapters=tuple(scope_chapters),
                exclude_paths=tuple(exclusions),
            )
            ops.append(LegalOperation(
                op_id=f"ee-global-text_replace-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=()),
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        return ops

    # Division-level repeal: "seaduse N. peatüki M. jagu tunnistatakse kehtetuks"
    # RT often renders these as surviving division headings plus boundary stubs,
    # so emit a division-targeted repeal op instead of falling through.
    if action == "repeal":
        _NUM_DIV = r'\d+(?:\s+\d+)?'
        m_subdivision_repeal = re.search(
            r'\b(?:seaduse\s+)?(' + _NUM_DIV + r')\s*[.]\s*peatüki\s+(' + _NUM_DIV + r')\s*[.]\s*jao\s+'
            r'(' + _NUM_DIV + r')\s*[.]\s*jaotis(?:e|es|t)?\s+tunnistatakse\s+kehtetuks',
            clean,
            re.IGNORECASE,
        )
        if m_subdivision_repeal:
            ch_label = _normalize_num(m_subdivision_repeal.group(1).strip())
            div_label = _normalize_num(m_subdivision_repeal.group(2).strip())
            sub_label = _normalize_num(m_subdivision_repeal.group(3).strip())
            ops.append(LegalOperation(
                op_id=f"ee-repeal-subdivision-{ch_label}-{div_label}-{sub_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(
                    path=(("chapter", ch_label), ("division", div_label), ("subdivision", sub_label))
                ),
                payload=None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            return ops
        division_repeals = _extract_division_repeals(clean)
        for ch_label, div_label in division_repeals:
            ops.append(LegalOperation(
                op_id=f"ee-repeal-division-{ch_label}-{div_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=(("chapter", ch_label), ("division", div_label))),
                payload=None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        if division_repeals and not re.search(r'(?:§|\bparagrahv|\blõige)', clean, re.IGNORECASE):
            return ops

    # Chapter-level replace: "seaduse N. peatükk muudetakse ja sõnastatakse
    # järgmiselt: „N. peatükk ... § K ...”".
    if action == "replace":
        _NUM_CH = r'\d+(?:\s+\d+)?'
        m_ch_replace = re.search(
            r'\b(?:seaduse\s+)?(' + _NUM_CH + r')\s*[.]\s*peatükk\w*'
            r'\s+muudetakse(?:\s+ja\s+sõnastatakse\s+järgmises\s+sõnastuses)?'
            r'(?:\s+järgmises\s+sõnastuses|\s+järgmiselt)?',
            clean,
            re.IGNORECASE,
        )
        if m_ch_replace:
            content = _extract_quoted_content(clean)
            if content:
                ch_label = _normalize_num(m_ch_replace.group(1).strip())
                ops.append(LegalOperation(
                    op_id=f"ee-replace-chapter-{ch_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=LegalAddress(path=(("chapter", ch_label),)),
                    payload=IRNode(kind=IRNodeKind.CONTENT, text=content),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                return ops

    # Division-level replace: "seaduse N. peatüki M. jagu muudetakse ja
    # sõnastatakse järgmiselt: „M. jagu Title § K ..."
    if action == "replace":
        _NUM_DIV = r'\d+(?:\s+\d+)?'
        m_div_replace = re.search(
            r'\b(?:seaduse\s+)?(' + _NUM_DIV + r')\s*[.]\s*peatüki\s+(' + _NUM_DIV + r')\s*[.]\s*jagu\w*'
            r'\s+muudetakse(?:\s+ja\s+sõnastatakse\s+järgmises\s+sõnastuses)?(?:\s+järgmises\s+sõnastuses|\s+järgmiselt)?',
            clean,
            re.IGNORECASE,
        )
        if m_div_replace:
            content = _extract_quoted_content(clean)
            if content:
                ch_label = _normalize_num(m_div_replace.group(1).strip())
                div_label = _normalize_num(m_div_replace.group(2).strip())
                ops.append(LegalOperation(
                    op_id=f"ee-replace-division-{ch_label}-{div_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=LegalAddress(path=(("chapter", ch_label), ("division", div_label))),
                    payload=IRNode(kind=IRNodeKind.CONTENT, text=content),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                return ops

    # Chapter-level repeal: "seaduse N. [ja M.] peatükk tunnistatakse kehtetuks"
    # Also: "N. ja M. peatükk tunnistatakse kehtetuks" (no "seaduse" prefix)
    # Handles ranges: "4. ja 5. peatükk", "4.–5. peatükk", "4. peatükk"
    if action == "repeal":
        _NUM_CH = _EE_NUM_ATOM
        _ch_repeal_labels: List[str] = []
        _CH_LIST_PAT = (
            _NUM_CH
            + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
            + _NUM_CH
            + r'\s*[.]?)?(?:\s*,\s*'
            + _NUM_CH
            + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
            + _NUM_CH
            + r'\s*[.]?)?)*(?:\s+(?:ja|ning)\s+'
            + _NUM_CH
            + r'\s*[.]?(?:\s*[' + _EE_DASH_CLASS + r']\s*'
            + _NUM_CH
            + r'\s*[.]?)?)*'
        )
        for m_ch_repeal in re.finditer(
            r'\b(' + _CH_LIST_PAT + r')\s*peatükk\w*(?=.*\btunnistatakse\s+kehtetuks\b)',
            _normalize_ee_parse_text(clean),
            re.IGNORECASE | re.DOTALL,
        ):
            for label in _expand_ee_numeric_list(m_ch_repeal.group(1).strip()):
                if label not in _ch_repeal_labels:
                    _ch_repeal_labels.append(label)
        if _ch_repeal_labels:
            for ch_label in _ch_repeal_labels:
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-chapter-{ch_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=LegalAddress(path=(("chapter", ch_label),)),
                    payload=None,
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
            return ops

    # Singular section sentence repeal: "Kommertspandiseaduse § 37 esimene lause
    # tunnistatakse kehtetuks". Emit a sentence-scoped replace; apply can
    # redirect section targets to subsection 1 when the section node itself is
    # only a heading.
    if action == "repeal":
        sentence_note = _extract_sentence_repeal_note(clean)
        m_single_sect_sentence_repeal = re.search(
            r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
            r'(?:(?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda))\s+lause\s+'
            r'(?:tunnistatakse\s+kehtetuks|j[aä]etakse\s+v[aä]lja)'
            r'|(?:tunnistatakse\s+kehtetuks|j[aä]etakse\s+v[aä]lja)\s+'
            r'(?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda))\s+lause)',
            clean,
            re.IGNORECASE,
        )
        if m_single_sect_sentence_repeal and sentence_note:
            sect_label = _normalize_num(m_single_sect_sentence_repeal.group(1))
            from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
            from lawvm.estonia.text_morphology import sentence_indexes_from_notes

            ops.append(LegalOperation(
                op_id=f"ee-replace-section-sentence-{sect_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=LegalAddress(path=(("section", sect_label),)),
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={
                        "sentence_target_meta": make_sentence_target_meta(
                            sentence_indexes=sentence_indexes_from_notes(sentence_note)
                        )
                    },
                ),
                source=source,
                provenance_tags=(clean[:200], sentence_note),
            ))
            return ops

    # Singular section repeal should win before broader plural-subsection
    # patterns inspect later clauses in the same sentence.
    if action == "repeal":
        section_clean = _strip_leading_clause_wrapper(clean)
        m_single_sect_repeal = re.match(
            r'^\s*paragrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+tunnistatakse\s+kehtetuks\b',
            section_clean,
            re.IGNORECASE,
        )
        if not m_single_sect_repeal:
            m_single_sect_repeal = re.match(
                r'^\s*paragrahvi\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+tunnistatakse\s+kehtetuks\b',
                section_clean,
                re.IGNORECASE,
            )
        if m_single_sect_repeal:
            num = _normalize_num(m_single_sect_repeal.group(1))
            ops.append(LegalOperation(
                op_id=f"ee-repeal-sect-{num}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=(("section", num),)),
                payload=None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            return ops

    # Plural section repeal/text-replace: "paragrahvid N, M ja K tunnistatakse kehtetuks"
    # and "§-d N–M ning K tunnistatakse kehtetuks"
    # Also handles "paragrahvides N ja M asendatakse" and en-dash ranges "paragrahvid N–M".
    # paragrahvid (nominative), paragrahvide (genitive), paragrahvides (inessive)
    _NUM_PAT_PS = r'\d+(?:\s+\d+)?'
    _RANGE_OR_NUM = _NUM_PAT_PS + r'(?:\s*[–‒\-]\s*' + _NUM_PAT_PS + r')?'
    section_clean = _strip_leading_clause_wrapper(clean)
    m_multi_sect = re.search(
        r'(?:\bparagrahvid(?:e[s]?)?\b|§-d)\s+('
        + _RANGE_OR_NUM
        + r'(?:\s*,\s*'
        + _RANGE_OR_NUM
        + r')*(?:\s+(?:ja|ning)\s+'
        + _RANGE_OR_NUM
        + r')?)',
        section_clean, re.IGNORECASE
    )
    prior_target_context = section_clean[: m_multi_sect.start()] if m_multi_sect else ""
    prior_target_mentions = bool(
        prior_target_context
        and re.search(
            r'(?:\d[\d\s]*\.\s*peat[üu]k|\d[\d\s]*\.\s*jagu|\d[\d\s]*\.\s*jaotis'
            r'|\bparagrahvi(?:s|st)?\b|\bparagrahv\b|§\s*\d|\bl[oõ]ike\b|\bpunkti\b)',
            prior_target_context,
            re.IGNORECASE,
        )
    )
    if m_multi_sect and action in ("repeal", "text_replace", "replace") and not prior_target_mentions:
        old_t, new_t = _extract_text_replace_args(clean) if action == "text_replace" else (None, None)
        content = _extract_quoted_content(clean) if action == "replace" else None
        split_content = (
            _split_plural_section_replace_payload(content)
            if action == "replace" and content
            else None
        )
        raw_section_group = m_multi_sect.group(1).strip()
        expanded_nums = _expand_ee_numeric_list(raw_section_group)
        section_selection_meta = None
        if action == "repeal":
            from lawvm.estonia.ee_instruction_waist import make_section_selection_meta

            section_selection_meta = make_section_selection_meta(
                explicit_labels=expanded_nums,
                plain_numeric_ranges=_plain_numeric_ranges(raw_section_group),
            )
        for num in expanded_nums:
            addr = LegalAddress(path=(("section", num),))
            payload = None
            _rewrite_witness = None
            if action == "text_replace" and new_t:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                payload, _rewrite_witness = _set_text_replace_payload_attrs(payload, clean, old_t, new_t)
            elif action == "replace" and content:
                payload_text = split_content.get(num) if split_content is not None else content
                if payload_text:
                    payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text)
                    payload = _set_sentence_replace_payload_attrs(payload, clean)
            elif action == "repeal" and section_selection_meta is not None:
                payload = IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={"section_selection_meta": section_selection_meta},
                )
            ops.append(LegalOperation(
                op_id=f"ee-{action}-sect-{num}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action(action),
                target=addr,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t) if action == "text_replace" else None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        if action == "repeal":
            seen_sub_paths: set[tuple[tuple[str, str], ...]] = set()
            for sect_label, sub_label in _extract_secondary_subsection_repeals(clean):
                sub_path = (("section", sect_label), ("subsection", sub_label))
                if sub_path in seen_sub_paths:
                    continue
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-sub-{sect_label}-{sub_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=LegalAddress(path=sub_path),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seen_sub_paths.add(sub_path)
                seq += 1
            seen_sections = {op.target.path for op in ops if op.target.path}
            for _num in _extract_sd_section_nums(clean):
                sect_path = (("section", _num),)
                if sect_path in seen_sections:
                    continue
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-sect-{_num}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=LegalAddress(path=sect_path),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seen_sections.add(sect_path)
                seq += 1
        return ops

    # Division retitle/reclassification: "seaduse N. peatüki tekst loetakse M. jaoks
    # ja see pealkirjastatakse järgmiselt: „M. jagu Title”".
    m_div_reclass = re.search(
        r'seaduse\s+(\d+)[.]\s*peatüki\s+tekst\s+loetakse\s+(\d[\d\s]*)[.]\s*jaoks'
        r'.*?pealkirjastatakse\s+järgnevalt|'
        r'seaduse\s+(\d+)[.]\s*peatüki\s+tekst\s+loetakse\s+(\d[\d\s]*)[.]\s*jaoks'
        r'.*?pealkirjastatakse\s+järgmiselt',
        clean,
        re.IGNORECASE,
    )
    if m_div_reclass:
        ch_label = _normalize_num((m_div_reclass.group(1) or m_div_reclass.group(3)).strip())
        div_label = _normalize_num((m_div_reclass.group(2) or m_div_reclass.group(4)).strip())
        content = _extract_quoted_content(clean)
        if content:
            ops.append(LegalOperation(
                op_id=f"ee-insert-div-reclass-{div_label}-in-ch-{ch_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("insert"),
                target=LegalAddress(path=(("chapter", ch_label), ("division", div_label))),
                payload=IRNode(kind=IRNodeKind.CONTENT, text=content),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            return ops

    # Statute-level insert: "seadustikku täiendatakse §-dega N ja M järgmises sõnastuses:"
    # Also: "seadust täiendatakse paragrahviga N järgmises sõnastuses:" (word form instead of §-ga)
    # The target is not a specific provision but the statute itself (inserted after existing §N)
    statute_level_insert = bool(
        re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+paragrahviga', clean, re.IGNORECASE)
        # Also: "seaduse N. peatükki täiendatakse §-dega M" (chapter-qualified section insert)
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)[a-z\s\d.]*peatük[k]?[i]+\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
        # Also: "seaduse N. peatüki M. jagu täiendatakse §-ga K" (division-qualified section insert)
        or re.search(r'\bjag[u-z]*\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
        # Also: "alljaotist täiendatakse §-dega 34^1 ja 34^2"; the shared
        # IR currently flattens jaotis/alljaotis under the owning jagu.
        or re.search(r'\balljaotis\w*\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
        # Also: "seaduse N. peatüki M. jagu täiendatakse K. jaotisega ..."
        or re.search(r'\bjag[u-z]*\s+täiendatakse\s+\d[\d\s]*[.]\s*jaotisega', clean, re.IGNORECASE)
        # Also: "seaduse N. peatükki täiendatakse N. jaoga järgmises sõnastuses: „N. jagu ... § K ..."
        # Handled below as whole-division inserts so split-division identity is preserved
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)[a-z\s\d.]*peatük[k]?[i]+\s+täiendatakse\s+\d+[.]\s*jaoga', clean, re.IGNORECASE)
        # Also: "seadust täiendatakse N 1. peatükiga järgmises sõnastuses: „N 1. peatükk ... § K ..."
        # Also: "seadust täiendatakse N 1. ja N 2. peatükiga ..." (multi-chapter insert)
        # Whole-chapter insert — handled specially below
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+\d+[\d\s]*[.]\s*(?:ja\s+\d+[\d\s]*[.]\s*)*peatükiga', clean, re.IGNORECASE)
        # Also: "seadust täiendatakse III 1. osaga järgmises sõnastuses:" (part insert)
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+[IVXLCDM]+[\d\s]*[.]\s*osaga', clean, re.IGNORECASE)
    )
    if statute_level_insert:
        m_section_renumber = re.search(
            r'\bparagrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+loetakse\s+'
            r'(?:§-ks|paragrahviks)\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
            clean,
            re.IGNORECASE,
        )
        if m_section_renumber:
            old_label = _normalize_num(m_section_renumber.group(1))
            new_label = _normalize_num(m_section_renumber.group(2))
            ops.append(LegalOperation(
                op_id=f"ee-renumber-section-{old_label}-{new_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("renumber"),
                target=LegalAddress(path=(("section", old_label),)),
                destination=LegalAddress(path=(("section", new_label),)),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1

        # Section numbers: "71 1" (superscript as separate digit) or just "71"
        # Also handles en-dash ranges: "§-dega 89 28 ‒89 31" → 89_28..89_31
        _NUM_PAT = r'\d+(?:\s+\d+)?'
        container_prefix: tuple[tuple[str, str], ...] = ()
        m_div_qualified_insert = re.search(
            r'seaduse\s+(' + _NUM_PAT + r')\s*[.]\s*peatüki\s+(' + _NUM_PAT + r')\s*[.]\s*jagu\w*\s+täiendatakse\s+§[‑–‒-](?:de)?ga',
            clean,
            re.IGNORECASE,
        )
        if not m_div_qualified_insert:
            m_div_qualified_insert = re.search(
                r'seaduse\s+(' + _NUM_PAT + r')\s*[.]\s*peatüki\s+(' + _NUM_PAT + r')\s*[.]\s*jao\s+'
                r'\d[\d\s]*\s*[.]\s*jaotise\s+\d[\d\s]*\s*[.]\s*alljaotis\w*\s+'
                r'täiendatakse\s+§[‑–‒-](?:de)?ga',
                clean,
                re.IGNORECASE,
            )
        if m_div_qualified_insert:
            container_prefix = (
                ("chapter", _normalize_num(m_div_qualified_insert.group(1).strip())),
                ("division", _normalize_num(m_div_qualified_insert.group(2).strip())),
            )
        else:
            m_ch_qualified_insert = re.search(
                r'seaduse\s+(' + _NUM_PAT + r')\s*[.]\s*peatük[k]?[iü]\s+täiendatakse\s+§[‑–‒-](?:de)?ga',
                clean,
                re.IGNORECASE,
            )
            if m_ch_qualified_insert:
                container_prefix = (
                    ("chapter", _normalize_num(m_ch_qualified_insert.group(1).strip())),
                )
        # Try §-ga / §-dega form first (also handles ranges), then paragrahviga form
        m_secs = re.search(
            r'§[‑–‒-](?:de)?ga\s+(' + _NUM_PAT + r'(?:\s*[–‒\-]\s*' + _NUM_PAT + r'|(?:\s+ja\s+' + _NUM_PAT + r'))*)',
            clean, re.IGNORECASE
        )
        if not m_secs:
            m_secs = re.search(
                r'paragrahviga\s+(' + _NUM_PAT + r'(?:\s+ja\s+' + _NUM_PAT + r')*)',
                clean, re.IGNORECASE
            )
        content = _extract_quoted_content(clean)
        if content:
            content = _unwrap_nested_statute_insert_payload(content)
        payload = IRNode(kind=IRNodeKind.CONTENT, text=content or "") if content else None

        # Division-qualified subdivision insert:
        #   "seaduse N. peatüki M. jagu täiendatakse K. jaotisega ..."
        # The current shared IR has no dedicated jaotis node, so flatten the
        # quoted subdivision body into ordinary section inserts under the
        # existing chapter/division container.
        _is_jaotis_insert = bool(
            re.search(r'jagu\w*\s+täiendatakse\s+\d[\d\s]*[.]\s*jaotisega', clean, re.IGNORECASE)
        )
        if content and _is_jaotis_insert:
            m_jaotis = re.search(
                r'(?:seaduse\s+)?(' + _NUM_PAT + r')\s*[.]\s*peatüki\s+(' + _NUM_PAT + r')\s*[.]\s*jagu\w*'
                r'\s+täiendatakse\s+(' + _NUM_PAT + r')\s*[.]\s*jaotisega',
                clean,
                re.IGNORECASE,
            )
            if m_jaotis:
                ch_label = _normalize_num(m_jaotis.group(1).strip())
                div_label = _normalize_num(m_jaotis.group(2).strip())
                _NUM_SEC = r'\d+(?:\s+\d+)?'
                for m_sec in re.finditer(
                    r'§\s*(' + _NUM_SEC + r')\s*[.]\s*(.*?)(?=§\s*\d|$)',
                    content,
                    re.DOTALL,
                ):
                    sec_num = _normalize_num(m_sec.group(1).strip())
                    sec_content = m_sec.group(0).strip()
                    sec_addr = LegalAddress(
                        path=(("chapter", ch_label), ("division", div_label), ("section", sec_num))
                    )
                    ops.append(LegalOperation(
                        op_id=f"ee-insert-sect-{sec_num}-in-jaotis-{div_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("insert"),
                        target=sec_addr,
                        payload=IRNode(kind=IRNodeKind.CONTENT, text=sec_content),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                if ops:
                    return ops

        # Whole-part (osa) insert: "seadust täiendatakse III 1. osaga järgmises sõnastuses:"
        # Roman numeral (e.g. III) → integer (3). Superscript digit is dropped (oracle uses
        # plain ordinal labels: III¹. osa → part:3).
        # The oracle IR has parts as childless title markers at body level, with sections also
        # at body level (NOT nested inside the part). So we emit:
        #   1. insert part:N (title-only node, no children)
        #   2. insert section:M for each § in the payload (body-level)
        _is_osa_insert = bool(
            re.search(r'täiendatakse\s+[IVXLCDM]+[\d\s]*[.]\s*osaga', clean, re.IGNORECASE)
        )
        if not m_secs and content and _is_osa_insert:
            m_osa = re.search(
                r'täiendatakse\s+([IVXLCDM]+)[\d\s]*[.]\s*osaga',
                clean, re.IGNORECASE,
            )
            if m_osa:
                roman_str = m_osa.group(1).upper()
                part_int = _roman_to_int(roman_str)
                if part_int is not None:
                    part_label = str(part_int)
                    # Extract part title: text after "osa" keyword before first §
                    m_pt_title = re.search(
                        r'[IVXLCDM]+[\d\s]*[.]\s*osa\s+(.*?)(?=§\s*\d)',
                        content, re.DOTALL | re.IGNORECASE,
                    )
                    part_title = m_pt_title.group(1).strip() if m_pt_title else ""
                    # 1. Emit part insert (title-only, no payload → grafter creates childless node)
                    part_payload = IRNode(kind=IRNodeKind.CONTENT, text=part_title)
                    ops.append(LegalOperation(
                        op_id=f"ee-insert-part-{part_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("insert"),
                        target=LegalAddress(path=(("part", part_label),)),
                        payload=part_payload,
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                    # 2. Extract and emit each § as a flat body-level section insert
                    # Pattern: "§ 10 1 . Title\x01 (1) ..." with optional space before dot
                    _NUM_SEC = r'\d+(?:\s+\d+)?'
                    for m_sec in re.finditer(
                        r'§\s*(' + _NUM_SEC + r')\s*[.]\s*(.*?)(?=§\s*\d|$)',
                        content, re.DOTALL,
                    ):
                        sec_num = _normalize_num(m_sec.group(1).strip())
                        sec_content = m_sec.group(0).strip()
                        sec_addr = LegalAddress(path=(("section", sec_num),))
                        ops.append(LegalOperation(
                            op_id=f"ee-insert-sect-{sec_num}-in-part-{part_label}-{source.statute_id}",
                            sequence=seq,
                            action=_to_structural_action("insert"),
                            target=sec_addr,
                            payload=IRNode(kind=IRNodeKind.CONTENT, text=sec_content),
                            source=source,
                            provenance_tags=(clean[:200],),
                        ))
                        seq += 1
                    return ops

        # Whole-chapter insert: "seadust täiendatakse N 1. peatükiga järgmises sõnastuses: „N 1. peatükk ...""
        # Also: "seadust täiendatakse N 1. ja N 2. peatükiga ..." (two chapters in one op).
        # Emit one chapter insert op per chapter found in the quoted content.
        # Do NOT emit one-op-per-section (that puts every section in the wrong place).
        _NUM_CH = r'\d+(?:\s+\d+)?'
        # Pattern: "täiendatakse N. peatükiga" (single) or
        #          "täiendatakse N. ja M. peatükiga" (multiple).
        # The full sequence between "täiendatakse" and "peatükiga" consists of
        # one or more "N ." segments joined by " ja ".
        _CH_SEQ = r'(?:' + _NUM_CH + r'\s*[.]\s*(?:ja\s+)?)+\s*'
        _is_peatukk_insert = bool(
            re.search(r'täiendatakse\s+' + _CH_SEQ + r'peatükiga', clean, re.IGNORECASE)
        )
        if not m_secs and content and _is_peatukk_insert:
            # Extract all chapter numbers: "täiendatakse 3 1 . ja 3 2 . peatükiga"
            # → ["3 1", "3 2"]
            m_ch_all = re.search(
                r'täiendatakse\s+(' + _CH_SEQ + r')peatükiga',
                clean, re.IGNORECASE,
            )
            ch_labels: List[str] = []
            if m_ch_all:
                raw_ch_group = m_ch_all.group(1)
                # Split on " ja " and strip trailing "." from each part
                raw_parts = re.split(r'\s*\bja\b\s*', raw_ch_group.strip())
                for raw_part in raw_parts:
                    raw_part = raw_part.strip().rstrip('.').strip()
                    if raw_part:
                        ch_labels.append(_normalize_num(raw_part))
            if not ch_labels:
                # Fallback: find chapter numbers from the quoted content itself
                ch_in_content = re.findall(
                    r'\b(' + _NUM_CH + r')\s*[.]\s*peatükk\b', content, re.IGNORECASE
                )
                ch_labels = [_normalize_num(r.strip()) for r in ch_in_content]
            if ch_labels:
                # When multiple chapters, split content on EXACT chapter heading boundaries.
                # Build a split pattern from the known chapter labels so we don't accidentally
                # split on intermediate digits (e.g. "3 1 . peatükk" must not split at "1 .").
                def _ch_label_to_pat(lbl: str) -> str:
                    if '_' in lbl:
                        base, suf = lbl.split('_', 1)
                        return rf'{re.escape(base)}\s+{re.escape(suf)}\s*[.]\s*peatükk\b'
                    return rf'{re.escape(lbl)}\s*[.]\s*peatükk\b'

                all_ch_pats = '|'.join(_ch_label_to_pat(lbl) for lbl in ch_labels)
                split_pat = rf'(?=(?:{all_ch_pats}))'
                ch_blocks = re.split(split_pat, content, flags=re.IGNORECASE)

                # Pair up chapter labels with content blocks
                paired: List[Tuple[str, str]] = []
                for block in ch_blocks:
                    block = block.strip()
                    if not block:
                        continue
                    # Identify which chapter label this block corresponds to
                    matched_label: Optional[str] = None
                    for lbl in ch_labels:
                        pat = _ch_label_to_pat(lbl)
                        if re.match(pat, block, re.IGNORECASE):
                            matched_label = lbl
                            break
                    if matched_label:
                        paired.append((matched_label, block))
                if not paired:
                    # Could not split — emit one op with the whole content for first label
                    paired = [(ch_labels[0], content)]
                for ch_label, ch_content in paired:
                    addr = LegalAddress(path=(("chapter", ch_label),))
                    ops.append(LegalOperation(
                        op_id=f"ee-insert-chapter-{ch_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("insert"),
                        target=addr,
                        payload=IRNode(kind=IRNodeKind.CONTENT, text=ch_content),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                return ops

        # Division insert: "N. peatükki täiendatakse M. jaoga järgmises sõnastuses: „M. jagu Title § K ..."
        # Emit a single division-level insert op targeting chapter:N/division:M so grafter can
        # build a structured division IRNode (with all its section children) and insert it in one
        # step.  Previously emitted per-section ops that landed at the wrong parent path.
        _is_jagu_insert = bool(
            re.search(r'täiendatakse\s+\d[\d\s]*[.]\s*jaoga', clean, re.IGNORECASE)
        )
        if not m_secs and content and _is_jagu_insert:
            # Extract "N. peatükki täiendatakse M. jaoga"
            m_jagu = re.search(
                r'(\d[\d\s]*)[.]\s*peatükki\s+täiendatakse\s+(\d[\d\s]*)[.]\s*jaoga',
                clean, re.IGNORECASE,
            )
            if m_jagu:
                ch_label = _normalize_num(m_jagu.group(1).strip())
                div_label = _normalize_num(m_jagu.group(2).strip())
                # Preserve superscript division identity (e.g. 1^1 -> 1_1) so inserted split divisions do not collapse onto division 1.
                addr = LegalAddress(path=(("chapter", ch_label), ("division", div_label)))
                ops.append(LegalOperation(
                    op_id=f"ee-insert-div-{div_label}-in-ch-{ch_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("insert"),
                    target=addr,
                    payload=IRNode(kind=IRNodeKind.CONTENT, text=content),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
                return ops

        if m_secs:
            # Expand any en-dash ranges and collect all section labels
            raw_group = m_secs.group(1).strip()
            raw_parts = re.split(r'\s+ja\s+', raw_group)
            expanded: list[str] = []
            for raw_part in raw_parts:
                raw_part = raw_part.strip()
                m_endash = re.match(r'^(\d+(?:\s+\d+)?)\s*[–‒\-]\s*(\d+(?:\s+\d+)?)$', raw_part)
                if m_endash:
                    s_norm = _normalize_num(m_endash.group(1).strip())
                    e_norm = _normalize_num(m_endash.group(2).strip())
                    if '_' in s_norm and '_' in e_norm:
                        s_base, s_suf = s_norm.rsplit('_', 1)
                        e_base, e_suf = e_norm.rsplit('_', 1)
                        if s_base == e_base and s_suf.isdigit() and e_suf.isdigit():
                            for suf in range(int(s_suf), int(e_suf) + 1):
                                expanded.append(f"{s_base}_{suf}")
                            continue
                    if s_norm.isdigit() and e_norm.isdigit():
                        for n in range(int(s_norm), int(e_norm) + 1):
                            expanded.append(str(n))
                        continue
                    expanded.extend([s_norm, e_norm])
                else:
                    expanded.append(_normalize_num(raw_part))
            section_payloads = (
                _split_plural_section_replace_payload(content or "")
                if content and len(expanded) > 1
                else None
            )
            # Each expanded section label is a separate insert op
            for num in expanded:
                addr = LegalAddress(path=container_prefix + (("section", num),))
                op_payload = payload
                if section_payloads is not None and num in section_payloads:
                    op_payload = IRNode(kind=IRNodeKind.CONTENT, text=section_payloads[num])
                ops.append(LegalOperation(
                    op_id=f"ee-insert-sect-{num}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("insert"),
                    target=addr,
                    payload=op_payload,
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
        return ops

    # Plural subsection repeal/replace: "paragrahvi N lõiked M ja K tunnistatakse kehtetuks"
    # Also handles en-dash ranges: "paragrahvi N lõiked M–K tunnistatakse kehtetuks"
    # and comma lists: "paragrahvi N lõiked M, P ja K muudetakse"
    # Also handles inessive plural "lõigetes M ja K" (= "in subsections M and K"),
    # used in text_replace clauses: "§ 13 lõigetes 5¹ ja 5² asendatakse sõna X sõnaga Y"
    # and "§ N lõigetes M ja K" shorthand (§ instead of paragrahvi).
    # Search only in preamble (before „ or järgmiselt:) to avoid matching cross-references
    # inside replacement body text like "sotsiaalmaksuseaduse § 10 lõigetes 1–3 ja 4".
    _clean_preamble = _instruction_preamble(clean)
    _NUM_PAT_SUB = _EE_NUM_ATOM
    sentence_note = _extract_sentence_repeal_note(_clean_preamble)
    if action == "repeal" and sentence_note:
        explicit_targets = _extract_multiple_explicit_targets(clean)
        subsection_targets = [
            target
            for target in explicit_targets
            if len(target.path) == 2
            and target.path[0][0] == "section"
            and target.path[1][0] == "subsection"
        ]
        if subsection_targets:
            from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
            from lawvm.estonia.text_morphology import sentence_indexes_from_notes

            sentence_indexes = sentence_indexes_from_notes(sentence_note)
            for target in subsection_targets:
                ops.append(LegalOperation(
                    op_id=(
                        f"ee-replace-sub-sentence-{target.path[0][1]}-{target.path[1][1]}-"
                        f"{source.statute_id}"
                    ),
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=target,
                    payload=IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="",
                        attrs={
                            "sentence_target_meta": make_sentence_target_meta(
                                sentence_indexes=sentence_indexes
                            )
                        },
                    ),
                    source=source,
                    provenance_tags=(clean[:200], sentence_note),
                ))
                seq += 1
            return ops
    m_plural_sub_sentence_repeal = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]igete\s+(.+?)\s+(esimene|teine|kolmas|neljas)\s+lause\s+tunnistatakse\s+kehtetuks',
        _clean_preamble,
        re.IGNORECASE,
    )
    if m_plural_sub_sentence_repeal and action == "repeal":
        sect_label = _normalize_num(m_plural_sub_sentence_repeal.group(1))
        raw_subs = m_plural_sub_sentence_repeal.group(2).strip()
        expanded = _expand_ee_numeric_list(raw_subs)
        sentence_word = m_plural_sub_sentence_repeal.group(3).lower()
        from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
        from lawvm.estonia.text_morphology import sentence_indexes_from_notes

        sentence_indexes = sentence_indexes_from_notes(f"{sentence_word} lause tunnistatakse kehtetuks")
        for num in expanded:
            ops.append(LegalOperation(
                op_id=f"ee-replace-sub-sentence-{sect_label}-{num}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=LegalAddress(path=(("section", sect_label), ("subsection", num))),
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={
                        "sentence_target_meta": make_sentence_target_meta(
                            sentence_indexes=sentence_indexes
                        )
                    },
                ),
                source=source,
                provenance_tags=(clean[:200], f"{sentence_word} lause tunnistatakse kehtetuks"),
            ))
            seq += 1
        if expanded:
            return ops

    m_singular_sub_sentence_repeal = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ike(?:s)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(esimene|teine|kolmas|neljas)\s+lause\s+tunnistatakse\s+kehtetuks',
        _clean_preamble,
        re.IGNORECASE,
    )
    if m_singular_sub_sentence_repeal and action == "repeal":
        sect_label = _normalize_num(m_singular_sub_sentence_repeal.group(1))
        sub_label = _normalize_num(m_singular_sub_sentence_repeal.group(2))
        sentence_word = m_singular_sub_sentence_repeal.group(3).lower()
        from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
        from lawvm.estonia.text_morphology import sentence_indexes_from_notes

        ops.append(LegalOperation(
            op_id=f"ee-replace-sub-sentence-{sect_label}-{sub_label}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("replace"),
            target=LegalAddress(path=(("section", sect_label), ("subsection", sub_label))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "sentence_target_meta": make_sentence_target_meta(
                        sentence_indexes=sentence_indexes_from_notes(
                            f"{sentence_word} lause tunnistatakse kehtetuks"
                        )
                    )
                },
            ),
            source=source,
            provenance_tags=(clean[:200], f"{sentence_word} lause tunnistatakse kehtetuks"),
        ))
        return ops

    m_plural_sub = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(?:(?:pealkiri)\s+(?:ning|ja)\s+)?'
        r'(?:l[oõ]iked|l[oõ]igetes)\s+(' + _NUM_PAT_SUB + r'(?:\s*(?:,|[' + _EE_DASH_CLASS + r'])\s*' + _NUM_PAT_SUB +
        r')*(?:\s+ja\s+' + _NUM_PAT_SUB + r')?)',
        _clean_preamble, re.IGNORECASE
    )
    if m_plural_sub and action in ("repeal", "replace", "text_replace"):
        sect_label = _normalize_num(m_plural_sub.group(1))
        raw_subs = m_plural_sub.group(2).strip()
        expanded = _expand_ee_numeric_list(raw_subs)
        target_addrs = [
            LegalAddress(path=(("section", sect_label), ("subsection", num)))
            for num in expanded
        ]
        if action == "text_replace":
            explicit_targets = _extract_multiple_explicit_targets(clean)
            if len(explicit_targets) > len(target_addrs):
                target_addrs = explicit_targets
        content = _extract_quoted_content(clean)
        split_content = None
        if action == "replace" and content:
            maybe_split = _split_plural_subsection_replace_payload(content)
            if maybe_split and set(expanded).issubset(set(maybe_split)):
                split_content = maybe_split
        old_t, new_t = _extract_text_replace_args(clean) if action == "text_replace" else (None, None)
        if action == "text_replace":
            old_t, new_t = _normalize_text_replace_args(clean, old_t, new_t)
        subsection_selection_meta = None
        if action == "repeal":
            from lawvm.estonia.ee_instruction_waist import make_subsection_selection_meta

            subsection_selection_meta = make_subsection_selection_meta(
                explicit_labels=expanded,
                plain_numeric_ranges=_plain_numeric_ranges(raw_subs),
                label_ranges=_ee_label_ranges(raw_subs),
            )
        for addr in target_addrs:
            payload = None
            _rewrite_witness = None
            if action == "text_replace" and new_t:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                payload, _rewrite_witness = _set_text_replace_payload_attrs(payload, clean, old_t, new_t)
                payload = _sentence_scoped_text_replace_payload_for_target(
                    payload,
                    clean,
                    addr,
                    target_count=len(target_addrs),
                )
            elif action == "replace" and content:
                num = addr.path[-1][1]
                payload_text = split_content[num] if split_content is not None else content
                payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text)
                payload = _set_sentence_replace_payload_attrs(payload, clean)
            elif action == "repeal" and subsection_selection_meta is not None:
                payload = IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={"subsection_selection_meta": subsection_selection_meta},
                )
            ops.append(LegalOperation(
                op_id=f"ee-{action}-{str(addr)}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action(action),
                target=addr,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t) if action == "text_replace" else None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        if (
            action == "text_replace"
            and old_t
            and new_t
            and re.search(r'\bpealkirja(?:s|st)\b', clean, re.IGNORECASE)
            and not any(op.target.special is FacetKind.HEADING for op in ops)
        ):
            heading_payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
            heading_payload, _heading_witness = _set_text_replace_payload_attrs(heading_payload, clean, old_t, new_t)
            ops.append(LegalOperation(
                op_id=f"ee-text_replace-title-{sect_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=(("section", sect_label),), special=FacetKind.HEADING),
                payload=heading_payload,
                text_patch=_typed_text_replace_patch(old_t, new_t),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        if expanded:
            # Also check for "ning §-d N ja M tunnistatakse kehtetuks" in the same clause
            # e.g. "paragrahvi 7 lõige 3 ning §-d 7 1 ja 33 tunnistatakse kehtetuks"
            if action == "repeal":
                prefix_target = parse_target(_clean_preamble[:m_plural_sub.start()].rstrip(" ,;"))
                if prefix_target is not None and prefix_target.path:
                    if not any(op.target.path == prefix_target.path for op in ops):
                        ops.insert(0, LegalOperation(
                            op_id=f"ee-repeal-prefix-{seq}-{source.statute_id}",
                            sequence=seq,
                            action=_to_structural_action("repeal"),
                            target=prefix_target,
                            source=source,
                            provenance_tags=(clean[:200],),
                        ))
                        seq += 1
                _extra = _extract_sd_section_nums(clean)
                for _num in _extra:
                    addr2 = LegalAddress(path=(("section", _num),))
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sect-{_num}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=addr2,
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                seen_sub_paths = {
                    op.target.path
                    for op in ops
                    if op.target.path
                    and len(op.target.path) >= 2
                    and op.target.path[0][0] == "section"
                    and op.target.path[1][0] == "subsection"
                }
                for extra_sect, extra_sub in _extract_secondary_subsection_repeals(clean):
                    sub_path = (("section", extra_sect), ("subsection", extra_sub))
                    if sub_path in seen_sub_paths:
                        continue
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=sub_path),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seen_sub_paths.add(sub_path)
                    seq += 1
                seen_item_paths = {
                    op.target.path
                    for op in ops
                    if op.target.path
                    and len(op.target.path) >= 3
                    and op.target.path[0][0] == "section"
                    and op.target.path[1][0] == "subsection"
                    and op.target.path[2][0] == "item"
                }
                for extra_sect, extra_sub, extra_item in _extract_trailing_section_item_repeals(clean):
                    item_path = (("section", extra_sect), ("subsection", extra_sub), ("item", extra_item))
                    if item_path in seen_item_paths:
                        continue
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-item-{extra_sect}-{extra_sub}-{extra_item}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=item_path),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seen_item_paths.add(item_path)
                    seq += 1
            return ops

    # Singular item sentence repeal: "paragrahvi N lõike M punkti K teine ja kolmas lause tunnistatakse kehtetuks".
    m_same_section_companion_item_repeal = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ike[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'punkt(?:id|ide|ides|i|is)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(?:ja|ning)\s+l[oõ]ike[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'punkt(?:id|ide|ides|i|is)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'tunnistatakse\s+kehtetuks',
        _clean_preamble,
        re.IGNORECASE,
    )
    if m_same_section_companion_item_repeal and action == "repeal":
        sect_label = _normalize_num(m_same_section_companion_item_repeal.group(1))
        first_sub_label = _normalize_num(m_same_section_companion_item_repeal.group(2))
        first_item_label = _normalize_num(m_same_section_companion_item_repeal.group(3))
        second_sub_label = _normalize_num(m_same_section_companion_item_repeal.group(4))
        second_item_label = _normalize_num(m_same_section_companion_item_repeal.group(5))
        for sub_label, item_label in (
            (first_sub_label, first_item_label),
            (second_sub_label, second_item_label),
        ):
            ops.append(LegalOperation(
                op_id=f"ee-repeal-item-{sect_label}-{sub_label}-{item_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=(("section", sect_label), ("subsection", sub_label), ("item", item_label))),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        return ops

    m_item_and_section_subsection_repeal = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ike[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'punkt(?:i|is)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(?:ja|ning)\s+§\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ige\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'tunnistatakse\s+kehtetuks',
        _clean_preamble,
        re.IGNORECASE,
    )
    if m_item_and_section_subsection_repeal and action == "repeal":
        first_sect = _normalize_num(m_item_and_section_subsection_repeal.group(1))
        first_sub = _normalize_num(m_item_and_section_subsection_repeal.group(2))
        first_item = _normalize_num(m_item_and_section_subsection_repeal.group(3))
        second_sect = _normalize_num(m_item_and_section_subsection_repeal.group(4))
        second_sub = _normalize_num(m_item_and_section_subsection_repeal.group(5))
        for target in (
            LegalAddress(path=(("section", first_sect), ("subsection", first_sub), ("item", first_item))),
            LegalAddress(path=(("section", second_sect), ("subsection", second_sub))),
        ):
            ops.append(LegalOperation(
                op_id=f"ee-repeal-compound-item-subsection-{target}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=target,
                source=source,
                provenance_tags=(clean[:200], "ee_compound_section_item_subsection_repeal"),
            ))
            seq += 1
        return ops

    m_item_sentence_repeal = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ike[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'punkti[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'((?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda))(?:\s+ja\s+(?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda)))?\s+lause)\s+'
        r'tunnistatakse\s+kehtetuks',
        _clean_preamble,
        re.IGNORECASE,
    )
    if m_item_sentence_repeal and action == "repeal":
        sect_label = _normalize_num(m_item_sentence_repeal.group(1))
        sub_label = _normalize_num(m_item_sentence_repeal.group(2))
        item_label = _normalize_num(m_item_sentence_repeal.group(3))
        sentence_phrase = m_item_sentence_repeal.group(4).strip().lower()
        from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
        from lawvm.estonia.text_morphology import sentence_indexes_from_notes

        ops.append(LegalOperation(
            op_id=f"ee-replace-item-sentence-{sect_label}-{sub_label}-{item_label}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("replace"),
            target=LegalAddress(path=(("section", sect_label), ("subsection", sub_label), ("item", item_label))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "sentence_target_meta": make_sentence_target_meta(
                        sentence_indexes=sentence_indexes_from_notes(
                            f"{sentence_phrase} tunnistatakse kehtetuks"
                        )
                    )
                },
            ),
            source=source,
            provenance_tags=(clean[:200], f"{sentence_phrase} tunnistatakse kehtetuks"),
        ))
        return ops

    # Plural item repeal/replace/insert: "paragrahvi N [lõike M] punktid K ja L ..."
    # Also: "paragrahvi N punktid K, L ja M tunnistatakse kehtetuks" (no subsection)
    _NUM_PAT_IT = r'\d+(?:\s+\d+)?'
    _ITEM_LIST_PAT = (
        _NUM_PAT_IT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT_IT
        + r')?(?:\s*,\s*'
        + _NUM_PAT_IT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT_IT
        + r')?)*(?:\s+ja\s+'
        + _NUM_PAT_IT
        + r'(?:\s*[–‒\-]\s*'
        + _NUM_PAT_IT
        + r')?)*'
    )
    m_plural_item = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)'
        r'(?:\s+l[oõ]ike[s]?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*))?'
        r'(?:\s+täiendatakse)?'
        r'\s+punkt(?:id|e)(?:ega|es)?\s+(' + _ITEM_LIST_PAT + r')',
        _clean_preamble, re.IGNORECASE
    )
    if m_plural_item and action in ("repeal", "replace", "text_replace", "insert"):
        sect_label = _normalize_num(m_plural_item.group(1))
        sub_label = _normalize_num(m_plural_item.group(2)) if m_plural_item.group(2) else None
        raw_items = m_plural_item.group(3).strip()
        expanded_items = _expand_ee_numeric_list(raw_items)
        target_addrs = []
        for num in expanded_items:
            path_parts: list[tuple[str, str]] = [("section", sect_label)]
            if sub_label:
                path_parts.append(("subsection", sub_label))
            path_parts.append(("item", num))
            target_addrs.append(LegalAddress(path=tuple(path_parts)))
        content = _extract_quoted_content(clean)
        split_content = None
        if action in ("replace", "insert") and content:
            maybe_split = _split_plural_item_payload(content)
            if maybe_split and set(expanded_items).issubset(set(maybe_split)):
                split_content = maybe_split
        old_t, new_t = _extract_text_replace_args(clean) if action == "text_replace" else (None, None)
        if action == "text_replace":
            old_t, new_t = _normalize_text_replace_args(clean, old_t, new_t)
            explicit_targets = _extract_multiple_explicit_targets(clean)
            if len(explicit_targets) > len(target_addrs):
                target_addrs = explicit_targets
        for addr in target_addrs:
            payload = None
            if action == "text_replace" and new_t:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                payload, _ = _set_text_replace_payload_attrs(payload, clean, old_t, new_t)
                payload = _sentence_scoped_text_replace_payload_for_target(
                    payload,
                    clean,
                    addr,
                    target_count=len(target_addrs),
                )
            elif action in ("replace", "insert") and content:
                item_label = addr.path[-1][1] if addr.path else ""
                payload_text = split_content[item_label] if split_content is not None else content
                payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text)
                if action == "replace":
                    payload = _set_sentence_replace_payload_attrs(payload, clean)
                else:
                    payload = _set_sentence_insert_payload_attrs(payload, clean)
            ops.append(LegalOperation(
                op_id=f"ee-{action}-item-{sect_label}-{num}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action(action),
                target=addr,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t) if action == "text_replace" else None,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        if expanded_items:
            if action == "repeal":
                prefix_target = parse_target(_clean_preamble[:m_plural_item.start()].rstrip(" ,;"))
                if (
                    prefix_target is not None
                    and prefix_target.path
                    and not any(op.target.path == prefix_target.path for op in ops)
                ):
                    ops.insert(0, LegalOperation(
                        op_id=f"ee-repeal-prefix-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=prefix_target,
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                from lawvm.estonia.ee_instruction_waist import make_subsection_selection_meta

                extra_same_section_sub_repeals = _extract_same_section_extra_subsection_repeals_after_items(
                    clean,
                    sect_label,
                )
                extra_same_section_labels_by_section: dict[str, list[str]] = {}
                for extra_sect, extra_sub in extra_same_section_sub_repeals:
                    labels = extra_same_section_labels_by_section.setdefault(extra_sect, [])
                    if extra_sub not in labels:
                        labels.append(extra_sub)
                extra_same_section_ranges_by_section: dict[str, list[tuple[str, str]]] = {}
                for extra_sect, start, end in _extract_same_section_extra_subsection_label_ranges_after_items(
                    clean,
                    sect_label,
                ):
                    ranges = extra_same_section_ranges_by_section.setdefault(extra_sect, [])
                    range_item = (start, end)
                    if range_item not in ranges:
                        ranges.append(range_item)
                for extra_sect, extra_sub in extra_same_section_sub_repeals:
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=(("section", extra_sect), ("subsection", extra_sub))),
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={
                                "subsection_selection_meta": make_subsection_selection_meta(
                                    explicit_labels=extra_same_section_labels_by_section.get(extra_sect, ()),
                                    label_ranges=extra_same_section_ranges_by_section.get(extra_sect, ()),
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1

                sentence_repeals, subsection_repeals = _extract_secondary_sentence_and_subsection_repeals(clean)
                companion_subsection_repeals = _extract_trailing_section_item_companion_subsection_repeals(clean)
                for extra_sect, extra_sub, sentence_word in sentence_repeals:
                    from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
                    from lawvm.estonia.text_morphology import sentence_indexes_from_notes

                    ops.append(LegalOperation(
                        op_id=f"ee-replace-sub-sentence-{extra_sect}-{extra_sub}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("replace"),
                        target=LegalAddress(path=(("section", extra_sect), ("subsection", extra_sub))),
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={
                                "sentence_target_meta": make_sentence_target_meta(
                                    sentence_indexes=sentence_indexes_from_notes(
                                        f"{sentence_word} lause tunnistatakse kehtetuks"
                                    )
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200], f"{sentence_word} lause tunnistatakse kehtetuks"),
                    ))
                    seq += 1

                secondary_subsection_labels_by_section: dict[str, list[str]] = {}
                for extra_sect, extra_sub in subsection_repeals:
                    labels = secondary_subsection_labels_by_section.setdefault(extra_sect, [])
                    if extra_sub not in labels:
                        labels.append(extra_sub)
                for extra_sect, extra_sub in companion_subsection_repeals:
                    labels = secondary_subsection_labels_by_section.setdefault(extra_sect, [])
                    if extra_sub not in labels:
                        labels.append(extra_sub)
                for extra_sect, extra_sub in subsection_repeals:
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=(("section", extra_sect), ("subsection", extra_sub))),
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={
                                "subsection_selection_meta": make_subsection_selection_meta(
                                    explicit_labels=secondary_subsection_labels_by_section.get(extra_sect, ())
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
                for extra_sect, extra_sub in companion_subsection_repeals:
                    if (extra_sect, extra_sub) in subsection_repeals:
                        continue
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=(("section", extra_sect), ("subsection", extra_sub))),
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={
                                "subsection_selection_meta": make_subsection_selection_meta(
                                    explicit_labels=secondary_subsection_labels_by_section.get(extra_sect, ())
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1

                for _num in _extract_sd_section_nums(clean):
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sect-{_num}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=(("section", _num),)),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    seq += 1
            return ops

    if (
        re.search(r'\blisa\s+\d+\b', clean, re.IGNORECASE)
        and re.search(r'\btabelis\s+muudetakse\b', clean, re.IGNORECASE)
        and 'sõnastatakse järgmiselt' in clean.lower()
    ):
        target = parse_target(clean)
        if target is not None:
            trimmed = re.split(r'\s+§\s*\d+\.\x01', clean, maxsplit=1)[0].strip()
            payload_text = trimmed.split('järgmiselt:', 1)[-1].strip() if 'järgmiselt:' in trimmed else ""
            if payload_text:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text)
                payload, _appendix_witness = _set_appendix_table_payload_attrs(
                    payload,
                    trimmed,
                    marker="Lisa 1",
                    categories=tuple(_extract_appendix_table_categories(trimmed)),
                )
                ops.append(LegalOperation(
                    op_id=f"ee-appendix-table-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=target,
                    payload=payload,
                    source=source,
                    provenance_tags=(trimmed[:200],),
                ))
                return ops

    # Nested amendment-point wrapper:
    #   "paragrahvi 2 täiendatakse muutmispunktiga 13 1 järgmises sõnastuses:
    #    „13 1) paragrahvi 37 1 täiendatakse lõikega 5 ...”"
    # The quoted payload is the real inner instruction for the target statute.
    if action == "insert" and re.search(r'\bmuutmispunktiga\b', clean, re.IGNORECASE):
        nested = _extract_quoted_content(clean)
        if nested:
            nested = nested.strip()
            if re.match(r'^\d[\d\s]*\)\s+', nested):
                return extract_ee_ops(nested, source, seq_start=seq_start)

    if re.search(r'\bnormitehnili\w*\s+märkus\w*\b', clean, re.IGNORECASE):
        content = _extract_quoted_content(clean)
        payload = IRNode(kind=IRNodeKind.CONTENT, text=content or "") if content else None
        ops.append(LegalOperation(
            op_id=f"ee-normitehniline-markus-{seq}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action(action),
            target=LegalAddress(path=()),
            payload=payload,
            source=source,
            provenance_tags=(clean[:200], "normitehniline_markus"),
        ))
        return ops

    # Amendment-of-amendment wrapper:
    #   "paragrahvi 75 tekst muudetakse ja sõnastatakse järgmiselt:
    #    „Toiduseaduse § 8 ... asendatakse ...”"
    # The wrapper targets the pending amendment act's own § 75, but the quoted
    # payload carries the real instruction for the target statute.
    if action == "replace" and re.search(
        r'\b(?:paragrahvi|§)\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\b.{0,80}\btekst\b',
        _instruction_preamble(clean),
        re.IGNORECASE | re.DOTALL,
    ):
        nested = _extract_quoted_content(clean)
        if nested:
            nested = nested.strip()
            nested_target = parse_target(nested)
            wrapper_target = parse_target(clean)
            nested_action = _classify_verb(nested)
            if (
                nested_target is not None
                and nested_target.path
                and wrapper_target is not None
                and wrapper_target.path
                and nested_target.path != wrapper_target.path
                and nested_action != "unknown"
                and re.search(
                    r'(?:seaduse|seadustiku|koodeksi|määruse)\b',
                    nested,
                    re.IGNORECASE,
                )
            ):
                return extract_ee_ops(nested, source, seq_start=seq_start)

    # Section renumber plus later insert in the same clause:
    #   "Paragrahv 27 1 loetakse §-ks 27 2 ja seadust täiendatakse §-ga 27 1 ..."
    # Emit the renumber first, but do not return: the later insert/replace path
    # still needs to compile from the same instruction.
    m_section_renumber = re.search(
        r'\bparagrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+loetakse\s+'
        r'(?:§-ks|paragrahviks)\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
        clean,
        re.IGNORECASE,
    )
    if m_section_renumber:
        old_label = _normalize_num(m_section_renumber.group(1))
        new_label = _normalize_num(m_section_renumber.group(2))
        ops.append(LegalOperation(
            op_id=f"ee-renumber-section-{old_label}-{new_label}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("renumber"),
            target=LegalAddress(path=(("section", old_label),)),
            destination=LegalAddress(path=(("section", new_label),)),
            source=source,
            provenance_tags=(clean[:200],),
        ))
        seq += 1

    # Direct chapter-qualified division insert: "seaduse N. peatükki täiendatakse M. jaoga ..."
    # Keep this before generic parse_target(), otherwise the first quoted § inside
    # the inserted division body collapses the whole clause into a bare section insert.
    if action == "insert":
        content = _extract_quoted_content(clean)
        m_direct_jagu_insert = re.search(
            r'\b(?:seaduse\s+)?(\d[\d\s]*)[.]\s*peatükki\s+täiendatakse\s+(\d[\d\s]*)[.]\s*jaoga',
            clean,
            re.IGNORECASE,
        )
        if content and m_direct_jagu_insert:
            ch_label = _normalize_num(m_direct_jagu_insert.group(1).strip())
            div_label = _normalize_num(m_direct_jagu_insert.group(2).strip())
            ops.append(LegalOperation(
                op_id=f"ee-insert-div-{div_label}-in-ch-{ch_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("insert"),
                target=LegalAddress(path=(("chapter", ch_label), ("division", div_label))),
                payload=IRNode(kind=IRNodeKind.CONTENT, text=content),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            return ops

    # Try to parse the provision target
    target = parse_target(clean)
    if target is None:
        # Could not identify a provision target — return unknown op for diagnostics
        ops.append(LegalOperation(
            op_id=f"ee-unknown-{seq}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action(action),
            target=LegalAddress(path=()),
            source=source,
            provenance_tags=(f"no_target: {clean[:200]}",),
        ))
        return ops

    # Build payload
    payload: Optional[IRNode] = None
    old_text: Optional[str] = None
    _rewrite_witness: object | None = None

    if (
        action == "text_replace"
        and re.search(r'\bj[aä]etakse\s+v[aä]lja\s+tekstiosa\b', clean, re.IGNORECASE)
    ):
        mixed_segments = _extract_mixed_delete_replace_segments(clean)
        if mixed_segments:
            for segment_text, segment_old, segment_new in mixed_segments:
                segment_payload = IRNode(kind=IRNodeKind.CONTENT, text=segment_new)
                segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                    segment_payload,
                    segment_text,
                    segment_old,
                    segment_new,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-combined-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=segment_payload,
                    text_patch=_typed_text_replace_patch(segment_old, segment_new),
                    source=source,
                    provenance_tags=(segment_text[:200],),
                ))
                seq += 1
            return ops

    if action == "text_replace":
        target_pairs = _extract_text_replace_pairs(clean)
        if len(target_pairs) > 1:
            explicit_targets = _extract_multiple_explicit_targets(clean)
            heading_targets = _extract_explicit_heading_targets(clean)
            missing_heading_targets = [
                heading_target
                for heading_target in heading_targets
                if heading_target not in explicit_targets
            ]
            if missing_heading_targets:
                combined_targets = explicit_targets + missing_heading_targets
                if all(target.special is FacetKind.HEADING for target in combined_targets):
                    explicit_targets = missing_heading_targets + explicit_targets
                else:
                    explicit_targets.extend(missing_heading_targets)
            pair_targets = (
                explicit_targets
                if len(explicit_targets) == len(target_pairs)
                else [target] * len(target_pairs)
            )
            explicit_heading_sections = {
                explicit_target.path[0][1]
                for explicit_target in explicit_targets
                if explicit_target.special is FacetKind.HEADING
                and explicit_target.path
                and len(explicit_target.path) == 1
                and explicit_target.path[0][0] == "section"
            }
            for pair_target, (old_t, new_t) in zip(pair_targets, target_pairs):
                pair_payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
                pair_payload, _rewrite_witness = _set_text_replace_payload_attrs(pair_payload, clean, old_t, new_t)
                pair_payload = _sentence_scoped_text_replace_payload_for_target(
                    pair_payload,
                    clean,
                    pair_target,
                    target_count=len(pair_targets),
                )
                pair_payload = _attach_subsection_text_scope_meta(pair_payload, clean, pair_target)
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-{str(pair_target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=pair_target,
                    payload=pair_payload,
                    text_patch=_typed_text_replace_patch(old_t, new_t),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
                if (re.search(r'\bpealkirja(?:s|st)\b', clean, re.IGNORECASE)
                        and pair_target.path and len(pair_target.path) >= 2
                        and pair_target.path[0][0] == "section"
                        and pair_target.path[0][1] not in explicit_heading_sections):
                    sect_path = pair_target.path[:1]
                    ops.append(LegalOperation(
                        op_id=f"ee-text_replace-title-{str(pair_target.path[0][1])}-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=LegalAddress(path=sect_path, special=FacetKind.HEADING),
                        payload=pair_payload,
                        text_patch=_typed_text_replace_patch(old_t, new_t),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                seq += 1
            return ops

    if action == "text_replace":
        old_text, new_text = _normalize_text_replace_args(
            clean,
            *_extract_text_replace_args(clean),
        )
        if new_text is not None or old_text is not None:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=new_text or "")
            payload, _rewrite_witness = _set_text_replace_payload_attrs(payload, clean, old_text, new_text)
            payload = _attach_subsection_text_scope_meta(payload, clean, target)

            explicit_targets = _extract_multiple_explicit_targets(clean)
            heading_targets = _extract_explicit_heading_targets(clean)
            missing_heading_targets = [
                heading_target
                for heading_target in heading_targets
                if heading_target not in explicit_targets
            ]
            if missing_heading_targets:
                combined_targets = explicit_targets + missing_heading_targets
                if all(target.special is FacetKind.HEADING for target in combined_targets):
                    explicit_targets = missing_heading_targets + explicit_targets
                else:
                    explicit_targets.extend(missing_heading_targets)
            if explicit_targets and (len(explicit_targets) > 1 or not target.path):
                for explicit_target in explicit_targets:
                    target_payload = IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=payload.text,
                        attrs=dict(payload.attrs),
                    )
                    target_payload = _sentence_scoped_text_replace_payload_for_target(
                        target_payload,
                        clean,
                        explicit_target,
                        target_count=len(explicit_targets),
                    )
                    target_payload = _attach_subsection_text_scope_meta(target_payload, clean, explicit_target)
                    ops.append(LegalOperation(
                        op_id=f"ee-text_replace-{str(explicit_target)}-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=explicit_target,
                        payload=target_payload,
                        text_patch=_typed_text_replace_patch(old_text, new_text),
                        source=source,
                        provenance_tags=(clean[:200],),
                ))
                seq += 1
                if re.search(r'\bpealkirja(?:s|st)\b', clean, re.IGNORECASE):
                    seen_heading_sections: set[str] = {
                        explicit_target.path[0][1]
                        for explicit_target in explicit_targets
                        if explicit_target.special is FacetKind.HEADING
                        and explicit_target.path
                        and len(explicit_target.path) == 1
                        and explicit_target.path[0][0] == "section"
                    }
                    for explicit_target in explicit_targets:
                        if (
                            explicit_target.path
                            and len(explicit_target.path) >= 2
                            and explicit_target.path[0][0] == "section"
                        ):
                            sect_label = explicit_target.path[0][1]
                            if sect_label in seen_heading_sections:
                                continue
                            preamble_lower = _normalize_ee_parse_text(_instruction_preamble(clean)).lower()
                            section_spans = _target_section_instruction_spans(preamble_lower, sect_label)
                            if not any(re.search(r'\bpealkir', span, re.IGNORECASE) for span in section_spans):
                                continue
                            heading_payload = IRNode(
                                kind=IRNodeKind.CONTENT,
                                text=payload.text,
                                attrs=dict(payload.attrs),
                            )
                            ops.append(LegalOperation(
                                op_id=f"ee-text_replace-title-{sect_label}-{seq}-{source.statute_id}",
                                sequence=seq,
                                action=_to_structural_action("text_replace"),
                                target=LegalAddress(path=(("section", sect_label),), special=FacetKind.HEADING),
                                payload=heading_payload,
                                text_patch=_typed_text_replace_patch(old_text, new_text),
                                source=source,
                                provenance_tags=(clean[:200],),
                            ))
                            seq += 1
                            seen_heading_sections.add(sect_label)
                return ops
    elif action in ("replace", "insert"):
        content = _extract_quoted_content(clean)
        if content:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=content)
            if action == "replace":
                payload = _set_sentence_replace_payload_attrs(payload, clean)
            elif action == "insert":
                payload = _set_sentence_insert_payload_attrs(payload, clean)

    # Handle lõige-range insert: "täiendatakse lõigetega 4 ja 5 järgmises sõnastuses:"
    # Also handles dash ranges: "täiendatakse lõigetega 3–5" or
    # superscript ranges: "täiendatakse lõigetega 5 2–5 9".
    _NUM_PAT = r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*'
    if action == "insert" and 'lõigetega' in clean.lower():
        m_range = re.search(
            r'lõigetega?\s+(' + _NUM_PAT + r'(?:\s*(?:,|ja|ning|–|‒|-)\s*' + _NUM_PAT + r')*)',
            clean, re.IGNORECASE
        )
        if m_range:
            content = _extract_quoted_content(clean)
            raw_group = m_range.group(1).strip()
            expanded = _expand_ee_numeric_list(raw_group)

            sect_label = target.path[0][1] if target.path else "?"
            for num in expanded:
                sub_addr = LegalAddress(path=(("section", sect_label), ("subsection", num)))
                sub_payload = IRNode(kind=IRNodeKind.CONTENT, text=content or "") if content else None
                if sub_payload is not None:
                    sub_payload = _set_sentence_insert_payload_attrs(sub_payload, clean)
                ops.append(LegalOperation(
                    op_id=f"ee-insert-sub-{sect_label}-{num}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("insert"),
                    target=sub_addr,
                    payload=sub_payload,
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
            return ops

    # Multi-target text_replace: "§ N lõikes M, § P lõikes Q, ..., §-s R läbivalt ja § S lõikes T
    # asendatakse sõna X sõnaga Y" — multiple explicit targets sharing the same word replacement.
    # Generate a single global text_replace (empty path) so all occurrences are updated.
    if (action == "text_replace" and payload is not None
            and payload.attrs.get("old_text")
            and not target.path
            and len(re.findall(r'§', clean)) >= 3):
        ops.append(LegalOperation(
            op_id=f"ee-global-text_replace-multi-{seq}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("text_replace"),
            target=LegalAddress(path=()),
            payload=payload,
            text_patch=_typed_text_replace_patch(
                str(payload.attrs.get("old_text") or ""),
                payload.text,
            ),
            source=source,
            provenance_tags=(clean[:200],),
        ))
        seq += 1
        return ops

    # Standard single-provision op
    standard_text_patch = None
    if action == "text_replace" and payload is not None:
        standard_text_patch = _typed_text_replace_patch(
            str(payload.attrs.get("old_text") or ""),
            payload.text,
        )
    ops.append(LegalOperation(
        op_id=f"ee-{action}-{str(target)}-{source.statute_id}",
        sequence=seq,
        action=_to_structural_action(action),
        target=target,
        payload=payload,
        text_patch=standard_text_patch,
        source=source,
        provenance_tags=(clean[:200],),
    ))
    seq += 1

    # "paragrahvi N pealkirjast/pealkirjas ... lõikest/lõike M ..." —
    # the replacement must also apply to the section title.
    # "pealkirjast" = elative (from the title, in deletion ops)
    # "pealkirjas"  = inessive (in the title, in replacement ops)
    # Both inflect as pealkiri → pealkirja + s/st (the 'j' is part of the stem).
    # parse_target only returns the subsection path; emit a second op targeting
    # just the section so _ee_apply_op updates node.text (= title).
    if (action == "text_replace"
            and payload is not None
            and payload.attrs.get("old_text")
            and re.search(r'\bpealkirja(?:s|st)\b', clean, re.IGNORECASE)
            and target.path and len(target.path) >= 2
            and target.path[0][0] == "section"):
        sect_path = target.path[:1]  # just the section, no subsection
        ops.append(LegalOperation(
            op_id=f"ee-text_replace-title-{str(target.path[0][1])}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("text_replace"),
            target=LegalAddress(path=sect_path, special=FacetKind.HEADING),
            payload=payload,
            text_patch=_typed_text_replace_patch(
                str(payload.attrs.get("old_text") or ""),
                payload.text,
            ),
            source=source,
            provenance_tags=(clean[:200],),
        ))
        seq += 1

    # Also check for "ning §-d N ja M tunnistatakse kehtetuks" in the same clause
    # e.g. "paragrahvi 7 lõige 3 ning §-d 7 1 ja 33 tunnistatakse kehtetuks"
    if action == "repeal":
        if (
            target.path
            and len(target.path) >= 3
            and target.path[0][0] == "section"
            and target.path[-1][0] == "item"
        ):
            sect_label = target.path[0][1]
            for extra_sect, extra_sub in _extract_same_section_extra_subsection_repeals_after_items(
                clean,
                sect_label,
            ):
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=LegalAddress(path=(("section", extra_sect), ("subsection", extra_sub))),
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
        _extra = _extract_sd_section_nums(clean)
        for _num in _extra:
            ops.append(LegalOperation(
                op_id=f"ee-repeal-sect-{_num}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=(("section", _num),)),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1
        seen_sub_paths = {
            op.target.path
            for op in ops
            if op.target.path
            and len(op.target.path) >= 2
            and op.target.path[0][0] == "section"
            and op.target.path[1][0] == "subsection"
        }
        companion_subsection_repeals = _extract_trailing_section_item_companion_subsection_repeals(clean)
        companion_labels_by_section: dict[str, list[str]] = {}
        for extra_sect, extra_sub in companion_subsection_repeals:
            labels = companion_labels_by_section.setdefault(extra_sect, [])
            if extra_sub not in labels:
                labels.append(extra_sub)
        for extra_sect, extra_sub in _extract_trailing_section_subsection_repeals(clean):
            sub_path = (("section", extra_sect), ("subsection", extra_sub))
            if sub_path in seen_sub_paths:
                continue
            ops.append(LegalOperation(
                op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=sub_path),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seen_sub_paths.add(sub_path)
            seq += 1
        if companion_subsection_repeals:
            from lawvm.estonia.ee_instruction_waist import make_subsection_selection_meta

        for extra_sect, extra_sub in companion_subsection_repeals:
            sub_path = (("section", extra_sect), ("subsection", extra_sub))
            if sub_path in seen_sub_paths:
                continue
            ops.append(LegalOperation(
                op_id=f"ee-repeal-sub-{extra_sect}-{extra_sub}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=sub_path),
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={
                        "subsection_selection_meta": make_subsection_selection_meta(
                            explicit_labels=companion_labels_by_section.get(extra_sect, ())
                        )
                    },
                ),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seen_sub_paths.add(sub_path)
            seq += 1
        seen_item_paths = {
            op.target.path
            for op in ops
            if op.target.path
            and len(op.target.path) >= 3
            and op.target.path[0][0] == "section"
            and op.target.path[1][0] == "subsection"
            and op.target.path[2][0] == "item"
        }
        for extra_sect, extra_sub, extra_item in _extract_trailing_section_item_repeals(clean):
            item_path = (("section", extra_sect), ("subsection", extra_sub), ("item", extra_item))
            if item_path in seen_item_paths:
                continue
            ops.append(LegalOperation(
                op_id=f"ee-repeal-item-{extra_sect}-{extra_sub}-{extra_item}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("repeal"),
                target=LegalAddress(path=item_path),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seen_item_paths.add(item_path)
            seq += 1
    return ops


def parse_html_op_items(html_cdata: str) -> List[str]:
    """Split an HTMLKonteiner CDATA block into individual numbered op texts.

    Each item starts with <b>N)</b> or <p><b>N)</b>.
    Returns a list of stripped plain-text op strings (HTML tags removed).
    """
    # Split on numbered item boundaries.
    # Allow optional HTML entities (e.g. &#8239; narrow no-break space) inside
    # <b>N)...</b> — some RT HTML uses <b>1)&#8239;</b> where the entity is
    # inside the tag before </b>.
    item_tag = r"(?:b|strong)"
    blocks = re.split(
        r"(?="
        r"<[pb]\b[^>]*>\s*<" + item_tag + r"\b[^>]*>\s*\(?\d+\)\s*[^<]*</" + item_tag + r">"
        r"|<" + item_tag + r"\b[^>]*>\s*\(?\d+\)\s*[^<]*</" + item_tag + r">"
        r")",
        html_cdata,
        flags=re.IGNORECASE,
    )

    result = []
    import html as _html
    for block in blocks:
        # Before stripping, mark bold section-title boundaries with \x01.
        # Pattern: <b>§ N. Title</b> → "§ N. Title\x01" so that
        # _parse_section_payload (grafter) can split title from body text
        # when no explicit (N) subsection markers are present.
        # Only targets bold containing § (section marker), not item markers.
        _SECT_TITLE_BOUNDARY = '\x01'
        # Match <b>...</b> blocks containing § (section marker), including when
        # nested tags like <sup> appear inside <b> (e.g. <b>§ 12<sup>1</sup>. Title</b>).
        # Strategy: strip inner tags from the b-content first, then check for §.
        def _b_sentinel(m: re.Match) -> str:
            inner = m.group(1)
            # Replace inner tags with a space so adjacent text/numbers are not
            # concatenated: "<b>§ 11<sup>1</sup>. Title</b>" → "§ 11 1 . Title"
            # (then _normalize_num converts "11 1" → "11_1").
            inner_plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', inner)).strip()
            inner_plain = _html.unescape(inner_plain)
            if '§' in inner_plain:
                return inner_plain + _SECT_TITLE_BOUNDARY
            return inner  # not a section title — keep original (tags will be stripped later)
        block = re.sub(
            r'<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)>',
            _b_sentinel,
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Strip HTML tags
        text = re.sub(r'<[^>]+>', ' ', block)
        # Decode HTML entities (old-format maarus CDATA uses &auml; etc.)
        text = _html.unescape(text)
        text = text.replace('\xa0', ' ')
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\(\s+', '(', text)
        text = re.sub(r'\s+\)', ')', text)
        if text and re.match(r'\(?\d+\)', text):
            result.append(text)

    return result
