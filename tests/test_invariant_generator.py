"""The GENERATIVE coverage gate — invariants generated from claim SHAPE.

This is the compounding spine (Pro doc ``pro_on_invariant_mining_and_more.md``
§13 step 1, "then generate invariants from claims"). Where
:mod:`tests.test_claim_surface_coverage` asserts "≥1 accounting path exists per
claim", THIS gate asserts the sharper claim-relative property: every invariant
KIND a claim's declared shape DEMANDS is actually discharged by an InvariantSpec
row. A new capability that declares a claim but forgets the per-unit / closure /
root / non-guarantee invariant its shape demands FAILS here — the generator
names the obligation it owes.

HONESTY BOUNDARY asserted (not just prose): the generator decides applicability
from a closed marker set over declared shape and discharge from invariant
profile; a discharged obligation means an invariant of the right KIND exists,
never that it is semantically correct. v0 ranges over the declared surface; the
deferred battery questions (CONSERVATION/DETERMINISM/PROVENANCE/GUARD_LIVENESS)
are declared, not silently skipped.
"""

from __future__ import annotations

import pytest

from lawvm.core.claim_surface_manifest import (
    ClaimSpec,
    ClaimSurfaceManifest,
    v0_claim_surface_manifest,
)
from lawvm.core.invariant_generator import (
    INVARIANT_GENERATOR_VERSION,
    UNDISCHARGED_OBLIGATION_FINDING,
    V0_BATTERY,
    GenerationResult,
    InvariantGeneratorError,
    generate_obligations,
    generate_v0_obligations,
)
from lawvm.core.invariant_spec import (
    InvariantSet,
    InvariantSpec,
    v0_invariant_set,
)


# --------------------------------------------------------------------------- #
# THE GENERATIVE GATE — the live v0 surface owes nothing undischarged.         #
# --------------------------------------------------------------------------- #


def test_v0_surface_has_zero_undischarged_obligations():
    """GATE: every invariant-kind the v0 claim surface demands is discharged.

    Adding a claim whose shape demands a per-unit / closure / authority / root /
    non-guarantee invariant WITHOUT adding that invariant breaks this — the
    generator surfaces the obligation it owes.
    """
    result = generate_v0_obligations()
    gaps = result.gaps
    assert gaps == (), (
        "the v0 claim surface has undischarged invariant obligations "
        "(its declared shape demands invariant kinds no InvariantSpec row "
        "provides):\n"
        + "\n".join(f"  - {g.claim_id} / {g.obligation_id}: {g.detail}" for g in gaps)
    )
    assert result.is_fully_discharged


def test_every_battery_question_fires_on_the_live_surface():
    """Each of the 5 active obligations is exercised ≥1× — no dead battery rule.

    A rule that never triggers over the real surface would be untested theatre;
    this proves every active axis does real work.
    """
    result = generate_v0_obligations()
    fired = {ob.obligation_id for ob in result.obligations}
    expected = {rule.obligation_id for rule in V0_BATTERY}
    assert fired == expected, (
        f"battery questions that never fired on the live surface: {expected - fired!r}"
    )


def test_obligations_carry_their_discharging_invariant_ids():
    """Each discharged obligation names the InvariantSpec row(s) that satisfy it."""
    result = generate_v0_obligations()
    for ob in result.obligations:
        assert ob.discharged, f"unexpected gap: {ob.claim_id}/{ob.obligation_id}"
        assert ob.discharged_by, "discharged obligation must name its invariant ids"
        assert ob.trigger, "every obligation records WHY its shape raised it"


# --------------------------------------------------------------------------- #
# The generator CAN find a real gap (its own fire-drill).                      #
# --------------------------------------------------------------------------- #


def test_generator_surfaces_a_totality_gap_drill():
    """A claim with totality language but NO per-unit invariant is a typed gap.

    The generator's fire-drill: it must be able to FIND a missing invariant, not
    just rubber-stamp the hand-authored surface.
    """
    leaky_claim = ClaimSpec(
        claim_id="lawvm.test.leaky_totality.v1",
        public_sentence=(
            "Every expected widget in the bundle is present — no expected widget "
            "is silently dropped."
        ),
        allowed_non_guarantees=("test_boundary",),
    )
    manifest = ClaimSurfaceManifest((leaky_claim,))
    # An invariant set that has ONLY a boundary invariant — no per-unit check.
    boundary_only = InvariantSpec(
        id="LEAKY-NG",
        claim_id="lawvm.test.leaky_totality.v1",
        plane="declaration",
        waist="claim_boundary",
        unit_kind="static",
        predicate="declared boundary",
        owner="test",
        bucket="declared_non_guarantee",
    )
    inv_set = InvariantSet((boundary_only,))
    result = generate_obligations(manifest, inv_set)

    gaps = result.gaps
    gap_kinds = {g.obligation_id for g in gaps}
    assert "TOTALITY" in gap_kinds, "totality language must raise an undischarged gap"
    assert all(g.finding_code == UNDISCHARGED_OBLIGATION_FINDING for g in gaps)
    # The non-guarantee obligation IS discharged (the boundary invariant exists).
    assert "NON_GUARANTEE_COVERAGE" not in gap_kinds
    assert not result.is_fully_discharged


def test_generator_surfaces_a_guard_liveness_gap_drill():
    """A claim whose enforcement finding_code is a RECORDED_DEAD guard is a gap.

    Embodies "no authority by proximity": a public claim may not rest on a guard
    that can never fire. Uses a real dead-guard code from the fire-drill registry.
    """
    from lawvm.core.fire_drill_registry import RECORDED_DEAD

    dead_code = sorted(RECORDED_DEAD)[0]  # a real recorded-dead guard
    claim = ClaimSpec(
        claim_id="lawvm.test.rests_on_dead_guard.v1",
        public_sentence="A claim enforced by a guard.",
        allowed_non_guarantees=("test_boundary",),
    )
    rests_on_dead = InvariantSpec(
        id="DEAD-CHK",
        claim_id="lawvm.test.rests_on_dead_guard.v1",
        plane="legal_state",
        waist="apply",
        unit_kind="per-unit",
        predicate="enforced by a guard that has no production call site",
        owner="test",
        bucket="implemented_check",
        finding_code=dead_code,
    )
    boundary = InvariantSpec(
        id="DEAD-NG",
        claim_id="lawvm.test.rests_on_dead_guard.v1",
        plane="declaration",
        waist="claim_boundary",
        unit_kind="static",
        predicate="declared boundary",
        owner="test",
        bucket="declared_non_guarantee",
    )
    result = generate_obligations(
        ClaimSurfaceManifest((claim,)), InvariantSet((rests_on_dead, boundary))
    )
    assert "GUARD_LIVENESS" in {g.obligation_id for g in result.gaps}

    # Swapping the dead code for a live (non-dead) finding_code discharges it.
    live_chk = InvariantSpec(
        id="LIVE-CHK",
        claim_id="lawvm.test.rests_on_dead_guard.v1",
        plane="legal_state",
        waist="apply",
        unit_kind="per-unit",
        predicate="enforced by a live guard",
        owner="test",
        bucket="implemented_check",
        finding_code="TEST.SOME_LIVE_FINDING",
    )
    ok = generate_obligations(
        ClaimSurfaceManifest((claim,)), InvariantSet((live_chk, boundary))
    )
    assert "GUARD_LIVENESS" not in {g.obligation_id for g in ok.gaps}


def test_generator_surfaces_a_root_commitment_gap_drill():
    """A claim naming a required_root with no committing invariant is a gap."""
    rootless_claim = ClaimSpec(
        claim_id="lawvm.test.rootless.v1",
        public_sentence="A bounded claim over a committed universe.",
        required_roots=("widget_universe_root",),
        allowed_non_guarantees=("test_boundary",),
    )
    manifest = ClaimSurfaceManifest((rootless_claim,))
    # An invariant that does NOT commit widget_universe_root.
    uncommitted = InvariantSpec(
        id="ROOTLESS-NG",
        claim_id="lawvm.test.rootless.v1",
        plane="declaration",
        waist="claim_boundary",
        unit_kind="static",
        predicate="declared boundary",
        owner="test",
        bucket="declared_non_guarantee",
        root_membership="",
    )
    result = generate_obligations(manifest, InvariantSet((uncommitted,)))
    assert "ROOT_COMMITMENT" in {g.obligation_id for g in result.gaps}


def test_root_commitment_discharged_when_every_root_committed():
    """The root obligation discharges only when EACH declared root is committed."""
    claim = ClaimSpec(
        claim_id="lawvm.test.rooted.v1",
        public_sentence="A bounded claim over a committed universe.",
        required_roots=("widget_universe_root",),
        allowed_non_guarantees=("test_boundary",),
    )
    committing = InvariantSpec(
        id="ROOTED-CHK",
        claim_id="lawvm.test.rooted.v1",
        plane="legal_state",
        waist="materialization",
        unit_kind="per-unit",
        predicate="per-unit check committed to the widget universe",
        owner="test",
        bucket="implemented_check",
        finding_code="WIDGET.DROPPED",
        root_membership="widget_universe_root",
    )
    boundary = InvariantSpec(
        id="ROOTED-NG",
        claim_id="lawvm.test.rooted.v1",
        plane="declaration",
        waist="claim_boundary",
        unit_kind="static",
        predicate="declared boundary",
        owner="test",
        bucket="declared_non_guarantee",
    )
    result = generate_obligations(
        ClaimSurfaceManifest((claim,)), InvariantSet((committing, boundary))
    )
    assert result.is_fully_discharged, [g.detail for g in result.gaps]


# --------------------------------------------------------------------------- #
# Determinism + committability (matches the rest of the backbone).             #
# --------------------------------------------------------------------------- #


def test_generation_is_deterministic():
    r1 = generate_v0_obligations()
    r2 = generate_v0_obligations()
    assert r1.obligation_set_root == r2.obligation_set_root
    assert [ (o.claim_id, o.obligation_id) for o in r1.obligations ] == [
        (o.claim_id, o.obligation_id) for o in r2.obligations
    ]


def test_obligation_root_is_membership_sensitive():
    full = generate_v0_obligations()
    fewer = GenerationResult(full.obligations[:-1])
    assert fewer.obligation_set_root != full.obligation_set_root


def test_generator_reports_its_version():
    assert generate_v0_obligations().generator_version == INVARIANT_GENERATOR_VERSION


# --------------------------------------------------------------------------- #
# Fail-loud — the join is version-aware (Pro §12).                             #
# --------------------------------------------------------------------------- #


def test_version_mismatch_between_manifest_and_invariant_set_raises():
    manifest = v0_claim_surface_manifest()
    mismatched = InvariantSet(
        v0_invariant_set().invariants,
        spec_version="v0",
        claim_surface_version="v-other",
    )
    with pytest.raises(InvariantGeneratorError):
        generate_obligations(manifest, mismatched)
