"""§2.9 production-lane guard-liveness for the UK lineage-acyclicity probe.

CONTEXT
``lawvm.core.timeline_lineage.check_lineage_acyclic`` (registry row
**LS-11** / ``LINEAGE.CYCLE``) — the structural enforcement of AGENTS.md
§2.8: *"provision/node identity is intrinsic and versioned, never
positional; moves, renumbers, same-label rebirths, native-vs-migrated
collisions, and repeal/reinsert cycles must be represented by lineage/
migration semantics — frontends emit migration events, core consumes
them."* Wired at commit `08cecf64` as a v0 env-gated observation-only probe.

Migrated to the shared ``lawvm.uk_legislation.probe_base`` harness per §2.6.
The migration keeps the probe's external behaviour identical — same
UK_LINEAGE_CYCLE_KIND constant, same probe_uk_lineage_acyclic signature,
same env flag, same adjudication detail payload shape.
"""
from __future__ import annotations

from typing import Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.timeline_lineage import (
    LineageAcyclicityResult,
    check_lineage_acyclic,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)
from lawvm.uk_legislation.uk_migration_events import derive_uk_migration_events

UK_LINEAGE_CYCLE_KIND = "uk_replay_lineage_cycle_observed"

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_LINEAGE_ACYCLIC_PROBE",
    kind=UK_LINEAGE_CYCLE_KIND,
    skipped_kind="uk_replay_lineage_acyclic_probe_skipped",
    family="lineage",
    audit_module_path=(
        "core.timeline_lineage.check_lineage_acyclic + "
        "lawvm.uk_legislation.uk_migration_events.derive_uk_migration_events"
    ),
    witness_prior_art="ls_11_lineage_cycle_fi_migration_ledger_assert",
    core_registry_finding_kind="LINEAGE.CYCLE",
)


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
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
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
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return None
    if result.acyclic:
        return result
    # The cycle witness is the ordered LegalAddress list returned by
    # check_lineage_acyclic — render as a list-of-strings so the
    # detail payload stays JSON-safe + self-evidencing.
    cycle_witness = [_address_str(addr) for addr in result.cycle]
    adjudication = make_probe_observed_adjudication(
        _PROBE_SPEC,
        statute_id=statute_id,
        message=(
            "UK replay fold exit: the migration/lineage DAG contains a directed "
            "cycle — a back-edge into the active path. A cyclic migration ledger "
            "implies non-terminating materialisation / repeated-PIT hash drift "
            "(LS-11 contract break). Emitted observably; strict enforcement stays "
            "multi-session pending a UK strict_profile lane."
        ),
        extra_detail={
            "reason_code": "migration_dag_cycle_observed",
            "cycle_witness": cycle_witness,
            "cycle_length": len(result.cycle),
            "migration_event_count": len(migration_events),
        },
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return result


def _address_str(addr: LegalAddress) -> str:
    """Render a LegalAddress as a stable string for the cycle witness payload."""
    return str(addr)


__all__ = [
    "UK_LINEAGE_CYCLE_KIND",
    "probe_uk_lineage_acyclic",
]
