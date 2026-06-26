"""Unified cross-jurisdiction benchmark contract.

Every jurisdiction's bench comparator emits a :class:`BenchUnitResult`. The
contract fixes the *shape* and the *honesty invariants*, never the metric
formula (each jurisdiction's oracle ontology is genuinely different — a single
universal formula would be a category error).

See ``notes/UNIFIED_BENCH_CONTRACT.md`` for the normative design.

Key properties:

- Canonical quantity is **error** (``[0, 1]``, ``0`` = perfect), not accuracy.
- Two canonical axes per scored unit: ``structural_err`` and ``text_err``,
  in the same units across all jurisdictions. An axis a jurisdiction does not
  compute is ``None`` (not attempted), never ``0`` (false perfection).
- Headline error is the **worst-of** (max) the attempted axes — the Liebig
  binding constraint, not a mean.
- A scored unit's ``structural_err`` must reconcile with its typed
  ``residue_buckets``: positive error iff there is typed residue. No silent
  unexplained error, no phantom residue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class BenchStatus(str, Enum):
    """Uniform per-unit benchmark status.

    Only ``SCORED`` carries axis errors. ``NO_TRUTH``, ``SOURCE_UNAVAILABLE``,
    and ``ORACLE_STALE`` are *non-scored*: excluded from scoring and **not**
    failures. ``CRASH`` is the only genuine failure (unexpected exception).
    """

    SCORED = "scored"
    NO_TRUTH = "no_truth"
    SOURCE_UNAVAILABLE = "source_unavailable"
    ORACLE_STALE = "oracle_stale"
    CRASH = "crash"


#: Statuses excluded from scoring that are NOT failures.
NON_SCORED_STATUSES: frozenset[BenchStatus] = frozenset(
    {
        BenchStatus.NO_TRUTH,
        BenchStatus.SOURCE_UNAVAILABLE,
        BenchStatus.ORACLE_STALE,
    }
)


class BenchContractError(ValueError):
    """A :class:`BenchUnitResult` violated a contract invariant."""


def _validate_axis(name: str, value: float | None) -> None:
    if value is None:
        return
    if not (0.0 <= value <= 1.0):
        raise BenchContractError(
            f"{name} must be in [0, 1] (0 = perfect) or None (not attempted), got {value!r}"
        )


@dataclass(frozen=True, slots=True)
class BenchUnitResult:
    """One scored (or non-scored) benchmark unit, jurisdiction-agnostic.

    Parameters
    ----------
    unit_id:
        Stable identifier for the unit (statute id, work id, window id, …).
    bench_unit_status:
        :class:`BenchStatus`. Only ``SCORED`` units carry axis errors.
    structural_err:
        Structural divergence error in ``[0, 1]`` (``0`` = structurally
        perfect), or ``None`` if the jurisdiction does not compute a structural
        axis. Must reconcile with ``residue_buckets`` (see
        :func:`check_residue_reconciliation`).
    text_err:
        Text divergence error in ``[0, 1]`` (``0`` = text-identical), or
        ``None`` if not attempted.
    residue_buckets:
        Typed residue families -> count. The discrete structural-event families
        the comparator emitted for this unit. Must explain ``structural_err``.
    witnesses:
        Opaque sampled evidence pointers (e.g. mismatching keys), for triage.
    """

    unit_id: str
    bench_unit_status: BenchStatus
    structural_err: float | None = None
    text_err: float | None = None
    residue_buckets: Mapping[str, int] = field(default_factory=dict)
    witnesses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_axis("structural_err", self.structural_err)
        _validate_axis("text_err", self.text_err)
        if self.bench_unit_status is not BenchStatus.SCORED:
            # Non-scored / crashed units must not carry axis errors — that would
            # silently leak them into aggregates. Fail loud.
            if self.structural_err is not None or self.text_err is not None:
                raise BenchContractError(
                    f"unit {self.unit_id!r} has status {self.bench_unit_status.value!r} but carries "
                    f"axis errors (structural={self.structural_err!r}, text={self.text_err!r}); "
                    "only SCORED units may carry errors"
                )

    @property
    def is_scored(self) -> bool:
        return self.bench_unit_status is BenchStatus.SCORED

    @property
    def is_failure(self) -> bool:
        """A genuine failure (crash), as opposed to a non-scored exclusion."""
        return self.bench_unit_status is BenchStatus.CRASH

    @property
    def attempted_axes(self) -> tuple[float, ...]:
        """The non-``None`` axis errors for this unit."""
        return tuple(v for v in (self.structural_err, self.text_err) if v is not None)

    def headline_error(self) -> float | None:
        """Worst-of (max) the attempted axes — the Liebig binding constraint.

        Returns ``None`` for non-scored units or when no axis was attempted.
        """
        axes = self.attempted_axes
        if not axes:
            return None
        return max(axes)

    def headline_accuracy(self) -> float | None:
        """``1 - headline_error`` for display, or ``None`` if no headline."""
        err = self.headline_error()
        return None if err is None else 1.0 - err


def headline_error(result: BenchUnitResult) -> float | None:
    """Worst-of headline error for *result* (free-function form)."""
    return result.headline_error()


def check_residue_reconciliation(result: BenchUnitResult) -> None:
    """Enforce the structural-axis residue-reconciliation invariant.

    For a ``SCORED`` unit with a structural axis:

    - ``structural_err > 0``  ⟺  ``sum(residue_buckets.values()) > 0``.

    No silent unexplained structural error (positive error with no typed
    residue) and no phantom residue (typed residue but zero structural error).
    The text axis is continuous and not event-typed, so it is not reconciled
    here.

    Raises :class:`BenchContractError` on violation.
    """
    if result.bench_unit_status is not BenchStatus.SCORED:
        return
    if result.structural_err is None:
        return
    residue_total = sum(int(v) for v in result.residue_buckets.values())
    has_error = result.structural_err > 0.0
    if has_error and residue_total == 0:
        raise BenchContractError(
            f"unit {result.unit_id!r}: structural_err={result.structural_err!r} > 0 but "
            "residue_buckets is empty — silent unexplained structural error"
        )
    if not has_error and residue_total > 0:
        raise BenchContractError(
            f"unit {result.unit_id!r}: structural_err == 0 but residue_buckets has "
            f"{residue_total} typed residue events — phantom residue"
        )


def residue_reconciliation_violation(result: BenchUnitResult) -> str | None:
    """Return a human-readable violation message, or ``None`` if reconciled."""
    try:
        check_residue_reconciliation(result)
    except BenchContractError as exc:
        return str(exc)
    return None
