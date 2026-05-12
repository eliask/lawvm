"""Governed vocabulary for observation, obligation, and finding kinds.

Every Observation.kind and Obligation.kind string used in PhaseResult
must be registered here.  This prevents stringly-typed drift where
different stages invent ad-hoc kind names.

FINDING_REGISTRY is the single authoritative registry for all pipeline
findings.  It classifies every known signal into an error family,
enforcement level, and registry taxonomy role
(observation / obligation / barrier).

Barrier is strictness metadata, not a runtime Finding role.  Barrier
kinds belong on the registry and verdict rails only.

The public registry query helpers should be used directly instead of
materializing ad-hoc projection maps.  Add entries to FINDING_REGISTRY;
callers can query it by role when they need observation or obligation
subsets.

Prefix scheme (pipeline boundary):
    SCAN.*  -- tokenization/filter boundary
    PARSE.* -- parse/clause-surface boundary
    LOWER.* -- ClauseAST/LegalOperation lowering boundary
    ELAB.*  -- elaboration/payload boundary
    APPLY.* -- apply/replay boundary
    TIME.*  -- timeline/PIT boundary
    EVID.*  -- evidence/compare boundary
    CACHE.* -- cache/DB/UI boundary

To add a new kind: add a FindingSpec entry to FINDING_REGISTRY with
the appropriate role.  Callers can query codes by role when they need an
observation subset.

API tier
--------
Stable governed vocabulary surface.  FINDING_REGISTRY is the single
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional


# ---------------------------------------------------------------------------
# Error family and enforcement taxonomy (Pro review architecture)
# ---------------------------------------------------------------------------

FindingFamily = Literal[
    "violation",         # impossible/contract-broken state
    "ambiguity",         # source permits multiple meanings
    "recovery",          # compiler made non-source-authored move
    "source_pathology",  # source artifact malformed/incomplete
    "external_drift",    # external witness differs (not compiler failure)
    "projection_drift",  # downstream flattened/lost facts
    "audit",             # informational audit signal
]

Enforcement = Literal[
    "hard_fail",    # violation -> fail now
    "strict_fail",  # strict mode fails, quirks continues
    "warn",         # always visible, never blocking
    "info",         # informational only
]

ProofCategory = Literal[
    "parse_witness",           # "op came from this source span under rule R"
    "preservation",            # "transform preserved semantic distinctions"
    "ambiguity_resolution",    # "among N interpretations, chose this for reason R"
    "safety_invariant",        # "internal state is inside the admissible model"
    "temporal_selection",      # "at date D, version V governs because..."
    "lineage",                 # "provision P descends from these acts"
    "negative",                # "no later amendment touched this; no source supports oracle"
    "strictness",              # "compiled under profile P without non-permitted recoveries"
    "comparative",             # "divergence attributed to X, not vague mismatch"
    "non_commensurability",    # "not same-layer contradiction; representation mismatch"
]


FindingRole = Literal[
    "observation",  # informational, non-blocking
    "obligation",   # blocking requirement
    "violation",    # always-blocking contract break projected through Finding
]

FindingRegistryRole = Literal[
    "observation",  # informational, non-blocking
    "obligation",   # blocking requirement
    "barrier",      # strictness taxonomy metadata
    "violation",    # runtime contract-break projection
]


@dataclass(frozen=True)
class FindingSpec:
    """Unified metadata for one pipeline finding.

    The single registry entry type for all pipeline signals.  Every
    observation, obligation, and strict-barrier kind is represented as
    a FindingSpec in FINDING_REGISTRY.

    The ``role`` field classifies whether this finding originated as an
    observation (informational), obligation (blocking requirement),
    barrier (strictness taxonomy metadata), or runtime violation
    (contract-break projection).
    """
    code: str              # unique identifier (e.g. "ELAB.SOURCE_PATHOLOGY")
    phase: str             # which pipeline phase
    family: FindingFamily  # one of the error families
    default_enforcement: Enforcement
    owner: str             # module/boundary that emits this
    description: str       # one-line description
    proof_categories: tuple[ProofCategory, ...] = ()  # which proof categories this finding serves
    role: FindingRegistryRole = "observation"  # registry-only taxonomy role

    def __post_init__(self):
        if not self.code:
            raise ValueError("FindingSpec.code must be non-empty")
        if not self.phase:
            raise ValueError("FindingSpec.phase must be non-empty")

    # Role predicates -----------------------------------------------------

    @property
    def is_observation(self) -> bool:
        """True if this finding was originally an observation (informational)."""
        return self.role == "observation"

    @property
    def is_obligation(self) -> bool:
        """True if this finding was originally an obligation (blocking requirement)."""
        return self.role == "obligation"

    @property
    def is_barrier(self) -> bool:
        """True if this finding is a strict-mode barrier diagnostic."""
        return self.role == "barrier"


# ---------------------------------------------------------------------------
# FINDING_REGISTRY — the single authoritative registry
# ---------------------------------------------------------------------------

FINDING_REGISTRY: Dict[str, FindingSpec] = {f.code: f for f in (
    # --- Observations (role="observation") ---
    FindingSpec("ELAB.MISSING_PAYLOAD_SURFACE", "_build_group_surface",
                "recovery", "strict_fail", "grafter",
                "section_ir absent despite non-trivial ops; no payload surface to elaborate",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE", "_build_group_surface",
                "recovery", "strict_fail", "grafter",
                "same-group recodification payload selected from destination section when source-number body is absent or an omission shell",
                ("preservation", "parse_witness", "strictness"), role="observation"),
    FindingSpec("ELAB.SOURCE_PATHOLOGY", "_elaborate_group",
                "source_pathology", "warn", "grafter",
                "source XML structural anomaly detected during elaboration or replay",
                ("comparative",), role="observation"),
    FindingSpec("ELAB.SPARSE_SLOT_BINDING", "_elaborate_group",
                "audit", "info", "grafter",
                "subsection slot bound to a payload position (diagnostic trace)",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH", "sparse_subsection_elaboration",
                "ambiguity", "strict_fail", "payload_normalize",
                "sparse slot contains both item-level and plain ops targeting different paragraphs",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.PAYLOAD_COMPLETENESS", "_elaborate_group",
                "audit", "warn", "payload_normalize",
                "payload completeness witness emitted before apply to classify tail policy and completeness confidence",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.DROP_ITEM_REPLACES_MISSING", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "item-replace ops dropped because their targets are absent from sparse payload",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "item INSERT ops dropped because a same-slot sparse payload already carries the item body",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("ELAB.REBASE_SPARSE_STALE_PREDECESSOR", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "sparse subsection replace was rebound from the nominal target to the predecessor based on live-text similarity",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "duplicate-target sparse replace was rebound from the shared visible target to the shifted successor slot",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("ELAB.UNASSIGNED_SPARSE_SLOTS", "sparse_subsection_elaboration",
                "recovery", "warn", "payload_normalize",
                "payload slots remain unassigned after subsection elaboration",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "carried sibling subsections were pruned from a section payload that only owns one targeted moment",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "locally dense sparse subsection numbering was rebound to explicit target moments",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("ELAB.TRAILING_SPARSE_INSERT_BINDING", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "a lone trailing sparse INSERT was bound to the last remaining payload slot",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("ELAB.AMBIGUOUS_BINDING", "sparse_subsection_elaboration",
                "ambiguity", "strict_fail", "payload_normalize",
                "subsection slot has multiple equally-valid candidate bindings",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.CONTAINER_PRUNED_SHADOWED", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "sections pruned from container payload because they shadow live tree members",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.NORMALIZE_ITEM_LIKE_TARGET", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "item-like target normalized from guessed provenance into a concrete live slot",
                ("ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "item-like target rebound from a guessed label to the sparse slot label selected by payload normalization",
                ("ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "sparse omission subsections aligned to live tree order",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "single sparse-omission subsection split across consecutive replace ops",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "fused restarted subsection split across consecutive replace ops",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.CHAPTER_SEED_SKIP", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "ops targeting chapters already seeded from the same amendment body were suppressed",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.SEC1_PRE_ROUTING_FALLBACK", "process_muutoslaki",
                "recovery", "strict_fail", "grafter",
                "section 1 body text replaced the parsed johtolause before routing",
                ("parse_witness", "strictness"), role="obligation"),
    FindingSpec("ELAB.SEC1_POST_ROUTING_FALLBACK", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "section 1 body text replaced the parsed johtolause after routing",
                ("parse_witness", "strictness"), role="observation"),
    FindingSpec("ELAB.REJECTED_OPERATION", "_elaborate_group",
                "recovery", "warn", "grafter",
                "operation was rejected before apply during frontend fallback gating, elaboration, or constraint filtering",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.LAW_LEVEL_TEXT_PATCH_SEPARATE_LANE", "frontend_compile",
                "recovery", "warn", "frontend_compile",
                "law-level text patch bypassed structural AmendmentOp conversion because it executes through the law-level text patch lane",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.LEGACY_DISPATCH_FALLBACK", "apply_op",
                "recovery", "strict_fail", "grafter",
                "operation fell back to field-based dispatch due to missing or unhandled intent",
                ("strictness",), role="obligation"),
    FindingSpec("APPLY.RELABEL_SKIPPED", "apply_op",
                "recovery", "strict_fail", "grafter",
                "typed relabel intent was skipped for a governed reason without mutating replay state",
                ("strictness", "preservation"), role="obligation"),
    FindingSpec("APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK", "apply_op",
                "recovery", "warn", "grafter",
                "section-path resolution fell back to a live unique match after scoped lookup failed",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.INTENT_COMPAT_MISMATCH", "apply_op",
                "violation", "warn", "grafter",
                "typed canonical intent disagreed with the late-waist compatibility mirror",
                ("preservation", "safety_invariant"), role="observation"),
    FindingSpec("APPLY.OCCUPANCY_POLICY_VIOLATION", "apply_op",
                "violation", "warn", "grafter",
                "typed occupancy contract rejected the current target occupancy",
                ("preservation", "safety_invariant"), role="observation"),
    FindingSpec("APPLY.RELABEL_SKIP", "restructure_plan",
                "recovery", "warn", "grafter",
                "restructure-plan relabel was skipped for a governed reason without mutating replay state",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.MOVE_SKIP", "restructure_plan",
                "recovery", "warn", "grafter",
                "restructure-plan move was skipped for a governed reason without mutating replay state",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.RESTRUCTURE_PLAN_OP_DEFERRED", "restructure_plan",
                "audit", "warn", "grafter",
                "restructure-plan op was explicitly deferred to the ordinary leaf/subtree replay path",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.GLOBAL_LABEL_DEDUP_APPLIED", "grafter",
                "recovery", "warn", "grafter",
                "global same-kind+label dedup backstop modified the replay tree",
                ("safety_invariant", "preservation"), role="observation"),
    FindingSpec("APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "pending amendment-of-amendment was composed onto an already-processed target amendment in the same replay chain",
                ("temporal_selection", "preservation"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_SOURCE_CHAIN_GAP", "process_muutoslaki",
                "source_pathology", "warn", "grafter",
                "apply failure suppressed because a recodification source-chain gap already owns the missing target",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_SAME_WAVE_MIGRATION", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "apply failure suppressed because an exact same-wave migration resolves the old-frame target",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("PARSE.DUPLICATE_TARGET_OP", "frontend_ops",
                "ambiguity", "warn", "frontend_observations",
                "two or more ops address the same extracted target",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER", "frontend_extraction",
                "recovery", "strict_fail", "frontend_observations",
                "move/renumber clause collapsed to plain duplicate REPLACE ops",
                ("preservation",), role="observation"),
    FindingSpec("PARSE.PEG_SKIP_SEC1_REPEAL_LIST", "frontend_compile",
                "recovery", "warn", "frontend_compile",
                "PEG extraction was intentionally skipped for a sec1 repeal-list fallback pattern",
                ("parse_witness",), role="observation"),
    FindingSpec("PARSE.VTS_SKIPPED_TARGET_UNSUPPORTED", "frontend_extraction",
                "source_pathology", "warn", "vts",
                "voimaantulosäännös target was parsed but skipped because no safe typed repeal carrier exists",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("PARSE.META_CLAUSE_UNSUPPORTED", "frontend_extraction",
                "source_pathology", "warn", "effect_lowering",
                "meta clause was parsed but has no executable temporal/effect carrier in this frontend",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("PARSE.EXTRACTION_EMPTY", "frontend_compile",
                "audit", "warn", "frontend_compile",
                "all frontend extraction paths produced no operations",
                ("parse_witness",), role="observation"),
    FindingSpec("LOWER.CONTEXT_DEPENDENT_ANCHOR", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target depends on chapter/part scope carry-forward for address resolution",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET", "_compile_group",
                "recovery", "strict_fail", "grafter",
                "scoped section target was rebound to a body-backed unique live section path",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.EXPLICIT_CHUNK_SCOPE", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target scope was carried from an explicit source chunk in the johtolause",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("LOWER.EXPLICIT_SCOPE_REWRITE", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "explicit scope from source was rewritten using live-tree uniqueness or fallback heuristics",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.SCOPE_CARRY_FORWARD", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target requires chapter-scope carry-forward",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("TIME.SECTION_NO_TIMELINE", "check_consistency",
                "violation", "hard_fail", "consistency",
                "section present in PIT-materialized replay state has no corresponding timeline entry",
                ("safety_invariant", "comparative"), role="observation"),
    FindingSpec("TIME.TIMELINE_NO_SECTION", "check_consistency",
                "violation", "hard_fail", "consistency",
                "timeline entry has no corresponding section in PIT-materialized replay state",
                ("safety_invariant", "comparative"), role="observation"),
    FindingSpec("TIME.CONTENT_DRIFT", "check_consistency",
                "violation", "hard_fail", "consistency",
                "section exists in both replay state and timeline but their text content differs",
                ("safety_invariant", "comparative"), role="observation"),
    FindingSpec("text_duplication_warning", "replay_lints",
                "audit", "warn", "replay_lints",
                "replay output contains a suspicious duplicated text tract",
                ("comparative",), role="observation"),
    FindingSpec("flattened_sublist_family_warning", "replay_lints",
                "audit", "warn", "replay_lints",
                "replay output contains a possible flattened sublist family",
                ("comparative", "preservation"), role="observation"),
    # --- Obligations (role="obligation") ---
    FindingSpec("ELAB.SPARSE_PAYLOAD_LEFTOVER", "_elaborate_group",
                "recovery", "warn", "grafter",
                "unassigned payload slots remain after elaboration; non-blocking",
                ("preservation",), role="obligation"),
    FindingSpec("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY", "_elaborate_group",
                "source_pathology", "strict_fail", "grafter",
                "strict profile rejected a suspicious non-literal source path",
                ("strictness",), role="obligation"),
    FindingSpec("ELAB.STRICT_REJECTED_OPERATION", "_elaborate_group",
                "recovery", "strict_fail", "grafter",
                "operation was rejected before apply during frontend fallback gating, elaboration, or constraint filtering",
                ("strictness", "preservation"), role="obligation"),
    FindingSpec("APPLY.STRICT_REJECTED_UNCOVERED_BODY", "process_muutoslaki",
                "recovery", "strict_fail", "grafter",
                "uncovered body recovery rejected by strict profile",
                ("strictness",), role="obligation"),
    FindingSpec("APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH", "process_muutoslaki",
                "source_pathology", "strict_fail", "grafter",
                "corrigendum Population B patch rejected by strict profile",
                ("strictness",), role="obligation"),
    FindingSpec("PARSE.STRICT_REJECTED_TARGET_GUESSING", "frontend_compile",
                "recovery", "strict_fail", "frontend_compile",
                "parse_ops fallback heuristic rejected by strict profile",
                ("strictness",), role="obligation"),
    # --- Strict barriers (role="barrier") ---
    FindingSpec("APPLY.UNCOVERED_BODY_RECOVERY", "apply",
                "recovery", "strict_fail", "compile_result",
                "uncovered body recovery was needed",
                ("strictness",), role="obligation"),
    FindingSpec("ELAB.OMISSION_EXPANSION", "apply",
                "recovery", "strict_fail", "compile_result",
                "omission expansion was needed",
                ("strictness", "ambiguity_resolution"), role="obligation"),
    FindingSpec("APPLY.FALLBACK_WHOLE_SECTION_REPLACE", "apply",
                "recovery", "strict_fail", "compile_result",
                "fallback whole-section replace was needed",
                ("strictness",), role="obligation"),
    FindingSpec("APPLY.SOURCE_INCOMPLETE", "scan",
                "source_pathology", "strict_fail", "compile_result",
                "source data incomplete",
                ("negative",), role="obligation"),
    FindingSpec("APPLY.SOURCE_PATHOLOGY_DETECTED", "elaborate",
                "source_pathology", "strict_fail", "compile_result",
                "source pathology detected during compilation",
                ("comparative",), role="obligation"),
    FindingSpec("APPLY.SOURCE_CORRECTED_BY_PATCH", "apply",
                "source_pathology", "strict_fail", "compile_result",
                "source corrected by corrigendum patch",
                ("lineage",), role="obligation"),
    FindingSpec("APPLY.FAILED_OPERATION", "apply",
                "source_pathology", "strict_fail", "compile_result",
                "one or more operations failed deterministically",
                ("safety_invariant",), role="obligation"),
    FindingSpec("PARSE.EXTRACTION_FALLBACK", "parse",
                "recovery", "strict_fail", "compile_result",
                "extraction fallback or heuristic parse was needed",
                ("parse_witness", "strictness"), role="obligation"),
    FindingSpec("PARSE.TARGET_GUESSING", "parse",
                "recovery", "strict_fail", "compile_result",
                "target guessing heuristic was needed",
                ("parse_witness", "ambiguity_resolution"), role="obligation"),
    FindingSpec("LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION", "lower",
                "recovery", "strict_fail", "compile_result",
                "context-dependent anchor resolution was needed",
                ("ambiguity_resolution",), role="obligation"),
    FindingSpec("LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED", "lower",
                "recovery", "strict_fail", "compile_result",
                "explicit source chunk scope was required to resolve the target address",
                ("parse_witness", "ambiguity_resolution"), role="obligation"),
    FindingSpec("LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED", "lower",
                "recovery", "strict_fail", "compile_result",
                "explicit source scope was rewritten using live-tree fallback or uniqueness heuristics",
                ("ambiguity_resolution", "strictness"), role="obligation"),
    FindingSpec("TIME.UNRESOLVED_TEMPORARY_EXPIRY", "frontend_compile",
                "source_pathology", "warn", "frontend_compile",
                "VÄLIAIKAINEN amendment has no parseable expiry date; version emitted as temporary without expiry",
                ("temporal_selection",), role="observation"),
    FindingSpec("COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED", "coverage_analysis",
                "recovery", "strict_fail", "grafter",
                "chapter-level INSERT plan has high uncovered body ratio; fallback proceeded with degraded confidence",
                ("preservation", "strictness"), role="obligation"),
    FindingSpec("COVERAGE.BODY_UNIT_IGNORED", "coverage_analysis",
                "audit", "warn", "grafter_uncovered",
                "body coverage ignored a malformed or unlabeled source unit and preserved an explicit witness",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("COVERAGE.CLAIM_REJECTED", "coverage_analysis",
                "audit", "warn", "grafter_uncovered",
                "body coverage rejected an unsupported or targetless coverage claim and preserved an explicit witness",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("COVERAGE.UNRESOLVED_BODY_GAP", "coverage_analysis",
                "ambiguity", "strict_fail", "grafter_uncovered",
                "body coverage found an unresolved uncovered unit that could not be synthesized automatically",
                ("preservation", "strictness"), role="obligation"),
    FindingSpec("TIME.MISSING_EFFECTIVE_DATE", "timeline",
                "ambiguity", "strict_fail", "compile_result",
                "no explicit effective date available",
                ("temporal_selection",), role="barrier"),
    FindingSpec("TIME.UNRESOLVED_COMMENCEMENT_TRIGGER", "timeline",
                "ambiguity", "strict_fail", "compile_result",
                "commencement depends on external trigger (decree/condition) not yet resolved",
                ("temporal_selection",), role="barrier"),
    FindingSpec("TIME.TRIGGER_COVERAGE_INCOMPLETE", "timeline",
                "ambiguity", "strict_fail", "compile_result",
                "cannot certify whether commencement trigger has been resolved — source coverage incomplete",
                ("temporal_selection",), role="obligation"),
    # --- Obligations (role="obligation") ---
    FindingSpec("TIME.ESTIMATED_EFFECTIVE_DATE", "timeline",
                "ambiguity", "strict_fail", "compile_result",
                "effective date was estimated from text or publication metadata",
                ("temporal_selection",), role="obligation"),
    FindingSpec("TIME.CONTINGENT_EFFECTIVE_DATE", "timeline",
                "ambiguity", "strict_fail", "compile_result",
                "effective date is contingent/non-deterministic (coarse umbrella)",
                ("temporal_selection",), role="obligation"),
    FindingSpec("TIME.EMPTY_SAME_DAY_INTERVAL", "timeline",
                "audit", "warn", "timeline",
                "timeline contains a zero-length same-day effective/expiry interval",
                ("temporal_selection",), role="observation"),
    FindingSpec("TIME.TIMELINE_EXECUTION_ISSUE", "timeline",
                "ambiguity", "strict_fail", "timeline",
                "timeline execution emitted a blocking typed issue; detail.rule_id carries the exact issue code",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("APPLY.TREE_INVARIANT_VIOLATION", "apply",
                "violation", "hard_fail", "compile_result",
                "tree structural invariant violated",
                ("safety_invariant",), role="violation"),
    FindingSpec("APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION", "apply",
                "violation", "hard_fail", "compile_result",
                "replay product invariant violated",
                ("safety_invariant",), role="violation"),
    FindingSpec("REPLAY_SKIPPED_OP_MUTATED_TREE", "apply",
                "violation", "hard_fail", "grafter",
                "skipped replay op still reported tree mutations",
                ("safety_invariant",), role="violation"),
    FindingSpec("REPLAY_FAILED_OP_MUTATED_TREE", "apply",
                "violation", "hard_fail", "grafter",
                "failed replay op still reported tree mutations",
                ("safety_invariant",), role="violation"),
    FindingSpec("REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION", "apply",
                "violation", "hard_fail", "grafter",
                "applied replay op did not consume its primary target",
                ("safety_invariant",), role="violation"),
    FindingSpec("REPLAY_APPLY_BOUNDARY_UNRESOLVED", "apply",
                "violation", "hard_fail", "grafter",
                "applied replay op mutated the tree without a resolved target boundary",
                ("safety_invariant",), role="violation"),
    FindingSpec("REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET", "apply",
                "violation", "hard_fail", "grafter",
                "applied replay op touched paths outside its declared target",
                ("safety_invariant",), role="violation"),
    FindingSpec("RUNTIME.VIOLATION", "phase_result",
                "violation", "hard_fail", "phase_result",
                "generic runtime contract violation projected through the finding ledger",
                ("safety_invariant",), role="violation"),
    FindingSpec("APPLY.WORD_SUBSTITUTION", "apply",
                "recovery", "strict_fail", "compile_result",
                "word-level text substitution was needed",
                ("strictness",), role="barrier"),
    FindingSpec("BASE_UNNUMBERED_PARAGRAPH_PEER", "base_source_analysis",
                "source_pathology", "info", "statute",
                "base statute has unnumbered paragraph as peer of numbered paragraphs",
                ("comparative",), role="observation"),
    FindingSpec("BASE_UNNUMBERED_PEER_REPARENT", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization reparented an unnumbered paragraph peer into the preceding numbered item",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_TAIL_PROSE_ABSORB", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization absorbed a tail-prose peer into the preceding numbered item wrap-up",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_NUM_IN_INTRO_RECOVERED", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization recovered a missing item number from intro/body text into a numbered child",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_NUM_IN_INTRO_MISMATCH", "source_normalize",
                "source_pathology", "info", "statute",
                "base statute source normalization detected a number-like intro/body token that could not be safely recovered into the sibling sequence",
                ("comparative",), role="observation"),
    FindingSpec("BASE_SUSPICIOUS_SHAPE", "source_normalize",
                "source_pathology", "info", "statute",
                "base statute source normalization detected a suspicious source shape and preserved it with an explicit witness",
                ("comparative",), role="observation"),
    FindingSpec("BASE_TAG_RECLASSIFY", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization reclassified a mis-tagged structural node into the legal Finland IR shape",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_CROSS_HEADING_HOIST", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization hoisted a standalone cross-heading into the following structural node",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_DUPLICATE_DROP", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization dropped a duplicate-labelled structural sibling",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_DUPLICATE_SIBLING_DROP", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization pruned a later duplicate-labelled sibling from a numbered sequence",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_DIGIT_RESET_SPLIT", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization split a digit-reset subparagraph run into a new sibling paragraph",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_DUPLICATE_TAIL_SPLIT", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization lifted duplicated trailing list prose into a new sibling subsection",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_EDITORIAL_STRIP", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization stripped editorial-only source material from the legal tree",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_NUMBERING_REPAIR", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization repaired or explicitly witnessed a numbering anomaly in the legal source tree",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("LABEL_EID_DIVERGENCE", "base_source_analysis",
                "source_pathology", "info", "statute",
                "base statute has paragraph with label/eId numeric mismatch",
                ("comparative",), role="observation"),
    FindingSpec("TIME.ACTIVATION_RULE_INPUT_SKIPPED", "temporal_lowering",
                "audit", "warn", "temporal_lowering",
                "typed temporal input was skipped because it does not lower to an ActivationRule",
                ("temporal_selection",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_DUPLICATE_CANDIDATE", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a duplicate recovered section candidate",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_CROSS_CHAPTER_COLLISION", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because the existing target resolves to a different chapter",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_MOVED_DESTINATION_MISMATCH", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because a move destination binds that label to a different chapter",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned as an explicit same-wave relabel destination",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_BODY_PAIRING_GUARD", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because body-pairing marked it foreign, unmatched, or repeal-claimed",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_NO_CONTENT_OPS", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because PEG had no substantive content operations for that target",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_WOULD_LOSE_SUBSECTIONS", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because literal adoption would lose live subsection structure",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PAST_REPEAL_GUARD", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a repeal-placeholder slot without an explicit restoring insert witness",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_JOHTO_GUARD", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because johtolause scope did not justify the label",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_OMISSION_MERGE_FAILED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because omission merge could not produce a safe replacement payload",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_OMISSION_MERGE_LOW_TEXT_RATIO", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because omission merge text retention was too low",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_OMISSION_MERGE_DUPLICATE_LABELS", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because omission merge introduced duplicate subsection labels",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_OMISSION_MERGE_WOULD_LOSE_SUBSECTIONS", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because omission merge would lose live subsection structure",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_LABEL_COLLISION", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned by PEG under the same label in another chapter",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_SAME_CHAPTER_OWNED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned by PEG under the same label in the same chapter",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_FUTURE_REPEAL_SKIP", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because a later amendment repeals it",
                ("preservation", "temporal_selection"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned by a whole-chapter payload claim",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_MIXED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "covered chapter payload mixed owned child sections with explicit uncovered-body adoptions",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery considered a body section candidate but skipped it for a governed reason",
                ("preservation",), role="observation"),
)}


# ---------------------------------------------------------------------------
# Registry query helpers
# ---------------------------------------------------------------------------

def finding_codes_by_role(role: FindingRegistryRole) -> tuple[str, ...]:
    """Return all registry codes whose FindingSpec.role matches ``role``."""
    return tuple(k for k, v in FINDING_REGISTRY.items() if v.role == role)


def get_finding_spec(code: str) -> Optional[FindingSpec]:
    """Look up a FindingSpec by code."""
    return FINDING_REGISTRY.get(code)


def is_registered_finding_kind(code: str) -> bool:
    """True when the code is present in the authoritative finding registry."""
    return code in FINDING_REGISTRY


def validate_finding_projection(kind: str, role: FindingRole, blocking: bool) -> None:
    """Validate the canonical Finding projection contract for one registry code."""
    if role not in ("observation", "obligation", "violation"):
        raise ValueError(
            f"Finding.kind={kind!r} has invalid role={role!r}; "
            f"expected one of {'observation'!r}, {'obligation'!r}, or {'violation'!r}"
        )
    spec = get_finding_spec(kind)
    if spec is None:
        raise ValueError(
            f"Finding.kind={kind!r} is not registered; runtime findings must use governed registry codes"
        )
    if spec.role == "barrier":
        raise ValueError(
            f"Finding.kind={kind!r} is a barrier registry code and has no runtime Finding.role"
        )
    if role != spec.role:
        raise ValueError(
            f"Finding.kind={kind!r} has role={role!r}; expected {spec.role!r}"
        )
    if not isinstance(blocking, bool):
        raise TypeError(
            f"Finding.kind={kind!r} role={role!r} requires blocking to be bool, "
            f"got {type(blocking).__name__}"
        )
    if role == "observation" and blocking:
        raise ValueError(
            f"Finding.kind={kind!r} role='observation' cannot be blocking=True"
        )
    if role == "violation" and not blocking:
        raise ValueError(
            f"Finding.kind={kind!r} role='violation' must be blocking=True"
        )


# ---------------------------------------------------------------------------
# Registry query helpers (Phase 8: registry-driven strict policy)
# ---------------------------------------------------------------------------

def strict_fail_codes_by_enforcement(enforcement: Enforcement) -> set[str]:
    """Return all finding codes with the given default enforcement."""
    return {f.code for f in FINDING_REGISTRY.values() if f.default_enforcement == enforcement}


def strict_fail_codes_by_family(family: FindingFamily) -> set[str]:
    """Return all finding codes in the given family."""
    return {f.code for f in FINDING_REGISTRY.values() if f.family == family}
