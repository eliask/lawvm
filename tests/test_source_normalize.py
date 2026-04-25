"""Tests for the explicit source normalization phase.

Verifies that normalize_source_ir:
  1. Detects and corrects item-style subsection pathology (TAG_RECLASSIFY),
     emitting a SourceNormalizationFact witness.
  2. Detects and removes editorial block children (EDITORIAL_STRIP),
     emitting a SourceNormalizationFact witness.
  3. Normalizes whitespace in text content (WHITESPACE).
  4. Detects numbering anomalies -- gaps (NUMBERING_REPAIR) and
     duplicates (DUPLICATE_DROP) -- among sibling items.
  5. Supports shape-driven sparse payload repairs.
  6. Leaves unaffected nodes untouched (no facts emitted, same node returned).
  7. Handles nested pathologies by returning one fact per corrected node.
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import (
    IRNodeKind,
    SourceNormalizationBasis,
    SourceNormalizationKind,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.grafter import _fi_label_postprocessor
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.source_normalization_kinds import (
    BASE_DIGIT_RESET_SPLIT,
    BASE_INTRO_LIST_RESTART_SPLIT,
    BASE_DUPLICATE_SIBLING_DROP,
    BASE_DUPLICATE_TAIL_SPLIT,
    TRAILING_CHAPTER_REPARENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subsection_xml_with_item_num(label: str = "9", letter_children: int = 3) -> etree._Element:
    """Build a <subsection> element with item-style num and letter-labeled paragraphs."""
    letters = "abcdefghij"[:letter_children]
    para_xml = "\n".join(
        f"""<paragraph><num>{ch})</num><content><p>text {ch}</p></content></paragraph>"""
        for ch in letters
    )
    return etree.fromstring(
        f"""
        <subsection>
          <num>{label})</num>
          <intro><p>Definition list:</p></intro>
          {para_xml}
        </subsection>
        """
    )


def _content_node_with_image_block() -> IRNode:
    """Build an IRNode content with an image-block child (as xml_ir.py produces it)."""
    return IRNode(
        kind=IRNodeKind.CONTENT,
        text="some legal text",
        children=(
            IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "image"}),
        ),
    )


def _plain_subsection_node() -> IRNode:
    """Build a plain, non-pathological subsection IRNode."""
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.CONTENT, text="Normaali momentti."),
        ),
    )


# ---------------------------------------------------------------------------
# TAG_RECLASSIFY: item-style subsection -> paragraph
# ---------------------------------------------------------------------------


class TestTagReclassify:
    def test_reclassifies_item_style_subsection(self) -> None:
        """normalize_source_ir corrects <subsection num='9)'> with letter paragraphs."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("9", 3), _fi_label_postprocessor)
        assert raw.kind == IRNodeKind.SUBSECTION, "fi_xml_to_ir_node must produce raw SUBSECTION"

        normalized, facts = normalize_source_ir(raw, "2002/672")

        assert normalized.kind == IRNodeKind.PARAGRAPH
        assert normalized.label == "9"

    def test_emits_tag_reclassify_fact(self) -> None:
        """A TAG_RECLASSIFY SourceNormalizationFact is emitted for each corrected node."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("9", 2), _fi_label_postprocessor)
        _, facts = normalize_source_ir(raw, "1999/123")

        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert len(tag_facts) == 1

        fact = tag_facts[0]
        assert fact.statute_id == "1999/123"
        assert "9)" in fact.before
        assert "paragraph" in fact.after

    def test_reclassifies_letter_paragraph_children_to_subparagraph(self) -> None:
        """Letter-labeled paragraph children become subparagraph (alakohta)."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("5", 3), _fi_label_postprocessor)
        normalized, _ = normalize_source_ir(raw, "2020/1")

        subparagraphs = [c for c in normalized.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        assert [sp.label for sp in subparagraphs] == ["a", "b", "c"]

        paragraphs = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert paragraphs == []

    def test_preserves_intro_node(self) -> None:
        """The intro child is preserved after reclassification."""
        raw = fi_xml_to_ir_node(_subsection_xml_with_item_num("3", 2), _fi_label_postprocessor)
        normalized, _ = normalize_source_ir(raw, "2020/1")

        intros = [c for c in normalized.children if c.kind == IRNodeKind.INTRO]
        assert len(intros) == 1
        assert "Definition list" in (intros[0].text or "")

    def test_no_reclassify_for_plain_subsection(self) -> None:
        """A normal subsection without item-style num produces no TAG_RECLASSIFY facts."""
        node = _plain_subsection_node()
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.kind == IRNodeKind.SUBSECTION
        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []

    def test_no_reclassify_without_letter_paragraphs(self) -> None:
        """A subsection with item-style num but only digit-labeled paragraphs stays as subsection."""
        xml = etree.fromstring(
            """
            <subsection>
              <num>2)</num>
              <paragraph><num>1)</num><content><p>first</p></content></paragraph>
              <paragraph><num>2)</num><content><p>second</p></content></paragraph>
            </subsection>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
        normalized, facts = normalize_source_ir(raw, "2020/1")

        assert normalized.kind == IRNodeKind.SUBSECTION
        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []

    def test_keeps_section_scoped_item_style_subsection_as_subsection(self) -> None:
        """A section-scoped item-style subsection should remain a subsection container."""
        xml = etree.fromstring(
            """
            <section>
              <num>1 §</num>
              <subsection>
                <num>9)</num>
                <intro><p>Tässä momentissa tarkoitetaan:</p></intro>
                <paragraph><num>a)</num><content><p>ensimmäinen</p></content></paragraph>
                <paragraph><num>b)</num><content><p>toinen</p></content></paragraph>
              </subsection>
            </section>
            """
        )
        raw = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
        normalized, facts = normalize_source_ir(raw, "2002/672")

        assert normalized.kind == IRNodeKind.SECTION
        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        assert subsections[0].label == "9"
        assert [c.label for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH] == []
        assert check_invariants(normalized) == []

        tag_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.TAG_RECLASSIFY.value]
        assert tag_facts == []
        suspicious_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.SUSPICIOUS_SHAPE.value]
        assert len(suspicious_facts) == 1
        fact = suspicious_facts[0]
        assert fact.basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "section-scoped subsection" in fact.before
        assert "illegal section -> paragraph edge" in fact.after


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: image blocks
# ---------------------------------------------------------------------------


class TestEditorialStrip:
    def test_strips_image_block_child(self) -> None:
        """normalize_source_ir removes BLOCK(name=image) children."""
        node = _content_node_with_image_block()
        normalized, facts = normalize_source_ir(node, "2020/1262")

        image_children = [c for c in normalized.children if c.kind == IRNodeKind.BLOCK and c.attrs.get("name") == "image"]
        assert image_children == []

    def test_emits_editorial_strip_fact(self) -> None:
        """An EDITORIAL_STRIP SourceNormalizationFact is emitted for each image block removed."""
        node = _content_node_with_image_block()
        _, facts = normalize_source_ir(node, "2020/1262")

        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1

        fact = strip_facts[0]
        assert fact.statute_id == "2020/1262"
        assert "image" in fact.before
        assert "(removed)" in fact.after

    def test_preserves_non_image_children(self) -> None:
        """Non-image children are preserved when an image block is stripped."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "image"}),
                IRNode(kind=IRNodeKind.P, text="legal text"),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        assert normalized.children[0].kind == IRNodeKind.P
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1

    def test_no_strip_for_non_image_block(self) -> None:
        """A block without name='image' is not stripped."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "other"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert strip_facts == []


# ---------------------------------------------------------------------------
# CROSS_HEADING_HOIST: standalone sibling heading → structural heading facet
# ---------------------------------------------------------------------------


class TestCrossHeadingHoist:
    def test_hoists_cross_heading_sibling_into_following_section(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CROSS_HEADING, text="Kustannusten ja toiminnan seuraaminen"),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="4 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="T"),)),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(node, "1994/951")

        assert all(c.kind != IRNodeKind.CROSS_HEADING for c in normalized.children)
        sec = next(c for c in normalized.children if c.kind == IRNodeKind.SECTION and c.label == "4")
        headings = [c for c in sec.children if c.kind == IRNodeKind.HEADING]
        assert len(headings) == 1
        assert headings[0].text == "Kustannusten ja toiminnan seuraaminen"

        hoist_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.CROSS_HEADING_HOIST.value]
        assert len(hoist_facts) == 1
        assert hoist_facts[0].statute_id == "1994/951"


class TestTrailingChapterReparent:
    def test_reparents_trailing_root_chapter_under_preceding_part(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="18"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="19"),
            ),
        )

        normalized, facts = normalize_source_ir(node, "2012/746")

        assert len(normalized.children) == 1
        assert normalized.children[0].kind == IRNodeKind.PART
        chapter_labels = [c.label for c in normalized.children[0].children if c.kind == IRNodeKind.CHAPTER]
        assert chapter_labels == ["18", "19"]

        reparent_facts = [f for f in facts if f.kind_value == TRAILING_CHAPTER_REPARENT]
        assert len(reparent_facts) == 1
        assert reparent_facts[0].basis_value == SourceNormalizationBasis.PROFILE_INVALID.value
        assert "top-level chapter '19'" in reparent_facts[0].before
        assert "reparented under part 6" in reparent_facts[0].after

    def test_does_not_reparent_chapter_before_first_part(self) -> None:
        node = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="1"),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="2"),),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(node, "2020/1")

        assert [child.kind for child in normalized.children] == [IRNodeKind.CHAPTER, IRNodeKind.PART]
        assert not any(f.kind_value == TRAILING_CHAPTER_REPARENT for f in facts)


# ---------------------------------------------------------------------------
# Identity: unmodified nodes
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_plain_node_unchanged(self) -> None:
        """A node with no pathologies passes through unchanged; no facts emitted."""
        node = _plain_subsection_node()
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node  # unchanged -> same object
        assert facts == []

    def test_empty_body_node(self) -> None:
        """An empty body node passes through unchanged."""
        node = IRNode(kind=IRNodeKind.BODY, children=())
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        assert facts == []


# ---------------------------------------------------------------------------
# WHITESPACE normalization
# ---------------------------------------------------------------------------


class TestWhitespace:
    def test_collapses_multiple_spaces(self) -> None:
        """Multiple spaces in text are collapsed to a single space."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="hello   world")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "hello world"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_collapses_newlines_and_tabs(self) -> None:
        """Newlines and tabs are collapsed to single space."""
        node = IRNode(kind=IRNodeKind.P, text="line one\n\n  line two\ttab")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "line one line two tab"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Leading and trailing whitespace is stripped."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="  leading and trailing  ")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.text == "leading and trailing"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert len(ws_facts) == 1

    def test_no_fact_when_already_clean(self) -> None:
        """No whitespace fact is emitted when text is already normalized."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="already clean text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []

    def test_no_fact_for_empty_text(self) -> None:
        """No whitespace fact for nodes with empty text."""
        node = IRNode(kind=IRNodeKind.CONTENT, text="")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized is node
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []

    def test_does_not_modify_labels(self) -> None:
        """Whitespace normalization does not modify node labels."""
        node = IRNode(kind=IRNodeKind.PARAGRAPH, label="3 a", text="clean text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized.label == "3 a"
        ws_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.WHITESPACE.value]
        assert ws_facts == []


# ---------------------------------------------------------------------------
# NUMBERING anomaly detection: gaps and duplicates
# ---------------------------------------------------------------------------


class TestNumberingAnomalies:
    def test_detects_gap_in_sibling_numbering(self) -> None:
        """A gap (1, 2, 4, 5) emits a NUMBERING_REPAIR fact."""
        children = tuple(
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(n), text=f"text {n}")
            for n in [1, 2, 4, 5]
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        normalized, facts = normalize_source_ir(parent, "2020/1")

        gap_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value]
        assert len(gap_facts) == 1
        assert "3" in gap_facts[0].before  # expected 3
        # All children preserved (gap doesn't remove anything)
        para_children = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(para_children) == 4

    def test_detects_duplicate_and_drops_second(self) -> None:
        """Duplicate labels (1, 2, 2, 3) emit DUPLICATE_DROP and keep first occurrence."""
        children = tuple(
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(n), text=f"text {n} v{i}")
            for i, n in enumerate([1, 2, 2, 3])
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        normalized, facts = normalize_source_ir(parent, "2020/1")

        dup_facts = [f for f in facts if f.kind_value == BASE_DUPLICATE_SIBLING_DROP]
        assert len(dup_facts) == 1
        assert "2" in dup_facts[0].before

        # Only 3 children remain (duplicate dropped)
        para_children = [c for c in normalized.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(para_children) == 3
        # First occurrence of label 2 is kept
        assert para_children[1].text == "text 2 v1"

    def test_no_anomaly_for_monotonic_sequence(self) -> None:
        """A clean 1, 2, 3 sequence produces no numbering facts."""
        children = tuple(
            IRNode(kind=IRNodeKind.PARAGRAPH, label=str(n), text=f"text {n}")
            for n in [1, 2, 3]
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []


class TestIntroListRestartSplit:
    def test_splits_standalone_intro_then_numbered_list_subsection(self) -> None:
        section = IRNode(
            kind=IRNodeKind.SECTION,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="4 §"),
                IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.INTRO,
                            text="Standalone earlier moment.",
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.CONTENT, text="The authority records the following:"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="item one;"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2)"),
                                IRNode(kind=IRNodeKind.CONTENT, text="item two."),
                            ),
                        ),
                    ),
                ),
            ),
        )

        normalized, facts = normalize_source_ir(section, "2017/367-fixture")

        subsections = [c for c in normalized.children if c.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 2
        assert subsections[0].label == "2"
        assert subsections[0].children == (IRNode(kind=IRNodeKind.CONTENT, text="Standalone earlier moment."),)
        assert subsections[1].children[0] == IRNode(
            kind=IRNodeKind.INTRO,
            text="The authority records the following:",
        )
        assert [c.label for c in subsections[1].children[1:]] == ["1", "2"]

        split_facts = [f for f in facts if f.kind_value == BASE_INTRO_LIST_RESTART_SPLIT]
        assert len(split_facts) == 1

    def test_no_anomaly_for_single_child(self) -> None:
        """A single numbered child produces no numbering facts."""
        children = (IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="only one"),)
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []

    def test_ignores_non_numbered_kinds(self) -> None:
        """Non-numbered node kinds (CONTENT, P, etc.) are not checked for numbering."""
        children = (
            IRNode(kind=IRNodeKind.CONTENT, label="1", text="a"),
            IRNode(kind=IRNodeKind.CONTENT, label="1", text="b"),
        )
        parent = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=children)
        _, facts = normalize_source_ir(parent, "2020/1")

        numbering_facts = [
            f for f in facts
            if f.kind_value in (
                SourceNormalizationKind.NUMBERING_REPAIR.value,
                BASE_DUPLICATE_SIBLING_DROP,
            )
        ]
        assert numbering_facts == []


# ---------------------------------------------------------------------------
# EDITORIAL_STRIP: note, footnote, authorialNote blocks
# ---------------------------------------------------------------------------


class TestEditorialStripExtended:
    def test_strips_note_block(self) -> None:
        """BLOCK(name=note) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "note"}),),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "note" in strip_facts[0].before

    def test_strips_footnote_block(self) -> None:
        """BLOCK(name=footnote) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(IRNode(kind=IRNodeKind.BLOCK, attrs={"name": "footnote"}),),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "footnote" in strip_facts[0].before

    def test_strips_authorial_note(self) -> None:
        """HCONTAINER(name=authorialNote) is stripped as editorial."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="legal text",
            children=(
                IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "authorialNote"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 0
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert len(strip_facts) == 1
        assert "authorialNote" in strip_facts[0].before

    def test_preserves_non_editorial_hcontainer(self) -> None:
        """HCONTAINER with non-editorial name is preserved."""
        node = IRNode(
            kind=IRNodeKind.CONTENT,
            text="text",
            children=(
                IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "omission"}),
            ),
        )
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert len(normalized.children) == 1
        strip_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.EDITORIAL_STRIP.value]
        assert strip_facts == []


# ---------------------------------------------------------------------------
# Disabled sparse-tail repairs
# ---------------------------------------------------------------------------


class TestSparsePayloadRepairs:
    def test_non_matching_sparse_repairs_are_noop(self) -> None:
        """Disabled sparse-tail repairs should leave unrelated nodes untouched."""
        node = IRNode(kind=IRNodeKind.SECTION, label="1", text="text")
        normalized, facts = normalize_source_ir(node, "2020/1")

        assert normalized == node
        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_1977_18_section_2_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>2§</num>
                  <subsection>
                    <intro><p>Eläkkeen saamisen edellytyksenä on:</p></intro>
                    <paragraph><num>1)</num><content><p>että luopuja ... kaksi hehtaaria;</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>että luopujan ...</p></content></paragraph>
                    <paragraph><num>3)</num><content><p>että luopuja ... 45 vuotta; ja</p></content></paragraph>
                    <paragraph><num>4)</num><content><p>että luopuminen ...</p><p class="omission"/></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1977/18")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        assert [child.kind for child in subsection.children] == [
            IRNodeKind.INTRO,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.PARAGRAPH,
            IRNodeKind.OMISSION,
        ]
        assert [child.label for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4"]
        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_2008_342_section_21_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>21 §</num>
                  <heading>Muu ydinenergian käyttö</heading>
                  <subsection>
                    <intro>
                      <p>Lupa 2 §:n 1 momentin 2―6 kohdassa ja 2 §:n 2 momentin 1 kohdassa tarkoitettuun toimintaan voidaan myöntää, milloin toiminta sitä edellyttää jos:</p>
                    </intro>
                    <paragraph>
                      <num>1)</num>
                      <content><p>ydinenergian käyttö täyttää tämän lain mukaiset turvallisuutta koskevat vaatimukset;</p></content>
                    </paragraph>
                    <hcontainer name="omission"/>
                    <paragraph>
                      <num>7)</num>
                      <content><p>niiden vieraiden valtioiden suostumukset ...; ja</p></content>
                    </paragraph>
                  </subsection>
                  <subsection>
                    <content>
                      <p>ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa.</p>
                    </content>
                  </subsection>
                  <hcontainer name="omission"/>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2008/342")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 2
        assert any(child.kind == IRNodeKind.OMISSION for child in normalized.children)

        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert subparagraphs == []
        assert len([child for child in seventh_para.children if child.kind == IRNodeKind.CONTENT]) == 1

        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_1994_1420_section_21_sparse_repair_is_not_applied(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>21 §</num>
                  <heading>Muu ydinenergian käyttö</heading>
                  <subsection>
                    <intro>
                      <p>Lupa 2 §:n 1 momentin 2―5 kohdassa tarkoitettuun toimintaan voidaan myöntää, jos, milloin toiminta sitä edellyttää:</p>
                    </intro>
                    <hcontainer name="omission"/>
                    <paragraph>
                      <num>5)</num>
                      <content><p>hakijalla on käytettävänään tarpeellinen asiantuntemus ...</p></content>
                    </paragraph>
                    <paragraph>
                      <num>6)</num>
                      <content><p>hakijalla harkitaan olevan taloudelliset ja muut tarpeelliset edellytykset ...</p></content>
                    </paragraph>
                    <paragraph>
                      <num>7)</num>
                      <content><p>niiden vieraiden valtioiden suostumukset ...; ja</p></content>
                      <content><p>ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa.</p></content>
                    </paragraph>
                  </subsection>
                  <hcontainer name="omission"/>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1994/1420")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 1
        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        content_children = [child for child in seventh_para.children if child.kind == IRNodeKind.CONTENT]
        assert len(content_children) == 2
        subparagraphs = [child for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert subparagraphs == []

        repair_facts = [f for f in facts if "sparse" in f.kind_value]
        assert repair_facts == []

    def test_2008_342_section_3_keeps_lettered_items_5a_and_5b(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>3 §</num>
                  <heading>Määritelmät</heading>
                  <subsection>
                    <intro><p>Tässä laissa tarkoitetaan:</p></intro>
                    <paragraph><num>1)</num><content><p>ydinenergian käytöllä ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>4)</num><content><p>ydinjätehuollolla ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>5 a)</num><content><p>ydinvoimalaitoksella ...</p></content></paragraph>
                    <paragraph><num>5 b)</num><content><p>käytöstä poistamisella ...</p></content></paragraph>
                    <hcontainer name="omission"/>
                    <paragraph><num>7)</num><content><p>valmiusjärjestelyillä ...</p></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2008/342")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        paragraph_labels = [child.label for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert paragraph_labels == ["1", "4", "5a", "5b", "7"]

        duplicate_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.DUPLICATE_DROP.value]
        assert duplicate_facts == []

        gap_facts = [f for f in facts if f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value]
        assert len(gap_facts) == 3

    def test_1981_555_section_11_splits_terminal_proportionality_sentence(self) -> None:
        """Maa-aineslaki 11 § keeps the proportionality sentence as a separate 4 mom."""
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>11 §</num>
                  <heading>Lupamääräykset</heading>
                  <subsection>
                    <content>
                      <p>Ainesten ottamista koskevaan lupaan on liitettävä määräykset siitä, mitä hakijan on noudatettava hankkeesta aiheutuvien haittojen välttämiseksi tai rajoittamiseksi.</p>
                    </content>
                  </subsection>
                  <subsection>
                    <intro><p>Lupamääräykset on annettava:</p></intro>
                    <paragraph><num>1)</num><content><p>ottamisalueen rajauksesta, kaivausten ja leikkausten syvyydestä ja muodosta sekä ottamistoiminnan etenemissuunnista;</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>alueen suojaamisesta ja siistimisestä ottamisen aikana ja sen jälkeen; sekä</p></content></paragraph>
                    <paragraph><num>3)</num><content><p>puuston ja muun kasvillisuuden säilyttämisestä, uusimisesta ja uusista istutuksista ottamisen aikana ja sen jälkeen.</p></content></paragraph>
                  </subsection>
                  <subsection>
                    <intro><p>Lupamääräyksiä voidaan lisäksi antaa:</p></intro>
                    <paragraph><num>1)</num><content><p>ottamiseen liittyvistä laitteista ja liikenteen järjestämisestä erityisesti pohjaveden suojelemiseksi; sekä</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>ajasta, jonka kuluessa tämän pykälän nojalla määrätyt toimenpiteet on suoritettava.</p></content></paragraph>
                    <paragraph><num>2)</num><content><p>Määräykset eivät saa aiheuttaa luvan saajalle sellaista vahinkoa ja haittaa, jota on pidettävä hankkeen laajuuteen ja hänen saamaansa hyötyyn nähden kohtuuttomana.</p></content></paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "1981/555")

        subsections = [child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in subsections] == ["1", "2", "3", "4"]

        third = subsections[2]
        assert [child.label for child in third.children if child.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]

        fourth = subsections[3]
        assert fourth.kind == IRNodeKind.SUBSECTION
        assert fourth.label == "4"
        assert "Määräykset eivät saa aiheuttaa" in (next(
            child for child in fourth.children if child.kind == IRNodeKind.CONTENT
        ).text or "")

        repair_facts = [f for f in facts if f.kind_value == BASE_DUPLICATE_TAIL_SPLIT]
        assert len(repair_facts) == 1
        assert repair_facts[0].basis_value == SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR.value
        assert not any(f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value for f in facts)


class TestDigitResetSubparagraphSplit:
    def test_splits_digit_reset_buried_inside_subparagraph_run(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <section>
                  <num>1 §</num>
                  <subsection>
                    <num>1 mom.</num>
                    <paragraph>
                      <num>4)</num>
                      <intro><p>naudanliha-alan yhteisestä markkinajärjestelystä;</p></intro>
                      <subparagraph><num>a)</num><content><p>sonnipalkkio;</p></content></subparagraph>
                      <subparagraph><num>b)</num><content><p>härkäpalkkio;</p></content></subparagraph>
                      <subparagraph><num>5)</num><content><p>lampaan- ja vuohenliha-alan yhteisestä markkinajärjestelystä;</p></content></subparagraph>
                      <subparagraph><num>a)</num><content><p>uuhipalkkio;</p></content></subparagraph>
                      <subparagraph><num>b)</num><content><p>lisäpalkkio;</p></content></subparagraph>
                    </paragraph>
                  </subsection>
                </section>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2000/154")

        subsection = next(child for child in normalized.children if child.kind == IRNodeKind.SUBSECTION)
        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [child.label for child in paragraphs] == ["4", "5"]

        para4_subs = [child.label for child in paragraphs[0].children if child.kind == IRNodeKind.SUBPARAGRAPH]
        para5_subs = [child.label for child in paragraphs[1].children if child.kind == IRNodeKind.SUBPARAGRAPH]
        assert para4_subs == ["a", "b"]
        assert para5_subs == ["a", "b"]
        assert check_invariants(normalized) == []

        repair_facts = [f for f in facts if f.kind_value == BASE_DIGIT_RESET_SPLIT]
        assert any("digit-labelled subparagraph 5" in f.before for f in repair_facts)
        assert not any(f.kind_value == SourceNormalizationKind.NUMBERING_REPAIR.value for f in facts)

    def test_does_not_split_plain_lettered_subparagraph_run(self) -> None:
        raw = fi_xml_to_ir_node(
            etree.fromstring(
                """
                <paragraph>
                  <num>4)</num>
                  <content><p>otsikko:</p></content>
                  <subparagraph><num>a)</num><content><p>ensimmäinen</p></content></subparagraph>
                  <subparagraph><num>b)</num><content><p>toinen</p></content></subparagraph>
                </paragraph>
                """
            ),
            _fi_label_postprocessor,
        )

        normalized, facts = normalize_source_ir(raw, "2020/1")

        assert normalized.kind == IRNodeKind.PARAGRAPH
        assert normalized.label == "4"
        assert [child.label for child in normalized.children if child.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
        assert not any("digit-labelled subparagraph" in f.before for f in facts)
