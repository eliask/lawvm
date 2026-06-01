"""Diagnostic witnesses for UK prospective-effect commencement resolution."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from lawvm.uk_legislation.affecting_act_commencement import (
    affecting_provision_in_force,
    affecting_provision_start_dates,
    get_affecting_act_xml,
)
from lawvm.uk_legislation.effects import UKEffectRecord, load_effects_for_statute_from_archive
from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND


@dataclass(frozen=True)
class UKProspectiveCommencementWitness:
    """One prospective-only effect with affecting-provision commencement evidence."""

    statute_id: str
    effect_id: str
    effect_type: str
    affected_provisions: str
    affecting_act_id: str
    affecting_provisions: str
    status: str
    rule_id: str
    start_dates: tuple[str, ...] = ()
    as_of: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "affected_provisions": self.affected_provisions,
            "affecting_act_id": self.affecting_act_id,
            "affecting_provisions": self.affecting_provisions,
            "status": self.status,
            "rule_id": self.rule_id,
        }
        if self.start_dates:
            row["start_dates"] = self.start_dates
        if self.as_of:
            row["as_of"] = self.as_of
        row.update(self.detail)
        row.setdefault("owner_phase", UK_PHASE_EFFECT_METADATA_FRONTEND)
        return row


def prospective_commencement_witness_for_effect(
    statute_id: str,
    effect: UKEffectRecord,
    *,
    archive: Any,
    as_of: str,
) -> UKProspectiveCommencementWitness | None:
    """Return a commencement-resolution witness for one prospective structural effect."""
    if not effect.is_prospective_only:
        return None
    if not effect.is_structural:
        return None
    affecting_act_xml = get_affecting_act_xml(effect.affecting_act_id, archive)
    start_dates = tuple(
        affecting_provision_start_dates(effect.affecting_provisions, affecting_act_xml)
    )
    in_force = affecting_provision_in_force(
        effect.affecting_provisions,
        affecting_act_xml,
        as_of=as_of,
    )
    if in_force is True:
        status = "resolved_in_force"
        rule_id = "uk_prospective_effect_affecting_provision_in_force"
    elif in_force is False:
        status = "resolved_future"
        rule_id = "uk_prospective_effect_affecting_provision_future"
    else:
        status = "unresolved"
        rule_id = "uk_prospective_effect_affecting_provision_unresolved"
    return UKProspectiveCommencementWitness(
        statute_id=statute_id,
        effect_id=str(effect.effect_id or ""),
        effect_type=effect.effect_type,
        affected_provisions=effect.affected_provisions,
        affecting_act_id=effect.affecting_act_id,
        affecting_provisions=effect.affecting_provisions,
        status=status,
        rule_id=rule_id,
        start_dates=start_dates,
        as_of=as_of,
        detail={
            "affecting_act_xml_available": bool(affecting_act_xml),
            "in_force_dates": tuple(effect.in_force_dates or ()),
        },
    )


def scan_prospective_commencement_witnesses_for_statute(
    statute_id: str,
    archive: Any,
    *,
    as_of: str,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKProspectiveCommencementWitness, ...]:
    effect_parse_diagnostics: list[dict[str, Any]] = []
    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=effect_parse_diagnostics,
    )
    if diagnostics_out is not None:
        diagnostics_out.extend(effect_parse_diagnostics)
    witnesses: list[UKProspectiveCommencementWitness] = []
    for effect in effects:
        witness = prospective_commencement_witness_for_effect(
            statute_id,
            effect,
            archive=archive,
            as_of=as_of,
        )
        if witness is not None:
            witnesses.append(witness)
    return tuple(witnesses)


def scan_prospective_commencement_witnesses(
    statute_ids: Iterable[str],
    archive: Any,
    *,
    as_of: str,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> tuple[UKProspectiveCommencementWitness, ...]:
    witnesses: list[UKProspectiveCommencementWitness] = []
    for statute_id in statute_ids:
        witnesses.extend(
            scan_prospective_commencement_witnesses_for_statute(
                statute_id,
                archive,
                as_of=as_of,
                diagnostics_out=diagnostics_out,
            )
        )
    return tuple(witnesses)


def prospective_commencement_status_counts(
    witnesses: Iterable[UKProspectiveCommencementWitness],
) -> dict[str, int]:
    return dict(Counter(witness.status for witness in witnesses))
