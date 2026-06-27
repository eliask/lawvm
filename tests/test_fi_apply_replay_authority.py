"""Tests for the FI apply/replay execution-authorization projection
(``lawvm.finland.apply_replay_authorization``).

The authorizer is the descriptive mapping from the gate predicate (the exact
conjunction that already lets a write stand) to a typed
:class:`ExecutionAuthorization` wrapped in an
:class:`AuthoritySurface`. It NEVER loosens the gate, so its tests pin the
conjunction precisely:

1. ``_receipt_boundary_authorized`` — boundary authorization for a landed
   receipt with a resolver binding. With ``bound_target_path is None``
   (today's production op-level apply receipt), it authorizes by absence of
   a binding. With a BOUND target, it authorizes iff ``divergence_explained``
   (bound==landed, or a named recovery/migration/fallback rule explains the
   divergence).
2. ``aggregate_replay_authority`` — the per-replay aggregate (AND over all
   landed receipts) plus the orthogonal ``no_boundary_violation`` conjunct
   (the blocking ``REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET`` finding).

DEFERRED §2.9 FIRE-DRILL: the task spec for this PR anticipated a §2.9
production-liveness fire-drill that drives a known-boundary-violating op
through ``_collect_op_write_receipt`` and asserts the
``_receipt_boundary_authorized`` finding fires. Threading ``bound_target_path``
from ``rop.resolved_target_address`` onto the op-level receipt (the natural
source) surfaced 115 false-positive divergences on the green corpus
(1997/1339): 71 landed-is-prefix-of-bound at identity-pruned granularity
shifts, 15 bound-is-prefix-of-landed at deeper mutations, 29 real rop-vs-IR
kind-label mismatches (e.g. ``item:7`` vs ``paragraph:7``). Threading
correctly requires deeper normalization (prefix-of-landed equivalence + a
surface path-kind reconciliation across the FI IR and the rop's logical/legal
address), exceeding this PR's bounded scope. The threading is reverted here;
the receipt arm stays unreachable in production (the
``no_boundary_violation`` conjunct carries the boundary check). The unit
tests below pin the function-level behavior the future PR will rely on.
"""

from __future__ import annotations

from lawvm.core.phase_result import Finding
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_replay_authorization import (
    APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
    FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID,
    _receipt_boundary_authorized,
    aggregate_replay_authority,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _receipt(
    *,
    op_id: str = "op",
    bound_target_path: tuple[tuple[str, str], ...] | None = None,
    landed_primary_path: tuple[tuple[str, str], ...] | None = None,
    recovery_rule_ids: tuple[str, ...] = (),
    migration_rule_ids: tuple[str, ...] = (),
    fallback_rule_ids: tuple[str, ...] = (),
) -> WriteReceipt:
    """Build a minimal receipt covering bound/landed + named-rule fields."""
    return WriteReceipt(
        op_id=op_id,
        helper="test",
        action="replace",
        bound_target_path=bound_target_path,
        landed_primary_path=landed_primary_path,
        replaced_paths=((landed_primary_path,) if landed_primary_path else ()),
        recovery_rule_ids=recovery_rule_ids,
        migration_rule_ids=migration_rule_ids,
        fallback_rule_ids=fallback_rule_ids,
    )


def _boundary_violation_finding(op_id: str = "op") -> Finding:
    """A blocking REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET finding."""
    return Finding(
        kind=APPLY_BOUNDARY_VIOLATION_FINDING_CODE,
        role="violation",
        stage="apply",
        blocking=True,
        source_statute="2020/1",
        detail={"op_id": op_id, "message": "boundary touch for tests"},
    )


# ---------------------------------------------------------------------------
# _receipt_boundary_authorized
# ---------------------------------------------------------------------------


def test_receipt_boundary_authorized_none_bound_authorizes_by_absence() -> None:
    """A receipt with no resolver binding (``bound_target_path is None``)
    has no divergence to explain and authorizes (today's production op-level
    apply receipt)."""
    receipt = _receipt(
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_bound_equals_landed() -> None:
    """A receipt whose bound target matches its landed path authorizes."""
    receipt = _receipt(
        bound_target_path=(("section", "5"),),
        landed_primary_path=(("section", "5"),),
    )
    assert _receipt_boundary_authorized(receipt) is True


def test_receipt_boundary_authorized_unexplained_divergence_returns_false() -> None:
    """A bound target that DIVERGED from the landed path with NO named rule
    is an UNEXPLAINED mutation-boundary divergence (§1.0). This is the
    fire-drill behavior the §2.9 production-liveness test will assert when
    the bound_target_path threading lands (see module docstring): a
    boundary-violating receipt must authoritatively refuse authorization.
    """
    receipt = _receipt(
        bound_target_path=(("section", "5"),),
        landed_primary_path=(("section", "6"),),
    )
    assert _receipt_boundary_authorized(receipt) is False


def test_receipt_boundary_authorized_explained_divergence_returns_true() -> None:
    """A bound target that diverged but carries a named recovery/migration/
    fallback rule authorizes (the section-relabel/move pattern uses
    migration_rule_ids)."""
    receipt = _receipt(
        bound_target_path=(("section", "5"),),
        landed_primary_path=(("section", "5a"),),
        migration_rule_ids=("section_relabel_renumber",),
    )
    assert _receipt_boundary_authorized(receipt) is True


# ---------------------------------------------------------------------------
# aggregate_replay_authority
# ---------------------------------------------------------------------------


def test_aggregate_replay_authority_empty_receipts_and_findings_authorizes() -> None:
    """An empty replay (no landed writes AND no findings) authorizes
    vacuously — the AND over an empty set is True."""
    surface = aggregate_replay_authority(write_receipts=(), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True
    assert surface.authorization.executable is True
    assert surface.authorization.authorization_rule_id == (
        FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID
    )


def test_aggregate_replay_authority_with_none_bound_receipt_stays_authorized() -> None:
    """Today's production op-level apply receipt (bound=None) authorizes;
    the receipt boundary arm is vacuous, and no_boundary_violation is clean."""
    receipt = _receipt(
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
    )
    surface = aggregate_replay_authority(write_receipts=(receipt,), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is True


def test_aggregate_replay_authority_blocks_on_boundary_finding() -> None:
    """The orthogonal ``no_boundary_violation`` conjunct: a blocking
    REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET finding forces the aggregate
    unauthorized (this is the load-bearing arm today, per the HONESTY NOTE
    in `_receipt_boundary_authorized`)."""
    receipt = _receipt(
        bound_target_path=None,
        landed_primary_path=(("section", "1"),),
    )
    finding = _boundary_violation_finding()
    surface = aggregate_replay_authority(
        write_receipts=(receipt,), findings=(finding,)
    )
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is False
    assert surface.authorization.executable is False
    assert surface.authorization.strict_disposition == "block"


def test_aggregate_replay_authority_blocks_on_unexplained_divergence_receipt() -> None:
    """DEFERRED §2.9 fire-drill precondition (function-level liveness):
    an unexplained bound→landed divergence on a CONSTRUCTED receipt
    authoritatively un-authorizes the aggregate via the
    `_receipt_boundary_authorized` arm. In production today the op-level
    receipt hardcodes ``bound_target_path=None`` so this arm is unreachable
    from the production lane (see module docstring); this test pins the
    function-liveness guarantee the future bound-target threading will rely
    on."""
    receipt = _receipt(
        bound_target_path=(("section", "5"),),
        landed_primary_path=(("section", "6"),),
    )
    surface = aggregate_replay_authority(write_receipts=(receipt,), findings=())
    assert surface.authorization is not None
    assert surface.authorization.replay_authorized is False
    assert surface.authorization.executable is False
