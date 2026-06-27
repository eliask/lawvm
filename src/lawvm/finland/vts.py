"""Voimaantulosäännös (transitional-provision) repeal extraction.

These functions extract REPEAL AmendmentOps from the transitional-provision
section of an amendment statute.  They depend on lxml (read-only) and
AmendmentOp, but not on XMLStatute or any replay state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from functools import lru_cache
from typing import List, Literal, Optional, Set, TYPE_CHECKING

import lxml.etree as etree

from lawvm.core.filter_result import FilterResult
from lawvm.core.stage_result import PartitionResult, Residual
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.address_parse import ParsedLegalAddress
from lawvm.finland.target_selector_facades import (
    fi_chapter_target,
    fi_section_target,
)
from lawvm.finland.references.freetext_addresses import scan_legal_addresses
from lawvm.finland.citation_routing import _head_genitive_title
from lawvm.core.quirks_disposition import QuirksDisposition

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile

VtsSkippedTargetReason = Literal[
    "unsupported_special_target",
    "unsupported_subitem_target",
    "standalone_target_without_section",
    "unsafe_kohta_only_bare_section_parse",
]
VtsSourceDiagnosticReason = Literal[
    "invalid_parent_id",
    "xml_syntax_error",
    "no_candidate_containers",
    "paragraphized_repeal_fragment_unparsed",
]

VTS_SKIPPED_TARGET_RULE_ID = "FI.COMMENCEMENT_PROVISION_SKIPPED_TARGET_UNSUPPORTED"
VTS_SOURCE_DIAGNOSTIC_RULE_ID = "FI.COMMENCEMENT_PROVISION_SOURCE_UNREADABLE_OR_EMPTY"
VTS_PARAGRAPHIZED_FRAGMENT_UNPARSED_RULE_ID = "FI.COMMENCEMENT_PROVISION_PARAGRAPHIZED_REPEAL_FRAGMENT_UNPARSED"

# Parse-witness provenance (diagnostic only — zero replay semantics) carried by every
# repeal op minted from a voimaantulosäännös fragment, so the spec-discovery ledger can
# attribute the divergence back to this transitional-provision repeal-extraction lane
# instead of dropping it into the unattributed blind-spot bucket.
FI_VTS_VOIMAANTULO_REPEAL_RULE_ID = "fi.repeal_vts_voimaantulo"


def _source_may_contain_vts_repeal(xml_bytes: bytes) -> bool:
    """Return True when raw XML contains a VTS repeal trigger family."""
    lowered = xml_bytes.lower()
    return b"kumotaan" in lowered or (b"ottamatta" in lowered and b"voimaan" in lowered)


@dataclass(frozen=True)
class VtsSkippedTarget:
    """Typed visibility record for a VTS target intentionally not lowered.

    VTS extraction must not silently widen unsupported child/facet targets into
    whole-section repeals. This record preserves the parsed target and source
    reason whenever the extractor skips one of those targets.
    """

    rule_id: str
    reason_code: VtsSkippedTargetReason
    source_reason: str
    source_statute: str
    source_excerpt: str
    target_section: str
    target_chapter: str | None = None
    target_paragraph: int | None = None
    target_item: str | None = None
    target_subitem: str | None = None
    target_special: str = ""
    phase: str = "frontend_extraction"
    family: str = "unsupported_target"
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "source_reason": self.source_reason,
            "source_statute": self.source_statute,
            "source_excerpt": self.source_excerpt,
            "target_section": self.target_section,
            "target_chapter": self.target_chapter,
            "target_paragraph": self.target_paragraph,
            "target_item": self.target_item,
            "target_subitem": self.target_subitem,
            "target_special": self.target_special,
            "phase": self.phase,
            "family": self.family,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


@dataclass(frozen=True)
class VtsSourceDiagnostic:
    """Typed visibility record for source shapes that prevent VTS inspection."""

    rule_id: str
    reason_code: VtsSourceDiagnosticReason
    source_reason: str
    source_statute: str
    source_excerpt: str
    phase: str = "frontend_extraction"
    family: str = "source_pathology"
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "source_reason": self.source_reason,
            "source_statute": self.source_statute,
            "source_excerpt": self.source_excerpt,
            "phase": self.phase,
            "family": self.family,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


@dataclass(frozen=True)
class VtsRepealPartition(PartitionResult[AmendmentOp]):
    """Conserving carrier for voimaantulosäännös repeal extraction (Audit C).

    Composes the canonical :class:`PartitionResult` (accepted = minted REPEAL
    ``AmendmentOp`` records + typed core ``residuals``) and ADDS the two rich
    domain channels the production replay ledger consumes:

      * ``skipped_targets`` — :class:`VtsSkippedTarget` records for parsed targets
        the extractor refused to widen into a whole-section repeal (the rejected
        lane).
      * ``source_diagnostics`` — :class:`VtsSourceDiagnostic` records for source
        shapes that prevented inspection (a source-pathology residual lane).

    These are not placed in the wrapped ``FilterResult`` rejected lane because
    their payload is a parsed target / source shape, not an ``AmendmentOp``. The
    core ``residuals`` mirror them so the total-accounting contract holds; the two
    typed fields are the channels the legacy out-params drain from.
    """

    skipped_targets: tuple[VtsSkippedTarget, ...] = ()
    source_diagnostics: tuple[VtsSourceDiagnostic, ...] = ()


# op-keyword set — used to gate VTS fallback (must contain operative verbs
# so we don't collide with real johtolause ops)
_VTS_OP_KEYWORDS = {
    "muutetaan",
    "muutettu",
    "muuttaa",
    "muuttanut",
    "kumotaan",
    "kumottu",
    "kumoaa",
    "kumonnut",
    "lisätään",
    "lisätty",
    "lisännyt",
    "siirretään",
    "siirretty",
    "siirtää",
    "siirtänyt",
}


def _parent_title_variants(parent_title: str) -> List[str]:
    """Return normalized title forms that may appear in repeal prose.

    Finnish legal prose may refer to a statute either by its canonical title
    (e.g. "laki sosiaalihuollon asiakkaan asemasta ja oikeuksista") or by its
    genitive/partitive form used in cross-statute repeal clauses
    (e.g. "sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain").
    When the title starts with "laki " the genitive form drops the leading
    "laki " and appends " annetun lain".
    """
    norm = re.sub(r"\s+", " ", (parent_title or "").strip().lower())
    norm = norm.rstrip(" .:;")
    if not norm:
        return []
    base_titles = [norm]
    title_without_citation = re.sub(r"\s*\(\s*\d+\s*/\s*\d{2,4}\s*\)\s*$", "", norm).strip()
    if title_without_citation and title_without_citation != norm:
        base_titles.append(title_without_citation)
    variants = []
    # Genitive form via real M1 head inflection (split off the closed-class
    # head, inflect via the morphology engine, re-attach the invariant
    # modifier), with a legacy string-slice fallback when M1 declines to
    # inflect a head so coverage never regresses below the old behavior.
    for title in base_titles:
        variants.append(title)
        genitive = _head_genitive_title(title)
        if genitive is not None:
            variants.append(genitive)
        # Titles that start with "laki " also appear in cross-statute prose as
        # "<rest> annetun lain" (genitive form without the leading "laki").
        if title.startswith("laki "):
            variants.append(title[5:] + " annetun lain")
    return list(dict.fromkeys(v for v in variants if v))


@lru_cache(maxsize=1024)
def _vts_parent_citation_re(parent_id: str) -> "re.Pattern[str] | None":
    if not parent_id:
        return None
    try:
        parent_year, parent_num_str = parent_id.split("/")
    except (ValueError, AttributeError):
        return None
    parent_num_head = parent_num_str.split("-", 1)[0]
    try:
        parent_num = int(parent_num_head)
    except ValueError:
        return None
    if not parent_year:
        return None
    parent_year_short = parent_year[-2:]
    return re.compile(
        r"\(\s*"
        + re.escape(str(parent_num))
        + r"\s*/\s*(?:"
        + re.escape(parent_year)
        + r"|"
        + re.escape(parent_year_short)
        + r")\s*\)",
        re.IGNORECASE,
    )


def _find_parent_title_span(text: str, title_variants: List[str]) -> tuple[int, int]:
    """Return the earliest matching bare-title span in *text*.

    Supports both exact normalized title mentions and dated enactment phrases
    like ``avioliittolain voimaanpanosta 13 päivänä kesäkuuta 1929 annetun
    lain``.
    """
    norm_text = re.sub(r"\s+", " ", text).strip()
    lower = norm_text.lower()
    best_start = -1
    best_end = -1

    for variant in title_variants:
        idx = lower.find(variant)
        if idx >= 0 and (best_start == -1 or idx < best_start):
            best_start = idx
            best_end = idx + len(variant)

        if variant.endswith(" annetun lain"):
            stem = variant[: -len(" annetun lain")]
            match = re.search(
                re.escape(stem)
                + r"(?:\s+\d{1,2}\s+päivänä\s+[a-zäöå]+(?:\s*\d{2,4})?)?\s+annetun\s+lain",
                lower,
                re.IGNORECASE,
            )
            if match and (best_start == -1 or match.start() < best_start):
                best_start = match.start()
                best_end = match.end()

    return best_start, best_end


def _vts_candidate_containers(tree: etree._Element) -> List[etree._Element]:
    """Return trailing XML containers that may carry VTS repeal prose.

    Most amendments encode voimaantulo repeal clauses in a trailing section or
    ``entryIntoForce`` hcontainer, but older Finlex XML also places the clause
    directly under ``<conclusions>`` as plain prose. Keep these containers on
    the same owned extraction rail instead of requiring statute-local lore.
    """
    return (
        tree.findall(".//{*}section")
        + tree.findall('.//{*}hcontainer[@eId="entryIntoForce"]')
        + tree.findall('.//{*}hcontainer[@name="conclusions"]')
        + tree.findall(".//{*}conclusions")
    )


def _record_vts_skipped_target(
    skipped_targets_out: Optional[List[VtsSkippedTarget]],
    *,
    reason_code: VtsSkippedTargetReason,
    source_reason: str,
    source_statute: str,
    source_excerpt: str,
    addr: ParsedLegalAddress,
) -> None:
    if skipped_targets_out is None:
        return
    skipped_targets_out.append(
        VtsSkippedTarget(
            rule_id=VTS_SKIPPED_TARGET_RULE_ID,
            reason_code=reason_code,
            source_reason=source_reason,
            source_statute=source_statute,
            source_excerpt=re.sub(r"\s+", " ", source_excerpt).strip()[:240],
            target_section=addr.section,
            target_chapter=addr.chapter,
            target_paragraph=addr.subsection,
            target_item=addr.item,
            target_subitem=addr.subitem,
            target_special=addr.special,
        )
    )


def _vts_source_excerpt(xml_bytes: bytes) -> str:
    return re.sub(r"\s+", " ", xml_bytes.decode("utf-8", errors="replace")).strip()[:160]


def _classify_vts_source_diagnostic(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str = "",
) -> VtsSourceDiagnostic | None:
    try:
        parent_year, parent_num_str = parent_id.split("/")
        int(parent_num_str.split("-", 1)[0])
    except (ValueError, AttributeError):
        return VtsSourceDiagnostic(
            rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
            reason_code="invalid_parent_id",
            source_reason="VTS extraction could not build a parent citation from the parent id",
            source_statute=parent_id,
            source_excerpt=_vts_source_excerpt(xml_bytes),
        )
    if not parent_year:
        return VtsSourceDiagnostic(
            rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
            reason_code="invalid_parent_id",
            source_reason="VTS extraction parent id had an empty year component",
            source_statute=parent_id,
            source_excerpt=_vts_source_excerpt(xml_bytes),
        )
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        return VtsSourceDiagnostic(
            rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
            reason_code="xml_syntax_error",
            source_reason=f"VTS source XML could not be parsed: {exc.__class__.__name__}",
            source_statute=parent_id,
            source_excerpt=_vts_source_excerpt(xml_bytes),
        )
    candidate_containers = _vts_candidate_containers(tree)
    if not candidate_containers:
        return VtsSourceDiagnostic(
            rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
            reason_code="no_candidate_containers",
            source_reason="VTS source XML contained no trailing section, entryIntoForce, or conclusion containers",
            source_statute=parent_id,
            source_excerpt=_vts_source_excerpt(xml_bytes),
        )
    if b"kumotaan" not in xml_bytes.lower():
        return None
    citation_re = _vts_parent_citation_re(parent_id)
    title_variants = _parent_title_variants(parent_title)
    for container in reversed(candidate_containers):
        paragraphs = container.findall(".//{*}paragraph")
        if not paragraphs:
            continue
        full_text = etree.tostring(container, method="text", encoding="unicode")
        if "kumotaan" not in full_text.lower():
            continue
        has_citation = bool(citation_re.search(full_text)) if citation_re is not None else False
        title_start, _title_end = _find_parent_title_span(full_text, title_variants)
        if not has_citation and title_start < 0:
            continue
        return VtsSourceDiagnostic(
            rule_id=VTS_PARAGRAPHIZED_FRAGMENT_UNPARSED_RULE_ID,
            reason_code="paragraphized_repeal_fragment_unparsed",
            source_reason=(
                "VTS paragraphized repeal-like source mentioned the parent statute but no "
                "single paragraph yielded a lowerable repeal fragment; whole-container fallback was suppressed."
            ),
            source_statute=parent_id,
            source_excerpt=re.sub(r"\s+", " ", full_text).strip()[:240],
        )
    return None


# ---------------------------------------------------------------------------
# Fragment extraction helpers
# ---------------------------------------------------------------------------


# Enacting verb of a transitional repeal clause. Genuine clauses read
# "Tällä lailla kumotaan …" / "Sillä kumotaan …" — the verb governs the parent
# reference that follows it. The verb "kumotaan" can also appear as an ordinary
# subordinate-clause verb ("… sovelletaan myös, jos äitiys kumotaan …"), where it
# is NOT a repeal enactment and the parent reference is the object of another verb
# ("sovelletaan"). Distinguishing the two requires checking that an enacting
# "kumotaan" precedes the parent reference within the same sentence.
_VTS_REPEAL_ENACT_RE = re.compile(r"\bkumotaan\b", re.IGNORECASE)
_CHAPTER_MARKER_RE = re.compile(r'(\d{1,4}\s{1,4}[a-z]|\d{1,4})\s{1,8}luvun\b', re.IGNORECASE)


def _has_repeal_enactment_before(text: str, ref_start: int) -> bool:
    """Return True if an enacting ``kumotaan`` governs the parent reference.

    A genuine voimaantulo repeal reads ``… kumotaan … <parent ref> …``: the
    enacting verb precedes the parent citation/title within the same sentence.
    The false-positive shape is ``<parent ref> … sovelletaan …, jos … kumotaan
    …`` (an application/conditional clause) where the parent reference comes
    first and ``kumotaan`` only appears later as a subordinate-clause verb.

    *ref_start* is the offset of the parent reference (citation or title) inside
    *text*. We accept the span only if some ``kumotaan`` occurs before
    *ref_start* with no sentence boundary (``.``) separating it from the
    reference.
    """
    if ref_start <= 0:
        return False
    before = text[:ref_start]
    last = None
    for m in _VTS_REPEAL_ENACT_RE.finditer(before):
        last = m
    if last is None:
        return False
    # No sentence boundary between the enacting verb and the parent reference.
    between = before[last.end() : ref_start]
    return "." not in between


def _voimaantulo_repeal_fragment_for_parent(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str = "",
) -> str:
    """Return the repeal clause text fragment that targets *parent_id*.

    Searches the last few sections of the amendment XML for a
    ``Tällä lailla kumotaan`` clause.  Returns the plain-text fragment
    starting right after either:
    - the citation ``(NUM/YY[YY])`` that matches *parent_id*, or
    - a bare parent-title mention that matches *parent_title*
    up to the first natural break (``sellaisena kuin``,
    semicolon, or end of text).  Returns empty string if nothing found.

    Two XML shapes are handled:

    1. Numbered list — ``<intro>Tällä lailla kumotaan:</intro>`` followed by
       ``<paragraph>`` items.  The citation is inside one of the items.
    2. Inline prose — ``Tällä lailla kumotaan X (NUM/YY) provisions …``
       as a single block of text.
    """
    if not parent_id:
        return ""
    # Build a regex that matches this statute's citation: (925/79) or (925/1979)
    citation_re = _vts_parent_citation_re(parent_id)
    if citation_re is None:
        return ""

    title_variants = _parent_title_variants(parent_title)

    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return ""

    # Collect trailing sections, entryIntoForce hcontainers, and conclusions.
    # Older source XML can place the repeal clause directly in <conclusions>
    # rather than under a numbered voimaantulo section.
    all_sections = _vts_candidate_containers(tree)
    if not all_sections:
        return ""
    candidate_sections = list(reversed(all_sections))

    for sec in candidate_sections:
        full_text = etree.tostring(sec, method="text", encoding="unicode")
        full_lower = full_text.lower()
        if "kumotaan" not in full_lower:
            continue
        has_citation = bool(citation_re.search(full_text))
        title_start, _title_end = _find_parent_title_span(full_text, title_variants)
        has_title = title_start >= 0
        if not has_citation and not has_title:
            continue

        # Shape 1: numbered-item list — look for <paragraph> children whose text
        # contains the citation.
        paragraphs = sec.findall(".//{*}paragraph")
        if paragraphs:
            # When the section has a subsection/intro saying "kumotaan", every
            # paragraph under that intro is a repeal list item — even if the
            # word "kumotaan" is absent from the individual paragraph text.
            kumotaan_intro_subsections: set[etree._Element] = {
                parent
                for intro in sec.findall(".//{*}intro")
                if "kumotaan" in (etree.tostring(intro, method="text", encoding="unicode") or "").lower()
                for parent in (intro.getparent(),)
                if parent is not None
            }
            for para in paragraphs:
                para_text = etree.tostring(para, method="text", encoding="unicode")
                para_plain = re.sub(r"\s+", " ", para_text).strip()
                para_lower = para_plain.lower()
                # Check whether the paragraph is under a "kumotaan" intro subsection.
                # If not, we still require "kumotaan" in the paragraph itself to
                # avoid stitching a cross-paragraph false repeal.
                para_parent = para.getparent()
                under_kumotaan_intro = para_parent in kumotaan_intro_subsections
                if not under_kumotaan_intro:
                    if "kumotaan" not in para_lower and "lukuun ottamatta" not in para_lower:
                        continue
                # Remove leading item label "N)" or "N." before locating the
                # parent reference, so the enactment-ordering check measures the
                # same string the fragment extractor sees.
                para_plain = re.sub(r"^\d+\)\s*", "", para_plain)
                cite_match = citation_re.search(para_plain)
                citation_has_repeal_authority = bool(
                    cite_match
                    and (
                        under_kumotaan_intro
                        or _has_repeal_enactment_before(para_plain, cite_match.start())
                    )
                )
                if cite_match and citation_has_repeal_authority:
                    # Extract text after citation, before "sellaisena kuin" / ";".
                    fragment = _vts_extract_after_citation(para_plain, citation_re)
                    if fragment:
                        return fragment
                if has_title:
                    title_pos, _ = _find_parent_title_span(para_plain, title_variants)
                    title_has_repeal_authority = title_pos >= 0 and (
                        under_kumotaan_intro
                        or _has_repeal_enactment_before(para_plain, title_pos)
                    )
                    if title_has_repeal_authority:
                        fragment = _vts_extract_after_parent_title(para_plain, title_variants)
                        if fragment:
                            return fragment
            # Paragraphized containers keep repeal ownership within one item.
            # Do not fall back to whole-section text, or a parent citation in one
            # paragraph and "kumotaan" in a sibling can be stitched together into
            # a false repeal of the parent statute.
            continue

        # Shape 2: inline prose — the whole section text contains the citation.
        sec_plain = re.sub(r"\s+", " ", full_text).strip()
        if has_citation:
            cite_match = citation_re.search(sec_plain)
            if cite_match and _has_repeal_enactment_before(sec_plain, cite_match.start()):
                fragment = _vts_extract_after_citation(sec_plain, citation_re)
                if fragment:
                    return fragment
        if has_title:
            title_pos, _ = _find_parent_title_span(sec_plain, title_variants)
            if title_pos >= 0 and _has_repeal_enactment_before(sec_plain, title_pos):
                fragment = _vts_extract_after_parent_title(sec_plain, title_variants)
                if fragment:
                    return fragment

    return ""


def _vts_extract_after_citation(text: str, citation_re: "re.Pattern[str]") -> str:
    """Extract the repeal-target fragment from *text* that follows the citation.

    Truncates at the earliest of:
    - ``sellaisena kuin`` / ``sellaisina kuin`` (prior-amendment back-references)
    - semicolon ``;`` (item boundary in numbered lists)
    - another statute citation ``(NUM/YY)`` (inline prose format)
    - end of text

    Returns the cleaned fragment, or empty string if there is nothing useful.
    """
    m = citation_re.search(text)
    if not m:
        return ""
    after = text[m.end() :]
    # Collect all truncation points; take the earliest.
    cut_pos = len(after)

    # "sellaisena/sellaisina kuin"
    c1 = re.search(r"\bsellais(?:ena|ina)\s+kuin\b", after, re.IGNORECASE)
    if c1:
        cut_pos = min(cut_pos, c1.start())

    # Semicolon (item boundary)
    sc = after.find(";")
    if sc >= 0:
        cut_pos = min(cut_pos, sc)

    # Sentence boundary. Transitional repeal lists are sentence-bounded; if
    # we bleed into the following sentence, ordinary cross-references like
    # "4 §:n 2 momentissa" become false repeal targets.
    period = after.find(".")
    if period >= 0:
        cut_pos = min(cut_pos, period + 1)

    # Next explicit statute citation (NUM/YY or NUM/YYYY) — marks boundary
    # between inline-prose statute entries.
    next_cit = re.search(r"\(\s*\d+\s*/\s*\d{2,4}\s*\)", after)
    if next_cit:
        cut_pos = min(cut_pos, next_cit.start())

    # Statute name transition: "... annetun lain|asetuksen|päätöksen N ..."
    # In inline prose the target clause ends at a comma followed by a new
    # statute-name phrase like "ikääntyneen väestön ... annetun lain".
    # Detect by a comma followed by a genitive statute reference that has no
    # explicit citation — i.e., comma + text + "annetun lain/asetuksen".
    statute_name_transition = re.search(
        r",\s+[A-ZÄÖÅ][^,;(]*\bannetun\s+(?:[a-zäöå]+(?:lain|asetuksen|päätöksen|päätös)|lain|asetuksen|päätöksen|päätös)\b",
        after,
        re.IGNORECASE,
    )
    if statute_name_transition:
        cut_pos = min(cut_pos, statute_name_transition.start())

    # Same transition but joined by "ja" instead of comma:
    # "11 § ja sosiaalihuollon ... annetun lain 24 §"
    # The "ja" must follow a § reference (not mid-word) to avoid false matches
    # on "ja" inside statute titles.
    # IMPORTANT: first char after "ja " must be a letter (not a digit) so that
    # numeric-list continuations like "43 ja 45–47 kohta sekä X annetun asetuksen"
    # are not mistaken for a statute-name transition.
    ja_statute_transition = re.search(
        r"\s+ja\s+[a-zäöå][^,;(]*\bannetun\s+(?:[a-zäöå]+(?:lain|asetuksen|päätöksen|päätös)|lain|asetuksen|päätöksen|päätös)\b",
        after,
        re.IGNORECASE,
    )
    if ja_statute_transition:
        cut_pos = min(cut_pos, ja_statute_transition.start())

    return after[:cut_pos].strip()


def _vts_extract_after_parent_title(text: str, title_variants: List[str]) -> str:
    """Extract repeal-target fragment that follows a bare parent-title mention."""
    norm_text = re.sub(r"\s+", " ", text).strip()
    start, end = _find_parent_title_span(norm_text, title_variants)
    if start < 0:
        return ""
    after = norm_text[end:]
    # Do not treat references to an amendment act of the parent statute as a
    # repeal of the parent itself, e.g. "X- lain 6 §:n muuttamisesta annettu laki".
    if re.match(
        r"\s+\d+\s*[a-z]?\s*§(?::n|\b)[^.;]{0,80}\bmuuttamisesta\s+annettu\s+(?:laki|asetus|päätöksen|päätös)\b",
        after,
        re.IGNORECASE,
    ):
        return ""
    # The matched parent title can be embedded inside the *name of an amending
    # law* that the clause repeals, e.g.
    #   "kumotaan laki <parent title> annetun lain 13 §:n muuttamisesta (679/2022)".
    # Here the matched title is in genitive ("<parent> annetun lain") and the
    # "N §:n muuttamisesta" belongs to the amending law's own title, terminated
    # by that amending law's separate citation "(XXXX/YYYY)" (or by the literal
    # "annettu laki" form). The "§" is part of the repealed law's name, not a
    # master section, so this is NOT a repeal of the master statute's section N.
    if re.match(
        r"\s+(?:annetun\s+lain\s+)?\d+\s*[a-z]?\s*§(?::n|\b)"
        r"[^.;]{0,80}\bmuuttamisesta\s+(?:\(\s*\d+\s*/\s*\d{2,4}\s*\)|annettu\s+laki)",
        after,
        re.IGNORECASE,
    ):
        return ""
    cut_pos = len(after)

    c1 = re.search(r"\bsellais(?:ena|ina)\s+kuin\b", after, re.IGNORECASE)
    if c1:
        cut_pos = min(cut_pos, c1.start())
    sc = after.find(";")
    if sc >= 0:
        cut_pos = min(cut_pos, sc)
    period = after.find(".")
    if period >= 0:
        cut_pos = min(cut_pos, period + 1)
    next_cit = re.search(r"\(\s*\d+\s*/\s*\d{2,4}\s*\)", after)
    if next_cit:
        cut_pos = min(cut_pos, next_cit.start())
    statute_name_transition = re.search(
        r",\s+[A-ZÄÖÅa-zäöå][^,;(]*\bannetun\s+(?:[a-zäöå]+(?:lain|asetuksen|päätöksen|päätös)|lain|asetuksen|päätöksen|päätös)\b",
        after,
        re.IGNORECASE,
    )
    if statute_name_transition:
        cut_pos = min(cut_pos, statute_name_transition.start())
    ja_statute_transition = re.search(
        r"\s+ja\s+[a-zäöå0-9][^,;(]*\bannetun\s+(?:[a-zäöå]+(?:lain|asetuksen|päätöksen|päätös)|lain|asetuksen|päätöksen|päätös)\b",
        after,
        re.IGNORECASE,
    )
    if ja_statute_transition:
        cut_pos = min(cut_pos, ja_statute_transition.start())
    return after[:cut_pos].strip()


def _voimaantulo_force_except_fragment_for_parent(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str = "",
) -> str:
    """Return excluded-target fragment from ``jää ... lukuun ottamatta voimaan`` prose.

    Cross-statute voimaantulo clauses sometimes keep another statute in force
    except for specific provisions, e.g.:

      "Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan ..."

    For replay this means the named provisions are repealed in the cited parent
    statute when the amendment enters into force.
    """
    if not parent_id:
        return ""
    citation_re = _vts_parent_citation_re(parent_id)
    if citation_re is None:
        return ""
    title_variants = _parent_title_variants(parent_title)
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return ""

    all_sections = _vts_candidate_containers(tree)
    if not all_sections:
        return ""

    for sec in reversed(all_sections):
        full_text = re.sub(r"\s+", " ", etree.tostring(sec, method="text", encoding="unicode")).strip()
        if "lukuun ottamatta" not in full_text.lower() or "voimaan" not in full_text.lower():
            continue
        has_citation = bool(citation_re.search(full_text))
        title_start, title_end = _find_parent_title_span(full_text, title_variants)
        has_title = title_start >= 0
        if not has_citation and not has_title:
            continue

        after = full_text
        if has_citation:
            match = citation_re.search(full_text)
            assert match is not None
            after = full_text[match.end():]
        elif has_title:
            after = full_text[title_end:]

        m = re.search(
            r"\bjää(?:vät)?\s+(?:sen|niiden|lain|asetuksen|päätöksen)?\s*(.+?)\s+lukuun\s+ottamatta\s+voimaan\b",
            after,
            flags=re.IGNORECASE,
        )
        if not m:
            continue
        fragment = m.group(1).strip(" ,;")
        fragment = re.sub(r"\bmomenttia\b", "momentti", fragment, flags=re.IGNORECASE)
        fragment = re.sub(r"§:ää\b", "§", fragment)
        return re.sub(r"\s+", " ", fragment).strip()
    return ""


def _expand_section_range_vts(start: str, end: str) -> List[str]:
    """Expand a section range for voimaantulo repeal extraction.

    Handles both numeric ranges (``"12"`` – ``"14"`` → ``["12","13","14"]``)
    and same-base letter-suffix ranges (``"33a"`` – ``"33c"`` → ``["33a","33b","33c"]``).
    Returns ``[start]`` unchanged for ranges that don't match either pattern.
    """
    # Pure numeric range
    if start.isdigit() and end.isdigit():
        s, e = int(start), int(end)
        if s <= e:
            return [str(i) for i in range(s, e + 1)]
        return [start]
    # Letter-suffix range: same numeric base, single letters differ (e.g. "33a"–"33c")
    m_start = re.fullmatch(r"(\d+)([a-z])", start, re.IGNORECASE)
    m_end = re.fullmatch(r"(\d+)([a-z])", end, re.IGNORECASE)
    if m_start and m_end and m_start.group(1) == m_end.group(1):
        base = m_start.group(1)
        s_chr = m_start.group(2).lower()
        e_chr = m_end.group(2).lower()
        if ord(s_chr) <= ord(e_chr):
            return [f"{base}{chr(c)}" for c in range(ord(s_chr), ord(e_chr) + 1)]
    return [start]


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_voimaantulo_repeals(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str = "",
    skipped_targets_out: Optional[List[VtsSkippedTarget]] = None,
    source_diagnostics_out: Optional[List[VtsSourceDiagnostic]] = None,
) -> List[AmendmentOp]:
    """Extract repeal operations from voimaantulosäännös (transitional provisions).

    Back-compat shim over :func:`extract_voimaantulo_repeals_partition`. The
    partition is the canonical conserving carrier; this shim drains its
    ``skipped_targets`` / ``source_diagnostics`` lanes into the legacy out-params
    (the production replay ledger reads them) and returns the accepted ops. The
    drain is the single emission path — the typed records are forwarded from the
    partition, never re-derived, so no double-emit occurs.

    Returns a (possibly empty) list of ``AmendmentOp`` objects. All returned ops
    carry ``op_type='REPEAL'`` and typed ``voimaantulo_repeal=True`` provenance.
    """
    partition = extract_voimaantulo_repeals_partition(
        xml_bytes, parent_id, parent_title=parent_title
    )
    if skipped_targets_out is not None:
        skipped_targets_out.extend(partition.skipped_targets)
    if source_diagnostics_out is not None:
        source_diagnostics_out.extend(partition.source_diagnostics)
    return list(partition.accepted)


def extract_voimaantulo_repeals_partition(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str = "",
) -> VtsRepealPartition:
    """Extract voimaantulosäännös repeals as a conserving partition.

    When a new law repeals provisions of another statute via a transitional
    provision section (``Tällä lailla kumotaan …``), the johtolause just says
    ``säädetään:`` with no op keywords.  This function searches the last
    sections of the amendment XML for such repeal clauses, filters by the
    parent statute citation, and returns the corresponding ``AmendmentOp``
    objects in the partition's accepted lane.

    Whole-section (``N §``), chapter (``N luku``), and subsection/item targets
    parsed by the shared Finnish address parser are extracted. Sub-item
    (alakohta) depth is still skipped here until the shared late-waist target
    model grows a dedicated carrier for it.

    This is a QUIRKS-mode feature: the caller should gate it behind
    ``strict_profile is None``.

    Conservation (Audit C): unsupported or unsafe parsed targets are routed to
    the partition's ``skipped_targets`` lane (typed ``VtsSkippedTarget``), and
    unreadable / structurally empty source shapes to ``source_diagnostics``
    (typed ``VtsSourceDiagnostic``), instead of disappearing silently. The
    accepted op set is byte-identical to the previous return value.
    """
    skipped_targets: List[VtsSkippedTarget] = []
    source_diagnostics: List[VtsSourceDiagnostic] = []
    skipped_targets_out = skipped_targets
    source_diagnostics_out = source_diagnostics

    if not _source_may_contain_vts_repeal(xml_bytes):
        diagnostic = _classify_vts_source_diagnostic(xml_bytes, parent_id, parent_title)
        if diagnostic is not None:
            source_diagnostics_out.append(diagnostic)
        return _vts_partition([], skipped_targets, source_diagnostics)

    fragment = _voimaantulo_repeal_fragment_for_parent(xml_bytes, parent_id, parent_title=parent_title)
    if not fragment:
        fragment = _voimaantulo_force_except_fragment_for_parent(xml_bytes, parent_id, parent_title=parent_title)
    if not fragment:
        diagnostic = _classify_vts_source_diagnostic(xml_bytes, parent_id, parent_title)
        if diagnostic is not None:
            source_diagnostics_out.append(diagnostic)
        return _vts_partition([], skipped_targets, source_diagnostics)

    ops: List[AmendmentOp] = []
    seen_labels: Set[tuple[str, int | None, str | None, str | None] | str] = set()

    def _chapter_scoped_address_blocks(text: str) -> List[tuple[str | None, List[ParsedLegalAddress]]]:
        markers = list(_CHAPTER_MARKER_RE.finditer(text))
        if not markers:
            return [(None, scan_legal_addresses(text))]

        blocks: List[tuple[str | None, str]] = []
        if markers[0].start() > 0:
            preamble = text[:markers[0].start()]
            blocks.append((None, preamble))
        for i, marker in enumerate(markers):
            chapter_label = re.sub(r"\s+", "", marker.group(1).strip()).lower()
            block_start = marker.end()
            block_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            blocks.append((chapter_label, text[block_start:block_end]))

        out: List[tuple[str | None, List[ParsedLegalAddress]]] = []
        for chapter_label, block_text in blocks:
            parsed: List[ParsedLegalAddress] = []
            for addr in scan_legal_addresses(block_text):
                if chapter_label is not None and addr.chapter is None and addr.section:
                    parsed.append(dc_replace(addr, chapter=chapter_label))
                else:
                    parsed.append(addr)
            out.append((chapter_label, parsed))
        return out

    # --- Chapter repeals (N luku) ---
    # Use the shared free-text grammar driver for chapter references too.
    # Only addresses with a chapter label (no section context) are turned
    # into REPEAL ops.
    for addr in scan_legal_addresses(fragment):
        if addr.chapter is None:
            continue  # not a chapter reference — handled below
        norm = addr.chapter
        if norm and norm not in seen_labels:
            seen_labels.add(norm)
            ops.append(
                AmendmentOp(
                    op_id=f"vts_repeal_L_{norm}",
                    op_type=OpType.REPEAL,
                    **fi_chapter_target(norm),
                    voimaantulo_repeal=True,
                    witness_rule_id=FI_VTS_VOIMAANTULO_REPEAL_RULE_ID,
                )
            )

    # Detect if the fragment uses "kohta" (items) but the address parser
    # cannot yet express item-only references (no momentin prefix).  When
    # "kohta" is present in the fragment and would produce a bare section
    # address with no subsection/item, skip the op — a false whole-section
    # repeal is worse than no repeal.
    fragment_has_kohta_only = bool(
        re.search(r"\bkohta\b", fragment, re.IGNORECASE)
        and not re.search(r"\bmomentin?\b", fragment, re.IGNORECASE)
    )

    # --- Section/subsection/item repeals ---
    # Use the shared free-text grammar driver to extract all legal addresses from
    # the fragment. Whole-section addresses become plain section REPEAL ops.
    # Subsection and plain item targets are carried through as paragraph/item
    # fields on the section-level AmendmentOp. Alakohta depth is still skipped
    # because AmendmentOp has no dedicated subitem carrier.
    for _block_chapter, addresses in _chapter_scoped_address_blocks(fragment):
        for addr in addresses:
            if addr.chapter is not None and not addr.section:
                # Chapter refs already handled above
                continue
            if addr.special:
                # Skip facet refs for now; VTS has no safe facet repeal carrier.
                _record_vts_skipped_target(
                    skipped_targets_out,
                    reason_code="unsupported_special_target",
                    source_reason="VTS repeal target names a facet; the extractor will not widen it into a whole-section repeal.",
                    source_statute=parent_id,
                    source_excerpt=fragment,
                    addr=addr,
                )
                continue
            if addr.subitem is not None:
                # Skip alakohta depth for now; AmendmentOp has no subitem carrier.
                _record_vts_skipped_target(
                    skipped_targets_out,
                    reason_code="unsupported_subitem_target",
                    source_reason="VTS repeal target reaches alakohta depth; AmendmentOp has no subitem carrier, so no broader repeal was emitted.",
                    source_statute=parent_id,
                    source_excerpt=fragment,
                    addr=addr,
                )
                continue
            if not addr.section:
                # Skip standalone momentti refs with no section context.
                _record_vts_skipped_target(
                    skipped_targets_out,
                    reason_code="standalone_target_without_section",
                    source_reason="VTS repeal target lacks section context; the extractor will not infer a host section.",
                    source_statute=parent_id,
                    source_excerpt=fragment,
                    addr=addr,
                )
                continue
            if fragment_has_kohta_only and addr.subsection is None and addr.item is None:
                # The fragment mentions items (kohdat) but the address parser
                # produced a bare section ref — skip to avoid a false
                # whole-section repeal. Tracked as a known limitation in
                # address_parse until "N kohta" (no momentin) is supported.
                _record_vts_skipped_target(
                    skipped_targets_out,
                    reason_code="unsafe_kohta_only_bare_section_parse",
                    source_reason="VTS fragment mentions kohta without momentti but parsed as a bare section; whole-section repeal suppressed.",
                    source_statute=parent_id,
                    source_excerpt=fragment,
                    addr=addr,
                )
                continue
            dedup_key = (
                addr.section,
                addr.subsection,
                addr.item,
                addr.chapter,
            )
            if dedup_key not in seen_labels:
                seen_labels.add(dedup_key)
                ops.append(
                    AmendmentOp(
                        op_id=(
                            f"vts_repeal_P_{addr.section}"
                            + (f"_L{addr.chapter}" if addr.chapter is not None else "")
                            + (f"_m{addr.subsection}" if addr.subsection is not None else "")
                            + (f"_k{addr.item}" if addr.item is not None else "")
                        ),
                        op_type=OpType.REPEAL,
                        **fi_section_target(
                            addr.section,
                            chapter=addr.chapter,
                            subsection=addr.subsection,
                            item=addr.item,
                        ),
                        voimaantulo_repeal=True,
                        witness_rule_id=FI_VTS_VOIMAANTULO_REPEAL_RULE_ID,
                    )
                )

    return _vts_partition(ops, skipped_targets, source_diagnostics)


def _vts_partition(
    ops: List[AmendmentOp],
    skipped_targets: List[VtsSkippedTarget],
    source_diagnostics: List[VtsSourceDiagnostic],
) -> VtsRepealPartition:
    """Assemble the conserving VTS partition from the accumulated lanes."""
    residuals = tuple(
        Residual(
            kind="out_of_scope",
            reason=f"{skip.reason_code}: {skip.source_reason}",
            scope=f"{skip.source_statute}:section:{skip.target_section}",
            text=skip.source_excerpt,
            blocking=skip.blocking,
        )
        for skip in skipped_targets
    ) + tuple(
        Residual(
            kind="benign_uninterpreted",
            reason=f"{diag.reason_code}: {diag.source_reason}",
            scope=f"{diag.source_statute}:source",
            source_unit_id=diag.source_statute,
            text=diag.source_excerpt,
            blocking=diag.blocking,
        )
        for diag in source_diagnostics
    )
    return VtsRepealPartition(
        FilterResult(accepted_items=tuple(ops)),
        residuals=residuals,
        skipped_targets=tuple(skipped_targets),
        source_diagnostics=tuple(source_diagnostics),
    )


# ---------------------------------------------------------------------------
# Wrapper helpers (gated by strict_profile)
# ---------------------------------------------------------------------------


def extract_vts_cross_statute_repeals(
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str,
    strict_profile: "Optional[StrictProfile]",
    skipped_targets_out: Optional[List[VtsSkippedTarget]] = None,
) -> Optional[List[AmendmentOp]]:
    """Heuristic #38: VTS cross-statute repeal.

    This is source-local repeal recovery, so it is safe in both strict and
    quirks replay modes. ``strict_profile`` is retained for API compatibility.
    """
    if parent_id:
        return extract_voimaantulo_repeals(
            xml_bytes,
            parent_id,
            parent_title=parent_title,
            skipped_targets_out=skipped_targets_out,
        )
    return None


def extract_vts_repeals_fallback(
    johto: str,
    xml_bytes: bytes,
    parent_id: str,
    parent_title: str,
    strict_profile: "Optional[StrictProfile]",
    skipped_targets_out: Optional[List[VtsSkippedTarget]] = None,
) -> Optional[List[AmendmentOp]]:
    """Heuristic #37: voimaantulosäännös repeal extraction.

    This handles section 1 / entry-into-force repeal clauses that do not use a
    normal operative johtolause. The extraction is source-local, so it is
    available in both strict and quirks replay modes.
    """
    if any(kw in johto.lower() for kw in _VTS_OP_KEYWORDS):
        return None
    if parent_id:
        return extract_voimaantulo_repeals(
            xml_bytes,
            parent_id,
            parent_title=parent_title,
            skipped_targets_out=skipped_targets_out,
        )
    return None
