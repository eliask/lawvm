"""Tests for the `lawvm replay-all` full-corpus replay subcommand.

The subcommand is a measurement/ops command: it enumerates EVERY statute id in
the full farchive (the same source as ``export-projections --corpus all``) and
runs the production FI replay pipeline once per statute. These tests prove the
parser wiring and that the ``main`` loop enumerates, replays, and counts
failures without aborting — using fakes so no real corpus is required.
"""

from __future__ import annotations

from argparse import Namespace
from typing import Any, List

import pytest

from lawvm.tools import cli, replay_all


def test_parser_accepts_replay_all_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        ["replay-all", "--workers", "4", "--limit", "10", "--mode", "legal_pit"]
    )
    assert args.command == "replay-all"
    assert args.workers == 4
    assert args.limit == 10
    assert args.mode == "legal_pit"


def test_parser_replay_all_defaults() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["replay-all"])
    assert args.command == "replay-all"
    assert args.workers == 1
    assert args.limit is None
    assert args.mode == "official_consolidation"


def test_main_enumerates_and_replays_with_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus_ids = [f"2020/{n}" for n in range(1, 21)]
    monkeypatch.setattr(replay_all, "_enumerate_statute_ids", lambda: list(corpus_ids))

    replayed: List[str] = []

    def fake_replay_one(statute_id: str, mode: str) -> tuple[str, bool, str]:
        replayed.append(statute_id)
        return statute_id, True, ""

    monkeypatch.setattr(replay_all, "_replay_one", fake_replay_one)

    args = Namespace(workers=1, limit=5, mode="official_consolidation", jurisdiction="fi")
    rc = replay_all.main(args)

    assert rc == 0
    # Limit honored: only first 5 of the 20-statute corpus were replayed.
    assert replayed == corpus_ids[:5]


def test_main_full_corpus_no_amendment_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zero-amendment statutes must NOT be filtered out: every enumerated id is
    # passed to the replay pipeline.
    corpus_ids = [f"1999/{n}" for n in range(1, 8)]
    monkeypatch.setattr(replay_all, "_enumerate_statute_ids", lambda: list(corpus_ids))

    seen: List[str] = []
    monkeypatch.setattr(
        replay_all,
        "_replay_one",
        lambda sid, mode: (seen.append(sid), (sid, True, ""))[1],
    )

    args = Namespace(workers=1, limit=None, mode="official_consolidation", jurisdiction="fi")
    rc = replay_all.main(args)

    assert rc == 0
    assert seen == corpus_ids


def test_main_counts_failures_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus_ids = [f"2001/{n}" for n in range(1, 11)]
    monkeypatch.setattr(replay_all, "_enumerate_statute_ids", lambda: list(corpus_ids))

    def flaky_replay(statute_id: str, mode: str) -> tuple[str, bool, str]:
        # Every 3rd statute "fails" — the run must not abort.
        n = int(statute_id.split("/")[1])
        if n % 3 == 0:
            return statute_id, False, "boom"
        return statute_id, True, ""

    monkeypatch.setattr(replay_all, "_replay_one", flaky_replay)

    args = Namespace(workers=1, limit=None, mode="official_consolidation", jurisdiction="fi")
    rc = replay_all.main(args)

    # Failures are counted and tolerated; the command still returns success.
    assert rc == 0


def test_main_rejects_non_fi_jurisdiction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay_all, "_enumerate_statute_ids", lambda: ["2020/1"])
    args = Namespace(workers=1, limit=None, mode="official_consolidation", jurisdiction="uk")
    rc = replay_all.main(args)
    assert rc == 2


def test_replay_one_uses_production_replay_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    # _replay_one must drive the production replay_xml entrypoint through the
    # typed request/sink boundary (call_replay_xml), not a bespoke path.
    import lawvm.finland.replay_entrypoint as entry
    import lawvm.finland.replay_request as rr

    captured: dict[str, Any] = {}

    def fake_call_replay_xml(func: Any, *, request: Any, sinks: Any = None) -> Any:
        captured["func"] = func
        captured["parent_id"] = request.parent_id
        captured["mode"] = request.mode
        return object()

    monkeypatch.setattr(rr, "call_replay_xml", fake_call_replay_xml)

    sid, ok, status = replay_all._replay_one("2020/738", "official_consolidation")

    assert ok is True
    assert status == ""
    assert captured["func"] is entry.replay_xml
    assert captured["parent_id"] == "2020/738"
    assert captured["mode"] == "official_consolidation"
