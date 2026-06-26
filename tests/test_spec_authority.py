"""Tests for the UK authority-grounding bridge (Stream C).

Validates that the mined ``data/uk/spec_authority_grounding.json`` parses into
frozen ``AuthorityGrounding`` rows with honest, well-formed fields, that the
loader round-trips, and (best-effort) that every ``witness_rule_id``-keyed entry
names a real string constant in ``src/lawvm/uk_legislation/``.  ``guidance_family``
entries are guidance-section family keys with no single named constant; they are
asserted to be clearly marked as such rather than silently failing the constant
check.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from lawvm.tools.spec_authority import (
    VALID_KEY_KINDS,
    VALID_STATUSES,
    AuthorityGrounding,
    _grounding_path,
    load_uk_authority_grounding,
    render_grounding_column,
)

_UK_LEGISLATION_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "lawvm" / "uk_legislation"
)


def test_grounding_json_parses_and_is_nonempty():
    grounding = load_uk_authority_grounding()
    assert grounding, "expected at least one grounded rule"
    for entry in grounding.values():
        assert isinstance(entry, AuthorityGrounding)


def test_every_entry_has_valid_status_key_kind_and_source_ref():
    grounding = load_uk_authority_grounding()
    for rule_id, entry in grounding.items():
        assert entry.authority_status in VALID_STATUSES, (rule_id, entry.authority_status)
        assert entry.key_kind in VALID_KEY_KINDS, (rule_id, entry.key_kind)
        assert entry.source_ref.strip(), f"empty source_ref for {rule_id}"
        # authority_tier is int or a split-tier string like "1/2"; never empty.
        assert isinstance(entry.authority_tier, (int, str))
        if isinstance(entry.authority_tier, str):
            assert entry.authority_tier.strip()


def test_loader_round_trips_against_raw_json():
    raw = json.loads(_grounding_path().read_text(encoding="utf-8"))
    grounding = load_uk_authority_grounding()
    raw_rows = raw["groundings"]
    assert len(grounding) == len(raw_rows), "loader dropped or merged a row"
    for row in raw_rows:
        entry = grounding[row["rule_id"]]
        assert entry.authority_tier == row["authority_tier"]
        assert entry.source_ref == row["source_ref"]
        assert entry.authority_status == row["status"]
        assert entry.key_kind == row.get("key_kind", "witness_rule_id")


def test_no_duplicate_rule_ids():
    raw = json.loads(_grounding_path().read_text(encoding="utf-8"))
    ids = [row["rule_id"] for row in raw["groundings"]]
    assert len(ids) == len(set(ids)), "duplicate rule_id in grounding file"


def _uk_legislation_source_blob() -> str:
    parts = []
    for py in sorted(_UK_LEGISLATION_DIR.glob("*.py")):
        parts.append(py.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_witness_rule_ids_correspond_to_real_constants():
    """Best-effort: each witness_rule_id-keyed entry names a real string literal
    in the UK frontend.  Family keys are skipped but asserted to be marked."""
    grounding = load_uk_authority_grounding()
    blob = _uk_legislation_source_blob()
    checked = 0
    for rule_id, entry in grounding.items():
        if entry.key_kind == "guidance_family":
            # Family keys are guidance-section refs, not constants; skipped here
            # and validated as clearly-marked in the dedicated family test.
            continue
        assert entry.key_kind == "witness_rule_id"
        # The exact quoted string literal must appear as a constant in the source.
        assert re.search(rf'["\']{re.escape(rule_id)}["\']', blob), (
            f"witness_rule_id {rule_id!r} not found as a constant in "
            "src/lawvm/uk_legislation/"
        )
        checked += 1
    assert checked >= 1, "expected at least one witness_rule_id-keyed entry"


def test_family_keys_are_marked_and_not_pretending_to_be_constants():
    grounding = load_uk_authority_grounding()
    family = [e for e in grounding.values() if e.key_kind == "guidance_family"]
    assert family, "expected guidance-family entries in the mined map"
    for entry in family:
        # A family key must carry a source_ref to a guidance section; it is the
        # honest stand-in for an unnamed/partial rule.
        assert entry.source_ref.strip()
        assert entry.ledger_section.strip()


def test_grounding_is_frozen():
    grounding = load_uk_authority_grounding()
    entry = next(iter(grounding.values()))
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.rule_id = "mutated"  # ty: ignore[invalid-assignment]


def test_render_grounding_column_for_known_and_unknown():
    grounding = load_uk_authority_grounding()
    known = next(iter(grounding))
    rendered = render_grounding_column(known, grounding)
    assert grounding[known].authority_status in rendered
    assert grounding[known].source_ref in rendered
    assert render_grounding_column("definitely_not_a_rule_id", grounding) == "-"


def test_honest_grounding_keeps_gap_and_spec_statuses():
    """The point of the bridge is faithful grounding: GAP/SPEC rules from the
    note must survive as GAP/SPEC, not be inflated to HAVE."""
    grounding = load_uk_authority_grounding()
    statuses = {e.authority_status for e in grounding.values()}
    assert "GAP" in statuses
    assert "SPEC" in statuses
    assert "HAVE" in statuses
