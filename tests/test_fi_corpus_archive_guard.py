"""Fail-loud guard + single-resolver tests for corpus_store.

These cover the regression where a missing/stub corpus archive caused a
read-path open to silently create an empty ~61 KB SQLite stub (via Farchive's
writable constructor), which then masqueraded downstream as
"statute X not found in corpus". Read opens must now raise
CorpusArchiveMissingError and must never create a file on disk.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from lawvm.corpus_store import (
    CorpusArchiveMissingError,
    get_corpus_store,
    open_corpus_archive,
    resolve_farchive_path,
)


def _make_populated_archive(path: Path) -> None:
    """Create a Farchive whose size clears the 1 MB populated-corpus floor."""
    from farchive import Farchive

    archive = Farchive(path, readonly=False)
    try:
        archive.store(
            "finlex://sd/2002/738/fin/main.xml",
            os.urandom(2_000_000),  # high entropy: zstd cannot shrink below 1 MB
            storage_class="xml",
        )
    finally:
        archive.close()
    assert path.stat().st_size >= 1_000_000


def _make_stub_archive(path: Path) -> None:
    """Create an empty init_schema'd Farchive stub (~61 KB)."""
    from farchive import Farchive

    archive = Farchive(path, readonly=False)
    archive.close()
    assert path.stat().st_size < 1_000_000


@pytest.fixture(autouse=True)
def _clear_corpus_env(monkeypatch):
    """Isolate every test from ambient corpus-path env vars."""
    for var in ("LAWVM_FARCHIVE_DB", "LAWVM_CANONICAL_DATA_ROOT", "LAWVM_HE_FARCHIVE_DB"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# (1) missing archive + read open raises, naming the path
# ---------------------------------------------------------------------------

def test_missing_archive_read_open_raises_naming_path(tmp_path, monkeypatch):
    missing = tmp_path / "nope.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(missing))

    with pytest.raises(CorpusArchiveMissingError) as exc:
        open_corpus_archive("finlex.farchive")

    msg = str(exc.value)
    assert "FARCHIVE_EMPTY_CORPUS" in msg
    assert str(missing) in msg
    assert "LAWVM_FARCHIVE_DB" in msg  # names the precedence rule
    assert "setup_worktree_links.sh" in msg  # remedy


def test_missing_archive_via_get_corpus_store_raises(tmp_path, monkeypatch):
    missing = tmp_path / "absent.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(missing))

    with pytest.raises(CorpusArchiveMissingError):
        get_corpus_store()


# ---------------------------------------------------------------------------
# (2) stub/empty archive raises the same error
# ---------------------------------------------------------------------------

def test_stub_archive_read_open_raises(tmp_path, monkeypatch):
    stub = tmp_path / "stub.farchive"
    _make_stub_archive(stub)
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(stub))

    with pytest.raises(CorpusArchiveMissingError) as exc:
        open_corpus_archive("finlex.farchive")
    assert "FARCHIVE_EMPTY_CORPUS" in str(exc.value)


# ---------------------------------------------------------------------------
# (3) read open NEVER creates a file on disk
# ---------------------------------------------------------------------------

def test_read_open_never_creates_file(tmp_path, monkeypatch):
    missing = tmp_path / "should_not_exist.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(missing))

    with pytest.raises(CorpusArchiveMissingError):
        open_corpus_archive("finlex.farchive")

    assert not missing.exists()
    # No sidecar SQLite/WAL/lock files either.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# (4) LAWVM_CANONICAL_DATA_ROOT override resolves correctly
# ---------------------------------------------------------------------------

def test_canonical_data_root_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    path, rule = resolve_farchive_path("finlex.farchive")
    assert path == tmp_path / "data" / "finlex.farchive"
    assert "LAWVM_CANONICAL_DATA_ROOT" in rule


def test_canonical_data_root_open_succeeds_when_populated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    archive_path = data_dir / "finlex.farchive"
    _make_populated_archive(archive_path)

    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path))
    archive, resolved, rule = open_corpus_archive("finlex.farchive")
    try:
        assert resolved == archive_path
        assert "LAWVM_CANONICAL_DATA_ROOT" in rule
        assert archive.get("finlex://sd/2002/738/fin/main.xml") is not None
    finally:
        archive.close()


def test_explicit_env_precedence_over_canonical_root(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.farchive"
    _make_populated_archive(explicit)
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(explicit))
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(tmp_path / "other"))

    path, rule = resolve_farchive_path("finlex.farchive")
    assert path == explicit
    assert "LAWVM_FARCHIVE_DB" in rule


def test_he_resolver_uses_distinct_env_var(tmp_path, monkeypatch):
    he_explicit = tmp_path / "he.farchive"
    monkeypatch.setenv("LAWVM_HE_FARCHIVE_DB", str(he_explicit))
    # The finlex var must NOT leak into HE resolution.
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(tmp_path / "finlex.farchive"))

    path, rule = resolve_farchive_path(
        "fi_government_proposal.farchive", explicit_env="LAWVM_HE_FARCHIVE_DB"
    )
    assert path == he_explicit
    assert "LAWVM_HE_FARCHIVE_DB" in rule


# ---------------------------------------------------------------------------
# (5) explicit create flag still creates for ingest paths
# ---------------------------------------------------------------------------

def test_allow_create_creates_missing_archive(tmp_path, monkeypatch):
    target = tmp_path / "fresh_ingest.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(target))
    assert not target.exists()

    archive, resolved, _rule = open_corpus_archive("finlex.farchive", allow_create=True)
    try:
        assert resolved == target
        assert target.exists()  # ingest legitimately creates
        archive.store("finlex://sd/2002/738/fin/main.xml", b"<xml/>", storage_class="xml")
    finally:
        archive.close()


def test_writable_open_on_populated_does_not_recreate(tmp_path, monkeypatch):
    archive_path = tmp_path / "rw.farchive"
    _make_populated_archive(archive_path)
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(archive_path))

    archive, _resolved, _rule = open_corpus_archive("finlex.farchive", writable=True)
    try:
        archive.store("finlex://sd/2099/1/fin/main.xml", b"<new/>", storage_class="xml")
        assert archive.get("finlex://sd/2099/1/fin/main.xml") == b"<new/>"
    finally:
        archive.close()


def test_writable_open_on_missing_still_fails_loud(tmp_path, monkeypatch):
    missing = tmp_path / "missing_rw.farchive"
    monkeypatch.setenv("LAWVM_FARCHIVE_DB", str(missing))

    with pytest.raises(CorpusArchiveMissingError):
        open_corpus_archive("finlex.farchive", writable=True)
    assert not missing.exists()


def test_source_tree_does_not_open_default_archive_writable():
    root = Path(__file__).resolve().parents[1] / "src" / "lawvm"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "Farchive":
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if (
                not isinstance(first_arg, ast.Call)
                or not isinstance(first_arg.func, ast.Name)
                or first_arg.func.id != "_archive_path"
            ):
                continue
            readonly_kw = next(
                (keyword for keyword in node.keywords if keyword.arg == "readonly"),
                None,
            )
            if (
                readonly_kw is None
                or not isinstance(readonly_kw.value, ast.Constant)
                or readonly_kw.value.value is not True
            ):
                rel = path.relative_to(root.parents[1])
                offenders.append(f"{rel}:{node.lineno}")

    assert offenders == []
