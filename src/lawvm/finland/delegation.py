"""Finnish delegation clause extractor — Phase 8.3a.

Extracts delegation clauses (asetuksenantovaltuudet) from Finnish statute XML,
producing typed graph edges that link law provisions to decree-space targets.

Two extraction paths:

  extract_delegations(xml_bytes, statute_id) -> List[DelegationEdge]
      Forward: find delegation clauses in a law's provisions.
      E.g. §12 mom.3 "säädetään valtioneuvoston asetuksella" → VN_ASETUS edge.

  extract_asetus_authority(xml_bytes, asetus_id) -> List[AuthorityEdge]
      Reverse: parse an asetus preamble for "nojalla" references to parent law.
      E.g. "(646/2011) 44 §:n nojalla" → AuthorityEdge(parent="2011/646", §44).

Data source: Finlex Akoma Ntoso consolidated XML in the corpus store.
Patterns ported from earlier local graph prototypes on 2026-03-22.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List

NS = '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}'

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class DelegationEdge:
    """A delegation clause found in a Finnish statute provision.

    Represents a provision that delegates rulemaking authority to a decree
    or agency. Graph edge: law_provision → decree-space.

    delegation_type values:
      VN_ASETUS   — Valtioneuvoston asetus (Government decree)
      MIN_ASETUS  — Ministeriön asetus (Ministerial decree)
      PRES_ASETUS — Tasavallan presidentin asetus (Presidential decree)
      AGENCY      — Viranomaisen määräys (Agency regulation/guidance)
      ASETUS      — Generic asetus (unclassified)
    """
    statute_id: str
    section: str           # e.g. "12"
    eid: str               # Akoma Ntoso eId of the provision unit
    delegation_type: str   # see above
    match_text: str        # the matched delegation clause text
    quote: str             # surrounding text (up to 500 chars)


@dataclass
class AuthorityEdge:
    """Reverse linkage: an asetus citing the law that authorized it.

    Extracted from asetus preamble "nojalla" references. Graph edge:
    asetus → parent_law_provision.
    """
    asetus_id: str          # the decree's statute_id, e.g. "2011/500"
    parent_statute_id: str  # authorizing law, e.g. "2011/646" (YEAR/NUM)
    parent_section: str     # section cited, e.g. "44" (may be empty)
    parent_moment: str      # subsection cited, e.g. "3" (may be empty)
    quote: str              # preamble text snippet (up to 300 chars)


# ---------------------------------------------------------------------------
# Delegation patterns (forward direction: law → decree-space)
# ---------------------------------------------------------------------------

# Ministry-name helper used in multiple patterns.
# Handles compound names: "sosiaali- ja terveysministeriön", "maa- ja metsätalousministeriön",
# "sisäasiainministeriön" (single compound word), "valtiovarainministeriön", etc.
# Form: (optional 0-3 prefix words) + (word ending in -ministeriön)
_MIN_GEN = r'(?:[\w-]+\s+){0,3}[\w-]*ministeriön'  # genitive (ministeriön)
_MIN_NOM = r'(?:[\w-]+\s+){0,3}[\w-]*ministeriö'   # nominative (ministeriö)

# Pattern 1: "Valtioneuvoston/Ministeriön asetuksella [voidaan] [adv] säädetään/annetaan/vahvistetaan"
# Extended: optional adverb between asetuksella and verb; wider verb set.
_PAT_DECREE_INVERTED = re.compile(
    r'((?:valtioneuvoston|' + _MIN_GEN + r'|tasavallan\s+presidentin)\s+'
    r'asetuksella\s+'
    r'(?:(?:voidaan|on)\s+)?'
    r'(?:[\w-]+\s+)?'    # any single optional adverb/qualifier (tarkemmin, tilapäisesti, etc.)
    r'(?:säätää|säädetään|antaa|annetaan|vahvistaa|vahvistetaan|'
    r'määrätä|määrätään|määritellään|määritellä|'
    r'kieltää|kielletään|rajoittaa|rajoitetaan))',
    re.IGNORECASE
)

# Pattern 2: "tarkemmat säännökset ... [voidaan] annetaan/säädetään ... [adv] asetuksella"
# Extended: span increased to 150 chars; compound ministry; optional adverb before ministry.
_PAT_DECREE_STANDARD = re.compile(
    r'((?:tarkemm(?:at|pia)|lähemm(?:ät|piä))\s+'
    r'(?:säännökset|säännöksiä|määräykset|määräyksiä)\s+'
    r'[\w\s,\.;\-]{0,150}?'
    r'(?:voidaan\s+)?'
    r'(?:antaa|annetaan|säätää|säädetään)\s+'
    r'(?:tarvittaessa\s+|tarkemmin\s+)?'
    r'(?:valtioneuvoston\s+|' + _MIN_GEN + r'\s+)?'
    r'asetuksella)',
    re.IGNORECASE
)

# Pattern 3: "säädetään/annetaan ... asetuksella" (shorter catch-all with compound ministry)
_PAT_DECREE_SHORT = re.compile(
    r'((?:säädöksiä|säännöksiä|säännökset)\s+'
    r'[\w\s,]{0,40}?'
    r'(?:voidaan\s+)?'
    r'(?:antaa|annetaan|säätää|säädetään)\s+'
    r'(?:tarvittaessa\s+|tarkemmin\s+)?'
    r'(?:valtioneuvoston\s+|' + _MIN_GEN + r'\s+)?'
    r'asetuksella)',
    re.IGNORECASE
)

# Pattern 4: Agency regulation — "voi antaa [tarkempia] määräyksiä"
_PAT_AGENCY = re.compile(
    r'((?:[\w-]+(?:virasto|keskus|laitos|hallinto|valvonta|hallitus|lautakunta|'
    r'neuvosto|komissio|ministeriö))\s+'
    r'(?:voi\s+antaa|antaa)\s+'
    r'(?:tarkempia\s+)?'
    r'(?:määräyksiä|teknisiä\s+määräyksiä|ohjeita\s+ja\s+määräyksiä|'
    r'hallinnollisia\s+määräyksiä))',
    re.IGNORECASE
)

# Pattern 5: Verb-first — "säädetään/annetaan [adv] VN/ministeriön asetuksella"
# Extended: optional word between voidaan and verb; tarkempia säännöksiä variant; compound ministry.
_PAT_DECREE_VERB_FIRST = re.compile(
    r'((?:säädetään|annetaan|'
    r'voidaan\s+(?:tarkemmin\s+|lisäksi\s+|tarvittaessa\s+)?(?:säätää|antaa))\s+'
    r'(?:(?:tarkemmin|tarkemmat\s+säännökset|tarkempia\s+säännöksiä|'
    r'tarkempia\s+määräyksiä|lisäksi|tarvittaessa)\s+){0,2}'
    r'(?:valtioneuvoston|' + _MIN_GEN + r')\s+'
    r'asetuksella)',
    re.IGNORECASE
)

# Pattern 6: Simpler agency — "voi antaa tarkempia määräyksiä"
_PAT_AGENCY_SIMPLE = re.compile(
    r'((?:voi|voivat)\s+antaa\s+(?:tarkempia\s+)?'
    r'(?:määräyksiä|teknisiä\s+määräyksiä|ohjeita\s+ja\s+määräyksiä))',
    re.IGNORECASE
)

# Pattern 7: Ministry nominative + antaa/vahvistaa asetuksella
# Catches: "Sosiaali- ja terveysministeriö antaa asetuksella palkkakertoimen..."
_PAT_MINISTRY_NOMINATIVE = re.compile(
    r'((?:valtioneuvosto|' + _MIN_NOM + r')\s+'
    r'(?:antaa|vahvistaa|määrää|hyväksyy)\s+'
    r'asetuksella)',
    re.IGNORECASE
)

# Pattern 8: Verb + ministry genitive + asetuksella (without "tarkemmat" prefix required)
# Catches: "annetaan maa- ja metsätalousministeriön asetuksella",
#          "vahvistetaan sosiaali- ja terveysministeriön asetuksella"
_PAT_ANNETAAN_MINISTRY = re.compile(
    r'((?:annetaan|voidaan\s+antaa|säädetään|voidaan\s+säätää|vahvistetaan)\s+'
    r'(?:(?:[\w-]+)\s+){0,4}'   # allow up to 4 qualifier words (e.g. "riskien arvioinnin perusteella")
    r'(?:' + _MIN_GEN + r')\s+'
    r'asetuksella)',
    re.IGNORECASE
)

# Pattern 9: Bare "asetuksella" forms without explicit issuer (common in pre-1990 statutes)
# Catches: "asetuksella säädetään", "voidaan asetuksella määrätä", "säädetään asetuksella"
_PAT_BARE_ASETUS = re.compile(
    r'('
    r'asetuksella\s+(?:voidaan\s+)?(?:toisin\s+)?(?:säädetään|säädetä|annetaan|määrätään|määrätä)'
    r'|voidaan\s+asetuksella\s+(?:säätää|antaa|määrätä)'
    r'|(?:säädetään|annetaan)\s+tarvittaessa\s+asetuksella'
    r')',
    re.IGNORECASE
)

_DELEGATION_PATTERNS = [
    _PAT_DECREE_INVERTED,
    _PAT_DECREE_STANDARD,
    _PAT_DECREE_SHORT,
    _PAT_DECREE_VERB_FIRST,
    _PAT_AGENCY,
    _PAT_AGENCY_SIMPLE,
    _PAT_MINISTRY_NOMINATIVE,
    _PAT_ANNETAAN_MINISTRY,
    _PAT_BARE_ASETUS,
]

# ---------------------------------------------------------------------------
# Negative patterns (false-positive filters)
# ---------------------------------------------------------------------------

_PAT_NEGATIVE = [
    # Commencement/transition: "asetuksen voimaantulosta säädetään"
    re.compile(r'voimaan(?:tulosta|panosta)\s+säädetään', re.IGNORECASE),
    # Repeal: "kumotaan ... asetuksella"
    re.compile(r'kumotaan\s+[\w\s]{0,40}asetuksella', re.IGNORECASE),
    # Reference to existing decree with ID: "(123/2004)"
    re.compile(r'asetuksessa\s+\(\d{1,4}/\d{4}\)', re.IGNORECASE),
    # Parameter adjustment, not delegation
    re.compile(r'(?:tarkistaa|muuttaa)\s+[\w\s]{0,30}asetuksella', re.IGNORECASE),
    # Reference to ANOTHER law's delegation authority ("on the basis of ... decree")
    re.compile(r'nojalla\s+annettavalla', re.IGNORECASE),
    # Existing statute reference in nojalla construction (asetus ID already issued)
    re.compile(r'\(\d{1,5}/\d{4}\)\s*\d*\s*§:n\s+nojalla', re.IGNORECASE),
    # Commencement delegation — law enters into force at time set by decree (always exercised)
    re.compile(r'tulee\s+voimaan\s+[\w\s]{0,50}asetuksella', re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Asetus preamble pattern (reverse direction: asetus → parent law)
# ---------------------------------------------------------------------------

# "säädetään [lain nimi] (NUM/YEAR) [§:n [momentin]] nojalla:"
# Matches both 4-digit years (1986) and 2-digit years (86 → normalized to 19xx/20xx).
# Middle part allows "sellaisena kuin...laissa (NUM/YY)" interpositions via [^:]*?.
_PAT_NOJALLA = re.compile(
    r'\((\d{1,5})\s*/\s*(\d{2,4})\)\s*'
    r'(?:(\d+)\s*(?:§:n|§)\s*(?:(\d+)\s*momentin\s*)?)?'
    r'[^:]*?nojalla',
    re.IGNORECASE
)


def _normalize_year(year_str: str) -> str:
    """Normalize 2-digit year string to 4-digit (e.g. '86' → '1986', '04' → '2004')."""
    if len(year_str) == 4:
        return year_str
    y = int(year_str)
    # Finnish laws: 17-99 → 1917-1999, 00-16 → 2000-2016
    return str(1900 + y) if y >= 17 else str(2000 + y)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _classify_delegation_type(match_text: str) -> str:
    """Return the delegation type from the matched clause text."""
    t = match_text.lower()
    if 'valtioneuvoston' in t:
        return 'VN_ASETUS'
    if 'ministeriön' in t:
        return 'MIN_ASETUS'
    if 'presidentin' in t:
        return 'PRES_ASETUS'
    if 'määräyksi' in t or 'ohjeita' in t:
        return 'AGENCY'
    return 'ASETUS'


def _is_false_positive(context_text: str) -> bool:
    """True if the surrounding context matches a known false-positive pattern."""
    return any(pat.search(context_text) for pat in _PAT_NEGATIVE)


def _section_num(section_elem: ET.Element) -> str:
    """Extract § number from an Akoma Ntoso section element."""
    num_elem = section_elem.find(f'{NS}num')
    if num_elem is not None and num_elem.text:
        return num_elem.text.strip().rstrip(' §')
    return ''


def _elem_text_norm(elem: ET.Element) -> str:
    """Extract normalized plain text from an XML element."""
    raw = ET.tostring(elem, encoding='unicode', method='text')
    return re.sub(r'\s+', ' ', raw).strip()


# ---------------------------------------------------------------------------
# Forward extraction: law → delegation clauses
# ---------------------------------------------------------------------------

def extract_delegations(xml_bytes: bytes, statute_id: str) -> List[DelegationEdge]:
    """Extract delegation clauses from a Finnish statute XML.

    Scans at subsection (momentti) level for precise addressing. Falls back to
    section level for statutes without subsection markup.

    Args:
        xml_bytes:  Raw XML bytes of the statute (Akoma Ntoso / Finlex format).
        statute_id: Canonical statute ID, e.g. "2011/646".

    Returns:
        List of DelegationEdge instances, one per detected delegation clause.
        A single provision may produce multiple edges (different patterns).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    # Build list of (element, section_num, eid) scan units.
    # Prefer subsections for fine-grained addressing.
    scan_units: List[tuple] = []
    sections = root.findall(f'.//{NS}section') + root.findall(f'.//{NS}article')

    if not sections:
        body = root.find(f'.//{NS}body')
        if body is not None:
            scan_units.append((body, '', '', ''))
    else:
        for sec in sections:
            sec_num = _section_num(sec)
            sec_eid = sec.get('eId', '')
            subsections = sec.findall(f'{NS}subsection')
            if subsections:
                for ss in subsections:
                    ss_eid = ss.get('eId', '') or sec_eid
                    scan_units.append((ss, sec_num, sec_eid, ss_eid))
            else:
                scan_units.append((sec, sec_num, sec_eid, sec_eid))

    results: List[DelegationEdge] = []

    for elem, sec_num, _sec_eid, unit_eid in scan_units:
        unit_text = _elem_text_norm(elem)
        if not unit_text:
            continue

        matched_spans: List[tuple] = []

        for pat in _DELEGATION_PATTERNS:
            for m in pat.finditer(unit_text):
                # Skip overlapping matches
                if any(m.start() < end and m.end() > start
                       for start, end in matched_spans):
                    continue
                ctx_start = max(0, m.start() - 100)
                ctx_end = min(len(unit_text), m.end() + 100)
                if _is_false_positive(unit_text[ctx_start:ctx_end]):
                    continue
                matched_spans.append((m.start(), m.end()))
                match_text = m.group(0).strip()
                results.append(DelegationEdge(
                    statute_id=statute_id,
                    section=sec_num,
                    eid=unit_eid,
                    delegation_type=_classify_delegation_type(match_text),
                    match_text=match_text,
                    quote=unit_text[:500],
                ))

    return results


# ---------------------------------------------------------------------------
# Reverse extraction: asetus → parent law authority
# ---------------------------------------------------------------------------

def extract_asetus_authority(xml_bytes: bytes, asetus_id: str) -> List[AuthorityEdge]:
    """Parse an asetus preamble for "nojalla" references to parent law.

    The Finnish "nojalla" construction identifies the legal authority under
    which a decree was issued. Each reference creates an AuthorityEdge:
    asetus → parent_law_provision.

    Args:
        xml_bytes: Raw XML bytes of the asetus (Finlex format).
        asetus_id: The decree's statute ID, e.g. "2011/500".

    Returns:
        List of AuthorityEdge instances (one per parent-law citation found).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    # Search preamble first; fall back to first 500 chars of full text
    preamble = root.find(f'.//{NS}preamble')
    if preamble is not None:
        ptext = _elem_text_norm(preamble)
    else:
        ptext = _elem_text_norm(root)[:500]

    results: List[AuthorityEdge] = []
    for m in _PAT_NOJALLA.finditer(ptext):
        num, year = m.group(1), m.group(2)
        parent_id = f"{_normalize_year(year)}/{num}"
        sec = m.group(3) or ''
        moment = m.group(4) or ''
        snippet_start = max(0, m.start() - 50)
        snippet_end = min(len(ptext), m.end() + 50)
        results.append(AuthorityEdge(
            asetus_id=asetus_id,
            parent_statute_id=parent_id,
            parent_section=sec,
            parent_moment=moment,
            quote=ptext[snippet_start:snippet_end],
        ))

    return results
