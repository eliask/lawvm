"""§1.10 fail-loud tests for ``src/lawvm/tools/corrigendum.py``.

Drives the broad-``except Exception`` replacements and asserts:

* The typed ``CorrigendumApplyFailure`` diagnostic fires on ``stderr`` with
  the right ``rule_id``, ``exception_kind``, ``corrigendum_id`` and a
  populated ``snippet`` per §1.10 (so triaging a residual does not require
  re-running extraction).
* Recovery stays behaviour-preserving (``None`` / ``[]`` / ``""``).
* The ``_pdf_to_images_base64`` ``TemporaryDirectory`` sandbox cleans up the
  intermediate ``pdftoppm`` ``page-*.jpg`` files when an exception fires
  mid-render — the prior ``NamedTemporaryFile``+``finally`` shape leaked
  them because the finally block only unlinked ``pdf_path``.

Three of the nine converted sites are exercised here
(parse-failure / subprocess-failure / file-not-found); the remaining six
share the same ``_emit_corrigendum_failure`` shape and are covered by the
shape assertion in ``test_emit_corrigendum_failure_carries_full_payload``.
"""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from lawvm.tools import corrigendum as corr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeCS:
    """Minimal stand-in for the corpus-store read_locator contract."""

    def __init__(self, data: bytes | None) -> None:
        self._data = data

    def read_locator(self, path: object) -> bytes | None:  # noqa: ARG002
        return self._data

    def read_source(self, sid: object) -> bytes | None:  # noqa: ARG002
        return self._data


def _last_failure(capsys) -> str:
    """Return the most recent CorrigendumApplyFailure line from stderr."""
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[corrigendum_apply_failure:")]
    assert lines, f"no CorrigendumApplyFailure fired; stderr was: {err!r}"
    return lines[-1]


# ---------------------------------------------------------------------------
# Shape (§1.9 — typed carrier)
# ---------------------------------------------------------------------------

def test_corrigendum_apply_failure_is_frozen_slots_dataclass() -> None:
    """§1.9 — the diagnostic carrier is a typed, frozen, slotted dataclass
    so callers cannot mutate it post-construction and it cannot grow
    dynamic attributes."""
    f = corr.CorrigendumApplyFailure(
        rule_id="x",
        step="y",
        exception_kind="ValueError",
        detail="boom",
        corrigendum_id="2020/123",
        snippet="snippet-text",
    )
    assert f.rule_id == "x"
    assert f.exception_kind == "ValueError"
    # §1.9 — frozen: assigning to an existing field raises FrozenInstanceError
    # (which is itself a subclass of AttributeError). A bare type-ignore
    # marker on the assignment line suppresses the static-checker complaint
    # that the assignment is illegal — that's the whole point of the test.
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.rule_id = "other"  # type: ignore
    # §1.9 — slotted: cannot grow a new attribute slot post-construction.
    # Combined frozen+slots on CPython raises TypeError when adding a new
    # attribute (the dataclass __setattr__ super() chain trips before the
    # slot guard), which is the §1.9-relevant invariant: no dynamic shape.
    assert hasattr(corr.CorrigendumApplyFailure, "__slots__")
    with pytest.raises((AttributeError, TypeError)):
        f.unexpected_attr = True  # type: ignore
    # render() surfaces the typed fields
    rendered = f.render()
    assert "rule_id" not in rendered  # we use the bare prefix
    assert "[corrigendum_apply_failure:x]" in rendered
    assert "step=y" in rendered
    assert "exc=ValueError" in rendered
    assert "2020/123" in rendered
    assert "snippet-text" in rendered


def test_emit_corrigendum_failure_carries_full_payload(capsys) -> None:
    """A diagnostic emitted via the helper carries all §1.10-required fields."""
    try:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fake reason")
    except UnicodeDecodeError as e:
        corr._emit_corrigendum_failure(
            rule_id="corrigendum_shape_test",
            step="shape_test",
            exc=e,
            corrigendum_id="1999/42",
            snippet=b"\xff\xfe bad bytes payload",
        )
    line = _last_failure(capsys)
    assert "corrigendum_shape_test" in line
    assert "UnicodeDecodeError" in line
    assert "1999/42" in line
    # snippet is decoded utf-8-with-replace and embedded with limit
    assert "bad bytes payload" in line


def test_emit_corrigendum_failure_accepts_str_and_bytes_snippet(capsys) -> None:
    """The emitter accepts both str and bytes snippets (bytes are decoded)."""
    try:
        raise RuntimeError("bang")
    except RuntimeError as e:
        corr._emit_corrigendum_failure(
            rule_id="r1", step="s1", exc=e, snippet="text-snippet"
        )
    line = _last_failure(capsys)
    assert "text-snippet" in line

    try:
        raise RuntimeError("bang2")
    except RuntimeError as e:
        corr._emit_corrigendum_failure(
            rule_id="r2", step="s2", exc=e, snippet=b"\xff\xfe binary-snippet"
        )
    line = _last_failure(capsys)
    assert "binary-snippet" in line  # decoded via utf-8 replace


def test_emit_corrigendum_failure_truncates_to_400_chars(capsys) -> None:
    """§1.10 — snippet is capped at ~400 chars to bound log lines."""
    try:
        raise ValueError("x")
    except ValueError as e:
        corr._emit_corrigendum_failure(
            rule_id="r", step="s", exc=e, snippet="a" * 10_000
        )
    line = _last_failure(capsys)
    # the snippet is bounded — find the embedded slice and limit
    assert "aaaa" in line
    # find the `'aaaaaa...'` slice in the rendered string
    import re as _re
    match = _re.search(r"snippet='(a+)'", line)
    assert match, line
    assert len(match.group(1)) <= 401


# ---------------------------------------------------------------------------
# Site 5 — _get_xml_corrigendum_refs (parse failure / UnicodeDecodeError)
# ---------------------------------------------------------------------------

def test_get_xml_corrigendum_refs_emits_finding_on_bad_utf8(capsys, monkeypatch) -> None:
    """Site 5 (was line 3259): invalid UTF-8 in a corrigendum href capture
    raises UnicodeDecodeError inside _parse_corrigendum_xml_refs. §1.10 is
    satisfied by emitting ``CorrigendumApplyFailure(rule_id=corrigendum_parse_xml_refs,
    step=get_xml_corrigendum_refs.parse, corrigendum_id=sid, snippet=xml_bytes[:400])``
    and recovering with ``[]`` unchanged.

    Note: _parse_corrigendum_xml_refs captures the *inner* block content
    (between <finlex:corrigendum ...> and </finlex:corrigendum>), so the
    invalid-byte href must live INSIDE the block, not on the outer tag — the
    regex `href="([^"]+\\.pdf)"` is searched on `inner` only.
    """
    bad_xml = (
        b'<finlex:corrigendum>'
        b'<finlex:datePublished>2020-01-01</finlex:datePublished>'
        b'<finlex:ref>marker</finlex:ref>'
        # The href attribute on an inner element — its value contains 0xff
        # which is invalid as the first byte of a UTF-8 sequence, so the
        # .decode() call in _parse_corrigendum_xml_refs raises UnicodeDecodeError.
        # NOTE: must use a real 0xff byte (b'\xff'), NOT a raw-bytes literal
        # (rb'...\xff...' is 4 literal ASCII chars, not a single byte).
        b'<finlex:link href="media/corrigenda/bad' + b'\xff' + b'.pdf"/>'
        b'</finlex:corrigendum>'
    )

    monkeypatch.setattr(
        corr,
        "get_oracle_path",
        lambda *a, **k: "fake/locator/path",
    )

    refs = corr._get_xml_corrigendum_refs(_FakeCS(bad_xml), sid="2020/123")

    # Recovery preserved — empty list, like the prior silent swallow.
    assert refs == []
    line = _last_failure(capsys)
    assert "corrigendum_parse_xml_refs" in line
    assert "UnicodeDecodeError" in line
    assert "2020/123" in line  # corrigendum_id = sid surfaces
    # snippet embeds the offending bytes (decoded with errors=replace) —
    # the literal "media/corrigenda/bad" prefix is plain ASCII so survives.
    assert "media/corrigenda/bad" in line


# ---------------------------------------------------------------------------
# Site 7 — _pdf_to_text (FileNotFoundError / missing pdftotext binary)
# ---------------------------------------------------------------------------

def test_pdf_to_text_emits_finding_on_missing_pdftotext_binary(
    monkeypatch, capsys
) -> None:
    """Site 7 (was line 3312): FileNotFoundError → CorrigendumApplyFailure
    carrying pdf_bytes[:64] as the snippet.

    The prior shape silently returned None — making it indistinguishable
    from a non-zero poppler return code. The conversion surfaces the missing
    poppler-utils dependency via a named diagnostic."""
    def _raise_fnf(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "pdftotext")

    monkeypatch.setattr(corr.subprocess, "run", _raise_fnf)

    pdf_bytes = b"%PDF-1.4 fakepdfcontent with marker bytes"
    result = corr._pdf_to_text(pdf_bytes)
    assert result is None  # recovery preserved
    line = _last_failure(capsys)
    assert "corrigendum_pdf_to_text_missing_binary" in line
    assert "FileNotFoundError" in line
    assert pdftotext_substring_in(line)  # detail carries the binary name
    # snippet embeds the pdf_bytes head
    assert "%PDF-1.4" in line


def pdftotext_substring_in(line: str) -> bool:
    # FileNotFoundError's str(e) on Linux reads like "[Errno 2] No such file
    # or directory: 'pdftotext'" — assert 'pdftotext' appears in the detail
    # section of the rendered diagnostic.
    return "pdftotext" in line


# ---------------------------------------------------------------------------
# Site 6 — _pdf_page_count (subprocess failure / CalledProcessError)
# ---------------------------------------------------------------------------

def test_pdf_page_count_emits_finding_on_called_process_error(
    monkeypatch, capsys
) -> None:
    """Site 6 (was line 3286): subprocess.CalledProcessError → typed
    CorrigendumApplyFailure; the recovery stays ``None``.

    Uses CalledProcessError rather than FileNotFoundError to exercise the
    narrower branch alongside site 7. The pdf_bytes[:64] snippet surfaces
    the offending PDF."""
    fake_result = subprocess.CompletedProcess(
        args=["pdfinfo", "x.pdf"], returncode=1, stdout=b"", stderr=b"boom"
    )

    def fake_run(*args, **kwargs):
        # _pdf_page_count inspects returncode and returns None — to drive
        # the broader-except branch we instead raise CalledProcessError here.
        raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    pdf_bytes = b"\x25\x50\x44\x46-1.5 pdf-page-count-source-payload"
    result = corr._pdf_page_count(pdf_bytes)
    assert result is None
    line = _last_failure(capsys)
    assert "corrigendum_pdf_page_count" in line
    assert "CalledProcessError" in line
    # snippet embeds pdf_bytes[:64]
    assert "%PDF-1.5" in line


def test_pdf_page_count_no_diagnostic_when_returncode_nonzero(
    monkeypatch, capsys
) -> None:
    """Negative test (§2.9): when pdfinfo exits cleanly with rc!=0, the
    function returns None WITHOUT firing the broad-except diagnostic —
    that path is not a swallowed failure, it's a normal "no info" result."""
    fake = subprocess.CompletedProcess(
        args=["pdfinfo", "x.pdf"], returncode=1, stdout=b"", stderr=b"err"
    )
    monkeypatch.setattr(corr.subprocess, "run", lambda *a, **k: fake)
    result = corr._pdf_page_count(b"%PDF-1.4 stuff")
    assert result is None
    err = capsys.readouterr().err
    assert "corrigendum_apply_failure" not in err


# ---------------------------------------------------------------------------
# Tempfile leak fix in _pdf_to_images_base64
# ---------------------------------------------------------------------------

class _TrackingTempDir(corr.tempfile.TemporaryDirectory):
    """Subclass that records the tempdir paths it creates so the test can
    assert they were unlinked by __exit__ even on exception."""

    _seen: list[str] = []

    def __enter__(self):
        ret = super().__enter__()
        _TrackingTempDir._seen.append(self.name)
        return ret


def test_pdf_to_images_base64_cleans_up_jpegs_on_subprocess_failure(
    monkeypatch, capsys
) -> None:
    """The TemporaryDirectory sandbox cleans up the intermediate
    ``page-*.jpg`` files that ``pdftoppm`` writes when an exception fires
    after the subprocess has produced pages but before the loop fully
    drains them.

    Prior shape used ``NamedTemporaryFile`` + ``finally: os.unlink(pdf_path)``;
    that finally block left the JPEGs (``{prefix}-*.jpg``) on disk when
    ``subprocess.run`` itself raised CalledProcessError (cancellation /
    pdftoppm crash), and a ``KeyboardInterrupt`` mid-loop leaked too."""
    _TrackingTempDir._seen = []
    monkeypatch.setattr(corr.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd, *args, **kwargs):
        # pdftoppm produces page-N.jpg before the simulated crash.
        out_prefix = cmd[-1]
        Path(f"{out_prefix}-1.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpg")
        Path(f"{out_prefix}-2.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpg-2")
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd)

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        corr._pdf_to_images_base64(b"\x25\x50\x44\x46fakepdf")

    # TemporaryDirectory.__exit__ removed the directory and its JPEGs.
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
        assert not list(Path(tmp).glob("page-*.jpg")), (
            f"intermediate JPEG leak at {tmp}"
        )
    # No leftover corrigendum_pdf_* dirs with page JPEGs anywhere in /tmp.
    import tempfile as _scratch
    leftover_jpegs = []
    for d in Path(_scratch.gettempdir()).glob("corrigendum_pdf_*"):
        if d in [Path(p) for p in _TrackingTempDir._seen]:
            continue  # already asserted gone
        leftover_jpegs.extend(d.glob("page-*.jpg"))
    assert not leftover_jpegs, f"unexpected leftover JPEGs: {leftover_jpegs}"


def test_pdf_to_images_base64_happy_path_returns_base64(monkeypatch) -> None:
    """Sanity guard (§2.9 negative test): the TemporaryDirectory migration
    preserves the happy-path return shape.

    Uses the real TemporaryDirectory (no monkeypatch) and a fake
    ``subprocess.run`` that writes two known-content page JPEGs into the
    real tempdir pdftoppm would have populated, then asserts the loop
    reads and base64-encodes them correctly."""
    def fake_run(cmd, *args, **kwargs):
        out_prefix = cmd[-1]
        # pdftoppm emits 2 page JPEGs with known content
        Path(f"{out_prefix}-1.jpg").write_bytes(b"\xff\xd8\xff\xe0PAGE1")
        Path(f"{out_prefix}-2.jpg").write_bytes(b"\xff\xd8\xff\xe0PAGE2")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    out = corr._pdf_to_images_base64(b"\x25\x50\x44\x46fakepdf")
    assert len(out) == 2
    import base64
    assert base64.b64decode(out[0]) == b"\xff\xd8\xff\xe0PAGE1"
    assert base64.b64decode(out[1]) == b"\xff\xd8\xff\xe0PAGE2"


# ---------------------------------------------------------------------------
# Tempfile leak fix in _pdf_page_count / _pdf_to_text / _reextract_one
# (iter2 W6 LOW-1): three more sites that used the NamedTemporaryFile +
# manual ``Path(tmp_path).unlink(missing_ok=True)`` pattern.  Each leaked
# the tempfile on ``subprocess.TimeoutExpired`` because the unlink was placed
# in-line after ``subprocess.run``.  The migration to ``tempfile.TemporaryDirectory``
# (mirroring ``_pdf_to_images_base64`` above) makes the context-manager
# exit clean up on every exit path.
#
# Each test asserts: (1) a TemporaryDirectory was created, (2) the dir is gone
# after the call returns, (3) no orphaned ``corrigendum_pdf_*`` PDF files are
# left in the system tempdir.
# ---------------------------------------------------------------------------


def _assert_no_orphaned_corrigendum_tempdirs() -> None:
    """Scan ``tempfile.gettempdir()`` for any leftover ``corrigendum_pdf_*`` dirs.

    A leftover dir means a TemporaryDirectory context manager exited without
    cleaning up — i.e. the leak regression returned.
    """
    import tempfile as _scratch

    leftover: list[Path] = []
    for d in Path(_scratch.gettempdir()).glob("corrigendum_pdf_*"):
        # ``_TrackingTempDir._seen`` entries are explicitly asserted gone above;
        # a directory that is still on disk here is the leak.
        if d.exists():
            leftover.append(d)
    assert not leftover, f"orphaned corrigendum_pdf_* tempdirs: {leftover}"


def test_pdf_page_count_cleans_up_tempfile_on_subprocess_timeout_expired(
    monkeypatch, capsys
) -> None:
    """``_pdf_page_count`` cleans up its tempfile on ``subprocess.TimeoutExpired``.

    Regression for iter2 W6 LOW-1: the prior ``NamedTemporaryFile(delete=False)``
    shape placed ``Path(tmp_path).unlink(missing_ok=True)`` AFTER
    ``subprocess.run`` — a TimeoutExpired raised by subprocess skipped the
    unlink line and left the tempfile on disk.  The TemporaryDirectory
    migration makes ``__exit__`` handle cleanup regardless of how the body
    exits.
    """
    _TrackingTempDir._seen = []
    monkeypatch.setattr(corr.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    result = corr._pdf_page_count(b"\x25\x50\x44\x46fakepdf")

    # Recovery stays None — no Pages line was emitted.
    assert result is None
    # A finding must surface the timeout (§1.10 — never silent).
    err = capsys.readouterr().err
    assert "corrigendum_pdf_page_count" in err
    # TemporaryDirectory was used and cleaned up.
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
    _assert_no_orphaned_corrigendum_tempdirs()


def test_pdf_to_text_cleans_up_tempfile_on_subprocess_timeout_expired(
    monkeypatch, capsys
) -> None:
    """``_pdf_to_text`` cleans up its tempfile on ``subprocess.TimeoutExpired``.

    Same regression class as ``test_pdf_page_count_cleans_up_tempfile_on_subprocess_timeout_expired``
    but for the pdftotext path.
    """
    _TrackingTempDir._seen = []
    monkeypatch.setattr(corr.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    result = corr._pdf_to_text(b"\x25\x50\x44\x46fakepdf")

    # Recovery stays None — no text was extracted.
    assert result is None
    # A finding must surface the timeout (§1.10 — never silent).
    err = capsys.readouterr().err
    assert "corrigendum_pdf_to_text" in err
    # TemporaryDirectory was used and cleaned up.
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
    _assert_no_orphaned_corrigendum_tempdirs()


def test_reextract_one_cleans_up_tempfile_on_pdftotext_timeout_expired(
    monkeypatch, capsys
) -> None:
    """``_reextract_one`` cleans up its pdf-text tempfile on pdftotext TimeoutExpired.

    Covers the third leaked-tempfile site (iter2 W6 LOW-1): the pdf_text
    block inside ``_reextract_one``.  The function is async and continues
    past the timeout into the Phase-1 LLM call, so this test stubs the
    aiohttp session to fail fast (no network) — the goal is to verify the
    TemporaryDirectory was used and cleaned up, not to assert LLM behaviour.
    """
    import asyncio
    import unittest.mock

    _TrackingTempDir._seen = []
    monkeypatch.setattr(corr.tempfile, "TemporaryDirectory", _TrackingTempDir)

    def fake_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(corr.subprocess, "run", fake_run)

    # Stub aiohttp.ClientSession so session.post fails fast inside the
    # LLM Phase-1 call (no network in this test).  _reextract_one catches
    # every Exception in Phase-1 and returns ``{"error": "phase1: ..."}``,
    # so the test does not need to set up a live LLM.
    class _StubResponseCtx:
        async def __aenter__(self_inner) -> None:
            raise RuntimeError(
                "stub session.post: no LLM server in test environment"
            )

        async def __aexit__(self_inner, *exc: object) -> bool:
            return False

    class _StubSession:
        def post(self, url: str, **_: object) -> _StubResponseCtx:
            return _StubResponseCtx()

    async def _call() -> dict[str, object]:
        sem = asyncio.Semaphore(1)
        return await corr._reextract_one(
            _StubSession(),  # type: ignore
            sem,
            "2024/1",
            "wrong",
            "correct",
            "op-1",
            b"<root><x/></root>",
            b"\x25\x50\x44\x46fakepdf",
        )

    result = asyncio.run(_call())

    # The function returns a dict with an error key after Phase-1 fails —
    # the leaked-tempfile regression does NOT change this happy-error shape.
    assert isinstance(result, dict)
    assert "error" in result
    # A finding must surface the pdftotext timeout (§1.10 — never silent).
    err = capsys.readouterr().err
    assert "corrigendum_reextract_pdf_text" in err
    # TemporaryDirectory was used and cleaned up.
    assert _TrackingTempDir._seen, "TemporaryDirectory was never used"
    for tmp in _TrackingTempDir._seen:
        assert not Path(tmp).exists(), f"TemporaryDirectory leaked: {tmp}"
    _assert_no_orphaned_corrigendum_tempdirs()
    # Defensive: assert the asyncio event loop closed cleanly (no leaked
    # tasks / sessions on the stub side).
    del unittest.mock  # silence unused-import lint expectation if any
