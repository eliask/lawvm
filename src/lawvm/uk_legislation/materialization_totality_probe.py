"""§2.9 production-lane guard-liveness for the UK materialization-totality probe.

CONTEXT
``lawvm.core.materialization_universe`` (the "no hidden universe" lens) is a
jurisdiction-neutral partition: a declared UNIVERSE of expected section units
for a UK work at a PIT is partitioned against the materialized PIT tree so
every expected unit is PRESENT, owned by a typed absence (a repeal
placeholder / caller-declared typed absence), covered by a typed residual,
or a NAMED ``VIOLATION`` — a silent drop. The lens is registered
(``core/invariant_spec.py:620`` registers ``LS-MAT-01`` /
``LS-MAT-02`` with ``checker_ref="...
check_materialization_totality"``) but it was previously not invoked
anywhere in ``src/lawvm/uk_legislation/`` — the textbook §2.9 worst failure
class: a check that exists, is registered, passes review, and creates
false confidence in invisible coverage.

This module wires the lens into the **UK replay fold exit** as an
OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.core.compile_records.CompileAdjudication` records for every
``SILENTLY_DROPPED_UNIT`` shortfall so the gap is VISIBLE without risking a
bench-wide metric shift. STRICT ENFORCEMENT (block under strict mode) stays
multi-session pending a TotalityPolicy ``off`` / ``sampled`` / ``always``
ramp (per ``core/materialization_universe.py`` docstring and the doctrine at
``notes_internal/pro_on_invariant_mining_and_more.md`` §2.9): the probe is
the discipline-disclosing first step, never the strict verdict.

OPT-IN ENV
``LAWVM_UK_MATERIALIZE_TOTALITY_PROBE=1`` enables the probe. Default-off so
production UK bench replay output is byte-stable; opt-in only for diagnostic
runs / smoke probes / CI liveness checks. The probe never raises — it appends
non-blocking ``uk_replay_materialization_totality_silent_drop_observed``
adjudications to the supplied sink.

WHAT IT DOES NOT PROMISE (honesty boundary, mirror of FI's pattern):
* It does NOT prove strict totality — only partition totality over the
  ``section`` unit kind.
* It does NOT assert that every short fall is a replay bug — the partition
  flag is ``UNKNOWN_DROP``, the policy is ``record``. A shortfall is
  disclosed evidence the materialization lane should explain, not a
  fail-stop verdict until the totality policy decides what to do.
* It does NOT reach into scheduled / supplemental unit kinds beyond
  ``section`` at v0 — those universes remain a future claim expansion.
"""
from __future__ import annotations

import os
from typing import Optional

from lawvm.core.ir import IRStatute
from lawvm.core.materialization_universe import (
    DEFAULT_UNIT_KIND,
    MaterializationTotalityResult,
    MaterializationTotalityVerdict,
    check_materialization_totality,
    universe_from_tree,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# UK-scoped universe domain. UK's universe roots must never collide with
# another jurisdiction's section universe (e.g. FI's
# ``fi.materialization_universe.section.v0``); domain-qualifying per
# ``core/materialization_universe.py`` honesty boundary.
_UK_UNIVERSE_DOMAIN = "uk.materialization_universe.section.v0"

# The UK adjudication kind emitted for a SILENTLY_DROPPED shortfall. Mirrored
# after the existing ``uk_replay_*`` adjudication kind vocabulary; names the
# purpose explicitly so consumers can distinguish a totality shortfall from a
# per-op rejection or observation.
UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND = (
    "uk_replay_materialization_totality_silent_drop_observed"
)

# Default-off: production UK bench replay output is byte-stable. The probe is
# opt-in diagnostic discipline disclosure: turn it on locally to surface the
# on-deck dormant silent-drop class. Never auto-on.
_PROBE_ENV_FLAG = "LAWVM_UK_MATERIALIZE_TOTALITY_PROBE"


def _probe_enabled() -> bool:
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


def probe_uk_materialization_totality(
    base: Optional[IRStatute],
    replayed: Optional[IRStatute],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> list[CompileAdjudication]:
    """Run the per-unit materialization-totality probe, appending each
    SILENTLY_DROPPED short fall as a non-blocking ``CompileAdjudication``.

    The probe partitions the declared UNIVERSE of expected ``section`` units
    in ``replayed`` (held at the same source-root ide as the FI analogue:
    the base statute body) against ``replayed.body``. Any expected section
    that vanishes with no live node, no tombstone marker, and no typed
    absence reason becomes a ``uk_replay_*_silent_drop_observed``
    adjudication on the sink list — never a strict-mode block.

    Returns the emitted records (also appended to ``adjudications_out`` when
    supplied). Callers without an output sink get the records as a return
    value, mirroring the helper-return shape Finland uses.
    """
    if not _probe_enabled():
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
        # A probe failure (e.g. transposed IR shape) is itself an observation,
        # never a fail-stop — the alternative is silently dropping the probe
        # check itself, which would recreate the §2.9 false confidence we're
        # fixing. The diagnostic names ``shortfall_probe_skip_reason`` so a
        # triager can trace why the probe did not run on a statute.
        if adjudications_out is not None:
            adjudications_out.append(
                _build_probe_skip_adjudication(
                    statute_id=statute_id,
                    reason=f"probe_unexpected_error: {exc.__class__.__name__}: {exc!r}",
                )
            )
        return []
    if result.verdict is not MaterializationTotalityVerdict.INCOMPLETE:
        return []
    emitted: list[CompileAdjudication] = []
    for shortfall in result.shortfalls:
        adjudication = CompileAdjudication(
            kind=UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND,
            message=(
                "UK replay fold exit: an expected section unit in the declared "
                "materialization universe is not present, is not a typed-absence "
                "(tombstone/caller-reason), and is not covered by a typed residual — "
                "a silent drop. Emitted observably; strict enforcement stays "
                "multi-session pending a TotalityPolicy ramp."
            ),
            source_statute=statute_id,
            op_id="",  # Post-fold observation; no single op produced this.
            blocking=False,
            phase="replay_products",
            detail={
                "rule_id": UK_MATERIALIZE_TOTALITY_SILENT_DROP_KIND,
                "family": "totality",
                "reason_code": "silently_dropped_unit_observation",
                "address_key": str(shortfall.address_key or ""),
                "unit_kind": DEFAULT_UNIT_KIND,
                "universe_domain": _UK_UNIVERSE_DOMAIN,
                "probe_mode": "observation_only",
                "strict_disposition": "record",
                "quirks_disposition": QuirksDisposition.RECORD,
                "witness_class": "core.materialization_universe.check_materialization_totality",
                # The "1929/234 §110-113 rikeslaki content=None masking"
                # class — the canonical FI totality witness — is the
                # analogous UK surface this probe seeks to surface.
                "witness_prior_art": "materialization_totality_1929_234_content_none_masking",
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
        kind="uk_replay_materialization_totality_probe_skipped",
        message=(
            "UK materialization-totality probe could not partition the "
            "declared universe against the materialized state. Recorded as a "
            "named diagnostic so the silence is itself audible."
        ),
        source_statute=statute_id,
        op_id="",
        blocking=False,
        phase="replay_products",
        detail={
            "rule_id": "uk_replay_materialization_totality_probe_skipped",
            "family": "totality",
            "reason_code": "probe_skipped",
            "shortfall_probe_skip_reason": str(reason),
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )
