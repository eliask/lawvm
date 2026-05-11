#!/usr/bin/env python3
"""Run named LawVM pytest shards.

This is an iteration/matrix helper.  The canonical local gate remains
``scripts/ci.sh``.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = REPO_ROOT / "tests"

EXCLUDED_TESTS = {
    "test_citation_routing.py": "large skip-heavy/gold-style corpus route inventory",
    "test_pipeline_gold.py": "gold corpus suite; intentionally outside bounded non-network CI",
}

SHARD_PATTERNS: dict[str, tuple[str, ...]] = {
    "boundary": (
        "test_conformance.py",
    ),
    "estonia": (
        "test_ee_*.py",
        "test_clause_ast_ee_validation.py",
    ),
    "norway": (
        "test_no_*.py",
        "test_norway_*.py",
    ),
    "sweden": (
        "test_sweden_*.py",
    ),
    "uk": (
        "test_uk_*.py",
        "test_acquire_uk_corpus.py",
    ),
    "eu": (
        "test_eu_*.py",
    ),
    "starter": (
        "test_jurisdiction_starter_*.py",
        "test_open_law_frontend.py",
        "test_scaffold_tool.py",
    ),
    "finland": (
        "test_amendment_index.py",
        "test_audit_verified_finlex_yaml.py",
        "test_backfill_finlex_consolidated_versions.py",
        "test_body_*.py",
        "test_build_publication_db.py",
        "test_clause_ast_curated.py",
        "test_clause_patterns.py",
        "test_clause_surface.py",
        "test_corrigendum_*.py",
        "test_fi_*.py",
        "test_finland_*.py",
        "test_finlex_*.py",
        "test_frontend_observations.py",
        "test_grafter_fallback.py",
        "test_johtolause_api.py",
        "test_lower_*.py",
        "test_normalize_fi.py",
        "test_parse_clause.py",
        "test_payload_normalize.py",
        "test_payload_surface.py",
        "test_qualified_jolloin_renumber.py",
        "test_replay_pipeline.py",
        "test_replay_products.py",
        "test_replay_revision.py",
        "test_scan_absent_ajantasa.py",
        "test_scan_annotations.py",
        "test_session_regressions_2026_04.py",
        "test_source_dump.py",
        "test_transparent_store.py",
        "test_vts.py",
        "test_wrapup_preservation.py",
    ),
    "evidence": (
        "test_capture.py",
        "test_chain_completeness.py",
        "test_evidence.py",
        "test_explain_facade.py",
        "test_proof_algebra.py",
        "test_section_evidence_context.py",
        "test_section_invariant_evidence.py",
        "test_section_strict_lineage.py",
        "test_statute_proof_algebra.py",
        "test_strict_payload_confidence.py",
        "test_strict_report.py",
        "test_version_drift.py",
    ),
    "properties": (
        "test_apply_properties.py",
        "test_crosshair_kernels.py",
        "test_decomposition.py",
        "test_exhaustive_enumeration.py",
        "test_kernel_properties.py",
        "test_merge_properties.py",
        "test_mutmut_kills.py",
        "test_payload_normalize_properties.py",
        "test_replay_stateful_properties.py",
        "test_stateful_properties.py",
        "test_timeline_properties.py",
        "test_tree_ops_properties.py",
        "test_z3_proofs.py",
    ),
    "core": (
        "test_address_parse.py",
        "test_admissible_binding.py",
        "test_annotations_views.py",
        "test_apply.py",
        "test_canonical_intent_kinds.py",
        "test_chapter_seed.py",
        "test_clause_ast.py",
        "test_compile_facade.py",
        "test_compile_result.py",
        "test_compile_views.py",
        "test_composite_interaction_reference_model.py",
        "test_constraints.py",
        "test_coordination_parser.py",
        "test_core_*.py",
        "test_destructive_repair_ledger.py",
        "test_effect_lowering.py",
        "test_elaboration_context_contracts.py",
        "test_graph_build_contract.py",
        "test_intent_compat.py",
        "test_ir_*.py",
        "test_law_level_text_patch.py",
        "test_legal_operation_text_patch.py",
        "test_materialization_invariants.py",
        "test_merge.py",
        "test_meta_parse.py",
        "test_metadata.py",
        "test_migration_ledger.py",
        "test_mutation_gaps.py",
        "test_normalize_structure.py",
        "test_occupancy.py",
        "test_opaque_marker_boundary.py",
        "test_parse_witness.py",
        "test_phase_result_*.py",
        "test_pipeline_capture.py",
        "test_projection_completeness.py",
        "test_replay_lints.py",
        "test_replay_metamorphic.py",
        "test_replay_small_model.py",
        "test_roman.py",
        "test_scope.py",
        "test_section_keys.py",
        "test_semantic_*.py",
        "test_shared_contracts.py",
        "test_solver_slot_assignment.py",
        "test_span_anchor.py",
        "test_statute_facets.py",
        "test_surface_*.py",
        "test_table_*.py",
        "test_target_scope.py",
        "test_temporal*.py",
        "test_text_amend.py",
        "test_timeline.py",
        "test_timeline_invariants.py",
        "test_token*.py",
        "test_tree_ops_ambiguity.py",
        "test_unit_registry.py",
        "test_verify_chain.py",
    ),
    "tools": (
        "test_acquisition.py",
        "test_audit.py",
        "test_audit_scripts.py",
        "test_bench.py",
        "test_bench_comparable.py",
        "test_bench_curate.py",
        "test_blame.py",
        "test_check_consistency.py",
        "test_ci_shards.py",
        "test_cli_debug_tools.py",
        "test_consolidated_artifacts.py",
        "test_corpus.py",
        "test_diagnose_phase.py",
        "test_diff.py",
        "test_divergence_heuristics.py",
        "test_dump.py",
        "test_export_sql.py",
        "test_failures.py",
        "test_finding_registry.py",
        "test_freshness_tool.py",
        "test_frontier.py",
        "test_gold_tool.py",
        "test_helpers.py",
        "test_import_zip.py",
        "test_metadata_fi.py",
        "test_oracle_check.py",
        "test_parser_smell_inventory.py",
        "test_peg_*.py",
        "test_publication_guarantees.py",
        "test_release_docs.py",
        "test_replay_adjudication_inventory.py",
        "test_replay_cli_contract.py",
        "test_replay_debt_inventory.py",
        "test_report_query.py",
        "test_residual_ledger.py",
        "test_restructure_plan.py",
        "test_source_normalize.py",
        "test_step_attribution.py",
        "test_structural_*.py",
        "test_verify_facade_execution.py",
        "test_verify_observations.py",
        "test_worker_pool.py",
    ),
}


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


def run_shard(shard: str, *, pytest_args: list[str]) -> int:
    assignments = shard_assignments()
    if shard == "all":
        filenames = [
            filename
            for names in assignments.values()
            for filename in names
        ]
        filenames = sorted(filenames)
    else:
        if shard not in assignments:
            choices = ", ".join(["all", *sorted(assignments)])
            print(f"Unknown shard {shard!r}. Choices: {choices}", file=sys.stderr)
            return 2
        filenames = assignments[shard]
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
    print(f"=== shard {shard}: {len(filenames)} files ===", flush=True)
    return subprocess.call(cmd, cwd=REPO_ROOT)


def list_shards() -> int:
    assignments = shard_assignments()
    print("all")
    for shard in sorted(assignments):
        print(shard)
    return 0


def list_files(shard: str) -> int:
    assignments = shard_assignments()
    if shard == "all":
        filenames = sorted(filename for names in assignments.values() for filename in names)
    else:
        filenames = assignments.get(shard)
        if filenames is None:
            print(f"Unknown shard {shard!r}", file=sys.stderr)
            return 2
    for filename in filenames:
        print(f"tests/{filename}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    list_files_parser = subparsers.add_parser("files")
    list_files_parser.add_argument("shard")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("shard")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate()
    if args.command == "list":
        return list_shards()
    if args.command == "files":
        return list_files(args.shard)
    if args.command == "run":
        return run_shard(args.shard, pytest_args=args.pytest_args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
