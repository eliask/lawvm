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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast


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
    "estonia_sources": (
        "test_clause_ast_ee_validation.py",
        "test_ee_act_identity_registry.py",
        "test_ee_bench.py",
        "test_ee_compare_normalization.py",
        "test_ee_fetch_diagnostics.py",
        "test_ee_fetch.py",
        "test_ee_frontier.py",
        "test_ee_inspect_source.py",
        "test_ee_new_tools.py",
        "test_ee_pair_planning.py",
        "test_ee_reporting_tools.py",
        "test_ee_residual_inventory.py",
        "test_ee_source_adjudication.py",
        "test_ee_tool_promotions.py",
    ),
    "estonia_replay_semantics": (
        "test_ee_apply_semantics.py",
        "test_ee_instruction_waist.py",
        "test_ee_parser_normalization.py",
    ),
    "estonia_replay_logic": (
        "test_ee_replay_logic.py",
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
        "test_new_zealand_effect_candidates.py",
        "test_new_zealand_effect_preflight.py",
        "test_new_zealand_effect_readiness.py",
        "test_new_zealand_instruction_workqueue.py",
        "test_new_zealand_text_comparison.py",
    ),
    "new_zealand_reports": (
        "test_new_zealand_agreement.py",
        "test_new_zealand_benchmark.py",
        "test_new_zealand_evidence_pack.py",
        "test_new_zealand_operation_surface.py",
        "test_new_zealand_payload_surface.py",
    ),
    "sweden_fetch": (
        "test_sweden_fetch.py",
    ),
    "sweden_misc": (
        "test_sweden_grafter.py",
        "test_sweden_tools.py",
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
    "finland_sources": (
        "test_amendment_index.py",
        "test_audit_verified_finlex_yaml.py",
        "test_backfill_finlex_consolidated_versions.py",
        "test_build_publication_db.py",
        "test_finlex_*.py",
        "test_scan_absent_ajantasa.py",
        "test_scan_annotations.py",
        "test_source_dump.py",
        "test_transparent_store.py",
        "test_vts.py",
    ),
    "finland_parse_payload": (
        "test_body_*.py",
        "test_clause_ast_curated.py",
        "test_clause_patterns.py",
        "test_clause_surface.py",
        "test_fi_num_in_intro_recovery.py",
        "test_fi_profile_normalize.py",
        "test_fi_source_pathology_observations.py",
        "test_fi_xml_ir.py",
        "test_frontend_observations.py",
        "test_johtolause_api.py",
        "test_lower_*.py",
        "test_normalize_fi.py",
        "test_parse_clause.py",
        "test_payload_normalize.py",
        "test_payload_surface.py",
        "test_qualified_jolloin_renumber.py",
    ),
    "finland_replay_compile": (
        "test_fi_compile.py",
    ),
    "finland_replay_grafter": (
        "test_grafter_fallback.py",
    ),
    "finland_replay_products_core": (
        "test_replay_products.py",
    ),
    "finland_replay_products_support": (
        "test_replay_pipeline.py",
        "test_replay_revision.py",
        "test_session_regressions_2026_04.py",
    ),
    "finland_replay_rules": (
        "test_corrigendum_*.py",
        "test_fi_editorial_adjudication.py",
        "test_fi_item_number_display.py",
        "test_fi_tail_prose_absorb.py",
        "test_fi_unnumbered_peer_reparent.py",
        "test_finland_cross_refs.py",
        "test_finland_delegation.py",
        "test_finland_ontology.py",
        "test_finland_profile.py",
        "test_finland_rulebook.py",
        "test_finland_rulebook_cli.py",
        "test_finland_rulebook_export.py",
        "test_finland_rulebook_registries.py",
        "test_wrapup_preservation.py",
    ),
    "evidence_claims": (
        "test_evidence.py",
    ),
    "evidence_core": (
        "test_adjudication_evidence.py",
        "test_capture.py",
        "test_chain_completeness.py",
        "test_proof_algebra.py",
        "test_section_evidence_context.py",
        "test_section_invariant_evidence.py",
        "test_section_strict_lineage.py",
        "test_statute_proof_algebra.py",
        "test_strict_payload_confidence.py",
        "test_version_drift.py",
    ),
    "evidence_reports": (
        "test_explain_facade.py",
        "test_strict_report.py",
    ),
    "properties_timeline": (
        "test_timeline_properties.py",
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
        "test_tree_ops_properties.py",
        "test_z3_proofs.py",
    ),
    "core_ir_contracts": (
        "test_address_parse.py",
        "test_admissible_binding.py",
        "test_authority.py",
        "test_branch_projection.py",
        "test_canonical_intent_kinds.py",
        "test_clause_ast.py",
        "test_coordination_parser.py",
        "test_core_unit_registry_contracts.py",
        "test_effect_lowering.py",
        "test_filter_result.py",
        "test_intent_compat.py",
        "test_ir_*.py",
        "test_meta_parse.py",
        "test_metadata.py",
        "test_roman.py",
        "test_scope.py",
        "test_section_keys.py",
        "test_shared_contracts.py",
        "test_source_lane.py",
        "test_source_path_index.py",
        "test_source_version_window.py",
        "test_span_anchor.py",
        "test_statute_facets.py",
        "test_target_resolution.py",
        "test_target_scope.py",
        "test_unit_registry.py",
    ),
    "core_tree_apply": (
        "test_annotations_views.py",
        "test_apply.py",
        "test_chapter_seed.py",
        "test_constraints.py",
        "test_destructive_repair_ledger.py",
        "test_invariant_detectors.py",
        "test_law_level_text_patch.py",
        "test_legal_operation_text_patch.py",
        "test_merge.py",
        "test_migration_ledger.py",
        "test_mutation_boundary.py",
        "test_mutation_events.py",
        "test_mutation_gaps.py",
        "test_normalize_structure.py",
        "test_occupancy.py",
        "test_opaque_marker_boundary.py",
        "test_text_amend.py",
        "test_tree_ops_ambiguity.py",
    ),
    "core_compile_projection": (
        "test_compile_facade.py",
        "test_compile_records.py",
        "test_compile_result.py",
        "test_compile_views.py",
        "test_graph_build_contract.py",
        "test_pipeline_capture.py",
        "test_projection_completeness.py",
        "test_verify_chain.py",
    ),
    "core_materialization_invariants": (
        "test_materialization_invariants.py",
    ),
    "core_replay_timeline": (
        "test_replay_lints.py",
        "test_replay_metamorphic.py",
        "test_replay_small_model.py",
        "test_timeline.py",
        "test_timeline_invariants.py",
        "test_timeline_selection_contracts.py",
    ),
    "core_surface_semantic": (
        "test_comparison_normalization.py",
        "test_composite_interaction_reference_model.py",
        "test_core_graph.py",
        "test_diagnostic_records.py",
        "test_elaboration_context_contracts.py",
        "test_parse_witness.py",
        "test_phase_result_*.py",
        "test_semantic_*.py",
        "test_solver_slot_assignment.py",
        "test_surface_*.py",
        "test_table_*.py",
        "test_temporal*.py",
        "test_token*.py",
    ),
    "tools_cli_debug_hotspot": (
        "test_cli_debug_tools.py",
    ),
    "tools_cli_oracle": (
        "test_oracle_check.py",
    ),
    "tools_cli_debug": (
        "test_check_consistency.py",
        "test_diagnose_phase.py",
        "test_diff.py",
        "test_dump.py",
        "test_freshness_tool.py",
        "test_replay_cli_contract.py",
        "test_verify_facade_execution.py",
        "test_verify_observations.py",
    ),
    "tools_runtime_io": (
        "test_acquisition.py",
        "test_consolidated_artifacts.py",
        "test_branch_demo.py",
        "test_export_sql.py",
        "test_graph_export.py",
        "test_import_zip.py",
        "test_worker_pool.py",
    ),
    "tools_audit_restructure": (
        "test_restructure_plan.py",
    ),
    "tools_audit_blame": (
        "test_blame.py",
    ),
    "tools_audit_release": (
        "test_audit.py",
        "test_audit_scripts.py",
        "test_ci_shards.py",
        "test_failures.py",
        "test_finding_registry.py",
        "test_helpers.py",
        "test_publication_guarantees.py",
        "test_release_docs.py",
        "test_source_normalize.py",
        "test_step_attribution.py",
        "test_structural_*.py",
    ),
    "tools_bench_inventory": (
        "test_bench_report.py",
        "test_bench.py",
        "test_bench_comparable.py",
        "test_bench_curate.py",
        "test_corpus.py",
        "test_divergence_heuristics.py",
        "test_frontier.py",
        "test_gold_tool.py",
        "test_metadata_fi.py",
        "test_parser_smell_inventory.py",
        "test_peg_*.py",
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
    ("src/lawvm/us_federal/", ("starter",)),
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
    print(f"=== shard {shard}: {len(filenames)} files ===", flush=True)
    started = time.perf_counter()
    exit_code = subprocess.call(cmd, cwd=REPO_ROOT)
    elapsed = time.perf_counter() - started
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
