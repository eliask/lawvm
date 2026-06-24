"""The COVERAGE GATE — "no public claim without a live accounting path".

This is the compounding deliverable of the claim-surface backbone (Pro doc
``pro_on_invariant_mining_and_more.md`` §13 step 1+3 + §4 forbidden-bucket
rule). It turns the per-capability hand-written boundary into a generated,
executable coverage gate over the DECLARED claim surface:

  * Every claim in the v0 :class:`ClaimSurfaceManifest` has ≥1
    :class:`InvariantSpec` citing it and terminating in an ALLOWED bucket — a
    live accounting path (a check / refusal / verifier / fixture) OR an explicit
    declared residual / non-guarantee / deferral / out-of-claim ruling.
  * ZERO invariants sit in the FORBIDDEN ``implicit_convention`` bucket.

HONESTY BOUNDARY (asserted here, not just in prose). v0 enumerates a DECLARED
SUBSET of LawVM's public claims (claim-relative, versioned —
``InvariantSet(spec_version, claim_surface_version)``), NOT all claims. It binds
to but does NOT auto-generate invariants from the finite-axis generator; fire-
drills (§13 step 4) and the MUST-trace linter (step 5) are future. The gate
asserts completeness RELATIVE TO the declared surface, never absolute.
"""

from __future__ import annotations

import pytest

from lawvm.core.claim_surface_manifest import (
    CLAIM_SURFACE_VERSION,
    ClaimSpec,
    ClaimSurfaceError,
    ClaimSurfaceManifest,
    V0_CLAIMS,
    v0_claim_surface_manifest,
)
from lawvm.core.invariant_spec import (
    ALLOWED_BUCKETS,
    ALL_BUCKETS,
    FORBIDDEN_BUCKET,
    InvariantSet,
    InvariantSpec,
    InvariantSpecError,
    V0_INVARIANTS,
    v0_invariant_set,
)


# --------------------------------------------------------------------------- #
# THE COVERAGE GATE                                                           #
# --------------------------------------------------------------------------- #


def test_every_v0_claim_has_a_live_accounting_path():
    """GATE: every declared claim has ≥1 invariant in an ALLOWED bucket.

    "No public claim without a live accounting path" made executable over the
    v0 declared claim surface.
    """
    manifest = v0_claim_surface_manifest()
    inv_set = v0_invariant_set()

    uncovered: list[str] = []
    for claim_id in manifest.claim_ids:
        invariants = inv_set.for_claim(claim_id)
        if not any(inv.is_allowed for inv in invariants):
            uncovered.append(claim_id)

    assert not uncovered, (
        "claims with NO live accounting path (no invariant in an ALLOWED bucket): "
        f"{uncovered!r} — every public claim must terminate in a check / refusal / "
        f"verifier / fixture / declared residual / non-guarantee / deferral"
    )


def test_zero_invariants_in_forbidden_implicit_convention_bucket():
    """GATE: ZERO invariants sit in the forbidden ``implicit_convention`` bucket (Pro §4)."""
    inv_set = v0_invariant_set()
    forbidden = inv_set.forbidden
    assert forbidden == (), (
        f"{len(forbidden)} invariant(s) in the FORBIDDEN {FORBIDDEN_BUCKET!r} bucket "
        f"— an implicit convention is the enemy: "
        f"{[inv.id for inv in forbidden]!r}"
    )


def test_every_invariant_cites_a_declared_claim():
    """No orphan invariant: every invariant's claim_id is in the declared manifest."""
    manifest = v0_claim_surface_manifest()
    inv_set = v0_invariant_set()
    declared = set(manifest.claim_ids)
    orphans = [inv.id for inv in inv_set.invariants if inv.claim_id not in declared]
    assert not orphans, (
        f"invariants citing a claim NOT in the declared surface: {orphans!r}"
    )


# --------------------------------------------------------------------------- #
# Wave-1 unification — the backbone visibly UNIFIES UniverseSpec / KNOW / AR.  #
# --------------------------------------------------------------------------- #


def test_wave1_objects_are_cited_as_first_class_claims():
    """UniverseSpec, KNOW, and AssumptionRegister are wired in as v0 claims/paths."""
    manifest = v0_claim_surface_manifest()
    inv_set = v0_invariant_set()

    # UniverseSpec bodies the materialization claim and commits the universe_root.
    mat = next(c for c in V0_CLAIMS if c.claim_id == "lawvm.fi.provision_state.selected.v1")
    assert "UniverseSpec" in mat.required_objects
    assert "universe_root" in mat.required_roots

    # KNOW family is a first-class claim with a live check.
    know = next(c for c in V0_CLAIMS if c.claim_id == "lawvm.know.source_monotonicity.v1")
    know_invs = inv_set.for_claim(know.claim_id)
    assert any(inv.id == "KNOW-01" and inv.is_allowed for inv in know_invs)

    # AssumptionRegister entries back the declared_non_guarantee invariants of
    # the bench claim (the Wave-1 non-guarantee objects).
    bench = next(c for c in V0_CLAIMS if c.claim_id == "lawvm.fi.bench.agreement_score.v1")
    assert bench.allowed_non_guarantees  # declared, not unstated
    bench_ng = [
        inv
        for inv in inv_set.for_claim(bench.claim_id)
        if inv.bucket == "declared_non_guarantee"
    ]
    assert bench_ng, "bench claim has no declared_non_guarantee accounting path"
    assert all("assumption_register" in inv.owner for inv in bench_ng)


def test_manifest_unifies_three_wave1_object_families():
    """All three Wave-1 object families appear as required_objects across v0 claims."""
    required_objects = {
        obj for claim in V0_CLAIMS for obj in claim.required_objects
    }
    assert "UniverseSpec" in required_objects  # materialization_totality
    assert "SourceObservation" in required_objects  # know_invariants
    # AssumptionRegister enters via allowed_non_guarantees (the declared-boundary
    # plane), not required_objects — assert it is reachable as a non-guarantee.
    non_guarantees = {
        ng for claim in V0_CLAIMS for ng in claim.allowed_non_guarantees
    }
    assert non_guarantees, "no declared non-guarantees wire in the AssumptionRegister plane"


# --------------------------------------------------------------------------- #
# Root commitment — adding/editing a claim or invariant moves the root.        #
# --------------------------------------------------------------------------- #


def test_manifest_root_is_deterministic_and_membership_sensitive():
    """manifest_root is stable and changes when the claim set changes."""
    m1 = v0_claim_surface_manifest()
    m2 = v0_claim_surface_manifest()
    assert m1.manifest_root == m2.manifest_root  # deterministic

    dropped = ClaimSurfaceManifest(V0_CLAIMS[:-1])
    assert dropped.manifest_root != m1.manifest_root  # dropping a claim moves it

    edited = ClaimSpec(
        claim_id=V0_CLAIMS[0].claim_id,
        public_sentence=V0_CLAIMS[0].public_sentence + " (edited)",
        required_objects=V0_CLAIMS[0].required_objects,
        required_roots=V0_CLAIMS[0].required_roots,
        allowed_non_guarantees=V0_CLAIMS[0].allowed_non_guarantees,
        checker_level=V0_CLAIMS[0].checker_level,
    )
    edited_manifest = ClaimSurfaceManifest((edited, *V0_CLAIMS[1:]))
    assert edited_manifest.manifest_root != m1.manifest_root  # editing moves it


def test_invariant_set_root_is_deterministic_and_membership_sensitive():
    s1 = v0_invariant_set()
    s2 = v0_invariant_set()
    assert s1.invariant_set_root == s2.invariant_set_root
    dropped = InvariantSet(V0_INVARIANTS[:-1])
    assert dropped.invariant_set_root != s1.invariant_set_root


def test_empty_manifest_has_a_valid_root():
    """An empty claim surface is a valid deterministic (empty) MapRoot."""
    empty = ClaimSurfaceManifest(())
    assert isinstance(empty.manifest_root, str)
    assert empty.manifest_root.startswith("sha256:")
    assert len(empty) == 0


# --------------------------------------------------------------------------- #
# Versioning honesty (Pro §12) — completeness is claim-relative + versioned.   #
# --------------------------------------------------------------------------- #


def test_invariant_set_carries_claim_relative_version_pair():
    """InvariantSet carries (spec_version, claim_surface_version) — never absolute."""
    inv_set = v0_invariant_set()
    assert inv_set.spec_version == "v0"
    assert inv_set.claim_surface_version == CLAIM_SURFACE_VERSION
    # The manifest declares the same surface version (the join is version-aware).
    assert v0_claim_surface_manifest().claim_surface_version == CLAIM_SURFACE_VERSION


# --------------------------------------------------------------------------- #
# Fail-loud validation (no silent acceptance of malformed declarations).       #
# --------------------------------------------------------------------------- #


def test_claim_spec_rejects_empty_public_sentence():
    with pytest.raises(ClaimSurfaceError):
        ClaimSpec(claim_id="x.y.v1", public_sentence="  ")


def test_claim_spec_rejects_bad_checker_level():
    with pytest.raises(ClaimSurfaceError):
        ClaimSpec(claim_id="x.y.v1", public_sentence="s", checker_level="L9")  # ty: ignore[invalid-argument-type]


def test_manifest_rejects_duplicate_claim_id():
    dup = ClaimSpec(claim_id="x.y.v1", public_sentence="s")
    with pytest.raises(ClaimSurfaceError):
        ClaimSurfaceManifest((dup, dup))


def test_invariant_spec_rejects_unknown_bucket():
    with pytest.raises(InvariantSpecError):
        InvariantSpec(
            id="X-01",
            claim_id="x.y.v1",
            plane="source",
            waist="w",
            unit_kind="per-unit",
            predicate="p",
            owner="o",
            bucket="made_up_bucket",  # ty: ignore[invalid-argument-type]
        )


def test_invariant_spec_rejects_empty_claim_id():
    with pytest.raises(InvariantSpecError):
        InvariantSpec(
            id="X-01",
            claim_id="",
            plane="source",
            waist="w",
            unit_kind="per-unit",
            predicate="p",
            owner="o",
            bucket="implemented_check",
        )


def test_bucket_taxonomy_is_the_pro_terminal_set():
    """The bucket vocabulary is exactly Pro §4's nine allowed + one forbidden."""
    assert FORBIDDEN_BUCKET == "implicit_convention"
    assert FORBIDDEN_BUCKET not in ALLOWED_BUCKETS
    assert ALL_BUCKETS == ALLOWED_BUCKETS | {FORBIDDEN_BUCKET}
    assert len(ALLOWED_BUCKETS) == 9


def test_forbidden_bucket_would_fail_the_gate_if_used():
    """A constructed implicit_convention invariant is detected as forbidden (drill)."""
    bad = InvariantSpec(
        id="BAD-01",
        claim_id="lawvm.fi.bench.agreement_score.v1",
        plane="source",
        waist="w",
        unit_kind="per-unit",
        predicate="assumed to hold by custom",
        owner="nobody",
        bucket="implicit_convention",
    )
    polluted = InvariantSet((*V0_INVARIANTS, bad))
    assert polluted.forbidden == (bad,)
    assert not bad.is_allowed and bad.is_forbidden
