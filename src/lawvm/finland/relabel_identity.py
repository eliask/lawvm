"""Typed identity helpers for same-parent Finnish relabel chains."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.tree_ops import Path
from lawvm.core.elaboration_context import TargetUnitKind


@dataclass(frozen=True, slots=True)
class RelabelParentKey:
    """Identity of a relabel chain constrained to one parent path."""

    unit_kind: TargetUnitKind
    parent_path: Path
