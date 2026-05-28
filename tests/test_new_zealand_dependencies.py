from __future__ import annotations

import json
from argparse import Namespace

from lawvm.new_zealand import dependencies as nz_dependencies
from lawvm.new_zealand.dependencies import extract_dependency_report, latest_xml_locator_selection_for_work
from lawvm.tools.cli import _build_parser


class _MemoryArchive:
    def __init__(self, rows: dict[str, bytes]):
        self._rows = rows

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self._rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        prefix = pattern[:-1] if pattern.endswith("%") else pattern
        return [locator for locator in self._rows if locator.startswith(prefix)]

    def close(self) -> None:
        return None


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


def test_latest_xml_locator_selection_records_rejected_newer_source_lanes() -> None:
    work_id = "act_public_2020_1"
    prefix = f"https://api.legislation.govt.nz/v0/versions/{work_id}_en_"
    newest = f"{prefix}2026-01-01"
    middle = f"{prefix}2025-01-01"
    older = f"{prefix}2024-01-01"
    older_xml = "https://www.legislation.govt.nz/act/public/2020/1/en/2024-01-01.xml"
    archive = _MemoryArchive(
        {
            newest: b"{not json",
            middle: json.dumps(
                {
                    "version_id": "act_public_2020_1_en_2025-01-01",
                    "formats": [{"type": "xml", "url": "https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}],
                }
            ).encode("utf-8"),
            older: json.dumps(
                {
                    "version_id": "act_public_2020_1_en_2024-01-01",
                    "formats": [{"type": "xml", "url": "https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}],
                }
            ).encode("utf-8"),
            older_xml: b"<act/>",
        }
    )

    selection = latest_xml_locator_selection_for_work(archive, work_id)

    assert selection.version_id == "act_public_2020_1_en_2024-01-01"
    assert selection.xml_locator == older_xml
    assert [diag["detail"]["reason_code"] for diag in selection.diagnostics] == [
        "detail_json_invalid",
        "xml_not_archived",
    ]
    assert [diag["source_lane_selection"]["selected_source_lane"] for diag in selection.diagnostics] == [
        "no_source_lane_selected_candidate_rejected",
        "no_source_lane_selected_candidate_rejected",
    ]
    assert [diag["source_lane_selection"]["source_lane_attempts"][0]["status"] for diag in selection.diagnostics] == [
        "detail_json_invalid",
        "xml_not_archived",
    ]
    assert selection.diagnostics[1]["source_lane_selection"]["source_lane_attempts"][0]["xml_locator"] == (
        "https://www.legislation.govt.nz/act/public/2020/1/en/2025-01-01.xml"
    )
    assert all(diag["rule_id"] == "nz_latest_xml_locator_candidate_rejected" for diag in selection.diagnostics)
    assert all(diag["strict_disposition"] == "block" for diag in selection.diagnostics)


def test_latest_xml_locator_selection_emits_no_diagnostics_for_clean_latest_candidate() -> None:
    work_id = "act_public_2020_1"
    version = f"https://api.legislation.govt.nz/v0/versions/{work_id}_en_2026-01-01"
    xml_locator = "https://www.legislation.govt.nz/act/public/2020/1/en/2026-01-01.xml"
    archive = _MemoryArchive(
        {
            version: json.dumps(
                {
                    "version_id": "act_public_2020_1_en_2026-01-01",
                    "formats": [{"type": "xml", "url": "https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}],
                }
            ).encode("utf-8"),
            xml_locator: b"<act/>",
        }
    )

    selection = latest_xml_locator_selection_for_work(archive, work_id)

    assert selection.version_id == "act_public_2020_1_en_2026-01-01"
    assert selection.xml_locator == xml_locator
    assert selection.diagnostics == ()


def test_nz_corpus_deps_cli_threads_latest_locator_selection_diagnostics(monkeypatch, capsys) -> None:
    work_id = "act_public_2020_1"
    prefix = f"https://api.legislation.govt.nz/v0/versions/{work_id}_en_"
    newest = f"{prefix}2026-01-01"
    older = f"{prefix}2025-01-01"
    older_xml = "https://www.legislation.govt.nz/act/public/2020/1/en/2025-01-01.xml"
    archive = _MemoryArchive(
        {
            newest: b"{not json",
            older: json.dumps(
                {
                    "version_id": "act_public_2020_1_en_2025-01-01",
                    "formats": [{"type": "xml", "url": "https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}],
                }
            ).encode("utf-8"),
            older_xml: b"<act/>",
        }
    )
    monkeypatch.setattr(nz_dependencies, "open_farchive", lambda _path: archive)

    nz_dependencies.main(
        Namespace(
            db="unused.farchive",
            xml_locator="",
            version_id="",
            work_id=work_id,
            output_json="",
            json=True,
            limit=40,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["xml_locator"] == older_xml
    assert [diag["detail"]["reason_code"] for diag in payload["diagnostics"]] == ["detail_json_invalid"]


def test_nz_corpus_deps_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "deps", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "deps"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
