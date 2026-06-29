"""§2.9 production-lane guard-liveness for the UK identity-intrinsic probe (D3+D4 / LS-12 + LS-13).

Wired at commit `cd3dcbd5` as a v0 env-gated observation-only probe. Migrated
to the shared ``lawvm.uk_legislation.probe_base`` harness per §2.6.

This probe is unique among the 9-probe suite: it emits TWO different
adjudication kinds (positional_id_leak AND synthetic_label_leak) depending
on the audit's finding_kind. So this migration introduces the canonical
"multi-spec per probe" pattern — TWO ProbeSpec instances at module scope,
one per emitted kind, dispatched by ``finding.finding_kind`` from the core
audit's report.

This shape reference will be reused if any future audit emits multiple
finding-kind codes from a single walk.
"""
from __future__ import annotations

from typing import Optional

from lawvm.core.ir import IRStatute
from lawvm.core.identity_intrinsic_audit import (
    IdentityAuditReport,
    sweep_identity_intrinsic,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

# Public symbols (backward-compat with fold-exit caller + tests).
UK_POSITIONAL_ID_LEAK_KIND = "uk_replay_positional_id_leak_observed"
UK_SYNTHETIC_LABEL_LEAK_KIND = "uk_replay_synthetic_label_leak_observed"

# Multi-spec: ONE ProbeSpec per emitted adjudication kind. Both share the
# env-gate; the audit's ``finding.finding_kind`` dispatches between them.
_PROBE_ENV_FLAG = "LAWVM_UK_IDENTITY_INTRINSIC_PROBE"
_PRIMARY_SPEC = ProbeSpec(
    env_flag=_PROBE_ENV_FLAG,
    kind=UK_POSITIONAL_ID_LEAK_KIND,
    skipped_kind="uk_replay_identity_intrinsic_probe_skipped",
    family="identity_intrinsic",
    audit_module_path="core.identity_intrinsic_audit.sweep_identity_intrinsic",
    witness_prior_art="ls_12_ls_13_identity_intrinsic_sweep_registry",
    core_registry_finding_kind="APPLY.POSITIONAL_ID_LEAK",
)
_SYNTHETIC_SPEC = ProbeSpec(
    env_flag=_PROBE_ENV_FLAG,
    kind=UK_SYNTHETIC_LABEL_LEAK_KIND,
    skipped_kind="uk_replay_identity_intrinsic_probe_skipped",
    family="identity_intrinsic",
    audit_module_path="core.identity_intrinsic_audit.sweep_identity_intrinsic",
    witness_prior_art="ls_12_ls_13_identity_intrinsic_sweep_registry",
    core_registry_finding_kind="APPLY.SYNTHETIC_LABEL_LEAK",
)

# Indexed by the audit's ``finding_kind`` so the dispatch from a single
# IdentityAuditReport to the right UK ProbeSpec stays central.
_FINDING_KIND_TO_SPEC: dict[str, ProbeSpec] = {
    "APPLY.POSITIONAL_ID_LEAK": _PRIMARY_SPEC,
    "APPLY.SYNTHETIC_LABEL_LEAK": _SYNTHETIC_SPEC,
}


def probe_uk_identity_intrinsic(
    replayed: Optional[IRStatute],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> list[CompileAdjudication]:
    """Run the identity-intrinsic sweep over the materialized ``replayed``
    statute, appending each leak as a non-blocking ``CompileAdjudication``.

    The probe walks ``replayed`` for positional-id and synthetic-marker
    leaks. Each finding's ``finding_kind`` dispatches to the matching
    ProbeSpec (positional_id_leak vs synthetic_label_leak). The probe
    returns the emitted CompileAdjudications (also appended to
    ``adjudications_out`` when supplied). Emits nothing on a clean sweep.
    """
    if not probe_env_enabled(_PRIMARY_SPEC.env_flag):
        return []
    if replayed is None:
        return []
    statute_id = str(source_statute or replayed.statute_id or "")
    try:
        report: IdentityAuditReport = sweep_identity_intrinsic(
            replayed,
            root_name="dossier",
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PRIMARY_SPEC,
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return []
    if report.clean:
        return []
    emitted: list[CompileAdjudication] = []
    for finding in report.findings:
        spec = _FINDING_KIND_TO_SPEC.get(finding.finding_kind)
        if spec is None:
            # Unknown finding_kind from the core sweep — surface as a probe
            # skip so an audit registry out-of-band addition does not vanish
            # silently (§0 evidence ledger is monotone).
            if adjudications_out is not None:
                adjudications_out.append(
                    make_probe_skip_adjudication(
                        _PRIMARY_SPEC,
                        statute_id=statute_id,
                        reason=(
                            f"sweep_emitted_unknown_finding_kind: "
                            f"{finding.finding_kind!r}"
                        ),
                    )
                )
            continue
        # The per-finding tail carries the audit's own evidence fields (the
        # offending value + its location + the vocab pattern that matched)
        # so a triager can answer §3.2's evidence path without re-running.
        adjudication = make_probe_observed_adjudication(
            spec,
            statute_id=statute_id,
            message=(
                "UK replay fold exit: the materialized IRStatute carries a "
                f"{finding.finding_kind} leak — a stored-surface value "
                "violates the identity-intrinsic rule (§2.8) or the "
                "synthetic-marker no-leak rule (§2.9 test-6). Emitted "
                "observably; strict enforcement stays multi-session pending "
                "a UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": "stored_surface_identity_leak_observed",
                "finding_kind": finding.finding_kind,
                "vocab": finding.vocab,
                "location": finding.location,
                "value": finding.value,
            },
        )
        emitted.append(adjudication)
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return emitted


__all__ = [
    "UK_POSITIONAL_ID_LEAK_KIND",
    "UK_SYNTHETIC_LABEL_LEAK_KIND",
    "probe_uk_identity_intrinsic",
]
