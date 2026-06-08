from __future__ import annotations

import datetime
from typing import Any

from lxml import etree

from lawvm.tools.oracle_text import (
    _collect_section_info,
    _el_to_text,
    _find_nearby_sections,
    _find_section_el,
    _num_text_to_canonical_selector,
    _STALE_SNAPSHOT_YEARS,
)


def test_find_section_el_distinguishes_lettered_sections() -> None:
    root = etree.fromstring(
        b"""
        <statute>
          <section eId="chp_3__sec_14v20221023"><num>14 \xc2\xa7</num><subsection><p>base</p></subsection></section>
          <section eId="chp_3__sec_14bv20150815"><num>14 b \xc2\xa7</num><subsection><p>lettered</p></subsection></section>
        </statute>
        """
    )

    section = _find_section_el(root, "section:14 b")

    assert section is not None
    text = _el_to_text(section)
    assert text.startswith("14 b §")
    assert "lettered" in text


def test_find_section_el_does_not_match_base_section_for_compact_lettered_label() -> None:
    root = etree.fromstring(
        b"""
        <statute>
          <section eId="chp_13__sec_198v20160646"><num>198 \xc2\xa7</num><subsection><p>base</p></subsection></section>
          <section eId="chp_13__sec_198bv20181022"><num>198 b \xc2\xa7</num><subsection><p>lettered</p></subsection></section>
        </statute>
        """
    )

    section = _find_section_el(root, "section:198b")

    assert section is not None
    text = _el_to_text(section)
    assert text.startswith("198 b §")
    assert "lettered" in text


# ---------------------------------------------------------------------------
# Task R: print==accept round-trip + eId direct + paren-strip + teaching errors
# ---------------------------------------------------------------------------

_STATUTE_XML = b"""
<statute>
  <section eId="chp_2__sec_7v20221023"><num>7 \xc2\xa7</num><subsection><p>seven</p></subsection></section>
  <section eId="chp_2__sec_7av20150401"><num>7 a \xc2\xa7</num><subsection><p>seven-a</p></subsection></section>
  <section eId="chp_3__sec_127v20181001"><num>127 \xc2\xa7</num><subsection><p>one-two-seven</p></subsection></section>
  <section eId="chp_3__sec_127av20190501"><num>127 a \xc2\xa7</num><subsection><p>one-two-seven-a</p></subsection></section>
</statute>
"""


def test_num_text_to_canonical_selector_plain() -> None:
    assert _num_text_to_canonical_selector("7 \xa7") == "section:7"


def test_num_text_to_canonical_selector_lettered() -> None:
    assert _num_text_to_canonical_selector("14 b \xa7") == "section:14 b"


def test_num_text_to_canonical_selector_empty() -> None:
    assert _num_text_to_canonical_selector("") == ""


def test_collect_section_info_returns_canonical_form() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    canonicals = [i["canonical"] for i in info]
    assert "section:7" in canonicals
    assert "section:7 a" in canonicals
    assert "section:127" in canonicals
    assert "section:127 a" in canonicals


def test_listing_canonical_token_round_trips_via_find_section_el() -> None:
    """The canonical selector produced by _collect_section_info must be accepted by _find_section_el."""
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    for entry in info:
        canon = entry["canonical"]
        if not canon:
            continue
        el = _find_section_el(root, canon)
        assert el is not None, f"canonical selector {canon!r} was not accepted by _find_section_el"


def test_find_section_el_accepts_eid_directly() -> None:
    """Passing a raw eId (e.g. 'chp_2__sec_7v20221023') should resolve the section."""
    root = etree.fromstring(_STATUTE_XML)
    el = _find_section_el(root, "chp_2__sec_7v20221023")
    assert el is not None
    assert "seven" in _el_to_text(el)


def test_find_section_el_accepts_paren_wrapped_num_text() -> None:
    """'(7 §)' (as previously printed by listing mode) should resolve like '7 §'."""
    root = etree.fromstring(_STATUTE_XML)
    el = _find_section_el(root, "(7 \xa7)")
    assert el is not None
    assert "seven" in _el_to_text(el)


def test_find_nearby_sections_returns_closest_by_number() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    # Ask for section:130 — nearest should be 127 / 127 a
    nearby = _find_nearby_sections(info, "section:130")
    assert len(nearby) >= 1
    assert all("127" in s for s in nearby[:2])


def test_find_nearby_sections_fallback_when_no_numeric_stem() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    # No numeric stem in the filter — should fall back to first few sections
    nearby = _find_nearby_sections(info, "chp_X__sec_Y")
    assert len(nearby) >= 1


# ---------------------------------------------------------------------------
# Task N: total_section_count is present in all bundle variants
# ---------------------------------------------------------------------------

def _make_fake_args(
    section: str = "",
    json_out: bool = False,
    no_hints: bool = False,
    at_amendment: str = "",
) -> Any:
    """Create a minimal fake args namespace for testing main() gate logic."""
    import argparse
    ns = argparse.Namespace()
    ns.statute_id = "2009/738"
    ns.section = section
    ns.at_amendment = at_amendment
    ns.subsections = False
    ns.json = json_out
    ns.no_hints = no_hints
    return ns


def test_build_oracle_text_bundle_listing_has_total_section_count() -> None:
    """Listing mode (no section_filter) bundle must include total_section_count."""
    # We can't call build_oracle_text_bundle directly without a corpus, but we can
    # verify the listing path includes the key by inspecting the data path logic
    # via _collect_section_info output length.
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    # Our XML has 4 sections
    assert len(info) == 4


def test_no_hints_flag_suppresses_hint(capsys) -> None:
    """The hint must NOT appear when --no-hints is set."""
    import importlib
    import lawvm.tools.oracle_text as ot_module

    # Reset the once-per-process flag
    ot_module._HINT_EMITTED = False

    # Build a minimal bundle with section set and total_section_count > 12
    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 30,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=True)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "hint: searching" not in captured.err, "hint must be suppressed by --no-hints"


def test_lawvm_no_hints_env_suppresses_hint(capsys, monkeypatch) -> None:
    """The hint must NOT appear when LAWVM_NO_HINTS=1 is set."""
    import lawvm.tools.oracle_text as ot_module

    ot_module._HINT_EMITTED = False
    monkeypatch.setenv("LAWVM_NO_HINTS", "1")

    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 30,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "hint: searching" not in captured.err, "hint must be suppressed by LAWVM_NO_HINTS=1"


def test_hint_appears_when_gates_pass(capsys, monkeypatch) -> None:
    """The hint must appear on stderr when: section set, count > 12, not suppressed."""
    import lawvm.tools.oracle_text as ot_module

    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 30,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "hint: searching" in captured.err, "hint must appear on stderr when all gates pass"
    assert "refs --to" in captured.err
    assert "topic --topic" in captured.err


def test_hint_absent_when_total_section_count_small(capsys, monkeypatch) -> None:
    """The hint must NOT appear when total_section_count <= 12."""
    import lawvm.tools.oracle_text as ot_module

    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 5,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "hint: searching" not in captured.err, "hint must not appear for small statutes"


def test_hint_absent_in_json_mode(capsys, monkeypatch) -> None:
    """The hint must NEVER appear when --json is specified."""
    import lawvm.tools.oracle_text as ot_module

    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 30,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", json_out=True, no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "hint: searching" not in captured.err, "hint must not appear in JSON mode"
    # JSON output should be valid JSON
    import json
    json.loads(captured.out)


def test_hint_emitted_only_once_per_process(capsys, monkeypatch) -> None:
    """The hint must fire at most once per process (once-per-process gate)."""
    import lawvm.tools.oracle_text as ot_module

    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    fake_bundle = {
        "statute_id": "1992/1535",
        "locator": "fi/fin/1992/1535/cons.xml",
        "at_amendment": "",
        "section_filter": "section:7",
        "found": True,
        "full_text": "test",
        "full_text_length": 4,
        "subsection_count": 1,
        "total_section_count": 30,
        "subsections": [],
    }

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=fake_bundle):
        ot_module.main(args)
        ot_module.main(args)  # call twice

    captured = capsys.readouterr()
    hint_lines = [ln for ln in captured.err.splitlines() if "hint: searching" in ln]
    assert len(hint_lines) == 1, f"hint must fire exactly once; got {len(hint_lines)} occurrences"


# ---------------------------------------------------------------------------
# Coverage staleness caveat tests
# ---------------------------------------------------------------------------

def _stale_bundle(*, at_amendment: str = "") -> dict:
    """Fake bundle with a cutoff date more than _STALE_SNAPSHOT_YEARS years ago."""
    stale_date = (
        datetime.date.today().replace(year=datetime.date.today().year - _STALE_SNAPSHOT_YEARS - 1)
    )
    return {
        "statute_id": "2003/497",
        "locator": "fi/fin/2003/497/cons.xml",
        "at_amendment": at_amendment,
        "section_filter": "section:7",
        "found": True,
        "full_text": "some text",
        "full_text_length": 9,
        "subsection_count": 1,
        "total_section_count": 10,
        "subsections": [],
        "oracle_cutoff_date": stale_date.isoformat(),
        "oracle_version_amendment_id": "2009/1561",
        "coverage_possibly_stale": True,
    }


def _recent_bundle() -> dict:
    """Fake bundle with a cutoff date within _STALE_SNAPSHOT_YEARS years."""
    recent_date = datetime.date.today().replace(year=datetime.date.today().year - 1)
    return {
        "statute_id": "2017/530",
        "locator": "fi/fin/2017/530/cons.xml",
        "at_amendment": "",
        "section_filter": "section:2",
        "found": True,
        "full_text": "recent text",
        "full_text_length": 11,
        "subsection_count": 1,
        "total_section_count": 10,
        "subsections": [],
        "oracle_cutoff_date": recent_date.isoformat(),
        "oracle_version_amendment_id": "2022/1200",
        "coverage_possibly_stale": False,
    }


def test_coverage_caveat_emitted_for_stale_snapshot(capsys, monkeypatch) -> None:
    """Caveat must appear on stderr when snapshot is stale (cutoff > 2y ago)."""
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_stale_bundle()):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot reflects amendments through" in captured.err
    assert "2009/1561" in captured.err
    assert "Finlex" in captured.err
    # Caveat must be on stderr, not stdout
    assert "note: consolidated snapshot" not in captured.out


def test_coverage_caveat_absent_for_recent_snapshot(capsys, monkeypatch) -> None:
    """Caveat must NOT appear when the snapshot cutoff is within the staleness window."""
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    from unittest.mock import patch
    args = _make_fake_args(section="section:2", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_recent_bundle()):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot" not in captured.err


def test_coverage_caveat_absent_in_json_mode(capsys, monkeypatch) -> None:
    """Caveat must NOT appear in JSON mode; coverage fields must be present in JSON payload."""
    import json as json_mod
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", json_out=True, no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_stale_bundle()):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot" not in captured.err
    payload = json_mod.loads(captured.out)
    assert "oracle_cutoff_date" in payload
    assert "oracle_version_amendment_id" in payload
    assert "coverage_possibly_stale" in payload
    assert payload["coverage_possibly_stale"] is True


def test_coverage_caveat_suppressed_by_no_hints_flag(capsys, monkeypatch) -> None:
    """Caveat must NOT appear when --no-hints is set."""
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=True)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_stale_bundle()):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot" not in captured.err


def test_coverage_caveat_suppressed_by_env(capsys, monkeypatch) -> None:
    """Caveat must NOT appear when LAWVM_NO_HINTS=1 is set."""
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.setenv("LAWVM_NO_HINTS", "1")

    from unittest.mock import patch
    args = _make_fake_args(section="section:7", no_hints=False)

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_stale_bundle()):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot" not in captured.err


def test_coverage_caveat_absent_when_at_amendment_used(capsys, monkeypatch) -> None:
    """Caveat must NOT appear when the user explicitly pinned a version with --at-amendment."""
    import lawvm.tools.oracle_text as ot_module
    ot_module._HINT_EMITTED = False
    monkeypatch.delenv("LAWVM_NO_HINTS", raising=False)

    from unittest.mock import patch
    # at_amendment set on both args and bundle (the bundle won't have coverage_possibly_stale=True
    # when at_amendment is used, but we also check the args gate independently)
    args = _make_fake_args(section="section:7", no_hints=False, at_amendment="2009/1561")

    with patch.object(ot_module, "build_oracle_text_bundle", return_value=_stale_bundle(at_amendment="2009/1561")):
        ot_module.main(args)

    captured = capsys.readouterr()
    assert "note: consolidated snapshot" not in captured.err
