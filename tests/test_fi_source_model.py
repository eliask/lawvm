from __future__ import annotations

import datetime as dt

import pytest
from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.phase_result import PhaseResult
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.amendment_payload_lookup import _find_muutos_ir
from lawvm.finland.body_coverage import BodyCoveragePayloadRef, extract_body_coverage
from lawvm.finland.body_pairing import build_observed_body_inventory
from lawvm.finland.corpus import get_corpus_store
from lawvm.finland.ops import OpType, AmendmentOp
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
    assert model.body_section_wrapper_scope("5") == ("5", "2")
    assert model.body_section_chapter("5") is None
    assert not model.body_carries_whole_section("5")
    assert model.body_carries_whole_section("5", target_part="5")
    assert model.unique_body_section_chapter("5") == "2"
    assert model.unique_body_section_chapter("5", target_part="5") == "2"
    assert model.unique_body_section_chapter("5", target_part="6") is None
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
    assert ambiguous.lookup_status == "ambiguous"
    assert ambiguous.unique_unit is None
    assert tuple(unit.chapter_label for unit in ambiguous.candidates) == ("1", "2")
    assert model.first_body_section_chapter("5") == "1"

    unique = model.body_section_lookup("5", target_chapter="2")
    assert unique.lookup_status == "unique"
    assert unique.unique_unit is not None
    assert unique.unique_unit.chapter_label == "2"

    missing = model.body_section_lookup("6")
    assert missing.lookup_status == "missing"
    assert missing.candidates == ()


def test_source_model_has_source_node_uses_typed_body_lookup() -> None:
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

    assert model.has_source_node("section", "5")
    assert model.has_source_node("section", "5", target_chapter="2")
    assert not model.has_source_node("section", "6")
    assert model.has_source_node("chapter", "1")
    assert not model.has_source_node("chapter", "3")


def test_source_model_has_source_node_preserves_single_unlabeled_section_payload() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <section><content><p>Unnumbered payload.</p></content></section>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/1")

    assert model.has_single_unlabeled_section_payload()
    assert model.has_source_node("section", "5")


def test_source_model_has_source_node_rejects_multiple_or_unusable_unlabeled_sections() -> None:
    two_unlabeled = AmendmentSourceModel.from_tree(
        etree.fromstring(
            b"""
            <akomaNtoso>
              <act>
                <body>
                  <section><content><p>One.</p></content></section>
                  <section><content><p>Two.</p></content></section>
                </body>
              </act>
            </akomaNtoso>
            """
        )
    )
    unusable_num = AmendmentSourceModel.from_tree(
        etree.fromstring(
            b"""
            <akomaNtoso>
              <act>
                <body>
                  <section><num>ei pykala</num><content><p>Text.</p></content></section>
                </body>
              </act>
            </akomaNtoso>
            """
        )
    )

    assert not two_unlabeled.has_single_unlabeled_section_payload()
    assert not two_unlabeled.has_source_node("section", "5")
    assert not unusable_num.has_single_unlabeled_section_payload()
    assert not unusable_num.has_source_node("section", "5")


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
    metadata = model.amendment_tree_metadata("2020/1")
    assert metadata is model.amendment_tree_metadata("2020/1")
    assert metadata.source_title == "Testilaki"
    assert metadata.source_issue_date == dt.date(2020, 1, 2)
    assert metadata.effective_date == dt.date(2020, 3, 4)
    assert metadata.expiry_date is None


def test_source_model_metadata_surface_caches_xml_adapter_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = etree.fromstring(b"<akomaNtoso><act><body>Teksti</body></act></akomaNtoso>")
    model = AmendmentSourceModel.from_tree(tree, source_ref="2020/2")
    calls = {
        "title": 0,
        "issue": 0,
        "effective": 0,
        "expiry": 0,
        "provision_overrides": 0,
        "section_overrides": 0,
    }

    import lawvm.finland.frontend_compile as frontend_compile
    import lawvm.finland.metadata as metadata

    def fake_title(_tree: etree._Element) -> str:
        calls["title"] += 1
        return "Cached title"

    def fake_issue(_tree: etree._Element) -> dt.date:
        calls["issue"] += 1
        return dt.date(2020, 1, 2)

    def fake_effective(_tree: etree._Element) -> tuple[dt.date, str]:
        calls["effective"] += 1
        return dt.date(2020, 3, 4), "test-step"

    def fake_expiry(_tree: etree._Element, *, raw_text: str | None = None) -> dt.date:
        assert raw_text == "Teksti"
        calls["expiry"] += 1
        return dt.date(2020, 12, 31)

    def fake_provision_overrides(
        _tree: etree._Element,
        _amendment_id: str,
        *,
        raw_text: str | None = None,
    ) -> tuple:
        assert raw_text == "Teksti"
        calls["provision_overrides"] += 1
        return ()

    def fake_section_overrides(
        _tree: etree._Element,
        _amendment_id: str,
        *,
        raw_text: str | None = None,
    ) -> tuple:
        assert raw_text == "Teksti"
        calls["section_overrides"] += 1
        return ()

    monkeypatch.setattr(frontend_compile, "_tree_title", fake_title)
    monkeypatch.setattr(metadata, "_statute_issue_date", fake_issue)
    monkeypatch.setattr(metadata, "_amendment_effective_date_with_step", fake_effective)
    monkeypatch.setattr(metadata, "_amendment_expiry_date", fake_expiry)
    monkeypatch.setattr(metadata, "_temporary_provision_expiry_overrides", fake_provision_overrides)
    monkeypatch.setattr(metadata, "_temporary_section_expiry_overrides", fake_section_overrides)

    assert model.title() == "Cached title"
    assert model.issue_date() == dt.date(2020, 1, 2)
    assert model.effective_date() == dt.date(2020, 3, 4)
    assert model.effective_date_with_step() == (dt.date(2020, 3, 4), "test-step")
    assert model.expiry_date() == dt.date(2020, 12, 31)
    assert model.metadata_surface() is model.metadata_surface()

    metadata_first = model.amendment_tree_metadata("2020/2")
    metadata_second = model.amendment_tree_metadata("2020/2")

    assert metadata_first is metadata_second
    assert metadata_first.source_title == "Cached title"
    assert calls == {
        "title": 1,
        "issue": 1,
        "effective": 1,
        "expiry": 1,
        "provision_overrides": 1,
        "section_overrides": 1,
    }


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


def test_source_model_exposes_operative_body_repeal_candidate() -> None:
    source_bytes = b"""
        <akomaNtoso>
          <act>
            <preamble><formula name="enactingClause"><p>saadetaar:</p></formula></preamble>
            <body>
              <hcontainer name="statuteTextWrapper">
                <content><p>Taten kumotaan asetuksen 9 \xc2\xa7.</p></content>
              </hcontainer>
              <hcontainer name="conclusions"><content><p>allekirjoitukset</p></content></hcontainer>
            </body>
          </act>
        </akomaNtoso>
        """
    tree = etree.fromstring(
        source_bytes
    )
    model = AmendmentSourceModel.from_tree(
        tree,
        source_ref="2000/1",
        source_bytes=source_bytes,
    )

    assert model.source_xml_bytes() is source_bytes
    assert model.operative_body_repeal_candidate() == "Taten kumotaan asetuksen 9 §."


def test_source_model_owns_frontend_normalization_xml_adapter() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/8")
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    calls: list[dict[str, object]] = []

    def fake_compile_ops(**kwargs: object) -> PhaseResult[list[AmendmentOp]]:
        calls.append(kwargs)
        return PhaseResult(output=[])

    result = model.normalize_and_compile_ops(
        compile_ops=fake_compile_ops,
        johto="muutetaan 5 §",
        master=state,
        base_ir=state.ir,
        amendment_id="2000/8",
        source_title="Testilaki",
        used_preamble_body_fallback=False,
        parent_id="1999/1",
        strict_profile=None,
        parse_result=None,
        regex_recognition_coverage_out=None,
        amendment_metadata=None,
    )

    assert result.output == []
    assert len(calls) == 1
    assert calls[0]["muutos_tree"] is tree
    assert calls[0]["amendment_id"] == "2000/8"
    assert calls[0]["parent_id"] == "1999/1"


def test_source_model_owns_op_enrichment_xml_adapter() -> None:
    tree = _tree()
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/9")
    op = AmendmentOp(
        op_id="replace_5",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="5",
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_enrich_ops(
        *args: object,
        **kwargs: object,
    ) -> list[AmendmentOp]:
        calls.append((args, kwargs))
        return [op]

    result = model.enrich_ops_from_amendment_tree(
        enrich_ops=fake_enrich_ops,
        ops=[op],
        amendment_id="2000/9",
        johto="muutetaan 5 §",
        parent_id="1999/1",
    )

    assert result == [op]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:5] == ([op], "2000/9", tree, None, "muutetaan 5 §")
    assert kwargs["parent_id"] == "1999/1"
    assert kwargs["metadata"] is model.amendment_tree_metadata("2000/9")


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
    payload_lookup = model.lookup_payload_ir("section", "5", "2", "5")
    inventory_payload_lookup = model.lookup_payload_ir("section", "5", target_part="5")

    assert model.lookup_payload_ir("section", "5", "2", "5") is model.lookup_payload_ir(
        "section",
        "5",
        "2",
        "5",
    )
    assert payload_lookup.lookup_status == "missing"
    assert payload_lookup.body_lookup_status == "missing"
    assert payload_lookup.payload_basis == "none"
    assert payload_lookup.payload_ir is None
    assert payload_lookup.cross_heading_ir is None
    assert model.find_payload_ir("section", "5", "2", "5") == (None, None)
    assert inventory_payload_lookup.lookup_status == "unique"
    assert inventory_payload_lookup.body_lookup_status == "unique"
    assert inventory_payload_lookup.payload_basis == "body_inventory"
    assert inventory_payload_lookup.payload_ir == direct_ir
    assert inventory_payload_lookup.cross_heading_ir == direct_cross_ir


def test_source_model_payload_lookup_keeps_chapter_scope_after_plain_cross_heading() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>4 luku</num>
                <heading>Sulautuminen</heading>
                <crossHeading>Sulautumisen m\xc3\xa4\xc3\xa4ritelm\xc3\xa4 ja toteuttamistavat</crossHeading>
                <section>
                  <num>60 \xc2\xa7</num>
                  <subsection><content><p>S\xc3\xa4\xc3\xa4st\xc3\xb6pankki voi sulautua.</p></content></subsection>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2007/1423")

    lookup = model.lookup_payload_ir("section", "60", target_chapter="4")

    assert [unit.unit_id for unit in lookup.body_candidates] == ["section:4/60"]
    assert lookup.lookup_status == "unique"
    assert lookup.payload_basis == "body_inventory"
    assert lookup.payload_ir is not None


def test_source_model_real_chapter_payload_lookup_uses_current_source_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>2 luku</num>
                <heading>Otsikko</heading>
                <section>
                  <num>5 \xc2\xa7</num>
                  <subsection><content><p>Chapter-owned payload.</p></content></subsection>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/12")

    import lawvm.finland.amendment_payload_lookup as payload_lookup

    def fail_full_xml_lookup(*_args: object, **_kwargs: object) -> None:
        pytest.fail("real chapter source-model payload lookup must not rescan the XML root")

    monkeypatch.setattr(payload_lookup, "_find_muutos_ir", fail_full_xml_lookup)

    lookup = model.lookup_payload_ir("chapter", "2")

    assert lookup.lookup_status == "unique"
    assert lookup.payload_basis == "body_inventory"
    assert lookup.payload_ir is not None
    assert lookup.payload_ir.kind is IRNodeKind.CHAPTER
    assert lookup.payload_ir.label == "2"
    assert "Chapter-owned payload" in irnode_to_text(lookup.payload_ir)


def test_source_model_payload_lookup_converts_only_selected_source_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <section>
                <num>1 \xc2\xa7</num>
                <subsection><content><p>first payload</p></content></subsection>
              </section>
              <section>
                <num>2 \xc2\xa7</num>
                <subsection><content><p>second payload</p></content></subsection>
              </section>
            </body>
          </act>
        </akomaNtoso>
        """
    )

    import lawvm.finland.amendment_payload_lookup as payload_lookup

    calls = 0
    real_payload_from_node = payload_lookup._payload_ir_from_muutos_node

    def counted_payload_from_node(
        muutos_sec: etree._Element,
        *,
        target_unit_kind: str,
        target_norm: str,
    ):
        nonlocal calls
        calls += 1
        return real_payload_from_node(
            muutos_sec,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
        )

    monkeypatch.setattr(
        payload_lookup,
        "_payload_ir_from_muutos_node",
        counted_payload_from_node,
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/10")

    first = model.lookup_payload_ir("section", "1")
    again = model.lookup_payload_ir("section", "1")

    assert first is again
    assert first.payload_ir is not None
    assert "first payload" in irnode_to_text(first.payload_ir)
    assert calls == 1


def test_source_model_chapter_payload_lookup_uses_logical_pseudo_chapter_segments() -> None:
    xml = get_corpus_store().read_source("1997/611")
    assert xml is not None
    model = AmendmentSourceModel.from_tree(etree.fromstring(xml), source_ref="1997/611")

    chapter_16a = model.lookup_payload_ir("chapter", "16a")
    chapter_16b = model.lookup_payload_ir("chapter", "16b")

    assert chapter_16a.lookup_status == "unique"
    assert chapter_16a.payload_ir is not None
    assert chapter_16a.payload_ir.kind is IRNodeKind.CHAPTER
    assert chapter_16a.payload_ir.label == "16a"
    chapter_16a_text = irnode_to_text(chapter_16a.payload_ir)
    assert "Vakuutuskannan luovuttaminen" in chapter_16a_text
    assert "Jakautuminen" not in chapter_16a_text
    assert all(
        child.label != "16bluku"
        for child in chapter_16a.payload_ir.children
        if child.kind is IRNodeKind.SECTION
    )

    assert chapter_16b.lookup_status == "unique"
    assert chapter_16b.payload_ir is not None
    assert chapter_16b.payload_ir.kind is IRNodeKind.CHAPTER
    assert chapter_16b.payload_ir.label == "16b"
    assert "Jakautuminen" in irnode_to_text(chapter_16b.payload_ir)
    assert [
        child.label
        for child in chapter_16b.payload_ir.children
        if child.kind is IRNodeKind.SECTION
    ] == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_source_model_chapter_payload_lookup_salvages_marker_only_pseudo_chapter() -> None:
    xml = get_corpus_store().read_source("1995/1396")
    assert xml is not None
    model = AmendmentSourceModel.from_tree(etree.fromstring(xml), source_ref="1995/1396")

    lookup = model.lookup_payload_ir("chapter", "8a")

    assert lookup.lookup_status == "unique"
    assert lookup.payload_basis == "body_inventory"
    assert lookup.payload_ir is not None
    assert lookup.payload_ir.kind is IRNodeKind.CHAPTER
    assert lookup.payload_ir.label == "8a"
    assert "Kansainvälinen hakemus" in irnode_to_text(lookup.payload_ir)
    assert all(child.kind is not IRNodeKind.SECTION for child in lookup.payload_ir.children)


def test_source_model_section_payload_text_uses_typed_payload_ir() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <section>
                <num>5 \xc2\xa7</num>
                <content><p>Vuodelta 1984 toimitettavassa verotuksessa teksti.</p></content>
              </section>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="1982/1035")

    result = model.lookup_section_payload_text("5")

    assert result.lookup_status == "unique"
    assert result.payload_lookup_status == "unique"
    assert result.payload_basis == "body_inventory"
    assert "Vuodelta 1984 toimitettavassa verotuksessa" in result.text


def test_source_model_payload_lookup_does_not_xml_fallback_for_non_unique_body_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/10")

    def fail_xml_lookup(*_args: object, **_kwargs: object) -> None:
        pytest.fail("typed non-unique payload lookup must not fall back to XML")

    monkeypatch.setattr(AmendmentSourceModel, "find_xml_node", fail_xml_lookup, raising=False)

    ambiguous = model.lookup_payload_ir("section", "5")
    assert ambiguous.lookup_status == "ambiguous"
    assert ambiguous.body_lookup_status == "ambiguous"
    assert ambiguous.payload_basis == "none"
    assert ambiguous.payload_ir is None

    missing = model.lookup_payload_ir("section", "6")
    assert missing.lookup_status == "missing"
    assert missing.body_lookup_status == "missing"
    assert missing.payload_basis == "none"
    assert missing.payload_ir is None

    scoped_mismatch = model.lookup_payload_ir("section", "5", target_chapter="3")
    assert scoped_mismatch.lookup_status == "missing"
    assert scoped_mismatch.body_lookup_status == "missing"
    assert scoped_mismatch.payload_basis == "none"
    assert scoped_mismatch.payload_ir is None


def test_source_model_payload_lookup_resolves_duplicate_coverage_refs_by_unit_id() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <section>
                <num>4 a \xc2\xa7</num>
                <subsection><content><p>foo</p></content></subsection>
              </section>
              <section>
                <num>4 a \xc2\xa7</num>
                <subsection><content><p>bar</p></content></subsection>
              </section>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/11")
    coverage_units = model.body_coverage_units()
    source_refs = [unit.payload_ref for unit in coverage_units]
    assert isinstance(source_refs[0], BodyCoveragePayloadRef)
    assert isinstance(source_refs[1], BodyCoveragePayloadRef)

    first = model.lookup_payload_ir_for_coverage_ref(source_refs[0])
    second = model.lookup_payload_ir_for_coverage_ref(source_refs[1])

    assert first.lookup_status == "unique"
    assert first.body_lookup_status == "ambiguous"
    assert first.payload_basis == "coverage_payload_ref"
    assert first.payload_ir is not None
    assert "foo" in irnode_to_text(first.payload_ir)
    assert second.lookup_status == "unique"
    assert second.body_lookup_status == "ambiguous"
    assert second.payload_basis == "coverage_payload_ref"
    assert second.payload_ir is not None
    assert "bar" in irnode_to_text(second.payload_ir)


def test_source_model_payload_lookup_exposes_missing_and_ambiguous_verdicts() -> None:
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
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/10")

    ambiguous = model.lookup_payload_ir("section", "5")
    assert ambiguous.lookup_status == "ambiguous"
    assert ambiguous.body_lookup_status == "ambiguous"
    assert ambiguous.payload_basis == "none"
    assert tuple(unit.chapter_label for unit in ambiguous.body_candidates) == ("1", "2")
    assert ambiguous.payload_ir is None
    assert ambiguous.cross_heading_ir is None

    missing = model.lookup_payload_ir("section", "6")
    assert missing.lookup_status == "missing"
    assert missing.body_lookup_status == "missing"
    assert missing.payload_basis == "none"
    assert missing.payload_ir is None
    assert missing.cross_heading_ir is None


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


def test_source_model_exposes_cached_source_chapter_declarations() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <part>
                <num>V osa</num>
                <chapter>
                  <num>7 luku</num>
                  <section><num>45 \xc2\xa7</num></section>
                  <section><num>46 \xc2\xa7</num></section>
                </chapter>
              </part>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/4")

    chapters = model.source_chapters()

    assert chapters is model.source_chapters()
    assert len(chapters) == 1
    assert chapters[0].part_label == "5"
    assert chapters[0].chapter_label == "7"
    assert chapters[0].section_labels == ("45", "46")


def test_source_model_exposes_cached_source_pseudo_chapter_declarations() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>7 luku</num>
                <section>
                  <num>7 a luku</num>
                  <section><num>55 \xc2\xa7</num></section>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/6")

    pseudo_chapters = model.source_pseudo_chapters()

    assert pseudo_chapters is model.source_pseudo_chapters()
    assert len(pseudo_chapters) == 1
    assert pseudo_chapters[0].part_label == ""
    assert pseudo_chapters[0].chapter_label == "7a"
    assert pseudo_chapters[0].num_text == "7 a luku"


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
    assert model.source_text() is model.source_text()
    assert model.source_text_contains("LISÄTÄÄN")
    assert not model.source_text_contains("")
    assert model.has_uncovered_recovery_content_ops([])


def test_source_model_builds_uncovered_recovery_context() -> None:
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <preamble><p>muutetaan 4 \xc2\xa7 ja lis\xc3\xa4t\xc3\xa4\xc3\xa4n uusi 9 luku</p></preamble>
            <body/>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2000/7")
    op = AmendmentOp(
        op_id="insert_4",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="4",
    )

    context = model.build_uncovered_recovery_context(
        ops=[op],
        new_chapter_labels={"2"},
    )

    assert "2" in context.owned_chapter_labels
    assert "4" in context.johto_mentioned_labels
    assert "9" in context.source_owned_insert_chapter_labels


# ---------------------------------------------------------------------------
# Intrinsic content-digest binding (source identity plane)
# ---------------------------------------------------------------------------


def _digest_source_bytes(suffix: bytes = b"") -> bytes:
    return (
        b"<akomaNtoso><act><body><section><num>1 \xc2\xa7</num>"
        b"<content>teksti.</content></section></body></act></akomaNtoso>" + suffix
    )


def test_source_model_binds_content_digest_when_bytes_present():
    import hashlib

    raw = _digest_source_bytes()
    model = AmendmentSourceModel.from_tree(
        etree.fromstring(raw),
        source_ref="2020/100",
        source_bytes=raw,
    )

    assert model.source_digest is not None
    assert model.source_digest.digest_algorithm == "sha256"
    # Content-addressed: digest is sha256 of the actual bytes, not the source_ref.
    assert model.source_digest.digest == hashlib.sha256(raw).hexdigest()
    assert model.source_digest.digest != hashlib.sha256(b"2020/100").hexdigest()
    assert model.pre_correction_digest is None


def test_source_model_digest_is_content_addressed_not_name_addressed():
    a = _digest_source_bytes(b"<!--a-->")
    b = _digest_source_bytes(b"<!--b-->")

    # Same source_ref, different bytes -> different digest.
    model_a = AmendmentSourceModel.from_tree(
        etree.fromstring(a), source_ref="2020/100", source_bytes=a
    )
    model_b = AmendmentSourceModel.from_tree(
        etree.fromstring(b), source_ref="2020/100", source_bytes=b
    )
    assert model_a.source_digest is not None
    assert model_b.source_digest is not None
    assert model_a.source_digest.digest != model_b.source_digest.digest


def test_source_model_no_digest_when_no_bytes():
    model = AmendmentSourceModel.from_tree(
        etree.fromstring(_digest_source_bytes()),
        source_ref="2020/100",
    )
    assert model.source_digest is None
    assert model.pre_correction_digest is None


def test_source_model_pre_post_correction_digest_pair():
    import hashlib

    pre = _digest_source_bytes(b"<!--uncorrected-->")
    post = _digest_source_bytes(b"<!--corrected-->")

    model = AmendmentSourceModel.from_tree(
        etree.fromstring(post),
        source_ref="2020/100",
        source_bytes=post,
        pre_correction_bytes=pre,
    )

    assert model.source_digest is not None
    assert model.pre_correction_digest is not None
    # The pair witnesses the correction as a content change.
    assert model.pre_correction_digest.digest == hashlib.sha256(pre).hexdigest()
    assert model.source_digest.digest == hashlib.sha256(post).hexdigest()
    assert model.source_digest.digest != model.pre_correction_digest.digest


def test_source_model_no_pre_correction_digest_when_bytes_unchanged():
    same = _digest_source_bytes()
    model = AmendmentSourceModel.from_tree(
        etree.fromstring(same),
        source_ref="2020/100",
        source_bytes=same,
        pre_correction_bytes=same,
    )
    # No correction happened -> no separate pre-correction witness.
    assert model.pre_correction_digest is None
    assert model.source_digest is not None
