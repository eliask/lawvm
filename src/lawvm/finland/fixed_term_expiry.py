"""Extract statute-level fixed-term validity bounds from Finnish timelines.

Ownership of *recognising* the whole-law expiry clause stays in the typed
meta-clause lane: ``extract_meta_surface_clauses`` classifies the
voimaantulosäännös prose as a ``MetaClauseKind.EXPIRY`` clause. This module
consumes that classification and reuses the proven date regex
(``whole_law_expiry_date_from_text``) as a helper to lift it into a
``StatuteValidityBound`` per version of the entry-into-force provision.

Extension acts text-replace the entry-into-force provision, so each version of
that provision carries the then-current bound; one bound fact is produced per
version (Pro design D′, §10 step 4).

Diagnostics (spec §4) are returned as structured records; the caller decides
their finding role (the governing-bound unparseable/ambiguous cases are
blocking obligations, scoped/weak cases are observations).
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import MetaClauseKind
from lawvm.core.statute_validity import (
    BoundKind,
    EpistemicStatus,
    StatuteValidityBound,
    expires_on_from_valid_until,
)
from lawvm.finland.fi_dates import (
    FiDateForm,
    fi_partitive_month_number,
    match_fi_date,
)
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.metadata import (
    CHAPTER_SCOPED_EXPIRY_RE,
    SECTION_SCOPED_EXPIRY_RE,
    _normalize_fi_parse_text,
    parse_whole_law_validity,
)
from lawvm.finland.temporal_arithmetic import (
    RULE_FI_ELIDED_YEAR_END,
    duration_validity_end,
)

# Diagnostic codes (registered in observation_registry under role per spec §4).
FIXED_TERM_EXPIRY_UNPARSEABLE = "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE"
FIXED_TERM_EXPIRY_AMBIGUOUS = "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS"
FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS = (
    "TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS"
)
SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED = "TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED"
POSSIBLE_EXPIRY_TEXT_UNSUPPORTED = "TEMPORAL.POSSIBLE_EXPIRY_TEXT_UNSUPPORTED"
FIXED_TERM_LATE_EXTENSION_GAP = "TEMPORAL.FIXED_TERM_LATE_EXTENSION_GAP"
EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT = (
    "TEMPORAL.EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT"
)
# Typed residue classes for recognised-but-unresolved validity clauses. Each
# names the missing authority or the reason the clause is not a bound at all,
# instead of collapsing every failure into the generic unparseable bucket.
DURATION_ARITHMETIC_AUTHORITY_MISSING = "TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING"
DURATION_COMMENCEMENT_UNRESOLVED = "TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED"
EVENT_BOUND_RESOLVER_MISSING = "TEMPORAL.EVENT_BOUND_RESOLVER_MISSING"
EVENT_BOUND_OUT_OF_DOCTRINE = "TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE"
DECREE_SET_COMMENCEMENT_UNRESOLVED = "TEMPORAL.DECREE_SET_COMMENCEMENT_UNRESOLVED"
SOURCE_IMPOSSIBLE_DATE = "TEMPORAL.SOURCE_IMPOSSIBLE_DATE"
START_ONLY_NOT_EXPIRY_BOUND = "TEMPORAL.START_ONLY_NOT_EXPIRY_BOUND"
NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED = "TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED"

# Blocking residue family: a recognised whole-law validity clause whose end the
# extractor must not guess. These keep the seam fail-loud (expiry_unverified).
BLOCKING_UNRESOLVED_EXPIRY_CODES = frozenset(
    {
        FIXED_TERM_EXPIRY_UNPARSEABLE,
        FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS,
        DURATION_ARITHMETIC_AUTHORITY_MISSING,
        DURATION_COMMENCEMENT_UNRESOLVED,
        EVENT_BOUND_RESOLVER_MISSING,
        EVENT_BOUND_OUT_OF_DOCTRINE,
        SOURCE_IMPOSSIBLE_DATE,
    }
)

# Diagnostic clause texts are evidence, not logs; keep enough to re-find the
# clause in the source without ballooning the record.
_CLAUSE_TEXT_LIMIT = 400


@dataclass(frozen=True, slots=True)
class FixedTermDiagnostic:
    """One extraction-time diagnostic about fixed-term validity.

    ``clause_text`` carries the offending source clause itself (truncated)
    so the diagnostic is self-evidencing — typing a residual must never
    require re-running extraction to see what the text said.
    """

    code: str
    statute_id: str
    address: str
    effective: str
    detail: str
    clause_text: str = ""


@dataclass(frozen=True, slots=True)
class FixedTermExtraction:
    """Result of scanning one statute's timelines for fixed-term bounds."""

    statute_id: str
    bounds: tuple[StatuteValidityBound, ...]
    diagnostics: tuple[FixedTermDiagnostic, ...]
    # True when a whole-law expiry CLAUSE was recognised on at least one version
    # (whether or not its date parsed). Cheap corpus-report candidate signal.
    has_candidate: bool


# "on voimassa toistaiseksi" without a hard cap is the permanent-law default
# ("until further notice"), not a fixed-term form. With a cap ("ei kuitenkaan
# kau(v)emmin kuin ...", "enintään ...") it states a real outer bound.
_BARE_TOISTAISEKSI_RE = compile_classifier_regex(r"\bon\s+voimassa\s+toistaiseksi\b", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.bare_toistaiseksi_re")
_TOISTAISEKSI_CAP_RE = compile_classifier_regex(r"kau[uv]emmin\s+kuin|enintään", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.toistaiseksi_cap_re")

# Commencement marker expected in a genuine voimaantulosäännös version.
# (Single bounded gap, not \s+ then .{0,40}: adjacent variable repeats with
# overlapping starts are a backtracking hazard the regex gate rejects.)
_COMMENCEMENT_CONTEXT_RE = compile_classifier_regex(r"(?:tulee|tuli|astuu|astui)\s+voimaan|voimassa.{1,41}?päivästä", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.commencement_context_re")

# Structural voimaantulosäännös marker: the carrying section's heading names
# itself the entry-into-force provision ("7 § Voimaantulo", "Voimaantulo- ja
# siirtymäsäännökset"). When this is present, the commencement-context guard
# must NOT suppress — a recognised-but-unparseable clause in a structurally
# known voimaantulosäännös stays blocking (Pro V5 asymmetry doctrine).
_VOIMAANTULO_HEADING_RE = compile_classifier_regex(r"(?:^\s*|§\s+)voimaantulo", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.voimaantulo_heading_re")


def _whole_law_expiry_clause_text(normalized_text: str) -> Optional[str]:
    """The clause text when the typed meta-clause lane classifies
    ``normalized_text`` as a whole-law expiry clause ("Tämä laki ... on
    voimassa ..."), else None."""
    for clause in extract_meta_surface_clauses(normalized_text):
        if clause.kind is not MetaClauseKind.EXPIRY:
            continue
        # Restrict to the WHOLE-law form: the clause subject is the act itself
        # ("Tämä laki/asetus/päätös"), not a named section/chapter.
        if not _whole_law_subject_re_search(clause.text):
            continue
        # lawvm-regex: owning_parser toistaiseksi-vs-cap discriminator over an already-EXPIRY-classified clause
        if _BARE_TOISTAISEKSI_RE.search(clause.text) and not _TOISTAISEKSI_CAP_RE.search(
            clause.text
        ):
            continue
        return clause.text
    return None


_WHOLE_LAW_SUBJECT = "tämä "


def _whole_law_subject_re_search(text: str) -> bool:
    lowered = text.lower()
    idx = lowered.find(_WHOLE_LAW_SUBJECT)
    if idx < 0:
        return False
    tail = lowered[idx : idx + 40]
    return any(word in tail for word in ("laki", "asetus", "päätös"))


# Dative end-date whose day number must validate against the calendar
# ("31 päivään kesäkuuta 1995" — June has no day 31).
_DATIVE_END_DATE_RE = compile_classifier_regex(r"(\d{1,2})\s*päivään\s+(\w+kuuta)\s+(\d{4})", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.dative_end_date_re")
_DECREE_SET_COMMENCEMENT_RE = compile_classifier_regex(r"asetuksella\s+säädettävänä\s+ajankohtana"
    r"|asetuksella\s+erikseen\s+säädettävänä\s+ajankohtana", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.decree_set_commencement_re")
_EVENT_BOUND_RE = compile_classifier_regex(r"\bkunnes\b|siihen\s+päivään,?\s+jona|siihen\s+saakka,?\s+kun\b", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.event_bound_re")
# Does the event tail name a säädöskokoelma-discernible instrument (another
# statute/decree/treaty whose entry into force is itself published)?
_EVENT_INSTRUMENT_RE = compile_classifier_regex(r"sopimu|voimaansaatt|asetuk|asetus|\blain\b|\blaki\b|tulee\s+voimaan|säädet|§", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.event_instrument_re")
_DURATION_FORM_RE = compile_classifier_regex(r"voimassa[^.]{0,40}?(?:vuoden|vuotta|kuukautta|kuukauden)\b", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.duration_form_re")
_ELIDED_YEAR_END_RE = compile_classifier_regex(r"voimassa\s+vuoden\s+loppuun", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.elided_year_end_re")
_START_ONLY_RE = compile_classifier_regex(r"voimassa\s+\d{1,2}\s*päivästä", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.start_only_re")
_END_MARKER_RE = compile_classifier_regex(r"päivään|saakka|asti|loppuun|\bkunnes\b|enintään|\bajan\b", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.end_marker_re")
_REFERENTIAL_VOIMASSA_RE = compile_classifier_regex(r"on\s+voimassa,\s+mitä|sikäli\s+kuin[^.]{0,80}?on\s+voimassa", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.referential_voimassa_re")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _subject_voimassa_same_sentence(text: str) -> bool:
    """Does any single sentence predicate ``voimassa`` of the act itself?

    Chapter-aggregate version texts glue many sections together, so a
    whole-law subject in one sentence and a ``voimassa`` about something else
    in another sentence can masquerade as a validity clause. A genuine
    validity statement keeps both in one sentence ("Tämä laki tulee voimaan X
    ja on voimassa Y")."""
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if "voimassa" in sentence.lower() and _whole_law_subject_re_search(sentence):
            return True
    return False


# ---------------------------------------------------------------------------
# Duration-form resolution under the pinned 150/1930 arithmetic rule
# ---------------------------------------------------------------------------

# Finnish period numerals as they appear before "vuoden/vuotta/kuukauden/
# kuukautta" (nominative and genitive, 1–12). Unknown numerals stay residue —
# the map never guesses.
_FI_PERIOD_NUM_WORDS: dict[str, int] = {
    "yhden": 1, "yksi": 1,
    "kahden": 2, "kaksi": 2,
    "kolmen": 3, "kolme": 3,
    "neljän": 4, "neljä": 4,
    "viiden": 5, "viisi": 5,
    "kuuden": 6, "kuusi": 6,
    "seitsemän": 7,
    "kahdeksan": 8,
    "yhdeksän": 9,
    "kymmenen": 10,
    "yhdentoista": 11, "yksitoista": 11,
    "kahdentoista": 12, "kaksitoista": 12,
}
# Longest-first so a prefix word ("yhden") never shadows a longer numeral
# ("yhdentoista") in the alternation.
_FI_PERIOD_NUM_ALT = "|".join(sorted(_FI_PERIOD_NUM_WORDS, key=len, reverse=True))

# "voimassa <N> vuotta/vuoden/kuukautta/kuukauden ..." — the year/month
# duration family computable under 150/1930 §3.
_DURATION_SPEC_RE = re.compile(
    r"voimassa\s+(" + _FI_PERIOD_NUM_ALT + r"|\d{1,3})\s+"
    r"(vuoden|vuotta|kuukauden|kuukautta)\b",
    re.IGNORECASE,
)
# Bare "voimassa vuoden voimaantulosta" (no numeral) = one year from
# commencement. The mandatory voimaantulo tail keeps this from matching the
# elided year-end idiom ("voimassa vuoden loppuun"). The owner words are an
# enumerated flat alternation (not nested optional groups) for the regex gate.
_BARE_YEAR_DURATION_RE = compile_classifier_regex(r"voimassa\s+vuoden\s+(?:voimaantulo|sen\s+voimaantulo|lain\s+voimaantulo"
    r"|tämän\s+lain\s+voimaantulo|asetuksen\s+voimaantulo|päätöksen\s+voimaantulo)", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.bare_year_duration_re")
# Concrete commencement date stated in a sentence ("tulee voimaan 15 päivänä
# joulukuuta 1992" / "tulee voimaan 1.3.2015").
_COMMENCEMENT_DATE_ESSIVE_RE = compile_classifier_regex(r"(?:tulee|tuli|astuu|astui)\s+voimaan\s+(\d{1,2})\.?\s*päivänä\s+"
    r"([a-zäöå]+kuuta)\s+(\d{4})", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.commencement_date_essive_re")
_COMMENCEMENT_DATE_DOTTED_RE = compile_classifier_regex(r"(?:tulee|tuli|astuu|astui)\s+voimaan\s+(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", re.IGNORECASE, classifier_id="fi.fixed_term_expiry.commencement_date_dotted_re")


@dataclass(frozen=True)
class _ComputedClauseBound:
    """A duration/elided-year validity end computed with full provenance."""

    valid_until: dt.date
    expires_on: dt.date
    rule_id: str
    bound_kind: BoundKind
    epistemic_status: EpistemicStatus
    arithmetic_authority: Optional[str]
    authority_scope_caveat: Optional[str]
    commencement_date: str
    commencement_source_kind: str
    duration_spec: Optional[str]


@dataclass(frozen=True, slots=True)
class _UnresolvedClauseBound:
    """Typed unresolved result for a recognised fixed-term validity clause."""

    code: str
    detail: str
    blocking: bool


def _sentence_containing(text: str, pos: int) -> str:
    """The sentence of ``text`` that contains character offset ``pos``."""
    start = 0
    # lawvm-regex: owning_parser sentence segmenter (lexer floor) inside the owned expiry construction
    for boundary in _SENTENCE_SPLIT_RE.finditer(text):
        if boundary.end() > pos:
            return text[start : boundary.start() + 1]
        start = boundary.end()
    return text[start:]


def _parse_commencement_date(sentence: str) -> Optional[dt.date]:
    """A concrete commencement date stated in ``sentence``, or None.

    The ``tulee/tuli/astuu/astui voimaan`` context regexes remain the owning
    discriminator that LOCATES the commencement clause; the date-token lexing
    inside the located span is delegated to the shared :func:`match_fi_date`
    recognizer (essive form for the spelled-out date, dotted form for
    ``D.M.YYYY``).
    """
    # lawvm-regex: owning_parser Finnish essive DD-month-YYYY commencement-date token inside a classified clause; unparsed -> typed bound residue
    m = _COMMENCEMENT_DATE_ESSIVE_RE.search(sentence)
    if m is not None:
        essive = match_fi_date(
            m.group(0), forms={FiDateForm.ESSIVE}, tolerate_dotted_day=True
        )
        if essive is not None:
            return essive.value
    # lawvm-regex: owning_parser dotted D.M.YYYY commencement-date token inside a classified clause; unparsed -> typed bound residue
    md = _COMMENCEMENT_DATE_DOTTED_RE.search(sentence)
    if md is not None:
        dotted = match_fi_date(md.group(0), forms={FiDateForm.DOTTED})
        if dotted is not None:
            return dotted.value
    return None


def _scan_statute_commencements(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
) -> tuple[str, ...]:
    """Distinct concrete whole-law commencement dates stated in this statute.

    Scans every version sentence for a whole-law subject ("Tämä laki/asetus/
    päätös ... tulee voimaan <date>") and collects the distinct concrete
    dates. Used only when a duration clause lacks a same-sentence
    commencement; more than one distinct date never resolves anything.
    """
    found: set[str] = set()
    for timeline in timelines.values():
        for version in timeline.versions:
            if version.content is None:
                continue
            normalized = _normalize_fi_parse_text(irnode_to_text(version.content))
            for sentence in _SENTENCE_SPLIT_RE.split(normalized):
                if not _whole_law_subject_re_search(sentence):
                    continue
                date = _parse_commencement_date(sentence)
                if date is not None:
                    found.add(date.isoformat())
    return tuple(sorted(found))


def _resolve_duration_form(
    clause_text: str,
    statute_commencements: Callable[[], tuple[str, ...]],
) -> "_ComputedClauseBound | _UnresolvedClauseBound":
    """Resolve a duration/elided-year validity clause to a computed bound.

    Returns a ``_ComputedClauseBound`` when the end is computable under the
    pinned rules, else ``_UnresolvedClauseBound``. Resolving the arithmetic
    authority never resolves missing commencement FACTS: a clause whose
    commencement is decree-set, unstated, or ambiguous stays blocked.
    """
    # Elided year-end ("on voimassa vuoden loppuun"): ONLY the narrow
    # same-sentence form "tulee voimaan ... <year> ... ja on voimassa vuoden
    # loppuun" resolves (end of the commencement year). A high-confidence
    # inference, never a grammar fact (the opas idiom always names the year).
    # lawvm-regex: owning_parser elided-year-end validity sub-construction; produces typed _UnresolvedClauseBound code
    elided = _ELIDED_YEAR_END_RE.search(clause_text)
    if elided is not None:
        sentence = _sentence_containing(clause_text, elided.start())
        commencement = _parse_commencement_date(sentence)
        if commencement is None:
            return _UnresolvedClauseBound(
                code=DURATION_COMMENCEMENT_UNRESOLVED,
                detail="elided-year validity end ('voimassa vuoden loppuun') without a "
                "same-sentence concrete commencement; the narrow "
                "commencement-year inference rule does not apply",
                blocking=True,
            )
        valid_until = dt.date(commencement.year, 12, 31)
        return _ComputedClauseBound(
            valid_until=valid_until,
            expires_on=expires_on_from_valid_until(valid_until),
            rule_id=RULE_FI_ELIDED_YEAR_END,
            bound_kind="stated_expiry",
            epistemic_status="high_confidence_inference",
            arithmetic_authority=None,
            authority_scope_caveat=None,
            commencement_date=commencement.isoformat(),
            commencement_source_kind="same_sentence",
            duration_spec=None,
        )

    # Year/month duration from commencement, computed under 150/1930 §3.
    years = 0
    months = 0
    # lawvm-regex: owning_parser duration-spec validity sub-construction; typed bound result
    spec_match = _DURATION_SPEC_RE.search(clause_text)
    if spec_match is not None:
        token = spec_match.group(1).lower()
        count = _FI_PERIOD_NUM_WORDS.get(token)
        if count is None:
            count = int(token)
        if spec_match.group(2).lower().startswith("vuo"):
            years = count
        else:
            months = count
    else:
        # lawvm-regex: owning_parser bare-year duration sub-construction; typed bound result
        bare = _BARE_YEAR_DURATION_RE.search(clause_text)
        if bare is None:
            return _UnresolvedClauseBound(
                code=DURATION_ARITHMETIC_AUTHORITY_MISSING,
                detail="duration-form validity recognised but the period is outside "
                "the pinned 150/1930 §3 year/month rule's input domain "
                "(unsupported unit or unparseable period); the end is never "
                "computed ad hoc",
                blocking=True,
            )
        spec_match = bare
        years = 1
    anchor_pos = spec_match.start()
    sentence = _sentence_containing(clause_text, anchor_pos)
    if "voimaantulo" not in sentence.lower():
        return _UnresolvedClauseBound(
            code=DURATION_ARITHMETIC_AUTHORITY_MISSING,
            detail="duration-form validity whose period is not anchored to the "
            "law's own commencement (no voimaantulo anchor in the clause "
            "sentence); outside the pinned duration_from_commencement rule",
            blocking=True,
        )
    # lawvm-regex: owning_parser toistaiseksi-cap discriminator over the classified clause
    if _TOISTAISEKSI_CAP_RE.search(sentence):
        # "enintään / ei kauemmin kuin <duration>": an OUTER CAP on an
        # otherwise open-ended validity, not a stated duration end. The
        # cap-as-duration combination is not modeled; never compute it as a
        # plain duration bound.
        return _UnresolvedClauseBound(
            code=DURATION_ARITHMETIC_AUTHORITY_MISSING,
            detail="duration-form validity stated as an outer cap ('enintään' / "
            "'ei kauemmin kuin'); cap-form durations are not modeled as "
            "computed duration bounds",
            blocking=True,
        )
    commencement = _parse_commencement_date(sentence)
    source_kind = "same_sentence"
    if commencement is None:
        # lawvm-regex: owning_parser decree-set commencement sub-construction; typed bound result
        if _DECREE_SET_COMMENCEMENT_RE.search(clause_text):
            return _UnresolvedClauseBound(
                code=DURATION_COMMENCEMENT_UNRESOLVED,
                detail="duration-form validity runs from a decree-set commencement "
                "('asetuksella säädettävänä ajankohtana'); the arithmetic "
                "authority is pinned but the commencement fact is unresolved",
                blocking=True,
            )
        candidates = statute_commencements()
        if len(candidates) == 1:
            commencement = dt.date.fromisoformat(candidates[0])
            source_kind = "same_statute_commencement_clause"
        else:
            return _UnresolvedClauseBound(
                code=DURATION_COMMENCEMENT_UNRESOLVED,
                detail="duration-form validity without a concrete commencement date "
                f"({len(candidates)} distinct whole-law commencement dates "
                "found in the statute); the end cannot be computed",
                blocking=True,
            )
    computed = duration_validity_end(commencement, years=years, months=months)
    rule = computed.rule
    return _ComputedClauseBound(
        valid_until=computed.valid_until,
        expires_on=computed.expires_on,
        rule_id=rule.rule_id,
        bound_kind="duration_from_commencement",
        epistemic_status="computed_under_pinned_authority",
        arithmetic_authority=rule.authority,
        authority_scope_caveat=rule.scope_caveat,
        commencement_date=commencement.isoformat(),
        commencement_source_kind=source_kind,
        duration_spec=computed.duration_spec,
    )


def _classify_unresolved_validity_clause(
    clause_text: str,
    statute_commencements: Callable[[], tuple[str, ...]],
) -> "_ComputedClauseBound | _UnresolvedClauseBound":
    """Type a recognised clause whose validity end did not parse directly.

    Returns ``_UnresolvedClauseBound``, or a ``_ComputedClauseBound`` when
    the clause is a duration/elided-year form whose end is computable under
    the pinned 150/1930 rule. Blocking classes name the missing authority or
    fact (the seam stays fail-loud); non-blocking classes are audited
    non-candidates (the clause is not a whole-law expiry bound at all).
    Ordered most-specific first; the generic unparseable code is the fallback.
    """
    # lawvm-regex: owning_parser dative end-date validity sub-construction; typed bound result
    for match in _DATIVE_END_DATE_RE.finditer(clause_text):
        day = int(match.group(1))
        month_num = fi_partitive_month_number(match.group(2))
        year = int(match.group(3))
        if month_num is None:
            continue
        last_day = calendar.monthrange(year, month_num)[1]
        if day > last_day:
            candidate = f"{year:04d}-{month_num:02d}-{last_day:02d}"
            return _UnresolvedClauseBound(
                code=SOURCE_IMPOSSIBLE_DATE,
                detail="source states a calendar-impossible end date "
                f"('{match.group(0)}'); candidate normalization {candidate} "
                "requires a säädöskokoelma correction or manual attestation",
                blocking=True,
            )
    if not _subject_voimassa_same_sentence(clause_text):
        return _UnresolvedClauseBound(
            code=NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED,
            detail="no sentence predicates 'voimassa' of the act itself; the "
            "voimassa-shaped text is about another subject",
            blocking=False,
        )
    # lawvm-regex: owning_parser referential-voimassa sub-construction; typed bound result
    if _REFERENTIAL_VOIMASSA_RE.search(clause_text):
        return _UnresolvedClauseBound(
            code=NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED,
            detail="referential/qualifying 'voimassa' (incorporation by reference or "
            "'sikäli kuin' qualifier), not a whole-law validity bound",
            blocking=False,
        )
    # lawvm-regex: owning_parser decree-set commencement discriminator over the classified clause
    if _DECREE_SET_COMMENCEMENT_RE.search(clause_text):
        # lawvm-regex: owning_parser duration-form / elided-year-end discriminators; typed bound result
        if _DURATION_FORM_RE.search(clause_text) or _ELIDED_YEAR_END_RE.search(
            clause_text
        ):
            # The clause ALSO states a duration validity end that runs from
            # the (unresolved) decree-set commencement: a real expiry bound
            # that cannot be computed — blocking, not a mere commencement
            # frontier.
            return _resolve_duration_form(clause_text, statute_commencements)
        return _UnresolvedClauseBound(
            code=DECREE_SET_COMMENCEMENT_UNRESOLVED,
            detail="commencement is decree-set ('asetuksella säädettävänä "
            "ajankohtana'); commencement resolution frontier, not a "
            "whole-law expiry bound",
            blocking=False,
        )
    # lawvm-regex: owning_parser event-bound validity sub-construction; typed bound result
    event_match = _EVENT_BOUND_RE.search(clause_text)
    if event_match is not None:
        tail = clause_text[event_match.start() :]
        # lawvm-regex: owning_parser event-instrument sub-construction; typed bound result
        if _EVENT_INSTRUMENT_RE.search(tail):
            return _UnresolvedClauseBound(
                code=EVENT_BOUND_RESOLVER_MISSING,
                detail="validity ends at a säädöskokoelma-discernible event (another "
                "instrument's entry into force); cross-document resolver not "
                "yet implemented",
                blocking=True,
            )
        return _UnresolvedClauseBound(
            code=EVENT_BOUND_OUT_OF_DOCTRINE,
            detail="validity ends at a substantive event not discernible from the "
            "säädöskokoelma; out of the blessed event-bound drafting pattern",
            blocking=True,
        )
    # lawvm-regex: owning_parser duration-form / elided-year-end discriminators; typed bound result
    if _DURATION_FORM_RE.search(clause_text) or _ELIDED_YEAR_END_RE.search(clause_text):
        return _resolve_duration_form(clause_text, statute_commencements)
    # lawvm-regex: owning_parser start-only-vs-end-marker discriminators; typed bound result
    if _START_ONLY_RE.search(clause_text) and not _END_MARKER_RE.search(clause_text):
        return _UnresolvedClauseBound(
            code=START_ONLY_NOT_EXPIRY_BOUND,
            detail="start-only validity statement ('voimassa N päivästä ...') with "
            "no end marker; a commencement fact, not an expiry bound",
            blocking=False,
        )
    return _UnresolvedClauseBound(
        code=FIXED_TERM_EXPIRY_UNPARSEABLE,
        detail="whole-law expiry clause recognised but validity date unparseable",
        blocking=True,
    )


def _content_hash(version: ProvisionVersion) -> str:
    if version.content_hash:
        return version.content_hash
    text = irnode_to_text(version.content) if version.content is not None else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _version_source_id(version: ProvisionVersion, statute_id: str) -> str:
    if version.source is not None and version.source.statute_id:
        return version.source.statute_id
    return statute_id


def extract_fixed_term_bounds(
    *,
    statute_id: str,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
) -> FixedTermExtraction:
    """Scan ``timelines`` for whole-law fixed-term validity bounds.

    Each version of the entry-into-force provision that carries a whole-law
    expiry clause yields one bound. Parent/child duplicates of the same clause
    (e.g. section + its subsection) are de-duplicated, keeping the shallowest
    carrying address. Detected-but-unparseable clauses and scoped forms emit
    diagnostics rather than silently degrading.
    """

    bounds: list[StatuteValidityBound] = []
    diagnostics: list[FixedTermDiagnostic] = []
    has_candidate = False

    # Lazy statute-wide whole-law commencement scan, shared across duration
    # clauses (only computed when a duration form lacks a same-sentence
    # commencement date).
    scanned_commencements: Optional[tuple[str, ...]] = None

    def statute_commencements() -> tuple[str, ...]:
        nonlocal scanned_commencements
        if scanned_commencements is None:
            scanned_commencements = _scan_statute_commencements(timelines)
        return scanned_commencements

    # (effective, source_id) -> shallowest carrying address depth seen so far.
    claimed: dict[tuple[str, str], int] = {}
    sequence = 0

    for address in sorted(timelines, key=lambda a: (len(a.path), str(a))):
        timeline = timelines[address]
        for version in timeline.versions:
            if version.content is None:
                continue
            normalized = _normalize_fi_parse_text(irnode_to_text(version.content))

            scoped_only = (
                # lawvm-regex: owning_parser scoped-vs-whole-law route discriminator over own normalized source-IR; scoped -> typed SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED, no lifecycle minted
                SECTION_SCOPED_EXPIRY_RE.search(normalized) is not None
                # lawvm-regex: owning_parser scoped-vs-whole-law route discriminator over own normalized source-IR; scoped -> typed diagnostic
                or CHAPTER_SCOPED_EXPIRY_RE.search(normalized) is not None
            )
            clause_text = _whole_law_expiry_clause_text(normalized)

            if clause_text is None and not scoped_only:
                continue

            source_id = _version_source_id(version, statute_id)
            key = (version.effective, source_id)

            if scoped_only and clause_text is None:
                # A scoped (chapter/section) fixed-term form. v1 does not lift it
                # into a statute-level bound; surface it for review.
                # lawvm-regex: owning_parser re-locates the scoped clause to populate the self-evidencing diagnostic; no lifecycle minted
                scoped_match = SECTION_SCOPED_EXPIRY_RE.search(
                    normalized
                # lawvm-regex: owning_parser re-locates the scoped clause for the self-evidencing diagnostic; no lifecycle minted
                ) or CHAPTER_SCOPED_EXPIRY_RE.search(normalized)
                diagnostics.append(
                    FixedTermDiagnostic(
                        code=SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED,
                        statute_id=statute_id,
                        address=str(address),
                        effective=version.effective,
                        detail="scoped (chapter/section) fixed-term expiry detected; not lifted in v1",
                        clause_text=(scoped_match.group(0) if scoped_match else "")[
                            :_CLAUSE_TEXT_LIMIT
                        ],
                    )
                )
                continue

            # Both None-cases of clause_text continued above.
            assert clause_text is not None

            parse = parse_whole_law_validity(normalized)
            valid_until = parse.valid_until if parse is not None else None
            if (
                valid_until is None
                # lawvm-regex: owning_parser voimaantulosäännös-context guard (Pro V5 asymmetry); false-positive body text -> typed suppressed diagnostic
                and not _COMMENCEMENT_CONTEXT_RE.search(normalized)
                # lawvm-regex: owning_parser structural voimaantulo-heading override of the suppression guard; discriminator only
                and not _VOIMAANTULO_HEADING_RE.search(normalized)
            ):
                # Substantive body text can match the expiry meta-clause shape
                # ("... ja tämä päätös ... on voimassa Suomessa") without being
                # a voimaantulosäännös. With neither a parseable date nor a
                # commencement marker in the carrying version, treat it as a
                # false positive rather than a blocking unparseable bound —
                # but say so on the audit lane instead of suppressing silently.
                # Structural override: a section whose heading names itself the
                # voimaantulosäännös never takes this branch and stays blocking.
                diagnostics.append(
                    FixedTermDiagnostic(
                        code=EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT,
                        statute_id=statute_id,
                        address=str(address),
                        effective=version.effective,
                        detail=(
                            "expiry-shaped clause without a parseable date or a "
                            "commencement marker; suppressed as a body-text false "
                            "positive"
                        ),
                        clause_text=clause_text[:_CLAUSE_TEXT_LIMIT],
                    )
                )
                continue

            # Whole-law clause recognised — this is a fixed-term candidate.
            has_candidate = True
            depth = len(address.path)
            prior_depth = claimed.get(key)
            if prior_depth is not None and depth >= prior_depth:
                # The same clause already owned by a shallower (or equal) address.
                continue

            computed: Optional[_ComputedClauseBound] = None
            if valid_until is None:
                if parse is not None and parse.ambiguous_years:
                    # Anaphoric "sanotun vuoden loppuun" with more than one
                    # plausible same-sentence antecedent year: blocking, never
                    # a guess (Pro V4).
                    diagnostics.append(
                        FixedTermDiagnostic(
                            code=FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS,
                            statute_id=statute_id,
                            address=str(address),
                            effective=version.effective,
                            detail=(
                                "anaphoric year-end ('sanotun vuoden loppuun') has "
                                f"multiple plausible antecedent years: "
                                f"{list(parse.ambiguous_years)}"
                            ),
                            clause_text=clause_text[:_CLAUSE_TEXT_LIMIT],
                        )
                    )
                    claimed[key] = depth
                    continue
                resolution = _classify_unresolved_validity_clause(
                    clause_text, statute_commencements
                )
                if not isinstance(resolution, _ComputedClauseBound):
                    diagnostics.append(
                        FixedTermDiagnostic(
                            code=resolution.code,
                            statute_id=statute_id,
                            address=str(address),
                            effective=version.effective,
                            detail=resolution.detail,
                            clause_text=clause_text[:_CLAUSE_TEXT_LIMIT],
                        )
                    )
                    # Do not record a bound; mark the key so deeper duplicates of
                    # the same unparseable/ambiguous clause do not re-diagnose.
                    claimed[key] = depth
                    continue
                # Duration/elided-year end computed under a named, pinned rule:
                # lift it into a bound carrying its arithmetic provenance.
                computed = resolution

            if computed is not None:
                bound = StatuteValidityBound(
                    statute_id=statute_id,
                    scope="whole_statute",
                    effective=version.effective,
                    enacted=version.enacted or None,
                    valid_until=computed.valid_until.isoformat(),
                    expires_on=computed.expires_on.isoformat(),
                    source_provision=address,
                    source_version_id=source_id,
                    source_hash=_content_hash(version),
                    source_span=None,
                    rule_id=computed.rule_id,
                    source_text=normalized[:500],
                    source_sequence=sequence,
                    bound_kind=computed.bound_kind,
                    source_phrase_kind=None,
                    earlier_termination_possible=False,
                    antecedent_text=None,
                    antecedent_span=None,
                    epistemic_status=computed.epistemic_status,
                    arithmetic_authority=computed.arithmetic_authority,
                    authority_scope_caveat=computed.authority_scope_caveat,
                    commencement_date=computed.commencement_date,
                    commencement_source_kind=computed.commencement_source_kind,
                    duration_spec=computed.duration_spec,
                )
            else:
                assert parse is not None  # valid_until is not None ⇒ parse exists
                assert valid_until is not None
                expires_on = expires_on_from_valid_until(valid_until)
                bound = StatuteValidityBound(
                    statute_id=statute_id,
                    scope="whole_statute",
                    effective=version.effective,
                    enacted=version.enacted or None,
                    valid_until=valid_until.isoformat(),
                    expires_on=expires_on.isoformat(),
                    source_provision=address,
                    source_version_id=source_id,
                    source_hash=_content_hash(version),
                    source_span=None,
                    rule_id=parse.rule_id,
                    source_text=normalized[:500],
                    source_sequence=sequence,
                    bound_kind=parse.bound_kind,
                    source_phrase_kind=parse.source_phrase_kind,
                    earlier_termination_possible=parse.earlier_termination_possible,
                    antecedent_text=parse.antecedent_text,
                    antecedent_span=parse.antecedent_span,
                )
            sequence += 1
            # Remove any earlier deeper-address bound for the same key.
            if prior_depth is not None:
                bounds[:] = [
                    b
                    for b in bounds
                    if not (b.effective == version.effective and b.source_version_id == source_id)
                ]
            claimed[key] = depth
            bounds.append(bound)

    # Ambiguity: two distinct whole-law bounds with the SAME effective date but
    # different validity ends cannot be deterministically ranked.
    by_effective: dict[str, set[str]] = {}
    for bound in bounds:
        by_effective.setdefault(bound.effective, set()).add(bound.valid_until)
    for effective, valids in by_effective.items():
        if len(valids) > 1:
            diagnostics.append(
                FixedTermDiagnostic(
                    code=FIXED_TERM_EXPIRY_AMBIGUOUS,
                    statute_id=statute_id,
                    address="<whole_statute>",
                    effective=effective,
                    detail=f"conflicting whole-law bounds at {effective}: {sorted(valids)}",
                )
            )

    return FixedTermExtraction(
        statute_id=statute_id,
        bounds=tuple(bounds),
        diagnostics=tuple(diagnostics),
        has_candidate=has_candidate,
    )


def has_ambiguity(extraction: FixedTermExtraction) -> bool:
    return any(d.code == FIXED_TERM_EXPIRY_AMBIGUOUS for d in extraction.diagnostics)


@dataclass(frozen=True)
class FixedTermCorpusReport:
    """Aggregate counts for a fixed-term extraction soak (Pro §10 step 5)."""

    statutes_scanned: int
    fixed_term_candidates: int
    whole_law_supported: int
    scoped_unsupported: int
    unparseable: int
    ambiguous: int
    affected_statutes: tuple[str, ...]
    anaphora_ambiguous: int = 0
    suppressed_non_commencement: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "lawvm_fixed_term_corpus_report",
            "statutes_scanned": self.statutes_scanned,
            "fixed_term_candidates": self.fixed_term_candidates,
            "whole_law_supported": self.whole_law_supported,
            "scoped_unsupported": self.scoped_unsupported,
            "unparseable": self.unparseable,
            "ambiguous": self.ambiguous,
            "anaphora_ambiguous": self.anaphora_ambiguous,
            "suppressed_non_commencement": self.suppressed_non_commencement,
            "affected_statutes": list(self.affected_statutes),
        }


def build_corpus_report(
    extractions: "list[FixedTermExtraction] | tuple[FixedTermExtraction, ...]",
) -> FixedTermCorpusReport:
    """Aggregate per-statute extractions into corpus counts (no semantic change).

    Cheap mode: callers pass extractions obtained from ``extract_fixed_term_bounds``
    over already-materialised timelines; this performs no replay itself.
    """
    candidates = 0
    whole_law_supported = 0
    scoped_unsupported = 0
    unparseable = 0
    ambiguous = 0
    anaphora_ambiguous = 0
    suppressed = 0
    affected: list[str] = []
    for extraction in extractions:
        if extraction.has_candidate:
            candidates += 1
        if extraction.bounds:
            whole_law_supported += 1
            affected.append(extraction.statute_id)
        for diagnostic in extraction.diagnostics:
            if diagnostic.code == SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED:
                scoped_unsupported += 1
            elif diagnostic.code == FIXED_TERM_EXPIRY_AMBIGUOUS:
                ambiguous += 1
            elif diagnostic.code == FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS:
                anaphora_ambiguous += 1
            elif diagnostic.code in BLOCKING_UNRESOLVED_EXPIRY_CODES:
                # All typed blocking residue classes count into the historical
                # "unparseable" aggregate so soak history stays comparable.
                unparseable += 1
            elif diagnostic.code == EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT:
                suppressed += 1
    return FixedTermCorpusReport(
        statutes_scanned=len(list(extractions)),
        fixed_term_candidates=candidates,
        whole_law_supported=whole_law_supported,
        scoped_unsupported=scoped_unsupported,
        unparseable=unparseable,
        ambiguous=ambiguous,
        affected_statutes=tuple(sorted(set(affected))),
        anaphora_ambiguous=anaphora_ambiguous,
        suppressed_non_commencement=suppressed,
    )


def governing_unparseable(
    extraction: FixedTermExtraction,
    *,
    as_of: str,
    query_type: str,
) -> Optional[FixedTermDiagnostic]:
    """Return a blocking diagnostic when the bound that WOULD govern at
    ``as_of`` is a recognised whole-law expiry clause whose date could not be
    determined (unparseable, or anaphorically ambiguous).

    A bound that fails to parse has no ``effective`` recorded in ``bounds``; we
    reconstruct eligibility from the diagnostic's effective date. This is what
    makes "detected but unparseable, and it governs" block rather than silently
    returning a live answer.
    """
    candidates = [
        d
        for d in extraction.diagnostics
        if d.code in BLOCKING_UNRESOLVED_EXPIRY_CODES and d.effective <= as_of
    ]
    if not candidates:
        return None
    latest_unparseable = max(candidates, key=lambda d: d.effective)
    # If a parseable bound takes effect at or after the unparseable one and is
    # itself eligible at as_of, the parseable bound governs and we do not block.
    for bound in extraction.bounds:
        if bound.effective < latest_unparseable.effective:
            continue
        if bound.effective > as_of:
            continue
        if query_type == "in_force" and bound.enacted and bound.enacted > as_of:
            continue
        return None
    return latest_unparseable
