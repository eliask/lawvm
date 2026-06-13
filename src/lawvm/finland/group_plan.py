"""Grouping helpers for Finnish amendment compile planning."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple, cast, overload

from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.helpers import _norm_num_token


@dataclass(frozen=True, slots=True, eq=False)
class GroupTargetKey:
    """Normalized amendment-operation group key.

    Legacy callers historically used ``(unit_kind, target_norm, chapter, part)``
    tuples.  This carrier remains tuple-compatible while giving phase-boundary
    code named fields.
    """

    unit_kind: IRNodeKind
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]

    def as_tuple(self) -> Tuple[IRNodeKind, str, Optional[str], Optional[str]]:
        return (self.unit_kind, self.target_norm, self.target_chapter, self.target_part)

    def __iter__(self) -> Iterator[IRNodeKind | str | None]:
        return iter(self.as_tuple())

    def __len__(self) -> int:
        return 4

    @overload
    def __getitem__(self, index: int) -> IRNodeKind | str | None: ...

    @overload
    def __getitem__(self, index: slice) -> Tuple[IRNodeKind, str, Optional[str], Optional[str]]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> IRNodeKind | str | None | Tuple[IRNodeKind, str, Optional[str], Optional[str]]:
        if isinstance(index, slice):
            return cast(Tuple[IRNodeKind, str, Optional[str], Optional[str]], self.as_tuple()[index])
        return self.as_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GroupTargetKey):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False

    def __hash__(self) -> int:
        return hash(self.as_tuple())


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
    return GroupTargetKey(IRNodeKind(op.target_unit_kind), section_norm, chapter, part)


def normalize_group_target_key(
    key: GroupTargetKey | Tuple[IRNodeKind, str, Optional[str], Optional[str]],
) -> GroupTargetKey:
    if isinstance(key, GroupTargetKey):
        return key
    unit_kind, target_norm, target_chapter, target_part = key
    return GroupTargetKey(unit_kind, target_norm, target_chapter, target_part)


def group_ops_by_target(ops: List[AmendmentOp]) -> Dict[GroupTargetKey, List[AmendmentOp]]:
    section_groups: Dict[GroupTargetKey, List[AmendmentOp]] = defaultdict(list)
    for op in ops:
        section_groups[target_group_key(op)].append(op)
    return section_groups


__all__ = [
    "GroupTargetKey",
    "target_group_key",
    "normalize_group_target_key",
    "group_ops_by_target",
]
