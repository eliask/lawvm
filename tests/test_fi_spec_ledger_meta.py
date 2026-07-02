"""Coverage guard for the FI spec-ledger rule metadata (S/P sort + falsifier).

Companion to ``tests/test_fi_spec_ledger_catalog.py`` (which guards the believed_spec
coverage). This guards the §3.5 ``rule_role`` and §3.2(4) ``falsifier`` sidecars:

* every catalogued FI rule id carries an explicit ``rule_role`` (``"S"``|``"P"``) and a
  non-empty ``falsifier`` — no silent default-``S`` absorption of a new rule;
* no dead meta entry: every role/falsifier key is a real catalogued FI rule id;
* the sidecars agree with the discovered parse-witness surface (guards the "·"
  uncatalogued drift the analysis names — a new witness_rule_id without a catalog entry
  is flagged by the sibling believed_spec test, and here every catalogued id must also be
  role+falsifier annotated).
"""
from __future__ import annotations

from lawvm.finland.spec_ledger_adapter import (
    _FI_RULE_FALSIFIERS_FULL,
    _FI_RULE_ROLES_FULL,
    _FI_RULE_SPECS_FULL,
)
from lawvm.tools.spec_ledger_fi_catalog_meta import (
    _FI_RULE_FALSIFIERS,
    _FI_RULE_ROLES,
)


def test_every_cataloged_fi_rule_has_a_role() -> None:
    missing = sorted(set(_FI_RULE_SPECS_FULL) - set(_FI_RULE_ROLES_FULL))
    assert not missing, (
        f"{len(missing)} catalogued FI rule id(s) have no rule_role (S/P) annotation "
        f"in _FI_RULE_ROLES: {missing}"
    )


def test_every_cataloged_fi_rule_has_a_falsifier() -> None:
    missing = sorted(set(_FI_RULE_SPECS_FULL) - set(_FI_RULE_FALSIFIERS_FULL))
    assert not missing, (
        f"{len(missing)} catalogued FI rule id(s) have no falsifier sentence "
        f"in _FI_RULE_FALSIFIERS: {missing}"
    )


def test_no_dead_fi_role_entries() -> None:
    dead = sorted(set(_FI_RULE_ROLES) - set(_FI_RULE_SPECS_FULL))
    assert not dead, f"{len(dead)} _FI_RULE_ROLES key(s) are not catalogued rules: {dead}"


def test_no_dead_fi_falsifier_entries() -> None:
    dead = sorted(set(_FI_RULE_FALSIFIERS) - set(_FI_RULE_SPECS_FULL))
    assert not dead, (
        f"{len(dead)} _FI_RULE_FALSIFIERS key(s) are not catalogued rules: {dead}"
    )


def test_fi_roles_are_valid_s_or_p() -> None:
    bad = sorted(k for k, v in _FI_RULE_ROLES.items() if v not in ("S", "P"))
    assert not bad, f"FI rule roles must be 'S' or 'P'; invalid: {bad}"


def test_fi_falsifiers_are_non_empty() -> None:
    empty = sorted(k for k, v in _FI_RULE_FALSIFIERS.items() if not v or not v.strip())
    assert not empty, f"empty FI falsifier sentences: {empty}"


def test_fi_has_both_sorts_populated() -> None:
    # The whole point of the S/P bit is a real partition; both sides must be non-empty
    # (a fallback lane exists => P present; surface recognizers exist => S present).
    roles = set(_FI_RULE_ROLES.values())
    assert "S" in roles and "P" in roles


def test_fi_fallback_lane_is_a_p_rule() -> None:
    # The named fallback-extraction lane is the canonical compiler-survival P-rule.
    assert _FI_RULE_ROLES["fi.fallback_extraction_recovery"] == "P"


def test_fi_surface_recognizer_is_an_s_rule() -> None:
    # A johtolause surface recognizer is a hypothesis about the drafting language (S).
    assert _FI_RULE_ROLES["fi.insertion_section"] == "S"
