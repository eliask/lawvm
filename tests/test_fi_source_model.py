from __future__ import annotations

from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.amendment_payload_lookup import _find_muutos_ir
from lawvm.finland.body_coverage import extract_body_coverage
from lawvm.finland.body_pairing import build_observed_body_inventory
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState


def _tree() -> etree._Element:
    return etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <part>
                <num>V osa</num>
                <chapter>
                  <num>2 luku</num>
                  <heading>Luvun otsikko</heading>
                  <crossHeading>V osa</crossHeading>
                  <section>
                    <num>5 \xc2\xa7</num>
                    <heading>Pykalan otsikko</heading>
                    <subsection>
                      <num>1 mom.</num>
                      <content>Uusi teksti.</content>
                    </subsection>
                  </section>
                </chapter>
              </part>
            </body>
          </act>
        </akomaNtoso>
        """
    )


def _inventory_projection(inventory):
    return tuple(
        (
            unit.unit_id,
            unit.kind,
            unit.label,
            unit.chapter_label,
            unit.part_label,
        )
        for unit in inventory
    )


def _coverage_projection(units):
    return tuple(
        (
            unit.unit_id,
            unit.kind,
            unit.observed_label,
            unit.parent_label,
            unit.tags,
        )
        for unit in units
    )


def test_source_model_caches_body_inventory_and_coverage() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    inventory = model.observed_body_inventory()
    coverage = model.body_coverage_units()

    assert inventory is model.observed_body_inventory()
    assert coverage is model.body_coverage_units()
    assert _inventory_projection(inventory) == _inventory_projection(
        build_observed_body_inventory(tree)
    )
    assert _coverage_projection(coverage) == _coverage_projection(
        extract_body_coverage(tree)
    )


def test_source_model_body_scope_queries_use_observed_inventory() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    assert model.body_section_scope("5") == ("5", None)
    assert model.body_section_chapter("5") is None
    assert model.body_has_real_chapter_container("2")
    assert not model.body_has_pseudo_chapter_marker("2")


def test_source_model_detects_pseudo_chapter_markers() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <section>
                <num>6 a luku</num>
                <section><num>25 \xc2\xa7</num><content><p>Text.</p></content></section>
              </section>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    assert model.body_section_scope("25") == (None, "6a")
    assert model.body_has_pseudo_chapter_marker("6a")
    assert not model.body_has_real_chapter_container("6a")


def test_source_model_preserves_coverage_ignored_units_side_channel() -> None:
    tree = etree.fromstring(b"<akomaNtoso><act><body><section/></body></act></akomaNtoso>")
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/2")

    ignored_first = []
    ignored_second = []

    assert model.body_coverage_units(ignored_units_out=ignored_first) == ()
    assert model.body_coverage_units(ignored_units_out=ignored_second) == ()
    assert [ignored.reason for ignored in ignored_first] == ["missing_num"]
    assert [ignored.reason for ignored in ignored_second] == ["missing_num"]


def test_source_model_payload_lookup_matches_direct_xml_lookup() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/3")

    direct_ir, direct_cross_ir = _find_muutos_ir(
        tree,
        "section",
        "5",
        "2",
        "5",
    )
    model_ir, model_cross_ir = model.find_payload_ir("section", "5", "2", "5")

    assert model.find_payload_ir("section", "5", "2", "5") is model.find_payload_ir(
        "section",
        "5",
        "2",
        "5",
    )
    assert model_ir == direct_ir
    assert model_cross_ir == direct_cross_ir


def test_source_model_precreates_source_body_chapters() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>7 luku</num>
                <section><num>45 \xc2\xa7</num><content><p>Text.</p></content></section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/4")

    result = model.pre_create_amendment_chapters(state, "2000/4")

    assert result is not None
    assert result.created_refs[0].chapter_label == "7"
    assert result.state.find("chapter", "7") is not None


def test_source_model_precreate_chapters_returns_none_without_body() -> None:
    tree = etree.fromstring(b"<akomaNtoso><act/></akomaNtoso>")
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/5")

    assert model.pre_create_amendment_chapters(state, "2000/5") is None


def test_source_model_preamble_text_and_content_authorization() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <preamble><p>lis\xc3\xa4t\xc3\xa4\xc3\xa4n lakiin uusi 4 \xc2\xa7</p></preamble>
            <body/>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/6")

    assert "lisätään" in model.preamble_text()
    assert model.has_uncovered_recovery_content_ops([])


def test_source_model_builds_uncovered_recovery_context() -> None:
    tree = etree.fromstring(b"<akomaNtoso><act><body/></act></akomaNtoso>")
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/7")
    op = AmendmentOp(
        op_id="insert_4",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="4",
    )

    context = model.build_uncovered_recovery_context(
        ops=[op],
        new_chapter_labels={"2"},
    )

    assert "2" in context.owned_chapter_labels
