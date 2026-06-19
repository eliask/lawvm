"""Focused tests for the per-provision SourceSyntaxGraph forest assembler.

Exercises the L1 object + assembler on synthetic provisions — no corpus:

  * a fully-parsed provision (a commencement clause the temporal family owns) →
    forest with the right construction node + ``parse_status='parsed'`` + a
    coverage that EQUALS the L0 union-ownership numbers on the same body;
  * a provision with an unowned cheap-signal span (an ``HE`` ref in plain prose) →
    ``parse_status='partial_with_residuals'`` + a self-evidencing residual node;
  * the firewall: the module imports NOTHING from apply/replay;
  * the consistency check: the forest's coverage equals L0's ``classify_body``
    numbers on the same provision (the key correctness invariant — the forest
    carries the ruler, it does not re-define it).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.source_syntax_graph import (
    SYNTAX_EDGE_KINDS,
    SYNTAX_NODE_KINDS,
    SourceSyntaxGraph,
    assemble_source_syntax_graph,
)
from lawvm.finland.legal_surface.union_ownership_census import classify_body

_SUBJECT = SurfaceGraphSubject(
    jurisdiction="fi",
    work_id="test/1",
    scope={},
    surface_time=None,
    source_bundle_hash="",
    language="fi",
)


def _assemble(body: str) -> SourceSyntaxGraph:
    return assemble_source_syntax_graph(
        subject=_SUBJECT,
        source_units=(),
        statute_id="test/1",
        body=body,
    )


def test_fully_parsed_commencement_provision() -> None:
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."
    forest = _assemble(body)

    # The temporal family owns the commencement cue + date → a temporal_phrase leaf.
    temporal = forest.nodes_of_kind("temporal_phrase")
    assert temporal, [n.kind for n in forest.syntax_nodes.values()]
    assert any("temporal" in n.families for n in temporal)

    # Every cheap signal owned, no typed residual → parsed.
    assert forest.parse_status == "parsed", forest.parse_status
    assert not forest.residuals

    # node/edge kinds are all in the closed taxonomy.
    assert {n.kind for n in forest.syntax_nodes.values()} <= SYNTAX_NODE_KINDS
    assert {e.kind for e in forest.syntax_edges} <= SYNTAX_EDGE_KINDS


def test_unowned_cheap_signal_is_partial_with_self_evidencing_residual() -> None:
    body = "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti."
    forest = _assemble(body)

    assert forest.parse_status == "partial_with_residuals", forest.parse_status
    he = [r for r in forest.residuals if r.shape == "he_ref"]
    assert he, forest.residuals
    # Self-evidencing: the residual embeds the verbatim offending span text.
    assert "HE 5/2019" in he[0].text
    assert body[he[0].char_start : he[0].char_end] == he[0].text

    # A residual_span NODE exists too (the no-silent-drop witness in the graph).
    residual_nodes = forest.nodes_of_kind("residual_span")
    assert residual_nodes
    assert any("HE 5/2019" in n.residual_text for n in residual_nodes)
    assert all(n.residual_reason for n in residual_nodes)


def test_coverage_equals_l0_ruler_on_same_body() -> None:
    # THE KEY CORRECTNESS CHECK: the forest's SyntaxCoverage is the L0
    # union-ownership partition for the same body, by construction.
    for body in (
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.",
        "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti.",
        "Viranomainen voi antaa tarkempia säännöksiä asetuksella.",
        "Tässä laissa tarkoitetaan sivutuotteella eläinperäistä materiaalia.",
    ):
        forest = _assemble(body)
        bc, fc, usc, _ex, _sc = classify_body("test/1", body)
        cov = forest.coverage
        assert cov.total_tokens == sum(bc.values()), body
        assert cov.owned_tokens == bc.get("owned", 0), body
        assert cov.benign_tokens == bc.get("benign", 0), body
        assert cov.residual_tokens == bc.get("residual", 0), body
        assert cov.silent_tokens == bc.get("silent", 0), body
        assert cov.is_partition(), body
        assert cov.family_token_counts == dict(fc), body
        assert cov.unowned_shape_counts == dict(usc), body


def test_structural_skeleton_and_list_inheritance() -> None:
    # A chapeau governing two list items → list_item nodes with inherits_chapeau.
    body = (
        "Lupa voidaan myöntää, jos:\n"
        "hakija täyttää edellytykset;\n"
        "toiminta ei vaaranna turvallisuutta.\n"
    )
    forest = _assemble(body)
    chapeaux = forest.nodes_of_kind("chapeau")
    items = forest.nodes_of_kind("list_item")
    assert chapeaux, [n.kind for n in forest.syntax_nodes.values()]
    assert items, [n.kind for n in forest.syntax_nodes.values()]
    inherit = forest.edges_of_kind("inherits_chapeau")
    assert inherit
    chapeau_ids = {n.node_id for n in chapeaux}
    assert all(e.dst in chapeau_ids for e in inherit)


def test_totality_promotes_silent_unowned_to_unsupported(monkeypatch) -> None:
    body = "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti."
    monkeypatch.setenv("LAWVM_PARSE_TOTALITY", "1")
    forest = _assemble(body)
    assert forest.parse_status == "unsupported", forest.parse_status
    residual_nodes = forest.nodes_of_kind("residual_span")
    assert residual_nodes
    assert all(n.status == "unsupported" for n in residual_nodes)


def test_graph_invariants_hold() -> None:
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."
    forest = _assemble(body)
    # Every edge endpoint resolves to a node (enforced in __post_init__).
    node_ids = set(forest.syntax_nodes)
    for e in forest.syntax_edges:
        assert e.src in node_ids
        assert e.dst in node_ids
    # The graph_id is stable across reruns of the same body.
    assert forest.graph_id == _assemble(body).graph_id


def test_firewall_no_apply_or_replay_import() -> None:
    # The forest is surface-only: the module must import NOTHING from apply/replay.
    module_path = Path(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "lawvm",
            "finland",
            "legal_surface",
            "source_syntax_graph.py",
        )
    ).resolve()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = [m for m in imported if "apply" in m or "replay" in m]
    assert not forbidden, f"surface-only module must not import apply/replay: {forbidden}"
