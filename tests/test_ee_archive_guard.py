from __future__ import annotations

from lawvm.estonia.fetch import open_rt_archive


def test_open_rt_archive_default_does_not_create_missing_unused_archive(tmp_path) -> None:
    missing = tmp_path / "unused.farchive"

    try:
        archive = open_rt_archive(missing)
    except Exception:
        pass
    else:
        archive.close()

    assert not missing.exists()


def test_open_rt_archive_explicit_writable_creates_archive(tmp_path) -> None:
    target = tmp_path / "ee_riigiteataja.farchive"

    archive = open_rt_archive(target, readonly=False)
    try:
        assert target.exists()
    finally:
        archive.close()
