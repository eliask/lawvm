"""Johtolause supplement and tagging functions.

Pure ``(ops, johto) -> ops`` transforms that enrich ``List[AmendmentOp]``
with typed carriers and supplementary ops derived from johtolause text.
No master state, no corpus access, no lxml.

These parsers handle item-shift-after-repeal clauses and named-row table
clauses — typed parse results that supplement the main PEG pipeline output.
They are regex-based and emit typed clause AST nodes directly; the PEG
grammar does not cover these phenomena.

Extracted from grafter.py (Phase A, lines 125–334 in the original file).
Clause-waist parsers consolidated here from clause_waist.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from typing import List, Tuple

from lawvm.core.tree_ops import normalized_label_key
from lawvm.core.clause_ast import ItemShiftClause, NamedRowClause
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.johtolause.clause_patterns import (
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)
from lawvm.finland.ops import AmendmentOp


# ---------------------------------------------------------------------------
# Item-shift clause types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemShiftAfterRepealClause:
    """Typed parse result for item-shift-after-repeal clause families.

    The typed ``ItemShiftClause`` is the owned semantic fact.  The optional
    extra repeal information is carried here so compatibility adapters can
    synthesize the legacy ``AmendmentOp`` while the parsing ownership lives
    in this module.
    """

    clause: ItemShiftClause
    extra_repeal_target_paragraph: int | None = None


# ---------------------------------------------------------------------------
# Clause-waist parsers (inlined from former clause_waist.py)
# ---------------------------------------------------------------------------


def _parse_item_shift_clauses(johto: str) -> List[ItemShiftClause]:
    """Parse item-shift-after-repeal clauses from johtolause text."""
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "").lower()
    if "jolloin" not in text or "muuttuvat kohdiksi" not in text:
        return []

    clauses: List[ItemShiftClause] = []
    for match in re.finditer(
        r"(\d+\s*[a-z]?)\s*§:n\s*(\d+)\s+momentin\s*([a-z])\s+kohdan\s*,\s*jolloin\s+kohdat\s+([a-z])\s*[–—―-]\s*([a-z])\s+muuttuvat\s+kohdiksi\s+([a-z])\s*[–—―-]\s*([a-z])",
        text,
        flags=re.I,
    ):
        sec, mom, repealed, src_lo, src_hi, dst_lo, dst_hi = match.groups()
        repealed = repealed.lower()
        src_lo = src_lo.lower()
        src_hi = src_hi.lower()
        dst_lo = dst_lo.lower()
        dst_hi = dst_hi.lower()

        if repealed != dst_lo:
            continue
        if ord(src_lo) - ord(dst_lo) != 1 or ord(src_hi) - ord(dst_hi) != 1:
            continue

        sec_norm = re.sub(r"\s+", "", sec)
        source_items = tuple(chr(c) for c in range(ord(src_lo), ord(src_hi) + 1))
        target_items = tuple(chr(c) for c in range(ord(dst_lo), ord(dst_hi) + 1))
        clauses.append(
            ItemShiftClause(
                source_items=source_items,
                target_items=target_items,
                target_paragraph=int(mom),
                target_section=sec_norm,
            )
        )
    return clauses


def _parse_item_shift_after_repeal_clauses(johto: str) -> List[ItemShiftAfterRepealClause]:
    """Parse item-shift clauses that also carry a trailing repeal target."""
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "").lower()
    if "jolloin" not in text or "muuttuvat kohdiksi" not in text:
        return []

    results: List[ItemShiftAfterRepealClause] = []
    for match in re.finditer(
        r"(\d+\s*[a-z]?)\s*§:n\s*(\d+)\s+momentin\s*([a-z])\s+kohdan\s*,\s*jolloin\s+kohdat\s+([a-z])\s*[–—―-]\s*([a-z])\s+muuttuvat\s+kohdiksi\s+([a-z])\s*[–—―-]\s*([a-z])\s+ja\s+(\d+)\s+momentin\s*,\s*muutetaan",
        text,
        flags=re.I,
    ):
        sec, repeal_mom, _repealed, src_lo, src_hi, dst_lo, dst_hi, extra_mom = match.groups()
        src_lo = src_lo.lower()
        src_hi = src_hi.lower()
        dst_lo = dst_lo.lower()
        dst_hi = dst_hi.lower()
        if ord(src_lo) - ord(dst_lo) != 1 or ord(src_hi) - ord(dst_hi) != 1:
            continue

        sec_norm = re.sub(r"\s+", "", sec)
        source_items = tuple(chr(c) for c in range(ord(src_lo), ord(src_hi) + 1))
        target_items = tuple(chr(c) for c in range(ord(dst_lo), ord(dst_hi) + 1))
        results.append(
            ItemShiftAfterRepealClause(
                clause=ItemShiftClause(
                    source_items=source_items,
                    target_items=target_items,
                    target_paragraph=int(repeal_mom),
                    target_section=sec_norm,
                ),
                extra_repeal_target_paragraph=int(extra_mom),
            )
        )
    return results


def _parse_named_row_clauses(johto: str) -> List[NamedRowClause]:
    """Parse named-row table clauses from johtolause text."""
    clauses: List[NamedRowClause] = []

    mixed = parse_named_table_row_mixed_clauses(johto)
    for clause in mixed:
        sec_norm = clause.section
        repeal_rows = clause.repeal_rows.targets
        replace_rows = clause.replace_rows.targets
        if repeal_rows:
            clauses.append(
                NamedRowClause(
                    action=StructuralAction.REPEAL,
                    named_targets=tuple(repeal_rows),
                    target_section=sec_norm,
                )
            )
        if replace_rows:
            clauses.append(
                NamedRowClause(
                    action=StructuralAction.REPLACE,
                    named_targets=tuple(replace_rows),
                    target_section=sec_norm,
                )
            )

    single = parse_named_table_row_single_clauses(johto)
    for clause in single:
        action_enum = StructuralAction(clause.action)
        clauses.append(
            NamedRowClause(
                action=action_enum,
                named_targets=tuple(clause.rows.targets),
                target_section=clause.section,
            )
        )

    return clauses


def _parse_item_shift_with_extra_repeal(johto: str) -> List[Tuple[ItemShiftClause, AmendmentOp]]:
    """Parse item-shift clauses that also carry a trailing ``ja N momentin`` repeal.

    Returns pairs of (ItemShiftClause, synthesized REPEAL AmendmentOp).
    """
    results: List[Tuple[ItemShiftClause, AmendmentOp]] = []
    for idx, match in enumerate(_parse_item_shift_after_repeal_clauses(johto)):
        clause = match.clause
        extra_op = AmendmentOp(
            op_id=f"explicit_repeal_after_item_shift_{idx}",
            op_type="REPEAL",
            target_section=clause.target_section or "",
            target_unit_kind="section",
            target_paragraph=match.extra_repeal_target_paragraph,
            post_repeal_item_shift_label=clause.target_items[0].lower() if clause.target_items else None,
        )
        results.append((clause, extra_op))
    return results


def _tag_explicit_item_shift_after_repeal_hints(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Attach narrow post-repeal item-renumber hints from explicit jolloin clauses.

    Delegates to ``_parse_item_shift_clauses`` for parsing; only performs the
    typed post-repeal item-shift tagging side-effect.
    """
    clauses = _parse_item_shift_clauses(johto)
    if not clauses:
        return ops

    tagged_ops = list(ops)
    for clause in clauses:
        if not clause.source_items or not clause.target_items:
            continue
        repealed = clause.target_items[0]
        for op in tagged_ops:
            if (
                op.op_type == "REPEAL"
                and op.target_section == clause.target_section
                and op.target_paragraph == clause.target_paragraph
                and normalized_label_key(op.target_item or "") == repealed
            ):
                op.post_repeal_item_shift_label = repealed
    return tagged_ops


def _supplement_missing_repeals_after_item_shift_clause(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Recover coordinated repeal targets that PEG drops after a jolloin side-effect clause.

    Delegates to ``_parse_item_shift_with_extra_repeal`` for parsing; only
    appends the synthesized REPEAL op when not already present.
    """
    results = _parse_item_shift_with_extra_repeal(johto)
    if not results:
        return ops

    supplemented = list(ops)
    for _clause, extra_op in results:
        already_present = any(
            op.op_type == "REPEAL"
            and op.target_section == extra_op.target_section
            and op.target_paragraph == extra_op.target_paragraph
            and not op.target_item
            for op in supplemented
        )
        if already_present:
            continue
        supplemented.append(extra_op)
    return supplemented


def _supplement_named_table_row_mixed_clause_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Recover mixed repeal+replace row-table clauses that PEG flattens.

    Delegates to ``_parse_named_row_clauses`` for parsing; only performs the
    typed row-target tagging and supplement side-effects.
    """
    clauses = _parse_named_row_clauses(johto)
    if not clauses:
        return ops

    supplemented = list(ops)
    for idx, clause in enumerate(clauses):
        if clause.action is StructuralAction.REPLACE:
            continue
        sec_norm = clause.target_section
        repeal_rows = clause.named_targets

        tagged_repeal = False
        for pos, op in enumerate(supplemented):
            if (
                op.op_type == "REPEAL"
                and op.target_section == sec_norm
                and op.target_unit_kind == "section"
                and op.target_paragraph is None
                and op.target_item is None
                and not op.target_special
            ):
                supplemented[pos] = dc_replace(
                    op,
                    named_row_targets=tuple(repeal_rows),
                )
                tagged_repeal = True
                break
        if not tagged_repeal:
            continue

        has_replace = any(
            op.op_type == "REPLACE"
            and op.target_section == sec_norm
            and op.target_unit_kind == "section"
            and op.target_paragraph is None
            and op.target_item is None
            and not op.target_special
            for op in supplemented
        )
        if has_replace:
            continue

        replace_clause = next(
            (c for c in clauses if c.action is StructuralAction.REPLACE and c.target_section == sec_norm),
            None,
        )
        if replace_clause is None:
            continue
        replace_rows = replace_clause.named_targets

        supplemented.append(
            AmendmentOp(
                op_id=f"named_table_row_replace_{idx}",
                op_type="REPLACE",
                target_section=sec_norm or "",
                target_unit_kind="section",
                named_row_targets=tuple(replace_rows),
            )
        )
    return supplemented


def _tag_named_table_row_single_clause_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Tag broad single-row table clauses so normalization can resolve them.

    Delegates to ``_parse_named_row_clauses`` for parsing; only performs the
    typed row-target tagging side-effect.
    """
    clauses = _parse_named_row_clauses(johto)
    if not clauses:
        return ops

    supplemented = list(ops)
    for clause in clauses:
        for pos, op in enumerate(supplemented):
            if (
                op.op_type.lower() == clause.action.value
                and op.target_section == clause.target_section
                and op.target_unit_kind == "section"
                and op.target_paragraph is None
                and op.target_item is None
                and not op.target_special
            ):
                if tuple(op.named_row_targets) == tuple(clause.named_targets):
                    break
                merged = list(clause.named_targets)
                supplemented[pos] = dc_replace(
                    op,
                    named_row_targets=tuple(merged),
                )
                break
    return supplemented
