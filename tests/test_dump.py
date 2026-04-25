from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from lawvm.tools import dump


def test_dump_apply_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, *, stop_before: str = "", quiet: bool = False):
        called["statute_id"] = statute_id
        called["stop_before"] = stop_before
        called["quiet"] = quiet
        return SimpleNamespace(
            serialize_text=lambda: "quiet dump text",
        )

    monkeypatch.setattr("lawvm.tools.dump.replay_xml", fake_replay_xml)

    dump.main(
        Namespace(
            statute_id="1991/1",
            after="apply",
            source=None,
            address=None,
            before="1992/1",
        )
    )

    assert called == {"statute_id": "1991/1", "stop_before": "1992/1", "quiet": True}
    out = capsys.readouterr().out
    assert "quiet dump text" in out
