from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import blame


def test_blame_sync_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, *, mode: str, quiet: bool = False, compiled_ops_out=None):
        called["statute_id"] = statute_id
        called["mode"] = mode
        called["quiet"] = quiet
        return SimpleNamespace(
            title="Quiet blame",
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
        )

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)

    blame.main(
        Namespace(
            statute_id="1991/1",
            address=None,
            source=None,
            mode="legal_pit",
        )
    )

    assert called == {"statute_id": "1991/1", "mode": "legal_pit", "quiet": True}
    out = capsys.readouterr().out
    assert "Statute : 1991/1" in out


def test_blame_main_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    blame.main(
        Namespace(
            statute_id="1978/38",
            address="chapter:12/section:1e",
            source=None,
            mode="legal_pit",
        )
    )

    out = capsys.readouterr().out

    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out
