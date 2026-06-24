"""Tests for the Sweden declared-assumption register (sibling of the FI one).

Mirrors ``tests/test_assumption_register.py``: the SE register converts SE's
data-ceiling facts (single-version oracle; reverse-patch non-invertibility;
archaeic cached acts) from prose in ``notes/SWEDEN_LAWVM_STATUS.md`` § Limits
into root-committed typed non-guarantees a checker can detect drift over.

The register is hand-curated v0 — see the SE module docstring honesty boundary.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.assumption_register import (
    AssumptionRegister,
    AssumptionRegisterError,
    assumption_register_root,
)
from lawvm.sweden.se_assumptions import build_se_assumption_register


# --------------------------------------------------------------------------- #
# The register builds and is well-formed.                                      #
# --------------------------------------------------------------------------- #


def test_se_register_builds_and_is_nonempty() -> None:
    register = build_se_assumption_register()
    assert len(register) >= 1
    assert all(isinstance(a, AssumptionRegister) for a in register)


def test_se_register_root_is_deterministic() -> None:
    assert assumption_register_root(
        build_se_assumption_register()
    ) == assumption_register_root(build_se_assumption_register())


def test_se_register_root_is_order_independent() -> None:
    # Set semantics — same as FI. The root is insensitive to argument order so
    # a checker comparing two register snapshots sees the same root for the same
    # declared set, regardless of how the caller built the tuple/list.
    register = list(build_se_assumption_register())
    forward = assumption_register_root(register)
    backward = assumption_register_root(list(reversed(register)))
    assert forward == backward


def test_se_register_assumption_ids_are_unique() -> None:
    # An assumption is declared once — set_root rejects duplicate ids.
    register = build_se_assumption_register()
    ids = [a.assumption_id for a in register]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Each entry encoding touchable.                                               #
# --------------------------------------------------------------------------- #


def test_se_single_version_oracle_assumption_is_well_formed() -> None:
    register = build_se_assumption_register()
    # Discriminate against the other two data-ceiling facts by the witness rule id.
    entry = next(
        a
        for a in register
        if a.witness_rule_id == "se_replay_base_surface_contains_post_amendment_targets"
    )
    assert entry.kind == "source_unavailable"
    assert entry.effect == "qualifies"
    # The entry names the load-bearing code surface so a future refactor can find it.
    assert "oracle_version_mismatch" in entry.scope or "version_mismatch" in entry.scope.lower()
    # public_message states the boundary honestly — "does NOT guarantee" appears.
    assert entry.public_message.strip()
    assert "does NOT guarantee" in entry.public_message
    # The status doc is cross-linked so prose and the typed assertion don't drift apart.
    assert any("SWEDEN_LAWVM_STATUS" in ref for ref in entry.finding_refs)


def test_se_reverse_patch_non_invertibility_assumption_is_well_formed() -> None:
    register = build_se_assumption_register()
    entry = next(
        a for a in register if a.witness_rule_id == "se_later_chain_reverse_op_exception"
    )
    # The mechanism is "doctrine_unresolved" — no discriminator in the source can
    # decide it (pre-amendment text physically absent). NOT "source_unavailable".
    assert entry.kind == "doctrine_unresolved"
    assert entry.effect == "qualifies"
    # The empirical 94.8% number is the load-bearing fact — quoted in scope/expires.
    assert "94.8%" in entry.scope
    # expires_when names the concrete inversion blocker, not hand-wave prose.
    assert "REPLACE" in entry.expires_when
    assert "REPEAL" in entry.expires_when
    # The reverse-chain scoping doc is cross-linked.
    assert any("SE_VERSION_AWARE_ORACLE_SCOPING" in ref for ref in entry.finding_refs)


def test_se_archaeic_cached_acts_assumption_is_well_formed() -> None:
    register = build_se_assumption_register()
    entry = next(
        a
        for a in register
        if a.witness_rule_id == "se_official_act_payload_row_duplicate_label"
    )
    assert entry.kind == "source_unavailable"
    assert entry.effect == "qualifies"
    # The ~5000-row magnitude is the load-bearing fact.
    assert "5000" in entry.scope
    # expires_when names the concrete re-ingest command, not "in the future".
    assert "fetch-official --force-reextract" in entry.expires_when


# --------------------------------------------------------------------------- #
# Validation parity with the FI/UK register shape.                            #
# --------------------------------------------------------------------------- #


def test_entry_validation_fails_loud_for_bad_kind() -> None:
    # Construction must reject bad kind — the closed vocabulary is load-bearing
    # for "did not check" (capability gap) vs "cannot check" (no discriminator)
    # triage per the core module docstring. ``kind="not_a_kind"`` is deliberately
    # outside the AssumptionKind Literal so it is cast through ``Any`` (mirrors
    # the FI ``_entry(**overrides)`` pattern that goes via a dict override).
    with pytest.raises(AssumptionRegisterError):
        AssumptionRegister(
            kind=cast(Any, "not_a_kind"),
            scope="scope",
            effect="qualifies",
            expires_when="a discriminator lands",
            public_message="boundary",
            witness_rule_id="some_witness",
        )


def test_entry_validation_fails_loud_for_blank_witness() -> None:
    # A blank witness_rule_id must be None, not "" — a non-blank-looking empty
    # string is folklore, not a declared assumption (mirrors FI build_fi test).
    with pytest.raises(AssumptionRegisterError):
        AssumptionRegister(
            kind="source_unavailable",
            scope="scope",
            effect="qualifies",
            expires_when="expires",
            public_message="boundary",
            witness_rule_id="   ",
        )
