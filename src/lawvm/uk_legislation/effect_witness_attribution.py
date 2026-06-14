"""Per-compiled-op effect-feed witness attribution (diagnostic, read-only).

Each UK effect-feed row compiles into one or more :class:`LegalOperation`
records.  Every op carries a ``witness_rule_id`` (often ``None``) and a
``group_id`` that is exactly the source ``effect_id`` (see
``witness_builders._uk_temporal_group_id``).  The broad baseline accumulates
large status buckets across effects, but until now there was no surface that
maps an *individual* compiled op's ``witness_rule_id`` back to the source
witness that produced it: which effect-feed row, which affecting-act fragment
locator, which action family, which owning phase, and which adjudication bucket.

This module builds that attribution surface.  It is a pure observation: it
reads already-compiled ops + effect rows + effect diagnostics and never alters
replay, lowering, target resolution, or scoring.  Every emitted record either
carries a non-empty ``witness_rule_id`` or is loudly tagged
``unattributed_witness_blind_spot`` — a compiled op's witness is never silently
blank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    uk_nonstructural_replay_candidate_family_for_effect_type,
)
from lawvm.uk_legislation.phase_discipline import (
    UK_PHASE_CANONICAL_OP_COMPILATION,
    uk_phase_owner_for_diagnostic,
)

# Loud sentinel for a compiled op whose witness rule the frontend never stamped.
UNATTRIBUTED_WITNESS_BLIND_SPOT = "unattributed_witness_blind_spot"

_MANUAL_FRONTIER_RULE_ID = "uk_manual_compile_frontier_classified"


@dataclass(frozen=True, slots=True)
class UKEffectSourceWitness:
    """Source-side witness for one compiled effect op.

    ``effect_row_id`` is the effect-feed row id (the op ``group_id``).
    ``affecting_fragment_locator`` is the affecting-act XML fragment locator
    (``<affecting_act_id> <affecting_provisions>``) the row was extracted from.
    ``authority_layer`` records which lane the source text came from.
    """

    effect_row_id: str
    affecting_act_id: str
    affecting_provisions: str
    affecting_fragment_locator: str
    authority_layer: str
    authority_layer_source: str
    source_lane: str
    effect_row_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_row_id": self.effect_row_id,
            "affecting_act_id": self.affecting_act_id,
            "affecting_provisions": self.affecting_provisions,
            "affecting_fragment_locator": self.affecting_fragment_locator,
            "authority_layer": self.authority_layer,
            "authority_layer_source": self.authority_layer_source,
            "source_lane": self.source_lane,
            "effect_row_present": self.effect_row_present,
        }


@dataclass(frozen=True, slots=True)
class UKEffectWitnessAttributionRecord:
    """Attribution of one compiled op back to its source effect witness."""

    op_id: str
    sequence: int
    target: str
    action: str
    witness_rule_id: str
    witness_attributed: bool
    action_family: str
    phase_owner: str
    adjudication_bucket: str
    source_witness: UKEffectSourceWitness

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "sequence": self.sequence,
            "target": self.target,
            "action": self.action,
            "witness_rule_id": self.witness_rule_id,
            "witness_attributed": self.witness_attributed,
            "action_family": self.action_family,
            "phase_owner": self.phase_owner,
            "adjudication_bucket": self.adjudication_bucket,
            "source_witness": self.source_witness.to_dict(),
        }


def _effect_rows_by_id(
    effect_rows: Iterable[UKEffectRecord],
) -> dict[str, UKEffectRecord]:
    by_id: dict[str, UKEffectRecord] = {}
    for effect in effect_rows:
        effect_id = str(effect.effect_id or "")
        if effect_id and effect_id not in by_id:
            by_id[effect_id] = effect
    return by_id


def _manual_frontier_by_effect_id(
    effect_diagnostics: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Index manual-frontier classifications by effect_id (first wins, stable)."""
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in effect_diagnostics:
        if str(row.get("rule_id") or "") != _MANUAL_FRONTIER_RULE_ID:
            continue
        effect_id = str(row.get("effect_id") or "")
        if effect_id and effect_id not in by_id:
            by_id[effect_id] = row
    return by_id


def _action_family_for(
    op: LegalOperation,
    effect: Optional[UKEffectRecord],
) -> str:
    """Return the action family for one compiled op.

    Prefer the effect-feed type's nonstructural replay family when available;
    otherwise fall back to the canonical structural action of the op.  This is
    diagnostic only — it labels, it does not gate.
    """
    if effect is not None:
        family = uk_nonstructural_replay_candidate_family_for_effect_type(
            effect.effect_type or ""
        )
        if family:
            return family
    return _action_name(op.action)


def _adjudication_bucket_for(
    frontier_row: Optional[Mapping[str, Any]],
) -> str:
    if frontier_row is None:
        return "compiled_no_manual_frontier_record"
    status = str(frontier_row.get("manual_compile_status") or "")
    return status or "compiled_no_manual_frontier_record"


def _source_witness_for(
    op: LegalOperation,
    effect: Optional[UKEffectRecord],
    frontier_row: Optional[Mapping[str, Any]],
    effect_row_id: str,
) -> UKEffectSourceWitness:
    affecting_act_id = ""
    affecting_provisions = ""
    if effect is not None:
        affecting_act_id = str(effect.affecting_act_id or "")
        affecting_provisions = str(effect.affecting_provisions or "")

    # Prefer the lane recorded by the manual-frontier source witness (it knows
    # whether the source text was extracted from the affecting act or only the
    # effect-feed row); fall back to the op's branch authority layer.
    source_lane = ""
    authority_layer = ""
    authority_layer_source = "op_source_branch_authority"
    if frontier_row is not None:
        witness = frontier_row.get("source_witness")
        if isinstance(witness, Mapping):
            source_lane = str(witness.get("source_lane") or "")
            if not affecting_act_id:
                affecting_act_id = str(witness.get("artifact_id") or "")
            metadata = witness.get("metadata")
            if isinstance(metadata, Mapping) and not affecting_provisions:
                affecting_provisions = str(metadata.get("affecting_provisions") or "")
    if source_lane:
        authority_layer = source_lane
        authority_layer_source = "manual_frontier_source_witness_lane"
    else:
        op_source = getattr(op, "source", None)
        authority_layer = str(getattr(op_source, "authority_layer", "") or "")

    fragment_locator = " ".join(
        part for part in (affecting_act_id, affecting_provisions) if part
    )
    return UKEffectSourceWitness(
        effect_row_id=effect_row_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        affecting_fragment_locator=fragment_locator,
        authority_layer=authority_layer,
        authority_layer_source=authority_layer_source,
        source_lane=source_lane,
        effect_row_present=effect is not None,
    )


def _phase_owner_for(
    op: LegalOperation,
    frontier_row: Optional[Mapping[str, Any]],
) -> str:
    if frontier_row is not None:
        return uk_phase_owner_for_diagnostic(frontier_row)
    # A compiled op with no manual-frontier record is owned by op compilation.
    return UK_PHASE_CANONICAL_OP_COMPILATION


def build_uk_effect_witness_attribution(
    *,
    ops: Sequence[LegalOperation],
    effect_rows: Iterable[UKEffectRecord] = (),
    effect_diagnostics: Iterable[Mapping[str, Any]] = (),
) -> tuple[UKEffectWitnessAttributionRecord, ...]:
    """Attribute each compiled op back to its source effect witness.

    Read-only.  The result is deterministically ordered by
    ``(sequence, op_id, target)`` so producers diff cleanly across runs.
    """
    rows_by_id = _effect_rows_by_id(effect_rows)
    frontier_by_id = _manual_frontier_by_effect_id(effect_diagnostics)

    records: list[UKEffectWitnessAttributionRecord] = []
    for op in ops:
        effect_row_id = str(getattr(op, "group_id", "") or "")
        effect = rows_by_id.get(effect_row_id)
        frontier_row = frontier_by_id.get(effect_row_id)
        raw_witness_rule_id = str(getattr(op, "witness_rule_id", "") or "")
        witness_attributed = bool(raw_witness_rule_id)
        witness_rule_id = (
            raw_witness_rule_id
            if witness_attributed
            else UNATTRIBUTED_WITNESS_BLIND_SPOT
        )
        records.append(
            UKEffectWitnessAttributionRecord(
                op_id=str(getattr(op, "op_id", "") or ""),
                sequence=int(getattr(op, "sequence", 0) or 0),
                target=str(op.target),
                action=_action_name(op.action),
                witness_rule_id=witness_rule_id,
                witness_attributed=witness_attributed,
                action_family=_action_family_for(op, effect),
                phase_owner=_phase_owner_for(op, frontier_row),
                adjudication_bucket=_adjudication_bucket_for(frontier_row),
                source_witness=_source_witness_for(
                    op, effect, frontier_row, effect_row_id
                ),
            )
        )

    records.sort(key=lambda record: (record.sequence, record.op_id, record.target))
    return tuple(records)


def uk_effect_witness_attribution_summary(
    records: Sequence[UKEffectWitnessAttributionRecord],
) -> dict[str, Any]:
    """Return a deterministic summary of an attribution record set."""
    from collections import Counter

    witness_rule_counts: Counter[str] = Counter()
    action_family_counts: Counter[str] = Counter()
    phase_owner_counts: Counter[str] = Counter()
    adjudication_bucket_counts: Counter[str] = Counter()
    authority_layer_counts: Counter[str] = Counter()
    unattributed = 0
    missing_effect_rows = 0
    for record in records:
        witness_rule_counts[record.witness_rule_id] += 1
        action_family_counts[record.action_family] += 1
        phase_owner_counts[record.phase_owner] += 1
        adjudication_bucket_counts[record.adjudication_bucket] += 1
        authority_layer_counts[
            record.source_witness.authority_layer or "__none__"
        ] += 1
        if not record.witness_attributed:
            unattributed += 1
        if not record.source_witness.effect_row_present:
            missing_effect_rows += 1
    return {
        "n_records": len(records),
        "n_unattributed_witness_blind_spots": unattributed,
        "n_missing_effect_rows": missing_effect_rows,
        "witness_rule_counts": dict(sorted(witness_rule_counts.items())),
        "action_family_counts": dict(sorted(action_family_counts.items())),
        "phase_owner_counts": dict(sorted(phase_owner_counts.items())),
        "adjudication_bucket_counts": dict(sorted(adjudication_bucket_counts.items())),
        "authority_layer_counts": dict(sorted(authority_layer_counts.items())),
    }
