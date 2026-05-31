from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.xml_helpers import _tag


def test_tag_ignores_lxml_comment_nodes_from_iter() -> None:
    root = ET.fromstring("<Root><!-- publisher comment --><Schedule /></Root>")

    tags = tuple(_tag(el) for el in root.iter())

    assert tags == ("Root", "", "Schedule")
    assert tuple(el for el in root.iter() if _tag(el) == "Schedule")
