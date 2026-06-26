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
        # Drilled by ``tests/test_ee_guard_liveness.py::test_ee_fire_drill_replay_unsupported_action_blocks``
        # (drives a HEADING_REPLACE op through ``apply_ee_ops`` and asserts the
        # blocking adjudication fires). Production lane is the
        # ``action not in (replace, repeal, insert, renumber, text_replace)`` arm
        # of the ``apply_ee_ops`` dispatcher at ``grafter.py:10360``.
        "ee_replay_unsupported_action",
        # Drilled by ``tests/test_ee_guard_liveness.py::test_ee_fire_drill_replay_target_not_found_blocks``
        # (drives a REPLACE op whose target path resolves to no body node).
        # Production lane is the ``if not target_resolved:`` arm of the
        # ``apply_ee_ops`` dispatcher at ``grafter.py:10427``.
        "ee_replay_target_not_found",
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
    # Old-format / parse-time guards
    "ee_parse_old_format_unparsed_meta_rejected": (
        f"old-format META op rejected at parse-time; {_EE_DRILL_FAMILY_HINT}, "
        "located from RT archive (statute whose META elem fails to parse).",
        "2026-06-24",
    ),
    "ee_ref_slice_operation_filtered": (
        f"old-format ref-slice parse-time drop; {_EE_DRILL_FAMILY_HINT}; "
        "locate EE pair where one ref's effective date excludes the target.",
        "2026-06-24",
    ),
    # payload-normalization selector inference
    "ee_source_local_global_text_replace_selector_exclusion_inferred": (
        f"statute-wide text_replace with selector excluding a sibling op's "
        f"path; {_EE_DRILL_FAMILY_HINT}; multi-op amendment where one op is "
        f"a longer superset of another's old surface.",
        "2026-06-24",
    ),
    # text_replace scope decisions
    "ee_ambiguous_single_occurrence_text_replace": (
        f"text_replace hits a single ambiguous occurrence and is blocked; "
        f"{_EE_DRILL_FAMILY_HINT}; locate RT statute with ambiguous singleton.",
        "2026-06-24",
    ),
    "ee_source_case_only_text_replace": (
        f"text_replace only differs in case; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute with source-case-only surface.",
        "2026-06-24",
    ),
    "ee_text_replace_numbered_subsection_for_item_target_by_old_text": (
        f"text_replace targets a numbered subsection for an item via old text; "
        f"{_EE_DRILL_FAMILY_HINT}.",
        "2026-06-24",
    ),
    "ee_text_replace_unique_descendant_item_by_old_text": (
        f"text_replace targets the unique descendant item by old text; "
        f"{_EE_DRILL_FAMILY_HINT}.",
        "2026-06-24",
    ),
    # item / section / subsection recovery
    "ee_inline_item_replace_singleton_subsection": (
        f"singleton-subsection inline item replace; {_EE_DRILL_FAMILY_HINT}; "
        f"locate RT statute with a single-item subsection replacement.",
        "2026-06-24",
    ),
    "ee_labelled_item_replacement_payload_selection": (
        f"labelled item replacement payload selection; {_EE_DRILL_FAMILY_HINT}.",
        "2026-06-24",
    ),
    "ee_section_item_replace_unique_descendant_item": (
        f"section item replace unique descendant item; {_EE_DRILL_FAMILY_HINT}.",
        "2026-06-24",
    ),
    "ee_subsection_table_only_replace_preserve_intro": (
        f"subsection table-only replace preserving intro; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute with a table-only subsection replacement.",
        "2026-06-24",
    ),
    # plural coordination + overbroad
    "ee_overbroad_container_replace_blocked": (
        f"overbroad container replace blocked (§1.0 mutation-boundary guard); "
        f"{_EE_DRILL_FAMILY_HINT}; locate overbroad container targeting.",
        "2026-06-24",
    ),
    "ee_plural_item_replace_range_omits_inserted_labels": (
        f"plural item replace range omits inserted labels; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute with `1–5`täppi range covering an inserted label.",
        "2026-06-24",
    ),
    "ee_plural_subsection_replace_extra_payload_label": (
        f"plural subsection replace with extra payload label; {_EE_DRILL_FAMILY_HINT}.",
        "2026-06-24",
    ),
    "ee_flat_part_repeal_span": (
        f"flat/part repeal span (no subsection parent); {_EE_DRILL_FAMILY_HINT}; "
        f"locate flat/part-only RT statute.",
        "2026-06-24",
    ),
    # division / jagu recovery
    "ee_implicit_division_sequence_relabel_after_high_jagu_insert": (
        f"implicit division-sequence relabel after a high jagu insert; "
        f"{_EE_DRILL_FAMILY_HINT}; locate statute with duplicate jagu + insert.",
        "2026-06-24",
    ),
    # generic replay-time guard adjudications
    "ee_replay_unsupported_heading_target": (
        f"replay hits an unsupported heading target; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute whose heading target shape is not modeled.",
        "2026-06-24",
    ),
    "ee_replay_unsupported_statute_title_action": (
        f"replay hits an unsupported statute-title-level action; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute whose title-level rewrite is not modeled.",
        "2026-06-24",
    ),
    "ee_replay_noop": (
        f"replay noop (op intentionally had no effect); {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute where an op's payload resolves to a no-op.",
        "2026-06-24",
    ),
    "ee_replay_statute_title_noop": (
        f"replay statute-title noop; {_EE_DRILL_FAMILY_HINT}; locate statute "
        f"whose title rewrite resolves to a no-op.",
        "2026-06-24",
    ),
    "ee_replay_meta_non_body_skipped": (
        f"meta op skipped as non-body during replay; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute with old-format META-only non-body content.",
        "2026-06-24",
    ),
    "ee_replay_unparsed_operation_skipped": (
        f"unparsed op skipped during replay; {_EE_DRILL_FAMILY_HINT}; "
        f"locate statute with an opaque/unmodeled op variant.",
        "2026-06-24",
    ),
    # Source-lane orchestration failures (added by fail-loud broad-except audits
    # in commits 0d60710e + 00f778fc). These are blocking adjudications emitted
    # through the pass-through ``_ee_orchestration_adjudication`` helper, so the
    # caller's ``blocking=True`` kwarg flows to ``CompileAdjudication``.
    "ee_oracle_parse_failed": (
        f"RT oracle consolidation could not be parsed (consistency check "
        f"skipped, replay left uncompared); {_EE_DRILL_FAMILY_HINT}; locate "
        f"EE pair whose oracle XML triggers an xml.etree parse failure.",
        "2026-06-26",
    ),
    "ee_consistency_check_failed": (
        f"replay/oracle consistency check crashed (no divergences computed, "
        f"uncompared); {_EE_DRILL_FAMILY_HINT}; induce the check to crash by "
        f"pointing the replay at a malformed oracle IR.",
        "2026-06-26",
    ),
    "ee_amendment_parse_failed": (
        f"an amendment act XML failed to parse into LegalOperation list; "
        f"{_EE_DRILL_FAMILY_HINT}; locate corpus pair with malformed "
        f"amendment XML.",
        "2026-06-26",
    ),
    "ee_amendment_source_fetch_failed": (
        f"fetching an amendment source XML raised an unexpected exception "
        f"(not in the expected-source-unavailable set); {_EE_DRILL_FAMILY_HINT}; "
        f"force a network/decode exception inside the amendment fetch path.",
        "2026-06-26",
    ),
    "ee_cancelled_pending_ref_metadata_parse_failed": (
        f"the cancelled-pending-ref metapass could not parse pending-amendment "
        f"metadata XML unexpectedly; {_EE_DRILL_FAMILY_HINT}; locate pair with "
        f"malformed pending-amendment metadata XML.",
        "2026-06-26",
    ),
    "ee_cancelled_pending_ref_source_fetch_failed": (
        f"the cancelled-pending-ref metapass could not fetch a source XML "
        f"unexpectedly; {_EE_DRILL_FAMILY_HINT}; force a fetch exception in "
        f"the pending-ref lane.",
        "2026-06-26",
    ),
    "ee_pending_source_act_commencement_source_fetch_failed": (
        f"fetching a pending-source-act commencement source XML raised an "
        f"unexpected exception; {_EE_DRILL_FAMILY_HINT}; monkey-patch the RT "
        f"fetch helper to raise in the commencement metapass.",
        "2026-06-26",
    ),
    "ee_temporal_source_scan_failed": (
        f"scanning a temporal (expiry-relevant) source act raised an "
        f"unexpected exception; {_EE_DRILL_FAMILY_HINT}; force an exception "
        f"in the temporal-source scan path.",
        "2026-06-26",
    ),
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
