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
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface import source_syntax_graph as ssg
from lawvm.finland.legal_surface.source_syntax_graph import (
    SYNTAX_EDGE_KINDS,
    SYNTAX_NODE_KINDS,
    SourceSyntaxGraph,
    _assemble_source_syntax_graph,
    assemble_source_syntax_graph,
    assemble_source_syntax_graph_for_unit,
    clear_forest_cache,
    project_list_inheritance,
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


def test_list_construction_binds_chapeau_frame_onto_items() -> None:
    # A chapeau setting a deontic frame (``Viranomainen voi …`` — permission) +
    # two governed items. The L2 ListConstruction must bind the chapeau's
    # actor/modal frame onto EACH item via the reserved has_subject / has_condition
    # edges, and the projection must emit one frame-inheriting sub-norm per item.
    body = (
        "Viranomainen voi myöntää luvan seuraavin edellytyksin:\n"
        "hakija täyttää vaatimukset;\n"
        "toiminta on turvallista.\n"
    )
    forest = _assemble(body)

    # The chapeau carries a parseable modal frame leaf.
    modals = forest.nodes_of_kind("modal_predicate")
    assert modals, [n.kind for n in forest.syntax_nodes.values()]

    # Exactly one ListConstruction, with an inherited frame and two items.
    assert len(forest.list_constructions) == 1
    lc = forest.list_constructions[0]
    assert lc.frame_status == "inherited"
    assert lc.is_inherited
    assert lc.frame_node_id
    assert len(lc.item_ids) == 2

    # The frame node is the chapeau's modal leaf, contained in the chapeau span.
    chapeau = next(
        n for n in forest.nodes_of_kind("chapeau") if n.node_id == lc.chapeau_id
    )
    frame = forest.syntax_nodes[lc.frame_node_id]
    assert frame.kind == "modal_predicate"
    assert chapeau.char_start <= frame.char_start and frame.char_end <= chapeau.char_end

    # Each item is bound to the chapeau's frame by the reserved edges.
    has_subject = forest.edges_of_kind("has_subject")
    has_condition = forest.edges_of_kind("has_condition")
    assert len(has_subject) == 2
    assert len(has_condition) == 2
    item_ids = set(lc.item_ids)
    # has_subject: item -> frame leaf (item inherits the chapeau's actor/modal).
    assert all(e.src in item_ids and e.dst == lc.frame_node_id for e in has_subject)
    # has_condition: frame leaf -> item (item is a condition-set member of the norm).
    assert all(e.src == lc.frame_node_id and e.dst in item_ids for e in has_condition)

    # The structural inherits_chapeau link still holds for each item.
    inherit = forest.edges_of_kind("inherits_chapeau")
    assert len(inherit) == 2
    assert all(e.src in item_ids and e.dst == lc.chapeau_id for e in inherit)

    # Projection: one frame-inheriting sub-norm per item, carrying the modal family.
    projected = project_list_inheritance(forest)
    assert len(projected) == 2
    assert all(p.frame_node_id == lc.frame_node_id for p in projected)
    assert all(p.chapeau_id == lc.chapeau_id for p in projected)
    assert all("modal" in p.inherited_families for p in projected)
    # Each projected sub-norm's item span recovers the verbatim item text.
    for p in projected:
        assert body[p.item_span[0] : p.item_span[1]].strip()

    # Edge/node vocab unchanged (no new kinds invented).
    assert {e.kind for e in forest.syntax_edges} <= SYNTAX_EDGE_KINDS
    assert {n.kind for n in forest.syntax_nodes.values()} <= SYNTAX_NODE_KINDS


def test_chapeau_without_parseable_frame_leaves_items_unattached() -> None:
    # A chapeau with NO parseable deontic/modal frame (a definitional lead-in,
    # ``Tässä laissa tarkoitetaan seuraavaa:``) must NOT fabricate inheritance:
    # the items keep their structural inherits_chapeau link but get NO frame edge,
    # and the construction is recorded ``unsupported`` (fail-loud).
    body = (
        "Tässä laissa tarkoitetaan seuraavaa:\n"
        "sivutuotteella eläinperäistä materiaalia;\n"
        "jätteellä hylättyä ainetta.\n"
    )
    forest = _assemble(body)

    # No modal frame leaf inside the chapeau → no governing frame.
    assert len(forest.list_constructions) == 1
    lc = forest.list_constructions[0]
    assert lc.frame_status == "unsupported"
    assert not lc.is_inherited
    assert lc.frame_node_id == ""
    assert len(lc.item_ids) == 2

    # NO frame edge fabricated.
    assert not forest.edges_of_kind("has_subject")
    assert not forest.edges_of_kind("has_condition")
    # Structural inheritance still holds (items not dropped).
    assert len(forest.edges_of_kind("inherits_chapeau")) == 2
    # Projection emits nothing for an unsupported construction.
    assert project_list_inheritance(forest) == ()


def test_list_inheritance_preserves_coverage_consistency_vs_l0() -> None:
    # The L2 list-inheritance construction adds only edges + a typed view; it must
    # NOT perturb the L0 union-ownership coverage the forest carries (no token
    # previously owned becomes unowned; no silent-unowned span appears).
    for body in (
        "Viranomainen voi myöntää luvan seuraavin edellytyksin:\n"
        "hakija täyttää vaatimukset;\n"
        "toiminta on turvallista.\n",
        "Toiminnanharjoittaja ei saa:\n"
        "laiminlyödä velvollisuuksiaan;\n"
        "vaarantaa turvallisuutta.\n",
        "Tässä laissa tarkoitetaan seuraavaa:\n"
        "sivutuotteella eläinperäistä materiaalia;\n"
        "jätteellä hylättyä ainetta.\n",
    ):
        forest = _assemble(body)
        bc, fc, usc, _ex, _sc = classify_body("test/1", body)
        cov = forest.coverage
        assert cov.total_tokens == sum(bc.values()), body
        assert cov.owned_tokens == bc.get("owned", 0), body
        assert cov.silent_tokens == bc.get("silent", 0), body
        assert cov.is_partition(), body
        # Every edge endpoint still resolves to a node (graph invariant).
        node_ids = set(forest.syntax_nodes)
        for e in forest.syntax_edges:
            assert e.src in node_ids and e.dst in node_ids, body


def test_bare_section_ref_is_a_forest_reference_np_leaf() -> None:
    # A bare same-statute § cross-reference (``5 §:ssä``) — the top L0
    # section_mark bucket — is now carried by the forest as a reference_np leaf
    # owned by the internal-ref recognizer, and is no longer silent-unowned.
    body = "Edellä 5 §:ssä tarkoitettu lupa myönnetään hakemuksesta."
    forest = _assemble(body)
    refs = forest.nodes_of_kind("reference_np")
    assert refs, [n.kind for n in forest.syntax_nodes.values()]
    internal = [n for n in refs if "ref_internal" in n.families]
    assert internal, [n.families for n in refs]
    # The leaf recovers the verbatim § surface.
    assert any(body[n.char_start : n.char_end] == "5 §:ssä" for n in internal)
    # No section_mark left silent-unowned.
    assert forest.coverage.unowned_shape_counts.get("section_mark", 0) == 0
    assert forest.coverage.silent_tokens == 0, forest.coverage


def test_by_name_cross_ref_is_a_forest_reference_np_leaf() -> None:
    # A by-name cross-statute § reference (``jätelain 5 §:ssä``) is forest-owned:
    # the by-name recognizer's span is a reference_np leaf (multi-family where it
    # overlaps the modal surface — no span dropped), and the section_mark signal
    # is no longer silent-unowned.
    body = "Tarkemmat säännökset annetaan jätelain 5 §:ssä."
    forest = _assemble(body)
    by_name_owned = [
        n
        for n in forest.syntax_nodes.values()
        if "ref_by_name" in n.families
    ]
    assert by_name_owned, [
        (n.kind, n.families) for n in forest.syntax_nodes.values()
    ]
    assert forest.coverage.unowned_shape_counts.get("section_mark", 0) == 0


def test_ref_element_boundary_case_stays_deferred_not_forest_owned() -> None:
    # SCOPE BOUNDARY (grammar7 annotation-is-witness): an editorial-annotation
    # cheap signal that NO body-text construction recognizer owns — here an ``HE``
    # preparatory-work reference (the <ref>/footer-annotation lane's territory,
    # DELIBERATELY EXCLUDED from the body-text reference roster) — is NOT promoted
    # to a forest-owned reference_np leaf. It stays a self-evidencing residual
    # (surfaced, never silently dropped; the deferred-by-design boundary).
    body = "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti."
    forest = _assemble(body)
    # No reference_np leaf claims the HE reference.
    he_owned = [
        n
        for n in forest.nodes_of_kind("reference_np")
        if "HE 5/2019" in body[n.char_start : n.char_end]
    ]
    assert not he_owned, he_owned
    # It remains a surfaced residual (the deferred boundary, self-evidencing).
    he_residual = [r for r in forest.residuals if r.shape == "he_ref"]
    assert he_residual, forest.residuals
    assert "HE 5/2019" in he_residual[0].text


def test_reference_recognizers_preserve_coverage_consistency_vs_l0() -> None:
    # The reference ownership source must keep the forest coverage EQUAL to the L0
    # ruler on the same body (both consume the one union), for reference-bearing
    # provisions too.
    for body in (
        "Edellä 5 §:ssä tarkoitettu lupa myönnetään hakemuksesta.",
        "Tarkemmat säännökset annetaan jätelain 5 §:ssä.",
        "Yleissopimuksen (SopS 11/2020) mukaan asia ratkaistaan.",
        "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti.",
    ):
        forest = _assemble(body)
        bc, fc, usc, _ex, _sc = classify_body("test/1", body)
        cov = forest.coverage
        assert cov.total_tokens == sum(bc.values()), body
        assert cov.owned_tokens == bc.get("owned", 0), body
        assert cov.silent_tokens == bc.get("silent", 0), body
        assert cov.is_partition(), body
        assert cov.family_token_counts == dict(fc), body
        assert cov.unowned_shape_counts == dict(usc), body
        # All node/edge kinds stay in the closed taxonomy.
        assert {n.kind for n in forest.syntax_nodes.values()} <= SYNTAX_NODE_KINDS
        assert {e.kind for e in forest.syntax_edges} <= SYNTAX_EDGE_KINDS


def test_totality_promotes_silent_unowned_to_unsupported(monkeypatch) -> None:
    body = "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti."
    monkeypatch.setenv("LAWVM_PARSE_TOTALITY", "1")
    forest = _assemble(body)
    assert forest.parse_status == "unsupported", forest.parse_status
    residual_nodes = forest.nodes_of_kind("residual_span")
    assert residual_nodes
    assert all(n.node_status == "unsupported" for n in residual_nodes)


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


def _forests_value_equal(a: SourceSyntaxGraph, b: SourceSyntaxGraph) -> None:
    """Assert two forests are VALUE-identical (every observable field)."""
    assert a.graph_id == b.graph_id
    assert a.subject == b.subject
    assert a.source_units == b.source_units
    assert a.text_hash == b.text_hash
    assert a.text_len == b.text_len
    assert a.parse_status == b.parse_status
    assert a.syntax_nodes == b.syntax_nodes
    assert a.syntax_edges == b.syntax_edges
    assert a.residuals == b.residuals
    assert a.coverage == b.coverage
    assert a.list_constructions == b.list_constructions


def test_cache_hit_returns_same_object_value_identical_to_fresh() -> None:
    # The memoized assembler must return the SAME cached object on a repeat call,
    # and that object must be value-identical to a fresh (uncached) assembly.
    clear_forest_cache()
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."

    first = _assemble(body)
    second = _assemble(body)
    # Cache hit: same identity (frozen, immutable → safe to share).
    assert first is second

    # Value-identical to a fresh, uncached assembly (the cache alters no output).
    fresh = _assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id="test/1", body=body
    )
    assert fresh is not first
    _forests_value_equal(first, fresh)


def test_unit_assembler_reuses_prebuilt_substrate_views(monkeypatch) -> None:
    clear_forest_cache()
    xml = """
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act><body><section><paragraph><content>
        <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.</p>
      </content></paragraph></section></body></act>
    </akomaNtoso>
    """.encode()
    bundle = build_surface_bundle(xml, "test/1")
    unit = bundle.units[0]
    fresh = _assemble_source_syntax_graph(
        subject=bundle.subject,
        source_units=(),
        statute_id=unit.source_unit_id,
        body=unit.raw_text,
    )

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("prebuilt substrate view was not reused")

    monkeypatch.setattr(ssg, "build_segmentation_graph", fail_rebuild)
    monkeypatch.setattr(ssg, "build_clause_index", fail_rebuild)
    clear_forest_cache()

    reused = assemble_source_syntax_graph_for_unit(
        subject=bundle.subject,
        unit=unit,
    )

    _forests_value_equal(reused, fresh)


def test_cache_keyed_on_inputs_distinct_bodies_distinct_forests() -> None:
    # Different inputs must NOT collide in the cache.
    clear_forest_cache()
    f1 = _assemble("Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.")
    f2 = _assemble("Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti.")
    assert f1 is not f2
    assert f1.text_hash != f2.text_hash
    assert f1.graph_id != f2.graph_id


def test_idempotent_assemble_two_uncached_builds_equal() -> None:
    # The assembler itself is deterministic: two independent uncached builds of the
    # same unit are value-identical (the property the cache relies on for soundness).
    body = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."
    a = _assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id="test/1", body=body
    )
    b = _assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id="test/1", body=body
    )
    assert a is not b
    _forests_value_equal(a, b)


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
