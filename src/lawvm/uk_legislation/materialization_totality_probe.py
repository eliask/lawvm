"""§2.9 production-lane guard-liveness for the UK materialization-totality probe (D5 / LS-MAT).

Wired at commit `ae4795ca`. Migrated to the shared ``lawvm.uk_legislation.
probe_base`` harness per §2.6. Note: this probe's detail payload does NOT
carry a `core_registry_finding_kind` field at v0 — the probe emits
a UK-scoped kind (`uk_replay_materialization_totality_silent_drop_observed`)
without a defined core-registered finding code. The ProbeSpec
`core_registry_finding_kind=""` matches the existing omission.
"""
from __future__ import annotations

from typing import Optional

from lawvm.core.ir import IRStatute
from lawvm.core.materialization_universe import (
    DEFAULT_UNIT_KIND,
    MaterializationTotalityResult,
    MaterializationTotalityVerdict,
    check_materialization_totality,
    universe_from_tree,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)

UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND = (
    "uk_replay_materialization_totality_silent_drop_observed"
)

_UK_UNIVERSE_DOMAIN = "uk.materialization_universe.section.v0"

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_MATERIALIZE_TOTALITY_PROBE",
    kind=UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND,
    skipped_kind="uk_replay_materialization_totality_probe_skipped",
    family="totality",
    audit_module_path=(
        "core.materialization_universe.check_materialization_totality"
    ),
    witness_prior_art="materialization_totality_1929_234_content_none_masking",
    core_registry_finding_kind="",
)


def probe_uk_materialization_totality(
    base: Optional[IRStatute],
    replayed: Optional[IRStatute],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> list[CompileAdjudication]:
    """Run the per-unit materialization-totality probe, appending each
    SILENTLY_DROPPED short fall as a non-blocking ``CompileAdjudication``.
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return []
    if base is None or replayed is None:
        return []
    statute_id = str(source_statute or replayed.statute_id or "")
    pit_date = str(
        (replayed.metadata or {}).get("materialize_as_of")
        or (replayed.metadata or {}).get("as_of")
        or ""
    )
    try:
        universe = universe_from_tree(
            base.body,
            work_id=statute_id,
            pit_date=pit_date,
            unit_kind=DEFAULT_UNIT_KIND,
            domain=_UK_UNIVERSE_DOMAIN,
        )
        result: MaterializationTotalityResult = check_materialization_totality(
            universe, replayed.body
        )
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
    if result.verdict is not MaterializationTotalityVerdict.INCOMPLETE:
        return []
    emitted: list[CompileAdjudication] = []
    for shortfall in result.shortfalls:
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            message=(
                "UK replay fold exit: an expected section unit in the declared "
                "materialization universe is not present, is not a typed-absence "
                "(tombstone/caller-reason), and is not covered by a typed residual "
                "— a silent drop. Emitted observably; strict enforcement stays "
                "multi-session pending a TotalityPolicy ramp."
            ),
            extra_detail={
                "reason_code": "silently_dropped_unit_observation",
                "address_key": str(shortfall.address_key or ""),
                "unit_kind": DEFAULT_UNIT_KIND,
                "universe_domain": _UK_UNIVERSE_DOMAIN,
            },
        )
        emitted.append(adjudication)
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return emitted


__all__ = [
    "UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND",
    "probe_uk_materialization_totality",
]
