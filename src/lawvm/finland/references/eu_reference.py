"""Shared EU / CELEX / OJ reference recognition for Finnish statutes.

Single source of truth for the EU-act, CELEX, and Official-Journal (OJ)
recognition patterns that were previously duplicated across:

  - finland/references/cross_refs.py            (cross-jurisdiction graph edges)
  - finland/references/preparatory_reference_extractor.py  (preliminaryWork rows)

The two original copies diverged in several small but behaviour-relevant ways.
This module is a PURE DEDUP: it preserves each lane's *exact* prior behaviour
behind a ``dialect`` selector, so output is byte-identical to before. The known
divergences (intentionally kept — do NOT "fix" coverage here):

  CROSS_REF dialect (was cross_refs.py):
    - form alternation: EU|EY|ETY|EURATOM|ETA   (has EURATOM/ETA, no EEY)
    - case-insensitive (re.I) on all EU-act + the N:o spacing is "\\s*N:o"
    - has the number-first/year/form "P2" pattern  (NUMBER/YEAR/EY)
    - CELEX restricted to type chars R|L|D, returns (year, type, number) groups
    - recognises ALL matches across P1 (N:o), P1B (year-first), P2

  PREPARATORY dialect (was preparatory_reference_extractor.py):
    - form alternation: EU|EY|EEY|ETY           (has EEY, no EURATOM/ETA)
    - case-sensitive, N:o spacing is "\\s+N:o"
    - NO number-first/year/form pattern
    - CELEX accepts any uppercase type char [A-Z], returns full celex + parts
    - recognises only the FIRST EU-act match (modern form, else N:o form)

Lowering into the lane-specific output type (CrossRefEdge / PreparatoryReference)
stays in each lane; this module only recognises and reports raw spans + parsed
fields.

§1.11 hot-path regex discipline: all patterns compiled at module scope with
bounded quantifiers (years exactly 4 digits, numbers 1-6 digits, CELEX numbers
exactly 4 digits) and a leading substring guard performed by the caller before
invoking the finditer/search helpers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import List, Optional

# ---------------------------------------------------------------------------
# Dialect selector
# ---------------------------------------------------------------------------

#: cross_refs.py lane — graph-edge recognition (EU|EY|ETY|EURATOM|ETA, re.I).
DIALECT_CROSS_REF = "cross_ref"
#: preparatory_reference_extractor.py lane — preliminaryWork rows (EU|EY|EEY|ETY).
DIALECT_PREPARATORY = "preparatory"

# ---------------------------------------------------------------------------
# Compiled patterns (module scope — §1.11). Bounded quantifiers throughout:
# 4-digit years, 1-6-digit sequence numbers, exactly-4-digit CELEX numbers.
# ---------------------------------------------------------------------------

# --- CROSS_REF dialect EU-act patterns (ported verbatim from cross_refs.py) ---

# "(EY|EU|ETY|EURATOM|ETA) N:o NUMBER/YEAR" — old number-first form. Note the
# "\s*" before N:o and the case-insensitive flag (cross_refs original). The year
# group accepts BOTH the 4-digit form ("(EY) N:o 999/2001") and the legacy
# 2-digit form ("(ETY) N:o 2092/91", "(EY) N:o 207/93"); the lane lowering
# (cross_refs._add) expands a 2-digit year by the codebase century pivot. The
# 4-digit alternative is listed first so a "/2001" never matches as "/20" + "01".
_CR_EU_P1 = re.compile(
    r'\((?:EU|EY|ETY|EURATOM|ETA)\)\s*N:o\s+(\d{1,6})/(\d{4}|\d{2})\b',
    re.I,
)
# "(EU|EY|...) YEAR/NUMBER" — modern year-first form (GDPR-style).
_CR_EU_P1B = re.compile(
    r'\((?:EU|EY|ETY|EURATOM|ETA)\)\s+(\d{4})/(\d{1,6})\b',
    re.I,
)
# "NUMBER/YEAR/EY|EU|ETY|EURATOM|ETA" — alternative order (cross_refs only).
_CR_EU_P2 = re.compile(
    r'(\d{1,6})/(\d{4})/(?:EU|EY|ETY|EURATOM|ETA)\b',
    re.I,
)
# CELEX "3YYYY(R|L|D)NNNN" — type chars restricted, no re.I (cross_refs original).
_CR_CELEX = re.compile(r'\b3(\d{4})(R|L|D)(\d{4})\b')

# --- PREPARATORY dialect EU-act patterns (ported verbatim from prep extractor) ---

# Modern: "(EU) YEAR/SEQUENTIAL" — case-sensitive, EEY in the form set.
_PREP_EU_MODERN = re.compile(
    r'\((?P<form>EU|EY|EEY|ETY)\)\s+(?P<eu_year>\d{4})/(?P<eu_n>\d{1,6})\b'
)
# Old "N:o" form: "(EY) N:o NUMBER/YEAR" — "\s+N:o" spacing, case-sensitive.
_PREP_EU_NNUM = re.compile(
    r'\((?P<form>EU|EY|EEY|ETY)\)\s+N:o\s+(?P<eu_n>\d{1,6})/(?P<eu_year>\d{4})\b'
)
# CELEX accepting any uppercase type char, full-celex + part groups.
_PREP_CELEX = re.compile(
    r'\b(?P<celex>3(?P<cy>\d{4})(?P<ctype>[A-Z])(?P<cn>\d{4}))\b'
)

# --- Embedded-repeal cue (long-form EU citation provenance) ---
# In a long-form EU citation an inner act named only as provenance can be the
# object of a repeal performed by the OUTER (enacting) act. The repealed act is
# REPEALED-EMBEDDED provenance; the enacting act is the primary target. Finnish
# spells this in two opposite word orders, and the cue's GRAMMAR tells us which
# neighbouring act is the repealed object:
#
#   (A) object-PRECEDES — deverbal-noun cue ("X:n kumoamisesta ... Y"):
#         "asetuksen (EY) N:o 1774/2002 kumoamisesta ... asetuksessa (EY) N:o 1069/2009"
#       The repealed act (1774/2002) sits in the gap BEFORE the cue.
#   (B) object-FOLLOWS — finite-verb cue ("Y, jolla kumotaan X"):
#         "asetuksessa (EY) N:o 1069/2009, jolla kumotaan asetus (EY) N:o 1774/2002"
#       The repealed act (1774/2002) sits in the gap AFTER the cue.
#
# Keeping the two cue classes apart prevents role INVERSION: a finite-verb cue
# trailing the primary act (case B) must NOT mark that primary act as repealed.
# Word-boundary anchored, bounded alternation (§1.11).
#
# Object-precedes cues: the deverbal noun "kumoaminen" in the elative/partitive
# ("kumoamisesta"/"kumoamista") — "of/about repealing the preceding act". Also
# the past participle "kumottu/kumotun" used attributively after the object.
_EMBEDDED_REPEAL_CUE_OBJECT_PRECEDES = re.compile(
    r'\b(?:kumoamisesta|kumoamista|kumoamisen|kumottu|kumotun)\b',
    re.I,
)
# Object-follows cues: a finite repeal verb / agentive participle that takes the
# FOLLOWING act as its object ("jolla kumotaan X", "joka kumoaa X", "X:n kumoava").
_EMBEDDED_REPEAL_CUE_OBJECT_FOLLOWS = re.compile(
    r'\b(?:kumotaan|kumoaa|kumoava|kumoavassa|kumoavan)\b',
    re.I,
)
# Bounded window (chars) between an act span and a repeal cue. Object-precedes:
# the cue follows the inner act almost immediately ("(EY) N:o 1774/2002
# kumoamisesta"). Object-follows: the act follows the cue almost immediately
# ("kumotaan asetus (EY) N:o 1774/2002"). Both windows are clipped to the
# neighbouring EU-act span so the cue must sit BETWEEN the two acts.
_EMBEDDED_REPEAL_WINDOW = 40

# --- OJ reference (only the preparatory lane recognised these) ---
# "EUVL L 327, 9.12.2017, s. 20" / "EYVL N:o L 31, 1.2.2002, s. 1". The
# issue-number→date separator varies: a comma (canonical), a semicolon
# ("EUVL N:o L 374; 22.12.2004"), or nothing at all ("EYVL N:o L 235 17.9.1996")
# — accept ``[,;]?`` there. The date→page separator stays a comma (always
# present in practice). Bounded throughout (§1.11).
_OJ_RE = re.compile(
    r'(?:EUVL|EYVL)\s+(?:N:o\s+)?(?P<series>[LCS])\s+(?P<n>\d{1,6})[,;]?\s*'
    r'(?P<d>\d{1,2}\.\d{1,2}\.\d{4}),\s*s\.\s*(?P<p>\d{1,6})'
)

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EuActRef:
    """A recognised EU-act citation span.

    form:    legislative form marker as written (EU/EY/EEY/ETY/EURATOM/ETA).
    number:  sequential act number (string, leading zeros preserved as matched).
    year:    4-digit year (string).
    celex:   full CELEX string if this span came from a CELEX match, else None.
    celex_type: CELEX type char (R/L/D/...) if from a CELEX match, else None.
    raw:     the matched substring.
    start, end: span offsets into the source text.
    role:    citation role within a long-form EU citation. Default ``"primary"``
             (the act the clause actually enacts/cites). ``"repealed_embedded"``
             marks an inner act named only as provenance that the outer act
             REPEALS, e.g. the 1774/2002 in "asetuksen (EY) N:o 1774/2002
             kumoamisesta ... asetuksessa (EY) N:o 1069/2009". Only the
             CROSS_REF dialect ever sets a non-default role.

    ``kind`` (reg/dir/dec/...) is intentionally NOT derived here — each lane
    classifies differently (cross_refs uses a Finnish-keyword look-behind;
    preparatory uses the CELEX type + paragraph keywords), so kind lowering
    stays in the lane.
    """

    form: Optional[str]
    number: str
    year: str
    raw: str
    start: int
    end: int
    celex: Optional[str] = None
    celex_type: Optional[str] = None
    role: str = "primary"


@dataclass(frozen=True)
class OjRef:
    """A recognised Official-Journal (OJ) reference span.

    series:  L / C / S.
    number:  OJ issue number (string).
    date:    raw "D.M.YYYY" date string (parsing into a date stays in the lane).
    page:    starting page (string).
    raw:     the matched substring.
    start, end: span offsets into the source text.
    """

    series: str
    number: str
    date: str
    page: str
    raw: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# EU-act recognition
# ---------------------------------------------------------------------------


def _tag_embedded_repeals(acts: List[EuActRef], text: str) -> List[EuActRef]:
    """Tag EU-act spans named only as the object of a repeal as embedded provenance.

    Two opposite Finnish word orders are recognised, each by its own cue class so
    roles never invert (see the cue-pattern docstring above):

      (A) object-PRECEDES ("X:n kumoamisesta ... Y"): an object-precedes cue in
          the bounded gap AFTER an act (clipped to the next act span) means THAT
          act is the repealed object → ``role="repealed_embedded"``.
      (B) object-FOLLOWS ("Y, jolla kumotaan X"): an object-follows cue in the
          bounded gap BEFORE an act (clipped to the previous act span) means THAT
          act is the repealed object → ``role="repealed_embedded"``.

    The enacting / cited act keeps the default ``role="primary"``.

    PURELY ROLE-TAGGING: only ``role`` changes; the set, order, spans, and parsed
    fields of the returned refs are byte-identical to the input list. When no cue
    is present (the overwhelming majority of EU citations), the input list is
    returned unchanged.
    """
    if len(acts) < 2:
        return acts
    # Process in source order so each act's gaps are clipped at the neighbouring
    # spans (the cue must sit BETWEEN the two acts, never spanning a third).
    order = sorted(range(len(acts)), key=lambda i: acts[i].start)
    tagged: dict[int, EuActRef] = {}
    for pos, idx in enumerate(order):
        act = acts[idx]
        # (A) object-precedes: cue in the trailing gap before the next act.
        next_start = (
            acts[order[pos + 1]].start if pos + 1 < len(order) else len(text)
        )
        gap_end = min(act.end + _EMBEDDED_REPEAL_WINDOW, next_start)
        if gap_end > act.end:
            after_gap = text[act.end:gap_end]
            if _EMBEDDED_REPEAL_CUE_OBJECT_PRECEDES.search(after_gap):
                tagged[idx] = replace(act, role="repealed_embedded")
                continue
        # (B) object-follows: cue in the leading gap after the previous act.
        prev_end = acts[order[pos - 1]].end if pos > 0 else 0
        gap_start = max(act.start - _EMBEDDED_REPEAL_WINDOW, prev_end)
        if gap_start < act.start:
            before_gap = text[gap_start:act.start]
            if _EMBEDDED_REPEAL_CUE_OBJECT_FOLLOWS.search(before_gap):
                tagged[idx] = replace(act, role="repealed_embedded")
    if not tagged:
        return acts
    return [tagged.get(i, act) for i, act in enumerate(acts)]


def recognize_eu_acts(text: str, *, dialect: str) -> List[EuActRef]:
    """Recognise EU-act citations in ``text`` for the given lane dialect.

    DIALECT_CROSS_REF: returns ALL matches across the N:o, year-first, and
        number/year/form patterns, in pattern order (P1, then P1B, then P2),
        each finditer in document order — matching the original cross_refs scan
        order exactly. CELEX matches are NOT included here (use
        :func:`recognize_celex`); cross_refs scanned CELEX as a separate pass.

    DIALECT_PREPARATORY: returns at most ONE match — the first modern-form
        match if any, otherwise the first N:o-form match — matching the
        original ``search()`` semantics. Returns ``[]`` if neither matches.
    """
    if dialect == DIALECT_CROSS_REF:
        out: List[EuActRef] = []
        # P1: (FORM) N:o NUMBER/YEAR  → groups (number, year)
        for m in _CR_EU_P1.finditer(text):
            out.append(EuActRef(
                form=None, number=m.group(1), year=m.group(2),
                raw=m.group(0), start=m.start(), end=m.end(),
            ))
        # P1B: (FORM) YEAR/NUMBER  → groups (year, number)
        for m in _CR_EU_P1B.finditer(text):
            out.append(EuActRef(
                form=None, number=m.group(2), year=m.group(1),
                raw=m.group(0), start=m.start(), end=m.end(),
            ))
        # P2: NUMBER/YEAR/FORM  → groups (number, year)
        for m in _CR_EU_P2.finditer(text):
            out.append(EuActRef(
                form=None, number=m.group(1), year=m.group(2),
                raw=m.group(0), start=m.start(), end=m.end(),
            ))
        # Embedded-repeal tagging (additive — match set/order/identity unchanged):
        # an act whose immediate trailing gap (before the next act span) contains
        # a repeal cue is provenance the OUTER act repeals → role tagging only.
        return _tag_embedded_repeals(out, text)

    if dialect == DIALECT_PREPARATORY:
        m = _PREP_EU_MODERN.search(text)
        if m is None:
            m = _PREP_EU_NNUM.search(text)
        if m is None:
            return []
        return [EuActRef(
            form=m.group("form"),
            number=m.group("eu_n"),
            year=m.group("eu_year"),
            raw=m.group(0),
            start=m.start(),
            end=m.end(),
        )]

    raise ValueError(f"unknown EU-reference dialect: {dialect!r}")


def recognize_celex(text: str, *, dialect: str) -> List[EuActRef]:
    """Recognise CELEX numbers in ``text`` for the given lane dialect.

    DIALECT_CROSS_REF: type chars restricted to R/L/D, case-sensitive.
        Returns ALL matches in document order, with ``year``/``number``/
        ``celex_type`` populated and ``celex`` set to the full CELEX string.

    DIALECT_PREPARATORY: any uppercase type char [A-Z]. Returns ALL matches
        in document order (callers historically used ``.search()`` for the
        first; the first element of this list is that match).
    """
    if dialect == DIALECT_CROSS_REF:
        out: List[EuActRef] = []
        for m in _CR_CELEX.finditer(text):
            year, type_char, num_str = m.group(1), m.group(2), m.group(3)
            out.append(EuActRef(
                form=None, number=num_str, year=year,
                raw=m.group(0), start=m.start(), end=m.end(),
                celex=m.group(0), celex_type=type_char,
            ))
        return out

    if dialect == DIALECT_PREPARATORY:
        out2: List[EuActRef] = []
        for m in _PREP_CELEX.finditer(text):
            out2.append(EuActRef(
                form=None, number=m.group("cn"), year=m.group("cy"),
                raw=m.group("celex"), start=m.start(), end=m.end(),
                celex=m.group("celex"), celex_type=m.group("ctype"),
            ))
        return out2

    raise ValueError(f"unknown EU-reference dialect: {dialect!r}")


# ---------------------------------------------------------------------------
# OJ recognition
# ---------------------------------------------------------------------------


def recognize_oj_refs(text: str) -> List[OjRef]:
    """Recognise Official-Journal references in ``text`` (all matches, in order).

    Single dialect: only the preparatory lane recognised OJ refs. Callers that
    want the first match take ``recognize_oj_refs(text)[0]`` when non-empty.
    """
    out: List[OjRef] = []
    for m in _OJ_RE.finditer(text):
        out.append(OjRef(
            series=m.group("series"),
            number=m.group("n"),
            date=m.group("d"),
            page=m.group("p"),
            raw=m.group(0),
            start=m.start(),
            end=m.end(),
        ))
    return out
