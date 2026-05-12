"""Unit tests for lawvm.finland.chapter_seed — chapter-seeding helpers."""

from typing import Any, Dict, Optional, Tuple, cast

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.chapter_seed import (
    ChapterSeedDiagnostic,
    _chapters_in_gap,
    _chapter_missing_span_notice,
    _find_chapter_containers_with_omissions,
    _labels_in_missing_span,
    _last_chapter_label,
    _next_chapter_label,
    _op_targets_chapter,
    _rebuild_at_path,
    _strip_trailing_missing_span_notice,
    seed_missing_chapters,
)
from lawvm.finland.ops import AmendmentOp

# ---------------------------------------------------------------------------
# IRNode fixture helpers
# ---------------------------------------------------------------------------


def _chapter(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(children))


def _omission() -> IRNode:
    return IRNode(kind=IRNodeKind.OMISSION)


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _hcontainer(label: str = "", *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.HCONTAINER, label=label or None, children=tuple(children))


def _section(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _subsection(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _seedable(*pairs: Tuple[str, IRNode]) -> Dict[str, Tuple[str, IRNode]]:
    """Build a seedable dict from (label, ir_node) pairs, using 'dummy_mid' as amendment ID."""
    return {label: ("dummy_mid", node) for label, node in pairs}


class _FakeCorpus:
    def __init__(self, payloads: Dict[str, bytes]) -> None:
        self._payloads = payloads

    def read_source(self, statute_id: str) -> Optional[bytes]:
        return self._payloads.get(statute_id)


def _chapter_xml(label: str, section_label: str) -> bytes:
    return f"""
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <chapter eId="chp_{label}">
        <num>{label} luku</num>
        <section eId="chp_{label}__sec_{section_label}">
          <num>{section_label} §</num>
        </section>
      </chapter>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


# ---------------------------------------------------------------------------
# _find_chapter_containers_with_omissions
# ---------------------------------------------------------------------------


def test_find_chapter_containers_direct_body() -> None:
    tree = _body(_chapter("1"), _omission(), _chapter("3"))
    results = _find_chapter_containers_with_omissions(tree)
    assert len(results) == 1
    path, node = results[0]
    assert path == []
    assert node is tree


def test_find_chapter_containers_no_result_when_no_omission() -> None:
    tree = _body(_chapter("1"), _chapter("2"), _chapter("3"))
    results = _find_chapter_containers_with_omissions(tree)
    assert results == []


def test_find_chapter_containers_no_result_when_no_chapter() -> None:
    tree = _body(_section("1"), _omission())
    results = _find_chapter_containers_with_omissions(tree)
    assert results == []


def test_find_chapter_containers_nested_hcontainer() -> None:
    inner = _hcontainer("wrapper", _chapter("2"), _omission(), _chapter("5"))
    tree = _body(inner)
    results = _find_chapter_containers_with_omissions(tree)
    assert len(results) == 1
    path, node = results[0]
    assert node is inner
    # Path should have one step: (hcontainer, wrapper)
    assert path == [("hcontainer", "wrapper")]


def test_find_chapter_containers_does_not_recurse_into_chapter_children() -> None:
    # A chapter containing an omission but no sibling chapter — not a container
    ch = _chapter("1", _omission())
    tree = _body(ch)
    results = _find_chapter_containers_with_omissions(tree)
    # body has only a chapter, no omission at body level -> no match
    assert results == []


# ---------------------------------------------------------------------------
# _last_chapter_label
# ---------------------------------------------------------------------------


def test_last_chapter_label_returns_last() -> None:
    children = [_chapter("1"), _chapter("2"), _chapter("3")]
    assert _last_chapter_label(children) == "3"


def test_last_chapter_label_returns_none_when_no_chapters() -> None:
    children = [_omission(), _section("1")]
    assert _last_chapter_label(children) is None


def test_last_chapter_label_skips_non_chapter_at_end() -> None:
    children = [_chapter("2"), _omission()]
    assert _last_chapter_label(children) == "2"


# ---------------------------------------------------------------------------
# _next_chapter_label
# ---------------------------------------------------------------------------


def test_next_chapter_label_returns_immediately_following() -> None:
    c1 = _chapter("1")
    omit = _omission()
    c3 = _chapter("3")
    children = [c1, omit, c3]
    assert _next_chapter_label(omit, children) == "3"


def test_next_chapter_label_returns_none_when_last() -> None:
    c1 = _chapter("1")
    c2 = _chapter("2")
    children = [c1, c2]
    assert _next_chapter_label(c2, children) is None


def test_next_chapter_label_skips_non_chapter_nodes() -> None:
    c1 = _chapter("1")
    omit = _omission()
    sec = _section("5")
    c3 = _chapter("3")
    children = [c1, omit, sec, c3]
    assert _next_chapter_label(omit, children) == "3"


# ---------------------------------------------------------------------------
# _chapters_in_gap
# ---------------------------------------------------------------------------


def test_chapters_in_gap_returns_labels_between_bounds() -> None:
    seedable = _seedable(
        ("2", _chapter("2")),
        ("3", _chapter("3")),
        ("5", _chapter("5")),
    )
    result = _chapters_in_gap(seedable, prev_label="1", next_label="4")
    assert set(result) == {"2", "3"}


def test_chapters_in_gap_no_bound_includes_everything_before_next() -> None:
    seedable = _seedable(("1", _chapter("1")), ("2", _chapter("2")))
    result = _chapters_in_gap(seedable, prev_label=None, next_label="3")
    assert set(result) == {"1", "2"}


def test_chapters_in_gap_no_next_bound_includes_everything_after_prev() -> None:
    seedable = _seedable(("5", _chapter("5")), ("7", _chapter("7")))
    result = _chapters_in_gap(seedable, prev_label="4", next_label=None)
    assert set(result) == {"5", "7"}


def test_chapters_in_gap_empty_when_none_in_range() -> None:
    seedable = _seedable(("10", _chapter("10")))
    result = _chapters_in_gap(seedable, prev_label="1", next_label="3")
    assert result == []


def test_labels_in_missing_span_filters_declared_range() -> None:
    result = _labels_in_missing_span(["7", "8", "10"], start_label="7", end_label="10")
    assert result == ["7", "8"]


def test_chapter_missing_span_notice_reads_trailing_placeholder_text() -> None:
    chapter = _chapter(
        "6",
        IRNode(
            kind=IRNodeKind.SECTION,
            label="32",
            children=(
                _subsection("1", _content("Real content.")),
                _subsection("4", _content("Puuttuu luvut 7-11")),
                _omission(),
            ),
        ),
    )
    assert _chapter_missing_span_notice(chapter) == ("7", "11")


def test_strip_trailing_missing_span_notice_removes_placeholder_tail() -> None:
    chapter = _chapter(
        "6",
        IRNode(
            kind=IRNodeKind.SECTION,
            label="32",
            children=(
                _subsection("1", _content("Real content.")),
                _subsection("4", _content("Puuttuu luvut 7-11")),
                _omission(),
            ),
        ),
    )
    stripped = _strip_trailing_missing_span_notice(chapter)
    section = next(child for child in stripped.children if child.kind == IRNodeKind.SECTION)
    assert [child.kind for child in section.children] == [IRNodeKind.SUBSECTION]
    assert section.children[0].label == "1"


# ---------------------------------------------------------------------------
# _rebuild_at_path
# ---------------------------------------------------------------------------


def test_rebuild_at_path_empty_path_returns_replacement() -> None:
    tree = _body(_chapter("1"))
    replacement = _body(_chapter("2"))
    result = _rebuild_at_path(tree, [], replacement)
    assert result is replacement


def test_rebuild_at_path_one_level_replaces_matching_child() -> None:
    hc = _hcontainer("wrapper", _chapter("1"))
    tree = _body(hc)
    new_hc = _hcontainer("wrapper", _chapter("1"), _chapter("2"))
    result = _rebuild_at_path(tree, [("hcontainer", "wrapper")], new_hc)
    # The hcontainer should be replaced
    assert result.children[0] is new_hc


def test_rebuild_at_path_leaves_siblings_unchanged() -> None:
    hc1 = _hcontainer("a", _chapter("1"))
    hc2 = _hcontainer("b", _chapter("2"))
    tree = _body(hc1, hc2)
    new_hc1 = _hcontainer("a", _chapter("1"), _chapter("3"))
    result = _rebuild_at_path(tree, [("hcontainer", "a")], new_hc1)
    assert result.children[0] is new_hc1
    assert result.children[1] is hc2


# ---------------------------------------------------------------------------
# _op_targets_chapter
# ---------------------------------------------------------------------------


def test_op_targets_chapter_true_for_chapter_level_op() -> None:
    op = AmendmentOp(op_id="", op_type="REPLACE", target_kind=TargetKind.CHAPTER, target_section="3")
    assert _op_targets_chapter(op, {"3", "4"}) is True


def test_op_targets_chapter_true_for_section_scoped_to_chapter() -> None:
    op = AmendmentOp(
        op_id="", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="12", target_chapter="3"
    )
    assert _op_targets_chapter(op, {"3", "4"}) is True


def test_op_targets_chapter_false_when_chapter_not_in_set() -> None:
    op = AmendmentOp(op_id="", op_type="REPLACE", target_kind=TargetKind.CHAPTER, target_section="9")
    assert _op_targets_chapter(op, {"3", "4"}) is False


def test_op_targets_chapter_false_for_unscoped_section_op() -> None:
    op = AmendmentOp(op_id="", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="5")
    assert _op_targets_chapter(op, {"3", "4", "5"}) is False


def test_seed_missing_chapters_seeds_textual_gap_notice() -> None:
    tree = _body(
        _chapter(
            "6",
            IRNode(
                kind=IRNodeKind.SECTION,
                label="32",
                children=(
                    _subsection("1", _content("Existing section content.")),
                    _subsection("4", _content("Puuttuu luvut 7-11")),
                    _omission(),
                ),
            ),
        ),
        _chapter("11", _section("55")),
    )
    corpus = _FakeCorpus(
        {
            "1993/700": _chapter_xml("7", "33"),
            "1993/701": _chapter_xml("8", "38"),
            "1993/999": b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><body/></act></akomaNtoso>',
        }
    )
    diagnostics: list[ChapterSeedDiagnostic] = []

    updated, seeded = seed_missing_chapters(
        tree,
        ["1993/700", "1993/701", "1993/999"],
        cast(Any, corpus),
        diagnostics_out=diagnostics,
    )

    chapters = [child for child in updated.children if child.kind == IRNodeKind.CHAPTER]
    assert [child.label for child in chapters] == ["6", "7", "8", "11"]
    assert seeded == {("7", "1993/700"), ("8", "1993/701")}
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_chapter_seed_inserted_from_amendment_body",
        "fi_chapter_seed_inserted_from_amendment_body",
    ]
    assert [(diagnostic.chapter_label, diagnostic.source_statute) for diagnostic in diagnostics] == [
        ("7", "1993/700"),
        ("8", "1993/701"),
    ]
    assert all(diagnostic.family == "ontology_normalization" for diagnostic in diagnostics)
    assert all(diagnostic.phase == "payload_normalization" for diagnostic in diagnostics)
    assert all(diagnostic.blocking is False for diagnostic in diagnostics)
    assert all(diagnostic.strict_disposition == "block" for diagnostic in diagnostics)
    assert all(diagnostic.quirks_disposition == "apply" for diagnostic in diagnostics)
    section32 = next(child for child in chapters[0].children if child.kind == IRNodeKind.SECTION)
    assert len(section32.children) == 1
    assert "Puuttuu luvut" not in str(section32.children[0].text or "")


def test_seed_missing_chapters_records_source_scan_failures() -> None:
    tree = _body(_chapter("1"), _omission(), _chapter("3"))
    corpus = _FakeCorpus({"1993/bad": b"<akomaNtoso><broken></akomaNtoso>"})
    diagnostics: list[ChapterSeedDiagnostic] = []

    updated, seeded = seed_missing_chapters(
        tree,
        ["1993/missing", "1993/bad"],
        cast(Any, corpus),
        diagnostics_out=diagnostics,
    )

    assert updated is tree
    assert seeded == set()
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_chapter_seed_source_missing",
        "fi_chapter_seed_source_xml_parse_failed",
    ]
    assert [diagnostic.source_statute for diagnostic in diagnostics] == [
        "1993/missing",
        "1993/bad",
    ]
    assert all(diagnostic.family == "source_pathology" for diagnostic in diagnostics)
    assert all(diagnostic.phase == "acquisition" for diagnostic in diagnostics)
    assert all(diagnostic.blocking is True for diagnostic in diagnostics)
    assert all(diagnostic.strict_disposition == "block" for diagnostic in diagnostics)
    assert diagnostics[0].as_detail()["rule_id"] == "fi_chapter_seed_source_missing"
