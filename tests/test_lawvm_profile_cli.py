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


def test_profile_rejects_unsupported_jurisdiction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FI/NZ/US/UK are supported; an unmodelled jurisdiction fails loud."""
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "-j",
            "ee",
            "profile",
            "RT_I_2023_1",
            "--as-of",
            "2024-01-01",
        ]
    )
    with pytest.raises(SystemExit) as exc:
        profile_mod.main(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "lawvm profile does not yet support -j ee" in err


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


# ---------------------------------------------------------------------------
# US / NZ / UK profiler branch smoke tests (#116).
#
# Same discipline as the FI tests above: pin the structural wiring only —
# dispatch reaches the frontend's production single-work entry point under
# cProfile, and the cumtime summary is emitted. The production entry points are
# faked so no real farchive corpus is required (AGENTS.md §2.9 — no fragile
# count / wall-time pins).
# ---------------------------------------------------------------------------


def _assert_pstats_summary_emitted(out: str) -> None:
    """The standard pstats cumtime table header was printed to stdout."""
    assert "ncalls" in out
    assert "cumtime" in out


def test_profile_us_window_dispatches_evaluate_window_under_cprofile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`-j us profile title<T>:<before>-><after>` reaches evaluate_window.

    The window key is parsed into a BenchWindow and passed to the production
    per-window entry point (``lawvm.us_federal.bench.evaluate_window``) under the
    profiler; the archive opener is faked so no real corpus is needed.
    """
    captured: dict[str, Any] = {}

    class _FakeArchive:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_archive = _FakeArchive()

    def fake_open(*, readonly: bool = True) -> Any:
        captured["readonly"] = readonly
        return fake_archive

    def fake_evaluate_window(archive: Any, window: Any) -> Any:
        captured["archive"] = archive
        captured["window"] = window
        total = 0
        for i in range(200):  # give cProfile a frame to attribute cumtime to
            total += i
        captured["_work"] = total
        return object()

    monkeypatch.setattr(
        "lawvm.us_federal.sources.open_us_federal_farchive", fake_open
    )
    monkeypatch.setattr(
        "lawvm.us_federal.bench.evaluate_window", fake_evaluate_window
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "-j",
            "us",
            "profile",
            "title11:2016->2018",
            "--as-of",
            "2000-01-01",
            "--top",
            "5",
        ],
    )
    cli.main()

    window = captured["window"]
    assert (window.title, window.before_year, window.after_year) == (11, 2016, 2018)
    assert window.include is True
    assert captured["archive"] is fake_archive
    assert fake_archive.closed is True  # profiler closes the archive it opened
    _assert_pstats_summary_emitted(capsys.readouterr().out)


def test_profile_us_rejects_malformed_window_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A window key that is not title<T>:<before>-><after> fails loud (exit 2)."""
    args = Namespace(
        statute_id="not-a-window",
        as_of="2000-01-01",
        out=None,
        top=5,
        mode="legal_pit",
        strict=False,
        jurisdiction="us",
    )
    with pytest.raises(SystemExit) as exc:
        profile_mod.main(args)
    assert exc.value.code == 2
    assert "malformed" in capsys.readouterr().err


def test_profile_nz_dispatches_chain_replay_under_cprofile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`-j nz profile <work_id>` reaches build_archived_work_chain_replay.

    The work id is threaded through as ``work_id`` and the NZ farchive path is
    resolved through the shared corpus-store chokepoint (faked here so no real
    corpus is needed). ``--as-of`` is IGNORED on this branch.
    """
    captured: dict[str, Any] = {}

    def fake_resolve(name: str, *, explicit_env: str = "") -> Any:
        from pathlib import Path

        captured["resolve_name"] = name
        return Path("/does/not/matter/nz.farchive"), "fake-rule"

    def fake_chain_replay(db_path: Any, work_id: str, *, families: Any = None) -> Any:
        captured["db_path"] = db_path
        captured["work_id"] = work_id
        captured["families"] = families
        total = 0
        for i in range(200):
            total += i
        captured["_work"] = total
        return object()

    monkeypatch.setattr(
        "lawvm.corpus_store.resolve_farchive_path", fake_resolve
    )
    monkeypatch.setattr(
        "lawvm.new_zealand.chain_replay_corpus.build_archived_work_chain_replay",
        fake_chain_replay,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "-j",
            "nz",
            "profile",
            "act_public_1992_47",
            "--as-of",
            "2000-01-01",
            "--top",
            "5",
        ],
    )
    cli.main()

    assert captured["work_id"] == "act_public_1992_47"
    assert captured["families"] == "all"
    assert captured["resolve_name"] == "nz_legislation.farchive"
    _assert_pstats_summary_emitted(capsys.readouterr().out)


def test_profile_uk_dispatches_compile_and_replay_under_cprofile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`-j uk profile <type>/<year>/<number>` reaches the UK compile + replay path.

    Resolves the enacted-XML locator from the (faked) archive, parses to IR,
    compiles archive-backed ops, and applies them — the same production path
    ``lawvm uk-bench`` runs, all faked so no real corpus is required.
    """
    captured: dict[str, Any] = {}

    class _FakeConn:
        def execute(self, _sql: str, _params: Any) -> Any:
            captured["query_params"] = _params

            class _Cur:
                def fetchall(self_inner) -> list[tuple[str]]:
                    return [("https://legislation.gov.uk/ukpga/1998/42/enacted/data.xml",)]

            return _Cur()

    class _FakeArchive:
        def __init__(self) -> None:
            self._conn = _FakeConn()
            self.closed = False

        def get(self, url: str) -> bytes:
            captured["get_url"] = url
            return b"<Legislation/>"

        def close(self) -> None:
            self.closed = True

    fake_archive = _FakeArchive()

    def fake_farchive(_db_path: Any) -> Any:
        return fake_archive

    def fake_resolve(name: str, *, explicit_env: str = "") -> Any:
        from pathlib import Path

        captured["resolve_name"] = name
        return Path("/does/not/matter/uk.farchive"), "fake-rule"

    def fake_parse_ir(
        _bytes: bytes, *, statute_id: str, version_label: str, source_path: str
    ) -> Any:
        captured["parsed_sid"] = statute_id
        return object()

    class _FakePipeline:
        def __init__(self, _root: Any) -> None:
            pass

        def compile_ops_for_statute(self, sid: str, *, archive: Any) -> list[Any]:
            captured["compiled_sid"] = sid
            captured["compile_archive"] = archive
            return []

    def fake_replay(_ir: Any, _ops: Any) -> Any:
        captured["replayed"] = True
        return object()

    monkeypatch.setattr("farchive.Farchive", fake_farchive)
    monkeypatch.setattr("lawvm.corpus_store.resolve_farchive_path", fake_resolve)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse_ir
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.replay_uk_ops", fake_replay
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "-j",
            "uk",
            "profile",
            "ukpga/1998/42",
            "--as-of",
            "2000-01-01",
            "--top",
            "5",
        ],
    )
    cli.main()

    assert captured["resolve_name"] == "uk_legislation.farchive"
    assert captured["parsed_sid"] == "ukpga/1998/42"
    assert captured["compiled_sid"] == "ukpga/1998/42"
    assert captured["compile_archive"] is fake_archive
    assert captured["replayed"] is True
    assert fake_archive.closed is True  # profiler closes the archive it opened
    _assert_pstats_summary_emitted(capsys.readouterr().out)


def test_profile_uk_fails_loud_when_no_enacted_xml(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A UK act absent from the archive fails loud (exit 2), never profiles empty."""

    class _FakeConn:
        def execute(self, _sql: str, _params: Any) -> Any:
            class _Cur:
                def fetchall(self_inner) -> list[tuple[str]]:
                    return []

            return _Cur()

    class _FakeArchive:
        def __init__(self) -> None:
            self._conn = _FakeConn()

        def close(self) -> None:
            pass

    def fake_farchive(_db_path: Any) -> Any:
        return _FakeArchive()

    def fake_resolve(name: str, *, explicit_env: str = "") -> Any:
        from pathlib import Path

        return Path("/does/not/matter/uk.farchive"), "fake-rule"

    monkeypatch.setattr("farchive.Farchive", fake_farchive)
    monkeypatch.setattr("lawvm.corpus_store.resolve_farchive_path", fake_resolve)

    args = Namespace(
        statute_id="ukpga/9999/999",
        as_of="2000-01-01",
        out=None,
        top=5,
        mode="legal_pit",
        strict=False,
        jurisdiction="uk",
    )
    with pytest.raises(SystemExit) as exc:
        profile_mod.main(args)
    assert exc.value.code == 2
    assert "no enacted XML" in capsys.readouterr().err
