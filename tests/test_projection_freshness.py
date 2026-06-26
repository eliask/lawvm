"""Tests for the READ-side Tier 2 projection-freshness guard.

The amendment-index cache fix (c6f266fa) closed one stale-artifact footgun; this
guard closes the same class for build-time projections: on load, a projection
whose recorded source_farchive_hash no longer matches the current farchive must
be reported stale (LOUD warning, or hard error under LAWVM_STRICT_FRESHNESS).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.tools import projection_freshness as pf
from lawvm.tools.tier2_state import (
    make_state,
    primary_farchive_hash,
    write_projection_state_after_export,
    write_state,
)


def _write_farchive(root: Path, name: str, payload: bytes) -> Path:
    p = root / name
    p.write_bytes(payload)
    return p


def _projection_dir(root: Path) -> Path:
    d = root / "fi" / "v1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_fresh_when_recorded_hash_matches(tmp_path: Path) -> None:
    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    # Record the CURRENT hash.
    current = primary_farchive_hash(str(tmp_path), ("finlex.farchive",))
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash=current,
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    verdict = pf.check_projection_freshness("ops", str(pdir))
    assert verdict.freshness_status == "fresh"
    assert verdict.is_stale is False


def test_stale_when_farchive_changes(tmp_path: Path) -> None:
    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    old_hash = primary_farchive_hash(str(tmp_path), ("finlex.farchive",))
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash=old_hash,
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    # Mutate the farchive (size + mtime change → new fingerprint).
    _write_farchive(tmp_path, "finlex.farchive", b"v2-bigger-payload")

    verdict = pf.check_projection_freshness("ops", str(pdir))
    assert verdict.freshness_status == "stale"
    assert verdict.is_stale is True
    assert verdict.recorded_hash != verdict.current_hash


def test_no_state_when_sidecar_missing(tmp_path: Path) -> None:
    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")
    verdict = pf.check_projection_freshness("ops", str(pdir))
    assert verdict.freshness_status == "no_state"


def test_unknown_when_unregistered_projection(tmp_path: Path) -> None:
    pdir = _projection_dir(tmp_path)
    verdict = pf.check_projection_freshness("not_a_real_projection", str(pdir))
    assert verdict.freshness_status == "unknown"


def test_unknown_when_no_farchive(tmp_path: Path) -> None:
    # No farchive present → cannot judge staleness (CI / farchive-less env).
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")
    verdict = pf.check_projection_freshness("ops", str(pdir))
    assert verdict.freshness_status == "unknown"


def test_write_state_after_export_yields_fresh(tmp_path: Path) -> None:
    # The dedicated-export sidecar writer must produce a state that the guard
    # immediately reads as fresh (the bug that motivated centralizing hashing).
    _write_farchive(tmp_path, "finlex.farchive", b"some-bytes")
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")
    write_projection_state_after_export(
        projection_dir=str(pdir),
        projection_name="ops",
        row_count=3,
        tier_1_dependencies=("finlex.farchive",),
        data_root=str(tmp_path),
    )
    verdict = pf.check_projection_freshness("ops", str(pdir))
    assert verdict.freshness_status == "fresh"


def test_warn_if_stale_emits_loud_warning(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("LAWVM_STRICT_FRESHNESS", raising=False)
    monkeypatch.delenv("LAWVM_SUPPRESS_FRESHNESS", raising=False)
    pf._WARNED.clear()

    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    old_hash = primary_farchive_hash(str(tmp_path), ("finlex.farchive",))
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash=old_hash,
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    _write_farchive(tmp_path, "finlex.farchive", b"v2-bigger-payload")

    verdict = pf.warn_if_stale("ops", str(pdir))
    err = capsys.readouterr().err
    assert verdict.is_stale
    assert "STALE PROJECTION" in err
    assert "rebuild-indexes" in err or "sync-fi-proposals" in err


def test_warn_if_stale_deduplicates(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("LAWVM_STRICT_FRESHNESS", raising=False)
    monkeypatch.delenv("LAWVM_SUPPRESS_FRESHNESS", raising=False)
    pf._WARNED.clear()

    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash="STALE",
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    pf.warn_if_stale("ops", str(pdir))
    first = capsys.readouterr().err
    pf.warn_if_stale("ops", str(pdir))
    second = capsys.readouterr().err
    assert "STALE PROJECTION" in first
    assert second == ""  # deduplicated, no second warning


def test_strict_freshness_exits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LAWVM_STRICT_FRESHNESS", "1")
    monkeypatch.delenv("LAWVM_SUPPRESS_FRESHNESS", raising=False)
    pf._WARNED.clear()

    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash="STALE",
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    with pytest.raises(SystemExit):
        pf.warn_if_stale("ops", str(pdir))


def test_suppress_freshness_silences(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("LAWVM_SUPPRESS_FRESHNESS", "1")
    pf._WARNED.clear()

    _write_farchive(tmp_path, "finlex.farchive", b"v1")
    pdir = _projection_dir(tmp_path)
    parquet = pdir / "ops.parquet"
    parquet.write_bytes(b"PAR1")
    write_state(
        parquet,
        make_state(
            projection_name="ops",
            schema_version="v1",
            row_count=1,
            source_farchive_hash="STALE",
            tier_1_dependencies=["finlex.farchive"],
        ),
    )
    verdict = pf.warn_if_stale("ops", str(pdir))
    assert verdict.freshness_status == "unknown"
    assert capsys.readouterr().err == ""


def test_source_age_warns_when_old(tmp_path: Path, capsys, monkeypatch) -> None:
    import time

    monkeypatch.delenv("LAWVM_SUPPRESS_FRESHNESS", raising=False)
    monkeypatch.setenv("LAWVM_SOURCE_AGE_WARN_DAYS", "30")
    pf._AGE_WARNED.clear()

    fa = _write_farchive(tmp_path, "finlex.farchive", b"v1")
    # Backdate the farchive mtime to 45 days ago.
    old = time.time() - 45 * 86400
    os.utime(fa, (old, old))
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")

    pf.warn_if_source_old("ops", str(pdir))
    err = capsys.readouterr().err
    assert "SOURCE MAY BE OUT OF DATE" in err
    assert "finlex.farchive" in err


def test_source_age_quiet_when_recent(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("LAWVM_SUPPRESS_FRESHNESS", raising=False)
    monkeypatch.setenv("LAWVM_SOURCE_AGE_WARN_DAYS", "30")
    pf._AGE_WARNED.clear()

    _write_farchive(tmp_path, "finlex.farchive", b"v1")  # fresh mtime
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")

    pf.warn_if_source_old("ops", str(pdir))
    assert capsys.readouterr().err == ""


def test_source_age_disabled_with_zero(tmp_path: Path, capsys, monkeypatch) -> None:
    import time

    monkeypatch.setenv("LAWVM_SOURCE_AGE_WARN_DAYS", "0")
    pf._AGE_WARNED.clear()

    fa = _write_farchive(tmp_path, "finlex.farchive", b"v1")
    old = time.time() - 999 * 86400
    os.utime(fa, (old, old))
    pdir = _projection_dir(tmp_path)
    (pdir / "ops.parquet").write_bytes(b"PAR1")

    pf.warn_if_source_old("ops", str(pdir))
    assert capsys.readouterr().err == ""


def test_branch_ops_registered() -> None:
    # Q1 added fi_he_branch_ops to the rebuild registry; the freshness sweep
    # must therefore know about it.
    from lawvm.tools.rebuild_indexes import _projections_for

    names = {s.name for s in _projections_for("fi")}
    assert "fi_he_branch_ops" in names
