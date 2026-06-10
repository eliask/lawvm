"""Regression tests for the certified transition-graph exporter (Design D).

These tests are hermetic: they inject a synthetic engine (a fixed timeline of
materialized trees plus a small resolved-op stream) so the SQLite assembly,
content-blob dedup, L3 transition diffing, checkpoints, and active_at coverage
are exercised without a populated farchive corpus.

A separate corpus-backed smoke test runs the real exporter on a small Finnish
statute when the corpus is available (skipped otherwise).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.tools import export_transition_graph as etg


# ---------------------------------------------------------------------------
# Synthetic engine fixtures
# ---------------------------------------------------------------------------


def _section(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
    )


def _body(children: List[IRNode]) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


# A three-date timeline:
#   D1 2010-01-01: sections 1,2 (originals)
#   D2 2015-01-01: section 2 amended; section 3 inserted (temporary, expires D3)
#   D3 2020-01-01: section 3 expires/reverts (gone); section 1 unchanged
_TREES_BY_DATE: Dict[str, IRNode] = {
    "2010-01-01": _body([_section("1", "alpha"), _section("2", "beta")]),
    "2015-01-01": _body(
        [_section("1", "alpha"), _section("2", "beta-amended"), _section("3", "temp")]
    ),
    "2020-01-01": _body([_section("1", "alpha"), _section("2", "beta-amended")]),
}
_CHANGE_DATES = sorted(_TREES_BY_DATE)


def _fake_op(action: StructuralAction, target: str, eff: str, expires: str, src: str, seq: int) -> Any:
    source = SimpleNamespace(
        statute_id=src, title=f"Laki {src}", enacted=eff, effective=eff, expires=expires
    )
    return SimpleNamespace(
        op_id=f"op_{target}_{eff}",
        sequence=seq,
        action=action,
        target=SimpleNamespace(__str__=lambda self=None, t=target: t),
        anchor=None,
        destination=None,
        source=source,
        payload=None,
        text_patch=None,
        group_id="",
    )


def _make_bundle() -> etg.ReplayBundle:
    # target stringification: SimpleNamespace can't override __str__ cleanly, so
    # use a tiny address-like shim.
    class _Addr:
        def __init__(self, s: str) -> None:
            self._s = s

        def __str__(self) -> str:
            return self._s

    def op(action: StructuralAction, target: str, eff: str, expires: str, src: str, seq: int) -> Any:
        source = SimpleNamespace(
            statute_id=src, title=f"Laki {src}", enacted=eff, effective=eff, expires=expires
        )
        return SimpleNamespace(
            op_id=f"op_{target}_{eff}",
            sequence=seq,
            action=action,
            target=_Addr(target),
            anchor=None,
            destination=None,
            source=source,
            payload=None,
            text_patch=None,
            group_id="",
        )

    lo_ops = [
        op(StructuralAction.REPLACE, "section:2", "2015-01-01", "", "12/2015", 0),
        op(StructuralAction.INSERT, "section:3", "2015-01-01", "2020-01-01", "12/2015", 1),
    ]

    base_body = _TREES_BY_DATE["2010-01-01"]
    ctx = SimpleNamespace(id="100/2010", title="Testilaki", base_ir=base_body)
    products = SimpleNamespace(
        replay_fold_state=None,
        materialized_state=None,
        temporal_events=(),
        migration_events=(),
    )
    result = SimpleNamespace(ctx=ctx, products=products, title="Testilaki")
    return etg.ReplayBundle(
        statute_id="100/2010",
        engine_id="2010/100",
        title="Testilaki",
        result=result,
        lo_ops=lo_ops,
        timelines={},
        change_dates=list(_CHANGE_DATES),
    )


@pytest.fixture()
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _make_bundle()

    def fake_run_engine_replay(_statute_id: str) -> etg.ReplayBundle:
        return bundle

    def fake_materialize(_bundle: etg.ReplayBundle, as_of: str) -> IRNode:
        return _TREES_BY_DATE[as_of]

    class _FakeCorpus:
        def read_amendment(self, _engine_id: str) -> bytes:
            return b""

    monkeypatch.setattr(etg, "run_engine_replay", fake_run_engine_replay)
    monkeypatch.setattr(etg, "materialize_oracle_tree", fake_materialize)
    monkeypatch.setattr(
        "lawvm.finland.corpus._get_corpus_store", lambda: _FakeCorpus()
    )


# ---------------------------------------------------------------------------
# Hermetic exporter tests
# ---------------------------------------------------------------------------


def test_export_produces_required_tables(patched_engine: None, tmp_path: Path) -> None:
    out = tmp_path / "synth.db"
    stats = etg.export_transition_graph("100/2010", out, quiet=True)

    assert out.exists()
    conn = sqlite3.connect(str(out))
    try:
        # --- meta row present and well-formed ---
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert json.loads(meta["statute_id"]) == "100/2010"
        assert json.loads(meta["schema_version"]) == etg.SCHEMA_VERSION
        assert json.loads(meta["change_dates"]) == _CHANGE_DATES

        # --- at least one checkpoint, one per change-date ---
        cp = conn.execute("SELECT date, tree_hash, active_node_count FROM checkpoints").fetchall()
        assert len(cp) == len(_CHANGE_DATES) >= 1
        assert all(h for (_d, h, _c) in cp)

        # --- content_blobs dedup is working: stored < attempted ---
        n_blobs = conn.execute("SELECT COUNT(*) FROM content_blobs").fetchone()[0]
        assert n_blobs >= 1
        assert stats.n_content_blobs < stats.n_content_blob_inserts_attempted
        assert stats.dedup_ratio > 0.0

        # --- transitions exist with non-null pre/post hashes ---
        trans = conn.execute(
            "SELECT action, pre_hash, post_hash FROM transitions"
        ).fetchall()
        assert len(trans) >= 1
        for action, pre, post in trans:
            # at least one of pre/post must be a real hash; both null is invalid
            assert pre is not None and post is not None
            assert (pre != "") or (post != "")

        # --- active_at covers every change-date ---
        dates_with_active = {
            r[0] for r in conn.execute("SELECT DISTINCT date FROM active_at").fetchall()
        }
        assert dates_with_active == set(_CHANGE_DATES)

        # --- active_at content hashes reference real blobs ---
        orphan = conn.execute(
            "SELECT COUNT(*) FROM active_at "
            "WHERE content_hash NOT IN (SELECT content_hash FROM content_blobs)"
        ).fetchone()[0]
        assert orphan == 0

        # --- source_artifacts include the base statute ---
        statutes = conn.execute(
            "SELECT source_id FROM source_artifacts WHERE kind='statute'"
        ).fetchall()
        assert ("100/2010",) in statutes
    finally:
        conn.close()


def test_export_captures_insert_and_expiry_transitions(
    patched_engine: None, tmp_path: Path
) -> None:
    out = tmp_path / "synth2.db"
    etg.export_transition_graph("100/2010", out, quiet=True)
    conn = sqlite3.connect(str(out))
    try:
        # section:3 is inserted at 2015-01-01 (set_subtree, created flag) and
        # removed at 2020-01-01 (delete_subtree, removed flag).
        rows = conn.execute(
            "SELECT effective_date, action, flags FROM transitions "
            "WHERE target_address='section:3' ORDER BY sequence"
        ).fetchall()
        actions = {(d, a) for (d, a, _f) in rows}
        assert ("2015-01-01", "set_subtree") in actions
        assert ("2020-01-01", "delete_subtree") in actions
        # the delete carries the removed flag
        del_flags = [
            json.loads(f) for (d, a, f) in rows if d == "2020-01-01"
        ]
        assert any(fl.get("removed") for fl in del_flags)
    finally:
        conn.close()


def test_l2_sidecar_emitted(patched_engine: None, tmp_path: Path) -> None:
    out = tmp_path / "synth3.db"
    etg.export_transition_graph("100/2010", out, quiet=True)
    sidecar = out.with_suffix(out.suffix + ".l2.json")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["statute_id"] == "100/2010"
    assert data["change_dates"] == _CHANGE_DATES
    assert data["base_body"]["kind"] == str(IRNodeKind.BODY)
    # ops carry effective/expires for L2 temporal replay
    assert any(op["expires"] for op in data["ops"])
    # oracle checkpoints embedded for self-scoring
    assert len(data["oracle_checkpoints"]) == len(_CHANGE_DATES)


def test_canonical_id_roundtrip() -> None:
    assert etg._canonical_statute_id("2004/301") == "301/2004"
    assert etg._canonical_statute_id("301/2004") == "301/2004"
    assert etg._engine_statute_id("301/2004") == "2004/301"
    assert etg._engine_statute_id("2004/301") == "2004/301"


def test_structural_hash_sensitive_to_text_and_structure() -> None:
    a = _section("1", "alpha")
    b = _section("1", "beta")
    c = IRNode(kind=IRNodeKind.SECTION, label="1", children=())
    assert etg.structural_subtree_hash(a) != etg.structural_subtree_hash(b)
    assert etg.structural_subtree_hash(a) != etg.structural_subtree_hash(c)
    assert etg.structural_subtree_hash(None) == ""


# ---------------------------------------------------------------------------
# Corpus-backed smoke test (skipped when no farchive is configured)
# ---------------------------------------------------------------------------


def _corpus_available() -> bool:
    db = os.environ.get("LAWVM_FARCHIVE_DB", "data/finlex.farchive")
    try:
        return Path(db).stat().st_size > 1_000_000  # >1MB ~= populated
    except OSError:
        return False


@pytest.mark.skipif(not _corpus_available(), reason="populated finlex farchive not available")
def test_corpus_backed_export_small_statute(tmp_path: Path) -> None:
    # 2000/1005 (säädös 1005/2000) is a small asetus with a handful of ops.
    out = tmp_path / "1005-2000.db"
    stats = etg.export_transition_graph("1005/2000", out, quiet=True)
    assert out.exists()
    assert stats.n_checkpoints >= 1
    assert stats.n_change_dates >= 1
    assert stats.n_content_blobs >= 1

    conn = sqlite3.connect(str(out))
    try:
        # active_at covers every change-date
        dates_active = {
            r[0] for r in conn.execute("SELECT DISTINCT date FROM active_at").fetchall()
        }
        change_dates = json.loads(
            conn.execute("SELECT value FROM meta WHERE key='change_dates'").fetchone()[0]
        )
        assert dates_active == set(change_dates)
        # every checkpoint has a non-empty engine tree hash
        cp = conn.execute("SELECT tree_hash FROM checkpoints").fetchall()
        assert all(h for (h,) in cp)
        # transitions have non-null pre/post
        bad = conn.execute(
            "SELECT COUNT(*) FROM transitions WHERE pre_hash IS NULL OR post_hash IS NULL"
        ).fetchone()[0]
        assert bad == 0
    finally:
        conn.close()
