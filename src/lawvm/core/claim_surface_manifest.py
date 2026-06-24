"""``lawvm.claim_surface_manifest.v0`` — the declared surface of PUBLIC claims.

WHAT THIS ENABLES — the compounding move. LawVM makes a handful of public,
externally-consumable claims: a bench agreement score over the corpus, a
"this provision text is governing at PIT D" materialization, a reference
classification, a source-monotonicity verdict. Historically the boundary on
each claim ("what we do NOT promise") is a per-capability, hand-written
paragraph in a STATUS doc. This module turns the SET of public claims into a
single frozen, root-committed artifact — a :class:`ClaimSurfaceManifest` — so a
machine can ask the compounding question once over ALL claims:

    *Does every declared public claim have at least one live accounting path
    (an :mod:`lawvm.core.invariant_spec` row terminating in an ALLOWED bucket),
    and is no invariant left in the forbidden ``implicit_convention`` bucket?*

That is "no public claim without a live accounting path" (Pro doc §13 step 1,
the terminal invariant of the total-invariant-mining discipline) made into an
enumerable, generated coverage gate instead of N hand-written boundaries.

THE HONESTY BOUNDARY — completeness is CLAIM-RELATIVE and VERSIONED (Pro §12).
v0 enumerates a DECLARED SUBSET of LawVM's public claims, NOT all of them, and
NOT all invariants. The manifest carries a ``claim_surface_version``; the
companion invariant set carries an ``InvariantSet(spec_version,
claim_surface_version)`` pair. Expanding the claim surface regenerates the
invariant set. There is NO assertion of absolute completeness anywhere here —
"all" is always "all required invariants for THIS declared claim surface at
THIS version". Three things v0 explicitly does NOT do:

* It does **not** auto-generate invariants from the finite-axis generator
  (planes × waists × object-kinds × ...). It BINDS to hand/registry-authored
  :class:`~lawvm.core.invariant_spec.InvariantSpec` rows.
* It does **not** run fire-drills (Pro §13 step 4 — driving each hard-fail guard
  from production) — those remain future work.
* It does **not** run the MUST-trace linter (Pro §13 step 5 — mapping every
  spec ``MUST`` to an invariant) — also future.

The manifest is a DECLARATION plane object: like
:mod:`lawvm.core.assumption_register`, it is computed ABOUT the semantic objects
and never enters any semantic object's hash. Its only hash is
:attr:`ClaimSurfaceManifest.manifest_root` (a ``MapRoot`` over claim bodies).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, map_root

_SCHEMA_CLAIM_SPEC = "lawvm.claim_spec.v0"
_SCHEMA_CLAIM_SURFACE_MANIFEST = "lawvm.claim_surface_manifest.v0"
_DOMAIN_CLAIM_SPEC = "claim_spec"
_DOMAIN_CLAIM_SURFACE_MANIFEST_ROOT = "claim_surface_manifest"

# The declared claim-surface version. Bumping this regenerates the required
# invariant set (Pro §12: completeness is relative to a DECLARED claim surface).
CLAIM_SURFACE_VERSION = "v0"

# Checker-level vocabulary (Pro §13 step 1 ``checker_level``). L0 = byte/storage
# decode; L1 = structural/row re-derivation over a pack; L2 = source->op
# re-derivation. The level a claim is checkable AT — NOT a quality grade.
CheckerLevel = Literal["L0", "L1", "L2"]
CHECKER_LEVELS: frozenset[str] = frozenset({"L0", "L1", "L2"})


class ClaimSurfaceError(ValueError):
    """A claim-surface object violates a v0 schema invariant."""


@dataclass(frozen=True, slots=True)
class ClaimSpec:
    """``lawvm.claim_spec.v0`` — one declared PUBLIC claim, root-committed.

    A frozen record that LawVM publicly asserts ``public_sentence``, naming the
    semantic objects + roots that body the claim out and the declared
    non-guarantees that bound it. The keystone for the coverage gate is
    ``claim_id``: every claim must have ≥1 :class:`~lawvm.core.invariant_spec.InvariantSpec`
    citing this id and terminating in an ALLOWED bucket.

    Fields:

    * ``claim_id`` — stable dotted id (e.g. ``lawvm.fi.provision_state.selected.v1``).
    * ``public_sentence`` — the honest, externally-consumable statement of what
      is claimed. This is the thing the public reads; the invariants are how it
      is held to account.
    * ``required_objects`` — semantic object kinds the claim is bodied out by
      (e.g. ``"UniverseSpec"``, ``"ReferenceMention"``). Names, not instances —
      this is a declaration, not a binding to a particular pack.
    * ``required_roots`` — named Merkle roots that commit the claim's universe /
      evidence (e.g. ``"universe_root"``, ``"assumption_register_root"``).
    * ``allowed_non_guarantees`` — ``assumption_id``s (or stable assumption
      handles) referencing :mod:`lawvm.core.assumption_register` entries — the
      declared boundaries of THIS claim (what it does NOT promise).
    * ``checker_level`` — the level the claim is independently checkable AT.
    """

    claim_id: str
    public_sentence: str
    required_objects: tuple[str, ...] = ()
    required_roots: tuple[str, ...] = ()
    allowed_non_guarantees: tuple[str, ...] = ()
    checker_level: CheckerLevel = "L1"

    def __post_init__(self) -> None:
        if not self.claim_id or not self.claim_id.strip():
            raise ClaimSurfaceError("ClaimSpec.claim_id must be a non-empty dotted id")
        if not self.public_sentence or not self.public_sentence.strip():
            raise ClaimSurfaceError(
                f"ClaimSpec {self.claim_id!r} must carry a non-empty public_sentence "
                f"— a claim with no public statement is folklore, not a declared claim"
            )
        if self.checker_level not in CHECKER_LEVELS:
            raise ClaimSurfaceError(
                f"ClaimSpec {self.claim_id!r} checker_level must be one of "
                f"{sorted(CHECKER_LEVELS)!r}, got {self.checker_level!r}"
            )
        for field_name, values in (
            ("required_objects", self.required_objects),
            ("required_roots", self.required_roots),
            ("allowed_non_guarantees", self.allowed_non_guarantees),
        ):
            if not isinstance(values, tuple):
                raise ClaimSurfaceError(
                    f"ClaimSpec {self.claim_id!r} {field_name} must be a tuple"
                )
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise ClaimSurfaceError(
                        f"ClaimSpec {self.claim_id!r} {field_name} has an empty/"
                        f"non-string member {value!r}"
                    )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CLAIM_SPEC,
            "claim_id": nfc(self.claim_id),
            "public_sentence": nfc(self.public_sentence),
            "required_objects": list(self.required_objects),
            "required_roots": list(self.required_roots),
            "allowed_non_guarantees": list(self.allowed_non_guarantees),
            "checker_level": self.checker_level,
        }

    @property
    def claim_body_hash(self) -> str:
        """Content hash of the claim body (the value committed by the manifest root)."""
        return leaf_hash(_DOMAIN_CLAIM_SPEC, self.to_canonical_dict())


@dataclass(frozen=True, slots=True)
class ClaimSurfaceManifest:
    """``lawvm.claim_surface_manifest.v0`` — the declared SET of public claims.

    Holds the v0 declared subset of LawVM's public claims as a single
    root-committed artifact. :attr:`manifest_root` is a ``MapRoot`` over
    ``{claim_id: claim_body_hash}`` so adding, dropping, or editing any claim
    changes the root — the claim surface itself is a checkable, committed set
    (exactly as ``UniverseSpec.universe_root`` makes a unit universe checkable).

    The ``claim_surface_version`` is carried explicitly: completeness is
    claim-relative and versioned (Pro §12), never absolute.
    """

    claims: tuple[ClaimSpec, ...]
    claim_surface_version: str = CLAIM_SURFACE_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.claims, tuple):
            raise ClaimSurfaceError("ClaimSurfaceManifest.claims must be a tuple")
        seen: set[str] = set()
        for claim in self.claims:
            if not isinstance(claim, ClaimSpec):
                raise ClaimSurfaceError(
                    f"ClaimSurfaceManifest.claims member is not a ClaimSpec: {claim!r}"
                )
            if claim.claim_id in seen:
                raise ClaimSurfaceError(
                    f"duplicate claim_id {claim.claim_id!r} in ClaimSurfaceManifest "
                    f"— a public claim is declared once"
                )
            seen.add(claim.claim_id)
        if not self.claim_surface_version or not self.claim_surface_version.strip():
            raise ClaimSurfaceError(
                "ClaimSurfaceManifest.claim_surface_version must be non-empty "
                "— completeness is claim-relative and VERSIONED (Pro §12)"
            )

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(claim.claim_id for claim in self.claims)

    @property
    def manifest_root(self) -> str:
        """``MapRoot`` over ``{claim_id: claim_body_hash}`` — the keystone.

        Empty surface is a valid deterministic root. Adding/dropping/editing a
        claim changes the root, so the SET of public claims the gate ranges over
        is itself committed and checkable.
        """
        return map_root(
            _DOMAIN_CLAIM_SURFACE_MANIFEST_ROOT,
            {claim.claim_id: claim.claim_body_hash for claim in self.claims},
        )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_CLAIM_SURFACE_MANIFEST,
            "claim_surface_version": self.claim_surface_version,
            "manifest_root": self.manifest_root,
            "claims": [claim.to_canonical_dict() for claim in self.claims],
        }

    def __len__(self) -> int:
        return len(self.claims)


def claim_surface_manifest_root(claims: Sequence[ClaimSpec]) -> str:
    """``MapRoot`` over the claim ids — convenience over a bare claim sequence."""
    return ClaimSurfaceManifest(tuple(claims)).manifest_root


# --------------------------------------------------------------------------- #
# The v0 DECLARED claim surface — a meaningful, HONEST subset of REAL claims.   #
#                                                                              #
# Each ``public_sentence`` is the honest externally-consumable statement the   #
# enumerated production code actually emits; nothing here is fabricated. The   #
# Wave-1 objects (UniverseSpec / AssumptionRegister / KNOW) are wired in as    #
# the first cited objects/roots/non-guarantees so the backbone UNIFIES them.   #
# --------------------------------------------------------------------------- #

# The bench claim. Source of truth for the public sentence: the FI bench
# evidence-surface ``truth_claim`` is literally
# ``"finland_benchmark_agreement_regression_evidence_not_source_truth"``
# (finland/bench_bundle_proof_projector.py:53) with forbidden shortcuts
# "bench_score_as_source_truth" / "bench_score_as_replay_authorization". The
# claim is the structural+Levenshtein MEAN agreement score over the corpus —
# regression evidence, NOT source truth and NOT replay authorization.
CLAIM_BENCH_AGREEMENT = ClaimSpec(
    claim_id="lawvm.fi.bench.agreement_score.v1",
    public_sentence=(
        "The Finland benchmark mean structural/Levenshtein agreement score over "
        "the corpus is regression evidence of how closely replayed provision text "
        "tracks the oracle — it is NOT a claim of source truth and NOT replay "
        "authorization."
    ),
    required_objects=("EvidenceSurfaceReport", "BenchRunSummary"),
    required_roots=(),
    # The bench score's defining non-guarantees: it is not source truth, not
    # replay authorization. These map to declared AssumptionRegister entries
    # (see DECLARED_NON_GUARANTEES below) — the bench Wave-1 boundary.
    allowed_non_guarantees=(
        "bench_score_not_source_truth",
        "bench_score_not_replay_authorization",
    ),
    checker_level="L1",
)

# The materialization / selection claim. Bodied out by the Wave-1
# ``UniverseSpec`` (finland/materialization_totality.py): a declared universe of
# expected section units checked against the materialized PIT tree, with a
# root-committed universe so omission is detectable (the 1929/234 silent-drop
# class). This is the "this provision text is governing at PIT D" claim, held to
# account by per-unit (not aggregate) totality.
CLAIM_MATERIALIZATION_SELECTED = ClaimSpec(
    claim_id="lawvm.fi.provision_state.selected.v1",
    public_sentence=(
        "Every expected provision section in work W at point-in-time D is live in "
        "the materialized tree, an owned repeal tombstone, a declared typed "
        "absence, or a typed residual — no expected section is silently dropped "
        "(the universe of expected units is itself root-committed)."
    ),
    required_objects=("UniverseSpec", "MaterializationTotalityResult", "IRNode"),
    required_roots=("universe_root",),
    # The materialization claim is CLEAN only relative to the completeness of the
    # caller-supplied typed-absence set (a legitimate repeal with no surviving
    # tombstone and no supplied reason reports as a VIOLATION by design) and
    # ranges over SECTION units only in v0.
    allowed_non_guarantees=(
        "materialization_clean_relative_to_supplied_typed_absences",
        "materialization_universe_section_units_only",
    ),
    checker_level="L1",
)

# The reference-resolution claim. SURF-05: every emitted ReferenceMention
# carries a classification in the closed set {resolved, statute_only, ambiguous,
# open, broken, unsupported}; an unclassified reference is a typed
# REFERENCE.UNCLASSIFIED_REFERENCE finding, never silently dropped
# (finland/references/surface_totality.py).
CLAIM_REFERENCE_CLASSIFICATION = ClaimSpec(
    claim_id="lawvm.fi.reference.classification.v1",
    public_sentence=(
        "Every emitted reference mention is classified into the closed set "
        "{resolved, statute_only, ambiguous, open, broken, unsupported}; an "
        "out-of-set classification is a typed finding, not a silently widened set."
    ),
    required_objects=("ReferenceMention", "CitationTotalityFinding"),
    required_roots=(),
    # The classification is a SURFACE fact, never replay authority (a resolved
    # reference does not authorize a mutation); and resolution recall is bounded
    # (an OPEN/UNSUPPORTED mention is an honest non-resolution, not a defect).
    allowed_non_guarantees=(
        "reference_classification_is_surface_not_replay_authority",
        "reference_resolution_recall_bounded_open_is_honest",
    ),
    checker_level="L1",
)

# The source-monotonicity claim. Wave-1 KNOW family
# (core/know_invariants.py): KNOW-01 — no source locator silently swaps bytes;
# KNOW-03 — an absent-bytes observation is UNCHECKABLE, never INVALID.
CLAIM_SOURCE_MONOTONICITY = ClaimSpec(
    claim_id="lawvm.know.source_monotonicity.v1",
    public_sentence=(
        "No external source locator is observed carrying two distinct content "
        "digests (KNOW-01, append-only); a source observation with no resolvable "
        "digest is reported UNCHECKABLE, never INVALID (KNOW-03)."
    ),
    required_objects=("SourceObservation", "SourceMonotonicityReport"),
    required_roots=(),
    # KNOW v0 does NOT check KNOW-02 (source-policy naming) or KNOW-04 (retraction
    # taint) — no populated subject exists; and AVAILABLE asserts a RECORDED
    # digest, not a live re-fetch.
    allowed_non_guarantees=(
        "know_02_source_policy_not_checked_no_subject",
        "know_04_retraction_taint_not_checked_no_graph",
        "know_available_is_recorded_digest_not_liveness_probe",
    ),
    checker_level="L1",
)

# The EU directive transposition + timeliness claim (Wave-2 #73,
# finland/references/eu_transposition_edges.py).
CLAIM_EU_TRANSPOSITION = ClaimSpec(
    claim_id="lawvm.fi.eu.transposition_edge.v1",
    public_sentence=(
        "Where a Finnish act declares in its own text that it transposes a named EU "
        "directive, LawVM emits a typed transposition edge to that directive's CELEX "
        "(when the nickname registry binds it) and a timeliness verdict that is a "
        "pure comparison of the act's enactment date against the directive's "
        "transposition deadline — computed ONLY when both dates are known, else a "
        "typed UNKNOWN, never guessed. It is the DECLARED relation + timing, NOT a "
        "claim of substantive conformance."
    ),
    required_objects=("TranspositionClaim", "TranspositionEdge"),
    required_roots=(),
    allowed_non_guarantees=(
        "transposition_edge_not_substantive_conformance",
        "transposition_deadline_seed_not_complete",
        "transposition_fi_enactment_date_caller_supplied",
    ),
    checker_level="L1",
)

# The transclusion typed-derivation-edge claim (Wave-2 #74,
# finland/references/derivation_edges.py). TRANS family: dedup is not authority.
CLAIM_DERIVATION_EDGE = ClaimSpec(
    claim_id="lawvm.fi.derivation_edge.v1",
    public_sentence=(
        "LawVM classifies a relation between two pieces of Finnish (or FI->EU) legal "
        "text into exactly one of four DISTINCT derivation kinds — textual "
        "derivation, model-code kinship, EU conformance, citation — never conflating "
        "them: textual derivation (shared bytes) does NOT imply lineage, conformance, "
        "or citation; model-code lineage is typed-UNKNOWN, never guessed; and the "
        "authority matrix forbids forging a resemblance onto the legal-state plane."
    ),
    required_objects=("DerivationEdge",),
    required_roots=(),
    allowed_non_guarantees=(
        "derivation_textual_is_bytes_not_lineage",
        "derivation_model_code_lineage_not_byte_decidable",
        "derivation_conformance_claimed_not_assessed",
    ),
    checker_level="L1",
)

# The counterfactual 3-tier bill-effects claim (Wave-2 #72,
# tools/bill_counterfactual_effects.py). BRANCH-06: tiers kept distinct.
CLAIM_COUNTERFACTUAL_EFFECTS = ClaimSpec(
    claim_id="lawvm.fi.bill.counterfactual_effects.v1",
    public_sentence=(
        "For a Finnish amending act, LawVM reports its effects in three "
        "structurally-distinct tiers kept separate: provisions its operations "
        "directly change (tier 1); provisions in the amended act that depend on a "
        "changed section through a resolved reference — kept as three distinct "
        "sub-tiers, provisions that CITE a changed section (1-hop), provisions "
        "reached through a bounded MULTI-HOP citation cascade (depth ≥ 2, to a "
        "declared maximum depth, each carrying its full hop chain), and provisions "
        "that USE a term DEFINED in a changed section (tier 2); and a DECLARED set "
        "of effect classes it does not compute (tier 3). Every tier-1 and tier-2 "
        "item carries provenance; the report states WHAT moves, never how much it "
        "matters."
    ),
    required_objects=(
        "CounterfactualEffectsReport",
        "DirectEffect",
        "CitingProvisionEffect",
        "CitationCascadeEffect",
        "DefinitionUserEffect",
        "UncomputedBoundary",
    ),
    required_roots=(),
    allowed_non_guarantees=(
        "counterfactual_definition_users_single_hop_only",
        "counterfactual_cascade_bounded_depth_semantic_uncomputed",
        "counterfactual_bare_section_precision_bounded",
    ),
    checker_level="L1",
)

# The cross-jurisdiction generality claim (Wave-2 #75,
# core/materialization_universe.py). Anti-FI-overfitting evidence.
CLAIM_MATERIALIZATION_GENERALITY = ClaimSpec(
    claim_id="lawvm.xjur.materialization_totality_generality.v1",
    public_sentence=(
        "The per-unit materialization-totality invariant is a SINGLE "
        "jurisdiction-neutral implementation that runs unmodified over any "
        "jurisdiction whose provision units are IR nodes of one unit kind, and "
        "discriminates correctly (TOTAL when complete, a named SILENTLY_DROPPED_UNIT "
        "when a unit vanishes) on a REAL Estonian replay tree as well as the Finnish "
        "1929/234 witness — evidence the method is not FI-specific."
    ),
    required_objects=("UniverseSpec", "MaterializationTotalityResult"),
    required_roots=("universe_root",),
    allowed_non_guarantees=(
        "xjur_not_bug_class_portability",
        "xjur_not_reconstruction_parity",
        "xjur_section_units_only",
    ),
    checker_level="L1",
)

# The dangling-reference claim (tools/dangling_references.py). A read-only
# projection over the published fi_refs artifact: every RESOLVED reference is
# classified into a closed three-way existence status against the target act's
# CURRENT consolidated text-state; an act absent/unmaterialized is
# EXISTENCE_UNKNOWN, NEVER dangling (tag-don't-guess, no false-positive broken).
CLAIM_DANGLING_REFERENCE = ClaimSpec(
    claim_id="lawvm.fi.reference.dangling.v1",
    public_sentence=(
        "Over the published fi_refs projection, every RESOLVED cross-reference "
        "(cite_confidence exact/approximate, asserting a specific target provision) "
        "is classified into exactly one of the closed three-way statuses "
        "{PRESENT, DANGLING, EXISTENCE_UNKNOWN}: PRESENT iff the cited provision "
        "resolves in the target act's CURRENT consolidated text-state, DANGLING "
        "iff the act is materialized but the cited provision resolves to nothing, "
        "and EXISTENCE_UNKNOWN iff existence could not be determined (target act "
        "absent from the corpus, body not materialized, or no statute identity) — "
        "an EXISTENCE_UNKNOWN is an honest non-determination, NEVER reported as "
        "DANGLING. Non-resolved references (statute_only/ambiguous/open/...) are "
        "OUT of scope and counted separately. It is an AS-OF-NOW surface fact, "
        "NOT an as-of-citing defect and NOT a legal conclusion."
    ),
    required_objects=("DanglingReferenceReport", "DanglingReferenceRow"),
    required_roots=(),
    allowed_non_guarantees=(
        "dangling_existence_oracle_as_of_now_not_as_of_citing",
        "dangling_existence_oracle_current_state_incomplete_corpus",
        "dangling_resolved_only_scope_section_granularity",
    ),
    checker_level="L1",
)

# The fixed-term / temporary whole-law expiry safety claim
# (finland/fixed_term_expiry.py + core/statute_validity.py). A whole-law
# fixed-term clause (määräaikainen laki) is resolved to a typed validity bound
# under the inclusive/exclusive discipline (expires_on = valid_until + 1 day;
# expired at D iff D >= expires_on iff D > valid_until) OR raised as a strict
# finding — never a silently-wrong "still in force" answer.
CLAIM_FIXED_TERM_EXPIRY = ClaimSpec(
    claim_id="lawvm.fi.expiry.fixed_term.v1",
    public_sentence=(
        "Every recognised whole-law fixed-term/temporary expiry clause "
        "(määräaikainen laki) is resolved to a typed statute validity bound — "
        "from which a query at point-in-time D answers expired exactly when "
        "D is at or after expires_on (the exclusive cutoff = the inclusive "
        "source valid_until + 1 day) — OR, where the validity date cannot be "
        "parsed, two bounds with the same effective date conflict, or an "
        "anaphoric year is genuinely ambiguous, is raised as a strict finding; "
        "it is never silently degraded into a live 'still in force' answer."
    ),
    required_objects=("StatuteValidityBound", "FixedTermValidityProof"),
    required_roots=(),
    # The fixed-term claim's declared boundaries: the inclusive valid_until vs
    # exclusive expires_on convention is a stored fact (not re-derivable from one
    # date); ambiguous/anaphoric expiry is a strict finding, never a guess; and
    # the VÄLIAIKAINEN temporary-amendment unresolved-expiry case is a WARN, not
    # a strict block (a weaker, separately-declared enforcement).
    allowed_non_guarantees=(
        "fixed_term_inclusive_valid_until_vs_exclusive_expires_on",
        "fixed_term_ambiguous_expiry_is_strict_finding_not_guess",
        "fixed_term_temporary_unresolved_expiry_is_warn_not_strict",
    ),
    checker_level="L1",
)

# The version-timeline integrity claim (core/timeline_invariants.py). A
# provision's version timeline has no overlapping permanent versions, consistent
# temporary overlays, and a preserved (monotone) expiry chain; any violation is
# a typed TimelineInvariantViolation, never silent.
CLAIM_TIMELINE_INTEGRITY = ClaimSpec(
    claim_id="lawvm.fi.timeline.integrity.v1",
    public_sentence=(
        "Every provision version timeline is checked for three structural "
        "integrity properties — no two permanent versions share an effective "
        "date with ambiguous precedence, each temporary overlay carries a "
        "well-formed (present, non-inverted, non-overlapping) expiry interval, "
        "and every expiry-extension chain is monotonically increasing — and any "
        "breach is surfaced as a typed TimelineInvariantViolation carrying the "
        "section attribution, never silently tolerated."
    ),
    required_objects=("TimelineInvariantViolation",),
    required_roots=(),
    # The timeline-integrity claim's declared boundaries: the typed-violation
    # surface is computed by check_all_timeline_invariants_typed over a SELECTED
    # family set (robust families); the heavier replay/materialization-drift
    # check (IR-vs-timeline) is a SEPARATE, materialization-variant-tier check
    # not part of the three robust structural properties; and a violation is a
    # detection, never a legal conclusion or an automatic repair.
    allowed_non_guarantees=(
        "timeline_integrity_robust_families_replay_drift_separate",
        "timeline_integrity_violation_is_detection_not_conclusion",
    ),
    checker_level="L1",
)

# The corpus Legal Surface Graph claim (tools/corpus_surface_graph.py +
# finland/legal_surface/corpus_graph.py). The corpus graph is the FAIL-LOUD MERGE
# of per-statute surface graphs into one cross-statute network where the same
# cited target collapses to one shared entity node; every node/edge is a SURFACE
# fact (surface_only, never legal authority); a cross-statute reference edge only
# promotes a target the citing text already committed (ambiguous -> has_candidate;
# open / statute_only left as-is — never an invented target).
CLAIM_LEGAL_SURFACE_GRAPH = ClaimSpec(
    claim_id="lawvm.fi.legal_surface_graph.v1",
    public_sentence=(
        "The corpus legal surface graph is the FAIL-LOUD MERGE of per-statute "
        "Legal Surface Graphs into ONE cross-statute network in which the same "
        "cited target collapses to one shared entity node — so 'what cites this "
        "act/provision' is a graph query — where a same-node-id/divergent-payload "
        "merge collision RAISES (never a silent overwrite); every node and edge is "
        "a SURFACE fact (surface_only, minted through the assembler's "
        "firewall-enforcing path, NEVER legal authority and never replay "
        "authorization); and a cross-statute reference edge only PROMOTES a target "
        "the citing text already committed — a single resolved provision target "
        "becomes an asserted refers_to, an ambiguous resolution becomes a "
        "has_candidate, and an open / statute_only resolution is left as-is — never "
        "an invented target. On top of that backbone it folds three typed relation "
        "families: DEFINITION-USE (defines_term / uses_term, a shared defined term "
        "collapsing to one term_symbol_entity), EU TRANSPOSITION (a transposes edge "
        "from the citing act to the EU directive it DECLARES it transposes — "
        "CELEX-bound is asserted, unbound is a candidate, and the edge is the "
        "declared relation, NEVER a conformance conclusion), and DANGLING-REFERENCE "
        "STATUS (each provision-target node carries a three-way PRESENT / DANGLING / "
        "EXISTENCE_UNKNOWN existence verdict, so a reference into a broken target is "
        "legible as broken). It is an as-of surface projection over a DECLARED "
        "corpus slice, never a legal conclusion."
    ),
    required_objects=(
        "LegalSurfaceGraph",
        "CorpusSurfaceGraphExport",
        "CorpusNodeRow",
        "CorpusEdgeRow",
        "CorpusGraphCensus",
    ),
    required_roots=(),
    # The corpus-graph claim's declared boundaries: the graph merges the reference +
    # anaphora backbone AND three typed relation families (definition-use, EU
    # transposition, dangling-reference status); the DERIVATION edge family alone is
    # NOT yet merged (the remaining declared extension — it needs a provision-pair
    # candidate source the reference-only build does not produce); resolution recall
    # is bounded (an open / statute_only mention is an honest non-promotion); and the
    # graph is an as-of surface projection over the declared slice, never replay
    # authority.
    allowed_non_guarantees=(
        "legal_surface_graph_v1_reference_family_only_v2_extends",
        "legal_surface_graph_resolution_recall_bounded",
        "legal_surface_graph_surface_only_declared_slice_as_of",
    ),
    checker_level="L1",
)

# The Estonian consolidation-error candidate claim
# (estonia/consolidation_error_candidates.py). The adoption wedge: a read-only
# diagnostic that surfaces RANKED CANDIDATE consolidation errors for an EE
# (base, oracle) act pair — divergences where the official Riigi Teataja
# consolidated text (terviktekst) plausibly conflicts with the replayed
# amendment chain. Each candidate lands in exactly one of the closed tier set
# {strong, triage}: STRONG iff backed by an adjudicated consolidation-side
# residual record (source_oracle_drift / oracle_correction_notice), TRIAGE iff
# unadjudicated (flagged unadjudicated_needs_review, surfaced for human review).
# A candidate is a FLAG FOR HUMAN REVIEW, never a confirmed error and never a
# legal conclusion (constructive-invariant honesty boundary).
CLAIM_EE_CONSOLIDATION_ERROR_CANDIDATE = ClaimSpec(
    claim_id="lawvm.ee.consolidation_error_candidate.v1",
    public_sentence=(
        "For an Estonian (base, oracle) act pair, LawVM surfaces RANKED CANDIDATE "
        "consolidation errors — divergences where the official consolidated text "
        "(terviktekst) plausibly conflicts with the replayed amendment chain — "
        "each placed in exactly one of the closed tier set {strong, triage}: "
        "STRONG iff backed by an adjudicated consolidation-side residual record "
        "(source_oracle_drift / oracle_correction_notice), TRIAGE iff "
        "unadjudicated (flagged unadjudicated_needs_review); each candidate "
        "carries text evidence (a bounded replay-vs-consolidated snippet plus the "
        "full carrier text) and witness-rule / amending-act provenance, and the "
        "candidates are emitted in a deterministic total order (strong tier "
        "first). A candidate is a FLAG FOR HUMAN REVIEW, NOT a confirmed "
        "consolidation error and NOT a legal conclusion."
    ),
    required_objects=(
        "ConsolidationErrorCandidateReport",
        "ConsolidationErrorCandidate",
        "ConsolidationErrorEvidence",
    ),
    required_roots=(),
    # The candidate claim's declared boundaries: a candidate is NOT a confirmed
    # error (only a flag for human review); the surface depends on the upstream
    # EE replay + residual adjudication it consumes (it never re-adjudicates);
    # coverage/recall is bounded by that adjudication (an unadjudicated
    # divergence is triage, never asserted strong); and it ranges over EE
    # (base, oracle) act pairs only.
    allowed_non_guarantees=(
        "ee_consolidation_candidate_is_flag_not_confirmed_error",
        "ee_consolidation_candidate_depends_on_replay_and_residual_adjudication",
        "ee_consolidation_candidate_recall_bounded_unadjudicated_is_triage",
        "ee_consolidation_candidate_ee_pair_scope",
    ),
    checker_level="L2",
)

#: The v0 declared claim surface (a DECLARED SUBSET, not all claims; Pro §12).
V0_CLAIMS: tuple[ClaimSpec, ...] = (
    CLAIM_BENCH_AGREEMENT,
    CLAIM_MATERIALIZATION_SELECTED,
    CLAIM_REFERENCE_CLASSIFICATION,
    CLAIM_SOURCE_MONOTONICITY,
    CLAIM_EU_TRANSPOSITION,
    CLAIM_DERIVATION_EDGE,
    CLAIM_COUNTERFACTUAL_EFFECTS,
    CLAIM_MATERIALIZATION_GENERALITY,
    CLAIM_DANGLING_REFERENCE,
    CLAIM_FIXED_TERM_EXPIRY,
    CLAIM_TIMELINE_INTEGRITY,
    CLAIM_LEGAL_SURFACE_GRAPH,
    CLAIM_EE_CONSOLIDATION_ERROR_CANDIDATE,
)


def v0_claim_surface_manifest() -> ClaimSurfaceManifest:
    """The v0 :class:`ClaimSurfaceManifest` — the declared public-claim subset."""
    return ClaimSurfaceManifest(V0_CLAIMS, claim_surface_version=CLAIM_SURFACE_VERSION)


__all__ = [
    "CHECKER_LEVELS",
    "CLAIM_BENCH_AGREEMENT",
    "CLAIM_COUNTERFACTUAL_EFFECTS",
    "CLAIM_DANGLING_REFERENCE",
    "CLAIM_DERIVATION_EDGE",
    "CLAIM_EE_CONSOLIDATION_ERROR_CANDIDATE",
    "CLAIM_EU_TRANSPOSITION",
    "CLAIM_FIXED_TERM_EXPIRY",
    "CLAIM_LEGAL_SURFACE_GRAPH",
    "CLAIM_TIMELINE_INTEGRITY",
    "CLAIM_MATERIALIZATION_GENERALITY",
    "CLAIM_MATERIALIZATION_SELECTED",
    "CLAIM_REFERENCE_CLASSIFICATION",
    "CLAIM_SOURCE_MONOTONICITY",
    "CLAIM_SURFACE_VERSION",
    "CheckerLevel",
    "ClaimSpec",
    "ClaimSurfaceError",
    "ClaimSurfaceManifest",
    "V0_CLAIMS",
    "claim_surface_manifest_root",
    "v0_claim_surface_manifest",
]
