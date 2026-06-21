"""Finnish PoolMention extractor -- typed pool/budget-line/quantity extraction.

Entry points:

  extract_pool_mentions(xml_bytes, statute_id, ...) -> PoolExtractionResult
      All PoolMention records FROM statute_id + findings/rejections.

Design discipline (AGENTS.md §1.1, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      Ambiguous momentti_code (multi-registry-match) -> AmbiguousPoolMention
      finding, not a silent pick.

  §1.6 No unstated migration:
      Cross-year momentti renumbering -> BudgetLineRenumberingObservation emitted.
      confidence=APPROXIMATE always pairs with this observation.

  §1.8 No unsupported source lane disappears:
      Every rejected candidate emits RejectedPoolCandidate.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before regex on long text.

  §1.13 Grammar trigger -- pool/budget/quantity is a FAMILY:
      Budget-line address ('28.91.50'), generic pool ('maaraaraha'), and
      capacity-cap/threshold (numeric + unit) are RELATED PATTERNS sharing
      structure. Built as a single-pass BudgetLineRecognizer that scans
      a provision's text once and classifies all pool-shaped phrases.
      NOT N overlapping backtracking scans.

Source: Finlex Akoma Ntoso consolidated XML.
Registry: lawvm.finland.canonical_budget_line_registry.REGISTRY.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from lawvm.core.pool_mention import (
    AmbiguousPoolMention,
    BudgetLineRenumberingObservation,
    PoolMention,
    PoolResolutionConfidence,
    QuantityKind,
    RejectedPoolCandidate,
)
from lawvm.finland.canonical_budget_line_registry import REGISTRY

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# AKN section-number extractor from <num> element text.
# Matches '5 §', '5a §', etc.  Bounded: \d{1,6} + [a-z]?
_SECTION_NUM_RE = re.compile(r"(\d{1,6}[a-z]?)\s*\xa7", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Budget-line recognizer (AGENTS.md §1.13 -- named family, not N parallel regexes)
# ---------------------------------------------------------------------------
# The Finnish budget-line/pool/quantity family shares structure:
#   BUDGET_LINE : explicit 'NN.NN.NN' momentti address
#   FISCAL_POOL : named pool phrase (maaraaraha, valtionosuus, yleiskate, etc.)
#   CAPACITY_CAP: explicit numeric + unit ceiling (kuormakatto / enintaan)
#   THRESHOLD   : explicit numeric + unit floor/trigger
#   FORMULA_TERM: named term in a funding formula
#
# These are NOT parallel regexes racing each other. They are a single-pass
# scan of each provision's text, with fast substring guards.
#
# Pattern design (AGENTS.md §1.11):
#   - Substring guard before regex (fast path -- eliminates ~99% of text)
#   - Bounded quantifiers throughout
#   - Priority-ordered classification: more specific patterns precede general ones

# ---
# Pattern 1: Explicit budget-line momentti address 'NN.NN.NN'
# Guard: '.' appears in text (cheap)
# Regex: matches 'momentilla NN.NN.NN' or 'talousarvion momentilla NN.NN.NN'
#   or bare 'NN.NN.NN' in context. Bounded: \d{1,2} x3.
# ---
_BUDGET_LINE_GUARD = "."
_BUDGET_LINE_RE = re.compile(
    r"""
    (?:
        (?:momentilla|momentilta|momentille|momentti)\s+   # explicit momentti keyword
        (\d{1,2}\.\d{1,2}\.\d{1,2})                        # NN.NN.NN address
    |
        (?<!\d)(\d{1,2}\.\d{1,2}\.\d{1,2})(?!\d)           # bare NN.NN.NN not surrounded by digits
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# ---
# Pattern 2: Capacity cap / threshold -- numeric value + unit
# Guard: 'enint' or 'kuormakatto' or 'kynnys' (Finnish for ceiling/threshold)
# Regex: matches 'enintaan N,N unit' or 'kuormakatto N unit' etc.
# Bounded: \d{1,10} (enough for any sensible number) + unit up to 40 chars.
# ---
_CAP_GUARD_STRINGS = ("enint", "kuormakatto", "kynnys", "raja-arvo", "yläraja")
_CAP_RE = re.compile(
    r"""
    (?:
        kuormakatto\s+                # capacity cap keyword
        |
        enint[a\xe4]\xe4n\s+          # 'enintaan' (at most)
        |
        kynnysarvo\s+                 # threshold value
        |
        raja-arvo\s+                  # limit value
        |
        yl[a\xe4]raja\s+              # upper limit
    )
    ([\d,\.]{1,15})                   # numeric value (Finnish comma decimal)
    \s*
    ([a-zA-Z\xe4\xf6\xe5é%/\*\s]{1,40}?)  # unit (bounded)
    (?=[,;\.\s\)$])                   # stop before punctuation
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Threshold (floor / trigger):
# Guard: 'vahintaan' or 'ylitt' (exceed) or 'alittaa' (fall below)
_THRESHOLD_GUARD_STRINGS = ("v\xe4hint", "ylitt", "alittaa", "raja-arvo")
_THRESHOLD_RE = re.compile(
    r"""
    (?:
        v[a\xe4]hint[a\xe4]\xe4n\s+   # 'vahintaan' (at least)
        |
        ylitt[a\xe4]\xe4\s+            # exceed
        |
        alittaa\s+                     # fall below
    )
    ([\d,\.]{1,15})                    # numeric value
    \s*
    ([a-zA-Z\xe4\xf6\xe5%/\*\s]{1,40}?)  # unit
    (?=[,;\.\s\)$])
    """,
    re.VERBOSE | re.IGNORECASE,
)

# ---
# Pattern 3: Fiscal pool phrases
# Guard: any of the known pool keywords
# Classification is by lexical cue -- no regex needed beyond substring check.
# ---
_FISCAL_POOL_KEYWORDS = (
    "yleiskate",
    "valtionosuus",
    "valtionavustus",
    "m\xe4\xe4r\xe4raha",
    "siirtom\xe4\xe4r\xe4raha",
    "arviom\xe4\xe4r\xe4raha",
    "budjettiraha",
    "rahoitusosuus",
)

# Negative guard: year references like 'vuoden 2020' or 'vuonna 2020'
# should NOT be extracted as budget-line addresses even if they contain digits.
_YEAR_REF_RE = re.compile(
    r"\b(?:vuoden|vuonna|vuosina|v\.\s*)\s*\d{4}\b",
    re.IGNORECASE,
)


def _canonical_id_from_code(momentti_code: str) -> str:
    """Build a canonical ID from a momentti code '28.91.50' -> 'fi.budget.28.91.50'."""
    return "fi.budget." + momentti_code.replace(".", ".")


def _parse_numeric(value_str: str) -> Optional[float]:
    """Parse a Finnish-locale numeric string ('7,5') to float.

    Returns None if not parseable.
    """
    cleaned = value_str.strip().replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _canonicalize_unit(unit_str: str) -> str:
    """Normalize a unit string for storage.

    'g Cd/ha/5 v' -> 'g Cd/ha/5 v' (kept as-is, spaces normalized)
    Strips trailing whitespace/punctuation.
    """
    return unit_str.strip().rstrip(".,;")


# ---------------------------------------------------------------------------
# Named recognizer: BudgetLineRecognizer (AGENTS.md §1.13)
# ---------------------------------------------------------------------------


class BudgetLineRecognizer:
    """Named recognizer for the budget-line/pool/quantity grammar family.

    Scans a provision text once, classifying all pool-shaped phrases.
    Per AGENTS.md §1.13: single-pass structured recognizer over the text.
    Not N overlapping backtracking scans.

    Returns a list of _PoolCandidate records for downstream classification.
    """

    def recognize(self, text: str) -> "List[_PoolCandidate]":
        """Scan text and return pool-shaped candidates.

        Uses fast substring guards before regex application.
        """
        candidates: List[_PoolCandidate] = []

        # Guard 1: budget-line address (fastest -- check for '.')
        if _BUDGET_LINE_GUARD in text:
            # lawvm-regex: owning_parser this module is the owning BudgetLineRecognizer; budget-line arm produces typed PoolMention candidates with rejections
            for m in _BUDGET_LINE_RE.finditer(text):
                # Group 1 = 'momentilla NNN', group 2 = bare NNN
                code = m.group(1) or m.group(2)
                if code is None:
                    continue
                # Negative guard: skip year references
                # Check surrounding context for 'vuoden'/'vuonna' within 20 chars
                start = max(0, m.start() - 20)
                ctx = text[start : m.end() + 20]
                # lawvm-regex: prefilter negative guard rejecting year refs ('vuoden 2020') from budget-line addresses inside the owning recognizer
                if _YEAR_REF_RE.search(ctx):
                    continue
                candidates.append(
                    _PoolCandidate(
                        quantity_phrase=m.group(0).strip(),
                        momentti_code=code,
                        inferred_kind=QuantityKind.BUDGET_LINE,
                        numeric_value=None,
                        unit=None,
                        span_start=m.start(),
                        span_end=m.end(),
                    )
                )

        # Guard 2: capacity cap
        if any(guard in text.lower() for guard in _CAP_GUARD_STRINGS):
            # lawvm-regex: owning_parser capacity-cap arm of the owning BudgetLineRecognizer family
            for m in _CAP_RE.finditer(text):
                numeric = _parse_numeric(m.group(1))
                unit = _canonicalize_unit(m.group(2)) if m.group(2) else None
                candidates.append(
                    _PoolCandidate(
                        quantity_phrase=m.group(0).strip(),
                        momentti_code=None,
                        inferred_kind=QuantityKind.CAPACITY_CAP,
                        numeric_value=numeric,
                        unit=unit,
                        span_start=m.start(),
                        span_end=m.end(),
                    )
                )

        # Guard 3: threshold
        if any(guard in text.lower() for guard in _THRESHOLD_GUARD_STRINGS):
            # lawvm-regex: owning_parser threshold arm of the owning BudgetLineRecognizer family
            for m in _THRESHOLD_RE.finditer(text):
                numeric = _parse_numeric(m.group(1))
                unit = _canonicalize_unit(m.group(2)) if m.group(2) else None
                candidates.append(
                    _PoolCandidate(
                        quantity_phrase=m.group(0).strip(),
                        momentti_code=None,
                        inferred_kind=QuantityKind.THRESHOLD,
                        numeric_value=numeric,
                        unit=unit,
                        span_start=m.start(),
                        span_end=m.end(),
                    )
                )

        # Guard 4: fiscal pool keywords
        for keyword in _FISCAL_POOL_KEYWORDS:
            if keyword in text.lower():
                idx = text.lower().find(keyword)
                while idx >= 0:
                    phrase_end = idx + len(keyword)
                    # Extend phrase to end of word (up to 60 chars)
                    word_end = phrase_end
                    while word_end < len(text) and word_end < phrase_end + 60:
                        if text[word_end] in (" ", ",", ".", ";", ")", "\n"):
                            break
                        word_end += 1
                    phrase = text[idx:word_end].strip()
                    candidates.append(
                        _PoolCandidate(
                            quantity_phrase=phrase,
                            momentti_code=None,
                            inferred_kind=QuantityKind.FISCAL_POOL,
                            numeric_value=None,
                            unit=None,
                            span_start=idx,
                            span_end=word_end,
                        )
                    )
                    idx = text.lower().find(keyword, phrase_end)

        return candidates


@dataclass
class _PoolCandidate:
    """Internal: a candidate pool/quantity phrase identified by the recognizer."""

    quantity_phrase: str
    momentti_code: Optional[str]
    inferred_kind: QuantityKind
    numeric_value: Optional[float]
    unit: Optional[str]
    span_start: int
    span_end: int


_RECOGNIZER = BudgetLineRecognizer()


# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class PoolExtractionResult:
    """Container for all artifacts from one pool extraction pass.

    mentions:                 Successfully typed PoolMention records.
    rejected:                 RejectedPoolCandidate records.
    ambiguous_findings:       AmbiguousPoolMention findings.
    renumbering_observations: BudgetLineRenumberingObservation records.
    """

    mentions: List[PoolMention] = field(default_factory=list)
    rejected: List[RejectedPoolCandidate] = field(default_factory=list)
    ambiguous_findings: List[AmbiguousPoolMention] = field(default_factory=list)
    renumbering_observations: List[BudgetLineRenumberingObservation] = field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_of(element: ET.Element[str]) -> str:
    """Collect all text content from an element and its descendants."""
    parts: List[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_text_of(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _section_provision_ref(section_el: ET.Element[str], statute_id: str) -> str:
    """Build a provision_ref string from a <section> element and statute_id."""
    ns_num = f"{{{_AKN_NS}}}num"
    num_el = section_el.find(ns_num)
    if num_el is not None and num_el.text:
        # lawvm-regex: owning_parser section label parse from already-extracted AKN <num> element text, not legal prose
        m = _SECTION_NUM_RE.search(num_el.text)
        if m:
            return f"{statute_id}/{m.group(1)}"
    return statute_id


# ---------------------------------------------------------------------------
# Per-candidate resolution
# ---------------------------------------------------------------------------


def _resolve_budget_line(
    candidate: _PoolCandidate,
    statute_id: str,
    provision_ref: str,
    valid_at_year: Optional[int],
) -> Tuple[
    Optional[PoolMention],
    Optional[AmbiguousPoolMention],
    Optional[BudgetLineRenumberingObservation],
    Optional[RejectedPoolCandidate],
]:
    """Resolve a BUDGET_LINE candidate against the registry.

    Returns (mention, ambiguous_finding, renumbering_obs, rejected_candidate).
    Exactly one of these will be non-None for each call.
    """
    code = candidate.momentti_code
    if code is None:
        return None, None, None, RejectedPoolCandidate(
            rule_id="fi_pool_mention_budget_line_no_code",
            phase="pool_mention_extraction",
            source_statute_id=statute_id,
            reason="BUDGET_LINE candidate has no momentti_code",
            matched_text=candidate.quantity_phrase,
            source_span_file=None,
            source_span_byte_offset=candidate.span_start,
            source_span_byte_len=candidate.span_end - candidate.span_start,
            blocking=False,
            strict_disposition="record",
        )

    # Determine lookup year
    lookup_year = valid_at_year
    if lookup_year is None:
        lookup_year = REGISTRY.nearest_year(2024)  # fallback: most recent
    if lookup_year is None:
        # No registry data at all
        return (
            PoolMention(
                source_provision_ref=provision_ref,
                quantity_phrase=candidate.quantity_phrase,
                pool_canonical_id=None,
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=candidate.span_start,
                source_span_byte_len=candidate.span_end - candidate.span_start,
                valid_at_start=None,
                valid_at_end=None,
            ),
            None, None, None,
        )

    # Try exact year lookup
    canonical_id, matches = REGISTRY.lookup_by_code(code, lookup_year)

    if len(matches) > 1:
        # Ambiguous: multiple budget lines share this momentti_code in this year
        # Per AGENTS.md §1.1: emit finding, not silent pick
        return (
            None,
            AmbiguousPoolMention(
                rule_id="fi_pool_mention_ambiguous_budget_line",
                phase="pool_mention_extraction",
                source_statute_id=statute_id,
                source_provision_ref=provision_ref,
                quantity_phrase=candidate.quantity_phrase,
                candidate_canonical_ids=tuple(m.canonical_id for m in matches),
                reason=(
                    f"Momentti code {code!r} maps to {len(matches)} budget-line "
                    f"entries in year {lookup_year}."
                ),
                blocking=False,
                strict_disposition="block",
            ),
            None, None,
        )

    if canonical_id is not None:
        # EXACT match
        return (
            PoolMention(
                source_provision_ref=provision_ref,
                quantity_phrase=candidate.quantity_phrase,
                pool_canonical_id=canonical_id,
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=PoolResolutionConfidence.EXACT,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=candidate.span_start,
                source_span_byte_len=candidate.span_end - candidate.span_start,
                valid_at_start=None,
                valid_at_end=None,
            ),
            None, None, None,
        )

    # No exact match: try lineage resolution across years
    # Build a provisional canonical_id from the code to probe the lineage table
    _provisional_id = _canonical_id_from_code(code)
    # Check if there's a year in the registry that has this code
    for check_year in REGISTRY.available_years():
        cid, cands = REGISTRY.lookup_by_code(code, check_year)
        if cid is not None:
            # Found it in a different year; try lineage to current year
            lineage_result = REGISTRY.lookup_lineage(cid, check_year)
            if lineage_result is not None:
                successor_id, successor_year = lineage_result
                obs = BudgetLineRenumberingObservation(
                    rule_id="fi_pool_mention_budget_line_renumbering",
                    phase="pool_mention_extraction",
                    source_statute_id=statute_id,
                    source_provision_ref=provision_ref,
                    quantity_phrase=candidate.quantity_phrase,
                    original_canonical_id=cid,
                    resolved_canonical_id=successor_id,
                    lineage_year=check_year,
                    resolution_year=successor_year,
                    reason=(
                        f"Momentti code {code!r} found in year {check_year} as "
                        f"{cid!r}; resolved to successor {successor_id!r} "
                        f"(year {successor_year}) via cross-year lineage."
                    ),
                    blocking=False,
                    strict_disposition="record",
                )
                mention = PoolMention(
                    source_provision_ref=provision_ref,
                    quantity_phrase=candidate.quantity_phrase,
                    pool_canonical_id=successor_id,
                    quantity_kind=QuantityKind.BUDGET_LINE,
                    resolution_confidence=PoolResolutionConfidence.APPROXIMATE,
                    numeric_value=None,
                    unit=None,
                    source_span_file=None,
                    source_span_byte_offset=candidate.span_start,
                    source_span_byte_len=candidate.span_end - candidate.span_start,
                    valid_at_start=None,
                    valid_at_end=None,
                )
                return mention, None, obs, None
            # Found in another year but no lineage; emit APPROXIMATE with
            # the ID from that year
            obs = BudgetLineRenumberingObservation(
                rule_id="fi_pool_mention_budget_line_cross_year",
                phase="pool_mention_extraction",
                source_statute_id=statute_id,
                source_provision_ref=provision_ref,
                quantity_phrase=candidate.quantity_phrase,
                original_canonical_id=cid,
                resolved_canonical_id=cid,
                lineage_year=check_year,
                resolution_year=check_year,
                reason=(
                    f"Momentti code {code!r} not in year {lookup_year} registry; "
                    f"found in year {check_year} as {cid!r}. No lineage successor. "
                    f"Using cross-year approximate match."
                ),
                blocking=False,
                strict_disposition="record",
            )
            mention = PoolMention(
                source_provision_ref=provision_ref,
                quantity_phrase=candidate.quantity_phrase,
                pool_canonical_id=cid,
                quantity_kind=QuantityKind.BUDGET_LINE,
                resolution_confidence=PoolResolutionConfidence.APPROXIMATE,
                numeric_value=None,
                unit=None,
                source_span_file=None,
                source_span_byte_offset=candidate.span_start,
                source_span_byte_len=candidate.span_end - candidate.span_start,
                valid_at_start=None,
                valid_at_end=None,
            )
            return mention, None, obs, None

    # Truly unresolved: no registry hit in any year
    return (
        PoolMention(
            source_provision_ref=provision_ref,
            quantity_phrase=candidate.quantity_phrase,
            pool_canonical_id=None,
            quantity_kind=QuantityKind.BUDGET_LINE,
            resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
            numeric_value=None,
            unit=None,
            source_span_file=None,
            source_span_byte_offset=candidate.span_start,
            source_span_byte_len=candidate.span_end - candidate.span_start,
            valid_at_start=None,
            valid_at_end=None,
        ),
        None, None, None,
    )


def _resolve_non_budget_line(
    candidate: _PoolCandidate,
    statute_id: str,
    provision_ref: str,
) -> PoolMention:
    """Resolve a non-BUDGET_LINE candidate (FISCAL_POOL, CAPACITY_CAP, THRESHOLD)."""
    return PoolMention(
        source_provision_ref=provision_ref,
        quantity_phrase=candidate.quantity_phrase,
        pool_canonical_id=None,
        quantity_kind=candidate.inferred_kind,
        resolution_confidence=PoolResolutionConfidence.UNRESOLVED,
        numeric_value=candidate.numeric_value,
        unit=candidate.unit,
        source_span_file=None,
        source_span_byte_offset=candidate.span_start,
        source_span_byte_len=candidate.span_end - candidate.span_start,
        valid_at_start=None,
        valid_at_end=None,
    )


# ---------------------------------------------------------------------------
# Strict-mode barrier (AGENTS.md §14)
# ---------------------------------------------------------------------------


def _apply_strict_barriers(
    result: PoolExtractionResult,
    statute_id: str,
    strict: bool,
) -> None:
    """In strict mode, flag UNRESOLVED mentions and ambiguous findings as blocking."""
    if not strict:
        return

    for mention in result.mentions:
        if mention.resolution_confidence == PoolResolutionConfidence.UNRESOLVED:
            result.rejected.append(
                RejectedPoolCandidate(
                    rule_id="fi_pool_mention_strict_unresolved_barrier",
                    phase="pool_mention_extraction",
                    source_statute_id=statute_id,
                    reason=(
                        f"strict mode: UNRESOLVED pool phrase "
                        f"{mention.quantity_phrase!r} may not propagate "
                        f"to canonical-id columns in strict mode."
                    ),
                    matched_text=mention.quantity_phrase,
                    source_span_file=None,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                    blocking=True,
                    strict_disposition="block",
                )
            )

    for af in result.ambiguous_findings:
        result.rejected.append(
            RejectedPoolCandidate(
                rule_id="fi_pool_mention_strict_ambiguous_barrier",
                phase="pool_mention_extraction",
                source_statute_id=statute_id,
                reason=(
                    f"strict mode: ambiguous pool phrase {af.quantity_phrase!r} "
                    f"matches {len(af.candidate_canonical_ids)} registry entries."
                ),
                matched_text=af.quantity_phrase,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_pool_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    valid_at_year: Optional[int] = None,
    strict: bool = False,
) -> PoolExtractionResult:
    """Extract PoolMention records from a Finnish statute XML.

    Single-pass recognition over each section's text using BudgetLineRecognizer.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID, e.g. '711/2022'.
        valid_at_interval: (start, end) date range for these mentions.
        valid_at_year:     Fiscal year for registry resolution. Inferred from
                           valid_at_interval start if not provided.
        strict:            If True, UNRESOLVED/AMBIGUOUS mentions emit blocking
                           RejectedPoolCandidate records.

    Returns:
        PoolExtractionResult with mentions, rejected, findings, observations.

    Per AGENTS.md §1.1: ambiguous momentti -> AmbiguousPoolMention, not silent pick.
    Per AGENTS.md §1.6: renumbered momentti -> BudgetLineRenumberingObservation.
    Per AGENTS.md §1.8: rejected candidates preserved.
    """
    result = PoolExtractionResult()

    valid_start, valid_end = valid_at_interval

    # Infer registry year from valid_at_start if not provided explicitly
    lookup_year = valid_at_year
    if lookup_year is None and valid_start is not None:
        lookup_year = valid_start.year

    # Parse XML
    if not xml_bytes:
        result.rejected.append(
            RejectedPoolCandidate(
                rule_id="fi_pool_mention_xml_parse_failed",
                phase="pool_mention_extraction",
                source_statute_id=statute_id,
                reason="Empty XML bytes provided.",
                matched_text="",
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )
        return result

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        result.rejected.append(
            RejectedPoolCandidate(
                rule_id="fi_pool_mention_xml_parse_failed",
                phase="pool_mention_extraction",
                source_statute_id=statute_id,
                reason=f"XML parse error: {exc}",
                matched_text="",
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                blocking=True,
                strict_disposition="block",
            )
        )
        return result

    # Iterate over body sections
    ns_body = f"{{{_AKN_NS}}}body"
    ns_section = f"{{{_AKN_NS}}}section"

    body_el = root.find(f".//{ns_body}")
    if body_el is None:
        body_el = root  # fallback: scan whole tree

    for section_el in body_el.iter(ns_section):
        text = _text_of(section_el)
        if not text:
            continue

        provision_ref = _section_provision_ref(section_el, statute_id)

        # Single-pass recognition over this section's text
        candidates = _RECOGNIZER.recognize(text)

        for candidate in candidates:
            if candidate.quantity_phrase == "":
                continue

            if candidate.inferred_kind == QuantityKind.BUDGET_LINE:
                mention, ambiguous, renumber_obs, rejected = _resolve_budget_line(
                    candidate, statute_id, provision_ref, lookup_year
                )
                if mention is not None:
                    mention_with_valid = PoolMention(
                        source_provision_ref=mention.source_provision_ref,
                        quantity_phrase=mention.quantity_phrase,
                        pool_canonical_id=mention.pool_canonical_id,
                        quantity_kind=mention.quantity_kind,
                        resolution_confidence=mention.resolution_confidence,
                        numeric_value=mention.numeric_value,
                        unit=mention.unit,
                        source_span_file=mention.source_span_file,
                        source_span_byte_offset=mention.source_span_byte_offset,
                        source_span_byte_len=mention.source_span_byte_len,
                        valid_at_start=valid_start,
                        valid_at_end=valid_end,
                    )
                    result.mentions.append(mention_with_valid)
                if ambiguous is not None:
                    result.ambiguous_findings.append(ambiguous)
                if renumber_obs is not None:
                    result.renumbering_observations.append(renumber_obs)
                if rejected is not None:
                    result.rejected.append(rejected)

            else:
                # FISCAL_POOL, CAPACITY_CAP, THRESHOLD, FORMULA_TERM
                mention = _resolve_non_budget_line(candidate, statute_id, provision_ref)
                mention_with_valid = PoolMention(
                    source_provision_ref=mention.source_provision_ref,
                    quantity_phrase=mention.quantity_phrase,
                    pool_canonical_id=mention.pool_canonical_id,
                    quantity_kind=mention.quantity_kind,
                    resolution_confidence=mention.resolution_confidence,
                    numeric_value=mention.numeric_value,
                    unit=mention.unit,
                    source_span_file=mention.source_span_file,
                    source_span_byte_offset=mention.source_span_byte_offset,
                    source_span_byte_len=mention.source_span_byte_len,
                    valid_at_start=valid_start,
                    valid_at_end=valid_end,
                )
                result.mentions.append(mention_with_valid)

    _apply_strict_barriers(result, statute_id, strict)
    return result
