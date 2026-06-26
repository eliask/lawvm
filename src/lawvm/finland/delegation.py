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

Post-flip role of ``extract_asetus_authority``
----------------------------------------------
The well-formed ``[act](NUM/YEAR) N §:n nojalla`` authority basis is now
**construction-owned** by
:func:`lawvm.finland.legal_surface.delegation_parse.extract_authority_bases`
(lifted to ISSUED_UNDER ``ReferenceMention`` records by
``references.ref_mention_extractor.extract_delegation_construction_authority_mentions``).
This regex extractor is therefore **DEMOTED** to two surviving roles, NOT
removed:

  * **Typed-residue fallback** in ``extract_all_reference_mentions``: for a basis
    parent the construction COVERS, the construction's richer/sectioned mention
    supersedes the regex-derived ISSUED_UNDER mention; the regex output is kept
    only for the construction-DECLINED residue (voimaantulo-/siirtymäsäännös
    bases, momentti-only/budget-momentti bases, prose provision paths, OCR/abbrev
    noise) so nothing the regex shipped at the parent level is lost.
  * **StatuteGraph metadata enricher**: ``extract_asetus_authority`` is still
    called by ``references.cross_refs._merge_authority_basis`` to populate
    ``target_section`` / ``target_kind`` on ISSUED_UNDER edges and to append
    edges for nojalla bases absent from the finlex:issuedUnderActs metadata —
    that enrichment is untouched by the construction flip.
"""
from __future__ import annotations

import re
from lawvm.core.regex_safety import PrefilteredPattern, compile_classifier_regex
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

from lawvm.core.filter_result import FilterResult, RejectedItem

# Shared authority-basis surface helpers relocated to the neutral
# ``authority_basis`` leaf module so the ``references`` lift can import them
# without a backreach into this legacy module. Re-exported here for the
# existing in-module call sites and for back-compat importers (e.g.
# ``tests/test_fi_delegation.py``).
from lawvm.finland.authority_basis import _classify_authority_kind, _normalize_year

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
    # The drafting KIND of the cited authority basis, read from the Finnish
    # inflection of the act-name word that immediately precedes the
    # ``(NUM/YEAR)`` id in the ``nojalla`` clause:
    #   "act"      — ``…lain (…)``, ``…laissa``, ``…kaaren`` (a laki/statute);
    #   "decree"   — ``…asetuksen (…)`` (an asetus);
    #   "decision" — ``…päätöksen (…)`` (a päätös);
    #   ""         — no recognizable kind word (multi-word name tail, etc.).
    # An authority basis can be a laki OR a decree/decision (a decree may be
    # issued under another decree's authority), so the kind is NOT assumed: it
    # is read from the surface and carried so the reference-mention lift types a
    # laki basis as a statute cross-reference instead of a non-statutory
    # instrument. Empirically the surface kind matches the target statute_type
    # in ~all sampled cases. See _classify_authority_kind.
    parent_kind: str = ""


@dataclass(frozen=True)
class DelegationDiagnostic:
    """Typed extraction diagnostic for delegation/authority edges not emitted."""

    rule_id: str
    family: str
    phase: str
    source_statute_id: str
    reason: str
    section: str = ""
    eid: str = ""
    match_text: str = ""
    quote: str = ""
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: str = "record"

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "phase": self.phase,
            "source_statute_id": self.source_statute_id,
            "reason": self.reason,
            "section": self.section,
            "eid": self.eid,
            "match_text": self.match_text,
            "quote": self.quote,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


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

@dataclass(frozen=True)
class _NegativeDelegationPattern:
    rule_id: str
    pattern: re.Pattern[str] | PrefilteredPattern


_PAT_NEGATIVE = [
    # Commencement/transition: "asetuksen voimaantulosta säädetään"
    _NegativeDelegationPattern(
        "fi_delegation_commencement_reference_filtered",
        compile_classifier_regex(r'voimaan(?:tulosta|panosta)\s+säädetään', re.IGNORECASE, classifier_id="fi.delegation.(negative)fi_delegation_commencement_reference_filtered"),
    ),
    # Repeal: "kumotaan ... asetuksella"
    _NegativeDelegationPattern(
        "fi_delegation_repeal_reference_filtered",
        re.compile(r'kumotaan\s+[\w\s]{0,40}asetuksella', re.IGNORECASE),
    ),
    # Reference to existing decree with ID: "(123/2004)"
    _NegativeDelegationPattern(
        "fi_delegation_existing_decree_reference_filtered",
        compile_classifier_regex(r'asetuksessa\s+\(\d{1,4}/\d{4}\)', re.IGNORECASE, classifier_id="fi.delegation.(negative)fi_delegation_existing_decree_reference_filtered"),
    ),
    # Parameter adjustment, not delegation
    _NegativeDelegationPattern(
        "fi_delegation_parameter_adjustment_filtered",
        re.compile(r'(?:tarkistaa|muuttaa)\s+[\w\s]{0,30}asetuksella', re.IGNORECASE),
    ),
    # Reference to ANOTHER law's delegation authority ("on the basis of ... decree")
    _NegativeDelegationPattern(
        "fi_delegation_nojalla_reference_filtered",
        compile_classifier_regex(r'nojalla\s+annettavalla', re.IGNORECASE, classifier_id="fi.delegation.(negative)fi_delegation_nojalla_reference_filtered"),
    ),
    # Existing statute reference in nojalla construction (asetus ID already issued)
    _NegativeDelegationPattern(
        "fi_delegation_existing_authority_reference_filtered",
        re.compile(r'\(\d{1,5}/\d{4}\)\s*\d*\s*§:n\s+nojalla', re.IGNORECASE),
    ),
    # Commencement delegation — law enters into force at time set by decree (always exercised)
    _NegativeDelegationPattern(
        "fi_delegation_commencement_decree_filtered",
        re.compile(r'tulee\s+voimaan\s+[\w\s]{0,50}asetuksella', re.IGNORECASE),
    ),
]

# ---------------------------------------------------------------------------
# Asetus preamble pattern (reverse direction: asetus → parent law)
# ---------------------------------------------------------------------------

# Single AUTHORITY-BASIS CONJUNCT inside a (possibly coordinated) ``nojalla``
# clause: "[lain nimi] (NUM/YEAR) [§:n [momentin]]". The Finnish drafting form
# coordinates several authority bases with ``ja`` / ``sekä`` / ``,`` before one
# terminal ``nojalla``:
#
#   "…lukiolain (629/1998) 36 §:n 1 momentin ja
#     valtion maksuperustelain (150/1992) 8 §:n nojalla"
#
# The earlier single-match approach captured ONLY the conjunct adjacent to
# ``nojalla`` (here ``150/1992 §8``) and dropped the first conjunct
# (``629/1998 §36``). This conjunct pattern is applied across the window that
# precedes a single ``nojalla`` so EVERY coordinated basis is distributed over
# the same ``nojalla`` authority and emitted with its own section/momentti.
# Years are matched as 4-digit (1986) or 2-digit (86 → normalized to 19xx/20xx).
#
#   group 1 = act-name word right before the id (kind signal; may be absent)
#   group 2 = statute number, group 3 = year (2- or 4-digit)
#   group 4 = section (optional), group 5 = section letter suffix (optional),
#   group 6 = momentti (optional)
#
# The section letter suffix ("60 a §" → section "60a") is CAPTURED, not merely
# consumed: a Finnish section label is the number glued to its letter suffix
# (the same "60a" convention the AKN sec_ path and the inline-CITES lane use).
# Dropping the letter silently collapses "60 a §" and "60 §" onto the same
# section, losing the distinction between two genuinely different provisions.
#
# Bounded quantifiers (AGENTS.md §1.11): the name word is a single bounded
# token; the section/momentti tails are bounded digit runs.
# Clause-boundary tokenizer for the demoted legacy authority extractor.
_NOJALLA_RE = compile_classifier_regex(r'nojalla', re.IGNORECASE, classifier_id="fi.delegation.nojalla_re")
_PAT_NOJALLA_CONJUNCT = re.compile(
    r'([A-Za-z\xe4\xf6\xe5\xc4\xd6\xc5\-]{1,60})?\s*'
    r'\((\d{1,5})\s*/\s*(\d{2,4})\)\s*'
    r'(?:(\d+)\s*([a-z])?\s*(?:§:n|§)\s*(?:(\d+)\s*momentin\s*)?)?',
    re.IGNORECASE
)


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


def _false_positive_rule_id(context_text: str) -> str:
    """Return the negative-filter rule ID for a known false-positive context."""
    for candidate in _PAT_NEGATIVE:
        if candidate.pattern.search(context_text):
            return candidate.rule_id
    return ""


def _section_num(section_elem: ET.Element[str]) -> str:
    """Extract § number from an Akoma Ntoso section element."""
    num_elem = section_elem.find(f'{NS}num')
    if num_elem is not None and num_elem.text:
        return num_elem.text.strip().rstrip(' §')
    return ''


def _elem_text_norm(elem: ET.Element[str]) -> str:
    """Extract normalized plain text from an XML element."""
    raw = ET.tostring(elem, encoding='unicode', method='text')
    return re.sub(r'\s+', ' ', raw).strip()


# ---------------------------------------------------------------------------
# Forward extraction: law → delegation clauses
# ---------------------------------------------------------------------------

def _record_parse_failure(
    diagnostics_out: Optional[list[DelegationDiagnostic]],
    *,
    statute_id: str,
    phase: str,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        DelegationDiagnostic(
            rule_id=f"fi_{phase}_xml_parse_failed",
            family="source_pathology",
            phase=phase,
            source_statute_id=statute_id,
            reason=f"Finnish {phase.replace('_', ' ')} skipped source XML because parsing failed.",
            blocking=True,
            strict_disposition="block",
        )
    )


def _record_false_positive_filter(
    diagnostics_out: Optional[list[DelegationDiagnostic]],
    *,
    rule_id: str,
    statute_id: str,
    section: str,
    eid: str,
    match_text: str,
    quote: str,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        DelegationDiagnostic(
            rule_id=rule_id,
            family="graph_edge_filter",
            phase="delegation_extraction",
            source_statute_id=statute_id,
            reason="Finnish delegation extractor rejected a regex candidate using a named negative filter.",
            section=section,
            eid=eid,
            match_text=match_text,
            quote=quote,
            blocking=False,
            strict_disposition="record",
        )
    )


def extract_delegations(
    xml_bytes: bytes,
    statute_id: str,
    *,
    diagnostics_out: Optional[list[DelegationDiagnostic]] = None,
) -> FilterResult[DelegationEdge]:
    """Extract delegation clauses from a Finnish statute XML.

    Scans at subsection (momentti) level for precise addressing. Falls back to
    section level for statutes without subsection markup.

    Conservation contract (AGENTS.md §1.8)
    --------------------------------------
    Returns a :class:`FilterResult[DelegationEdge]`: ``accepted_items`` are the
    detected delegation edges; ``rejected_items`` are the regex candidates a
    named negative filter rejected (and a whole-document rejection on XML parse
    failure), each carrying the rejecting rule id as ``reason_code``. A caller
    therefore cannot receive the kept edges without also receiving the reject
    ledger — the false-positive rejections are no longer computed and silently
    discarded behind an optional ``diagnostics_out`` sink. ``diagnostics_out``
    remains supported for the richer typed :class:`DelegationDiagnostic` records.

    Demoted to typed residue / cross-check (NOT the production source)
    -----------------------------------------------------------------
    The production StatuteGraph forward-grant source is now the canonical
    token-native parser via
    :func:`lawvm.finland.legal_surface.delegation_edge_adapter.extract_delegations_canonical`.
    On a 2500-statute differential the canonical parser had materially higher
    recall (+774 edges) at higher precision (the A-only residue was dominated by
    A false positives), and the last genuine-drop class (published_norm over-fire)
    was closed before the flip. This nine-regex extractor (``_DELEGATION_PATTERNS``)
    is RETAINED — importable and available as a residue/cross-check oracle — but is
    no longer wired into ``build_statute_graph_fi`` / ``..._lightweight``. It also
    still backs the ``delegation_census`` differential harness and the
    ``lawvm delegate`` CLI cross-check. Do not delete: it is the fallible regex
    oracle the canonical parser is differentiated against.

    Args:
        xml_bytes:  Raw XML bytes of the statute (Akoma Ntoso / Finlex format).
        statute_id: Canonical statute ID, e.g. "2011/646".

    Returns:
        A :class:`FilterResult` whose ``accepted_items`` are DelegationEdge
        instances (one per detected delegation clause; a single provision may
        produce multiple edges) and whose ``rejected_items`` are the
        negative-filtered regex candidates with their rejecting rule id.
    """
    rejected: list[RejectedItem[DelegationEdge]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        _record_parse_failure(
            diagnostics_out,
            statute_id=statute_id,
            phase="delegation_extraction",
        )
        return FilterResult(
            accepted_items=(),
            rejected_items=(
                RejectedItem(
                    item=DelegationEdge(
                        statute_id=statute_id,
                        section="",
                        eid="",
                        delegation_type="",
                        match_text="",
                        quote="",
                    ),
                    reason=(
                        "Finnish delegation extractor skipped source XML "
                        "because parsing failed."
                    ),
                    reason_code="fi_delegation_extraction_xml_parse_failed",
                    blocking=True,
                ),
            ),
        )

    # Build list of (element, section_num, eid) scan units.
    # Prefer subsections for fine-grained addressing.
    scan_units: List[tuple[ET.Element[str], str, str, str]] = []
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

        matched_spans: List[tuple[int, int]] = []

        for pat in _DELEGATION_PATTERNS:
            for m in pat.finditer(unit_text):
                # Skip overlapping matches
                if any(m.start() < end and m.end() > start
                       for start, end in matched_spans):
                    continue
                ctx_start = max(0, m.start() - 100)
                ctx_end = min(len(unit_text), m.end() + 100)
                context_text = unit_text[ctx_start:ctx_end]
                rule_id = _false_positive_rule_id(context_text)
                if rule_id:
                    matched_spans.append((m.start(), m.end()))
                    candidate_text = m.group(0).strip()
                    _record_false_positive_filter(
                        diagnostics_out,
                        rule_id=rule_id,
                        statute_id=statute_id,
                        section=sec_num,
                        eid=unit_eid,
                        match_text=candidate_text,
                        quote=context_text[:500],
                    )
                    rejected.append(
                        RejectedItem(
                            item=DelegationEdge(
                                statute_id=statute_id,
                                section=sec_num,
                                eid=unit_eid,
                                delegation_type=_classify_delegation_type(candidate_text),
                                match_text=candidate_text,
                                quote=context_text[:500],
                            ),
                            reason=(
                                "Finnish delegation extractor rejected a regex "
                                "candidate using a named negative filter."
                            ),
                            reason_code=rule_id,
                            blocking=False,
                        )
                    )
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

    return FilterResult(accepted_items=tuple(results), rejected_items=tuple(rejected))


# ---------------------------------------------------------------------------
# Reverse extraction: asetus → parent law authority
# ---------------------------------------------------------------------------

def extract_asetus_authority(
    xml_bytes: bytes,
    asetus_id: str,
    *,
    diagnostics_out: Optional[list[DelegationDiagnostic]] = None,
) -> FilterResult[AuthorityEdge]:
    """Parse an asetus preamble for "nojalla" references to parent law.

    The Finnish "nojalla" construction identifies the legal authority under
    which a decree was issued. Each reference creates an AuthorityEdge:
    asetus → parent_law_provision.

    Conservation contract (AGENTS.md §1.8)
    --------------------------------------
    Returns a :class:`FilterResult[AuthorityEdge]`: ``accepted_items`` are the
    parsed authority edges; ``rejected_items`` carries a whole-document
    rejection when the source XML fails to parse, so a caller cannot receive the
    kept edges without the reject ledger. ``diagnostics_out`` remains supported
    for the richer typed :class:`DelegationDiagnostic` record.

    Args:
        xml_bytes: Raw XML bytes of the asetus (Finlex format).
        asetus_id: The decree's statute ID, e.g. "2011/500".

    Returns:
        A :class:`FilterResult` whose ``accepted_items`` are AuthorityEdge
        instances (one per parent-law citation found).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        _record_parse_failure(
            diagnostics_out,
            statute_id=asetus_id,
            phase="authority_extraction",
        )
        return FilterResult(
            accepted_items=(),
            rejected_items=(
                RejectedItem(
                    item=AuthorityEdge(
                        asetus_id=asetus_id,
                        parent_statute_id="",
                        parent_section="",
                        parent_moment="",
                        quote="",
                    ),
                    reason=(
                        "Finnish authority extractor skipped source XML "
                        "because parsing failed."
                    ),
                    reason_code="fi_authority_extraction_xml_parse_failed",
                    blocking=True,
                ),
            ),
        )

    # Search preamble first; fall back to first 500 chars of full text
    preamble = root.find(f'.//{NS}preamble')
    if preamble is not None:
        ptext = _elem_text_norm(preamble)
    else:
        ptext = _elem_text_norm(root)[:500]

    results: List[AuthorityEdge] = []
    seen: set[tuple[str, str, str]] = set()

    # The decree's own enactment year is the causal upper bound on the year of any
    # authorizing-law cite in its preamble (an authority basis cannot post-date the
    # decree it authorizes). ``asetus_id`` is the canonical ``YEAR/NUMBER`` key.
    _asetus_head = asetus_id.split("/", 1)[0]
    citing_year = int(_asetus_head) if _asetus_head.isdigit() else None

    # A ``nojalla`` clause may coordinate several authority bases with
    # ``ja`` / ``sekä`` / ``,`` before one terminal ``nojalla``. Distribute that
    # single ``nojalla`` authority over ALL coordinated conjuncts: for each
    # ``nojalla`` occurrence, take the text window from the previous clause
    # boundary up to it and emit one edge per ``(NUM/YEAR)`` conjunct, each with
    # its own section/momentti and surface-derived kind. The original code took
    # only the conjunct adjacent to ``nojalla`` and dropped the earlier ones.
    prev_boundary = 0
    # lawvm-regex: owning_parser clause-boundary tokenizer in the demoted legacy authority extractor (canonical owner is legal_surface.delegation_parse), owns the preamble-authority surface it parses
    for nm in _NOJALLA_RE.finditer(ptext):
        window = ptext[prev_boundary:nm.start()]
        prev_boundary = nm.end()
        for m in _PAT_NOJALLA_CONJUNCT.finditer(window):
            name_word = (m.group(1) or '').strip()
            num, year = m.group(2), m.group(3)
            parent_id = f"{_normalize_year(year, citing_year)}/{num}"
            # Glue the optional letter suffix onto the section number ("60 a §" →
            # "60a"), matching the AKN sec_ / inline-CITES "60a" convention. A bare
            # number with no suffix stays "60".
            sec = m.group(4) or ''
            if sec and m.group(5):
                sec = f"{sec}{m.group(5).lower()}"
            moment = m.group(6) or ''
            # Deduplicate identical (parent, section, momentti) triples that can
            # arise from overlapping windows or repeated surfaces.
            dedup_key = (parent_id, sec, moment)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            abs_start = prev_boundary - len(window) + m.start()
            snippet_start = max(0, abs_start - 50)
            snippet_end = min(len(ptext), nm.end() + 10)
            results.append(AuthorityEdge(
                asetus_id=asetus_id,
                parent_statute_id=parent_id,
                parent_section=sec,
                parent_moment=moment,
                quote=ptext[snippet_start:snippet_end],
                parent_kind=_classify_authority_kind(name_word),
            ))

    return FilterResult(accepted_items=tuple(results), rejected_items=())
