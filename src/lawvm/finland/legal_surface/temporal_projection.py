"""Forest → temporal projection — L5 of the SourceSyntaxGraph strangle.

The temporal half of the L5 lens→SourceSyntaxGraph strangle, following the L3
TEMPLATE (:mod:`lawvm.finland.legal_surface.reference_projection`): make the
:class:`~lawvm.finland.legal_surface.source_syntax_graph.SourceSyntaxGraph`
forest a PRODUCER of temporal facts and difference its temporal layer against the
converged H3 :class:`~lawvm.finland.legal_surface.lenses.temporal.TemporalLens`
(the differential ORACLE, wrapping
:func:`lawvm.finland.references.temporal.recognize_temporal_exprs`):

    forest temporal_phrase leaves  ──(reparse via the temporal family's own
        construction parse)──▶  typed projection facts  ──(corpus differential vs
        the lens)──▶  0-delta on the characterised subset.

WHICH FOREST FAMILY BACKS THE LENS
==================================
The forest's ``temporal_phrase`` construction leaves come from ONE family — the
**temporal / applicability family** (:func:`…temporal_parse.parse_temporal_sentence`),
which MIRRORS the production ``meta_parse``/``temporal_lowering`` classifier (the
johtolause commencement-vs-application clause-role vocabulary: commencement /
validity / application / delegation, each with the production-extracted ISO date).

The :class:`TemporalLens` is a DIFFERENT grammar — the H3 inline-prose recognizer
:func:`recognize_temporal_exprs`, whose :class:`TemporalKind` vocabulary is
``FIXED_DATE`` / ``COMMENCEMENT`` / ``DURATION_FROM_COMMENCEMENT`` / ``EVENT_BOUND``
/ ``VALIDITY_OPEN`` / ``FIXED_TERM_EXPIRY``. The two grammars OVERLAP but neither
contains the other:

  * **shared** (the forest-owned subset this rung proves 0-delta on): the
    **dated commencement** and **dated fixed-term expiry / validity** cores —
    ``Tämä laki tulee voimaan 1.1.2027`` (forest ``commencement`` + ISO date;
    lens ``COMMENCEMENT`` co-located with a ``FIXED_DATE`` carrying the same ISO
    date) and ``on voimassa … YYYY saakka`` (forest ``validity`` + ISO date; lens
    ``FIXED_TERM_EXPIRY`` + the same ISO date);
  * **lens-only** (residual worklist — the forest family does not carry these):
    a bare ``FIXED_DATE`` with no temporal-operator cue, the
    ``DURATION_FROM_COMMENCEMENT`` anchor (``… alkaen`` / ``voimaantulosta
    lukien``), the ``EVENT_BOUND`` ``kunnes …`` cue, and the undated
    ``VALIDITY_OPEN`` (``on voimassa toistaiseksi``);
  * **forest-only** (the meta-classifier vocabulary the H3 lens does not model):
    the ``application`` / ``transition`` clause (``Tätä lakia sovelletaan …``)
    and the ``delegation`` clause (``antaa tarkempia säännöksiä``).

So a NAIVE forest "temporal" set is NOT the lens set. This projection therefore
canonicalises BOTH sides onto a shared ``(kind, iso_date)`` temporal identity,
restricts the differential to the **shared canonical kinds** (commencement /
expiry, dated), proves 0-delta on THAT subset, and surfaces the rest as an
explicit residual worklist (no silent drop), exactly as L3 does.

The projection is surface-only: it reads ONLY the assembled forest's
``temporal_phrase`` leaves (the SET GATE) and reparses each leaf's enclosing
structural segment via the temporal family's OWN construction parse. It
re-implements no grammar, makes no activation/expiry decision, and authorises no
replay.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceNodeSeed,
)
from lawvm.finland.legal_surface.source_syntax_graph import (
    SourceSyntaxGraph,
    assemble_source_syntax_graph_for_unit,
)
from lawvm.finland.legal_surface.temporal_parse import (
    ROLE_COMMENCEMENT,
    ROLE_VALIDITY,
    TemporalClause,
    parse_temporal_sentence,
)
from lawvm.finland.references.temporal import (
    TemporalExpr,
    TemporalKind,
    recognize_temporal_exprs,
)

# ---------------------------------------------------------------------------
# Shared canonical temporal identity (the differential's comparison key).
# ---------------------------------------------------------------------------
#: The canonical temporal kinds BOTH grammars carry — the comparison vocabulary
#: the differential is restricted to. A clause/expr is comparable only when it
#: canonicalises to one of these AND carries an ISO date (the load-bearing
#: identity both sides agree on). Anything else is residual (surfaced, never
#: silently compared away).
CANON_COMMENCEMENT = "commencement"
CANON_EXPIRY = "expiry"

#: The forest temporal roles that map onto a shared canonical kind. The forest's
#: ``application`` / ``delegation`` roles have NO H3-lens counterpart and are
#: surfaced as forest-only residual (see module docstring).
_FOREST_ROLE_TO_CANON: dict[str, str] = {
    ROLE_COMMENCEMENT: CANON_COMMENCEMENT,
    ROLE_VALIDITY: CANON_EXPIRY,
}

#: The H3-lens :class:`TemporalKind`s that map onto a shared canonical kind. The
#: lens's ``FIXED_DATE`` (bare date, no operator), ``DURATION_FROM_COMMENCEMENT``,
#: ``EVENT_BOUND`` and undated ``VALIDITY_OPEN`` have NO forest-family counterpart
#: and are surfaced as lens-only residual (the residual worklist).
_LENS_KIND_TO_CANON: dict[TemporalKind, str] = {
    TemporalKind.COMMENCEMENT: CANON_COMMENCEMENT,
    TemporalKind.FIXED_TERM_EXPIRY: CANON_EXPIRY,
}

#: The H3-lens temporal families the forest's ``temporal_phrase`` leaf does NOT
#: (yet) reproduce — the explicit lens-side residual worklist (surfaced, never
#: hidden). Keyed by the :class:`TemporalKind` value.
FOREST_UNOWNED_TEMPORAL_LENS_KINDS: tuple[str, ...] = (
    TemporalKind.FIXED_DATE.value,  # a bare date with no temporal-operator cue
    TemporalKind.DURATION_FROM_COMMENCEMENT.value,  # … alkaen / voimaantulosta lukien
    TemporalKind.EVENT_BOUND.value,  # kunnes <event>
    TemporalKind.VALIDITY_OPEN.value,  # on voimassa (toistaiseksi) — undated
)

#: The forest temporal roles with NO H3-lens counterpart — the explicit
#: forest-side residual worklist (the meta-classifier vocabulary the lens does
#: not model).
FOREST_ONLY_TEMPORAL_ROLES: tuple[str, ...] = (
    "application",  # Tätä lakia sovelletaan … (transition)
    "delegation",  # antaa tarkempia säännöksiä
)


@dataclass(frozen=True, slots=True)
class ProjectedTemporal:
    """One temporal segment PROJECTED from a forest ``temporal_phrase`` leaf.

    Surface-only and source-anchored: ``[char_start, char_end)`` is the span of
    the structural segment the ``temporal_phrase`` leaf sits in (the leaf is only
    the GATE that a temporal cue is present; the full clause — cue + date tail —
    lives in the surrounding segment, so the segment is the unit reparsed).
    Carries the reconstructed temporal clauses so the projection is directly
    comparable to the lens's temporal exprs over the same span.

    Attributes:
        segment_node_id: ``node_id`` of the structural segment reparsed.
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        clauses:    The reconstructed temporal clauses (>=1) the segment carries.
    """

    segment_node_id: str
    char_start: int
    char_end: int
    clauses: tuple[TemporalClause, ...]


def _canonical_temporal_key_from_clause(clause: TemporalClause) -> str | None:
    """Canonical ``(kind, iso_date)`` key for one forest temporal clause, or None.

    Comparable only when the clause's role maps onto a SHARED canonical kind
    (commencement / expiry) AND it carries an ISO date — the load-bearing
    identity both grammars agree on. An application / delegation clause (no shared
    kind) or a dateless commencement/validity clause (no shared identity) returns
    ``None`` — it is forest-only / undated residual, surfaced separately, never
    forced into the comparison.
    """
    canon = _FOREST_ROLE_TO_CANON.get(clause.role)
    if canon is None or not clause.date:
        return None
    return f"{canon}:{clause.date}"


def _canonical_temporal_key_from_expr(expr: TemporalExpr) -> str | None:
    """Canonical ``(kind, iso_date)`` key for one H3-lens temporal expr, or None.

    The H3 lens emits a dateless ``COMMENCEMENT`` cue plus a SEPARATE
    ``FIXED_DATE`` row carrying the date (it never co-locates them on one row).
    For the shared identity we therefore pair a ``COMMENCEMENT`` with the date the
    forest's commencement clause carries via :func:`lens_temporal_subset_keys`
    (which scans the whole span). Here, a single expr is comparable only when it
    is a ``FIXED_TERM_EXPIRY`` (the only lens kind that carries its OWN date on a
    shared canonical kind). Everything else returns ``None`` — handled by the
    span-level pairing or surfaced as residual.
    """
    canon = _LENS_KIND_TO_CANON.get(expr.kind)
    if canon != CANON_EXPIRY or expr.bound is None:
        return None
    return f"{canon}:{expr.bound.isoformat()}"


#: The family id the temporal construction leaf carries. The SET GATE keys on
#: FAMILY MEMBERSHIP (``"temporal" in leaf.families``), NOT on the leaf KIND: a
#: span owned by several families is minted with the lexicographically-first
#: family's kind by the assembler, yet a span carrying the temporal family is
#: STILL a temporal-gated span — the temporal owner is preserved on
#: ``leaf.families``. Gating on family membership is the faithful gate.
TEMPORAL_FAMILY_ID = "temporal"


def _enclosing_segment_id(forest: SourceSyntaxGraph, leaf_node_id: str) -> str | None:
    """The structural segment that ``contains`` this construction leaf, or None.

    Reads the assembler's ``contains`` edge from a leaf's enclosing structural
    segment to the leaf (mirrors
    :func:`reference_projection._enclosing_segment_id`).
    """
    for edge in forest.edges_of_kind("contains"):
        if edge.dst == leaf_node_id and edge.src in forest.syntax_nodes:
            return edge.src
    return None


def _temporal_gated_leaf_ids(forest: SourceSyntaxGraph) -> list[str]:
    """Construction-leaf node ids whose family ownership includes the temporal family.

    The SET GATE: every leaf carrying ``"temporal"`` among its ``families``
    (including multi-family leaves minted under another family's kind), in span
    order.
    """
    return [
        n.node_id
        for n in sorted(
            (
                node
                for node in forest.syntax_nodes.values()
                if TEMPORAL_FAMILY_ID in node.families
            ),
            key=lambda node: (node.char_start, node.char_end),
        )
    ]


def project_forest_temporal(
    forest: SourceSyntaxGraph,
    body: str,
) -> tuple[ProjectedTemporal, ...]:
    """Project the forest's temporal-bearing segments to reconstructed clauses.

    The forest's ``temporal_phrase`` leaves are the SET GATE — only structural
    segments the temporal family owned a span of project. A leaf is a coalesced
    union sub-span, so the reconstruction reparses the leaf's ENCLOSING structural
    segment via the temporal family's OWN construction parse
    (:func:`parse_temporal_sentence`) and lifts each recognised clause. One
    :class:`ProjectedTemporal` per gated segment; a segment that reparses to no
    temporal clause (a spurious coalesced fragment) projects nothing.

    Deterministic and surface-only: reads ONLY the assembled forest + the body
    text, makes no activation/expiry decision, authorises no replay. Segments are
    emitted in span order.
    """
    gated_segment_ids: list[str] = []
    seen: set[str] = set()
    for leaf_id in _temporal_gated_leaf_ids(forest):
        seg_id = _enclosing_segment_id(forest, leaf_id)
        if seg_id is None or seg_id in seen:
            continue
        seen.add(seg_id)
        gated_segment_ids.append(seg_id)

    out: list[ProjectedTemporal] = []
    for seg_id in gated_segment_ids:
        seg = forest.syntax_nodes[seg_id]
        seg_text = body[seg.char_start : seg.char_end]
        tp = parse_temporal_sentence(seg_text)
        if not tp.clauses:
            continue
        out.append(
            ProjectedTemporal(
                segment_node_id=seg_id,
                char_start=seg.char_start,
                char_end=seg.char_end,
                clauses=tp.clauses,
            )
        )
    out.sort(key=lambda p: (p.char_start, p.char_end))
    return tuple(out)


def _gated_temporal_segment_ids(forest: SourceSyntaxGraph) -> list[str]:
    """The distinct structural segments the forest's temporal family gated, in order.

    Mirrors :func:`project_forest_temporal`'s gate: each ``temporal_phrase`` leaf's
    enclosing structural segment, deduplicated, span-ordered. This is the SET GATE
    deciding which segments the shared-slice recognizer is re-run on.
    """
    seg_ids: list[str] = []
    seen: set[str] = set()
    for leaf_id in _temporal_gated_leaf_ids(forest):
        seg_id = _enclosing_segment_id(forest, leaf_id)
        if seg_id is None or seg_id in seen:
            continue
        seen.add(seg_id)
        seg_ids.append(seg_id)
    return seg_ids


def project_forest_temporal_seeds(
    bundle: SourceSurfaceBundle,
) -> list[SurfaceNodeSeed]:
    """Project the SHARED-CANONICAL ``temporal_expr`` node seeds FROM the cached forest.

    THE PARTIAL PRODUCTION STRANGLE-FLIP (doc-6): the shared-canonical temporal
    node slice the production :class:`TemporalLens` emits — the dated fixed-term-
    expiry node (:data:`…lenses.temporal.FOREST_SHARED_TEMPORAL_KINDS`) — now comes
    FROM the cached :class:`SourceSyntaxGraph` forest, not the whole-unit scan. For
    each unit we assemble (or reuse) the cached forest, take its temporal-family-
    owned ``temporal_phrase`` leaves' enclosing segments as the SET GATE, re-run the
    H3 recognizer (:func:`recognize_temporal_exprs`) on each gated segment, keep
    ONLY the shared-canonical kinds, and mint each through the SAME node-minting
    authority the lens uses (:func:`…lenses.temporal.mint_temporal_expr_seed`) at
    the segment's raw_text base offset.

    PARTIAL by design: only the fixed-term-expiry node is flippable at the NODE
    level. The dated-commencement DATE is carried by the recognizer in a
    ``FIXED_DATE`` node, which is also the (lens-only) residual kind for a bare date
    — so the commencement date cannot leave the residual scan without splitting a
    residual kind. Every other temporal kind stays on the whole-unit scan; this
    function emits ONLY the shared slice.

    0-DELTA BY CONSTRUCTION vs the whole-unit scan
    ==============================================
    The forest's temporal ownership is computed from the SAME temporal grammar's
    own construction parse over the SAME structural segmentation the assembler
    uses, so a segment carries forest temporal ownership iff it contains a temporal
    cue. Re-running the H3 recognizer on the gated segment reproduces the
    fixed-term-expiry exprs the whole-unit scan finds at the SAME raw_text-absolute
    spans (the recognizer is span-local and segment boundaries are sentence-aligned,
    so a fixed-term-expiry never straddles a segment boundary). Gate-then-reparse
    therefore reproduces the scan's fixed-term-expiry node set node-identically,
    proven 0-delta corpus-wide by
    :func:`classify_forest_temporal_gate_coverage` (asserted in
    ``tests/test_fi_temporal_projection`` —
    ``test_corpus_forest_gate_reproduces_only_shared_expiry_slice``). Surface-only;
    reads the forest + the body; authorises no replay.
    """
    # Lazy import: the lens imports this module, so a module-top import would
    # cycle. This keeps the seed-minting authority shared without a cycle.
    from lawvm.finland.legal_surface.lenses.temporal import (
        FOREST_SHARED_TEMPORAL_KINDS,
        mint_temporal_expr_seed,
    )

    seeds: list[SurfaceNodeSeed] = []
    for unit in bundle.units:
        forest = assemble_source_syntax_graph_for_unit(
            subject=bundle.subject,
            unit=unit,
        )
        for seg_id in _gated_temporal_segment_ids(forest):
            seg = forest.syntax_nodes[seg_id]
            seg_text = unit.raw_text[seg.char_start : seg.char_end]
            for expr in recognize_temporal_exprs(seg_text):
                if expr.kind in FOREST_SHARED_TEMPORAL_KINDS:
                    seeds.append(
                        mint_temporal_expr_seed(unit, expr, base=seg.char_start)
                    )
    return seeds


class _TemporalSeedFingerprint(NamedTuple):
    """A node-identity fingerprint (kind / span / surface) for one temporal expr.

    The comparison key both gate and golden sides reduce to: same raw_text-absolute
    span + kind + surface ⇒ identical fingerprint (the 0-delta node identity).
    """

    source_unit_id: str
    kind: str
    surface_text: str
    rule_id: str
    char_start: int
    char_end: int


def _gate_temporal_seed_fingerprints(
    bundle: SourceSurfaceBundle,
) -> set[_TemporalSeedFingerprint]:
    """Node fingerprints the forest TEMPORAL GATE reproduces over ALL kinds.

    The forest gate re-runs the H3 recognizer (:func:`recognize_temporal_exprs`)
    on EVERY temporal-family-gated structural segment — NOT filtered to the shared
    slice — and fingerprints each expr at its raw_text-absolute span. This is the
    FULL gate-reachable temporal node set: the population the "flip every temporal
    kind to the forest" hypothesis would route through the forest. It is the LEFT
    side of :func:`classify_forest_temporal_gate_coverage`.
    """
    fps: set[_TemporalSeedFingerprint] = set()
    for unit in bundle.units:
        forest = assemble_source_syntax_graph_for_unit(
            subject=bundle.subject,
            unit=unit,
        )
        for seg_id in _gated_temporal_segment_ids(forest):
            seg = forest.syntax_nodes[seg_id]
            seg_text = unit.raw_text[seg.char_start : seg.char_end]
            for expr in recognize_temporal_exprs(seg_text):
                fps.add(_temporal_seed_fingerprint(unit, expr, base=seg.char_start))
    return fps


def _golden_temporal_seed_fingerprints(
    bundle: SourceSurfaceBundle,
) -> set[_TemporalSeedFingerprint]:
    """Node fingerprints the whole-unit GOLDEN scan produces over ALL kinds.

    The RIGHT side of :func:`classify_forest_temporal_gate_coverage`: the
    independent whole-unit ``recognize_temporal_exprs`` scan (the golden reference
    the production lens preserves), every kind, fingerprinted the same way.
    """
    fps: set[_TemporalSeedFingerprint] = set()
    for unit in bundle.units:
        for expr in recognize_temporal_exprs(unit.raw_text):
            fps.add(_temporal_seed_fingerprint(unit, expr, base=0))
    return fps


def _temporal_seed_fingerprint(
    unit: SourceSurfaceUnit, expr: TemporalExpr, *, base: int
) -> _TemporalSeedFingerprint:
    """A node-identity fingerprint for one expr at its raw_text-absolute span.

    Built from the SAME raw_text-absolute span the production seed carries
    (``base`` is the gated segment's ``char_start`` for the gate side, ``0`` for
    the whole-unit golden scan), so a gate-projected expr and a whole-unit-scanned
    expr that name the SAME temporal fact at the SAME raw_text span fingerprint
    IDENTICALLY (the 0-delta identity). Kept local (no seed minting) so this
    characterisation mirrors :func:`…lenses.temporal.mint_temporal_expr_seed`'s
    span math without importing the lens (avoids the module cycle).
    """
    start = base + expr.source_span.byte_offset
    end = start + expr.source_span.byte_len
    return _TemporalSeedFingerprint(
        source_unit_id=unit.source_unit_id,
        kind=expr.kind.value,
        surface_text=expr.surface_text,
        rule_id=expr.rule_id,
        char_start=start,
        char_end=end,
    )


@dataclass(frozen=True, slots=True)
class TemporalGateCoverage:
    """Per-kind coverage of the whole-unit golden scan BY the forest temporal gate.

    The empirical, committed boundary of WHICH temporal node kinds the forest
    temporal-family gate can reproduce 0-delta (so the boundary is a typed gate,
    not a docstring claim). For each :class:`TemporalKind` value:

    Attributes:
        reproduced: kinds whose golden seeds the gate reproduces EXACTLY (no
            golden seed missed, no extra) — the 0-delta-flippable population.
        missed: ``{kind: count}`` of golden seeds the gate does NOT reach (they
            sit in segments the temporal family does not gate — e.g. a bare
            ``fixed_date`` in a non-temporal segment). Flipping these kinds to the
            gate would SILENTLY DROP these nodes ⇒ NOT 0-delta ⇒ must stay
            lens-produced.
        extra: ``{kind: count}`` of gate seeds with no golden counterpart (the
            gate over-produces). Expected EMPTY: the gate is span-local re-scan of
            a strict subset of the body, so it cannot out-produce the whole-unit
            scan.
    """

    reproduced: frozenset[str]
    missed: dict[str, int]
    extra: dict[str, int]


def classify_forest_temporal_gate_coverage(
    bundle: SourceSurfaceBundle,
) -> TemporalGateCoverage:
    """Classify which temporal kinds the forest gate reproduces 0-delta vs misses.

    The GO/NO-GO characterisation for the "flip the non-shared temporal kinds to
    the forest" hypothesis (standing task #27 Lane T): compares the FULL
    gate-reachable temporal node set (:func:`_gate_temporal_seed_fingerprints`,
    every kind) against the whole-unit golden scan
    (:func:`_golden_temporal_seed_fingerprints`). A kind is ``reproduced`` only
    when the gate reaches EVERY golden seed of that kind with no extra. Any kind
    with a non-zero ``missed`` count is NOT flippable to the gate (the flip would
    silently drop the missed nodes — the forest gate keys on temporal-family
    OWNERSHIP, and the missed kinds occur in segments that carry no temporal cue).
    Surface-only; reads the forest + the body; authorises no replay.
    """
    gate_fps = _gate_temporal_seed_fingerprints(bundle)
    golden_fps = _golden_temporal_seed_fingerprints(bundle)

    missed_counter: Counter[str] = Counter()
    extra_counter: Counter[str] = Counter()
    golden_kinds: set[str] = {fp.kind for fp in golden_fps}
    for fp in golden_fps - gate_fps:
        missed_counter[fp.kind] += 1
    for fp in gate_fps - golden_fps:
        extra_counter[fp.kind] += 1

    reproduced = frozenset(
        k for k in golden_kinds if missed_counter[k] == 0 and extra_counter[k] == 0
    )
    return TemporalGateCoverage(
        reproduced=reproduced,
        missed=dict(missed_counter),
        extra=dict(extra_counter),
    )


def forest_temporal_keys(
    forest: SourceSyntaxGraph,
    body: str,
) -> set[str]:
    """The canonical shared-kind temporal key SET the forest's temporal layer produces.

    The forest's owned temporal projection as a set of canonical ``(kind, iso_date)``
    keys, restricted to the SHARED canonical kinds (commencement / expiry, dated).
    This is the LEFT side of the differential.
    """
    keys: set[str] = set()
    for projected in project_forest_temporal(forest, body):
        for clause in projected.clauses:
            key = _canonical_temporal_key_from_clause(clause)
            if key is not None:
                keys.add(key)
    return keys


def lens_temporal_subset_keys(exprs: list[TemporalExpr]) -> set[str]:
    """The canonical shared-kind temporal key SET of the H3 lens's FOREST-OWNED subset.

    Filters the :class:`TemporalLens` recognizer output to the temporal cores the
    forest owns (the shared canonical kinds) and keys each by the canonical
    ``(kind, iso_date)`` identity:

      * a ``FIXED_TERM_EXPIRY`` carries its own date → keyed directly;
      * a ``COMMENCEMENT`` is DATELESS on the lens side (the date is a separate
        ``FIXED_DATE`` row), so it is paired with the FIXED_DATE date(s) in the
        same span — exactly the ``commencement 1.1.2027`` shape the forest's
        commencement clause keys as ``commencement:<iso>``.

    This is the RIGHT side of the differential — the temporal portion of the H3
    lens the forest claims to reproduce in this strangle rung. The lens-only
    kinds (bare FIXED_DATE / duration / event-bound / undated validity-open) are
    NOT keyed (they are the residual worklist).
    """
    keys: set[str] = set()
    # FIXED_TERM_EXPIRY: self-dated.
    for expr in exprs:
        key = _canonical_temporal_key_from_expr(expr)
        if key is not None:
            keys.add(key)
    # COMMENCEMENT paired with the FIXED_DATE dates in the same span. A
    # COMMENCEMENT cue with >=1 resolved FIXED_DATE in the text is the dated
    # commencement core the forest reproduces; pair each resolved date.
    has_commencement = any(e.kind is TemporalKind.COMMENCEMENT for e in exprs)
    if has_commencement:
        for expr in exprs:
            if expr.kind is TemporalKind.FIXED_DATE and expr.bound is not None:
                keys.add(f"{CANON_COMMENCEMENT}:{expr.bound.isoformat()}")
    return keys


def lens_temporal_keys_for_text(text: str) -> set[str]:
    """Convenience: the lens's forest-owned canonical subset over a raw text span.

    Runs the H3 recognizer (the :class:`TemporalLens`'s underlying recognizer —
    the lens is a thin adapter that maps ``expr.kind.value`` + ``expr.bound``,
    which is exactly the identity :func:`lens_temporal_subset_keys` keys on) and
    returns the shared canonical subset.
    """
    return lens_temporal_subset_keys(recognize_temporal_exprs(text))


@dataclass(frozen=True, slots=True)
class TemporalDifferential:
    """The forest-projection vs H3-lens-subset canonical-key differential.

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


def diff_forest_vs_lens_temporal_subset(
    forest_keys: set[str], lens_subset_keys: set[str]
) -> TemporalDifferential:
    """Classify forest-projection vs H3-lens-subset canonical keys (the flip gate).

    IDENTICAL / forest-MISSING / forest-EXTRA. The gate is 0-delta on the
    characterised shared-kind subset (``is_zero_delta``).
    """
    return TemporalDifferential(
        identical=frozenset(forest_keys & lens_subset_keys),
        forest_missing=frozenset(lens_subset_keys - forest_keys),
        forest_extra=frozenset(forest_keys - lens_subset_keys),
    )
