from __future__ import annotations

from lawvm.finland.delegation import (
    DelegationDiagnostic,
    _normalize_year,
    extract_asetus_authority,
    extract_delegations,
)


def test_extract_delegations_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[DelegationDiagnostic] = []

    result = extract_delegations(b"<akomaNtoso>", "2000/1", diagnostics_out=diagnostics)

    assert result.accepted_items == ()
    # Conservation: the parse failure is also carried structurally in the result
    # so a caller cannot receive (empty) edges without the reject ledger.
    assert [r.reason_code for r in result.rejected_items] == [
        "fi_delegation_extraction_xml_parse_failed"
    ]
    assert result.rejected_items[0].blocking is True
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "fi_delegation_extraction_xml_parse_failed"
    ]
    assert diagnostics[0].family == "source_pathology"
    assert diagnostics[0].blocking is True
    assert diagnostics[0].strict_disposition == "block"


def test_extract_asetus_authority_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[DelegationDiagnostic] = []

    result = extract_asetus_authority(b"<akomaNtoso>", "2000/2", diagnostics_out=diagnostics)

    assert result.accepted_items == ()
    assert [r.reason_code for r in result.rejected_items] == [
        "fi_authority_extraction_xml_parse_failed"
    ]
    assert result.rejected_items[0].blocking is True
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

    result = extract_delegations(xml, "2000/1", diagnostics_out=diagnostics)

    assert result.accepted_items == ()
    # Conservation fire-drill: the rejected regex candidate is carried in the
    # result's reject ledger (not silently discarded behind diagnostics_out).
    assert [r.reason_code for r in result.rejected_items] == [
        "fi_delegation_commencement_reference_filtered"
    ]
    assert result.rejected_items[0].item.section == "1"
    assert result.rejected_items[0].item.eid == "sec_1__subsec_1"
    assert result.rejected_items[0].blocking is False
    assert result.rejected_reason_counts() == {
        "Finnish delegation extractor rejected a regex candidate using a named negative filter.": 1
    }
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

    result = extract_delegations(xml, "2000/1", diagnostics_out=diagnostics)

    assert [
        (edge.section, edge.eid, edge.delegation_type)
        for edge in result.accepted_items
    ] == [("1", "sec_1__subsec_1", "VN_ASETUS")]
    assert result.rejected_items == ()
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

    edges = extract_asetus_authority(xml, "2018/1158").accepted_items

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

    edges = extract_asetus_authority(xml, "2010/908").accepted_items

    triples = {
        (e.parent_statute_id, e.parent_section, e.parent_moment, e.parent_kind)
        for e in edges
    }
    # Both conjuncts present, each with its own section and act kind.
    assert ("1998/629", "36", "1", "act") in triples
    assert ("1992/150", "8", "", "act") in triples


def test_extract_asetus_authority_preserves_section_letter_suffix() -> None:
    # "(1301/2014) 60 a §:n nojalla" — repro 2024/348. The letter suffix MUST be
    # glued onto the section ("60a", matching the AKN sec_ / inline-CITES form);
    # dropping it collapses "60 a §" and "60 §" onto the same provision.
    xml = _preamble_xml(
        "Maa- ja metsätalousministeriön päätöksen mukaisesti säädetään "
        "eräiden lain (1301/2014) 60 a §:n nojalla:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "2024/348").accepted_items

    assert [
        (e.parent_statute_id, e.parent_section, e.parent_kind) for e in edges
    ] == [("2014/1301", "60a", "act")]


def test_extract_asetus_authority_decree_basis_kind_not_act() -> None:
    # A genuine decree authority basis ("…asetuksen (…) … nojalla") must NOT be
    # classified as an act, so the lift keeps it a non-statutory instrument.
    xml = _preamble_xml(
        "Säädetään esimerkkiasetuksen (1248/2005) 3 §:n nojalla:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "2099/1").accepted_items

    assert [
        (e.parent_statute_id, e.parent_section, e.parent_kind) for e in edges
    ] == [("2005/1248", "3", "decree")]


def test_normalize_year_bounded_by_citing_year() -> None:
    # An authority basis cannot post-date the decree it authorizes: when the
    # decree's year is known it bounds the 2-digit pivot. ``04`` cited by a 1990
    # decree is 1904 (2004 post-dates 1990); cited by a 2010 decree it is 2004.
    assert _normalize_year("04", 1990) == "1904"
    assert _normalize_year("04", 2010) == "2004"
    assert _normalize_year("86", 1990) == "1986"
    # 4-digit untouched, with or without a citing year.
    assert _normalize_year("1986", 1990) == "1986"
    # Unknown citing year preserves the legacy fixed cutoff (no regression).
    assert _normalize_year("04") == "2004"
    assert _normalize_year("86") == "1986"


def test_extract_asetus_authority_two_digit_year_bounded_to_citing_decree() -> None:
    # A pre-2000 decree's preamble cites its authorizing law with a 2-digit year.
    # The cited law cannot post-date the citing decree, so ``(82/16)`` cited by a
    # 1952 decree is 1916, not 2016.
    xml = _preamble_xml(
        "Säädetään maanmittauslain (82/16) 3 §:n nojalla:".encode("utf-8")
    )

    edges = extract_asetus_authority(xml, "1952/407").accepted_items

    assert [
        (e.parent_statute_id, e.parent_section) for e in edges
    ] == [("1916/82", "3")]


def test_census_oracle_path_conserves_rejected_false_positive() -> None:
    # Rank-14 fire-drill: a known false-positive (a commencement reference the
    # negative filter rejects) driven through the SAME production extractor the
    # delegation census oracle path (``_build_delegation_oracle``) consumes.
    # The census reads ``.accepted_items`` (so the rejected candidate is NOT
    # bucketed as a grant — no phantom oracle edge), while the conservation
    # contract carries that rejection in the reject ledger rather than dropping
    # it silently behind an omitted ``diagnostics_out``.
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

    result = extract_delegations(xml, "2000/9")

    # Production census consumes only accepted edges → no phantom grant.
    assert list(result.accepted_items) == []
    # ...but the false-positive rejection is conserved, not silently discarded.
    assert [r.reason_code for r in result.rejected_items] == [
        "fi_delegation_commencement_reference_filtered"
    ]
