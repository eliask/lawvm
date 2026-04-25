from __future__ import annotations

import re

from lawvm.semantic.align import align_semantic_facets, align_semantic_trees
from lawvm.semantic.model import (
    SemanticDiffEvent,
    SemanticDiffResult,
    SemanticDiffStats,
    SemanticPath,
    SemanticStructureNode,
    _node_wording_facet,
    is_semantic_facet_kind,
)

_HEADING_ATTRIBUTION_RE = re.compile(r"\s*\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)\s*$")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D\u00AD]")
_ORDINAL_LABEL_RE = re.compile(r"^\d+[a-zäöå]?$", re.IGNORECASE)


def _normalize_heading_for_diff(text: str) -> str:
    """Strip trailing period and Finlex attribution suffix for heading comparison.

    Also normalizes Unicode dash variants (em-dash, en-dash, etc.) to ASCII hyphen,
    consistent with _normalize_wording_for_diff. Year-range headings like
    "2012—2016" vs "2012–2016" differ only in dash variant and should compare equal.
    """
    text = _HEADING_ATTRIBUTION_RE.sub("", text)
    text = _DASH_VARIANTS_RE.sub("-", text)
    return text.rstrip(". ")


def _normalize_wording_for_diff(text: str) -> str:
    """Normalize encoding artifacts for wording comparison.

    Normalizes:
    - Unicode dash variants → ASCII hyphen (em-dash, en-dash, etc. are equivalent)
    - Space around § signs (5§:ssä vs 5 §:ssä)
    - Quote mark variants (curly quotes → ASCII quotes)
    - Trailing whitespace before punctuation (artifact of some serializers)
    - Space before/after hyphens in compounds (EU -asianajaja → EU-asianajaja)
    - Line-break hyphenation artifacts (jalostuskel-poisiksi → jalostuskelpoisiksi).
      Also collapses Finnish compound-word hyphen variants (vuokra-alue = vuokraalue).
      Safe: coordination hyphens (myynti- tai) have adjacent spaces and don't match.
    - Trailing Finlex amendment attribution suffixes like ``(9.7.1982/540)``

    Preserves verbatim text in emitted diff events.
    """
    text = _DASH_VARIANTS_RE.sub("-", text)
    text = re.sub(r"\s*§\s*", " § ", text)
    text = re.sub(r'[\u201c\u201d\u201e\u201f\u2033\u2036]', '"', text)  # curly/fancy → ASCII "
    text = re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035]", "'", text)  # curly single → ASCII '
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    text = re.sub(r"(\w)\s+-(\w)", r"\1-\2", text)  # "EU -asianajaja" → "EU-asianajaja"
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)  # "2- kohdassa" → "2-kohdassa"
    text = re.sub(r"(\w)-(\w)", r"\1\2", text)
    text = _HEADING_ATTRIBUTION_RE.sub("", text)
    text = text.rstrip(". ")  # trailing period presence varies between sources
    return text.strip()


_REPEAL_LABEL_BASES = frozenset({"editorial_repeal_notice", "repeal_placeholder"})


def _is_repeal_indicator(node: SemanticStructureNode) -> bool:
    """True when a node signals repeal — either editorial kumottu or LawVM placeholder."""
    return node.label_basis in _REPEAL_LABEL_BASES


def _is_editorial_or_empty_shell(node: SemanticStructureNode) -> bool:
    """True when a node is editorial noise or an empty shell (expired temp law).

    A node qualifies if:
    - It has no text, no facets with text, and no children with text (empty shell), OR
    - It is itself an editorial_repeal_notice or repeal_placeholder, OR
    - All of its children are editorial/empty shells (parent of editorial-only subtree)
    """
    if node.label_basis in _REPEAL_LABEL_BASES:
        return True
    has_own_text = bool(node.text) or any(f.text for f in node.facets)
    if has_own_text:
        return False
    if not node.children:
        return True  # no text, no children = empty shell
    return all(_is_editorial_or_empty_shell(c) for c in node.children)


def semantic_diff_stats(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
) -> SemanticDiffStats:
    structural = 0
    label = 0
    text = 0
    editorial = 0

    def visit(node) -> None:
        nonlocal structural, label, text, editorial
        if node is None:
            return
        lhs = node.left
        rhs = node.right
        if lhs is None or rhs is None:
            present = lhs if lhs is not None else rhs
            if present is not None and is_semantic_facet_kind(present.kind):
                text += 1
            elif present is not None and _is_repeal_indicator(present):
                editorial += 1
                return
            elif present is not None and _is_editorial_or_empty_shell(present):
                editorial += 1
                return
            else:
                structural += 1
            return
        # Both sides present: if both confirm repeal, skip entirely
        if _is_repeal_indicator(lhs) or _is_repeal_indicator(rhs):
            # At least one side says "repealed". If the other side also says
            # repealed or is an empty shell, this is a confirmed repeal —
            # presentation differs but semantic state agrees.
            if _is_repeal_indicator(lhs) and (_is_repeal_indicator(rhs) or _is_editorial_or_empty_shell(rhs)):
                editorial += 1
                return
            if _is_repeal_indicator(rhs) and (_is_repeal_indicator(lhs) or _is_editorial_or_empty_shell(lhs)):
                editorial += 1
                return
            # One side repealed, other has real content — genuine disagreement
        if lhs.kind != rhs.kind:
            structural += 1
        elif (lhs.label != rhs.label and lhs.display_badge() != rhs.display_badge()) or (
            lhs.label == rhs.label and lhs.visible_label != rhs.visible_label
        ):
            label += 1
        for left_facet, right_facet, _ in align_semantic_facets(lhs, rhs):
            if left_facet is None or right_facet is None:
                text += 1
                continue
            lt = left_facet.text
            rt = right_facet.text
            if left_facet.kind == "heading":
                lt = _normalize_heading_for_diff(lt)
                rt = _normalize_heading_for_diff(rt)
            else:
                lt = _normalize_wording_for_diff(lt)
                rt = _normalize_wording_for_diff(rt)
            if lt != rt:
                text += 1
        left_wording = _node_wording_facet(lhs)
        right_wording = _node_wording_facet(rhs)
        if (left_wording is None) != (right_wording is None):
            text += 1
        elif (
            left_wording is not None
            and right_wording is not None
            and _normalize_wording_for_diff(left_wording.text)
            != _normalize_wording_for_diff(right_wording.text)
        ):
            text += 1
        for child in node.children:
            if is_semantic_facet_kind(child.kind()):
                continue
            visit(child)

    visit(align_semantic_trees(left, right))
    return SemanticDiffStats(structural=structural, label=label, text=text, editorial=editorial)


def semantic_diff_kind(stats: SemanticDiffStats) -> str:
    if stats.structural == 0 and stats.label == 0 and stats.text == 0:
        if stats.editorial > 0:
            return "editorial_only"
        return "identical"
    if stats.structural == 0 and stats.label == 0 and stats.text > 0:
        return "text_only"
    if stats.structural == 0 and stats.label > 0 and stats.text == 0:
        return "label_only"
    if stats.structural == 0 and stats.label > 0 and stats.text > 0:
        return "label_and_text"
    if stats.structural > 0 and stats.text == 0 and stats.label == 0:
        return "structure_only"
    return "structure_and_text"


def semantic_diff_summary(stats: SemanticDiffStats) -> str:
    kind = semantic_diff_kind(stats)
    if kind == "identical":
        return "Sama rakenne ja sanamuoto."
    if kind == "editorial_only":
        return "Toimituksellinen ero (kumottu/poistettu)."
    if kind == "text_only":
        return "Sama rakenne, eri sanamuoto."
    if kind == "label_only":
        return "Sama rakenne, eri tunnus."
    if kind == "label_and_text":
        return "Sama rakenne, eri tunnus ja sanamuoto."
    if kind == "structure_only":
        return "Rakenne eroaa."
    return "Rakenne ja sanamuoto eroavat."


def semantic_diff(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
) -> SemanticDiffResult:
    stats = semantic_diff_stats(left, right)
    return SemanticDiffResult(
        stats=stats,
        kind=semantic_diff_kind(stats),
        summary=semantic_diff_summary(stats),
    )


def semantic_diff_events(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
) -> tuple[SemanticDiffEvent, ...]:
    events: list[SemanticDiffEvent] = []

    def missing_event_kind(unit_kind: str, side: str) -> str:
        if is_semantic_facet_kind(unit_kind):
            return "facet_removed" if side == "right" else "facet_added"
        return "unit_missing_right" if side == "right" else "unit_missing_left"

    def text_event_kind(unit_kind: str) -> str:
        if unit_kind == "heading":
            return "heading_text_changed"
        if unit_kind == "intro":
            return "intro_text_changed"
        if unit_kind == "wrapUp":
            return "wrapup_text_changed"
        return "wording_text_changed"

    def visit(node, path: SemanticPath) -> None:
        if node is None:
            return
        unit_kind = node.kind()
        unit_label = node.label()
        badge_node = node.left if node.left is not None else node.right
        badge = badge_node.display_badge() if badge_node is not None else ""
        node_path = path.append(unit_kind, unit_label)

        if node.left is None and node.right is not None:
            if node.right.label_basis == "editorial_repeal_notice":
                event_kind = "editorial_repeal_notice"
            elif _is_editorial_or_empty_shell(node.right):
                event_kind = "empty_oracle_shell"
            else:
                event_kind = missing_event_kind(unit_kind, "left")
            events.append(
                SemanticDiffEvent(
                    kind=event_kind,
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    right_text=node.right.text,
                    right_badge=badge,
                )
            )
            return
        if node.right is None and node.left is not None:
            event_kind = (
                "editorial_repeal_notice"
                if node.left.label_basis == "editorial_repeal_notice"
                else missing_event_kind(unit_kind, "right")
            )
            events.append(
                SemanticDiffEvent(
                    kind=event_kind,
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    left_text=node.left.text,
                    left_badge=badge,
                )
            )
            return

        assert node.left is not None and node.right is not None
        # Both sides confirm repeal → editorial convention, not a real diff.
        # Covers: replay repeal_placeholder vs oracle editorial_repeal_notice,
        # and both-side editorial repeals.
        if _is_repeal_indicator(node.left) and (
            _is_repeal_indicator(node.right) or _is_editorial_or_empty_shell(node.right)
        ):
            events.append(
                SemanticDiffEvent(
                    kind="editorial_repeal_notice",
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    left_text=node.left.text,
                    right_text=node.right.text,
                    left_badge=badge,
                    right_badge=badge,
                )
            )
            return
        if _is_repeal_indicator(node.right) and _is_editorial_or_empty_shell(node.left):
            events.append(
                SemanticDiffEvent(
                    kind="editorial_repeal_notice",
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    left_text=node.left.text,
                    right_text=node.right.text,
                    left_badge=badge,
                    right_badge=badge,
                )
            )
            return
        if node.left.kind != node.right.kind:
            events.append(
                SemanticDiffEvent(
                    kind="unit_kind_changed",
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    left_text=node.left.text,
                    right_text=node.right.text,
                    left_badge=node.left.display_badge(),
                    right_badge=node.right.display_badge(),
                )
            )
        elif node.left.label != node.right.label:
            left_badge = node.left.display_badge()
            right_badge = node.right.display_badge()
            if left_badge != right_badge:
                events.append(
                    SemanticDiffEvent(
                        kind="canonical_label_changed",
                        semantic_path=node_path,
                        match_basis=node.match_basis,
                        unit_kind=unit_kind,
                        unit_label=unit_label,
                        left_text=node.left.text,
                        right_text=node.right.text,
                        left_badge=left_badge,
                        right_badge=right_badge,
                    )
                )
        elif node.left.visible_label != node.right.visible_label:
            events.append(
                SemanticDiffEvent(
                    kind="visible_label_changed",
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    left_text=node.left.text,
                    right_text=node.right.text,
                    left_badge=node.left.display_badge(),
                    right_badge=node.right.display_badge(),
                )
            )
        for left_facet, right_facet, facet_basis in align_semantic_facets(node.left, node.right):
            facet_kind = (
                left_facet.kind
                if left_facet is not None
                else right_facet.kind
                if right_facet is not None
                else ""
            )
            facet_path = node_path.append(facet_kind)
            if left_facet is None and right_facet is None:
                continue
            if left_facet is None and right_facet is not None:
                oracle_diagnosis = ""
                if facet_kind == "heading":
                    m = _HEADING_ATTRIBUTION_RE.search(right_facet.text)
                    if m:
                        raw = m.group(0).strip().strip("()")
                        oracle_diagnosis = f"replay_gap:amendment_heading:{raw}"
                events.append(
                    SemanticDiffEvent(
                        kind="facet_added",
                        semantic_path=facet_path,
                        match_basis=facet_basis,
                        unit_kind=facet_kind,
                        facet_kind=facet_kind,
                        right_text=right_facet.text,
                        right_badge=right_facet.display_badge(),
                        oracle_diagnosis=oracle_diagnosis,
                    )
                )
                continue
            if right_facet is None and left_facet is not None:
                events.append(
                    SemanticDiffEvent(
                        kind="facet_removed",
                        semantic_path=facet_path,
                        match_basis=facet_basis,
                        unit_kind=facet_kind,
                        facet_kind=facet_kind,
                        left_text=left_facet.text,
                        left_badge=left_facet.display_badge(),
                    )
                )
                continue
            assert left_facet is not None and right_facet is not None
            lt = left_facet.text
            rt = right_facet.text
            if facet_kind == "heading":
                lt = _normalize_heading_for_diff(lt)
                rt = _normalize_heading_for_diff(rt)
            else:
                lt = _normalize_wording_for_diff(lt)
                rt = _normalize_wording_for_diff(rt)
            if lt != rt:
                events.append(
                    SemanticDiffEvent(
                        kind=text_event_kind(facet_kind),
                        semantic_path=facet_path,
                        match_basis=facet_basis,
                        unit_kind=facet_kind,
                        facet_kind=facet_kind,
                        left_text=left_facet.text,
                        right_text=right_facet.text,
                        left_badge=left_facet.display_badge(),
                        right_badge=right_facet.display_badge(),
                    )
                )
        left_wording = _node_wording_facet(node.left)
        right_wording = _node_wording_facet(node.right)
        if (
            (left_wording is None) != (right_wording is None)
            or (
                left_wording is not None
                and right_wording is not None
                and _normalize_wording_for_diff(left_wording.text)
                != _normalize_wording_for_diff(right_wording.text)
            )
        ):
            events.append(
                SemanticDiffEvent(
                    kind=text_event_kind(unit_kind),
                    semantic_path=node_path,
                    match_basis=node.match_basis,
                    unit_kind=unit_kind,
                    unit_label=unit_label,
                    facet_kind="wording",
                    left_text=left_wording.text if left_wording is not None else "",
                    right_text=right_wording.text if right_wording is not None else "",
                    left_badge=node.left.display_badge(),
                    right_badge=node.right.display_badge(),
                )
            )
        for child in node.children:
            if is_semantic_facet_kind(child.kind()):
                continue
            visit(child, node_path)

    visit(align_semantic_trees(left, right), SemanticPath())
    return tuple(events)
