"""Forest → reference projection — L3 of the SourceSyntaxGraph strangle.

The FIRST lens→SourceSyntaxGraph projection. Makes the
:class:`~lawvm.finland.legal_surface.source_syntax_graph.SourceSyntaxGraph`
forest a PRODUCER of reference facts, instead of an independent scan, so the
forest's reference layer can be differenced against the converged
:class:`~lawvm.finland.legal_surface.lenses.references.ReferenceLens` (the
authoritative ORACLE). This is the TEMPLATE the L4/L5 lens projections follow:

    forest leaves  ──(reconstruct via the family's own construction parse)──▶
        typed projection facts  ──(corpus differential vs the lens)──▶  0-delta.

WHAT THE FOREST OWNS (the strangle's current frontier)
======================================================
The forest's ``reference_np`` construction leaves come from ONE family — the
**citation family** (:func:`…sentence_parse.parse_citation_sentence`), the
inline-``(NUMBER/YEAR)`` plain-text statute-citation construction (the
``citation_construction`` lane of :func:`extract_all_reference_mentions`,
including the Finding-B head-separated-from-paren class and ``-kaari`` heads).

This is a STRICT SUBSET of the full :class:`ReferenceLens`, which combines SEVEN
lanes over the statute's ``xml_bytes``:

  1. domestic inline ``<ref>`` elements + ``finlex:`` metadata edges,
  2. ``<affectedDocument>`` AMENDS targets (in the preamble, OUTSIDE ``<body>``),
  3. EU citations (text scan),
  4. **inline-(id) plain-text citation construction**  ← the ONLY forest-owned lane,
  5. surface-grammar (treaty / vague-OPEN / EU-nickname directive articles),
  6. the preparatory chain (committee mietintö, EV/EVK, LA, EU prep act, OJ),
  7. delegation-authority (``… nojalla``) bases.

Lanes 1, 2, 6 are sourced from editorial MARKUP / preamble / footer — structures
the body-text forest does not see — and lanes 3, 5, 7 are disjoint body-prose
families the forest's reference_np leaf does not (yet) carry. So a NAIVE forest
"references" set is NOT the lens set; this projection therefore characterises the
forest's OWNED subset (the citation construction) and proves 0-delta on THAT
subset, surfacing the rest as an explicit residual worklist (no silent drop).

The projection is surface-only: it reads ONLY the already-assembled forest's
``reference_np`` leaves (the SET GATE: only spans the citation family owned
project) and reconstructs each via the family's OWN construction parse over the
leaf's verbatim body substring. It re-implements no grammar, makes no attachment
decision, and authorises no replay.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from lawvm.core.reference_mention import ReferenceMention, SourceSpan
from lawvm.finland.references.shared_reference_orchestrator import (
    CITATION_CONSTRUCTION_PHRASE_LEMMA,
    lift_inline_id_construction_mentions,
)
from lawvm.finland.legal_surface.source_syntax_graph import SourceSyntaxGraph

#: The reference families :class:`ReferenceLens` owns that the forest's
#: ``reference_np`` leaf does NOT (yet) produce — the explicit residual worklist
#: (surfaced, never hidden). Keyed by the ``phrase_lemma`` / ``edge_subtype`` the
#: lens stamps each lane's mentions with, so a consumer can audit exactly which
#: lane a residual mention came from.
FOREST_UNOWNED_REFERENCE_FAMILIES: tuple[str, ...] = (
    "ref_element",  # lane 1: inline <ref> elements + finlex: metadata edges
    "affected_document",  # lane 2: <affectedDocument> AMENDS targets (preamble)
    "eu_text_pattern",  # lane 3: EU citations from text scan
    # lane 4 residue: legacy regex fallback the construction missed. RETIRED — the
    # production residue lane was deleted after a whole-corpus census proved it
    # emitted only mis-pivoted duplicate citations (see
    # tests/test_fi_ref_legacy_regex_residue_census.py). Production no longer emits
    # this lemma; the family is kept here for backward-tolerance of historical rows.
    "plain_text_fallback",
    # lane 5: surface-grammar (treaty / vague / EU-nickname) phrase lemmas
    "treaty",
    "treaty_article",
    "vague",
    "eu_directive",
    # lane 6: the preparatory chain (HE excluded — it rides the <ref> lane)
    "preparatory",
    # lane 7: delegation-authority (… nojalla) bases
    "ISSUED_UNDER",
)

#: The single ``phrase_lemma`` the forest-owned citation construction lane stamps
#: on its mentions — the subset key the differential filters the lens to. Sourced
#: from the SHARED lifter so the forest projection and the lens citation lane agree
#: on the lane key by construction (ONE lifter, no rival parser).
FOREST_OWNED_PHRASE_LEMMA = CITATION_CONSTRUCTION_PHRASE_LEMMA


@dataclass(frozen=True, slots=True)
class ProjectedReference:
    """One reference fact PROJECTED from a forest segment that carries a citation.

    Surface-only and source-anchored: ``[char_start, char_end)`` is the span of the
    structural segment (prose / chapeau / list_item) the ``reference_np`` leaf sits
    in — the citation construction owns a coalesced SUB-span of it, but the full
    provision tail (``5 a §:ssä``) lives in the surrounding segment, so the segment
    is the unit reparsed (the leaf is only the GATE that a citation is present).
    Carries the reconstructed :class:`ReferenceMention`s (one per expanded provision
    target) so the projection is directly comparable to the citation-construction
    subset the :class:`ReferenceLens` emits.

    Attributes:
        segment_node_id: ``node_id`` of the structural segment reparsed.
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        mentions:   The reconstructed reference mentions (>=1; a statute-level
                    citation yields one STATUTE_ONLY mention, a provision-precise
                    one yields one EXACT mention per target).
    """

    segment_node_id: str
    char_start: int
    char_end: int
    mentions: tuple[ReferenceMention, ...]


#: Chapter token in an AKN provision path (``chp_9__sec_9b`` → ``9``). Used to fold
#: a chapter distinction into the canonical key WITHOUT keying on the whole
#: provision_path (which the two lanes populate inconsistently — see
#: :func:`_canonical_target_key`).
_CHP_PATH_RE = re.compile(r"chp_([0-9]+[a-z]?)", re.IGNORECASE)


def _canonical_target_key(mention: ReferenceMention) -> str | None:
    """Canonical statute+provision identity key for one citation TARGET.

    The differential compares the forest projection and the lens citation subset on
    the CITATION'S IDENTITY, not on representational fields the two lanes fill
    differently. ``provision_path`` is such a field — the forest projection fills
    ``sec_N`` (from :func:`…sentence_parse._target_to_provision_ref`) while the lens
    ``citation_construction`` lane leaves it empty for non-chapter targets — so it
    is NOT keyed directly. The actual provision identity lives in ``section_label``
    / ``subsection_num`` / ``item_label`` (identical on both sides); the only
    path-only distinction that matters is the CHAPTER, which both lanes encode as
    ``chp_N`` and which is folded in via :data:`_CHP_PATH_RE` so a chapter-qualified
    cite is not collapsed onto a bare one. The statute id is expected already in the
    canonical YEAR/NUMBER orientation (the forest projection re-orients it at
    projection time; the lens lane emits it canonical). Returns ``None`` for a
    target with no statute id (nothing to dedup on).
    """
    ref = mention.target_provision_ref
    if ref is None or not ref.statute_id:
        return None
    chp_match = _CHP_PATH_RE.search(ref.provision_path or "")
    chapter = chp_match.group(1) if chp_match else ""
    return "/".join(
        part
        for part in (
            ref.statute_id,
            chapter,
            ref.section_label or "",
            str(ref.subsection_num) if ref.subsection_num is not None else "",
            ref.item_label or "",
        )
    )


def _segment_char_span_locator(
    seg_text: str, source_statute_id: str
) -> "Callable[[str], SourceSpan | None]":
    """A char-offset surface locator into one segment's text (forest coordinate).

    Mirrors the lens lane's byte-offset-into-``xml_bytes`` locator: a left-to-right
    per-surface cursor maps repeated identical surfaces to successive char positions
    within the segment, so several targets sharing one anchor surface keep the same
    span and a repeated surface advances. Returns ``None`` when the surface is not
    found (fail-loud by absence; never a fabricated offset).
    """
    surface_char_cursor: dict[str, int] = {}

    def locate(surface: str) -> SourceSpan | None:
        if not surface:
            return None
        start = seg_text.find(surface, surface_char_cursor.get(surface, 0))
        if start < 0:
            return None
        surface_char_cursor[surface] = start + 1
        return SourceSpan(
            source_file=source_statute_id,
            byte_offset=start,
            byte_len=len(surface),
        )

    return locate


def _enclosing_segment_id(forest: SourceSyntaxGraph, leaf_node_id: str) -> str | None:
    """The structural segment that ``contains`` this construction leaf, or None.

    The assembler emits a ``contains`` edge from a leaf's enclosing structural
    segment to the leaf (when the leaf sits inside one; a boundary-straddling leaf
    is left unparented). We read that edge to recover the segment whose full text
    must be reparsed to recover the citation's provision tail.
    """
    for edge in forest.edges_of_kind("contains"):
        if edge.dst == leaf_node_id and edge.src in forest.syntax_nodes:
            return edge.src
    return None


def project_forest_references(
    forest: SourceSyntaxGraph,
    body: str,
    *,
    source_statute_id: str,
) -> tuple[ProjectedReference, ...]:
    """Project the forest's citation-bearing segments to reconstructed reference facts.

    The forest's ``reference_np`` leaves are the SET GATE — only structural segments
    the citation family owned a span of project. But a leaf is a COALESCED union
    sub-span (the citation family's chars merged with adjacent families'), so the
    leaf substring alone loses the citation's provision tail. The reconstruction
    therefore reparses the leaf's ENCLOSING structural segment (the prose / chapeau
    / list_item the ``contains`` edge points from) and lifts each recognised
    citation through the SHARED inline-(id) citation lifter
    (:func:`…shared_reference_orchestrator.lift_inline_id_construction_mentions`) —
    the SAME lifter the production :class:`ReferenceLens` ``citation_construction``
    lane uses (ONE lifter, no rival parser). One :class:`ProjectedReference` per
    gated segment; a segment that reparses to no citation (a leaf that was a
    spurious coalesced fragment, e.g. part of a verb) projects nothing.

    The shared lifter re-orients each cited-act id NUMBER/YEAR → canonical
    YEAR/NUMBER, extends the surface to the statute-name head, builds the
    chapter-qualified provision path, dedups per provision target, and skips
    self-cites — identically to the lens lane, so the projection dedups onto the
    same entity identity by construction. The cite's char span is located in the
    segment text (the forest coordinate); the citing statute id bounds the
    2-digit-year pivot and the self-cite skip.

    Deterministic and surface-only: reads ONLY the assembled forest + the body
    text, makes no attachment decision, authorises no replay. Segments are emitted
    in span order.
    """
    # The gated segments: each structural segment that contains >=1 reference_np
    # leaf. De-duplicated so a segment with several citation leaves is reparsed
    # once (its construction parse already recovers every citation in it).
    gated_segment_ids: list[str] = []
    seen: set[str] = set()
    for leaf in forest.nodes_of_kind("reference_np"):
        seg_id = _enclosing_segment_id(forest, leaf.node_id)
        if seg_id is None or seg_id in seen:
            continue
        seen.add(seg_id)
        gated_segment_ids.append(seg_id)

    out: list[ProjectedReference] = []
    for seg_id in gated_segment_ids:
        seg = forest.syntax_nodes[seg_id]
        seg_text = body[seg.char_start : seg.char_end]

        # Char-offset locator into the segment text (the forest coordinate system),
        # mirroring the lens lane's byte-offset-into-xml_bytes locator. A
        # left-to-right cursor maps repeated identical surfaces to successive char
        # positions within the segment.
        mentions_list, _keys = lift_inline_id_construction_mentions(
            seg_text,
            source_statute_id,
            span_for_surface=_segment_char_span_locator(seg_text, source_statute_id),
        )
        if not mentions_list:
            continue
        out.append(
            ProjectedReference(
                segment_node_id=seg_id,
                char_start=seg.char_start,
                char_end=seg.char_end,
                mentions=tuple(mentions_list),
            )
        )
    out.sort(key=lambda p: (p.char_start, p.char_end))
    return tuple(out)


def forest_reference_target_keys(
    forest: SourceSyntaxGraph,
    body: str,
    *,
    source_statute_id: str,
) -> set[str]:
    """The canonical target-provision key SET the forest's reference layer produces.

    The forest's owned-citation projection as a set of canonical statute+provision
    keys (:func:`_canonical_target_key`) — the structural fact both the forest and
    the lens canonicalise identically, robust to surface/byte-coordinate
    differences. This is the LEFT side of the differential.
    """
    keys: set[str] = set()
    for projected in project_forest_references(
        forest, body, source_statute_id=source_statute_id
    ):
        for mention in projected.mentions:
            key = _canonical_target_key(mention)
            if key is not None:
                keys.add(key)
    return keys


def lens_citation_subset_target_keys(mentions: list[ReferenceMention]) -> set[str]:
    """The canonical target-key SET of the lens's FOREST-OWNED citation subset.

    Filters a :class:`ReferenceLens` mention list (the full
    :func:`extract_all_reference_mentions` output) to the citation-construction
    lane the forest owns (``phrase_lemma == "citation_construction"``) and keys each
    by its canonical statute+provision target. This is the RIGHT side of the
    differential — the citation portion of the converged lens, the only portion
    the forest claims to reproduce in this strangle rung.
    """
    keys: set[str] = set()
    for mention in mentions:
        if mention.phrase_lemma != FOREST_OWNED_PHRASE_LEMMA:
            continue
        key = _canonical_target_key(mention)
        if key is not None:
            keys.add(key)
    return keys


@dataclass(frozen=True, slots=True)
class ReferenceDifferential:
    """The forest-projection vs lens-citation-subset target-key differential.

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


def diff_forest_vs_lens_citation_subset(
    forest_keys: set[str], lens_subset_keys: set[str]
) -> ReferenceDifferential:
    """Classify forest-projection vs lens-citation-subset keys (the flip gate).

    IDENTICAL / forest-MISSING / forest-EXTRA. The flip gate is 0-delta on the
    characterised citation subset (``is_zero_delta``).
    """
    return ReferenceDifferential(
        identical=frozenset(forest_keys & lens_subset_keys),
        forest_missing=frozenset(lens_subset_keys - forest_keys),
        forest_extra=frozenset(forest_keys - lens_subset_keys),
    )
