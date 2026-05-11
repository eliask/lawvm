from __future__ import annotations

import importlib.util
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
