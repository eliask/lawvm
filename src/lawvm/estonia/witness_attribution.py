"""Estonia witness-attribution surface.

The EE analog of the UK effect-witness surface. For an EE ``(base_id, oracle_id)``
replay pair, this module maps each compiled operation's ``witness_rule_id`` back
to the source witness that produced it: which amending RT act/instruction is
responsible, what address it targets, and which operation family it belongs to.

Operations with no ``witness_rule_id`` are not hidden: they are tagged
``witness_attributed=False`` and carry a loud ``unattributed_witness_blind_spot``
tag so that every discovered-spec hypothesis remains individually traceable to
source (or is loudly flagged as a blind spot when it is not).

DIAGNOSTIC / READ-ONLY: this module only *reads* ``replay_ee_to_pit`` output
(``EEPitResult.compiled_ops``). It never edits the replay/compile path and
introduces no behavior change. All ordering is stable for byte-deterministic
reports (no timestamps, sorted keys).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.estonia.replay import replay_ee_to_pit


@runtime_checkable
class _ReplayResultLike(Protocol):
    """The read-only slice of ``EEPitResult`` this surface consumes.

    Stating it as a structural protocol keeps the surface diagnostic and
    decoupled: it depends only on the compiled ops and oracle/error provenance,
    never on the full replay result shape, and tests can pass a small mock.
    """

    @property
    def compiled_ops(self) -> tuple[LegalOperation, ...]: ...

    @property
    def oracle_id(self) -> Optional[str]: ...

    @property
    def error(self) -> Optional[str]: ...

# Loud, stable blind-spot tag for an op that carries no ``witness_rule_id``.
UNATTRIBUTED_WITNESS_BLIND_SPOT_TAG = "unattributed_witness_blind_spot"


@dataclass(frozen=True, slots=True)
class EESourceWitness:
    """The amending source that produced one compiled EE operation.

    ``amending_act_id`` is the ``OperationSource.statute_id`` (e.g.
    ``"ee/123032019003"``); ``locator`` is a human-readable source pointer.
    """

    amending_act_id: str
    locator: str
    title: str = ""
    enacted: str = ""
    effective: str = ""
    raw_text_excerpt: str = ""

    def to_jsonable(self) -> dict[str, str]:
        return {
            "amending_act_id": self.amending_act_id,
            "locator": self.locator,
            "title": self.title,
            "enacted": self.enacted,
            "effective": self.effective,
            "raw_text_excerpt": self.raw_text_excerpt,
        }


@dataclass(frozen=True, slots=True)
class EEOpWitnessRecord:
    """Per-op witness attribution for one compiled EE operation."""

    op_id: str
    sequence: int
    target_address: str
    operation_family: str
    witness_rule_id: Optional[str]
    witness_attributed: bool
    source_witness: Optional[EESourceWitness]
    provenance_tags: tuple[str, ...] = ()
    blind_spot_tags: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "sequence": self.sequence,
            "target_address": self.target_address,
            "operation_family": self.operation_family,
            "witness_rule_id": self.witness_rule_id,
            "witness_attributed": self.witness_attributed,
            "source_witness": (
                self.source_witness.to_jsonable() if self.source_witness is not None else None
            ),
            "provenance_tags": list(self.provenance_tags),
            "blind_spot_tags": list(self.blind_spot_tags),
        }


@dataclass(frozen=True, slots=True)
class EEWitnessAttributionSummary:
    """Key-sorted rollup of a witness-attribution surface."""

    n_ops: int
    n_attributed: int
    n_blind_spots: int
    by_operation_family: tuple[tuple[str, int], ...]
    by_witness_rule_id: tuple[tuple[str, int], ...]
    by_amending_act_id: tuple[tuple[str, int], ...]
    blind_spot_op_ids: tuple[str, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "n_ops": self.n_ops,
            "n_attributed": self.n_attributed,
            "n_blind_spots": self.n_blind_spots,
            "attribution_rate": (
                round(self.n_attributed / self.n_ops, 6) if self.n_ops else 0.0
            ),
            "by_operation_family": [list(pair) for pair in self.by_operation_family],
            "by_witness_rule_id": [list(pair) for pair in self.by_witness_rule_id],
            "by_amending_act_id": [list(pair) for pair in self.by_amending_act_id],
            "blind_spot_op_ids": list(self.blind_spot_op_ids),
        }


@dataclass(frozen=True, slots=True)
class EEWitnessAttributionSurface:
    """The full per-op witness-attribution surface for one replay pair."""

    base_id: str
    as_of: str
    oracle_id: str
    records: tuple[EEOpWitnessRecord, ...]
    summary: EEWitnessAttributionSummary
    replay_error: Optional[str] = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "as_of": self.as_of,
            "oracle_id": self.oracle_id,
            "replay_error": self.replay_error,
            "summary": self.summary.to_jsonable(),
            "records": [record.to_jsonable() for record in self.records],
        }


def _sorted_counts(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    """Stable, key-sorted ``(key, count)`` pairs.

    Sort by descending count then ascending key so the report is deterministic
    regardless of dict/insertion order.
    """
    return tuple(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _operation_family(op: LegalOperation) -> str:
    """Name the operation family for one op.

    Built from the canonical ``StructuralAction`` plus a text-patch
    discriminator so item/text replaces are distinguishable from structural
    ones. Never guesses a witness — it only describes the executable action.
    """
    action = op.action
    if not isinstance(action, StructuralAction):  # defensive; IR enforces the type
        return "unknown"
    base = action.value
    if action is StructuralAction.REPLACE and op.text_patch is not None:
        return "replace_text_patch"
    return base


def _source_witness_for(op: LegalOperation) -> Optional[EESourceWitness]:
    source = op.source
    if source is None:
        return None
    excerpt = (source.raw_text or "").strip()
    if len(excerpt) > 240:
        excerpt = excerpt[:240]
    return EESourceWitness(
        amending_act_id=source.statute_id,
        locator=source.statute_id,
        title=source.title or "",
        enacted=source.enacted or "",
        effective=source.effective or "",
        raw_text_excerpt=excerpt,
    )


def _record_for(op: LegalOperation) -> EEOpWitnessRecord:
    witness_rule_id = op.witness_rule_id or None
    attributed = witness_rule_id is not None
    blind_spot_tags: tuple[str, ...] = ()
    if not attributed:
        blind_spot_tags = (UNATTRIBUTED_WITNESS_BLIND_SPOT_TAG,)
    return EEOpWitnessRecord(
        op_id=op.op_id,
        sequence=op.sequence,
        target_address=str(op.target),
        operation_family=_operation_family(op),
        witness_rule_id=witness_rule_id,
        witness_attributed=attributed,
        source_witness=_source_witness_for(op),
        provenance_tags=tuple(op.provenance_tags),
        blind_spot_tags=blind_spot_tags,
    )


def build_ee_op_witness_attribution_from_ops(
    *,
    base_id: str,
    as_of: str,
    oracle_id: str,
    compiled_ops: tuple[LegalOperation, ...],
    replay_error: Optional[str] = None,
) -> EEWitnessAttributionSurface:
    """Build the surface from already-compiled ops (no replay run).

    This is the deterministic core used by both the live and the fixtured
    entrypoints, so tests can exercise it without the RT archive.
    """
    records = tuple(
        _record_for(op)
        for op in sorted(compiled_ops, key=lambda o: (o.sequence, o.op_id))
    )

    family_counter: Counter[str] = Counter()
    rule_counter: Counter[str] = Counter()
    act_counter: Counter[str] = Counter()
    blind_spot_op_ids: list[str] = []
    n_attributed = 0
    for record in records:
        family_counter[record.operation_family] += 1
        if record.witness_attributed and record.witness_rule_id is not None:
            n_attributed += 1
            rule_counter[record.witness_rule_id] += 1
        else:
            blind_spot_op_ids.append(record.op_id)
        if record.source_witness is not None:
            act_counter[record.source_witness.amending_act_id] += 1
        else:
            act_counter["<no_source>"] += 1

    summary = EEWitnessAttributionSummary(
        n_ops=len(records),
        n_attributed=n_attributed,
        n_blind_spots=len(blind_spot_op_ids),
        by_operation_family=_sorted_counts(family_counter),
        by_witness_rule_id=_sorted_counts(rule_counter),
        by_amending_act_id=_sorted_counts(act_counter),
        blind_spot_op_ids=tuple(sorted(blind_spot_op_ids)),
    )

    return EEWitnessAttributionSurface(
        base_id=base_id,
        as_of=as_of,
        oracle_id=oracle_id,
        records=records,
        summary=summary,
        replay_error=replay_error,
    )


def build_ee_op_witness_attribution(
    base_id: str,
    as_of: str,
    *,
    oracle_id: Optional[str] = None,
    archive: Any = None,
    result: Optional[_ReplayResultLike] = None,
) -> EEWitnessAttributionSurface:
    """Build an EE witness-attribution surface for one replay pair.

    Either pass an existing ``EEPitResult`` (``result=``) — preferred, reuses the
    replay output without re-running it — or let this function run
    ``replay_ee_to_pit`` itself. Read-only: it never mutates the replay result.
    """
    replay_result: _ReplayResultLike
    if result is None:
        replay_result = replay_ee_to_pit(
            base_id,
            as_of,
            archive=archive,
            oracle_id=oracle_id,
        )
    else:
        replay_result = result

    resolved_oracle_id = oracle_id or (replay_result.oracle_id or "")
    return build_ee_op_witness_attribution_from_ops(
        base_id=base_id,
        as_of=as_of,
        oracle_id=resolved_oracle_id,
        compiled_ops=tuple(replay_result.compiled_ops),
        replay_error=replay_result.error,
    )
