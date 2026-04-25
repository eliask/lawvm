"""Finnish legal label algebra for LawVM.

This module provides typed, frozen label objects for every label surface
encountered in Finnish statutes, together with parsing, rendering,
comparison/sorting, and validation helpers.

Normative source: PRO_FINLAND_ONTOLOGY_AND_PROFILE.md, sections 1.6-1.7.

Design invariants
-----------------
- Labels are frozen dataclasses.  No mutable state.
- ``parse_label`` is permissive on input, strict on output.
- ``render_label`` produces one canonical Finnish display form per kind.
- Raw surface stays in provenance; canonical display is derived.
- ``InsertableArabic(1, "a")`` as item != ``AlphaSequence("a")`` as subitem.

Usage
-----
    from lawvm.finland.labels import parse_label, render_label, label_sort_key
    lbl = parse_label("10 a luku.", "chapter")
    assert render_label(lbl, "chapter") == "10 a luku"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple, Union

from lawvm.roman import roman_to_arabic as _roman_to_arabic_shared


# ---------------------------------------------------------------------------
# Roman numeral helper
# ---------------------------------------------------------------------------
#
# Finnish structural labels use I-X-class numerals (chapters, parts) and
# never the L/C/D/M range in practice.  ``_ROMAN_RE`` keeps that
# conservative gate; conversion itself delegates to the shared parser in
# ``lawvm.roman`` which rejects non-canonical spellings.

_ROMAN_RE = re.compile(r"^[IiVvXx]+$")


def roman_to_arabic(token: str) -> int | None:
    """Convert a Finnish Roman numeral label token to its integer value.

    Restricted to I/V/X characters (the range that appears in Finnish
    chapter and part labels).  Non-canonical spellings such as ``IIII``
    are rejected by the shared ``lawvm.roman`` parser.
    """
    if not isinstance(token, str) or not _ROMAN_RE.match(token):
        return None
    return _roman_to_arabic_shared(token)


# ---------------------------------------------------------------------------
# Label types (frozen dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinlandLabel:
    """Base class for Finnish legal labels.  Not instantiated directly."""


@dataclass(frozen=True)
class InsertableArabic(FinlandLabel):
    """Arabic numeral with optional alphabetic insertion suffix.

    Examples: ``5 ``, ``10 a``, ``23 a``, ``50 a``.
    Suffix is lowercase, empty string for unsuffixed labels.
    """
    base: int
    suffix: str = ""


@dataclass(frozen=True)
class RomanOrdinal(FinlandLabel):
    """Roman numeral label.

    ``value`` is the numeric value; ``token`` preserves the original case
    (``"IV"``, ``"iv"``).
    """
    value: int
    token: str


@dataclass(frozen=True)
class AlphaSequence(FinlandLabel):
    """Alphabetic sequence label (single or compound).

    Examples: ``"a"``, ``"b"``, ``"aa"``, ``"ab"``, ``"ba"``.
    Token is always lowercase.
    """
    token: str


@dataclass(frozen=True)
class ImplicitOrdinal(FinlandLabel):
    """Positional ordinal label for units without explicit printed labels.

    Used for ``momentti`` when not explicitly numbered in source text.
    ``index`` is 1-based.
    """
    index: int


@dataclass(frozen=True)
class SymbolicLabel(FinlandLabel):
    """Symbolic label for supplements and other special containers.

    Examples: ``"A"``, ``"B"``, ``"C"``.
    Token preserves original case.
    """
    token: str


# ---------------------------------------------------------------------------
# Type alias for any Finland label
# ---------------------------------------------------------------------------

AnyFinlandLabel = Union[
    InsertableArabic,
    RomanOrdinal,
    AlphaSequence,
    ImplicitOrdinal,
    SymbolicLabel,
]


# ---------------------------------------------------------------------------
# Label series per unit kind (from the Pro spec)
# ---------------------------------------------------------------------------

# Mapping from unit kind to the primary and allowed label types.
# Used by is_valid_label_for_kind.

_LABEL_SERIES: dict[str, tuple[type, ...]] = {
    "statute": (),
    "supplement": (SymbolicLabel, RomanOrdinal, InsertableArabic),
    "part": (RomanOrdinal, InsertableArabic),
    "division": (RomanOrdinal, InsertableArabic, SymbolicLabel),
    "chapter": (InsertableArabic,),
    "subdivision": (InsertableArabic, SymbolicLabel),
    "section": (InsertableArabic,),
    "subsection": (InsertableArabic, ImplicitOrdinal),
    "item": (InsertableArabic, AlphaSequence),
    "subitem": (AlphaSequence, RomanOrdinal),
}


# ---------------------------------------------------------------------------
# Raw label normalization
# ---------------------------------------------------------------------------

# Structural keywords that appear inside Finnish <num> elements
_STRUCTURAL_SUFFIXES = {
    "chapter": ("luku",),
    "part": ("osasto", "osa"),  # osasto before osa: "osasto" won't match removesuffix("osa")
    "division": ("osasto",),
}

# Regex: parenthesized suffix like ")" at end
_TRAILING_PAREN_RE = re.compile(r"\)$")
# Regex: leading "§" or trailing "§" with optional dot
_SECTION_SIGN_RE = re.compile(r"§\.?")
# Regex: trailing dot (old-format "3.")
_TRAILING_DOT_RE = re.compile(r"\.$")
# Regex: detect Arabic + optional suffix pattern.
# Per Finnish drafting rules, section/chapter suffixes are Latin a–z only
# (confirmed by corpus sample and Finlex Lainkirjoittaja guide); ä/ö/å
# never appear as structural suffixes so we tighten to [a-zA-Z].
_ARABIC_SUFFIX_RE = re.compile(
    r"^(\d+)\s*([a-zA-Z]?)$"
)
# Regex: detect compound alpha sequences (e.g. "aa", "ab" for very long lists)
_ALPHA_SEQ_RE = re.compile(r"^([a-z]+)$")
# Regex: detect uppercase single letter (symbolic)
_SYMBOLIC_RE = re.compile(r"^([A-Z])$")


def normalize_raw_label(raw: str, tag: str) -> str:
    """Normalize a raw Finnish label string for a given unit kind *tag*.

    Replaces the logic of ``_fi_label_postprocessor`` and
    ``_norm_num_token`` with a unified, kind-aware normalization.

    The returned string is suitable for further parsing by ``parse_label``.
    """
    # Step 1: strip leading/trailing whitespace
    s = raw.strip()

    # Step 2: strip § sign (with optional dot) — sections
    s = _SECTION_SIGN_RE.sub("", s).strip()

    # Step 3: strip trailing parenthesis — items/subitems
    s = _TRAILING_PAREN_RE.sub("", s).strip()

    # Step 4: strip trailing dot — old format "3." for sections/chapters/parts
    if tag in ("section", "chapter", "part"):
        s = _TRAILING_DOT_RE.sub("", s).strip()

    # Step 5: strip structural keyword suffixes
    s_lower = s.lower()
    for suffix_tag, suffixes in _STRUCTURAL_SUFFIXES.items():
        if tag == suffix_tag or (tag == "part" and suffix_tag == "division"):
            for suffix in suffixes:
                if s_lower.endswith(suffix):
                    s = s[:len(s) - len(suffix)].strip()
                    s_lower = s.lower()
                    break

    # Step 6: handle "OSASTO VII" / "II A OSA" style prefix keywords
    for prefix in ("osasto", "osa"):
        if s_lower.startswith(prefix + " "):
            s = s[len(prefix):].strip()
            s_lower = s.lower()
        elif s_lower.startswith(prefix):
            remainder = s[len(prefix):]
            if remainder and not remainder[0].isalnum():
                s = remainder.strip()
                s_lower = s.lower()

    return s


def parse_label(raw: str, unit_kind: str) -> AnyFinlandLabel:
    """Parse a raw Finnish label string into a typed ``FinlandLabel``.

    This is context-sensitive: the same raw string may parse differently
    depending on the ``unit_kind``.

    Parameters
    ----------
    raw : str
        The raw label text, e.g. ``"10 a luku."``, ``"13.§"``, ``"aa)"``.
    unit_kind : str
        The unit kind context: ``"chapter"``, ``"section"``, ``"item"``, etc.

    Returns
    -------
    AnyFinlandLabel
        A frozen, typed label object.

    Raises
    ------
    ValueError
        If the raw string cannot be parsed as any known label form.
    """
    norm = normalize_raw_label(raw, unit_kind)

    if not norm:
        raise ValueError(f"Empty label after normalization: raw={raw!r}, kind={unit_kind!r}")

    # Try implicit ordinal first (only for subsection with pure digit)
    if unit_kind == "subsection" and norm.isdigit():
        return InsertableArabic(base=int(norm), suffix="")

    # Try Arabic + optional suffix: "10", "10 a", "10a", "50 a"
    # Allow space between number and suffix
    m = re.match(r"^(\d+)\s*([a-z]?)$", norm, re.IGNORECASE)
    if m:
        base = int(m.group(1))
        suffix = m.group(2).lower()
        # For subitem context, a single digit+letter like "1a" could be
        # InsertableArabic. But a bare single letter in subitem context
        # should be AlphaSequence (handled below).
        return InsertableArabic(base=base, suffix=suffix)

    # Try Roman numeral with optional trailing token: "II A", "V", "IV"
    # (for part, division, supplement, subitem)
    norm_lower = norm.lower()
    # Check for "ROMAN LETTER" pattern (e.g. "II A" for part numbering)
    multi_match = re.match(r"^([IiVvXx]+)\s+([A-Za-z]+)$", norm)
    if multi_match:
        roman_part = multi_match.group(1)
        if roman_to_arabic(roman_part) is not None:
            # Roman + symbolic suffix: return as SymbolicLabel with full token
            return SymbolicLabel(token=norm.upper())

    roman_value = roman_to_arabic(norm)
    if roman_value is not None:
        return RomanOrdinal(
            value=roman_value,
            token=norm,
        )

    # Try compound alpha sequence: "aa", "ab", "ba", etc.
    # Also single alpha "a", "b" for subitem context
    if _ALPHA_SEQ_RE.match(norm_lower):
        # For item context, a single letter could be AlphaSequence
        # (when host uses lettered points) — but default to InsertableArabic
        # is handled above for digit+letter. Pure alpha goes to AlphaSequence.
        if unit_kind in ("subitem", "item"):
            return AlphaSequence(token=norm_lower)
        # For other contexts, a single letter is symbolic
        if len(norm) == 1 and norm.isupper():
            return SymbolicLabel(token=norm)
        # Lowercase single letter in non-subitem context: still alpha sequence
        return AlphaSequence(token=norm_lower)

    # Try symbolic: single uppercase letter for supplement/division
    if _SYMBOLIC_RE.match(norm):
        return SymbolicLabel(token=norm)

    raise ValueError(
        f"Cannot parse label: raw={raw!r}, normalized={norm!r}, kind={unit_kind!r}"
    )


# ---------------------------------------------------------------------------
# Label rendering
# ---------------------------------------------------------------------------

def render_label(label: AnyFinlandLabel, unit_kind: str) -> str:
    """Render a typed label as canonical Finnish display text.

    Parameters
    ----------
    label : AnyFinlandLabel
        The typed label to render.
    unit_kind : str
        The unit kind context for rendering decorations.

    Returns
    -------
    str
        The canonical Finnish display string.
    """
    if isinstance(label, InsertableArabic):
        base_str = str(label.base)
        suffix_part = f" {label.suffix}" if label.suffix else ""
        core = f"{base_str}{suffix_part}"

        if unit_kind == "chapter":
            return f"{core} luku"
        if unit_kind == "section":
            return f"{core} \u00a7"  # § sign
        if unit_kind in ("item", "subitem"):
            return f"{core})"
        if unit_kind == "part":
            return f"{core} osa"
        if unit_kind == "division":
            return f"{core} osasto"
        if unit_kind == "subsection":
            return f"{core} momentti"
        if unit_kind == "subdivision":
            return f"{core} jakso"
        if unit_kind == "supplement":
            return core
        return core

    if isinstance(label, RomanOrdinal):
        token = label.token.upper()
        if unit_kind == "part":
            return f"{token} osa"
        if unit_kind == "division":
            return f"{token} osasto"
        if unit_kind == "supplement":
            return token
        if unit_kind in ("subitem",):
            # Nested subitem: lowercase roman with paren
            return f"{label.token.lower()})"
        return token

    if isinstance(label, AlphaSequence):
        if unit_kind in ("subitem", "item"):
            return f"{label.token})"
        return label.token

    if isinstance(label, ImplicitOrdinal):
        if unit_kind == "subsection":
            return f"{label.index} momentti"
        return str(label.index)

    if isinstance(label, SymbolicLabel):
        return label.token

    return str(label)  # pragma: no cover


# ---------------------------------------------------------------------------
# Label comparison and sorting
# ---------------------------------------------------------------------------

def label_sort_key(label: AnyFinlandLabel) -> Tuple[int, int, str]:
    """Return a sort key for ordering labels of the same unit kind.

    The tuple ``(category, numeric_value, suffix_or_token)`` ensures:
    - Numeric labels sort by number, then by suffix alphabetically.
    - Roman labels sort by numeric value.
    - Alpha labels sort lexicographically.
    - Symbolic labels sort by token.
    - Implicit ordinals sort by index.

    Categories: 0=numeric/insertable, 1=roman, 2=alpha, 3=symbolic, 4=implicit.
    """
    if isinstance(label, InsertableArabic):
        return (0, label.base, label.suffix)
    if isinstance(label, RomanOrdinal):
        return (1, label.value, label.token.lower())
    if isinstance(label, AlphaSequence):
        return (2, _alpha_sort_value(label.token), label.token)
    if isinstance(label, SymbolicLabel):
        return (3, ord(label.token[0]) if label.token else 0, label.token)
    if isinstance(label, ImplicitOrdinal):
        return (4, label.index, "")
    return (99, 0, "")  # pragma: no cover


def _alpha_sort_value(token: str) -> int:
    """Compute a numeric sort value for an alphabetic sequence token.

    Single letters: a=1, b=2, ..., z=26.
    Compound letters: aa=27, ab=28, ..., az=52, ba=53, ...
    """
    if not token:
        return 0
    value = 0
    for ch in token:
        value = value * 26 + (ord(ch) - ord("a") + 1)
    return value


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def is_valid_label_for_kind(label: AnyFinlandLabel, unit_kind: str) -> bool:
    """Return True if *label* is a valid label type for *unit_kind*.

    Checks against the per-kind label series defined in the ontology spec.
    """
    allowed = _LABEL_SERIES.get(unit_kind)
    if allowed is None:
        return False
    if not allowed:
        # No label series defined (e.g. statute) — no label expected
        return False
    return type(label) in allowed
