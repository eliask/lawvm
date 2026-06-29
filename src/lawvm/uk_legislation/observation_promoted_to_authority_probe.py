"""§2.9 production-lane guard-liveness for the UK observation-promoted-to-authority probe (D11).

CONTEXT
``lawvm.core.execution_authorization.authority_source_set_observation_audit``
(registry row **D11** / ``EVID.OBSERVATION_PROMOTED_TO_AUTHORITY`` —
AGENTS.md §2.10 firewall: evidence explains authority; it does not become
authority by existing). Wired at commit ``3ed59107`` as a v0 forward-compat
no-op audit per audit_impl_D11 spec intent: *"today this is a forward-
compatible no-op audit call ... the hook makes the firewall explicit"*.

Migrated to the shared ``lawvm.uk_legislation.probe_base`` harness per §2.6
rule-of-three. Second probe migrated (after D12 at commit ``cad2caea``).
"""
from __future__ import annotations

from typing import Iterable, Optional

from lawvm.core.execution_authorization import (
    EVID_OBSERVATION_PROMOTED_TO_AUTHORITY_FINDING_CODE,
    ObservationPromotedToAuthority,
    authority_source_set_observation_audit,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

# Public symbol kept (backward-compat with fold-exit caller + tests).
UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND = (
    "uk_replay_observation_promoted_to_authority_observed"
)

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_OBSERVATION_PROMOTED_TO_AUTHORITY_PROBE",
    kind=UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND,
    skipped_kind="uk_replay_observation_promoted_to_authority_probe_skipped",
    family="observation_promoted_to_authority",
    audit_module_path=(
        "core.execution_authorization.authority_source_set_observation_audit"
    ),
    witness_prior_art=(
        "d11_evid_observation_promoted_to_authority_fi_ev04_firewall"
    ),
    core_registry_finding_kind=EVID_OBSERVATION_PROMOTED_TO_AUTHORITY_FINDING_CODE,
)


def probe_uk_observation_promoted_to_authority(
    authority_source_kinds: Optional[Iterable[str]] = None,
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
    op_id: str = "",
) -> tuple[ObservationPromotedToAuthority, ...]:
    """Run the observation-promoted-to-authority audit, appending a
    non-blocking ``CompileAdjudication`` per promotion carrier.

    The probe consumes the apply-path authority-source-kinds set
    (``authority_source_kinds``) and runs
    :func:`authority_source_set_observation_audit` (D11) — emitting one
    ``uk_replay_observation_promoted_to_authority_observed`` adjudication
    per observation-role finding kind found in that set.

    UK has no collected authority-source-kinds surface at the fold-exit
    today, so the default ``authority_source_kinds=None`` argument is
    normalised to ``()`` — the audit runs and emits nothing. The
    fire-drill at the test bypasses production by passing a known-violating
    iterable directly.

    Returns the audit's promotion carriers (also surfaced as
    CompileAdjudications on ``adjudications_out`` when non-empty). Emits
    nothing when the audit yields no promotions (clean no-op cycle).
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return ()
    statute_id = str(source_statute or "")
    op_id_str = str(op_id or "")
    kinds = list(authority_source_kinds) if authority_source_kinds else []
    try:
        promotions = authority_source_set_observation_audit(
            kinds,
            op_id=op_id_str,
            owner_phase="replay_products",
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    op_id=op_id_str,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return ()
    if not promotions:
        return ()
    for promotion in promotions:
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            op_id=op_id_str,
            message=(
                "UK replay fold exit: an observation-role finding kind appears "
                "in the apply-path authority-source-kinds set — the §2.10 "
                "evidence→authority firewall is breached (evidence may explain "
                "authority, never become authority). Emitted observably; strict "
                "enforcement stays multi-session pending a UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": (
                    "observation_role_kind_in_authority_set_observed"
                ),
                "promoted_kind": str(promotion.promoted_kind),
                "owner_phase": str(promotion.owner_phase),
                "authority_source_kinds": list(kinds),
                "audit_rule_id": "authority_source_set_observation_audit",
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return promotions


__all__ = [
    "UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND",
    "probe_uk_observation_promoted_to_authority",
]
