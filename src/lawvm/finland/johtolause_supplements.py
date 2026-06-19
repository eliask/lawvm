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

from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import normalized_label_key
from lawvm.core.clause_ast import ItemShiftClause, NamedRowClause
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.johtolause.clause_patterns import (
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johto_scope_mentions import collect_johto_numbered_table_targets
from lawvm.finland.ops import AmendmentOp, _lo_with_path_update

_SPARSE_OSALTA_ROW_OMISSION_RULE_ID = "fi.sparse_osalta_row_omission_repeal.v1"
_SPARSE_OSALTA_ROW_OMISSION_TAG = "sparse_osalta_row_omission_repeal"
_NUMBERED_TABLE_TARGET_RULE_ID = "fi.numbered_table_target.v1"
_NUMBERED_TABLE_TARGET_TAG = "numbered_table_target"
_ITEM_AND_MOMENT_TARGET_RULE_ID = "fi.item_and_moment_target_supplement.v1"
_ITEM_AND_MOMENT_TARGET_TAG = "item_and_moment_target_supplement"
_REPLACE_ITEMS_AND_MOMENT_RE = re.compile(
    r"(?P<section>\d{1,4}\s*[a-zäöå]?)\s*§\s*:\s*n\s+"
    r"(?P<moment>\d{1,3})\s+momentin\s+kohdat\s+"
    r"(?P<items>\d{1,3}(?:\s*(?:,|ja)\s*\d{1,3}){0,12})\s+"
    r"sekä\s+(?P<extra_moment>\d{1,3})\s+momentti\b",
    flags=re.I,
)
_INSERT_ITEM_RE = re.compile(
    r"\blisätään\b[\s\S]{0,800}?"
    r"(?P<section>\d{1,4}\s*[a-zäöå]?)\s*§\s*:\s*n\s+"
    r"(?P<moment>\d{1,3})\s+momenttiin\s+uusi\s+kohta\s+"
    r"(?P<item>\d{1,3})\b",
    flags=re.I,
)
_SPARSE_OSALTA_ROW_OMISSION_RE = re.compile(
    r"\bmuut[a-zäöå]{0,12}\b.{0,500}?"
    r"(?P<section>\d{1,4}\s*[a-zäöå]?)\s*§(?::[a-zäöå]{1,6})?.{0,300}?"
    r"\boikeusaputoimiston\s+"
    r"(?P<row>[a-zåäö][a-zåäö-]{1,80})\s+sivutoimiston\s+osalta\s+seuraavasti\b",
    flags=re.I,
)


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


@dataclass(frozen=True)
class SparseOsaltaRowOmissionClause:
    """Typed recovery for sparse ``osalta`` paragraph-list row omissions.

    Historical administrative decisions sometimes say the section is amended
    "as regards" a named branch office, while the published source payload
    omits that branch row from an omission-bracketed excerpt.  The source verb
    is ``muutetaan`` but the executable row effect is a deletion; that action
    family recovery must be witnessed explicitly.
    """

    section: str
    row_target: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class ItemAndMomentReplaceClause:
    """Typed supplement for ``N momentin kohdat ... sekä M momentti`` clauses."""

    section: str
    moment: int
    item_labels: tuple[str, ...]
    extra_moment: int
    raw_text: str


@dataclass(frozen=True, slots=True)
class ItemInsertClause:
    """Typed supplement for ``N momenttiin uusi kohta M`` clauses."""

    section: str
    moment: int
    item_label: str
    raw_text: str


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
    for idx, clause in enumerate(clauses):
        tagged = False
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
                    tagged = True
                    break
                merged = list(clause.named_targets)
                supplemented[pos] = dc_replace(
                    op,
                    named_row_targets=tuple(merged),
                )
                tagged = True
                break
        if tagged or clause.action is not StructuralAction.REPLACE:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"named_table_row_single_replace_{idx}",
                op_type="REPLACE",
                target_section=clause.target_section or "",
                target_unit_kind="section",
                named_row_targets=tuple(clause.named_targets),
            )
        )
    return supplemented


def _numbered_table_targets_by_section(johto: str) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for target in collect_johto_numbered_table_targets(johto):
        labels = out.setdefault(target.section_label, [])
        if target.table_label not in labels:
            labels.append(target.table_label)
    return {section: tuple(labels) for section, labels in out.items()}


def _with_numbered_table_targets(op: AmendmentOp, labels: tuple[str, ...]) -> AmendmentOp:
    merged = tuple(dict.fromkeys((*op.numbered_table_targets, *labels)))
    tags = tuple(dict.fromkeys((*op.extraction_provenance_tags, _NUMBERED_TABLE_TARGET_TAG)))
    return dc_replace(
        op,
        numbered_table_targets=merged,
        extraction_provenance_tags=tags,
        witness_rule_id=op.witness_rule_id or _NUMBERED_TABLE_TARGET_RULE_ID,
    )


def _tag_numbered_table_target_clause_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Attach explicit ``N §:n taulukko M`` targets to section replace ops."""
    targets_by_section = _numbered_table_targets_by_section(johto)
    if not targets_by_section:
        return ops

    supplemented = list(ops)
    for idx, (section, table_labels) in enumerate(targets_by_section.items()):
        tagged = False
        for pos, op in enumerate(supplemented):
            if (
                op.op_type == "REPLACE"
                and op.target_section == section
                and op.target_unit_kind == "section"
                and op.target_paragraph is None
                and op.target_item is None
                and not op.target_special
            ):
                supplemented[pos] = _with_numbered_table_targets(op, table_labels)
                tagged = True
                break
        if tagged:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"numbered_table_target_replace_{idx}",
                op_type="REPLACE",
                target_section=section,
                target_unit_kind="section",
                numbered_table_targets=table_labels,
                extraction_provenance_tags=(_NUMBERED_TABLE_TARGET_TAG,),
                witness_rule_id=_NUMBERED_TABLE_TARGET_RULE_ID,
            )
        )
    return supplemented


def _parse_item_labels(raw_items: str) -> tuple[str, ...]:
    labels: list[str] = []
    for raw_label in re.findall(r"\d{1,3}", raw_items or ""):
        label = _norm_num_token(raw_label)
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _parse_item_and_moment_replace_clauses(johto: str) -> tuple[ItemAndMomentReplaceClause, ...]:
    clauses: list[ItemAndMomentReplaceClause] = []
    for match in _REPLACE_ITEMS_AND_MOMENT_RE.finditer(johto or ""):
        section = _norm_num_token(match.group("section"))
        item_labels = _parse_item_labels(match.group("items"))
        if not section or not item_labels:
            continue
        clauses.append(
            ItemAndMomentReplaceClause(
                section=section,
                moment=int(match.group("moment")),
                item_labels=item_labels,
                extra_moment=int(match.group("extra_moment")),
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _parse_item_insert_clauses(johto: str) -> tuple[ItemInsertClause, ...]:
    clauses: list[ItemInsertClause] = []
    for match in _INSERT_ITEM_RE.finditer(johto or ""):
        section = _norm_num_token(match.group("section"))
        item_label = _norm_num_token(match.group("item"))
        if not section or not item_label:
            continue
        clauses.append(
            ItemInsertClause(
                section=section,
                moment=int(match.group("moment")),
                item_label=item_label,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _has_op(
    ops: list[AmendmentOp],
    *,
    op_type: str,
    section: str,
    moment: int,
    item: str | None,
) -> bool:
    return any(
        op.op_type == op_type
        and op.target_unit_kind == "section"
        and op.target_section == section
        and op.target_paragraph == moment
        and (op.target_item or None) == item
        and not op.target_special
        for op in ops
    )


def _supplement_item_and_moment_clause_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Recover explicit item and sparse moment targets flattened by PEG."""
    replace_clauses = _parse_item_and_moment_replace_clauses(johto)
    insert_clauses = _parse_item_insert_clauses(johto)
    if not replace_clauses and not insert_clauses:
        return ops

    supplemented = list(ops)
    for clause_index, clause in enumerate(replace_clauses):
        for item_label in clause.item_labels:
            if _has_op(
                supplemented,
                op_type="REPLACE",
                section=clause.section,
                moment=clause.moment,
                item=item_label,
            ):
                continue
            supplemented.append(
                AmendmentOp(
                    op_id=f"item_and_moment_replace_item_{clause_index}_{item_label}",
                    op_type="REPLACE",
                    target_section=clause.section,
                    target_unit_kind="section",
                    target_paragraph=clause.moment,
                    target_item=item_label,
                    extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                    witness_rule_id=_ITEM_AND_MOMENT_TARGET_RULE_ID,
                )
            )
        if not _has_op(
            supplemented,
            op_type="REPLACE",
            section=clause.section,
            moment=clause.extra_moment,
            item=None,
        ):
            supplemented.append(
                AmendmentOp(
                    op_id=f"item_and_moment_replace_moment_{clause_index}_{clause.extra_moment}",
                    op_type="REPLACE",
                    target_section=clause.section,
                    target_unit_kind="section",
                    target_paragraph=clause.extra_moment,
                    extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                    witness_rule_id=_ITEM_AND_MOMENT_TARGET_RULE_ID,
                )
            )

    for clause_index, clause in enumerate(insert_clauses):
        if _has_op(
            supplemented,
            op_type="INSERT",
            section=clause.section,
            moment=clause.moment,
            item=clause.item_label,
        ):
            continue
        converted = False
        for pos, op in enumerate(supplemented):
            if (
                op.op_type == "INSERT"
                and op.target_unit_kind == "section"
                and (not op.target_section or op.target_section == clause.section)
                and op.target_paragraph == clause.moment
                and not op.target_item
                and not op.target_special
            ):
                supplemented[pos] = dc_replace(
                    op,
                    target_section=clause.section,
                    target_item=clause.item_label,
                    lo=(
                        _lo_with_path_update(
                            op.lo,
                            section=clause.section,
                            subsection=str(clause.moment),
                            item=clause.item_label,
                        )
                        if op.lo is not None
                        else op.lo
                    ),
                    extraction_provenance_tags=tuple(
                        dict.fromkeys((*op.extraction_provenance_tags, _ITEM_AND_MOMENT_TARGET_TAG))
                    ),
                    witness_rule_id=op.witness_rule_id or _ITEM_AND_MOMENT_TARGET_RULE_ID,
                )
                converted = True
                break
        if converted:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"item_and_moment_insert_item_{clause_index}_{clause.item_label}",
                op_type="INSERT",
                target_section=clause.section,
                target_unit_kind="section",
                target_paragraph=clause.moment,
                target_item=clause.item_label,
                extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                witness_rule_id=_ITEM_AND_MOMENT_TARGET_RULE_ID,
            )
        )
    return supplemented


def _parse_sparse_osalta_row_omission_clauses(johto: str) -> tuple[SparseOsaltaRowOmissionClause, ...]:
    """Parse office-branch ``osalta`` clauses that publish omission excerpts."""
    text = re.sub(r"\s+", " ", johto or "").strip()
    lowered = text.lower()
    if (
        "osalta" not in lowered
        or "sivutoimiston" not in lowered
        or "oikeusaputoimiston" not in lowered
        or "seuraavasti" not in lowered
    ):
        return ()

    clauses: list[SparseOsaltaRowOmissionClause] = []
    for match in _SPARSE_OSALTA_ROW_OMISSION_RE.finditer(text):
        section = re.sub(r"\s+", "", match.group("section")).lower()
        row_target = match.group("row").strip()
        if not section or not row_target:
            continue
        clauses.append(
            SparseOsaltaRowOmissionClause(
                section=section,
                row_target=row_target,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _sparse_osalta_recovery_finding(
    clause: SparseOsaltaRowOmissionClause,
    *,
    amendment_id: str,
) -> Finding:
    return Finding(
        kind="ELAB.SPARSE_OSALTA_ROW_OMISSION_REPEAL",
        role="observation",
        stage="frontend_extraction",
        source_statute=amendment_id,
        blocking=False,
        detail={
            "kind": "ELAB.SPARSE_OSALTA_ROW_OMISSION_REPEAL",
            "rule_id": _SPARSE_OSALTA_ROW_OMISSION_RULE_ID,
            "source_statute": amendment_id,
            "source_verb": "muutetaan",
            "lowered_action": "REPEAL",
            "target_unit_kind": "paragraph_row",
            "target_section": clause.section,
            "named_row_targets": (clause.row_target,),
            "raw_text": clause.raw_text,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )


def _supplement_sparse_osalta_row_omission_repeals(
    ops: List[AmendmentOp],
    johto: str,
    *,
    amendment_id: str = "",
) -> tuple[List[AmendmentOp], tuple[Finding, ...]]:
    """Add witnessed row-level repeals for sparse office-branch omissions."""
    clauses = _parse_sparse_osalta_row_omission_clauses(johto)
    if not clauses:
        return ops, ()

    supplemented = list(ops)
    findings: list[Finding] = []
    for idx, clause in enumerate(clauses):
        duplicate = any(
            op.op_type == "REPEAL"
            and op.target_unit_kind == "section"
            and op.target_section == clause.section
            and tuple(op.named_row_targets) == (clause.row_target,)
            for op in supplemented
        )
        if duplicate:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"sparse_osalta_row_omission_repeal_{idx}",
                op_type="REPEAL",
                target_section=clause.section,
                target_unit_kind="section",
                named_row_targets=(clause.row_target,),
                extraction_provenance_tags=(_SPARSE_OSALTA_ROW_OMISSION_TAG,),
                witness_rule_id=_SPARSE_OSALTA_ROW_OMISSION_RULE_ID,
            )
        )
        findings.append(_sparse_osalta_recovery_finding(clause, amendment_id=amendment_id))
    return supplemented, tuple(findings)
