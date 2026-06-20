from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import verify_chain


def test_verify_chain_main_suppresses_raw_replay_chatter_for_1978_38(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),),
    )

    class FakeCorpus:
        def read_source(self, _sid: str) -> bytes:
            return b"<root/>"

    def fake_process_muutoslaki(request):
        print("COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED")
        print("REPLACE 10 luku otsikko → FAILED")
        print("INSERT 10 luku 16 § 2 mom → FAILED")
        return SimpleNamespace(output=request.state)

    monkeypatch.setattr("lawvm.tools.verify_chain.get_corpus", lambda: FakeCorpus())
    monkeypatch.setattr(
        "lawvm.tools.verify_chain.StatuteContext.from_xml",
        lambda _xml, _postprocessor: SimpleNamespace(base_ir=base_ir),
    )
    monkeypatch.setattr(
        "lawvm.tools.verify_chain._resolve_applicable_amendment_records",
        lambda _sid, _mode: ([{"statute_id": "2001/1"}], None, None),
    )
    monkeypatch.setattr("lawvm.tools.verify_chain._build_pit_map", lambda _sid: {})
    monkeypatch.setattr("lawvm.tools.verify_chain.process_muutoslaki", fake_process_muutoslaki)

    verify_chain.main(
        Namespace(
            sids=["1991/1"],
            no_html=True,
            output=str(tmp_path),
        )
    )

    captured = capsys.readouterr()
    merged = captured.out + captured.err

    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" not in merged
    assert "REPLACE 10 luku otsikko → FAILED" not in merged
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in merged

    payload = json.loads((tmp_path / "1991_1.json").read_text(encoding="utf-8"))
    assert payload["statute_id"] == "1991/1"
    assert payload["total_amendments"] > 0


def test_fetch_html_sections_does_not_create_missing_archive(tmp_path) -> None:
    archive_db = tmp_path / "unused"

    labels, error = verify_chain._fetch_html_sections("2020/369", archive_db=archive_db)

    assert labels == []
    assert "archive fetch error" in error
    assert not archive_db.exists()
