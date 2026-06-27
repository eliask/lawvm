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

1b. **TAG_RECLASSIFY / IMPOSSIBLE_NUMBERING** -- a section-scoped item-style
    ``<subsection num="2)">`` may be folded into the immediately preceding
    subsection as a paragraph when its label continues that subsection's item
    sequence.  This handles base XML that split one momentti's kohdat across
    two sibling ``subsection`` elements.

1c. **PROFILE_INVALID** -- an unlabelled ``subsection`` carrying only
    letter-labelled paragraph children and an omission marker may be folded into
    the preceding comma-ended subsection.  This handles amendment XML that
    splits one changed momentti's intro/content from its item list.

1d. **SUSPICIOUS_SHAPE / PROFILE_INVALID** -- a section-scoped item-style
    ``<subsection num="9)">`` that cannot be safely folded is preserved as a
    subsection container, and a typed witness is emitted. This keeps the
    suspicious source shape visible while avoiding an illegal
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
   carried under it.  A terminal digit reset is split only when the surrounding
   peer item sequence witnesses it as the next sibling paragraph.

9. **UNNUMBERED_PEER_REPARENT / PROFILE_INVALID** -- an unnumbered paragraph
   peer of numbered kohdat carrying a subparagraph list (``sub_clause_with_list``
   sub-type) is reparented under the preceding numbered kohta.  Sub-case A
   (preceding kohta has no own subparagraphs): subparagraphs are attached
   directly as kohta children.  Sub-case B (preceding kohta already has
   subparagraphs): a ``WRAP_UP`` continuation facet is appended.  This pass
   runs BEFORE the numbering-anomaly dedup so the peer is still present.

10. **UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT / PROFILE_INVALID** -- a closed
    numbered paragraph whose direct child run starts with an unnumbered
    ``subparagraph`` is split so that the unnumbered payload becomes the next
    peer subsection.  If numbered subparagraphs and immediately following
    numbered paragraph siblings continue that payload's item run, they are
    carried into the new peer subsection.

11. **HEADING_BODY_SUBSECTION_SPLIT / PROFILE_INVALID** -- a section-local
    ``heading`` that carries paragraph-length body text before a consecutive
    subsection run is converted to subsection 1, and following subsections are
    shifted by one. Real short section headings are preserved.

All corrections are applied in a single recursive tree walk so that a
statute with multiple pathological nodes produces one fact per corrected node.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import default_label_sort_key
from lawvm.core.semantic_types import (
    IRNodeKind,
    SourceNormalizationBasis,
    SourceNormalizationFact,
    SourceNormalizationKind,
)
from lawvm.finland.helpers import _norm_num_token, may_attach_post_list_loppukappale
from lawvm.xml_ingest import (
    _paragraph_ends_with_terminal_punctuation,
    _paragraph_has_num,
    _paragraph_is_content_only,
)
from lawvm.finland.source_normalization_kinds import (
    BASE_DIGIT_RESET_SPLIT,
    BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION,
    BASE_INTRO_LIST_RESTART_SPLIT,
    BASE_INTRO_LIST_TAIL_MOMENT_SPLIT,
    BASE_DUPLICATE_SIBLING_DROP,
    BASE_DUPLICATE_TAIL_SPLIT,
    BASE_NUM_IN_INTRO_MISMATCH,
    BASE_NUM_IN_INTRO_RECOVERED,
    BASE_HEADING_BODY_SUBSECTION_SPLIT,
    HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR,
    BASE_SECTION_ITEM_SUBSECTION_FOLD,
    BASE_TABLE_NOTE_SUBSECTION_FOLD,
    BASE_TAIL_PROSE_ABSORB,
    BASE_TABLE_CONTINUATION_SUBSECTION_MERGE,
    BASE_TABLE_CONTINUATION_HEADER_REPAIR,
    BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT,
    TRAILING_CHAPTER_REPARENT,
    TABLE_CONTINUATION_SUBSECTION_MERGE_RULE_ATTR,
    UNNUMBERED_PEER_REPARENT,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ITEM_NUM_RE = re.compile(r"^\d+[a-z]?\)$")
_LEADING_DIGIT_ITEM_TEXT_RE = re.compile(r"^\s*(\d+)\)\s*(.+?)\s*$", re.S)
_DOTTED_MOMENT_NUM_RE = re.compile(r"^(\d+)\.$")
_DOTTED_MOMENT_INTRO_RE = re.compile(r"^\s*(\d+)\.\s+")
_LETTER_LABEL_RE = re.compile(r"^[a-z]$")
_ARABIC_LABEL_RE = re.compile(r"^\d+[a-z]?$")
_GLUED_ITEM_COORDINATORS = ("seka", "sekä", "ja")


def source_normalization_fact_finding_kind(kind_value: str) -> str | None:
    """Resolve a source-normalization fact kind to its registered BASE finding kind."""
    raw = str(kind_value or "").strip()
    if not raw:
        return None
    candidate = raw.upper() if raw.startswith("base_") else f"BASE_{raw.upper()}"
    spec = get_finding_spec(candidate)
    if spec is None or not candidate.startswith("BASE_"):
        return None
    return candidate


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


def _is_substantive_body_heading(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 80:
        return False
    if not compact.endswith("."):
        return False
    # Section titles can be long, but they rarely contain multiple full
    # sentences. A body-bearing heading in malformed source XML does.
    return compact.count(".") >= 2


def _numeric_subsection_labels_are_initial_run(subsections: list[IRNode]) -> bool:
    labels: list[int] = []
    for subsection in subsections:
        label = str(subsection.label or "")
        if not label.isdigit():
            return False
        labels.append(int(label))
    return labels == list(range(1, len(labels) + 1))


def _shift_subsection_label(subsection: IRNode) -> IRNode:
    label = str(subsection.label or "")
    if not label.isdigit():
        return subsection
    attrs = dict(subsection.attrs)
    attrs.setdefault("lawvm_source_normalization_original_label", label)
    attrs["lawvm_source_normalization_rule"] = HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR
    return IRNode(
        kind=subsection.kind,
        label=str(int(label) + 1),
        text=subsection.text,
        attrs=attrs,
        children=tuple(subsection.children),
    )


def _split_body_heading_into_first_subsection(
    children: list[IRNode],
    statute_id: str,
    parent_path: tuple[str, ...],
    facts: list[SourceNormalizationFact],
) -> list[IRNode]:
    """Convert a paragraph-length section heading into first subsection body.

    Historical Finlex XML occasionally wraps the first momentti body in a
    ``heading`` tag while the following momentit are normal ``subsection``
    nodes. A real section heading is preserved; this only fires for long,
    sentence-like heading text followed by a clean consecutive subsection run.
    """
    if not children:
        return children
    heading_indexes = [idx for idx, child in enumerate(children) if child.kind == IRNodeKind.HEADING]
    if len(heading_indexes) != 1:
        return children
    heading_index = heading_indexes[0]
    heading = children[heading_index]
    heading_text = " ".join(irnode_to_text(heading).split())
    if not _is_substantive_body_heading(heading_text):
        return children

    following = children[heading_index + 1 :]
    if not following or any(child.kind is not IRNodeKind.SUBSECTION for child in following):
        return children
    subsections = list(following)
    if not _numeric_subsection_labels_are_initial_run(subsections):
        return children
    if any(child.kind is IRNodeKind.SUBSECTION for child in children[:heading_index]):
        return children

    first_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        attrs={"lawvm_source_normalization_rule": HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR},
        children=(IRNode(kind=IRNodeKind.CONTENT, text=heading_text),),
    )
    rewritten = (
        children[:heading_index]
        + [first_subsection]
        + [_shift_subsection_label(subsection) for subsection in subsections]
    )
    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=BASE_HEADING_BODY_SUBSECTION_SPLIT,
            basis=SourceNormalizationBasis.PROFILE_INVALID,
            before=(
                "section heading contains paragraph-length first-moment body; "
                f"heading excerpt={heading_text[:120]!r}; shifted_subsections={len(subsections)}"
            ),
            after="heading converted to subsection:1 and following subsection labels shifted by +1",
            explanation=(
                "The source section has no title facet; its <heading> carries substantive "
                "body prose while the following children are a consecutive subsection run. "
                "Treat the heading text as the missing first momentti body and preserve the "
                "following momentit by shifting their labels."
            ),
            path=parent_path,
            confidence=0.94,
        )
    )
    return rewritten


def _contains_node_kind(node: IRNode, kind: IRNodeKind) -> bool:
    return node.kind == kind or any(_contains_node_kind(child, kind) for child in node.children)


def _first_text_segment(node: IRNode) -> str:
    if node.text and node.text.strip():
        return node.text.strip()
    for child in node.children:
        text = _first_text_segment(child)
        if text:
            return text
    return ""


def _relabel_subsection_with_source_label(subsection: IRNode, label: int) -> IRNode:
    source_label = str(subsection.label or "")
    if not source_label.isdigit() or int(source_label) == label:
        return subsection
    attrs = dict(subsection.attrs)
    source_eid = attrs.pop("eId", None)
    if source_eid:
        attrs["lawvm_source_subsection_eid"] = str(source_eid)
    attrs.setdefault("lawvm_source_normalization_original_label", source_label)
    attrs["lawvm_source_normalization_rule"] = TABLE_CONTINUATION_SUBSECTION_MERGE_RULE_ATTR
    return IRNode(
        kind=subsection.kind,
        label=str(label),
        text=subsection.text,
        attrs=attrs,
        children=tuple(subsection.children),
    )


def _table_cell_texts(row: IRNode) -> list[str]:
    if row.kind != IRNodeKind.ROW:
        return []
    return [" ".join(irnode_to_text(cell).split()) for cell in row.children if cell.kind == IRNodeKind.CELL]


def _table_row_from_texts(cells: tuple[str, ...]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.ROW,
        children=tuple(IRNode(kind=IRNodeKind.CELL, text=cell) for cell in cells),
    )


def _normalize_table_number_cell(cell: IRNode) -> IRNode:
    text = " ".join(irnode_to_text(cell).split())
    if len(text) == 4 and text.isdigit():
        attrs = dict(cell.attrs)
        attrs["lawvm_source_normalization_rule"] = TABLE_CONTINUATION_SUBSECTION_MERGE_RULE_ATTR
        return IRNode(kind=cell.kind, label=cell.label, text=f"{text[0]} {text[1:]}", attrs=attrs)
    return cell


def _repair_table_continuation_header(table: IRNode) -> tuple[IRNode, bool]:
    if table.kind != IRNodeKind.TABLE or not table.children:
        return table, False
    first_row = table.children[0]
    cells = _table_cell_texts(first_row)
    if len(cells) != 4:
        return table, False
    third_tokens = cells[2].split()
    if (
        cells[0] != "Alue"
        or cells[1] != "Mänty"
        or cells[3] != "Muu puulaji"
        or third_tokens != ["Pääpuulaji", "Kuusi", "kpl/hehtaari"]
    ):
        return table, False

    data_rows = []
    for row in table.children[1:]:
        if row.kind == IRNodeKind.ROW:
            data_rows.append(
                IRNode(
                    kind=row.kind,
                    label=row.label,
                    attrs=row.attrs,
                    children=tuple(
                        _normalize_table_number_cell(cell)
                        if cell.kind == IRNodeKind.CELL
                        else cell
                        for cell in row.children
                    ),
                )
            )
        else:
            data_rows.append(row)
    rewritten = IRNode(
        kind=table.kind,
        label=table.label,
        text=table.text,
        attrs=dict(table.attrs),
        children=(
            _table_row_from_texts(("", "", "Pääpuulaji", "")),
            _table_row_from_texts(("Alue", "Mänty", "Kuusi", "Muu puulaji")),
            _table_row_from_texts(("", "", "kpl/hehtaari", "")),
            *data_rows,
        ),
    )
    return rewritten, True


def _repair_table_continuation_payload(
    node: IRNode,
) -> tuple[IRNode, bool]:
    changed = False
    rewritten_children = []
    for child in node.children:
        if child.kind == IRNodeKind.TABLE:
            rewritten_child, child_changed = _repair_table_continuation_header(child)
        else:
            rewritten_child, child_changed = _repair_table_continuation_payload(child)
        rewritten_children.append(rewritten_child)
        changed = changed or child_changed
    if not changed:
        return node, False
    return (
        IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=node.attrs,
            children=tuple(rewritten_children),
        ),
        True,
    )


def _merge_table_continuation_subsection(
    children: list[IRNode],
    statute_id: str,
    parent_path: tuple[str, ...],
    facts: list[SourceNormalizationFact],
) -> list[IRNode]:
    """Merge a table-bearing continuation split out as a fake momentti.

    Some Finlex enacted XML splits a first momentti immediately before a table:
    ``subsection:1`` ends mid-sentence and ``subsection:2`` starts with a
    lowercase continuation plus the table. Consolidated XML for the same text
    treats them as one momentti. This repair is limited to the initial
    subsection run so it cannot merge real later legal paragraphs.
    """
    subsections = [child for child in children if child.kind == IRNodeKind.SUBSECTION]
    if len(subsections) < 3:
        return children
    if not _numeric_subsection_labels_are_initial_run(subsections):
        return children

    first, second, *remaining = subsections
    first_text = " ".join(irnode_to_text(first).split())
    second_first_text = _first_text_segment(second)
    if not first_text or first_text[-1] in ".;:":
        return children
    first_char = second_first_text[:1]
    if not first_char or not first_char.islower():
        return children
    if not _contains_node_kind(second, IRNodeKind.TABLE):
        return children

    first_index = children.index(first)
    second_index = children.index(second)
    if second_index != first_index + 1:
        return children

    repaired_second, repaired_table_header = _repair_table_continuation_payload(second)

    first_attrs = dict(first.attrs)
    first_attrs["lawvm_source_normalization_rule"] = TABLE_CONTINUATION_SUBSECTION_MERGE_RULE_ATTR
    first_attrs["lawvm_source_normalization_merged_label"] = str(second.label or "")
    merged_first = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        text=first.text,
        attrs=first_attrs,
        children=tuple(first.children) + tuple(repaired_second.children),
    )
    rewritten_subsections = [merged_first] + [
        _relabel_subsection_with_source_label(subsection, idx)
        for idx, subsection in enumerate(remaining, start=2)
    ]
    subsection_iter = iter(rewritten_subsections)
    rewritten: list[IRNode] = []
    skip_second = False
    for child in children:
        if child is first:
            rewritten.append(next(subsection_iter))
            skip_second = True
            continue
        if skip_second and child is second:
            skip_second = False
            continue
        if child.kind == IRNodeKind.SUBSECTION:
            rewritten.append(next(subsection_iter))
        else:
            rewritten.append(child)

    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=BASE_TABLE_CONTINUATION_SUBSECTION_MERGE,
            basis=SourceNormalizationBasis.PROFILE_INVALID,
            before=(
                "first subsection ended without terminal punctuation and the next "
                "subsection began with a lowercase table continuation; "
                f"first_excerpt={first_text[:120]!r}; second_start={second_first_text[:80]!r}"
            ),
            after="subsection:2 payload merged into subsection:1 and later subsection labels shifted by -1",
            explanation=(
                "The source XML split one printed momentti around a table. The first "
                "subsection ends mid-sentence, the following subsection starts as a "
                "lowercase continuation and contains the table, while the remaining "
                "siblings form a consecutive momentti run. Preserve the legal text by "
                "merging the continuation into momentti 1."
            ),
            path=parent_path,
            confidence=0.95,
        )
    )
    if repaired_table_header:
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_TABLE_CONTINUATION_HEADER_REPAIR,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before="table continuation had one fused header row: Alue | Mänty | Pääpuulaji Kuusi kpl/hehtaari | Muu puulaji",
                after=(
                    "table header split into superheader, column-label, and unit rows; "
                    "four-digit numeric cells normalized with a thousands space"
                ),
                explanation=(
                    "The same malformed source split that separated the table from "
                    "the first moment also fused the table's multi-row header into one "
                    "cell. The consolidated witness presents the header as distinct "
                    "rows. Reconstruct that local table header shape while preserving "
                    "the data rows."
                ),
                path=parent_path,
                confidence=0.94,
            )
        )
    return rewritten


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


def _numeric_label_value(label: str | None) -> int | None:
    if label is None:
        return None
    normalized = _norm_num_token(str(label))
    return int(normalized) if normalized.isdigit() else None


def _section_item_style_subsection_continues_previous(
    previous_subsection: IRNode,
    item_subsection: IRNode,
) -> bool:
    item_value = _numeric_label_value(item_subsection.label)
    if item_value is None:
        return False
    paragraph_values = [
        value
        for child in previous_subsection.children
        if child.kind == IRNodeKind.PARAGRAPH
        for value in (_numeric_label_value(child.label),)
        if value is not None
    ]
    return bool(paragraph_values) and max(paragraph_values) + 1 == item_value


def _section_item_style_subsection_payload_nodes(node: IRNode) -> Tuple[IRNode, ...]:
    """Convert a section-scoped item-style subsection into paragraph siblings.

    The leading ``NUM``/``INTRO`` plus letter-labelled paragraphs become one
    paragraph with subparagraph children.  Later digit-labelled paragraphs in
    the same malformed source block are trailing sibling kohdat, not children
    of the converted paragraph.
    """
    item_children: List[IRNode] = []
    trailing_siblings: List[IRNode] = []
    in_trailing_siblings = False

    for child in node.children:
        if child.kind == IRNodeKind.PARAGRAPH and child.label is not None:
            if _LETTER_LABEL_RE.match(child.label) and not in_trailing_siblings:
                item_children.append(
                    IRNode(
                        kind=IRNodeKind.SUBPARAGRAPH,
                        label=child.label,
                        text=child.text,
                        attrs=child.attrs,
                        children=child.children,
                    )
                )
                continue
            if _ARABIC_LABEL_RE.match(child.label):
                in_trailing_siblings = True
                trailing_siblings.append(child)
                continue
        if in_trailing_siblings:
            trailing_siblings.append(child)
        else:
            item_children.append(child)

    converted = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=node.label,
        text=node.text,
        attrs=node.attrs,
        children=tuple(item_children),
    )
    return (converted, *trailing_siblings)


def _dotted_paragraph_num_value(paragraph: IRNode) -> int | None:
    if paragraph.kind != IRNodeKind.PARAGRAPH:
        return None
    num_child = next((child for child in paragraph.children if child.kind == IRNodeKind.NUM), None)
    if num_child is None:
        return None
    match = _DOTTED_MOMENT_NUM_RE.match((num_child.text or "").strip())
    if match is None:
        return None
    value = int(match.group(1))
    label_value = _numeric_label_value(paragraph.label)
    if label_value is not None and label_value != value:
        return None
    return value


def _dotted_intro_moment_value(subsection: IRNode) -> int | None:
    if subsection.kind != IRNodeKind.SUBSECTION:
        return None
    for child in subsection.children:
        if child.kind not in {IRNodeKind.INTRO, IRNodeKind.CONTENT}:
            continue
        match = _DOTTED_MOMENT_INTRO_RE.match(irnode_to_text(child).strip())
        if match is not None:
            return int(match.group(1))
    return None


def _subsection_payload_children_from_dotted_paragraph(paragraph: IRNode) -> tuple[IRNode, ...]:
    if paragraph.children:
        return paragraph.children
    if paragraph.text:
        return (IRNode(kind=IRNodeKind.CONTENT, text=paragraph.text),)
    return ()


def _promoted_dotted_paragraph_subsections(subsection: IRNode) -> list[IRNode] | None:
    source_label = _numeric_label_value(subsection.label)
    if source_label is None:
        return None
    semantic_children = [child for child in subsection.children if child.kind != IRNodeKind.NUM]
    if not semantic_children or any(child.kind != IRNodeKind.PARAGRAPH for child in semantic_children):
        return None

    promoted: list[IRNode] = []
    expected = source_label
    for paragraph in semantic_children:
        value = _dotted_paragraph_num_value(paragraph)
        if value != expected:
            return None
        attrs = dict(subsection.attrs) if value == source_label else {}
        source_eid = paragraph.attrs.get("eId")
        if source_eid:
            attrs["lawvm_source_paragraph_eid"] = source_eid
        attrs["lawvm_source_normalization_rule"] = "fi_dotted_paragraph_subsection_promotion_v1"
        promoted.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=str(value),
                attrs=attrs,
                children=_subsection_payload_children_from_dotted_paragraph(paragraph),
            )
        )
        expected += 1
    return promoted


def _with_dotted_intro_subsection_label(subsection: IRNode, label: int) -> IRNode:
    attrs = dict(subsection.attrs)
    source_eid = attrs.pop("eId", None)
    if source_eid:
        attrs["lawvm_source_subsection_eid"] = source_eid
    if subsection.label is not None:
        attrs["lawvm_source_subsection_label"] = str(subsection.label)
    attrs["lawvm_source_normalization_rule"] = "fi_dotted_paragraph_subsection_promotion_v1"
    return IRNode(
        kind=subsection.kind,
        label=str(label),
        text=subsection.text,
        attrs=attrs,
        children=subsection.children,
    )


def _promote_dotted_paragraph_subsections(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Promote old dotted-number paragraph rows into peer momentti subsections.

    Historical decision-style Finnish source XML may encode printed moments as
    ``<paragraph><num>1.</num>`` rows under one subsection wrapper.  These are
    peer momentit, not kohdat under moment 1.
    """
    if len(children) < 1:
        return children

    rewritten: List[IRNode] = []
    changed = False
    expected_next: int | None = None
    for idx, child in enumerate(children):
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            expected_next = None
            continue

        promoted = _promoted_dotted_paragraph_subsections(child)
        if promoted is not None:
            next_child = children[idx + 1] if idx + 1 < len(children) else None
            next_intro_value = (
                _dotted_intro_moment_value(next_child)
                if next_child is not None and next_child.kind == IRNodeKind.SUBSECTION
                else None
            )
            last_promoted = int(promoted[-1].label or "0")
            if len(promoted) > 1 or next_intro_value == last_promoted + 1:
                rewritten.extend(promoted)
                facts.append(
                    SourceNormalizationFact(
                        statute_id=statute_id,
                        kind=BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION,
                        basis=SourceNormalizationBasis.PROFILE_INVALID,
                        before=(
                            f"{_node_path_label(child)} carried dotted paragraph rows "
                            f"{[p.label for p in promoted]!r}"
                        ),
                        after=f"promoted to peer subsections {[p.label for p in promoted]!r}",
                        explanation=(
                            "The source encoded printed decision-style moment numbers "
                            "as paragraph rows inside one subsection wrapper. Finnish "
                            "kohdat are numbered with closing-parenthesis labels; dotted "
                            "integer rows here are peer momentti subsections."
                        ),
                        path=parent_path + (_node_path_label(child),),
                        confidence=0.96,
                    )
                )
                expected_next = last_promoted + 1
                changed = True
                continue

        intro_value = _dotted_intro_moment_value(child)
        if expected_next is not None and intro_value is not None and intro_value == expected_next:
            rewritten_child = _with_dotted_intro_subsection_label(child, intro_value)
            rewritten.append(rewritten_child)
            facts.append(
                SourceNormalizationFact(
                    statute_id=statute_id,
                    kind=BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION,
                    basis=SourceNormalizationBasis.PROFILE_INVALID,
                    before=(
                        f"{_node_path_label(child)} carried dotted intro moment "
                        f"{intro_value}."
                    ),
                    after=f"relabelled as subsection:{intro_value}",
                    explanation=(
                        "The preceding subsection wrapper was split into printed "
                        "dotted-number momentti peers, and this following sibling's "
                        "intro starts with the next printed moment number."
                    ),
                    path=parent_path + (_node_path_label(child),),
                    confidence=0.95,
                )
            )
            expected_next = intro_value + 1
            changed = True
            continue

        rewritten.append(child)
        expected_next = None

    return rewritten if changed else children


def _first_tail_has_peer_moment_signal(node: IRNode) -> bool:
    text = irnode_to_text(node).strip().lower()
    return text.startswith("edellä 1 momentissa tarkoitet") or text.startswith("jos ")


def _split_intro_list_tail_moment_subsections(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Split multi-moment tail prose after a one-moment intro-list section payload.

    Some Finlex section payloads encode the first moment's intro and numbered
    kohdat correctly but leave following momentit as unnumbered CONTENT/WRAP_UP
    children inside the same first subsection wrapper.  A single generic
    first-moment reference is not enough to split, but multiple tail prose
    children after a closed numbered list give a local section-level witness
    that these are peer momentit.
    """
    subsection_indices = [
        idx for idx, child in enumerate(children) if child.kind == IRNodeKind.SUBSECTION
    ]
    if len(subsection_indices) != 1:
        return children

    idx = subsection_indices[0]
    subsection = children[idx]
    base_label = _numeric_label_value(subsection.label)
    if base_label is None:
        return children

    numbered_positions = [
        child_idx
        for child_idx, child in enumerate(subsection.children)
        if child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(child)
    ]
    if not numbered_positions or not any(
        child.kind == IRNodeKind.INTRO for child in subsection.children[: numbered_positions[0]]
    ):
        return children

    last_numbered_idx = numbered_positions[-1]
    paragraph_values = _paragraph_label_values(subsection)
    if paragraph_values != list(range(1, len(paragraph_values) + 1)):
        return children
    if not _paragraph_ends_with_terminal_punctuation(subsection.children[last_numbered_idx]):
        return children

    trailing = tuple(subsection.children[last_numbered_idx + 1 :])
    if len(trailing) < 2:
        return children
    if not all(child.kind in {IRNodeKind.CONTENT, IRNodeKind.WRAP_UP} for child in trailing):
        return children
    if not _first_tail_has_peer_moment_signal(trailing[0]):
        return children

    split_subsections: list[IRNode] = [
        IRNode(
            kind=subsection.kind,
            label=subsection.label,
            text=subsection.text,
            attrs=subsection.attrs,
            children=subsection.children[: last_numbered_idx + 1],
        )
    ]
    new_labels: list[str] = []
    for offset, node in enumerate(trailing, start=1):
        label = str(base_label + offset)
        attrs = dict(node.attrs)
        attrs["lawvm_source_normalization_rule"] = "fi_intro_list_tail_moment_split_v1"
        new_labels.append(label)
        split_subsections.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=label,
                attrs=attrs,
                children=(IRNode(kind=IRNodeKind.CONTENT, text=irnode_to_text(node).strip()),),
            )
        )

    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=BASE_INTRO_LIST_TAIL_MOMENT_SPLIT,
            basis=SourceNormalizationBasis.PROFILE_INVALID,
            before=(
                f"{_node_path_label(subsection)} carried {len(trailing)} unnumbered "
                "tail prose children after a closed intro-list moment"
            ),
            after=f"split tail prose into peer subsections {new_labels!r}",
            explanation=(
                "The source encoded later momentti prose as content/wrap-up children "
                "inside the first subsection after an intro plus consecutive numbered "
                "kohta list.  The first tail has a peer-moment signal (prior-moment "
                "anaphora or conditional Jos-clause) and multiple tail children "
                "follow, so they are split into peer momentti subsections rather "
                "than preserved as list wrap-up."
            ),
            path=parent_path + (_node_path_label(subsection),),
            confidence=0.94,
        )
    )

    return children[:idx] + split_subsections + children[idx + 1 :]


def _fold_section_scoped_item_style_subsections(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold section-scoped item-style subsection continuations into prior momentti."""
    if len(children) < 2:
        return children

    rewritten: List[IRNode] = []
    changed = False
    for child in children:
        if (
            child.kind != IRNodeKind.SUBSECTION
            or not rewritten
            or rewritten[-1].kind != IRNodeKind.SUBSECTION
            or not _is_item_style_subsection(child)
            or not _section_item_style_subsection_continues_previous(rewritten[-1], child)
        ):
            rewritten.append(child)
            continue

        previous = rewritten[-1]
        payload_nodes = _section_item_style_subsection_payload_nodes(child)
        rewritten[-1] = IRNode(
            kind=previous.kind,
            label=previous.label,
            text=previous.text,
            attrs=previous.attrs,
            children=tuple(previous.children) + payload_nodes,
        )
        num_child = next((c for c in child.children if c.kind == IRNodeKind.NUM), None)
        raw_num = (num_child.text or "").strip() if num_child else f"{child.label})"
        node_path = parent_path + (_node_path_label(child),)
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=SourceNormalizationKind.TAG_RECLASSIFY,
                basis=SourceNormalizationBasis.IMPOSSIBLE_NUMBERING,
                before=f"section-scoped subsection continuation with item-style num {raw_num!r}",
                after="folded into previous subsection as paragraph continuation",
                explanation=(
                    "The source encoded a kohta continuation as a sibling subsection. "
                    "Because its item label continues the preceding subsection's "
                    "paragraph sequence, it is folded into that preceding momentti; "
                    "letter-labelled children become alakohta children and trailing "
                    "digit-labelled children remain sibling kohdat."
                ),
                path=node_path,
                confidence=0.96,
            )
        )
        changed = True

    return rewritten if changed else children


def _is_lettered_paragraph(node: IRNode) -> bool:
    if node.kind != IRNodeKind.PARAGRAPH:
        return False
    label = _norm_num_token(str(node.label or ""))
    if _LETTER_LABEL_RE.match(label):
        return True
    num_child = next((child for child in node.children if child.kind == IRNodeKind.NUM), None)
    if num_child is None:
        return False
    raw = _norm_num_token((num_child.text or "").strip().rstrip(")"))
    return bool(_LETTER_LABEL_RE.match(raw))


def _subsection_paragraph_list_payload(node: IRNode) -> tuple[tuple[IRNode, ...], tuple[IRNode, ...]] | None:
    if node.kind != IRNodeKind.SUBSECTION:
        return None
    if any(child.kind == IRNodeKind.NUM for child in node.children):
        return None
    paragraphs: list[IRNode] = []
    omissions: list[IRNode] = []
    for child in node.children:
        if child.kind == IRNodeKind.PARAGRAPH and _is_lettered_paragraph(child):
            paragraphs.append(child)
            continue
        if child.kind == IRNodeKind.OMISSION:
            omissions.append(child)
            continue
        return None
    if len(paragraphs) < 2:
        return None
    labels = [_norm_num_token(str(paragraph.label or "")) for paragraph in paragraphs]
    if labels and labels[0] and labels[0] != "a":
        return None
    return tuple(paragraphs), tuple(omissions)


def _fold_unlabelled_paragraph_list_subsection_wrappers(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold an unlabelled paragraph-list wrapper into a preceding momentti.

    Finlex amendment XML sometimes splits one changed subsection into a
    content-only subsection followed by an unlabelled subsection carrying the
    ``a)``/``b)`` list and a section-level omission marker. The latter is not a
    valid peer momentti; the lettered paragraphs complete the preceding
    comma-ended moment while the omission remains section-local sparse context.
    """
    if len(children) < 2:
        return children

    rewritten: list[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION or i + 1 >= len(children):
            rewritten.append(child)
            i += 1
            continue

        payload = _subsection_paragraph_list_payload(children[i + 1])
        child_text = irnode_to_text(child).strip()
        if payload is None or not child_text.endswith(","):
            rewritten.append(child)
            i += 1
            continue

        paragraphs, omissions = payload
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(child.children) + paragraphs,
            )
        )
        rewritten.extend(omissions)
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_SECTION_ITEM_SUBSECTION_FOLD,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"unlabelled paragraph-list wrapper {_node_path_label(children[i + 1])} "
                    f"after comma-ended {_node_path_label(child)}"
                ),
                after=(
                    f"folded paragraphs {[paragraph.label for paragraph in paragraphs]!r} "
                    f"into {_node_path_label(child)}; kept {len(omissions)} omission marker(s) section-local"
                ),
                explanation=(
                    "The source split one subsection replacement across two "
                    "subsection wrappers: the first carries comma-ended prose and "
                    "the second carries only letter-labelled kohdat plus omission "
                    "context. The wrapper is therefore transport shape, not a peer "
                    "momentti."
                ),
                path=parent_path + (_node_path_label(children[i + 1]),),
                confidence=0.97,
            )
        )
        changed = True
        i += 2

    return rewritten if changed else children


def _content_item_subsection_payload(node: IRNode) -> tuple[int, IRNode] | None:
    if node.kind != IRNodeKind.SUBSECTION:
        return None
    if any(child.kind in {IRNodeKind.NUM, IRNodeKind.PARAGRAPH} for child in node.children):
        return None
    text = irnode_to_text(node).strip()
    match = _LEADING_DIGIT_ITEM_TEXT_RE.match(text)
    if match is None:
        return None
    label = match.group(1)
    item_text = match.group(2).strip()
    if not item_text:
        return None
    return (
        int(label),
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=label,
            attrs=node.attrs,
            children=(
                IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
                IRNode(kind=IRNodeKind.CONTENT, text=item_text),
            ),
        ),
    )


def _content_only_subsection_payload(node: IRNode) -> tuple[IRNode, ...] | None:
    if node.kind != IRNodeKind.SUBSECTION:
        return None
    if any(child.kind in {IRNodeKind.NUM, IRNodeKind.PARAGRAPH} for child in node.children):
        return None
    if _node_has_descendant_kind(node, IRNodeKind.TABLE):
        return None
    payload = tuple(child for child in node.children if irnode_to_text(child).strip())
    return payload or None


def _direct_section_item_carrier_label(node: IRNode) -> str | None:
    if node.kind != IRNodeKind.SUBSECTION:
        return None
    num_child = next((child for child in node.children if child.kind == IRNodeKind.NUM), None)
    if num_child is None:
        return None
    raw = (num_child.text or "").strip()
    compact = "".join(raw.split())
    if not compact.endswith(")"):
        return None
    num_label = _norm_num_token(compact[:-1])
    node_label = _norm_num_token(str(node.label or ""))
    if not num_label or node_label != num_label:
        return None
    return node_label


def _fold_sparse_section_item_subsection_payload(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold sparse section-level item carriers into the first moment wrapper."""
    subsections = [child for child in children if child.kind == IRNodeKind.SUBSECTION]
    if not subsections:
        return children
    item_labels = [_direct_section_item_carrier_label(child) for child in subsections]
    if any(label is None for label in item_labels):
        return children
    labels = [str(label) for label in item_labels if label is not None]
    if len(labels) == 1 and labels[0].isdigit():
        return children
    if not any(any(ch.isalpha() for ch in label) for label in labels) and labels == [
        str(idx) for idx in range(1, len(labels) + 1)
    ]:
        return children

    payload_nodes: list[IRNode] = []
    consumed_paths: list[str] = []
    for subsection in subsections:
        payload_nodes.extend(_section_item_style_subsection_payload_nodes(subsection))
        consumed_paths.append(_node_path_label(subsection))

    rewritten: List[IRNode] = []
    inserted = False
    for child in children:
        if child.kind == IRNodeKind.SUBSECTION:
            if not inserted:
                rewritten.append(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=tuple(payload_nodes),
                    )
                )
                inserted = True
            continue
        rewritten.append(child)

    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=BASE_SECTION_ITEM_SUBSECTION_FOLD,
            basis=SourceNormalizationBasis.PROFILE_INVALID,
            before=f"sparse section-level item carriers {consumed_paths!r}",
            after=(
                "folded into subsection:1 as paragraphs "
                f"{[node.label for node in payload_nodes if node.kind == IRNodeKind.PARAGRAPH]!r}"
            ),
            explanation=(
                "The source encoded a sparse set of section-level kohdat as "
                "subsection wrappers. Finnish momentti labels are numeric, while "
                "the wrapper num/label pair carries item notation such as '1 a)' "
                "or '5)'. The wrappers are therefore transport shape and are "
                "folded into the first moment's item list for payload binding."
            ),
            path=parent_path,
            confidence=0.95,
        )
    )
    return rewritten


def _append_to_last_numbered_paragraph(
    children: list[IRNode],
    payload: tuple[IRNode, ...],
) -> list[IRNode] | None:
    for idx in range(len(children) - 1, -1, -1):
        child = children[idx]
        if child.kind != IRNodeKind.PARAGRAPH or _numeric_label_value(child.label) is None:
            continue
        updated = list(children)
        updated[idx] = _append_item_continuation(child, payload)
        return updated
    return None


def _fold_section_content_item_subsection_run(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold content-only ``1)``/``2)`` subsection siblings into their intro moment."""
    if len(children) < 3:
        return children

    rewritten: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        if _node_has_descendant_kind(child, IRNodeKind.TABLE):
            rewritten.append(child)
            i += 1
            continue

        existing_values = _paragraph_label_values(child)
        if existing_values:
            if existing_values != list(range(1, len(existing_values) + 1)):
                rewritten.append(child)
                i += 1
                continue
            expected_value = max(existing_values) + 1
            min_folded = 1
        else:
            intro_text = irnode_to_text(child).strip()
            parent_has_container_numbering = any(
                segment.startswith(("part:", "chapter:")) for segment in parent_path
            )
            if not intro_text or intro_text.endswith((".", "!", "?", ":")) or parent_has_container_numbering:
                rewritten.append(child)
                i += 1
                continue
            expected_value = 1
            min_folded = 2

        base_children = list(child.children)
        folded_paragraphs: list[IRNode] = []
        consumed_paths: list[str] = []
        j = i + 1
        while j < len(children):
            candidate = children[j]
            payload = _content_item_subsection_payload(candidate)
            if payload is not None:
                value, paragraph = payload
                if value != expected_value:
                    break
                folded_paragraphs.append(paragraph)
                consumed_paths.append(_node_path_label(candidate))
                expected_value += 1
                j += 1
                continue

            continuation_payload = _content_only_subsection_payload(candidate)
            next_payload = (
                _content_item_subsection_payload(children[j + 1])
                if j + 1 < len(children)
                else None
            )
            if continuation_payload is None or next_payload is None or next_payload[0] != expected_value:
                break
            if folded_paragraphs:
                folded_paragraphs[-1] = _append_item_continuation(
                    folded_paragraphs[-1],
                    continuation_payload,
                )
            else:
                updated_base = _append_to_last_numbered_paragraph(
                    base_children,
                    continuation_payload,
                )
                if updated_base is None:
                    break
                base_children = updated_base
            consumed_paths.append(_node_path_label(candidate))
            j += 1

        if len(folded_paragraphs) < min_folded:
            rewritten.append(child)
            i += 1
            continue

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(base_children) + tuple(folded_paragraphs),
            )
        )

        next_label = _numeric_label_value(child.label)
        relabelled: list[str] = []
        if next_label is not None:
            next_label += 1
        for rest in children[j:]:
            if next_label is not None and rest.kind == IRNodeKind.SUBSECTION and _numeric_label_value(rest.label) is not None:
                old_label = str(rest.label)
                new_label = str(next_label)
                relabelled.append(f"{old_label}->{new_label}")
                rest = IRNode(
                    kind=rest.kind,
                    label=new_label,
                    text=rest.text,
                    attrs=rest.attrs,
                    children=rest.children,
                )
                next_label += 1
            rewritten.append(rest)

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_SECTION_ITEM_SUBSECTION_FOLD,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"content-only item subsection run {consumed_paths!r} after "
                    f"{_node_path_label(child)}"
                ),
                after=(
                    f"folded into {_node_path_label(child)} as paragraphs "
                    f"{[p.label for p in folded_paragraphs]!r}; relabelled true "
                    f"subsections {relabelled!r}"
                ),
                explanation=(
                    "The source encoded one moment's numbered kohdat as sibling "
                    "subsection elements whose text begins with consecutive item "
                    "markers. Because the preceding subsection is either a "
                    "sentence-continuation intro in a flat source section "
                    "followed by a 1..N item run or a list-bearing moment "
                    "followed by its next consecutive items, those transport "
                    "wrappers are folded into that moment and later true "
                    "subsections are relabelled in document order."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.95,
            )
        )
        changed = True
        i = len(children)

    return rewritten if changed else children


def _item_num_value(node: IRNode) -> int | None:
    num_child = next((child for child in node.children if child.kind == IRNodeKind.NUM), None)
    if num_child is None:
        return None
    raw = (num_child.text or "").strip()
    if not _ITEM_NUM_RE.match(raw):
        return None
    return _numeric_label_value(raw.rstrip(")"))


def _paragraph_label_values(subsection: IRNode) -> list[int]:
    return [
        value
        for child in subsection.children
        if child.kind == IRNodeKind.PARAGRAPH
        for value in (_numeric_label_value(child.label),)
        if value is not None
    ]


def _node_text_starts_with_dash_bullet(node: IRNode) -> bool:
    text = irnode_to_text(node).lstrip()
    return text.startswith(("-", "–", "—", "−"))


def _subsection_resumes_item_run_after_dash_continuation(
    subsection: IRNode,
    expected_next_value: int,
) -> bool:
    """Return True for a synthetic subsection carrying dash-list item tails.

    Finlex occasionally splits a single definition-list moment so that the
    first numbered item remains in one subsection and following dash bullets
    plus items 2..N appear in a synthetic sibling subsection.  The witness is
    narrow: the sibling must start with dash-bullet continuation text before
    the expected next numbered item appears.
    """
    if subsection.kind != IRNodeKind.SUBSECTION:
        return False

    saw_dash_continuation = False
    for child in subsection.children:
        numbered_value = _numbered_paragraph_value(child)
        if numbered_value is not None:
            return saw_dash_continuation and numbered_value == expected_next_value
        if child.kind in (IRNodeKind.INTRO, IRNodeKind.PARAGRAPH) and _node_text_starts_with_dash_bullet(child):
            saw_dash_continuation = True
            continue
        if child.kind == IRNodeKind.NUM:
            continue
        if irnode_to_text(child).strip():
            return False
    return False


def _numbered_paragraph_value(paragraph: IRNode) -> int | None:
    if paragraph.kind != IRNodeKind.PARAGRAPH or not _paragraph_has_num_child(paragraph):
        return None
    return _numeric_label_value(paragraph.label)


def _paragraph_continuation_children(paragraph: IRNode) -> tuple[IRNode, ...]:
    payload = tuple(child for child in paragraph.children if child.kind != IRNodeKind.NUM)
    if payload:
        return payload
    if paragraph.text:
        return (IRNode(kind=IRNodeKind.CONTENT, text=paragraph.text),)
    return ()


def _append_item_continuation(item: IRNode, nodes: tuple[IRNode, ...]) -> IRNode:
    if not nodes:
        return item
    return IRNode(
        kind=item.kind,
        label=item.label,
        text=item.text,
        attrs=item.attrs,
        children=tuple(item.children) + nodes,
    )


def _subsection_has_numbered_paragraphs(subsection: IRNode) -> bool:
    return any(_numbered_paragraph_value(child) is not None for child in subsection.children)


def _subsection_has_payload_before_first_numbered_paragraph(subsection: IRNode) -> bool:
    """True when a carrier has substantive child payload before its first numbered item."""
    for child in subsection.children:
        if child.kind == IRNodeKind.NUM:
            continue
        if _numbered_paragraph_value(child) is not None:
            return False
        if irnode_to_text(child).strip():
            return True
    return False


def _fold_item_carrier_into_paragraphs(
    subsection: IRNode,
    paragraphs: list[IRNode],
) -> bool:
    """Append one malformed section-level item carrier into paragraph items."""
    direct_item_value = _item_num_value(subsection)
    if direct_item_value is None and not _subsection_has_numbered_paragraphs(subsection):
        return False
    if direct_item_value is None and not paragraphs:
        return False

    consumed = False
    if direct_item_value is not None:
        num_child = next(child for child in subsection.children if child.kind == IRNodeKind.NUM)
        item_children: list[IRNode] = [num_child]
        for child in subsection.children:
            if child.kind == IRNodeKind.NUM:
                continue
            if child.kind == IRNodeKind.PARAGRAPH and _numbered_paragraph_value(child) is not None:
                break
            if child.kind == IRNodeKind.PARAGRAPH:
                item_children.extend(_paragraph_continuation_children(child))
            else:
                item_children.append(child)
        paragraphs.append(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=str(direct_item_value),
                attrs=subsection.attrs,
                children=tuple(item_children),
            )
        )
        consumed = True

    seen_numbered_paragraph = False
    for child in subsection.children:
        if child.kind == IRNodeKind.NUM:
            continue
        numbered_value = _numbered_paragraph_value(child)
        if numbered_value is not None:
            seen_numbered_paragraph = True
            paragraphs.append(child)
            consumed = True
            continue
        if direct_item_value is not None and not seen_numbered_paragraph:
            continue
        if child.kind == IRNodeKind.PARAGRAPH:
            continuation = _paragraph_continuation_children(child)
        else:
            continuation = (child,)
        if paragraphs:
            paragraphs[-1] = _append_item_continuation(paragraphs[-1], continuation)

    return consumed


def _section_item_connector_payload(subsection: IRNode) -> tuple[IRNode, ...] | None:
    """Return payload for a connector-only subsection inside a split item run."""
    if subsection.kind != IRNodeKind.SUBSECTION:
        return None
    if any(child.kind in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.INTRO} for child in subsection.children):
        return None
    if any(child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(child) for child in subsection.children):
        return None
    payload = tuple(child for child in subsection.children if irnode_to_text(child).strip())
    if not payload:
        return None
    text = " ".join(irnode_to_text(child).strip() for child in payload).casefold()
    if text not in {"ja", "sekä", "tai", "taikka"}:
        return None
    return payload


def _subsection_can_start_item_run_at(
    subsection: IRNode | None,
    expected_next_value: int,
) -> bool:
    if subsection is None or subsection.kind != IRNodeKind.SUBSECTION or _is_item_style_subsection(subsection):
        return False
    direct_item_value = _item_num_value(subsection)
    if direct_item_value == expected_next_value:
        return True
    resumes_after_dash = _subsection_resumes_item_run_after_dash_continuation(
        subsection,
        expected_next_value,
    )
    if direct_item_value is None and _subsection_has_payload_before_first_numbered_paragraph(subsection):
        return resumes_after_dash
    paragraph_values = [
        value
        for child in subsection.children
        for value in (_numbered_paragraph_value(child),)
        if value is not None
    ]
    if paragraph_values and paragraph_values == list(
        range(expected_next_value, expected_next_value + len(paragraph_values))
    ):
        return True
    return resumes_after_dash


def _subsection_can_start_item_run_after_connector(
    subsection: IRNode | None,
    expected_next_value: int,
) -> bool:
    if _subsection_can_start_item_run_at(subsection, expected_next_value):
        return True
    if subsection is None or subsection.kind != IRNodeKind.SUBSECTION or _is_item_style_subsection(subsection):
        return False
    paragraph_values = [
        value
        for child in subsection.children
        for value in (_numbered_paragraph_value(child),)
        if value is not None
    ]
    if paragraph_values and paragraph_values == list(
        range(expected_next_value, expected_next_value + len(paragraph_values))
    ):
        return True
    return False


def _paragraph_labels_are_consecutive(paragraphs: list[IRNode]) -> bool:
    values = [
        value
        for paragraph in paragraphs
        for value in (_numeric_label_value(paragraph.label),)
        if value is not None
    ]
    return values == list(range(1, len(values) + 1))


def _subsection_is_intro_only_open_list_host(subsection: IRNode, parent_path: Tuple[str, ...]) -> bool:
    if subsection.kind != IRNodeKind.SUBSECTION:
        return False
    if _is_item_style_subsection(subsection):
        return False
    if any(child.kind == IRNodeKind.PARAGRAPH for child in subsection.children):
        return False
    if _node_has_descendant_kind(subsection, IRNodeKind.TABLE):
        return False
    text = irnode_to_text(subsection).strip()
    return bool(text) and not text.endswith((".", "!", "?"))


def _subsection_paragraph_item_run(subsection: IRNode) -> list[IRNode]:
    if subsection.kind != IRNodeKind.SUBSECTION or _is_item_style_subsection(subsection):
        return []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    if not paragraphs:
        return []
    if any(child.kind not in {IRNodeKind.NUM, IRNodeKind.PARAGRAPH} for child in subsection.children):
        return []
    return paragraphs if _paragraph_labels_are_consecutive(paragraphs) else []


def _subsection_item_num_paragraph_run(subsection: IRNode) -> tuple[list[IRNode], list[IRNode]]:
    """Return paragraphs from a ``1)`` carrier plus section-local omissions."""
    if subsection.kind != IRNodeKind.SUBSECTION or _item_num_value(subsection) != 1:
        return [], []
    paragraphs: list[IRNode] = []
    omissions: list[IRNode] = []
    for child in subsection.children:
        if child.kind == IRNodeKind.OMISSION:
            omissions.append(child)
            continue
        if child.kind == IRNodeKind.NUM:
            continue
        if child.kind == IRNodeKind.INTRO:
            num_child = next((candidate for candidate in subsection.children if candidate.kind == IRNodeKind.NUM), None)
            if num_child is None:
                return [], []
            paragraphs.append(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="1",
                    attrs=subsection.attrs,
                    children=(
                        num_child,
                        IRNode(kind=IRNodeKind.CONTENT, text=child.text, attrs=child.attrs, children=child.children),
                    ),
                )
            )
            continue
        numbered_value = _numbered_paragraph_value(child)
        if numbered_value is not None:
            paragraphs.append(child)
            continue
        if irnode_to_text(child).strip():
            return [], []
    if len(paragraphs) < 2 or not _paragraph_labels_are_consecutive(paragraphs):
        return [], []
    return paragraphs, omissions


def _fold_intro_only_subsection_item_list_wrapper(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold an intro-only moment plus sibling 1..N paragraph wrapper."""
    if len(children) < 3:
        return children

    rewritten: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if not _subsection_is_intro_only_open_list_host(child, parent_path):
            rewritten.append(child)
            i += 1
            continue
        if i + 1 >= len(children):
            rewritten.append(child)
            i += 1
            continue
        folded_paragraphs = _subsection_paragraph_item_run(children[i + 1])
        folded_omissions: list[IRNode] = []
        if not folded_paragraphs:
            folded_paragraphs, folded_omissions = _subsection_item_num_paragraph_run(children[i + 1])
        if not folded_paragraphs:
            rewritten.append(child)
            i += 1
            continue

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(child.children) + tuple(folded_paragraphs),
            )
        )
        rewritten.extend(folded_omissions)

        next_label = (_numeric_label_value(child.label) or 0) + 1
        relabelled: list[str] = []
        for rest in children[i + 2:]:
            if rest.kind == IRNodeKind.SUBSECTION and _numeric_label_value(rest.label) is not None:
                old_label = str(rest.label)
                new_label = str(next_label)
                relabelled.append(f"{old_label}->{new_label}")
                rest = IRNode(
                    kind=rest.kind,
                    label=new_label,
                    text=rest.text,
                    attrs=rest.attrs,
                    children=rest.children,
                )
                next_label += 1
            rewritten.append(rest)

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_SECTION_ITEM_SUBSECTION_FOLD,
                basis=SourceNormalizationBasis.IMPOSSIBLE_NUMBERING,
                before=(
                    f"intro-only subsection {_node_path_label(child)} followed by "
                    f"paragraph item wrapper {_node_path_label(children[i + 1])}"
                ),
                after=(
                    f"folded into {_node_path_label(child)} as paragraphs "
                    f"{[p.label for p in folded_paragraphs]!r}; relabelled true "
                    f"subsections {relabelled!r}"
                ),
                explanation=(
                    "The source encoded one moment's lead sentence and numbered "
                    "kohdat as sibling subsection elements. Because the first "
                    "subsection is an open list lead and the next wrapper carries "
                    "a complete consecutive 1..N paragraph sequence, the wrapper "
                    "is transport shape rather than a peer momentti."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.95,
            )
        )
        changed = True
        i = len(children)

    return rewritten if changed else children


def _fold_section_item_subsection_run(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold a section-level item run misencoded as sibling subsections."""
    if len(children) < 3:
        return children

    rewritten: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue

        base_paragraphs = [c for c in child.children if c.kind == IRNodeKind.PARAGRAPH]
        base_values = _paragraph_label_values(child)
        if not base_values or base_values != list(range(1, len(base_values) + 1)):
            rewritten.append(child)
            i += 1
            continue

        next_child = children[i + 1] if i + 1 < len(children) else None
        expected_next_value = max(base_values) + 1
        starts_with_next_item = _subsection_can_start_item_run_at(next_child, expected_next_value)
        starts_with_connector_then_item = (
            next_child is not None
            and _section_item_connector_payload(next_child) is not None
            and _subsection_can_start_item_run_after_connector(
                children[i + 2] if i + 2 < len(children) else None,
                expected_next_value,
            )
        )
        if not starts_with_next_item and not starts_with_connector_then_item:
            rewritten.append(child)
            i += 1
            continue

        folded_paragraphs = list(base_paragraphs)
        consumed_paths: list[str] = []
        j = i + 1
        while j < len(children):
            candidate = children[j]
            if candidate.kind != IRNodeKind.SUBSECTION or _is_item_style_subsection(candidate):
                break
            connector_payload = _section_item_connector_payload(candidate)
            if connector_payload is not None:
                following = children[j + 1] if j + 1 < len(children) else None
                expected_after_connector = len(folded_paragraphs) + 1
                if not folded_paragraphs or not _subsection_can_start_item_run_after_connector(
                    following,
                    expected_after_connector,
                ):
                    break
                folded_paragraphs[-1] = _append_item_continuation(
                    folded_paragraphs[-1],
                    connector_payload,
                )
                consumed_paths.append(_node_path_label(candidate))
                j += 1
                continue
            trial = list(folded_paragraphs)
            if not _fold_item_carrier_into_paragraphs(candidate, trial):
                break
            if len(trial) == len(folded_paragraphs) or not _paragraph_labels_are_consecutive(trial):
                break
            folded_paragraphs = trial
            consumed_paths.append(_node_path_label(candidate))
            j += 1

        if not consumed_paths:
            rewritten.append(child)
            i += 1
            continue

        non_paragraph_children = [c for c in child.children if c.kind != IRNodeKind.PARAGRAPH]
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(non_paragraph_children + folded_paragraphs),
            )
        )

        next_label = (_numeric_label_value(child.label) or 0) + 1
        relabelled: list[str] = []
        for rest in children[j:]:
            if rest.kind == IRNodeKind.SUBSECTION and _numeric_label_value(rest.label) is not None:
                old_label = str(rest.label)
                new_label = str(next_label)
                relabelled.append(f"{old_label}->{new_label}")
                rest = IRNode(
                    kind=rest.kind,
                    label=new_label,
                    text=rest.text,
                    attrs=rest.attrs,
                    children=rest.children,
                )
                next_label += 1
            rewritten.append(rest)

        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_SECTION_ITEM_SUBSECTION_FOLD,
                basis=SourceNormalizationBasis.IMPOSSIBLE_NUMBERING,
                before=(
                    f"section-level subsection item carriers {consumed_paths!r} "
                    f"after {_node_path_label(child)}"
                ),
                after=(
                    f"folded into {_node_path_label(child)} as paragraphs "
                    f"{[p.label for p in folded_paragraphs]!r}; relabelled true "
                    f"subsections {relabelled!r}"
                ),
                explanation=(
                    "The source encoded one moment's numbered kohdat as sibling "
                    "subsection elements. Because the carried item labels form a "
                    "single consecutive paragraph sequence under the preceding "
                    "list-bearing moment, those carriers are folded into that "
                    "moment and following true subsections are relabelled in "
                    "document order."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.95,
            )
        )
        changed = True
        i = len(children)

    return rewritten if changed else children


def _node_has_descendant_kind(node: IRNode, kind: IRNodeKind) -> bool:
    return any(child.kind == kind or _node_has_descendant_kind(child, kind) for child in node.children)


def _subsection_has_omission_marker(node: IRNode) -> bool:
    return any(
        child.kind == IRNodeKind.OMISSION
        or (child.kind == IRNodeKind.HCONTAINER and child.attrs.get("name") == "omission")
        for child in node.children
    )


def _has_source_eid(node: IRNode) -> bool:
    return bool(node.attrs.get("eId") or node.attrs.get("lawvm_source_subsection_eid"))


def _is_table_note_continuation_subsection(node: IRNode) -> bool:
    """Synthetic source wrapper for table notes/prose, not a real momentti."""
    if node.kind != IRNodeKind.SUBSECTION or _has_source_eid(node):
        return False
    if any(child.kind == IRNodeKind.NUM for child in node.children):
        return False
    if any(child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(child) for child in node.children):
        return False
    return bool(irnode_to_text(node).strip())


def _starts_table_note_run(node: IRNode) -> bool:
    leading = "".join(irnode_to_text(node).lstrip()[:24].split())
    return leading.startswith(("(*)", "(**)", "(***)", "(****)"))


def _numeric_table_note_label(node: IRNode) -> str | None:
    """Return a leading numeric table-note marker label, e.g. ``"1"``."""
    if node.kind != IRNodeKind.SUBSECTION:
        return None
    if any(child.kind == IRNodeKind.NUM for child in node.children):
        return None
    if any(child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(child) for child in node.children):
        return None
    text = irnode_to_text(node).lstrip()
    if not text or not text[0].isdigit():
        return None
    i = 0
    while i < len(text) and text[i].isdigit():
        i += 1
    label = text[:i]
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != ")":
        return None
    return label


def _table_text_has_numeric_note_marker(node: IRNode, label: str) -> bool:
    marker = f"{label})"
    pending = [node]
    while pending:
        current = pending.pop()
        if current.kind == IRNodeKind.TABLE:
            compact = "".join(irnode_to_text(current).split())
            if marker in compact:
                return True
        pending.extend(current.children)
    return False


def _starts_numeric_table_note_run(table_host: IRNode, node: IRNode) -> bool:
    label = _numeric_table_note_label(node)
    return label is not None and _table_text_has_numeric_note_marker(table_host, label)


def _fold_table_note_subsections_into_previous_moment(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Fold synthetic table-note subsection wrappers into a table-bearing moment.

    Some amendment payload XML encodes one targeted moment as a table-bearing
    subsection followed by source-synthetic subsection wrappers for footnotes,
    dash bullets, and final prose.  The source johto owns the whole moment; the
    wrappers are transport shape, not separate momentit.
    """
    if len(children) < 2:
        return children

    rewritten: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if (
            child.kind != IRNodeKind.SUBSECTION
            or not _node_has_descendant_kind(child, IRNodeKind.TABLE)
        ):
            rewritten.append(child)
            i += 1
            continue

        j = i + 1
        if j >= len(children):
            rewritten.append(child)
            i += 1
            continue
        starts_star_note_run = _subsection_has_omission_marker(child) and _starts_table_note_run(children[j])
        starts_numeric_note_run = _starts_numeric_table_note_run(child, children[j])
        if not starts_star_note_run and not starts_numeric_note_run:
            rewritten.append(child)
            i += 1
            continue

        continuation_children: list[IRNode] = []
        consumed_paths: list[str] = []
        while j < len(children):
            continuation = children[j]
            if starts_star_note_run:
                if not _is_table_note_continuation_subsection(continuation):
                    break
            else:
                label = _numeric_table_note_label(continuation)
                if label is None or not _table_text_has_numeric_note_marker(child, label):
                    break
            continuation_children.extend(continuation.children)
            consumed_paths.append(_node_path_label(continuation))
            j += 1

        if not continuation_children:
            rewritten.append(child)
            i += 1
            continue

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(child.children) + tuple(continuation_children),
            )
        )
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_TABLE_NOTE_SUBSECTION_FOLD,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"table-bearing {_node_path_label(child)} followed by synthetic "
                    f"table-note subsection wrappers {consumed_paths!r}"
                ),
                after=(
                    f"folded {len(consumed_paths)} table-note/prose wrappers into "
                    f"{_node_path_label(child)}"
                ),
                explanation=(
                    "The source encoded footnotes and concluding prose for one "
                    "table-bearing moment as sibling subsection wrappers without "
                    "source eIds or numbered paragraph children.  Fold those "
                    "transport wrappers into the preceding table-bearing moment "
                    "so the targeted subsection replacement preserves the whole "
                    "source-owned moment payload."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.96,
            )
        )
        changed = True
        i = j

    return rewritten if changed else children


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
    paragraph, not under paragraph 4.  A terminal digit-labelled subparagraph is
    also split when the parent paragraph and the immediately following sibling
    paragraph prove the same consecutive item sequence.
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

    def _next_paragraph_numeric_value(start_idx: int) -> int | None:
        for sibling in children[start_idx:]:
            if sibling.kind != IRNodeKind.PARAGRAPH:
                return None
            value = _numeric_label_value(sibling.label)
            if value is not None:
                return value
            return None
        return None

    def _terminal_digit_reset_is_peer_item(
        parent: IRNode,
        reset_node: IRNode,
        next_sibling_value: int | None,
    ) -> bool:
        parent_value = _numeric_label_value(parent.label)
        reset_value = _numeric_label_value(reset_node.label)
        return (
            parent_value is not None
            and reset_value is not None
            and reset_value == parent_value + 1
            and next_sibling_value == reset_value + 1
        )

    rewritten: List[IRNode] = []
    for child_pos, child in enumerate(children):
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
            if trailing and not all(_is_letter_subparagraph(node) for node in trailing):
                continue
            if not trailing and not _terminal_digit_reset_is_peer_item(
                child,
                gc,
                _next_paragraph_numeric_value(child_pos + 1),
            ):
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


def _find_glued_coordinator_item_split(text: str, expected_label: int) -> tuple[str, str, str] | None:
    """Find ``; seka5)`` / ``; sekä5)`` / ``; ja5)`` transport glue."""
    lowered = text.casefold()
    for idx, ch in enumerate(text):
        if ch not in ";.":
            continue
        cursor = idx + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        for coordinator in _GLUED_ITEM_COORDINATORS:
            if not lowered.startswith(coordinator, cursor):
                continue
            digit_start = cursor + len(coordinator)
            digit_end = digit_start
            while digit_end < len(text) and text[digit_end].isdigit():
                digit_end += 1
            if digit_end == digit_start or digit_end >= len(text) or text[digit_end] != ")":
                continue
            label = text[digit_start:digit_end]
            if int(label) != expected_label:
                continue
            left = text[: idx + 1].strip()
            right = text[digit_end + 1 :].strip()
            if left and right:
                return left, label, right
    return None


def _split_glued_coordinator_item_paragraphs(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Split a source-transport-glued item marker out of paragraph content."""
    rewritten: list[IRNode] = []
    changed = False
    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue
        current_label = _numeric_label_value(child.label)
        if current_label is None:
            rewritten.append(child)
            continue
        expected_label = current_label + 1
        split_child_index: int | None = None
        split_payload: tuple[str, str, str] | None = None
        for child_index, grandchild in enumerate(child.children):
            if grandchild.kind != IRNodeKind.CONTENT or not grandchild.text:
                continue
            split_payload = _find_glued_coordinator_item_split(grandchild.text, expected_label)
            if split_payload is not None:
                split_child_index = child_index
                break
        if split_child_index is None or split_payload is None:
            rewritten.append(child)
            continue

        left_text, new_label, right_text = split_payload
        left_content = child.children[split_child_index]
        left_children = list(child.children[:split_child_index])
        left_children.append(
            IRNode(
                kind=left_content.kind,
                label=left_content.label,
                text=left_text,
                attrs=left_content.attrs,
                children=left_content.children,
            )
        )
        new_paragraph_children = [
            IRNode(kind=IRNodeKind.NUM, text=f"{new_label})"),
            IRNode(kind=IRNodeKind.CONTENT, text=right_text, attrs=left_content.attrs),
        ]
        new_paragraph_children.extend(child.children[split_child_index + 1 :])

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(left_children),
            )
        )
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=new_label,
                attrs=child.attrs,
                children=tuple(new_paragraph_children),
            )
        )
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_DIGIT_RESET_SPLIT,
                basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
                before=(
                    f"paragraph {child.label or '?'} contains coordinator-glued "
                    f"item marker {new_label}) inside content"
                ),
                after=f"split into sibling paragraph {new_label}",
                explanation=(
                    "The source glued a coordinating word directly to the next "
                    "numbered item marker inside a paragraph payload. The marker "
                    "continues the local item sequence, so it is transport glue "
                    "rather than legal prose."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.96,
            )
        )
        changed = True

    return rewritten if changed else children


def _node_without_kind_text(node: IRNode, excluded_kind: IRNodeKind) -> str:
    return irnode_to_text(
        IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=node.attrs,
            children=tuple(child for child in node.children if child.kind is not excluded_kind),
        )
    ).strip()


def _closed_paragraph_before_misnested_subparagraphs(paragraph: IRNode) -> bool:
    text = _node_without_kind_text(paragraph, IRNodeKind.SUBPARAGRAPH)
    return bool(text) and text[-1] in ".!?"


def _is_unnumbered_subparagraph_payload(node: IRNode) -> bool:
    return node.kind is IRNodeKind.SUBPARAGRAPH and node.label is None and not _paragraph_has_num(node)


def _subparagraph_as_content(node: IRNode) -> IRNode:
    children = tuple(child for child in node.children if child.kind is not IRNodeKind.NUM)
    if children:
        return IRNode(kind=IRNodeKind.CONTENT, attrs=node.attrs, children=children)
    return IRNode(kind=IRNodeKind.CONTENT, text=irnode_to_text(node).strip(), attrs=node.attrs)


def _subparagraph_as_paragraph(node: IRNode) -> IRNode | None:
    value = _numeric_label_value(node.label)
    if value is None:
        return None
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=str(value),
        text=node.text,
        attrs=node.attrs,
        children=node.children,
    )


def _relabel_subsection(node: IRNode, label: int) -> IRNode:
    if node.kind is not IRNodeKind.SUBSECTION:
        return node
    if _numeric_label_value(node.label) is None:
        return node
    if node.label == str(label):
        return node
    attrs = dict(node.attrs)
    attrs.setdefault("lawvm_source_subsection_label", str(node.label))
    attrs["lawvm_source_normalization_rule"] = "fi_unnumbered_subparagraph_moment_split_v1"
    return IRNode(
        kind=node.kind,
        label=str(label),
        text=node.text,
        attrs=attrs,
        children=node.children,
    )


def _split_unnumbered_subparagraph_moment_payloads(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Split a peer moment misnested as an unnumbered subparagraph under an item.

    Finlex AKN sometimes places a later momentti under the final numbered kohta
    as an unnumbered ``subparagraph``.  When the containing kohta is already a
    closed sentence, that child cannot be a normal alakohta introduction.  If
    the child is followed by numeric subparagraphs, those become kohdat of the
    new peer moment; immediate numbered paragraph siblings continuing the same
    run are carried with it.
    """
    if len(children) < 1:
        return children

    rewritten: list[IRNode] = []
    changed = False
    relabel_from: int | None = None
    idx = 0
    while idx < len(children):
        child = children[idx]
        if child.kind is not IRNodeKind.SUBSECTION:
            rewritten.append(child)
            idx += 1
            continue

        child_label_value = _numeric_label_value(child.label)
        split_result = _split_one_subsection_unnumbered_subparagraph_payload(child)
        if split_result is None or child_label_value is None:
            if relabel_from is not None and child_label_value is not None:
                rewritten.append(_relabel_subsection(child, relabel_from))
                relabel_from += 1
            else:
                rewritten.append(child)
            idx += 1
            continue

        trimmed_subsection, new_payload_children, source_paragraph_label, consumed_positions = split_result
        next_label = child_label_value + 1
        expected_item = _next_expected_item_label(new_payload_children)
        remaining_children = list(trimmed_subsection.children)

        # Carry immediately following numbered paragraph siblings into the new
        # peer moment when they continue the numeric run started inside the
        # misnested subparagraph payload.
        if expected_item is not None:
            kept_remaining: list[IRNode] = []
            for pos, node in enumerate(remaining_children):
                if pos <= max(consumed_positions):
                    kept_remaining.append(node)
                    continue
                if node.kind is not IRNodeKind.PARAGRAPH:
                    kept_remaining.append(node)
                    continue
                value = _numeric_label_value(node.label)
                if value == expected_item:
                    new_payload_children.append(node)
                    consumed_positions.append(pos)
                    expected_item += 1
                    continue
                kept_remaining.append(node)
            remaining_children = kept_remaining

        trimmed_subsection = IRNode(
            kind=trimmed_subsection.kind,
            label=trimmed_subsection.label,
            text=trimmed_subsection.text,
            attrs=trimmed_subsection.attrs,
            children=tuple(remaining_children),
        )
        new_subsection = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(next_label),
            attrs={"lawvm_source_normalization_rule": "fi_unnumbered_subparagraph_moment_split_v1"},
            children=tuple(new_payload_children),
        )
        rewritten.append(trimmed_subsection)
        rewritten.append(new_subsection)
        facts.append(
            SourceNormalizationFact(
                statute_id=statute_id,
                kind=BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT,
                basis=SourceNormalizationBasis.PROFILE_INVALID,
                before=(
                    f"{_node_path_label(child)} paragraph {source_paragraph_label or '?'} "
                    "contained an unnumbered subparagraph payload under a closed item"
                ),
                after=f"split payload into peer subsection:{next_label}",
                explanation=(
                    "The source nested a later momentti payload under a numbered kohta "
                    "as an unnumbered subparagraph even though the kohta's own text is "
                    "a closed sentence. The unnumbered payload is split into the next "
                    "peer subsection; any numeric subparagraph run and immediately "
                    "following numbered paragraph siblings are carried as that new "
                    "moment's kohdat."
                ),
                path=parent_path + (_node_path_label(child),),
                confidence=0.95,
            )
        )
        relabel_from = next_label + 1
        changed = True
        idx += 1

    return rewritten if changed else children


def _split_one_subsection_unnumbered_subparagraph_payload(
    subsection: IRNode,
) -> tuple[IRNode, list[IRNode], str | None, list[int]] | None:
    new_subsection_children: list[IRNode] = []
    for child_pos, child in enumerate(subsection.children):
        if child.kind is not IRNodeKind.PARAGRAPH:
            continue
        subparagraphs = [node for node in child.children if node.kind is IRNodeKind.SUBPARAGRAPH]
        if not subparagraphs:
            continue
        first = subparagraphs[0]
        if not _is_unnumbered_subparagraph_payload(first):
            continue
        if not _closed_paragraph_before_misnested_subparagraphs(child):
            continue

        payload_intro = _subparagraph_as_content(first)
        numeric_payload: list[IRNode] = []
        for node in subparagraphs[1:]:
            paragraph = _subparagraph_as_paragraph(node)
            if paragraph is None:
                return None
            numeric_payload.append(paragraph)

        if numeric_payload and irnode_to_text(payload_intro).strip().endswith(":"):
            new_subsection_children.append(
                IRNode(kind=IRNodeKind.INTRO, text=irnode_to_text(payload_intro).strip(), attrs=payload_intro.attrs)
            )
            new_subsection_children.extend(numeric_payload)
        elif numeric_payload:
            return None
        else:
            new_subsection_children.append(payload_intro)

        trimmed_paragraph = IRNode(
            kind=child.kind,
            label=child.label,
            text=child.text,
            attrs=child.attrs,
            children=tuple(node for node in child.children if node.kind is not IRNodeKind.SUBPARAGRAPH),
        )
        trimmed_children = list(subsection.children)
        trimmed_children[child_pos] = trimmed_paragraph
        return (
            IRNode(
                kind=subsection.kind,
                label=subsection.label,
                text=subsection.text,
                attrs=subsection.attrs,
                children=tuple(trimmed_children),
            ),
            new_subsection_children,
            child.label,
            [child_pos],
        )
    return None


def _next_expected_item_label(nodes: list[IRNode]) -> int | None:
    values = [
        value
        for node in nodes
        if node.kind is IRNodeKind.PARAGRAPH
        for value in (_numeric_label_value(node.label),)
        if value is not None
    ]
    if not values:
        return None
    if values != list(range(1, len(values) + 1)):
        return None
    return values[-1] + 1


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: image, note, footnote, authorialNote blocks
# ---------------------------------------------------------------------------

_EDITORIAL_BLOCK_NAMES = frozenset({"image", "note", "footnote", "noteAuthorial"})


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

    shift_subsection_labels_from: int | None = None
    for idx, child in enumerate(children):
        if (
            shift_subsection_labels_from is not None
            and child.kind == IRNodeKind.SUBSECTION
            and child.label is not None
            and _norm_num_token(child.label).isdigit()
            and int(_norm_num_token(child.label)) >= shift_subsection_labels_from
        ):
            new_label = str(int(_norm_num_token(child.label)) + 1)
            rewritten.append(
                IRNode(
                    kind=child.kind,
                    label=new_label,
                    text=child.text,
                    attrs=child.attrs,
                    children=child.children,
                )
            )
            changed = True
            continue
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

        child_label_norm = _norm_num_token(child.label or "")
        split_label: str | None = None
        if child_label_norm.isdigit():
            split_label = str(int(child_label_norm) + 1)
            next_subsection = next(
                (
                    candidate
                    for candidate in children[idx + 1 :]
                    if candidate.kind == IRNodeKind.SUBSECTION and candidate.label is not None
                ),
                None,
            )
            if (
                next_subsection is not None
                and _norm_num_token(next_subsection.label or "").isdigit()
                and int(_norm_num_token(next_subsection.label or "")) == int(split_label)
            ):
                shift_subsection_labels_from = int(split_label)

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
                label=split_label,
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
                    + (f"; split subsection labelled {split_label}" if split_label is not None else "")
                ),
                explanation=(
                    "The source encoded two legal moments inside one subsection. "
                    "The first sentence is a complete standalone moment, while the "
                    "following colon-ended paragraph introduces a fresh numbered list. "
                    "Split the malformed subsection at the moment boundary. When the "
                    "enclosing subsection has a numeric label, the second moment receives "
                    "the next consecutive label; following colliding subsection labels are "
                    "shifted as part of the same local numbering repair."
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


def _reclassify_leading_unnumbered_item_intro(
    children: List[IRNode],
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> List[IRNode]:
    """Convert a malformed leading paragraph into an INTRO before an item run."""
    if len(children) < 3:
        return children
    lead = children[0]
    if lead.kind != IRNodeKind.PARAGRAPH or _paragraph_has_num_child(lead):
        return children
    if _paragraph_has_subparagraph_children(lead):
        return children
    lead_text = irnode_to_text(lead).strip()
    if not lead_text.endswith(":"):
        return children
    numbered = [
        child
        for child in children[1:]
        if child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num_child(child)
    ]
    if len(numbered) < 2:
        return children
    labels = [_numeric_label_value(child.label) for child in numbered[:2]]
    if labels != [1, 2]:
        return children
    lead_label = _numeric_label_value(lead.label)
    if lead_label != labels[0]:
        return children
    intro = IRNode(
        kind=IRNodeKind.INTRO,
        text=lead_text,
        attrs=lead.attrs,
    )
    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=SourceNormalizationKind.TAG_RECLASSIFY,
            basis=SourceNormalizationBasis.PROFILE_INVALID,
            before=(
                f"leading unnumbered paragraph {_node_path_label(lead)} before "
                "numbered paragraph items"
            ),
            after="reclassified as subsection INTRO facet",
            explanation=(
                "The paragraph has no explicit NUM child, ends with a list "
                "introducing colon, and is followed by an explicit 1), 2) item "
                "run whose first item has the same inherited label. The lead-in "
                "label is source transport shape; keeping it in the item sibling "
                "label space would collide with the real first item."
            ),
            path=parent_path + (_node_path_label(lead),),
            confidence=0.98,
        )
    )
    return [intro, *children[1:]]


def _item_text_ends_with_open_coordinator(node: IRNode) -> bool:
    text = irnode_to_text(node).strip().casefold()
    return text.endswith((" ja", " sekä", " tai", " taikka"))


def _with_paragraph_label(node: IRNode, label: str) -> IRNode:
    children: list[IRNode] = []
    replaced_num = False
    for child in node.children:
        if child.kind == IRNodeKind.NUM and not replaced_num:
            children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=f"{label})",
                    attrs=child.attrs,
                    children=child.children,
                )
            )
            replaced_num = True
            continue
        children.append(child)
    attrs = dict(node.attrs)
    attrs["lawvm_source_normalization_rule"] = "fi_terminal_duplicate_item_label_repair_v1"
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=attrs,
        children=tuple(children),
    )


def _repair_terminal_duplicate_item_label(
    node: IRNode,
    statute_id: str,
    parent_path: Tuple[str, ...],
    facts: List[SourceNormalizationFact],
) -> IRNode:
    """Relabel a terminal duplicate item when local list grammar proves a typo.

    A sequence like ``1), 2), 2)`` is normally a duplicate-source pathology.
    If the earlier ``2)`` ends with an open-list coordinator, the final duplicate
    is locally witnessed as the next item number rather than a peer moment or a
    duplicate to drop.
    """
    if node.kind != IRNodeKind.SUBSECTION:
        return node
    children = list(node.children)
    paragraph_positions = [
        idx
        for idx, child in enumerate(children)
        if child.kind == IRNodeKind.PARAGRAPH and _numeric_label_value(child.label) is not None
    ]
    if len(paragraph_positions) < 3:
        return node
    last_idx = paragraph_positions[-1]
    prev_idx = paragraph_positions[-2]
    if last_idx != len(children) - 1:
        return node
    last = children[last_idx]
    prev = children[prev_idx]
    last_value = _numeric_label_value(last.label)
    prev_value = _numeric_label_value(prev.label)
    if last_value is None or prev_value is None or last_value != prev_value:
        return node
    prior_values = [
        value
        for idx in paragraph_positions[:-1]
        for value in (_numeric_label_value(children[idx].label),)
        if value is not None
    ]
    if prior_values != list(range(1, len(prior_values) + 1)):
        return node
    if not _item_text_ends_with_open_coordinator(prev):
        return node
    new_label = str(prev_value + 1)
    children[last_idx] = _with_paragraph_label(last, new_label)
    facts.append(
        SourceNormalizationFact(
            statute_id=statute_id,
            kind=SourceNormalizationKind.NUMBERING_REPAIR,
            basis=SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR,
            before=(
                f"terminal duplicate paragraph label {last_value} after open-list "
                f"coordinator in paragraph {prev_value}"
            ),
            after=f"relabelled terminal paragraph as {new_label}",
            explanation=(
                "The preceding item ends with an open-list coordinator, so the "
                "terminal duplicate item is a locally witnessed next item number "
                "rather than a duplicate sibling or a peer moment."
            ),
            path=parent_path + (_node_path_label(node),),
            confidence=0.94,
        )
    )
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=node.attrs,
        children=tuple(children),
    )


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
        if tail_para.label is None or prev_para.label is None:
            rebuilt_children.append(child)
            continue
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
    allow_dotted_paragraph_subsection_promotion: bool = True,
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
      8.65. Reclassify a leading unnumbered paragraph before a numbered item
            run as an INTRO facet when the local list grammar proves it is the
            list lead-in, not item ``1)``.
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
    if ir.kind == IRNodeKind.SECTION and allow_dotted_paragraph_subsection_promotion:
        initial_children = _promote_dotted_paragraph_subsections(
            initial_children, statute_id, current_path, facts
        )
    if ir.kind == IRNodeKind.SECTION:
        initial_children = _split_intro_list_tail_moment_subsections(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _split_body_heading_into_first_subsection(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _merge_table_continuation_subsection(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _split_intro_then_numbered_list_subsections(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_section_scoped_item_style_subsections(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_unlabelled_paragraph_list_subsection_wrappers(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_table_note_subsections_into_previous_moment(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_section_content_item_subsection_run(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_sparse_section_item_subsection_payload(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_intro_only_subsection_item_list_wrapper(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_section_item_subsection_run(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _fold_section_content_item_subsection_run(
            initial_children, statute_id, current_path, facts
        )
        initial_children = _split_unnumbered_subparagraph_moment_payloads(
            initial_children, statute_id, current_path, facts
        )

    # Step 1: recurse into children first (bottom-up), collecting child facts.
    new_children: List[IRNode] = []
    children_changed = False
    for child in initial_children:
        normalized_child, child_facts = normalize_source_ir(
            child,
            statute_id,
            _parent_path=current_path,
            allow_dotted_paragraph_subsection_promotion=allow_dotted_paragraph_subsection_promotion,
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

    if len(new_children) >= 2:
        hoisted_children = _hoist_cross_heading_siblings(new_children, statute_id, current_path, facts)
        if len(hoisted_children) != len(new_children) or any(
            a is not b for a, b in zip(hoisted_children, new_children, strict=True)
        ):
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
    working = _repair_terminal_duplicate_item_label(
        working,
        statute_id,
        current_path,
        facts,
    )
    working = _split_nonpenal_trailing_duplicate_paragraph(
        working,
        statute_id,
        current_path,
        facts,
    )
    # Step 7: split malformed paragraph-local digit resets before generic numbering checks.
    if working.children:
        new_children = list(working.children)
        repaired_children = _split_digit_reset_subparagraph_runs(
            new_children, statute_id, current_path, facts
        )
        if len(repaired_children) != len(new_children) or any(
            a is not b for a, b in zip(repaired_children, new_children, strict=True)
        ):
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

    # Step 8.65: reclassify a malformed lead-in paragraph as INTRO.
    #
    # Must run before step 9 so an inferred label on unnumbered prose cannot
    # collide with the real first numbered item and trigger duplicate pruning.
    if working.kind == IRNodeKind.SUBSECTION:
        new_children = list(working.children)
        intro_children = _reclassify_leading_unnumbered_item_intro(
            new_children, statute_id, current_path, facts
        )
        if len(intro_children) != len(new_children) or any(
            a is not b for a, b in zip(intro_children, new_children, strict=True)
        ):
            working = IRNode(
                kind=working.kind,
                label=working.label,
                text=working.text,
                attrs=working.attrs,
                children=tuple(intro_children),
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

    # Step 8.8: split source-transport glued item markers inside paragraph
    # content before generic numbering anomaly detection sees the sibling run.
    if working.kind == IRNodeKind.SUBSECTION:
        new_children = list(working.children)
        split_children = _split_glued_coordinator_item_paragraphs(
            new_children, statute_id, current_path, facts
        )
        if len(split_children) != len(new_children) or any(
            a is not b for a, b in zip(split_children, new_children, strict=True)
        ):
            working = IRNode(
                kind=working.kind,
                label=working.label,
                text=working.text,
                attrs=working.attrs,
                children=tuple(split_children),
            )

    # Step 9: detect numbering anomalies (gaps/duplicates) among siblings.
    if len(working.children) >= 2:
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


__all__ = ["normalize_source_ir", "source_normalization_fact_finding_kind"]
