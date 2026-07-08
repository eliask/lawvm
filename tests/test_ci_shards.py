from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest


def _load_test_shard_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "test_shard.py"
    spec = importlib.util.spec_from_file_location("lawvm_test_shard_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ci_sharded_default_bounded_shards() -> list[str]:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ci_sharded.sh"
    groups_match = re.search(
        r'^DEFAULT_SHARD_GROUPS="([^"]+)"$',
        script_path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert groups_match is not None
    module = _load_test_shard_module()
    return module.expand_shard_names(groups_match.group(1).split())


def _ci_sharded_static_check_paths() -> list[str]:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ci_sharded.sh"
    script = script_path.read_text(encoding="utf-8")
    match = re.search(
        r"^STATIC_CHECK_PATHS=\(\n(?P<body>.*?)\n\)$",
        script,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return [
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip()
    ]


def _without_path_expanded_shards(plan: dict[str, object]) -> dict[str, object]:
    stripped = dict(plan)
    if stripped.get("blocking_paths") == []:
        stripped.pop("blocking_paths")
    elif "blocking_paths" in stripped:
        blocking = cast(list[dict[str, Any]], stripped["blocking_paths"])
        stripped["blocking_paths"] = [
            {key: value for key, value in item.items() if key != "expanded_shards"}
            for item in blocking
        ]
    paths = cast(list[dict[str, Any]], plan["paths"])
    stripped["paths"] = [
        {key: value for key, value in item.items() if key != "expanded_shards"}
        for item in paths
    ]
    return stripped


CORE_EXECUTION_SHARDS_SORTED = [
    "core_compile_projection",
    "core_discipline_gates",
    "core_ir_contracts",
    "core_materialization_invariants",
    "core_replay_timeline",
    "core_surface_semantic",
    "core_tree_apply",
]

ESTONIA_EXECUTION_SHARDS = [
    "estonia_sources",
    "estonia_replay_semantics",
    "estonia_replay_logic",
]

FINLAND_EXECUTION_SHARDS = [
    "finland_sources",
    "finland_parse_payload",
    "finland_replay_compile",
    "finland_replay_grafter",
    "finland_replay_products_core",
    "finland_replay_products_support",
    "finland_replay_rules",
]

FINLAND_EXECUTION_SHARDS_SORTED = sorted(FINLAND_EXECUTION_SHARDS)

EVIDENCE_EXECUTION_SHARDS = [
    "evidence_claims",
    "evidence_core",
    "evidence_reports",
]

SWEDEN_EXECUTION_SHARDS = [
    "sweden_fetch",
    "sweden_misc",
]

TOOLS_EXECUTION_SHARDS_SORTED = [
    "tools_audit_blame",
    "tools_audit_release",
    "tools_audit_restructure",
    "tools_bench_inventory",
    "tools_cli_debug",
    "tools_cli_debug_hotspot",
    "tools_cli_oracle",
    "tools_runtime_io",
]


def test_test_shard_validate_is_clean() -> None:
    module = _load_test_shard_module()

    assert module.validate() == 0


def test_test_shard_assigns_every_bounded_file_once() -> None:
    module = _load_test_shard_module()
    assignments = module.shard_assignments()
    assigned = [
        filename
        for filenames in assignments.values()
        for filename in filenames
    ]
    expected = sorted(set(module._all_test_files()) - set(module.EXCLUDED_TESTS))

    assert sorted(assigned) == expected
    assert len(assigned) == len(set(assigned))


def test_test_shard_validate_rejects_unassigned_tests(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_test_shard_module()
    real_files = module._all_test_files()
    monkeypatch.setattr(
        module,
        "_all_test_files",
        lambda: [*real_files, "test_new_surface.py"],
    )

    assert module.validate() == 1
    captured = capsys.readouterr()
    assert "Tests not assigned to an explicit shard" in captured.err
    assert "test_new_surface.py" in captured.err


def test_test_shard_keeps_known_expensive_files_explicitly_excluded() -> None:
    module = _load_test_shard_module()

    assert module.EXCLUDED_TESTS == {
        "test_fi_citation_routing.py": "large skip-heavy/gold-style corpus route inventory",
        "test_fi_pipeline_gold.py": "gold corpus suite; intentionally outside bounded non-network CI",
    }


def test_test_shard_plan_is_jsonable_and_filterable() -> None:
    module = _load_test_shard_module()

    plan = module.shard_plan("norway")

    assert plan["kind"] == "lawvm_pytest_shard_plan"
    assert plan["selected"] == "norway"
    assert plan["shards"] == [
        {
            "name": "norway",
            "patterns": list(module.SHARD_PATTERNS["norway"]),
            "files": [f"tests/{filename}" for filename in module.shard_assignments()["norway"]],
            "file_count": len(module.shard_assignments()["norway"]),
        }
    ]
    assert plan["excluded_tests"] == [
        {
            "file": "tests/test_fi_citation_routing.py",
            "reason": "large skip-heavy/gold-style corpus route inventory",
        },
        {
            "file": "tests/test_fi_pipeline_gold.py",
            "reason": "gold corpus suite; intentionally outside bounded non-network CI",
        },
    ]


def test_test_shard_named_groups_expand_to_stable_shards() -> None:
    module = _load_test_shard_module()

    assert module.expand_shard_names(["frontends"]) == [
        *ESTONIA_EXECUTION_SHARDS,
        "eu",
        *FINLAND_EXECUTION_SHARDS,
        "new_zealand_sources",
        "new_zealand_effects",
        "new_zealand_reports",
        "norway",
        "starter",
        *SWEDEN_EXECUTION_SHARDS,
        "uk",
        "us_federal",
    ]
    assert module.expand_shard_names(["frontends", "modules", "finland"]) == [
        *ESTONIA_EXECUTION_SHARDS,
        "eu",
        *FINLAND_EXECUTION_SHARDS,
        "new_zealand_sources",
        "new_zealand_effects",
        "new_zealand_reports",
        "norway",
        "starter",
        *SWEDEN_EXECUTION_SHARDS,
        "uk",
        "us_federal",
        "core_discipline_gates",
        "core_ir_contracts",
        "core_tree_apply",
        "core_compile_projection",
        "core_materialization_invariants",
        "core_replay_timeline",
        "core_surface_semantic",
        *EVIDENCE_EXECUTION_SHARDS,
        "properties",
        "properties_timeline",
        "substrate",
        "tools_ctsf_gate",
        "tools_cli_debug_hotspot",
        "tools_cli_oracle",
        "tools_cli_debug",
        "tools_runtime_io",
        "tools_audit_restructure",
        "tools_audit_blame",
        "tools_audit_release",
        "tools_bench_inventory",
    ]


def test_ci_default_bounded_shards_cover_frontends_and_modules() -> None:
    module = _load_test_shard_module()
    default_shards = _ci_sharded_default_bounded_shards()
    expected_default_shards = sorted({
        *module.expand_shard_names(["frontends"]),
        *module.expand_shard_names(["modules"]),
    })

    assert sorted(default_shards) == expected_default_shards
    assert {
        "new_zealand_sources",
        "new_zealand_effects",
        "new_zealand_reports",
        "us_federal",
    } <= set(default_shards)


def test_ci_static_checks_cover_uk_acquisition_scripts() -> None:
    static_paths = set(_ci_sharded_static_check_paths())

    # The core trees are always statically checked.
    assert {"src/lawvm/", "tests/"} <= static_paths

    # The UK acquisition scripts (and any other script) must be COVERED by some
    # static-check path — either listed literally or by a directory prefix. #225
    # replaced the drift-prone per-script allowlist with the ``scripts/`` glob;
    # this assertion holds for either form and encodes the real intent (the
    # scripts are ruff+ty-checked), not the literal path list.
    def _covered(path: str) -> bool:
        return any(
            path == p or (p.endswith("/") and path.startswith(p))
            for p in static_paths
        )

    for script in (
        "scripts/test_shard.py",
        "scripts/fetch_uk_affecting_acts.py",
        "scripts/uk_fetch_affecting_acts.py",
        "scripts/uk_fetch_effects.py",
        "scripts/uk_inspect_metadata_effects.py",
    ):
        assert _covered(script), f"{script} is not statically checked"


def test_test_shard_new_zealand_group_expands_to_subshards() -> None:
    module = _load_test_shard_module()

    assert module.expand_shard_names(["new_zealand"]) == [
        "new_zealand_sources",
        "new_zealand_effects",
        "new_zealand_reports",
    ]
    assert module.affected_shards(["tests/test_new_zealand_acquisition.py"]) == [
        "new_zealand_sources"
    ]


def test_test_shard_evidence_group_expands_to_subshards() -> None:
    module = _load_test_shard_module()

    assert module.expand_shard_names(["evidence"]) == EVIDENCE_EXECUTION_SHARDS
    assert module.affected_shards(["tests/test_evidence.py"]) == ["evidence_claims"]
    assert module.affected_shards(["tests/test_fi_explain_facade.py"]) == ["evidence_reports"]


def test_test_shard_group_plan_is_jsonable() -> None:
    module = _load_test_shard_module()

    plan = module.shard_plan("modules")

    assert plan["kind"] == "lawvm_pytest_shard_plan"
    assert plan["selected"] == "modules"
    assert [item["name"] for item in plan["shards"]] == [
        "core_discipline_gates",
        "core_ir_contracts",
        "core_tree_apply",
        "core_compile_projection",
        "core_materialization_invariants",
        "core_replay_timeline",
        "core_surface_semantic",
        *EVIDENCE_EXECUTION_SHARDS,
        "properties",
        "properties_timeline",
        "substrate",
        "tools_ctsf_gate",
        "tools_cli_debug_hotspot",
        "tools_cli_oracle",
        "tools_cli_debug",
        "tools_runtime_io",
        "tools_audit_restructure",
        "tools_audit_blame",
        "tools_audit_release",
        "tools_bench_inventory",
    ]
    json.dumps(plan)


def test_test_shard_timing_record_is_jsonable() -> None:
    module = _load_test_shard_module()

    record = module.shard_timing_record(
        shard="finland",
        file_count=50,
        elapsed_seconds=123.4567,
        exit_code=0,
    )

    assert record == {
        "kind": "lawvm_pytest_shard_timing",
        "shard": "finland",
        "file_count": 50,
        "elapsed_seconds": 123.457,
        "exit_code": 0,
        "status": "passed",
    }
    json.dumps(record)


def test_test_shard_timing_record_can_include_run_id() -> None:
    module = _load_test_shard_module()

    record = module.shard_timing_record(
        shard="uk",
        file_count=13,
        elapsed_seconds=2.5,
        exit_code=0,
        run_id="local-full-1",
    )

    assert record["run_id"] == "local-full-1"
    json.dumps(record)


def test_test_shard_appends_timing_jsonl(tmp_path: Path) -> None:
    module = _load_test_shard_module()
    out = tmp_path / "nested" / "timings.jsonl"
    record = module.shard_timing_record(
        shard="tools",
        file_count=42,
        elapsed_seconds=1.0,
        exit_code=1,
    )

    module.append_shard_timing_record(out, record)

    assert json.loads(out.read_text(encoding="utf-8")) == {
        "kind": "lawvm_pytest_shard_timing",
        "shard": "tools",
        "file_count": 42,
        "elapsed_seconds": 1.0,
        "exit_code": 1,
        "status": "failed",
    }


def test_test_shard_timing_balance_report_uses_latest_shard_records(tmp_path: Path) -> None:
    module = _load_test_shard_module()
    timings = tmp_path / "timings.jsonl"
    for record in [
        module.shard_timing_record(shard="core", file_count=10, elapsed_seconds=20.0, exit_code=0),
        module.shard_timing_record(shard="tools", file_count=5, elapsed_seconds=5.0, exit_code=0),
        module.shard_timing_record(shard="core", file_count=11, elapsed_seconds=33.0, exit_code=0),
    ]:
        module.append_shard_timing_record(timings, record)

    report = module.shard_timing_balance_report(timings, imbalance_threshold=1.5)

    assert report["kind"] == "lawvm_pytest_shard_balance_report"
    assert report["run_id_filter"] is None
    assert report["record_count"] == 3
    assert report["valid_record_count"] == 3
    assert report["latest_shard_count"] == 2
    assert report["latest_run_ids"] == []
    assert report["total_elapsed_seconds"] == 38.0
    assert report["average_elapsed_seconds"] == 19.0
    assert report["imbalance_ratio"] == 6.6
    assert report["overweight_shards"] == ["core"]
    assert report["single_file_hotspots"] == []
    assert report["single_file_hotspot_profiles"] == []
    assert report["splittable_hotspots"] == ["core"]
    assert report["shards"] == [
        {
            "shard": "core",
            "elapsed_seconds": 33.0,
            "file_count": 11,
            "seconds_per_file": 3.0,
            "status": "passed",
        },
        {
            "shard": "tools",
            "elapsed_seconds": 5.0,
            "file_count": 5,
            "seconds_per_file": 1.0,
            "status": "passed",
        },
    ]
    assert report["invalid_records"] == []
    json.dumps(report)


def test_test_shard_timing_balance_report_records_invalid_jsonl(tmp_path: Path) -> None:
    module = _load_test_shard_module()
    timings = tmp_path / "timings.jsonl"
    timings.write_text(
        "\n".join([
            json.dumps(module.shard_timing_record(shard="tools", file_count=5, elapsed_seconds=5.0, exit_code=0)),
            "not-json",
            json.dumps({"kind": "lawvm_pytest_shard_timing", "shard": "core"}),
            json.dumps({
                "kind": "lawvm_pytest_shard_timing",
                "shard": "uk",
                "file_count": 13,
                "elapsed_seconds": 2.0,
                "run_id": 123,
            }),
        ]),
        encoding="utf-8",
    )

    report = module.shard_timing_balance_report(timings)

    assert report["valid_record_count"] == 1
    assert report["invalid_record_count"] == 3
    assert [item["kind"] for item in report["invalid_records"]] == [
        "lawvm_pytest_shard_timing_invalid",
        "lawvm_pytest_shard_timing_invalid",
        "lawvm_pytest_shard_timing_invalid",
    ]


def test_test_shard_timing_balance_report_filters_by_run_id(tmp_path: Path) -> None:
    module = _load_test_shard_module()
    timings = tmp_path / "timings.jsonl"
    for record in [
        module.shard_timing_record(
            shard="uk",
            file_count=13,
            elapsed_seconds=2.0,
            exit_code=0,
            run_id="full-a",
        ),
        module.shard_timing_record(
            shard="boundary",
            file_count=1,
            elapsed_seconds=1.0,
            exit_code=0,
            run_id="full-a",
        ),
        module.shard_timing_record(
            shard="uk",
            file_count=13,
            elapsed_seconds=99.0,
            exit_code=0,
            run_id="narrow-b",
        ),
    ]:
        module.append_shard_timing_record(timings, record)

    report = module.shard_timing_balance_report(timings, run_id="full-a")

    assert report["run_id_filter"] == "full-a"
    assert report["record_count"] == 3
    assert report["valid_record_count"] == 2
    assert report["latest_run_ids"] == ["full-a"]
    assert report["total_elapsed_seconds"] == 3.0
    assert [row["shard"] for row in report["shards"]] == ["uk", "boundary"]


def test_test_shard_timing_balance_report_identifies_single_file_hotspots(tmp_path: Path) -> None:
    module = _load_test_shard_module()
    timings = tmp_path / "timings.jsonl"
    for record in [
        module.shard_timing_record(shard="single_hotspot", file_count=1, elapsed_seconds=30.0, exit_code=0),
        module.shard_timing_record(shard="multi_hotspot", file_count=5, elapsed_seconds=25.0, exit_code=0),
        module.shard_timing_record(shard="small", file_count=4, elapsed_seconds=1.0, exit_code=0),
    ]:
        module.append_shard_timing_record(timings, record)

    report = module.shard_timing_balance_report(timings, imbalance_threshold=1.2)

    assert report["overweight_shards"] == ["single_hotspot", "multi_hotspot"]
    assert report["single_file_hotspots"] == ["single_hotspot"]
    assert report["single_file_hotspot_profiles"] == [
        {
            "shard": "single_hotspot",
            "file": None,
            "command": (
                "LAWVM_PYTEST_WORKERS=0 ./scripts/test_shard.sh run "
                "single_hotspot -- --durations=25"
            ),
        }
    ]
    assert report["splittable_hotspots"] == ["multi_hotspot"]


def test_test_shard_timing_balance_report_profiles_known_single_file_hotspot(
    tmp_path: Path,
) -> None:
    module = _load_test_shard_module()
    timings = tmp_path / "timings.jsonl"
    for record in [
        module.shard_timing_record(
            shard="tools_cli_debug_hotspot",
            file_count=1,
            elapsed_seconds=30.0,
            exit_code=0,
        ),
        module.shard_timing_record(
            shard="tools_cli_debug",
            file_count=8,
            elapsed_seconds=1.0,
            exit_code=0,
        ),
    ]:
        module.append_shard_timing_record(timings, record)

    report = module.shard_timing_balance_report(timings, imbalance_threshold=1.2)

    assert report["single_file_hotspot_profiles"] == [
        {
            "shard": "tools_cli_debug_hotspot",
            "file": "tests/test_fi_cli_debug_tools.py",
            "command": (
                "LAWVM_PYTEST_WORKERS=0 ./scripts/test_shard.sh run "
                "tools_cli_debug_hotspot -- --durations=25"
            ),
        }
    ]


def test_test_shard_timings_cli_outputs_json(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "test_shard.py"
    timings = tmp_path / "timings.jsonl"
    timings.write_text(
        json.dumps({
            "kind": "lawvm_pytest_shard_timing",
            "shard": "tools",
            "file_count": 5,
            "elapsed_seconds": 5.0,
            "exit_code": 0,
            "status": "passed",
            "run_id": "run-1",
        })
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(script), "timings", str(timings), "--json", "--run-id", "run-1"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["kind"] == "lawvm_pytest_shard_balance_report"
    assert payload["run_id_filter"] == "run-1"
    assert payload["latest_run_ids"] == ["run-1"]
    assert payload["shards"][0]["shard"] == "tools"


def test_test_shard_filters_files_when_pytest_selectors_are_supplied() -> None:
    module = _load_test_shard_module()

    selected, unknown = module.filter_filenames_by_pytest_selectors(
        ["test_a.py", "test_b.py"],
        ["--", "tests/test_b.py::test_specific", "-k", "specific"],
    )

    assert selected == ["test_b.py"]
    assert unknown == []


def test_test_shard_reports_selectors_outside_selected_shard() -> None:
    module = _load_test_shard_module()

    selected, unknown = module.filter_filenames_by_pytest_selectors(
        ["test_a.py"],
        ["tests/test_b.py"],
    )

    assert selected == []
    assert unknown == ["test_b.py"]


def test_test_shard_maps_changed_tests_to_explicit_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        [
            "tests/test_norway_replay.py",
            "tests/test_uk_replay_adjudications.py",
        ]
    ) == ["norway", "uk"]
    assert module.affected_shards(["tests/test_ci_shards.py::test_test_shard_validate_is_clean"]) == ["tools_audit_release"]
    assert module.affected_plan(["tests/test_ci_shards.py::test_test_shard_validate_is_clean"])["paths"] == [
        {
            "path": "tests/test_ci_shards.py::test_test_shard_validate_is_clean",
            "shards": ["tools_audit_release"],
            "expanded_shards": ["tools_audit_release"],
            "reason": "test file matches explicit shard pattern",
        }
    ]


def test_test_shard_maps_source_modules_to_frontend_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "scripts/ci.sh",
        ]
    ) == [
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_audit_release",
    ]
    assert module.affected_shards(["src/lawvm/tools/ee_replay.py"]) == [
        "estonia_replay_logic",
        "estonia_replay_semantics",
        "estonia_sources",
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/eu_replay.py"]) == [
        "eu",
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/finland_rulebook.py"]) == [
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_cli_debug",
    ]


def test_test_shard_maps_uk_living_notes_to_documentation_noop() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["notes/UK_REPLAY_LIVING_SPEC.md"]) == []
    assert _without_path_expanded_shards(
        module.affected_plan(["notes/UK_REPLAY_LIVING_SPEC.md"])
    ) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["notes/UK_REPLAY_LIVING_SPEC.md"],
        "shards": [],
        "paths": [
            {
                "path": "notes/UK_REPLAY_LIVING_SPEC.md",
                "shards": [],
                "reason": "documentation path has no bounded pytest shard impact",
            }
        ],
    }
    assert module.affected_shards(["src/lawvm/tools/sync_finlex_latest.py"]) == [
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_runtime_io",
    ]
    assert module.affected_shards(["src/lawvm/tools/no_op_trace.py"]) == [
        "norway",
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/new_zealand/acquisition.py"]) == [
        "new_zealand_effects",
        "new_zealand_reports",
        "new_zealand_sources",
    ]
    assert module.affected_shards(["src/lawvm/tools/sweden.py"]) == [
        *SWEDEN_EXECUTION_SHARDS,
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/uk_replay.py"]) == [
        "tools_cli_debug",
        "uk",
    ]
    assert module.affected_shards(["src/lawvm/tools/evidence.py"]) == [
        *EVIDENCE_EXECUTION_SHARDS,
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/evidence_claims.py"]) == [
        *EVIDENCE_EXECUTION_SHARDS,
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/strict_report.py"]) == [
        *EVIDENCE_EXECUTION_SHARDS,
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/tools/spec_ledger_us_catalog.py"]) == [
        "us_federal"
    ]
    assert module.affected_shards(["src/lawvm/tools/us_anchor_manifest.py"]) == [
        "tools_cli_debug",
        "us_federal",
    ]


def test_test_shard_maps_core_and_dependency_changes_to_all() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/core/timeline.py"]) == ["all"]
    assert module.affected_shards(["pyproject.toml"]) == ["all"]


def test_test_shard_maps_readonly_core_audits_to_bounded_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/core/cross_jurisdiction_parity.py"]) == [
        "core_tree_apply"
    ]
    assert _without_path_expanded_shards(
        module.affected_plan(["src/lawvm/core/cross_jurisdiction_parity.py"])
    ) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["src/lawvm/core/cross_jurisdiction_parity.py"],
        "shards": ["core_tree_apply"],
        "paths": [
            {
                "path": "src/lawvm/core/cross_jurisdiction_parity.py",
                "shards": ["core_tree_apply"],
                "reason": (
                    "known source path src/lawvm/core/cross_jurisdiction_parity.py "
                    "maps to core_tree_apply"
                ),
            }
        ],
    }


def test_test_shard_maps_repo_hygiene_and_ratchet_baselines_narrowly() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards([".gitignore"]) == ["tools_audit_release"]
    assert module.affected_shards([
        "tests/data/classifier_wrap_ratchet_baseline.json",
        "tests/data/module_roles_baseline.json",
        "tests/data/regex_ratchet_baseline.json",
    ]) == ["core_ir_contracts"]
    assert module.affected_shards([
        "src/lawvm/core/ctsf_gate.py",
        "tests/data/ctsf_gate_residual_baseline.json",
        "tests/data/ctsf_gate_ee_residual_baseline.json",
        "tests/data/ctsf_gate_eu_residual_baseline.json",
        "tests/data/ctsf_gate_no_residual_baseline.json",
        "tests/data/ctsf_gate_nz_residual_baseline.json",
        "tests/data/ctsf_gate_se_residual_baseline.json",
        "tests/data/ctsf_gate_uk_residual_baseline.json",
        "tests/data/ctsf_gate_us_residual_baseline.json",
    ]) == ["tools_ctsf_gate"]
    assert _without_path_expanded_shards(module.affected_plan([
        ".gitignore",
        "tests/data/classifier_wrap_ratchet_baseline.json",
    ])) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            ".gitignore",
            "tests/data/classifier_wrap_ratchet_baseline.json",
        ],
        "shards": ["core_ir_contracts", "tools_audit_release"],
        "paths": [
            {
                "path": ".gitignore",
                "shards": ["tools_audit_release"],
                "reason": "known tooling path .gitignore maps to tools_audit_release",
            },
            {
                "path": "tests/data/classifier_wrap_ratchet_baseline.json",
                "shards": ["core_ir_contracts"],
                "reason": (
                    "known source path tests/data/classifier_wrap_ratchet_baseline.json "
                    "maps to core_ir_contracts"
                ),
            },
        ],
    }


def test_test_shard_maps_nested_test_paths_by_relative_test_name() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["tests/substrate/test_canonical_json.py"]) == [
        "substrate"
    ]


def test_test_shard_maps_finland_source_defect_fixes_to_bounded_fi_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        ["data/finland/reference_successor_promotion_claims_fi.jsonl"]
    ) == ["core_surface_semantic"]
    assert module.affected_shards(
        [
            "data/finland/source_defect_fixes_fi.yaml",
            "tests/test_fi_replay_products.py",
        ]
    ) == [
        "finland_replay_products_core",
        "finland_replay_rules",
    ]
    assert _without_path_expanded_shards(module.affected_plan(
        ["data/finland/source_defect_fixes_fi.yaml"]
    )) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["data/finland/source_defect_fixes_fi.yaml"],
        "shards": [
            "finland_replay_products_core",
            "finland_replay_rules",
        ],
        "paths": [
            {
                "path": "data/finland/source_defect_fixes_fi.yaml",
                "shards": [
                    "finland_replay_products_core",
                    "finland_replay_rules",
                ],
                "reason": (
                    "known frontend prefix data/finland/source_defect_fixes_fi.yaml maps "
                    "to finland_replay_products_core, finland_replay_rules"
                ),
            }
        ],
    }


def test_test_shard_maps_shared_non_core_modules_to_bounded_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/contracts.py"]) == [
        *CORE_EXECUTION_SHARDS_SORTED,
    ]
    assert module.affected_shards(["src/lawvm/graph_build.py"]) == [
        *CORE_EXECUTION_SHARDS_SORTED,
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/semantic/model.py"]) == [
        *CORE_EXECUTION_SHARDS_SORTED,
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/xml_ingest.py"]) == [
        *CORE_EXECUTION_SHARDS_SORTED,
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_cli_debug",
    ]
    assert module.affected_shards(["src/lawvm/us_federal/bootstrap.py"]) == ["us_federal"]
    assert set(module.affected_shards(["src/lawvm/us_federal/us_ordering.py"])).isdisjoint({
        "eu",
        "sweden_fetch",
        "sweden_misc",
    })


def test_test_shard_affected_plan_ignores_documentation_paths() -> None:
    module = _load_test_shard_module()

    assert _without_path_expanded_shards(module.affected_plan(["notes/ARCHITECTURE.md"])) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["notes/ARCHITECTURE.md"],
        "shards": [],
        "paths": [
            {
                "path": "notes/ARCHITECTURE.md",
                "shards": [],
                "reason": "documentation path has no bounded pytest shard impact",
            }
        ],
    }
    assert module.affected_shards(["docs/getting-started.md"]) == []
    assert module.affected_shards(["notes/EU_STRUCTURAL_INGESTION_ROADMAP.md"]) == []
    assert module.affected_shards(["README.md"]) == []
    assert module.affected_shards(["AGENTS.md"]) == []
    assert module.affected_shards(["CHANGELOG.md"]) == []
    assert module.affected_shards(["LICENSE"]) == []
    assert module.affected_shards(
        [
            "src/lawvm/us_federal/dry_run.py",
            "notes/DEFERRED_ROADMAP.md",
        ]
    ) == ["us_federal"]
    assert module.affected_shards(
        [
            "src/lawvm/eu/pipeline.py",
            "notes/EU_STRUCTURAL_INGESTION_ROADMAP.md",
        ]
    ) == ["eu"]
    assert module.affected_shards(
        [
            "src/lawvm/us_federal/dry_run.py",
            "docs/getting-started.md",
        ]
    ) == ["us_federal"]


def test_test_shard_affected_plan_explains_core_and_dependency_all() -> None:
    module = _load_test_shard_module()

    assert _without_path_expanded_shards(module.affected_plan(
        [
            "src/lawvm/core/timeline.py",
            "uv.lock",
        ]
    )) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "src/lawvm/core/timeline.py",
            "uv.lock",
        ],
        "shards": ["all"],
        "paths": [
            {
                "path": "src/lawvm/core/timeline.py",
                "shards": ["all"],
                "reason": "core/dependency prefix src/lawvm/core/ forces all affected shards",
            },
            {
                "path": "uv.lock",
                "shards": ["all"],
                "reason": "global dependency change forces all affected shards",
            },
        ],
    }


def test_test_shard_affected_plan_explains_frontend_and_tool_shards() -> None:
    module = _load_test_shard_module()

    assert _without_path_expanded_shards(module.affected_plan(
        [
            "src/lawvm/finland/frontend_compile.py",
            "src/lawvm/tools/no_op_trace.py",
            "src/lawvm/tools/uk_replay.py",
            "scripts/ci.sh",
        ]
    )) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "src/lawvm/finland/frontend_compile.py",
            "src/lawvm/tools/no_op_trace.py",
            "src/lawvm/tools/uk_replay.py",
            "scripts/ci.sh",
        ],
        "shards": [
            "finland_parse_payload",
            "finland_replay_compile",
            "finland_replay_grafter",
            "finland_replay_products_core",
            "finland_replay_products_support",
            "finland_replay_rules",
            "finland_sources",
            "norway",
            "tools_audit_release",
            "tools_cli_debug",
            "uk",
        ],
        "paths": [
            {
                "path": "src/lawvm/finland/frontend_compile.py",
                "shards": ["finland"],
                "reason": "known frontend prefix src/lawvm/finland/ maps to finland",
            },
            {
                "path": "src/lawvm/tools/no_op_trace.py",
                "shards": ["norway", "tools_cli_debug"],
                "reason": (
                    "known tool source path src/lawvm/tools/no_op_trace.py maps to "
                    "norway, tools_cli_debug"
                ),
            },
            {
                "path": "src/lawvm/tools/uk_replay.py",
                "shards": ["uk", "tools_cli_debug"],
                "reason": (
                    "known tool source path src/lawvm/tools/uk_replay.py maps to "
                    "uk, tools_cli_debug"
                ),
            },
            {
                "path": "scripts/ci.sh",
                "shards": ["tools_audit_release"],
                "reason": "known tooling path scripts/ci.sh maps to tools_audit_release",
            },
        ],
    }


def test_test_shard_affected_plan_exposes_expanded_execution_shards() -> None:
    module = _load_test_shard_module()

    plan = module.affected_plan(
        [
            "src/lawvm/finland/frontend_compile.py",
            "src/lawvm/estonia/grafter.py",
            "src/lawvm/tools/uk_replay.py",
        ]
    )

    assert plan["paths"][0]["shards"] == ["finland"]
    assert plan["paths"][0]["expanded_shards"] == [
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
    ]
    assert plan["paths"][1]["shards"] == ["estonia"]
    assert plan["paths"][1]["expanded_shards"] == ["estonia_replay_logic", "estonia_replay_semantics", "estonia_sources"]
    assert plan["paths"][2]["shards"] == ["uk", "tools_cli_debug"]
    assert plan["paths"][2]["expanded_shards"] == [
        "tools_cli_debug",
        "uk",
    ]
    assert plan["shards"] == [
        "estonia_replay_logic",
        "estonia_replay_semantics",
        "estonia_sources",
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
        "tools_cli_debug",
        "uk",
    ]


def test_test_shard_affected_plan_explains_shared_non_core_shards() -> None:
    module = _load_test_shard_module()

    assert _without_path_expanded_shards(module.affected_plan(
        [
            "src/lawvm/semantic/model.py",
            "src/lawvm/xml_ingest.py",
            "src/lawvm/graph_build.py",
            "src/lawvm/contracts.py",
            "src/lawvm/us_federal/bootstrap.py",
        ]
    )) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "src/lawvm/semantic/model.py",
            "src/lawvm/xml_ingest.py",
            "src/lawvm/graph_build.py",
            "src/lawvm/contracts.py",
            "src/lawvm/us_federal/bootstrap.py",
        ],
        "shards": [
            *CORE_EXECUTION_SHARDS_SORTED,
            "finland_parse_payload",
            "finland_replay_compile",
            "finland_replay_grafter",
            "finland_replay_products_core",
            "finland_replay_products_support",
            "finland_replay_rules",
            "finland_sources",
            "tools_cli_debug",
            "us_federal",
        ],
        "paths": [
            {
                "path": "src/lawvm/semantic/model.py",
                "shards": ["core", "finland", "tools_cli_debug"],
                "reason": "known frontend prefix src/lawvm/semantic/ maps to core, finland, tools_cli_debug",
            },
            {
                "path": "src/lawvm/xml_ingest.py",
                "shards": ["core", "finland", "tools_cli_debug"],
                "reason": "known frontend prefix src/lawvm/xml_ingest.py maps to core, finland, tools_cli_debug",
            },
            {
                "path": "src/lawvm/graph_build.py",
                "shards": ["core", "tools_cli_debug"],
                "reason": "known frontend prefix src/lawvm/graph_build.py maps to core, tools_cli_debug",
            },
            {
                "path": "src/lawvm/contracts.py",
                "shards": ["core"],
                "reason": "known frontend prefix src/lawvm/contracts.py maps to core",
            },
            {
                "path": "src/lawvm/us_federal/bootstrap.py",
                "shards": ["us_federal"],
                "reason": "known frontend prefix src/lawvm/us_federal/ maps to us_federal",
            },
        ],
    }


def test_test_shard_affected_plan_explains_excluded_all_and_unknown_blocking() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "notes/ARCHITECTURE.md",
        ]
    ) == [
        "finland_parse_payload",
        "finland_replay_compile",
        "finland_replay_grafter",
        "finland_replay_products_core",
        "finland_replay_products_support",
        "finland_replay_rules",
        "finland_sources",
    ]
    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "tests/test_fi_pipeline_gold.py",
        ]
    ) == ["all"]
    assert _without_path_expanded_shards(module.affected_plan(
        [
            "notes/ARCHITECTURE.md",
            "tests/test_fi_pipeline_gold.py",
        ]
    )) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "notes/ARCHITECTURE.md",
            "tests/test_fi_pipeline_gold.py",
        ],
        "shards": ["all"],
        "paths": [
            {
                "path": "notes/ARCHITECTURE.md",
                "shards": [],
                "reason": "documentation path has no bounded pytest shard impact",
            },
            {
                "path": "tests/test_fi_pipeline_gold.py",
                "shards": ["all"],
                "reason": "excluded test: gold corpus suite; intentionally outside bounded non-network CI; run all affected shards",
            },
        ],
    }
    unknown_plan = _without_path_expanded_shards(
        module.affected_plan(["assets/new_fixture.bin"])
    )
    assert unknown_plan == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["assets/new_fixture.bin"],
        "shards": [],
        "blocking_paths": [
            {
                "path": "assets/new_fixture.bin",
                "shards": [],
                "reason": "unknown path is not mapped to a bounded shard",
                "blocking": True,
                "fix": (
                    "add an affected-shard mapping in scripts/test_shard.py, or run "
                    "./scripts/ci.sh / ./scripts/ci.sh --shards 'frontends modules' explicitly"
                ),
            }
        ],
        "paths": [
            {
                "path": "assets/new_fixture.bin",
                "shards": [],
                "reason": "unknown path is not mapped to a bounded shard",
                "blocking": True,
                "fix": (
                    "add an affected-shard mapping in scripts/test_shard.py, or run "
                    "./scripts/ci.sh / ./scripts/ci.sh --shards 'frontends modules' explicitly"
                ),
            }
        ],
    }
    with pytest.raises(ValueError, match="assets/new_fixture.bin"):
        module.affected_shards(["assets/new_fixture.bin"])


def test_test_shard_affected_cli_rejects_unknown_paths() -> None:
    root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        ["./scripts/test_shard.sh", "affected", "assets/new_fixture.bin"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Cannot compute --affected shard set for unknown path" in result.stderr
    assert "assets/new_fixture.bin" in result.stderr
    assert "add an affected-shard mapping" in result.stderr


def test_test_shard_affected_cli_rejects_unmapped_tooling_paths() -> None:
    root = Path(__file__).resolve().parents[1]

    for path in ("scripts/new_tool.py", "src/lawvm/tools/new_tool.py"):
        result = subprocess.run(
            ["./scripts/test_shard.sh", "affected", path],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 2
        assert path in result.stderr
        assert "no explicit affected-shard mapping" in result.stderr


def test_test_shard_tool_source_paths_are_exact_not_prefix_wildcards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/tools/no_op_trace.py"]) == [
        "norway",
        "tools_cli_debug",
    ]
    with pytest.raises(ValueError, match="src/lawvm/tools/no_new_probe.py"):
        module.affected_shards(["src/lawvm/tools/no_new_probe.py"])


def test_test_shard_tool_source_mapping_covers_every_existing_tool_file_once() -> None:
    module = _load_test_shard_module()
    repo_root = Path(__file__).resolve().parents[1]
    tool_files = {
        str(path.relative_to(repo_root)).replace("\\", "/")
        for path in (repo_root / "src" / "lawvm" / "tools").glob("*.py")
    }
    grouped_paths = [
        path
        for paths in module.TOOL_SOURCE_SHARD_GROUPS.values()
        for path in paths
    ] + list(module._GENERAL_TOOL_SOURCE_PATHS)

    assert sorted(grouped_paths) == sorted(set(grouped_paths))
    assert set(grouped_paths) == tool_files
    assert set(module.TOOL_SOURCE_SHARD_PATHS) == tool_files


def test_ci_sharded_accepts_explicit_shard_flags_and_rejects_affected_mix() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "ci_sharded.sh"

    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0
    help_result = subprocess.run(
        [str(script), "--help"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert help_result.returncode == 0
    assert "--shard norway" in help_result.stdout
    assert "--shards \"norway sweden eu\"" in help_result.stdout
    assert "--shards \"frontends modules\"" in help_result.stdout
    assert "LAWVM_CI_TIMING_JSONL=0" in help_result.stdout
    assert "LAWVM_CI_TIMING_HISTORY_JSONL" in help_result.stdout
    assert "LAWVM_CI_TIMING_RUN_ID" in help_result.stdout

    conflict_result = subprocess.run(
        [str(script), "--affected", "tests/test_ci_shards.py", "--shard", "tools"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert conflict_result.returncode == 2
    assert "--affected cannot be combined with --shard/--shards" in conflict_result.stderr

    bare_affected_result = subprocess.run(
        [str(script), "--affected"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert bare_affected_result.returncode == 2
    assert "--affected requires at least one path" in bare_affected_result.stderr
    assert "run ./scripts/ci.sh" in bare_affected_result.stderr


def test_ci_sharded_docs_only_affected_is_noop_green() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "ci_sharded.sh"

    result = subprocess.run(
        [str(script), "--affected", "notes/DEFERRED_ROADMAP.md", "README.md", "AGENTS.md"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "=== [affected docs-only] no CI execution required ===" in result.stdout
    assert "Selected shards: (none)" in result.stdout
    assert "=== SHARDED CI GREEN ===" in result.stdout
    assert "=== [1/6] ruff check ===" not in result.stdout
    assert "=== [6/6] release hygiene ===" not in result.stdout
