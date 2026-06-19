"""Forest → definition projection — L4 of the SourceSyntaxGraph strangle.

The definitions half of the lens→SourceSyntaxGraph strangle, completing the
quartet (references=L3, temporal+modal=L5 already merged). Following the L3
TEMPLATE (:mod:`lawvm.finland.legal_surface.reference_projection`) and the L5
modal TEMPLATE (:mod:`lawvm.finland.legal_surface.modal_projection`): make the
:class:`~lawvm.finland.legal_surface.source_syntax_graph.SourceSyntaxGraph`
forest a PRODUCER of defined-term facts and difference its definition layer
against the converged :class:`~…lenses.definitions.DefinitionLens` (the
authoritative ORACLE):

    forest definition_entry leaves  ──(reparse via the definition family's own
        construction parse)──▶  canonical definition keys  ──(corpus differential
        vs the lens)──▶  0-delta on the characterised subset.

WHICH FOREST FAMILY BACKS THE LENS — AND THE ORACLE SUBSET
==========================================================
The forest's ``definition_entry`` construction leaves come from ONE family — the
**definition family** (:func:`…definition_parse.parse_definition_block`), the
formulaic Finnish definition construction (the enumerated ``Tässä laissa
tarkoitetaan: 1) X:llä Y …`` block and the single-sentence inline ``X:llä
tarkoitetaan Y`` / post-verb ``tarkoitetaan X:llä Y`` shapes). The ``def-recall``
arms added a ``definition_header`` parse KIND that carries ZERO entry keys (the
canonical block OPENER whose enumerated items live in following segments) — it is
a definition construction (so the leaf is family-``definition``-owned) but
projects no definition key, exactly like the L5 ``definition_header`` no-op.

The differential ORACLE is the converged :class:`DefinitionLens`, which wraps the
production binder :func:`…references.defined_terms.recognize_defined_term_bindings`
and mints one ``definition_binding`` node per :class:`DefinedTermBinding`. That
binder emits THREE binding kinds:

  * ``tarkoitetaan`` — the definiendum-entry definitions the FOREST family owns;
  * ``parenthetical_alias`` — ``Asetus … (sivutuoteasetus)`` act-naming aliases;
  * ``jaljempana`` — ``… asetuksessa …, jäljempänä sivutuoteasetus`` aliases.

The two ALIAS kinds are the **citation-alias family**, NOT the definition-entry
island the forest reproduces (mirroring the census's
``_definition_oracle_keys_for_span`` filter to ``BINDING_TARKOITETAAN``). So a
NAIVE forest "definitions" set is NOT the full lens binding set; this projection
characterises the forest's OWNED subset (the ``tarkoitetaan`` definition entries)
and proves 0-delta on THAT subset, surfacing the alias kinds as an explicit
residual worklist (no silent claim) — the same honest-(B) discipline as L3.

A SECOND asymmetry runs the OTHER way: the ``def-recall`` post-verb-inline arm
(``tarkoitetaan <X-adessive> Y``) is a recall gain the production binder does NOT
cover (its inline arm requires a PRE-verb definiendum; its enumerated arm requires
``tarkoitetaan:``). On exactly those shapes the forest is a strict SUPERSET of the
lens — the forest-EXTRA keys are annotation-/recognizer-INDEPENDENT recoveries,
NOT regressions (characterise, do not assume regression). The 0-delta flip gate is
therefore stated on the characterised subset that EXCLUDES the post-verb recall
shapes, exactly as L3 states it on the citation-construction subset.

The shared comparison identity is the production binder's own definition key —
``definiendum-surface | scope | target-act`` (:func:`…definition_parse.definition_key`)
— the SAME identity the production lens keys a ``definition_binding`` on (it is
the binder's ``_canonical_term_id`` surface, plus scope + bound act). The EU-id
orientation interaction (an act-bound definiens now minting ``eu/reg/YYYY/NNNN`` /
``celex:`` rather than bare ``NNNN/YYYY``) is shared BY CONSTRUCTION: both the
forest projection and the lens oracle compute ``target_ref`` via the SAME
``_act_id_in_expansion`` recognizer, so the canonical key already agrees on EU
targets — no extra canonicalisation is needed (unlike L3, where the two lanes
oriented the cited-act id differently).

The projection is surface-only: it reads ONLY the assembled forest's
``definition_entry`` leaves (the SET GATE) and reparses each leaf's enclosing
structural segment via the definition family's OWN construction parse. It
re-implements no grammar, makes no binding/attachment decision, and authorises no
replay.
"""
from __future__ import annotations

from dataclasses import dataclass

from lawvm.finland.legal_surface.definition_parse import (
    DefinitionEntry,
    definition_key,
    parse_definition_block,
)
from lawvm.finland.legal_surface.source_syntax_graph import SourceSyntaxGraph
from lawvm.finland.references.defined_terms import (
    BINDING_TARKOITETAAN,
    DefinedTermBinding,
)

#: The binding kinds :class:`DefinitionLens` emits that the forest's definition
#: family does NOT own — the explicit residual worklist (surfaced, never hidden).
#: They are the **citation-alias family** (act-naming aliases), a disjoint family
#: from the definiendum-entry definitions the forest reproduces; keyed by the
#: ``binding_kind`` the production binder stamps so a consumer can audit exactly
#: which alias lane a residual binding came from.
FOREST_UNOWNED_DEFINITION_FAMILIES: tuple[str, ...] = (
    "parenthetical_alias",  # Asetus … (sivutuoteasetus) — parenthetical act alias
    "jaljempana",  # … asetuksessa …, jäljempänä sivutuoteasetus — jäljempänä alias
)

#: The single ``binding_kind`` the forest-owned definition family reproduces — the
#: subset key the differential filters the lens binding set to.
FOREST_OWNED_BINDING_KIND = BINDING_TARKOITETAAN

#: The phrase identifying the lens the forest projects onto (the differential ORACLE).
FOREST_OWNED_LENS = "fi.definitions.v0"

#: The family id the definition construction leaf carries. The SET GATE keys on
#: FAMILY MEMBERSHIP (``"definition" in leaf.families``), NOT on the leaf KIND: a
#: span owned by several families (e.g. definition + condition_exception) is minted
#: by the assembler under the lexicographically-FIRST family's kind (which sorts
#: BEFORE ``definition`` for ``condition_exception``/``citation``), yet it is STILL
#: a definition-gated span — the definition owner is preserved on ``leaf.families``.
#: Gating on kind (``nodes_of_kind("definition_entry")``) would silently drop every
#: such multi-family definition span; gating on family membership is the faithful
#: gate (the L0 union the lens oracle effectively uses) — L5's key lesson.
DEFINITION_FAMILY_ID = "definition"


def _canonical_entry_key(entry: DefinitionEntry) -> str:
    """Canonical ``definiendum | scope | target`` identity for one forest entry.

    The SAME identity :func:`…definition_parse.definition_key` projects and the
    production binder keys a ``DefinedTermBinding`` on. ``target_ref`` is computed
    by the shared ``_act_id_in_expansion`` on both sides, so EU-act targets agree
    by construction (no extra orientation).
    """
    return definition_key(entry.term, entry.scope, entry.target_ref)


def _canonical_binding_key(binding: DefinedTermBinding) -> str:
    """Canonical ``definiendum | scope | target`` identity for one lens binding.

    The production binder records the definiendum surface, scope, and bound act on
    :class:`DefinedTermBinding`; the lens mints a ``definition_binding`` node from
    each. We key it identically to the forest entry so the two sides share the key
    space.
    """
    return definition_key(binding.term, binding.scope, binding.target_ref)


@dataclass(frozen=True, slots=True)
class ProjectedDefinition:
    """One definition segment PROJECTED from a forest ``definition_entry`` leaf.

    Surface-only and source-anchored: ``[char_start, char_end)`` is the span of the
    structural segment (definition_list block / chapeau / prose) the
    ``definition_entry`` leaf sits in (the leaf is only the GATE that a definition
    construction is present; the full definition — definiendum + cue + definiens —
    lives in the surrounding segment, so the segment is the unit reparsed). Carries
    the reconstructed definition entries so the projection is directly comparable to
    the ``tarkoitetaan`` binding subset the :class:`DefinitionLens` emits.

    Attributes:
        segment_node_id: ``node_id`` of the structural segment reparsed.
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        entries:    The reconstructed definition entries (>=0; a definition_header
                    opener reparses to ZERO entries — a benign no-op gate).
    """

    segment_node_id: str
    char_start: int
    char_end: int
    entries: tuple[DefinitionEntry, ...]


def _enclosing_segment_id(forest: SourceSyntaxGraph, leaf_node_id: str) -> str | None:
    """The structural segment that ``contains`` this construction leaf, or None.

    Reads the assembler's ``contains`` edge from a leaf's enclosing structural
    segment to the leaf (mirrors
    :func:`reference_projection._enclosing_segment_id` /
    :func:`modal_projection._enclosing_segment_id`).
    """
    for edge in forest.edges_of_kind("contains"):
        if edge.dst == leaf_node_id and edge.src in forest.syntax_nodes:
            return edge.src
    return None


def _definition_gated_leaf_ids(forest: SourceSyntaxGraph) -> list[str]:
    """Construction-leaf node ids whose family ownership includes the definition family.

    The SET GATE: every leaf carrying ``"definition"`` among its ``families``
    (including multi-family leaves minted under another family's kind), in span
    order.
    """
    return [
        node.node_id
        for node in sorted(
            (
                n
                for n in forest.syntax_nodes.values()
                if DEFINITION_FAMILY_ID in n.families
            ),
            key=lambda n: (n.char_start, n.char_end),
        )
    ]


def project_forest_definitions(
    forest: SourceSyntaxGraph,
    body: str,
) -> tuple[ProjectedDefinition, ...]:
    """Project the forest's definition-bearing segments to reconstructed entries.

    The forest's ``definition_entry`` leaves are the SET GATE — only structural
    segments the definition family owned a span of project. A leaf is a coalesced
    union sub-span, so the reconstruction reparses the leaf's ENCLOSING structural
    segment via the definition family's OWN construction parse
    (:func:`parse_definition_block`) and lifts each recognised entry. One
    :class:`ProjectedDefinition` per gated segment; a segment that reparses to a
    ``definition_header`` opener (a real definition construction whose enumerated
    items live downstream) projects an empty entry tuple — a benign no-op gate, not
    a drop.

    Deterministic and surface-only: reads ONLY the assembled forest + the body
    text, makes no binding/attachment decision, authorises no replay. Segments are
    emitted in span order. NOTE: the lens oracle binds per STATUTE-BODY span while
    the forest groups by STRUCTURAL segment; ``parse_definition_block`` recovers
    every entry in the span it is handed, so the entry SET is identical even when
    the unit-of-iteration differs.
    """
    gated_segment_ids: list[str] = []
    seen: set[str] = set()
    for leaf_id in _definition_gated_leaf_ids(forest):
        seg_id = _enclosing_segment_id(forest, leaf_id)
        if seg_id is None or seg_id in seen:
            continue
        seen.add(seg_id)
        gated_segment_ids.append(seg_id)

    out: list[ProjectedDefinition] = []
    for seg_id in gated_segment_ids:
        seg = forest.syntax_nodes[seg_id]
        seg_text = body[seg.char_start : seg.char_end]
        dp = parse_definition_block(seg_text)
        out.append(
            ProjectedDefinition(
                segment_node_id=seg_id,
                char_start=seg.char_start,
                char_end=seg.char_end,
                entries=dp.entries,
            )
        )
    out.sort(key=lambda p: (p.char_start, p.char_end))
    return tuple(out)


def forest_definition_keys(
    forest: SourceSyntaxGraph,
    body: str,
) -> set[str]:
    """The canonical definition key SET the forest's definition layer produces.

    The forest's owned-definition projection as a set of canonical
    ``definiendum | scope | target`` keys (:func:`_canonical_entry_key`) — the
    identity both the forest and the lens canonicalise identically. This is the
    LEFT side of both differentials.
    """
    keys: set[str] = set()
    for projected in project_forest_definitions(forest, body):
        for entry in projected.entries:
            keys.add(_canonical_entry_key(entry))
    return keys


def lens_definition_subset_keys(bindings: list[DefinedTermBinding]) -> set[str]:
    """The canonical definition key SET of the lens's FOREST-OWNED ``tarkoitetaan`` subset.

    Filters a :class:`DefinitionLens` binding list (the full
    :func:`recognize_defined_term_bindings` output) to the ``tarkoitetaan``
    definiendum-entry kind the forest owns (:data:`FOREST_OWNED_BINDING_KIND`) and
    keys each by its canonical ``definiendum | scope | target``. This is the RIGHT
    side of the differential — the definition-entry portion of the converged lens,
    the only portion the forest claims to reproduce in this strangle rung. The
    forest-EXTRA keys vs this subset are the post-verb-inline recall residual
    (annotation-independent recoveries, not misses); the alias bindings the filter
    drops are the citation-alias residual worklist
    (:data:`FOREST_UNOWNED_DEFINITION_FAMILIES`).
    """
    return {
        _canonical_binding_key(b)
        for b in bindings
        if b.binding_kind == FOREST_OWNED_BINDING_KIND
    }


@dataclass(frozen=True, slots=True)
class DefinitionDifferential:
    """A forest-projection vs definition-lens-subset canonical-key differential.

    Attributes:
        identical:      keys both the forest projection AND the lens subset produce.
        forest_missing: keys the lens subset has that the forest projection lacks.
        forest_extra:   keys the forest projection has that the lens subset lacks.
    """

    identical: frozenset[str]
    forest_missing: frozenset[str]
    forest_extra: frozenset[str]

    @property
    def is_zero_delta(self) -> bool:
        return not self.forest_missing and not self.forest_extra


def diff_forest_vs_definition_lens_subset(
    forest_keys: set[str], lens_subset_keys: set[str]
) -> DefinitionDifferential:
    """Classify forest-projection vs definition-lens-subset canonical keys.

    IDENTICAL / forest-MISSING / forest-EXTRA. The flip gate is 0-delta on the
    characterised ``tarkoitetaan`` subset that EXCLUDES the post-verb-inline recall
    shapes (``is_zero_delta``); ``forest_extra`` on the broader corpus is the
    recall residual the forest recovers that the production binder does not.
    """
    return DefinitionDifferential(
        identical=frozenset(forest_keys & lens_subset_keys),
        forest_missing=frozenset(lens_subset_keys - forest_keys),
        forest_extra=frozenset(forest_keys - lens_subset_keys),
    )
