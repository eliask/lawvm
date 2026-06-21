"""Typed boundary for compiling one Finland amendment target group."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from lawvm.core.compile_result import StrictProfile
from lawvm.core.elaboration_context import ReplayLookups, TargetUnitKind
from lawvm.finland.ops import AmendmentOp, ReplayProfile
from lawvm.finland.sparse_tail_claims import SparseOmissionTailClaim
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.standalone_targets import StandaloneSectionTarget
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
    source_model: AmendmentSourceModel
    johto: str
    profile: ReplayProfile
    strict_profile: Optional[StrictProfile]
    foreign_scoped_standalone_section_targets: Set[str]
    foreign_scoped_replace_section_targets: Set[str]
    foreign_scoped_descendant_section_targets: Set[str] = field(default_factory=set)
    foreign_scoped_replace_section_target_scopes: frozenset[StandaloneSectionTarget] = frozenset()
    sparse_omission_tail_claims: tuple[SparseOmissionTailClaim, ...] = ()
    lookups: Optional[ReplayLookups] = None


@dataclass(frozen=True, slots=True)
class CompileGroupSinks:
    """Mutable artifact channels for compiling one same-target group."""

    compiled_ops_out: Optional[List[Dict[str, object]]] = None
