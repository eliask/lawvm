from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lawvm.finland.helpers import _norm_row_anchor_text


@dataclass(frozen=True)
class ClauseModifier:
    kind: str
    text: str


@dataclass(frozen=True)
class NamedTargetList:
    targets: Tuple[str, ...] = ()
    modifiers: Tuple[ClauseModifier, ...] = ()
    raw_text: str = ""


@dataclass(frozen=True)
class NamedTableRowMixedClause:
    section: str
    repeal_rows: NamedTargetList
    replace_rows: NamedTargetList
    pattern_kind: str
    raw_text: str


@dataclass(frozen=True)
class NamedTableRowSingleClause:
    section: str
    action: str
    rows: NamedTargetList
    raw_text: str


_MIXED_ROW_PATTERNS = [
    (
        "kohdat",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n\s+(.+?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohd[a-zäöå]*\s+"
            r"(?:sekä|ja)\s+muut[a-zäöå]*\s+(.+?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohd[a-zäöå]*(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
    (
        "kohta",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n\s+(.+?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohta\s+"
            r"(?:sekä|ja)\s+muut[a-zäöå]*\s+(.+?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohta(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
    (
        "osalta",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n.*?(.+?)\s+käräjäoikeuden\s+osalta\s+ja\s+muut[a-zäöå]*\s+\d+\s*[a-zäöå]?\s*§:n.*?(.+?)\s+käräjäoikeuden\s+osalta(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
]

_MODIFIER_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    ("version_qualifier", re.compile(r"\bsellais(?:ena|ina)\b", flags=re.I)),
    ("version_qualifier", re.compile(r"\bviimeksi\b", flags=re.I)),
    ("source_citation", re.compile(r"\bmainitulla\b", flags=re.I)),
    ("source_citation", re.compile(r"\b(?:lailla|asetuksessa|asetuksella|päätöksellä)\b", flags=re.I)),
    ("date_citation", re.compile(r"\b\d{1,2}\s+päivänä\b", flags=re.I)),
    ("issuer_citation", re.compile(r"\boikeusministeriön\b", flags=re.I)),
]


def _clean_clause_text(text: str) -> str:
    # Callers always pass johto-derived text, which is already Zs-normalized
    # by _normalize_fi_parse_text upstream of normalize_and_compile_ops.
    return re.sub(r"\s+", " ", text or "").strip()


def _is_section_reference(text: str) -> bool:
    """Return True if *text* looks like a Finnish section reference (e.g. '5 §', '5a §:n')."""
    return bool(re.search(r"§", text))


def _classify_named_row_segment(text: str) -> tuple[Optional[str], Optional[ClauseModifier]]:
    cleaned = _clean_clause_text(text)
    cleaned = re.sub(r"^(?:ja|sekä)\s+", "", cleaned, flags=re.I)
    if not cleaned:
        return None, None

    for kind, pattern in _MODIFIER_PATTERNS:
        if pattern.search(cleaned):
            return None, ClauseModifier(kind=kind, text=cleaned)

    norm = _norm_row_anchor_text(cleaned)
    if not norm:
        return None, None
    # Reject true section references (contain §) but accept tariff/code-style
    # numeric identifiers (e.g. "1234", "90.12", "H 01") as valid row names.
    if _is_section_reference(cleaned):
        return None, ClauseModifier(kind="numeric_reference", text=cleaned)
    return norm, None


def _parse_named_target_list(text: str) -> NamedTargetList:
    cleaned = _clean_clause_text(text)
    if not cleaned:
        return NamedTargetList(raw_text="")

    targets: List[str] = []
    modifiers: List[ClauseModifier] = []
    # Narrow current scope: conjunction-separated target names plus separate
    # citation/provenance segments. Mixed in-segment target+citation phrases
    # belong in the next AST/elaboration pass, not in grafter blacklists.
    parts = re.split(r"\s*,\s*|\s*,?\s+ja\s+", cleaned, flags=re.I)
    for part in parts:
        target, modifier = _classify_named_row_segment(part)
        if target:
            targets.append(target)
        elif modifier:
            modifiers.append(modifier)
    return NamedTargetList(targets=tuple(targets), modifiers=tuple(modifiers), raw_text=cleaned)


def parse_named_table_row_mixed_clauses(johto: str) -> List[NamedTableRowMixedClause]:
    text = _clean_clause_text(johto).lower()
    if "käräjäoikeu" not in text or "muut" not in text:
        return []

    clauses: List[NamedTableRowMixedClause] = []
    for pattern_kind, pattern in _MIXED_ROW_PATTERNS:
        for match in pattern.finditer(text):
            sec, repeal_clause, replace_clause = match.groups()
            section = re.sub(r"\s+", "", sec)
            repeal_rows = _parse_named_target_list(repeal_clause)
            replace_rows = _parse_named_target_list(replace_clause)
            if not repeal_rows.targets or not replace_rows.targets:
                continue
            clauses.append(
                NamedTableRowMixedClause(
                    section=section,
                    repeal_rows=repeal_rows,
                    replace_rows=replace_rows,
                    pattern_kind=pattern_kind,
                    raw_text=match.group(0),
                )
            )
    return clauses


def parse_named_table_row_single_clauses(johto: str) -> List[NamedTableRowSingleClause]:
    text = _clean_clause_text(johto).lower()
    if "käräjäoikeu" not in text:
        return []

    patterns = [
        (
            "replace",
            re.compile(
                r"muut[a-zäöå]*\s+.*?(\d+\s*[a-zäöå]?)\s*§:n\s+(.+?)\s+käräjäoikeutta\s+koskev[a-zäöå]*\s+kohd[a-zäöå]*",
                flags=re.I,
            ),
        ),
        (
            "repeal",
            re.compile(
                r"kumot[a-zäöå]*\s+.*?(\d+\s*[a-zäöå]?)\s*§:n\s+(.+?)\s+käräjäoikeutta\s+koskev[a-zäöå]*\s+kohd[a-zäöå]*",
                flags=re.I,
            ),
        ),
    ]

    clauses: List[NamedTableRowSingleClause] = []
    for action, pattern in patterns:
        for match in pattern.finditer(text):
            raw_text = match.group(0)
            if re.search(r"\b(?:sekä|ja)\s+muut[a-zäöå]*\b", raw_text, flags=re.I):
                continue
            sec, row_clause = match.groups()
            rows = _parse_named_target_list(row_clause)
            if not rows.targets:
                continue
            clauses.append(
                NamedTableRowSingleClause(
                    section=re.sub(r"\s+", "", sec),
                    action=action,
                    rows=rows,
                    raw_text=raw_text,
                )
            )
    return clauses
