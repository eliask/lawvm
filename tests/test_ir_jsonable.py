from __future__ import annotations

import pickle
from typing import Any, cast

import pytest
from lxml import etree

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.ir import FrozenDict, IRNode, IRStatute
from lawvm.core.ir_helpers import ir_statute_from_dict
from lawvm.contracts import to_wire_jsonable
from lawvm.xml_ingest import xml_body_to_ir
from lawvm.core.semantic_types import IRNodeKind


def test_irnode_to_jsonable_dict_accepts_nested_jsonable_values() -> None:
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        attrs={
            "flags": ["a", "b"],
            "meta": {"count": 2, "active": True},
            "tags": frozenset({"x", "y"}),
        },
        children=(IRNode(kind=IRNodeKind.CONTENT, text="hello"),),
    )

    got = node.to_jsonable_dict()

    assert got["attrs"]["meta"]["count"] == 2
    assert got["children"][0]["text"] == "hello"
    assert sorted(got["attrs"]["tags"]) == ["x", "y"]


def test_irnode_freezes_nested_attrs_recursively() -> None:
    nested_list = ["a", {"inner": ["b", "c"]}]
    nested_set = {"x", "y"}
    nested_dict = {"items": nested_list, "tags": nested_set}
    node = IRNode(kind=IRNodeKind.SECTION, label="1", attrs={"meta": nested_dict})

    meta = node.attrs["meta"]
    assert isinstance(meta, FrozenDict)
    assert isinstance(meta["items"], tuple)
    assert isinstance(meta["items"][1], FrozenDict)
    assert isinstance(meta["items"][1]["inner"], tuple)
    assert isinstance(meta["tags"], frozenset)
    with pytest.raises(TypeError):
        meta["items"] += ("d",)  # type: ignore[operator]
    with pytest.raises(TypeError):
        meta["items"][1]["inner"] += ("d",)  # type: ignore[operator]
    with pytest.raises(AttributeError):
        meta["tags"].add("z")  # type: ignore[attr-defined]

    nested_list.append("mutated")
    nested_set.add("z")
    assert node.attrs["meta"]["items"] == ("a", FrozenDict({"inner": ("b", "c")}))
    assert node.attrs["meta"]["tags"] == frozenset({"x", "y"})


def test_freeze_mapping_public_helper_freezes_nested_values() -> None:
    source = {"items": [{"inner": ["a"]}]}

    frozen = freeze_mapping(source)
    source["items"][0]["inner"].append("mutated")

    assert isinstance(frozen, FrozenDict)
    assert frozen["items"] == (FrozenDict({"inner": ("a",)}),)


def test_frozen_dict_in_place_union_does_not_mutate() -> None:
    frozen = FrozenDict({"items": ("a",)})

    with pytest.raises(TypeError, match="immutable"):
        frozen |= {"extra": "blocked"}

    assert frozen == {"items": ("a",)}


def test_frozen_dict_round_trips_through_pickle_without_losing_immutability() -> None:
    original = FrozenDict({"items": (FrozenDict({"inner": ("a", "b")}),)})

    restored = pickle.loads(pickle.dumps(original))

    assert isinstance(restored, FrozenDict)
    assert restored == original
    assert isinstance(restored["items"][0], FrozenDict)
    with pytest.raises(TypeError):
        restored["items"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        restored["items"][0]["inner"] = ()  # type: ignore[index]


def test_irnode_to_jsonable_dict_rejects_non_jsonable_values() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, label="1", attrs={"bad": object()})

    with pytest.raises(TypeError, match="not JSON-serializable"):
        node.to_jsonable_dict()


def test_ir_statute_to_jsonable_dict_rejects_non_string_metadata_keys() -> None:
    statute = IRStatute(
        statute_id="1/2000",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY),
        metadata=cast(Any, {1: "bad"}),
    )

    with pytest.raises(TypeError, match="Non-string key"):
        statute.to_jsonable_dict()


def test_ir_statute_freezes_nested_metadata_recursively() -> None:
    metadata = {
        "layers": [{"kind": "outer", "items": [1, 2]}],
        "flags": {"alpha", "beta"},
    }
    statute = IRStatute(
        statute_id="1/2000",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY),
        metadata=metadata,
    )

    layers = statute.metadata["layers"]
    assert isinstance(layers, tuple)
    assert isinstance(layers[0], FrozenDict)
    assert layers[0]["items"] == (1, 2)
    assert isinstance(statute.metadata["flags"], frozenset)

    metadata["layers"].append({"kind": "mutated", "items": []})
    metadata["flags"].add("gamma")
    assert statute.metadata["layers"] == (FrozenDict({"kind": "outer", "items": (1, 2)}),)
    assert statute.metadata["flags"] == frozenset({"alpha", "beta"})


def test_ir_statute_supplements_are_authoritative_and_serialized() -> None:
    schedule = IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule text")
    statute = IRStatute(
        statute_id="1/2000",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=[schedule],
    )

    assert statute.supplements == (schedule,)

    payload = statute.to_jsonable_dict()
    assert payload["supplements"][0]["label"] == "1"


def test_to_wire_jsonable_prefers_to_jsonable_dict_for_dict_like_values() -> None:
    class DualProjectionDict(dict[str, Any]):
        def to_dict(self) -> dict[str, Any]:
            return {"kind": "alternate", "value": self["value"]}

        def to_jsonable_dict(self) -> dict[str, Any]:
            return {"kind": "preferred", "value": self["value"]}

    value = DualProjectionDict(value={"nested": IRNode(kind=IRNodeKind.CONTENT, text="hello")})

    got = to_wire_jsonable(value)

    assert got == {"kind": "preferred", "value": {"nested": {"kind": "content", "label": None, "text": "hello", "attrs": {}, "children": []}}}


def test_ir_statute_from_dict_rejects_schedules_payload() -> None:
    with pytest.raises(ValueError, match="rejects schedules payload"):
        ir_statute_from_dict(
            {
                "statute_id": "1/2000",
                "title": "Deprecated schedules payload",
                "body": {"kind": "body"},
                "schedules": [{"kind": "schedule", "label": "1", "text": "Legacy schedule"}],
            }
        )


def test_xml_body_to_ir_preserves_top_level_supplements() -> None:
    tree = etree.fromstring(
        """
        <akomaNtoso>
          <docNumber>1/2000</docNumber>
          <docTitle>Body Only</docTitle>
          <body>
            <section><num>1</num><content>Body text</content></section>
          </body>
          <schedule>
            <num>1</num><content>Schedule text</content>
          </schedule>
        </akomaNtoso>
        """
    )

    statute = xml_body_to_ir(tree)

    assert statute.title == "Body Only"
    assert statute.body.kind == IRNodeKind.BODY
    assert [child.kind for child in statute.supplements] == [IRNodeKind.SCHEDULE]
    assert statute.supplements[0].label == "1"
    assert [child.kind for child in statute.body.children] == [IRNodeKind.SECTION]


def test_xml_body_to_ir_is_body_only_ingress() -> None:
    tree = etree.fromstring(
        """
        <akomaNtoso>
          <docNumber>1/2000</docNumber>
          <docTitle>Alias</docTitle>
          <body><section><num>1</num><content>Body text</content></section></body>
        </akomaNtoso>
        """
    )

    statute = xml_body_to_ir(tree)

    assert statute.statute_id == "1/2000"
    assert statute.body.kind == IRNodeKind.BODY


def test_xml_body_to_ir_absorbs_orphaned_subsections_into_preceding_section() -> None:
    """Orphaned <subsection> siblings of a <section> get reparented into that section.

    Some finlex source XMLs have a malformed structure where a <section> closes
    immediately after its <num> and the <subsection> content elements appear as
    sibling elements at the container level.  xml_body_to_ir must absorb them.
    """
    tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <docNumber>1124/2015</docNumber>
          <docTitle>Test statute</docTitle>
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section eId="sec_1">
                <num>1</num>
                <subsection eId="sec_1__subsec_1">
                  <content><p>Normal nested subsection.</p></content>
                </subsection>
              </section>
              <section eId="sec_3">
                <num>3</num>
              </section>
              <subsection eId="subsec_1">
                <content><p>Entry into force paragraph 1.</p></content>
              </subsection>
              <subsection eId="subsec_2">
                <content><p>Entry into force paragraph 2.</p></content>
              </subsection>
            </hcontainer>
          </body>
        </akomaNtoso>
        """.encode()
    )
    statute = xml_body_to_ir(tree)

    hc = statute.body.children[0]
    sections = [c for c in hc.children if c.kind == IRNodeKind.SECTION]
    assert len(sections) == 2

    sec1, sec3 = sections
    assert sec1.label == "1"
    subsecs_1 = [c for c in sec1.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(subsecs_1) == 1, "section 1 should keep its properly nested subsection"

    assert sec3.label == "3"
    subsecs_3 = [c for c in sec3.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(subsecs_3) == 2, "orphaned subsections must be absorbed into section 3"
    assert subsecs_3[0].label == "1"
    assert subsecs_3[1].label == "2"
    from lawvm.core.ir_helpers import irnode_to_text
    assert "Entry into force paragraph 1" in irnode_to_text(subsecs_3[0])
    assert "Entry into force paragraph 2" in irnode_to_text(subsecs_3[1])
