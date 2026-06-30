"""§2.9 production-lane guard-liveness for the Estonia per-op core audits.

CONTEXT
The jurisdiction-neutral core per-op audits
``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`` (LS-01 / D1)
and ``lawvm.core.commencement_totality_audit.assert_effect_totality`` (LS-23 /
D7) were, until this module, consumed only by the UK observation probes
(``uk_legislation/mutation_boundary_per_op_probe.py`` +
``commencement_effect_totality_probe.py``) and the Finland apply gates
(``finland/apply_resolved_op.py``). Estonia — which applies amendment ops with
accessible before/after IRNode snapshots and builds compiled timelines — had NO
production call site for either audit: the §2.9 worst failure class, a check
that exists, is registered, and creates false confidence in invisible
containment.

This module makes Estonia the FIRST non-UK/non-FI consumer of these core
producers, mirroring the UK OBSERVATION-ONLY, env-gated, default-OFF shape:

* ``probe_ee_op_mutation_boundary`` (D1) wires
  ``audit_op_mutation_boundary`` into the EE per-op apply fold
  (``estonia/grafter.py::apply_ee_ops`` at the ``pre_op_body`` / ``new_body``
  seam). It diffs the body IRNode before/after each landed op and projects any
  out-of-boundary changed path into a non-blocking ``CompileAdjudication`` on
  the EE diagnostic sink. EE op targets are rooted at the body's children (the
  EE body root is ``IRNode(kind=BODY, label=None)`` with NO wrapper step), so —
  unlike the FI replay fold which strips an ``("hcontainer", "")`` wrapper — the
  observed diff paths and declared op target surfaces already align and
  ``strip_root_prefix`` stays ``()`` (matching UK).

* ``probe_ee_commencement_effect_totality`` (D7) wires
  ``assert_effect_totality`` into the EE fold-exit timeline-compile seam
  (``estonia/replay.py`` where ``compile_timelines`` runs over ``lo_ops_out`` +
  ``result.temporal_events``). It reports any executed op that is neither
  commenced by a typed temporal event nor classified pending/unresolved.

DISCIPLINE (AGENTS.md §0-§4, mirroring UK §2.9):
* Default-OFF: both probes are gated behind their own ``LAWVM_EE_*`` env flags
  read through the core-owned ``mutation_boundary_audit_enabled`` ("1"-only)
  helper, so EE bench/replay output is byte-identical with the probes disabled
  (the default).
* Observation-only: every emission is ``blocking=False`` — the mutation has
  already landed by probe time, and EE has no ``strict_profile`` lane (matching
  UK's §2.9 rationale). A future EE strict profile can flip ``blocking=True``.
* Core producers reused VERBATIM: the probes never re-run ``verify_per_op`` or
  re-derive the verdict→finding / observation shape — they call the core audit
  and project its typed output, so the EE record cannot drift from UK / FI.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from lawvm.core.commencement_totality_audit import (
    COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
    assert_effect_totality,
)
from lawvm.core.ir import IRNode, LegalOperation
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.mutation_boundary_proof import (
    PerOpMutationBoundaryVerdict,
    audit_op_mutation_boundary,
    mutation_boundary_audit_enabled,
)
from lawvm.core.phase_result import Observation
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.temporal import TemporalEvent
from lawvm.replay_adjudication import CompileAdjudication

# Opt-in env flags — default-off preserves byte-stable EE bench replay output.
# Each names its own flag so the two audits opt in independently, exactly like
# the UK probe suite.
_MUTATION_BOUNDARY_ENV_FLAG = "LAWVM_EE_MUTATION_BOUNDARY_PER_OP"
_COMMENCEMENT_TOTALITY_ENV_FLAG = "LAWVM_EE_COMMENCEMENT_EFFECT_TOTALITY_PROBE"

# EE-scoped adjudication kinds. Mirrored after the existing ``ee_replay_*``
# adjudication vocabulary so consumers can distinguish a per-op mutation-
# boundary escape (D1) from a fold-exit commencement-totality shortfall (D7).
EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND = (
    "ee_replay_mutation_boundary_per_op_violation_observed"
)
EE_COMMENCEMENT_EFFECT_TOTALITY_KIND = (
    "ee_replay_commencement_effect_totality_observed"
)
EE_COMMENCEMENT_EFFECT_TOTALITY_SKIPPED_KIND = (
    "ee_replay_commencement_effect_totality_probe_skipped"
)


def mutation_boundary_probe_enabled() -> bool:
    """True when the per-op mutation-boundary probe (D1) should run per apply.

    Thin alias over the core-owned :func:`mutation_boundary_audit_enabled` gate
    keyed on the EE flag — one fact, read from core, default-off.
    """
    return mutation_boundary_audit_enabled(_MUTATION_BOUNDARY_ENV_FLAG)


def commencement_totality_probe_enabled() -> bool:
    """True when the commencement-effect totality probe (D7) should run.

    Same core-owned "1"-only env-gate helper, keyed on the EE D7 flag.
    """
    return mutation_boundary_audit_enabled(_COMMENCEMENT_TOTALITY_ENV_FLAG)


def probe_ee_op_mutation_boundary(
    *,
    before: Optional[IRNode],
    after: Optional[IRNode],
    op: LegalOperation,
    op_id: str,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
    declared_recovery_prefixes: Sequence[TreePath] = (),
) -> Optional[PerOpMutationBoundaryVerdict]:
    """Run the per-op mutation-boundary probe (D1), appending each
    ``out_of_boundary`` shortfall as a non-blocking ``CompileAdjudication``.

    Delegates verify+emit to the core-owned
    :func:`~lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`
    (observation mode — ``is_strict=False``), which computes the op's storage
    mutation boundary and, on an escape, emits the typed
    ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` core finding. The probe PROJECTS
    that core finding into the EE ``CompileAdjudication`` interop surface — it
    does NOT re-run the verifier or re-derive the diagnostic shape, so the EE
    record cannot drift from the core producer (or from UK / FI, which consume
    the same producer family).

    ``declared_recovery_prefixes`` carries the concrete full paths that a recovery
    lane INTENTIONALLY retargeted the write to (surfaced by the EE apply fold) —
    e.g. a section-level ``item`` text_replace resolved to the unique descendant
    ``subsection/item``. They are forwarded verbatim into the core audit's
    ``declared_recovery_prefixes`` so the landed write reads as an authorized
    within-boundary recovery, not an unexplained escape. This records the SPECIFIC
    recovered target — it is not a blanket boundary widening.

    Returns the typed verdict on an out-of-boundary escape (also projected to
    ``adjudications_out`` when supplied); returns ``None`` on a clean apply or a
    ``None`` snapshot — emitting nothing (no diagnostic noise on a clean apply).
    """
    if before is None or after is None:
        return None
    audit = audit_op_mutation_boundary(
        before,
        after,
        op,
        op_id=str(op_id or ""),
        source_statute=str(source_statute or ""),
        is_strict=False,  # EE has no strict_profile lane yet (§2.9).
        declared_recovery_prefixes=tuple(declared_recovery_prefixes),
        # EE op targets are rooted at the body's children and the EE body root is
        # an unwrapped IRNode(kind=BODY); the observed diff and declared op
        # surfaces already align, so ``strip_root_prefix`` stays () (unlike the
        # FI replay fold which strips an ("hcontainer", "") wrapper). Matches UK.
    )
    verdict = audit.verdict
    if audit.within_boundary or not audit.findings:
        return None
    core_finding = audit.findings[0]
    core_detail = dict(core_finding.detail)
    adjudication = CompileAdjudication(
        kind=EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
        message=(
            "EE replay per-op mutation boundary escaped: the op's changed tree "
            "paths are not a subset of its declared storage target boundary. "
            "Emitted observably; strict enforcement stays multi-session pending "
            "an EE strict_profile lane (§2.9 liveness, observation-only at v0)."
        ),
        source_statute=str(source_statute or ""),
        op_id=str(op_id or ""),
        blocking=core_finding.blocking,
        phase="replay",
        detail={
            "rule_id": EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
            "family": "mutation_boundary",
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the EE
            # record cannot diverge from core / UK / FI.
            "changed_paths": list(core_detail.get("changed_paths", ())),
            "out_of_boundary_paths": list(
                core_detail.get("out_of_boundary_paths", ())
            ),
            "boundary_status": core_detail.get(
                "boundary_status", verdict.boundary_status
            ),
            "core_finding_kind": core_finding.kind,
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": "core.mutation_boundary_proof.audit_op_mutation_boundary",
            "witness_prior_art": "fi_apply_resolved_op_mutation_boundary_at_op_gate",
        },
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return verdict


def probe_ee_commencement_effect_totality(
    ops: Optional[Sequence[LegalOperation]],
    temporal_events: Optional[Sequence[TemporalEvent]],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """Run the commencement-effect totality probe (D7), appending a
    non-blocking ``CompileAdjudication`` per op that is neither commenced nor
    pending/unresolved.

    Delegates to the core-owned
    :func:`~lawvm.core.commencement_totality_audit.assert_effect_totality`
    over the EE executable-ops stream (``lo_ops_out`` snapshots) and the EE
    ``temporal_events`` stream already assembled at the timeline-compile seam —
    EE supplies its OWN timeline events (no UK-style ``_uk_temporal_events_from_ops``
    re-derivation needed). Returns the typed Observations (also surfaced as
    CompileAdjudications on ``adjudications_out`` when non-empty). Emits nothing
    when every executed op is commenced or pending/unresolved.

    Fail-loud-as-no-op: an unexpected audit error is recorded as a named
    probe-skipped diagnostic (never strict, never silent) and the probe returns
    ``()`` — the silence is itself audible rather than recreating the §2.9 false
    confidence the wire is fixing.
    """
    if not ops:
        return ()
    statute_id = str(source_statute or "")
    try:
        observations = assert_effect_totality(
            ops,
            tuple(temporal_events or ()),
            source_statute=statute_id,
            pending_classifier=None,
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                _ee_commencement_totality_skip_adjudication(
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
        obs_detail = _observation_detail_to_json_safe_dict(observation.detail)
        adjudication = CompileAdjudication(
            kind=EE_COMMENCEMENT_EFFECT_TOTALITY_KIND,
            message=(
                "EE replay fold exit: an executed op is neither commenced by a "
                "typed temporal event nor classified as pending-or-unresolved — "
                "a §0 totality shortfall. The audit never synthesises a date, "
                "drops the op, or re-tags its classification; strict enforcement "
                "stays multi-session pending an EE strict_profile lane."
            ),
            source_statute=statute_id,
            op_id=str(obs_detail.get("op_id") or ""),
            blocking=False,
            phase="replay_products",
            detail={
                "rule_id": EE_COMMENCEMENT_EFFECT_TOTALITY_KIND,
                "family": "commencement_totality",
                "reason_code": "op_without_temporal_authorization_observed",
                "probe_mode": "observation_only",
                "strict_disposition": "record",
                "quirks_disposition": QuirksDisposition.RECORD,
                "witness_class": (
                    "core.commencement_totality_audit.assert_effect_totality"
                ),
                "witness_prior_art": (
                    "ls_23_commencement_effect_totality_compile_timelines_wire"
                ),
                "core_registry_finding_kind": (
                    COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION
                ),
                "audit_finding_kind": observation.kind,
                "audit_stage": observation.stage,
                "observation_detail": obs_detail,
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return observations


def _ee_commencement_totality_skip_adjudication(
    *,
    statute_id: str,
    reason: str,
) -> CompileAdjudication:
    """Non-blocking probe-skipped diagnostic for a D7 audit error.

    Records the probe failure as a named diagnostic so the silence is itself
    audible — the alternative is silently dropping the probe check, which would
    recreate the §2.9 false confidence we are fixing.
    """
    return CompileAdjudication(
        kind=EE_COMMENCEMENT_EFFECT_TOTALITY_SKIPPED_KIND,
        message=(
            "EE probe for commencement_totality could not run the audit. "
            "Recorded as a named diagnostic so the silence is itself audible."
        ),
        source_statute=str(statute_id or ""),
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": EE_COMMENCEMENT_EFFECT_TOTALITY_SKIPPED_KIND,
            "family": "commencement_totality",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


def _observation_detail_to_json_safe_dict(detail: Any) -> dict[str, Any]:
    """Convert a frozen ``Observation.detail`` mapping to a JSON-safe dict.

    Sub-mappings are recursed; non-JSON-shaped values are stringified
    defensively so the adjudication payload never fails serialisation. Mirrors
    the UK probe-base ``detail_mapping_to_json_safe_dict`` shape (kept EE-local
    rather than importing the UK harness so EE carries no UK dependency).
    """
    out: dict[str, Any] = {}
    if not detail:
        return out
    try:
        for key, value in detail.items():
            if hasattr(value, "items") and callable(getattr(value, "items", None)):
                out[str(key)] = _observation_detail_to_json_safe_dict(value)
            elif isinstance(value, (str, int, float, bool, type(None))):
                out[str(key)] = value
            elif isinstance(value, (tuple, list)):
                out[str(key)] = [
                    item
                    if isinstance(item, (str, int, float, bool, type(None)))
                    else str(item)
                    for item in value
                ]
            else:
                out[str(key)] = str(value)
    except Exception:  # noqa: BLE001 — best-effort stringification, never strict
        return {"detail_render_failed": str(detail)[:200]}
    return out


__all__ = [
    "EE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "EE_COMMENCEMENT_EFFECT_TOTALITY_KIND",
    "EE_COMMENCEMENT_EFFECT_TOTALITY_SKIPPED_KIND",
    "mutation_boundary_probe_enabled",
    "commencement_totality_probe_enabled",
    "probe_ee_op_mutation_boundary",
    "probe_ee_commencement_effect_totality",
]
