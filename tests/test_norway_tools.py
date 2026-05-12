from __future__ import annotations

import asyncio
import io
import json
import tarfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from lawvm.tools.no_commencement_report import main as no_commencement_report_main
from lawvm.tools.no_commencement_candidates import main as no_commencement_candidates_main
from lawvm.tools.no_commencement_backfill import main as no_commencement_backfill_main
from lawvm.tools.no_commencement_evidence_plan import main as no_commencement_evidence_plan_main
from lawvm.tools.no_commencement_phrases import main as no_commencement_phrases_main
from lawvm.tools.no_frontier import main as no_frontier_main
from lawvm.tools.no_ingest import main as no_ingest_main
from lawvm.tools.no_law import main as no_law_main
from lawvm.tools.no_missing_base import main as no_missing_base_main
from lawvm.tools.no_progress import main as no_progress_main
from lawvm.tools.no_source import main as no_source_main
from lawvm.tools.no_statsrad import main as no_statsrad_main
from lawvm.tools.no_verify import main as no_verify_main
from lawvm.tools.no_verify_partition import main as no_verify_partition_main
from lawvm.tools.no_verify_scan import main as no_verify_scan_main
from lawvm.tools.no_verify_workqueue import main as no_verify_workqueue_main
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.tools import build as build_tools
from lawvm.tools.no_workqueue import main as no_workqueue_main
from lawvm.norway.commencement import (
    build_no_commencement_backfill_artifact,
    build_no_commencement_external_evidence_plan_artifact,
)
from lawvm.norway.index import load_no_amendment_index
from lawvm.norway.sources import NOLocatedArtifact, load_no_current_law_ids, load_no_current_law_titles


def _write_archive(archive_path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:bz2") as tf:
        for member_name, payload in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def test_load_no_current_law_titles_records_parse_skip(monkeypatch) -> None:
    artifacts = [
        NOLocatedArtifact(
            locator="no://lov/2025-01-01-1/current.xml",
            logical_id="no/lov/2025-01-01-1",
            source_name="gjeldende-lover.tar.bz2",
            member_name="good.xml",
            payload=b"<good/>",
        ),
        NOLocatedArtifact(
            locator="no://lov/2025-01-02-2/current.xml",
            logical_id="no/lov/2025-01-02-2",
            source_name="gjeldende-lover.tar.bz2",
            member_name="bad.xml",
            payload=b"<bad/>",
        ),
    ]
    monkeypatch.setattr("lawvm.norway.sources.iter_no_current_artifacts", lambda _source_path=None: iter(artifacts))

    def fake_parse_no_statute(_payload: bytes, logical_id: str):
        if logical_id == "no/lov/2025-01-02-2":
            raise ValueError("malformed current law")
        return SimpleNamespace(title="Good law")

    monkeypatch.setattr("lawvm.norway.grafter.parse_no_statute", fake_parse_no_statute)
    diagnostics: list[dict[str, object]] = []

    titles = load_no_current_law_titles(diagnostics_out=diagnostics)

    assert titles == {"no/lov/2025-01-01-1": "Good law"}
    assert diagnostics == [
        {
            "rule_id": "no_current_law_title_parse_skipped",
            "phase": "parse",
            "family": "source_pathology",
            "reason": "Norway current-law title extraction skipped an artifact because statute parsing failed.",
            "statute_id": "no/lov/2025-01-02-2",
            "locator": "no://lov/2025-01-02-2/current.xml",
            "source_name": "gjeldende-lover.tar.bz2",
            "member_name": "bad.xml",
            "exception_type": "ValueError",
            "error": "malformed current law",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_load_no_current_law_ids_records_parse_skip_and_marker_fallback(monkeypatch) -> None:
    marker_payload = """
<html>
  <body>
    <article class="legalArticle">
      <h4 class="legalArticleHeader">§ 1 Test</h4>
      <div class="legalArticleText"><p>Operativ tekst.</p></div>
    </article>
  </body>
</html>
""".encode("utf-8")
    artifacts = [
        NOLocatedArtifact(
            locator="no://lov/2025-01-01-1/current.xml",
            logical_id="no/lov/2025-01-01-1",
            source_name="gjeldende-lover.tar.bz2",
            member_name="good.xml",
            payload=b"<good/>",
        ),
        NOLocatedArtifact(
            locator="no://lov/2025-01-02-2/current.xml",
            logical_id="no/lov/2025-01-02-2",
            source_name="gjeldende-lover.tar.bz2",
            member_name="marker.xml",
            payload=marker_payload,
        ),
        NOLocatedArtifact(
            locator="no://lov/2025-01-03-3/current.xml",
            logical_id="no/lov/2025-01-03-3",
            source_name="gjeldende-lover.tar.bz2",
            member_name="bad.xml",
            payload=b"<bad/>",
        ),
    ]
    monkeypatch.setattr("lawvm.norway.sources.iter_no_current_artifacts", lambda _source_path=None: iter(artifacts))

    def fake_parse_no_statute(_payload: bytes, logical_id: str):
        if logical_id != "no/lov/2025-01-01-1":
            raise ValueError("malformed current law")
        return SimpleNamespace(
            body=SimpleNamespace(
                kind="body",
                children=[SimpleNamespace(kind="section", text="Operative text", children=[])],
            )
        )

    monkeypatch.setattr("lawvm.norway.grafter.parse_no_statute", fake_parse_no_statute)
    diagnostics: list[dict[str, object]] = []

    current_ids = load_no_current_law_ids(diagnostics_out=diagnostics)

    assert current_ids == {"no/lov/2025-01-01-1", "no/lov/2025-01-02-2"}
    assert [row["rule_id"] for row in diagnostics] == [
        "no_current_law_id_parse_marker_fallback_used",
        "no_current_law_id_parse_skipped",
    ]
    assert [row["retained_by_marker_fallback"] for row in diagnostics] == [True, False]
    assert all(row["family"] == "source_pathology" for row in diagnostics)
    assert all(row["phase"] == "parse" for row in diagnostics)
    assert all(row["strict_disposition"] == "block" for row in diagnostics)


def test_no_commencement_report_merges_existing_template(tmp_path, monkeypatch, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    },
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "b.tar.bz2",
                        "member_name": "b.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "B",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps(
            {
                "overrides": {
                    "no/lovtid/2025-02-02-5": {
                        "effective_date": "2025-02-10",
                        "note": "kept",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=str(template_path),
        base_id=None,
        phrase=None,
        override_state=None,
        current_laws_only=False,
        sort="source",
        limit=None,
        template_output=str(template_path),
        json=False,
    )

    no_commencement_report_main(args)
    output = capsys.readouterr().out

    merged = json.loads(template_path.read_text(encoding="utf-8"))
    assert merged["overrides"]["no/lovtid/2025-02-02-5"]["effective_date"] == "2025-02-10"
    assert merged["overrides"]["no/lovtid/2025-02-02-5"]["note"] == "kept"
    assert merged["overrides"]["no/lovtid/2025-03-03-6"]["effective_date"] == ""
    assert "override states:" in output


def test_no_missing_base_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-19461213-021.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "dated",
                        "effective_date": "2025-02-10",
                        "raw_date_in_force": "2025-02-10",
                        "title": "A",
                        "base_ids": ["no/lov/1946-12-13-21"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        base_id=None,
        min_amendments=1,
        limit=None,
        json=True,
    )

    no_missing_base_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["missing_base_source_law_count"] == 1
    assert data["laws"][0]["base_id"] == "no/lov/1946-12-13-21"


def test_no_ingest_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    db_path = tmp_path / "norway.farchive"

    args = Namespace(
        data_dir=str(tmp_path),
        db=str(db_path),
        skip_existing=False,
        json=True,
    )

    no_ingest_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["db_path"] == str(db_path)
    assert data["current_locators_stored"] == 1
    assert data["original_locators_stored"] == 1
    assert data["amendment_locators_stored"] == 1


def test_no_ingest_tool_reports_skip_existing_entries(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    db_path = tmp_path / "norway.farchive"
    base_args = Namespace(
        data_dir=str(tmp_path),
        db=str(db_path),
        skip_existing=False,
        json=True,
    )
    no_ingest_main(base_args)
    capsys.readouterr()

    skip_args = Namespace(
        data_dir=str(tmp_path),
        db=str(db_path),
        skip_existing=True,
        json=True,
    )
    no_ingest_main(skip_args)
    data = json.loads(capsys.readouterr().out)

    assert data["skipped_existing"] == 3
    assert data["current_locators_stored"] == 0
    assert data["original_locators_stored"] == 0
    assert data["amendment_locators_stored"] == 0
    assert [entry["kind"] for entry in data["skipped_existing_entries"]] == [
        "current",
        "original",
        "amendment",
    ]
    assert {
        entry["rule_id"] for entry in data["skipped_existing_entries"]
    } == {"no_ingest_existing_locator_skipped"}
    assert {
        entry["phase"] for entry in data["skipped_existing_entries"]
    } == {"acquisition"}
    assert {
        entry["family"] for entry in data["skipped_existing_entries"]
    } == {"transport_cleanup"}
    assert all(entry["locator"].startswith("no://") for entry in data["skipped_existing_entries"])


def test_build_no_records_skipped_statutes_in_stats(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "lover.tar.bz2"
    input_path.write_bytes(b"dummy")
    output_dir = tmp_path / "graph"

    monkeypatch.setattr(
        "lawvm.norway.grafter.open_lovdata_archive",
        lambda _path: iter(
            [
                ("no/lov/2025-01-01-1", b"<html>good</html>"),
                ("no/lov/2025-01-02-2", b"<html>bad</html>"),
            ]
        ),
    )

    def fake_parse_no_statute(html_bytes: bytes, sid: str):
        if sid == "no/lov/2025-01-02-2":
            raise ValueError("malformed source")
        return SimpleNamespace(title="Good law", body=(), supplements=())

    monkeypatch.setattr("lawvm.norway.grafter.parse_no_statute", fake_parse_no_statute)
    monkeypatch.setattr("lawvm.core.timeline.compile_timelines", lambda _statute, _ops: {"1": object()})

    asyncio.run(build_tools._build_no(input_path, output_dir, verbose=False))

    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["n_statutes"] == 1
    assert stats["n_skipped"] == 1
    assert stats["skipped_statutes"] == [
        {
            "rule_id": "no_build_statute_parse_skipped",
            "phase": "build",
            "family": "source_pathology",
            "reason": "Norway build skipped statute after parse or timeline compilation failure",
            "statute_id": "no/lov/2025-01-02-2",
            "error": "malformed source",
        },
    ]
    assert stats["skipped_amendments"] == []


def test_build_no_records_skipped_amendments_in_stats(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "lover.tar.bz2"
    input_path.write_bytes(b"dummy")
    amendment_path = tmp_path / "lovtidend.tar.bz2"
    amendment_path.write_bytes(b"dummy")
    output_dir = tmp_path / "graph"

    monkeypatch.setattr(
        "lawvm.norway.grafter.open_lovdata_archive",
        lambda _path: iter([("no/lov/2025-01-01-1", b"<html>good</html>")]),
    )
    monkeypatch.setattr(
        "lawvm.norway.grafter.open_lovdata_amendment_archive",
        lambda _path: iter([("no/lovtid/2025-02-02-5", b"<html>bad-amendment</html>")]),
    )
    monkeypatch.setattr(
        "lawvm.norway.grafter.parse_no_statute",
        lambda _html_bytes, _sid: SimpleNamespace(title="Good law", body=(), supplements=()),
    )
    monkeypatch.setattr("lawvm.core.timeline.compile_timelines", lambda _statute, _ops: {"1": object()})

    def fail_document_change_ops(_html_bytes: bytes, _source_id: str):
        raise ValueError("malformed amendment")

    monkeypatch.setattr("lawvm.norway.grafter.iter_no_document_change_ops", fail_document_change_ops)

    asyncio.run(build_tools._build_no(input_path, output_dir, verbose=False, amendment_archives=[amendment_path]))

    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["n_statutes"] == 1
    assert stats["n_skipped"] == 0
    assert stats["n_amendment_links"] == 0
    assert stats["n_skipped_amendments"] == 1
    assert stats["skipped_statutes"] == []
    assert stats["skipped_amendments"] == [
        {
            "rule_id": "no_build_amendment_parse_skipped",
            "phase": "build",
            "family": "source_pathology",
            "reason": "Norway build skipped amendment artifact after parser or index extraction failure",
            "source_id": "no/lovtid/2025-02-02-5",
            "archive_path": str(amendment_path),
            "error": "malformed amendment",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_build_fi_lightweight_records_missing_source_skip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus_path = tmp_path / "corpus.csv"
    corpus_path.write_text("row,1999/1\n", encoding="utf-8")
    output_dir = tmp_path / "graph"

    def fake_parallel(statute_ids, _n_workers, _verbose):
        assert statute_ids == ["1999/1"]
        yield None

    monkeypatch.setattr(build_tools, "_build_fi_lightweight_parallel", fake_parallel)
    monkeypatch.setattr("lawvm.finland.amendment_index.get_amendment_children", lambda: {})

    build_tools.main(
        Namespace(
            jurisdiction="fi",
            output=str(output_dir),
            verbose=False,
            full=False,
            corpus=str(corpus_path),
            with_timelines=False,
            concurrency=1,
        )
    )

    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["n_skipped"] == 1
    assert stats["skipped_statutes"] == [
        {
            "rule_id": "fi_build_source_missing_skipped",
            "phase": "build",
            "family": "source_pathology",
            "reason": "Finnish lightweight build skipped statute because source XML was unavailable",
            "statute_id": "1999/1",
        },
    ]


def test_build_fi_timelines_records_graph_failure_skip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "graph"

    async def fake_build_statute_graph_fi(sid: str):
        assert sid == "1999/1"
        raise ValueError("bad graph")

    monkeypatch.setattr("lawvm.finland.graph.build_statute_graph_fi", fake_build_statute_graph_fi)
    monkeypatch.setattr("lawvm.finland.amendment_index.get_amendment_children", lambda: {})

    asyncio.run(
        build_tools._build_fi_timelines(
            statute_ids=["1999/1"],
            output_dir=output_dir,
            concurrency=1,
            verbose=False,
        )
    )

    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["n_skipped"] == 1
    assert stats["skipped_statutes"] == [
        {
            "rule_id": "fi_build_timeline_statute_skipped",
            "phase": "build",
            "family": "source_pathology",
            "reason": "Finnish timeline build skipped statute after graph construction failure",
            "statute_id": "1999/1",
            "error": "bad graph",
        },
    ]


def test_no_frontier_tool_emits_json(tmp_path, monkeypatch, capsys) -> None:
    # Pre-import verify BEFORE patching inventory, so verify.py's module-level
    # `from lawvm.norway.inventory import build_no_inventory` binds the real
    # function rather than the lambda we're about to patch.  Without this, the
    # first monkeypatch.setattr("lawvm.norway.verify.*") triggers the import of
    # verify.py at a moment when inventory.build_no_inventory is already patched,
    # leaving verify.build_no_inventory permanently bound to the lambda after
    # teardown and breaking all subsequent tests in this session.
    import lawvm.norway.verify  # noqa: F401 – side-effect import for binding safety
    from lawvm.norway.inventory import NOInventory

    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "diagnostics": [
                    {
                        "rule_id": "no_amendment_index_no_change_ops",
                        "family": "source_pathology",
                        "phase": "extraction",
                        "reason": "Norway amendment artifact did not yield document-change operations",
                        "source_id": "no/lovtid/2025-03-03-6",
                        "locator": "no://lovtid/2025-03-03-6/amendment.xml",
                        "archive": "a.tar.bz2",
                        "member_name": "empty.xml",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                ],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.inventory.build_no_inventory",
        lambda data_dir=None, index=None, index_path=None, commencement_path=None: NOInventory(
            data_dir=tmp_path
        ),
    )
    _empty_scan: dict = {
        "data_dir": str(tmp_path),
        "as_of": "2026-03-29",
        "candidate_count": 0,
        "scanned_count": 0,
        "summary": {"consistent": 0, "divergent": 0, "error": 0},
        "source_signal_counts": {},
        "results": [],
    }
    monkeypatch.setattr(
        "lawvm.norway.verify.build_no_verify_scan",
        lambda **kwargs: _empty_scan,
    )
    monkeypatch.setattr(
        "lawvm.norway.verify.build_no_verify_partition",
        lambda **kwargs: {
            "data_dir": str(tmp_path),
            "as_of": "2026-03-29",
            "candidate_count": 0,
            "scanned_count": 0,
            "summary": {"consistent": 0, "divergent": 0, "error": 0},
            "source_signal_counts": {},
            "partitions": {
                "replay_defect": [],
                "untouched_drift": [],
                "source_sparse": [],
                "consistent": [],
                "error": [],
            },
        },
    )
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        limit=5,
        min_blockers=1,
        min_amendments=1,
        json=True,
    )

    no_frontier_main(args)
    data = json.loads(capsys.readouterr().out)

    assert "inventory" in data
    assert "unlock_queue" in data
    assert "commencement_candidate_source_counts" in data
    assert "executable_blockers" in data
    assert "missing_base_source" in data
    assert "consistency_sample" in data
    assert "consistency_partition" in data
    assert data["index_diagnostic_count"] == 1
    assert data["index_diagnostics"][0]["rule_id"] == "no_amendment_index_no_change_ops"
    assert data["active_consistency_lane"] in {
        "replay_defect",
        "untouched_drift",
        "source_sparse",
        "consistent",
        "error",
    }


def test_no_frontier_tool_prints_partition_summary(tmp_path, capsys) -> None:
    from tests.test_norway_verify import _BASE_XML, _CURRENT_DIVERGENT_XML, _amendment_xml

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )

    args = Namespace(
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        limit=5,
        min_blockers=1,
        min_amendments=1,
        as_of="2025-02-15",
        json=False,
    )

    no_frontier_main(args)
    output = capsys.readouterr().out

    assert "consistency partition" in output
    assert "commencement candidate lanes" in output
    assert "active consistency lane" in output
    assert "top replay defects:" in output


def test_no_verify_partition_tool_emits_json(tmp_path, capsys) -> None:
    from tests.test_norway_verify import _BASE_XML, _CURRENT_DIVERGENT_XML, _amendment_xml

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )

    args = Namespace(
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        as_of="2025-02-15",
        limit=5,
        json=True,
    )

    no_verify_partition_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["summary"] == {"consistent": 0, "divergent": 1, "error": 0}
    assert data["partitions"]["replay_defect"][0]["base_id"] == "no/lov/2025-01-01-1"
    assert data["partitions"]["source_sparse"] == []


def test_no_verify_partition_tool_writes_output_file(tmp_path, capsys) -> None:
    from tests.test_norway_verify import _BASE_XML, _CURRENT_DIVERGENT_XML, _amendment_xml

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )
    output_path = tmp_path / "partition.json"

    args = Namespace(
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        as_of="2025-02-15",
        limit=5,
        output=str(output_path),
        json=False,
    )

    no_verify_partition_main(args)
    output = capsys.readouterr().out
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert "output" in output
    assert data["partitions"]["replay_defect"][0]["base_id"] == "no/lov/2025-01-01-1"


def test_no_verify_partition_tool_prints_bucket_counts(tmp_path, capsys) -> None:
    from tests.test_norway_verify import _BASE_XML, _CURRENT_DIVERGENT_XML, _amendment_xml

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )

    args = Namespace(
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        as_of="2025-02-15",
        limit=5,
        json=False,
    )

    no_verify_partition_main(args)
    output = capsys.readouterr().out

    assert "Replay Defects (1):" in output


def test_no_verify_workqueue_tool_emits_json(tmp_path, capsys) -> None:
    from tests.test_norway_verify import _BASE_XML, _CURRENT_DIVERGENT_XML, _amendment_xml

    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", _BASE_XML),
            ("lti/2025/nl-20250202-005.xml", _amendment_xml()),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", _CURRENT_DIVERGENT_XML)],
    )

    args = Namespace(
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        as_of="2025-02-15",
        limit=5,
        bucket="replay_defect",
        json=True,
    )

    no_verify_workqueue_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["bucket"] == "replay_defect"
    assert data["queue_count"] == 1
    assert data["queue"][0]["base_id"] == "no/lov/2025-01-01-1"


def test_no_verify_workqueue_tool_reuses_saved_partition(tmp_path, capsys) -> None:
    partition_path = tmp_path / "partition.json"
    partition_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "as_of": "2025-02-15",
                "candidate_count": 1,
                "scanned_count": 1,
                "summary": {"consistent": 0, "divergent": 1, "error": 0},
                "source_signal_counts": {"sparse_indexed_history": 2},
                "partitions": {
                    "replay_defect": [
                        {
                            "base_id": "no/lov/2025-01-01-1",
                            "divergence_count": 7,
                            "replay_op_count": 1,
                            "indexed_amendment_count": 1,
                            "source_signal": "",
                            "error": "",
                        }
                    ],
                    "untouched_drift": [],
                    "source_sparse": [],
                    "consistent": [],
                    "error": [],
                },
            }
        ),
        encoding="utf-8",
    )

    args = Namespace(
        data_dir=None,
        index=None,
        commencement=None,
        partition=str(partition_path),
        as_of="2025-02-15",
        limit=5,
        bucket="replay_defect",
        json=True,
    )

    no_verify_workqueue_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["queue_count"] == 1
    assert data["source_signal_counts"] == {"sparse_indexed_history": 2}
    assert data["queue"][0]["base_id"] == "no/lov/2025-01-01-1"


def test_no_verify_workqueue_tool_can_emit_untouched_drift_bucket(tmp_path, capsys) -> None:
    partition_path = tmp_path / "partition.json"
    partition_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "as_of": "2025-02-15",
                "candidate_count": 2,
                "scanned_count": 2,
                "summary": {"consistent": 0, "divergent": 2, "error": 0},
                "source_signal_counts": {},
                "partitions": {
                    "replay_defect": [],
                    "untouched_drift": [
                        {
                            "base_id": "no/lov/2025-01-01-2",
                            "divergence_count": 3,
                            "replay_op_count": 1,
                            "indexed_amendment_count": 1,
                            "source_signal": "",
                            "error": "",
                        }
                    ],
                    "source_sparse": [],
                    "consistent": [],
                    "error": [],
                },
            }
        ),
        encoding="utf-8",
    )

    args = Namespace(
        data_dir=None,
        index=None,
        commencement=None,
        partition=str(partition_path),
        as_of="2025-02-15",
        limit=5,
        bucket="untouched_drift",
        json=True,
    )

    no_verify_workqueue_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["bucket"] == "untouched_drift"
    assert data["bucket_label"] == "Untouched Drift"
    assert data["queue_count"] == 1
    assert data["queue"][0]["base_id"] == "no/lov/2025-01-01-2"


def test_no_source_tool_emits_json(tmp_path, capsys) -> None:
    current_payload = """<html>
      <head><title>Testlov</title></head>
      <body>
        <main>
          <article class="legalArticle" data-name="§1">
            <h4 class="legalArticleHeader">§ 1 Test</h4>
            <div class="legalArticleText">
              <p>Operativ tekst.</p>
            </div>
          </article>
        </main>
      </body>
    </html>""".encode("utf-8")
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", current_payload)],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"overrides": {"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}}}),
        encoding="utf-8",
    )
    args = Namespace(
        source_id="no/lovtid/2025-02-02-5",
        data_dir=None,
        index=str(index_path),
        commencement=str(commencement_path),
        limit=None,
        json=True,
    )

    no_source_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["source_id"] == "no/lovtid/2025-02-02-5"
    assert data["executable_current_law_count"] == 1
    assert data["override_state"] == "blank"


def test_no_source_tool_embeds_direct_candidate_scan(monkeypatch, tmp_path, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda *args, **kwargs: {
            "source_id": "no/lovtid/2025-02-02-5",
            "candidate_count": 1,
            "local_candidate_count": 1,
            "statsrad_candidate_count": 1,
            "candidates": [
                {
                    "source_id": "no/lovtid/2025-02-10-7",
                    "title": "Ikraftsettingslov",
                    "score": 42,
                    "commencement_marker": True,
                }
            ],
            "local_candidates": [],
            "statsrad_candidates": [
                {
                    "source_id": "id3103197",
                    "title": "Offisielt fra statsråd 27. mai 2025",
                    "score": 17,
                    "commencement_marker": True,
                }
            ],
        },
    )
    args = Namespace(
        source_id="no/lovtid/2025-02-02-5",
        data_dir=None,
        index=str(index_path),
        commencement=None,
        limit=None,
        json=True,
    )

    no_source_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["candidate_scan_direct"]["candidate_count"] == 1
    assert data["candidate_scan_direct"]["candidates"][0]["source_id"] == "no/lovtid/2025-02-10-7"
    assert data["candidate_scan_statsrad"]["candidate_count"] == 1
    assert data["candidate_scan_statsrad"]["candidates"][0]["source_id"] == "id3103197"
    assert data["candidate_scans"]["local_corpus"]["candidate_count"] == 1
    assert data["candidate_scans"]["statsrad"]["candidate_count"] == 1


def test_no_commencement_candidates_tool_writes_artifact(tmp_path, monkeypatch, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda **kwargs: {
            "source_id": "no/lovtid/2025-02-02-5",
            "source_title": "A",
            "source_effective_status": "contingent",
            "source_raw_date_in_force": "Kongen bestemmer",
            "source_date": "",
            "index_generated_at_utc": "2026-04-05T00:00:00Z",
            "base_ids": ["no/lov/2025-01-01-1"],
            "data_dir": str(tmp_path),
            "direct_only": False,
            "candidate_count": 2,
            "local_candidate_count": 1,
            "statsrad_candidate_count": 1,
            "candidates": [],
            "local_candidates": [],
            "statsrad_candidates": [],
            "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
            "candidate_groups": [
                {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
            ],
        },
    )
    output_path = tmp_path / "candidate_artifact.json"
    args = Namespace(
        source_id="no/lovtid/2025-02-02-5",
        data_dir=None,
        index=str(index_path),
        commencement=None,
        limit=None,
        direct_only=False,
        output=str(output_path),
        json=True,
    )

    no_commencement_candidates_main(args)
    data = json.loads(capsys.readouterr().out)
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert data["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}
    assert artifact["artifact_kind"] == "commencement_candidate_artifact"
    assert artifact["phase_owner"] == "lawvm.norway.commencement"
    assert artifact["source_lanes"] == {"local_corpus": 1, "statsrad": 1}
    assert artifact["input_locators"]["index_path"] == str(index_path)
    assert artifact["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}


def test_no_commencement_backfill_tool_writes_artifact(tmp_path, monkeypatch, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_backfill_artifact",
        lambda *args, **kwargs: {
            "jurisdiction": "no",
            "artifact_kind": "commencement_backfill_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": "2026-04-05T00:00:00Z",
            "input_locators": {"data_dir": str(tmp_path), "index_path": str(index_path)},
            "source_lanes": {"local_corpus": 1, "statsrad": 1},
            "work_queue": {"work_items": []},
            "backfill_items": [
                {
                    "source_id": "no/lovtid/2025-02-02-5",
                    "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
                    ],
                    "candidate_count": 2,
                    "recommended_lane": "mixed",
                }
            ],
        },
    )
    output_path = tmp_path / "backfill_artifact.json"
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=None,
        current_laws_only=True,
        sort="unlock",
        phrase=None,
        override_state=None,
        laws_per_source=5,
        limit=10,
        output=str(output_path),
        json=True,
    )

    no_commencement_backfill_main(args)
    data = json.loads(capsys.readouterr().out)
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert data["artifact_kind"] == "commencement_backfill_artifact"
    assert data["source_lanes"] == {"local_corpus": 1, "statsrad": 1}
    assert artifact["phase_owner"] == "lawvm.norway.commencement"
    assert artifact["backfill_items"][0]["recommended_lane"] == "mixed"


def test_no_commencement_backfill_artifact_includes_action_hints(tmp_path, monkeypatch) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_work_queue",
        lambda *args, **kwargs: {
            "data_dir": str(tmp_path),
            "unresolved_count": 1,
            "unresolved_by_status": {"contingent": 1},
            "override_state_counts": {"blank": 1},
            "work_items": [
                {
                    "source_id": "no/lovtid/2025-02-02-5",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 1,
                    "executable_current_law_count": 1,
                    "sole_blocker_current_law_count": 1,
                    "sole_blocker_executable_current_law_count": 1,
                    "top_laws": [{"base_id": "no/lov/2025-01-01-1", "title": "Law"}],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda **kwargs: {
            "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
            "candidate_groups": [
                {
                    "candidate_source": "local_corpus",
                    "candidate_count": 1,
                    "candidates": [
                        {
                            "candidate_source": "local_corpus",
                            "source_id": "no/lovtid/2025-04-25-12",
                            "title": "Statsrad A",
                            "score": 19,
                            "commencement_marker": True,
                            "direct_match": True,
                            "matches": [
                                {
                                    "kind": "source_title",
                                    "needle": "Statsrad A",
                                    "excerpt": "Statsrad A trer i kraft 1. mai 2025.",
                                }
                            ],
                        }
                    ],
                },
                {
                    "candidate_source": "statsrad",
                    "candidate_count": 1,
                    "candidates": [
                        {
                            "candidate_source": "statsrad",
                            "source_id": "no/lovtid/2025-05-27-18",
                            "title": "Statsrad B",
                            "score": 10,
                            "commencement_marker": True,
                            "direct_match": False,
                            "matches": [
                                {
                                    "kind": "base_title",
                                    "needle": "Law",
                                    "excerpt": "Law trer i kraft straks.",
                                }
                            ],
                        }
                    ],
                },
            ],
            "candidate_count": 2,
            "local_candidate_count": 1,
            "statsrad_candidate_count": 1,
            "candidates": [
                {
                    "candidate_source": "local_corpus",
                    "source_id": "no/lovtid/2025-04-25-12",
                    "title": "Statsrad A",
                    "score": 19,
                    "commencement_marker": True,
                    "direct_match": True,
                    "matches": [
                        {
                            "kind": "source_title",
                            "needle": "Statsrad A",
                            "excerpt": "Statsrad A trer i kraft 1. mai 2025.",
                        }
                    ],
                },
                {
                    "candidate_source": "statsrad",
                    "source_id": "no/lovtid/2025-05-27-18",
                    "title": "Statsrad B",
                    "score": 10,
                    "commencement_marker": True,
                    "direct_match": False,
                    "matches": [
                        {
                            "kind": "base_title",
                            "needle": "Law",
                            "excerpt": "Law trer i kraft straks.",
                        }
                    ],
                },
            ],
        },
    )

    artifact = build_no_commencement_backfill_artifact(
        load_no_amendment_index(index_path),
        data_dir=tmp_path,
        index_path=index_path,
        current_laws_only=True,
        sort_mode="unlock",
        laws_per_source=5,
        limit=1,
    )

    item = artifact["backfill_items"][0]
    assert item["recommended_lane"] == "mixed"
    assert item["action_hint"]["next_steps"][0].startswith("Compare local_corpus and statsrad candidates")
    assert item["action_hint"]["candidate_snapshots"][0]["top_match_excerpt"] == "Statsrad A trer i kraft 1. mai 2025."
    assert item["action_hint"]["candidate_group_summary"] == [
        {"candidate_source": "local_corpus", "candidate_count": 1},
        {"candidate_source": "statsrad", "candidate_count": 1},
    ]
    assert "candidate_artifact" not in item
    assert item["next_source_plan"] == [
        {
            "source_family": "local_corpus",
            "display_name": "local_corpus",
            "priority": 1,
            "mode": "compare",
            "status": "candidate",
            "why": "Local corpus candidates exist and should be compared with statsrad.",
            "candidate_group_summary": [{"candidate_source": "local_corpus", "candidate_count": 1}],
        },
        {
            "source_family": "statsrad",
            "display_name": "statsrad",
            "priority": 2,
            "mode": "compare",
            "status": "candidate",
            "why": "Statsrad candidates exist and should be compared against local corpus.",
            "candidate_group_summary": [{"candidate_source": "statsrad", "candidate_count": 1}],
        },
    ]
    assert item["next_source_hint"]["status"] == "compare_existing_lanes"
    assert item["next_source_hint"]["primary_source_family"] == "local_corpus_and_statsrad"


def test_no_commencement_backfill_artifact_marks_unresolved_next_source(tmp_path, monkeypatch) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_work_queue",
        lambda *args, **kwargs: {
            "data_dir": str(tmp_path),
            "unresolved_count": 1,
            "unresolved_by_status": {"contingent": 1},
            "override_state_counts": {"blank": 1},
            "work_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 0,
                    "executable_current_law_count": 0,
                    "sole_blocker_current_law_count": 0,
                    "sole_blocker_executable_current_law_count": 0,
                    "top_laws": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda **kwargs: {
            "candidate_source_counts": {"local_corpus": 0, "statsrad": 0},
            "candidate_groups": [
                {"candidate_source": "local_corpus", "candidate_count": 0, "candidates": []},
                {"candidate_source": "statsrad", "candidate_count": 0, "candidates": []},
            ],
            "candidate_count": 0,
            "local_candidate_count": 0,
            "statsrad_candidate_count": 0,
            "candidates": [],
        },
    )

    artifact = build_no_commencement_backfill_artifact(
        load_no_amendment_index(index_path),
        data_dir=tmp_path,
        index_path=index_path,
        current_laws_only=True,
        sort_mode="unlock",
        laws_per_source=5,
        limit=1,
    )

    item = artifact["backfill_items"][0]
    assert item["recommended_lane"] == "unresolved"
    assert item["next_source_hint"]["status"] == "needs_external_official_source"
    assert item["next_source_hint"]["primary_source_family"] == "other_official_publication_channels"
    assert "Offisielt fra statsråd" in item["next_source_hint"]["suggested_sources"][0]
    assert "candidate_artifact" not in item
    assert item["next_source_plan"] == [
        {
            "source_family": "offisielt_fra_statsrad",
            "display_name": "Offisielt fra statsråd",
            "priority": 1,
            "mode": "search",
            "status": "next_official_source",
            "why": "No local_corpus or statsrad candidate surfaced for this source.",
            "search_targets": [
                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                "sanction / commencement decisions in council-of-state bulletins",
            ],
        },
        {
            "source_family": "ministerial_regulations",
            "display_name": "ministerial regulations / delegated commencement decisions",
            "priority": 2,
            "mode": "search",
            "status": "next_official_source",
            "why": "Some contingent provisions are resolved by ministerial or delegated publication channels.",
            "search_targets": ["ministerial regulations", "delegated commencement decisions"],
        },
        {
            "source_family": "lovdata_pro_history",
            "display_name": "Lovdata Pro historical layers",
            "priority": 3,
            "mode": "search",
            "status": "fallback_history",
            "why": "Deeper historical layers may contain the missing commencement context.",
            "search_targets": ["historical version / expression layer", "oldest accessible base or amendment chain"],
        },
    ]


def test_no_commencement_backfill_tool_prints_action_hints(monkeypatch, capsys) -> None:
    from lawvm.norway.index import NOAmendmentIndex

    monkeypatch.setattr(
        "lawvm.norway.index.build_no_amendment_index",
        lambda data_dir=None: NOAmendmentIndex(data_dir=""),
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_backfill_artifact",
        lambda *args, **kwargs: {
            "jurisdiction": "no",
            "artifact_kind": "commencement_backfill_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": "2026-04-05T00:00:00Z",
            "input_locators": {"data_dir": "", "index_path": ""},
            "source_lanes": {"local_corpus": 1, "statsrad": 1},
            "work_queue": {"work_items": []},
            "backfill_items": [
                {
                    "source_id": "no/lovtid/2025-02-02-5",
                    "recommended_lane": "mixed",
                    "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
                    ],
                    "candidate_count": 2,
                    "action_hint": {
                        "next_steps": [
                            "Compare local_corpus and statsrad candidates side-by-side.",
                            "Use the top excerpts to decide which source states the force-setting event most directly.",
                        ],
                        "candidate_snapshots": [
                            {
                                "candidate_source": "local_corpus",
                                "source_id": "no/lovtid/2025-04-25-12",
                                "score": 19,
                                "direct_match": True,
                                "top_match_excerpt": "Statsrad A trer i kraft 1. mai 2025.",
                            }
                        ],
                    },
                    "next_source_plan": [
                        {
                            "source_family": "local_corpus",
                            "display_name": "local_corpus",
                            "priority": 1,
                            "mode": "compare",
                            "status": "candidate",
                            "why": "Local corpus candidates exist and should be compared with statsrad.",
                            "candidate_group_summary": [{"candidate_source": "local_corpus", "candidate_count": 1}],
                        },
                        {
                            "source_family": "statsrad",
                            "display_name": "statsrad",
                            "priority": 2,
                            "mode": "compare",
                            "status": "candidate",
                            "why": "Statsrad candidates exist and should be compared against local corpus.",
                            "candidate_group_summary": [{"candidate_source": "statsrad", "candidate_count": 1}],
                        },
                    ],
                    "executable_current_law_count": 1,
                    "sole_blocker_executable_current_law_count": 1,
                }
            ],
        },
    )

    from lawvm.tools.no_commencement_backfill import main as no_commencement_backfill_main

    no_commencement_backfill_main(Namespace(json=False))
    captured = capsys.readouterr()
    assert "next: Compare local_corpus and statsrad candidates side-by-side." in captured.out
    assert "top candidates:" in captured.out
    assert "Statsrad A trer i kraft 1. mai 2025." in captured.out
    assert "source plan:" in captured.out
    assert "1. local_corpus [compare]" in captured.out
    assert "2. statsrad [compare]" in captured.out


def test_no_commencement_backfill_tool_prints_next_source_hint(monkeypatch, capsys) -> None:
    from lawvm.norway.index import NOAmendmentIndex

    monkeypatch.setattr(
        "lawvm.norway.index.build_no_amendment_index",
        lambda data_dir=None: NOAmendmentIndex(data_dir=""),
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_backfill_artifact",
        lambda *args, **kwargs: {
            "jurisdiction": "no",
            "artifact_kind": "commencement_backfill_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": "2026-04-05T00:00:00Z",
            "input_locators": {"data_dir": "", "index_path": ""},
            "source_lanes": {"local_corpus": 0, "statsrad": 0},
            "work_queue": {"work_items": []},
            "backfill_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "recommended_lane": "unresolved",
                    "candidate_source_counts": {"local_corpus": 0, "statsrad": 0},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 0, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 0, "candidates": []},
                    ],
                    "candidate_count": 0,
                    "action_hint": {
                        "next_steps": [
                            "No local_corpus or statsrad candidate surfaced for this source.",
                            "Search other official publication channels or handle this one manually.",
                        ],
                        "candidate_snapshots": [],
                    },
                    "next_source_hint": {
                        "status": "needs_external_official_source",
                        "primary_source_family": "other_official_publication_channels",
                        "suggested_sources": [
                            "Offisielt fra statsråd on regjeringen.no",
                            "ministerial regulations / delegated commencement decisions",
                        ],
                    },
                    "next_source_plan": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "display_name": "Offisielt fra statsråd",
                            "priority": 1,
                            "mode": "search",
                            "status": "next_official_source",
                            "why": "No local_corpus or statsrad candidate surfaced for this source.",
                            "search_targets": [
                                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                                "sanction / commencement decisions in council-of-state bulletins",
                            ],
                        },
                        {
                            "source_family": "ministerial_regulations",
                            "display_name": "ministerial regulations / delegated commencement decisions",
                            "priority": 2,
                            "mode": "search",
                            "status": "next_official_source",
                            "why": "Some contingent provisions are resolved by ministerial or delegated publication channels.",
                            "search_targets": ["ministerial regulations", "delegated commencement decisions"],
                        },
                        {
                            "source_family": "lovdata_pro_history",
                            "display_name": "Lovdata Pro historical layers",
                            "priority": 3,
                            "mode": "search",
                            "status": "fallback_history",
                            "why": "Deeper historical layers may contain the missing commencement context.",
                            "search_targets": ["historical version / expression layer", "oldest accessible base or amendment chain"],
                        },
                    ],
                    "executable_current_law_count": 0,
                    "sole_blocker_executable_current_law_count": 0,
                }
            ],
        },
    )

    from lawvm.tools.no_commencement_backfill import main as no_commencement_backfill_main

    no_commencement_backfill_main(Namespace(json=False))
    captured = capsys.readouterr()
    assert "next source: other_official_publication_channels (needs_external_official_source)" in captured.out
    assert "Offisielt fra statsråd on regjeringen.no" in captured.out
    assert "source plan:" in captured.out
    assert "1. Offisielt fra statsråd [search]" in captured.out
    assert "2. ministerial regulations / delegated commencement decisions [search]" in captured.out


def test_no_commencement_external_evidence_plan_artifact_is_unresolved_only(tmp_path, monkeypatch) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_backfill_artifact",
        lambda *args, **kwargs: {
            "source_lanes": {"local_corpus": 0, "statsrad": 0},
            "work_queue": {
                "work_items": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "title": "A",
                        "normalized_phrase": "kongen bestemmer",
                        "override_state": "blank",
                        "override_effective_date": "",
                        "current_law_count": 0,
                        "executable_current_law_count": 0,
                        "sole_blocker_current_law_count": 0,
                        "sole_blocker_executable_current_law_count": 0,
                        "top_laws": [],
                    }
                ]
            },
            "backfill_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 0,
                    "executable_current_law_count": 0,
                    "sole_blocker_current_law_count": 0,
                    "sole_blocker_executable_current_law_count": 0,
                    "recommended_lane": "unresolved",
                    "candidate_source_counts": {"local_corpus": 0, "statsrad": 0},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 0, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 0, "candidates": []},
                    ],
                    "candidate_count": 0,
                    "action_hint": {"next_steps": []},
                    "next_source_hint": {"status": "needs_external_official_source"},
                    "next_source_plan": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "priority": 1,
                            "display_name": "Offisielt fra statsråd",
                            "mode": "scan",
                            "search_targets": ["regjeringen.no/no/aktuelt/offisielt-fra-statsrad/"],
                            "why": "statssraad evidence",
                        }
                    ],
                    "source_packets": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "priority": 1,
                            "display_name": "Offisielt fra statsråd",
                            "mode": "scan",
                            "search_targets": ["regjeringen.no/no/aktuelt/offisielt-fra-statsrad/"],
                            "packet_note": "statsraad evidence",
                        }
                    ],
                    "top_laws": [],
                }
            ],
        },
    )

    artifact = build_no_commencement_external_evidence_plan_artifact(
        load_no_amendment_index(index_path),
        data_dir=tmp_path,
        index_path=index_path,
        current_laws_only=True,
        sort_mode="unlock",
        laws_per_source=5,
        limit=1,
    )

    assert artifact["artifact_kind"] == "commencement_external_evidence_plan_artifact"
    assert artifact["unresolved_count"] == 1
    assert artifact["external_source_family_counts"] == {
        "lovdata_pro_history": 1,
        "ministerial_regulations": 1,
        "offisielt_fra_statsrad": 1,
    }
    item = artifact["plan_items"][0]
    assert item["next_source_hint"]["status"] == "needs_external_official_source"
    assert [packet["source_family"] for packet in item["source_packets"]] == [
        "offisielt_fra_statsrad",
        "ministerial_regulations",
        "lovdata_pro_history",
    ]
    assert item["source_packets"][0]["search_targets"] == [
        "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
        "sanction / commencement decisions in council-of-state bulletins",
    ]
    assert [step["source_family"] for step in item["next_source_plan"]] == [
        "offisielt_fra_statsrad",
        "ministerial_regulations",
        "lovdata_pro_history",
    ]


def test_no_commencement_external_evidence_plan_derives_lane_when_queue_item_lacks_one(
    tmp_path, monkeypatch
) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_backfill_artifact",
        lambda *args, **kwargs: {
            "source_lanes": {"local_corpus": 0, "statsrad": 0},
            "work_queue": {"work_items": []},
            "backfill_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 0,
                    "executable_current_law_count": 0,
                    "sole_blocker_current_law_count": 0,
                    "sole_blocker_executable_current_law_count": 0,
                    "candidate_source_counts": {"local_corpus": 0, "statsrad": 0},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 0, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 0, "candidates": []},
                    ],
                    "candidate_count": 0,
                    "top_laws": [],
                }
            ],
        },
    )

    artifact = build_no_commencement_external_evidence_plan_artifact(
        load_no_amendment_index(index_path),
        data_dir=tmp_path,
        index_path=index_path,
        current_laws_only=True,
        sort_mode="unlock",
        laws_per_source=5,
        limit=1,
    )

    assert artifact["unresolved_count"] == 1
    assert artifact["plan_items"][0]["source_id"] == "no/lovtid/2025-03-03-6"
    assert artifact["plan_items"][0]["source_packets"][0]["source_family"] == "offisielt_fra_statsrad"


def test_no_commencement_evidence_plan_tool_writes_artifact(tmp_path, monkeypatch, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "plan.json"

    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_external_evidence_plan_artifact",
        lambda *args, **kwargs: {
            "jurisdiction": "no",
            "artifact_kind": "commencement_external_evidence_plan_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": "2026-04-05T00:00:00Z",
            "input_locators": {"data_dir": str(tmp_path), "index_path": str(index_path)},
            "current_laws_only": True,
            "sort_mode": "unlock",
            "phrase_filter": "",
            "override_state_filter": "",
            "unresolved_count": 1,
            "external_source_family_counts": {
                "lovdata_pro_history": 1,
                "ministerial_regulations": 1,
                "offisielt_fra_statsrad": 1,
            },
            "plan_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 1,
                    "executable_current_law_count": 1,
                    "sole_blocker_current_law_count": 1,
                    "sole_blocker_executable_current_law_count": 1,
                    "next_source_hint": {
                        "status": "needs_external_official_source",
                        "primary_source_family": "other_official_publication_channels",
                    },
                    "next_source_plan": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "display_name": "Offisielt fra statsråd",
                            "priority": 1,
                            "mode": "search",
                            "status": "next_official_source",
                            "why": "No local_corpus or statsrad candidate surfaced for this source.",
                            "search_targets": [
                                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                                "sanction / commencement decisions in council-of-state bulletins",
                            ],
                        }
                    ],
                    "source_packets": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "display_name": "Offisielt fra statsråd",
                            "priority": 1,
                            "mode": "search",
                            "status": "next_official_source",
                            "packet_note": "No local_corpus or statsrad candidate surfaced for this source.",
                            "search_targets": [
                                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                                "sanction / commencement decisions in council-of-state bulletins",
                            ],
                        }
                    ],
                    "top_laws": [],
                }
            ],
        },
    )

    no_commencement_evidence_plan_main(
        Namespace(
            data_dir=None,
            index=str(index_path),
            commencement=None,
            current_laws_only=True,
            sort="unlock",
            phrase=None,
            override_state=None,
            laws_per_source=5,
            limit=1,
            output=str(output_path),
            json=True,
        )
    )
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    artifact = json.loads(output_path.read_text(encoding="utf-8"))

    assert data["artifact_kind"] == "commencement_external_evidence_plan_artifact"
    assert data["unresolved_count"] == 1
    assert data["external_source_family_counts"] == artifact["external_source_family_counts"]
    assert artifact["plan_items"][0]["next_source_hint"]["status"] == "needs_external_official_source"
    assert artifact["plan_items"][0]["source_packets"][0]["source_family"] == "offisielt_fra_statsrad"


def test_no_commencement_evidence_plan_tool_prints_source_packets(tmp_path, monkeypatch, capsys) -> None:
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_external_evidence_plan_artifact",
        lambda *args, **kwargs: {
            "jurisdiction": "no",
            "artifact_kind": "commencement_external_evidence_plan_artifact",
            "phase_owner": "lawvm.norway.commencement",
            "generated_at_utc": "2026-04-05T00:00:00Z",
            "input_locators": {"data_dir": str(tmp_path), "index_path": str(index_path)},
            "current_laws_only": True,
            "sort_mode": "unlock",
            "phrase_filter": "",
            "override_state_filter": "",
            "unresolved_count": 1,
            "external_source_family_counts": {
                "lovdata_pro_history": 1,
                "ministerial_regulations": 1,
                "offisielt_fra_statsrad": 1,
            },
            "plan_items": [
                {
                    "source_id": "no/lovtid/2025-03-03-6",
                    "title": "A",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "current_law_count": 1,
                    "executable_current_law_count": 1,
                    "sole_blocker_current_law_count": 1,
                    "sole_blocker_executable_current_law_count": 1,
                    "next_source_hint": {
                        "status": "needs_external_official_source",
                        "primary_source_family": "other_official_publication_channels",
                    },
                    "source_packets": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "display_name": "Offisielt fra statsråd",
                            "priority": 1,
                            "mode": "search",
                            "status": "next_official_source",
                            "packet_note": "No local_corpus or statsrad candidate surfaced for this source.",
                            "search_targets": [
                                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                                "sanction / commencement decisions in council-of-state bulletins",
                            ],
                        }
                    ],
                    "next_source_plan": [
                        {
                            "source_family": "offisielt_fra_statsrad",
                            "display_name": "Offisielt fra statsråd",
                            "priority": 1,
                            "mode": "search",
                            "status": "next_official_source",
                            "why": "No local_corpus or statsrad candidate surfaced for this source.",
                            "search_targets": [
                                "regjeringen.no/no/aktuelt/offisielt-fra-statsrad/",
                                "sanction / commencement decisions in council-of-state bulletins",
                            ],
                        }
                    ],
                    "top_laws": [],
                }
            ],
        },
    )

    no_commencement_evidence_plan_main(
        Namespace(
            data_dir=None,
            index=str(index_path),
            commencement=None,
            current_laws_only=True,
            sort="unlock",
            phrase=None,
            override_state=None,
            laws_per_source=5,
            limit=1,
            output=None,
            json=False,
        )
    )
    out = capsys.readouterr().out

    assert "source packets:" in out
    assert "Offisielt fra statsråd [search]" in out


def test_no_law_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        data_dir=None,
        index=str(index_path),
        commencement=None,
        limit=None,
        json=True,
    )

    no_law_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["base_id"] == "no/lov/2025-01-01-1"
    assert data["executable_replay_status"] == "blocked_contingent"


def test_no_workqueue_tool_emits_json(tmp_path, monkeypatch, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda **kwargs: {
            "candidate_count": 2,
            "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
            "candidate_groups": [
                {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
            ],
        },
    )
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=None,
        current_laws_only=False,
        sort="unlock",
        laws_per_source=3,
        limit=None,
        json=True,
    )

    no_workqueue_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["unresolved_count"] == 1
    assert data["work_items"][0]["source_id"] == "no/lovtid/2025-02-02-5"
    assert data["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}
    assert data["work_items"][0]["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}
    assert data["work_items"][0]["candidate_count"] == 2


def test_no_commencement_phrases_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen fastset",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"overrides": {"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}}}),
        encoding="utf-8",
    )
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=str(commencement_path),
        current_laws_only=True,
        phrase="Kongen fastset",
        override_state=None,
        sort="unlock",
        limit=None,
        json=True,
    )

    no_commencement_phrases_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["phrase_count"] == 1
    assert data["groups"][0]["phrase"] == "kongen fastsetter"
    assert data["groups"][0]["override_state_counts"] == {"blank": 1}


def test_no_workqueue_tool_writes_packets(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"overrides": {"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}}}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "packets"
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=str(commencement_path),
        current_laws_only=True,
        sort="unlock",
        phrase="Kongen bestemmer",
        override_state="blank",
        laws_per_source=3,
        limit=None,
        output_dir=str(output_dir),
        json=True,
    )

    no_workqueue_main(args)
    data = json.loads(capsys.readouterr().out)

    assert (output_dir / "summary.json").exists()
    assert data["written_paths"][0].endswith("summary.json")
    assert data["phrase_filter"] == "kongen bestemmer"
    assert data["override_state_filter"] == "blank"
    assert data["override_state_counts"] == {"blank": 1}


def test_no_progress_tool_emits_json(tmp_path, monkeypatch, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps({"overrides": {"no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lawvm.tools.no_commencement_candidates.build_no_commencement_candidate_report",
        lambda **kwargs: {
            "candidate_count": 2,
            "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
            "candidate_groups": [
                {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
            ],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_work_queue",
        lambda *args, **kwargs: {
            "data_dir": str(tmp_path),
            "current_laws_only": True,
            "phrase_filter": "",
            "override_state_filter": "",
            "sort_mode": "unlock",
            "laws_per_source": 3,
            "unresolved_count": 1,
            "unresolved_by_status": {"contingent": 1},
            "override_state_counts": {"blank": 1},
            "work_items": [
                {
                    "source_id": "no/lovtid/2025-02-02-5",
                    "normalized_phrase": "kongen bestemmer",
                    "override_state": "blank",
                    "override_effective_date": "",
                    "override_note": "",
                    "override_has_note": False,
                    "title": "A",
                    "effective_status": "contingent",
                    "raw_date_in_force": "Kongen bestemmer",
                    "n_ops": 1,
                    "current_law_count": 1,
                    "executable_current_law_count": 1,
                    "sole_blocker_current_law_count": 1,
                    "sole_blocker_current_laws": ["no/lov/2025-01-01-1"],
                    "sole_blocker_executable_current_law_count": 1,
                    "sole_blocker_executable_current_laws": ["no/lov/2025-01-01-1"],
                    "top_laws": [{"base_id": "no/lov/2025-01-01-1", "title": "Base"}],
                    "top_sole_blocker_executable_laws": [{"base_id": "no/lov/2025-01-01-1", "title": "Base"}],
                    "archive": "a.tar.bz2",
                    "member_name": "a.xml",
                    "candidate_source_counts": {"local_corpus": 1, "statsrad": 1},
                    "candidate_groups": [
                        {"candidate_source": "local_corpus", "candidate_count": 1, "candidates": []},
                        {"candidate_source": "statsrad", "candidate_count": 1, "candidates": []},
                    ],
                    "candidate_count": 2,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_commencement_phrase_report",
        lambda *args, **kwargs: {
            "data_dir": str(tmp_path),
            "current_laws_only": True,
            "phrase_filter": "",
            "override_state_filter": "",
            "sort_mode": "unlock",
            "phrase_count": 1,
            "groups": [
                {
                    "phrase": "kongen bestemmer",
                    "raw_examples": ["Kongen bestemmer"],
                    "source_ids": ["no/lovtid/2025-02-02-5"],
                    "source_count": 1,
                    "current_law_count": 1,
                    "executable_current_law_count": 1,
                    "sole_blocker_current_law_count": 1,
                    "sole_blocker_executable_current_law_count": 1,
                    "n_ops": 1,
                    "override_state_counts": {"blank": 1},
                    "top_sources": [],
                }
            ],
        },
    )
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=str(commencement_path),
        limit=5,
        output_dir=None,
        json=True,
    )

    no_progress_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["override_state_counts"] == {"blank": 1}
    assert data["unresolved_count"] == 1
    assert data["phrase_count"] == 1
    assert data["blank_work_items"][0]["source_id"] == "no/lovtid/2025-02-02-5"
    assert data["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}
    assert data["blank_work_items"][0]["candidate_source_counts"] == {"local_corpus": 1, "statsrad": 1}


def test_no_progress_tool_writes_packet_directories(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", b"<html/>")],
    )
    _write_archive(
        tmp_path / "lovtidend-avd1-2025.tar.bz2",
        [("lti/2025/nl-20250101-001.xml", b"<html/>")],
    )
    index_path = tmp_path / "no_index.json"
    index_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "archive_names": [],
                "entries": [
                    {
                        "source_id": "no/lovtid/2025-02-02-5",
                        "archive": "a.tar.bz2",
                        "member_name": "a.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen bestemmer",
                        "title": "A",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    },
                    {
                        "source_id": "no/lovtid/2025-03-03-6",
                        "archive": "b.tar.bz2",
                        "member_name": "b.xml",
                        "effective_status": "contingent",
                        "effective_date": None,
                        "raw_date_in_force": "Kongen fastsetter",
                        "title": "B",
                        "base_ids": ["no/lov/2025-01-01-1"],
                        "n_ops": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    commencement_path = tmp_path / "commencement.json"
    commencement_path.write_text(
        json.dumps(
            {
                "overrides": {
                    "no/lovtid/2025-02-02-5": {"effective_date": "", "note": ""},
                }
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "progress_packets"
    args = Namespace(
        data_dir=None,
        index=str(index_path),
        commencement=str(commencement_path),
        limit=5,
        output_dir=str(output_dir),
        json=True,
    )

    no_progress_main(args)
    data = json.loads(capsys.readouterr().out)

    assert (output_dir / "summary.json").exists()
    assert (output_dir / "phrase_summary.json").exists()
    assert (output_dir / "blank" / "summary.json").exists()
    assert (output_dir / "untracked" / "summary.json").exists()
    assert any(path.endswith("blank/summary.json") for path in data["written_paths"])
    assert any(path.endswith("untracked/summary.json") for path in data["written_paths"])


def test_no_verify_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><main class="documentBody"><section class="section" data-name="kap1">
<article class="legalArticle" data-name="§1"><article class="legalP">grunntekst</article></article>
</section></main></body></html>""".encode("utf-8")),
            ("lti/2025/nl-20250202-005.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><dd class="dateInForce">2025-02-10</dd>
<article class="document-change" data-document="lov/2025-01-01-1">
<article class="change" data-change-part="lov/2025-01-01-1/§1">
<article class="legalArticle" data-name="§1"><article class="legalP">endret tekst</article></article>
</article></article></body></html>""".encode("utf-8")),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><main class="documentBody"><section class="section" data-name="kap1">
<article class="legalArticle" data-name="§1"><article class="legalP">endret tekst</article></article>
</section></main></body></html>""".encode("utf-8"))],
    )
    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        verbose=False,
        json=True,
    )

    no_verify_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["base_id"] == "no/lov/2025-01-01-1"
    assert data["consistent"] is True


def test_no_verify_tool_preserves_replay_adjudication_evidence(monkeypatch, capsys) -> None:
    adjudication = CompileAdjudication(
        kind="no_replay_missing_amendment_source",
        message="Norway replay skipped amendment: source not found.",
        source_statute="no/lovtid/2025-02-02-5",
        op_id="no-op-1",
        detail={
            "rule_id": "no.replay.missing_amendment_source",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: SimpleNamespace(
            base_id="no/lov/2025-01-01-1",
            as_of="2025-02-15",
            current_title="Demo",
            replay_status="blocked_missing_source",
            consistent=False,
            divergence_count=0,
            divergence_counts={},
            raw_divergence_count=0,
            raw_divergence_counts={},
            indexed_amendment_count=1,
            applied_amendment_count=0,
            replay_op_count=0,
            source_signal="sparse_indexed_history",
            replay=SimpleNamespace(adjudications=[adjudication]),
            error="",
            divergences=None,
        ),
    )
    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=None,
        index=None,
        commencement=None,
        verbose=False,
        json=True,
    )

    no_verify_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["replay_adjudication_count"] == 1
    assert data["replay_adjudication_kind_counts"] == {"no_replay_missing_amendment_source": 1}
    assert data["replay_adjudications"][0]["source_statute"] == "no/lovtid/2025-02-02-5"
    row = data["evidence"]["finding_rows"][0]
    assert row["family"] == "no_replay_missing_amendment_source"
    assert row["rule_id"] == "no.replay.missing_amendment_source"
    assert row["source_artifact_id"] == "no/lovtid/2025-02-02-5"
    assert row["source_unit_id"] == "no-op-1"
    assert row["blocking"] is True
    assert row["strict_disposition"] == "block"


def test_no_verify_tool_preserves_filtered_divergence_evidence(monkeypatch, capsys) -> None:
    divergence = SimpleNamespace(
        address=SimpleNamespace(path=(("section", "1"),)),
        divergence_type="MISMATCH",
        ops_text="ops parent",
        consolidated_text="current parent",
    )
    filtered = SimpleNamespace(
        divergence=divergence,
        rule_id="no_verify.prefix_descendant_suppressed",
        reason="Divergence address is a strict prefix of another raw divergence address.",
    )
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: SimpleNamespace(
            base_id="no/lov/2025-01-01-1",
            as_of="2025-02-15",
            current_title="Demo",
            replay_status="replayed",
            consistent=True,
            divergence_count=0,
            divergence_counts={},
            raw_divergence_count=1,
            raw_divergence_counts={"MISMATCH": 1},
            filtered_divergence_count=1,
            filtered_divergence_rule_counts={"no_verify.prefix_descendant_suppressed": 1},
            indexed_amendment_count=1,
            applied_amendment_count=1,
            replay_op_count=1,
            source_signal="",
            replay=SimpleNamespace(adjudications=[]),
            error="",
            divergences=[],
            filtered_divergences=[filtered],
        ),
    )
    args = Namespace(
        base_id="no/lov/2025-01-01-1",
        as_of="2025-02-15",
        data_dir=None,
        index=None,
        commencement=None,
        verbose=True,
        max_divergences=10,
        json=True,
    )

    no_verify_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["filtered_divergence_count"] == 1
    assert data["filtered_divergence_rule_counts"] == {"no_verify.prefix_descendant_suppressed": 1}
    assert data["filtered_divergences"] == [
        {
            "rule_id": "no_verify.prefix_descendant_suppressed",
            "reason": "Divergence address is a strict prefix of another raw divergence address.",
            "address": [["section", "1"]],
            "divergence_type": "MISMATCH",
            "ops_text": "ops parent",
            "consolidated_text": "current parent",
        }
    ]
def test_no_verify_scan_tool_emits_json(tmp_path, capsys) -> None:
    _write_archive(
        tmp_path / "lovtidend-avd1-2001-2025.tar.bz2",
        [
            ("lti/2025/nl-20250101-001.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><main class="documentBody"><section class="section" data-name="kap1">
<article class="legalArticle" data-name="§1"><article class="legalP">grunntekst</article></article>
</section></main></body></html>""".encode("utf-8")),
            ("lti/2025/nl-20250202-005.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><dd class="dateInForce">2025-02-10</dd>
<article class="document-change" data-document="lov/2025-01-01-1">
<article class="change" data-change-part="lov/2025-01-01-1/§1">
<article class="legalArticle" data-name="§1"><article class="legalP">endret tekst</article></article>
</article></article></body></html>""".encode("utf-8")),
        ],
    )
    _write_archive(
        tmp_path / "gjeldende-lover.tar.bz2",
        [("nl/nl-20250101-001.xml", """<?xml version="1.0" encoding="utf-8"?>
<html><body><main class="documentBody"><section class="section" data-name="kap1">
<article class="legalArticle" data-name="§1"><article class="legalP">endret tekst</article></article>
</section></main></body></html>""".encode("utf-8"))],
    )
    args = Namespace(
        as_of="2025-02-15",
        data_dir=str(tmp_path),
        index=None,
        commencement=None,
        limit=3,
        json=True,
    )

    no_verify_scan_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["candidate_count"] == 1
    assert data["summary"]["consistent"] == 1


def test_no_statsrad_tool_emits_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_index_report",
        lambda **kwargs: {
            "start_page": 1,
            "page_count": 1,
            "article_count": 2,
            "pages": [],
            "articles": [],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_fetch_report",
        lambda **kwargs: {
            "selected_article_count": 2,
            "stored_raw_count": 2,
            "stored_record_count": 2,
            "bulletin_ids": ["id1", "id2"],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_extract_report",
        lambda **kwargs: {"processed_article_count": 2, "event_count": 3},
    )

    args = Namespace(
        data_dir="data/norway.farchive",
        db=None,
        start_page=1,
        max_age_hours=24.0,
        bulletin_id=None,
        limit=None,
        skip_existing=False,
        json=True,
    )
    no_statsrad_main(args)
    data = json.loads(capsys.readouterr().out)

    assert data["index"]["article_count"] == 2
    assert data["fetch"]["stored_raw_count"] == 2
    assert data["extract"]["event_count"] == 3


def test_no_statsrad_tool_prints_new_report_shape(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_index_report",
        lambda **kwargs: {
            "source_name": "regjeringen.no/offisielt-fra-statsrad",
            "start_page": 24,
            "discovered_page_count": 1,
            "discovered_article_count": 18,
            "stored_page_count": 1,
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_fetch_report",
        lambda **kwargs: {
            "selected_article_count": 2,
            "stored_raw_count": 2,
            "stored_record_count": 2,
            "bulletin_ids": ["id2394534", "id2358371"],
        },
    )
    monkeypatch.setattr(
        "lawvm.norway.statsrad.build_no_statsrad_extract_report",
        lambda **kwargs: {"processed_article_count": 2, "event_count": 1},
    )

    args = Namespace(
        data_dir="data/norway.farchive",
        db=None,
        start_page=24,
        max_age_hours=24.0,
        bulletin_id=None,
        limit=None,
        skip_existing=False,
        json=False,
    )
    no_statsrad_main(args)
    out = capsys.readouterr().out

    assert "page count         : 1" in out
    assert "article count      : 18" in out
    assert "requested articles : 2" in out
    assert "stored articles    : 2" in out
    assert "extracted articles : 2" in out
    assert "event count        : 1" in out
