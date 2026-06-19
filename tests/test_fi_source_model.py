from __future__ import annotations

import datetime as dt

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


def test_source_model_detects_eid_free_body_sections() -> None:
    no_eid = AmendmentSourceModel.from_tree(
        etree.fromstring(
            b"<akomaNtoso><act><body><section><num>1 \xc2\xa7</num></section></body></act></akomaNtoso>"
        )
    )
    with_eid = AmendmentSourceModel.from_tree(
        etree.fromstring(
            b'<akomaNtoso><act><body><section eId="sec_1"><num>1 \xc2\xa7</num></section></body></act></akomaNtoso>'
        )
    )
    no_sections = AmendmentSourceModel.from_tree(
        etree.fromstring(b"<akomaNtoso><act><body/></act></akomaNtoso>")
    )

    assert no_eid.has_eid_free_body_sections()
    assert not with_eid.has_eid_free_body_sections()
    assert not no_sections.has_eid_free_body_sections()


def test_source_model_body_scope_queries_use_observed_inventory() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    assert model.body_section_scope("5") == ("5", None)
    assert model.body_section_chapter("5") is None
    assert model.body_has_section("5")
    assert model.body_has_section("5", target_part="5")
    assert not model.body_has_section("5", target_chapter="2")
    assert model.first_body_section_chapter("5") is None
    assert model.body_has_real_chapter_container("2")
    assert not model.body_has_pseudo_chapter_marker("2")


def test_source_model_body_lookup_returns_typed_verdicts() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter><num>1 luku</num><section><num>5 \xc2\xa7</num></section></chapter>
              <chapter><num>2 luku</num><section><num>5 \xc2\xa7</num></section></chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    ambiguous = model.body_section_lookup("5")
    assert ambiguous.status == "ambiguous"
    assert ambiguous.unique_unit is None
    assert tuple(unit.chapter_label for unit in ambiguous.candidates) == ("1", "2")
    assert model.first_body_section_chapter("5") == "1"

    unique = model.body_section_lookup("5", target_chapter="2")
    assert unique.status == "unique"
    assert unique.unique_unit is not None
    assert unique.unique_unit.chapter_label == "2"

    missing = model.body_section_lookup("6")
    assert missing.status == "missing"
    assert missing.candidates == ()


def test_source_model_scoped_body_chapter_requires_unique_inventory_match() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <part><num>1 osa</num><chapter><num>2 luku</num><section><num>5 \xc2\xa7</num></section></chapter></part>
              <part><num>2 osa</num><chapter><num>2 luku</num><section><num>5 \xc2\xa7</num></section></chapter></part>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    assert (
        model.source_body_chapter_for_scoped_section_target(
            target_norm="5",
            target_chapter="2",
            target_part="1",
        )
        == "2"
    )
    assert (
        model.source_body_chapter_for_scoped_section_target(
            target_norm="5",
            target_chapter="2",
            target_part=None,
        )
        is None
    )


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


def test_source_model_exposes_metadata_surfaces() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <meta>
              <identification>
                <FRBRWork>
                  <FRBRdate name="dateIssued" date="2020-01-02"/>
                </FRBRWork>
              </identification>
              <lifecycle>
                <eventRef>
                  <dateEntryIntoForce date="2020-03-04"/>
                </eventRef>
              </lifecycle>
            </meta>
            <preface><longTitle><docTitle>Testilaki</docTitle></longTitle></preface>
            <body/>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2020/1")

    assert model.title() == "Testilaki"
    assert model.issue_date() == dt.date(2020, 1, 2)
    assert model.effective_date() == dt.date(2020, 3, 4)
    assert model.effective_date_with_step() == (dt.date(2020, 3, 4), "metadata")
    assert model.expiry_date() is None


def test_source_model_exposes_commencement_expiry_override() -> None:
    tree = etree.fromstring(
        """
        <akomaNtoso>
          <act>
            <body>
              <hcontainer name="entryIntoForce">
                <content>
                  <p>muutetaan sosiaalihuoltolain väliaikaisesta muuttamisesta annetun lain
                  (1428/2004) voimaantulosäännös, sellaisena kuin se on laissa 1105/2008,
                  seuraavasti: Tämä laki tulee voimaan 1 päivänä tammikuuta 2005 ja on
                  voimassa 31 päivään joulukuuta 2014.</p>
                </content>
              </hcontainer>
            </body>
          </act>
        </akomaNtoso>
        """.encode()
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2010/1314")

    override = model.commencement_expiry_override("2010/1314")

    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2004/1428"
    assert labels is None
    assert expiry == dt.date(2014, 12, 31)


def test_source_model_exposes_section_commencement_overrides() -> None:
    tree = etree.fromstring(
        (
            "<doc>Tämä laki tulee voimaan 1 päivänä syyskuuta 2023. "
            "Lain 51 a ja 51 b § tulevat kuitenkin voimaan 1 päivänä marraskuuta 2024.</doc>"
        ).encode()
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2023/116")

    override = model.section_commencement_effective_override("2023/116")

    assert override is not None
    target_mid, chapter_section_map, effective = override
    assert target_mid == "2023/116"
    assert chapter_section_map == {None: {"51a", "51b"}}
    assert effective == dt.date(2024, 11, 1)


def test_source_model_exposes_subsection_commencement_overrides() -> None:
    tree = etree.fromstring(
        (
            "<doc>Tämä laki tulee voimaan 1 päivänä tammikuuta 2023. "
            "Lain 3 a §:n 1 momentti, 14 §:n 1 momentti sekä 14 a ja 15 b § "
            "tulevat kuitenkin voimaan vasta 1 päivänä tammikuuta 2028.</doc>"
        ).encode()
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2022/876")

    override = model.section_subsection_commencement_effective_override("2022/876")

    assert override is not None
    target_mid, addresses, effective = override
    assert target_mid == "2022/876"
    assert {str(address) for address in addresses} == {
        "section:3a/subsection:1",
        "section:14/subsection:1",
    }
    assert effective == dt.date(2028, 1, 1)


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
