from __future__ import annotations

import json
from pathlib import Path
import sys
import types
from argparse import Namespace
from types import SimpleNamespace
import pytest

from lawvm.tools import cli
from lawvm.tools import eu_replay


def test_cli_parser_accepts_eu_replay_command() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "eu-replay",
            "32016R0679",
            "--pit-date",
            "2026-01-01",
            "--json",
            "--format",
            "markdown",
        ]
    )
    assert args.command == "eu-replay"
    assert args.celex == "32016R0679"
    assert args.pit_date == "2026-01-01"
    assert args.json is True
    assert args.format == "markdown"


def test_cli_parser_shows_default_cache_dir_in_help(capsys) -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["eu-replay", "--help"])
    help_text = capsys.readouterr().out
    assert "eu-replay" in help_text
    assert "--cache-dir" in help_text
    args = parser.parse_args(["eu-replay", "32016R0679"])
    assert args.cache_dir == ".cache/eu_replay"


def test_eu_replay_tool_prints_summary(monkeypatch, capsys) -> None:
    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        assert celex == "32016R0679"
        assert cutoff_date == "2026-01-01"
        return SimpleNamespace(
            celex="32016R0679",
            ops=[1, 2, 3],
            adjudications=[
                SimpleNamespace(
                    kind="eu_replay_target_not_found",
                    message="missed",
                    source_statute="2026/1",
                    op_id="op-1",
                    detail={"action": "replace", "target": "section:9"},
                ),
                SimpleNamespace(
                    kind="text_duplication_warning",
                    message="dup",
                    source_statute="32016R0679",
                    op_id="",
                    detail={"phase": "replay_fold"},
                ),
            ],
            timelines={"section:1": None},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir

        replay_statute = _fake_replay_statute
    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date="2026-01-01",
            cache_dir=".tmp",
            json=False,
        )
    )

    output = capsys.readouterr().out
    assert "EU Replay" in output
    assert "CELEX: 32016R0679" in output
    assert "Ops: 3" in output
    assert "Adjudications: 2" in output
    assert "Kinds:" in output
    assert "eu_replay_target_not_found: 1" in output
    assert "text_duplication_warning: 1" in output
    assert "phase=replay_fold" in output


def test_eu_replay_tool_supports_json(monkeypatch, capsys) -> None:
    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        return SimpleNamespace(
            celex="32016R0679",
            ops=[1],
            adjudications=[
                SimpleNamespace(
                    kind="text_duplication_warning",
                    message="dup",
                    source_statute="32016R0679",
                    op_id="op-dup",
                    detail={"phase": "materialized"},
                )
            ],
            timelines={},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir

        replay_statute = _fake_replay_statute
    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            cache_dir=".tmp",
            format="json",
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["celex"] == "32016R0679"
    assert payload["ops"] == 1
    assert payload["adjudications"] == 1
    assert payload["text_duplication_phases"] == ["materialized"]
    assert payload["adjudications_data"][0]["op_id"] == "op-dup"


def test_eu_replay_tool_forwards_cache_dir(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        assert celex == "32016R0679"
        assert cutoff_date is None
        return SimpleNamespace(
            celex="32016R0679",
            ops=[],
            adjudications=[],
            timelines={},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir) -> None:
            captured["cache_dir"] = cache_dir

        replay_statute = _fake_replay_statute

    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            cache_dir="/tmp/custom-eu-cache",
            format="text",
            json=False,
        )
    )

    captured_cache_dir = captured.get("cache_dir")
    assert isinstance(captured_cache_dir, Path)
    assert str(captured_cache_dir) == "/tmp/custom-eu-cache"
    assert capsys.readouterr().out.startswith("EU Replay")


def test_eu_replay_tool_default_cache_dir(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        return SimpleNamespace(
            celex="32016R0679",
            ops=[],
            adjudications=[],
            timelines={},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir) -> None:
            captured["cache_dir"] = cache_dir

        replay_statute = _fake_replay_statute

    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            format="text",
            json=False,
        )
    )

    captured_cache_dir = captured.get("cache_dir")
    assert isinstance(captured_cache_dir, Path)
    assert captured_cache_dir == Path(".cache/eu_replay")
    assert "EU Replay" in capsys.readouterr().out


def test_eu_replay_tool_supports_markdown_format(monkeypatch, capsys) -> None:
    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        return SimpleNamespace(
            celex="32016R0679",
            ops=[1, 2],
            adjudications=[
                SimpleNamespace(
                    kind="eu_replay_target_not_found",
                    message="missing",
                    source_statute="2026/2",
                    op_id="op-1",
                    detail={"action": "replace"},
                )
            ],
            timelines={},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir

        replay_statute = _fake_replay_statute
    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            cache_dir=".tmp",
            format="markdown",
            json=False,
        )
    )
    output = capsys.readouterr().out
    assert "# EU Replay Report" in output
    assert "| Metric | Value |" in output
    assert "| eu_replay_target_not_found | 1 |" in output


def test_eu_replay_tool_json_wins_over_text_or_markdown_format(monkeypatch, capsys) -> None:
    def _fake_replay_statute(_self, celex: str, cutoff_date: str | None = None):
        return SimpleNamespace(
            celex="32016R0679",
            ops=[1, 2],
            adjudications=[
                SimpleNamespace(
                    kind="eu_replay_target_not_found",
                    message="missing",
                    source_statute="2026/2",
                    op_id="op-1",
                    detail={"action": "replace"},
                )
            ],
            timelines={},
            replayed=object(),
        )

    class _FakePipeline:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir

        replay_statute = _fake_replay_statute

    monkeypatch.setitem(sys.modules, "lawvm.eu.pipeline", types.SimpleNamespace(EUReplayPipeline=_FakePipeline))

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            cache_dir=".tmp",
            format="text",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["celex"] == "32016R0679"
    assert payload["adjudications"] == 1

    eu_replay.main(
        Namespace(
            command="eu-replay",
            celex="32016R0679",
            pit_date=None,
            cache_dir=".tmp",
            format="markdown",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["celex"] == "32016R0679"
    assert payload["adjudications"] == 1


def test_serialize_adjudication_tolerates_non_dict_detail() -> None:
    payload = eu_replay._serialize_adjudication(
        SimpleNamespace(
            kind="k",
            message="m",
            source_statute="s",
            op_id="o",
            detail=["unexpected", "shape"],
        )
    )
    assert payload["detail"] == {"value": "['unexpected', 'shape']"}


def test_cli_dispatches_eu_replay(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def _fake_main(args: Namespace) -> None:
        captured["celex"] = args.celex
        captured["pit_date"] = args.pit_date
        print("EU_REPLAY_DISPATCHED")

    fake_module = types.SimpleNamespace(main=_fake_main)
    monkeypatch.setitem(sys.modules, "lawvm.tools.eu_replay", fake_module)
    monkeypatch.setattr(sys, "argv", ["lawvm", "eu-replay", "32016R0679", "--pit-date", "2026-01-01"])

    cli.main()

    assert captured["celex"] == "32016R0679"
    assert captured["pit_date"] == "2026-01-01"
    assert "EU_REPLAY_DISPATCHED" in capsys.readouterr().out
