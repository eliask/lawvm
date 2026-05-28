from __future__ import annotations

import csv
import types

from lawvm.core.authority import BranchGraphEdge, BranchLifecycleEvent, LegalBranch
from lawvm.core.graph import CorpusGraph
from lawvm.tools.export import export_neo4j


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_export_neo4j_writes_branch_graph_tables(tmp_path, monkeypatch) -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        source_artifact_id="proposal/example/2026/1",
        title="Example proposal",
    )
    edge = BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind="would_replace",
        source_artifact_id="proposal/example/2026/1",
        source_statute_id="proposal/example/2026/1",
        source_unit_id="clause:1",
        target_statute_id="base/1",
        target_address="section:1",
        operation_id="op-1",
    )
    event = BranchLifecycleEvent(
        event_id="event-1",
        branch_id=branch.branch_id,
        event_kind="introduced",
        source_artifact_id="proposal/example/2026/1",
        event_date="2026-01-01",
    )

    async def fake_build_corpus_graph(corpus, *, with_timelines=False):
        assert corpus == ["base/1"]
        assert with_timelines is False
        return CorpusGraph(
            statute_meta={"base/1": {"title": "Base", "statute_type": "act"}},
            branches=[branch],
            branch_edges=[edge],
            branch_lifecycle_events=[event],
        )

    monkeypatch.setitem(
        __import__("sys").modules,
        "lawvm.graph_build",
        types.SimpleNamespace(build_corpus_graph=fake_build_corpus_graph),
    )

    export_neo4j(tmp_path, ["base/1"])

    assert _read_csv(tmp_path / "nodes_branches.csv") == [
        {
            "branch_id": "proposal:example:2026-1",
            "authority_layer": "proposal",
            "legal_status": "unknown",
            "scenario_id": "",
            "parent_branch_id": "",
            "source_artifact_id": "proposal/example/2026/1",
            "title": "Example proposal",
            "terminated_by": "",
        }
    ]
    assert _read_csv(tmp_path / "rels_branch_edges.csv") == [
        {
            "branch_id": "proposal:example:2026-1",
            "edge_kind": "would_replace",
            "source_artifact_id": "proposal/example/2026/1",
            "source_statute_id": "proposal/example/2026/1",
            "source_unit_id": "clause:1",
            "target_statute_id": "base/1",
            "target_address": "section:1",
            "operation_id": "op-1",
            "authority_layer": "proposal",
            "legal_status": "unknown",
        }
    ]
    assert _read_csv(tmp_path / "events_branch_lifecycle.csv") == [
        {
            "event_id": "event-1",
            "branch_id": "proposal:example:2026-1",
            "event_kind": "introduced",
            "source_artifact_id": "proposal/example/2026/1",
            "event_date": "2026-01-01",
            "resulting_status": "unknown",
            "derived_enacted_source_id": "",
        }
    ]
