"""Property-based tests for tree_ops primitives, check_invariants, and omission merge.

Run:
    uv run pytest tests/test_tree_ops_properties.py -v

Tests cover three groups:

A. tree_ops primitives:
    1. replace_at preserves child count at non-replaced levels
    2. remove_at reduces child count by exactly 1 at parent level
    3. insert_sorted increases child count by exactly 1
    4. insert_sorted maintains sort order (using _default_sort_key)
    5. replace_at([]) returns replacement (identity on empty path)

B. check_invariants:
    6. Well-formed generated trees pass check_invariants
    7. insert_sorted on valid tree produces valid tree
    8. replace_at on valid tree produces valid tree
    9. remove_at on valid tree produces valid tree

C. Omission merge:
    10. Result contains no omission nodes
    11. All non-omission content from amendment appears in result
    12. If amendment has no omissions, result equals amendment (full replace)
"""

from __future__ import annotations

import string
from typing import List, Optional, Tuple, cast

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.core.tree_ops import (
    Path,
    _default_sort_key,
    build_label_index,
    check_invariants,
    find_flattened_sublist_warnings,
    find_text_duplication_warnings,
    find,
    insert_sorted,
    remove_at,
    replace_at,
    resolve,
)
from lawvm.finland.grafter import (
    AmendmentOp,
    _apply_deterministic_subsection_op,
    _merge_section_with_omission_ir,
    _merge_subsection_with_omission_ir,
    get_replay_profile,
)


# ---------------------------------------------------------------------------
# Strategies (generators for hypothesis)
# ---------------------------------------------------------------------------

SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=40,
)

# Section labels: "1" through "50", with optional letter suffix
SECTION_LABELS = st.one_of(
    st.integers(min_value=1, max_value=50).map(str),
    st.builds(
        lambda n, s: f"{n}{s}",
        st.integers(min_value=1, max_value=30),
        st.sampled_from(list("abcde")),
    ),
)

SUBSECTION_LABELS = st.integers(min_value=1, max_value=10).map(str)

PARAGRAPH_LABELS = st.integers(min_value=1, max_value=10).map(str)


def test_find_text_duplication_warnings_detects_duplicate_full_text() -> None:
    repeated = " ".join(["sama", "teksti"] * 45)
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated),)),
            IRNode(kind=IRNodeKind.SECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated),)),
        ),
    )

    warnings = find_text_duplication_warnings(body)

    assert any(item["kind"] == "duplicate_full_text" for item in warnings)


def test_find_text_duplication_warnings_detects_duplicate_suffix_text() -> None:
    shared_tail = " ".join(["yhteinen", "loppu"] * 45)
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text=f"alku a {shared_tail}"),),
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text=f"alku b {shared_tail}"),),
            ),
        ),
    )

    warnings = find_text_duplication_warnings(body)

    assert any(item["kind"] == "duplicate_suffix_text" for item in warnings)


def test_find_flattened_sublist_warnings_detects_interleaved_family() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=tuple(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label)
                    for label in ("a", "b", "1", "2", "a", "b")
                ),
            ),
        ),
    )

    warnings = find_flattened_sublist_warnings(body)

    assert warnings == [
        {
            "kind": "flattened_sublist_interleaved",
            "path": "body/section:1",
            "node_kind": "paragraph",
            "repeated_families": ["alpha"],
            "label_sample": ["a", "b", "1", "2", "a", "b"],
        }
    ]


def test_find_flattened_sublist_warnings_ignores_monotonic_family() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=tuple(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label)
                    for label in ("1", "2", "3", "4", "5")
                ),
            ),
        ),
    )

    assert find_flattened_sublist_warnings(body) == []


@st.composite
def paragraph_node(draw) -> IRNode:
    """Generate a paragraph node (leaf within subsection)."""
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=draw(PARAGRAPH_LABELS),
        text=draw(SHORT_TEXT),
    )


@st.composite
def content_node(draw) -> IRNode:
    """Generate a content node (leaf within section/subsection)."""
    return IRNode(kind=IRNodeKind.CONTENT, label=None, text=draw(SHORT_TEXT))


@st.composite
def subsection_node(draw, with_paragraphs: bool = False) -> IRNode:
    """Generate a subsection with optional paragraph children."""
    label = draw(SUBSECTION_LABELS)
    if with_paragraphs and draw(st.booleans()):
        n = draw(st.integers(min_value=1, max_value=4))
        para_labels = list(range(1, n + 1))
        children: List[IRNode] = [
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(pl), text=draw(SHORT_TEXT)) for pl in para_labels
        ]
        return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=draw(SHORT_TEXT))


@st.composite
def section_with_subsections(draw) -> IRNode:
    """Generate a section with unique-labeled subsections, in sorted order."""
    label = draw(SECTION_LABELS)
    n = draw(st.integers(min_value=1, max_value=5))
    children: List[IRNode] = [
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT)) for i in range(1, n + 1)
    ]
    heading = IRNode(kind=IRNodeKind.HEADING, label=None, text=draw(SHORT_TEXT))
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=(heading, *children))


@st.composite
def chapter_with_sorted_sections(draw) -> IRNode:
    """Generate a chapter with unique, sorted section labels."""
    label = draw(st.integers(min_value=1, max_value=10).map(str))
    n = draw(st.integers(min_value=1, max_value=5))
    section_labels = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=50).map(str),
                min_size=n,
                max_size=n,
                unique=True,
            )
        ),
        key=_default_sort_key,
    )
    sections: List[IRNode] = []
    for sl in section_labels:
        n_sub = draw(st.integers(min_value=1, max_value=3))
        subs = [IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT)) for i in range(1, n_sub + 1)]
        sections.append(IRNode(kind=IRNodeKind.SECTION, label=sl, children=tuple(subs)))
    heading = IRNode(kind=IRNodeKind.HEADING, label=None, text=draw(SHORT_TEXT))
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=(heading, *sections))


def _label_text(value: Optional[str]) -> str:
    assert value is not None
    return value


@st.composite
def well_formed_body(draw) -> IRNode:
    """Generate a body -> chapter -> section -> subsection tree that passes check_invariants.

    Keeps trees small: 1-3 chapters, 1-4 sections per chapter, 1-3 subsections per section.
    All labels unique within their siblings, all sorted.
    """
    n_chapters = draw(st.integers(min_value=1, max_value=3))
    chapter_labels = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=10).map(str),
                min_size=n_chapters,
                max_size=n_chapters,
                unique=True,
            )
        ),
        key=_default_sort_key,
    )
    chapters: List[IRNode] = []
    for cl in chapter_labels:
        n_sections = draw(st.integers(min_value=1, max_value=4))
        section_labels = sorted(
            draw(
                st.lists(
                    st.integers(min_value=1, max_value=40).map(str),
                    min_size=n_sections,
                    max_size=n_sections,
                    unique=True,
                )
            ),
            key=_default_sort_key,
        )
        sections: List[IRNode] = []
        for sl in section_labels:
            n_sub = draw(st.integers(min_value=1, max_value=3))
            subs = [
                IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT)) for i in range(1, n_sub + 1)
            ]
            sections.append(IRNode(kind=IRNodeKind.SECTION, label=sl, children=tuple(subs)))
        ch_heading = IRNode(kind=IRNodeKind.HEADING, label=None, text=draw(SHORT_TEXT))
        chapters.append(IRNode(kind=IRNodeKind.CHAPTER, label=cl, children=(ch_heading, *sections)))
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(chapters))


@st.composite
def flat_body_unique_sections(draw) -> IRNode:
    """Generate a body with directly nested sections (no chapters), sorted and unique labels."""
    n = draw(st.integers(min_value=1, max_value=5))
    labels = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=50).map(str),
                min_size=n,
                max_size=n,
                unique=True,
            )
        ),
        key=_default_sort_key,
    )
    sections = [IRNode(kind=IRNodeKind.SECTION, label=lbl, text=draw(SHORT_TEXT)) for lbl in labels]
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(sections))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_children(node: IRNode) -> int:
    return len(node.children)


def _collect_labels(node: IRNode, kind: str) -> List[Optional[str]]:
    """Collect labels of children matching kind, in order."""
    return [c.label for c in node.children if c.kind == kind]


def _all_sort_keys(node: IRNode, kind: str) -> List[Tuple[int, str, int]]:
    """Collect sort keys for children of given kind."""
    return [_default_sort_key(c.label) for c in node.children if c.kind == kind and c.label is not None]


def _has_omission(node: IRNode) -> bool:
    """Check if node or any descendant is an omission."""
    if node.kind == IRNodeKind.OMISSION:
        return True
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") == "omission":
        return True
    return any(_has_omission(c) for c in node.children)


def _collect_non_omission_texts(node: IRNode) -> List[str]:
    """Collect text content from all non-omission nodes in DFS order."""
    if node.kind == IRNodeKind.OMISSION:
        return []
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") == "omission":
        return []
    texts: List[str] = []
    if node.text:
        texts.append(node.text)
    for child in node.children:
        texts.extend(_collect_non_omission_texts(child))
    return texts


# ============================================================================
# GROUP A: tree_ops primitive properties
# ============================================================================

# ---------------------------------------------------------------------------
# A1: replace_at preserves child count at non-replaced levels
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=50)
def test_replace_at_preserves_child_count(body: IRNode, new_text: str) -> None:
    """replace_at on a section preserves child count of the body (non-replaced level)."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)

    # Pick first chapter, first section within it
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None)

    path: Path = (("chapter", _label_text(ch.label)), ("section", _label_text(sec.label)))
    replacement = IRNode(kind=IRNodeKind.SECTION, label=sec.label, text=new_text)

    result = replace_at(body, path, replacement)

    # Body-level child count unchanged
    assert _count_children(result) == _count_children(body), (
        f"Body child count changed: {_count_children(body)} -> {_count_children(result)}"
    )
    # Chapter-level child count unchanged for the target chapter
    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    assert _count_children(result_ch) == _count_children(ch), (
        f"Chapter child count changed: {_count_children(ch)} -> {_count_children(result_ch)}"
    )


# ---------------------------------------------------------------------------
# A2: remove_at reduces child count by exactly 1 at parent level
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_remove_at_reduces_child_count_by_one(body: IRNode) -> None:
    """remove_at on a section reduces that chapter's child count by exactly 1."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None)

    path: Path = (("chapter", _label_text(ch.label)), ("section", _label_text(sec.label)))
    result = remove_at(body, path)

    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    original_section_count = len([c for c in ch.children if c.kind == IRNodeKind.SECTION])
    result_section_count = len([c for c in result_ch.children if c.kind == IRNodeKind.SECTION])
    assert result_section_count == original_section_count - 1, (
        f"Section count after remove_at: expected {original_section_count - 1}, got {result_section_count}"
    )


# ---------------------------------------------------------------------------
# A3: insert_sorted increases child count by exactly 1
# ---------------------------------------------------------------------------


@given(flat_body_unique_sections(), SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=50)
def test_insert_sorted_increases_child_count_by_one(body: IRNode, new_label: str, new_text: str) -> None:
    """insert_sorted into body increases section count by exactly 1."""
    existing = {c.label for c in body.children if c.kind == IRNodeKind.SECTION}
    assume(new_label not in existing)

    new_section = IRNode(kind=IRNodeKind.SECTION, label=new_label, text=new_text)
    result = insert_sorted(body, [], new_section)

    original_count = len(body.children)
    result_count = len(result.children)
    assert result_count == original_count + 1, (
        f"Child count after insert: expected {original_count + 1}, got {result_count}"
    )


def test_replace_at_updates_only_first_matching_duplicate_branch() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="First", children=()),
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="Second", children=()),
                ),
            ),
        ),
    )

    result = replace_at(
        body,
        [("chapter", "2"), ("division", "6")],
        IRNode(kind=IRNodeKind.DIVISION, label="6", text="Updated", children=()),
    )

    chapter = result.children[0]
    assert [child.text for child in chapter.children if child.kind == IRNodeKind.DIVISION] == [
        "Updated",
        "Second",
    ]


def test_remove_at_updates_only_first_matching_duplicate_branch() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="First", children=()),
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="Second", children=()),
                ),
            ),
        ),
    )

    result = remove_at(body, [("chapter", "2"), ("division", "6")])

    chapter = result.children[0]
    assert [child.text for child in chapter.children if child.kind == IRNodeKind.DIVISION] == [
        "Second",
    ]


def test_insert_sorted_targets_only_first_matching_duplicate_parent() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="First", children=()),
                    IRNode(kind=IRNodeKind.DIVISION, label="6", text="Second", children=()),
                ),
            ),
        ),
    )

    result = insert_sorted(
        body,
        [("chapter", "2"), ("division", "6")],
        IRNode(kind=IRNodeKind.SECTION, label="44_1", text="Inserted"),
    )

    chapter = result.children[0]
    first_div, second_div = [child for child in chapter.children if child.kind == IRNodeKind.DIVISION]
    assert [(child.kind, child.label) for child in first_div.children] == [
        (IRNodeKind.SECTION, "44_1"),
    ]
    assert second_div.children == ()


# ---------------------------------------------------------------------------
# A4: insert_sorted maintains sort order
# ---------------------------------------------------------------------------


@given(flat_body_unique_sections(), SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=50)
def test_insert_sorted_maintains_sort_order(body: IRNode, new_label: str, new_text: str) -> None:
    """insert_sorted preserves sort order among same-kind children."""
    existing = {c.label for c in body.children if c.kind == IRNodeKind.SECTION}
    assume(new_label not in existing)

    new_section = IRNode(kind=IRNodeKind.SECTION, label=new_label, text=new_text)
    result = insert_sorted(body, [], new_section)

    keys = _all_sort_keys(result, "section")
    for i in range(len(keys) - 1):
        assert keys[i] <= keys[i + 1], (
            f"Sort order violated after insert: position {i}: "
            f"{keys[i]} > {keys[i + 1]}. "
            f"Labels: {_collect_labels(result, 'section')}"
        )


# ---------------------------------------------------------------------------
# A5: replace_at with empty path returns replacement (identity)
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=50)
def test_replace_at_empty_path_returns_replacement(body: IRNode, text: str) -> None:
    """replace_at(tree, [], content) returns content regardless of tree."""
    replacement = IRNode(kind=IRNodeKind.BODY, label=None, text=text)
    result = replace_at(body, [], replacement)
    assert result.kind == replacement.kind
    assert result.text == replacement.text
    assert result.children == replacement.children


# ============================================================================
# GROUP B: check_invariants
# ============================================================================

# ---------------------------------------------------------------------------
# B1: Well-formed generated trees pass check_invariants
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_well_formed_tree_passes_invariants(body: IRNode) -> None:
    """Trees produced by well_formed_body generator have no invariant violations."""
    violations = check_invariants(body)
    assert violations == [], f"Generated tree has violations: {violations}"


def test_check_invariants_accepts_top_level_schedule_and_appendix() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SCHEDULE, label="1", children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),)),
            IRNode(kind=IRNodeKind.APPENDIX, label="A", children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)),
        ),
    )

    violations = check_invariants(body)

    assert violations == []


def test_check_invariants_accepts_nested_schedule_shapes() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="a",
                                        children=(IRNode(kind=IRNodeKind.SENTENCE, label="1"),),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    violations = check_invariants(body)

    assert violations == []


# ---------------------------------------------------------------------------
# B2: insert_sorted on valid tree produces valid tree
# ---------------------------------------------------------------------------


@given(well_formed_body(), SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=50)
def test_insert_sorted_preserves_invariants(body: IRNode, new_label: str, new_text: str) -> None:
    """insert_sorted on a valid tree produces a tree that still passes check_invariants."""
    # Precondition: body is valid
    pre = check_invariants(body)
    assume(pre == [])

    # Pick the first chapter to insert into
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    assume(ch.label is not None)

    # Ensure label not already present
    existing = {c.label for c in ch.children if c.kind == IRNodeKind.SECTION}
    assume(new_label not in existing)

    new_section = IRNode(
        kind=IRNodeKind.SECTION,
        label=new_label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    result = insert_sorted(body, [("chapter", _label_text(ch.label))], new_section)

    violations = check_invariants(result)
    assert violations == [], f"Invariant violations after insert_sorted: {violations}"


# ---------------------------------------------------------------------------
# B3: replace_at on valid tree produces valid tree
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=50)
def test_replace_at_preserves_invariants(body: IRNode, new_text: str) -> None:
    """replace_at on a valid tree produces a tree that still passes check_invariants."""
    pre = check_invariants(body)
    assume(pre == [])

    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)

    replacement = IRNode(
        kind=IRNodeKind.SECTION,
        label=sec.label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    path: Path = (("chapter", _label_text(ch.label)), ("section", _label_text(sec.label)))
    result = replace_at(body, path, replacement)

    violations = check_invariants(result)
    assert violations == [], f"Invariant violations after replace_at: {violations}"


# ---------------------------------------------------------------------------
# B4: remove_at on valid tree produces valid tree
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_remove_at_preserves_invariants(body: IRNode) -> None:
    """remove_at on a valid tree still passes check_invariants."""
    pre = check_invariants(body)
    assume(pre == [])

    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)

    path: Path = (("chapter", _label_text(ch.label)), ("section", _label_text(sec.label)))
    result = remove_at(body, path)

    violations = check_invariants(result)
    assert violations == [], f"Invariant violations after remove_at: {violations}"


# ============================================================================
# GROUP C: Omission merge
# ============================================================================


def _make_omission() -> IRNode:
    """Create an omission marker node."""
    return IRNode(kind=IRNodeKind.OMISSION, label=None, attrs={"name": "omission"})


# ---------------------------------------------------------------------------
# C1: Result of section merge contains no omission nodes
# ---------------------------------------------------------------------------


@given(section_with_subsections(), SHORT_TEXT)
@settings(max_examples=50)
def test_section_merge_no_omissions_in_result(master_sec: IRNode, amend_text: str) -> None:
    """After merging section with omission, result has zero omission nodes."""
    master_subs = [c for c in master_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assume(len(master_subs) >= 2)

    # Build amendment: first subsection replaced, rest omitted
    amend_children: List[IRNode] = [
        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=amend_text),
        _make_omission(),
    ]
    amend_sec = IRNode(kind=IRNodeKind.SECTION, label=master_sec.label, children=tuple(amend_children))

    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    assert result is not None, "merge returned None unexpectedly"
    assert not _has_omission(result), f"Result still contains omission nodes: {[c.kind for c in result.children]}"


# ---------------------------------------------------------------------------
# C2: All non-omission content from amendment appears in result
# ---------------------------------------------------------------------------


@given(section_with_subsections(), SHORT_TEXT)
@settings(max_examples=50)
def test_section_merge_preserves_amendment_content(master_sec: IRNode, amend_text: str) -> None:
    """Non-omission text from amendment appears in merged result."""
    master_subs = [c for c in master_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assume(len(master_subs) >= 2)

    amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=amend_text)
    amend_children: List[IRNode] = [amend_sub, _make_omission()]
    amend_sec = IRNode(kind=IRNodeKind.SECTION, label=master_sec.label, children=tuple(amend_children))

    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    assert result is not None

    result_texts = _collect_non_omission_texts(result)
    assert amend_text in result_texts, f"Amendment text {amend_text!r} not found in result texts: {result_texts}"


# ---------------------------------------------------------------------------
# C3: If amendment has no omissions, merge returns None (caller does full replace)
# ---------------------------------------------------------------------------


@given(section_with_subsections(), SHORT_TEXT)
@settings(max_examples=50)
def test_section_merge_no_omissions_returns_none(master_sec: IRNode, amend_text: str) -> None:
    """If amendment section has no omissions at any level, merge returns None."""
    # Build an amendment with NO omission markers
    amend_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label=master_sec.label,
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=amend_text),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text=amend_text),
        ),
    )
    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    # When no omissions anywhere (section-level or inner-subsection), returns None
    assert result is None, f"Expected None for no-omission amendment, got {result}"


# ---------------------------------------------------------------------------
# C4: Subsection merge with trailing omission resolves all omissions
# ---------------------------------------------------------------------------


@given(SHORT_TEXT, SHORT_TEXT, SHORT_TEXT)
@settings(max_examples=50)
def test_subsection_merge_no_omissions_in_result(master_text_1: str, master_text_2: str, amend_text: str) -> None:
    """After subsection merge with trailing omission, result has no omission nodes."""
    master_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=master_text_1),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text=master_text_2),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=amend_text),
            _make_omission(),
        ),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)
    assert result is not None
    assert not _has_omission(result), f"Subsection result contains omission: {[c.kind for c in result.children]}"


# ---------------------------------------------------------------------------
# C5: Subsection merge preserves amendment content
# ---------------------------------------------------------------------------


@given(SHORT_TEXT, SHORT_TEXT, SHORT_TEXT)
@settings(max_examples=50)
def test_subsection_merge_preserves_amendment_content(master_text_1: str, master_text_2: str, amend_text: str) -> None:
    """Amendment paragraph text appears in the merged subsection result."""
    master_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=master_text_1),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text=master_text_2),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=amend_text),
            _make_omission(),
        ),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)
    assert result is not None

    result_texts = _collect_non_omission_texts(result)
    assert amend_text in result_texts, f"Amendment text {amend_text!r} not found in subsection merge result"


# ---------------------------------------------------------------------------
# C6: Subsection merge without omission returns None
# ---------------------------------------------------------------------------


@given(SHORT_TEXT, SHORT_TEXT)
@settings(max_examples=50)
def test_subsection_merge_no_omission_returns_none(master_text: str, amend_text: str) -> None:
    """If amendment subsection has no omission, _merge_subsection_with_omission_ir returns None."""
    master_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=master_text),),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text=amend_text),),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)
    assert result is None, f"Expected None for no-omission subsection amendment, got {result}"


# ---------------------------------------------------------------------------
# C7: Section merge with 1:1 omission/subsection mapping fills all slots
# ---------------------------------------------------------------------------


@given(section_with_subsections(), SHORT_TEXT)
@settings(max_examples=50)
def test_section_merge_1_to_1_fills_all_slots(master_sec: IRNode, amend_text: str) -> None:
    """When amendment has same count of slots (subsections + omissions) as master,
    result has the same number of subsections as master."""
    master_subs = [c for c in master_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assume(len(master_subs) >= 3)

    # Build amendment: replace first, omit second, replace third
    amend_children: List[IRNode] = [
        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=amend_text),
        _make_omission(),
        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text=amend_text),
    ]
    # Pad with omissions for remaining slots
    for _ in range(len(master_subs) - 3):
        amend_children.append(_make_omission())

    amend_sec = IRNode(kind=IRNodeKind.SECTION, label=master_sec.label, children=tuple(amend_children))

    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    assert result is not None

    result_subs = [c for c in result.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(result_subs) == len(master_subs), f"Expected {len(master_subs)} subsections, got {len(result_subs)}"


def test_section_merge_inner_omission_falls_back_to_subsection_merge() -> None:
    """Inner omission at subsection tail should still replace that subsection."""
    master_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old intro only"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old second moment"),),
            ),
        ),
    )
    amend_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tama laki ei koske:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new item 1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="new item 2"),
                    _make_omission(),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="replacement second moment"),),
            ),
        ),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    assert result is not None

    subs = [c for c in result.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(subs) == 2
    assert [c.kind for c in subs[0].children] == [IRNodeKind.INTRO, IRNodeKind.PARAGRAPH, IRNodeKind.PARAGRAPH]
    assert [c.label for c in subs[0].children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]
    assert subs[1].children[0].text == "replacement second moment"


def test_item_replace_does_not_promote_content_only_target_subsection() -> None:
    """Kohta replace must not widen into subsection-level replacement."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="8",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="8 §"),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 1"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 2"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 3"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="4",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="old intro"),),
                    ),
                ),
            ),
        ),
    )

    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="New intro:"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new item 1"),
        ),
    )
    muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="8", children=(amend_sub,))
    op = AmendmentOp(
        op_type="REPLACE",
        target_section="8",
        target_kind=TargetKind.SECTION,
        target_paragraph=4,
        target_item="1",
        source_statute="1992/1600",
    )

    class DummyMaster:
        def __init__(self, ir: IRNode) -> None:
            self.ir = ir

    from lawvm.finland.statute import ReplayState

    state = ReplayState(ir=body)
    result_state = _apply_deterministic_subsection_op(
        state,
        op,
        (("section", "8"),),
        muutos_ir,
        amend_sub,
        None,  # slot_assignment
        get_replay_profile("finlex_oracle"),
        "[1992/1600] REPLACE 8 § 4 mom 1 kohta",
    )

    assert result_state is None


# ============================================================================
# GROUP D: Additional tree_ops properties
# ============================================================================

# ---------------------------------------------------------------------------
# D1: resolve finds what replace_at placed
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=50)
def test_resolve_finds_replaced_node(body: IRNode, new_text: str) -> None:
    """After replace_at, resolve at the same path returns the new content."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)
    sec_label = _label_text(sec.label)
    ch_label = _label_text(ch.label)

    replacement = IRNode(
        kind=IRNodeKind.SECTION,
        label=sec.label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    path: Path = (("chapter", ch_label), ("section", sec_label))
    result = replace_at(body, path, replacement)

    found = resolve(result, path)
    assert found is not None, f"resolve returned None after replace_at at {path}"
    assert found.kind == IRNodeKind.SECTION
    assert found.label == sec.label
    assert len(found.children) == 1
    assert found.children[0].text == new_text


# ---------------------------------------------------------------------------
# D2: remove_at + insert_sorted roundtrip preserves label set
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_remove_insert_roundtrip_preserves_labels(body: IRNode) -> None:
    """Remove a section then reinsert it; the label set in that chapter is unchanged."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    assume(ch.label is not None)
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None)

    original_labels = {c.label for c in ch.children if c.kind == IRNodeKind.SECTION}

    parent_path: Path = (("chapter", _label_text(ch.label)),)
    remove_path: Path = parent_path + (("section", _label_text(sec.label)),)

    after_remove = remove_at(body, remove_path)
    after_reinsert = insert_sorted(after_remove, parent_path, sec)

    result_ch = next(c for c in after_reinsert.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    result_labels = {c.label for c in result_ch.children if c.kind == IRNodeKind.SECTION}
    assert result_labels == original_labels, f"Label set changed: {original_labels} -> {result_labels}"


# ---------------------------------------------------------------------------
# D3: replace_at is idempotent (replacing with same content is no semantic change)
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_replace_at_idempotent_with_same_content(body: IRNode) -> None:
    """Replacing a node with itself produces a tree with identical structure."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)
    sec_label = _label_text(sec.label)
    ch_label = _label_text(ch.label)

    path: Path = (("chapter", ch_label), ("section", sec_label))
    result = replace_at(body, path, sec)

    # The replaced section should be structurally identical
    found = resolve(result, path)
    assert found is not None
    assert found.kind == sec.kind
    assert found.label == sec.label
    assert found.text == sec.text
    assert len(found.children) == len(sec.children)


# ---------------------------------------------------------------------------
# D4: insert_sorted at deep path increases count at leaf parent only
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=50)
def test_insert_sorted_deep_path(body: IRNode, new_text: str) -> None:
    """insert_sorted into a chapter increases that chapter's section count by 1,
    without changing other chapters or body-level child count."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    assume(ch.label is not None)

    existing_section_labels = {c.label for c in ch.children if c.kind == IRNodeKind.SECTION}
    new_label = str(max(int(l) for l in existing_section_labels if l and l.isdigit()) + 100)

    new_section = IRNode(
        kind=IRNodeKind.SECTION,
        label=new_label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    result = insert_sorted(body, [("chapter", _label_text(ch.label))], new_section)

    # Body child count unchanged
    assert len(result.children) == len(body.children)

    # Target chapter section count increased by 1
    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    orig_sec_count = len([c for c in ch.children if c.kind == IRNodeKind.SECTION])
    result_sec_count = len([c for c in result_ch.children if c.kind == IRNodeKind.SECTION])
    assert result_sec_count == orig_sec_count + 1

    # Other chapters unchanged
    for orig_ch in chapters[1:]:
        if orig_ch.label is None:
            continue
        result_other = next(
            (c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == orig_ch.label),
            None,
        )
        assert result_other is not None
        assert len(result_other.children) == len(orig_ch.children)


# ============================================================================
# GROUP E: Label index tests
# ============================================================================

# ---------------------------------------------------------------------------
# E1: indexed find matches DFS find for all sections
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_indexed_find_matches_dfs(body: IRNode) -> None:
    """build_label_index + find(label_index=...) returns same path as DFS find."""
    idx = build_label_index(body)
    # Check all sections in all chapters
    for ch in body.children:
        if ch.kind != IRNodeKind.CHAPTER or ch.label is None:
            continue
        for sec in ch.children:
            if sec.kind != IRNodeKind.SECTION or sec.label is None:
                continue
            dfs_path = find(body, "section", sec.label)
            idx_path = find(body, "section", sec.label, label_index=idx)
            enum_idx_path = find(body, cast(str, IRNodeKind.SECTION), sec.label, label_index=idx)
            assert idx_path is not None, f"indexed find missed section {sec.label}"
            assert enum_idx_path is not None, f"enum-typed indexed find missed section {sec.label}"
            # Both should resolve to the same node
            dfs_node = resolve(body, dfs_path) if dfs_path else None
            idx_node = resolve(body, idx_path)
            enum_idx_node = resolve(body, enum_idx_path)
            assert dfs_node is not None
            assert idx_node is not None
            assert enum_idx_node is not None
            assert dfs_node.label == idx_node.label
            assert dfs_node.kind == idx_node.kind
            assert dfs_node.label == enum_idx_node.label
            assert dfs_node.kind == enum_idx_node.kind


# ---------------------------------------------------------------------------
# E2: indexed scoped find matches DFS scoped find
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=50)
def test_indexed_scoped_find_matches_dfs(body: IRNode) -> None:
    """Scoped find with index matches DFS scoped find."""
    idx = build_label_index(body)
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER and c.label]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    for sec in ch.children:
        if sec.kind != IRNodeKind.SECTION or sec.label is None:
            continue
        dfs_path = find(body, "section", sec.label, scope_kind="chapter", scope_label=ch.label)
        idx_path = find(body, "section", sec.label, scope_kind="chapter", scope_label=ch.label, label_index=idx)
        enum_idx_path = find(
            body,
            cast(str, IRNodeKind.SECTION),
            sec.label,
            scope_kind=cast(str, IRNodeKind.CHAPTER),
            scope_label=ch.label,
            label_index=idx,
        )
        if dfs_path is None:
            assert idx_path is None
            assert enum_idx_path is None
        else:
            assert idx_path is not None
            assert enum_idx_path is not None
            lhs = resolve(body, dfs_path)
            rhs = resolve(body, idx_path)
            enum_rhs = resolve(body, enum_idx_path)
            assert lhs is not None and rhs is not None
            assert enum_rhs is not None
            assert lhs.label == rhs.label
            assert lhs.label == enum_rhs.label


@given(well_formed_body())
@settings(max_examples=50)
def test_sparse_provision_index_matches_dfs_for_sections(body: IRNode) -> None:
    """A section/chapter/part-only index still serves hot section lookups correctly."""
    idx = build_label_index(body, indexed_kinds=frozenset({"section", "chapter", "part"}))
    for ch in body.children:
        if ch.kind != IRNodeKind.CHAPTER or ch.label is None:
            continue
        for sec in ch.children:
            if sec.kind != IRNodeKind.SECTION or sec.label is None:
                continue
            dfs_path = find(body, "section", sec.label, scope_kind="chapter", scope_label=ch.label)
            idx_path = find(
                body,
                "section",
                sec.label,
                scope_kind="chapter",
                scope_label=ch.label,
                label_index=idx,
            )
            assert dfs_path is not None
            assert idx_path is not None
            lhs = resolve(body, dfs_path)
            rhs = resolve(body, idx_path)
            assert lhs is not None and rhs is not None
            assert lhs.label == rhs.label


# ============================================================================
# GROUP F: Bug fix regression tests
# ============================================================================

# ---------------------------------------------------------------------------
# F1: find() DFS returns actual stored label, not query label (Bug #5)
# ---------------------------------------------------------------------------


def test_find_dfs_returns_actual_stored_label_not_query_label() -> None:
    """When searching for "1" but the stored label is "1.", the returned path
    should contain "1." (actual label), not "1" (query label)."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1.", text="section with period label"),),
    )
    # Search without label_index (DFS path)
    path = find(body, "section", "1")
    assert path is not None, "find() should match '1.' when searching for '1'"
    # The returned path should use the actual stored label "1.", not the query "1"
    assert path == (("section", "1."),), f"Expected path with actual label '1.', got {path}"
    # Verify the path resolves correctly
    node = resolve(body, path)
    assert node is not None
    assert node.label == "1."
    assert node.text == "section with period label"


def test_find_dfs_returns_actual_label_in_nested_tree() -> None:
    """DFS find returns the actual stored label at every level of the path."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(IRNode(kind=IRNodeKind.SECTION, label="5.", text="nested with period"),),
            ),
        ),
    )
    path = find(body, "section", "5")
    assert path is not None
    # The section step should use actual label "5.", not query "5"
    assert path[-1] == ("section", "5."), f"Expected last step ('section', '5.'), got {path[-1]}"
    node = resolve(body, path)
    assert node is not None
    assert node.label == "5."


def test_find_dfs_and_indexed_return_same_label() -> None:
    """DFS and indexed paths should return the same actual stored labels."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="3.", text="with period"),),
    )
    dfs_path = find(body, "section", "3")
    idx = build_label_index(body)
    idx_path = find(body, "section", "3", label_index=idx)
    enum_idx_path = find(body, cast(str, IRNodeKind.SECTION), "3", label_index=idx)
    assert dfs_path is not None
    assert idx_path is not None
    assert enum_idx_path is not None
    assert dfs_path == idx_path, f"DFS path {dfs_path} != indexed path {idx_path}"
    assert dfs_path == enum_idx_path, f"DFS path {dfs_path} != enum indexed path {enum_idx_path}"


# ---------------------------------------------------------------------------
# F2: check_invariants detects normalized-duplicate labels (Bug #6)
# ---------------------------------------------------------------------------


def test_check_invariants_detects_normalized_duplicate_labels() -> None:
    """Siblings with labels "1" and "1." of the same kind should trigger a
    normalized-duplicate violation, since mutators treat them as the same slot."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
                    IRNode(kind=IRNodeKind.SECTION, label="1.", text="second"),
                ),
            ),
        ),
    )
    violations = check_invariants(body)
    norm_dupes = [v for v in violations if "normalized-duplicate" in v]
    assert len(norm_dupes) >= 1, (
        f"Expected normalized-duplicate violation for section:1 vs section:1., got violations: {violations}"
    )


def test_check_invariants_no_false_positive_for_distinct_normalized_labels() -> None:
    """Labels that are genuinely distinct after normalization should not trigger."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="second"),
        ),
    )
    violations = check_invariants(body)
    norm_dupes = [v for v in violations if "normalized-duplicate" in v]
    assert norm_dupes == [], f"Unexpected normalized-duplicate violation: {norm_dupes}"
