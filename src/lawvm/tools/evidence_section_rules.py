"""Typed rule functions for section-level evidence claim construction (A1).

Each rule takes a SectionEvidenceContext and returns a tuple of typed
claims / sinks / defeaters.  Rules are registered into phase-ordered
tuples consumed by the staged resolver in evidence_claim_algebra.py.

Every rule is a 1:1 extraction from a branch in the legacy
_build_section_claims() function.
"""
from __future__ import annotations

from lawvm.core.section_evidence_context import SectionEvidenceContext
from lawvm.tools.evidence_claim_algebra import (
    ClaimSelector,
    Defeater,
    DefeaterEffect,
    PositiveClaim,
    ProofTier,
    RulePhase,
    RuleSpec,
    UnresolvedSink,
)
from lawvm.tools.evidence_claims import (
    _is_deterministic_payload_completeness_oracle_stale_support,
    _is_deterministic_sparse_oracle_stale_support,
)

# =========================================================================
# PREEMPTIVE POSITIVE RULES
# =========================================================================


def rule_oracle_stale_diagnosis(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Oracle-incorrect diagnosis present (line 117-126 of legacy)."""
    if not ctx.is_oracle_incorrect_diagnosis:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.ORACLE_STALE_DIAG",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_section_stale",
            inference_rule="oracle_stale_section_diagnosis_present",
            observation_sources=("oracle_check",),
            support={},
        ),
    )


def rule_oracle_temporal_impossibility(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Oracle suspect detail + replay-bug diagnosis (B2, line 128-148)."""
    if not ctx.oracle_suspect_detail:
        return ()
    if not ctx.is_replay_bug_diagnosis:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.ORACLE_TEMPORAL_IMPOSSIBILITY",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_temporal_impossibility",
            inference_rule=(
                "oracle_version_effective_date_exceeds_cutoff_"
                "therefore_oracle_presents_temporally_ineligible_state"
            ),
            observation_sources=("oracle_check", "timeline"),
            support={
                "oracle_suspect_detail": ctx.oracle_suspect_detail,
            },
        ),
    )


# =========================================================================
# PRIMARY POSITIVE RULES
# =========================================================================


def rule_extra_empty_oracle_explicit_content_absent(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """EXTRA + empty oracle + explicit contentAbsent (line 155-183)."""
    if ctx.diagnosis != "EXTRA":
        return ()
    if ctx.oracle_text.strip():
        return ()
    if ctx.cross_chapter_oracle_match:
        return ()
    if ctx.alternative_replay_match:
        return ()
    if ctx.oracle_range_match:
        return ()
    if not ctx.oracle_content_absent:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.EXTRA_EMPTY_ORACLE_CONTENT_ABSENT",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_section_stale",
            inference_rule="oracle_content_absent_replay_has_content",
            observation_sources=("oracle_check",),
            support={
                "reason": "oracle is contentAbsent — no consolidated text available",
                "explicit_content_absent": True,
            },
        ),
    )


def rule_duplicate_unscoped_oracle_labels_noncommensurable(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Empty oracle + duplicate unscoped HTML labels (line 184-198)."""
    if ctx.oracle_text.strip():
        return ()
    if not ctx.html_noncommensurable_reason.startswith(
        "duplicate_unscoped_oracle_labels:"
    ):
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.DUPLICATE_UNSCOPED_ORACLE_LABELS",
            tier=ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
            kind="html_xml_scope_noncommensurable",
            inference_rule="empty_oracle_section_with_duplicate_unscoped_oracle_labels",
            observation_sources=("html_topology", "oracle_check"),
            support={
                "noncommensurable_reason": ctx.html_noncommensurable_reason,
            },
        ),
    )


def rule_same_chapter_oracle_range_drift(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Oracle range match present (line 199-213)."""
    if not ctx.oracle_range_match:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.ORACLE_RANGE_DRIFT",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="same_chapter_oracle_range_drift",
            inference_rule=(
                "oracle_uses_same_chapter_section_range_instead_of_exact_section_label"
            ),
            observation_sources=("oracle_check",),
            support={
                "oracle_range_section": ctx.oracle_range_match.get(
                    "oracle_range_section"
                ),
                "oracle_range_label": ctx.oracle_range_match.get(
                    "oracle_range_label"
                ),
            },
        ),
    )


def rule_cross_chapter_oracle_match_exact(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Cross-chapter oracle match with score >= 0.95 (line 214-229)."""
    if not ctx.cross_chapter_oracle_match:
        return ()
    score = float(
        ctx.cross_chapter_oracle_match.get("oracle_section_score") or 0.0
    )
    runner_up_score = float(
        ctx.cross_chapter_oracle_match.get("runner_up_oracle_section_score") or 0.0
    )
    if score < 0.95 or score < (runner_up_score + 0.05):
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.CROSS_CHAPTER_ORACLE_EXACT",
            tier=ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
            kind="address_relocation_cross_chapter_exact",
            inference_rule="oracle_matches_same_label_section_in_different_chapter",
            observation_sources=("oracle_check",),
            support={
                "oracle_section": ctx.cross_chapter_oracle_match.get(
                    "oracle_section"
                ),
                "oracle_section_score": ctx.cross_chapter_oracle_match.get(
                    "oracle_section_score"
                ),
                "same_section_score": ctx.cross_chapter_oracle_match.get(
                    "same_section_score"
                ),
                "runner_up_oracle_section": ctx.cross_chapter_oracle_match.get(
                    "runner_up_oracle_section"
                ),
                "runner_up_oracle_section_score": ctx.cross_chapter_oracle_match.get(
                    "runner_up_oracle_section_score"
                ),
            },
        ),
    )


def rule_cross_chapter_replay_match_exact(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Cross-chapter replay match with score >= 0.95."""
    if not ctx.cross_chapter_replay_match:
        return ()
    score = float(
        ctx.cross_chapter_replay_match.get("replay_section_score") or 0.0
    )
    runner_up_score = float(
        ctx.cross_chapter_replay_match.get("runner_up_replay_section_score") or 0.0
    )
    if score < 0.95 or score < (runner_up_score + 0.05):
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.CROSS_CHAPTER_REPLAY_EXACT",
            tier=ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
            kind="address_relocation_cross_chapter_exact",
            inference_rule="replay_matches_same_label_section_in_different_chapter_than_oracle",
            observation_sources=("oracle_check",),
            support={
                "replay_section": ctx.cross_chapter_replay_match.get(
                    "replay_section"
                ),
                "replay_section_score": ctx.cross_chapter_replay_match.get(
                    "replay_section_score"
                ),
                "same_section_score": ctx.cross_chapter_replay_match.get(
                    "same_section_score"
                ),
                "runner_up_replay_section": ctx.cross_chapter_replay_match.get(
                    "runner_up_replay_section"
                ),
                "runner_up_replay_section_score": ctx.cross_chapter_replay_match.get(
                    "runner_up_replay_section_score"
                ),
            },
        ),
    )


def rule_preexisting_baseline_high_confidence(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Preexisting before any drop + baseline >= 0.95 (line 244-261)."""
    if not ctx.has_preexisting_residue_support:
        return ()
    baseline_score = float(ctx.bisect_support.get("baseline_score") or 0.0)
    if baseline_score < 0.95:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.PREEXISTING_BASELINE_HIGH",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_editorial_drift_baseline_witness",
            inference_rule=(
                "divergence_predates_all_amendments_and_baseline_replay_"
                "matches_base_statute_therefore_oracle_is_editorial"
            ),
            observation_sources=("section_bisect", "baseline_witness"),
            support={
                "baseline_score": baseline_score,
                "first_bad_source": ctx.bisect_support.get("first_bad_source"),
            },
        ),
    )


def rule_negligible_blame_drop_high_confidence(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Negligible blame drop + baseline >= 0.95 (line 275-291).

    Only fires when preexisting_before_any_drop is False (elif branch).
    """
    if ctx.has_preexisting_residue_support:
        return ()
    if not ctx.negligible_blame_drop_on_preexisting_residue:
        return ()
    baseline_score = float(ctx.bisect_support.get("baseline_score") or 0.0)
    if baseline_score < 0.95:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.NEGLIGIBLE_BLAME_DROP_HIGH",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_editorial_drift_baseline_witness",
            inference_rule=(
                "divergence_predates_all_amendments_and_baseline_replay_"
                "matches_base_statute_therefore_oracle_is_editorial"
            ),
            observation_sources=("section_bisect", "baseline_witness"),
            support={
                "baseline_score": baseline_score,
                "first_bad_source": ctx.bisect_support.get("first_bad_source"),
            },
        ),
    )


def rule_blame_only_repeal_without_payload(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Blame only repeal without payload (line 333-344)."""
    if not bool(ctx.bisect_support.get("blame_only_repeal_without_payload")):
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.BLAME_ONLY_REPEAL",
            tier=ProofTier.PROVED_SOURCE_PATHOLOGY,
            kind="blamed_source_lacks_payload_support",
            inference_rule="blamed_amendment_has_only_repeal_support_without_section_payload",
            observation_sources=("source_payload", "section_bisect"),
            support={
                "compiled_actions": list(
                    ctx.bisect_support.get(
                        "blame_compiled_actions_for_section", []
                    )
                    or []
                ),
            },
        ),
    )


def rule_blame_payload_prefers_replay(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Blame payload prefers replay (line 345-357)."""
    if not bool(ctx.bisect_support.get("blame_payload_prefers_replay")):
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.BLAME_PAYLOAD_PREFERS_REPLAY",
            tier=ProofTier.PROVED_SOURCE_PATHOLOGY,
            kind="blamed_source_payload_prefers_replay",
            inference_rule="blamed_section_payload_matches_replay_better_than_oracle",
            observation_sources=("source_payload", "section_bisect"),
            support={
                "payload_vs_replay_score": ctx.bisect_support.get(
                    "blame_payload_vs_replay_score"
                ),
                "payload_vs_oracle_score": ctx.bisect_support.get(
                    "blame_payload_vs_oracle_score"
                ),
            },
        ),
    )


def rule_deterministic_sparse_oracle_stale(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Deterministic sparse oracle stale (line 358-386)."""
    if not _is_deterministic_sparse_oracle_stale_support(ctx.bisect_support):
        return ()
    support = ctx.bisect_support
    return (
        PositiveClaim(
            rule_id="SEC.POS.DETERMINISTIC_SPARSE_STALE",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_section_stale",
            inference_rule="deterministic_sparse_same_section_drops_leave_oracle_stale",
            observation_sources=(
                "section_bisect",
                "elaboration",
                "apply_mutation",
            ),
            support={
                "first_drop_source": str(
                    support.get("first_drop_source") or ""
                ),
                "blame_source": ctx.blame_source,
                "drop_sources": sorted(
                    {
                        str(item.get("source_id") or "")
                        for item in list(support.get("worst_drops") or [])
                        if str(item.get("source_id") or "")
                    }
                ),
                "observation_kinds": list(
                    support.get("blame_elaboration_kinds", []) or []
                ),
                "first_drop_binding_labels": list(
                    support.get(
                        "first_drop_sparse_slot_binding_labels", []
                    )
                    or []
                ),
                "blame_binding_labels": list(
                    support.get(
                        "blame_sparse_slot_binding_labels", []
                    )
                    or []
                ),
            },
        ),
    )


def rule_deterministic_payload_completeness_oracle_stale(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Deterministic payload-completeness oracle stale."""
    if not _is_deterministic_payload_completeness_oracle_stale_support(ctx.bisect_support):
        return ()
    support = ctx.bisect_support
    return (
        PositiveClaim(
            rule_id="SEC.POS.DETERMINISTIC_PAYLOAD_COMPLETENESS_STALE",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_section_stale",
            inference_rule="deterministic_payload_completeness_same_section_drop_leaves_oracle_stale",
            observation_sources=(
                "section_bisect",
                "elaboration",
                "apply_mutation",
            ),
            support={
                "first_drop_source": str(
                    support.get("first_drop_source") or ""
                ),
                "blame_source": ctx.blame_source,
                "drop_sources": sorted(
                    {
                        str(item.get("source_id") or "")
                        for item in list(support.get("worst_drops") or [])
                        if str(item.get("source_id") or "")
                    }
                ),
                "observation_kinds": list(
                    support.get("blame_elaboration_kinds", []) or []
                ),
                "first_drop_binding_labels": list(
                    support.get(
                        "first_drop_sparse_slot_binding_labels", []
                    )
                    or []
                ),
                "blame_binding_labels": list(
                    support.get(
                        "blame_sparse_slot_binding_labels", []
                    )
                    or []
                ),
            },
        ),
    )


def rule_same_chapter_alternative_match_exact(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Alternative match with score >= 0.95 (line 482-500)."""
    if not ctx.alternative_replay_match:
        return ()
    score = float(
        ctx.alternative_replay_match.get("best_replay_score") or 0.0
    )
    if score < 0.95:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.SAME_CHAPTER_ALT_EXACT",
            tier=ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
            kind="address_relocation_same_chapter_exact",
            inference_rule=(
                "same_chapter_replay_section_matches_oracle_better_"
                "than_same_number_section"
            ),
            observation_sources=("oracle_check",),
            support={
                "best_replay_section": ctx.alternative_replay_match.get(
                    "best_replay_section"
                ),
                "best_replay_score": ctx.alternative_replay_match.get(
                    "best_replay_score"
                ),
                "same_section_score": ctx.alternative_replay_match.get(
                    "same_section_score"
                ),
            },
        ),
    )


def rule_no_blame_no_timeline(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """No blame + no timeline entry → oracle editorial drift (line 518-557).

    Only fires when blame_source is empty AND no preexisting baseline_residue
    candidate would have been emitted (matching legacy ``not candidates``
    check approximation — lifted to has_preexisting_residue_support /
    has_negligible_preexisting_drop_support context flags).

    Attack #9 guard: if chain completeness is populated and incomplete,
    this rule does NOT fire — the negative proof is unsound because a
    missing amendment could be the real cause. The downgraded case is
    handled by rule_no_blame_no_timeline_chain_incomplete.
    """
    if ctx.blame_source:
        return ()
    # Legacy: ``not any("preexisting" in kind and "baseline_residue" in kind ...)``
    # The only candidates with those kind substrings come from the preexisting
    # rules.  The context flags are the typed equivalent.
    if ctx.has_preexisting_residue_support or ctx.has_negligible_preexisting_drop_support:
        return ()
    if ctx.has_timeline_entry is not False:
        return ()
    # Attack #9: only block promotion when chain completeness was actually
    # computed and found incomplete. Standalone typed uses without a chain
    # certificate must preserve legacy behavior.
    if ctx.chain_completeness is not None and not ctx.has_complete_chain:
        return ()  # handled by rule_no_blame_no_timeline_chain_incomplete
    return (
        PositiveClaim(
            rule_id="SEC.POS.NO_BLAME_NO_TIMELINE",
            tier=ProofTier.PROVED_ORACLE_INCORRECT,
            kind="oracle_editorial_drift_no_timeline",
            inference_rule=(
                "section_has_no_blamed_amendment_and_no_timeline_entry_"
                "therefore_oracle_text_is_editorial_not_legislative"
            ),
            observation_sources=("oracle_check", "timeline_invariants"),
            support={
                "diagnosis": ctx.diagnosis,
                "similarity": ctx.similarity,
                "timeline_present": False,
            },
        ),
    )


def rule_no_blame_no_timeline_chain_incomplete(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """No blame + no timeline + incomplete chain → UNRESOLVED (attack #9).

    When the amendment chain is incomplete, the negative proof
    "no amendment touched this section" is unsound — a missing or
    failed amendment could be the real cause of the divergence.
    Downgrades to UNRESOLVED instead of PROVED_ORACLE_INCORRECT.
    """
    if ctx.blame_source:
        return ()
    if ctx.has_preexisting_residue_support or ctx.has_negligible_preexisting_drop_support:
        return ()
    if ctx.has_timeline_entry is not False:
        return ()
    # Only fire when chain was computed and is explicitly incomplete.
    if ctx.chain_completeness is None or ctx.has_complete_chain:
        return ()
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.NO_BLAME_NO_TIMELINE_CHAIN_INCOMPLETE",
            kind="UNRESOLVED.chain_incomplete.negative_proof_unsound",
            inference_rule=(
                "section_has_no_blamed_amendment_and_no_timeline_entry_"
                "but_amendment_chain_is_incomplete_so_negative_proof_unsound"
            ),
            observation_sources=("oracle_check", "timeline_invariants", "chain_completeness"),
            support={
                "diagnosis": ctx.diagnosis,
                "similarity": ctx.similarity,
                "timeline_present": False,
                "chain_incomplete_reasons": list(ctx.chain_incomplete_reasons),
            },
        ),
    )


# =========================================================================
# PRIMARY SINK RULES
# =========================================================================


def rule_extra_empty_oracle_unverified_absence(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """EXTRA + empty oracle but contentAbsent NOT verified (line 155-183)."""
    if ctx.diagnosis != "EXTRA":
        return ()
    if ctx.oracle_text.strip():
        return ()
    if ctx.cross_chapter_oracle_match:
        return ()
    if ctx.alternative_replay_match:
        return ()
    if ctx.oracle_range_match:
        return ()
    if ctx.oracle_content_absent:
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.EXTRA_EMPTY_ORACLE_UNVERIFIED",
            kind="UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
            inference_rule="oracle_text_empty_but_contentAbsent_not_verified",
            observation_sources=("oracle_check",),
            support={
                "reason": "oracle text is empty but contentAbsent flag not explicitly checked",
                "explicit_content_absent": False,
            },
        ),
    )


def rule_cross_chapter_oracle_match_unresolved(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Cross-chapter oracle match with score < 0.95 (line 230-243)."""
    if not ctx.cross_chapter_oracle_match:
        return ()
    score = float(
        ctx.cross_chapter_oracle_match.get("oracle_section_score") or 0.0
    )
    runner_up_score = float(
        ctx.cross_chapter_oracle_match.get("runner_up_oracle_section_score") or 0.0
    )
    if score >= 0.95 and score >= (runner_up_score + 0.05):
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.CROSS_CHAPTER_ORACLE_UNRESOLVED",
            kind="UNRESOLVED.address_projection.cross_chapter_oracle_drift",
            inference_rule="oracle_matches_same_label_section_in_different_chapter",
            observation_sources=("oracle_check",),
            support={
                "oracle_section": ctx.cross_chapter_oracle_match.get(
                    "oracle_section"
                ),
                "oracle_section_score": ctx.cross_chapter_oracle_match.get(
                    "oracle_section_score"
                ),
                "same_section_score": ctx.cross_chapter_oracle_match.get(
                    "same_section_score"
                ),
                "runner_up_oracle_section": ctx.cross_chapter_oracle_match.get(
                    "runner_up_oracle_section"
                ),
                "runner_up_oracle_section_score": ctx.cross_chapter_oracle_match.get(
                    "runner_up_oracle_section_score"
                ),
            },
        ),
    )


def rule_cross_chapter_replay_match_unresolved(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Cross-chapter replay match with score < 0.95."""
    if not ctx.cross_chapter_replay_match:
        return ()
    score = float(
        ctx.cross_chapter_replay_match.get("replay_section_score") or 0.0
    )
    runner_up_score = float(
        ctx.cross_chapter_replay_match.get("runner_up_replay_section_score") or 0.0
    )
    if score >= 0.95 and score >= (runner_up_score + 0.05):
        return ()
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.CROSS_CHAPTER_REPLAY_UNRESOLVED",
            kind="UNRESOLVED.address_projection.cross_chapter_replay_drift",
            inference_rule="replay_matches_same_label_section_in_different_chapter_than_oracle",
            observation_sources=("oracle_check",),
            support={
                "replay_section": ctx.cross_chapter_replay_match.get(
                    "replay_section"
                ),
                "replay_section_score": ctx.cross_chapter_replay_match.get(
                    "replay_section_score"
                ),
                "same_section_score": ctx.cross_chapter_replay_match.get(
                    "same_section_score"
                ),
                "runner_up_replay_section": ctx.cross_chapter_replay_match.get(
                    "runner_up_replay_section"
                ),
                "runner_up_replay_section_score": ctx.cross_chapter_replay_match.get(
                    "runner_up_replay_section_score"
                ),
            },
        ),
    )


def rule_preexisting_baseline_low_confidence(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Preexisting before any drop + baseline < 0.95 (line 263-274)."""
    if not ctx.has_preexisting_residue_support:
        return ()
    baseline_score = float(ctx.bisect_support.get("baseline_score") or 0.0)
    if baseline_score >= 0.95:
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.PREEXISTING_BASELINE_LOW",
            kind="UNRESOLVED.preexisting.baseline_residue",
            inference_rule="replay_residue_predates_any_amendment_drop",
            observation_sources=("section_bisect",),
            support={
                "baseline_score": ctx.bisect_support.get("baseline_score"),
                "first_bad_source": ctx.bisect_support.get("first_bad_source"),
            },
        ),
    )


def rule_negligible_blame_drop_low_confidence(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Negligible blame drop + baseline < 0.95 (line 293-311).

    Only fires when preexisting_before_any_drop is False (elif branch).
    """
    if ctx.has_preexisting_residue_support:
        return ()
    if not ctx.negligible_blame_drop_on_preexisting_residue:
        return ()
    baseline_score = float(ctx.bisect_support.get("baseline_score") or 0.0)
    if baseline_score >= 0.95:
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.NEGLIGIBLE_BLAME_DROP_LOW",
            kind="UNRESOLVED.preexisting.baseline_residue",
            inference_rule="material_divergence_predates_blamed_change_and_blame_delta_is_negligible",
            observation_sources=("section_bisect", "section_trace"),
            support={
                "baseline_score": ctx.bisect_support.get("baseline_score"),
                "first_bad_source": ctx.bisect_support.get("first_bad_source"),
                "blame_before_score": ctx.bisect_support.get(
                    "blame_before_score"
                ),
                "blame_after_score": ctx.bisect_support.get(
                    "blame_after_score"
                ),
                "blame_delta": (
                    float(ctx.bisect_support.get("blame_before_score") or 0.0)
                    - float(
                        ctx.bisect_support.get("blame_after_score") or 0.0
                    )
                ),
            },
        ),
    )


def rule_baseline_same_section_structure_drift(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Baseline same-section structure drift (line 312-332)."""
    drift = ctx.baseline_same_section_structure_drift
    if not drift:
        return ()
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.BASELINE_SAME_SECTION_STRUCTURE_DRIFT",
            kind="UNRESOLVED.preexisting.same_section_structure_drift",
            inference_rule=(
                "oracle_has_unmatched_same_section_subsection_fragments_"
                "before_blamed_amendment"
            ),
            observation_sources=("section_bisect", "oracle_check"),
            support={
                "unmatched_oracle_subsection_count": drift.get("count"),
                "unmatched_oracle_subsection_excerpts": list(
                    drift.get("oracle_text_excerpts", []) or []
                ),
                "max_best_replay_score": drift.get("max_best_replay_score"),
            },
        ),
    )


def rule_blame_sparse_elaboration(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Blame sparse elaboration (line 387-414)."""
    support = ctx.bisect_support
    if not bool(support.get("blame_sparse_elaboration")):
        return ()
    inference_rule = (
        "blamed_amendment_has_same_section_elaboration_observation"
    )
    if (
        int(support.get("blame_sparse_leftover_count") or 0) > 0
        and not list(
            support.get("blame_elaboration_kinds", []) or []
        )
    ):
        inference_rule = "blamed_amendment_has_same_section_sparse_leftovers"
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.BLAME_SPARSE_ELABORATION",
            kind="UNRESOLVED.source_underdetermined.elaboration_ambiguity",
            inference_rule=inference_rule,
            observation_sources=(
                "elaboration",
                "apply_mutation",
                "section_bisect",
            ),
            support={
                "observation_kinds": list(
                    support.get("blame_elaboration_kinds", []) or []
                ),
                "sparse_slot_binding_count": int(
                    support.get("blame_sparse_slot_binding_count")
                    or 0
                ),
                "sparse_slot_binding_labels": list(
                    support.get(
                        "blame_sparse_slot_binding_labels", []
                    )
                    or []
                ),
                "sparse_leftover_count": int(
                    support.get("blame_sparse_leftover_count") or 0
                ),
                "apply_helpers": list(
                    support.get("blame_apply_helpers_for_section", []) or []
                ),
            },
        ),
    )


def rule_first_drop_sparse_elaboration(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """First-drop sparse elaboration (line 416-451)."""
    support = ctx.bisect_support
    if not bool(support.get("first_drop_sparse_elaboration")):
        return ()
    first_drop_source = str(support.get("first_drop_source") or "")
    if not first_drop_source:
        return ()
    if first_drop_source == ctx.blame_source:
        return ()
    inference_rule = (
        "first_drop_amendment_has_same_section_elaboration_observation"
    )
    if (
        int(support.get("first_drop_sparse_leftover_count") or 0)
        > 0
        and not list(
            support.get("first_drop_elaboration_kinds", []) or []
        )
    ):
        inference_rule = "first_drop_amendment_has_same_section_sparse_leftovers"
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.FIRST_DROP_SPARSE_ELABORATION",
            kind="UNRESOLVED.preexisting.elaboration_ambiguity",
            inference_rule=inference_rule,
            observation_sources=(
                "elaboration",
                "apply_mutation",
                "section_bisect",
            ),
            support={
                "first_drop_source": first_drop_source,
                "observation_kinds": list(
                    support.get(
                        "first_drop_elaboration_kinds", []
                    )
                    or []
                ),
                "sparse_slot_binding_count": int(
                    support.get(
                        "first_drop_sparse_slot_binding_count"
                    )
                    or 0
                ),
                "sparse_slot_binding_labels": list(
                    support.get(
                        "first_drop_sparse_slot_binding_labels", []
                    )
                    or []
                ),
                "sparse_leftover_count": int(
                    support.get(
                        "first_drop_sparse_leftover_count"
                    )
                    or 0
                ),
                "apply_helpers": list(
                    support.get(
                        "first_drop_apply_helpers_for_section", []
                    )
                    or []
                ),
            },
        ),
    )


def rule_blame_source_improved_or_equal(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Blame source improved or equal (line 452-464)."""
    if not bool(ctx.bisect_support.get("blame_source_improved_or_equal")):
        return ()
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.BLAME_SOURCE_IMPROVED",
            kind="UNRESOLVED.source_underdetermined.amendment_improves_section",
            inference_rule="blamed_amendment_improves_or_preserves_section_similarity",
            observation_sources=("section_trace",),
            support={
                "before_score": ctx.bisect_support.get("blame_before_score"),
                "after_score": ctx.bisect_support.get("blame_after_score"),
            },
        ),
    )


def rule_baseline_alternative_match(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Baseline alternative match (line 465-481)."""
    bam = ctx.baseline_alternative_match
    if not bam:
        return ()
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.BASELINE_ALT_MATCH",
            kind="UNRESOLVED.address_projection.same_chapter_section_drift",
            inference_rule=(
                "preexisting_same_chapter_replay_section_matches_oracle_"
                "better_than_same_number_section"
            ),
            observation_sources=("section_bisect", "oracle_check"),
            support={
                "best_replay_section": bam.get("best_replay_section"),
                "best_replay_score": bam.get("best_replay_score"),
                "same_section_score": bam.get("same_section_score"),
            },
        ),
    )


def rule_same_chapter_alternative_match_unresolved(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """Alternative match with score < 0.95 (line 501-517)."""
    if not ctx.alternative_replay_match:
        return ()
    score = float(
        ctx.alternative_replay_match.get("best_replay_score") or 0.0
    )
    if score >= 0.95:
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.SAME_CHAPTER_ALT_UNRESOLVED",
            kind="UNRESOLVED.address_projection.same_chapter_replay_drift",
            inference_rule=(
                "same_chapter_replay_section_matches_oracle_better_"
                "than_same_number_section"
            ),
            observation_sources=("oracle_check",),
            support={
                "best_replay_section": ctx.alternative_replay_match.get(
                    "best_replay_section"
                ),
                "best_replay_score": ctx.alternative_replay_match.get(
                    "best_replay_score"
                ),
                "same_section_score": ctx.alternative_replay_match.get(
                    "same_section_score"
                ),
            },
        ),
    )


def rule_no_blame_has_timeline(
    ctx: SectionEvidenceContext,
) -> tuple[UnresolvedSink, ...]:
    """No blame + has timeline (or timeline unknown) (line 558-570).

    Fires when blame_source is empty AND no preexisting baseline_residue
    candidate would have been emitted AND timeline is not False.
    """
    if ctx.blame_source:
        return ()
    if ctx.has_preexisting_residue_support or ctx.has_negligible_preexisting_drop_support:
        return ()
    if ctx.has_timeline_entry is False:
        return ()  # handled by positive rule
    return (
        UnresolvedSink(
            rule_id="SEC.SINK.NO_BLAME_HAS_TIMELINE",
            kind="UNRESOLVED.preexisting.baseline_residue",
            inference_rule="residual_replay_divergence_has_no_blamed_amendment",
            observation_sources=("oracle_check",),
            support={
                "diagnosis": ctx.diagnosis,
                "similarity": ctx.similarity,
            },
        ),
    )


# =========================================================================
# FALLBACK DEFEATER RULES
# =========================================================================


def rule_extraction_gap_defeater(
    ctx: SectionEvidenceContext,
) -> tuple[Defeater, ...]:
    """Extraction gap defeats fallback replay attribution (line 576-596)."""
    if not ctx.has_extraction_gap:
        return ()
    sink = UnresolvedSink(
        rule_id="SEC.SINK.EXTRACTION_GAP",
        kind="UNRESOLVED.source_underdetermined.extraction_coverage_gap",
        inference_rule=(
            "statute_has_extraction_fallback_so_replay_divergence_"
            "cannot_be_attributed_to_replay_logic"
        ),
        observation_sources=("oracle_check", "compile_result"),
        support={
            "diagnosis": ctx.diagnosis,
            "similarity": ctx.similarity,
            "extraction_fallback": True,
        },
    )
    return (
        Defeater(
            rule_id="SEC.DEF.EXTRACTION_GAP",
            targets=ClaimSelector(
                tags=frozenset({"fallback_replay_attribution"})
            ),
            effect=DefeaterEffect.REPLACE_WITH_SINK,
            inference_rule="extraction_gap_defeats_fallback_replay_attribution",
            observation_sources=("compile_result",),
            support={"strict_fail_reasons": list(ctx.strict_fail_reasons)},
            replacement_sink=sink,
        ),
    )


def rule_section_source_barrier_defeater(
    ctx: SectionEvidenceContext,
) -> tuple[Defeater, ...]:
    """Source/extraction barrier defeats fallback replay (C1, line 601-625)."""
    if not ctx.has_source_barrier:
        return ()
    sink = UnresolvedSink(
        rule_id="SEC.SINK.SOURCE_BARRIER",
        kind="UNRESOLVED.source_underdetermined.section_strict_lineage",
        inference_rule=(
            "blamed_amendment_section_has_source_or_extraction_"
            "strict_barriers_so_replay_attribution_unsupported"
        ),
        observation_sources=("compile_result", "section_strict_lineage"),
        support={
            "amendment_id": ctx.strict_amendment_id,
            "status": ctx.strict_status,
            "barrier_kinds": list(ctx.strict_barrier_kinds),
            "barrier_families": list(ctx.strict_barrier_families),
        },
    )
    return (
        Defeater(
            rule_id="SEC.DEF.SOURCE_BARRIER",
            targets=ClaimSelector(
                tags=frozenset({"fallback_replay_attribution"})
            ),
            effect=DefeaterEffect.REPLACE_WITH_SINK,
            inference_rule="source_barrier_defeats_fallback_replay_attribution",
            observation_sources=("compile_result", "section_strict_lineage"),
            support={
                "barrier_kinds": list(ctx.strict_barrier_kinds),
            },
            replacement_sink=sink,
        ),
    )


def rule_section_recovery_barrier_defeater(
    ctx: SectionEvidenceContext,
) -> tuple[Defeater, ...]:
    """Recovery barrier defeats fallback replay (C1, line 626-643)."""
    if not ctx.has_recovery_barrier:
        return ()
    # Only fire if source barrier didn't fire (mirror legacy elif)
    if ctx.has_source_barrier:
        return ()
    sink = UnresolvedSink(
        rule_id="SEC.SINK.RECOVERY_BARRIER",
        kind="UNRESOLVED.source_underdetermined.section_recovery_barriers",
        inference_rule=(
            "blamed_amendment_section_required_recovery_paths_"
            "so_replay_divergence_may_be_recovery_artifact"
        ),
        observation_sources=("compile_result", "section_strict_lineage"),
        support={
            "amendment_id": ctx.strict_amendment_id,
            "status": ctx.strict_status,
            "barrier_kinds": list(ctx.strict_barrier_kinds),
            "barrier_families": list(ctx.strict_barrier_families),
        },
    )
    return (
        Defeater(
            rule_id="SEC.DEF.RECOVERY_BARRIER",
            targets=ClaimSelector(
                tags=frozenset({"fallback_replay_attribution"})
            ),
            effect=DefeaterEffect.REPLACE_WITH_SINK,
            inference_rule="recovery_barrier_defeats_fallback_replay_attribution",
            observation_sources=("compile_result", "section_strict_lineage"),
            support={
                "barrier_kinds": list(ctx.strict_barrier_kinds),
            },
            replacement_sink=sink,
        ),
    )


# =========================================================================
# PROMOTION POSITIVE RULES
# =========================================================================


def rule_timeline_invariant_violation(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Timeline invariant violation (C3, line 647-667)."""
    if not ctx.invariant_violations:
        return ()
    return (
        PositiveClaim(
            rule_id="SEC.POS.TIMELINE_INVARIANT_VIOLATION",
            tier=ProofTier.PROVED_REPLAY_BUG,
            kind="timeline_invariant_violation",
            inference_rule=(
                "section_has_timeline_invariant_violation_"
                "therefore_replay_state_is_inconsistent"
            ),
            observation_sources=("timeline_invariants",),
            support={
                "violation_count": len(ctx.invariant_violations),
                "violation_kinds": sorted(
                    {
                        str(v.get("kind", ""))
                        for v in ctx.invariant_violations
                        if str(v.get("kind", ""))
                    }
                ),
                "violations": ctx.invariant_violations[:5],
            },
        ),
    )


# =========================================================================
# FINAL FALLBACK RULES
# =========================================================================


def rule_replay_divergence_fallback(
    ctx: SectionEvidenceContext,
) -> tuple[PositiveClaim, ...]:
    """Fallback replay divergence (line 668-686)."""
    replay_support: dict = {}
    if ctx.alternative_replay_match:
        replay_support.update(
            {
                "best_replay_section": ctx.alternative_replay_match.get(
                    "best_replay_section"
                ),
                "best_replay_score": ctx.alternative_replay_match.get(
                    "best_replay_score"
                ),
                "same_section_score": ctx.alternative_replay_match.get(
                    "same_section_score"
                ),
            }
        )
    return (
        PositiveClaim(
            rule_id="SEC.POS.REPLAY_DIVERGENCE_FALLBACK",
            tier=ProofTier.PROVED_REPLAY_BUG,
            kind="replay_divergence",
            inference_rule="residual_replay_bug_diagnosis_present",
            observation_sources=("oracle_check",),
            support=replay_support,
            proof_tags=frozenset(
                {
                    "fallback_replay_attribution",
                    "requires_complete_extraction",
                    "requires_section_strict_lineage",
                }
            ),
        ),
    )


# =========================================================================
# REGISTRY TUPLES
# =========================================================================

PREEMPTIVE_POSITIVE_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.POS.ORACLE_STALE_DIAG",
        phase=RulePhase.PREEMPTIVE,
        order=0,
        emit=rule_oracle_stale_diagnosis,
    ),
    RuleSpec(
        rule_id="SEC.POS.ORACLE_TEMPORAL_IMPOSSIBILITY",
        phase=RulePhase.PREEMPTIVE,
        order=1,
        emit=rule_oracle_temporal_impossibility,
    ),
)

PRIMARY_POSITIVE_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.POS.EXTRA_EMPTY_ORACLE_CONTENT_ABSENT",
        phase=RulePhase.PRIMARY,
        order=10,
        emit=rule_extra_empty_oracle_explicit_content_absent,
    ),
    RuleSpec(
        rule_id="SEC.POS.DUPLICATE_UNSCOPED_ORACLE_LABELS",
        phase=RulePhase.PRIMARY,
        order=11,
        emit=rule_duplicate_unscoped_oracle_labels_noncommensurable,
    ),
    RuleSpec(
        rule_id="SEC.POS.ORACLE_RANGE_DRIFT",
        phase=RulePhase.PRIMARY,
        order=12,
        emit=rule_same_chapter_oracle_range_drift,
    ),
    RuleSpec(
        rule_id="SEC.POS.CROSS_CHAPTER_ORACLE_EXACT",
        phase=RulePhase.PRIMARY,
        order=13,
        emit=rule_cross_chapter_oracle_match_exact,
    ),
    RuleSpec(
        rule_id="SEC.POS.CROSS_CHAPTER_REPLAY_EXACT",
        phase=RulePhase.PRIMARY,
        order=14,
        emit=rule_cross_chapter_replay_match_exact,
    ),
    RuleSpec(
        rule_id="SEC.POS.PREEXISTING_BASELINE_HIGH",
        phase=RulePhase.PRIMARY,
        order=15,
        emit=rule_preexisting_baseline_high_confidence,
    ),
    RuleSpec(
        rule_id="SEC.POS.NEGLIGIBLE_BLAME_DROP_HIGH",
        phase=RulePhase.PRIMARY,
        order=16,
        emit=rule_negligible_blame_drop_high_confidence,
    ),
    RuleSpec(
        rule_id="SEC.POS.BLAME_ONLY_REPEAL",
        phase=RulePhase.PRIMARY,
        order=17,
        emit=rule_blame_only_repeal_without_payload,
    ),
    RuleSpec(
        rule_id="SEC.POS.BLAME_PAYLOAD_PREFERS_REPLAY",
        phase=RulePhase.PRIMARY,
        order=18,
        emit=rule_blame_payload_prefers_replay,
    ),
    RuleSpec(
        rule_id="SEC.POS.DETERMINISTIC_SPARSE_STALE",
        phase=RulePhase.PRIMARY,
        order=19,
        emit=rule_deterministic_sparse_oracle_stale,
    ),
    RuleSpec(
        rule_id="SEC.POS.DETERMINISTIC_PAYLOAD_COMPLETENESS_STALE",
        phase=RulePhase.PRIMARY,
        order=20,
        emit=rule_deterministic_payload_completeness_oracle_stale,
    ),
    RuleSpec(
        rule_id="SEC.POS.SAME_CHAPTER_ALT_EXACT",
        phase=RulePhase.PRIMARY,
        order=21,
        emit=rule_same_chapter_alternative_match_exact,
    ),
    RuleSpec(
        rule_id="SEC.POS.NO_BLAME_NO_TIMELINE",
        phase=RulePhase.PRIMARY,
        order=22,
        emit=rule_no_blame_no_timeline,
    ),
)

PRIMARY_SINK_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.SINK.EXTRA_EMPTY_ORACLE_UNVERIFIED",
        phase=RulePhase.PRIMARY,
        order=30,
        emit=rule_extra_empty_oracle_unverified_absence,
    ),
    RuleSpec(
        rule_id="SEC.SINK.CROSS_CHAPTER_ORACLE_UNRESOLVED",
        phase=RulePhase.PRIMARY,
        order=31,
        emit=rule_cross_chapter_oracle_match_unresolved,
    ),
    RuleSpec(
        rule_id="SEC.SINK.CROSS_CHAPTER_REPLAY_UNRESOLVED",
        phase=RulePhase.PRIMARY,
        order=32,
        emit=rule_cross_chapter_replay_match_unresolved,
    ),
    RuleSpec(
        rule_id="SEC.SINK.PREEXISTING_BASELINE_LOW",
        phase=RulePhase.PRIMARY,
        order=33,
        emit=rule_preexisting_baseline_low_confidence,
    ),
    RuleSpec(
        rule_id="SEC.SINK.NEGLIGIBLE_BLAME_DROP_LOW",
        phase=RulePhase.PRIMARY,
        order=34,
        emit=rule_negligible_blame_drop_low_confidence,
    ),
    RuleSpec(
        rule_id="SEC.SINK.BASELINE_SAME_SECTION_STRUCTURE_DRIFT",
        phase=RulePhase.PRIMARY,
        order=35,
        emit=rule_baseline_same_section_structure_drift,
    ),
    RuleSpec(
        rule_id="SEC.SINK.BLAME_FRONTEND_SPARSE_ELABORATION",
        phase=RulePhase.PRIMARY,
        order=35,
        emit=rule_blame_sparse_elaboration,
    ),
    RuleSpec(
        rule_id="SEC.SINK.FIRST_DROP_FRONTEND_SPARSE_ELABORATION",
        phase=RulePhase.PRIMARY,
        order=36,
        emit=rule_first_drop_sparse_elaboration,
    ),
    RuleSpec(
        rule_id="SEC.SINK.BLAME_SOURCE_IMPROVED",
        phase=RulePhase.PRIMARY,
        order=37,
        emit=rule_blame_source_improved_or_equal,
    ),
    RuleSpec(
        rule_id="SEC.SINK.BASELINE_ALT_MATCH",
        phase=RulePhase.PRIMARY,
        order=38,
        emit=rule_baseline_alternative_match,
    ),
    RuleSpec(
        rule_id="SEC.SINK.SAME_CHAPTER_ALT_UNRESOLVED",
        phase=RulePhase.PRIMARY,
        order=39,
        emit=rule_same_chapter_alternative_match_unresolved,
    ),
    RuleSpec(
        rule_id="SEC.SINK.NO_BLAME_HAS_TIMELINE",
        phase=RulePhase.PRIMARY,
        order=40,
        emit=rule_no_blame_has_timeline,
    ),
    RuleSpec(
        rule_id="SEC.SINK.NO_BLAME_NO_TIMELINE_CHAIN_INCOMPLETE",
        phase=RulePhase.PRIMARY,
        order=41,
        emit=rule_no_blame_no_timeline_chain_incomplete,
    ),
)

FALLBACK_DEFEATER_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.DEF.EXTRACTION_GAP",
        phase=RulePhase.FALLBACK_DEFEATER,
        order=50,
        emit=rule_extraction_gap_defeater,
    ),
    RuleSpec(
        rule_id="SEC.DEF.SOURCE_BARRIER",
        phase=RulePhase.FALLBACK_DEFEATER,
        order=51,
        emit=rule_section_source_barrier_defeater,
    ),
    RuleSpec(
        rule_id="SEC.DEF.RECOVERY_BARRIER",
        phase=RulePhase.FALLBACK_DEFEATER,
        order=52,
        emit=rule_section_recovery_barrier_defeater,
    ),
)

PROMOTION_POSITIVE_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.POS.TIMELINE_INVARIANT_VIOLATION",
        phase=RulePhase.PROMOTION,
        order=60,
        emit=rule_timeline_invariant_violation,
    ),
)

FINAL_FALLBACK_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="SEC.POS.REPLAY_DIVERGENCE_FALLBACK",
        phase=RulePhase.FINAL_FALLBACK,
        order=70,
        emit=rule_replay_divergence_fallback,
    ),
)
