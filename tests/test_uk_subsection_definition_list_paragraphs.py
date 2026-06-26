"""Regression for subsection definition unordered-lists with multiple nested alpha lists.

Finance Act 2000 s. 107(7) defines several terms under one
``<UnorderedList Class="Definition">``.  Some terms expand into nested
``<OrderedList Type="alpha">`` sub-lists.  When these were flattened to direct
paragraph children of the subsection, duplicate ``paragraph:a``, ``paragraph:b``
etc. labels were produced and the core all_tree detector flagged both
``duplicate_label`` and ``sort_order`` violations.  The parser now wraps each
top-level definition item as an unlabelled paragraph and attaches its nested
alpha sub-list items as children.
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.uk_grafter import (
    _LEG_NS,
    _local_structural_text,
    _parse_definition_unordered_list,
)


def _subsection_with_multi_definition_list() -> ET._Element:
    """Mimics Finance Act 2000 s. 107(7) structure."""
    xml = f"""<P2 xmlns="{_LEG_NS}" id="section-107-7">
      <Pnumber>7</Pnumber>
      <P2para>
        <Text>In this section—</Text>
        <UnorderedList Decoration="none" Class="Definition">
          <ListItem>
            <Para><Text>"closing year", in relation to a syndicate, has a meaning;</Text></Para>
          </ListItem>
          <ListItem>
            <Para>
              <Text>"general insurer" means any of the following—</Text>
              <OrderedList Type="alpha" Decoration="parens">
                <ListItem><Para><Text>a company to which Part II applies;</Text></Para></ListItem>
                <ListItem><Para><Text>an EC company;</Text></Para></ListItem>
                <ListItem><Para><Text>a controlled foreign company; and</Text></Para></ListItem>
                <ListItem><Para><Text>an underwriting member of Lloyd’s.</Text></Para></ListItem>
              </OrderedList>
            </Para>
          </ListItem>
          <ListItem>
            <Para>
              <Text>"period of account" means—</Text>
              <OrderedList Type="alpha" Decoration="parens">
                <ListItem><Para><Text>except in relation to an underwriting member, a period;</Text></Para></ListItem>
                <ListItem><Para><Text>in relation to such a member, an underwriting year.</Text></Para></ListItem>
              </OrderedList>
            </Para>
          </ListItem>
        </UnorderedList>
      </P2para>
    </P2>"""
    return ET.fromstring(xml)


def test_multi_item_definition_unordered_list_wraps_items() -> None:
    subsection = _subsection_with_multi_definition_list()
    ul = subsection.find(f".//{{{_LEG_NS}}}UnorderedList")
    assert ul is not None
    nodes = _parse_definition_unordered_list(ul, "")
    # Each top-level definition item becomes a parent paragraph node.
    assert len(nodes) == 3
    assert all(node.kind == IRNodeKind.PARAGRAPH for node in nodes)
    assert all(node.label is None for node in nodes)
    # Nested alpha items are attached as children; labels are preserved, but
    # they are no longer direct siblings at subsection level, so no duplicates.
    wrapped = [n for n in nodes if n.children]
    assert len(wrapped) == 2
    assert [c.label for c in wrapped[0].children] == ["a", "b", "c", "d"]
    assert [c.label for c in wrapped[1].children] == ["a", "b"]
    assert all(
        c.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved"
        for w in wrapped
        for c in w.children
    )
    assert all(c.kind == IRNodeKind.PARAGRAPH for w in wrapped for c in w.children)


def test_subsection_local_text_excludes_definition_term_intros() -> None:
    """The subsection's own ``text`` is just the lead-in, not the term intros.

    The definition ``UnorderedList`` is homed into child paragraphs; if
    ``_local_structural_text`` also recursed into it the term intros would be
    double-counted (parent ``text`` + child ``text``), skewing grounding.
    """
    subsection = _subsection_with_multi_definition_list()
    text = _local_structural_text(subsection)
    assert text == "In this section—"
    # None of the defined-term intros leak into the parent subsection text.
    for intro in ('"closing year"', '"general insurer"', '"period of account"'):
        assert intro not in text


def test_single_nested_definition_subsection_local_text_keeps_term_intro() -> None:
    """The single-nested flat path leaves intros only in the parent text.

    Here the nested ordered list's items are the only homed children, so the
    defined-term intro is NOT carried by any child. Dropping it from the parent
    text too would silently lose it; instead it must stay in the subsection text
    (no double-count because no child carries it).
    """
    xml = f"""<P2 xmlns="{_LEG_NS}" id="section-9-2">
      <Pnumber>2</Pnumber>
      <P2para>
        <Text>In this section—</Text>
        <UnorderedList Decoration="none" Class="Definition">
          <ListItem>
            <Para>
              <Text>"relevant provision" means—</Text>
              <OrderedList Type="alpha" Decoration="parens">
                <ListItem><Para><Text>section 13(2),</Text></Para></ListItem>
                <ListItem><Para><Text>section 19(2),</Text></Para></ListItem>
              </OrderedList>
            </Para>
          </ListItem>
        </UnorderedList>
      </P2para>
    </P2>"""
    subsection = ET.fromstring(xml)
    text = _local_structural_text(subsection)
    assert text == 'In this section— "relevant provision" means—'
    # The nested ordered-list items are homed into children, never duplicated
    # into the parent text.
    assert "section 13(2)" not in text
    assert "section 19(2)" not in text


def test_single_nested_definition_list_retains_flat_paragraphs() -> None:
    """A single definition item with a nested alpha list keeps legacy flat paragraphs."""
    xml = f"""<Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1>
          <Pnumber>42</Pnumber>
          <P1para>
            <P2>
              <Pnumber>2</Pnumber>
              <P2para>
                <Text>In this section-</Text>
                <UnorderedList Decoration="none" Class="Definition">
                  <ListItem>
                    <Para>
                      <Text>"relevant provision" means-</Text>
                      <OrderedList Type="alpha" Decoration="parens">
                        <ListItem><Para><Text>section 13(2),</Text></Para></ListItem>
                        <ListItem><Para><Text>section 19(2),</Text></Para></ListItem>
                      </OrderedList>
                    </Para>
                  </ListItem>
                </UnorderedList>
              </P2para>
            </P2>
          </P1para>
        </P1>
      </Body>
    </Legislation>"""
    root = ET.fromstring(xml)
    ul = root.find(f".//{{{_LEG_NS}}}UnorderedList")
    assert ul is not None
    nodes = _parse_definition_unordered_list(ul, "")
    assert [n.kind for n in nodes] == [IRNodeKind.PARAGRAPH, IRNodeKind.PARAGRAPH]
    assert [n.label for n in nodes] == ["a", "b"]
    assert all(n.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved" for n in nodes)
    # Legacy flat nodes are direct paragraph children of the subsection, with
    # no additional wrapping paragraph level.
    assert all(len(n.children) == 0 for n in nodes)
