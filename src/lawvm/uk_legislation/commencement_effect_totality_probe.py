"""§2.9 production-lane guard-liveness for the UK commencement-effect totality probe.

CONTEXT
``lawvm.core.commencement_totality_audit.assert_effect_totality`` (registry
row **LS-23** / ``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` — the §0
total-accounting enforcement: every executed op MUST either (a) be commenced
at a typed temporal event, (b) be classified as pending-or-unresolved
(typed residual), or (c) surface as a typed Observation. Never silently
effective-dated without authority) is wired into ``compile_timelines`` in
core (per commit ``6a176e9c``) — but the UK ``apply_ops`` fold does NOT call
``compile_timelines`` (UK returns ``IRStatute`` only), so the audit is dead
code against UK replay.

This module wires the audit into the UK replay fold-exit as an
OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` shortfall so the gap is
VISIBLE without risking a bench-wide metric shift. STRICT ENFORCEMENT (block
under strict mode) stays multi-session pending a UK ``strict_profile`` lane
(mirrors D1/D5/D3-D4/D2 probe posture).

WHAT THIS DOES NOT PROMISE (honesty boundary):
* It does NOT build a UK ``Timelines`` production. The probe invokes
  ``assert_effect_totality`` DIRECTLY with the executed ops stream + the
  temporal events synthesised from those ops via the existing
  ``_uk_temporal_events_from_ops`` helper — no ``compile_timelines`` call.
  The audit's contract is read-only over op/temporal-event pairs; it doesn't
  need a full Timelines production to fire. This is the discipline-
  disclosing first step toward a future compile_timelines wire (Tier B PR4).
* It does NOT block the replay — the IRStatute has already materialized.
* It does NOT carry a UK-specific ``pending_classifier`` at v0 — UK has no
  datatype for "pending-or-unresolved" amendment taxonomy. The default
  ``pending_classifier=None`` is used; a future strict-profile lane can
  extend this when UK grows a pending-amendment classifier. This might
  over-report observations for ops that UK considers pending — the audit's
  compensating design: those are emitted as Observations (per the §0
  over-retention-safe direction), not as blocking findings.

§1.12 RE-DERIVATION RISK: NONE. ``assert_effect_totality`` is a pure read-only
audit over ``LegalOperation`` + ``TemporalEvent`` pairs; both derive from the
source-side op stream. The ``_uk_temporal_events_from_ops`` builder
(``src/lawvm/uk_legislation/witness_builders.py:30``) reads ``op.source.
effective`` / ``op.source.enacted`` — the source-side dates carried by the
UK source-carrier — never rendered/oracle text.

CONTINGENT-UNFIRED COMMENCE IS AUTHORITY: per the audit's docstring + the
landed ``commencement_totality_audit.py`` design, an op with a
``pending_decree`` / ``pending_condition`` activation-rule IS authority —
owned by the existing ``skipped_contingent_unresolved`` / ``TIME.TIMELINE_
EXECUTION_ISSUE`` lane (per memory ``uk_d1_d7_childtail_findings.md`` §3).
D7 must not double-fire there. The probe invokes ``assert_effect_totality``
which already handles contingent-unfired commence via the
``pending_classifier`` hook; UK's v0 default (None) is acceptable because
``_op_is_pending_or_unresolved`` has its own internal checks for
``activation_rule.kind`` per the audit's docstring.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

from lawvm.core.commencement_totality_audit import (
    COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
    assert_effect_totality,
)
from lawvm.core.ir import LegalOperation
from lawvm.core.phase_result import Observation
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.witness_builders import _uk_temporal_events_from_ops

# UK-scoped adjudication kind emitted for a COMMENCEMENT.OP_WITHOUT_TEMPORAL_
# AUTHORIZATION shortfall. Mirrored after the existing ``uk_replay_*_
# observed`` adjudication kind vocabulary; the underlying audit registry row
# (LS-23) wires its own Observation emission into compile_timelines.
UK_COMMENCEMENT_EFFECT_TOTALITY_KIND = (
    "uk_replay_commencement_effect_totality_observed"
)

# Opt-in env flag — default-off preserves byte-stable bench replay output.
_PROBE_ENV_FLAG = "LAWVM_UK_COMMENCEMENT_EFFECT_TOTALITY_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


def probe_uk_commencement_effect_totality(
    ops: Optional[Sequence[LegalOperation]],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """Run the commencement-effect totality probe, appending a non-blocking
    ``CompileAdjudication`` per op that is neither commenced nor
    pending/unresolved.

    The probe projects the executed ops stream into a temporal-event stream
    via the existing ``_uk_temporal_events_from_ops`` helper
    (read from ``op.source.effective`` / ``.enacted`` — never
    oracle/rendered text), then runs ``assert_effect_totality`` and emits one
    ``uk_replay_commencement_effect_totality_observed`` adjudication per
    Observation — never a strict-mode block at v0.

    Returns the typed Observations (also appended to ``adjudications_out``
    when supplied and non-empty). Callers without an output sink get the
    Observations as a return value, mirroring the helper-return shape the
    FI side uses.

    Emits nothing when every executed op is commenced or pending/unresolved.
    """
    if not _probe_enabled():
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
            # UK has no pending-amendment classifier at v0; the audit's
            # internal ``_op_is_pending_or_unresolved`` carries its own
            # activation-rule.kind check.
            pending_classifier=None,
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return ()
    if not observations:
        return ()
    for observation in observations:
        adjudication = _build_adjudication(
            statute_id=statute_id,
            observation=observation,
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return observations


def _build_adjudication(
    *,
    statute_id: str,
    observation: Observation,
) -> CompileAdjudication:
    """Render an audit Observation as a UK CompileAdjudication.

    The underlying Observation carries the registered finding kind
    (``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` — surfaced via the
    audit registry row LS-23) + its stage + the audit's own detail payload.
    The UK adjudication kind is the UK-scoped observation kind
    (``uk_replay_commencement_effect_totality_observed``); the original
    core-registered kind is preserved as ``audit_finding_kind`` in the detail
    payload so a multi-jurisdiction audit consumer can group by the
    universal finding code.
    """
    # Observation.detail is a frozen Mapping[str, Any] — convert to plain
    # dict so the JSON-safe CompileAdjudication.detail remains mutable
    # upstream (the runner can attach adjudication-id / matched-finding
    # rows when projecting the adjudication out to a public packet).
    obs_detail = _observation_detail_to_dict(observation.detail)
    obs_detail["audit_finding_kind"] = observation.kind
    obs_detail["audit_stage"] = observation.stage
    return CompileAdjudication(
        kind=UK_COMMENCEMENT_EFFECT_TOTALITY_KIND,
        message=(
            "UK replay fold exit: an executed op is neither commenced by a "
            "typed temporal event nor classified as pending-or-unresolved — "
            "a §0 totality short fall. The audit never synthesises a "
            "date, drops the op, or re-tags its classification; strict "
            "enforcement stays multi-session pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id=str(obs_detail.get("op_id") or ""),
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_COMMENCEMENT_EFFECT_TOTALITY_KIND,
            "family": "commencement_totality",
            "reason_code": "op_without_temporal_authorization_observed",
            "audit_finding_kind": observation.kind,
            "audit_stage": observation.stage,
            "observation_detail": obs_detail,
            "core_registry_finding_kind": COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.commencement_totality_audit.assert_effect_totality + "
                "lawvm.uk_legislation.witness_builders._uk_temporal_events_from_ops"
            ),
            # The canonical prior-art witness is the LS-23 row at
            # ``core/invariant_spec.py`` (registered there) + the FI
            # compile_timelines wire at commit 6a176e9c. The probe is the
            # discipline-disclosing first step toward a UK compile_timelines
            # wire (Tier B PR4).
            "witness_prior_art": "ls_23_commencement_effect_totality_compile_timelines_wire",
        },
    )


def _observation_detail_to_dict(detail) -> dict:  # noqa: N802
    """Convert the Observation's frozen Mapping[str, Any] to a JSON-safe
    dict for the adjudication detail payload. Values that are themselves
    Mappings are recursed; non-JSON-shaped values are stringified so the
    adjudication payload never fails serialisation."""
    out: dict = {}
    if not detail:
        return out
    try:
        for key, value in detail.items():
            # Sub-mapping — recurse. Don't use isinstance(value, Mapping)
            # inlined to keep the recursion guard tight.
            if hasattr(value, "items") and callable(getattr(value, "items", None)):
                out[str(key)] = _observation_detail_to_dict(value)
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
        kind="uk_replay_commencement_effect_totality_probe_skipped",
        message=(
            "UK commencement-effect totality probe could not run the audit "
            "over the executed ops stream. Recorded as a named diagnostic so "
            "the silence is itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_commencement_effect_totality_probe_skipped",
            "family": "commencement_totality",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_COMMENCEMENT_EFFECT_TOTALITY_KIND",
    "probe_uk_commencement_effect_totality",
]
