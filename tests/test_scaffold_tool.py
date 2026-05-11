from __future__ import annotations

import pytest

from lawvm.tools.scaffold import _validate_jurisdiction, scaffold


def test_scaffold_generates_blocked_non_claim_starter(tmp_path) -> None:
    src_root = tmp_path / "src"

    scaffold("New-Land", src_root)

    dest = src_root / "lawvm" / "new_land"
    assert sorted(path.name for path in dest.iterdir()) == [
        "README.md",
        "__init__.py",
        "starter.py",
        "test_new_land.py",
    ]
    starter = (dest / "starter.py").read_text(encoding="utf-8")
    assert "build_blocked_p5_runtime_scaffold" in starter
    assert "zero replay attempts" in starter
    assert "parse_" not in starter
    assert "apply_" not in starter

    package_init = (dest / "__init__.py").read_text(encoding="utf-8")
    assert "build_blocked_clause_surface" in package_init
    assert "parse_" not in package_init
    assert "apply_" not in package_init

    readme = (dest / "README.md").read_text(encoding="utf-8")
    assert "does not parse" in readme
    assert "claim replay support" in readme


def test_scaffold_refuses_existing_destination(tmp_path) -> None:
    src_root = tmp_path / "src"
    (src_root / "lawvm" / "demo").mkdir(parents=True)

    with pytest.raises(SystemExit):
        scaffold("demo", src_root)


def test_validate_jurisdiction_normalizes_without_inventing_prefix() -> None:
    assert _validate_jurisdiction("New-Land_2") == "new_land_2"
    with pytest.raises(ValueError, match="must start with a letter"):
        _validate_jurisdiction("123")
