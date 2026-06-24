"""Cross-stage residual-ledger monotonicity sweep (registry row EV-03).

EV-03 — *residual ledger monotone per unit*
===========================================
The §0 conservation question (``LAWVM_INVARIANT_GENERATOR_V0`` §3.B — *no silent
loss*) applied to the per-stage account ledger the certificate dossier folds
(``lawvm.tools.certificate_bundle`` builds one ``stage_account_row`` per pipeline
stage, each carrying a committed ``coverage_row`` + ``residual_rows`` projected
off that stage's :class:`~lawvm.core.stage_result.StageResult`). A residual that a
stage's coverage account COUNTS in its signal-bearing class (the
``unowned_violation`` / ``violation`` bucket) must not SILENTLY vanish from that
stage's committed residual ledger: it must be present as ≥1 typed residual record
(a witness), or the count is a phantom — a residual counted but not recorded, the
exact "uncertainty recorded then dropped without a trace" EV-03 forbids.

WHY THIS IS A REAL CHECK (NOT by-construction-asserted)
=======================================================
``certificate_bundle._verify_stage_accounts`` already proves each committed
``residual_rows`` HASHES to its committed ``residual_subroot`` (tamper integrity:
a row dropped AFTER commit diverges), and ``_verify_coverage_row_arithmetic``
proves the four coverage classes SUM to ``total``. Neither cross-checks that a
coverage-counted ``violation`` actually has a committed residual RECORD. A
producer/aggregator bug that increments ``coverage.violation`` but forgets to
append the ``unowned_violation`` :class:`~lawvm.core.stage_result.Residual` (the
"built-then-severed" class at the stage-account boundary) passes both existing
checks today. EV-03 is the conservation assertion that closes that gap.

THE HONEST PREDICATE (presence, not strict cardinality)
=======================================================
The stages do NOT share a residual-record cardinality. The SEAM-B aggregators
(``replay_products.aggregate_structural_stage`` / ``aggregate_canonical_op_stage``)
hold ``coverage.violation == #blocking-residual-records`` exactly (one residual
per declined op / per unexplained write). But the SEAM-A read-offs count TOKENS or
NODES: the forest stage's ``coverage.violation`` is a token count while its
residual records are SPAN records (one span can cover several violation tokens),
so a strict equality would FALSELY fire. What DOES hold uniformly by construction —
and is the honest conservation law — is **presence**::

    coverage.violation > 0  ==>  at least one BLOCKING residual record committed

(you cannot have violation tokens without ≥1 violation span; you cannot have a
declined op without its decline residual). A stage that counts a signal-bearing
violation with ZERO blocking residual records is a silent residual loss. The
symmetric arm — a blocking residual record present while ``coverage.violation``
is 0 — is the dual silent loss (a recorded residual the coverage account forgot to
count), and is also reported.

OBSERVATION-ROLE, GREEN-SILENT
==============================
On the green corpus every stage carries ``violation == 0`` and no blocking
residual, so the sweep is silent (0-delta). The blocking signal that a real
broken-ref / dropped-universe-member carries already rides the existing #5/#10
status-contribution arm in the dossier; EV-03 is the additive *conservation*
assertion over the committed ledger, surfaced as an OBSERVATION finding (a
non-monotone ledger is a producer defect to fix, not a corpus fact to block on).
The synthetic stage-account bite is the guard-liveness fire-drill.

The sweep is PURE: it reads the already-committed ``stage_account_rows`` and
returns typed finding records; no production behavior changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Finding code (registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

RESIDUAL_LEDGER_NONMONOTONE = "EVID.RESIDUAL_LEDGER_NONMONOTONE"


@dataclass(frozen=True, slots=True)
class ResidualLedgerMonotonicityFinding:
    """One EV-03 conservation fact about a stage's committed residual ledger.

    Attributes:
        code:                ``EVID.RESIDUAL_LEDGER_NONMONOTONE``.
        stage:               The pipeline stage id whose account is non-monotone.
        coverage_violation:  The stage coverage's ``violation`` count (signal-bearing
                             unowned units the account claims).
        blocking_residuals:  The number of BLOCKING residual records committed in
                             that stage's residual ledger.
        direction:           ``"counted_not_recorded"`` (violation>0, no blocking
                             residual record — a residual counted then dropped) or
                             ``"recorded_not_counted"`` (a blocking residual record
                             present while the coverage account counted 0 — a
                             recorded residual the count forgot).
        detail:              SELF-EVIDENCING message naming the stage, the coverage
                             violation count, and the blocking-residual-record count,
                             so the finding is auditable from the record alone.
    """

    code: str
    stage: str
    coverage_violation: int
    blocking_residuals: int
    direction: str
    detail: str


def _blocking_residual_count(residual_rows: Sequence[Mapping[str, Any]]) -> int:
    """Count BLOCKING residual records in one stage's committed residual ledger.

    Reads the committed ``residual_rows`` (the projection
    ``stage_result_ledger.residual_row`` produced) — ``blocking`` is the row field
    that says the residual forbids a clean claim. A row missing the key degrades to
    non-blocking (the conservative reading: only an explicitly-blocking record
    discharges a counted violation).
    """
    return sum(1 for row in residual_rows if bool(row.get("blocking", False)))


def sweep_stage_residual_ledger(
    account_rows: Sequence[Mapping[str, Any]],
) -> tuple[ResidualLedgerMonotonicityFinding, ...]:
    """Assert residual-ledger conservation over the committed stage accounts (EV-03).

    For each committed ``stage_account_row`` (``coverage_row`` + ``residual_rows``):

    * if ``coverage_row["violation"] > 0`` but the residual ledger commits ZERO
      blocking residual records, a signal-bearing residual was counted then
      dropped — ``RESIDUAL_LEDGER_NONMONOTONE`` (``counted_not_recorded``);
    * if ``coverage_row["violation"] == 0`` but a blocking residual record IS
      committed, a recorded residual was never counted — the dual silent loss
      (``recorded_not_counted``).

    Args:
        account_rows: The per-stage account rows the dossier folds (each a mapping
            with ``stage`` / ``coverage_row`` / ``residual_rows``).

    Returns:
        A tuple of :class:`ResidualLedgerMonotonicityFinding`, sorted by stage id.
        Empty when every stage's counted violations are discharged by a committed
        blocking residual record and vice versa (the green-corpus norm).

    Discipline (no re-derivation): the sweep NEVER recomputes a stage's partition
    or re-buckets a token. It reads the stage's OWN committed coverage count and its
    OWN committed residual records and asserts they agree on the *presence* of a
    signal-bearing residual.
    """
    findings: list[ResidualLedgerMonotonicityFinding] = []
    for row in account_rows:
        stage = str(row.get("stage", ""))
        coverage = row.get("coverage_row") or {}
        violation = int(coverage.get("violation", 0))
        blocking = _blocking_residual_count(row.get("residual_rows", ()) or ())
        if violation > 0 and blocking == 0:
            findings.append(
                ResidualLedgerMonotonicityFinding(
                    code=RESIDUAL_LEDGER_NONMONOTONE,
                    stage=stage,
                    coverage_violation=violation,
                    blocking_residuals=blocking,
                    direction="counted_not_recorded",
                    detail=(
                        f"stage {stage!r} coverage counts {violation} signal-bearing "
                        f"violation unit(s) but its committed residual ledger holds 0 "
                        f"blocking residual record(s): a residual counted in coverage "
                        f"vanished from the ledger with no witness (non-monotone)"
                    ),
                )
            )
        elif violation == 0 and blocking > 0:
            findings.append(
                ResidualLedgerMonotonicityFinding(
                    code=RESIDUAL_LEDGER_NONMONOTONE,
                    stage=stage,
                    coverage_violation=violation,
                    blocking_residuals=blocking,
                    direction="recorded_not_counted",
                    detail=(
                        f"stage {stage!r} commits {blocking} blocking residual "
                        f"record(s) but its coverage account counts 0 violation "
                        f"unit(s): a recorded residual the coverage partition forgot "
                        f"to count (non-monotone)"
                    ),
                )
            )
    findings.sort(key=lambda f: f.stage)
    return tuple(findings)


__all__ = [
    "RESIDUAL_LEDGER_NONMONOTONE",
    "ResidualLedgerMonotonicityFinding",
    "sweep_stage_residual_ledger",
]
