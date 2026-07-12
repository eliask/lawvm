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
    # Distributable-substrate tests (tests/substrate/).
    # These live under the tests/substrate/ subdirectory; their shard patterns
    # use the "substrate/" prefix so _all_test_files() + run_shard resolve them
    # correctly.  The wildcard catches all existing substrate unit tests plus
    # the committed prototype-pack acceptance test (§17).
    "substrate": (
        "substrate/test_*.py",
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
        "test_new_zealand_html_source.py",
        "test_new_zealand_source_tree.py",
        "test_new_zealand_version_diff.py",
        "test_nz_legal_text_compose.py",
    ),
    "new_zealand_effects": (
        "test_new_zealand_actual_replay.py",
        "test_new_zealand_actual_replay_structural.py",
        "test_new_zealand_actual_replay_corpus_smoke.py",
        "test_new_zealand_bench_regression.py",
        "test_new_zealand_chain_replay.py",
        "test_new_zealand_chain_replay_corpus.py",
        "test_new_zealand_chain_replay_idempotency_smoke.py",
        "test_nz_same_moment_ambiguity.py",
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
        "test_nz_surface_reuse.py",
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
        # #186 §4.2 item 4 / §7 delta #4: UK_LABEL_ALGEBRA, the DECLARED,
        # conformance-tested mirror of UK's numeric-stem + ``4A`` / ``4ZA`` insert
        # label calculus. Each op is bound to UK's ACTUAL label code
        # (_label_sort_key / _clean_num / _next_same_stem_alnum_label). Not matched
        # by the ``test_uk_*`` glob (name is ``test_label_algebra_uk``), so pinned
        # explicitly. Parallel-first: grafter insert-path routing deferred.
        "test_label_algebra_uk.py",
    ),
    "eu": (
        "test_eu_*.py",
        # Tests nested under tests/eu/ subdirectory.
        "eu/test_eu_*.py",
    ),
    "starter": (
        # Generic scaffold/starter frontend tests. The U.S. federal jurisdiction
        # frontend tests (``test_jurisdiction_starter_us_federal_*.py``) are owned
        # by the dedicated ``us_federal`` shard below, so this glob is scoped to
        # the ``p5`` runtime-scaffold family to avoid double-ownership.
        "test_jurisdiction_starter_p5_*.py",
        "test_open_law_frontend.py",
        "test_open_law_belief_revision.py",
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
        # #186 §4.2 item 4 / §7 delta #4: US_LABEL_ALGEBRA, the DECLARED,
        # conformance-tested mirror of the US Code numeric-stem + ``106A`` letter
        # insert label calculus. Each op is bound to the SHARED label code the US
        # frontend orders on (default_label_sort_key / normalized_label_key); the
        # successor is synthesized from that decomposition (no standalone US
        # next-label helper). Not matched by the ``test_jurisdiction_starter_*`` /
        # ``test_us_*`` globs (name is ``test_label_algebra_us``), so pinned
        # explicitly. Parallel-first: grafter insert-path routing deferred.
        "test_label_algebra_us.py",
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
        # D2+D3 e2e: native-PDF SourceDocumentIR ingest + determinism firewall
        # (plan .claude/plans/calm-kindling-wand.md).
        "test_fi_source_document_pdf.py",
        # Proposal-carrier → enacted-infra lowering (branch_lowering.py): a
        # draft-HE CandidateOperation/ConditionalBranch projects into
        # core.ir.LegalOperation + branch_authority carriers, never replay-authorized.
        "test_fi_he_branch_lowering.py",
        "test_fi_branch_conflicts.py",
        # `lawvm fi-he-branch`: farchive HE PDF → conditional branches (+ materialize).
        "test_fi_he_branch_cli.py",
        # Derived-IR store: content-addressed cache of LawVM IR parsed from PDFs.
        "test_fi_parsed_store.py",
        # Bulk parse driver: bounded per-PDF concurrency + struct/flat lane routing.
        "test_fi_parse_attachments_concurrency.py",
        # Scanned / text-poor corpus census (fi_scan_stratum): pdfium text-layer
        # coverage → born_digital / mixed / scanned strata; hermetic thresholds +
        # deterministic content-key CSV ordering (the vision-fidelity hard-case set).
        "test_fi_scan_stratum.py",
        # v2 span-copy build-script wire: explicit structural build script
        # (hierarchy, tables, content-addressed images, span vs inline leaves).
        "test_fi_struct_build_wire.py",
        # Store-boundary guard rejecting HTTP-error bodies archived as PDF blobs
        # (finland/pdf_blob_guard.py) + the report-only junk-PDF farchive scan.
        "test_fi_pdf_blob_guard.py",
        # Draft-HE corpus-sweep status vocabulary (scripts/he_corpus_sweep.py):
        # clean / partial / failed classification of the deterministic lowering.
        "test_he_corpus_sweep.py",
        # Draft-HE materialize + cross-bill conflict report
        # (scripts/he_draft_materialize_report.py): pure (statute, section)
        # cross-bill collision grouping + typed materialize-status skips.
        "test_fi_he_materialize_report.py",
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
        "test_fi_statute_lifecycle_lookup.py",
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
        # #186 θ (theta) TotalizationTable extended to Finland. FI is
        # observation-based (off-domain occupancy is a non-blocking observation;
        # the op still applies) with a three-outcome mutation-event ledger, so the
        # deliverable is a DECLARED FI_TOTALIZATION_TABLE with routing DEFERRED;
        # this test binds each declared cell to FI's ACTUAL runtime code at the
        # source level (the faithful-spec guard).
        "test_totalization_conformance_fi.py",
        # #186 §4.2 item 4 / §7 delta #4: FI_LABEL_ALGEBRA, the DECLARED,
        # conformance-tested mirror of Finland's Arabic + lettered ``14 a §``
        # section-label calculus. Each op is bound to FI's ACTUAL label code
        # (_section_sort_key / _norm_num_token / next_letter_label), plus
        # neutral-type order-law + parse round-trip unit tests. Parallel-first:
        # grafter insert-path routing deferred to the load-bearing follow-up.
        "test_label_algebra_fi.py",
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
        # Determinism firewall (Fable 5 #5): whole-graph ratchet — no
        # replay/projection-cone module may import an LLM client
        # (finland.llm_backends.*). Sits with the determinism/fail-loud gates.
        # See notes/DETERMINISM_FIREWALL.md.
        "test_determinism_firewall.py",
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
        # D0 source-document ingestion carrier: SourceDocumentIR node + assurance
        # ladder + anchors/extraction/coverage/residual taxonomy.
        "test_source_document_carrier.py",
        # Track-A FROZEN two-level-pipeline ingest carriers (§5.5): SpanRef /
        # FreeformRegion / ConvergenceInfo / PageSimulacrum / DeFacsimileClaim +
        # the NodeMetadata ↔ attrs closed-vocab codec, and the new additive
        # MATH_REGION / VERBATIM_REGION freeform node kinds.
        "test_ingest_carriers.py",
        # Track-B Level-1 (faithful per-page simulacra): freeform MATH/VERBATIM
        # wire parse+lowering, per-line geometry + metadata capture, the
        # patch-to-convergence gate + termination (empty_patch/fixpoint/
        # oscillation/max_iters/gated_single_pass/truncated), the
        # unwitnessed_content tripwire, furniture-kept-as-hint, and the
        # PageSimulacrum store round-trip.
        "test_ingest_page_level.py",
        # §8 Level-1 agentic re-read (garble recovery): the shared visual primitive
        # (render_region_crop content-addressed locator + typed raise), deterministic
        # suspect surfacing (cross-reader disagreement PRIMARY + lexical
        # implausibility SECONDARY, never on length alone), the gated re-read
        # (more-plausible / cross-reader-agreement) replacing the leaf via the
        # existing patch mechanism, zero-re-reads on a clean page, and determinism
        # (byte-identical simulacrum + JSON round-trip carrying the new rereads field).
        "test_ingest_reread.py",
        # Cold multi-line region reader + systemic pdfium lock (#250): the
        # calibration/§9 cold-read adapter (``read_region_cold``) returns a WHOLE
        # region's multi-line transcription while the §8 ``reread_region`` correction
        # path stays one-line; the calibration ``live_region_reader`` hook binds the
        # cold reader (real multi-line over a fake vision, honest empty on an
        # un-croppable region); and the ONE shared ``ingest.visual.PDFIUM_LOCK`` is
        # genuinely held around every pdfium call (a concurrent thread cannot acquire
        # it mid-render) and is the SAME object across page_elements / calibration.
        "test_ingest_cold_region_reader.py",
        # Thread-safe token + throughput ledger (observability): the vision choke
        # point ``_post_chat`` records one tagged row per model call (input/output
        # tokens from ``usage``, prompt/decode tok/s from llama.cpp ``timings``, wall
        # time around the round-trip); concurrent N×M calls accumulate with no lost
        # updates; thread-local ``meter_unit`` pdf/page tags survive a real
        # ThreadPool and roll up; missing usage/timings degrades to a typed partial
        # row; ``summary()`` computes wall-vs-compute tok/s + the GPU-utilization
        # ratio; the meter never perturbs the parse result (determinism firewall).
        "test_ingest_token_meter.py",
        # meta.v2 typography lane: pdfplumber char-span → pypdfium2 PageLine
        # geometry alignment (fake spans), font-name → family/bold/italic parse,
        # document-adaptive size_class (relative to page median), char-grouping
        # collapse, the typo.* keys' encode/decode round-trip + node lowering,
        # graceful absence when unalignable / pdfplumber-absent, and a live
        # 1-page real-PDF extraction (skips when the pdf extra is absent).
        "test_ingest_typography.py",
        # Track-C Level-2 de-facsimile: the PURE fold + idempotence, verify_ledger
        # (phantom-drop / invented-REJOIN-text / multiset / claim-disjointness /
        # NUMERIC-change gates), the windowed adjudicator (line-reply parse,
        # repetition-loop withhold, truncation → per-window deterministic
        # fallback), and the HE-2015/1 defect fixtures.
        "test_ingest_defacsimile.py",
        # M3 stigmergic (blackboard) de-facsimile composer (§7): metadata
        # mark pre-seeding (FURNITURE?/GARBLE/OPEN), the typed+extensible
        # affordance dispatch table (VIEW/EXPAND/PAGE/NOTES/NOTE/PREFIX/DEFER,
        # crop injected/faked), controller scheduling to a stigmergic fixpoint,
        # budget-exhaustion → context_exhausted fallback, the content-addressed
        # workspace journal round-trip + determinism, verify_ledger still gating,
        # the M1 single-pass path unchanged, and put_workspace/get_workspace.
        "test_ingest_blackboard.py",
        # Track-C de-facsimile ledger persistence (Decision 5): put_ledger /
        # get_ledger sibling-blob round-trip + manifest op/tier histograms.
        "test_ingest_defacsimile_ledger_store.py",
        # Integration: the converged two-level de-facsimile parse lane end-to-end
        # (parse_defacsimile_and_cache) — Level-1 simulacra persisted, Level-2
        # verified ledger persisted, canonical IR produced, idempotent cache hit.
        "test_ingest_defacsimile_parse_lane.py",
        # Deterministic born-digital geom lane (ingest.born_digital): span-copy
        # segmentation + coverage gate + struct_geom fast-path on synthetic
        # PageElements (hermetic, no PDF lib / model). Ingest-carrier family.
        "test_ingest_born_digital.py",
        # Deterministic OMISSION census (ingest.coverage_census): the coverage-ledger
        # verifier that audits DROPPED units (the blind spot every extracted-unit
        # verifier misses) — unclaimed source ink -> pdf.omission_suspect, page/§
        # ordinal gaps -> pdf.sequence_gap, furniture distinguished by geometry.
        # Seeded-drop proof + clean-page no-false-flag (hermetic). Ingest-carrier.
        "test_ingest_coverage_census.py",
        # Jurisdiction-agnostic glyph-substitution text-layer repair primitive
        # (ingest.text_layer_repair): validated re.sub against a known shape +
        # independent-constraint validator (the general multi-witness token
        # reconciliation the FI cite slash-as-"1" repair delegates to). Ingest-carrier.
        "test_text_layer_repair.py",
        # The CORROBORATE edge (ingest.corroboration): typed EscalationPending +
        # CorroborationReceipt + offline-safe corroborate() driving a candidate through
        # an injected vision witness (agree/verdict-changed off the canonical
        # cross_reader_disagrees/more_plausible primitives). Ingest-carrier family.
        "test_corroboration.py",
        # Durable model-I/O side-channel log (env-gated append-only JSONL; images
        # stored as sha256/len metadata, never the blob). Ingest-carrier family.
        "test_ingest_model_io_log.py",
        # D1 coverage metric + owned-content quality detectors.
        "test_source_document_coverage.py",
        # Producer-neutral adjudication layer: assurance_for + Adjudicator.
        "test_source_document_adjudication.py",
        # Workflow-LLM adjudicator: reconcile candidates → composed node + tier.
        "test_source_document_llm_adjudicator.py",
        # Draft-HE → ConditionalBranch ("if enacted, then …") extraction.
        "test_fi_he_conditional_branch.py",
        # Reproducible draft-HE acquisition (lausuntopalvelu / hankeikkuna URL).
        "test_fi_lausuntopalvelu.py",
        # Nemotron-Parse thin client: process-isolated vision producer
        # (subprojects/nemotron_parse) — wire contract + isolation ratchet.
        "test_fi_nemotron_client.py",
        # Docling structural producer: learned-layout + TableFormer cell grids.
        "test_fi_docling_producer.py",
        # Cross-page composition: per-page trees → one whole-document IR.
        "test_source_document_composition.py",
        # Materialize a conditional branch against enacted law (if-enacted diff).
        "test_fi_materialize.py",
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
        "test_filter_result_pending_cell.py",
        # #186 §5.5 / §7 delta #7: group_id atomicity — a rejected member rejects
        # the whole group with a per-member witness, partition stays total+disjoint.
        "test_filter_result_group_atomicity.py",
        "test_fi_intent_compat.py",
        "test_fi_optype_strenum.py",
        "test_ir_*.py",
        # #186 §5.4: optional ordinal disambiguator on LegalAddress path elements
        # (duplicate-label resolution) + resolve_with_ordinals contract.
        "test_legal_address_ordinal.py",
        # #186 §5.3 / §7 delta #6: optional address-ROOT compartment selector on
        # LegalAddress (body vs supplements/bilaga) — body byte-identity + the
        # supplements-root resolution lane.
        "test_legal_address_compartment_root.py",
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
        # #186 θ (theta) TotalizationTable: the DECLARED, conformance-tested spec
        # of the grafters' off-domain (precondition-failure) behaviour. The core
        # neutral θ type (Reject/NoopIdempotent/Recover) + SE (strict default) &
        # NO (rich recovery) tables, with each declared (action, failure_class)
        # cell bound to the ACTUAL runtime disposition via the real conserved
        # apply path. Parallel-first: control-flow routing deferred to the
        # load-bearing follow-up.
        "test_totalization_conformance.py",
        # #186 §4.2 item 4 / §7 delta #4: the neutral LabelAlgebra seam
        # (parse / successor-set / order / collision) + EE_LABEL_ALGEBRA, the
        # DECLARED, conformance-tested mirror of Estonia's superscript ``§10¹`` /
        # lettered ``14a`` label calculus. Each op is bound to EE's ACTUAL label
        # code (default_label_sort_key / normalized_label_key / _normalize_num),
        # plus neutral-type order-law + parse round-trip unit tests.
        # Parallel-first: grafter relabel-path routing deferred to the follow-up.
        "test_label_algebra.py",
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
        "test_oracle_default_policy_parity.py",
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
        # AST-scan §2.3 firewall test — the FI ``he_draft`` document-role idiom
        # (source_role="he_draft") must never leak into ``lawvm.core`` as a
        # machine identifier. Mirrors the sibling firewall no-leak tests above.
        "test_core_firewall_no_he_draft_source_role.py",
        "test_comparison_normalization.py",
        "test_comparison_normalization_shared_lifts.py",
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
        "test_fi_interlink_target_subsection.py",
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
        "test_temporal_pit_seeds.py",
        "test_temporal_resolution.py",
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
        # #191 oracle-defect external-confirmation rail (keeper acknowledgments).
        "test_oracle_defect_confirmation.py",
        "test_spec_ledger.py",
        "test_spec_ledger_discovery.py",
        # §8(7) frontier ranking: B × S × EIG replaces raw firing-count as the rank key.
        "test_spec_ledger_frontier.py",
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
        # #184 Canonical Text-State Form v0: projection + control-pair admission
        # gate (incl. negative rejection test) + migrated editorial rules + the
        # spec-ledger glue unification + bench byte-identity guard.
        "test_ctsf.py",
        # #197 CTSF Phase 2: STATE_INDEX commensurability layer (each typed
        # residual + the commensurability-first short-circuit), the two
        # newly-migrated label-redundancy rules (+ negative gate test), the
        # parallel residual-set report, and the bench byte-identity guard.
        "test_ctsf_phase2.py",
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
    "tools_ctsf_gate": (
        # CTSF residual-set-diff gate surface. Split out of tools_cli_debug so
        # baseline and gate-module edits do not pull unrelated CLI/debug tests.
        "test_ctsf_gate.py",
        "test_ctsf_gate_ee.py",
        "test_ctsf_gate_uk.py",
        "test_ctsf_gate_eu.py",
        "test_ctsf_gate_nz.py",
        "test_ctsf_gate_se.py",
        "test_ctsf_gate_no.py",
        "test_ctsf_gate_us.py",
        # CTSF mis-typing canary: a secondary audit re-derives billability from each
        # residual's raw rule_id (independent of the classifier's family verdict) and
        # surfaces any billable-rule residual typed into a non-failing family — proving a
        # mis-typed replay-bug can't ride an 11-family non-failing space to a green gate.
        "test_ctsf_gate_family_typing_canary.py",
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
        "test_fi_parse_compare.py",
        # Track-C spec-§2 A/B: intelligent XML-vs-PDF adjudication over a
        # de-facsimiled reconstruction; acceptance = EXTRA+STRUCTURE down,
        # MISSING not up, NUMERIC unchanged (mocked adjudicator, hermetic).
        "test_fi_parse_compare_defacsimile_ab.py",
        # Corpus-scale end-to-end A/B harness (fi-parse-corpus): enumeration +
        # PDF/XML sibling pairing, worst-first ranking, success-criterion
        # aggregate, and byte-identical determinism (scripted stub processor,
        # hermetic — the live corpus sweep is operator-invoked, not CI).
        "test_fi_parse_corpus.py",
        # Reliability calibration U-curve harness (fi-calibration): region
        # subdivision, per-config end-to-end post-stitch scoring, ceiling
        # detection + 0.7x operating-point derating, adaptive-policy version-tag
        # fold, and proxy validation (fake reader, hermetic — the live GPU sweep
        # is operator-invoked, not CI).
        "test_fi_calibration.py",
        # Vision transcription-error calibration harness (fi-vision-read-calibration):
        # GT extraction/validation, CER/WER/hallucination metrics, crop/reflow geometry,
        # read-cache keying, and the config-grid runner + multi-read consensus (stub
        # backend, hermetic — no :8080 / libvoikko call in CI).
        "test_fi_vision_read_calibration.py",
        # VoI-staged corpus reliability sweep driver (fi-sweep): escalating
        # superset-nested stratified stages, the per-stage NUMERIC-exact + accept-
        # regression gate (PROCEED clean / STOP on regression, dropping the next
        # tranche), deterministic dry-run plan, resume skipping completed PDFs, and
        # the ranked residual-defect-class report (fake processor, hermetic — the
        # full GPU run is operator-invoked, not CI).
        "test_fi_sweep.py",
        # PDF→IR amendment EXACTNESS eval harness (fi-amendment-ir-compare):
        # op IR-equivalence (PDF→ops vs XML→ops) + the payload BODY-text diff,
        # and the corpus aggregation/JSONL driver. Hermetic siblings of the
        # fi-parse-compare / fi-parse-corpus A/B harnesses above.
        "test_fi_amendment_ir_compare.py",
        "test_fi_amendment_ir_corpus.py",
        "test_fi_amendment_ir_payload.py",
        # HE proposed-effect IR EXACTNESS eval harness (fi-he-ir-compare, phase 2):
        # the scripted-witness op-equivalence diff + the corpus driver.
        "test_fi_he_ir_compare.py",
        "test_fi_he_ir_corpus.py",
        # HE-IR-compare supporting lanes (LLM transport INJECTED, hermetic): the
        # johtolause span-tagger + its determinism-firewall cache, the payload-
        # divergence adjudicator, and the payload-verdict content-addressed store.
        "test_fi_he_johtolause_tagger.py",
        "test_fi_he_payload_adjudicator.py",
        "test_fi_he_payload_verdict_store.py",
        # Legally-inert encoding quotient (finland.op_equivalence) the payload
        # compare folds over: inert invisible/whitespace collapse, visible-glyph
        # differences fall through as typed residuals.
        "test_fi_op_equivalence.py",
        # Phase-3 appendix-structure prototype (fi-appendix-structure): pure
        # numeric-completeness / cross-witness metrics + Docling-node → table-IR
        # lowering on plain data (hermetic; the docling/pdfium seam is not driven).
        "test_fi_appendix_structure.py",
        # Level-1 producer usefulness A/B (fi-producer-compare): reused NUMERIC /
        # WER / word-coverage scorers + per-page union combo, typed-failure
        # discipline, token attribution (scripted fake producers, hermetic).
        "test_fi_producer_compare.py",
        # Phase-3 vision witness layer: holistic sanity screen (garble/gestalt,
        # never graduates) + false-graduation canary ratchet (frozen mutant gold) +
        # the end-to-end vision-lane wiring proof (build_statute_report + derived-IR
        # sink + corroborate edge, hermetic with a scripted region reader).
        "test_fi_appendix_vision_screen.py",
        "test_fi_appendix_vision_canary.py",
        "test_fi_appendix_vision_wiring.py",
        # MinerU firewalled table producer: pure HTML grid-occupancy parse + MineruTable
        # → StructuredTable lowering + the born-digital text-layer VERIFY GATE (glyph
        # errors typed, never graduated) + store-replay / cold-offline control flow. The
        # subprocess seam (external py3.12 venv) is never driven in CI.
        "test_mineru_producer.py",
        # False-graduation canary (fi-verification-canary): the error bar on "verified".
        # Seeds known errors and drives the REAL gates — op-equivalence quotient, vision
        # consensus reconcile (Gate A/B), MinerU table verify — measuring the per-class
        # false-graduation rate (fold non-masking, Gate-B correlated false-corroboration,
        # the omitted-cell census blind-spot). Stub witnesses, hermetic (no :8080/subprocess).
        "test_fi_verification_canary.py",
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
        "test_all_pit_driver.py",
        "test_fi_anchor_manifest.py",
        "test_temporal_holdout.py",
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
        "test_seeded_fault_study.py",
    ),
}

SHARD_GROUPS: dict[str, tuple[str, ...]] = {
    "frontends": (
        "estonia",
        "eu",
        "finland",
        "new_zealand",
        "norway",
        "starter",
        "sweden",
        "uk",
        "us_federal",
    ),
    "modules": ("core", "evidence", "properties", "properties_timeline", "substrate", "tools"),
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
        "tools_ctsf_gate",
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
    # FAST VALIDATION PATH for ingest-local edits (src/lawvm/ingest/**, its
    # finland/source_document shim, and the fi_parse_*/fi_calibration drivers).
    #
    # WHY THIS EXISTS: ``--affected src/lawvm/ingest/...`` maps (below, in
    # SOURCE_SHARD_PREFIXES) to the whole ``finland`` + ``core`` GROUPS plus
    # ``tools_runtime_io`` — 15 shards / ~580 test files — because the ingest
    # modules are transitively
    # imported by the full Finland-replay + core closure (facades / CLIs /
    # registries pull ``source_document`` which pulls ``ingest`` at import time).
    # That map is deliberately conservative and STAYS conservative (a real
    # cross-cutting edit must keep pulling the broad set), so the closure cannot
    # be safely narrowed by import analysis alone — every replay/core shard has
    # at least one test that transitively imports the ingest cone.
    #
    # This ``ingest`` group is the AGENT CONVENTION to use INSTEAD of
    # ``--affected`` while iterating on an ingest-LOCAL change:
    #
    #     ./scripts/ci.sh --shards ingest        # or scripts/validate_ingest.sh
    #
    # It is the BOUNDED, SUFFICIENT set: every test that DIRECTLY exercises
    # ingest behaviour (all ``test_ingest_*`` + ``source_document_*`` in
    # ``core_ir_contracts``; the FI PDF-parse tests in ``finland_sources`` /
    # ``finland_parse_payload``; ``fi_calibration`` / ``fi_parse_*`` in
    # ``tools_runtime_io``) PLUS the whole-graph ratchets an ingest edit can trip
    # (module-role / naming-hygiene / regex in ``core_ir_contracts``;
    # determinism-firewall / discipline gates in ``core_discipline_gates``).
    # ~248 files / 5 shards vs. 580 / 15. It drops only the heavy full-corpus
    # REPLAY shards (finland_replay_*, core_tree_apply / _replay_timeline /
    # _surface_semantic) that load the Finland corpus but never run ingest.
    #
    # This is a convenience gate, NOT a coverage reduction: the full
    # ``./scripts/ci.sh`` remains the pre-push authority.
    "ingest": (
        "finland_sources",
        "finland_parse_payload",
        "core_ir_contracts",
        "core_discipline_gates",
        "tools_runtime_io",
    ),
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
    (
        "data/finland/reference_successor_promotion_claims_fi.jsonl",
        ("core_surface_semantic",),
    ),
    ("src/lawvm/contracts.py", ("core",)),
    ("src/lawvm/graph_build.py", ("core", "tools_cli_debug")),
    ("src/lawvm/semantic/", ("core", "finland", "tools_cli_debug")),
    ("src/lawvm/xml_ingest.py", ("core", "finland", "tools_cli_debug")),
    ("src/lawvm/estonia/", ("estonia",)),
    ("src/lawvm/eu/", ("eu",)),
    ("tests/eu/fixtures/", ("eu",)),
    ("src/lawvm/finland/", ("finland",)),
    # Neutral two-level PDF→IR ingest machinery (Track A move). Its tests did NOT
    # move (the finland compat shim keeps their imports working), so a change to
    # ingest source must trigger those tests — which live in BOTH the finland
    # shard-group (``finland_sources``: struct-build/parsed-store) and the core
    # shard-group (``core_ir_contracts``: llm-adjudicator/nemotron/docling +
    # ``core_discipline_gates``: determinism-firewall).
    #
    # This map stays deliberately CONSERVATIVE (whole finland + core groups plus
    # ``tools_runtime_io``): ingest modules are transitively imported across the
    # full replay/core closure, so ``--affected src/lawvm/ingest/...`` runs 15
    # shards / ~580 files (~13 min). ``tools_runtime_io`` is included because
    # ``fi_calibration`` / ``fi_parse_compare`` / ``fi_parse_corpus`` DIRECTLY
    # import ``lawvm.ingest`` yet live outside the finland/core groups (a former
    # coverage gap). For fast local iteration on an ingest-LOCAL edit, use the
    # bounded ``ingest`` shard GROUP instead (``./scripts/ci.sh --shards ingest``
    # or ``scripts/validate_ingest.sh``); see the ``ingest`` entry in
    # SHARD_GROUPS. Full ``./scripts/ci.sh`` remains the pre-push authority.
    ("src/lawvm/ingest/", ("finland", "core", "tools_runtime_io")),
    ("src/lawvm/new_zealand/", ("new_zealand",)),
    ("src/lawvm/norway/", ("norway",)),
    ("src/lawvm/open_law/", ("starter",)),
    ("src/lawvm/sweden/", ("sweden",)),
    ("src/lawvm/uk_legislation/", ("uk",)),
    ("src/lawvm/us_federal/", ("us_federal",)),
    ("src/lawvm/core/", ("all",)),
    ("src/lawvm/jurisdiction_starter/", ("starter",)),
    ("src/lawvm/substrate/", ("substrate",)),
)

TOOLING_SHARD_PATHS: dict[str, tuple[str, ...]] = {
    ".gitignore": ("tools_audit_release",),
    "scripts/ci.sh": ("tools_audit_release",),
    "scripts/ci_sharded.sh": ("tools_audit_release",),
    "scripts/test_shard.py": ("tools_audit_release",),
    "scripts/test_shard.sh": ("tools_audit_release",),
    "scripts/release_hygiene.sh": ("tools_audit_release",),
}
TOOL_SOURCE_SHARD_GROUPS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("estonia", "tools_cli_debug"): (
        "src/lawvm/tools/ee_anchor_manifest.py",
        "src/lawvm/tools/ee_bench.py",
        "src/lawvm/tools/ee_blame.py",
        "src/lawvm/tools/ee_chain_quality.py",
        "src/lawvm/tools/ee_consolidation_candidates.py",
        "src/lawvm/tools/ee_corpus.py",
        "src/lawvm/tools/ee_explain.py",
        "src/lawvm/tools/ee_frontier.py",
        "src/lawvm/tools/ee_inspect_source.py",
        "src/lawvm/tools/ee_pair_status.py",
        "src/lawvm/tools/ee_publication_db.py",
        "src/lawvm/tools/ee_replay.py",
        "src/lawvm/tools/ee_reporting.py",
        "src/lawvm/tools/ee_residual_inventory.py",
        "src/lawvm/tools/ee_residual_proposal.py",
        "src/lawvm/tools/ee_self_consistency.py",
    ),
    ("eu", "tools_cli_debug"): (
        "src/lawvm/tools/eu_anchor_manifest.py",
        "src/lawvm/tools/eu_reference_report.py",
        "src/lawvm/tools/eu_replay.py",
        "src/lawvm/tools/eu_reul.py",
        "src/lawvm/tools/mine_eu_nicknames.py",
    ),
    ("evidence", "tools_cli_debug"): (
        "src/lawvm/tools/_evidence_helpers.py",
        "src/lawvm/tools/evidence.py",
        "src/lawvm/tools/evidence_claim_algebra.py",
        "src/lawvm/tools/evidence_claims.py",
        "src/lawvm/tools/evidence_context.py",
        "src/lawvm/tools/evidence_render.py",
        "src/lawvm/tools/evidence_section_rules.py",
        "src/lawvm/tools/evidence_statute_rules.py",
        "src/lawvm/tools/strict_report.py",
    ),
    ("finland", "tools_cli_debug"): (
        "src/lawvm/tools/fi_adjudication_audit.py",
        "src/lawvm/tools/fi_anchor_manifest.py",
        "src/lawvm/tools/fi_aux_pit_probe.py",
        "src/lawvm/tools/fi_invariant_audit.py",
        "src/lawvm/tools/fi_parse_explain.py",
        "src/lawvm/tools/fi_parse_view.py",
        "src/lawvm/tools/fi_periodic_table.py",
        "src/lawvm/tools/fi_proposal_bundle.py",
        "src/lawvm/tools/fi_scan_stratum.py",
        "src/lawvm/tools/fi_proposal_history.py",
        "src/lawvm/tools/fi_proposal_show.py",
        "src/lawvm/tools/fi_proposals_competing.py",
        "src/lawvm/tools/fi_proposals_query.py",
        "src/lawvm/tools/fi_refs_view.py",
        "src/lawvm/tools/fi_source_label_audit.py",
        "src/lawvm/tools/fi_timeline_robust_sweep.py",
        "src/lawvm/tools/finland_rulebook.py",
        "src/lawvm/tools/sync_fi_proposals.py",
    ),
    ("finland", "tools_runtime_io"): (
        "src/lawvm/tools/branch_demo.py",
        "src/lawvm/tools/build_index_db.py",
        "src/lawvm/tools/certificate_bundle.py",
        "src/lawvm/tools/corpus_io.py",
        "src/lawvm/tools/export_fi_actors.py",
        "src/lawvm/tools/fi_he_branch.py",
        "src/lawvm/tools/fi_parse_attachments.py",
        "src/lawvm/tools/fi_parse_compare.py",
        "src/lawvm/tools/fi_parse_corpus.py",
        "src/lawvm/tools/fi_calibration.py",
        "src/lawvm/tools/fi_sweep.py",
        # Vision transcription-error calibration harness (fi-vision-read-calibration):
        # reading-order GT extraction + validation, render/aspect geometry (band/
        # single-line crops, pad-to-square, word-gap reflow-stack, overlap tiles), the
        # content-addressed read cache, the config-grid runner, and the multi-read
        # consensus + agreement-predicts-correctness measurement (stub reader, hermetic
        # — the live GPU sweep is operator-invoked, not CI).
        "src/lawvm/tools/fi_vision_read_calibration.py",
        # PDF→IR EXACTNESS eval harnesses (siblings of fi_parse_compare/corpus):
        # amendment-IR + HE proposed-effect IR compare/corpus drivers, the HE
        # payload-divergence adjudicator, the phase-3 appendix-structure tool, and
        # the Level-1 producer-usefulness A/B.
        "src/lawvm/tools/fi_amendment_ir_compare.py",
        "src/lawvm/tools/fi_amendment_ir_corpus.py",
        "src/lawvm/tools/fi_he_ir_compare.py",
        "src/lawvm/tools/fi_he_ir_corpus.py",
        "src/lawvm/tools/fi_he_payload_adjudicate.py",
        "src/lawvm/tools/fi_appendix_structure.py",
        # False-graduation canary (test_fi_verification_canary.py): drives the real
        # op-equivalence / vision-reconcile / MinerU-verify gates on seeded known errors.
        "src/lawvm/tools/fi_verification_canary.py",
        "src/lawvm/tools/fi_producer_compare.py",
        # Phase-3 vision witness layer (siblings of fi_appendix_structure): the
        # holistic sanity SCREEN (garble-scan + gestalt predicate, recall-critical,
        # never graduates) and the false-graduation validation harness/canary.
        "src/lawvm/tools/fi_appendix_vision_screen.py",
        "src/lawvm/tools/fi_appendix_vision_eval.py",
        "src/lawvm/tools/export_fi_he_branch_ops.py",
        "src/lawvm/tools/export_fi_he_corpus.py",
        "src/lawvm/tools/export_fi_inline_citations.py",
        "src/lawvm/tools/export_fi_interlinks.py",
        "src/lawvm/tools/export_fi_pools.py",
        "src/lawvm/tools/export_fi_preparatory_refs.py",
        "src/lawvm/tools/export_fi_refs.py",
        "src/lawvm/tools/export_fi_sections_text.py",
        "src/lawvm/tools/export_markdown_git.py",
        "src/lawvm/tools/export_parquet.py",
        "src/lawvm/tools/export_persistence.py",
        "src/lawvm/tools/export_transition_graph.py",
        "src/lawvm/tools/import_zip.py",
        "src/lawvm/tools/projection_freshness.py",
        "src/lawvm/tools/sync_finlex_latest.py",
        "src/lawvm/tools/tier2_state.py",
    ),
    ("new_zealand", "tools_cli_debug"): (
        "src/lawvm/tools/nz_anchor_manifest.py",
        "src/lawvm/tools/nz_bench.py",
        "src/lawvm/tools/nz_self_consistency.py",
    ),
    ("norway", "tools_cli_debug"): (
        "src/lawvm/tools/no_anchor_manifest.py",
        "src/lawvm/tools/no_bench.py",
        "src/lawvm/tools/no_blockers.py",
        "src/lawvm/tools/no_commencement_backfill.py",
        "src/lawvm/tools/no_commencement_candidates.py",
        "src/lawvm/tools/no_commencement_evidence_plan.py",
        "src/lawvm/tools/no_commencement_phrases.py",
        "src/lawvm/tools/no_commencement_report.py",
        "src/lawvm/tools/no_commencement_validate.py",
        "src/lawvm/tools/no_coverage.py",
        "src/lawvm/tools/no_debug.py",
        "src/lawvm/tools/no_divergence.py",
        "src/lawvm/tools/no_frontier.py",
        "src/lawvm/tools/no_impact.py",
        "src/lawvm/tools/no_index.py",
        "src/lawvm/tools/no_ingest.py",
        "src/lawvm/tools/no_inventory.py",
        "src/lawvm/tools/no_law.py",
        "src/lawvm/tools/no_missing_base.py",
        "src/lawvm/tools/no_op_trace.py",
        "src/lawvm/tools/no_progress.py",
        "src/lawvm/tools/no_replay.py",
        "src/lawvm/tools/no_source.py",
        "src/lawvm/tools/no_source_excerpt.py",
        "src/lawvm/tools/no_statsrad.py",
        "src/lawvm/tools/no_verify.py",
        "src/lawvm/tools/no_verify_partition.py",
        "src/lawvm/tools/no_verify_scan.py",
        "src/lawvm/tools/no_verify_workqueue.py",
        "src/lawvm/tools/no_workqueue.py",
    ),
    ("sweden", "tools_cli_debug"): (
        "src/lawvm/tools/se_anchor_manifest.py",
        "src/lawvm/tools/se_bench.py",
        "src/lawvm/tools/sweden.py",
    ),
    ("tools_audit_blame",): ("src/lawvm/tools/blame.py",),
    ("tools_audit_release",): (
        "src/lawvm/tools/audit.py",
        "src/lawvm/tools/audit_channels.py",
        "src/lawvm/tools/audit_trail.py",
        "src/lawvm/tools/failures.py",
        "src/lawvm/tools/freshness.py",
        "src/lawvm/tools/invariant_harvest.py",
        "src/lawvm/tools/recall_audit.py",
        "src/lawvm/tools/step_attribution.py",
        "src/lawvm/tools/structural_grep.py",
    ),
    ("tools_audit_restructure",): ("src/lawvm/tools/structural_review.py",),
    ("tools_bench_inventory",): (
        "src/lawvm/tools/bench.py",
        "src/lawvm/tools/bench_curate.py",
        "src/lawvm/tools/bench_diagnostic_tiers.py",
        "src/lawvm/tools/bench_hydrate.py",
        "src/lawvm/tools/bench_regression_guard.py",
        "src/lawvm/tools/bench_report.py",
        "src/lawvm/tools/bench_triage.py",
        "src/lawvm/tools/parse_bench.py",
        "src/lawvm/tools/refs_bench.py",
        "src/lawvm/tools/seeded_fault_study.py",
    ),
    ("tools_runtime_io",): (
        "src/lawvm/tools/_parallel_corpus.py",
        "src/lawvm/tools/_worker_pool.py",
        "src/lawvm/tools/corpus_sweep.py",
        "src/lawvm/tools/rebuild_indexes.py",
    ),
    ("uk", "tools_cli_debug"): (
        "src/lawvm/tools/uk_acquire.py",
        "src/lawvm/tools/uk_anchor_manifest.py",
        "src/lawvm/tools/uk_bench.py",
        "src/lawvm/tools/uk_branch_demo.py",
        "src/lawvm/tools/uk_branch_import.py",
        "src/lawvm/tools/uk_candidates.py",
        "src/lawvm/tools/uk_claim_templates.py",
        "src/lawvm/tools/uk_corpus.py",
        "src/lawvm/tools/uk_cross_statute_graph.py",
        "src/lawvm/tools/uk_effect.py",
        "src/lawvm/tools/uk_effects.py",
        "src/lawvm/tools/uk_eids.py",
        "src/lawvm/tools/uk_live_targets.py",
        "src/lawvm/tools/uk_manual_frontier.py",
        "src/lawvm/tools/uk_misses.py",
        "src/lawvm/tools/uk_oracle_check.py",
        "src/lawvm/tools/uk_replay.py",
        "src/lawvm/tools/uk_replay_regime.py",
        "src/lawvm/tools/uk_self_consistency.py",
        "src/lawvm/tools/uk_semantic_claims.py",
        "src/lawvm/tools/uk_structural_review.py",
    ),
    ("us_federal",): (
        "src/lawvm/tools/spec_ledger_us_catalog.py",
    ),
    ("us_federal", "tools_cli_debug"): (
        "src/lawvm/tools/us_anchor_manifest.py",
        "src/lawvm/tools/us_classification.py",
        "src/lawvm/tools/us_self_consistency.py",
    ),
}
_GENERAL_TOOL_SOURCE_PATHS = (
    "src/lawvm/tools/__init__.py",
    "src/lawvm/tools/_cli_duckdb.py",
    "src/lawvm/tools/_cli_output.py",
    "src/lawvm/tools/_compile_report_record.py",
    "src/lawvm/tools/_section_debug.py",
    "src/lawvm/tools/actors_query.py",
    "src/lawvm/tools/all_pit_driver.py",
    "src/lawvm/tools/bilingual.py",
    "src/lawvm/tools/bill_analysis.py",
    "src/lawvm/tools/bill_counterfactual_effects.py",
    "src/lawvm/tools/bisect.py",
    "src/lawvm/tools/bisect_section.py",
    "src/lawvm/tools/bisect_support.py",
    "src/lawvm/tools/bitemporal_refs.py",
    "src/lawvm/tools/build.py",
    "src/lawvm/tools/build_statute_name_registry.py",
    "src/lawvm/tools/capture.py",
    "src/lawvm/tools/capture_models.py",
    "src/lawvm/tools/census.py",
    "src/lawvm/tools/cite.py",
    "src/lawvm/tools/classify.py",
    "src/lawvm/tools/classify_result.py",
    "src/lawvm/tools/cli.py",
    "src/lawvm/tools/cmd_claim.py",
    "src/lawvm/tools/cmd_follow_refs.py",
    "src/lawvm/tools/cmd_migrate_manual_claims.py",
    "src/lawvm/tools/cmd_pit_diff.py",
    "src/lawvm/tools/cmd_pit_timeline.py",
    "src/lawvm/tools/cmd_propose_claims.py",
    "src/lawvm/tools/cmd_recipes.py",
    "src/lawvm/tools/cmd_telos.py",
    "src/lawvm/tools/cmd_topic.py",
    "src/lawvm/tools/cmd_validate_claims.py",
    "src/lawvm/tools/consistency.py",
    "src/lawvm/tools/corpus_surface_graph.py",
    "src/lawvm/tools/corrigendum.py",
    "src/lawvm/tools/coverage.py",
    "src/lawvm/tools/coverage_totality_report.py",
    "src/lawvm/tools/cross_reference_integrity_report.py",
    "src/lawvm/tools/dangling_references.py",
    "src/lawvm/tools/dangling_temporal_cause.py",
    "src/lawvm/tools/delegate.py",
    "src/lawvm/tools/destructive_repair_ledger.py",
    "src/lawvm/tools/diagnose_phase.py",
    "src/lawvm/tools/diff.py",
    "src/lawvm/tools/disagreement.py",
    "src/lawvm/tools/divergence_core.py",
    "src/lawvm/tools/divergence_heuristics.py",
    "src/lawvm/tools/drift.py",
    "src/lawvm/tools/dump.py",
    "src/lawvm/tools/editorial_hygiene.py",
    "src/lawvm/tools/explain.py",
    "src/lawvm/tools/export.py",
    "src/lawvm/tools/faults.py",
    "src/lawvm/tools/frontier.py",
    "src/lawvm/tools/gold.py",
    "src/lawvm/tools/graph_query.py",
    "src/lawvm/tools/hyperlinks.py",
    "src/lawvm/tools/inline_citations_query.py",
    "src/lawvm/tools/inspect_amendment.py",
    "src/lawvm/tools/invariant_bisect.py",
    "src/lawvm/tools/lower_audit.py",
    "src/lawvm/tools/open_law.py",
    "src/lawvm/tools/ops.py",
    "src/lawvm/tools/oracle_check.py",
    "src/lawvm/tools/oracle_classify.py",
    "src/lawvm/tools/oracle_context.py",
    "src/lawvm/tools/oracle_defect_confirmation.py",
    "src/lawvm/tools/oracle_defect_confirmation_cli.py",
    "src/lawvm/tools/oracle_text.py",
    "src/lawvm/tools/parse_characterize.py",
    "src/lawvm/tools/parse_johto.py",
    "src/lawvm/tools/peg_audit.py",
    "src/lawvm/tools/peg_rules.py",
    "src/lawvm/tools/phase_witness.py",
    "src/lawvm/tools/pit_projection.py",
    "src/lawvm/tools/pools_query.py",
    "src/lawvm/tools/preparatory_refs_query.py",
    "src/lawvm/tools/product_debug.py",
    "src/lawvm/tools/profile.py",
    "src/lawvm/tools/provenance.py",
    "src/lawvm/tools/provenance_totality_report.py",
    "src/lawvm/tools/provision_state.py",
    "src/lawvm/tools/query.py",
    "src/lawvm/tools/read_provision.py",
    "src/lawvm/tools/reconcile.py",
    "src/lawvm/tools/reconcile_sweep.py",
    "src/lawvm/tools/reference_integrity_demo_report.py",
    "src/lawvm/tools/refs_query.py",
    "src/lawvm/tools/replay_all.py",
    "src/lawvm/tools/replay_debug.py",
    "src/lawvm/tools/replay_inspect.py",
    "src/lawvm/tools/replay_mode_arg.py",
    "src/lawvm/tools/replay_payloads.py",
    "src/lawvm/tools/replay_plan.py",
    "src/lawvm/tools/report_models.py",
    "src/lawvm/tools/report_query.py",
    "src/lawvm/tools/residual_ledger.py",
    "src/lawvm/tools/resolution_miss_analysis.py",
    "src/lawvm/tools/scaffold.py",
    "src/lawvm/tools/section_keys.py",
    "src/lawvm/tools/self_consistency.py",
    "src/lawvm/tools/show.py",
    "src/lawvm/tools/simulate.py",
    "src/lawvm/tools/snapshot_debug.py",
    "src/lawvm/tools/solver_slot_assignment.py",
    "src/lawvm/tools/source_dump.py",
    "src/lawvm/tools/spec_authority.py",
    "src/lawvm/tools/spec_ledger.py",
    "src/lawvm/tools/spec_ledger_discovery.py",
    "src/lawvm/tools/spec_ledger_ee_catalog.py",
    "src/lawvm/tools/spec_ledger_eu_catalog.py",
    "src/lawvm/tools/spec_ledger_eu_catalog_meta.py",
    "src/lawvm/tools/spec_ledger_fi_catalog_meta.py",
    "src/lawvm/tools/spec_ledger_fi_catalog_supplement.py",
    "src/lawvm/tools/spec_ledger_glue.py",
    "src/lawvm/tools/spec_ledger_no_catalog.py",
    "src/lawvm/tools/spec_ledger_nz_catalog.py",
    "src/lawvm/tools/spec_ledger_report.py",
    "src/lawvm/tools/spec_ledger_se_catalog.py",
    "src/lawvm/tools/spec_ledger_uk_catalog.py",
    "src/lawvm/tools/spec_ledger_uk_catalog_meta.py",
    "src/lawvm/tools/spec_ledger_uk_catalog_supplement.py",
    "src/lawvm/tools/sql_query.py",
    "src/lawvm/tools/surface_graph.py",
    "src/lawvm/tools/surface_lints.py",
    "src/lawvm/tools/temporal_holdout.py",
    "src/lawvm/tools/timeline.py",
    "src/lawvm/tools/timeline_integrity.py",
    "src/lawvm/tools/trace_section.py",
    "src/lawvm/tools/transition_graph_interlinks.py",
    "src/lawvm/tools/transition_graph_jurisdictions.py",
    "src/lawvm/tools/transition_graph_overlays.py",
    "src/lawvm/tools/transition_graph_profile.py",
    "src/lawvm/tools/verify.py",
    "src/lawvm/tools/verify_chain.py",
    "src/lawvm/tools/verify_consistency.py",
    "src/lawvm/tools/version_drift.py",
)
TOOL_SOURCE_SHARD_PATHS: dict[str, tuple[str, ...]] = {
    path: shards
    for shards, paths in {
        **TOOL_SOURCE_SHARD_GROUPS,
        ("tools_cli_debug",): _GENERAL_TOOL_SOURCE_PATHS,
    }.items()
    for path in paths
}
TOOLING_BLOCKED_PREFIXES = (
    "scripts/",
    "src/lawvm/tools/",
)
SOURCE_SHARD_PATHS: dict[str, tuple[str, ...]] = {
    "tests/data/classifier_wrap_ratchet_baseline.json": ("core_ir_contracts",),
    "tests/data/ctsf_gate_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_ee_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_eu_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_no_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_nz_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_se_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_uk_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/ctsf_gate_us_residual_baseline.json": ("tools_ctsf_gate",),
    "tests/data/module_roles_baseline.json": ("core_ir_contracts",),
    "tests/data/regex_ratchet_baseline.json": ("core_ir_contracts",),
    "src/lawvm/core/ctsf_gate.py": ("tools_ctsf_gate",),
    # XP-06 parity is read-mostly audit/report code. It is not on the replay
    # execution path, so edits need the parity shard, not every frontend shard.
    "src/lawvm/core/cross_jurisdiction_parity.py": ("core_tree_apply",),
}
GLOBAL_CHANGE_PATHS = frozenset({"pyproject.toml", "uv.lock"})
DOCUMENTATION_PREFIXES = ("docs/", "notes/", "us/spec/")
# Process-isolated subprojects (own pyproject + heavy deps main CI never
# installs; e.g. subprojects/nemotron_parse). Their tests run in their own
# env (`uv run --project subprojects/<name> pytest`), so a change there has
# no bounded MAIN-repo shard impact. The main-side wire contract is pinned by
# main tests (e.g. test_fi_nemotron_client.py), which map via their own paths.
ISOLATED_SUBPROJECT_PREFIXES = ("subprojects/",)
TOP_LEVEL_DOCUMENTATION_PATHS = frozenset({
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "RELEASE_V0_1.md",
    "ROADMAP.md",
    "ROADMAP_V0_1.md",
    "ROADMAP_V1_0.md",
})

ALL_SHARDS = ("all",)


def _all_test_files() -> list[str]:
    flat = sorted(path.name for path in TEST_DIR.glob("test_*.py"))
    # Also include tests nested one level deep under named subdirectories
    # (e.g. tests/substrate/test_*.py).  These are returned as
    # "<subdir>/test_name.py" so that SHARD_PATTERNS can reference them with
    # the subdir prefix and run_shard resolves them via TEST_DIR / filename.
    sub: list[str] = []
    for subdir in sorted(TEST_DIR.iterdir()):
        if subdir.is_dir() and not subdir.name.startswith(".") and not subdir.name.startswith("_"):
            sub.extend(
                f"{subdir.name}/{path.name}"
                for path in sorted(subdir.glob("test_*.py"))
            )
    return flat + sub


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
    test_selector = (
        selector_path.removeprefix("tests/")
        if selector_path.startswith("tests/")
        else filename
    )

    def plan(shards: list[str], reason: str) -> dict[str, Any]:
        return {
            "path": raw_path,
            "shards": shards,
            "expanded_shards": _affected_shards_from_path_plans([{"shards": shards}]),
            "reason": reason,
        }

    def unknown(reason: str) -> dict[str, Any]:
        return {
            "path": raw_path,
            "shards": [],
            "expanded_shards": [],
            "reason": reason,
            "blocking": True,
            "fix": (
                "add an affected-shard mapping in scripts/test_shard.py, or run "
                "./scripts/ci.sh / ./scripts/ci.sh --shards 'frontends modules' explicitly"
            ),
        }

    if not path:
        return plan(
            list(ALL_SHARDS),
            "empty input path is not mapped to a bounded shard; run all affected shards",
        )
    if normalized in GLOBAL_CHANGE_PATHS:
        return plan(list(ALL_SHARDS), "global dependency change forces all affected shards")
    if normalized in TOOLING_SHARD_PATHS:
        shards = TOOLING_SHARD_PATHS[normalized]
        return plan(list(shards), f"known tooling path {normalized} maps to {', '.join(shards)}")
    if normalized in SOURCE_SHARD_PATHS:
        shards = SOURCE_SHARD_PATHS[normalized]
        return plan(list(shards), f"known source path {normalized} maps to {', '.join(shards)}")
    if normalized in TOOL_SOURCE_SHARD_PATHS:
        shards = TOOL_SOURCE_SHARD_PATHS[normalized]
        return plan(list(shards), f"known tool source path {normalized} maps to {', '.join(shards)}")
    if selector_path.startswith("tests/") and filename.startswith("test_") and filename.endswith(".py"):
        if filename in EXCLUDED_TESTS:
            return plan(
                list(ALL_SHARDS),
                f"excluded test: {EXCLUDED_TESTS[filename]}; run all affected shards",
            )
        matches = explicit_matches(test_selector)
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
    if normalized.startswith(DOCUMENTATION_PREFIXES) or normalized in TOP_LEVEL_DOCUMENTATION_PATHS:
        return plan([], "documentation path has no bounded pytest shard impact")
    if normalized.startswith(ISOLATED_SUBPROJECT_PREFIXES):
        return plan(
            [],
            "process-isolated subproject (own pyproject/env); no bounded main-repo shard impact",
        )
    if normalized.startswith(TOOLING_BLOCKED_PREFIXES):
        prefixes = ", ".join(TOOLING_BLOCKED_PREFIXES)
        return unknown(
            f"tooling path under {prefixes} has no explicit affected-shard mapping"
        )
    return unknown("unknown path is not mapped to a bounded shard")


def affected_path_plans(paths: list[str]) -> list[dict[str, Any]]:
    return [affected_path_plan(path) for path in paths]


def _blocking_affected_path_plans(path_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in path_plans if item.get("blocking") is True]


def _affected_shards_from_path_plans(path_plans: list[dict[str, Any]]) -> list[str]:
    affected: set[str] = set()
    for item in path_plans:
        affected.update(item["shards"])
    if "all" in affected:
        return ["all"]
    if not affected:
        return []
    return sorted(expand_shard_names(sorted(affected)))


def affected_shards(paths: list[str]) -> list[str]:
    """Map changed repo paths to a conservative bounded-test shard set."""

    if not paths:
        return ["all"]
    path_plans = affected_path_plans(paths)
    blocking = _blocking_affected_path_plans(path_plans)
    if blocking:
        joined = "; ".join(f"{item['path']}: {item['reason']}" for item in blocking)
        raise ValueError(f"cannot compute affected shards for unknown path(s): {joined}")
    return _affected_shards_from_path_plans(path_plans)


def affected_plan(paths: list[str]) -> dict[str, Any]:
    path_plans = affected_path_plans(paths)
    shards = _affected_shards_from_path_plans(path_plans)
    return {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": list(paths),
        "shards": shards,
        "blocking_paths": _blocking_affected_path_plans(path_plans),
        "paths": path_plans,
    }


def print_affected(paths: list[str], *, json_output: bool = False) -> int:
    plan = affected_plan(paths)
    blocking = plan["blocking_paths"]
    if json_output:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 2 if blocking else 0
    if blocking:
        print("Cannot compute --affected shard set for unknown path(s):", file=sys.stderr)
        for item in blocking:
            print(f"  {item['path']}: {item['reason']}", file=sys.stderr)
            print(f"    fix: {item['fix']}", file=sys.stderr)
        return 2
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
