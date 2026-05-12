"""Voimaantulosäännös (transitional-provision) repeal extraction.

These functions extract REPEAL AmendmentOps from the transitional-provision
section of an amendment statute.  They depend on lxml (read-only) and
AmendmentOp, but not on XMLStatute or any replay state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from typing import List, Literal, Optional, Set, TYPE_CHECKING

import lxml.etree as etree

from lawvm.finland.ops import AmendmentOp
from lawvm.finland.address_parse import ParsedLegalAddress, parse_legal_addresses

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
]

VTS_SKIPPED_TARGET_RULE_ID = "PARSE.VTS_SKIPPED_TARGET_UNSUPPORTED"
VTS_SOURCE_DIAGNOSTIC_RULE_ID = "PARSE.VTS_SOURCE_UNREADABLE_OR_EMPTY"


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
    quirks_disposition: str = "record"

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
    quirks_disposition: str = "record"

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
    variants = [norm]
    if norm.endswith("laki"):
        variants.append(norm[:-4] + "lain")
    elif norm.endswith("asetus"):
        variants.append(norm[:-6] + "asetuksen")
    elif norm.endswith("päätös"):
        variants.append(norm[:-6] + "päätöksen")
    # Titles that start with "laki " also appear in cross-statute prose as
    # "<rest> annetun lain" (genitive form without the leading "laki").
    if norm.startswith("laki "):
        variants.append(norm[5:] + " annetun lain")
    return list(dict.fromkeys(v for v in variants if v))


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
) -> VtsSourceDiagnostic | None:
    try:
        parent_year, parent_num_str = parent_id.split("/")
        int(parent_num_str)
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
    if not _vts_candidate_containers(tree):
        return VtsSourceDiagnostic(
            rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
            reason_code="no_candidate_containers",
            source_reason="VTS source XML contained no trailing section, entryIntoForce, or conclusion containers",
            source_statute=parent_id,
            source_excerpt=_vts_source_excerpt(xml_bytes),
        )
    return None


# ---------------------------------------------------------------------------
# Fragment extraction helpers
# ---------------------------------------------------------------------------


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
    try:
        parent_year, parent_num_str = parent_id.split("/")
        parent_num = int(parent_num_str)
    except (ValueError, AttributeError):
        return ""
    parent_year_short = parent_year[-2:]

    # Build a regex that matches this statute's citation: (925/79) or (925/1979)
    citation_re = re.compile(
        r"\(\s*"
        + re.escape(str(parent_num))
        + r"\s*/\s*(?:"
        + re.escape(parent_year)
        + r"|"
        + re.escape(parent_year_short)
        + r")\s*\)",
        re.IGNORECASE,
    )

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
                if citation_re.search(para_text):
                    # Extract text after citation, before "sellaisena kuin" / ";",
                    # stripping item prefix like "3)"
                    # Remove leading item label "N)" or "N."
                    para_plain = re.sub(r"^\d+\)\s*", "", para_plain)
                    fragment = _vts_extract_after_citation(para_plain, citation_re)
                    if fragment:
                        return fragment
                if has_title:
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
            fragment = _vts_extract_after_citation(sec_plain, citation_re)
            if fragment:
                return fragment
        if has_title:
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
    try:
        parent_year, parent_num_str = parent_id.split("/")
        parent_num = int(parent_num_str)
    except (ValueError, AttributeError):
        return ""
    parent_year_short = parent_year[-2:]
    citation_re = re.compile(
        r"\(\s*"
        + re.escape(str(parent_num))
        + r"\s*/\s*(?:"
        + re.escape(parent_year)
        + r"|"
        + re.escape(parent_year_short)
        + r")\s*\)",
        re.IGNORECASE,
    )
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

    When a new law repeals provisions of another statute via a transitional
    provision section (``Tällä lailla kumotaan …``), the johtolause just says
    ``säädetään:`` with no op keywords.  This function searches the last
    sections of the amendment XML for such repeal clauses, filters by the
    parent statute citation, and returns the corresponding ``AmendmentOp``
    objects.

    Whole-section (``N §``), chapter (``N luku``), and subsection/item targets
    parsed by the shared Finnish address parser are extracted. Sub-item
    (alakohta) depth is still skipped here until the shared late-waist target
    model grows a dedicated carrier for it.

    This is a QUIRKS-mode feature: the caller should gate it behind
    ``strict_profile is None``.

    If ``skipped_targets_out`` is provided, unsupported or unsafe parsed
    targets are appended as ``VtsSkippedTarget`` records instead of disappearing
    silently. If ``source_diagnostics_out`` is provided, unreadable or
    structurally empty VTS source shapes are recorded separately from a normal
    no-match result. Returns a (possibly empty) list of ``AmendmentOp`` objects.
    All returned ops carry ``op_type='REPEAL'`` and typed
    ``voimaantulo_repeal=True`` provenance.
    """
    fragment = _voimaantulo_repeal_fragment_for_parent(xml_bytes, parent_id, parent_title=parent_title)
    if not fragment:
        fragment = _voimaantulo_force_except_fragment_for_parent(xml_bytes, parent_id, parent_title=parent_title)
    if not fragment:
        if source_diagnostics_out is not None:
            diagnostic = _classify_vts_source_diagnostic(xml_bytes, parent_id)
            if diagnostic is not None:
                source_diagnostics_out.append(diagnostic)
        return []

    ops: List[AmendmentOp] = []
    seen_labels: Set[tuple[str, int | None, str | None, str | None] | str] = set()

    def _chapter_scoped_address_blocks(text: str) -> List[tuple[str | None, List[ParsedLegalAddress]]]:
        chapter_marker_re = re.compile(r'(\d+(?:\s*[a-z])?)\s+luvun\b', re.IGNORECASE)
        markers = list(chapter_marker_re.finditer(text))
        if not markers:
            return [(None, parse_legal_addresses(text))]

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
            for addr in parse_legal_addresses(block_text):
                if chapter_label is not None and addr.chapter is None and addr.section:
                    parsed.append(dc_replace(addr, chapter=chapter_label))
                else:
                    parsed.append(addr)
            out.append((chapter_label, parsed))
        return out

    # --- Chapter repeals (N luku) ---
    # Use the shared address_parse library for chapter references too.
    # Only addresses with a chapter label (no section context) are turned
    # into REPEAL ops.
    for addr in parse_legal_addresses(fragment):
        if addr.chapter is None:
            continue  # not a chapter reference — handled below
        norm = addr.chapter
        if norm and norm not in seen_labels:
            seen_labels.add(norm)
            ops.append(
                AmendmentOp(
                    op_id=f"vts_repeal_L_{norm}",
                    op_type="REPEAL",
                    target_section=norm,
                    target_unit_kind="chapter",
                    voimaantulo_repeal=True,
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
    # Use the shared address_parse library to extract all legal addresses from
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
                        op_type="REPEAL",
                        target_section=addr.section,
                        target_unit_kind="section",
                        target_chapter=addr.chapter,
                        target_paragraph=addr.subsection,
                        target_item=addr.item,
                        voimaantulo_repeal=True,
                    )
                )

    return ops


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
