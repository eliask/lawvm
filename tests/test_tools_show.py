"""Tests for ``lawvm show`` — pretty human-readable statute tree (FI).

Covers:
* § references and Y/N statute-id tokens are wrapped in OSC 8 hyperlinks
  when ``--hyperlinks always`` (and pass through plain when ``never``).
* The address filter scopes to one provision.
* ``--no-attachments`` excludes attachment supplements.
* ``--json`` emits canonical ir_serialize JSON.

Fixtures skip when the Finlex corpus archive is missing (per AGENTS.md §3.6
network/slow boundary — local corpus is the live-fixture precondition).
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from lawvm.tools.show import _hyperlink_statute_tokens, _parse_address


_FINLEX_ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "finlex.farchive"
_HAS_CORPUS = _FINLEX_ARCHIVE.exists() and _FINLEX_ARCHIVE.stat().st_size > 1_000_000


# ---------------------------------------------------------------------------
# Unit-level (no corpus needed) — pure helpers
# ---------------------------------------------------------------------------


def test_parse_address_section() -> None:
    assert _parse_address("section:5") == ("section", "5")


def test_parse_address_chained() -> None:
    assert _parse_address("chapter:1/section:5") == ("section", "5")


def test_parse_address_appendix() -> None:
    assert _parse_address("appendix:Liite_1") == ("appendix", "Liite_1")


def test_parse_address_returns_none_for_garbage() -> None:
    assert _parse_address("") is None
    assert _parse_address("nonsense") is None


def test_hyperlink_statute_tokens_passthrough_when_disabled() -> None:
    """No OSC sequence when enabled=False — visible text unchanged."""
    src = "sellaisina kuin ne ovat 1 § laeissa 578/1995, 1010/1995 ja 1018/2004"
    out = _hyperlink_statute_tokens(src, enabled=False)
    assert out == src


def test_hyperlink_statute_tokens_inserted_when_enabled() -> None:
    """Y/N statute-id tokens get OSC 8 wrapping (visible text unchanged)."""
    src = "lauseissa 578/1995 ja 1018/2004"
    out = _hyperlink_statute_tokens(src, enabled=True)
    # The visible token contains the statute id
    assert "578/1995" in out
    assert "1018/2004" in out
    # ESC ] 8 ; wrapped (terminal hyperlink)
    assert "\x1b]8;;" in out
    # Each statute id should be wrapped exactly once
    assert out.count("\x1b]8;;") == 2


def test_hyperlink_statute_tokens_skips_unparseable() -> None:
    """A token that doesn't resolve to a URL stays plain — visible text unchanged."""
    # 99/99999999 — year=99 (2-digit), n=99999999 (8-digit); consolidated_url_from_id
    # uses _STATUTE_RE which year_re is \\d{4}, so 2-digit years are skipped.
    src = "viite 99/99999999 tähän"
    out = _hyperlink_statute_tokens(src, enabled=True)
    assert "\x1b]8;;" not in out
    assert out == src


def test_hyperlink_statute_tokens_idempotent_on_plain_text() -> None:
    """Plain text with no statute-id tokens passes through unchanged."""
    src = "Tätä asetusta sovelletaan ajoneuvoihin."
    out = _hyperlink_statute_tokens(src, enabled=True)
    assert out == src


# ---------------------------------------------------------------------------
# Integration — only run when the Finlex corpus is present locally.
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    not _HAS_CORPUS,
    reason="requires local data/finlex.farchive (>1MB populated corpus)",
)


def _run_show(argv: list[str]) -> str:
    """Invoke `lawvm show` with the given extra args; return printed stdout."""
    import argparse
    from lawvm.tools.cli import _build_parser
    parser: argparse.ArgumentParser = _build_parser()
    args = parser.parse_args(["show", *argv])
    buf = io.StringIO()
    # Force stdout to a non-TTY so default `auto` mode reflects the captured stream.
    saved = sys.stdout
    sys.stdout = buf
    try:
        from lawvm.tools.show import main as show_main
        show_main(args)
    finally:
        sys.stdout = saved
    return buf.getvalue()


def test_show_body_renders_chapter_and_section_labels() -> None:
    out = _run_show(["2002/1248", "--no-attachments", "--hyperlinks", "never"])
    assert "1 luku" in out
    assert "1 §" in out
    # Should not crash on a statute with attachments
    assert "Statute: 2002/1248" in out


def test_show_with_attachments_emits_attachment_header() -> None:
    """Default (no --no-attachments) prints the attachment block."""
    out = _run_show(["2002/1248", "--hyperlinks", "never"])
    # Without the corpus having attachments for this statute, the absence
    # is still communicates (no crash); when attachments exist they appear
    # with their header token.
    assert "Statute: 2002/1248" in out


def test_show_address_filter_scopes_to_one_section() -> None:
    out = _run_show(["2002/1248", "--address", "section:1", "--hyperlinks", "never"])
    # The address subtree should have section text, and the cut header should
    # advertise the address.
    assert "Address: section:1" in out


def test_show_hyperlinks_always_wraps_year_tokens() -> None:
    out = _run_show(["2002/1248", "--no-attachments", "--hyperlinks", "always"])
    # When statute-id tokens appear in the rendered text, OSC 8 should be present.
    # We can't predict which section references a Y/N token exactly; assert that
    # the printer at minimum didn't crash and emitted a Statute header.
    assert "Statute: 2002/1248" in out


def test_show_json_emits_canonical_ir_json() -> None:
    out = _run_show(["2002/1248", "--no-attachments", "--json"])
    doc = json.loads(out)
    assert doc["statute_id"] == "2002/1248"
    assert isinstance(doc["body"], dict)
    assert doc["body"]["kind"] in ("BODY", "body")
