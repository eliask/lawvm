"""he_branch_parser.py — HE-source → typed proposed_ops parser (feature #8).

Parses Finnish government-proposal (HE) AKN XML to extract the amendment-proposal
sections ("enactment text") and lower them to typed ``BranchProposedOp`` records.

The promotion chain:
    Witness (HE source in fi_government_proposal.farchive)
    → Claim (typed proposed_ops populating BranchContext)
    → Materialization (fi_he_branch_ops.parquet + lawvm simulate)

Design decisions
----------------
- **Enactment-text location**: HE bodies contain ``<hcontainer name='enactment-text'>``
  (or bare ``<hcontainer>`` children of mainBody whose text content matches the
  Finnish "Ehdotetaan, että ..." drafting pattern).  We do NOT restrict to a
  specific name attribute because HE corpus variability is high; instead we use
  a named recognizer (``EnactmentSectionRecognizer``) per AGENTS.md §1.13.

- **"Ehdotetaan, että" wrapper stripping**: HE clauses wrap the enacted verb
  with "Ehdotetaan, että <statute-cite> <clause>".  The wrapper is stripped by
  the ``HEClauseRecognizer`` before handing the inner clause text to the
  existing ``johtolause/api.parse_clause`` machinery.  The stripped preamble is
  recorded as ``source_span_preamble`` in the finding.

- **Multi-statute HE**: a single HE may contain enactment clauses for N
  statutes.  We produce one ``BranchProposedOp`` per clause×statute, with
  ``target_statute_id`` resolved from the embedded statute citation.

- **Parse status accounting**: FULL when all enactment-text clauses parsed
  without error; PARTIAL when some succeed and some fail; FAILED when none
  succeeded (but enactment text was present); NOT_APPLICABLE when no
  enactment-text sections could be found (treaty ratifications, budget
  proposals, purely rationale HEs).

- **AGENTS.md §1.1 no silent target hijacking**: if a clause's target provision
  cannot be resolved (proposal-relative address like "uusi 4 a §" that doesn't
  exist in the target statute), we emit ``BranchTargetResolutionFinding`` and
  mark the op as ``target_resolution=unresolved`` — we do NOT re-route silently.

- **AGENTS.md §1.13 recognizer discipline**: the enactment-section locator and
  the "Ehdotetaan, että" preamble stripper are named recognizers, not piles of
  regexes.  Each pattern is compiled at module scope and documented with its
  grammar production.

- **No new normative semantics**: the parser answers structural questions.
  Whether a proposed op represents a policy problem is consumer-layer work.

AGENTS.md compliance
--------------------
§1.1  No silent target hijacking — emit BranchTargetResolutionFinding.
§1.2  No action-family mutation — verb parsed from clause grammar, not guessed.
§1.3  No granularity escalation — carried over from johtolause parser.
§1.4  No sibling deletion by coincidence — carried over.
§1.5  No payload smuggling — each op's target is per-clause.
§1.6  No unstated migration — moves/renumbers emit typed notes on op.
§1.7  No legal conflict resolved by Python accident — multi-HE conflicts
      visible as separate ops with same (target, voimaantulo); no silent merge.
§1.8  No unsupported source lane disappearance — parse failures emit
      BranchParseRecovery records, not silent drops.
§1.13 Recognizer discipline — EnactmentSectionRecognizer, HEClauseRecognizer.

Phase: Parse (§6 phase 3) + Emit evidence (§6 phase 11).
"""

from __future__ import annotations

import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from lxml import etree

from lawvm.finland.fi_dates import parse_fi_day_month_year


# ---------------------------------------------------------------------------
# AKN namespace constants (shared with he_acquisition.py)
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_FINLEX_NS = "http://data.finlex.fi/schema/finlex"


def _localname(node: etree._Element) -> str:
    tag = node.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag) if isinstance(tag, str) else ""


# ---------------------------------------------------------------------------
# Typed output primitives
# ---------------------------------------------------------------------------


class HEParseStatus(Enum):
    """Parse completeness classification for one HEParsedBranch.

    FULL        — all enactment-text clauses parsed without error.
    PARTIAL     — some clauses parsed, some emitted BranchParseRecovery.
    FAILED      — enactment text present but no ops could be extracted.
    NOT_APPLICABLE — no enactment text found (treaty ratification, budget, etc.).
    """

    FULL = "full"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class BranchTargetResolution(Enum):
    """Resolution status for a proposed op's target provision."""

    RESOLVED = "resolved"
    """Target resolved exactly in current state."""
    UNRESOLVED = "unresolved"
    """Target could not be resolved; BranchTargetResolutionFinding emitted."""
    PROPOSAL_RELATIVE = "proposal_relative"
    """Target is proposal-relative (new section like '4 a §'); will not exist yet."""
    AMBIGUOUS = "ambiguous"
    """Multiple candidates in live statute; ambiguity preserved as finding."""


@dataclass(frozen=True, slots=True)
class BranchProposedOp:
    """One typed proposed operation from an HE.

    Each op corresponds to one amendment clause in the HE body (one verb group
    targeting one provision).  Multi-clause HEs produce multiple ops.

    AGENTS.md §1.1: target_resolution records how the target was obtained.
    AGENTS.md §1.2: operation_kind is always derived from the parsed verb, never
    inferred from context.
    """

    op_index: int
    """0-based index of this op within the HE's proposed_ops tuple."""
    operation_kind: str
    """replace | insert | repeal | text_replace | relabel | move | commencement | expiry"""
    target_provision_ref: str
    """Textual provision reference, e.g. '711/2022/7/3' or '711/2022/7' or '711/2022'."""
    target_statute_id: str
    """Statute ID extracted from the clause citation, e.g. '711/2022'."""
    payload_summary: str
    """Short text summary of the payload (first 200 chars of replacement text, or verb description)."""
    source_he_id: str
    """Provenance: the HE ID, e.g. 'HE 184/2024 vp'."""
    branch_id: str
    """Branch identifier, e.g. 'fi/he/2024/184'."""
    source_span_text: str
    """Raw clause text from the HE body that produced this op."""
    source_span_preamble: str
    """'Ehdotetaan, että ...' preamble stripped before parse, or ''."""
    target_resolution: BranchTargetResolution = BranchTargetResolution.UNRESOLVED
    parse_confidence: float = 0.0
    """0.0–1.0 confidence estimate for this op's parse quality."""
    is_proposal_relative: bool = False
    """True when the target is a proposal-relative address (new provision)."""


@dataclass(frozen=True, slots=True)
class BranchParseRecovery:
    """Typed record for a clause that failed to parse.

    AGENTS.md §1.8: never silently drop a parsed clause; emit this instead.
    """

    rule_id: str
    """Stable rule ID, e.g. 'HE_BRANCH.CLAUSE_PARSE_ERROR'."""
    op_index: int
    clause_text: str
    reason: str
    detail: str
    phase: str = "parse"
    family: str = "source_pathology"
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class BranchTargetResolutionFinding:
    """AGENTS.md §1.1 — no silent target hijacking.

    Emitted when a proposed op's target cannot be resolved in the current
    enacted statute state.  Consumer decides what to do; we do not re-route.
    """

    rule_id: str
    """e.g. 'HE_BRANCH.TARGET_UNRESOLVED'"""
    op_index: int
    target_provision_ref: str
    target_statute_id: str
    reason: str
    """Human-readable explanation of why resolution failed."""
    is_proposal_relative: bool = False
    """True when the target is a genuinely new provision (not a bug)."""
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class HEParsedBranch:
    """Result of parsing one HE language variant into typed proposed_ops.

    Per the feature brief §2 and LEGAL_BRANCH_AND_AUTHORITY_AXIS.md.
    """

    branch_id: str
    """Unique per-HE: 'fi/he/{year}/{number}'."""
    he_id: str
    """Human-readable HE identifier, e.g. 'HE 184/2024 vp'."""
    he_year: int
    he_number: int
    proposed_voimaantulo: Optional[date]
    proposed_ops: tuple[BranchProposedOp, ...]
    target_statute_ids: tuple[str, ...]
    """All statute IDs the HE would touch (deduplicated, sorted)."""
    parse_status: HEParseStatus
    parse_findings: tuple[object, ...]
    """BranchParseRecovery | BranchTargetResolutionFinding instances."""
    enactment_sections_found: int
    """Number of enactment-text sections found in the HE body."""
    clauses_attempted: int
    """Number of individual amendment clauses attempted."""
    clauses_succeeded: int
    """Number of clauses that produced at least one BranchProposedOp."""


# ---------------------------------------------------------------------------
# Grammar: EnactmentSectionRecognizer (AGENTS.md §1.13)
#
# Finnish HE bodies contain rationale sections and enactment-text sections.
# Modern Finnish HEs (2020+) use the 'bills'/'bill'/'enactingClause' structure:
#   mainBody
#     hcontainer[name='bills']
#       hcontainer[name='bill']
#         hcontainer[name='enactingClause']   ← amendment directive (johtolause)
#         hcontainer[name='statuteProvisionsWrapper']
#           section  ← payload content (replacement text, NOT a directive)
#
# Legacy / synthetic HEs may use:
#   hcontainer[@name='enactment-text'] with <section> children containing
#   "Ehdotetaan, että ..." text.
#
# EnactingClauseRecognizer (primary, modern HE structure):
#   Extracts hcontainer[name='enactingClause'] text verbatim.
#   Each enactingClause is exactly one amendment directive.
#
# IMPORTANT: The previous extractor incorrectly treated <section> elements
# inside 'statuteProvisionsWrapper' as clauses.  Those sections contain
# the replacement statute text (payload), not johtolause directives.
# The fix: enumerate enactingClause elements first; fall back to the
# legacy section-based path only when none are found.
#
# Per AGENTS.md §1.13: single-pass structural recognizer, not N regex passes.
# ---------------------------------------------------------------------------

# Module-scope compiled patterns (AGENTS.md §1.11)
# Enactment-text trigger pattern: "Ehdotetaan" start or section numbered text
_EHDOTETAAN_RE = re.compile(
    r"^(?:Ehdotetaan|1\s+Lakiehdotusten\s+perustelut|Laki\s+)",
    re.IGNORECASE,
)

# Names of hcontainer elements that hold enactment-text
_ENACTMENT_CONTAINER_NAMES = frozenset({
    "enactment-text",
    "proposal",
    "lainSisalto",
    "lakiehdotus",
    "schedules",
})

# Names of purely-rationale hcontainers (explicitly excluded from enactment parsing)
_RATIONALE_CONTAINER_NAMES = frozenset({
    "rationale",
    "introduction",
    "background",
    "goals",
    "impact",
    "remarks",
    "preface",
    "conclusions",
    "contentAbsent",
})

# The name attribute marking amendment directives in modern Finnish HEs (2020+).
# Each hcontainer[name='enactingClause'] holds exactly one johtolause directive,
# e.g. "Eduskunnan päätöksen mukaisesti muutetaan lannoitelain (711/2022) 7 §...".
# This is the PRIMARY extraction target.
#
# Source: finlex.fi AKN XML corpus, 2020–2025 HEs, all examined examples.
_ENACTING_CLAUSE_NAME = "enactingClause"

# Container names holding section PAYLOAD content (replacement statute text),
# not amendment directives.  Section elements inside these containers must NOT
# be passed to parse_clause() — they are the "what the statute will say", not
# "what the amendment directive says to change".
_PAYLOAD_CONTAINER_NAMES = frozenset({
    "statuteProvisionsWrapper",
    "bill",
    "bills",
})


def _is_enactment_section(element: etree._Element, text_sample: str) -> bool:
    """Named EnactmentSectionRecognizer: decide if an element holds enactment text.

    Per AGENTS.md §1.13: single-pass structured recognizer, not N regex passes.

    Decision tree:
    1. name attribute in _ENACTMENT_CONTAINER_NAMES → yes.
    2. name attribute in _RATIONALE_CONTAINER_NAMES → no.
    3. element has <section> children with legal numbering content → yes.
    4. text_sample matches _EHDOTETAAN_RE → yes.
    5. Otherwise → no.
    """
    name_attr = element.attrib.get("name", "")
    if name_attr in _ENACTMENT_CONTAINER_NAMES:
        return True
    if name_attr in _RATIONALE_CONTAINER_NAMES:
        return False

    # Check for section children that contain legal text
    for child in element:
        child_lname = _localname(child)
        if child_lname == "section":
            return True

    # Text sample heuristic
    # lawvm-regex: prefilter enactment-vs-rationale routing guard on a bounded text sample; clause parse delegated to johtolause/api.parse_clause
    if text_sample and _EHDOTETAAN_RE.search(text_sample[:200]):
        return True

    return False


# ---------------------------------------------------------------------------
# Grammar: HEClauseRecognizer (AGENTS.md §1.13)
#
# Finnish HE clauses follow this structure:
#   "Ehdotetaan, että <statute-name> (<statute-ref>) <amendment-clause>:"
#   "<replacement text>"
# OR
#   "<statute-name> (<statute-ref>) <amendment-clause>:"
#   (without the "Ehdotetaan, että" prefix for grouped clauses)
#
# We produce: preamble (stripped), statute_ref (citation), inner_clause (text)
# ---------------------------------------------------------------------------

# Pattern for "Ehdotetaan, että" preamble (optionally present)
# Bound: preamble is at most 300 characters before the actual clause
_PREAMBLE_RE = re.compile(
    r"^(?:Ehdotetaan,\s+että\s+)?",
    re.IGNORECASE,
)

# Pattern for Finnish statute citation: "lain (711/2022)" or "STATUTE_NAME (711/2022)"
# Finnish statute IDs are NUMBER/YEAR format: e.g. 711/2022 = statute #711, year 2022.
# Statute names appear in genitive form (e.g. "lannoitelain", "ympäristönsuojelulain")
# and may start with lowercase in mid-sentence position.
# The digits tolerate stray interior whitespace ("(396 /1997)") emitted by some HE
# text layers — a spaced citation is otherwise dropped, forcing a bare-ref op.
# AGENTS.md §1.11: bounded quantifiers, compiled at module scope
_STATUTE_CITE_RE = re.compile(
    r"(?P<statute_name>[A-ZÄÖÅa-zäöå][a-zäöåA-ZÄÖÅ\-]{0,100}?)\s*"
    r"\(\s*(?P<statute_number>\d{1,5})\s*/\s*(?P<statute_year>\d{4})\s*\)",
)

# Unparenthesised statute citation: "tutkintavankeuslain 768/2005 …",
# "arpajaislakiin 1047/2001 …", "annetun lain 761/2003 …".  A large minority of HEs
# (esp. pre-2010 and criminal-procedure bills) write the amended act's NUMBER/YEAR
# *without* parentheses right after the target-name.  Requiring parentheses drops the
# id and forces a bare-ref op (a false op_missing against the PDF witness).
#
# CRITICAL discriminator — the name must be in a TARGET case (genitive/illative:
# "…lain", "…lakiin", "…kaaren", "…asetuksen") that the amendment verb governs.  The
# ubiquitous "sellaisina kuin … laissa 424/2017" back-reference to the *amending* laws
# is INESSIVE ("laissa"/"laeissa") — deliberately EXCLUDED from the suffix set — so this
# never hijacks the base target to a later amending statute (AGENTS.md §1.1).
#
# Flat, bounded quantifiers only (FW-07 / AGENTS.md §1.11): a lazy bounded prefix then a
# fixed suffix alternation then a disjoint "\s+\d" — no nested/adjacent variable repeats.
_STATUTE_CITE_BARE_RE = re.compile(
    r"\b(?P<statute_name>[A-Za-zÄÖÅäöå\-]{0,60}?"
    r"(?:lakiin|laista|lakia|laki|lain|kaareen|kaaren|kaari|asetukseen|asetuksen|asetusta))"
    r"\s+(?P<statute_number>\d{1,5})\s*/\s*(?P<statute_year>\d{4})\b",
)

# Amendment-HISTORY marker ("sellaisena kuin se on … / sellaisina kuin ne ovat …" = "as it
# stands, [as] amended by acts …").  Ids listed AFTER this marker within a directive are the
# PRIOR AMENDING acts of the touched sections, not the amended (target) act.  The governing act
# is always cited BEFORE it, so a citation past the marker (with no intervening directive
# boundary) must NOT be read as the op target (AGENTS.md §1.1 — no silent target hijacking to a
# later amending statute).  Label-independent: reads only the clause word order.
_HE_HISTORY_MARKER_RE = re.compile(r"sellais(?:ena|ina)\s+kuin", re.IGNORECASE)

# A directive BOUNDARY — an amendment-verb head opening a new directive, or the "seuraavasti:"
# terminator closing one.  A citation after a history marker but AFTER such a boundary belongs
# to a FRESH directive (its governing cite), so it is NOT a history id.
_HE_DIRECTIVE_BOUNDARY_RE = re.compile(
    r"\b(?:muut(?:etaan|ettu)|lis[äa]t[äa]{1,2}n|kumo(?:taan|ttu)|korv(?:ataan|attu))\b"
    r"|seuraavasti\s*:",
    re.IGNORECASE,
)


def _cite_in_history_span(clause_text: str, pos: int) -> bool:
    """True iff the citation at offset ``pos`` lies inside a "sellaisena/sellaisina kuin …" span.

    The nearest preceding directive marker before ``pos`` decides: if it is a history marker
    (:data:`_HE_HISTORY_MARKER_RE`) rather than a directive boundary
    (:data:`_HE_DIRECTIVE_BOUNDARY_RE` — a new amendment-verb head or "seuraavasti:"), the cite
    is a PRIOR-AMENDING-act id in a provenance list, not the governing target.  Purely on the
    clause word order; never reads the XML or a label.
    """
    markers = [m.start() for m in _HE_HISTORY_MARKER_RE.finditer(clause_text, 0, pos)]
    if not markers:
        return False
    boundaries = [m.start() for m in _HE_DIRECTIVE_BOUNDARY_RE.finditer(clause_text, 0, pos)]
    last_marker = max(markers)
    last_boundary = max(boundaries) if boundaries else -1
    return last_marker > last_boundary


# Pattern for "Ehdotetaan muutettavaksi" (alternative "ehdotetaan" forms)
_EHDOTETAAN_VARIANT_RE = re.compile(
    r"^Ehdotetaan\s+(?:säädettäväksi|muutettavaksi|kumottavaksi|lisättäväksi)",
    re.IGNORECASE,
)

# Pattern to detect "uusi N a §" (new section, proposal-relative address)
_PROPOSAL_RELATIVE_RE = re.compile(
    r"\buusi\s+\d+\s*[a-zA-Z]?\s*§",
    re.IGNORECASE,
)

# Pattern for proposed voimaantulo date extraction from enactment sections
# Matches: "päivänä MONTH YEAR", "YEAR päivänä", numeric dates
_VOIMAANTULO_RE = compile_classifier_regex(r"(?:voimaan|voimaantulo)[^.]{0,150}?"
    r"(?:(\d{1,2})\s+päivän[aä]\s+([a-zäöå]+)\s+(\d{4})|(\d{4})-(\d{2})-(\d{2}))", re.IGNORECASE, classifier_id="fi.he_branch_parser.voimaantulo_re")

def _extract_proposed_voimaantulo(full_body_text: str) -> Optional[date]:
    """Extract proposed voimaantulo (entry-into-force) date from HE body text.

    HEs express proposed voimaantulo in the enactment-text section, typically:
    "Tämä laki on tarkoitettu tulemaan voimaan X päivänä MONTH YEAR."
    or ISO-format dates in metadata.

    Returns None when no date can be extracted (common: "when needed" proposals).
    """
    # lawvm-regex: owning_parser this module is the HE-source branch owning parser; voimaantulo date extraction is its own production
    m = _VOIMAANTULO_RE.search(full_body_text)
    if m is None:
        return None
    if m.group(4):
        # ISO-format group
        try:
            return date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            return None
    # Finnish-text group
    return parse_fi_day_month_year(m.group(1), m.group(2), m.group(3))


def _element_text(element: etree._Element) -> str:
    """Extract all text content from an element, whitespace-normalized."""
    parts: list[str] = []
    for text in element.itertext():
        parts.append(str(text))
    raw = "".join(parts)
    return re.sub(r"\s+", " ", raw).strip()


def _strip_preamble(clause_text: str) -> tuple[str, str]:
    """Strip 'Ehdotetaan, että' preamble from a clause string.

    Returns (preamble, inner_text).
    Per HEClauseRecognizer grammar: preamble is up to the first statute citation.
    """
    # lawvm-regex: owning_parser HE owning parser strips its own clause preamble before delegating inner clause to parse_clause
    m = _PREAMBLE_RE.match(clause_text)
    if m and m.end() > 0:
        preamble = clause_text[:m.end()].strip()
        inner = clause_text[m.end():]
        return preamble, inner
    return "", clause_text


def _extract_statute_citation(clause_text: str) -> Optional[tuple[str, str]]:
    """Extract statute ID from a clause citation: number/year (Finnish format).

    Returns (statute_id, statute_name) or None.
    statute_id is in the form 'NUMBER/YEAR', e.g. '711/2022'.
    Finnish statute IDs use NUMBER/YEAR format (not YEAR/NUMBER).
    """
    # lawvm-regex: owning_parser HE branch owning parser extracts the statute citation from its own clause text
    # Return the first GOVERNING cite, skipping any id inside a "sellaisena/sellaisina kuin …"
    # amendment-history span (the parenthesised most-recent amending act is otherwise picked as a
    # phantom target — AGENTS.md §1.1).  Parenthesised form preferred, then the bare form.
    m = _first_non_history_cite(_STATUTE_CITE_RE, clause_text)
    if m is None:
        # lawvm-regex: owning_parser fallback to the UNPARENTHESISED citation form (target-case
        # name + NUMBER/YEAR) that many pre-2010 / procedural HEs use; suffix-gated so the
        # "sellaisina kuin … laissa NNNN" amending-law back-references cannot hijack the target.
        m = _first_non_history_cite(_STATUTE_CITE_BARE_RE, clause_text)
    if m is None:
        return None
    year = m.group("statute_year")
    number = m.group("statute_number")
    name = m.group("statute_name").strip()
    return f"{number}/{year}", name


def _first_non_history_cite(
    pattern: "re.Pattern[str]", clause_text: str
) -> "Optional[re.Match[str]]":
    """First ``pattern`` match whose id does NOT lie in a history span (:func:`_cite_in_history_span`)."""
    for m in pattern.finditer(clause_text):
        if not _cite_in_history_span(clause_text, m.start("statute_number")):
            return m
    return None


# Finnish nominal case suffixes stripped to key a statute name for cross-citation
# matching.  A heading names the amended act in genitive ("pelastuslain"); a body
# citation may use the inessive ("pelastuslaissa (379/2011)") — both must key to the
# same stem.  Ordered longest-first so a longer ending is stripped before its prefix.
_STATUTE_NAME_CASE_SUFFIXES: tuple[str, ...] = (
    "ksessa", "ksesta", "kseen", "ksen",
    "issa", "issä", "ista", "istä", "iksi", "iin",
    "lla", "llä", "lle", "ksi", "ssa", "ssä", "sta", "stä",
    "na", "nä", "ta", "tä", "in", "en",
    "a", "ä", "n", "t",
)

#: Minimum stem length kept after case-stripping — below this a name is too generic
#: ("lain", "laki") to key reliably, so it is dropped rather than risk a wrong match.
_STATUTE_NAME_MIN_STEM = 5

#: Bill-title heading heads: "Laki … muuttamisesta", "Laiksi … kumoamisesta".
_HEADING_TITLE_HEADS: tuple[str, ...] = ("laki ", "laiksi ", "laeiksi ")
#: Terminal keywords of an amend/repeal bill title; the amended act's name is the last
#: word BEFORE the first of these.
_HEADING_TITLE_TERMINALS: tuple[str, ...] = ("muuttamisesta", "kumoamisesta", "muutamisesta")


def _normalize_statute_name(name: str) -> str:
    """Reduce a Finnish statute name to a case-invariant stem key.

    Lowercases and strips ONE trailing nominal case ending so that the same act cited in
    different cases (genitive "pelastuslain" vs inessive "pelastuslaissa") keys equally.
    Returns "" when the resulting stem is too short to be a reliable key.
    """
    n = name.lower().strip()
    for suf in _STATUTE_NAME_CASE_SUFFIXES:
        if n.endswith(suf) and len(n) - len(suf) >= _STATUTE_NAME_MIN_STEM:
            return n[: -len(suf)]
    return n if len(n) >= _STATUTE_NAME_MIN_STEM else ""


def _build_he_statute_name_map(body_text: str) -> dict[str, str]:
    """Build an UNAMBIGUOUS {normalized-name-stem → statute_id} map from an HE's body.

    Scans the whole HE body (perustelut included) for both the parenthesised and the
    unparenthesised citation forms, keyed by the case-normalized statute name.  A stem
    that maps to more than one distinct id anywhere in the document is AMBIGUOUS and is
    dropped — the map only ever asserts a name→id link the document itself makes
    unambiguously (AGENTS.md §1.1: no silent target hijacking on a guessed id).
    """
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    # lawvm-regex: owning_parser HE owning parser harvests its own body's statute citations to
    # resolve a heading-named amended act whose enacting clause omits the number.
    for rx in (_STATUTE_CITE_RE, _STATUTE_CITE_BARE_RE):
        for m in rx.finditer(body_text):
            stem = _normalize_statute_name(m.group("statute_name"))
            if not stem:
                continue
            sid = f"{m.group('statute_number')}/{m.group('statute_year')}"
            if stem in mapping and mapping[stem] != sid:
                ambiguous.add(stem)
            mapping.setdefault(stem, sid)
    for stem in ambiguous:
        mapping.pop(stem, None)
    return mapping


def _heading_amended_name(heading_text: str) -> str:
    """Extract the amended act's name from a bill-title heading, or "".

    "Laki pelastuslain muuttamisesta" → "pelastuslain"; "Laki merenkulun
    ympäristönsuojelulain muuttamisesta" → "ympäristönsuojelulain" (the head noun, the
    last word before the terminal "muuttamisesta"/"kumoamisesta").  Plain string scanning
    (no regex) keeps this off the perf gate and lets the multi-word name pass through.
    """
    low = heading_text.strip().lower()
    if not any(low.startswith(h) for h in _HEADING_TITLE_HEADS):
        return ""
    words = heading_text.strip().split()
    for i, w in enumerate(words):
        if w.lower().rstrip(".,:;") in _HEADING_TITLE_TERMINALS and i > 0:
            return words[i - 1].strip(".,:;")
    return ""


def _governing_statute_id_from_bill(
    enacting_clause: etree._Element, name_map: dict[str, str]
) -> str:
    """Resolve the amended-act id for a modern enactingClause whose text omits the number.

    The amended act is named only in the bill TITLE heading ("Laki pelastuslain
    muuttamisesta"); the number lives in the perustelut.  We read the bill's first
    title-shaped heading, take the amended-act name, and look it up in the per-HE
    name→id map.  Returns "" when no title heading is found or the name is unmapped —
    the op then stays honestly bare rather than acquiring a guessed id.
    """
    bill = enacting_clause.getparent()
    if bill is None:
        return ""
    for node in bill.iter():
        if _localname(node) == "heading":
            name = _heading_amended_name(_element_text(node))
            if name:
                return name_map.get(_normalize_statute_name(name), "")
    return ""


def _is_proposal_relative_address(clause_text: str) -> bool:
    """Return True when the clause introduces a proposal-relative target.

    Per AGENTS.md §1.1: proposal-relative addresses (like 'uusi 4 a §') will
    not exist in the current enacted statute state; we mark the op and emit
    BranchTargetResolutionFinding rather than failing.
    """
    # lawvm-regex: owning_parser HE owning parser flags a proposal-relative address in its own clause text; emits BranchTargetResolutionFinding
    return _PROPOSAL_RELATIVE_RE.search(clause_text) is not None


# ---------------------------------------------------------------------------
# Verb-to-operation-kind mapping
# ---------------------------------------------------------------------------

_VERB_TO_OPERATION_KIND: dict[str, str] = {
    "M": "replace",
    "K": "repeal",
    "L": "insert",
    "S": "relabel",
    "META": "commencement",
}


def _verb_to_operation_kind(verb: str) -> str:
    """Map Finnish amendment verb code to operation_kind string."""
    return _VERB_TO_OPERATION_KIND.get(verb, "replace")


def _build_provision_ref(
    statute_id: str,
    op: object,  # ParsedOp
) -> str:
    """Build a textual provision reference from a ParsedOp.

    Format: STATUTE_ID/SECTION[/SUBSECTION[/ITEM]]
    e.g. '711/2022/7/3' or '711/2022/5' or '711/2022' (statute-level).
    """
    _kind = getattr(op, "kind", "")
    number = getattr(op, "number", "")
    chapter = getattr(op, "chapter", "")
    momentti = getattr(op, "momentti", 0)
    item = getattr(op, "item", "")

    parts = [statute_id]
    if chapter:
        parts.append(f"luku_{chapter}")
    if number:
        parts.append(number)
    if momentti:
        parts.append(str(momentti))
    if item:
        parts.append(f"kohta_{item}")
    return "/".join(parts)


def _build_payload_summary(clause_text: str, op: object) -> str:
    """Build a short payload summary string for the Parquet projection.

    Truncated to 200 chars. Captures what the op does: verb + target.
    """
    verb = getattr(op, "verb", "?")
    kind = getattr(op, "kind", "")
    number = getattr(op, "number", "")
    momentti = getattr(op, "momentti", 0)
    op_desc = f"[{_verb_to_operation_kind(verb)}] {kind}:{number}"
    if momentti:
        op_desc += f"/{momentti}"
    # Append leading words of clause text for context
    clause_snippet = clause_text[:100].rstrip()
    return f"{op_desc} — {clause_snippet}"[:200]


# ---------------------------------------------------------------------------
# Per-clause parse function
# ---------------------------------------------------------------------------


def _parse_one_clause(
    clause_text: str,
    op_index_start: int,
    source_he_id: str,
    branch_id: str,
    *,
    strict: bool = False,
    governing_statute_id: str = "",
) -> tuple[list[BranchProposedOp], list[object]]:
    """Parse one amendment clause from HE body text.

    Returns (ops, findings) where findings may contain BranchParseRecovery
    and BranchTargetResolutionFinding records.

    Per AGENTS.md §1.13: we reuse johtolause/api.parse_clause for the inner
    amendment grammar, which is exactly the same grammar as enacted-amendment
    text.  We strip the "Ehdotetaan, että" preamble before handing off.

    ``governing_statute_id`` is the amended act's id resolved from the clause's
    governing citation OUTSIDE the clause text (the bill title heading), used ONLY as a
    fallback when the clause itself carries no in-text citation.  It propagates the
    once-named statute to every op in the clause so a heading-only amendment does not
    lower to bare-ref ops that can never match the PDF witness.
    """
    from lawvm.finland.johtolause.api import parse_clause

    ops: list[BranchProposedOp] = []
    findings: list[object] = []

    # Strip preamble: "Ehdotetaan, että" wrapper
    preamble, inner_text = _strip_preamble(clause_text)

    # Extract statute citation from the clause text
    cite_result = _extract_statute_citation(inner_text)
    if cite_result is None:
        # Try the original (preamble might have statute context)
        cite_result = _extract_statute_citation(clause_text)

    # Fall back to the governing citation (bill-title heading) when the clause omits the
    # number in-text; never overrides an in-clause citation.
    statute_id = cite_result[0] if cite_result else governing_statute_id

    # Detect proposal-relative address before parsing
    is_prop_rel = _is_proposal_relative_address(inner_text)

    # Parse the inner clause using the existing johtolause parser
    result = parse_clause(inner_text)

    if result.is_failed or not result.parsed_ops:
        # Emit parse recovery finding (AGENTS.md §1.8)
        detail = result.parse_error or "no_ops_parsed"
        findings.append(
            BranchParseRecovery(
                rule_id="HE_BRANCH.CLAUSE_PARSE_ERROR",
                op_index=op_index_start,
                clause_text=clause_text[:500],
                reason="johtolause parser returned no ops or error",
                detail=detail[:300],
                strict_disposition="abort" if strict else "record",
            )
        )
        return ops, findings

    # Lower each ParsedOp to a BranchProposedOp
    for idx, parsed_op in enumerate(result.parsed_ops):
        op_index = op_index_start + idx
        provision_ref = _build_provision_ref(statute_id, parsed_op)

        # Determine target resolution status (AGENTS.md §1.1)
        if is_prop_rel or getattr(parsed_op, "verb", "") == "L":
            # Insert/new-section ops are often proposal-relative
            target_resolution = BranchTargetResolution.PROPOSAL_RELATIVE
            is_pr = True
            findings.append(
                BranchTargetResolutionFinding(
                    rule_id="HE_BRANCH.PROPOSAL_RELATIVE_TARGET",
                    op_index=op_index,
                    target_provision_ref=provision_ref,
                    target_statute_id=statute_id,
                    reason=(
                        "INSERT op introduces a provision not yet in current statute; "
                        "target_resolution=proposal_relative is expected behavior"
                    ),
                    is_proposal_relative=True,
                    strict_disposition="record",
                )
            )
        elif not statute_id:
            target_resolution = BranchTargetResolution.UNRESOLVED
            is_pr = False
            findings.append(
                BranchTargetResolutionFinding(
                    rule_id="HE_BRANCH.NO_STATUTE_CITATION",
                    op_index=op_index,
                    target_provision_ref=provision_ref,
                    target_statute_id="",
                    reason="no statute citation found in clause text",
                    is_proposal_relative=False,
                    strict_disposition="record",
                )
            )
        else:
            target_resolution = BranchTargetResolution.RESOLVED
            is_pr = False

        # Confidence: full parse without error → 1.0; partial → 0.5
        confidence = 0.9 if not result.residuals else 0.6

        operation_kind = _verb_to_operation_kind(getattr(parsed_op, "verb", "M"))

        payload_summary = _build_payload_summary(clause_text, parsed_op)

        ops.append(
            BranchProposedOp(
                op_index=op_index,
                operation_kind=operation_kind,
                target_provision_ref=provision_ref,
                target_statute_id=statute_id,
                payload_summary=payload_summary,
                source_he_id=source_he_id,
                branch_id=branch_id,
                source_span_text=clause_text[:500],
                source_span_preamble=preamble,
                target_resolution=target_resolution,
                parse_confidence=confidence,
                is_proposal_relative=is_pr,
            )
        )

    return ops, findings


# ---------------------------------------------------------------------------
# EnactingClauseRecognizer — primary extractor for modern Finnish HEs
# ---------------------------------------------------------------------------


def _extract_enacting_clauses_modern(
    main_body: etree._Element,
    name_map: Optional[dict[str, str]] = None,
) -> list[tuple[str, str, str]]:
    """Extract amendment directives from modern Finnish HE structure.

    Modern HEs (2020+) use:
      mainBody
        hcontainer[name='bills']
          hcontainer[name='bill']
            hcontainer[name='enactingClause']  ← one amendment directive

    Each enactingClause holds exactly one johtolause (amendment verb phrase),
    e.g. "Eduskunnan päätöksen mukaisesti muutetaan lannoitelain (711/2022)
    7 §:n 3 momentti, ...".

    Returns list of (clause_text, context, governing_statute_id) tuples.  The third
    element is the amended act's id resolved from the bill TITLE heading via ``name_map``
    (empty when ``name_map`` is None or the act cannot be resolved) — it rescues clauses
    that name the statute only in the heading, not in the directive.
    Per AGENTS.md §1.13: single-pass, structured, not N regex passes.
    """
    clauses: list[tuple[str, str, str]] = []
    for el in main_body.iter():
        name_attr = el.attrib.get("name", "")
        if name_attr == _ENACTING_CLAUSE_NAME:
            text = _element_text(el)
            if text:
                # Context: try to find bill index for traceability
                parent = el.getparent()
                parent_eid = parent.attrib.get("eId", "") if parent is not None else ""
                context = f"enactingClause:{parent_eid or 'unknown'}"
                gov_id = (
                    _governing_statute_id_from_bill(el, name_map)
                    if name_map is not None
                    else ""
                )
                clauses.append((text, context, gov_id))
    return clauses


# ---------------------------------------------------------------------------
# Enactment section extractor (legacy path)
# ---------------------------------------------------------------------------


def _extract_enactment_clauses_legacy(
    main_body: etree._Element,
) -> list[tuple[str, str, str]]:
    """Legacy extractor: used when no enactingClause elements are found.

    Walks mainBody looking for hcontainer[name='enactment-text'] or similar
    containers, then extracts <section> children as clauses.

    This path handles:
    - Synthetic test fixtures using hcontainer[name='enactment-text']
    - Older HE formats that don't use the bills/bill/enactingClause structure

    Returns list of (clause_text, section_name, governing_statute_id) tuples.  The legacy
    section format carries its statute citation inline, so the governing-id slot is always
    "" (heading-based resolution is a modern-structure concern only).
    """
    clauses: list[tuple[str, str, str]] = []

    def _walk_body(node: etree._Element) -> None:
        lname = _localname(node)
        name_attr = node.attrib.get("name", "")

        if lname == "section":
            # A section element holds one amendment clause in legacy format.
            # NOTE: In modern HEs, section elements inside statuteProvisionsWrapper
            # are PAYLOAD content (replacement statute text), not directives.
            # The modern path (enactingClause) avoids this entirely.
            text = _element_text(node)
            if text:
                clauses.append((text, f"section:{name_attr or 'unnamed'}", ""))
            return

        if lname in ("hcontainer", "div", "blockContainer"):
            # Skip payload containers that hold statute text, not directives.
            # These appear in modern HEs inside bill elements.
            if name_attr in _PAYLOAD_CONTAINER_NAMES:
                return

            text_sample = ""
            # Build text sample from first 300 chars for recognizer
            for subnode in node.iter():
                for txt in (subnode.text or "", subnode.tail or ""):
                    text_sample += txt
                    if len(text_sample) > 300:
                        break
                if len(text_sample) > 300:
                    break
            text_sample = re.sub(r"\s+", " ", text_sample).strip()

            if _is_enactment_section(node, text_sample):
                # Recurse into this section to find individual clauses
                for child in node:
                    _walk_body(child)
                return

            # Recurse into non-enactment hcontainers (may have nested enactment ones)
            for child in node:
                _walk_body(child)
            return

        # Other elements: recurse
        for child in node:
            _walk_body(child)

    _walk_body(main_body)
    return clauses


def _extract_enactment_clauses(
    root: etree._Element,
    name_map: Optional[dict[str, str]] = None,
) -> list[tuple[str, str, str]]:
    """Extract (clause_text, section_context, governing_statute_id) triples from an HE body.

    Resolution order per EnactingClauseRecognizer grammar (AGENTS.md §1.13):

    1. Primary (modern Finnish HE structure, 2020+):
       Find all hcontainer[name='enactingClause'] elements.  These are the
       amendment directives (johtolause) in modern HEs.  Each holds exactly
       one verb phrase targeting one statute.

    2. Fallback (legacy / synthetic test format):
       Walk hcontainer[name='enactment-text'] and extract <section> children.
       Used for pre-modern HEs and synthetic test fixtures.

    The primary path is tried first.  The fallback is used only when no
    enactingClause elements are found.

    Returns list of (clause_text, context_label, governing_statute_id) triples.
    """
    main_body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if main_body is None:
        return []

    # Primary: modern enactingClause structure
    clauses = _extract_enacting_clauses_modern(main_body, name_map)
    if clauses:
        return clauses

    # Fallback: legacy section-based extraction
    return _extract_enactment_clauses_legacy(main_body)


# ---------------------------------------------------------------------------
# Main parser entry point
# ---------------------------------------------------------------------------


def parse_he_branch(
    xml_bytes: bytes,
    *,
    he_year: int,
    he_number: int,
    he_id: str,
    lang: str = "fin",
    strict: bool = False,
) -> HEParsedBranch:
    """Parse one HE language variant to typed HEParsedBranch.

    This is the main entry point for feature #8 parser-side.

    Parameters
    ----------
    xml_bytes:
        Raw AKN XML bytes for the HE's main.xml.
    he_year, he_number:
        Year and number from the farchive metadata.
    he_id:
        Human-readable HE ID, e.g. 'HE 184/2024 vp'.
    lang:
        Language variant code, e.g. 'fin'.
    strict:
        If True, emit strict_disposition='abort' in parse failures.

    Returns
    -------
    HEParsedBranch with typed proposed_ops and findings.

    AGENTS.md §1.7: multiple HEs with the same target at the same voimaantulo
    remain as separate BranchProposedOp records; no silent merge.
    """
    branch_id = f"fi/he/{he_year}/{he_number}"

    # Parse XML
    root: Optional[etree._Element] = None
    parse_err: Optional[str] = None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        parse_err = str(exc)

    if root is None or parse_err is not None:
        return HEParsedBranch(
            branch_id=branch_id,
            he_id=he_id,
            he_year=he_year,
            he_number=he_number,
            proposed_voimaantulo=None,
            proposed_ops=(),
            target_statute_ids=(),
            parse_status=HEParseStatus.FAILED,
            parse_findings=(
                BranchParseRecovery(
                    rule_id="HE_BRANCH.XML_PARSE_ERROR",
                    op_index=0,
                    clause_text="",
                    reason="XML parse error in HE main.xml",
                    detail=parse_err or "unknown",
                    phase="acquisition",
                    strict_disposition="abort" if strict else "record",
                ),
            ),
            enactment_sections_found=0,
            clauses_attempted=0,
            clauses_succeeded=0,
        )

    # Extract full body text for voimaantulo search
    body_text = ""
    main_body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if main_body is not None:
        body_text = _element_text(main_body)

    proposed_voimaantulo = _extract_proposed_voimaantulo(body_text)

    # Build the per-HE statute name→id map so a clause naming its amended act only in the
    # bill title (number in the perustelut) still resolves to a full-ref op.
    statute_name_map = _build_he_statute_name_map(body_text)

    # Extract enactment clauses via EnactmentSectionRecognizer
    raw_clauses = _extract_enactment_clauses(root, statute_name_map)
    enactment_count = len(raw_clauses)

    if enactment_count == 0:
        # No enactment text found — NOT_APPLICABLE (treaty, budget, etc.)
        return HEParsedBranch(
            branch_id=branch_id,
            he_id=he_id,
            he_year=he_year,
            he_number=he_number,
            proposed_voimaantulo=proposed_voimaantulo,
            proposed_ops=(),
            target_statute_ids=(),
            parse_status=HEParseStatus.NOT_APPLICABLE,
            parse_findings=(),
            enactment_sections_found=0,
            clauses_attempted=0,
            clauses_succeeded=0,
        )

    # Parse each clause
    all_ops: list[BranchProposedOp] = []
    all_findings: list[object] = []
    clauses_succeeded = 0

    for clause_text, _section_ctx, governing_statute_id in raw_clauses:
        if not clause_text.strip():
            continue
        op_index_start = len(all_ops)
        new_ops, new_findings = _parse_one_clause(
            clause_text,
            op_index_start=op_index_start,
            source_he_id=he_id,
            branch_id=branch_id,
            strict=strict,
            governing_statute_id=governing_statute_id,
        )
        all_ops.extend(new_ops)
        all_findings.extend(new_findings)
        if new_ops:
            clauses_succeeded += 1

    # Deduplicate and sort target statute IDs
    statute_ids = sorted(set(op.target_statute_id for op in all_ops if op.target_statute_id))

    # Determine parse status
    clauses_attempted = len([c for c, _, _ in raw_clauses if c.strip()])
    if clauses_attempted == 0:
        status = HEParseStatus.NOT_APPLICABLE
    elif clauses_succeeded == 0:
        status = HEParseStatus.FAILED
    elif clauses_succeeded < clauses_attempted:
        status = HEParseStatus.PARTIAL
    else:
        status = HEParseStatus.FULL

    return HEParsedBranch(
        branch_id=branch_id,
        he_id=he_id,
        he_year=he_year,
        he_number=he_number,
        proposed_voimaantulo=proposed_voimaantulo,
        proposed_ops=tuple(all_ops),
        target_statute_ids=tuple(statute_ids),
        parse_status=status,
        parse_findings=tuple(all_findings),
        enactment_sections_found=enactment_count,
        clauses_attempted=clauses_attempted,
        clauses_succeeded=clauses_succeeded,
    )


# ---------------------------------------------------------------------------
# Parquet row projection helpers
# ---------------------------------------------------------------------------


def branch_op_to_parquet_row(op: BranchProposedOp) -> dict[str, object]:
    """Project a BranchProposedOp to a flat Parquet row dict.

    Columns per the feature brief §3:
    branch_id / he_id / proposed_voimaantulo / op_index /
    operation_kind / target_provision_ref / payload_summary /
    source_span_* / parse_status / parse_confidence.

    Note: proposed_voimaantulo and parse_status must be filled in by the
    caller (they live on HEParsedBranch, not on individual ops).
    """
    return {
        "branch_id": op.branch_id,
        "he_id": op.source_he_id,
        "op_index": op.op_index,
        "operation_kind": op.operation_kind,
        "target_provision_ref": op.target_provision_ref,
        "target_statute_id": op.target_statute_id,
        "payload_summary": op.payload_summary,
        "source_span_text": op.source_span_text,
        "source_span_preamble": op.source_span_preamble,
        "parse_confidence": op.parse_confidence,
        "target_resolution": op.target_resolution.value,
        "is_proposal_relative": op.is_proposal_relative,
    }


def branch_to_parquet_rows(
    branch: HEParsedBranch,
) -> list[dict[str, object]]:
    """Project an HEParsedBranch to a list of Parquet rows.

    Each BranchProposedOp becomes one row; HE-level fields are repeated.
    """
    voimaantulo_str = branch.proposed_voimaantulo.isoformat() if branch.proposed_voimaantulo else None
    rows = []
    for op in branch.proposed_ops:
        row = branch_op_to_parquet_row(op)
        row["proposed_voimaantulo"] = voimaantulo_str
        row["parse_status"] = branch.parse_status.value
        row["he_year"] = branch.he_year
        row["he_number"] = branch.he_number
        rows.append(row)
    return rows
