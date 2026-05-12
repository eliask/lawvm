from __future__ import annotations

import json
from argparse import Namespace
from typing import Any
from urllib.error import URLError

from lawvm.eu import cellar


def test_fetch_manifest_records_failed_request_rows(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "label": "gdpr",
                        "celex": "32016R0679",
                        "requests": [
                            {
                                "path": "data/eu/gdpr/tree.xml",
                                "format": "xml",
                                "notice": "tree",
                                "language": "eng",
                                "accept_language": "eng",
                                "in_notice_only": True,
                            },
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cellar, "_repo_root", lambda: tmp_path)

    def fake_request_notice(_notice: cellar.NoticeRequest, timeout_s: int = cellar.DEFAULT_TIMEOUT_S):
        raise URLError("network down")

    monkeypatch.setattr(cellar, "_request_notice", fake_request_notice)

    report = cellar.fetch_manifest(manifest_path)

    assert report.fetched_count == 0
    assert report.failed_count == 1
    assert report.failed_requests == (
        {
            "rule_id": "eu_cellar_manifest_request_failed",
            "phase": "acquisition",
            "family": "source_pathology",
            "source_label": "gdpr",
            "celex": "32016R0679",
            "request_path": "data/eu/gdpr/tree.xml",
            "notice_url": "http://publications.europa.eu/resource/celex/32016R0679?language=eng&filter=true",
            "accept_header": "application/xml;notice=tree",
            "error_type": "URLError",
            "error": "<urlopen error network down>",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )


def test_fetch_manifest_preserves_successes_when_later_request_fails(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "label": "mixed",
                        "celex": "32000R0001",
                        "requests": [
                            {"path": "data/eu/ok.xml", "format": "xml", "notice": "object", "language": "eng"},
                            {"path": "data/eu/fail.xml", "format": "xml", "notice": "tree", "language": "eng"},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cellar, "_repo_root", lambda: tmp_path)

    def fake_request_notice(notice: cellar.NoticeRequest, timeout_s: int = cellar.DEFAULT_TIMEOUT_S):
        if notice.notice_type == "tree":
            raise URLError("timeout")
        return b"<NOTICE/>", {"url": notice.url(), "content_type": "application/xml"}

    monkeypatch.setattr(cellar, "_request_notice", fake_request_notice)

    report = cellar.fetch_manifest(manifest_path)

    assert report.fetched_count == 1
    assert report.failed_count == 1
    assert (tmp_path / "data/eu/ok.xml").read_bytes() == b"<NOTICE/>"
    assert (tmp_path / "data/eu/ok.xml.meta.json").exists()
    assert report.failed_requests[0]["request_path"] == "data/eu/fail.xml"


def test_fetch_manifest_cli_writes_failure_jsonl(monkeypatch, tmp_path, capsys) -> None:
    failures_jsonl = tmp_path / "failures.jsonl"
    row = {
        "rule_id": "eu_cellar_manifest_request_failed",
        "phase": "acquisition",
        "family": "source_pathology",
        "source_label": "gdpr",
        "celex": "32016R0679",
        "request_path": "data/eu/gdpr/tree.xml",
        "notice_url": "http://example.test",
        "accept_header": "application/xml;notice=tree",
        "error_type": "URLError",
        "error": "<urlopen error network down>",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }

    monkeypatch.setattr(
        cellar,
        "fetch_manifest",
        lambda manifest, dry_run=False: cellar.ManifestFetchReport(0, 1, (row,)),
    )

    exit_code = cellar.main(
        [
            "fetch-manifest",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--failures-jsonl",
            str(failures_jsonl),
        ]
    )

    assert exit_code == 1
    assert "Completed with 1 failed fetch(es)" in capsys.readouterr().err
    assert [json.loads(line) for line in failures_jsonl.read_text(encoding="utf-8").splitlines()] == [row]


def test_fetch_manifest_success_has_empty_failed_request_rows(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "label": "ok",
                        "celex": "32000R0001",
                        "requests": [
                            {"path": "data/eu/ok.xml", "format": "xml", "notice": "object", "language": "eng"},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cellar, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cellar,
        "_request_notice",
        lambda notice, timeout_s=cellar.DEFAULT_TIMEOUT_S: (b"<NOTICE/>", {"url": notice.url()}),
    )

    report = cellar.fetch_manifest(manifest_path)

    assert report.fetched_count == 1
    assert report.failed_count == 0
    assert report.failed_requests == ()


def test_list_manifestation_options_records_skipped_source_lanes(tmp_path) -> None:
    tree_notice = tmp_path / "tree.xml"
    tree_notice.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NOTICE>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/no-language</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc-no-language.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/eng</VALUE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>eng</IDENTIFIER></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION/>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE/></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="xhtml">
    <URI><VALUE>http://example.test/doc.xhtml</VALUE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>http://example.test/item.xhtml</VALUE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""",
        encoding="utf-8",
    )
    diagnostics: list[dict[str, Any]] = []

    options = cellar.list_manifestation_options(tree_notice, diagnostics_out=diagnostics)

    assert len(options) == 1
    assert options[0]["language"] == "ENG"
    assert options[0]["manifestation_uri"]["value"] == "http://example.test/doc.xhtml"
    assert [row["rule_id"] for row in diagnostics] == [
        "eu_cellar_manifestation_option_skipped",
        "eu_cellar_manifestation_option_skipped",
        "eu_cellar_manifestation_option_skipped",
    ]
    assert [row["detail"]["reason_code"] for row in diagnostics] == [
        "missing_expression_language",
        "missing_manifestation_uri_node",
        "empty_manifestation_uri",
    ]
    assert all(row["family"] == "source_pathology" for row in diagnostics)
    assert all(row["phase"] == "acquisition" for row in diagnostics)
    assert all(row["strict_disposition"] == "block" for row in diagnostics)


def test_select_manifestation_option_threads_skipped_source_lanes(tmp_path) -> None:
    tree_notice = tmp_path / "tree.xml"
    tree_notice.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NOTICE>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/no-language</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc-no-language.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/eng</VALUE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>eng</IDENTIFIER></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="xhtml">
    <URI><VALUE>http://example.test/doc.xhtml</VALUE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>http://example.test/item.xhtml</VALUE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""",
        encoding="utf-8",
    )
    diagnostics: list[dict[str, Any]] = []

    option = cellar.select_manifestation_option(
        tree_notice,
        "eng",
        "xhtml",
        diagnostics_out=diagnostics,
    )

    assert option["items"][0]["uri"]["value"] == "http://example.test/item.xhtml"
    assert [row["detail"]["reason_code"] for row in diagnostics] == [
        "missing_expression_language",
    ]


def test_fetch_manifestation_outputs_acquisition_diagnostics(monkeypatch, tmp_path, capsys) -> None:
    tree_notice = tmp_path / "tree.xml"
    tree_notice.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NOTICE>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/no-language</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc-no-language.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/eng</VALUE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>eng</IDENTIFIER></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="xhtml">
    <URI><VALUE>http://example.test/doc.xhtml</VALUE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>http://example.test/item.xhtml</VALUE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cellar,
        "_request_url",
        lambda url, timeout_s, accept: (
            b"<html><body>ok</body></html>",
            {"url": url, "content_type": "application/xhtml+xml"},
        ),
    )

    exit_code = cellar.fetch_manifestation(
        Namespace(
            tree_notice=tree_notice,
            language="eng",
            format="xhtml",
            out=tmp_path / "out.xhtml",
            timeout=10,
            accept="application/xhtml+xml",
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["acquisition_diagnostic_count"] == 1
    assert payload["acquisition_diagnostics"][0]["detail"]["reason_code"] == "missing_expression_language"
    assert json.loads((tmp_path / "out.xhtml.meta.json").read_text(encoding="utf-8"))["acquisition_diagnostic_count"] == 1


def test_fetch_manifestation_failure_prints_acquisition_diagnostics(tmp_path, capsys) -> None:
    tree_notice = tmp_path / "tree.xml"
    tree_notice.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NOTICE>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/no-language</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc-no-language.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
</NOTICE>
""",
        encoding="utf-8",
    )

    exit_code = cellar.fetch_manifestation(
        Namespace(
            tree_notice=tree_notice,
            language="eng",
            format="xhtml",
            out=tmp_path / "out.xhtml",
            timeout=10,
            accept="application/xhtml+xml",
        )
    )
    err = capsys.readouterr().err

    assert exit_code == 1
    assert "No manifestation found" in err
    assert '"rule_id": "eu_cellar_manifestation_option_skipped"' in err
    assert '"reason_code": "missing_expression_language"' in err
