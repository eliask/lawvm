from __future__ import annotations

from lawvm.uk_legislation.si_source_semantics import (
    is_uk_si_document_id,
    scan_si_source_semantics_bytes,
)


def _records(xml: str):
    return scan_si_source_semantics_bytes("uksi/2022/34", xml.encode(), source_path="source.xml")


def test_si_source_semantics_records_metadata_commencement_and_structure() -> None:
    rows = _records(
        """
        <Legislation xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"
                     NumberOfProvisions="4">
          <ukm:SecondaryMetadata>
            <ukm:DocumentMainType Value="UnitedKingdomStatutoryInstrument"/>
            <ukm:DocumentMinorType Value="order"/>
            <ukm:ComingIntoForce>
              <ukm:DateTime Date="2022-02-01"/>
            </ukm:ComingIntoForce>
          </ukm:SecondaryMetadata>
          <Secondary>
            <SecondaryPrelims/>
            <Body/>
            <SignedSection/>
          </Secondary>
        </Legislation>
        """
    )

    by_family = {row.family: row for row in rows}
    structure = by_family["si_structure_vocabulary"].to_dict()
    assert structure["document_main_type"] == "UnitedKingdomStatutoryInstrument"
    assert structure["document_minor_type"] == "order"
    assert structure["number_of_provisions"] == "4"
    assert structure["has_secondary_prelims"] is True
    assert structure["has_body"] is True
    assert structure["has_signed_section"] is True

    commencement = by_family["si_commencement_surface"].to_dict()
    assert commencement["status"] == "single_date"
    assert commencement["coming_into_force_dates"] == ("2022-02-01",)
    assert commencement["coming_into_force_element_count"] == 1


def test_si_source_semantics_records_vires_and_body_semantic_surfaces() -> None:
    rows = _records(
        """
        <Legislation>
          <Secondary>
            <SecondaryPrelims>
              <EnactingText>
                The Secretary of State makes these Regulations in exercise of
                the powers conferred by section 2.
              </EnactingText>
            </SecondaryPrelims>
            <Body>
              <P1>
                <Pnumber>1.</Pnumber>
                <Title>Citation, commencement and extent</Title>
                <P1para><Text>
                  These Regulations come into force on 1 March 2022 and extend
                  to England and Wales.
                </Text></P1para>
              </P1>
              <P1>
                <Pnumber>2.</Pnumber>
                <Title>Application</Title>
                <P1para><Text>
                  These Regulations apply to qualifying authorities only.
                </Text></P1para>
              </P1>
              <P1>
                <Pnumber>3.</Pnumber>
                <Title>Revocation</Title>
                <P1para><Text>
                  The 1999 Regulations are revoked.
                </Text></P1para>
              </P1>
            </Body>
          </Secondary>
        </Legislation>
        """
    )

    by_family = {}
    for row in rows:
        by_family.setdefault(row.family, []).append(row.to_dict())

    vires = by_family["si_vires_recital_surface"][0]
    assert vires["status"] == "matched"
    assert vires["has_vires_phrase"] is True

    assert by_family["si_body_commencement_clause_surface"][0]["provision_label"] == "1."
    assert (
        by_family["si_body_commencement_clause_surface"][0]["source_role"]
        == "instrument_body_provision"
    )
    assert by_family["si_extent_clause_surface"][0]["provision_title"] == (
        "Citation, commencement and extent"
    )
    assert by_family["si_application_clause_surface"][0]["provision_label"] == "2."
    assert by_family["si_revocation_lapse_surface"][0]["provision_label"] == "3."


def test_si_source_semantics_records_nested_body_p1_and_correction_slip_marker() -> None:
    rows = _records(
        """
        <Legislation>
          <Secondary>
            <Body>
              <Part>
                <P1>
                  <Pnumber>4.</Pnumber>
                  <Title>Lapse</Title>
                  <P1para><Text>This article ceases to have effect on 31 December.</Text></P1para>
                </P1>
              </Part>
            </Body>
            <ExplanatoryNotes>
              <P>This instrument has an associated correction slip.</P>
            </ExplanatoryNotes>
          </Secondary>
        </Legislation>
        """
    )

    by_family = {row.family: row.to_dict() for row in rows}
    assert by_family["si_revocation_lapse_surface"]["provision_label"] == "4."
    assert "correction slip" in by_family["si_correction_slip_surface"]["text_preview"]


def test_si_source_semantics_does_not_treat_ordinary_application_as_scope() -> None:
    rows = _records(
        """
        <Legislation>
          <Secondary>
            <Body>
              <P1>
                <Pnumber>2.</Pnumber>
                <P1para><Text>
                  An application for registration must include the applicant's name.
                </Text></P1para>
              </P1>
            </Body>
          </Secondary>
        </Legislation>
        """
    )

    assert "si_application_clause_surface" not in {row.family for row in rows}


def test_si_source_semantics_marks_amendment_payload_clause_role() -> None:
    rows = _records(
        """
        <Legislation>
          <Secondary>
            <Body>
              <P1>
                <Pnumber>2.</Pnumber>
                <P1para><Text>After section 233 insert—</Text>
                  <BlockAmendment>
                    <P1>
                      <Pnumber>234.</Pnumber>
                      <P1para><Text>This section applies to quoted companies.</Text></P1para>
                    </P1>
                  </BlockAmendment>
                </P1para>
              </P1>
            </Body>
          </Secondary>
        </Legislation>
        """
    )

    rows_by_label = {
        row.to_dict()["provision_label"]: row.to_dict()
        for row in rows
        if row.family == "si_application_clause_surface"
    }

    assert rows_by_label["234."]["status"] == "payload_carried"
    assert rows_by_label["234."]["source_role"] == "amendment_payload_provision"


def test_si_source_semantics_parse_error_is_blocking_record() -> None:
    rows = scan_si_source_semantics_bytes("uksi/2022/34", b"<Legislation>")

    assert len(rows) == 1
    row = rows[0].to_dict()
    assert row["family"] == "si_source_parse_error"
    assert row["status"] == "blocking"
    assert row["rule_id"] == "uk_si_source_xml_parse_error"


def test_is_uk_si_document_id_classifies_si_like_ids() -> None:
    assert is_uk_si_document_id("uksi/2022/34") is True
    assert is_uk_si_document_id("ssi/2008/223") is True
    assert is_uk_si_document_id("ukpga/2022/1") is False
