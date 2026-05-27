"""UK source-table elaboration helpers."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import weakref
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, Sequence

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
_UK_FLAT_REPEAL_SCHEDULE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID = (
    "uk_effect_flat_repeal_schedule_quoted_words_text_repeal"
)
_UK_FLAT_REPEAL_SCHEDULE_STRUCTURAL_REPEAL_RULE_ID = (
    "uk_effect_flat_repeal_schedule_structural_repeal"
)
_UK_REPEAL_TABLE_SENTENCE_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_sentence_text_repeal"
)
_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_definition_entry_text_repeal"
)
_UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_definition_child_text_repeal"
)
_UK_REPEAL_TABLE_COLUMN_ENTRY_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_column_entry_text_repeal"
)
_UK_REPEAL_TABLE_REFERENCE_TEXT_REPEAL_RULE_ID = (
    "uk_effect_repeal_table_reference_text_repeal"
)
_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID = "uk_effect_repeal_table_structural_repeal"
_UK_REPEAL_TABLE_PARENT_CHILD_TEXT_REPEAL_SPLIT_RULE_ID = (
    "uk_effect_repeal_table_parent_child_text_repeal_split"
)
_UK_DEFINITION_SELECTOR_SEPARATOR = "\x1f"
_UK_REPEAL_TABLE_DEFINITION_OR_ENTRY_CLAUSE_RE = re.compile(
    r"\b(?:definition\s+of|definitions\s+of|entry\s+for|entries\s+for)\b",
    flags=re.I,
)


def _uk_quoted_capture(name: str) -> str:
    return (
        rf"(?:[\u201c\"](?P<{name}_double>.*?)[\u201d\"]|"
        rf"[\u2018'](?P<{name}_single>.*?)[\u2019'])"
    )


def _uk_first_quote_group(match: re.Match[str], *names: str) -> str:
    for name in names:
        value = match.group(name)
        if value is not None:
            return " ".join(value.split()).strip()
    return ""


_UK_FLAT_REPEAL_SCHEDULE_QUOTED_WORDS_CLAUSE_RE = re.compile(
    r"\bIn\s+[^.;]{0,320}?\b(?:the\s+)?words?\s+"
    + _uk_quoted_capture("quoted")
    + r"[^.;]{0,80}(?:[.;]|$)",
    flags=re.I,
)
_UK_FLAT_REPEAL_SCHEDULE_STRUCTURAL_CLAUSE_RE = re.compile(
    r"\b(?:In\s+)?(?:part|parts|chapter|chapters|section|sections|schedule|schedules|"
    r"paragraph|paragraphs|subsection|subsections|sub-?paragraph|sub-?paragraphs)"
    r"\b[^.;]{0,260}(?:[.;]|$)",
    flags=re.I,
)


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
class _UKTableDrivenFeeSubstitutionMatch:
    table_index: int
    original: str
    replacement: str
    row_text: str


@dataclass(frozen=True)
class _UKTableDrivenCorrespondingEntryMatch:
    table_index: int
    original: str
    row_text: str


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
    table_cell_selector: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _UKRepealTableQuotedWordsMatch:
    table_index: int
    original: str
    additional_originals: tuple[str, ...]
    occurrence: int
    end_occurrence: int
    rule_id: str
    row_text: str
    enactment_cell: str
    extent_cell: str
    enactment_match_basis: str
    table_cell_selector: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _UKRepealTableStructuralRepeal:
    recognized: bool
    rule_id: str = _UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID
    reason_code: str = ""
    match_count: int = 0
    table_index: int = -1
    row_text: str = ""
    enactment_cell: str = ""
    extent_cell: str = ""
    enactment_match_basis: str = ""
    broad_container_target: str = ""
    mixed_word_selector: str = ""


@dataclass(frozen=True)
class _UKRepealTableParentChildTextRepealSplit:
    recognized: bool
    parent_target: Optional[LegalAddress] = None
    structural_target: Optional[LegalAddress] = None
    text_selectors: tuple[str, ...] = ()
    reason_code: str = ""
    match_count: int = 0
    table_index: int = -1
    row_text: str = ""
    enactment_cell: str = ""
    extent_cell: str = ""
    enactment_match_basis: str = ""


@dataclass(frozen=True)
class _UKRepealTableParentChildTextRepealMatch:
    table_index: int
    row_text: str
    enactment_cell: str
    extent_cell: str
    enactment_match_basis: str
    structural_target: LegalAddress
    text_selectors: tuple[str, ...]


@dataclass(frozen=True)
class _UKRepealTableStructuralMatch:
    table_index: int
    row_text: str
    enactment_cell: str
    extent_cell: str
    enactment_match_basis: str
    rule_id: str = _UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID
    reason_code: str = ""
    mixed_word_selector: str = ""


@dataclass(frozen=True)
class _UKRepealTableMixedStructuralWordMatch:
    table_index: int
    row_text: str
    enactment_cell: str
    extent_cell: str
    enactment_match_basis: str


@dataclass(frozen=True)
class _UKRepealTableBroadContainerMatch:
    table_index: int
    row_text: str
    enactment_cell: str
    extent_cell: str
    enactment_match_basis: str
    broad_container_target: str


class _UKRepealTableColumns(NamedTuple):
    enactment_index: int
    extent_index: int


class _UKRepealExtentSourceTable(NamedTuple):
    table: ET.Element
    columns: _UKRepealTableColumns


class _UKRepealTableQuotedWordsSelector(NamedTuple):
    original: str
    occurrence: int
    end_occurrence: int


_REPEAL_EXTENT_TABLE_CACHE: weakref.WeakKeyDictionary[
    ET.Element,
    tuple[_UKRepealExtentSourceTable, ...],
] = weakref.WeakKeyDictionary()


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


def _uk_flat_repeal_schedule_enactment_context(
    text: str,
    *,
    clause_start: int,
    effect: UKEffectRecord,
) -> tuple[str, str]:
    """Return nearby affected-enactment context for flattened repeal schedules."""
    context = text[:clause_start]
    citation_matches = tuple(
        re.finditer(
            r"\b(?:1[6-9]|20)\d{2}\s*\(\s*(?:c\.?|asp|s\.?\s*i\.?|si|uksi)\s*[^)]*\)",
            context,
            flags=re.I,
        )
    )
    current_enactment_context = (
        context[citation_matches[-1].start() :]
        if citation_matches
        else context
    )
    basis = _uk_repeal_table_enactment_match_basis(current_enactment_context, effect)
    if not basis:
        return "", ""
    return current_enactment_context, f"flat_preceding_context_{basis}"


def _uk_repeal_table_enactment_match_cell(
    row: Sequence[str],
    *,
    enactment_idx: int,
    extent_idx: int,
    effect: UKEffectRecord,
    identity_cells: Sequence[str] | None = None,
) -> tuple[str, str]:
    primary_cell = row[enactment_idx] if enactment_idx < len(row) else ""
    primary_basis = _uk_repeal_table_enactment_match_basis(primary_cell, effect)
    if primary_basis:
        return primary_cell, primary_basis
    if identity_cells is None:
        identity_cells = tuple(
            cell
            for idx, cell in enumerate(row)
            if idx != extent_idx and cell.strip()
        )
    combined_cell = " | ".join(identity_cells)
    if combined_cell == primary_cell:
        return primary_cell, ""
    combined_basis = _uk_repeal_table_enactment_match_basis(combined_cell, effect)
    if combined_basis:
        return combined_cell, f"combined_identity_cells_{combined_basis}"
    return primary_cell, ""


def _uk_repeal_table_columns(row: Sequence[str]) -> _UKRepealTableColumns | None:
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
    return _UKRepealTableColumns(enactment_idx, extent_idx)


def _uk_repeal_table_quoted_words_selector(extent_cell: str) -> _UKRepealTableQuotedWordsSelector:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return _UKRepealTableQuotedWordsSelector("", 0, 0)
    quoted = _uk_quoted_capture
    range_match = re.search(
        r"\bthe\s+words?\s+from\s+"
        + quoted("start")
        + r"(?:,?\s+where\s+(?:they|it|the\s+words?)\s+"
        r"(?P<occurrence>firstly|first|1st|secondly|second|2nd|thirdly|third|3rd|fourthly|fourth|4th|fifthly|fifth|5th)"
        r"\s+occurs?)?"
        r",?\s+to\s+"
        r"(?:(?:the\s+)?end|"
        + quoted("end")
        + r")",
        text,
        re.I,
    )
    if range_match is not None:
        start = _uk_first_quote_group(
            range_match,
            "start_double",
            "start_single",
        )
        end = _uk_first_quote_group(
            range_match,
            "end_double",
            "end_single",
        )
        if not start:
            return _UKRepealTableQuotedWordsSelector("", 0, 0)
        occurrence = 0
        if range_match.group("occurrence"):
            occurrence = _uk_ordinal_to_int(range_match.group("occurrence")) or 0
        if end:
            return _UKRepealTableQuotedWordsSelector(f"TEXT_FROM_{start}_TO_{end}", occurrence, 0)
        return _UKRepealTableQuotedWordsSelector(f"TEXT_FROM_{start}_TO_END", occurrence, 0)
    match = re.search(
        r"\bthe\s+words?\s+"
        + _uk_quoted_capture("quoted"),
        text,
        re.I,
    )
    if match is None:
        bare_matches = list(re.finditer(_uk_quoted_capture("bare"), text, re.I))
        if (
            len(bare_matches) == 1
            and re.match(r"\s*In\s+", text, flags=re.I) is not None
            and re.search(r"\b(?:insert|substitute|substituted|for)\b", text, re.I) is None
            and _UK_REPEAL_TABLE_DEFINITION_OR_ENTRY_CLAUSE_RE.search(text) is None
        ):
            return _UKRepealTableQuotedWordsSelector(
                _uk_first_quote_group(
                    bare_matches[0],
                    "bare_double",
                    "bare_single",
                ),
                0,
                0,
            )
        return _UKRepealTableQuotedWordsSelector("", 0, 0)
    occurrence = (
        -1
        if re.search(
            r"\bat\s+the\s+end\s+of\s+"
            r"(?:paragraph|sub-?paragraph|subsection|item|point)\s*\([^)]+\)",
            text,
            re.I,
        )
        else 0
    )
    return _UKRepealTableQuotedWordsSelector(
        _uk_first_quote_group(match, "quoted_double", "quoted_single"),
        occurrence,
        0,
    )


def _uk_flat_repeal_schedule_quoted_words_text_repeal(
    *,
    effect: UKEffectRecord,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> _UKRepealTableQuotedWordsTextRepeal:
    """Recover quoted-word repeal rows from flattened repeal-schedule text."""
    effect_type = str(effect.effect_type or "").strip().lower()
    if effect_type not in {
        "",
        "words repealed",
        "word repealed",
        "words omitted",
        "word omitted",
    }:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return _UKRepealTableQuotedWordsTextRepeal(recognized=False)
    matches: list[_UKRepealTableQuotedWordsMatch] = []
    for match in _UK_FLAT_REPEAL_SCHEDULE_QUOTED_WORDS_CLAUSE_RE.finditer(text):
        clause = " ".join(match.group(0).split()).strip()
        if not _uk_table_cell_mentions_target(
            clause,
            target=target,
            affected_year=str(effect.affected_year or ""),
        ):
            continue
        enactment_context, enactment_match_basis = _uk_flat_repeal_schedule_enactment_context(
            text,
            clause_start=match.start(),
            effect=effect,
        )
        if not enactment_match_basis:
            continue
        original = _uk_first_quote_group(match, "quoted_double", "quoted_single")
        if not original:
            continue
        matches.append(
            _UKRepealTableQuotedWordsMatch(
                table_index=-1,
                original=original,
                additional_originals=(),
                occurrence=0,
                end_occurrence=0,
                rule_id=_UK_FLAT_REPEAL_SCHEDULE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID,
                row_text=clause,
                enactment_cell=" ".join(enactment_context.split())[-240:],
                extent_cell=clause,
                enactment_match_basis=enactment_match_basis,
            )
        )
    if len(matches) != 1:
        return _UKRepealTableQuotedWordsTextRepeal(
            recognized=bool(matches),
            reason_code="no_unique_matching_flat_repeal_schedule_clause",
            match_count=len(matches),
        )
    flat_match = matches[0]
    return _UKRepealTableQuotedWordsTextRepeal(
        recognized=True,
        original=flat_match.original,
        additional_originals=flat_match.additional_originals,
        rule_id=flat_match.rule_id,
        match_count=1,
        table_index=flat_match.table_index,
        row_text=flat_match.row_text,
        enactment_cell=flat_match.enactment_cell,
        extent_cell=flat_match.extent_cell,
        enactment_match_basis=flat_match.enactment_match_basis,
        occurrence=flat_match.occurrence,
        end_occurrence=flat_match.end_occurrence,
    )


def _uk_flat_repeal_schedule_structural_repeal(
    *,
    effect: UKEffectRecord,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> _UKRepealTableStructuralRepeal:
    """Recover whole-provision repeal rows from flattened repeal-schedule text."""
    effect_type = str(effect.effect_type or "").strip().lower()
    source_supplies_repeal_action = (
        not effect_type and _uk_repeal_schedule_source_text(extracted_text)
    )
    if effect_type not in {"repealed", "omitted", "revoked"} and not source_supplies_repeal_action:
        return _UKRepealTableStructuralRepeal(recognized=False)
    if str(target.special or "") == "whole_act":
        return _UKRepealTableStructuralRepeal(recognized=False)
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return _UKRepealTableStructuralRepeal(recognized=False)

    matches: list[_UKRepealTableStructuralMatch] = []
    for match in _UK_FLAT_REPEAL_SCHEDULE_STRUCTURAL_CLAUSE_RE.finditer(text):
        clause = " ".join(match.group(0).split()).strip()
        if _uk_table_cell_explicitly_excepts_target(
            clause,
            target=target,
            affected_year=str(effect.affected_year or ""),
        ):
            continue
        reason_code = "unique_flat_repeal_schedule_structural_repeal"
        if not _uk_repeal_table_clause_is_structural_repeal(clause):
            if not _uk_repeal_table_mixed_clause_explicitly_names_target_with_parent(
                clause,
                target=target,
                affected_year=str(effect.affected_year or ""),
            ):
                continue
            reason_code = "flat_mixed_structural_and_word_repeal_split_structural_target"
        if re.search(r"\b(?:definition|entry|entries)\b", clause, flags=re.I):
            continue
        if not _uk_table_cell_mentions_target(
            clause,
            target=target,
            affected_year=str(effect.affected_year or ""),
        ):
            continue
        enactment_context, enactment_match_basis = _uk_flat_repeal_schedule_enactment_context(
            text,
            clause_start=match.start(),
            effect=effect,
        )
        if not enactment_match_basis:
            continue
        matches.append(
            _UKRepealTableStructuralMatch(
                table_index=-1,
                row_text=clause,
                enactment_cell=" ".join(enactment_context.split())[-240:],
                extent_cell=clause,
                enactment_match_basis=enactment_match_basis,
                rule_id=_UK_FLAT_REPEAL_SCHEDULE_STRUCTURAL_REPEAL_RULE_ID,
                reason_code=reason_code,
            )
        )

    if len(matches) != 1:
        if not matches:
            return _UKRepealTableStructuralRepeal(recognized=False)
        return _UKRepealTableStructuralRepeal(
            recognized=True,
            rule_id=_UK_FLAT_REPEAL_SCHEDULE_STRUCTURAL_REPEAL_RULE_ID,
            reason_code="no_unique_matching_flat_repeal_schedule_structural_clause",
            match_count=len(matches),
        )
    flat_match = matches[0]
    return _UKRepealTableStructuralRepeal(
        recognized=True,
        rule_id=flat_match.rule_id,
        reason_code=flat_match.reason_code,
        match_count=1,
        table_index=flat_match.table_index,
        row_text=flat_match.row_text,
        enactment_cell=flat_match.enactment_cell,
        extent_cell=flat_match.extent_cell,
        enactment_match_basis=flat_match.enactment_match_basis,
    )


def _uk_repeal_table_column_entry_text_selector(extent_cell: str) -> Optional[dict[str, Any]]:
    """Extract a singular repeal-table table-column entry without deleting its row."""
    text = " ".join((extent_cell or "").split()).strip()
    if not text or re.search(r"\bthat\s+(?:act|schedule|column)\b", text, flags=re.I):
        return None
    if re.search(r"\bentries\b", text, flags=re.I):
        return None
    if len(re.findall(r"\bentry\s+(?:for|relating\s+to)\b", text, flags=re.I)) != 1:
        return None
    match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
        r"\s+column|column\s+(?P<column_number>\d+))\s+"
        r"of\s+(?:the\s+)?table\b.*?"
        r"\b(?:the\s+)?entry\s+(?:for|relating\s+to)\s+(?P<entry>.+?)\s*\.?$",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    column_token = match.group("column_ordinal") or match.group("column_number")
    column_index = _uk_ordinal_to_int(column_token or "")
    entry_text = " ".join(match.group("entry").split()).strip(" ,;.")
    if (
        column_index is None
        or column_index < 1
        or not entry_text
        or re.search(r"\bthat\s+(?:act|schedule|column)\b", entry_text, flags=re.I)
    ):
        return None
    return {
        "rule_id": _UK_REPEAL_TABLE_COLUMN_ENTRY_TEXT_REPEAL_RULE_ID,
        "selector_mode": "unique_column_text",
        "column_index": column_index,
        "match_text": entry_text,
        "match_scope": "full_cell",
        "source_table_mode": "repeal_table_extent_row",
        "table_column_entry_action": "delete_entry_text",
        "text_patch_original": entry_text,
        "text_patch_replacement": "",
    }


def _uk_repeal_table_column_entry_clause_mentions_target(
    extent_cell: str,
    *,
    target: LegalAddress,
) -> bool:
    """Match table-column entry selectors without broadening quoted-word rows."""
    text = " ".join((extent_cell or "").split()).lower()
    labels = {kind: label for kind, label in target.path}
    section = labels.get("section", "").strip().lower()
    subsection = labels.get("subsection", "").strip().lower()
    if not section or subsection != "table" or "table" not in text:
        return False
    return (
        re.search(
            rf"\b(?:section|sections|s)\.?\s*{re.escape(section)}\b",
            text,
        )
        is not None
    )


def _uk_repeal_table_definition_entry_selectors(extent_cell: str) -> tuple[str, ...]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return ()
    match = re.search(
        r"\b(?:the\s+)?(?:definition\s+of|entry\s+for)\s+"
        + _uk_quoted_capture("term"),
        text,
        re.I,
    )
    if match is not None:
        tail = text[match.end() :]
        if re.search(r"\b(?:paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|head|heads)\b", tail, re.I):
            return ()
        term = _uk_first_quote_group(match, "term_double", "term_single")
        return (f"TEXT_DEFINITION_ENTRY_{term}",) if term else ()
    plural_match = re.search(
        r"\b(?:the\s+)?(?:entries\s+for|definitions\s+of)\s+(?P<body>.+?)(?:\.|$)",
        text,
        re.I,
    )
    if plural_match is None:
        return ()
    body = plural_match.group("body")
    if re.search(r"\b(?:paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|head|heads)\b", body, re.I):
        return ()
    terms: list[str] = []
    for term_match in re.finditer(_uk_quoted_capture("term"), body):
        term = _uk_first_quote_group(term_match, "term_double", "term_single")
        if term:
            terms.append(f"TEXT_DEFINITION_ENTRY_{term}")
    return tuple(terms)


def _uk_repeal_table_definition_child_selectors(extent_cell: str) -> tuple[str, ...]:
    text = " ".join(extent_cell.split()).strip()
    if not text:
        return ()
    match = re.search(
        r"\bin\s+the\s+definition\s+of\s+"
        + _uk_quoted_capture("term")
        + r",?\s+(?P<kind>paragraphs?|sub-?paragraphs?)\s+(?P<labels>[^.;]+)",
        text,
        re.I,
    )
    if match is None:
        return ()
    term = _uk_first_quote_group(match, "term_double", "term_single")
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


def _uk_repeal_table_sentence_repeal_requires_selector(extent_cell: str) -> bool:
    text = " ".join((extent_cell or "").split()).strip()
    if not text:
        return False
    return bool(
        re.search(
            r"\b(?:the\s+)?(?:first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+sentence\b",
            text,
            re.I,
        )
    )


def _uk_repeal_table_sentence_selector(extent_cell: str) -> str:
    text = " ".join((extent_cell or "").split()).strip()
    if not text:
        return ""
    match = re.search(
        r"\b(?:the\s+)?(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+sentence\b",
        text,
        re.I,
    )
    if match is None:
        return ""
    ordinal = _uk_ordinal_to_int(match.group("ordinal"))
    if ordinal is None:
        return ""
    return f"TEXT_SENTENCE_{ordinal}"


def _uk_repeal_table_reference_selector(extent_cell: str) -> str:
    """Extract singular `the reference to X` repeal-table selectors."""
    text = " ".join((extent_cell or "").split()).strip()
    if not text:
        return ""
    if len(re.findall(r"\bthe\s+reference\s+to\b", text, flags=re.I)) != 1:
        return ""
    if re.search(r"\breferences\s+to\b", text, flags=re.I):
        return ""
    match = re.search(
        r"\bthe\s+reference\s+to\s+(?P<reference>.+?)\s*\.?$",
        text,
        flags=re.I,
    )
    if match is None:
        return ""
    reference = " ".join(match.group("reference").split()).strip(" ,;.")
    if not reference:
        return ""
    if not re.search(
        r"\b(?:section|schedule|paragraph|regulations?|article|part|chapter)\b",
        reference,
        flags=re.I,
    ):
        return ""
    if re.search(r"\b(?:and|or)\b", reference, flags=re.I):
        return ""
    return reference


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
) -> _UKRepealTableColumns | None:
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
                return _UKRepealTableColumns(0, 1)
    return None


def _uk_repeal_extent_source_tables(root: ET.Element) -> tuple[_UKRepealExtentSourceTable, ...]:
    cached = _REPEAL_EXTENT_TABLE_CACHE.get(root)
    if cached is not None:
        return cached
    tables: list[_UKRepealExtentSourceTable] = []
    source_is_repeal_schedule = _uk_repeal_schedule_source_text(_text_content(root))
    for el in root.iter():
        if _tag(el).lower() != "table":
            continue
        columns = _uk_table_is_repeal_extent_source_table(
            el,
            source_is_repeal_schedule=source_is_repeal_schedule,
        )
        if columns is not None:
            tables.append(_UKRepealExtentSourceTable(el, columns))
    result = tuple(tables)
    _REPEAL_EXTENT_TABLE_CACHE[root] = result
    return result


def _uk_repeal_extent_source_tables_for_roots(
    roots: Sequence[ET.Element],
) -> tuple[_UKRepealExtentSourceTable, ...]:
    tables: list[_UKRepealExtentSourceTable] = []
    seen_table_ids: set[int] = set()
    for root in roots:
        for source_table in _uk_repeal_extent_source_tables(root):
            table_id = id(source_table.table)
            if table_id in seen_table_ids:
                continue
            seen_table_ids.add(table_id)
            tables.append(source_table)
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
        "",
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
        return _uk_flat_repeal_schedule_quoted_words_text_repeal(
            effect=effect,
            extracted_text=extracted_text,
            target=target,
        )

    matches: list[_UKRepealTableQuotedWordsMatch] = []
    tables = _uk_repeal_extent_source_tables_for_roots(search_roots)
    if not tables:
        return _uk_flat_repeal_schedule_quoted_words_text_repeal(
            effect=effect,
            extracted_text=extracted_text,
            target=target,
        )
    unsupported_sentence_repeal: Optional[_UKRepealTableQuotedWordsTextRepeal] = None
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        last_enactment_cell = ""
        last_identity_cells: tuple[str, ...] = ()
        for row in rows[1:]:
            if len(row) >= max(enactment_idx, extent_idx) + 1:
                enactment_cell = row[enactment_idx]
                extent_cell = row[extent_idx]
                if enactment_cell:
                    last_enactment_cell = enactment_cell
                elif last_enactment_cell:
                    enactment_cell = last_enactment_cell
                identity_cells = tuple(
                    cell
                    for idx, cell in enumerate(row)
                    if idx != extent_idx and cell.strip()
                )
                if identity_cells:
                    last_identity_cells = identity_cells
                elif last_identity_cells:
                    identity_cells = last_identity_cells
            elif len(row) == 1 and last_enactment_cell:
                enactment_cell = last_enactment_cell
                extent_cell = row[0]
                identity_cells = last_identity_cells or (last_enactment_cell,)
            else:
                continue
            enactment_cell, enactment_match_basis = _uk_repeal_table_enactment_match_cell(
                row,
                enactment_idx=enactment_idx,
                extent_idx=extent_idx,
                effect=effect,
                identity_cells=identity_cells,
            )
            if not enactment_match_basis:
                continue
            for extent_clause in _uk_repeal_table_extent_clauses(extent_cell):
                mentions_target = _uk_table_cell_mentions_target(
                    extent_clause,
                    target=target,
                    affected_year=str(effect.affected_year or ""),
                )
                table_cell_selector = _uk_repeal_table_column_entry_text_selector(extent_clause)
                column_entry_mentions_target = (
                    table_cell_selector is not None
                    and _uk_repeal_table_column_entry_clause_mentions_target(
                        extent_clause,
                        target=target,
                    )
                )
                if not mentions_target and not column_entry_mentions_target:
                    continue
                additional_originals: tuple[str, ...] = ()
                rule_id = _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID
                original = ""
                occurrence = 0
                end_occurrence = 0
                if not structural_definition_entry_effect:
                    original = _uk_repeal_table_mixed_clause_word_selector(
                        extent_clause,
                        target=target,
                    )
                    if not original:
                        (
                            original,
                            occurrence,
                            end_occurrence,
                        ) = _uk_repeal_table_quoted_words_selector(extent_clause)
                if not original:
                    definition_originals = _uk_repeal_table_definition_entry_selectors(extent_clause)
                    if definition_originals:
                        original = definition_originals[0]
                        additional_originals = definition_originals[1:]
                        rule_id = _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID
                if (
                    not original
                    and not structural_definition_entry_effect
                    and column_entry_mentions_target
                ):
                    if table_cell_selector:
                        original = str(table_cell_selector["match_text"])
                        rule_id = _UK_REPEAL_TABLE_COLUMN_ENTRY_TEXT_REPEAL_RULE_ID
                if (
                    not original
                    and not structural_definition_entry_effect
                ):
                    reference_original = _uk_repeal_table_reference_selector(extent_clause)
                    if reference_original:
                        original = reference_original
                        rule_id = _UK_REPEAL_TABLE_REFERENCE_TEXT_REPEAL_RULE_ID
                if (
                    not original
                    and not structural_definition_entry_effect
                ):
                    sentence_original = _uk_repeal_table_sentence_selector(extent_clause)
                    if sentence_original:
                        original = sentence_original
                        rule_id = _UK_REPEAL_TABLE_SENTENCE_TEXT_REPEAL_RULE_ID
                if (
                    not original
                    and not structural_definition_entry_effect
                ):
                    definition_child_originals = _uk_repeal_table_definition_child_selectors(
                        extent_clause
                    )
                    if definition_child_originals:
                        original = definition_child_originals[0]
                        additional_originals = definition_child_originals[1:]
                        rule_id = _UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID
                if not original:
                    if (
                        unsupported_sentence_repeal is None
                        and _uk_repeal_table_sentence_repeal_requires_selector(extent_clause)
                    ):
                        unsupported_sentence_repeal = _UKRepealTableQuotedWordsTextRepeal(
                            recognized=True,
                            reason_code="sentence_repeal_requires_sentence_selector",
                            match_count=1,
                            table_index=table_index,
                            row_text=" | ".join((enactment_cell, extent_clause)),
                            enactment_cell=enactment_cell,
                            extent_cell=extent_clause,
                            enactment_match_basis=enactment_match_basis,
                        )
                    continue
                matches.append(
                    _UKRepealTableQuotedWordsMatch(
                        table_index=table_index,
                        original=original,
                        additional_originals=additional_originals,
                        occurrence=occurrence,
                        end_occurrence=end_occurrence,
                        rule_id=rule_id,
                        row_text=" | ".join((enactment_cell, extent_clause)),
                        enactment_cell=enactment_cell,
                        extent_cell=extent_clause,
                        enactment_match_basis=enactment_match_basis,
                        table_cell_selector=table_cell_selector,
                    )
                )

    if len(matches) != 1:
        if unsupported_sentence_repeal is not None and not matches:
            return unsupported_sentence_repeal
        if not matches:
            flat_repeal = _uk_flat_repeal_schedule_quoted_words_text_repeal(
                effect=effect,
                extracted_text=extracted_text,
                target=target,
            )
            if flat_repeal.recognized:
                return flat_repeal
        return _UKRepealTableQuotedWordsTextRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_row",
            match_count=len(matches),
        )

    match = matches[0]
    return _UKRepealTableQuotedWordsTextRepeal(
        recognized=True,
        original=match.original,
        additional_originals=match.additional_originals,
        rule_id=match.rule_id,
        reason_code="",
        match_count=1,
        table_index=match.table_index,
        row_text=match.row_text,
        enactment_cell=match.enactment_cell,
        extent_cell=match.extent_cell,
        enactment_match_basis=match.enactment_match_basis,
        occurrence=match.occurrence,
        end_occurrence=match.end_occurrence,
        table_cell_selector=match.table_cell_selector,
    )


def _uk_repeal_table_clause_is_structural_repeal(extent_clause: str) -> bool:
    """Return true for repeal-table clauses that claim whole target provisions."""
    text = " ".join((extent_clause or "").split()).strip()
    if not text:
        return False
    if re.search(r'(?:["“][^"”]*["”]|‘[^’]*’)', text):
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
        rf"(?:[,;—–-]|\band\b)\s*(?:the\s+)?{kind_pattern}\s*{label_pattern}",
        scope_text,
        flags=re.I,
    ) is not None or _uk_repeal_table_mixed_clause_names_target_in_list_or_range(
        scope_text,
        kind_pattern=kind_pattern,
        label=label,
    )


def _uk_repeal_table_mixed_clause_names_target_in_list_or_range(
    scope_text: str,
    *,
    kind_pattern: str,
    label: str,
) -> bool:
    """Match the structural half of mixed rows like `subsections (1) to (5)`."""
    wanted = _clean_num(label)
    if not wanted:
        return False
    for match in re.finditer(
        rf"(?:[,;—–-]|\band\b)\s*(?:\([a-z]\)\s*)?(?:the\s+)?{kind_pattern}\s+(?P<body>[^.;]+)",
        scope_text,
        flags=re.I,
    ):
        body = re.split(
            r"(?:;\s*|,\s*(?:and\s+)?|\band\s+)"
            r"(?:\([a-z]\)\s*)?in\s+(?:subsection|paragraph|sub-?paragraph)\b",
            match.group("body"),
            maxsplit=1,
            flags=re.I,
        )[0]
        if _uk_container_label_body_mentions_label(body, wanted):
            return True
        if not wanted.isdigit():
            continue
        wanted_int = int(wanted)
        for range_match in re.finditer(
            r"\(\s*(?P<start>[0-9]+|[ivxlcdm]+)\s*\)\s*"
            r"(?:to|-|–|—)\s*"
            r"\(\s*(?P<end>[0-9]+|[ivxlcdm]+)\s*\)",
            body,
            flags=re.I,
        ):
            start = _clean_num(range_match.group("start"))
            end = _clean_num(range_match.group("end"))
            if not start.isdigit() or not end.isdigit():
                continue
            low = min(int(start), int(end))
            high = max(int(start), int(end))
            if low <= wanted_int <= high:
                return True
    return False


def _uk_repeal_table_mixed_clause_explicitly_names_target_with_parent(
    extent_clause: str,
    *,
    target: LegalAddress,
    affected_year: str,
) -> bool:
    """Return true when a mixed row names the parent context plus target leaf."""
    if len(target.path) < 2:
        return False
    parent_target = LegalAddress(path=target.path[:-1], special=None)
    return _uk_table_cell_mentions_target(
        extent_clause,
        target=parent_target,
        affected_year=affected_year,
    ) and _uk_repeal_table_mixed_clause_explicitly_names_structural_target(
        extent_clause,
        target=target,
    )


def _uk_repeal_table_mixed_clause_word_selector(
    extent_clause: str,
    *,
    target: LegalAddress,
) -> str:
    """Return a contextual selector for mixed structural + adjacent-word repeals."""
    leaf_kind = target.leaf_kind().strip().lower()
    leaf_label = target.leaf_label().strip()
    if leaf_kind not in {"paragraph", "subparagraph", "item", "point"} or not leaf_label:
        return ""
    text = " ".join((extent_clause or "").split()).strip()
    quoted = _uk_quoted_capture("word")
    preceding_target = re.search(
        r"(?:the\s+)?(?:preceding\s+word|word)\s+"
        + quoted
        + r"\s+(?:immediately\s+)?(?:before|preceding)\s+it\b",
        text,
        re.I,
    )
    preceding_bare = re.search(
        r"(?:the\s+)?preceding\s+word\s+" + quoted,
        text,
        re.I,
    )
    match = preceding_target or preceding_bare
    if match is None:
        following_target = re.search(
            r"(?:the\s+)?word\s+"
            + quoted
            + r"\s+(?:immediately\s+)?following\s+(?:it|that\s+"
            + re.escape(leaf_kind)
            + r")\b",
            text,
            re.I,
        )
        if following_target is None:
            return ""
        word = _uk_first_quote_group(
            following_target,
            "word_double",
            "word_single",
        )
        if not word:
            return ""
        return f"TEXT_WORD_{word}_IMMEDIATELY_FOLLOWING_{leaf_kind}_{leaf_label}"
    word = _uk_first_quote_group(match, "word_double", "word_single")
    if not word:
        return ""
    return f"TEXT_WORD_{word}_IMMEDIATELY_PRECEDING_{leaf_kind}_{leaf_label}"


def _uk_repeal_table_explicit_child_structural_targets(
    extent_clause: str,
    *,
    parent_target: LegalAddress,
) -> tuple[LegalAddress, ...]:
    """Return child targets explicitly named inside a parent-scoped mixed repeal row."""
    if not parent_target.path:
        return ()
    scope_text = " ".join((extent_clause or "").split())
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", scope_text)
    child_kind_patterns = (
        ("paragraph", r"paragraphs?"),
        ("subparagraph", r"sub-?paragraphs?"),
        ("item", r"items?"),
        ("point", r"points?"),
    )
    targets: list[LegalAddress] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for child_kind, kind_pattern in child_kind_patterns:
        for list_match in re.finditer(
            rf"(?:[,;]|\band\b)\s*(?:the\s+)?{kind_pattern}\s+"
            r"(?P<body>(?:\(\s*[0-9a-zivxlcdm]+\s*\)(?:\s*(?:,|and)\s*)?)+)",
            scope_text,
            flags=re.I,
        ):
            for label_match in re.finditer(
                r"\(\s*(?P<label>[0-9a-zivxlcdm]+)\s*\)",
                list_match.group("body"),
                flags=re.I,
            ):
                label = _clean_num(label_match.group("label"))
                if not label:
                    continue
                target = LegalAddress(
                    path=(*parent_target.path, (child_kind, label)),
                    special=None,
                )
                if target.path in seen:
                    continue
                seen.add(target.path)
                targets.append(target)
        for match in re.finditer(
            rf"(?:[,;]|\band\b)\s*(?:the\s+)?{kind_pattern}\s*\(\s*(?P<label>[0-9a-zivxlcdm]+)\s*\)",
            scope_text,
            flags=re.I,
        ):
            label = _clean_num(match.group("label"))
            if not label:
                continue
            target = LegalAddress(
                path=(*parent_target.path, (child_kind, label)),
                special=None,
            )
            if target.path in seen:
                continue
            if not _uk_repeal_table_mixed_clause_explicitly_names_structural_target(
                extent_clause,
                target=target,
            ):
                continue
            seen.add(target.path)
            targets.append(target)
    return tuple(targets)


def _uk_table_driven_repeal_table_parent_child_text_repeal_split(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    target: LegalAddress,
) -> _UKRepealTableParentChildTextRepealSplit:
    """Resolve parent feed targets whose repeal row names child and text deletes."""
    effect_type = str(effect.effect_type or "").strip().lower()
    source_supplies_repeal_action = (
        not effect_type and _uk_repeal_schedule_source_text(extracted_text)
    )
    if effect_type not in {"repealed", "omitted", "revoked"} and not source_supplies_repeal_action:
        return _UKRepealTableParentChildTextRepealSplit(recognized=False)
    if str(target.special or "") == "whole_act" or not target.path:
        return _UKRepealTableParentChildTextRepealSplit(recognized=False)
    search_roots = _uk_repeal_extent_search_roots(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
    )
    if not search_roots:
        return _UKRepealTableParentChildTextRepealSplit(recognized=False)
    tables = _uk_repeal_extent_source_tables_for_roots(search_roots)
    if not tables:
        return _UKRepealTableParentChildTextRepealSplit(recognized=False)

    matches: list[_UKRepealTableParentChildTextRepealMatch] = []
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        last_enactment_cell = ""
        last_identity_cells: tuple[str, ...] = ()
        for row in rows[1:]:
            if len(row) >= max(enactment_idx, extent_idx) + 1:
                enactment_cell = row[enactment_idx]
                extent_cell = row[extent_idx]
                if enactment_cell:
                    last_enactment_cell = enactment_cell
                elif last_enactment_cell:
                    enactment_cell = last_enactment_cell
                identity_cells = tuple(
                    cell
                    for idx, cell in enumerate(row)
                    if idx != extent_idx and cell.strip()
                )
                if identity_cells:
                    last_identity_cells = identity_cells
                elif last_identity_cells:
                    identity_cells = last_identity_cells
            elif len(row) == 1 and last_enactment_cell:
                enactment_cell = last_enactment_cell
                extent_cell = row[0]
                identity_cells = last_identity_cells or (last_enactment_cell,)
            else:
                continue
            enactment_cell, enactment_match_basis = _uk_repeal_table_enactment_match_cell(
                row,
                enactment_idx=enactment_idx,
                extent_idx=extent_idx,
                effect=effect,
                identity_cells=identity_cells,
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
                child_targets = _uk_repeal_table_explicit_child_structural_targets(
                    extent_clause,
                    parent_target=target,
                )
                if len(child_targets) != 1:
                    continue
                structural_target = child_targets[0]
                text_selectors: list[str] = []
                mixed_word_selector = _uk_repeal_table_mixed_clause_word_selector(
                    extent_clause,
                    target=structural_target,
                )
                if mixed_word_selector:
                    text_selectors.append(mixed_word_selector)
                quoted_selector, _occurrence, _end_occurrence = (
                    _uk_repeal_table_quoted_words_selector(extent_clause)
                )
                if quoted_selector and quoted_selector not in text_selectors:
                    text_selectors.append(quoted_selector)
                if not text_selectors:
                    continue
                matches.append(
                    _UKRepealTableParentChildTextRepealMatch(
                        table_index=table_index,
                        row_text=" | ".join((enactment_cell, extent_clause)),
                        enactment_cell=enactment_cell,
                        extent_cell=extent_clause,
                        enactment_match_basis=enactment_match_basis,
                        structural_target=structural_target,
                        text_selectors=tuple(text_selectors),
                    )
                )

    if len(matches) != 1:
        if not matches:
            return _UKRepealTableParentChildTextRepealSplit(recognized=False)
        return _UKRepealTableParentChildTextRepealSplit(
            recognized=True,
            reason_code="no_unique_parent_child_text_repeal_row",
            match_count=len(matches),
        )
    match = matches[0]
    return _UKRepealTableParentChildTextRepealSplit(
        recognized=True,
        parent_target=target,
        structural_target=match.structural_target,
        text_selectors=match.text_selectors,
        reason_code="parent_target_child_structural_and_text_repeal_split",
        match_count=1,
        table_index=match.table_index,
        row_text=match.row_text,
        enactment_cell=match.enactment_cell,
        extent_cell=match.extent_cell,
        enactment_match_basis=match.enactment_match_basis,
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
        return _uk_flat_repeal_schedule_structural_repeal(
            effect=effect,
            extracted_text=extracted_text,
            target=target,
        )

    matches: list[_UKRepealTableStructuralMatch] = []
    mixed_structural_word_matches: list[_UKRepealTableMixedStructuralWordMatch] = []
    broad_container_matches: list[_UKRepealTableBroadContainerMatch] = []
    tables = _uk_repeal_extent_source_tables_for_roots(search_roots)
    if not tables:
        return _uk_flat_repeal_schedule_structural_repeal(
            effect=effect,
            extracted_text=extracted_text,
            target=target,
        )
    for table_index, (table, (enactment_idx, extent_idx)) in enumerate(tables):
        rows = _uk_table_rows_with_rowspans(table)
        last_enactment_cell = ""
        last_identity_cells: tuple[str, ...] = ()
        for row in rows[1:]:
            if len(row) >= max(enactment_idx, extent_idx) + 1:
                enactment_cell = row[enactment_idx]
                extent_cell = row[extent_idx]
                if enactment_cell:
                    last_enactment_cell = enactment_cell
                elif last_enactment_cell:
                    enactment_cell = last_enactment_cell
                identity_cells = tuple(
                    cell
                    for idx, cell in enumerate(row)
                    if idx != extent_idx and cell.strip()
                )
                if identity_cells:
                    last_identity_cells = identity_cells
                elif last_identity_cells:
                    identity_cells = last_identity_cells
            elif len(row) == 1 and last_enactment_cell:
                enactment_cell = last_enactment_cell
                extent_cell = row[0]
                identity_cells = last_identity_cells or (last_enactment_cell,)
            else:
                continue
            enactment_cell, enactment_match_basis = _uk_repeal_table_enactment_match_cell(
                row,
                enactment_idx=enactment_idx,
                extent_idx=extent_idx,
                effect=effect,
                identity_cells=identity_cells,
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
                if not source_mentions_target and re.search(
                    r"\b(?:word|words)\b",
                    " ".join(extent_clause.split()),
                    flags=re.I,
                ):
                    source_mentions_target = (
                        _uk_repeal_table_mixed_clause_explicitly_names_target_with_parent(
                            extent_clause,
                            target=target,
                            affected_year=str(effect.affected_year or ""),
                        )
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
                            _UKRepealTableBroadContainerMatch(
                                table_index=table_index,
                                row_text=" | ".join((enactment_cell, extent_clause)),
                                enactment_cell=enactment_cell,
                                extent_cell=extent_clause,
                                enactment_match_basis=enactment_match_basis,
                                broad_container_target=broad_container_target,
                            )
                        )
                    continue
                if not _uk_repeal_table_clause_is_structural_repeal(extent_clause):
                    if (
                        source_supplies_repeal_action
                        and _uk_repeal_table_quoted_words_selector(extent_clause)[0]
                    ):
                        continue
                    norm_clause = " ".join(extent_clause.split()).lower()
                    if re.search(r"\b(?:word|words)\b", norm_clause) and re.search(
                        r"\b(?:section|sections|schedule|schedules|paragraph|paragraphs|"
                        r"subsection|subsections|sub-?paragraph|sub-?paragraphs)\b",
                        norm_clause,
                    ):
                        mixed_word_selector = _uk_repeal_table_mixed_clause_word_selector(
                            extent_clause,
                            target=target,
                        )
                        if mixed_word_selector:
                            matches.append(
                                _UKRepealTableStructuralMatch(
                                    table_index=table_index,
                                    row_text=" | ".join((enactment_cell, extent_clause)),
                                    enactment_cell=enactment_cell,
                                    extent_cell=extent_clause,
                                    enactment_match_basis=enactment_match_basis,
                                    reason_code="mixed_structural_and_word_repeal_split",
                                    mixed_word_selector=mixed_word_selector,
                                )
                            )
                        elif _uk_repeal_table_mixed_clause_explicitly_names_structural_target(
                            extent_clause,
                            target=target,
                        ):
                            matches.append(
                                _UKRepealTableStructuralMatch(
                                    table_index=table_index,
                                    row_text=" | ".join((enactment_cell, extent_clause)),
                                    enactment_cell=enactment_cell,
                                    extent_cell=extent_clause,
                                    enactment_match_basis=enactment_match_basis,
                                    reason_code="mixed_structural_and_word_repeal_split_structural_target",
                                )
                            )
                        else:
                            mixed_structural_word_matches.append(
                                _UKRepealTableMixedStructuralWordMatch(
                                    table_index=table_index,
                                    row_text=" | ".join((enactment_cell, extent_clause)),
                                    enactment_cell=enactment_cell,
                                    extent_cell=extent_clause,
                                    enactment_match_basis=enactment_match_basis,
                                )
                            )
                    continue
                matches.append(
                    _UKRepealTableStructuralMatch(
                        table_index=table_index,
                        row_text=" | ".join((enactment_cell, extent_clause)),
                        enactment_cell=enactment_cell,
                        extent_cell=extent_clause,
                        enactment_match_basis=enactment_match_basis,
                        reason_code=container_except_reason_code
                        or (
                            "source_repeal_schedule_structural_repeal"
                            if source_supplies_repeal_action
                            else ""
                        ),
                    )
                )

    if len(matches) != 1:
        if (
            source_supplies_repeal_action
            and not matches
            and not mixed_structural_word_matches
            and not broad_container_matches
        ):
            return _UKRepealTableStructuralRepeal(recognized=False)
        if not matches and len(mixed_structural_word_matches) == 1:
            mixed_match = mixed_structural_word_matches[0]
            return _UKRepealTableStructuralRepeal(
                recognized=True,
                reason_code="mixed_structural_and_word_repeal_requires_split",
                match_count=0,
                table_index=mixed_match.table_index,
                row_text=mixed_match.row_text,
                enactment_cell=mixed_match.enactment_cell,
                extent_cell=mixed_match.extent_cell,
                enactment_match_basis=mixed_match.enactment_match_basis,
            )
        if not matches and len(broad_container_matches) == 1:
            flat_repeal = _uk_flat_repeal_schedule_structural_repeal(
                effect=effect,
                extracted_text=extracted_text,
                target=target,
            )
            if flat_repeal.recognized:
                return flat_repeal
            broad_match = broad_container_matches[0]
            return _UKRepealTableStructuralRepeal(
                recognized=True,
                reason_code="broad_container_repeal_requires_grouped_feed_compilation",
                match_count=0,
                table_index=broad_match.table_index,
                row_text=broad_match.row_text,
                enactment_cell=broad_match.enactment_cell,
                extent_cell=broad_match.extent_cell,
                enactment_match_basis=broad_match.enactment_match_basis,
                broad_container_target=broad_match.broad_container_target,
            )
        if not matches and not mixed_structural_word_matches and not broad_container_matches:
            flat_repeal = _uk_flat_repeal_schedule_structural_repeal(
                effect=effect,
                extracted_text=extracted_text,
                target=target,
            )
            if flat_repeal.recognized:
                return flat_repeal
        return _UKRepealTableStructuralRepeal(
            recognized=True,
            reason_code="no_unique_matching_repeal_table_structural_row",
            match_count=len(matches),
        )

    match = matches[0]
    return _UKRepealTableStructuralRepeal(
        recognized=True,
        rule_id=match.rule_id,
        reason_code=match.reason_code,
        match_count=1,
        table_index=match.table_index,
        row_text=match.row_text,
        enactment_cell=match.enactment_cell,
        extent_cell=match.extent_cell,
        enactment_match_basis=match.enactment_match_basis,
        mixed_word_selector=match.mixed_word_selector,
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


def _uk_parenthesized_label_in_simple_range(
    text: str,
    *,
    label: str,
    word_pattern: str,
) -> bool:
    wanted_label = re.sub(r"[^0-9a-z]", "", (label or "").lower())
    if not wanted_label:
        return False
    for match in re.finditer(
        rf"\b{word_pattern}\s*\(\s*(?P<start>\d+[a-z]?)\s*\)"
        rf"\s*(?:to|-|–|—)\s*\(\s*(?P<end>\d+[a-z]?)\s*\)",
        text or "",
        flags=re.I,
    ):
        start_label = match.group("start").lower()
        end_label = match.group("end").lower()
        if wanted_label in {start_label, end_label}:
            return True
        if not (wanted_label.isdigit() and start_label.isdigit() and end_label.isdigit()):
            continue
        wanted = int(wanted_label)
        start = int(start_label)
        end = int(end_label)
        if min(start, end) <= wanted <= max(start, end):
            return True
    return False


def _uk_parenthesized_label_in_bare_simple_range(text: str, *, label: str) -> bool:
    """Return true for source-owned bare ranges like `(3) to (5)`."""
    wanted_label = re.sub(r"[^0-9a-z]", "", (label or "").lower())
    if not wanted_label:
        return False
    for match in re.finditer(
        r"\(\s*(?P<start>\d+[a-z]?)\s*\)\s*(?:to|-|–|—)\s*"
        r"\(\s*(?P<end>\d+[a-z]?)\s*\)",
        text or "",
        flags=re.I,
    ):
        start_label = match.group("start").lower()
        end_label = match.group("end").lower()
        if wanted_label in {start_label, end_label}:
            return True
        if not (wanted_label.isdigit() and start_label.isdigit() and end_label.isdigit()):
            continue
        wanted = int(wanted_label)
        start = int(start_label)
        end = int(end_label)
        if min(start, end) <= wanted <= max(start, end):
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
        before = text[max(0, m.start() - 48) : m.start()]
        ordinal_match = re.search(r"\b(?P<ordinal>[a-z]+(?:-[a-z]+)?)\s+$", before)
        if (
            is_numeric
            and ordinal_match is not None
            and _uk_ordinal_to_int(ordinal_match.group("ordinal")) == wanted_num
        ):
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
    if _uk_cell_has_reversed_section_descendant_scope(
        scope_text,
        section=section,
        descendant_labels=wanted,
    ):
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
        if len(wanted) == 1 and _uk_parenthesized_label_in_simple_range(
            segment,
            label=wanted[0],
            word_pattern=r"subsections?",
        ):
            return True
        if len(wanted) == 1 and _uk_parenthesized_label_in_bare_simple_range(
            segment,
            label=wanted[0],
        ):
            return True

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


def _uk_cell_has_reversed_section_descendant_scope(
    scope_text: str,
    *,
    section: str,
    descendant_labels: Sequence[str],
) -> bool:
    """Match source-owned phrasing like `paragraph (c) of section 85B(2)`."""
    section_pat = re.escape(section.lower())
    wanted = [label.lower() for label in descendant_labels if label]
    subsection = wanted[0] if wanted else ""
    paragraph = wanted[1] if len(wanted) >= 2 else ""
    subparagraph = wanted[2] if len(wanted) >= 3 else ""
    if subsection:
        subsection_pat = re.escape(subsection)
        if paragraph:
            paragraph_pat = re.escape(paragraph)
            if re.search(
                rf"\b(?:paragraph|para)\.?\s*\(\s*{paragraph_pat}\s*\)\s+"
                rf"of\s+section\s+{section_pat}\s*\(\s*{subsection_pat}\s*\)",
                scope_text,
                flags=re.I,
            ):
                if not subparagraph:
                    return True
                subparagraph_pat = re.escape(subparagraph)
                return re.search(
                    rf"\b(?:sub-?paragraph|subpara)\.?\s*\(\s*{subparagraph_pat}\s*\)",
                    scope_text,
                    flags=re.I,
                ) is not None
            if re.search(
                rf"\b(?:paragraph|para)\.?\s*\(\s*{paragraph_pat}\s*\)\s+"
                rf"of\s+subsection\s*\(\s*{subsection_pat}\s*\)"
                rf"(?:(?!\bsection\b).){{0,120}}\bsection\s+{section_pat}\b",
                scope_text,
                flags=re.I,
            ):
                if not subparagraph:
                    return True
                subparagraph_pat = re.escape(subparagraph)
                return re.search(
                    rf"\b(?:sub-?paragraph|subpara)\.?\s*\(\s*{subparagraph_pat}\s*\)",
                    scope_text,
                    flags=re.I,
                ) is not None
        return re.search(
            rf"\bsubsection\s*\(\s*{subsection_pat}\s*\)\s+of\s+section\s+{section_pat}\b",
            scope_text,
            flags=re.I,
        ) is not None
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
    text = _uk_table_cell_target_scope_text(text)
    scope_text = re.sub(r"[“\"'‘].*?[”\"'’]", "", text)
    labels = {kind: label for kind, label in target.path}
    section = labels.get("section", "")
    schedule = labels.get("schedule", "")
    paragraph = labels.get("paragraph", "")
    subsection = labels.get("subsection", "")
    subparagraph = labels.get("subparagraph", "")
    part = labels.get("part", "")
    chapter = labels.get("chapter", "")
    years = set(re.findall(r"\b(?:1[6-9]|20)\d{2}\b", scope_text))
    if years and affected_year and affected_year not in years:
        return False

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
            scope_text,
        )
        section_range_match = _uk_section_label_in_simple_range(scope_text, section)
        section_list_match = _uk_section_label_in_simple_list(scope_text, section)
        listed_section_match = (
            re.search(r"\bsections\b", scope_text) is not None
            and re.search(rf"\b{section_pat}\s*\(", scope_text) is not None
        )
        if (
            not direct_section_match
            and not section_range_match
            and not section_list_match
            and not listed_section_match
        ):
            return False
        if subsection and subsection.lower() == "table" and "table" not in text:
            return False
        descendant_labels = [
            label
            for label in (subsection, paragraph, subparagraph)
            if label and label.lower() != "table"
        ]
        if descendant_labels and not _uk_cell_has_section_descendant_scope(
            scope_text,
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


def _uk_table_cell_target_scope_text(text: str) -> str:
    """Drop descriptive target qualifiers without dropping address labels.

    Repeal schedules sometimes qualify an explicit target with a parenthetical
    such as `(as it has effect by virtue of section 196 of the Finance Act
    1994)`. That qualifier is source context for the named target, not an
    alternate target and not affected-Act identity. Preserve short address
    parentheticals like `(7)(a)`. Singular reference selectors after the target
    are likewise payload/preimage text, not target scope.
    """
    text = re.sub(
        r"\((?=[^)]*\s)[^)]*\b(?:act|article|effect|instrument|order|regulations?|section|virtue)\b[^)]*\)",
        "",
        text,
        flags=re.I,
    )
    return re.sub(
        r"\bthe\s+references?\s+to\s+.+$",
        "the reference to",
        text,
        flags=re.I,
    )


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
    """Return true when a section label is owned by a simple source range."""

    wanted_label = re.sub(r"[^0-9a-z]", "", (label or "").lower())
    if not wanted_label:
        return False
    for match in re.finditer(
        r"\bsections?\s+(?P<start>\d+[a-z]?)\s*(?:to|-|–|—)\s*(?P<end>\d+[a-z]?)\b",
        text or "",
        flags=re.I,
    ):
        start_label = match.group("start").lower()
        end_label = match.group("end").lower()
        if wanted_label in {start_label, end_label}:
            return True
        start_match = re.fullmatch(r"(?P<num>\d+)(?P<suffix>[a-z]?)", start_label)
        end_match = re.fullmatch(r"(?P<num>\d+)(?P<suffix>[a-z]?)", end_label)
        if wanted_label.isdigit() and start_match is not None and end_match is not None:
            wanted = int(wanted_label)
            start = int(start_match.group("num"))
            end = int(end_match.group("num"))
        elif (
            not wanted_label.isdigit()
            or not start_label.isdigit()
            or not end_label.isdigit()
        ):
            continue
        else:
            wanted = int(wanted_label)
            start = int(start_label)
            end = int(end_label)
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
    matches: list[_UKTableDrivenFeeSubstitutionMatch] = []

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
                    matches.append(
                        _UKTableDrivenFeeSubstitutionMatch(
                            table_index=table_index,
                            original=original,
                            replacement=new_fee,
                            row_text=" | ".join(row[:4]),
                        )
                    )

    if len(matches) == 0:
        return _UKTableDrivenWordSubstitution(recognized=False)

    if len(matches) > 1:
        return _UKTableDrivenWordSubstitution(
            recognized=True,
            replacement=matches[0].replacement,
            reason_code="no_unique_matching_table_row",
            match_count=len(matches),
        )

    match = matches[0]
    return _UKTableDrivenWordSubstitution(
        recognized=True,
        original=match.original,
        replacement=match.replacement,
        reason_code="",
        match_count=1,
        table_index=match.table_index,
        row_text=match.row_text,
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
    matches: list[_UKTableDrivenCorrespondingEntryMatch] = []
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
            matches.append(
                _UKTableDrivenCorrespondingEntryMatch(
                    table_index=table_index,
                    original=old_words,
                    row_text=" | ".join(row[:2]),
                )
            )

    if len(matches) != 1:
        return _UKTableDrivenWordSubstitution(
            recognized=True,
            replacement=replacement,
            reason_code="no_unique_matching_table_row",
            match_count=len(matches),
        )

    match = matches[0]
    return _UKTableDrivenWordSubstitution(
        recognized=True,
        original=match.original,
        replacement=replacement,
        reason_code="",
        match_count=1,
        table_index=match.table_index,
        row_text=match.row_text,
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
