"""Shared pure utility functions for the Finnish law processing pipeline.

All functions here are stateless, have no grafter-state dependency, and
import only from the standard library or lawvm.core.ir.  They are collected
here so that normalize.py, apply.py, and other modules can import them without
creating circular dependencies back into grafter.py.

Grafter.py re-exports all of these under its own namespace for backward
compatibility.
"""
from __future__ import annotations

import functools
import re
import datetime as dt
from typing import List, Literal, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.roman import roman_to_arabic as _roman_to_arabic_shared


@functools.lru_cache(maxsize=4096)
def _norm_num_token(text: str) -> str:
    """Normalise a section/chapter numeric token.

    Strips §, whitespace, parentheses, and trailing periods so that
    labels like ``"3 a §."`` and ``"3a"`` compare equal.
    """
    # Strip §, whitespace, parentheses, and trailing periods
    # (pre-1980s nums like "1 §.")
    token = re.sub(r'[)\s§.]', '', text).strip().lower()
    arabic = _roman_label_to_arabic(token)
    if arabic is not None:
        return arabic
    for suffix in ("luku", "osa", "osasto"):
        if token.endswith(suffix):
            prefix = token.removesuffix(suffix)
            prefix_arabic = _roman_label_to_arabic(prefix)
            if prefix_arabic is not None:
                return f"{prefix_arabic}{suffix}"
    return token


def _norm_row_anchor_text(text: str) -> str:
    """Normalize Finland table-row anchor text for replay matching."""
    cleaned = text.lower().replace("\xa0", " ")
    cleaned = re.sub(r"[(),.:;§]", " ", cleaned)
    cleaned = re.sub(
        r"\b("
        r"käräjäoikeu[a-zäöå]*|"
        r"kohta[a-zäöå]*|"
        r"koskev[a-zäöå]*|"
        r"osalt[a-zäöå]*|"
        r"seuraav[a-zäöå]*"
        r")\b",
        " ",
        cleaned,
        flags=re.I,
    )
    return " ".join(cleaned.split())


def _section_sort_key(text: str) -> Tuple[int, str]:
    """Return a sort key for a Finnish section/chapter label string.

    ``"5a"`` → ``(5, "a")``, ``"10"`` → ``(10, "")``.
    Labels that cannot be parsed return ``(-1, token)``.
    """
    token = _norm_num_token(text).replace("luku", "").replace("osa", "")
    m = re.match(r'^(\d+)([a-z]*)$', token)
    if m:
        return (int(m.group(1)), m.group(2))
    digits = re.sub(r'[^0-9]', '', token)
    return (int(digits), '') if digits else (-1, token)


def _is_omission_ir(node: IRNode) -> bool:
    """Return True for omission-marker nodes.

    A node is an omission marker when its ``kind`` is ``'omission'``, when it
    is an ``hcontainer`` with ``name='omission'``, or when it is a ``p``
    element with ``class='omission'`` (alternate encoding used in some older
    Finnish amendment XMLs).
    """
    if node.kind is IRNodeKind.OMISSION:
        return True
    if node.kind is IRNodeKind.HCONTAINER and node.attrs.get('name') == 'omission':
        return True
    if node.kind is IRNodeKind.P and node.attrs.get('class') == 'omission':
        return True
    return False


_RANGAISTUS_SENTENCING_RE = re.compile(r"\bon tuomittava\b", re.I)
_RANGAISTUS_PENALTY_RE = re.compile(r"\b(sakkoon|vankeuteen|elinkaudeksi)\b", re.I)
_RANGAISTUS_OFFENCE_PREFIX_RE = re.compile(r"^\s*(joka|jos)\b", re.I)
_RANGAISTUS_ADMIN_SANCTION_RE = re.compile(
    r"\b(seuraamusmaksu|rikkomusmaksu|rikemaksu|laiminlyöntimaksu|myöhästymismaksu)\b",
    re.I,
)


def _normalized_node_text(node: IRNode) -> str:
    """Return normalized descendant text for conservative shape classification."""
    return " ".join(irnode_to_text(node).split())


def _direct_child_text(node: IRNode, kinds: Tuple[IRNodeKind, ...]) -> str:
    parts: List[str] = []
    for child in node.children:
        if child.kind in kinds:
            text = _normalized_node_text(child)
            if text:
                parts.append(text)
    return " ".join(parts)


def _has_colon_intro_signal(node: IRNode) -> bool:
    """Return True when a node exposes the ordinary Finnish list-intro signal."""
    for child in node.children:
        if child.kind not in (IRNodeKind.INTRO, IRNodeKind.CONTENT):
            continue
        text = _normalized_node_text(child)
        if not text:
            continue
        if text.endswith(":"):
            return True
    return False


def classify_rangaistussaannos(node: IRNode) -> Literal["yes", "no", "unknown"]:
    """Classify whether a provision has the criminal-penalty drafting shape.

    The classifier is conservative: only clear penal-shape evidence yields
    ``"yes"``.  Ordinary list provisions with a colon-intro and no sentencing
    command are ``"no"``.  Weak or conflicting evidence stays ``"unknown"``.
    """
    text = _normalized_node_text(node).lower()
    heading_text = _direct_child_text(node, (IRNodeKind.HEADING,)).lower()
    intro_text = _direct_child_text(node, (IRNodeKind.INTRO, IRNodeKind.CONTENT)).lower()

    has_sentencing_command = bool(_RANGAISTUS_SENTENCING_RE.search(text))
    has_penalty_expression = bool(_RANGAISTUS_PENALTY_RE.search(text))
    has_offence_formula = bool(_RANGAISTUS_OFFENCE_PREFIX_RE.match(text))
    has_offence_name = bool(re.search(r"\b(rangaistus|rikos)\b", heading_text or intro_text))
    has_admin_sanction_terms = bool(_RANGAISTUS_ADMIN_SANCTION_RE.search(text))
    has_colon_intro_list = _has_colon_intro_signal(node)

    if has_admin_sanction_terms:
        return "no"

    if has_sentencing_command and has_penalty_expression and (has_offence_formula or has_offence_name):
        return "yes"

    if has_colon_intro_list and not has_sentencing_command and not has_penalty_expression:
        return "no"

    return "unknown"


def may_attach_post_list_loppukappale(node: IRNode) -> bool:
    """Return True only when the provision is clearly a rangaistussäännös."""
    return classify_rangaistussaannos(node) == "yes"


def _previous_item_token(item_norm: str) -> Optional[str]:
    """Return the label that immediately precedes *item_norm* in Finnish item sequences.

    Examples: ``"3"`` → ``"2"``, ``"3a"`` → ``"3"``, ``"3b"`` → ``"3a"``.
    Returns ``None`` when there is no predecessor (base=1, no suffix).
    """
    m = re.match(r'^(\d+)([a-z]?)$', item_norm, flags=re.I)
    if not m:
        return None
    base = int(m.group(1))
    suffix = m.group(2).lower()
    if suffix:
        if suffix == 'a':
            return str(base)
        return f"{base}{chr(ord(suffix) - 1)}"
    if base <= 1:
        return None
    return str(base - 1)


def _parse_iso_date(value: Optional[str]) -> Optional[dt.date]:
    """Parse an ISO-8601 date string, returning ``None`` on failure."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


_FI_ROMAN_RE = re.compile(r"^[ivxIVX]+$")


def _roman_label_to_arabic(token: str) -> Optional[str]:
    """Return the Arabic-string form of a Finnish I/V/X Roman label, or None.

    Used by the structural-label normalizers below.  Conservatively gated
    to I/V/X characters (the surface that appears in Finnish chapters and
    parts) and delegates the actual parse — including non-canonical
    rejection — to ``lawvm.roman``.
    """
    if not token or not _FI_ROMAN_RE.match(token):
        return None
    value = _roman_to_arabic_shared(token)
    return None if value is None else str(value)


def _fi_label_postprocessor(tag: str, norm: str) -> str:
    """Strip Finnish structural keyword suffixes from normalised AKN label text.

    Finnish AKN XML encodes the structural keyword directly inside the <num>
    element: chapter 3 is ``<num>3 luku</num>``, part 2 is ``<num>2 osa</num>``.
    After ``_norm_num`` collapses whitespace and strips punctuation, these become
    ``"3luku"`` and ``"2osa"``.  The suffixes must be removed so that timeline
    addresses use only the numeric label (``"3"``, ``"2"``).

    Roman numeral chapter/part labels (e.g. ``"I luku"`` → ``"iluku"``) are
    converted to Arabic after suffix stripping so that they match oracle labels
    which always use Arabic numerals.

    Also strips trailing dots from old-format statutes where sections are
    numbered ``1.``, ``2.`` instead of ``1 §``, ``2 §``.

    Passed as ``label_postprocessor`` to ``xml_to_ir_node`` for all Finnish XML.
    """
    # Strip trailing dots FIRST — old-format statutes use "3 luku." whose
    # normalised form is "3luku.".  removesuffix("luku") fails on "3luku."
    # because the string ends with "." not "luku", so the dot must come off
    # before the structural keyword suffix is removed.
    # Only for sections, chapters, parts — not paragraphs/subsections (risk of
    # creating duplicate labels when "1." and "1" coexist).
    if norm.endswith(".") and tag in ("section", "chapter", "part"):
        norm = norm.rstrip(".")
    if tag == "chapter":
        norm = norm.removesuffix("luku")
    elif tag == "part":
        # Strip "osasto" BEFORE "osa" — removesuffix("osa") does not match
        # "1osasto" because the string ends with "sto", not "osa".
        norm = norm.removesuffix("osasto")
        norm = norm.removesuffix("osa")
    # Convert pure Roman numeral labels to Arabic for chapter and part nodes.
    # Only convert when the entire label after suffix stripping is a known
    # Roman numeral — labels like "3a" or "12" are left untouched.
    if tag in ("chapter", "part"):
        arabic = _roman_label_to_arabic(norm)
        if arabic is not None:
            norm = arabic
    return norm


def _expand_section_range(section: str) -> List[str]:
    """Expand a Finnish section range like ``'12―14'`` → ``['12', '13', '14']``.

    Handles horizontal bar (―), em-dash (—), en-dash (–) and ASCII hyphen (-)
    as range separators.

    Supports:
    - pure numeric ranges: ``12-14`` → ``12, 13, 14``
    - same-base letter ranges: ``12a-12d`` → ``12a, 12b, 12c, 12d``
    - alpha-start to later plain-number end: ``52a-55`` → ``52a, 53, 54, 55``

    Other mixed ranges are returned unchanged as ``[section]``.
    """
    for dash in ('\u2015', '\u2014', '\u2013', '-'):  # ―, —, –, -
        if dash in section:
            parts = section.split(dash, 1)
            start, end = parts[0].strip(), parts[1].strip()
            if start.isdigit() and end.isdigit():
                return [str(i) for i in range(int(start), int(end) + 1)]
            m_start = re.fullmatch(r"(\d+)([a-z])", start, re.IGNORECASE)
            m_end = re.fullmatch(r"(\d+)([a-z])", end, re.IGNORECASE)
            if m_start and m_end and m_start.group(1) == m_end.group(1):
                base = m_start.group(1)
                s_c = m_start.group(2).lower()
                e_c = m_end.group(2).lower()
                if ord(s_c) <= ord(e_c):
                    return [f"{base}{chr(c)}" for c in range(ord(s_c), ord(e_c) + 1)]
            if m_start and end.isdigit():
                s_n = int(m_start.group(1))
                e_n = int(end)
                if s_n < e_n:
                    return [f"{s_n}{m_start.group(2).lower()}"] + [str(i) for i in range(s_n + 1, e_n + 1)]
            break
    return [section]
