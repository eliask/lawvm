"""Norway (NO) believed_spec catalog — the discovered-spec hypotheses, one per rule.

This is a standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS``.
The main ledger's NO adapter will guard-import ``_NO_RULE_SPECS`` once its dispatch is
generalized; nothing here imports the norway frontend, so it carries no heavy deps and
stays conflict-free with parallel ``spec_ledger.py`` edits.

Voice and contract (see ``spec_ledger.py`` and ``notes_internal/SPEC_DISCOVERY_DESIGN.md``):
each entry is a one-line, falsifiable hypothesis about the *legal-amendment semantics*
the witness rule encodes — the believed spec the compiler is testing against the
authoritative Lovdata consolidated law. NO replays as consistency verification
against the live consolidated text (see ``notes/NORWAY_LAWVM_STATUS.md``), so these
describe genuine amendment semantics, not editorial conventions. Grounded in the
rule-emitting code under ``src/lawvm/norway/`` (acquisition, index, grafter, replay,
verify, statsrad, sources, inventory).

Coverage is anti-drift-guarded by ``tests/test_spec_ledger_no_catalog.py``: every
statically discoverable NO ``rule_id`` literal must have a non-empty entry, and
every key here must map to a real literal in the norway source (no dead entries).

Honest scope note — what is and is not statically enumerable.

* Discovery is by AST over ``src/lawvm/norway/*.py`` and captures every Str literal
  that ``startswith("no_")`` or ``startswith("no_verify.")``. The rule-id population
  is therefore the union of:

    - module-level ``NO_*_RULE_ID`` constants aliased to a string (e.g.
      ``NO_PARSE_STRUCTURED_TARGET_REBOUND_FROM_LEAD``) and their inline twin at
      the call site ``rule_id="no_parse_..."``;
    - inline ``rule_id=`` / ``kind=`` / ``name=`` string literals at emit sites
      (the ``ComparisonNormalizationRule.name`` and the ``NOFilteredDivergence.rule_id``
      literals under ``no_verify.``);
    - ``detail={"rule_id": "no_..."}`` payloads on action_family /
      migration_or_lineage / ontology_normalization / target_resolution_recovery
      findings, where the detail rule ids name the specific recovery contract while
      the ``kind`` (top-level) identifies the family.

* Three ``"no_..."`` literals are deliberately NOT rule ids and are excluded from
  the coverage denominator:
    - ``no_amendments`` — a replay-status enum value returned by
      ``_base_replay_status_from_statuses`` (``commencement.py``). Not a hypothesis.
    - ``no_list_items`` — a ``stopped_reason`` enum value (``statsrad.py``). Not a
      hypothesis.
    - ``no_replay_`` — a *prefix* matched by ``kind.startswith("no_replay_")`` in
      the diagnostic family-stratification path (``grafter.py``); the bare prefix
      is never an emitted rule id, only its suffixed instances are.

* Dynamic op-id prefixes: there is no Norway counterpart of Estonia's
  ``ee_snap_{n}``. Norway does not synthesize prefix+runtimesuffix op ids, so no
  dynamic-prefix exclusion registry is required.

Every ``"no_*"`` / ``"no_verify.*"`` literal maps to exactly one believed-spec
hypothesis here.
"""
from __future__ import annotations

from typing import Dict

# Believed-spec hypothesis per NO witness_rule_id / detail rule_id / projection
# rule_id. Keys are the literal rule-id strings bound to ``NO_*_RULE_ID`` constants
# (or passed inline as ``rule_id=`` / ``kind=`` / ``name=``) across
# ``src/lawvm/norway/``.  Each value is a falsifiable one-line claim about the law.
_NO_RULE_SPECS: Dict[str, str] = {
    # --- Acquisition / index / inventory / sources -------------------------------
    "no_acquisition_duplicate_logical_locator": (
        "A Norway artifact emitted a duplicate logical locator across acquisition "
        "attempts; both attempt rows are recorded, not collapsed/silently dropped."
    ),
    "no_amendment_index_no_change_ops": (
        "A Norway amendment artifact in the index yielded zero document-change "
        "operations; the artifact is recorded as a no-op finding, not silently "
        "absorbed into the denominator."
    ),
    "no_amendment_index_unmapped_lovtidend_xml_member": (
        "A Norway Lovtidend XML member filename could not be mapped to a law or "
        "amendment source id; recorded as blocking source-pathology under strict "
        "mode, never silently coerced."
    ),
    "no_amendment_index_unrecognized_amendment_locator": (
        "A Norway amendment index artifact whose member name and locator both "
        "failed to identify an amendment lane is recorded as a skipped acquisition."
    ),
    "no_current_law_id_parse_marker_fallback_used": (
        "Norway current-law statute id parsing fell back to a marker-bearing "
        "fallback path after the canonical parse failed but operative content is "
        "present; the fallback is recorded so the artifact stays owned."
    ),
    "no_current_law_id_parse_skipped": (
        "Norway current-law statute id parsing was skipped (canonical parse failed "
        "and no marker fallback existed); blocking source-pathology."
    ),
    "no_current_law_title_parse_skipped": (
        "Norway current-law title extraction skipped an artifact whose statute "
        "parse failed; blocking source-pathology."
    ),
    "no_ingest_existing_locator_skipped": (
        "Norway ingestion skipped an artifact whose locator was already present "
        "(skip_existing=True); recorded as transport_cleanup so the skip is "
        "auditable, not silent."
    ),
    "no_ingest_unmapped_xml_member": (
        "Norway Lovdata XML member filename could not be mapped to a legal source "
        "id during ingestion; blocking source-pathology."
    ),
    "no_inventory_current_law_id_artifact_fallback_used": (
        "Norway inventory used current artifact locators as a fallback because "
        "current-law ids could not be resolved directly; blocking source-pathology "
        "that affects the inventory denominator."
    ),
    "no_source_lane_selected_conflicting_duplicates": (
        "A Norway acquisition selected no source lane among conflicting duplicate "
        "logical locators; the selected-lane value records the conflict rather "
        "than silently picking one."
    ),
    "no_statsrad_event_artifact_invalid_json": (
        "A Norway statsrad event artifact failed JSON decoding; recorded with the "
        "parse error so the artifact is owned, not silently skipped."
    ),
    "no_statsrad_event_artifact_invalid_utf8": (
        "A Norway statsrad event artifact failed UTF-8 decoding; recorded with the "
        "decode error so the artifact is owned, not silently skipped."
    ),
    "no_statsrad_event_artifact_missing_payload": (
        "A Norway statsrad event locator had no stored payload; recorded as a "
        "missing acquisition so the locator is owned, not silently skipped."
    ),
    "no_statsrad_event_artifact_non_list": (
        "A Norway statsrad event artifact root was not a list; recorded as a "
        "structural source-pathology, not silently coerced."
    ),
    "no_statsrad_event_item_non_object": (
        "A Norway statsrad event artifact item was not an object; recorded with "
        "the offending index, not silently coerced."
    ),
    "no_statsrad_extract_missing_raw_artifact": (
        "A Norway statsrad article raw HTML artifact was missing; recorded so the "
        "missing acquisition is owned, not silently skipped."
    ),
    "no_statsrad_extract_missing_record_artifact": (
        "A Norway statsrad article metadata record artifact was missing; recorded "
        "so the missing acquisition is owned, not silently skipped."
    ),
    # --- Parse / grafter --------------------------------------------------------
    "no_parse_action_recovered_from_structured_lead": (
        "A Norway structured amendment lead carried an action needing normalization; "
        "the recovered action-family is recorded as a finding so the original "
        "intent stays traceable."
    ),
    "no_parse_cross_base_structured_renumber_skipped": (
        "A Norway structured renumber crossed base-act boundaries (source or "
        "destination on a different statute); it is skipped, never applied across "
        "an unrelated act."
    ),
    "no_parse_cross_base_structured_target_skipped": (
        "A Norway structured target referenced a different base act than the lead; "
        "the spec is skipped with a typed finding, not applied cross-base."
    ),
    "no_parse_document_change_base_unresolved": (
        "A Norway structured document-change lead referenced a missing or "
        "unmappable base act; the spec is skipped, failing forward to a finding "
        "instead of applying to an unresolved base."
    ),
    "no_parse_malformed_structured_renumber_attr_skipped": (
        "A Norway structured renumber attribute had a malformed token shape "
        "(e.g. trailing separators); skipped with a typed finding, not coerced."
    ),
    "no_parse_replace_promoted_to_insert_for_same_target_renumber": (
        "A Norway REPLACE targeting the same address as a RENUMBER in the same "
        "group is compiled as an INSERT at the newly-renumbered label; the "
        "action-family conversion is recorded (action_family_recovery)."
    ),
    "no_parse_structured_target_rebound_from_lead": (
        "A Norway structured amendment lead resolved to a target via lead-context "
        "rebound; recorded as target_resolution_recovery so the rebound is "
        "auditable, not silent."
    ),
    "no_parse_unresolved_structured_renumber_skipped": (
        "A Norway structured renumber could not lower its source or destination "
        "path; skipped with a typed finding, not coerced."
    ),
    "no_parse_unresolved_structured_target_skipped": (
        "A Norway structured target could not be lowered into a path; skipped "
        "with a typed finding, not coerced."
    ),
    "no_parse_unstructured_lead_base_unresolved": (
        "An unstructured Norway amendment lead looked operative but no base act "
        "could be resolved; recorded as a parse finding, not silently discarded."
    ),
    "no_parse_unstructured_lead_unmatched": (
        "An unstructured Norway amendment lead looked operative but matched no "
        "supported lowering family; recorded as a parse finding, not silently "
        "discarded."
    ),
    "no_parse_unstructured_payload_unresolved": (
        "An unstructured Norway heading-replacement lead resolved a target but no "
        "heading payload could be extracted; recorded with target evidence, not "
        "silently dropped."
    ),
    "no_parse_unstructured_renumber_arity_mismatch_skipped": (
        "An unstructured Norway repeal/renumber lead resolved unequal source and "
        "destination target counts; unmatched targets are not compiled and the "
        "mismatch is recorded."
    ),
    # --- Sort-order reconciliation ---------------------------------------------
    "no_sort_order_spurious_roman_single_letter_recheck": (
        "A Norway sibling-group ordering flagged by the litra sort key is "
        "re-classified as correctly roman-numeral-ordered (i, ii, …, v); the "
        "spurious flag is recorded as a nonblocking reclassification."
    ),
    # --- Replay / apply recovery -----------------------------------------------
    "no_replay_contingent_commencement_skipped": (
        "A Norway amendment whose entry-into-force is contingent on royal decree "
        "is skipped from replay until the override sidecar supplies an effective "
        "date; the skip is recorded, never silent."
    ),
    "no_replay_future_effective_skipped": (
        "A Norway amendment with an effective date after the requested as-of date "
        "is skipped; recorded, never silent."
    ),
    "no_replay_missing_amendment_source": (
        "A Norway amendment referenced in the index had no locally available "
        "source artifact; recorded as blocking acquisition, never silent."
    ),
    "no_replay_no_matching_change_group": (
        "A Norway amendment artifact compiled zero change-group matches; the "
        "no-op replay is recorded as a finding so the artifact stays owned."
    ),
    "no_replay_unknown_effective_skipped": (
        "A Norway amendment's effective status flag was not one of "
        "{contingent, dated, immediate, override}; the unknown status is "
        "recorded as blocking, never silently guessed."
    ),
    "no_repeal_payload_dropped": (
        "A Norway REPEAL/TEXT_REPEAL op arrived at the generic structured-spec mint "
        "boundary carrying a non-None payload (the candidate map is consulted for all "
        "actions); the payload is coerced to None — a repeal removes its target, so a "
        "content payload is contradictory — and the drop is recorded as a non-blocking "
        "adjudication, never silently discarded."
    ),
    "no_replay_insert_occupied_direct_child_replaced": (
        "A Norway INSERT landed on an occupied direct child; replay recovers by "
        "replacing that child, recording the action-family conversion "
        "(insert→replace) rather than silently overwriting it."
    ),
    "no_replay_insert_occupied_target_replaced": (
        "A Norway INSERT landed on an occupied single target; replay recovers by "
        "replacing the target, recording the action-family conversion "
        "(insert→replace) rather than silently overwriting it."
    ),
    "no_replay_renumber_occupied_destination_removed": (
        "A Norway RENUMBER landed on an occupied destination that was not itself "
        "moved by the same group; replay clears the destination and records the "
        "removal as a migration_or_lineage finding rather than silently destroying "
        "it."
    ),
    "no_replay_replace_recovered_by_insert": (
        "A Norway REPLACE whose target is absent is recovered by an INSERT at the "
        "missing location, recording the action-family conversion "
        "(replace→insert) rather than failing silently or widening scope."
    ),
    "no_replay_sentence_children_materialized": (
        "A Norway sentence-level operation targets a shallow sentence host with "
        "sentence children; replay materializes the children and records the "
        "ontology_normalization finding rather than silently picking one."
    ),
    "no_replay_shallow_sentence_target_rebound": (
        "A Norway section-level sentence target resolves through the section's "
        "unique direct sentence host; replay records the target_resolution_recovery "
        "finding rather than silently re-routing."
    ),
    "no_sentence_text_materialized_for_sentence_target": (
        "A Norway sentence-level operation targets a section whose sentence host "
        "was not materialized; replay materializes the sentence text from the "
        "unique host and records the ontology_normalization finding."
    ),
    "no_sentence_text_materialized_for_shallow_sentence_target": (
        "A Norway sentence-level operation targets a shallow sentence host; "
        "replay materializes sentence text from that host and records the "
        "ontology_normalization finding."
    ),
    "no_shallow_sentence_target_rebound_to_unique_host": (
        "A Norway shallow sentence target rebinds to the section's unique direct "
        "sentence host; replay records the target_resolution_recovery finding "
        "rather than silently re-keying the address."
    ),
    # --- Detail rule ids on action_family / migration_or_lineage records ---------
    # These are the detail payload rule ids that name the specific recovery
    # contract paired with their family-level ``kind`` counterparts above.
    "no_insert_occupied_direct_child_replace": (
        "Detail rule id on the action_family_recovery record for "
        "no_replay_insert_occupied_direct_child_replaced — names the specific "
        "insert→replace recovery contract on the direct child path."
    ),
    "no_insert_occupied_target_replace": (
        "Detail rule id on the action_family_recovery record for "
        "no_replay_insert_occupied_target_replaced — names the specific "
        "insert→replace recovery contract on the resolved target path."
    ),
    "no_renumber_occupied_destination_removed": (
        "Detail rule id on the migration_or_lineage recovery record for "
        "no_replay_renumber_occupied_destination_removed — names the specific "
        "destination-clearing recovery contract."
    ),
    "no_replace_missing_last_item_append_to_parent": (
        "Detail rule id on an insert-recovery of a missing-target replace that "
        "appended an item to the parent — names the specific replace→insert "
        "recovery contract on the last item slot."
    ),
    "no_replace_missing_section_insert": (
        "Detail rule id on an insert-recovery of a missing-target replace that "
        "inserted a section at the inferred parent — names the specific "
        "replace→insert recovery contract at section granularity."
    ),
    "no_replace_missing_sentence_append_to_resolved_parent": (
        "Detail rule id on an insert-recovery of a missing-target replace that "
        "inserted a sentence into a resolved parent — names the specific "
        "replace→insert recovery contract at sentence granularity."
    ),
    "no_replace_missing_sentence_append_to_shallow_host": (
        "Detail rule id on an insert-recovery of a missing-target replace that "
        "inserted a sentence into a shallow host — names the specific "
        "replace→insert recovery contract at sentence granularity on a shallow "
        "host."
    ),
    # --- Comparison normalization (verify.compare surface) ---------------------
    "no_compare_nbsp": (
        "Norway comparison text projects non-breaking spaces to ordinary spaces "
        "so equivalent wording is not flagged as a divergence."
    ),
    "no_compare_whitespace_collapse": (
        "Norway comparison text collapses whitespace runs so equivalent wording "
        "is not flagged as a divergence."
    ),
    "no_compare_punctuation_spacing": (
        "Norway comparison text removes spaces before punctuation so equivalent "
        "wording is not flagged as a divergence."
    ),
    "no_compare_open_paren_spacing": (
        "Norway comparison text removes spaces after opening parenthesis so "
        "equivalent wording is not flagged as a divergence."
    ),
    "no_compare_inline_footnote_marker": (
        "Norway comparison text removes inline numeric footnote markers between "
        "sentences so equivalent wording is not flagged as a divergence."
    ),
    "no_compare_standalone_footnote_marker": (
        "Norway comparison text removes standalone numeric footnote markers after "
        "punctuation so equivalent wording is not flagged as a divergence."
    ),
    "no_compare_numeric_hyphen_gap": (
        "Norway comparison text closes a spacing gap before a hyphen after a "
        "digit so equivalent wording is not flagged as a divergence."
    ),
    "no_compare_other_laws_placeholder_dash_tail": (
        "Norway comparison text suppresses pure dash tails inside other-laws "
        "placeholder clauses so the placeholder is not flagged as a divergence."
    ),
    "no_compare_trailing_footnote_marker": (
        "Norway comparison text removes trailing numeric footnote markers after "
        "terminal punctuation so equivalent wording is not flagged as a "
        "divergence."
    ),
    # --- Verify projections (no_verify.* — emitted by NOCompareProjection) ------
    "no_verify.compare_repealed_shell_blanked": (
        "A Norway proforma 'repealed-shell' provision (archived text body for a "
        "repealed section) is blanked on the compare surface so the archived "
        "shell is not a divergence against the live text."
    ),
    "no_verify.compare_sentence_children_collapsed": (
        "Norway comparison collapses a parent's sentence children into the parent "
        "for the compare surface so a folded presentation is not a divergence."
    ),
    "no_verify.compare_nested_item_tail_suppressed": (
        "Norway comparison suppresses a nested item tail where replay and current "
        "diverge only in trailing container membership."
    ),
    "no_verify.compare_self_section_shell_blanked": (
        "A Norway proforma 'self-section shell' (cited section reference "
        "placeholder body) is blanked on the compare surface."
    ),
    "no_verify.compare_contingent_other_laws_placeholder_suppressed": (
        "A Norway contingent 'Kongen bestemmer' other-laws placeholder is "
        "suppressed on the compare surface because its content is not yet "
        "deterministically replayable."
    ),
    "no_verify.compare_definition_subsection_pairs_collapsed": (
        "Norway comparison collapses paired definition subsections whose "
        "replay-vs-current divergence is presentation-only."
    ),
    "no_verify.compare_other_laws_context_suppressed": (
        "Norway comparison suppresses other-laws contextual boilerplate that is "
        "not deterministically replayable."
    ),
    "no_verify.chapter_relocation_pair": (
        "Two divergences whose text matches at different chapter paths are "
        "paired as a chapter relocation, suppressed on the primary surface and "
        "recorded as a single relocation finding rather than two mismatches."
    ),
    "no_verify.annex_prefixed_relocation_pair": (
        "Two divergences whose text matches at non-container paths whose "
        "only difference is a Lovdata Vedlegg-annex-token prefix on the "
        "section label (e.g. chapter:v22c/section:v22c/a1 vs chapter:1/"
        "section:a1) are paired as an annex-encoded relocation and "
        "suppressed on the primary surface, recorded as a single filtered "
        "relocation receipt rather than two mismatches. Distinct from "
        "no_verify.chapter_relocation_pair, which pairs provisionally-"
        "relocated provisions whose section labels match exactly."
    ),
    "no_verify.prefix_descendant_suppressed": (
        "A Norway divergence whose address is a strict prefix of another raw "
        "divergence address is suppressed on the primary surface (the more "
        "specific divergence takes priority); the suppression is recorded as a "
        "filtered divergence with a receipt."
    ),
    "no_verify_source_signal_base_year_unresolved": (
        "A Norway base_id does not carry the canonical no/lov/YYYY-MM-DD-N "
        "form, so the source-signal inference cannot use an enactment year "
        "and falls through the sparse-indexed-history branch unconditionally. "
        "Recorded so the malformed base_id surfaces in verify diagnostics "
        "rather than silently behaving as 'year unknown'."
    ),
}
