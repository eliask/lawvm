from __future__ import annotations

from lxml import etree

from lawvm.core.compile_result import StrictProfile
from lawvm.finland.acquisition import build_amendment_acquisition_result
from lawvm.tools.phase_witness import _build_acquisition_witness


def _sec1_fallback_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <formula name="enactingClause">Ympäristöministerin esittelystä säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <content>muutetaan rakennuslain (370/1958) 3 § seuraavasti:</content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _sec1_keeper_repeal_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <formula name="enactingClause">Eduskunnan päätöksen mukaisesti säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <content>
            Tällä lailla kumotaan eläintautilailla (441/2013) voimaan jätetyt
            kumotun eläintautilain (55/1980) 12 §:n 1 momentin johdantokappale
            ja 9 kohta sekä 2-4 momentti, 12 f § ja 15 §:n 5 momentti,
            sellaisina kuin ne ovat laissa 303/2006.
          </content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _sec1_multi_parent_repeal_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <formula name="enactingClause">Eduskunnan päätöksen mukaisesti säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <content>
            Tällä lailla kumotaan 17 päivänä syyskuuta 1982 annetun
            sosiaalihuoltolain (710/1982) 30-38 § ja 30 §:n edellä oleva
            väliotsikko sekä 29 päivänä kesäkuuta 1983 annetun
            sosiaalihuoltoasetuksen (607/1983) 14 §, sellaisina kuin niistä
            ovat lain 34 § osaksi laissa 736/1992 ja 38 § mainitussa laissa.
          </content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _sec1_numbered_multi_statute_repeal_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <formula name="enactingClause">Eduskunnan päätöksen mukaisesti säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <subsection>
            <intro>
              <p>Tällä lailla kumotaan seuraavat lainkohdat:</p>
            </intro>
            <paragraph>
              <num>1)</num>
              <content>
                <p>tasavallan presidentin kansliasta annetun lain (1382/1995) 57 §;</p>
              </content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content>
                <p>valtioneuvostosta annetun lain (78/1922) 3 §:n 2 momentti;</p>
              </content>
            </paragraph>
            <paragraph>
              <num>3)</num>
              <content>
                <p>ulkoasiainhallintolain (204/2000) 24 §:n 1 momentti.</p>
              </content>
            </paragraph>
          </subsection>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _body_lead_fallback_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>Eduskunnan päätöksen mukaisesti</p>
        </formula>
      </preamble>
      <body>
        <section eId="sec_body">
          <content>
            kumotaan merenkulun ympäristönsuojelulain (1672/2009) 4 luvun 2 §:n 2 momentti,
            muutetaan 1 luvun 2 §:n 25 kohta sekä
            lisätään lakiin uusi 2 a luku, 7 lukuun uusi 14 a ja 14 b § sekä
            13 luvun 3 §:n 2 momenttiin uusi 3 a ja 8 a kohta seuraavasti:
          </content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _operative_preamble_wins_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>muutetaan merenkulun ympäristönsuojelulain (1672/2009) 13 luvun 3 §:ää seuraavasti:</p>
        </formula>
      </preamble>
      <body>
        <section eId="sec_body">
          <content>
            lisätään 13 luvun 3 §:n 2 momenttiin uusi 8 a kohta seuraavasti:
          </content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _split_preamble_body_lead_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>kumotaan yritystuen yleisistä ehdoista annetun lain (786/1997) 10 §:n 2 momentti sekä</p>
        </formula>
      </preamble>
      <body>
        <hcontainer name="statuteProvisionsWrapper">
          <section eId="body_lead">
            <subsection>
              <content>
                <p><i>muutetaan </i>9 § seuraavasti:</p>
              </content>
            </subsection>
          </section>
          <section eId="sec_9">
            <num>9 §</num>
            <heading>Salassa pidettävien tietojen luovuttaminen</heading>
          </section>
        </hcontainer>
      </body>
    </akn>
    """.encode("utf-8")


def _corrupt_citation_rewrite_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>Eduskunnan päätöksen mukaisesti muutetaan 16 päivänä elokuuta 1958
          annetun rakennuslain (70/58) 11 §:n 2 momentti seuraavasti:</p>
        </formula>
      </preamble>
    </akn>
    """.encode("utf-8")


def _nojalla_authority_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>Opetusministerin esittelystä säädetään ammatillisista oppilaitoksista
          annetun lain (487/87) 60 §:n nojalla:</p>
        </formula>
      </preamble>
    </akn>
    """.encode("utf-8")


def test_build_amendment_acquisition_result_uses_sec1_pre_routing_fallback() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_fallback_xml(),
        parent_id="1958/370",
        amendment_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        parent_title="Rakennuslaki",
    )

    assert result.decision.selected_lane == "sec1_fallback_pre_routing"
    assert result.decision.pre_routing_sec1_requested is True
    assert result.decision.pre_routing_sec1_applied is True
    assert "rakennuslain (370/1958) 3 §" in result.decision.chosen_normalized_text
    assert result.decision.should_apply is True


def test_build_amendment_acquisition_result_keeps_parent_owned_sec1_repeal_list() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_keeper_repeal_xml(),
        parent_id="1980/55",
        amendment_id="2015/521",
        source_title="Laki kumotun eläintautilain voimaan jätettyjen säännösten kumoamisesta",
        parent_title="Eläintautilaki",
    )

    assert result.decision.selected_lane == "sec1_fallback_pre_routing"
    assert "12 f §" in result.sec1_text
    assert "15 §:n 5 momentti" in result.sec1_text
    assert "(441/2013)" in result.sec1_text
    assert "(55/1980)" in result.sec1_text


def test_build_amendment_acquisition_result_still_narrows_multi_parent_sec1_repeal() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_multi_parent_repeal_xml(),
        parent_id="1983/607",
        amendment_id="1992/736",
        source_title="Laki sosiaalihuollon muutoksista",
        parent_title="Sosiaalihuoltoasetus",
    )

    assert result.decision.selected_lane == "sec1_fallback_pre_routing"
    assert "(607/1983)" in result.sec1_text
    assert "14 §" in result.sec1_text
    assert "(710/1982)" not in result.sec1_text
    assert "30-38 §" not in result.sec1_text


def test_build_amendment_acquisition_result_narrows_numbered_multi_statute_sec1_repeal() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_numbered_multi_statute_repeal_xml(),
        parent_id="1995/1382",
        amendment_id="2000/962",
        source_title="Laki eräiden oikeuspaikkaa koskevien säännösten kumoamisesta",
        parent_title="Laki tasavallan presidentin kansliasta",
    )

    assert result.decision.selected_lane == "sec1_fallback_pre_routing"
    assert "(1382/1995)" in result.sec1_text
    assert "57 §" in result.sec1_text
    assert "(78/1922)" not in result.sec1_text
    assert "3 §:n 2 momentti" not in result.sec1_text
    assert "(204/2000)" not in result.sec1_text
    assert "24 §:n 1 momentti" not in result.sec1_text


def test_build_amendment_acquisition_result_reuses_supplied_tree(monkeypatch) -> None:
    xml_bytes = _sec1_fallback_xml()
    muutos_tree = etree.fromstring(xml_bytes)

    def fail_fromstring(_xml_bytes: bytes):
        raise AssertionError("build_amendment_acquisition_result reparsed supplied tree")

    monkeypatch.setattr("lawvm.finland.acquisition.etree.fromstring", fail_fromstring)

    result = build_amendment_acquisition_result(
        xml_bytes=xml_bytes,
        muutos_tree=muutos_tree,
        parent_id="1958/370",
        amendment_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        parent_title="Rakennuslaki",
    )

    assert result.decision.selected_lane == "sec1_fallback_pre_routing"


def test_build_amendment_acquisition_result_keeps_short_operative_preamble() -> None:
    xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>muutetaan (516/2011) 1 § seuraavasti:</p>
        </formula>
      </preamble>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <subsection>
            <content>
              <p>Tuomioistuimen on tehtävä ilmoitukset ulosottoviranomaiselle.</p>
            </content>
          </subsection>
        </section>
      </body>
    </akn>
    """.encode("utf-8")

    result = build_amendment_acquisition_result(
        xml_bytes=xml,
        parent_id="2011/516",
        amendment_id="2011/582",
        source_title=(
            "Oikeusministeriön asetus ulosottoperustetta koskevan tuomioistuimen "
            "ilmoitusvelvollisuuden alkamisesta annetun asetuksen muuttamisesta"
        ),
        parent_title=(
            "Oikeusministeriön asetus ulosottoperustetta koskevan tuomioistuimen "
            "ilmoitusvelvollisuuden alkamisesta"
        ),
    )

    assert result.decision.selected_lane == "preamble"
    assert result.decision.pre_routing_sec1_requested is False
    assert result.decision.chosen_normalized_text == "muutetaan (516/2011) 1 § seuraavasti:"
    assert result.decision.should_apply is True
    assert result.decision.route_reason == "references_parent"


def test_build_amendment_acquisition_result_accepts_parent_validated_citation_typo() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_corrupt_citation_rewrite_xml(),
        parent_id="1958/370",
        amendment_id="1965/301",
        source_title="Laki rakennuslain muuttamisesta",
        parent_title="Rakennuslaki",
        parent_issue_date="1958-08-16",
    )

    assert result.decision.should_apply is True
    assert result.decision.route_reason == "citation_typo_rewrite_parent_validated"
    assert result.decision.selected_lane == "preamble"


def test_build_amendment_acquisition_result_classifies_nojalla_authority_skip() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_nojalla_authority_xml(),
        parent_id="1987/491",
        amendment_id="1992/1314",
        source_title="Asetus ammatillisista oppilaitoksista",
        parent_title="Asetus ammatillisista oppilaitoksista",
    )

    assert result.decision.should_apply is False
    assert result.decision.route_reason == "delegated_authority_nojalla_skip"


def test_phase_witness_acquisition_projects_shared_acquisition_result() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_fallback_xml(),
        parent_id="1958/370",
        amendment_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        parent_title="Rakennuslaki",
    )

    witness = _build_acquisition_witness(
        parent_id="1958/370",
        parent_title="Rakennuslaki",
        source_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        xml_bytes=_sec1_fallback_xml(),
    )

    assert witness["source_lane_used"] == result.decision.selected_lane
    assert witness["chosen_operative_text"] == result.decision.chosen_normalized_text
    assert witness["route"]["should_apply"] == result.decision.should_apply
    assert witness["route"]["reason"] == result.decision.route_reason
    assert witness["route"]["target_amendment_id"] == result.decision.route_target_amendment_id
    assert witness["source_lane_selection"]["family"] == "source_lane_selection"
    assert witness["source_lane_selection"]["selected_source_lane"] == "sec1_fallback_pre_routing"
    assert witness["source_lane_selection"]["source_lane_attempts"][1]["lane"] == "sec1_fallback"
    assert witness["source_lane_selection"]["source_lane_attempts"][1]["lane_attempt_status"] == "selected"
    assert witness["diagnostics"] == []


def test_strict_profile_records_blocked_sec1_pre_routing_fallback() -> None:
    strict_profile = StrictProfile(
        name="test_strict",
        allows_context_dependent_anchor_resolution=False,
    )

    result = build_amendment_acquisition_result(
        xml_bytes=_sec1_fallback_xml(),
        parent_id="1958/370",
        amendment_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        parent_title="Rakennuslaki",
        strict_profile=strict_profile,
    )

    assert result.decision.pre_routing_sec1_requested is True
    assert result.decision.pre_routing_sec1_applied is False
    assert result.decision.selected_lane == "preamble"
    assert ("sec1_fallback", "strict_profile_blocked_context_dependent_anchor_resolution") in result.rejected_lanes
    sec1_candidate = next(candidate for candidate in result.candidates if candidate.lane == "sec1_fallback")
    assert sec1_candidate.reason == "strict_profile_blocked_context_dependent_anchor_resolution"
    assert [diagnostic.rule_id for diagnostic in result.diagnostics] == [
        "ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
        "ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
    ]
    assert [diagnostic.lane for diagnostic in result.diagnostics] == [
        "sec1_fallback_pre_routing",
        "sec1_fallback_post_routing",
    ]
    assert {diagnostic.strict_disposition for diagnostic in result.diagnostics} == {"block"}


def test_phase_witness_projects_strict_blocked_acquisition_diagnostics() -> None:
    witness = _build_acquisition_witness(
        parent_id="1958/370",
        parent_title="Rakennuslaki",
        source_id="1993/949",
        source_title="Rakennuslain muuttamisesta",
        xml_bytes=_sec1_fallback_xml(),
        strict_profile=StrictProfile(
            name="test_strict",
            allows_context_dependent_anchor_resolution=False,
        ),
    )

    assert witness["diagnostics"] == [
        {
            "rule_id": "ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
            "family": "target_resolution_recovery",
            "phase": "acquisition",
            "reason": "strict profile blocked context-dependent section 1 operative fallback",
            "lane": "sec1_fallback_pre_routing",
            "strict_profile": "test_strict",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
        {
            "rule_id": "ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
            "family": "target_resolution_recovery",
            "phase": "acquisition",
            "reason": "strict profile blocked context-dependent section 1 operative fallback after routing",
            "lane": "sec1_fallback_post_routing",
            "strict_profile": "test_strict",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]
    assert witness["rejected_lanes"] == [
        {
            "lane": "sec1_fallback",
            "reason": "strict_profile_blocked_context_dependent_anchor_resolution",
        }
    ]
    assert witness["source_lane_selection"]["selected_source_lane"] == "preamble"
    assert witness["source_lane_selection"]["source_lane_attempts"][1]["lane_attempt_status"] == (
        "strict_profile_blocked_context_dependent_anchor_resolution"
    )


def test_build_amendment_acquisition_result_uses_body_lead_pre_routing_fallback() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_body_lead_fallback_xml(),
        parent_id="2009/1672",
        amendment_id="2017/275",
        source_title="Laki merenkulun ympäristönsuojelulain muuttamisesta",
        parent_title="Merenkulun ympäristönsuojelulaki",
    )

    assert result.decision.selected_lane == "body_lead_fallback_pre_routing"
    assert "13 luvun 3 §:n 2 momenttiin uusi 3 a ja 8 a kohta" in result.decision.chosen_normalized_text
    assert "7 lukuun uusi 14 a ja 14 b §" in result.decision.chosen_normalized_text
    assert result.decision.should_apply is True


def test_build_amendment_acquisition_result_keeps_operative_preamble_over_body_lead() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_operative_preamble_wins_xml(),
        parent_id="2009/1672",
        amendment_id="2024/999",
        source_title="Test amendment",
        parent_title="Merenkulun ympäristönsuojelulaki",
    )

    assert result.decision.selected_lane == "preamble"
    assert "13 luvun 3 §:ää seuraavasti" in result.decision.chosen_normalized_text
    assert "8 a kohta" not in result.decision.chosen_normalized_text


def test_build_amendment_acquisition_result_combines_split_preamble_body_lead() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_split_preamble_body_lead_xml(),
        parent_id="1997/786",
        amendment_id="1999/638",
        source_title="Laki yritystuen yleisistä ehdoista annetun lain muuttamisesta",
        parent_title="Laki yritystuen yleisistä ehdoista",
    )

    assert result.decision.selected_lane == "preamble_body_lead_combined"
    assert result.decision.preamble_body_lead_combine_requested is True
    assert result.decision.preamble_body_lead_combine_applied is True
    assert "10 §:n 2 momentti sekä muutetaan 9 § seuraavasti" in result.decision.chosen_normalized_text
    assert result.decision.should_apply is True
    assert [candidate.lane for candidate in result.candidates] == [
        "preamble",
        "body_lead_fallback",
        "preamble_body_lead_combined",
    ]
    assert result.candidates[-1].selected is True


def test_build_amendment_acquisition_result_strict_blocks_split_preamble_body_lead() -> None:
    result = build_amendment_acquisition_result(
        xml_bytes=_split_preamble_body_lead_xml(),
        parent_id="1997/786",
        amendment_id="1999/638",
        source_title="Laki yritystuen yleisistä ehdoista annetun lain muuttamisesta",
        parent_title="Laki yritystuen yleisistä ehdoista",
        strict_profile=StrictProfile(
            name="test_strict",
            allows_context_dependent_anchor_resolution=False,
        ),
    )

    assert result.decision.selected_lane == "preamble"
    assert result.decision.preamble_body_lead_combine_requested is True
    assert result.decision.preamble_body_lead_combine_applied is False
    assert "muutetaan 9 §" not in result.decision.chosen_normalized_text
    assert [diagnostic.lane for diagnostic in result.diagnostics] == ["preamble_body_lead_combined"]
    assert result.diagnostics[0].rule_id == "ACQ.OPERATIVE_LANE_STRICT_BLOCKED"


def test_build_amendment_acquisition_result_extracts_pending_amendment_target_id() -> None:
    xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <p>muutetaan valmiuslain muuttamisesta annetun lain (631/2022) 88 ja 126 § seuraavasti:</p>
        </formula>
      </preamble>
    </akn>
    """.encode("utf-8")

    result = build_amendment_acquisition_result(
        xml_bytes=xml,
        parent_id="2011/1552",
        amendment_id="2022/1188",
        source_title="Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta",
        parent_title="Valmiuslaki",
    )

    assert result.decision.should_apply is False
    assert result.decision.route_reason == "pending_amendment_of_parent_skip"
    assert result.decision.route_target_amendment_id == "2022/631"
