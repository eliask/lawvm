from __future__ import annotations

from argparse import Namespace
import hashlib
from pathlib import Path

from lawvm.tools import cli
from lawvm.tools import corrigendum as corr_tools
from lawvm.tools import oracle_check
from lawvm.tools.classify_result import ClassifyResult


def test_cli_parser_accepts_corrigendum_manual_template() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["corrigendum", "manual-template", "991/2012", "--json"])

    assert args.command == "corrigendum"
    assert args.corrigendum_command == "manual-template"
    assert args.amendment_id == "991/2012"
    assert args.json is True

    args = parser.parse_args(["corrigendum", "review", "1995/1552", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "review"
    assert args.statute_id == "1995/1552"
    assert args.json is True

    args = parser.parse_args(["corrigendum", "verify", "--amendment", "1246/2002"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "verify"
    assert args.amendment_id == "1246/2002"

    args = parser.parse_args(["corrigendum", "open-manual", "--limit", "5", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "open-manual"
    assert args.limit == 5
    assert args.json is True
    assert args.all is False

    args = parser.parse_args(["corrigendum", "provenance", "442/2016", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "provenance"
    assert args.amendment_id == "442/2016"
    assert args.json is True

    args = parser.parse_args(["corrigendum", "overview", "--limit", "7", "--live", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "overview"
    assert args.limit == 7
    assert args.live is True
    assert args.json is True

    args = parser.parse_args(["corrigendum", "sources", "--refresh", "--limit", "3", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "sources"
    assert args.refresh is True
    assert args.limit == 3
    assert args.json is True

    args = parser.parse_args(["corrigendum", "backfill-meta", "--update", "--json"])
    assert args.command == "corrigendum"
    assert args.corrigendum_command == "backfill-meta"
    assert args.update is True
    assert args.json is True


def test_corrigendum_manual_template_prints_yaml(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_manual_template_bundle",
        lambda amendment_id, db_path=None, include_all=False: {
            "amendment_id": amendment_id,
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "include_all": include_all,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "manual_entry_count": 0,
            "already_covered": False,
            "attachment_only_entry_count": 0,
            "entry_count": 1,
            "entries": [
                {
                    "amendment_id": amendment_id,
                    "wrong_text": "sekä 43 b ja 43 c §,",
                    "correct_text": "sekä väliaikaisesti 43 b ja 43 c §,",
                    "correction_type": "johtolause",
                    "notes": "source_pdf=sk20120991_1.pdf; current_verify=False",
                    "verified": "",
                }
            ],
        },
    )

    corr_tools._cmd_manual_template(
        Namespace(amendment_id="991/2012", db=None, all=False, json=False)
    )

    out = capsys.readouterr().out
    assert "# Manual corrigendum scaffold for 991/2012" in out
    assert 'amendment_id: 991/2012' in out
    assert 'wrong_text: sekä 43 b ja 43 c §,' in out


def test_corrigendum_manual_template_prints_no_rows_message(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_manual_template_bundle",
        lambda amendment_id, db_path=None, include_all=False: {
            "amendment_id": amendment_id,
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "include_all": include_all,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "manual_entry_count": 0,
            "already_covered": False,
            "attachment_only_entry_count": 0,
            "entry_count": 0,
            "entries": [],
        },
    )

    corr_tools._cmd_manual_template(
        Namespace(amendment_id="991/2012", db=None, all=False, json=False)
    )

    out = capsys.readouterr().out
    assert "No manual-template items for 991/2012" in out


def test_corrigendum_manual_template_notes_existing_manual_override(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_manual_template_bundle",
        lambda amendment_id, db_path=None, include_all=False: {
            "amendment_id": amendment_id,
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "include_all": include_all,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "manual_entry_count": 2,
            "already_covered": False,
            "attachment_only_entry_count": 0,
            "entry_count": 0,
            "entries": [],
        },
    )

    corr_tools._cmd_manual_template(
        Namespace(amendment_id="991/2012", db=None, all=False, json=False)
    )

    out = capsys.readouterr().out
    assert "# NOTE: 991/2012 already has 2 manual override entries" in out


def test_corrigendum_manual_template_notes_attachment_only_rows(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_manual_template_bundle",
        lambda amendment_id, db_path=None, include_all=False: {
            "amendment_id": amendment_id,
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "include_all": include_all,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "manual_entry_count": 0,
            "already_covered": False,
            "attachment_only_entry_count": 6,
            "entry_count": 0,
            "entries": [],
        },
    )

    corr_tools._cmd_manual_template(
        Namespace(amendment_id="577/2019", db=None, all=False, json=False)
    )

    out = capsys.readouterr().out
    assert "skipped 6 attachment-only items" in out


def test_corrigendum_manual_template_suppresses_db_rows_when_already_covered(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_manual_template_bundle",
        lambda amendment_id, db_path=None, include_all=False: {
            "amendment_id": amendment_id,
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "include_all": include_all,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "manual_entry_count": 2,
            "already_covered": True,
            "attachment_only_entry_count": 0,
            "entry_count": 3,
            "entries": [
                {"amendment_id": amendment_id, "wrong_text": "x", "correct_text": "y", "correction_type": "johtolause", "notes": "", "verified": ""}
            ],
        },
    )

    corr_tools._cmd_manual_template(
        Namespace(amendment_id="442/2016", db=None, all=False, json=False)
    )

    out = capsys.readouterr().out
    assert "already has 2 manual override entries" in out
    assert "manual override already covers this amendment" in out
    assert "# Manual corrigendum scaffold" not in out


def test_corrigendum_review_prints_grouped_amendment_evidence(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_review_bundle",
        lambda statute_id, mode="legal_pit", db_path=None: {
            "statute_id": statute_id,
            "mode": mode,
            "title": "Test statute",
            "overall_score": 0.93,
            "section_score": 0.98,
            "source_pathologies": [],
            "contingent_effective_sources": [],
            "amendments": [
                {
                    "amendment_id": "2019/577",
                    "db_amendment_id": "577/2019",
                    "blame_title": "Laki testisäädöksen muuttamisesta",
                    "sections": [
                        {"section": "section:6a", "diagnosis": "REPLAY_MISSING", "oracle_version": ""},
                    ],
                    "linked_sections": [
                        {
                            "section": "section:7",
                            "diagnosis": "REPLAY_EXTRA",
                            "oracle_version": "",
                            "why": "DESTRUCTIVE_SHAPE_LOSS_RISK 7 §",
                        }
                    ],
                    "corrigendum_db_rows": 6,
                    "corrigendum_no_match_rows": 2,
                    "corrigendum_verified_rows": 0,
                    "corrigendum_types": ["johtolause", "table"],
                    "corrigendum_pdfs": ["sk20190577_1.pdf", "sk20190577_2.pdf"],
                    "manual_override_count": 0,
                    "manual_template_entry_count": 2,
                    "relevance_kinds": ["blame", "source_pathology"],
                    "source_pathology_codes": ["DESTRUCTIVE_SHAPE_LOSS_RISK"],
                    "source_pathology_targets": ["7 §"],
                    "contingent_effective": False,
                }
            ],
            "unblamed_sections": [
                {"section": "liitteet", "diagnosis": "LIITE_DIFF", "oracle_version": ""}
            ],
        },
    )

    corr_tools._cmd_review(
        Namespace(statute_id="1995/1552", mode="legal_pit", db=None, json=False)
    )

    out = capsys.readouterr().out
    assert "Related amendments: 1" in out
    assert "2019/577  sections=1  linked=1  db_rows=6  no_match=2  manual=0  manual_open=2" in out
    assert "reasons: blame, source_pathology" in out
    assert "types: johtolause, table" in out
    assert "- section:6a: REPLAY_MISSING" in out
    assert "related current sections:" in out
    assert "- section:7: REPLAY_EXTRA via DESTRUCTIVE_SHAPE_LOSS_RISK 7 §" in out
    assert "Unblamed sections:" in out


def test_build_review_bundle_links_source_pathology_amendment_to_current_section(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        oracle_check,
        "_classify_statute",
        lambda statute_id, mode: ClassifyResult(
            sid=statute_id,
            title="Test statute",
            mode=mode,
            overall_score=0.91,
            section_score=0.95,
            section_results=[
                {
                    "section": "chapter:13/section:48",
                    "diagnosis": "REPLAY_EXTRA",
                    "blame_source": "",
                    "blame_title": "",
                    "oracle_version": "",
                }
            ],
            source_pathologies=[
                {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "message": "x",
                    "source_statute": "2004/1334",
                    "target_kind": "P",
                    "target_label": "48 §",
                }
            ],
            contingent_effective_sources=[],
        ),
    )

    bundle = corr_tools.build_review_bundle(
        "1995/1598",
        mode="legal_pit",
        db_path=tmp_path / "missing.db",
    )

    assert len(bundle["amendments"]) == 1
    amendment = bundle["amendments"][0]
    assert amendment["amendment_id"] == "2004/1334"
    assert amendment["relevance_kinds"] == ["source_pathology"]
    assert amendment["source_pathology_targets"] == ["48 §"]
    assert amendment["linked_sections"] == [
        {
            "section": "chapter:13/section:48",
            "diagnosis": "REPLAY_EXTRA",
            "oracle_version": "",
            "why": "DESTRUCTIVE_SHAPE_LOSS_RISK 48 §",
        }
    ]


def test_corrigendum_open_manual_prints_rows(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "list_open_manual_candidates",
        lambda db_path=None, limit=20, include_all=False: [
            {
                "amendment_id": "442/2016",
                "db_row_count": 4,
                "db_no_match_rows": 2,
                "open_manual_rows": 1,
                "attachment_only_rows": 0,
                "manual_entry_count": 2,
            }
        ],
    )

    corr_tools._cmd_open_manual(Namespace(db=None, limit=20, all=False, json=False))

    out = capsys.readouterr().out
    assert "AMENDMENT" in out
    assert "442/2016" in out
    assert "1" in out


def test_corrigendum_provenance_prints_summary(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_provenance_bundle",
        lambda amendment_id, db_path=None: {
            "amendment_id": amendment_id,
            "row_count": 2,
            "verified_count": 1,
            "manual_exact_count": 1,
            "attachment_only_count": 0,
            "open_manual_candidate_count": 0,
            "manual_entry_count": 2,
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "rows": [
                {
                    "correction_index": 0,
                    "correction_type": "johtolause",
                    "db_verified": 1,
                    "current_verified": True,
                    "status": "source_verified",
                    "source_pdf": "sk20160442_1.pdf",
                    "location_desc": "johtolause",
                },
                {
                    "correction_index": 1,
                    "correction_type": "johtolause",
                    "db_verified": 0,
                    "current_verified": False,
                    "status": "manual_override_exact",
                    "source_pdf": "sk20160442_1.pdf",
                    "location_desc": "johtolause",
                },
            ],
        },
    )

    corr_tools._cmd_provenance(
        Namespace(amendment_id="442/2016", db=None, json=False)
    )

    out = capsys.readouterr().out
    assert "Amendment    : 442/2016" in out
    assert "Items        : 2" in out
    assert "Manual exact : 1" in out
    assert "source_verified" in out
    assert "manual_override_exact" in out


def test_corrigendum_overview_prints_summary(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        corr_tools,
        "build_overview_bundle",
        lambda db_path=None, limit=10, live=False: {
            "mode": "stored",
            "records_path": "/tmp/corrigendum_official_fi.jsonl",
            "manual_yaml_path": "/tmp/corrigendum_manual.yaml",
            "official_item_count": 12,
            "amendment_count": 5,
            "source_pdf_count": 4,
            "missing_amendment_id_count": 0,
            "missing_date_published_count": 3,
            "source_date_status_counts": {"present": 2, "xml_ref_without_date": 2},
            "type_counts": {"johtolause": 5, "prose": 7},
            "status_counts": {
                "source_verified": 9,
                "manual_override_exact": 1,
                "amendment_manually_overridden": 1,
                "attachment_only": 0,
                "unresolved_unverified": 1,
                "unresolved_unreviewed": 0,
                "open_manual_candidate": 0,
            },
            "top_unresolved_amendments": [
                {
                    "amendment_id": "577/2019",
                    "unresolved_unverified": 1,
                    "unresolved_unreviewed": 0,
                    "item_count": 6,
                    "manual_entry_count": 0,
                }
            ],
            "top_open_manual_amendments": [],
            "top_attachment_only_amendments": [],
        },
    )

    corr_tools._cmd_overview(Namespace(db=None, limit=10, live=False, json=False))

    out = capsys.readouterr().out
    assert "Mode         : stored" in out
    assert "Official     : 12 items" in out
    assert "Source date  : present=2, xml_ref_without_date=2" in out
    assert "source_verified" in out
    assert "Top unresolved amendments:" in out
    assert "577/2019" in out


def test_build_provenance_bundle_classifies_row_statuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        corr_tools,
        "_load_patch_rows",
        lambda path=None: [
            {
                "stable_id": "sk20160442_1.pdf#0",
                "amendment_id": "442/2016",
                "lang": "fi",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "johtolause",
                "wrong_text": "alpha",
                "correct_text": "beta",
                "llm_confidence": "high",
                "verified_in_source": 1,
                "date_published": "31.5.2016",
            },
            {
                "stable_id": "sk20160442_1.pdf#1",
                "amendment_id": "442/2016",
                "lang": "fi",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "correction_index": 1,
                "correction_type": "johtolause",
                "location_desc": "johtolause",
                "wrong_text": "gamma",
                "correct_text": "delta",
                "llm_confidence": "medium",
                "verified_in_source": 0,
                "date_published": "31.5.2016",
            },
        ],
    )

    manual_yaml = tmp_path / "corrigendum_manual.yaml"
    manual_yaml.write_text(
        "- amendment_id: \"442/2016\"\n"
        "  wrong_text: \"gamma\"\n"
        "  correct_text: \"delta\"\n"
        "  correction_type: johtolause\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(corr_tools, "_MANUAL_YAML", manual_yaml)

    class _Store:
        def read_source(self, sid):
            assert sid == "2016/442"
            return b"<root/>"

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_verify_in_source_xml",
        lambda source_xml, wrong_text: True if wrong_text == "alpha" else False,
    )
    monkeypatch.setattr(
        corr_tools,
        "_looks_like_attachment_only_correction",
        lambda location_desc, correction_type, source_xml: False,
    )

    bundle = corr_tools.build_provenance_bundle("442/2016", db_path=tmp_path / "missing.jsonl")

    assert bundle["row_count"] == 2
    assert bundle["verified_count"] == 1
    assert bundle["manual_exact_count"] == 1
    assert bundle["open_manual_candidate_count"] == 0
    assert [row["status"] for row in bundle["rows"]] == [
        "source_verified",
        "manual_override_exact",
    ]


def test_build_overview_bundle_counts_statuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        corr_tools,
        "_load_patch_rows",
        lambda path=None: [
            {
                "stable_id": "a#0",
                "amendment_id": "442/2016",
                "lang": "fi",
                "source_pdf": "a.pdf",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "johtolause",
                "wrong_text": "alpha",
                "correct_text": "beta",
            },
            {
                "stable_id": "b#0",
                "amendment_id": "577/2019",
                "lang": "fi",
                "source_pdf": "b.pdf",
                "correction_index": 0,
                "correction_type": "table",
                "location_desc": "liite 1",
                "wrong_text": "gamma",
                "correct_text": "delta",
            },
        ],
    )
    monkeypatch.setattr(corr_tools, "_MANUAL_YAML", tmp_path / "corrigendum_manual.yaml")
    monkeypatch.setattr(
        corr_tools,
        "load_source_records",
        lambda path=None: [
            {"lang": "fi", "date_status": "present"},
            {"lang": "fi", "date_status": "xml_ref_without_date"},
        ],
    )
    (tmp_path / "corrigendum_manual.yaml").write_text(
        "- amendment_id: \"442/2016\"\n"
        "  wrong_text: \"alpha\"\n"
        "  correct_text: \"beta2\"\n"
        "  correction_type: johtolause\n",
        encoding="utf-8",
    )

    class _Store:
        def read_source(self, sid):
            return b'<root href="media/x.pdf" />'

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_verify_in_source_xml",
        lambda source_xml, wrong_text: True if wrong_text == "alpha" else False,
    )
    monkeypatch.setattr(
        corr_tools,
        "_looks_like_attachment_only_correction",
        lambda location_desc, correction_type, source_xml: correction_type == "table",
    )

    bundle = corr_tools.build_overview_bundle(db_path=tmp_path / "missing.jsonl", limit=5, live=True)

    assert bundle["official_item_count"] == 2
    assert bundle["missing_amendment_id_count"] == 0
    assert bundle["missing_date_published_count"] == 2
    assert bundle["source_date_status_counts"] == {"present": 1, "xml_ref_without_date": 1}
    assert bundle["status_counts"]["source_verified"] == 1
    assert bundle["status_counts"]["attachment_only"] == 1
    assert bundle["top_attachment_only_amendments"][0]["amendment_id"] == "577/2019"


def test_build_source_manifest_records_groups_items_per_pdf(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        corr_tools,
        "_load_patch_rows",
        lambda path=None: [
            {
                "stable_id": "a#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "date_published": "2016-06-01",
            },
            {
                "stable_id": "a#1",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 1,
                "date_published": "2016-06-01",
            },
            {
                "stable_id": "b#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2019/577/media/corrigenda/sk20190577_1.pdf",
                "statute_id": "2019/577",
                "amendment_id": "577/2019",
                "lang": "fi",
                "correction_index": 0,
                "date_published": "2019-07-01",
            },
        ],
    )

    class _Store:
        def oracle_path_index(self):
            return {}

        def read_oracle(self, sid: str):
            return None

        def read_corrigendum_media(self, sid: str, filename: str):
            return {
                ("2013/23", "sk20160442_1.pdf"): b"pdf-a",
                ("2019/577", "sk20190577_1.pdf"): b"pdf-b",
            }[(sid, filename)]

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())

    records = corr_tools.build_source_manifest_records(records_path=tmp_path / "ignored.jsonl")

    assert len(records) == 2
    assert records[0]["amendment_id"] == "442/2016"
    assert records[0]["correction_item_count"] == 2
    assert records[0]["pdf_name"] == "sk20160442_1.pdf"
    assert records[0]["date_status"] == "present"
    assert records[0]["sha256"] == hashlib.sha256(b"pdf-a").hexdigest()
    assert records[1]["amendment_id"] == "577/2019"


def test_build_source_manifest_records_backfills_missing_metadata_from_xml_refs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        corr_tools,
        "_load_patch_rows",
        lambda path=None: [
            {
                "stable_id": "x#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2009/1705/media/corrigenda/sk_20241079_1.pdf",
                "statute_id": "2009/1705",
                "amendment_id": None,
                "lang": "fi",
                "correction_index": 0,
                "date_published": None,
            }
        ],
    )

    class _Store:
        def read_oracle(self, sid: str):
            return None

        def read_corrigendum_media(self, sid: str, filename: str):
            assert sid == "2009/1705"
            assert filename == "sk_20241079_1.pdf"
            return b"pdf-x"

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_get_xml_corrigendum_refs",
        lambda cs, sid: [
            {
                "pdf_href": "corrigenda/sk_20241079_1.pdf",
                "date": "3.7.2025",
                "ref_text": "1079/2024",
            }
        ],
    )

    records = corr_tools.build_source_manifest_records(records_path=tmp_path / "ignored.jsonl")

    assert records == [
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2009/1705/media/corrigenda/sk_20241079_1.pdf",
            "pdf_name": "sk_20241079_1.pdf",
            "statute_id": "2009/1705",
            "amendment_id": "1079/2024",
            "lang": "fi",
            "date_published": "3.7.2025",
            "date_status": "present",
            "correction_item_count": 1,
            "sha256": hashlib.sha256(b"pdf-x").hexdigest(),
            "size_bytes": 5,
        }
    ]


def test_build_source_manifest_records_classifies_missing_date_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        corr_tools,
        "_load_patch_rows",
        lambda path=None: [
            {
                "stable_id": "x#0",
                "source_pdf": "akn/fi/act/statute-consolidated/1991/1512/media/corrigenda/sk19911512_1.pdf",
                "statute_id": "1991/1512",
                "amendment_id": "1512/1991",
                "lang": "fi",
                "correction_index": 0,
                "date_published": None,
            }
        ],
    )

    class _Store:
        def read_oracle(self, sid: str):
            return None

        def read_corrigendum_media(self, sid: str, filename: str):
            return b"pdf-y"

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_get_xml_corrigendum_refs",
        lambda cs, sid: [
            {
                "pdf_href": "corrigenda/sk19911512_1.pdf",
                "date": None,
                "ref_text": "1512/1991",
            }
        ],
    )

    records = corr_tools.build_source_manifest_records(records_path=tmp_path / "ignored.jsonl")

    assert records[0]["date_status"] == "xml_ref_without_date"


def test_corrigendum_sources_prints_summary(capsys, monkeypatch, tmp_path: Path) -> None:
    stored_path = tmp_path / "corrigendum_sources_fi.jsonl"
    monkeypatch.setattr(corr_tools, "_SOURCES_TEXT", stored_path)
    monkeypatch.setattr(
        corr_tools,
        "load_source_records",
        lambda path=None: [
            {
                "source_pdf": "akn/.../sk20160442_1.pdf",
                "pdf_name": "sk20160442_1.pdf",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "date_published": "2016-06-01",
                "date_status": "present",
                "correction_item_count": 2,
                "sha256": "abc123def456",
                "size_bytes": 321,
            }
        ],
    )

    corr_tools._cmd_sources(Namespace(db=None, refresh=False, limit=5, json=False))

    out = capsys.readouterr().out
    assert "Mode         : stored" in out
    assert "PDFs         : 1" in out
    assert "Items        : 2" in out
    assert "Date status  : present=1" in out
    assert "sk20160442_1.pdf" in out


def test_build_official_metadata_backfill_fills_missing_metadata(monkeypatch, tmp_path: Path) -> None:
    official_path = tmp_path / "corrigendum_official_fi.jsonl"
    monkeypatch.setattr(
        corr_tools,
        "load_official_records",
        lambda path=None: [
            {
                "stable_id": "x#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2009/1705/media/corrigenda/sk_20241079_1.pdf",
                "statute_id": "2009/1705",
                "amendment_id": None,
                "lang": "fi",
                "correction_index": 0,
                "date_published": None,
            }
        ],
    )

    class _Store:
        def read_oracle(self, sid: str):
            return b"<xml />"

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_get_xml_corrigendum_refs",
        lambda cs, sid: [
            {
                "pdf_href": "corrigenda/sk_20241079_1.pdf",
                "date": "3.7.2025",
                "ref_text": "1079/2024",
            }
        ],
    )

    bundle = corr_tools.build_official_metadata_backfill(records_path=official_path)

    assert bundle["changed_count"] == 1
    assert bundle["changed_items"][0]["after_amendment_id"] == "1079/2024"
    assert bundle["changed_items"][0]["after_date_published"] == "3.7.2025"
    assert bundle["records"][0]["amendment_id"] == "1079/2024"
    assert bundle["records"][0]["date_published"] == "3.7.2025"


def test_build_official_metadata_backfill_classifies_residual_missing_dates(monkeypatch, tmp_path: Path) -> None:
    official_path = tmp_path / "corrigendum_official_fi.jsonl"
    monkeypatch.setattr(
        corr_tools,
        "load_official_records",
        lambda path=None: [
            {
                "stable_id": "x#0",
                "source_pdf": "akn/fi/act/statute-consolidated/1991/1512/media/corrigenda/sk19911512_1.pdf",
                "statute_id": "1991/1512",
                "amendment_id": "1512/1991",
                "lang": "fi",
                "correction_index": 0,
                "date_published": None,
            }
        ],
    )

    class _Store:
        def read_oracle(self, sid: str):
            return b"<xml />"

    monkeypatch.setattr(corr_tools, "_make_corpus_store", lambda: _Store())
    monkeypatch.setattr(
        corr_tools,
        "_get_xml_corrigendum_refs",
        lambda cs, sid: [
            {
                "pdf_href": "corrigenda/sk19911512_1.pdf",
                "date": None,
                "ref_text": "1512/1991",
            }
        ],
    )

    bundle = corr_tools.build_official_metadata_backfill(records_path=official_path)

    assert bundle["changed_count"] == 0
    assert bundle["residual_missing_date_counts"] == {"xml_ref_without_date": 1}
    assert bundle["residual_missing_date_samples"]["xml_ref_without_date"] == [
        {
            "pdf_name": "sk19911512_1.pdf",
            "amendment_id": "1512/1991",
            "statute_id": "1991/1512",
        }
    ]


def test_corrigendum_verify_updates_adjudication_text_corpus(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    official_path = tmp_path / "corrigendum_official_fi.jsonl"
    adjudication_path = tmp_path / "corrigendum_adjudications_fi.jsonl"
    official_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(corr_tools, "_OFFICIAL_TEXT", official_path)
    monkeypatch.setattr(corr_tools, "_ADJUDICATIONS_TEXT", adjudication_path)
    monkeypatch.setattr(
        corr_tools,
        "load_official_records",
        lambda path=None: [
            {
                "stable_id": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf#0",
                "lang": "fi",
                "correction_type": "johtolause",
                "amendment_id": "442/2016",
                "wrong_text": "18 §:n 4 ja 5 momentti ja 31 § ja",
            }
        ],
    )
    monkeypatch.setattr(
        corr_tools,
        "load_adjudication_records",
        lambda path=None: [
            {
                "stable_id": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf#0",
                "verified_in_source": 0,
            }
        ],
    )
    monkeypatch.setattr(corr_tools, "_verify_in_source", lambda amendment_id, wrong_text: True)

    written: list[dict] = []

    def _capture(records: list[dict], path=None):
        written[:] = records
        return Path(path) if path is not None else adjudication_path

    monkeypatch.setattr(corr_tools, "write_adjudication_records", _capture)

    corr_tools._cmd_verify(Namespace(type="johtolause", amendment_id="442/2016"))

    out = capsys.readouterr().out
    assert "Verifying 1 johtolause corrections against the corpus store for 442/2016" in out
    assert "Updated: 1  Found in source: 1  Not found: 0  Skipped: 0" in out
    assert written == [
        {
            "stable_id": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf#0",
            "verified_in_source": 1,
        }
    ]
