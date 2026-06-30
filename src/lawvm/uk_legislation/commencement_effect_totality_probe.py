"""§2.9 production-lane guard-liveness for the UK commencement-effect totality probe (D7 / LS-23).

Wired at commit `28234f36`. Migrated to the shared ``lawvm.uk_legislation.
probe_base`` harness per §2.6. The Observation→CompileAdjudication
conversion now uses ``detail_mapping_to_json_safe_dict`` + ``make_probe_
observed_adjudication`` from the harness; the per-finding tail
(audit_finding_kind, audit_stage, observation_detail) flows through
``extra_detail``.
"""
from __future__ import annotations

from typing import Optional, Sequence

from lawvm.core.commencement_totality_audit import (
    COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
    assert_effect_totality,
)
from lawvm.core.ir import LegalOperation
from lawvm.core.phase_result import Observation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)
from lawvm.uk_legislation.witness_builders import _uk_temporal_events_from_ops

UK_COMMENCEMENT_EFFECT_TOTALITY_KIND = (
    "uk_replay_commencement_effect_totality_observed"
)

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_COMMENCEMENT_EFFECT_TOTALITY_PROBE",
    kind=UK_COMMENCEMENT_EFFECT_TOTALITY_KIND,
    skipped_kind="uk_replay_commencement_effect_totality_probe_skipped",
    family="commencement_totality",
    audit_module_path=(
        "core.commencement_totality_audit.assert_effect_totality + "
        "lawvm.uk_legislation.witness_builders._uk_temporal_events_from_ops"
    ),
    witness_prior_art=(
        "ls_23_commencement_effect_totality_compile_timelines_wire"
    ),
    core_registry_finding_kind=COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
)


def probe_uk_commencement_effect_totality(
    ops: Optional[Sequence[LegalOperation]],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """Run the commencement-effect totality probe, appending a non-blocking
    ``CompileAdjudication`` per op that is neither commenced nor
    pending/unresolved.

    Returns the typed Observations (also surfaced as CompileAdjudications on
    ``adjudications_out`` when non-empty). Emits nothing when every executed
    op is commenced or pending/unresolved.
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return ()
    if not ops:
        return ()
    statute_id = str(source_statute or "")
    try:
        temporal_events = _uk_temporal_events_from_ops(
            ops,
            target_statute=statute_id,
        )
        observations = assert_effect_totality(
            ops,
            temporal_events,
            source_statute=statute_id,
            pending_classifier=None,
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
        return ()
    if not observations:
        return ()
    for observation in observations:
        obs_detail = detail_mapping_to_json_safe_dict(observation.detail)
        obs_detail["audit_finding_kind"] = observation.kind
        obs_detail["audit_stage"] = observation.stage
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            op_id=str(obs_detail.get("op_id") or ""),
            message=(
                "UK replay fold exit: an executed op is neither commenced by "
                "a typed temporal event nor classified as pending-or-unresolved "
                "— a §0 totality short fall. The audit never synthesises a "
                "date, drops the op, or re-tags its classification; strict "
                "enforcement stays multi-session pending a UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": (
                    "op_without_temporal_authorization_observed"
                ),
                "audit_finding_kind": observation.kind,
                "audit_stage": observation.stage,
                "observation_detail": obs_detail,
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return observations


__all__ = [
    "UK_COMMENCEMENT_EFFECT_TOTALITY_KIND",
    "probe_uk_commencement_effect_totality",
]
