"""§2.9 production-lane guard-liveness for the UK observation-promoted-to-authority probe (D11).

CONTEXT
``lawvm.core.execution_authorization.authority_source_set_observation_audit``
(registry row **D11** / ``EVID.OBSERVATION_PROMOTED_TO_AUTHORITY`` —
AGENTS.md §2.10: *evidence explains authority; it does not become authority
by existing*. Any observation-role finding kind appearing in the apply-path
authority source set breaches the evidence→authority firewall. This audit
surfaces each such kind as a typed
:class:`~lawvm.core.execution_authorization.ObservationPromotedToAuthority`
carrier; the success witness is an empty tuple) had ZERO UK production call
sites (the §2.9 worst failure class: a check that exists, is registered,
passes review, and creates false confidence in invisible coverage).

The D11 audit at the D11 spec's docstring is a pure read over the
apply-path authority-source-kinds set: any observation-role finding kind
appearing in that set breaches the firewall; the audit emits one
``ObservationPromotedToAuthority`` carrier per breach. The wire consumer
(FI's ``apply_replay_authorization._apply_authority_relevant_findings:175``
per memory ``uk_d1_d7_childtail_findings.md``) raises
``ObservationPromotedToAuthorityError`` already; UK has no equivalent
apply-path filter today, so the audit cannot fire from production today.
Per audit_impl_D11 spec intent: *"today this is a forward-compatible no-op
audit call ... the hook makes the firewall explicit"*.

This module wires the audit at the UK replay fold-exit as a v0
OBSERVATION-ONLY, env-gated probe — emitting a
:class:`~lawvm.replay_adjudication.CompileAdjudication` per
``ObservationPromotedToAuthority`` carrier the audit returns. Because UK
production has no collected authority-source-kinds set today, the probe
runs the audit with ``authority_source_kinds=()`` and emits nothing (clean
no-op cycle showcasing the audit was run; no firewall breach). As UK grows
an authority-source-kinds collected surface (a future wire), the probe will
receive those kinds so the audit can fire on
observation-role-kinds-in-authority breaches.

§2.9 GUARD-LIVENESS DISCIPLINE: the v0 emit-nothing case is the textbook
§2.9 worst failure class (a check that exists but is never actually
exercised). The §2.9 fire-drill at
:func:`tests.test_uk_observation_promoted_to_authority_probe.
test_probe_fires_on_known_violating_authority_source_kinds_input` drives
a known-violating input directly through the probe (an
observation-role-registered kind passed in the authority-source-kinds set)
and asserts the corresponding
``uk_replay_observation_promoted_to_authority_observed`` adjudication
fires. This is the "guard-liveness" §2.9 rule: proves the wire is
reachable from production, complementing the runtime empty-input tests.

OPT-IN ENV
``LAWVM_UK_OBSERVATION_PROMOTED_TO_AUTHORITY_PROBE=1`` enables the probe.
Default-off so production UK bench replay output stays byte-stable; opt-in
only for diagnostic runs. The probe never raises — it appends non-blocking
``uk_replay_*_observed`` adjudications to the supplied sink.

WHAT THIS DOES NOT PROMISE (honesty boundary):
* ``authority_source_kinds=()`` at v0 means the audit always emits nothing
  in production today. The probe is a FORWARD-COMPATIBLE NO-OP audit call
  (per audit_impl_D11 spec intent) — it makes the firewall hook explicit,
  surfaces the requirement that UK grow an authority-source-kinds collected
  set, and exercises the audit's pathway so the audit cannot become dead
  code if a future wire forgets to populate it.
* It does NOT block replay — observation-only (per AGENTS.md §0 over-
  retention-safe direction). Strict enforcement stays multi-session pending
  a UK strict_profile lane (mirrors the totality/mutation-boundary/
  identity-intrinsic/lineage/commencement/overlay probes).
* It does NOT raise on unregistered authority-source-kinds — that case is
  the downstream unregistered-code guard's territory (per audit_impl_D11 §9
  risk + the audit's docstring); here the audit returns no promotion for
  unregistered kinds silently (the audit itself is not fail-loud, by spec).

§1.12 RE-DERIVATION RISK: NONE. The audit is a pure read over a set of
finding-kind strings registered in ``observation_registry.FINDING_REGISTRY``
(via ``get_finding_spec``). No re-parsing of source text or rendered/oracle
text.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from lawvm.core.execution_authorization import (
    ObservationPromotedToAuthority,
    authority_source_set_observation_audit,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# UK-scoped adjudication kind emitted for an EVID.OBSERVATION_PROMOTED_TO_
# AUTHORITY shortfall. Mirrored after the existing ``uk_replay_*_observed``
# adjudication kind vocabulary; the underlying audit registry row (D11) is
# preserved as ``core_registry_finding_code`` in the detail payload.
UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND = (
    "uk_replay_observation_promoted_to_authority_observed"
)

# Opt-in env flag — default-off preserves byte-stable bench replay output.
_PROBE_ENV_FLAG = "LAWVM_UK_OBSERVATION_PROMOTED_TO_AUTHORITY_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


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
    if not _probe_enabled():
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
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    op_id=op_id_str,
                    reason=(
                        f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return ()
    if not promotions:
        return ()
    for promotion in promotions:
        adjudication = _build_adjudication(
            statute_id=statute_id,
            op_id=op_id_str,
            promotion=promotion,
            authority_source_kinds=tuple(kinds),
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return promotions


def _build_adjudication(
    *,
    statute_id: str,
    op_id: str,
    promotion: ObservationPromotedToAuthority,
    authority_source_kinds: tuple[str, ...],
) -> CompileAdjudication:
    """Render one audit promotion carrier as a UK CompileAdjudication."""
    return CompileAdjudication(
        kind=UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND,
        message=(
            "UK replay fold exit: an observation-role finding kind appears "
            "in the apply-path authority-source-kinds set — the §2.10 "
            "evidence→authority firewall is breached (evidence may explain "
            "authority, never become authority). Emitted observably; strict "
            "enforcement stays multi-session pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id=str(op_id or promotion.op_id or ""),
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND,
            "family": "observation_promoted_to_authority",
            "reason_code": "observation_role_kind_in_authority_set_observed",
            "promoted_kind": str(promotion.promoted_kind),
            "owner_phase": str(promotion.owner_phase),
            "authority_source_kinds": list(authority_source_kinds),
            "audit_rule_id": "authority_source_set_observation_audit",
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.execution_authorization.authority_source_set_observation_audit"
            ),
            # The canonical prior-art witness is the D11 registry row +
            # FI's apply_replay_authorization._apply_authority_relevant_
            # findings firewall (per memory uk_d1_d7_childtail_findings.md
            # — FI raises ObservationPromotedToAuthorityError). The probe
            # is the discipline-disclosing first step toward a UK
            # apply-path authority filter that mirrors FI's EV-04 firewall.
            "witness_prior_art": "d11_evid_observation_promoted_to_authority_fi_ev04_firewall",
        },
    )


def _build_probe_skip_adjudication(
    *,
    statute_id: str,
    op_id: str,
    reason: str,
) -> CompileAdjudication:
    """Diagnostic record when the probe could not run — never silent."""
    return CompileAdjudication(
        kind="uk_replay_observation_promoted_to_authority_probe_skipped",
        message=(
            "UK observation-promoted-to-authority probe could not run the "
            "audit. Recorded as a named diagnostic so the silence is itself "
            "audible."
        ),
        source_statute=statute_id,
        op_id=str(op_id or ""),
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_observation_promoted_to_authority_probe_skipped",
            "family": "observation_promoted_to_authority",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_OBSERVATION_PROMOTED_TO_AUTHORITY_KIND",
    "probe_uk_observation_promoted_to_authority",
]
