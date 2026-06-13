from __future__ import annotations

from lawvm.finland.source_xml_label_policy_audit import (
    audit_source_xml_label_policies,
    summarize_label_policy_rows,
)


def test_source_xml_label_policy_audit_surfaces_part_suffix_divergence() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <part><num>I osasto</num>
            <chapter><num>1 luku</num>
              <section><num>1 \xc2\xa7</num><content><p>Text</p></content></section>
            </chapter>
          </part>
        </body>
      </act>
    </akomaNtoso>
    """

    rows = audit_source_xml_label_policies("2000/1", xml)

    part_rows = [row for row in rows if row.element_kind == "part"]
    assert len(part_rows) == 1
    policies = {item.policy: item.value for item in part_rows[0].policies}
    assert policies["norm_strip_osa"] == "1osasto"
    assert policies["norm_strip_osasto_osa"] == "1"
    assert policies["fi_label_postprocessor"] == "1"


def test_source_xml_label_policy_audit_surfaces_section_tail_divergence() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <section><num>12 \xc2\xa7 Soveltamisala</num><content><p>Text</p></content></section>
        </body>
      </act>
    </akomaNtoso>
    """

    rows = audit_source_xml_label_policies("2000/1", xml)

    section_rows = [row for row in rows if row.element_kind == "section"]
    assert len(section_rows) == 1
    policies = {item.policy: item.value for item in section_rows[0].policies}
    assert policies["section_strip_sign_suffix"] == "12"
    assert policies["fi_label_postprocessor"] == "12soveltamisala"


def test_source_xml_label_policy_summary_counts_divergence_by_kind() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <part><num>II osa</num>
            <chapter><num>1 luku</num>
              <section><num>1 \xc2\xa7</num><content><p>Text</p></content></section>
            </chapter>
          </part>
        </body>
      </act>
    </akomaNtoso>
    """

    rows = audit_source_xml_label_policies("2000/1", xml, include_agreeing=True)
    summary = summarize_label_policy_rows(rows)

    assert summary["rows"] == 3
    assert summary["divergent_rows"] == 0
    assert summary["by_kind"] == {"part": 1, "chapter": 1, "section": 1}
