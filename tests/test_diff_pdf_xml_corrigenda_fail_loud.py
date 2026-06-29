"""§1.10 guard-liveness tests for ``scripts/diff_pdf_xml_corrigenda.py``.

The script's ``_pdf_bytes_to_text`` previously used the
``NamedTemporaryFile(suffix=".pdf", delete=False)`` + manual
``Path(tmp_path).unlink(missing_ok=True)`` pattern with a bare
``except Exception: return None`` (silent swallow). C4 migrated the tempfile to
``tempfile.TemporaryDirectory`` (so the context manager handles cleanup on
every exit path) and routed the unexpected-exception swallow through
``named_swallow`` so a typed Finding surfaces via ``log_emitter``.

Mirrors the corrigendum.py ``_pdf_page_count`` migration precedent at iter2 W6
LOW-1 (``test_pdf_page_count_cleans_up_tempfile_on_subprocess_timeout_expired``
in ``test_corrigendum_fail_loud.py``) and the sweden/grafter.py
``se_pdf_bytes_to_text`` named_swallow precedent
(``test_sweden_grafter_pdf_bytes_to_text_witnesses_unexpected_subprocess_error``).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

import scripts.diff_pdf_xml_corrigenda as dpzc  # noqa: F401  (asserts importability)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TrackingTempDir(dpzc.tempfile.TemporaryDirectory):
    """Subclass that records the tempdir paths it creates so the test can
    assert they were unlinked by ``__exit__`` even on exception."""

    _seen: list[str] = []

    def __enter__(self) -> str:
        ret = super().__enter__()
        _TrackingTempDir._seen.append(self.name)
        return ret


def _assert_no_orphaned_diff_pdf_tempdirs() -> None:
    """Scan ``tempfile.gettempdir()`` for leftover ``diff_pdf_xml_corrigenda_*`` dirs."""
    import tempfile as _scratch

    leftover: list[Path] = [
        d for d in Path(_scratch.gettempdir()).glob("diff_pdf_xml_corrigenda_*")
        if d.exists()
    ]
    assert not leftover, (
        f"orphaned diff_pdf_xml_corrigenda_* tempdirs (TemporaryDirectory leak): {leftover}"
    )


# ---------------------------------------------------------------------------
# Test 1: TemporaryDirectory cleans up on subprocess TimeoutExpired
# ---------------------------------------------------------------------------

def test_pdf_bytes_to_text_cleans_up_tempfile_on_subprocess_timeout_expired(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_pdf_bytes_to_text`` cleans up its tempfile on ``subprocess.TimeoutExpired``.

    Regression for C4: the prior ``NamedTemporaryFile(suffix=".pdf", delete=False)``
    + in-line ``Path(tmp_path).unlink(missing_ok=True)`` shape placed the unlink
    AFTER the subprocess call — a TimeoutExpired raised by subprocess skipped the
    cleanup line and leaked the tempfile. The ``TemporaryDirectory`` migration
    makes ``__exit__`` handle cleanup regardless of how the body exits.

    TimeoutExpired is a subclass of subprocess.SubprocessError (NOT of
    FileNotFoundError) — the broad ``except Exception`` clause catches it and
    routes through ``named_swallow`` (the swallow-for-anything-other-than-
    FileNotFoundError path). The witness is the same Finding as the unexpected-
    ValueError path below (asserted in the next test); here we assert the
    TemporaryDirectory cleanup invariant.
    """
    _TrackingTempDir._seen = []
    monkeypatch.setattr(dpzc.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(dpzc.subprocess, "run", fake_run)

    # Silence the named_swallow ``log_emitter`` WARNING (the witness fires for
    # TimeoutExpired too — asserted explicitly in the next test; here the
    # focus is the tempdir cleanup invariant, not the log emission).
    with caplog.at_level(logging.WARNING, logger="lawvm.core.named_swallow"):
        result = dpzc._pdf_bytes_to_text(b"\x25\x50\x44\x46fakepdf")

    # Recovery preserved — None returned on subprocess failure.
    assert result is None
    # TemporaryDirectory was used and cleaned up.
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
    _assert_no_orphaned_diff_pdf_tempdirs()
    # The named_swallow Finding fires for TimeoutExpired too (the broad-except
    # path catches it; FileNotFoundError is the only narrow-silent branch).
    assert any(
        "diff_pdf_xml_corrigenda_pdf_bytes_to_text_subprocess" in record.getMessage()
        and "TimeoutExpired" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Test 2: unexpected-subprocess swallow routes through named_swallow (§1.10)
# ---------------------------------------------------------------------------

def test_pdf_bytes_to_text_witnesses_unexpected_subprocess_error_via_named_swallow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D8/C4 guard-liveness: the unexpected-subprocess ``except Exception``
    swallow MUST route through ``named_swallow`` so a typed Finding surfaces
    via ``log_emitter`` (the prior shape silently returned None).

    Drives a synthesized known-violating input (``subprocess.run`` raising
    ValueError — a non-FileNotFoundError, non-TimeoutExpired-on-SubprocessError
    branch) through the production path and asserts the typed Finding fires
    with ``rule_id="diff_pdf_xml_corrigenda_pdf_bytes_to_text_subprocess"``.
    """
    _TrackingTempDir._seen = []
    monkeypatch.setattr(dpzc.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise ValueError("simulated unexpected subprocess.run error")

    monkeypatch.setattr(dpzc.subprocess, "run", fake_run)

    # ``named_swallow`` writes via ``logging.getLogger("lawvm.core.named_swallow")``
    # — capture WARNING level to witness the typed Finding's render line.
    with caplog.at_level(logging.WARNING, logger="lawvm.core.named_swallow"):
        result = dpzc._pdf_bytes_to_text(b"\x25\x50\x44\x46fakepdf")

    # Recovery preserved — None returned on unexpected subprocess error.
    assert result is None
    # TemporaryDirectory was used and cleaned up (no leak regression).
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
    _assert_no_orphaned_diff_pdf_tempdirs()
    # The typed named_swallow Finding was logged via log_emitter — stderr
    # WARNING visibility per §1.10 (never silent).
    assert any(
        "diff_pdf_xml_corrigenda_pdf_bytes_to_text_subprocess" in record.getMessage()
        and "ValueError" in record.getMessage()
        for record in caplog.records
    ), (
        "Expected named_swallow Finding with "
        "rule_id=diff_pdf_xml_corrigenda_pdf_bytes_to_text_subprocess and "
        "exception_type=ValueError in the log; got: "
        + "; ".join(r.getMessage() for r in caplog.records)
    )


# ---------------------------------------------------------------------------
# Test 3: happy-path return shape preserved (negative test, §2.9)
# ---------------------------------------------------------------------------

def test_pdf_bytes_to_text_happy_path_returns_decoded_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity guard (§2.9): the TemporaryDirectory migration preserves the
    happy-path return shape (decoded stdout on pdftotext success).
    """
    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"PDF text content", stderr=b""
        )

    monkeypatch.setattr(dpzc.subprocess, "run", fake_run)

    result = dpzc._pdf_bytes_to_text(b"\x25\x50\x44\x46fakepdf")
    assert result == "PDF text content"


# ---------------------------------------------------------------------------
# Test 4: FileNotFoundError stays silent (pdftotext binary missing — matched
# the prior swallow's intent; unchanged by the migration).
# ---------------------------------------------------------------------------

def test_pdf_bytes_to_text_file_not_found_error_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative test (§2.9): FileNotFoundError (pdftotext not installed) is
    silent — the narrow-narrowed branch returns None without firing the
    ``named_swallow`` witness. Matches the prior swallow's intent (a missing
    poppler-utils install is not a swallowed FAILURE — it's an environmental
    invariant surfaced elsewhere).
    """
    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError(2, "No such file or directory", "pdftotext")

    monkeypatch.setattr(dpzc.subprocess, "run", fake_run)

    with caplog.at_level(logging.WARNING, logger="lawvm.core.named_swallow"):
        result = dpzc._pdf_bytes_to_text(b"\x25\x50\x44\x46fakepdf")

    assert result is None
    # No named_swallow Finding — FileNotFoundError is the narrow silent branch.
    assert not any(
        "diff_pdf_xml_corrigenda_pdf_bytes_to_text_subprocess" in record.getMessage()
        for record in caplog.records
    )
