from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lawvm.core.regex_recognition_coverage import (
    REGEX_RECOGNITION_FULLY_CLASSIFIED,
    REGEX_RECOGNITION_UNCLASSIFIED_GAP,
    RegexRecognitionCoverage,
    regex_source_text_hash,
)
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


@dataclass(frozen=True)
class NamedTableRowMixedClauseParseResult:
    clauses: Tuple[NamedTableRowMixedClause, ...] = ()
    regex_recognition_coverage: Tuple[RegexRecognitionCoverage, ...] = ()


@dataclass(frozen=True)
class NamedTableRowSingleClauseParseResult:
    clauses: Tuple[NamedTableRowSingleClause, ...] = ()
    regex_recognition_coverage: Tuple[RegexRecognitionCoverage, ...] = ()


# Compiled at module scope per §1.11.  Bounded lazy quantifiers replace
# unbounded .+? / .*? to prevent O(N^2) backtracking on long non-matching
# inputs.  200 chars per named-court segment is generous —
# typical käräjäoikeus names are 10–40 chars.
_MIXED_ROW_PATTERNS = [
    (
        "kohdat",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n\s+(.{1,200}?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohd[a-zäöå]*\s+"
            r"(?:sekä|ja)\s+muut[a-zäöå]*\s+(.{1,200}?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohd[a-zäöå]*(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
    (
        "kohta",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n\s+(.{1,200}?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohta\s+"
            r"(?:sekä|ja)\s+muut[a-zäöå]*\s+(.{1,200}?)\s+käräjäoikeu[a-zäöå]*(?:\s+koskev[a-zäöå]*)?\s+kohta(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
    (
        "osalta",
        re.compile(
            r"(\d+\s*[a-zäöå]?)\s*§:n.{0,200}?(.{1,200}?)\s+käräjäoikeuden\s+osalta\s+ja\s+muut[a-zäöå]*\s+\d+\s*[a-zäöå]?\s*§:n.{0,200}?(.{1,200}?)\s+käräjäoikeuden\s+osalta(?:\s+seuraavasti)?",
            flags=re.I,
        ),
    ),
]

# Lifted to module scope per §1.11 and bounded (lazy quantifiers).
# .*? before the section label and .+? for the court name segment are both
# bounded at 200 chars — amply covers any realistic Finnish johtolause clause.
_SINGLE_ROW_REPLACE_RE = re.compile(
    r"muut[a-zäöå]*\s+.{0,200}?(\d+\s*[a-zäöå]?)\s*§:n\s+(.{1,200}?)\s+käräjäoikeutta\s+koskev[a-zäöå]*\s+kohd[a-zäöå]*",
    flags=re.I,
)
_SINGLE_ROW_REPEAL_RE = re.compile(
    r"kumot[a-zäöå]*\s+.{0,200}?(\d+\s*[a-zäöå]?)\s*§:n\s+(.{1,200}?)\s+käräjäoikeutta\s+koskev[a-zäöå]*\s+kohd[a-zäöå]*",
    flags=re.I,
)
_REGIONAL_ROW_REPLACE_RE = re.compile(
    r"((?:\d+\s*[a-zäöå]?\s*(?:,|\bja\b)?\s*){1,6})§:n\s+(.{1,360}?)\s+"
    r"(?:lääniä|maakuntaa)\s+koskev[a-zäöå]*\s+kohd[a-zäöå]*",
    flags=re.I,
)
_KNOWN_COMPOUND_REGIONAL_ROW_NAMES = frozenset(
    {
        "turun ja porin",
    }
)

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


def _parse_regional_named_target_list(text: str) -> NamedTargetList:
    """Parse Finnish province/region ``kohta`` descriptors.

    Province formulas apply the terminal noun to a list of genitive names:
    ``Uudenmaan, Turun ja Porin, Hämeen ... lääniä koskevat kohdat``.  The
    ordinary named-row splitter treats every ``ja`` as a list separator, which
    would split the historical compound province ``Turun ja Porin``.  Keep the
    finite compound names owned here and split the remaining comma/conjunction
    chunks as separate target descriptors.
    """
    cleaned = _clean_clause_text(text)
    if not cleaned:
        return NamedTargetList(raw_text="")

    targets: List[str] = []
    modifiers: List[ClauseModifier] = []
    for comma_part in re.split(r"\s*,\s*", cleaned):
        part = comma_part.strip()
        if not part:
            continue
        norm_part = _norm_row_anchor_text(part)
        if norm_part in _KNOWN_COMPOUND_REGIONAL_ROW_NAMES:
            targets.append(norm_part)
            continue
        for piece in re.split(r"\s+ja\s+", part, flags=re.I):
            target, modifier = _classify_named_row_segment(piece)
            if target:
                targets.append(target)
            elif modifier:
                modifiers.append(modifier)
    return NamedTargetList(targets=tuple(targets), modifiers=tuple(modifiers), raw_text=cleaned)


def _parse_section_list(text: str) -> tuple[str, ...]:
    sections = tuple(re.sub(r"\s+", "", match.group(0)) for match in re.finditer(r"\d+\s*[a-zäöå]?", text))
    return sections


def _coverage_id(
    recognizer_id: str,
    source_hash: str,
    start: int,
    end: int,
) -> str:
    digest = re.sub(r"[^0-9a-f]", "", source_hash.lower())[:16]
    return f"{recognizer_id}:{digest}:{start}:{end}"


def _classify_named_row_ignored_span(text: str) -> str:
    cleaned = _clean_clause_text(text).strip(" ,;:.-–—―")
    if not cleaned:
        return "empty"
    lowered = cleaned.lower()
    if re.search(r"\b(kuitenkin|siltä\s+osin|lukuun\s+ottamatta|paitsi|edellyttäen)\b", lowered):
        return "unclassified"
    if re.fullmatch(r"(?:sekä|ja)\s+muut[a-zäöå]*", lowered):
        return "drafting_connector_and_action"
    if re.fullmatch(r"(?:muut[a-zäöå]*|kumot[a-zäöå]*)\b.*", lowered):
        return "source_context_and_action"
    if re.fullmatch(r"käräjäoikeuksien\b.*\bpäätöksen", lowered):
        return "source_context"
    if lowered in {"sekä", "ja"}:
        return "drafting_connector"
    return "unclassified"


def _named_row_ignored_span_row(
    source_text: str,
    *,
    start: int,
    end: int,
) -> list[dict[str, object]]:
    if end <= start:
        return []
    text = source_text[start:end]
    classification = _classify_named_row_ignored_span(text)
    if classification == "empty":
        return []
    return [
        {
            "span": [start, end],
            "classification": classification,
            "text_preview": source_text[start:end][:160],
            "could_alter_meaning": classification == "unclassified",
        }
    ]


def _named_row_coverage_row(
    *,
    recognizer_id: str,
    source_text: str,
    source_hash: str,
    source_artifact_id: str,
    match: re.Match[str],
    semantic_slots: dict[str, object],
    ignored_spans: list[dict[str, object]],
) -> RegexRecognitionCoverage:
    unclassified_count = sum(
        1 for row in ignored_spans if row.get("classification") == "unclassified"
    )
    return RegexRecognitionCoverage(
        coverage_id=_coverage_id(recognizer_id, source_hash, match.start(), match.end()),
        jurisdiction="fi",
        recognizer_id=recognizer_id,
        owner_phase="surface_syntax_frontend",
        source_artifact_id=source_artifact_id,
        source_text_hash=source_hash,
        matched_span=(match.start(), match.end()),
        coverage_status=(
            REGEX_RECOGNITION_UNCLASSIFIED_GAP
            if unclassified_count
            else REGEX_RECOGNITION_FULLY_CLASSIFIED
        ),
        semantic_slots=semantic_slots,
        ignored_spans=tuple(ignored_spans),
        required_proofs=(
            ("regex_skipped_span_classification",)
            if unclassified_count
            else ()
        ),
        detail={
            "matched_text_preview": source_text[match.start():match.end()][:240],
            "unclassified_ignored_span_count": unclassified_count,
            "rule_note": "bounded regex named-row coverage only; not replay authority",
        },
    )


def parse_named_table_row_mixed_clauses(johto: str) -> List[NamedTableRowMixedClause]:
    return list(parse_named_table_row_mixed_clauses_with_coverage(johto).clauses)


def parse_named_table_row_mixed_clauses_with_coverage(
    johto: str,
    *,
    source_artifact_id: str = "",
) -> NamedTableRowMixedClauseParseResult:
    text = _clean_clause_text(johto).lower()
    if "käräjäoikeu" not in text or "muut" not in text:
        return NamedTableRowMixedClauseParseResult()

    clauses: List[NamedTableRowMixedClause] = []
    coverage_rows: list[RegexRecognitionCoverage] = []
    source_hash = regex_source_text_hash(text)
    for pattern_kind, pattern in _MIXED_ROW_PATTERNS:
        for match in pattern.finditer(text):
            sec, repeal_clause, replace_clause = match.groups()
            section = re.sub(r"\s+", "", sec)
            repeal_rows = _parse_named_target_list(repeal_clause)
            replace_rows = _parse_named_target_list(replace_clause)
            if not repeal_rows.targets or not replace_rows.targets:
                continue
            raw_text = match.group(0)
            clauses.append(
                NamedTableRowMixedClause(
                    section=section,
                    repeal_rows=repeal_rows,
                    replace_rows=replace_rows,
                    pattern_kind=pattern_kind,
                    raw_text=raw_text,
                )
            )
            ignored_spans = _named_row_ignored_span_row(
                text,
                start=match.start(),
                end=match.start(1),
            )
            coverage_rows.append(
                _named_row_coverage_row(
                    recognizer_id=f"fi_named_table_row_mixed_{pattern_kind}",
                    source_text=text,
                    source_hash=source_hash,
                    source_artifact_id=source_artifact_id,
                    match=match,
                    semantic_slots={
                        "action": "REPEAL_AND_REPLACE",
                        "target_unit_kind": "named_table_row",
                        "target_section": section,
                        "pattern_kind": pattern_kind,
                        "repeal_rows": repeal_rows.targets,
                        "replace_rows": replace_rows.targets,
                        "repeal_row_clause": repeal_rows.raw_text,
                        "replace_row_clause": replace_rows.raw_text,
                    },
                    ignored_spans=ignored_spans,
                )
            )
    return NamedTableRowMixedClauseParseResult(
        clauses=tuple(clauses),
        regex_recognition_coverage=tuple(coverage_rows),
    )


def parse_named_table_row_single_clauses(johto: str) -> List[NamedTableRowSingleClause]:
    return list(parse_named_table_row_single_clauses_with_coverage(johto).clauses)


def parse_named_table_row_single_clauses_with_coverage(
    johto: str,
    *,
    source_artifact_id: str = "",
) -> NamedTableRowSingleClauseParseResult:
    text = _clean_clause_text(johto).lower()
    has_court_row = "käräjäoikeu" in text
    has_regional_row = ("lääniä koskev" in text or "maakuntaa koskev" in text) and "muut" in text
    if not has_court_row and not has_regional_row:
        return NamedTableRowSingleClauseParseResult()

    patterns = [
        ("replace", _SINGLE_ROW_REPLACE_RE),
        ("repeal", _SINGLE_ROW_REPEAL_RE),
    ]

    clauses: List[NamedTableRowSingleClause] = []
    coverage_rows: list[RegexRecognitionCoverage] = []
    source_hash = regex_source_text_hash(text)
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
            ignored_spans = _named_row_ignored_span_row(
                text,
                start=match.start(),
                end=match.start(1),
            )
            coverage_rows.append(
                _named_row_coverage_row(
                    recognizer_id=f"fi_named_table_row_single_{action}",
                    source_text=text,
                    source_hash=source_hash,
                    source_artifact_id=source_artifact_id,
                    match=match,
                    semantic_slots={
                        "action": action.upper(),
                        "target_unit_kind": "named_table_row",
                        "target_section": re.sub(r"\s+", "", sec),
                        "rows": rows.targets,
                        "row_clause": rows.raw_text,
                    },
                    ignored_spans=ignored_spans,
                )
            )
    if has_regional_row:
        for match in _REGIONAL_ROW_REPLACE_RE.finditer(text):
            raw_text = match.group(0)
            sections = _parse_section_list(match.group(1))
            rows = _parse_regional_named_target_list(match.group(2))
            if not sections or not rows.targets:
                continue
            for section in sections:
                clauses.append(
                    NamedTableRowSingleClause(
                        section=section,
                        action="replace",
                        rows=rows,
                        raw_text=raw_text,
                    )
                )
                ignored_spans = _named_row_ignored_span_row(
                    text,
                    start=match.start(),
                    end=match.start(1),
                )
                coverage_rows.append(
                    _named_row_coverage_row(
                        recognizer_id="fi_named_table_row_single_regional_replace",
                        source_text=text,
                        source_hash=source_hash,
                        source_artifact_id=source_artifact_id,
                        match=match,
                        semantic_slots={
                            "action": "REPLACE",
                            "target_unit_kind": "named_table_row",
                            "target_section": section,
                            "rows": rows.targets,
                            "row_clause": rows.raw_text,
                        },
                        ignored_spans=ignored_spans,
                    )
                )
    return NamedTableRowSingleClauseParseResult(
        clauses=tuple(clauses),
        regex_recognition_coverage=tuple(coverage_rows),
    )
