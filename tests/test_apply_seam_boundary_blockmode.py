"""B-enforcement: LS-01 mutation-boundary block-mode promotion — the
measure-then-flip safety gate for the tree frontends (task #102).

The universal apply seam (``core/apply_seam.apply_op``) is the always-on LS-01
mutation-boundary OBSERVER for every ``boundary_mode="off"`` tree profile (inc 2):
on every landed write it runs the SAME core ``audit_op_mutation_boundary`` the
in-fold env-probes run and routes any ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``
escape witness (a write whose changed paths escaped its declared
target/migration/recovery boundary) to the SEPARATE ``AppliedOp.observations``
lane — NEVER to production ``findings``. This module is the per-frontend
measure-then-flip gate that decides whether each tree frontend's profile can be
promoted ``boundary_mode="off"`` → ``"block"`` (escapes fail loud in production
``findings``), following EE's inc-4 occupancy template: flip ONLY if the
frontend's corpus is MEASURED boundary-clean (zero escapes), so the flip is
byte-identical; otherwise KEEP observe and surface the escape as a typed report.

THE DRAIN. Each tree frontend's production apply lane (``apply_no_ops`` /
``apply_se_ops`` / ``apply_ee_ops`` / ``replay_uk_ops``) gained an additive
``seam_observations_out: list[Finding] | None`` parameter (default ``None`` = a
pure no-op: production never allocates or reads it, so byte-identity is
unconditional — the exact EE inc-4 pattern). When provided, the per-op
``AppliedOp.observations`` (the boundary witnesses) are appended verbatim. This
is the corpus boundary-cleanliness MEASUREMENT carrier.

THE MEASURED OUTCOME (this task). Driving each frontend's representative op set
(the fixtures the seam parallel-run byte-identity gates certify cover every apply
action family) through its production apply lane with the drain, plus EE's REAL
replayable corpus (measured out-of-band with the archive present — recorded in
``notes/B_ENFORCEMENT_STATUS.md`` §8):

* **NO** — representative op set: 0 escapes. Real corpus NOT replayable in this
  environment (no NO original-act source). KEEP OBSERVE (unproven-clean).
* **SE** — representative op set: 0 escapes. No production corpus replay lane /
  committed corpus exists. KEEP OBSERVE (unproven-clean).
* **EE** — representative op set: 0 escapes. The REAL replayable corpus surfaced
  escapes in observe mode that #108-EE proved were ALL ONE declaration artifact
  (a flat PEG op target vs the chapter-nested body); once
  ``_ee_resolved_boundary_prefixes`` corrected the DECLARATION (never a write) the
  escape count went to 0 over the 30/120/300-statute samples with off-vs-block
  body-hash byte-identity, so EE was PROMOTED off -> block (the one frontend that
  completed the flip; guarded by ``tests/test_ee_boundary_enforcement.py``).
* **UK** — representative op set: **0 escapes** after task #108-UK. A REPLACE on
  a missing leaf RECOVERED as an INSERT at the body root WAS read as an escape
  because UK's materializer did not thread its recovery retarget into
  ``declared_recovery_prefixes``; that instrumentation gap is now closed (UK's
  missing-leaf REPLACE→INSERT lane records the resolved write-parent path on the
  per-op carrier, surfaced on the ``MaterializeResult``, exactly like NO/EE). The
  declaration changes NO write — the recovered section is still inserted; replay
  is byte-identical. UK still KEEPS OBSERVE: no real UK corpus replay lane exists
  in this environment, so op-set-cleanliness is not a flip authorization (§8.2:
  op-set-clean ≠ corpus-clean).

So EE alone flips to block — and ONLY after its real corpus was proven clean
(#108-EE); the two op-set-clean frontends (NO, SE) cannot have their real corpora
verified here, and UK's op set is clean but has no verifiable real corpus here,
so flipping any of the three would be unsafe speculation and they KEEP OBSERVE.
This is the honest outcome of the safety-first discipline — a representative op
set can dramatically UNDER-report escapes, so only a proven-clean corpus
authorizes the flip. The ``boundary_mode`` block path is wired and ready; the
flip is one profile field per frontend once its corpus is proven clean, as EE
demonstrated.
"""
from __future__ import annotations

from lawvm.core.mutation_boundary_proof import MUTATION_BOUNDARY_FINDING_AT_OP_CODE
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.norway.grafter import apply_no_ops
from lawvm.sweden.grafter import apply_se_ops
from lawvm.estonia.grafter import apply_ee_ops
from lawvm.uk_legislation.replay_executor import replay_uk_ops


# ── shared helpers ────────────────────────────────────────────────────────────


def _count_boundary_escapes(observations: list[Finding]) -> int:
    """Count the LS-01 boundary-escape witnesses in a drained observe lane.

    ``audit_op_mutation_boundary`` emits a ``MUTATION_BOUNDARY_FINDING_AT_OP``
    finding ONLY on an out-of-boundary escape (a within-boundary op emits
    nothing), so this count IS the per-op escape count — the measure-then-flip
    signal. Zero ⇒ the run is boundary-clean ⇒ a block flip would be
    byte-identical; non-zero ⇒ a real latent escape ⇒ keep observe.
    """
    return sum(
        1
        for f in observations
        if f.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    )


def _measure_no() -> int:
    from tests.test_no_apply_seam_parallel_run import _op_sets, _statute

    total = 0
    for _name, ops in _op_sets():
        obs: list[Finding] = []
        apply_no_ops(_statute(), ops, seam_observations_out=obs)
        total += _count_boundary_escapes(obs)
    return total


def _measure_se() -> int:
    from tests.test_se_apply_seam_parallel_run import _op_sets, _statute

    total = 0
    for _name, ops in _op_sets():
        obs: list[Finding] = []
        apply_se_ops(_statute(), ops, seam_observations_out=obs)
        total += _count_boundary_escapes(obs)
    return total


def _measure_ee() -> int:
    from tests.test_ee_apply_seam_parallel_run import _op_sets, _statute

    total = 0
    for _name, ops in _op_sets():
        obs: list[Finding] = []
        apply_ee_ops(_statute(), ops, seam_observations_out=obs)
        total += _count_boundary_escapes(obs)
    return total


def _measure_uk() -> tuple[int, list[Finding]]:
    from tests.test_uk_apply_seam_parallel_run import _op_sets, _statute

    total = 0
    escapes: list[Finding] = []
    for _name, ops in _op_sets():
        obs: list[Finding] = []
        replay_uk_ops(_statute(), ops, seam_observations_out=obs)
        n = _count_boundary_escapes(obs)
        total += n
        escapes.extend(
            f for f in obs if f.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
        )
    return total, escapes


# ── 1. MEASURED per-frontend escape counts drive the flip decision ────────────


def test_no_representative_op_set_is_boundary_clean() -> None:
    """NO's representative op set produces zero boundary escapes (op-set clean).

    NO is NOT flipped to block: its REAL corpus is not replayable in this
    environment (no NO original-act source), so the op-set-clean result is not a
    clean-corpus proof — keep observe (the safety-first discipline).
    """
    assert _measure_no() == 0


def test_se_representative_op_set_is_boundary_clean() -> None:
    """SE's representative op set produces zero boundary escapes (op-set clean).

    SE is NOT flipped to block: it has no production corpus replay lane /
    committed replayable corpus, so the op-set-clean result is not a clean-corpus
    proof — keep observe.
    """
    assert _measure_se() == 0


def test_ee_representative_op_set_is_boundary_clean() -> None:
    """EE's representative op set produces zero boundary escapes (op-set clean).

    The safety-first measure-then-flip lesson still holds: the tiny representative
    op set reports ZERO escapes, so op-set-cleanliness was NEVER the flip
    authorization. EE's REAL replayable corpus was what surfaced escapes in
    observe mode; #108-EE proved they were all ONE declaration artifact,
    corrected the declaration (never a write), re-measured 0 over the
    30/120/300-statute samples, and only THEN promoted EE to block. So the real
    corpus — not this op set — is what authorized EE's flip (see
    ``test_ee_frontend_promoted_to_boundary_block``).
    """
    assert _measure_ee() == 0  # op set is clean (it always was).


def test_uk_representative_op_set_is_boundary_clean_after_recovery_declaration() -> None:
    """UK's representative op set is boundary-clean once the recovery retarget is
    declared (task #108-UK closed the instrumentation gap).

    A REPLACE on a missing leaf (section "999") is RECOVERED as an INSERT at the
    body root. Previously UK's ``_uk_materialize_one`` did NOT thread that
    recovery retarget into ``MaterializeResult.declared_recovery_prefixes`` (unlike
    NO/EE), so the seam observer read the authorized body-root write as an
    UNDECLARED escape (count == 1). The missing-leaf REPLACE→INSERT recovery lane
    now records the resolved write-parent path (the body root) on the per-op
    carrier, surfaced on the ``MaterializeResult``, so the seam's always-on LS-01
    audit treats the retargeted write as in-boundary. The op set therefore now
    reports ZERO escapes — a benign instrumentation gap closed, NOT a write change
    (the recovered section is still inserted; production replay is byte-identical).

    Op-set-cleanliness still does NOT authorize a ``boundary_mode`` flip to block:
    no real UK corpus replay lane exists in this environment, so per §8.2's
    safety discipline (op-set-clean ≠ corpus-clean) UK stays OBSERVE. The flip is
    a staged step (see ``test_unproven_frontends_keep_boundary_mode_off``; EE is
    the one frontend that completed it in #108-EE —
    ``test_ee_frontend_promoted_to_boundary_block``).
    """
    count, escapes = _measure_uk()
    assert count == 0
    assert escapes == []


# ── 2. NO frontend was flipped to block (the safety-first outcome) ────────────


def test_unproven_frontends_keep_boundary_mode_off() -> None:
    """The tree frontends WITHOUT a proven-clean real corpus keep ``boundary_mode``
    "off": NO / SE / UK each build their ``ApplyProfile`` with ``boundary_mode="off"``.

    This pins the safety-first half of the measure-then-flip gate: NO/SE are
    op-set-clean but have no clean-corpus proof, and UK's op set is clean but has
    no verifiable real corpus in this environment, so NONE of the three qualifies
    for the block promotion — each stays OBSERVE. A future edit that flips any of
    these profiles to block WITHOUT first proving its corpus boundary-clean (the
    way EE did in #108-EE, see ``test_ee_frontend_promoted_to_boundary_block``)
    breaks this gate loudly.
    """
    import inspect

    import lawvm.norway.grafter as no_g
    import lawvm.sweden.grafter as se_g
    import lawvm.uk_legislation.replay_executor as uk_x

    no_src = inspect.getsource(no_g.apply_no_ops)
    se_src = inspect.getsource(se_g.apply_se_ops)
    uk_src = inspect.getsource(uk_x.UKReplayExecutor._uk_seam_apply_profile)

    for src in (no_src, se_src, uk_src):
        assert 'boundary_mode="off"' in src
        assert 'boundary_mode="block"' not in src


def test_ee_frontend_promoted_to_boundary_block() -> None:
    """EE's production profile WAS promoted ``boundary_mode`` off -> "block" (#108-EE).

    This is the other, earned half of the measure-then-flip gate: EE replayed its
    REAL corpus in observe mode, found every escape was the one chapter-nesting
    DECLARATION artifact, corrected the declaration WITHOUT changing any write
    (``_ee_resolved_boundary_prefixes``), re-measured 0 escapes over the
    30/120/300-statute samples, proved off-vs-block body-hash byte-identity, and
    only THEN flipped its ``ApplyProfile.boundary_mode`` to "block" — the second
    enforcing apply-seam gate after LS-03 occupancy. So EE alone carries
    ``boundary_mode="block"`` and NOT "off".

    The promotion is not free-floating: its corpus-clean proof and block-mode
    enforcement live in the dedicated ``tests/test_ee_boundary_enforcement.py``.
    This assertion anchors the profile marker to that guard so a future edit that
    reverts EE to observe (or promotes it without the proof) is caught here.
    """
    import importlib
    import inspect

    import lawvm.estonia.grafter as ee_g

    ee_src = inspect.getsource(ee_g.apply_ee_ops)
    assert 'boundary_mode="block"' in ee_src
    assert 'boundary_mode="off"' not in ee_src

    # The block promotion is anchored to its corpus-clean enforcement guard: the
    # declaration-correcting helper that drove the escape count to zero must
    # exist, and its dedicated block-mode enforcement test suite must import.
    assert hasattr(ee_g, "_ee_resolved_boundary_prefixes")
    importlib.import_module("tests.test_ee_boundary_enforcement")


# ── 3. The measurement drain is byte-identical (default None = no-op) ──────────


def _ee_replace(label: str, text: str) -> LegalOperation:
    return LegalOperation(
        op_id=f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id="ee/amend"),
    )


def _ee_body(*sections: IRNode) -> IRStatute:
    return IRStatute(
        statute_id="ee/base",
        title="Base",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(sections),
        ),
        supplements=(),
    )


def test_seam_observations_drain_is_byte_identical_to_no_drain() -> None:
    """Passing ``seam_observations_out`` does NOT change the materialized output.

    The drain is additive evidence: with it absent vs present, the production
    apply lane returns the identical materialized statute (the §2.7
    grounding-neutral invariant — observation never becomes authority, EV-04).
    """
    statute = _ee_body(
        IRNode(kind=IRNodeKind.SECTION, label="1", text="Original"),
    )
    op = _ee_replace("1", "Amended")

    without = apply_ee_ops(statute, [op])
    obs: list[Finding] = []
    with_drain = apply_ee_ops(statute, [op], seam_observations_out=obs)

    from lawvm.core.ir_helpers import structural_subtree_hash

    assert structural_subtree_hash(without.body) == structural_subtree_hash(
        with_drain.body
    )
    assert without.title == with_drain.title
