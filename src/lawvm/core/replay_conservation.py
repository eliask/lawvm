"""Replay-side conservation law: every source effect leaves with a receipt.

This is the *twin* of the scoring-side residue-reconciliation invariant in
:mod:`lawvm.core.bench_contract`. Where that side guarantees
``structural_err > 0 ⟺ Σ residue_buckets > 0`` ("every scored error is typed"),
this side guarantees the analogous property on the *replay* boundary:

    For each source effect, EXACTLY ONE of
      {an op was emitted, a typed rejection/finding was emitted}
    holds — and every emitted op traces back to a source warrant.

That single invariant subsumes three distinct silent-failure classes:

- **silent-drop**     a source effect that produced neither an op nor a typed
                      rejection (it vanished untyped);
- **silent-consume**  a source effect marked *handled* that produced neither an
                      op nor a finding (the ``handled=True`` with no ops and no
                      finding class);
- **silent-widen**    an emitted op with no source warrant (a phantom op the
                      replay invented).

The Finland johtolause five-bucket census
(:mod:`lawvm.finland.johtolause.census_accounting`) is the canonical *instance*
of the effect side of this law: every amendment clause lands in EXACTLY ONE of
{owned-0delta / registered-fallback / unregistered / genuine-delta-*}, and the
five buckets PARTITION the corpus. This module lifts that partition shape into a
jurisdiction-agnostic primitive so any frontend can certify the same property
over its own (source effect -> {op | typed finding}) ledger.

The shape is deliberately tiny and frontend-agnostic: a frontend supplies, per
unit, the list of :class:`EffectReceipt`s its replay produced. Each receipt is
the *accounting record* for one source effect — what disposition it received.
:func:`check_effect_conservation` then enforces the partition: no effect may be
unaccounted (untyped), and no op may be unwarranted (phantom).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class EffectDisposition(str, Enum):
    """The disposition a single source effect received during replay.

    This is the closed set of *outcomes* a frontend may assign to a source
    effect. It is the receipt's type. The partition law: every source effect
    must carry exactly one of these, and the optimistic-looking dispositions
    (``OP_EMITTED``) are NOT the silent default — a frontend that cannot place
    an effect must say so with ``TYPED_REJECTION`` or ``TYPED_OBSERVATION``,
    never let it fall off the edge.
    """

    #: The effect lowered to one or more replay operations (the ownership case).
    OP_EMITTED = "op_emitted"
    #: The effect was declined with a typed, registered rejection/finding
    #: (the accounted fallback case — e.g. FI ``OutOfScope`` -> registered class).
    TYPED_REJECTION = "typed_rejection"
    #: The effect was consumed as a typed, non-blocking observation that
    #: deliberately emits no op (e.g. a presentation-only or provenance-only
    #: effect). Distinct from ``TYPED_REJECTION`` only in blocking semantics;
    #: both are *typed* receipts and so both satisfy conservation.
    TYPED_OBSERVATION = "typed_observation"


#: Dispositions that count as a *typed* receipt (an effect that left with a
#: receipt rather than vanishing). All members are typed — the point of the
#: closed enum is that there is no untyped member to leak into.
TYPED_DISPOSITIONS: frozenset[EffectDisposition] = frozenset(EffectDisposition)


class ReplayConservationError(ValueError):
    """An :class:`EffectLedger` violated the effect-conservation invariant."""


@dataclass(frozen=True, slots=True)
class EffectReceipt:
    """The accounting record for ONE source effect's replay disposition.

    Parameters
    ----------
    effect_id:
        Stable identifier of the source effect (clause id, instruction id,
        effect-feed row id, …). Must be non-empty: an effect with no identity
        cannot be reconciled.
    disposition:
        Which :class:`EffectDisposition` this effect received.
    op_count:
        Number of replay operations emitted for this effect. Must be ``> 0`` iff
        ``disposition is OP_EMITTED`` (an op-emitting effect with zero ops is a
        silent-consume; a typed-rejection with ops is a contradiction).
    finding_kind:
        The typed finding/rejection kind, REQUIRED (non-empty) for the
        ``TYPED_REJECTION`` and ``TYPED_OBSERVATION`` dispositions — that string
        is the *type* that makes the disposition non-silent. Empty for
        ``OP_EMITTED``.
    warrant:
        Opaque source warrant pointer for the emitted op(s) (e.g. the source
        span / instruction id the op traces back to). REQUIRED (non-empty) for
        ``OP_EMITTED`` so that every emitted op traces to a source warrant
        (the no-silent-widen / no-phantom-op direction).
    """

    effect_id: str
    disposition: EffectDisposition
    op_count: int = 0
    finding_kind: str = ""
    warrant: str = ""

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ReplayConservationError(
                "EffectReceipt.effect_id must be non-empty — an effect with no "
                "identity cannot be reconciled"
            )
        if self.op_count < 0:
            raise ReplayConservationError(
                f"effect {self.effect_id!r}: op_count must be >= 0, got {self.op_count}"
            )
        if self.disposition is EffectDisposition.OP_EMITTED:
            if self.op_count <= 0:
                raise ReplayConservationError(
                    f"effect {self.effect_id!r}: disposition OP_EMITTED but op_count="
                    f"{self.op_count} (<= 0) — a 'handled' effect that emitted no op and "
                    "no finding is a SILENT-CONSUME; emit a typed rejection/observation "
                    "instead"
                )
            if not self.warrant:
                raise ReplayConservationError(
                    f"effect {self.effect_id!r}: OP_EMITTED with empty warrant — every "
                    "emitted op must trace to a source warrant (no phantom op / "
                    "silent-widen)"
                )
        else:
            # Typed rejection / observation: must carry a non-empty type, and
            # must NOT also claim emitted ops (that would be two dispositions).
            if not self.finding_kind:
                raise ReplayConservationError(
                    f"effect {self.effect_id!r}: disposition {self.disposition.value!r} "
                    "but empty finding_kind — a typed disposition MUST carry the kind "
                    "string that types it (otherwise it is an untyped, silent drop)"
                )
            if self.op_count != 0:
                raise ReplayConservationError(
                    f"effect {self.effect_id!r}: disposition {self.disposition.value!r} "
                    f"but op_count={self.op_count} != 0 — an effect cannot both be a "
                    "typed rejection/observation AND emit ops (exactly one disposition "
                    "per effect)"
                )

    @property
    def is_typed_receipt(self) -> bool:
        """True — every well-formed receipt is a typed receipt (closed enum)."""
        return self.disposition in TYPED_DISPOSITIONS


@dataclass(frozen=True, slots=True)
class EffectLedger:
    """The per-unit (source effect -> receipt) accounting ledger.

    Parameters
    ----------
    unit_id:
        Stable identifier of the replayed unit (statute id, work id, window id).
    source_effect_ids:
        The COMPLETE set of source effect ids the unit's source produced — the
        denominator of the conservation law. Every id here must appear in
        exactly one receipt (no silent drop), and no receipt may reference an id
        absent here (no phantom effect).
    receipts:
        One receipt per source effect.
    """

    unit_id: str
    source_effect_ids: tuple[str, ...]
    receipts: tuple[EffectReceipt, ...] = ()

    @property
    def emitted_op_total(self) -> int:
        return sum(r.op_count for r in self.receipts)

    def disposition_counts(self) -> dict[str, int]:
        """Receipt count by disposition value (the partition's bucket counts)."""
        counts = {d.value: 0 for d in EffectDisposition}
        for r in self.receipts:
            counts[r.disposition.value] += 1
        return counts


def conservation_violations(ledger: EffectLedger) -> list[str]:
    """Return every effect-conservation violation in *ledger* (empty == clean).

    Enforces the partition law over the ledger:

    1. **No silent drop** — every ``source_effect_id`` has exactly one receipt.
    2. **No phantom effect** — every receipt references a known source effect.
    3. **No duplicate receipt** — no effect is accounted twice.

    The per-receipt invariants (no silent-consume, no untyped disposition, no
    phantom op / silent-widen) are enforced eagerly in
    :meth:`EffectReceipt.__post_init__`, so a well-formed receipt already
    guarantees them; this function adds the *cross-receipt* partition checks.
    """
    violations: list[str] = []
    source_ids = list(ledger.source_effect_ids)
    source_set = set(source_ids)
    if len(source_set) != len(source_ids):
        dupes = sorted({i for i in source_ids if source_ids.count(i) > 1})
        violations.append(
            f"unit {ledger.unit_id!r}: duplicate source_effect_ids {dupes} — the "
            "effect denominator must be a set"
        )

    seen: dict[str, int] = {}
    for r in ledger.receipts:
        seen[r.effect_id] = seen.get(r.effect_id, 0) + 1

    # No duplicate receipt (an effect accounted twice).
    for eid, n in sorted(seen.items()):
        if n > 1:
            violations.append(
                f"unit {ledger.unit_id!r}: effect {eid!r} has {n} receipts — an effect "
                "must receive EXACTLY ONE disposition"
            )

    # No silent drop: every source effect has a receipt.
    for eid in sorted(source_set - set(seen)):
        violations.append(
            f"unit {ledger.unit_id!r}: source effect {eid!r} has NO receipt — it left "
            "with neither an op nor a typed finding (SILENT DROP)"
        )

    # No phantom effect: every receipt references a known source effect.
    for eid in sorted(set(seen) - source_set):
        violations.append(
            f"unit {ledger.unit_id!r}: receipt references effect {eid!r} which is not in "
            "the source effect set (PHANTOM EFFECT / silent-widen at the ledger level)"
        )

    return violations


def check_effect_conservation(ledger: EffectLedger) -> None:
    """Raise :class:`ReplayConservationError` if *ledger* violates conservation."""
    violations = conservation_violations(ledger)
    if violations:
        raise ReplayConservationError(
            f"effect conservation violated for unit {ledger.unit_id!r}:\n  "
            + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------
# Partition census — the jurisdiction-agnostic lift of the FI five-bucket
# census. A frontend hands a sequence of *outcome bucket ids* (one per unit of
# accounting) plus the closed set of bucket ids that partition its domain, and
# this certifies the partition: every accounted item lands in exactly one
# declared bucket, and the bucket counts sum to the total (no "other" bucket,
# nothing off the edge).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionCensus:
    """A closed-bucket partition over a counted domain.

    Parameters
    ----------
    total:
        Total number of accounted items (the denominator).
    bucket_ids:
        The CLOSED set of bucket ids that partition the domain (report order).
    counts:
        ``bucket_id -> count``. Keys must be exactly ``bucket_ids``.
    unaccounted_bucket_ids:
        The subset of ``bucket_ids`` that MUST be zero (the closed-set breach
        buckets — e.g. FI ``legacy_fallback_unregistered``). A non-zero count in
        any of these is a hard breach, surfaced distinctly from a mere partition
        mismatch.
    """

    total: int
    bucket_ids: tuple[str, ...]
    counts: Mapping[str, int]
    unaccounted_bucket_ids: tuple[str, ...] = ()

    @property
    def partition_total(self) -> int:
        return sum(int(v) for v in self.counts.values())

    def is_partition(self) -> bool:
        return self.partition_total == self.total


def partition_violations(census: PartitionCensus) -> list[str]:
    """Return every partition violation in *census* (empty == a clean partition)."""
    violations: list[str] = []
    declared = set(census.bucket_ids)
    present = set(census.counts)

    missing = sorted(declared - present)
    if missing:
        violations.append(
            f"census missing declared bucket(s) {missing} — every closed-set bucket "
            "must be materialized (even at zero), so nothing falls off the edge"
        )
    extra = sorted(present - declared)
    if extra:
        violations.append(
            f"census has undeclared bucket(s) {extra} — a bucket outside the closed set "
            "is an untyped 'other' bucket (partition leak)"
        )
    if not census.is_partition():
        violations.append(
            f"partition sum {census.partition_total} != total {census.total} — items "
            "vanished untyped or were double-counted"
        )
    for bid in census.unaccounted_bucket_ids:
        n = int(census.counts.get(bid, 0))
        if n != 0:
            violations.append(
                f"closed-set BREACH: bucket {bid!r} must be 0 but is {n} — an "
                "un-accounted disposition leaked through (e.g. an unregistered decline)"
            )
    return violations


def check_partition(census: PartitionCensus) -> None:
    """Raise :class:`ReplayConservationError` if *census* is not a clean partition."""
    violations = partition_violations(census)
    if violations:
        raise ReplayConservationError(
            "partition census violated:\n  " + "\n  ".join(violations)
        )


def census_from_bucket_assignments(
    assignments: Sequence[str],
    bucket_ids: Sequence[str],
    *,
    unaccounted_bucket_ids: Sequence[str] = (),
) -> PartitionCensus:
    """Build a :class:`PartitionCensus` from a per-item bucket-id assignment list.

    *assignments* is one bucket id per accounted item; *bucket_ids* the closed
    set. An assignment to a bucket outside the closed set is itself a partition
    violation (surfaced by :func:`partition_violations`), so this never silently
    invents a bucket — the count for an out-of-set id is materialized under that
    id and flagged as undeclared.
    """
    counts: dict[str, int] = {b: 0 for b in bucket_ids}
    for a in assignments:
        counts[a] = counts.get(a, 0) + 1
    return PartitionCensus(
        total=len(assignments),
        bucket_ids=tuple(bucket_ids),
        counts=counts,
        unaccounted_bucket_ids=tuple(unaccounted_bucket_ids),
    )
