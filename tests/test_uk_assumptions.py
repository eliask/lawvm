"""Tests for the UK declared-assumption register (sibling of the FI/SE ones).

Mirrors ``tests/test_se_assumptions.py`` (which mirrors
``tests/test_assumption_register.py``): the UK register converts UK's four
data-ceiling facts from prose (in ``notes/UK_REPLAY_REGIME_CONTRACT.md`` § 6,
``notes/UK_HARD_CANARY_FRONTIER.md``, ``notes/MANUAL_COMPILATION_CLAIMS.md``,
agreement-clause references in ``AGENTS.md`` § 0) into root-committed typed
non-guarantees a checker can detect drift over.

The register is hand-curated v0 — see the UK module docstring honesty boundary.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.assumption_register import (
    AssumptionRegister,
    AssumptionRegisterError,
    assumption_register_root,
)
from lawvm.uk_legislation.uk_assumptions import build_uk_assumption_register


# --------------------------------------------------------------------------- #
# The register builds and is well-formed.                                      #
# --------------------------------------------------------------------------- #


def test_uk_register_builds_and_is_nonempty() -> None:
    register = build_uk_assumption_register()
    assert len(register) >= 1
    assert all(isinstance(a, AssumptionRegister) for a in register)


def test_uk_register_root_is_deterministic() -> None:
    assert assumption_register_root(
        build_uk_assumption_register()
    ) == assumption_register_root(build_uk_assumption_register())


def test_uk_register_root_is_order_independent() -> None:
    # Set semantics — same as FI/SE. The root is insensitive to argument order
    # so a checker comparing two register snapshots sees the same root for the
    # same declared set, regardless of how the caller built the tuple/list.
    register = list(build_uk_assumption_register())
    forward = assumption_register_root(register)
    backward = assumption_register_root(list(reversed(register)))
    assert forward == backward


def test_uk_register_assumption_ids_are_unique() -> None:
    # An assumption is declared once — set_root rejects duplicate ids.
    register = build_uk_assumption_register()
    ids = [a.assumption_id for a in register]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Each entry encoding touchable.                                              #
# --------------------------------------------------------------------------- #


def test_uk_oracle_alignment_lane_not_source_first_assumption_is_well_formed() -> None:
    register = build_uk_assumption_register()
    entry = next(
        a
        for a in register
        if a.witness_rule_id == "uk_oracle_eid_alignment_adapter"
    )
    # Capability gap (the lane is not built) — NOT doctrine_unresolved.
    assert entry.kind == "parser_incomplete"
    # The claim is QUALIFIED by the regime boundary, not outside claim scope.
    assert entry.effect == "qualifies"
    # The load-bearing lane name appears in scope so a future refactor can find it.
    assert "source_first_enacted_base" in entry.scope
    assert "oracle_alignment_adapter" in entry.scope
    # public_message states the boundary honestly — "does NOT guarantee" appears.
    assert entry.public_message.strip()
    assert "does NOT guarantee" in entry.public_message
    # The regime contract doc is cross-linked so prose and the typed assertion don't drift apart.
    assert any("UK_REPLAY_REGIME_CONTRACT" in ref for ref in entry.finding_refs)


def test_uk_devolved_extent_limited_repeal_assumption_is_well_formed() -> None:
    register = build_uk_assumption_register()
    entry = next(
        a
        for a in register
        if a.witness_rule_id
        == "uk_manual_frontier_devolved_extent_limited_repeal_out_of_scope"
    )
    # No compile-time discriminator in the source can decide the territorial
    # scope of a devolved whole-Act repeal until a territorial-extent model
    # is built — doctrine_unresolved, NOT a parser gap.
    assert entry.kind == "doctrine_unresolved"
    # The devolved extent slice is explicitly OUT OF CONTRACT, not a defect.
    assert entry.effect == "outside_claim"
    # The lowering guard rule id is named in scope so a refactor can find it.
    assert "uk_effect_devolved_whole_act_repeal_extent_limited_rejected" in entry.scope
    # expires_when names the concrete model that would unblock, not hand-wave.
    assert "territorial-extent" in entry.expires_when
    # A real-corpus or fixture test is cross-linked (per AGENTS.md §2.9).
    assert any(
        "test_uk_devolved_whole_act_repeal_extent" in ref for ref in entry.finding_refs
    )


def test_uk_manual_compilation_frontier_assumption_is_well_formed() -> None:
    register = build_uk_assumption_register()
    entry = next(
        a for a in register if a.witness_rule_id == "uk_manual_frontier_unclassified"
    )
    # AGENTS.md § 0 doctrine — the source does not deterministically specify
    # the result; no compile-time discriminator decides it.
    assert entry.kind == "doctrine_unresolved"
    # The claim stands but is QUALIFIED by the frontier (the row is a candidate,
    # not a compile failure).
    assert entry.effect == "qualifies"
    # The §0 over-retention/over-repeal framing is load-bearing — quoted.
    # Case-insensitive because the prose may start a sentence ("Over-retention ...").
    assert "over-retention" in entry.scope.lower() or "over-retention" in entry.public_message.lower()
    assert "over-repeal" in entry.scope.lower() or "over-repeal" in entry.public_message.lower()
    # AGENTS.md § 0 is cross-linked.
    assert any("AGENTS.md" in ref for ref in entry.finding_refs)
    # The promotion chain (the §0 proof boundary) is named in expires_when.
    assert "execution-authorization" in entry.expires_when or "promotion" in entry.expires_when


def test_uk_no_strict_profile_observation_default_assumption_is_well_formed() -> None:
    register = build_uk_assumption_register()
    entry = next(
        a
        for a in register
        if a.witness_rule_id
        == "uk_replay_materialization_totality_silent_drop_observed"
    )
    # Capability gap (no strict_profile built yet), NOT a doctrine debt.
    assert entry.kind == "parser_incomplete"
    # The claim is QUALIFIED, not out of scope: the probes fire observations,
    # they just don't enforce.
    assert entry.effect == "qualifies"
    # The observation-only posture is the load-bearing fact — named in scope.
    assert "observation" in entry.scope or "observed" in entry.scope
    assert "strict_profile" in entry.scope or "strict_profile" in entry.expires_when
    # The probes' module docstrings are cross-linked (the load-bearing source
    # of the "non-blocking `uk_replay_*_observed`" claim).
    assert any(
        "mutation_boundary_per_op_probe" in ref for ref in entry.finding_refs
    )
    assert any(
        "materialization_totality_probe" in ref for ref in entry.finding_refs
    )
    # The disabled-by-default production-lane test pin is cross-linked so a
    # future strict-profile rewrite cannot silently drop the assay without
    # touching these tests (mirrors the FI guard-liveness SOTA pattern).
    assert any(
        "test_probe_disabled_by_default" in ref for ref in entry.finding_refs
    )


# --------------------------------------------------------------------------- #
# Validation parity with the FI/SE register shape.                            #
# --------------------------------------------------------------------------- #


def test_entry_validation_fails_loud_for_bad_kind() -> None:
    # Construction must reject bad kind — the closed vocabulary is load-bearing
    # for "did not check" (capability gap) vs "cannot check" (no discriminator)
    # triage per the core module docstring. ``kind="not_a_kind"`` is deliberately
    # outside the AssumptionKind Literal so it is cast through ``Any`` (mirrors
    # the FI `_entry(**overrides)` pattern that goes via a dict override, and
    # the SE test_entry_validation_fails_loud_for_bad_kind pattern).
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
    # string is folklore, not a declared assumption (mirrors FI/SE build tests).
    with pytest.raises(AssumptionRegisterError):
        AssumptionRegister(
            kind="source_unavailable",
            scope="scope",
            effect="qualifies",
            expires_when="expires",
            public_message="boundary",
            witness_rule_id="   ",
        )
