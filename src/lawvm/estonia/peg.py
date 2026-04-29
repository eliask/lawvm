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


_EE_OPTIONAL_TARGET_LABEL_SPACE_RULE = "ee_optional_target_label_space"
_EE_SECTION_INTRO_REPLACE_TO_FIRST_SUBSECTION_RULE = "ee_section_intro_replace_to_first_subsection"
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
_EE_DASH_CHARS += "\u2212"
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


_EE_ORDINAL_WORD_NUMS = {
    "esimese": "1",
    "teise": "2",
    "kolmanda": "3",
    "neljanda": "4",
    "viienda": "5",
    "kuuenda": "6",
    "seitsmenda": "7",
    "kaheksanda": "8",
    "üheksanda": "9",
    "kümnenda": "10",
}
_EE_ORDINAL_WORD_PAT = "|".join(re.escape(word) for word in _EE_ORDINAL_WORD_NUMS)


def _ee_ordinal_word_num(raw: str) -> str | None:
    return _EE_ORDINAL_WORD_NUMS.get(raw.casefold())


def _instruction_preamble(text: str) -> str:
    """Return the instruction part before quoted replacement payload begins."""
    text = _normalize_ee_parse_text(text)
    verb_match = re.search(
        r'\b(?:asendatakse|täiendatakse|tunnistatakse|sõnastatakse|muudetakse|'
        r'jäetakse|lisatakse|kehtestatakse|loetakse)\b',
        text,
        re.IGNORECASE,
    )
    operative_start = verb_match.start() if verb_match is not None else 0
    preamble_end = len(text)
    for marker in (
        '\u201e',
        '\u201c',
        '\u201d',
        '\u02ee',
        '\u00ab',
        'järgmises sõnastuses:',
        'järgnevas sõnastuses:',
        'järgmiselt:',
    ):
        idx = text.find(marker, operative_start)
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

    # Part + chapter + division title:
    # "N. osa M. peatüki K. jao pealkiri"
    m_part_ch_div = re.search(
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*osa\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatük[k]?i\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*jao\s+pealkir(?:i|ja(?:s|st)?)',
        text,
        re.IGNORECASE,
    )
    if m_part_ch_div:
        part_num = _normalize_num(m_part_ch_div.group(1))
        ch_num = _normalize_num(m_part_ch_div.group(2))
        div_num = _normalize_num(m_part_ch_div.group(3))
        return LegalAddress(
            path=(("part", part_num), ("chapter", ch_num), ("division", div_num)),
            special=FacetKind.HEADING,
        )

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

    m_part = re.search(
        r'(?:seaduse|seadustiku|määruse|akti)?\s*'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*osa\b',
        text,
        re.IGNORECASE,
    )
    if m_part:
        return LegalAddress(path=(("part", _normalize_num(m_part.group(1))),))

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
    m_ch_word = re.search(
        rf'\b({_EE_ORDINAL_WORD_PAT})\s+peatük[k]?i\s+pealkir(?:i|ja(?:s|st)?)',
        text,
        re.IGNORECASE,
    )
    if m_ch_word:
        ch_num = _ee_ordinal_word_num(m_ch_word.group(1))
        if ch_num is not None:
            return LegalAddress(path=(("chapter", ch_num),), special=FacetKind.HEADING)

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
        m_sect = re.search(r'§(?:-s|-st|-i|-ga)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)', preamble)
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
        m_sect = re.search(r'§(?:-s|-st|-i|-ga)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)', section_context)
    if not m_sect:
        return None
    path.append(("section", _normalize_num(m_sect.group(1))))

    # Subsection/item qualifiers belong only to the local span of the matched
    # section reference. Mixed clauses like "§ 87^2, § 100^3 lõige 3 ..."
    # must not leak the later subsection onto the leading plain section target.
    section_tail = section_context[m_sect.end():]
    next_section = re.search(
        r'(?:\bparagrahvi(?:s|st)?\s+\d|\bparagrahv\s+\d|§(?:-s|-st)?\s*\d)',
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
        r'\b(?:lõikest|lõike[s]?|lõiget|lõige|lõikega)\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
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
            or 'sõnastatakse' in t_lower
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
        r'\u201c.*?\u201d',
        r'\u201e.*?\u201d',
        r'\u201e.*?\u201c',
        r'\u02ee.*?\u02ee',
        r'\u00ab.*?\u00bb',
        r'".*?"',
    ):
        preamble = re.sub(pat, ' ', preamble, flags=re.DOTALL)
    preamble = _strip_embedded_reference_wrapper(preamble)
    preamble = re.sub(r"§\s*[–‒‑-]\s*s\b", "§-s", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r"§\s*[–‒‑-]\s*i\b", "§-i", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r"§\s*[–‒‑-]\s*d\b", "§-d", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r"§\s*[–‒‑-]\s*des\b", "§-des", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r"\bl[oõ]igetest\b", "lõigetes", preamble, flags=re.IGNORECASE)
    preamble = re.sub(r'\s+', ' ', preamble).strip()
    chunks = re.split(
        r'(?:,\s*|\s+(?:ning|ja)\s+)'
        r'(?=(?:§(?:-s|-i|-des)?\s*\d|\bparagrahvi(?:s|st)?\s+\d|\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+lõike))',
        preamble,
        flags=re.IGNORECASE,
    )
    targets: List[LegalAddress] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for chunk in chunks:
        if re.match(
            r'^\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+lõike(?:te|tes|st|s|t|ga)?\b',
            chunk,
            re.IGNORECASE,
        ):
            chunk = f"paragrahvi {chunk.strip()}"
        m_plural_sections = re.search(
            r'^(?:\bparagrahve\s+|§-(?:d|des)\s*)'
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
            r'(?:\bparagrahvi(?:s|st)?\s+|§(?:-i)?\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+',
            chunk,
            re.IGNORECASE,
        )
        if m_same_section_mixed:
            sect_label = _normalize_num(m_same_section_mixed.group(1))
            remainder = chunk[m_same_section_mixed.end():]
            mixed_targets: List[LegalAddress] = []
            mixed_seen: set[tuple[tuple[str, str], ...]] = set()
            subsection_intro_item_spans: list[tuple[int, int]] = []

            for intro_item_ref in re.finditer(
                r'lõike(?:te|tes|st|s|t|ga)?\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
                r'sissejuhatava(?:t\s+lauseosa|s\s+lauseosas|st\s+lauseosast)(?:\s*,\s*|\s+(?:ning|ja)\s+)'
                r'punkt(?:id|e|ide|ides|i|is|ist|iga)?\s+'
                r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                remainder,
                re.IGNORECASE,
            ):
                subsection_intro_item_spans.append(intro_item_ref.span())
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

            for section_intro_item_ref in re.finditer(
                r'sissejuhatava(?:t\s+lauseosa|s\s+lauseosas|st\s+lauseosast)(?:\s*,\s*|\s+(?:ning|ja)\s+)'
                r'punkt(?:id|e|ide|ides|i|is|ist|iga)?\s+'
                r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*)',
                remainder,
                re.IGNORECASE,
            ):
                if any(
                    start <= section_intro_item_ref.start() < end
                    for start, end in subsection_intro_item_spans
                ):
                    continue
                section_path = (("section", sect_label),)
                if section_path not in mixed_seen:
                    mixed_seen.add(section_path)
                    mixed_targets.append(LegalAddress(path=section_path))
                for item_label in _expand_ee_numeric_list(section_intro_item_ref.group(1).strip()):
                    item_path = (
                        ("section", sect_label),
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

            plain_item_remainder = remainder
            for start, end in reversed(subsection_intro_item_spans):
                plain_item_remainder = plain_item_remainder[:start] + " " + plain_item_remainder[end:]
            plain_item_remainder = re.sub(
                r'lõike(?:te|tes|st|s|t|ga)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+'
                r'punkt(?:id|ide|ides|idest|i|is|ist|iga)?\s+'
                r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*(?:\s*(?:,|ja|–|‒|-)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)*',
                ' ',
                plain_item_remainder,
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
            r'(?:\bparagrahvi(?:s|st)?\s+|§(?:-i)?\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
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
            r'(?:\bparagrahvi(?:s|st)?\s+|§(?:-i)?\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
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
            and target.path not in intro_only_subsections
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
    if _heading_mention_precedes_child_target(text):
        deduped.sort(key=lambda target: 0 if target.special is FacetKind.HEADING else 1)
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
            r'sissejuhatav(?:\s+lauseosa|at\s+lauseosa|as\s+lauseosas|ast\s+lauseosast)',
            section_span,
            re.IGNORECASE,
        ):
            intro_only_paths.add(
                (
                    ("section", sect_label),
                    ("subsection", _normalize_num(sub_ref.group(1))),
                )
            )
        if re.search(
            r'\b(?:teksti\s+)?sissejuhatav(?:\s+lauseosa|at\s+lauseosa|as\s+lauseosas|ast\s+lauseosast)\b',
            section_span,
            re.IGNORECASE,
        ):
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
    if len(target.path) == 1 and target.path[0][0] == "section":
        attrs.setdefault("source_family", _EE_SECTION_INTRO_REPLACE_TO_FIRST_SUBSECTION_RULE)
    return replace(payload, attrs=attrs)


def _is_mixed_subsection_and_item_replace_scope(clean: str, target: LegalAddress) -> bool:
    """Return True for clauses that explicitly replace a subsection and one of its items."""
    if len(target.path) < 3:
        return False
    if target.path[0][0] != "section" or target.path[1][0] != "subsection" or target.path[2][0] != "item":
        return False
    section_label = re.escape(target.path[0][1]).replace("_", r"\s*")
    subsection_label = re.escape(target.path[1][1]).replace("_", r"\s*")
    item_label = re.escape(target.path[2][1]).replace("_", r"\s*")
    preamble = _instruction_preamble(clean)
    return re.search(
        rf'(?:\bparagrahvi(?:s|st)?\s+|§\s*){section_label}\s+'
        rf'lõige\s+{subsection_label}\s+ja\s+'
        rf'lõike\s+{subsection_label}\s+punkt(?:i|is|ist)?\s+{item_label}\b',
        preamble,
        re.IGNORECASE,
    ) is not None


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

    for match in re.finditer(
        rf'\b({_EE_ORDINAL_WORD_PAT})\s+peatük[k]?i',
        heading_scope,
        re.IGNORECASE,
    ):
        ch_num = _ee_ordinal_word_num(match.group(1))
        if ch_num is None:
            continue
        path = (("chapter", ch_num),)
        if path in seen:
            continue
        seen.add(path)
        targets.append(LegalAddress(path=path, special=FacetKind.HEADING))

    return sorted(targets, key=lambda target: (len(target.path), target.path))


def _heading_mention_precedes_child_target(text: str) -> bool:
    preamble = _strip_embedded_reference_wrapper(_instruction_preamble(text))
    heading_match = re.search(r'\bpealkir(?:i|ja(?:s|st)?)\b', preamble, re.IGNORECASE)
    if heading_match is None:
        return False
    child_match = re.search(
        r'\bl[oõ]ike(?:d|te|tes|s|st|t|ga|id)?\b|\bpunkt(?:id|ide|ides|i|is|ist|iga)?\b',
        preamble,
        re.IGNORECASE,
    )
    return child_match is None or heading_match.start() < child_match.start()


# ---------------------------------------------------------------------------
# Verb / action extraction
# ---------------------------------------------------------------------------

_EE_TEXTUAL_INVALIDATION_RULE = "ee_textual_invalidation_as_text_delete"
_EE_SECTION_SEQUENCE_RENUMBER_RULE = "ee_section_sequence_renumber_before_insert"
_EE_SUBSECTION_SEQUENCE_RENUMBER_RULE = "ee_subsection_sequence_renumber_before_insert"
_EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE = "ee_item_renumber_before_replace"
_EE_FLAT_SECTIONLESS_SINGLETON_ITEM_INSERT_RULE = "ee_flat_sectionless_singleton_item_insert"
_EE_FLAT_SECTIONLESS_SINGLETON_ITEM_REPEAL_RULE = "ee_flat_sectionless_singleton_item_repeal"
_EE_FLAT_SECTIONLESS_SINGLETON_SUBSECTION_SCOPE_RULE = "ee_flat_sectionless_singleton_subsection_scope"
_EE_PAYLOAD_AFTER_TITLE_QUOTE_RULE = "ee_payload_after_marker_ignores_premarker_title_quote"
_EE_ASCII_QUOTED_MARKER_PAYLOAD_RULE = "ee_ascii_quoted_marker_payload"
_EE_PLURAL_ITEM_PAYLOAD_OUTER_QUOTE_TAIL_RULE = "ee_plural_item_payload_outer_quote_tail_stripped"
_EE_PLURAL_ITEM_MARKER_PAYLOAD_INNER_QUOTE_RULE = "ee_plural_item_marker_payload_recovers_inner_quote"
_EE_PLURAL_ITEM_REPLACE_MISSING_LABEL_REPEAL_RULE = "ee_plural_item_replace_missing_label_repeal"
_EE_PLURAL_ITEM_REPLACE_RANGE_OMITS_INSERTED_LABELS_RULE = "ee_plural_item_replace_range_omits_inserted_labels"
_EE_PLURAL_SUBSECTION_INSERT_PAYLOAD_SPLIT_RULE = "ee_plural_subsection_insert_payload_split"
_EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE = (
    "ee_plural_subsection_replace_extra_payload_label"
)
_EE_MULTI_TARGET_TEXT_DELETE_SPLIT_RULE = "ee_multi_target_text_delete_split"
_EE_MIXED_DELETE_REPLACE_SAME_TARGET_RULE = "ee_mixed_delete_and_replace_same_target"
_EE_MIXED_REPLACE_INSERT_AFTER_SAME_TARGET_RULE = "ee_mixed_replace_and_insert_after_same_target"
_EE_MIXED_TEXT_REPLACE_SENTENCE_REPLACE_SAME_TARGET_RULE = (
    "ee_mixed_text_replace_and_sentence_replace_same_target"
)
_EE_MIXED_SENTENCE_REPLACE_INSERT_SAME_TARGET_RULE = (
    "ee_mixed_sentence_replace_and_insert_same_target"
)
_EE_PLURAL_SECTION_INSERT_PAYLOAD_SPLIT_RULE = "ee_plural_section_insert_payload_split"
_EE_EXPLICIT_MIXED_STRUCTURAL_REPEAL_LIST_RULE = "ee_explicit_mixed_structural_repeal_list"
_EE_SUBSECTION_TABLE_ONLY_REPLACE_RULE = "ee_subsection_table_only_replace_preserve_intro"
_EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE = "ee_senine_text_subsection_renumber_before_insert"
_EE_FRAKTSIONEERITUD_TYPO_DELETE_RULE = "ee_fraktsioneeritud_source_typo_delete_variant"
_EE_LOCAL_KOHTKUTE_SOURCE_SURFACE_DELETE_RULE = "ee_lokaal_kohtkute_source_surface_delete_variant"
_EE_OLEMASOLEV_TAHKEL_KUTUSEL_PHRASE_FORMS_RULE = "ee_case_inflected_olemasolev_tahkel_kutusel_phrase_forms"
_EE_VOLITATUD_VASTUTAV_FORMS_RULE = "ee_case_inflected_volitatud_vastutav_forms"
_EE_TAOTLUSVOOR_COORDINATION_FORMS_RULE = "ee_case_inflected_taotlusvoor_coordination_forms"
_EE_MIXED_ACRONYM_SUFFIX_CASE_REWRITE_RULE = "ee_case_inflected_mixed_acronym_suffix_case"
_EE_NETO_OMAVAHEND_PREFIX_FORMS_RULE = "ee_case_inflected_neto_omavahend_prefix_forms"
_EE_KYSK_RTK_FORMS_RULE = "ee_case_inflected_kysk_riigi_tugiteenuste_keskus_forms"
_EE_INSERT_MULTI_EXPLICIT_TARGETS_PAYLOAD_LABEL_FILTER_RULE = (
    "ee_insert_multi_explicit_targets_payload_label_filter"
)


def _is_textual_invalidation(text: str) -> bool:
    """Return true for clauses invalidating quoted words, not legal units."""
    preamble = _instruction_preamble(text).lower()
    return (
        "tunnistatakse kehtetuks" in preamble
        and re.search(r'\b(?:sõna[a-z]*|sõnad|tekstiosa[a-z]*|lauseosa[a-z]*)\b', preamble)
        is not None
    )


def _is_lokaal_kohtkute_source_surface_delete_variant(old_text: str | None, new_text: str | None) -> bool:
    if new_text:
        return False
    return (old_text or "") == (
        "enne 2010. aasta 1. jaanuari. Elamu või selle osa soojusvarustuse liigina "
        "peab ehitisregistrisse olema märgitud lokaal- või kohtküte ja energiaallika "
        "liigina tahkekütus"
    )


def _case_inflected_phrase_source_family(old_text: str | None, new_text: str | None) -> str:
    if old_text == "olemasolev tahkel kütusel põhinev kütteseade" and new_text == "olemasolev kütteseade":
        return _EE_OLEMASOLEV_TAHKEL_KUTUSEL_PHRASE_FORMS_RULE
    if old_text == "volitatud" and new_text == "vastutav":
        return _EE_VOLITATUD_VASTUTAV_FORMS_RULE
    if (
        old_text == "teine ja viies taotlusvoor"
        and new_text == "teine, viies ja järgnevad taotlusvoorud"
    ):
        return _EE_TAOTLUSVOOR_COORDINATION_FORMS_RULE
    if old_text == "neto-omavahend" and new_text == "omavahend":
        return _EE_NETO_OMAVAHEND_PREFIX_FORMS_RULE
    if old_text == "KÜSK" and new_text == "Riigi Tugiteenuste Keskus":
        return _EE_KYSK_RTK_FORMS_RULE
    if new_text and re.fullmatch(r"[A-ZÕÄÖÜŠŽ]{2,}-[a-zäöõüšž]+", new_text.strip()):
        return _EE_MIXED_ACRONYM_SUFFIX_CASE_REWRITE_RULE
    return ""


def _split_section_renumber_labels(surface: str) -> tuple[str, ...]:
    """Split an Estonian section-label list and normalize superscript labels."""
    labels: list[str] = []
    for part in re.split(r"\s*(?:,|\bja\b)\s*", surface):
        raw = part.strip()
        if not raw:
            continue
        if re.fullmatch(r"\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*", raw):
            labels.append(_normalize_num(raw))
    return tuple(labels)


def _filter_insert_targets_by_payload_label(
    targets: list[LegalAddress],
    payload_text: str,
) -> tuple[list[LegalAddress], str | None]:
    """Keep the inserted unit when the clause also names its host as context."""
    subsection_match = re.match(r"^\s*\(\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*\)", payload_text)
    if subsection_match is not None:
        label = _normalize_num(subsection_match.group(1))
        matching = [
            target
            for target in targets
            if target.path
            and target.path[-1][0] == "subsection"
            and target.path[-1][1] == label
        ]
        if len(matching) == 1:
            return matching, _EE_INSERT_MULTI_EXPLICIT_TARGETS_PAYLOAD_LABEL_FILTER_RULE

    item_match = re.match(r"^\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*\)", payload_text)
    if item_match is not None:
        label = _normalize_num(item_match.group(1))
        matching = [
            target
            for target in targets
            if target.path
            and target.path[-1][0] == "item"
            and target.path[-1][1] == label
        ]
        if len(matching) == 1:
            return matching, _EE_INSERT_MULTI_EXPLICIT_TARGETS_PAYLOAD_LABEL_FILTER_RULE

    return targets, None


def _extract_section_renumber_pairs(text: str) -> tuple[tuple[str, str], ...]:
    """Extract source-backed section relabel pairs from ``loetakse`` clauses."""
    plural = re.search(
        r"\bparagrahvid\s+(?P<old>.+?)\s+loetakse\s+"
        r"(?:§-deks|paragrahvideks)\s+(?P<new>.+?)"
        r"(?=\s+(?:ning|ja)\s+(?:määr|sead|koodeks)|[.;:])",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if plural is not None:
        old_labels = _split_section_renumber_labels(plural.group("old"))
        new_labels = _split_section_renumber_labels(plural.group("new"))
        if len(old_labels) == len(new_labels) and old_labels:
            return tuple(zip(old_labels, new_labels))

    singular = re.search(
        r'\bparagrahv\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+loetakse\s+'
        r'(?:§-ks|paragrahviks)\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)',
        text,
        re.IGNORECASE,
    )
    if singular is None:
        return ()
    return ((_normalize_num(singular.group(1)), _normalize_num(singular.group(2))),)


def _section_renumber_ops(
    clean: str,
    source: OperationSource,
    *,
    seq_start: int,
) -> tuple[LegalOperation, ...]:
    """Build renumber ops, moving occupied higher destinations first."""
    pairs = _extract_section_renumber_pairs(clean)
    ops: list[LegalOperation] = []
    for offset, (old_label, new_label) in enumerate(reversed(pairs)):
        payload = IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "rule_id": _EE_SECTION_SEQUENCE_RENUMBER_RULE,
                "source_old_label": old_label,
                "source_new_label": new_label,
            },
        )
        ops.append(LegalOperation(
            op_id=f"ee-renumber-section-{old_label}-{new_label}-{source.statute_id}",
            sequence=seq_start + offset,
            action=_to_structural_action("renumber"),
            target=LegalAddress(path=(("section", old_label),)),
            destination=LegalAddress(path=(("section", new_label),)),
            payload=payload,
            source=source,
            provenance_tags=(clean[:200], _EE_SECTION_SEQUENCE_RENUMBER_RULE),
            witness_rule_id=_EE_SECTION_SEQUENCE_RENUMBER_RULE,
        ))
    return tuple(ops)


def _subsection_renumber_then_insert_ops(
    clean: str,
    source: OperationSource,
    *,
    seq_start: int,
) -> tuple[LegalOperation, ...]:
    """Build ops for ``lõige A loetakse lõikeks B`` plus a new subsection insert."""
    match = re.search(
        r'\bparagrahvi\s+(?P<section>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'lõige\s+(?P<old>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+loetakse\s+'
        r'lõikeks\s+(?P<new>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(?:ning|ja)\s+paragrahvi\s+t[aä]iendatakse\s+'
        r'lõikega\s+(?P<insert>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\b',
        clean,
        re.IGNORECASE,
    )
    content = _extract_quoted_content(clean)
    if match is None or not content:
        return ()
    section_label = _normalize_num(match.group("section"))
    old_label = _normalize_num(match.group("old"))
    new_label = _normalize_num(match.group("new"))
    insert_label = _normalize_num(match.group("insert"))
    renumber_payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text="",
        attrs={
            "rule_id": _EE_SUBSECTION_SEQUENCE_RENUMBER_RULE,
            "source_old_label": old_label,
            "source_new_label": new_label,
        },
    )
    insert_payload = _set_sentence_insert_payload_attrs(
        IRNode(
            kind=IRNodeKind.CONTENT,
            text=content,
            attrs={"source_family": _EE_SUBSECTION_SEQUENCE_RENUMBER_RULE},
        ),
        clean,
    )
    return (
        LegalOperation(
            op_id=f"ee-renumber-subsection-{section_label}-{old_label}-{new_label}-{source.statute_id}",
            sequence=seq_start,
            action=_to_structural_action("renumber"),
            target=LegalAddress(path=(("section", section_label), ("subsection", old_label))),
            destination=LegalAddress(path=(("section", section_label), ("subsection", new_label))),
            payload=renumber_payload,
            source=source,
            provenance_tags=(clean[:200], _EE_SUBSECTION_SEQUENCE_RENUMBER_RULE),
            witness_rule_id=_EE_SUBSECTION_SEQUENCE_RENUMBER_RULE,
        ),
        LegalOperation(
            op_id=f"ee-insert-renumbered-subsection-{section_label}-{insert_label}-{source.statute_id}",
            sequence=seq_start + 1,
            action=_to_structural_action("insert"),
            target=LegalAddress(path=(("section", section_label), ("subsection", insert_label))),
            payload=insert_payload,
            source=source,
            provenance_tags=(clean[:200], _EE_SUBSECTION_SEQUENCE_RENUMBER_RULE),
            witness_rule_id=_EE_SUBSECTION_SEQUENCE_RENUMBER_RULE,
        ),
    )


def _senine_text_subsection_renumber_then_insert_ops(
    clean: str,
    source: OperationSource,
    *,
    seq_start: int,
) -> tuple[LegalOperation, ...]:
    """Build ops for ``senine tekst loetakse lõikeks N`` plus a new subsection insert."""
    match = re.search(
        r'\bparagrahvi\s+(?P<section>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'senine\s+tekst\s+loetakse\s+lõikeks\s+(?P<new>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'(?:ning|ja)\s+paragrahvi\s+t[aä]iendatakse\s+'
        r'lõikega\s+(?P<insert>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\b',
        clean,
        re.IGNORECASE,
    )
    content = _extract_quoted_content(clean)
    if match is None or not content:
        return ()
    section_label = _normalize_num(match.group("section"))
    new_label = _normalize_num(match.group("new"))
    insert_label = _normalize_num(match.group("insert"))
    old_label = "1"
    insert_payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text=content,
        attrs={"source_family": _EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE},
    )
    if new_label == old_label:
        return (
            LegalOperation(
                op_id=f"ee-insert-senine-native-subsection-{section_label}-{insert_label}-{source.statute_id}",
                sequence=seq_start,
                action=_to_structural_action("insert"),
                target=LegalAddress(path=(("section", section_label), ("subsection", insert_label))),
                payload=insert_payload,
                source=source,
                provenance_tags=(clean[:200], _EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE),
                witness_rule_id=_EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE,
            ),
        )
    renumber_payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text="",
        attrs={
            "rule_id": _EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE,
            "source_old_label": old_label,
            "source_new_label": new_label,
            "source_scope": "senine_text",
        },
    )
    return (
        LegalOperation(
            op_id=f"ee-renumber-senine-subsection-{section_label}-{old_label}-{new_label}-{source.statute_id}",
            sequence=seq_start,
            action=_to_structural_action("renumber"),
            target=LegalAddress(path=(("section", section_label), ("subsection", old_label))),
            destination=LegalAddress(path=(("section", section_label), ("subsection", new_label))),
            payload=renumber_payload,
            source=source,
            provenance_tags=(clean[:200], _EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE),
            witness_rule_id=_EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE,
        ),
        LegalOperation(
            op_id=f"ee-insert-senine-renumbered-subsection-{section_label}-{insert_label}-{source.statute_id}",
            sequence=seq_start + 1,
            action=_to_structural_action("insert"),
            target=LegalAddress(path=(("section", section_label), ("subsection", insert_label))),
            payload=insert_payload,
            source=source,
            provenance_tags=(clean[:200], _EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE),
            witness_rule_id=_EE_SENINE_TEXT_SUBSECTION_RENUMBER_RULE,
        ),
    )


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
    normalized_text = re.sub(r"\bteksti\s+osaga\b", "tekstiosaga", text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"\bteksti\s+osa\b", "tekstiosa", normalized_text, flags=re.IGNORECASE)
    normalized_preamble = re.sub(r"\bteksti\s+osaga\b", "tekstiosaga", preamble, flags=re.IGNORECASE)
    normalized_preamble = re.sub(
        r"\bteksti\s+osa\b",
        "tekstiosa",
        normalized_preamble,
        flags=re.IGNORECASE,
    )
    t = normalized_preamble.lower()

    # Text-level replacement: asendatakse ... sõna/arv/tekstiosa/lauseosa
    # Check BEFORE repeal — payload text often contains "tunnistatakse kehtetuks" for
    # EU regulation titles (e.g. "millega tunnistatakse kehtetuks määrus (EÜ) nr 854/2004"),
    # which would trigger repeal if checked first.  "asendatakse sõnad" in the instruction
    # preamble unambiguously identifies a text-replace regardless of payload content.
    # Covers: asendatakse sõna/sõnad/sõnu/arv/tekstiosa/lauseosa/number/lühend
    # Also: asendatakse läbivalt sõna (läbivalt = throughout, intervenes before noun)
    # and targeted forms where the provision list sits between the verb and the
    # replaced word, e.g. "seaduses asendatakse § 8 lõike 4 punktis 2 ja lõikes 5
    # ... sõna „X” sõnaga „Y”".
    if re.search(
        r'\b(?:sõn(?:ad|u)|lauseosa[a-z]*|tekstiosa[a-z]*)\b[^.;]{0,240}\basendatakse\b[^.;]{0,120}\b'
        r'(?:sõn(?:a|aga|adega)|tekstiosaga|lauseosaga)\b',
        normalized_text,
        re.IGNORECASE | re.DOTALL,
    ):
        return "text_replace"
    if re.search(
        r'asendatakse\b.{0,240}?\b(?:läbivalt\s+)?'
        r'(?:sõna[a-z]*|arv[a-z]*|aastaarv[a-z]*|tekstiosa[a-z]*|lauseosa[a-z]*|number[a-z]*|viide[a-z]*|lühend[a-z]*)',
        t,
        re.DOTALL,
    ):
        return "text_replace"
    if re.search(
        r'\b(?:sõna[a-z]*|sõnad|sõnu|arv[a-z]*|aastaarv[a-z]*|tekstiosa[a-z]*|lauseosa[a-z]*|number[a-z]*|viide[a-z]*|lühend[a-z]*)'
        r'\b.{0,240}\basendatakse\b',
        t,
        re.DOTALL,
    ):
        return "text_replace"

    # Text-level invalidation: "lõikes 3 tunnistatakse kehtetuks tekstiosa
    # „...”;". The legal unit remains active; only the quoted surface is
    # deleted. This must be classified before structural repeal.
    if _is_textual_invalidation(text):
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
    if re.search(r'\bjäetakse\b', t) and re.search(r'\bvälja', t):
        if re.search(r'\bvälja\s*(?:sõna[a-z]*|lauseosa|tekstiosa|arv[a-z]*|number)', t):
            return "text_replace"
        return "repeal"

    if (
        'täiendatakse' in t
        and 'sõnastatakse' in t
        and re.search(r'\b(?:punktiga|lõikega|paragrahviga|§-ga)\b', t)
    ):
        return "insert"

    # Structural replace. _instruction_preamble() strips "järgmiselt:" before
    # payload parsing, so bare "sõnastatakse" in the preamble is the operative
    # replacement verb.
    if (
        'kehtestatakse' in t
        and 'uues sõnastuses' in t
        and re.search(r'\b(?:paragrahvi|§|lõige|lõikes|lõiget|punkt(?:i|is)?)\b', t)
        and not re.search(r'\blisa(?:s|ga)?\b', t)
    ):
        return "replace"
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
            or re.search(
                r'\b(?:sõn[au]|tekstiosa|lauseosa|arvu)\s+[„"«”][^„”“"«»]{0,240}'
                r'[”"»“]\s+j[aä]rel\b',
                normalized_text,
                re.IGNORECASE,
            )
        ):
            return "text_replace"
        # täiendatakse lausega / lõigetega / §-dega → structural insert
        return "insert"

    # Imperative supplement form used in amendment points:
    # "täiendada määrust paragrahviga 17 1 järgmises sõnastuses".
    if re.search(r'\btäiendada\b', t):
        return "insert"

    # "lisatakse pärast sõna X sõnad Y" mutates text inside an existing
    # provision; it is not a structural insert of a duplicate child node.
    if 'lisatakse' in t:
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
    balanced_left_right = _extract_balanced_quoted_contents(text, '\u201c', '\u201d')
    if balanced_left_right:
        return balanced_left_right
    balanced_estonian_left_close = _extract_balanced_quoted_contents(text, '\u201e', '\u201c')
    if balanced_estonian_left_close:
        return balanced_estonian_left_close
    balanced_estonian = _extract_balanced_quoted_contents(text, '\u201e', '\u201d')
    if balanced_estonian:
        return balanced_estonian
    balanced_estonian_ascii = _extract_balanced_quoted_contents(text, '\u201e', '"')
    if balanced_estonian_ascii:
        return balanced_estonian_ascii
    balanced_estonian_prime = _extract_balanced_quoted_contents(text, '\u201e', '\u02ee')
    if balanced_estonian_prime:
        return balanced_estonian_prime
    balanced_french = _extract_balanced_quoted_contents(text, '\u00ab', '\u00bb')
    if balanced_french:
        return balanced_french
    for pat in (
        r'\u201c(.*?)\u201d',
        r'\u201e(.*?)\u201c',
        r'\u201e(.*?)\u201d',
        r'\u201e(.*?)"',
        r'\u201e(.*?)\u02ee',
        r'\u201d(.*?)\u201d',
        r'\u201c(.*?)\u201c',
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
    matches = list(re.finditer(
        r'(?:järgmises\s+sõnastuses|järgnevas\s+sõnastuses|järgmiselt)\s*:\s*',
        text,
        re.IGNORECASE | re.DOTALL,
    ))
    if not matches:
        return None
    marker = matches[-1]
    payload = text[marker.end():].strip()
    starts_ascii_quote = payload.startswith('"')
    payload = re.sub(r'^[\u201c\u201e\u201d"\u00ab\u00bb\u02ee]\s*', '', payload)
    payload = re.sub(r'\s*[.;]\s*$', '', payload)
    if starts_ascii_quote:
        payload = re.sub(r'\s*"\s*$', '', payload)
    if not re.search(r'[\u201e\u00ab"]', payload) or payload.startswith("\u201e"):
        payload = re.sub(r'\s*[\u201c\u201d\u00bb"\u02ee]\s*$', '', payload)
    return payload.strip() or None


def _extract_flat_sectionless_singleton_item_insert(
    clean: str,
    source: OperationSource,
    seq: int,
) -> LegalOperation | None:
    """Recover old-format singleton-regulation item inserts with no section phrase.

    This handles clauses like ``määrust täiendatakse punktiga 12 ... "12) X"``
    where the source explicitly owns the item label but omits the only section
    path. The singleton section/subsection frame is represented explicitly and
    visibly so replay does not treat the op as whole-act META.
    """
    match = re.search(
        rf"\btäiendatakse\s+punktiga\s+(?P<label>{_EE_NUM_ATOM})\b",
        clean,
        re.IGNORECASE,
    )
    if match is None:
        return None
    label = _normalize_num(match.group("label"))
    payload_text = _extract_payload_after_marker(clean)
    if not payload_text:
        return None
    payload_match = re.match(
        rf"^\(?\s*(?P<label>{re.escape(match.group('label'))}|{re.escape(label)})\s*\)\s*(?P<body>.+)$",
        payload_text,
        re.DOTALL,
    )
    if payload_match is None:
        return None
    body = payload_match.group("body").strip()
    if not body:
        return None
    payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text=body,
        attrs={
            "source_family": _EE_FLAT_SECTIONLESS_SINGLETON_ITEM_INSERT_RULE,
            "scope_confidence": "inferred_from_live_unique",
            "source_item_label": label,
            "inferred_singleton_path": "section:1/subsection:1",
        },
    )
    return LegalOperation(
        op_id=f"ee-flat-sectionless-item-insert-{label}-{seq}-{source.statute_id}",
        sequence=seq,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "1"), ("subsection", "1"), ("item", label))),
        payload=payload,
        source=source,
        provenance_tags=(
            clean[:200],
            _EE_FLAT_SECTIONLESS_SINGLETON_ITEM_INSERT_RULE,
            "scope_confidence:inferred_from_live_unique",
        ),
        witness_rule_id=_EE_FLAT_SECTIONLESS_SINGLETON_ITEM_INSERT_RULE,
    )


def _extract_flat_sectionless_singleton_item_repeals(
    clean: str,
    source: OperationSource,
    seq: int,
) -> list[LegalOperation]:
    """Recover top-level ``määruse punktid`` repeals in singleton list regulations."""
    item_list_pat = (
        _EE_NUM_ATOM
        + r'(?:\s*[–‒\-]\s*'
        + _EE_NUM_ATOM
        + r')?(?:\s*,\s*'
        + _EE_NUM_ATOM
        + r'(?:\s*[–‒\-]\s*'
        + _EE_NUM_ATOM
        + r')?)*(?:\s+ja\s+'
        + _EE_NUM_ATOM
        + r'(?:\s*[–‒\-]\s*'
        + _EE_NUM_ATOM
        + r')?)*'
    )
    match = re.search(
        r"\b(?:määruse|seaduse)\s+punkt(?:id|e)\s+(" + item_list_pat + r")\s+tunnistatakse\s+kehtetuks\b",
        clean,
        re.IGNORECASE,
    )
    if match is None:
        return []
    ops: list[LegalOperation] = []
    for label in _expand_ee_numeric_list(match.group(1).strip()):
        ops.append(LegalOperation(
            op_id=f"ee-flat-sectionless-item-repeal-{label}-{seq}-{source.statute_id}",
            sequence=seq,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "1"), ("subsection", "1"), ("item", label))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "source_family": _EE_FLAT_SECTIONLESS_SINGLETON_ITEM_REPEAL_RULE,
                    "scope_confidence": "inferred_from_live_unique",
                    "source_item_label": label,
                    "inferred_singleton_path": "section:1/subsection:1",
                },
            ),
            source=source,
            provenance_tags=(
                clean[:200],
                _EE_FLAT_SECTIONLESS_SINGLETON_ITEM_REPEAL_RULE,
                "scope_confidence:inferred_from_live_unique",
            ),
            witness_rule_id=_EE_FLAT_SECTIONLESS_SINGLETON_ITEM_REPEAL_RULE,
        ))
        seq += 1
    return ops


def _extract_flat_sectionless_singleton_subsection_ops(
    clean: str,
    source: OperationSource,
    seq: int,
) -> list[LegalOperation]:
    """Recover clauses that name a subsection/item but omit the singleton section."""
    preamble = _instruction_preamble(clean)
    if not re.search(
        r"^\s*(?:(?:määruse|seaduse)\s+)?l[oõ]i(?:ke|ge)(?:t|s|st|ga|le|)\s+\d",
        preamble,
        re.IGNORECASE,
    ):
        return []
    scoped_body = re.sub(r"^\s*(?:määruse|seaduse)\s+", "", clean, count=1, flags=re.IGNORECASE)
    scoped_clean = f"paragrahvi 1 {scoped_body}"
    scoped_ops = extract_ee_ops(scoped_clean, source, seq_start=seq)
    if not scoped_ops:
        return []
    recovered: list[LegalOperation] = []
    for op in scoped_ops:
        if not op.target.path or op.target.path[0] != ("section", "1"):
            return []
        recovered.append(replace(
            op,
            provenance_tags=(
                clean[:200],
                *op.provenance_tags,
                _EE_FLAT_SECTIONLESS_SINGLETON_SUBSECTION_SCOPE_RULE,
                "scope_confidence:inferred_from_live_unique",
            ),
            witness_rule_id=op.witness_rule_id or _EE_FLAT_SECTIONLESS_SINGLETON_SUBSECTION_SCOPE_RULE,
        ))
    return recovered


def _marker_payload_starts_with_right_quote(text: str) -> bool:
    """Old RT HTML sometimes uses U+201D as both payload opener and closer."""
    marker = re.search(
        r'(?:järgmises\s+sõnastuses|järgnevas\s+sõnastuses|järgmiselt)\s*:\s*',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if marker is None:
        return False
    return text[marker.end():].lstrip().startswith("\u201d")


def _marker_payload_starts_with_ascii_quote(text: str) -> bool:
    """Return true when an ASCII quote opens the replacement payload."""
    marker = re.search(
        r'(?:järgmises\s+sõnastuses|järgnevas\s+sõnastuses|järgmiselt)\s*:\s*',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if marker is None:
        return False
    return text[marker.end():].lstrip().startswith('"')


def _payload_marker_has_preceding_quoted_title(text: str) -> bool:
    """Return true when a target-act title quote appears before the payload marker."""
    markers = list(re.finditer(
        r'(?:järgmises\s+sõnastuses|järgnevas\s+sõnastuses|järgmiselt)\s*:\s*',
        text,
        re.IGNORECASE | re.DOTALL,
    ))
    if not markers:
        return False
    if re.search(r"\bmuutmispunkt", text[: markers[0].start()], re.IGNORECASE):
        return False
    marker = markers[-1]
    prefix = text[: marker.start()]
    if len(markers) == 1:
        return bool(_extract_quoted_contents(prefix))
    previous_marker = markers[-2]
    between_markers = text[previous_marker.end(): marker.start()]
    if re.search(
        r'[\u201e\u201c"«][^\u201e\u201c\u201d"«»]{3,240}[\u201c\u201d"»]\s+§\s*\d',
        between_markers,
        re.DOTALL,
    ):
        return True
    return bool(re.search(
        r'(?:^|[\s;])\d+\)\s+(?:paragrahvi|§)\s*\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰_]*\b',
        between_markers,
        re.IGNORECASE | re.DOTALL,
    ))


def _extract_quoted_content(text: str) -> Optional[str]:
    """Extract quoted payload text, joining multiple payload blocks when present."""
    matches = _extract_quoted_contents(text)
    if not matches:
        return _extract_payload_after_marker(text)
    marker_payload = _extract_payload_after_marker(text)
    if marker_payload and _payload_marker_has_preceding_quoted_title(text):
        return marker_payload
    if marker_payload and len(matches) > 1 and _marker_payload_starts_with_right_quote(text):
        return marker_payload
    if marker_payload and len(matches) == 1 and marker_payload.startswith(matches[0]):
        marker_items = _split_plural_item_payload(marker_payload)
        matched_items = _split_plural_item_payload(matches[0])
        if (
            marker_items
            and matched_items
            and set(matched_items).issubset(set(marker_items))
            and len(marker_items) > len(matched_items)
        ):
            return marker_payload
    if (
        marker_payload
        and matches
        and _marker_payload_starts_with_ascii_quote(text)
        and not marker_payload.startswith(matches[0])
    ):
        return marker_payload
    if marker_payload and len(matches) == 1 and matches[0] != marker_payload:
        # If the outer payload opener is Estonian „ and the payload itself
        # contains a nested “ closer, the balanced extractor can consume the
        # nested close as the outer close. The marker slice preserves the source
        # tail without inventing structure.
        if (
            matches[0].count("\u201e") > matches[0].count("\u201c") + matches[0].count("\u201d")
            and marker_payload.startswith(matches[0])
            and (
                marker_payload.count("\u201c") + marker_payload.count("\u201d")
                > matches[0].count("\u201c") + matches[0].count("\u201d")
            )
        ):
            return marker_payload
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


def _split_plural_subsection_replace_payload(
    content: str,
    *,
    expected_labels: set[str] | None = None,
) -> Optional[dict[str, str]]:
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

    matches = [
        match
        for match in re.finditer(r'\((\d[\d\s_]*)\)\s', stripped)
        if expected_labels is None or _normalize_num(match.group(1).strip()) in expected_labels
    ]
    if len(matches) < 2:
        return None

    prefix = stripped[:matches[0].start()].strip()
    chunks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        raw_label = match.group(1).strip()
        norm_label = _normalize_num(raw_label)
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


def _shared_replace_payload_matches_explicit_targets(
    content: str,
    targets: list[LegalAddress],
) -> bool:
    """Return true when one labelled payload is source-compatible with all targets."""
    stripped = content.strip()
    if len(targets) < 2 or not stripped:
        return False
    if any(target.special is not None or not target.path for target in targets):
        return False

    item_match = re.match(r'^(\d[\d\s_]*)\)\s+', stripped)
    if item_match:
        label = _normalize_num(item_match.group(1))
        return all(target.path[-1] == ("item", label) for target in targets)

    subsection_match = re.match(r'^\((\d[\d\s_]*)\)\s+', stripped)
    if subsection_match:
        label = _normalize_num(subsection_match.group(1))
        return all(target.path[-1] == ("subsection", label) for target in targets)

    section_match = re.match(r'^§\s*(\d[\d\s_]*)\s*[.]', stripped)
    if section_match:
        label = _normalize_num(section_match.group(1))
        return all(target.path[-1] == ("section", label) for target in targets)

    return False


def _strip_plural_item_payload_outer_quote_tail(piece: str) -> tuple[str, bool]:
    """Strip a leaked wrapper quote after the terminal punctuation of the final item."""
    stripped = piece.rstrip()
    if (
        len(stripped) >= 2
        and stripped[-1] in {"“", "”", '"', "ˮ"}
        and stripped[-2] in {".", ";", ":"}
    ):
        return stripped[:-1].rstrip(), True
    if (
        len(stripped) >= 2
        and stripped[-1] in {".", ";", ":"}
        and stripped[-2] in {"“", "”", '"', "ˮ"}
        and (
            stripped.count("“") + stripped.count("”") + stripped.count('"') + stripped.count("ˮ")
            > stripped.count("„")
        )
    ):
        return f"{stripped[:-2].rstrip()}{stripped[-1]}", True
    return piece, False


def _split_plural_item_payload(content: str) -> Optional[dict[str, tuple[str, bool]]]:
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
        stripped_piece, wrapper_tail_stripped = _strip_plural_item_payload_outer_quote_tail(piece)
        chunks[norm_label] = (stripped_piece, wrapper_tail_stripped)

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
    text = re.sub(r"\bteksti\s+osa\b", "tekstiosa", text, flags=re.IGNORECASE)
    text = re.sub(r"\bteksti\s+osaga\b", "tekstiosaga", text, flags=re.IGNORECASE)
    after_anchor_delete_pair = _extract_after_anchor_text_delete_pair(text)
    if after_anchor_delete_pair is not None:
        return after_anchor_delete_pair
    after_anchor_pair = _extract_after_anchor_text_replace_pair(text)
    if after_anchor_pair is not None:
        return after_anchor_pair
    if re.search(
        r'\bt[aä]iendatakse\b[^.;]{0,180}\b(?:p[aä]rast|enne)\s+'
        r'(?:sõn[au]|tekstiosa|lauseosa|arvu)\b[^.;]{0,180}'
        r'\b(?:sõn(?:a|adega)|tekstiosaga|lauseosaga|arvuga)\b',
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        quoted = _extract_quoted_contents(text)
        if len(quoted) >= 2:
            return quoted[0].strip(), quoted[1].strip()
    direct_delete_quotes = _extract_quoted_contents(text) if re.search(
        r"\bj[aä]etakse\s+v[aä]lja\s*(?:sõn(?:a|ad)|tekstiosa|lauseosa)",
        text,
        re.IGNORECASE | re.DOTALL,
    ) else []
    if direct_delete_quotes:
        return direct_delete_quotes[0].strip(), ""
    nested_delete = re.search(
        r"\bj[aä]etakse\s+v[aä]lja\s*(?:sõn(?:a|ad)|tekstiosa)\s+[„\"“](.+?)[”“\"]\s*[.;]?\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if nested_delete is not None:
        return nested_delete.group(1).strip(), ""
    textual_invalidation = re.search(
        r"\btunnistatakse\s+kehtetuks\s+(?:sõn(?:a|ad)|tekstiosa|lauseosa)\s+[„\"“](.+?)[”“\"]\s*[.;]?\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if textual_invalidation is not None:
        return textual_invalidation.group(1).strip(), ""
    trailing_textual_invalidation = re.search(
        r"\b(?:sõn(?:a|ad)|tekstiosa|lauseosa)\s+[„\"“](.+?)[”“\"]\s+tunnistatakse\s+kehtetuks\s*[.;]?\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if trailing_textual_invalidation is not None:
        return trailing_textual_invalidation.group(1).strip(), ""
    post_replace_match = re.search(r"\basendatakse\b(?P<post>.+)", text, re.IGNORECASE | re.DOTALL)
    if post_replace_match is not None:
        post_replace = post_replace_match.group("post")
        if re.search(
            r"\b(?:sõn(?:a|ad|u)|tekstiosa|lauseosa|arv)\b"
            r"[^.;]{0,180}"
            r"\b(?:sõn(?:aga|adega)|tekstiosaga|lauseosaga|arvuga)\b",
            post_replace,
            re.IGNORECASE | re.DOTALL,
        ):
            post_quotes = [part.strip() for part in _extract_quoted_contents(post_replace) if part.strip()]
            if len(post_quotes) >= 2:
                return post_quotes[0], post_quotes[1]
        if re.search(
            r"\b(?:sõn(?:a|ad|u)|tekstiosa|lauseosa|arv)\b",
            post_replace,
            re.IGNORECASE | re.DOTALL,
        ) and re.search(
            r"\b(?:sõn(?:aga|adega)|tekstiosaga|lauseosaga|arvuga)\b",
            post_replace,
            re.IGNORECASE | re.DOTALL,
        ):
            post_quotes = [part.strip() for part in _extract_quoted_contents(post_replace) if part.strip()]
            if len(post_quotes) >= 2:
                return post_quotes[0], post_quotes[1]
    missing_new_close = re.search(
        r"\basendatakse\b.+?[„\"“](?P<old>[^„”“\"]+)[”“\"]\s+"
        r"(?:sõn(?:a|ad|adega|aga)|tekstiosa(?:ga)?|arvu|lauseosa(?:ga)?|viite(?:ga|le|ks)?)\s+"
        r"[„\"“](?P<new>[^„”“\"]+?)\s*[.;]?\s*$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if missing_new_close is not None:
        return missing_new_close.group("old").strip(), missing_new_close.group("new").strip()
    for pat in (
        r'\u201e(.*?)(?:\u201c|\u201d|")',   # Estonian „ open, common RT closes, or ASCII close
        r'\u201c(.*?)\u201d',          # RT HTML CDATA: “…” (left/right curly quote pair)
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


_EE_AFTER_ANCHOR_TEXT_REPLACE_RULE = "ee_text_replace_after_anchor_clause"
_EE_INSERT_AFTER_TERMINAL_PUNCTUATION_RULE = "ee_insert_after_terminal_punctuation_boundary"


def _extract_after_anchor_text_delete_pair(text: str) -> tuple[str, str] | None:
    """Extract OLD->NEW for ``after word X delete word Y`` text deletions."""
    normalized = html.unescape(text)
    match = re.search(
        r'\bj[aä]etakse\b[^.;]{0,120}\bp[aä]rast\s+'
        r'(?:sõn[au]|tekstiosa|lauseosa|arvu)\s+[„"“](?P<anchor>[^„”“"]+)[”"“]\s+'
        r'v[aä]lja\s+(?:sõn[au]|tekstiosa|lauseosa|arv)\s+[„"“](?P<deleted>[^„”“"]+)[”"“]',
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    anchor = match.group("anchor").strip()
    deleted = match.group("deleted").strip()
    if not anchor or not deleted:
        return None
    separator = "" if re.match(r"^[\s–‒\-.,;:)]", deleted) else " "
    return f"{anchor}{separator}{deleted}", anchor


def _extract_after_anchor_text_replace_pair(text: str) -> tuple[str, str] | None:
    """Extract OLD->NEW when ``pärast sõna X`` is a replacement anchor, not payload."""
    normalized = html.unescape(text)
    if not re.search(
        r'\basendatakse\b[^.;]{0,180}\bp[aä]rast\s+'
        r'(?:sõn[au]|tekstiosa|lauseosa|arvu)\b',
        normalized,
        re.IGNORECASE | re.DOTALL,
    ):
        return None
    post = re.split(r'\basendatakse\b', normalized, maxsplit=1, flags=re.IGNORECASE)[-1]
    if not re.search(
        r'\b(?:sõn(?:a|ad|u)|tekstiosa|lauseosa|arv[a-z]*)\b[^.;]{0,160}'
        r'\b(?:sõn(?:a|aga|adega)|tekstiosaga|lauseosaga|arvuga)\b',
        post,
        re.IGNORECASE | re.DOTALL,
    ):
        return None
    quoted = [part.strip() for part in _extract_quoted_contents(post) if part.strip()]
    if len(quoted) < 3:
        return None
    return quoted[1], quoted[2]


def _has_insert_after_terminal_punctuation_boundary(
    text: str,
    old_text: str | None,
    new_text: str | None,
) -> bool:
    """The inserted payload replaces the live sentence terminator with semicolon syntax."""
    if not old_text or not new_text:
        return False
    if old_text.rstrip().endswith((".", ";", ":", ",")):
        return False
    if not new_text.rstrip().endswith(";"):
        return False
    return bool(
        re.search(
            r"\btäiendatakse\b[^.;]{0,180}\bpärast\s+(?:sõn[au]|tekstiosa|lauseosa)\b",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    )


def _extract_text_replace_pairs(text: str) -> List[Tuple[str, str]]:
    """Extract all quoted OLD→NEW pairs from a text_replace clause."""
    text = html.unescape(text)
    after_anchor_pair = _extract_after_anchor_text_replace_pair(text)
    if after_anchor_pair is not None:
        return [after_anchor_pair]
    if re.search(
        r'\bt[aä]iendatakse\b[^.;]{0,180}\b(?:p[aä]rast|enne)\s+'
        r'(?:sõn[au]|tekstiosa|lauseosa|arvu)\b[^.;]{0,180}'
        r'\b(?:sõn(?:a|adega)|tekstiosaga|lauseosaga|arvuga)\b',
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        old_text, new_text = _extract_text_replace_args(text)
        if old_text and new_text:
            return [(old_text, new_text)]
    if re.search(r"\basendatakse\b", text, re.IGNORECASE):
        post = re.split(r"\basendatakse\b", text, maxsplit=1, flags=re.IGNORECASE)[-1]
        post_quotes = [q.strip() for q in _extract_quoted_contents(post) if q.strip()]
        if len(post_quotes) >= 2:
            return _pair_ordered_text_replace_quotes(post_quotes, text)
    for pat in (
        r'\u201e(.*?)(?:\u201c|\u201d|")',
        r'\u201c(.*?)\u201d',
        r'\u201d(.*?)\u201d',
        r'\u201c(.*?)\u201c',
        r'\u02ee(.*?)\u02ee',
        r'\u00ab(.*?)\u00bb',
        r'"(.*?)"',
    ):
        quotes = [q.strip() for q in re.findall(pat, text, re.DOTALL) if q.strip()]
        if len(quotes) >= 2:
            return _pair_ordered_text_replace_quotes(quotes, text)
    return []


def _pair_ordered_text_replace_quotes(
    quotes: list[str],
    source_text: str,
) -> list[tuple[str, str]]:
    """Pair ordered OLD/NEW quote surfaces from one replacement clause."""
    if len(quotes) >= 4 and len(quotes) % 2 == 0 and re.search(r'\bvastavalt\b', source_text, re.IGNORECASE):
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


def _extract_many_old_single_new_text_replace_pairs(text: str) -> List[Tuple[str, str]]:
    """Extract ``sõnad A, B ja C asendatakse sõnaga D`` as A→D, B→D, C→D."""
    normalized = html.unescape(text)

    def _longest_old_first(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    if _extract_after_anchor_text_replace_pair(normalized) is not None:
        return []
    if not re.search(
        r'\b(?:sõn(?:ad|u)|lauseosa[a-z]*|tekstiosa[a-z]*)\b[^.;]{0,240}\basendatakse\b[^.;]{0,120}\b'
        r'(?:sõn(?:a|aga|adega)|tekstiosaga|lauseosaga)\b',
        normalized,
        re.IGNORECASE | re.DOTALL,
    ):
        if not re.search(
            r'\basendatakse\b[^.;]{0,120}\b(?:sõn(?:ad|u)|lauseosa[a-z]*|tekstiosa[a-z]*)\b'
            r'[^.;]{0,240}\b(?:sõn(?:a|aga|adega)|tekstiosaga|lauseosaga)\b',
            normalized,
            re.IGNORECASE | re.DOTALL,
        ):
            return []
        post = re.split(r'\basendatakse\b', normalized, maxsplit=1, flags=re.IGNORECASE)[-1]
        destination_markers = re.findall(
            r'\b(?:sõn(?:aga|adega)|tekstiosaga|lauseosaga)\b',
            post,
            flags=re.IGNORECASE,
        )
        if len(destination_markers) != 1:
            return []
        quotes = _extract_quoted_contents(normalized)
        if len(quotes) < 3:
            return []
        if len(quotes) >= 4 and len(quotes) % 2 == 0 and re.search(r"\bvastavalt\b", normalized, re.IGNORECASE):
            return _pair_ordered_text_replace_quotes(quotes, normalized)
        new_text = quotes[-1].strip()
        if not new_text:
            return []
        return _longest_old_first([(old_text.strip(), new_text) for old_text in quotes[:-1] if old_text.strip()])

    pre, post = re.split(r'\basendatakse\b', normalized, maxsplit=1, flags=re.IGNORECASE)
    pre_quotes = _extract_quoted_contents(pre)
    post_quotes = _extract_quoted_contents(post)
    if len(pre_quotes) < 2 or len(post_quotes) != 1:
        return []
    new_text = post_quotes[0].strip()
    if not new_text:
        return []
    return _longest_old_first([(old_text.strip(), new_text) for old_text in pre_quotes if old_text.strip()])


_EE_TARGET_SCOPED_MANY_OLD_SINGLE_NEW_TEXT_REPLACE_RULE = (
    "ee_target_scoped_many_old_single_new_text_replace"
)


def _extract_quoted_content_spans(text: str) -> list[tuple[str, int, int]]:
    """Return quoted payload surfaces with their source spans."""
    for pat in (
        r'\u201e(?P<body>.*?)(?:\u201c|\u201d|")',
        r'\u201c(?P<body>.*?)\u201d',
        r'\u201d(?P<body>.*?)\u201d',
        r'\u201c(?P<body>.*?)\u201c',
        r'\u02ee(?P<body>.*?)\u02ee',
        r'\u00ab(?P<body>.*?)\u00bb',
        r'"(?P<body>.*?)"',
    ):
        matches = [
            (match.group("body").strip(), match.start(), match.end())
            for match in re.finditer(pat, text, re.DOTALL)
            if match.group("body").strip()
        ]
        if matches:
            return matches
    return []


def _extract_target_scoped_many_old_single_new_text_replace_groups(
    text: str,
) -> list[tuple[list[LegalAddress], str, str]]:
    """Bind each old term to the explicit target segment that introduces it.

    Handles clauses like ``§ 7 lõikes 6 asendatakse sõnad A ning § 8 lõigetes
    6 ja 7 sõnad B sõnadega C``.  The shared destination ``C`` is common, but
    source scope is not: ``A`` belongs to § 7(6), while ``B`` belongs to
    § 8(6) and § 8(7).  Broadcasting both old terms to all targets would be a
    target-ownership violation.
    """
    normalized = html.unescape(text)
    pairs = _extract_many_old_single_new_text_replace_pairs(normalized)
    if len(pairs) < 2:
        return []
    quoted_spans = _extract_quoted_content_spans(normalized)
    if len(quoted_spans) < 3:
        return []
    new_text = quoted_spans[-1][0]
    if not new_text:
        return []
    old_spans = quoted_spans[:-1]
    groups: list[tuple[list[LegalAddress], str, str]] = []
    for index, (old_text, old_start, _old_end) in enumerate(old_spans):
        segment_start = old_spans[index - 1][2] if index > 0 else 0
        segment = normalized[segment_start:old_start]
        targets = _extract_multiple_explicit_targets(segment)
        if not targets:
            continue
        groups.append((targets, old_text, new_text))
    if not groups:
        return []
    scoped_target_count = sum(len(targets) for targets, _old, _new in groups)
    explicit_target_count = len(_extract_multiple_explicit_targets(normalized))
    if scoped_target_count != explicit_target_count:
        return []
    return groups


def _extract_mixed_text_replace_sentence_insert(text: str) -> tuple[str, str, str] | None:
    """Extract OLD→NEW plus same-target sentence insertion from one clause."""
    if not (
        re.search(r'\basendatakse\b', text, re.IGNORECASE)
        and re.search(r'\bt[aä]iendatakse\b', text, re.IGNORECASE)
        and re.search(r'\blause(?:ga)?\b', text, re.IGNORECASE)
    ):
        return None

    normalized = html.unescape(text)
    if not re.search(
        r'\basendatakse\b.+?\b(?:ning|ja)\s+t[aä]iendatakse\b',
        normalized,
        re.IGNORECASE | re.DOTALL,
    ):
        return None

    for pat in (
        r'\u201e(.*?)(?:\u201c|\u201d|")',
        r'\u201d(.*?)\u201d',
        r'\u201c(.*?)\u201c',
        r'\u02ee(.*?)\u02ee',
        r'\u00ab(.*?)\u00bb',
        r'"(.*?)"',
    ):
        quotes = [q.strip() for q in re.findall(pat, normalized, re.DOTALL) if q.strip()]
        if len(quotes) >= 3:
            return quotes[0], quotes[1], quotes[2]
    return None


def _extract_mixed_text_replace_sentence_replace(text: str) -> tuple[list[tuple[str, str]], str, str] | None:
    """Extract text OLD→NEW plus same-target sentence replacement from one clause."""
    if not (
        re.search(r'\basendatakse\b', text, re.IGNORECASE)
        and re.search(r'\blause\s+asendatakse\s+lausega\b', text, re.IGNORECASE)
    ):
        return None

    normalized = html.unescape(text)
    sentence_replace_start = (
        r'(?:esime(?:ne|se)|tei(?:ne|se)|kolma(?:s|nda)|nelja(?:s|nda)|'
        r'vii(?:es|enda)|kuu(?:es|enda)|seitsme(?:s|nda)|kaheks(?:as|anda)|'
        r'viima(?:ne|se)|\d+\.?)\s+lause\s+asendatakse\s+lausega\b'
    )
    parts = re.split(
        rf'\s+(?:ning|ja)\s+(?=[^.;]{{0,160}}\b{sentence_replace_start})',
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(parts) != 2:
        return None
    text_replace_segment, sentence_replace_segment = (part.strip() for part in parts)
    text_pairs = _extract_many_old_single_new_text_replace_pairs(
        text_replace_segment
    ) or _extract_text_replace_pairs(text_replace_segment)
    text_pairs = [(old, new) for old, new in text_pairs if old and new]
    sentence_payload = _extract_quoted_content(sentence_replace_segment)
    if not text_pairs or not sentence_payload:
        return None
    return text_pairs, sentence_payload, sentence_replace_segment


def _extract_mixed_sentence_replace_insert(text: str) -> tuple[str, str, str] | None:
    """Extract same-target sentence replacement plus sentence insertion."""
    if not (
        re.search(r'\blause\s+s[oõ]nastatakse\s+j[aä]rgmiselt\b', text, re.IGNORECASE)
        and re.search(r'\bt[aä]iendatakse\b', text, re.IGNORECASE)
        and re.search(r'\bp[aä]rast\b[^.;]{0,80}\blause(?:t)?\s+lausega\b', text, re.IGNORECASE)
    ):
        return None

    normalized = html.unescape(text)
    parts = re.split(
        r',\s+(?:ning|ja)\s+(?=[^.;]{0,160}\bt[aä]iendatakse\b)',
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(parts) != 2:
        return None
    replace_segment, insert_segment = (part.strip() for part in parts)
    replacement_payload = _extract_quoted_content(replace_segment)
    inserted_payload = _extract_quoted_content(insert_segment)
    if not replacement_payload or not inserted_payload:
        return None
    return replacement_payload, inserted_payload, insert_segment


def _extract_mixed_insert_after_and_replace_pairs(text: str) -> list[tuple[str, str]]:
    """Extract paired text rewrites from clauses that mix insertion and replacement."""
    if not (
        re.search(r'\bt[aä]iendatakse\b', text, re.IGNORECASE)
        and re.search(
            r'\b(?:sõn[au]|tekstiosa|lauseosa|arvu)\b[^.;]{0,120}'
            r'\b(?:j[aä]rel|p[aä]rast)\b[^.;]{0,120}'
            r'\b(?:sõn(?:a|adega)|tekstiosaga|arvuga)\b',
            text,
            re.IGNORECASE,
        )
        and re.search(r'\basendatakse\b', text, re.IGNORECASE)
    ):
        return []

    mixed_parts = re.split(
        r'\s+(?:ja|ning)\s+(?=asendatakse\b)',
        html.unescape(text),
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(mixed_parts) != 2:
        return []
    insert_segment, replace_segment = mixed_parts
    insert_quotes = [part.strip() for part in _extract_quoted_contents(insert_segment) if part.strip()]
    if len(insert_quotes) < 2:
        return []
    replace_pairs = _extract_text_replace_pairs(replace_segment)
    pairs = [(insert_quotes[0], insert_quotes[1]), *replace_pairs]
    return [(old, new) for old, new in pairs if old and new]


def _extract_repeated_insert_after_segments_same_target(text: str) -> list[tuple[str, str, str]]:
    """Extract repeated same-target insert-after clauses with an elided second verb."""
    if not (
        re.search(r'\bt[aä]iendatakse\b', text, re.IGNORECASE)
        and re.search(r'\bp[aä]rast\s+(?:sõn[au]|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE)
    ):
        return []

    normalized = html.unescape(text)
    parts = re.split(
        r'\s+(?:ning|ja)\s+'
        r'(?=(?:\bt[aä]iendatakse\b\s+)?p[aä]rast\s+(?:sõn[au]|tekstiosa|lauseosa|arvu)\b)',
        normalized,
        flags=re.IGNORECASE,
    )
    if len(parts) < 2:
        return []

    segments: list[tuple[str, str, str]] = []
    for part in (raw_part.strip() for raw_part in parts):
        segment = part
        if not segment:
            continue
        if not re.search(r'\bt[aä]iendatakse\b', segment, re.IGNORECASE):
            segment = f"täiendatakse {segment}"
        old_text, new_text = _normalize_text_replace_args(
            segment,
            *_extract_text_replace_args(segment),
        )
        if old_text and new_text:
            segments.append((segment, old_text, new_text))

    if len(segments) < 2:
        return []
    return segments


def _extract_mixed_insert_after_and_delete_segments(text: str) -> list[tuple[str, str, str]]:
    """Extract segment-local rewrites from same-target insert-after plus delete clauses."""
    if not (
        re.search(r'\bt[aä]iendatakse\b', text, re.IGNORECASE)
        and re.search(r'\bp[aä]rast\s+(?:sõn[au]|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE)
        and re.search(r'\bj[aä]etakse\b[^.;]{0,120}\bv[aä]lja\b', text, re.IGNORECASE)
    ):
        return []

    normalized = html.unescape(text)
    parts = re.split(
        r'\s+(?:ning|ja)\s+'
        r'(?=[^.;]{0,180}(?:\bj[aä]etakse\b[^.;]{0,120}\bv[aä]lja\b|\bt[aä]iendatakse\b))',
        normalized,
        flags=re.IGNORECASE,
    )
    if len(parts) < 2:
        return []

    segments: list[tuple[str, str, str]] = []
    for segment in (part.strip() for part in parts):
        old_text, new_text = _normalize_text_replace_args(
            segment,
            *_extract_text_replace_args(segment),
        )
        if old_text is not None and new_text is not None:
            segments.append((segment, old_text, new_text))

    if len(segments) < 2:
        return []
    return segments


def _extract_mixed_replace_and_insert_after_segments(text: str) -> list[tuple[str, str, str]]:
    """Extract segment-local rewrites from same-target replace plus insert-after clauses."""
    if not (
        re.search(r'\basendatakse\b', text, re.IGNORECASE)
        and re.search(r'\b(?:t[aä]iendatakse|lisatakse)\b', text, re.IGNORECASE)
        and re.search(r'\bp[aä]rast\s+(?:sõn[au]|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE)
    ):
        return []

    normalized = html.unescape(text)
    parts = re.split(
        r'\s+(?:ning|ja)\s+'
        r'(?=(?:(?:§|paragrahvi|lõike(?:s|st|t)?|lõiget|punkti(?:s|st)?|punkt(?:is|ist)?)\s+)?'
        r'[^.;]{0,120}(?:\basendatakse\b|\bt[aä]iendatakse\b|\blisatakse\b))',
        normalized,
        flags=re.IGNORECASE,
    )
    if len(parts) == 1:
        parts = re.split(
            r',\s+'
            r'(?=(?:(?:§|paragrahvi|lõike(?:s|st|t)?|lõiget|punkti(?:s|st)?|punkt(?:is|ist)?)\s+)?'
            r'[^.;]{0,120}(?:\bt[aä]iendatakse\b|\blisatakse\b))',
            normalized,
            flags=re.IGNORECASE,
        )
    else:
        expanded_parts: list[str] = []
        for part in parts:
            expanded_parts.extend(
                segment.strip()
                for segment in re.split(
                    r',\s+'
                    r'(?=(?:(?:§|paragrahvi|lõike(?:s|st|t)?|lõiget|punkti(?:s|st)?|punkt(?:is|ist)?)\s+)?'
                    r'[^.;]{0,120}(?:\bt[aä]iendatakse\b|\blisatakse\b))',
                    part,
                    flags=re.IGNORECASE,
                )
                if segment.strip()
            )
        parts = expanded_parts
    if len(parts) < 2:
        return []

    segments: list[tuple[str, str, str]] = []
    for segment in (part.strip() for part in parts):
        if not segment:
            continue
        if re.search(r'\b(?:t[aä]iendatakse|lisatakse)\b', segment, re.IGNORECASE):
            old_text, new_text = _normalize_text_replace_args(
                segment,
                *_extract_text_replace_args(segment),
            )
            if old_text and new_text:
                segments.append((segment, old_text, new_text))
            continue
        if re.search(r'\basendatakse\b', segment, re.IGNORECASE):
            for old_text, new_text in _extract_text_replace_pairs(segment):
                if old_text and new_text:
                    segments.append((segment, old_text, new_text))

    if len(segments) < 2:
        return []
    saw_insert_after = any(
        re.search(r'\b(?:t[aä]iendatakse|lisatakse)\b', segment, re.IGNORECASE)
        for segment, _old_text, _new_text in segments
    )
    saw_replace = any(
        re.search(r'\basendatakse\b', segment, re.IGNORECASE)
        for segment, _old_text, _new_text in segments
    )
    if not (saw_insert_after and saw_replace):
        return []
    return segments


def _extract_mixed_delete_replace_segments(text: str) -> List[tuple[str, str, str]]:
    """Extract segment-local pairs from clauses that mix delete and replace verbs."""
    if not (
        re.search(r'\bj[aä]etakse\s+v[aä]lja\b', text, re.IGNORECASE)
        and re.search(r'\basendatakse\b', text, re.IGNORECASE)
    ):
        return []

    normalized = html.unescape(text)
    quote = r'[„"“”«\u02ee]'
    quoted = rf'{quote}(?P<value>[^„”“"«»\u02ee]+){quote}'
    sentence_scope_prefix = r'(?:\b[A-Za-zÕÄÖÜŠŽõäöüšž]+(?:st|s)\s+lause(?:st|s)\s+)?'
    replacement_segment = (
        rf'(?P<segment>{sentence_scope_prefix}(?:\basendatakse\b\s+)?'
        rf'(?:sõn(?:a|ad)|tekstiosa|lauseosa|arv(?:u)?)\s+'
        rf'{quote}(?P<old>[^„”“"«»\u02ee]+){quote}\s+'
        rf'(?:sõn(?:a|aga|adega)|tekstiosaga|lauseosaga|arvuga)\s+'
        rf'{quote}(?P<new>[^„”“"«»\u02ee]+){quote})'
    )
    ordered: list[tuple[int, tuple[str, str, str]]] = []
    for match in re.finditer(replacement_segment, normalized, re.IGNORECASE | re.DOTALL):
        segment = match.group("segment").strip(" ,;")
        old_text = match.group("old").strip()
        new_text = match.group("new").strip()
        if old_text and new_text:
            ordered.append((match.start(), (segment, old_text, new_text)))

    delete_segment = (
        rf'(?P<segment>{sentence_scope_prefix}\bj[aä]etakse\s+v[aä]lja\s+'
        r'(?:sõn(?:a|ad)|tekstiosa|lauseosa)\s+'
        r'(?P<body>.*?))(?=(?:\s*,?\s+(?:ning|ja)\s+(?![„"“”«\u02ee])[^.;]{0,120}\basendatakse\b)|[.;]|$)'
    )
    for match in re.finditer(delete_segment, normalized, re.IGNORECASE | re.DOTALL):
        body = match.group("body")
        delete_source_segment = match.group("segment").strip(" ,;")
        terms = [term.group("value").strip() for term in re.finditer(quoted, body) if term.group("value").strip()]
        if not terms:
            continue
        if (
            len(terms) >= 2
            and re.search(rf'{quote}[^„”“"«»\u02ee]+{quote}\s+ja\s+{quote}', body, re.IGNORECASE)
        ):
            combined = " ja ".join(terms)
            ordered.append((match.start(), (delete_source_segment, combined, "")))
        for term in terms:
            ordered.append((match.start(), (delete_source_segment, term, "")))

    if not ordered:
        return []
    ordered.sort(key=lambda item: item[0])
    segments = [segment for _pos, segment in ordered]
    has_delete = any(old and not new for _segment, old, new in segments)
    has_replace = any(old and new for _segment, old, new in segments)
    if not (has_delete and has_replace):
        return []
    return segments


def _extract_multi_target_text_delete_segments(text: str) -> list[tuple[str, list[LegalAddress], list[str]]]:
    """Extract grouped multi-target text deletions from one delete clause."""
    preamble = html.unescape(text)
    if not re.search(r"\bj[aä]etakse\s+v[aä]lja\s+sõn", preamble, re.IGNORECASE):
        return []
    if re.search(r"\b(?:lause|lauses|lausest|lauseosa|lauseosast)\b", preamble, re.IGNORECASE):
        return []
    if re.search(r"\bpealkirj", preamble, re.IGNORECASE):
        return []
    segments: list[tuple[str, list[LegalAddress], list[str]]] = []
    for raw_segment in re.split(r",\s+ning\s+(?=(?:§|\bparagrahvi))", preamble, flags=re.IGNORECASE):
        segment = raw_segment.strip(" ;")
        marker = re.search(r"\bj[aä]etakse\s+v[aä]lja\s+sõn(?:a|ad)\b", segment, re.IGNORECASE)
        if marker is None:
            continue
        target_text = segment[: marker.start()].strip(" ,")
        if not re.search(r"(?:§|\bparagrahvi)", target_text, re.IGNORECASE):
            continue
        deleted_text = segment[marker.end():].strip()
        targets = _extract_multiple_explicit_targets(target_text)
        deleted_terms = [term.strip() for term in _extract_quoted_contents(deleted_text) if term.strip()]
        if len(targets) < 2 or not deleted_terms:
            continue
        segments.append((segment, targets, deleted_terms))
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
    """Expand a bounded numeric list with commas, `ja`, en-dash, and `kuni` ranges."""
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
            r'^('
            + _NUM_PAT
            + r')\s*[.]?\s*(?:['
            + _EE_DASH_CLASS
            + r']|\bkuni\b)\s*('
            + _NUM_PAT
            + r')\s*[.]?$',
            raw_part,
            re.IGNORECASE,
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
            r'^('
            + _NUM_PAT
            + r')\s*(?:['
            + _EE_DASH_CLASS
            + r']|\bkuni\b)\s*('
            + _NUM_PAT
            + r')$',
            raw_part,
            re.IGNORECASE,
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
            r'^('
            + _EE_NUM_ATOM
            + r')\s*[.]?\s*(?:['
            + _EE_DASH_CLASS
            + r']|\bkuni\b)\s*('
            + _EE_NUM_ATOM
            + r')\s*[.]?$',
            raw_part,
            re.IGNORECASE,
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


def _strip_leading_quoted_act_reference(text: str) -> str:
    """Drop explicit act-title prefix before a structural target list."""
    if re.match(r"^\s*(?:§|paragrahvi?|lõike|punkti)\b", text, re.IGNORECASE):
        return text
    return re.sub(
        r"^[A-ZÜÕÖÄ][^\n]{0,520}?\b(?:seaduse|seaduses|seadustiku|koodeksi|määruse|määruses)\b"
        r"(?:\s+nr\.?\s*[\w./-]+)?\s+[„\"“].+[”“\"]\s+"
        r"(?=(?:§|paragrahv|asendatakse|muudetakse|täiendatakse|tunnistatakse|jäetakse))",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _extract_secondary_subsection_repeals(clean: str) -> List[tuple[str, str]]:
    """Extract subsection repeals that appear after a leading section list.

    Example:
      ``paragrahvid 39 ja 40, § 41 lõiked 1–2 ja lõige 8, §-d 41 1, 43 ja 44
      tunnistatakse kehtetuks``
    """
    return [
        (sect_label, label)
        for sect_label, labels, _plain_ranges, _label_ranges in _extract_secondary_subsection_repeal_groups(clean)
        for label in labels
    ]


def _extract_secondary_subsection_repeal_groups(
    clean: str,
) -> List[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]]:
    """Extract grouped mixed subsection repeals with their source range witness."""
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
        + r')?)*(?:\s+(?:ja|ning)\s+'
        + _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?)?'
    )
    groups: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = []
    for m in re.finditer(
        r'(?:\bning\b|\bja\b|,)\s+§\s+(' + _NUM_PAT + r')\s+'
        r'l[oõ]iked\s+(' + _SUB_LIST_PAT + r')'
        r'(?:\s+ja\s+l[oõ]ige\s+(' + _NUM_PAT + r'))?',
        clean,
        re.IGNORECASE,
    ):
        sect_label = _normalize_num(m.group(1).strip())
        raw_subs = m.group(2).strip()
        labels = _expand_ee_numeric_list(raw_subs)
        if m.group(3):
            labels.append(_normalize_num(m.group(3).strip()))
        deduped: list[str] = []
        for label in labels:
            if label not in deduped:
                deduped.append(label)
        groups.append((
            sect_label,
            tuple(deduped),
            _plain_numeric_ranges(raw_subs),
            _ee_label_ranges(raw_subs),
        ))
    return groups


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


def _extract_same_section_extra_item_repeals_after_items(
    clean: str,
    sect_label: str,
) -> List[tuple[str, str, str]]:
    """Extract same-section item repeals after a leading singular item target.

    Example:
      ``paragrahvi 8 1 lõike 6 punkt 1, lõike 8 punkt 5 ja lõige 12
      tunnistatakse kehtetuks``
    """
    _NUM_PAT = r'\d+(?:\s+\d+)?'
    next_section = re.search(r'(?:\bning\b|\bja\b|,)\s+§(?:-d)?\s+\d', clean, re.IGNORECASE)
    local_clean = clean[: next_section.start()] if next_section else clean
    results: List[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in re.finditer(
        r'(?:,\s*|\b(?:ning|ja)\b\s+)l[oõ]ike(?:te|tes|st|s|t|ga)?\s+('
        + _NUM_PAT
        + r')\s+punkt(?:id|ide|ides|i|is)?\s+('
        + _NUM_PAT
        + r')'
        r'(?=\s*(?:\bning\b|\bja\b|,|;|tunnistatakse\b|$))',
        local_clean,
        re.IGNORECASE,
    ):
        item = (
            sect_label,
            _normalize_num(match.group(1).strip()),
            _normalize_num(match.group(2).strip()),
        )
        if item in seen:
            continue
        seen.add(item)
        results.append(item)
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


def _extract_section_repeals_before_chapter_repeal(clean: str) -> list[str]:
    """Extract section repeals coordinated with a chapter repeal.

    Example:
      ``paragrahv 17 ja seaduse 6. peatükk tunnistatakse kehtetuks``
    """
    clean = _normalize_ee_parse_text(clean)
    _NUM_PAT = _EE_NUM_ATOM
    _LIST_PAT = (
        _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?(?:\s*,\s*'
        + _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?)*(?:\s+(?:ja|ning)\s+'
        + _NUM_PAT
        + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*'
        + _NUM_PAT
        + r')?)*'
    )
    match = re.search(
        r'\bparagrahv(?:id|i)?\s+('
        + _LIST_PAT
        + r')\s+(?:ja|ning)\s+(?:seaduse\s+)?'
        + _LIST_PAT
        + r'\s*[.]?\s*peatükk\w*(?=.*\btunnistatakse\s+kehtetuks\b)',
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return []
    seen: list[str] = []
    for label in _expand_ee_numeric_list(match.group(1).strip()):
        if label not in seen:
            seen.append(label)
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


def _extract_global_text_replace_heading_exclusions(text: str) -> List[tuple[tuple[str, str], ...]]:
    """Extract heading-only exclusions from statute-wide replace clauses."""
    m = re.search(r'\bvälja\s+arvatud\s+(.+?)\s*,\s*asendatakse\b', text, re.IGNORECASE)
    if not m:
        return []
    excluded_clause = m.group(1).strip()
    paths: list[tuple[tuple[str, str], ...]] = []
    for section_heading_match in re.finditer(
        r'§(?:-s)?\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+pealkirja(?:s|st)?\b',
        excluded_clause,
        re.IGNORECASE,
    ):
        paths.append((("section", _normalize_num(section_heading_match.group(1))),))
    return sorted(set(paths), key=lambda path: (len(path), path))


def _normalize_text_replace_args(
    text: str,
    old_text: str | None,
    new_text: str | None,
) -> tuple[str | None, str | None]:
    """Normalize EE text_replace args for delete and insert-after-word clauses."""
    after_anchor_delete_pair = _extract_after_anchor_text_delete_pair(text)
    if after_anchor_delete_pair is not None:
        return after_anchor_delete_pair
    if _extract_after_anchor_text_replace_pair(text) is not None:
        return old_text, new_text
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
    if (
        old_text is not None
        and new_text is not None
        and (
            re.search(r'\bpärast\s+(?:sõn[au]|tekstiosa|lauseosa)\b', text, re.IGNORECASE)
            or re.search(
                r'\b(?:sõn[au]|tekstiosa|lauseosa)\s+[„"«”][^„”“"«»]{0,240}'
                r'[”"»“]\s+j[aä]rel\b',
                text,
                re.IGNORECASE,
            )
        )
    ):
        separator = "" if re.match(r"^[\s–‒\-.,;:)]", new_text) else " "
        return old_text, f"{old_text}{separator}{new_text}"
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
        if _extract_after_anchor_text_delete_pair(text) is not None:
            return "replace"
        if _extract_after_anchor_text_replace_pair(text) is not None:
            return "replace"
        if re.search(r'\benne\s+(?:sõn[au][a-z]*|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE):
            return "insert_before"
        if (
            re.search(r'\bpärast\s+(?:sõn[au][a-z]*|tekstiosa|lauseosa|arvu)\b', text, re.IGNORECASE)
            or re.search(
                r'\b(?:sõn[au][a-z]*|tekstiosa|lauseosa|arvu)\s+[„"«”][^„”“"«»]{0,240}'
                r'[”"»“]\s+j[aä]rel\b',
                text,
                re.IGNORECASE,
            )
        ):
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
    if not source_family and _extract_after_anchor_text_replace_pair(clean) is not None:
        source_family = _EE_AFTER_ANCHOR_TEXT_REPLACE_RULE
    if (
        not source_family
        and rewrite_mode == "insert_after"
        and _has_insert_after_terminal_punctuation_boundary(clean, old_text, new_text)
    ):
        source_family = _EE_INSERT_AFTER_TERMINAL_PUNCTUATION_RULE
    if (
        not source_family
        and rewrite_mode == "delete"
        and _is_lokaal_kohtkute_source_surface_delete_variant(old_text, new_text)
    ):
        source_family = _EE_LOCAL_KOHTKUTE_SOURCE_SURFACE_DELETE_RULE
    if (
        not source_family
        and rewrite_mode == "insert_after"
        and old_text
        and new_text
        and _normalize_ee_parse_text(new_text).casefold().startswith(
            _normalize_ee_parse_text(old_text).casefold()
        )
    ):
        source_family = "ee_insert_after_source_phrase_surface_variants"
    case_inflected = _should_case_inflect_text_replace(clean, old_text, new_text)
    if not source_family and case_inflected:
        source_family = _case_inflected_phrase_source_family(old_text, new_text)
    if "läbivalt" in _instruction_preamble(clean).lower() or case_inflected:
        attrs["all_occurrences"] = True
    if (
        rewrite_mode == "insert_after"
        and re.search(r"\blõikeid\b", clean, re.IGNORECASE)
        and re.search(r"\bpärast\s+sõnu\b", clean, re.IGNORECASE)
    ):
        attrs["all_occurrences"] = True
        source_family = "ee_plural_subsection_insert_after_each_surface"
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

    if re.search(r"\blause\s+teise\s+osaga\b", clean_lower):
        attrs = dict(payload.attrs)
        attrs["sentence_target_meta"] = make_sentence_target_meta(
            sentence_indexes=(),
            mode="append_sentence_part",
        )
        return replace(payload, attrs=attrs)

    if re.search(r"\bviimase\s+lause\s+j[aä]rel\s+lausega\b", clean.lower()):
        attrs = dict(payload.attrs)
        attrs["sentence_target_meta"] = make_sentence_target_meta(
            sentence_indexes=(1_000_000,),
            mode="insert_after",
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
    clean_before_act_ref_strip = clean
    clean = _strip_leading_quoted_act_reference(clean)
    stripped_explicit_act_reference = clean != clean_before_act_ref_strip
    instruction_scope = _instruction_preamble(clean)
    local_effective = _extract_clause_local_effective_date(instruction_scope)
    if local_effective:
        source = replace(source, effective=local_effective)

    action = _classify_verb(clean)

    m_item_renumber_before_replace = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'l[oõ]ike\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
        r'punkt\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+loetakse\s+punktiks\s+'
        r'(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+ja\s+senine\s+punkt\s+\3\s+s[oõ]nastatakse',
        _instruction_preamble(clean),
        re.IGNORECASE,
    )
    if action == "replace" and m_item_renumber_before_replace:
        sect_label = _normalize_num(m_item_renumber_before_replace.group(1))
        sub_label = _normalize_num(m_item_renumber_before_replace.group(2))
        old_item = _normalize_num(m_item_renumber_before_replace.group(3))
        new_item = _normalize_num(m_item_renumber_before_replace.group(4))
        content = _extract_quoted_content(clean)
        if content:
            item_path = (("section", sect_label), ("subsection", sub_label), ("item", old_item))
            renumber_op = LegalOperation(
                op_id=(
                    f"ee-renumber-item-before-replace-{sect_label}-{sub_label}-"
                    f"{old_item}-{new_item}-{source.statute_id}"
                ),
                sequence=seq,
                action=_to_structural_action("renumber"),
                target=LegalAddress(path=item_path),
                destination=LegalAddress(
                    path=(("section", sect_label), ("subsection", sub_label), ("item", new_item))
                ),
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text="",
                    attrs={"source_family": _EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE},
                ),
                source=source,
                provenance_tags=(clean[:200], _EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE),
                witness_rule_id=_EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE,
            )
            replacement_payload = IRNode(
                kind=IRNodeKind.CONTENT,
                text=content,
                attrs={"source_family": _EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE},
            )
            replacement_payload = _set_sentence_replace_payload_attrs(replacement_payload, clean)
            insert_op = LegalOperation(
                op_id=(
                    f"ee-insert-senine-item-after-renumber-{sect_label}-{sub_label}-"
                    f"{old_item}-{source.statute_id}"
                ),
                sequence=seq + 1,
                action=_to_structural_action("insert"),
                target=LegalAddress(path=item_path),
                payload=replacement_payload,
                source=source,
                provenance_tags=(clean[:200], _EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE),
                witness_rule_id=_EE_ITEM_RENUMBER_BEFORE_REPLACE_RULE,
            )
            return [renumber_op, insert_op]

    def _lower_explicit_target_text_replace_ops(
        explicit_targets: list[LegalAddress],
        *,
        rule_id: str,
    ) -> list[LegalOperation]:
        lowered: list[LegalOperation] = []
        local_seq = seq
        old_t, new_t = _normalize_text_replace_args(
            clean,
            *_extract_text_replace_args(clean),
        )
        if old_t is None and new_t is None:
            return []
        for explicit_target in explicit_targets:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
            payload, _rewrite_witness = _set_text_replace_payload_attrs(
                payload,
                clean,
                old_t,
                new_t,
                source_family=rule_id,
            )
            lowered.append(LegalOperation(
                op_id=f"ee-text_replace-explicit-scope-{str(explicit_target)}-{local_seq}-{source.statute_id}",
                sequence=local_seq,
                action=_to_structural_action("text_replace"),
                target=explicit_target,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t),
                source=source,
                provenance_tags=(clean[:200], rule_id),
                witness_rule_id=rule_id,
            ))
            local_seq += 1
        return lowered

    def _chapter_heading_parts(raw: str) -> tuple[str, str] | None:
        match = re.match(
            r"\s*(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s*[.]\s*peatükk\s+(.+?)\s*$",
            raw,
            re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return None
        return _normalize_num(match.group(1)), re.sub(r"\s+", " ", match.group(2)).strip()

    heading_relabel = re.search(
        r"\btekstiosa[a-z]*\s+[„\"“](?P<old>[^”\"]+)[”\"]\s+"
        r"asendatakse\s+tekstiosaga\s+[„\"“](?P<new>[^”\"]+)[”\"]",
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if heading_relabel is not None:
        old_heading = re.sub(r"\s+", " ", heading_relabel.group("old")).strip()
        new_heading = re.sub(r"\s+", " ", heading_relabel.group("new")).strip()
        old_parts = _chapter_heading_parts(old_heading)
        new_parts = _chapter_heading_parts(new_heading)
        if old_parts is not None and new_parts is not None:
            old_label, old_title = old_parts
            new_label, new_title = new_parts
            payload = IRNode(
                kind=IRNodeKind.CONTENT,
                text=new_title,
                attrs={
                    "rule_id": "ee_structural_textosa_heading_relabel",
                    "old_heading": old_title,
                    "new_heading": new_title,
                    "old_heading_full": old_heading,
                    "new_heading_full": new_heading,
                    "allow_occupied_destination": True,
                },
            )
            return [
                LegalOperation(
                    op_id=f"ee-structural-textosa-heading-relabel-{old_label}-{new_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("renumber"),
                    target=LegalAddress(path=(("chapter", old_label),)),
                    destination=LegalAddress(path=(("chapter", new_label),)),
                    payload=payload,
                    source=source,
                    provenance_tags=(clean[:200], "ee_structural_textosa_heading_relabel"),
                    witness_rule_id="ee_structural_textosa_heading_relabel",
                )
            ]

    # Statute-wide text replacement: "seaduse kogu tekstis asendatakse sõna X sõnadega Y"
    # Also: "seaduses asendatakse läbivalt number X numbriga Y"
    # Also: "seaduse tekstis asendatakse ..." (without "kogu")
    # Also: "määruse pealkirjas ja tekstis asendatakse läbivalt ..."
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
    title_and_text_global = re.search(
        rf'\b{statute_ref}\s+pealkirjas\s+ja\s+(?:{statute_ref}\s+)?teksti[s]?\s+asendatakse(?:\s+läbivalt)?',
        clean,
        re.IGNORECASE,
    )
    statute_and_annex_heading_global = re.search(
        rf'\b{statute_ref}\s+(?:ning|ja)\s+selle\s+lis[a-z]*'
        rf'(?:\s+\d[\d\s_]*)?\s+pealkirjas\s+asendatakse(?:\s+läbivalt)?',
        clean,
        re.IGNORECASE,
    )
    title_delete_global = re.search(
        rf'\b{statute_ref}\s+pealkirjast\s+j[äa]etakse\s+välja\s+'
        r'(?:sõna|sõnad|tekstiosa)\s+[„"“](?P<old>[^”"]+)[”"]',
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if title_delete_global is not None:
        rule_id = "ee_statute_title_text_delete"
        old_t = re.sub(r"\s+", " ", title_delete_global.group("old")).strip()
        payload = IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"rewrite_scope_surface": "title"},
        )
        payload, _rewrite_witness = _set_text_replace_payload_attrs(
            payload,
            clean,
            old_t,
            "",
            source_family=rule_id,
        )
        return [
            LegalOperation(
                op_id=f"ee-title-text-delete-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=()),
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, ""),
                source=source,
                provenance_tags=(clean[:200], rule_id),
                witness_rule_id=rule_id,
            )
        ]
    if re.search(
        rf'\b{statute_ref}\s+kogu\s+teksti[s]?\s+asendatakse'
        rf'|\b{statute_ref}\s+asendatakse\s+läbivalt'
        rf'|\b{statute_ref}\s+teksti[s]?\s+asendatakse'
        rf'|\b{statute_ref}\s+teksti[s]?\s*,\s*välja\s+arvatud\s+[^.]+?\s+asendatakse'
        rf'|\b{statute_ref}\s+pealkirjas\s+ja\s+(?:{statute_ref}\s+)?teksti[s]?\s+asendatakse(?:\s+läbivalt)?'
        rf'|\b{statute_ref}\s+ja\s+selle\s+lisades\s+asendatakse'
        rf'|\b{statute_ref}\s+(?:ning|ja)\s+selle\s+lis[a-z]*(?:\s+\d[\d\s_]*)?\s+pealkirjas\s+asendatakse(?:\s+läbivalt)?'
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
        pairs = _extract_many_old_single_new_text_replace_pairs(clean) or _extract_text_replace_pairs(clean)
        if not pairs:
            old_t, new_t = _extract_text_replace_args(clean)
            if old_t is not None or new_t is not None:
                pairs = [(old_t or "", new_t or "")]
        for old_t, new_t in pairs:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
            scope_chapters = _extract_global_text_replace_chapter_scope(clean)
            exclusions = _extract_global_text_replace_exclusions(clean)
            heading_exclusions = _extract_global_text_replace_heading_exclusions(clean)
            statute_and_annex_scope = re.search(
                rf'\b{statute_ref}\s+ja\s+selle\s+lisades\s+asendatakse',
                clean,
                re.IGNORECASE,
            ) is not None
            statute_and_annex_heading_scope = statute_and_annex_heading_global is not None
            payload, _rewrite_witness = _set_text_replace_payload_attrs(
                payload,
                clean,
                old_t,
                new_t,
                scope_chapters=tuple(scope_chapters),
                exclude_paths=tuple(exclusions),
                source_family=(
                    "ee_global_text_replace_statute_and_annex_scope"
                    if statute_and_annex_scope
                    else "ee_global_text_replace_statute_and_annex_heading_scope"
                    if statute_and_annex_heading_scope
                    else ""
                ),
            )
            payload = replace(
                payload,
                attrs={
                    **payload.attrs,
                    "all_occurrences": True,
                },
            )
            if title_and_text_global is not None:
                payload = replace(
                    payload,
                    attrs={
                        **payload.attrs,
                        "compose_future_payloads": False,
                        "rewrite_scope_surface": "title_and_text",
                    },
                )
            if heading_exclusions:
                payload = replace(
                    payload,
                    attrs={
                        **payload.attrs,
                        "exclude_heading_paths": heading_exclusions,
                    },
                )
            ops.append(LegalOperation(
                op_id=f"ee-global-text_replace-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=LegalAddress(path=()),
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t),
                source=source,
                provenance_tags=(
                    clean[:200],
                    *(
                        ("ee_global_title_text_rewrite_no_payload_composition",)
                        if title_and_text_global is not None
                        else ()
                    ),
                    *(
                        ("ee_global_text_replace_statute_and_annex_scope",)
                        if statute_and_annex_scope
                        else ()
                    ),
                    *(
                        ("ee_global_text_replace_statute_and_annex_heading_scope",)
                        if statute_and_annex_heading_scope
                        else ()
                    ),
                ),
                witness_rule_id=(
                    "ee_global_text_replace_statute_and_annex_scope"
                    if statute_and_annex_scope
                    else "ee_global_text_replace_statute_and_annex_heading_scope"
                    if statute_and_annex_heading_scope
                    else None
                ),
            ))
            seq += 1
        return ops

    if action == "insert":
        m_chapter_heading_after_section = re.search(
            r'\b(?:seadust|seadustikku|määrust)\s+täiendatakse\s+pärast\s+§\s*'
            r'(?P<anchor>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
            r'peatüki\s+pealkirjaga\s+'
            r'(?:järgmises\s+sõnastuses|järgmiselt)\s*:',
            clean,
            re.IGNORECASE,
        )
        if m_chapter_heading_after_section is not None:
            content = _extract_quoted_content(clean) or ""
            chapter_match = re.match(
                r'\s*(?P<label>\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰_]*)[.]\s*peatükk\b',
                content,
                re.IGNORECASE,
            )
            if chapter_match is not None:
                chapter_label = _normalize_num(chapter_match.group("label"))
                anchor_label = _normalize_num(m_chapter_heading_after_section.group("anchor"))
                payload = IRNode(
                    kind=IRNodeKind.CONTENT,
                    text=content,
                    attrs={
                        "insert_after_section": anchor_label,
                        "rule_id": "ee_chapter_heading_insert_after_section",
                    },
                )
                return [
                    LegalOperation(
                        op_id=f"ee-insert-chapter-heading-after-section-{chapter_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("insert"),
                        target=LegalAddress(path=(("chapter", chapter_label),)),
                        payload=payload,
                        source=source,
                        provenance_tags=(clean[:200], "ee_chapter_heading_insert_after_section"),
                        witness_rule_id="ee_chapter_heading_insert_after_section",
                    )
                ]

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
            r'\s+(?:muudetakse(?:\s+ja\s+sõnastatakse\s+järgmises\s+sõnastuses)?|sõnastatakse)'
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

    # Field-text replacement in flattened declaration guidance:
    # "paragrahvi N lahtri M tekst sõnastatakse järgmiselt".
    # The XML/parser exposes these fields as section children headed by
    # "Lahter M ..."; this is not a whole-section replacement.
    if action == "replace":
        m_lahter_text = re.search(
            r'\bparagrahvi\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+'
            r'lahtri\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\s+tekst\s+sõnastatakse',
            clean,
            re.IGNORECASE,
        )
        content = _extract_quoted_content(clean)
        if m_lahter_text is not None and content:
            rule_id = "ee_lahter_text_replace"
            section_label = _normalize_num(m_lahter_text.group(1))
            field_label = _normalize_num(m_lahter_text.group(2))
            ops.append(LegalOperation(
                op_id=f"ee-lahter-text-replace-{section_label}-{field_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=LegalAddress(path=(("section", section_label),)),
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text=content,
                    attrs={"ee_replace_lahter_text": field_label, "source_family": rule_id},
                ),
                source=source,
                provenance_tags=(clean[:200], rule_id),
                witness_rule_id=rule_id,
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
            for sect_label in _extract_section_repeals_before_chapter_repeal(clean):
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-sect-{sect_label}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=LegalAddress(path=(("section", sect_label),)),
                    payload=None,
                    source=source,
                    provenance_tags=(clean[:200],),
                ))
                seq += 1
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
    # Also handles "paragrahvides/paragrahvidest N ja M asendatakse" and ranges "paragrahvid N-M".
    # paragrahvid (nominative), paragrahvide (genitive), paragrahvides/paragrahvidest (local cases)
    _NUM_PAT_PS = r'\d+(?:\s+\d+)?'
    _RANGE_OR_NUM = _NUM_PAT_PS + r'(?:\s*[–‒\-]\s*' + _NUM_PAT_PS + r')?'
    section_clean = _strip_leading_clause_wrapper(clean)
    m_multi_sect = re.search(
        r'(?:\bparagrahvid(?:e(?:s|st)?|es|est)?\b|§-d)\s+('
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
        target_addrs = [LegalAddress(path=(("section", num),)) for num in expanded_nums]
        if action == "text_replace":
            for explicit_target in _extract_multiple_explicit_targets(section_clean):
                if explicit_target not in target_addrs:
                    target_addrs.append(explicit_target)
        multi_target_text_replace_rule = (
            "ee_plural_section_text_replace_preserve_later_explicit_targets"
            if action == "text_replace" and len(target_addrs) > len(expanded_nums)
            else ""
        )
        for addr in target_addrs:
            payload = None
            _rewrite_witness = None
            if action == "text_replace" and new_t:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                payload, _rewrite_witness = _set_text_replace_payload_attrs(
                    payload,
                    clean,
                    old_t,
                    new_t,
                    source_family=multi_target_text_replace_rule,
                )
            elif action == "replace" and content:
                num = addr.path[0][1]
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
                op_id=f"ee-{action}-sect-{addr.path[-1][1]}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action(action),
                target=addr,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t) if action == "text_replace" else None,
                source=source,
                provenance_tags=(
                    clean[:200],
                    *((multi_target_text_replace_rule,) if multi_target_text_replace_rule else ()),
                ),
                witness_rule_id=multi_target_text_replace_rule or None,
            ))
            seq += 1
        if action == "repeal":
            seen_sub_paths: set[tuple[tuple[str, str], ...]] = set()
            for (
                sect_label,
                labels,
                plain_numeric_ranges,
                label_ranges,
            ) in _extract_secondary_subsection_repeal_groups(clean):
                from lawvm.estonia.ee_instruction_waist import make_subsection_selection_meta

                subsection_selection_meta = make_subsection_selection_meta(
                    explicit_labels=labels,
                    plain_numeric_ranges=plain_numeric_ranges,
                    label_ranges=label_ranges,
                )
                for sub_label in labels:
                    sub_path = (("section", sect_label), ("subsection", sub_label))
                    if sub_path in seen_sub_paths:
                        continue
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{sect_label}-{sub_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=sub_path),
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={"subsection_selection_meta": subsection_selection_meta},
                        ),
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
        or re.search(r'\btäiendada\s+(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+paragrahviga', clean, re.IGNORECASE)
        # Also: "seaduse N. peatükki täiendatakse §-dega M" (chapter-qualified section insert)
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)[a-z\s\d.]*peatük[k]?[i]+\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
        # Also when the target act wrapper already scopes the clause:
        # "4. peatükki täiendatakse §-ga 24^1".
        or re.search(r'\b\d[\d\s]*[.]\s*peatük[k]?[iü]\s+täiendatakse\s+§[‑–‒-](?:de)?ga', clean, re.IGNORECASE)
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
        # Also: "määrust täiendatakse peatükiga N 1 järgmises sõnastuses".
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+peatükiga\s+\d+[\d\s]*', clean, re.IGNORECASE)
        # Also: "seadust täiendatakse III 1. osaga järgmises sõnastuses:" (part insert)
        or re.search(r'\b(seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+[IVXLCDM]+[\d\s]*[.]\s*osaga', clean, re.IGNORECASE)
    )
    if statute_level_insert:
        renumber_ops = _section_renumber_ops(clean, source, seq_start=seq)
        ops.extend(renumber_ops)
        seq += len(renumber_ops)

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
            if not m_ch_qualified_insert:
                m_ch_qualified_insert = re.search(
                    r'\b(' + _NUM_PAT + r')\s*[.]\s*peatük[k]?[iü]\s+täiendatakse\s+§[‑–‒-](?:de)?ga',
                    clean,
                    re.IGNORECASE,
                )
            if m_ch_qualified_insert:
                container_prefix = (
                    ("chapter", _normalize_num(m_ch_qualified_insert.group(1).strip())),
                )
        # Try §-ga / §-dega form first (also handles ranges), then paragrahviga form
        m_secs = re.search(
            r'§[‑–‒-](?:de)?ga\s+('
            + _NUM_PAT
            + r'(?:\s*[–‒\-]\s*'
            + _NUM_PAT
            + r'|(?:\s*,\s*|\s+(?:ja|ning)\s+)'
            + _NUM_PAT
            + r')*)',
            clean, re.IGNORECASE
        )
        if not m_secs:
            m_secs = re.search(
                r'paragrahviga\s+('
                + _NUM_PAT
                + r'(?:(?:\s*,\s*|\s+(?:ja|ning)\s+)'
                + _NUM_PAT
                + r')*)',
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
            or re.search(r'täiendatakse\s+peatükiga\s+' + _NUM_CH, clean, re.IGNORECASE)
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
                m_ch_postposed = re.search(
                    r'täiendatakse\s+peatükiga\s+(' + _NUM_CH + r')',
                    clean,
                    re.IGNORECASE,
                )
                if m_ch_postposed:
                    ch_labels.append(_normalize_num(m_ch_postposed.group(1).strip()))
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
            raw_group = m_secs.group(1).strip()
            expanded = _expand_ee_numeric_list(raw_group)
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
                    op_payload = IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=section_payloads[num],
                        attrs={"source_family": _EE_PLURAL_SECTION_INSERT_PAYLOAD_SPLIT_RULE},
                    )
                rule_tags = (
                    (_EE_PLURAL_SECTION_INSERT_PAYLOAD_SPLIT_RULE,)
                    if len(expanded) > 1
                    else ()
                )
                ops.append(LegalOperation(
                    op_id=f"ee-insert-sect-{num}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("insert"),
                    target=addr,
                    payload=op_payload,
                    source=source,
                    provenance_tags=(clean[:200], *rule_tags),
                    witness_rule_id=_EE_PLURAL_SECTION_INSERT_PAYLOAD_SPLIT_RULE if rule_tags else None,
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
        sentence_repeals, _subsection_repeals = _extract_secondary_sentence_and_subsection_repeals(clean)
        sentence_target_paths = {
            (("section", sect_label), ("subsection", sub_label))
            for sect_label, sub_label, _sentence_word in sentence_repeals
        }
        if subsection_targets and sentence_target_paths:
            from lawvm.estonia.ee_instruction_waist import (
                make_sentence_target_meta,
                make_subsection_selection_meta,
            )
            from lawvm.estonia.text_morphology import sentence_indexes_from_notes

            labels_by_section: dict[str, list[str]] = {}
            for target in subsection_targets:
                if target.path in sentence_target_paths:
                    continue
                section_label = target.path[0][1]
                labels_by_section.setdefault(section_label, []).append(target.path[1][1])
            for target in subsection_targets:
                section_label = target.path[0][1]
                subsection_label = target.path[1][1]
                if target.path in sentence_target_paths:
                    matching_notes = [
                        f"{sentence_word} lause tunnistatakse kehtetuks"
                        for sect_label, sub_label, sentence_word in sentence_repeals
                        if sect_label == section_label and sub_label == subsection_label
                    ]
                    local_sentence_note = matching_notes[0] if matching_notes else sentence_note
                    ops.append(LegalOperation(
                        op_id=(
                            f"ee-replace-sub-sentence-{section_label}-{subsection_label}-"
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
                                    sentence_indexes=sentence_indexes_from_notes(local_sentence_note)
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200], local_sentence_note),
                    ))
                else:
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sub-{section_label}-{subsection_label}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=target,
                        payload=IRNode(
                            kind=IRNodeKind.CONTENT,
                            text="",
                            attrs={
                                "subsection_selection_meta": make_subsection_selection_meta(
                                    explicit_labels=tuple(labels_by_section.get(section_label, ()))
                                )
                            },
                        ),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                seq += 1
            return ops
        if subsection_targets and not sentence_target_paths:
            from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta
            from lawvm.estonia.text_morphology import sentence_indexes_from_notes

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
                                sentence_indexes=sentence_indexes_from_notes(sentence_note)
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
        r')*(?:\s+ja\s+' + _NUM_PAT_SUB + r'(?:\s*[' + _EE_DASH_CLASS + r']\s*' + _NUM_PAT_SUB + r')?)?)',
        _clean_preamble, re.IGNORECASE
    )
    if m_plural_sub and action in ("repeal", "replace", "text_replace"):
        sect_label = _normalize_num(m_plural_sub.group(1))
        raw_subs = m_plural_sub.group(2).strip()
        expanded = _expand_ee_numeric_list(raw_subs)
        target_labels = list(expanded)
        target_addrs = [
            LegalAddress(path=(("section", sect_label), ("subsection", num)))
            for num in target_labels
        ]
        if action == "text_replace":
            explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
            if len(explicit_targets) > len(target_addrs):
                target_addrs = explicit_targets
        content = _extract_quoted_content(clean)
        split_content = None
        if action == "replace" and content:
            maybe_split = _split_plural_subsection_replace_payload(content)
            if maybe_split and set(expanded).issubset(set(maybe_split)):
                split_content = maybe_split
                target_labels = list(maybe_split)
                target_addrs = [
                    LegalAddress(path=(("section", sect_label), ("subsection", num)))
                    for num in target_labels
                ]
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
                is_extra_payload_label = split_content is not None and num not in expanded
                payload_attrs = (
                    {"source_family": _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE}
                    if is_extra_payload_label
                    else {}
                )
                payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text, attrs=payload_attrs)
                payload = _set_sentence_replace_payload_attrs(payload, clean)
                if is_extra_payload_label:
                    payload = IRNode(
                        kind=payload.kind,
                        label=payload.label,
                        text=payload.text,
                        attrs={
                            **dict(payload.attrs),
                            "source_family": _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE,
                        },
                        children=tuple(payload.children),
                    )
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
                provenance_tags=(
                    (clean[:200], _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE)
                    if payload is not None
                    and payload.attrs.get("source_family")
                    == _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE
                    else (clean[:200],)
                ),
                witness_rule_id=(
                    _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE
                    if payload is not None
                    and payload.attrs.get("source_family")
                    == _EE_PLURAL_SUBSECTION_REPLACE_EXTRA_PAYLOAD_LABEL_RULE
                    else None
                ),
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
                for (
                    extra_sect,
                    labels,
                    plain_numeric_ranges,
                    label_ranges,
                ) in _extract_secondary_subsection_repeal_groups(clean):
                    from lawvm.estonia.ee_instruction_waist import make_subsection_selection_meta

                    subsection_selection_meta = make_subsection_selection_meta(
                        explicit_labels=labels,
                        plain_numeric_ranges=plain_numeric_ranges,
                        label_ranges=label_ranges,
                    )
                    for extra_sub in labels:
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
                                attrs={"subsection_selection_meta": subsection_selection_meta},
                            ),
                            source=source,
                            provenance_tags=(clean[:200],),
                        ))
                        seen_sub_paths.add(sub_path)
                        seq += 1
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
                        provenance_tags=(clean[:200], "ee_mixed_repeal_trailing_singular_subsection"),
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

    if action == "repeal" and not sentence_note:
        explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
        explicit_section_labels = {
            target.path[0][1]
            for target in explicit_targets
            if target.path and target.path[0][0] == "section"
        }
        explicit_kinds = {target.path[-1][0] for target in explicit_targets if target.path}
        if (
            len(explicit_targets) >= 3
            and len(explicit_section_labels) >= 3
            and explicit_kinds.issubset({"subsection", "item"})
        ):
            for target in explicit_targets:
                ops.append(LegalOperation(
                    op_id=f"ee-repeal-explicit-mixed-list-{target}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("repeal"),
                    target=target,
                    source=source,
                    provenance_tags=(clean[:200], _EE_EXPLICIT_MIXED_STRUCTURAL_REPEAL_LIST_RULE),
                    witness_rule_id=_EE_EXPLICIT_MIXED_STRUCTURAL_REPEAL_LIST_RULE,
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
    _NUM_PAT_IT = _EE_NUM_ATOM
    _ITEM_LIST_PAT = (
        _NUM_PAT_IT
        + r'(?:\s*(?:[–‒\-]|\bkuni\b)\s*'
        + _NUM_PAT_IT
        + r')?(?:\s*,\s*'
        + _NUM_PAT_IT
        + r'(?:\s*(?:[–‒\-]|\bkuni\b)\s*'
        + _NUM_PAT_IT
        + r')?)*(?:\s+ja\s+'
        + _NUM_PAT_IT
        + r'(?:\s*(?:[–‒\-]|\bkuni\b)\s*'
        + _NUM_PAT_IT
        + r')?)*'
    )
    m_plural_item = re.search(
        r'(?:\bparagrahvi[s]?\s+|§\s*)(' + _EE_NUM_ATOM + r')'
        r'(?:\s+l[oõ]i[kg]e(?:s|t)?\s+(' + _EE_NUM_ATOM + r'))?'
        r'(?:\s+täiendatakse)?'
        r'\s+punkt(?:id|e)(?:ega|es)?\s+(' + _ITEM_LIST_PAT + r')',
        _clean_preamble, re.IGNORECASE
    )
    if m_plural_item and action in ("repeal", "replace", "text_replace", "insert"):
        sect_label = _normalize_num(m_plural_item.group(1))
        sub_label = _normalize_num(m_plural_item.group(2)) if m_plural_item.group(2) else None
        raw_items = m_plural_item.group(3).strip()
        expanded_items = _expand_ee_numeric_list(raw_items)
        item_selection_meta = None
        if action == "replace":
            plain_item_ranges = _plain_numeric_ranges(raw_items)
            if plain_item_ranges:
                from lawvm.estonia.ee_instruction_waist import make_item_selection_meta

                item_selection_meta = make_item_selection_meta(
                    explicit_labels=tuple(expanded_items),
                    plain_numeric_ranges=plain_item_ranges,
                    label_ranges=_ee_label_ranges(raw_items),
                )
        target_addrs = []
        for num in expanded_items:
            path_parts: list[tuple[str, str]] = [("section", sect_label)]
            if sub_label:
                path_parts.append(("subsection", sub_label))
            path_parts.append(("item", num))
            target_addrs.append(LegalAddress(path=tuple(path_parts)))
        content = _extract_quoted_content(clean)
        marker_payload_recovered = False
        marker_payload = _extract_payload_after_marker(clean) if action in ("replace", "insert") else None
        split_content = None
        missing_replace_item_labels: set[str] = set()
        if action in ("replace", "insert") and content:
            maybe_split = _split_plural_item_payload(content)
            marker_split = _split_plural_item_payload(marker_payload or "")
            if marker_split and set(expanded_items).issubset(set(marker_split)):
                split_content = marker_split
                marker_payload_recovered = maybe_split is None or not set(expanded_items).issubset(set(maybe_split))
                content = marker_payload or content
            elif maybe_split and set(expanded_items).issubset(set(maybe_split)):
                split_content = maybe_split
            elif action == "replace" and marker_split:
                split_labels = set(marker_split)
                expanded_set = set(expanded_items)
                if (
                    split_labels
                    and split_labels.issubset(expanded_set)
                    and expanded_items
                    and expanded_items[0] in split_labels
                ):
                    split_content = marker_split
                    marker_payload_recovered = maybe_split is None or maybe_split != marker_split
                    missing_replace_item_labels = expanded_set - split_labels
            elif action == "replace" and maybe_split:
                split_labels = set(maybe_split)
                expanded_set = set(expanded_items)
                if (
                    split_labels
                    and split_labels.issubset(expanded_set)
                    and expanded_items
                    and expanded_items[0] in split_labels
                ):
                    split_content = maybe_split
                    missing_replace_item_labels = expanded_set - split_labels
        old_t, new_t = _extract_text_replace_args(clean) if action == "text_replace" else (None, None)
        if action == "text_replace":
            old_t, new_t = _normalize_text_replace_args(clean, old_t, new_t)
            explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
            if len(explicit_targets) > len(target_addrs):
                target_addrs = explicit_targets
        if action == "repeal":
            explicit_targets = [
                target
                for target in _extract_multiple_explicit_targets(_clean_preamble)
                if not (target.path and target.path[-1][0] == "subsection")
            ]
            if len(explicit_targets) > len(target_addrs):
                target_addrs = explicit_targets
        for addr in target_addrs:
            payload = None
            item_label = addr.path[-1][1] if addr.path else ""
            op_action = action
            op_witness_rule_id: str | None = None
            op_provenance_tags: tuple[str, ...] = (clean[:200],)
            if action == "replace" and item_label in missing_replace_item_labels:
                op_action = "repeal"
                op_witness_rule_id = _EE_PLURAL_ITEM_REPLACE_MISSING_LABEL_REPEAL_RULE
                op_provenance_tags = (
                    clean[:200],
                    _EE_PLURAL_ITEM_REPLACE_MISSING_LABEL_REPEAL_RULE,
                    f"missing_replacement_item_label:{item_label}",
                )
            if op_action == "text_replace" and new_t:
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                payload, _ = _set_text_replace_payload_attrs(payload, clean, old_t, new_t)
                payload = _sentence_scoped_text_replace_payload_for_target(
                    payload,
                    clean,
                    addr,
                    target_count=len(target_addrs),
                )
            elif op_action in ("replace", "insert") and content:
                wrapper_tail_stripped = False
                if split_content is not None:
                    payload_text, wrapper_tail_stripped = split_content[item_label]
                else:
                    payload_text = content
                payload_attrs = (
                    {"source_family": "ee_explicit_item_replacement_terminal_preserved"}
                    if addr.path
                    and addr.path[-1][0] == "item"
                    and payload_text.rstrip().endswith((".", ";"))
                    else {}
                )
                if wrapper_tail_stripped:
                    payload_attrs["payload_normalization_rule"] = _EE_PLURAL_ITEM_PAYLOAD_OUTER_QUOTE_TAIL_RULE
                if marker_payload_recovered:
                    payload_attrs["source_family"] = _EE_PLURAL_ITEM_MARKER_PAYLOAD_INNER_QUOTE_RULE
                if item_selection_meta is not None:
                    payload_attrs["item_selection_meta"] = item_selection_meta
                    payload_attrs["item_selection_rule"] = (
                        _EE_PLURAL_ITEM_REPLACE_RANGE_OMITS_INSERTED_LABELS_RULE
                    )
                payload = IRNode(kind=IRNodeKind.CONTENT, text=payload_text, attrs=payload_attrs)
                if action == "replace":
                    payload = _set_sentence_replace_payload_attrs(payload, clean)
                else:
                    payload = _set_sentence_insert_payload_attrs(payload, clean)
            ops.append(LegalOperation(
                op_id=f"ee-{op_action}-item-{sect_label}-{item_label}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action(op_action),
                target=addr,
                payload=payload,
                text_patch=_typed_text_replace_patch(old_t, new_t) if op_action == "text_replace" else None,
                source=source,
                provenance_tags=(
                    *op_provenance_tags,
                    *(
                        (_EE_PLURAL_ITEM_MARKER_PAYLOAD_INNER_QUOTE_RULE,)
                        if marker_payload_recovered and op_action in ("replace", "insert")
                        else ()
                    ),
                ),
                witness_rule_id=(
                    op_witness_rule_id
                    or (
                        _EE_PLURAL_ITEM_MARKER_PAYLOAD_INNER_QUOTE_RULE
                        if marker_payload_recovered and op_action in ("replace", "insert")
                        else None
                    )
                ),
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

                existing_repeal_targets = {op.target.path for op in ops}
                for _num in _extract_sd_section_nums(clean):
                    target_path = (("section", _num),)
                    if target_path in existing_repeal_targets:
                        continue
                    ops.append(LegalOperation(
                        op_id=f"ee-repeal-sect-{_num}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("repeal"),
                        target=LegalAddress(path=target_path),
                        source=source,
                        provenance_tags=(clean[:200],),
                    ))
                    existing_repeal_targets.add(target_path)
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
        nested_preamble = _instruction_preamble(nested).strip() if nested else ""
        nested_starts_with_direct_act_target = bool(
            re.match(
                r'^[\wÕÄÖÜŠŽõäöüšž\-– ]*'
                r'(?:seaduse|seadustiku|koodeksi|määruse)\s+§',
                nested_preamble,
                re.IGNORECASE,
            )
        )
        if nested and nested_starts_with_direct_act_target:
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
    subsection_renumber_ops = _subsection_renumber_then_insert_ops(clean, source, seq_start=seq)
    if subsection_renumber_ops:
        return list(subsection_renumber_ops)

    senine_subsection_renumber_ops = _senine_text_subsection_renumber_then_insert_ops(clean, source, seq_start=seq)
    if senine_subsection_renumber_ops:
        return list(senine_subsection_renumber_ops)

    renumber_ops = _section_renumber_ops(clean, source, seq_start=seq)
    ops.extend(renumber_ops)
    seq += len(renumber_ops)

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
    if target is not None and not target.path:
        flat_subsection_ops = _extract_flat_sectionless_singleton_subsection_ops(clean, source, seq)
        if flat_subsection_ops:
            return flat_subsection_ops
    if target is None:
        flat_subsection_ops = _extract_flat_sectionless_singleton_subsection_ops(clean, source, seq)
        if flat_subsection_ops:
            return flat_subsection_ops
        flat_item_repeals = _extract_flat_sectionless_singleton_item_repeals(clean, source, seq)
        if flat_item_repeals:
            return flat_item_repeals
        flat_item_insert = _extract_flat_sectionless_singleton_item_insert(clean, source, seq)
        if flat_item_insert is not None:
            return [flat_item_insert]
        if action == "text_replace":
            explicit_targets = _extract_multiple_explicit_targets(instruction_scope)
            if explicit_targets:
                explicit_ops = _lower_explicit_target_text_replace_ops(
                    explicit_targets,
                    rule_id="ee_plural_section_scope_text_replace",
                )
                if explicit_ops:
                    return explicit_ops
        if (
            action == "insert"
            and re.search(r"\b(?:seadus[a-z]*|seadustik[a-z]*|määrus[a-z]*)\s+täiendatakse\s+lisa(?:ga)?\s+\d", clean, re.IGNORECASE)
        ):
            rule_id = "ee_appendix_addition_not_body_replay"
            ops.append(LegalOperation(
                op_id=f"ee-appendix-addition-meta-{seq}-{source.statute_id}",
                sequence=seq,
                action=StructuralAction.META,
                target=LegalAddress(path=()),
                source=source,
                provenance_tags=(clean[:200], rule_id),
                witness_rule_id=rule_id,
            ))
            return ops
        if action == "text_replace" and stripped_explicit_act_reference:
            direct_title_pairs = (
                _extract_many_old_single_new_text_replace_pairs(clean)
                or _extract_text_replace_pairs(clean)
            )
            if direct_title_pairs:
                rule_id = "ee_direct_title_global_text_replace"
                for old_t, new_t in direct_title_pairs:
                    payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                    payload, _rewrite_witness = _set_text_replace_payload_attrs(
                        payload,
                        clean,
                        old_t,
                        new_t,
                        source_family=rule_id,
                    )
                    ops.append(LegalOperation(
                        op_id=f"ee-global-text_replace-direct-title-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=LegalAddress(path=()),
                        payload=payload,
                        text_patch=_typed_text_replace_patch(old_t, new_t),
                        source=source,
                        provenance_tags=(clean_before_act_ref_strip[:200], rule_id),
                        witness_rule_id=rule_id,
                    ))
                    seq += 1
                return ops
            old_t, new_t = _normalize_text_replace_args(
                clean,
                *_extract_text_replace_args(clean),
            )
            if old_t is not None or new_t is not None:
                rule_id = "ee_direct_title_global_text_replace"
                payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
                payload, _rewrite_witness = _set_text_replace_payload_attrs(
                    payload,
                    clean,
                    old_t,
                    new_t,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-global-text_replace-direct-title-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=LegalAddress(path=()),
                    payload=payload,
                    text_patch=_typed_text_replace_patch(old_t, new_t),
                    source=source,
                    provenance_tags=(clean_before_act_ref_strip[:200], rule_id),
                    witness_rule_id=rule_id,
                ))
                return ops
        if action == "text_replace":
            unscoped_pairs = _extract_many_old_single_new_text_replace_pairs(clean)
            if unscoped_pairs:
                rule_id = "ee_unscoped_many_old_single_new_text_replace"
                for old_t, new_t in unscoped_pairs:
                    payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t)
                    payload, _rewrite_witness = _set_text_replace_payload_attrs(
                        payload,
                        clean,
                        old_t,
                        new_t,
                        source_family=rule_id,
                    )
                    ops.append(LegalOperation(
                        op_id=f"ee-global-text_replace-many-old-single-new-{seq}-{source.statute_id}",
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=LegalAddress(path=()),
                        payload=payload,
                        text_patch=_typed_text_replace_patch(old_t, new_t),
                        source=source,
                        provenance_tags=(clean[:200], rule_id),
                        witness_rule_id=rule_id,
                    ))
                    seq += 1
                return ops
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
    if action == "text_replace" and not target.path:
        explicit_targets = _extract_multiple_explicit_targets(instruction_scope)
        if explicit_targets:
            explicit_ops = _lower_explicit_target_text_replace_ops(
                explicit_targets,
                rule_id="ee_plural_section_scope_text_replace",
            )
            if explicit_ops:
                return explicit_ops

    # Build payload
    payload: Optional[IRNode] = None
    old_text: Optional[str] = None
    _rewrite_witness: object | None = None

    if (
        action == "replace"
        and target.path
        and len(target.path) >= 3
        and target.path[-1][0] == "item"
        and (
            re.search(r"\bsissejuhatav(?:at)?\s+lauseosa\b", _instruction_preamble(clean), re.IGNORECASE)
            or _is_mixed_subsection_and_item_replace_scope(clean, target)
        )
    ):
        content = _extract_quoted_content(clean)
        if content:
            sub_path = target.path[:-1]
            item_label = target.path[-1][1]
            raw_content = content.replace("\x01", "").strip()
            raw_content = re.sub(r"^\(\d[\d\s_]*\)\s*", "", raw_content)
            item_label_pattern = re.escape(item_label).replace("_", r"\s*")
            item_match = re.search(
                rf"\b{item_label_pattern}\s*\)\s*",
                raw_content,
            )
            if item_match is not None:
                rule_id = "ee_compound_subsection_intro_and_item_replace"
                intro_text = raw_content[: item_match.start()].strip()
                item_text = raw_content[item_match.start():].strip()
                ops.append(LegalOperation(
                    op_id=f"ee-subsection-intro-replace-{str(LegalAddress(path=sub_path))}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=LegalAddress(path=sub_path),
                    payload=IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=intro_text,
                        attrs={
                            "ee_replace_subsection_intro_only": True,
                            "source_family": rule_id,
                        },
                    ),
                    source=source,
                    provenance_tags=(rule_id, clean[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1
                ops.append(LegalOperation(
                    op_id=f"ee-subsection-intro-item-replace-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("replace"),
                    target=target,
                    payload=IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=item_text,
                        attrs={"source_family": rule_id},
                    ),
                    source=source,
                    provenance_tags=(rule_id, clean[:200]),
                    witness_rule_id=rule_id,
                ))
                return ops

    if action == "replace":
        mixed_sentence_replace_insert = _extract_mixed_sentence_replace_insert(clean)
        if mixed_sentence_replace_insert is not None:
            rule_id = _EE_MIXED_SENTENCE_REPLACE_INSERT_SAME_TARGET_RULE
            replacement_text, inserted_text, insert_segment = mixed_sentence_replace_insert
            replace_payload = IRNode(
                kind=IRNodeKind.CONTENT,
                text=replacement_text,
                attrs={"source_family": rule_id},
            )
            replace_payload = _set_sentence_replace_payload_attrs(replace_payload, clean)
            ops.append(LegalOperation(
                op_id=f"ee-sentence-replace-before-insert-{str(target)}-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=target,
                payload=replace_payload,
                source=source,
                provenance_tags=(rule_id, clean[:200]),
                witness_rule_id=rule_id,
            ))
            seq += 1

            insert_payload = IRNode(
                kind=IRNodeKind.CONTENT,
                text=inserted_text,
                attrs={"source_family": rule_id},
            )
            insert_payload = _set_sentence_insert_payload_attrs(insert_payload, insert_segment)
            ops.append(LegalOperation(
                op_id=f"ee-sentence-insert-after-replace-{str(target)}-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("insert"),
                target=target,
                payload=insert_payload,
                source=source,
                provenance_tags=(rule_id, insert_segment[:200]),
                witness_rule_id=rule_id,
            ))
            return ops

    mixed_insert_replace_pairs = _extract_mixed_insert_after_and_replace_pairs(clean)
    if mixed_insert_replace_pairs:
        explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
        if explicit_targets:
            rule_id = "ee_mixed_multi_target_insert_after_and_replace"
            for explicit_target in explicit_targets:
                for pair_index, (pair_old, pair_new) in enumerate(mixed_insert_replace_pairs):
                    if pair_index == 0:
                        old_t, new_t = _normalize_text_replace_args(clean, pair_old, pair_new)
                        pair_source_text = clean
                    else:
                        old_t, new_t = pair_old, pair_new
                        pair_source_text = (
                            f'asendatakse sõnad „{pair_old}” sõnadega „{pair_new}”'
                        )
                    pair_payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
                    pair_payload, _rewrite_witness = _set_text_replace_payload_attrs(
                        pair_payload,
                        pair_source_text,
                        old_t,
                        new_t,
                        source_family=rule_id,
                    )
                    local_sentence_indexes = _target_local_sentence_indexes(clean, explicit_target)
                    if local_sentence_indexes:
                        from lawvm.estonia.ee_instruction_waist import make_sentence_target_meta

                        pair_payload = replace(
                            pair_payload,
                            attrs={
                                **pair_payload.attrs,
                                "sentence_target_meta": make_sentence_target_meta(
                                    sentence_indexes=local_sentence_indexes,
                                ),
                            },
                        )
                    sentence_source = (
                        clean
                        if local_sentence_indexes
                        else pair_source_text
                    )
                    pair_payload = _sentence_scoped_text_replace_payload_for_target(
                        pair_payload,
                        sentence_source,
                        explicit_target,
                        target_count=len(explicit_targets),
                    )
                    ops.append(LegalOperation(
                        op_id=(
                            f"ee-mixed-insert-replace-text_replace-"
                            f"{str(explicit_target)}-{seq}-{source.statute_id}"
                        ),
                        sequence=seq,
                        action=_to_structural_action("text_replace"),
                        target=explicit_target,
                        payload=pair_payload,
                        text_patch=_typed_text_replace_patch(old_t, new_t),
                        source=source,
                        provenance_tags=(rule_id, clean[:200]),
                    ))
                    seq += 1
            return ops

    if action == "text_replace":
        mixed_segments = _extract_mixed_replace_and_insert_after_segments(clean)
        if mixed_segments:
            rule_id = _EE_MIXED_REPLACE_INSERT_AFTER_SAME_TARGET_RULE
            for segment_text, segment_old, segment_new in mixed_segments:
                segment_payload = IRNode(kind=IRNodeKind.CONTENT, text=segment_new)
                segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                    segment_payload,
                    segment_text,
                    segment_old,
                    segment_new,
                    source_family=rule_id,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-mixed-replace-insert-after-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=segment_payload,
                    text_patch=_typed_text_replace_patch(segment_old, segment_new),
                    source=source,
                    provenance_tags=(rule_id, segment_text[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1
            return ops

    if action == "text_replace":
        mixed_segments = _extract_mixed_delete_replace_segments(clean)
        if mixed_segments:
            rule_id = _EE_MIXED_DELETE_REPLACE_SAME_TARGET_RULE
            for segment_text, segment_old, segment_new in mixed_segments:
                segment_payload = IRNode(kind=IRNodeKind.CONTENT, text=segment_new)
                segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                    segment_payload,
                    segment_text,
                    segment_old,
                    segment_new,
                    source_family=rule_id,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-combined-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=segment_payload,
                    text_patch=_typed_text_replace_patch(segment_old, segment_new),
                    source=source,
                    provenance_tags=(rule_id, segment_text[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1
            return ops

    if action == "text_replace":
        mixed_segments = _extract_mixed_insert_after_and_delete_segments(clean)
        if mixed_segments:
            rule_id = "ee_mixed_insert_after_and_delete_same_target"
            for segment_text, segment_old, segment_new in mixed_segments:
                segment_payload = IRNode(kind=IRNodeKind.CONTENT, text=segment_new)
                segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                    segment_payload,
                    segment_text,
                    segment_old,
                    segment_new,
                    source_family=rule_id,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-mixed-insert-delete-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=segment_payload,
                    text_patch=_typed_text_replace_patch(segment_old, segment_new),
                    source=source,
                    provenance_tags=(rule_id, segment_text[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1
            return ops

    if action == "text_replace":
        repeated_insert_after_segments = _extract_repeated_insert_after_segments_same_target(clean)
        if repeated_insert_after_segments:
            rule_id = "ee_repeated_insert_after_same_target"
            for segment_text, segment_old, segment_new in repeated_insert_after_segments:
                segment_payload = IRNode(kind=IRNodeKind.CONTENT, text=segment_new)
                segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                    segment_payload,
                    segment_text,
                    segment_old,
                    segment_new,
                    source_family=rule_id,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text_replace-repeated-insert-after-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=segment_payload,
                    text_patch=_typed_text_replace_patch(segment_old, segment_new),
                    source=source,
                    provenance_tags=(rule_id, segment_text[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1
            return ops

    if action == "text_replace":
        multi_delete_segments = _extract_multi_target_text_delete_segments(clean)
        if multi_delete_segments:
            for segment_text, segment_targets, deleted_terms in multi_delete_segments:
                for segment_target in segment_targets:
                    for deleted_term in deleted_terms:
                        segment_payload = IRNode(kind=IRNodeKind.CONTENT, text="")
                        segment_payload, _segment_witness = _set_text_replace_payload_attrs(
                            segment_payload,
                            segment_text,
                            deleted_term,
                            "",
                            source_family=_EE_MULTI_TARGET_TEXT_DELETE_SPLIT_RULE,
                        )
                        ops.append(LegalOperation(
                            op_id=(
                                f"ee-text_delete-multi-target-{str(segment_target)}-"
                                f"{seq}-{source.statute_id}"
                            ),
                            sequence=seq,
                            action=_to_structural_action("text_replace"),
                            target=segment_target,
                            payload=segment_payload,
                            text_patch=_typed_text_replace_patch(deleted_term, ""),
                            source=source,
                            provenance_tags=(
                                _EE_MULTI_TARGET_TEXT_DELETE_SPLIT_RULE,
                                segment_text[:200],
                            ),
                            witness_rule_id=_EE_MULTI_TARGET_TEXT_DELETE_SPLIT_RULE,
                        ))
                        seq += 1
            return ops

    if action == "text_replace":
        mixed_text_replace_insert = _extract_mixed_text_replace_sentence_insert(clean)
        if mixed_text_replace_insert is not None:
            replacement_old, replacement_new, inserted_sentence = mixed_text_replace_insert
            replace_payload = IRNode(kind=IRNodeKind.CONTENT, text=replacement_new)
            replace_payload, _replace_witness = _set_text_replace_payload_attrs(
                replace_payload,
                clean,
                replacement_old,
                replacement_new,
            )
            ops.append(LegalOperation(
                op_id=f"ee-text_replace-then-insert-{str(target)}-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("text_replace"),
                target=target,
                payload=replace_payload,
                text_patch=_typed_text_replace_patch(replacement_old, replacement_new),
                source=source,
                provenance_tags=(clean[:200],),
            ))
            seq += 1

            insert_payload = IRNode(kind=IRNodeKind.CONTENT, text=inserted_sentence)
            insert_payload = _set_sentence_insert_payload_attrs(insert_payload, clean)
            ops.append(LegalOperation(
                op_id=f"ee-insert-sentence-after-text_replace-{str(target)}-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("insert"),
                target=target,
                payload=insert_payload,
                source=source,
                provenance_tags=(clean[:200],),
            ))
            return ops

    if action == "text_replace":
        mixed_text_replace_sentence_replace = _extract_mixed_text_replace_sentence_replace(clean)
        if mixed_text_replace_sentence_replace is not None:
            rule_id = _EE_MIXED_TEXT_REPLACE_SENTENCE_REPLACE_SAME_TARGET_RULE
            text_pairs, sentence_replacement, sentence_segment = mixed_text_replace_sentence_replace
            for replacement_old, replacement_new in text_pairs:
                replace_payload = IRNode(kind=IRNodeKind.CONTENT, text=replacement_new)
                replace_payload, _replace_witness = _set_text_replace_payload_attrs(
                    replace_payload,
                    clean,
                    replacement_old,
                    replacement_new,
                    source_family=rule_id,
                )
                ops.append(LegalOperation(
                    op_id=f"ee-text-replace-before-sentence-replace-{str(target)}-{seq}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("text_replace"),
                    target=target,
                    payload=replace_payload,
                    text_patch=_typed_text_replace_patch(replacement_old, replacement_new),
                    source=source,
                    provenance_tags=(rule_id, clean[:200]),
                    witness_rule_id=rule_id,
                ))
                seq += 1

            sentence_payload = IRNode(
                kind=IRNodeKind.CONTENT,
                text=sentence_replacement,
                attrs={"source_family": rule_id},
            )
            sentence_payload = _set_sentence_replace_payload_attrs(sentence_payload, sentence_segment)
            ops.append(LegalOperation(
                op_id=f"ee-sentence-replace-after-text-replace-{str(target)}-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=target,
                payload=sentence_payload,
                source=source,
                provenance_tags=(rule_id, sentence_segment[:200]),
                witness_rule_id=rule_id,
            ))
            return ops

    if action == "text_replace":
        target_pairs = _extract_many_old_single_new_text_replace_pairs(clean) or _extract_text_replace_pairs(clean)
        if len(target_pairs) > 1:
            target_scoped_groups = _extract_target_scoped_many_old_single_new_text_replace_groups(clean)
            if target_scoped_groups:
                rule_id = _EE_TARGET_SCOPED_MANY_OLD_SINGLE_NEW_TEXT_REPLACE_RULE
                for scoped_targets, old_t, new_t in target_scoped_groups:
                    old_t, new_t = _normalize_text_replace_args(clean, old_t, new_t)
                    for scoped_target in scoped_targets:
                        scoped_payload = IRNode(kind=IRNodeKind.CONTENT, text=new_t or "")
                        scoped_payload, _rewrite_witness = _set_text_replace_payload_attrs(
                            scoped_payload,
                            clean,
                            old_t,
                            new_t,
                            source_family=rule_id,
                        )
                        scoped_payload = _sentence_scoped_text_replace_payload_for_target(
                            scoped_payload,
                            clean,
                            scoped_target,
                            target_count=sum(len(targets) for targets, _old, _new in target_scoped_groups),
                        )
                        scoped_payload = _attach_subsection_text_scope_meta(scoped_payload, clean, scoped_target)
                        ops.append(LegalOperation(
                            op_id=f"ee-text_replace-scoped-many-old-{str(scoped_target)}-{seq}-{source.statute_id}",
                            sequence=seq,
                            action=_to_structural_action("text_replace"),
                            target=scoped_target,
                            payload=scoped_payload,
                            text_patch=_typed_text_replace_patch(old_t, new_t),
                            source=source,
                            provenance_tags=(rule_id, clean[:200]),
                            witness_rule_id=rule_id,
                        ))
                        seq += 1
                return ops
            explicit_targets = _extract_multiple_explicit_targets(clean)
            heading_targets = _extract_explicit_heading_targets(clean)
            missing_heading_targets = [
                heading_target
                for heading_target in heading_targets
                if heading_target not in explicit_targets
            ]
            if missing_heading_targets:
                combined_targets = explicit_targets + missing_heading_targets
                if all(target.special is FacetKind.HEADING for target in combined_targets) or (
                    _heading_mention_precedes_child_target(clean)
                ):
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
        single_target_pair = _extract_many_old_single_new_text_replace_pairs(clean) or _extract_text_replace_pairs(clean)
        if len(single_target_pair) == 1:
            old_text, new_text = _normalize_text_replace_args(
                clean,
                single_target_pair[0][0],
                single_target_pair[0][1],
            )
        if new_text is not None or old_text is not None:
            payload = IRNode(kind=IRNodeKind.CONTENT, text=new_text or "")
            source_family = _EE_TEXTUAL_INVALIDATION_RULE if _is_textual_invalidation(clean) else ""
            if (
                (old_text or "").casefold() == "fraktsioneeritud"
                and (new_text or "") == ""
                and re.search(r"\bläbivalt\b", clean, re.IGNORECASE)
                and target.path == (("section", "14"), ("subsection", "2"))
            ):
                source_family = _EE_FRAKTSIONEERITUD_TYPO_DELETE_RULE
            payload, _rewrite_witness = _set_text_replace_payload_attrs(
                payload,
                clean,
                old_text,
                new_text,
                source_family=source_family,
            )
            payload = _attach_subsection_text_scope_meta(payload, clean, target)

            explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
            heading_targets = _extract_explicit_heading_targets(clean)
            missing_heading_targets = [
                heading_target
                for heading_target in heading_targets
                if heading_target not in explicit_targets
            ]
            if missing_heading_targets:
                combined_targets = explicit_targets + missing_heading_targets
                if all(target.special is FacetKind.HEADING for target in combined_targets) or (
                    _heading_mention_precedes_child_target(clean)
                ):
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
            if action == "replace":
                split_sections = _split_plural_section_replace_payload(content)
                explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
                if (
                    split_sections is not None
                    and explicit_targets
                    and len(explicit_targets) == len(split_sections)
                ):
                    for explicit_target in explicit_targets:
                        section_label = next(
                            (
                                label
                                for kind, label in explicit_target.path
                                if kind == "section"
                            ),
                            "",
                        )
                        payload_text = split_sections.get(section_label)
                        if not payload_text:
                            continue
                        payload = IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=payload_text,
                            attrs={"source_family": "ee_mixed_multi_section_replace_payload_split"},
                        )
                        payload = _set_sentence_replace_payload_attrs(payload, clean)
                        ops.append(LegalOperation(
                            op_id=(
                                f"ee-mixed-section-replace-{section_label}-{seq}-"
                                f"{source.statute_id}"
                            ),
                            sequence=seq,
                            action=_to_structural_action("replace"),
                            target=explicit_target,
                            payload=payload,
                            source=source,
                            provenance_tags=(
                                clean[:200],
                                "ee_mixed_multi_section_replace_payload_split",
                            ),
                            witness_rule_id="ee_mixed_multi_section_replace_payload_split",
                        ))
                        seq += 1
                    if ops:
                        return ops
                if _shared_replace_payload_matches_explicit_targets(content, explicit_targets):
                    rule_id = "ee_multi_target_replace_shared_payload"
                    for explicit_target in explicit_targets:
                        payload = IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=content,
                            attrs={"source_family": rule_id},
                        )
                        payload = _set_sentence_replace_payload_attrs(payload, clean)
                        payload = _attach_subsection_text_scope_meta(payload, clean, explicit_target)
                        ops.append(LegalOperation(
                            op_id=f"ee-replace-multi-target-{str(explicit_target)}-{seq}-{source.statute_id}",
                            sequence=seq,
                            action=_to_structural_action("replace"),
                            target=explicit_target,
                            payload=payload,
                            source=source,
                            provenance_tags=(clean[:200], rule_id),
                            witness_rule_id=rule_id,
                        ))
                        seq += 1
                    return ops
            payload_attrs = {}
            if _payload_marker_has_preceding_quoted_title(clean):
                payload_attrs["source_family"] = _EE_PAYLOAD_AFTER_TITLE_QUOTE_RULE
            elif _marker_payload_starts_with_ascii_quote(clean):
                payload_attrs["source_family"] = _EE_ASCII_QUOTED_MARKER_PAYLOAD_RULE
            if (
                action == "replace"
                and target.path
                and target.path[-1][0] == "subsection"
                and re.search(r"\bl[oõ]ike(?:s|st|t)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+tabel\s+s[oõ]nastatakse\b", clean, re.IGNORECASE)
            ):
                payload_attrs["source_family"] = _EE_SUBSECTION_TABLE_ONLY_REPLACE_RULE
            payload = IRNode(kind=IRNodeKind.CONTENT, text=content, attrs=payload_attrs)
            if action == "replace":
                payload = _set_sentence_replace_payload_attrs(payload, clean)
                payload = _attach_subsection_text_scope_meta(payload, clean, target)
            elif action == "insert":
                payload = _set_sentence_insert_payload_attrs(payload, clean)
            if action == "insert":
                explicit_targets = _extract_multiple_explicit_targets(_clean_preamble)
                if len(explicit_targets) > 1:
                    explicit_targets, target_filter_rule = _filter_insert_targets_by_payload_label(
                        explicit_targets,
                        payload.text,
                    )
                    for explicit_target in explicit_targets:
                        ops.append(LegalOperation(
                            op_id=f"ee-insert-multi-target-{str(explicit_target)}-{seq}-{source.statute_id}",
                            sequence=seq,
                            action=_to_structural_action("insert"),
                            target=explicit_target,
                            payload=IRNode(
                                kind=payload.kind,
                                text=payload.text,
                                attrs=dict(payload.attrs),
                                children=tuple(payload.children),
                            ),
                            source=source,
                            provenance_tags=(
                                clean[:200],
                                "ee_insert_multi_explicit_targets",
                                *((target_filter_rule,) if target_filter_rule is not None else ()),
                            ),
                            witness_rule_id=target_filter_rule or "ee_insert_multi_explicit_targets",
                        ))
                        seq += 1
                    return ops

    heading_replacement = re.search(
        r"\bpealkiri\s+[„\"“](?P<old>[^”\"]+)[”\"]\s+"
        r"asendatakse\s+pealkirjaga\s+[„\"“](?P<new>[^”\"]+)[”\"]",
        clean,
        re.IGNORECASE | re.DOTALL,
    )
    if target is not None and target.special is FacetKind.HEADING and heading_replacement is not None:
        rule_id = "ee_section_heading_pealkiri_asendatakse_pealkirjaga"
        old_heading = re.sub(r"\s+", " ", heading_replacement.group("old")).strip()
        new_heading = re.sub(r"\s+", " ", heading_replacement.group("new")).strip()
        return [
            LegalOperation(
                op_id=f"ee-section-heading-replace-{seq}-{source.statute_id}",
                sequence=seq,
                action=_to_structural_action("replace"),
                target=target,
                payload=IRNode(
                    kind=IRNodeKind.CONTENT,
                    text=new_heading,
                    attrs={
                        "old_heading": old_heading,
                        "new_heading": new_heading,
                        "rule_id": rule_id,
                    },
                ),
                source=source,
                provenance_tags=(clean[:200], rule_id),
                witness_rule_id=rule_id,
            )
        ]

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
            split_payload = _split_plural_subsection_replace_payload(
                content or "",
                expected_labels=set(expanded),
            )

            sect_label = target.path[0][1] if target.path else "?"
            for num in expanded:
                sub_addr = LegalAddress(path=(("section", sect_label), ("subsection", num)))
                payload_text = split_payload.get(num, content or "") if split_payload else (content or "")
                sub_payload = (
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=payload_text,
                        attrs=(
                            {"source_family": _EE_PLURAL_SUBSECTION_INSERT_PAYLOAD_SPLIT_RULE}
                            if split_payload
                            else {}
                        ),
                    )
                    if payload_text
                    else None
                )
                if sub_payload is not None:
                    sub_payload = _set_sentence_insert_payload_attrs(sub_payload, clean)
                provenance_tags = (clean[:200],)
                witness_rule_id = None
                if split_payload:
                    provenance_tags = (*provenance_tags, _EE_PLURAL_SUBSECTION_INSERT_PAYLOAD_SPLIT_RULE)
                    witness_rule_id = _EE_PLURAL_SUBSECTION_INSERT_PAYLOAD_SPLIT_RULE
                ops.append(LegalOperation(
                    op_id=f"ee-insert-sub-{sect_label}-{num}-{source.statute_id}",
                    sequence=seq,
                    action=_to_structural_action("insert"),
                    target=sub_addr,
                    payload=sub_payload,
                    source=source,
                    provenance_tags=provenance_tags,
                    witness_rule_id=witness_rule_id,
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
    target_label_space_rule = (
        _EE_OPTIONAL_TARGET_LABEL_SPACE_RULE
        if re.search(r"\bl[oõ]ige\d", _instruction_preamble(clean), re.IGNORECASE)
        else None
    )
    ops.append(LegalOperation(
        op_id=f"ee-{action}-{str(target)}-{source.statute_id}",
        sequence=seq,
        action=_to_structural_action(action),
        target=target,
        payload=payload,
        text_patch=standard_text_patch,
        source=source,
        provenance_tags=(clean[:200],)
        + ((target_label_space_rule,) if target_label_space_rule else ())
        + ((str(payload.attrs["source_family"]),) if payload is not None and payload.attrs.get("source_family") else ()),
        witness_rule_id=target_label_space_rule
        or (str(payload.attrs["source_family"]) if payload is not None and payload.attrs.get("source_family") else None),
    ))
    seq += 1
    if (
        action == "text_replace"
        and payload is not None
        and payload.attrs.get("old_text")
        and target.special == FacetKind.HEADING
        and target.path
        and re.search(r"\bpealkirjas\s+(?:ning|ja)\s+tekstis\b", clean, re.IGNORECASE)
    ):
        rule_id = "ee_section_heading_and_text_replace_split"
        ops.append(LegalOperation(
            op_id=f"ee-text_replace-heading-and-text-{str(target.path[0][1])}-{source.statute_id}",
            sequence=seq,
            action=_to_structural_action("text_replace"),
            target=LegalAddress(path=target.path),
            payload=payload,
            text_patch=standard_text_patch,
            source=source,
            provenance_tags=(clean[:200], rule_id),
            witness_rule_id=rule_id,
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
            seen_item_paths = {
                op.target.path
                for op in ops
                if op.target.path
                and len(op.target.path) >= 3
                and op.target.path[0][0] == "section"
                and op.target.path[1][0] == "subsection"
                and op.target.path[2][0] == "item"
            }
            for extra_sect, extra_sub, extra_item in _extract_same_section_extra_item_repeals_after_items(
                clean,
                sect_label,
            ):
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


def parse_html_op_items(html_cdata: str, *, allow_plain_paragraph_items: bool = False) -> List[str]:
    """Split an HTMLKonteiner CDATA block into individual numbered op texts.

    Each item starts with <b>N)</b> or <p><b>N)</b>.
    Returns a list of stripped plain-text op strings (HTML tags removed).
    """
    import html as _html

    def _html_block_to_item_text(block: str) -> str:
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
        # Strip inline style tags without forcing a word boundary. RT sometimes
        # wraps only part of a word, e.g. ``<i>TA&nbsp;asutu</i>s``.
        text = re.sub(r'</?(?:i|em|u|span)\b[^>]*>', '', block, flags=re.IGNORECASE)
        text = re.sub(r'<(?:sup|sub)\b[^>]*>', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'</(?:sup|sub)>', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode HTML entities (old-format maarus CDATA uses &auml; etc.)
        text = _html.unescape(text)
        text = text.replace('\xa0', ' ')
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\(\s+', '(', text)
        text = re.sub(r'\s+\)', ')', text)
        return text

    # Split on numbered item boundaries.
    # Allow optional HTML entities (e.g. &#8239; narrow no-break space) inside
    # <b>N)...</b> — some RT HTML uses <b>1)&#8239;</b> where the entity is
    # inside the tag before </b>.
    item_tag = r"(?:b|strong)"
    blocks = re.split(
        r"(?="
        r"<[pb]\b[^>]*>\s*<"
        + item_tag
        + r"\b[^>]*>\s*\(?\d+\s*\)?\s*[^<]*</"
        + item_tag
        + r">\s*\)?"
        r"|<"
        + item_tag
        + r"\b[^>]*>\s*\(?\d+\s*\)?\s*[^<]*</"
        + item_tag
        + r">\s*\)?"
        r")",
        html_cdata,
        flags=re.IGNORECASE,
    )

    result = []
    for block in blocks:
        text = _html_block_to_item_text(block)
        if text and re.match(r'\(?\d+\)', text):
            result.append(text)
    if result and not allow_plain_paragraph_items:
        return result
    if not allow_plain_paragraph_items:
        return result

    # Some new-format RT amendment HTML uses plain paragraph starts such as
    # <p>1) paragrahvi ...</p> instead of bold/strong item labels. Treat those
    # as item boundaries only when the paragraph starts with an unparenthesized
    # item marker followed by amendment-target vocabulary. Quoted replacement
    # payloads and subsection payloads are deliberately excluded.
    paragraph_blocks = re.findall(r"<p\b[^>]*>.*?</p>", html_cdata, flags=re.DOTALL | re.IGNORECASE)
    if not paragraph_blocks:
        return result

    item_start = re.compile(
        r"^\d+\)\s*("
        r"paragrahv(?:i|is|ist|ile|id)?|"
        r"lõige(?:t|test|tes|tele)?|"
        r"lõik(?:e|es|est|ele)?|"
        r"määrus(?:e|t|es|est|ele)?|"
        r"seadus(?:e|t|es|est|ele)?|"
        r"lisa(?:d|de|sid|le|ga|s|st)?|"
        r"§|"
        r"asendatakse|muudetakse|täiendatakse|tunnistatakse|lisatakse|jäetakse|sõnastatakse"
        r")\b",
        flags=re.IGNORECASE,
    )
    grouped_blocks: list[str] = []
    current: list[str] = []

    def _has_open_replacement_quote(text: str) -> bool:
        # Plain paragraph splitting is only a fallback for old RT HTML. If an
        # amendment item has opened a quoted replacement payload, numbered
        # paragraphs inside that payload are legal-unit/list content, not new
        # amendment item boundaries.
        open_quote = False
        for char in text:
            if char in {'"', "“", "„", "”"}:
                open_quote = not open_quote
        return open_quote

    for paragraph in paragraph_blocks:
        paragraph_text = _html_block_to_item_text(paragraph)
        current_text = _html_block_to_item_text("".join(current)) if current else ""
        starts_item = bool(item_start.match(paragraph_text)) and not _has_open_replacement_quote(current_text)
        if starts_item:
            if current:
                grouped_blocks.append("".join(current))
            current = [paragraph]
            continue
        if current:
            current.append(paragraph)
    if current:
        grouped_blocks.append("".join(current))

    fallback_result: list[str] = []
    for block in grouped_blocks:
        text = _html_block_to_item_text(block)
        if text and re.match(r"\d+\)", text):
            fallback_result.append(text)
    if result and fallback_result and len(fallback_result) == len(result):
        return [
            rich if len(rich) >= len(fallback) else fallback
            for rich, fallback in zip(result, fallback_result)
        ]
    if len(fallback_result) > 1 or not result:
        return fallback_result
    return result
