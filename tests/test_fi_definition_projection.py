"""Differential: the SourceSyntaxGraph forest's definition projection vs the lens.

The definitions half of the lens→forest projection strangle (L4) — completing the
quartet (references=L3, temporal+modal=L5). Following the L3 TEMPLATE
(``test_fi_reference_projection``) and L5 (``test_fi_modal_projection``). It proves
the forest can REPRODUCE the converged ``DefinitionLens``'s ``tarkoitetaan``
definiendum-entry subset (0-delta on the characterised subset) and CHARACTERISES
the two asymmetries:

OUTCOME (B): the forest's ``definition_entry`` leaf is sourced from the definition
family (``parse_definition_block``). The lens (``recognize_defined_term_bindings``)
emits THREE binding kinds; the forest owns only the ``tarkoitetaan`` definiendum
entries — the two ALIAS kinds (parenthetical / jäljempänä) are the citation-alias
family, the surfaced residual worklist. On the ``tarkoitetaan`` subset the forest
is 0-delta. The ``def-recall`` post-verb-inline arm
(``tarkoitetaan <X-adessive> Y``) is a recall gain the production binder does NOT
cover, so on those shapes the forest SUPERSETS the lens — the forest-EXTRA keys are
annotation-independent recoveries, not misses.

The differential compares the CANONICAL ``definiendum | scope | target`` identity
(the production binder's own ``definition_key``), robust to surface/byte-coordinate
differences. EU-act targets agree by construction (shared ``_act_id_in_expansion``).
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.definition_projection import (
    DEFINITION_FAMILY_ID,
    FOREST_OWNED_BINDING_KIND,
    FOREST_OWNED_LENS,
    FOREST_UNOWNED_DEFINITION_FAMILIES,
    diff_forest_vs_definition_lens_subset,
    forest_definition_keys,
    lens_definition_subset_keys,
    project_forest_definitions,
)
from lawvm.finland.legal_surface.source_syntax_graph import assemble_source_syntax_graph
from lawvm.finland.references.defined_terms import recognize_defined_term_bindings

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
    return forest_definition_keys(_forest_for(body, statute_id), body)


def _lens_keys(body: str) -> set[str]:
    return lens_definition_subset_keys(
        list(recognize_defined_term_bindings(body, source_file=""))
    )


# ── outcome characterisation ────────────────────────────────────────────────


def test_outcome_is_tarkoitetaan_subset_plus_characterised_alias_residual() -> None:
    """The forest owns the ``tarkoitetaan`` entries; the alias kinds are surfaced.

    Documents the strangle's frontier: the forest reproduces the lens's
    definiendum-entry definition kind, and the citation-alias binding kinds the lens
    also emits are the surfaced residual worklist — never hidden.
    """
    assert FOREST_OWNED_LENS == "fi.definitions.v0"
    assert FOREST_OWNED_BINDING_KIND == "tarkoitetaan"
    assert "parenthetical_alias" in FOREST_UNOWNED_DEFINITION_FAMILIES
    assert "jaljempana" in FOREST_UNOWNED_DEFINITION_FAMILIES


# ── 0-delta on the characterised subset (the flip gate) ─────────────────────


def test_zero_delta_enumerated_block() -> None:
    """An enumerated definitions block: forest projection == lens subset."""
    body = "Tässä laissa tarkoitetaan: sivutuotteella eläimen ruhoa; rehulla ainetta."
    forest_keys = _forest_keys_for(body, "2026/100")
    lens_keys = _lens_keys(body)

    diff = diff_forest_vs_definition_lens_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "sivutuotteella|statute" in forest_keys, sorted(forest_keys)
    assert "rehulla|statute" in forest_keys, sorted(forest_keys)


def test_zero_delta_single_sentence_pre_verb_inline() -> None:
    """A pre-verb inline definition: forest projection == lens subset.

    ``X:llä tarkoitetaan Y`` — the production binder's inline arm and the forest's
    own inline arm agree, so the projection is 0-delta.
    """
    body = "Tässä laissa moottoriajoneuvolla tarkoitetaan konevoimalla kulkevaa ajoneuvoa."
    forest_keys = _forest_keys_for(body, "2026/200")
    lens_keys = _lens_keys(body)

    diff = diff_forest_vs_definition_lens_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "moottoriajoneuvolla|statute" in forest_keys, sorted(forest_keys)


def test_zero_delta_eu_act_bound_definiens_target_shared() -> None:
    """An act-bound definiens: the EU target key agrees by construction.

    ``sivutuotteella tarkoitetaan asetuksessa (EY) N:o 1069/2009 …`` — both the
    forest projection and the lens oracle compute ``target_ref`` via the SAME
    ``_act_id_in_expansion``, so the ``…|<target>`` key matches with 0 delta (no
    extra orientation, unlike the L3 cited-act re-orientation).
    """
    body = (
        "Tässä laissa sivutuotteella tarkoitetaan asetuksessa (EY) N:o 1069/2009 "
        "tarkoitettua tuotetta."
    )
    forest_keys = _forest_keys_for(body, "2026/300")
    lens_keys = _lens_keys(body)

    diff = diff_forest_vs_definition_lens_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    # The bound act target is folded into the key on BOTH sides (identical orientation).
    assert any(k.count("|") >= 2 for k in forest_keys), sorted(forest_keys)


# ── SUPERSET: the def-recall post-verb-inline recall residual ───────────────


def test_superset_postverb_inline_recall_gain() -> None:
    """A post-verb inline definition: forest recovers it, the production binder does not.

    ``tarkoitetaan kemikaalilla Y`` (the colon-less header idiom, the def-recall
    arm) is a recall gain the production binder does NOT cover (its inline arm needs
    a PRE-verb definiendum; its enumerated arm needs ``tarkoitetaan:``). The core is
    therefore a forest-EXTRA vs the lens subset — an annotation-independent recovery,
    NOT a miss.
    """
    body = "Tässä laissa tarkoitetaan kemikaalilla ainetta tai seosta."
    forest_keys = _forest_keys_for(body, "2026/400")
    lens_keys = _lens_keys(body)

    # The production binder emits nothing for this shape …
    assert lens_keys == set(), sorted(lens_keys)
    # … but the forest recovers the definition entry.
    assert "kemikaalilla|statute" in forest_keys, sorted(forest_keys)

    diff = diff_forest_vs_definition_lens_subset(forest_keys, lens_keys)
    # No miss: every (zero) lens binding is among the forest entries.
    assert diff.forest_missing == frozenset(), sorted(diff.forest_missing)
    # The forest-EXTRA is the recall residual.
    assert "kemikaalilla|statute" in diff.forest_extra, sorted(diff.forest_extra)


def test_parenthetical_alias_is_outside_the_owned_subset() -> None:
    """A parenthetical act alias is a citation-alias binding, not a definition entry.

    ``Asetus (EY) N:o 1069/2009 (sivutuoteasetus) …`` mints a
    ``parenthetical_alias`` binding in the lens — the citation-alias family — which
    the forest-owned ``tarkoitetaan`` subset filter drops (the residual worklist).
    """
    body = "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa."
    bindings = list(recognize_defined_term_bindings(body, source_file=""))
    # The binder DOES emit the alias …
    assert any(b.binding_kind == "parenthetical_alias" for b in bindings)
    # … but it is NOT in the forest-owned definition subset.
    assert lens_definition_subset_keys(bindings) == set()


# ── the family-membership gate regression (L5's key lesson) ─────────────────


def test_projection_is_gated_by_definition_family_membership() -> None:
    """The projection emits facts only for segments the definition family gated.

    A modal-only provision with no definition cue carries no definition family
    ownership on any leaf and therefore projects no definition segment.
    """
    body = "Tuomioistuin voi määrätä asiasta tarkemmin."
    forest = _forest_for(body, "2026/500")
    assert not any(
        DEFINITION_FAMILY_ID in n.families for n in forest.syntax_nodes.values()
    )
    assert project_forest_definitions(forest, body) == ()


def test_gate_recovers_multi_family_definition_span() -> None:
    """A definition entry in a MULTI-FAMILY span (minted under another kind) still gates.

    A span owned by definition + condition_exception is minted by the assembler
    with the lexicographically-FIRST family's KIND (``condition_clause`` sorts
    before ``definition``), so a kind-based gate
    (``nodes_of_kind("definition_entry")``) would silently drop it. The
    family-membership gate recovers it: the projection finds the definition entry
    even though some definition-owned leaves carry ``kind != "definition_entry"``.
    This is L5's key lesson, re-confirmed for the definition family.
    """
    body = (
        "Tässä laissa sivutuotteella tarkoitetaan tuotetta, jos se täyttää "
        "3 §:n edellytykset."
    )
    forest = _forest_for(body, "2026/550")
    # The span is owned by the definition family …
    assert any(
        DEFINITION_FAMILY_ID in n.families for n in forest.syntax_nodes.values()
    )
    # … but (being multi-family) MORE definition-owned leaves exist than are minted
    # under the definition_entry kind (the condition_exception-kind leaves).
    entry_kind_leaves = forest.nodes_of_kind("definition_entry")
    family_def_leaves = [
        n for n in forest.syntax_nodes.values() if DEFINITION_FAMILY_ID in n.families
    ]
    assert len(family_def_leaves) > len(entry_kind_leaves)
    # The family-membership gate still recovers the definition entry, 0-delta.
    forest_keys = forest_definition_keys(forest, body)
    assert "sivutuotteella|statute" in forest_keys, sorted(forest_keys)
    lens_keys = _lens_keys(body)
    diff = diff_forest_vs_definition_lens_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )


# ── projection shape sanity ──────────────────────────────────────────────────


def test_projected_definition_anchors_to_enclosing_segment() -> None:
    """Each projected definition segment anchors to a real structural segment node."""
    body = "Tässä laissa moottoriajoneuvolla tarkoitetaan konevoimalla kulkevaa ajoneuvoa."
    forest = _forest_for(body, "2026/600")
    projected = project_forest_definitions(forest, body)
    assert projected, "expected one projected definition segment"
    p = projected[0]
    assert p.segment_node_id in forest.syntax_nodes
    assert "tarkoitetaan" in body[p.char_start : p.char_end]
    assert p.entries


def test_definition_header_opener_is_a_benign_no_op_gate() -> None:
    """A bare definitions-block opener gates but projects ZERO entry keys.

    The ``def-recall`` ``definition_header`` kind (``Tässä laissa tarkoitetaan:``
    with no in-span entries) is a real definition construction (so it is
    definition-family-gated) whose enumerated items live downstream — it must
    project an empty entry set, never a fabricated key and never a silent drop.
    """
    body = "Tässä laissa tarkoitetaan:"
    forest = _forest_for(body, "2026/700")
    # The opener is definition-family-owned …
    assert any(
        DEFINITION_FAMILY_ID in n.families for n in forest.syntax_nodes.values()
    )
    # … and gates a projected segment that carries ZERO entries (the no-op gate).
    assert forest_definition_keys(forest, body) == set()
