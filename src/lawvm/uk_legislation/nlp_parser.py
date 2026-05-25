import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Dict

from lawvm.uk_legislation.source_text_normalization import normalize_uk_parser_text

# ASCII Unit Separator
US = "\x1f"

_ORDINAL_OCCURRENCES = {
    "first": "1",
    "firstly": "1",
    "1st": "1",
    "second": "2",
    "secondly": "2",
    "2nd": "2",
    "third": "3",
    "thirdly": "3",
    "3rd": "3",
    "fourth": "4",
    "fourthly": "4",
    "4th": "4",
    "fifth": "5",
    "fifthly": "5",
    "5th": "5",
}

_ORDINAL_OCCURRENCE_WORDS = (
    r"first|firstly|1st|second|secondly|2nd|third|thirdly|3rd|"
    r"fourth|fourthly|4th|fifth|fifthly|5th"
)

_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019"
_COMPOUND_LETTERED_TEXT_PATCH_RULE_ID = (
    "uk_effect_compound_lettered_text_patch_instruction"
)
UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID = (
    "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch"
)
UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID = (
    "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch"
)
UK_BOTH_SUBSEQUENT_OCCURRENCES_SUBSTITUTION_RULE_ID = (
    "uk_effect_both_subsequent_occurrences_substitution_text_patch"
)


def _normalize_quotes(text: str) -> str:
    return (text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")).strip()


def _strip_inserted_child_label(text: str) -> str:
    """Remove an explicit inserted child label while preserving single-letter words."""
    m = re.match(r"^\(?([A-Za-z]{2,}|\d+[A-Za-z]?)\)?\s+(.+)$", text.strip())
    if not m:
        return text.strip()
    return m.group(2).strip()


def _quoted_terms(text: str) -> list[str]:
    terms: list[str] = []
    for m in re.finditer(r"“([^”]*)”|\"([^\"]*)\"|‘([^’]*)’|'([^']*)'", text):
        term = next((group for group in m.groups() if group is not None), "")
        if term.strip():
            terms.append(term.strip())
    return terms


def _last_quoted_term(text: str) -> str | None:
    terms = _quoted_terms(text)
    if not terms:
        return None
    return terms[-1]


def _ordinal_occurrences_from_phrase(text: str) -> tuple[str, ...]:
    occurrences: list[str] = []
    for part in re.split(r"\s*(?:,|and)\s*", text.strip(), flags=re.I):
        ordinal = _ORDINAL_OCCURRENCES.get(part.lower())
        if ordinal is None or ordinal in occurrences:
            continue
        occurrences.append(ordinal)
    return tuple(occurrences)


def _span_overlaps(span: tuple[int, int], blocked_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < blocked_end and blocked_start < end for blocked_start, blocked_end in blocked_spans)


def _has_respectively_all_occurrences_signal(text: str) -> bool:
    return bool(
        re.search(
            r"\bwherever\b|\bin\s+(?:each|both)\s+places?\b",
            text,
            re.I,
        )
    )


def _looks_like_definition_entry_payload(text: str) -> bool:
    return bool(
        re.search(
            r"[“\"'‘].+?[”\"'’](?:\s*\([^;]*?\))*[^;]{0,240}?"
            r"\b(?:means|includes|has\s+the\s+(?:same\s+)?meaning|is\s+to\s+be\s+construed|shall\s+be\s+construed)\b",
            text,
            re.I | re.S,
        )
    )


def _strip_optional_child_label(text: str, label: str) -> str:
    cleaned = text.strip()
    label_pattern = re.escape(label.strip())
    if not label_pattern:
        return cleaned
    m = re.match(rf"^\(?{label_pattern}\)?\s+(.+)$", cleaned, flags=re.I)
    if not m:
        return cleaned
    return m.group(1).strip()


def _deduplicate_fragment_substitutions(subs: list[Dict[str, str]]) -> list[Dict[str, str]]:
    definition_child_labels = set()
    for sub in subs:
        orig = sub.get("original") or ""
        if orig.startswith("TEXT_DEFINITION_CHILD_PARAGRAPH_"):
            parts = orig.split(US)
            if len(parts) >= 2:
                lbl = parts[-1].strip(" \x1f")
                definition_child_labels.add(lbl)
    filtered = []
    for sub in subs:
        orig = sub.get("original") or ""
        if orig.startswith("TEXT_OMIT_PARAGRAPH_"):
            lbl = orig[len("TEXT_OMIT_PARAGRAPH_") :].strip()
            if lbl in definition_child_labels:
                continue
        filtered.append(sub)

    deduped: list[Dict[str, str]] = []
    by_key: dict[tuple[str, str, str], int] = {}
    for sub in filtered:
        key = (
            str(sub.get("original") or ""),
            str(sub.get("replacement") or ""),
            str(sub.get("occurrence") or ""),
        )
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(deduped)
            deduped.append(sub)
            continue
        if "rule_id" in sub and "rule_id" not in deduped[existing_index]:
            deduped[existing_index] = sub
    return deduped


def _compound_lettered_text_patch_source(text: str) -> bool:
    """Return True for one paragraph carrying lettered sibling text patches."""
    return bool(
        re.search(
            r"[—-]\s*[a-z]\s+\bfor\b.+?\band\s+[a-z]\s+\bafter\b",
            text,
            re.I,
        )
    )


def _mark_compound_lettered_text_patches(
    text: str,
    subs: list[Dict[str, str]],
) -> list[Dict[str, str]]:
    if not subs or not _compound_lettered_text_patch_source(text):
        return subs
    marked: list[Dict[str, str]] = []
    for sub in subs:
        original = str(sub.get("original") or "")
        rule_id = str(sub.get("rule_id") or "")
        if (
            rule_id == "uk_effect_for_there_is_inserted_replacement_text_patch"
            and any(quote in original for quote in _QUOTE_CHARS)
        ):
            continue
        if rule_id in {"", "uk_effect_after_quoted_anchor_insert_text_patch"}:
            marked.append(
                {
                    **sub,
                    "rule_id": _COMPOUND_LETTERED_TEXT_PATCH_RULE_ID,
                }
            )
            continue
        marked.append(sub)
    return marked


@dataclass
class UKLegalRef:
    kind: str # 'section', 'subsection', 'paragraph', 'item'
    label: str # '1', '(a)', '(i)'

@dataclass
class UKAmendmentIntent:
    operation: str # 'substitution', 'omission', 'insertion'
    scope: List[UKLegalRef]
    targets: List[str] # literal strings or ranges like FROM_X_TO_Y

def parse_fragment_substitution(text: str) -> List[Dict[str, str]]:
    """Parse source-carried fragment substitutions.

    The parser is pure but callers may mutate returned fragment dictionaries
    while adding replay-specific context. Cache only immutable parse facts and
    return fresh dictionaries on every public call.
    """
    return [dict(items) for items in _parse_fragment_substitution_cached(text)]


@lru_cache(maxsize=8192)
def _parse_fragment_substitution_cached(text: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    """
    NLP-enhanced fragment extraction. Returns a list of substitution dicts.
    'for "the Lord Chancellor" substitute "the Secretary of State"'
    'from "(a)" to "(b)" are omitted'
    """
    subs = []

    text = normalize_uk_parser_text(text)
    respectively_spans: list[tuple[int, int]] = []

    matches_nested_quote_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](?P<original>.+)[”\"'’],?\s+"
        r"substitute\s+[“\"'‘](?P<replacement>.+)[”\"'’]\s*;?$",
        text,
        re.I,
    )
    for m in matches_nested_quote_substituted:
        original = m.group("original").strip()
        replacement = m.group("replacement").strip()
        if any(q in original + replacement for q in ("“", "”", '"', "‘", "’", "'")) and not re.search(
            r"\b(?:in both places where|wherever)\b",
            original,
            re.I,
        ):
            subs.append(
                {
                    "original": original,
                    "replacement": replacement,
                    "rule_id": "uk_effect_nested_quote_substitution_text_patch",
                }
            )

    matches_quoted_anchor_block_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"substitute\s*[—-]?\s+(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_quoted_anchor_block_substituted:
        replacement = m.group("replacement").strip()
        if replacement and not replacement.startswith(("“", '"', "'", "‘")):
            subs.append(
                {
                    "original": m.group("original").strip(),
                    "replacement": re.sub(r"\s+\.$", "", replacement).strip(),
                    "rule_id": "uk_effect_quoted_anchor_block_substitution_text_patch",
                }
            )

    matches_child_qualified_quoted_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"in\s+(?P<child_kind>paragraph|sub-paragraph|subsection|section)\s+"
        r"\(?(?P<child_label>[0-9A-Za-z]+)\)?\s+"
        r"substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_child_qualified_quoted_substituted:
        subs.append(
            {
                "original": m.group("original").strip(),
                "replacement": m.group("replacement").strip(),
                "source_child_kind": m.group("child_kind").strip().lower(),
                "source_child_label": m.group("child_label").strip(),
                "rule_id": "uk_effect_child_qualified_quoted_substitution_text_patch",
            }
        )

    matches_respectively_all_occurrences_substituted = re.finditer(
        r"[“\"](?P<original_1>.*?)[”\"]\s+and\s+"
        r"[“\"](?P<original_2>.*?)[”\"],?\s+"
        r"wherever\s+(?:these\s+expressions|they|those\s+words?)\s+"
        r"(?:occur|occurs|appear|appears),?\s+"
        r"become,?\s+respectively,?\s+"
        r"[“\"](?P<replacement_1>.*?)[”\"]\s+and\s+"
        r"[“\"](?P<replacement_2>.*?)[”\"]",
        text,
        re.I,
    )
    for m in matches_respectively_all_occurrences_substituted:
        for original_name, replacement_name in (
            ("original_1", "replacement_1"),
            ("original_2", "replacement_2"),
        ):
            subs.append(
                {
                    "original": m.group(original_name).strip(),
                    "replacement": m.group(replacement_name).strip(),
                    "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
                }
            )

    matches_respectively_there_is_substituted = re.finditer(
        r"for\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original_1>.*?)[”\"'’]\s+and\s+"
        r"[“\"'‘](?P<original_2>.*?)[”\"'’],?\s+"
        r"wherever\s+(?:occurring|(?:these\s+expressions|they|those\s+words?)\s+"
        r"(?:occur|occurs|appear|appears)),?\s+"
        r"there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"[“\"'‘](?P<replacement_1>.*?)[”\"'’]\s+and\s+"
        r"[“\"'‘](?P<replacement_2>.*?)[”\"'’]\s+respectively\b",
        text,
        re.I,
    )
    for m in matches_respectively_there_is_substituted:
        for original_name, replacement_name in (
            ("original_1", "replacement_1"),
            ("original_2", "replacement_2"),
        ):
            subs.append(
                {
                    "original": m.group(original_name).strip(),
                    "replacement": m.group(replacement_name).strip(),
                    "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
                }
            )
        respectively_spans.append(m.span())

    matches_respectively_series_there_is_substituted = re.finditer(
        r"\bfor\s+(?P<originals>.+?)\s+"
        r"there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"(?P<replacements>.+?)\s+respectively\b",
        text,
        re.I,
    )
    for m in matches_respectively_series_there_is_substituted:
        originals_text = m.group("originals")
        replacements_text = m.group("replacements")
        originals = _quoted_terms(originals_text)
        replacements = _quoted_terms(replacements_text)
        if (
            len(originals) < 2
            or len(originals) != len(replacements)
            or not _has_respectively_all_occurrences_signal(originals_text)
        ):
            continue
        for original, replacement in zip(originals, replacements):
            subs.append(
                {
                    "original": original,
                    "replacement": replacement,
                    "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
                }
            )
        respectively_spans.append(m.span())

    # Pattern 1: Substitution (Multiple possible)
    # Use non-greedy match for the fragments.
    # Allow an optional comma (and whitespace) between the quoted original and “substitute”,
    # which is the standard Scottish/UK drafting style: for “X”, substitute “Y”
    matches = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s*(?:(?:(?:in both places where|wherever) it (?:occurs|appears))[”\"'’]?,?\s*)?substitute [“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    matches_wherever_occurring_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"wherever\s+occurring,?\s+substitute\s+[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_wherever_occurring_substituted:
        subs.append(
            {
                "original": m.group(1),
                "replacement": m.group(2),
                "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
            }
        )

    matches_wherever_occurring_passive_substituted = re.finditer(
        r"for\s+(?:(?:the\s+)?words?\s+)?(?P<originals>.+?),?\s+"
        r"wherever\s+occurring,?\s+there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"[“”\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_wherever_occurring_passive_substituted:
        if _span_overlaps(m.span(), respectively_spans):
            continue
        replacement = m.group("replacement").strip()
        for original in _quoted_terms(m.group("originals")):
            subs.append(
                {
                    "original": original,
                    "replacement": replacement,
                    "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
                }
            )

    matches_all_occurrences_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"(?:\(\s*)?in (?:each|both) places?"
        r"(?:\s+(?:where\s+)?(?:(?:it|they|those words?)\s+)?"
        r"(?:occurs?|occurring|appears?|appear)(?:\s+in\s+[^,;]+)?)?"
        r"(?:\s*\))?,?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+(?:(?:the\s+)?words?\s+)?[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_all_occurrences_substituted:
        subs.append(
            {
                "original": m.group(1),
                "replacement": m.group(2),
                "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
            }
        )

    matches_each_case_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](?P<original>.*?)[”\"'’],?\s+"
        r"in each case\s+(?:where\s+)?(?:(?:it|they|those words?)\s+)"
        r"(?:occurs?|appears?),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+(?:(?:the\s+)?words?\s+)?[“”\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_each_case_substituted:
        subs.append(
            {
                "original": m.group("original"),
                "replacement": m.group("replacement"),
                "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
            }
        )

    matches_first_second_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"(?:\(\s*)?in the (?:first and second|first two) places?"
        r"(?:\s+(?:where\s+)?(?:it|they|those words?)\s+(?:occurs?|appear)s?)?"
        r"(?:\s*\))?,?\s+substitute\s+[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_first_second_substituted:
        # Emit in descending occurrence order so sequential replay changes the
        # second original occurrence before the first one.
        for occurrence in ("2", "1"):
            subs.append(
                {
                    "original": m.group(1),
                    "replacement": m.group(2),
                    "occurrence": occurrence,
                    "rule_id": "uk_effect_first_second_occurrence_substitution_text_patch",
                }
            )

    matches_ordinal_substituted = re.finditer(
        r"for\s+(?:the\s+)?(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"[“\"'‘](.*?)[”\"'’]\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_ordinal_substituted:
        subs.append(
            {
                "original": m.group(2),
                "replacement": m.group(3),
                "occurrence": _ORDINAL_OCCURRENCES[m.group(1).lower()],
                "rule_id": "uk_effect_ordinal_substitution_text_patch",
            }
        )

    matches_post_quoted_ordinal_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"\(?\s*in the (first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th) place"
        r"(?:\s+(?:where\s+)?(?:it|they|those words?)\s+(?:occurs?|appear)s?)?,?\s*\)?,?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+(?:(?:the\s+)?words?\s+)?[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_post_quoted_ordinal_substituted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": m.group(3).strip(),
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
                "rule_id": "uk_effect_post_quoted_ordinal_substitution_text_patch",
            }
        )

    matches_post_quoted_where_ordinal_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"where\s+(?:(?:it|they|those words?)\s+)?"
        r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:occurs?|occurring|appears?),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_post_quoted_where_ordinal_substituted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": m.group(3).strip(),
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
                "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
            }
        )

    matches_post_quoted_where_occurs_ordinal_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](.*?)[”\"'’],?\s+"
        r"where\s+(?:(?:it|they|those words?)\s+)"
        r"(?:occurs?|appear)s?\s+"
        r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+[“”\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_post_quoted_where_occurs_ordinal_substituted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": m.group(3).strip(),
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
                "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
            }
        )

    matches_post_quoted_where_ordinal_occurrences_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](?P<original>.*?)[”\"'’],?\s+"
        r"where\s+(?:(?:it|they|those words?)\s+)?"
        rf"(?P<ordinals>(?:{_ORDINAL_OCCURRENCE_WORDS})"
        rf"(?:\s*(?:,|and)\s*(?:{_ORDINAL_OCCURRENCE_WORDS}))+)\s+"
        r"(?:occurs?|occur|occurring|appears?|appear),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+[“”\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_post_quoted_where_ordinal_occurrences_substituted:
        for occurrence in sorted(
            _ordinal_occurrences_from_phrase(m.group("ordinals")),
            key=int,
            reverse=True,
        ):
            subs.append(
                {
                    "original": m.group("original").strip(),
                    "replacement": m.group("replacement").strip(),
                    "occurrence": occurrence,
                    "rule_id": UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID,
                }
            )

    matches_post_quoted_ordinal_places_occurrences_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](?P<original>.*?)[”\"'’],?\s+"
        rf"in\s+the\s+(?P<ordinals>(?:{_ORDINAL_OCCURRENCE_WORDS})"
        rf"(?:\s*(?:,|and)\s*(?:{_ORDINAL_OCCURRENCE_WORDS}))+)\s+places?"
        r"\s+where\s+(?:it|they|those words?)\s+(?:occurs?|occur|appears?|appear),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+[“”\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_post_quoted_ordinal_places_occurrences_substituted:
        occurrences = _ordinal_occurrences_from_phrase(m.group("ordinals"))
        if set(occurrences) == {"1", "2"}:
            continue
        for occurrence in sorted(occurrences, key=int, reverse=True):
            subs.append(
                {
                    "original": m.group("original").strip(),
                    "replacement": m.group("replacement").strip(),
                    "occurrence": occurrence,
                    "rule_id": UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID,
                }
            )

    matches_both_subsequent_occurrences_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“”\"'‘](?P<original>.*?)[”\"'’],?\s+"
        r"where\s+(?:it|they|those words?)\s+(?:appears?|appear|occurs?|occur)"
        r"\s+in\s+both\s+subsequent\s+places,?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)"
        r"\s+[“”\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_both_subsequent_occurrences_substituted:
        for occurrence in ("3", "2"):
            subs.append(
                {
                    "original": m.group("original").strip(),
                    "replacement": m.group("replacement").strip(),
                    "occurrence": occurrence,
                    "rule_id": UK_BOTH_SUBSEQUENT_OCCURRENCES_SUBSTITUTION_RULE_ID,
                }
            )

    matches_parenthesized_nested_quote_substituted = re.finditer(
        r"for\s+[“”\"'‘]\((?P<original>[“\"'‘].*?[”\"'’])\)\s+"
        r"substitute\s+[“”\"'‘]\((?P<replacement>.*?[“\"'‘].*?[”\"'’])\)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_parenthesized_nested_quote_substituted:
        subs.append(
            {
                "original": f"({m.group('original')})",
                "replacement": f"({m.group('replacement')})",
                "rule_id": "uk_effect_parenthesized_nested_quote_substitution_text_patch",
            }
        )

    matches_definition_range_to_end_substituted = re.finditer(
        r"in the definition of [“\"'‘](.*?)[”\"'’],?\s+"
        r"for (?:the )?words? from [“\"'‘](.*?)[”\"'’] to the end"
        r"(?: of (?:the )?(?:definition|subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_definition_range_to_end_substituted:
        subs.append(
            {
                "original": (
                    f"TEXT_IN_DEFINITION_{m.group(1).strip()}"
                    f"{US}FROM{US}{m.group(2).strip()}{US}TO_END"
                ),
                "replacement": m.group(3).strip(),
                "rule_id": "uk_effect_definition_range_to_end_substitution_text_patch",
            }
        )

    matches_definition_range_to_end_occurrence_substituted = re.finditer(
        r"in the definition of [“\"'‘](?P<term>.*?)[”\"'’],?\s+"
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’],?\s+"
        rf"where\s+(?:(?:it|they|those words?)\s+)?"
        rf"(?P<ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+"
        r"(?:occurs?|occurring|appear)s?,?\s+to the end"
        r"(?: of (?:the )?(?:definition|subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_definition_range_to_end_occurrence_substituted:
        subs.append(
            {
                "original": (
                    f"TEXT_IN_DEFINITION_{m.group('term').strip()}"
                    f"{US}FROM{US}{m.group('start').strip()}{US}TO_END"
                ),
                "replacement": m.group("replacement").strip(),
                "occurrence": _ORDINAL_OCCURRENCES[m.group("ordinal").lower()],
                "rule_id": "uk_effect_definition_range_to_end_occurrence_substitution_text_patch",
            }
        )

    matches_same_anchor_adjacent_occurrence_range_substituted = re.finditer(
        r"for (?:the )?words? from [“\"‘](?P<start>.*?)[”\"’],?"
        r"\s+where it (?P<start_ordinal>first|1st|second|2nd|third|3rd|fourth|4th)\s+occurs,?"
        r"\s+to [“\"‘](?P<end>.*?)[”\"’],?"
        r"\s+where it (?P<end_ordinal>second|2nd|third|3rd|fourth|4th|fifth|5th)\s+occurs,?"
        r"\s+substitute\s+[“\"‘](?P<replacement>.*?)[”\"’]",
        text,
        re.I,
    )
    for m in matches_same_anchor_adjacent_occurrence_range_substituted:
        start = m.group("start").strip()
        end = m.group("end").strip()
        start_occurrence = int(_ORDINAL_OCCURRENCES[m.group("start_ordinal").lower()])
        end_occurrence = int(_ORDINAL_OCCURRENCES[m.group("end_ordinal").lower()])
        if start != end or end_occurrence != start_occurrence + 1:
            continue
        subs.append(
            {
                "original": f"TEXT_FROM_{start}_TO_{end}",
                "replacement": m.group("replacement").strip(),
                "occurrence": str(start_occurrence),
                "rule_id": "uk_effect_same_anchor_adjacent_occurrence_range_substitution_text_patch",
            }
        )

    # Pattern 1aa: "for the words from 'X' to 'Y' substitute 'Z'"
    # This is a text-span replacement across the target subtree, not a
    # structural child-label range like FROM_(a)_TO_(b).
    matches_range_substituted = re.finditer(
        r"for (?:the )?words? from\s+(?:the\s+(?P<start_pre_ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+)?[“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:(?:\s+where it|,\s+where(?:\s+it)?)\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+(?:occurs|occurring),?)?"
        r"\s+to\s+(?:the\s+(?P<end_pre_ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+)?[“\"'‘](?P<end>.*?)[”\"'’]"
        r"(?:(?:,\s+where it|,\s+where)\s+(?P<end_ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+(?:occurs|occurring),?)?"
        r"(?:\s+\(?\s*in the (?P<range_ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th) place\s*\)?)?"
        r",?\s+(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_range_substituted:
        if (
            m.group("ordinal")
            and m.group("end_ordinal")
            and m.group("start").strip() == m.group("end").strip()
        ):
            continue
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_{m.group('end').strip()}",
            "replacement": m.group("replacement").strip(),
            "rule_id": "uk_effect_range_substitution_text_patch",
        }
        if m.group("start_pre_ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("start_pre_ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_occurrence_substitution_text_patch"
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_occurrence_substitution_text_patch"
        if m.group("end_pre_ordinal"):
            patch["end_occurrence"] = _ORDINAL_OCCURRENCES[m.group("end_pre_ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_independent_end_occurrence_substitution_text_patch"
        if m.group("end_ordinal"):
            patch["end_occurrence"] = _ORDINAL_OCCURRENCES[m.group("end_ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_independent_end_occurrence_substitution_text_patch"
        if m.group("range_ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("range_ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_occurrence_substitution_text_patch"
        subs.append(patch)

    matches_range_unquoted_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:,\s+where\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+occurring)?"
        r"\s+to [“\"'‘](?P<end>.*?)[”\"'’],?\s+substitute\s*[—-]?\s+"
        r"(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_range_unquoted_substituted:
        replacement = m.group("replacement").strip()
        if replacement.startswith(("“", '"', "'", "‘")):
            continue
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_{m.group('end').strip()}",
            "replacement": re.sub(r"\s+\.$", "", replacement).strip(),
            "rule_id": "uk_effect_range_unquoted_substitution_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_where_ordinal_substitution_text_patch"
        subs.append(patch)

    matches_bare_range_unquoted_substituted = re.finditer(
        r"(?<!words )(?<!word )\bfrom [“\"'‘](?P<start>.*?)[”\"'’]"
        r"\s+to [“\"'‘](?P<end>.*?)[”\"'’],?\s+substitute\s*[—-]?\s+"
        r"(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_bare_range_unquoted_substituted:
        replacement = m.group("replacement").strip()
        if replacement.startswith(("“", '"', "'", "‘")):
            continue
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group('start').strip()}_TO_{m.group('end').strip()}",
                "replacement": re.sub(r"\s+\.$", "", replacement).strip(),
                "rule_id": "uk_effect_bare_range_unquoted_substitution_text_patch",
            }
        )

    matches_labeled_end_range_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:\s+where it\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:occurs|appears))?"
        r" to the end of (?P<kind>sub-?paragraph|paragraph|subsection)\s*"
        r"\((?P<label>[0-9A-Za-z]+)\),?\s+"
        r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\s+"
        r"[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_labeled_end_range_substituted:
        suffix_kind = m.group("kind").lower().replace("-", "")
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
            "replacement": m.group("replacement").strip(),
            "target_suffix_kind": suffix_kind,
            "target_suffix_label": m.group("label").strip(),
            "rule_id": "uk_effect_labeled_end_range_substitution_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
        subs.append(patch)

    matches_range_to_end_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:\s+where it\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:occurs|appears))?"
        r" (?:to the end(?: of (?:(?:the|that) )?(?:subsection|paragraph|sub-paragraph|section))?|onwards),?\s+"
        r"(?P<verb>substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\s+"
        r"[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_range_to_end_substituted:
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
            "replacement": m.group("replacement").strip(),
        }
        if m.group("verb").lower().startswith("there"):
            patch["rule_id"] = "uk_effect_range_to_end_there_is_substituted_text_patch"
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
        subs.append(patch)

    matches_anchor_to_end_substituted = re.finditer(
        r"(?<!words )(?<!word )\bfrom [“\"'‘](.*?)[”\"'’] to the end"
        r"(?: of (?:(?:the|that) )?(?:subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_anchor_to_end_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group(1).strip()}_TO_END",
                "replacement": m.group(2).strip(),
                "rule_id": "uk_effect_anchor_to_end_substitution_text_patch",
            }
        )

    matches_quoted_anchor_to_end_block_substituted = re.finditer(
        r"for\s+[“\"'‘](.*?)[”\"'’]\s+to the end"
        r"(?: of (?:the )?(?:subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute[—-]?\s+(.+)$",
        text,
        re.I,
    )
    for m in matches_quoted_anchor_to_end_block_substituted:
        replacement = m.group(2).strip()
        if replacement:
            subs.append(
                {
                    "original": f"TEXT_FROM_{m.group(1).strip()}_TO_END",
                    "replacement": replacement,
                    "rule_id": "uk_effect_quoted_anchor_to_end_block_substitution_text_patch",
                }
            )

    matches_quoted_words_anchor_to_end_substituted = re.finditer(
        r"for\s+(?:the\s+)?words?\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+to the end"
        r"(?: of (?:the )?(?:subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_quoted_words_anchor_to_end_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group('anchor').strip()}_TO_END",
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_quoted_words_anchor_to_end_substitution_text_patch",
            }
        )

    matches_anchor_to_end_block_substituted = re.finditer(
        r"(?:for (?:the )?words?\s+)?(?:from\s+)?[“\"'‘](.*?)[”\"'’]\s+to\s+the\s+end"
        r"(?: of (?:(?:the|that) )?(?:subsection|paragraph|sub-paragraph|section))?"
        r",?\s+substitute\s*[—-]?\s+(.+?)(?:\s+[.;])?$",
        text,
        re.I,
    )
    for m in matches_anchor_to_end_block_substituted:
        replacement = m.group(2).strip()
        if replacement and not replacement.startswith(("“", '"', "'", "‘")):
            subs.append(
                {
                    "original": f"TEXT_FROM_{m.group(1).strip()}_TO_END",
                    "replacement": replacement,
                    "rule_id": "uk_effect_anchor_to_end_block_substitution_text_patch",
                }
            )

    matches_range_to_end_open_quote_block_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:\s+where it\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:occurs|appears))?"
        r" to the end(?: of (?:(?:the|that) )?(?:subsection|paragraph|sub-paragraph|section))?,?\s+"
        r"substitute\s+[“\"'‘]\s*[—-]\s+(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_range_to_end_open_quote_block_substituted:
        replacement = m.group("replacement").strip()
        if not replacement or replacement.endswith(("”", '"', "'", "’")):
            continue
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
            "replacement": re.sub(r"\s+\.$", "", replacement).strip(),
            "rule_id": "uk_effect_range_to_end_open_quote_block_substitution_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
        subs.append(patch)

    matches_range_to_end_block_substituted = re.finditer(
        r"for (?:the )?words? from [“\"'‘](?P<start>.*?)[”\"'’]"
        r"\s+where it\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:occurs|appears)"
        r" to the end(?: of (?:(?:the|that) )?(?:subsection|paragraph|sub-paragraph|section))?,?\s+"
        r"substitute\s*[—-]\s+(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_range_to_end_block_substituted:
        replacement = m.group("replacement").strip()
        if replacement.startswith(("“", '"', "'", "‘")):
            continue
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
            "replacement": re.sub(r"\s+\.$", "", replacement).strip(),
            "occurrence": _ORDINAL_OCCURRENCES[m.group("ordinal").lower()],
            "rule_id": "uk_effect_range_to_end_ordinal_block_substitution_text_patch",
        }
        subs.append(patch)

    matches_after_anchor_substituted = re.finditer(
        r"for (?:the )?words? (?:after|following) [“\"'‘](.*?)[”\"'’]"
        r"\s+substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_anchor_substituted:
        subs.append(
            {
                "original": f"TEXT_AFTER_{m.group(1).strip()}_TO_END",
                "replacement": m.group(2).strip(),
                "rule_id": "uk_effect_after_anchor_to_end_substitution_text_patch",
            }
        )



    matches_opening_words_substituted = re.finditer(
        r"for (?:the )?opening words substitute [“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_opening_words_substituted:
        subs.append(
            {
                "original": "TEXT_OPENING_WORDS",
                "replacement": m.group(1).strip(),
                "rule_id": "uk_effect_opening_words_substitution_text_patch",
            }
        )

    matches_words_before_child_substituted = re.finditer(
        r"for (?:the )?words? before "
        r"(paragraph|sub-paragraph|subsection)\s+\(([0-9A-Za-z]+)\),?\s+"
        r"substitute\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_words_before_child_substituted:
        unit_kind = m.group(1).lower().replace("-", "")
        subs.append(
            {
                "original": f"TEXT_BEFORE_CHILD_{unit_kind}_{m.group(2).strip()}",
                "replacement": m.group(3).strip(),
                "rule_id": "uk_effect_before_child_text_substitution_patch",
            }
        )

    matches_for_there_is_inserted = re.finditer(
        r"for [“\"'‘]([^“”\"'‘’]*?)[”\"'’]\s+"
        r"there\s+(?:is|are|shall\s+be)\s+inserted\s+"
        r"[“\"'‘]([^“”\"'‘’]*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_for_there_is_inserted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": m.group(2).strip(),
                "rule_id": "uk_effect_for_there_is_inserted_replacement_text_patch",
            }
        )

    matches_for_insert = re.finditer(
        r"for [“\"'‘](.*?)[”\"'’]\s+insert\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_for_insert:
        original = m.group(1).strip()
        inserted = m.group(2).strip()
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "rule_id": "uk_effect_for_insert_text_insertion_patch",
            }
        )

    matches_from_beginning_substituted = re.finditer(
        r"(?:for|from)\s+(?:the\s+)?words?\s+from\s+the\s+beginning\s+to\s+"
        r"[“\"'‘](.*?)[”\"'’]\s+substitute\s+[“\"'‘](.*)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_from_beginning_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM__TO_{m.group(1).strip()}",
                "replacement": m.group(2).strip(),
            }
        )

    matches_from_beginning_passive_substituted = re.finditer(
        r"for\s+(?:the\s+)?words?\s+from\s+the\s+beginning"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?(?:subsection|paragraph|sub-paragraph|section))?"
        r"\s+to\s+[“\"'‘](?P<end>.*?)[”\"'’]\s+"
        r"(?:is|are|shall\s+be)\s+substituted\s+"
        r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_from_beginning_passive_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM__TO_{m.group('end').strip()}",
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
            }
        )

    matches_from_beginning_there_shall_be_substituted = re.finditer(
        r"for\s+(?:the\s+)?words?\s+from\s+the\s+beginning"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?(?:subsection|paragraph|sub-paragraph|section))?"
        r"\s+to\s+[“\"'‘](?P<end>.*?)[”\"'’]\s+"
        r"there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_from_beginning_there_shall_be_substituted:
        subs.append(
            {
                "original": f"TEXT_FROM__TO_{m.group('end').strip()}",
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
            }
        )

    matches_from_beginning_block_substituted = re.finditer(
        r"(?:for|from)\s+(?:the\s+)?words?\s+from\s+the\s+beginning\s+to\s+"
        r"[“\"'‘](.*?)[”\"'’]\s+substitute\s*[—-]?\s+(.+?)(?:\s+[.;])?$",
        text,
        re.I,
    )
    for m in matches_from_beginning_block_substituted:
        replacement = m.group(2).strip()
        if replacement and not replacement.startswith(("“", '"', "'", "‘")):
            subs.append(
                {
                    "original": f"TEXT_FROM__TO_{m.group(1).strip()}",
                    "replacement": replacement,
                    "rule_id": "uk_effect_from_beginning_block_substitution_text_patch",
                }
            )

    matches_proviso_child_substituted = re.finditer(
        r"for\s+paragraph\s+\(?([a-zA-Z0-9]+)\)?\s+of\s+the\s+proviso\s+substitute\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_proviso_child_substituted:
        subs.append(
            {
                "original": f"TEXT_PROVISO_CHILD_{m.group(1).strip()}",
                "replacement": m.group(2).strip(),
                "rule_id": "uk_effect_proviso_child_substitution_text_patch",
            }
        )

    matches_paragraphs_substituted = re.finditer(
        r"for\s+paragraphs?\s+(?P<labels>[a-zA-Z0-9\s\(\),&and]+)\s+substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_paragraphs_substituted:
        labels_str = m.group("labels")
        labels = [lbl.strip("() ") for lbl in re.split(r",|\band\b|&", labels_str) if lbl.strip()]
        if labels:
            labels_suffix = "_".join(labels)
            subs.append(
                {
                    "original": f"TEXT_REPLACE_CHILDREN_PARAGRAPH_{labels_suffix}",
                    "replacement": m.group("replacement").strip(),
                    "rule_id": "uk_effect_paragraphs_range_substitution_text_patch",
                }
            )

    # Pattern 1a: "for the words 'X' are substituted the words 'Y'"
    matches_are_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’](?:\s*\([^)]*\))?\s+(?:is|are|shall\s+be)\s+substituted\s+(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_are_substituted:
        if _span_overlaps(m.span(), respectively_spans):
            continue
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    matches_preposed_passive_substituted = re.finditer(
        r"there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"for\s+(?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"(?:(?:the )?words? )?[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_preposed_passive_substituted:
        subs.append(
            {
                "original": m.group("original").strip(),
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_preposed_passive_substitution_text_patch",
            }
        )

    matches_missing_space_there_is_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’]"
        r"there\s+(?:is|are|shall\s+be)\s+substituted\s+"
        r"(?:(?:the )?words? )?[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_missing_space_there_is_substituted:
        if _span_overlaps(m.span(), respectively_spans):
            continue
        subs.append(
            {
                "original": m.group("original").strip(),
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_missing_space_there_is_substituted_text_patch",
            }
        )

    matches_there_is_substituted = re.finditer(
        r"for (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’](?:\s*\([^)]*\))?\s+there\s+(?:is|are|shall\s+be)\s+substituted\s+(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_there_is_substituted:
        if _span_overlaps(m.span(), respectively_spans):
            continue
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    matches_is_replaced_with = re.finditer(
        r"(?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]\s+(?:is|are)\s+replaced\s+with\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_is_replaced_with:
        subs.append({"original": m.group(1), "replacement": m.group(2)})

    # Pattern 1b: Insertion after a quoted fragment.
    # Treat this as a text replacement on the matched fragment so replay can
    # materialize the inserted words without inventing structural descendants.
    matches_after_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’]"
        r"(?:\s+\([^)]*(?:\([^)]*\)[^)]*)*\))?"
        r"(?P<all_occurrences>,?\s+in (?:(?:each|both) places?|each place|each of the two places)"
        r"(?:\s+(?:where\s+)?(?:(?:it|they|those words?)\s+)?"
        r"(?:occurs?|occurring|appear)s?(?:\s+in\s+[^,;]+)?)?)?"
        r",?\s+(?:there is inserted|there are inserted|there shall be inserted|there is entered|there are entered|there shall be entered|insert|enter)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_insert:
        if re.search(r"in the definition of [“\"'‘].*?[”\"'’],?\s*$", text[: m.start()], re.I):
            continue
        original = m.group(1)
        inserted = m.group(3)
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        patch = {
            "original": original,
            "replacement": f"{original}{joiner}{inserted}",
        }
        if m.group("all_occurrences"):
            patch["rule_id"] = "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
        else:
            patch["rule_id"] = "uk_effect_after_quoted_anchor_insert_text_patch"
        subs.append(patch)

    matches_bare_quoted_anchor_insert = re.finditer(
        r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
        r"(?:the\s+words?\s+)?"
        r"[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]"
        r"\s*[;,]?\s*(?:and)?\s*\.?\s*$",
        text,
        re.I,
    )
    for m in matches_bare_quoted_anchor_insert:
        original = m.group("original")
        inserted = m.group("inserted")
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "rule_id": "uk_effect_bare_quoted_anchor_insert_text_patch",
            }
        )

    matches_after_parenthesized_anchor_insert = re.finditer(
        r"\bafter\s+\((?P<original>[0-9A-Za-z]+)\),?\s+"
        r"(?:there\s+(?:is|are|shall\s+be)\s+inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_parenthesized_anchor_insert:
        original = f"({m.group('original').strip()})"
        inserted = m.group("inserted").strip()
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "rule_id": "uk_effect_after_parenthesized_anchor_insert_text_patch",
            }
        )

    matches_after_each_occurrence_insert = re.finditer(
        r"after\s+each\s+occurrence\s+of\s+[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
        r"insert\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_each_occurrence_insert:
        original = m.group("original")
        inserted = m.group("inserted")
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
            }
        )

    matches_after_each_occasion_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
        r"on each occasion where (?:it|they|those words?)\s+(?:appears?|occurs?),?\s+"
        r"insert\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_each_occasion_insert:
        original = m.group("original")
        inserted = m.group("inserted")
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "rule_id": "uk_effect_after_quoted_anchor_each_occasion_insert_text_patch",
            }
        )

    matches_after_anchor_block_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s*[—-]\s+"
        r"(?P<inserted>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_after_anchor_block_insert:
        original = m.group("original").strip()
        inserted = re.sub(r"\s+\.$", "", m.group("inserted").strip()).strip()
        if inserted and not inserted.startswith(("“", '"', "'", "‘")):
            joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
            subs.append(
                {
                    "original": original,
                    "replacement": f"{original}{joiner}{inserted}",
                    "rule_id": "uk_effect_after_quoted_anchor_block_insert_text_patch",
                }
            )
        elif inserted and re.search(
            r"^[“\"'‘].*?[”\"'’]\s+(?:means|has\s+the\s+same\s+meaning\s+as|includes)\b",
            inserted,
            re.I,
        ):
            joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
            subs.append(
                {
                    "original": original,
                    "replacement": f"{original}{joiner}{inserted}",
                    "rule_id": "uk_effect_after_quoted_anchor_definition_entry_block_insert_text_patch",
                }
            )

    matches_after_ordinal_places_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
        rf"in\s+the\s+(?P<ordinals>(?:{_ORDINAL_OCCURRENCE_WORDS})"
        rf"(?:\s*(?:,|and)\s*(?:{_ORDINAL_OCCURRENCE_WORDS}))*)\s+places?"
        r"\s+where\s+(?:it|they|those words?)\s+(?:occurs?|appear)s?,?\s+"
        r"(?:insert|there\s+(?:is|are|shall\s+be)\s+inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_ordinal_places_insert:
        original = m.group("original")
        inserted = m.group("inserted")
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        for occurrence in sorted(
            _ordinal_occurrences_from_phrase(m.group("ordinals")),
            key=int,
            reverse=True,
        ):
            subs.append(
                {
                    "original": original,
                    "replacement": f"{original}{joiner}{inserted}",
                    "occurrence": occurrence,
                    "rule_id": UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID,
                }
            )

    matches_after_ordinal_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’],?\s+"
        r"in the (first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th) place"
        r"(?:\s+(?:it|they|those words?)\s+(?:occurs?|appear)s?)?,?\s+"
        r"insert\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_ordinal_insert:
        original = m.group(1)
        inserted = m.group(3)
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
                "rule_id": "uk_effect_after_quoted_anchor_ordinal_insert_text_patch",
            }
        )

    matches_after_where_ordinal_nested_quote_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
        rf"where\s+(?:(?:it|they|those words?)\s+)?"
        rf"(?P<ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+"
        r"(?:occurs?|occurring|appear)s?,?\s+(?:there\s+(?:is|are|shall\s+be)\s+inserted|insert)\s+"
        r"[“\"'‘](?P<inserted>.+)[”\"'’]\s*(?:[,.;]|$)",
        text,
        re.I,
    )
    for m in matches_after_where_ordinal_nested_quote_insert:
        original = m.group("original")
        inserted = m.group("inserted")
        if not any(q in inserted for q in ("“", "”", '"', "‘", "’", "'")):
            continue
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": _ORDINAL_OCCURRENCES[m.group("ordinal").lower()],
                "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_nested_quote_insert_text_patch",
            }
        )

    matches_after_where_ordinal_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’],?\s+"
        rf"where\s+(?:(?:it|they|those words?)\s+)?"
        rf"({_ORDINAL_OCCURRENCE_WORDS})\s+"
        r"(?:occurs?|occurring|appear)s?,?\s+(?:there\s+(?:is|are|shall\s+be)\s+inserted|insert)\s+"
        r"[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_where_ordinal_insert:
        if re.match(r"\s*[^\s,.;]", text[m.end() :]):
            continue
        original = m.group(1)
        inserted = m.group(3)
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
                "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch",
            }
        )

    matches_word_inserted_after_word_where_ordinal = re.finditer(
        r"(?:the\s+)?word\s+[“\"'‘](?P<inserted>.*?)[”\"'’]\s+"
        r"(?:is|are)\s+inserted\s+after\s+(?:the\s+)?word\s+[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"where\s+it\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
        r"(?:appears?|occurs?)",
        text,
        re.I,
    )
    for m in matches_word_inserted_after_word_where_ordinal:
        original = m.group("original")
        inserted = m.group("inserted")
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": _ORDINAL_OCCURRENCES[m.group("ordinal").lower()],
                "rule_id": "uk_effect_word_inserted_after_word_where_ordinal_text_patch",
            }
        )

    matches_after_where_last_insert = re.finditer(
        r"after (?:(?:the )?words? )?[“\"'‘](.*?)[”\"'’],?\s+"
        r"where\s+last\s+occurring,?\s+insert\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_where_last_insert:
        original = m.group(1)
        inserted = m.group(2)
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": "-1",
                "rule_id": "uk_effect_after_quoted_anchor_last_occurrence_insert_text_patch",
            }
        )

    matches_after_definition_insert = re.finditer(
        r"after the definition of (?:the\s+)?[“\"'‘](.*?)[”\"'’],?\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_after_definition_insert:
        inserted = re.sub(r"\s+\.$", "", m.group(2).strip()).strip()
        if inserted:
            subs.append(
                {
                    "original": f"TEXT_AFTER_DEFINITION_{m.group(1).strip()}",
                    "replacement": inserted,
                    "rule_id": "uk_effect_after_definition_text_insertion_patch",
                }
            )

    matches_after_definitions_insert = re.finditer(
        r"after the definitions of (?P<terms>.+?)\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s*[—-]?\s+"
        r"(?P<inserted>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_after_definitions_insert:
        anchor = _last_quoted_term(m.group("terms"))
        inserted = re.sub(r"\s+\.$", "", m.group("inserted").strip()).strip()
        if anchor and inserted:
            subs.append(
                {
                    "original": f"TEXT_AFTER_DEFINITION_{anchor}",
                    "replacement": inserted,
                    "rule_id": "uk_effect_after_definitions_text_insertion_patch",
                }
            )

    matches_before_definition_insert = re.finditer(
        r"before the definition of (?:the\s+)?"
        r"(?:[“\"'‘](?P<quoted>.*?)[”\"'’]|(?P<bare>.+?)),?\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_before_definition_insert:
        anchor = (m.group("quoted") or m.group("bare") or "").strip()
        inserted = re.sub(r"\s+\.$", "", m.group(3).strip()).strip()
        if inserted:
            subs.append(
                {
                    "original": f"TEXT_BEFORE_DEFINITION_{anchor}",
                    "replacement": inserted,
                    "rule_id": "uk_effect_before_definition_text_insertion_patch",
                }
            )

    matches_definition_entry_insert = re.finditer(
        r"(?P<direction>before|after)\s+(?:the\s+)?entry\s+for\s+"
        r"(?:[“\"'‘](?P<quoted>.*?)[”\"'’]|(?P<bare>.+?))\s*,?\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert(?:ed)?)"
        r"(?:\s+(?:the\s+)?words?)?\s*[—-]?\s+"
        r"(?P<inserted>.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_definition_entry_insert:
        inserted = re.sub(r"\s+\.$", "", m.group("inserted").strip()).strip()
        if not _looks_like_definition_entry_payload(inserted):
            continue
        anchor = (m.group("quoted") or m.group("bare") or "").strip()
        if not anchor:
            continue
        direction = m.group("direction").lower()
        selector = "TEXT_BEFORE_DEFINITION" if direction == "before" else "TEXT_AFTER_DEFINITION"
        subs.append(
            {
                "original": f"{selector}_{anchor}",
                "replacement": inserted,
                "rule_id": f"uk_effect_{direction}_definition_entry_text_insertion_patch",
            }
        )

    matches_definition_entry_substituted = re.finditer(
        r"for the definition of (?:the\s+)?[“\"'‘](.*?)[”\"'’],?\s+substitute[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_definition_entry_substituted:
        replacement = re.sub(r"\s+\.$", "", m.group(2).strip()).strip()
        if replacement:
            subs.append(
                {
                    "original": f"TEXT_DEFINITION_ENTRY_{m.group(1).strip()}",
                    "replacement": replacement,
                    "rule_id": "uk_effect_definition_entry_substitution_text_patch",
                }
            )

    matches_after_entry_for_insert = re.finditer(
        r"after the entry for [“\"'‘](.*?)[”\"'’]\s+of\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_entry_for_insert:
        original = m.group(1).strip()
        inserted = m.group(2).strip()
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
            }
        )

    matches_after_child_insert = re.finditer(
        r"after\s+(paragraph|sub-paragraph|subsection)\s+\(([0-9A-Za-z]+)\)\s+insert\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_child_insert:
        unit_kind = m.group(1).lower().replace("-", "")
        subs.append(
            {
                "original": f"TEXT_AFTER_CHILD_{unit_kind}_{m.group(2).strip()}",
                "replacement": m.group(3).strip(),
                "rule_id": "uk_effect_after_child_text_insertion_patch",
            }
        )

    matches_after_compound_subsection_child_insert = re.finditer(
        r"after\s+subsection\s+\([0-9A-Za-z]+\)\([a-z]\)\(([ivxlcdm0-9A-Za-z]+)\),?\s+"
        r"insert\s+[“\"'‘](.+?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_compound_subsection_child_insert:
        subs.append(
            {
                "original": f"TEXT_AFTER_CHILD_subparagraph_{m.group(1).strip()}",
                "replacement": m.group(2).strip(),
                "rule_id": "uk_effect_after_compound_subsection_child_text_insertion_patch",
            }
        )

    matches_after_definition_child_insert = re.finditer(
        r"in the definition of [“\"'‘](.*?)[”\"'’],\s+"
        r"after\s+(paragraph)\s+\(([0-9A-Za-z]+)\)\s+insert\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_after_definition_child_insert:
        inserted = _strip_inserted_child_label(m.group(4))
        if inserted:
            subs.append(
                {
                    "original": (
                        f"TEXT_AFTER_DEFINITION_{m.group(2).strip().upper()}_"
                        f"{m.group(1).strip()}_AFTER_{m.group(3).strip()}"
                    ),
                    "replacement": inserted,
                    "rule_id": "uk_effect_after_definition_child_text_insertion_patch",
                }
            )

    matches_definition_at_end_insert = re.finditer(
        r"at\s+the\s+end\s+of\s+the\s+definition\s+of\s+[“\"'‘](?P<term>.*?)[”\"'’],?\s+"
        r"(?:insert|there is inserted|there are inserted|there shall be inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_definition_at_end_insert:
        inserted = m.group("inserted").strip()
        term = m.group("term").strip()
        if inserted and term:
            subs.append(
                {
                    "original": f"TEXT_IN_DEFINITION_{term}{US}AT_END",
                    "replacement": inserted,
                    "rule_id": "uk_effect_in_definition_at_end_insert_text_patch",
                }
            )

    matches_definition_child_substituted = re.finditer(
        r"in the definition of [“\"'‘](.*?)[”\"'’],?\s+"
        r"for\s+(paragraph)\s+\(([0-9A-Za-z]+)\)\s+substitute\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_definition_child_substituted:
        replacement = _strip_optional_child_label(m.group(4), m.group(3))
        replacement = re.sub(r"\s+\.$", "", replacement).strip()
        if replacement:
            subs.append(
                {
                    "original": (
                        f"TEXT_DEFINITION_CHILD_{m.group(2).strip().upper()}_"
                        f"{m.group(1).strip()}{US}{m.group(3).strip()}"
                    ),
                    "replacement": replacement,
                    "rule_id": "uk_effect_definition_child_substitution_text_patch",
                }
            )

    matches_definition_child_repeal = re.finditer(
        r"in the definition of [“\"'‘](.*?)[”\"'’],?\s+"
        r"omit\s+(paragraph)\s+\(([0-9A-Za-z]+)\)",
        text,
        re.I,
    )
    for m in matches_definition_child_repeal:
        subs.append(
            {
                "original": (
                    f"TEXT_DEFINITION_CHILD_{m.group(2).strip().upper()}_"
                    f"{m.group(1).strip()}{US}{m.group(3).strip()}"
                ),
                "replacement": "",
                "rule_id": "uk_effect_definition_child_repeal_text_patch",
            }
        )

    matches_definition_child_repeal_postpositive = re.finditer(
        r"omit\s+(paragraph)\s+\(([0-9A-Za-z]+)\)\s+"
        r"of\s+the\s+definition\s+of\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_definition_child_repeal_postpositive:
        subs.append(
            {
                "original": (
                    f"TEXT_DEFINITION_CHILD_{m.group(1).strip().upper()}_"
                    f"{m.group(3).strip()}{US}{m.group(2).strip()}"
                ),
                "replacement": "",
                "rule_id": "uk_effect_definition_child_repeal_text_patch",
            }
        )
    matches_omit_paragraphs = re.finditer(
        r"\bomit\s+paragraphs?\s+(?P<labels>[0-9A-Za-z\s\(\),&and]+)\.?",
        text,
        re.I,
    )
    for m in matches_omit_paragraphs:
        labels_str = m.group("labels")
        labels = [lbl.strip("() ") for lbl in re.split(r",|\band\b|&", labels_str) if lbl.strip()]
        for lbl in labels:
            if lbl:
                subs.append(
                    {
                        "original": f"TEXT_OMIT_PARAGRAPH_{lbl}",
                        "replacement": "",
                        "rule_id": "uk_effect_omit_paragraph_fragment_patch",
                    }
                )


    matches_definition_child_substituted_postpositive = re.finditer(
        r"for\s+(paragraph)\s+\(([0-9A-Za-z]+)\)\s+"
        r"of\s+the\s+definition\s+of\s+[“\"'‘](.*?)[”\"'’]\s+"
        r"substitute\s*[—-]?\s+(.+?)(?:\s+\.)?$",
        text,
        re.I,
    )
    for m in matches_definition_child_substituted_postpositive:
        replacement = _strip_optional_child_label(m.group(4), m.group(2))
        replacement = re.sub(r"\s+\.$", "", replacement).strip()
        if replacement:
            subs.append(
                {
                    "original": (
                        f"TEXT_DEFINITION_CHILD_{m.group(1).strip().upper()}_"
                        f"{m.group(3).strip()}{US}{m.group(2).strip()}"
                    ),
                    "replacement": replacement,
                    "rule_id": "uk_effect_definition_child_substitution_text_patch",
                }
            )

    matches_definition_child_and_tail_substituted = re.finditer(
        r"for\s+(paragraph)\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
        r"of\s+the\s+definition\s+of\s+[“\"'‘](?P<term>.*?)[”\"'’]\s+"
        r"and\s+the\s+[“\"'‘]?(?P<tail_connector>or|and)[”\"'’]?\s+"
        r"at\s+the\s+end\s+of\s+that\s+paragraph\s+"
        r"substitute\s*[—–-]?\s+(?P<replacement>.+?)(?:\s+\.)?$",
        text,
        re.I | re.S,
    )
    for m in matches_definition_child_and_tail_substituted:
        replacement = _strip_optional_child_label(m.group("replacement"), m.group("label"))
        replacement = re.sub(r"\s+\.$", "", replacement).strip()
        if replacement:
            subs.append(
                {
                    "original": (
                        f"TEXT_DEFINITION_CHILD_{m.group(1).strip().upper()}_"
                        f"{m.group('term').strip()}{US}{m.group('label').strip()}"
                    ),
                    "replacement": replacement,
                    "tail_connector": m.group("tail_connector").strip().lower(),
                    "rule_id": "uk_effect_definition_child_and_tail_substitution_text_patch",
                }
            )

    matches_in_definition_after_all_occurrences_insert = re.finditer(
        r"in the definition of [“\"'‘](?P<term>.*?)[”\"'’],?\s+"
        r"after\s+[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
        r"in (?:each|both) places? where (?:it|they|those words?)\s+(?:appears?|occurs?),?\s+"
        r"insert\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_in_definition_after_all_occurrences_insert:
        term = m.group("term").strip()
        anchor = m.group("anchor").strip()
        inserted = m.group("inserted").strip()
        if term and anchor and inserted:
            joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
            subs.append(
                {
                    "original": f"TEXT_IN_DEFINITION_{term}{US}AFTER_EACH{US}{anchor}",
                    "replacement": f"{anchor}{joiner}{inserted}",
                    "rule_id": "uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch",
                }
            )

    matches_in_definition_after_insert = re.finditer(
        r"in the definition of [“\"'‘](.*?)[”\"'’],?\s+"
        r"after\s+[“\"'‘](.*?)[”\"'’],?\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s*;?\s+[“\"'‘](.+?)[”\"'’]"
        r"\s*(?:[,;]\s*(?:and)?|\.)?\s*$",
        text,
        re.I,
    )
    for m in matches_in_definition_after_insert:
        term = m.group(1).strip()
        anchor = m.group(2).strip()
        inserted = m.group(3).strip()
        if term and anchor and inserted:
            joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
            subs.append(
                {
                    "original": f"TEXT_IN_DEFINITION_{term}{US}AFTER{US}{anchor}",
                    "replacement": f"{anchor}{joiner}{inserted}",
                    "rule_id": "uk_effect_in_definition_after_anchor_insert_text_patch",
                }
            )

    matches_after_insert = re.finditer(
        r"after [“\"'‘](.*?)[”\"'’]"
        r"(?:\s+\([^)]*(?:\([^)]*\)[^)]*)*\))?"
        r"(?P<all_occurrences>,?\s+in (?:(?:each|both) places?|each of the two places)"
        r"(?:\s+(?:where\s+)?(?:(?:it|they|those words?)\s+)?"
        r"(?:occurs?|appear)s?(?:\s+in\s+[^,;]+)?)?)?"
        r",?\s+(?:there is inserted|there are inserted|there shall be inserted|there is entered|there are entered|there shall be entered|insert|enter)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_insert:
        if re.search(r"in the definition of [“\"'‘].*?[”\"'’],?\s*$", text[: m.start()], re.I):
            continue
        original = m.group(1)
        inserted = m.group(3)
        joiner = (
            ""
            if original.endswith((" ", "\t", "\n", "\r"))
            or inserted.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        patch = {
            "original": original,
            "replacement": f"{original}{joiner}{inserted}",
        }
        if m.group("all_occurrences"):
            patch["rule_id"] = "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
        else:
            patch["rule_id"] = "uk_effect_after_quoted_anchor_insert_text_patch"
        subs.append(patch)

    matches_after_ordinal_insert = re.finditer(
        r"after\s+(?:the\s+)?(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+[“\"'‘](.*?)[”\"'’]\s+"
        r"(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_after_ordinal_insert:
        original = m.group(2)
        inserted = m.group(3)
        joiner = "" if inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        subs.append(
            {
                "original": original,
                "replacement": f"{original}{joiner}{inserted}",
                "occurrence": _ORDINAL_OCCURRENCES[m.group(1).lower()],
                "rule_id": "uk_effect_after_prefixed_quoted_anchor_ordinal_insert_text_patch",
            }
        )

    matches_before_insert = re.finditer(
        r"before [“\"'‘](.*?)[”\"'’]"
        r"(?:,\s+in the\s+(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+place it occurs)?"
        r",?\s+(?:there is inserted|there are inserted|there shall be inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_before_insert:
        original = m.group(1)
        inserted = m.group(3)
        joiner = "" if inserted.endswith((" ", "(", "/", "-")) else " "
        patch = {
            "original": original,
            "replacement": f"{inserted}{joiner}{original}",
        }
        if m.group(2):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group(2).lower()]
            patch["rule_id"] = "uk_effect_before_quoted_anchor_ordinal_insert_text_patch"
        else:
            patch["rule_id"] = "uk_effect_before_quoted_anchor_insert_text_patch"
        subs.append(patch)

    matches_immediately_before_word_insert = re.finditer(
        r"immediately\s+before\s+(?:the\s+)?word\s+[“\"'‘](?P<original>.*?)[”\"'’]"
        r"(?:,\s+where\s+it\s+occurs\s+for\s+the\s+"
        r"(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+time)?"
        r",?\s+insert\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_immediately_before_word_insert:
        original = m.group("original").strip()
        inserted = m.group("inserted").strip()
        joiner = "" if inserted.endswith((" ", "(", "/", "-")) else " "
        patch = {
            "original": original,
            "replacement": f"{inserted}{joiner}{original}",
            "rule_id": "uk_effect_immediately_before_word_insert_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
            patch["rule_id"] = "uk_effect_immediately_before_word_ordinal_insert_text_patch"
        subs.append(patch)

    matches_at_beginning_insert = re.finditer(
        r"at the beginning(?: of (?:(?:that|the) )?(?:paragraph|sub-paragraph|subsection|section)(?:\s+\([^)]+\))?(?:\s+\([^)]*\))?)?,?\s+"
        r"(?:insert|there is inserted|there are inserted|there shall be inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_at_beginning_insert:
        subs.append(
            {
                "original": "TEXT_BEGINNING",
                "replacement": m.group(1).strip(),
                "rule_id": "uk_effect_beginning_text_insertion_patch",
            }
        )

    matches_preposed_at_beginning_insert = re.finditer(
        r"(?:there is inserted|there are inserted|there shall be inserted)\s+"
        r"at the beginning(?: of (?:(?:that|the) )?"
        r"(?:paragraph|sub-paragraph|subsection|section)(?:\s+\([^)]+\))?"
        r"(?:\s+\([^)]*\))?)?,?\s+"
        r"(?:the\s+)?words?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_preposed_at_beginning_insert:
        subs.append(
            {
                "original": "TEXT_BEGINNING",
                "replacement": m.group("inserted").strip(),
                "rule_id": "uk_effect_preposed_beginning_text_insertion_patch",
            }
        )

    matches_at_end_insert = re.finditer(
        r"at the end(?: of (?:(?:that|the) )?(?:paragraph|sub-paragraph|subsection|section)(?:\s+\([^)]+\))?(?:\s+\([^)]*\))?)?,?\s+"
        r"(?:insert|there is inserted|there are inserted|there shall be inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_at_end_insert:
        inserted = m.group(1).strip()
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": inserted,
                "rule_id": "uk_effect_at_end_text_insertion_patch",
            }
        )

    matches_at_end_unquoted_dash_insert = re.finditer(
        r"at the end(?: of (?:(?:that|the) )?(?:paragraph|sub-paragraph|subsection|section)"
        r"(?:\s+\([^)]+\))?(?:\s+\([^)]*\))?)?,?\s+"
        r"insert\s*[—-]\s+(?P<inserted>[^.;]+?)\s*\.?\s*$",
        text,
        re.I,
    )
    for m in matches_at_end_unquoted_dash_insert:
        inserted = m.group("inserted").strip()
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": inserted,
                "rule_id": "uk_effect_at_end_unquoted_text_insertion_patch",
            }
        )

    matches_insert_at_end = re.finditer(
        r"insert at the end [“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_insert_at_end:
        inserted = m.group(1).strip()
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": inserted,
                "rule_id": "uk_effect_at_end_text_insertion_patch",
            }
        )

    matches_insert_text_at_end = re.finditer(
        r"\binsert(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]"
        r"\s+at\s+the\s+end(?:\s+of\s+[^.;]+)?",
        text,
        re.I,
    )
    for m in matches_insert_text_at_end:
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": m.group("inserted").strip(),
                "rule_id": "uk_effect_insert_text_at_end_patch",
            }
        )

    matches_passive_insert_text_at_end = re.finditer(
        r"(?:the\s+)?words?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]\s+"
        r"(?:is|are|shall\s+be)\s+inserted\s+"
        r"at\s+the\s+end(?:\s+of\s+[^.;]+)?",
        text,
        re.I,
    )
    for m in matches_passive_insert_text_at_end:
        subs.append(
            {
                "original": "TEXT_FROM__TO_END",
                "replacement": m.group("inserted").strip(),
                "rule_id": "uk_effect_passive_insert_text_at_end_patch",
            }
        )

    matches_leave_out_and_insert = re.finditer(
        r"\bleave out\s+[“\"'‘](?P<original>.*?)[”\"'’]\s+"
        r"and insert\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_leave_out_and_insert:
        subs.append(
            {
                "original": m.group("original").strip(),
                "replacement": m.group("replacement").strip(),
                "rule_id": "uk_effect_leave_out_and_insert_text_patch",
            }
        )

    matches_imperative_contextual_word_omission = re.finditer(
        r"\bomit\s+(?:the\s+)?(?:word\s+)?[“\"'‘](.*?)[”\"'’]\s+"
        r"((?:immediately\s+)?(?:preceding|following)|after|before)\s+"
        r"(paragraph|sub-paragraph|subsection)\s+\(([0-9A-Za-z]+)\)",
        text,
        re.I,
    )
    for m in matches_imperative_contextual_word_omission:
        relation = m.group(2).lower()
        relation_key = (
            "PRECEDING"
            if relation in {"preceding", "immediately preceding", "before"}
            else "FOLLOWING"
        )
        unit_kind = m.group(3).lower().replace("-", "")
        subs.append(
            {
                "original": (
                    f"TEXT_WORD_{m.group(1).strip()}_IMMEDIATELY_"
                    f"{relation_key}_{unit_kind}_{m.group(4).strip()}"
                ),
                "replacement": "",
                "rule_id": "uk_effect_contextual_adjacent_word_omit_text_patch",
            }
        )

    # Pattern 2: Omission from A to B
    matches_direct_quoted_word_omission = re.finditer(
        r"\bomit\s+(?:the\s+)?(?:words?\s+)?[“\"'‘](.*?)[”\"'’]"
        r"(?!\s+(?:immediately\s+)?(?:preceding|following|after|before)\s+"
        r"(?:paragraph|sub-paragraph|subsection)\s+\([0-9A-Za-z]+\))"
        r"(?:\s+at the end(?: of [^.;]+)?)?",
        text,
        re.I,
    )
    for m in matches_direct_quoted_word_omission:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": "",
                "rule_id": "uk_effect_direct_quoted_word_omission_text_patch",
            }
        )

    matches_repeal_quoted_words = re.finditer(
        r"\brepeal\s+(?:the\s+)?words?\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_repeal_quoted_words:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": "",
                "rule_id": "uk_effect_repeal_quoted_words_text_patch",
            }
        )

    matches_repeal_range = re.finditer(
        r"(?:the\s+)?words?\s+from\s+[“\"'‘](?P<start>.*?)[”\"'’]"
        r"(?:\s+\(\s*where\s+(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+occurring\s*\))?"
        r"\s+to\s+[“\"'‘](?P<end>.*?)[”\"'’]\s+(?:are|is)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_repeal_range:
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_{m.group('end').strip()}",
            "replacement": "",
            "rule_id": "uk_effect_range_repeal_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_occurrence_repeal_text_patch"
        subs.append(patch)

    matches_repeal_range_end_occurrence = re.finditer(
        r"(?:the\s+)?words?\s+from\s+[“\"'‘](?P<start>.*?)[”\"'’]"
        r"\s+to\s+[“\"'‘](?P<end>.*?)[”\"'’],?\s+"
        rf"where\s+(?:(?:it|they|those words?)\s+)?(?P<end_ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+"
        r"(?:occurs?|occurring|appear)s?,?\s+"
        r"(?:are|is|shall\s+be)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_repeal_range_end_occurrence:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group('start').strip()}_TO_{m.group('end').strip()}",
                "replacement": "",
                "end_occurrence": _ORDINAL_OCCURRENCES[m.group("end_ordinal").lower()],
                "rule_id": "uk_effect_range_independent_end_occurrence_repeal_text_patch",
            }
        )

    matches_passive_repeal_to_end = re.finditer(
        r"(?:the\s+)?words?\s+from\s+[“\"'‘](?P<start>.*?)[”\"'’]"
        rf"(?:,?\s+where\s+(?P<ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+occurring)?"
        r",?\s+to\s+the\s+end"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?(?:subsection|paragraph|sub-paragraph|section))?"
        r"\s+(?:are|is|shall\s+be)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_passive_repeal_to_end:
        patch = {
            "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_range_to_end_passive_repeal_text_patch",
        }
        if m.group("ordinal"):
            patch["occurrence"] = _ORDINAL_OCCURRENCES[m.group("ordinal").lower()]
            patch["rule_id"] = "uk_effect_range_to_end_passive_ordinal_repeal_text_patch"
        subs.append(patch)

    matches_passive_repeal_onwards = re.finditer(
        r"(?:the\s+)?words?\s+from\s+[“\"'‘](?P<start>.*?)[”\"'’]\s+"
        r"onwards\s+(?:are|is|shall\s+be)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_passive_repeal_onwards:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group('start').strip()}_TO_END",
                "replacement": "",
                "rule_id": "uk_effect_range_to_end_passive_repeal_text_patch",
            }
        )

    matches_omit = re.finditer(r"from [“\"'‘](.*?)[”\"'’] to [“\"'‘](.*?)[”\"'’] (?:are omitted|is omitted|omit)", text, re.I)
    for m in matches_omit:
        subs.append({"original": f"FROM_{m.group(1)}_TO_{m.group(2)}", "replacement": ""})

    matches_omit_range = re.finditer(
        r"\bomit\s+(?:(?:the\s+)?words?\s+)?from\s+[“\"'‘](.*?)[”\"'’]\s+to\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_omit_range:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group(1).strip()}_TO_{m.group(2).strip()}",
                "replacement": "",
                "rule_id": "uk_effect_omit_quoted_range_text_patch",
            }
        )

    matches_omit_after_anchor = re.finditer(
        r"\bomit\s+(?:the\s+)?words?\s+after\s+[“\"'‘](?P<anchor>.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_omit_after_anchor:
        subs.append(
            {
                "original": f"TEXT_AFTER_{m.group('anchor').strip()}_TO_END",
                "replacement": "",
                "rule_id": "uk_effect_after_anchor_to_end_omission_text_patch",
            }
        )

    matches_omit_to_end = re.finditer(
        r"omit (?:(?:the )?words? )?from [“\"'‘](.*?)[”\"'’] to the end",
        text,
        re.I,
    )
    for m in matches_omit_to_end:
        subs.append({"original": f"TEXT_FROM_{m.group(1).strip()}_TO_END", "replacement": ""})

    matches_omit_to_end_ordinal = re.finditer(
        r"(?:omit\s+)?(?:(?:the )?words? )?from [“\"'‘](.*?)[”\"'’]\s+in the\s+(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+place where it occurs to the end\s+(?:are|is)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_omit_to_end_ordinal:
        subs.append(
            {
                "original": f"TEXT_FROM_{m.group(1).strip()}_TO_END",
                "replacement": "",
                "occurrence": _ORDINAL_OCCURRENCES[m.group(2).lower()],
            }
        )

    matches_final_quoted_word_omitted = re.finditer(
        r"omit\s+(?:the\s+)?final\s+[“\"'‘](.*?)[”\"'’]",
        text,
        re.I,
    )
    for m in matches_final_quoted_word_omitted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": "",
                "occurrence": "-1",
                "rule_id": "uk_effect_final_quoted_word_omit_text_patch",
            }
        )

    matches_definition_repeal = re.finditer(
        r"(?:the )?definitions? of (?P<terms>.+?)\s+"
        r"(?:is|are|shall\s+be)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_definition_repeal:
        for term in _quoted_terms(m.group("terms")):
            subs.append(
                {
                    "original": f"TEXT_DEFINITION_ENTRY_{term}",
                    "replacement": "",
                    "rule_id": "uk_effect_definition_entry_repeal_text_patch",
                }
            )

    matches_imperative_definition_repeal = re.finditer(
        r"\bomit\s+(?:the\s+)?definitions?\s+of\s+(.+?)(?:[.;]|$)",
        text,
        re.I,
    )
    for m in matches_imperative_definition_repeal:
        for term in _quoted_terms(m.group(1)):
            subs.append(
                {
                    "original": f"TEXT_DEFINITION_ENTRY_{term}",
                    "replacement": "",
                    "rule_id": "uk_effect_definition_entry_repeal_text_patch",
                }
            )

    matches_words_are_omitted = re.finditer(
        r"(?:the )?words? [“\"'‘](.*?)[”\"'’]\s+(?:is|are)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_words_are_omitted:
        subs.append({"original": m.group(1).strip(), "replacement": ""})

    matches_words_shall_be_omitted = re.finditer(
        r"(?:the )?words? [“\"'‘](.*?)[”\"'’]\s+shall\s+be\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_words_shall_be_omitted:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": "",
                "rule_id": "uk_effect_quoted_word_passive_omit_text_patch",
            }
        )

    matches_final_word_repealed = re.finditer(
        r"(?:the\s+)?word\s+[“\"'‘](.*?)[”\"'’]\s+at the end(?: of [^.;]+)?\s+"
        r"(?:is|are)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_final_word_repealed:
        subs.append(
            {
                "original": m.group(1).strip(),
                "replacement": "",
                "occurrence": "-1",
                "rule_id": "uk_effect_final_quoted_word_repeal_text_patch",
            }
        )

    matches_final_bare_quoted_word_repealed = re.finditer(
        r"(?:the\s+)?[“\"'‘](?P<word>.*?)[”\"'’]\s+at the end(?: of [^.;]+)?\s+"
        r"(?:is|are)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_final_bare_quoted_word_repealed:
        subs.append(
            {
                "original": m.group("word").strip(),
                "replacement": "",
                "occurrence": "-1",
                "rule_id": "uk_effect_final_bare_quoted_word_repeal_text_patch",
            }
        )

    matches_contextual_word_repeal = re.finditer(
        r"(?:the )?word [“\"'‘](.*?)[”\"'’]\s+"
        r"(?:(immediately preceding|immediately following)|which (?:immediately )?follows|which appears immediately after)\s+"
        r"(paragraph|sub-paragraph|subsection)\s+\(([0-9A-Za-z]+)\)\s+"
        r"(?:is|are)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_contextual_word_repeal:
        relation = m.group(2) or "immediately following"
        relation_key = "PRECEDING" if "preceding" in relation.lower() else "FOLLOWING"
        unit_kind = m.group(3).lower().replace("-", "")
        subs.append(
            {
                "original": (
                    f"TEXT_WORD_{m.group(1).strip()}_IMMEDIATELY_"
                    f"{relation_key}_{unit_kind}_{m.group(4).strip()}"
                ),
                "replacement": "",
                "rule_id": "uk_effect_contextual_adjacent_word_repeal_text_patch",
            }
        )

    matches_target_contextual_word_repeal = re.finditer(
        r"(?:the )?word [“\"'‘](.*?)[”\"'’]\s+(immediately following)\s+"
        r"(subsection|paragraph|sub-paragraph)\s+\(([0-9A-Za-z]+)\)\(([0-9A-Za-z]+)\)\s+"
        r"(?:is|are)\s+(?:omitted|repealed)",
        text,
        re.I,
    )
    for m in matches_target_contextual_word_repeal:
        unit_kind = m.group(3).lower().replace("-", "")
        if unit_kind == "subsection":
            anchor_kind = "paragraph"
        elif unit_kind == "paragraph":
            anchor_kind = "subparagraph"
        else:
            anchor_kind = "item"
        subs.append(
            {
                "original": f"TEXT_WORD_{m.group(1).strip()}_IMMEDIATELY_FOLLOWING_{anchor_kind}_{m.group(5).strip()}",
                "replacement": "",
                "rule_id": "uk_effect_contextual_nested_word_repeal_text_patch",
            }
        )

    # Pattern 3: Reversed-order substitution: substitute "X" for "Y"
    # Requires that the original (after "for") starts with a quote character —
    # this prevents false positives when "for" appears inside the replacement text,
    # e.g. 'substitute "the Commissioner for Public Appointments" ...' would
    # otherwise split on the "for" inside the quoted string.
    if not subs:
        m = re.search(r"substitute (.*?) for ([\"'\u201c\u201d\u2018\u2019].*)", text, re.I)
        if m:
            subs.append({"original": m.group(2).strip(), "replacement": m.group(1).strip()})

    subs = _mark_compound_lettered_text_patches(text, subs)
    return tuple(tuple(sub.items()) for sub in _deduplicate_fragment_substitutions(subs))

def is_whole_node_replacement(text: str, effect_type: str) -> bool:
    """
    Decide if the text implies a whole node replacement or a word-level change.
    """
    if "word" in effect_type.lower():
        return False

    # If text contains "for ... substitute ...", it's likely a fragment
    if parse_fragment_substitution(text):
        return False

    return True
