"""§2.9 production-lane guard-liveness for the UK identity-intrinsic probe.

CONTEXT
``lawvm.core.identity_intrinsic_audit`` (registry rows **LS-12**
``APPLY.POSITIONAL_ID_LEAK`` and **LS-13** ``APPLY.SYNTHETIC_LABEL_LEAK`` —
the structural enforcement of AGENTS.md §2.8 / §2.9 test-6: *"Provision/node
identity is intrinsic and versioned, never positional"*; *"synthetic markers
never reach user output, persisted artifacts, ``LegalAddress``, or
``ProvisionTimeline``"*) is a deterministic read-only sweep over the dossier's
stored surfaces. It is implemented as a test-gate-facing API and stays
conflict-free with the central registries ``observation_registry.py``.

Finland does not invoke these sweeps from production either — they live in
core with zero call sites in ``src/lawvm/finland/`` and ``src/lawvm/uk_
legislation/``. The §2.9 worst failure class: a check that exists, is
registered, passes review, and creates false confidence in invisible coverage.

This module wires the sweep into the **UK replay fold exit** as an
OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
``APPLY.POSITIONAL_ID_LEAK`` / ``APPLY.SYNTHETIC_LABEL_LEAK`` short fall so the
gap is VISIBLE without risking a bench-wide metric shift. STRICT ENFORCEMENT
(block under strict mode) stays multi-session pending a UK ``strict_profile``
lane (the UK replay fold has no ``strict_profile`` signaling path today — see
``uk_assumptions.py``): the probe is the discipline-disclosing first step,
never the strict verdict.

OPT-IN ENV
``LAWVM_UK_IDENTITY_INTRINSIC_PROBE=1`` enables the probe. Default-off so
production UK bench replay output is byte-stable; opt-in only for diagnostic
runs / smoke probes / CI liveness checks. The probe never raises — it appends
non-blocking ``uk_replay_*_observed`` adjudications to the supplied sink.

WHAT IT DOES NOT PROMISE (honesty boundary, mirror of the totality/boundary
probes):
* It does NOT block the replay — the IRStatute has already materialized by the
  time the probe runs at fold-exit. A future ``strict_profile`` UK lane can
  flip ``blocking=True`` after a TotalityPolicy-style ramp.
* It does NOT prove full §2.8 totality over every identity-bearing surface —
  only the IRStatute tree is swept at v0. :class:`LegalAddress` path tuples,
  :class:`ProvisionTimeline` keys, edge payloads, and projection rows have no
  UK production-emitter today (per ``uk_d1_d7_childtail_findings`` memory:
  UK has NO ``Timelines`` production — ``apply_ops`` returns ``IRStatute``
  only). Those surfaces become sweepable when UK grows its own timeline
  production.
* It does NOT modify the IR tree — read-only sweep over the materialized
  statute. The ``attrs.source_rule_id`` exemption is honoured: a synthesized
  rule id under that ONE key is the sanctioned home (§2.9); anywhere else it
  is a leak.
"""
from __future__ import annotations

import os
from typing import Optional

from lawvm.core.ir import IRStatute
from lawvm.core.identity_intrinsic_audit import (
    IdentityAuditReport,
    sweep_identity_intrinsic,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# Opt-in env flag — default-off preserves byte-stable bench replay output.
# Turn it on locally to surface the on-deck dormant positional-id /
# synthetic-label leak class.
_PROBE_ENV_FLAG = "LAWVM_UK_IDENTITY_INTRINSIC_PROBE"

# UK-scoped adjudication kinds emitted for an APPLY.POSITIONAL_ID_LEAK /
# APPLY.SYNTHETIC_LABEL_LEAK shortfall. Mirrored after the existing
# ``uk_replay_*_observed`` adjudication kind vocabulary; names the purpose
# explicitly so consumers can distinguish an identity-intrinsic leak from a
# per-op mutation-boundary escape or a totality shortfall.
UK_POSITIONAL_ID_LEAK_KIND = "uk_replay_positional_id_leak_observed"
UK_SYNTHETIC_LABEL_LEAK_KIND = "uk_replay_synthetic_label_leak_observed"

# Indexed by the audit's ``finding_kind`` so the dispatch from a single
# IdentityAuditReport to the right UK adjudication kind stays central.
_FINDING_KIND_TO_UK = {
    "APPLY.POSITIONAL_ID_LEAK": UK_POSITIONAL_ID_LEAK_KIND,
    "APPLY.SYNTHETIC_LABEL_LEAK": UK_SYNTHETIC_LABEL_LEAK_KIND,
}


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


def probe_uk_identity_intrinsic(
    replayed: Optional[IRStatute],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> list[CompileAdjudication]:
    """Run the identity-intrinsic sweep over the materialized ``replayed``
    statute, appending each leak as a non-blocking ``CompileAdjudication``.

    The probe walks ``replayed`` (an :class:`IRStatute` is a frozen dataclass,
    so the generic stored-surface walker visits ``statute_id`` / ``title`` /
    ``body`` / ``supplements`` / ``metadata`` plus every nested
    :class:`IRNode`'s ``label`` / ``kind`` / ``attrs``). Any string leaf (or
    mapping key) that matches the positional-id or synthetic-marker vocabulary
    becomes a ``uk_replay_*_observed`` adjudication on the sink list — never
    a strict-mode block at v0.

    Returns the emitted records (also appended to ``adjudications_out`` when
    supplied). Callers without an output sink get the records as a return
    value, mirroring the helper-return shape Finland uses.

    Emits nothing when the sweep is clean (no diagnostic noise on a clean
    replay).
    """
    if not _probe_enabled():
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
        # A probe failure (e.g. IR shape drift) is itself an observation, never
        # a fail-stop — the alternative is silently dropping the probe check
        # itself, which would recreate the §2.9 false confidence we're fixing.
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return []
    if report.clean:
        return []
    emitted: list[CompileAdjudication] = []
    for finding in report.findings:
        uk_kind = _FINDING_KIND_TO_UK.get(finding.finding_kind, "")
        if not uk_kind:
            # Unknown finding_kind from the core sweep — surface as a probe
            # skip so an audit registry out-of-band addition does not vanish
            # silently (§0 evidence ledger is monotone).
            if adjudications_out is not None:
                adjudications_out.append(
                    _build_probe_skip_adjudication(
                        statute_id=statute_id,
                        reason=(
                            f"sweep_emitted_unknown_finding_kind: {finding.finding_kind!r}"
                        ),
                    )
                )
            continue
        adjudication = CompileAdjudication(
            kind=uk_kind,
            message=(
                "UK replay fold exit: the materialized IRStatute carries a "
                f"{finding.finding_kind} leak — a stored-surface value violates "
                "the identity-intrinsic rule (§2.8) or the synthetic-marker "
                "no-leak rule (§2.9 test-6). Emitted observably; strict "
                "enforcement stays multi-session pending a UK strict_profile lane."
            ),
            source_statute=statute_id,
            op_id="",  # Post-fold observation; no single op produced this.
            blocking=False,
            phase="replay_products",
            detail={
                "rule_id": uk_kind,
                "family": "identity_intrinsic",
                "reason_code": "stored_surface_identity_leak_observed",
                "finding_kind": finding.finding_kind,
                "vocab": finding.vocab,
                "location": finding.location,
                "value": finding.value,
                "probe_mode": "observation_only",
                "strict_disposition": "record",
                "quirks_disposition": QuirksDisposition.RECORD,
                "witness_class": "core.identity_intrinsic_audit.sweep_identity_intrinsic",
                # The canonical witness is the registry row itself — LS-12 /
                # LS-13 at ``core/invariant_spec.py``. The probe is the
                # discipline-disclosing first step toward a strict-profile
                # enforcement lane.
                "witness_prior_art": "ls_12_ls_13_identity_intrinsic_sweep_registry",
            },
        )
        emitted.append(adjudication)
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return emitted


def _build_probe_skip_adjudication(
    *,
    statute_id: str,
    reason: str,
) -> CompileAdjudication:
    """Diagnostic record when the probe could not run — never silent."""
    return CompileAdjudication(
        kind="uk_replay_identity_intrinsic_probe_skipped",
        message=(
            "UK identity-intrinsic probe could not sweep the materialized "
            "state. Recorded as a named diagnostic so the silence is itself "
            "audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_identity_intrinsic_probe_skipped",
            "family": "identity_intrinsic",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_POSITIONAL_ID_LEAK_KIND",
    "UK_SYNTHETIC_LABEL_LEAK_KIND",
    "probe_uk_identity_intrinsic",
]
