"""UK source-table elaboration helpers."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import weakref
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag, _text_content


_UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_quoted_words_text_repeal"
)
_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_definition_entry_text_repeal"
)
_UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_definition_child_text_repeal"
)
_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID = "uk_effect_repeal_table_structural_repeal"
_UK_DEFINITION_SELECTOR_SEPARATOR = "\x1f"
_REPEAL_EXTENT_TABLE_CACHE: weakref.WeakKeyDictionary[
    ET.Element,
    tuple[tuple[ET.Element, tuple[int, int]], ...],
] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class _UKTableDrivenWordSubstitution:
    recognized: bool
    original: Optional[str] = None
    replacement: Optional[str] = None
    reason_code: str = ""
    match_count: int = 0
    table_index: int = -1
    row_text: str = ""


@dataclass(frozen=True)
class UKTableWordSubstitutionLowering:
    recognized: bool
    skip_effect: bool
    curr_action: Optional[str]
    content_ir: Optional[dict[str, Any]]
    fragment_subs: Optional[list[dict[str, str]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]


@dataclass(frozen=True)
class _UKRepealTableQuotedWordsTextRepeal:
    recognized: bool
    original: Optional[str] = None
    additional_originals: tuple[str, ...] = ()
    rule_id: str = _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID
    reason_code: str = ""
    match_count: int = 0
    table_index: int = -1
    row_text: str = ""
    enactment_cell: str = ""
    extent_cell: str = ""
    enactment_match_basis: str = ""
    occurrence: int = 0
    end_occurrence: int = 0


@dataclass(frozen=True)
class _UKRepealTableStructuralRepeal:
    recognized: bool
    reason_code: str = ""
    match_count: int = 0
    table_index: int = -1
    row_text: str = ""
    enactment_cell: str = ""
    extent_cell: str = ""
    enactment_match_basis: str = ""
    broad_container_target: str = ""


def _strip_outer_uk_quotes(text: str) -> str:
    stripped = " ".join(text.split()).strip()
    quote_pairs = (("\u201c", "\u201d"), ("\u2018", "\u2019"), ('"', '"'), ("'", "'"))
    for left, right in quote_pairs:
        if stripped.startswith(left) and stripped.endswith(right) and len(stripped) >= 2:
            return stripped[1:-1].strip()
    return stripped


def _uk_effect_act_slug(effect: UKEffectRecord) -> str:
    cls_map = {
        "UnitedKingdomPublicGeneralAct": "ukpga",
        "UnitedKingdomStatutoryInstrument": "uksi",
        "WelshParliamentAct": "asc",
        "WelshStatutoryInstrument": "wsi",
        "ScottishAct": "asp",
        "ScottishStatutoryInstrument": "ssi",
        "NorthernIrelandAssemblyMeasure": "mnia",
        "NorthernIrelandParliamentAct": "apni",
        "NorthernIrelandStatutoryRule": "nisr",
        "UnitedKingdomChurchInstrument": "ukci",
        "UnitedKingdomMinisterialOrder": "ukmo",
        "EuropeanUnionRegulation": "eur",
        "EuropeanUnionDecision": "eudn",
        "EuropeanUnionDirective": "eudr",
    }
    return cls_map.get(effect.affected_class, effect.affected_class.lower())


def _normalize_uk_enactment_title(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().lower()
    if normalized.startswith("the "):
        normalized = normalized[4:]
    normalized = re.sub(
        r"\s*\((?:asp|c\.?|s\.?\s*i\.?|si|uksi)\s*[^)]*\)\s*$",
        "",
        normalized,
        flags=re.I,
    )
    return normalized.rstrip(".")


def _uk_repeal_table_enactment_match_basis(cell_text: str, effect: UKEffectRecord) -> str:
    """Conservatively match a repeal-table enactment cell to the affected Act."""
    text = " ".join(cell_text.split()).lower()
    if not text:
        return ""
    year = str(effect.affected_year or "").strip()
    number = str(effect.affected_number or "").strip()
    slug = _uk_effect_act_slug(effect)
    if not year or not number or year not in text:
        return ""
    num_pat = re.escape(number.lower())
    if slug == "asp":
        if re.search(r"\basp\s*\d+\b", text):
            return (
                "explicit_short_citation"
                if re.search(rf"\basp\s*{num_pat}\b", text) is not None
                else ""
            )
    if slug == "ukpga":
        if re.search(r"\bc\.?\s*\d+\b", text):
            return (
                "explicit_short_citation"
                if re.search(rf"\bc\.?\s*{num_pat}\b", text) is not None
                else ""
            )
    if slug == "uksi":
        if re.search(r"\b(?:s\.?\s*i\.?|si|uksi)\s*\d{4}\s*/\s*\d+\b", text):
            return (
                "explicit_short_citation"
                if re.search(
                    rf"\b(?:s\.?\s*i\.?|si|uksi)\s*{re.escape(year)}\s*/\s*{num_pat}\b",
                    text,
                )
                is not None
                else ""
            )
    else:
        if re.search(rf"\b{re.escape(slug)}\s*\d+\b", text):
            return (
                "explicit_short_citation"
                if re.search(rf"\b{re.escape(slug)}\s*{num_pat}\b", text) is not None
                else ""
            )

    affected_title = _normalize_uk_enactment_title(effect.affected_title)
    cell_title = _normalize_uk_enactment_title(cell_text)
    if affected_title and cell_title == affected_title:
        return "exact_affected_title_year"
    return ""


def _uk_repeal_table_enactment_matches_effect(cell_text: str, effect: UKEffectRecord) -> bool:
    return bool(_uk_repeal_table_enactment_match_basis(cell_text, effect))


def _uk_repeal_table_columns(row: Sequence[str]) -> tuple[int, int] | None:
    enactment_idx: int | None = None
    extent_idx: int | None = None
    for idx, cell in enumerate(row):
        text = " ".join(cell.split()).lower()
        if not text:
            continue
        if extent_idx is None and "extent of repeal" in text:
            extent_idx = idx
        if enactment_idx is None and (
            "enactment" in text
            or "provision" in text
            or "short title and chapter" in text
            or "title and chapter" in text
            or "reference" in text
            or "chapter" in text
        ):
            enactment_idx = idx
    if enactment_idx is None or extent_idx is None or enactment_idx == extent_idx:
        return None
    return enactment_idx, extent_idx


def _uk_repeal_table_quoted_words_selector(extent_cell: str) -> tuple[str, int, int]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return "", 0, 0
    quoted = r"(?:\u201c(?P<{name}_curly>.*?)\u201d|\"(?P<{name}_double>.*?)\"|'(?P<{name}_single>.*?)')"
    range_match = re.search(
        r"\bthe\s+words?\s+from\s+"
        + quoted.format(name="start")
        + r"(?:,?\s+where\s+(?:they|it|the\s+words?)\s+"
        r"(?P<occurrence>firstly|first|1st|secondly|second|2nd|thirdly|third|3rd|fourthly|fourth|4th|fifthly|fifth|5th)"
        r"\s+occurs?)?"
        r",?\s+to\s+"
        r"(?:(?:the\s+)?end|"
        + quoted.format(name="end")
        + r")",
        text,
        re.I,
    )
    if range_match is not None:
        start = next(
            group.strip()
            for group in (
                range_match.group("start_curly"),
                range_match.group("start_double"),
                range_match.group("start_single"),
            )
            if group is not None
        )
        end = next(
            (
                group.strip()
                for group in (
                    range_match.group("end_curly"),
                    range_match.group("end_double"),
                    range_match.group("end_single"),
                )
                if group is not None
            ),
            "",
        )
        start = " ".join(start.split()).strip()
        end = " ".join(end.split()).strip()
        if not start:
            return "", 0, 0
        occurrence = 0
        if range_match.group("occurrence"):
            occurrence = _uk_ordinal_to_int(range_match.group("occurrence")) or 0
        if end:
            return f"TEXT_FROM_{start}_TO_{end}", occurrence, 0
        return f"TEXT_FROM_{start}_TO_END", occurrence, 0
    match = re.search(
        r"\bthe\s+words?\s+"
        r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\"|'(?P<single>.*?)')",
        text,
        re.I,
    )
    if match is None:
        return "", 0, 0
    original = next(
        group.strip()
        for group in (
            match.group("curly"),
            match.group("double"),
            match.group("single"),
        )
        if group is not None
    )
    return " ".join(original.split()).strip(), 0, 0


def _uk_repeal_table_definition_entry_selectors(extent_cell: str) -> tuple[str, ...]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return ()
    match = re.search(
        r"\b(?:the\s+)?(?:definition\s+of|entry\s+for)\s+"
        r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\"|'(?P<single>.*?)')",
        text,
        re.I,
    )
    if match is not None:
        tail = text[match.end() :]
        if re.search(r"\b(?:paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|head|heads)\b", tail, re.I):
            return ()
        term = next(
            group.strip()
            for group in (
                match.group("curly"),
                match.group("double"),
                match.group("single"),
            )
            if group is not None
        )
        term = " ".join(term.split()).strip()
        return (f"TEXT_DEFINITION_ENTRY_{term}",) if term else ()
    plural_match = re.search(
        r"\b(?:the\s+)?entries\s+for\s+(?P<body>.+?)(?:\.|$)",
        text,
        re.I,
    )
    if plural_match is None:
        return ()
    body = plural_match.group("body")
    if re.search(r"\b(?:paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|head|heads)\b", body, re.I):
        return ()
    terms: list[str] = []
    for term_match in re.finditer(
        r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\"|'(?P<single>.*?)')",
        body,
    ):
        term = next(
            group.strip()
            for group in (
                term_match.group("curly"),
                term_match.group("double"),
                term_match.group("single"),
            )
            if group is not None
        )
        term = " ".join(term.split()).strip()
        if term:
            terms.append(f"TEXT_DEFINITION_ENTRY_{term}")
    return tuple(terms)


def _uk_repeal_table_definition_child_selectors(extent_cell: str) -> tuple[str, ...]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return ()
    match = re.search(
        r"\bin\s+the\s+definition\s+of\s+"
        r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\"|'(?P<single>.*?)')"
        r",?\s+(?P<kind>paragraphs?|sub-?paragraphs?)\s+(?P<labels>[^.;]+)",
        text,
        re.I,
    )
    if match is None:
        return ()
    term = next(
        group.strip()
        for group in (
            match.group("curly"),
            match.group("double"),
            match.group("single"),
        )
        if group is not None
    )
    term = " ".join(term.split()).strip()
    if not term:
        return ()
    labels_text = match.group("labels")
    label_matches = list(re.finditer(r"\(\s*(?P<label>[0-9A-Za-z]+)\s*\)", labels_text))
    if not label_matches:
        return ()
    label_remainder = re.sub(r"\(\s*[0-9A-Za-z]+\s*\)", "", labels_text)
    label_remainder = re.sub(r"\b(?:and|or)\b|[,()\s]", "", label_remainder, flags=re.I)
    if label_remainder:
        return ()
    # Nested labels such as `(a)(iii)` need a separate nested-child ownership rule.
    compact = re.sub(r"\s+", "", labels_text)
    if re.search(r"\([0-9A-Za-z]+\)\([0-9A-Za-z]+\)", compact):
        return ()
    kind = "SUBPARAGRAPH" if "sub" in match.group("kind").lower() else "PARAGRAPH"
    return tuple(
        f"TEXT_DEFINITION_CHILD_{kind}_{term}{_UK_DEFINITION_SELECTOR_SEPARATOR}"
        f"{label_match.group('label').strip()}"
        for label_match in label_matches
    )


def _uk_repeal_table_extent_clauses(extent_cell: str) -> list[str]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return []
    clauses = re.split(
        r"(?<=\.)\s+(?=(?:In\s+)?(?:part|parts|chapter|chapters|section|sections|schedule|paragraph)\b)",
        text,
        flags=re.I,
    )
    expanded: list[str] = []
    for clause in clauses:
        stripped = clause.strip()
        if not stripped:
            continue
        section_context = re.match(
            r"^(?P<context>In\s+section\s+[0-9A-Za-z]+)\s*,\s*(?P<body>.+)$",
            stripped,
            re.I,
        )
        if section_context is None:
            expanded.append(stripped)
            continue
        parts = re.split(
            r";\s+and\s+(?=in\s+(?:subsection|paragraph|sub-paragraph)\b)",
            section_context.group("body"),
            flags=re.I,
        )
        if len(parts) == 1:
            expanded.append(stripped)
            continue
        context = section_context.group("context")
        for part in parts:
            part_text = part.strip()
            if part_text:
                expanded.append(f"{context}, {part_text}".rstrip(".") + ".")
    return expanded


def _uk_repeal_table_gateway_text(text: Optional[str]) -> bool:
    """Return true when prose points outward to a repeal extent table."""
    norm = " ".join((text or "").split()).lower()
    if not norm:
        return False
    if "extent specified" in norm and (
        "schedule" in norm or "column" in norm or "table" in norm
    ):
        return True
    return "enactments specified" in norm and (
        "extent" in norm or "column" in norm or "schedule" in norm
    )


def _uk_repeal_schedule_source_text(text: Optional[str]) -> bool:
    """Return true when the extracted source itself is a repeal schedule/table."""
    norm = " ".join((text or "").split()).lower()
    if not norm:
        return False
    if not re.search(r"\brepeals?\b|\brevocations?\b", norm[:240]):
        return False
    return (
        re.search(r"\bextent\s+of\s+(?:repeal|revocation)", norm) is not None
        or re.search(r"\benactments?\b", norm[:400]) is not None
        or re.search(r"\bsection\s+\d+\b", norm[:400]) is not None
    )


def _uk_repeal_extent_search_roots(
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
) -> list[ET.Element]:
    """Select source roots that are legally relevant to repeal-table lookup."""
    search_roots: list[ET.Element] = []
    if extracted_el is not None:
        search_roots.append(extracted_el)
    if (
        source_root is not None
        and source_root is not extracted_el
        and _uk_repeal_table_gateway_text(extracted_text)
    ):
        search_roots.append(source_root)
    return search_roots


def _uk_table_is_repeal_extent_source_table(
    table: ET.Element,
    *,
    source_is_repeal_schedule: bool = False,
) -> tuple[int, int] | None:
    for row in _uk_table_rows_with_rowspans(table)[:4]:
        columns = _uk_repeal_table_columns(row)
        if columns is not None:
            return columns
    if source_is_repeal_schedule:
        for row in _uk_table_rows_with_rowspans(table)[:8]:
            if len(row) < 2:
                continue
            extent_cell = " ".join(row[1].split()).lower()
            if re.search(
                r"\b(?:section|sections|schedule|schedules|paragraph|paragraphs|"
                r"subsection|subsections|sub-?paragraph|sub-?paragraphs|word|words)\b",
                extent_cell,
            ):
                return 0, 1
    return None


def _uk_repeal_extent_source_tables(root: ET.Element) -> tuple[tuple[ET.Element, tuple[int, int]], ...]:
    cached = _REPEAL_EXTENT_TABLE_CACHE.get(root)
    if cached is not None:
        return cached
    tables: list[tuple[ET.Element, tuple[int, int]]] = []
    source_is_repeal_schedule = _uk_repeal_schedule_source_text(_text_content(root))
    for el in root.iter():
        if _tag(el).lower() != "table":
            continue
        columns = _uk_table_is_repeal_extent_source_table(
            el,
            source_is_repeal_schedule=source_is_repeal_schedule,
        )
        if columns is not None:
            tables.append((el, columns))
    result = tuple(tables)
    _REPEAL_EXTENT_TABLE_CACHE[root] = result
    return result


def _uk_repeal_extent_source_tables_for_roots(
    roots: Sequence[ET.Element],
) -> tuple[tuple[ET.Element, tuple[int, int]], ...]:
    tables: list[tuple[ET.Element, tuple[int, int]]] = []
    seen_table_ids: set[int] = set()
    for root in roots:
        for table, columns in _uk_repeal_extent_source_tables(root):
            table_id = id(table)
            if table_id in seen_table_ids:
                continue
            seen_table_ids.add(table_id)
            tables.append((table, columns))
    return tuple(tables)


def _uk_table_driven_repeal_table_quoted_words_text_repeal(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
    allow_structural_definition_entry: bool = False,
) -> _UKRepealTableQuotedWordsTextRepeal:
    """Resolve bounded repeal-schedule rows to quoted word-level text deletes."""
    effect_type = str(effect.effect_type or "").strip().lower()
    word_effect = effect_type in {
        "words repealed",
        "word repealed",
        "words omitted",
        "word omitted",
    }
    structural_definition_entry_effect = allow_structural_definition_entry and effect_type in {
        "repealed",
        "omitted",
        "revoked",
    }
    if not word_effect and not structural_definition_entry_effect:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    search_roots = _uk_repeal_extent_search_roots(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
    )
    if not search_roots:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)

    matches: list[tuple[int, str, tuple[str, ...], int, int, str, str, str, str, str]] = []
    tables = _uk_repeal_extent_source_tables_for_roots(search_roots)
    if not tables:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        last_enactment_cell = ""
        for row in rows[1:]:
            if len(row) >= max(enactment_idx, extent_idx) + 1:
                enactment_cell = row[enactment_idx]
                extent_cell = row[extent_idx]
                if enactment_cell:
                    last_enactment_cell = enactment_cell
                elif last_enactment_cell:
                    enactment_cell = last_enactment_cell
            elif len(row) == 1 and last_enactment_cell:
                enactment_cell = last_enactment_cell
                extent_cell = row[0]
            else:
                continue
            enactment_match_basis = _uk_repeal_table_enactment_match_basis(
                enactment_cell,
                effect,
            )
            if not enactment_match_basis:
                continue
            for extent_clause in _uk_repeal_table_extent_clauses(extent_cell):
                if not _uk_table_cell_mentions_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                ):
                    continue
                additional_originals: tuple[str, ...] = ()
                rule_id = _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID
                target_kinds = {kind.lower() for kind, _ in target.path}
                original = ""
                occurrence = 0
                end_occurrence = 0
                if not structural_definition_entry_effect:
                    (
                        original,
                        occurrence,
                        end_occurrence,
                    ) = _uk_repeal_table_quoted_words_selector(extent_clause)
                if not original and "section" in target_kinds:
                    definition_originals = _uk_repeal_table_definition_entry_selectors(extent_clause)
                    if definition_originals:
                        original = definition_originals[0]
                        additional_originals = definition_originals[1:]
                        rule_id = _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID
                if (
                    not original
                    and not structural_definition_entry_effect
                    and "section" in target_kinds
                ):
                    definition_child_originals = _uk_repeal_table_definition_child_selectors(
                        extent_clause
                    )
                    if definition_child_originals:
                        original = definition_child_originals[0]
                        additional_originals = definition_child_originals[1:]
                        rule_id = _UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID
                if not original:
                    continue
                matches.append(
                    (
                        table_index,
                        original,
                        additional_originals,
                        occurrence,
                        end_occurrence,
                        rule_id,
                        " | ".join((enactment_cell, extent_clause)),
                        enactment_cell,
                        extent_clause,
                        enactment_match_basis,
                    )
                )

    if len(matches) != 1:
        return _UKRepealTableQuotedWordsTextRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_row",
            match_count=len(matches),
        )

    (
        table_index,
        original,
        additional_originals,
        occurrence,
        end_occurrence,
        rule_id,
        row_text,
        enactment_cell,
        extent_cell,
        enactment_match_basis,
    ) = matches[0]
    return _UKRepealTableQuotedWordsTextRepeal(
        recognized=True,
        original=original,
        additional_originals=additional_originals,
        rule_id=rule_id,
        reason_code="",
        match_count=1,
        table_index=table_index,
        row_text=row_text,
        enactment_cell=enactment_cell,
        extent_cell=extent_cell,
        enactment_match_basis=enactment_match_basis,
        occurrence=occurrence,
        end_occurrence=end_occurrence,
    )


def _uk_repeal_table_clause_is_structural_repeal(extent_clause: str) -> bool:
    """Return true for repeal-table clauses that claim whole target provisions."""
    text = " ".join((extent_clause or "").split()).strip()
    if not text:
        return False
    norm = text.lower()
    if re.search(r"\b(?:word|words|definition|entry|entries)\b", norm):
        return False
    return bool(
        re.search(
            r"\b(?:part|parts|chapter|chapters|section|sections|schedule|schedules|paragraph|paragraphs|"
            r"subsection|subsections|sub-?paragraph|sub-?paragraphs)\b",
            norm,
        )
    )


def _uk_repeal_table_mixed_clause_explicitly_names_structural_target(
    extent_clause: str,
    *,
    target: LegalAddress,
) -> bool:
    """Return true when a mixed word/structural repeal row separately names target."""
    label = target.leaf_label().strip()
    if not label:
        return False
    kind = target.leaf_kind().strip().lower()
    kind_patterns = {
        "section": r"sections?",
        "subsection": r"subsections?",
        "schedule": r"schedules?",
        "paragraph": r"paragraphs?",
        "subparagraph": r"sub-?paragraphs?",
    }
    kind_pattern = kind_patterns.get(kind)
    if kind_pattern is None:
        return False
    scope_text = " ".join((extent_clause or "").split())
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", scope_text)
    if kind in {"subsection", "paragraph", "subparagraph"}:
        label_pattern = rf"\(\s*{re.escape(label)}\s*\)(?=$|[\s,.;])"
    else:
        label_pattern = rf"{re.escape(label)}\b"
    return re.search(
        rf"(?:[,;]|\band\b)\s*(?:the\s+)?{kind_pattern}\s*{label_pattern}",
        scope_text,
        flags=re.I,
    ) is not None


def _uk_table_driven_repeal_table_structural_repeal(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> _UKRepealTableStructuralRepeal:
    """Resolve repeal-schedule rows that exactly corroborate a provision repeal."""
    effect_type = str(effect.effect_type or "").strip().lower()
    source_supplies_repeal_action = (
        not effect_type and _uk_repeal_schedule_source_text(extracted_text)
    )
    if effect_type not in {"repealed", "omitted", "revoked"} and not source_supplies_repeal_action:
        return _UKRepealTableStructuralRepeal(recognized=False)
    if str(target.special or "") == "whole_act":
        return _UKRepealTableStructuralRepeal(recognized=False)
    search_roots = _uk_repeal_extent_search_roots(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
    )
    if not search_roots:
        return _UKRepealTableStructuralRepeal(recognized=False)

    matches: list[tuple[int, str, str, str, str, str]] = []
    mixed_structural_word_matches: list[tuple[int, str, str, str, str]] = []
    broad_container_matches: list[tuple[int, str, str, str, str, str]] = []
    tables = _uk_repeal_extent_source_tables_for_roots(search_roots)
    if not tables:
        return _UKRepealTableStructuralRepeal(recognized=False)
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        last_enactment_cell = ""
        for row in rows[1:]:
            if len(row) >= max(enactment_idx, extent_idx) + 1:
                enactment_cell = row[enactment_idx]
                extent_cell = row[extent_idx]
                if enactment_cell:
                    last_enactment_cell = enactment_cell
                elif last_enactment_cell:
                    enactment_cell = last_enactment_cell
            elif len(row) == 1 and last_enactment_cell:
                enactment_cell = last_enactment_cell
                extent_cell = row[0]
            else:
                continue
            enactment_match_basis = _uk_repeal_table_enactment_match_basis(
                enactment_cell,
                effect,
            )
            if not enactment_match_basis:
                continue
            for extent_clause in _uk_repeal_table_extent_clauses(extent_cell):
                if _uk_table_cell_explicitly_excepts_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                ):
                    continue
                container_except_reason_code = ""
                source_mentions_target = _uk_table_cell_mentions_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                )
                if not source_mentions_target:
                    if _uk_table_cell_mentions_target_via_container_except(
                        extent_clause,
                        target=target,
                        affected_year=str(effect.affected_year or ""),
                    ):
                        source_mentions_target = True
                        container_except_reason_code = (
                            "container_except_extent_row_feed_descendant_repeal"
                        )
                if not source_mentions_target:
                    broad_container_target = _uk_table_cell_mentions_target_ancestor_container(
                        extent_clause,
                        target=target,
                        affected_year=str(effect.affected_year or ""),
                    )
                    if _uk_repeal_table_clause_is_structural_repeal(
                        extent_clause
                    ) and broad_container_target:
                        broad_container_matches.append(
                            (
                                table_index,
                                " | ".join((enactment_cell, extent_clause)),
                                enactment_cell,
                                extent_clause,
                                enactment_match_basis,
                                broad_container_target,
                            )
                        )
                    continue
                if not _uk_repeal_table_clause_is_structural_repeal(extent_clause):
                    norm_clause = " ".join(extent_clause.split()).lower()
                    if re.search(r"\b(?:word|words)\b", norm_clause) and re.search(
                        r"\b(?:section|sections|schedule|schedules|paragraph|paragraphs|"
                        r"subsection|subsections|sub-?paragraph|sub-?paragraphs)\b",
                        norm_clause,
                    ):
                        if _uk_repeal_table_mixed_clause_explicitly_names_structural_target(
                            extent_clause,
                            target=target,
                        ):
                            matches.append(
                                (
                                    table_index,
                                    " | ".join((enactment_cell, extent_clause)),
                                    enactment_cell,
                                    extent_clause,
                                    enactment_match_basis,
                                    "mixed_structural_and_word_repeal_split_structural_target",
                                )
                            )
                        else:
                            mixed_structural_word_matches.append(
                                (
                                    table_index,
                                    " | ".join((enactment_cell, extent_clause)),
                                    enactment_cell,
                                    extent_clause,
                                    enactment_match_basis,
                                )
                            )
                    continue
                matches.append(
                    (
                        table_index,
                        " | ".join((enactment_cell, extent_clause)),
                        enactment_cell,
                        extent_clause,
                        enactment_match_basis,
                        container_except_reason_code
                        or (
                            "source_repeal_schedule_structural_repeal"
                            if source_supplies_repeal_action
                            else ""
                        ),
                    )
                )

    if len(matches) != 1:
        if not matches and len(mixed_structural_word_matches) == 1:
            (
                table_index,
                row_text,
                enactment_cell,
                extent_cell,
                enactment_match_basis,
            ) = mixed_structural_word_matches[0]
            return _UKRepealTableStructuralRepeal(
                recognized=True,
                reason_code="mixed_structural_and_word_repeal_requires_split",
                match_count=0,
                table_index=table_index,
                row_text=row_text,
                enactment_cell=enactment_cell,
                extent_cell=extent_cell,
                enactment_match_basis=enactment_match_basis,
            )
        if not matches and len(broad_container_matches) == 1:
            (
                table_index,
                row_text,
                enactment_cell,
                extent_cell,
                enactment_match_basis,
                broad_container_target,
            ) = broad_container_matches[0]
            return _UKRepealTableStructuralRepeal(
                recognized=True,
                reason_code="broad_container_repeal_requires_grouped_feed_compilation",
                match_count=0,
                table_index=table_index,
                row_text=row_text,
                enactment_cell=enactment_cell,
                extent_cell=extent_cell,
                enactment_match_basis=enactment_match_basis,
                broad_container_target=broad_container_target,
            )
        return _UKRepealTableStructuralRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_structural_row",
            match_count=len(matches),
        )

    (
        table_index,
        row_text,
        enactment_cell,
        extent_cell,
        enactment_match_basis,
        reason_code,
    ) = matches[0]
    return _UKRepealTableStructuralRepeal(
        recognized=True,
        reason_code=reason_code,
        match_count=1,
        table_index=table_index,
        row_text=row_text,
        enactment_cell=enactment_cell,
        extent_cell=extent_cell,
        enactment_match_basis=enactment_match_basis,
    )


def _uk_parenthetical_labels(text: str) -> list[str]:
    return [match.group(1).strip().lower() for match in re.finditer(r"\(\s*([^)]+?)\s*\)", text)]


def _uk_parenthetical_labels_contain_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle:
        return True
    if len(needle) > len(haystack):
        return False
    for idx in range(0, len(haystack) - len(needle) + 1):
        if list(haystack[idx : idx + len(needle)]) == list(needle):
            return True
    return False


def _uk_schedule_in_cell_text(text: str, schedule_pat: str) -> bool:
    is_numeric = schedule_pat.isdigit()
    wanted_num = int(schedule_pat) if is_numeric else None

    for m in re.finditer(r"\b(?:schedule|schedules|sch|schs)\b\.?", text):
        start_idx = m.end()
        next_boundary = len(text)
        boundary_pat = r"\b(?:part|pt|section|sections|s|paragraph|paragraphs|para|paras)\b\.?|;"
        bm = re.search(boundary_pat, text[start_idx:])
        if bm:
            next_boundary = start_idx + bm.start()

        segment = text[start_idx:next_boundary]
        if re.search(rf"\b{schedule_pat}\b", segment):
            return True

        if is_numeric:
            for match in re.finditer(
                r"\b(?P<start>\d+)\s*(?:to|-|–|—)\s*(?P<end>\d+)\b",
                segment,
            ):
                start = int(match.group("start"))
                end = int(match.group("end"))
                low = min(start, end)
                high = max(start, end)
                if low <= wanted_num <= high:
                    return True
    return False


def _uk_cell_has_section_descendant_scope(
    text: str,
    *,
    section: str,
    descendant_labels: Sequence[str],
) -> bool:
    """Return true when descendant labels belong to the requested section."""
    scope_text = " ".join(text.split()).lower()
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", scope_text)
    section_pat = re.escape(section.lower())
    wanted = [label.lower() for label in descendant_labels if label]
    if not wanted:
        return True

    explicit_ref_re = re.compile(rf"\b{section_pat}\s*((?:\([^)]*\)\s*)+)", re.I)
    for match in explicit_ref_re.finditer(scope_text):
        if _uk_parenthetical_labels_contain_sequence(
            _uk_parenthetical_labels(match.group(1)),
            wanted,
        ):
            return True

    for match in re.finditer(rf"\b{section_pat}\b", scope_text):
        start_idx = match.end()
        next_boundary = len(scope_text)
        boundary_matches = list(re.finditer(
            r"\b(?:section|sections|s|schedule|schedules|sch|schs|part|pt|chapter|ch|paragraph|paragraphs|para|paras)\.?\s+(?:\d+|[a-zA-Z]\b)",
            scope_text[start_idx:]
        ))
        for bm in boundary_matches:
            bm_text = bm.group(0)
            if any(w in bm_text for w in wanted):
                continue
            next_boundary = start_idx + bm.start()
            break

        segment = scope_text[start_idx:next_boundary]
        current_pos = 0
        matched_all = True
        for label in wanted:
            if label == "proviso":
                m = re.search(r"\bproviso\b", segment[current_pos:])
                if not m:
                    matched_all = False
                    break
                current_pos += m.end()
            else:
                pat = rf"(?:\(\s*{re.escape(label)}\s*\)|\b{re.escape(label)}\b)"
                m = re.search(pat, segment[current_pos:])
                if not m:
                    matched_all = False
                    break
                current_pos += m.end()
        if matched_all:
            return True

    return False


def _uk_table_cell_mentions_target_ancestor_container(
    cell_text: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> str:
    """Return a matched source-owned ancestor when the feed target is a descendant."""
    labels = {kind: label for kind, label in target.path}
    ancestor_candidates: list[LegalAddress] = []
    section = labels.get("section", "")
    subsection = labels.get("subsection", "")
    schedule = labels.get("schedule", "")
    paragraph = labels.get("paragraph", "")
    subparagraph = labels.get("subparagraph", "")
    if schedule and (paragraph or subparagraph):
        ancestor_candidates.append(LegalAddress(path=(("schedule", schedule),)))
    if section and (subsection or paragraph or subparagraph):
        ancestor_candidates.append(LegalAddress(path=(("section", section),)))
    if section and subsection and (paragraph or subparagraph):
        ancestor_candidates.append(
            LegalAddress(path=(("section", section), ("subsection", subsection)))
        )
    if not ancestor_candidates:
        return ""

    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", " ".join(cell_text.split()).lower())
    if re.search(r"\b(?:paragraphs?|paras?\.?|sub-?paragraphs?|subsections?)\b", scope_text):
        return ""
    for ancestor in ancestor_candidates:
        if _uk_table_cell_mentions_target(
            cell_text,
            target=ancestor,
            affected_year=affected_year,
        ):
            return str(ancestor)
    return ""


def _uk_table_cell_mentions_target_via_container_except(
    cell_text: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> bool:
    """Match rows like `Schedule 12, except paragraphs 17 and 18` to child feed rows."""
    labels = {kind: label for kind, label in target.path}
    paragraph = labels.get("paragraph", "")
    body = _uk_schedule_paragraph_exception_body(
        cell_text,
        target=target,
        affected_year=affected_year,
    )
    wanted = _clean_num(paragraph)
    return bool(body and wanted) and not _uk_container_label_body_mentions_label(
        body,
        wanted,
    )


def _uk_table_cell_explicitly_excepts_target(
    cell_text: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> bool:
    labels = {kind: label for kind, label in target.path}
    paragraph = labels.get("paragraph", "")
    body = _uk_schedule_paragraph_exception_body(
        cell_text,
        target=target,
        affected_year=affected_year,
    )
    wanted = _clean_num(paragraph)
    return bool(body and wanted) and _uk_container_label_body_mentions_label(
        body,
        wanted,
    )


def _uk_schedule_paragraph_exception_body(
    cell_text: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> str:
    labels = {kind: label for kind, label in target.path}
    schedule = labels.get("schedule", "")
    paragraph = labels.get("paragraph", "")
    if not schedule or not paragraph:
        return ""
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", " ".join(cell_text.split()).lower())
    years = set(re.findall(r"\b(?:1[6-9]|20)\d{2}\b", scope_text))
    if years and affected_year and affected_year not in years:
        return ""
    if not _uk_schedule_in_cell_text(scope_text, re.escape(schedule.lower())):
        return ""
    exception_match = re.search(
        r"\bexcept\s+(?P<body>(?:paragraphs?|paras?\.?)\s+[^.;]+)",
        scope_text,
        flags=re.I,
    )
    return exception_match.group("body") if exception_match is not None else ""


def _uk_table_cell_mentions_target(
    cell_text: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> bool:
    """Conservatively match an effect target against a source-table provision cell."""
    text = " ".join(cell_text.split()).lower()
    if not text:
        return False
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", text)
    years = set(re.findall(r"\b(?:1[6-9]|20)\d{2}\b", scope_text))
    if years and affected_year and affected_year not in years:
        return False

    labels = {kind: label for kind, label in target.path}
    section = labels.get("section", "")
    schedule = labels.get("schedule", "")
    paragraph = labels.get("paragraph", "")
    subsection = labels.get("subsection", "")
    subparagraph = labels.get("subparagraph", "")
    part = labels.get("part", "")
    chapter = labels.get("chapter", "")

    if part:
        return _uk_table_cell_mentions_container_label(
            scope_text,
            kind="part",
            label=part,
        )

    if chapter:
        return _uk_table_cell_mentions_container_label(
            scope_text,
            kind="chapter",
            label=chapter,
        )

    if section:
        section_pat = re.escape(section.lower())
        direct_section_match = re.search(
            rf"\b(?:section|sections|s)\.?\s*{section_pat}\b",
            text,
        )
        section_range_match = _uk_section_label_in_simple_range(text, section)
        section_list_match = _uk_section_label_in_simple_list(text, section)
        listed_section_match = (
            re.search(r"\bsections\b", text) is not None
            and re.search(rf"\b{section_pat}\s*\(", text) is not None
        )
        if (
            not direct_section_match
            and not section_range_match
            and not section_list_match
            and not listed_section_match
        ):
            return False
        descendant_labels = [label for label in (subsection, paragraph, subparagraph) if label]
        if descendant_labels and not _uk_cell_has_section_descendant_scope(
            text,
            section=section,
            descendant_labels=descendant_labels,
        ):
            return False
        return True

    if schedule:
        schedule_pat = re.escape(schedule.lower())
        if not _uk_schedule_in_cell_text(scope_text, schedule_pat):
            return False
        if paragraph:
            paragraph_pat = re.escape(paragraph.lower())
            if not (
                re.search(rf"\b(?:paragraph|paragraphs|para)\.?\s*{paragraph_pat}\b", text)
                or _uk_container_label_in_simple_list(
                    text,
                    label=paragraph,
                    word_pattern=r"paragraphs?|paras?\.?",
                )
            ):
                return False
        if subparagraph:
            if not (
                f"({subparagraph.lower()})" in text
                or _uk_container_label_in_simple_list(
                    text,
                    label=subparagraph,
                    word_pattern=r"sub-?paragraphs?",
                )
            ):
                return False
        return True

    return False


def _uk_table_cell_mentions_container_label(
    text: str,
    *,
    kind: str,
    label: str,
) -> bool:
    """Match a source-owned table extent against a body container target."""

    wanted = _clean_num(label)
    if not wanted:
        return False
    if kind == "part":
        word_pattern = r"(?:parts?|pts?\.?)"
    elif kind == "chapter":
        word_pattern = r"(?:chapters?|chs?\.?)"
    else:
        return False

    for match in re.finditer(
        rf"\b{word_pattern}\s+(?P<body>[^.;]+)",
        text or "",
        flags=re.I,
    ):
        body = match.group("body")
        if _uk_container_label_body_mentions_label(body, wanted):
            return True
    return False


def _uk_container_label_body_mentions_label(body: str, wanted: str) -> bool:
    tokens = [
        _clean_num(match.group(0))
        for match in re.finditer(r"\b(?:[0-9]+[a-z]?|[ivxlcdm]+)\b", body, re.I)
    ]
    if wanted in tokens:
        return True
    if not wanted.isdigit():
        return False
    wanted_int = int(wanted)
    for match in re.finditer(
        r"\b(?P<start>[0-9]+|[ivxlcdm]+)\s*(?:to|-|–|—)\s*(?P<end>[0-9]+|[ivxlcdm]+)\b",
        body or "",
        flags=re.I,
    ):
        start = _clean_num(match.group("start"))
        end = _clean_num(match.group("end"))
        if not start.isdigit() or not end.isdigit():
            continue
        low = min(int(start), int(end))
        high = max(int(start), int(end))
        if low <= wanted_int <= high:
            return True
    return False


def _uk_section_label_in_simple_range(text: str, label: str) -> bool:
    """Return true when a numeric section label falls inside `sections 26 to 31`."""

    if not re.fullmatch(r"\d+", label or ""):
        return False
    wanted = int(label)
    for match in re.finditer(
        r"\bsections?\s+(?P<start>\d+)\s*(?:to|-|–|—)\s*(?P<end>\d+)\b",
        text or "",
        flags=re.I,
    ):
        start = int(match.group("start"))
        end = int(match.group("end"))
        low = min(start, end)
        high = max(start, end)
        if low <= wanted <= high:
            return True
    return False


def _uk_section_label_in_simple_list(text: str, label: str) -> bool:
    """Return true when a numeric section label is listed in `sections 153 and 154`."""

    if not re.fullmatch(r"\d+", label or ""):
        return False
    wanted = label.strip()
    for match in re.finditer(
        r"\bsections?\s+(?P<body>[0-9,\sand]+)(?:\.|;|$)",
        text or "",
        flags=re.I,
    ):
        labels = re.findall(r"\b\d+\b", match.group("body"))
        if wanted in labels:
            return True
    return False


def _uk_container_label_in_simple_list(
    text: str,
    *,
    label: str,
    word_pattern: str,
) -> bool:
    """Return true when a label is listed in a same-kind table extent phrase."""
    wanted = _clean_num(label)
    if not wanted:
        return False
    for match in re.finditer(
        rf"\b(?:{word_pattern})\s+(?P<body>[^.;]+)",
        text or "",
        flags=re.I,
    ):
        if _uk_container_label_body_mentions_label(match.group("body"), wanted):
            return True
    return False


def _uk_table_is_column_1_2_source_table(table: ET.Element) -> bool:
    rows = _uk_table_rows_with_rowspans(table)
    for row in rows[:3]:
        header = " ".join(row).lower()
        if "column 1" in header and "column 2" in header:
            return True
    return False


def _uk_table_rows_with_rowspans(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    rowspans: dict[int, tuple[int, str]] = {}
    for row in table.iter():
        if _tag(row).lower() != "tr":
            continue
        cells: list[str] = []
        col_idx = 0
        explicit_cells = [child for child in row if _tag(child).lower() in {"td", "th", "entry"}]
        for cell in explicit_cells:
            while col_idx in rowspans:
                remaining, carried_text = rowspans[col_idx]
                cells.append(carried_text)
                if remaining <= 1:
                    del rowspans[col_idx]
                else:
                    rowspans[col_idx] = (remaining - 1, carried_text)
                col_idx += 1
            cell_text = " ".join(_text_content(cell).split())
            cells.append(cell_text)
            try:
                span = int(cell.get("rowspan") or cell.get("morerows") or "1")
            except ValueError:
                span = 1
            if _tag(cell).lower() == "entry" and cell.get("morerows"):
                span += 1
            if span > 1:
                rowspans[col_idx] = (span - 1, cell_text)
            col_idx += 1
        while col_idx in rowspans:
            remaining, carried_text = rowspans[col_idx]
            cells.append(carried_text)
            if remaining <= 1:
                del rowspans[col_idx]
            else:
                rowspans[col_idx] = (remaining - 1, carried_text)
            col_idx += 1
        if any(cells):
            rows.append(cells)
    return rows


def _uk_table_is_fee_table(table: ET.Element) -> bool:
    rows = _uk_table_rows_with_rowspans(table)
    for row in rows[:3]:
        header = " ".join(row).lower()
        if "enactment specifying fees" in header or "fee payable" in header:
            return True
    return False


def _uk_table_driven_fee_substitution(
    *,
    effect: UKEffectRecord,
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> _UKTableDrivenWordSubstitution:
    if source_root is None:
        affecting_title = (effect.affecting_title or "").lower()
        if "fee" in affecting_title or "fees" in affecting_title:
            return _UKTableDrivenWordSubstitution(
                recognized=True,
                reason_code="source_root_unavailable",
            )
        return _UKTableDrivenWordSubstitution(recognized=False)

    tables = [
        el
        for el in source_root.iter()
        if el.tag.split("}")[-1].lower() == "table" and _uk_table_is_fee_table(el)
    ]
    if not tables:
        return _UKTableDrivenWordSubstitution(recognized=False)

    affected_year = str(effect.affected_year or "")
    matches = []

    for table_index, table in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        current_chapter = ""
        current_act_title = ""
        current_provision = ""

        for row in rows:
            if len(row) < 3:
                continue

            col0 = row[0].strip()
            col1 = row[1].strip()
            col2 = row[2].strip()

            if col0:
                current_chapter = col0
                col1_lower = col1.lower()
                if not any(x in col1_lower for x in ["section", "schedule", "s.", "sch.", "para"]):
                    current_act_title = col1
                    current_provision = ""
                    continue

            if col1:
                col1_lower = col1.lower()
                if any(x in col1_lower for x in ["section", "schedule", "s.", "sch.", "para"]):
                    current_provision = col1

            if not current_provision:
                continue

            if affected_year and affected_year not in current_chapter and affected_year not in current_act_title:
                continue

            combined_cell = f"{current_provision} {col2}"
            if _uk_table_cell_mentions_target(
                combined_cell,
                target=target,
                affected_year=affected_year,
            ):
                new_fee = row[3].strip() if len(row) > 3 else ""
                old_fee = row[4].strip() if len(row) > 4 else ""
                original = f"TEXT_FEE_SUM_{old_fee}" if old_fee else "TEXT_FEE_SUM_ANY"
                if new_fee:
                    matches.append((table_index, original, new_fee, " | ".join(row[:4])))

    if len(matches) == 0:
        return _UKTableDrivenWordSubstitution(recognized=False)

    if len(matches) > 1:
        return _UKTableDrivenWordSubstitution(
            recognized=True,
            replacement=matches[0][2],
            reason_code="no_unique_matching_table_row",
            match_count=len(matches),
        )

    table_index, original, new_fee, row_text = matches[0]
    return _UKTableDrivenWordSubstitution(
        recognized=True,
        original=original,
        replacement=new_fee,
        reason_code="",
        match_count=1,
        table_index=table_index,
        row_text=row_text,
    )


def _uk_table_driven_corresponding_entry_word_substitution(
    *,
    effect: UKEffectRecord,
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> _UKTableDrivenWordSubstitution:
    """Resolve "column 1 provision / corresponding column 2 words" source tables."""
    text = " ".join((extracted_text or "").split())
    replacement_match = re.search(
        r"\bprovisions?\s+listed\s+in\s+column\s+1\b"
        r".*?\bfor\s+the\s+words\s+in\s+the\s+corresponding\s+entry\s+in\s+column\s+2\b"
        r".*?\bsubstitute\s+(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\"|'(?P<single>.*?)')",
        text,
        re.I,
    )
    if not replacement_match:
        return _UKTableDrivenWordSubstitution(recognized=False)
    if source_root is None:
        return _UKTableDrivenWordSubstitution(
            recognized=True,
            reason_code="source_root_unavailable",
        )

    replacement = next(
        group.strip()
        for group in (
            replacement_match.group("curly"),
            replacement_match.group("double"),
            replacement_match.group("single"),
        )
        if group is not None
    )
    matches: list[tuple[int, str, str]] = []
    tables = [
        el
        for el in source_root.iter()
        if _tag(el).lower() == "table" and _uk_table_is_column_1_2_source_table(el)
    ]
    for table_index, table in enumerate(tables):
        for row in _uk_table_rows_with_rowspans(table):
            if len(row) < 2:
                continue
            provision_cell = row[0]
            words_cell = row[1]
            if not _uk_table_cell_mentions_target(
                provision_cell,
                target=target,
                affected_year=str(effect.affected_year or ""),
            ):
                continue
            old_words = _strip_outer_uk_quotes(words_cell)
            if not old_words:
                continue
            matches.append((table_index, old_words, " | ".join(row[:2])))

    if len(matches) != 1:
        return _UKTableDrivenWordSubstitution(
            recognized=True,
            replacement=replacement,
            reason_code="no_unique_matching_table_row",
            match_count=len(matches),
        )

    table_index, original, row_text = matches[0]
    return _UKTableDrivenWordSubstitution(
        recognized=True,
        original=original,
        replacement=replacement,
        reason_code="",
        match_count=1,
        table_index=table_index,
        row_text=row_text,
    )


def lower_uk_table_driven_corresponding_entry_word_substitution(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    content_ir: Optional[dict[str, Any]],
    fragment_subs: Optional[list[dict[str, str]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTableWordSubstitutionLowering:
    substitution = _uk_table_driven_corresponding_entry_word_substitution(
        effect=effect,
        extracted_text=extracted_text,
        source_root=source_root,
        target=target,
    )
    if not substitution.recognized:
        substitution = _uk_table_driven_fee_substitution(
            effect=effect,
            extracted_text=extracted_text,
            source_root=source_root,
            target=target,
        )
    if not substitution.recognized:
        return UKTableWordSubstitutionLowering(
            recognized=False,
            skip_effect=False,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
        )

    if substitution.original and substitution.replacement is not None:
        fragment = {
            "original": substitution.original,
            "replacement": substitution.replacement,
            "rule_id": "uk_effect_corresponding_table_entry_word_substitution",
        }
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_corresponding_table_entry_word_substitution",
            family="source_table_elaboration",
            reason_code="unique_column_1_target_column_2_words_match",
            reason=(
                "UK table-driven word substitution resolved by matching "
                "the affected provision to a unique source table row"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "table_index": substitution.table_index,
                "row_text": substitution.row_text,
                "original": substitution.original,
                "replacement": substitution.replacement,
            },
        )
        return UKTableWordSubstitutionLowering(
            recognized=True,
            skip_effect=False,
            curr_action="text_replace",
            content_ir=None,
            fragment_subs=[fragment],
            op_text_match=substitution.original,
            op_text_replacement=substitution.replacement,
        )

    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_corresponding_table_entry_word_substitution_unresolved",
        family="source_table_elaboration",
        reason_code=substitution.reason_code,
        reason=(
            "UK table-driven word substitution could not be "
            "resolved to a unique source table row"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "match_count": substitution.match_count,
            "replacement": substitution.replacement or "",
        },
    )
    return UKTableWordSubstitutionLowering(
        recognized=True,
        skip_effect=True,
        curr_action=None,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
    )


def _uk_table_driven_fee_target_refinements(
    *,
    effect: UKEffectRecord,
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> list[LegalAddress]:
    """Identify if a generic target provision matches multiple refined child targets in a fee table."""
    if source_root is None:
        return []

    tables = [
        el
        for el in source_root.iter()
        if el.tag.split("}")[-1].lower() == "table" and _uk_table_is_fee_table(el)
    ]
    if not tables:
        return []

    affected_year = str(effect.affected_year or "")
    refined_targets = []

    for table in tables:
        rows = _uk_table_rows_with_rowspans(table)
        current_chapter = ""
        current_act_title = ""
        current_provision = ""

        for row in rows:
            if len(row) < 3:
                continue

            col0 = row[0].strip()
            col1 = row[1].strip()
            col2 = row[2].strip()

            if col0:
                current_chapter = col0
                col1_lower = col1.lower()
                if not any(x in col1_lower for x in ["section", "schedule", "s.", "sch.", "para"]):
                    current_act_title = col1
                    current_provision = ""
                    continue

            if col1:
                col1_lower = col1.lower()
                if any(x in col1_lower for x in ["section", "schedule", "s.", "sch.", "para"]):
                    current_provision = col1

            if not current_provision:
                continue

            if affected_year and affected_year not in current_chapter and affected_year not in current_act_title:
                continue

            combined_cell = f"{current_provision} {col2}"
            if _uk_table_cell_mentions_target(
                combined_cell,
                target=target,
                affected_year=affected_year,
            ):
                labels = {kind: lbl for kind, lbl in target.path}
                if "subsection" in labels and "paragraph" not in labels:
                    match = re.match(r"^\s*(\([a-z0-9]+\)|[a-z0-9]+)\b", col2, re.I)
                    if match:
                        child_label = match.group(1).strip("()").lower()
                        if child_label in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "a", "b", "c", "d", "e", "f", "g"}:
                            refined = LegalAddress(path=target.path + (("paragraph", child_label),))
                            refined_targets.append(refined)
                elif "paragraph" in labels and "subparagraph" not in labels:
                    match = re.match(r"^\s*(\([a-z0-9]+\)|[a-z0-9]+)\b", col2, re.I)
                    if match:
                        child_label = match.group(1).strip("()").lower()
                        if child_label in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "a", "b", "c", "d", "e", "f", "g"}:
                            refined = LegalAddress(path=target.path + (("subparagraph", child_label),))
                            refined_targets.append(refined)

    return refined_targets


def address_to_citation(addr: LegalAddress) -> str:
    """Format a LegalAddress back into a standard UK citation string."""
    labels = {kind: lbl for kind, lbl in addr.path}
    section = labels.get("section")
    subsection = labels.get("subsection")
    paragraph = labels.get("paragraph")
    subparagraph = labels.get("subparagraph")

    parts = []
    if section:
        parts.append(f"s. {section}")
    if subsection:
        parts.append(f"({subsection})")
    if paragraph:
        parts.append(f"({paragraph})")
    if subparagraph:
        parts.append(f"({subparagraph})")
    return "".join(parts)
