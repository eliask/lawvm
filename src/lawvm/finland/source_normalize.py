"""Source normalization phase for Finland IR.

This module implements the explicit source-normalization phase described in
PRO_RESPONSE_5_1.md (Query 10).  It sits between the raw XML parse
(``fi_xml_to_ir_node``) and body-pairing / payload elaboration.

Policy (Query 10):
  - ``raw_source`` (the XML bytes) is immutable and always preserved.
  - ``normalized_source`` is the derived artifact produced here.
  - Every correction emits a typed ``SourceNormalizationFact`` witness.
  - Only auto-correctable pathologies are fixed here (schema-invalid,
    impossible numbering, editorial contamination, monotonic local repair).
  - Unusual-but-valid structures pass through unchanged.

Currently handled corrections
-------------------------------
1. **TAG_RECLASSIFY / IMPOSSIBLE_NUMBERING** -- a ``<subsection>`` element
   carrying an item-style ``<num>`` (e.g. ``9)``) and direct ``<paragraph>``
   children with letter labels (``a)``, ``b)``) is a mislabelled kohta.
   The node kind is changed from SUBSECTION to PARAGRAPH, and letter-labeled
   paragraph children are demoted to SUBPARAGRAPH (alakohta).

1b. **SUSPICIOUS_SHAPE / PROFILE_INVALID** -- a section-scoped item-style
    ``<subsection num="9)">`` is preserved as a subsection container rather
    than reclassified into paragraph, and a typed witness is emitted. This
    keeps the suspicious source shape visible while avoiding an illegal
    ``section -> paragraph`` edge.

2. **EDITORIAL_STRIP / EDITORIAL_CONTAMINATION** -- ``<block name="image">``
   nodes, ``<block name="note">``, ``<block name="footnote">``, and
   ``<authorialNote>`` (represented as ``IRNode(kind=BLOCK, ...)`` or
   ``IRNode(kind=HCONTAINER, attrs={"name": "authorialNote"})`` by the raw
   parser) carry no legal text.  They are removed from the tree and recorded
   as editorial strips.

3. **WHITESPACE / MONOTONIC_LOCAL_REPAIR** -- collapse multiple
   whitespace/newline runs in ``IRNode.text`` to single spaces and strip
   leading/trailing whitespace.  Only emits a fact when text actually changed.
   Conservative: only applies to ``.text`` content, not labels or attributes.

4. **NUMBERING_REPAIR / MONOTONIC_LOCAL_REPAIR** -- detect non-monotonic
   numbering among sibling items (kohta/paragraph).  For duplicates: emit a
   ``DUPLICATE_DROP`` fact and keep the first occurrence.  For gaps: emit a
   ``NUMBERING_REPAIR`` fact as a downstream witness without renumbering.

5. **CROSS_HEADING_HOIST / MONOTONIC_LOCAL_REPAIR** -- attach standalone
   ``crossHeading`` siblings to the following structural node when that node
   lacks its own heading.  This preserves source headings as real structural
   facets instead of leaving them stranded as flat siblings.

6. **Post-list split / MONOTONIC_LOCAL_REPAIR** -- in non-penal provisions,
   a duplicated trailing numbered paragraph that reads like concluding prose
   is lifted into a new sibling subsection instead of being treated as a
   shared wrap-up.

7. **Shape-driven sparse repairs** -- specific malformed sparse payload
   shapes are rewritten when they match their generic structure, with a
   typed ``SourceNormalizationFact`` emitted for each correction.

8. **NUMBERING_REPAIR / MONOTONIC_LOCAL_REPAIR** -- split a malformed
   paragraph whose direct ``subparagraph`` run contains a later digit reset
   (for example ``a)``, ``b)``, ..., ``5)``, ``a)``, ``b)``).  The digit
   reset becomes a new sibling paragraph and following lettered children are
   carried under it.

9. **UNNUMBERED_PEER_REPARENT / PROFILE_INVALID** -- an unnumbered paragraph
   peer of numbered kohdat carrying a subparagraph list (``sub_clause_with_list``
   sub-type) is reparented under the preceding numbered kohta.  Sub-case A
   (preceding kohta has no own subparagraphs): subparagraphs are attached
   directly as kohta children.  Sub-case B (preceding kohta already has
   subparagraphs): a ``WRAP_UP`` continuation facet is appended.  This pass
   runs BEFORE the numbering-anomaly dedup so the peer is still present.

All corrections are applied in a single recursive tree walk so that a
statute with multiple pathological nodes produces one fact per corrected node.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import default_label_sort_key
from lawvm.core.semantic_types import (
    IRNodeKind,
    SourceNormalizationBasis,
    SourceNormalizationFact,
    SourceNormalizationKind,
)
from lawvm.finland.helpers import _norm_num_token, may_attach_post_list_loppukappale
from lawvm.xml_ingest import _paragraph_is_content_only
from lawvm.finland.source_normalization_kinds import (
    BASE_DIGIT_RESET_SPLIT,
    BASE_INTRO_LIST_RESTART_SPLIT,
    BASE_DUPLICATE_SIBLING_DROP,
    BASE_DUPLICATE_TAIL_SPLIT,
    BASE_NUM_IN_INTRO_MISMATCH,
    BASE_NUM_IN_INTRO_RECOVERED,
    BASE_TAIL_PROSE_ABSORB,
    TRAILING_CHAPTER_REPARENT,
    UNNUMBERED_PEER_REPARENT,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ITEM_NUM_RE = re.compile(r"^\d+[a-z]?\)$")
_LETTER_LABEL_RE = re.compile(r"^[a-z]$")
_ARABIC_LABEL_RE = re.compile(r"^\d+[a-z]?$")


def _node_path_label(node: IRNode) -> str:
    """Return a single-segment path label for a node, e.g. ``"subsection:1"``."""
    kind_str = str(node.kind)
    label_str = str(node.label) if node.label is not None else "?"
    return f"{kind_str}:{label_str}"


def _is_item_style_subsection(node: IRNode) -> bool:
    """Return True when a SUBSECTION node has an item-style num AND letter-labeled paragraphs.

    This detects the Finlex source pathology where a kohta (paragraph/item) is
    encoded as a <subsection> with a "9)" style num and direct <paragraph>
    children carrying letter-only labels like "a)", "b)".  It is an
    IMPOSSIBLE_NUMBERING pathology: real Finnish subsections (momentti) never
    carry letter-labeled paragraphs at the direct child level.
    """
    if node.kind != IRNodeKind.SUBSECTION:
        return False
    # Find a NUM child with item-style text
    num_child = next((c for c in node.children if c.kind == IRNodeKind.NUM), None)
    if num_child is None:
        return False
    raw_num = (num_child.text or "").strip()
    if not _ITEM_NUM_RE.match(raw_num):
        return False
    # Confirm at least one direct PARAGRAPH child with a letter-only label
    return any(
        c.kind == IRNodeKind.PARAGRAPH
        and c.label is not None
        and _LETTER_LABEL_RE.match(c.label)
        for c in node.children
    )


def _reclassify_item_style_subsection(
    node: IRNode,
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> IRNode:
    """Reclassify a mislabelled subsection->paragraph, emitting a witness fact."""
    parent_kind = ""
    if parent_path:
        parent_kind = parent_path[-1].split(":", 1)[0]
    if parent_kind == "section":
        # Section-scoped item-style subsections are already legal structural
        # containers in the Finland tree.  Reclassifying them to PARAGRAPH would
        # manufacture an illegal section -> paragraph nesting edge, so keep the
        # original subsection shape in that context.
        num_child = next((c for c in node.children if c.kind == IRNodeKind.NUM), None)
        raw_num = (num_child.text or "").strip() if num_child else "?"
        node_path = parent_path + (_node_path_label(node),)
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=SourceNormalizationKind.SUSPICIOUS_SHAPE,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=f"section-scoped subsection with item-style num {raw_num!r}",
                after="kept as subsection to avoid illegal section -> paragraph edge",
                explanation=(
                    "A section-scoped item-style subsection is a suspicious source shape, "
                    "but reclassifying it to PARAGRAPH would manufacture an illegal "
                    "section -> paragraph nesting edge. Preserve the subsection and emit "
                    "a typed witness instead."
                ),
                path=node_path,
                confidence=0.93,
            )
        )
        return node

    num_child = next((c for c in node.children if c.kind == IRNodeKind.NUM), None)
    raw_num = (num_child.text or "").strip() if num_child else "?"
    node_path = parent_path + (_node_path_label(node),)

    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=SourceNormalizationKind.TAG_RECLASSIFY,
            basis=SourceNormalizationBasis.IMPOSSIBLE_NUMBERING,
            before=f"subsection with item-style num {raw_num!r}",
            after="paragraph (kohta) with subparagraph (alakohta) children",
            explanation=(
                "Finnish subsections (momentti) never carry letter-labeled paragraph "
                "children at the direct child level.  A <subsection> with an item-style "
                "num (e.g. '9)') and letter-labeled <paragraph> children is a mislabelled "
                "kohta; reclassified to PARAGRAPH with SUBPARAGRAPH children."
            ),
            path=node_path,
            confidence=0.97,
        )
    )

    # Reclassify direct letter-labeled PARAGRAPH children as SUBPARAGRAPH (alakohta)
    new_children: List[IRNode] = []
    for child in node.children:
        if (
            child.kind == IRNodeKind.PARAGRAPH
            and child.label is not None
            and _LETTER_LABEL_RE.match(child.label)
        ):
            new_children.append(
                IRNode(
                    kind=IRNodeKind.SUBPARAGRAPH,
                    label=child.label,
                    text=child.text,
                    attrs=child.attrs,
                    children=child.children,
                )
            )
        else:
            new_children.append(child)

    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=node.label,
        text=node.text,
        attrs=node.attrs,
        children=tuple(new_children),
    )


def _split_digit_reset_subparagraph_runs(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Split malformed paragraph-local subparagraph runs when numbering resets to a digit.

    Source pathology example:

      paragraph 4
        subparagraph a
        subparagraph b
        ...
        subparagraph 5
        subparagraph a
        subparagraph b

    The digit-labelled subparagraph is actually the start of a new paragraph 5,
    and the following letter-labelled subparagraphs belong under that new
    paragraph, not under paragraph 4.
    """

    def _is_letter_subparagraph(node: IRNode) -> bool:
        return bool(
            node.kind == IRNodeKind.SUBPARAGRAPH and bool(node.label) and _LETTER_LABEL_RE.match(str(node.label))
        )

    def _is_digit_subparagraph(node: IRNode) -> bool:
        raw_label = str(node.label or "").strip().rstrip(".)")
        return bool(
            node.kind == IRNodeKind.SUBPARAGRAPH
            and bool(node.label)
            and _ARABIC_LABEL_RE.match(raw_label)
        )

    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue

        sub_idx = [i for i, gc in enumerate(child.children) if gc.kind == IRNodeKind.SUBPARAGRAPH]
        if len(sub_idx) < 2:
            rewritten.append(child)
            continue

        split_at: int | None = None
        for idx, child_idx in enumerate(sub_idx):
            gc = child.children[child_idx]
            if not _is_digit_subparagraph(gc):
                continue
            prior = [child.children[j] for j in sub_idx[:idx]]
            trailing = [child.children[j] for j in sub_idx[idx + 1 :]]
            if not prior or not any(_is_letter_subparagraph(node) for node in prior):
                continue
            if not trailing or not all(_is_letter_subparagraph(node) for node in trailing):
                continue
            split_at = child_idx
            break

        if split_at is None:
            rewritten.append(child)
            continue

        reset_node = child.children[split_at]
        assert reset_node.label is not None
        new_label = str(reset_node.label).strip().rstrip(".)")
        reset_num = next((gc for gc in reset_node.children if gc.kind == IRNodeKind.NUM), None)
        reset_payload = tuple(gc for gc in reset_node.children if gc.kind != IRNodeKind.NUM)
        new_para_children: List[IRNode] = []
        if reset_num is not None:
            new_para_children.append(reset_num)
        new_para_children.extend(reset_payload)
        for gc in child.children[split_at + 1 :]:
            if gc.kind == IRNodeKind.SUBPARAGRAPH:
                new_para_children.append(gc)

        left_children = tuple(child.children[:split_at])
        new_para = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=new_label,
            text=reset_node.text,
            attrs=reset_node.attrs,
            children=tuple(new_para_children),
        )

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_DIGIT_RESET_SPLIT,
                basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                before=(
                    f"paragraph {child.label or '?'} contains digit-labelled subparagraph "
                    f"{reset_node.label or '?'} after lettered subparagraphs"
                ),
                after=f"split into sibling paragraph {new_label} with trailing lettered subparagraphs",
                explanation=(
                    "The source encoded a new numbered item inside a paragraph-local "
                    "subparagraph run. The digit reset starts a new sibling paragraph, "
                    "and subsequent lettered subparagraphs belong under that new item."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.96,
            )
        )

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=left_children,
            )
        )
        rewritten.append(new_para)

    return rewritten


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: image, note, footnote, authorialNote blocks
# ---------------------------------------------------------------------------

_EDITORIAL_BLOCK_NAMES = frozenset({"image", "note", "footnote"})


def _is_editorial_block(node: IRNode) -> bool:
    """Return True when a node is an editorial block that carries no legal text.

    Matches:
    - ``BLOCK`` with ``name`` in {image, note, footnote}
    - ``HCONTAINER`` with ``name`` == "authorialNote"
    """
    if node.kind == IRNodeKind.BLOCK and node.attrs.get("name") in _EDITORIAL_BLOCK_NAMES:
        return True
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") == "authorialNote":
        return True
    return False


def _strip_editorial_blocks(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Remove editorial block children, emitting an EDITORIAL_STRIP fact per removal.

    Strips BLOCK(name=image), BLOCK(name=note), BLOCK(name=footnote), and
    HCONTAINER(name=authorialNote).  These carry editorial apparatus, not
    enacted law text.
    """
    kept: List[IRNode] = []
    for child in children:
        if _is_editorial_block(child):
            block_name = child.attrs.get("name", "unknown")
            facts.append(
                SourceNormalizationFact(
                    statute_id=statute_id,
                    kind=SourceNormalizationKind.EDITORIAL_STRIP,
                    basis=SourceNormalizationBasis.EDITORIAL_CONTAMINATION,
                    before=f"{child.kind} name='{block_name}' at path {'/'.join(parent_path) or '(root)'}",
                    after="(removed)",
                    explanation=(
                        f"<{child.kind} name='{block_name}'> nodes carry editorial apparatus "
                        f"(images, notes, footnotes, authorial annotations), not enacted law "
                        f"text.  Removed during source normalization."
                    ),
                    path=parent_path,
                    confidence=1.0,
                )
            )
        else:
            kept.append(child)
    return kept


# ---------------------------------------------------------------------------
# WHITESPACE normalization
# ---------------------------------------------------------------------------

_MULTI_WS_RE = re.compile(r"\s+")


def _normalize_whitespace(
    node: IRNode,
    statute_id: str,
    node_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> IRNode:
    """Collapse multi-whitespace runs in text content to single spaces.

    Conservative: only normalizes ``IRNode.text``, not labels or attribute
    values.  Only emits a fact and returns a new node when text actually
    changed.
    """
    if not node.text:
        return node

    normalized_text = _MULTI_WS_RE.sub(" ", node.text).strip()
    if normalized_text == node.text:
        return node

    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=SourceNormalizationKind.WHITESPACE,
            basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
            before=repr(node.text[:80]) if len(node.text) > 80 else repr(node.text),
            after=repr(normalized_text[:80]) if len(normalized_text) > 80 else repr(normalized_text),
            explanation="Collapsed multi-whitespace/newline runs in text content to single space.",
            path=node_path,
            confidence=1.0,
        )
    )

    return IRNode(
        kind=node.kind,
        label=node.label,
        text=normalized_text,
        attrs=node.attrs,
        children=node.children,
    )


def _node_has_heading_child(node: IRNode) -> bool:
    return any(child.kind == IRNodeKind.HEADING for child in node.children)


def _attach_heading_child(node: IRNode, heading_text: str, heading_attrs: dict[str, object]) -> IRNode:
    heading = IRNode(kind=IRNodeKind.HEADING, text=heading_text, attrs=heading_attrs)
    new_children: List[IRNode] = []
    inserted = False
    for child in node.children:
        if not inserted and child.kind != IRNodeKind.NUM:
            new_children.append(heading)
            inserted = True
        new_children.append(child)
    if not inserted:
        new_children.insert(0, heading)
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=node.attrs,
        children=tuple(new_children),
    )


def _hoist_cross_heading_siblings(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Attach standalone crossHeading siblings to the following structural node."""
    hoistable_kinds = {IRNodeKind.SECTION, IRNodeKind.CHAPTER, IRNodeKind.PART}
    new_children: List[IRNode] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind == IRNodeKind.CROSS_HEADING and i + 1 < len(children):
            target = children[i + 1]
            if target.kind in hoistable_kinds and child.text and not _node_has_heading_child(target):
                new_target = _attach_heading_child(target, child.text, dict(child.attrs))
                facts.append(
                    SourceNormalizationFact(
                        statute_id=statute_id,
                        kind=SourceNormalizationKind.CROSS_HEADING_HOIST,
                        basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                        before=f"crossHeading sibling {child.text!r} before {_node_path_label(target)}",
                        after=f"heading attached to {_node_path_label(target)}",
                        explanation=(
                            "Source encodes the section/chapter heading as a standalone crossHeading "
                            "sibling. Hoist it into the following structural node so the IR preserves "
                            "the heading as a real structural facet instead of a flat sibling."
                        ),
                        path=parent_path + (_node_path_label(target),),
                        confidence=0.98,
                    )
                )
                new_children.append(new_target)
                i += 2
                continue
        new_children.append(child)
        i += 1
    return new_children


def _reparent_trailing_chapters_into_preceding_part(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Reparent trailing body-scope chapters under the immediately preceding part."""
    if not any(child.kind == IRNodeKind.PART for child in children):
        return children

    rewritten: List[IRNode] = []
    current_part: IRNode | None = None
    current_part_index = -1
    last_chapter_key: tuple[int, str, int] | None = None

    for child in children:
        if child.kind == IRNodeKind.PART:
            if current_part is not None and current_part_index >= 0:
                rewritten[current_part_index] = current_part
            current_part = child
            current_part_index = len(rewritten)
            part_chapters = [gc for gc in child.children if gc.kind == IRNodeKind.CHAPTER and gc.label is not None]
            last_chapter_key = default_label_sort_key(part_chapters[-1].label) if part_chapters else None
            rewritten.append(child)
            continue

        if child.kind == IRNodeKind.CHAPTER and current_part is not None and child.label is not None:
            child_key = default_label_sort_key(child.label)
            if last_chapter_key is None or child_key > last_chapter_key:
                current_part = _tops._with_children(current_part, list(current_part.children) + [child])
                last_chapter_key = child_key
                facts.append(
                    SourceNormalizationFact(
                        statute_id=statute_id,
                        kind=TRAILING_CHAPTER_REPARENT,
                        basis=SourceNormalizationBasis.PROFILE_INVALID,
                        before=(
                            f"top-level chapter {child.label!r} followed part "
                            f"{current_part.label or '?'} as a root sibling"
                        ),
                        after=f"chapter {child.label!r} reparented under part {current_part.label or '?'}",
                        explanation=(
                            "The source closed a part container before a later chapter that "
                            "continues the same chapter sequence. Reparent the trailing "
                            "chapter under the immediately preceding part and keep an "
                            "explicit source-normalization witness."
                        ),
                        path=parent_path + (_node_path_label(child),),
                        confidence=0.97,
                    )
                )
                continue

        if current_part is not None and current_part_index >= 0:
            rewritten[current_part_index] = current_part
        current_part = None
        current_part_index = -1
        last_chapter_key = None
        rewritten.append(child)

    if current_part is not None and current_part_index >= 0:
        rewritten[current_part_index] = current_part

    return rewritten


def _split_intro_then_numbered_list_subsections(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Split malformed subsection pairs encoded as one intro-bearing subsection.

    Witness family:
    - subsection starts with a standalone sentence in INTRO
    - immediately followed by one content-only paragraph ending with ':'
    - followed by a fresh numbered list restart 1..N

    The standalone sentence is its own moment. The colon-ended paragraph is the
    intro of the following numbered-list moment.
    """
    rewritten: List[IRNode] = []
    changed = False

    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue

        semantic_children = [c for c in child.children if c.kind != IRNodeKind.NUM]
        if len(semantic_children) < 3 or semantic_children[0].kind != IRNodeKind.INTRO:
            rewritten.append(child)
            continue

        lead_para = semantic_children[1]
        if not (lead_para.kind == IRNodeKind.PARAGRAPH and _paragraph_is_content_only(lead_para)):
            rewritten.append(child)
            continue

        intro_text = (semantic_children[0].text or "").strip()
        lead_text = irnode_to_text(lead_para).strip()
        remaining = semantic_children[2:]
        if not intro_text or not lead_text:
            rewritten.append(child)
            continue
        if intro_text.endswith(":") or not intro_text.endswith((".", "!", "?")):
            rewritten.append(child)
            continue
        if not lead_text.endswith(":"):
            rewritten.append(child)
            continue
        if not remaining or not all(
            c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c) for c in remaining
        ):
            rewritten.append(child)
            continue
        if remaining[0].label != "1":
            rewritten.append(child)
            continue

        changed = True
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=(IRNode(kind=IRNodeKind.CONTENT, text=intro_text),),
            )
        )
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.INTRO, text=lead_text), *remaining),
            )
        )
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_INTRO_LIST_RESTART_SPLIT,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    "single subsection encoded as INTRO sentence + content-only paragraph "
                    "ending ':' + restarted numbered list"
                ),
                after=(
                    "split into standalone content-only subsection plus intro-bearing "
                    "numbered-list subsection"
                ),
                explanation=(
                    "The source encoded two legal moments inside one subsection. "
                    "The first sentence is a complete standalone moment, while the "
                    "following colon-ended paragraph introduces a fresh numbered list. "
                    "Split the malformed subsection at the moment boundary and keep "
                    "an explicit source-normalization witness."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.96,
            )
        )

    return rewritten if changed else children


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# NUMBERING witness: monotonic gap and duplicate detection
# ---------------------------------------------------------------------------

_NUMERIC_LABEL_RE = re.compile(r"^(\d+)")

# Node kinds that participate in sibling numbering sequences.
_NUMBERED_SIBLING_KINDS = frozenset({
    IRNodeKind.PARAGRAPH,
    IRNodeKind.SUBPARAGRAPH,
    IRNodeKind.ITEM,
    IRNodeKind.SUBSECTION,
})


def _detect_numbering_anomalies(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Detect non-monotonic numbering among sibling items.

    For each contiguous run of numbered siblings of the same kind:
    - **Duplicates**: emit a ``DUPLICATE_DROP`` fact and keep only the first
      occurrence.
    - **Gaps**: emit a ``NUMBERING_REPAIR`` fact as a witness but do NOT
      renumber (just record the gap for downstream).

    Returns the (possibly pruned) child list.
    """
    # Identify numbered children with normalized labels.
    #
    # Use the full normalized token for duplicate detection so legitimate
    # alphanumeric siblings like ``5a`` / ``5b`` are preserved.  Gap witnesses
    # still use the numeric prefix of the normalized token.
    labelled: list[tuple[int, IRNode, str, int]] = []  # (index, node, norm_label, numeric_label)
    current_is_subsection = bool(parent_path and parent_path[-1].startswith("subsection:"))
    current_subsection = (
        IRNode(kind=IRNodeKind.SUBSECTION, children=tuple(children))
        if current_is_subsection
        else None
    )
    for i, child in enumerate(children):
        if child.kind in _NUMBERED_SIBLING_KINDS and child.label is not None:
            norm_label = _norm_num_token(child.label)
            m = _NUMERIC_LABEL_RE.match(norm_label)
            if m:
                if (
                    current_subsection is not None
                    and child.kind == IRNodeKind.PARAGRAPH
                    and i == len(children) - 1
                    and len([c for c in children if c.kind == IRNodeKind.PARAGRAPH]) >= 2
                    and not may_attach_post_list_loppukappale(current_subsection)
                ):
                    prev_para = next(
                        (
                            prev
                            for prev in reversed(children[:i])
                            if prev.kind == IRNodeKind.PARAGRAPH and prev.label is not None
                        ),
                        None,
                    )
                    if prev_para is not None and _norm_num_token(str(prev_para.label or "")) == norm_label:
                        continue
                labelled.append((i, child, norm_label, int(m.group(1))))

    if len(labelled) < 2:
        return children

    # Check for duplicates
    seen_labels: dict[str, int] = {}  # normalized label -> first index
    duplicate_indices: set[int] = set()
    for idx, _node, norm_label, _num_label in labelled:
        if norm_label in seen_labels:
            duplicate_indices.add(idx)
            facts.append(
                SourceNormalizationFact(
                    statute_id=statute_id,
                    kind=BASE_DUPLICATE_SIBLING_DROP,
                    basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                    before=f"duplicate label {norm_label} at index {idx}",
                    after=f"(dropped, first occurrence at index {seen_labels[norm_label]})",
                    explanation=(
                        f"Sibling node with label {norm_label} appears more than once.  "
                        f"Keeping first occurrence at index {seen_labels[norm_label]}, "
                        f"dropping duplicate at index {idx}."
                    ),
                    path=parent_path,
                    confidence=0.95,
                )
            )
        else:
            seen_labels[norm_label] = idx

    # Check for gaps (after dedup)
    kept_labels = sorted(
        num_label for idx, _node, _norm_label, num_label in labelled if idx not in duplicate_indices
    )
    for i in range(1, len(kept_labels)):
        expected = kept_labels[i - 1] + 1
        actual = kept_labels[i]
        if actual != expected:
            facts.append(
                SourceNormalizationFact(
                    statute_id=statute_id,
                    kind=SourceNormalizationKind.NUMBERING_REPAIR,
                    basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                    before=f"labels {kept_labels[i-1]} -> {actual} (expected {expected})",
                    after="(gap recorded as witness, no renumbering applied)",
                    explanation=(
                        f"Non-monotonic numbering: after label {kept_labels[i-1]}, "
                        f"next label is {actual} (expected {expected}).  "
                        f"Gap recorded for downstream phases; no renumbering applied."
                    ),
                    path=parent_path,
                    confidence=1.0,
                )
            )

    # Drop duplicates from children list
    if duplicate_indices:
        return [child for i, child in enumerate(children) if i not in duplicate_indices]
    return children


def _split_nonpenal_trailing_duplicate_paragraph(
    node: IRNode,
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> IRNode:
    """Split a non-penal trailing duplicate paragraph into its own sibling subsection.

    Finnish drafting allows shared post-list continuation only in rangaistussäännös
    provisions.  When a non-penal subsection ends in a duplicated final numbered
    paragraph that reads like prose, lift that sentence into a fresh sibling
    subsection instead of keeping it attached to the list.
    """
    if node.kind != IRNodeKind.SECTION:
        return node

    semantic_children = [child for child in node.children if child.kind not in (IRNodeKind.NUM, IRNodeKind.HEADING)]
    if len(semantic_children) < 2:
        return node

    rebuilt_children: List[IRNode] = []
    changed = False

    for child in semantic_children:
        if child.kind != IRNodeKind.SUBSECTION:
            rebuilt_children.append(child)
            continue
        if may_attach_post_list_loppukappale(child):
            rebuilt_children.append(child)
            continue

        paragraph_children = [gc for gc in child.children if gc.kind == IRNodeKind.PARAGRAPH]
        if len(paragraph_children) < 2:
            rebuilt_children.append(child)
            continue

        tail_para = paragraph_children[-1]
        prev_para = paragraph_children[-2]
        if _norm_num_token(str(tail_para.label or "")) != _norm_num_token(str(prev_para.label or "")):
            rebuilt_children.append(child)
            continue
        if child.children[-1] is not tail_para:
            rebuilt_children.append(child)
            continue

        tail_text = irnode_to_text(tail_para).strip()
        if not tail_text:
            rebuilt_children.append(child)
            continue
        if tail_text[-1] not in ".;:!?":
            rebuilt_children.append(child)
            continue

        label_match = _NUMERIC_LABEL_RE.match(str(child.label or "").strip())
        if not label_match:
            rebuilt_children.append(child)
            continue
        next_label = str(int(label_match.group(1)) + 1)

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_DUPLICATE_TAIL_SPLIT,
                basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                before=(
                    f"subsection {child.label or '?'} ends with duplicated paragraph label "
                    f"{tail_para.label or '?'} carrying trailing prose"
                ),
                after=f"trailing prose lifted into new subsection {next_label}",
                explanation=(
                    "The source encoded a concluding prose sentence as a duplicated "
                    "final numbered paragraph in a non-penal provision.  Finnish "
                    "drafting rules allow shared post-list continuation only for "
                    "rangaistussäännös provisions, so the trailing sentence becomes "
                    "its own sibling moment."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.98,
            )
        )

        rewritten_child = IRNode(
            kind=child.kind,
            label=child.label,
            text=child.text,
            attrs=child.attrs,
            children=tuple(child.children[:-1]),
        )
        lifted_child = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=next_label,
            text="",
            attrs={},
            children=(IRNode(kind=IRNodeKind.CONTENT, text=tail_text),),
        )
        rebuilt_children.append(rewritten_child)
        rebuilt_children.append(lifted_child)
        changed = True

    if not changed:
        return node

    preserved_prefix = [child for child in node.children if child.kind in (IRNodeKind.NUM, IRNodeKind.HEADING)]
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=node.attrs,
        children=tuple(preserved_prefix + rebuilt_children),
    )


# ---------------------------------------------------------------------------
# Disabled sparse-tail repair notes
# ---------------------------------------------------------------------------
#
# The old statute-shaped sparse-tail rewrites were not universal and encoded a
# handful of specific source misfeatures as normalization authority. Keep the
# misfeatures documented in tests, but do not auto-repair them in live source
# normalization.


# ---------------------------------------------------------------------------
# UNNUMBERED_PEER_REPARENT: absorb sub_clause_with_list unnumbered peers
# ---------------------------------------------------------------------------
#
# Finnish source XML sometimes encodes the "continuation/exception half" of a
# numbered kohta as an unnumbered <paragraph> sibling of the numbered kohdat.
# This violates the Finnish profile ontology (there is no "unnumbered paragraph
# peer" law-point unit).  When the peer has subparagraph children (a/b/c), it
# is a structurally significant continuation that amendments may target.
#
# This pass runs BEFORE _detect_numbering_anomalies so the peer is still
# present when reparenting happens.  After reparenting the numbered sibling
# sequence is clean and the numbering check finds no duplicates.
#
# Two sub-cases:
#
#   Sub-case A — preceding kohta has NO own subparagraphs:
#     Reparent the peer's subparagraphs directly as children of the preceding
#     kohta.  The peer's intro text becomes a second INTRO child on the kohta.
#     Labels (a, b, c, ...) are preserved.  Amendment addresses like
#     "6 kohdan f alakohta" resolve to the reparented subparagraph.
#
#   Sub-case B — preceding kohta ALREADY has subparagraphs (a/b/c main list):
#     Reparenting the peer's a/b/c directly would create duplicate labels.
#     Instead, wrap the peer's intro + subparagraphs in a WRAP_UP node
#     (kind=WRAP_UP) appended after the main subparagraphs.  The WRAP_UP
#     carries:
#       - .text   = the peer's intro text (e.g. "kaatopaikkana ei kuitenkaan
#                   pidetä:")
#       - .attrs  = {"__continuation__": "1"} — opaque internal marker,
#                   syntactically impossible as a real Finnish legal label
#       - .children = the peer's SUBPARAGRAPH nodes
#     Labels inside the WRAP_UP are the peer's real Finnish labels (a, b, c)
#     but are scoped by the WRAP_UP — they do NOT join the parent's main label
#     space.  No synthetic public labels are produced.
#     irnode_to_text() will collect the WRAP_UP's text and its children's text,
#     so the continuation content appears in section-level text comparison.
#
# The BASE_UNNUMBERED_PARAGRAPH_PEER observation for T1b is emitted here via
# a SourceNormalizationFact(kind=UNNUMBERED_PEER_REPARENT).  statute.py's
# _collect_base_observations now runs on raw_ir (pre-normalization) so
# detect_unnumbered_paragraph_peers fires on the unmodified tree.


def _paragraph_has_subparagraph_children(para: IRNode) -> bool:
    """Return True when a PARAGRAPH node has at least one SUBPARAGRAPH child."""
    return any(c.kind == IRNodeKind.SUBPARAGRAPH for c in para.children)


def _paragraph_has_num_child(para: IRNode) -> bool:
    """Return True when a PARAGRAPH node has a NUM child (= numbered kohta)."""
    return any(c.kind == IRNodeKind.NUM for c in para.children)


def _paragraph_intro_text(para: IRNode) -> str:
    """Return the intro text from a PARAGRAPH node, or '' if absent."""
    intro = next((c for c in para.children if c.kind == IRNodeKind.INTRO), None)
    if intro is not None:
        return (intro.text or "").strip()
    return (para.text or "").strip()


def _reparent_sub_clause_with_list_peers(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Reparent unnumbered paragraph peers that carry subparagraph children.

    Detects unnumbered paragraphs that:
    1. Have SUBPARAGRAPH children (a/b/c style sublist), AND
    2. Have a preceding numbered PARAGRAPH sibling (= preceding kohta exists).

    Reparents the peer's content under the preceding numbered kohta.
    See module-level comment above for sub-case A vs B logic.

    Must run BEFORE _detect_numbering_anomalies so the unnumbered peer is
    still present.  After reparenting the peer is removed from the sibling
    list, and the numbered sequence is clean.
    """
    # Only applies when there is at least one numbered paragraph followed by
    # at least one unnumbered paragraph with subparagraph children.
    has_numbered = any(
        c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c)
        for c in children
    )
    if not has_numbered:
        return children

    # Scan for unnumbered-with-subparagraphs pattern
    # We need to track the last numbered paragraph seen.
    rewritten: List[IRNode] = []
    last_numbered_idx: int = -1
    last_numbered_para: Optional[IRNode] = None

    for i, child in enumerate(children):
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue

        if _paragraph_has_num_child(child) or child.attrs.get("__num_from_intro__"):
            # Normal numbered paragraph (or a paragraph whose number was encoded in
            # <intro> text and recovered by _apply_recover_intro_labeled_paragraphs
            # during the xml parse phase — it carries __num_from_intro__="1").
            # Such paragraphs are genuine numbered kohdat; they must NOT be treated
            # as unnumbered continuation peers.
            rewritten.append(child)
            last_numbered_idx = len(rewritten) - 1
            last_numbered_para = child
            continue

        # Unnumbered paragraph: check if it has subparagraph children
        if not _paragraph_has_subparagraph_children(child):
            # Not a sub_clause_with_list — leave untouched
            rewritten.append(child)
            continue

        # It's an unnumbered paragraph with subparagraph children.
        # Check if there's a preceding numbered kohta to reparent under.
        if last_numbered_para is None:
            # No preceding numbered kohta — can't reparent
            rewritten.append(child)
            continue

        # Reparenting is possible.
        peer_intro = _paragraph_intro_text(child)
        peer_subparas = [c for c in child.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        peer_eId = child.attrs.get("eId", "")

        # Determine sub-case based on whether preceding kohta has own subparagraphs
        kohta_has_own_subparas = _paragraph_has_subparagraph_children(last_numbered_para)

        if not kohta_has_own_subparas:
            # Sub-case A: reparent subparagraphs directly under preceding kohta
            # The peer's intro becomes a second INTRO child on the kohta.
            new_kohta_children = list(last_numbered_para.children)
            if peer_intro:
                new_kohta_children.append(
                    IRNode(kind=IRNodeKind.INTRO, text=peer_intro)
                )
            new_kohta_children.extend(peer_subparas)
            new_kohta = IRNode(
                kind=last_numbered_para.kind,
                label=last_numbered_para.label,
                text=last_numbered_para.text,
                attrs=last_numbered_para.attrs,
                children=tuple(new_kohta_children),
            )
            reparent_kind = "sub_case_A"
        else:
            # Sub-case B: preceding kohta already has subparagraphs.
            # Wrap the peer's content in a WRAP_UP node with opaque internal marker.
            continuation_node = IRNode(
                kind=IRNodeKind.WRAP_UP,
                text=peer_intro,
                attrs={"__continuation__": "1"},
                children=tuple(peer_subparas),
            )
            new_kohta_children = list(last_numbered_para.children) + [continuation_node]
            new_kohta = IRNode(
                kind=last_numbered_para.kind,
                label=last_numbered_para.label,
                text=last_numbered_para.text,
                attrs=last_numbered_para.attrs,
                children=tuple(new_kohta_children),
            )
            reparent_kind = "sub_case_B"

        # Replace the preceding kohta in rewritten list with the updated version
        rewritten[last_numbered_idx] = new_kohta
        last_numbered_para = new_kohta  # update reference in case another peer follows

        # Emit a SourceNormalizationFact for this reparenting
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=UNNUMBERED_PEER_REPARENT,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"unnumbered paragraph peer (eId={peer_eId!r}, intro={peer_intro[:60]!r}) "
                    f"with {len(peer_subparas)} subparagraph(s) following "
                    f"numbered kohta {last_numbered_para.label!r}"
                ),
                after=(
                    f"{reparent_kind}: reparented {len(peer_subparas)} subparagraph(s) "
                    f"under preceding kohta {new_kohta.label!r}; peer removed from sibling list"
                ),
                explanation=(
                    "Finnish legal ontology (Lainkirjoittajan opas) disallows unnumbered "
                    "paragraph peers of numbered kohdat. When such a peer carries subparagraph "
                    "children (a/b/c), it is semantically a continuation/exception clause of the "
                    "preceding numbered kohta. Reparenting under the preceding kohta preserves "
                    "addressing of the subparagraphs and removes the ontology violation. "
                    f"({reparent_kind}: {'subparagraphs directly nested' if reparent_kind == 'sub_case_A' else 'continuation wrapped in WRAP_UP with opaque marker __continuation__=1'})"
                ),
                path=parent_path,
                confidence=0.97,
            )
        )
        # The peer itself is NOT appended to rewritten (it's absorbed)

    return rewritten


# ---------------------------------------------------------------------------
# BASE_TAIL_PROSE_ABSORB: absorb tail_prose unnumbered peers as wrapUp
# ---------------------------------------------------------------------------
#
# Finnish source XML sometimes contains unnumbered <paragraph> siblings that
# carry only plain text (no <subparagraph> children).  These are the
# ``tail_prose`` sub-type: a closing or qualifying sentence that follows a
# numbered kohta list — analogous to the penal ``loppukappale`` described in
# Lainkirjoittajan opas.
#
# No amendment in the corpus targets these fragments at sub-unit level, so
# absorption is cosmetic: the peer's text is folded into a WRAP_UP facet on
# the preceding numbered kohta and the peer is removed from the sibling list.
# The WRAP_UP carries the opaque internal marker ``__tail_prose__=1``, which
# is syntactically impossible as a real Finnish legal label.
#
# Skip conditions (peer is left untouched and the existing pathology path fires):
#   1. The peer has SUBPARAGRAPH children — handled by T4a.
#   2. The peer's content text starts with N)/N. prefix — likely num_in_intro
#      (T4c), skip to avoid double-handling.
#   3. No preceding numbered kohta exists — cannot absorb without anchor.
#
# Note: ``_merge_split_numbered_paragraph_continuations`` in ``xml_ingest.py``
# already merges many of these cases at parse time (when the preceding para
# does not end with terminal punctuation).  This pass catches the remaining
# cases where that merge was suppressed (e.g., preceding kohta ends with `.`).

# Matches: "1) ", "1. ", "a) ", "a. " at start of content text.
_NUM_IN_INTRO_RE = re.compile(r"^\s*(\d+|[a-z])\s*[)\.]\s")


def _paragraph_content_text(para: IRNode) -> str:
    """Return the leading content text of a PARAGRAPH node for heuristic checks.

    Tries CONTENT child first, then INTRO child, then ``para.text``.
    Used to detect ``num_in_intro`` peers before absorption.
    """
    for child in para.children:
        if child.kind == IRNodeKind.CONTENT:
            return (child.text or "").strip()
    for child in para.children:
        if child.kind == IRNodeKind.INTRO:
            return (child.text or "").strip()
    return (para.text or "").strip()


def _absorb_tail_prose_peers(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Absorb tail_prose unnumbered paragraph peers into the preceding kohta wrapUp.

    Detects unnumbered paragraphs that:
    1. Have NO SUBPARAGRAPH children (those are sub_clause_with_list, handled by T4a).
    2. Do not look like num_in_intro cases (leading ``N)`` / ``N.`` pattern).
    3. Have a preceding numbered PARAGRAPH sibling to absorb into.

    The peer's full text (via ``irnode_to_text``) is appended to (or creates)
    a WRAP_UP node on the preceding numbered kohta.  The WRAP_UP carries the
    opaque marker ``__tail_prose__=1``.  If the preceding kohta already has a
    tail_prose WRAP_UP, the new text is appended with a double-newline separator.

    Must run AFTER ``_reparent_sub_clause_with_list_peers`` (Step 8.5) so that
    sub_clause_with_list peers have already been consumed.
    Must run BEFORE ``_detect_numbering_anomalies`` (Step 9) so the peer is
    still present when absorption is attempted.
    """
    has_numbered = any(
        c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c)
        for c in children
    )
    if not has_numbered:
        return children

    rewritten: List[IRNode] = []
    last_numbered_idx: int = -1
    last_numbered_para: Optional[IRNode] = None

    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue

        if _paragraph_has_num_child(child):
            rewritten.append(child)
            last_numbered_idx = len(rewritten) - 1
            last_numbered_para = child
            continue

        # Unnumbered paragraph: skip if it already has SUBPARAGRAPH children
        # (sub_clause_with_list — handled by T4a, should not reach here).
        if _paragraph_has_subparagraph_children(child):
            rewritten.append(child)
            continue

        # Skip if content looks like a num_in_intro case (T4c).
        content_text = _paragraph_content_text(child)
        if _NUM_IN_INTRO_RE.match(content_text):
            rewritten.append(child)
            continue

        # No preceding numbered kohta — cannot absorb.
        if last_numbered_para is None:
            rewritten.append(child)
            continue

        # Collect the peer's full text content.
        peer_text = irnode_to_text(child).strip()
        peer_eId = child.attrs.get("eId", "")

        # Check if the preceding kohta already has a tail_prose WRAP_UP.
        existing_tail_wu_idx: Optional[int] = None
        for ki, kc in enumerate(last_numbered_para.children):
            if kc.kind == IRNodeKind.WRAP_UP and kc.attrs.get("__tail_prose__"):
                existing_tail_wu_idx = ki
                break

        if existing_tail_wu_idx is not None:
            # Append to the existing tail_prose WRAP_UP.
            existing_wu = last_numbered_para.children[existing_tail_wu_idx]
            combined_text = (existing_wu.text or "") + "\n\n" + peer_text
            new_wu = IRNode(
                kind=IRNodeKind.WRAP_UP,
                text=combined_text,
                attrs={"__tail_prose__": "1"},
                children=existing_wu.children,
            )
            new_kohta_children = list(last_numbered_para.children)
            new_kohta_children[existing_tail_wu_idx] = new_wu
        else:
            # Create a new tail_prose WRAP_UP.
            new_wu = IRNode(
                kind=IRNodeKind.WRAP_UP,
                text=peer_text,
                attrs={"__tail_prose__": "1"},
                children=(),
            )
            new_kohta_children = list(last_numbered_para.children) + [new_wu]

        new_kohta = IRNode(
            kind=last_numbered_para.kind,
            label=last_numbered_para.label,
            text=last_numbered_para.text,
            attrs=last_numbered_para.attrs,
            children=tuple(new_kohta_children),
        )
        rewritten[last_numbered_idx] = new_kohta
        last_numbered_para = new_kohta

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_TAIL_PROSE_ABSORB,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"unnumbered tail-prose paragraph peer (eId={peer_eId!r}) "
                    f"following numbered kohta {last_numbered_para.label!r}; "
                    f"text excerpt: {peer_text[:80]!r}"
                ),
                after=(
                    f"absorbed as wrapUp(__tail_prose__=1) on preceding kohta "
                    f"{new_kohta.label!r}; peer removed from sibling list"
                ),
                explanation=(
                    "Finnish legal ontology disallows unnumbered paragraph peers of "
                    "numbered kohdat.  This peer has no subparagraph children and no "
                    "num_in_intro prefix, making it a tail_prose fragment (loppukappale "
                    "analogue).  Its text is absorbed into a WRAP_UP facet on the preceding "
                    "numbered kohta with opaque marker __tail_prose__=1.  No synthetic "
                    "public labels are produced.  Text content is preserved."
                ),
                path=parent_path,
                confidence=0.95,
            )
        )
        # The peer is NOT appended to rewritten (it's absorbed).

    return rewritten


# ---------------------------------------------------------------------------
# BASE_NUM_IN_INTRO_RECOVERED: parse-phase num recovery for num_in_intro peers
# ---------------------------------------------------------------------------
#
# Finnish source XML sometimes encodes a numbered kohta with the number written
# in the body text instead of a <num> element, e.g.:
#
#   <paragraph>
#     <content><p>2) tämä alkaa numerolla</p></content>
#   </paragraph>
#
# This pass detects such unnumbered paragraphs (no NUM child) whose leading
# content text matches ``^\s*(N|letter)\s*[)\.]\s+`` and where the candidate
# label N is consistent with the surrounding sibling sequence (prev N-1, next
# N+1 for digits; or analogous letter adjacency).  When consistent, the
# leading token is lifted into a synthetic NUM child and the paragraph becomes
# a normal numbered kohta.
#
# Skip conditions (peer left unchanged, MISMATCH observation emitted):
#   1. Candidate label does not fit the surrounding numbered sequence.
#   2. No previous or next numbered sibling exists to anchor the sequence.
#
# This pass runs AFTER T4b (step 8.6) and BEFORE step 9 (numbering anomalies).
# It must run before step 9 so the now-numbered peer is included in the
# duplicate/gap check with its correct label.

# Matches leading "N) " or "N. " at start of content text.
# Groups: (1) candidate label (digit(s) or single letter), (2) separator char,
# (3) remaining text.
_NUM_IN_INTRO_CAPTURE_RE = re.compile(r"^\s*(\d+|[a-z])\s*([)\.])\s+(.*)$", re.DOTALL)


def _label_to_int(label: str) -> Optional[int]:
    """Convert a digit-only label to int, or None if non-numeric."""
    try:
        return int(label)
    except (ValueError, TypeError):
        return None


def _letter_to_ord(label: str) -> Optional[int]:
    """Convert a single lowercase letter to its ordinal (a=1, b=2, ...), or None."""
    if len(label) == 1 and label.islower():
        return ord(label) - ord("a") + 1
    return None


def _infer_separator_from_siblings(numbered_siblings: List[IRNode]) -> str:
    """Infer the num-separator style ('N)' or 'N.') used by existing siblings."""
    for sib in numbered_siblings:
        num_child = next((c for c in sib.children if c.kind == IRNodeKind.NUM), None)
        if num_child is not None:
            raw = (num_child.text or "").strip()
            if raw.endswith(")"):
                return ")"
            if raw.endswith("."):
                return "."
    return ")"  # default: parenthesis style


def _recover_num_in_intro_peers(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Recover unnumbered paragraph peers whose content begins with a leading N)/N. token.

    For each unnumbered PARAGRAPH child (no NUM child, no label):
    1. Extracts leading content text via ``_paragraph_content_text``.
    2. Tries to match ``^\\s*(\\d+|[a-z])\\s*[)\\.]\\s+(.*)$``.
    3. Checks consistency with the surrounding numbered sibling sequence:
       - For digits: prev label must be N-1, next label must be N+1 (if both exist),
         or at least one anchor must confirm N is the expected next/missing value.
       - For letters: same adjacency check in letter space.
    4. On match: constructs a new PARAGRAPH IRNode with the candidate label,
       NUM child using the same separator style as siblings, remaining text as
       CONTENT, and any additional original children preserved after the content.
    5. On mismatch: emits BASE_NUM_IN_INTRO_MISMATCH and leaves the peer unchanged.

    Skips peers that have SUBPARAGRAPH children (handled by T4a).
    """
    has_numbered = any(
        c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c)
        for c in children
    )
    if not has_numbered:
        return children

    rewritten: List[IRNode] = []
    changed = False

    for i, child in enumerate(children):
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue

        # Already numbered — pass through
        if _paragraph_has_num_child(child):
            rewritten.append(child)
            continue

        # Skip peers with subparagraph children (T4a handles those)
        if _paragraph_has_subparagraph_children(child):
            rewritten.append(child)
            continue

        # Extract leading content text for heuristic check
        content_text = _paragraph_content_text(child)
        m = _NUM_IN_INTRO_CAPTURE_RE.match(content_text)
        if m is None:
            # No leading N)/N. token — not a num_in_intro peer
            rewritten.append(child)
            continue

        candidate_raw = m.group(1)
        separator_char = m.group(2)
        remaining_text = m.group(3).strip()

        # Gather surrounding numbered siblings for sequence consistency check
        prev_numbered: List[IRNode] = [
            c for c in children[:i]
            if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c)
        ]
        next_numbered: List[IRNode] = [
            c for c in children[i + 1:]
            if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(c)
        ]

        # Determine the expected candidate based on sequence context
        # We need at least one neighboring numbered sibling to anchor.
        if not prev_numbered and not next_numbered:
            # No anchors at all — cannot verify consistency; skip
            rewritten.append(child)
            continue

        # Compute expected label from neighbors
        candidate_int = _label_to_int(candidate_raw)
        candidate_letter_ord = _letter_to_ord(candidate_raw)

        is_consistent = False

        if candidate_int is not None:
            # Digit sequence consistency
            prev_label = prev_numbered[-1].label if prev_numbered else None
            next_label = next_numbered[0].label if next_numbered else None
            prev_int = _label_to_int(str(prev_label)) if prev_label is not None else None
            next_int = _label_to_int(str(next_label)) if next_label is not None else None

            if prev_int is not None and next_int is not None:
                # Both anchors present: candidate must be prev_int+1 == next_int-1
                is_consistent = (candidate_int == prev_int + 1 == next_int - 1)
            elif prev_int is not None:
                # Only previous anchor: candidate must be prev_int+1
                is_consistent = (candidate_int == prev_int + 1)
            elif next_int is not None:
                # Only next anchor: candidate must be next_int-1
                is_consistent = (candidate_int == next_int - 1)

        elif candidate_letter_ord is not None:
            # Letter sequence consistency
            prev_label = prev_numbered[-1].label if prev_numbered else None
            next_label = next_numbered[0].label if next_numbered else None
            prev_ord = _letter_to_ord(str(prev_label)) if prev_label is not None else None
            next_ord = _letter_to_ord(str(next_label)) if next_label is not None else None

            if prev_ord is not None and next_ord is not None:
                is_consistent = (candidate_letter_ord == prev_ord + 1 == next_ord - 1)
            elif prev_ord is not None:
                is_consistent = (candidate_letter_ord == prev_ord + 1)
            elif next_ord is not None:
                is_consistent = (candidate_letter_ord == next_ord - 1)

        if not is_consistent:
            # Candidate label doesn't fit the sequence — emit mismatch, leave unchanged
            facts.append(
                SourceNormalizationFact(
                    statute_id=statute_id,
                    kind=BASE_NUM_IN_INTRO_MISMATCH,
                    basis=SourceNormalizationBasis.PROFILE_INVALID,
                    before=(
                        f"unnumbered paragraph with leading token {candidate_raw!r} "
                        f"(separator={separator_char!r}) at index {i}; "
                        f"content: {content_text[:80]!r}"
                    ),
                    after="(skipped: candidate does not fit surrounding numbered sequence)",
                    explanation=(
                        "The paragraph's content begins with a token that looks like a "
                        "kohta number placed in body text instead of a <num> element. "
                        "However the candidate label does not fit the surrounding sibling "
                        "sequence (no real Finnish legal label can be inferred). "
                        "Recovery would require inventing a label — skipped per "
                        "no-synthetic-public-label rule. Peer left unchanged."
                    ),
                    path=parent_path,
                    confidence=0.85,
                )
            )
            rewritten.append(child)
            continue

        # Recovery: lift the leading token into a synthetic NUM child.
        # Infer separator style from existing siblings (prefer neighbor style).
        all_numbered_siblings = prev_numbered + next_numbered
        inferred_sep = _infer_separator_from_siblings(all_numbered_siblings)
        # Use the inferred separator unless the candidate itself used a different one;
        # in practice always trust the neighbor separator for consistency.
        num_text = f"{candidate_raw}{inferred_sep}"

        # Build the new CONTENT child with remaining text.
        # Also preserve any non-CONTENT / non-INTRO children from the original peer
        # (unlikely but safe to forward them).
        leading_content = IRNode(kind=IRNodeKind.CONTENT, text=remaining_text)
        other_children = [
            c for c in child.children
            if c.kind not in (IRNodeKind.CONTENT, IRNodeKind.INTRO)
        ]
        new_para_children: List[IRNode] = [
            IRNode(kind=IRNodeKind.NUM, text=num_text),
            leading_content,
        ]
        new_para_children.extend(other_children)

        recovered = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=candidate_raw,
            text=child.text,
            attrs=child.attrs,
            children=tuple(new_para_children),
        )
        rewritten.append(recovered)
        changed = True

        peer_eId = child.attrs.get("eId", "")
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_NUM_IN_INTRO_RECOVERED,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"unnumbered paragraph (eId={peer_eId!r}) with leading token "
                    f"{candidate_raw!r}{separator_char} in body text; "
                    f"content excerpt: {content_text[:80]!r}"
                ),
                after=(
                    f"recovered as numbered kohta label={candidate_raw!r} with "
                    f"synthetic NUM child {num_text!r}; leading token stripped from content"
                ),
                explanation=(
                    "The source paragraph has its kohta number written in body text "
                    f"({candidate_raw!r}{separator_char}) instead of a <num> element — "
                    "a Finlex source encoding defect.  The leading token is consistent "
                    "with the surrounding sibling sequence (prev N-1, next N+1 or "
                    "letter-adjacent), so recovery is safe: the token is lifted into a "
                    "synthetic NUM child and the remaining text kept as CONTENT.  "
                    "The real Finnish label is preserved; no synthetic public label is invented."
                ),
                path=parent_path,
                confidence=0.94,
            )
        )

    if not changed:
        return children
    return rewritten


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_source_ir(
    ir: IRNode,
    statute_id: str,
    *,
    _parent_path: Optional[Tuple[str, ...]] = None,
) -> Tuple[IRNode, List[SourceNormalizationFact]]:
    """Apply source normalization to an already-parsed IR tree.

    This is a separate phase from XML parsing (``fi_xml_to_ir_node``).  It
    detects and corrects source pathologies that are auto-correctable per the
    policy in PRO_RESPONSE_5_1.md Query 10, and emits a typed
    ``SourceNormalizationFact`` for every correction applied.

    Corrections applied (in order):
      1. Recurse into children (bottom-up).
      2. Strip editorial blocks (image, note, footnote, authorialNote).
      3. Hoist standalone cross-heading siblings into the following node.
      4. Rebuild node if children changed.
      5. Normalize whitespace in text content.
      6. Reclassify item-style subsections.
      7. Repair paragraph-local digit resets buried inside subparagraph runs.
      8. Apply shape-driven sparse repairs for known malformed payloads.
      8.5. Reparent sub_clause_with_list unnumbered paragraph peers under the
           preceding numbered kohta (SUBSECTION nodes only).  Must run before
           step 9 so the peer is still present.
      8.6. Absorb tail_prose unnumbered paragraph peers (no subparagraph
           children, no num_in_intro prefix) as a wrapUp facet on the
           preceding numbered kohta (SUBSECTION nodes only).  Runs after
           step 8.5 and before step 9.
      8.7. Recover num_in_intro unnumbered paragraph peers by lifting the
           leading ``N)``/``N.`` token from body text into a synthetic NUM
           child (SUBSECTION nodes only).  Only recovers when the candidate
           label is consistent with the surrounding sibling sequence.  Emits
           BASE_NUM_IN_INTRO_RECOVERED on success, BASE_NUM_IN_INTRO_MISMATCH
           on skip.  Runs after step 8.6 and before step 9.
      9. Detect numbering anomalies (gaps and duplicates) among siblings.

    Parameters
    ----------
    ir:
        The IR tree returned by ``fi_xml_to_ir_node``.  Not mutated.
    statute_id:
        The statute identifier, used in emitted facts (e.g. ``"2020/1262"``).

    Returns
    -------
    normalized_ir:
        The corrected IR tree (a new tree if any correction was applied,
        otherwise the input ``ir`` object unchanged).
    facts:
        All ``SourceNormalizationFact`` records emitted during normalization.
        Empty if no corrections were applied.
    """
    parent_path: Tuple[str, ...] = _parent_path if _parent_path is not None else ()
    facts: List[SourceNormalizationFact] = []

    current_path = parent_path + (_node_path_label(ir),)
    # Step 0: section-local shape repair before child recursion.
    #
    # Some malformed section shapes must be split before subsection-local
    # numbering cleanup runs; otherwise the child subsection loses real items as
    # apparent duplicates before the moment boundary is restored.
    initial_children: List[IRNode] = list(ir.children)
    if ir.kind == IRNodeKind.SECTION:
        initial_children = _split_intro_then_numbered_list_subsections(
            initial_children, statute_id, current_path, facts
        )

    # Step 1: recurse into children first (bottom-up), collecting child facts.
    new_children: List[IRNode] = []
    children_changed = False
    for child in initial_children:
        normalized_child, child_facts = normalize_source_ir(
            child, statute_id, _parent_path=current_path
        )
        facts.extend(child_facts)
        new_children.append(normalized_child)
        if normalized_child is not child:
            children_changed = True
    if len(initial_children) != len(ir.children) or any(a is not b for a, b in zip(initial_children, ir.children, strict=True)):
        children_changed = True

    # Step 2: strip editorial block children (image, note, footnote, authorialNote).
    stripped_children = _strip_editorial_blocks(new_children, statute_id, current_path, facts)
    if len(stripped_children) != len(new_children):
        children_changed = True
    new_children = stripped_children

    hoisted_children = _hoist_cross_heading_siblings(new_children, statute_id, current_path, facts)
    if len(hoisted_children) != len(new_children) or any(a is not b for a, b in zip(hoisted_children, new_children, strict=True)):
        children_changed = True
    new_children = hoisted_children

    reparented_children = _reparent_trailing_chapters_into_preceding_part(
        new_children, statute_id, current_path, facts
    )
    if len(reparented_children) != len(new_children) or any(
        a is not b for a, b in zip(reparented_children, new_children, strict=True)
    ):
        children_changed = True
    new_children = reparented_children

    # Step 4: rebuild the node with updated children if anything changed.
    working: IRNode = ir
    if children_changed:
        working = IRNode(
            kind=ir.kind,
            label=ir.label,
            text=ir.text,
            attrs=ir.attrs,
            children=tuple(new_children),
        )

    # Step 5: normalize whitespace in text content.
    working = _normalize_whitespace(working, statute_id, current_path, facts)

    # Step 6: apply node-level structural corrections.
    if _is_item_style_subsection(working):
        working = _reclassify_item_style_subsection(working, statute_id, parent_path, facts)
    working = _split_nonpenal_trailing_duplicate_paragraph(
        working,
        statute_id,
        current_path,
        facts,
    )
    # Step 7: split malformed paragraph-local digit resets before generic numbering checks.
    new_children = list(working.children)
    repaired_children = _split_digit_reset_subparagraph_runs(
        new_children, statute_id, current_path, facts
    )
    if len(repaired_children) != len(new_children) or any(a is not b for a, b in zip(repaired_children, new_children, strict=True)):
        working = IRNode(
            kind=working.kind,
            label=working.label,
            text=working.text,
            attrs=working.attrs,
            children=tuple(repaired_children),
        )

    # Step 8 is intentionally disabled.
    #
    # These statute-shaped sparse-tail rewrites were not universal.  They are
    # left documented in tests only, not as live normalization authority.

    # Step 8.5: reparent sub_clause_with_list unnumbered paragraph peers.
    #
    # Must run BEFORE step 9 (numbering anomaly detection) so the unnumbered
    # peer is still present when reparenting is attempted.  After reparenting
    # the peer is removed from the sibling list, and the numbered sequence is
    # clean for the subsequent dedup check.
    #
    # Only meaningful when the current node is a SUBSECTION (unnumbered peers
    # of numbered kohdat occur at subsection level).
    if working.kind == IRNodeKind.SUBSECTION:
        new_children = list(working.children)
        reparented_children = _reparent_sub_clause_with_list_peers(
            new_children, statute_id, current_path, facts
        )
        if len(reparented_children) != len(new_children) or any(
            a is not b for a, b in zip(reparented_children, new_children, strict=True)
        ):
            working = IRNode(
                kind=working.kind,
                label=working.label,
                text=working.text,
                attrs=working.attrs,
                children=tuple(reparented_children),
            )

    # Step 8.6: absorb tail_prose unnumbered paragraph peers as wrapUp.
    #
    # Must run AFTER step 8.5 (sub_clause_with_list peers already consumed).
    # Must run BEFORE step 9 (numbering anomaly detection) so the peer is
    # still present when absorption is attempted.
    #
    # Only meaningful when the current node is a SUBSECTION.
    if working.kind == IRNodeKind.SUBSECTION:
        new_children = list(working.children)
        absorbed_children = _absorb_tail_prose_peers(
            new_children, statute_id, current_path, facts
        )
        if len(absorbed_children) != len(new_children) or any(
            a is not b for a, b in zip(absorbed_children, new_children, strict=True)
        ):
            working = IRNode(
                kind=working.kind,
                label=working.label,
                text=working.text,
                attrs=working.attrs,
                children=tuple(absorbed_children),
            )

    # Step 8.7: recover num_in_intro unnumbered paragraph peers.
    #
    # Must run AFTER step 8.6 (tail_prose peers already consumed) and BEFORE
    # step 9 (numbering anomaly detection) so the recovered peer is visible
    # as a normal numbered paragraph when dedup/gap checks run.
    #
    # Only meaningful when the current node is a SUBSECTION.
    if working.kind == IRNodeKind.SUBSECTION:
        new_children = list(working.children)
        recovered_children = _recover_num_in_intro_peers(
            new_children, statute_id, current_path, facts
        )
        if len(recovered_children) != len(new_children) or any(
            a is not b for a, b in zip(recovered_children, new_children, strict=True)
        ):
            working = IRNode(
                kind=working.kind,
                label=working.label,
                text=working.text,
                attrs=working.attrs,
                children=tuple(recovered_children),
            )

    # Step 9: detect numbering anomalies (gaps/duplicates) among siblings.
    new_children = list(working.children)
    deduped_children = _detect_numbering_anomalies(
        new_children, statute_id, current_path, facts
    )
    if len(deduped_children) != len(new_children):
        working = IRNode(
            kind=working.kind,
            label=working.label,
            text=working.text,
            attrs=working.attrs,
            children=tuple(deduped_children),
        )

    return working, facts


__all__ = ["normalize_source_ir"]
