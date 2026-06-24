"""Tests for the AssumptionRegister credibility-layer object + the FI register."""

from __future__ import annotations

import pytest

from lawvm.core.assumption_register import (
    AssumptionRegister,
    AssumptionRegisterError,
    assumption_register_root,
)
from lawvm.finland.fi_assumptions import build_fi_assumption_register


def _entry(**overrides: object) -> AssumptionRegister:
    base: dict[str, object] = {
        "kind": "doctrine_unresolved",
        "scope": "some scope locator",
        "effect": "qualifies",
        "expires_when": "a discriminator lands",
        "public_message": "we do not guarantee X",
        "witness_rule_id": "some_witness",
        "finding_refs": ("tests/foo.py::test_bar",),
    }
    base.update(overrides)
    return AssumptionRegister(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Root commitment determinism.                                                #
# --------------------------------------------------------------------------- #


def test_root_is_deterministic_for_same_assumptions() -> None:
    a = [_entry(scope="a"), _entry(scope="b")]
    assert assumption_register_root(a) == assumption_register_root(list(a))


def test_root_is_order_independent_set_semantics() -> None:
    a = _entry(scope="a")
    b = _entry(scope="b")
    assert assumption_register_root([a, b]) == assumption_register_root([b, a])


def test_root_changes_when_assumption_added() -> None:
    base = [_entry(scope="a")]
    more = [_entry(scope="a"), _entry(scope="b")]
    assert assumption_register_root(base) != assumption_register_root(more)


def test_root_changes_when_assumption_dropped() -> None:
    full = [_entry(scope="a"), _entry(scope="b")]
    dropped = [_entry(scope="a")]
    assert assumption_register_root(full) != assumption_register_root(dropped)


def test_root_changes_when_assumption_edited() -> None:
    before = [_entry(public_message="we do not guarantee X")]
    after = [_entry(public_message="we do not guarantee Y")]
    assert assumption_register_root(before) != assumption_register_root(after)


def test_empty_register_root_is_well_defined() -> None:
    # Mirrors set_root over empty — the v0 "declares nothing" case is committed to.
    assert assumption_register_root([]).startswith("sha256:")


def test_assumption_id_is_content_addressed() -> None:
    assert _entry(scope="a").assumption_id == _entry(scope="a").assumption_id
    assert _entry(scope="a").assumption_id != _entry(scope="b").assumption_id


# --------------------------------------------------------------------------- #
# Validation fails loud.                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "not_a_kind"},
        {"effect": "not_an_effect"},
        {"scope": ""},
        {"scope": "   "},
        {"expires_when": ""},
        {"public_message": ""},
        {"witness_rule_id": "  "},
    ],
)
def test_validation_fails_loud(overrides: dict[str, object]) -> None:
    with pytest.raises(AssumptionRegisterError):
        _entry(**overrides)


def test_witness_rule_id_may_be_none() -> None:
    assert _entry(witness_rule_id=None).witness_rule_id is None


# --------------------------------------------------------------------------- #
# The B2 entry is well-formed.                                                 #
# --------------------------------------------------------------------------- #


def test_fi_register_builds_and_is_nonempty() -> None:
    register = build_fi_assumption_register()
    assert len(register) >= 1
    assert all(isinstance(a, AssumptionRegister) for a in register)


def test_fi_register_root_is_deterministic() -> None:
    assert assumption_register_root(
        build_fi_assumption_register()
    ) == assumption_register_root(build_fi_assumption_register())


def test_b2_entry_is_well_formed() -> None:
    register = build_fi_assumption_register()
    b2 = next(
        a
        for a in register
        if "test_source_body_scope_overrides_prior_repeal_reinstatement_address"
        in a.scope
    )
    assert b2.kind == "doctrine_unresolved"
    assert b2.effect == "qualifies"
    # expires_when names the concrete source-body-wins anchor AND the 1973/36 discriminator.
    assert "2016/773 §148" in b2.expires_when
    assert "1973/36" in b2.expires_when
    # public_message states the boundary honestly.
    assert b2.public_message.strip()
    assert "does NOT guarantee" in b2.public_message
    assert b2.witness_rule_id == "fi_reinstated_section_scope_from_prior_repeal_address"
    assert any(
        "test_source_body_scope_overrides_prior_repeal_reinstatement_address" in ref
        for ref in b2.finding_refs
    )
