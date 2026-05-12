from __future__ import annotations

import json
from argparse import Namespace as SimpleNamespace

from lawvm.sweden.fetch import (
    se_backfill_official_checkpoint_locator,
    se_backfill_official_completeness_locator,
    se_backfill_official_chunk_plan_locator,
    se_backfill_official_gap_report_locator,
    se_backfill_official_history_locator,
    se_backfill_official_status_locator,
)
from lawvm.tools.sweden import main as sweden_main


class _FakeArchiveContext:
    def __init__(self, stored: dict[str, bytes] | None = None) -> None:
        self.stored = dict(stored or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, locator: str, *, at: int | None = None) -> bytes | None:
        return self.stored.get(locator)

    def has(self, locator: str, *, max_age_hours: float = float("inf")) -> bool:
        return locator in self.stored

    def locators(self, pattern: str = "%") -> list[str]:
        return list(self.stored.keys())

    def store(self, locator: str, data: bytes, *, observed_at: int | None = None, storage_class: str | None = None, metadata: dict | None = None) -> str:
        self.stored[locator] = data
        return "fakehash"


def _write_json(tmp_path, payload: dict) -> str:
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_sweden_source_record_command_outputs_json(tmp_path, capsys) -> None:
    payload = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    json_path = _write_json(tmp_path, payload)

    sweden_main(SimpleNamespace(sweden_command="source-record", json_path=json_path, doc_html=None))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["sfs_id"] == "2025:399"
    assert data["act_type"] == "förordning"
    assert data["source_urls"]["official_sfs_doc_url"] == "https://svenskforfattningssamling.se/doc/2025399.html"


def test_sweden_parse_current_command_prints_summary(tmp_path, capsys) -> None:
    payload = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    json_path = _write_json(tmp_path, payload)

    sweden_main(SimpleNamespace(sweden_command="parse-current", json_path=json_path, format="summary"))
    out = capsys.readouterr().out

    assert "Statute: 2025:399" in out
    assert "body" in out
    assert "section 1" in out


def test_sweden_parse_current_command_outputs_json(tmp_path, capsys) -> None:
    payload = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    json_path = _write_json(tmp_path, payload)

    sweden_main(SimpleNamespace(sweden_command="parse-current", json_path=json_path, format="json"))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["statute_id"] == "2025:399"
    assert data["body"]["kind"] == "body"
    assert data["body"]["children"][0]["kind"] == "section"


def test_sweden_ingest_json_command_prints_archive_locators(tmp_path, capsys) -> None:
    payload = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    json_path = _write_json(tmp_path, payload)
    db_path = tmp_path / "sweden.db"

    sweden_main(SimpleNamespace(sweden_command="ingest-json", json_path=json_path, doc_html=None, db=str(db_path)))
    out = capsys.readouterr().out

    assert "SFS ID:             2025:399" in out
    assert "se://sfs/2025:399/rk.current.json" in out
    assert "se://sfs/2025:399/source_record.json" in out
    assert "se://sfs/2025:399/current.ir.json" in out


def test_sweden_show_archive_command_reports_archived_state(tmp_path, capsys) -> None:
    payload = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    json_path = _write_json(tmp_path, payload)
    db_path = tmp_path / "sweden.db"

    sweden_main(SimpleNamespace(sweden_command="ingest-json", json_path=json_path, doc_html=None, db=str(db_path)))
    sweden_main(SimpleNamespace(sweden_command="show-archive", sfs_id="2025:399", db=str(db_path), format="summary", show_text=False, raw_text=False))
    out = capsys.readouterr().out

    assert "RK current JSON:    yes" in out
    assert "Bundle present:     yes" in out
    assert "Source record:      yes" in out
    assert "Current IR:         yes" in out
    assert "Official act JSON:  no" in out
    assert "Official ops JSON:  no" in out


def test_sweden_ingest_scrape_json_command_reports_summary(tmp_path, capsys) -> None:
    scrape_path = tmp_path / "sweden_scraped_results.json"
    scrape_path.write_text(
        json.dumps(
            {
                "https://svenskforfattningssamling.se/doc/2026286.html": (
                    '<main><a href="../sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a></main>'
                )
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "sweden.db"

    sweden_main(SimpleNamespace(sweden_command="ingest-scrape-json", json_path=str(scrape_path), db=str(db_path)))
    out = capsys.readouterr().out

    assert "Entries:            1" in out
    assert "Imported:           1" in out
    assert "PDF links parsed:   1" in out


def test_sweden_fetch_current_command_prints_locator(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_current(sfs_id: str, archive_obj, *, max_age_hours: float = 24.0) -> bytes:
        payload = b'{"beteckning": "2025:399"}'
        archive_obj.stored["se://sfs/2025:399/rk.current.json"] = payload
        return payload

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_rk_current_json", fake_fetch_current)

    sweden_main(
        SimpleNamespace(
            sweden_command="fetch-current",
            sfs_id="2025:399",
            db=None,
            max_age_hours=None,
            show_json=False,
        )
    )
    out = capsys.readouterr().out

    assert "SFS ID:             2025:399" in out
    assert "RK current locator: se://sfs/2025:399/rk.current.json" in out


def test_sweden_hydrate_live_command_prints_archive_locators(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext(
        stored={"se://sfs/2025:399/official.cleaned.txt": b"Recovered PDF text"}
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_hydrate_live(
        sfs_id: str,
        archive_obj,
        *,
        pdf_url_override=None,
        current_max_age_hours: float = 24.0,
        official_max_age_hours: float = float("inf"),
        force_reextract: bool = False,
    ):
        from lawvm.sweden.fetch import SEOfficialArtifacts, SESourceBundle
        from lawvm.sweden.grafter import parse_se_source_record, parse_se_statute

        payload = {
            "beteckning": "2025:399",
            "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
            "ikraftDateTime": "2025-07-01T00:00:00",
            "ikraftOvergangsbestammelse": False,
            "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
            "forfattningstypNamn": "Förordning",
            "register": {"forarbeten": None},
            "fulltext": {
                "utfardadDateTime": "2025-05-22T00:00:00",
                "andringInford": None,
                "forfattningstext": "1 § Testbestämmelse.",
            },
            "publiceradDateTime": "2025-05-26T08:54:29.3888676",
            "andringsforfattningar": [],
        }
        return SESourceBundle(
            source_record=parse_se_source_record(payload),
            current_statute=parse_se_statute(payload),
            official_artifacts=SEOfficialArtifacts(
                sfs_id="2025:399",
                doc_url="https://svenskforfattningssamling.se/doc/2025399.html",
                doc_locator="se://sfs/2025:399/official.doc.html",
                pdf_url="https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf",
                pdf_locator="se://sfs/2025:399/official.pdf",
                pdf_text_url="se://sfs/2025:399/official.pdf.txt",
                pdf_cleaned_text_url="se://sfs/2025:399/official.cleaned.txt",
            ),
        )

    monkeypatch.setattr("lawvm.tools.sweden.hydrate_se_bundle_live", fake_hydrate_live)

    sweden_main(
        SimpleNamespace(
            sweden_command="hydrate-live",
            sfs_id="2025:399",
            db=None,
            current_max_age_hours=None,
            official_max_age_hours=None,
            pdf_url=None,
            force_reextract=False,
            show_text=False,
            raw_text=False,
        )
    )
    out = capsys.readouterr().out

    assert "SFS ID:             2025:399" in out
    assert "Source record loc:  se://sfs/2025:399/source_record.json" in out
    assert "Official PDF loc:   se://sfs/2025:399/official.pdf" in out
    assert "Official act loc:   se://sfs/2025:399/official.act.json" in out


def test_sweden_show_official_command_prints_summary(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext(
        stored={
            "se://sfs/2026:286/official.act.json": json.dumps(
                {
                    "sfs_id": "2026:286",
                    "title": "Förordning om ändring i förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område",
                    "act_type": "förordning",
                    "is_amending_act": True,
                    "amended_act_sfs_id": "2026:106",
                    "published_date": "2026-03-24",
                    "issued_date": "2026-03-19",
                    "affected_section_labels": ["2", "8", "11"],
                    "provisions": [{"label": "2", "text": "Uppgifter om målsägande får göras gemensamt tillgängliga."}],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        }
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="show-official",
            sfs_id="2026:286",
            db=None,
            format="summary",
            show_text=False,
        )
    )
    out = capsys.readouterr().out

    assert "SFS ID:             2026:286" in out
    assert "Amending act:       yes" in out
    assert "Affected sections:  2, 8, 11" in out


def test_sweden_compile_official_command_prints_compiled_ops(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.compile_se_official_ops_to_archive",
        lambda archive_obj, sfs_id: [
            {
                "sequence": 1,
                "action": "replace",
                "target": {"path": [["section", "2"]], "special": None},
            },
            {
                "sequence": 2,
                "action": "replace",
                "target": {"path": [["section", "8"]], "special": None},
            },
        ],
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="compile-official",
            sfs_id="2026:286",
            db=None,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "SFS ID:             2026:286" in out
    assert "Official ops loc:   se://sfs/2026:286/official.ops.json" in out
    assert "1. replace section:2" in out


def test_sweden_show_official_ops_command_prints_summary(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext(
        stored={
            "se://sfs/2026:286/official.ops.json": json.dumps(
                [
                    {
                        "sequence": 1,
                        "action": "replace",
                        "target": {"path": [["section", "2"]], "special": None},
                    }
                ]
            ).encode("utf-8")
        }
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="show-official-ops",
            sfs_id="2026:286",
            db=None,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Official ops loc:   se://sfs/2026:286/official.ops.json" in out
    assert "1. replace section:2" in out


def test_sweden_materialize_current_command_prints_summary(monkeypatch, capsys) -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2026:286",
            "forfattningstext": "2 § /Träder i kraft I:2026-04-15/\nNya lydelsen.",
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    archive = _FakeArchiveContext(
        stored={"se://sfs/2026:106/rk.current.json": json.dumps(payload, ensure_ascii=False).encode("utf-8")}
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="materialize-current",
            sfs_id="2026:106",
            db=None,
            as_of="2026-04-15",
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Statute: 2026:106" in out
    assert "As of:   2026-04-15" in out


def test_sweden_replay_check_command_reports_matches(monkeypatch, capsys) -> None:
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2026:286",
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-04-15/\nGamla lydelsen.\n\n"
                "2 § /Träder i kraft I:2026-04-15/\nNya lydelsen.\n\n"
                "8 § /Upphör att gälla U:2026-04-15/\nÄldre text.\n\n"
                "8 § /Träder i kraft I:2026-04-15/\nYngre text."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2 och 8 §§ förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2", "8"],
        "provisions": [
            {"label": "2", "text": "Nya lydelsen."},
            {"label": "8", "text": "Yngre text."},
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchiveContext(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="replay-check",
            sfs_id="2026:286",
            db=None,
            base_sfs_id=None,
            as_of=None,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Base SFS ID:        2026:106" in out
    assert "Matched sections:   2/2" in out
    assert "2 § MATCH" in out


def test_sweden_replay_check_command_prints_heading_and_appendix_rows(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.check_se_official_replay",
        lambda archive_obj, sfs_id, base_sfs_id=None, as_of=None: {
            "amending_sfs_id": sfs_id,
            "base_sfs_id": "2023:676",
            "effective_date": "2026-05-01",
            "pre_date": "2026-04-30",
            "match_count": 2,
            "target_count": 2,
            "rows": [
                {"target_kind": "heading", "section": "7a", "match": True, "classification": "exact"},
                {"target_kind": "appendix", "appendix": "3", "match": True, "classification": "exact"},
            ],
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="replay-check",
            sfs_id="2026:290",
            db=None,
            base_sfs_id=None,
            as_of=None,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "heading 7a § MATCH [exact]" in out
    assert "appendix 3 MATCH [exact]" in out


def test_sweden_replay_check_command_uses_table_canonicalization(monkeypatch, capsys) -> None:
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2026:286",
            "forfattningstext": (
                "11 § /Upphör att gälla U:2026-04-15/\nGammal tabell.\n\n"
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 11 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["11"],
        "provisions": [
            {
                "label": "11",
                "text": (
                    "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                    "Uppgift lämnas av\n\n"
                    "Uppgift lämnas om\n\n"
                    "1. Polismyndigheten\n\n"
                    "Beslut i nådeärenden."
                ),
            }
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchiveContext(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="replay-check",
            sfs_id="2026:286",
            db=None,
            base_sfs_id=None,
            as_of=None,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Matched sections:   1/1" in out
    assert "11 § MATCH [table_rows_match]" in out


def test_sweden_diagnose_replay_command_reports_contamination(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.analyze_se_official_replay_feasibility",
        lambda archive_obj, sfs_id, base_sfs_id=None, as_of=None: {
            "amending_sfs_id": sfs_id,
            "base_sfs_id": "2015:284",
            "effective_date": "2018-08-01",
            "pre_date": "2018-07-31",
            "replay_feasible": False,
            "self_reverse_feasible": False,
            "later_chain_reverse_feasible": False,
            "replay_ready": False,
            "recovery_strategy": "older_base_required",
            "op_count": 11,
            "contamination": [
                {"target_kind": "section", "label": "16", "issue": "preexisting_renumber_destination", "action": "renumber"},
                {"target_kind": "section", "label": "17", "issue": "preexisting_insert_target", "action": "insert"},
            ],
            "self_reverse_residual_contamination": [{"target_kind": "section", "label": "16"}],
            "later_chain_residual_contamination": [{"target_kind": "section", "label": "16"}],
            "replay_precondition_issues": [{"target_kind": "section", "label": "17", "issue": "missing_renumber_source", "action": "renumber"}],
            "later_chain_hints": [],
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.sweden.plan_se_older_base_rebuild",
        lambda archive_obj, sfs_id, base_sfs_id=None, as_of=None, fetch_missing=False, probe_sources=False: {
            "official_chain_ready": False,
            "rebuild_ready": False,
            "prior_amendment_count": 2,
            "compiled_count": 1,
            "missing_official_count": 1,
            "unsupported_count": 0,
            "base_seed": {
                "official_act_available": False,
                "official_base_ir_available": False,
                "pdf_available": False,
                "doc_available": False,
                "public_source_probe": {"doc_status": "cloudflare_blocked", "pdf_status": "not_found"},
            },
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="diagnose-replay",
            sfs_id="2018:1381",
            db=None,
            base_sfs_id=None,
            as_of=None,
            fetch_missing=False,
            probe_sources=False,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Amending SFS ID:    2018:1381" in out
    assert "Replay feasible:    no" in out
    assert "Self-reverse:       no" in out
    assert "Later reverse:      no" in out
    assert "Replay ready:       no" in out
    assert "Strategy:           older_base_required" in out
    assert "Compiled op count:  11" in out
    assert "section 16 [preexisting_renumber_destination] via renumber origin=unknown source=? reverse_patch=unknown" in out
    assert "section 17 [preexisting_insert_target] via insert origin=unknown source=? reverse_patch=unknown" in out
    assert "Residual after self-reverse: 1" in out
    assert "Residual after later reverse: 1" in out
    assert "Replay preconditions: 1" in out
    assert "section 17 [missing_renumber_source] via renumber" in out
    assert "Older-base chain:  chain=no  rebuild=no  prior=2  compiled=1  missing=1  unsupported=0" in out
    assert "Base seed source:  official_act=no  seed_ir=no  pdf=no  doc=no" in out
    assert "Base public probe: doc=cloudflare_blocked pdf=not_found" in out


def test_sweden_plan_older_base_command_prints_summary(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.plan_se_older_base_rebuild",
        lambda archive_obj, sfs_id, base_sfs_id=None, as_of=None, fetch_missing=False, probe_sources=False: {
            "amending_sfs_id": sfs_id,
            "base_sfs_id": "2015:284",
            "effective_date": "2018-08-01",
            "pre_date": "2018-07-31",
            "recovery_strategy": "older_base_required",
            "official_chain_ready": False,
            "rebuild_ready": False,
            "prior_amendment_count": 2,
            "compiled_count": 1,
            "missing_official_count": 1,
            "unsupported_count": 0,
            "invalid_count": 0,
            "base_seed": {"official_act_available": False, "official_base_ir_available": False, "pdf_available": False, "doc_available": False},
            "chain": [
                {"effective_date": "2016-03-01", "sfs_id": "2016:13", "ops_status": "compiled", "op_count": 1, "error": ""},
                {
                    "effective_date": "2018-03-01",
                    "sfs_id": "2018:11",
                    "ops_status": "missing_official_act",
                    "op_count": 0,
                    "error": "",
                    "public_source_probe": {"doc_status": "cloudflare_blocked", "pdf_status": "not_found"},
                },
            ],
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="plan-older-base",
            sfs_id="2018:1381",
            db=None,
            base_sfs_id=None,
            as_of=None,
            fetch_missing=False,
            probe_sources=False,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Amending SFS ID:    2018:1381" in out
    assert "Official chain:     blocked" in out
    assert "Rebuild ready:      no" in out
    assert "Prior amendments:   2" in out
    assert "Chain counts:       compiled=1 missing=1 unsupported=0 invalid=0" in out
    assert "Base seed source:   official_act=no seed_ir=no pdf=no doc=no" in out
    assert "2016-03-01  2016:13  compiled  ops=1" in out
    assert "2018-03-01  2018:11  missing_official_act  ops=0  source_doc=cloudflare_blocked source_pdf=not_found" in out


def test_sweden_probe_command_prints_summary(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", lambda sfs_id, archive_obj, force_reextract=False: object())
    monkeypatch.setattr(
        "lawvm.tools.sweden.load_se_official_act_from_archive",
        lambda archive_obj, sfs_id: {"amended_act_sfs_id": "2023:676"} if sfs_id == "2026:290" else {"amended_act_sfs_id": "2026:106"},
    )
    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_rk_current_json", lambda sfs_id, archive_obj: b"{}")
    monkeypatch.setattr(
        "lawvm.tools.sweden.compile_se_official_ops_to_archive",
        lambda archive_obj, sfs_id: [{"action": "replace"}] if sfs_id == "2026:286" else [{"action": "replace"}, {"action": "insert"}],
    )
    monkeypatch.setattr(
        "lawvm.tools.sweden.analyze_se_official_replay_feasibility",
        lambda archive_obj, sfs_id, as_of=None: {
            "effective_date": "2026-04-15" if sfs_id == "2026:286" else "2026-05-01",
            "replay_feasible": True,
            "self_reverse_feasible": True,
            "later_chain_reverse_feasible": True,
            "replay_ready": True,
            "recovery_strategy": "direct_replay",
            "contamination": [],
            "self_reverse_residual_contamination": [],
            "later_chain_residual_contamination": [],
            "replay_precondition_issues": [],
            "later_chain_hints": [],
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.sweden.check_se_official_replay",
        lambda archive_obj, sfs_id, as_of=None: {
            "match_count": 3 if sfs_id == "2026:286" else 4,
            "target_count": 3 if sfs_id == "2026:286" else 4,
            "rows": (
                [
                    {"classification": "editorial_attribution_only"},
                    {"classification": "table_rows_match"},
                ]
                if sfs_id == "2026:286"
                else [
                    {"classification": "editorial_attribution_only"},
                    {"classification": "exact"},
                    {"classification": "inline_numbering_only"},
                ]
            ),
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="probe",
            sfs_ids=["2026:286", "2026:290"],
            db=None,
            force_reextract=True,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "2026:286  OK  3/3  ops=1  base=2026:106  classes=editorial_attribution_only,table_rows_match" in out
    assert "2026:290  OK  4/4  ops=2  base=2023:676  classes=editorial_attribution_only,exact,inline_numbering_only" in out


def test_sweden_probe_command_prints_historical_blocked_summary(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", lambda sfs_id, archive_obj, force_reextract=False: object())
    monkeypatch.setattr(
        "lawvm.tools.sweden.load_se_official_act_from_archive",
        lambda archive_obj, sfs_id: {"amended_act_sfs_id": "2015:284"},
    )
    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_rk_current_json", lambda sfs_id, archive_obj: b"{}")
    monkeypatch.setattr(
        "lawvm.tools.sweden.compile_se_official_ops_to_archive",
        lambda archive_obj, sfs_id: [{"action": "renumber"}, {"action": "insert"}],
    )
    monkeypatch.setattr(
        "lawvm.tools.sweden.analyze_se_official_replay_feasibility",
        lambda archive_obj, sfs_id, as_of=None: {
            "effective_date": "2018-08-01",
            "replay_feasible": False,
            "self_reverse_feasible": False,
            "later_chain_reverse_feasible": False,
            "replay_ready": False,
            "recovery_strategy": "later_reverse_chain",
            "contamination": [
                {"reverse_patch_candidate": "yes"},
                {"reverse_patch_candidate": "yes"},
                {"reverse_patch_candidate": "no"},
            ],
            "self_reverse_residual_contamination": [{"reverse_patch_candidate": "no"}],
            "later_chain_residual_contamination": [{"reverse_patch_candidate": "no"}],
            "replay_precondition_issues": [{"issue": "missing_renumber_source"}],
            "later_chain_hints": [{"sfs_id": "2026:63", "official_act_available": False}],
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="probe",
            sfs_ids=["2018:1381"],
            db=None,
            force_reextract=True,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "2018:1381  HIST  ops=2  base=2015:284  eff=2018-08-01  reverse_patch=2/3  self_reverse=no  later_reverse=no  ready=no  strategy=later_reverse_chain  chain=2026:63(missing)" in out


def test_sweden_probe_base_command_uses_amendment_register(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    probe_calls: list[dict[str, object]] = []
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.fetch_se_rk_current_json",
        lambda sfs_id, archive_obj: json.dumps(
            {
                "beteckning": sfs_id,
                "rubrik": f"Förordning ({sfs_id}) om något",
                "ikraftDateTime": "2015-01-01T00:00:00",
                "ikraftOvergangsbestammelse": False,
                "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
                "forfattningstypNamn": "Förordning",
                "register": {"forarbeten": None},
                "fulltext": {"utfardadDateTime": "2015-01-01T00:00:00", "andringInford": None, "forfattningstext": "1 § Test."},
                "publiceradDateTime": "2015-01-01T00:00:00",
                "andringsforfattningar": [
                    {"beteckning": "2018:1381", "rubrik": "Ändringsförordning", "anteckningar": "ny 17 §", "ikraftDateTime": "2018-08-01T00:00:00"},
                    {"beteckning": "2019:77", "rubrik": "Ändringsförordning", "anteckningar": "ändr. 23 §", "ikraftDateTime": "2019-04-01T00:00:00"},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    )
    monkeypatch.setattr(
        "lawvm.tools.sweden._probe_sfs_ids",
        lambda archive_obj, sfs_ids, force_reextract=False, effective_dates=None: (
            probe_calls.append(
                {
                    "sfs_ids": list(sfs_ids),
                    "force_reextract": force_reextract,
                    "effective_dates": dict(effective_dates or {}),
                }
            )
            or [
                {"sfs_id": sfs_ids[0], "status": "ok", "match_count": 1, "target_count": 1, "op_count": 2, "base_sfs_id": "2015:284", "classifications": ["exact"]},
                {"sfs_id": sfs_ids[1], "status": "error", "error": "NotImplementedError: unsupported"},
            ]
        ),
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="probe-base",
            base_sfs_id="2015:284",
            db=None,
            limit=2,
            force_reextract=False,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Base SFS ID:        2015:284" in out
    assert "Amendment count:    2" in out
    assert "2018:1381  OK  1/1  ops=2  base=2015:284  classes=exact" in out
    assert "2019:77  ERROR  NotImplementedError: unsupported" in out
    assert probe_calls == [
        {
            "sfs_ids": ["2018:1381", "2019:77"],
            "force_reextract": False,
            "effective_dates": {"2018:1381": "2018-08-01", "2019:77": "2019-04-01"},
        }
    ]


def test_sweden_hydrate_bulk_command_skips_complete_archive_rows(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext(
        stored={
            "se://sfs/2025:399/official.doc.html": b"<main></main>",
            "se://sfs/2025:399/official.pdf": b"%PDF-1.7 fake",
            "se://sfs/2025:399/official.pdf.txt": b"Recovered PDF text",
            "se://sfs/2025:399/official.cleaned.txt": b"Recovered PDF text",
            "se://sfs/2025:399/official.act.json": b"{}",
            "se://sfs/2025:399/official.ops.json": b"[]",
            "se://sfs/2025:399/rk.current.json": b"{}",
        }
    )
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="hydrate-bulk",
            sfs_ids=[],
            db=None,
            scrape_json=None,
            hydrate_current=True,
            compile_ops=True,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            offset=0,
            limit=0,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Input IDs:          1" in out
    assert "Skipped complete:   1" in out
    assert "2025:399  SKIP  complete" in out


def test_sweden_hydrate_bulk_command_ingests_scrape_and_hydrates(monkeypatch, tmp_path, capsys) -> None:
    scrape_path = tmp_path / "sweden_scraped_results.json"
    scrape_path.write_text(
        json.dumps(
            {
                "https://svenskforfattningssamling.se/doc/2026286.html": (
                    '<main><a href="../sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a></main>'
                )
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": True}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    def fake_fetch_current(sfs_id: str, archive_obj, *, max_age_hours: float = 24.0) -> bytes:
        payload = json.dumps({"beteckning": sfs_id, "rubrik": "Lag", "fulltext": {"forfattningstext": "1 § Test."}}, ensure_ascii=False).encode("utf-8")
        archive_obj.store(f"se://sfs/{sfs_id}/rk.current.json", payload)
        return payload

    def fake_archive_bundle(payload, archive_obj, *, doc_html=None):
        data = json.loads(payload.decode("utf-8")) if isinstance(payload, bytes) else payload
        sfs_id = data["beteckning"]
        archive_obj.store(f"se://sfs/{sfs_id}/source_record.json", b"{}")
        archive_obj.store(f"se://sfs/{sfs_id}/current.ir.json", b"{}")
        archive_obj.store(f"se://sfs/{sfs_id}/bundle.json", b"{}")
        class _Bundle:
            source_record = type("_R", (), {"sfs_id": sfs_id})()
        return _Bundle()

    def fake_compile_ops(archive_obj, sfs_id: str):
        archive_obj.store(f"se://sfs/{sfs_id}/official.ops.json", b"[]")
        return [{"sequence": 1}]

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)
    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_rk_current_json", fake_fetch_current)
    monkeypatch.setattr("lawvm.tools.sweden.archive_se_source_bundle", fake_archive_bundle)
    monkeypatch.setattr("lawvm.tools.sweden.attach_official_artifacts_to_bundle", lambda bundle, official: bundle)
    monkeypatch.setattr("lawvm.tools.sweden.compile_se_official_ops_to_archive", fake_compile_ops)

    sweden_main(
        SimpleNamespace(
            sweden_command="hydrate-bulk",
            sfs_ids=[],
            db=None,
            scrape_json=str(scrape_path),
            hydrate_current=True,
            compile_ops=True,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            offset=0,
            limit=0,
            format="summary",
        )
    )
    out = capsys.readouterr().out

    assert "Input IDs:          1" in out
    assert "Scrape imported:    1" in out
    assert "Completed:          1" in out
    assert "2026:286  OK  pdf=yes  act=yes  ops=1  rk=yes" in out


def test_sweden_backfill_official_command_generates_candidate_ids(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    seen: list[str] = []

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        seen.append(sfs_id)
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=3,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            offset=0,
            limit=2,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    out = captured.out
    err = captured.err

    assert seen == ["1999:1", "1999:2"]
    assert "[1/2] 1999:1 START" in err
    assert "[1/2] 1999:1 FETCH_OFFICIAL" in err
    assert "Input IDs:          2" in out
    assert "Year range:         1999..1999" in out
    assert "Max number/year:    3" in out
    assert "Run outcome:        completed_only" in out
    assert "History:            se://sweden/backfill-official/history.json" in out
    assert "1999:1  OK  pdf=yes  act=yes  ops=-  rk=no" in out
    assert "[1/2] 1999:1 OK" in err
    assert "[2/2] 1999:2 OK" in err


def test_sweden_backfill_official_command_prints_skip_progress_to_stderr(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    archive.store("se://sfs/1999:1/official.doc.html", b"<html></html>")
    archive.store("se://sfs/1999:1/official.pdf", b"%PDF-1.7 fake")
    archive.store("se://sfs/1999:1/official.pdf.txt", b"Recovered PDF text")
    archive.store("se://sfs/1999:1/official.cleaned.txt", b"Recovered PDF text")
    archive.store("se://sfs/1999:1/official.act.json", json.dumps({"sfs_id": "1999:1", "is_amending_act": False}).encode("utf-8"))
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=1,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            offset=0,
            limit=0,
            format="summary",
        )
    )
    captured = capsys.readouterr()

    assert "[1/1] 1999:1 START" in captured.err
    assert "[1/1] 1999:1 SKIP complete" in captured.err


def test_sweden_backfill_official_command_emits_json(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)
    monkeypatch.setattr(
        "lawvm.tools.sweden.fetch_se_official_artifacts",
        lambda sfs_id, archive_obj, max_age_hours=float("inf"), force_reextract=False: (
            archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake"),
            archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text"),
            archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text"),
            archive_obj.store(f"se://sfs/{sfs_id}/official.act.json", json.dumps({"sfs_id": sfs_id, "is_amending_act": False}).encode("utf-8")),
        ) and None,
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=2000,
            year_end=2000,
            max_number=2,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            offset=0,
            limit=0,
            format="json",
        )
    )
    data = json.loads(capsys.readouterr().out)

    assert data["input_count"] == 2
    assert data["year_start"] == 2000
    assert data["year_end"] == 2000
    assert data["max_number"] == 2
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))
    assert history[-1]["outcome_kind"] == "completed_only"
    assert history[-1]["completion_ratio"] == 1.0


def test_sweden_backfill_official_command_writes_and_resumes_checkpoint(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    seen: list[str] = []

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        seen.append(sfs_id)
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=2,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=True,
            resume=False,
            offset=0,
            limit=1,
            format="summary",
        )
    )
    first = capsys.readouterr()
    checkpoint = json.loads(archive.stored[se_backfill_official_checkpoint_locator()].decode("utf-8"))
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))

    assert checkpoint["next_index"] == 1
    assert checkpoint["last_sfs_id"] == "1999:1"
    assert len(history) == 1
    assert history[0]["checkpoint_locator"] == se_backfill_official_checkpoint_locator()
    assert history[0]["status_locator"] == se_backfill_official_status_locator()
    assert "Completeness:       se://sweden/backfill-official/completeness.json" in first.out
    assert "[1/1] 1999:1 OK" in first.err

    seen.clear()
    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=2,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=True,
            resume=True,
            offset=0,
            limit=0,
            format="summary",
        )
    )
    second = capsys.readouterr()

    assert seen == ["1999:2"]
    assert "[1/1] 1999:2 OK" in second.err
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))
    assert len(history) == 2
    assert history[-1]["last_sfs_id"] == "1999:2"
    assert history[-1]["outcome_kind"] == "completed_only"
    assert history[-1]["dominant_error_kind"] == ""


def test_sweden_backfill_official_command_writes_live_status_artifact(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        status = json.loads(archive_obj.stored[se_backfill_official_status_locator()].decode("utf-8"))
        assert status["current_stage"] == "FETCH_OFFICIAL"
        assert status["current_stage_state"] == "running"
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=1,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=1,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    status = json.loads(archive.stored[se_backfill_official_status_locator()].decode("utf-8"))

    assert status["current_stage"] == "DONE"
    assert status["current_stage_state"] == "completed"
    assert "[1/1] 1999:1 FETCH_OFFICIAL" in captured.err


def test_sweden_backfill_official_command_records_error_kind_counts(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        if sfs_id == "1999:1":
            raise RuntimeError("temporary failure")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=2,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=2,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    checkpoint = json.loads(archive.stored[se_backfill_official_checkpoint_locator()].decode("utf-8"))
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))

    assert checkpoint["error_kind_counts"] == {"RuntimeError": 1}
    assert checkpoint["last_error_kind"] == "RuntimeError"
    assert checkpoint["non_ok_count"] == 1
    assert checkpoint["non_ok_rows"] == [
        {
            "rule_id": "se_backfill_official_error",
            "phase": "acquisition",
            "family": "source_pathology",
            "reason": "Sweden official backfill recorded a source acquisition or compilation error.",
            "sfs_id": "1999:1",
            "status": "error",
            "error_kind": "RuntimeError",
            "error": "RuntimeError: temporary failure",
            "frontier_classification": "",
            "frontier_detail": "",
        },
    ]
    assert history[-1]["error_count"] == 1
    assert history[-1]["error_kind_counts"] == {"RuntimeError": 1}
    assert history[-1]["non_ok_count"] == 1
    assert history[-1]["non_ok_rows"] == checkpoint["non_ok_rows"]
    assert history[-1]["outcome_kind"] == "mixed_completed_error"
    assert history[-1]["dominant_error_kind"] == "RuntimeError"
    assert "Run outcome:        mixed_completed_error" in captured.out
    assert "[1/2] 1999:1 START" in captured.err
    assert "[1/2] 1999:1 FETCH_OFFICIAL" in captured.err
    assert "[2/2] 1999:2 START" in captured.err
    assert "[1/2] 1999:1 ERROR RuntimeError RuntimeError: temporary failure" in captured.err
    assert "[2/2] 1999:2 OK" in captured.err


def test_sweden_backfill_official_command_handles_recovered_word_substitution_family(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps(
                {
                    "sfs_id": sfs_id,
                    "title": "Förordning om ändring i förordningen (1991:978) om statsbidrag till produktion av vissa läromedel",
                    "act_type": "förordning",
                    "amended_act_sfs_id": "1991:978",
                    "is_amending_act": True,
                    "published_date": "2002-12-19",
                    "issued_date": "2002-12-19",
                    "enacting_clause": "Regeringen föreskriver att i 2 och 6 §§ förordningen (1991:978) om statsbidrag till produktion av vissa läromedel ordet ”Skolverket” skall bytas ut mot ”Myndigheten för skolutveckling”.",
                    "effective_clause": "Denna förordning träder i kraft den 1 mars 2003.",
                    "affected_section_labels": ["2", "6"],
                    "provisions": [],
                    "inserted_headings": [],
                    "appendices": [],
                    "signatories": [],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=1,
            hydrate_current=False,
            compile_ops=True,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=1,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    checkpoint = json.loads(archive.stored[se_backfill_official_checkpoint_locator()].decode("utf-8"))
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))

    assert checkpoint["error_kind_counts"] == {}
    assert history[-1]["error_kind_counts"] == {}
    assert checkpoint["frontier_classification_counts"] == {}
    assert history[-1]["frontier_classification_counts"] == {}
    assert checkpoint["frontier_detail_counts"] == {}
    assert history[-1]["frontier_detail_counts"] == {}
    assert "Frontier classes:" not in captured.out
    assert "Frontier detail:" not in captured.out
    assert "ERROR" not in captured.err
    assert "OK" in captured.err


def test_sweden_backfill_official_command_classifies_missing_base_act(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps(
                {
                    "sfs_id": sfs_id,
                    "title": "Förordning om ändring i räddningstjänstförordningen",
                    "act_type": "förordning",
                    "amended_act_sfs_id": "",
                    "is_amending_act": True,
                    "published_date": "2003-01-10",
                    "issued_date": "2003-01-02",
                    "enacting_clause": "Regeringen föreskriver i fråga om räddningstjänstförordningen dels att 10 § skall upphöra att gälla.",
                    "effective_clause": "Denna förordning träder i kraft den 1 februari 2003.",
                    "affected_section_labels": ["10"],
                    "provisions": [{"label": "10", "text": "10 § ska upphöra att gälla."}],
                    "inserted_headings": [],
                    "appendices": [],
                    "signatories": [],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts

        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=1,
            hydrate_current=False,
            compile_ops=True,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=1,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    checkpoint = json.loads(archive.stored[se_backfill_official_checkpoint_locator()].decode("utf-8"))
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))

    assert checkpoint["error_kind_counts"] == {"missing_base_act": 1}
    assert checkpoint["frontier_classification_counts"] == {"missing_base_act": 1}
    assert history[-1]["error_kind_counts"] == {"missing_base_act": 1}
    assert history[-1]["frontier_classification_counts"] == {"missing_base_act": 1}
    assert "ERROR missing_base_act" in captured.err


def test_sweden_backfill_official_command_appends_history_artifact(monkeypatch) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts
        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=1,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=1,
            format="json",
        )
    )
    data = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))

    assert len(data) == 1
    entry = data[0]
    assert entry["artifact_kind"] == "sweden_backfill_official_run_history"
    assert entry["input_count"] == 1
    assert entry["completed_count"] == 1
    assert entry["outcome_kind"] == "completed_only"
    assert entry["checkpoint_locator"] == se_backfill_official_checkpoint_locator()
    completeness = json.loads(archive.stored[se_backfill_official_completeness_locator()].decode("utf-8"))
    assert completeness["candidate_universe_count"] == 1
    assert completeness["processed_candidate_count"] == 1
    assert completeness["processed_candidate_ratio"] == 1.0
    assert completeness["run_count"] == 1
    assert completeness["outcome_kind_counts"] == {"completed_only": 1}


def test_sweden_backfill_official_command_tracks_sweep_and_chunk_sizes(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts

        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=3,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=2,
            format="summary",
        )
    )
    captured = capsys.readouterr()
    data = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))
    completeness = json.loads(archive.stored[se_backfill_official_completeness_locator()].decode("utf-8"))

    assert "Sweep candidates:   3" in captured.out
    assert "Gap report:         se://sweden/backfill-official/gap-report.json" in captured.out
    assert len(data) == 1
    assert data[0]["chunk_candidate_count"] == 2
    assert data[0]["sweep_candidate_count"] == 3
    assert completeness["candidate_universe_count"] == 3
    assert completeness["chunk_candidate_count"] == 2
    assert completeness["processed_candidate_count"] == 2
    assert completeness["processed_candidate_ratio"] == 2 / 3


def test_sweden_backfill_official_command_prints_priority_range(monkeypatch, capsys) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps({"sfs_id": sfs_id, "is_amending_act": False}, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts

        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)
    monkeypatch.setattr(
        "lawvm.tools.sweden.load_se_backfill_official_chunk_plan_from_archive",
        lambda archive_obj: {
            "recommended_year_range": {
                "start_year": 1999,
                "end_year": 1999,
                "remaining_candidate_count": 1,
            },
            "priority_year_range": {
                "start_year": 2001,
                "end_year": 2002,
                "remaining_candidate_count": 3,
                "frontier_signal_count": 3,
            },
            "largest_remaining_year_range": {
                "start_year": 2001,
                "end_year": 2002,
                "remaining_candidate_count": 3,
            },
        },
    )

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=1999,
            max_number=2,
            hydrate_current=False,
            compile_ops=False,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=1,
            format="summary",
        )
    )
    captured = capsys.readouterr()

    assert "Priority range:     2001..2002 (3 remaining, 3 frontier signals)" in captured.out


def test_sweden_backfill_official_command_records_year_coverage_buckets(monkeypatch) -> None:
    archive = _FakeArchiveContext()
    monkeypatch.setattr("lawvm.tools.sweden.open_se_archive", lambda db_path=None: archive)

    def fake_fetch_official(sfs_id: str, archive_obj, *, max_age_hours=float("inf"), force_reextract: bool = False):
        act: dict[str, object] = {
            "sfs_id": sfs_id,
            "title": f"Förordning om ändring i förordningen ({sfs_id})",
            "act_type": "förordning",
            "published_date": "2000-01-01",
            "issued_date": "2000-01-01",
            "effective_clause": "Denna förordning träder i kraft den 1 juli 2000.",
            "inserted_headings": [],
            "appendices": [],
            "signatories": [],
            "footnotes": [],
        }
        if sfs_id == "1999:1":
            act.update(
                {
                    "amended_act_sfs_id": "",
                    "is_amending_act": True,
                    "enacting_clause": "Regeringen föreskriver att 2 § skall ha följande lydelse.",
                    "affected_section_labels": ["2"],
                    "provisions": [{"label": "2", "text": "2 § ska ha följande lydelse."}],
                }
            )
        elif sfs_id == "1999:2":
            act.update(
                {
                    "amended_act_sfs_id": "1991:978",
                    "is_amending_act": True,
                    "enacting_clause": "Regeringen föreskriver att 2 och 6 §§ förordningen (1991:978) ska ha följande lydelse.",
                    "affected_section_labels": ["2", "6"],
                    "provisions": [],
                }
            )
        else:
            act.update(
                {
                    "amended_act_sfs_id": "1991:978",
                    "is_amending_act": True,
                    "enacting_clause": "Regeringen föreskriver att denna förordning träder i kraft den 1 juli 2000.",
                    "affected_section_labels": [],
                    "provisions": [],
                }
            )
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf", b"%PDF-1.7 fake")
        archive_obj.store(f"se://sfs/{sfs_id}/official.pdf.txt", b"Recovered PDF text")
        archive_obj.store(f"se://sfs/{sfs_id}/official.cleaned.txt", b"Recovered PDF text")
        archive_obj.store(
            f"se://sfs/{sfs_id}/official.act.json",
            json.dumps(act, ensure_ascii=False).encode("utf-8"),
        )
        from lawvm.sweden.fetch import SEOfficialArtifacts

        return SEOfficialArtifacts(
            sfs_id=sfs_id,
            doc_url=f"https://svenskforfattningssamling.se/doc/{sfs_id.replace(':', '')}.html",
            doc_locator=f"se://sfs/{sfs_id}/official.doc.html",
            pdf_url=f"https://example.com/SFS{sfs_id.replace(':', '-')}.pdf",
            pdf_locator=f"se://sfs/{sfs_id}/official.pdf",
            pdf_text_url=f"se://sfs/{sfs_id}/official.pdf.txt",
            pdf_cleaned_text_url=f"se://sfs/{sfs_id}/official.cleaned.txt",
        )

    monkeypatch.setattr("lawvm.tools.sweden.fetch_se_official_artifacts", fake_fetch_official)

    sweden_main(
        SimpleNamespace(
            sweden_command="backfill-official",
            db=None,
            year_start=1999,
            year_end=2000,
            max_number=2,
            hydrate_current=False,
            compile_ops=True,
            official_max_age_hours=None,
            current_max_age_hours=None,
            force_reextract=False,
            no_skip_complete=False,
            resume=False,
            offset=0,
            limit=3,
            format="json",
        )
    )

    completeness = json.loads(archive.stored[se_backfill_official_completeness_locator()].decode("utf-8"))
    gap_report = json.loads(archive.stored[se_backfill_official_gap_report_locator()].decode("utf-8"))
    chunk_plan = json.loads(archive.stored[se_backfill_official_chunk_plan_locator()].decode("utf-8"))
    history = json.loads(archive.stored[se_backfill_official_history_locator()].decode("utf-8"))
    assert completeness["sweep_year_counts"] == {"1999": 2, "2000": 2}
    assert completeness["chunk_year_counts"] == {"1999": 2, "2000": 1}
    assert completeness["chunk_year_status_counts"] == {"1999": {"error": 2}, "2000": {"error": 1}}
    assert completeness["chunk_year_frontier_classification_counts"] == {
        "1999": {"empty_effect_plan_with_clause_targets": 1, "missing_base_act": 1},
        "2000": {"empty_effect_plan_without_targets": 1},
    }
    assert gap_report["candidate_universe_count"] == 4
    assert gap_report["processed_candidate_count"] == 3
    assert gap_report["processed_candidate_ratio"] == 3 / 4
    assert gap_report["sweep_year_counts"] == {"1999": 2, "2000": 2}
    assert gap_report["processed_year_counts"] == {"1999": 2, "2000": 1}
    assert gap_report["remaining_year_counts"] == {"1999": 0, "2000": 1}
    assert gap_report["processed_year_frontier_classification_counts"] == {
        "1999": {"empty_effect_plan_with_clause_targets": 1, "missing_base_act": 1},
        "2000": {"empty_effect_plan_without_targets": 1},
    }
    assert gap_report["year_gap_ranges"] == [
        {
            "start_year": 2000,
            "end_year": 2000,
            "year_count": 1,
            "remaining_candidate_count": 1,
            "processed_candidate_count": 1,
            "error_count": 1,
            "skipped_count": 0,
        }
    ]
    assert chunk_plan["recommended_year_range"] == {
        "start_year": 2000,
        "end_year": 2000,
        "year_count": 1,
        "remaining_candidate_count": 1,
        "processed_candidate_count": 1,
        "error_count": 1,
        "skipped_count": 0,
        "years": ["2000"],
        "state_counts": {"partial_with_errors": 1},
        "frontier_classification_counts": {"empty_effect_plan_without_targets": 1},
        "frontier_signal_count": 1,
        "frontier_signal_density": 1.0,
        "priority_score": 2,
    }
    assert chunk_plan["largest_remaining_year_range"] == chunk_plan["recommended_year_range"]
    assert chunk_plan["ranked_year_ranges"] == [chunk_plan["recommended_year_range"]]
    assert chunk_plan["priority_year_range"] == chunk_plan["recommended_year_range"]
    assert chunk_plan["priority_ranked_year_ranges"] == [chunk_plan["recommended_year_range"]]
    assert gap_report["year_gap_rows"] == [
        {
            "year": "1999",
            "sweep_candidate_count": 2,
            "processed_candidate_count": 2,
            "remaining_candidate_count": 0,
            "completed_count": 0,
            "skipped_count": 0,
            "error_count": 2,
            "status_counts": {"error": 2},
            "frontier_classification_counts": {
                "empty_effect_plan_with_clause_targets": 1,
                "missing_base_act": 1,
            },
            "state": "completed_with_errors",
        },
        {
            "year": "2000",
            "sweep_candidate_count": 2,
            "processed_candidate_count": 1,
            "remaining_candidate_count": 1,
            "completed_count": 0,
            "skipped_count": 0,
            "error_count": 1,
            "status_counts": {"error": 1},
            "frontier_classification_counts": {"empty_effect_plan_without_targets": 1},
            "state": "partial_with_errors",
        },
    ]
    assert history[-1]["chunk_year_frontier_classification_counts"] == {
        "1999": {"empty_effect_plan_with_clause_targets": 1, "missing_base_act": 1},
        "2000": {"empty_effect_plan_without_targets": 1},
    }


def test_sweden_backfill_chunk_plan_prefers_chronological_next_range() -> None:
    from lawvm.tools.sweden import _se_backfill_chunk_plan_from_gap_report

    gap_report = {
        "run_signature": {"year_start": 1999, "year_end": 2002, "max_number": 2},
        "checkpoint_locator": "se://sweden/backfill-official/checkpoint.json",
        "status_locator": "se://sweden/backfill-official/status.json",
        "history_locator": "se://sweden/backfill-official/history.json",
        "completeness_locator": "se://sweden/backfill-official/completeness.json",
        "candidate_universe_count": 8,
        "processed_candidate_count": 5,
        "processed_candidate_ratio": 5 / 8,
        "gap_state_counts": {"partial": 1, "partial_with_errors": 1, "untouched": 1},
        "year_gap_rows": [
            {
                "year": "1999",
                "sweep_candidate_count": 2,
                "processed_candidate_count": 1,
                "remaining_candidate_count": 1,
                "completed_count": 0,
                "skipped_count": 0,
                "error_count": 1,
                "status_counts": {"error": 1},
                "frontier_classification_counts": {"missing_base_act": 1},
                "state": "partial",
            },
            {
                "year": "2000",
                "sweep_candidate_count": 2,
                "processed_candidate_count": 1,
                "remaining_candidate_count": 0,
                "completed_count": 1,
                "skipped_count": 0,
                "error_count": 0,
                "status_counts": {"ok": 1},
                "frontier_classification_counts": {},
                "state": "complete",
            },
            {
                "year": "2001",
                "sweep_candidate_count": 2,
                "processed_candidate_count": 2,
                "remaining_candidate_count": 2,
                "completed_count": 0,
                "skipped_count": 0,
                "error_count": 1,
                "status_counts": {"error": 1, "skipped_complete": 1},
                "frontier_classification_counts": {"empty_effect_plan_without_targets": 2},
                "state": "partial_with_errors",
            },
            {
                "year": "2002",
                "sweep_candidate_count": 2,
                "processed_candidate_count": 1,
                "remaining_candidate_count": 1,
                "completed_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "status_counts": {"skipped_complete": 1},
                "frontier_classification_counts": {"empty_effect_plan_with_clause_targets": 1},
                "state": "untouched",
            },
        ],
        "year_gap_ranges": [
            {
                "start_year": 1999,
                "end_year": 1999,
                "year_count": 1,
                "remaining_candidate_count": 1,
                "processed_candidate_count": 1,
                "error_count": 1,
                "skipped_count": 0,
            },
            {
                "start_year": 2001,
                "end_year": 2002,
                "year_count": 2,
                "remaining_candidate_count": 3,
                "processed_candidate_count": 3,
                "error_count": 1,
                "skipped_count": 0,
            },
        ],
    }

    chunk_plan = _se_backfill_chunk_plan_from_gap_report(gap_report)

    assert chunk_plan["recommended_year_range"] == {
        "start_year": 1999,
        "end_year": 1999,
        "year_count": 1,
        "remaining_candidate_count": 1,
        "processed_candidate_count": 1,
        "error_count": 1,
        "skipped_count": 0,
        "years": ["1999"],
        "state_counts": {"partial": 1},
        "frontier_classification_counts": {"missing_base_act": 1},
        "frontier_signal_count": 1,
        "frontier_signal_density": 1.0,
        "priority_score": 2,
    }
    assert chunk_plan["largest_remaining_year_range"] == {
        "start_year": 2001,
        "end_year": 2002,
        "year_count": 2,
        "remaining_candidate_count": 3,
        "processed_candidate_count": 3,
        "error_count": 1,
        "skipped_count": 0,
        "years": ["2001", "2002"],
        "state_counts": {"partial_with_errors": 1, "untouched": 1},
        "frontier_classification_counts": {
            "empty_effect_plan_with_clause_targets": 1,
            "empty_effect_plan_without_targets": 2,
        },
        "frontier_signal_count": 3,
        "frontier_signal_density": 1.5,
        "priority_score": 6,
    }
    assert chunk_plan["ranked_year_ranges"][0]["start_year"] == 2001
    assert chunk_plan["priority_year_range"] == chunk_plan["largest_remaining_year_range"]
    assert chunk_plan["priority_ranked_year_ranges"][0]["start_year"] == 2001
