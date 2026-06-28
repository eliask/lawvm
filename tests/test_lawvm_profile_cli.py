"""Smoke tests for the `lawvm profile` CLI subcommand (AGENTS.md §2.7).

These tests pin the structural wiring only:
- argparse accepts the documented flags,
- dispatch reaches the production FI compile + replay entrypoint
  (``call_replay_xml`` over ``lawvm.finland.replay_entrypoint.replay_xml``)
  under cProfile,
- a pstats dump is written when --out is supplied, and the file exists,
- the stdout cumtime summary carries the expected pstats header.

They deliberately do NOT pin wall-time numbers, counts, or per-statute corpus
state (AGENTS.md §2.9 — do not pin fragile counts). The replay entrypoint is
faked so no real farchive corpus is required.
"""
from __future__ import annotations

import cProfile
import pstats
from argparse import Namespace
from typing import Any

import pytest

from lawvm.tools import cli, profile as profile_mod


def test_parser_accepts_profile_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "profile",
            "2006/1299",
            "--as-of",
            "2024-01-01",
            "--out",
            "out.pstats",
            "--top",
            "10",
            "--mode",
            "legal_pit",
            "--strict",
        ]
    )
    assert args.command == "profile"
    assert args.statute_id == "2006/1299"
    assert args.as_of == "2024-01-01"
    assert args.out == "out.pstats"
    assert args.top == 10
    assert args.mode == "legal_pit"
    assert args.strict is True


def test_parser_profile_defaults_match_replay() -> None:
    """Defaults must mirror `lawvm replay`: legal_pit mode, top=25 summary rows."""
    parser = cli._build_parser()
    args = parser.parse_args(["profile", "2006/1299", "--as-of", "2024-01-01"])
    assert args.command == "profile"
    assert args.mode == "legal_pit"
    assert args.top == 25
    assert args.out is None
    assert args.strict is False


def test_profile_rejects_non_fi_jurisdiction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "-j",
            "uk",
            "profile",
            "ukpga/1998/42",
            "--as-of",
            "2024-01-01",
        ]
    )
    with pytest.raises(SystemExit) as exc:
        profile_mod.main(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "lawvm profile does not yet support -j uk" in err


def _stub_replay_result() -> Any:
    """Minimal ReplayResult stub — the profile command never inspects the value."""
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.finland.statute import ReplayResult, ReplayState, StatuteContext

    body = IRNode(kind=IRNodeKind.BODY)
    ctx = StatuteContext(
        id="2006/1299",
        title="Profiled statute",
        base_ir=body,
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        temporal_events=(),
        migration_events=(),
        source_adjudication=None,
        source_effects=(),
        effect_relations=(),
        effect_lifecycle_events=(),
    )
    return ReplayResult(ctx=ctx, products=products, findings=())


def _install_typed_replay_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Install a fake ``replay_xml`` that matches the typed request/sink call shape.

    Records the request so the test can assert the production boundary
    (``call_replay_xml`` over ``lawvm.finland.replay_entrypoint.replay_xml``)
    was driven exactly the way ``lawvm replay`` drives it.
    """
    captured: dict[str, Any] = {}

    def fake_replay_xml(*, request: Any, sinks: Any = None) -> Any:
        captured["request"] = request
        captured["sinks"] = sinks
        # Exercise a small amount of real Python work so cProfile has frames
        # to attribute cumtime to beyond the bare stub return.
        total = 0
        for i in range(200):
            total += i
        captured["_work"] = total
        return _stub_replay_result()

    monkeypatch.setattr(
        "lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml
    )
    return captured


def test_profile_main_drives_production_replay_xml_under_cprofile(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end dispatch through ``cli.main()`` against a fake replay_xml.

    Asserts:
    - the production boundary (call_replay_xml over replay_entrypoint.replay_xml)
      is driven with the statute_id / as_of / mode / strict_profile we requested,
    - a pstats dump is written when --out is supplied and the file exists,
    - the stdout summary contains the standard pstats header.
    """
    captured = _install_typed_replay_fake(monkeypatch)

    out_path = tmp_path / "statute.pstats"
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "profile",
            "2006/1299",
            "--as-of",
            "2024-01-01",
            "--out",
            str(out_path),
            "--top",
            "10",
            "--strict",
        ],
    )
    cli.main()

    # The production boundary was driven with the expected request shape.
    request = captured["request"]
    assert request.parent_id == "2006/1299"
    assert request.as_of == "2024-01-01"
    assert request.mode == "legal_pit"
    assert request.quiet is True  # tools that request profile must not leak replay chatter
    # --strict maps onto the FINLAND_INGESTION_V1 profile (mirrors `lawvm explain`).
    assert request.strict_profile is not None

    # pstats dump written and loadable.
    assert out_path.exists()
    stats = pstats.Stats(str(out_path))
    assert isinstance(stats, pstats.Stats)

    # stdout summary carries the standard pstats header — the structural invariant
    # that the cumtime summary was printed (NOT a fragile count or wall-time pin).
    out = capsys.readouterr().out
    assert "ncalls" in out
    assert "cumtime" in out


def test_profile_main_without_out_still_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When --out is omitted, only the cumtime summary is printed to stdout."""
    _install_typed_replay_fake(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "profile",
            "2006/1299",
            "--as-of",
            "2024-01-01",
            "--top",
            "5",
        ],
    )
    cli.main()

    out = capsys.readouterr().out
    assert "ncalls" in out
    assert "cumtime" in out
    # The summary was actually bounded by --top=5; we don't assert the exact row
    # count (would be fragile), only that the standard pstats header is present.


def test_profile_main_dispatch_isolates_replay_under_profiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cProfile.Profile object is enabled only around the production
    ``call_replay_xml`` invocation — NOT around the pstats bookkeeping that
    follows, and NOT around the invoke chain itself. This is the §2.7
    synchronous-single-statute invariant: profile the compile + replay path,
    not the CLI plumbing or the summary dump.

    Note: ``pstats.Stats(prof)`` itself calls ``prof.disable()`` via
    ``create_stats`` during the summary/dump step (CPython behaviour), so the
    total ``disable()`` count is not a stable structural invariant. What is
    stable and is what we actually mean by "framed the replay call" is:
    (1) the profiler is ENABLED exactly once and that enable happens before the
        production replay call,
    (2) at the moment the production replay call runs, the profiler is enabled,
    (3) after ``main`` returns the profiler is disabled (no leak into later
        caller code).
    """
    enable_calls: list[bool] = []
    disable_calls: list[bool] = []
    enabled_when_replay_called: list[bool] = []
    captured_request: dict[str, Any] = {}

    real_profile_cls = cProfile.Profile

    def prof_is_enabled() -> bool:
        """ Profiler is enabled iff enable count exceeds disable count.
        Uses the recorded call log so it reflects the live enable state, not
        just the recorded post-hoc one. cProfile has no public "is_enabled"
        probe; diffing enable/disable counts is the simplest reliable proxy.
        """
        return sum(enable_calls) - sum(disable_calls) > 0

    class TrackingProfile(real_profile_cls):  # type: ignore[misc, valid-type]
        def enable(self, subcalls: bool = True, builtins: bool = True) -> None:
            enable_calls.append(True)
            super().enable(subcalls=subcalls, builtins=builtins)

        def disable(self) -> None:
            disable_calls.append(True)
            super().disable()

    monkeypatch.setattr(profile_mod.cProfile, "Profile", TrackingProfile)

    def observing_replay_xml(*, request: Any, sinks: Any = None) -> Any:
        enabled_when_replay_called.append(prof_is_enabled())
        captured_request["request"] = request
        return _stub_replay_result()

    monkeypatch.setattr(
        "lawvm.finland.replay_entrypoint.replay_xml", observing_replay_xml
    )

    args = Namespace(
        statute_id="2006/1299",
        as_of="2024-01-01",
        out=None,
        top=5,
        mode="legal_pit",
        strict=False,
        jurisdiction="fi",
    )
    profile_mod.main(args)

    # (1) Profiler enabled exactly once.
    assert len(enable_calls) == 1
    # (2) At the production replay invocation moment, the profiler was enabled.
    assert enabled_when_replay_called == [True]
    # (3) After main() returns, the profiler is not left running.
    assert sum(enable_calls) - sum(disable_calls) <= 0
    # And the production boundary was actually driven with the expected request.
    assert captured_request["request"].parent_id == "2006/1299"
