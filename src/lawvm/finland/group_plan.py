"""Grouping helpers for Finnish amendment compile planning."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.helpers import _norm_num_token


GroupTargetKey = Tuple[IRNodeKind, str, Optional[str], Optional[str]]


def target_group_key(op: AmendmentOp) -> GroupTargetKey:
    def norm(s: str) -> str:
        return re.sub(r"[^\d\w]", "", s).lower()

    # For osa/part targets, use _norm_num_token which converts Roman numerals
    # to Arabic (III→3, V→5). The master tree stores parts with Arabic labels
    # but the PEG parser emits Roman numeral labels from the johtolause.
    section_norm = (
        _norm_num_token(op.target_section)
        if op.target_unit_kind == "part" and op.target_section
        else norm(op.target_section)
    )
    chapter = norm(op.target_chapter) if op.target_unit_kind == "section" and op.target_chapter else None
    part = (
        _norm_num_token(op.target_part)
        if op.target_unit_kind in {"section", "chapter"} and op.target_part
        else None
    )
    return (IRNodeKind(op.target_unit_kind), section_norm, chapter, part)


def group_ops_by_target(ops: List[AmendmentOp]) -> Dict[GroupTargetKey, List[AmendmentOp]]:
    section_groups: Dict[GroupTargetKey, List[AmendmentOp]] = defaultdict(list)
    for op in ops:
        section_groups[target_group_key(op)].append(op)
    return section_groups


__all__ = [
    "target_group_key",
    "group_ops_by_target",
]
