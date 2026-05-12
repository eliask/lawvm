from __future__ import annotations

import io
import tarfile
from typing import Any, cast

from lawvm.norway.index import (
    build_no_amendment_index,
    load_no_amendment_index,
    save_no_amendment_index,
)
from lawvm.norway.sources import NOLocatedArtifact


def _amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1">
      <article class="change" data-change-part="lov/2025-01-01-1/§1">
        <article class="futureLegalArticle" data-name="§1">
          <span class="futureLegalArticleHeader">
            <span class="legalArticleValue">§ 1</span>.
            <span class="legalArticleTitle">Nytt krav</span>
          </span>
          <article class="legalP">Oppdatert paragraftekst.</article>
        </article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _non_operational_amendment_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <p>No document-change payload is present.</p>
  </body>
</html>
"""


def _unresolved_base_amendment_xml() -> bytes:
    return """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">2025-02-10</dd>
    <article class="document-change" data-document="not-a-lovdata-ref">
      <article class="change" data-change-part="lov/2025-01-01-1/§1">
        <article class="legalP">§ 1 skal lyde:</article>
      </article>
    </article>
  </body>
</html>
""".encode("utf-8")


def _write_archive(archive_path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_build_no_amendment_index_captures_member_and_status(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
            ("lti/2025/nl-20250303-006.xml", _amendment_xml("Kongen bestemmer")),
        ],
    )

    index = build_no_amendment_index(tmp_path)

    assert len(index.entries) == 2
    first = index.entries[0]
    assert first.archive == "lovtidend-avd1-2025.tar.bz2"
    assert first.member_name == "lti/2025/nl-20250202-005.xml"
    assert first.effective_status == "dated"
    assert first.effective_date == "2025-02-10"
    assert first.base_ids == ("no/lov/2025-01-01-1",)


def test_build_no_amendment_index_records_artifacts_without_change_ops(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _non_operational_amendment_xml())],
    )

    index = build_no_amendment_index(tmp_path)

    assert index.entries == []
    assert index.diagnostics == [
        {
            "rule_id": "no_amendment_index_no_change_ops",
            "family": "source_pathology",
            "phase": "extraction",
            "reason": "Norway amendment artifact did not yield document-change operations",
            "source_id": "no/lovtid/2025-02-02-5",
            "locator": "no://lovtid/2025-02-02-5/amendment.xml",
            "archive": "lovtidend-avd1-2025.tar.bz2",
            "member_name": "lti/2025/nl-20250202-005.xml",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]
    assert index.to_dict()["diagnostics"] == index.diagnostics


def test_build_no_amendment_index_forwards_parser_adjudications(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _unresolved_base_amendment_xml())],
    )

    index = build_no_amendment_index(tmp_path)

    assert index.entries == []
    assert [diagnostic["rule_id"] for diagnostic in index.diagnostics] == [
        "no_parse_document_change_base_unresolved",
        "no_amendment_index_no_change_ops",
    ]
    parser_diagnostic = index.diagnostics[0]
    assert parser_diagnostic["kind"] == "no_parse_document_change_base_unresolved"
    assert parser_diagnostic["family"] == "source_pathology"
    assert parser_diagnostic["phase"] == "parse"
    assert parser_diagnostic["source_id"] == "no/lovtid/2025-02-02-5"
    assert parser_diagnostic["locator"] == "no://lovtid/2025-02-02-5/amendment.xml"
    assert parser_diagnostic["archive"] == "lovtidend-avd1-2025.tar.bz2"
    assert parser_diagnostic["member_name"] == "lti/2025/nl-20250202-005.xml"
    assert parser_diagnostic["blocking"] is True
    assert parser_diagnostic["strict_disposition"] == "block"
    assert parser_diagnostic["quirks_disposition"] == "record"
    assert parser_diagnostic["detail"]["source_doc"] == "not-a-lovdata-ref"
    assert parser_diagnostic["detail"]["reason"] == "unmappable_data_document"


def test_build_no_amendment_index_records_unmapped_xml_members(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/unexpected-name.xml", _amendment_xml("2025-02-10"))],
    )

    index = build_no_amendment_index(tmp_path)

    assert index.entries == []
    assert index.diagnostics == [
        {
            "rule_id": "no_amendment_index_unmapped_lovtidend_xml_member",
            "family": "source_pathology",
            "phase": "acquisition",
            "reason": "Norway Lovtidend XML member filename could not be mapped to a law or amendment source id",
            "source_id": "",
            "locator": "",
            "archive": "lovtidend-avd1-2025.tar.bz2",
            "member_name": "lti/2025/unexpected-name.xml",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_build_no_amendment_index_records_unrecognized_amendment_lane(tmp_path, monkeypatch) -> None:
    artifact = NOLocatedArtifact(
        locator="no://unexpected/2025-02-02-5/amendment.xml",
        logical_id="no/lovtid/2025-02-02-5",
        source_name="synthetic.farchive",
        member_name="unexpected-member.xml",
        payload=_amendment_xml("2025-02-10"),
    )

    monkeypatch.setattr(
        "lawvm.norway.index.iter_no_amendment_artifacts",
        lambda _data_dir: iter((artifact,)),
    )

    index = build_no_amendment_index(tmp_path)

    assert index.entries == []
    assert index.diagnostics == [
        {
            "rule_id": "no_amendment_index_unrecognized_amendment_locator",
            "family": "source_pathology",
            "phase": "acquisition",
            "reason": "Norway amendment index skipped artifact whose member name and locator did not identify an amendment source lane",
            "source_id": "no/lovtid/2025-02-02-5",
            "locator": "no://unexpected/2025-02-02-5/amendment.xml",
            "archive": "synthetic.farchive",
            "member_name": "unexpected-member.xml",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_save_and_load_no_amendment_index_round_trips(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10"))],
    )
    index = build_no_amendment_index(tmp_path)
    index_path = tmp_path / "no_index.json"

    save_no_amendment_index(index, index_path)
    loaded = load_no_amendment_index(index_path)

    assert loaded.to_dict() == index.to_dict()


def test_no_amendment_index_staleness_report_detects_archive_change(tmp_path) -> None:
    archive_path = tmp_path / "lovtidend-avd1-2025.tar.bz2"
    _write_archive(
        archive_path,
        [("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10"))],
    )
    index = build_no_amendment_index(tmp_path)

    fresh = index.staleness_report(tmp_path)
    assert fresh["index_stale"] is False

    _write_archive(
        archive_path,
        [("lti/2025/nl-20250303-006.xml", _amendment_xml("2025-03-15"))],
    )
    stale = cast(dict[str, Any], index.staleness_report(tmp_path))

    assert stale["index_stale"] is True
    assert stale["stale_archives"][0]["archive"] == "lovtidend-avd1-2025.tar.bz2"
