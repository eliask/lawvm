"""us_ordering.py — US federal enactment-order ordering profile (task #105).

US joined the apply seam at char-span (task #86), but same-moment / ``order_ops``
was explicitly DEFERRED: ``us_federal/dry_run.py`` sorted its lowered Public-Law
reports by ``(enacted_date, statute_id)`` and applied them in that list order, so
a same-moment cross-act collision (two acts amending the SAME section at the SAME
effective moment with incompatible whole-target payloads) was silently
materialized by iteration order with ZERO finding. EU got same-moment detection
in its Increment 0 (``eu/eu_ordering.py``); the tree frontends in Wave 0. US was
the last ordering-cascade gap.

This module supplies the US :class:`OrderingProfile` so the lowered op stream
routes through the shared :func:`lawvm.core.op_ordering.order_ops`:

  - ``temporal_key`` = ``(enacted-or-statute-id, statute_id, sequence)`` — EXACTLY
    the prior ``dry_run.py`` ``lowered_reports.sort(key=lambda x: (x[0] or x[1],
    x[1]))`` plus the within-report instruction order (``op.sequence``, which the
    lowerer assigns monotonically per Public Law). So ``order_ops`` returns the
    ops in the SAME application order the old sort + per-report iteration
    produced — byte-identical where no collision exists.
  - ``same_moment_effective_date_of`` = ``effective or enacted`` — the shared
    detector buckets same-moment conflicts by ``OperationSource.effective``, but
    the dominant US case is an undated-effective amendment that applies AT
    ENACTMENT (``effective`` empty). The US "same moment" is therefore the
    enactment date when no explicit effective date is parsed; this accessor makes
    the detector bucket by exactly the application moment the
    ``(enacted_date, statute_id)`` order already uses, so a genuine
    same-enacted-date cross-act collision on one section is surfaced rather than
    silently order-resolved.
  - ``finder_kind_prefix="us"`` — keeps US same-moment findings frontend-distinct
    (kind ``us_same_moment_cross_act_incompatible_payload_ambiguous``), mirroring
    EU/EE/NO.
  - default incompatible-payload predicate (no US-specific re-implementation): US
    ops carry ``StructuralAction`` enum actions, which the shared default
    conservative predicate classifies directly. US has no validated
    precedence-rule registry, so every detected conflict emits
    ``resolution: "sequence_order_unproven"`` (AGENTS.md §1.7 — preserve
    uncertainty; do not magically pick a winner).

The change is ADDITIVE: ``order_ops`` (the shared detector) never reorders or
drops an op — apply order stays the deterministic temporal+sequence order
(byte-identical to the old sort for non-colliding ops) and the detector only ADDS
a finding when a real same-moment incompatible-payload collision exists. No core
algebra change beyond a defaulted ``same_moment_effective_date_of`` seam on the
shared ``OrderingProfile`` / detector (every other frontend passes ``None`` and
is byte-identical).
"""

from __future__ import annotations

from lawvm.core.ir import LegalOperation
from lawvm.core.op_ordering import OrderedOps, OrderingProfile, order_ops

__all__ = [
    "us_temporal_key",
    "us_same_moment_effective_date",
    "us_ordering_profile",
    "order_us_ops",
]


def us_temporal_key(op: LegalOperation) -> tuple[str, str, int]:
    """US temporal sort key: ``(enacted_or_statute_id, statute_id, sequence)``.

    Reproduces ``dry_run.py``'s prior ``lowered_reports.sort`` key
    ``(report.enacted or statute_id, statute_id)`` (the enactment-order Public-Law
    sort) followed by ``op.sequence`` — the lowerer's per-Public-Law instruction
    sequence, which is the within-report application order the old per-report
    iteration used. ``op.source.enacted`` mirrors ``report.enacted`` and
    ``op.source.statute_id`` mirrors ``report.statute_id`` for every op of a
    report, so this key is the exact tuple the old sort + iteration applied in.
    """
    enacted = op.source.enacted if op.source and op.source.enacted else ""
    statute_id = op.source.statute_id if op.source and op.source.statute_id else ""
    # ``report.enacted or statute_id`` — fall back to statute id when the act
    # carries no parsed enactment date (mirrors the prior ``x[0] or x[1]``).
    primary = enacted or statute_id
    return (primary, statute_id, op.sequence)


def us_same_moment_effective_date(op: LegalOperation) -> str:
    """US same-moment bucketing date: ``effective or enacted`` (``""`` if neither).

    The shared detector groups same-moment conflicts by this date. US amendments
    that carry no parsed future-effective date apply AT ENACTMENT, so the
    enactment date is their same-moment key; an explicit parsed ``effective`` date
    (future-effective / sunset-conditioned amendment) takes precedence when
    present. Ops with neither date sort out of same-moment bucketing entirely
    (the shared detector excludes empty-date records — an honest gap, never a
    manufactured collision).
    """
    if op.source is None:
        return ""
    effective = str(getattr(op.source, "effective", "") or "")
    if effective:
        return effective
    return str(getattr(op.source, "enacted", "") or "")


def us_ordering_profile() -> OrderingProfile:
    """The US federal ordering profile fed to the unified kernel (task #105).

    Encodes the prior ``dry_run.py`` enactment-order contract so
    ``order_ops(ops, us_ordering_profile())`` returns the ops in the SAME
    application order the old ``lowered_reports.sort`` + per-report iteration
    produced (byte-identical for non-colliding ops), while ADDING the shared
    same-moment cross-act conflict finding (bucketed by ``effective or enacted``)
    that the old order-dependent path silently resolved.

    - ``finder_kind_prefix="us"`` — US-distinct finding kind / rule ids.
    - ``temporal_key=us_temporal_key`` — ``(enacted_or_statute_id, statute_id,
      sequence)``, the old sort key + within-report instruction order.
    - ``same_moment_effective_date_of=us_same_moment_effective_date`` —
      ``effective or enacted`` (US applies undated amendments at enactment).
    - ``incompatible_payload_predicate=None`` — the shared default conservative
      predicate (no US re-implementation).
    - no ``precedence_claims`` — US has no validated precedence-rule registry yet
      (every conflict emits ``resolution: "sequence_order_unproven"``).
    - ``lex_posterior=False``, ``prospective_gate``/``renumber_vacate`` unset.
    """
    return OrderingProfile(
        finder_kind_prefix="us",
        temporal_key=us_temporal_key,
        same_moment_effective_date_of=us_same_moment_effective_date,
    )


def order_us_ops(ops: list[LegalOperation]) -> OrderedOps:
    """Order US lowered ops in enactment order via the shared core algebra.

    Returns the ordered op tuple (byte-identical application order to the prior
    enactment sort for non-colliding ops) plus any same-moment cross-act conflict
    findings (the gap the old order-dependent path silently resolved).
    """
    return order_ops(ops, us_ordering_profile())
