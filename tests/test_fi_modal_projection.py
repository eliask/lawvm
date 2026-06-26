"""Differential: the SourceSyntaxGraph forest's modal projection vs the modal lenses.

The modal half of the L5 lens→forest projection strangle — following the L3
TEMPLATE (``test_fi_reference_projection``). It proves the forest can REPRODUCE
the DENSE ``DeonticCoreLens`` subset (0-delta BY CONSTRUCTION — same parser) and
CHARACTERISES the registered-actor density gate the SPARSE production
``ActorModalLens`` imposes that the forest does not.

OUTCOME (B): the forest's ``modal_predicate`` leaf is sourced from the modal /
actor_modal family (``parse_modal_sentence``). The DENSE deontic-core lens is a
thin adapter over that SAME parser, so the forest modal projection EQUALS its key
set with 0 delta. The SPARSE production actor_modal lens additionally requires a
REGISTERED actor within 60 chars before the cue, so the forest SUPERSETS it; the
forest-EXTRA keys vs the sparse subset are the registry-independent residual
worklist (actor-underspecified / unregistered-actor cores), not misses.

The differential compares the CANONICAL ``token:polarity:voice`` surface-modality
identity (the production ``SurfaceModality`` key), robust to the construction
``kind`` enrichment and the actor/object surface the two sides represent
differently.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.modal_projection import (
    FOREST_OWNED_DENSE_LENS,
    FOREST_UNOWNED_ACTOR_MODAL_GATES,
    actor_modal_lens_subset_keys,
    dense_lens_modal_keys_for_text,
    diff_forest_vs_modal_lens_subset,
    forest_modal_keys,
    project_forest_modal,
)
from lawvm.finland.legal_surface.source_syntax_graph import assemble_source_syntax_graph
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.references.actor_modal import recognize_actor_modal_frames

_SUBJECT = SurfaceGraphSubject(
    jurisdiction="fi",
    work_id="test/1",
    scope={},
    surface_time=None,
    source_bundle_hash="",
    language="fi",
)


def _forest_for(body: str, statute_id: str):
    return assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id=statute_id, body=body
    )


def _forest_keys_for(body: str, statute_id: str) -> set[str]:
    return forest_modal_keys(_forest_for(body, statute_id), body)


def _actor_modal_keys(body: str) -> set[str]:
    scan = recognize_actor_modal_frames(build_token_tape("x", body))
    return actor_modal_lens_subset_keys(list(scan.frames))


# ── outcome characterisation ────────────────────────────────────────────────


def test_outcome_is_dense_subset_plus_characterised_actor_gate_residual() -> None:
    """The forest owns the dense deontic core; the actor-density gate is surfaced.

    Documents the strangle's frontier: the forest reproduces the DENSE
    deontic-core lens 1:1 (same parser), and the registered-actor density gate the
    SPARSE actor_modal lens imposes is the surfaced residual worklist — never
    hidden.
    """
    assert FOREST_OWNED_DENSE_LENS == "fi.deontic_core.v0"
    assert "actor_underspecified" in FOREST_UNOWNED_ACTOR_MODAL_GATES
    assert "unregistered_actor" in FOREST_UNOWNED_ACTOR_MODAL_GATES


# ── 0-delta vs the DENSE deontic-core lens (the flip gate, by construction) ──


def test_zero_delta_vs_dense_lens_registered_actor() -> None:
    """A registered-actor permission core: forest projection == dense lens.

    ``Tuomioistuin voi …`` — the forest reparses the segment via the same
    ``parse_modal_sentence`` the dense lens uses → identical
    ``voi:affirmative:active`` key.
    """
    body = "Tuomioistuin voi määrätä asiasta tarkemmin."
    statute_id = "2026/100"
    forest_keys = _forest_keys_for(body, statute_id)
    dense_keys = dense_lens_modal_keys_for_text(body)

    diff = diff_forest_vs_modal_lens_subset(forest_keys, dense_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "voi:affirmative:active" in forest_keys, sorted(forest_keys)


def test_zero_delta_vs_dense_lens_impersonal_passive() -> None:
    """An impersonal passive provision verb: forest projection == dense lens.

    ``Asiasta säädetään …`` has NO overt actor (the actor_modal lens emits no
    frame), but the dense lens recognises the passive deontic core from the cue
    alone — and so does the forest, with 0 delta on the
    ``säädetään:affirmative:passive`` key.
    """
    body = "Asiasta säädetään valtioneuvoston asetuksella."
    statute_id = "2026/200"
    forest_keys = _forest_keys_for(body, statute_id)
    dense_keys = dense_lens_modal_keys_for_text(body)

    diff = diff_forest_vs_modal_lens_subset(forest_keys, dense_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "säädetään:affirmative:passive" in forest_keys, sorted(forest_keys)


# ── SUPERSET vs the SPARSE actor_modal lens (the characterised residual) ─────


def test_superset_vs_sparse_actor_modal_unregistered_actor() -> None:
    """An UNREGISTERED actor's core: forest carries it; the sparse lens does not.

    ``Ministeriö saa …`` — the actor is not in the institutional registry, so the
    production actor_modal lens emits NO frame, but the forest recognises the
    deontic core from the cue. The core is therefore a forest-EXTRA vs the sparse
    subset — the registry-independent residual, NOT a miss.
    """
    body = "Ministeriö saa periä maksun."
    statute_id = "2026/300"
    forest_keys = _forest_keys_for(body, statute_id)
    sparse_keys = _actor_modal_keys(body)

    # The sparse lens emits nothing (Ministeriö unregistered) …
    assert sparse_keys == set(), sorted(sparse_keys)
    # … but the forest recovers the deontic core.
    assert "saa:affirmative:active" in forest_keys, sorted(forest_keys)

    diff = diff_forest_vs_modal_lens_subset(forest_keys, sparse_keys)
    # No miss: every (zero) sparse frame is among the forest cores.
    assert diff.forest_missing == frozenset(), sorted(diff.forest_missing)
    # The forest-EXTRA is the registry-gated residual.
    assert "saa:affirmative:active" in diff.forest_extra, sorted(diff.forest_extra)


def test_sparse_actor_modal_is_subset_of_forest_registered_actor() -> None:
    """A registered-actor core appears on BOTH the sparse lens and the forest.

    ``Tuomioistuin voi …`` — the actor IS registered, so the sparse lens DOES emit
    the frame; its modal identity is among the forest cores (no miss). This is the
    sparse subset being a strict SUBSET of the forest (the SUPERSET relation made
    explicit).
    """
    body = "Tuomioistuin voi määrätä asiasta tarkemmin."
    statute_id = "2026/400"
    forest_keys = _forest_keys_for(body, statute_id)
    sparse_keys = _actor_modal_keys(body)

    assert sparse_keys == {"voi:affirmative:active"}, sorted(sparse_keys)
    diff = diff_forest_vs_modal_lens_subset(forest_keys, sparse_keys)
    assert diff.forest_missing == frozenset(), sorted(diff.forest_missing)
    assert "voi:affirmative:active" in diff.identical, sorted(diff.identical)


# ── projection shape sanity ──────────────────────────────────────────────────


def test_projection_is_gated_by_modal_family_membership() -> None:
    """The projection emits facts only for segments the modal family gated.

    A definition provision with no modal cue carries no modal family ownership on
    any leaf and therefore projects no modal segment.
    """
    body = "Tässä pykälässä tarkoitetaan tietojärjestelmällä tietoaineistoa."
    statute_id = "2026/500"
    forest = _forest_for(body, statute_id)
    assert not any("modal" in n.families for n in forest.syntax_nodes.values())
    assert project_forest_modal(forest, body) == ()


def test_gate_recovers_multi_family_modal_span() -> None:
    """A modal core in a MULTI-FAMILY span (minted under another kind) still gates.

    A span owned by modal + condition_exception (+ delegation) is minted by the
    assembler with the lexicographically-first family's KIND (``condition_clause``),
    so a kind-based gate (``nodes_of_kind("modal_predicate")``) would silently drop
    it. The family-membership gate recovers it: the projection finds the modal core
    even though no leaf has ``kind == "modal_predicate"``. This is the regression
    that drove the gate from kind to family membership (corpus dense-lens 0-delta).
    """
    body = (
        "Korvaus maksetaan siltä osin kuin siitä ministeriön asetuksella "
        "tarkemmin säädetään."
    )
    statute_id = "2026/550"
    forest = _forest_for(body, statute_id)
    # The span is owned by the modal family …
    assert any("modal" in n.families for n in forest.syntax_nodes.values())
    # … but (being multi-family) NOT minted as a modal_predicate-kind leaf.
    modal_kind_leaves = forest.nodes_of_kind("modal_predicate")
    family_modal_leaves = [
        n for n in forest.syntax_nodes.values() if "modal" in n.families
    ]
    assert len(family_modal_leaves) > len(modal_kind_leaves)
    # The family-membership gate still recovers the deontic core.
    forest_keys = forest_modal_keys(forest, body)
    assert "säädetään:affirmative:passive" in forest_keys, sorted(forest_keys)
    # And it matches the dense lens with 0 delta.
    dense_keys = dense_lens_modal_keys_for_text(body)
    diff = diff_forest_vs_modal_lens_subset(forest_keys, dense_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )


def test_projected_modal_anchors_to_enclosing_segment() -> None:
    """Each projected modal segment anchors to a real structural segment node."""
    body = "Tuomioistuin voi määrätä asiasta tarkemmin."
    statute_id = "2026/600"
    forest = _forest_for(body, statute_id)
    projected = project_forest_modal(forest, body)
    assert projected, "expected one projected modal segment"
    p = projected[0]
    assert p.segment_node_id in forest.syntax_nodes
    assert "voi" in body[p.char_start : p.char_end]
    assert p.cores


# ── the PRODUCTION node-seed flip projection (doc-6 strangle-flip) ────────────


def test_forest_deontic_core_seed_projection_matches_independent_scan() -> None:
    """The forest deontic_core seed projection == the independent per-sentence scan.

    The node-level flip gate: ``project_forest_deontic_core_seeds`` (production now
    reads the forest) reproduces the golden-reference independent scan
    (``deontic_core_seeds_for_unit``) node-identically over a multi-shape body
    (permission / impersonal obligation / passive provision verb / prohibition).
    """
    from lawvm.finland.legal_surface.modal_projection import (
        project_forest_deontic_core_seeds,
    )

    _AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

    def _xml(*paras: str) -> bytes:
        body = "\n".join(f"      <p>{p}</p>" for p in paras)
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
            f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
            f"  </content></section>\n"
            f"</body></act></akomaNtoso>\n"
        ).encode("utf-8")

    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.legal_surface.lenses.deontic_core import (
        deontic_core_seeds_for_unit,
    )

    bundle = build_surface_bundle(
        _xml(
            "Hakija voi pyytaa oikaisua.",
            "Hakemus on toimitettava maaraajassa.",
            "Asiasta saadetaan valtioneuvoston asetuksella.",
            "Viranomainen ei saa luovuttaa tietoa.",
        ),
        "2026/700",
    )
    forest_seeds = project_forest_deontic_core_seeds(bundle)
    golden = [s for u in bundle.units for s in deontic_core_seeds_for_unit(u)]
    assert forest_seeds, "expected deontic cores from the forest projection"

    def _fp(seed):
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
            None if ref is None else (ref.char_start, ref.char_end, ref.text_hash),
            payload,
        )

    assert {_fp(s) for s in forest_seeds} == {_fp(s) for s in golden}
