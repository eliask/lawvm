from __future__ import annotations

from lawvm.new_zealand.dependencies import extract_dependency_report
from lawvm.tools.cli import _build_parser


def test_extract_dependency_report_uses_reprint_and_history_surfaces() -> None:
    xml = b"""\
<act>
  <end>
    <reprint.notes>
      <reprint.amend><citation>Example Amendment Act 2025 (2025 No 54): section 78</citation></reprint.amend>
      <reprint.amend><citation>Second Example Act 2024 (2024 No 10): Part 2</citation></reprint.amend>
    </reprint.notes>
  </end>
  <body>
    <prov>
      <history-note>Section 1: amended, on 1 January 2025, by section 78 of the Example Amendment Act 2025 (2025 No 54).</history-note>
    </prov>
  </body>
</act>
"""

    report = extract_dependency_report(
        xml_bytes=xml,
        xml_locator="https://example.test/latest.xml",
        work_id="act_public_1957_87",
        version_id="act_public_1957_87_en_2026-04-05B",
    )

    assert report.reprint_amendment_count == 2
    assert report.history_note_count == 1
    assert [ref.work_id for ref in report.amending_works] == [
        "act_public_2025_54",
        "act_public_2024_10",
    ]
    assert report.amending_works[0].occurrence_count == 2
    assert report.amending_works[0].source == "reprint.amend"


def test_extract_dependency_report_records_unparsed_reprint_amend() -> None:
    xml = b"""\
<act>
  <end>
    <reprint.notes>
      <reprint.amend><citation>Unusual instrument without public Act number</citation></reprint.amend>
    </reprint.notes>
  </end>
</act>
"""

    report = extract_dependency_report(xml_bytes=xml, xml_locator="loc")

    assert report.amending_works == ()
    assert [diag["rule_id"] for diag in report.diagnostics] == ["nz_dependency_reprint_amend_unparsed"]


def test_extract_dependency_report_parses_history_note_act_title_after_connector() -> None:
    xml = b"""\
<act>
  <body>
    <prov>
      <history-note>Section 2(1) paragraph (gaa): editorial change made by the PCO, on 29 April 2026, under sections 86(1) and 87(l) of the Legislation Act 2019 (2019 No 58).</history-note>
    </prov>
  </body>
</act>
"""

    report = extract_dependency_report(xml_bytes=xml, xml_locator="loc")

    assert len(report.amending_works) == 1
    assert report.amending_works[0].work_id == "act_public_2019_58"
    assert report.amending_works[0].title == "Legislation Act"


def test_nz_corpus_deps_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "deps", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "deps"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
