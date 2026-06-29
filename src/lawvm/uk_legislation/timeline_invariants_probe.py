"""§2.9 production-lane guard-liveness for the UK timeline-invariants probe (Tier B PR5).

Wired at commit `74d7c552`; pit_date-unavailable skip added at `d36a47f2`;
pit_date CLI threading fixed at `7deef9da`. Migrated to the shared
``lawvm.uk_legislation.probe_base`` harness per §2.6.

The TimelineInvariantViolation→CompileAdjudication conversion now uses
``detail_mapping_to_json_safe_dict`` + ``make_probe_observed_adjudication``
from the harness. The pit_date-unavailable and general-exception skip cases
both flow through ``make_probe_skip_adjudication`` with their custom
reason text in ``shortfall_probe_skip_reason``.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.timeline import Timelines, compile_timelines
from lawvm.core.timeline_invariants import (
    TimelineInvariantViolation,
    check_all_timeline_invariants_typed,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)
from lawvm.uk_legislation.witness_builders import _uk_temporal_events_from_ops

UK_TIMELINE_INVARIANTS_KIND = "uk_replay_timeline_invariants_observed"

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_TIMELINE_INVARIANTS_PROBE",
    kind=UK_TIMELINE_INVARIANTS_KIND,
    skipped_kind="uk_replay_timeline_invariants_probe_skipped",
    family="timeline_invariants",
    audit_module_path=(
        "core.timeline_invariants.check_all_timeline_invariants_typed + "
        "core.timeline.compile_timelines"
    ),
    witness_prior_art=(
        "fi_replay_timeline_diagnostics_project_timeline_invariant_findings"
    ),
    # No single core_registry_finding_kind — the audit emits a family of
    # invariant-kind codes (temporal_overlap, temporary_overlay,
    # expiry_chain, replay_timeline, replay_timeline_robust); each
    # violation carries its own ``kind`` field exposed as ``audit_kind``.
    core_registry_finding_kind="",
)


def probe_uk_timeline_invariants(
    base_ir: Optional[IRStatute],
    ops: Optional[Sequence[LegalOperation]],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
    pit_date: str = "",
) -> List[TimelineInvariantViolation]:
    """Run the timeline-invariants probe, appending a non-blocking
    ``CompileAdjudication`` per ``TimelineInvariantViolation``.

    See the module docstring for the composition details (compile_timelines
    issue_sink=None deduplication + pit_date resolution + pit_date_
    unavailable skip) — these are preserved unchanged from the pre-migration
    ``74d7c552`` / ``d36a47f2`` / ``7deef9da`` implementations.
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return []
    if base_ir is None or not ops:
        return []
    statute_id = str(source_statute or base_ir.statute_id or "")
    if not pit_date:
        metadata = base_ir.metadata or {}
        pit_date = str(
            metadata.get("pit_date")
            or metadata.get("effective_date")
            or metadata.get("enacted_date")
            or ""
        )
    if not pit_date:
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    reason=(
                        "pit_date_unavailable: base_ir.metadata carries no "
                        "pit_date / effective_date / enacted_date AND the "
                        "caller did not supply an explicit pit_date argument; "
                        "check_all_timeline_invariants_typed requires a "
                        "non-empty as_of to select versions. Fix: invoke "
                        "lawvm uk-replay with --pit-date YYYY-MM-DD."
                    ),
                )
            )
        return []
    try:
        temporal_events = _uk_temporal_events_from_ops(
            ops,
            target_statute=statute_id,
        )
        timelines: Timelines = compile_timelines(
            base_ir,
            list(ops),
            temporal_events=temporal_events,
            issue_sink=None,
            authorizations=(),
        )
        violations: List[TimelineInvariantViolation] = (
            check_all_timeline_invariants_typed(
                base_ir,
                timelines,
                pit_date,
                families=None,
            )
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return []
    if not violations:
        return []
    for violation in violations:
        violation_detail = detail_mapping_to_json_safe_dict(violation.detail)
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            message=(
                "UK replay fold exit: a timeline invariant is breached for "
                f"the materialized IRStatute at pit_date={pit_date or '<empty>'}. "
                "Per check_all_timeline_invariants_typed (FI's C3 evidence "
                "wiring). Emitted observably; strict enforcement stays multi-"
                "session pending a UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": "timeline_invariant_violation_observed",
                "audit_kind": str(violation.kind),
                "section_label": str(violation.section_label),
                "address_path": str(violation.address_path),
                "audit_message": str(violation.message),
                "violation_detail": violation_detail,
                "pit_date": str(pit_date or ""),
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return violations


__all__ = [
    "UK_TIMELINE_INVARIANTS_KIND",
    "probe_uk_timeline_invariants",
]
