"""§2.9 production-lane guard-liveness for the UK timeline-invariants probe.

CONTEXT
``lawvm.core.timeline_invariants.check_all_timeline_invariants_typed`` (the
typed version of ``check_all_timeline_invariants`` — C3 evidence wiring per
the docstring; structured violations with section attribution instead of
plain strings; 5 legacy invariant families: temporal_overlap /
temporary_overlay / expiry_chain / replay_timeline / replay_timeline_)
had ZERO UK production call sites — the §2.9 worst failure class: a check
that exists, is registered, passes review, and creates false confidence in
invisible coverage. FI's only production caller is
``src/lawvm/finland/replay_timeline_diagnostics.py:project_timeline_invariant_
findings`` (env-gated by ``LAWVM_FI_ENABLE_TIMELINE_INVARIANTS`` per memory
``uk_d1_d7_childtail_findings.md``); UK had no equivalent.

The blocker per memory was: "UK has NO ``Timelines`` production —
``apply_ops`` returns ``IRStatute`` only". Resolution (commit ``edd1012d``
session): ``derive_uk_migration_events`` now exists; the input-shape blocker
collapsed on inspection. The audit's contract is a PURE READ-ONLY pass over
``(ir_node, timelines, pit_date)`` tuples — and the ``timelines`` tuple is
itself producible via a direct ``compile_timelines`` call (which UK already
has all the inputs for: ``base_ir``, ``prepared_ops.accepted_ops``, and
``_uk_temporal_events_from_ops`` for the temporal_events stream).

This module wires the audit into the UK replay fold-exit as an
OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
``TimelineInvariantViolation`` so the gap is VISIBLE without risking a
bench-wide metric shift. STRICT ENFORCEMENT (block under strict mode) stays
multi-session pending a UK ``strict_profile`` lane (mirrors the
totality/mutation-boundary/identity-intrinsic/lineage/commencement/overlay/
observation-to-authority/unknown-attestation-policy probes).

WHY ``issue_sink=None`` (DEDUPLICATION DISCIPLINE)
``compile_timelines`` internally invokes the D7 ``assert_effect_totality``
and D8 ``iter_overlay_default_replay_authorized_false_violations`` audits
(per ``core/timeline.py:580-662``). When ``issue_sink=None``, those internal
audit findings are unwrapped to nowhere — the TimelineIssue wrapping is
discarded (per ``_record_timeline_issue``'s ``if issue_sink is not None:
append`` shape, verified at ``core/timeline.py:192-208``). The standalone
D7 (`commencement_effect_totality_probe.py`) + D8 (`overlay_authorization_
probe.py`) wires those audits at fold-exit and emit CompileAdjudications
directly. There is **zero double-emission** between the standalone D7/D8
probes and this probe — ``issue_sink=None`` deduplicates.

PARALLEL-REPLAY WALL-TIME
``compile_timelines`` re-replays the op stream ITSELF (per its docstring:
"Seeds from base ... then appends a new ProvisionVersion for each
LegalOperation"). This is O(N) in the op count — same as the
``UKReplayExecutor.apply_op`` loop that already ran. Default-off probe pays
zero cost in production; opt-in pays ~2x replay wall time per-statute
(once in UKReplayExecutor, once in compile_timelines). Acceptable for a
default-off diagnostic probe.

WHAT THIS DOES NOT PROMISE (honesty boundary, mirror of the prior probes):
* It does NOT block the replay — the IRStatute has already materialized.
* It does NOT carry ``authorizations`` — passes ``()`` (FI's canonical
  ``compile_timelines`` call deliberately omits this; defaults to ``()``).
  Future wire to UK's collected ``ExecutionAuthorization`` surface will
  forward these.
* It does NOT pass ``migration_events`` at v0 — UK has no per-op-emitter
  that threads ``mutation_events_out`` into ``compile_timelines``'s
  timeline-bridge at v0; ``compile_timelines`` does not require migration
  events at v0. A future wire could thread them via the function-as-input
  pattern if needed (Tier B PR4½).
* The empty-``Timelines`` case is v0-acceptable: ``check_all_timeline_
  invariants_typed`` iterates ``timelines.items()`` — empty Timelines yields
  zero violations (a clean no-op cycle showcasing the probe was run).

§1.12 RE-DERIVATION RISK: NONE. ``compile_timelines`` consumes read-only
``base_ir`` + ``ops`` + ``temporal_events`` tuples (``op.source.effective``
/ ``.enacted`` — never rendered/oracle text). ``check_all_timeline_
invariants_typed`` is a pure read over the resulting Timelines dict +
IRStatute structure. No re-parsing of source text.
"""
from __future__ import annotations

import os
from typing import List, Optional, Sequence

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.timeline import Timelines, compile_timelines
from lawvm.core.timeline_invariants import (
    TimelineInvariantViolation,
    check_all_timeline_invariants_typed,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.witness_builders import _uk_temporal_events_from_ops

# UK-scoped adjudication kind emitted for a timeline-invariants shortfall
# (one per TimelineInvariantViolation kind).  Mirrored after the existing
# ``uk_replay_*_observed`` adjudication kind vocabulary.
UK_TIMELINE_INVARIANTS_KIND = "uk_replay_timeline_invariants_observed"

# Opt-in env flag — default-off preserves byte-stable bench replay output
# (the ``compile_timelines`` parallel-replay cost is ~2x replay wall time).
_PROBE_ENV_FLAG = "LAWVM_UK_TIMELINE_INVARIANTS_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


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

    The probe (a) projects the executed-ops stream into a ``Timelines`` via
    ``compile_timelines`` (passing ``issue_sink=None`` so internal D7+D8
    audits are silent — the standalone probes own those emissions), then
    (b) invokes ``check_all_timeline_invariants_typed`` over the result,
    emitting one ``uk_replay_timeline_invariants_observed`` adjudication per
    violation — never a strict-mode block at v0.

    Returns the typed violations (also surfaced as CompileAdjudications on
    ``adjudications_out`` when non-empty). Emits nothing when the audit
    yields zero violations (the v0 default — empty Timelines yields zero
    violations per the audit's iter-empty-dict-no-op shape).

    When the caller cannot supply a pit_date AND the base_ir.metadata does
    not carry effective_date/enacted_date, the probe emits a
    ``uk_replay_timeline_invariants_probe_skipped`` diagnostic naming the
    ``pit_date_unavailable`` reason — never raises, never silently omits.
    Mirrors the §2.9 worst-class discipline: a check that exists but is
    unreachable from production is forbidden; here the probe runs and IS
    audible about why it could not finish.
    """
    if not _probe_enabled():
        return []
    if base_ir is None or not ops:
        return []
    statute_id = str(source_statute or base_ir.statute_id or "")
    # Per FI's pattern at replay_products.py — read base_date from
    # base_ir.metadata if pit_date wasn't supplied explicitly. The CLI's
    # ``--pit-date YYYY-MM-DD`` may NOT propagate to base_ir.metadata today
    # (verified on ukpga/1990/8 2026-06-29: base_ir.metadata carries neither
    # effective_date nor enacted_date when invoked via lawvm uk-replay);
    # surface that via the probe-skipped diagnostic.
    if not pit_date:
        metadata = base_ir.metadata or {}
        pit_date = str(
            metadata.get("effective_date")
            or metadata.get("enacted_date")
            or ""
        )
    if not pit_date:
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=(
                        "pit_date_unavailable: base_ir.metadata carries no "
                        "effective_date or enacted_date AND the caller did not "
                        "supply an explicit pit_date argument; check_all_"
                        "timeline_invariants_typed requires a non-empty "
                        "as_of to select versions. Fix: thread --pit-date "
                        "from the lawvm uk-replay CLI into base_ir.metadata."
                    ),
                )
            )
        return []
    try:
        temporal_events = _uk_temporal_events_from_ops(
            ops,
            target_statute=statute_id,
        )
        # ``issue_sink=None`` so compile_timelines' internal D7+D8 audit
        # findings are NOT recorded as TimelineIssues — the standalone
        # ``probe_uk_commencement_effect_totality`` + ``probe_uk_overlay_
        # authorization`` probes own those CompileAdjudication emissions.
        # ``authorizations=()`` mirrors FI's canonical compile_timelines
        # call shape (replay_products.py:2269 omits authorizations; defaults
        # to ()).
        # ``migration_events=()`` at v0 — see ``WHAT THIS DOES NOT PROMISE``
        # docstring above; a future wire could thread UK's MutationEvent→
        # MigrationEvent projection here.
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
                families=None,  # All 5 legacy families.
            )
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return []
    if not violations:
        return []
    for violation in violations:
        adjudication = _build_adjudication(
            statute_id=statute_id,
            violation=violation,
            pit_date=pit_date,
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return violations


def _build_adjudication(
    *,
    statute_id: str,
    violation: TimelineInvariantViolation,
    pit_date: str,
) -> CompileAdjudication:
    """Render one timeline-invariants violation as a UK CompileAdjudication."""
    violation_detail = _violation_detail_to_dict(violation.detail)
    return CompileAdjudication(
        kind=UK_TIMELINE_INVARIANTS_KIND,
        message=(
            "UK replay fold exit: a timeline invariant is breached for the "
            f"materialized IRStatute at pit_date={pit_date or '<empty>'}. "
            "Per check_all_timeline_invariants_typed (FI's C3 evidence "
            "wiring). Emitted observably; strict enforcement stays multi-"
            "session pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_TIMELINE_INVARIANTS_KIND,
            "family": "timeline_invariants",
            "reason_code": "timeline_invariant_violation_observed",
            "audit_kind": str(violation.kind),
            "section_label": str(violation.section_label),
            "address_path": str(violation.address_path),
            "audit_message": str(violation.message),
            "violation_detail": violation_detail,
            "pit_date": str(pit_date or ""),
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.timeline_invariants.check_all_timeline_invariants_typed + "
                "core.timeline.compile_timelines"
            ),
            # The canonical prior-art witness is FI's
            # project_timeline_invariant_findings at
            # finland/replay_timeline_diagnostics.py (env-gated by
            # LAWVM_FI_ENABLE_TIMELINE_INVARIANTS per memory
            # uk_d1_d7_childtail_findings.md). The probe is the discipline-
            # disclosing first step toward a UK Timelines production lane
            # that emits Timelines alongside the IRStatute output.
            "witness_prior_art": "fi_replay_timeline_diagnostics_project_timeline_invariant_findings",
        },
    )


def _violation_detail_to_dict(detail) -> dict:
    """Convert the violation's frozen Mapping[str, Any] to a JSON-safe dict
    for the adjudication detail payload (mirrors the D7/D8 helpers)."""
    out: dict = {}
    if not detail:
        return out
    try:
        for key, value in detail.items():
            if hasattr(value, "items") and callable(getattr(value, "items", None)):
                out[str(key)] = _violation_detail_to_dict(value)
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
        kind="uk_replay_timeline_invariants_probe_skipped",
        message=(
            "UK timeline-invariants probe could not run the audit. Recorded "
            "as a named diagnostic so the silence is itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_timeline_invariants_probe_skipped",
            "family": "timeline_invariants",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_TIMELINE_INVARIANTS_KIND",
    "probe_uk_timeline_invariants",
]
