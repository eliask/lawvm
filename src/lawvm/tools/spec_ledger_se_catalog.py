"""Sweden (SE) believed_spec catalog — the discovered-spec hypotheses, one per rule.

A standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS`` and
``spec_ledger_uk_catalog.py``'s ``_UK_RULE_SPECS``. This module holds the one-line
falsifiable hypothesis per SE ``witness_rule_id`` discovered under
``src/lawvm/sweden/``. The main ledger's SE adapter can guard-import
``_SE_RULE_SPECS`` once its dispatch is generalized; nothing here imports the
sweden frontend, so it carries no heavy deps and stays conflict-free with
parallel ``spec_ledger.py`` edits.

Voice and contract (see ``spec_ledger.py``, ``spec_ledger_uk_catalog.py``,
``spec_ledger_ee_catalog.py``, and ``notes/SWEDEN_LAWVM_STATUS.md``):
each entry is a one-line, falsifiable hypothesis about the *legal-amendment
semantics* the witness rule encodes — the believed spec the compiler is testing
against the consolidated-text oracle. SE replays as consistency verification
against the authoritative SFS consolidated text surface (latest consolidation;
single-version oracle), so these describe genuine amendment semantics and
manual-compilation frontier states, not editorial conventions. Grounded in the
rule-emitting code under ``src/lawvm/sweden/``.

Anti-drift-guarded by ``tests/test_spec_ledger_se_catalog.py``: every statically
discoverable SE rule-id literal must have a non-empty entry, and every key here
must map to a real literal in the sweden source (no dead entries).

Honest scope note — what is and is not statically enumerable.

* SE call sites pass ``rule_id="..."`` / ``reason_code="..."`` / ``kind="..."``
  where the string is a literal, so the id *is* statically enumerable and is
  catalogued here.

* Dynamic construction families (f-string ``op_id`` prefixes like
  ``se_official_renumber_{sfs_id}_{src}->{dst}``, ``se_official_repeal_{...}``,
  ``se_official_insert_heading_{...}``, ``se_official_insert_appendix_{...}``,
  ``se_official_text_replace_{...}``, ``se_reverse_insert_{...}``,
  ``se_reverse_renumber_{...}``, ``se_reverse_heading_{...}``,
  ``se_reverse_appendix_{...}``, ``se_reverse_chain::``) are NOT believed-spec
  hypotheses — they are per-op instance identifiers constructed at compile time.
  Each is documented as a dynamic exclusion in the test's ``_NON_RULE_PREFIXES``
  set rather than fabricated per-instance.

* locator strings (``se_official_ops_locator``, ``se_rk_current_url``, etc.)
  and function names (``se_section_text_map``, ``se_legal_operation_from_dict``,
  ``se_pdf_bytes_to_text``, etc.) are excluded from the rule-id denominator
  because they carry a ``.`` (filename/locator shape) or are exported as
  ``__all__`` function names.

Every other ``"se_…"`` literal maps to exactly one believed-spec hypothesis here.
"""
from __future__ import annotations

from typing import Dict

# Believed-spec hypothesis per SE witness_rule_id.  Keys are the literal rule-id strings
# bound to ``se_*`` constants (or passed inline as ``rule_id=`` / ``reason_code=`` /
# ``kind=``) across ``src/lawvm/sweden/``.  Each value is a falsifiable one-line claim
# about the law.
_SE_RULE_SPECS: Dict[str, str] = {
    # --- Current-text parse surface residual families ------------------------------------
    "se_current_text_orphan_item_skipped": "A current-text item node whose parent subsection was not parsed is a typed residual, not silently dropped.",
    "se_current_text_orphan_temporal_marker_skipped": "A current-text temporal marker (U/I restriction) whose sibling section is not parsed is a typed residual, not silently dropped.",

    # --- Later-chain reverse-patch exceptions --------------------------------------------
    "se_later_chain_reverse_op_exception": "A later-chain reverse-patch op that raises during apply is a typed frontier residual, the recovery lane is bounded, the residual surfaces as evidence.",

    # --- Official-act payload-surface coercion (rows skipped as typed residuals) ----------
    "se_official_act_payload_row_duplicate_label": "A duplicate-label provision in a cached official-act JSON that is NOT a cross-reference continuation is a typed schema-drift diagnostic, not a silent fold.",
    "se_official_act_payload_row_invalid_shape": "A non-dict provision row in the official-act payload is a typed schema-diagnostic, skipped without inventing a payload.",
    "se_official_act_payload_row_skipped": "An official-act provision row that the payload-surface builder skips for an unspecified reason is a typed residual.",
    "se_official_act_payload_row_unlabeled": "An official-act provision row with an empty/missing label is a typed diagnostic, skipped without fabricating a label.",

    # --- Amendment register (RK current JSON) shape --------------------------------------
    "se_official_amendment_register_invalid_shape": "The RK amendment-register JSON root that isn't the expected ``hits`` container is a typed diagnostic, not silently defaulted.",
    "se_official_amendment_register_row_invalid_shape": "A non-dict amendment-register entry is a typed diagnostic, skipped without inventing the field set.",

    # --- Official SFS document acquisition -----------------------------------------------
    "se_official_artifacts_unavailable": "When the official SFS doc page + PDF + structured JSON sources are unavailable for a statute, the acquisition yields a typed unavailable residual, not a silent empty bundle.",
    "se_official_artifacts_fetch_failed": "When ``fetch_missing=True`` acquisition raises (network/HTTP/parse) inside ``fetch_se_official_artifacts``, the failure surfaces as a named acquisition-residual diagnostic on ``base_seed`` (sfs_id + error type + message), not a silent swallow that lets replay proceed against an empty base.",
    "se_official_base_ir_build_failed": "A base act for which the non-amending IR seed fails to construct is a typed acquisition failure, not a silent empty body.",
    "se_official_pdf_source_lane_fallback": "When the official SFS PDF is unreachable over HTTP, the acquisition falls back to the legacy SFS PDF index with a typed source-lane note.",
    "se_official_pdf_text_extraction_failed": "When pdftotext fails to extract text from a valid SFS PDF, the row is a typed extraction-failure residual, not a silent empty buffer.",
    "se_official_artifacts_force_reextract_overwrite": "When ``--force-reextract`` overlays prior bytes at a cached locator, the prior+new content hashes are recorded as a typed SEOverwriteEvent in the caller accumulator — never a silent in-place mutation (KNOW-01 monotonicity + §1.6 no unstated migration at the archive-write boundary).",

    # --- Clause-surface parse findings ---------------------------------------------------
    "se_official_clause_renumber_arity_mismatch": "A renumber enacting clause whose source-label list and destination-label list have different arities is a typed parse diagnostic, not a silent best-effort zip.",
    "se_official_clause_surface_skipped": "A clause-surface diagnostic skipped for an unspecified reason is a typed residual.",

    # --- Effect-plan lowering (the canonical-effects-plan → ops waist) --------------------
    "se_official_effect_lowering_skipped": "A planned-effect that the lowering pass cannot emit is a typed lowering residual, not a silent drop.",
    "se_official_effect_payload_not_found": "A planned effect whose source provision is not in the payload surface is a typed lowering diagnostic, not a silent default.",
    "se_official_effect_plan_missing_base_act": "An official-effects-plan that has no amended-act SFS id is a typed frontier residual; the lowering raises ``NotImplementedError`` so the outcome can carry the fact.",
    "se_official_effect_plan_missing_elaboration": "An official-effects-plan whose elaboration is None is a typed lowering diagnostic, not a silent empty op list.",
    "se_official_effect_plan_unclaimed_payload": "A payload row that the effect plan never claimed is a typed residual surface, not a silent drop.",
    "se_official_effect_plan_unsupported": "An official-effects-plan with no planned items is a typed frontier residual; the lowering raises ``NotImplementedError`` so the outcome surfaces it.",
    "se_official_effect_text_patch_incomplete": "A text-patch planned effect with a selector but no replacement is a typed lowering diagnostic, not a silent no-op.",
    "se_official_effect_text_patch_missing": "A text-patch planned effect with no structured text_patch is a typed lowering diagnostic, not a silent default.",

    # --- Effective-date inference (manual-compilation frontier) ---------------------------
    "se_official_effective_date_inferred_from_issued_date": "An effective-date inferred from the cached ``issued_date`` (vs the legal publication + 7 days default) is a typed assumption, not silent substitution.",
    "se_official_effective_date_inferred_from_published_date": "An effective-date inferred from the cached ``published_date`` (vs the legal publication + 7 days default) is a typed assumption, not silent substitution.",

    # --- Non-amending act ---------------------------------------------------------------
    "se_official_non_amending_act_ops_skipped": "A non-amending official act has no replay ops; the lowering path emits a typed non-blocking note, not a silent empty op list.",

    # --- Older-base rebuild chain (the recovery ladder's deepest rung) --------------------
    "se_official_rebuild_chain_invalid_official_act": "A prior chain-step act that the act parser cannot coerce is a typed rebuild-chain row, the chain is partial, not silently dropped.",
    "se_official_rebuild_chain_missing_official_act": "A prior chain-step act whose official-act surface is not archived is a typed rebuild-chain row, the chain is incomplete, not silently skipped.",
    "se_official_rebuild_chain_ops_unsupported": "A prior chain-step act whose compiled ops are unsupported is a typed rebuild-chain row, the chain is partial, not silently dropped.",
    "se_official_rebuild_chain_unknown_ops_status": "A prior chain-step act whose ops-status is unknown to the rebuild chain is a typed schema-drift row, not a silent default.",

    # --- Unclaimed payload ---------------------------------------------------------------
    "se_official_unclaimed_payload_skipped": "A payload row that the effect-plan did not claim during lowering is a typed surface residual, not a silent drop.",

    # --- Replay outcome typed signals (§1.0 Mutation Boundary Invariant failures) ----------
    "se_replay_base_surface_contains_post_amendment_targets": "When the contaminated base current surface has post-amendment targets before the amending act's effective date and reverse-patching plateaued, the replay outcome is ``older_base_required`` (a typed frontier signal), not a silent replay.",
    "se_replay_classification_to_agreement_residual": "Each check_se_official_replay row classification is projected to a typed AgreementResidual carrying residual_id, family, status, missing_proofs — the evidence-plane dossier the CLI/aggregate dict is re-derived FROM (§2.10 projection plane). Closed-vocabulary classification→family mapping; an unknown class raises (§1.10 fail-loud), not a silent drop.",
    "se_replay_destination_missing": "A RENUMBER op whose destination address is absent is a typed replay-skip adjudication, not a silent no-op.",
    "se_replay_payload_missing": "A REPLACE/INSERT op whose payload is missing or wrong-kind is a typed replay-skip adjudication, not a silent default.",
    "se_replay_recovered_base_lacks_required_targets": "When the recovered older-base rebuild still lacks a replay target the amending act needs, the replay outcome is ``precondition_issues_blocking``, not a silent mismatch.",
    "se_replay_renumber_collision": "A RENUMBER op whose destination section already exists is a typed replay-skip adjudication, not a silent overwrite.",
    "se_replay_skipped_unspecified": "A skipped op whose reason the replay accumulator did not record is typed as ``se_replay_skipped_unspecified`` (a fail-loud gap), not a silent drop.",
    "se_replay_target_not_found": "An op whose target section does not exist in the base statute is a typed replay-skip adjudication, not a silent no-op.",
    "se_replay_text_replace_no_match": "A TEXT_REPLACE op whose ``selector.match_text`` is not found in the target subtree is a typed replay-skip adjudication, not a silent fallback to whole-section replacement.",
    "se_replay_unsupported_action": "An op whose action is not in the SE replay-supported set is a typed replay-skip adjudication, not a silent pass.",
    "se_replay_unsupported_target_kind": "An op whose target leaf-kind is not in the SE replay-supported set is a typed replay-skip adjudication, not a silent pass.",

    # --- Apply receipt contract (§4 WriteReceipt divergence-naming) -----------------------
    "se_renumber_relabel": "A RENUMBER op's bound_target_path (source label) vs landed_primary_path (destination label) divergence is the typed named migration for a section relabel/renumber — receipt-audited as ``qualified`` (named-rule-explained divergence), not a ``violation`` (unexplained); the §1.6 unstated-migration invariant risks being violated if the receipt omits this rule id.",

    # --- RK current JSON acquisition (the consolidated-text-oracle source) ----------------
    "se_rk_current_fetch_failed": "A network/HTTP/RK failure when fetching the current-text oracle is a typed acquisition residual, not a silent empty buffer.",
    "se_rk_current_invalid_hit": "A malformed entry inside the RK current-text hits container is a typed acquisition residual, the row is skipped without fabrication.",
    "se_rk_current_invalid_json": "A RK current-text payload that does not decode as a JSON object is a typed acquisition residual, not a silent default.",
    "se_rk_current_invalid_root": "A RK current-text payload whose root is not the expected dict is a typed acquisition residual, not a silent fallback.",
    "se_rk_current_invalid_source": "A RK current-text payload whose ``source`` field is not the expected dict is a typed acquisition residual, not a silent default.",
    "se_rk_current_missing_hits_container": "A RK current-text payload whose ``hits`` container is absent is a typed acquisition residual, not a silent empty hits list.",
    "se_rk_current_no_hits": "A RK current-text query whose result set is empty is a typed acquisition residual, surfaced as evidence (not a silent empty oracle).",

    # --- Scraped-doc ingestion -----------------------------------------------------------
    "se_scraped_doc_entry_invalid_shape": "A scraped doc-page entry that does not decode to a dict is a typed ingestion residual, skipped without fabrication.",
    "se_scraped_doc_entry_unrecognized_url": "A scraped doc-page entry whose URL does not match the SFS doc shape is a typed ingestion residual, skipped without fabrication.",
}

