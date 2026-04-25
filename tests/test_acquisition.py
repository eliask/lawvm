from __future__ import annotations

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
