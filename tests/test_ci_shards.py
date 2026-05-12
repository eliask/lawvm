from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def _load_test_shard_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "test_shard.py"
    spec = importlib.util.spec_from_file_location("lawvm_test_shard_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
        "test_citation_routing.py": "large skip-heavy/gold-style corpus route inventory",
        "test_pipeline_gold.py": "gold corpus suite; intentionally outside bounded non-network CI",
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
            "file": "tests/test_citation_routing.py",
            "reason": "large skip-heavy/gold-style corpus route inventory",
        },
        {
            "file": "tests/test_pipeline_gold.py",
            "reason": "gold corpus suite; intentionally outside bounded non-network CI",
        },
    ]


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


def test_test_shard_maps_source_modules_to_frontend_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "scripts/ci.sh",
        ]
    ) == ["finland", "tools"]


def test_test_shard_maps_core_and_dependency_changes_to_all() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/core/timeline.py"]) == ["all"]
    assert module.affected_shards(["pyproject.toml"]) == ["all"]


def test_test_shard_maps_shared_non_core_modules_to_bounded_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(["src/lawvm/contracts.py"]) == ["core"]
    assert module.affected_shards(["src/lawvm/graph_build.py"]) == ["core", "tools"]
    assert module.affected_shards(["src/lawvm/semantic/model.py"]) == ["core", "finland", "tools"]
    assert module.affected_shards(["src/lawvm/xml_ingest.py"]) == ["core", "finland", "tools"]
    assert module.affected_shards(["src/lawvm/us_federal/bootstrap.py"]) == ["starter"]


def test_test_shard_affected_plan_defaults_to_all_for_unknown_paths() -> None:
    module = _load_test_shard_module()

    assert module.affected_plan(["notes/ARCHITECTURE.md"]) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": ["notes/ARCHITECTURE.md"],
        "shards": ["all"],
        "paths": [
            {
                "path": "notes/ARCHITECTURE.md",
                "shards": ["all"],
                "reason": "unknown path is not mapped to a bounded shard; run all affected shards",
            }
        ],
    }


def test_test_shard_affected_plan_explains_core_and_dependency_all() -> None:
    module = _load_test_shard_module()

    assert module.affected_plan(
        [
            "src/lawvm/core/timeline.py",
            "uv.lock",
        ]
    ) == {
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

    assert module.affected_plan(
        [
            "src/lawvm/finland/frontend_compile.py",
            "scripts/ci.sh",
        ]
    ) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "src/lawvm/finland/frontend_compile.py",
            "scripts/ci.sh",
        ],
        "shards": ["finland", "tools"],
        "paths": [
            {
                "path": "src/lawvm/finland/frontend_compile.py",
                "shards": ["finland"],
                "reason": "known frontend prefix src/lawvm/finland/ maps to finland",
            },
            {
                "path": "scripts/ci.sh",
                "shards": ["tools"],
                "reason": "tools prefix scripts/ maps to tools",
            },
        ],
    }


def test_test_shard_affected_plan_explains_shared_non_core_shards() -> None:
    module = _load_test_shard_module()

    assert module.affected_plan(
        [
            "src/lawvm/semantic/model.py",
            "src/lawvm/xml_ingest.py",
            "src/lawvm/graph_build.py",
            "src/lawvm/contracts.py",
            "src/lawvm/us_federal/bootstrap.py",
        ]
    ) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "src/lawvm/semantic/model.py",
            "src/lawvm/xml_ingest.py",
            "src/lawvm/graph_build.py",
            "src/lawvm/contracts.py",
            "src/lawvm/us_federal/bootstrap.py",
        ],
        "shards": ["core", "finland", "starter", "tools"],
        "paths": [
            {
                "path": "src/lawvm/semantic/model.py",
                "shards": ["core", "finland", "tools"],
                "reason": "known frontend prefix src/lawvm/semantic/ maps to core, finland, tools",
            },
            {
                "path": "src/lawvm/xml_ingest.py",
                "shards": ["core", "finland", "tools"],
                "reason": "known frontend prefix src/lawvm/xml_ingest.py maps to core, finland, tools",
            },
            {
                "path": "src/lawvm/graph_build.py",
                "shards": ["core", "tools"],
                "reason": "known frontend prefix src/lawvm/graph_build.py maps to core, tools",
            },
            {
                "path": "src/lawvm/contracts.py",
                "shards": ["core"],
                "reason": "known frontend prefix src/lawvm/contracts.py maps to core",
            },
            {
                "path": "src/lawvm/us_federal/bootstrap.py",
                "shards": ["starter"],
                "reason": "known frontend prefix src/lawvm/us_federal/ maps to starter",
            },
        ],
    }


def test_test_shard_affected_plan_explains_unknown_and_excluded_all() -> None:
    module = _load_test_shard_module()

    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "notes/ARCHITECTURE.md",
        ]
    ) == ["all"]
    assert module.affected_shards(
        [
            "src/lawvm/finland/frontend_compile.py",
            "tests/test_pipeline_gold.py",
        ]
    ) == ["all"]
    assert module.affected_plan(
        [
            "notes/ARCHITECTURE.md",
            "tests/test_pipeline_gold.py",
        ]
    ) == {
        "kind": "lawvm_pytest_affected_shards",
        "input_paths": [
            "notes/ARCHITECTURE.md",
            "tests/test_pipeline_gold.py",
        ],
        "shards": ["all"],
        "paths": [
            {
                "path": "notes/ARCHITECTURE.md",
                "shards": ["all"],
                "reason": "unknown path is not mapped to a bounded shard; run all affected shards",
            },
            {
                "path": "tests/test_pipeline_gold.py",
                "shards": ["all"],
                "reason": "excluded test: gold corpus suite; intentionally outside bounded non-network CI; run all affected shards",
            },
        ],
    }


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

    conflict_result = subprocess.run(
        [str(script), "--affected", "tests/test_ci_shards.py", "--shard", "tools"],
        check=False,
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert conflict_result.returncode == 2
    assert "--affected cannot be combined with --shard/--shards" in conflict_result.stderr
