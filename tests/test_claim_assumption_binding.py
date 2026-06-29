"""The PER-HANDLE coverage gate — every declared non-guarantee resolves.

This closes the boundary the invariant generator explicitly DEFERRED. The
``NON_GUARANTEE_COVERAGE`` battery question in
:mod:`lawvm.core.invariant_generator` checks only that ≥1 boundary invariant
exists PER CLAIM; it does NOT resolve each declared handle to a registered
assumption. :mod:`lawvm.core.claim_assumption_binding` provides that binding, and
THIS gate asserts the sharper property: every ``allowed_non_guarantee`` handle in
EVERY :data:`~lawvm.core.claim_surface_manifest.V0_CLAIMS` claim resolves to
exactly one registered :class:`~lawvm.core.assumption_register.AssumptionRegister`
entry. A NEW handle with no binding FAILS here.

HONESTY BOUNDARY (asserted here, not just in prose). A resolved binding asserts
the handle is a DECLARED, registered non-guarantee with a real kind / effect /
public_message — it does NOT assert the boundary is complete, the gap harmless,
or the registered assumption the full story (resolution proves a declared
assumption EXISTS for the handle, not that it is true/minimal/exhaustive). v0
covers exactly the ``V0_CLAIMS`` handles (claim-relative, VERSIONED — Pro §12);
expanding the claim surface regenerates the binding set.
"""

from __future__ import annotations

import re

from lawvm.core.assumption_register import (
    ASSUMPTION_EFFECTS,
    ASSUMPTION_KINDS,
    AssumptionRegister,
)
from lawvm.core.claim_assumption_binding import (
    V0_CLAIM_ASSUMPTION_BINDINGS,
    ClaimAssumptionBinding,
    bound_handles,
    claim_assumption_binding_root,
    resolve_non_guarantee,
)
from lawvm.core.claim_surface_manifest import V0_CLAIMS

# Implementation-level Finland module path literal — the W7 M13 lift
# (iter2 arch review MEDIUM-2) replaces these inside the v0 binding set's
# scope/public_message text with the concept-id form `fi.frontend.<concept>`
# so that renaming a frontend module no longer silently drifts assumption
# bindings. A future re-paste of a `lawvm.finland.X.Y` literal into a binding's
# scope re-opens the §2.3 leak — this regex pins the lift-invariant at the
# binding-set level (the file-level firewall lives in
# tests/test_core_firewall_no_finland_module_paths.py).
_FINLAND_MODPATH_RE = re.compile(r"lawvm\.finland\.[A-Za-z_][A-Za-z_0-9.]*")


def _declared_handles() -> set[str]:
    return {h for claim in V0_CLAIMS for h in claim.allowed_non_guarantees}


# --------------------------------------------------------------------------- #
# THE PER-HANDLE COVERAGE GATE                                                 #
# --------------------------------------------------------------------------- #


def test_every_v0_handle_resolves_to_exactly_one_binding():
    """GATE: every allowed_non_guarantee handle resolves to one registered assumption.

    The deferred per-handle boundary closed: a declared handle that resolves to
    NOTHING (a NEW handle with no binding) FAILS this.
    """
    unresolved: list[str] = []
    for claim in V0_CLAIMS:
        for handle in claim.allowed_non_guarantees:
            if resolve_non_guarantee(handle) is None:
                unresolved.append(f"{claim.claim_id} / {handle}")
    assert not unresolved, (
        "declared non-guarantee handles with NO registered assumption "
        f"(the per-handle coverage gap): {unresolved!r} — every handle a claim "
        f"declares must bind to exactly one AssumptionRegister entry"
    )


def test_each_handle_binds_to_exactly_one_binding():
    """No handle is bound twice (the index rejects a duplicate handle)."""
    handles = [b.handle for b in V0_CLAIM_ASSUMPTION_BINDINGS]
    assert len(handles) == len(set(handles)), (
        f"a non-guarantee handle is bound more than once: "
        f"{sorted({h for h in handles if handles.count(h) > 1})!r}"
    )


def test_no_orphan_binding():
    """Every binding's handle is referenced by some V0_CLAIMS claim (no orphan)."""
    declared = _declared_handles()
    orphans = sorted(bound_handles() - declared)
    assert not orphans, (
        f"bindings whose handle no declared claim references (orphans): {orphans!r}"
    )


def test_binding_set_exactly_covers_the_declared_surface():
    """The bound handle set is EXACTLY the declared-handle set (no gap, no orphan)."""
    assert bound_handles() == _declared_handles()


# --------------------------------------------------------------------------- #
# Each bound assumption is WELL-FORMED.                                        #
# --------------------------------------------------------------------------- #


def test_every_bound_assumption_is_well_formed():
    """Each binding's AssumptionRegister constructs and carries a real kind/effect."""
    for binding in V0_CLAIM_ASSUMPTION_BINDINGS:
        assert isinstance(binding, ClaimAssumptionBinding)
        a = binding.assumption
        assert isinstance(a, AssumptionRegister)
        # A real, in-vocabulary kind/effect (not a placeholder).
        assert a.kind in ASSUMPTION_KINDS
        assert a.effect in ASSUMPTION_EFFECTS
        # A non-empty, specific public message + revisit condition (the type's
        # own __post_init__ guards these, but assert the boundary is honoured).
        assert a.public_message.strip()
        assert a.expires_when.strip()
        assert a.scope.strip()
        # The handle resolves back to THIS assumption.
        assert resolve_non_guarantee(binding.handle) is a


def test_resolve_returns_none_for_an_unknown_handle():
    """An undeclared handle resolves to None (the per-handle gap signal)."""
    assert resolve_non_guarantee("totally_unregistered_handle_xyz") is None


# --------------------------------------------------------------------------- #
# The binding does NOT touch the hashed AssumptionRegister schema.             #
# --------------------------------------------------------------------------- #


def test_binding_does_not_perturb_the_assumption_id():
    """The handle lives in the declaration plane, NOT the hashed assumption body.

    Binding a handle must not change the bound assumption's content id — the
    handle is external (the design constraint: no silent assumption_register_root
    migration).
    """
    for binding in V0_CLAIM_ASSUMPTION_BINDINGS:
        a = binding.assumption
        # Reconstructing the SAME register body yields the SAME id regardless of
        # the handle it is bound under — the handle is not part of the hash.
        rebuilt = AssumptionRegister(
            kind=a.kind,
            scope=a.scope,
            effect=a.effect,
            expires_when=a.expires_when,
            public_message=a.public_message,
            witness_rule_id=a.witness_rule_id,
            finding_refs=a.finding_refs,
        )
        assert rebuilt.assumption_id == a.assumption_id == binding.assumption_id
        # The canonical dict carries the assumption_id, NOT raw assumption fields
        # the hash would not see (the binding is a declaration-plane projection).
        assert binding.to_canonical_dict()["assumption_id"] == a.assumption_id


# --------------------------------------------------------------------------- #
# Root commitment — the binding set is itself checkable.                       #
# --------------------------------------------------------------------------- #


def test_binding_root_is_deterministic_and_membership_sensitive():
    """The binding root is stable and changes when the binding set changes."""
    r1 = claim_assumption_binding_root()
    r2 = claim_assumption_binding_root()
    assert r1 == r2  # deterministic
    assert r1.startswith("sha256:")

    dropped = claim_assumption_binding_root(V0_CLAIM_ASSUMPTION_BINDINGS[:-1])
    assert dropped != r1  # dropping a binding moves the root

    # Editing a bound assumption's body (its public_message) moves its
    # assumption_id and thus the root — the binding set commits the assumptions.
    head = V0_CLAIM_ASSUMPTION_BINDINGS[0]
    edited = ClaimAssumptionBinding(
        handle=head.handle,
        assumption=AssumptionRegister(
            kind=head.assumption.kind,
            scope=head.assumption.scope,
            effect=head.assumption.effect,
            expires_when=head.assumption.expires_when,
            public_message=head.assumption.public_message + " (edited)",
            witness_rule_id=head.assumption.witness_rule_id,
            finding_refs=head.assumption.finding_refs,
        ),
    )
    edited_root = claim_assumption_binding_root(
        (edited, *V0_CLAIM_ASSUMPTION_BINDINGS[1:])
    )
    assert edited_root != r1  # editing a bound assumption moves the root


# --------------------------------------------------------------------------- #
# W7 M13 lift-invariant — the v0 binding set carries no `lawvm.finland.<X>`   #
# implementation-level module paths in its scope/public_message text.        #
#                                                                             #
# The 13 `lawvm.finland.X.Y` strings that lived in the v0 binding set were    #
# lifted to concept-id form `fi.frontend.<concept>` (iter2 W7 M13 arch review #
# MEDIUM-2). The lift preserves the v0 claim-relative completeness contract    #
# (every V0_CLAIMS handle still resolves to one registered AssumptionRegister) #
# while decoupling the v→vN module rename surface: renaming a frontend module  #
# no longer silently drifts assumption bindings. A future regression (re-     #
# pasting a `lawvm.finland.X.Y` literal into a binding's scope text) re-opens  #
# the §2.3 leak. The companion file-level firewall lives in                   #
# tests/test_core_firewall_no_finland_module_paths.py.                         #
# --------------------------------------------------------------------------- #


def test_v0_binding_set_carries_no_finland_module_path_literals() -> None:
    """Lift-invariant: no `lawvm.finland.<module>` substring remains in any
    binding's scope/public_message text after the W7 M13 lift to concept-id
    form `fi.frontend.<concept>`.

    Pins the lift invariant at the binding-set level (the file-level AST
    firewall lives in tests/test_core_firewall_no_finland_module_paths.py).
    Mirrors the precedent of test_claim_assumption_binding's other
    structural-invariant tests — the v0 binding set's contract is the union
    of (1) handle-coverage, (2) assumption-well-formedness, and now (3)
    concept-id-binding (no implementation-level module paths in the bound
    text). Violations point to the offending handle + field.
    """
    offenders: list[str] = []
    for binding in V0_CLAIM_ASSUMPTION_BINDINGS:
        assumption = binding.assumption
        for field_name in ("scope", "public_message"):
            field_value = getattr(assumption, field_name)
            match = _FINLAND_MODPATH_RE.search(field_value)
            if match:
                offenders.append(
                    f"{binding.handle!r} assumption.{field_name} carries "
                    f"{match.group(0)!r}"
                )
    assert not offenders, (
        "v0 binding set carries `lawvm.finland.<module>` implementation-level "
        "module paths in its scope/public_message text (M13 lift regression, "
        "AGENTS.md §2.3). The W7 M13 arch-review-MEDIUM-2 fix lifted the 13 "
        "originals to concept-id form `fi.frontend.<concept>`; a re-introduced "
        "literal silently drifts assumption bindings on a frontend module "
        "rename. Offenders: " + "; ".join(offenders)
    )


def test_v0_binding_set_lift_preserves_claim_relative_completeness() -> None:
    """The W7 M13 lift swaps implementation-level module paths for concept-id
    strings; it must NOT drop a binding or shift the bound-handle set. The
    pre-existing per-handle coverage gate (`test_binding_set_exactly_covers_
    the_declared_surface`) is the load-bearing claim-relative-completeness
    assertion; this test restates the invariant directly against the LIFT
    boundary so a regression of the lift itself surfaces here even before the
    broader coverage test fails.

    Mirrors the precedent's `test_binding_set_exactly_covers_the_declared_surface`
    — the lift-invariant is a contract link: it strengthens the binding set
    from (handle resolves) to (handle resolves AND bound text is concept-id
    form), not a weakening.
    """
    # The bound handle set is exactly the declared V0_CLAIMS handle set.
    declared = {h for claim in V0_CLAIMS for h in claim.allowed_non_guarantees}
    assert bound_handles() == declared
    # No bindings were dropped or added by the lift.
    assert len(V0_CLAIM_ASSUMPTION_BINDINGS) == len(declared)


def test_empty_binding_set_has_a_valid_root():
    """An empty binding set is a valid deterministic (empty) MapRoot."""
    root = claim_assumption_binding_root(())
    assert isinstance(root, str)
    assert root.startswith("sha256:")
