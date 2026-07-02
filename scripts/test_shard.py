#!/usr/bin/env python3
"""Run named LawVM pytest shards.

This is an iteration/matrix helper.  The canonical local gate remains
``scripts/ci.sh``.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = REPO_ROOT / "tests"

EXCLUDED_TESTS = {
    "test_fi_citation_routing.py": "large skip-heavy/gold-style corpus route inventory",
    "test_fi_pipeline_gold.py": "gold corpus suite; intentionally outside bounded non-network CI",
}

SHARD_PATTERNS: dict[str, tuple[str, ...]] = {
    "boundary": (
        "test_fi_conformance.py",
    ),
    "estonia_sources": (
        "test_clause_ast_ee_validation.py",
        "test_ee_act_identity_registry.py",
        "test_ee_archive_guard.py",
        "test_ee_bench.py",
        "test_ee_authority_grounding.py",
        "test_ee_compare_normalization.py",
        "test_ee_consolidation_candidates_cli.py",
        "test_ee_consolidation_error_candidates.py",
        "test_ee_fetch_diagnostics.py",
        "test_ee_fetch.py",
        "test_ee_frontier.py",
        "test_ee_guard_liveness.py",
        "test_ee_inline_directive_shape_a.py",
        "test_ee_invariant_bisect.py",
        "test_ee_structural_invariants.py",
        "test_ee_unrecognized_source_shape.py",
        "test_ee_inspect_source.py",
        "test_ee_new_tools.py",
        "test_ee_pair_planning.py",
        "test_ee_replay_summary.py",
        "test_ee_replayability_frontier.py",
        "test_ee_reporting_tools.py",
        "test_ee_residual_inventory.py",
        "test_ee_self_consistency.py",
        "test_ee_source_adjudication.py",
        "test_ee_source_anchor.py",
        "test_ee_tool_promotions.py",
        "test_ee_op_provenance_totality.py",
    ),
    "estonia_replay_semantics": (
        "test_ee_apply_conserved.py",
        "test_ee_apply_semantics.py",
        "test_ee_apply_filter_result.py",
        # §2.9 guard-liveness: EE as first non-UK/non-FI consumer of the core
        # per-op mutation-boundary (D1) + commencement-effect totality (D7) audits.
        "test_ee_per_op_audit_probe.py",
        # §2.5 retirement parity gate for the EE same-moment cross-act detector.
        "test_ee_cross_act_same_moment_parity.py",
        # D7 deepcopy migration tests for estonia/target_resolution.py.
        "test_ee_target_resolution_deepcopy.py",
        "test_ee_blame_provision_walk.py",
        "test_ee_instruction_waist.py",
        "test_ee_parser_normalization.py",
        "test_ee_same_moment_ambiguity.py",
        # Wave 0 ordering-kernel cutover parallel-run equality gate (EE).
        "test_ee_order_ops_parallel_run.py",
        # Wave 3 apply-seam cutover parallel-run equality gate (EE) + the first
        # real frontend ingestion of core ``assert_coverage_totality``.
        "test_ee_apply_seam_parallel_run.py",
        "test_ee_coverage_totality_ingestion.py",
        # XP-06 (#107): EE per-op WriteReceipt emission — closes the carrier gap
        # (EE was the only frontend with zero WriteReceipt construction sites)
        # via estonia/ee_write_receipts.emit_ee_op_receipt threaded into
        # apply_ee_ops through the additive write_receipts_out opt-in sink.
        "test_ee_write_receipt.py",
        # #108-EE: LS-01 per-op mutation-boundary BLOCK mode. The chapter-nesting
        # declaration fix (_ee_resolved_boundary_prefixes) drives the real-corpus
        # boundary-escape count to 0, then flips boundary_mode "off" -> "block".
        "test_ee_boundary_enforcement.py",
    ),
    "estonia_replay_logic": (
        "test_ee_replay_logic.py",
        "test_ee_structural_op_witness_tagging.py",
        "test_ee_witness_attribution.py",
    ),
    "norway": (
        "test_no_*.py",
        "test_norway_*.py",
    ),
    "new_zealand_sources": (
        "test_new_zealand_acquisition.py",
        "test_new_zealand_closure.py",
        "test_new_zealand_dates.py",
        "test_new_zealand_dependencies.py",
        "test_new_zealand_source_tree.py",
        "test_new_zealand_version_diff.py",
    ),
    "new_zealand_effects": (
        "test_new_zealand_actual_replay.py",
        "test_new_zealand_actual_replay_structural.py",
        "test_new_zealand_actual_replay_corpus_smoke.py",
        "test_new_zealand_bench_regression.py",
        "test_new_zealand_chain_replay.py",
        "test_new_zealand_chain_replay_corpus.py",
        "test_new_zealand_chain_replay_idempotency_smoke.py",
        "test_new_zealand_commencement.py",
        "test_new_zealand_dry_run.py",
        "test_new_zealand_dry_run_corpus.py",
        "test_new_zealand_dry_run_divergence.py",
        "test_new_zealand_dry_run_insert.py",
        "test_new_zealand_dry_run_north_star.py",
        "test_new_zealand_dry_run_oracle.py",
        "test_new_zealand_dry_run_replace.py",
        "test_new_zealand_dry_run_text_replace.py",
        "test_new_zealand_effect_candidates.py",
        "test_new_zealand_effect_preflight.py",
        "test_new_zealand_effect_readiness.py",
        "test_new_zealand_instruction_workqueue.py",
        "test_new_zealand_oracle_normalization.py",
        "test_new_zealand_self_consistency.py",
        "test_new_zealand_text_comparison.py",
    ),
    "new_zealand_reports": (
        "test_new_zealand_agreement.py",
        "test_new_zealand_agreement_taxonomy.py",
        "test_new_zealand_bench.py",
        "test_new_zealand_bench_corpus.py",
        "test_new_zealand_benchmark.py",
        "test_new_zealand_benchmark_declaration.py",
        "test_new_zealand_evidence_pack.py",
        "test_new_zealand_frontier_work_items.py",
        "test_new_zealand_operation_surface.py",
        "test_new_zealand_payload_surface.py",
        "test_new_zealand_spec_ledger.py",
    ),
    "sweden_fetch": (
        "test_sweden_fetch.py",
    ),
    "sweden_misc": (
        "test_sweden_grafter.py",
        "test_sweden_tools.py",
        # iter4 W1 C2: applies-raise scan-lane bucketing integration test (drives
        # apply_raise through scan_se_official_replay_act → asserts
        # BenchStatus.CRASH, not SOURCE_UNAVAILABLE).
        "test_se_scan_lane_apply_raise_bucketing.py",
        # SE conserved apply wrapper fire-drill tests (AGENTS.md §1.8, §2.9).
        "test_se_apply_conserved.py",
        # §1.7 same-moment cross-act ambiguity pre-pass wired into apply_se_ops
        # (B1: routes SE through lawvm.core.cross_act_same_moment).
        "test_se_same_moment_ambiguity.py",
        # Wave 0 ordering-kernel parallel-run equality gate: old direct-detector
        # path == new order_ops(se_ordering_profile()) path on the SE corpus
        # (ordered ops + findings), driving the real apply_se_ops fold.
        "test_se_order_ops_parallel_run.py",
        # §2.9 per-op mutation-boundary probe wired into apply_se_ops
        # (observation-only, env-gated, default-off; consumes the core
        # lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary).
        "test_se_mutation_boundary_per_op_probe.py",
        # Wave 2 apply-seam cutover parallel-run equality gate: seam-based
        # apply_se_ops == pre-cutover materialized statute + adjudications, AND
        # the seam-synthesized WriteReceipts are byte-identical to SE's existing
        # se_replay_write_receipts production emitter (the Wave-2 deliverable).
        "test_se_apply_seam_parallel_run.py",
        # Byte-span SourceAnchor arm for Sweden (task #93): REACHABLE via a
        # separate UTF-8 anchor artifact (json.dumps(act, ensure_ascii=False)) +
        # per-op clause raw_text. The canonical compile artifact is UNCHANGED;
        # only the anchor source is re-encoded. Pins per-op granularity, byte-exact
        # re-verification, honest None, and grounding-neutrality.
        "test_se_source_anchor.py",
        # EV-05 execution-authorization proof carrier: SE mints/reads a typed
        # ExecutionAuthorization from each op's affecting-act identity so the
        # universal apply-seam EV-05 observe gate goes quiet on authorized ops
        # and fires on the unauthorized residue (observe-only, byte-identical).
        # AM-01 is intentionally NOT wired (SE has no Parsed-vs-Recovered signal).
        "test_se_proof_carrier.py",
    ),
    "uk": (
        "test_uk_*.py",
        "test_ops_uk.py",
        "test_regex_batch6_perf.py",
    ),
    "eu": (
        "test_eu_*.py",
    ),
    "starter": (
        # Generic scaffold/starter frontend tests. The U.S. federal jurisdiction
        # frontend tests (``test_jurisdiction_starter_us_federal_*.py``) are owned
        # by the dedicated ``us_federal`` shard below, so this glob is scoped to
        # the ``p5`` runtime-scaffold family to avoid double-ownership.
        "test_jurisdiction_starter_p5_*.py",
        "test_open_law_frontend.py",
        "test_scaffold_tool.py",
    ),
    "us_federal": (
        "test_jurisdiction_starter_us_federal_*.py",
        "test_us_act_name_registry.py",
        "test_us_classification_tables.py",
        "test_us_release_points.py",
        "test_us_source_anchor.py",
        "test_us_table3_import.py",
        "test_us_table3_resolver.py",
        "test_us_usc_release_import.py",
        "test_us_uslm_parser.py",
        "test_us_write_receipts.py",
        "test_us_apply_seam_boundary.py",
        # EV-05 execution-authorization proof carrier for the US char-span apply
        # lane (``us_federal/apply_profile.py`` mint/read + resolver wiring).
        "test_us_proof_carrier.py",
        # task #105: US same-moment cross-act conflict detection + ordering parity
        # (``us_federal/us_ordering.py`` routed through the shared ``order_ops``).
        "test_us_same_moment.py",
    ),
    "finland_sources": (
        "test_fi_amendment_index.py",
        "test_fi_amendment_selection_residuals.py",
        "test_fi_lane_c_filter_conservation.py",
        "test_fi_source_xml_label_policy_audit.py",
        "test_fi_audit_verified_finlex_yaml.py",
        "test_fi_backfill_finlex_consolidated_versions.py",
        "test_fi_by_name.py",
        "test_fi_build_publication_db.py",
        "test_fi_corpus_archive_guard.py",
        "test_fi_corpus_graph.py",
        "test_fi_corpus_lints.py",
        "test_fi_editorial_filter.py",
        "test_corpus_surface_graph_export.py",
        "test_fi_finlex_*.py",
        "test_fi_name_registry_build.py",
        "test_fi_scan_absent_ajantasa.py",
        "test_fi_scan_annotations.py",
        "test_fi_source_model.py",
        "test_fi_source_dump.py",
        "test_fi_statute_name_aliases.py",
        "test_fi_statute_name_full_registry.py",
        "test_fi_statute_name_registry.py",
        "test_fi_statute_id_ordering.py",
        "test_fi_transparent_store.py",
        "test_fi_vts.py",
    ),
    "finland_parse_payload": (
        "test_fi_attachment_ir.py",
        # FI PDF spine Phase 1 — attachment-PDF spine as a graftable base.
        "test_fi_pdf_spine_base.py",
        # FI PDF spine Phase 2 (Option B) — AKN-XML serialisation of the spine
        # (part_N__chp_N__sec_N eIds) so the section_resolver/oracle path
        # resolves against the PDF-derived base; generalisation to a second
        # §-structured in-force PDF-only statute (2008/721).
        "test_fi_pdf_spine_xml.py",
        # SDOC-13 unified tree merge helper — attachments as APPENDIX
        # siblings of BODY under one HCONTAINER root.
        "test_fi_attachment_merge.py",
        # Task #147 — Regime-B budget-PDF mojibake decoder (font-scoped
        # glyph-offset decode in pdf_layout.py).
        "test_fi_pdf_mojibake_decode.py",
        # SDOC invariants — pinned against the D0 attachment-IR fixture.
        "test_fi_sdoc_invariants.py",
        # D4 footnote scoped collation (doc3 D4).
        "test_fi_footnote_collation.py",
        "test_fi_body_*.py",
        "test_fi_definition_introducer.py",
        "test_fi_definition_projection.py",
        "test_fi_clause_ast_curated.py",
        "test_fi_clause_patterns.py",
        "test_fi_clause_segment.py",
        "test_fi_clause_surface.py",
        "test_fi_condition_exception_parse.py",
        "test_fi_coverage_audit.py",
        "test_fi_definition_parse.py",
        "test_fi_delegation_parse.py",
        "test_fi_delegation_canonical.py",
        "test_fi_johtolause_morph_derived_lexicon.py",
        "test_fi_modal_projection.py",
        "test_fi_num_in_intro_recovery.py",
        "test_fi_modal_parse.py",
        "test_fi_profile_normalize.py",
        "test_fi_se_tools_regex_perf.py",
        "test_fi_sentence_parse.py",
        "test_fi_source_pathology_observations.py",
        "test_fi_xml_boundary_static.py",
        "test_fi_xml_ir.py",
        "test_fi_frontend_observations.py",
        "test_fi_grammar_*.py",
        "test_fi_johtolause_api.py",
        "test_fi_lower_*.py",
        "test_fi_normalize.py",
        "test_fi_parse_clause.py",
        "test_fi_parse_explain.py",
        "test_fi_payload_normalize.py",
        "test_fi_sparse_tail_claims.py",
        "test_fi_source_syntax_graph.py",
        "test_fi_source_syntax_stage.py",
        "test_fi_token_partition_coverage.py",
        "test_fi_union_ownership_census.py",
        "test_payload_surface.py",
        "test_fi_peg_audit.py",
        "test_fi_peg_curated.py",
        "test_fi_peg_rule_registry.py",
        "test_fi_qualified_jolloin_renumber.py",
        "test_fi_totality_predicate.py",
    ),
    "finland_replay_compile": (
        "test_fi_compile.py",
    ),
    "finland_replay_grafter": (
        "test_fi_grafter_fallback.py",
    ),
    "finland_replay_products_core": (
        "test_fi_replay_products.py",
        "test_fi_base_final_provisions_allowance.py",
        "test_fi_seam_raw_text_witness.py",
        # Duplicate pure-kumotaan REPEAL suppression regression (kumotaan_replay.py:406 +
        # PureKumotaanInjectedRepeal witness for §2.10 monotone evidence).
        "test_fi_duplicate_repeal_suppression.py",
        # §39 misparenting regression — operative section railed into attachments wrapper
        # vs re-homed into statuteProvisionsWrapper (replay_products.py:1125).
        "test_fi_sec39_misparenting.py",
    ),
    "finland_replay_products_support": (
        "test_fi_replay_fold_timeline_backfill.py",
        "test_fi_replay_pipeline.py",
        "test_fi_process_temporal_postprocessing.py",
        "test_fi_replay_revision.py",
        "test_fi_session_regressions_2026_04.py",
        "test_fi_provision_state_consumer_contract.py",
    ),
    "finland_replay_rules": (
        "test_fi_corrigendum_*.py",
        "test_corrigendum_fail_loud.py",
        # §1.10 guard-liveness tests for scripts/diff_pdf_xml_corrigenda.py.
        "test_diff_pdf_xml_corrigenda_fail_loud.py",
        "test_fi_guard_liveness.py",
        "test_filter_conservation_ratchet.py",
        "test_scope_source_ratchet.py",
        "test_fi_post_process_repeal_consolidation.py",
        "test_fi_repeal_payload_invariant.py",
        "test_fi_uncovered_dispose.py",
        "test_fi_uncovered_recovery_helpers.py",
        "test_fi_uncovered_target_resolve.py",
        "test_fi_apply_intent_facade.py",
        "test_fi_apply_resolved_op.py",
        "test_fi_apply_authority_stage.py",
        "test_fi_apply_loop_state.py",
        "test_fi_broken_detection.py",
        "test_fi_chapter_bleed_root_section_momentti.py",
        "test_fi_chapter_labelled_subheading_recovery.py",
        "test_fi_chapter_nimike_heading_recovery.py",
        "test_fi_compile_group_scope_recovery.py",
        "test_fi_cross_statute_chapter.py",
        "test_fi_elliptical_resolve.py",
        "test_fi_enclosing_anaphora.py",
        "test_fi_eu_repeal_embedded.py",
        "test_fi_fallback_residue_registry.py",
        "test_fi_process_route_rejection.py",
        "test_fi_invariant_bisect_chapter_seeding.py",
        "test_fi_invariant_bisect_descendant_sibling_loss.py",
        "test_fi_timeline_export_plan.py",
        "test_fi_timeline_hook.py",
        "test_fi_timeline_robust_corpus.py",
        "test_fi_timeline_version_dedupe.py",
        "test_fi_temporary_expiry_tombstone.py",
        "test_fi_scoped_section_resolver.py",
        "test_fi_resolve.py",
        "test_fi_resolve_defined_terms.py",
        "test_fi_future_repeal.py",
        "test_fi_editorial_adjudication.py",
        "test_fi_replay_base_evidence.py",
        "test_fi_replay_capture.py",
        "test_fi_replay_findings.py",
        "test_fi_replay_state.py",
        "test_fi_resolution_conformance.py",
        "test_fi_item_number_display.py",
        "test_fi_tail_prose_absorb.py",
        "test_fi_penal_sentencing_wrapup_fold.py",
        "test_fi_unnumbered_peer_reparent.py",
        "test_fi_cross_refs.py",
        "test_fi_xstatute_coordination.py",
        "test_fi_delegation.py",
        "test_fi_ontology.py",
        "test_fi_profile.py",
        "test_fi_rulebook.py",
        "test_fi_rulebook_cli.py",
        "test_fi_rulebook_export.py",
        "test_fi_rulebook_registries.py",
        "test_fi_actor_mention.py",
        "test_fi_he_acquisition.py",
        "test_fi_he_projection.py",
        "test_fi_proposal_bundle.py",
        "test_fi_trigger_coverage_certificate.py",
        "test_fi_pool_mention.py",
        "test_fi_query_cli_surface.py",
        "test_fi_annotation_reliability.py",
        "test_fi_freetext_addresses.py",
        "test_fi_reference_projection.py",
        "test_fi_reference_mention.py",
        "test_fi_cited_version_recognizer.py",
        "test_fi_telos_section_flag.py",
        "test_fi_wrapup_preservation.py",
        "test_fi_census_accounting.py",
        "test_fi_census_adjudication.py",
        "test_fi_family_census.py",
        "test_fi_fallback_coverage_census.py",
        "test_fi_legacy_strippers_loadbearing.py",
        "test_fi_normalize_fallback_heuristic_census.py",
        "test_fi_ref_legacy_regex_residue_census.py",
        # writer E + F + G additions
        "test_fi_proposal_history.py",
        "test_fi_proposals_competing.py",
        "test_fi_sections_text.py",
        "test_fi_export_interlinks.py",
        "test_fi_projection_stage.py",
        "test_fi_interlink_placement_v0.py",
        "test_fi_inline_citation.py",
        "test_fi_preparatory_reference.py",
        # manual claims slices 1+2+3
        "test_fi_cmd_claim.py",
        "test_fi_inline_statute_resolution.py",
        "test_fi_manual_claims_primitive.py",
        "test_fi_manual_claims_storage.py",
        "test_fi_manual_claims_slice3.py",
        "test_export_fi_refs_authority.py",
        "test_export_projection_coverage_leak.py",
        # v3 graph-native claims (Step 2 + Step 3 CLI migration)
        "test_fi_strict_profile_v3.py",
        "test_fi_manual_claims_native.py",
        "test_fi_cmd_migrate_manual_claims.py",
        "test_fi_cmd_claim_v3.py",
        # proof surfaces + section resolver
        "test_fi_proof_surfaces.py",
        "test_fi_section_resolver.py",
        # frontier claims + claim kinds
        "test_fi_xml_manual_frontier_claims.py",
        "test_fi_inline_statute_resolution_canonicalization.py",
        # proposal/validation backends + lifecycle
        "test_fi_proposal_backend.py",
        "test_fi_propose_claims_cli.py",
        # frontier scan + corpus existence + provision-ref locator
        "test_fi_corpus_existence_check.py",
        "test_fi_frontier_scan_inline_citations.py",
        "test_fi_provision_ref_locator.py",
        "test_fi_qwen_local_backend.py",
        "test_fi_retraction_lifecycle.py",
        "test_fi_source_provider.py",
        "test_fi_validate_claims_cli.py",
        "test_fi_payload_realization_audit.py",
    ),
    "evidence_claims": (
        "test_evidence.py",
        "test_best_section_similarity.py",
        # v3 evidence policy + kernel
        "test_evidence_policy.py",
        "test_evidence_kernel.py",
        # build-consumption recorder + retraction-taint projection
        "test_fi_build_consumption.py",
    ),
    "evidence_core": (
        "test_adjudication_evidence.py",
        "test_certified_transition.py",
        "test_fi_capture.py",
        "test_chain_completeness.py",
        "test_proof_algebra.py",
        "test_section_evidence_context.py",
        "test_section_invariant_evidence.py",
        "test_fi_section_strict_lineage.py",
        "test_statute_proof_algebra.py",
        "test_strict_payload_confidence.py",
        "test_fi_version_drift.py",
        "test_ev_residual_ledger_and_self_evidencing.py",
        # Declared non-guarantees as root-committed evidence-plane objects.
        "test_assumption_register.py",
        "test_se_assumptions.py",
        # SE agreement-residual projector (typed evidence-plane dossier).
        "test_se_agreement_residuals.py",
        # SE coverage-scan universe — committed content-addressed corpus root.
        "test_se_coverage_universe.py",
        # SE archive-write monotonicity ledger (KNOW-01 + §1.6).
        "test_se_overwrite_event_ledger.py",
    ),
    "evidence_reports": (
        "test_fi_explain_facade.py",
        "test_fi_strict_report.py",
    ),
    "properties_timeline": (
        "test_timeline_properties.py",
    ),
    "properties": (
        "test_fi_apply_properties.py",
        "test_crosshair_kernels.py",
        "test_fi_decomposition.py",
        "test_exhaustive_enumeration.py",
        "test_kernel_properties.py",
        "test_fi_merge_properties.py",
        "test_mutmut_kills.py",
        "test_fi_payload_normalize_properties.py",
        "test_fi_replay_stateful_properties.py",
        "test_stateful_properties.py",
        "test_fi_tree_ops_properties.py",
        "test_z3_proofs.py",
        "test_tla_invariant_mirror.py",
    ),
    "core_discipline_gates": (
        "test_archive_safety.py",
        "test_replay_conservation.py",
        "test_downgrade_witness.py",
        "test_downgrade_witness_lint.py",
        "test_dual_registration_completeness.py",
        "test_authority_boundary_ratchet.py",
        "test_source_witness_liveness_ratchet.py",
        # iter2 W6 Fix 5 (guard-liveness review F3): meta-test asserting
        # ``tests/conftest.py``'s xfail-drift prefixes still XFAIL (catches
        # silent XPASS drift under ``strict=False``). Marked ``@slow`` —
        # re-runs all 22 drift prefixes via subprocess when opted in.
        "test_xfail_drift_markers.py",
        # Audit-invariant registry program (lanes L2a/L2b/L3/L5): control-flow,
        # determinism-spine, typed-carrier, identity-leak, and replay-determinism gates.
        "test_fail_loud_ratchet.py",
        "test_confidence_control_ratchet.py",
        "test_determinism_spine_ratchet.py",
        "test_typed_carrier_boundary_ratchet.py",
        "test_waist_contract_ratchet.py",
        # Static-ratchet completers wave (CONTRACT-01/02, VOCAB-02, FW-07/FW-08):
        # waist field/type contract, namespaced-status/confidence, classifier-WRAP
        # mandate, frozen-residue structural sensors.
        "test_waist_field_contract_ratchet.py",
        "test_vocab_namespaced_status_ratchet.py",
        "test_classifier_wrap_ratchet.py",
        "test_frozen_residue_sensors_ratchet.py",
        "test_identity_intrinsic_audit.py",
        "test_synthetic_label_leak.py",
        "test_replay_determinism.py",
        # F REPLAY.NONDETERMINISM guard-liveness fire-drill (task #104): drives the
        # replay-determinism audit into its firing state over the production
        # replay spine; sits next to its clean-corpus sibling above.
        "test_replay_determinism_firedrill.py",
        # Audit-invariant registry program (PROJECTION plane): row PROJ-01
        # (projection re-derivability from committed matter) + row PROJ-02
        # (tree-wide no-author-set-replay_authorized-at-projection sweep).
        "test_projection_rederivability.py",
        # D9 PROJECTION.REDERIVATION_DRIFT guard-liveness fire-drill (task #104):
        # drives the projection-rederivation audit into its firing state over the
        # production seam-projection producer (real committed bundle rows).
        "test_projection_rederivation_firedrill.py",
        "test_projection_author_set_authority.py",
        "test_explicit_address_level.py",
        "test_guard_liveness_totality.py",
        # Claim-surface backbone (Pro invariant-mining §13 step 1+3 + §4): the
        # generated coverage gate — every declared public claim has a live
        # accounting path; zero invariants in the forbidden implicit_convention
        # bucket.
        "test_claim_surface_coverage.py",
        # Claim-surface backbone, continued: the finite-axis invariant GENERATOR
        # (Pro §13 step 1 — invariants generated from claim shape; undischarged
        # obligation = typed gap); the MUST-trace ledger + drift detector (step 5);
        # and the per-handle non-guarantee binding gate (every declared
        # allowed_non_guarantee handle resolves to a registered assumption).
        "test_invariant_generator.py",
        "test_must_trace.py",
        "test_claim_assumption_binding.py",
        # Audit-invariant registry program (CROSS-JURISDICTION, §2.3): row
        # XJUR-02 — no hidden replay kernel in a frontend (static boundary audit;
        # monotone ratchet over a discovered-debt baseline).
        "test_hidden_replay_kernel_ratchet.py",
        # Audit-invariant registry program (PROMOTION-CHAIN integrity, §0):
        # rows PROMOTE-02 (authorization scope-match), CHAIN-01/02 (completeness +
        # monotonicity), PROMOTE-01 (retraction down-chain propagation).
        "test_promotion_chain_integrity.py",
        "test_frozen_slots_discipline.py",
        "test_strict_profile_registry.py",
    ),
    "core_ir_contracts": (
        "test_quirks_disposition_enum.py",
        "test_scope_confidence_protocol.py",
        # iter3 W1 Fix 4: mirror of ``test_scope_confidence_protocol.py`` for the
        # typed ``ClaimAssertion`` / ``ExecutionAuthorizationResult`` /
        # ``CompileAdjudicationProtocol`` carrier Protocols at the core boundary
        # (AGENTS.md §1.9 typed carriers over dynamic shape + §1.10 fail-loud).
        "test_typed_carrier_protocols.py",
        # D1 node-kind registry: governed IRNodeKind specs + validator.
        "test_core_node_kind_registry.py",
        "test_fi_address_parse.py",
        "test_irnodekind_stringly_typed_gate.py",
        "test_fi_admissible_binding.py",
        "test_branch_authority.py",
        "test_core_identity_ledger.py",
        "test_fi_branch_graph_parser.py",
        "test_fi_bitemporal.py",
        "test_branch_projection.py",
        "test_canonical_intent_kinds.py",
        "test_fi_canonical_op_stage.py",
        "test_fi_canonical_op_stage_carrier.py",
        "test_fi_op_coverage_totality.py",
        "test_fi_clause_ast.py",
        "test_fi_coordination_parser.py",
        "test_core_locator.py",
        "test_core_unit_registry_contracts.py",
        "test_fi_effect_lowering.py",
        "test_fi_dates_recognizer.py",
        "test_filter_result.py",
        "test_fi_intent_compat.py",
        "test_fi_optype_strenum.py",
        "test_ir_*.py",
        "test_fi_meta_parse.py",
        "test_fi_metadata_temporal.py",
        "test_fi_parser_facade.py",
        "test_fi_provision_index.py",
        "test_apply_decline_ratchet.py",
        "test_deprecated_callsite_ratchet.py",
        "test_target_write_ratchet.py",
        "test_module_role_consistency.py",
        "test_naming_hygiene_ratchet.py",
        "test_regex_perf_gate.py",
        "test_regex_prefilter.py",
        "test_regex_ratchet.py",
        "test_roman.py",
        "test_stage_result.py",
        "test_fi_scope.py",
        "test_fi_scope_regex_perf.py",
        "test_fi_section_keys.py",
        "test_selector.py",
        "test_fi_shared_contracts.py",
        "test_source_lane.py",
        "test_source_path_index.py",
        "test_source_version_window.py",
        "test_span_anchor.py",
        "test_statute_facets.py",
        "test_target_resolution.py",
        "test_fi_target_scope.py",
        "test_target_selector_codec.py",
        "test_target_selector_consistency.py",
        "test_target_selector_facades.py",
        "test_w6_target_column_census.py",
        "test_unicode_folds.py",
        "test_fi_unit_registry.py",
    ),
    "core_tree_apply": (
        "test_fi_annotations_views.py",
        "test_fi_apply_ir_ops.py",
        "test_fi_apply_unscoped_section_insert_nesting.py",
        "test_observed_write_audit.py",
        "test_fi_apply.py",
        "test_fi_recovery_kind_enum.py",
        # Totality predicate + per-site guard-liveness for
        # lawvm.core.named_swallow (§1.10 no-silent-default +
        # §2.6 rule-of-three crystallisation).
        "test_named_swallow_totality.py",
        "test_fi_apply_write_receipt_seam.py",
        "test_fi_apply_replay_authority.py",
        # Wave N3a PR1 (BOUND_TARGET_PATH_NORMALIZATION_DESIGN): wrapper-strip +
        # kind-alias canonicalization for the op-level WriteReceipt.bound_target_path.
        "test_fi_receipt_path_norm.py",
        "test_fi_receipt_prefix_eq.py",
        "test_fi_item_relabel_replay.py",
        "test_fi_chapter_seed.py",
        "test_fi_constraints.py",
        "test_destructive_repair_ledger.py",
        "test_invariant_detectors.py",
        "test_fi_law_level_text_patch.py",
        "test_legal_operation_text_patch.py",
        "test_fi_merge.py",
        "test_fi_migration_ledger.py",
        "test_mutation_boundary.py",
        "test_core_mutation_boundary_audit.py",
        "test_mutation_events.py",
        "test_mutation_gaps.py",
        "test_normalize_structure.py",
        "test_fi_occupancy.py",
        "test_opaque_marker_boundary.py",
        "test_fi_text_amend.py",
        "test_tree_ops_ambiguity.py",
        "test_tree_ops_stage.py",
        "test_tree_ops_roman_labels.py",
        "test_core_tree_invariant_scan_families.py",
        # iter2 W5 H3: shared §1.7 cross-act same-moment conflict detector
        # (extracted out of EE/UK into ``lawvm.core.cross_act_same_moment``).
        # Synthetic op-driven coverage mirroring the §2.9 test pyramid; placed
        # alongside ``test_named_swallow_totality.py`` / ``test_fi_recovery_kind_enum.py``
        # in ``core_tree_apply`` per the iter2 W6 shard-registration hint.
        "test_core_cross_act_same_moment.py",
        # Wave 0 unified ordering kernel (lawvm.core.op_ordering.order_ops),
        # which WRAPS the shared cross-act same-moment detector. Synthetic
        # algebra coverage (temporal+sequence order, delegated same-moment
        # detection, validated-claim resolution, lex-posterior tiebreak).
        "test_core_op_ordering.py",
        # B-enforcement increments 1-4: the universal apply-seam gate battery
        # (synthetic-profile coverage). EV-05 authorization OBSERVE (inc 1), LS-01
        # boundary-unification observer (inc 2), LS-03 occupancy OBSERVE (inc 3),
        # AM-01 provenance-acceptance OBSERVE (inc 4), and EE's first real LS-03
        # occupancy gate flipped to BLOCK after a clean-corpus measurement (inc 4).
        "test_apply_seam_authorization_gate.py",
        "test_apply_seam_boundary_unification.py",
        "test_apply_seam_occupancy_gate.py",
        "test_apply_seam_provenance_gate.py",
        "test_ee_occupancy_enforcement.py",
        # EV-05 PROOF CARRIER on core/ir.LegalOperation + the generic seam
        # resolver (the framework change CROSS_JURISDICTION_PARITY names), and EE
        # as the first MINTING frontend: a real ExecutionAuthorization minted from
        # each op's amending-act identity (EV-05 quiet for known authority, fires
        # on the unauthorized residue) + a real Parsed-vs-Recovered AM-01 verdict
        # from EE's scope_confidence rung. Both observe-only (observations lane).
        "test_op_execution_authorization_carrier.py",
        "test_ee_proof_carrier.py",
        # LS-01 boundary block-mode promotion gate (measure-then-flip): the
        # per-frontend boundary-escape measurement over the production apply lanes
        # + the safety-first kept-observe decision (no frontend flipped; EE/UK
        # have real latent escapes, NO/SE op-set-clean but corpus-unverified here).
        "test_apply_seam_boundary_blockmode.py",
        # B-enforcement increment 5: the receipt-totality CONTRACT (observe). The
        # receipt analogue of coverage-totality over the accumulated per-op
        # receipt ledger (landed-writes <-> receipts is a bijection), surfaced as
        # a non-blocking APPLY.RECEIPT_TOTALITY_OBSERVED on the separate
        # observations lane (its STAGED strict twin APPLY.RECEIPT_TOTALITY_REQUIRED
        # registered but unrouted). Synthetic-profile mechanism coverage.
        "test_receipt_totality.py",
        # XP-06 cross-jurisdiction invariant-parity audit: the read-mostly
        # analysis that builds the invariant x frontend parity matrix from the
        # real ApplyProfile registrations (AST-scanned) + the FI reference upper
        # bound, and emits typed INVARIANT_COVERAGE_DIVERGENCE rows (e.g. EE's
        # LS-03 occupancy=block vs the other tree frontends' no-op default).
        "test_cross_jurisdiction_parity.py",
    ),
    "core_compile_projection": (
        "test_compile_metadata_default_fail_loud.py",
        "test_fi_effect_lifecycle_projection.py",
        "test_fi_compile_facade.py",
        "test_compile_records.py",
        "test_compile_record_carrier.py",
        "test_compile_result.py",
        "test_compile_views.py",
        "test_emitters_compile_metadata_required.py",
        "test_export_emitters_compile_metadata_round2.py",
        "test_fi_graph_build_contract.py",
        "test_pipeline_capture.py",
        "test_projection_completeness.py",
        "test_proof_surface_graph.py",
        "test_fi_provenance.py",
        "test_hyperlinks.py",
        "test_verify_chain.py",
        # Step 1: provenance graph substrate
        "test_provenance_graph.py",
        "test_fi_provenance_graph_facade.py",
        "test_provenance_graph_storage.py",
        "test_unique_byte_run_texts.py",
        # Step 5: compile metadata + reproducibility fingerprints
        "test_compile_metadata.py",
        "test_compile_metadata_verify.py",
        "test_compile_facade_v3.py",
        "test_export_emitters_compile_metadata.py",
        "test_build_index_db_compile_metadata.py",
        "test_payload_realization.py",
    ),
    "core_materialization_invariants": (
        "test_fi_materialization_invariants.py",
        "test_fi_materialization_totality.py",
        "test_crossjur_materialization_universe.py",
    ),
    "core_replay_timeline": (
        "test_part_snapshot_section_retention.py",
        "test_replay_lints.py",
        "test_replay_metamorphic.py",
        "test_replay_small_model.py",
        "test_fi_timeline.py",
        "test_commencement_totality_audit.py",
        "test_citation_graph_totality_audit.py",
        "test_compare_eid_parity_audit.py",
        "test_oracle_divergence_kernel.py",
        # NB: test_uk_oracle_divergence_parallel_run.py is intentionally NOT pinned
        # here — it matches the "uk" shard's test_uk_*.py glob, and the flat
        # fnmatch matcher has no per-shard exclusion, so a second explicit pin
        # would double-own it (fails validate()). It runs in the "uk" shard.
        "test_replay_determinism_audit.py",
        "test_provenance_totality_audit.py",
        "test_coverage_totality_audit.py",
        "test_overlay_default_replay_authorized_false.py",
        "test_audit_observation_promoted_to_authority.py",
        "test_audit_attestation_policy_gap.py",
        "test_know_invariants.py",
        "test_timeline_invariants.py",
        "test_timeline_lineage_contracts.py",
        "test_timeline_materialization_stage.py",
        "test_timeline_promotion.py",
        "test_timeline_results_contracts.py",
        "test_timeline_selection_contracts.py",
    ),
    "core_surface_semantic": (
        "test_core_firewall_no_fi_definition_phrases.py",
        # iter2 W5 H1: AST-scan no-leak test for the §2.3 core/frontend firewall
        # fix that moved ``lawvm.core.pool_mention`` (Finnish fiscal doctrine)
        # into ``lawvm.finland.pool_mention_primitive``. Mirrors the precedent
        # of ``test_core_firewall_no_fi_definition_phrases.py`` above.
        "test_core_firewall_no_fi_fiscal_doctrine.py",
        # iter3 W1 Fix 4: AST-scan §2.3 firewall test — no ``lawvm.finland.<module>``
        # implementation-level paths anywhere in ``lawvm.core.*``. Mirrors the
        # ``test_core_firewall_no_fi_fiscal_doctrine.py`` precedent (W7 M13 arch
        # review MEDIUM-2 lift of 13 ``lawvm.finland.X.Y`` paths out of core).
        "test_core_firewall_no_finland_module_paths.py",
        "test_comparison_normalization.py",
        "test_composite_interaction_reference_model.py",
        "test_fi_abstraction_modules.py",
        "test_fi_actor_modal.py",
        "test_fi_anaphora.py",
        "test_fi_annotation_independence.py",
        "test_fi_cross_lens_passes.py",
        "test_fi_defined_terms.py",
        "test_fi_definition_graph.py",
        "test_fi_definition_lint_precision.py",
        "test_fi_delegation_edge_adapter.py",
        "test_fi_delegation_instrument.py",
        "test_fi_deontic_core_lens.py",
        "test_fi_deontic_frame_edges.py",
        "test_fi_eu_directive.py",
        "test_fi_eu_nickname_binding.py",
        "test_fi_eu_reference_year_first_suffix.py",
        "test_fi_exception_condition.py",
        "test_fi_export_parity.py",
        "test_fi_frame_affordances.py",
        "test_fi_frame_id_collisions.py",
        "test_fi_frame_relations.py",
        "test_fi_derivation_edges.py",
        "test_fi_graph_build.py",
        "test_fi_graph_parity.py",
        "test_fi_interlink_targets.py",
        "test_fi_interlinks.py",
        "test_fi_internal_ref_span_boundary.py",
        "test_fi_internal_refs.py",
        "test_fi_lemma_gate.py",
        "test_fi_lemma_index.py",
        "test_fi_lens_anaphora.py",
        "test_fi_lens_defined_terms.py",
        "test_fi_lens_definitions.py",
        "test_fi_lens_frames.py",
        "test_fi_lens_references.py",
        "test_fi_lens_temporal_actor.py",
        "test_fi_lens_token_migration.py",
        "test_fi_mine_eu_nicknames.py",
        "test_fi_morph_analyze_open.py",
        "test_fi_case_frame.py",
        "test_fi_case_frame_group.py",
        "test_fi_morph_overlay.py",
        "test_fi_morphology.py",
        "test_fi_norm_composition.py",
        "test_fi_norm_subject_and_procedure_edges.py",
        "test_fi_op_provenance.py",
        "test_fi_overlay_projection.py",
        "test_fi_procedure.py",
        "test_fi_ref_lints.py",
        "test_fi_reference_span_alignment.py",
        "test_fi_references_delegation.py",
        "test_fi_registry_nicknames.py",
        "test_fi_sanction.py",
        "test_fi_sanction_reference.py",
        "test_fi_segmentation_graph.py",
        "test_fi_source_identity_stage.py",
        "test_fi_source_unit_stage.py",
        "test_fi_term_use.py",
        "test_fi_treaty_article.py",
        "test_fi_treaty_vague.py",
        "test_fi_core_graph.py",
        "test_diagnostic_records.py",
        "test_elaboration_context_contracts.py",
        "test_fi_parse_witness.py",
        "test_phase_result_*.py",
        "test_semantic_*.py",
        "test_fi_semantic_*.py",
        "test_solver_slot_assignment.py",
        "test_fi_sched_window_totality.py",
        "test_fi_scope_lattice_totality.py",
        "test_fi_surface_*.py",
        "test_table_*.py",
        "test_fi_table_*.py",
        "test_temporal*.py",
        "test_fi_temporal*.py",
        "test_fi_token_actor_modal.py",
        "test_fi_token_delegation.py",
        "test_fi_token_model.py",
        "test_fi_token_procedure.py",
        "test_fi_token_sanction.py",
        "test_fi_tokenize_char_offsets.py",
        "test_fi_tokentape.py",
        "test_interlinks.py",
        "test_legal_surface_graph.py",
        "test_reference_sets.py",
        "test_surface_lints.py",
    ),
    "tools_cli_debug_hotspot": (
        "test_fi_cli_debug_tools.py",
    ),
    "tools_cli_oracle": (
        "test_fi_oracle_check.py",
        "test_oracle_text.py",
    ),
    "tools_cli_debug": (
        "test_check_consistency.py",
        "test_lawvm_profile_cli.py",
        "test_proof_gate_summary.py",
        "test_fi_self_consistency.py",
        "test_spec_authority.py",
        "test_spec_ledger.py",
        "test_spec_ledger_discovery.py",
        "test_spec_ledger_uk.py",
        "test_spec_ledger_uk_catalog.py",
        "test_spec_ledger_uk_catalog_supplement.py",
        "test_spec_ledger_report.py",
        "test_spec_ledger_ee_catalog.py",
        "test_spec_ledger_se_catalog.py",
        "test_spec_ledger_ee.py",
        "test_spec_ledger_no.py",
        "test_spec_ledger_se.py",
        "test_spec_ledger_eu.py",
        "test_spec_ledger_no_catalog.py",
        "test_spec_ledger_nz_catalog.py",
        # iter4 W1 C1: new EU spec_ledger catalog (mirror of SE/EE/the-UK precedent
        # shape; 5-test anti-drift guard — coverage + dead + non-empty + excluded
        # + dir-present). Previously EU had NO AST-discovery catalog at all.
        "test_spec_ledger_eu_catalog.py",
        "test_delegate_tool.py",
        "test_fi_spec_ledger_catalog.py",
        # #181 spec-ledger enrichment: S/P rule_role + falsifier + ≺/≈ glue guards.
        "test_fi_spec_ledger_meta.py",
        "test_spec_ledger_meta.py",
        "test_diagnose_phase.py",
        "test_diff.py",
        "test_dump.py",
        "test_dump_json_hashes.py",
        "test_dump_tombstone_render.py",
        # `lawvm show` — pretty human-readable statute tree; counterpart to dump.
        "test_tools_show.py",
        "test_fi_freshness_tool.py",
        "test_fi_provision_state.py",
        "test_fi_parse_view.py",
        "test_fi_refs_view.py",
        "test_bill_analysis.py",
        "test_bill_counterfactual_effects.py",
        "test_dangling_references.py",
        "test_dangling_temporal_cause.py",
        "test_cross_reference_integrity_report.py",
        # test_eu_reference_report.py is owned by the `eu` shard's test_eu_* glob.
        "test_reference_integrity_demo_report.py",
        "test_provision_state_window_unmaterialized.py",
        "test_read_provision.py",
        "test_reconcile.py",
        "test_explain_snippet.py",
        "test_fi_reconcile_sweep.py",
        "test_replay_cli_contract.py",
        "test_fi_replay_all_cli.py",
        "test_verify_facade_execution.py",
        "test_verify_observations.py",
    ),
    "tools_runtime_io": (
        "test_corpus_store_path_validation.py",
        "test_corpus_xml_parser_ratchet.py",
        "test_xml_parse_hardened.py",
        "test_fi_acquisition.py",
        "test_fi_source_anchor.py",
        "test_fi_process_acquisition_digest_consumer.py",
        "test_fi_consolidated_artifacts.py",
        "test_branch_demo.py",
        "test_certificate_bundle.py",
        "test_certificate_stage_roots.py",
        "test_fi_export_sql.py",
        "test_fi_export_transition_graph.py",
        "test_graph_export.py",
        "test_markdown_git_export.py",
        "test_export_markdown_git_safe_path.py",
        # iter2 W6 Fix 2 (MEDIUM-3): curl argv ``--`` separator for NO + EE
        # fetch paths (argument-injection guard).
        "test_curl_separator.py",
        "test_import_zip.py",
        "test_parallel_corpus_determinism.py",
        "test_projection_freshness.py",
        "test_projection_rederivation_audit.py",
        "test_tier_2_storage.py",
        "test_worker_pool.py",
    ),
    "tools_audit_restructure": (
        "test_fi_restructure_plan.py",
    ),
    "tools_audit_blame": (
        "test_blame.py",
    ),
    "tools_audit_release": (
        "test_fi_audit.py",
        "test_fi_annotation_witness.py",
        "test_fi_audit_channels.py",
        "test_fi_audit_scripts.py",
        "test_ci_shards.py",
        "test_fi_failures.py",
        "test_finding_registry.py",
        "test_fi_helpers.py",
        "test_publication_guarantees.py",
        "test_release_docs.py",
        "test_fi_source_normalize.py",
        "test_fi_step_attribution.py",
        "test_structural_*.py",
        "test_invariant_harvest.py",
        "test_fi_recall_audit.py",
        "test_fi_uncovered_recovery_audit.py",
    ),
    "tools_bench_inventory": (
        "test_bench_report.py",
        "test_bench_triage.py",
        "test_bench.py",
        "test_bench_contract.py",
        "test_bench_contract_adapters.py",
        "test_fi_bench_contract_adapter.py",
        "test_fi_bench_comparable.py",
        "test_fi_aux_pit_probe.py",
        "test_fi_aux_pit_bench.py",
        "test_fi_oracle_amb_match.py",
        "test_fi_segmentation_neutralizer.py",
        "test_bench_curate.py",
        "test_parse_bench.py",
        "test_parse_characterize.py",
        "test_fi_corpus.py",
        "test_divergence_heuristics.py",
        "test_frontier.py",
        "test_fi_gold_tool.py",
        "test_fi_metadata.py",
        "test_parser_smell_inventory.py",
        "test_refs_bench.py",
        "test_replay_adjudication_inventory.py",
        "test_replay_debt_inventory.py",
        "test_report_query.py",
        "test_residual_ledger.py",
    ),
}

SHARD_GROUPS: dict[str, tuple[str, ...]] = {
    "frontends": ("estonia", "eu", "finland", "new_zealand", "norway", "starter", "sweden", "uk"),
    "modules": ("core", "evidence", "properties", "properties_timeline", "tools"),
    "evidence": ("evidence_claims", "evidence_core", "evidence_reports"),
    "core": (
        "core_discipline_gates",
        "core_ir_contracts",
        "core_tree_apply",
        "core_compile_projection",
        "core_materialization_invariants",
        "core_replay_timeline",
        "core_surface_semantic",
    ),
    "tools": (
        "tools_cli_debug_hotspot",
        "tools_cli_oracle",
        "tools_cli_debug",
        "tools_runtime_io",
        "tools_audit_restructure",
        "tools_audit_blame",
        "tools_audit_release",
        "tools_bench_inventory",
    ),
    "estonia": ("estonia_sources", "estonia_replay_semantics", "estonia_replay_logic"),
    "finland": (
        "finland_sources",
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
    ),
    "new_zealand": ("new_zealand_sources", "new_zealand_effects", "new_zealand_reports"),
    "sweden": ("sweden_fetch", "sweden_misc"),
}

SOURCE_SHARD_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data/finland/corrigendum_manual.yaml",
        ("finland_replay_products_core", "finland_replay_rules"),
    ),
    (
        "data/finland/source_defect_fixes_fi.yaml",
        ("finland_replay_products_core", "finland_replay_rules"),
    ),
    ("src/lawvm/contracts.py", ("core",)),
    ("src/lawvm/graph_build.py", ("core", "tools")),
    ("src/lawvm/semantic/", ("core", "finland", "tools")),
    ("src/lawvm/xml_ingest.py", ("core", "finland", "tools")),
    ("src/lawvm/estonia/", ("estonia",)),
    ("src/lawvm/eu/", ("eu",)),
    ("src/lawvm/finland/", ("finland",)),
    ("src/lawvm/new_zealand/", ("new_zealand",)),
    ("src/lawvm/norway/", ("norway",)),
    ("src/lawvm/open_law/", ("starter",)),
    ("src/lawvm/sweden/", ("sweden",)),
    ("src/lawvm/uk_legislation/", ("uk",)),
    ("src/lawvm/us_federal/", ("us_federal",)),
    ("src/lawvm/tools/ee_", ("estonia", "tools")),
    ("src/lawvm/tools/eu_", ("eu", "tools")),
    ("src/lawvm/tools/finland_", ("finland", "tools")),
    ("src/lawvm/tools/sync_finlex_", ("finland", "tools")),
    ("src/lawvm/tools/no_", ("norway", "tools")),
    ("src/lawvm/tools/sweden.py", ("sweden", "tools")),
    ("src/lawvm/tools/uk_", ("uk", "tools")),
    ("src/lawvm/tools/_evidence_helpers.py", ("evidence", "tools")),
    ("src/lawvm/tools/bisect_support.py", ("evidence", "tools")),
    ("src/lawvm/tools/evidence", ("evidence", "tools")),
    ("src/lawvm/tools/strict_report.py", ("evidence", "tools")),
    ("src/lawvm/tools/", ("tools",)),
    ("notes/UK_", ("uk", "tools_cli_debug")),
    ("src/lawvm/core/", ("all",)),
    ("src/lawvm/jurisdiction_starter/", ("starter",)),
)

TOOLING_SHARD_PREFIXES = (
    "scripts/",
)
GLOBAL_CHANGE_PATHS = frozenset({"pyproject.toml", "uv.lock"})

ALL_SHARDS = ("all",)


def _all_test_files() -> list[str]:
    return sorted(path.name for path in TEST_DIR.glob("test_*.py"))


def _matches(patterns: tuple[str, ...], filename: str) -> bool:
    return any(fnmatch.fnmatchcase(filename, pattern) for pattern in patterns)


def explicit_matches(filename: str) -> list[str]:
    return [
        shard
        for shard, patterns in SHARD_PATTERNS.items()
        if _matches(patterns, filename)
    ]


def shard_assignments() -> dict[str, list[str]]:
    assignments = {shard: [] for shard in SHARD_PATTERNS}
    assignments["misc"] = []
    for filename in _all_test_files():
        if filename in EXCLUDED_TESTS:
            continue
        matches = explicit_matches(filename)
        if len(matches) == 1:
            assignments[matches[0]].append(filename)
        elif len(matches) == 0:
            assignments["misc"].append(filename)
        else:
            # validate() reports this as an error; keep deterministic assignment
            # for list/debug output.
            assignments[matches[0]].append(filename)
    return {key: sorted(value) for key, value in assignments.items()}


def expand_shard_names(shards: list[str]) -> list[str]:
    """Expand named shard groups while preserving order and de-duplicating."""

    expanded: list[str] = []

    def expand_one(shard: str, ancestry: tuple[str, ...]) -> None:
        if shard == "all":
            expanded.clear()
            expanded.append("all")
            return
        members = SHARD_GROUPS.get(shard)
        if members is None:
            if shard not in expanded:
                expanded.append(shard)
            return
        if shard in ancestry:
            raise ValueError(f"Shard group cycle: {' -> '.join((*ancestry, shard))}")
        for member in members:
            if expanded == ["all"]:
                return
            expand_one(member, (*ancestry, shard))

    for shard in shards:
        expand_one(shard, ())
        if expanded == ["all"]:
            return expanded
    return expanded


def validate() -> int:
    files = set(_all_test_files())
    assigned: dict[str, list[str]] = {}
    duplicate_errors: list[str] = []
    dead_patterns: list[str] = []
    for shard, patterns in SHARD_PATTERNS.items():
        for pattern in patterns:
            if not any(fnmatch.fnmatchcase(filename, pattern) for filename in files):
                dead_patterns.append(f"{shard}: {pattern}")
    for filename in sorted(files - set(EXCLUDED_TESTS)):
        matches = explicit_matches(filename)
        if len(matches) > 1:
            duplicate_errors.append(f"{filename}: {', '.join(matches)}")
        assigned[filename] = matches or ["misc"]

    missing_exclusions = sorted(set(EXCLUDED_TESTS) - files)
    unknown_excluded = sorted(set(EXCLUDED_TESTS) & set(assigned))
    assignments = shard_assignments()
    unassigned_errors = assignments["misc"]
    if missing_exclusions:
        print("Excluded tests do not exist:", ", ".join(missing_exclusions), file=sys.stderr)
    if unknown_excluded:
        print("Excluded tests were also assigned:", ", ".join(unknown_excluded), file=sys.stderr)
    if unassigned_errors:
        print("Tests not assigned to an explicit shard:", file=sys.stderr)
        for filename in unassigned_errors:
            print(f"  {filename}", file=sys.stderr)
    if duplicate_errors:
        print("Tests matched multiple explicit shards:", file=sys.stderr)
        for item in duplicate_errors:
            print(f"  {item}", file=sys.stderr)
    if dead_patterns:
        print("Shard patterns matched no files:", file=sys.stderr)
        for item in dead_patterns:
            print(f"  {item}", file=sys.stderr)
    for shard in sorted(assignments):
        print(f"{shard}: {len(assignments[shard])}")
    for filename, reason in sorted(EXCLUDED_TESTS.items()):
        print(f"excluded: {filename} ({reason})")
    return 1 if missing_exclusions or unknown_excluded or unassigned_errors or duplicate_errors or dead_patterns else 0


def shard_timing_record(
    *,
    shard: str,
    file_count: int,
    elapsed_seconds: float,
    exit_code: int,
    run_id: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": "lawvm_pytest_shard_timing",
        "shard": shard,
        "file_count": file_count,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "exit_code": exit_code,
        "status": "passed" if exit_code == 0 else "failed",
    }
    if run_id:
        record["run_id"] = run_id
    return record


def append_shard_timing_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def load_shard_timing_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "line_number": line_number,
                "error": str(exc),
            })
            continue
        if not isinstance(record, dict):
            records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "line_number": line_number,
                "error": "timing record is not a JSON object",
            })
            continue
        records.append(record)
    return records


def shard_timing_balance_report(
    path: Path,
    *,
    imbalance_threshold: float = 2.0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Summarize latest shard timings without changing shard membership."""

    assignments = shard_assignments()
    raw_records = load_shard_timing_records(path)
    invalid_records = [
        record for record in raw_records if record.get("kind") == "lawvm_pytest_shard_timing_invalid"
    ]
    latest_by_shard: dict[str, dict[str, Any]] = {}
    valid_record_count = 0
    for record in raw_records:
        if record.get("kind") != "lawvm_pytest_shard_timing":
            continue
        shard = record.get("shard")
        elapsed = record.get("elapsed_seconds")
        file_count = record.get("file_count")
        record_run_id = record.get("run_id")
        if not isinstance(shard, str) or not shard:
            invalid_records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "error": "timing record missing shard",
                "record": record,
            })
            continue
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            invalid_records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "error": "timing record missing non-negative elapsed_seconds",
                "record": record,
            })
            continue
        if not isinstance(file_count, int) or file_count < 0:
            invalid_records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "error": "timing record missing non-negative file_count",
                "record": record,
            })
            continue
        if record_run_id is not None and not isinstance(record_run_id, str):
            invalid_records.append({
                "kind": "lawvm_pytest_shard_timing_invalid",
                "error": "timing record run_id is not a string",
                "record": record,
            })
            continue
        if run_id is not None and record_run_id != run_id:
            continue
        valid_record_count += 1
        latest_by_shard[shard] = record
    latest_run_ids = sorted({
        run_id
        for record in latest_by_shard.values()
        if isinstance((run_id := record.get("run_id")), str) and run_id
    })
    shard_rows = [
        {
            "shard": shard,
            "elapsed_seconds": round(float(record["elapsed_seconds"]), 3),
            "file_count": int(record["file_count"]),
            "seconds_per_file": round(
                float(record["elapsed_seconds"]) / int(record["file_count"]),
                3,
            )
            if int(record["file_count"]) > 0
            else None,
            "status": str(record.get("status") or ""),
        }
        for shard, record in sorted(latest_by_shard.items())
    ]
    shard_rows.sort(key=lambda row: (-float(row["elapsed_seconds"]), str(row["shard"])))
    elapsed_values = [
        value
        for row in shard_rows
        if isinstance((value := row["elapsed_seconds"]), float)
    ]
    total_elapsed = round(sum(elapsed_values), 3)
    average_elapsed = round(total_elapsed / len(elapsed_values), 3) if elapsed_values else 0.0
    max_elapsed = max(elapsed_values) if elapsed_values else 0.0
    nonzero_values = [value for value in elapsed_values if value > 0]
    min_nonzero_elapsed = min(nonzero_values) if nonzero_values else 0.0
    imbalance_ratio = round(max_elapsed / min_nonzero_elapsed, 3) if min_nonzero_elapsed else 0.0
    overweight_shards = [
        str(row["shard"])
        for row in shard_rows
        if average_elapsed > 0
        and isinstance(row["elapsed_seconds"], float)
        and row["elapsed_seconds"] >= average_elapsed * imbalance_threshold
    ]
    single_file_hotspots = [
        str(row["shard"])
        for row in shard_rows
        if row["shard"] in overweight_shards and cast(int, row["file_count"]) == 1
    ]
    single_file_hotspot_profiles = [
        {
            "shard": shard,
            "file": f"tests/{filenames[0]}" if len(filenames := assignments.get(shard, [])) == 1 else None,
            "command": (
                f"LAWVM_PYTEST_WORKERS=0 ./scripts/test_shard.sh run {shard} -- --durations=25"
            ),
        }
        for shard in single_file_hotspots
    ]
    splittable_hotspots = [
        str(row["shard"])
        for row in shard_rows
        if row["shard"] in overweight_shards and cast(int, row["file_count"]) > 1
    ]
    return {
        "kind": "lawvm_pytest_shard_balance_report",
        "source": str(path),
        "run_id_filter": run_id,
        "record_count": len(raw_records),
        "valid_record_count": valid_record_count,
        "invalid_record_count": len(invalid_records),
        "latest_shard_count": len(shard_rows),
        "latest_run_ids": latest_run_ids,
        "imbalance_threshold": imbalance_threshold,
        "total_elapsed_seconds": total_elapsed,
        "average_elapsed_seconds": average_elapsed,
        "max_elapsed_seconds": round(max_elapsed, 3),
        "min_nonzero_elapsed_seconds": round(min_nonzero_elapsed, 3),
        "imbalance_ratio": imbalance_ratio,
        "overweight_shards": overweight_shards,
        "single_file_hotspots": single_file_hotspots,
        "single_file_hotspot_profiles": single_file_hotspot_profiles,
        "splittable_hotspots": splittable_hotspots,
        "shards": shard_rows,
        "invalid_records": invalid_records,
    }


def _pytest_selector_filename(arg: str) -> str | None:
    if not arg or arg == "--" or arg.startswith("-"):
        return None
    selector_path = arg.split("::", 1)[0]
    path = Path(selector_path)
    if path.suffix != ".py":
        return None
    return path.name


def filter_filenames_by_pytest_selectors(filenames: list[str], pytest_args: list[str]) -> tuple[list[str], list[str]]:
    """Narrow shard files when explicit pytest file/node selectors are supplied."""

    selected_names = [
        filename
        for arg in pytest_args
        if (filename := _pytest_selector_filename(arg)) is not None
    ]
    if not selected_names:
        return filenames, []
    available = set(filenames)
    unknown = sorted({filename for filename in selected_names if filename not in available})
    selected = [filename for filename in filenames if filename in set(selected_names)]
    return selected, unknown


def _memcap_wrap_cmd(cmd: list[str]) -> list[str]:
    """Return cmd wrapped in a systemd memory-capped scope, or cmd unchanged.

    Controlled by LAWVM_CI_MEMCAP (e.g. ``18G``).  When the variable is unset
    the returned command is byte-identical to the input.  When set but
    ``systemd-run`` is unavailable or the user scope is not usable, a one-line
    warning is printed and the original command is returned so the shard still
    runs uncapped.
    """
    memcap = os.environ.get("LAWVM_CI_MEMCAP", "").strip()
    if not memcap:
        return cmd
    if not shutil.which("systemd-run"):
        print(
            f"MEMCAP WARNING: systemd-run not found; running shard uncapped"
            f" (LAWVM_CI_MEMCAP={memcap} ignored)",
            flush=True,
        )
        return cmd
    # Quick probe: verify a user scope can actually be created before committing
    # to the real run. This catches "no user session" (e.g. bare containers).
    probe = subprocess.run(
        ["systemd-run", "--user", "--scope", "-q", "--", "true"],
        capture_output=True,
    )
    if probe.returncode != 0:
        probe_err = probe.stderr.decode(errors="replace").strip()
        print(
            f"MEMCAP WARNING: systemd-run user scope probe failed"
            f" (exit {probe.returncode}: {probe_err or '(no stderr)'})"
            f"; running shard uncapped (LAWVM_CI_MEMCAP={memcap} ignored)",
            flush=True,
        )
        return cmd
    return [
        "systemd-run",
        "--user",
        "--scope",
        "-q",
        "-p", f"MemoryMax={memcap}",
        "-p", "MemorySwapMax=2G",
        "--",
        *cmd,
    ]


def _report_memcap_oom(shard: str, exit_code: int) -> None:
    """Print a distinct diagnostic block when the cgroup likely OOM-killed the shard."""
    memcap = os.environ.get("LAWVM_CI_MEMCAP", "").strip()
    signal_name = {143: "SIGTERM (143)", 137: "SIGKILL (137)"}.get(exit_code, str(exit_code))
    print(
        f"\n"
        f"!!! SHARD KILLED: shard={shard!r} exit={signal_name}"
        f" — likely cgroup OOM under LAWVM_CI_MEMCAP={memcap}\n"
        f"!!! To inspect:  journalctl --user | grep -i oom\n"
        f"!!! To raise cap: export LAWVM_CI_MEMCAP=32G  (current: {memcap})\n"
        f"!!! To disable:   unset LAWVM_CI_MEMCAP\n",
        flush=True,
    )


def run_shard(shard: str, *, pytest_args: list[str], timing_jsonl: str | None = None) -> int:
    assignments = shard_assignments()
    if shard == "all":
        filenames = [
            filename
            for names in assignments.values()
            for filename in names
        ]
        filenames = sorted(filenames)
    elif shard in SHARD_GROUPS:
        filenames = sorted(
            filename
            for member in expand_shard_names([shard])
            for filename in assignments[member]
        )
    else:
        if shard not in assignments:
            choices = ", ".join(["all", *sorted(assignments), *sorted(SHARD_GROUPS)])
            print(f"Unknown shard {shard!r}. Choices: {choices}", file=sys.stderr)
            return 2
        filenames = assignments[shard]
    filenames, unknown_selectors = filter_filenames_by_pytest_selectors(filenames, pytest_args)
    if unknown_selectors:
        print(
            f"Selectors outside shard {shard!r}: {', '.join(unknown_selectors)}",
            file=sys.stderr,
        )
        return 2
    if not filenames:
        print(f"Shard {shard} has no test files.")
        return 0

    workers = os.environ.get("LAWVM_PYTEST_WORKERS", "4")
    xdist_args = ["-p", "no:xdist"] if workers == "0" else ["-n", workers]
    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "pytest",
        "--override-ini=addopts=",
        "-x",
        "-q",
        *xdist_args,
        "-m",
        "not network and not slow",
        *(str(TEST_DIR / filename) for filename in filenames),
        *pytest_args,
    ]
    memcap = os.environ.get("LAWVM_CI_MEMCAP", "").strip()
    effective_cmd = _memcap_wrap_cmd(cmd)
    print(f"=== shard {shard}: {len(filenames)} files ===", flush=True)
    started = time.perf_counter()
    exit_code = subprocess.call(effective_cmd, cwd=REPO_ROOT)
    elapsed = time.perf_counter() - started
    if memcap and effective_cmd is not cmd and exit_code in (143, 137):
        _report_memcap_oom(shard, exit_code)
    record = shard_timing_record(
        shard=shard,
        file_count=len(filenames),
        elapsed_seconds=elapsed,
        exit_code=exit_code,
        run_id=os.environ.get("LAWVM_SHARD_TIMING_RUN_ID"),
    )
    print(
        f"=== shard {shard} {record['status']}: {record['elapsed_seconds']:.3f}s ===",
        flush=True,
    )
    if timing_jsonl:
        append_shard_timing_record(Path(timing_jsonl), record)
    return exit_code


def list_shards() -> int:
    assignments = shard_assignments()
    print("all")
    for shard in sorted(SHARD_GROUPS):
        print(shard)
    for shard in sorted(assignments):
        print(shard)
    return 0


def list_files(shard: str) -> int:
    assignments = shard_assignments()
    if shard == "all":
        filenames = sorted(filename for names in assignments.values() for filename in names)
    elif shard in SHARD_GROUPS:
        filenames = sorted(filename for member in expand_shard_names([shard]) for filename in assignments[member])
    else:
        filenames = assignments.get(shard)
        if filenames is None:
            print(f"Unknown shard {shard!r}", file=sys.stderr)
            return 2
    for filename in filenames:
        print(f"tests/{filename}")
    return 0


def shard_plan(shard: str = "all") -> dict[str, Any]:
    assignments = shard_assignments()
    if shard != "all" and shard not in assignments and shard not in SHARD_GROUPS:
        choices = ", ".join(["all", *sorted(assignments), *sorted(SHARD_GROUPS)])
        raise ValueError(f"Unknown shard {shard!r}. Choices: {choices}")
    selected = sorted(assignments) if shard == "all" else expand_shard_names([shard])
    shards: list[dict[str, Any]] = [
        {
            "name": name,
            "patterns": list(SHARD_PATTERNS.get(name, ())),
            "files": [f"tests/{filename}" for filename in assignments[name]],
            "file_count": len(assignments[name]),
        }
        for name in selected
    ]
    assigned_count = sum(len(assignments[name]) for name in selected)
    return {
        "kind": "lawvm_pytest_shard_plan",
        "selected": shard,
        "assigned_file_count": assigned_count,
        "shards": shards,
        "excluded_tests": [
            {
                "file": f"tests/{filename}",
                "reason": reason,
            }
            for filename, reason in sorted(EXCLUDED_TESTS.items())
        ],
    }


def affected_path_plan(raw_path: str) -> dict[str, Any]:
    path = raw_path.strip()
    normalized = path.replace("\\", "/")
    selector_path = normalized.split("::", 1)[0]
    filename = Path(selector_path).name

    def plan(shards: list[str], reason: str) -> dict[str, Any]:
        return {
            "path": raw_path,
            "shards": shards,
            "expanded_shards": _affected_shards_from_path_plans([{"shards": shards}]),
            "reason": reason,
        }

    if not path:
        return plan(
            list(ALL_SHARDS),
            "empty input path is not mapped to a bounded shard; run all affected shards",
        )
    if normalized in GLOBAL_CHANGE_PATHS:
        return plan(list(ALL_SHARDS), "global dependency change forces all affected shards")
    if selector_path.startswith("tests/") and filename.startswith("test_") and filename.endswith(".py"):
        if filename in EXCLUDED_TESTS:
            return plan(
                list(ALL_SHARDS),
                f"excluded test: {EXCLUDED_TESTS[filename]}; run all affected shards",
            )
        matches = explicit_matches(filename)
        if matches:
            return plan(sorted(matches), "test file matches explicit shard pattern")
        return plan(["misc"], "test file has no explicit shard pattern and maps to misc")
    for prefix, shards in SOURCE_SHARD_PREFIXES:
        if normalized.startswith(prefix):
            if shards == ALL_SHARDS:
                return plan(
                    list(ALL_SHARDS),
                    f"core/dependency prefix {prefix} forces all affected shards",
                )
            return plan(list(shards), f"known frontend prefix {prefix} maps to {', '.join(shards)}")
    if normalized.startswith(TOOLING_SHARD_PREFIXES):
        prefixes = ", ".join(TOOLING_SHARD_PREFIXES)
        return plan(["tools"], f"tools prefix {prefixes} maps to tools")
    return plan(
        list(ALL_SHARDS),
        "unknown path is not mapped to a bounded shard; run all affected shards",
    )


def affected_path_plans(paths: list[str]) -> list[dict[str, Any]]:
    return [affected_path_plan(path) for path in paths]


def _affected_shards_from_path_plans(path_plans: list[dict[str, Any]]) -> list[str]:
    affected: set[str] = set()
    for item in path_plans:
        affected.update(item["shards"])
    if not affected or "all" in affected:
        return ["all"]
    return sorted(expand_shard_names(sorted(affected)))


def affected_shards(paths: list[str]) -> list[str]:
    """Map changed repo paths to a conservative bounded-test shard set."""

    if not paths:
        return ["all"]
    return _affected_shards_from_path_plans(affected_path_plans(paths))


def affected_plan(paths: list[str]) -> dict[str, Any]:
    path_plans = affected_path_plans(paths)
    shards = _affected_shards_from_path_plans(path_plans)
    return {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": list(paths),
        "shards": shards,
        "paths": path_plans,
    }


def print_affected(paths: list[str], *, json_output: bool = False) -> int:
    plan = affected_plan(paths)
    if json_output:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    for shard in plan["shards"]:
        print(shard)
    return 0


def print_expanded(shards: list[str]) -> int:
    for shard in expand_shard_names(shards):
        print(shard)
    return 0


def print_plan(shard: str, *, json_output: bool = False) -> int:
    try:
        plan = shard_plan(shard)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if json_output:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    for item in plan["shards"]:
        print(f"{item['name']}: {item['file_count']} files")
        for filename in item["files"]:
            print(f"  {filename}")
    if shard == "all":
        print(f"assigned: {plan['assigned_file_count']}")
        for item in plan["excluded_tests"]:
            print(f"excluded: {item['file']} ({item['reason']})")
    return 0


def print_timing_balance(
    path: str,
    *,
    json_output: bool = False,
    imbalance_threshold: float = 2.0,
    run_id: str | None = None,
) -> int:
    report = shard_timing_balance_report(
        Path(path),
        imbalance_threshold=imbalance_threshold,
        run_id=run_id,
    )
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["invalid_record_count"] == 0 else 1
    print(f"timing records: {report['valid_record_count']} valid, {report['invalid_record_count']} invalid")
    if report["run_id_filter"]:
        print(f"run id filter: {report['run_id_filter']}")
    print(f"latest shards: {report['latest_shard_count']}")
    if report["latest_run_ids"]:
        print("latest run ids:", ", ".join(report["latest_run_ids"]))
    print(f"total elapsed: {report['total_elapsed_seconds']:.3f}s")
    print(f"average shard: {report['average_elapsed_seconds']:.3f}s")
    print(f"imbalance ratio: {report['imbalance_ratio']:.3f}")
    if report["overweight_shards"]:
        print("overweight shards:", ", ".join(report["overweight_shards"]))
    if report["single_file_hotspots"]:
        print("single-file hotspots:", ", ".join(report["single_file_hotspots"]))
    if report["single_file_hotspot_profiles"]:
        print("single-file hotspot profiling commands:")
        for profile in report["single_file_hotspot_profiles"]:
            file_label = profile["file"] or "(file unknown for shard)"
            print(f"  {profile['shard']}: {file_label}")
            print(f"    {profile['command']}")
    if report["splittable_hotspots"]:
        print("multi-file split candidates:", ", ".join(report["splittable_hotspots"]))
    for row in report["shards"]:
        seconds_per_file = row["seconds_per_file"]
        per_file = "n/a" if seconds_per_file is None else f"{seconds_per_file:.3f}s/file"
        print(
            f"{row['shard']}: {row['elapsed_seconds']:.3f}s "
            f"({row['file_count']} files, {per_file}, {row['status']})"
        )
    return 0 if report["invalid_record_count"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    list_files_parser = subparsers.add_parser("files")
    list_files_parser.add_argument("shard")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("shard", nargs="?", default="all")
    plan_parser.add_argument("--json", action="store_true", dest="json_output")
    affected_parser = subparsers.add_parser("affected")
    affected_parser.add_argument("--json", action="store_true", dest="json_output")
    affected_parser.add_argument("paths", nargs="*")
    expand_parser = subparsers.add_parser("expand")
    expand_parser.add_argument("shards", nargs="+")
    timings_parser = subparsers.add_parser("timings")
    timings_parser.add_argument("path")
    timings_parser.add_argument("--json", action="store_true", dest="json_output")
    timings_parser.add_argument(
        "--imbalance-threshold",
        type=float,
        default=2.0,
        help="flag shards at or above average elapsed seconds multiplied by this value",
    )
    timings_parser.add_argument(
        "--run-id",
        default=None,
        help="summarize only timing records with this run_id",
    )
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--timing-jsonl",
        default=os.environ.get("LAWVM_SHARD_TIMING_JSONL"),
        help="append a JSONL timing record for this shard run",
    )
    run_parser.add_argument("shard")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate()
    if args.command == "list":
        return list_shards()
    if args.command == "files":
        return list_files(args.shard)
    if args.command == "plan":
        return print_plan(args.shard, json_output=args.json_output)
    if args.command == "affected":
        return print_affected(args.paths, json_output=args.json_output)
    if args.command == "expand":
        return print_expanded(args.shards)
    if args.command == "timings":
        return print_timing_balance(
            args.path,
            json_output=args.json_output,
            imbalance_threshold=args.imbalance_threshold,
            run_id=args.run_id,
        )
    if args.command == "run":
        return run_shard(args.shard, pytest_args=args.pytest_args, timing_jsonl=args.timing_jsonl)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
