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
