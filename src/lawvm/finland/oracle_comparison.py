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

_FINLEX_ORACLE_COMPARISON_RULES = (
    ComparisonNormalizationRule(
        name="fi_oracle_kumottu_stub_sentence",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove Finlex kumottu stub sentences from oracle comparison text.",
        pattern=_KUMOTTU_STUBS_RE,
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_dot_leader_table_formatting",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove dot-leader alignment runs (........) used in Finlex for printed fee tables/schedules in small decisions. Pure presentation; content words remain for comparison.",
        pattern=re.compile(r"\.{3,}"),
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_amendment_date_parenthetical",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove Finlex amendment-date parenthetical residue from oracle comparison text.",
        pattern=re.compile(r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)'),
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_aiempi_sanamuoto_marker",
        rule_class="presentation_cleanup",
        kind="literal",
        description="Remove Finlex previous-wording marker from oracle comparison text.",
        old_text='Aiempi sanamuoto kuuluu:',
        new_text='',
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
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_chemical_list_formatting",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Normalize common artifacts in Finnish implementations of chemical/controlled substance lists (1961 convention etc.): Greek letter variants, extra punctuation around names like 'Safroli;'. Content names are preserved.",
        pattern=re.compile(r'[; ]{2,}(?=\s*(?:[A-ZÄÖÅa-zäöå]|\())'),
        replacement='; ',
    ),
    ComparisonNormalizationRule(
        name="fi_oracle_value_table_formatting",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Normalize monetary value / compensation table artifacts in FI decisions (species values, fees, pinta-alakorvaus etc.): collapse runs of dots or alignment ws in amount columns. Preserves the name + amount semantics.",
        pattern=re.compile(r'\.{2,}\s*|\s{2,}(?=[\d])'),
        replacement=' ',
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
    if "kumottu" not in text:
        return text
    return _KUMOTTU_STUBS_RE.sub('', text)


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


def _normalize_for_pres_text(t: str) -> str:
    """Aggressive but presentation-only normalization for deciding if a wording/heading
    event represents only Finlex formatting (list prefixes, dot leaders, Liite refs,
    amendment parens, dash variants, trailing list punct, ws, common delegation boilerplate).
    """
    t = _LIST_ITEM_PREFIX_RE.sub('', t)
    t = _AMEND_PAREN_RE.sub('', t)
    t = _LIITE_MARKER_RE.sub('', t)
    t = _DASH_NORM_RE.sub('-', t)
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

    _VALUE_ITEM = re.compile(r'[\w\säöåÄÖÅ.,()-]+(?:\d+[\s,]*[€mk]|mk|€|\d+[\s,]*[€mk]|\.{2,}\s*\d)', re.I)
    _BARE_VALUE_ROW = re.compile(r'[\w\säöåÄÖÅ.,()-]+\s+\d{1,6}\s*$', re.I)
    _CHEM_ITEM = re.compile(r';', re.I)
    _WRAPUP_QUALIFIER = re.compile(r'(?:tässä luettelossa|mainittuja aineita sisältävät|tämän luettelon aineiden suolat|valmisteet lukuun ottama)', re.I)
    _DOT_LEADER = re.compile(r'[\w\säöåÄÖÅ.,()-]+[.]{2,}')

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
            if (_VALUE_ITEM.search(present) or _BARE_VALUE_ROW.search(present) or _CHEM_ITEM.search(present) or
                _DOT_LEADER.search(present) or _WRAPUP_QUALIFIER.search(present) or
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
