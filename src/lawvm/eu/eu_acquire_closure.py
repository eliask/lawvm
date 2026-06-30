"""eu_acquire_closure.py — DAG-recursion acquisition into the persistent farchive.

``eu_acquire.acquire_celex`` acquires ONE CELEX's notice + manifestation. This
module walks the dated amendment DAG (``eu_amendment_graph``) and acquires the
base act PLUS the transitive ``amended_by`` closure (each amending act's own
FMX4 bytes — the SOURCE of its ops), populating the persistent
``eu_cellar.farchive``. This is design §3.3's "eu_acquire recursion: walk the
DAG, acquire each amending act's FMX4".

It is honest about the two-lane reality observed live in Increment 0: the Cellar
SPARQL endpoint (the DAG source) is UP, but the REST content-negotiation lane
(the FMX4 byte source) intermittently returns HTTP 500
(``Unable to acquire JDBC Connection``). Each acquisition's typed
``CelexIngestRun`` records failures as first-class witnesses — never a silent
skip — so a partial closure is a recorded gap, not a fabricated success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from lawvm.eu import eu_acquire
from lawvm.eu.eu_amendment_graph import (
    AmendmentGraph,
    AmendmentGraphError,
    build_amendment_graph,
)
from lawvm.eu_lex.celex import is_well_formed_celex


@dataclass
class ClosureAcquisitionRun:
    """Provenance for one DAG-closure acquisition (base + amenders)."""

    base_celex: str
    fetched_at: datetime
    graph: Optional[AmendmentGraph] = None
    graph_error: str = ""
    base_run: Optional[eu_acquire.CelexIngestRun] = None
    amender_runs: list[eu_acquire.CelexIngestRun] = field(default_factory=list)
    acquired_celexes: list[str] = field(default_factory=list)
    skipped_non_act_amenders: list[str] = field(default_factory=list)
    failed_celexes: list[str] = field(default_factory=list)

    @property
    def total_added(self) -> int:
        runs = ([self.base_run] if self.base_run else []) + self.amender_runs
        return sum(r.added for r in runs)

    @property
    def total_failed(self) -> int:
        runs = ([self.base_run] if self.base_run else []) + self.amender_runs
        return sum(r.failed for r in runs)


def acquire_amendment_closure(
    base_celex: str,
    *,
    fetched_at: datetime,
    language: str = "eng",
    fmt: str = "fmx4",
    farchive: Any = None,
    farchive_path: Optional[str] = None,
    max_amenders: Optional[int] = None,
    _build_graph: Optional[Callable[[str], AmendmentGraph]] = None,
    _acquire_celex: Optional[Callable[..., eu_acquire.CelexIngestRun]] = None,
) -> ClosureAcquisitionRun:
    """Acquire the base act + its ``amended_by`` closure into the farchive.

    Parameters
    ----------
    base_celex:
        The pilot base CELEX, e.g. ``'32016R0679'``.
    language / fmt:
        Expression + manifestation slug. Default EN/fmx4 (the replay base — the
        amendment grammar is least ambiguous in EN, per design §2.4).
    max_amenders:
        Optional bound on how many amending acts to acquire (a sampled closure,
        recorded honestly rather than silently truncated). None = full closure.
    _build_graph / _acquire_celex:
        Test seams. ``_build_graph(base_celex) -> AmendmentGraph`` and
        ``_acquire_celex(celex, **kw) -> CelexIngestRun``. When None, the live
        SPARQL graph build + Cellar acquisition are used.
    """
    if not is_well_formed_celex(base_celex):
        raise ValueError(f"not a well-formed base CELEX: {base_celex!r}")

    run = ClosureAcquisitionRun(base_celex=base_celex, fetched_at=fetched_at)
    build = _build_graph or (lambda c: build_amendment_graph(c))
    acquire = _acquire_celex or eu_acquire.acquire_celex

    def _do_acquire(celex: str) -> eu_acquire.CelexIngestRun:
        return acquire(
            celex,
            fetched_at=fetched_at,
            language=language,
            fmt=fmt,
            farchive=farchive,
            farchive_path=farchive_path,
        )

    # --- 1. Acquire the base act's own bytes -------------------------------
    run.base_run = _do_acquire(base_celex)
    if run.base_run.added:
        run.acquired_celexes.append(base_celex)
    if run.base_run.failed:
        run.failed_celexes.append(base_celex)

    # --- 2. Build the dated DAG (fail loud on a non-results body) -----------
    try:
        run.graph = build(base_celex)
    except (AmendmentGraphError, OSError) as exc:
        run.graph_error = f"{type(exc).__name__}: {exc}"
        return run  # base acquired; closure recorded as a gap, not fabricated

    # --- 3. Acquire each amending act's own FMX4 bytes ---------------------
    # Partition FIRST, then bound: ``max_amenders`` caps the ACT amenders we
    # fetch, so a closure dominated by corrigenda (...R(NN), not acquirable
    # through the strict act gate) does not silently consume the whole budget.
    act_amenders: list[str] = []
    for celex in run.graph.amenders():
        if is_well_formed_celex(celex):
            act_amenders.append(celex)
        else:
            # Corrigenda are not well-formed ACT CELEXes; record honestly.
            run.skipped_non_act_amenders.append(celex)
    if max_amenders is not None:
        act_amenders = act_amenders[:max_amenders]
    for celex in act_amenders:
        amender_run = _do_acquire(celex)
        run.amender_runs.append(amender_run)
        if amender_run.added:
            run.acquired_celexes.append(celex)
        if amender_run.failed:
            run.failed_celexes.append(celex)

    return run
