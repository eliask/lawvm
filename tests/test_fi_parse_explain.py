"""Tests for the ``lawvm fi-parse-explain`` one-clause parse diagnostic.

Two layers, mirroring the census-accounting test gating:

* Structure (corpus-free, always run): the missing-source path returns a clean
  typed error record without touching the parser.

* Corpus-gated: on a known statute id the diagnostic record carries every
  contracted field (normalized text, parser_lane, OLD-vs-NEW comparison, the
  totality predicate, and — under --ops — the parsed op codes), and the JSON
  mode serializes. Skips cleanly when the canonical corpus is not linked.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from lawvm.tools.fi_parse_explain import _collect, main

KNOWN_SID = "2002/375"


# ---------------------------------------------------------------------------
# Structure (corpus-free, always run)
# ---------------------------------------------------------------------------
def test_missing_source_returns_typed_error() -> None:
    record = _collect("9999/99999", want_ops=False)
    assert record["statute_id"] == "9999/99999"
    assert "error" in record
    # No parser fields when there was nothing to parse.
    assert "parser_lane" not in record


# ---------------------------------------------------------------------------
# Corpus-gated
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


_SKIP = pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)


@_SKIP
def test_record_carries_all_contract_fields() -> None:
    record = _collect(KNOWN_SID, want_ops=True)
    assert record["statute_id"] == KNOWN_SID
    assert "error" not in record

    # (1) normalized johtolause text.
    assert record["johtolause"]
    assert "\n" not in record["johtolause"]
    assert "  " not in record["johtolause"]

    # (2) lane + decline reason fields present.
    assert record["parser_lane"] in (
        "grammar_owned",
        "legacy_reference_fallback",
        "old_parser_forced",
    )
    assert "grammar_decline_reason" in record
    assert "used_legacy_fallback" in record

    # (3) OLD-vs-NEW comparison present and well-formed.
    diff = record["old_vs_new"]
    assert isinstance(diff, dict)
    # Either a clean compare (equal/deltas) or a typed parse-status key.
    assert (
        "equal" in diff
        or "new_declined" in diff
        or "new_parse_error" in diff
        or "old_parse_error" in diff
    )

    # (4) totality predicate present.
    tot = record["totality"]
    assert isinstance(tot["n_ops"], int)
    assert isinstance(tot["flagged_drops"], list)

    # (5) --ops dumped the op codes.
    assert "parsed_ops" in record
    assert isinstance(record["parsed_ops"], list)


@_SKIP
def test_json_mode_serializes(capsys) -> None:
    args = argparse.Namespace(sid=KNOWN_SID, ops=False, json=True)
    main(args)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["statute_id"] == KNOWN_SID
    assert "parser_lane" in parsed
    assert "totality" in parsed


@_SKIP
def test_human_mode_prints_key_sections(capsys) -> None:
    args = argparse.Namespace(sid=KNOWN_SID, ops=True, json=False)
    main(args)
    out = capsys.readouterr().out
    assert "fi-parse-explain" in out
    assert "parser_lane" in out
    assert "OLD vs NEW surface model" in out
    assert "totality predicate" in out
    assert "parsed ops" in out
