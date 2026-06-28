"""§2.9 production-lane guard-liveness for the UK lineage-acyclicity probe.

CONTEXT
``lawvm.core.timeline_lineage.check_lineage_acyclic`` (registry row **LS-11** /
``LINEAGE.CYCLE`` — the structural enforcement of AGENTS.md §2.8: *"provision/
node identity is intrinsic and versioned, never positional; moves, renumbers,
same-label rebirths, native-vs-migrated collisions, and repeal/reinsert
cycles must be represented by lineage/migration semantics — frontends emit
migration events, core consumes them."*) is a deterministic read-only DAG walk
over the migration edge graph. Itself a pure function on a tuple of
``MigrationEvent``. ``core/timeline_lineage`` previously had ZERO UK callers
(the §2.9 worst failure class: a check that exists, is registered, passes
review, and creates false confidence in invisible coverage).

The enabling emitter landed in commit ``edd1012d`` —
``lawvm.uk_legislation.uk_migration_events.derive_uk_migration_events``
projects the existing ``mutation_events_out`` stream (the structural
``MutationEvent`` ledger populated per-apply from
``replay_state.py:_record_*_mutation_event``) onto the ``MigrationEvent``
lineage plane. This module wires ``check_lineage_acyclic`` into the UK replay
fold-exit as an OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
detected cycle so the gap is VISIBLE without risking a bench-wide metric
shift. STRICT ENFORCEMENT (block under strict mode) stays multi-session
pending a UK ``strict_profile`` lane (mirroring D1/D5/D3-D4 probe posture).

OPT-IN ENV
``LAWVM_UK_LINEAGE_ACYCLIC_PROBE=1`` enables the probe. Default-off so
production UK bench replay output stays byte-stable; opt-in only for diagnostic
runs / smoke probes / CI liveness checks. The probe never raises — it appends
non-blocking ``uk_replay_lineage_cycle_observed`` adjudications to the supplied
sink.

WHAT IT DOES NOT PROMISE (honesty boundary, mirror of the totality/boundary/
identity-intrinsic probes):
* It does NOT block the replay — the IRStatute has already materialized by the
  time the probe runs at fold-exit. A future ``strict_profile`` UK lane can
  flip ``blocking=True`` after a policy ramp (mirrors the totality probe's
  staged-wire discipline).
* It does NOT prove full §2.8 totality — only the migration-event DAG
  acyclicity subset. Other §2.8 dimensions (identity-intrinsic, lineage
  PIT-materialisation, native-vs-migrated collisions) are owned by sibling
  audits (LS-12/LS-13, FI's ReplayProducts.migration_events assertion).
* The cycle witness carries the ordered address list returned by
  ``check_lineage_acyclic`` so a triager can trace the back-edge without
  re-running replay.

§1.12 RE-DERIVATION RISK: NONE. The probe consumes the
``MutationEvent`` stream (source-side IRStatute-derived, per the emitter's
documented no-rendered-text guarantee at ``uk_migration_events.py``) and
projects via the emitter to ``MigrationEvent`` tuples; the cycle detection
operates over ``LegalAddress`` path tuples only. No re-parsing of source
text or rendered/oracle text.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.timeline_lineage import (
    LineageAcyclicityResult,
    check_lineage_acyclic,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_migration_events import (
    derive_uk_migration_events,
)

# UK-scoped adjudication kind emitted for a LINEAGE.CYCLE shortfall. Mirrored
# after the existing ``uk_replay_*_observed`` adjudication kind vocabulary;
# names the purpose explicitly so consumers can distinguish a lineage
# acyclicity escape from a per-op mutation-boundary escape or a totality
# shortfall.
UK_LINEAGE_CYCLE_KIND = "uk_replay_lineage_cycle_observed"

# Opt-in env flag — default-off preserves byte-stable bench replay output.
_PROBE_ENV_FLAG = "LAWVM_UK_LINEAGE_ACYCLIC_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


def probe_uk_lineage_acyclic(
    mutation_events: Optional[Sequence[MutationEvent]],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> Optional[LineageAcyclicityResult]:
    """Run the lineage-acyclicity probe, appending each detected cycle as a
    non-blocking ``CompileAdjudication``.

    The probe projects the structural ``MutationEvent`` stream onto the
    ``MigrationEvent`` lineage plane via
    :func:`derive_uk_migration_events`, runs
    :func:`check_lineage_acyclic` (LS-11), and emits a
    ``uk_replay_lineage_cycle_observed`` adjudication per detected cycle —
    never a strict-mode block at v0.

    Returns the typed verdict (also appended to ``adjudications_out`` when
    supplied and cyclic). Callers without an output sink get the verdict as
    a return value, mirroring the helper-return shape Finland uses.

    Emits nothing when the migration graph is acyclic (no diagnostic noise on
    a clean replay).
    """
    if not _probe_enabled():
        return None
    if not mutation_events:
        return None
    statute_id = str(source_statute or "")
    try:
        migration_events = derive_uk_migration_events(mutation_events)
        result: LineageAcyclicityResult = check_lineage_acyclic(migration_events)
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        # A probe failure (e.g. emitter or walker drift) is itself an
        # observation, never a fail-stop — silently dropping the probe would
        # recreate the §2.9 false confidence we're fixing. The diagnostic
        # names ``probe_skip_reason`` so a triager can trace why the probe
        # did not run on a statute.
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return None
    if result.acyclic:
        return result
    # The cycle witness is the ordered LegalAddress list returned by
    # check_lineage_acyclic — render as a list-of-strings so the
    # detail payload stays JSON-safe + self-evidencing.
    cycle_witness = [_address_str(addr) for addr in result.cycle]
    adjudication = CompileAdjudication(
        kind=UK_LINEAGE_CYCLE_KIND,
        message=(
            "UK replay fold exit: the migration/lineage DAG contains a directed "
            "cycle — a back-edge into the active path. A cyclic migration ledger "
            "implies non-terminating materialisation / repeated-PIT hash drift "
            "(LS-11 contract break). Emitted observably; strict enforcement stays "
            "multi-session pending a UK strict_profile lane."
        ),
        source_statute=statute_id,
        op_id="",  # Post-fold observation; no single op produced this.
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": UK_LINEAGE_CYCLE_KIND,
            "family": "lineage",
            "reason_code": "migration_dag_cycle_observed",
            "cycle_witness": cycle_witness,
            "cycle_length": len(result.cycle),
            "migration_event_count": len(migration_events),
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": (
                "core.timeline_lineage.check_lineage_acyclic + "
                "lawvm.uk_legislation.uk_migration_events.derive_uk_migration_events"
            ),
            # The canonical FI prior-art witness is the LS-11 row at
            # ``core/invariant_spec.py`` + Finland's
            # ``MigrationLedger.record_renumber`` / ``record_move`` semantics
            # (``src/lawvm/finland/migration_ledger.py``). The probe is the
            # discipline-disclosing first step toward a strict-profile
            # enforcement lane that mirrors FI's
            # ``ReplayProducts.migration_events`` assertion.
            "witness_prior_art": "ls_11_lineage_cycle_fi_migration_ledger_assert",
        },
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return result


def _address_str(addr: LegalAddress) -> str:
    """Render a LegalAddress as a stable string for the cycle witness payload."""
    return str(addr)


def _build_probe_skip_adjudication(
    *,
    statute_id: str,
    reason: str,
) -> CompileAdjudication:
    """Diagnostic record when the probe could not run — never silent."""
    return CompileAdjudication(
        kind="uk_replay_lineage_acyclic_probe_skipped",
        message=(
            "UK lineage-acyclicity probe could not walk the migration graph. "
            "Recorded as a named diagnostic so the silence is itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_lineage_acyclic_probe_skipped",
            "family": "lineage",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "UK_LINEAGE_CYCLE_KIND",
    "probe_uk_lineage_acyclic",
]
