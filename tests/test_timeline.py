from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import timeline


def test_timeline_main_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(statute_id: str, *, quiet: bool = False, lo_ops_out=None):
        called["statute_id"] = statute_id
        called["quiet"] = quiet
        if lo_ops_out is not None:
            lo_ops_out.extend([])
        return SimpleNamespace(
            title="Quiet timeline",
            timelines={},
            ctx=SimpleNamespace(base_ir=IRNode(kind=IRNodeKind.BODY, children=())),
        )

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)

    timeline.main(
        Namespace(
            statute_id="1991/1",
            list=False,
            provision=None,
            as_of=None,
            export=None,
            query_type="governing",
        )
    )

    assert called == {"statute_id": "1991/1", "quiet": True}
    out = capsys.readouterr()
    assert "Statute   : 1991/1" in out.out
    assert "Replaying 1991/1..." in out.err
