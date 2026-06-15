from __future__ import annotations

import asyncio
import sys
import types

import pytest

from lawvm.core.graph import BuildMeta, CorpusGraph, StatuteGraph
from lawvm.graph_build import build_corpus_graph, build_corpus_graph_sync
from lawvm.contracts import ProcessingStatus
from lawvm.core.ir import LegalAddress, OperationSource, ProvisionTimeline, ProvisionVersion, ScopePredicate, IRNode
from lawvm.core.semantic_types import IRNodeKind


def test_build_corpus_graph_exposes_partial_failure_ledger(monkeypatch) -> None:
    async def fake_build_statute_graph_fi(sid: str):
        if sid == "fi/bad":
            raise RuntimeError("boom")
        return StatuteGraph(statute_id=sid, title=f"title:{sid}")

    async def fake_build_statute_graph_fi_lightweight(sid: str):
        return await fake_build_statute_graph_fi(sid)

    fake_module = types.SimpleNamespace(
        build_statute_graph_fi=fake_build_statute_graph_fi,
        build_statute_graph_fi_lightweight=fake_build_statute_graph_fi_lightweight,
    )
    monkeypatch.setitem(__import__("sys").modules, "lawvm.finland.graph", fake_module)
    monkeypatch.setitem(
        __import__("sys").modules,
        "lawvm.finland.amendment_index",
        types.SimpleNamespace(get_amendment_children=lambda: {}),
    )

    graph = asyncio.run(build_corpus_graph(["fi/good", "fi/bad"], with_timelines=False))

    assert graph.is_partial is True
    assert graph.processing_status == ProcessingStatus(
        kind="partial",
        blockers=("graph_build_failed:fi/bad",),
    )
    assert graph.build_meta is not None
    assert graph.build_meta.failed_statutes == 1
    assert graph.build_failures == [{"statute_id": "fi/bad", "error": "boom"}]
    assert "fi/good" in graph.statute_meta


def test_build_corpus_graph_sync_wraps_async_builder(monkeypatch) -> None:
    async def fake_build_statute_graph_fi(sid: str):
        return StatuteGraph(statute_id=sid, title=f"title:{sid}")

    async def fake_build_statute_graph_fi_lightweight(sid: str):
        return await fake_build_statute_graph_fi(sid)

    fake_module = types.SimpleNamespace(
        build_statute_graph_fi=fake_build_statute_graph_fi,
        build_statute_graph_fi_lightweight=fake_build_statute_graph_fi_lightweight,
    )
    monkeypatch.setitem(__import__("sys").modules, "lawvm.finland.graph", fake_module)
    monkeypatch.setitem(
        __import__("sys").modules,
        "lawvm.finland.amendment_index",
        types.SimpleNamespace(get_amendment_children=lambda: {}),
    )

    graph = build_corpus_graph_sync(["fi/good"], with_timelines=False)

    assert graph.is_partial is False
    assert graph.processing_status == ProcessingStatus(kind="complete")
    assert "fi/good" in graph.statute_meta


def test_corpus_graph_rejects_status_failure_contradiction() -> None:
    with pytest.raises(ValueError, match="must have a partial processing_status"):
        CorpusGraph(
            build_failures=[{"statute_id": "fi/z", "error": "boom"}],
            processing_status=ProcessingStatus(kind="complete"),
        )


def test_corpus_graph_rejects_failed_statute_count_mismatch() -> None:
    with pytest.raises(ValueError, match="failed_statutes must match build_failures"):
        CorpusGraph(
            build_failures=[{"statute_id": "fi/z", "error": "boom"}],
            build_meta=BuildMeta(
                built_at="2026-04-06T00:00:00+00:00",
                lawvm_commit="deadbeef",
                corpus_size=1,
                failed_statutes=0,
            ),
            processing_status=ProcessingStatus(
                kind="partial",
                blockers=("graph_build_failed:fi/z",),
            ),
        )


def test_silent_breakage_marks_missing_scope_as_ambiguous() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    timeline = ProvisionTimeline(
        address=addr,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),
                applicability=[
                    ScopePredicate(
                        dimension="territory",
                        includes=frozenset({"England"}),
                    )
                ],
                source=OperationSource(statute_id="2000/1"),
            )
        ],
    )
    citation = types.SimpleNamespace(
        source_statute_id="fi/citing",
        target_statute_id="fi/changed",
        source_section="1",
        target_section="section/1",
        edge_type="CITES",
        count=1,
    )
    graph = CorpusGraph(
        timelines={"fi/citing": {addr: timeline}},
        citations=[citation],
    )

    rows = graph.silent_breakage("fi/changed", as_of="2010-01-01")

    assert rows[0]["active_at_date"] is None
    assert rows[0]["selection_status"] == "ambiguous_missing_scope"


def test_corpus_graph_wire_artifact_projects_stable_summary() -> None:
    graph = CorpusGraph(
        statute_meta={"fi/a": {"title": "A"}, "fi/b": {"title": "B"}},
        amendment_index={"fi/b": ["2001/1"]},
        build_failures=[{"statute_id": "fi/z", "error": "boom"}],
        processing_status=ProcessingStatus(kind="partial", blockers=("graph_build_failed:fi/z",)),
    )

    artifact = graph.to_wire_artifact(producer="tests.graph", version="wire-1")

    assert artifact.schema == "lawvm.corpus_graph"
    assert artifact.producer == "tests.graph"
    assert artifact.version == "wire-1"
    assert artifact.status == ProcessingStatus(
        kind="partial",
        blockers=("graph_build_failed:fi/z",),
    )
    assert artifact.payload["loaded_statutes"] == ("fi/a", "fi/b")
    assert artifact.payload["amendment_index_statutes"] == ("fi/b",)
    assert artifact.payload["build_failures"] == ({"statute_id": "fi/z", "error": "boom"},)


def test_build_statute_graph_fi_prefers_replay_owned_timelines(monkeypatch) -> None:
    async def _run() -> StatuteGraph:
        from lawvm.finland.graph import build_statute_graph_fi

        class _Corpus:
            def read_source(self, sid: str):
                assert sid == "fi/test"
                return (
                    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
                    b"<act><docTitle>Test</docTitle><meta><identification>"
                    b'<FRBRWork><FRBRalias name="typeStatute" refersTo="#act"/></FRBRWork>'
                    b"</identification></meta><body/></act></akomaNtoso>"
                )

            def read_oracle(self, sid: str):
                assert sid == "fi/test"
                return None

        replay_timelines = {"owned": "timeline"}
        fake_grafter = types.SimpleNamespace(
            get_corpus=lambda: _Corpus(),
            replay_xml=lambda sid, lo_ops_out=None: types.SimpleNamespace(
                title="Test",
                timelines=replay_timelines,
                temporal_events=("should-not-matter",),
            ),
            _fi_label_postprocessor=lambda kind, label: label,
        )
        monkeypatch.setitem(sys.modules, "lawvm.finland.grafter", fake_grafter)
        monkeypatch.setitem(
            sys.modules,
            "lawvm.finland.amendment_index",
            types.SimpleNamespace(get_amendment_children=lambda: {}),
        )
        monkeypatch.setitem(
            sys.modules,
            "lawvm.finland.cross_refs",
            types.SimpleNamespace(extract_cross_refs=lambda xml, sid: []),
        )
        monkeypatch.setitem(
            sys.modules,
            "lawvm.finland.delegation",
            types.SimpleNamespace(extract_delegations=lambda xml, sid: []),
        )

        import lawvm.core.timeline as timeline_mod

        monkeypatch.setattr(
            timeline_mod,
            "compile_timelines",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback timeline compile should not run")),
        )

        return await build_statute_graph_fi("fi/test")

    graph = asyncio.run(_run())

    assert graph.timelines == {"owned": "timeline"}
