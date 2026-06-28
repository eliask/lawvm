"""Tests for the decompression-bomb cap on ZIP / tar.bz2 ingestion (Security M1).

Drives ``safe_zip_read`` and ``safe_tar_read`` with members that declare sizes
above the cap and verifies:
* the typed ``ArchiveMemberTooLarge`` exception fires before any byte read,
* the diagnostic carries (archive_path, member_name, declared_size, cap_bytes),
* a small member passes through unchanged (negative case),
* a malformed env var fails loud per AGENTS.md §1.10.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from lawvm.core.archive_safety import (
    ArchiveMemberTooLarge,
    InvalidArchiveMemberCapSetting,
    archive_max_member_bytes,
    log_archive_member_too_large,
    safe_tar_read,
    safe_zip_read,
)


def _build_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _build_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_safe_zip_read_returns_bytes_for_small_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "1048576")  # 1 MB
    payload = b"<xml/>"
    blob = _build_zip({"akn/fi/act/statute/1988/46/fin@/main.xml": payload})

    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        data = safe_zip_read(zf, "akn/fi/act/statute/1988/46/fin@/main.xml")

    assert data == payload


def test_safe_zip_read_raises_before_materialising_oversized_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 1024  # 1 KB
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    # Member DECLARED size 100 KB — large enough to trip the cap, but the
    # outer archive contents are minimal so the test proves the check fires
    # on the declared size, not on the actual bytes read.
    declared_size = cap * 100
    members = {"huge.bin": b"x" * min(declared_size, 4096)}
    blob = _build_zip(members)

    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        info = zf.getinfo("huge.bin")
        # Force the declared (uncompressed) size past the cap independently
        # of the on-disk payload length.
        info.file_size = declared_size
        with pytest.raises(ArchiveMemberTooLarge) as exc_info:
            safe_zip_read(zf, "huge.bin", archive_path="statute.zip")

    diag = exc_info.value.diagnostic
    assert diag.archive_path == "statute.zip"
    assert diag.member_name == "huge.bin"
    assert diag.declared_size == declared_size
    assert diag.cap_bytes == cap
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in exc_info.value.diagnostic.render_reason()


def test_safe_tar_read_returns_bytes_for_small_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "1048576")  # 1 MB
    payload = b"<lov/>"
    blob = _build_tar({"lov/1998/100.xml": payload})

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r") as tf:
        member = tf.getmember("lov/1998/100.xml")
        data = safe_tar_read(tf, member, archive_path="gjeldende-lover.tar.bz2")

    assert data == payload


def test_safe_tar_read_raises_before_materialising_oversized_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 1024  # 1 KB
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    declared_size = cap * 100
    blob = _build_tar({"huge.bin": b"x" * 4096})

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r") as tf:
        member = tf.getmember("huge.bin")
        # Override the declared size past the cap; we never read the bytes.
        member.size = declared_size
        with pytest.raises(ArchiveMemberTooLarge) as exc_info:
            safe_tar_read(tf, member, archive_path="gjeldende-lover.tar.bz2")

    diag = exc_info.value.diagnostic
    assert diag.archive_path == "gjeldende-lover.tar.bz2"
    assert diag.member_name == "huge.bin"
    assert diag.declared_size == declared_size
    assert diag.cap_bytes == cap


def test_archive_max_member_bytes_defaults_to_100mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", raising=False)
    assert archive_max_member_bytes() == 100 * 1024 * 1024


def test_archive_max_member_bytes_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "42")
    assert archive_max_member_bytes() == 42


def test_archive_max_member_bytes_rejects_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "not-a-number")
    with pytest.raises(InvalidArchiveMemberCapSetting) as exc_info:
        archive_max_member_bytes()
    assert exc_info.value.raw_value == "not-a-number"
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in str(exc_info.value)


def test_archive_max_member_bytes_rejects_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "0")
    with pytest.raises(InvalidArchiveMemberCapSetting):
        archive_max_member_bytes()

    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", "-1")
    with pytest.raises(InvalidArchiveMemberCapSetting):
        archive_max_member_bytes()


def test_log_archive_member_too_large_emits_structured_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exc = ArchiveMemberTooLarge(
        archive_path="gjeldende-lover.tar.bz2",
        member_name="lov/1998/100.xml",
        declared_size=10_000_000,
        cap_bytes=1_048_576,
    )

    log_archive_member_too_large(exc)

    captured = capsys.readouterr().err
    assert "ARCHIVE_MEMBER_TOO_LARGE" in captured
    assert "lov/1998/100.xml" in captured
    assert "gjeldende-lover.tar.bz2" in captured
    assert "10000000" in captured
    assert "1048576" in captured
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in captured
