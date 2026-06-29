"""Tests for the decompression-bomb cap on ZIP / tar.bz2 ingestion (Security M1).

Drives ``safe_zip_read`` and ``safe_tar_read`` with members that declare sizes
above the cap and verifies:
* the typed ``ArchiveMemberTooLarge`` exception fires before any byte read,
* the diagnostic carries (archive_path, member_name, declared_size, cap_bytes),
* a small member passes through unchanged (negative case),
* a malformed env var fails loud per AGENTS.md §1.10.

Wave 3 production-bypass fire-drills: drives the actual EU grafter
``parse_fmx4`` zip-ingestion path (src/lawvm/eu/grafter.py:92) and the
corrigendum integration-doc contract (src/lawvm/finland/corrigendum.py:34) so a
regression that re-introduces bare ``zf.read()`` at either site fails here
rather than silently re-opening the decompression-bomb surface.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Wave 3 production-bypass fire-drills (Security M1)
#
# These drive the actual production ingestion paths that were previously
# bypassing safe_zip_read: src/lawvm/eu/grafter.py:92 (EUIRGrafter.parse_fmx4)
# and src/lawvm/finland/corrigendum.py:34 (integration-doc contract for the
# Population B zip-read in process_muutoslaki). A regression that
# re-introduces bare zf.read() at either site fails here rather than silently
# re-opening the decompression-bomb surface. Per AGENTS.md §2.9 — guard-
# liveness: every new guard needs a test that drives a known-violating input
# through the FULL production path, not just a unit test of the guard.
# ---------------------------------------------------------------------------


def _build_fmx4_zip(zip_path: Path, member_name: str, payload: bytes) -> Path:
    """Write a single-member FMX4-shaped zip to ``zip_path``."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(member_name, payload)
    return zip_path


def test_eu_grafter_parse_fmx4_raises_on_oversized_zip_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fire-drill: parse_fmx4 must refuse an FMX4 zip member that exceeds the cap.

    Sets the cap to 1KB and feeds a real >1KB FMX4 XML zip through the
    production ``EUIRGrafter.parse_fmx4`` path. The cap check must fire
    BEFORE the bytes are materialised, and the typed receipt must carry the
    caller-provided ``archive_path`` (the xml_path) and the resolved member
    name.  If this regresses to bare ``zf.read()``, the test will OOM or,
    worse, silently accept the bomb — both are security failures.
    """
    cap = 1024  # 1 KB
    monkeypatch.setenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", str(cap))

    # FMX4 main-act XML: name must contain "01000101" or "000101" (per the
    # grafter's pattern-based act selection at src/lawvm/eu/grafter.py:86),
    # and the payload must be larger than `cap` to trip the check.
    member_name = "01000101.xml"
    # ~4 KB of valid XML content — well above the 1KB cap, well below the
    # test runner's memory budget.
    body = b"<ACT><TITLE>cap-bypass fire drill</TITLE>" + (b" " * (cap * 4)) + b"</ACT>"
    zip_path = tmp_path / "oversized.zip"
    _build_fmx4_zip(zip_path, member_name, body)

    from lawvm.eu.grafter import EUIRGrafter

    grafter = EUIRGrafter(celex="32000R0001")
    with pytest.raises(ArchiveMemberTooLarge) as exc_info:
        grafter.parse_fmx4(zip_path)

    diag = exc_info.value.diagnostic
    # archive_path MUST be the xml_path so triage points at the right file:
    assert diag.archive_path == str(zip_path)
    assert diag.member_name == member_name
    assert diag.declared_size > cap
    assert diag.cap_bytes == cap
    assert "LAWVM_MAX_ARCHIVE_MEMBER_BYTES" in diag.render_reason()


def test_eu_grafter_parse_fmx4_admits_small_zip_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Negative: a small FMX4 zip passes through parse_fmx4 unchanged.

    A small valid XML must round-trip through ``parse_fmx4`` and yield a
    statute whose title matches the source XML — proving the Wave 3 cap
    is not over-rejecting legitimate input.
    """
    # Default 100MB cap from archive_max_member_bytes() applies.
    monkeypatch.delenv("LAWVM_MAX_ARCHIVE_MEMBER_BYTES", raising=False)

    member_name = "01000101.xml"
    payload = b"<ACT><TITLE>small negative case</TITLE><ENACTING.TERMS/></ACT>"
    zip_path = tmp_path / "small.zip"
    _build_fmx4_zip(zip_path, member_name, payload)

    from lawvm.eu.grafter import EUIRGrafter

    grafter = EUIRGrafter(celex="32000R0001")
    statute = grafter.parse_fmx4(zip_path)

    assert statute.title == "small negative case"
    assert statute.statute_id == "32000R0001"


def test_corrigendum_integration_doc_references_safe_zip_read() -> None:
    """Contract test: the corrigendum.py integration-doc example must show
    ``safe_zip_read``, not bare ``zf.read()``.

    ``src/lawvm/finland/corrigendum.py:34`` is the integration-pattern
    example for Population B (``process_muutoslaki``'s zip-read of the
    amendment's main.xml). Iter 2's security review flagged this example
    — the production code that imports the integration pattern copies
    whatever shape the docstring documents, so the docstring MUST show
    the Wave 3 secure pattern. A regression that re-writes the example
    back to ``zf.read(...)`` fails here.

    The check is narrowly scoped to the bypass shape (``= zf.read(`` —
    assignment from bare zf.read) so the comment warning against bare
    zf.read() is itself allowed; only the actual insecure call shape
    registers as a regression.
    """
    import lawvm.finland.corrigendum as corrigendum_mod

    doc = corrigendum_mod.__doc__ or ""
    # Reject the bypass shape — assignment from bare zf.read without the cap:
    assert "= zf.read(" not in doc, (
        "corrigendum.py integration docstring must NOT show '= zf.read(' "
        "(bare zf.read bypass); it should show safe_zip_read "
        "(Wave 3 decompression-bomb cap, Security M1)"
    )
    # Require the secure shape — safe_zip_read with archive_path receipt:
    assert "safe_zip_read(" in doc, (
        "corrigendum.py integration docstring must show safe_zip_read "
        "for the Population B zip-read integration example"
    )
    assert "ArchiveMemberTooLarge" in doc, (
        "corrigendum.py integration docstring must show the "
        "ArchiveMemberTooLarge receipt shape, not just safe_zip_read()"
    )
    assert "archive_path=" in doc, (
        "corrigendum.py integration docstring must show archive_path= "
        "as the receipt field on safe_zip_read (AGENTS.md §1.8/§1.10)"
    )
