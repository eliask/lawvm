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
from dataclasses import replace as dc_replace
from typing import List, Optional, Set, Tuple

import lxml.etree as etree

from lawvm.finland.helpers import _expand_section_range, _norm_num_token
from lawvm.finland.ops import (
    AmendmentOp,
    OpType,
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
_RE_CONTAINER_NOUN = re.compile(r"\b(luku|osa)\b")
_RE_NEW_SUBSECTION = re.compile(
    r"\buusi\s+("
    r"(?:\d+(?:\s*[–—―-]\s*\d+)?)"
    r"(?:\s*(?:,|ja)\s*\d+(?:\s*[–—―-]\s*\d+)?)*)"
    r"\s+momentti\b"
)
_RE_NEW_ITEM = re.compile(
    r"\buusi\s+("
    r"(?:\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?)"
    r"(?:\s*(?:,|ja)\s*\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?)*)"
    r"\s+kohta\b"
)
_RE_STATUTE_CREATION_CHAPTER = re.compile(r"\blakiin\s+uusi\s+(\d+\s*[a-z]?)\s+luku\b")

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


def _sec1_fallback_peg_skip_required(johto: str, parent_id: str) -> bool:
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
    has_subprovision_targets = bool(
        re.search(
            r"§:?n?\s+(?:\d[\d.]*\s+)?(?:kohta|kohdan|momentti|momentin|johdantokappale)",
            lower_tail,
        )
    )

    try:
        parent_year, parent_num = parent_id.split("/")
        parent_num_i = int(parent_num)
    except (ValueError, AttributeError):
        return True

    parent_year_short = parent_year[-2:]
    for ref_num, ref_year in refs:
        try:
            ref_num_i = int(ref_num)
        except ValueError:
            return True
        if ref_num_i != parent_num_i or ref_year not in {parent_year, parent_year_short}:
            return True
    if has_explicit_section_targets:
        return False
    if has_subprovision_targets:
        return False
    return not has_non_repeal_ops


def _extract_insert_section_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover law-level whole-section inserts from complex johtolause text.

    FALLBACK: Compensates for PEG3 undercounting section-range inserts with
    inherited suffixes (e.g. ``uusi 14a, 14b, 14c §``).  Remove when PEG3
    handles all ``lakiin uusi ... §`` patterns — verify with
    ``lawvm bench --compare`` showing 0 regressions after disabling.
    """
    ops: List[AmendmentOp] = []
    seen: Set[str] = set()
    for m in re.finditer(
        r"\b(?:lakiin|asetuksen)\s+(?:siitä\s+lailla\s+\d+/\d+\s+kumotun\s+)?"
        r"(\d+\s*[a-z]?)\s*§:n\s+tilalle\s+uusi\s+(\d+\s*[a-z]?)\s*§",
        cleaned,
        flags=re.I,
    ):
        reinstated = _RE_WHITESPACE.sub("", m.group(2)).lower()
        if not reinstated or reinstated in seen:
            continue
        seen.add(reinstated)
        ops.append(
            AmendmentOp(
                op_id="",
                op_type="INSERT",
                target_section=reinstated,
                target_unit_kind="section",
            )
        )
    for m in re.finditer(r"\b(?:lakiin|asetuksen)\s+uusi\s+([^§]{1,120})§", cleaned, flags=re.I):
        clause = m.group(1)
        if _RE_CONTAINER_NOUN.search(clause):
            continue
        for sec in _expand_spaced_insert_label_list_ir(clause):
            norm = _RE_WHITESPACE.sub("", sec).lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=norm,
                    target_unit_kind="section",
                )
            )
    for m in re.finditer(
        r"\buuden\s+((?:\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?"
        r"(?:\s*(?:,|ja)\s*\d+\s*[a-z]?(?:\s*[–—―-]\s*\d+\s*[a-z]?)?)*)?)\s*§",
        cleaned,
        flags=re.I,
    ):
        clause = m.group(1)
        for sec in _expand_spaced_insert_label_list_ir(clause):
            norm = _RE_WHITESPACE.sub("", sec).lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=norm,
                    target_unit_kind="section",
                )
            )
    return ops


def _extract_insert_subsection_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit ``§:ään uusi N momentti`` inserts from long johtolause.

    FALLBACK: Compensates for PEG3 missing subsection inserts in complex
    ``sellaisena kuin`` clauses.  Remove when PEG3 handles all
    ``§:ään ... uusi N momentti`` patterns — verify with bench.
    """
    ops: List[AmendmentOp] = []
    seen: Set[Tuple[str, int]] = set()
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
                        op_type="INSERT",
                        target_section=sec_norm,
                        target_unit_kind="section",
                        target_paragraph=mom_i,
                    )
                )
    return ops


def _extract_insert_item_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit ``§:n N momenttiin uusi K kohta`` inserts from long johtolause.

    FALLBACK: Compensates for PEG3 missing item inserts inside mixed
    ``muutetaan ..., lisätään ...`` clause families with ``sellaisena kuin``
    provenance spans. Remove when PEG3 handles these mixed clauses natively.
    """
    ops: List[AmendmentOp] = []
    seen: Set[Tuple[str, int, str]] = set()
    for m in re.finditer(
        r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+momenttiin\s*,?\s*(?:sellaisena\s+kuin\s+[^,]+,\s*)?"
        r"(.*?)(?=(?:\d+\s*[a-z]?\s*§\s*:n\s*\d+\s+momenttiin)"
        r"|(?:\d+\s*[a-z]?\s*§\s*:ään)"
        r"|(?:\blakiin\s+uusi\b)|\bseuraavasti\b|$)",
        cleaned,
        flags=re.I,
    ):
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
                        op_type="INSERT",
                        target_section=sec_norm,
                        target_unit_kind="section",
                        target_paragraph=mom_i,
                        target_item=item,
                    )
                )
    return ops


def _prune_shadowed_parent_subsection_insert_fallbacks(ops: List[AmendmentOp]) -> List[AmendmentOp]:
    """Drop coarse fallback subsection inserts shadowed by explicit item inserts."""
    explicit_item_targets = {
        (_norm_num_token(op.target_section), op.target_paragraph)
        for op in ops
        if op.op_type == "INSERT" and op.target_section and op.target_paragraph is not None and op.target_item
    }
    if not explicit_item_targets:
        return ops
    pruned: List[AmendmentOp] = []
    for op in ops:
        if (
            op.op_type == "INSERT"
            and op.target_section
            and (_norm_num_token(op.target_section), op.target_paragraph) in explicit_item_targets
            and op.target_paragraph is not None
            and op.target_item is None
            and op.target_special is None
        ):
            continue
        pruned.append(op)
    return pruned


def _extract_insert_container_ops_fallback(cleaned: str) -> List[AmendmentOp]:
    """Recover chapter inserts, combined root inserts, and chapter-scoped section inserts.

    FALLBACK: Compensates for PEG3 missing ``lakiin uusi N luku`` and
    ``N lukuun uusi M §`` patterns.  Remove when PEG3 handles all
    chapter-level insert forms — verify with bench.
    """
    ops: List[AmendmentOp] = []
    seen_chapters: Set[str] = set()
    seen_sections: Set[Tuple[str, str]] = set()

    for m in re.finditer(
        r"\blakiin\s+uusi\s+(.{1,80}?)\s+luku\s+ja\s+(.{1,140}?)\s*§",
        cleaned,
        flags=re.I,
    ):
        chapter_clause = m.group(1)
        section_clause = m.group(2)
        for chapter in _expand_spaced_insert_label_list_ir(chapter_clause):
            if chapter in seen_chapters:
                continue
            seen_chapters.add(chapter)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=chapter,
                    target_unit_kind="chapter",
                )
            )
        for sec in _expand_spaced_insert_label_list_ir(section_clause):
            key = ("", sec)
            if key in seen_sections:
                continue
            seen_sections.add(key)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=sec,
                    target_unit_kind="section",
                )
            )

    for m in re.finditer(r"\blakiin\s+uusi\s+(\d+\s*[a-z]?)\s+luku\b", cleaned, flags=re.I):
        chapter = _RE_WHITESPACE.sub("", m.group(1)).lower()
        if not chapter or chapter in seen_chapters:
            continue
        seen_chapters.add(chapter)
        ops.append(
            AmendmentOp(
                op_id="",
                op_type="INSERT",
                target_section=chapter,
                target_unit_kind="chapter",
            )
        )

    for m in re.finditer(r"\b(\d+\s*[a-z]?)\s+lukuun\s+uusi\s+([^§]{1,120})§", cleaned, flags=re.I):
        chapter = _RE_WHITESPACE.sub("", m.group(1)).lower()
        clause = m.group(2)
        for sec in _SECTION_TOKEN_RE.findall(clause):
            norm = _RE_WHITESPACE.sub("", sec).lower()
            key = (chapter, norm)
            if not chapter or not norm or key in seen_sections:
                continue
            seen_sections.add(key)
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=norm,
                    target_unit_kind="section",
                    target_chapter=chapter,
                )
            )
    return ops


def _extract_root_insert_ops_fallback(johto: str) -> List[AmendmentOp]:
    """Recover only whole-object insert roots that PEG commonly undercounts.

    FALLBACK: Delegates to section + container fallbacks.  Remove when both
    sub-functions are removed (i.e. PEG3 covers all insert patterns).
    """
    cleaned = _RE_WHITESPACE.sub(" ", johto).strip().lower()
    return _extract_insert_section_ops_fallback(cleaned) + _extract_insert_container_ops_fallback(cleaned)


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
        ops.append(AmendmentOp(op_id="", op_type="REPLACE", target_section=label, target_unit_kind="section"))
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
        op.op_type == "INSERT" and op.target_paragraph is None and op.target_item is None and op.target_special is None
    )


def _same_root_insert_target(lhs: AmendmentOp, rhs: AmendmentOp) -> bool:
    return (
        _is_root_insert_op(lhs)
        and _is_root_insert_op(rhs)
        and lhs.target_unit_kind == rhs.target_unit_kind
        and lhs.target_section == rhs.target_section
    )


def _merge_root_insert_supplements(
    existing_ops: List[AmendmentOp],
    fallback_insert_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Add only root inserts that are still missing from the compiled op stream.

    The fallback root extractor is intentionally less precise than PEG: it can
    recover that a section root exists without preserving chapter scope. If PEG
    already emitted an insert for the same section root under a chapter, keep the
    PEG version and skip the weaker fallback duplicate.

    Also skips when PEG has ANY op targeting the same section (even at subsection
    level) — a section-level INSERT would overwrite the master section including
    subsections that PEG correctly left unchanged.

    FEATURE FLAG: Restored behind LAWVM_FALLBACK_MERGES=1 to test against
    regressors. See notes/PRO_RESPONSE3_4_regression_hunting.md §1.
    """
    existing = {_op_signature(op) for op in existing_ops}
    existing_root_inserts = [op for op in existing_ops if _is_root_insert_op(op)]
    # Sections already targeted by PEG at any granularity
    peg_targeted_sections: Set[str] = set()
    for op in existing_ops:
        if op.target_unit_kind == "section" and op.target_section:
            peg_targeted_sections.add(_norm_num_token(op.target_section))
    supplemented: List[AmendmentOp] = []
    for op in fallback_insert_ops:
        sig = _op_signature(op)
        if sig in existing:
            continue
        if any(_same_root_insert_target(op, prev) for prev in existing_root_inserts):
            continue
        # Don't supplement if PEG already targets this section at any level
        if op.target_unit_kind == "section" and op.target_section:
            if _norm_num_token(op.target_section) in peg_targeted_sections:
                continue
        op = dc_replace(
            op,
            extraction_provenance_tags=tuple(dict.fromkeys((*op.extraction_provenance_tags, "root_insert_supplement"))),
        )
        existing.add(sig)
        existing_root_inserts.append(op)
        supplemented.append(op)
    return existing_ops + supplemented


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


def _merge_missing_insert_supplements(
    ops: List[AmendmentOp],
    fallback_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Merge narrow fallback-only INSERT ops that PEG missed.

    FEATURE FLAG: Restored behind LAWVM_FALLBACK_MERGES=1 to test against
    regressors. See notes/PRO_RESPONSE3_4_regression_hunting.md §1.
    """
    merged = list(ops)
    seen = {
        (
            op.op_type,
            op.target_unit_kind,
            op.target_section,
            op.target_paragraph,
            op.target_item,
            op.target_special,
            op.target_chapter,
        )
        for op in ops
    }
    def _mark_scoped_winner(weaker_key: tuple[str, str, str, int | None, str | None, str | None]) -> None:
        for idx, existing in enumerate(merged):
            existing_key = (
                existing.op_type,
                existing.target_unit_kind,
                _norm_num_token(existing.target_section) if existing.target_section else "",
                existing.target_paragraph,
                existing.target_item,
                existing.target_special,
            )
            if existing_key != weaker_key or existing.op_type != "INSERT" or not existing.target_chapter:
                continue
            merged[idx] = dc_replace(
                existing,
                extraction_provenance_tags=tuple(
                    dict.fromkeys((*existing.extraction_provenance_tags, "fallback_insert_supplement_shadowed"))
                ),
            )
            return
    scoped_insert_targets = {
        (
            op.op_type,
            op.target_unit_kind,
            _norm_num_token(op.target_section) if op.target_section else "",
            op.target_paragraph,
            op.target_item,
            op.target_special,
        )
        for op in ops
        if op.op_type == "INSERT" and op.target_chapter
    }
    for op in fallback_ops:
        if op.op_type != "INSERT":
            continue
        key = (
            op.op_type,
            op.target_unit_kind,
            op.target_section,
            op.target_paragraph,
            op.target_item,
            op.target_special,
            op.target_chapter,
        )
        if key in seen:
            continue
        if op.target_chapter is None:
            weaker_key = (
                op.op_type,
                op.target_unit_kind,
                _norm_num_token(op.target_section) if op.target_section else "",
                op.target_paragraph,
                op.target_item,
                op.target_special,
            )
            if weaker_key in scoped_insert_targets:
                _mark_scoped_winner(weaker_key)
                continue
        op = dc_replace(
            op,
            fallback_provenance=True,
            extraction_provenance_tags=tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "fallback_insert_supplement"))
            ),
        )
        merged.append(op)
        seen.add(key)
    return merged


def _merge_missing_replace_supplements(
    ops: List[AmendmentOp],
    fallback_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Merge narrow fallback-only REPLACE ops that PEG missed.

    FEATURE FLAG: Restored behind LAWVM_FALLBACK_MERGES=1 to test against
    regressors. See notes/PRO_RESPONSE3_4_regression_hunting.md §1.
    """
    merged = list(ops)
    seen = {_op_signature(op) for op in ops}

    def _mark_scoped_winner(weaker_key: tuple[str, str, str, int | None, str | None, str | None]) -> None:
        for idx, existing in enumerate(merged):
            existing_key = (
                existing.op_type,
                existing.target_unit_kind,
                _norm_num_token(existing.target_section) if existing.target_section else "",
                existing.target_paragraph,
                existing.target_item,
                existing.target_special,
            )
            if existing_key != weaker_key or existing.op_type != "REPLACE" or not existing.target_chapter:
                continue
            merged[idx] = dc_replace(
                existing,
                extraction_provenance_tags=tuple(
                    dict.fromkeys((*existing.extraction_provenance_tags, "fallback_replace_supplement_shadowed"))
                ),
            )
            return

    scoped_replace_targets = {
        (
            op.op_type,
            op.target_unit_kind,
            _norm_num_token(op.target_section) if op.target_section else "",
            op.target_paragraph,
            op.target_item,
            op.target_special,
        )
        for op in ops
        if op.op_type == "REPLACE" and op.target_chapter
    }
    for op in fallback_ops:
        if op.op_type != "REPLACE":
            continue
        sig = _op_signature(op)
        if sig in seen:
            continue
        if op.target_chapter is None:
            weaker_key = (
                op.op_type,
                op.target_unit_kind,
                _norm_num_token(op.target_section) if op.target_section else "",
                op.target_paragraph,
                op.target_item,
                op.target_special,
            )
            if weaker_key in scoped_replace_targets:
                _mark_scoped_winner(weaker_key)
                continue
        op = dc_replace(
            op,
            fallback_provenance=True,
            extraction_provenance_tags=tuple(
                dict.fromkeys((*op.extraction_provenance_tags, "fallback_replace_supplement"))
            ),
        )
        merged.append(op)
        seen.add(sig)
    return merged


def _extract_replace_ops_from_muutetaan_tail(cleaned: str) -> List[AmendmentOp]:
    """Recover explicit REPLACE refs from a trailing `muutetaan ..., seuraavasti` clause.

    This is intentionally narrower than the broad fallback parser: it only looks
    inside the tail introduced by `muutetaan` and only recovers plain section
    and subsection targets. It exists for mixed clauses where PEG found the
    repeal side but dropped the subsequent replace side entirely.
    """
    m = re.search(r"\bmuutetaan\b(.*?)(?:\bseuraavasti\b|$)", cleaned, flags=re.I)
    if m is None:
        return []
    tail = re.sub(r"\(\s*\d+/\d+\s*\)", " ", m.group(1))
    if not re.search(r"\d+\s*[a-z]?\s*§", tail, flags=re.I):
        return []
    if re.search(r"\b(luku|osa|kohta|otsikko|johd|johdantokappale)\b", tail, flags=re.I):
        return []

    refs: List[Tuple[str, Optional[str]]] = []
    refs.extend(
        (_RE_WHITESPACE.sub("", sec), mom)
        for sec, mom in re.findall(
            r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+moment(?:ti|in)",
            tail,
            flags=re.I,
        )
    )
    refs.extend(
        (_RE_WHITESPACE.sub("", sec), None)
        for sec in re.findall(
            r"(\d+\s*[a-z]?)\s*§(?!\s*:)",
            tail,
            flags=re.I,
        )
    )
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
            op_type="REPLACE",
            target_section=sec,
            target_unit_kind="section",
            target_paragraph=int(mom) if mom is not None else None,
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
        and re.search(r"\bsekä\s+(?:kumotaan|muutetaan)\b", cleaned)
    ):
        verb_matches = list(re.finditer(r"\b(kumotaan|muutetaan)\b", cleaned))
        split_ops: List[AmendmentOp] = []
        for i, match in enumerate(verb_matches):
            start = match.start()
            end = verb_matches[i + 1].start() if i + 1 < len(verb_matches) else len(cleaned)
            chunk = cleaned[start:end].strip(" ,")
            chunk = re.sub(r"^(?:sekä|ja)\s+", "", chunk)
            if not chunk or chunk == cleaned:
                continue
            split_ops.extend(parse_ops_fallback_heuristic(chunk))
        if split_ops:
            return _dedupe_fallback_ops_ir(split_ops)
    insert_section_ops = _extract_insert_section_ops_fallback(cleaned)
    insert_subsection_ops = _extract_insert_subsection_ops_fallback(cleaned)
    insert_item_ops = _extract_insert_item_ops_fallback(cleaned)
    insert_container_ops = _extract_insert_container_ops_fallback(cleaned)
    fallback_insert_ops = _prune_shadowed_parent_subsection_insert_fallbacks(
        insert_section_ops + insert_subsection_ops + insert_item_ops + insert_container_ops
    )

    repeal_range_ops: List[AmendmentOp] = []
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
                    op_type="REPEAL",
                    target_section=sec_norm,
                    target_unit_kind="section",
                    target_paragraph=mom,
                )
            )
    has_non_repeal_verbs = bool(re.search(r"\b(muutetaan|lisätään|korvataan|otetaan|siirretään)\b", cleaned))
    pure_repeal_range_clause = bool(repeal_range_ops) and not has_non_repeal_verbs

    container_shape = bool(re.search(r"\b(luku|osa|otsikko|johd|johdantokappale|kohta)\b", cleaned))
    if container_shape and pure_repeal_range_clause:
        return repeal_range_ops
    # Remove cited statute numbers like "(64/2015)" so target extraction focuses on
    # the amended provision reference that follows the citation.
    cleaned = re.sub(r"\(\s*\d+/\d+\s*\)", " ", cleaned)
    op_type: Optional[OpType] = None
    _KW_TO_OP: tuple[tuple[str, OpType], ...] = (
        ("muutetaan", "REPLACE"),
        ("muuttaa", "REPLACE"),
        ("kumotaan", "REPEAL"),
        ("kumoaa", "REPEAL"),
        ("lisätään", "INSERT"),
        ("lisää", "INSERT"),
        ("siirretään", "REPLACE"),
        ("siirtää", "REPLACE"),
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
        for sec, mom in re.findall(
            r"(\d+\s*[a-z]?)\s*§\s*:n\s*(\d+)\s+moment(?:ti|in)",
            cleaned,
            flags=re.I,
        )
    )
    refs.extend(
        (sec, None)
        for sec in re.findall(
            r"(\d+\s*[a-z]?)\s*§(?!\s*:)",
            cleaned,
            flags=re.I,
        )
    )
    for sec_list in re.findall(
        r"((?:\d+\s*[–—―-]\s*\d+|\d+)(?:\s*(?:,|ja)\s*(?:\d+\s*[–—―-]\s*\d+|\d+))*)\s*§(?!\s*:)",
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
                target_section=_RE_WHITESPACE.sub("", sec),
                target_unit_kind="section",
                target_paragraph=int(mom) if mom else None,
            )
        )

    insert_matches = [
        (_RE_WHITESPACE.sub("", sec), int(mom))
        for sec, mom in re.findall(
            r"lisätään\s+(\d+\s*[a-z]?)\s*§\s*:ään\s+uusi\s+(\d+)\s+momentti",
            cleaned,
            flags=re.I,
        )
    ]
    for sec, mom in insert_matches:
        if not any(
            op.op_type == "INSERT" and op.target_section == sec and op.target_paragraph == mom and not op.target_item
            for op in ops
        ):
            ops.append(
                AmendmentOp(
                    op_id="",
                    op_type="INSERT",
                    target_section=sec,
                    target_unit_kind="section",
                    target_paragraph=mom,
                )
            )

    for sec, old_mom, new_mom in re.findall(
        r"lisätään\s+(\d+\s*[a-z]?)\s*§\s*:ään\s+uusi\s+\d+\s+momentti\s*,\s*jolloin\s+(?:muutettu|nykyinen)\s+(\d+)\s+momentti\s+siirtyy\s+(\d+)\s+momentiksi",
        cleaned,
        flags=re.I,
    ):
        sec_norm = _RE_WHITESPACE.sub("", sec)
        for i, op in enumerate(ops):
            if (
                op.op_type == "REPLACE"
                and op.target_section == sec_norm
                and op.target_paragraph == int(old_mom)
                and not op.target_item
            ):
                ops[i] = dc_replace(op, target_paragraph=int(new_mom))
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
                op.op_type == "INSERT"
                or (
                    _RE_WHITESPACE.sub("", str(op.target_section or "")).lower(),
                    op.target_paragraph,
                    str(op.target_item or "") or None,
                    str(op.target_special or "") or None,
                )
                not in fallback_insert_keys
            )
        ]

    return _dedupe_fallback_ops_ir(repeal_range_ops + fallback_insert_ops + ops)


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

    for chapter in re.findall(r"(\d+[a-z]?)\s+luvun\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(AmendmentOp(op_id="", op_type="REPEAL", target_section=chapter, target_unit_kind="chapter"))

    for part in re.findall(r"(\d+[a-z]?)\s+osan\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(AmendmentOp(op_id="", op_type="REPEAL", target_section=part, target_unit_kind="part"))

    for sec in re.findall(r"(\d+[a-z]?)\s*§(?::n)?\s+kumoamisesta", cleaned, flags=re.I):
        ops.append(
            AmendmentOp(
                op_id="",
                op_type="REPEAL",
                target_section=_RE_WHITESPACE.sub("", sec),
                target_unit_kind="section",
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
