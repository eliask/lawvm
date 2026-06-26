"""Tests for OSC 8 terminal hyperlinks in human provenance output.

Correctness story under test:
  - hyperlink() emits the exact OSC 8 byte sequence.
  - ref_url() returns the four verified templates and None for unknown types.
  - should_hyperlink() gates correctly (non-tty / TERM=dumb / json / never off;
    tty+auto and always on).
  - the JSON / non-tty provenance path contains NO OSC 8 bytes.
  - default-off human output is byte-identical to the pre-feature renderer.
"""
from __future__ import annotations
from typing_extensions import override

import io
import json
from types import SimpleNamespace

from lawvm.tools import hyperlinks, provenance

OSC8 = "\033]8"


class _FakeTTY(io.StringIO):
    @override
    def isatty(self) -> bool:
        """Return true for terminal-gated rendering tests."""
        return True


class _FakeNonTTY(io.StringIO):
    @override
    def isatty(self) -> bool:
        return False


# --- hyperlink() exact bytes ----------------------------------------------------


def test_hyperlink_exact_osc8_bytes() -> None:
    out = hyperlinks.hyperlink("LaVM 3/2026", "https://example.test/x")
    assert out == "\033]8;;https://example.test/x\033\\LaVM 3/2026\033]8;;\033\\"
    # visible text preserved verbatim
    assert "LaVM 3/2026" in out


# --- ref_url() verified templates ----------------------------------------------


def test_ref_url_he() -> None:
    assert hyperlinks.ref_url("he", 188, 2025) == "https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/HE%20188%2F2025%20vp"


def test_ref_url_committee_report() -> None:
    url = hyperlinks.ref_url("committee_report", 3, 2026, type_prefix="LaVM")
    assert url == "https://www.eduskunta.fi/valtiopaivaasiakirjat/LaVM+3/2026"


def test_ref_url_parliament_response() -> None:
    url = hyperlinks.ref_url("parliament_response", 23, 2026)
    assert url == "https://www.eduskunta.fi/valtiopaivaasiakirjat/EV+23/2026"


def test_ref_url_statute() -> None:
    url = hyperlinks.ref_url("statute", 269, 2026)
    assert url == "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2026/269"


def test_ref_url_unknown_kind_is_none() -> None:
    assert hyperlinks.ref_url("mystery", 1, 2026) is None


def test_ref_url_unknown_committee_prefix_is_none() -> None:
    # A prefix not in the verified committee map degrades to plain (None URL).
    assert hyperlinks.ref_url("committee_report", 3, 2026, type_prefix="ZZZ") is None


def test_ref_url_committee_without_prefix_is_none() -> None:
    assert hyperlinks.ref_url("committee_report", 3, 2026) is None


def test_ref_url_non_numeric_is_none() -> None:
    assert hyperlinks.ref_url("he", "x", 2025) is None


# --- should_hyperlink() gate ----------------------------------------------------


def test_should_hyperlink_json_always_off() -> None:
    assert hyperlinks.should_hyperlink("always", _FakeTTY(), is_json=True) is False
    assert hyperlinks.should_hyperlink("auto", _FakeTTY(), is_json=True) is False


def test_should_hyperlink_never_off() -> None:
    assert hyperlinks.should_hyperlink("never", _FakeTTY()) is False


def test_should_hyperlink_always_on() -> None:
    # always = on even for a non-tty stream (still not for json).
    assert hyperlinks.should_hyperlink("always", _FakeNonTTY()) is True


def test_should_hyperlink_auto_non_tty_off() -> None:
    assert hyperlinks.should_hyperlink("auto", _FakeNonTTY()) is False


def test_should_hyperlink_auto_tty_on(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    assert hyperlinks.should_hyperlink("auto", _FakeTTY()) is True


def test_should_hyperlink_auto_dumb_term_off(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "dumb")
    assert hyperlinks.should_hyperlink("auto", _FakeTTY()) is False


# --- token parsing helpers ------------------------------------------------------


def test_he_url_from_canonical() -> None:
    assert (
        hyperlinks.he_url_from_canonical("he/2025/188")
        == "https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/HE%20188%2F2025%20vp"
    )
    assert hyperlinks.he_url_from_canonical("not-an-he") is None
    assert hyperlinks.he_url_from_canonical(None) is None


def test_statute_url_from_id() -> None:
    assert (
        hyperlinks.statute_url_from_id("2026/269")
        == "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2026/269"
    )
    assert hyperlinks.statute_url_from_id(None) is None


def test_consolidated_url_from_id() -> None:
    assert (
        hyperlinks.consolidated_url_from_id("2011/805")
        == "https://www.finlex.fi/fi/lainsaadanto/2011/805"
    )
    assert hyperlinks.consolidated_url_from_id(None) is None
    # suffixed ids are not the bare consolidated key -> None
    assert hyperlinks.consolidated_url_from_id("2011/805-x") is None


def test_committee_url_from_raw() -> None:
    assert (
        hyperlinks.committee_url_from_raw("LaVM 3/2026")
        == "https://www.eduskunta.fi/valtiopaivaasiakirjat/LaVM+3/2026"
    )
    # unknown committee prefix -> None (plain text)
    assert hyperlinks.committee_url_from_raw("ZZZ 3/2026") is None


def test_ev_url_from_raw() -> None:
    assert (
        hyperlinks.ev_url_from_raw("EV 23/2026")
        == "https://www.eduskunta.fi/valtiopaivaasiakirjat/EV+23/2026"
    )
    assert hyperlinks.ev_url_from_raw("LaVM 3/2026") is None


def test_maybe_link_off_returns_plain() -> None:
    assert hyperlinks.maybe_link("L 2026/269", "https://x.test", enabled=False) == "L 2026/269"


def test_maybe_link_no_url_returns_plain() -> None:
    assert hyperlinks.maybe_link("L 2026/269", None, enabled=True) == "L 2026/269"


def test_maybe_link_on_wraps() -> None:
    out = hyperlinks.maybe_link("L 2026/269", "https://x.test", enabled=True)
    assert out.startswith(OSC8)
    assert "L 2026/269" in out


# --- renderer integration: default-off is byte-identical, on adds escapes -------


def _statute_record() -> dict:
    return {
        "statute_id": "2011/805",
        "as_of": "2026-06-09",
        "amendment_count": 1,
        "he_resolved_count": 1,
        "amendments": [
            {
                "amendment_id": "2026/269",
                "commencement": {
                    "enacted": "2026-04-17",
                    "effective": "2026-06-01",
                    "legal_status": "commenced",
                    "title": "Laki esitutkintalain muuttamisesta",
                },
                "applied_in_replay": True,
                "preparatory_available": True,
                "originating_he": {
                    "he_id": "he/2025/188",
                    "title": "Hallituksen esitys",
                    "ministry": "Oikeusministeriö",
                    "date_issued": "2025-12-11",
                    "finlex_state": "pending",
                },
                "committee_refs": [
                    {"raw_text": "LaVM 3/2026", "canonical_id": "fi.committee.lavm.3.2026"}
                ],
                "parliament_response_refs": [
                    {"raw_text": "EV 23/2026", "canonical_id": "fi.ev.23.2026"}
                ],
            }
        ],
        "notes": [],
    }


def test_statute_human_default_off_byte_identical() -> None:
    record = _statute_record()
    off = provenance._render_statute_human(record, link=False)
    default = provenance._render_statute_human(record)  # default link=False
    assert off == default
    assert OSC8 not in off


def test_statute_human_on_has_escapes_for_all_ref_kinds() -> None:
    record = _statute_record()
    on = provenance._render_statute_human(record, link=True)
    assert OSC8 in on
    # header statute id -> consolidated (ajantasa) version
    assert "https://www.finlex.fi/fi/lainsaadanto/2011/805" in on
    # statute, HE, committee, EV all linked
    assert "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2026/269" in on
    assert "https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/HE%20188%2F2025%20vp" in on
    assert "https://www.eduskunta.fi/valtiopaivaasiakirjat/LaVM+3/2026" in on
    assert "https://www.eduskunta.fi/valtiopaivaasiakirjat/EV+23/2026" in on


def _section_record() -> dict:
    return {
        "statute_id": "2011/805",
        "selector": "§3:1",
        "as_of": "2026-06-09",
        "locator": "chapter:3/section:1",
        "query_type": "in_force",
        "in_force": {
            "in_force_status": "selected",
            "text": "3 § text",
            "available": True,
            "source_amendment": "2026/269",
        },
        "originating_he": {
            "he_id": "he/2025/188",
            "title": "Hallituksen esitys",
            "ministry": "Oikeusministeriö",
            "date_issued": "2025-12-11",
            "finlex_state": "pending",
            "enacted_law_surfaced": "2026/269",
        },
        "preparatory": [
            {"kind": "he", "raw_text": "HE 188/2025", "canonical_id": "he/2025/188"},
            {"kind": "committee_report", "raw_text": "LaVM 3/2026", "canonical_id": "fi.committee.lavm.3.2026"},
            {"kind": "parliament_response", "raw_text": "EV 23/2026", "canonical_id": "fi.ev.23.2026"},
        ],
        "commencement": {
            "effective": "2026-06-01",
            "enacted": "2026-05-01",
            "content_state": "live",
            "gate": "in_force",
        },
        "notes": [],
    }


def test_section_human_default_off_byte_identical() -> None:
    record = _section_record()
    off = provenance._render_human(record, link=False)
    default = provenance._render_human(record)
    assert off == default
    assert OSC8 not in off


def test_section_human_on_links_refs() -> None:
    record = _section_record()
    on = provenance._render_human(record, link=True)
    assert OSC8 in on
    # header statute id -> consolidated (ajantasa) version
    assert "https://www.finlex.fi/fi/lainsaadanto/2011/805" in on
    assert "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2026/269" in on
    assert "https://www.eduskunta.fi/asiat-ja-aanestykset/valtiopaivaasiat/HE%20188%2F2025%20vp" in on
    assert "https://www.eduskunta.fi/valtiopaivaasiakirjat/LaVM+3/2026" in on
    assert "https://www.eduskunta.fi/valtiopaivaasiakirjat/EV+23/2026" in on


# --- regression: JSON / non-tty path has NO OSC 8 bytes -------------------------


def test_json_output_has_no_osc8_bytes() -> None:
    record = _statute_record()
    # JSON serialization of the record must never carry escapes.
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert OSC8 not in blob


def test_main_json_path_emits_no_escapes(monkeypatch, capsys) -> None:
    record = _section_record()
    monkeypatch.setattr(provenance, "build_provenance", lambda **kw: record)
    args = SimpleNamespace(
        jurisdiction="fi",
        statute_id="2011/805",
        selector="§3:1",
        as_of="2026-06-09",
        query_type="in_force",
        data_dir="test-data",
        json=True,
        hyperlinks="always",  # even forced-on, JSON must stay escape-free
    )
    provenance.main(args)
    captured = capsys.readouterr()
    assert OSC8 not in captured.out
    # and it is valid JSON
    json.loads(captured.out)


def test_main_human_non_tty_emits_no_escapes(monkeypatch, capsys) -> None:
    record = _section_record()
    monkeypatch.setattr(provenance, "build_provenance", lambda **kw: record)
    # capsys replaces stdout with a non-tty buffer, so auto mode must stay off.
    args = SimpleNamespace(
        jurisdiction="fi",
        statute_id="2011/805",
        selector="§3:1",
        as_of="2026-06-09",
        query_type="in_force",
        data_dir="test-data",
        json=False,
        hyperlinks="auto",
    )
    provenance.main(args)
    captured = capsys.readouterr()
    assert OSC8 not in captured.out
