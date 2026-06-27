"""Typed Finland op-provenance / acceptance-mode model.

This module owns the canonical typed form for *how a compiled op was derived*
and *whether a strict consumer may accept it*. It is the consolidation target
for the scattered provenance/recovery primitives currently spread across
``AmendmentOp`` (the ``*_fallback`` booleans and ``*_provenance_tags`` string
bags) and the stringly ``quirks_disposition``/``strict_disposition`` finding
metadata.

Design intent (see ``notes/FI_OP_PROVENANCE_CONSOLIDATION_SPEC.md``):

- ``OpProvenance`` is a sum type: ``Parsed`` (a grammar rule produced the op) or
  ``Recovered`` (a recognizer/fallback guessed it). Recognizer coverage is
  *intrinsic* to ``Recovered`` — there is no separate "with coverage" shadow.
- A single op may be touched by SEVERAL recovery recognizers at once (e.g. a
  sec1-body fallback whose op is then uncovered-body recovered). Recovery markers
  are therefore COMPOSABLE: ``Recovered`` carries a ``frozenset`` of
  :class:`RecognizerId` members, not one recognizer. ``RecognizerId`` is a CLOSED
  enum — one member per load-bearing recovery recognizer that an apply site
  branches on today: the literal ``*_provenance_tags`` membership tests, the
  boolean ``*_fallback`` / ``*_recovery`` flags, and the branched
  ``witness_rule_id`` values. Each member's ``value`` is the existing literal
  string, so Phase 2 rekeys each apply-site branch onto a membership test against
  this set (``RecognizerId.X in prov.recognizer_ids``), one recognizer per commit,
  with the serialized strings round-trippable.
- ``ConfidenceTier`` is a DISCRETE enum (no floats, no numeric thresholds),
  mirroring the existing ``CiteConfidence`` / ``ScopeResolutionConfidence``
  style: string values + semantic docstrings.
- ``AcceptanceMode`` is keyed on the provenance: ``STRICT`` admits only
  ``Parsed`` ops; ``QUIRKS`` records-with-finding. This makes "silently relying
  on a guess in strict mode" a type-level impossibility for any consumer that
  routes acceptance through :func:`admits` / :func:`mode_for`.

``AcceptanceMode`` is DERIVED FROM the existing :class:`StrictProfile`
(``lawvm.core.compile_result``), never a second toggle: :func:`mode_for` is the
only bridge between the two. ``StrictProfile`` remains the single source of
truth for strict-vs-quirks policy.

Note on scope confidence: ``scope_confidence`` is ORTHOGONAL to recovery — it
rides on ``Parsed`` ops too (a grammar-parsed op can still carry a
context-resolved scope). It is therefore NOT a facet of ``Recovered``; it stays a
separate op-level field (the existing ``AmendmentOp.scope_confidence``), and
``scope_provenance_tags`` retires into that typed field, not into this provenance
sum type.

This module is intentionally dependency-light: it does not import from
``lawvm.finland.ops`` (so it can be wired into ``AmendmentOp`` in a later phase
without an import cycle).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile


class RecognizerId(Enum):
    """CLOSED namespace of load-bearing FI recovery recognizers.

    Exhaustive over every recovery recognizer / provenance tag / witness rule
    that an apply (or compile) site branches on today. Each member's ``value`` is
    the existing literal string used at those sites, so the Phase-2 migration is a
    mechanical rekey of each literal-membership / boolean-flag branch onto a
    membership test against :attr:`Recovered.recognizer_ids`, preserving the
    serialized identity.

    Provenance is composable: an op carries the SET of every recognizer that
    touched it, so co-occurring markers (e.g. ``SEC1_BODY_JOHTO`` together with a
    body fallback) both land in ``recognizer_ids``.
    """

    # --- Boolean recovery flags on AmendmentOp (§1a) ---
    SEC1_BODY_JOHTO = "sec1_body_johto_fallback"
    """``AmendmentOp.sec1_body_johto_fallback``: placeholder -> semantic repeal
    recovery (apply_runtime_support)."""

    BODY_ROOT_REPLACE = "body_root_replace_fallback"
    """``AmendmentOp.body_root_replace_fallback``: whole-section body replace
    fallback (group_ops heading dedup)."""

    UNCOVERED_BODY = "uncovered_body_recovery"
    """``AmendmentOp.uncovered_body_recovery``: chapter-scaffold / uncovered-body
    recovery (apply_structure_ops, merge, apply_subsection_ops)."""

    # --- extraction_provenance_tags literal branches ---
    EXTRACTION_FALLBACK_HEURISTIC = "extraction_fallback_heuristic"
    """Bare body-text extraction heuristic (frontend_compile gating)."""

    JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT = "jolloin_moment_renumber_supplement"
    """`jolloin` moment renumber supplement (payload_normalize gating)."""

    # --- target_guessing_provenance_tags literal branches ---
    UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK = "unique_item_label_subsection_fallback"
    """Item label -> subsection target guess (apply_item_ops, apply_payload_ops)."""

    NORMALIZE_ITEM_LIKE_TARGET = "normalize_item_like_target"
    """Item-like target normalization (payload_normalize)."""

    REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE = "rebase_duplicate_target_shifted_replace"
    """Replace already rebased off a same-wave shifted duplicate target
    (apply_policy, group_ops, apply_runtime_support, payload_normalize)."""

    REBASE_REPLACED_RENUMBER_SOURCE = "rebase_replaced_renumber_source"
    """Replace rebased off a renumber source (apply_policy)."""

    # --- LegalOperation provenance_tags literal branch (scope.py) ---
    CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION = "chapter_scope_from_unique_live_section"
    """Chapter scope inferred from a unique live section (scope.py jolloin
    renumber path)."""

    # --- Branched witness_rule_id values ---
    JOLLOIN_RENUMBER = "fi.jolloin_renumber"
    """`jolloin` renumber witness (scope.py)."""

    REPEAL_VTS_VOIMAANTULO = "fi.repeal_vts_voimaantulo"
    """Voimaantulo repeal VTS witness (apply_subsection_dispatch)."""

    # ===================================================================
    # Step A: FULL serialized tag namespace.
    #
    # The members above are the recovery recognizers an APPLY/COMPILE site
    # branches on. The members below complete the closed namespace so that
    # EVERY tag string ever written into the three serialized provenance bags
    # (``extraction_provenance_tags`` / ``target_guessing_provenance_tags`` /
    # ``scope_provenance_tags``) has a typed home whose ``.value`` is the exact
    # literal serialized string. Membership was established by an EXHAUSTIVE
    # whole-corpus census (59,574 statutes, ``official_consolidation`` replay)
    # of the FINAL compiled-op rows, unioned with the static write-site literals
    # that are written-then-stripped before serialization (so the write site is
    # still typed). The census is the authoritative T-relevance selector: a tag
    # that reaches a serialized golden column is, by construction, load-bearing.
    #
    # These members are NOT (yet) all branched on at apply sites; they exist so
    # the serialized columns can be reconstructed FROM ``provenance`` (Step C)
    # and so adding/removing a tag is a typed change, not a silent string edit.
    # ===================================================================

    # --- extraction_provenance_tags: remaining serialized tags ---
    EXTRACTION_BODY_ROOT_REPLACE = "extraction_body_root_replace"
    """Whole-section body root replace fallback (frontend_compile)."""

    EXTRACTION_ENACTING_FORMULA_BODY_REPLACE = "extraction_enacting_formula_body_replace"
    """Enacting-formula body replace fallback (frontend_compile)."""

    EXTRACTION_ENACTING_FORMULA_BODY_INSERT = "extraction_enacting_formula_body_insert"
    """Enacting-formula body insert fallback (frontend_compile)."""

    EXTRACTION_CEREMONIAL_BODY_ONLY = "extraction_ceremonial_body_only"
    """Ceremonial body-only extraction fallback (frontend_compile)."""

    EXTRACTION_ACT_WIDE_BODY_SECTION_REPLACE = "extraction_act_wide_body_section_replace"
    """Act-wide body section replace fallback (frontend_compile)."""

    EXTRACTION_TITLE_FALLBACK = "extraction_title_fallback"
    """Title-only extraction fallback (frontend_compile)."""

    EXTRACTION_PREAMBLE_BODY = "extraction_preamble_body"
    """Preamble-body extraction fallback (frontend_compile)."""

    REPEAL_REENACT_NORMALIZED = "repeal_reenact_normalized"
    """Repeal+re-enact normalized to a single replace (group_ops)."""

    NUMBERED_TABLE_TARGET = "numbered_table_target"
    """Numbered-table target supplement (johtolause_supplements)."""

    ITEM_AND_MOMENT_TARGET_SUPPLEMENT = "item_and_moment_target_supplement"
    """Item+moment target supplement (johtolause_supplements)."""

    MIXED_EXPLICIT_TARGET_SUPPLEMENT = "mixed_explicit_target_supplement"
    """Mixed-explicit target supplement (johtolause_supplements)."""

    SPARSE_OSALTA_ROW_OMISSION_REPEAL = "sparse_osalta_row_omission_repeal"
    """Sparse `osalta` row-omission repeal supplement (johtolause_supplements)."""

    HISTORICAL_TOP_LEVEL_KOHTA_AS_SUBSECTION = "fi.historical_top_level_kohta_as_subsection"
    """Historical top-level kohta retargeted as subsection (frontend_compile);
    the witness rule id doubles as the extraction-bag tag."""

    # --- target_guessing_provenance_tags: remaining serialized tags ---
    REBASE_SPARSE_STALE_PREDECESSOR = "rebase_sparse_stale_predecessor"
    """Replace rebased off a sparse stale predecessor (payload_normalize)."""

    NUMBERED_TABLE_XML_SUBSECTION_OFFSET = "numbered_table_xml_subsection_offset"
    """Numbered-table XML subsection offset rebind (payload_normalize)."""

    FOLLOW_SAME_WAVE_MIGRATION = "follow_same_wave_migration"
    """Target followed a same-wave migration (apply_subsection_dispatch).
    Written into the target-guessing bag, then stripped before serialization;
    typed here so the write site is covered by the closed namespace."""

    # --- scope_provenance_tags: serialized tags ---
    SCOPE_CARRY_FORWARD = "chapter_scope_carry_forward"
    """Chapter scope carried forward from a prior op (scope resolution)."""

    SCOPE_FROM_EXPLICIT_CHUNK = "chapter_scope_from_explicit_chunk"
    """Chapter scope from an explicit johtolause chunk (johtolause_supplements)."""

    SCOPE_FROM_PREAMBLE = "chapter_scope_from_preamble"
    """Chapter scope from the amendment preamble (scope resolution)."""

    SCOPE_FROM_SAME_AMENDMENT_STEM = "chapter_scope_from_same_amendment_stem"
    """Chapter scope from a same-amendment letter-suffix stem (scope resolution)."""

    SCOPE_GROUPED_CHAPTER = "grouped_chapter_scope"
    """Chapter scope from a grouped-chapter merge (group scope)."""

    SCOPE_GROUPED_PART = "grouped_part_scope"
    """Part scope from a grouped-part merge (group scope)."""

    SCOPE_CHAPTER_SEED = "chapter_seed"
    """Scope tag stamped on a chapter-seed compiled op (replay_pipeline)."""

    SCOPE_MIXED_GROUP_MERGE = "mixed_scope_group_merge"
    """Scope tag stamped on a mixed-scope group merge (group_plan)."""

    SCOPE_IDENTITY_RENUMBER_ABSENT_TARGET_TO_INSERT = (
        "identity_renumber_absent_target_to_insert"
    )
    """Identity-renumber against an absent target lowered to insert
    (frontend_compile); stamped into the scope bag."""

    # --- scope_provenance_tags: explicit-scope-rewrite witnesses ---
    # These mark an explicit chapter-scope rewrite that scope.py stamps onto a
    # ``LegalOperation``'s ``provenance_tags`` (``lo_with_added_scope_tag``). The
    # ``compile_result`` scope-witness legacy fallback and
    # ``ops.scope_confidence_from_tags`` both key on them as EXPLICIT_SCOPE_REWRITE
    # scope tags, so they are part of the SCOPE-bag consumer surface and the codec
    # must carry them through the typed provenance.
    SCOPE_STRIPPED_SUBSECTION_INSERT = "chapter_scope_stripped_subsection_insert"
    """Explicit scope rewrite: chapter scope stripped on a subsection insert."""

    SCOPE_STRIPPED_SECTION_FACET_INSERT = "chapter_scope_stripped_section_facet_insert"
    """Explicit scope rewrite: chapter scope stripped on a section-facet insert."""

    SCOPE_STRIPPED_UNIQUE_SECTION = "chapter_scope_stripped_unique_section"
    """Explicit scope rewrite: chapter scope stripped on a unique section."""

    SCOPE_STRIPPED_DUPLICATE_LABEL_OUTSIDE_STATED_CHAPTER = (
        "chapter_scope_stripped_duplicate_label_outside_stated_chapter"
    )
    """Explicit scope rewrite: chapter scope stripped on a duplicate label outside
    the stated chapter."""


class RecoverySurface(Enum):
    """Which surface a recovery recognizer read to guess the op."""

    BODY = "body"
    """Johtolause body-text recovery (the rank-3 fallback heuristic)."""

    TITLE = "title"
    """Title-only recovery; weakest surface by construction."""

    SCOPE = "scope"
    """Chapter-scope resolution recovery."""

    PAYLOAD = "payload"
    """Sparse-omission / payload elaboration recovery."""


class ConfidenceTier(Enum):
    """Discrete recovery confidence, ordered worst -> best.

    No floats and no numeric thresholds: a tier is assigned by *which
    recognizer* produced the op, never by scoring. This matches the project's
    other confidence enums (``CiteConfidence``, ``ScopeResolutionConfidence``).
    """

    TITLE_ONLY = "title_only"
    """Recovered from the act title alone; the body yielded no ops."""

    HEURISTIC = "heuristic"
    """Bare body-text regex heuristic; no span-coverage witness."""

    COVERAGE_BACKED = "coverage_backed"
    """Body heuristic carrying its intrinsic recognizer span coverage."""

    ANCHORED = "anchored"
    """Context-resolved against live structure (strongest recovery)."""


@dataclass(frozen=True, slots=True)
class RecognitionCoverage:
    """Recognizer span coverage, intrinsic to a ``Recovered`` provenance.

    Folds in the diagnostics that
    ``normalize.parse_ops_fallback_heuristic_with_coverage`` returns separately
    today: which input spans the bounded recognizers covered, and which they
    skipped (still-unowned source text).
    """

    recognized_spans: tuple[tuple[int, int], ...] = ()
    skipped_spans: tuple[tuple[int, int], ...] = ()

    @property
    def is_total(self) -> bool:
        """True when the recognizer left no skipped span unowned."""
        return not self.skipped_spans


@dataclass(frozen=True, slots=True)
class Parsed:
    """The op was produced by a deterministic grammar rule (not a guess)."""

    grammar_rule_id: str


@dataclass(frozen=True, slots=True)
class Recovered:
    """The op was guessed by one or more recovery recognizers / fallbacks.

    ``recognizer_ids`` is the SET of every load-bearing recognizer that touched
    the op (the boolean ``*_fallback`` flags, the load-bearing provenance tag
    strings, and the branched ``witness_rule_id`` values all fold here). Recovery
    markers are independent and composable, so co-occurring recognizers all land
    in the set, and an apply site asks ``RecognizerId.X in op.provenance.recognizer_ids``.

    ``scope_confidence`` is deliberately NOT here: it is orthogonal to recovery
    (it rides on ``Parsed`` ops too) and stays a separate op-level field.

    ``from_fallback_provenance`` is the typed home for the distinct
    ``AmendmentOp.fallback_provenance`` bit. That boolean is a SEPARATE marker
    from any single tag string: a frontend extraction fallback stamps BOTH an
    ``extraction_*`` tag AND ``fallback_provenance=True`` on the same op, and a
    downstream reader (``body_coverage``) keys on the bit alone. It is therefore
    not a ``RecognizerId`` member (it names no serialized bag tag); it rides here
    as an intrinsic facet of the ``Recovered`` provenance.
    """

    surface: RecoverySurface
    recognizer_ids: frozenset[RecognizerId]
    tier: ConfidenceTier
    coverage: RecognitionCoverage = field(default_factory=RecognitionCoverage)
    from_fallback_provenance: bool = False


OpProvenance = Parsed | Recovered
"""Sum type carried (eventually) by every compiled op."""


class AcceptanceMode(Enum):
    """Whether a consumer accepts recovered (guessed) ops."""

    STRICT = "strict"
    """Rejects any ``Recovered`` op; admits only ``Parsed``."""

    QUIRKS = "quirks"
    """Records-with-finding; admits all provenance."""


def has_recognizer(provenance: OpProvenance | None, recognizer: RecognizerId) -> bool:
    """Return whether ``provenance`` carries ``recognizer`` in its set.

    The migration seam: an apply site that today tests a boolean
    ``*_fallback`` flag or a literal ``*_provenance_tags`` membership asks this
    instead. Only :class:`Recovered` provenance can carry a recognizer; ``None``
    and :class:`Parsed` never do.
    """
    return isinstance(provenance, Recovered) and recognizer in provenance.recognizer_ids


# Recognizers that ride on a core ``LegalOperation`` (not an ``AmendmentOp``):
# the branched ``witness_rule_id`` values and the ``provenance_tags`` entries a
# scope-resolution site keys on. Their ``value`` is the literal string used at
# the site, so this map is a closed, exhaustive translation of those literals.
_WITNESS_RULE_RECOGNIZERS: dict[str, RecognizerId] = {
    RecognizerId.JOLLOIN_RENUMBER.value: RecognizerId.JOLLOIN_RENUMBER,
    RecognizerId.REPEAL_VTS_VOIMAANTULO.value: RecognizerId.REPEAL_VTS_VOIMAANTULO,
}
_PROVENANCE_TAG_RECOGNIZERS: dict[str, RecognizerId] = {
    RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION.value: (
        RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION
    ),
}


def provenance_from_witness_and_tags(
    witness_rule_id: str | None,
    provenance_tags: tuple[str, ...],
) -> OpProvenance | None:
    """Derive typed provenance from a core ``LegalOperation``'s raw fields.

    Step 3c (FI-LOCAL): a scope-resolution site that branches on a core
    ``LegalOperation``'s ``witness_rule_id`` or ``provenance_tags`` membership
    routes the read through this helper instead, so the literal-string test
    becomes ``has_recognizer(prov, RecognizerId.X)``. The core
    ``LegalOperation`` carries no ``provenance`` facet (it is a cross-jurisdiction
    type), so the typed set is reconstructed inline from the op's existing fields
    here -- no core change. Exact equivalence: only the recognizers a scope site
    keys on are translated; any other witness/tag yields ``None``.
    """
    ids: set[RecognizerId] = set()
    recognizer = _WITNESS_RULE_RECOGNIZERS.get(witness_rule_id or "")
    if recognizer is not None:
        ids.add(recognizer)
    for tag in provenance_tags:
        tag_recognizer = _PROVENANCE_TAG_RECOGNIZERS.get(tag)
        if tag_recognizer is not None:
            ids.add(tag_recognizer)
    if not ids:
        return None
    recognizer_ids = frozenset(ids)
    return Recovered(
        surface=dominant_surface(recognizer_ids),
        recognizer_ids=recognizer_ids,
        tier=dominant_tier(recognizer_ids),
    )


def admits(mode: AcceptanceMode, provenance: OpProvenance) -> bool:
    """Return whether ``mode`` accepts an op with ``provenance``.

    STRICT admits only :class:`Parsed`. This is the type-level guard: a strict
    consumer that routes acceptance through this function cannot silently
    execute a guessed (:class:`Recovered`) op.
    """
    if mode is AcceptanceMode.QUIRKS:
        return True
    return isinstance(provenance, Parsed)


def mode_for(profile: "StrictProfile | None", provenance: OpProvenance) -> AcceptanceMode:
    """Derive the acceptance mode for ``provenance`` under ``profile``.

    ``StrictProfile`` is the single source of truth. ``None`` means lenient
    (QUIRKS). A non-None profile yields STRICT for the recovery surface(s) it
    forbids and QUIRKS otherwise, keyed per-recovery so the per-family
    ``allows_*`` booleans stay authoritative.

    A :class:`Parsed` op is never recovered, so it is always QUIRKS-equivalent
    (admitted everywhere); the surface gate only matters for :class:`Recovered`.
    """
    if profile is None:
        return AcceptanceMode.QUIRKS
    if isinstance(provenance, Parsed):
        return AcceptanceMode.QUIRKS

    surface = provenance.surface
    if surface is RecoverySurface.BODY or surface is RecoverySurface.TITLE:
        # Body/title recovery is target-guessing in the StrictProfile sense.
        forbidden = not profile.allows_target_guessing
    elif surface is RecoverySurface.SCOPE:
        forbidden = not profile.allows_context_dependent_anchor_resolution
    elif surface is RecoverySurface.PAYLOAD:
        forbidden = not profile.allows_omission_expansion
    else:  # pragma: no cover - exhaustive over RecoverySurface
        raise ValueError(f"unhandled RecoverySurface: {surface!r}")

    return AcceptanceMode.STRICT if forbidden else AcceptanceMode.QUIRKS


# --- Recognizer-id classification (which surface/tier a recognizer implies) ---

# Recognizers grouped by the recovery surface they read. A single op may carry
# several recognizers across surfaces; the dominant surface is picked by the
# fixed precedence below.
_BODY_RECOGNIZERS: frozenset[RecognizerId] = frozenset(
    {
        RecognizerId.SEC1_BODY_JOHTO,
        RecognizerId.BODY_ROOT_REPLACE,
        RecognizerId.UNCOVERED_BODY,
        RecognizerId.EXTRACTION_FALLBACK_HEURISTIC,
    }
)
_PAYLOAD_RECOGNIZERS: frozenset[RecognizerId] = frozenset(
    {
        RecognizerId.JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT,
        RecognizerId.UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK,
        RecognizerId.NORMALIZE_ITEM_LIKE_TARGET,
        RecognizerId.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE,
        RecognizerId.REBASE_REPLACED_RENUMBER_SOURCE,
    }
)
_SCOPE_RECOGNIZERS: frozenset[RecognizerId] = frozenset(
    {
        RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION,
        RecognizerId.JOLLOIN_RENUMBER,
        RecognizerId.REPEAL_VTS_VOIMAANTULO,
    }
)


def dominant_surface(recognizer_ids: frozenset[RecognizerId]) -> RecoverySurface:
    """Pick the recovery surface for a set of co-occurring recognizers.

    Precedence BODY > PAYLOAD > SCOPE: a body-text recovery is the broadest
    guess (it reconstructs the op's existence), so it dominates a narrower
    payload/scope refinement that may ride on the same op. The precedence is a
    presentation choice only — the authoritative recovery markers are the full
    set in ``recognizer_ids``; it does not change which markers are present.
    """
    if recognizer_ids & _BODY_RECOGNIZERS:
        return RecoverySurface.BODY
    if recognizer_ids & _PAYLOAD_RECOGNIZERS:
        return RecoverySurface.PAYLOAD
    return RecoverySurface.SCOPE


def dominant_tier(recognizer_ids: frozenset[RecognizerId]) -> ConfidenceTier:
    """Pick the confidence tier for a set of co-occurring recognizers.

    A scope/witness recognizer is context-resolved against live structure
    (ANCHORED); a body-extraction or payload guess is a bare HEURISTIC. When a
    body/payload recognizer co-occurs with an anchored one, the weaker tier
    governs (the op still rode a heuristic guess).
    """
    if recognizer_ids & (_BODY_RECOGNIZERS | _PAYLOAD_RECOGNIZERS):
        return ConfidenceTier.HEURISTIC
    return ConfidenceTier.ANCHORED


# --- Serialized bag classification (lossless tag<->recognizer codec) ---


class ProvenanceBag(Enum):
    """Which serialized provenance column a recovery tag belongs to.

    The three string bags an ``AmendmentOp`` carries today
    (``extraction_provenance_tags`` / ``target_guessing_provenance_tags`` /
    ``scope_provenance_tags``) are semantically DISTINCT to the serialized
    consumers: ``core.compile_result`` raises ``PARSE.TARGET_GUESSING`` from the
    target-guessing bag and ``PARSE.EXTRACTION_FALLBACK`` from the extraction
    bag, and ``_compile`` keys its target-guessing finding off the
    target-guessing bag specifically. So the bag a tag lives in is load-bearing,
    not free: a flat recognizer set must remember each recognizer's bag to
    reconstruct the columns. The two boolean-flag / witness members that never
    reach a serialized string bag map to :attr:`NONE`.
    """

    EXTRACTION = "extraction_provenance_tags"
    TARGET_GUESSING = "target_guessing_provenance_tags"
    SCOPE = "scope_provenance_tags"
    NONE = ""


# Every ``RecognizerId`` -> the serialized bag its ``.value`` string is written
# into. Verified per write-site against the FI source. ``NONE`` is for the
# recognizers that ride on a boolean ``AmendmentOp`` flag (``SEC1_BODY_JOHTO``,
# ``BODY_ROOT_REPLACE``, ``UNCOVERED_BODY``) or a ``witness_rule_id`` value
# (``JOLLOIN_RENUMBER``, ``REPEAL_VTS_VOIMAANTULO``) rather than a string bag, so
# they have no serialized-bag home and must not synthesize a bag tag.
_RECOGNIZER_BAG: dict[RecognizerId, ProvenanceBag] = {
    # Boolean recovery flags: no serialized string-bag home.
    RecognizerId.SEC1_BODY_JOHTO: ProvenanceBag.NONE,
    RecognizerId.BODY_ROOT_REPLACE: ProvenanceBag.NONE,
    RecognizerId.UNCOVERED_BODY: ProvenanceBag.NONE,
    # Branched witness_rule_id values: carried on witness_rule_id, not a bag.
    RecognizerId.JOLLOIN_RENUMBER: ProvenanceBag.NONE,
    RecognizerId.REPEAL_VTS_VOIMAANTULO: ProvenanceBag.NONE,
    # extraction_provenance_tags.
    RecognizerId.EXTRACTION_FALLBACK_HEURISTIC: ProvenanceBag.EXTRACTION,
    RecognizerId.JOLLOIN_MOMENT_RENUMBER_SUPPLEMENT: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_BODY_ROOT_REPLACE: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_ENACTING_FORMULA_BODY_REPLACE: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_ENACTING_FORMULA_BODY_INSERT: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_CEREMONIAL_BODY_ONLY: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_ACT_WIDE_BODY_SECTION_REPLACE: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_TITLE_FALLBACK: ProvenanceBag.EXTRACTION,
    RecognizerId.EXTRACTION_PREAMBLE_BODY: ProvenanceBag.EXTRACTION,
    RecognizerId.REPEAL_REENACT_NORMALIZED: ProvenanceBag.EXTRACTION,
    RecognizerId.NUMBERED_TABLE_TARGET: ProvenanceBag.EXTRACTION,
    RecognizerId.ITEM_AND_MOMENT_TARGET_SUPPLEMENT: ProvenanceBag.EXTRACTION,
    RecognizerId.MIXED_EXPLICIT_TARGET_SUPPLEMENT: ProvenanceBag.EXTRACTION,
    RecognizerId.SPARSE_OSALTA_ROW_OMISSION_REPEAL: ProvenanceBag.EXTRACTION,
    RecognizerId.HISTORICAL_TOP_LEVEL_KOHTA_AS_SUBSECTION: ProvenanceBag.EXTRACTION,
    # target_guessing_provenance_tags.
    RecognizerId.UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.NORMALIZE_ITEM_LIKE_TARGET: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.REBASE_REPLACED_RENUMBER_SOURCE: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.REBASE_SPARSE_STALE_PREDECESSOR: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.NUMBERED_TABLE_XML_SUBSECTION_OFFSET: ProvenanceBag.TARGET_GUESSING,
    RecognizerId.FOLLOW_SAME_WAVE_MIGRATION: ProvenanceBag.TARGET_GUESSING,
    # scope_provenance_tags (and the core LegalOperation provenance_tags that the
    # scope authority reads as a scope tag).
    RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_CARRY_FORWARD: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_FROM_EXPLICIT_CHUNK: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_FROM_PREAMBLE: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_FROM_SAME_AMENDMENT_STEM: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_GROUPED_CHAPTER: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_GROUPED_PART: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_CHAPTER_SEED: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_MIXED_GROUP_MERGE: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_IDENTITY_RENUMBER_ABSENT_TARGET_TO_INSERT: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_STRIPPED_SUBSECTION_INSERT: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_STRIPPED_SECTION_FACET_INSERT: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_STRIPPED_UNIQUE_SECTION: ProvenanceBag.SCOPE,
    RecognizerId.SCOPE_STRIPPED_DUPLICATE_LABEL_OUTSIDE_STATED_CHAPTER: ProvenanceBag.SCOPE,
}

# Reverse index: literal tag string -> RecognizerId, for every member that has a
# serialized-bag home. Witness/boolean members are excluded (they have no bag).
_TAG_VALUE_TO_RECOGNIZER: dict[str, RecognizerId] = {
    member.value: member
    for member, bag in _RECOGNIZER_BAG.items()
    if bag is not ProvenanceBag.NONE
}


def recognizer_bag(recognizer: RecognizerId) -> ProvenanceBag:
    """Return the serialized provenance bag ``recognizer`` is written into.

    :attr:`ProvenanceBag.NONE` for the boolean-flag / witness recognizers that
    never reach a serialized string bag.
    """
    return _RECOGNIZER_BAG[recognizer]


def recognizer_for_tag(bag: ProvenanceBag, tag: str) -> RecognizerId | None:
    """Return the recognizer a serialized ``tag`` in ``bag`` denotes, or ``None``.

    The inverse of :func:`recognizer_bag` at the string level: a serialized
    reader translates a raw tag string back to its typed recognizer, checking the
    bag matches so a tag is only honored in the column it is written to.
    """
    recognizer = _TAG_VALUE_TO_RECOGNIZER.get(tag)
    if recognizer is None or _RECOGNIZER_BAG[recognizer] is not bag:
        return None
    return recognizer


def bag_tags(
    recognizer_ids: frozenset[RecognizerId], bag: ProvenanceBag
) -> tuple[str, ...]:
    """Return the sorted serialized tag strings ``recognizer_ids`` contributes to ``bag``.

    The serialization codec: reconstructs one bag column's tag list from the
    typed recognizer set. Sorted for a deterministic column ordering (the
    consumers are membership-only — see ``core.compile_result`` /
    ``finland._compile`` — so ordering is a free presentation choice).
    """
    return tuple(
        sorted(
            member.value
            for member in recognizer_ids
            if _RECOGNIZER_BAG[member] is bag
        )
    )


# --- Row-level serialization of the typed provenance (the schema cut) ---
#
# The compiled-op transport row no longer carries the three raw string bags
# (``extraction_provenance_tags`` / ``target_guessing_provenance_tags`` /
# ``scope_provenance_tags``). It carries ONE typed ``provenance`` field instead,
# serialized by :func:`serialize_provenance` and reconstructed by
# :func:`deserialize_provenance`. A reader that still wants a per-bag tag-set view
# of a row reconstructs it with :func:`serialized_bag_tags` (the codec, fed by the
# serialized provenance), so the three columns become DERIVED views of the typed
# field rather than stored data.


def serialize_provenance(provenance: OpProvenance | None) -> dict[str, object] | None:
    """Serialize a typed :class:`OpProvenance` into a JSON-safe transport dict.

    ``None`` (no recovery stamp) serializes to ``None`` so a canonical
    grammar-produced op carries no ``provenance`` field. A :class:`Parsed` op
    serializes its grammar rule id; a :class:`Recovered` op serializes the SET of
    recognizer-id strings plus its surface / tier / coverage / fallback bit.
    """
    if provenance is None:
        return None
    if isinstance(provenance, Parsed):
        return {"kind": "parsed", "grammar_rule_id": provenance.grammar_rule_id}
    return {
        "kind": "recovered",
        "surface": provenance.surface.value,
        "tier": provenance.tier.value,
        "recognizer_ids": sorted(member.value for member in provenance.recognizer_ids),
        "from_fallback_provenance": provenance.from_fallback_provenance,
        "recognized_spans": [list(span) for span in provenance.coverage.recognized_spans],
        "skipped_spans": [list(span) for span in provenance.coverage.skipped_spans],
        # Per-bag tag views, DERIVED from recognizer_ids by the codec at serialize
        # time. A cross-jurisdiction (``core``) consumer that needs the
        # extraction-vs-target-guessing-vs-scope split reads these without
        # importing the FI codec; the typed ``recognizer_ids`` set above stays the
        # single source of truth (these views are reconstructible from it).
        "bags": {
            ProvenanceBag.EXTRACTION.value: list(
                bag_tags(provenance.recognizer_ids, ProvenanceBag.EXTRACTION)
            ),
            ProvenanceBag.TARGET_GUESSING.value: list(
                bag_tags(provenance.recognizer_ids, ProvenanceBag.TARGET_GUESSING)
            ),
            ProvenanceBag.SCOPE.value: list(
                bag_tags(provenance.recognizer_ids, ProvenanceBag.SCOPE)
            ),
        },
    }


def serialized_provenance_from_bags(
    *,
    extraction_tags: tuple[str, ...] = (),
    target_guessing_tags: tuple[str, ...] = (),
    scope_tags: tuple[str, ...] = (),
) -> dict[str, object] | None:
    """Build a serialized ``provenance`` row field from raw per-bag tag lists.

    The bag-thinking entry point: a producer (or test) that has the three raw
    provenance-tag lists rebuilds the canonical serialized provenance field from
    them. Known tags fold into the typed ``recognizer_ids`` (and the derived
    ``bags`` views echo each tag back); the supplied lists are also preserved
    verbatim as the ``bags`` views so a serialized consumer that keys on a tag
    string outside the closed namespace (e.g. the ``compile_result``
    extraction-fallback filter's dead supplement tags) still sees it. Returns
    ``None`` when all bags are empty (no recovery stamp).
    """
    if not (extraction_tags or target_guessing_tags or scope_tags):
        return None
    ids: set[RecognizerId] = set()
    for bag, tags in (
        (ProvenanceBag.EXTRACTION, extraction_tags),
        (ProvenanceBag.TARGET_GUESSING, target_guessing_tags),
        (ProvenanceBag.SCOPE, scope_tags),
    ):
        for tag in tags:
            recognizer = recognizer_for_tag(bag, tag)
            if recognizer is not None:
                ids.add(recognizer)
    recognizer_ids = frozenset(ids)
    serialized = serialize_provenance(
        Recovered(
            surface=dominant_surface(recognizer_ids),
            recognizer_ids=recognizer_ids,
            tier=dominant_tier(recognizer_ids),
        )
    )
    assert serialized is not None  # recognizer_ids may be empty but bags are not
    # Preserve the supplied tag lists verbatim (covers out-of-namespace tags a
    # serialized consumer's own filter still keys on).
    serialized["bags"] = {
        ProvenanceBag.EXTRACTION.value: list(extraction_tags),
        ProvenanceBag.TARGET_GUESSING.value: list(target_guessing_tags),
        ProvenanceBag.SCOPE.value: list(scope_tags),
    }
    return serialized


def serialized_provenance_bag(serialized: object, bag: ProvenanceBag) -> tuple[str, ...]:
    """Return one bag's tag list from a serialized provenance dict.

    Prefers the pre-derived ``bags`` view embedded at serialize time; falls back
    to reconstructing from ``recognizer_ids`` via the codec. Either way the result
    equals ``bag_tags(provenance.recognizer_ids, bag)``.
    """
    if isinstance(serialized, Mapping):
        bags = cast(Mapping[str, Any], serialized).get("bags")
        if isinstance(bags, Mapping):
            view = cast(Mapping[str, Any], bags).get(bag.value)
            if isinstance(view, (list, tuple)):
                return tuple(
                    str(tag).strip()
                    for tag in cast("list[Any] | tuple[Any, ...]", view)
                    if str(tag).strip()
                )
    return serialized_bag_tags(serialized, bag)


def _coerce_spans(value: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    spans: list[tuple[int, int]] = []
    for entry in cast("list[Any] | tuple[Any, ...]", value):
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            pair = cast("list[Any] | tuple[Any, ...]", entry)
            spans.append((int(pair[0]), int(pair[1])))
    return tuple(spans)


def deserialize_provenance(data: object) -> OpProvenance | None:
    """Reconstruct a typed :class:`OpProvenance` from a serialized transport dict.

    Inverse of :func:`serialize_provenance`. ``None`` / a malformed value yields
    ``None`` (no recovery stamp). Unknown recognizer strings are dropped (they are
    outside the closed namespace by construction); the codec stays total over the
    serialized namespace.
    """
    if not isinstance(data, Mapping):
        return None
    mapping = cast(Mapping[str, Any], data)
    kind = mapping.get("kind")
    if kind == "parsed":
        rule_id = mapping.get("grammar_rule_id")
        if not isinstance(rule_id, str):
            return None
        return Parsed(grammar_rule_id=rule_id)
    if kind != "recovered":
        return None
    raw_ids = mapping.get("recognizer_ids")
    ids: set[RecognizerId] = set()
    if isinstance(raw_ids, (list, tuple)):
        for value in raw_ids:
            try:
                ids.add(RecognizerId(str(value)))
            except ValueError:
                continue
    recognizer_ids = frozenset(ids)
    surface_value = mapping.get("surface")
    tier_value = mapping.get("tier")
    try:
        surface = RecoverySurface(str(surface_value))
    except ValueError:
        surface = dominant_surface(recognizer_ids)
    try:
        tier = ConfidenceTier(str(tier_value))
    except ValueError:
        tier = dominant_tier(recognizer_ids)
    coverage = RecognitionCoverage(
        recognized_spans=_coerce_spans(mapping.get("recognized_spans")),
        skipped_spans=_coerce_spans(mapping.get("skipped_spans")),
    )
    return Recovered(
        surface=surface,
        recognizer_ids=recognizer_ids,
        tier=tier,
        coverage=coverage,
        from_fallback_provenance=bool(mapping.get("from_fallback_provenance")),
    )


def serialized_bag_tags(serialized: object, bag: ProvenanceBag) -> tuple[str, ...]:
    """Reconstruct one serialized bag column's tag list from a row's provenance field.

    The derived-view codec for serialized consumers: a reader that today did
    ``row.get("extraction_provenance_tags")`` asks
    ``serialized_bag_tags(row.get("provenance"), ProvenanceBag.EXTRACTION)``
    instead. Only :class:`Recovered` provenance contributes bag tags; ``None`` /
    :class:`Parsed` yield an empty tuple.
    """
    provenance = deserialize_provenance(serialized)
    if not isinstance(provenance, Recovered):
        return ()
    return bag_tags(provenance.recognizer_ids, bag)


# Deterministic display precedence over the SCOPE-bag recognizers, strongest /
# most-explicit scope source first. Used to pick ONE representative scope tag for
# a witness DISPLAY field when the typed scope carrier left its ``tag`` empty, so
# the chosen tag does not depend on any (now order-free) tag-bag ordering. The
# order mirrors ``scope_confidence_from_tags``' precedence (explicit chunk >
# carry-forward > preamble > same-amendment stem > grouped part > grouped chapter)
# and then covers the remaining scope tags; it is presentation-only and changes no
# replay behaviour.
_SCOPE_TAG_DISPLAY_PRECEDENCE: tuple[RecognizerId, ...] = (
    RecognizerId.SCOPE_FROM_EXPLICIT_CHUNK,
    RecognizerId.SCOPE_CARRY_FORWARD,
    RecognizerId.SCOPE_FROM_PREAMBLE,
    RecognizerId.SCOPE_FROM_SAME_AMENDMENT_STEM,
    RecognizerId.SCOPE_GROUPED_PART,
    RecognizerId.SCOPE_GROUPED_CHAPTER,
    RecognizerId.SCOPE_MIXED_GROUP_MERGE,
    RecognizerId.CHAPTER_SCOPE_FROM_UNIQUE_LIVE_SECTION,
    RecognizerId.SCOPE_CHAPTER_SEED,
    RecognizerId.SCOPE_IDENTITY_RENUMBER_ABSENT_TARGET_TO_INSERT,
)


def representative_scope_tag(provenance: OpProvenance | None) -> str | None:
    """Return one deterministic scope tag for a display witness, or ``None``.

    Picks the highest-precedence SCOPE-bag recognizer present in ``provenance``
    by the fixed :data:`_SCOPE_TAG_DISPLAY_PRECEDENCE`, so a witness DISPLAY tag
    derived from the typed set is stable and free of any tag-bag ordering. This
    is the order-free replacement for the positional ``scope_provenance_tags[0]``
    read: the ``obs_kind`` / semantics are decided elsewhere (off the typed scope
    ``source``), so this only governs the cosmetic ``detail["tag"]`` value.
    """
    if not isinstance(provenance, Recovered):
        return None
    for recognizer in _SCOPE_TAG_DISPLAY_PRECEDENCE:
        if recognizer in provenance.recognizer_ids:
            return recognizer.value
    return None


__all__ = [
    "AcceptanceMode",
    "ConfidenceTier",
    "OpProvenance",
    "Parsed",
    "ProvenanceBag",
    "RecognitionCoverage",
    "RecognizerId",
    "Recovered",
    "RecoverySurface",
    "admits",
    "bag_tags",
    "deserialize_provenance",
    "dominant_surface",
    "dominant_tier",
    "has_recognizer",
    "mode_for",
    "provenance_from_witness_and_tags",
    "recognizer_bag",
    "recognizer_for_tag",
    "representative_scope_tag",
    "serialize_provenance",
    "serialized_bag_tags",
    "serialized_provenance_bag",
    "serialized_provenance_from_bags",
]
