"""New Zealand believed_spec catalog — the discovered-spec hypotheses, one per rule.

Standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS`` and of
``spec_ledger_ee_catalog`` / ``spec_ledger_uk_catalog`` / ``spec_ledger_us_catalog``.
Nothing in this module imports the new_zealand frontend, so it carries no heavy deps
and stays conflict-free with parallel edits under ``src/lawvm/new_zealand/``.

Voice and contract (see ``spec_ledger.py`` and the EE catalog module docstring): each
entry is a falsifiable one-line hypothesis about the *legal-amendment semantics* (or
the dry-run / preflight discipline) the witness rule encodes — the believed spec the
compiler is testing against the witness. The NZ witness is the archived on-or-after
version (the dry-run oracle): an AGREES firing corroborates the hypothesis, a residual
contradicts it; a refusal rule is a typed blocker that must be carried to the
actual-replay ledger rather than silently dropped.

Scope (this iteration): the dry-run kernel + the actual-replay promotion layer —
``src/lawvm/new_zealand/dry_run.py``, ``dry_run_oracle.py``, and
``actual_replay.py``. These are the rule-bearing files for the four promotable families
(repeal, text_replace, replace, insert). The instruction/lowering/source-change lanes
emit their own ``nz_instruction_*`` / ``nz_source_change_text_*`` / ``nz_target_address_*``
rule ids and will be covered when those are added; the test pins any unscaffolded lane
to a known-fixed denominator (this file list), so extending coverage later is a
deliberate expansion rather than a silent widening.

The NZ rule vocabulary in this scope splits into:

* **agreement / residual vocabulary** (``nz_dry_run_*_matches_oracle`` /
  ``nz_dry_run_*_residual_*``): the per-op dry-run agreement notion per family. The
  AGREES rule is the corroborating witness; each residual is a named contradiction
  with a side-of-the-gap disposition (oracle-suspect vs replay-bug frontier).
* **per-op refusals** (``nz_dry_run_refused_*``): typed blockers a candidate op
  cannot pass — never silent skips, each lifts into ``actual_replay_fail_closed``.
* **family-level refusals** (``nz_dry_run_refused_no_replayable_*``): the kernel
  found no candidate witness row in that family's surface for this work — a
  whole-family receipt, never per-op.
* **whole-tree oracle residual taxonomy** (``nz_dry_run_oracle_*``): the broader-tree
  comparison layer above the per-op kernel — agreement vs the residual kinds the
  whole-tree classifier can name.
* **actual-replay agreement / refusal** (``nz_actual_replay_*``): the promotion
  layer's own typed carriers — replayed vs fail-closed transitions and the slice
  re-confirm rebroadcast rules.

Coverage is anti-drift-guarded by ``tests/test_spec_ledger_nz_catalog.py``: every
statically discoverable ``nz_*`` witness rule_id literal in the scoping files
must have a non-empty entry here, and every key here must map to a real literal (no
dead entries). The scoping files cover nine NZ subsystems: the four promotable families'
kernel (``dry_run.py`` / ``dry_run_oracle.py`` / ``actual_replay.py``), instruction
lowering (``instruction_workqueue.py``), effect-candidate / source-change witnesses
(``effect_candidates.py``), readiness voter (``effect_readiness.py``), the manual-
compilation frontier (``frontier_work_items.py``), the target-resolution + operation
surface (``operation_surface.py``), and the bench surface (``benchmark.py``).

The ``nz_*`` literals that are NOT believed-spec witness rule ids are the
documented exclusions (``NZ_NON_RULE_LITERALS``):

* ``nz_dry_run_repeal`` / ``nz_dry_run_structural_insert`` /
  ``nz_dry_run_structural_replace`` / ``nz_dry_run_text_replace`` — the per-family
  ``surface_name`` identity field on the dry-run report (not a per-rule firing);
* ``nz_dry_run_repeal_whole_tree`` — the dry-run-oracle ``agreement_surface``
  identity field (not a per-rule firing);
* ``nz_actual_replay`` — the agreement_surface identity for residual rows on the
  actual-replay plane (carried on every residual, not a per-rule firing);
* f-string prefix fragments ``nz_source_change_text_``, ``nz_target_address_``,
  ``nz_target_address_hint_``, ``nz_operation_surface_``, ``nz_lowering_readiness_``,
  ``nz_instruction_latest_oracle_text_``, ``nz_text_replace_witness_support_``,
  ``nz_effect_readiness_`` — the bare Constant portion of ``f"nz_X_{status}"``
  expressions (each has concrete rule-id siblings of the form ``nz_X_<status>``);
* surface/role identity tags carried on witness dicts: ``nz_effect_feed_target_address``
  (target witness ``surface`` field), ``nz_amending_instruction_source`` (source witness
  ``default_role`` field), ``nz_instruction_semantics`` (residual ``owner_phase``
  identity), ``nz_instruction_semantic_compile`` (manual-compilation claim ``kind``).

(The ``nz_dry_run:`` / ``nz_dry_run_candidate_after:`` / ``nz_on_or_after_oracle:``
literals with a ``:`` are excluded by the discovery's ``:``-fragment filter, not
enumerated here.)
"""
from __future__ import annotations

# Per AGENTS §2.5 (one source per family, typed residue): the production NZ
# spec-ledger adapter (`lawvm.new_zealand.spec_ledger_adapter.NZRuleCatalogEntry`)
# owns its richer ``NZ_RULE_CATALOG`` of 16 dry-oracle rule_ids with a paired
# *confidence tier*. This catalog imports those beliefs verbatim as the single
# source of truth for the overlap, then ADDS the broader anti-drift surface (199
# extras across acquisition/commencement/payload/chain_replay/etc.) the adapter
# pipeline does not consume. A parity test pins that the two catalogs MUST agree
# on overlap (prose verbatim) — no second authors' voice may silently drift.
from lawvm.new_zealand.spec_ledger_adapter import NZ_RULE_SPECS as _ADAPTER_RULE_SPECS

from typing import Dict, FrozenSet

# ``nz_*`` literals in the scoping files that are deliberately NOT believed-spec
# witness rule ids (see the module docstring). The coverage test excludes these from
# the denominator and asserts they never appear as catalog keys.
NZ_NON_RULE_LITERALS: FrozenSet[str] = frozenset(
    {
        # Per-family surface_name identity fields on NZDryRunReport (not per-rule firings):
        "nz_dry_run_repeal",
        "nz_dry_run_structural_insert",
        "nz_dry_run_structural_replace",
        "nz_dry_run_text_replace",
        # Dry-run-oracle agreement_surface identity field (not a per-rule firing):
        "nz_dry_run_repeal_whole_tree",
        # Actual-replay residual agreement_surface identity (carried on every residual
        # row from the actual-replay plane; not a per-rule firing):
        "nz_actual_replay",
        # f-string prefix fragments: the bare Constant portion of ``f"nz_X_{status}"``
        # appears as a child literal in AST and must be excluded from the rule-id
        # denominator. Each has concrete rule-id siblings of the form ``nz_X_<status>``:
        "nz_source_change_text_",  # effect_candidates.py: rule_id=f"nz_source_change_text_{status}"
        "nz_target_address_",  # operation_surface.py: f"nz_target_address_{candidate.status}"
        "nz_target_address_hint_",  # operation_surface.py: f"nz_target_address_hint_{status}"
        "nz_operation_surface_",  # operation_surface.py: f"nz_operation_surface_{operation_status}"
        "nz_lowering_readiness_",  # operation_surface.py: f"nz_lowering_readiness_{readiness_status}"
        "nz_instruction_latest_oracle_text_",  # instruction_workqueue.py: f"..._{status}"
        "nz_text_replace_witness_support_",  # effect_candidates.py: f"nz_text_replace_witness_support_{status}"
        "nz_effect_readiness_",  # effect_readiness.py: f"nz_effect_readiness_{rule_suffix}"
        "nz_agreement_comparator_status_",  # agreement.py: f"nz_agreement_comparator_status_{status}"
        # surface/role identity tags carried on witness dicts: ``nz_effect_feed_target_address``
        # (target witness ``surface`` field), ``nz_amending_instruction_source`` (source witness
        # ``default_role`` field), ``nz_instruction_semantics`` (residual ``owner_phase``
        # identity), ``nz_instruction_semantic_compile`` (manual-compilation claim ``kind``).
        "nz_effect_feed_target_address",
        "nz_amending_instruction_source",
        "nz_instruction_semantics",
        "nz_instruction_semantic_compile",
        # agreement-surface identity fields (the ``agreement_surface`` value carried on
        # residual/finding rows; not per-rule firings):
        "nz_candidate_oracle_source_tree",  # agreement.py: agreement_surface default
        "nz_actual_replay_materialized_after_vs_oracle",  # agreement.py: agreement_surface in projection
        "nz_commencement",  # commencement.py: agreement_surface on commencement outcomes
        # acquisition source-lane / API identity tags (not per-rule firings):
        "nz_legislation_api_v0",  # acquisition.py: source_regime identity token
        "nz_api_v0_works_page",  # acquisition.py: source-lane / fetcher flavour tag
        "nz_api_v0_work_versions",
        "nz_api_v0_version_detail",
        "nz_api_v0_version_xml",
        "nz_corpus_run_cache",  # corpus_cache.py: cache key surface identity
        # Intentional uncataloged-fixture: this string only appears as the
        # negative-test input for the legacy_unknown sentinel path
        # (tests/test_new_zealand_spec_ledger.py: asserts an uncataloged rule
        # surfaces as legacy_unknown). It MUST NOT be cataloged — the test would
        # otherwise never exercise the uncataloged path. Test-file anti-drift
        # guard (test_every_nz_rule_id_used_in_nz_tests_is_cataloged_or_non_rule)
        # excludes it via NON_RULE.
        "nz_dry_run_some_future_uncataloged_rule",
    }
)

# rule_id -> believed_spec prose (the catalog this module exposes). Each value is a
# falsifiable one-line claim about what the witness rule asserts (and so what an
# AGREES corroborates / a residual contradicts / a refusal must be carried forward
# as). For the 16 dry-oracle rule_ids the production adapter owns authoritatively
# (with a paired confidence tier), this dict DEFERS to the adapter's prose — see
# `_ADAPTER_RULE_SPECS` import above (AGENTS §2.5 single-source-per-family).
_EXTRA_NZ_RULE_SPECS: Dict[str, str] = {
    # --- Dry-run kernel: surface / authorization / readiness -----------------------------
    "nz_dry_run_surface_not_replay_authorized": (
        "A candidate op that is not ``replay_authorized`` may never be promoted; the "
        "dry-run surface carries it as an observation only, never as replay authority."
    ),
    "nz_dry_run_refused_preflight_not_ready_for_dry_run": (
        "When the per-work preflight is not ready (missing candidates / unresolved date), "
        "the whole-family kernel refuses before any per-op proof — a work-level, never op-level, receipt."
    ),
    # --- Family-level "no candidate in this family's witness surface" refusals ----------
    "nz_dry_run_refused_no_replayable_repeal_candidate": (
        "Repeal lane found no candidate ``repealed``-family witness row in the operation surface — "
        "work-level receipt, never per-op; surfaces as family-level in actual replay."
    ),
    "nz_dry_run_refused_no_replayable_replace_candidate": (
        "Replace lane found no candidate ``replaced``/``substituted`` witness row; family-specific "
        "so a missing replace reader is distinguishable from a missing repeal reader (§1.10)."
    ),
    "nz_dry_run_refused_no_replayable_insert_candidate": (
        "Insert lane found no candidate ``inserted``/``added`` witness row; family-specific so a missing "
        "insert reader is distinguishable from a missing repeal/replace reader (§1.10)."
    ),
    # --- Per-op refusals: target resolution / payload shape -----------------------------
    "nz_dry_run_refused_target_recovered_not_exact": (
        "The target was inferred (recovered) rather than explicitly named in the source — the dry-run "
        "refuses inferred targets so replay never promotes a guessed resolution (§1.1)."
    ),
    "nz_dry_run_refused_source_change_only_payload": (
        "The candidate payload is a source-change-only diff (no executable amend verb) — not a "
        "replayable op; carried forward as a receipt rather than silently dropped."
    ),
    "nz_dry_run_refused_missing_before_after_version_window": (
        "The work lacks a before/on-or-after archived version window for this amendment date — no "
        "proof frame can be built; refusal rather than fabricated slice."
    ),
    "nz_dry_run_refused_before_xml_unreadable": (
        "The archived before-version XML was unreadable — the proof cannot be built; refusal rather "
        "than silent skip."
    ),
    "nz_dry_run_refused_on_or_after_xml_unreadable": (
        "The archived on-or-after oracle XML was unreadable — no comparison surface; refusal "
        "rather than fabricated oracle."
    ),
    "nz_dry_run_amending_act_root": (
        "A dry-run failure to resolve or parse the amending act XML root is emitted as a "
        "named source/proof-frame finding rather than swallowed to a missing payload silently."
    ),
    "nz_dry_run_refused_target_not_present_in_before_tree": (
        "The named target node is absent from the before tree — the op cannot act on it; refusal, "
        "not silent widening of the resolved path."
    ),
    "nz_dry_run_refused_target_not_substantive_in_before_tree": (
        "The resolved target node is non-substantive (an editorial shell, not a body node) — the dry-run "
        "will not mutate an editorial carrier."
    ),
    "nz_dry_run_refused_target_path_ambiguous_in_before_tree": (
        "The resolved target path matches more than one before-tree node — ambiguity is preserved as a "
        "refusal rather than resolved by parser order (§1.7)."
    ),
    "nz_dry_run_refused_target_address_path_unmappable_to_source": (
        "The target address cannot be mapped onto the source-tree path scheme — refusal rather than invented path."
    ),
    # --- Repeal: agreement + residuals (tombstone / removed-node / oracle-side) --------
    # The 6 dry-oracle repeal agreement/residual rule_ids and their paired believed_spec
    # prose are OWNED by the production adapter (`lawvm.new_zealand.spec_ledger_adapter`
    # NZRuleCatalogEntry, with a paired *confidence tier*) — single source of truth per
    # AGENTS §2.5 (parity-tested: see `test_catalog_prose_agrees_with_production_adapter_-
    # on_overlap`). Keys: nz_dry_run_repeal_tombstone_matches_oracle /
    # nz_dry_run_repeal_removed_node_matches_oracle /
    # nz_dry_run_residual_target_missing_in_oracle /
    # nz_dry_run_residual_target_not_tombstone_in_oracle /
    # nz_dry_run_residual_target_not_removed_in_oracle.
    # --- Text-replace (TEXT_REPLACE): agreement + residuals -----------------------------
    # The 4 dry-oracle text-replace agreement/residual rule_ids are OWNED by the
    # production adapter (same parity contract, §2.5). Keys:
    # nz_dry_run_text_replace_substitution_reflected_in_oracle /
    # nz_dry_run_text_replace_residual_old_text_remains_in_oracle /
    # nz_dry_run_text_replace_residual_new_text_absent_in_oracle /
    # nz_dry_run_text_replace_residual_target_missing_in_oracle.
    "nz_dry_run_refused_text_replace_missing_text_patch": (
        "Refusal: the lowering did not produce a text patch (old/new text) for a text-substitution op."
    ),
    "nz_dry_run_refused_text_replace_scope_not_single_occurrence": (
        "Refusal: the substitution does not resolve to a single-occurrence scope and the source did not "
        "declare each-place — ambiguous scope is refused, not guessed (§1.7)."
    ),
    "nz_dry_run_refused_text_replace_old_text_occurrence_not_single_in_before_target": (
        "Refusal: the old_text does not occur exactly once in the before target — neither a single-occurrence "
        "substitution nor a valid each-place proof (occurrence count inconsistent with intent)."
    ),
    "nz_dry_run_refused_text_replace_apply_left_node_unchanged": (
        "Refusal: the substitution applied to the candidate after-node produced no change (no-op); the proof "
        "is not materialized."
    ),
    "nz_text_replace_candidate_latest_oracle_witness_unavailable": (
        "Refusal: the candidate text-replace op (direct-instruction or source-change lane) requires the "
        "latest archived on-or-after version as its 'latest oracle witness' context for text-substitution "
        "evidence — when that witness is unavailable the candidate is blocked rather than guessed."
    ),
    # --- Structural whole-provision REPLACE: agreement + residuals --------------------
    # The 3 dry-oracle structural-replace agreement/residual rule_ids are OWNED by the
    # production adapter (same parity contract, §2.5). Keys:
    # nz_dry_run_structural_replace_subtree_matches_oracle /
    # nz_dry_run_structural_replace_residual_replacement_mismatch_in_oracle /
    # nz_dry_run_structural_replace_residual_target_missing_in_oracle.
    "nz_dry_run_refused_structural_replace_target_address_not_candidate": (
        "Refusal: the resolved replace target is not a candidate (exact) resolved target — the kernel "
        "refuses inferred/recovered rather than explicitly-named replace scope (§1.1)."
    ),
    "nz_dry_run_refused_structural_replace_amending_work_unresolved": (
        "Refusal: the amending work cited by the witness row could not be resolved to an archived XML payload."
    ),
    "nz_dry_run_refused_structural_replace_amending_act_xml_unreadable": (
        "Refusal: the amending act XML is unreadable — replacement subtree cannot be extracted."
    ),
    "nz_dry_run_refused_structural_replace_amending_provision_href_not_found": (
        "Refusal: the amending-provision href (the part of the amending act that carries the replacement "
        "payload) was not found in the amending act XML."
    ),
    "nz_dry_run_refused_structural_replace_payload_not_cleanly_extractable": (
        "Refusal: the replacement subtree inside the amending provision could not be cleanly extracted "
        "(malformed wrapper / nested ambiguity)."
    ),
    "nz_dry_run_refused_structural_replace_apply_left_subtree_unchanged": (
        "Refusal: applying the extracted replacement subtree to the candidate after-tree produced no change "
        "(no-op); the proof is not materialized."
    ),
    # --- Structural whole-provision INSERT: agreement + residuals ---------------------
    # The 4 dry-oracle structural-insert agreement/residual rule_ids are OWNED by the
    # production adapter (same parity contract, §2.5). Keys:
    # nz_dry_run_structural_insert_new_node_present_and_matches_oracle /
    # nz_dry_run_structural_insert_residual_new_node_not_present_in_oracle /
    # nz_dry_run_structural_insert_residual_new_node_content_mismatch_in_oracle /
    # nz_dry_run_structural_insert_residual_new_node_position_mismatch_in_oracle.
    "nz_dry_run_refused_structural_insert_target_address_not_candidate": (
        "Refusal: the resolved insert target (the inserted node's own address) is not an exact-resolved "
        "candidate; inferred scope is refused."
    ),
    "nz_dry_run_refused_structural_insert_anchor_not_derivable_from_inserted_label": (
        "Refusal: the pre-existing anchor sibling cannot be derived from the inserted node's label (suffix-letter "
        "or numeric predecessor convention) — refused rather than guessed."
    ),
    "nz_dry_run_refused_structural_insert_amending_work_unresolved": (
        "Refusal: the amending work cited by the insert witness row could not be resolved to an archived XML payload."
    ),
    "nz_dry_run_refused_structural_insert_amending_act_xml_unreadable": (
        "Refusal: the amending act XML is unreadable — the new-node payload cannot be extracted."
    ),
    "nz_dry_run_refused_structural_insert_amending_provision_href_not_found": (
        "Refusal: the amending-provision href that carries the inserted-node payload was not found in the "
        "amending act XML."
    ),
    "nz_dry_run_refused_structural_insert_payload_not_cleanly_extractable": (
        "Refusal: the new-node subtree inside the amending provision could not be cleanly extracted (malformed "
        "wrapper / nested ambiguity)."
    ),
    "nz_dry_run_refused_structural_insert_new_node_already_present_in_before_tree": (
        "Refusal: the new node is already present in the before tree — the insertion would duplicate, so the "
        "kernel refuses (manual-compilation frontier or wrong source-lane)."
    ),
    "nz_dry_run_refused_structural_insert_anchor_not_present_in_before_tree": (
        "Refusal: the derived pre-existing anchor sibling is absent from the before tree — the position proof "
        "cannot be constructed."
    ),
    "nz_dry_run_refused_structural_insert_anchor_path_ambiguous_in_before_tree": (
        "Refusal: the anchor path matches more than one before-tree node — ambiguity preserved, never resolved "
        "by parser order (§1.7)."
    ),
    "nz_dry_run_refused_structural_insert_nested_parent_not_present_in_before_tree": (
        "Refusal (nested insert): the parent provision under which the inserted child lives is absent from the "
        "before tree — the nested insert cannot be placed."
    ),
    "nz_dry_run_refused_structural_insert_nested_parent_path_ambiguous_in_before_tree": (
        "Refusal (nested insert): the nested parent path matches more than one before-tree node — "
        "ambiguity preserved, never resolved by position (§1.7)."
    ),
    "nz_dry_run_refused_structural_insert_nested_anchor_not_derivable_from_sibling_group": (
        "Refusal (nested insert): the leaf's position among its sibling group cannot be derived — the nested "
        "anchor derivation produces no deterministic placement, refused rather than guessed."
    ),
    # --- Whole-tree oracle residual taxonomy (above the per-op kernel) ----------------
    "nz_dry_run_oracle_candidate_node_agrees_with_oracle": (
        "AGREES (whole-tree): the candidate after-tree node matches its oracle on-or-after counterpart at "
        "the normalized-text level — the broader-tree classifier corroborates the per-op kernel."
    ),
    "nz_dry_run_oracle_repeal_target_tombstone_agrees": (
        "AGREES (whole-tree, repeal): the candidate tombstone and the oracle both mark the target deleted "
        "(and the oracle retains the body text consistently with the boring tombstone kernel)."
    ),
    "nz_dry_run_oracle_repeal_target_tombstone_agrees_oracle_erased_body_text": (
        "RESIDUAL-separate (whole-tree, repeal): the repeal direction agrees but the oracle additionally "
        "erased the provision body text the boring tombstone kernel deliberately keeps — source-honest, "
        "NOT a repeal-direction bug."
    ),
    "nz_dry_run_oracle_unapplied_non_repeal_change_in_window": (
        "RESIDUAL (whole-tree): a non-repeal change exists in the change window that no dry-run op applied — "
        "a missing op left unaccounted for, never silently dropped."
    ),
    "nz_dry_run_oracle_applied_repeal_target_diverges_from_oracle": (
        "RESIDUAL (whole-tree, repeal): the applied repeal's target diverges from the oracle's tombstoned "
        "target — the candidate tombstone landed on the wrong node or the oracle node is non-tombstoned."
    ),
    # --- Actual-replay promotion layer: agreement + fail-closed refusals ---------------
    "nz_actual_replay_transition_materialized_from_archived_before_and_verified_ops": (
        "AGREEMENT-output: one declared transition materialized as (archived before version) + (dry-run-verified "
        "ops) -> (candidate after version), separately counted from fail-closed-blocked windows."
    ),
    "nz_actual_replay_materialized_target_slice_agrees_with_archived_on_or_after_oracle": (
        "AGREEMENT-output: the materialized target slice re-confirms under the SAME family agreement notion the "
        "dry-run proof was verified under; the materialization is sound."
    ),
    "nz_actual_replay_refused_declared_op_not_dry_run_verified": (
        "Refusal: the declared op was not dry-run-verified (refused by the dry-run kernel); the whole declared "
        "transition is blocked, never partially materialized (fail-closed)."
    ),
    "nz_actual_replay_refused_declared_op_dry_run_oracle_residual_not_agreement": (
        "Refusal: the dry-run proof was a residual, not AGREES; the transition is fail-closed with the residual "
        "carried forward under its residual taxonomy."
    ),
    "nz_actual_replay_refused_declared_op_mutation_perturbed_neighbours": (
        "Refusal: the dry-run mutation perturbed a neighbour or parent node — the mutation boundary is not "
        "clean; the transition is fail-closed rather than admitting the op with side effects (§1.0)."
    ),
    "nz_actual_replay_refused_op_family_not_in_promotable_set": (
        "Refusal: the op's family is not in the promotable set (repeal/text_replace/replace/insert); actual "
        "replay refuses rather than guessing a kernel for an unsupported family."
    ),
    "nz_actual_replay_refused_before_version_xml_unreadable": (
        "Refusal: the archived before-version XML for this transition's change window is unreadable — no "
        "materialization baseline is fabricated."
    ),
    "nz_actual_replay_refused_on_or_after_version_xml_unreadable": (
        "Refusal: the archived on-or-after oracle XML is unreadable — no comparison surface for the slice "
        "re-confirm."
    ),
    "nz_actual_replay_refused_missing_before_after_version_window": (
        "Refusal: the change window's before/on-or-after archived versions are missing — no transition "
        "can be materialized."
    ),
    "nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle": (
        "Refusal (defence-in-depth): after compositing all verified ops, the materialized target slice "
        "re-confirms as a divergence under the family agreement notion — never silently admitted as sound."
    ),
    "nz_actual_replay_refused_structural_payload_not_re_materializable": (
        "Refusal: a dry-run-verified structural op could not be re-materialized into the after-tree — the "
        "structural payload is no longer cleanly re-extractable."
    ),
    "nz_actual_replay_refused_operation_surface_missing_for_structural_family": (
        "Refusal: a structural family (replace/insert) was requested but no operation surface was provided — "
        "the family is not attempted, never silently skipped."
    ),
    "nz_actual_replay_carried_family_level_dry_run_refusal": (
        "Receipt (NOT a fail-closed refusal): the dry-run surface emitted a family-level refusal "
        "(no candidate for repeal/replace/insert, preflight not ready) — the family declared nothing "
        "to replay, so it never blocks a transition; carried onto the actual-replay plane as a work-"
        "level receipt so the family-level no-candidate event stays observable per AGENTS §1.8 "
        "(every filtered lane stays visible with a receipt; differing from the per-op blocking "
        "``refusals`` which DO fail-closed a transition)."
    ),
    # --- Instruction-semantics readiness (effect_readiness.py) ------------------------
    "nz_instruction_semantics_payload_witness_not_available": (
        "Readiness refusal: the op's amending payload witness is absent (payload_status != "
        "``payload_found``); the readiness lane refuses before lowering rather than guessing a payload."
    ),
    "nz_instruction_semantics_not_required_repeal": (
        "Readiness outcome: a ``repealed``-family op needs no enrolled payload witness — "
        "the readiness lane marks it not-required-for-repeal so the dry-run repeal kernel handles it."
    ),
    "nz_instruction_semantics_review_retrospective_incorporated_note": (
        "Readiness outcome: the payload is a retrospective incorporated-amendment note (an editorial "
        "summary, not a replayable amend verb) — surfaced for manual review, never lowered as an op."
    ),
    "nz_instruction_semantics_candidate_direct_instruction": (
        "Readiness outcome (candidate-only semantics): the payload is classified for direct-instruction "
        "lowering only — passed to the instruction_workqueue lane; not lowered as a structural op."
    ),
    "nz_instruction_semantics_blocked_schedule_or_omnibus_indirection": (
        "Readiness refusal: the payload is reached only via a schedule/omnibus indirection the readiness "
        "lane will not silently follow (a manual-compilation frontier, never a guessed payload)."
    ),
    "nz_instruction_semantics_blocked_opaque_or_unclassified": (
        "Readiness refusal: the payload instruction shape is opaque or unclassified — blocked, never "
        "guessed to a candidate lowering (AGENTS §1.11 — surface-not-semantic)."
    ),
    "nz_instruction_semantics_unclassified": (
        "Readiness refusal (fallback): no readiness branch matched the payload shape — "
        "a typed unclassified residual rather than a silent default."
    ),
    # --- Instruction workqueue: text-substitution direct candidates (NOT lowered) -----
    "nz_instruction_semantics_direct_single_text_substitution_candidate": (
        "Direct-instruction candidate (single-occurrence): the 'substitute <old> with <new>' shape with "
        "exactly one occurrence in the target — passed to the direct-instruction lowering lane; the dry-run "
        "text-substitution kernel corroborates it from the source-change witness."
    ),
    "nz_instruction_semantics_direct_each_place_text_substitution_candidate": (
        "Direct-instruction candidate (each-place): the substitution applies at every occurrence of the "
        "old_text in the target (explicit 'each place it occurs' scope)."
    ),
    "nz_instruction_semantics_direct_omitting_substituting_text_substitution_candidate": (
        "Direct-instruction candidate (single-occurrence): the 'omitting <deleted> substituting <new>' "
        "shape with one occurrence; lowering produces a single-occurrence text-replace op."
    ),
    "nz_instruction_semantics_direct_each_place_omitting_substituting_text_substitution_candidate": (
        "Direct-instruction candidate (each-place): the omitting/substituting shape with explicit "
        "each-place scope; lowering produces an each-place text-replace op."
    ),
    "nz_instruction_semantics_direct_multi_clause_text_substitution_candidate": (
        "Direct-instruction candidate (multi-clause, single-occurrence per clause): a multi-clause "
        "substitute shape whose clauses each lower as a single-occurrence text-replace against the target."
    ),
    "nz_instruction_semantics_direct_multi_clause_each_place_text_substitution_candidate": (
        "Direct-instruction candidate (multi-clause, each-place): a multi-clause substitute shape with "
        "explicit each-place scope across the clauses."
    ),
    "nz_instruction_semantics_direct_multi_clause_omitting_substituting_text_substitution_candidate": (
        "Direct-instruction candidate (multi-clause, single-occurrence per clause): a multi-clause "
        "omitting/substituting shape whose clauses each lower as a single-occurrence text-replace."
    ),
    "nz_instruction_semantics_direct_multi_clause_each_place_omitting_substituting_text_substitution_candidate": (
        "Direct-instruction candidate (multi-clause, each-place): a multi-clause omitting/substituting "
        "shape with explicit each-place scope across the clauses."
    ),
    "nz_instruction_semantics_direct_typed_amend_in_text_substitution_candidate": (
        "Direct-instruction candidate (typed amend-in): a typed 'amend in' instruction whose body is a "
        "text-substitution shape; lowers as a single-occurrence text-replace."
    ),
    "nz_instruction_semantics_direct_each_place_typed_amend_in_text_substitution_candidate": (
        "Direct-instruction candidate (typed amend-in, each-place): a typed amend-in whose body is an "
        "each-place text-substitution; lowers as an each-place text-replace."
    ),
    "nz_instruction_semantics_direct_typed_amend_in_omit_deletion_candidate": (
        "Direct-instruction candidate (typed amend-in omit): a typed amend-in whose body deletes text "
        "(new_text empty); lowers as a single-occurrence text-replace that removes the old_text."
    ),
    "nz_instruction_semantics_direct_each_place_typed_amend_in_omit_deletion_candidate": (
        "Direct-instruction candidate (typed amend-in omit, each-place): a typed amend-in whose body "
        "deletes the old_text at every occurrence; lowers as an each-place deletion text-replace."
    ),
    "nz_instruction_semantics_direct_typed_amend_in_insert_candidate": (
        "Direct-instruction candidate (typed amend-in insert): a typed amend-in that wraps an insert "
        "(anchor + new); lowers as a single-occurrence text-replace that prepends/appends the new to the anchor."
    ),
    "nz_instruction_semantics_direct_each_place_typed_amend_in_insert_candidate": (
        "Direct-instruction candidate (typed amend-in insert, each-place): the wrap-an-insert shape with "
        "explicit each-place scope; lowers as an each-place text-replace."
    ),
    # --- Instruction workqueue: refusals (NOT lowered) --------------------------------
    "nz_instruction_semantics_blocked_text_substitution_parse_failed": (
        "Refusal: the substitute/replace-with phrase shape could not be parsed (the operative text does "
        "not match the recognized drafting idiom); lowering is blocked, never guessed (§1.11)."
    ),
    "nz_instruction_semantics_blocked_target_citation_mismatch": (
        "Refusal: the explicit target citation in the operative phrase does not match the resolved "
        "target address — the lowering refuses rather than silently rebinding to a different target (§1.1)."
    ),
    "nz_instruction_semantics_blocked_multiple_occurrence_text_substitution": (
        "Refusal: the occurrence scope is ambiguous (neither a single-occurrence nor an explicitly "
        "each-place scope) — ambiguity is refused rather than resolved by parser order (§1.7)."
    ),
    "nz_instruction_semantics_blocked_structural_replacement_payload": (
        "Refusal: the payload is a structural replacement ('replace with:' whole-provision shape) that "
        "the direct-instruction text-substitution lane does not lower — the structural-replace family owns it."
    ),
    "nz_instruction_semantics_blocked_payload_multiplicity": (
        "Refusal: the operative text spans multiple amending-provision payloads or clauses; the direct-"
        "instruction lane refuses a multi-payload lowering rather than guessing which one is primary."
    ),
    "nz_instruction_semantics_blocked_omitting_substituting_parse_failed": (
        "Refusal: the omitting/substituting phrase shape could not be parsed — the direct-instruction lane "
        "refuses rather than inventing a substitution."
    ),
    "nz_instruction_semantics_blocked_structural_omitting_substituting_payload": (
        "Refusal: the omitting/substituting payload carries a structural (whole-provision) replacement — "
        "the text-substitution lane refuses rather than widening into structural-replace (§1.2)."
    ),
    "nz_instruction_semantics_blocked_typed_amend_in_ambiguous_target": (
        "Refusal (typed amend-in): the target cannot be resolved unambiguously from the typed amend-in's "
        "anchor — refused rather than guessed (§1.7)."
    ),
    "nz_instruction_semantics_blocked_typed_amend_in_not_substitution_verb": (
        "Refusal (typed amend-in): the amend-in's verb is not a recognized substitution/insert/omit "
        "verb — blocked rather than coerced into a substitute lowering (§1.2)."
    ),
    "nz_instruction_semantics_blocked_typed_amend_in_payload_incomplete": (
        "Refusal (typed amend-in): the typed amend-in payload is incomplete (missing anchor or new text) "
        "— lowering is blocked rather than fabricated."
    ),
    "nz_instruction_semantics_blocked_typed_amend_in_insert_anchor_unparsed": (
        "Refusal (typed amend-in insert): the anchor sibling or insert position could not be parsed from "
        "the typed amend-in — the insert-payload lowering is blocked rather than guessed (§1.7)."
    ),
    "nz_instruction_subfamily_not_text_substitution_shape": (
        "Classification refusal: the operative payload is not a text-substitution shape at all (neither "
        "direct nor omitting/substituting) — the text-substitution lane will not absorb it silently."
    ),
    "nz_instruction_workqueue_not_lowered": (
        "Workqueue refusal (fallback): the direct-instruction workqueue row could not be lowered to "
        "any candidate op and carries this fallback receipt (the row is UNBLOCKED classification, never "
        "silently dropped — surfaces as evidence with `evidence_status=UNSUPPORTED`)."
    ),
    # --- Instruction workqueue: structural-subfamily refusals (frontier blockers) -----
    "nz_instruction_structural_subfamily_ambiguous_amend_replace_payload_blocked": (
        "Structural-subfamily refusal: the payload looks like an amend+replace but the structural shape is "
        "ambiguous (multiple interpretations) — the structural-replace lowering is blocked, not guessed (§1.7)."
    ),
    "nz_instruction_structural_subfamily_mixed_repeal_substitute_payload_blocked": (
        "Structural-subfamily refusal: the payload mixes a repeal with a 'substitute the following …' "
        "structural payload — neither family can own it without an owned recovery; blocking surfaces the "
        "frontier rather than splitting silently (§1.2)."
    ),
    "nz_instruction_structural_subfamily_retrospective_incorporated_note_review": (
        "Structural-subfamily outcome: the payload is a retrospective incorporated-amendment note (editorial "
        "summary of changes already baked in) — surfaced for manual review, never lowered as an op."
    ),
    "nz_instruction_structural_subfamily_schedule_indirection_payload_blocked": (
        "Structural-subfamily refusal: the structural payload is reached only via a schedule indirection "
        "the lowering will not silently follow (manual-compilation frontier, never a guessed payload)."
    ),
    "nz_instruction_structural_subfamily_incorporated_amendment_stub_payload_blocked": (
        "Structural-subfamily refusal: the payload is an 'amendment(s) incorporated in the … act(s)' stub "
        "(no concrete amend content) — the structural lane refuses rather than fabricating a payload."
    ),
    "nz_instruction_structural_subfamily_multi_section_replace_payload_blocked": (
        "Structural-subfamily refusal: the payload replaces multiple sections in one phrase ('replace "
        "sections A–B with …') — the single-section replace lowering refuses the multi-section range "
        "rather than guessing per-section decomposition."
    ),
    "nz_instruction_structural_subfamily_whole_provision_substitution_payload_blocked": (
        "Structural-subfamily refusal: the payload is a whole-provision substitution the structural-replace "
        "lane recognizes but the direct-instruction lane does not own — refused at this lane, never absorbed."
    ),
    "nz_instruction_structural_subfamily_direct_replace_payload_blocked": (
        "Structural-subfamily refusal (direct-replace catch-all): the payload is a direct structural-replace "
        "shape the lowering has not yet implemented — blocking surfaces the frontier rather than dropping it."
    ),
    "nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload mixes a text insert with a structural insert — neither "
        "family can own it without an owned split; blocking surfaces the mixed frontier (§1.2)."
    ),
    "nz_instruction_structural_subfamily_direct_amend_payload_blocked": (
        "Structural-subfamily refusal (direct-amend catch-all): a direct structural-amend shape the lowering "
        "has not yet implemented — a frontier blocker, never a silent skip."
    ),
    "nz_instruction_structural_subfamily_historical_inserted_note_payload_blocked": (
        "Structural-subfamily refusal: the payload is a historical 'this section inserted' editorial note "
        "(not a replayable structural payload) — blocking rather than lowering as an insert."
    ),
    "nz_instruction_structural_subfamily_cross_heading_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload inserts a cross-heading (a heading facet, not a body "
        "provision) — the body-insert lane refuses the heading-facet shape rather than absorbing it silently."
    ),
    "nz_instruction_structural_subfamily_definition_alphabetical_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload is an 'in its appropriate alphabetical order' definition "
        "insert — the ordering constraint is a manual-compilation frontier; blocking rather than guessing a "
        "position (§1.7)."
    ),
    "nz_instruction_structural_subfamily_paragraph_after_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload inserts a paragraph-'after' shape the structural-insert "
        "lowering has not yet implemented — frontier blocker."
    ),
    "nz_instruction_structural_subfamily_subsection_after_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload inserts a subsection-'after' shape the structural-insert "
        "lowering has not yet implemented — frontier blocker."
    ),
    "nz_instruction_structural_subfamily_section_after_insert_payload_blocked": (
        "Structural-subfamily refusal: the payload inserts a section-'after' shape the structural-insert "
        "lowering has not yet implemented — frontier blocker."
    ),
    "nz_instruction_structural_subfamily_direct_insert_payload_blocked": (
        "Structural-subfamily refusal (direct-insert catch-all): a direct structural-insert shape the "
        "lowering has not yet implemented — frontier blocker, never a silent skip."
    ),
    "nz_instruction_structural_subfamily_direct_text_insert_payload_blocked": (
        "Structural-subfamily refusal (direct-text-insert): the payload is a textual insert the lowering "
        "does not yet lower as a typed op — frontier blocker."
    ),
    # --- Source-change-text witness (effect_candidates.py) ----------------------------
    "nz_source_change_text_missing_amendment_date_iso": (
        "Source-change witness refusal: the operation row carries no amendment date — no change window "
        "can be derived; the witness is not computed rather than fabricated."
    ),
    "nz_source_change_text_target_source_path_missing": (
        "Source-change witness refusal: the instruction row's resolved latest-oracle target source path is "
        "absent — no target node to diff, the witness is not computed."
    ),
    "nz_source_change_text_change_window_incomplete": (
        "Source-change witness refusal: the work's before/on-or-after archived version window for this "
        "amendment date is incomplete — no diff can be produced."
    ),
    "nz_source_change_text_change_window_xml_missing": (
        "Source-change witness refusal: the change window's before or on-or-after archived XML is missing "
        "or unreadable — no source-text diff can be computed."
    ),
    "nz_source_change_text_target_node_missing_in_change_window": (
        "Source-change witness refusal: the resolved target source path does not resolve to a single node "
        "in the before or on-or-after archived document — no per-node text-diff target."
    ),
    "nz_source_change_text_witness_not_computed": (
        "Source-change witness fallback receipt: the witness for this row was not computed in the current "
        "run (a structured absence, never an opaque skip)."
    ),
    "nz_source_version_date_window_not_computed": (
        "Source-version-date witness fallback: the version date window for this row was not computed "
        "(structure absence, surfaced as a receipt)."
    ),
    "nz_source_version_date_window_missing_amendment_date_iso": (
        "Source-version-date witness refusal: the operation row has no amendment date — no version-date "
        "window can be derived."
    ),
    # --- Repeal payload corroboration (effect_candidates.py) --------------------------
    "nz_repeal_candidate_from_history_note_payload_witness": (
        "Candidate-source witness: a repeal candidate is sourced from the history-note payload witness "
        "(the operation-surface's repeal-amending-source pointer) — the dry-run repeal kernel corroborates it."
    ),
    "nz_repeal_payload_target_corroborated": (
        "Repeal-payload corroboration outcome: the amending payload corroborates the repeal's target address; "
        "no separate directive is required for the dry-run to proceed."
    ),
    "nz_repeal_payload_corroboration_not_required_non_direct_payload": (
        "Repeal-payload corroboration outcome: corroboration is not required because the payload is a non-"
        "direct-amend shape (e.g. retrospective note); the repeal lowering proceeds without corroboration."
    ),
    "nz_repeal_payload_target_unparsed": (
        "Repeal-payload corroboration refusal: the amending payload was not parsed cleanly enough to "
        "corroborate the repeal's target — the refusal surfaces the parsing frontier rather than guessing."
    ),
    "nz_repeal_payload_target_mismatch": (
        "Repeal-payload corroboration refusal: the amending payload names a target that does not match the "
        "history-note's target address — corroboration fails (the repeal may still be lowered if the direct "
        "history-note witness is itself sufficient, but the mismatch is carried as a finding)."
    ),
    # --- Text-replace candidate sourcing (effect_candidates.py) ----------------------
    "nz_text_replace_candidate_from_direct_instruction_workqueue": (
        "Candidate-source witness: a text-replace candidate is sourced from the direct-instruction workqueue "
        "(the instruction_workqueue lane emitted a single-/each- occurrence text-substitution candidate)."
    ),
    "nz_text_replace_candidate_from_archived_source_change_witness": (
        "Candidate-source witness: a text-replace candidate is sourced from the archived source-change-text "
        "witness (before/after archived-XML text diff) — used when no direct-instruction witness is present."
    ),
    # --- Target-address resolution (operation_surface.py) ----------------------------
    "nz_target_address_duplicate_source_path": (
        "Target-address refusal: the resolved source path is duplicated in the live surface (two nodes share "
        "the path) — ambiguity carried as a refusal, never resolved by index (§1.7)."
    ),
    "nz_target_address_same_label_rebirth_duplicate": (
        "Target-address refusal: the candidate label appears twice in the surface under a same-label rebirth "
        "(provision was re-inserted after repeal) — lineage/migration must disambiguate; ambiguity carried as "
        "a refusal, never rebinding."
    ),
    "nz_target_address_non_current_skeleton_node": (
        "Target-address refusal: the resolved node is a non-current skeleton (an editorial shell from an "
        "older consolidation, not a current-body node) — the lowering refuses to target a non-current skeleton."
    ),
    "nz_target_address_document_level_facet": (
        "Target-address refusal: the candidate target is a document-level facet (not a structural provision) "
        "— the structural-lower lane refuses the non-structural facet."
    ),
    "nz_target_address_unsupported_target_kind": (
        "Target-address refusal: the target hint's kind is not one the lowering supports (section / schedule / "
        "part) — refused rather than coerced into a supported kind."
    ),
    "nz_target_address_skeleton_duplicate_resolved": (
        "Target-address resolution: the skeleton-duplicate (same label appears under both a current and a "
        "non-current skeleton) is resolved to the current skeleton — kept separate from "
        "`non_current_skeleton_node` so the residual is distinguishable (§1.10)."
    ),
    "nz_target_address_attached_heading_from_context": (
        "Target-address resolution: the target is an attached heading facet (target_hint.facet == heading) "
        "resolved from the source context — a heading-facet target lowered through its own lane, not the body-lower."
    ),
    "nz_target_address_attached_heading_source_path_unparsed": (
        "Target-address refusal (attached heading): the attached heading's source path could not be parsed — "
        "the heading-facet lowering is blocked rather than fabricated."
    ),
    "nz_target_address_attached_heading_missing_source_path": (
        "Target-address refusal (attached heading): the attached heading's source path is empty in the "
        "source — the heading-facet lowering is blocked rather than guessed."
    ),
    # --- Operation surface / bench / dependency lane ----------------------------------
    "nz_source_history_note_legacy_amended_provision_verb_recovery": (
        "Recovery (non-blocking evidence finding, AGENTS §2.1): the canonical "
        "<amending-operation> element was absent from the history note, and the "
        "operation verb was recovered from a SECOND <amended-provision> element "
        "in the legacy editorial-consolidation XML -- early-format history notes "
        "pre-XML-standardisation reuse the <amended-provision> tag for the verb "
        "phrase ('<amended-provision>Section X</amended-provision>: <amended-"
        "provision>amended</amended-provision>'). Witness: act_public_1956_47 "
        "@ 2001-10-02 nz-opw-244/245/246/255/257/305 (6 rows, Shape A); "
        "act_public_1876_79 @ 2003-07-01 nz-opw-5 (1 row, Shape D). Recovery "
        "is strict-superset additive: never blocks replay, never changes the "
        "classified op_family (downstream dispatch on operation_family is "
        "identical to the canonical case)."
    ),
    "nz_source_history_note_legacy_amending_instruction_verb_recovery": (
        "Recovery (non-blocking evidence finding, AGENTS §2.1): the canonical "
        "<amending-operation> element was absent from the history note, and the "
        "operation verb was recovered from a non-standard <amending-instruction> "
        "element in the early-format editorial-consolidation XML. Witness: "
        "act_public_1871_24 @ 1980-04-01 nz-opw-16 (1 row, Shape B). Recovery is "
        "strict-superset additive: never blocks replay, never changes the "
        "classified op_family."
    ),
    "nz_operation_surface_effect_lowering_not_implemented": (
        "Operation-surface refusal: the operation row carries an effect family the lowering has not yet "
        "implemented — a typed frontier blocker, never a silent drop. This is the forward-compatible "
        "fallback rule_id for an unrecognised lowering_readiness_status bucket (the named-bucket "
        "rule_ids below cover the known per-row readiness states; this constant surfaces only when a "
        "future bucket is added without extending the readiness->rule_id map, so a new bucket never "
        "silently re-derives to a guessed blocker)."
    ),
    "nz_operation_surface_effect_lowering_lane_unimplemented": (
        "Operation-surface row whose lowering_readiness_status is ready_for_amending_act_payload_extraction. "
        "The row's witness is READY to lower; the downstream canonical-effect lowering LANE that consumes "
        "ready rows is the unimplemented bit. Distinguishes 'I am ready' from 'I am blocked' on the same "
        "rule_id field so a benchmark reading only effect_blocking_rule_id can attribute the readiness state "
        "without re-deriving from lowering_readiness_status (AGENTS §1.10 distinct named diagnostic)."
    ),
    "nz_operation_surface_effect_lowering_amending_work_unarchived": (
        "Operation-surface row whose dependency_status resolved amending_work_resolved_unarchived: the "
        "amending act's XML is not archived locally. Acquisition frontier (not a parser gap, not a replay "
        "bug); the witness would lower once the act is acquired."
    ),
    "nz_operation_surface_effect_lowering_non_structural_facet": (
        "Operation-surface row whose target is a non-structural facet (Heading / Title / document-level — "
        "not a substantive section/subsection/paragraph). The lowering lane does not apply facet edits as "
        "canonical effects; the row is recorded-witness-only, never replayed."
    ),
    "nz_operation_surface_effect_lowering_target_address_non_current_skeleton_node": (
        "Operation-surface row whose target address candidate resolves to a non-current-skeleton node "
        "(a repealed/superseded copy in the end-of-document skeleton). The lowering refuses rather than "
        "applying an op to a stale skeleton copy."
    ),
    "nz_operation_surface_effect_lowering_target_hint_unparsed": (
        "Operation-surface row whose target hint could not be parsed (e.g. 'Section 1A to 1C' range, "
        "compound 'Schedule 1 clause 5' target). The lowering refuses rather than guessing a "
        "decomposition; needs the target-hint recogniser extended to admit the shape."
    ),
    "nz_operation_surface_effect_lowering_target_hint_missing": (
        "Operation-surface row whose amended_provision carried no parseable target hint. The lowering "
        "refuses rather than fabricating a target."
    ),
    "nz_operation_surface_effect_lowering_citation_unparsed": (
        "Operation-surface row whose amending-work citation could not be parsed into (year, number). "
        "Acquisition/parser frontier; the witness would lower once the citation recogniser admits the shape."
    ),
    "nz_operation_surface_effect_lowering_citation_missing": (
        "Operation-surface row whose amending-work citation was entirely absent from the history note. "
        "Source-footing gap; the witness is honest residue, never silently dropped."
    ),
    "nz_operation_surface_effect_lowering_operation_missing": (
        "Operation-surface row whose amending-operation verb element was absent (no <amending-operation> "
        "in the history note). The op-family classifier returns __missing__ and the lowering refuses rather "
        "than fabricating a verb."
    ),
    "nz_operation_surface_effect_lowering_operation_unclassified": (
        "Operation-surface row whose amending-operation verb was present but not in _KNOWN_OPERATION_FAMILIES "
        "and not a recognised synonym (e.g. 'revoked' was unclassified before the revoked-as-repealed synonym "
        "landed). The lowering refuses rather than guessing a family."
    ),
    "nz_operation_surface_effect_lowering_editorial_change_non_canonical": (
        "Operation-surface row whose family is 'editorial change' (PCO editorial change per Legislation Act "
        "2019 s.86/s.87). The lowering intentionally does not apply editorial changes as canonical effects; "
        "the row is recorded-witness-only, surfaced as a typed frontier blocker rather than silently "
        "suppressed."
    ),
    "nz_operation_surface_effect_lowering_duplicate_source_path": (
        "Operation-surface row whose source_xml_path collides with another row's source_xml_path (same "
        "history-note clause, two op witnesses). The lowering refuses both rather than guessing which to "
        "lower; needs disambiguation of the duplicate-source-path shape."
    ),
    "nz_operation_surface_effect_lowering_same_label_rebirth_duplicate": (
        "Operation-surface row whose target label was re-created after a prior repeal (a same-label rebirth). "
        "The lowering refuses rather than conflating the two identities (§2.8 identity-vs-lineage); needs "
        "explicit migration event for the rebirth."
    ),
    "nz_replay_canonical_effects_not_implemented": (
        "Bench refusal: the canonical-effects replay path is not implemented for this row (the bench "
        "surface reports it as blocked rather than fabricating an effect)."
    ),
    "nz_oracle_agreement_candidate_replay_missing": (
        "Bench refusal (oracle agreement): the candidate replay output is missing, so the oracle-agreement "
        "step cannot run — surfaced as a typed blocker, never a false-agreement."
    ),
    "nz_effect_candidates_not_replayed": (
        "Bench refusal: the effect-candidates surface was not replayed (canonical-effects replay unavailable) "
        "— surfaced as a typed blocker rather than skipping the report row."
    ),
    "nz_effect_candidate_emitted_operation_missing": (
        "Effect-candidate refusal: the candidate row emitted no operation (lowering produced no op) — "
        "the candidate is not counted as a replayable effect."
    ),
    "nz_effect_candidate_not_ready": (
        "Effect-candidate refusal (readiness fallback): the candidate is not readiness-ready — the row "
        "carries this fallback receipt rather than being silently filtered."
    ),
    "nz_effect_preflight_refused_blocked_candidate_rows": (
        "Effect-preflight refusal: the preflight refused candidate rows that are blocked at the readiness "
        "or target-resolution planes — surfaced as a typed refusal set rather than silently dropped."
    ),
    "nz_effect_preflight_no_candidate_rows": (
        "Effect-preflight outcome: the work has no candidate effect rows in the preflight — an honest "
        "zero-denominator, never a fabricated candidate."
    ),
    "nz_effect_preflight_candidate_operation_missing": (
        "Effect-preflight refusal: a candidate row carries no operation (lowering produced no op for it) — "
        "the preflight surfaces the absence rather than counting the row as a candidate."
    ),
    "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable": (
        "Effect-preflight refusal: the candidate rows are source-change-only (no executable amend verb) — "
        "they are not dry-run replayable; the preflight surfaces the lane rather than guessing an op."
    ),
    "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable": (
        "Effect-preflight refusal: the candidate rows required target recovery (inferred, not explicit) — "
        "they are not dry-run replayable (§1.1); the preflight surfaces the lane."
    ),
    "nz_effect_preflight_non_replayable_candidates_not_dry_run_replayable": (
        "Effect-preflight refusal: the candidate rows are non-replayable for a typed reason "
        "(unsupported family / non-canonical effect) — surfaced as a frontier lane."
    ),
    # --- Frontier / manual-compilation lane (frontier_work_items.py) ----------------
    "nz_frontier_work_item_as_canonical_operation": (
        "Frontier classification: the work item is a manual-compilation claim that would become a canonical "
        "operation if claimed — typed as frontier (not lowered as a guessed op)."
    ),
    "nz_frontier_work_item_as_replay_authorization": (
        "Frontier classification: the work item is a manual-compilation claim that would authorize replay "
        "(an owned claim) — typed as frontier (never silently absorbs the claim as authority)."
    ),
    "nz_frontier_work_item_non_executable": (
        "Frontier classification: the work item is a non-executable manual-compilation claim (savings / "
        "contingent commencement / cross-act placement) — typed as frontier, reported separately from a "
        "coverage miss."
    ),
    "nz_latest_oracle_text_as_payload_authority": (
        "Frontier surface identity: the latest-oracle-text surface is the manual-compilation payload "
        "authority for this frontier work item — carried as the surface identity, not as a per-rule firing."
    ),
    "nz_latest_oracle_text_presence": (
        "Frontier surface identity: a latest-oracle-text presence token carried by the frontier work item "
        "— the surface identity, not a per-rule firing."
    ),
    "nz_obvious_before_after_diff_as_mutation_boundary_proof": (
        "Frontier proposal: an obvious before/after archived-XML diff was used as a (weak) mutation-"
        "boundary proof proposal — proposal only, never replay authority (AGENTS §0 — evidence is not authority)."
    ),
    "nz_unclassified_manual_frontier": (
        "Frontier classification refusal: the work item could not be classified into a named frontier kind — "
        "carried as an unclassified-manual-frontier residual, never silently absorbed."
    ),
    "nz_history_note_dependency_unarchived": (
        "Dependency refusal: a history-note-cited amending work is referenced but unarchived — the "
        "dependency cannot be resolved to a payload; surfaced as a typed dependency refusal."
    ),
    # --- Bench / source-parse (benchmark.py) ------------------------------------------
    "nz_benchmark_latest_xml_missing": (
        "Bench refusal: the latest archived XML for the work is missing — the bench row cannot run, "
        "surfaced as a typed blocker rather than skipped."
    ),
    "nz_benchmark_latest_xml_unreadable": (
        "Bench refusal: the latest archived XML for the work is unreadable — the bench row cannot parse, "
        "surfaced as a typed blocker."
    ),
    "nz_benchmark_source_parse_error": (
        "Bench refusal: the source tree could not be parsed for the work — the bench row cannot run; "
        "the parse diagnostic is carried rather than fabricated as agreement."
    ),
    "nz_archived_xml_version_change_window_source_only": (
        "Change-window source identity: the archived-XML version change-window was built from the source-"
        "only side (no on-or-after oracle was available) — a source-only window, never a fabricated on-or-after."
    ),
    "nz_archived_xml_version_date_window_source_only": (
        "Change-window source identity (date-window variant): the archived-version date window was built "
        "from the source-only side (no on-or-after version available at that date) — surfaced as a distinct "
        "identity from the change-window variant so the two readings are distinguishable (§1.10)."
    ),
    # --- Acquisition lane refusal vocabulary (acquisition.py) -----------------------
    "nz_acquire_xml_format_missing": (
        "Acquisition refusal: the fetched resource's advertised format was missing or unrecognized — "
        "the source cannot be ingested; surfaced as a typed refusal rather than guessed content."
    ),
    "nz_acquire_rate_limit_stop": (
        "Acquisition refusal: the Legislation API rate-limit ceiling was hit — acquisition halts with a "
        "typed receipt; the work is re-fetchable later, never silently aborted."
    ),
    "nz_acquire_json_decode_failed": (
        "Acquisition refusal: the fetched JSON could not be decoded (malformed payload) — surfaced as a "
        "typed refusal so the URL/witness is traced; never silently retried with a fabricated body."
    ),
    "nz_acquire_json_shape_unexpected": (
        "Acquisition refusal: the fetched JSON decoded but its shape did not match the API v0 schema — "
        "surfaced as a typed refusal so the next-step (fix schema or fix adapter) is named."
    ),
    # --- Commencement / temporal-state effects (commencement.py) ---------------------
    "nz_commencement_recorded_in_force_status_temporal_state_effect": (
        "AGREEMENT-output: a commencement is recorded as a typed in-force status temporal-state effect — "
        "the work's commencement-date is a sound temporal-state fact, never a structural mutation."
    ),
    "nz_commencement_refused_target_address_not_determinate_candidate": (
        "Commencement refusal: the commencement clause's target address could not be resolved to a single "
        "determinate candidate — refused rather than guessed (§1.1); carried as a typed temporal_refusal."
    ),
    "nz_commencement_refused_effective_date_not_determinate_iso": (
        "Commencement refusal: the commencement's effective date cannot be reduced to a single ISO date "
        "(contingent development / undated instrument) — refused rather than estimated (§1.7)."
    ),
    # --- Closure lane (closure.py) ---------------------------------------------------
    "nz_closure_latest_xml_missing": (
        "Closure refusal: the latest archived XML for the work's closure curve is missing — the closure "
        "row cannot run; surfaced as a typed blocker."
    ),
    "nz_closure_latest_xml_locator_unreadable": (
        "Closure refusal: the latest archived XML locator is unreadable — the closure row cannot parse; "
        "surfaced as a typed blocker rather than fabricated agreement."
    ),
    # --- Dependencies lane (dependencies.py) -----------------------------------------
    "nz_dependency_reprint_amend_unparsed": (
        "Dependency refusal: a reprint/amend-type dependency could not be parsed into a typed payload — "
        "surfaced as a refusal rather than silently absorbed as a dependency edge."
    ),
    "nz_latest_xml_locator_candidate_rejected": (
        "Locator refusal: the latest-XML locator for the work could not be resolved to a single archived "
        "candidate (multiple or no candidates) — the locator ambiguity is preserved as a typed refusal (§1.7)."
    ),
    # --- Payload-surface refusals (payload_surface.py) -------------------------------
    "nz_payload_operation_not_payload_ready": (
        "Payload-surface refusal: the operation row is not payload-ready (the readiness lane did not mark "
        "it ``payload_found``) — the payload lane refuses rather than forcing the lowering."
    ),
    "nz_payload_dependency_unarchived": (
        "Payload-surface refusal: the amending-work dependency cited by the payload row is unarchived — "
        "the payload cannot be extracted; surfaced as a typed dependency refusal."
    ),
    "nz_payload_href_missing": (
        "Payload-surface refusal: the amending-provision href is missing from the witness row — the payload "
        "cannot be located; surfaced as a refusal, never invented."
    ),
    "nz_payload_href_not_found": (
        "Payload-surface refusal: the amending-provision href was not found in the amending act XML — "
        "the payload cannot be extracted; surfaced as a refusal."
    ),
    # --- Amendment-chain replay refusals (chain_replay.py — experimental all-families lane) ---
    "nz_chain_replay_op_target_resolution_not_exact": (
        "Chain-replay refusal: the op's target did not resolve to an exact (candidate) source path — "
        "chain replay refuses inferred scope rather than widening the resolved path (§1.1)."
    ),
    "nz_chain_replay_op_unextractable_no_source_path": (
        "Chain-replay refusal: the operation's target has no source path (it was emitted/amended without a "
        "path); the op is unextractable, never silently dropped."
    ),
    "nz_chain_replay_target_absent_in_evolving_tree": (
        "Chain-replay refusal: the named target node is absent from the evolving before-tree at this step — "
        "the op cannot act on it; refused rather than rebinding (§1.1)."
    ),
    "nz_chain_replay_target_ambiguous_in_evolving_tree": (
        "Chain-replay refusal: the resolved target path matches more than one node in the evolving tree at "
        "this step — ambiguity preserved, never resolved by parser order (§1.7)."
    ),
    "nz_chain_replay_target_already_tombstoned_in_evolving_tree": (
        "Chain-replay refusal (honest skip — pre-cutoff): the target was already tombstoned in the evolving "
        "tree (an earlier amendment already repealed it); the op is correctly a no-op (per status doc "
        "Limits #3 — pre-cutoff baked-in changes), surfaced as a typed skip rather than silently dropped."
    ),
    "nz_chain_replay_amending_work_or_provision_href_unresolved": (
        "Chain-replay refusal: the amending work cited by the op could not be resolved to an archived act, "
        "or the amending-provision href was not found — the payload cannot be extracted."
    ),
    "nz_chain_replay_amending_payload_not_extractable": (
        "Chain-replay refusal: the amending payload's replacement/insertion subtree could not be cleanly "
        "extracted — the structural op is blocked rather than applied with a fabricated payload."
    ),
    "nz_chain_replay_replace_apply_left_subtree_unchanged": (
        "Chain-replay refusal: applying the extracted replacement subtree to the evolving tree produced no "
        "change (no-op) — the proof is not materialized."
    ),
    "nz_chain_replay_text_apply_left_node_unchanged": (
        "Chain-replay refusal: applying the text substitution produced no change to the evolving-tree node "
        "(no-op) — the proof is not materialized; the candidate is rejected, never silently credited."
    ),
    "nz_chain_replay_text_old_text_not_single_occurrence_in_evolving_tree": (
        "Chain-replay refusal: the old_text does not occur exactly once in the evolving-tree target at this "
        "step — neither a single-occurrence substitution nor a valid each-place proof."
    ),
    "nz_chain_replay_insert_target_already_present_in_evolving_tree": (
        "Chain-replay refusal: the new node to insert is already present in the evolving tree — the insertion "
        "would duplicate; the chain refuses rather than deduping silently (manual-compilation frontier)."
    ),
    "nz_chain_replay_insert_anchor_not_derivable_from_label_or_siblings": (
        "Chain-replay refusal: the pre-existing anchor sibling cannot be derived from the inserted label "
        "or its sibling group in the evolving tree — refused rather than guessed (§1.7)."
    ),
    "nz_chain_replay_insert_anchor_or_parent_not_unique_in_evolving_tree": (
        "Chain-replay refusal: the derived anchor (or nested parent) is not unique in the evolving tree at "
        "this step — ambiguity preserved, never resolved by position (§1.7)."
    ),
    "nz_chain_replay_effective_date_after_latest_archived_version": (
        "Chain-replay refusal: the op's effective date falls after the latest archived version of the work "
        "(beyond the verified consolidation window) — replay refuses rather than extrapolating."
    ),
    "nz_chain_replay_insert_def_term_case_fold_collision_recognized": (
        "Family-D (def-term case-fold collision) -- chain-replay recognised that "
        "an INSERT op's ``def-para:<term>`` leaf targets the SAME def-term as an "
        "existing carried-tree ``def-para:<Same-Term-Different-Case>`` (case-only "
        "label difference). Per AGENTS §1.4 (no silent sibling absorption by "
        "label text equality or case-touch alone): the recognition emits a typed "
        "skip receipt (distinct from the generic insert-already-present bucket) "
        "so the absorption is auditable under its own rule_id rather than silently "
        "absorbed or duplicated. Test pin: ``test_def_term_case_fold_collision_"
        "recognised_and_inhibits_duplicate_insert`` in "
        "tests/test_new_zealand_chain_replay.py. Witnesses verified 2026-06-27 "
        "across the smoke corpus: 8 Family-D divergences pre-fix -> 22 Family-D "
        "skips fired + 2 residual 'Government Superannuation Fund Authority' "
        "content-difference cases deferred to Family-F (a new family probe "
        "carried-tree's 'or Authority'-suffix content difference, not just case)."
    ),
    "nz_chain_replay_insert_def_term_or_suffix_collision_recognized": (
        "Family-F (def-term trailing-'or X' suffix collision) -- chain-replay "
        "recognised that an INSERT op's ``def-para:<term>`` leaf targets the SAME "
        "def-term as an existing carried-tree ``def-para:<term> or <word>`` whose "
        "<word> repeats the preceding word (a 2007-era NZ reprint-tool artifact "
        "that placed the entire term+suffix inside a single <def-term> element; "
        "the amending act and the latest archived oracle both use the clean form "
        "without the suffix). Per AGENTS §1.4 (no silent sibling absorption by "
        "label text equality or suffix-touch alone): the recognition emits a "
        "typed skip receipt (distinct from Family-D's case-fold and from the "
        "generic insert-already-present) so the suffix-stripping absorption is "
        "auditable under its own rule_id. Witnesses verified 2026-06-27: "
        "act_public_1956_47 nz-opw-81 ('Government Superannuation Fund Authority' "
        "vs carried-tree '... Authority or Authority') and nz-opw-82 "
        "('... Authority board' vs carried-tree '... board or board')."
    ),
    # --- Effect readiness voter — amendment/structural payload extraction state --------
    "nz_effect_readiness_amendment_semantics_not_extracted": (
        "Readiness refusal: the amendment semantics could not be extracted from the payload — the readiness "
        "lane refuses before any structure lowering (payload parse frontier)."
    ),
    "nz_effect_readiness_operation_family_not_canonical": (
        "Readiness refusal: the operation family derived from the history note is not a canonical replay-"
        "able family — the readiness lane refuses rather than coercing to a related family."
    ),
    "nz_effect_readiness_structural_payload_semantics_not_extracted": (
        "Readiness refusal: the structural payload semantics could not be extracted (the structural payload "
        "shape did not parse) — readiness refuses before structural lowering."
    ),
    # --- Latest-oracle-text target resolution (instruction_workqueue.py) --------------
    "nz_instruction_latest_oracle_text_not_applicable": (
        "Latest-oracle-text witness outcome (not_applicable): the latest oracle text is not applicable to "
        "this op (the op's effect shape doesn't use the latest-oracle-text witness)."
    ),
    "nz_instruction_latest_oracle_text_target_document_unavailable": (
        "Latest-oracle-text witness refusal: the target document is unavailable (cannot be resolved in the "
        "latest archived XML) — the witness is recorded as unavailable, never fabricated."
    ),
    "nz_instruction_latest_oracle_text_target_address_unmapped": (
        "Latest-oracle-text witness refusal: the resolved target address could not be mapped to a source "
        "path in the latest archived XML — the witness is refused rather than guessed (§1.1)."
    ),
    "nz_instruction_latest_oracle_text_target_granularity_not_indexed": (
        "Latest-oracle-text witness refusal: the target granularity (section/subsection/paragraph) is not "
        "indexed in the latest archived XML — the witness is refused rather than silently downgraded."
    ),
    "nz_instruction_latest_oracle_text_target_source_node_missing": (
        "Latest-oracle-text witness refusal: the resolved target source node is missing from the latest "
        "archived XML — the witness is recorded as missing, never invented."
    ),
    "nz_instruction_latest_oracle_text_target_source_node_not_unique": (
        "Latest-oracle-text witness refusal: the resolved target source path matches more than one node "
        "in the latest archived XML — ambiguity preserved, never resolved by index (§1.7)."
    ),
    "nz_instruction_latest_oracle_text_target_source_node_deleted": (
        "Latest-oracle-text witness refusal: the resolved target source node is marked deleted in the latest "
        "archived XML — the witness records the deletion rather than treating the node as live."
    ),
    "nz_instruction_latest_oracle_target_exact_source_path": (
        "Latest-oracle-text witness resolution: the target was resolved to an exact source path in the "
        "latest archived XML — the resolved path is the prior-segment that the source-change witness will diff against."
    ),
    "nz_instruction_latest_oracle_target_via_unlabeled_source_carrier": (
        "Latest-oracle-text witness resolution (fallback): the target was resolved via an unlabeled source "
        "carrier (the latest oracle text carried it without a label) — an inferred-resolution fallback, surfaced "
        "as a distinct resolver lane so the exact-match lane is distinguishable (§1.10)."
    ),
    # --- Instruction workqueue refusals: multi-clause cases --------------------------
    "nz_instruction_semantics_blocked_multi_clause_no_matching_target": (
        "Refusal (multi-clause): no clause in the multi-clause substitute shape produced a matching target — "
        "the lowering is blocked rather than emitting a partial candidate."
    ),
    "nz_instruction_semantics_blocked_multi_clause_target_ambiguous": (
        "Refusal (multi-clause): the multi-clause substitute shape's clause targets are ambiguous (multiple "
        "interpretations) — the lowering refuses rather than picking a parser-order interpretation (§1.7)."
    ),
    "nz_instruction_semantics_blocked_multi_clause_payload": (
        "Refusal (multi-clause): the multi-clause payload could not be cleanly parsed — the lowering is "
        "blocked rather than lowering one clause and dropping the others silently."
    ),
    # --- Dynamic-emitted rule_ids (f-string concatenation results the test suite asserts against) ---
    # These rule_ids are never AST-visible full literals in src/ — they are built at
    # runtime via f-string templates (``f"nz_X_{status_value}"``) so the
    # AST-discovery test on src/ can only see the bare prefix. The test suite
    # asserts against the FULL concatenated id and so confirms they fire at runtime;
    # cataloging them keeps the anti-drift guard honest about both surfaces.
    # --- nz_target_address_hint_<status> (operation_surface.py: f"nz_target_address_hint_{target_hint.status}") ---
    "nz_target_address_hint_missing": (
        "Target-resolution refusal: no target hint was extracted from the witness row (the target "
        "citation was empty) — surfaced as a typed refusal, never invented."
    ),
    "nz_target_address_hint_unparsed": (
        "Target-resolution refusal: the target hint was not parseable into any known shape (no "
        "section / schedule / part matched the citation) — refused rather than guessed (§1.11)."
    ),
    "nz_target_address_hint_compound_target_unparsed": (
        "Target-resolution refusal: the target citation is compound (e.g. multi-segment like "
        "'subsection (1)(a)(i)') and the hint parser could not reduce it — refused rather than "
        "silently abbreviated."
    ),
    # --- nz_lowering_readiness_<status> (operation_surface.py: f"nz_lowering_readiness_{readiness_status}") ---
    "nz_lowering_readiness_blocked_amending_work_resolved_unarchived": (
        "Lowering-readiness refusal: the amending work cited by the witness row was resolved (its ID "
        "is known) but it is NOT archived locally — the lowering-readiness lane refuses the row's "
        "structural-payload extraction (acquisition frontier)."
    ),
    "nz_lowering_readiness_blocked_non_structural_facet": (
        "Lowering-readiness refusal: the resolved target is a non-structural facet (a heading "
        "attached to a body provision rather than a structural target) — the structural-lower lane "
        "refuses the facet shape rather than absorbing it as a body-lower (§1.3)."
    ),
    "nz_lowering_readiness_blocked_operation_missing": (
        "Lowering-readiness refusal: the witness row's amended family is missing from the survey "
        "(no operation classifier output) — typed refusal rather than guessed family."
    ),
    "nz_lowering_readiness_blocked_operation_unclassified": (
        "Lowering-readiness refusal: the witness row's amended family is unclassified (the operation "
        "classifier saw it but did not match a canonical family) — typed refusal."
    ),
    "nz_lowering_readiness_blocked_same_label_rebirth_duplicate": (
        "Lowering-readiness refusal: the candidate target is one of a same-label-rebirth duplicate "
        "pair (a section was re-inserted after repeal) — the readiness lane refuses rather than "
        "rebinding; lineage/migration must disambiguate (§1.6 / §1.7)."
    ),
    "nz_lowering_readiness_blocked_target_hint_compound_target_unparsed": (
        "Lowering-readiness refusal: the target-hint parser could not reduce the compound target "
        "citation into typed path steps — surfaced as the readiness-blocked variant of "
        "nz_target_address_hint_compound_target_unparsed, distinguishing the readiness plane from "
        "the target-resolution plane (§1.10)."
    ),
    "nz_lowering_readiness_blocked_target_hint_unparsed": (
        "Lowering-readiness refusal: the target-hint parser returned NONE of the known target shapes "
        "(section / schedule / part); surfaced as the readiness-blocked variant of "
        "nz_target_address_hint_unparsed (§1.10 distinguishability)."
    ),
    # --- nz_operation_surface_<operation_status> (operation_surface.py: f"nz_operation_surface_{operation_status}") ---
    "nz_operation_surface_missing": (
        "Operation-surface classifier refusal: the witness row's classified operation family is "
        "missing (no classified amendment verb) — surfaced as a typed refusal, the row stays "
        "UNBLOCKED but never silently dropped."
    ),
    "nz_operation_surface_unclassified": (
        "Operation-surface classifier refusal: the witness row's amended verb did not match any "
        "canonical operation family — typed refusal, never coerced to a related family (§1.2)."
    ),
    # --- nz_source_change_text_<status> (effect_candidates.py: f"nz_source_change_text_{status}") ---
    "nz_source_change_text_observed_single_replacement": (
        "Source-change witness outcome: before/oracle text-diff shows exactly one occurrence of the "
        "old_text replaced by the new_text — strong witness for a single-occurrence text-replace op."
    ),
    "nz_source_change_text_partial_text_change_observed": (
        "Source-change witness outcome: before/oracle text-diff shows the old/new text co-occurred "
        "in a way that does NOT cleanly match a single or each-place substitution — the witness is "
        "partial/ambiguous, never silently lowered as agreement."
    ),
    # --- nz_text_replace_witness_support_<status> (effect_candidates.py: f"nz_text_replace_witness_support_{status}") ---
    "nz_text_replace_witness_support_latest_oracle_and_source_change_observed": (
        "Witness-support outcome: BOTH the latest-oracle-text witness AND the archived source-change "
        "witness corroborate the candidate substitution — strongest evidence combination."
    ),
    "nz_text_replace_witness_support_source_change_observed_target_mismatch": (
        "Witness-support refusal: the latest-oracle-text witness and the source-change witness "
        "corroborate substitution but resolved to DIFFERENT target addresses — typed refusal rather "
        "than silently rebinding to one (§1.7)."
    ),
    # --- nz_effect_readiness_<rule_suffix> (effect_readiness.py: f"nz_effect_readiness_{rule_suffix}") ---
    # ``rule_suffix = payload_status.removeprefix('blocked_')``, so:
    # `nz_effect_readiness_payload_witness_not_available` = payload_status="blocked_payload_witness_not_available".
    "nz_effect_readiness_payload_witness_not_available": (
        "Effect-readiness refusal: the readiness lane's payload-witness row is missing or blocked at "
        "the readiness plane — the row cannot be classified for lowering; surfaced as a typed refusal "
        "rather than guessed payload semantics."
    ),
    "nz_effect_readiness_operation_not_payload_ready": (
        "Effect-readiness refusal: the readiness lane's operation row is not payload-ready (its "
        "payload-status did not reach `payload_found`) — surfaced as a typed refusal before lowering."
    ),
    # --- nz_instruction_latest_oracle_text_<status> (instruction_workqueue.py: f"nz_instruction_latest_oracle_text_{status}") ---
    "nz_instruction_latest_oracle_text_oracle_new_text_only": (
        "Latest-oracle-text witness outcome: the on-or-after oracle carries the new_text but the "
        "before-version does not — the substitution is observable in oracle-new-text-only form (one "
        "side of the diff is empty, the other carries the new content)."
    ),
    # --- nz_api_v0_version_detail_http_error (acquisition.py: f"{rule_id}_http_error") ---
    "nz_api_v0_version_detail_http_error": (
        "Acquisition refusal (HTTP-error variant): an HTTP failure occurred when fetching the "
        "version-detail payload via the Legislation API v0 — surfaced as a variant of the "
        "nz_api_v0_version_detail source-lane tag with the `_http_error` suffix, distinguishing the "
        "fetcher-failure lane from the source-lane-flavour lane (§1.10)."
    ),
}


# Consolidate per AGENTS §2.5 (one parser per family, no rival recognizers without
# a parity gate / retirement plan). The production adapter owns the dry-oracle
# 16 authoritatively (its richer ``NZRuleCatalogEntry`` adds a confidence tier); the
# filter below drops any adapter-owned rule_id from the extras, then the public
# catalog composes BOTH. The parity test pins this so a future drift (an
# adapter-owned rule_id ALSO appearing in extras with different prose) becomes
# a failing test rather than silent drift — single source of truth for every rule.
_EXTRA_NZ_RULE_SPECS = {
    rule_id: believed_spec
    for rule_id, believed_spec in _EXTRA_NZ_RULE_SPECS.items()
    if rule_id not in _ADAPTER_RULE_SPECS
}
_NZ_RULE_SPECS: Dict[str, str] = {**_EXTRA_NZ_RULE_SPECS, **_ADAPTER_RULE_SPECS}


def get(rule_id: str) -> str:
    """Return the believed_spec prose for ``rule_id`` (or ``""`` if uncataloged)."""
    return _NZ_RULE_SPECS.get(rule_id, "")
