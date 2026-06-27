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
from typing import Dict, Literal, Optional, get_args


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
    "migration",               # "provision identity/path moved under an owned migration event"
    "negative",                # "no later amendment touched this; no source supports oracle"
    "strictness",              # "compiled under profile P without non-permitted recoveries"
    "comparative",             # "divergence attributed to X, not vague mismatch"
    "non_commensurability",    # "not same-layer contradiction; representation mismatch"
    "provenance",              # "source/evidence lineage for why a phase fact exists"
]

_VALID_FINDING_FAMILIES = frozenset(get_args(FindingFamily))
_VALID_ENFORCEMENTS = frozenset(get_args(Enforcement))
_VALID_PROOF_CATEGORIES = frozenset(get_args(ProofCategory))


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

_VALID_FINDING_REGISTRY_ROLES = frozenset(get_args(FindingRegistryRole))


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
        if self.family not in _VALID_FINDING_FAMILIES:
            raise ValueError(f"FindingSpec.family is not a known finding family: {self.family!r}")
        if self.default_enforcement not in _VALID_ENFORCEMENTS:
            raise ValueError(
                f"FindingSpec.default_enforcement is not a known enforcement: {self.default_enforcement!r}"
            )
        if not self.owner:
            raise ValueError("FindingSpec.owner must be non-empty")
        if not self.description:
            raise ValueError("FindingSpec.description must be non-empty")
        proof_categories = tuple(self.proof_categories)
        unknown_categories = sorted(
            category for category in proof_categories if category not in _VALID_PROOF_CATEGORIES
        )
        if unknown_categories:
            joined = ", ".join(repr(category) for category in unknown_categories)
            raise ValueError(f"FindingSpec.proof_categories contains unknown categories: {joined}")
        object.__setattr__(self, "proof_categories", proof_categories)
        if self.role not in _VALID_FINDING_REGISTRY_ROLES:
            raise ValueError(f"FindingSpec.role is not a known registry role: {self.role!r}")

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
                "recovery", "warn", "grafter",
                "section_ir absent despite non-trivial ops; no payload surface to elaborate",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE", "_build_group_surface",
                "recovery", "warn", "grafter",
                "same-group recodification payload selected from destination section when source-number body is absent or an omission shell",
                ("preservation", "parse_witness", "strictness"), role="observation"),
    FindingSpec("ELAB.RENUMBER_DESTINATION_PAYLOAD_SURFACE", "_build_group_surface",
                "recovery", "warn", "compile_group_surface",
                "pure renumber payload selected from destination section rather than source label",
                ("preservation", "parse_witness"), role="observation"),
    FindingSpec("ELAB.RENUMBER_SOURCE_LABEL_PAYLOAD_NOT_CLAIMED", "_build_group_surface",
                "recovery", "warn", "compile_group_surface",
                "pure renumber left same-label source-body payload unclaimed because relabel payload belongs at destination label",
                ("preservation", "parse_witness"), role="observation"),
    FindingSpec("ELAB.SPARSE_OMISSION_TAIL_CLAIM", "_build_group_surface",
                "recovery", "strict_fail", "compile_group_surface",
                "explicit descendant target claimed the unique post-omission subsection payload carried by another source section",
                ("parse_witness", "preservation", "strictness"), role="observation"),
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
    FindingSpec("ELAB.SPLIT_MIXED_SPARSE_SLOT_CROSS_PARAGRAPH_PAYLOAD", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "plain moment payload was pruned so cross-paragraph item bodies remain owned by their explicit item ops",
                ("preservation", "ambiguity_resolution", "strictness"), role="observation"),
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
    FindingSpec("ELAB.REBASE_REPLACED_RENUMBER_SOURCE", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "same-wave replacement of a renumbered subsection source was rebound onto the typed destination",
                ("ambiguity_resolution", "lineage", "strictness"), role="observation"),
    FindingSpec("ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "same-target sparse insert was bound to the slot before an explicitly moved replacement target",
                ("ambiguity_resolution", "lineage", "strictness"), role="observation"),
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
    FindingSpec("ELAB.SAME_TARGET_ITEM_SLOT_SHARING", "sparse_subsection_elaboration",
                "audit", "info", "payload_normalize",
                "item operations with the same subsection target shared an already-owned sparse payload slot",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("ELAB.UNLABELED_TABLE_ITEM_ROW_SOURCE_ORDER", "sparse_subsection_elaboration",
                "audit", "info", "payload_normalize",
                "same-moment item operations were bound to unlabeled table-row payloads in source order",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("ELAB.TRAILING_SPARSE_INSERT_BINDING", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "a lone trailing sparse INSERT was bound to the last remaining payload slot",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_normalize",
                "subsection renumber destination was bound to an explicit carried payload slot",
                ("preservation", "lineage", "strictness"), role="observation"),
    FindingSpec("ELAB.AMBIGUOUS_BINDING", "sparse_subsection_elaboration",
                "ambiguity", "strict_fail", "payload_normalize",
                "subsection slot has multiple equally-valid candidate bindings",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.CONTAINER_PRUNED_SHADOWED", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "sections pruned from container payload because they shadow live tree members",
                ("preservation",), role="observation"),
    FindingSpec("ELAB.SPARSE_OMISSION_TAIL_PRUNED_FROM_CARRIER", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "post-omission subsection payload pruned from its carrier section after an explicit descendant target claimed it",
                ("parse_witness", "preservation", "strictness"), role="observation"),
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
    FindingSpec("ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "carried live sibling text trimmed from the explicitly targeted sparse subsection payload",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "fused restarted subsection split across consecutive replace ops",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("ELAB.SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "source-split target subsection prefix and intro/list tail folded into one owned payload slot",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "source-split wrap-up slots folded into explicitly replaced list-shaped subsection payloads",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "detached sentence glued to the final list item promoted to a following subsection",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "source-split sibling tail folded into the one explicitly inserted list-shaped subsection payload",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.SPLIT_FLATTENED_INSERT_SUBSECTION_TAIL", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "flattened insert-subsection tail split to explicit consecutive source targets",
                ("ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "flattened first-subsection list rows collapsed into owned paragraph/subparagraph payload",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "body-bearing section heading admitted as an explicitly targeted subsection payload slot",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "whole-section payload leading subsection promoted to section heading and following subsection slots shifted",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET", "group_payload_normalization",
                "recovery", "strict_fail", "compile_group_elaboration",
                "typed source heading restored to a sparse prepared payload for an explicit same-section heading facet op",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.LEADING_OMISSION_ANCHOR_PREFIX_MERGE", "group_payload_normalization",
                "recovery", "strict_fail", "merge",
                "leading section omission preserved a live same-subsection prefix before an explicit payload anchor",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.SPARSE_DESCENDANT_LABEL_OMISSION_MERGE", "group_payload_normalization",
                "recovery", "strict_fail", "merge",
                "sparse omission payload row replaced a uniquely labelled live descendant while preserving live subsection shape",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.TEXT_TABLE_ROW_CONTINUATION", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "textual table-row sibling subsections folded into the explicitly targeted subsection payload",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.UNLABELED_ADJACENT_SECTION_CONTINUATION", "group_payload_normalization",
                "recovery", "strict_fail", "amendment_payload_lookup",
                "unlabeled adjacent amendment section was admitted as continuation of the selected numbered section payload",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.WRAPPER_ORPHAN_SUBSECTION_CONTINUATION", "group_payload_normalization",
                "recovery", "strict_fail", "amendment_payload_lookup",
                "wrapper-level orphan amendment subsections were admitted as continuation of the selected numbered section payload",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE", "group_payload_normalization",
                "recovery", "warn", "payload_normalize",
                "partial province-table payload merged into live table using named_row_targets",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("ELAB.NUMBERED_TABLE_TARGET_MERGE", "group_payload_normalization",
                "recovery", "strict_fail", "payload_normalize",
                "explicit numbered-table target merged into live section without authorizing a whole-section replacement",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.DUPLICATE_TABLE_NOTE_BLOCK_PRUNED", "group_payload_normalization",
                "source_pathology", "strict_fail", "payload_normalize",
                "adjacent duplicate note-row block pruned from an explicitly targeted table replacement payload",
                ("provenance", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.NUMBERED_TABLE_CHILD_GROUP_SPLIT", "compile_amendment_ops",
                "audit", "info", "compile_amendment",
                "numbered-table proxy compiled separately from explicit child targets to preserve mutation authority",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("ELAB.NUMBERED_TABLE_XML_SUBSECTION_OFFSET", "sparse_subsection_elaboration",
                "audit", "info", "payload_elaboration",
                "legal moment target rebound to the following XML subsection because a numbered table occupies a source subsection slot",
                ("provenance", "ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.SPARSE_PLAIN_SUBSECTION_SHELL_CONTINUATION_MERGE", "sparse_subsection_elaboration",
                "recovery", "strict_fail", "payload_elaboration",
                "colon-ending sparse subsection shell merged into the previous authorized intro while its immediate continuation slot was rebound to the already-authorized plain subsection replacement",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.INTERNAL_ORDERED_LIST_INSERT_REWRITE", "group_payload_normalization",
                "audit", "info", "payload_elaboration",
                "internal ordered-list section update rewritten from an unsafe whole-section replace to item inserts",
                ("provenance", "ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.NUMBERED_TABLE_COMPANION_SUBSECTION_BINDING", "sparse_subsection_elaboration",
                "audit", "info", "payload_elaboration",
                "single non-table companion subsection bound to an explicit numbered-table moment target after sparse omission alignment",
                ("provenance", "ambiguity_resolution", "preservation"), role="observation"),
    FindingSpec("ELAB.SPARSE_PARTIAL_SCOPE_ROW_OMISSION_REPEAL", "frontend_extraction",
                "recovery", "warn", "frontend_compile",
                "sparse osalta amendment lowered from source modify verb to explicit named paragraph-row repeal",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.HISTORICAL_TOP_LEVEL_ITEM_AS_SUBSECTION", "frontend_compile",
                "recovery", "warn", "frontend_compile",
                "historical top-level kohta wording mapped to direct subsection targets when source payload and live tree prove parenthesized subsection siblings",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.CHAPTER_SEED_SKIP", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "ops targeting chapters already seeded from the same amendment body were suppressed",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("ELAB.CHAPTER_SEED_REPAIR", "execute_replay_plan",
                "recovery", "strict_fail", "chapter_seed",
                "missing chapter container was inserted from an amendment body before replay",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.CHAPTER_MEMBERSHIP_MIGRATION", "replay_apply",
                "recovery", "warn", "grafter",
                "existing flat section was moved into a source-declared chapter introduced by the same amendment",
                ("preservation", "migration", "parse_witness"), role="observation"),
    FindingSpec("ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY", "execute_replay_plan",
                "source_pathology", "strict_fail", "chapter_seed",
                "chapter-seed pre-scan could not inspect a source artifact needed to prove completeness",
                ("negative", "strictness"), role="obligation"),
    FindingSpec("SOURCE.ABRIDGED_BASE_CHAPTER_UNRECONSTRUCTABLE", "execute_replay_plan",
                "source_pathology", "warn", "chapter_seed",
                "abridged base omits a whole chapter span no amendment body restates; "
                "its delta-touched provisions diverge from the oracle by construction, not by replay fault",
                ("negative", "comparative"), role="observation"),
    FindingSpec("FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK", "process_muutoslaki",
                "recovery", "strict_fail", "grafter",
                "section 1 body text replaced the parsed preamble (fi: johtolause) before routing",
                ("parse_witness", "strictness"), role="obligation"),
    FindingSpec("FI.PREAMBLE_BODY_POST_ROUTING_FALLBACK", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "section 1 body text replaced the parsed preamble (fi: johtolause) after routing",
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
    FindingSpec("APPLY.SAME_WAVE_MIGRATION_REBASE", "apply_op",
                "recovery", "warn", "grafter",
                "section-path resolution followed a same-wave migration event to the current address",
                ("preservation", "lineage"), role="observation"),
    FindingSpec("APPLY.SAME_WAVE_SHIFTED_SUBSECTION_REPEAL_TARGET", "replay_apply",
                "recovery", "warn", "apply_ops_executor",
                "subsection repeal target followed a same-amendment insert shift witnessed by a renumber destination",
                ("preservation", "lineage"), role="observation"),
    FindingSpec("APPLY.RESOLVER_BINDING_CONTRACT_ERROR", "apply_op",
                "violation", "warn", "grafter",
                "apply-time target resolver binding instrumentation violated its contract",
                ("safety_invariant", "lineage"), role="observation"),
    FindingSpec("APPLY.REPLAY_UNDECLARED_TREE_TOUCH", "process_muutoslaki",
                "audit", "info", "grafter",
                "passive observed-vs-declared cross-check saw a changed tree path the op's mutation events do not declare",
                ("safety_invariant",), role="observation"),
    FindingSpec("APPLY.INTENT_COMPAT_MISMATCH", "apply_op",
                "violation", "warn", "grafter",
                "typed canonical intent disagreed with the late-waist compatibility mirror",
                ("preservation", "safety_invariant"), role="observation"),
    FindingSpec("APPLY.OCCUPANCY_POLICY_VIOLATION", "apply_op",
                "violation", "warn", "grafter",
                "typed occupancy contract rejected the current target occupancy",
                ("preservation", "safety_invariant"), role="observation"),
    FindingSpec("APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT", "apply_op",
                "audit", "info", "grafter",
                "temporary insert whose bounded in-force window can be represented without a permanent occupancy violation",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.RELABEL_SKIP", "restructure_plan",
                "recovery", "warn", "grafter",
                "restructure-plan relabel was skipped for a governed reason without mutating replay state",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.RELABEL_MIGRATION_LEDGER_LOOKUP", "restructure_plan",
                "recovery", "warn", "grafter",
                "restructure-plan relabel resolved an amendment-frame target via prior migration lineage",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.RELABEL_STRUCTURAL_LABEL_ALIAS_LOOKUP", "restructure_plan",
                "recovery", "warn", "grafter",
                "restructure-plan relabel resolved an amendment-frame target via Finland structural-label normalization",
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
    FindingSpec("REPLAY.FOLD_TIMELINE_BACKFILL", "replay_products",
                "recovery", "warn", "grafter",
                "replay fold section lacked timeline authority and received an owned snapshot graft before PIT materialization",
                ("preservation", "lineage"), role="observation"),
    FindingSpec("REPLAY.TIMELINE_VERSION_DEDUPE", "replay_products",
                "recovery", "warn", "grafter",
                "same-source timeline bucket carried competing version rows collapsed by an owned dedupe rule before PIT materialization",
                ("preservation", "lineage"), role="observation"),
    FindingSpec("REPLAY.MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT", "replay_products",
                "recovery", "warn", "grafter",
                "materialized PIT product split fold-owned operative sections out of an attachments wrapper",
                ("preservation", "safety_invariant"), role="observation"),
    FindingSpec("REPLAY.CITED_VERSION_SNAPSHOT_DROP", "replay_products",
                "recovery", "warn", "grafter",
                "a later amending act's stale item-scoped cited-version ancestor snapshot op was "
                "dropped from the materialized-state op stream because the cited act's same-effective "
                "snapshot structurally covers it; recorded as a witness so the legal-state op drop is "
                "never a silent omission",
                ("preservation", "provenance"), role="observation"),
    FindingSpec("REPLAY.EDITORIAL_REPEAL_NOTICE_SUBSTRING", "replay_products",
                "recovery", "warn", "grafter",
                "replay-fold placeholder restoration recognised an existing editorial repeal notice "
                "by a residual 'kumottu' substring scan (no typed marker owns this case yet); "
                "recorded as a witness rather than a silent surface predicate",
                ("parse_witness", "provenance"), role="observation"),
    FindingSpec("REPLAY.MATERIALIZED_PROVISIONS_WRAPPER_PROJECTED", "replay_products",
                "recovery", "warn", "grafter",
                "materialized PIT product projected fold-owned provisions-wrapper children into legal topology",
                ("preservation", "lineage"), role="observation"),
    FindingSpec("APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "pending amendment-of-amendment was composed onto an already-processed target amendment in the same replay chain",
                ("temporal_selection", "preservation"), role="observation"),
    FindingSpec("APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED", "process_muutoslaki",
                "ambiguity", "strict_fail", "grafter",
                "pending amendment-of-amendment named a prior amending instrument/effect that could not be deterministically resolved",
                ("temporal_selection", "provenance", "strictness"), role="obligation"),
    FindingSpec("APPLY.META_REPEAL_EFFECT_RECORDED", "process_muutoslaki",
                "audit", "warn", "grafter",
                "meta-repeal of a prior amending instrument was recorded as an effect-lifecycle relation",
                ("temporal_selection", "provenance"), role="observation"),
    FindingSpec("APPLY.META_REPEAL_EFFECT_UNRESOLVED", "process_muutoslaki",
                "ambiguity", "strict_fail", "grafter",
                "meta-repeal targeted a prior amending instrument/effect but no target effect could be deterministically identified",
                ("temporal_selection", "provenance", "strictness"), role="obligation"),
    FindingSpec("APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED", "effect_lifecycle",
                "ambiguity", "strict_fail", "compile_result",
                "effect lifecycle event names a target instrument or source witness but no executable target effect was resolved",
                ("temporal_selection", "provenance", "strictness"), role="obligation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_SOURCE_CHAIN_GAP", "process_muutoslaki",
                "source_pathology", "warn", "grafter",
                "apply failure suppressed because a recodification source-chain gap already owns the missing target",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_SAME_WAVE_MIGRATION", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "apply failure suppressed because an exact same-wave migration resolves the old-frame target",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_RESTRUCTURE_DEFERRED_TARGET", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "apply failure suppressed because an exact restructure-plan deferred target already owns the old-frame miss",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_TIMELINE_SNAPSHOT", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "apply failure suppressed because an exact same-source timeline snapshot owns the target state",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.FAILED_OPERATION_GOVERNED_BY_PARENT_SNAPSHOT", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "descendant apply failure suppressed because a same-source parent snapshot owns the descendant payload",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.OCCUPANCY_POLICY_GOVERNED_BY_TIMELINE_SNAPSHOT", "process_muutoslaki",
                "recovery", "warn", "grafter",
                "occupancy-policy violation suppressed because source text authorizes replacing a repealed section and an exact same-source timeline snapshot owns the target state",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("PARSE.DUPLICATE_TARGET_OP", "frontend_ops",
                "ambiguity", "warn", "frontend_observations",
                "two or more ops address the same extracted target",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER", "frontend_extraction",
                "recovery", "strict_fail", "frontend_observations",
                "move/renumber clause collapsed to plain duplicate REPLACE ops",
                ("preservation",), role="observation"),
    FindingSpec("PARSE.GRAMMAR_SKIP_PREAMBLE_REPEAL_LIST", "frontend_compile",
                "recovery", "warn", "frontend_compile",
                "PEG extraction was intentionally skipped for a sec1 repeal-list fallback pattern",
                ("parse_witness",), role="observation"),
    FindingSpec("PARSE.REPEAL_RECYCLE_GUARD", "process_muutoslaki",
                "audit", "info", "repeal",
                "kumotaan repeal candidate was excluded because the same source also replaces that target",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("PARSE.PURE_REPEAL_CLAUSE_RECONSTRUCTED", "process_muutoslaki",
                "recovery", "warn", "repeal_clause",
                "a repeal op was reconstructed from a raw repeal preamble clause (fi: kumotaan johtolause) because the typed pipeline produced no op for the target",
                ("parse_witness", "provenance"), role="observation"),
    FindingSpec("PARSE.FALLBACK_OP_FROM_RAW_TEXT", "frontend_extraction",
                "recovery", "warn", "fallback",
                "an executable op was minted from raw johtolause by a heuristic fallback recognizer (no typed parser owned the clause)",
                ("parse_witness", "provenance"), role="observation"),
    FindingSpec("PARSE.COMMENCEMENT_SHAPE_NO_EFFECT", "frontend_extraction",
                "source_pathology", "warn", "unsupported_meta_clause",
                "a commencement/expiry meta clause was recognized but lowered to no executable effect intent",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("FI.COMMENCEMENT_PROVISION_SKIPPED_TARGET_UNSUPPORTED", "frontend_extraction",
                "source_pathology", "warn", "vts",
                "voimaantulosäännös target was parsed but skipped because no safe typed repeal carrier exists",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("FI.COMMENCEMENT_PROVISION_SOURCE_UNREADABLE_OR_EMPTY", "frontend_extraction",
                "source_pathology", "warn", "vts",
                "voimaantulosäännös pre-scan could not inspect source XML or found no inspectable source containers",
                ("parse_witness", "comparative"), role="observation"),
    FindingSpec("PARSE.FUTURE_REPEAL_PRESCAN_DIAGNOSTIC", "frontend_extraction",
                "source_pathology", "warn", "future_repeal_prescan",
                "future-repeal pre-scan could not inspect an amendment source or one best-effort extractor failed",
                ("parse_witness", "comparative"), role="observation"),
    FindingSpec("FI.COMMENCEMENT_PROVISION_PARAGRAPHIZED_REPEAL_FRAGMENT_UNPARSED", "frontend_extraction",
                "source_pathology", "warn", "vts",
                "paragraphized voimaantulosäännös source mentioned the parent statute but no single paragraph yielded a lowerable repeal fragment",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("PARSE.META_CLAUSE_UNSUPPORTED", "frontend_extraction",
                "source_pathology", "warn", "effect_lowering",
                "meta clause was parsed but has no executable temporal/effect carrier in this frontend",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("PARSE.EXTRACTION_EMPTY", "frontend_compile",
                "audit", "warn", "frontend_compile",
                "all frontend extraction paths produced no operations",
                ("parse_witness",), role="observation"),
    FindingSpec("PARSE.UNOWNED_BODY_SECTION", "frontend_compile",
                "recovery", "strict_fail", "frontend_compile",
                "an enacting-formula body fallback accepted some body sections while leaving sibling body sections unowned",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA", "frontend_compile",
                "recovery", "warn", "frontend_compile",
                "an act-wide change formula supplied provision targets through labelled body sections",
                ("parse_witness", "preservation", "strictness"), role="observation"),
    FindingSpec("LOWER.CONTEXT_DEPENDENT_ANCHOR", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target depends on chapter/part scope carry-forward for address resolution",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET", "_compile_group",
                "recovery", "strict_fail", "grafter",
                "scoped section target was rebound to a body-backed unique live section path",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION", "_compile_group",
                "recovery", "warn", "grafter",
                "section INSERT target chapter was corrected from amendment-body chapter placement",
                ("parse_witness", "ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE", "_compile_group",
                "recovery", "strict_fail", "grafter",
                "section REPLACE was lowered to INSERT+MOVE because the source body moved it under a new letter-suffix chapter",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.BODY_CHAPTER_DECLARED_MOVE_REPLACE", "_compile_group",
                "recovery", "warn", "grafter",
                "section REPLACE target was retargeted to the chapter destination declared by an explicit move rider",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION", "_compile_group",
                "recovery", "strict_fail", "grafter",
                "descendant section REPLACE target chapter was corrected from amendment-body chapter placement",
                ("parse_witness", "ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.ITEM_AS_SUBSECTION_TARGET_REWRITE", "_compile_group",
                "recovery", "warn", "grafter",
                "definition-section kohta target was rewritten to a subsection target when source and live structure encode the entry as a flat subsection",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("LOWER.EXPLICIT_CHUNK_SCOPE", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target scope was carried from an explicit source chunk in the preamble (fi: johtolause)",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("LOWER.EXPLICIT_SCOPE_REWRITE", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "explicit scope from source was rewritten using live-tree uniqueness or fallback heuristics",
                ("ambiguity_resolution", "strictness"), role="observation"),
    FindingSpec("LOWER.SCOPE_CARRY_FORWARD", "frontend_scope",
                "recovery", "strict_fail", "frontend_observations",
                "op target requires chapter-scope carry-forward",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("PARSE.FRONTEND_DIAGNOSTIC", "frontend_phase_surface",
                "audit", "info", "frontend_phase_surface",
                "frontend phase diagnostic projected into the governed finding ledger",
                ("parse_witness",), role="observation"),
    # Surface-plane totality sweeps (audit-registry rows SURF-04, SURF-05).
    # Observation-role per-unit totality: the sweep asserts the surface contract
    # and surfaces a residual population; over the real corpus the residual is the
    # expected, correct outcome (an orphan reference / unclassified mention is a
    # real surface fact, not a pipeline fault), so it is non-blocking by design
    # (tag-don't-guess). The synthetic unit-level bite is the guard-liveness drill.
    FindingSpec("DEFINITION.DUPLICATE_DEFINITION", "surface_totality",
                "ambiguity", "warn", "fi_surface_totality",
                "a defined term is bound more than once per (PIT, scope); "
                "exactly one definition site per scope is the totality contract",
                ("parse_witness", "ambiguity_resolution"), role="observation"),
    FindingSpec("DEFINITION.ORPHAN_DEFINITION_REFERENCE", "surface_totality",
                "source_pathology", "warn", "fi_surface_totality",
                "a reference to a defined term has no resolvable definition in "
                "scope (used before / without an in-scope definition site)",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("REFERENCE.UNCLASSIFIED_REFERENCE", "surface_totality",
                "violation", "warn", "fi_surface_totality",
                "an emitted ReferenceMention carries a cite_confidence outside the "
                "closed classification set (resolved/statute_only/ambiguous/open/"
                "broken/unsupported); the closed set was silently widened",
                ("parse_witness", "safety_invariant"), role="observation"),
    # Surface-plane token-realization + entity-handle totality sweeps
    # (audit-registry rows SURF-01, SURF-02, SURF-07). Same observation-role
    # per-unit-totality disposition as SURF-04/05: the sweep asserts the surface
    # contract and surfaces a residual population; over the real corpus the
    # residual is the expected, correct outcome (a leaked token / an orphan
    # entity node is a real surface fact, surfaced — not a pipeline crash), so it
    # is non-blocking by design (tag-don't-guess). The synthetic unit-level bite
    # is the guard-liveness drill.
    FindingSpec("SURFACE.TOKEN_REALIZATION_GAP", "surface_totality",
                "violation", "warn", "fi_surface_totality",
                "a source token reached NO typed destination bucket: the four "
                "Pro-D2 partition classes (owned/benign_uninterpreted/"
                "typed_residual/unowned_violation) do not sum to total_tokens — a "
                "silently-dropped (or double-counted) token",
                ("parse_witness", "safety_invariant"), role="observation"),
    FindingSpec("WAIST.HANDOFF_PARITY_SOURCE_TO_TOKEN", "surface_totality",
                "violation", "warn", "fi_surface_totality",
                "source->token handoff parity break: the source span consumed by "
                "tokenization (total_tokens) does not equal "
                "owned+typed_residual+benign+violation (the waist-edge form of "
                "SURFACE.TOKEN_REALIZATION_GAP)",
                ("parse_witness", "safety_invariant"), role="observation"),
    FindingSpec("SURFACE.ORPHAN_ENTITY_NODE", "surface_totality",
                "source_pathology", "warn", "fi_surface_totality",
                "a surface entity-handle node (legal_work_entity/term_symbol_entity"
                "/legal_address_entity/actor_entity) appears in no edge endpoint — "
                "an entity node with no covering edge, surfaced not left uncovered",
                ("parse_witness", "preservation"), role="observation"),
    # Disjoint-window scheduling totality sweep (audit-registry rows
    # SCHED-01/02/03). Read-only per-window totality over the finished replay
    # output (ReplayProducts.temporal_events + timelines): every temporary
    # legal-effect window is materialized as a version interval, carried as a
    # typed residual, or surfaced here — never silently dropped. Same
    # observation-role, non-blocking disposition as the SURF-* sweeps: over a
    # real corpus a disjoint window the document-order fold did not materialize
    # is a REAL legal fact (a temporary gap-filler whose slot a deferred-
    # commencement twin holds), surfaced — not a pipeline fault — so blocking
    # would contradict tag-don't-guess. The synthetic unit-level bite is the
    # guard-liveness fire-drill. The apply-time discovery twin
    # (APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT -> TEMPORAL.WINDOW_UNMATERIALIZED)
    # is the fold-time repair lane; this is the complementary read-only audit
    # over the FINAL output.
    FindingSpec("SCHED.WINDOW_UNMATERIALIZED", "schedule_window_totality",
                "source_pathology", "warn", "fi_schedule_window_totality",
                "a temporary legal-effect window (effective..expires) on the "
                "replay output is NOT materialized as a version interval and is "
                "NOT carried as a typed residual: a disjoint window the document-"
                "order fold left unmaterialized, surfaced not silently dropped",
                ("temporal_selection", "preservation"), role="observation"),
    # SCOPE-01/02 scope-lattice totality (read-only sweep over the FINAL
    # timelines). The full disjointness lattice is PART (missing carrier: no
    # populated structured scope predicate on FI selection rows); the CHECKABLE
    # part is the precedence-rail residual — two co-effective distinct-content
    # rows that share the precedence rank key (lex posterior does NOT separate
    # them) and carry no disjoint scope predicate, so the winner depends on list
    # order. Non-blocking (a real co-effective tie is a source fact to surface,
    # not a crash); the apply/selection engine's own ambiguous_missing_scope arm
    # is the complementary live-query lane. Over the FI corpus it stands at 0.
    FindingSpec("SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE", "scope_lattice_totality",
                "source_pathology", "warn", "fi_scope_lattice_totality",
                "two co-effective versions at one address share the precedence-rail "
                "rank key (effective/enacted/source) with distinct content and NO "
                "disjoint scope predicate admits the overlap: the selection winner "
                "would depend on list order, not a proved legal precedence or a "
                "scope distinction, surfaced not silently order-resolved",
                ("temporal_selection", "ambiguity_resolution"), role="observation"),
    # Rank-17 silent-drop closure (canonical_op plane). The clause_ast ingress
    # seam (fi extract_legal_ops_from_parse_result) and the legacy lower_surface
    # bridge previously dropped unsupported clause/surface nodes
    # (MetaClause/ItemShiftClause/NamedRowClause; SurfaceMetaClause/
    # SurfaceTextAmend/SurfaceValiotsikkoRef/unresolved SurfaceBackRef) by
    # returning None/[] with no receipt. These observations make every such
    # drop visible. Non-blocking by design: routing through the diagnostic twin
    # must not change which ops replay — only add a receipt for the already-
    # dropped node — so bench stays flat.
    FindingSpec("LOWER.CLAUSE_AST_NODE_UNSUPPORTED_GENERIC_LOWERING", "frontend_compile",
                "source_pathology", "warn", "extract_legal_ops_from_parse_result",
                "clause AST node has no generic LegalOperation lowering at the ingress seam",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("LOWER.SURFACE_NODE_UNLOWERABLE_TO_PARSED_OP", "lower_surface",
                "source_pathology", "warn", "lower_surface_clause_to_parsed_ops",
                "surface node kind has no ParsedOp representation in the legacy lowering bridge",
                ("parse_witness", "preservation"), role="observation"),
    # Non-blocking observations. The sole producer
    # (tools/consistency.py:ConsistencyResult.to_phase_result) emits these as
    # role=observation/blocking=False and is not wired into the compile/replay
    # pipeline, so registering them at hard_fail made them blocking guards that
    # could never be driven into their firing state from production. The
    # enforcement is downgraded to warn to match the only real producer; the
    # family stays "violation" (an internal-incoherence signal) as a
    # non-blocking observation, the same shape as APPLY.OCCUPANCY_POLICY_VIOLATION
    # and APPLY.INTENT_COMPAT_MISMATCH.
    FindingSpec("TIME.SECTION_NO_TIMELINE", "check_consistency",
                "violation", "warn", "consistency",
                "section present in PIT-materialized replay state has no corresponding timeline entry",
                ("safety_invariant", "comparative"), role="observation"),
    FindingSpec("TIME.TIMELINE_NO_SECTION", "check_consistency",
                "violation", "warn", "consistency",
                "timeline entry has no corresponding section in PIT-materialized replay state",
                ("safety_invariant", "comparative"), role="observation"),
    FindingSpec("TIME.CONTENT_DRIFT", "check_consistency",
                "violation", "warn", "consistency",
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
    FindingSpec("merge_invariant_violation", "merge",
                "audit", "warn", "merge",
                "typed merge invariant contract violated after section merge",
                ("safety_invariant", "preservation"), role="observation"),
    FindingSpec("ELAB.REGISTRY_PIPELINE", "recover_uncovered_body_ops",
                "audit", "info", "audit",
                "named elaboration pipeline executed under registry rule ids",
                ("strictness", "preservation"), role="observation"),
    FindingSpec("ELAB.REGISTRY_STAGE", "run_registered_elaboration_stage",
                "audit", "info", "audit",
                "one registry-owned elaboration stage executed",
                ("strictness", "preservation"), role="observation"),
    FindingSpec("REPLAY.TRANSITION_DETECTOR", "project_transition_detector_findings",
                "audit", "warn", "audit",
                "transition detector flagged sparse broad snapshot or shadow conflict",
                ("safety_invariant", "preservation"), role="observation"),
    FindingSpec("timeline_invariant_violation", "timeline_invariants",
                "audit", "warn", "timeline",
                "replay timeline invariant violated at materialized PIT",
                ("safety_invariant", "preservation"), role="observation"),
    FindingSpec("label_sequence_gap_warning", "replay_lints",
                "audit", "warn", "replay_lints",
                "replay output contains a suspicious legal-unit label sequence gap",
                ("comparative", "preservation", "safety_invariant"), role="observation"),
    FindingSpec("uk_replay_text_match_punctuation_space_normalized", "UKReplayExecutor.apply_op",
                "recovery", "warn", "uk_legislation",
                "UK replay applied a text patch after normalizing citation punctuation spacing in the effect-feed match text",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("uk_replay_contextual_word_anchor_kind_normalized", "UKReplayExecutor.apply_op",
                "recovery", "warn", "uk_legislation",
                "UK replay applied a contextual word patch after normalizing the source anchor kind to the parsed child kind",
                ("parse_witness", "preservation"), role="observation"),
    FindingSpec("uk_replay_text_patch_preimage_drift", "UKReplayExecutor.apply_op",
                "source_pathology", "strict_fail", "uk_legislation",
                "UK replay skipped a text patch because an earlier same-target text patch changed the target preimage",
                ("parse_witness", "preservation", "strictness"), role="observation"),
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
    FindingSpec("PARSE.FRONTEND_BLOCKING_DIAGNOSTIC", "frontend_phase_surface",
                "ambiguity", "strict_fail", "frontend_phase_surface",
                "frontend phase diagnostic blocks promotion until resolved",
                ("parse_witness", "strictness"), role="obligation"),
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
    FindingSpec("APPLY.SOURCE_CORRECTION_DIGEST_DRIFT", "process_muutoslaki",
                "source_pathology", "info", "grafter",
                "source bytes changed under correction with no owning patch op",
                ("lineage",), role="observation"),
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
    # Fixed-term whole-law validity (määräaikainen laki) — statute-level bound.
    FindingSpec("TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "whole-law fixed-term expiry clause recognised but its validity date could not be parsed; a live answer would be unsafe",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "two conflicting whole-law fixed-term bounds share an effective date; the governing validity end is ambiguous",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "anaphoric year-end expiry ('sanotun vuoden loppuun') has multiple plausible same-sentence antecedent years; the bound must not be guessed",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT", "fixed_term_expiry",
                "audit", "info", "fixed_term_expiry",
                "expiry-shaped clause without a parseable date or commencement marker was suppressed as a body-text false positive (audit trail for the commencement-context guard)",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "duration-form whole-law validity outside the pinned 150/1930 §3 year/month rule's input domain (non-commencement anchor, unsupported unit, or unparseable period); the end is never computed ad hoc",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "duration-form whole-law validity recognised but the commencement it runs from is unresolved (decree-set, unstated, or ambiguous); the pinned arithmetic authority cannot supply missing commencement facts",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.EVENT_BOUND_RESOLVER_MISSING", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "whole-law validity ends at a statute-gazette-discernible (fi: säädöskokoelma) event (another instrument's entry into force); the cross-document resolver is not yet implemented",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "whole-law validity ends at a substantive event not discernible from the statute gazette (fi: säädöskokoelma); outside the blessed event-bound drafting pattern, never guessed",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.SOURCE_IMPOSSIBLE_DATE", "fixed_term_expiry",
                "ambiguity", "strict_fail", "fixed_term_expiry",
                "source states a calendar-impossible validity end date; a candidate normalization is recorded but never used without a statute-gazette (fi: säädöskokoelma) correction or manual attestation",
                ("temporal_selection", "strictness"), role="obligation"),
    FindingSpec("TEMPORAL.DECREE_SET_COMMENCEMENT_UNRESOLVED", "fixed_term_expiry",
                "audit", "info", "fixed_term_expiry",
                "commencement is decree-set (asetuksella säädettävänä ajankohtana); recorded as a commencement-resolution frontier, not a whole-law expiry bound",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.START_ONLY_NOT_EXPIRY_BOUND", "fixed_term_expiry",
                "audit", "info", "fixed_term_expiry",
                "start-only validity statement (fi: voimassa N päivästä ...) with no end marker; a commencement fact, not an expiry bound",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED", "fixed_term_expiry",
                "audit", "info", "fixed_term_expiry",
                "validity-shaped (fi: voimassa) text does not predicate validity of the act itself (referential incorporation, qualifier, or another subject); suppressed as a non-candidate with audit trail",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED", "fixed_term_expiry",
                "source_pathology", "warn", "fixed_term_expiry",
                "chapter/section-scoped fixed-term expiry detected; v1 does not lift scoped bounds into statute-level validity",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.POSSIBLE_EXPIRY_TEXT_UNSUPPORTED", "fixed_term_expiry",
                "audit", "info", "fixed_term_expiry",
                "weak textual hint of a validity period without a recognised whole-law expiry clause",
                ("temporal_selection",), role="observation"),
    FindingSpec("TEMPORAL.FIXED_TERM_LATE_EXTENSION_GAP", "fixed_term_expiry",
                "audit", "warn", "fixed_term_expiry",
                "fixed-term extension took effect only after the prior bound had lapsed, creating a deterministic gap-then-revival period",
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
    FindingSpec("COVERAGE.PAYLOAD_REALIZATION_GAP", "post_apply_payload_realization",
                "audit", "warn", "replay",
                "source amendment payload text was not realized in the immediate post-amendment folded state",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("COVERAGE.PAYLOAD_REALIZATION_SHADOWED_BY_SAME_AMENDMENT", "post_apply_payload_realization",
                "audit", "info", "replay",
                "post-amendment realization audit suppressed an earlier payload because a later same-amendment replace superseded its target",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("COVERAGE.PAYLOAD_REALIZATION_SUPERSEDED_BY_LATER_AMENDMENT", "post_apply_payload_realization",
                "audit", "info", "replay",
                "post-amendment realization audit classified missing final-product payload text as superseded by a later applied amendment",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("COVERAGE.PAYLOAD_REALIZATION_EXPIRED_SOURCE_WINDOW", "post_apply_payload_realization",
                "audit", "info", "replay",
                "post-amendment realization audit classified missing materialized payload text as an applied temporary source window that expired before the query horizon",
                ("comparative", "preservation", "temporal_selection"), role="observation"),
    FindingSpec("COVERAGE.PAYLOAD_REALIZATION_BLOCKED_BY_APPLY_FAILURE", "post_apply_payload_realization",
                "audit", "info", "replay",
                "post-amendment realization audit classified missing payload text as derivative of a failed apply operation",
                ("comparative", "preservation"), role="observation"),
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
    FindingSpec("TIME.RESOLVED_CONTINGENT_EFFECTIVE_DATE", "timeline",
                "audit", "warn", "compile_result",
                "contingent commencement was resolved from a separate commencement-law witness",
                ("temporal_selection", "provenance"), role="observation"),
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
    FindingSpec("APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP", "apply",
                "violation", "hard_fail", "apply_resolved_op",
                "per-op mutation-boundary REJECT: an op's changed paths are not a subset of "
                "its target plus declared migration/recovery/editorial-projection boundary",
                ("safety_invariant",), role="violation"),
    FindingSpec("APPLY.MUTATION_BOUNDARY_FINDING_AT_OP", "apply",
                "violation", "warn", "apply_resolved_op",
                "quirks-mode accounting for a per-op mutation-boundary escape "
                "(same condition as APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP, recorded not blocked)",
                ("safety_invariant",), role="observation"),
    FindingSpec("APPLY.OCCUPANCY_TRANSITION_BLOCKED", "apply",
                "violation", "hard_fail", "apply_resolved_op",
                "strict-mode occupancy gate: a state-mutating op attempted an invalid "
                "(action, from->to) occupancy transition",
                ("safety_invariant",), role="violation"),
    FindingSpec("EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED", "apply",
                "violation", "hard_fail", "apply_resolved_op",
                "a state-mutating op landed without resolving an ExecutionAuthorization "
                "(rule_id + required proofs) under strict mode",
                ("safety_invariant",), role="violation"),
    # AM-01 (strict-blocking): a Recovered (guessed) op rejected at the typed
    # acceptance boundary under a StrictProfile that forbids its recovery surface.
    FindingSpec("APPLY.RECOVERED_OP_REJECTED_IN_STRICT", "apply",
                "recovery", "hard_fail", "apply_resolved_op",
                "typed acceptance gate: a strict profile rejected a Recovered (guessed) "
                "op via mode_for/admits over its OpProvenance, so a certified/strict "
                "claim rests only on grammar-recognized (Parsed) ops",
                ("safety_invariant", "provenance"), role="violation"),
    # --- Wave-2 apply-authority closure: per-op + whole-tree sweeps ---
    # LS-07 (strict-blocking): a descendant-granularity op whose resolved address
    # carries no descendant slot would overwrite its host whole-unit.
    FindingSpec("APPLY.GRANULARITY_ESCALATION_AT_OP", "apply",
                "violation", "hard_fail", "apply_op_closure_sweeps",
                "strict granularity gate: a descendant-granularity op resolved to its "
                "host whole-unit with no descendant slot, escalating to overwrite the host",
                ("safety_invariant",), role="violation"),
    # EV-06 (strict-blocking): an ExecutionAuthorization citing an unknown policy id.
    FindingSpec("EVID.UNKNOWN_ATTESTATION_POLICY", "apply",
                "violation", "hard_fail", "apply_op_closure_sweeps",
                "an ExecutionAuthorization reaching apply cites an evidence policy id "
                "not present in the known/pinned policy set (attestation-policy gap)",
                ("safety_invariant", "provenance"), role="violation"),
    # FW-01 (whole-tree closure): a surface-origin node minting replay authority.
    FindingSpec("FW.SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED", "apply",
                "violation", "hard_fail", "apply_tree_closure",
                "whole-tree closure: a surface-origin node in the materialized replay "
                "tree minted replay authority with no typed ExecutionAuthorization promotion",
                ("safety_invariant", "provenance"), role="violation"),
    # OV-01 (whole-tree closure): a replay-authorized overlay node with no promotion.
    FindingSpec("OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION", "apply",
                "violation", "hard_fail", "apply_tree_closure",
                "whole-tree closure: an overlay-origin node is replay-authorized with no "
                "typed promotion event + witness",
                ("safety_invariant", "provenance"), role="violation"),
    # OV-02 (whole-tree closure): an overlay promotion that does not cite provenance.
    FindingSpec("OVERLAY.PROMOTION_WITNESS_INCOMPLETE", "apply",
                "violation", "hard_fail", "apply_tree_closure",
                "whole-tree closure: an overlay-origin promotion does not cite "
                "provider_id+model_version OR registry_version+entry_id",
                ("safety_invariant", "provenance"), role="violation"),
    # LS-05 (non-blocking observation): a landed op with no scope-resolution witness.
    FindingSpec("APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP", "apply",
                "audit", "warn", "apply_op_closure_sweeps",
                "scope-confidence totality: a state-mutating op landed with no typed "
                "ScopeConfidence witness recording how its scope was obtained",
                ("provenance", "safety_invariant"), role="observation"),
    # LS-06 (non-blocking observation): an unwitnessed verb conversion.
    FindingSpec("LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP", "apply",
                "recovery", "warn", "apply_op_closure_sweeps",
                "action-family conversion totality: a landed op's resolved action family "
                "differs from its parsed action with no named conversion witness",
                ("parse_witness", "preservation"), role="observation"),
    # LS-09 (non-blocking observation closure): a parent-container payload smuggle.
    FindingSpec("APPLY.PAYLOAD_SMUGGLING_AT_OP", "apply",
                "audit", "warn", "apply_op_closure_sweeps",
                "payload-smuggling closure: a descendant-claiming op resolved to its bare "
                "host unit with no descendant step (could touch the unclaimed parent container)",
                ("safety_invariant", "preservation"), role="observation"),
    # LS-10 (non-blocking observation closure): an unstated migration / address rekey.
    FindingSpec("APPLY.UNSTATED_MIGRATION_AT_OP", "apply",
                "audit", "warn", "apply_op_closure_sweeps",
                "unstated-migration closure: a target address-key delta (nominal -> resolved) "
                "with no migration/lineage event or typed rekey witness",
                ("migration", "lineage"), role="observation"),
    # --- Promotion-chain integrity wave (CHAIN-/PROMOTE- families, §0) ---
    # PROMOTE-02 (strict-blocking): an ExecutionAuthorization gating an op whose
    # derived identity does not equal the authorization's bound rule_id.
    FindingSpec("PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH", "apply",
                "violation", "hard_fail", "apply_promotion_chain",
                "authorization scope-match: an ExecutionAuthorization gating a state-mutating "
                "op is bound to a different op's derived identity (rule_id mismatch); authority "
                "minted for one op may not gate another (smuggled authority, §1.5 analogue)",
                ("safety_invariant", "provenance"), role="violation"),
    # CHAIN-01 (strict-blocking): a mutating op's promotion chain is missing a
    # materialized link (incomplete over the links that exist as typed carriers).
    FindingSpec("CHAIN.PROMOTION_CHAIN_INCOMPLETE", "apply",
                "violation", "hard_fail", "apply_promotion_chain",
                "promotion-chain completeness: a state-mutating op's promotion chain is missing "
                "a materialized link (every materialized source-witness -> ... -> agreement-row "
                "link must be present)",
                ("safety_invariant", "provenance"), role="violation"),
    # CHAIN-02 (strict-blocking): a link reached with an absent materialized
    # predecessor — authority by accumulation rather than by climbing.
    FindingSpec("CHAIN.AUTHORITY_BY_ACCUMULATION", "apply",
                "violation", "hard_fail", "apply_promotion_chain",
                "promotion-chain monotonicity: a chain link was reached with an absent "
                "materialized predecessor — authority acquired by accumulation, not by climbing "
                "the boundary (never by accumulation)",
                ("safety_invariant", "provenance"), role="violation"),
    # PROMOTE-01 (strict-blocking): a downstream link standing on a retracted
    # predecessor without reopen/taint (immediate one-hop arm).
    FindingSpec("PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION", "apply",
                "violation", "hard_fail", "apply_promotion_chain",
                "retraction down-chain propagation: a retracted promotion-chain link has a "
                "downstream link left standing without reopen/taint (the whole sub-chain below "
                "a retracted link must be re-opened, not just the immediate consumer)",
                ("safety_invariant", "provenance"), role="violation"),
    FindingSpec("REPLAY_UNKNOWN_MUTATION_OUTCOME", "apply",
                "violation", "hard_fail", "grafter",
                "replay mutation event carried an outcome label outside the registered outcome sets",
                ("safety_invariant",), role="violation"),
    FindingSpec("RUNTIME.VIOLATION", "phase_result",
                "violation", "hard_fail", "phase_result",
                "generic runtime contract violation projected through the finding ledger",
                ("safety_invariant",), role="violation"),
    FindingSpec("LINEAGE.CYCLE", "lineage_ledger_build",
                "violation", "hard_fail", "timeline_lineage",
                "migration/lineage segments form a cycle (an eId migrates into its own ancestry); "
                "non-terminating materialization / repeated-PIT hash drift; detail.cycle carries the address witness",
                ("lineage", "safety_invariant"), role="violation"),
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
    FindingSpec("BASE_INTRO_LIST_TAIL_MOMENT_SPLIT", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization split multi-moment tail prose after an intro-list moment into peer subsections",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_SECTION_ITEM_SUBSECTION_FOLD", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization folded section-level item continuations misencoded as subsections into their list-bearing moment",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_TABLE_NOTE_SUBSECTION_FOLD", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization folded synthetic table-note subsection wrappers into the table-bearing moment",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization promoted dotted-number paragraph rows into peer subsection moments",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization split an unnumbered subparagraph payload out into a peer subsection moment",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_TABLE_CONTINUATION_SUBSECTION_MERGE", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization merged a table-bearing continuation subsection back into its interrupted first moment",
                ("comparative", "preservation"), role="observation"),
    FindingSpec("BASE_TABLE_CONTINUATION_HEADER_REPAIR", "source_normalize",
                "recovery", "info", "statute",
                "base statute source normalization repaired a malformed table continuation header row after merging an interrupted moment",
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
    FindingSpec("APPLY.UNCOVERED_BODY_CANDIDATE_AUDIT", "grafter_uncovered",
                "audit", "info", "grafter_uncovered",
                "uncovered-body recovery recorded a per-candidate disposition audit row",
                ("preservation", "parse_witness"), role="observation"),
    FindingSpec("APPLY.RESOLVED_OP_AUDIT", "replay_apply",
                "audit", "info", "apply_resolved_op",
                "replay recorded whether a resolved operation applied, failed, or required no apply pass",
                ("preservation", "parse_witness"), role="observation"),
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
    FindingSpec("APPLY.UNCOVERED_BODY_PREAMBLE_GUARD", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section because preamble (fi: johtolause) scope did not justify the label",
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
    FindingSpec("APPLY.UNCOVERED_BODY_OMISSION_MERGE_MISSING_SCOPE", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section-level omission merge because no parsed scoped target owned the sparse payload",
                ("preservation", "strictness"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_SPECIAL_SUBPROVISION_SCOPE", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section-level omission merge because the johtolause named only a descriptor-scoped sub-provision",
                ("preservation", "strictness", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_LABEL_COLLISION", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned by PEG under the same label in another chapter",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_SAME_CHAPTER_OWNED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section already owned by PEG under the same label in the same chapter",
                ("preservation",), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_DESCENDANT_LABEL_COLLISION", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section whose descendant is already owned by PEG under the same label in another chapter",
                ("preservation", "ambiguity_resolution"), role="observation"),
    FindingSpec("APPLY.UNCOVERED_BODY_PEG_DESCENDANT_SAME_CHAPTER_OWNED", "grafter_uncovered",
                "recovery", "warn", "grafter_uncovered",
                "uncovered-body recovery skipped a section whose descendant is already owned by PEG in the same chapter",
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
    # v3 attestation-resolution findings (§8.1 of UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md)
    # These are gated by StrictProfile channel fields via _PROFILE_GATES.
    FindingSpec("ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unresolved inline statute citation resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unresolved EU act reference resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unresolved committee report reference resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unresolved pool address resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unclassified modal surface resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unlocated source-labeled purpose resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("APPLY.REF_TARGET_CORRECTED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "ref target corrected by provenance attestation",
                ("strictness",), role="barrier"),
    FindingSpec("APPLY.METADATA_ATTRIBUTION_CORRECTED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "metadata attribution corrected by provenance attestation",
                ("strictness",), role="barrier"),
    FindingSpec("ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "target selection requirement resolved via provenance attestation",
                ("ambiguity_resolution", "strictness"), role="barrier"),
    FindingSpec("PARSE.PREAMBLE_CLAUSE_FAILED.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "preamble (fi: johtolause) parse failure resolved via provenance attestation",
                ("parse_witness", "strictness"), role="barrier"),
    FindingSpec("ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unclassified target ambiguity resolved via provenance attestation",
                ("ambiguity_resolution", "strictness"), role="barrier"),
    FindingSpec("LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unclassified provision migration resolved via provenance attestation",
                ("lineage", "strictness"), role="barrier"),
    FindingSpec("COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION", "evidence_kernel",
                "recovery", "strict_fail", "evidence_kernel",
                "unadjudicated oracle divergence resolved via provenance attestation",
                ("comparative", "strictness"), role="barrier"),
    # Generic evidence-audit findings (§8.2)
    FindingSpec("EVID.ATTESTATION_POLICY_SATISFIED", "evidence_kernel",
                "audit", "info", "evidence_kernel",
                "evidence policy predicate satisfied for assertion",
                ("strictness",), role="observation"),
    FindingSpec("EVID.ATTESTATION_POLICY_FAILED", "evidence_kernel",
                "audit", "warn", "evidence_kernel",
                "evidence policy predicate not satisfied for assertion",
                ("strictness",), role="observation"),
    FindingSpec("EVID.ASSERTION_RETRACTED_AFTER_CONSUMPTION", "evidence_kernel",
                "audit", "warn", "evidence_kernel",
                "assertion was retracted after it was consumed by a build",
                ("strictness", "negative"), role="observation"),
    FindingSpec("EVID.PURPOSE_INDEX_REQUIRED", "evidence_kernel",
                "audit", "warn", "evidence_kernel",
                "purpose index is required by the active build profile",
                ("strictness",), role="observation"),
    FindingSpec("EVID.NEGATIVE_EVIDENCE_BOUND_REACHED", "evidence_kernel",
                "audit", "info", "evidence_kernel",
                "bounded negative-evidence search completed without finding a counterexample",
                ("negative",), role="observation"),
    # EV-03 (totality, observation): a residual COUNTED in a stage's coverage
    # violation class but absent from that stage's committed residual ledger (or the
    # dual: a committed blocking residual the coverage account never counted) — an
    # uncertainty recorded then silently dropped across the per-stage account fold.
    FindingSpec("EVID.RESIDUAL_LEDGER_NONMONOTONE", "certificate_dossier",
                "audit", "warn", "stage_residual_monotonicity",
                "a residual counted in a stage's coverage violation class is absent "
                "from that stage's committed residual ledger (or vice versa): a "
                "non-monotone per-stage residual account (no silent loss)",
                ("provenance", "preservation"), role="observation"),
    # EV-07 (totality, observation): a source-text-failure residual
    # (unowned_violation / typed_residual) carrying no verbatim offending snippet —
    # an opaque diagnostic about unhandled source text, not self-evidencing.
    FindingSpec("EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING", "evidence_kernel",
                "audit", "warn", "diagnostic_self_evidencing",
                "a source-text-failure residual embeds no verbatim offending snippet "
                "(empty text field): an opaque diagnostic, not self-evidencing",
                ("provenance",), role="observation"),
    FindingSpec("PARSE.FRONTEND_INTERNAL_ERROR", "frontend_phase_surface",
                "violation", "hard_fail", "frontend_phase_surface",
                "frontend phase diagnostic reports an internal compiler error",
                ("safety_invariant",), role="violation"),
    # --- XML ingest token-structure observations (self-contained block) ---
    # Non-blocking observations that witness what the XML→IR ingest boundary
    # does at the token_structure plane: dropping a source child element,
    # encountering an unknown XML tag, assigning a positional label, or
    # mutating tree shape with a structural-repair heuristic. These turn
    # previously silent drops/guesses into witnessed ones reachable from
    # IRStatute.metadata["xml_ingest_observations"]. They are deliberately
    # non-blocking (role=observation) so they do not trip the guard-liveness
    # ratchet without a dedicated fire-drill.
    FindingSpec("SCAN.XML_INGEST_DROPPED_CHILD", "xml_ingest",
                "source_pathology", "warn", "xml_ingest",
                "a source XML child element was dropped during XML→IR ingest "
                "because its tag is not a known structural/leaf/table kind and "
                "its text collapsed to empty; detail.tag/detail.snippet witness it",
                ("parse_witness", "comparative"), role="observation"),
    FindingSpec("SCAN.XML_INGEST_UNKNOWN_TAG", "xml_ingest",
                "source_pathology", "warn", "xml_ingest",
                "an XML tag encountered during ingest has no mapped IRNodeKind; "
                "detail.tag carries the offending tag for a class→kind mapping fix",
                ("parse_witness",), role="observation"),
    FindingSpec("SCAN.XML_INGEST_POSITIONAL_LABEL", "xml_ingest",
                "recovery", "warn", "xml_ingest",
                "an unlabelled subsection/paragraph was assigned a positional label "
                "by enumeration order during ingest (identity is positional, not "
                "intrinsic); detail.kind/detail.assigned_label witness the guess",
                ("ambiguity_resolution",), role="observation"),
    FindingSpec("SCAN.XML_INGEST_STRUCTURAL_REPAIR", "xml_ingest",
                "recovery", "warn", "xml_ingest",
                "an ingest structural-repair heuristic re-parented or merged tree "
                "shape on a regex/letter-sequence guess; detail.repair names the rule",
                ("preservation", "ambiguity_resolution"), role="observation"),
    # XP-03 — op-coverage totality (runtime parity arm). At the canonical-op
    # lowering waist (#6) every candidate operation MUST lower to exactly one
    # canonical op (coverage.owned) OR a typed candidate-effect residual
    # (coverage.violation), never silently dropped — i.e. the candidate-op
    # CoverageCertificate must be a partition (owned + violation == total under
    # totality_claimed). The partition holds BY CONSTRUCTION today (the lowering
    # seam computes total = emitted + rejected; see compile_amendment.build_canonical_op_stage),
    # so this code is a defensive RUNTIME pin: a candidate op that neither lowered
    # nor residualized would break is_partition() and surface here as a typed
    # residual. NON-BLOCKING (role=observation): a real uncovered op should be
    # SURFACED for triage, not silently block the corpus; the population is the
    # finding, not a hard fail.
    FindingSpec("CANONICAL_OP.OP_COVERAGE_GAP", "build_canonical_op_stage",
                "violation", "warn", "compile_amendment",
                "a candidate operation neither lowered to a canonical op nor "
                "residualized at the canonical-op lowering waist: the candidate-op "
                "coverage account is not a partition (owned + violation != total); "
                "detail carries the owned/violation/total counts witnessing the gap",
                ("parse_witness", "preservation"), role="observation"),
    # KNOW-01 (source-monotonicity): a single source locator was observed
    # carrying two DISTINCT content digests — i.e. the external witness behind
    # one stable locator changed in place. The source plane must be append-only:
    # a re-publish is a NEW manifestation under a new locator/digest, never an
    # in-place byte swap behind the same locator. Detail carries the locator and
    # the conflicting digests so the violation is self-evidencing.
    FindingSpec("EVID.SOURCE_LOCATOR_DIGEST_CONFLICT", "know_invariants",
                "external_drift", "warn", "source_witness",
                "one source locator carries two distinct content digests across "
                "observations: an in-place byte mutation of an external witness "
                "(source plane must be append-only — a re-publish is a new "
                "manifestation, never a silent overwrite behind the same locator)",
                ("provenance",), role="observation"),
    # KNOW-03 (lost source -> UNCHECKABLE, never INVALID): a source record names
    # an artifact whose bytes/digest are NOT resolvable (referenced-only, lost,
    # digest-unknown). Such a record is UNCHECKABLE for monotonicity, NOT a
    # violation — the honest verdict for absent bytes is "cannot check", never
    # "invalid". Detail carries the locator and the availability classification.
    FindingSpec("EVID.SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST", "know_invariants",
                "audit", "info", "source_witness",
                "a source record names an artifact with no resolvable content "
                "digest (referenced-only/lost/digest-unknown): UNCHECKABLE for "
                "source-monotonicity, never INVALID (absent bytes => cannot "
                "check, not a violation)",
                ("provenance", "negative"), role="observation"),
    # D8 OVERLAY.DEFAULT_REPLAY_AUTHORIZED_FALSE (audit_impl_D8): AGENTS.md §2.10
    # declares a surface/overlay node defaults to replay_authorized=False; a node
    # tagged as originating from the overlay plane may mutate legal state ONLY
    # through a typed ExecutionAuthorization promotion event. An overlay-tagged
    # node carrying replay_authorized=True WITHOUT a matching promotion breaches
    # the deterministic firewall; this audit surfaces it as a blocking
    # obligation. The audit module is landed; wire into compile_timelines is
    # staged as follow-up (parallel to D7's wire-then-promote discipline).
    FindingSpec("OVERLAY.UNAUTHORIZED_PROMOTION", "compile-timelines",
                "violation", "strict_fail", "overlay_default_replay_authorized_false_audit",
                "an overlay-tagged IRNode carries replay_authorized=True but has no typed "
                "ExecutionAuthorization promotion event with rule_id and witness (AGENTS.md §2.10)",
                ("safety_invariant", "provenance"), role="obligation"),
    # D11 EVID.AUTHORITY_SOURCE_EXCLUDES_OBSERVATION_KINDS (audit_impl_D11): the
    # evidence plane may explain authority but never become it (AGENTS.md §2.10).
    # An observation-role finding kind appearing in the apply-path authority
    # source set voids the ExecutionAuthorization — a breach of the evidence->
    # authority firewall, not a soft mismatch. hard_fail: strict mode aborts the
    # apply pass; quirks emits the finding non-blocking and proceeds (over-
    # retention-safe direction per §0).
    FindingSpec("EVID.OBSERVATION_PROMOTED_TO_AUTHORITY", "apply_authority_audit",
                "violation", "hard_fail", "execution_authorization",
                "apply-path authority source set contained a finding whose registry role is "
                "'observation', breaching the §2.10 evidence->authority firewall",
                ("safety_invariant", "strictness"), role="violation"),
    # D12 NOTE: ``EVID.UNKNOWN_ATTESTATION_POLICY`` is ALREADY registered above
    # by the EV-06 apply-authority closure wave (phase=apply, owner=
    # apply_op_closure_sweeps, family=violation, hard_fail, role=violation) at
    # line 1128. The D12 spec wanted a sibling bundle-emission audit in
    # ``evidence_policy.audit_attestation_policy_gap`` that EMITS THE SAME
    # code from a different evidence-plane surface (certificate-bundle
    # emission at tools/certificate_bundle.py:~2404). To avoid silently
    # overriding the existing emitter's FindingSpec metadata (which would
    # invalidate EV-06's existing fire-drill metadata: phase=apply,
    # owner=apply_op_closure_sweeps), the new helper emits the SAME code and
    # relies on the EXISTING registry row for FindingSpec metadata. There is
    # no second FindingSpec row here for D12 — the single canonical registry
    # row is the EV-06 apply-path emitter's, shared by the bundle-emission
    # sweep.
    # D7 / LS-23 COMMENCEMENT.EFFECT_TOTALITY (audit_impl_D7): every LegalOperation
    # reaching compile-timelines is temporally authorized — a commence/revive
    # TemporalEvent matches via group_id + scope, OR the op carries an explicit
    # pending/unresolved/manual-frontier classification. An op that is neither
    # is a PIT-correctness risk: replay must not silently choose an effective
    # date. Owner module: core.commencement_totality_audit. The audit function
    # emits Observation carriers only (role=observation; never raises, never
    # mutates legal state) — a strict-profile consumer may flip the finding to
    # a strict barrier via this default_enforcement; the audit itself just
    # records the gap and continues. Mirrors the precedent
    # APPLY.UNCOVERED_BODY_SECTION registry code (strict_fail + observation:
    # strict barrier in strict mode, observation in quirks).
    FindingSpec("COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION", "compile-timelines",
                "audit", "strict_fail", "commencement_totality_audit",
                "A LegalOperation reached timeline compilation without a matching "
                "commencement TemporalEvent and without a pending/unresolved/"
                "manual-frontier classification.",
                ("temporal_selection", "strictness"), role="observation"),
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
