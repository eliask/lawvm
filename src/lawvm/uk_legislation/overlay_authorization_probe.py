"""§2.9 production-lane guard-liveness for the UK overlay-authorization probe (D8).

CONTEXT
``lawvm.core.overlay_default_replay_authorized_false_audit.iter_overlay_
default_replay_authorized_false_violations`` (registry row **D8** /
``OVERLAY.UNAUTHORIZED_PROMOTION`` — AGENTS.md §2.10 §0: a surface/overlay
node **defaults to** ``replay_authorized=False``. A node tagged as
originating from the overlay plane may mutate legal state ONLY through a
typed ExecutionAuthorization promotion event. An overlay-tagged node that
claims ``replay_authorized=True`` without such a promotion breaches the
deterministic firewall) was wired into ``compile_timelines`` in core (per
commit ``a6c067c8``) — but the UK ``apply_ops`` fold does NOT call
``compile_timelines``, so the audit was dead code against UK replay.

This module wires the audit into the UK replay fold-exit as an
OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
``OVERLAY.UNAUTHORIZED_PROMOTION`` short fall so the gap is VISIBLE without
risking a bench-wide metric shift. STRICT ENFORCEMENT (block under strict
mode) stays multi-session pending a UK ``strict_profile`` lane (mirrors the
totality/mutation-boundary/identity-intrinsic/lineage/commencement probes).

INPUT SHAPE
The audit takes ``(statute: IRStatute, *, authorizations: Iterable[
ExecutionAuthorization])`` — both are available at the UK fold-exit today.
``replayed`` is the fold's output ``IRStatute`` (frozen). ``authorizations``
is `()` at v0 — UK has no collected ExecutionAuthorization surface today;
only `frontier_work_items.py:13` imports `validate_execution_authorization`
for per-record validation, not for collecting auth rows at fold-exit.

This v0 omits-authorizations posture mirrors FI's canonical
``compile_timelines`` call (``src/lawvm/finland/replay_products.py:2269``
— ``compile_timelines(base_ir, lo_ops, base_enacted_date=...,
label_norm=fi_label_norm, temporal_events=resolved_temporal_events)``
deliberately omits ``authorizations``; defaults to ``()``.). A node the audit
finds overlay-tagged with no matching promotion breaches the §2.10
firewall — emitted observation-only at v0.

WHAT THIS DOES NOT PROMISE (honesty boundary):
* It does NOT block the replay — the IRStatute has already materialized by
  the time the probe runs at fold-exit. A future ``strict_profile`` UK lane
  can flip ``blocking=True`` after a policy ramp (mirrors the totality
  probe's staged-wire discipline).
* It does NOT carry UK-side ExecutionAuthorization tuples — ``authorizations
  =()`` at v0. As UK grows a collected ExecutionAuthorization surface (a
  future wire), the probe will receive those rows so the matching promotions
  suppress findings for explicitly-authorized overlay nodes. Until then,
  every overlay-tagged node in the materialized IRStatute is reported as
  an explicit breach.
* It does NOT assume UK produces overlay-tagged nodes. UK's normal
  replay output may have zero overlay-tagged nodes today; in that case the
  probe yields nothing (clean no-op cycle showcasing the audit was run).

§1.12 RE-DERIVATION RISK: NONE. The audit operates over IRNode attrs
(``overlay_kind``, ``lawvm_temporal_overlay``, ``authority.authority_plane``)
— all set by the lowering/elaboration phase from source-side carriers,
never from rendered/oracle text. The ``iter_overlay_default_replay_
authorized_false_violations`` walk yields Findings; the probe converts each
to a UK CompileAdjudication (mirroring the D7 Finding/Observation →
adjudication converter, with the audit-registered finding kind preserved as
``core_registry_finding_kind`` in the detail payload).
"""
from __future__ import annotations

import os
from typing import Iterable, Iterator, Optional

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRStatute
from lawvm.core.overlay_default_replay_authorized_false_audit import (
    OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
    OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
    iter_overlay_default_replay_authorized_false_violations,
)
from lawvm.core.phase_result import Finding
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# UK-scoped adjudication kind emitted for an OVERLAY.UNAUTHORIZED_PROMOTION
# shortfall. Mirrored after the existing ``uk_replay_*_observed`` adjudication
# kind vocabulary; the underlying audit registry row (D8) is preserved as
# ``core_registry_finding_kind`` in the detail payload.
UK_OVERLAY_AUTHORIZATION_KIND = "uk_replay_overlay_unauthorized_promotion_observed"

# Opt-in env flag — default-off preserves byte-stable bench replay output.
_PROBE_ENV_FLAG = "LAWVM_UK_OVERLAY_AUTHORIZATION_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


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
    supplied and non-empty). Callers without an output sink get the Findings
    as a return value, mirroring the helper-return shape the FI side uses.

    Emits nothing when the IRStatute carries no overlay-tagged nodes (clean
    no-op cycle — the audit was run, no overlay plane violations present).
    """
    if not _probe_enabled():
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
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return []
    if not materialised:
        return []
    for finding in materialised:
        adjudication = _build_adjudication(
            statute_id=statute_id,
            finding=finding,
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return materialised


def _build_adjudication(
    *,
    statute_id: str,
    finding: Finding,
) -> CompileAdjudication:
    """Render an audit Finding as a UK CompileAdjudication.

    The underlying Finding carries the registered finding kind
    (``OVERLAY.UNAUTHORIZED_PROMOTION`` — surfaced via the audit registry row
    D8) + its role + its stage + the audit's own detail payload. The UK
    adjudication kind is the UK-scoped observation kind
    (``uk_replay_overlay_unauthorized_promotion_observed``); the original
    core-registered kind is preserved as ``audit_finding_kind`` in the detail
    payload so a multi-jurisdiction audit consumer can group by the universal
    finding code.
    """
    find_detail = _finding_detail_to_dict(finding.detail)
    return CompileAdjudication(
        kind=UK_OVERLAY_AUTHORIZATION_KIND,
        message=(
            "UK replay fold exit: the materialized IRStatute contains an "
            "overlay-tagged node (per AGENTS.md §2.10) without a matching "
            "ExecutionAuthorization promotion event — the §2.10 deterministic "
            "firewall is breached. The finding is emitted observably; strict "
            "enforcement stays multi-session pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id=str(find_detail.get("op_id") or ""),
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_OVERLAY_AUTHORIZATION_KIND,
            "family": "overlay_authorization",
            "reason_code": "overlay_unauthorized_promotion_observed",
            "audit_finding_kind": finding.kind,
            "audit_role": str(finding.role),
            "audit_stage": finding.stage,
            "audit_blocking": bool(finding.blocking),
            "finding_detail": find_detail,
            "core_registry_finding_kind": OVERLAY_UNAUTHORIZED_PROMOTION_FINDING_CODE,
            "audit_rule_id": OVERLAY_DEFAULT_REPLAY_AUTHORIZED_FALSE_AUDIT_RULE_ID,
            "probe_mode": "observation_only",
            "authorizations_supplied_count": 0,  # v0 — authorizations=() (FI pattern)
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.overlay_default_replay_authorized_false_audit."
                "iter_overlay_default_replay_authorized_false_violations"
            ),
            # The canonical prior-art witness is the D8 row at
            # ``core/invariant_spec.py`` + the FI compile_timelines wire at
            # commit a6c067c8. The probe is the discipline-disclosing first
            # step toward a UK compile_timelines wire (Tier B PR4).
            "witness_prior_art": "d8_overlay_authorized_false_audit_compile_timelines_wire",
        },
    )


def _finding_detail_to_dict(detail) -> dict:
    """Convert the Finding's frozen Mapping[str, Any] to a JSON-safe dict
    for the adjudication detail payload (mirrors the D7 helper)."""
    out: dict = {}
    if not detail:
        return out
    try:
        for key, value in detail.items():
            if hasattr(value, "items") and callable(getattr(value, "items", None)):
                out[str(key)] = _finding_detail_to_dict(value)
            elif isinstance(value, (str, int, float, bool, type(None))):
                out[str(key)] = value
            else:
                out[str(key)] = str(value)
    except Exception:  # noqa: BLE001 — best-effort stringification, never strict
        return {"detail_render_failed": str(detail)[:200]}
    return out


def _build_probe_skip_adjudication(
    *,
    statute_id: str,
    reason: str,
) -> CompileAdjudication:
    """Diagnostic record when the probe could not run — never silent."""
    return CompileAdjudication(
        kind="uk_replay_overlay_authorization_probe_skipped",
        message=(
            "UK overlay-authorization probe could not walk the materialized "
            "IRStatute. Recorded as a named diagnostic so the silence is "
            "itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_overlay_authorization_probe_skipped",
            "family": "overlay_authorization",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_OVERLAY_AUTHORIZATION_KIND",
    "probe_uk_overlay_authorization",
]
