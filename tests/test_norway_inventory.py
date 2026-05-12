from __future__ import annotations

import io
import json
import tarfile
from typing import Any, cast

from lawvm.norway.index import NOAmendmentIndex, NOAmendmentIndexEntry
from lawvm.norway.index import build_no_amendment_index, save_no_amendment_index
from lawvm.norway.inventory import build_no_inventory, build_no_missing_base_report
from lawvm.norway.sources import ingest_no_public_archives


_BASE_XML = """<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <head><title>Testlov om data</title></head>
  <body>
    <main class="documentBody" data-lovdata-URL="NL/lov/2025-01-01-1">
      <article class="legalArticle" data-name="§1">
        <h3 class="legalArticleHeader">§ 1. Formaal</h3>
        <article class="legalP">Loven gjelder testdata.</article>
      </article>
    </main>
  </body>
</html>
""".encode("utf-8")


def _amendment_xml(date_in_force: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html lang="nb">
  <body>
    <dd class="dateInForce">{date_in_force}</dd>
    <article class="document-change" data-document="lov/2025-01-01-1/§1">
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


def _write_archive(archive_path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_build_no_inventory_summarizes_replayability(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
            ("lti/2025/nl-20250303-006.xml", _amendment_xml("Kongen bestemmer")),
        ],
    )

    inventory = build_no_inventory(tmp_path).to_dict()

    assert inventory["current_laws"] == 1
    assert inventory["amendment_documents"] == 2
    assert inventory["amendment_documents_by_status"] == {"dated": 1, "contingent": 1}
    assert inventory["current_laws_with_amendments"] == 1
    assert inventory["current_laws_blocked_contingent"] == 1
    assert inventory["top_executable_blocked_current_laws"] == [
        {"base_id": "no/lov/2025-01-01-1", "amendments": 2}
    ]
    assert inventory["current_laws_fully_replayable"] == 0


def test_build_no_inventory_accepts_prebuilt_index(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )
    index = build_no_amendment_index(tmp_path)
    index_path = tmp_path / "no_index.json"
    save_no_amendment_index(index, index_path)

    inventory = build_no_inventory(tmp_path, index_path=index_path).to_dict()

    assert inventory["amendment_documents_by_status"] == {"dated": 1}
    assert inventory["current_laws_fully_replayable"] == 1
    assert inventory["current_laws_with_amendments_fully_replayable_executable"] == 1


def test_build_no_inventory_preserves_current_law_source_diagnostics(tmp_path, monkeypatch) -> None:
    def fake_current_law_ids(_data_dir, *, diagnostics_out=None):
        if diagnostics_out is not None:
            diagnostics_out.append(
                {
                    "rule_id": "no_current_law_id_parse_marker_fallback_used",
                    "family": "source_pathology",
                    "phase": "parse",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
        return {"no/lov/2025-01-01-1"}

    monkeypatch.setattr("lawvm.norway.inventory.load_no_current_law_ids", fake_current_law_ids)
    monkeypatch.setattr("lawvm.norway.inventory.load_available_lti_law_ids", lambda _data_dir: set())

    inventory = build_no_inventory(tmp_path, index=NOAmendmentIndex(data_dir=str(tmp_path))).to_dict()

    assert inventory["current_law_source_diagnostic_count"] == 1
    assert inventory["current_law_source_diagnostic_rule_counts"] == {
        "no_current_law_id_parse_marker_fallback_used": 1
    }
    assert inventory["current_law_source_diagnostics"][0]["strict_disposition"] == "block"


def test_build_no_inventory_records_current_law_id_artifact_fallback(tmp_path, monkeypatch) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    monkeypatch.setattr("lawvm.norway.inventory.load_no_current_law_ids", lambda _data_dir, *, diagnostics_out=None: set())
    monkeypatch.setattr("lawvm.norway.inventory.load_available_lti_law_ids", lambda _data_dir: set())

    inventory = build_no_inventory(tmp_path, index=NOAmendmentIndex(data_dir=str(tmp_path))).to_dict()

    assert inventory["current_laws"] == 1
    assert inventory["current_law_source_diagnostic_rule_counts"] == {
        "no_inventory_current_law_id_artifact_fallback_used": 1
    }
    diagnostic = inventory["current_law_source_diagnostics"][0]
    assert diagnostic["phase"] == "acquisition"
    assert diagnostic["family"] == "source_pathology"
    assert diagnostic["fallback_current_law_count"] == 1
    assert diagnostic["strict_disposition"] == "block"


def test_build_no_inventory_accepts_farchive_source_path(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10")),
        ],
    )
    db_path = tmp_path / "norway.farchive"
    ingest_no_public_archives(tmp_path, db_path)

    index = build_no_amendment_index(db_path)
    inventory = build_no_inventory(db_path, index=index).to_dict()

    assert index.source_kind == "farchive"
    assert inventory["current_laws"] == 1
    assert inventory["amendment_documents_by_status"] == {"dated": 1}
    assert inventory["current_laws_fully_replayable"] == 1
    assert inventory["current_laws_with_amendments_fully_replayable_executable"] == 1


def test_ingest_no_public_archives_reports_unmapped_xml_members(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/unexpected-current.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/unexpected-lovtidend.xml", b"<html/>")],
    )
    db_path = tmp_path / "norway.farchive"

    report = ingest_no_public_archives(tmp_path, db_path)

    assert report["current_locators_stored"] == 0
    assert report["original_locators_stored"] == 0
    assert report["amendment_locators_stored"] == 0
    assert report["skipped_unmapped"] == 2
    assert report["skipped_unmapped_entries"] == [
        {
            "rule_id": "no_ingest_unmapped_xml_member",
            "phase": "acquisition",
            "family": "source_pathology",
            "reason": "Norway Lovdata XML member filename could not be mapped to a legal source id",
            "kind": "current",
            "locator": "",
            "logical_id": "",
            "source_name": "gjeldende-lover.tar.bz2",
            "member_name": "nl/unexpected-current.xml",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
        {
            "rule_id": "no_ingest_unmapped_xml_member",
            "phase": "acquisition",
            "family": "source_pathology",
            "reason": "Norway Lovdata XML member filename could not be mapped to a legal source id",
            "kind": "lovtidend",
            "locator": "",
            "logical_id": "",
            "source_name": "lovtidend-avd1-2025.tar.bz2",
            "member_name": "lti/2025/unexpected-lovtidend.xml",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    ]


def test_ingest_no_public_archives_reports_duplicate_logical_locators(tmp_path) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-02-10"))],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025-2026.tar.bz2",
        [("lti/2025/nl-20250202-005.xml", _amendment_xml("2025-03-15"))],
    )
    db_path = tmp_path / "norway.farchive"

    report = ingest_no_public_archives(tmp_path, db_path)

    assert report["original_locators_stored"] == 1
    assert report["amendment_locators_stored"] == 1
    assert report["duplicate_locator_count"] == 2
    duplicate_entries = cast(list[dict[str, Any]], report["duplicate_locator_entries"])
    amendment_entries = [
        entry
        for entry in duplicate_entries
        if entry["kind"] == "amendment"
    ]
    assert len(amendment_entries) == 1
    entry = amendment_entries[0]
    assert entry["rule_id"] == "no_acquisition_duplicate_logical_locator"
    assert entry["phase"] == "acquisition"
    assert entry["family"] == "source_pathology"
    assert entry["logical_id"] == "no/lovtid/2025-02-02-5"
    assert entry["locator"] == "no://lovtid/2025-02-02-5/amendment.xml"
    assert entry["identical_payloads"] is False
    assert entry["blocking"] is True
    assert entry["strict_disposition"] == "block"
    assert entry["quirks_disposition"] == "block"


def test_build_no_inventory_accepts_commencement_override(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _BASE_XML)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml("Kongen bestemmer")),
        ],
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps(
            {"no/lovtid/2025-02-02-5": {"effective_date": "2025-02-10", "note": "manual"}}
        ),
        encoding="utf-8",
    )

    inventory = build_no_inventory(tmp_path, commencement_path=commencement_path).to_dict()

    assert inventory["amendment_documents_by_status"] == {"override": 1}
    assert inventory["current_laws_fully_replayable"] == 1
    assert inventory["current_laws_with_amendments_fully_replayable_executable"] == 1
    assert inventory["current_laws_blocked_contingent"] == 0


def test_build_no_inventory_tracks_missing_local_base_source(tmp_path) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-19461213-021.xml", _BASE_XML)],
    )
    index = NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                title="A",
                base_ids=("no/lov/1946-12-13-21",),
                n_ops=1,
            )
        ],
    )

    inventory = build_no_inventory(tmp_path, index=index).to_dict()

    assert inventory["current_laws"] == 1
    assert inventory["current_laws_with_local_base_source"] == 0
    assert inventory["current_laws_without_local_base_source"] == 1
    assert inventory["current_laws_with_amendments_missing_base_source"] == 1
    assert inventory["current_laws_with_amendments_fully_replayable_executable"] == 0
    assert inventory["top_executable_blocked_current_laws"] == []
    assert inventory["top_missing_base_source_current_laws"] == [
        {"base_id": "no/lov/1946-12-13-21", "amendments": 1}
    ]


def test_build_no_missing_base_report_groups_laws(tmp_path) -> None:
    inventory = build_no_inventory(tmp_path, index=NOAmendmentIndex(
        data_dir=str(tmp_path),
        archive_names=[],
        entries=[
            NOAmendmentIndexEntry(
                source_id="no/lovtid/2025-02-02-5",
                archive="lovtidend-avd1-2025.tar.bz2",
                member_name="lti/2025/nl-20250202-005.xml",
                effective_status="dated",
                effective_date="2025-02-10",
                raw_date_in_force="2025-02-10",
                title="A",
                base_ids=("no/lov/1946-12-13-21",),
                n_ops=1,
            )
        ],
    ))
    inventory.current_law_ids = {"no/lov/1946-12-13-21"}
    inventory.current_law_ids_with_local_base_source = set()

    report = build_no_missing_base_report(
        inventory,
        current_law_titles={"no/lov/1946-12-13-21": "Old law"},
    )

    assert report["missing_base_source_law_count"] == 1
    assert report["laws"] == [
        {
            "base_id": "no/lov/1946-12-13-21",
            "title": "Old law",
            "amendments": 1,
            "source_ids": ["no/lovtid/2025-02-02-5"],
        }
    ]
