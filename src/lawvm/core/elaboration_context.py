"""Typed live-state snapshot types for LawVM's elaboration phase.

These types replace raw ``master`` access in ``_compile_group`` with narrow,
auditable dependencies.  They are the **environment** for elaboration — not a
waist in the pipeline.  The three pipeline waists remain:

    ClauseAST          — clause surface waist (parse output)
    PayloadSurface     — amendment body surface waist (future)
    late-waist bridge  — elaboration → apply

``TargetContext`` is the *local* snapshot for a single amendment group's
target section/chapter/part, computed from the live replay tree immediately
before elaboration begins.  It supports the absent-target case (insert-new
families where ``live_node`` is ``None``).

``ReplayLookups`` is the *global* index — a small, bounded set of facts
extracted once per amendment from the current replay snapshot, shared across
all groups.  It captures uniqueness, chapter membership, and containment
without exposing the full tree.

Neither ``TargetContext`` nor ``ReplayLookups`` has methods that reach back
into ``master``.  They are frozen, serializable, and fact-bearing only.

Cross-jurisdiction shared execution authority continues to converge on
``LegalOperation``.

Architecture reference: ``notes/PRO_RESPONSE3_1.md`` §§ 4, 6, 7.

API tier
--------
Internal elaboration environment contract. Shared inside the compiler, but not
the preferred public/reporting API surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.target_scope import TargetUnitKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path, PathStep


@runtime_checkable
class _TargetSnapshotStateLike(Protocol):
    ir: IRNode

    def find_section_path(
        self,
        target_norm: str,
        target_chapter: str | None,
        target_part: str | None = None,
    ) -> Path | None: ...

    def find(
        self,
        kind: str,
        label: str,
        scope_kind: str | None = None,
        scope_label: str | None = None,
    ) -> Path | None: ...


@runtime_checkable
class _ReplayLookupStateLike(_TargetSnapshotStateLike, Protocol):
    @property
    def snapshot_rev(self) -> int: ...


def _identity_row_anchor_normalizer(text: str) -> str:
    return text


# ---------------------------------------------------------------------------
# LiveSubsectionSlot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSubsectionSlot:
    """One subsection position in the live target section.

    Populated from the live replay tree at snapshot time.  ``ordinal`` is the
    1-based positional index among subsection children of the target node.
    ``label`` is the XML label attribute (may be ``None`` for unlabelled
    nodes).  ``intro_present`` is ``True`` when the subsection has a leading
    intro text node before its item list.  ``item_labels`` collects the labels
    of direct item children (e.g. ``("a", "b", "c")``).  ``row_anchors``
    collects row-id attributes for table rows inside the subsection.
    """

    ordinal: int
    label: Optional[str]
    node: IRNode
    intro_present: bool
    item_labels: Tuple[str, ...]
    row_anchors: Tuple[str, ...]


# ---------------------------------------------------------------------------
# TargetContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetContext:
    """Typed snapshot of the live replay state for one amendment group's target.

    Computed from ``master`` immediately before elaboration.  Immutable,
    serializable, fact-bearing.  No ability to wander back into ``master``.

    **Absent-target case:** For insert-new groups the target does not yet exist
    in the live tree.  In that case ``live_node`` is ``None`` and ``node_path``
    is ``None``, but ``parent_node``, ``parent_path``, and ``sibling_labels``
    are still populated so elaboration can determine insertion order.
    """

    # ------------------------------------------------------------------
    # Target identity
    # ------------------------------------------------------------------
    target_unit_kind: TargetUnitKind
    target_norm: str  # normalized section/chapter/part number
    target_chapter: Optional[str]  # enclosing chapter label, if any

    # ------------------------------------------------------------------
    # Path into the replay tree (None if target doesn't exist yet)
    # ------------------------------------------------------------------
    node_path: Optional[Path]
    parent_path: Optional[Path]

    # ------------------------------------------------------------------
    # Live nodes (None for insert-new targets)
    # ------------------------------------------------------------------
    live_node: Optional[IRNode]
    parent_node: Optional[IRNode]
    sibling_labels: Tuple[str, ...]

    # ------------------------------------------------------------------
    # Subsection structure of the live target
    # ------------------------------------------------------------------
    subsection_slots: Tuple[LiveSubsectionSlot, ...]

    # ------------------------------------------------------------------
    # Optional target scope refinement
    # ------------------------------------------------------------------
    target_part: Optional[str] = None  # enclosing part label, if any

    # ------------------------------------------------------------------
    # Derived property
    # ------------------------------------------------------------------
    @property
    def target_exists(self) -> bool:
        """True when the target section/chapter/part exists in the live tree."""
        return self.live_node is not None

    def __post_init__(self) -> None:
        if self.live_node is None and self.node_path is not None:
            raise ValueError("TargetContext cannot carry node_path without live_node")
        if self.live_node is not None and self.node_path is None:
            raise ValueError("TargetContext with live_node must carry node_path")
        if self.live_node is None and self.subsection_slots:
            raise ValueError("TargetContext without live_node cannot carry subsection_slots")
        if self.node_path is not None and self.node_path[-1][0] != self.target_unit_kind:
            raise ValueError("TargetContext.node_path leaf kind must match target_unit_kind")

# ---------------------------------------------------------------------------
# ReplayLookups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayLookups:
    """Immutable global indexes from the current replay state.

    Extracted once per amendment, shared across all groups.  Contains bounded
    global facts (not the full tree) needed by elaboration:

    ``snapshot_rev``
        Replay-snapshot freshness token used by elaboration caches to detect
        stale snapshots. A true monotonic mutation counter is preferred, but
        the current factory may fall back to a best-effort tree-identity token
        until state owners expose one explicitly.

    ``unique_section_paths``
        Maps ``(target_norm, target_chapter)`` to the node path for sections
        that appear exactly once in the live tree.  Used for uniqueness checks
        during address resolution.

    ``chapter_members``
        Maps chapter label to the frozenset of section labels it contains.
        Used for container-shadowing and supplemental synthesis decisions.

    ``part_members``
        Maps part label to the frozenset of chapter labels it contains.
        Analogous to ``chapter_members`` one level up.

    ``all_section_labels``
        The set of all section labels present anywhere in the live tree.
        Used for existence checks that don't require path resolution
        (e.g. "does section 3a exist somewhere?").
    """

    snapshot_rev: int

    # (target_norm, target_chapter) → node path
    unique_section_paths: Dict[Tuple[str, Optional[str]], Path]

    # chapter label → frozenset of member section labels
    chapter_members: Dict[str, FrozenSet[str]]

    # part label → frozenset of member chapter labels
    part_members: Dict[str, FrozenSet[str]]

    # all section labels in the tree (for existence checks)
    all_section_labels: FrozenSet[str]

    def __post_init__(self) -> None:
        for (section_label, _chapter_label), path in self.unique_section_paths.items():
            if not path or path[-1][0] != IRNodeKind.SECTION.value:
                raise ValueError("ReplayLookups.unique_section_paths must point to section paths")
            if section_label != path[-1][1]:
                raise ValueError("ReplayLookups.unique_section_paths key label must match path leaf label")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_subsection_slot(ordinal: int, sub: IRNode) -> LiveSubsectionSlot:
    """Build a ``LiveSubsectionSlot`` from a subsection IRNode."""
    intro_present = any(child.kind == IRNodeKind.INTRO for child in sub.children)
    item_labels = tuple(
        child.label for child in sub.children if child.kind in (IRNodeKind.PARAGRAPH, IRNodeKind.ITEM) and child.label
    )
    row_anchors = tuple(
        child.attrs.get("row_anchor", "")
        for child in sub.children
        if child.kind in (IRNodeKind.PARAGRAPH, IRNodeKind.ITEM) and child.attrs.get("row_anchor", "")
    )
    return LiveSubsectionSlot(
        ordinal=ordinal,
        label=sub.label,
        node=sub,
        intro_present=intro_present,
        item_labels=item_labels,
        row_anchors=row_anchors,
    )


def _path_to_tuple(path: Optional[Sequence[PathStep]]) -> Optional[Path]:
    """Convert a ``tree_ops`` list path to a hashable tuple, or return None."""
    if path is None:
        return None
    return tuple(path)


def _parent_path(path: Optional[Path]) -> Optional[Path]:
    """Return the parent path (all but the last step), or None."""
    if path is None or len(path) < 1:
        return None
    parent = path[:-1]
    return parent if parent else None


def _snapshot_revision(master: _ReplayLookupStateLike) -> int:
    """Return the best available replay-snapshot freshness token.

    Replay snapshots should expose an explicit freshness token via
    ``snapshot_rev``. The current replay state already does this; other
    callers should implement the same narrow contract instead of relying on
    attribute probing.
    """
    return master.snapshot_rev


# ---------------------------------------------------------------------------
# Factory implementations
# ---------------------------------------------------------------------------


def snapshot_replay_lookups(master: _ReplayLookupStateLike) -> ReplayLookups:
    """Build ``ReplayLookups`` from live master state.

    Walks ``master.ir`` once to extract:

    * ``unique_section_paths`` — sections that appear exactly once (the
      common case), keyed by ``(section_label, chapter_label | None)``.
    * ``chapter_members`` — for each chapter, the frozenset of section labels.
    * ``part_members`` — for each part, the frozenset of chapter labels.

    """
    ir = master.ir

    # Maps (section_label, chapter_label_or_None) → list of paths
    section_path_lists: Dict[Tuple[str, Optional[str]], List[Path]] = {}
    chapter_members: Dict[str, FrozenSet[str]] = {}
    part_members: Dict[str, FrozenSet[str]] = {}
    all_section_labels_set: set = set()

    def _walk(
        node: IRNode,
        prefix: Path,
        current_chapter: Optional[str],
        current_part: Optional[str],
    ) -> None:
        for child in node.children:
            step = (str(child.kind), child.label or "")
            child_path = prefix + (step,)

            if child.kind == IRNodeKind.PART and child.label:
                # Collect chapter children of this part
                part_chapters: List[str] = []
                for grandchild in child.children:
                    if grandchild.kind == IRNodeKind.CHAPTER and grandchild.label:
                        part_chapters.append(grandchild.label)
                part_members[child.label] = frozenset(part_chapters)
                _walk(child, child_path, current_chapter, child.label)

            elif child.kind == IRNodeKind.CHAPTER and child.label:
                # Collect section children of this chapter
                chap_sections: List[str] = []
                for grandchild in child.children:
                    if grandchild.kind == IRNodeKind.SECTION and grandchild.label:
                        chap_sections.append(grandchild.label)
                chapter_members[child.label] = frozenset(chap_sections)
                _walk(child, child_path, child.label, current_part)

            elif child.kind == IRNodeKind.SECTION and child.label:
                sec_label = child.label
                all_section_labels_set.add(sec_label)
                # Keyed with chapter scope
                key_with_chap: Tuple[str, Optional[str]] = (sec_label, current_chapter)
                section_path_lists.setdefault(key_with_chap, []).append(child_path)
                # Also key with None chapter for unscoped lookups
                key_no_chap: Tuple[str, Optional[str]] = (sec_label, None)
                section_path_lists.setdefault(key_no_chap, []).append(child_path)
                _walk(child, child_path, current_chapter, current_part)

            else:
                _walk(child, child_path, current_chapter, current_part)

    _walk(ir, (), None, None)

    # Unique section paths: only include entries with exactly one candidate
    unique_section_paths: Dict[Tuple[str, Optional[str]], Path] = {
        key: paths[0] for key, paths in section_path_lists.items() if len(paths) == 1
    }

    return ReplayLookups(
        snapshot_rev=_snapshot_revision(master),
        unique_section_paths=unique_section_paths,
        chapter_members=chapter_members,
        part_members=part_members,
        all_section_labels=frozenset(all_section_labels_set),
    )


def snapshot_target_context(
    master: _TargetSnapshotStateLike,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    lookups: ReplayLookups,
    target_part: Optional[str] = None,
) -> TargetContext:
    """Build ``TargetContext`` from live master state.

    Resolves the target node in ``master.ir`` and captures:

    * ``node_path`` / ``live_node`` — the target itself (None if absent)
    * ``parent_path`` / ``parent_node`` — the enclosing container
    * ``sibling_labels`` — labels of all children of the parent node
    * ``subsection_slots`` — subsection children of the live target section

    Supports the absent-target case: for insert-new families ``live_node``
    will be ``None`` but parent context is still populated.
    """
    unit_kind = target_unit_kind
    ir = master.ir
    expected_child_kind = (
        IRNodeKind.SECTION
        if unit_kind == "section"
        else IRNodeKind.CHAPTER
        if unit_kind == "chapter"
        else IRNodeKind.PART
        if unit_kind == "part"
        else None
    )

    # ------------------------------------------------------------------
    # Resolve target path
    # ------------------------------------------------------------------
    if unit_kind == "section":
        raw_path = master.find_section_path(target_norm, target_chapter, target_part)
    elif unit_kind == "chapter":
        if target_part:
            part_path = master.find("part", target_part)
            part_node = _tops.resolve(ir, part_path) if part_path is not None else None
            chapter_path = _tops.find(part_node, "chapter", target_norm) if part_node is not None else None
            raw_path = part_path + chapter_path if part_path is not None and chapter_path is not None else None
        else:
            raw_path = master.find("chapter", target_norm)
    elif unit_kind == "part":
        raw_path = master.find("part", target_norm)
    else:
        raw_path = master.find_section_path(target_norm, target_chapter, target_part)

    node_path: Optional[Path] = _path_to_tuple(raw_path)
    live_node: Optional[IRNode] = _tops.resolve(ir, raw_path) if raw_path is not None else None

    # ------------------------------------------------------------------
    # Resolve parent
    # ------------------------------------------------------------------
    raw_parent_path: Optional[Path] = _parent_path(raw_path)
    if raw_parent_path is None:
        if unit_kind == "section" and target_chapter:
            if target_part:
                part_path = master.find("part", target_part)
                part_node = _tops.resolve(ir, part_path) if part_path is not None else None
                chapter_path = _tops.find(part_node, "chapter", target_chapter) if part_node is not None else None
                if part_path is not None and chapter_path is not None:
                    raw_parent_path = part_path + chapter_path
            if raw_parent_path is None:
                raw_parent_path = master.find("chapter", target_chapter)
        elif unit_kind == "chapter" and target_part:
            raw_parent_path = master.find("part", target_part)
        if raw_parent_path is None:
            raw_parent_path = ()
    parent_path: Optional[Path] = _path_to_tuple(raw_parent_path)
    parent_node: Optional[IRNode] = (
        _tops.resolve(ir, raw_parent_path) if raw_parent_path is not None else ir  # target is a top-level child of body
    )

    # ------------------------------------------------------------------
    # Sibling labels from parent
    # ------------------------------------------------------------------
    if parent_node is not None:
        sibling_labels: Tuple[str, ...] = tuple(
            child.label
            for child in parent_node.children
            if expected_child_kind is not None and child.kind is expected_child_kind and child.label
        )
    else:
        sibling_labels = ()

    # ------------------------------------------------------------------
    # Subsection slots from live target node (sections only)
    # ------------------------------------------------------------------
    subsection_slots: Tuple[LiveSubsectionSlot, ...] = ()
    if live_node is not None and unit_kind == "section":
        slots: List[LiveSubsectionSlot] = []
        ordinal = 0
        for child in live_node.children:
            if child.kind == IRNodeKind.SUBSECTION:
                ordinal += 1
                slots.append(_make_subsection_slot(ordinal, child))
        subsection_slots = tuple(slots)

    return TargetContext(
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
        node_path=node_path,
        parent_path=parent_path,
        live_node=live_node,
        parent_node=parent_node,
        sibling_labels=sibling_labels,
        subsection_slots=subsection_slots,
    )


# ---------------------------------------------------------------------------
# PayloadElaborationContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayloadElaborationContext:
    """Typed context for payload normalization — replaces raw master access.

    Derived from TargetContext + ReplayLookups.  Carries the full local live
    subtree for omission/text/tree-sensitive work, plus precomputed indexes
    for repeated normalized lookups.

    Rule: arbitrary depth inside a bounded snapshot is fine.
    Arbitrary breadth over the live master is not.

    Dependency classes for helpers below _elaborate_group:

    - Class 1 (amendment-only): no live state — use PayloadSurface only
    - Class 2 (local live subtree): traverse ctx.live_node
    - Class 3 (replay topology): use ctx.lookups
    - Class 4 (ambient master): FORBIDDEN below _elaborate_group
    """

    # Target identity (copied from TargetContext)
    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str]

    # Bounded local snapshot — traversal allowed to any depth
    live_node: Optional[IRNode]
    parent_node: Optional[IRNode]

    # From TargetContext
    subsection_slots: Tuple[LiveSubsectionSlot, ...]

    # Precomputed indexes (high fan-out across helpers)
    live_subsections: Tuple[IRNode, ...]
    subsection_by_label: Mapping[str, IRNode]  # label -> subsection node
    item_index: Mapping[Tuple[int, str], IRNode]  # (subsection_ordinal, item_label) -> node
    row_anchor_index: Mapping[str, IRNode]  # normalized anchor text -> node
    container_member_labels: Optional[FrozenSet[str]]

    # Bounded global facts only
    lookups: ReplayLookups

    def __post_init__(self) -> None:
        if self.live_node is None:
            if self.live_subsections:
                raise ValueError("PayloadElaborationContext without live_node cannot carry live_subsections")
            if self.subsection_by_label or self.item_index or self.row_anchor_index:
                raise ValueError("PayloadElaborationContext without live_node cannot carry live-derived indexes")
        else:
            direct_subsections = tuple(child for child in self.live_node.children if child.kind == IRNodeKind.SUBSECTION)
            if self.live_subsections != direct_subsections:
                raise ValueError("PayloadElaborationContext.live_subsections must match direct live subsections")
        if self.container_member_labels is not None and self.target_chapter is None:
            raise ValueError("PayloadElaborationContext.container_member_labels requires target_chapter")
        if any(ordinal < 1 or ordinal > len(self.live_subsections) for ordinal, _label in self.item_index):
            raise ValueError("PayloadElaborationContext.item_index ordinals must refer to live_subsections")


def build_payload_elaboration_context(
    target_ctx: TargetContext,
    lookups: ReplayLookups,
    *,
    row_anchor_normalizer: Callable[[str], str] = _identity_row_anchor_normalizer,
) -> PayloadElaborationContext:
    """Build PayloadElaborationContext from TargetContext + ReplayLookups.

    Copies target identity fields and live snapshot from target_ctx, then
    builds the precomputed indexes over the live subsection children:

    * ``live_subsections`` — tuple of subsection children of live_node
    * ``subsection_by_label`` — {label: node} for labelled subsections
    * ``item_index`` — {(subsection_ordinal, item_label): paragraph_node}
      where subsection_ordinal is the 1-based ordinal of the parent subsection
      and item_label is the paragraph's label attribute
    * ``row_anchor_index`` — {normalized_anchor_text: paragraph_node}
      for paragraph children of subsections that carry a row_anchor attr
    * ``container_member_labels`` — frozenset of member section labels for
      the enclosing chapter, or None if target_chapter is absent
    """
    live_node = target_ctx.live_node

    # Build live_subsections and subsection_by_label
    live_subsections_list: List[IRNode] = []
    subsection_by_label: Dict[str, IRNode] = {}
    if live_node is not None:
        for child in live_node.children:
            if child.kind == IRNodeKind.SUBSECTION:
                live_subsections_list.append(child)
                if child.label:
                    subsection_by_label[child.label] = child

    live_subsections: Tuple[IRNode, ...] = tuple(live_subsections_list)

    # Build item_index and row_anchor_index
    # item_index: (subsection_ordinal, paragraph_label) -> paragraph node
    # row_anchor_index: normalized row_anchor attr text -> paragraph node
    item_index_dict: Dict[Tuple[int, str], IRNode] = {}
    row_anchor_index_dict: Dict[str, IRNode] = {}
    for ordinal, sub in enumerate(live_subsections, start=1):
        for child in sub.children:
            if child.kind in {IRNodeKind.PARAGRAPH, IRNodeKind.ITEM}:
                if child.label:
                    item_index_dict[(ordinal, child.label)] = child
                row_anchor = row_anchor_normalizer(child.attrs.get("row_anchor", ""))
                if row_anchor:
                    row_anchor_index_dict[row_anchor] = child

    # container_member_labels from chapter membership in lookups
    container_member_labels: Optional[FrozenSet[str]] = None
    if target_ctx.target_chapter is not None:
        container_member_labels = lookups.chapter_members.get(target_ctx.target_chapter)

    return PayloadElaborationContext(
        target_unit_kind=target_ctx.target_unit_kind,
        target_norm=target_ctx.target_norm,
        target_chapter=target_ctx.target_chapter,
        live_node=live_node,
        parent_node=target_ctx.parent_node,
        subsection_slots=target_ctx.subsection_slots,
        live_subsections=live_subsections,
        subsection_by_label=subsection_by_label,
        item_index=item_index_dict,
        row_anchor_index=row_anchor_index_dict,
        container_member_labels=container_member_labels,
        lookups=lookups,
    )
