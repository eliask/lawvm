"""Chapter-seeding for partial-base Finnish statutes.

Pre-1900 statutes sometimes have partial base XML where entire chapters are
omission placeholders.  When amendments target these missing chapters, REPLACE
ops fail and cascade into hundreds of missing sections.

This module provides ``seed_missing_chapters``, called from ``replay_xml``
before the amendment loop, plus the pure helper functions it relies on.

``seed_missing_chapters`` is pure: it takes an IRNode and returns the
updated IRNode together with the set of (chapter_label, amendment_id) pairs
that were seeded.  The caller is responsible for propagating the new IR.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import xml_to_ir_node
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import default_label_sort_key
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.replay_notices import replay_print
from lawvm.corpus_store import CorpusStore

DEBUG = False
_MISSING_CHAPTER_SPAN_RE = re.compile(
    r"\bpuuttuu\s+luvut?\s+(\d+)\s*[-–]\s*(\d+)\b",
    re.IGNORECASE,
)


def _chapter_sort_key(label: str) -> Tuple[int, str, int]:
    return default_label_sort_key(label)


@dataclass(frozen=True)
class ChapterSeedDiagnostic:
    """Typed diagnostic for Finland missing-chapter seeding repairs."""

    rule_id: str
    family: str
    phase: str
    reason: str
    source_statute: str = ""
    chapter_label: str = ""
    blocking: bool = True
    strict_disposition: str = "block"
    quirks_disposition: str = "record"

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "phase": self.phase,
            "reason": self.reason,
            "source_statute": self.source_statute,
            "chapter_label": self.chapter_label,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


def _record_chapter_seed_diagnostic(
    diagnostics_out: Optional[List[ChapterSeedDiagnostic]],
    *,
    rule_id: str,
    family: str,
    phase: str,
    reason: str,
    source_statute: str = "",
    chapter_label: str = "",
    blocking: bool = True,
    quirks_disposition: str = "record",
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        ChapterSeedDiagnostic(
            rule_id=rule_id,
            family=family,
            phase=phase,
            reason=reason,
            source_statute=source_statute,
            chapter_label=chapter_label,
            blocking=blocking,
            quirks_disposition=quirks_disposition,
        )
    )


# ---------------------------------------------------------------------------
# Pure helpers (no XMLStatute dependency)
# ---------------------------------------------------------------------------

def _find_chapter_containers_with_omissions(
    tree: IRNode, path: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[List[Tuple[str, str]], IRNode]]:
    """Find all nodes that have both chapter and omission children.

    Returns list of (path_to_node, node) tuples.  The path is a list of
    (kind, label) pairs from root to node (exclusive of root).
    """
    if path is None:
        path = []
    results = []
    has_chapter = any(c.kind == IRNodeKind.CHAPTER for c in tree.children)
    has_omission = any(c.kind == IRNodeKind.OMISSION for c in tree.children)
    if has_chapter and has_omission:
        results.append((list(path), tree))
    # Recurse into hcontainer and body nodes (not into chapters themselves)
    for child in tree.children:
        if child.kind in (IRNodeKind.BODY, IRNodeKind.HCONTAINER):
            child_step = (child.kind.value, child.label or "")
            results.extend(_find_chapter_containers_with_omissions(
                child, path + [child_step]
            ))
    return results


def _find_chapter_containers(
    tree: IRNode,
    path: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[List[Tuple[str, str]], IRNode]]:
    """Find body/hcontainer nodes that directly contain chapter children."""
    if path is None:
        path = []
    results: List[Tuple[List[Tuple[str, str]], IRNode]] = []
    if any(child.kind == IRNodeKind.CHAPTER for child in tree.children):
        results.append((list(path), tree))
    for child in tree.children:
        if child.kind in (IRNodeKind.BODY, IRNodeKind.HCONTAINER):
            child_step = (child.kind.value, child.label or "")
            results.extend(_find_chapter_containers(child, path + [child_step]))
    return results


def _last_chapter_label(children: Sequence[IRNode]) -> Optional[str]:
    """Return the label of the last chapter node in the list, or None."""
    for child in reversed(children):
        if child.kind == IRNodeKind.CHAPTER and child.label is not None:
            return child.label
    return None


def _next_chapter_label(after_node: IRNode, all_children: Sequence[IRNode]) -> Optional[str]:
    """Return the label of the next chapter node after after_node."""
    found = False
    for child in all_children:
        if child is after_node:
            found = True
            continue
        if found and child.kind == IRNodeKind.CHAPTER and child.label is not None:
            return child.label
    return None


def _chapter_missing_span_notice(chapter: IRNode) -> Optional[Tuple[str, str]]:
    """Return a textual missing-chapter span declared inside the chapter, if any."""
    section_children = [child for child in chapter.children if child.kind == IRNodeKind.SECTION]
    if not section_children:
        return None
    text = " ".join(irnode_to_text(section_children[-1]).split())
    m = _MISSING_CHAPTER_SPAN_RE.search(text)
    if m is None:
        return None
    return m.group(1), m.group(2)


def _labels_in_missing_span(
    labels: List[str],
    start_label: str,
    end_label: str,
) -> List[str]:
    """Filter labels to those inside a textual missing-chapter span.

    The textual form ``Puuttuu luvut 7-11`` is treated as the missing gap
    starting at 7 and ending *before* the next present chapter 11.
    """
    start_key = default_label_sort_key(start_label)
    end_key = default_label_sort_key(end_label)
    return [
        label for label in labels
        if start_key <= default_label_sort_key(label) < end_key
    ]


def _strip_trailing_missing_span_notice(chapter: IRNode) -> IRNode:
    """Drop trailing placeholder notice/omission children from a chapter tail.

    Some base sources encode a chapter gap as a fake final subsection like
    ``Puuttuu luvut 7-11`` followed by an omission marker inside the preceding
    section. Once we seed real chapters into that gap, the placeholder tail
    should disappear.
    """
    section_children = [child for child in chapter.children if child.kind == IRNodeKind.SECTION]
    if not section_children:
        return chapter
    last_section = section_children[-1]
    new_sec_children = list(last_section.children)
    changed = False
    while new_sec_children and new_sec_children[-1].kind == IRNodeKind.OMISSION:
        new_sec_children.pop()
        changed = True
    if new_sec_children:
        trailing_text = " ".join(irnode_to_text(new_sec_children[-1]).split())
        if _MISSING_CHAPTER_SPAN_RE.search(trailing_text):
            new_sec_children.pop()
            changed = True
            while new_sec_children and new_sec_children[-1].kind == IRNodeKind.OMISSION:
                new_sec_children.pop()
    if not changed:
        return chapter
    new_last_section = _tops._with_children(last_section, new_sec_children)
    chapter_children = list(chapter.children)
    chapter_children[len(chapter_children) - 1 - list(reversed(chapter.children)).index(last_section)] = new_last_section
    return _tops._with_children(chapter, chapter_children)


def _chapters_in_gap(
    seedable: Dict[str, Tuple[str, IRNode]],
    prev_label: Optional[str],
    next_label: Optional[str],
) -> List[str]:
    """Return seedable chapter labels that sort between prev and next."""
    prev_key = default_label_sort_key(prev_label) if prev_label else (-1, '', 0)
    # Use a very large upper bound if there's no next chapter
    next_key = default_label_sort_key(next_label) if next_label else (999999, '', 0)
    return [
        label for label in seedable
        if prev_key < default_label_sort_key(label) < next_key
    ]


def _rebuild_at_path(
    tree: IRNode,
    path: List[Tuple[str, str]],
    replacement: IRNode,
) -> IRNode:
    """Rebuild the tree with a replacement node at the given path.

    If path is empty, returns the replacement directly.
    """
    if not path:
        return replacement
    kind, label = path[0]
    new_children = []
    for child in tree.children:
        if _tops._kind_str(child.kind) == kind and (child.label or "") == label:
            new_children.append(_rebuild_at_path(child, path[1:], replacement))
        else:
            new_children.append(child)
    return _tops._with_children(tree, new_children)


def _op_targets_chapter(op: AmendmentOp, chapter_labels: Set[str]) -> bool:
    """Return True if this op targets a chapter in the given label set.

    An op targets a seeded chapter if:
    - It is a chapter-level op (target_kind == `TargetKind.CHAPTER`) whose target_section
      matches a seeded chapter label, OR
    - It is a section-level op (target_kind == `TargetKind.SECTION`) scoped to a seeded
      chapter via target_chapter.
    """
    if op.target_unit_kind == "chapter" and op.target_section in chapter_labels:
        return True
    if op.target_unit_kind == "section" and op.target_chapter in chapter_labels:
        return True
    return False


# ---------------------------------------------------------------------------
# Main seeding function
# ---------------------------------------------------------------------------

def seed_missing_chapters(
    ir: IRNode,
    muutoslait: List[str],
    corpus_store: CorpusStore,
    diagnostics_out: Optional[List[ChapterSeedDiagnostic]] = None,
) -> Tuple[IRNode, Set[Tuple[str, str]]]:
    """Seed missing chapters from amendment bodies for partial-base statutes.

    Some pre-1900 statutes have partial base XML where entire chapters are
    omission placeholders.  When amendments target these missing chapters,
    REPLACE ops fail and cascade into hundreds of missing sections.

    This function runs AFTER base loading but BEFORE the amendment loop.
    It scans amendment bodies for ``<chapter>`` elements whose labels don't
    exist in the base, parses them into IRNodes, and inserts them at the
    correct sorted position — replacing the omission placeholders.

    Pure: returns (new_ir, seeded_set).  The caller must use the returned
    IRNode; the input is never mutated.

    Returns (updated_ir, seeded_set) where seeded_set is a set of
    ``(chapter_label, amendment_id)`` pairs.  The caller should skip the
    seeding amendment's chapter-scoped ops for that chapter to avoid
    double-application.
    """
    # Step 1: Find containers that have chapter children. Missing chapter spans
    # can be encoded either as sibling omissions or as textual sentinels like
    # "Puuttuu luvut 7-11" in the tail of the preceding section.
    containers = _find_chapter_containers(ir)
    if not containers:
        return ir, set()

    # Step 2: Collect all chapter labels present in the base.
    base_chapter_labels: Set[str] = set()
    for _path, container in containers:
        for ch in container.children:
            if ch.kind == IRNodeKind.CHAPTER and ch.label is not None:
                base_chapter_labels.add(ch.label)

    if DEBUG:
        replay_print(f"  SEED: base chapters = {sorted(base_chapter_labels, key=_chapter_sort_key)}")

    # Step 3: Scan amendments chronologically for chapters not in the base.
    # Map: chapter_label → (amendment_id, IRNode)
    seedable: Dict[str, Tuple[str, IRNode]] = {}
    for amendment_id in muutoslait:
        xml_bytes = corpus_store.read_source(amendment_id)
        if xml_bytes is None:
            _record_chapter_seed_diagnostic(
                diagnostics_out,
                rule_id="fi_chapter_seed_source_missing",
                family="source_pathology",
                phase="acquisition",
                reason="Finland chapter seeding skipped amendment because source XML was unavailable",
                source_statute=amendment_id,
            )
            continue
        try:
            root = etree.fromstring(xml_bytes)
        except etree.XMLSyntaxError:
            _record_chapter_seed_diagnostic(
                diagnostics_out,
                rule_id="fi_chapter_seed_source_xml_parse_failed",
                family="source_pathology",
                phase="acquisition",
                reason="Finland chapter seeding skipped amendment because source XML could not be parsed",
                source_statute=amendment_id,
            )
            continue
        ns = '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}'
        for ch_el in root.findall(f'.//{ns}chapter'):
            num_el = ch_el.find(f'{ns}num')
            if num_el is None or not num_el.text:
                continue
            raw_label = num_el.text.strip()
            norm = re.sub(r'[^\d\w]', '', raw_label).lower()
            label = _fi_label_postprocessor('chapter', norm)
            if not label:
                continue
            if label in base_chapter_labels:
                continue  # already in base
            if label in seedable:
                continue  # already found a seed (first chronological wins)
            # Parse the chapter element into an IRNode
            ch_ir = xml_to_ir_node(ch_el, _fi_label_postprocessor)
            seedable[label] = (amendment_id, ch_ir)

    if not seedable:
        return ir, set()

    # Step 4: Insert seeded chapters into the container, replacing omissions.
    # Strategy: for each container that has omissions, rebuild its children
    # list by inserting seeded chapters at sorted positions and removing
    # omission nodes that are now covered.
    seeded_set: Set[Tuple[str, str]] = set()
    current_ir = ir
    for container_path, container in containers:
        new_children: List[IRNode] = []
        container_changed = False
        for child in container.children:
            if child.kind == IRNodeKind.OMISSION:
                # This omission may represent one or more missing chapters.
                # Determine which missing chapters fall between the preceding
                # and following chapter labels.
                prev_label = _last_chapter_label(new_children)
                next_label = _next_chapter_label(child, container.children)
                # Find seedable chapters that belong in this gap.
                gap_seeds = _chapters_in_gap(seedable, prev_label, next_label)
                if gap_seeds:
                    # Insert the seeded chapters in sorted order, drop the omission.
                    for label in sorted(gap_seeds, key=_chapter_sort_key):
                        amendment_id, ch_ir = seedable[label]
                        new_children.append(ch_ir)
                        seeded_set.add((label, amendment_id))
                        _record_chapter_seed_diagnostic(
                            diagnostics_out,
                            rule_id="fi_chapter_seed_inserted_from_amendment_body",
                            family="ontology_normalization",
                            phase="payload_normalization",
                            reason="Finland chapter seeding inserted a missing chapter from an amendment body before replay",
                            source_statute=amendment_id,
                            chapter_label=label,
                            blocking=False,
                            quirks_disposition="apply",
                        )
                    container_changed = True
                else:
                    # No seeds found for this gap — keep the omission.
                    new_children.append(child)
            elif child.kind == IRNodeKind.CHAPTER and child.label is not None:
                gap_seeds: List[str] = []
                next_label = _next_chapter_label(child, container.children)
                notice_span = _chapter_missing_span_notice(child)
                chapter_node = child
                if next_label is not None and notice_span is not None:
                    gap_labels = _chapters_in_gap(seedable, child.label, next_label)
                    gap_seeds = _labels_in_missing_span(
                        gap_labels,
                        start_label=notice_span[0],
                        end_label=notice_span[1],
                    )
                    if gap_seeds:
                        stripped = _strip_trailing_missing_span_notice(child)
                        if stripped is not child:
                            chapter_node = stripped
                            container_changed = True
                new_children.append(chapter_node)
                for label in sorted(gap_seeds, key=_chapter_sort_key):
                    amendment_id, ch_ir = seedable[label]
                    new_children.append(ch_ir)
                    seeded_set.add((label, amendment_id))
                    _record_chapter_seed_diagnostic(
                        diagnostics_out,
                        rule_id="fi_chapter_seed_inserted_from_amendment_body",
                        family="ontology_normalization",
                        phase="payload_normalization",
                        reason="Finland chapter seeding inserted a missing chapter from an amendment body before replay",
                        source_statute=amendment_id,
                        chapter_label=label,
                        blocking=False,
                        quirks_disposition="apply",
                    )
                    container_changed = True
            else:
                new_children.append(child)

        if container_changed or new_children != list(container.children):
            # Rebuild the tree along the path from root to this container.
            new_container = _tops._with_children(container, new_children)
            current_ir = _rebuild_at_path(current_ir, container_path, new_container)

    if seeded_set:
        labels_str = ", ".join(
            f"ch{l} (from {m})" for l, m in sorted(seeded_set, key=lambda x: _chapter_sort_key(x[0]))
        )
        replay_print(f"  SEED: inserted {len(seeded_set)} chapter(s): {labels_str}")

    return current_ir, seeded_set
