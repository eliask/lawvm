"""Security MEDIUM-3: ``curl`` argv separator tests for NO + EE fetch paths.

Both ``lawvm.norway.statsrad.fetch_statsrad_url`` and
``lawvm.estonia.fetch._curl`` shell out to the system ``curl`` binary with a
URL taken from caller input (a statsrad bulletin URL / an EE archive URL).

Without an explicit ``--`` end-of-options separator in the argv list, a URL
that starts with ``-`` (e.g. an attacker-controlled ``--help``,
``--config``, or ``-o``) would be parsed by curl as one of its own flags
rather than as the request target. The iter2 W6 security review
(MEDIUM-3) classified this as a MEDIUM-severity argument-injection
vector.

The fix inserts a literal ``"--"`` token between the last curl flag and the
URL in both call sites. These tests pin that property by monkeypatching
``subprocess.run`` and asserting on the resulting argv list — they do NOT
execute the real ``curl`` binary (hermetic / no network).

Per AGENTS.md §2.9 (guard-liveness): the assertion drives the *full
production path* via ``fetch_statsrad_url`` / ``_curl``, not a hand-rolled
argv builder in test code, so a future regression that rebuilds the argv
list without the separator is caught.
"""

from __future__ import annotations

import subprocess
from typing import Any

from lawvm.estonia.fetch import _curl as ee_curl
from lawvm.norway.statsrad import fetch_statsrad_url


def _payload_after_separator(argv: list[str]) -> list[str]:
    """Return the argv tail after the last ``--`` (the URL payload).

    If no ``--`` separator is present, returns an empty list to make the
    failure mode loud (the assertion comparing to the expected URL will
    fail with a useful diff).
    """
    if "--" not in argv:
        return []
    # Take everything after the LAST occurrence of ``--`` — curl semantics.
    idx = len(argv) - 1 - argv[::-1].index("--")
    return argv[idx + 1 :]


def _flag_region(argv: list[str]) -> list[str]:
    """Return the argv slice between the binary name and the ``--`` separator.

    These are the tokens curl parses as its own flags. The injection vector
    fires when a URL-value gets into this region.
    """
    if "--" not in argv:
        return argv[1:]
    idx = len(argv) - 1 - argv[::-1].index("--")
    return argv[1:idx]


# ----------------------------------------------------------------------
# Norway — fetch_statsrad_url
# ----------------------------------------------------------------------


def test_fetch_statsrad_url_inserts_separator_before_url(monkeypatch: Any) -> None:
    """``fetch_statsrad_url`` argv carries ``--`` immediately before the URL.

    Drives the full production path with a benign URL to demonstrate the
    separator is present in the normal case, not only when the URL is
    suspicious.
    """
    captured: list[list[str]] = []

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[bytes]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"<html>ok</html>", stderr=b"")

    monkeypatch.setattr("lawvm.norway.statsrad.subprocess.run", fake_run)

    fetch_statsrad_url("https://www.regjeringen.no/no/aktuelt/test/id1/")

    assert captured, "subprocess.run must be invoked exactly once"
    argv = captured[0]
    assert argv[0] == "curl"
    assert "--" in argv, "curl argv must contain the `--` end-of-options separator"
    payload = _payload_after_separator(argv)
    assert payload == ["https://www.regjeringen.no/no/aktuelt/test/id1/"], (
        f"URL must appear after `--` (got payload={payload!r}, full argv={argv!r})"
    )


def test_fetch_statsrad_url_does_not_let_url_look_like_a_flag(monkeypatch: Any) -> None:
    """A URL starting with ``-`` is routed past the separator, not as a flag.

    Regression for the MEDIUM-3 argument-injection vector: before the fix,
    ``--help`` would be parsed as a curl flag. After the fix, it is the sole
    element after ``--`` in the argv tail.
    """
    captured: list[list[str]] = []

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool
    ) -> subprocess.CompletedProcess[bytes]:
        captured.append(list(cmd))
        # Return a successful CompletedProcess so the retry loop exits early.
        return subprocess.CompletedProcess(cmd, 0, stdout=b"<html>ok</html>", stderr=b"")

    monkeypatch.setattr("lawvm.norway.statsrad.subprocess.run", fake_run)

    fetch_statsrad_url("--help")  # would-be argument-injection vector

    assert captured, "subprocess.run must be invoked"
    argv = captured[0]
    assert "--" in argv
    payload = _payload_after_separator(argv)
    assert payload == ["--help"], (
        "URL starting with `-` must be the post-`--` payload, not parsed as a "
        f"curl flag (got payload={payload!r}, full argv={argv!r})"
    )
    # Sanity: ``--help`` does not appear before the separator, i.e. is not
    # mixed into the flag region of the argv (which would let curl try to
    # interpret it as ``--help``).
    flag_region = _flag_region(argv)
    assert "--help" not in flag_region, (
        f"`--help` must not appear in the flag region {flag_region!r} of argv"
    )


# ----------------------------------------------------------------------
# Estonia — _curl
# ----------------------------------------------------------------------


class _StubArchive:
    """Minimal ``archive`` stand-in for ``ee_fetch._curl`` — records what `store` got."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, bytes]] = []

    def store(self, url: str, data: bytes) -> None:
        self.stored.append((url, data))


def _ee_fake_run_factory(captured: list[list[str]]) -> Any:
    """Build a ``subprocess.run`` stub for EE `_curl` that also seeds the
    tempfile so the post-run size-check passes.

    EE `_curl` checks ``tmp_path.stat().st_size >= 50`` after ``subprocess.run``
    returns. Since we don't actually execute curl, the tempfile would be empty
    and `_curl` would return ``None`` before reaching ``archive.store``. To let
    the test exercise the full post-run path, the stub finds the ``-o``
    tempfile path in argv and writes a sentinel payload into it.
    """

    def fake_run(
        cmd: list[str],
        capture_output: bool = False,
        **_: Any,
    ) -> subprocess.CompletedProcess[bytes]:
        captured.append(list(cmd))
        # Find the ``-o <tmp_path>`` pair in argv and seed the tempfile with
        # >50 bytes so the post-run size check passes.
        if "-o" in cmd:
            idx = cmd.index("-o")
            if idx + 1 < len(cmd):
                with open(cmd[idx + 1], "wb") as fh:
                    fh.write(b"x" * 64)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"x" * 64, stderr=b"")

    return fake_run


def test_ee_curl_inserts_separator_before_url(monkeypatch: Any) -> None:
    """``lawvm.estonia.fetch._curl`` argv carries ``--`` immediately before URL."""
    captured: list[list[str]] = []
    monkeypatch.setattr("lawvm.estonia.fetch.subprocess.run", _ee_fake_run_factory(captured))

    archive = _StubArchive()

    result = ee_curl("https://www.riigiteataja.ee/akt/123456789", archive)

    assert captured, "subprocess.run must be invoked exactly once"
    argv = captured[0]
    assert argv[0] == "curl"
    assert "--" in argv, "EE curl argv must contain the `--` end-of-options separator"
    payload = _payload_after_separator(argv)
    assert payload == ["https://www.riigiteataja.ee/akt/123456789"], (
        f"EE URL must appear after `--` (got payload={payload!r}, full argv={argv!r})"
    )
    # Since the stub seeds the tempfile, `_curl` should fully succeed:
    assert result is not None
    assert archive.stored, "archive.store must be called when curl returns bytes"


def test_ee_curl_does_not_let_url_look_like_a_flag(monkeypatch: Any) -> None:
    """EE `_curl` regression for MEDIUM-3: URL starting with ``-`` routed past ``--``."""
    captured: list[list[str]] = []
    monkeypatch.setattr("lawvm.estonia.fetch.subprocess.run", _ee_fake_run_factory(captured))

    archive = _StubArchive()

    ee_curl("-o/etc/passwd", archive)  # would-be argument-injection vector

    assert captured, "subprocess.run must be invoked"
    argv = captured[0]
    assert "--" in argv
    payload = _payload_after_separator(argv)
    assert payload == ["-o/etc/passwd"], (
        "URL starting with `-` must be the post-`--` payload, not parsed as a "
        f"curl flag (got payload={payload!r}, full argv={argv!r})"
    )
    flag_region = _flag_region(argv)
    assert "-o/etc/passwd" not in flag_region, (
        f"`-o/etc/passwd` must not appear in the flag region {flag_region!r} of argv"
    )

    # Defensive: the literal ``-o`` is still in the flag region (the EE curl
    # uses ``-o <tmpfile>`` for its output), but that ``-o`` belongs to the
    # curated curl flags BEFORE ``--``, NOT to the URL.
    assert flag_region.count("-o") >= 1, (
        "EE curl should still pass its own `-o <tmpfile>` flag before `--`; "
        f"flag region was {flag_region!r}"
    )
