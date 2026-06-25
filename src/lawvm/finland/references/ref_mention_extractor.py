"""Finnish ReferenceMention extractor — lifts CrossRefEdge to core typed primitive.

This module promotes Finland's existing ``CrossRefEdge`` extraction to the
stable core ``ReferenceMention`` type.  It is the primary extractor for
``fi_refs.parquet`` projection.

Entry points:

  extract_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      All ReferenceMention records FROM statute_id + any findings/rejections.

  extract_eu_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      EU cross-jurisdiction references from text scan.

  extract_plain_text_statute_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      Plain-text Finnish statute citations NOT covered by <ref> markup.

  extract_all_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      Combined domestic + EU + plain-text extraction.

Design discipline (AGENTS.md §1.1, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      CrossRefEdge resolution maps edge_type → CiteKind deterministically.
      No fallback widening; unresolvable targets get confidence=UNRESOLVED.

  §1.8 No unsupported source lane disappears:
      Every rejected candidate emits RejectedRefCandidate.
      Every diagnostic from CrossRefDiagnostic is preserved.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before regex on long text.

  §1.13 Named recognizer for plain-text statute citations:
      Finnish statute citations without <ref> markup form a GRAMMAR FAMILY
      (3+ inflection variants: -lain, -asetuksen, -laissa, -lakia, etc.).
      Implemented as PlainTextStatuteCitationRecognizer — one single-pass
      structured recognizer, not N overlapping backtracking regexes.

Source: Finlex Akoma Ntoso consolidated XML in the corpus store.
Promotion from: ``lawvm.finland.cross_refs`` (CrossRefEdge, CrossRefDiagnostic).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable, List, Optional, Tuple

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    ApproximateReferenceFinding,
    BrokenReferenceFinding,
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    RejectedRefCandidate,
    SourceSpan,
)
from lawvm.finland.references.cross_refs import (
    CrossRefDiagnostic,
    CrossRefEdge,
    _make_statute_id,
    extract_affected_document_refs,
    extract_cross_refs,
    extract_eu_refs,
)
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    chapter_akn_path,
    parse_body_provision_tail,
)
from lawvm.finland.references.lemma_gate import head_case_forms
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.eu_nickname_binding import (
    build_statute_local_nicknames,
)
from lawvm.finland.references.treaty import recognize_treaty_refs
from lawvm.finland.references.treaty_article import recognize_treaty_article_refs
from lawvm.finland.references.vague import recognize_vague_refs
from lawvm.finland.references.internal_refs import recognize_internal_refs
from lawvm.finland.references.by_name import (
    recognize_by_name_refs,
)
from lawvm.finland.references.shared_reference_orchestrator import (
    lift_inline_id_construction_mentions,
)
from lawvm.core.preparatory_reference import (
    PreparatoryReference,
    PreparatoryReferenceKind,
)
from lawvm.finland.references.preparatory_reference_extractor import (
    extract_preparatory_refs,
)
from lawvm.finland.legal_surface.delegation_parse import extract_authority_bases
from lawvm.finland.authority_basis import _classify_authority_kind, _normalize_year

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Section label extractor from AKN sec_N or sec_Na paths.
# Bounded: [a-z0-9_]{0,100} is safe.
_AKN_SECTION_PATH_RE = re.compile(
    r"(?:^|/|__)sec_([0-9]{1,6}[a-z]?)(?:/|$|__|_sub)",
    re.IGNORECASE,
)

# Subsection extractor from AKN path: sec_N_sub_M or sub_M.
_AKN_SUBSECTION_PATH_RE = re.compile(
    r"(?:_sub_|__subsec_)([0-9]{1,4})(?:/|$|__|_)",
    re.IGNORECASE,
)

# Bare section label as carried directly on a CrossRefEdge.target_section by the
# delegation/authority preamble parser (``"37"``, ``"8"``, ``"115a"``) — NOT an
# AKN ``sec_N`` path. The authority (``nojalla``) lane populates target_section
# with the section number as it appears in the surface, so this is the leading
# numeric run plus an optional letter suffix. Used as the fallback when
# _AKN_SECTION_PATH_RE (which requires the ``sec_`` prefix) does not match, so
# the cited §37/§36/§8 is retained on the mention instead of silently dropped.
_BARE_SECTION_LABEL_RE = re.compile(
    r"^([0-9]{1,6}[a-z]?)$",
    re.IGNORECASE,
)

# EU statute id extractor: "eu/TYPE/YEAR/NUMBER"
_EU_ID_RE = re.compile(
    r"^eu/([a-z]{2,10})/(\d{4})/(\d{1,6})$",
    re.IGNORECASE,
)

# Section label from a <section><num>…</num> surface (``10 §.`` -> ``10``,
# ``115 a §`` -> ``115a``, ``5 §`` -> ``5``). The label is the leading number run
# plus an optional letter suffix, normalized to the glued AKN form (no spaces),
# matching the body sub-ref grammar's section-label shape (``\d{1,6}[a-z]?``).
_SECTION_NUM_LABEL_RE = re.compile(
    r"(\d{1,6})\s*([a-zA-Z\xe4\xf6\xc4\xd6])?",
)

# ---------------------------------------------------------------------------
# Plain-text statute citation recognizer (AGENTS.md §1.13)
# ---------------------------------------------------------------------------
#
# Finnish statute citations in body prose without <ref> markup follow a
# shared pattern family:
#
#   Inflection suffixes on the statute name:
#     -lain          (lannoitelain)
#     -lakia         (lannoitelakia)
#     -laissa        (elintarvikelaissa)
#     -laista        (elintarvikelaista)
#     -laiksi        (elintarvikelaiksi)
#     -laille        (elintarvikelaille)
#     -asetuksen     (ympäristönsuojeluasetuksen)
#     -asetusta      (ympäristönsuojeluasetusta)
#     -asetuksessa   (ympäristönsuojeluasetuksessa)
#     -asetuksesta   (ympäristönsuojeluasetuksesta)
#     -asetukseksi   (ympäristönsuojeluasetukseksi)
#     -lain          (also short: "lain (711/2022)")
#     -asetuksen     (also short: "asetuksen (964/2023)")
#   ...
#   NOMINATIVE head ``laki`` / ``asetus`` — ONLY inside the ``annettu``-participle
#   repeal/description frame ``[…sta/stä] annettu asetus/laki (NNN/YYYY)``
#   ("kumotaan … annettu asetus (875/1983)"). The bare nominative is too common
#   to anchor alone, so the discriminating ``annettu`` participle + trailing
#   ``(NNN/YYYY)`` id are both required; ``tämä laki`` / ``asetus annetaan``
#   (no participle, no id) never match.
#   Followed by: (NUMBER/YEAR) or (YEAR/NUMBER) in parentheses
#   Optionally followed by: SECTION § and SUBSECTION momentti/momentin
#
# This is a grammar (3+ variants) → build ONE named recognizer, not N regexes.
#
# The recognizer uses a SINGLE compiled regex over the text of <p> nodes
# that does not include any <ref> element text, to avoid double-counting.
# The regex is structured so group(1)=statute_number, group(2)=statute_year,
# group(3)=section_label (optional).
#
# Grammar:
#   WORD_WITH_SUFFIX "(" NUMBER "/" YEAR ")" [WHITESPACE SECTION "§"]
#   where SUFFIX is one of the known Finnish inflection suffixes.
#
# Bounded quantifiers (AGENTS.md §1.11):
#   - Word stem: [a-zA-ZäöåÄÖÅ\-]{1,60}
#   - Suffix alternatives: alternation of bounded strings
#   - NUMBER: \d{1,6}
#   - YEAR: \d{4}
#   - SECTION: \d{1,6}[a-zA-ZäöÄÖ]?
#
# Substring guard: check for "§" in text before running the regex
# (all valid statute citations in Finnish law refer to some section/§).
# Additional guard: check for "(" in text (all citations have parenthetical ID).

# Anchor-only regex: matches the statute-name head + ``(NUMBER/YEAR)`` id
# parenthetical and STOPS at the closing paren. The structural tail (section /
# momentti / kohta path that follows the §) is NOT parsed here — it is handed to
# the shared section/sub-ref recognizers (``references.sections``) which model
# en-dash section ranges, section coordination, and momentti/kohta coordination
# with the SAME expressiveness as the johtolause amendment grammar. The regex
# remains the prefilter/anchor only (statute identity), per AGENTS.md §1.13.
#
# STATUTE-HEAD INFLECTION DEMOTION (single source of inflection truth):
#   The head-inflection alternations are no longer hand-written suffix lists.
#   They are GENERATED from the M1 morphology engine (``head_case_forms``,
#   paradigm inversion) for a CURATED ``(case, number)`` set per head — exactly
#   the case set each arm historically recognized — killing the consonant-
#   gradation substring bug class and giving the by-name / inline lanes ONE
#   source of statute-head inflection truth (mirrors
#   ``inline_citation_extractor._STATUTE_HEAD_FORMS``).
#
#   The curated case sets reproduce the prior hand-written alternations
#   BYTE-FOR-BYTE (verified superset with zero extra, zero dropped), so this is
#   a pure refactor with no output change. Where M1's ``reference_v1`` profile
#   cannot generate a form (it omits the ESSIVE), the form is supplied as a
#   closed explicit supplement, same pattern as the inline lane.
#
#   group 1 = statute number, group 2 = statute year.
_STEM = r"[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]"

def _head_alt(
    lemma: str,
    case_numbers: tuple[tuple[str, str], ...],
    *,
    supplement: tuple[str, ...] = (),
) -> str:
    """Morphology-generated inflection alternation body for one statute head.

    Produces the ``form|form|...`` alternation (longest-first, for suffix-
    alternation safety) of the M1 surfaces of ``lemma`` for the curated
    ``(case, number)`` set, plus an explicit ``supplement`` for forms M1's
    ``reference_v1`` profile cannot generate (the ESSIVE — supplied per call
    only where the original arm carried it). This replaces the hand-written
    suffix lists with the single morphology source of truth; the curated case
    set + per-call supplement reproduce the prior alternation byte-for-byte.
    """
    forms = set(head_case_forms(lemma, case_numbers)) | set(supplement)
    if not forms:  # pragma: no cover - reference_v1 always emits these heads
        raise AssertionError(f"M1 generated no surfaces for statute head {lemma!r}")
    return "|".join(sorted(forms, key=lambda s: (-len(s), s)))


# Curated (case, number) sets — exactly the cases each arm historically matched.
_LAKI_CASES = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"),
    ("TRA", "SG"), ("ALL", "SG"), ("ADE", "SG"), ("ABL", "SG"), ("ILL", "SG"),
)
_LAKI_BARE_CASES = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"),
    ("TRA", "SG"), ("ALL", "SG"), ("ADE", "SG"), ("ABL", "SG"),
)
_ASETUS_CASES = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"),
    ("TRA", "SG"), ("ADE", "SG"), ("ALL", "SG"), ("ABL", "SG"), ("ILL", "SG"),
)
_ASETUS_BARE_CASES = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"),
    ("TRA", "SG"), ("ADE", "SG"), ("ALL", "SG"), ("ABL", "SG"),
)
_PAATOS_CASES = (
    ("GEN", "SG"), ("INE", "SG"), ("ELA", "SG"), ("TRA", "SG"),
    ("ADE", "SG"), ("ALL", "SG"), ("ABL", "SG"), ("PART", "SG"),
)
_SAADOS_CASES = (("GEN", "SG"), ("PART", "SG"), ("ILL", "SG"))
_GEN_ONLY = (("GEN", "SG"),)

_PLAIN_TEXT_FI_STATUTE_RE = re.compile(
    r"""
    (?:
        # Named law/statute word with inflection suffix (case forms; NOM is NOT
        # in these arms — bare ``laki``/``asetus`` are handled by the discriminating
        # ``annettu``-participle arm below). Alternations are M1-generated.
        #   ``laki``    GEN/PART/INE/ELA/TRA/ALL/ADE/ABL/ILL + ESS ``lakina``
    """ + _STEM + r"""{1,60}
        (?:""" + _head_alt("laki", _LAKI_CASES, supplement=("lakina",)) + r""")
      | """ + _STEM + r"""{1,60}
        #   ``asetus``  GEN/PART/INE/ELA/TRA/ADE/ALL/ABL/ILL + ESS ``asetuksena``
        (?:""" + _head_alt("asetus", _ASETUS_CASES, supplement=("asetuksena",)) + r""")
      | """ + _STEM + r"""{0,60}
        #   ``päätös``  GEN/INE/ELA/TRA/ADE/ALL/ABL/PART
        (?:""" + _head_alt("päätös", _PAATOS_CASES) + r""")
      | """ + _STEM + r"""{1,60}
        #   ``säädös``  GEN/PART/ILL
        (?:""" + _head_alt("säädös", _SAADOS_CASES) + r""")
      | """ + _STEM + r"""{1,60}
        #   ``määräys`` GEN  (määräyksen)   ``direktiivi`` GEN  (direktiivin)
        (?:""" + _head_alt("määräys", _GEN_ONLY) + r"""|"""
        + _head_alt("direktiivi", _GEN_ONLY) + r""")
      | \b(?:""" + _head_alt("laki", _LAKI_BARE_CASES) + r""")
      | \b(?:""" + _head_alt("asetus", _ASETUS_BARE_CASES) + r""")
      # NOMINATIVE head ``laki`` / ``asetus`` — the repeal/description johtolause
      # form ``[…sta/stä] annettu asetus/laki (NNN/YYYY)`` (``kumotaan … annettu
      # asetus (875/1983)``). The nominative heads are extremely common bare
      # words, so this arm fires ONLY inside the discriminating ``annettu``-
      # participle frame: the participle (``annettu``/``annettua``/``annetun``,
      # agreeing with / governing the head) IMMEDIATELY precedes the nominative
      # head, and the trailing ``(NNN/YYYY)`` id is required by the shared id tail
      # below. ``tämä laki`` / ``asetus annetaan`` (no participle, no id) never
      # matches. The participle prefix is non-capturing so groups 1/2 stay the
      # statute number/year. ``\b`` before the participle anchors it on a word
      # boundary (not a glued ``-annettu`` compound).
      | \bannet(?:tu|tua|un)\s+(?:laki|asetus)
    )
    \s{0,5}
    \(
    \s{0,3}
    (\d{1,6})/(\d{2}|\d{4})   # group 1 = number, group 2 = year (2- or 4-digit)
    \s{0,3}
    \)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Substring guard for the plain-text extractor:
#   - "(" (the parenthetical statute ID, mandatory in every citation)
# The "§" mark is NOT a guard: section-less citations to a whole act
# ("…annetussa laissa (205/2000)") carry no §, and gating on it dropped them.
_PLAIN_TEXT_GUARD_PAREN = "("

# Whitespace-run collapse used ONLY when folding ``<ref>`` inner text into the
# plain-text scan (annotation-independence measurement). Finlex pretty-prints the
# AKN body, so the ``<ref>`` boundary that SPLITS a statute name from its
# ``(NNN/YYYY)`` id leaves a newline + deep indentation between them; collapsing
# the run to one space restores the name→id adjacency the recogniser needs. It
# only ever shrinks whitespace, so two tokens separated by real words stay apart.
_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Entry-into-force editorial date-ref guard. Finlex wraps a consolidation's
# commencement footnotes as ``<ref href="#entryIntoForce_...">13.6.1929/228</ref>``
# — a DATE (``d.m.YYYY``) glued to the amendment's running number, NOT a
# cross-statute citation. The ``YYYY/NNN`` tail looks like a statute id, so once
# the ``<ref>`` inner text is folded into the plain-text scan a name head that
# happens to precede it could otherwise bind the date as a bogus CROSS_STATUTE
# cite. This recognises the ``d.m.YYYY/NNN`` editorial date-ref so the by-id lane
# can decline it (it is an editorial/temporal marker, owned by the temporal lane,
# never a statute reference). Anchored at the ``(`` paren the by-id anchor needs:
# a genuine cite is ``name (NNN/YYYY)`` with parens; the date-ref has none, so the
# guard is a belt-and-braces decline for any future relaxation of the anchor.
_ENTRY_INTO_FORCE_DATEREF_RE = re.compile(
    r"\d{1,2}\.\d{1,2}\.\d{4}\s*/\s*\d{1,6}"
)


@dataclass(frozen=True)
class PlainTextStatuteHit:
    """A single plain-text statute citation hit with provision precision.

    Carries the sub-section precision (momentti, kohta) the citation names so a
    deeplink consumer can target the exact provision, not just the §.

    Attributes:
        statute_id:     "NUMBER/YEAR" canonical form.
        section_label:  e.g. "7", "7a", or "" if not present.
        subsection_num: Momentti number (int) or None when the citation stops
                        at the §.
        item_label:     Kohta label (e.g. "3", "3a") or None.
    """

    statute_id: str
    section_label: str = ""
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None
    # Chapter label (``"9"``, ``"9a"``) when the citation is chapter-qualified
    # (``poliisilain (872/2011) 9 luvun 9 b §``), or None. Carried so the caller
    # builds a chapter-qualified target path (``chp_9__sec_9b``) — mirroring the
    # internal lane — instead of dropping the chapter. A chapter-only citation
    # (``… (NNN/YYYY) 5 luvussa``) sets ``chapter`` with an empty section_label.
    chapter: Optional[str] = None
    # Literal matched anchor surface (e.g. "lannoitelain (711/2022)"), used by
    # the caller to locate the citation's byte span in the source xml_bytes.
    surface_text: str = ""


class PlainTextStatuteCitationRecognizer:
    """Named recognizer for Finnish plain-text statute citations (AGENTS.md §1.13).

    Extracts statute citations from <p> text that is NOT inside <ref> elements,
    to complement the structured AKN <ref>-based extraction.

    This is a SINGLE-PASS structured recognizer scanning non-ref text fragments
    within <p> elements, not N overlapping backtracking regexes. Each match
    yields (statute_id, section_label_or_empty, start_position) tuples.

    Statute ID form: "NUMBER/YEAR" (canonical, matching Finnish statute_id format).
    Section label: extracted when present; empty string otherwise.

    Usage:
        recognizer = PlainTextStatuteCitationRecognizer()
        for statute_id, section_label in recognizer.scan_non_ref_text(p_element):
            ...

    Per AGENTS.md §1.11:
        - Module-scope compiled pattern _PLAIN_TEXT_FI_STATUTE_RE.
        - Substring guards applied before regex scan.
        - Bounded quantifiers; no adjacent unbounded repeats.
    """

    def _collect_non_ref_text(
        self, p_el: ET.Element[str], *, include_ref_text: bool = False
    ) -> str:
        """Collect text of <p> element, by default excluding <ref> inner text.

        Returns the concatenated text content of:
          - p_el.text (direct text before first child)
          - For each non-<ref> child: child.text + child.tail
          - For each <ref> child: ONLY child.tail (the text AFTER the ref,
            not inside it — since it's already captured by the <ref> extractor)

        This default ensures we do NOT double-count text that was already
        covered by an AKN <ref> element.

        ``include_ref_text`` is the annotation-independence MEASUREMENT path
        (grammar7 §13-C/E): when True the ``<ref>`` inner text is treated as
        ordinary plain text so the text lane gets a real shot at the cite the
        production pipeline hid inside the editorial markup. It is OFF by default,
        so production behaviour is unchanged.
        """
        ref_local = "ref"  # AKN local name

        parts: List[str] = []
        if p_el.text:
            parts.append(p_el.text)

        for child in p_el:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if local == ref_local:
                if include_ref_text:
                    # MEASUREMENT mode: treat the <ref> inner text as plain text
                    # so the text lane can recover the cite the markup carried.
                    if child.text:
                        parts.append(child.text)
                    for gc in child.iter():
                        if gc is child:
                            continue
                        if gc.text:
                            parts.append(gc.text)
                        if gc.tail:
                            parts.append(gc.tail)
                # Always include the tail (text immediately after the </ref> tag).
                if child.tail:
                    parts.append(child.tail)
            else:
                # Non-ref child: include its text subtree + tail
                if child.text:
                    parts.append(child.text)
                for grandchild in child.iter():
                    if grandchild is child:
                        continue
                    gl = grandchild.tag.split("}")[-1] if "}" in grandchild.tag else grandchild.tag
                    if gl != ref_local and grandchild.text:
                        parts.append(grandchild.text)
                    if grandchild.tail:
                        parts.append(grandchild.tail)
                if child.tail:
                    parts.append(child.tail)

        return "".join(parts)

    def scan(
        self,
        p_el: ET.Element[str],
    ) -> List[Tuple[str, str]]:
        """Scan a <p> element for plain-text Finnish statute citations.

        Returns a list of (statute_id, section_label) tuples:
          - statute_id:    "NUMBER/YEAR" canonical form
          - section_label: e.g. "7", "7a", or "" if not present

        This is the statute+section view. For sub-section (momentti / kohta)
        precision used by deeplink consumers, use :meth:`scan_precise`.

        Per AGENTS.md §1.11: substring guards applied before regex scan.
        """
        return [(hit.statute_id, hit.section_label) for hit in self.scan_precise(p_el)]

    def scan_precise(
        self,
        p_el: ET.Element[str],
        *,
        include_ref_text: bool = False,
    ) -> List[PlainTextStatuteHit]:
        """Scan a <p> element returning provision-precise statute citation hits.

        Each hit carries the momentti (subsection) and kohta (item) precision
        the citation names, so a deeplink consumer can target the exact
        provision rather than only the §. Citations that stop at the § yield a
        hit with ``subsection_num=None`` (section-level fallback).

        ``include_ref_text`` (annotation-independence measurement) folds the
        ``<ref>`` inner text into the scanned text; OFF by default. When folding,
        Finlex's habit of SPLITTING the statute-name prose from the ``(NNN/YYYY)``
        id across the ``<ref>`` boundary leaves a long whitespace run (the XML
        indentation around the element) between the name head and the id — far
        more than the recogniser's ``\\s{0,5}`` name→id gap allows. So in folding
        mode the inter-token whitespace runs are first collapsed to a single space
        (``annetun lain \\n<indent>(688/1988)`` → ``annetun lain (688/1988)``),
        letting the SAME by-name+id anchor bind the cite from text alone. This is
        bounded: it only removes markup whitespace, so a name and id separated by
        intervening WORDS stay non-adjacent and never bind. It applies ONLY in the
        fold path; the default (production) text is unchanged byte-for-byte.

        Per AGENTS.md §1.11: substring guards applied before regex scan.
        """
        text = self._collect_non_ref_text(p_el, include_ref_text=include_ref_text)
        if not text:
            return []

        if include_ref_text:
            # Collapse markup whitespace runs so a name head split from its
            # ``(id)`` by the ``<ref>`` boundary becomes adjacent again. Bounded
            # (whitespace only — never merges across intervening words).
            text = _WHITESPACE_RUN_RE.sub(" ", text)

        return self.scan_text(text)

    def scan_text(self, text: str) -> List[PlainTextStatuteHit]:
        """Scan a plain text STRING for provision-precise statute citation hits.

        Text-level twin of :meth:`scan_precise` (which first extracts non-``<ref>``
        text from a ``<p>`` element, then delegates here). This is the shared
        statute-cite recognizer surface for any caller that already holds the body
        text as a string — e.g. the inline-citation lane, which scans ``<p>`` text
        nodes directly. Routing both lanes through this one method keeps the
        statute-id + section/momentti grammar UNIFIED (AGENTS.md §1.13): there is
        exactly one structural parser for ``name (NNN/YYYY) <provision tail>``.

        Per AGENTS.md §1.11: substring guard applied before regex scan.
        """
        if not text:
            return []

        # Substring guard (fast path — eliminates ~99% of non-matching calls).
        # A statute citation always carries the ``(NUMBER/YEAR)`` parenthetical,
        # so "(" is the mandatory marker. The "§" is NOT required: a citation to
        # a whole act ("…annetussa laissa (205/2000)") names no section. Gating
        # on "§" alone silently dropped every section-less id-cite; the regex's
        # mandatory by-name anchor already bounds precision, so "(" suffices.
        if _PLAIN_TEXT_GUARD_PAREN not in text:
            return []

        results: List[PlainTextStatuteHit] = []
        seen_ids: set[str] = set()

        for m in _PLAIN_TEXT_FI_STATUTE_RE.finditer(text):
            num_raw = m.group(1)
            year = m.group(2)

            # Entry-into-force editorial date-ref decline. Finlex commencement
            # footnotes read ``…tulee voimaan 13.6.1929/228`` — a date glued to a
            # running number, which the by-id anchor must NOT promote to a
            # CROSS_STATUTE cite. If the matched ``(NNN/YYYY)``/``NNN/YYYY`` id is
            # immediately preceded (modulo whitespace) by a ``d.m.YYYY`` date, the
            # whole token is an editorial date-ref owned by the temporal lane;
            # skip it. Bounded back-scan over a short window before the match.
            preceding = text[max(0, m.start() - 16) : m.start()]
            if _ENTRY_INTO_FORCE_DATEREF_RE.search(preceding + m.group(0)):
                continue

            # Two-digit year ids ("(307/86)") are common in pre-2000 statutes.
            # Expand to a full century: a 2-digit year <= the current 2-digit
            # year maps to 20xx, otherwise 19xx (Finnish statute ids run from
            # the 1800s to present, so this window is unambiguous in practice).
            if len(year) == 2:
                yy = int(year)
                current_yy = date.today().year % 100
                century = 2000 if yy <= current_yy else 1900
                year = str(century + yy)

            # Sanity: year must be plausible (applied to the EXPANDED year).
            year_int = int(year)
            if year_int < 1700 or year_int > 2100:
                continue

            # Sanity: number must be non-zero
            num_int = int(num_raw)
            if num_int <= 0 or num_int > 999999:
                continue

            # The TARGET link id is the canonical corpus-key orientation
            # YEAR/NUMBER (e.g. "2001/55"), the SAME form the <ref>-element lane
            # mints via cross_refs._make_statute_id and the form the corpus store
            # keys statutes under. Use that helper as the single source of truth
            # so a plain-text cross-statute cite dedups/merges onto the SAME
            # canonical entity node as its <ref>-element citation, instead of
            # minting a non-canonical NUMBER/YEAR node that never merges.
            # NOTE: the human-visible surface_text stays the visible NUMBER/YEAR
            # form ("(55/2001)") — only the link target canonicalizes.
            statute_id = _make_statute_id(year, str(num_int))

            # Parse the structural tail (everything after the ``(id)`` paren)
            # through the shared section / sub-ref recognizers in BODY mode, so
            # section ranges (``108—110 §``), coordination (``6 ja 8 §``), and
            # momentti precision (``§:n 1 momentissa``) expand with the same
            # expressiveness as the amendment grammar. Bound the tail slice so
            # the recognizer does not scan the rest of the paragraph; the
            # recognizer itself stops at the first non-section token, and a
            # short window suffices for a citation tail.
            tail = text[m.end() : m.end() + 120]
            targets = parse_body_provision_tail(tail)

            if not targets:
                # The anchor matched a statute id but no parsable § tail follows
                # — a statute-level citation. Emit one section-less hit (the
                # STATUTE_ONLY fallback the caller types).
                targets = [BodyProvisionTarget(section_label="")]

            for tgt in targets:
                # Deduplicate same provision within this <p>. The key includes
                # the chapter and the sub-section precision so distinct
                # momentit/kohdat — and distinct chapters of one statute — are not
                # collapsed into one hit.
                key = "/".join(
                    part for part in (
                        statute_id,
                        tgt.chapter or "",
                        tgt.section_label,
                        str(tgt.subsection_num) if tgt.subsection_num is not None else "",
                        tgt.item_label or "",
                    )
                )
                if key in seen_ids:
                    continue
                seen_ids.add(key)

                results.append(PlainTextStatuteHit(
                    statute_id=statute_id,
                    section_label=tgt.section_label,
                    subsection_num=tgt.subsection_num,
                    item_label=tgt.item_label,
                    chapter=tgt.chapter,
                    surface_text=m.group(0),
                ))

        return results


# Module-level singleton recognizer (built once at import time)
_PLAIN_TEXT_RECOGNIZER = PlainTextStatuteCitationRecognizer()

# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Container for all artifacts from one extraction pass.

    mentions:         Successfully typed ReferenceMention records.
    rejected:         RejectedRefCandidate records (non-empty citations that
                      failed grammar or sanity).
    broken_findings:  BrokenReferenceFinding records.
    ambiguous_findings: AmbiguousReferenceFinding records.
    approximate_findings: ApproximateReferenceFinding records.
    diagnostics:      CrossRefDiagnostic records from underlying extractor.
    """

    mentions: List[ReferenceMention] = field(default_factory=list)
    rejected: List[RejectedRefCandidate] = field(default_factory=list)
    broken_findings: List[BrokenReferenceFinding] = field(default_factory=list)
    ambiguous_findings: List[AmbiguousReferenceFinding] = field(default_factory=list)
    approximate_findings: List[ApproximateReferenceFinding] = field(default_factory=list)
    diagnostics: List[CrossRefDiagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source span provenance
# ---------------------------------------------------------------------------
#
# OFFSET UNIT (consistent across ALL three lanes): BYTE offset into the source
# statute's ``xml_bytes``. ``SourceSpan.source_file`` carries the statute_id
# (the only stable document identity at this layer; the interlink overlay reads
# it as ``source_artifact_id``).
#
#   - <ref> lane:        byte span of the inline ``<ref>…</ref>`` element,
#                        carried on CrossRefEdge.source_byte_offset/len.
#   - EU lane:           byte span of the EU citation surface (recognizer char
#                        offset → byte offset via UTF-8 prefix length, computed
#                        in cross_refs.extract_eu_refs), carried on the edge.
#   - plain-text lane:   byte span of the matched statute-citation surface,
#                        located in xml_bytes by searching for the matched text.
#
# Every emitted mention in these lanes carries a non-None SourceSpan with
# byte_len > 0 whenever the surface could be located in the raw bytes. Where a
# surface cannot be located (rare; e.g. an href re-encoded by the parser), the
# span stays None — fail-loud-by-absence rather than a fabricated zero offset.


def _find_with_left_boundary(haystack: bytes, needle: bytes, start: int) -> int:
    """Locate ``needle`` in ``haystack`` from ``start``, skipping matches lodged
    inside a longer number run.

    A reference surface that begins with a digit is a section/momentti/article
    number (``"56 \xc2\xa7:ssa"``); it must not be re-anchored at the tail of a
    longer number (``"156 \xc2\xa7:ssa"``), where a plain :meth:`bytes.find` would
    place it. Reject a candidate whose immediately preceding byte is also an ASCII
    digit and keep scanning. Surfaces that do not begin with a digit are
    unaffected (the common case), so this is a targeted guard, not a slowdown.
    """
    leading_digit = needle[:1].isdigit()
    pos = start
    while True:
        i = haystack.find(needle, pos)
        if i < 0:
            return -1
        if leading_digit and i > 0 and haystack[i - 1 : i].isdigit():
            pos = i + 1
            continue
        return i


def _span_from_edge(
    edge: CrossRefEdge,
    source_statute_id: str,
) -> Optional[SourceSpan]:
    """Build a SourceSpan from a CrossRefEdge's recovered byte offset.

    Returns None when the edge carries no located byte span (metadata edges, or
    a CITES/EU edge whose surface could not be located in the raw bytes).
    UNIT: bytes into the source statute's xml_bytes.
    """
    if edge.source_byte_offset is None or edge.source_byte_len <= 0:
        return None
    return SourceSpan(
        source_file=source_statute_id,
        byte_offset=edge.source_byte_offset,
        byte_len=edge.source_byte_len,
    )


# ---------------------------------------------------------------------------
# CrossRefEdge → ReferenceMention lift
# ---------------------------------------------------------------------------


def _parse_provision_ref_from_path(
    statute_id: str,
    provision_path: str,
) -> ProvisionRef:
    """Build a ProvisionRef from a statute_id and AKN provision_path fragment.

    The AKN path fragment is like "sec_7", "sec_7_sub_3", "sec_12a_sub_2".
    We extract human-readable labels where possible.
    """
    section_label = ""
    subsection_num: Optional[int] = None

    if provision_path:
        m_sec = _AKN_SECTION_PATH_RE.search(provision_path)
        if m_sec:
            section_label = m_sec.group(1)
        else:
            # No AKN ``sec_N`` path matched. The authority (``nojalla``) lane
            # carries the cited section as a BARE label (``"37"``, ``"8"``,
            # ``"115a"``) on the edge's target_section, not a glued AKN path —
            # accept it directly so the §37/§36/§8 is retained instead of being
            # silently dropped. A comma-joined list (``"8,36"``, emitted when one
            # parent law is cited with several sections) keeps its first member
            # as the primary section label; the full path string is preserved on
            # provision_path for any consumer that needs the rest.
            first = provision_path.split(",", 1)[0].strip()
            m_bare = _BARE_SECTION_LABEL_RE.match(first)
            if m_bare:
                section_label = m_bare.group(1)

        m_sub = _AKN_SUBSECTION_PATH_RE.search(provision_path)
        if m_sub:
            subsection_num = int(m_sub.group(1))

    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=section_label,
        subsection_num=subsection_num,
    )


# Authority-basis target-kind values that mean "the cited basis is a laki /
# statute, NOT a delegated instrument". Carried on an ISSUED_UNDER edge as
# ``target_kind`` (set by the graph layer from the ``nojalla`` surface inflection
# or the target's statute_type). An act-basis is a CROSS_STATUTE reference; a
# decree/decision basis (a decree CAN be issued under another decree) stays a
# NON_STATUTORY_INSTRUMENT. ``"act"`` is the only statute-typed value.
_AUTHORITY_STATUTE_KINDS = frozenset({"act"})


def _edge_to_cite_kind(
    edge: CrossRefEdge,
    source_statute_id: str,
) -> CiteKind:
    """Map CrossRefEdge.edge_type to CiteKind.

    AGENTS.md §1.1: mapping is deterministic. No fallback.

    CITES:        CROSS_STATUTE (or INTERNAL if target == source; EU if the
                  target is an EU id; NON_STATUTORY_INSTRUMENT if the target is
                  an HE government-proposal backlink ``he/...`` — HE is
                  preparatory material, not an enacted statute).
    REPEALS:      CROSS_STATUTE (metadata-level fact).
    ISSUED_UNDER: CROSS_STATUTE when the cited authority basis is a laki/statute
                  (``target_kind == "act"``); otherwise NON_STATUTORY_INSTRUMENT.
                  The ``nojalla`` authority basis names the ACT that delegated the
                  rulemaking power and is a statute cross-reference, NOT a
                  non-statutory instrument. A decree CAN be issued under another
                  decree's authority, so the act-vs-instrument split is taken from
                  the edge's ``target_kind`` (the surface inflection / target
                  statute_type), never assumed.
    ISSUES:       NON_STATUTORY_INSTRUMENT (source issued a decree as target —
                  the target IS the delegated instrument).
    """
    edge_type = edge.edge_type
    if edge_type == "CITES":
        if edge.target_statute_id == source_statute_id:
            return CiteKind.INTERNAL
        # EU ids are "eu/TYPE/YEAR/NUMBER"
        if edge.target_statute_id.startswith("eu/"):
            return CiteKind.EU
        # HE government-proposal backlinks (he/YEAR/NUMBER) are PREPARATORY
        # material, NOT an enacted statute. Finlex marks the HE→act lineage with
        # an AKN <ref href=".../government-proposal/..."> in the preliminaryWork
        # ("Esityöt") footer, so cross_refs._parse_ref_href emits it as a CITES
        # edge to a "he/..." target. Type it the same way the preparatory text
        # lane types every other preparation-chain instrument
        # (NON_STATUTORY_INSTRUMENT) so consumers treat the whole HE/HaVM/EV/EU
        # chain uniformly instead of mistaking the HE for an act cross-reference.
        if edge.target_statute_id.startswith("he/"):
            return CiteKind.NON_STATUTORY_INSTRUMENT
        return CiteKind.CROSS_STATUTE
    if edge_type == "ISSUED_UNDER":
        # The authority basis is the cited ACT under which the source decree was
        # issued. When the graph layer has tagged the basis as a laki/statute,
        # this is a statute cross-reference — NOT an instrument. The tag rides on
        # the edge as ``target_kind`` (absent on un-tagged edges → legacy
        # instrument typing, so no over-correction of a genuine decree basis).
        target_kind = str(getattr(edge, "target_kind", "") or "").lower()
        if target_kind in _AUTHORITY_STATUTE_KINDS:
            return CiteKind.CROSS_STATUTE
        return CiteKind.NON_STATUTORY_INSTRUMENT
    if edge_type == "ISSUES":
        return CiteKind.NON_STATUTORY_INSTRUMENT
    if edge_type == "REPEALS":
        return CiteKind.CROSS_STATUTE
    if edge_type == "AMENDS":
        # The johtolause <affectedDocument> names the enacted act THIS statute
        # amends — always another Finnish statute/decree (CROSS_STATUTE). The
        # amendment ROLE is carried by edge_subtype="AMENDS", not by widening the
        # cite_kind.
        return CiteKind.CROSS_STATUTE
    # Unknown edge_type: default CROSS_STATUTE, emitter will flag
    return CiteKind.CROSS_STATUTE


def _edge_to_confidence(
    edge: CrossRefEdge,
    target_ref: ProvisionRef,
) -> CiteConfidence:
    """Assign a resolution-status-driven confidence to a CrossRefEdge.

    The status records *how far* the structural markup pinned the target, per
    the resolution-status ladder (``FI_REFERENCE_CATALOGUE.md`` §0.1). It is
    driven by the ACTUAL resolution outcome — never hardcoded:

    - **Metadata / amendment edges** (``REPEALS`` / ``ISSUED_UNDER`` /
      ``ISSUES`` / ``AMENDS``) target the *whole act* by construction: the
      ``finlex:`` metadata names an act, not a provision, so act-level
      resolution is *complete*, not pending → ``EXACT``.
    - **CITES `<ref>` edges with a resolved provision** (the AKN href carries a
      ``#sec_N`` fragment, so ``target_ref`` has a ``section_label``) name both
      act and provision unambiguously → ``EXACT``.
    - **CITES `<ref>` edges that name only an act** (bare statute href, no
      provision fragment → empty ``section_label``) have a known act but a
      *pending* provision target → ``STATUTE_ONLY``. Per tag-don't-guess the
      provision is not silently widened to "the whole act as if exact".

    Target *re-validation* against the consolidated statute graph (``BROKEN``)
    is a separate bitemporal projection-phase pass (``broken_detection``), not
    an extraction-time concern.
    """
    if edge.edge_type == "CITES":
        # A CITES <ref> resolves to EXACT only when the AKN href pinned a
        # provision (#sec_N → section_label). A bare act href leaves the
        # in-act provision pending → STATUTE_ONLY (not a guessed whole-act).
        if target_ref.section_label:
            return CiteConfidence.EXACT
        return CiteConfidence.STATUTE_ONLY
    # REPEALS / ISSUED_UNDER / ISSUES / AMENDS: the finlex metadata / johtolause
    # <affectedDocument> names the WHOLE act as the target by construction, so
    # act-level resolution is complete → EXACT (no pending provision).
    return CiteConfidence.EXACT


def _source_provision_ref(
    edge: CrossRefEdge,
    source_statute_id: str,
) -> ProvisionRef:
    """Build source ProvisionRef from a CrossRefEdge."""
    section_label = edge.source_section or ""
    return ProvisionRef(
        statute_id=source_statute_id,
        provision_path="",
        section_label=section_label,
    )


def _target_provision_ref(edge: CrossRefEdge) -> ProvisionRef:
    """Build target ProvisionRef from a CrossRefEdge."""
    return _parse_provision_ref_from_path(
        edge.target_statute_id,
        edge.target_section or "",
    )


def _edge_to_mention(
    edge: CrossRefEdge,
    source_statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
) -> ReferenceMention:
    """Lift one CrossRefEdge to a ReferenceMention."""
    cite_kind = _edge_to_cite_kind(edge, source_statute_id)
    src_ref = _source_provision_ref(edge, source_statute_id)
    tgt_ref = _target_provision_ref(edge)
    # Resolution-status-driven: a CITES <ref> with no resolved provision
    # (bare act href) is STATUTE_ONLY, not a guessed whole-act EXACT.
    confidence = _edge_to_confidence(edge, tgt_ref)

    # For CITES edges, phrase_lemma is "ref_element" (AKN <ref> element).
    # AMENDS edges come from the johtolause <affectedDocument> element, so carry
    # its own syntactic class. For metadata edges, it is the edge_type name.
    if edge.edge_type == "CITES":
        phrase_lemma = "ref_element"
    elif edge.edge_type == "AMENDS":
        phrase_lemma = "affected_document"
    else:
        phrase_lemma = edge.edge_type  # REPEALS / ISSUED_UNDER / ISSUES

    # Subtype defaults to the edge_type (CITES / REPEALS / ISSUED_UNDER / ISSUES).
    # HE government-proposal <ref> backlinks are preparatory material, so carry
    # the preparatory HE kind ("he") as the subtype — identical to the subtype
    # the preparatory text lane emits for non-HE chain instruments — so the whole
    # HE/HaVM/EV/EU preparation chain presents uniformly to consumers that split
    # on edge_subtype.
    if edge.edge_type == "CITES" and edge.target_statute_id.startswith("he/"):
        edge_subtype = PreparatoryReferenceKind.HE.value
    else:
        edge_subtype = edge.edge_type

    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=tgt_ref,
        cite_kind=cite_kind,
        cite_confidence=confidence,
        phrase_lemma=phrase_lemma,
        # Byte span of the <ref> element in the source xml_bytes (None for
        # metadata edges that have no body surface). UNIT: bytes.
        source_span=_span_from_edge(edge, source_statute_id),
        valid_at_interval=valid_at_interval,
        edge_subtype=edge_subtype,
        target_stat_hash=edge.target_stat_hash if edge.target_stat_hash else None,
        surface_text=edge.surface_text,
    )


# ---------------------------------------------------------------------------
# EU mention lift (from extract_eu_refs)
# ---------------------------------------------------------------------------


def _eu_edge_to_mention(
    edge: CrossRefEdge,
    source_statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
) -> ReferenceMention:
    """Lift an EU CrossRefEdge (always CITES) to a ReferenceMention."""
    src_ref = ProvisionRef(
        statute_id=source_statute_id,
        provision_path="",
        section_label=edge.source_section or "",
    )
    tgt_ref = ProvisionRef(
        statute_id=edge.target_statute_id,
        provision_path="",
    )
    # An EU act named only as repealed provenance inside a long-form citation
    # carries edge_subtype="REPEALS_EMBEDDED" (distinct from the statute's own
    # finlex:repeals metadata). All other EU citations stay "CITES".
    edge_subtype = edge.edge_subtype or "CITES"
    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=tgt_ref,
        cite_kind=CiteKind.EU,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="eu_text_pattern",
        # Byte span of the EU citation surface in the source xml_bytes. UNIT: bytes.
        source_span=_span_from_edge(edge, source_statute_id),
        valid_at_interval=valid_at_interval,
        edge_subtype=edge_subtype,
        # The matched EU citation surface (e.g. "(EY) N:o 999/2001") — a verbatim
        # substring of the source text. Carried so the hub's byte re-anchoring,
        # the viewer overlay, and provenance behave like the <ref>/plain-text
        # lanes; previously this lane left surface_text empty.
        surface_text=edge.surface_text,
    )


# ---------------------------------------------------------------------------
# CrossRefDiagnostic passthrough
# ---------------------------------------------------------------------------


def _diagnostic_to_rejected(
    diag: CrossRefDiagnostic,
    *,
    is_skip: bool,
) -> Optional[RejectedRefCandidate]:
    """Convert a CrossRefDiagnostic to a RejectedRefCandidate where appropriate.

    Self-reference skips are NOT rejections — they are valid structural records.
    Only diagnostics for extraction failures become RejectedRefCandidate.
    """
    if diag.rule_id in (
        "fi_cross_ref_self_reference_skipped",
        "fi_cross_ref_xml_parse_failed",
    ):
        # These remain as CrossRefDiagnostic in the diagnostics list;
        # xml_parse_failed is a blocker diagnostic, not a rejected candidate.
        return None
    # Unknown diagnostic family — surface as rejected candidate
    return RejectedRefCandidate(
        rule_id=diag.rule_id,
        phase=diag.phase,
        source_statute_id=diag.source_statute_id,
        reason=diag.reason,
        matched_text=diag.href or "",
        # CrossRefDiagnostic carries no byte offset (metadata-lane diagnostics
        # are not body-text refs and have no surface span to recover). None.
        source_span=None,
        blocking=diag.blocking,
        strict_disposition=diag.strict_disposition,
    )


# ---------------------------------------------------------------------------
# Annotation-independence measurement toggle (grammar7 §13-C/E)
# ---------------------------------------------------------------------------
#
# The principle (grammar7): LawVM should delete annotation DEPENDENCE, not
# annotation USE — parse deterministically from text, treat ``<ref>`` as a
# fallible witness. This toggle is the cheapest decisive experiment for the
# annotation-independence question: when ON, ``extract_all_reference_mentions``
# SKIPS the AKN ``<ref>``-element semantic-annotation lane
# (``extract_reference_mentions`` → ``extract_cross_refs``, which lifts inline
# ``<ref>`` elements AND finlex: metadata edges) and runs ONLY the text-derived
# lanes (EU text scan, plain-text statute citations, surface-grammar, and the
# preparatory chain — all of which parse from source text, not editorial
# markup). It ALSO drops the ``<ref>``-derived ``ref_covered_statute_ids`` dedup
# guard so the plain-text lane is not suppressed by annotations that are now
# being ignored (else the measurement is contaminated: a cite the text lane
# WOULD recover stays hidden behind a now-ignored ``<ref>``).
#
# This is a MEASUREMENT mode, NOT a new default. Fail-closed to OFF (current
# behaviour) on any unset / empty / falsy value, so the production replay /
# parquet projection are byte-identical to today unless explicitly opted in.

_IGNORE_ANNOTATIONS_ENV = "LAWVM_IGNORE_SEMANTIC_ANNOTATIONS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def ignore_semantic_annotations() -> bool:
    """Whether the ``<ref>``-element semantic-annotation lane is suppressed.

    Reads ``LAWVM_IGNORE_SEMANTIC_ANNOTATIONS`` from the environment each call
    (so a test / census can flip it per-invocation). Fail-closed: any unset,
    empty, or non-truthy value means OFF = current production behaviour. Only an
    explicit truthy value (``1`` / ``true`` / ``yes`` / ``on``, case-insensitive)
    turns the measurement mode ON.
    """
    return os.environ.get(_IGNORE_ANNOTATIONS_ENV, "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Main extraction entry points
# ---------------------------------------------------------------------------


def extract_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> ExtractionResult:
    """Extract ReferenceMention records from a Finnish statute XML.

    This wraps ``extract_cross_refs`` and promotes each CrossRefEdge to
    a ReferenceMention using the core typed primitive.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source, e.g. "711/2022".
        valid_at_interval: (start, end) date range for which these references
                           hold. Pass (None, None) for "whole statute history."
        strict:            If True, APPROXIMATE/UNRESOLVED mentions cause a
                           strict-mode block rather than a warning.

    Returns:
        ExtractionResult with mentions, rejected candidates, findings.

    Per AGENTS.md §1.1: target not found → UNRESOLVED, not widened.
    Per AGENTS.md §1.8: every CrossRefDiagnostic is preserved.
    Per AGENTS.md §1.11: all patterns compiled at module scope.
    """
    result = ExtractionResult()

    # Collect CrossRefDiagnostic from underlying extractor
    diag_list: List[CrossRefDiagnostic] = []
    edges: List[CrossRefEdge] = extract_cross_refs(
        xml_bytes, statute_id, diagnostics_out=diag_list
    )

    # Preserve all diagnostics (AGENTS.md §1.8)
    result.diagnostics.extend(diag_list)

    # Convert edges → ReferenceMention
    for edge in edges:
        mention = _edge_to_mention(edge, statute_id, valid_at_interval)

        # Strict-mode check: APPROXIMATE and UNRESOLVED are blocked in strict mode
        if strict and mention.cite_confidence in (
            CiteConfidence.APPROXIMATE,
            CiteConfidence.UNRESOLVED,
        ):
            # In strict mode, emit a blocking diagnostic rather than silently reject.
            # The mention is still emitted (audit trail preserved per §1.8).
            result.rejected.append(
                RejectedRefCandidate(
                    rule_id="fi_ref_mention_strict_confidence_barrier",
                    phase="cross_ref_extraction",
                    source_statute_id=statute_id,
                    reason=(
                        f"strict mode: {mention.cite_confidence.value} confidence "
                        f"for {edge.edge_type} → {edge.target_statute_id}"
                    ),
                    matched_text=edge.target_statute_id,
                    # Same byte span as the underlying mention (edge surface). UNIT: bytes.
                    source_span=_span_from_edge(edge, statute_id),
                    blocking=True,
                    strict_disposition="block",
                )
            )

        result.mentions.append(mention)

    # Convert diagnostics to RejectedRefCandidate where appropriate
    for diag in diag_list:
        rej = _diagnostic_to_rejected(diag, is_skip=True)
        if rej is not None:
            result.rejected.append(rej)

    return result


def extract_affected_document_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> ExtractionResult:
    """Extract johtolause amendment-target ReferenceMention records.

    Wraps :func:`extract_affected_document_refs` and lifts each AMENDS
    ``CrossRefEdge`` (one per distinct ``<affectedDocument>`` target) to a
    ReferenceMention. Each mention is ``cite_kind=CROSS_STATUTE`` /
    ``edge_subtype="AMENDS"`` / ``phrase_lemma="affected_document"`` — the surface
    link to the act this statute amends, which lives in the preamble enacting
    clause OUTSIDE ``<body>`` and so is invisible to the inline-``<ref>`` lane.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source.
        valid_at_interval: Date range for which these references hold.

    Returns:
        ExtractionResult with the amendment-target mentions (+ self-reference
        skip diagnostics).
    """
    result = ExtractionResult()

    diag_list: List[CrossRefDiagnostic] = []
    edges: List[CrossRefEdge] = extract_affected_document_refs(
        xml_bytes, statute_id, diagnostics_out=diag_list
    )
    result.diagnostics.extend(diag_list)
    for edge in edges:
        result.mentions.append(
            _edge_to_mention(edge, statute_id, valid_at_interval)
        )
    return result


def extract_eu_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> ExtractionResult:
    """Extract EU cross-jurisdiction ReferenceMention records from a statute.

    Wraps ``extract_eu_refs`` and promotes each EU CrossRefEdge to a
    ReferenceMention with cite_kind=EU.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source.
        valid_at_interval: Date range for which these references hold.

    Returns:
        ExtractionResult with EU mentions.
    """
    result = ExtractionResult()

    edges: List[CrossRefEdge] = extract_eu_refs(xml_bytes, statute_id)
    for edge in edges:
        mention = _eu_edge_to_mention(edge, statute_id, valid_at_interval)
        result.mentions.append(mention)

    return result


def extract_plain_text_statute_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    ref_covered_statute_ids: Optional[set[str]] = None,
    include_ref_text: bool = False,
) -> ExtractionResult:
    """MEASUREMENT/AUDIT recognizer for plain-text statute citations — NO production authority.

    CONTRACT (post-census, base f5843e95): this is the regex-backed plain-text
    statute-citation recognizer. It is NOT a production reference source and carries
    NO reference authority. The production citation lane in
    :func:`extract_all_reference_mentions` is the construction parse
    (:func:`extract_inline_id_construction_mentions`) and that lane ALONE — the
    legacy regex residue fallback this function once fed
    (``phrase_lemma="plain_text_fallback"``) was DELETED after a whole-corpus census
    proved every firing was a 2-digit-year citation the construction lane already
    caught with the correct (causal) century, mis-duplicated by the regex's acausal
    ``date.today()`` pivot. See ``tests/test_fi_ref_legacy_regex_residue_census.py``.

    This function is retained for exactly two non-production uses:

      1. The annotation-independence MEASUREMENT lane
         (``extract_all_reference_mentions(..., ignore_annotations=True)``), which
         folds ``<ref>`` inner text (``include_ref_text=True``) to measure how much
         of the annotated surface the text recognizer could recover unaided. This is
         a metric, not a reference-emission path.
      2. Direct unit-testing of the ``PlainTextStatuteCitationRecognizer`` grammar
         family.

    Callers MUST NOT treat the ``statute_id`` orientation of its mentions as legal
    truth: the regex 2-digit-year pivot is acausal (``date.today()`` based) and can
    mint future-dated ids; only the construction lane resolves the century causally.
    De-deprecated (no longer a production fallback) but explicitly non-authoritative.

    Extract plain-text Finnish statute citations NOT covered by <ref> markup.

    Walks <p> elements in the AKN body, collecting text that is NOT inside
    <ref> child elements, and applies the PlainTextStatuteCitationRecognizer
    to find statute citations of the form "[word]lain (711/2022) 7 §".

    Per AGENTS.md §1.13: PlainTextStatuteCitationRecognizer is a named
    single-pass recognizer for the Finnish statute citation grammar family
    (-lain, -asetuksen, -laissa, etc.), not N overlapping regex passes.

    Per AGENTS.md §1.8: all results are emitted as ReferenceMention records;
    no candidate disappears silently.

    Args:
        xml_bytes:               Raw XML bytes of the statute.
        statute_id:              Canonical statute ID of the source, e.g. "711/2022".
        valid_at_interval:       (start, end) date range for these references.
        ref_covered_statute_ids: Set of statute IDs already captured by the
                                 <ref>-element extraction pass for this statute.
                                 When provided, plain-text mentions for the same
                                 target statute_id are skipped to avoid double-emission
                                 at the statute level.
                                 Note: provision-level deduplication is more precise
                                 but requires span tracking; this is the statute-level guard.
        include_ref_text:        Annotation-independence MEASUREMENT mode
                                 (grammar7 §13-C/E). When True the ``<ref>`` inner
                                 text is folded into the scanned plain text so the
                                 text lane can recover a cite the production
                                 pipeline hid inside the editorial markup. OFF by
                                 default — production behaviour is unchanged.

    Returns:
        ExtractionResult with plain-text ReferenceMention records.
        phrase_lemma is ``"plain_text"`` to distinguish from ``"ref_element"``
        (AKN <ref>-derived) records.
        cite_confidence is EXACT for well-formed statute IDs (NUMBER/YEAR within
        plausible range); confidence elevation to APPROXIMATE is reserved for the
        projection phase when statute-graph resolution occurs.
    """
    result = ExtractionResult()

    if not xml_bytes:
        return result

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # XML parse errors are already reported by extract_reference_mentions;
        # return empty result rather than double-reporting.
        return result

    covered: set[str] = ref_covered_statute_ids or set()
    valid_start, valid_end = valid_at_interval

    # Per-surface byte cursor: locate each matched anchor surface in the raw
    # xml_bytes left-to-right so repeated identical surfaces map to successive
    # byte positions. UNIT: bytes into xml_bytes.
    surface_byte_cursor: dict[bytes, int] = {}

    def _locate_surface_span(
        surface: str,
        local_cache: dict[str, Optional[SourceSpan]],
    ) -> Optional[SourceSpan]:
        # Several targets parsed from ONE anchor (tail expansion to distinct
        # sections) share the same citation surface and therefore the same byte
        # span: cache per surface within a single <p> so they do not advance the
        # cursor past each other. A fresh <p> uses a fresh cache, so a repeated
        # surface in a later paragraph maps to the next byte occurrence.
        if not surface:
            return None
        if surface in local_cache:
            return local_cache[surface]
        needle = surface.encode("utf-8")
        start = _find_with_left_boundary(
            xml_bytes, needle, surface_byte_cursor.get(needle, 0)
        )
        if start < 0:
            # Surface re-encoded/normalized vs raw bytes — fail loud by absence.
            local_cache[surface] = None
            return None
        surface_byte_cursor[needle] = start + 1
        span = SourceSpan(
            source_file=statute_id,
            byte_offset=start,
            byte_len=len(needle),
        )
        local_cache[surface] = span
        return span

    # Walk <p> elements in the body
    _ns_p = f"{{{_AKN_NS}}}p"
    # Also accept bare <p> (some test fixtures omit the namespace on p)
    _bare_p = "p"

    p_elements: List[ET.Element[str]] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "p":
            p_elements.append(el)

    for p_el in p_elements:
        hits = _PLAIN_TEXT_RECOGNIZER.scan_precise(
            p_el, include_ref_text=include_ref_text
        )
        # Per-<p> span cache so multiple targets from one anchor share a span.
        p_span_cache: dict[str, Optional[SourceSpan]] = {}
        for hit in hits:
            target_statute_id = hit.statute_id
            # Skip if this target is already covered by a <ref>-element mention
            if target_statute_id in covered:
                continue

            # Skip self-reference (same logic as <ref> extractor)
            if target_statute_id == statute_id:
                continue

            src_ref = ProvisionRef(
                statute_id=statute_id,
                provision_path="",
                section_label="",
            )
            # Chapter-qualified target path (``chp_9__sec_9b`` / chapter-only
            # ``chp_5``), built with the SAME AKN form the internal lane uses, so
            # a ``N luvun M §`` cross-statute citation keeps its chapter instead of
            # dropping it. Section-only citations keep an empty provision_path
            # (unchanged behavior).
            target_provision_path = (
                chapter_akn_path(hit.chapter, hit.section_label)
                if hit.chapter is not None
                else ""
            )
            tgt_ref = ProvisionRef(
                statute_id=target_statute_id,
                provision_path=target_provision_path,
                section_label=hit.section_label,
                subsection_num=hit.subsection_num,
                item_label=hit.item_label,
            )
            # Resolution status (Tier 1, no registry): the statute id is always
            # explicit in this lane (the ``(NUMBER/YEAR)`` anchor). When the
            # structural tail parsed to a concrete provision (a section — possibly
            # chapter-qualified), the citation resolves EXACT. When the anchor
            # matched an explicit id but only a chapter (``5 luvussa``) or no
            # provision parsed (a bare statute-level reference), the act/chapter is
            # fixed but the in-act section is deferred → STATUTE_ONLY, never
            # silently widened to "whole statute" as if EXACT.
            cite_confidence = (
                CiteConfidence.EXACT
                if hit.section_label
                else CiteConfidence.STATUTE_ONLY
            )
            mention = ReferenceMention(
                source_provision_ref=src_ref,
                target_provision_ref=tgt_ref,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=cite_confidence,
                phrase_lemma="plain_text",
                # Byte span of the matched citation surface in xml_bytes. UNIT: bytes.
                source_span=_locate_surface_span(hit.surface_text, p_span_cache),
                valid_at_interval=(valid_start, valid_end),
                edge_subtype="CITES",
                # The literal matched citation anchor (e.g. "lannoitelain
                # (711/2022)"). Carried so downstream surface consumers (the
                # Legal Surface Graph reference lens) can anchor this mention in
                # raw_text exactly as the <ref>/EU lanes already do; previously
                # this lane left surface_text empty, dropping the mention to a
                # residual instead of a reference_expr node.
                surface_text=hit.surface_text,
            )
            result.mentions.append(mention)

    return result


# ---------------------------------------------------------------------------
# Enclosing-section provenance for internal (same-statute) mentions
# ---------------------------------------------------------------------------
#
# An INTERNAL bare reference (``Edellä 2 momentissa``, ``1 kohdassa``) names a
# provision of the SECTION the citing text sits in; the surface omits that
# section. The downstream elliptical resolver needs the TRUE enclosing section to
# fill the omitted part. We thread it onto each internal mention's
# ``source_provision_ref.section_label`` here, derived from real AKN ancestry —
# the nearest ``<section>`` ancestor of the ``<p>`` the citation sits in — rather
# than re-deriving it from a byte-offset remap downstream (which fails for old
# statutes whose ``<section>`` elements carry no ``eId``).


def _akn_section_label(section_el: ET.Element[str]) -> str:
    """Bare section label of a ``<section>`` from its eId, else its ``<num>``.

    Prefers the eId-derived label (``sec_115a`` -> ``115a``) so it matches the
    body sub-ref grammar's glued AKN form. Falls back to the section's own
    ``<num>`` surface (``10 §.`` -> ``10``, ``115 a §`` -> ``115a``) for statutes
    whose sections carry no eId (pre-eId Finlex consolidations). Returns ``""``
    when neither yields a label (the mention then stays section-less and the
    elliptical resolver tags it OPEN — fail-loud, never guessed).
    """
    eid = section_el.get("eId") or ""
    if eid:
        m = _AKN_SECTION_PATH_RE.search(eid)
        if m is not None:
            return m.group(1)
    for child in section_el:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == "num":
            num_text = (child.text or "").strip()
            nm = _SECTION_NUM_LABEL_RE.match(num_text)
            if nm is not None:
                letter = (nm.group(2) or "").lower()
                return nm.group(1) + letter
            break
    return ""


def _trusted_section_labels(
    root: ET.Element[str],
) -> Optional[frozenset[str]]:
    """The statute's own section labels, IFF the tree is trusted for ABSENCE.

    Returns the set of ``<section>`` labels only when EVERY section carries an
    eId (a consolidated, fully-addressed body) and there are at least three of
    them. On such a body, a section absent from the set is genuinely not part of
    the statute — the signal the internal-ref recogniser uses as a secondary net
    to decline a foreign section number that leaked in as a bogus internal
    target. Returns ``None`` for a partial / un-eId'd / non-consolidated body
    (enacted source, amendment act, pre-eId Finlex consolidation): absence there
    is untrustworthy, so the recogniser must NOT guard (recall over a speculative
    decline). Fail-loud by abstention.
    """
    labels: list[str] = []
    all_eid = True
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local != "section":
            continue
        if not el.get("eId"):
            all_eid = False
            break
        lbl = _akn_section_label(el)
        if lbl:
            labels.append(lbl)
    if not all_eid or len(labels) < 3:
        return None
    return frozenset(labels)


def _fill_bare_momentti_target(
    mention: ReferenceMention,
    enclosing_section_label: str,
) -> ReferenceMention:
    """Fill a bare same-section momentti/kohta TARGET from the enclosing section.

    A purely-bare anaphora (``1 momentissa`` with no explicit ``§``) names a
    momentti/kohta of the SECTION the citing text sits in (drafting convention).
    The recogniser leaves the target's ``section_label`` empty (the surface omits
    it). Without filling, the target collapses to the whole-statute root and the
    citation surfaces as ``open`` despite the section being known. This fills the
    TARGET's section from the enclosing section the extractor already threaded
    onto the source provenance, so ``1 momentissa`` inside section ``34 a``
    resolves to target ``sec=34a mom=1``.

    Fail-loud: only an INTERNAL mention whose target carries a momentti/kohta but
    NO section and NO chapter-qualified ``__`` path is filled, and only when the
    enclosing section is genuinely known. An explicit-section momentti
    (``34 a §:n 1 momentissa``, target already ``sec=34a``) is untouched. An
    unknown enclosing section leaves the target bare (stays ``open`` downstream —
    never a guessed section). The elliptical resolver remains the authority for
    the bare-kohta structural-uniqueness case; this only handles the
    section-from-convention fill that the parquet projection (which does not run
    the elliptical resolver) would otherwise drop.
    """
    if not enclosing_section_label:
        return mention
    if mention.cite_kind is not CiteKind.INTERNAL:
        return mention
    tgt = mention.target_provision_ref
    if tgt is None:
        return mention
    if tgt.section_label:
        return mention
    if "__" in (tgt.provision_path or ""):
        return mention
    # Only fill the bare-MOMENTTI convention case (a momentti is named). A bare
    # KOHTA (item only, no momentti) needs the enclosing section's materialized
    # child STRUCTURE to pick the momentti-with-kohta — that is the elliptical
    # resolver's job (it consults the tree); pre-filling its section here would
    # make the resolver treat it as already-anchored and skip the structural
    # disambiguation. Leave bare-kohta to the resolver (fail-loud there).
    if tgt.subsection_num is None:
        return mention
    return replace(
        mention,
        target_provision_ref=replace(tgt, section_label=enclosing_section_label),
    )


def _enclosing_section_labels(
    root: ET.Element[str],
) -> dict[ET.Element[str], str]:
    """Map each ``<p>`` element to its nearest enclosing ``<section>``'s label.

    Builds a child->parent map once (ElementTree has no parent pointers), then
    walks up from each ``<p>`` to the first ``<section>`` ancestor and records its
    label (eId- or ``<num>``-derived). A ``<p>`` outside any section is absent
    from the map. This is the authoritative "which section am I in" signal the
    elliptical resolver consumes — real ancestry, not a byte-offset remap.
    """
    parent: dict[ET.Element[str], ET.Element[str]] = {}
    for el in root.iter():
        for child in el:
            parent[child] = el

    # Cache a section element's derived label so a section with many <p> is
    # labeled once.
    sec_label_cache: dict[ET.Element[str], str] = {}
    out: dict[ET.Element[str], str] = {}
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local != "p":
            continue
        cur = parent.get(el)
        while cur is not None:
            cur_local = cur.tag.split("}")[-1] if "}" in cur.tag else cur.tag
            if cur_local == "section":
                label = sec_label_cache.get(cur)
                if label is None:
                    label = _akn_section_label(cur)
                    sec_label_cache[cur] = label
                if label:
                    out[el] = label
                break
            cur = parent.get(cur)
    return out


def extract_surface_grammar_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> ExtractionResult:
    """Extract mentions from the standalone surface-grammar recognizers.

    Runs, per ``<p>`` body element, three recognizers whose families are
    disjoint from the ``<ref>``/EU-formal/plain-text lanes:

      - ``recognize_treaty_refs``         — ``SopS NNN/YYYY`` treaty-series cites.
      - ``recognize_vague_refs``          — closed-list vague markers → OPEN.
      - ``recognize_eu_directive_refs``   — EU instrument-by-nickname + ``artikla``.

    Each recognizer reports a char-offset span (or None) relative to the text it
    was handed; this lane re-anchors every mention to a byte span in
    ``xml_bytes`` using its ``surface_text`` (the spanfix convention), and sets
    the source-provision ref to the citing statute. Mentions whose surface
    cannot be located in the raw bytes keep ``source_span=None`` (fail-loud by
    absence; never a fabricated zero offset).
    """
    result = ExtractionResult()
    if not xml_bytes:
        return result
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # Already reported by extract_reference_mentions; do not double-report.
        return result

    # Per-surface byte cursor over xml_bytes; surfaces appear in document order.
    surface_byte_cursor: dict[bytes, int] = {}

    def _relocate(surface: str) -> Optional[SourceSpan]:
        if not surface:
            return None
        needle = surface.encode("utf-8")
        start = _find_with_left_boundary(
            xml_bytes, needle, surface_byte_cursor.get(needle, 0)
        )
        if start < 0:
            return None
        surface_byte_cursor[needle] = start + 1
        return SourceSpan(
            source_file=statute_id,
            byte_offset=start,
            byte_len=len(needle),
        )

    src_ref = ProvisionRef(
        statute_id=statute_id,
        provision_path="",
        section_label="",
    )

    # Map each <p> to its enclosing <section> label (real AKN ancestry), so an
    # INTERNAL bare reference carries the section it sits in on its source
    # provenance. The elliptical resolver consumes this authoritative context
    # instead of re-deriving the enclosing section by a byte-offset remap.
    enclosing_labels = _enclosing_section_labels(root)

    # The statute's own section set when the tree is trusted for ABSENCE (fully
    # eId'd consolidated body), else None. The internal-ref recogniser uses it as
    # a secondary net to decline a foreign section number that leaked in as a
    # bogus internal target (external-law phrase that did not anchor).
    trusted_sections = _trusted_section_labels(root)

    # Statute-local EU-nickname pre-pass (built ONCE over the whole document):
    # an act that coins an ad-hoc ``(jäljempänä <nickname>)`` for an EU instrument
    # binds that nickname → CELEX here, so a later ``<nickname> N artikla`` use in
    # any <p> resolves to the right EU-regulation article instead of being dropped.
    local_eu_aliases = build_statute_local_nicknames("".join(root.itertext()))

    def _src_ref_for(section_label: str) -> ProvisionRef:
        if not section_label:
            return src_ref
        return ProvisionRef(
            statute_id=statute_id,
            provision_path="",
            section_label=section_label,
        )

    def _reanchor(mention: ReferenceMention) -> ReferenceMention:
        return replace(
            mention,
            source_provision_ref=src_ref,
            source_span=_relocate(mention.surface_text or ""),
            valid_at_interval=valid_at_interval,
        )

    def _reanchor_grouped(
        mentions: List[ReferenceMention],
        source_provision_ref: ProvisionRef,
    ) -> List[ReferenceMention]:
        """Re-anchor a lane's mentions, sharing ONE span per coordinated run.

        A coordinated reference (``47 ja 49 §:ssä``, ``1 ja 2 momentissa``) is
        enumerated into one mention PER member, all carrying the SAME whole-
        coordination ``surface_text`` and emitted consecutively for a single
        recognizer match. The per-surface byte cursor in :func:`_relocate`
        advances one document occurrence per call, so calling it once per member
        would (a) walk each member onto a DIFFERENT later occurrence of the same
        surface and (b) starve later occurrences of a span. Group consecutive
        mentions that share a surface and relocate ONCE per group: every member
        of one coordinated occurrence shares that occurrence's whole-coordination
        span. Distinct later occurrences of the same surface still advance the
        cursor (one group = one occurrence), preserving document-order anchoring.
        """
        out: List[ReferenceMention] = []
        i = 0
        n = len(mentions)
        while i < n:
            surface = mentions[i].surface_text or ""
            j = i + 1
            while j < n and (mentions[j].surface_text or "") == surface:
                j += 1
            span = _relocate(surface)
            for k in range(i, j):
                out.append(
                    replace(
                        _fill_bare_momentti_target(
                            mentions[k], source_provision_ref.section_label
                        ),
                        source_provision_ref=source_provision_ref,
                        source_span=span,
                        valid_at_interval=valid_at_interval,
                    )
                )
            i = j
        return out

    for p_el in root.iter():
        local = p_el.tag.split("}")[-1] if "}" in p_el.tag else p_el.tag
        if local != "p":
            continue
        text = "".join(p_el.itertext())
        if not text:
            continue
        for mention in recognize_treaty_refs(text):
            result.mentions.append(_reanchor(mention))
        for mention in recognize_treaty_article_refs(text):
            result.mentions.append(_reanchor(mention))
        for mention in recognize_vague_refs(text):
            result.mentions.append(_reanchor(mention))
        for dref in recognize_eu_directive_refs(
            text, source_statute_id=statute_id, local_aliases=local_eu_aliases
        ):
            result.mentions.append(_reanchor(dref.mention))
        # Internal (same-statute) and by-name cross-statute refs partition by the
        # context preceding the §: bare/self -> internal; name head -> by-name;
        # id-anchored is owned by the plain-text lane (both decline it).
        # Coordinated internal refs (``47 ja 49 §:ssä``) enumerate into one
        # mention per member, all sharing the whole-coordination surface; group
        # them so each coordinated occurrence shares one span (see below).
        # The internal mentions carry their enclosing-section label on the source
        # provenance (real AKN ancestry of THIS <p>), so the elliptical resolver
        # can fill a bare momentti/kohta against the right section without a
        # byte-offset remap.
        enclosing_src_ref = _src_ref_for(enclosing_labels.get(p_el, ""))
        result.mentions.extend(
            _reanchor_grouped(
                recognize_internal_refs(
                    text, statute_id, known_sections=trusted_sections
                ),
                enclosing_src_ref,
            )
        )
        # By-name cross-statute refs coordinate the SAME way internal refs do
        # (``tukilain 10 c §:n 1–3 momentissa ja 10 d §:n 1–3 momentissa``
        # enumerates one mention per member, all carrying the whole-coordination
        # surface). Re-anchoring per-member would advance the per-surface byte
        # cursor past the single document occurrence on member 1, starving
        # members 2+ of a span; without a span their use-offset is None and the
        # offset-gated defined-term / alias resolution in resolve.py cannot fire,
        # so they stay statute_only while member 1 resolves. Group them so every
        # coordinated member shares the one occurrence's span (see the INTERNAL
        # lane above). By-name refs carry no enclosing-section provenance (they
        # are not INTERNAL), so the group source ref is the plain whole-statute
        # ``src_ref`` and the grouped helper's bare-momentti fill is a no-op.
        result.mentions.extend(
            _reanchor_grouped(recognize_by_name_refs(text), src_ref)
        )

    return result


# ---------------------------------------------------------------------------
# Preparatory-reference lane (committee reports/opinions, parliament response,
# EU prep acts, OJ refs) — the rest of the legislative-preparation chain that
# sits alongside the HE proposal in the preliminaryWork ("Esityöt") footer.
# ---------------------------------------------------------------------------
#
# The HE government-proposal is ALREADY emitted by the <ref>-element lane
# (extract_reference_mentions → extract_cross_refs) as a ``he/YEAR/NUMBER``
# target, because Finlex marks HE backlinks with AKN <ref href=".../
# government-proposal/...">. The preparatory recognizer also recognises HE, but
# its docstring states HE is handled by the caller — so this lane EXCLUDES
# kind=HE to avoid double-counting. Every other preparatory kind (committee
# mietintö/lausunto, EV/EVK response, LA initiative, EU prep act, OJ ref) has
# no <ref> markup and is owned by no other lane.


def _prep_ref_to_mention(
    prep: PreparatoryReference,
    statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
    locate_span: Callable[[str], Optional[SourceSpan]],
) -> ReferenceMention:
    """Lift one (non-HE) PreparatoryReference to a ReferenceMention.

    Preparatory instruments are NOT statutes — they are committee reports,
    parliament responses, EU prep acts, etc. They are typed as
    ``NON_STATUTORY_INSTRUMENT`` with the preparatory ``canonical_id`` carried
    as the target's ``statute_id`` (an explicit, non-statute identity such as
    ``fi.committee.livm.28.2010`` / ``fi.ev.351.2010``). This keeps them out of
    the cross-statute dedup space (their ids never collide with
    ``NUMBER/YEAR`` statute ids) while still surfacing them as first-class
    reference mentions.

    The preparatory extractor leaves byte spans None (it normalises text before
    matching); this lane re-anchors each mention to a byte span in the raw
    ``xml_bytes`` by locating its ``raw_text`` surface, exactly like the
    plain-text / surface-grammar lanes (fail-loud by absence when not found).
    """
    # canonical_id is None only for UNRESOLVED kind; those are not emitted here.
    target_id = prep.canonical_id or ""
    tgt_ref = ProvisionRef(
        statute_id=target_id,
        provision_path="",
        section_label="",
    )
    src_ref = ProvisionRef(
        statute_id=statute_id,
        provision_path="",
        section_label="",
    )
    surface = prep.raw_text or ""
    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=tgt_ref,
        cite_kind=CiteKind.NON_STATUTORY_INSTRUMENT,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="preparatory",
        source_span=locate_span(surface),
        valid_at_interval=valid_at_interval,
        # edge_subtype carries the preparatory kind so consumers can split the
        # chain (committee_report / committee_opinion / parliament_response /
        # eu_regulation / oj_reference / ...). Disjoint from the CITES/REPEALS
        # edge_subtype vocabulary used by the statute lanes.
        edge_subtype=prep.kind.value,
        surface_text=surface,
    )


def extract_preparatory_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> ExtractionResult:
    """Extract preparatory-chain ReferenceMention records (committee, EV, …).

    Wraps :func:`extract_preparatory_refs` and lifts every NON-HE
    PreparatoryReference to a ReferenceMention. HE is excluded because the
    <ref>-element lane already emits it as a ``he/YEAR/NUMBER`` target; emitting
    it here too would double-count it.

    Mentions carry phrase_lemma="preparatory" and cite_kind=NON_STATUTORY_INSTRUMENT.
    """
    result = ExtractionResult()
    if not xml_bytes:
        return result
    try:
        prep_result = extract_preparatory_refs(
            xml_bytes,
            statute_id,
            valid_at_interval=valid_at_interval,
        )
    except ET.ParseError:
        # XML parse errors are already reported by extract_reference_mentions;
        # do not double-report.
        return result

    # Per-surface byte cursor over xml_bytes; surfaces appear in document order.
    surface_byte_cursor: dict[bytes, int] = {}

    def _locate(surface: str) -> Optional[SourceSpan]:
        if not surface:
            return None
        needle = surface.encode("utf-8")
        start = _find_with_left_boundary(
            xml_bytes, needle, surface_byte_cursor.get(needle, 0)
        )
        if start < 0:
            # The recognizer normalises whitespace before matching, so a raw_text
            # surface may not be a verbatim byte substring — fail loud by absence
            # (None span) rather than fabricate an offset.
            return None
        surface_byte_cursor[needle] = start + 1
        return SourceSpan(
            source_file=statute_id,
            byte_offset=start,
            byte_len=len(needle),
        )

    for prep in prep_result.refs:
        if prep.kind == PreparatoryReferenceKind.HE:
            # Owned by the <ref>-element lane (he/YEAR/NUMBER). Skip — no dupes.
            continue
        if prep.kind == PreparatoryReferenceKind.UNRESOLVED:
            # No canonical target; surfaced as a rejected candidate by the prep
            # extractor, not a typed mention.
            continue
        result.mentions.append(
            _prep_ref_to_mention(prep, statute_id, valid_at_interval, _locate)
        )

    return result


# ---------------------------------------------------------------------------
# Inline-(id) citation-construction lane (PRIMARY for the plain-text inline-(id)
# family) — strangle payoff over _PLAIN_TEXT_FI_STATUTE_RE.
# ---------------------------------------------------------------------------
#
# The construction parse (``legal_surface.sentence_parse.parse_citation_sentence``)
# keys purely on the ``(NUMBER/YEAR)`` anchor, so it recovers the "Finding-B" class
# the production regex MISSES — every inline-(id) cite whose statute-name head is
# separated from the paren by an intervening genitive/provision modifier
# (``annettu opetusministeriön asetus (253/2001)``, ``patenttilain 70 a §:n
# (593/94)``), and ``-kaari`` / ``Maakaaren`` heads (``perintökaaren (40/65)``,
# ``Maakaaren (540/1995)``). It provably SUBSUMES the regex over the inline-(id)
# family (0 lost on a large mixed-era corpus sweep), while its only over-emission
# class (parenthetical FRACTIONS, ``kymmenesosalla (1/10)``) is declined inside the
# construction parse itself (closed audited surface guard, never a magnitude rule).
#
# This lane is the PRIMARY producer for the inline-(id) family; the regex lane
# (``extract_plain_text_statute_mentions``) is demoted to a typed-residue FALLBACK
# that fires only for inline-(id) targets the construction did NOT cover — and any
# such residue mention is marked ``phrase_lemma="plain_text_fallback"`` so it is
# auditable (fail-loud, no silent merge). Orientation: the cited statute is keyed
# YEAR/NUMBER via ``cross_refs._make_statute_id`` — the SAME canonical corpus key
# the ``<ref>`` lane and the demoted regex lane mint — so a construction-derived
# cite dedups onto the SAME entity node, never a re-inverted NUMBER/YEAR node.


# The inline-(id) citation-construction surface/dedup helpers live in the shared
# orchestrator (ONE lifter for the lens lane AND the forest projection).


def extract_inline_id_construction_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    ref_covered_statute_ids: Optional[set[str]] = None,
) -> Tuple[ExtractionResult, set[str]]:
    """Extract inline-(id) plain-text citations via the construction parse (PRIMARY).

    Walks the AKN body ``<p>`` elements, collecting text NOT inside ``<ref>``
    children (the same non-ref text the demoted regex lane scans), runs the
    citation-sentence construction parse over each, and lifts every recognized
    inline-(id) citation construction to a CROSS_STATUTE ``ReferenceMention``
    keyed YEAR/NUMBER (``_make_statute_id``) — identical orientation/typing to the
    demoted plain-text lane, plus the Finding-B class.

    Returns ``(result, covered_keys)`` where ``covered_keys`` is the set of
    statute+provision keys (see :func:`_construction_provision_key`) this lane
    emitted, so the caller can filter the regex fallback to genuine residue only.
    Mentions carry ``phrase_lemma="citation_construction"``.

    Per AGENTS.md §1.13: the construction parse is the named single-pass recognizer
    for this grammar family. Per §1.8: nothing disappears silently — the regex lane
    still runs as an audited fallback in the caller.
    """
    result = ExtractionResult()
    covered_keys: set[str] = set()
    if not xml_bytes:
        return result, covered_keys

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return result, covered_keys

    covered: set[str] = ref_covered_statute_ids or set()

    # Byte-offset locator into ``xml_bytes`` for a cite's name-inclusive surface
    # (the lens coordinate system). A left-to-right byte cursor maps repeated
    # identical surfaces to successive byte positions; the per-<p> span cache (held
    # by the shared lifter) keeps several targets from one anchor on one span.
    surface_byte_cursor: dict[bytes, int] = {}

    def _locate_surface_span(surface: str) -> Optional[SourceSpan]:
        if not surface:
            return None
        needle = surface.encode("utf-8")
        start = _find_with_left_boundary(
            xml_bytes, needle, surface_byte_cursor.get(needle, 0)
        )
        if start < 0:
            return None
        surface_byte_cursor[needle] = start + 1
        return SourceSpan(
            source_file=statute_id,
            byte_offset=start,
            byte_len=len(needle),
        )

    for p_el in root.iter():
        local = p_el.tag.split("}")[-1] if "}" in p_el.tag else p_el.tag
        if local != "p":
            continue
        text = _PLAIN_TEXT_RECOGNIZER._collect_non_ref_text(p_el)
        if not text or _PLAIN_TEXT_GUARD_PAREN not in text:
            continue
        # The canonical inline-(id) citation lift, shared with the forest's
        # reference projection (ONE lifter — no rival parser). The CITING statute id
        # bounds the 2-digit-year century pivot causally and skips self-cites; the
        # byte-offset locator anchors each surface in ``xml_bytes`` (the lens
        # coordinate); targets already owned by the <ref> lane (``covered``) are
        # dropped so the occurrence is single-sourced.
        p_mentions, p_keys = lift_inline_id_construction_mentions(
            text,
            statute_id,
            valid_at_interval=valid_at_interval,
            covered_statute_ids=covered,
            span_for_surface=_locate_surface_span,
        )
        result.mentions.extend(p_mentions)
        covered_keys.update(p_keys)

    return result, covered_keys


def _authority_basis_text(root: ET.Element) -> str:
    """Decoded text of the spans that carry a ``… nojalla`` authority basis.

    The asetus authority basis lives in the decree PREAMBLE enacting clause; some
    older statutes carry it in the leading body text instead. Prefer the preamble;
    fall back to the whole-document text head when no preamble element exists. The
    text is whitespace-normalized so the construction's clause scan sees a clean
    surface (the same normalization the production extractor applies).
    """
    pre = root.find(f".//{{{_AKN_NS}}}preamble")
    raw = ET.tostring(
        pre if pre is not None else root, encoding="unicode", method="text"
    )
    norm = re.sub(r"\s+", " ", raw).strip()
    if pre is not None:
        return norm
    # No preamble element: scan only the head (the enacting clause region) to avoid
    # body sentences that are forward grants, not authority bases.
    return norm[:600]


def extract_delegation_construction_authority_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> Tuple[ExtractionResult, set[str]]:
    """Lift ``… nojalla`` authority bases via the construction parse (PRIMARY).

    Recognizes the asetus authority-basis construction (``[act-name] (NUM/YEAR) N
    §:n nojalla``) in the decree preamble using the construction recognizer
    (:func:`lawvm.finland.legal_surface.delegation_parse.extract_authority_bases`),
    and lifts each recognized basis to an ISSUED_UNDER ``ReferenceMention`` keyed
    canonically YEAR/NUMBER (routed through ``_make_statute_id`` after 2-digit-year
    normalization — NO inverted ids). The cite_kind is taken from the per-basis
    drafting-kind inflection (``…lain`` → act → CROSS_STATUTE; a genuine
    ``…asetuksen`` / ``…päätöksen`` basis stays a NON_STATUTORY_INSTRUMENT), exactly
    as the metadata/regex lift types it.

    This is the construction-PRIMARY recall source for the authority-basis family;
    the production ``extract_asetus_authority`` regex (already consumed by
    ``extract_cross_refs`` → the metadata ISSUED_UNDER edges) is demoted to the
    typed-residue FALLBACK, surfaced ONLY for bases the construction did not cover
    (the caller dedups by parent id). Mentions carry a distinct
    ``phrase_lemma="delegation_construction"`` so the construction-recall frontier
    is auditable.

    Returns ``(result, covered_parent_ids)`` — the canonical YEAR/NUMBER parent ids
    the construction covered, so the caller can mark the regex/metadata residue.
    """
    result = ExtractionResult()
    covered: set[str] = set()
    if not xml_bytes:
        return result, covered
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return result, covered

    basis_text = _authority_basis_text(root)
    if not basis_text:
        return result, covered

    src_ref = ProvisionRef(statute_id=statute_id, provision_path="", section_label="")
    for basis in extract_authority_bases(basis_text):
        parent_id = _make_statute_id(_normalize_year(basis.year), basis.num)
        # ISSUED_UNDER cite_kind from the basis drafting kind (act vs instrument),
        # mirroring _edge_to_cite_kind: only an "act" basis is a statute
        # cross-reference; a decree/decision basis stays a non-statutory instrument.
        kind = _classify_authority_kind(basis.name_word)
        cite_kind = (
            CiteKind.CROSS_STATUTE
            if kind in _AUTHORITY_STATUTE_KINDS
            else CiteKind.NON_STATUTORY_INSTRUMENT
        )
        # One mention per recognized section (or one sectionless mention when the
        # basis carries an id but no overt §). Same target orientation as the lift.
        section_labels = basis.section_labels or ("",)
        for sec in section_labels:
            tgt_ref = _parse_provision_ref_from_path(parent_id, sec)
            result.mentions.append(
                ReferenceMention(
                    source_provision_ref=src_ref,
                    target_provision_ref=tgt_ref,
                    cite_kind=cite_kind,
                    cite_confidence=CiteConfidence.EXACT,
                    phrase_lemma="delegation_construction",
                    source_span=None,
                    valid_at_interval=valid_at_interval,
                    edge_subtype="ISSUED_UNDER",
                )
            )
        covered.add(parent_id)
    return result, covered


def extract_all_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
    ignore_annotations: Optional[bool] = None,
) -> ExtractionResult:
    """Extract all ReferenceMention records (domestic + EU + plain-text) from a statute.

    Combines:
      - ``extract_reference_mentions``: AKN <ref> element mentions + metadata edges.
      - ``extract_eu_reference_mentions``: EU citations from text scan.
      - ``extract_plain_text_statute_mentions``: plain-text statute citations
        NOT covered by <ref> markup (phrase_lemma="plain_text").

    EU and plain-text mentions are appended after domestic mentions.

    This is the primary entry point for the ``fi_refs.parquet`` projection.

    Annotation-independence measurement (grammar7 §13-C/E):
        When ``ignore_annotations`` is True (or, when left None, when
        ``LAWVM_IGNORE_SEMANTIC_ANNOTATIONS`` is set truthy), the AKN
        ``<ref>``-element semantic-annotation lane (``extract_reference_mentions``)
        is SKIPPED and the ``<ref>``-derived ``ref_covered_statute_ids`` dedup
        guard is dropped, so ONLY the text-derived lanes run and the plain-text
        lane is no longer suppressed by now-ignored annotations. Default
        (None → env, fail-closed OFF) is byte-identical to current behaviour.
        This is a MEASUREMENT mode, not a new default.
    """
    if ignore_annotations is None:
        ignore_annotations = ignore_semantic_annotations()

    if ignore_annotations:
        # Suppress the <ref>-element semantic-annotation lane entirely: no
        # domestic <ref>/metadata mentions, and — crucially — an EMPTY
        # ref_covered set so the plain-text lane's would-be-<ref>-covered hits
        # are NOT suppressed (else the measurement is contaminated by the very
        # annotations we are ignoring).
        domestic = ExtractionResult()
        ref_covered: set[str] = set()
    else:
        domestic = extract_reference_mentions(
            xml_bytes,
            statute_id,
            valid_at_interval=valid_at_interval,
            strict=strict,
        )
        # Build the set of statute IDs already covered by <ref>-element
        # extraction to pass as the dedup guard to the plain-text pass.
        ref_covered = {
            m.target_provision_ref.statute_id
            for m in domestic.mentions
            if m.target_provision_ref is not None and m.edge_subtype == "CITES"
        }

    # Johtolause amendment-target lane: the act this statute amends, named by an
    # AKN <affectedDocument> in the preamble enacting clause — OUTSIDE <body>, so
    # invisible to the inline-<ref> body lane above. This is the single most
    # important cross-statute link in an amending statute; pure-amendment statutes
    # (whose whole substance is the johtolause) otherwise surface zero references.
    # Deduplicated against the body <ref> CITES targets (``ref_covered``) so a
    # target named both by an <affectedDocument> AND a body <ref> element yields
    # ONE amendment-target surface — the body <ref> CITES already owns that
    # occurrence, so the johtolause surface is suppressed there. The plain-text /
    # by-name body lanes are intentionally NOT touched: a body prose cite to the
    # amended act is a genuine, distinct surface occurrence and stays a separate
    # reference_expr (the surface graph collapses both to one legal_work_entity by
    # work_id, so this is one entity, two surface nodes — no double-emission of the
    # same occurrence). Skipped entirely in annotation-independence measurement
    # mode (ref_covered empty), like the rest of the <ref>-annotation lane.
    affected = ExtractionResult()
    if not ignore_annotations:
        affected_raw = extract_affected_document_mentions(
            xml_bytes,
            statute_id,
            valid_at_interval=valid_at_interval,
        )
        affected.diagnostics = affected_raw.diagnostics
        for m in affected_raw.mentions:
            tgt = m.target_provision_ref
            if tgt is not None and tgt.statute_id in ref_covered:
                # Already an inline-<ref> CITES occurrence for this act — keep the
                # single body <ref> surface, drop the duplicate johtolause one.
                continue
            affected.mentions.append(m)

    eu = extract_eu_reference_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
    )

    # Inline-(id) plain-text citation family.
    #
    # SOLE PRODUCTION SOURCE: the citation-construction parse (keys on the
    # ``(NUMBER/YEAR)`` anchor). It subsumes the brittle ``_PLAIN_TEXT_FI_STATUTE_RE``
    # and recovers the Finding-B class (statute-name head separated from its paren by
    # an intervening modifier; ``-kaari`` heads). It bounds the 2-digit-year century
    # pivot CAUSALLY by the citing statute (a 1993 act citing ``(71/23)`` resolves to
    # ``1923/71``), which the regex lane could not — the regex pivoted by
    # ``date.today()`` (acausal), minting future-dated ids.
    #
    # The legacy regex lane (:func:`extract_plain_text_statute_mentions`) was
    # previously retained here as a typed-residue FALLBACK
    # (``phrase_lemma="plain_text_fallback"``) for inline-(id) targets the
    # construction did not cover. A whole-corpus census (guarded by
    # ``tests/test_fi_ref_legacy_regex_residue_census.py``) found the residue
    # contributed ZERO citations the construction lane misses correctly: every
    # firing corpus-wide was a 2-digit-year citation the construction ALREADY caught
    # with the correct (causal) century, duplicated by the regex with the WRONG
    # century — a different statute id, hence undeduped. So the residue net was not a
    # safety net but a source of mis-pivoted false edges; it is DELETED. The
    # production guard test pins that ``extract_all_reference_mentions`` never emits
    # ``plain_text_fallback`` again (a future construction regression surfaces as a
    # test failure, not silent loss).
    #
    # Measurement mode (``ignore_annotations``) is exempt: it still runs the plain-text
    # recognizer with ``include_ref_text`` folded in to measure annotation
    # independence (the construction lane scans only non-ref text). That path is an
    # AUDIT/measurement lane with NO production reference authority — it never feeds
    # the ``plain_text_fallback`` production surface (deleted above).
    plain = ExtractionResult()
    if ignore_annotations:
        plain = extract_plain_text_statute_mentions(
            xml_bytes,
            statute_id,
            valid_at_interval=valid_at_interval,
            ref_covered_statute_ids=ref_covered,
            # Measurement mode also reads <ref> INNER text (production hides it in
            # the markup), so the text lane gets a real shot at the hidden cite.
            include_ref_text=True,
        )
    else:
        construction, _construction_keys = extract_inline_id_construction_mentions(
            xml_bytes,
            statute_id,
            valid_at_interval=valid_at_interval,
            ref_covered_statute_ids=ref_covered,
        )
        plain.mentions.extend(construction.mentions)
        plain.diagnostics.extend(construction.diagnostics)

    # Surface-grammar lane: treaty (SopS), vague-OPEN, EU-by-nickname directive
    # articles. Families disjoint from the lanes above (no statute-id dedup
    # needed); each mention is re-anchored to a byte span in xml_bytes.
    surface = extract_surface_grammar_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
    )

    # Preparatory-chain lane: the rest of the legislative-preparation footer
    # (committee mietintö/lausunto, EV/EVK response, LA, EU prep act, OJ) that
    # accompanies the HE proposal. HE itself is excluded inside this lane (it is
    # already emitted by the <ref> lane as he/YEAR/NUMBER), so no double-count.
    preparatory = extract_preparatory_reference_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
    )

    # Delegation-authority (``… nojalla``) lane — construction PRIMARY.
    #
    # The authority-basis family is OWNED by the construction parse
    # (:func:`extract_delegation_construction_authority_mentions`): it recognizes
    # the well-formed ``[act-name] (NUM/YEAR) N §:n nojalla`` basis in the preamble
    # and lifts each to an ISSUED_UNDER mention (canonical YEAR/NUMBER orientation,
    # per-basis drafting-kind typing, references-recognized section path). The
    # construction is the SOLE source for every basis it covers.
    #
    # The production ``extract_asetus_authority`` regex — consumed upstream by
    # ``extract_cross_refs`` → ``_merge_authority_basis`` into the ``domestic``
    # ISSUED_UNDER mentions (metadata edges enriched / nojalla-only edges appended)
    # — is DEMOTED to the typed-residue FALLBACK at the mention surface: for a
    # parent the construction COVERS, the construction's (richer, sectioned) mention
    # supersedes the regex-derived ``domestic`` ISSUED_UNDER mention, which is
    # dropped here so the covered basis is single-sourced (no double-emission and no
    # sectionless-regex shadow of a sectioned construction basis). The regex-derived
    # ``domestic`` ISSUED_UNDER mention is KEPT only for the construction-DECLINED
    # residue — the genuine shapes the construction refuses (fail-loud on noise).
    # The construction now ALSO owns the ``… N §:n, sellaisena kuin se on laissa
    # NNN/YYYY, nojalla`` amendment-history interjection: it blanks the interjection
    # (amendment-version metadata about WHICH act amended the basis provision — the
    # inner ``(NNN/YYYY)`` is the AMENDING act, never the basis) and binds the OUTER
    # ``[act] (NUM/YEAR) N §:n … nojalla``. So the interjection is no longer residue.
    # What genuinely REMAINS for the regex fallback is: voimaantulo-/siirtymäsäännös
    # bases (a ``voimaantulosäännöksen nojalla`` with no ``§`` provision path),
    # momentti-only/budget-momentti bases (``… momentin 28.37.40 nojalla``), prose
    # provision paths (``10 §:n nimikkeen … kohdalla olevan säännöksen nojalla``),
    # and OCR/abbreviation noise (``§;n`` / ``§.n``, ``mom.`` / ``mom:n``, ``7§:n``,
    # ``sekä. 33 §:n``, ``#:n``, glued ``nojallapäättänyt``). Empirically (full
    # Finlex corpus) the construction owns the overwhelming majority of bases with
    # their sections; the sellaisena interjection class (~707 parents / ~476
    # statutes, formerly the dominant regex residue) is now construction-owned with
    # NO loss and NO amending-act mis-binding, leaving a smaller genuine-residue
    # tail. NOTHING the regex shipped at the PARENT level is lost (§1.8); the
    # demotion only prefers the construction's typing/sections where both cover the
    # same parent. The cross_refs ``_merge_authority_basis``
    # enrichment of the StatuteGraph is untouched — this demotion is purely at the
    # ``extract_all_reference_mentions`` surface. Skipped in annotation-independence
    # measurement mode (the ISSUED_UNDER metadata lane is part of the suppressed
    # ``<ref>``/metadata lane).
    authority = ExtractionResult()
    domestic_mentions: List[ReferenceMention] = domestic.mentions
    if not ignore_annotations:
        constr_authority, construction_covered = (
            extract_delegation_construction_authority_mentions(
                xml_bytes,
                statute_id,
                valid_at_interval=valid_at_interval,
            )
        )
        # Construction PRIMARY: emit every construction basis.
        authority.mentions.extend(constr_authority.mentions)
        # Regex residue ONLY: drop the regex/metadata-derived ``domestic``
        # ISSUED_UNDER mention for any parent the construction already covers
        # (single-source the covered basis); keep it for construction-declined
        # residue parents and keep ALL non-ISSUED_UNDER domestic mentions intact.
        if construction_covered:
            domestic_mentions = [
                m
                for m in domestic.mentions
                if not (
                    m.edge_subtype == "ISSUED_UNDER"
                    and m.target_provision_ref is not None
                    and m.target_provision_ref.statute_id in construction_covered
                )
            ]

    combined = ExtractionResult()
    combined.mentions = (
        domestic_mentions + affected.mentions + eu.mentions + plain.mentions
        + surface.mentions + preparatory.mentions + authority.mentions
    )
    combined.rejected = (
        domestic.rejected + eu.rejected + plain.rejected + surface.rejected + preparatory.rejected
    )
    combined.broken_findings = (
        domestic.broken_findings + eu.broken_findings + plain.broken_findings
        + surface.broken_findings + preparatory.broken_findings
    )
    combined.ambiguous_findings = (
        domestic.ambiguous_findings + eu.ambiguous_findings + plain.ambiguous_findings
        + surface.ambiguous_findings + preparatory.ambiguous_findings
    )
    combined.approximate_findings = (
        domestic.approximate_findings + eu.approximate_findings + plain.approximate_findings
        + surface.approximate_findings + preparatory.approximate_findings
    )
    combined.diagnostics = (
        domestic.diagnostics + affected.diagnostics + eu.diagnostics + plain.diagnostics
        + surface.diagnostics + preparatory.diagnostics
    )
    return combined
