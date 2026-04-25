from __future__ import annotations

import json
from argparse import Namespace


from lawvm.tools.no_source_excerpt import main as no_source_excerpt_main


def test_no_source_excerpt_auto_current_json(monkeypatch, capsys, tmp_path) -> None:
    def fake_current(source_id, data_dir):
        assert source_id == "no/lov/2024-01-01-1"
        assert data_dir == tmp_path
        return b"alpha needle beta needle gamma"

    monkeypatch.setattr("lawvm.norway.sources.load_no_current_bytes", fake_current)
    monkeypatch.setattr("lawvm.norway.sources.load_no_original_lti_bytes", lambda *_: None)
    monkeypatch.setattr("lawvm.norway.sources.load_no_amendment_bytes", lambda *_: None)

    no_source_excerpt_main(
        Namespace(
            source_id="no/lov/2024-01-01-1",
            needles=["needle"],
            data_dir=str(tmp_path),
            mode="auto",
            context=5,
            max_hits=3,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_source_kind"] == "current"
    assert payload["needle_count"] == 1
    hits = payload["needles"][0]["hits"]
    assert payload["needles"][0]["match_count"] == 2
    assert hits[0]["offset"] == 6
    assert hits[0]["excerpt"].startswith("...")
    assert "needle" in hits[0]["excerpt"]


def test_no_source_excerpt_explicit_original_text(monkeypatch, capsys, tmp_path) -> None:
    def fake_original(source_id, data_dir):
        assert source_id == "no/lov/2024-01-01-1"
        assert data_dir == tmp_path
        return b"prefix needle suffix"

    monkeypatch.setattr("lawvm.norway.sources.load_no_current_bytes", lambda *_: None)
    monkeypatch.setattr("lawvm.norway.sources.load_no_original_lti_bytes", fake_original)
    monkeypatch.setattr("lawvm.norway.sources.load_no_amendment_bytes", lambda *_: None)

    no_source_excerpt_main(
        Namespace(
            source_id="no/lov/2024-01-01-1",
            needles=["needle"],
            data_dir=str(tmp_path),
            mode="original",
            context=3,
            max_hits=1,
            json=False,
        )
    )

    output = capsys.readouterr().out
    assert "resolved kind       : original" in output
    assert "needle: needle (matches=1)" in output
    assert "@7" in output


def test_no_source_excerpt_auto_amendment_bounded_hits(monkeypatch, capsys, tmp_path) -> None:
    def fake_amendment(source_id, data_dir):
        assert source_id == "no/lovtid/2024-01-01-2"
        assert data_dir == tmp_path
        return b"111needle222needle333needle444"

    monkeypatch.setattr("lawvm.norway.sources.load_no_current_bytes", lambda *_: None)
    monkeypatch.setattr("lawvm.norway.sources.load_no_original_lti_bytes", lambda *_: None)
    monkeypatch.setattr("lawvm.norway.sources.load_no_amendment_bytes", fake_amendment)

    no_source_excerpt_main(
        Namespace(
            source_id="no/lovtid/2024-01-01-2",
            needles=["needle"],
            data_dir=str(tmp_path),
            mode="auto",
            context=3,
            max_hits=2,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_source_kind"] == "amendment"
    assert payload["needles"][0]["match_count"] == 2
    assert len(payload["needles"][0]["hits"]) == 2
    assert "needle" in payload["needles"][0]["hits"][0]["excerpt"]
    assert payload["needles"][0]["hits"][0]["excerpt"].endswith("...")
