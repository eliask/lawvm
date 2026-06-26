"""Tests for the EE authority-grounding bridge.

These pin the contract that every external-keyed grounding row names a real
``ee_*`` _RULE constant in ``src/lawvm/estonia/``, that statuses /
authority_kinds are valid, that the loader round-trips the JSON, and that the
honesty invariant holds: an ``internal_spec`` row never claims an external
source.  See ``src/lawvm/estonia/ee_authority_grounding.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lawvm.estonia.ee_authority_grounding import (
    VALID_AUTHORITY_KINDS,
    VALID_STATUSES,
    EEAuthorityGrounding,
    load_ee_authority_grounding,
    render_ee_grounding_column,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GROUNDING_JSON = _REPO_ROOT / "data" / "ee" / "spec_authority_grounding.json"
_EE_PKG = _REPO_ROOT / "src" / "lawvm" / "estonia"


def _ee_rule_constants() -> set[str]:
    """Every ``ee_*`` string assigned to an ``_EE_*_RULE`` constant in EE code."""
    ids: set[str] = set()
    pat = re.compile(r'^_EE_[A-Z0-9_]*_RULE\s*=\s*(?:\(\s*)?"(ee_[a-z0-9_]+)"', re.M)
    for path in _EE_PKG.glob("*.py"):
        for m in pat.finditer(path.read_text(encoding="utf-8")):
            ids.add(m.group(1))
    return ids


def test_grounding_json_parses_and_is_object_with_groundings_list() -> None:
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert isinstance(raw["groundings"], list)
    assert raw["groundings"], "grounding map must not be empty"
    assert "_meta" in raw


def test_every_row_has_valid_status_and_authority_kind() -> None:
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    for row in raw["groundings"]:
        assert row["status"] in VALID_STATUSES, row
        assert row["authority_kind"] in VALID_AUTHORITY_KINDS, row
        assert isinstance(row["rule_id"], str) and row["rule_id"].startswith("ee_")
        assert isinstance(row["source_ref"], str)


def test_rule_ids_are_unique() -> None:
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    ids = [row["rule_id"] for row in raw["groundings"]]
    assert len(ids) == len(set(ids)), "duplicate rule_id in grounding file"


def test_every_external_keyed_rule_id_is_a_real_ee_constant() -> None:
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    constants = _ee_rule_constants()
    external_ids = [
        row["rule_id"]
        for row in raw["groundings"]
        if row["authority_kind"] == "external"
    ]
    for rule_id in external_ids:
        assert rule_id in constants, (
            f"external-keyed rule_id {rule_id!r} is not a real ee_* _RULE constant "
            f"in src/lawvm/estonia/"
        )


def test_every_keyed_rule_id_is_a_real_ee_constant() -> None:
    # Stronger than the external-only contract: today every grounded rule_id is a
    # real peg/grafter constant.  This guards against a rule_id typo silently
    # grounding nothing.
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    constants = _ee_rule_constants()
    for row in raw["groundings"]:
        assert row["rule_id"] in constants, row["rule_id"]


def test_loader_round_trips() -> None:
    table = load_ee_authority_grounding()
    raw = json.loads(_GROUNDING_JSON.read_text(encoding="utf-8"))
    assert set(table) == {row["rule_id"] for row in raw["groundings"]}
    for row in raw["groundings"]:
        entry = table[row["rule_id"]]
        assert isinstance(entry, EEAuthorityGrounding)
        assert entry.authority_grounding_status == row["status"]
        assert entry.authority_kind == row["authority_kind"]
        assert entry.source_ref == row["source_ref"]
        assert entry.ledger_section == row.get("ledger_section", "")
        assert entry.note == row.get("note", "")


def test_honesty_internal_spec_rows_do_not_claim_external_source() -> None:
    # An internal_spec row is grounded only in the EE living spec; it must cite a
    # living-spec source_ref and must not present itself as an external
    # drafting-authority grounding (no fabricated HÕNTE/RT citation).
    table = load_ee_authority_grounding()
    for entry in table.values():
        if entry.authority_kind == "internal_spec":
            assert entry.source_ref, entry.rule_id
            assert "ESTONIA_FRONTEND_LIVING_SPEC.md" in entry.source_ref, entry.rule_id
            assert entry.authority_kind != "external"


def test_loader_rejects_internal_spec_without_source_ref(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "groundings": [
                    {
                        "rule_id": "ee_unparsed_operation_clause",
                        "authority_kind": "internal_spec",
                        "source_ref": "",
                        "status": "SPEC",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_ee_authority_grounding(path=bad)


def test_loader_rejects_invalid_status(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "groundings": [
                    {
                        "rule_id": "ee_peale_sona_insert_after_synonym",
                        "authority_kind": "internal_spec",
                        "source_ref": "ESTONIA_FRONTEND_LIVING_SPEC.md §70",
                        "status": "MAYBE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_ee_authority_grounding(path=bad)


def test_render_column_for_known_and_unknown_rule() -> None:
    table = load_ee_authority_grounding()
    known = render_ee_grounding_column("ee_peale_sona_insert_after_synonym", table)
    assert "internal_spec" in known
    assert render_ee_grounding_column("ee_not_a_real_rule_xyz", table) == "-"
