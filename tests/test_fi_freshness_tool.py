from __future__ import annotations

import logging

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import freshness


def test_replay_section_count_replays_quietly(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class DummyMaster:
        ir = IRNode(kind=IRNodeKind.SECTION)

    def fake_replay_xml(sid: str, **kwargs):
        seen["sid"] = sid
        seen.update(kwargs)
        return DummyMaster()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    count, err = freshness._replay_section_count("2000/1")

    assert err == ""
    assert seen["sid"] == "2000/1"
    assert seen["quiet"] is True


def test_replay_section_count_suppresses_raw_replay_chatter_for_1978_38(capsys) -> None:
    count, err = freshness._replay_section_count("1978/38")
    out = capsys.readouterr().out

    assert err == ""
    assert count >= 0
    assert "Master 1978/38 rehydrated." not in out
    assert "Applying 57 muutoslait..." not in out


def test_replay_section_count_suppresses_replay_warning_logs(monkeypatch, caplog) -> None:
    class DummyMaster:
        ir = IRNode(kind=IRNodeKind.SECTION)

    def fake_replay_xml(_sid: str, **_kwargs):
        logging.getLogger("lawvm.finland.grafter_uncovered").warning("should stay hidden")
        return DummyMaster()

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    with caplog.at_level(logging.WARNING):
        count, err = freshness._replay_section_count("2000/1")

    assert err == ""
    assert "should stay hidden" not in caplog.text
