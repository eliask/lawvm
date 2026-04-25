"""Tests for server-side structure normalization (normalize_structure.py).

These tests verify that the Python normalize_structure_for_viewer function
produces the same output as the client-side JS normalizeStructureNode,
ensuring viewer rendering is identical after the server-side migration.
"""

from __future__ import annotations

from lawvm.semantic.normalize_structure import normalize_structure_for_viewer


def test_none_input_returns_none() -> None:
    assert normalize_structure_for_viewer(None) is None


def test_empty_dict_returns_none() -> None:
    assert normalize_structure_for_viewer({}) is None


def test_non_semantic_kind_returns_none() -> None:
    assert normalize_structure_for_viewer({"kind": "content"}) is None


def test_already_normalized_passthrough() -> None:
    node = {"kind": "section", "label": "10", "_normalized": True}
    assert normalize_structure_for_viewer(node) is node


def test_section_kind_preserved() -> None:
    result = normalize_structure_for_viewer({"kind": "section", "label": "10"})
    assert result is not None
    assert result["kind"] == "section"
    assert result["label"] == "10"
    assert result["_normalized"] is True


def test_paragraph_canonicalized_to_item() -> None:
    result = normalize_structure_for_viewer({"kind": "paragraph", "label": "1"})
    assert result is not None
    assert result["kind"] == "item"


def test_subparagraph_canonicalized_to_subitem() -> None:
    result = normalize_structure_for_viewer({"kind": "subparagraph", "label": "a"})
    assert result is not None
    assert result["kind"] == "subitem"


def test_section_label_strips_paragraph_sign() -> None:
    result = normalize_structure_for_viewer({"kind": "section", "label": "10 §"})
    assert result is not None
    assert result["label"] == "10"


def test_subsection_label_extracts_number() -> None:
    result = normalize_structure_for_viewer({"kind": "subsection", "label": "3 mom."})
    assert result is not None
    assert result["label"] == "3"


def test_item_label_strips_kohta_suffix() -> None:
    result = normalize_structure_for_viewer({"kind": "item", "label": "3 a kohta"})
    assert result is not None
    assert result["label"] == "3a"


def test_subitem_label_strips_alakohta_suffix() -> None:
    result = normalize_structure_for_viewer({"kind": "subitem", "label": "a alakohta"})
    assert result is not None
    assert result["label"] == "a"


def test_num_child_used_as_label_fallback() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "children": [
                {"kind": "num", "text": "10 §"},
                {"kind": "subsection", "label": "1", "text": "Text."},
            ],
        }
    )
    assert result is not None
    assert result["label"] == "10"


def test_heading_child_moved_to_facets() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "5",
            "children": [
                {"kind": "heading", "text": "Otsikko"},
                {"kind": "subsection", "label": "1", "text": "Text."},
            ],
        }
    )
    assert result is not None
    assert result.get("facets", {}).get("heading") == {"text": "Otsikko"}
    # heading should not be in structural children
    child_kinds = [c["kind"] for c in result.get("children", [])]
    assert "heading" not in child_kinds


def test_intro_child_moved_to_facets() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "5",
            "children": [
                {"kind": "intro", "text": "Johdanto"},
                {"kind": "subsection", "label": "1", "text": "Text."},
            ],
        }
    )
    assert result is not None
    assert result.get("facets", {}).get("intro") == {"text": "Johdanto"}


def test_existing_facets_dict_preserved() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "5",
            "facets": {
                "heading": {"text": "Otsikko"},
                "intro": {"text": "Johdanto"},
            },
        }
    )
    assert result is not None
    assert result["facets"]["heading"] == {"text": "Otsikko"}
    assert result["facets"]["intro"] == {"text": "Johdanto"}


def test_wording_facet_extracted_as_text() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "subsection",
            "label": "1",
            "facets": {
                "wording": {"text": "Sanamuoto tästä."},
            },
        }
    )
    assert result is not None
    assert result["text"] == "Sanamuoto tästä."


def test_text_from_content_p_children() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "subsection",
            "label": "1",
            "children": [
                {
                    "kind": "content",
                    "children": [
                        {"kind": "p", "text": "Ensimmäinen virke."},
                        {"kind": "p", "text": "Toinen virke."},
                    ],
                },
            ],
        }
    )
    assert result is not None
    assert result.get("text") == "Ensimmäinen virke. Toinen virke."


def test_text_from_content_without_p() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "subsection",
            "label": "1",
            "children": [
                {"kind": "content", "text": "Suora teksti."},
            ],
        }
    )
    assert result is not None
    assert result.get("text") == "Suora teksti."


def test_ordinal_assignment_for_unlabeled_subsections() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "5",
            "children": [
                {"kind": "subsection", "text": "Ensimmäinen."},
                {"kind": "subsection", "text": "Toinen."},
            ],
        }
    )
    assert result is not None
    children = result.get("children", [])
    assert len(children) == 2
    assert children[0]["label"] == "1"
    assert children[1]["label"] == "2"


def test_ordinal_assignment_continues_from_max_labeled() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "5",
            "children": [
                {"kind": "subsection", "label": "1", "text": "Ensimmäinen."},
                {"kind": "subsection", "label": "2", "text": "Toinen."},
                {"kind": "subsection", "text": "Kolmas."},
            ],
        }
    )
    assert result is not None
    children = result.get("children", [])
    assert len(children) == 3
    assert children[2]["label"] == "3"


def test_non_semantic_kind_wraps_as_group() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "unknown",
            "children": [
                {"kind": "section", "label": "1", "text": "Text."},
            ],
        }
    )
    assert result is not None
    assert result["kind"] == "group"
    assert len(result["children"]) == 1


def test_non_semantic_kind_with_no_children_returns_none() -> None:
    result = normalize_structure_for_viewer({"kind": "unknown"})
    assert result is None


def test_full_pipeline_output_roundtrip() -> None:
    """Test normalizing a dict that looks like SemanticStructureNode.to_dict() output."""
    pipeline_dict = {
        "kind": "section",
        "label": "10",
        "facets": {
            "heading": {"text": "Kulkuväylän käytön maksullisuus"},
        },
        "children": [
            {
                "kind": "subsection",
                "label": "1",
                "facets": {
                    "intro": {"text": "Varsinainen momenttiteksti."},
                },
                "children": [
                    {
                        "kind": "item",
                        "label": "a",
                        "text": "Kohta A.",
                        "facets": {
                            "wording": {"text": "Kohta A."},
                        },
                    }
                ],
            },
        ],
    }

    result = normalize_structure_for_viewer(pipeline_dict)
    assert result is not None
    assert result["kind"] == "section"
    assert result["label"] == "10"
    assert result["facets"]["heading"] == {"text": "Kulkuväylän käytön maksullisuus"}
    assert result["_normalized"] is True

    # Children should be recursively normalized
    sub = result["children"][0]
    assert sub["kind"] == "subsection"
    assert sub["label"] == "1"
    assert sub["facets"]["intro"] == {"text": "Varsinainen momenttiteksti."}
    assert sub["_normalized"] is True

    item = sub["children"][0]
    assert item["kind"] == "item"
    assert item["label"] == "a"
    assert item["text"] == "Kohta A."
    assert item["_normalized"] is True


def test_whitespace_normalization() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "  10  §  ",
            "facets": {
                "heading": {"text": "  Otsikko   tässä  "},
            },
        }
    )
    assert result is not None
    assert result["label"] == "10"
    assert result["facets"]["heading"] == {"text": "Otsikko tässä"}


def test_empty_text_fields_omitted() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "label": "10",
        }
    )
    assert result is not None
    assert "text" not in result
    assert "facets" not in result
    assert "children" not in result


def test_empty_label_omitted() -> None:
    result = normalize_structure_for_viewer(
        {
            "kind": "section",
            "text": "Some text.",
        }
    )
    assert result is not None
    assert "label" not in result
    assert result.get("text") == "Some text."


class TestProjectionIntegration:
    """Integration tests: run Python semantic projection, then normalize for viewer."""

    def test_ir_structure_normalizes_cleanly(self) -> None:
        from lawvm.core.ir import IRNode
        from lawvm.core.semantic_types import IRNodeKind
        from lawvm.semantic.projection import semantic_structure_from_ir

        node = IRNode(
            kind=IRNodeKind.SECTION,
            label="10",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="10 §"),
                IRNode(kind=IRNodeKind.HEADING, text="Kulkuväylän käytön maksullisuus"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="Momenttiteksti."),),
                ),
            ),
        )
        structure = semantic_structure_from_ir(node)
        assert structure is not None
        structure_dict = structure.to_dict()
        result = normalize_structure_for_viewer(structure_dict)
        assert result is not None
        assert result["kind"] == "section"
        assert result["label"] == "10"
        assert result["_normalized"] is True
        assert result["facets"]["heading"] == {"text": "Kulkuväylän käytön maksullisuus"}

    def test_oracle_structure_normalizes_cleanly(self) -> None:
        from lxml import etree
        from lawvm.semantic.projection import semantic_structure_from_oracle

        node = etree.fromstring(
            """
            <section xmlns="urn:akn">
              <num>10 §</num>
              <subsection>
                <num>1 mom.</num>
                <content><p>Ensimmäinen virke.</p></content>
              </subsection>
            </section>
            """
        )
        structure = semantic_structure_from_oracle(node)
        assert structure is not None
        structure_dict = structure.to_dict()
        result = normalize_structure_for_viewer(structure_dict)
        assert result is not None
        assert result["kind"] == "section"
        assert result["label"] == "10"
        assert result["_normalized"] is True


class TestSupportProjectionWiring:
    """Test that semantic_support_projection applies normalization."""

    def test_support_projection_produces_normalized_oracle_structure(self) -> None:
        import json
        from lawvm.semantic.contracts import semantic_support_projection

        support = {
            "semantic_contract_version": "semantic-v1",
            "oracle": {"kind": "section", "label": "10"},
            "replay": {"kind": "section", "label": "10"},
            "aligned": {
                "kind": "section",
                "label": "10",
                "match_basis": "exact_label",
                "left": {"kind": "section", "label": "10"},
                "right": {"kind": "section", "label": "10"},
            },
            "semantic_diff": {
                "kind": "identical",
                "summary": "Identtinen.",
                "structural": 0,
                "label": 0,
                "text": 0,
                "events": [],
            },
        }
        projection = semantic_support_projection(support)

        # oracle_structure and replay_structure should contain _normalized marker
        oracle = json.loads(projection["oracle_structure"])
        assert oracle["_normalized"] is True
        assert oracle["kind"] == "section"
        assert oracle["label"] == "10"

        replay = json.loads(projection["replay_structure"])
        assert replay["_normalized"] is True
        assert replay["kind"] == "section"
        assert replay["label"] == "10"
