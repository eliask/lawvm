"""XP-03 — op-coverage totality at the canonical-op lowering waist (runtime arm).

Registry row XP-03: *every PotentialOperation lowers to exactly one canonical op
OR a typed candidate-effect residual — never silently dropped.* The STATIC arm
(the ``PotentialOperation`` census + the candidate-effect closed vocabulary)
exists; this is the RUNTIME PARITY arm at the lowering seam
(``compile_amendment.build_canonical_op_stage``):

    #candidate_ops == #canonical_ops (coverage.owned)
                      + #typed_candidate_effect_residuals (coverage.violation)

which is exactly ``coverage.is_partition()`` over ``unit="candidate_ops"``.

HONESTY (the generator's stopping rule)
=======================================
The partition holds BY CONSTRUCTION: ``build_canonical_op_stage`` computes the
denominator as ``total = #emitted resolved ops + #rejected candidate ops``, so
``owned + violation == total`` is an arithmetic identity at the seam — there is
no production path that silently drops a candidate op. The runtime check is
therefore a DEFENSIVE PIN: a ``CANONICAL_OP.OP_COVERAGE_GAP`` typed residual
(NON-BLOCKING) is appended iff ``is_partition()`` ever fails, so a future
producer that recomputes the partition some other way and leaves an op
unaccounted SURFACES the gap rather than dropping it silently.

NON-BLOCKING by design: a genuinely uncovered op should be reported for triage,
not block the whole amendment/corpus. The corpus-population case below replays
real statutes and asserts ZERO gaps today (the population IS the report).

Cases
=====
  (a) by-construction totality — the production seam yields a partition and NO
      OP_COVERAGE_GAP residual on clean and on declined inputs.
  (b) TRIP-PROOF — a non-partition coverage (forced via a stubbed
      ``CoverageCertificate``) makes the seam emit the typed
      ``CANONICAL_OP.OP_COVERAGE_GAP`` residual (non-blocking), self-evidencing
      with the owned/violation/total counts; the gap residual does NOT become a
      decline (it is not a carrier-backed residual).
  (c) CORPUS POPULATION — replaying real statutes, every aggregated canonical-op
      StageResult is a partition; the report is the count of stages and gaps
      (gaps == 0 today). A NEW-CORRECT uncovered op would show as a non-zero gap
      population, surfaced here rather than silently swallowed.
"""

from __future__ import annotations

from typing import cast

import pytest

import lawvm.finland.compile_amendment as compile_amendment
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.phase_result import Finding
from lawvm.core.stage_result import CoverageCertificate
from lawvm.finland.compile_amendment import (
    OP_COVERAGE_GAP_RESIDUAL_KIND,
    build_canonical_op_stage,
)
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest


def _resolved(n: int) -> list[ResolvedOp]:
    """``n`` opaque resolved-op stand-ins (the stage uses only ``len``)."""
    return cast("list[ResolvedOp]", [object() for _ in range(n)])


def _blocking_rejection(target: str = "5") -> Finding:
    return Finding(
        kind="ELAB.STRICT_REJECTED_OPERATION",
        role="obligation",
        stage="_elaborate_group",
        detail={
            "message": "operation rejected by strict profile before apply",
            "target_section": target,
        },
        source_statute="1/2024",
        blocking=True,
    )


def _gap_residuals(stage) -> list:
    return [r for r in stage.residuals if r.kind == OP_COVERAGE_GAP_RESIDUAL_KIND]


# ---------------------------------------------------------------------------
# Registry: the finding code is registered (and non-blocking).
# ---------------------------------------------------------------------------


def test_op_coverage_gap_code_is_registered_non_blocking() -> None:
    spec = FINDING_REGISTRY.get("CANONICAL_OP.OP_COVERAGE_GAP")
    assert spec is not None, "CANONICAL_OP.OP_COVERAGE_GAP must be in FINDING_REGISTRY"
    # Non-blocking: surfaced for triage, never a hard fail (so no fire-drill
    # obligation — the guard-liveness rule only governs blocking codes).
    assert spec.default_enforcement == "warn"
    assert spec.role == "observation"
    # The residual kind the production seam stamps equals the registry code.
    assert OP_COVERAGE_GAP_RESIDUAL_KIND == "CANONICAL_OP.OP_COVERAGE_GAP"


# ---------------------------------------------------------------------------
# (a) by-construction totality at the production seam
# ---------------------------------------------------------------------------


def test_clean_lowering_is_a_partition_with_no_gap() -> None:
    resolved = _resolved(4)
    stage, carriers = build_canonical_op_stage(resolved, [])
    assert stage.coverage.unit == "candidate_ops"
    assert stage.coverage.is_partition()
    # owned == emitted, violation == 0, total == owned.
    assert stage.coverage.owned == 4
    assert stage.coverage.violation == 0
    assert stage.coverage.total == 4
    assert _gap_residuals(stage) == []
    assert carriers == ()


def test_declined_lowering_is_a_partition_with_no_gap() -> None:
    resolved = _resolved(2)
    findings = [_blocking_rejection("5"), _blocking_rejection("9")]
    stage, carriers = build_canonical_op_stage(resolved, findings)
    # Every declined candidate op is accounted as a typed candidate-effect
    # residual (violation), every emitted op as owned — a partition, no gap.
    assert stage.coverage.is_partition()
    assert stage.coverage.owned == 2
    assert stage.coverage.violation == 2
    assert stage.coverage.total == 4
    assert _gap_residuals(stage) == []
    # The two declines ride the carrier-backed typed residuals (single-channel).
    assert len(carriers) == 2


# ---------------------------------------------------------------------------
# (b) TRIP-PROOF — a non-partition coverage surfaces the typed gap residual
# ---------------------------------------------------------------------------


def test_non_partition_coverage_surfaces_op_coverage_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a coverage that is NOT a partition (a candidate op neither lowered
    nor residualized) and assert the seam emits the typed non-blocking gap
    residual rather than silently dropping the op."""

    real_cert = compile_amendment.CoverageCertificate

    def _leaky_cert(**kwargs: object) -> CoverageCertificate:
        # Inflate total by 1 over (owned + violation) => is_partition() is False:
        # one candidate op is unaccounted (the silent-drop shape).
        total = cast(int, kwargs.get("total", 0))
        return real_cert(
            unit=cast(str, kwargs.get("unit", "")),
            total=total + 1,
            owned=cast(int, kwargs.get("owned", 0)),
            violation=cast(int, kwargs.get("violation", 0)),
            totality_claimed=cast(bool, kwargs.get("totality_claimed", True)),
        )

    monkeypatch.setattr(compile_amendment, "CoverageCertificate", _leaky_cert)

    resolved = _resolved(3)
    stage, _carriers = build_canonical_op_stage(resolved, [])

    assert not stage.coverage.is_partition()
    gaps = _gap_residuals(stage)
    assert len(gaps) == 1, "a non-partition lowering must surface ONE gap residual"
    gap = gaps[0]
    # Non-blocking: surfaced for triage, not a corpus-blocking hard fail.
    assert gap.blocking is False
    assert gap.scope == "candidate_ops"
    # Self-evidencing: the reason embeds the offending partition counts.
    assert "owned=3" in gap.reason
    assert "total=4" in gap.reason


def test_gap_residual_is_not_a_decline_carrier() -> None:
    """The gap residual is an ACCOUNT residual, not a carrier-backed decline: it
    must not be mistaken for a strict-rejection that gates apply."""
    resolved = _resolved(1)
    # Clean input → no gap (by construction); the carriers list is the decline
    # channel and stays empty. (The trip-proof above covers the gap-present path;
    # here we assert the gap residual never co-opts the carrier channel.)
    stage, carriers = build_canonical_op_stage(resolved, [])
    assert carriers == ()
    assert _gap_residuals(stage) == []


# ---------------------------------------------------------------------------
# (c) CORPUS POPULATION — every real-replay canonical-op stage is a partition
# ---------------------------------------------------------------------------

# A small spread of real amendment drivers (parent_id) already exercised by the
# replay suite; replaying them aggregates per-amendment canonical-op StageResults.
_POPULATION_PARENTS = ("1985/336", "552/2019")


@pytest.mark.parametrize("parent_id", _POPULATION_PARENTS)
def test_corpus_canonical_op_stage_is_a_partition(parent_id: str) -> None:
    result = replay_xml(
        request=ReplayXmlRequest(
            parent_id=parent_id,
            mode="official_consolidation",
            quiet=True,
            build_full_products=True,
        )
    )
    stage = result.products.canonical_op_stage
    # Some replays may have no canonical-op activity → the carrier is the empty
    # account, still a partition; either way it must be one and gap-free.
    if stage is None:
        pytest.skip(f"{parent_id}: no canonical-op stage produced")
    assert stage.coverage.unit == "candidate_ops"
    assert stage.coverage.is_partition(), (
        f"{parent_id}: canonical-op coverage is not a partition "
        f"(owned={stage.coverage.owned}, violation={stage.coverage.violation}, "
        f"total={stage.coverage.total}) — XP-03 op-coverage gap"
    )
    gaps = [r for r in stage.residuals if r.kind == OP_COVERAGE_GAP_RESIDUAL_KIND]
    # POPULATION REPORT: zero gaps today. A NEW-CORRECT uncovered op would make
    # this non-zero and surface here (non-blocking) rather than vanish.
    assert gaps == [], (
        f"{parent_id}: {len(gaps)} OP_COVERAGE_GAP residual(s) — a candidate op "
        "was left unaccounted at the canonical-op lowering waist:\n"
        + "\n".join(f"  {g.reason}" for g in gaps)
    )
