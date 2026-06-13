from __future__ import annotations

from lawvm.finland.helpers import _normalize_source_part_num, _normalize_source_section_num
from lawvm.finland.source_xml_label_policy_audit import (
    audit_source_xml_label_policies,
    summarize_label_policy_rows,
)


def test_source_section_num_normalizer_preserves_leading_sign_labels() -> None:
    assert _normalize_source_section_num("§ 1.") == "1"


def test_source_section_num_normalizer_keeps_tail_stripping_policy() -> None:
    assert _normalize_source_section_num("12 § Soveltamisala") == "12"
    assert _normalize_source_section_num("4 §,") == "4"
    assert _normalize_source_section_num("24 §*") == "24"


def test_source_section_num_normalizer_preserves_suffix_split_by_sign() -> None:
    assert _normalize_source_section_num("23 § a") == "23a"


def test_source_part_num_normalizer_strips_osa_and_osasto_labels() -> None:
    assert _normalize_source_part_num("II osa") == "2"
    assert _normalize_source_part_num("II OSASTO.") == "2"


def test_source_xml_label_policy_audit_collapses_part_suffix_variants() -> None:
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
    assert part_rows == []


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
    assert policies["source_section_num"] == "12"
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
