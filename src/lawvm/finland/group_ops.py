"""Group-level op sorting and remapping heuristics.

Extracted from grafter.py (Phase E, lines 899–1068).  These functions are
called from ``_compile_group`` and are self-contained sorting/remapping
transforms on ``List[AmendmentOp]``.

Dependencies:
- ``AmendmentOp`` (ops.py)
- ``IRNode`` + ``irnode_to_text`` (core.ir)
- ``tree_ops`` (core.tree_ops) — read-only resolve calls
- ``_norm_num_token`` (helpers.py)
- ``_relabel_section_ir`` (apply_ir_ops.py)
- ``TargetContext``, ``ReplayLookups`` (core.elaboration_context) — typed snapshots
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace as dc_replace
from itertools import pairwise
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.elaboration_context import TargetContext, ReplayLookups
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import (
    AmendmentOp,
    ResolvedOp,
    projection_scope_confidence,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.apply_ir_ops import _relabel_section_ir

if TYPE_CHECKING:
    pass


def normalize_group_ops_for_repeal_reenact(
    group_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Collapse a REPEAL + non-repeal group into a single REPLACE op.

    Only applies when there is exactly one whole-section repeal and other ops
    target the same section — indicating a repeal-and-reenact pattern.
    Multiple whole-section repeals in a group are pure repeals and must not be
    converted.
    """
    whole_repeals = [o for o in group_ops if o.op_type == "REPEAL" and not o.target_paragraph]
    other_ops = [o for o in group_ops if o not in whole_repeals]
    # Only convert when exactly one repeal exists and other ops target the same section
    if len(whole_repeals) == 1 and other_ops:
        repeal_op = whole_repeals[0]
        # Check that at least one other op targets the same section
        repeal_target = (repeal_op.target_section or "").strip()
        same_section_ops = (
            [o for o in other_ops if (o.target_section or "").strip() == repeal_target] if repeal_target else []
        )
        if same_section_ops:
            new_lo = dc_replace(repeal_op.lo, action=StructuralAction.REPLACE) if repeal_op.lo else None
            return [
                dc_replace(
                    repeal_op,
                    op_type="REPLACE",
                    lo=new_lo,
                    extraction_provenance_tags=tuple(
                        dict.fromkeys((*repeal_op.extraction_provenance_tags, "repeal_reenact_normalized"))
                    ),
                )
            ]
    return group_ops


def remap_body_root_replace_group_before_terminal_voimaantulo(
    target_ctx: TargetContext,
    lookups: ReplayLookups,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> Tuple[str, Optional[IRNode], List[AmendmentOp]]:
    """Turn malformed body-root ``REPLACE 4 §`` into ``INSERT 3a §`` before terminal voimaantulo.

    Some short decrees are restated in full and encoded as generic body-root section
    replaces. If the live statute ends with ``4 § Voimaantulo`` and the amendment body
    contains a new substantive ``4 §``, the intended effect is usually ``insert 3 a §``
    before the terminal voimaantulo section, not overwrite ``4 §``.

    Reads from ``target_ctx`` (live_node, parent_node, sibling_labels) and
    ``lookups`` (all_section_labels for existence check on the insert target).
    No direct ``master`` access.
    """
    target_unit_kind = target_ctx.target_unit_kind
    target_norm = target_ctx.target_norm
    target_chapter = target_ctx.target_chapter

    def _heading_text(node: Optional[IRNode]) -> str:
        if node is None:
            return ""
        heading = next((c for c in node.children if c.kind == IRNodeKind.HEADING), None)
        return " ".join(irnode_to_text(heading).split()).strip().lower() if heading is not None else ""

    def _next_letter_label(label: str) -> Optional[str]:
        norm = _norm_num_token(label)
        m = re.fullmatch(r"(\d+)([a-z]?)", norm)
        if not m:
            return None
        base, suffix = m.groups()
        if not suffix:
            return f"{base}a"
        if suffix == "z":
            return None
        return f"{base}{chr(ord(suffix) + 1)}"

    if (
        target_unit_kind != "section"
        or target_chapter is not None
        or muutos_ir is None
        or not re.fullmatch(r"\d+", target_norm)
        or not group_ops
        or any(
            op.op_type != "REPLACE"
            or op.target_paragraph
            or op.target_item
            or op.target_special
            or not op.body_root_replace_fallback
            for op in group_ops
        )
    ):
        return target_norm, muutos_ir, group_ops

    # Use target_ctx.live_node instead of master.find_section_path + resolve
    existing = target_ctx.live_node
    if existing is None:
        return target_norm, muutos_ir, group_ops

    if not _heading_text(existing).startswith("voimaantulo"):
        return target_norm, muutos_ir, group_ops
    if not _heading_text(muutos_ir) or _heading_text(muutos_ir).startswith("voimaantulo"):
        return target_norm, muutos_ir, group_ops

    # Use target_ctx.parent_node instead of resolving parent from master.ir
    parent = target_ctx.parent_node
    section_siblings = [c for c in parent.children if c.kind == IRNodeKind.SECTION] if parent is not None else []
    if existing not in section_siblings:
        return target_norm, muutos_ir, group_ops
    existing_idx = section_siblings.index(existing)
    if existing_idx <= 0:
        return target_norm, muutos_ir, group_ops

    insert_label = _next_letter_label(section_siblings[existing_idx - 1].label or "")
    # Use lookups.all_section_labels for existence check (no master access)
    if not insert_label or insert_label in lookups.all_section_labels:
        return target_norm, muutos_ir, group_ops

    remapped_ops = [
        dc_replace(
            op,
            op_type="INSERT",
            target_section=insert_label,
        )
        for op in group_ops
    ]
    return insert_label, _relabel_section_ir(muutos_ir, insert_label), remapped_ops


@dataclass(frozen=True)
class CompiledOpTargetScope:
    """Neutral target-scope carrier for compiled-op reporting rows."""

    target_unit_kind: str
    target_norm: str
    target_chapter: str
    target_part: str
    target_paragraph: str
    target_item: str
    target_special: str

    @classmethod
    def from_amendment_op(cls, op: AmendmentOp) -> "CompiledOpTargetScope":
        return cls(
            target_unit_kind=op.target_unit_kind,
            target_norm=_norm_num_token(op.target_section) if op.target_section else "",
            target_chapter=_norm_num_token(op.target_chapter) if op.target_chapter else "",
            target_part=_norm_num_token(op.target_part) if op.target_part else "",
            target_paragraph=str(op.target_paragraph) if op.target_paragraph is not None else "",
            target_item=str(op.target_item).strip() if op.target_item is not None else "",
            target_special=str(op.target_special).strip() if op.target_special is not None else "",
        )

    @classmethod
    def from_resolved_op(cls, rop: ResolvedOp) -> "CompiledOpTargetScope":
        return cls(
            target_unit_kind=rop.target_unit_kind,
            target_norm=rop.resolved_target_label,
            target_chapter=_norm_num_token(rop.resolved_target_scope_chapter_label or ""),
            target_part=_norm_num_token(rop.resolved_target_scope_part_label or ""),
            target_paragraph=str(rop.effective_target_paragraph) if rop.effective_target_paragraph is not None else "",
            target_item=str(rop.effective_target_item_label).strip() if rop.effective_target_item_label is not None else "",
            target_special=str(rop.effective_target_special).strip() if rop.effective_target_special is not None else "",
        )


def _item_target_sort_key(item: Optional[str]) -> tuple[int, int, str]:
    """Natural sort key for Finland item labels within one subsection.

    Keeps legal insertion order stable for targets like ``5a``, ``5b``, ``9``,
    ``10`` instead of lexicographic order, which would place ``10`` before
    ``5a``/``5b`` and can corrupt replay insertion order.
    """
    token = re.sub(r"[)\s.]", "", item or "").strip().lower()
    if not token:
        return (0, -1, "")
    m = re.fullmatch(r"(\d+)([a-z]?)", token, flags=re.I)
    if m:
        return (1, int(m.group(1)), m.group(2))
    if re.fullmatch(r"[a-z]", token, flags=re.I):
        return (2, 0, token)
    return (3, 0, token)


def _op_apply_sort_key(op: AmendmentOp) -> tuple[int, tuple[int, int, str]]:
    return (op.target_paragraph or 0, _item_target_sort_key(op.target_item))


def sort_group_ops_for_apply(
    target_ctx: TargetContext,
    group_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Sort *group_ops* so that INSERT ops land after REPLACEs when needed.

    Reads from ``target_ctx.live_node`` to inspect subsection children.
    No direct ``master`` access.

    Returns a new list; does not mutate *group_ops*.
    """
    plain_moment_ops = [
        o
        for o in group_ops
        if (
            o.target_unit_kind == "section"
            and o.target_paragraph
            and not o.target_item
            and not o.target_special
            and o.op_type in ("REPLACE", "INSERT")
        )
    ]
    if plain_moment_ops:
        # Narrow historical sparse-rewrite carve-out: when a pure exact-bound
        # REPLACE group includes ``1 mom`` plus a later gapped target, the
        # leading replace may absorb old live ``2 mom`` and rebase the section
        # before the later target lands. Run only that family in ascending
        # order; keep the broader reverse-order default for other pure REPLACE
        # groups so existing sparse merge behaviour stays stable.
        if all(o.op_type == "REPLACE" for o in plain_moment_ops):
            replace_targets = sorted(
                int(str(o.target_paragraph))
                for o in plain_moment_ops
                if o.target_paragraph is not None
            )
            sec = target_ctx.live_node
            live_labels = (
                sorted(
                    int(str(child.label))
                    for child in sec.children
                    if child.kind == IRNodeKind.SUBSECTION and (child.label or "").isdigit()
                )
                if sec is not None
                else []
            )
            # Consecutive tail-only sparse replacements must run ascending.
            # When replay currently has ``1,2`` and the amendment replaces
            # ``3 mom`` and ``4 mom``, descending order runs ``4 mom`` first
            # and the subsection executor treats it as a gap, dropping the
            # later moment entirely.  Keep this carve-out narrow: only pure
            # replace groups that start exactly at the next live label and
            # form one consecutive tail are reordered.
            if (
                live_labels
                and replace_targets
                and replace_targets[0] == live_labels[-1] + 1
                and replace_targets == list(range(replace_targets[0], replace_targets[-1] + 1))
            ):
                return sorted(group_ops, key=_op_apply_sort_key)
            if (
                len(replace_targets) >= 2
                and replace_targets[0] == 1
                and any(curr > prev + 1 for prev, curr in pairwise(replace_targets))
            ):
                return sorted(group_ops, key=_op_apply_sort_key)
        # When all plain moment ops are INSERTs, sort ascending so earlier
        # subsections are inserted first (each shifts later siblings).
        # This applies even when the group contains non-moment ops (e.g.
        # otsikko replaces) since those don't interact with subsection
        # ordering — they have target_paragraph=None and sort to the front.
        if all(o.op_type == "INSERT" for o in plain_moment_ops):
            return sorted(group_ops, key=_op_apply_sort_key)
        if (
            len(plain_moment_ops) == len(group_ops)
            and any(o.op_type == "INSERT" for o in plain_moment_ops)
            and any(o.op_type == "REPLACE" for o in plain_moment_ops)
        ):
            # Use target_ctx.live_node instead of master.find_section_path + resolve
            sec = target_ctx.live_node
            if sec is not None:
                live_labels = {
                    int(child.label)  # ty: ignore[invalid-argument-type]  # guarded by isdigit() filter
                    for child in sec.children
                    if child.kind == IRNodeKind.SUBSECTION and (child.label or "").isdigit()
                }
                insert_targets = [
                    int(o.target_paragraph)
                    for o in plain_moment_ops
                    if o.op_type == "INSERT" and o.target_paragraph is not None
                ]
                replace_targets = {
                    int(o.target_paragraph)
                    for o in plain_moment_ops
                    if o.op_type == "REPLACE" and o.target_paragraph is not None
                }
                if insert_targets and live_labels and min(insert_targets) > max(live_labels):
                    return sorted(
                        group_ops,
                        key=_op_apply_sort_key,
                    )
                if not (replace_targets & live_labels):
                    return sorted(
                        group_ops,
                        key=_op_apply_sort_key,
                    )
    return sorted(group_ops, key=lambda o: (-(o.target_paragraph or 0), _item_target_sort_key(o.target_item)))


def append_compiled_group_ops(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    resolved_ops: List[ResolvedOp],
) -> None:
    """Append *resolved_ops* as serialised dicts into *compiled_ops_out* (in-place)."""
    if compiled_ops_out is None:
        return
    for rop in resolved_ops:
        sequence = len(compiled_ops_out) + 1
        target_scope = CompiledOpTargetScope.from_resolved_op(rop)
        row: Dict[str, object] = {
            "sequence": sequence,
            "action": rop.resolved_action_type.lower(),
            "source_statute": rop.resolved_source_statute,
            "source_title": rop.resolved_source_title or None,
            "extraction_provenance_tags": list(rop.extraction_provenance_tags),
            "target_guessing_provenance_tags": list(rop.target_guessing_provenance_tags),
            "scope_provenance_tags": list(rop.scope_provenance_tags),
            "witness_rule_id": rop.witness_rule_id,
            **asdict(target_scope),
        }
        scope_confidence = projection_scope_confidence(
            scope_confidence=rop.scope_confidence,
            scope_provenance_tags=rop.scope_provenance_tags,
            resolved_chapter=rop.resolved_target_scope_chapter_label,
        )
        if scope_confidence is not None:
            row["scope_source"] = scope_confidence.source
            row["scope_confidence"] = scope_confidence.confidence
        compiled_ops_out.append(row)
