"""UK source-table elaboration helpers."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.xml_helpers import _tag, _text_content


_UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_quoted_words_text_repeal"
)
_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_definition_entry_text_repeal"
)
_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID = "uk_effect_repeal_table_structural_repeal"


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


def _uk_repeal_table_enactment_matches_effect(cell_text: str, effect: UKEffectRecord) -> bool:
    """Conservatively match a repeal-table enactment cell to the affected Act."""
    text = " ".join(cell_text.split()).lower()
    if not text:
        return False
    year = str(effect.affected_year or "").strip()
    number = str(effect.affected_number or "").strip()
    slug = _uk_effect_act_slug(effect)
    if not year or not number or year not in text:
        return False
    num_pat = re.escape(number.lower())
    if slug == "asp":
        return re.search(rf"\basp\s*{num_pat}\b", text) is not None
    if slug == "ukpga":
        return re.search(rf"\bc\.?\s*{num_pat}\b", text) is not None
    if slug == "uksi":
        return re.search(rf"\b(?:s\.?\s*i\.?|si|uksi)\s*{re.escape(year)}\s*/\s*{num_pat}\b", text) is not None
    return re.search(rf"\b{re.escape(slug)}\s*{num_pat}\b", text) is not None


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
            "enactment" in text or "short title and chapter" in text or "title and chapter" in text
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


def _uk_repeal_table_extent_clauses(extent_cell: str) -> list[str]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return []
    clauses = re.split(
        r"(?<=\.)\s+(?=(?:In\s+)?(?:section|sections|schedule|paragraph)\b)",
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


def _uk_table_is_repeal_extent_source_table(table: ET.Element) -> tuple[int, int] | None:
    for row in _uk_table_rows_with_rowspans(table)[:4]:
        columns = _uk_repeal_table_columns(row)
        if columns is not None:
            return columns
    return None


def _uk_table_driven_repeal_table_quoted_words_text_repeal(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> _UKRepealTableQuotedWordsTextRepeal:
    """Resolve bounded repeal-schedule rows to quoted word-level text deletes."""
    effect_type = str(effect.effect_type or "").strip().lower()
    if effect_type not in {"words repealed", "word repealed", "words omitted", "word omitted"}:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    source_text = " ".join((extracted_text or "").split()).lower()
    if "extent of repeal" not in source_text:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    search_roots = [root for root in (extracted_el, source_root) if root is not None]
    if not search_roots:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)

    matches: list[tuple[int, str, tuple[str, ...], int, int, str, str, str, str]] = []
    tables = []
    seen_table_ids: set[int] = set()
    for root in search_roots:
        for el in root.iter():
            if _tag(el).lower() != "table" or id(el) in seen_table_ids:
                continue
            seen_table_ids.add(id(el))
            columns = _uk_table_is_repeal_extent_source_table(el)
            if columns is not None:
                tables.append((el, columns))
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        for row in rows[1:]:
            if len(row) <= max(enactment_idx, extent_idx):
                continue
            enactment_cell = row[enactment_idx]
            extent_cell = row[extent_idx]
            if not _uk_repeal_table_enactment_matches_effect(enactment_cell, effect):
                continue
            for extent_clause in _uk_repeal_table_extent_clauses(extent_cell):
                if not _uk_table_cell_mentions_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                ):
                    continue
                original, occurrence, end_occurrence = _uk_repeal_table_quoted_words_selector(extent_clause)
                additional_originals: tuple[str, ...] = ()
                rule_id = _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID
                target_kinds = {kind.lower() for kind, _ in target.path}
                if not original and "section" in target_kinds:
                    definition_originals = _uk_repeal_table_definition_entry_selectors(extent_clause)
                    if definition_originals:
                        original = definition_originals[0]
                        additional_originals = definition_originals[1:]
                        rule_id = _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID
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
                    )
                )

    if len(matches) != 1:
        return _UKRepealTableQuotedWordsTextRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_row",
            match_count=len(matches),
        )

    table_index, original, additional_originals, occurrence, end_occurrence, rule_id, row_text, enactment_cell, extent_cell = matches[0]
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
            r"\b(?:section|sections|schedule|schedules|paragraph|paragraphs|"
            r"subsection|subsections|sub-?paragraph|sub-?paragraphs)\b",
            norm,
        )
    )


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
    if effect_type not in {"repealed", "omitted", "revoked"}:
        return _UKRepealTableStructuralRepeal(recognized=False)
    if str(target.special or "") == "whole_act":
        return _UKRepealTableStructuralRepeal(recognized=False)
    source_text = " ".join((extracted_text or "").split()).lower()
    if "extent of repeal" not in source_text:
        return _UKRepealTableStructuralRepeal(recognized=False)
    search_roots = [root for root in (extracted_el, source_root) if root is not None]
    if not search_roots:
        return _UKRepealTableStructuralRepeal(recognized=False)

    matches: list[tuple[int, str, str, str]] = []
    tables = []
    seen_table_ids: set[int] = set()
    for root in search_roots:
        for el in root.iter():
            if _tag(el).lower() != "table" or id(el) in seen_table_ids:
                continue
            seen_table_ids.add(id(el))
            columns = _uk_table_is_repeal_extent_source_table(el)
            if columns is not None:
                tables.append((el, columns))
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        for row in rows[1:]:
            if len(row) <= max(enactment_idx, extent_idx):
                continue
            enactment_cell = row[enactment_idx]
            extent_cell = row[extent_idx]
            if not _uk_repeal_table_enactment_matches_effect(enactment_cell, effect):
                continue
            for extent_clause in _uk_repeal_table_extent_clauses(extent_cell):
                if not _uk_repeal_table_clause_is_structural_repeal(extent_clause):
                    continue
                if not _uk_table_cell_mentions_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                ):
                    continue
                matches.append(
                    (
                        table_index,
                        " | ".join((enactment_cell, extent_clause)),
                        enactment_cell,
                        extent_clause,
                    )
                )

    if len(matches) != 1:
        return _UKRepealTableStructuralRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_structural_row",
            match_count=len(matches),
        )

    table_index, row_text, enactment_cell, extent_cell = matches[0]
    return _UKRepealTableStructuralRepeal(
        recognized=True,
        reason_code="",
        match_count=1,
        table_index=table_index,
        row_text=row_text,
        enactment_cell=enactment_cell,
        extent_cell=extent_cell,
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


def _uk_cell_has_section_descendant_scope(
    text: str,
    *,
    section: str,
    descendant_labels: Sequence[str],
) -> bool:
    """Return true when descendant labels belong to the requested section."""
    # Quoted legal text may itself contain parenthetical labels. Those labels
    # are payload/preimage evidence, not target-scope evidence.
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", text)
    section_pat = re.escape(section.lower())
    wanted = [label.lower() for label in descendant_labels if label]
    explicit_ref_re = re.compile(rf"\b{section_pat}\s*((?:\([^)]*\)\s*)+)", re.I)
    for match in explicit_ref_re.finditer(scope_text):
        if _uk_parenthetical_labels_contain_sequence(
            _uk_parenthetical_labels(match.group(1)),
            wanted,
        ):
            return True

    singular_prefix = re.search(rf"\bsection\s+{section_pat}\b", scope_text, re.I) is not None
    plural_prefix = re.search(r"\bsections\b", scope_text, re.I) is not None
    if singular_prefix and not plural_prefix:
        before_act_title = re.split(r"\bof\b", scope_text, maxsplit=1, flags=re.I)[0]
        return _uk_parenthetical_labels_contain_sequence(
            _uk_parenthetical_labels(before_act_title),
            wanted,
        )
    return False


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

    if section:
        section_pat = re.escape(section.lower())
        direct_section_match = re.search(
            rf"\b(?:section|sections|s)\.?\s*{section_pat}\b",
            text,
        )
        section_range_match = _uk_section_label_in_simple_range(text, section)
        listed_section_match = (
            re.search(r"\bsections\b", text) is not None
            and re.search(rf"\b{section_pat}\s*\(", text) is not None
        )
        if not direct_section_match and not section_range_match and not listed_section_match:
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
        if not re.search(rf"\b(?:schedule|sch)\.?\s*{schedule_pat}\b", text):
            return False
        if paragraph:
            paragraph_pat = re.escape(paragraph.lower())
            if not re.search(rf"\b(?:paragraph|paragraphs|para)\.?\s*{paragraph_pat}\b", text):
                return False
        for label in (subparagraph,):
            if label and f"({label.lower()})" not in text:
                return False
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
