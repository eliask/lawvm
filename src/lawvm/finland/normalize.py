"""Op normalization helpers for the Finnish law amendment pipeline.

Extracted from grafter.py to allow independent testing and to break the
import cycle that would arise if a future normalize_and_compile_ops module
needed to import from grafter.

This module has NO imports from grafter.py.  It depends only on:
  - Python stdlib (re, typing, dataclasses)
  - lxml.etree (read-only — only for inspecting amendment XML structure)
  - lawvm.core.ir (LegalOperation)
  - lawvm.finland.ops (AmendmentOp, OpType, _lo_path_dict, _lo_with_path_update)
  - lawvm.finland.helpers (_norm_num_token, _expand_section_range)

grafter.py re-exports every public symbol from here for backward compatibility.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import List, Optional, Set, Tuple
from warnings import deprecated

import lxml.etree as etree

from lawvm.core.regex_recognition_coverage import (
    REGEX_RECOGNITION_FULLY_CLASSIFIED,
    REGEX_RECOGNITION_UNCLASSIFIED_GAP,
    RegexRecognitionCoverage,
    regex_source_text_hash,
)
from lawvm.finland.helpers import _expand_section_range, _norm_num_token
from lawvm.finland.ops import (
    AmendmentOp,
    OpType,
)
from lawvm.finland.references.lemma_gate import head_case_forms
from lawvm.finland.target_selector_facades import (
    fi_chapter_target,
    fi_part_target,
    fi_section_target,
    replace_target,
)

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level constants)
# Note: Only static patterns are pre-compiled. Patterns with dynamic components
# (e.g., re.escape(var), f-strings, etc.) remain as bare calls for clarity.
# ---------------------------------------------------------------------------

_RE_NON_ALNUM = re.compile(r"[^\d\w]")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_COMMA_OR_JA = re.compile(r"\s*,\s*|\s+ja\s+")
_RE_COMMA_OR_JA_ALT = re.compile(r"\s*(?:,|ja)\s*")
_RE_PARENS_STATUTE_REF = re.compile(r"\(\s*(\d+)\s*/\s*(\d{2,4})\s*\)")
_RE_SECTION_SIGN = re.compile(r"\b\d+\s*[a-z]?\s*§")
_RE_NUMBERED_LIST = re.compile(r"\b\d+\)\s")
_RE_LUU_OR_OSA = re.compile(r"\b(?:luku|osa)\b")
_RE_MUUTOS_VERBS = re.compile(r"\b(muutetaan|lisätään|korvataan|otetaan)\b")
_RE_STATUTE_CREATION = re.compile(r"\b(?:lakiin|asetuksen)\s+uusi\s+([^§]{1,120})§")

# Sub-provision unit alternation for the omnibus-repeal fallback discriminator
# (``§:n kohta/momentti ...``). The ``kohta`` (NOM/GEN) and ``momentti`` (NOM/GEN)
# case forms are GENERATED from the M1 morphology engine (paradigm inversion)
# rather than hand-enumerated, so this lane shares the single source of
# inflection truth with the reference lanes. ``johdantokappale`` is a fixed
# nominative compound (no case enumeration), kept as an explicit literal.
# The generated set reproduces the prior hand-written alternation byte-for-byte
# (``kohta|kohdan|momentti|momentin``); longest-first for alternation safety.
_SUBPROVISION_UNIT_ALT = "|".join(
    sorted(
        {
            *head_case_forms("kohta", (("NOM", "SG"), ("GEN", "SG"))),
            *head_case_forms("momentti", (("NOM", "SG"), ("GEN", "SG"))),
            "johdantokappale",
        },
        key=lambda s: (-len(s), s),
    )
)
_RE_SUBPROVISION_TARGET = re.compile(
    r"§:?n?\s+(?:\d[\d.]*\s+)?(?:" + _SUBPROVISION_UNIT_ALT + r")",
    flags=re.I,
)
_RE_CONTAINER_NOUN = re.compile(r"\b(luku|osa)\b")
_RE_NEW_SUBSECTION = re.compile(
    r"\buusi\s+("
    r"(?:\d+(?:\s*[–—―-]\s*\d+)?)"
    r"(?:\s*(?:,|ja)\s*\d+(?:\s*[–—―-]\s*\d+)?)*)"
    r"\s+(?:momentti\b|mom\.)"
)
_RE_NEW_ITEM = re.compile(
    r"\buusi\s+(?:näin\s+kuuluva\s+)?("
    r"(?:\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?)"
    r"(?:\s*(?:,|ja)\s*\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?)*)"
    r"\s+kohta\b"
)
_RE_STATUTE_CREATION_CHAPTER = re.compile(r"\blakiin\s+uusi\s+(\d+\s*[a-z]?)\s+luku\b")
_INSERT_ROOT_PART_FALLBACK_RE = re.compile(
    r"\blakiin\s+uusi\s+((?:[ivxlcdm]+|\d+)\s*[a-z]?)\s+osa\b",
    flags=re.I,
)
_INSERT_CHAPTER_SECTION_FALLBACK_RE = re.compile(
    r"\b(\d+\s*[a-z]?)\s+lukuun\s+uusi\s+([^§]{1,120})§",
    flags=re.I,
)
_INSERT_SUBSECTION_FALLBACK_RE = re.compile(
    r"(\d+\s*[a-z]?)\s*§\s*:ään\s*,?\s*(?:sellaisena\s+kuin\s+[^,]+,\s*)?"
    r"(.*?)(?=(?:\d+\s*[a-z]?\s*§\s*:ään)|(?:\d+\s*[a-z]?\s+luvun\s+\d+\s*[a-z]?\s*§\s*:)"
    r"|(?:\d+\s*[a-z]?\s+luvun\s+\d+\s*[a-z]?\s*§\s*:ään)|\bseuraavasti\b|$)",
    flags=re.I,
)
_INSERT_ITEM_FALLBACK_RE = re.compile(
    r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+momenttiin\s*,?\s*(?:sellaisena\s+kuin\s+[^,]+,\s*)?"
    r"(.*?)(?=(?:\d+\s*[a-z]?\s*§\s*:n\s*\d+\s+momenttiin)"
    r"|(?:\d+\s*[a-z]?\s*§\s*:ään)"
    r"|(?:\blakiin\s+uusi\b)|\bseuraavasti\b|$)",
    flags=re.I,
)


@dataclass(frozen=True)
class FallbackParseResult:
    ops: List[AmendmentOp]
    regex_recognition_coverage: tuple[RegexRecognitionCoverage, ...] = ()

# ---------------------------------------------------------------------------
# LO repair chain — operate on LegalOperation lists before AmendmentOp.from_lo
# ---------------------------------------------------------------------------


def _extract_grouped_container_targets(johto: str, noun: str) -> Set[str]:
    """Extract coordinated bare-number refs that inherit a trailing container noun.

    Motivating example from `1998/745` / `2012/475`:
    `2 §, 3, 4, 6 ja 7 luku sekä 40 §`
    Here `3, 4, 6, 7` are chapter refs even though only the last number carries
    the visible `luku` token.
    """
    text = _RE_WHITESPACE.sub(" ", johto or "").replace("\xa0", " ")
    labels: Set[str] = set()
    # lawvm-regex: owning_parser N-container coordinated bare-number container-noun inheritance over passed-in johto (label-interpolated dynamic pattern, left inline per §1.11); part of the deferred rank-3 fallback retirement (route into clause-AST/PEG + disable allows_target_guessing)
    for match in re.finditer(rf"((?:\d+\s*,\s*)*\d+(?:\s+ja\s+\d+)?)\s+{noun}\b", text, flags=re.I):
        cluster = match.group(1)
        for token in _RE_COMMA_OR_JA.split(cluster):
            token = token.strip()
            if re.fullmatch(r"\d+[a-z]?", token, flags=re.I):
                labels.add(token.lower())
    return labels


# ---------------------------------------------------------------------------
# Fallback op extractors — operate on johtolause text, produce AmendmentOp lists
# ---------------------------------------------------------------------------

TYPE_CODES = {"P", "L", "O"}
_SECTION_TOKEN_RE = re.compile(r"\d+(?:\s*[a-z](?![a-z]))?", flags=re.I)


def _expand_spaced_insert_label_list_ir(text: str) -> List[str]:
    """Expand numeric and same-base letter-suffix ranges from a johtolause label list."""
    labels: List[str] = []
    for token in _RE_COMMA_OR_JA_ALT.split(text):
        token = token.strip().lower()
        if not token:
            continue
        token = _RE_WHITESPACE.sub(" ", token)
        m_same_base = re.fullmatch(
            r"(\d+)\s*([a-z])\s*[–—―-]\s*(?:(\d+)\s*)?([a-z])",
            token,
            flags=re.I,
        )
        if m_same_base:
            start_num = m_same_base.group(1)
            start_suffix = m_same_base.group(2)
            end_num = m_same_base.group(3) or start_num
            end_suffix = m_same_base.group(4)
            if start_num == end_num and start_suffix <= end_suffix:
                labels.extend(f"{start_num}{chr(code)}" for code in range(ord(start_suffix), ord(end_suffix) + 1))
                continue
        compact = token.replace(" ", "")
        expanded = _expand_section_range(compact)
        if expanded != [compact]:
            labels.extend(label.lower() for label in expanded)
            continue
        if re.fullmatch(r"\d+[a-z]?", compact, flags=re.I):
            labels.append(compact.lower())
    return labels


def _sec1_fallback_peg_skip_required(
    johto: str,
    parent_id: str,
    *,
    parser_has_structural_targets: bool = False,
) -> bool:
    """True when sec_1 fallback text should suppress PEG extraction.

    The skip is only justified for omnibus repeal structures where the fallback
    text is still cross-statute or citation-free after parent restriction. If
    the remaining text is explicitly scoped to the current parent statute, PEG
    should still run even when sec_1 uses numbered enumeration.
    """
    if not johto or "kumotaan" not in johto.lower():
        return False
    if _RE_NUMBERED_LIST.search(johto) is None and _RE_LUU_OR_OSA.search(johto) is None and "§" not in johto:
        return False

    refs = _RE_PARENS_STATUTE_REF.findall(johto)
    if not refs:
        # Citation-free sec_1 fallback is the classic omnibus-repeal shape, but
        # parent-restricted fallback text can also be citation-free while still
        # naming explicit § targets that PEG should parse normally.
        if "§" in johto and _RE_SECTION_SIGN.search(johto):
            return False
        return True

    lower_tail = johto.lower().split("kumotaan", 1)[1]
    has_non_repeal_ops = bool(_RE_MUUTOS_VERBS.search(lower_tail))
    has_explicit_section_targets = bool(_RE_SECTION_SIGN.search(lower_tail))
    has_subprovision_targets = bool(_RE_SUBPROVISION_TARGET.search(lower_tail))
    if parser_has_structural_targets and (
        has_explicit_section_targets or has_subprovision_targets
    ):
        return False

    try:
        parent_year, parent_num = parent_id.split("/")
        parent_num_i = int(parent_num)
    except (ValueError, AttributeError):
        return True

    parent_year_short = parent_year[-2:]
    normalized_refs: list[tuple[int, str]] = []
    for ref_num, ref_year in refs:
        try:
            normalized_refs.append((int(ref_num), ref_year))
        except ValueError:
            return True

    for ref_num_i, ref_year in normalized_refs:
        if ref_num_i == parent_num_i and ref_year in {parent_year, parent_year_short}:
            continue
        return True
    if has_explicit_section_targets:
        return False
    if has_subprovision_targets:
        return False
    return not has_non_repeal_ops


def _extract_insert_subsection_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit ``§:ään uusi N momentti`` inserts from long johtolause.

    FALLBACK: Compensates for PEG3 missing subsection inserts in complex
    ``sellaisena kuin`` clauses.  Remove when PEG3 handles all
    ``§:ään ... uusi N momentti`` patterns — verify with bench.
    """
    ops: List[AmendmentOp] = []
    seen: Set[Tuple[str, int]] = set()
    # lawvm-regex: owning_parser N-insert-fallback subsection insert recognizer over passed-in johto; gated by allows_target_guessing, has RegexRecognitionCoverage shadow; part of the deferred rank-3 fallback retirement
    for m in re.finditer(
        r"(\d+\s*[a-z]?)\s*§\s*:ään\s*,?\s*(?:sellaisena\s+kuin\s+[^,]+,\s*)?"
        r"(.*?)(?=(?:\d+\s*[a-z]?\s*§\s*:ään)|(?:\d+\s*[a-z]?\s+luvun\s+\d+\s*[a-z]?\s*§\s*:)"
        r"|(?:\d+\s*[a-z]?\s+luvun\s+\d+\s*[a-z]?\s*§\s*:ään)|\bseuraavasti\b|$)",
        cleaned,
        flags=re.I,
    ):
        sec = m.group(1)
        clause = m.group(2)
        sec_norm = _RE_WHITESPACE.sub("", sec).lower()
        if not sec_norm:
            continue
        for mom_clause in _RE_NEW_SUBSECTION.findall(clause):
            for mom in _expand_spaced_insert_label_list_ir(mom_clause):
                try:
                    mom_i = int(mom)
                except ValueError:
                    continue
                key = (sec_norm, mom_i)
                if key in seen:
                    continue
                seen.add(key)
                ops.append(
                    AmendmentOp(
                        op_id="",
                        op_type=OpType.INSERT,
                        **fi_section_target(sec_norm, subsection=mom_i),
                    )
                )
    return ops


def _extract_insert_subsection_ops_fallback_with_coverage(
    cleaned: str,
    *,
    source_artifact_id: str = "",
) -> FallbackParseResult:
    ops = _extract_insert_subsection_ops_fallback(cleaned)
    coverage_rows: list[RegexRecognitionCoverage] = []
    source_hash = regex_source_text_hash(cleaned)
    # lawvm-regex: owning_parser N-insert-fallback subsection coverage-row builder (same recognizer); part of the deferred rank-3 fallback retirement
    for m in _INSERT_SUBSECTION_FALLBACK_RE.finditer(cleaned):
        sec_norm = _RE_WHITESPACE.sub("", m.group(1)).lower()
        clause = m.group(2)
        matched_moments: list[int] = []
        ignored_spans: list[dict[str, object]] = []
        cursor = 0
        for mom_match in _RE_NEW_SUBSECTION.finditer(clause):
            ignored_spans.extend(
                _regex_ignored_span_rows(
                    cleaned,
                    base_offset=m.start(2),
                    clause=clause,
                    start=cursor,
                    end=mom_match.start(),
                )
            )
            cursor = mom_match.end()
            for mom in _expand_spaced_insert_label_list_ir(mom_match.group(1)):
                if mom.isdigit():
                    matched_moments.append(int(mom))
        ignored_spans.extend(
            _regex_ignored_span_rows(
                cleaned,
                base_offset=m.start(2),
                clause=clause,
                start=cursor,
                end=len(clause),
            )
        )
        if matched_moments:
            coverage_rows.append(
                _regex_recognition_coverage_row(
                    recognizer_id="fi_insert_subsection_fallback",
                    source_hash=source_hash,
                    source_artifact_id=source_artifact_id,
                    matched_span=(m.start(), m.end()),
                    semantic_slots={
                        "action": "INSERT",
                        "target_unit_kind": "subsection",
                        "target_section": sec_norm,
                        "target_subsections": tuple(matched_moments),
                    },
                    ignored_spans=ignored_spans,
                    matched_text=cleaned[m.start():m.end()],
                )
            )
    return FallbackParseResult(ops=ops, regex_recognition_coverage=tuple(coverage_rows))


def _extract_insert_item_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit ``§:n N momenttiin uusi K kohta`` inserts from long johtolause.

    FALLBACK: Compensates for PEG3 missing item inserts inside mixed
    ``muutetaan ..., lisätään ...`` clause families with ``sellaisena kuin``
    provenance spans. Remove when PEG3 handles these mixed clauses natively.
    """
    ops: List[AmendmentOp] = []
    seen: Set[Tuple[str, int, str]] = set()
    # lawvm-regex: owning_parser N-insert-fallback item insert recognizer over passed-in johto; gated by allows_target_guessing; part of the deferred rank-3 fallback retirement
    for m in _INSERT_ITEM_FALLBACK_RE.finditer(cleaned):
        sec = m.group(1)
        mom = m.group(2)
        clause = m.group(3)
        sec_norm = _RE_WHITESPACE.sub("", sec).lower()
        try:
            mom_i = int(mom)
        except ValueError:
            continue
        if not sec_norm:
            continue
        for item_clause in _RE_NEW_ITEM.findall(clause):
            for item in _expand_spaced_insert_label_list_ir(item_clause):
                key = (sec_norm, mom_i, item)
                if key in seen:
                    continue
                seen.add(key)
                ops.append(
                    AmendmentOp(
                        op_id="",
                        op_type=OpType.INSERT,
                        **fi_section_target(sec_norm, subsection=mom_i, item=item),
                    )
                )
    return ops


def _extract_insert_item_ops_fallback_with_coverage(
    cleaned: str,
    *,
    source_artifact_id: str = "",
) -> FallbackParseResult:
    ops = _extract_insert_item_ops_fallback(cleaned)
    coverage_rows: list[RegexRecognitionCoverage] = []
    source_hash = regex_source_text_hash(cleaned)
    # lawvm-regex: owning_parser N-insert-fallback item coverage-row builder (same recognizer); part of the deferred rank-3 fallback retirement
    for m in _INSERT_ITEM_FALLBACK_RE.finditer(cleaned):
        sec_norm = _RE_WHITESPACE.sub("", m.group(1)).lower()
        try:
            mom_i = int(m.group(2))
        except ValueError:
            continue
        clause = m.group(3)
        matched_items: list[str] = []
        ignored_spans: list[dict[str, object]] = []
        cursor = 0
        for item_match in _RE_NEW_ITEM.finditer(clause):
            ignored_spans.extend(
                _regex_ignored_span_rows(
                    cleaned,
                    base_offset=m.start(3),
                    clause=clause,
                    start=cursor,
                    end=item_match.start(),
                )
            )
            cursor = item_match.end()
            matched_items.extend(_expand_spaced_insert_label_list_ir(item_match.group(1)))
        ignored_spans.extend(
            _regex_ignored_span_rows(
                cleaned,
                base_offset=m.start(3),
                clause=clause,
                start=cursor,
                end=len(clause),
            )
        )
        if matched_items:
            coverage_rows.append(
                _regex_recognition_coverage_row(
                    recognizer_id="fi_insert_item_fallback",
                    source_hash=source_hash,
                    source_artifact_id=source_artifact_id,
                    matched_span=(m.start(), m.end()),
                    semantic_slots={
                        "action": "INSERT",
                        "target_unit_kind": "item",
                        "target_section": sec_norm,
                        "target_subsection": mom_i,
                        "target_items": tuple(matched_items),
                    },
                    ignored_spans=ignored_spans,
                    matched_text=cleaned[m.start():m.end()],
                )
            )
    return FallbackParseResult(ops=ops, regex_recognition_coverage=tuple(coverage_rows))


def _prune_shadowed_parent_subsection_insert_fallbacks(ops: List[AmendmentOp]) -> List[AmendmentOp]:
    """Drop coarse fallback subsection inserts shadowed by explicit item inserts."""
    explicit_item_targets = {
        (_norm_num_token(op.target_section), op.target_paragraph)
        for op in ops
        if op.op_type == OpType.INSERT and op.target_section and op.target_paragraph is not None and op.target_item
    }
    if not explicit_item_targets:
        return ops
    pruned: List[AmendmentOp] = []
    for op in ops:
        if (
            op.op_type == OpType.INSERT
            and op.target_section
            and (_norm_num_token(op.target_section), op.target_paragraph) in explicit_item_targets
            and op.target_paragraph is not None
            and op.target_item is None
            and op.target_special is None
        ):
            continue
        pruned.append(op)
    return pruned


def _regex_ignored_span_rows(
    source_text: str,
    *,
    base_offset: int,
    clause: str,
    start: int,
    end: int,
) -> list[dict[str, object]]:
    if end <= start:
        return []
    text = clause[start:end]
    if not text:
        return []
    classification = _classify_regex_ignored_span(text)
    if classification == "empty":
        return []
    absolute_start = base_offset + start
    absolute_end = base_offset + end
    return [
        {
            "span": [absolute_start, absolute_end],
            "classification": classification,
            "text_preview": source_text[absolute_start:absolute_end][:160],
            "could_alter_meaning": classification == "unclassified",
        }
    ]


def _classify_regex_ignored_span(text: str) -> str:
    cleaned = _RE_WHITESPACE.sub(" ", text or "").strip(" ,;:.-–—―")
    if not cleaned:
        return "empty"
    lowered = cleaned.lower()
    if lowered in {"lisätään", "lisää", "ja", "sekä"}:
        return "drafting_connector"
    if re.fullmatch(r"(?:sellaisena|sellaisina)\s+kuin\b.*", lowered):
        return "source_version_qualifier"
    return "unclassified"


def _regex_coverage_id(
    recognizer_id: str,
    source_hash: str,
    start: int,
    end: int,
) -> str:
    digest = re.sub(r"[^0-9a-f]", "", source_hash.lower())[:16]
    return f"{recognizer_id}:{digest}:{start}:{end}"


def _regex_recognition_coverage_row(
    *,
    recognizer_id: str,
    source_hash: str,
    source_artifact_id: str,
    matched_span: tuple[int, int],
    semantic_slots: dict[str, object],
    ignored_spans: list[dict[str, object]],
    matched_text: str,
) -> RegexRecognitionCoverage:
    unclassified_count = sum(
        1 for row in ignored_spans if row.get("classification") == "unclassified"
    )
    return RegexRecognitionCoverage(
        coverage_id=_regex_coverage_id(
            recognizer_id,
            source_hash,
            matched_span[0],
            matched_span[1],
        ),
        jurisdiction="fi",
        recognizer_id=recognizer_id,
        owner_phase="surface_syntax_frontend",
        source_artifact_id=source_artifact_id,
        source_text_hash=source_hash,
        matched_span=matched_span,
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
            "matched_text_preview": matched_text[:240],
            "unclassified_ignored_span_count": unclassified_count,
            "rule_note": "bounded regex fallback coverage only; not replay authority",
        },
    )


def _regex_label_clause_ignored_spans(
    source_text: str,
    *,
    base_offset: int,
    clause: str,
) -> list[dict[str, object]]:
    ignored_spans: list[dict[str, object]] = []
    cursor = 0
    # lawvm-regex: owning_parser section-label tokenizer for coverage ignored-span accounting; lexer-shaped
    for label_match in _SECTION_TOKEN_RE.finditer(clause):
        ignored_spans.extend(
            _regex_ignored_span_rows(
                source_text,
                base_offset=base_offset,
                clause=clause,
                start=cursor,
                end=label_match.start(),
            )
        )
        cursor = label_match.end()
    ignored_spans.extend(
        _regex_ignored_span_rows(
            source_text,
            base_offset=base_offset,
            clause=clause,
            start=cursor,
            end=len(clause),
        )
    )
    return ignored_spans


def _extract_insert_container_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover bounded container insert fallbacks.

    FALLBACK: Compensates for the new parser dropping chapter-scoped section
    inserts that sit inside long heterogeneous insertion lists (where a mid-list
    anomaly halts the native continuation loop), plus the narrow citation-prose
    shape ``lakiin uusi N osa`` where fallback parsing may see the root part
    insertion after surface parsing has declined. Root-level chapter and
    combined ``lakiin uusi N luku [ja M §]`` lanes remain parser-owned.
    """
    ops: List[AmendmentOp] = []
    seen_sections: Set[Tuple[str, str]] = set()
    seen_parts: Set[str] = set()

    # lawvm-regex: owning_parser N-insert-fallback root-part insert recognizer over passed-in johto; gated by allows_target_guessing; part of the deferred rank-3 fallback retirement
    for m in _INSERT_ROOT_PART_FALLBACK_RE.finditer(cleaned):
        part = _norm_num_token(m.group(1))
        if not part or part in seen_parts:
            continue
        seen_parts.add(part)
        ops.append(
            AmendmentOp(
                op_id="",
                op_type=OpType.INSERT,
                **fi_part_target(part),
            )
        )

    # lawvm-regex: owning_parser N-insert-fallback chapter-scoped section insert recognizer over passed-in johto; gated by allows_target_guessing; part of the deferred rank-3 fallback retirement
    for m in _INSERT_CHAPTER_SECTION_FALLBACK_RE.finditer(cleaned):
        chapter = _RE_WHITESPACE.sub("", m.group(1)).lower()
        clause = m.group(2)
        # lawvm-regex: owning_parser section-label tokenizer inside matched clause; lexer-shaped
        for sec in _SECTION_TOKEN_RE.findall(clause):
            norm = _RE_WHITESPACE.sub("", sec).lower()
            key = (chapter, norm)
            if not chapter or not norm or key in seen_sections:
                continue
            seen_sections.add(key)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type=OpType.INSERT,
                    **fi_section_target(norm, chapter=chapter),
                )
            )
    return ops


def _extract_insert_container_ops_fallback_with_coverage(
    cleaned: str,
    *,
    source_artifact_id: str = "",
) -> FallbackParseResult:
    ops = _extract_insert_container_ops_fallback(cleaned)
    coverage_rows: list[RegexRecognitionCoverage] = []
    source_hash = regex_source_text_hash(cleaned)
    seen_sections: Set[Tuple[str, str]] = set()
    seen_parts: Set[str] = set()

    # lawvm-regex: owning_parser N-insert-fallback root-part coverage-row builder (same recognizer); part of the deferred rank-3 fallback retirement
    for m in _INSERT_ROOT_PART_FALLBACK_RE.finditer(cleaned):
        part = _norm_num_token(m.group(1))
        if not part or part in seen_parts:
            continue
        seen_parts.add(part)
        coverage_rows.append(
            _regex_recognition_coverage_row(
                recognizer_id="fi_insert_root_part_fallback",
                source_hash=source_hash,
                source_artifact_id=source_artifact_id,
                matched_span=(m.start(), m.end()),
                semantic_slots={
                    "action": "INSERT",
                    "target_unit_kind": "part",
                    "target_part": part,
                },
                ignored_spans=[],
                matched_text=cleaned[m.start():m.end()],
            )
        )

    # lawvm-regex: owning_parser N-insert-fallback chapter-section coverage-row builder (same recognizer); part of the deferred rank-3 fallback retirement
    for m in _INSERT_CHAPTER_SECTION_FALLBACK_RE.finditer(cleaned):
        chapter = _RE_WHITESPACE.sub("", m.group(1)).lower()
        clause = m.group(2)
        target_sections: list[str] = []
        # lawvm-regex: owning_parser section-label tokenizer inside matched clause; lexer-shaped
        for sec in _SECTION_TOKEN_RE.findall(clause):
            norm = _RE_WHITESPACE.sub("", sec).lower()
            key = (chapter, norm)
            if not chapter or not norm or key in seen_sections:
                continue
            seen_sections.add(key)
            target_sections.append(norm)
        if not target_sections:
            continue
        coverage_rows.append(
            _regex_recognition_coverage_row(
                recognizer_id="fi_insert_chapter_scoped_section_fallback",
                source_hash=source_hash,
                source_artifact_id=source_artifact_id,
                matched_span=(m.start(), m.end()),
                semantic_slots={
                    "action": "INSERT",
                    "target_unit_kind": "section",
                    "target_chapter": chapter,
                    "target_sections": tuple(target_sections),
                },
                ignored_spans=_regex_label_clause_ignored_spans(
                    cleaned,
                    base_offset=m.start(2),
                    clause=clause,
                ),
                matched_text=cleaned[m.start():m.end()],
            )
        )
    return FallbackParseResult(ops=ops, regex_recognition_coverage=tuple(coverage_rows))


def _extract_root_replace_ops_from_body_fallback(
    johto: str,
    muutos_tree: etree._Element,
) -> List[AmendmentOp]:
    """Recover whole-section replaces from generic ``muutetaan ..., seuraavasti:`` johtolause.

    Some amendment acts restate a small decision almost in full and use only a
    generic lead-in like ``muutetaan [act], seuraavasti:`` without enumerating the
    affected sections in prose. In that narrow shape, the direct body sections are
    the operative replace targets.

    FALLBACK: Body-structure inference for amendments with no explicit section
    targets.  Unlike the insert fallbacks, this may be genuinely irreducible
    (target must be inferred from body, not johtolause).  Remove only if PEG3
    gains body-aware extraction -- verify with bench on generic-lead amendments.
    """
    cleaned = _RE_WHITESPACE.sub(" ", johto).strip().lower()
    if not cleaned.startswith("muutetaan"):
        return []
    if "seuraavasti" not in cleaned:
        return []
    if "kumotaan" in cleaned or "lisätään" in cleaned:
        return []
    # lawvm-regex: owning_parser N-body-replace §-presence guard over passed-in johto; part of the deferred rank-3 fallback retirement
    if re.search(r"\d+\s*[a-z]?\s*§", cleaned, flags=re.I):
        return []

    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []
    direct_sections = body.findall("./{*}section")
    if not direct_sections:
        direct_sections = body.findall("./{*}hcontainer/{*}section")
    if len(direct_sections) < 2:
        return []
    if body.find(".//{*}chapter") is not None or body.find(".//{*}part") is not None:
        return []

    ops: List[AmendmentOp] = []
    for sec in direct_sections:
        num_el = sec.find("{*}num")
        if num_el is None or not (num_el.text or "").strip():
            continue
        label = _norm_num_token(num_el.text or "")
        if not label:
            continue
        ops.append(AmendmentOp(op_id="", op_type=OpType.REPLACE, **fi_section_target(label)))
    return _dedupe_fallback_ops_ir(ops)


# ---------------------------------------------------------------------------
# Op dedup / merge helpers
# ---------------------------------------------------------------------------


def _op_signature(op: AmendmentOp) -> Tuple[object, ...]:
    return (
        op.op_type,
        op.target_unit_kind,
        op.target_chapter,
        op.target_section,
        op.target_paragraph,
        op.target_item,
        op.target_special,
    )


def _is_root_insert_op(op: AmendmentOp) -> bool:
    return (
        op.op_type == OpType.INSERT and op.target_paragraph is None and op.target_item is None and op.target_special is None
    )


def _same_root_insert_target(lhs: AmendmentOp, rhs: AmendmentOp) -> bool:
    return (
        _is_root_insert_op(lhs)
        and _is_root_insert_op(rhs)
        and lhs.target_unit_kind == rhs.target_unit_kind
        and lhs.target_section == rhs.target_section
    )


def _dedupe_fallback_ops_ir(ops: List[AmendmentOp]) -> List[AmendmentOp]:
    deduped: List[AmendmentOp] = []
    seen: Set[
        Tuple[
            str,
            str,
            str,
            Optional[int],
            Optional[str],
            Optional[str],
            Optional[str],
            Optional[str],
            Optional[str],
        ]
    ] = set()
    for op in ops:
        destination_label: Optional[str] = None
        if op.lo is not None and op.lo.destination is not None:
            dest_path = tuple(op.lo.destination.path)
            if dest_path:
                destination_label = "/".join(f"{kind}:{label}" for kind, label in dest_path if label)
        key = (
            op.op_type,
            op.target_unit_kind,
            _norm_num_token(op.target_section) if op.target_section else "",
            op.target_paragraph,
            op.target_item,
            op.target_special,
            _norm_num_token(op.target_chapter) if op.target_chapter else None,
            _norm_num_token(op.target_part) if op.target_part else None,
            destination_label,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(op)
    return deduped


def _extract_replace_ops_from_muutetaan_tail(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit REPLACE refs from a trailing `muutetaan ..., seuraavasti` clause.

    This is intentionally narrower than the broad fallback parser: it only looks
    inside the tail introduced by `muutetaan` and only recovers plain section
    and subsection targets. It exists for mixed clauses where PEG found the
    repeal side but dropped the subsequent replace side entirely.
    """
    # lawvm-regex: owning_parser N-tail-replace muutetaan-tail recognizer over passed-in johto; part of the deferred rank-3 fallback retirement
    m = re.search(r"\bmuutetaan\b(.*?)(?:\bseuraavasti\b|$)", cleaned, flags=re.I)
    if m is None:
        return []
    tail = re.sub(r"\(\s*\d+/\d+\s*\)", " ", m.group(1))
    # lawvm-regex: owning_parser N-tail-replace §-presence guard
    if not re.search(r"\d+\s*[a-z]?\s*§", tail, flags=re.I):
        return []
    # lawvm-regex: owning_parser N-tail-replace container-shape exclusion guard
    if re.search(r"\b(luku|osa|kohta|otsikko|johd|johdantokappale)\b", tail, flags=re.I):
        return []

    refs: List[Tuple[str, Optional[str]]] = []
    refs.extend(
        (_RE_WHITESPACE.sub("", sec), mom)
        # lawvm-regex: owning_parser N-tail-replace subsection ref recognizer; part of the deferred rank-3 fallback retirement
        for sec, mom in re.findall(
            r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+moment(?:ti|in)",
            tail,
            flags=re.I,
        )
    )
    refs.extend(
        (_RE_WHITESPACE.sub("", sec), None)
        # lawvm-regex: owning_parser N-tail-replace whole-section ref recognizer; part of the deferred rank-3 fallback retirement
        for sec in re.findall(
            r"(\d+\s*[a-z]?)\s*§(?!\s*:)",
            tail,
            flags=re.I,
        )
    )
    # lawvm-regex: owning_parser N-tail-replace section-list ref recognizer; part of the deferred rank-3 fallback retirement
    for sec_list in re.findall(
        r"((?:\d+\s*[–—―-]\s*\d+|\d+)(?:\s*(?:,|ja)\s*(?:\d+\s*[–—―-]\s*\d+|\d+))*)\s*§(?!\s*:)",
        tail,
        flags=re.I,
    ):
        refs.extend((sec, None) for sec in _expand_numeric_section_list_ir(sec_list))

    if not refs:
        return []

    ops = [
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            **fi_section_target(sec, subsection=int(mom) if mom is not None else None),
        )
        for sec, mom in refs
    ]
    return _dedupe_fallback_ops_ir(ops)


def _expand_numeric_section_list_ir(text: str) -> List[str]:
    labels: List[str] = []
    for token in re.split(r"\s*(?:,|ja)\s*", text):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*[–—―-]\s*(\d+)", token)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            labels.extend(str(i) for i in range(lo, hi + 1))
            continue
        if re.fullmatch(r"\d+", token):
            labels.append(token)
    return labels


# ---------------------------------------------------------------------------
# Fallback op heuristics — parse johtolause text when LLM/PEG yields nothing
# ---------------------------------------------------------------------------


@deprecated(
    "Legacy fallback op heuristic (rank-3 fallback retirement, gated by "
    "allows_target_guessing). New callers must route johtolause through the "
    "owning johtolause/forest parser in lawvm.finland.frontend_compile; this "
    "regex heuristic only fires when the typed parse yields no ops and is being "
    "strangled out."
)
def parse_ops_fallback_heuristic(johto: str) -> List[AmendmentOp]:
    """Deterministic fallback for very simple johtolause patterns.

    Used only when the LLM returns no ops. Keeps scope intentionally narrow:
    whole-section or whole-momentti changes without ranges, kohta targets, or chapter/part refs.
    """
    cleaned = _RE_WHITESPACE.sub(" ", johto).strip().lower()
    if "voimaantulosäännös" in cleaned:
        return []
    if (
        "lisätään" not in cleaned
        and "siirretään" not in cleaned
        and re.search(r"\bsekä\s+(?:kumotaan|muutetaan)\b", cleaned)  # lawvm-regex: owning_parser N-fallback-heuristic multi-verb split discriminator; part of the deferred rank-3 fallback retirement
    ):
        # lawvm-regex: owning_parser N-fallback-heuristic verb-clause splitter; part of the deferred rank-3 fallback retirement
        verb_matches = list(re.finditer(r"\b(kumotaan|muutetaan)\b", cleaned))
        split_ops: List[AmendmentOp] = []
        for i, match in enumerate(verb_matches):
            start = match.start()
            end = verb_matches[i + 1].start() if i + 1 < len(verb_matches) else len(cleaned)
            chunk = cleaned[start:end].strip(" ,")
            chunk = re.sub(r"^(?:sekä|ja)\s+", "", chunk)
            if not chunk or chunk == cleaned:
                continue
            with warnings.catch_warnings():
                # Internal recursion of this legacy fallback over verb-split
                # chunks; the @deprecated signal is for external callers, not
                # the fallback's own bounded self-recursion.
                warnings.simplefilter("ignore", DeprecationWarning)
                split_ops.extend(parse_ops_fallback_heuristic(chunk))
        if split_ops:
            return _dedupe_fallback_ops_ir(split_ops)
    insert_subsection_ops = _extract_insert_subsection_ops_fallback(cleaned)
    insert_item_ops = _extract_insert_item_ops_fallback(cleaned)
    insert_container_ops = _extract_insert_container_ops_fallback(cleaned)
    fallback_insert_ops = _prune_shadowed_parent_subsection_insert_fallbacks(
        insert_subsection_ops + insert_item_ops + insert_container_ops
    )

    repeal_range_ops: List[AmendmentOp] = []
    # lawvm-regex: owning_parser N-fallback-heuristic repeal-range op-minter over passed-in johto; gated by allows_target_guessing; part of the deferred rank-3 fallback retirement
    for sec, start, end in re.findall(
        r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s*[–—―-]\s*(\d+)\s+moment(?:ti|in)",
        cleaned,
        flags=re.I,
    ):
        sec_norm = _RE_WHITESPACE.sub("", sec)
        try:
            lo, hi = int(start), int(end)
        except ValueError:
            continue
        if lo > hi:
            lo, hi = hi, lo
        for mom in range(lo, hi + 1):
            repeal_range_ops.append(
                AmendmentOp(
                    op_id="",
                    op_type=OpType.REPEAL,
                    **fi_section_target(sec_norm, subsection=mom),
                )
            )
    # lawvm-regex: owning_parser N-fallback-heuristic non-repeal verb guard; part of the deferred rank-3 fallback retirement
    has_non_repeal_verbs = bool(re.search(r"\b(muutetaan|lisätään|korvataan|otetaan|siirretään)\b", cleaned))
    pure_repeal_range_clause = bool(repeal_range_ops) and not has_non_repeal_verbs

    # lawvm-regex: owning_parser N-fallback-heuristic container-shape guard; part of the deferred rank-3 fallback retirement
    container_shape = bool(re.search(r"\b(luku|osa|otsikko|johd|johdantokappale|kohta)\b", cleaned))
    if container_shape and pure_repeal_range_clause:
        return repeal_range_ops
    # Remove cited statute numbers like "(64/2015)" so target extraction focuses on
    # the amended provision reference that follows the citation.
    cleaned = re.sub(r"\(\s*\d+/\d+\s*\)", " ", cleaned)
    op_type: Optional[OpType] = None
    _KW_TO_OP: tuple[tuple[str, OpType], ...] = (
        ("muutetaan", OpType.REPLACE),
        ("muuttaa", OpType.REPLACE),
        ("kumotaan", OpType.REPEAL),
        ("kumoaa", OpType.REPEAL),
        ("lisätään", OpType.INSERT),
        ("lisää", OpType.INSERT),
        ("siirretään", OpType.REPLACE),
        ("siirtää", OpType.REPLACE),
    )
    for kw, mapped in _KW_TO_OP:
        if kw in cleaned:
            op_type = mapped
            break
    if op_type is None:
        return repeal_range_ops if pure_repeal_range_clause else fallback_insert_ops
    refs: List[Tuple[str, Optional[str]]] = []
    refs.extend(
        (sec, mom)
        # lawvm-regex: owning_parser N-fallback-heuristic subsection-target recognizer; part of the deferred rank-3 fallback retirement
        for sec, mom in re.findall(
            r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+moment(?:ti|in)",
            cleaned,
            flags=re.I,
        )
    )
    # lawvm-regex: owning_parser N-fallback-heuristic moment-list target recognizer; part of the deferred rank-3 fallback retirement
    for sec, moments in re.findall(
        r"(\d+\s*[a-z]?)\s*§\s*(?::n\s*)?"
        r"(\d+(?:\s*(?:,|ja)\s*\d+){0,12})\s+moment(?:ti|in)",
        cleaned,
        flags=re.I,
    ):
        # lawvm-regex: owning_parser moment-label tokenizer; lexer-shaped
        refs.extend((sec, mom) for mom in re.findall(r"\d+", moments))
    refs.extend(
        (sec, None)
        # lawvm-regex: owning_parser N-fallback-heuristic whole-section target recognizer; part of the deferred rank-3 fallback retirement
        for sec in re.findall(
            r"(\d+\s*[a-z]?)\s*§(?!\s*:)"
            r"(?!\s*\d+(?:\s*(?:,|ja)\s*\d+){0,12}\s+moment)",
            cleaned,
            flags=re.I,
        )
    )
    # lawvm-regex: owning_parser N-fallback-heuristic section-list target recognizer; part of the deferred rank-3 fallback retirement
    for sec_list in re.findall(
        r"((?:\d+\s*[–—―-]\s*\d+|\d+)(?:\s*(?:,|ja)\s*(?:\d+\s*[–—―-]\s*\d+|\d+))*)"
        r"\s*§(?!\s*:)(?!\s*\d+(?:\s*(?:,|ja)\s*\d+){0,12}\s+moment)",
        cleaned,
        flags=re.I,
    ):
        refs.extend((sec, None) for sec in _expand_numeric_section_list_ir(sec_list))
    if not refs or len(refs) > 8:
        return repeal_range_ops if pure_repeal_range_clause else fallback_insert_ops
    ops = []
    for sec, mom in refs:
        ops.append(
            AmendmentOp(
                op_id="",
                op_type=op_type,
                **fi_section_target(_RE_WHITESPACE.sub("", sec), subsection=int(mom) if mom else None),
            )
        )

    insert_matches = [
        (_RE_WHITESPACE.sub("", sec), int(mom))
        # lawvm-regex: owning_parser N-fallback-heuristic insert-subsection op-minter; part of the deferred rank-3 fallback retirement
        for sec, mom in re.findall(
            r"lisätään\s+(\d+\s*[a-z]?)\s*§\s*:ään\s+uusi\s+(\d+)\s+momentti",
            cleaned,
            flags=re.I,
        )
    ]
    for sec, mom in insert_matches:
        if not any(
            op.op_type == OpType.INSERT and op.target_section == sec and op.target_paragraph == mom and not op.target_item
            for op in ops
        ):
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type=OpType.INSERT,
                    **fi_section_target(sec, subsection=mom),
                )
            )

    # lawvm-regex: owning_parser N-fallback-heuristic renumber-on-insert op-rewriter; part of the deferred rank-3 fallback retirement
    for sec, old_mom, new_mom in re.findall(
        r"lisätään\s+(\d+\s*[a-z]?)\s*§\s*:ään\s+uusi\s+\d+\s+momentti\s*,\s*jolloin\s+(?:muutettu|nykyinen)\s+(\d+)\s+momentti\s+siirtyy\s+(\d+)\s+momentiksi",
        cleaned,
        flags=re.I,
    ):
        sec_norm = _RE_WHITESPACE.sub("", sec)
        for i, op in enumerate(ops):
            if (
                op.op_type == OpType.REPLACE
                and op.target_section == sec_norm
                and op.target_paragraph == int(old_mom)
                and not op.target_item
            ):
                ops[i] = dc_replace(op, **replace_target(op, target_paragraph=int(new_mom)))
                break
    if fallback_insert_ops:
        fallback_insert_keys = {
            (
                _RE_WHITESPACE.sub("", str(op.target_section or "")).lower(),
                op.target_paragraph,
                str(op.target_item or "") or None,
                str(op.target_special or "") or None,
            )
            for op in fallback_insert_ops
        }
        ops = [
            op
            for op in ops
            if (
                op.op_type == OpType.INSERT
                or (
                    _RE_WHITESPACE.sub("", str(op.target_section or "")).lower(),
                    op.target_paragraph,
                    str(op.target_item or "") or None,
                    str(op.target_special or "") or None,
                )
                not in fallback_insert_keys
            )
        ]

    return _dedupe_fallback_ops_ir(
        _prune_shadowed_parent_subsection_insert_fallbacks(repeal_range_ops + fallback_insert_ops + ops)
    )


@deprecated(
    "Legacy fallback op heuristic (coverage-diagnostic shadow of "
    "parse_ops_fallback_heuristic; rank-3 fallback retirement). New callers must "
    "route johtolause through the owning johtolause/forest parser in "
    "lawvm.finland.frontend_compile; this regex heuristic only fires when the "
    "typed parse yields no ops and is being strangled out."
)
def parse_ops_fallback_heuristic_with_coverage(
    johto: str,
    *,
    source_artifact_id: str = "",
) -> FallbackParseResult:
    """Return fallback ops plus passive regex span coverage diagnostics.

    This is intentionally a shadow API.  It does not change which fallback ops
    replay sees; it exposes whether bounded regex recognizers skipped text that
    is still semantically unowned.
    """

    with warnings.catch_warnings():
        # Internal delegation to the sibling legacy fallback; both are demoted
        # together. The @deprecated signal targets external callers.
        warnings.simplefilter("ignore", DeprecationWarning)
        ops = parse_ops_fallback_heuristic(johto)
    cleaned = _RE_WHITESPACE.sub(" ", johto).strip().lower()
    container_coverage = _extract_insert_container_ops_fallback_with_coverage(
        cleaned,
        source_artifact_id=source_artifact_id,
    ).regex_recognition_coverage
    subsection_coverage = _extract_insert_subsection_ops_fallback_with_coverage(
        cleaned,
        source_artifact_id=source_artifact_id,
    ).regex_recognition_coverage
    item_coverage = _extract_insert_item_ops_fallback_with_coverage(
        cleaned,
        source_artifact_id=source_artifact_id,
    ).regex_recognition_coverage
    return FallbackParseResult(
        ops=ops,
        regex_recognition_coverage=(
            *container_coverage,
            *subsection_coverage,
            *item_coverage,
        ),
    )


@deprecated(
    "Legacy title-only fallback op heuristic (rank-3 fallback retirement). New "
    "callers must route the amendment through the owning johtolause/forest "
    "parser in lawvm.finland.frontend_compile; this title-driven repeal-only "
    "heuristic fires only when the body yields no ops and is being strangled out."
)
def parse_ops_title_fallback(title: str) -> List[AmendmentOp]:
    """Recover narrow title-only amendment semantics when the body yields no ops.

    Motivating statute: `1998/745` amendment `2005/636`, whose operative effect is
    entirely visible in the title `... lain 5 luvun kumoamisesta` while the parsed
    johtolause/LLM path returns `NONE`. Keeping this fallback title-driven and
    repeal-only avoids smearing broader semantic guessing into the front-end.
    """
    cleaned = _RE_WHITESPACE.sub(" ", title or "").strip().lower()
    if not cleaned:
        return []

    ops: List[AmendmentOp] = []

    # lawvm-regex: owning_parser N-title-fallback chapter-repeal title op-minter; part of the deferred rank-3 fallback retirement
    for chapter in re.findall(r"(\d+[a-z]?)\s+luvun\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(AmendmentOp(op_id="", op_type=OpType.REPEAL, **fi_chapter_target(chapter)))

    # lawvm-regex: owning_parser N-title-fallback part-repeal title op-minter; part of the deferred rank-3 fallback retirement
    for part in re.findall(r"(\d+[a-z]?)\s+osan\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(AmendmentOp(op_id="", op_type=OpType.REPEAL, **fi_part_target(part)))

    # lawvm-regex: owning_parser N-title-fallback section-repeal title op-minter; part of the deferred rank-3 fallback retirement
    for sec in re.findall(r"(\d+[a-z]?)\s*§(?::n)?\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(
            AmendmentOp(
                op_id="",
                op_type=OpType.REPEAL,
                **fi_section_target(_RE_WHITESPACE.sub("", sec)),
            )
        )

    deduped: List[AmendmentOp] = []
    seen: Set[Tuple[str, str, str]] = set()
    for op in ops:
        key = (op.op_type, op.target_unit_kind, _norm_num_token(op.target_section))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(op)
    return deduped
