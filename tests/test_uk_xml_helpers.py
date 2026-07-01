from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.xml_helpers import _clone_element, _tag, _text_content


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


def test_text_content_leaf_fast_path_ignores_own_tail() -> None:
    root = ET.fromstring("<Root><Text>  alpha\t beta\n</Text>tail</Root>")
    leaf = root[0]

    assert _text_content(leaf) == "alpha beta"
    assert _text_content(root) == "alpha beta tail"


# ---------------------------------------------------------------------------
# iter2 W5 M6 — _clone_element uses copy.deepcopy instead of the prior
# serialize→parse round-trip (``ET.fromstring(ET.tostring(...))``). The new
# form preserves tag / text / tail / attrib / children / nsmap exactly;
# these tests pin that byte-identical preservation + deepcopy identity so a
# future regression that re-introduces the round-trip (or — worse — uses a
# shallow copy that aliases the subtree) is caught immediately.
# ---------------------------------------------------------------------------


def _roundtrip_clone(el: ET._Element) -> ET._Element:
    """The prior implementation, kept here only as the byte-identical
    reference oracle for the deepcopy migration's behavioural test."""
    return ET.fromstring(ET.tostring(el, encoding="unicode"))


class TestCloneElementDeepcopyMigration:
    """``_clone_element`` MUST be byte-identical to the prior serialize→parse
    round-trip form AND a true deep copy (children distinct by identity)."""

    def test_clone_is_byte_identical_to_roundtrip_simple(self) -> None:
        original = ET.fromstring("<P1><Pnumber>1</Pnumber><P1para>text</P1para></P1>")
        cloned = _clone_element(original)

        # Byte-identical serialization: tag, text, attrib, structure preserved.
        assert ET.tostring(cloned) == ET.tostring(_roundtrip_clone(original))
        assert ET.tostring(cloned) == ET.tostring(original)

    def test_clone_is_byte_identical_to_roundtrip_with_attrs_and_tail(self) -> None:
        # Adversarial: attributes, tail text, mixed namespaces — the prior
        # round-trip form was load-bearing for all three.
        original = ET.fromstring(
            '<Root xmlns:uk="http://example/uk" uk:lang="en" id="r1">'
            "<Child>before<!--c--><Inner attr=\"v\"/>after</Child>"
            "<Sibling tsibling=\"x\"/>tail-of-sibling<Final/></Root>"
        )
        cloned = _clone_element(original)
        assert ET.tostring(cloned) == ET.tostring(_roundtrip_clone(original))
        assert ET.tostring(cloned) == ET.tostring(original)

    def test_clone_is_byte_identical_to_roundtrip_deep_tree(self) -> None:
        # Adversarial: deep tree (4-5 levels) preserves children identity-by-
        # value not by-reference (a deepcopy, not a shallow copy).
        original = ET.fromstring(
            "<L0>"
            "<L1>"
            "<L2>"
            "<L3>"
            "<L4>deep</L4>"
            "<L4 sib=\"1\">sibling</L4>"
            "</L3>"
            "</L2>"
            "<L2 alt=\"yes\"/>"
            "</L1>"
            "</L0>"
        )
        cloned = _clone_element(original)
        # Byte-identical serialization to the prior round-trip form.
        assert ET.tostring(cloned) == ET.tostring(_roundtrip_clone(original))
        assert ET.tostring(cloned) == ET.tostring(original)

    def test_clone_children_are_distinct_by_identity(self) -> None:
        # The pre-fix form re-parsed so every node was a fresh object. A
        # shallow copy (a future regression) would alias children. Pin that
        # deepcopy produces value-equal-but-identity-distinct descendants at
        # every level of a deep tree.
        original = ET.fromstring(
            "<L0><L1><L2><L3><L4>deep</L4></L3></L2></L1></L0>"
        )
        cloned = _clone_element(original)
        # Root is value-equal but identity-distinct.
        assert cloned is not original, (
            "_clone_element must return a fresh element, not alias the input."
        )
        # Walk the same descent path on each tree and assert identity-distinct
        # at every level (deepcopy invariant — a shallow copy would alias at
        # the first non-trivial depth).
        o_cursor = original
        c_cursor = cloned
        for depth in range(4):
            o_cursor = o_cursor[0]
            c_cursor = c_cursor[0]
            assert c_cursor is not o_cursor, (
                f"_clone_element aliased a descendant at depth {depth + 1}; "
                "deepcopy must produce value-equal but identity-distinct "
                "children all the way down the tree."
            )
            assert ET.tostring(c_cursor) == ET.tostring(o_cursor)

    def test_clone_mutating_original_does_not_leak_into_clone(self) -> None:
        # The load-bearing property of deepcopy: post-clone mutation of the
        # original must not appear in the clone. A shallow copy or the prior
        # round-trip would both pass this, but so does deepcopy — the invariant
        # is that the clone is a fully independent tree.
        original = ET.fromstring("<Root><Child>original</Child></Root>")
        cloned = _clone_element(original)
        original[0].text = "mutated"
        # The clone must carry the original text — no shared subtree aliasing.
        assert cloned[0].text == "original", (
            "_clone_element produced a clone whose child text mirrors "
            "post-clone mutation of the original — subtree aliasing leak."
        )

    def test_clone_preserves_nsmap_inheritance(self) -> None:
        # nsmap inheritance is the deepcopy-vs-roundtrip edge case named in
        # the brief — pin the byte-identical serialization here specifically.
        original = ET.fromstring(
            '<Root xmlns:akn="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            '<akn:section eId="sec_1">'
            "<akn:num>1</akn:num>"
            "<akn:heading>Heading</akn:heading>"
            "</akn:section>"
            "</Root>"
        )
        cloned = _clone_element(original)
        assert ET.tostring(cloned) == ET.tostring(_roundtrip_clone(original))
        assert ET.tostring(cloned) == ET.tostring(original)
        # nsmap carried through.
        assert cloned.nsmap == original.nsmap
        assert cloned[0].nsmap == original[0].nsmap
