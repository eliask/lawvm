from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lawvm.uk_legislation import bootstrap
from lawvm.uk_legislation.bootstrap import (
    _openapi_server_urls,
    _parse_effect_entries,
    build_effects_graph,
    fetch_effects_pages,
    fetch_manifest,
)


def test_openapi_server_urls_extracts_server_url_metadata() -> None:
    urls = _openapi_server_urls(
        [{"url": "https://www.legislation.gov.uk"}, {"description": "missing url"}],
        source=Path("uk/openapi/spec.yaml"),
    )

    assert urls == ["https://www.legislation.gov.uk", ""]


def test_openapi_server_urls_rejects_non_object_entries() -> None:
    with pytest.raises(ValueError, match="non-object entries at indexes: 1, 2"):
        _openapi_server_urls(
            [{"url": "https://www.legislation.gov.uk"}, "silently-dropped-before", 42],
            source=Path("uk/openapi/spec.yaml"),
        )


def test_openapi_server_urls_rejects_non_array_servers_field() -> None:
    with pytest.raises(ValueError, match="servers field did not decode to a JSON array"):
        _openapi_server_urls({"url": "https://www.legislation.gov.uk"}, source=Path("uk/openapi/spec.yaml"))


def test_fetch_manifest_writes_sha256_metadata(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "label": "pilot",
                        "artifacts": [
                            {
                                "url": "https://example.test/source.xml",
                                "path": "uk/pilot/source.xml",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(bootstrap, "_repo_root", lambda: repo)
    monkeypatch.setattr(
        bootstrap,
        "_download",
        lambda url: (b"<xml>source</xml>", f"{url}?final=1"),
    )

    assert fetch_manifest(manifest) == 0

    meta = json.loads((repo / "uk/pilot/source.xml.meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "requested_url": "https://example.test/source.xml",
        "final_url": "https://example.test/source.xml?final=1",
        "bytes": len(b"<xml>source</xml>"),
        "sha256": hashlib.sha256(b"<xml>source</xml>").hexdigest(),
    }


def test_fetch_effects_pages_writes_sha256_metadata(monkeypatch, tmp_path: Path) -> None:
    feed = tmp_path / "seed.feed"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation">
  <leg:totalPages>2</leg:totalPages>
  <link rel="self" href="https://example.test/effects?page=1"/>
</feed>
""",
        encoding="utf-8",
    )
    out_dir = tmp_path / "pages"
    monkeypatch.setattr(
        bootstrap,
        "_download",
        lambda url: (b"<feed>page 2</feed>", f"{url}&resolved=1"),
    )

    assert fetch_effects_pages(feed, out_dir) == 0

    meta = json.loads((out_dir / "page-2.feed.meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "requested_url": "https://example.test/effects?page=2",
        "final_url": "https://example.test/effects?page=2&resolved=1",
        "bytes": len(b"<feed>page 2</feed>"),
        "sha256": hashlib.sha256(b"<feed>page 2</feed>").hexdigest(),
    }


def test_parse_effect_entries_records_entry_without_effect(tmp_path: Path) -> None:
    feed = tmp_path / "effects.feed"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation"
      xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <leg:page>1</leg:page>
  <leg:totalPages>1</leg:totalPages>
  <openSearch:totalResults>1</openSearch:totalResults>
  <entry>
    <id>missing-effect-entry</id>
    <title>Editorial feed item without effect metadata</title>
  </entry>
</feed>
""",
        encoding="utf-8",
    )
    diagnostics: list[dict[str, object]] = []

    summary = _parse_effect_entries(feed, diagnostics_out=diagnostics)

    assert summary["effects"] == []
    assert diagnostics == [
        {
            "rule_id": "uk_effect_feed_entry_without_effect_skipped",
            "kind": "uk_effect_feed_entry_without_effect_skipped",
            "family": "source_pathology",
            "phase": "acquisition",
            "source": str(feed),
            "reason": "UK affected-statute feed entry was skipped because it did not contain a metadata Effect element",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "detail": {
                "entry_index": 0,
                "entry_id": "missing-effect-entry",
                "entry_title": "Editorial feed item without effect metadata",
            },
        }
    ]


def test_build_effects_graph_threads_effect_feed_source_diagnostics(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    current_xml = tmp_path / "current.xml"
    current_xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link rel="http://purl.org/dc/terms/hasVersion" title="2024-01-01" href="/id/version/2024-01-01"/>
</feed>
""",
        encoding="utf-8",
    )
    feed = tmp_path / "effects.feed"
    feed.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation"
      xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <leg:page>1</leg:page>
  <leg:totalPages>1</leg:totalPages>
  <openSearch:totalResults>1</openSearch:totalResults>
  <entry>
    <id>missing-effect-entry</id>
    <title>Editorial feed item without effect metadata</title>
  </entry>
</feed>
""",
        encoding="utf-8",
    )

    assert build_effects_graph(current_xml, [feed]) == 0

    graph = json.loads(capsys.readouterr().out)
    assert graph["source_diagnostics"][0]["rule_id"] == "uk_effect_feed_entry_without_effect_skipped"
    assert graph["source_diagnostics"][0]["strict_disposition"] == "block"
