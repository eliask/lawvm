from __future__ import annotations

from lxml import etree

from lawvm.finland.amendment_payload_lookup import _find_muutos_ir
from lawvm.finland.body_coverage import extract_body_coverage
from lawvm.finland.body_pairing import build_observed_body_inventory
from lawvm.finland.source_model import AmendmentSourceModel


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

