from __future__ import annotations
from typing import Any, Mapping, cast

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion

from lawvm.core.authority import BranchGraphEdge, LegalBranch
from lawvm.core.graph import CorpusGraph
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.cross_refs import CrossRefEdge
from lawvm.finland.delegation import DelegationEdge


def test_silent_breakage_returns_none_for_ambiguous_same_label_sections() -> None:
    ch1_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    ch2_addr = LegalAddress(path=(("chapter", "2"), ("section", "5")))

    timelines = {
        "fi/source": {
            ch1_addr: ProvisionTimeline(
                address=ch1_addr,
                versions=(
                    [
                        ProvisionVersion(
                            effective="2000-01-01",
                            enacted="2000-01-01",
                            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="active"),
                        )
                    ]
                ),
            ),
            ch2_addr: ProvisionTimeline(
                address=ch2_addr,
                versions=(
                    [
                        ProvisionVersion(
                            effective="2000-01-01",
                            enacted="2000-01-01",
                            content=None,
                        )
                    ]
                ),
            ),
        }
    }

    graph = CorpusGraph(
        timelines=timelines,
        citations=[
            CrossRefEdge(
                source_statute_id="fi/source",
                target_statute_id="fi/target",
                edge_type="CITES",
                source_section="5",
                target_section="section:9",
            )
        ],
    )

    rows = graph.silent_breakage("fi/target", as_of="2001-01-01")

    assert len(rows) == 1
    assert rows[0]["active_at_date"] is None


def test_graph_query_helpers_return_canonical_ordering() -> None:
    graph = CorpusGraph(
        citations=[
            CrossRefEdge(
                source_statute_id="fi/b",
                target_statute_id="fi/x",
                edge_type="CITES",
                source_section="9",
                target_section="section:2",
                count=1,
            ),
            CrossRefEdge(
                source_statute_id="fi/a",
                target_statute_id="fi/x",
                edge_type="CITES",
                source_section="1",
                target_section="section:1",
                count=1,
            ),
        ],
        delegations=[
            DelegationEdge(
                statute_id="fi/a",
                section="9",
                eid="e2",
                delegation_type="ASETUS",
                match_text="later",
                quote="later",
            ),
            DelegationEdge(
                statute_id="fi/a",
                section="1",
                eid="e1",
                delegation_type="ASETUS",
                match_text="earlier",
                quote="earlier",
            ),
        ],
        amendment_index={"fi/x": ["2005/2", "2004/10"]},
        statute_meta={"fi/a": {"title": "A", "statute_type": "act"}},
    )

    statute = graph.get_statute("fi/a")

    assert statute is not None
    assert [edge.source_section for edge in graph.reverse_citations("fi/x")] == ["1", "9"]
    assert [edge.section for edge in graph.delegation_chain("fi/a")] == ["1", "9"]
    assert graph.affecting_acts("fi/x") == ["2004/10", "2005/2"]
    assert [edge.source_section for edge in statute.citations] == ["1"]
    assert [edge.section for edge in statute.delegations] == ["1", "9"]
    assert statute.amendment_chain == []


def test_corpus_graph_exposes_branch_edges_without_live_materialization_claim() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        legal_status="unknown",
        scenario_id="if_enacted_as_introduced",
        source_artifact_id="proposal/example/2026/1",
        title="Example proposal 2026/1",
    )
    edge = BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind="would_replace",
        source_artifact_id="proposal/example/2026/1",
        source_statute_id="proposal/example/2026/1",
        source_unit_id="clause:1",
        target_statute_id="fi/target",
        target_address="section:5",
        operation_id="proposal-op-1",
        authority_layer="proposal",
        legal_status="unknown",
    )
    graph = CorpusGraph(
        branches=[branch],
        branch_edges=[edge],
        statute_meta={"fi/target": {"title": "Target", "statute_type": "act"}},
    )

    statute = graph.get_statute("fi/target")
    artifact_payload = cast(Mapping[str, Any], graph.to_wire_artifact().payload)
    counts = cast(Mapping[str, Any], artifact_payload["counts"])
    branches = cast(tuple[Mapping[str, Any], ...], artifact_payload["branches"])
    branch_edges = cast(tuple[Mapping[str, Any], ...], artifact_payload["branch_edges"])

    assert statute is not None
    assert statute.branches == [branch]
    assert statute.branch_edges == [edge]
    assert graph.branch_edges_for_statute("fi/target") == [edge]
    assert counts["branches"] == 1
    assert counts["branch_edges"] == 1
    assert branches[0]["authority_layer"] == "proposal"
    assert branch_edges[0]["edge_kind"] == "would_replace"
