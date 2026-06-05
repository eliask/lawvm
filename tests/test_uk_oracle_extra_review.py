from __future__ import annotations

import json

from scripts import uk_oracle_extra_review as review


_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _xml(body: str, commentaries: str = "") -> bytes:
    return f"""\
<Legislation xmlns="{_NS}">
  <Body>{body}</Body>
  <Commentaries>{commentaries}</Commentaries>
</Legislation>
""".encode()


def test_addition_with_commentary_is_source_chain_gap() -> None:
    row = review.review_target(
        statute_id="ukpga/1980/60",
        target="section-6-1A",
        base_xml=_xml('<P1 id="section-6"><Pnumber>6</Pnumber></P1>'),
        oracle_xml=_xml(
            """
            <P2 id="section-6-1A">
              <Pnumber><Addition ChangeId="d30p378" CommentaryRef="c739958">1A</Addition></Pnumber>
              <P2para><Text><Addition ChangeId="d30p378" CommentaryRef="c739958">Inserted text.</Addition></Text></P2para>
            </P2>
            """,
            '<Commentary id="c739958"><Para><Text>S. 6(1A) inserted by S.I. 1988/1984.</Text></Para></Commentary>',
        ),
    )

    assert row.review_status == "likely_source_chain_or_lowering_gap"
    assert row.oracle_markup_kinds == ("Addition",)
    assert row.oracle_commentaries == ("S. 6(1A) inserted by S.I. 1988/1984.",)
    assert row.agreement_residual["family"] == "source_footing_gap"


def test_markdown_surfaces_source_chain_leads_even_without_manual_candidates() -> None:
    row = review.review_target(
        statute_id="ukpga/1980/60",
        target="section-6-1A",
        base_xml=_xml('<P1 id="section-6"><Pnumber>6</Pnumber></P1>'),
        oracle_xml=_xml(
            """
            <P2 id="section-6-1A">
              <Pnumber><Addition ChangeId="d30p378" CommentaryRef="c739958">1A</Addition></Pnumber>
              <P2para><Text><Addition ChangeId="d30p378" CommentaryRef="c739958">Inserted text.</Addition></Text></P2para>
            </P2>
            """,
            '<Commentary id="c739958"><Para><Text>S. 6(1A) inserted by S.I. 1988/1984.</Text></Para></Commentary>',
        ),
    )

    markdown = review._emit_markdown([row])

    assert "likely_source_chain_or_lowering_gap: 1" in markdown
    assert "Source-chain/lowering leads to inspect:" in markdown
    assert "ukpga/1980/60 section-6-1A" in markdown
    assert "S. 6(1A) inserted by S.I. 1988/1984." in markdown
    assert "No sampled target currently survives" in markdown


def test_wrapper_target_is_topology_residual() -> None:
    row = review.review_target(
        statute_id="asp/2020/2",
        target="schedule-7-paragraph-wrapper1n1",
        base_xml=_xml(""),
        oracle_xml=_xml(
            '<P1 id="schedule-7-paragraph-wrapper1n1"><Pnumber>1</Pnumber></P1>'
        ),
    )

    assert row.review_status == "likely_topology_wrapper_residual"
    assert row.agreement_residual["family"] == "topology_granularity_mismatch"
    assert row.agreement_residual["missing_proofs"] == ["topology_granularity_review"]


def test_annotation_target_is_compare_projection_artifact() -> None:
    row = review.review_target(
        statute_id="eur/2020/1231",
        target="annex-i-part-ii-division-1-division-1.2-annotation-6",
        base_xml=_xml(""),
        oracle_xml=_xml(
            '<P1 id="annex-i-part-ii-division-1-division-1.2-annotation-6">'
            "<Text>1. Open air.</Text>"
            "</P1>"
        ),
    )

    assert row.review_status == "likely_annotation_projection_residual"
    assert row.agreement_residual["family"] == "non_commensurable_surface"
    assert row.agreement_residual["missing_proofs"] == ["compare_projection_review"]


def test_compacted_range_target_is_compare_projection_artifact() -> None:
    row = review.review_target(
        statute_id="ukpga/1958/55",
        target="section-4753",
        base_xml=_xml(""),
        oracle_xml=_xml('<P1 id="section-4753"><Pnumber>47–53</Pnumber></P1>'),
    )

    assert row.review_status == "likely_range_or_legacy_label_residual"
    assert row.agreement_residual["family"] == "non_commensurable_surface"


def test_number_only_section_target_is_legacy_label_residual() -> None:
    row = review.review_target(
        statute_id="ukpga/1860/124",
        target="section-42",
        base_xml=_xml('<P1 id="section-XLII"><Pnumber>XLII</Pnumber></P1>'),
        oracle_xml=_xml('<P1 id="section-42"><Pnumber>42</Pnumber><P1para><Text/></P1para></P1>'),
    )

    assert row.review_status == "likely_range_or_legacy_label_residual"
    assert row.agreement_residual["family"] == "non_commensurable_surface"


def test_existing_section_with_number_only_current_text_is_projection_residual() -> None:
    row = review.review_target(
        statute_id="ukpga/1920/50",
        target="section-1",
        base_xml=_xml(
            '<P1 id="section-1"><Pnumber>1</Pnumber><P1para><Text>Substantive enacted text.</Text></P1para></P1>'
        ),
        oracle_xml=_xml('<P1 id="section-1"><Pnumber>1</Pnumber><P1para><Text/></P1para></P1>'),
    )

    assert row.review_status == "likely_number_only_placeholder_residual"
    assert row.base_target_present is True
    assert row.agreement_residual["family"] == "non_commensurable_surface"
    assert row.agreement_residual["missing_proofs"] == ["compare_projection_review"]


def test_base_text_materialization_gap_is_compare_projection_artifact() -> None:
    row = review.review_target(
        statute_id="ukpga/1983/23",
        target="section-11-4-d",
        base_xml=_xml(
            """
            <P2 id="section-11-4">
              <P2para><P3 id="section-11-4-c"><P3para><Text>this section; (d) Schedule 2;</Text></P3para></P3></P2para>
            </P2>
            """
        ),
        oracle_xml=_xml(
            '<P3 id="section-11-4-d"><Pnumber>d</Pnumber><P3para><Text>Schedule 2;</Text></P3para></P3>'
        ),
    )

    assert row.review_status == "likely_base_text_materialization_gap"
    assert row.base_text_witness_present is True
    assert row.agreement_residual["family"] == "non_commensurable_surface"
    assert row.agreement_residual["detail"]["base_text_witness_present"] is True


def test_arabic_current_section_can_use_roman_enacted_id_witness() -> None:
    row = review.review_target(
        statute_id="ukpga/1825/120",
        target="section-44",
        base_xml=_xml(
            """
            <P1 id="section-XLIV">
              <Pnumber>XLIV</Pnumber>
              <P1para><Text>That when any Judgment shall be pronounced by an Inferior Court.</Text></P1para>
            </P1>
            """
        ),
        oracle_xml=_xml(
            """
            <P1 id="section-44">
              <Pnumber>44</Pnumber>
              <P1para><Text>When any judgment shall be pronounced by an inferior court.</Text></P1para>
            </P1>
            """
        ),
    )

    assert row.review_status == "likely_base_text_materialization_gap"
    assert row.base_text_witness_present is True
    assert row.agreement_residual["family"] == "non_commensurable_surface"


def test_heading_materialization_gap_can_use_short_heading_witness() -> None:
    row = review.review_target(
        statute_id="ukpga/1967/45",
        target="schedule-1-part-3",
        base_xml=_xml(
            """
            <Chapter id="schedule-1-chapter-III">
              <Number>CHAPTER III</Number>
              <Title>OBLIGATIONS OF THE SELLER</Title>
              <P><Text>Section I Delivery of the Goods.</Text></P>
            </Chapter>
            """
        ),
        oracle_xml=_xml(
            """
            <Part id="schedule-1-part-3">
              <Number>Chapter III.—Obligations of the seller</Number>
              <Chapter id="schedule-1-part-3-chapter-1"><Number>Article 18</Number></Chapter>
            </Part>
            """
        ),
    )

    assert row.review_status == "likely_base_text_materialization_gap"
    assert row.base_text_witness_present is True


def test_short_id_targets_are_reviewed_as_target_elements() -> None:
    row = review.review_target(
        statute_id="ukpga/1967/45",
        target="schedule-1-paragraph-1",
        base_xml=_xml(""),
        oracle_xml=_xml(
            """
            <P1 id="schedule-1-chapter-I-crossheading-1_paragraph-1" shortId="schedule-1-paragraph-1">
              <Pnumber>1</Pnumber>
              <P1para><Text>The present Law shall apply.</Text></P1para>
            </P1>
            """
        ),
    )

    assert row.oracle_target_present is True
    assert row.review_status == "manual_review_candidate"


def test_target_tuple_accepts_broad_report_sample_field_names() -> None:
    assert review._target_tuple({"oracle_only_eid_samples": ["section-1"]}) == (
        "section-1",
    )
    assert review._target_tuple({"replay_only_eid_samples": ["section-2"]}) == (
        "section-2",
    )


def test_repeal_commentary_is_display_convention() -> None:
    row = review.review_target(
        statute_id="ukpga/1986/2",
        target="section-4",
        base_xml=_xml('<P1 id="section-4"><Pnumber>4</Pnumber></P1>'),
        oracle_xml=_xml(
            '<P1 id="section-4"><Pnumber><CommentaryRef Ref="c1"/>4</Pnumber></P1>',
            '<Commentary id="c1"><Para><Text>S. 4 repealed by 1995 c. 21.</Text></Para></Commentary>',
        ),
    )

    assert row.review_status == "likely_repeal_display_convention"
    assert row.agreement_residual["family"] == "oracle_editorial_pathology"


def test_unmarked_oracle_extra_survives_manual_review() -> None:
    rows = [
        review.review_target(
            statute_id="ukpga/1900/1",
            target="section-9",
            base_xml=_xml('<P1 id="section-1"><Pnumber>1</Pnumber></P1>'),
            oracle_xml=_xml(
                '<P1 id="section-9"><Pnumber>9</Pnumber><P1para><Text>Unmarked text.</Text></P1para></P1>'
            ),
        )
    ]
    payload = json.loads(review._emit_json(rows))

    assert rows[0].review_status == "manual_review_candidate"
    assert rows[0].agreement_residual["status"] == "residual"
    assert payload["report_kind"] == "uk_oracle_extra_review"
    assert payload["summary"]["manual_review_candidate_count"] == 1
    assert payload["agreement_claims"] is True
    assert payload["replay_claims"] is False
