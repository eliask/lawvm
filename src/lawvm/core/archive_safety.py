"""Decompression-bomb guards for archive ingestion (Security M1).

zipfile.ZipFile.read(name) and tarfile.extractfile(member).read() materialise
the full member into memory with no upper bound. A malicious archive member
declaring a huge uncompressed size could OOM the process. This module
centralises the per-member cap plus a typed diagnostic so every call site
applies the same defence and emits the same receipt shape on overflow
(AGENTS.md §1.8 / §1.10 — the skip is visible, the diagnostic names the
concrete fix, and the rejected member is rejectable in strict mode).

The cap is read once per call from ``$LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` (default
1 GB) so operators can raise it without code changes; a member declaring
zero bytes is allowed through unchanged (handled by callers' existing
empty-member filters).

This module is intentionally small and dependency-free: it lives below the
frontend/core boundary so every acquisition lane (FI HE ZIP ingest, EU
CELLAR FMX4, US PLAW zip, Norway tar.bz2 corpus) imports the same helper.
"""

from __future__ import annotations

import os
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from typing import IO


_DEFAULT_MAX_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024  # 1 GB uncompressed


def archive_max_member_bytes() -> int:
    """Resolved per-member byte cap from ``$LAWVM_MAX_ARCHIVE_MEMBER_BYTES``.

    Read on every call (not module-scope) so test fixtures and operators
    adjusting the env mid-run are honoured. Malformed values fail loud with
    a named diagnostic — silent fallback to the default would hide a
    misconfigured cap (AGENTS.md §1.10).
    """
    raw = os.environ.get("LAWVM_MAX_ARCHIVE_MEMBER_BYTES")
    if not raw:
        return _DEFAULT_MAX_ARCHIVE_MEMBER_BYTES
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise InvalidArchiveMemberCapSetting(raw_value=raw) from exc
    if parsed <= 0:
        raise InvalidArchiveMemberCapSetting(raw_value=raw)
    return parsed


@dataclass(frozen=True, slots=True)
class ArchiveMemberTooLargeDiagnostic:
    """Typed record describing an oversized archive member.

    Carried on the ``ArchiveMemberTooLarge`` exception and surfaced to caller
    accumulators (e.g. ImportReport.skipped_entries, HEAcquisitionFailure) so
    the rejected member is conserved per AGENTS.md §1.8 — the receipt is
    inspectable, not silently dropped.
    """

    archive_path: str
    member_name: str
    declared_size: int
    cap_bytes: int

    def render_reason(self) -> str:
        return (
            f"archive member declares {self.declared_size} bytes "
            f"(cap {self.cap_bytes}); refusing to materialise into memory. "
            f"Raise LAWVM_MAX_ARCHIVE_MEMBER_BYTES to admit this member, or "
            f"trim the source archive."
        )


class ArchiveMemberTooLarge(Exception):
    """Raised when an archive member's declared size exceeds the cap.

    The fields mirror :class:`ArchiveMemberTooLargeDiagnostic` so callers can
    promote the exception into whatever typed receipt their lane already
    uses (ImportReport skip record, HEAcquisitionFailure, ...). Per §1.8 the
    skip MUST be visible — callers that swallow this exception are required
    to record an equivalent receipt in their accumulator before continuing.
    """

    def __init__(
        self,
        *,
        archive_path: str,
        member_name: str,
        declared_size: int,
        cap_bytes: int,
    ) -> None:
        self.diagnostic = ArchiveMemberTooLargeDiagnostic(
            archive_path=archive_path,
            member_name=member_name,
            declared_size=declared_size,
            cap_bytes=cap_bytes,
        )
        super().__init__(self.diagnostic.render_reason())

    @property
    def archive_path(self) -> str:
        return self.diagnostic.archive_path

    @property
    def member_name(self) -> str:
        return self.diagnostic.member_name

    @property
    def declared_size(self) -> int:
        return self.diagnostic.declared_size

    @property
    def cap_bytes(self) -> int:
        return self.diagnostic.cap_bytes


class InvalidArchiveMemberCapSetting(ValueError):
    """Raised when ``$LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` is unparseable or non-positive.

    Distinct from a plain ``ValueError`` so triage points at the exact env
    var instead of a generic "could not parse integer" (AGENTS.md §1.10 —
    fail loud, name the concrete fix).
    """

    def __init__(self, *, raw_value: str) -> None:
        self.raw_value = raw_value
        super().__init__(
            f"LAWVM_MAX_ARCHIVE_MEMBER_BYTES must be a positive integer; "
            f"got {raw_value!r}"
        )


def safe_zip_read(
    zf: zipfile.ZipFile,
    name: str,
    *,
    archive_path: str = "",
) -> bytes:
    """Read a zip member, raising ``ArchiveMemberTooLarge`` on declared overflow.

    Checks ``info.file_size`` (the zip-declared uncompressed size) BEFORE the
    read so the bomb never materialises. The declared size is authoritative
    for the cap check; the actual bytes are only read on success.
    """
    info = zf.getinfo(name)
    cap = archive_max_member_bytes()
    if info.file_size > cap:
        raise ArchiveMemberTooLarge(
            archive_path=archive_path,
            member_name=name,
            declared_size=info.file_size,
            cap_bytes=cap,
        )
    return zf.read(name)


def safe_tar_read(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    archive_path: str = "",
) -> bytes | None:
    """Read a tar member, raising ``ArchiveMemberTooLarge`` on declared overflow.

    Checks ``member.size`` (the tar-declared uncompressed size) BEFORE
    ``extractfile(...).read()`` so a malicious member declaring a huge size
    is rejected without materialising it. Returns ``None`` when
    ``extractfile`` reports the member is not a regular file (symlinks,
    hardlinks, devices) — this preserves the generator-skip pattern used by
    the Norway tar.bz2 iterators that previously did
    ``if tf.extractfile(member) is None: continue``.
    """
    cap = archive_max_member_bytes()
    if member.size > cap:
        raise ArchiveMemberTooLarge(
            archive_path=archive_path,
            member_name=member.name,
            declared_size=member.size,
            cap_bytes=cap,
        )
    file_obj: IO[bytes] | None = tf.extractfile(member)
    if file_obj is None:
        return None
    return file_obj.read()


def log_archive_member_too_large(exc: ArchiveMemberTooLarge) -> None:
    """Visible stderr receipt for generators without a typed accumulator.

    Norway's ``open_lovdata_archive`` / ``open_lovdata_amendment_archive``
    and ``_iter_current_artifacts_from_dir`` yield ``(id, bytes)`` tuples and
    cannot append to a ``RejectedItem`` list without a signature change. Per
    AGENTS.md §1.8 the skip MUST still be visible — this helper emits a
    structured stderr line carrying the same fields as the typed diagnostic
    so the rejection is greppable downstream instead of disappearing.
    """
    sys.stderr.write(
        f"ARCHIVE_MEMBER_TOO_LARGE: skipped {exc.member_name} "
        f"from {exc.archive_path or '<archive>'} "
        f"(declared {exc.declared_size} bytes, cap {exc.cap_bytes}); "
        f"raise LAWVM_MAX_ARCHIVE_MEMBER_BYTES to admit it.\n"
    )
    sys.stderr.flush()


__all__ = [
    "ArchiveMemberTooLarge",
    "ArchiveMemberTooLargeDiagnostic",
    "InvalidArchiveMemberCapSetting",
    "archive_max_member_bytes",
    "log_archive_member_too_large",
    "safe_tar_read",
    "safe_zip_read",
]
