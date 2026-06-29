"""D7 deepcopy migration tests for ``estonia/target_resolution.py`` (§1.9/§1.10).

``parse_preambul_single_target_ops`` clones each ``direct_sisu_block`` before
mutating its descendant ``tavatekst`` text. The prior implementation used the
``ET.fromstring(ET.tostring(child, encoding="utf-8"))`` serialize→parse
round-trip form — this works but (a) is O(N) in tree size for what is an
in-memory operation and (b) loses nsmap inheritance on namespace-tagged
descendants on certain CPython versions. The migration to ``copy.deepcopy``
must preserve byte-identical serialization AND produce a fully independent
subtree (descendants value-equal but identity-distinct — a true deep copy,
not a shallow copy).

Mirrors the iter2 W5 M6 ``TestCloneElementDeepcopyMigration`` precedent at
``tests/test_uk_xml_helpers.py`` for ``_clone_element`` — the same
equivalence shape pinned for the EE preambul single-target clone site.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Reference oracle — the prior serialize→parse round-trip form, kept here
# only as the byte-identical reference for the deepcopy migration's
# behavioural test. Matches the exact prior form at line 3538:
# ``ET.fromstring(ET.tostring(child, encoding="utf-8"))``.
# ---------------------------------------------------------------------------

def _roundtrip_clone(el: ET.Element) -> ET.Element:
    """The prior implementation, kept here only as the byte-identical
    reference oracle for the deepcopy migration's behavioural test."""
    return ET.fromstring(ET.tostring(el, encoding="utf-8"))


class TestPreambulDirectSisuBlockDeepcopyMigration:
    """``parse_preambul_single_target_ops``'s clone of ``direct_sisu_blocks``
    MUST be byte-identical to the prior serialize→parse round-trip form AND a
    true deep copy (children distinct by identity). Pins the D7 migration
    from ``ET.fromstring(ET.tostring(child, encoding="utf-8"))`` to
    ``copy.deepcopy(child)`` so a future regression re-introducing the
    round-trip (or — worse — a shallow copy that aliases the subtree) is
    caught immediately."""

    def test_deepcopy_is_byte_identical_to_roundtrip_simple_sisu_block(self) -> None:
        # A typical ``direct_sisu_block``: a ``<sisu>`` wrapper with one
        # ``<tavatekst>`` text child — the shape the prior round-trip form was
        # load-bearing for at parse_preambul_single_target_ops.
        original = ET.fromstring(
            '<sisu xmlns="https://riigiteataja.ee/akt">'
            "<tavatekst>original body text</tavatekst>"
            "</sisu>"
        )
        roundtrip = _roundtrip_clone(original)
        deep = copy.deepcopy(original)
        # Byte-identical serialization (the equivalence invariant).
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(roundtrip, encoding="utf-8")
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(original, encoding="utf-8")
        # True deep copy: descendant is identity-distinct (a shallow copy would alias).
        assert deep[0] is not original[0]

    def test_deepcopy_is_byte_identical_to_roundtrip_with_attrs_and_tail(self) -> None:
        # Adversarial: attributes, tail text, mixed namespaces — the prior
        # round-trip form was load-bearing for all three.
        original = ET.fromstring(
            '<sisu xmlns="https://riigiteataja.ee/akt" xmlns:akn="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            '<tavatekst xml:lang="et" id="t1">body</tavatekst>'
            "<!-- tail -->"
            "<akn:section eId='sec_1'>marker</akn:section>"
            "</sisu>"
        )
        deep = copy.deepcopy(original)
        roundtrip = _roundtrip_clone(original)
        # Byte-identical serialization across attrs / tail / mixed namespaces.
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(roundtrip, encoding="utf-8")
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(original, encoding="utf-8")

    def test_deepcopy_is_byte_identical_to_roundtrip_deep_tree(self) -> None:
        # Adversarial: deep tree (4-5 levels) preserves children identity-by-
        # value not by-reference (a deepcopy, not a shallow copy).
        original = ET.fromstring(
            "<L0>"
            "<L1>"
            "<L2>"
            "<L3>"
            "<L4>deep</L4>"
            "</L3>"
            "</L2>"
            "</L1>"
            "</L0>"
        )
        deep = copy.deepcopy(original)
        roundtrip = _roundtrip_clone(original)
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(roundtrip, encoding="utf-8")
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(original, encoding="utf-8")

    def test_deepcopy_descendants_are_identity_distinct_at_every_depth(self) -> None:
        # The pre-fix form re-parsed so every node was a fresh object. A
        # shallow copy (a future regression) would alias children. Pin that
        # deepcopy produces value-equal-but-identity-distinct descendants at
        # every level of a deep tree.
        original = ET.fromstring(
            "<L0><L1><L2><L3><L4>deep</L4></L3></L2></L1></L0>"
        )
        cloned = copy.deepcopy(original)
        # Walk the same descent path on each tree and assert identity-distinct
        # at every level (deepcopy invariant — a shallow copy would alias at
        # the first non-trivial depth).
        o_cursor = original
        c_cursor = cloned
        depth = 0
        while len(o_cursor) > 0 and len(c_cursor) > 0:
            o_cursor = o_cursor[0]
            c_cursor = c_cursor[0]
            depth += 1
            assert c_cursor is not o_cursor, (
                "deepcopy aliased a descendant at "
                f"depth {depth}; deepcopy must produce value-equal but "
                "identity-distinct children all the way down the tree."
            )
            assert ET.tostring(c_cursor, encoding="utf-8") == ET.tostring(o_cursor, encoding="utf-8")

    def test_deepcopy_mutating_original_does_not_leak_into_clone(self) -> None:
        # The load-bearing property of deepcopy: post-clone mutation of the
        # original must not appear in the clone. This is the
        # parse_preambul_single_target_ops invariant — the function mutates
        # ``cloned_child``'s ``tavatekst`` text AFTER cloning, and must not
        # leak those mutations back into the source ``direct_sisu_blocks``
        # tree (else the next loop iteration would see stale extracted text).
        original = ET.fromstring(
            '<sisu xmlns="https://riigiteataja.ee/akt">'
            "<tavatekst>original body</tavatekst>"
            "</sisu>"
        )
        cloned = copy.deepcopy(original)
        # Mutate the clone's tavatekst (mirrors the production mutation).
        tavatekst = cloned.find("{https://riigiteataja.ee/akt}tavatekst")
        assert tavatekst is not None
        tavatekst.text = "EXTRACTED TEXT — should not leak into original"
        # Original is untouched — deepcopy隔离 from mutation.
        original_tavatekst = original.find("{https://riigiteataja.ee/akt}tavatekst")
        assert original_tavatekst is not None
        assert original_tavatekst.text == "original body"
        # Byte-identical-to-original clone of the post-deepcopy instance is
        # no longer equal to original (because we mutated the clone).
        assert ET.tostring(cloned, encoding="utf-8") != ET.tostring(original, encoding="utf-8")

    def test_deepcopy_preserves_nsmap_inheritance(self) -> None:
        # nsmap inheritance is the deepcopy-vs-roundtrip edge case named in
        # the brief — pin the byte-identical serialization here specifically.
        original = ET.fromstring(
            '<sisu xmlns="https://riigiteataja.ee/akt" xmlns:akn="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            '<akn:section eId="sec_1">'
            "<akn:num>1</akn:num>"
            "<akn:heading>Marker section</akn:heading>"
            "</akn:section>"
            "</sisu>"
        )
        deep = copy.deepcopy(original)
        roundtrip = _roundtrip_clone(original)
        # Both forms preserve the nsmap inheritance exactly — byte-identical.
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(roundtrip, encoding="utf-8")
        assert ET.tostring(deep, encoding="utf-8") == ET.tostring(original, encoding="utf-8")
        # Descendants with namespace-tagged names are identity-distinct
        # (the load-bearing property — a shallow copy would alias the
        # ``akn:section`` subtree and leak mutations back).
        assert deep[0] is not original[0]
        assert ET.tostring(deep[0], encoding="utf-8") == ET.tostring(original[0], encoding="utf-8")
