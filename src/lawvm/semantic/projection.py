from __future__ import annotations

import re
from typing import Any, Callable

from lawvm.core.table_model import (
    RowKey,
    TableBody,
    TableCell,
    TableRow,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.semantic.model import (
    SEMANTIC_STRUCTURE_KINDS,
    SemanticStructureFacet,
    SemanticStructureNode,
    _join_text_parts,
    _normalize_text,
    _partition_semantic_children,
    _with_wording_facet,
    canonical_structure_kind,
    normalize_semantic_label,
    normalize_visible_semantic_label,
)
from lawvm.tools.editorial_hygiene import (
    _EDITORIAL_RE,
    _TEMPORARY_RESIDUE_RE,
)


# ---------------------------------------------------------------------------
# Jurisdiction dispatch: inline repeal stub detectors
#
# Each jurisdiction frontend registers a callable that accepts one XML node and
# returns an observation dict (or None).  The shared projection layer never
# imports Finnish-specific regex strings directly; it calls through this table.
# ---------------------------------------------------------------------------

_INLINE_REPEAL_STUB_DETECTORS: dict[str, Callable[[Any], dict[str, Any] | None]] = {}


def register_inline_repeal_stub_detector(
    jurisdiction: str, fn: Callable[[Any], dict[str, Any] | None]
) -> None:
    """Register a jurisdiction-specific inline repeal stub detector.

    ``fn(node)`` should return an observation dict when *node* is a stub for
    the given jurisdiction, or ``None`` otherwise.  The Finland frontend
    registers itself at import time in ``lawvm.finland.inline_repeal_stub``.
    """
    _INLINE_REPEAL_STUB_DETECTORS[jurisdiction] = fn


def _detect_inline_repeal_stub(
    node: Any, jurisdiction: str
) -> dict[str, Any] | None:
    """Dispatch to the registered stub detector for *jurisdiction*.

    Returns the observation dict from the detector, or ``None`` when no
    detector is registered or the node is not a stub.
    """
    fn = _INLINE_REPEAL_STUB_DETECTORS.get(jurisdiction)
    if fn is None:
        return None
    return fn(node)


_KUMOTTU_WHOLE_NODE_RE = re.compile(
    r'^\s*(?:'
    + _EDITORIAL_RE.pattern
    + r'|'
    + _TEMPORARY_RESIDUE_RE.pattern
    + r')\s*$',
    re.DOTALL | re.IGNORECASE,
)


# Matches the leading ordinal(s) in a kumottu editorial like:
#   "1–2 momentit on kumottu ..."   → range [1, 2]
#   "3 momentti on kumottu ..."     → [3]
#   "1 ja 2 momentti on kumottu ..." → [1, 2]
#   "1, 2 ja 3 kohta on kumottu ..." → [1, 2, 3]
# Captured groups: (first_num, optional_dash_second, optional_ja_list_tail)
# Works for momentti/momentit/mom/kohta/kohdat/alakohta and similar ordinal units.
_KUMOTTU_ORDINAL_PREFIX_RE = re.compile(
    r'^\s*(\d+)'                                          # first number
    r'(?:'
    r'\s*[–\-—]\s*(\d+)'                                 # optional range end: "1–2"
    r'|'
    r'((?:\s*,\s*\d+)*(?:\s+ja\s+\d+)?)'                 # optional enumeration: ", 2 ja 3"
    r')'
    r'\s*(?:mome?ntti|momentit|mom\.?|kohta|kohdat|alakohta|alakohdat)',
    re.IGNORECASE,
)


def _extract_kumottu_ordinal_range(text: str) -> list[int] | None:
    """Extract the subsection/item ordinals mentioned in a kumottu editorial text.

    Finnish legal identity rule (Lainkirjoittajan opas): repeal does NOT shift
    ordinals. "1–2 momentit on kumottu" means ordinals 1 and 2 are gone; the
    next surviving child stays at its original ordinal (e.g. 3).

    Returns a sorted list of integers (e.g. [1, 2]) or None if no ordinals found.
    """
    m = _KUMOTTU_ORDINAL_PREFIX_RE.match(text.strip())
    if not m:
        return None
    first = int(m.group(1))
    range_end = m.group(2)
    enum_tail = m.group(3)
    if range_end is not None:
        return list(range(first, int(range_end) + 1))
    if enum_tail:
        nums: list[int] = [first]
        for n in re.findall(r'\d+', enum_tail):
            nums.append(int(n))
        return sorted(nums)
    return [first]


def _is_kumottu_editorial_node(text: str, kind: str) -> bool:
    if kind not in {"subsection", "section", "item"}:
        return False
    if not text:
        return False
    stripped = text.strip()
    if _KUMOTTU_WHOLE_NODE_RE.match(stripped):
        return True
    # Finlex sometimes inserts the source-law title between "L:lla"/"A:lla"
    # and the repeal citation, e.g.:
    #   "1 momentti on kumottu L:lla isyyslain voimaanpanosta 5.9.1975/701."
    # Treat these as the same editorial repeal notice family even when the
    # stricter citation regex in editorial_hygiene does not match verbatim.
    return bool(
        re.search(r"\bon\s+kumottu\b", stripped, re.IGNORECASE)
        and re.search(r"\b[LAP]:ll[äa]\b", stripped, re.IGNORECASE)
    )


def _oracle_child_tag(node: Any) -> str:
    tag = str(getattr(node, "tag", "") or "")
    return tag.split("}")[-1] if "}" in tag else tag


def _oracle_child_num(node: Any) -> str:
    if node is None:
        return ""
    num = node.find("{*}num")
    if num is None:
        num = node.find("num")
    if num is None:
        return ""
    return _normalize_text("".join(num.itertext()))


def _oracle_child_has_num(node: Any) -> bool:
    return _oracle_child_num(node) != ""


def _oracle_trailing_wrapup_child(node: Any, index: int, siblings: list[Any]) -> bool:
    """Return True for an unlabeled trailing paragraph that should be wrapUp.

    Finlex often encodes the closing penalty sentence of a numbered subsection as
    an unlabeled paragraph after the numbered list. That is structurally a wrap-up
    clause, not an extra numbered item.
    """
    if _oracle_child_tag(node) != "paragraph":
        return False
    if _oracle_child_has_num(node):
        return False
    if index <= 0:
        return False
    if not any(
        _oracle_child_tag(sibling) == "paragraph" and _oracle_child_has_num(sibling)
        for sibling in siblings[:index]
    ):
        return False
    if any(
        _oracle_child_tag(sibling) == "paragraph" and _oracle_child_has_num(sibling)
        for sibling in siblings[index + 1 :]
    ):
        return False
    return True


def _ir_trailing_wrapup_child(node: Any, index: int, siblings: list[Any]) -> bool:
    """Return True for a trailing content/paragraph leaf that should be wrapUp."""
    from lawvm.core.ir import IRNode

    if not isinstance(node, IRNode):
        return False
    node_kind = getattr(node.kind, "value", str(node.kind))
    if node_kind not in {"content", "paragraph"}:
        return False
    if node_kind == "paragraph" and any(getattr(child.kind, "value", str(child.kind)) == "num" for child in node.children):
        return False
    if index <= 0:
        return False
    if not any(
        getattr(sibling.kind, "value", str(sibling.kind)) == "paragraph"
        and any(getattr(child.kind, "value", str(child.kind)) == "num" for child in sibling.children)
        for sibling in siblings[:index]
    ):
        return False
    if any(
        getattr(sibling.kind, "value", str(sibling.kind)) == "paragraph"
        and any(getattr(child.kind, "value", str(child.kind)) == "num" for child in sibling.children)
        for sibling in siblings[index + 1 :]
    ):
        return False
    return True


def _ir_child_kind(node: Any) -> str:
    return str(getattr(node, "kind", "") or "")


def _serialize_ir_semantic_text(node: Any) -> str:
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text

    if not isinstance(node, IRNode):
        return ""
    kind = _ir_child_kind(node)
    if kind in {"heading", "intro", "wrapUp"}:
        return _normalize_text(irnode_to_text(node))
    parts: list[str] = []
    for child in node.children:
        child_kind = _ir_child_kind(child)
        if child_kind == "table":
            text = _serialize_ir_table_text(child)
            if text:
                parts.append(text)
        elif child_kind in {"content", "p", "block"}:
            text = _normalize_text(irnode_to_text(child))
            if text:
                parts.append(text)
    if not parts:
        text = _normalize_text(getattr(node, "text", ""))
        if text:
            parts.append(text)
    return _join_text_parts(parts)


def _serialize_oracle_table_text(table_node: Any) -> str:
    """Flatten an AKN <table> into space-joined row text for backward-compatible comparison.

    The structured projection is now available via ``_project_oracle_table_body``.
    This flat text serialization is kept as the canonical comparison surface so
    existing diff scores are preserved.
    """
    row_parts: list[str] = []
    for tr in list(table_node):
        if _oracle_child_tag(tr) != "tr":
            continue
        cells = [_normalize_text("".join(td.itertext())) for td in list(tr)
                 if _oracle_child_tag(td) in {"td", "th"}]
        row_text = " ".join(c for c in cells if c).strip()
        if row_text:
            row_parts.append(row_text)
    return _join_text_parts(row_parts)


def _project_ir_table_body(node: Any, table_index: int = 0) -> TableBody | None:
    """Project an IR ``IRNode(kind="table")`` into a typed ``TableBody``.

    Returns ``None`` if the node is not a table IRNode.
    """
    from lawvm.core.ir import IRNode

    if not isinstance(node, IRNode) or node.kind != IRNodeKind.TABLE:
        return None

    # Detect header row (first row where all cells are header_cell)
    columns: list[str] = []
    rows: list[TableRow] = []
    for row_node in node.children:
        if row_node.kind != IRNodeKind.ROW:
            continue
        all_header = all(
            c.kind == IRNodeKind.HEADER_CELL
            for c in row_node.children
            if c.kind in {IRNodeKind.CELL, IRNodeKind.HEADER_CELL}
        )
        if all_header and not columns and not rows:
            columns = [_normalize_text(c.text) for c in row_node.children if c.kind == IRNodeKind.HEADER_CELL]
            continue

        cells: list[TableCell] = []
        for col_idx, cell_node in enumerate(row_node.children):
            if cell_node.kind not in {IRNodeKind.CELL, IRNodeKind.HEADER_CELL}:
                continue
            col_key = columns[col_idx] if col_idx < len(columns) else str(col_idx)
            rowspan = int(cell_node.attrs.get("rowspan", 1))
            colspan = int(cell_node.attrs.get("colspan", 1))
            cells.append(TableCell(
                column_key=col_key,
                text=_normalize_text(cell_node.text),
                rowspan=rowspan,
                colspan=colspan,
            ))

        if not cells:
            continue

        first_text = next((c.text for c in cells if c.text), "")
        if first_text:
            row_key = RowKey(basis="named_anchor", value=first_text, strength="medium")
        else:
            row_key = RowKey(basis="ordinal", value=str(len(rows) + 1), strength="weak")

        rows.append(TableRow(
            row_key=row_key,
            cells=tuple(cells),
            source_basis="xml_table",
        ))

    return TableBody(
        table_id=f"table_{table_index}",
        columns=tuple(columns),
        rows=tuple(rows),
    )


def _project_oracle_table_body(table_node: Any, table_index: int = 0) -> TableBody | None:
    """Project an oracle XML ``<table>`` element into a typed ``TableBody``.

    Returns ``None`` if the element has no processable rows.
    """
    columns: list[str] = []
    rows: list[TableRow] = []
    for tr in list(table_node):
        if _oracle_child_tag(tr) != "tr":
            continue
        cell_elements = [td for td in list(tr) if _oracle_child_tag(td) in {"td", "th"}]
        all_header = all(_oracle_child_tag(td) == "th" for td in cell_elements) if cell_elements else False

        if all_header and not columns and not rows:
            columns = [_normalize_text("".join(td.itertext())) for td in cell_elements]
            continue

        cells: list[TableCell] = []
        for col_idx, td in enumerate(cell_elements):
            col_key = columns[col_idx] if col_idx < len(columns) else str(col_idx)
            rowspan = int(td.get("rowspan", "1"))
            colspan = int(td.get("colspan", "1"))
            cells.append(TableCell(
                column_key=col_key,
                text=_normalize_text("".join(td.itertext())),
                rowspan=rowspan,
                colspan=colspan,
            ))

        if not cells:
            continue

        first_text = next((c.text for c in cells if c.text), "")
        if first_text:
            row_key = RowKey(basis="named_anchor", value=first_text, strength="medium")
        else:
            row_key = RowKey(basis="ordinal", value=str(len(rows) + 1), strength="weak")

        rows.append(TableRow(
            row_key=row_key,
            cells=tuple(cells),
            source_basis="xml_table",
        ))

    if not rows:
        return None
    return TableBody(
        table_id=f"table_{table_index}",
        columns=tuple(columns),
        rows=tuple(rows),
    )


def _serialize_ir_table_text(node: Any) -> str:
    """Flatten an IR table node to space-joined row text, matching legacy format."""
    from lawvm.core.ir import IRNode

    if not isinstance(node, IRNode) or _ir_child_kind(node) != "table":
        return ""
    row_parts: list[str] = []
    for row_node in node.children:
        if _ir_child_kind(row_node) != "row":
            continue
        cell_texts = [
            _normalize_text(c.text)
            for c in row_node.children
            if _ir_child_kind(c) in {"cell", "header_cell"} and c.text
        ]
        row_text = " ".join(cell_texts).strip()
        if row_text:
            row_parts.append(row_text)
    return _join_text_parts(row_parts)


def _collect_ir_table_bodies(node: Any) -> tuple[TableBody, ...]:
    """Collect structured TableBody projections from IR table children."""
    from lawvm.core.ir import IRNode

    if not isinstance(node, IRNode):
        return ()
    tables: list[TableBody] = []
    table_idx = 0
    for child in node.children:
        if _ir_child_kind(child) == "table":
            body = _project_ir_table_body(child, table_idx)
            if body is not None:
                tables.append(body)
                table_idx += 1
        elif _ir_child_kind(child) == "content":
            for grandchild in child.children:
                if _ir_child_kind(grandchild) == "table":
                    body = _project_ir_table_body(grandchild, table_idx)
                    if body is not None:
                        tables.append(body)
                        table_idx += 1
    return tuple(tables)


def _collect_oracle_table_bodies(node: Any) -> tuple[TableBody, ...]:
    """Collect structured TableBody projections from oracle XML table children."""
    tables: list[TableBody] = []
    table_idx = 0
    for child in list(node):
        child_kind = _oracle_child_tag(child)
        if child_kind == "table":
            body = _project_oracle_table_body(child, table_idx)
            if body is not None:
                tables.append(body)
                table_idx += 1
        elif child_kind == "content":
            for grandchild in list(child):
                if _oracle_child_tag(grandchild) == "table":
                    body = _project_oracle_table_body(grandchild, table_idx)
                    if body is not None:
                        tables.append(body)
                        table_idx += 1
    return tuple(tables)


def _serialize_oracle_semantic_text(node: Any, kind: str) -> str:
    parts: list[str] = []
    if kind in {IRNodeKind.HEADING.value, IRNodeKind.INTRO.value, IRNodeKind.WRAP_UP.value}:
        return _normalize_text("".join(node.itertext()))
    for child in list(node):
        child_kind = _oracle_child_tag(child)
        if child_kind == "content":
            p_children = [grandchild for grandchild in list(child) if _oracle_child_tag(grandchild) == "p"]
            table_children = [grandchild for grandchild in list(child) if _oracle_child_tag(grandchild) == "table"]
            if p_children:
                for p_child in p_children:
                    text = _normalize_text("".join(p_child.itertext()))
                    if text:
                        parts.append(text)
            if table_children:
                for tbl in table_children:
                    text = _serialize_oracle_table_text(tbl)
                    if text:
                        parts.append(text)
            if not p_children and not table_children:
                text = _normalize_text("".join(child.itertext()))
                if text:
                    parts.append(text)
        elif child_kind == "table":
            text = _serialize_oracle_table_text(child)
            if text:
                parts.append(text)
        elif child_kind in {"p", "block"}:
            text = _normalize_text("".join(child.itertext()))
            if text:
                parts.append(text)
    if not parts:
        if not parts:
            direct_text = _normalize_text("".join(node.xpath("text()")))
            if direct_text:
                parts.append(direct_text)
    return _join_text_parts(parts)


_NUMERIC_LABEL_RE = re.compile(r"^(\d+)([a-zäöå]?)$", re.IGNORECASE)

_STRUCTURAL_CHILD_KINDS = frozenset({"section", "subsection", "item", "subitem"})

# Prefix used for synthetic/opaque ordinal-fallback labels.
# This prefix is syntactically impossible as a real Finnish law-point label
# (no Finnish label starts with "__"), so it can never collide with real
# legal identifiers.  Any label that starts with this prefix is internal
# only and must not be exposed to users or used in LegalAddress construction.
_SYNTHETIC_LABEL_PREFIX = "__ord_"

def _is_synthetic_label(label: str) -> bool:
    """Return True if *label* is an opaque synthetic ordinal-fallback label.

    Synthetic labels are of the form ``__ord_N__`` where N is the naive
    positional ordinal.  They cannot be valid Finnish legal identifiers.
    """
    return label.startswith(_SYNTHETIC_LABEL_PREFIX)

# ---------------------------------------------------------------------------
# content-before-items → intro normalization
#
# Amendment source XML (Finnish AKN) encodes subsection replacement text as flat
# <content><p>...</p></content> regardless of whether the subsection contains an
# enumerated list with a lead-in sentence.  The Finlex oracle consolidation uses
# <intro>...</intro><paragraph>1)...</paragraph> structure for exactly this case.
#
# When replay applies such an amendment the IR ends up with a `content` child as
# the first text-bearing node, followed by `paragraph` (item) children.  Without
# normalization the semantic projection produces a `wording` facet instead of an
# `intro` facet, causing spurious `facet_added` diff events.
#
# The heuristic below detects this pattern and synthesises an `intro` facet in
# the projection layer — the IR itself is not mutated.
#
# Conditions (all must hold):
#  1. The node is a subsection (or section that directly contains paragraphs).
#  2. The FIRST text-bearing child has kind `content` or `p`.
#  3. At least one `paragraph` child follows it.
#
# Non-application:
#  - Subsection/section with NO `paragraph` children → flat wording, no intro.
#  - First text-bearing child is already `intro` → already handled correctly.
# ---------------------------------------------------------------------------

def _ir_first_text_child_before_paragraphs(node: Any) -> Any | None:
    """Return the first text-bearing child if it precedes paragraph children.

    Returns the child IRNode when the heuristic applies (content-before-items
    → intro normalization), otherwise returns None.

    Only applies when:
    - The first text-bearing child has kind 'content' or 'p'.
    - At least one 'paragraph' child follows it in the children list.
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text

    if not isinstance(node, IRNode):
        return None

    children = node.children
    if not children:
        return None

    # Find the first text-bearing child (content or p).
    first_text_child: IRNode | None = None
    first_text_idx: int = -1
    for i, child in enumerate(children):
        if _ir_child_kind(child) in {"content", "p"}:
            text = _normalize_text(irnode_to_text(child))
            if text:
                first_text_child = child
                first_text_idx = i
                break

    if first_text_child is None:
        return None

    # Check that at least one paragraph child follows.
    has_following_paragraph = any(
        _ir_child_kind(child) == "paragraph"
        for child in children[first_text_idx + 1 :]
    )
    if not has_following_paragraph:
        return None

    return first_text_child


def _serialize_ir_semantic_text_excluding_intro(node: Any, intro_child: Any) -> str:
    """Serialize IR semantic text, skipping the designated intro child.

    Used after content-before-items normalization: the intro child's text is
    emitted as an `intro` facet, not as part of the wording text.
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text

    if not isinstance(node, IRNode):
        return ""
    kind = _ir_child_kind(node)
    if kind in {"heading", "intro", "wrapUp"}:
        return _normalize_text(irnode_to_text(node))
    parts: list[str] = []
    for child in node.children:
        if child is intro_child:
            continue  # already captured as intro facet
        child_kind = _ir_child_kind(child)
        if child_kind == "table":
            text = _serialize_ir_table_text(child)
            if text:
                parts.append(text)
        elif child_kind in {"content", "p", "block"}:
            text = _normalize_text(irnode_to_text(child))
            if text:
                parts.append(text)
    return _join_text_parts(parts)


def _label_sort_key(label: str) -> tuple[int, str]:
    """Return a (number, letter) sort key for a numeric-ish label like '6a'."""
    m = _NUMERIC_LABEL_RE.match(label)
    if m:
        return (int(m.group(1)), m.group(2).lower())
    return (0, label)


def _next_free_ordinal(counter: int, explicit_labels: set[str]) -> str:
    """Return the lowest positive integer ordinal not already in ``explicit_labels``.

    Starts searching from ``counter`` (the naive positional count) and scans
    upward.  When all plain integer candidates within a reasonable window are
    taken, returns an **opaque synthetic label** of the form
    ``"__ord_N__"`` (where N is the original *counter*) rather than a
    letter-suffix string like ``"2a"`` that could be mistaken for a real
    Finnish legal label (e.g. ``5 a §``, ``4 a–4 d §``).

    The opaque label:
    - Is syntactically impossible as a Finnish law-point label (no real label
      starts with ``__``).
    - Never collides with real amendment target labels.
    - Must never appear in user-visible output or in LegalAddress construction.

    Callers that need to emit a defect observation about the collision should
    reference the naive ordinal (``str(counter)``), NOT the opaque string.

    Example (2013/331 § 3 / subsec 1):
      counter=2, explicit_labels={"1","2","3",...,"13"}
      → previously returned "2a" (unsafe — looks like a real Finnish label)
      → now returns "__ord_2__" (opaque internal anchor)
    """
    # Try the counter and then incrementing integers.
    candidate = counter
    # Scan up to a bounded window (N+20 should be more than enough for any realistic list).
    for _ in range(20):
        label = str(candidate)
        if label not in explicit_labels:
            return label
        candidate += 1
    # All plain integers are taken within the window.
    # Return an opaque synthetic label keyed on the original counter.
    # Do NOT use letter suffixes (e.g. "2a") — they look like real Finnish labels.
    return f"{_SYNTHETIC_LABEL_PREFIX}{counter}__"


def _check_child_ordering(
    parent_kind: str,
    parent_label: str,
    children: tuple[SemanticStructureNode, ...],
    source_label: str,
) -> tuple[str, ...]:
    """Return typed defect tokens if structural children have out-of-order or duplicate numeric labels.

    Returns a tuple of defect observation strings (empty when no defects).
    Observation names:
    - ``ORACLE_DUPLICATE_CHILD_LABEL:<child_kind>:<label>`` — duplicate label after two-pass relabeling.
    - ``ORACLE_OUT_OF_ORDER_CHILDREN:<child_kind>:<label_list>`` — out-of-order labels.
    - Same with ``REPLAY_`` prefix for IR projections.
    """
    numeric_children = [
        child for child in children
        if child.kind in _STRUCTURAL_CHILD_KINDS and _NUMERIC_LABEL_RE.match(child.label)
    ]
    if len(numeric_children) < 2:
        return ()

    labels = [child.label for child in numeric_children]
    seen: set[str] = set()
    keys = [_label_sort_key(lbl) for lbl in labels]
    prefix = "ORACLE" if source_label == "oracle" else "REPLAY"

    defects: list[str] = []

    # Check for duplicates
    for lbl in labels:
        if lbl in seen:
            defects.append(
                f"{prefix}_DUPLICATE_CHILD_LABEL:{numeric_children[0].kind}:{lbl}"
            )
            return tuple(defects)
        seen.add(lbl)

    # Check for out-of-order
    if keys != sorted(keys):
        defects.append(
            f"{prefix}_OUT_OF_ORDER_CHILDREN:{numeric_children[0].kind}:{','.join(labels)}"
        )

    return tuple(defects)


def semantic_structure_from_ir(node: Any) -> SemanticStructureNode | None:
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import irnode_to_text

    if not isinstance(node, IRNode):
        return None
    kind = canonical_structure_kind(node.kind)
    if kind not in SEMANTIC_STRUCTURE_KINDS:
        return None
    ir_children = list(node.children)
    raw_children_list: list[SemanticStructureNode] = []
    for idx, raw_child in enumerate(ir_children):
        if kind == "subsection" and _ir_trailing_wrapup_child(raw_child, idx, ir_children):
            text = _normalize_text(getattr(raw_child, "text", "") or _serialize_ir_semantic_text(raw_child))
            if text:
                raw_children_list.append(SemanticStructureNode(kind="wrapUp", text=text))
                continue
        child = semantic_structure_from_ir(raw_child)
        if child is not None:
            raw_children_list.append(child)
    raw_children = tuple(raw_children_list)

    # Content-before-items → intro normalization.
    #
    # Amendment source XML encodes subsection replacement text as flat
    # <content>...</content> even when the subsection contains an enumerated list
    # with a lead-in sentence.  The oracle consolidation uses <intro>...</intro>
    # for the same structure.  When the first text-bearing child is a `content`
    # or `p` node AND it is followed by at least one `paragraph` child, treat
    # that first child as an `intro` facet rather than including its text in the
    # wording.  The IR tree is not mutated — normalization is projection-only.
    #
    # Provenance: 1889/39-001 chapter:39/section:2 — amendment content+paragraph
    # vs oracle intro+paragraph (amendment 2017/813 and similar).
    # Intro normalization only for section/subsection — item-level intros
    # should come from explicit IR structure, not the content-before-paragraphs
    # heuristic (which misfires when items have subitems).
    # Collect structured table bodies from IR children (carried alongside
    # flat text in the wording facet for backward-compatible comparison).
    ir_table_bodies = _collect_ir_table_bodies(node)

    intro_child = _ir_first_text_child_before_paragraphs(node) if kind in {"section", "subsection"} else None
    if intro_child is not None:
        intro_text = _normalize_text(irnode_to_text(intro_child))
        text = _serialize_ir_semantic_text_excluding_intro(node, intro_child)
        intro_facet = SemanticStructureFacet(kind="intro", text=intro_text)
        facets, children = _partition_semantic_children(raw_children)
        # Prepend the synthetic intro facet; existing facets from raw_children
        # (heading, etc.) are preserved.
        if not any(f.kind == "intro" for f in facets):
            facets = (intro_facet,) + facets
    else:
        text = _serialize_ir_semantic_text(node)
        facets, children = _partition_semantic_children(raw_children)
    facets = _with_wording_facet(facets, text, tables=ir_table_bodies)

    # Two-pass ordinal fallback labeling for unlabeled structural children.
    #
    # Pass 1: collect explicit labels already used by same-kind siblings so the
    #         fallback assignment can avoid collisions.
    # Pass 2: for each unlabeled child, pick the lowest positive integer NOT in
    #         the explicit-labels set.  If no plain integer is free, use letter
    #         suffixes (e.g. "2a", "2b").
    #
    # This preserves positional correspondence when some siblings have explicit
    # labels: e.g., if items "1","2","3" are followed by an unlabeled item, the
    # unlabeled item gets ordinal "4", not "1" (which would collide).
    explicit_labels_by_kind: dict[str, set[str]] = {}
    for child in children:
        if child.kind in {"subsection", "item", "subitem"} and child.label:
            explicit_labels_by_kind.setdefault(child.kind, set()).add(child.label)

    ir_positional: dict[str, int] = {}
    ir_averted_collision_defects: list[str] = []
    labeled_children: list[SemanticStructureNode] = []
    for child in children:
        if child.kind in {"subsection", "item", "subitem"}:
            ir_positional[child.kind] = ir_positional.get(child.kind, 0) + 1
            if not child.label:
                naive_ordinal = str(ir_positional[child.kind])
                explicit_set = explicit_labels_by_kind.get(child.kind, set())
                ordinal_str = _next_free_ordinal(ir_positional[child.kind], explicit_set)
                if ordinal_str != naive_ordinal:
                    ir_averted_collision_defects.append(
                        f"REPLAY_DUPLICATE_CHILD_LABEL:{child.kind}:{naive_ordinal}"
                    )
                basis = (
                    child.label_basis
                    if child.label_basis == "editorial_repeal_notice"
                    else "ordinal_fallback"
                )
                child = SemanticStructureNode(
                    kind=child.kind,
                    label=ordinal_str,
                    # Synthetic opaque labels must not appear in user-visible output.
                    # Use empty visible_label; renderers should show "(unlabeled)" or
                    # check label_basis == "ordinal_fallback" to suppress display.
                    visible_label="" if _is_synthetic_label(ordinal_str) else ordinal_str,
                    label_basis=basis,
                    text=child.text,
                    facets=child.facets,
                    children=child.children,
                )
        labeled_children.append(child)
    children = tuple(labeled_children)

    def _projected_child_is_editorial_or_empty(child: SemanticStructureNode) -> bool:
        if child.label_basis in {"editorial_repeal_notice", "repeal_placeholder"}:
            return True
        has_own_text = bool(child.text) or any(f.text for f in child.facets)
        if has_own_text:
            return False
        if not child.children:
            return True
        return all(_projected_child_is_editorial_or_empty(grandchild) for grandchild in child.children)

    def _all_children_editorial_or_empty(items: tuple[SemanticStructureNode, ...]) -> bool:
        if not items:
            return True
        return all(_projected_child_is_editorial_or_empty(child) for child in items)

    # Tag authoritative repeal placeholders so the diff layer can recognise
    # them without relying on text content (which we deliberately removed).
    #
    # A stale section-level ``lawvm_repeal_placeholder`` attribute must not
    # mask surviving live children. This occurs in some historical Finland
    # chains where a whole-section repeal placeholder is later partially
    # replaced by live subsection content. Treat the node as a repeal
    # placeholder only when the projected subtree is still wholly editorial or
    # empty.
    label_basis: str = "explicit"
    if (
        getattr(node, "attrs", None)
        and node.attrs.get("lawvm_repeal_placeholder") == "1"
        and _all_children_editorial_or_empty(children)
    ):
        label_basis = "repeal_placeholder"

    node_label = normalize_semantic_label(kind, str(node.label or ""))
    ordering_defects = _check_child_ordering(kind, node_label, children, "ir")
    projection_defects = tuple(ir_averted_collision_defects) + ordering_defects
    return SemanticStructureNode(
        kind=kind,
        label=node_label,
        visible_label=normalize_visible_semantic_label(kind, str(node.label or "")),
        label_basis=label_basis,
        text=text,
        facets=facets,
        children=children,
        defects=projection_defects,
    )


def semantic_structure_from_oracle(
    node: Any,
    *,
    _observations_out: list[dict[str, Any]] | None = None,
) -> SemanticStructureNode | None:
    """Project an oracle XML element to a ``SemanticStructureNode``.

    Parameters
    ----------
    node:
        An ``lxml.etree._Element`` representing a Finnish AKN structural element.
    _observations_out:
        Optional mutable list.  When supplied, editorial observations emitted
        during projection are appended as dicts.  Callers that want to inspect
        ``FINLEX_INLINE_REPEAL_STUB`` events should pass an empty list here.
        The list is *not* threaded recursively — only top-level observations for
        the immediate children of *this* node are emitted.
    """
    from lxml import etree

    if not isinstance(node, etree._Element):
        return None
    kind = canonical_structure_kind(_oracle_child_tag(node))
    if kind not in SEMANTIC_STRUCTURE_KINDS:
        return None

    # Strip jurisdiction-registered inline repeal stubs from the child list
    # before projection.  The detection logic is jurisdiction-specific (e.g.
    # Finland's Finlex editorial conventions); this layer only dispatches to
    # registered detectors via _detect_inline_repeal_stub().
    #
    # Finland registers its detector in lawvm.finland.inline_repeal_stub at
    # import time under the "fi" key.  The "fi" key is used here as the default
    # because oracle projection currently runs exclusively against Finnish Finlex
    # XML.  When other jurisdictions need stub stripping they register their own
    # detector and the relevant call site passes their jurisdiction key.
    #
    # TODO: cross-check against ProvisionTimeline is not feasible at projection
    # time (projection runs above the replay layer and has no direct access to
    # the compiled timeline).  The observation emitted here (kind=
    # FINLEX_INLINE_REPEAL_STUB) is the hook for a downstream adjudication pass
    # to perform that cross-check once the timeline is available.
    # See notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md §1.9.
    all_oracle_children = list(node)
    filtered_children: list[Any] = []
    for child in all_oracle_children:
        obs = _detect_inline_repeal_stub(child, "fi")
        if obs is not None:
            if _observations_out is not None:
                _observations_out.append(obs)
        else:
            filtered_children.append(child)

    # Pass 1 (pre-scan): collect explicit labels from filtered children of each kind
    # so the ordinal-fallback pass can avoid collisions with explicitly-labeled siblings.
    oracle_children = filtered_children
    explicit_oracle_labels: dict[str, set[str]] = {}
    for _prescan_child in oracle_children:
        _prescan_tag = _oracle_child_tag(_prescan_child)
        _prescan_kind = canonical_structure_kind(_prescan_tag)
        if _prescan_kind not in {"subsection", "item", "subitem"}:
            continue
        _prescan_num = _oracle_child_num(_prescan_child)
        if _prescan_num:
            _prescan_label = normalize_semantic_label(_prescan_kind, _prescan_num)
            if _prescan_label:
                explicit_oracle_labels.setdefault(_prescan_kind, set()).add(_prescan_label)

    # Pass 2: project children and assign ordinal fallbacks that don't collide.
    # Averted-collision observations are collected here so the parent node can
    # carry an ORACLE_DUPLICATE_CHILD_LABEL defect even though the label was
    # successfully relabeled.
    raw_children: list[SemanticStructureNode] = []
    semantic_ordinals: dict[str, int] = {}
    averted_collision_defects: list[str] = []
    for idx, raw_child in enumerate(oracle_children):
        if kind == "subsection" and _oracle_trailing_wrapup_child(raw_child, idx, oracle_children):
            text = _normalize_text(" ".join(str(part) for part in raw_child.itertext()))
            if text:
                raw_children.append(SemanticStructureNode(kind="wrapUp", text=text))
                continue
        child = semantic_structure_from_oracle(raw_child)
        if child is None:
            continue
        # Count ALL siblings of each kind (labeled + unlabeled) so that the positional
        # ordinal assigned to unlabeled children reflects their true position among all
        # siblings. Without this, a kumottu item with no <num> (Finlex omits it) would
        # always get ordinal "1", colliding with the explicitly-labeled first sibling.
        # This mirrors the logic in semantic_structure_from_ir().
        #
        # Finnish legal identity rule (Lainkirjoittajan opas): repeal does NOT shift
        # ordinals downward. "1–2 momentit on kumottu" means momentti 3 stays "3", not "1".
        # When a kumottu editorial notice covers an explicit range (e.g. "1–2 momentit"),
        # advance the ordinal counter to the max of that range so subsequent children
        # receive their correct legal ordinals.
        if child.kind in {"subsection", "item", "subitem"}:
            if not child.label and child.label_basis == "editorial_repeal_notice":
                # Try to extract the explicit ordinal range from the kumottu text.
                kumottu_range = _extract_kumottu_ordinal_range(child.text or "")
                if kumottu_range:
                    # Advance counter to the last repealed ordinal (next increment lands right).
                    semantic_ordinals[child.kind] = max(kumottu_range)
                    # Assign the first ordinal in the range as the node's label (deterministic
                    # addressing; represents the whole repealed range).
                    ordinal_str = str(kumottu_range[0])
                    child = SemanticStructureNode(
                        kind=child.kind,
                        label=ordinal_str,
                        visible_label=child.visible_label or ordinal_str,
                        label_basis="editorial_repeal_notice",
                        text=child.text,
                        facets=child.facets,
                        children=child.children,
                    )
                    raw_children.append(child)
                    continue
            semantic_ordinals[child.kind] = semantic_ordinals.get(child.kind, 0) + 1
            if not child.label:
                naive_ordinal = str(semantic_ordinals[child.kind])
                explicit_set = explicit_oracle_labels.get(child.kind, set())
                # Two-pass collision avoidance: pick the lowest free ordinal that
                # does not collide with any explicitly-labeled sibling of the same kind.
                ordinal_str = _next_free_ordinal(semantic_ordinals[child.kind], explicit_set)
                if ordinal_str != naive_ordinal:
                    # Record that a collision would have occurred under naive counting.
                    averted_collision_defects.append(
                        f"ORACLE_DUPLICATE_CHILD_LABEL:{child.kind}:{naive_ordinal}"
                    )
                # Preserve editorial_repeal_notice label_basis — ordinal fallback
                # is only an addressing aid; it must not mask editorial classification.
                basis = (
                    child.label_basis
                    if child.label_basis == "editorial_repeal_notice"
                    else "ordinal_fallback"
                )
                child = SemanticStructureNode(
                    kind=child.kind,
                    label=ordinal_str,
                    # Synthetic opaque labels must not appear in user-visible output.
                    # Use empty visible_label; renderers should show "(unlabeled)" or
                    # check label_basis == "ordinal_fallback" to suppress display.
                    visible_label="" if _is_synthetic_label(ordinal_str) else (child.visible_label or ordinal_str),
                    label_basis=basis,
                    text=child.text,
                    facets=child.facets,
                    children=child.children,
                )
        raw_children.append(child)
    text = _serialize_oracle_semantic_text(node, kind)
    oracle_table_bodies = _collect_oracle_table_bodies(node)
    facets, children = _partition_semantic_children(tuple(raw_children))
    facets = _with_wording_facet(facets, text, tables=oracle_table_bodies)

    label_basis: str = "explicit"
    if _is_kumottu_editorial_node(text, kind) and not children:
        label_basis = "editorial_repeal_notice"
    elif children and all(c.label_basis == "editorial_repeal_notice" for c in children):
        # Whole-chapter repeal: all children are editorial repeal notices.
        # The parent section/subsection is itself editorial — tag it so the
        # diff layer can classify it as editorial_repeal_notice rather than
        # unit_missing_left.
        label_basis = "editorial_repeal_notice"

    raw_num = _oracle_child_num(node)
    oracle_label = normalize_semantic_label(kind, raw_num)
    ordering_defects = _check_child_ordering(kind, oracle_label, children, "oracle")
    projection_defects = tuple(averted_collision_defects) + ordering_defects
    return SemanticStructureNode(
        kind=kind,
        label=oracle_label,
        visible_label=normalize_visible_semantic_label(kind, raw_num),
        label_basis=label_basis,
        text=text,
        facets=facets,
        children=children,
        defects=projection_defects,
    )
