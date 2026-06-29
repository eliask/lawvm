"""EE-side guard-liveness discipline.

The active frontend's parser emits blocking ``CompileAdjudication`` records
(those with ``blocking=True``) whenever it cannot silently resolve a legal-
state mutation. Each blocking code is a safety contract: the parser is
saying "I stopped here on purpose and the replay path must surface this
finding". A guard that exists but is unreachable from the production lane
looks real but creates false confidence (AGENTS.md §2.9 — "the worst
failure class").

This module mirrors the FI guard-liveness ratchet for the Estonia frontend:
a partitioned inventory in which every blocking rule_id is either

* exercised by a fire-drill that drives a known-violating input through the
  full ``replay_ee_to_pit`` production path and asserts the diagnostic
  fires, OR
* explicitly debt-admitted via ``EE_NO_FIRE_DRILL_YET`` with a stated reason
  and last-reviewed date (so the debt is consciously maintained, never
  silently parked).

``EE_NO_FIRE_DRILL_CEILING`` is a committed monotone-decreasing ceiling:
the allowlist may shrink (a drill is built and the entry removed) but it may
never grow past the ceiling, and the ceiling itself only ratchets down. A
new blocking code added to the inventory must either come with a fire-drill
or pay the debt ceiling back down somewhere first. The discipline therefore
makes silent-guard additions a CI failure rather than a deferred smell.

What lives here vs what does NOT:

* NOT a port of FI's ``FINDING_REGISTRY``. EE emits adjudications through
  ``replay_adjudication.CompileAdjudication`` instead of FI's finding
  registry, so EE's blocking-code namespace is its own. The catalog over
  at ``lawvm.tools.spec_ledger_ee_catalog`` already covers the anti-drift
  layer — every ``ee_*`` literal is registered as a falsifiable belief.
  This module is the finer-grained "is it a blocking code, and is it drilled
  or debt-admitted" layer on top.
* The set ``EE_BLOCKING_RULE_IDS`` is curated from a static AST scan over
  the EE source (``src/lawvm/estonia/*.py``). The companion test
  ``tests/test_ee_guard_liveness.py::test_ee_blocking_rule_ids_match_source_scan``
  re-runs that scan and asserts it still matches the inventory, so a future
  blocking code that lands in source can't slip past this module without
  also being admitted to (or drilled out of) the debt list.
"""
from __future__ import annotations

from typing import Dict, Final, FrozenSet

# ---------------------------------------------------------------------------
# Blocking rule_id inventory
# ---------------------------------------------------------------------------
#
# Verified blocking emit sites as of 2026-06-24 (master @ d22c44d0).
# Discovered by AST-scanning ``src/lawvm/estonia/*.py`` for
# ``CompileAdjudication(..., blocking=True, ...)`` emit sites — both direct
# calls and via the two helpers that hardcode ``blocking=True``:
#   * ``_record_old_format_ref_slice_drop`` (grafter.py —
#     ee_ref_slice_operation_filtered host)
#   * ``_append_ee_replay_adjudication`` (grafter.py — generic EE replay
#     adjudication broadcaster: hardcodes blocking=True for every kind=)
# Plus one direct ``CompileAdjudication(kind="ee_parse_old_format_unparsed_meta_rejected",
# ..., blocking=True)`` literal and one ``rule_id = "ee_..."`` literal
# assigned before a ``CompileAdjudication(kind=rule_id, ..., blocking=True)``.
#
# 24 distinct blocking rule_ids across 1 statutory signature
# (``CompileAdjudication``) and 2 named helpers.
EE_BLOCKING_RULE_IDS: Final[FrozenSet[str]] = frozenset(
    {
        # --- Old-format META / ref-slice parse-time guards --------------------
        "ee_parse_old_format_unparsed_meta_rejected",
        "ee_ref_slice_operation_filtered",
        # --- payload-normalization selector inference -------------------------
        "ee_source_local_global_text_replace_selector_exclusion_inferred",
        # --- text_replace scope decisions -------------------------------------
        "ee_ambiguous_single_occurrence_text_replace",
        "ee_source_case_only_text_replace",
        "ee_text_replace_numbered_subsection_for_item_target_by_old_text",
        "ee_text_replace_unique_descendant_item_by_old_text",
        # --- item / section / subsection recovery -----------------------------
        "ee_inline_item_replace_singleton_subsection",
        "ee_labelled_item_replacement_payload_selection",
        "ee_section_item_replace_unique_descendant_item",
        "ee_subsection_table_only_replace_preserve_intro",
        # --- plural coordination + overbroad guards ---------------------------
        "ee_overbroad_container_replace_blocked",
        "ee_plural_item_replace_range_omits_inserted_labels",
        "ee_plural_subsection_replace_extra_payload_label",
        "ee_flat_part_repeal_span",
        # --- division / jagu recovery -----------------------------------------
        "ee_implicit_division_sequence_relabel_after_high_jagu_insert",
        # --- §1.7 same-moment cross-act conflict (pre-pass in grafter.py) ------
        # Blocking finding emitted by the shared
        # ``lawvm.core.cross_act_same_moment.detect_cross_act_same_moment_conflicts``
        # (called by ``apply_ee_ops`` with ``finder_kind_prefix="ee"``) before the
        # apply fold when two distinct affecting acts change the same target at
        # the same effective date with incompatible whole-target payloads. The
        # EE-specific compatibility predicate ``ee_same_moment_payloads_incompatible``
        # is passed so finding output is byte-identical to the pre-B1 standalone
        # detector. Cross-act (carries an empty op_id), so it surfaces as an
        # evidence row without partition impact in the conserved wrapper.
        "ee_same_moment_cross_act_incompatible_payload_ambiguous",
        # --- generic replay-time guard adjudications --------------------------
        # Broadcast via ``_append_ee_replay_adjudication``.
        # ``ee_replay_unsupported_action`` and ``ee_replay_target_not_found``
        # are drilled out of the debt list by
        # ``tests/test_ee_guard_liveness.py`` (drives a HEADING_REPLACE op /
        # a REPLACE op against a non-existent target through ``apply_ee_ops``
        # and asserts the blocking adjudication fires); the remaining
        # ``ee_replay_*`` codes below stay debt-admitted until their
        # individual drills land.
        "ee_replay_unsupported_action",
        "ee_replay_target_not_found",
        "ee_replay_unsupported_heading_target",
        "ee_replay_unsupported_statute_title_action",
        "ee_replay_noop",
        "ee_replay_statute_title_noop",
        "ee_replay_meta_non_body_skipped",
        "ee_replay_unparsed_operation_skipped",
        # --- source-lane orchestration failures (via _ee_orchestration_*) ------
        # The fail-loud broad-except audits in commits 0d60710e (pair-planning)
        # + 00f778fc (replay) added explicit blocking adjudications for source
        # fetch / parse / consistency-check failures that previously crashed
        # silently. Each forwards ``blocking=True`` and a kind= through
        # ``_ee_orchestration_adjudication`` (a pass-through helper — the
        # caller's ``blocking=`` flows to the inner CompileAdjudication).
        "ee_oracle_parse_failed",
        "ee_consistency_check_failed",
        "ee_amendment_parse_failed",
        "ee_amendment_source_fetch_failed",
        "ee_cancelled_pending_ref_metadata_parse_failed",
        "ee_cancelled_pending_ref_source_fetch_failed",
        "ee_pending_source_act_commencement_source_fetch_failed",
        "ee_temporal_source_scan_failed",
    }
)


# ---------------------------------------------------------------------------
# Fire-drill coverage
# ---------------------------------------------------------------------------
#
# A fire-drill is a test that drives a known-violator through the full
# ``replay_ee_to_pit`` production path and asserts the blocking
# adjudication fires (AGENTS.md §2.9 guard-liveness branch). The moment a
# drill is built, the corresponding rule_id leaves ``EE_NO_FIRE_DRILL_YET``
# and joins ``EE_FIRE_DRILL_COVERAGE``. Drills live in
# ``tests/test_ee_guard_liveness.py`` under the names recorded here, so the
# ratchet gate (every blocking code is either in DRILLS or in DEBT) is
# machine-enforced and the inspection surface is small.
EE_FIRE_DRILL_COVERAGE: Final[FrozenSet[str]] = frozenset(
    {
        # === Self-authored drills in tests/test_ee_guard_liveness.py ===
        # ``ee_replay_unsupported_action`` — drives HEADING_REPLACE through apply_ee_ops.
        # See ``test_ee_fire_drill_replay_unsupported_action_blocks``.
        "ee_replay_unsupported_action",
        # ``ee_replay_target_not_found`` — drives REPLACE targeting a non-existent
        # section through apply_ee_ops. See
        # ``test_ee_fire_drill_replay_target_not_found_blocks``.
        "ee_replay_target_not_found",
        # ``ee_replay_statute_title_noop`` — drives a REPLACE op targeting the
        # statute-title address whose payload text equals the current title.
        # See ``test_ee_fire_drill_replay_statute_title_noop_blocks``.
        "ee_replay_statute_title_noop",
        # ``ee_replay_unsupported_statute_title_action`` — drives a REPEAL op
        # targeting the statute-title address (a non-replace action against
        # the statute title). See
        # ``test_ee_fire_drill_replay_unsupported_statute_title_action_blocks``.
        "ee_replay_unsupported_statute_title_action",
        # ``ee_same_moment_cross_act_incompatible_payload_ambiguous`` — drives
        # two REPLACE ops on §5 from distinct affecting acts at the same
        # effective date through ``apply_ee_ops`` and asserts the blocking
        # §1.7 same-moment finding fires. See
        # ``tests/test_ee_same_moment_ambiguity.py::test_two_distinct_acts_replace_same_target_same_effective_date_emits_ambiguity_finding``.
        "ee_same_moment_cross_act_incompatible_payload_ambiguous",
        # === Crash-path drills (via monkeypatched replay_ee_to_pit) ===
        # ``ee_oracle_parse_failed`` — monkeypatches parse_ee_statute to raise
        # on oracle XML; asserts the blocking adjudication fires.
        # See ``test_ee_fire_drill_oracle_parse_failed_blocks``.
        "ee_oracle_parse_failed",
        # ``ee_consistency_check_failed`` — monkeypatches verify_consistency to
        # raise; asserts the blocking adjudication fires.
        # See ``test_ee_fire_drill_consistency_check_failed_blocks``.
        "ee_consistency_check_failed",
        # === Existing-tests-registered drills (verified production-path) ===
        # Each rule_id below is driven through the full production path by an
        # existing EE test (apply_ee_ops / parse_ee_amendment_ops / replay_ee_to_pit /
        # _ee_filter_cancelled_pending_refs / old_format_lower_op_texts /
        # _precompose_pending_source_act_commencement). The drill name listed in
        # the comment is the canonical production-path witness locating the
        # violator + the assert. Cross-referenced by the
        # ``notes_internal/_cross_ref_drills.py`` AST scan (2026-06-26).
        # ``ee_ambiguous_single_occurrence_text_replace`` —
        # ``test_exact_target_insert_after_with_repeated_source_surface_emits_ambiguity``
        "ee_ambiguous_single_occurrence_text_replace",
        # ``ee_amendment_parse_failed`` —
        # ``test_replay_ee_to_pit_adjudicates_amendment_parse_failure``
        "ee_amendment_parse_failed",
        # ``ee_amendment_source_fetch_failed`` —
        # ``test_replay_ee_to_pit_adjudicates_amendment_fetch_failure`` (monkeypatches
        # fetch to raise; asserts the parse-fail adjudication fires).
        "ee_amendment_source_fetch_failed",
        # ``ee_cancelled_pending_ref_metadata_parse_failed`` —
        # ``test_filter_cancelled_pending_refs_records_metadata_parse_failure_and_retains_ref``
        "ee_cancelled_pending_ref_metadata_parse_failed",
        # ``ee_cancelled_pending_ref_source_fetch_failed`` —
        # ``test_filter_cancelled_pending_refs_records_source_fetch_failure_and_retains_ref``
        "ee_cancelled_pending_ref_source_fetch_failed",
        # ``ee_flat_part_repeal_span`` —
        # ``test_repeal_flat_part_marker_removes_owned_section_run_until_next_part``
        "ee_flat_part_repeal_span",
        # ``ee_implicit_division_sequence_relabel_after_high_jagu_insert`` —
        # ``test_high_division_insert_relabels_unique_duplicate_division_suffix_with_adjudication``
        "ee_implicit_division_sequence_relabel_after_high_jagu_insert",
        # ``ee_inline_item_replace_singleton_subsection`` —
        # ``test_replace_section_item_recovers_inline_singleton_subsection_item``
        "ee_inline_item_replace_singleton_subsection",
        # ``ee_labelled_item_replacement_payload_selection`` —
        # ``test_replace_item_selects_matching_label_from_multi_item_payload``
        "ee_labelled_item_replacement_payload_selection",
        # ``ee_overbroad_container_replace_blocked`` —
        # ``test_replace_blocks_child_payload_from_overwriting_part_container``
        "ee_overbroad_container_replace_blocked",
        # ``ee_parse_old_format_unparsed_meta_rejected`` —
        # ``test_old_format_lower_op_texts_records_rejected_unparsed_meta``
        "ee_parse_old_format_unparsed_meta_rejected",
        # ``ee_pending_source_act_commencement_source_fetch_failed`` —
        # ``test_precompose_pending_source_act_commencement_records_fetch_failure``
        "ee_pending_source_act_commencement_source_fetch_failed",
        # ``ee_plural_item_replace_range_omits_inserted_labels`` —
        # ``test_plural_item_replace_range_removes_omitted_inserted_item_labels``
        "ee_plural_item_replace_range_omits_inserted_labels",
        # ``ee_plural_subsection_replace_extra_payload_label`` —
        # ``test_replace_extra_plural_subsection_payload_label_inserts_absent_subsection``
        "ee_plural_subsection_replace_extra_payload_label",
        # ``ee_ref_slice_operation_filtered`` —
        # ``test_parse_old_format_ref_slice_drop_uses_ref_slice_filtered_adjudication``
        "ee_ref_slice_operation_filtered",
        # ``ee_replay_meta_non_body_skipped`` —
        # ``test_apply_ee_ops_records_meta_as_non_body_skip_not_unsupported``
        "ee_replay_meta_non_body_skipped",
        # ``ee_replay_noop`` —
        # ``test_apply_ee_ops_records_unresolved_target_and_noop``
        "ee_replay_noop",
        # ``ee_replay_unparsed_operation_skipped`` —
        # ``test_apply_ee_ops_records_unparsed_meta_as_coverage_skip_not_non_body``
        "ee_replay_unparsed_operation_skipped",
        # ``ee_replay_unsupported_heading_target`` —
        # ``test_ee_apply_unsupported_heading_target_records_adjudication_not_warning``
        "ee_replay_unsupported_heading_target",
        # ``ee_section_item_replace_unique_descendant_item`` —
        # ``test_apply_ee_ops_resolves_section_item_replace_to_unique_descendant_item``
        "ee_section_item_replace_unique_descendant_item",
        # ``ee_source_case_only_text_replace`` —
        # ``test_apply_ee_ops_records_case_only_source_text_recovery``
        "ee_source_case_only_text_replace",
        # ``ee_source_local_global_text_replace_selector_exclusion_inferred`` —
        # ``test_parse_ee_amendment_ops_keeps_selector_exclusion_out_of_global_replay_scope``
        "ee_source_local_global_text_replace_selector_exclusion_inferred",
        # ``ee_subsection_table_only_replace_preserve_intro`` —
        # ``test_subsection_table_only_replace_preserves_existing_intro``
        "ee_subsection_table_only_replace_preserve_intro",
        # ``ee_temporal_source_scan_failed`` —
        # ``test_replay_ee_to_pit_adjudicates_temporal_source_scan_failure``
        "ee_temporal_source_scan_failed",
        # ``ee_text_replace_numbered_subsection_for_item_target_by_old_text`` —
        # ``test_apply_ee_ops_retargets_section_item_text_replace_to_same_number_subsection_old_text``
        "ee_text_replace_numbered_subsection_for_item_target_by_old_text",
        # ``ee_text_replace_unique_descendant_item_by_old_text`` —
        # ``test_apply_ee_ops_retargets_section_item_text_replace_to_unique_descendant_old_text``
        "ee_text_replace_unique_descendant_item_by_old_text",
    }
)


# ---------------------------------------------------------------------------
# Debt admission: the conscious NO_FIRE_DRILL_YET allowlist
# ---------------------------------------------------------------------------
#
# Each row is (reason_or_ticket, last_reviewed_date). The reason carries the
# concrete next step — NOT a generic "needs a fixture". The date is the most
# recent conscious re-admission.
#
# Initial baseline (2026-06-24): every blocking code is debt-admitted; no
# fire-drills exist yet. The committed ceiling matches this cardinality so
# the allowlist may shrink (drills are built) but never silently grow past
# the baseline. As future work drills codes one by one, both the ceiling and
# the allowlist entry come down together.
_EE_DRILL_FAMILY_HINT = "drill needs a known-violator witness statute + replay_ee_to_pit run asserting the blocking adjudication fires"

EE_NO_FIRE_DRILL_YET: Dict[str, tuple[str, str]] = {
    # The EE guard-liveness ratchet has achieved 100% fire-drill coverage:
    # every blocking rule_id in ``EE_BLOCKING_RULE_IDS`` is registered in
    # ``EE_FIRE_DRILL_COVERAGE``. This dict is empty at baseline. New
    # blocking codes that land in source without a corresponding drill
    # must be added here as a conscious debt-admission (with a stated
    # reason and last-reviewed date) — the partition ratchet gate
    # (``test_ee_blocking_code_inventory_is_fully_partitioned``) catches
    # any blocking code that violates this invariant.
}

# Committed monotone-decreasing ceiling over the NO_FIRE_DRILL_YET debt
# allowlist. The allowlist may shrink (a drill is built and the entry
# removed) but may never grow past the ceiling, and the ceiling itself only
# ratchets down. To admit new debt you must first pay down existing debt
# (drill an existing entry); the allowlist cannot silently grow.
EE_NO_FIRE_DRILL_CEILING: Final[int] = len(EE_NO_FIRE_DRILL_YET)


def enumerate_ee_blocking_rule_ids() -> FrozenSet[str]:
    """Return the EE blocking-rule_id inventory.

    A frozen set so callers can compare without the option to mutate it.
    """
    return EE_BLOCKING_RULE_IDS


__all__ = [
    "EE_BLOCKING_RULE_IDS",
    "EE_FIRE_DRILL_COVERAGE",
    "EE_NO_FIRE_DRILL_YET",
    "EE_NO_FIRE_DRILL_CEILING",
    "enumerate_ee_blocking_rule_ids",
]
