"""§2.9 production-lane guard-liveness for the UK overlay-authorization probe (D8).

CONTEXT
``lawvm.core.overlay_default_replay_authorized_false_audit.iter_overlay_
default_replay_authorized_false_violations`` (registry row **D8** /
``OVERLAY.UNAUTHORIZED_PROMOTION`` — AGENTS.md §2.10 deterministic
firewall). Wired at commit `728af919` as a v0 env-gated observation-only
probe with ``authorizations=()`` (mirrors FI's canonical compile_timelines
call which omits authorizations; defaults to ``()``).

Migrated to the shared ``lawvm.uk_legislation.probe_base`` harness per §2.6.
The Finding→CompileAdjudication conversion now uses
``detail_mapping_to_json_safe_dict`` + ``make_probe_observed_adjudication``
from the harness; the per-finding tail (audit_finding_kind, audit_role,
audit_stage, audit_blocking, finding_detail, audit_rule_id,
authorizations_supplied_count) flows through ``extra_detail``.
"""
from __future__ import annotations

from typing import Iterable, Iterator, Optional

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRStatute
from lawvm.core.overlay_default_replay_authorized_false_audit import (
    OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
    OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
    iter_overlay_default_replay_authorized_false_violations,
)
from lawvm.core.phase_result import Finding
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

UK_OVERLAY_AUTHORIZATION_KIND = "uk_replay_overlay_unauthorized_promotion_observed"

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_OVERLAY_AUTHORIZATION_PROBE",
    kind=UK_OVERLAY_AUTHORIZATION_KIND,
    skipped_kind="uk_replay_overlay_authorization_probe_skipped",
    family="overlay_authorization",
    audit_module_path=(
        "core.overlay_default_replay_authorized_false_audit."
        "iter_overlay_default_replay_authorized_false_violations"
    ),
    witness_prior_art="d8_overlay_authorized_false_audit_compile_timelines_wire",
    core_registry_finding_kind=OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
)


def probe_uk_overlay_authorization(
    replayed: Optional[IRStatute],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    authorizations: Iterable[ExecutionAuthorization] = (),
    source_statute: str = "",
) -> list[Finding]:
    """Run the overlay authorization probe, appending a non-blocking
    ``CompileAdjudication`` per ``OVERLAY.UNAUTHORIZED_PROMOTION`` Finding.

    The probe walks the materialized ``replayed`` IRStatute for overlay-
    tagged nodes (per the closed-set predicate at
    :data:`_OVERLAY_TAG_PREDICATES`), checks each against the supplied
    ``authorizations`` for a matching promotion, and emits one
    ``uk_replay_overlay_unauthorized_promotion_observed`` adjudication per
    unauthorised overlay-tagged node — never a strict-mode block at v0.

    ``authorizations`` defaults to ``()`` because UK has no collected
    ExecutionAuthorization surface at the fold-exit today — this v0
    omits-authorizations posture mirrors FI's ``compile_timelines`` call
    (deliberately omits authorizations; defaults to ``()``).

    Returns the Findings (also appended to ``adjudications_out`` when
    supplied and non-empty). Emits nothing when the IRStatute carries no
    overlay-tagged nodes (clean no-op cycle).
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return []
    if replayed is None:
        return []
    statute_id = str(source_statute or replayed.statute_id or "")
    try:
        findings: Iterator[Finding] = (
            iter_overlay_default_replay_authorized_false_violations(
                replayed,
                authorizations=authorizations,
            )
        )
        # Materialise once so a probe-skipped diagnostic can be emitted if
        # the audit iterator raises mid-walk. The closed-set predicate
        # discipline at audit_impl_D8 §9 suggests fail-loud on tag-vocab
        # drift — surface as a probe-skipped diagnostic, never silent.
        materialised = list(findings)
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
    if not materialised:
        return []
    for finding in materialised:
        find_detail = detail_mapping_to_json_safe_dict(finding.detail)
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            op_id=str(find_detail.get("op_id") or ""),
            message=(
                "UK replay fold exit: the materialized IRStatute contains an "
                "overlay-tagged node (per AGENTS.md §2.10) without a matching "
                "ExecutionAuthorization promotion event — the §2.10 "
                "deterministic firewall is breached. The finding is emitted "
                "observably; strict enforcement stays multi-session pending a "
                "UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": "overlay_unauthorized_promotion_observed",
                "audit_finding_kind": finding.kind,
                "audit_role": str(finding.role),
                "audit_stage": finding.stage,
                "audit_blocking": bool(finding.blocking),
                "finding_detail": find_detail,
                "audit_rule_id": (
                    OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID
                ),
                "authorizations_supplied_count": 0,  # v0 — authorizations=() (FI pattern)
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return materialised


__all__ = [
    "UK_OVERLAY_AUTHORIZATION_KIND",
    "probe_uk_overlay_authorization",
]
