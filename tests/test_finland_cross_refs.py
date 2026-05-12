from __future__ import annotations

from lawvm.finland.cross_refs import CrossRefDiagnostic, extract_cross_refs


def test_extract_cross_refs_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(b"<akomaNtoso>", "2000/1", diagnostics_out=diagnostics)

    assert edges == []
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_xml_parse_failed"]
    assert diagnostics[0].family == "source_pathology"
    assert diagnostics[0].blocking is True
    assert diagnostics[0].strict_disposition == "block"


def test_extract_cross_refs_records_skipped_inline_self_reference() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <section>
            <num>5 \xc2\xa7</num>
            <paragraph>
              <content>
                <p>
                  <ref href="/akn/fi/act/statute/2000/1#sec_5">same act</ref>
                  <ref href="/akn/fi/act/statute/2001/2#sec_9">other act</ref>
                </p>
              </content>
            </paragraph>
          </section>
        </body>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(xml, "2000/1", diagnostics_out=diagnostics)

    assert [(edge.target_statute_id, edge.target_section) for edge in edges] == [("2001/2", "sec_9")]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_self_reference_skipped"]
    assert diagnostics[0].edge_type == "CITES"
    assert diagnostics[0].source_section == "5"
    assert diagnostics[0].target_section == "sec_5"
    assert diagnostics[0].blocking is False


def test_extract_cross_refs_records_skipped_metadata_self_reference() -> None:
    xml = b"""
    <akomaNtoso
      xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
      xmlns:finlex="http://data.finlex.fi/schema/finlex">
      <act>
        <meta>
          <finlex:repeals>
            <finlex:ref href="/akn/fi/act/statute/2000/1"/>
            <finlex:ref href="/akn/fi/act/statute/2001/2"/>
          </finlex:repeals>
        </meta>
        <body/>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(xml, "2000/1", diagnostics_out=diagnostics)

    assert [(edge.edge_type, edge.target_statute_id) for edge in edges] == [("REPEALS", "2001/2")]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_self_reference_skipped"]
    assert diagnostics[0].edge_type == "REPEALS"
    assert diagnostics[0].target_statute_id == "2000/1"
