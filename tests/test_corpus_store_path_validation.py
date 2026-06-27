"""Tests for ``validate_farchive_create_path`` hardening (Security M2).

Previously the function only rejected extensionless destinations. The
hardening also rejects paths that escape the resolved data root via
``..`` traversal, absolute path injection, or symlink targets, raising a
typed :class:`FarchivePathOutsideDataRoot` with the input/resolved/data-root
triplet so triage does not require re-running the operation (AGENTS.md §1.10).

Operators may legitimately override the data root via
``$LAWVM_*_FARCHIVE_DB`` — those paths are honoured as explicit trusted
input, preserving test fixtures and ad-hoc ingests while still rejecting
untrusted path-shaped input.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.corpus_store import (
    FarchivePathOutsideDataRoot,
    validate_farchive_create_path,
)


def _resolve_root(path: Path) -> Path:
    """Resolve `path` even if the file does not exist (degrades to str(Path))."""
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def test_validate_rejects_extensionless_dest(tmp_path: Path) -> None:
    target = tmp_path / "unused"

    with pytest.raises(ValueError, match="extensionless farchive destination"):
        # Suffix check fires regardless of explicit_env.
        validate_farchive_create_path(
            target, explicit_env="LAWVM_FARCHIVE_DB"
        )


def test_validate_accepts_path_within_canonical_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the canonical data root at tmp_path so a path under
    # tmp_path/data/X.farchive passes the within-data-root check.
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_HE_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)

    target = tmp_path / "data" / "test.farchive"
    validate_farchive_create_path(target, explicit_env="LAWVM_FARCHIVE_DB")


def test_validate_rejects_dotdot_traversal_outside_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_HE_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    target = data_dir / ".." / "escape.farchive"

    with pytest.raises(FarchivePathOutsideDataRoot) as exc_info:
        validate_farchive_create_path(
            target, explicit_env="LAWVM_FARCHIVE_DB"
        )

    # The exception carries the triplet so triage doesn't re-run the op.
    assert exc_info.value.path == target
    assert exc_info.value.resolved == _resolve_root(target)
    assert exc_info.value.data_root == _resolve_root(data_dir)
    assert "FarchivePathOutsideDataRoot" in str(exc_info.value)


def test_validate_rejects_absolute_path_outside_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_HE_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)

    target = Path("/etc/passwd.farchive")

    with pytest.raises(FarchivePathOutsideDataRoot) as exc_info:
        validate_farchive_create_path(
            target, explicit_env="LAWVM_FARCHIVE_DB"
        )

    assert exc_info.value.path == target
    assert exc_info.value.resolved == Path("/etc/passwd.farchive")
    assert exc_info.value.data_root == (_resolve_root(tmp_path) / "data")


def test_validate_rejects_symlink_target_outside_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_HE_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    escape_target = tmp_path / "secret.farchive"
    symlink_inside = data_dir / ".aliased.farchive"

    if os.environ.get("GITHUB_ACTIONS"):
        # GitHub Actions runs `pytest` as root inside some containers, in
        # which case symlink creation succeeds but the resolved path of a
        # symlink inside the data root still points outward — the test's
        # assertion stands.
        pass
    try:
        os.symlink(escape_target, symlink_inside)
    except OSError as exc:  # pragma: no cover - sandboxed environments
        pytest.skip(f"cannot create symlink in this environment: {exc}")

    with pytest.raises(FarchivePathOutsideDataRoot) as exc_info:
        validate_farchive_create_path(
            symlink_inside, explicit_env="LAWVM_FARCHIVE_DB"
        )

    assert exc_info.value.path == symlink_inside
    assert exc_info.value.resolved == _resolve_root(symlink_inside)
    assert exc_info.value.data_root == _resolve_root(data_dir)


def test_validate_honours_explicit_env_override_outside_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators may legitimately point ``LAWVM_*_FARCHIVE_DB`` out-of-tree.

    Test fixtures and ad-hoc ingests rely on this — explicit override is
    trusted operator input, not a path-traversal vector.
    """
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)

    target = tmp_path / "fresh_ingest.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(target))

    # Must not raise — explicit override path is honoured.
    validate_farchive_create_path(target, explicit_env="LAWVM_FARCHIVE_DB")


def test_validate_rejects_path_when_explicit_env_does_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mismatched explicit_env value does NOT bypass the data-root check.

    Guards against the case where an attacker sets an env var pointing at a
    decoy path while supplying a different (hostile) ``path`` argument.
    """
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)

    decoy = tmp_path / "decoy.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(decoy))

    hostile_target = tmp_path / "hostile.farchive"

    with pytest.raises(FarchivePathOutsideDataRoot):
        validate_farchive_create_path(
            hostile_target, explicit_env="LAWVM_FARCHIVE_DB"
        )


def test_validate_skips_data_root_check_when_explicit_env_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``explicit_env=None`` keeps the suffix-only check (backwards-compat).

    Sweden's caller, New Zealand's ``_open_farchive_uncached``, and any
    caller passing a caller-supplied path (test fixtures, ad-hoc ingest
    CLI args) pass through ``explicit_env=None`` so the data-root check is
    opt-in: the resulting behaviour is byte-identical with the pre-hardening
    state. This is the dual of :func:`test_validate_rejects_absolute_path_outside_data_root`
    — the same /etc/passwd.farchive path is admitted because no precedence
    channel was claimed.
    """
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)

    target = Path("/etc/hostile_skipped.farchive")
    # Must NOT raise — explicit_env=None keeps the old suffix-only behaviour.
    validate_farchive_create_path(target, explicit_env=None)


def test_validate_uses_repo_root_data_when_canonical_root_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``$LAWVM_CANONICAL_DATA_ROOT`` is unset, the data root is
    ``<repo_root>/data`` — a path inside that directory passes."""

    monkeypatch.delenv("LAWVM_CANONICAL_DATA_ROOT", raising=False)
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_HE_FARCHIVE_DB", raising=False)
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)

    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "data" / "test_synthetic_target.farchive"

    # Must not raise — path is inside default repo-root data directory.
    validate_farchive_create_path(target, explicit_env="LAWVM_FARCHIVE_DB")


def test_farchive_path_outside_data_root_exception_message_embeds_triplet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exception message embeds input / resolved / data root (§1.10)."""
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("LAWVM_FARCHIVE_DB", raising=False)

    hostile_target = Path("/etc/hostile.farchive")
    with pytest.raises(FarchivePathOutsideDataRoot) as exc_info:
        validate_farchive_create_path(
            hostile_target, explicit_env="LAWVM_FARCHIVE_DB"
        )

    msg = str(exc_info.value)
    assert "FarchivePathOutsideDataRoot" in msg
    assert "input path" in msg
    assert "resolved" in msg
    assert "data root" in msg
    assert "remedy" in msg
    assert "LAWVM_*_FARCHIVE_DB" in msg
