from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.xml_helpers import _tag, _text_content


def test_tag_ignores_lxml_comment_nodes_from_iter() -> None:
    root = ET.fromstring("<Root><!-- publisher comment --><Schedule /></Root>")

    tags = tuple(_tag(el) for el in root.iter())

    assert tags == ("Root", "", "Schedule")
    assert tuple(el for el in root.iter() if _tag(el) == "Schedule")


def test_text_content_preserves_nested_inline_tail_order() -> None:
    root = ET.fromstring(
        """
        <P3>
          <Pnumber>a</Pnumber>
          <P3para>
            <Text>insert <InlineAmendment>“before <Term>term</Term> after”</InlineAmendment>;</Text>
          </P3para>
        </P3>
        """
    )

    assert _text_content(root) == "a insert “before term after” ;"
