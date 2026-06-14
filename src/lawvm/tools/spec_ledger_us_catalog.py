"""U.S. federal believed_spec catalog — the discovered-spec hypotheses, one per rule.

Standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS`` and of
``spec_ledger_uk_catalog`` / ``spec_ledger_ee_catalog``. The US adapter
(``lawvm.us_federal.spec_ledger_adapter``) guard-imports ``_US_RULE_SPECS`` from
here; nothing in this module imports the us_federal frontend, so it carries no heavy
deps and stays conflict-free with parallel edits under ``src/lawvm/us_federal/``.

Voice and contract (see ``spec_ledger.py`` and notes_internal/SPEC_DISCOVERY_DESIGN.md):
each entry is a one-line, falsifiable hypothesis about the *legal-amendment semantics*
(or the source-import / source-parse / coverage discipline) the witness rule encodes —
the believed spec the compiler is testing against the witness. The US witness is the
published USC after-edition (the dry-run oracle): an AGREES firing corroborates the
hypothesis, a residual contradicts it.

The US rule vocabulary splits into families:

* **amendatory lowering** (``us_amend_*``): the kernel's hypothesis about what a
  classified amendatory instruction does to the target section text. These are the
  rules the dry-run oracle most directly corroborates/contradicts.
* **amendatory findings** (``us_amendatory_*``): an instruction the kernel could not
  lower / could not resolve / does not target the US Code — a coverage frontier, not
  a materialization.
* **dry-run outcome rules** (``us_dry_run_*``): the per-section agreement/residual
  classifier — the AGREES rule is the corroborating witness, each residual rule is a
  named contradiction with a side-of-the-gap disposition.
* **sunset / temporal** (``us_sunset_*``): the F2 reclassification of an otherwise
  missing-source change as a temporary-provision reversion.
* **source import / parse** (``us_plaw_import_*`` / ``us_statute_import_*`` /
  ``us_usc_import_*`` / ``us_usc_*``): import-time and source-tree-parse hygiene findings
  (these fire during
  ingestion / parsing, not during dry-run, but are cataloged so a fired id is never a
  silent blind spot).
* **non-positive-law mapping** (``us_nonpositive_*``): the address-resolution
  hypotheses for amendments that target a non-positive-law title via Statutes-at-Large.
* **effect scan** (``us_effect_scan_*``): the candidate-derivation gate.
* **bench window** (``us_bench_window_*``): typed skips that keep an empty/missing
  window from masquerading as a zero evaluation.

Coverage is anti-drift-guarded by
``tests/test_jurisdiction_starter_us_federal_spec_ledger.py``: every statically
discoverable ``us_…`` witness rule_id literal under ``src/lawvm/us_federal/`` must have
a non-empty entry here, and every key here must map to a real literal (no dead
entries). The ``us_…`` literals that are NOT believed-spec witness rule ids are the
documented exclusions (``US_NON_RULE_LITERALS``):

* ``us_federal`` — the jurisdiction name / a provenance-tag token, not a rule id;
* ``us_federal_plaw_inventory`` — the inventory report ``kind`` tag;
* ``us_amendatory`` — a provenance tag on every lowered op (``provenance_tags``), not a
  witness rule id;
* ``us_dry_run_section_changed_set`` / ``us_dry_run_section_text`` — agreement-surface
  *identity* constants (the ``agreement_surface`` field value), not per-rule firings;
* ``us_dry_run_changed_section_set_matches_oracle`` /
  ``us_dry_run_changed_section_set_diverges_from_oracle`` — the whole-window
  mutation-boundary-proof outcome id (one per window, not a per-section ledger firing);
* ``us_dry_run_title`` — the constant prefix of an f-string ``proof_id`` /
  ``operation_id`` (``f"us_dry_run_title{title}_window"``), not a rule id.

(The f-string fragments that carry a ``:`` — e.g. ``us_dry_run:title`` — are excluded by
the discovery's ``:``-fragment filter, not enumerated here.)
"""
from __future__ import annotations

from typing import Dict, FrozenSet

# ``us_…`` literals under src/lawvm/us_federal/ that are deliberately NOT believed-spec
# witness rule ids (see the module docstring). The coverage test excludes these from the
# denominator and asserts they never appear as catalog keys.
US_NON_RULE_LITERALS: FrozenSet[str] = frozenset(
    {
        "us_federal",
        "us_federal_plaw_inventory",
        "us_amendatory",
        "us_dry_run_section_changed_set",
        "us_dry_run_section_text",
        "us_dry_run_changed_section_set_matches_oracle",
        "us_dry_run_changed_section_set_diverges_from_oracle",
        "us_dry_run_title",
    }
)

# Confidence tiers (mirror the NZ catalog's): ``certain`` = a direct, exactness-gated
# structural fact; ``heuristic`` = a believed editorial/lowering convention the oracle
# could legitimately render differently.
US_CONFIDENCE_CERTAIN = "certain"
US_CONFIDENCE_HEURISTIC = "heuristic"

# rule_id -> believed_spec prose. Each value is a falsifiable one-line claim.
_US_RULE_SPECS: Dict[str, str] = {
    # --- Amendatory lowering: the materialization hypotheses --------------------------
    "us_amend_strike_insert": (
        "A 'strike <old> and insert <new>' instruction replaces the first (or every, "
        "for 'each place it appears') occurrence of the quoted old text with the new "
        "text in the target section; a quoted block payload replaces the whole struck "
        "unit."
    ),
    "us_amend_strike": (
        "A 'strike <text>' instruction deletes the first (or every, for 'each place') "
        "occurrence of the quoted text from the target section."
    ),
    "us_amend_insert_after_anchor": (
        "An 'insert <new> after <anchor>' instruction places the new text immediately "
        "after the quoted anchor text in the target section."
    ),
    "us_amend_add_at_end": (
        "An 'add at the end' instruction appends the quoted payload (a block or a "
        "string) as a new child at the end of the target section."
    ),
    "us_amend_to_read": (
        "An 'amend ... to read as follows' instruction replaces the whole target "
        "section subtree with the quoted replacement block."
    ),
    "us_amend_repeal": (
        "A repeal instruction removes the target section (no quoted payload; the "
        "section's content is struck in full)."
    ),
    "us_amend_redesignate": (
        "A 'redesignate <from> as <to>' instruction renumbers the target node from its "
        "source address to the destination address."
    ),
    "us_amend_redesignate_range": (
        "A 'redesignating <X> through <Y> as <X'> through <Y'>' instruction emits one "
        "RENUMBER per member of the range, relabelling only each node's leading enumerator."
    ),
    "us_amend_strike_structural_unit": (
        "A 'strike subsection/paragraph (X)' instruction repeals the named structural "
        "node from the section body; striking an absent node is a typed no-op refusal, "
        "never an over-broad deletion."
    ),
    "us_amend_insert_node_after_unit": (
        "An 'insert after <anchor> the following: <block>' instruction splices the quoted "
        "payload node immediately after the anchor node in the section body."
    ),
    # --- Amendatory findings: the coverage frontier (no materialization) --------------
    "us_amendatory_unlowered": (
        "A classified amendatory instruction whose shape the lowerer does not yet "
        "support (e.g. a structural-unit strike with no quoted string): left as an "
        "unlowered finding rather than guessed."
    ),
    "us_amendatory_target_unresolved": (
        "A classified amendatory instruction whose target address could not be "
        "resolved to a concrete USC location."
    ),
    "us_amendatory_target_non_us_code": (
        "An amendatory instruction whose target is not a positive-law US Code title "
        "(it amends another law / a non-Code provision): out of the section-replay "
        "frame."
    ),
    # --- Dry-run outcome rules: the witness classifier --------------------------------
    "us_dry_run_section_materialized_text_matches_oracle": (
        "AGREES witness: the composed section text (before-text with all in-scope ops "
        "applied in source order) matches the oracle after-edition section text — the "
        "lowering hypotheses for that section are corroborated."
    ),
    "us_dry_run_residual_materialized_text_mismatch_with_oracle": (
        "Contradiction: the composed section text disagrees with a genuinely "
        "oracle-changed section (lawvm_wrong), or matches only after an OLRC editorial "
        "projection (quote-stripping / courtesy spacing), in which case the gap is on "
        "the oracle's editorial side (oracle_suspect)."
    ),
    "us_dry_run_residual_claimed_section_unchanged_in_oracle": (
        "Contradiction: the kernel claimed (materialized) a section the oracle did not "
        "change at all — a spurious claim, never an agreement."
    ),
    "us_dry_run_residual_oracle_changed_section_not_claimed": (
        "Contradiction (missing_source): the oracle changed a section the kernel never "
        "claimed — the honest lowering/coverage gap, no op was emitted for it."
    ),
    "us_dry_run_residual_match_text_not_found_in_before_section": (
        "Contradiction: an op's quoted match_text was not found in the before/running "
        "section text — the kernel refuses to fuzzy-match into a guess and surfaces a "
        "typed residual."
    ),
    "us_dry_run_residual_subsection_target_node_not_located_in_before_section": (
        "Contradiction: a sub-section-scoped op named a node (paragraph/clause/...) the "
        "before-section split does not expose, or whose text an earlier op already "
        "mutated — surfaced as a typed residual rather than an unscoped string replace."
    ),
    "us_dry_run_surface_not_replay_authorized": (
        "Invariant: the dry-run surface never authorizes actual replay — the gate is "
        "always closed (replay_authorized is False)."
    ),
    "us_dry_run_window_source_not_in_archive": (
        "A requested window source (a before/after edition or a PL blob) was absent "
        "from the archive: the window is refused loudly rather than run partially."
    ),
    # --- Dry-run typed refusals: not representable at section granularity --------------
    "us_dry_run_refused_target_outside_proof_title": (
        "Refusal: an op's target lies outside the title under proof — not materialized "
        "in this window."
    ),
    "us_dry_run_refused_target_section_not_present_in_before_edition": (
        "Refusal: the target section is not present in the before edition — the op has "
        "no section text to compose onto."
    ),
    "us_dry_run_refused_structural_op_not_representable_at_section_granularity": (
        "Refusal: a structural op (e.g. a sub-section redesignation) cannot be "
        "represented at the section-text granularity the dry-run materializes."
    ),
    "us_dry_run_refused_text_op_missing_text_patch": (
        "Refusal: a text op carries no text patch (no match/replacement) — nothing to "
        "materialize."
    ),
    # --- Sunset / temporal reclassification (F2) --------------------------------------
    "us_sunset_temporary_provision_reverted_to_prior_permanent": (
        "An otherwise missing-source oracle change is explained by the expiry of a "
        "temporary provision reverting the section to its prior permanent form (the "
        "temporal layer owns it; it is not a lowering gap)."
    ),
    "us_sunset_temporal_note_present_but_reversion_unproven": (
        "A temporal/sunset note is present on the section but the reversion to a prior "
        "permanent edition could not be proven — an ambiguous temporal residual, not a "
        "reversion claim."
    ),
    # --- PL import hygiene ------------------------------------------------------------
    "us_plaw_import_unrecognized_member": (
        "A Public Law USLM member element was not a recognized importable unit — "
        "skipped with a typed finding rather than silently dropped."
    ),
    "us_plaw_import_private_law_filtered": (
        "A private law was filtered out of the public-law import (private laws are not "
        "part of the public-law corpus)."
    ),
    "us_plaw_import_duplicate_logical_locator": (
        "Two imported PL members resolved to the same logical locator — the duplicate "
        "is flagged rather than overwriting."
    ),
    "us_plaw_import_existing_content_skipped": (
        "An imported PL member's content already exists in the archive — skipped as "
        "idempotent rather than re-written."
    ),
    # --- Statutes-at-Large import hygiene (older public laws) -------------------------
    "us_statute_import_volume_unreachable": (
        "A Statutes-at-Large volume USLM document could not be fetched from govinfo — "
        "recorded as a typed gap rather than producing a silent missing-law hole."
    ),
    "us_statute_import_volume_unparsable": (
        "A Statutes-at-Large volume USLM document did not parse as XML — refused loudly "
        "rather than importing partial/garbled slices."
    ),
    "us_statute_import_unidentified_plaw": (
        "A volume pLaw unit lacked a parseable congress/law-number identity — skipped "
        "with a typed finding rather than mis-filed."
    ),
    "us_statute_import_private_law_filtered": (
        "A private-law unit was filtered out of the public-law import (private laws are "
        "not part of the public-law corpus)."
    ),
    "us_statute_import_duplicate_logical_locator": (
        "Two pLaw units within one volume resolved to the same logical locator — the "
        "duplicate is flagged rather than overwriting."
    ),
    "us_statute_import_existing_content_skipped": (
        "A sliced public-law unit's content already exists in the archive — skipped as "
        "idempotent rather than re-written."
    ),
    "us_statute_import_congress_meta_mismatch": (
        "A pLaw unit's <congress> meta disagreed with its authoritative <citableAs> "
        "Public-Law citation — the citation is used for the canonical locator and the "
        "discarded meta value is recorded rather than silently mis-filing the law."
    ),
    # --- USC import hygiene -----------------------------------------------------------
    "us_usc_import_unrecognized_member": (
        "A USC edition member element was not a recognized importable section — "
        "skipped with a typed finding."
    ),
    "us_usc_import_member_identity_mismatch": (
        "An imported USC member's identity (title/section) did not match its expected "
        "address — flagged rather than mis-filed."
    ),
    "us_usc_import_existing_content_skipped": (
        "A USC member's content already exists in the archive — skipped as idempotent."
    ),
    "us_usc_import_source_unreadable": (
        "A USC edition source blob could not be read/parsed at import time — refused "
        "loudly rather than imported partially."
    ),
    # --- USC source-tree parse hygiene ------------------------------------------------
    "us_usc_subsection_parse_ambiguous": (
        "A section's sub-structure (leading (a)/(1)/(A)/(i) markers plus indent depth) "
        "did not map unambiguously to the USC address convention — emitted as a typed "
        "ambiguity finding rather than a guessed split."
    ),
    "us_usc_no_sections_found": (
        "A parsed USC title document yielded no sections — flagged (an empty parse is "
        "never silently a valid title)."
    ),
    "us_usc_duplicate_section_number": (
        "A parsed USC title carried two sections with the same number — flagged rather "
        "than one silently shadowing the other."
    ),
    "us_usc_source_credit_unparsed_public_law": (
        "A section's source-credit names a Public Law citation the witness extractor "
        "could not parse into a (congress, number) pair — surfaced rather than dropped "
        "from the witness delta."
    ),
    "us_usc_oracle_unavailable": (
        "The USC oracle edition needed to witness a window is unavailable — the check "
        "is refused rather than run against an absent oracle."
    ),
    # --- Non-positive-law title address resolution ------------------------------------
    "us_nonpositive_target_via_paren": (
        "A non-positive-law amendment's target classified-section was resolved via the "
        "parenthetical classification reference in the source credit."
    ),
    "us_nonpositive_target_via_href": (
        "A non-positive-law amendment's target was resolved via the USLM href "
        "classification link."
    ),
    "us_nonpositive_target_paren_href_agree": (
        "Both the parenthetical and the href classification references resolved to the "
        "same target — corroborated resolution."
    ),
    "us_nonpositive_target_paren_href_disagree": (
        "The parenthetical and href classification references disagreed on the target "
        "— a flagged ambiguity, never silently picking one."
    ),
    "us_nonpositive_target_unmapped": (
        "A non-positive-law amendment's target could not be mapped to a USC "
        "classified section by any channel — an unmapped finding."
    ),
    "us_nonpositive_target_note_only": (
        "A non-positive-law amendment maps only to a note (not an operative classified "
        "section) — out of the operative-section frame."
    ),
    # --- Effect scan ------------------------------------------------------------------
    "us_effect_scan_law_does_not_target_title": (
        "A scanned public law produced no effect candidates against the title under "
        "study (it does not amend that title)."
    ),
    # --- Bench window typed skips -----------------------------------------------------
    "us_bench_window_edition_not_in_archive": (
        "A bench window marked include=true whose before/after USC editions are absent "
        "from the archive — a typed skip, never a silently-zero evaluation."
    ),
    "us_bench_window_witness_delta_empty": (
        "A bench window whose derived witness delta is empty (the after edition "
        "credits no new public law) — a typed skip, never mistaken for 'ran and found "
        "nothing'."
    ),
}

# rule_id -> confidence tier. Amendatory lowering + dry-run text-mismatch are
# heuristic (a believed lowering/editorial convention the OLRC could render
# differently); the structural/refusal/import/skip facts are certain.
_US_RULE_CONFIDENCE: Dict[str, str] = {
    "us_amend_strike_insert": US_CONFIDENCE_HEURISTIC,
    "us_amend_strike": US_CONFIDENCE_HEURISTIC,
    "us_amend_insert_after_anchor": US_CONFIDENCE_HEURISTIC,
    "us_amend_add_at_end": US_CONFIDENCE_HEURISTIC,
    "us_amend_to_read": US_CONFIDENCE_HEURISTIC,
    "us_amend_repeal": US_CONFIDENCE_HEURISTIC,
    "us_amend_redesignate": US_CONFIDENCE_HEURISTIC,
    "us_amend_redesignate_range": US_CONFIDENCE_HEURISTIC,
    "us_amend_strike_structural_unit": US_CONFIDENCE_HEURISTIC,
    "us_amend_insert_node_after_unit": US_CONFIDENCE_HEURISTIC,
    "us_dry_run_residual_materialized_text_mismatch_with_oracle": US_CONFIDENCE_HEURISTIC,
    "us_nonpositive_target_via_paren": US_CONFIDENCE_HEURISTIC,
    "us_nonpositive_target_via_href": US_CONFIDENCE_HEURISTIC,
    "us_sunset_temporary_provision_reverted_to_prior_permanent": US_CONFIDENCE_HEURISTIC,
}


def us_confidence(rule_id: str) -> str:
    """Confidence tier for a rule id (default ``certain`` for cataloged structural facts)."""
    return _US_RULE_CONFIDENCE.get(rule_id, US_CONFIDENCE_CERTAIN)
