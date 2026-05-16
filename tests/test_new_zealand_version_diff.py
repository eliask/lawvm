from __future__ import annotations

import json

from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.version_diff import (
    archived_xml_version_change_window,
    archived_xml_version_date_window,
    archived_xml_versions_for_work,
    diff_source_documents,
    previous_archived_xml_version_for_work,
)
from lawvm.tools.cli import _build_parser


class _FakeArchive:
    def __init__(self, rows: dict[str, bytes]) -> None:
        self.rows = rows

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self.rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        prefix = pattern[:-1] if pattern.endswith("%") else pattern
        return sorted(locator for locator in self.rows if locator.startswith(prefix))


def test_diff_source_documents_reports_added_removed_changed_nodes() -> None:
    before_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Old text</text></para></prov.body></prov>
    <prov id="S2"><label>2</label><heading>Removed</heading></prov>
  </body>
</act>
"""
    after_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>New text</text></para></prov.body></prov>
    <prov id="S3"><label>3</label><heading>Added</heading></prov>
  </body>
</act>
"""

    diff = diff_source_documents(
        parse_nz_source_document(before_xml, version_id="before"),
        parse_nz_source_document(after_xml, version_id="after"),
    )

    assert diff.summary()["change_counts"] == {"changed": 1, "removed": 1, "added": 1}
    assert ["/".join(change.path) for change in diff.changes] == ["prov:1", "prov:2", "prov:3"]


def test_diff_source_documents_preserves_duplicate_source_paths() -> None:
    before_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First duplicate</heading></prov>
    <prov id="S1A"><label>1</label><heading>Second duplicate</heading></prov>
  </body>
</act>
"""
    after_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First duplicate changed</heading></prov>
    <prov id="S1A"><label>1</label><heading>Second duplicate</heading></prov>
  </body>
</act>
"""

    diff = diff_source_documents(
        parse_nz_source_document(before_xml, version_id="before"),
        parse_nz_source_document(after_xml, version_id="after"),
    )

    assert diff.summary()["change_counts"] == {"changed": 1}
    assert diff.changes[0].path == ("prov:1", "source-duplicate:S1")


def test_archived_xml_versions_for_work_lists_xml_witnesses_newest_first() -> None:
    work_id = "act_public_1957_87"
    older = "act_public_1957_87_en_2024-01-01"
    latest = "act_public_1957_87_en_2026-04-05B"
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{older}/": json.dumps(
                {
                    "version_id": older,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{latest}/": json.dumps(
                {
                    "version_id": latest,
                    "formats": [
                        {
                            "format": "XML",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/latest.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05B.xml": b"<act />",
            "https://api.legislation.govt.nz/v0/versions/act_public_1957_87_en_2025-01-01/": json.dumps(
                {
                    "version_id": "act_public_1957_87_en_2025-01-01",
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2025-01-01.xml",
                        }
                    ],
                }
            ).encode(),
        }
    )

    versions = archived_xml_versions_for_work(archive, work_id)

    assert [version.version_id for version in versions] == [latest, older]
    assert versions[0].version_date == "2026-04-05B"
    assert versions[0].xml_locator.endswith("/2026-04-05B.xml")


def test_previous_archived_xml_version_for_work_uses_inventory_order() -> None:
    work_id = "act_public_1957_87"
    older = "act_public_1957_87_en_2024-01-01"
    latest = "act_public_1957_87_en_2026-04-05"
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{older}/": json.dumps(
                {
                    "version_id": older,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{latest}/": json.dumps(
                {
                    "version_id": latest,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05.xml": b"<act />",
        }
    )

    previous = previous_archived_xml_version_for_work(archive, work_id=work_id, after_version_id=latest)

    assert previous is not None
    assert previous.version_id == older


def test_archived_xml_version_date_window_is_source_version_bracket_only() -> None:
    work_id = "act_public_1957_87"
    first = "act_public_1957_87_en_2024-01-01"
    second = "act_public_1957_87_en_2025-04-05B"
    third = "act_public_1957_87_en_2026-04-05"
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{first}/": json.dumps(
                {
                    "version_id": first,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2024-01-01.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{second}/": json.dumps(
                {
                    "version_id": second,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2025-04-05B.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2025-04-05B.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{third}/": json.dumps(
                {
                    "version_id": third,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05.xml": b"<act />",
        }
    )

    window = archived_xml_version_date_window(archive, work_id=work_id, version_date="2025-06-01")

    assert window.truth_claim == "source_version_date_window_not_effective_date"
    assert window.on_or_before is not None
    assert window.on_or_before.version_id == second
    assert window.on_or_after is not None
    assert window.on_or_after.version_id == third


def test_archived_xml_version_change_window_uses_strict_before_witness() -> None:
    work_id = "act_public_1957_87"
    before = "act_public_1957_87_en_2025-04-05"
    exact = "act_public_1957_87_en_2025-06-01"
    after = "act_public_1957_87_en_2025-08-27"
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{before}/": json.dumps(
                {
                    "version_id": before,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2025-04-05.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2025-04-05.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{exact}/": json.dumps(
                {
                    "version_id": exact,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2025-06-01.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2025-06-01.xml": b"<act />",
            f"https://api.legislation.govt.nz/v0/versions/{after}/": json.dumps(
                {
                    "version_id": after,
                    "formats": [
                        {
                            "type": "xml",
                            "url": "https://www.legislation.govt.nz/act/public/1957/87/en/2025-08-27.xml",
                        }
                    ],
                }
            ).encode(),
            "https://www.legislation.govt.nz/act/public/1957/87/en/2025-08-27.xml": b"<act />",
        }
    )

    window = archived_xml_version_change_window(archive, work_id=work_id, version_date="2025-06-01")

    assert window.truth_claim == "source_change_window_not_effective_date"
    assert window.before is not None
    assert window.before.version_id == before
    assert window.on_or_after is not None
    assert window.on_or_after.version_id == exact


def test_nz_version_diff_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "version-diff", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "version-diff"
    assert args.work_id == "act_public_1957_87"
    assert args.before_version_id == ""
    assert args.after_version_id == ""
    assert args.list_versions is False
    assert args.version_date == ""
    assert args.change_window is False


def test_nz_version_diff_cli_parse_list_versions() -> None:
    parser = _build_parser()

    args = parser.parse_args(
        [
            "nz-corpus",
            "version-diff",
            "--work-id",
            "act_public_1957_87",
            "--list-versions",
            "--version-date",
            "2025-06-01",
            "--change-window",
        ]
    )

    assert args.list_versions is True
    assert args.version_date == "2025-06-01"
    assert args.change_window is True
