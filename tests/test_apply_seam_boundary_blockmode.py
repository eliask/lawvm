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
* **EE** — representative op set: 0 escapes, but the REAL replayable corpus has
  **136 escapes per 30 statutes** (the seam observer and EE's in-fold probe agree
  exactly — these are genuine deep sub-section/item-level LS-01 escapes that are
  observe-only today). KEEP OBSERVE (a real latent boundary-escape finding).
* **UK** — representative op set: **1 escape** (a REPLACE on a missing leaf
  RECOVERED as an INSERT at the body root; UK's materializer does NOT thread its
  recovery retarget into ``declared_recovery_prefixes``, so the seam reads the
  body-root write as an undeclared escape). KEEP OBSERVE (a real latent
  boundary-escape finding).

So NO frontend flips to block: the two frontends with replayable/real corpora
(EE, UK) have demonstrable latent escapes; the two whose tiny op sets are clean
(NO, SE) cannot have their real corpora verified here, so flipping them would be
unsafe speculation. This is the honest outcome of the safety-first discipline —
the representative op sets dramatically UNDER-report escapes (EE: 0 vs 136), so
only a proven-clean corpus authorizes the flip, and none qualifies today. The
``boundary_mode`` block path is wired and ready; the flip is one profile field
per frontend once its corpus is proven clean (the escapes' declared-recovery
threading is closed).
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


def test_ee_representative_op_set_is_boundary_clean_but_real_corpus_is_not() -> None:
    """EE's representative op set is clean, yet the REAL corpus has 136 escapes.

    This is the decisive lesson of the safety-first measure-then-flip: the tiny
    representative op set reports ZERO escapes, but EE's REAL replayable corpus
    has 136 boundary escapes per 30 statutes (the seam observer and EE's in-fold
    probe agree exactly; recorded in notes/B_ENFORCEMENT_STATUS.md §8). So
    op-set-cleanliness is NOT a flip authorization — only the real corpus is.
    EE stays OBSERVE.
    """
    assert _measure_ee() == 0  # op set is clean — but the real corpus is NOT.


def test_uk_representative_op_set_surfaces_a_real_boundary_escape() -> None:
    """UK's representative op set surfaces exactly one real LS-01 boundary escape.

    A REPLACE on a missing leaf (section "999") is RECOVERED as an INSERT at the
    body root, but UK's ``_uk_materialize_one`` does NOT thread its recovery
    retarget into ``declared_recovery_prefixes`` (unlike NO/EE), so the seam reads
    the body-root write as an UNDECLARED escape. This is a real latent
    boundary-escape finding — UK stays OBSERVE (count > 0 ⇒ no flip), surfaced
    here as the typed report rather than papered over.
    """
    count, escapes = _measure_uk()
    assert count == 1
    escape = escapes[0]
    assert escape.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    assert escape.role == "observation"
    assert escape.blocking is False
    # The escape is the body-root write of the recovered REPLACE→INSERT.
    assert escape.detail["op_id"] == "miss"
    assert escape.detail["boundary_status"] == "out_of_boundary"


# ── 2. NO frontend was flipped to block (the safety-first outcome) ────────────


def test_all_tree_frontends_keep_boundary_mode_off() -> None:
    """No tree frontend's production profile was flipped to ``boundary_mode``
    "block": each builds its ``ApplyProfile`` with ``boundary_mode="off"``.

    This pins the safety-first outcome: the measure-then-flip gate found that the
    frontends with verifiable corpora (EE/UK) have latent escapes, and the
    op-set-clean frontends (NO/SE) have no clean-corpus proof, so NONE qualifies
    for the block promotion. A future edit that flips any profile to block
    WITHOUT first proving its corpus boundary-clean breaks this gate loudly.
    """
    import inspect

    import lawvm.norway.grafter as no_g
    import lawvm.sweden.grafter as se_g
    import lawvm.estonia.grafter as ee_g
    import lawvm.uk_legislation.replay_executor as uk_x

    no_src = inspect.getsource(no_g.apply_no_ops)
    se_src = inspect.getsource(se_g.apply_se_ops)
    ee_src = inspect.getsource(ee_g.apply_ee_ops)
    uk_src = inspect.getsource(uk_x.UKReplayExecutor._uk_seam_apply_profile)

    for src in (no_src, se_src, ee_src, uk_src):
        assert 'boundary_mode="off"' in src
        assert 'boundary_mode="block"' not in src


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
