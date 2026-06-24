"""Regression for body-section definition ordered lists parsed as paragraphs.

Finance Act 2000 s. 128(8) defines terms via an `<UnorderedList Class="Definition">`
whose nested `<OrderedList Type="alpha">` items are alphabetical paragraphs (a), (b),
etc.  The oracle materialises these as `section-128-8-a` … EIDs, so amendments
targeting `section:128/subsection:8/paragraph:a` must resolve.

AGENTS.md obligations:
  §0  owned rule
  §15 synthetic + corpus regression + negative + strict-mode where applicable
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _parse_definition_ordered_list


def _body_subsection_with_definition_list() -> ET._Element:
    """Mimics Finance Act 2000 s. 128(8)."""
    xml = f"""<P2 xmlns="{_LEG_NS}" id="section-128-8">
      <Pnumber>8</Pnumber>
      <P2para>
        <Text>In this section—</Text>
        <UnorderedList Decoration="none" Class="Definition">
          <ListItem>
            <Para>
              <Text>"land register" means—</Text>
              <OrderedList Type="alpha" Decoration="parens">
                <ListItem><Para><Text>England and Wales text.</Text></Para></ListItem>
                <ListItem><Para><Text>Scotland text.</Text></Para></ListItem>
                <ListItem><Para><Text>Northern Ireland text.</Text></Para></ListItem>
              </OrderedList>
            </Para>
          </ListItem>
        </UnorderedList>
      </P2para>
    </P2>"""
    return ET.fromstring(xml)


def test_body_definition_ordered_list_items_are_paragraphs() -> None:
    subsection = _body_subsection_with_definition_list()
    ordered_list = subsection.find(
        f".//{{{_LEG_NS}}}UnorderedList//{{{_LEG_NS}}}OrderedList"
    )
    assert ordered_list is not None
    # _parse_definition_ordered_list is passed the OrderedList and its direct
    # parent (the Para containing the defined term)
    para = ordered_list.getparent()
    assert para is not None
    nodes = _parse_definition_ordered_list(ordered_list, para)
    assert len(nodes) == 3
    for node in nodes:
        # kind must be a replay-addressable paragraph, not ITEM
        assert node.kind == IRNodeKind.PARAGRAPH
        assert node.label in {"a", "b", "c"}
        assert node.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved"
        assert node.attrs.get("definition_term") == "land register"
        assert "definition_child_label" in node.attrs
        assert node.text


def test_body_definition_ordered_list_items_get_alpha_labels() -> None:
    subsection = _body_subsection_with_definition_list()
    ordered_list = subsection.find(
        f".//{{{_LEG_NS}}}UnorderedList//{{{_LEG_NS}}}OrderedList"
    )
    assert ordered_list is not None
    para = ordered_list.getparent()
    assert para is not None
    nodes = _parse_definition_ordered_list(ordered_list, para)
    labels = [node.label for node in nodes]
    assert labels == ["a", "b", "c"]
