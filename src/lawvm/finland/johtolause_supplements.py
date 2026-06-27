"""Johtolause supplement and tagging functions.

Pure ``(ops, johto) -> ops`` transforms that enrich ``List[AmendmentOp]``
with typed carriers and supplementary ops derived from johtolause text.
No master state, no corpus access, no lxml.

These transforms run AFTER the grammar pipeline as a recovery layer: they
re-scan the johtolause for explicit amendment targets the PEG flattens or drops
in long mixed ``muutetaan``/``lisätään`` lists (item/momentti/table replace and
insert ops, bare whole-section targets, sparse office-row omission repeals). The
grammar's insertion family declines these enumeration-continuation shapes by
design (see ``johtolause/grammar/insertions.py`` "Out of scope" list), so this is
the registered typed-residue lane for them: every supplemented op carries a
``witness_rule_id`` and ``extraction_provenance_tags`` so the recovery is
witnessed, never silently invented. The structural regexes here are bounded
anchors over that residue, not a competing primary parser.

Item-shift (``jolloin … muuttuvat kohdiksi``) and named-row table parsing are NOT
re-implemented here: they delegate to the canonical grammar-package recognizers
``johtolause.clause_surface.parse_item_shift_clauses`` /
``parse_item_shift_after_repeal_clauses`` and
``johtolause.clause_patterns.parse_named_table_row_*`` (one canonical parser per
family — no rival regex copy).

Extracted from grafter.py (Phase A, lines 125–334 in the original file).
Clause-waist parsers consolidated here from clause_waist.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from collections.abc import Sequence
from typing import List, Tuple

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import normalized_label_key
from lawvm.core.clause_ast import ItemShiftClause, NamedRowClause
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.johtolause.clause_patterns import (
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)
from lawvm.finland.johtolause.clause_surface import (
    parse_item_shift_after_repeal_clauses,
    parse_item_shift_clauses,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johto_scope_mentions import (
    collect_johto_numbered_table_targets,
    expand_johto_section_label_range,
)
from lawvm.finland.ops import AmendmentOp, OpType, _lo_with_path_update
from lawvm.finland.target_selector_facades import fi_section_target, replace_target
from lawvm.core.quirks_disposition import QuirksDisposition

_SPARSE_OSALTA_ROW_OMISSION_RULE_ID = "fi.sparse_osalta_row_omission_repeal.v1"
_SPARSE_OSALTA_ROW_OMISSION_TAG = "sparse_osalta_row_omission_repeal"
_NUMBERED_TABLE_TARGET_RULE_ID = "fi.numbered_table_target.v1"
_NUMBERED_TABLE_TARGET_TAG = "numbered_table_target"
_ITEM_AND_MOMENT_TARGET_RULE_ID = "fi.item_and_moment_target_supplement.v1"
_ITEM_AND_MOMENT_TARGET_TAG = "item_and_moment_target_supplement"
_MIXED_EXPLICIT_TARGET_RULE_ID = "fi.mixed_explicit_target_supplement.v1"
_MIXED_EXPLICIT_TARGET_TAG = "mixed_explicit_target_supplement"
_JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT_TAG = "jolloin_moment_renumber_supplement"
_EXPLICIT_CHAPTER_SCOPE_TAG = "chapter_scope_from_explicit_chunk"
_SECTION_LABEL_PATTERN = r"\d{1,4}(?:[a-zäöå]|\s[a-zäöå])?"
_CHAPTER_LABEL_PATTERN = _SECTION_LABEL_PATTERN
_OPTIONAL_CHAPTER_SECTION_PREFIX = rf"(?:(?P<chapter>{_CHAPTER_LABEL_PATTERN})\s{{1,10}}luvun\s{{1,20}})?"
_ITEM_LABEL_PATTERN = r"\d{1,3}(?:[a-zäöå]|\s[a-zäöå])?"
_REPLACE_ITEMS_AND_MOMENT_RE = re.compile(
    _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}n\s+"
    r"(?P<moment>\d{1,3})\s+momentin\s+kohdat\s+"
    r"(?P<items>\d{1,3}(?:\s{0,20}(?:,|ja)\s{0,20}\d{1,3}){0,12})\s+"
    r"sekä\s+(?P<extra_moment>\d{1,3})\s+momentti\b",
    flags=re.I,
)
_INSERT_ITEM_RE = re.compile(
    r"\blisätään\b[\s\S]{0,800}?"
    + _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}n\s+"
    r"(?P<moment>\d{1,3})\s+momenttiin"
    r"(?:\s*,\s*(?:(?!\buusi\b).){0,500}?)?\s+uusi\s+"
    rf"(?:(?:näin\s+kuuluva\s+)?(?P<item_before>{_ITEM_LABEL_PATTERN})\s+kohta|kohta\s+(?P<item_after>{_ITEM_LABEL_PATTERN}))\b",
    flags=re.I,
)
_ITEM_REPLACE_RE = re.compile(
    _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}n\s+"
    r"(?P<moment>\d{1,3})\s+momentin\s+kohta\s+"
    rf"(?P<item>{_ITEM_LABEL_PATTERN})\b",
    flags=re.I,
)
_TABLE_AND_MOMENT_RE = re.compile(
    _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}n\s+"
    rf"taulukko\s+{_SECTION_LABEL_PATTERN}\s+ja\s+"
    r"(?P<moment>\d{1,3})\s+momentti\b",
    flags=re.I,
)
_MOMENT_REPLACE_RE = re.compile(
    _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}n\s+"
    r"(?P<moment>\d{1,3})\s+momentti\b",
    flags=re.I,
)
_INSERT_MOMENT_RE = re.compile(
    _OPTIONAL_CHAPTER_SECTION_PREFIX
    + rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§\s{{0,10}}:\s{{0,10}}ään\s+uusi\s+"
    r"(?P<moment>\d{1,3})\s+momentti\b",
    flags=re.I,
)
_BARE_REPLACE_SECTION_RE = re.compile(
    rf"(?<![/\d]){_OPTIONAL_CHAPTER_SECTION_PREFIX}"
    rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§"
    r"(?!\s{0,10}:)(?!\s{0,10}n\b)(?!\s{0,20}\d{1,3}\s+moment)",
    flags=re.I,
)
_BARE_SECTION_LIST_RE = re.compile(
    r"(?:(?<=^)|(?<=[,;]))\s{0,20}"
    r"(?P<run>(?:\d{1,4}\s{0,3}[a-zäöå]?\s*[–—―-]\s*\d{1,4}\s{0,3}[a-zäöå]?|\d{1,4}\s{0,3}[a-zäöå]?)(?:\s{0,20}(?:,|ja|sekä)\s{0,20}(?:\d{1,4}\s{0,3}[a-zäöå]?\s*[–—―-]\s*\d{1,4}\s{0,3}[a-zäöå]?|\d{1,4}\s{0,3}[a-zäöå]?)){1,40})"
    r"\s{0,10}§(?!\s{0,10}:)(?!\s{0,10}n\b)(?!\s{0,20}\d{1,3}\s+moment)",
    flags=re.I,
)
_GLUED_ALPHA_JA_RE = re.compile(
    r"\b(?P<num>\d{1,4})\s{0,3}(?P<suffix>[a-zäöå])ja\s+(?P<next>\d{1,4})\b",
    flags=re.I,
)
_SECTION_LIST_SPLIT_RE = re.compile(r"\s*(?:,|ja|sekä)\s*", flags=re.I)
_SECTION_RANGE_RE = re.compile(
    r"(?P<start>\d{1,4}\s{0,3}[a-zäöå]?)\s*[–—―-]\s*(?P<end>\d{1,4}\s{0,3}[a-zäöå]?)",
    flags=re.I,
)
_CHAPTER_HEADING_PAIR_PREFIX_RE = re.compile(
    r"\d{1,4}\s*(?:luku|luvun)\s+nimike\s+ja\s*$",
    flags=re.I,
)
_SPARSE_OSALTA_ROW_OMISSION_RE = re.compile(
    r"\bmuut[a-zäöå]{0,12}\b.{0,500}?"
    rf"(?P<section>{_SECTION_LABEL_PATTERN})\s{{0,10}}§(?::[a-zäöå]{{1,6}})?.{{0,300}}?"
    r"\boikeusaputoimiston\s+"
    r"(?P<row>[a-zåäö][a-zåäö-]{1,80})\s+sivutoimiston\s+osalta\s+seuraavasti\b",
    flags=re.I,
)


# ---------------------------------------------------------------------------
# Item-shift clause types
# ---------------------------------------------------------------------------


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
    chapter: str | None
    moment: int
    item_labels: tuple[str, ...]
    extra_moment: int
    raw_text: str


@dataclass(frozen=True, slots=True)
class ItemInsertClause:
    """Typed supplement for ``N momenttiin uusi kohta M`` clauses."""

    section: str
    chapter: str | None
    moment: int
    item_label: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class ItemReplaceClause:
    """Typed supplement for ``N §:n K momentin kohta M`` clauses."""

    section: str
    chapter: str | None
    moment: int
    item_label: str
    raw_text: str


@dataclass(frozen=True, slots=True)
class BareSectionReplaceClause:
    """Typed supplement for bare whole-section targets inside ``muutetaan``."""

    section: str
    chapter: str | None
    raw_text: str


@dataclass(frozen=True, slots=True)
class MomentTargetClause:
    """Typed supplement for explicit moment targets skipped in mixed lists."""

    section: str
    chapter: str | None
    moment: int
    op_type: str
    raw_text: str
    table_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JolloinMomentRenumberClause:
    """Typed supplement for dropped ``jolloin`` subsection renumber pairs."""

    section: str
    source_moment: int
    destination_moment: int
    inserted_moments: tuple[int, ...]


# ---------------------------------------------------------------------------
# Clause-waist parsers (inlined from former clause_waist.py)
# ---------------------------------------------------------------------------


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
    for idx, match in enumerate(parse_item_shift_after_repeal_clauses(johto)):
        clause = match.clause
        extra_op = AmendmentOp(
            op_id=f"explicit_repeal_after_item_shift_{idx}",
            op_type=OpType.REPEAL,
            **fi_section_target(
                clause.target_section or "",
                subsection=match.extra_repeal_target_paragraph,
            ),
            post_repeal_item_shift_label=clause.target_items[0].lower() if clause.target_items else None,
        )
        results.append((clause, extra_op))
    return results


def _tag_explicit_item_shift_after_repeal_hints(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Attach narrow post-repeal item-renumber hints from explicit jolloin clauses.

    Delegates to the canonical ``clause_surface.parse_item_shift_clauses`` for
    parsing; only performs the typed post-repeal item-shift tagging side-effect.
    """
    clauses = parse_item_shift_clauses(johto)
    if not clauses:
        return ops

    tagged_ops = list(ops)
    for clause in clauses:
        if not clause.source_items or not clause.target_items:
            continue
        repealed = clause.target_items[0]
        for op in tagged_ops:
            if (
                op.op_type == OpType.REPEAL
                and op.target_cols.target_section == clause.target_section
                and op.target_cols.target_paragraph == clause.target_paragraph
                and normalized_label_key(op.target_cols.target_item or "") == repealed
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
            op.op_type == OpType.REPEAL
            and op.target_cols.target_section == extra_op.target_cols.target_section
            and op.target_cols.target_paragraph == extra_op.target_cols.target_paragraph
            and not op.target_cols.target_item
            for op in supplemented
        )
        if already_present:
            continue
        supplemented.append(extra_op)
    return supplemented


def _jolloin_moment_renumber_anchor(
    tokens: Sequence[object],
    jolloin_pos: int,
) -> tuple[str, tuple[int, ...]] | None:
    """Return the section and inserted moments owning a dropped ``jolloin`` renumber.

    The evidence comes from the token scanner, not a prose regex: only a
    preceding ``N §:ään ... uusi ... momentti`` insertion span immediately before
    the ``JOLLOIN_MOVE`` sentinel can anchor the consequence renumber.
    """

    pykala_pos = None
    for idx in range(jolloin_pos - 1, -1, -1):
        token = tokens[idx]
        cat = getattr(token, "cat", "")
        if cat == "PYKALA":
            pykala_pos = idx
            break
        if cat == "VERB":
            break
    if pykala_pos is None or pykala_pos == 0:
        return None
    section_token = tokens[pykala_pos - 1]
    if getattr(section_token, "cat", "") != "NUM":
        return None
    between = tokens[pykala_pos + 1 : jolloin_pos]
    if not any(getattr(token, "cat", "") == "UUSI" for token in between):
        return None
    if not any(getattr(token, "cat", "") == "MOMENTTI" for token in between):
        return None
    inserted: list[int] = []
    seen_uusi = False
    for token in between:
        cat = getattr(token, "cat", "")
        if cat == "UUSI":
            seen_uusi = True
            continue
        if not seen_uusi:
            continue
        if cat == "MOMENTTI":
            break
        if cat == "NUM":
            text = str(getattr(token, "text", "") or "")
            if text.isdigit():
                inserted.append(int(text))
    return _norm_num_token(str(getattr(section_token, "text", "") or "")), tuple(inserted)


def _parse_jolloin_moment_renumber_clauses(
    johto: str,
) -> tuple[JolloinMomentRenumberClause, ...]:
    tokens, jolloin_pairs = apply_annotations_with_jolloin_pairs(tokenize(johto))
    if not jolloin_pairs:
        return ()
    clauses: list[JolloinMomentRenumberClause] = []
    for jolloin_pos, pairs in sorted(jolloin_pairs.items()):
        anchor = _jolloin_moment_renumber_anchor(tokens, jolloin_pos)
        if anchor is None:
            continue
        section, inserted_moments = anchor
        for source, destination, kind in pairs:
            if kind != "M":
                continue
            if not source.isdigit() or not destination.isdigit():
                continue
            clauses.append(
                JolloinMomentRenumberClause(
                    section=section,
                    source_moment=int(source),
                    destination_moment=int(destination),
                    inserted_moments=inserted_moments,
                )
            )
    return tuple(clauses)


def _supplement_jolloin_moment_renumber_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Recover scanner-owned ``jolloin`` subsection renumbers dropped by fallback parsing."""

    clauses = _parse_jolloin_moment_renumber_clauses(johto)
    if not clauses:
        return ops
    supplemented = list(ops)
    for idx, clause in enumerate(clauses):
        already_present = False
        for op in supplemented:
            if op.op_type != OpType.RENUMBER:
                continue
            if _norm_num_token(op.target_cols.target_section or "") != clause.section:
                continue
            if op.target_cols.target_paragraph != clause.source_moment:
                continue
            destination = op.lo.destination if op.lo is not None else None
            if destination is None:
                continue
            dest_path = {kind: label for kind, label in destination.path}
            if dest_path.get("subsection") == str(clause.destination_moment):
                already_present = True
                break
        if already_present:
            continue
        target = LegalAddress(
            path=(
                ("section", clause.section),
                ("subsection", str(clause.source_moment)),
            )
        )
        destination = LegalAddress(
            path=(
                ("section", clause.section),
                ("subsection", str(clause.destination_moment)),
            )
        )
        renumber_ops = AmendmentOp.from_lo(
            LegalOperation(
                op_id=(
                    "jolloin_moment_renumber_"
                    f"{clause.section}_{clause.source_moment}_to_{clause.destination_moment}_{idx}"
                ),
                sequence=0,
                action=StructuralAction.RENUMBER,
                target=target,
                destination=destination,
                provenance_tags=(_JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT_TAG,),
                witness_rule_id="fi.jolloin_renumber",
            ),
            len(supplemented),
        )
        for op in renumber_ops:
            op.extraction_provenance_tags = (
                *(
                    tag
                    for tag in op.extraction_provenance_tags
                    if tag != _JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT_TAG
                ),
                _JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT_TAG,
            )
            op.restamp_provenance()
        supplemented.extend(renumber_ops)
        for moment in clause.inserted_moments:
            insert_already_present = any(
                op.op_type == OpType.INSERT
                and _norm_num_token(op.target_cols.target_section or "") == clause.section
                and op.target_cols.target_paragraph == moment
                and not op.target_cols.target_item
                and not op.target_cols.target_special
                for op in supplemented
            )
            if insert_already_present:
                continue
            supplemented.append(
                AmendmentOp(
                    op_id=f"jolloin_moment_insert_{clause.section}_{moment}_{idx}",
                    op_type=OpType.INSERT,
                    **fi_section_target(
                        clause.section,
                        subsection=moment,
                    ),
                    extraction_provenance_tags=(_JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT_TAG,),
                    witness_rule_id="fi.jolloin_renumber",
                )
            )
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
                op.op_type == OpType.REPEAL
                and op.target_cols.target_section == sec_norm
                and op.target_cols.target_unit_kind == "section"
                and op.target_cols.target_paragraph is None
                and op.target_cols.target_item is None
                and not op.target_cols.target_special
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
            op.op_type == OpType.REPLACE
            and op.target_cols.target_section == sec_norm
            and op.target_cols.target_unit_kind == "section"
            and op.target_cols.target_paragraph is None
            and op.target_cols.target_item is None
            and not op.target_cols.target_special
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
                op_type=OpType.REPLACE,
                **fi_section_target(sec_norm or ""),
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
                and op.target_cols.target_section == clause.target_section
                and op.target_cols.target_unit_kind == "section"
                and op.target_cols.target_paragraph is None
                and op.target_cols.target_item is None
                and not op.target_cols.target_special
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
                op_type=OpType.REPLACE,
                **fi_section_target(clause.target_section or ""),
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
                op.op_type == OpType.REPLACE
                and op.target_cols.target_section == section
                and op.target_cols.target_unit_kind == "section"
                and op.target_cols.target_paragraph is None
                and op.target_cols.target_item is None
                and not op.target_cols.target_special
            ):
                supplemented[pos] = _with_numbered_table_targets(op, table_labels)
                tagged = True
                break
        if tagged:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"numbered_table_target_replace_{idx}",
                op_type=OpType.REPLACE,
                **fi_section_target(section),
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


def _match_chapter(match: re.Match[str]) -> str | None:
    return _norm_num_token(match.group("chapter") or "") or None


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
                chapter=_match_chapter(match),
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
        item_label = _norm_num_token(match.group("item_before") or match.group("item_after") or "")
        if not section or not item_label:
            continue
        clauses.append(
            ItemInsertClause(
                section=section,
                chapter=_match_chapter(match),
                moment=int(match.group("moment")),
                item_label=item_label,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


_ANY_CHAPTER: object = object()


def _chapter_matches(op_chapter: str | None, chapter: str | None | object) -> bool:
    return chapter is _ANY_CHAPTER or op_chapter == chapter


def _scope_tags_for_chapter(chapter: str | None) -> tuple[str, ...]:
    return (_EXPLICIT_CHAPTER_SCOPE_TAG,) if chapter else ()


def _has_op(
    ops: list[AmendmentOp],
    *,
    op_type: str,
    section: str,
    moment: int,
    item: str | None,
    chapter: str | None | object = _ANY_CHAPTER,
) -> bool:
    return any(
        op.op_type == op_type
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == section
        and _chapter_matches(op.target_cols.target_chapter, chapter)
        and op.target_cols.target_paragraph == moment
        and (op.target_cols.target_item or None) == item
        and not op.target_cols.target_special
        for op in ops
    )


def _blocks_moment_supplement(
    ops: list[AmendmentOp],
    *,
    section: str,
    chapter: str | None,
) -> bool:
    chapter_filter: str | None | object = chapter if chapter is not None else _ANY_CHAPTER
    return any(
        op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == section
        and _chapter_matches(op.target_cols.target_chapter, chapter_filter)
        and (
            op.target_cols.target_paragraph is not None
            or bool(op.target_cols.target_item)
            or bool(op.target_cols.target_special)
            or not op.numbered_table_targets
        )
        for op in ops
    )


def _append_unique_op(
    ops: list[AmendmentOp],
    *,
    op_id: str,
    op_type: str,
    section: str,
    moment: int | None = None,
    item: str | None = None,
    chapter: str | None = None,
    numbered_table_targets: tuple[str, ...] = (),
) -> None:
    chapter_filter: str | None | object = chapter if chapter is not None else _ANY_CHAPTER
    if _has_op(
        ops,
        op_type=op_type,
        section=section,
        moment=moment or 0,
        item=item,
        chapter=chapter_filter,
    ):
        return
    if moment is None and any(
        op.op_type == op_type
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section == section
        and _chapter_matches(op.target_cols.target_chapter, chapter_filter)
        and op.target_cols.target_paragraph is None
        and not op.target_cols.target_item
        and not op.target_cols.target_special
        for op in ops
    ):
        return
    ops.append(
        AmendmentOp(
            op_id=op_id,
            op_type=OpType(op_type),
            **fi_section_target(
                section,
                chapter=chapter,
                subsection=moment,
                item=item,
            ),
            numbered_table_targets=numbered_table_targets,
            extraction_provenance_tags=(_MIXED_EXPLICIT_TARGET_TAG,),
            scope_provenance_tags=_scope_tags_for_chapter(chapter),
            witness_rule_id=_MIXED_EXPLICIT_TARGET_RULE_ID,
        )
    )


def _parse_item_replace_clauses(johto: str) -> tuple[ItemReplaceClause, ...]:
    clauses: list[ItemReplaceClause] = []
    for match in _ITEM_REPLACE_RE.finditer(johto or ""):
        section = _norm_num_token(match.group("section"))
        item_label = _norm_num_token(match.group("item"))
        if not section or not item_label:
            continue
        clauses.append(
            ItemReplaceClause(
                section=section,
                chapter=_match_chapter(match),
                moment=int(match.group("moment")),
                item_label=item_label,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _parse_bare_section_replace_clauses(johto: str) -> tuple[BareSectionReplaceClause, ...]:
    muutetaan_match = re.search(r"\bmuutetaan\b", johto or "", flags=re.I)
    if muutetaan_match is None:
        return ()
    muutetaan_segment = (johto or "")[muutetaan_match.end() :]
    stop_matches = [
        match.start()
        for match in re.finditer(
            r"\b(?:lisätään|kumotaan|seuraavasti)\b",
            muutetaan_segment,
            flags=re.I,
        )
    ]
    if stop_matches:
        muutetaan_segment = muutetaan_segment[: min(stop_matches)]
    clauses: list[BareSectionReplaceClause] = []
    seen: set[tuple[str | None, str]] = set()

    def append_clause(section: str, chapter: str | None, raw_text: str) -> None:
        key = (chapter, section)
        if not section or key in seen:
            return
        seen.add(key)
        clauses.append(
            BareSectionReplaceClause(
                section=section,
                chapter=chapter,
                raw_text=raw_text,
            )
        )

    for match in _BARE_SECTION_LIST_RE.finditer(muutetaan_segment):
        prefix = " ".join(muutetaan_segment[: match.start()].split())
        if _CHAPTER_HEADING_PAIR_PREFIX_RE.search(prefix):
            continue
        for section in _parse_bare_section_list_run(match.group("run")):
            append_clause(section, None, match.group(0))

    for match in _BARE_REPLACE_SECTION_RE.finditer(muutetaan_segment):
        prefix = " ".join(muutetaan_segment[: match.start()].split())
        if _CHAPTER_HEADING_PAIR_PREFIX_RE.search(prefix):
            continue
        section = _norm_num_token(match.group("section"))
        chapter = _match_chapter(match)
        append_clause(section, chapter, match.group(0))
    return tuple(clauses)


def _parse_bare_section_list_run(run: str) -> tuple[str, ...]:
    """Expand a terminal-section-sign list such as ``6-9, 11 ja 12 §``.

    Historical source typo ``16 aja 18 §`` means ``16 a ja 18 §``.  The
    correction is scoped to a number+letter list immediately before a terminal
    section sign; it does not rewrite the source text globally.
    """
    normalized = _GLUED_ALPHA_JA_RE.sub(
        lambda m: f"{m.group('num')} {m.group('suffix')} ja {m.group('next')}",
        run or "",
    )
    labels: list[str] = []
    for raw_part in _SECTION_LIST_SPLIT_RE.split(normalized):
        part = raw_part.strip()
        if not part:
            continue
        range_match = _SECTION_RANGE_RE.fullmatch(part)
        if range_match is not None:
            expanded = expand_johto_section_label_range(
                range_match.group("start"),
                range_match.group("end"),
            )
        else:
            expanded = (_norm_num_token(part),)
        for label in expanded:
            if label and label not in labels:
                labels.append(label)
    return tuple(labels)


def _parse_moment_replace_clauses(johto: str) -> tuple[MomentTargetClause, ...]:
    clauses: list[MomentTargetClause] = []
    muutetaan_match = re.search(r"\bmuutetaan\b", johto or "", flags=re.I)
    if muutetaan_match is None:
        return ()
    muutetaan_segment = (johto or "")[muutetaan_match.end() :]
    stop_matches = [
        match.start()
        for match in re.finditer(
            r"\b(?:lisätään|kumotaan|seuraavasti)\b",
            muutetaan_segment,
            flags=re.I,
        )
    ]
    if stop_matches:
        muutetaan_segment = muutetaan_segment[: min(stop_matches)]
    for match in _MOMENT_REPLACE_RE.finditer(muutetaan_segment):
        section = _norm_num_token(match.group("section"))
        if not section:
            continue
        clauses.append(
            MomentTargetClause(
                section=section,
                chapter=_match_chapter(match),
                moment=int(match.group("moment")),
                op_type=OpType.REPLACE,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _parse_table_and_moment_replace_clauses(johto: str) -> tuple[MomentTargetClause, ...]:
    clauses: list[MomentTargetClause] = []
    for match in _TABLE_AND_MOMENT_RE.finditer(johto or ""):
        section = _norm_num_token(match.group("section"))
        if not section:
            continue
        clauses.append(
            MomentTargetClause(
                section=section,
                chapter=_match_chapter(match),
                moment=int(match.group("moment")),
                op_type=OpType.REPLACE,
                raw_text=match.group(0),
                table_labels=_numbered_table_targets_by_section(johto).get(section, ()),
            )
        )
    return tuple(clauses)


def _parse_moment_insert_clauses(johto: str) -> tuple[MomentTargetClause, ...]:
    lisataan_segment = ""
    parts = re.split(r"\blisätään\b", johto or "", maxsplit=1, flags=re.I)
    if len(parts) == 2:
        lisataan_segment = parts[1]
    clauses: list[MomentTargetClause] = []
    for match in _INSERT_MOMENT_RE.finditer(lisataan_segment):
        section = _norm_num_token(match.group("section"))
        if not section:
            continue
        clauses.append(
            MomentTargetClause(
                section=section,
                chapter=_match_chapter(match),
                moment=int(match.group("moment")),
                op_type=OpType.INSERT,
                raw_text=match.group(0),
            )
        )
    return tuple(clauses)


def _supplement_mixed_explicit_clause_ops(
    ops: List[AmendmentOp],
    johto: str,
) -> List[AmendmentOp]:
    """Recover explicit targets skipped by long mixed ``muutetaan`` lists."""
    item_clauses = _parse_item_replace_clauses(johto)
    bare_sections = _parse_bare_section_replace_clauses(johto)
    moment_clauses = (
        *_parse_moment_replace_clauses(johto),
        *_parse_table_and_moment_replace_clauses(johto),
        *_parse_moment_insert_clauses(johto),
    )
    if not item_clauses and not bare_sections and not moment_clauses:
        return ops

    supplemented = list(ops)
    table_targets_by_section = _numbered_table_targets_by_section(johto)
    for idx, clause in enumerate(item_clauses):
        _append_unique_op(
            supplemented,
            op_id=f"mixed_item_replace_{idx}_{clause.section}_{clause.moment}_{clause.item_label}",
            op_type=OpType.REPLACE,
            section=clause.section,
            chapter=clause.chapter,
            moment=clause.moment,
            item=clause.item_label,
        )
    for idx, clause in enumerate(moment_clauses):
        if clause.op_type == "REPLACE" and _blocks_moment_supplement(
            ops,
            section=clause.section,
            chapter=clause.chapter,
        ):
            continue
        _append_unique_op(
            supplemented,
            op_id=f"mixed_moment_{clause.op_type.lower()}_{idx}_{clause.section}_{clause.moment}",
            op_type=clause.op_type,
            section=clause.section,
            chapter=clause.chapter,
            moment=clause.moment,
            numbered_table_targets=clause.table_labels or table_targets_by_section.get(clause.section, ()),
        )
    for idx, clause in enumerate(bare_sections):
        _append_unique_op(
            supplemented,
            op_id=f"mixed_bare_section_replace_{idx}_{clause.section}",
            op_type=OpType.REPLACE,
            section=clause.section,
            chapter=clause.chapter,
        )
    return supplemented


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
                op_type=OpType.REPLACE,
                section=clause.section,
                moment=clause.moment,
                item=item_label,
                chapter=clause.chapter if clause.chapter is not None else _ANY_CHAPTER,
            ):
                continue
            supplemented.append(
                AmendmentOp(
                    op_id=f"item_and_moment_replace_item_{clause_index}_{item_label}",
                    op_type=OpType.REPLACE,
                    **fi_section_target(
                        clause.section,
                        chapter=clause.chapter,
                        subsection=clause.moment,
                        item=item_label,
                    ),
                    extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                    scope_provenance_tags=_scope_tags_for_chapter(clause.chapter),
                    witness_rule_id=_ITEM_AND_MOMENT_TARGET_RULE_ID,
                )
            )
        if not _has_op(
            supplemented,
            op_type=OpType.REPLACE,
            section=clause.section,
            moment=clause.extra_moment,
            item=None,
            chapter=clause.chapter if clause.chapter is not None else _ANY_CHAPTER,
        ):
            supplemented.append(
                AmendmentOp(
                    op_id=f"item_and_moment_replace_moment_{clause_index}_{clause.extra_moment}",
                    op_type=OpType.REPLACE,
                    **fi_section_target(
                        clause.section,
                        chapter=clause.chapter,
                        subsection=clause.extra_moment,
                    ),
                    extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                    scope_provenance_tags=_scope_tags_for_chapter(clause.chapter),
                    witness_rule_id=_ITEM_AND_MOMENT_TARGET_RULE_ID,
                )
            )

    for clause_index, clause in enumerate(insert_clauses):
        if _has_op(
            supplemented,
            op_type=OpType.INSERT,
            section=clause.section,
            moment=clause.moment,
            item=clause.item_label,
            chapter=clause.chapter if clause.chapter is not None else _ANY_CHAPTER,
        ):
            continue
        converted = False
        for pos, op in enumerate(supplemented):
            if (
                op.op_type == OpType.INSERT
                and op.target_cols.target_unit_kind == "section"
                and (not op.target_cols.target_section or op.target_cols.target_section == clause.section)
                and (clause.chapter is None or not op.target_cols.target_chapter or op.target_cols.target_chapter == clause.chapter)
                and op.target_cols.target_paragraph == clause.moment
                and not op.target_cols.target_item
                and not op.target_cols.target_special
            ):
                supplemented[pos] = dc_replace(
                    op,
                    **replace_target(
                        op,
                        target_section=clause.section,
                        target_chapter=clause.chapter or op.target_cols.target_chapter,
                        target_item=clause.item_label,
                    ),
                    lo=(
                        _lo_with_path_update(
                            op.lo,
                            chapter=clause.chapter or op.target_cols.target_chapter,
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
                    scope_provenance_tags=tuple(
                        dict.fromkeys((*op.scope_provenance_tags, *_scope_tags_for_chapter(clause.chapter)))
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
                op_type=OpType.INSERT,
                **fi_section_target(
                    clause.section,
                    chapter=clause.chapter,
                    subsection=clause.moment,
                    item=clause.item_label,
                ),
                extraction_provenance_tags=(_ITEM_AND_MOMENT_TARGET_TAG,),
                scope_provenance_tags=_scope_tags_for_chapter(clause.chapter),
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
        kind="ELAB.SPARSE_PARTIAL_SCOPE_ROW_OMISSION_REPEAL",
        role="observation",
        stage="frontend_extraction",
        source_statute=amendment_id,
        blocking=False,
        detail={
            "kind": "ELAB.SPARSE_PARTIAL_SCOPE_ROW_OMISSION_REPEAL",
            "rule_id": _SPARSE_OSALTA_ROW_OMISSION_RULE_ID,
            "source_statute": amendment_id,
            "source_verb": "muutetaan",
            "lowered_action": "REPEAL",
            "target_unit_kind": "paragraph_row",
            "target_section": clause.section,
            "named_row_targets": (clause.row_target,),
            "raw_text": clause.raw_text,
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
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
            op.op_type == OpType.REPEAL
            and op.target_cols.target_unit_kind == "section"
            and op.target_cols.target_section == clause.section
            and tuple(op.named_row_targets) == (clause.row_target,)
            for op in supplemented
        )
        if duplicate:
            continue
        supplemented.append(
            AmendmentOp(
                op_id=f"sparse_osalta_row_omission_repeal_{idx}",
                op_type=OpType.REPEAL,
                **fi_section_target(clause.section),
                named_row_targets=(clause.row_target,),
                extraction_provenance_tags=(_SPARSE_OSALTA_ROW_OMISSION_TAG,),
                witness_rule_id=_SPARSE_OSALTA_ROW_OMISSION_RULE_ID,
            )
        )
        findings.append(_sparse_osalta_recovery_finding(clause, amendment_id=amendment_id))
    return supplemented, tuple(findings)
