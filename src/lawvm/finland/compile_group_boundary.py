"""Typed boundary for compiling one Finland amendment target group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

import lxml.etree as etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.elaboration_context import ReplayLookups, TargetUnitKind
from lawvm.finland.body_pairing import ObservedBodyUnit
from lawvm.finland.ops import AmendmentOp, ReplayProfile
from lawvm.finland.statute import ReplayState


@dataclass(frozen=True, slots=True)
class CompileGroupRequest:
    """Semantic inputs for compiling one same-target amendment group."""

    master: ReplayState
    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    group_ops: List[AmendmentOp]
    standalone_section_targets: Set[str]
    inserted_chapter_labels: Set[str]
    muutos_tree: etree._Element
    johto: str
    profile: ReplayProfile
    strict_profile: Optional[StrictProfile]
    foreign_scoped_standalone_section_targets: Set[str]
    foreign_scoped_replace_section_targets: Set[str]
    lookups: Optional[ReplayLookups] = None
    body_inventory: Optional[Sequence[ObservedBodyUnit]] = None


@dataclass(frozen=True, slots=True)
class CompileGroupSinks:
    """Mutable artifact channels for compiling one same-target group."""

    compiled_ops_out: Optional[List[Dict[str, object]]] = None
