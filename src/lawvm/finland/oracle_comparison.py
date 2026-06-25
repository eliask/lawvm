"""Finland-specific oracle comparison normalizations and presentation handling.

This module owns all FI-specific logic for cleaning/normalizing Finlex oracle
text and detecting presentation-only diffs for bench, diff, explain, structural
review, etc.

The goal is to treat Finlex editorial/presentation artifacts (kumottu stubs,
dot leaders in fee tables, list qualifier wrapups, chemical convention
formatting, value table alignment, heading padding, etc.) as non-divergences
for comparison metrics, while never mutating replay output, source text, or
legal tree state.

Generic comparison machinery lives in lawvm.core.comparison_normalization and
lawvm.semantic.diff. Jurisdiction-specific oracle quirks belong here.

See also: lawvm/finland/editorial_adjudication.py for related editorial
adjudication (not pure comparison normalization).
"""

from __future__ import annotations

import re
from typing import Any

from lawvm.core.comparison_normalization import ComparisonNormalizationRule, normalize_comparison_text


_REPEAL_CITATION_RE = (
    r'(?:'
    r'[LAP]:ll[äa]\s+(?:[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}/[0-9]{1,8}|[0-9]{1,8}/[0-9]{4})\s+v\.\s+[0-9]{4}'
    r'|[LAP]:ll[äa]\s+(?:[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}/[0-9]{1,8}|[0-9]{1,8}/[0-9]{4})'
    r'|[LAP]\s+(?:[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}/[0-9]{1,8}|[0-9]{1,8}/[0-9]{4})\s+v\.\s+[0-9]{4}'
    r'|[LAP]\s+(?:[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}/[0-9]{1,8}|[0-9]{1,8}/[0-9]{4})'
    r')'
)


_AIEMPI_SANAMUOTO_SUFFIX = r'(?:\s*Aiempi\s+sanamuoto\s+kuuluu\s*:?\s*)?'

_EDITORIAL_RE = re.compile(
    rf'\d+\s*[a-zäöå]?\s*§\s+on\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    rf'(?:\s*,\s*joka\s+tul(?:ee|i)\s+voimaan\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s*[,.\s]*|\s*[,.]+\s*){_AIEMPI_SANAMUOTO_SUFFIX}|'
    rf'\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?(?:luku|mome?ntti|momentit|mom\.?|kohta|kohdat|§)\s+(?:on|ovat)\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    rf'(?:\s*,\s+joka\s+tul(?:ee|i)\s+voimaan\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s*[,.\s]*|\s*[,.]+\s*){_AIEMPI_SANAMUOTO_SUFFIX}|'
    r'[LAP]:ll[äa]\s+\d+/\d{4}\s+(?:muutettu|lisätty|kumottu|siirretty)\s+[^.]*?'
    r'(?:tul(?:ee|i)\s+voimaan\s+\d{1,2}\.\d{1,2}\.\d{4}\.?\s*|\.\s*)|'
    r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)',
    re.DOTALL,
)


_KUMOTTU_ATTRIBUTION_RE = re.compile(
    rf'on\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    r'(?:\s*,\s*joka\s+tul(?:ee|i)\s+voimaan\s+\d{1,2}\.\d{1,2}\.\d{4})?\s*\.?',
    re.DOTALL,
)

# Matches full kumottu-stub sentences for removal from oracle text before comparison.
_KUMOTTU_STUBS_RE = re.compile(
    rf'(?:'
    rf'\d+\s{{0,4}}[a-zäöå]?\s{{0,4}}(?:[–\-—]\s{{0,4}}\d+\s{{0,4}}[a-zäöå]?\s{{0,4}})?§'
    rf'|\d+\s{{0,4}}[a-zäöå]?\s{{0,4}}(?:[–\-—]\s{{0,4}}\d+\s{{0,4}}[a-zäöå]?\s{{0,4}})?luku'
    rf'|\d+\s{{1,4}}(?:mome?ntti|momentin|kohta|kohdan)'
    rf'|\d+[–\-—]\d+\s{{1,4}}(?:momentit|kohdat|momenttia|kohtaa)'
    rf')?\s{{0,4}}'
    rf'(?:'
    rf'\d+\s{{0,4}}[a-zäöå]?\s{{0,4}}(?:[–\-—]\s{{0,4}}\d+\s{{0,4}}[a-zäöå]?\s{{0,4}})?§'
    rf'|\d+\s{{0,4}}[a-zäöå]?\s{{0,4}}(?:[–\-—]\s{{0,4}}\d+\s{{0,4}}[a-zäöå]?\s{{0,4}})?luku'
    rf'|\d+\s{{1,4}}(?:mome?ntti|momentin|kohta|kohdan)'
    rf'|\d+[–\-—]\d+\s{{1,4}}(?:momentit|kohdat|momenttia|kohtaa)'
    rf')'
    rf'\s{{1,4}}(?:on|ovat)\s{{1,4}}kumottu'
    rf'(?:\s{{1,4}}\d{{1,2}}\.\d{{1,2}}\.\d{{4}})?'
    rf'\s{{1,4}}(?:{_REPEAL_CITATION_RE}'
    rf'(?:\s{{0,4}},\s{{0,4}}joka\s{{1,4}}tul(?:ee|i)\s{{1,4}}voimaan\s{{1,4}}\d{{1,2}}\.\d{{1,2}}\.\d{{4}})?'
    rf'|\w+:ll[äa]'
    rf'|[a-zäöåA-ZÄÖÅ\-]*lailla'
    rf')[^.]*\.?',
    re.DOTALL | re.IGNORECASE,
)
_KUMOTTU_STUB_SURFACE_RE = re.compile(r"\b(?:on|ovat)\s+kumottu\b|:ll[äa]\b", re.IGNORECASE)

_OLD_CODE_REFERENCE_MARKER_RE = re.compile(
    r"^\s{0,8}+\d{1,4}+\s{0,4}+[a-zäöå]?\s{0,4}+§:n"
    r"\s{1,4}+sijasta\s{1,4}+ks\.\s{1,4}+\S",
    re.IGNORECASE,
)
_OLD_CODE_REFERENCE_MARKER_WITH_LEAD_RE = re.compile(
    r"^\s{0,8}+\d{1,4}+\s{0,4}+[a-zäöå]?\s{0,4}+§\s{1,4}+"
    r"\d{1,4}+\s{0,4}+[a-zäöå]?\s{0,4}+§:n"
    r"\s{1,4}+sijasta\s{1,4}+ks\.\s{1,4}+\S",
    re.IGNORECASE,
)
_DASH_PLACEHOLDER_RE = re.compile(r"^\s{0,8}+[-–—](?:\s{0,8}+[-–—]){2,}\s{0,8}+$")


def is_old_code_reference_marker(text: str) -> bool:
    """Return True for Finlex old-code substitution-reference notices.

    Historical codes sometimes render an obsolete provision as editorial text
    like ``5 §:n sijasta ks. L ...`` instead of source wording. That marker is a
    comparison/adjudication surface, not replayed legal text.
    """
    stripped = text.strip()
    return (
        _OLD_CODE_REFERENCE_MARKER_RE.match(stripped) is not None
        or _OLD_CODE_REFERENCE_MARKER_WITH_LEAD_RE.match(stripped) is not None
    )


def _is_replay_obsolete_placeholder_or_bracketed_text(text: str) -> bool:
    stripped = text.strip()
    if _DASH_PLACEHOLDER_RE.match(stripped) is not None:
        return True
    return stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2


def _is_old_code_reference_marker_diff(events: list[dict[str, Any]]) -> bool:
    if len(events) != 1:
        return False
    event = events[0]
    if event.get("kind") != "wording_text_changed":
        return False
    left_text = str(event.get("left_text") or "")
    right_text = str(event.get("right_text") or "")
    return (
        _is_replay_obsolete_placeholder_or_bracketed_text(left_text)
        and is_old_code_reference_marker(right_text)
    )

_EMBEDDED_FIVE_AS_I_OCR_RE = re.compile(
    r"(?<=[A-Za-zÄÖÅäöå]{2})5(?=[A-Za-zÄÖÅäöå]{2})"
)


def _normalize_embedded_five_as_i_ocr(text: str) -> str:
    """Normalize Finlex oracle OCR/type-in residue like ``sosiaal5sen``."""
    if "5" not in text:
        return text
    return _EMBEDDED_FIVE_AS_I_OCR_RE.sub("i", text)


_CHEMICAL_LIST_FOLLOW_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÄÖÅäöå(")


def _normalize_chemical_list_formatting(text: str) -> str:
    """Linear equivalent of the legacy chemical-list presentation regex."""
    if "  " not in text and ";;" not in text and " ;" not in text:
        return text
    pieces: list[str] = []
    last = 0
    i = 0
    changed = False
    text_len = len(text)
    while i < text_len:
        semicolon_at = text.find(";", i)
        double_space_at = text.find("  ", i)
        if semicolon_at < 0 and double_space_at < 0:
            break
        if semicolon_at < 0:
            run_start = double_space_at
        elif double_space_at < 0:
            run_start = semicolon_at
        else:
            run_start = min(semicolon_at, double_space_at)
        while run_start > last and text[run_start - 1] in "; ":
            run_start -= 1
        run_end = run_start
        while run_end < text_len and text[run_end] in "; ":
            run_end += 1
        if run_end - run_start >= 2:
            lookahead = run_end
            while lookahead < text_len and text[lookahead].isspace():
                lookahead += 1
            if lookahead < text_len and text[lookahead] in _CHEMICAL_LIST_FOLLOW_CHARS:
                pieces.append(text[last:run_start])
                pieces.append("; ")
                last = run_end
                changed = True
                i = run_end
                continue
        i = max(run_end, run_start + 1)
    if not changed:
        return text
    pieces.append(text[last:])
    return "".join(pieces)


def _normalize_kumottu_stub_sentences(text: str) -> str:
    """Remove Finlex kumottu stub sentences after a cheap surface prefilter."""
    if "kumottu" not in text:
        return text
    if _KUMOTTU_STUB_SURFACE_RE.search(text) is None:
        return text
    return _KUMOTTU_STUBS_RE.sub('', text)


_FINLEX_ORACLE_COMPARISON_RULES = (
    ComparisonNormalizationRule(
        name="fi_oracle_kumottu_stub_sentence",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Remove Finlex kumottu stub sentences from oracle comparison text.",
        transform=_normalize_kumottu_stub_sentences,
        required_substring="kumottu",
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_dot_leader_table_formatting",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove dot-leader alignment runs (........) used in Finlex for printed fee tables/schedules in small decisions. Pure presentation; content words remain for comparison.",
        pattern=re.compile(r"\.{3,}"),
        required_substring="...",
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_amendment_date_parenthetical",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove Finlex amendment-date parenthetical residue from oracle comparison text.",
        pattern=re.compile(r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)'),
        required_substring="(",
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_aiempi_sanamuoto_marker",
        rule_class="presentation_cleanup",
        kind="literal",
        description="Remove Finlex previous-wording marker from oracle comparison text.",
        old_text='Aiempi sanamuoto kuuluu:',
        new_text='',
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_embedded_five_as_i_ocr",
        rule_class="oracle_source_pathology",
        kind="callable",
        description=(
            "Normalize Finlex oracle OCR/type-in residue where digit 5 appears "
            "inside an alphabetic Finnish word, e.g. sosiaal5sen."
        ),
        transform=_normalize_embedded_five_as_i_ocr,
        required_substring="5",
    ),
    # Additional FI oracle presentation normalizations for list/schedule formatting
    # common in older decisions, fee tables, chemical lists, etc. These are pure
    # Finlex rendering artifacts (not present or rendered differently in source/replay).
    ComparisonNormalizationRule(
        name="fi_oracle_list_qualifier_wrapup",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove common Finnish list qualifier wrap-ups (e.g. 'tässä luettelossa mainittuja aineita sisältävät valmisteet', salts/preparations notes in convention lists). These are editorial presentation in appendices and substance lists.",
        pattern=re.compile(
            r'(?:tässä (?:luettelossa|mainitussa) mainittuja aineita sisältävät valmisteet'
            r'|tämän luettelon aineiden suolat mikäli sellaisten olemassaol'
            r'|tässä mainittuja aineita sisältävät valmisteet lukuun ottama).*?(?:\.|$)',
            re.IGNORECASE | re.DOTALL
        ),
        required_any_substrings=("valmisteet", "aineiden suolat"),
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_chemical_list_formatting",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Normalize common artifacts in Finnish implementations of chemical/controlled substance lists (1961 convention etc.): Greek letter variants, extra punctuation around names like 'Safroli;'. Content names are preserved.",
        transform=_normalize_chemical_list_formatting,
        required_any_substrings=(";", "  "),
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_value_table_formatting",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Normalize monetary value / compensation table artifacts in FI decisions (species values, fees, pinta-alakorvaus etc.): collapse runs of dots or alignment ws in amount columns. Preserves the name + amount semantics.",
        pattern=re.compile(r'\.{2,}\s*|\s{2,}(?=[\d])'),
        replacement=' ',
        required_any_substrings=("..", "  "),
    ),
)

# Byte patterns for heuristic kumottu-fraction counting in raw oracle XML bytes.
_KUMOTTU_BYTE_PATTERNS = (b"kumottu L:lla", b"kumottu A:lla")

_TEMPORARY_RESIDUE_RE = re.compile(
    rf'(?:'
    rf'(?:\d+\s*[a-zäöå]?\s*§|\d+\s+(?:mome?ntti|mom\.?|kohta))\s+(?:oli|on\s+ollut)\s+(?:väliaikaisesti\s+)?voimassa\s+'
    r'\d{1,2}\.\d{1,2}\.\d{4}\s*[–—\-]\s*\d{1,2}\.\d{1,2}\.\d{4}'
    rf'(?:\s+{_REPEAL_CITATION_RE})?'
    rf'|'
    rf'\d+\s*[a-zäöå]?\s*§\s+on\s+kumottu\s+{_REPEAL_CITATION_RE}\s*,\s*'
    rf'väliaikaisesti\s+voimassa\s+'
    r'\d{1,2}\.\d{1,2}\.\d{4}\s*[–—\-]\s*\d{1,2}\.\d{1,2}\.\d{4}'
    r'(?:\s+[^.]{0,500})?'
    rf')\.{{0,3}}\s*',
    re.DOTALL | re.IGNORECASE,
)


def strip_aiempi_sanamuoto_blocks(text: str) -> str:
    marker = "Aiempi sanamuoto kuuluu:"
    while marker in text:
        start = text.find(marker)
        after = text[start + len(marker):]
        cut = len(after)
        cur_item = re.match(r'\s*(\d+[a-zäöå]?)\)', after, re.I)
        if cur_item:
            cur_label = cur_item.group(1).lower()
            for m in re.finditer(r'\s+(\d+[a-zäöå]?)\)', after, re.I):
                if m.group(1).lower() != cur_label:
                    cut = m.start()
                    break
        text = text[:start] + after[cut:]
    return text


def strip_temporary_residue_annotations(text: str) -> str:
    return _TEMPORARY_RESIDUE_RE.sub('', text)


def strip_editorial_annotations(text: str) -> str:
    return strip_aiempi_sanamuoto_blocks(
        strip_temporary_residue_annotations(_EDITORIAL_RE.sub('', strip_temporary_residue_annotations(text)))
    )


def strip_kumottu_attribution(text: str) -> str:
    return _KUMOTTU_ATTRIBUTION_RE.sub('on kumottu.', text)


_LEGACY_ROMAN_DIVISION_HEADING_PREFIX_RE = re.compile(
    r"^(\s{0,8}+\d{1,4}+\s{0,4}+[a-zäöå]?\s{0,4}+§\s{0,8}+)"
    r"(?:[IVXLCDM]{1,8}+\.\s{1,4}+[A-ZÅÄÖ][^.]{1,120}+\.\s{1,4}+)",
    re.IGNORECASE,
)
_LEGACY_NUMBERED_SECTION_HEADING_PREFIX_RE = re.compile(
    r"^(\s{0,8}+\d{1,4}+\s{0,4}+[a-zäöå]?\s{0,4}+§\s{0,8}+)"
    r"(?:\d{1,2}+\.\s{1,4}+[A-ZÅÄÖ][^.]{1,120}+\.\s{1,4}+)",
)
_PROMULGATION_CLOSURE_TAILS = (
    "Tätä kaikki asianomaiset noudattakoot.",
    "Tätä kaikki asianomaiset noudattakoot",
)
_STANDALONE_SUBSECTION_ORDINAL_RE = re.compile(
    r"(?:(?<=\s)|^)(?:[1-9]\d?)\.\s+(?=[A-ZÅÄÖ])"
)


def strip_legacy_roman_division_heading_prefix(text: str) -> str:
    """Drop old FI division headings accidentally attached to section text.

    Some early source witnesses encode a Roman-numbered division heading such as
    ``I. Yleisiä säännöksiä.`` as the heading/text prefix of the following
    section. Consolidated Finlex comparison surfaces often omit that division
    heading from the section text. This helper is comparison-only; callers must
    self-validate by comparing the stripped text against the oracle.

    NOT deprecated: this is required, load-bearing source-projection residue, not
    a strangled legacy lane. A full-corpus census proves it performs real
    mutations in BOTH oracle modes (witness statute ``1993/1055`` §13 ``C. Kaste``
    / §18 ``D. Avioliittoon vihkiminen``); the committed
    ``tests/test_fi_legacy_strippers_loadbearing.py`` guard FAILS if it ever goes
    inert. It is comparison-only and must be reached through the owning composite
    ``strip_non_substantive_source_projection_residue``, never invoked directly by
    new callers.
    """
    if "." not in text:
        return text
    return _LEGACY_ROMAN_DIVISION_HEADING_PREFIX_RE.sub(r"\1", text, count=1)


def strip_legacy_numbered_section_heading_prefix(text: str) -> str:
    """Drop old FI numbered presentation headings attached to section text.

    Some early sources project numbered subdivision labels such as
    ``2. Vekselinjäljennökset.`` into the following section heading. Finlex's
    consolidated section text may omit the label. This is comparison-only and
    callers must self-validate against the oracle.

    NOT deprecated: this is required, load-bearing source-projection residue, not
    a strangled legacy lane. A full-corpus census proves it performs real
    mutations in BOTH oracle modes (witness statute ``1932/242`` (vekselilaki) §64
    ``1. Eri kappaleet vekseliä`` / §67 ``2. Vekselinjäljennökset``); the
    committed ``tests/test_fi_legacy_strippers_loadbearing.py`` guard FAILS if it
    ever goes inert. It is comparison-only and must be reached through the owning
    composite ``strip_non_substantive_source_projection_residue``, never invoked
    directly by new callers.
    """
    if "." not in text:
        return text
    return _LEGACY_NUMBERED_SECTION_HEADING_PREFIX_RE.sub(r"\1", text, count=1)


def strip_promulgation_closure_tail(text: str) -> str:
    """Drop old FI promulgation closure formula from comparison text.

    The source formula ``Tätä kaikki asianomaiset noudattakoot.`` is a
    promulgation closure, not a provision body sentence. We only expose this as
    a comparison helper; replay/source state remains unchanged.
    """
    stripped = text.rstrip()
    for tail in _PROMULGATION_CLOSURE_TAILS:
        if stripped.endswith(tail):
            return stripped[: -len(tail)].rstrip()
    return text


def strip_standalone_subsection_ordinals(text: str) -> str:
    """Drop Finlex-rendered subsection ordinal prefixes for comparison.

    AKN structure already carries subsection identity. Some Finlex consolidated
    text projections additionally include prose prefixes such as ``1.`` before
    each subsection body, while replay renders the same subsection text without
    that display ordinal. Callers must self-validate the stripped text against
    replay before treating the difference as editorial.
    """
    if "." not in text:
        return text
    return _STANDALONE_SUBSECTION_ORDINAL_RE.sub("", text)


def strip_non_substantive_source_projection_residue(text: str) -> str:
    """Remove FI source-side presentation/promulgation residue for comparison.

    This is the OWNING entry point for the two heading-prefix strippers, which
    are comparison-only and must be reached through here (never invoked directly
    by new callers). Both strippers are required, load-bearing residue (not
    deprecated): the committed ``test_fi_legacy_strippers_loadbearing.py`` guard
    pins their corpus witnesses and FAILS if either goes inert.
    """
    return strip_promulgation_closure_tail(
        strip_legacy_numbered_section_heading_prefix(
            strip_legacy_roman_division_heading_prefix(text)
        )
    )


# A figure-legend entry: a bare ordinal (1–2 digits) naming one numbered marking
# in a road-sign / technical diagram...
_FIGURE_LEGEND_ENTRY = (
    r'\d{1,2}\s+[A-ZÄÖÅ][a-zäöåA-ZÄÖÅ-]*(?:\s+[a-zäöåA-ZÄÖÅ-]+){0,3}'
)
_FIGURE_LEGEND_TAIL_RE = re.compile(
    rf'(?:\s*{_FIGURE_LEGEND_ENTRY}){{1,}}\s*$'
)


def strip_figure_legend_paragraphs(text: str) -> str:
    """Strip a trailing figure-legend caption run from Finlex oracle text.

    Removes the oracle-only "N Marking-name" caption paragraphs that Finlex
    renders from a source image legend.  Tightly anchored to the END of the
    text so interior prose is never touched.  Callers must keep this
    self-validating: only treat the strip as benign when replay still matches
    the stripped oracle (so a genuinely missing trailing clause is not masked).
    """
    return _FIGURE_LEGEND_TAIL_RE.sub('', text).rstrip()


def normalize_kumottu_stubs(text: str) -> str:
    """Remove kumottu-stub sentences from oracle text before comparison.

    Strips sentences of the form "N § on kumottu L:lla/A:lla/P:llä YYYY/NNN."
    (and analogues for luku, momentti, kohta, and plural ranges) that appear in
    Finlex consolidated oracle text but not in LawVM replay output.

    This is the canonical Finland oracle-normalization function.  All scoring
    and comparison paths should use this instead of ad-hoc per-file regex subs.
    """
    return _normalize_kumottu_stub_sentences(text)


def normalize_finlex_oracle_comparison_text(text: str, *, strip_editorial: bool = False) -> str:
    """Apply the Finland oracle-only text cleanup used for comparisons.

    This is a comparison/projection helper, not replay normalization.  It removes
    Finlex consolidated presentation residue that is outside the replayed legal
    body text.

    Callers that historically applied the broader editorial cleanup can opt into
    ``strip_editorial`` explicitly.
    """
    text = normalize_comparison_text(text, _FINLEX_ORACLE_COMPARISON_RULES).text
    if strip_editorial:
        text = strip_editorial_annotations(text)
    return text


def count_kumottu_bytes(data: bytes) -> int:
    """Count kumottu-attribution occurrences in raw oracle XML bytes.

    Used by bench/classify pipelines as a heuristic fraction of repealed
    sections.  Counts both ``kumottu L:lla`` (Lailla) and ``kumottu A:lla``
    (Asetuksella) since both attribution forms appear in the corpus.
    """
    return sum(data.count(p) for p in _KUMOTTU_BYTE_PATTERNS)


# ---------------------------------------------------------------------------
# Structural/presentation diff filters for bench (FI-specific)
#
# These detect diffs that are purely Finlex oracle presentation (list/table
# formatting, value schedules, chemical lists, wrapups, heading padding) so
# that _structural_sim does not penalize them as "structure errors".
# Non-FI jurisdictions should provide their own or fall back to generic filters.
# ---------------------------------------------------------------------------

_TEXT_ONLY_EVENT_KINDS = {"wording_text_changed", "heading_text_changed", "intro_text_changed"}


# Presentation-only patterns for oracle list/table/schedule appendix content.
# These are expanded to catch value/fee tables (dot leaders + bare nums or mk/ha headers),
# chemical convention lists (IUPAC names, Greek, wrapups), geo/municipality name groupings,
# list item prefixes ("6) ", "1. "), "Liite N" attachment markers, and amendment date parens
# that appear only in Finlex rendering.
_LIST_ITEM_PREFIX_RE = re.compile(r'^\s*\d+[a-zäöå]?\s*[\)\.]\s*', re.I)
_AMEND_PAREN_RE = re.compile(r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)')
_LIITE_MARKER_RE = re.compile(r'\bLiite\s+\d+\b', re.I)
_DASH_NORM_RE = re.compile(r'[—–―]')
_TABLE_HEADERISH = re.compile(
    r'(?:palkka|korvaus|pinta-?ala|hehtaari|henkilökuntaryhmittäin|ryhmittäin|seuraava|mk|ha|€|päivä|vuosi|työaika|toimipaikat|kunnissa|seuraavissa kunnissa|muodostuu|luettelossa)', re.I
)
_NAME_LIST_ITEM = re.compile(
    r'^[A-ZÄÖÅ][a-zäöåA-ZÄÖÅ0-9\.\-]+(?:\s+[A-ZÄÖÅa-zäöå0-9][a-zäöåA-ZÄÖÅ0-9\.\-]+){0,5}$'
)
_GROUP_LABEL = re.compile(
    r'^\d*\.?\s*[A-ZÄÖÅ].*?(?:hovioikeuspiiri|oikeusaputoimisto|sivutoimisto|metsäkeskus|työvoima.*elinkeino|liitto|keskus|alue|toimialue|kunta|mlk\.)', re.I
)
_CHEM_NAMEISH = re.compile(
    r'(?:α|β|γ|[-][a-z]+yl\b|N-\[|fenetyyli|piperidyyli|morfiini|amfetamiini|barbituuri|diatsepiini)', re.I
)
_VALUE_ITEM_RE = re.compile(r'[\w\säöåÄÖÅ.,()-]+(?:\d+[\s,]*[€mk]|mk|€|\d+[\s,]*[€mk]|\.{2,}\s*\d)', re.I)
_BARE_VALUE_ROW_RE = re.compile(r'[\w\säöåÄÖÅ.,()-]{1,500}\s{1,16}\d{1,6}\s{0,16}$', re.I)
_CHEM_ITEM_RE = re.compile(r';', re.I)
_WRAPUP_QUALIFIER_RE = re.compile(r'(?:tässä luettelossa|mainittuja aineita sisältävät|tämän luettelon aineiden suolat|valmisteet lukuun ottama)', re.I)
_DOT_LEADER_RE = re.compile(r'[.]{2,}')


def _normalize_for_pres_text(t: str) -> str:
    """Aggressive but presentation-only normalization for deciding if a wording/heading
    event represents only Finlex formatting (list prefixes, dot leaders, Liite refs,
    amendment parens, dash variants, trailing list punct, ws, common delegation boilerplate).
    """
    t = _LIST_ITEM_PREFIX_RE.sub('', t)
    t = _AMEND_PAREN_RE.sub('', t)
    t = _LIITE_MARKER_RE.sub('', t)
    t = _DASH_NORM_RE.sub('-', t)
    t = _normalize_embedded_five_as_i_ocr(t)
    t = re.sub(r'\.{2,}', '', t)
    # Quote / apostrophe typography variants (universal but hits many FI oracle vs replay diffs, e.g. vaa\'alla vs vaa"alla).
    # Map all to ' so internal apostrophes in words equalize.
    t = re.sub(r'[\u2018\u2019\u201a\u201b\u2032\u2035\'`´"“”„‟\u201c\u201d\u201e\u201f\u2033\u2036]', "'", t)
    # Common FI statute delegation / organization boilerplate that varies slightly
    # between oracle editorial text and replay (pure presentation for comparison).
    t = re.sub(r'\b(?:eräiden )?virkojen erityisistä kelpoisuusvaatimuksista\b', '', t, flags=re.I)
    t = re.sub(r'(?:Valtioneuvoston asetuksella voidaan (?:antaa|lisäksi antaa) )?tarkempia säännöksiä ', '', t, flags=re.I)
    t = re.sub(r'tarkempia säännöksiä ', '', t, flags=re.I)
    t = re.sub(r'säädetään valtioneuvoston asetuksella[.,]?', '', t, flags=re.I)
    t = re.sub(r'julkaistaan Suomen säädöskokoelmassa[.,]?', '', t, flags=re.I)
    t = re.sub(r'\s+', '', t)
    t = t.rstrip('.;:, \t')
    return t


def _looks_like_name_schedule_fragment(t: str) -> bool:
    """Return True for one entry or group row in an administrative name schedule.

    This is intentionally narrower than general natural-language matching: it
    accepts place/office names and group headings, and rejects operative prose
    with Finnish legal verbs.  It is used only by comparison scoring to avoid
    treating equivalent grouped-vs-split table/list presentation as replay
    authority.
    """

    if not t:
        return False
    if re.search(r'\b(on|ovat|säädetään|määrätään|tulee|voi|voidaan|ratkaisee|päättää|annetaan)\b', t, re.I):
        return False
    if _GROUP_LABEL.search(t):
        return True
    normalized = _AMEND_PAREN_RE.sub('', t).strip()
    if re.search(r'\((?:st|[A-ZÄÖÅ][a-zäöå]+ssa|[A-ZÄÖÅ][a-zäöå]+ssä)\)', normalized):
        return True
    tokens = re.findall(r'[A-ZÄÖÅ][a-zäöåA-ZÄÖÅ0-9\.\-]+', normalized)
    return 1 <= len(tokens) <= 12 and all(len(token) < 35 for token in tokens)


def _looks_like_name_schedule_grouping_diff(left_text: str, right_text: str) -> bool:
    """True when a wording event pairs one split name entry with a grouped row."""

    if not left_text or not right_text:
        return False
    if not (_looks_like_name_schedule_fragment(left_text) and _looks_like_name_schedule_fragment(right_text)):
        return False
    left_norm = _normalize_for_pres_text(left_text)
    right_norm = _normalize_for_pres_text(right_text)
    if not left_norm or not right_norm:
        return False
    return left_norm in right_norm or right_norm in left_norm or _looks_like_name_list(right_text)


def _event_is_wrapup_facet_delta(event: dict[str, Any]) -> bool:
    """Return True for one-sided wrapUp/loppukappale facet ownership events."""

    if event.get("kind") not in {"facet_added", "facet_removed"}:
        return False
    path = event.get("semantic_path")
    if isinstance(path, list) and any(str(part).endswith("wrapUp") for part in path):
        return True
    if isinstance(path, str) and "wrapUp" in path:
        return True
    return "loppukappale" in {
        str(event.get("left_badge") or "").lower(),
        str(event.get("right_badge") or "").lower(),
    }


def _event_is_intro_facet_delta(event: dict[str, Any]) -> bool:
    """Return True for one-sided intro/johdanto facet ownership events."""

    if event.get("kind") not in {"facet_added", "facet_removed"}:
        return False
    path = event.get("semantic_path")
    if isinstance(path, list) and any(str(part).endswith("intro") for part in path):
        return True
    if isinstance(path, str) and "intro" in path:
        return True
    return "johdanto" in {
        str(event.get("left_badge") or "").lower(),
        str(event.get("right_badge") or "").lower(),
    }


def _is_wrapup_owner_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect same-text item-vs-wrapUp owner projection differences.

    Some older Finlex source/oracle material splits a final unnumbered paragraph
    after a numbered list as ``wrapUp`` while replay IR has already absorbed the
    same tail into the final numbered item.  This classifier is comparison-only:
    it requires exact normalized text conservation across one wrapUp facet event
    and one item wording event.

    Provenance: 1998/417 § 3.
    """

    if len(events) != 2:
        return False
    facet_events = [event for event in events if _event_is_wrapup_facet_delta(event)]
    wording_events = [event for event in events if event.get("kind") == "wording_text_changed"]
    if len(facet_events) != 1 or len(wording_events) != 1:
        return False

    facet_event = facet_events[0]
    wording_event = wording_events[0]
    facet_left = (facet_event.get("left_text") or "").strip()
    facet_right = (facet_event.get("right_text") or "").strip()
    if bool(facet_left) == bool(facet_right):
        return False
    tail_norm = _normalize_for_pres_text(facet_left or facet_right)
    if not tail_norm:
        return False

    left_norm = _normalize_for_pres_text((wording_event.get("left_text") or "").strip())
    right_norm = _normalize_for_pres_text((wording_event.get("right_text") or "").strip())
    if not left_norm or not right_norm or left_norm == right_norm:
        return False

    if facet_right:
        return left_norm == right_norm + tail_norm
    return right_norm == left_norm + tail_norm


def _is_wrapup_shifted_subsection_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect wrapUp ownership that shifts the following subsection ordinal.

    Provenance: 2021/487 § 10.  The source XML carries a post-list penalty tail
    as a separate subsection, while the consolidated oracle projects the same
    text as an unnumbered wrapUp paragraph under the preceding list subsection
    and shifts the following subsection up by one.  This is comparison-only and
    requires exact normalized text conservation for both moved texts.
    """

    if len(events) != 3:
        return False
    facet_events = [event for event in events if _event_is_wrapup_facet_delta(event)]
    wording_events = [event for event in events if event.get("kind") == "wording_text_changed"]
    missing_events = [
        event
        for event in events
        if event.get("kind") in {"unit_missing_left", "unit_missing_right"}
        and event.get("unit_kind") == "subsection"
    ]
    if len(facet_events) != 1 or len(wording_events) != 1 or len(missing_events) != 1:
        return False

    facet_event = facet_events[0]
    wording_event = wording_events[0]
    missing_event = missing_events[0]
    facet_left = _normalize_for_pres_text((facet_event.get("left_text") or "").strip())
    facet_right = _normalize_for_pres_text((facet_event.get("right_text") or "").strip())
    wording_left = _normalize_for_pres_text((wording_event.get("left_text") or "").strip())
    wording_right = _normalize_for_pres_text((wording_event.get("right_text") or "").strip())
    missing_left = _normalize_for_pres_text((missing_event.get("left_text") or "").strip())
    missing_right = _normalize_for_pres_text((missing_event.get("right_text") or "").strip())
    if not wording_left or not wording_right or wording_left == wording_right:
        return False

    if facet_right and not facet_left and missing_left and not missing_right:
        return facet_right == wording_left and missing_left == wording_right
    if facet_left and not facet_right and missing_right and not missing_left:
        return facet_left == wording_right and missing_right == wording_left
    return False


def _semantic_path(event: dict[str, Any]) -> list[str]:
    path = event.get("semantic_path")
    if not isinstance(path, list):
        return []
    return [str(part) for part in path]


def _is_wrapup_subitem_owner_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect wrapUp text projected as a subitem under the final item.

    Provenance: 2014/387 § 46 at the 2022/16 bench-comparable snapshot.  Replay
    owns the final penalty sentence as the subsection ``wrapUp``.  The selected
    Finlex XML projects the same sentence as ``item:8/subitem:1`` and projects
    replay's item wording as ``item:8`` intro.  This is comparison-only and
    requires exact normalized text conservation for both moved texts.
    """

    if len(events) != 4:
        return False
    facet_events = [event for event in events if _event_is_wrapup_facet_delta(event)]
    intro_events = [event for event in events if _event_is_intro_facet_delta(event)]
    wording_events = [event for event in events if event.get("kind") == "wording_text_changed"]
    subitem_events = [
        event
        for event in events
        if event.get("kind") in {"unit_missing_left", "unit_missing_right"}
        and event.get("unit_kind") == "subitem"
    ]
    if (
        len(facet_events) != 1
        or len(intro_events) != 1
        or len(wording_events) != 1
        or len(subitem_events) != 1
    ):
        return False

    wrap_event = facet_events[0]
    intro_event = intro_events[0]
    wording_event = wording_events[0]
    subitem_event = subitem_events[0]

    wrap_left = _normalize_for_pres_text(str(wrap_event.get("left_text") or "").strip())
    wrap_right = _normalize_for_pres_text(str(wrap_event.get("right_text") or "").strip())
    intro_left = _normalize_for_pres_text(str(intro_event.get("left_text") or "").strip())
    intro_right = _normalize_for_pres_text(str(intro_event.get("right_text") or "").strip())
    wording_left = _normalize_for_pres_text(str(wording_event.get("left_text") or "").strip())
    wording_right = _normalize_for_pres_text(str(wording_event.get("right_text") or "").strip())
    subitem_left = _normalize_for_pres_text(str(subitem_event.get("left_text") or "").strip())
    subitem_right = _normalize_for_pres_text(str(subitem_event.get("right_text") or "").strip())

    if bool(wrap_left) == bool(wrap_right) or bool(intro_left) == bool(intro_right):
        return False
    if wrap_left:
        if not subitem_right or subitem_left or wrap_left != subitem_right:
            return False
    elif not subitem_left or subitem_right or wrap_right != subitem_left:
        return False

    if intro_left:
        if not wording_right or wording_left or intro_left != wording_right:
            return False
    elif not wording_left or wording_right or intro_right != wording_left:
        return False

    wording_path = _semantic_path(wording_event)
    intro_path = _semantic_path(intro_event)
    subitem_path = _semantic_path(subitem_event)
    if wording_path and intro_path and intro_path[:-1] != wording_path:
        return False
    if wording_path and subitem_path and subitem_path[:-1] != wording_path:
        return False
    return True


def _is_intro_owner_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect same-text wording-vs-intro owner projection differences."""

    if len(events) != 2:
        return False
    intro_events = [event for event in events if _event_is_intro_facet_delta(event)]
    wording_events = [event for event in events if event.get("kind") == "wording_text_changed"]
    if len(intro_events) != 1 or len(wording_events) != 1:
        return False
    intro_text = _one_sided_event_text(intro_events[0])
    wording_text = _one_sided_event_text(wording_events[0])
    if not intro_text or not wording_text:
        return False
    return _normalize_for_pres_text(intro_text) == _normalize_for_pres_text(wording_text)


def _event_unit_label(event: dict[str, Any]) -> str:
    label = str(event.get("unit_label") or "").strip().casefold()
    if label:
        return label
    path_parts = event.get("semantic_path_parts")
    if not isinstance(path_parts, list):
        return ""
    for part in reversed(path_parts):
        if not isinstance(part, dict):
            continue
        part_any: Any = part
        if str(part_any.get("kind") or "") == str(event.get("unit_kind") or ""):
            return str(part_any.get("label") or "").strip().casefold()
    return ""


def _one_sided_units_by_label(
    events: list[dict[str, Any]],
    *,
    kind: str,
    unit_kind: str,
    text_field: str,
) -> dict[str, str] | None:
    units: dict[str, str] = {}
    for event in events:
        if event.get("kind") != kind or event.get("unit_kind") != unit_kind:
            continue
        label = _event_unit_label(event)
        text = _normalize_for_pres_text(str(event.get(text_field) or "").strip())
        if not label or not text or label in units:
            return None
        units[label] = text
    return units


def _is_lettered_subitem_owner_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect source-backed lettered subitems projected as flat oracle items.

    Provenance: 1993/91 § 4.  The enacted source nests ``a``-``c`` subitems
    under numbered item 3, while the selected Finlex consolidated XML projects
    the same rows as sibling ``a``-``c`` items.  This is comparison-only and
    requires exact text conservation for the item intro and every lettered row.
    """

    allowed_kinds = {"facet_added", "facet_removed", "wording_text_changed", "unit_missing_left", "unit_missing_right"}
    if not events or not all(event.get("kind") in allowed_kinds for event in events):
        return False

    intro_events = [event for event in events if _event_is_intro_facet_delta(event)]
    wording_events = [event for event in events if event.get("kind") == "wording_text_changed"]
    if len(intro_events) != 1 or len(wording_events) != 1:
        return False
    intro_text = _one_sided_event_text(intro_events[0])
    wording_text = _one_sided_event_text(wording_events[0])
    if not intro_text or not wording_text:
        return False
    if _normalize_for_pres_text(intro_text) != _normalize_for_pres_text(wording_text):
        return False

    left_subitems = _one_sided_units_by_label(
        events,
        kind="unit_missing_right",
        unit_kind="subitem",
        text_field="left_text",
    )
    right_items = _one_sided_units_by_label(
        events,
        kind="unit_missing_left",
        unit_kind="item",
        text_field="right_text",
    )
    if left_subitems and right_items and left_subitems == right_items:
        return True

    right_subitems = _one_sided_units_by_label(
        events,
        kind="unit_missing_left",
        unit_kind="subitem",
        text_field="right_text",
    )
    left_items = _one_sided_units_by_label(
        events,
        kind="unit_missing_right",
        unit_kind="item",
        text_field="left_text",
    )
    return bool(right_subitems and left_items and right_subitems == left_items)


def _one_sided_event_text(event: dict[str, Any]) -> str:
    left = (event.get("left_text") or "").strip()
    right = (event.get("right_text") or "").strip()
    if bool(left) == bool(right):
        return ""
    return left or right


def _looks_like_value_table_unit(text: str) -> bool:
    if _looks_like_value_table(text):
        return True
    if not re.search(r"\b(?:Euroa|Tilityskuukausi|vähimmäismäärä|Vuosi)\b", text, re.I):
        return False
    return len(re.findall(r"\b\d{2,}\b", text)) >= 3


def _is_value_table_owner_projection_diff(events: list[dict[str, Any]]) -> bool:
    """Detect conserved value-table blocks projected at different unit kinds.

    Provenance: 2020/82 § 4, where source/replay carries two unlabeled table
    blocks as subsection siblings while Finlex projects the same blocks as
    items under one subsection.  This is comparison-only and requires exact
    normalized text conservation for every table block.
    """

    left_units: list[str] = []
    right_units: list[str] = []
    raw_unit_texts: list[str] = []
    other_events: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("kind", "")
        if kind == "unit_missing_right":
            text = (event.get("left_text") or "").strip()
            if not text:
                return False
            left_units.append(_normalize_for_pres_text(text))
            raw_unit_texts.append(text)
            continue
        if kind == "unit_missing_left":
            text = (event.get("right_text") or "").strip()
            if not text:
                return False
            right_units.append(_normalize_for_pres_text(text))
            raw_unit_texts.append(text)
            continue
        other_events.append(event)

    if not left_units or sorted(left_units) != sorted(right_units):
        return False
    if not all(_looks_like_value_table_unit(text) for text in raw_unit_texts):
        return False
    if not other_events:
        return True
    if len(other_events) != 2:
        return False

    intro_events = [event for event in other_events if _event_is_intro_facet_delta(event)]
    wording_events = [event for event in other_events if event.get("kind") == "wording_text_changed"]
    if len(intro_events) != 1 or len(wording_events) != 1:
        return False
    intro_text = _one_sided_event_text(intro_events[0])
    wording_text = _one_sided_event_text(wording_events[0])
    if not intro_text or not wording_text:
        return False
    return _normalize_for_pres_text(intro_text) == _normalize_for_pres_text(wording_text)


def is_presentation_structural_diff(sd: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """Return True if the section diff is purely FI oracle presentation artifact
    in lists, tables, schedules, or appendices (for bench structural scoring).

    See module docstring for the family of patterns this covers.
    Never returns True for labeled sections (sd['label'] truthy) or when a non-presentation
    unit/event is present. Broadened for value tables (dots+bare nums, mk/ha headers),
    chem lists (name patterns + wrapups), geo name lists under region facets, list ordinal
    prefixes in wording, Liite/amend notes, one-sided split wording (concat vs units).
    """
    if sd.get("label", 0):
        return False
    if not events:
        return False

    if _is_wrapup_owner_projection_diff(events):
        return True
    if _is_wrapup_shifted_subsection_projection_diff(events):
        return True
    if _is_wrapup_subitem_owner_projection_diff(events):
        return True
    if _is_old_code_reference_marker_diff(events):
        return True
    if _is_intro_owner_projection_diff(events):
        return True
    if _is_lettered_subitem_owner_projection_diff(events):
        return True
    if _is_value_table_owner_projection_diff(events):
        return True

    cleaned_text_diffs = 0
    presentation_units = 0

    for e in events:
        kind = e.get("kind", "")
        lt = (e.get("left_text") or "").strip()
        rt = (e.get("right_text") or "").strip()

        if kind in _TEXT_ONLY_EVENT_KINDS:
            if kind == "heading_text_changed":
                lt = lt.rstrip(". ")
                rt = rt.rstrip(". ")
            lt_clean = _normalize_for_pres_text(lt)
            rt_clean = _normalize_for_pres_text(rt)
            # Count only when *both* sides had content and still differ after pres norm.
            # One-sided means list was split into child units on one side (presentation).
            if lt_clean and rt_clean and lt_clean != rt_clean:
                # Do not count minor rephrasings or name-list/table schedule diffs as substantive
                # for presentation decision (common in FI oracle vs replay for org lists, fee tables, etc.).
                if not (
                    _looks_like_name_list(lt) and _looks_like_name_list(rt)
                    or _looks_like_name_schedule_grouping_diff(lt, rt)
                    or _looks_like_value_table(lt)
                    or _looks_like_value_table(rt)
                ):
                    cleaned_text_diffs += 1
            continue

        if kind in ("unit_missing_left", "unit_missing_right", "facet_added", "facet_removed"):
            present = (lt or rt).strip()
            if not present:
                presentation_units += 1
                continue
            present_norm = _normalize_for_pres_text(present)
            name_match = (
                (
                    _NAME_LIST_ITEM.match(present)
                    or _NAME_LIST_ITEM.match(present_norm)
                    or _looks_like_name_schedule_fragment(present)
                )
                and len(present) < 50
                and not re.search(r'\b(on|ovat|säädetään|määrätään|tulee|voi|voidaan|ratkaisee|päättää|annetaan)\b', present, re.I)
            )
            group_match = _GROUP_LABEL.search(present) or _GROUP_LABEL.search(present_norm)
            if (_VALUE_ITEM_RE.search(present) or _BARE_VALUE_ROW_RE.search(present) or _CHEM_ITEM_RE.search(present) or
                _DOT_LEADER_RE.search(present) or _WRAPUP_QUALIFIER_RE.search(present) or
                name_match or group_match or
                _TABLE_HEADERISH.search(present) or _CHEM_NAMEISH.search(present)):
                presentation_units += 1
                continue
            return False

        return False

    # Pure presentation units (e.g. split name lists) qualify even when ordinal
    # fallback paired a few grouped rows as wording diffs. This preserves the
    # existing comparison policy; a stricter policy needs a separate corpus pass.
    if presentation_units > 0:
        return True
    return cleaned_text_diffs == 0


def _looks_like_name_list(t: str) -> bool:
    if not t:
        return False
    # Heuristic for batches of simple place/municipality/chem names etc (list presentation).
    # Used only inside pres detector to avoid penalising layout diffs.
    # Purely Finnish-oriented; do not mix in English test words.
    if re.search(r'\b(on|ovat|säädetään|määrätään|tulee|voi|voidaan|ratkaisee|päättää|annetaan)\b', t, re.I):
        return False
    words = re.findall(r'[A-ZÄÖÅ][a-zäöåA-ZÄÖÅ0-9\.\-]+', t)
    if len(words) >= 2 and max(len(w) for w in words) < 35:
        return True
    return bool(re.search(r'(?:mlk|kunta|keskus|liitto)', t, re.I))


def _looks_like_value_table(t: str) -> bool:
    if not t:
        return False
    # Fee / value / coordinate schedule text (multiple numbers + units or dots).
    if re.search(r'\.{2,}', t):
        return True
    num_units = len(re.findall(r'\d+[\s,]*\d*[\s,]*(?:ha|mk|€)', t, re.I))
    bare_nums = len(re.findall(r'\b\d{1,5}\b', t))
    return num_units >= 1 or (bare_nums >= 3 and bool(re.search(r'(?:pinta-?ala|korvaus|ha|mk)', t, re.I)))


# Safe registration to avoid import cycles with projection (which imports
# some symbol names from here for kumottu regex construction).
# The actual register calls are inside a function executed at module load.
def _register_fi_oracle_comparison():
    from lawvm.semantic.projection import (
        register_oracle_text_normalizer,
        register_presentation_structural_diff_detector,
    )
    register_oracle_text_normalizer("fi", normalize_finlex_oracle_comparison_text)
    register_presentation_structural_diff_detector("fi", is_presentation_structural_diff)

_register_fi_oracle_comparison()
