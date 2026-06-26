"""Regression tests for the certified transition-graph exporter (Design D).

These tests are hermetic: they inject a synthetic engine (a fixed timeline of
materialized trees plus a small resolved-op stream) so the SQLite assembly,
content-blob dedup, L3 transition diffing, checkpoints, and active_at coverage
are exercised without a populated farchive corpus.

A separate corpus-backed smoke test runs the real exporter on a small Finnish
statute when the corpus is available (skipped otherwise).
"""

from __future__ import annotations
from typing_extensions import override

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.compile_result import SourcePathology
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.interlink_targets import (
    fi_transition_graph_interlink_provider,
    fi_transition_graph_overlay_provider,
    resolve_fi_interlink_target_row,
)
from lawvm.finland.ops import FailedOp
from lawvm.tools import export_transition_graph as etg
from lawvm.tools.transition_graph_interlinks import (
    LawvmInterlinkTargetRow,
    SurfaceTextSpanPlacer,
    place_surface_text_spans_many,
)
from lawvm.tools.transition_graph_jurisdictions import transition_graph_adapter_for_jurisdiction


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


# ---------------------------------------------------------------------------
# Subsection-granular fixture: a chapter/section/subsection tree where a single
# subsection changes, so the exporter must emit transitions at
# ``chapter:N/section:M/subsection:K`` granularity rather than whole-chapter.
# ---------------------------------------------------------------------------


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
    )


def _section_with_subs(label: str, subs: List[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label} §"),
            IRNode(kind=IRNodeKind.HEADING, text=f"Section {label} heading"),
            *subs,
        ),
    )


def _chapter(label: str, sections: List[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label} luku"),
            IRNode(kind=IRNodeKind.HEADING, text=f"Chapter {label} heading"),
            *sections,
        ),
    )


def _deep_tree(s1_sub2_text: str, s2_present: bool) -> IRNode:
    """chapter:1 with section:1 (two subsections) and section:2 (one subsection).

    ``s1_sub2_text`` varies the text of chapter:1/section:1/subsection:2 only;
    ``s2_present`` toggles whether chapter:1/section:2 exists at all.
    """
    sections = [
        _section_with_subs(
            "1",
            [_subsection("1", "s1-sub1-const"), _subsection("2", s1_sub2_text)],
        ),
    ]
    if s2_present:
        sections.append(
            _section_with_subs("2", [_subsection("1", "s2-sub1-const")])
        )
    return _body([_chapter("1", sections)])


# Three dates: only chapter:1/section:1/subsection:2 changes between D1 and D2;
# chapter:1/section:2 is removed at D3. A correct subsection-granular diff emits
# exactly one transition at chapter:1/section:1/subsection:2 (D2) and one
# delete at chapter:1/section:2/subsection:1 (D3) — never whole-chapter churn.
_DEEP_TREES_BY_DATE: Dict[str, IRNode] = {
    "2011-01-01": _deep_tree("v1", True),
    "2016-01-01": _deep_tree("v2", True),
    "2021-01-01": _deep_tree("v2", False),
}
_DEEP_CHANGE_DATES = sorted(_DEEP_TREES_BY_DATE)


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

        @override
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
        replay_findings=[
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="elab",
                source_statute="12/2015",
                detail={"target_address": "section:2", "message": "synthetic finding"},
            )
        ],
        source_pathologies=[
            SourcePathology.from_scope(
                code="ITEM_TARGET_STRUCTURE_ABSENT",
                message="synthetic pathology",
                source_statute="12/2015",
                target_unit_kind="section",
                target_label="2",
            )
        ],
        failed_ops=[
            FailedOp.from_scope(
                amendment_id="12/2015",
                description="REPLACE 3 §",
                reason="synthetic failure",
                reason_code="synthetic_failed_op",
                target_section="3",
                target_unit_kind="section",
            )
        ],
    )


def _sample_interlink_row() -> dict[str, object]:
    return {
        "interlink_id": "fi.inline_citations:100_2010:0",
        "source_jurisdiction": "fi",
        "source_work_kind": "normative_act",
        "source_local_id": "100/2010",
        "source_work_id": "fi:normative_act:100/2010",
        "source_locator": "section:1",
        "surface_text": "luonnonsuojelulain (9/2023)",
        "surface_kind": "prose_ref",
        "role": "cites",
        "target_jurisdiction": "fi",
        "target_work_kind": "normative_act",
        "target_local_id": "9/2023",
        "target_work_id": "fi:normative_act:9/2023",
        "target_locator": "section:2",
        "target_url": None,
        "candidate_work_ids": None,
        "resolution_status": "resolved",
        "confidence": "exact",
        "resolver_id": "fi.inline_citation",
        "source_artifact_id": None,
        "source_span_byte_offset": None,
        "source_span_byte_len": None,
        "rendered_statute_id": "100/2010",
        "rendered_effective_date": "2010-01-01",
        "rendered_address": "section:1",
        "rendered_segment_index": 0,
        "rendered_char_start": 0,
        "rendered_char_end": 27,
        "valid_at_start": "2010-01-01",
        "valid_at_end": None,
        "detail_json": "{}",
    }


def _placeable_interlink_row() -> dict[str, object]:
    row = dict(_sample_interlink_row())
    row.update({
        "interlink_id": "fi.inline_citations:100_2010:placeable",
        "source_locator": "section:1",
        "surface_text": "alpha",
        "rendered_statute_id": None,
        "rendered_effective_date": None,
        "rendered_address": None,
        "rendered_segment_index": None,
        "rendered_char_start": None,
        "rendered_char_end": None,
    })
    return row


def _interlink_provider_for_rows(rows: list[etg.LawvmInterlinkRow]) -> etg.LawvmInterlinkExportProvider:
    return etg.LawvmInterlinkExportProvider(
        project_interlinks=lambda _sid, _corpus: rows,
        resolve_target=resolve_fi_interlink_target_row,
    )


def _overlay_row(
    *,
    overlay_id: str,
    kind: str,
    node_id: str,
    label: str,
    status: str | None = None,
    payload_json: str = "{}",
    links_json: str = "[]",
) -> dict[str, object]:
    """A whole-body overlay projection row (rendered_* null, as projected)."""
    return {
        "overlay_id": overlay_id,
        "statute_id": "100/2010",
        "kind": kind,
        "node_id": node_id,
        "label": label,
        "payload_json": payload_json,
        "links_json": links_json,
        "overlay_status": status,
        "source_span_byte_offset": None,
        "source_span_byte_len": None,
        "rendered_statute_id": None,
        "rendered_effective_date": None,
        "rendered_address": None,
        "rendered_segment_index": None,
        "rendered_char_start": None,
        "rendered_char_end": None,
    }


def _overlay_provider_for_rows(
    rows: list[dict[str, object]],
) -> etg.LawvmSurfaceOverlayExportProvider:
    return etg.LawvmSurfaceOverlayExportProvider(
        project_overlays=lambda _sid, _corpus: [dict(r) for r in rows],
    )


@pytest.fixture()
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _make_bundle()

    def fake_run_engine_replay(
        _statute_id: str,
        *,
        profile: etg.TransitionGraphExportProfile | None = None,
    ) -> etg.ReplayBundle:
        return bundle

    def fake_materialize(_bundle: etg.ReplayBundle, as_of: str) -> IRNode:
        return _TREES_BY_DATE[as_of]

    class _FakeCorpus:
        def read_amendment(self, _engine_id: str) -> bytes:
            return b""

        def read_oracle(self, _engine_id: str) -> bytes | None:
            return None

        def read_source(self, _engine_id: str) -> bytes | None:
            return None

    monkeypatch.setattr(etg, "run_engine_replay", fake_run_engine_replay)
    monkeypatch.setattr(etg, "materialize_oracle_tree", fake_materialize)
    monkeypatch.setattr(
        "lawvm.finland.corpus._get_corpus_store", lambda: _FakeCorpus()
    )


def _make_deep_bundle() -> etg.ReplayBundle:
    class _Addr:
        def __init__(self, s: str) -> None:
            self._s = s

        @override
        def __str__(self) -> str:
            return self._s

    def op(action: StructuralAction, target: str, eff: str, src: str, seq: int) -> Any:
        source = SimpleNamespace(
            statute_id=src, title=f"Laki {src}", enacted=eff, effective=eff, expires=""
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

    # The whole-section replace at 2016-01-01 must attribute provenance down to
    # the changed subsection (ancestor-of-covering-address case); the section
    # repeal at 2021-01-01 attributes to its subsection delete.
    lo_ops = [
        op(StructuralAction.REPLACE, "chapter:1/section:1", "2016-01-01", "55/2016", 0),
        op(StructuralAction.REPEAL, "chapter:1/section:2", "2021-01-01", "77/2021", 1),
    ]
    base_body = _DEEP_TREES_BY_DATE["2011-01-01"]
    ctx = SimpleNamespace(id="200/2011", title="Syvälaki", base_ir=base_body)
    products = SimpleNamespace(
        replay_fold_state=None,
        materialized_state=None,
        temporal_events=(),
        migration_events=(),
    )
    result = SimpleNamespace(ctx=ctx, products=products, title="Syvälaki")
    return etg.ReplayBundle(
        statute_id="200/2011",
        engine_id="2011/200",
        title="Syvälaki",
        result=result,
        lo_ops=lo_ops,
        timelines={},
        change_dates=list(_DEEP_CHANGE_DATES),
    )


@pytest.fixture()
def patched_deep_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _make_deep_bundle()

    def fake_run_engine_replay(
        _statute_id: str,
        *,
        profile: etg.TransitionGraphExportProfile | None = None,
    ) -> etg.ReplayBundle:
        return bundle

    def fake_materialize(_bundle: etg.ReplayBundle, as_of: str) -> IRNode:
        return _DEEP_TREES_BY_DATE[as_of]

    class _FakeCorpus:
        def read_amendment(self, _engine_id: str) -> bytes:
            return b""

    monkeypatch.setattr(etg, "run_engine_replay", fake_run_engine_replay)
    monkeypatch.setattr(etg, "materialize_oracle_tree", fake_materialize)
    monkeypatch.setattr(
        "lawvm.finland.corpus._get_corpus_store", lambda: _FakeCorpus()
    )


def _fold_certifies_checkpoints(db_path: Path) -> None:
    """Python port of exp1_certified_reducer.mjs.

    Folds ``transitions`` in sequence into a live covering set (address ->
    subtree_hash), asserting pre/post hashes per transition, and at every
    change-date recomputes the reproducible tree hash over the live covering set
    and asserts it equals ``checkpoints.tree_hash``. This is the certification
    contract a browser-side JS reducer relies on: the finer subsection/section
    transitions MUST still fold back to every engine checkpoint exactly.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        transitions = conn.execute(
            "SELECT transition_id, effective_date, target_address, action, "
            "pre_hash, post_hash FROM transitions ORDER BY sequence ASC"
        ).fetchall()
        checkpoints = conn.execute(
            "SELECT date, tree_hash FROM checkpoints ORDER BY date ASC"
        ).fetchall()
    finally:
        conn.close()

    live: Dict[str, str] = {}
    ti = 0
    for date, expected_hash in checkpoints:
        while ti < len(transitions) and transitions[ti][1] == date:
            _tid, _d, addr, action, pre, post = transitions[ti]
            cur = live.get(addr, "")
            assert cur == pre, f"pre_hash mismatch at {addr} on {date}"
            if action == "delete_subtree" or post == "":
                live.pop(addr, None)
            else:
                live[addr] = post
            now = live.get(addr, "")
            assert now == post, f"post_hash mismatch at {addr} on {date}"
            ti += 1
        got = etg.reproducible_tree_hash(list(live.items()))
        assert got == expected_hash, f"checkpoint mismatch on {date}"
    assert ti == len(transitions), "not all transitions consumed"


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

        # --- semantic interlink table is always present, even when no link
        # projector rows are available for this hermetic fixture.
        interlink_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lawvm_interlinks'"
        ).fetchone()
        assert interlink_table == ("lawvm_interlinks",)
        target_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lawvm_interlink_targets'"
        ).fetchone()
        assert target_table == ("lawvm_interlink_targets",)
        assert stats.n_lawvm_interlinks == 0
        assert conn.execute("SELECT COUNT(*) FROM lawvm_interlinks").fetchone()[0] == 0
        assert stats.n_lawvm_interlink_targets == 0
        assert conn.execute("SELECT COUNT(*) FROM lawvm_interlink_targets").fetchone()[0] == 0

        explicit_indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
        assert explicit_indexes == []
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0

        # --- internal LawVM evidence surfaces are visible, not folded into law ---
        evidence = conn.execute(
            "SELECT surface, kind, role, severity, source_id, effective_date, "
            "target_address, rule_id, title, detail_json "
            "FROM evidence_events ORDER BY event_id"
        ).fetchall()
        assert stats.n_evidence_events == 3
        assert {row[0] for row in evidence} == {
            "replay_finding",
            "source_pathology",
            "failed_op",
        }
        by_surface = {row[0]: row for row in evidence}
        assert by_surface["replay_finding"][1] == "ELAB.SOURCE_PATHOLOGY"
        assert by_surface["replay_finding"][4] == "12/2015"
        assert by_surface["replay_finding"][5] == "2015-01-01"
        assert by_surface["replay_finding"][6] == "section:2"
        assert by_surface["source_pathology"][6] == "section:2"
        assert by_surface["failed_op"][1] == "synthetic_failed_op"
        assert by_surface["failed_op"][3] == "error"
        assert by_surface["failed_op"][6] == "section:3"
        assert json.loads(by_surface["failed_op"][9])["reason"] == "synthetic failure"
    finally:
        conn.close()


def test_export_always_persists_lawvm_interlinks(
    patched_engine: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = etg.LawvmInterlinkRow.from_mapping(_sample_interlink_row())

    out = tmp_path / "synth_links.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        interlink_provider=_interlink_provider_for_rows([row]),
    )

    assert stats.n_lawvm_interlinks == 1
    assert stats.n_lawvm_interlink_targets == 1
    conn = sqlite3.connect(str(out))
    try:
        stored = conn.execute(
            "SELECT interlink_id, surface_text, target_work_id, rendered_address, "
            "rendered_segment_index, rendered_char_start, rendered_char_end, detail_json "
            "FROM lawvm_interlinks"
        ).fetchone()
        assert stored[:7] == (
            "fi.inline_citations:100_2010:0",
            "luonnonsuojelulain (9/2023)",
            "fi:normative_act:9/2023",
            "section:1",
            0,
            0,
            27,
        )
        assert json.loads(stored[7])["target_key"] == "fi|normative_act|9/2023|section:2"
        target = conn.execute(
            "SELECT target_key, target_url, target_links_json, preview_status "
            "FROM lawvm_interlink_targets"
        ).fetchone()
        assert target[0] == "fi|normative_act|9/2023|section:2"
        assert target[1] == "https://www.finlex.fi/fi/lainsaadanto/2023/9"
        assert json.loads(target[2]) == [
            {
                "label": "Finlex",
                "rel": "canonical",
                "url": "https://www.finlex.fi/fi/lainsaadanto/2023/9",
            },
            {
                "label": "Säädöskokoelma",
                "rel": "source_publication",
                "url": "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2023/9",
            },
        ]
        assert target[3] == "missing_local_corpus"
    finally:
        conn.close()


def test_export_accepts_non_fi_profile_and_runtime_hooks(
    tmp_path: Path,
) -> None:
    profile = etg.TransitionGraphExportProfile(
        jurisdiction="zz",
        lang="en",
        canonical_statute_id=lambda value: f"zz:{value}",
        engine_statute_id=lambda value: value.removeprefix("zz:"),
    )
    bundle = _make_bundle()

    def replay_runner(
        _engine_id: str,
        *,
        profile: etg.TransitionGraphExportProfile | None = None,
    ) -> etg.ReplayBundle:
        return bundle

    def tree_materializer(_bundle: etg.ReplayBundle, as_of: str) -> IRNode:
        return _TREES_BY_DATE[as_of]

    out = tmp_path / "non_fi_runtime.db"
    stats = etg.export_transition_graph(
        "act-1",
        out,
        quiet=True,
        profile=profile,
        replay_runner=replay_runner,
        tree_materializer=tree_materializer,
    )

    assert stats.statute_id == "zz:act-1"
    conn = sqlite3.connect(str(out))
    try:
        assert json.loads(
            conn.execute("SELECT value FROM meta WHERE key='jurisdiction'").fetchone()[0]
        ) == "zz"
        assert conn.execute("SELECT COUNT(*) FROM lawvm_interlink_targets").fetchone()[0] == 0
    finally:
        conn.close()


def test_export_non_fi_interlink_provider_does_not_require_corpus(
    tmp_path: Path,
) -> None:
    profile = etg.TransitionGraphExportProfile(
        jurisdiction="zz",
        lang="en",
        canonical_statute_id=lambda value: f"zz:{value}",
        engine_statute_id=lambda value: value.removeprefix("zz:"),
    )
    bundle = _make_bundle()
    raw = _sample_interlink_row()
    raw.update({
        "interlink_id": "zz.inline:act-1:0",
        "source_jurisdiction": "zz",
        "source_local_id": "act-1",
        "source_work_id": "zz:normative_act:act-1",
        "target_jurisdiction": "zz",
        "target_local_id": "target-act",
        "target_work_id": "zz:normative_act:target-act",
        "target_locator": "section:A",
        "rendered_statute_id": "zz:act-1",
    })
    row = etg.LawvmInterlinkRow.from_mapping(raw)

    def replay_runner(
        _engine_id: str,
        *,
        profile: etg.TransitionGraphExportProfile | None = None,
    ) -> etg.ReplayBundle:
        return bundle

    def tree_materializer(_bundle: etg.ReplayBundle, as_of: str) -> IRNode:
        return _TREES_BY_DATE[as_of]

    def project_interlinks(statute_id: str, corpus: object | None) -> list[etg.LawvmInterlinkRow]:
        assert statute_id == "zz:act-1"
        assert corpus is None
        return [row]

    def resolve_target(target_ref: Any, context: Any) -> LawvmInterlinkTargetRow:
        assert context.source_statute_id == "zz:act-1"
        assert context.corpus is None
        return LawvmInterlinkTargetRow(
            target_key=target_ref.key,
            target_jurisdiction=target_ref.jurisdiction,
            target_work_kind=target_ref.work_kind,
            target_local_id=target_ref.local_id,
            target_work_id=target_ref.work_id,
            target_locator=target_ref.locator,
            target_url="https://example.test/target-act/section-a",
            target_links_json=json.dumps([
                {
                    "rel": "canonical",
                    "label": "Example",
                    "url": "https://example.test/target-act/section-a",
                }
            ]),
            preview_status="resolved_by_zz_adapter",
            preview_source="zz.synthetic_preview",
            title="Target Act",
            locator_label="Section A",
            hierarchy_json=json.dumps([
                {"kind": "section", "label": "A", "title": "Synthetic section"}
            ]),
            preview_text="Adapter-owned target preview.",
            detail_json="{}",
        )

    provider = etg.LawvmInterlinkExportProvider(
        project_interlinks=project_interlinks,
        resolve_target=resolve_target,
    )
    out = tmp_path / "non_fi_links_no_corpus.db"
    stats = etg.export_transition_graph(
        "act-1",
        out,
        quiet=True,
        profile=profile,
        interlink_provider=provider,
        replay_runner=replay_runner,
        tree_materializer=tree_materializer,
    )

    assert stats.n_lawvm_interlinks == 1
    assert stats.n_lawvm_interlink_targets == 1
    conn = sqlite3.connect(str(out))
    try:
        target = conn.execute(
            "SELECT target_jurisdiction, target_url, preview_status, title, hierarchy_json "
            "FROM lawvm_interlink_targets"
        ).fetchone()
        assert target[0:4] == (
            "zz",
            "https://example.test/target-act/section-a",
            "resolved_by_zz_adapter",
            "Target Act",
        )
        assert json.loads(target[4]) == [
            {"kind": "section", "label": "A", "title": "Synthetic section"}
        ]
        detail = json.loads(
            conn.execute("SELECT detail_json FROM lawvm_interlinks").fetchone()[0]
        )
        assert detail["target_key"] == "zz|normative_act|target-act|section:A"
    finally:
        conn.close()


def test_export_places_unambiguous_lawvm_interlinks_in_rendered_text(
    patched_engine: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = etg.LawvmInterlinkRow.from_mapping(_placeable_interlink_row())

    out = tmp_path / "synth_placed_links.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        interlink_provider=_interlink_provider_for_rows([row]),
    )

    assert stats.n_lawvm_interlinks == len(_CHANGE_DATES)
    assert stats.n_lawvm_interlink_targets == 1
    conn = sqlite3.connect(str(out))
    try:
        stored = conn.execute(
            "SELECT rendered_effective_date, rendered_address, rendered_segment_index, "
            "rendered_char_start, rendered_char_end, surface_text "
            "FROM lawvm_interlinks AS l "
            "JOIN checkpoints AS c ON l.rendered_effective_date = c.date "
            "ORDER BY c.date"
        ).fetchall()
        assert stored == [
            ("2010-01-01", "section:1", 0, 0, 5, "alpha"),
            ("2015-01-01", "section:1", 0, 0, 5, "alpha"),
            ("2020-01-01", "section:1", 0, 0, 5, "alpha"),
        ]
    finally:
        conn.close()


def test_lawvm_interlink_placement_token_index_keeps_subtoken_candidates() -> None:
    row_data = _placeable_interlink_row()
    row_data["source_locator"] = None
    row_data["surface_text"] = "2014"
    row = etg.LawvmInterlinkRow.from_mapping(row_data)
    segments_by_date = {
        "2010-01-01": [
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="section:1",
                segment_index=0,
                text="x2014y",
            )
        ],
        "2015-01-01": [
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:1",
                segment_index=0,
                text="x2014y",
            ),
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:2",
                segment_index=0,
                text="2014",
            ),
        ],
    }

    placed = etg.place_lawvm_interlinks(
        [row],
        statute_id="100/2010",
        segments_by_date=segments_by_date,
    )

    assert [
        (
            link.rendered_effective_date,
            link.rendered_address,
            link.rendered_segment_index,
            link.rendered_char_start,
            link.rendered_char_end,
        )
        for link in placed
    ] == [("2010-01-01", "section:1", 0, 1, 5)]


def test_surface_text_placer_known_locator_index_matches_default_index() -> None:
    segments_by_date = {
        "2010-01-01": [
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="part:1/chapter:2/section:3/subsection:4",
                segment_index=0,
                text="alpha beta",
            ),
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="part:1/chapter:2/section:5",
                segment_index=1,
                text="alpha beta",
            ),
        ],
        "2015-01-01": [
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="part:1/chapter:2/section:3/subsection:4",
                segment_index=0,
                text="alpha beta",
            )
        ],
    }

    default = SurfaceTextSpanPlacer(segments_by_date).place(
        "alpha beta",
        "section:3/subsection:4",
    )
    known = SurfaceTextSpanPlacer(
        segments_by_date,
        known_locators=frozenset({"section:3/subsection:4"}),
    ).place(
        "alpha beta",
        "section:3/subsection:4",
    )

    assert known == default
    assert [(date, segment.address, start) for date, segment, start in known] == [
        ("2010-01-01", "part:1/chapter:2/section:3/subsection:4", 0),
        ("2015-01-01", "part:1/chapter:2/section:3/subsection:4", 0),
    ]


def test_bulk_surface_text_placement_keeps_exact_token_prefilter_semantics() -> None:
    segments_by_date = {
        "2010-01-01": [
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="section:1",
                segment_index=0,
                text="x2014y alpha",
            )
        ],
        "2015-01-01": [
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:1",
                segment_index=0,
                text="2014 alpha",
            ),
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:2",
                segment_index=0,
                text="2014 alpha",
            ),
        ],
        "2020-01-01": [
            etg.RenderedTextSegment(
                date="2020-01-01",
                address="section:1",
                segment_index=0,
                text="2014 alpha",
            )
        ],
    }

    placements = place_surface_text_spans_many(
        ["2014", "alpha"],
        None,
        segments_by_date,
    )

    assert [
        (date, segment.address, start)
        for date, segment, start in placements["2014"]
    ] == [("2020-01-01", "section:1", 0)]
    assert [
        (date, segment.address, start)
        for date, segment, start in placements["alpha"]
    ] == [
        ("2010-01-01", "section:1", 7),
        ("2020-01-01", "section:1", 5),
    ]


def test_bulk_surface_text_placement_prefilters_no_token_fallback_surfaces() -> None:
    segments_by_date = {
        "2010-01-01": [
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="section:1",
                segment_index=0,
                text="irrelevant long text with no fallback citation",
            ),
            etg.RenderedTextSegment(
                date="2010-01-01",
                address="section:2",
                segment_index=0,
                text="47 f § applies",
            ),
        ],
        "2015-01-01": [
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:1",
                segment_index=0,
                text="71 a §:n first copy",
            ),
            etg.RenderedTextSegment(
                date="2015-01-01",
                address="section:2",
                segment_index=0,
                text="71 a §:n second copy",
            ),
        ],
    }

    placements = place_surface_text_spans_many(
        ["47 f §", "71 a §:n", "20 §", "58 b §", "86  §"],
        None,
        segments_by_date,
    )

    assert [
        (date, segment.address, start)
        for date, segment, start in placements["47 f §"]
    ] == [("2010-01-01", "section:2", 0)]
    assert placements["71 a §:n"] == []
    assert placements["20 §"] == []


@pytest.mark.parametrize(
    "surface_kind",
    ["xml_ref", "preparatory_ref", "effect_feed_ref", "manual_claim_ref"],
)
def test_export_places_unambiguous_rendered_interlink_surfaces(
    surface_kind: str,
    patched_engine: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row_data = _placeable_interlink_row()
    row_data["surface_kind"] = surface_kind
    row = etg.LawvmInterlinkRow.from_mapping(row_data)

    out = tmp_path / f"synth_placed_{surface_kind}_links.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        interlink_provider=_interlink_provider_for_rows([row]),
    )

    assert stats.n_lawvm_interlinks == len(_CHANGE_DATES)
    conn = sqlite3.connect(str(out))
    try:
        stored = conn.execute(
            "SELECT rendered_effective_date, rendered_address, rendered_segment_index, "
            "rendered_char_start, rendered_char_end, surface_kind, surface_text "
            "FROM lawvm_interlinks AS l "
            "JOIN checkpoints AS c ON l.rendered_effective_date = c.date "
            "ORDER BY c.date"
        ).fetchall()
        assert stored == [
            ("2010-01-01", "section:1", 0, 0, 5, surface_kind, "alpha"),
            ("2015-01-01", "section:1", 0, 0, 5, surface_kind, "alpha"),
            ("2020-01-01", "section:1", 0, 0, 5, surface_kind, "alpha"),
        ]
    finally:
        conn.close()


def test_export_does_not_place_metadata_interlinks_in_rendered_text(
    patched_engine: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row_data = _placeable_interlink_row()
    row_data["surface_kind"] = "metadata_ref"
    row = etg.LawvmInterlinkRow.from_mapping(row_data)

    out = tmp_path / "synth_unplaced_metadata_links.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        interlink_provider=_interlink_provider_for_rows([row]),
    )

    assert stats.n_lawvm_interlinks == 1
    conn = sqlite3.connect(str(out))
    try:
        stored = conn.execute(
            "SELECT rendered_effective_date, rendered_address, rendered_char_start, "
            "rendered_char_end, surface_kind, surface_text "
            "FROM lawvm_interlinks"
        ).fetchall()
        assert stored == [(None, None, None, None, "metadata_ref", "alpha")]
    finally:
        conn.close()


def test_export_emits_surface_overlay_table_schema(
    patched_engine: None, tmp_path: Path
) -> None:
    """The overlay table is always created with the exact viewer-coded columns,
    even when no overlay provider is supplied."""
    out = tmp_path / "synth_overlay_schema.db"
    stats = etg.export_transition_graph("100/2010", out, quiet=True)
    assert stats.n_lawvm_surface_overlays == 0

    conn = sqlite3.connect(str(out))
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='lawvm_surface_overlays'"
        ).fetchone()
        assert table == ("lawvm_surface_overlays",)
        cols = [
            r[1]
            for r in conn.execute("PRAGMA table_info(lawvm_surface_overlays)").fetchall()
        ]
        # The viewer reads `SELECT * ... ORDER BY overlay_id`; the columns must be
        # exactly the shared overlay row schema (same as the standalone export).
        assert tuple(cols) == etg.SURFACE_OVERLAY_ROW_COLUMNS
        assert conn.execute("SELECT COUNT(*) FROM lawvm_surface_overlays").fetchone()[0] == 0
    finally:
        conn.close()


def test_export_places_surface_overlays_in_rendered_text(
    patched_engine: None, tmp_path: Path
) -> None:
    """A whole-body overlay row (null rendered_*) is placed onto the per-date
    rendered segments, exactly like an interlink, so the viewer can paint it.

    section:1 carries the text 'alpha' at every change-date; a defined_term
    overlay labelled 'alpha' must therefore be placed at section:1 char [0,5)
    once per change-date with populated rendered_* columns."""
    overlay = _overlay_row(
        overlay_id="fi.overlay:abc",
        kind="defined_term",
        node_id="def_alpha",
        label="alpha",
        payload_json=json.dumps({"term": "alpha", "definition": "the first letter"}),
    )
    out = tmp_path / "synth_overlay_placed.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        overlay_provider=_overlay_provider_for_rows([overlay]),
    )

    # One placed copy per change-date.
    assert stats.n_lawvm_surface_overlays == len(_CHANGE_DATES)

    conn = sqlite3.connect(str(out))
    try:
        placed = conn.execute(
            "SELECT rendered_effective_date, kind, label, rendered_address, "
            "rendered_segment_index, rendered_char_start, rendered_char_end, "
            "payload_json "
            "FROM lawvm_surface_overlays AS o "
            "JOIN checkpoints AS c ON o.rendered_effective_date = c.date "
            "ORDER BY c.date"
        ).fetchall()
        assert [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in placed] == [
            ("2010-01-01", "defined_term", "alpha", "section:1", 0, 0, 5),
            ("2015-01-01", "defined_term", "alpha", "section:1", 0, 0, 5),
            ("2020-01-01", "defined_term", "alpha", "section:1", 0, 0, 5),
        ]
        # The typed payload survives the projection->placement->persist round-trip.
        assert json.loads(placed[0][7])["definition"] == "the first letter"
        # overlay_id stays unique per placed copy (date-keyed).
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT overlay_id FROM lawvm_surface_overlays"
            ).fetchall()
        ]
        assert len(ids) == len(set(ids)) == len(_CHANGE_DATES)
    finally:
        conn.close()


def test_export_places_duplicate_surface_overlay_labels_independently(
    patched_engine: None, tmp_path: Path
) -> None:
    """Rows that share a label may reuse placement work, but remain distinct
    semantic overlays with distinct overlay_ids."""
    overlays = [
        _overlay_row(
            overlay_id="fi.overlay:alpha:def",
            kind="defined_term",
            node_id="def_alpha",
            label="alpha",
        ),
        _overlay_row(
            overlay_id="fi.overlay:alpha:temporal",
            kind="temporal",
            node_id="temporal_alpha",
            label="alpha",
        ),
    ]
    out = tmp_path / "synth_overlay_duplicate_labels.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        overlay_provider=_overlay_provider_for_rows(overlays),
    )

    assert stats.n_lawvm_surface_overlays == 2 * len(_CHANGE_DATES)

    conn = sqlite3.connect(str(out))
    try:
        rows = conn.execute(
            "SELECT overlay_id, kind, rendered_effective_date, rendered_address, "
            "rendered_char_start, rendered_char_end "
            "FROM lawvm_surface_overlays ORDER BY overlay_id, rendered_effective_date"
        ).fetchall()
        assert len(rows) == 2 * len(_CHANGE_DATES)
        assert len({row[0] for row in rows}) == len(rows)
        assert {row[1] for row in rows} == {"defined_term", "temporal"}
        assert {row[3:] for row in rows} == {("section:1", 0, 5)}
    finally:
        conn.close()


def test_export_keeps_unplaceable_surface_overlay_unplaced(
    patched_engine: None, tmp_path: Path
) -> None:
    """An overlay whose surface does not occur in the rendered text stays a valid
    semantic row with null rendered_* (never a fabricated span)."""
    overlay = _overlay_row(
        overlay_id="fi.overlay:nomatch",
        kind="temporal",
        node_id="t1",
        label="this surface appears nowhere in the body",
    )
    out = tmp_path / "synth_overlay_unplaced.db"
    stats = etg.export_transition_graph(
        "100/2010",
        out,
        quiet=True,
        overlay_provider=_overlay_provider_for_rows([overlay]),
    )
    assert stats.n_lawvm_surface_overlays == 1
    conn = sqlite3.connect(str(out))
    try:
        row = conn.execute(
            "SELECT overlay_id, rendered_address, rendered_effective_date, "
            "rendered_char_start FROM lawvm_surface_overlays"
        ).fetchone()
        assert row == ("fi.overlay:nomatch", None, None, None)
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


# ---------------------------------------------------------------------------
# Section/subsection-granularity tests
# ---------------------------------------------------------------------------


def test_default_granularity_emits_subsection_targets(
    patched_deep_engine: None, tmp_path: Path
) -> None:
    """Default export targets deeper-than-chapter addresses (section/subsection),
    not whole chapters, and emits MINIMAL subtrees for an isolated change."""
    out = tmp_path / "deep.db"
    stats = etg.export_transition_graph("200/2011", out, quiet=True)
    assert stats.granularity == "subsection"

    conn = sqlite3.connect(str(out))
    try:
        addrs = [
            r[0]
            for r in conn.execute("SELECT target_address FROM transitions").fetchall()
        ]
        assert addrs, "expected at least one transition"

        # No transition is a bare whole-chapter address; every target is deeper
        # than chapter (has a section, and subsection components present).
        for a in addrs:
            depth = a.count("/") + 1
            assert depth >= 3, f"expected section/subsection depth, got {a!r}"
        assert any("subsection:" in a for a in addrs), "expected subsection targets"
        assert all("chapter:" in a and "section:" in a for a in addrs)

        # The single subsection text change (D2) lands EXACTLY on
        # chapter:1/section:1/subsection:2 — not on the whole chapter/section,
        # and the unchanged sibling subsection is not re-emitted.
        d2 = conn.execute(
            "SELECT target_address, action FROM transitions "
            "WHERE effective_date='2016-01-01'"
        ).fetchall()
        d2_addrs = {a for (a, _act) in d2}
        assert d2_addrs == {"chapter:1/section:1/subsection:2"}

        # Even though active_at stores subsection blobs at this granularity,
        # display metadata preserves scaffold ancestor titles for the viewer.
        section_display = conn.execute(
            "SELECT kind, label, num, heading FROM display_nodes "
            "WHERE date='2016-01-01' AND address='chapter:1/section:1'"
        ).fetchone()
        assert section_display == ("section", "1", "1 §", "Section 1 heading")
        chapter_display = conn.execute(
            "SELECT kind, label, num, heading FROM display_nodes "
            "WHERE date='2016-01-01' AND address='chapter:1'"
        ).fetchone()
        assert chapter_display == ("chapter", "1", "1 luku", "Chapter 1 heading")

        # The section:2 removal at D3 surfaces at its subsection address.
        d3 = conn.execute(
            "SELECT target_address, action FROM transitions "
            "WHERE effective_date='2021-01-01'"
        ).fetchall()
        assert ("chapter:1/section:2/subsection:1", "delete_subtree") in {
            (a, act) for (a, act) in d3
        }
    finally:
        conn.close()


def test_subsection_provenance_attributed_from_section_op(
    patched_deep_engine: None, tmp_path: Path
) -> None:
    """A whole-section amendment op carries its provenance (source_id/op kind)
    down to the finer subsection transition it produced."""
    out = tmp_path / "deep_prov.db"
    etg.export_transition_graph("200/2011", out, quiet=True)
    conn = sqlite3.connect(str(out))
    try:
        row = conn.execute(
            "SELECT source_id, legal_op_kind FROM transitions "
            "WHERE target_address='chapter:1/section:1/subsection:2' "
            "AND effective_date='2016-01-01'"
        ).fetchone()
        assert row is not None
        source_id, legal_op_kind = row
        # provenance backpropagated from the chapter:1/section:1 replace op
        assert source_id == "55/2016"
        assert "replace" in legal_op_kind
    finally:
        conn.close()


def test_certified_reducer_reproduces_checkpoints_subsection(
    patched_deep_engine: None, tmp_path: Path
) -> None:
    """The certified L3 fold of the finer subsection transitions reproduces every
    engine checkpoint tree-hash exactly (Exp-1 reducer contract)."""
    out = tmp_path / "deep_cert.db"
    etg.export_transition_graph("200/2011", out, quiet=True)
    _fold_certifies_checkpoints(out)


def test_certified_reducer_reproduces_checkpoints_flat(
    patched_engine: None, tmp_path: Path
) -> None:
    """Certification also holds for the flat section fixture under the default
    subsection granularity (sections with no labeled subsection stay whole)."""
    out = tmp_path / "flat_cert.db"
    etg.export_transition_graph("100/2010", out, quiet=True)
    _fold_certifies_checkpoints(out)


def test_chapter_granularity_fallback(
    patched_deep_engine: None, tmp_path: Path
) -> None:
    """The legacy chapter granularity still tiles at whole-chapter depth and
    remains certified."""
    out = tmp_path / "chapter.db"
    stats = etg.export_transition_graph(
        "200/2011", out, granularity="chapter", quiet=True
    )
    assert stats.granularity == "chapter"
    conn = sqlite3.connect(str(out))
    try:
        addrs = {
            r[0]
            for r in conn.execute("SELECT DISTINCT address FROM active_at").fetchall()
        }
        # whole-chapter covering: only chapter:1 (no deeper addresses)
        assert addrs == {"chapter:1"}
    finally:
        conn.close()
    _fold_certifies_checkpoints(out)


def test_unknown_granularity_rejected(patched_deep_engine: None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown granularity"):
        etg.export_transition_graph(
            "200/2011", tmp_path / "bad.db", granularity="nonsense", quiet=True
        )


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


def test_transition_graph_jurisdiction_adapter_fails_loudly_for_unsupported() -> None:
    with pytest.raises(ValueError, match="not implemented for jurisdiction 'zz'"):
        transition_graph_adapter_for_jurisdiction("zz")


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
    stats = etg.export_transition_graph(
        "1005/2000",
        out,
        quiet=True,
        interlink_provider=fi_transition_graph_interlink_provider(),
        overlay_provider=fi_transition_graph_overlay_provider(),
    )
    assert out.exists()
    assert stats.n_checkpoints >= 1
    assert stats.n_change_dates >= 1
    assert stats.n_content_blobs >= 1
    # The real Legal Surface Graph yields overlays for this statute.
    assert stats.n_lawvm_surface_overlays >= 1

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


def test_fi_profile_urls_use_current_finlex_scheme() -> None:
    from lawvm.finland.transition_graph_profile import (
        fi_amendment_url,
        fi_current_statute_url,
    )

    # Consolidated ("ajantasa") -> lainsaadanto, bare statute number.
    assert (
        fi_current_statute_url("fi:normative_act:2007/360", "2007/360")
        == "https://www.finlex.fi/fi/lainsaadanto/2007/360"
    )
    # As-enacted (alkup) -> saadoskokoelma, bare statute number.
    assert (
        fi_amendment_url("fi:normative_act:2007/360", "2007/360")
        == "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2007/360"
    )
    # Malformed ids degrade to empty (no link is fine; a wrong link is not).
    assert fi_current_statute_url("", "2007") == ""
    assert fi_amendment_url("", "2007") == ""
