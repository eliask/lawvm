"""Unit tests for the family-agnostic differential-census engine.

Exercises the generalized 4-bucket machinery (match/superset/miss/decline,
partition, miss-shape ranking, totality counting) extracted from the Pilot-A
citation census, on synthetic in-memory plug-points — no corpus.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from lawvm.finland.legal_surface.family_census import (
    CENSUS_BUCKETS,
    CensusUnit,
    classify,
)


def test_classify_buckets() -> None:
    assert classify({"a"}, {"a"}, False) == "match"
    assert classify({"a", "b"}, {"a"}, False) == "superset"
    assert classify({"a"}, {"a", "b"}, False) == "miss"
    # symmetric difference both ways -> conservative miss
    assert classify({"a", "x"}, {"a", "b"}, False) == "miss"
    assert classify(set(), set(), True) == "decline"


def _engine_on(units_by_statute, projections, oracles, *, check_totality=False):
    """Drive the engine with synthetic plug-points (monkeypatching the corpus).

    ``units_by_statute`` maps a synthetic statute id to its CensusUnit list;
    ``projections``/``oracles`` map a unit's text to its key set.
    """
    import lawvm.finland.legal_surface.family_census as fc

    # Patch the corpus access the engine performs (store + decode + archive path)
    # so the test runs entirely in-memory.
    class _FakeStore:
        def list_statute_ids(self):
            return list(units_by_statute)

        def read_source(self, sid):
            return sid.encode()

        def read_amendment(self, sid):  # pragma: no cover - read_source always hits
            return None

    import sys
    import types

    previous_farchive = sys.modules.get("farchive")
    fake_farchive = types.ModuleType("farchive")
    cast(Any, fake_farchive).Farchive = lambda *_a, **_k: object()
    sys.modules["farchive"] = fake_farchive

    # Monkeypatch the three lazily-imported symbols inside run_family_census by
    # injecting fakes into the modules it imports from.
    import lawvm.finland.legal_surface.bundle as bundle_mod
    import lawvm.finland.transparent_store as ts_mod
    import lawvm.tools.parse_bench as pb_mod

    saved = (
        ts_mod.TransparentCorpusStore,
        bundle_mod.decode_body_text,
        pb_mod._archive_path,
    )
    cast(Any, ts_mod).TransparentCorpusStore = lambda *_a, **_k: _FakeStore()
    cast(Any, bundle_mod).decode_body_text = lambda xb: xb.decode()
    cast(Any, pb_mod)._archive_path = lambda: "unused"
    try:
        return fc.run_family_census(
            family="synthetic",
            segment_selector=lambda sid, body: iter(units_by_statute[sid]),
            projection_fn=lambda unit, sid: projections[unit.text],
            oracle_fn=lambda unit, _ctx=None: oracles[unit.text],
            miss_shape_fn=lambda missing, marker: "shape",
            check_totality=check_totality,
        )
    finally:
        (
            ts_mod.TransparentCorpusStore,
            bundle_mod.decode_body_text,
            pb_mod._archive_path,
        ) = saved
        if previous_farchive is None:
            sys.modules.pop("farchive", None)
        else:
            sys.modules["farchive"] = previous_farchive


def test_engine_partition_and_buckets() -> None:
    units = {
        "s1": [
            CensusUnit(text="u_match", parser_lane="L"),
            CensusUnit(text="u_super", parser_lane="L"),
            CensusUnit(text="u_miss", parser_lane="L"),
            CensusUnit(text="u_decl", parser_lane="LD", declined=True),
        ]
    }
    projections = {
        "u_match": {"a"},
        "u_super": {"a", "b"},
        "u_miss": {"a"},
        "u_decl": set(),
    }
    oracles = {
        "u_match": {"a"},
        "u_super": {"a"},
        "u_miss": {"a", "b"},
        "u_decl": set(),
    }
    res = _engine_on(units, projections, oracles)
    assert res.in_scope_units == 4
    assert res.buckets["match"] == 1
    assert res.buckets["superset"] == 1
    assert res.buckets["miss"] == 1
    assert res.buckets["decline"] == 1
    assert res.is_partition()
    assert set(res.buckets) == set(CENSUS_BUCKETS)
    assert res.miss_shape_counts == {"shape": 1}


def test_engine_totality_counting() -> None:
    units = {
        "s1": [
            CensusUnit(text="ok", parser_lane="L", totality_ok=True),
            CensusUnit(text="bad", parser_lane="L", totality_ok=False),
        ]
    }
    proj = {"ok": {"a"}, "bad": {"a"}}
    orc = {"ok": {"a"}, "bad": {"a"}}
    res = _engine_on(units, proj, orc, check_totality=True)
    assert res.totality_violations == 1


def test_engine_threads_oracle_prepare_context_per_statute() -> None:
    # The optional oracle_prepare_fn is called once per statute with (sid, body)
    # and its result is threaded to every oracle_fn call for that statute — the
    # hook the citation family uses to run a WHOLE-STATUTE oracle once.
    import lawvm.finland.legal_surface.family_census as fc

    units = {"7/2025": [CensusUnit(text="u", parser_lane="L")]}
    projections = {"u": {"a"}}
    prepare_calls: list[tuple[str, str]] = []

    class _FakeStore:
        def list_statute_ids(self):
            return list(units)

        def read_source(self, sid):
            return sid.encode()

        def read_amendment(self, sid):
            return None

    import sys
    import types

    previous_farchive = sys.modules.get("farchive")
    fake_farchive = types.ModuleType("farchive")
    cast(Any, fake_farchive).Farchive = lambda *_a, **_k: object()
    sys.modules["farchive"] = fake_farchive

    import lawvm.finland.legal_surface.bundle as bundle_mod
    import lawvm.finland.transparent_store as ts_mod
    import lawvm.tools.parse_bench as pb_mod

    saved = (
        ts_mod.TransparentCorpusStore,
        bundle_mod.decode_body_text,
        pb_mod._archive_path,
    )
    cast(Any, ts_mod).TransparentCorpusStore = lambda *_a, **_k: _FakeStore()
    cast(Any, bundle_mod).decode_body_text = lambda xb: xb.decode()
    cast(Any, pb_mod)._archive_path = lambda: "unused"

    def _oracle_from_context(_unit: CensusUnit, ctx: object) -> set[str]:
        return set(cast(Iterable[str], ctx)) if ctx else set()

    def _prepare(sid: str, body: str) -> object:
        prepare_calls.append((sid, body))
        return {"a"}  # the per-statute oracle context

    try:
        res = fc.run_family_census(
            family="synthetic",
            segment_selector=lambda sid, body: iter(units[sid]),
            projection_fn=lambda unit, sid: projections[unit.text],
            # oracle reads the threaded per-statute context, ignoring the unit
            oracle_fn=_oracle_from_context,
            miss_shape_fn=lambda missing, marker: "shape",
            oracle_prepare_fn=_prepare,
        )
    finally:
        (
            ts_mod.TransparentCorpusStore,
            bundle_mod.decode_body_text,
            pb_mod._archive_path,
        ) = saved
        if previous_farchive is None:
            sys.modules.pop("farchive", None)
        else:
            sys.modules["farchive"] = previous_farchive

    assert prepare_calls == [("7/2025", "7/2025")]
    assert res.buckets["match"] == 1  # projection {a} == oracle-from-context {a}
