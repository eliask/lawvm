from __future__ import annotations

from lawvm.finland.delegation import (
    DelegationDiagnostic,
    extract_asetus_authority,
    extract_delegations,
)


def test_extract_delegations_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[DelegationDiagnostic] = []

    edges = extract_delegations(b"<akomaNtoso>", "2000/1", diagnostics_out=diagnostics)

    assert edges == []
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_delegation_extraction_xml_parse_failed"
    ]
    assert diagnostics[0].family == "source_pathology"
    assert diagnostics[0].blocking is True
    assert diagnostics[0].strict_disposition == "block"


def test_extract_asetus_authority_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[DelegationDiagnostic] = []

    edges = extract_asetus_authority(b"<akomaNtoso>", "2000/2", diagnostics_out=diagnostics)

    assert edges == []
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_authority_extraction_xml_parse_failed"
    ]
    assert diagnostics[0].phase == "authority_extraction"
    assert diagnostics[0].blocking is True


def test_extract_delegations_records_named_negative_filter() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <section eId="sec_1">
            <num>1 \xc2\xa7</num>
            <subsection eId="sec_1__subsec_1">
              <content>
                <p>
                  Tarkemmat s\xc3\xa4\xc3\xa4nn\xc3\xb6kset voimaantulosta
                  s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n valtioneuvoston asetuksella.
                </p>
              </content>
            </subsection>
          </section>
        </body>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[DelegationDiagnostic] = []

    edges = extract_delegations(xml, "2000/1", diagnostics_out=diagnostics)

    assert edges == []
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_delegation_commencement_reference_filtered"
    ]
    assert diagnostics[0].family == "graph_edge_filter"
    assert diagnostics[0].section == "1"
    assert diagnostics[0].eid == "sec_1__subsec_1"
    assert diagnostics[0].blocking is False


def test_extract_delegations_negative_filter_does_not_block_valid_delegation() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <section eId="sec_1">
            <num>1 \xc2\xa7</num>
            <subsection eId="sec_1__subsec_1">
              <content>
                <p>
                  Tarkemmat s\xc3\xa4\xc3\xa4nn\xc3\xb6kset hakemuksesta
                  s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n valtioneuvoston asetuksella.
                </p>
              </content>
            </subsection>
          </section>
        </body>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[DelegationDiagnostic] = []

    edges = extract_delegations(xml, "2000/1", diagnostics_out=diagnostics)

    assert [(edge.section, edge.eid, edge.delegation_type) for edge in edges] == [
        ("1", "sec_1__subsec_1", "VN_ASETUS")
    ]
    assert diagnostics == []


def _preamble_xml(preamble_text: bytes) -> bytes:
    return (
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<act><preamble><p>" + preamble_text + b"</p></preamble>"
        b"<body><section><num>1 \xc2\xa7</num></section></body></act>"
        b"</akomaNtoso>"
    )


def test_extract_asetus_authority_single_nojalla_basis_typed_act() -> None:
    # "annetun lain (1048/2016) 37 §:n nojalla" — repro 2018/1158.
    xml = _preamble_xml(
        "Maa- ja metsätalousministeriön päätöksen mukaisesti säädetään Euroopan "
        "unionin yhteisen kalastuspolitiikan kansallisesta täytäntöönpanosta "
        "annetun lain (1048/2016) 37 §:n nojalla:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "2018/1158")

    assert [
        (e.parent_statute_id, e.parent_section, e.parent_moment, e.parent_kind)
        for e in edges
    ] == [("2016/1048", "37", "", "act")]


def test_extract_asetus_authority_distributes_over_coordinated_conjuncts() -> None:
    # "lukiolain (629/1998) 36 §:n 1 momentin ja valtion maksuperustelain
    #  (150/1992) 8 §:n nojalla" — repro 2010/908. The single nojalla authority
    # must distribute over BOTH conjuncts (the original code dropped 629/1998).
    xml = _preamble_xml(
        "Opetus- ja kulttuuriministeriön päätöksen mukaisesti säädetään "
        "lukiolain (629/1998) 36 §:n 1 momentin ja valtion maksuperustelain "
        "(150/1992) 8 §:n nojalla, sellaisena kuin niistä jälkimmäinen on "
        "laissa 348/1994:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "2010/908")

    triples = {
        (e.parent_statute_id, e.parent_section, e.parent_moment, e.parent_kind)
        for e in edges
    }
    # Both conjuncts present, each with its own section and act kind.
    assert ("1998/629", "36", "1", "act") in triples
    assert ("1992/150", "8", "", "act") in triples


def test_extract_asetus_authority_decree_basis_kind_not_act() -> None:
    # A genuine decree authority basis ("…asetuksen (…) … nojalla") must NOT be
    # classified as an act, so the lift keeps it a non-statutory instrument.
    xml = _preamble_xml(
        "Säädetään esimerkkiasetuksen (1248/2005) 3 §:n nojalla:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "2099/1")

    assert [
        (e.parent_statute_id, e.parent_section, e.parent_kind) for e in edges
    ] == [("2005/1248", "3", "decree")]
