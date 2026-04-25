from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_ir_ops import _relabel_paragraph_ir
from lawvm.finland.merge import _paragraph_to_subparagraph_ir
from tests.corpus_pin_helpers import pinned_replay
from lawvm.tools.section_keys import extract_ir_sections


def test_relabel_paragraph_ir_preserves_spaced_letter_suffix_display() -> None:
    paragraph = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 a)"),
            IRNode(kind=IRNodeKind.CONTENT, text="IMSBC-säännöstöllä ..."),
        ),
    )

    relabelled = _relabel_paragraph_ir(paragraph, "4a")

    assert relabelled.label == "4a"
    assert relabelled.children[0].kind == IRNodeKind.NUM
    assert relabelled.children[0].text == "4 a)"


def test_paragraph_to_subparagraph_ir_preserves_spaced_letter_suffix_display() -> None:
    paragraph = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 a)"),
            IRNode(kind=IRNodeKind.CONTENT, text="IMSBC-säännöstöllä ..."),
        ),
    )

    converted = _paragraph_to_subparagraph_ir(paragraph, "4a")

    assert converted.label == "4a"
    assert converted.children[0].kind == IRNodeKind.NUM
    assert converted.children[0].text == "4 a)"


def test_replay_xml_preserves_letter_suffix_item_spacing_for_2014_346() -> None:
    replay = pinned_replay("2014/346", mode="finlex_oracle", quiet=True)
    section = extract_ir_sections(replay.materialized_state.ir)["section:1"]

    num_text = None
    for child in section.children:
        if child.kind != IRNodeKind.SUBSECTION:
            continue
        for paragraph in child.children:
            if paragraph.kind != IRNodeKind.PARAGRAPH or paragraph.label != "3a":
                continue
            num_text = next(
                (grandchild.text for grandchild in paragraph.children if grandchild.kind == IRNodeKind.NUM),
                None,
            )
            break
        if num_text is not None:
            break

    assert num_text == "3 a)"
