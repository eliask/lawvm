"""Gate for the deontic_core surface lens (the dense Layer-2 deontic substrate).

The :class:`DeonticCoreLens` mints one ``deontic_core`` node per construction
modal core (:func:`parse_modal_sentence`) in each sentence of each unit — the
DENSE node substrate the Layer-2 condition/exception attachment edges point at.
It runs ALONGSIDE the sparse production ``actor_modal_frame`` lens (additive
strangle), never replacing it.

  (a) a deontic_core node is minted from a modal sentence with the correct
      surface payload (kind/polarity/voice/addressee/object + cue span);
  (b) the norm edge now attaches a condition to a deontic_core that has NO
      production actor_modal_frame node (the impersonal/unregistered-actor case);
  (c) firewall: every deontic_core node + every NORM edge is surface_only /
      not replay_authorized;
  (d) graph-id / payload-hash invariants hold with the new node kind (stable
      across reruns; deterministic);
  (e) candidate-not-asserted is preserved (no core in sentence -> no edge);
  (f) the production actor_modal_frame lens still runs alongside (not removed).
"""
from __future__ import annotations

from typing import cast

from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses.deontic_core import (
    DEONTIC_CORE_NODE_KIND,
    DeonticCoreLens,
)
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_CONDITION_ATTACHES,
    EDGE_EXCEPTION_EXCEPTS,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_NORM_KINDS = {EDGE_CONDITION_ATTACHES, EDGE_EXCEPTION_EXCEPTS}


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# ── (a) the lens mints a deontic_core node with the right surface payload ─────


def test_deontic_core_node_minted_with_surface_payload() -> None:
    # A permission core with an overt subject; no registered actor needed.
    xml = _xml("Hakija voi pyytaa oikaisua paatokseen.")
    graph = build_legal_surface_graph(xml, "100/2025")
    cores = [n for n in graph.nodes.values() if n.node_kind == DEONTIC_CORE_NODE_KIND]
    assert cores, "expected at least one deontic_core node"
    permissions = [n for n in cores if n.payload.get("kind") == "permission"]
    assert permissions, "expected a permission deontic core (voi)"
    node = permissions[0]
    payload = cast(dict[str, object], node.payload)
    assert payload["cue"] == "voi"
    assert payload["polarity"] == "affirmative"
    assert payload["voice"] == "active"
    assert payload["source"] == "construction_modal_parse"
    # the node's anchoring span is the modal cue span (raw_text coords)
    ref = node.source_ref
    assert ref is not None
    cue_span = cast(tuple[int, int], payload["cue_span"])
    assert ref.char_start == cue_span[0]
    assert ref.char_end == cue_span[1]


def test_deontic_core_lens_is_dense_relative_to_actor_modal_frame() -> None:
    # An IMPERSONAL obligation ("on toimitettava" with no overt registered actor)
    # yields NO production actor_modal_frame, but DOES yield a deontic_core node.
    xml = _xml("Hakemus on toimitettava maaraajassa.")
    graph = build_legal_surface_graph(xml, "101/2025")
    cores = [n for n in graph.nodes.values() if n.node_kind == DEONTIC_CORE_NODE_KIND]
    assert cores, "the construction recognizes the impersonal obligation core"
    obligations = [n for n in cores if n.payload.get("kind") == "obligation"]
    assert obligations, "expected an obligation deontic core (on + -ttava)"


# ── (b) a condition attaches to a deontic_core that has no production frame ───


def test_condition_attaches_to_deontic_core_without_production_frame() -> None:
    # "Jos X, hakemus on hylattava." — the matrix core is an impersonal necessive
    # ("on hylattava") with NO registered actor, so the production actor_modal lens
    # mints NO actor_modal_frame for it. Before this lens the condition produced no
    # edge; now it attaches to the deontic_core node.
    xml = _xml("Jos hakemus on puutteellinen, se on hylattava.")
    graph = build_legal_surface_graph(xml, "102/2025")
    conds = [e for e in graph.edges if e.edge_kind == EDGE_CONDITION_ATTACHES]
    assert conds, "expected a condition_attaches_norm edge to a deontic_core"
    for edge in conds:
        dst = graph.nodes[edge.dst]
        assert dst.node_kind == DEONTIC_CORE_NODE_KIND
        # and the matrix core had NO production actor_modal_frame node
    frames = [n for n in graph.nodes.values() if n.node_kind == "actor_modal_frame"]
    assert not frames, (
        "this impersonal-obligation sentence yields NO production actor_modal_frame; "
        "the edge would have been unbacked before deontic_core nodes existed"
    )


# ── (c) firewall over every deontic_core node + every NORM edge ───────────────


def test_deontic_core_nodes_and_norm_edges_obey_firewall() -> None:
    xml = _xml(
        "Hakija voi pyytaa oikaisua.",
        "Jos hakemus on puutteellinen, se on hylattava.",
    )
    graph = build_legal_surface_graph(xml, "103/2025")
    cores = [n for n in graph.nodes.values() if n.node_kind == DEONTIC_CORE_NODE_KIND]
    assert cores
    for node in cores:
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in (e for e in graph.edges if e.edge_kind in _NORM_KINDS):
        assert edge.surface_only is True
        assert edge.replay_authorized is False


# ── (d) graph-id / payload-hash invariants hold with the new node kind ────────


def test_graph_id_and_payload_hash_invariants_with_deontic_core() -> None:
    xml = _xml("Hakija voi pyytaa oikaisua.", "Hakemus on toimitettava maaraajassa.")
    g1 = build_legal_surface_graph(xml, "104/2025")
    g2 = build_legal_surface_graph(xml, "104/2025")
    assert g1.graph_id == g2.graph_id
    cores1 = {
        n.node_id: n.payload_hash
        for n in g1.nodes.values()
        if n.node_kind == DEONTIC_CORE_NODE_KIND
    }
    cores2 = {
        n.node_id: n.payload_hash
        for n in g2.nodes.values()
        if n.node_kind == DEONTIC_CORE_NODE_KIND
    }
    assert cores1 == cores2 and cores1, "deontic_core ids + payload hashes stable"


# ── (e) candidate-not-asserted preserved (no core in sentence -> no edge) ─────


def test_candidate_no_core_still_emits_no_edge() -> None:
    # A condition whose matrix carries NO modal core -> no deontic_core to attach
    # to -> no asserted edge (candidate-not-asserted preserved).
    xml = _xml("Jos hakemus on puutteellinen, asia raukeaa.")
    graph = build_legal_surface_graph(xml, "105/2025")
    # the matrix "asia raukeaa" has no modal cue -> the only core (if any) is from
    # "on puutteellinen" which is a copula, not necessive -> no deontic core.
    cores = [n for n in graph.nodes.values() if n.node_kind == DEONTIC_CORE_NODE_KIND]
    assert not cores, "a copula-only sentence yields no deontic core"
    norm = [e for e in graph.edges if e.edge_kind in _NORM_KINDS]
    assert not norm, "no deontic core -> no asserted norm edge (candidate-not-asserted)"


# ── (f) the production actor_modal_frame lens still runs alongside ────────────


def test_production_actor_modal_frame_lens_still_runs() -> None:
    # A registered-actor modal ("Valtioneuvosto voi antaa ...") still yields a
    # production actor_modal_frame AND a deontic_core node (strangle, not removal).
    xml = _xml("Valtioneuvosto voi antaa tarkempia saannoksia asetuksella.")
    graph = build_legal_surface_graph(xml, "106/2025")
    kinds = {n.node_kind for n in graph.nodes.values()}
    assert "actor_modal_frame" in kinds, "production lens must still run alongside"
    assert DEONTIC_CORE_NODE_KIND in kinds, "deontic_core lens runs too"


# ── lens contract sanity ──────────────────────────────────────────────────────


def test_lens_declares_its_kinds() -> None:
    lens = DeonticCoreLens()
    assert lens.produces_node_kinds == (DEONTIC_CORE_NODE_KIND,)
    assert lens.produces_edge_kinds == ()
    bundle = build_surface_bundle(_xml("Hakija voi pyytaa oikaisua."), "107/2025")
    from lawvm.core.legal_surface_lens import SurfaceAnalysisContext

    result = lens.analyze(bundle, context=SurfaceAnalysisContext())
    assert result.node_seeds, "lens emits deontic_core node seeds"
    assert result.edge_seeds == ()


# ── (g) THE PRODUCTION STRANGLE-FLIP: production reads the forest, 0-delta ─────


def _seed_fp(seed) -> tuple:
    """A node-identity fingerprint of a deontic_core seed (span/discr/payload)."""
    ref = seed.source_ref
    payload = tuple(
        sorted(
            (k, tuple(v) if isinstance(v, list) else v)
            for k, v in dict(seed.payload).items()
        )
    )
    return (
        seed.node_kind,
        seed.local_discriminator,
        seed.rule_id,
        seed.node_status,
        seed.authority_role,
        None if ref is None else (ref.char_start, ref.char_end, ref.text_hash),
        payload,
    )


def test_production_deontic_facts_derive_from_forest_and_are_identical() -> None:
    """The flip happened (production projects from the forest) AND is 0-delta.

    (1) The production lens now mints its deontic_core seeds via the cached
        SourceSyntaxGraph forest projection
        (``modal_projection.project_forest_deontic_core_seeds``) — not an
        independent body scan;
    (2) those seeds are node-identical (span / discriminator / payload) to the
        pre-flip independent per-sentence scan, kept as the golden reference
        (``deontic_core_seeds_for_unit``).
    """
    from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
    from lawvm.finland.legal_surface.lenses.deontic_core import (
        deontic_core_seeds_for_unit,
    )
    from lawvm.finland.legal_surface.modal_projection import (
        project_forest_deontic_core_seeds,
    )

    xml = _xml(
        "Hakija voi pyytaa oikaisua paatokseen.",
        "Hakemus on toimitettava maaraajassa.",
        "Jos hakemus on puutteellinen, se on hylattava.",
        "Asiasta saadetaan valtioneuvoston asetuksella.",
        "Viranomainen ei saa luovuttaa tietoa.",
    )
    bundle = build_surface_bundle(xml, "108/2025")

    # (1) the production lens emits exactly the forest projection's seeds …
    lens = DeonticCoreLens()
    lens_seeds = lens.analyze(bundle, context=SurfaceAnalysisContext()).node_seeds
    forest_seeds = project_forest_deontic_core_seeds(bundle)
    assert {_seed_fp(s) for s in lens_seeds} == {_seed_fp(s) for s in forest_seeds}
    assert forest_seeds, "expected deontic cores from the forest projection"

    # (2) … and the forest projection is node-identical to the golden-reference
    # independent scan (the pre-flip behaviour) — the 0-delta flip gate.
    golden = [s for u in bundle.units for s in deontic_core_seeds_for_unit(u)]
    assert {_seed_fp(s) for s in forest_seeds} == {_seed_fp(s) for s in golden}
    assert len(forest_seeds) == len(golden)
