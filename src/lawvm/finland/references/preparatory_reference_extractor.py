"""Finnish PreparatoryReference extractor.

Extracts typed PreparatoryReference records from ``<hcontainer name="preliminaryWork">``
blocks in Finlex consolidated AKN statute XML.

Entry point:

  extract_preparatory_refs(xml_bytes, statute_id, ...) -> PrepRefExtractionResult
      All PreparatoryReference records FROM statute_id's preliminaryWork block.

Design discipline (AGENTS.md §1.1, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      HE refs that have AKN <ref> markup delegate to cross_refs._parse_ref_href
      to obtain the canonical "he/YEAR/NUMBER" form — no fallback widening.

  §1.8 No unsupported source lane disappears:
      Every <p> in preliminaryWork that is not classified emits
      RejectedPreparatoryCandidate with kind=UNRESOLVED + rule_id.
      Every <p> is accounted for.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before regex (guards listed per pattern).

  §1.13 Grammar trigger — named recognizer:
      The 4+ citation patterns in preliminaryWork form a FAMILY.
      Built as PreparatoryRefRecognizer (single-pass priority scan),
      not N overlapping backtracking scans.

Source: Finlex Akoma Ntoso consolidated XML in the corpus store.
Core primitive: lawvm.core.preparatory_reference.PreparatoryReference.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from lawvm.core.preparatory_reference import (
    CommitteeLifecycleObservation,
    PreparatoryReference,
    PreparatoryReferenceConfidence,
    PreparatoryReferenceKind,
    RejectedPreparatoryCandidate,
)
from lawvm.finland.references.eu_reference import (
    DIALECT_PREPARATORY,
    recognize_celex,
    recognize_eu_acts,
    recognize_oj_refs,
)

# ---------------------------------------------------------------------------
# XML namespaces
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_FINLEX_NS = "http://data.finlex.fi/schema/finlex"

# Tag helper
_AKN = f"{{{_AKN_NS}}}"
_FINLEX = f"{{{_FINLEX_NS}}}"

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

# HE <ref> href pattern: /akn/fi/doc/government-proposal/YEAR/NUMBER
# Reused from cross_refs._HE_REF_PATTERN — keeps canonical_id consistent.
# Substring guard: "/government-proposal/"
_HE_REF_HREF_RE = re.compile(
    r'/akn/fi/doc/government-proposal/(\d{4})/(\d+(?:-\d+)?)'
)

# Closed set of valiokunta (parliamentary committee) abbreviation stems — the
# part BEFORE the "VM" (mietintö) / "VL" (lausunto) suffix (AGENTS.md §1.6: a
# closed real-abbreviation set, not an open `[A-Z][a-zA-Z]{0,12}` wildcard that
# would mis-accept e.g. "EVL" as a committee opinion).  These are the standing
# committees of the Finnish Parliament (eduskunnan valiokunnat):
#
#   Su  suuri valiokunta            Pe  perustuslakivaliokunta
#   Ua  ulkoasiainvaliokunta        Va  valtiovarainvaliokunta
#   Ta  talousvaliokunta            Tar tarkastusvaliokunta
#   Ha  hallintovaliokunta          La  lakivaliokunta
#   Li  liikenne- ja viestintä-     Mm  maa- ja metsätalous-
#   Pu  puolustusvaliokunta         Si  sivistysvaliokunta
#   St  sosiaali- ja terveys-       Ty  työ- ja tasa-arvo-
#   Ym  ympäristövaliokunta         Tu  tulevaisuusvaliokunta
#   Ti  tiedusteluvalvonta-
#
# Both VM (mietintö) and VL (lausunto) are produced by the same committee stems
# (PeVM exists historically alongside the common PeVL), so a single stem set
# serves both recognizers.
_COMMITTEE_STEMS = (
    "Su", "Pe", "Ua", "Va", "Ta", "Tar", "Ha", "La", "Li",
    "Mm", "Pu", "Si", "St", "Ty", "Ym", "Tu", "Ti",
)
# Longest-first so "Tar" wins over "Ta" in the alternation.
_COMMITTEE_STEM_ALT = "|".join(
    sorted(_COMMITTEE_STEMS, key=len, reverse=True)
)

# Committee mietintö: "HaVM 23/2022" or "HaVM 23/2022 vp"
# Pattern: STEM + "VM" + N/YYYY (closed STEM set — AGENTS.md §1.6).
# Substring guard: "VM "
_COMMITTEE_REPORT_RE = re.compile(
    r'(?P<abbr>(?:' + _COMMITTEE_STEM_ALT + r')VM)\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# Committee opinion / lausunto: "PeVL 12/2021" — STEM + "VL".
# Substring guard: "VL "
_COMMITTEE_OPINION_RE = re.compile(
    r'(?P<abbr>(?:' + _COMMITTEE_STEM_ALT + r')VL)\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# Parliament response: "EV 156/2022" — exactly "EV" at start.
# Substring guard: "EV "
_PARLIAMENT_RESPONSE_RE = re.compile(
    r'EV\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# Supplementary parliament response: "EVK 3/2019"
# Substring guard: "EVK "
_PARLIAMENT_RESPONSE_COMM_RE = re.compile(
    r'EVK\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# Law initiative: "LA 5/2021"
# Substring guard: "LA "
_LAW_INITIATIVE_RE = re.compile(
    r'LA\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# EU-act suffix form: "direktiivi 2014/40/EU" — the form letters are a SUFFIX on
# the act number (NUMBER/YEAR/FORM or YEAR/NUMBER/FORM), not a parenthesized
# "(EU)" token.  Used only to recover eu_form when a paragraph carries a CELEX
# but no parenthesized EU marker (the prep EU recognizer only matches the
# parenthesized form).  Bounded groups; literal "/"-delimited; the form
# alternation cannot overlap the digit groups (§1.11).
_EU_SUFFIX_FORM_RE = re.compile(
    r'\b\d{1,6}/\d{1,6}/(?P<form>EU|EY|EEY|ETY|EURATOM|ETA)\b'
)

# Boundary-delimited segment splitter (F2 packed-paragraph enumeration).
# A footer paragraph packs several citations behind ","/";"/"(" delimiters:
#   "<ref>HE…</ref>, LaVM 6/2025, EV 52/2025"
# Splitting on these delimiters and `^`-matching each segment enumerates ALL
# tokens while PRESERVING the start-anchor FP-resistance: a bare mid-sentence
# token ("jotain EV 5/2020 keskellä") is never at a segment start, so it still
# does not match.  Bounded literal character class; no backtracking (§1.11).
_DOMESTIC_SEGMENT_SPLIT_RE = re.compile(r'[,;(]')

# EU act / CELEX / OJ recognition is shared with the cross-reference graph lane
# via references.eu_reference (DIALECT_PREPARATORY preserves this lane's exact
# patterns: form set EU|EY|EEY|ETY, case-sensitive, "\\s+N:o" spacing, modern
# form first then N:o form, CELEX type [A-Z]). Lowering into PreparatoryReference
# (kind determination + canonical_id + date parsing) stays here.

# ---------------------------------------------------------------------------
# Date parsing helper
# ---------------------------------------------------------------------------


def _parse_oj_date(s: str) -> Optional[date]:
    """Parse OJ date string "D.M.YYYY" → date object, or None on failure."""
    parts = s.split(".")
    if len(parts) != 3:
        return None
    day_s, month_s, year_s = parts
    day = int(day_s)
    month = int(month_s)
    year = int(year_s)
    return date(year, month, day)


# ---------------------------------------------------------------------------
# CELEX type → PreparatoryReferenceKind mapping
# ---------------------------------------------------------------------------

# CELEX type character → kind.  "R" = Regulation, "L" = Directive, "D" = Decision.
# Unknown CELEX types fall back to EU_REGULATION for forward-compat.
_CELEX_TYPE_TO_KIND: dict[str, PreparatoryReferenceKind] = {
    "R": PreparatoryReferenceKind.EU_REGULATION,
    "L": PreparatoryReferenceKind.EU_DIRECTIVE,
    "D": PreparatoryReferenceKind.EU_DECISION,
}

# ---------------------------------------------------------------------------
# Canonical ID helpers
# ---------------------------------------------------------------------------


def _committee_canonical_id(abbr: str, n: int, y: int, kind: PreparatoryReferenceKind) -> str:
    """Build canonical_id for committee mietintö / lausunto."""
    abbr_low = abbr.lower()
    if kind == PreparatoryReferenceKind.COMMITTEE_OPINION:
        return f"fi.committee_opinion.{abbr_low}.{n}.{y}"
    return f"fi.committee.{abbr_low}.{n}.{y}"


def _eu_canonical_id(
    kind: PreparatoryReferenceKind,
    eu_form: str,
    eu_number: int,
    eu_year: int,
    celex: Optional[str],
) -> str:
    """Build canonical_id for EU act row."""
    if celex:
        return f"eu.celex.{celex}"
    if kind == PreparatoryReferenceKind.EU_DIRECTIVE:
        type_code = "dir"
    elif kind == PreparatoryReferenceKind.EU_DECISION:
        type_code = "dec"
    else:
        type_code = "reg"
    form_low = eu_form.lower()
    return f"eu.{type_code}.{form_low}.{eu_number}.{eu_year}"


def _oj_canonical_id(series: str, oj_number: int, oj_date: Optional[date]) -> str:
    """Build canonical_id for OJ reference."""
    year = oj_date.year if oj_date else 0
    return f"eu.oj.{series.lower()}.{oj_number}.{year}"


# ---------------------------------------------------------------------------
# PreparatoryRefRecognizer (AGENTS.md §1.13 — named family, not N parallel regexes)
# ---------------------------------------------------------------------------


class PreparatoryRefRecognizer:
    """Named recognizer for Finnish legislative preparation citations (AGENTS.md §1.13).

    Recognizes the citation FAMILY found in preliminaryWork blocks:
      1. HE (via AKN <ref> element or text — handled by caller before this recognizer)
      2. Committee mietintö (VM suffix)
      3. Committee opinion (VL suffix)
      4. Parliament response (EV prefix)
      5. Supplementary parliament response (EVK prefix)
      6. Law initiative (LA prefix)
      7. EU act (+ optional CELEX + optional OJ in same paragraph)
      8. OJ reference standalone

    This is a single-pass priority scanner over the normalized text of each
    <p> element, NOT N overlapping backtracking regex passes.

    The recognizer:
      - runs substring guards before regex (fast path; eliminates ~99% of
        irrelevant <p> elements)
      - returns a list of PreparatoryReference objects from one <p>
        (a <p> may yield >1 rows when EU act + OJ appear together)
      - does NOT silently drop unrecognized text — caller emits UNRESOLVED

    Usage:
        recognizer = PreparatoryRefRecognizer()
        refs, observations = recognizer.recognize(text, statute_id, valid_at)
    """

    # Lifecycle committee abbreviation map.
    # Maps historical abbreviation → (current_canonical_id, lifecycle_event).
    # Deliberately small for now — extend as corpus evidence grows.
    # Per AGENTS.md §1.6.
    _LIFECYCLE_COMMITTEES: dict[str, tuple[str, str]] = {
        # TyVM became TyVL? No — "TyVM" = "Työ- ja tasa-arvoasiain valiokunnan mietintö"
        # is still active. This map is for truly renamed/dissolved committees.
        # Placeholder: empty until corpus evidence warrants an entry.
    }

    def recognize(
        self,
        text: str,
        statute_id: str,
        valid_at: Tuple[Optional[date], Optional[date]],
    ) -> Tuple[List[PreparatoryReference], List[CommitteeLifecycleObservation]]:
        """Recognize ALL citations in normalized paragraph text.

        A preparatory footer paragraph packs several citations behind
        delimiters, e.g. ``"<ref>HE…</ref>, LaVM 6/2025, EV 52/2025"``.  The
        scan enumerates every token rather than returning after the first:

          - Domestic tokens (committee mietintö/lausunto, EV, EVK, LA) are
            recognized per delimiter-bounded SEGMENT (split on ``,;(``), with
            each pattern anchored at the segment start.  This both enumerates
            packed paragraphs AND keeps the start-anchor FP-resistance: a bare
            mid-sentence token ("jotain EV 5/2020 keskellä") is never at a
            segment start, so it still does not match.
          - EU act / CELEX / OJ recognition stays whole-paragraph (its tokens
            span delimiters: "(EU) 2017/2226 (32017R2226); EUVL L 327, …").

        Args:
            text:       Normalized text of one <p> element.
            statute_id: Source statute canonical ID.
            valid_at:   Valid-at interval for emitted PreparatoryReference records.

        Returns:
            Tuple of (refs, lifecycle_observations).
            refs is empty if text was not recognized (caller emits UNRESOLVED).
        """
        refs: List[PreparatoryReference] = []
        obs: List[CommitteeLifecycleObservation] = []

        text = text.strip()
        if not text:
            return refs, obs

        # --- Domestic tokens: one per delimiter-bounded segment ---
        for segment in _DOMESTIC_SEGMENT_SPLIT_RE.split(text):
            seg = segment.strip()
            if not seg:
                continue
            ref = self._recognize_domestic_segment(seg, statute_id, valid_at, obs)
            if ref is not None:
                refs.append(ref)

        # --- EU act / CELEX / OJ: whole-paragraph (tokens span delimiters) ---
        # Classify as an EU act when EITHER a parenthesized EU marker is present
        # OR a CELEX number is present (F3: the suffix form "direktiivi
        # 2014/40/EU (32014L0040)" has no parenthesized marker but carries a
        # CELEX that fully identifies the act).
        has_paren_marker = any(
            marker in text for marker in ("(EU)", "(EY)", "(EEY)", "(ETY)")
        )
        has_celex = bool(recognize_celex(text, dialect=DIALECT_PREPARATORY))
        if has_paren_marker or has_celex:
            eu_refs, eu_obs = self._recognize_eu_paragraph(
                text, statute_id, valid_at
            )
            refs.extend(eu_refs)
            obs.extend(eu_obs)
        elif "EUVL " in text or "EYVL " in text:
            # Standalone OJ reference (no EU act, no CELEX).
            oj_ref = self._extract_oj(text, statute_id, valid_at)
            if oj_ref is not None:
                refs.append(oj_ref)

        return refs, obs

    def _recognize_domestic_segment(
        self,
        seg: str,
        statute_id: str,
        valid_at: Tuple[Optional[date], Optional[date]],
        obs: List[CommitteeLifecycleObservation],
    ) -> Optional[PreparatoryReference]:
        """Recognize ONE domestic citation anchored at the start of ``seg``.

        ``seg`` is a delimiter-bounded, stripped segment.  Each pattern is
        matched with :meth:`re.match` (anchored at position 0), preserving the
        prior start-anchor FP-resistance.  ``F4``: the emitted ``raw_text`` is
        the matched citation TOKEN, not the whole paragraph, so the downstream
        byte span slices to just the token.
        """
        # Priority 1: committee mietintö — substring guard "VM"
        if "VM" in seg:
            m = _COMMITTEE_REPORT_RE.match(seg)
            if m:
                abbr = m.group("abbr")
                n = int(m.group("n"))
                y = int(m.group("y"))
                lifecycle_obs = self._check_lifecycle(abbr, statute_id)
                if lifecycle_obs:
                    obs.append(lifecycle_obs)
                return self._domestic_ref(
                    statute_id, valid_at,
                    kind=PreparatoryReferenceKind.COMMITTEE_REPORT,
                    canonical_id=_committee_canonical_id(
                        abbr, n, y, PreparatoryReferenceKind.COMMITTEE_REPORT
                    ),
                    token=m.group(0),
                    committee_abbrev=abbr,
                )

        # Priority 2: committee opinion — substring guard "VL"
        if "VL" in seg:
            m = _COMMITTEE_OPINION_RE.match(seg)
            if m:
                abbr = m.group("abbr")
                n = int(m.group("n"))
                y = int(m.group("y"))
                lifecycle_obs = self._check_lifecycle(abbr, statute_id)
                if lifecycle_obs:
                    obs.append(lifecycle_obs)
                return self._domestic_ref(
                    statute_id, valid_at,
                    kind=PreparatoryReferenceKind.COMMITTEE_OPINION,
                    canonical_id=_committee_canonical_id(
                        abbr, n, y, PreparatoryReferenceKind.COMMITTEE_OPINION
                    ),
                    token=m.group(0),
                    committee_abbrev=abbr,
                )

        # Priority 3: supplementary parliament response — substring guard "EVK"
        if seg.startswith("EVK"):
            m = _PARLIAMENT_RESPONSE_COMM_RE.match(seg)
            if m:
                n = int(m.group("n"))
                y = int(m.group("y"))
                return self._domestic_ref(
                    statute_id, valid_at,
                    kind=PreparatoryReferenceKind.PARLIAMENT_RESPONSE_COMM,
                    canonical_id=f"fi.evk.{n}.{y}",
                    token=m.group(0),
                )

        # Priority 4: parliament response — substring guard "EV" (not "EVK")
        if seg.startswith("EV ") or seg == "EV" or (
            seg.startswith("EV") and not seg.startswith("EVK")
        ):
            m = _PARLIAMENT_RESPONSE_RE.match(seg)
            if m:
                n = int(m.group("n"))
                y = int(m.group("y"))
                return self._domestic_ref(
                    statute_id, valid_at,
                    kind=PreparatoryReferenceKind.PARLIAMENT_RESPONSE,
                    canonical_id=f"fi.ev.{n}.{y}",
                    token=m.group(0),
                )

        # Priority 5: law initiative — substring guard "LA "
        if seg.startswith("LA "):
            m = _LAW_INITIATIVE_RE.match(seg)
            if m:
                n = int(m.group("n"))
                y = int(m.group("y"))
                return self._domestic_ref(
                    statute_id, valid_at,
                    kind=PreparatoryReferenceKind.LAW_INITIATIVE,
                    canonical_id=f"fi.la.{n}.{y}",
                    token=m.group(0),
                )

        return None

    def _domestic_ref(
        self,
        statute_id: str,
        valid_at: Tuple[Optional[date], Optional[date]],
        *,
        kind: PreparatoryReferenceKind,
        canonical_id: str,
        token: str,
        committee_abbrev: Optional[str] = None,
    ) -> PreparatoryReference:
        """Build a domestic (non-EU) PreparatoryReference from a matched token."""
        return PreparatoryReference(
            source_statute_id=statute_id,
            kind=kind,
            canonical_id=canonical_id,
            raw_text=token,
            committee_abbrev=committee_abbrev,
            he_year=None,
            he_number=None,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=valid_at,
        )

    def _recognize_eu_paragraph(
        self,
        text: str,
        statute_id: str,
        valid_at: Tuple[Optional[date], Optional[date]],
    ) -> Tuple[List[PreparatoryReference], List[CommitteeLifecycleObservation]]:
        """Recognize an EU-act paragraph that may contain EU act + CELEX + OJ.

        A single <p> like:
          "Euroopan parlamentin ja neuvoston asetus (EU) 2017/2226 (32017R2226);
           EUVL L 327, 9.12.2017, s. 20"

        emits one EU_REGULATION/EU_DIRECTIVE/EU_DECISION row (with celex,
        oj_series, etc. populated), NOT separate rows per component — unless the
        paragraph contains ONLY an OJ reference.
        """
        refs: List[PreparatoryReference] = []
        obs: List[CommitteeLifecycleObservation] = []

        # Extract EU act fields — recognize_eu_acts(PREPARATORY) tries the
        # modern "(EU) YEAR/SEQUENTIAL" form first, then the old
        # "(EY) N:o NUMBER/YEAR" form, returning at most one match (matching
        # the prior search()-with-fallback semantics).
        eu_acts = recognize_eu_acts(text, dialect=DIALECT_PREPARATORY)
        eu_act = eu_acts[0] if eu_acts else None
        eu_form = eu_act.form if eu_act else None
        eu_year = int(eu_act.year) if eu_act else None
        eu_number = int(eu_act.number) if eu_act else None

        # Extract CELEX if present (first match — prior code used search()).
        celex_matches = recognize_celex(text, dialect=DIALECT_PREPARATORY)
        celex_match = celex_matches[0] if celex_matches else None
        celex: Optional[str] = celex_match.celex if celex_match else None
        celex_type: Optional[str] = (
            celex_match.celex_type if celex_match else None
        )

        # F3: when the parenthesized EU-act form is absent but a CELEX is
        # present (the common suffix form "direktiivi 2014/40/EU (32014L0040)",
        # whose form letters are a SUFFIX on the number, not a "(EU)" token),
        # recover the act identity from the CELEX itself rather than degrading
        # to a bare OJ row and discarding the CELEX.  The CELEX year/number ARE
        # the act's year/number; the form letter comes from the suffix form.
        if eu_act is None and celex_match is not None:
            eu_year = int(celex_match.year)
            eu_number = int(celex_match.number)
            suffix_m = _EU_SUFFIX_FORM_RE.search(text)
            eu_form = suffix_m.group("form") if suffix_m else "EU"

        # Determine EU act kind from CELEX type (if available)
        if celex_type:
            kind = _CELEX_TYPE_TO_KIND.get(
                celex_type, PreparatoryReferenceKind.EU_REGULATION
            )
        else:
            # No CELEX — determine from EU act text context
            text_low = text.lower()
            if "direktiivi" in text_low:
                kind = PreparatoryReferenceKind.EU_DIRECTIVE
            elif "päätös" in text_low or "beslut" in text_low:
                kind = PreparatoryReferenceKind.EU_DECISION
            else:
                kind = PreparatoryReferenceKind.EU_REGULATION

        # Extract OJ fields (first match — prior code used search()).
        oj_matches = recognize_oj_refs(text)
        m_oj = oj_matches[0] if oj_matches else None
        oj_series: Optional[str] = None
        oj_number_val: Optional[int] = None
        oj_date: Optional[date] = None
        oj_page: Optional[int] = None
        if m_oj:
            oj_series = m_oj.series
            oj_number_val = int(m_oj.number)
            oj_date = _parse_oj_date(m_oj.date)
            oj_page = int(m_oj.page)

        if eu_number is not None and eu_year is not None and eu_form is not None:
            canonical_id = _eu_canonical_id(kind, eu_form, eu_number, eu_year, celex)
            # F4: surface = the citation token, not the whole paragraph. Prefer
            # the parenthesized act span; fall back to the CELEX span.
            if eu_act is not None:
                token = eu_act.raw
            elif celex is not None:
                token = celex
            else:
                token = text
            refs.append(PreparatoryReference(
                source_statute_id=statute_id,
                kind=kind,
                canonical_id=canonical_id,
                raw_text=token,
                committee_abbrev=None,
                he_year=None,
                he_number=None,
                eu_form=eu_form,
                eu_number=eu_number,
                eu_year=eu_year,
                celex=celex,
                oj_series=oj_series,
                oj_number=oj_number_val,
                oj_date=oj_date,
                oj_page=oj_page,
                confidence=PreparatoryReferenceConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_interval=valid_at,
            ))
        elif m_oj:
            # EU marker present but no recognizable EU act form / CELEX — emit OJ
            # standalone.
            oj_ref = self._extract_oj(text, statute_id, valid_at)
            if oj_ref is not None:
                refs.append(oj_ref)

        return refs, obs

    def _extract_oj(
        self,
        text: str,
        statute_id: str,
        valid_at: Tuple[Optional[date], Optional[date]],
    ) -> Optional[PreparatoryReference]:
        """Extract a standalone OJ reference from text."""
        oj_matches = recognize_oj_refs(text)
        if not oj_matches:
            return None
        m_oj = oj_matches[0]
        series = m_oj.series
        oj_number_val = int(m_oj.number)
        oj_date = _parse_oj_date(m_oj.date)
        oj_page = int(m_oj.page)
        canonical_id = _oj_canonical_id(series, oj_number_val, oj_date)
        # F4: surface = the OJ citation token, not the whole paragraph.
        return PreparatoryReference(
            source_statute_id=statute_id,
            kind=PreparatoryReferenceKind.OJ_REFERENCE,
            canonical_id=canonical_id,
            raw_text=m_oj.raw,
            committee_abbrev=None,
            he_year=None,
            he_number=None,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=series,
            oj_number=oj_number_val,
            oj_date=oj_date,
            oj_page=oj_page,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=valid_at,
        )

    def _check_lifecycle(
        self,
        abbr: str,
        statute_id: str,
    ) -> Optional[CommitteeLifecycleObservation]:
        """Emit CommitteeLifecycleObservation if abbr maps to a known lifecycle event."""
        lifecycle = self._LIFECYCLE_COMMITTEES.get(abbr)
        if lifecycle is None:
            return None
        canonical_id, lifecycle_event = lifecycle
        return CommitteeLifecycleObservation(
            rule_id="fi_prep_ref_committee_lifecycle",
            phase="preparatory_ref_extraction",
            source_statute_id=statute_id,
            committee_abbrev=abbr,
            canonical_id=canonical_id,
            lifecycle_event=lifecycle_event,
            blocking=False,
            strict_disposition="record",
        )


# Module-scope recognizer instance (shared across all extractions)
_RECOGNIZER = PreparatoryRefRecognizer()


# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class PrepRefExtractionResult:
    """Container for all artifacts from one preparatory reference extraction pass.

    refs:                   Successfully typed PreparatoryReference records.
    rejected:               RejectedPreparatoryCandidate records (per AGENTS.md §1.8).
    lifecycle_observations: CommitteeLifecycleObservation records (per AGENTS.md §1.6).
    """

    refs: List[PreparatoryReference] = field(default_factory=list)
    rejected: List[RejectedPreparatoryCandidate] = field(default_factory=list)
    lifecycle_observations: List[CommitteeLifecycleObservation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


# hcontainer @name values whose <p> children carry preparatory citations.
#
#   preliminaryWork
#       The base statute's preparation chain (HE + committee + EV + EU act).
#   amendmentEntryIntoForceAndApplianceProvisions / entryIntoForce
#       Each AMENDMENT's footer (voimaantulo- ja soveltamissäännös), which is
#       where every amendment's own preparatory citations live (HaVM/EV/LA/EU
#       act tokens packed into a footer paragraph).  These were previously never
#       visited, so ~93% of preparatory tokens — those belonging to amendments
#       rather than the base act — were silently lost (F1).  The HE <ref> lane
#       walks the WHOLE document, so footer HEs were already captured; only the
#       text-recognized preparatory chain broke here.
_PREPARATORY_BLOCK_NAMES = frozenset({
    "preliminaryWork",
    "amendmentEntryIntoForceAndApplianceProvisions",
    "entryIntoForce",
})


def _is_preparatory_block(hc: ET.Element[str]) -> bool:
    """True if this hcontainer is a preparatory-citation block."""
    name = hc.get("name", "")
    outline = hc.get(f"{_FINLEX}outline", "")
    return name in _PREPARATORY_BLOCK_NAMES or outline in ("Esityöt", "Esiöt")


def _iter_preliminary_work_blocks(root: ET.Element[str]):
    """Yield each TOP-LEVEL hcontainer that may hold preparatory citations.

    Matches by @name in :data:`_PREPARATORY_BLOCK_NAMES` (the base
    ``preliminaryWork`` block AND every amendment's entry-into-force footer),
    and also by finlex:outline="Esityöt" as a fallback for older Finlex AKN
    variants whose name attribute may differ.

    Finlex nests ``entryIntoForce`` blocks INSIDE the
    ``amendmentEntryIntoForceAndApplianceProvisions`` wrapper.  Because the
    caller walks each yielded block's ``<content>`` RECURSIVELY, yielding both
    the wrapper and its nested children would visit every footer ``<p>`` twice.
    A matching block is therefore yielded only when NO ancestor is also a
    matching block (the outermost wins; its recursive content walk covers the
    descendants).  An element matching by both name and outline is yielded once.
    """
    # Parent map for ancestor lookup (ElementTree has no parent pointers).
    parents: dict[int, ET.Element[str]] = {
        id(child): parent for parent in root.iter() for child in parent
    }
    seen_ids: set[int] = set()
    for hc in root.iter(f"{_AKN}hcontainer"):
        if not _is_preparatory_block(hc):
            continue
        # Skip if an ancestor is also a preparatory block (avoid double-visit).
        ancestor = parents.get(id(hc))
        nested = False
        while ancestor is not None:
            if _is_preparatory_block(ancestor):
                nested = True
                break
            ancestor = parents.get(id(ancestor))
        if nested:
            continue
        hc_id = id(hc)
        if hc_id not in seen_ids:
            seen_ids.add(hc_id)
            yield hc


def _get_element_text(elem: ET.Element[str]) -> str:
    """Get all text content from an element (including tail text of children)."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _extract_he_ref_from_element(
    p_elem: ET.Element[str],
    statute_id: str,
    valid_at: Tuple[Optional[date], Optional[date]],
) -> Optional[PreparatoryReference]:
    """Extract HE reference from a <p> element that contains an AKN <ref>.

    Reuses the _HE_REF_HREF_RE pattern (matches cross_refs._HE_REF_PATTERN)
    to obtain canonical_id = "he/YEAR/NUMBER".

    Returns a PreparatoryReference with kind=HE if found, else None.
    """
    # Look for <ref href="/akn/fi/doc/government-proposal/...">
    for ref_el in p_elem.iter(f"{_AKN}ref"):
        href = ref_el.get("href", "")
        if "/government-proposal/" in href:
            m = _HE_REF_HREF_RE.search(href)
            if m:
                year = int(m.group(1))
                number_str = m.group(2)
                # Strip potential revision suffix (e.g. "173-1" → 173)
                number = int(number_str.split("-")[0])
                canonical_id = f"he/{year}/{number}"
                raw_text = _get_element_text(p_elem)
                return PreparatoryReference(
                    source_statute_id=statute_id,
                    kind=PreparatoryReferenceKind.HE,
                    canonical_id=canonical_id,
                    raw_text=raw_text if raw_text else href,
                    committee_abbrev=None,
                    he_year=year,
                    he_number=number,
                    eu_form=None,
                    eu_number=None,
                    eu_year=None,
                    celex=None,
                    oj_series=None,
                    oj_number=None,
                    oj_date=None,
                    oj_page=None,
                    confidence=PreparatoryReferenceConfidence.EXACT,
                    source_span_file=None,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                    valid_at_interval=valid_at,
                )
    return None


def _p_contains_he_ref(p_elem: ET.Element[str]) -> bool:
    """Fast check: does this <p> contain an AKN <ref> to a government-proposal?"""
    for ref_el in p_elem.iter(f"{_AKN}ref"):
        href = ref_el.get("href", "")
        if "/government-proposal/" in href:
            return True
    return False


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_preparatory_refs(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> PrepRefExtractionResult:
    """Extract PreparatoryReference records from a Finnish statute XML.

    Walks every ``<hcontainer name="preliminaryWork">`` block and classifies
    each ``<p>`` child as a typed PreparatoryReference or emits a
    RejectedPreparatoryCandidate for unclassified text.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source, e.g. "711/2022".
        valid_at_interval: (start, end) date range for which these references
                           hold. Pass (None, None) for "whole statute history."
        strict:            If True, UNRESOLVED refs cause a strict-mode block
                           rather than a plain record.

    Returns:
        PrepRefExtractionResult with refs, rejected candidates, lifecycle obs.

    Per AGENTS.md §1.1: canonical_id=None only for UNRESOLVED.
    Per AGENTS.md §1.8: every <p> in preliminaryWork is accounted for.
    Per AGENTS.md §1.11: all patterns compiled at module scope.
    Per AGENTS.md §1.13: PreparatoryRefRecognizer is the named recognizer.
    """
    result = PrepRefExtractionResult()

    root = ET.fromstring(xml_bytes)

    for hc_block in _iter_preliminary_work_blocks(root):
        # Walk <content> children, then <p> elements within
        for content_el in hc_block.iter(f"{_AKN}content"):
            for p_el in content_el:
                # Only process <p> elements
                tag_local = p_el.tag.split("}")[-1] if "}" in p_el.tag else p_el.tag
                if tag_local != "p":
                    continue

                emitted = False

                # --- Step 1: HE refs via AKN <ref> markup (reuse #1 logic) ---
                # A footer <p> commonly PACKS the HE <ref> together with the
                # domestic chain in one paragraph ("HE 10/2019, HaVM 3/2019,
                # EV 20/2019"), so the text recognizer (Step 2) ALWAYS runs even
                # when an HE <ref> was found — it must not be skipped, or the
                # co-located committee/EV tokens are lost.
                if _p_contains_he_ref(p_el):
                    he_ref = _extract_he_ref_from_element(
                        p_el, statute_id, valid_at_interval
                    )
                    if he_ref is not None:
                        result.refs.append(he_ref)
                        emitted = True

                # --- Step 2: plain text recognizer (domestic + EU/OJ tokens) ---
                text = _get_element_text(p_el)
                if text:
                    recognized, lifecycle_obs = _RECOGNIZER.recognize(
                        text, statute_id, valid_at_interval
                    )
                    result.lifecycle_observations.extend(lifecycle_obs)
                    if recognized:
                        result.refs.extend(recognized)
                        emitted = True

                if emitted or not text:
                    continue

                # UNRESOLVED: no pattern matched anywhere in this <p>
                # (and no HE <ref>) — emit rejection per AGENTS.md §1.8.
                strict_disp = "block" if strict else "record"
                result.rejected.append(RejectedPreparatoryCandidate(
                    rule_id="fi_prep_ref_unresolved_p_text",
                    phase="preparatory_ref_extraction",
                    source_statute_id=statute_id,
                    reason=(
                        "Text in preliminaryWork block did not match any known "
                        "citation pattern (committee VM/VL, EV, EVK, LA, EU act, "
                        "EUVL OJ, or HE <ref>)."
                    ),
                    raw_text=text[:500],  # bounded for safety
                    blocking=strict,
                    strict_disposition=strict_disp,
                ))

    return result
