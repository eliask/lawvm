from __future__ import annotations

import json
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
