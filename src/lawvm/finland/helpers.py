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
from lawvm.core.regex_safety import compile_classifier_regex
import datetime as dt
from typing import List, Literal, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.roman import roman_to_arabic as _roman_to_arabic_shared

_NUM_TOKEN_STRIP_RE = re.compile(r"[)\s§.]")
# lawvm-regex: prefilter conservative penal heading-name evidence over normalized heading/intro text; classifier only
_RANGAISTUS_HEADING_NAME_RE = compile_classifier_regex(r"\b(rangaistus|rikos)\b", classifier_id="fi.helpers.rangaistus_heading_name_re")
_SECTION_SORT_RE = re.compile(r"^(\d+)([a-z]*)$")
_SECTION_SORT_NON_DIGIT_RE = re.compile(r"[^0-9]")
_PREVIOUS_ITEM_NUM_SUFFIX_RE = re.compile(r"^(\d+)([a-z]?)$", flags=re.I)
_PREVIOUS_ITEM_ALPHA_RE = re.compile(r"[a-z]", flags=re.I)
_SECTION_RANGE_SUFFIX_RE = re.compile(r"(\d+)([a-z])", flags=re.I)


@functools.lru_cache(maxsize=4096)
def _norm_num_token(text: str) -> str:
    """Normalise a section/chapter numeric token.

    Strips §, whitespace, parentheses, and trailing periods so that
    labels like ``"3 a §."`` and ``"3a"`` compare equal.
    """
    # Strip §, whitespace, parentheses, and trailing periods
    # (pre-1980s nums like "1 §.")
    token = _NUM_TOKEN_STRIP_RE.sub("", text).strip().lower()
    arabic = _roman_label_to_arabic(token)
    if arabic is not None:
        return arabic
    for suffix in ("luku", "osa", "osasto"):
        if token.endswith(suffix):
            prefix = token.removesuffix(suffix)
            prefix_arabic = _roman_label_to_arabic(prefix)
            if prefix_arabic is not None:
                return f"{prefix_arabic}{suffix}"
            prefixed = _roman_prefixed_label_to_arabic_suffix(prefix)
            if prefixed is not None:
                return f"{prefixed}{suffix}"
    prefixed = _roman_prefixed_label_to_arabic_suffix(token)
    if prefixed is not None:
        return prefixed
    return token


_SOURCE_SECTION_SIGN_SUFFIX_RE = re.compile(r"\s*§.*$")
_SOURCE_SECTION_SIGN_SUFFIX_LETTER_RE = re.compile(
    r"^\s{0,4}(?P<base>\d{1,4}\s{0,3}[a-z]?)\s{0,3}§\s{1,3}(?P<suffix>[a-z])\s{0,4}$",
    flags=re.I,
)


@functools.lru_cache(maxsize=8192)
def _normalize_source_section_num(raw: str) -> str:
    """Normalize a Finland source XML ``<section><num>`` label.

    Most modern source nums are shaped like ``"6 §"`` or ``"6 § Heading"``;
    those use the legacy policy of taking the text before the section sign.
    Some older XMLs instead encode the sign first, e.g. ``"§ 1."``.  For that
    source shape, stripping the suffix would erase the actual label, so fall
    back to the ordinary token normalizer.
    """
    stripped = raw.strip()
    if stripped.startswith("§"):
        return _norm_num_token(stripped)
    # lawvm-regex: prefilter source <num> label normalization (sign-first `§ 1.` shape); pure label-token shape, mints no legal state
    suffix_match = _SOURCE_SECTION_SIGN_SUFFIX_LETTER_RE.match(stripped)
    if suffix_match is not None:
        return _norm_num_token(f"{suffix_match.group('base')}{suffix_match.group('suffix')}")
    cleaned = _SOURCE_SECTION_SIGN_SUFFIX_RE.sub("", stripped).strip()
    return _norm_num_token(cleaned or stripped)


@functools.lru_cache(maxsize=8192)
def _normalize_source_part_num(raw: str) -> str:
    """Normalize a Finland source XML ``<part><num>`` label.

    Historical sources use both ``osa`` and ``osasto`` for part containers.
    Live-tree part labels are the bare structural label, so both suffixes are
    source syntax and must be stripped before scope comparison.
    """
    label = _norm_num_token(raw).removesuffix("osasto").removesuffix("osa")
    arabic = _roman_label_to_arabic(label.lower()) if label else None
    return str(arabic) if arabic is not None else label


@functools.lru_cache(maxsize=8192)
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


@functools.lru_cache(maxsize=8192)
def _section_sort_key(text: str) -> Tuple[int, str]:
    """Return a sort key for a Finnish section/chapter label string.

    ``"5a"`` → ``(5, "a")``, ``"10"`` → ``(10, "")``.
    Labels that cannot be parsed return ``(-1, token)``.
    """
    token = _norm_num_token(text).replace("luku", "").replace("osa", "")
    # lawvm-regex: prefilter numeric+letter split of a normalized label for sort ordering; pure label-token shape, mints no legal state
    m = _SECTION_SORT_RE.match(token)
    if m:
        return (int(m.group(1)), m.group(2))
    digits = _SECTION_SORT_NON_DIGIT_RE.sub("", token)
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


_RANGAISTUS_SENTENCING_RE = compile_classifier_regex(r"\bon tuomittava\b", re.I, classifier_id="fi.helpers.rangaistus_sentencing_re")
_RANGAISTUS_PENALTY_RE = compile_classifier_regex(r"\b(sakkoon|vankeuteen|elinkaudeksi)\b", re.I, classifier_id="fi.helpers.rangaistus_penalty_re")
_RANGAISTUS_OFFENCE_PREFIX_RE = compile_classifier_regex(r"^\s*(joka|jos)\b", re.I, classifier_id="fi.helpers.rangaistus_offence_prefix_re")
_RANGAISTUS_ADMIN_SANCTION_RE = compile_classifier_regex(r"\b(seuraamusmaksu|rikkomusmaksu|rikemaksu|laiminlyöntimaksu|myöhästymismaksu)\b", re.I, classifier_id="fi.helpers.rangaistus_admin_sanction_re")


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

    # lawvm-regex: prefilter conservative penal-shape evidence over a node's own normalized text; tri-state drafting-shape classifier, mints/drops no legal state
    has_sentencing_command = bool(_RANGAISTUS_SENTENCING_RE.search(text))
    # lawvm-regex: prefilter conservative penalty-expression evidence over the node's own normalized text; classifier only
    has_penalty_expression = bool(_RANGAISTUS_PENALTY_RE.search(text))
    # lawvm-regex: prefilter conservative offence-formula prefix evidence over the node's own normalized text; classifier only
    has_offence_formula = bool(_RANGAISTUS_OFFENCE_PREFIX_RE.match(text))
    # lawvm-regex: prefilter conservative offence-name evidence over the node's own heading/intro text; classifier only
    has_offence_name = bool(_RANGAISTUS_HEADING_NAME_RE.search(heading_text or intro_text))
    # lawvm-regex: prefilter conservative admin-sanction-term evidence over the node's own normalized text; classifier only
    has_admin_sanction_terms = bool(_RANGAISTUS_ADMIN_SANCTION_RE.search(text))
    has_colon_intro_list = _has_colon_intro_signal(node)

    if has_sentencing_command and has_penalty_expression and (has_offence_formula or has_offence_name):
        return "yes"

    if has_admin_sanction_terms:
        return "no"

    if has_colon_intro_list and not has_sentencing_command and not has_penalty_expression:
        return "no"

    return "unknown"


def may_attach_post_list_loppukappale(node: IRNode) -> bool:
    """Return True only when the provision is clearly a rangaistussäännös."""
    return classify_rangaistussaannos(node) == "yes"


# A criminal-sentencing closing clause begins with the sentencing predicate
# (``on tuomittava`` / ``tuomitaan``) and carries a penalty expression.  Because
# the offender subject is supplied by the preceding offence frame ("Joka ..."),
# the clause cannot grammatically stand as an independent momentti: it is the
# loppukappale of the offence provision.  A common drafting variant interjects a
# qualifying clause between ``on`` and ``tuomittava`` ("on, jollei ... ankarampaa
# rangaistusta, tuomittava ...").  Allow that bounded interjection but anchor on
# the clause opening so an unrelated sentence that merely contains
# "on ... tuomittava" is not matched.
_PENAL_SENTENCING_LEADIN_RE = compile_classifier_regex(
    r"^\s*on\b[^.]{0,200}?\btuomittava\b|^\s*tuomitaan\b",
    re.I,
    classifier_id="fi.helpers.penal_sentencing_leadin_re",
)

# The rangaistussäännös offence frame is marked by the culpability formula: an
# offender subject ("Joka ..." or a named subject) qualified by ", joka tahallaan
# tai (törkeästä) huolimattomuudesta".  Either a leading "Joka"/"Jos" or the
# embedded culpability clause identifies the frame.
_PENAL_CULPABILITY_FORMULA_RE = compile_classifier_regex(
    r"\bjoka\s+tahallaan\b",
    re.I,
    classifier_id="fi.helpers.penal_culpability_formula_re",
)


def is_penal_offence_frame_without_sentencing(node: IRNode) -> bool:
    """Return True for a penal offence frame whose sentencing clause is missing.

    Matches a rangaistussäännös offence frame that opens with the offender
    formula ("Joka ..." / "Jos ...") and a numbered kohta list, but does NOT yet
    contain the sentencing command.  Such a frame is grammatically incomplete:
    the sentencing predicate ("on tuomittava ... sakkoon/vankeuteen") lives in a
    following sibling that must be folded back as the frame's loppukappale.
    """
    has_offence_intro = False
    for child in node.children:
        if child.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT):
            intro_text = _normalized_node_text(child)
            intro_lower = intro_text.lower()
            if intro_text and (
                # lawvm-regex: prefilter offence-formula prefix over the frame's own intro text; classifier only, mints no legal state
                _RANGAISTUS_OFFENCE_PREFIX_RE.match(intro_lower)
                # lawvm-regex: prefilter embedded culpability formula (named-subject offence frame); classifier only
                or _PENAL_CULPABILITY_FORMULA_RE.search(intro_lower)
            ):
                has_offence_intro = True
            break
    if not has_offence_intro:
        return False

    has_numbered_kohta = any(
        child.kind == IRNodeKind.PARAGRAPH
        and any(gc.kind is IRNodeKind.NUM for gc in child.children)
        for child in node.children
    )
    if not has_numbered_kohta:
        return False

    text = _normalized_node_text(node).lower()
    # lawvm-regex: prefilter sentencing-command presence over the frame's own text; classifier only
    if _RANGAISTUS_SENTENCING_RE.search(text):
        return False
    return True


def is_penal_sentencing_closing_clause(node: IRNode) -> bool:
    """Return True for a content-only sentencing closing clause (loppukappale).

    The clause begins with the sentencing predicate ("on tuomittava" /
    "tuomitaan") and carries a penalty expression.  It is the grammatical
    continuation of a preceding offence frame, not an independent momentti.
    """
    children = tuple(node.children)
    if not children:
        return False
    if any(
        child.kind not in (IRNodeKind.CONTENT,)
        for child in children
    ):
        return False
    text = _normalized_node_text(node)
    # lawvm-regex: prefilter sentencing-clause lead-in over the clause's own text; classifier only
    if not _PENAL_SENTENCING_LEADIN_RE.match(text):
        return False
    # lawvm-regex: prefilter penalty expression over the clause's own text; classifier only
    if not _RANGAISTUS_PENALTY_RE.search(text):
        return False
    return True


# A flat penal block keeps the sentencing clause's qualifiers and further
# offence frames as their own momentit rather than folding the closing clause.
# The tell is a sibling that *continues the penal provision*: a "Jollei ..."
# severity qualifier or another offender frame ("Se, joka ..." / "Joka ...").
_PENAL_BLOCK_CONTINUATION_RE = compile_classifier_regex(
    r"^\s*jollei\b|^\s*se,\s*joka\b|^\s*joka\b",
    re.I,
    classifier_id="fi.helpers.penal_block_continuation_re",
)


def continues_penal_block(node: IRNode) -> bool:
    """Return True when a sibling continues a flat penal block.

    Such a sibling ("Jollei ...", "Se, joka ...", "Joka ...") qualifies the
    sentencing or opens a further offence frame, signalling that Finlex keeps the
    whole penal provision flat — so the closing sentencing clause must not be
    folded into the first offence frame.
    """
    text = _normalized_node_text(node)
    # lawvm-regex: prefilter penal-block continuation lead-in over the sibling's own text; classifier only
    return bool(_PENAL_BLOCK_CONTINUATION_RE.match(text))


def _previous_item_token(item_norm: str) -> Optional[str]:
    """Return the label that immediately precedes *item_norm* in Finnish item sequences.

    Examples: ``"3"`` → ``"2"``, ``"3a"`` → ``"3"``, ``"3b"`` → ``"3a"``, ``"c"`` → ``"b"``.
    Returns ``None`` when there is no predecessor (base=1, no suffix, or ``"a"``).
    """
    # lawvm-regex: prefilter predecessor-label split of a normalized item label; pure label-sequence shape, mints no legal state
    m = _PREVIOUS_ITEM_NUM_SUFFIX_RE.match(item_norm)
    if m:
        base = int(m.group(1))
        suffix = m.group(2).lower()
        if suffix:
            if suffix == 'a':
                return str(base)
            return f"{base}{chr(ord(suffix) - 1)}"
        if base <= 1:
            return None
        return str(base - 1)
    if _PREVIOUS_ITEM_ALPHA_RE.fullmatch(item_norm):
        letter = item_norm.lower()
        if letter == 'a':
            return None
        return chr(ord(letter) - 1)
    return None


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
    # lawvm-regex: prefilter I/V/X charset gate before delegating the parse to lawvm.roman; pure label-token shape, mints no legal state
    if not token or not _FI_ROMAN_RE.match(token):
        return None
    value = _roman_to_arabic_shared(token)
    return None if value is None else str(value)


@functools.lru_cache(maxsize=8192)
def _roman_prefixed_label_to_arabic_suffix(token: str) -> Optional[str]:
    """Normalize compact Roman-prefix labels such as ``iva`` to ``4a``."""
    if not token:
        return None
    for split_at in range(len(token) - 1, 0, -1):
        prefix = token[:split_at]
        suffix = token[split_at:]
        if not suffix.isalpha():
            continue
        prefix_arabic = _roman_label_to_arabic(prefix)
        if prefix_arabic is not None:
            return f"{prefix_arabic}{suffix}"
    return None


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
    # Strip trailing punctuation FIRST — old and noisy source labels include
    # forms such as "3 luku.", "10 §:", and "24 §*".  The punctuation is not a
    # legal coordinate.  Only do this for structural containers, not paragraphs
    # or subsections, where "1." and "1" can be distinct source surfaces.
    if tag in ("section", "chapter", "part"):
        norm = norm.rstrip(".,:*")
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


@functools.lru_cache(maxsize=8192)
def _expand_section_range_tuple(section: str) -> tuple[str, ...]:
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
                return tuple(str(i) for i in range(int(start), int(end) + 1))
            m_start = _SECTION_RANGE_SUFFIX_RE.fullmatch(start)
            m_end = _SECTION_RANGE_SUFFIX_RE.fullmatch(end)
            if m_start and m_end and m_start.group(1) == m_end.group(1):
                base = m_start.group(1)
                s_c = m_start.group(2).lower()
                e_c = m_end.group(2).lower()
                if ord(s_c) <= ord(e_c):
                    return tuple(f"{base}{chr(c)}" for c in range(ord(s_c), ord(e_c) + 1))
            if m_start and end.isdigit():
                s_n = int(m_start.group(1))
                e_n = int(end)
                if s_n < e_n:
                    return (f"{s_n}{m_start.group(2).lower()}",) + tuple(
                        str(i) for i in range(s_n + 1, e_n + 1)
                    )
            break
    return (section,)


def _expand_section_range(section: str) -> List[str]:
    """Expand a Finnish section range like ``'12―14'`` → ``['12', '13', '14']``."""
    return list(_expand_section_range_tuple(section))
