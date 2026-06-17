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


def test_inline_ref_byte_span_is_inner_phrase_only() -> None:
    """The CITES byte span must slice exactly the citation phrase.

    Before the fix the span covered the whole ``<ref href="…">…</ref>`` markup
    envelope, so the slice contained ``<ref``/``href=`` markup instead of the
    surface phrase.
    """
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><section><num>5 §</num><paragraph><content><p>"
        'Katso <ref href="/akn/fi/act/statute/2001/2#sec_9">toinen laki</ref> tarkemmin.'
        "</p></content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    edges = extract_cross_refs(xml, "2000/1")
    cites = [e for e in edges if e.edge_type == "CITES"]
    assert len(cites) == 1
    edge = cites[0]
    assert edge.source_byte_offset is not None
    sliced = xml[edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len]
    assert sliced == b"toinen laki"
    assert b"<ref" not in sliced and b"href=" not in sliced
    assert " ".join(sliced.decode("utf-8").split()) == edge.surface_text


def test_inline_ref_byte_span_ignores_metadata_duplicate_href() -> None:
    """The locator must not latch onto a duplicate href in the metadata block.

    The same citation href appears first inside a leading ``<references>``
    block; a non-body-scoped search latched onto it and then ``<ref`` (a prefix
    of ``<references``) ran the close-tag forward to a body ``</ref>``, yielding
    a multi-KB span.
    """
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><meta><references>"
        '<TLCReference href="/akn/fi/act/statute/2001/2#sec_9" showAs="x"/>'
        "</references></meta><body><section><num>5 §</num>"
        "<paragraph><content><p>"
        'Katso <ref href="/akn/fi/act/statute/2001/2#sec_9">toinen laki</ref> tarkemmin.'
        "</p></content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    edges = extract_cross_refs(xml, "2000/1")
    cites = [e for e in edges if e.edge_type == "CITES"]
    assert len(cites) == 1
    edge = cites[0]
    assert edge.source_byte_offset is not None
    sliced = xml[edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len]
    assert sliced == b"toinen laki"
    # Span must be the short phrase, never the multi-KB catastrophe.
    assert edge.source_byte_len < 50
