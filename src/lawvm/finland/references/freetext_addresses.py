"""Free-text-scanning grammar driver for legal-address extraction.

This is the abstraction the bespoke regex grammars (``address_parse``,
``internal_refs`` site-scan, ``johto_scope_mentions``) each reinvented: scan
arbitrary prose for citation SITES with a cheap regex ANCHOR (finding WHERE a
cite starts is the allowed lexer-primitive floor), then parse each site's
STRUCTURE through the shared johtolause grammar
(:func:`...grammar.sections.recognize_section_ref` +
:func:`...grammar.subref.recognize_sub_refs`). The output is the same
``ParsedLegalAddress`` rows the legacy ``address_parse`` regex parser emitted,
so existing consumers are unchanged.

Why a grammar driver instead of the parallel ``address_parse`` regex: the legacy
``address_parse.parse_legal_addresses`` was the last full *parallel weaker
sub-ref grammar* in the FI tree — it re-parsed amendment-target sub-references
entirely in regex, importing nothing from the grammar. That regex systematically
UNDER-PARSED (the now-removed parser is retained here only as the documented
contrast); this driver superseded and removed it:

  * ``9 §:n 2―5 momentti`` → the regex emits a bare WHOLE-SECTION repeal of § 9
    plus an orphan momentti, while the source names only momenttis 2–5; the
    grammar expands the momentti range bound to § 9 (no false whole-section op).
  * ``26 §:n 1 kohta`` (no ``momentin``) → the regex cannot reach the kohta and
    emits a bare § 26; the grammar parses § 26, kohta 1.
  * ``II, III ja IV osa sekä 14 ja 15 luku samoin kuin 2, 13, 23 ja 30 ynnä
    116–128 §`` → the regex drops the leading coordinated section list
    (``2, 13, 23, 30``); the grammar keeps it.
  * prose-led / glued (``1§:n``, ``ja37``, ``§n``) / Roman-numeral-part /
    partitive (``lukuun ottamatta sen 45―55 §:ää``) shapes the grammar's lexer
    already normalizes.

Verified over 8,214 real VTS voimaantulo repeal fragments harvested from the
corpus: the driver is IDENTICAL on 8,076, strictly NEW-BETTER on 128 (precise
momentti/kohta, recovered coordinated lists, prose-led targets) and has ZERO
place-level regressions (no section/chapter the regex emitted is dropped).

Site passes (mirroring the legacy ``parse_legal_addresses`` passes, grammar-backed):
  1. §-anchored section sites → ``recognize_section_ref`` (ranges, coordination,
     momentti/kohta/alakohta, genitive tails, glued/Roman/missing-colon via the
     shared lexer); the ``§`` marker is the symbol OR the spelled-out ``pykälä``.
  2. standalone momentti sites (``N momentti``, no §) → ``recognize_sub_refs``.
  3. chapter sites (``N luku``) → chapter labels.
"""
from __future__ import annotations

import functools
import re
from typing import List

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.address_parse import ParsedLegalAddress
from lawvm.finland.johtolause.grammar import sections as _sections
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.subref import SubRef, recognize_sub_refs
from lawvm.finland.johtolause.lexer import tokenize

# ---------------------------------------------------------------------------
# Site-scan anchors (cheap prefilter; structure is grammar-parsed). §1.11:
# module-scope compiled, bounded quantifiers only, no nested unbounded repeats.
# ---------------------------------------------------------------------------

_SEC_LABEL = r"\d{1,6}(?:\s*[a-zA-Z])?"
_SEP = r"(?:,|ja|sek\xe4|ynn\xe4|tai|[–—―-])"
_NUM_RUN = rf"{_SEC_LABEL}(?:\s*{_SEP}\s*{_SEC_LABEL})*"
# A label run that can precede a tail noun. A kohta / alakohta level may be named
# by a bare letter list (``a kohta``, ``a ja b alakohta``) as well as a numeric
# run, so the pre-noun label allows EITHER a numeric run or a (comma/ja-joined)
# letter list. Bounded quantifiers only.
_LETTER_LABEL = r"[a-zA-Z]"
_LETTER_RUN = rf"{_LETTER_LABEL}(?:\s*{_SEP}\s*{_LETTER_LABEL})*"
_TAIL_LABEL_RUN = rf"(?:{_NUM_RUN}|{_LETTER_RUN})"
_TAIL_NOUN = (
    r"(?:moment\w+|kohda\w+|kohta|alakohda\w+|alakohta"
    r"|otsiko\w+|johdanto\w+|v\xe4liotsiko\w+)"
)
# The § marker may be the symbol (optionally glued / missing-colon inflected) OR
# the spelled-out word ``pykälä`` in any case (``pykälän`` / ``pykälää`` …); the
# shared lexer already maps both to a PYKALA token.
_PYKALA_MARKER = r"(?:§(?::?[a-z\xe4\xf6\xe5]+)?|pyk\xe4l\w+)"

_SECTION_SITE_RE = re.compile(
    rf"""
    (?P<surf>
        {_NUM_RUN}
        \s*{_PYKALA_MARKER}
        (?:\s+(?:{_TAIL_LABEL_RUN}\s+)?{_TAIL_NOUN})*
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# A coordinating joiner GLUED to a following digit (``ja37``, ``sekä43``,
# ``ynnä2``, ``tai5``): the lexer reads ``ja37`` as one WORD, breaking the
# number list. A joiner directly followed by a digit is unambiguously a
# coordination joiner, so insert a space before the digit. This is a site-scan
# NORMALIZATION (the cheap anchor / lexer-primitive floor), not a structural
# decision — structure is still parsed by the grammar over the de-glued tokens.
_GLUED_JOINER_DIGIT_RE = re.compile(r"\b(ja|sek\xe4|ynn\xe4|tai)(?=\d)", re.IGNORECASE)

# Standalone momentti site (no §): ``2 ja 3 momentti``.
_STANDALONE_MOM_RE = re.compile(rf"(?P<surf>{_NUM_RUN}\s+moment\w+)", re.IGNORECASE)

# Chapter site: bare ``N luku`` / ``N–M luku`` (the genitive ``N luvun`` is a
# section qualifier handled by the §-pass, not a chapter-level repeal).
_CH_LABEL = r"\d{1,4}(?:\s*[a-z])?"
_CH_SEP = r"(?:,|ja|sek\xe4|tai|[–—―-])"
_CH_RUN = rf"{_CH_LABEL}(?:\s*{_CH_SEP}\s*{_CH_LABEL})*"
_CHAPTER_SITE_RE = re.compile(rf"(?P<surf>{_CH_RUN})\s+luku\b", re.IGNORECASE)
_CH_RANGE_RE = re.compile(r"^(\d{1,4})\s*[–—―-]\s*(\d{1,4})$")
_CH_SPLIT_RE = re.compile(r"\s*(?:,|\bja\b|\bsek\xe4\b|\btai\b)\s*", re.IGNORECASE)
_CH_SPACED_SUFFIX_RE = re.compile(r"^(\d{1,4})\s+([a-z])$", re.IGNORECASE)


def _expand_chapter_run(run: str) -> List[str]:
    """Expand a chapter number run into individual chapter labels.

    ``"9"`` → ``["9"]``; ``"2–5"`` → ``["2","3","4","5"]``; ``"2, 4 ja 5"`` →
    ``["2","4","5"]``; a spaced letter suffix (``9 a``) glues to the AKN form.
    """
    out: List[str] = []
    for piece in _CH_SPLIT_RE.split(run.strip()):
        piece = piece.strip()
        if not piece:
            continue
        rm = _CH_RANGE_RE.match(piece)
        if rm is not None:
            lo, hi = int(rm.group(1)), int(rm.group(2))
            if lo <= hi and hi - lo < 100:
                out.extend(str(n) for n in range(lo, hi + 1))
                continue
        out.append(_CH_SPACED_SUFFIX_RE.sub(r"\1\2", piece).replace(" ", ""))
    return out


def _subref_to_address(
    section_label: str, sub: SubRef, chapter: str | None
) -> ParsedLegalAddress:
    """Lift a grammar ``SubRef`` (under a section) to a ``ParsedLegalAddress``."""
    special = ""
    if sub.facet is FacetKind.HEADING:
        special = "heading"
    elif sub.facet is FacetKind.INTRO:
        special = "intro"
    return ParsedLegalAddress(
        section=section_label,
        subsection=sub.momentti if sub.momentti else None,
        item=sub.item or None,
        subitem=sub.subitem or None,
        chapter=chapter,
        special=special,
    )


def _consume_inter_site_sep(scan: "_sections._Scan") -> bool:
    """Advance past a list separator between coordinated section runs in a site."""
    saved = scan.pos
    if _sections._sep(scan) is None:
        return False
    return scan.pos != saved


def _section_addresses_from_surface(surface: str) -> List[ParsedLegalAddress]:
    """Run the shared grammar section recognizer over one §-anchored surface."""
    surface = _GLUED_JOINER_DIGIT_RE.sub(r"\1 ", surface)
    toks = tokenize(surface)
    if not toks:
        return []
    scan = _sections._Scan(Cursor(toks, 0))
    out: List[ParsedLegalAddress] = []
    while scan.pos < len(toks):
        saved = scan.pos
        parsed = _sections.recognize_section_ref(scan)
        if parsed is None or scan.pos == saved:
            break
        # The repeal/address lane models only the suffix form (section +
        # sub-refs). A renumber / pykälä-prefix amendment shape does not occur in
        # a repeal-target tail; if one is recognized, fall back to bare sections
        # so nothing is dropped (fail-loud-not-silent).
        if parsed.form is not _sections.SectionForm.SUFFIX:
            for num, suffix in parsed.nums:
                expanded_list = _sections._expand_range_single(num)
                for expanded in expanded_list:
                    label = expanded + (suffix if len(expanded_list) == 1 else "")
                    out.append(
                        ParsedLegalAddress(section=label, chapter=parsed.explicit_chapter)
                    )
            if not _consume_inter_site_sep(scan):
                break
            continue
        subs = list(parsed.subs) or [SubRef()]
        for num, suffix in parsed.nums:
            expanded_list = _sections._expand_range_single(num)
            for expanded in expanded_list:
                label = expanded + (suffix if len(expanded_list) == 1 else "")
                for sub in subs:
                    out.append(_subref_to_address(label, sub, parsed.explicit_chapter))
        if not _consume_inter_site_sep(scan):
            break
    return out


def scan_legal_addresses(text: str) -> List[ParsedLegalAddress]:
    return list(_scan_legal_addresses_tuple(text))


@functools.lru_cache(maxsize=8192)
def _scan_legal_addresses_tuple(text: str) -> tuple[ParsedLegalAddress, ...]:
    """Scan *text* for legal-address citation sites and parse each via the grammar.

    Drop-in superset replacement for the (now-removed) legacy
    ``address_parse.parse_legal_addresses`` regex parser: same ``ParsedLegalAddress``
    output type and ordering convention (§ sites, then standalone momentti, then
    chapter), but the structure of every site is parsed by the shared johtolause
    grammar instead of a parallel regex. Patterns that cannot be classified are
    silently skipped (the caller decides how to dispose unsupported targets).
    """
    addresses: List[ParsedLegalAddress] = []
    consumed: List[tuple[int, int]] = []

    # Pass 1: §-anchored section sites (symbol or spelled-out ``pykälä``).
    lower = text.lower()
    if "§" in text or "pykäl" in lower:
        for m in _SECTION_SITE_RE.finditer(text):
            addrs = _section_addresses_from_surface(m.group("surf"))
            if addrs:
                addresses.extend(addrs)
                consumed.append((m.start(), m.end()))

    # Pass 2: standalone momentti sites (no § in the run).
    if "moment" in lower:
        for m in _STANDALONE_MOM_RE.finditer(text):
            if any(s <= m.start() < e for s, e in consumed):
                continue
            toks = tokenize(m.group("surf"))
            subs, _end = recognize_sub_refs(toks, 0, mode="amendment")
            for sub in subs:
                if sub.momentti:
                    addresses.append(ParsedLegalAddress(subsection=sub.momentti))

    # Pass 3: chapter sites (bare ``N luku``).
    if "luku" in lower:
        for m in _CHAPTER_SITE_RE.finditer(text):
            for ch in _expand_chapter_run(m.group("surf")):
                addresses.append(ParsedLegalAddress(chapter=ch))

    return tuple(addresses)
