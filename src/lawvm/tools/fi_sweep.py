"""``lawvm fi-sweep`` — the VoI-STAGED corpus reliability sweep driver.

The corpus-proof over the ~6,232 finlex PDFs (or the vision-hard subset) must NOT
be one big GPU run against an unproven assumption. It ESCALATES in tranches
(small → 4-10× → full), GATING each stage on the prior looking sane, so a bad
assumption costs MINUTES of GPU, not hours. This is the Value-of-Information
staging: only spend the next, larger tranche once the current one has cleared the
gate.

This CLI is ADDITIVE tooling — it NEVER edits the ingest pipeline. It REUSES the
three landed pieces verbatim:

  * ``fi_parse_corpus`` — corpus enumeration (``enumerate_corpus``), the per-PDF
    ThreadPool + the ``evaluate_defacsimile_ab`` A/B (``_process_one`` → a typed
    ``RowResult``), and the deterministic worst-first ranking.
  * ``fi_scan_stratum`` — the born_digital / mixed / scanned text-layer census
    (``census_scan_strata``), the source of the per-PDF stratum used to draw a
    STRATIFIED sample.
  * ``fi_calibration`` — the NUMERIC-exact scoring discipline (the primary gate is
    NUMERIC-exact, mirrored here as the stage gate's hard stop).

Design (spec §10 dec. 4 / §10.2 sequencing):

  1. **Escalating, superset-nested stages.** A single deterministic stratified
     ORDER over the selected pool is built once; stage N is the size-``S_N`` prefix
     of that order, so stage N+1 ⊇ stage N (comparability) and any prefix is
     ~stratum-proportional (each stratum is evenly spread across the order).
  2. **Per-stage VoI gate.** After a stage's A/B completes, PROCEED to the next
     (larger) tranche only if: NUMERIC-exact regressions ≤ ``numeric_tolerance``
     (default 0), the acceptance rate has not regressed vs the prior stage beyond
     ``accept_regression_tolerance``, and there were no run errors. Otherwise STOP
     and print WHY + what was DROPPED (the un-run tranche), so a bad assumption
     never spends the next, larger GPU tranche.
  3. **Resumable + deterministic.** Resume is by CONSTRUCTION — the processor
     persists each member's verdict into the OUTPUT FARCHIVE and returns any
     pre-existing one without recompute, so a re-run (after a shutdown, or the next
     batch up the ladder) re-walks completed PDFs as fast farchive lookups and only
     spends tokens on genuinely new members. No side checkpoint file to keep in
     sync. The stratified order is a pure function of the (locator, stratum) set,
     so two runs diff-empty.
  4. **Report.** Per-stage + cumulative acceptance rate, NUMERIC-exact regression
     count, EXTRA/STRUCTURE/MISSING deltas, and a RANKED residual-defect-class
     table (worst first). If a token/throughput meter is available it adds tokens +
     wall-tok/s per stage (guarded — absence is normal).

The full sweep needs the GPU and is OPERATOR-invoked; CI exercises the driver
HERMETICALLY with a fake per-PDF processor + an injected stratum map (see the
test). ``--dry-run`` plans the samples (the cheap pdfium census only) without ever
touching vision.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from lawvm.tools.fi_parse_corpus import (
    _DEFAULT_WORKERS,
    CorpusMember,
    RowResult,
    enumerate_corpus,
)
from lawvm.tools.fi_scan_stratum import (
    STRATUM_BORN_DIGITAL,
    STRATUM_MIXED,
    STRATUM_SCANNED,
    STRATUM_UNREADABLE,
    census_scan_strata,
)

_FINLEX_DEFAULT = "data/finlex.farchive"

# Default escalation ladder (spec §10.2: small → 4-10× → full). The trailing
# ``full`` expands to the whole selected pool size at plan time.
DEFAULT_STAGES = "10,50,200,1000,full"

# Acceptance rate may not fall this far below the prior (smaller) stage before the
# gate STOPS — a widening sample that suddenly accepts far fewer PDFs is exactly the
# "bad assumption" the staging is built to catch cheaply.
DEFAULT_ACCEPT_REGRESSION_TOLERANCE = 0.05

# A fixed stratum order for the deterministic interleave + report (unreadable last).
_STRATUM_ORDER: Tuple[str, ...] = (
    STRATUM_BORN_DIGITAL,
    STRATUM_MIXED,
    STRATUM_SCANNED,
    STRATUM_UNREADABLE,
)

# The residual defect classes, ranked in the report worst-first by residual count.
_DEFECT_CLASSES: Tuple[str, ...] = ("EXTRA", "STRUCTURE", "MISSING", "NUMERIC")


class TokenMeter(Protocol):
    """A best-effort cumulative token/wall meter on the vision client (OPTIONAL).

    ``snapshot`` returns ``(cumulative_output_tokens, cumulative_wall_seconds)``.
    The driver diffs two snapshots around each stage; any exception or a missing
    meter degrades to "no meter" (the token columns are simply omitted).
    """

    def snapshot(self) -> Tuple[int, float]:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- #
# Stage ladder + stratified, superset-nested ordering.                          #
# --------------------------------------------------------------------------- #


def resolve_stages(spec: str, total: int) -> List[int]:
    """Parse a ``--stages`` spec into an ascending list of cumulative sizes.

    ``full`` expands to ``total``; numeric sizes are clamped to ``[1, total]``.
    The result is strictly increasing (duplicates and out-of-order shrinks folded
    away) so the stages are genuinely escalating and superset-nested. An empty /
    all-clamped spec collapses to a single ``[total]`` full stage.
    """
    out: List[int] = []
    for raw in spec.split(","):
        tok = raw.strip()
        if not tok:
            continue
        size = total if tok.lower() == "full" else int(tok)
        size = min(max(1, size), max(1, total))
        if not out or size > out[-1]:
            out.append(size)
    if not out:
        out.append(max(1, total))
    return out


def stratified_order(
    members: Sequence[CorpusMember], stratum_of: Callable[[str], str]
) -> List[CorpusMember]:
    """A deterministic order whose every PREFIX is ~stratum-proportional.

    Each stratum's members are sorted by locator, then given a fractional position
    ``(idx + 0.5) / n`` within the stratum; sorting ALL members by that position
    (ties broken by stratum then locator) evenly interleaves the strata, so a
    size-N prefix draws ~proportionally from every stratum. Prefixes are nested by
    construction, giving the superset-nested escalation for free. Pure function of
    the (locator, stratum) set → two runs order identically.
    """
    groups: Dict[str, List[CorpusMember]] = {}
    for m in members:
        groups.setdefault(stratum_of(m.pdf_locator), []).append(m)
    ranked: List[Tuple[float, str, str, CorpusMember]] = []
    for stratum in sorted(groups):
        g = sorted(groups[stratum], key=lambda m: m.pdf_locator)
        n = len(g)
        for idx, m in enumerate(g):
            pos = (idx + 0.5) / n
            ranked.append((pos, stratum, m.pdf_locator, m))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in ranked]


def _stratum_counts(
    members: Sequence[CorpusMember], stratum_of: Callable[[str], str]
) -> Tuple[Tuple[str, int], ...]:
    """``(stratum, count)`` over ``members`` in the fixed stratum order."""
    counts: Dict[str, int] = {s: 0 for s in _STRATUM_ORDER}
    for m in members:
        s = stratum_of(m.pdf_locator)
        counts[s] = counts.get(s, 0) + 1
    ordered = [s for s in _STRATUM_ORDER if counts.get(s)]
    extra = sorted(s for s in counts if s not in _STRATUM_ORDER and counts[s])
    return tuple((s, counts[s]) for s in ordered + extra)


@dataclass(frozen=True, slots=True)
class StagePlan:
    """One planned stage: the size-``size`` prefix of the stratified order."""

    index: int
    planned_size: int
    members: Tuple[CorpusMember, ...]
    stratum_counts: Tuple[Tuple[str, int], ...]


def plan_stages(
    members: Sequence[CorpusMember],
    stratum_of: Callable[[str], str],
    stages: Sequence[int],
) -> List[StagePlan]:
    """Turn the stage-size ladder into concrete, superset-nested stage prefixes."""
    order = stratified_order(members, stratum_of)
    plans: List[StagePlan] = []
    for i, size in enumerate(stages):
        prefix = tuple(order[:size])
        plans.append(
            StagePlan(
                index=i,
                planned_size=size,
                members=prefix,
                stratum_counts=_stratum_counts(prefix, stratum_of),
            )
        )
    return plans


# --------------------------------------------------------------------------- #
# Per-stage aggregate + the VoI gate.                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageAggregate:
    """The A/B aggregate over ONE stage's PDFs + the gate verdict.

    ``numeric_regressions`` is the count of NEW numeric-exact errors the
    de-facsimile lane introduced vs the baseline (positive ``numeric_delta`` only —
    a de-facsimile that FIXES numerics is never a gate failure). ``residual_*`` are
    the ABSOLUTE post-de-facsimile defect counts per class (baseline + delta), the
    input to the ranked residual table. ``gate_ok`` / ``stop_reasons`` carry the VoI
    verdict; token fields are populated only when a meter was present.
    """

    index: int
    planned_size: int
    actual_size: int
    stratum_counts: Tuple[Tuple[str, int], ...]
    n_ab: int
    n_coverage_only: int
    n_failed: int
    n_accepted: int
    acceptance_rate: float
    numeric_regressions: int
    extra_delta: int
    structure_delta: int
    missing_delta: int
    numeric_delta: int
    residual_extra: int
    residual_structure: int
    residual_missing: int
    residual_numeric: int
    gate_ok: bool
    stop_reasons: Tuple[str, ...]
    output_tokens: Optional[int] = None
    wall_seconds: Optional[float] = None
    tokens_per_second: Optional[float] = None


def _residual(base: int, delta: int) -> int:
    """Absolute post-de-facsimile defect count (never negative)."""
    return max(0, base + delta)


def aggregate_rows(
    rows: Sequence[RowResult],
    *,
    index: int,
    planned_size: int,
    stratum_counts: Tuple[Tuple[str, int], ...],
) -> StageAggregate:
    """Fold a stage's ``RowResult`` rows into the stage aggregate (gate left open).

    The gate verdict is filled in later by :func:`gate` (which needs the prior
    stage). Only ``status == "ab"`` rows contribute deltas / acceptance; coverage-
    only and failed rows are counted but carry no findings.
    """
    ab = [r for r in rows if r.status == "ab"]
    n_ab = len(ab)
    n_accepted = sum(1 for r in ab if r.accepted)
    numeric_regressions = sum(r.numeric_delta for r in ab if r.numeric_delta > 0)
    return StageAggregate(
        index=index,
        planned_size=planned_size,
        actual_size=len(rows),
        stratum_counts=stratum_counts,
        n_ab=n_ab,
        n_coverage_only=sum(1 for r in rows if r.status == "coverage_only"),
        n_failed=sum(1 for r in rows if r.status == "failed"),
        n_accepted=n_accepted,
        acceptance_rate=(n_accepted / n_ab) if n_ab else 0.0,
        numeric_regressions=numeric_regressions,
        extra_delta=sum(r.extra_delta for r in ab),
        structure_delta=sum(r.structure_delta for r in ab),
        missing_delta=sum(r.missing_delta for r in ab),
        numeric_delta=sum(r.numeric_delta for r in ab),
        residual_extra=sum(_residual(r.baseline_extra, r.extra_delta) for r in ab),
        residual_structure=sum(
            _residual(r.baseline_structure, r.structure_delta) for r in ab
        ),
        residual_missing=sum(_residual(r.baseline_missing, r.missing_delta) for r in ab),
        residual_numeric=sum(_residual(r.baseline_numeric, r.numeric_delta) for r in ab),
        gate_ok=True,
        stop_reasons=(),
    )


def gate(
    agg: StageAggregate,
    prev: Optional[StageAggregate],
    *,
    numeric_tolerance: int,
    accept_regression_tolerance: float,
) -> StageAggregate:
    """Return ``agg`` with its VoI gate verdict filled in.

    STOP (do NOT spend the next, larger tranche) if ANY of:
      * a run error occurred (a typed ``failed`` row — the lane itself is broken);
      * the de-facsimile introduced more than ``numeric_tolerance`` NEW numeric-exact
        errors (the PRIMARY gate, mirroring ``fi_calibration``'s NUMERIC-exact gate);
      * the acceptance rate fell more than ``accept_regression_tolerance`` below the
        prior stage (a widening sample suddenly accepting far fewer PDFs).
    The reasons are deterministic + human-readable so the operator sees WHY.
    """
    reasons: List[str] = []
    if agg.n_failed > 0:
        reasons.append(f"run_errors={agg.n_failed} (typed failed rows)")
    if agg.numeric_regressions > numeric_tolerance:
        reasons.append(
            f"numeric_regressions={agg.numeric_regressions} > tolerance={numeric_tolerance}"
        )
    if prev is not None:
        drop = prev.acceptance_rate - agg.acceptance_rate
        if drop > accept_regression_tolerance:
            reasons.append(
                f"acceptance_regressed by {drop:.3f} "
                f"({prev.acceptance_rate:.3f}->{agg.acceptance_rate:.3f}) "
                f"> tolerance={accept_regression_tolerance:.3f}"
            )
    return replace(agg, gate_ok=not reasons, stop_reasons=tuple(reasons))


# --------------------------------------------------------------------------- #
# The staged sweep run.                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SweepReport:
    """The full staged sweep result: per-stage aggregates + cumulative residual."""

    stages: Tuple[StageAggregate, ...]
    stopped_at: Optional[int]  # stage index whose gate stopped escalation, else None
    dropped_count: int
    dropped_stratum_counts: Tuple[Tuple[str, int], ...]
    n_selected: int
    residual_ranking: Tuple[Tuple[str, int], ...]  # (defect_class, residual) worst-first
    final_rows: Tuple[RowResult, ...]  # cumulative, worst-first ranked
    numeric_tolerance: int
    accept_regression_tolerance: float


def _rank_final_rows(rows: Sequence[RowResult]) -> Tuple[RowResult, ...]:
    """Worst-first (baseline EXTRA+STRUCTURE) A/B rows, then coverage/failed rows."""
    ab = sorted(
        (r for r in rows if r.status == "ab"),
        key=lambda r: (
            -r.baseline_findings,
            -r.baseline_missing,
            -r.baseline_numeric,
            r.pdf_locator,
        ),
    )
    rest = sorted(
        (r for r in rows if r.status != "ab"),
        key=lambda r: (r.status, r.pdf_locator),
    )
    return tuple(ab) + tuple(rest)


def _residual_ranking(agg: Optional[StageAggregate]) -> Tuple[Tuple[str, int], ...]:
    """Rank the four defect classes by residual count, worst first (deterministic)."""
    if agg is None:
        return tuple((c, 0) for c in _DEFECT_CLASSES)
    residual = {
        "EXTRA": agg.residual_extra,
        "STRUCTURE": agg.residual_structure,
        "MISSING": agg.residual_missing,
        "NUMERIC": agg.residual_numeric,
    }
    return tuple(sorted(residual.items(), key=lambda kv: (-kv[1], kv[0])))


def run_sweep(
    members: Sequence[CorpusMember],
    stratum_of: Callable[[str], str],
    processor: Callable[[CorpusMember], RowResult],
    *,
    stages: Sequence[int],
    numeric_tolerance: int = 0,
    accept_regression_tolerance: float = DEFAULT_ACCEPT_REGRESSION_TOLERANCE,
    workers: int = _DEFAULT_WORKERS,
    stratum_filter: Optional[str] = None,
    only_with_xml: bool = False,
    meter: Optional[TokenMeter] = None,
) -> SweepReport:
    """Run the escalating, gated staged sweep over ``members``.

    ``processor`` maps ONE member → its ``RowResult`` (the real driver binds
    ``fi_parse_corpus._process_one``; tests inject a scripted stub). Stages are the
    superset-nested prefixes of the stratified order; each stage processes only its
    NEW members (nested → prior rows re-used), folds the aggregate, and GATES. On a
    STOP the later, larger tranches are DROPPED (logged) and never run.

    Resume is by CONSTRUCTION, not a side checkpoint: the real processor persists
    every member's verdict into the OUTPUT FARCHIVE and returns any pre-existing one
    without recompute, so a re-run (after a shutdown, or the next batch up the ladder)
    re-walks completed members as fast farchive lookups and only spends model tokens
    on the genuinely new ones. There is no checkpoint file to keep in sync.
    """
    plans = plan_stages(members, stratum_of, stages)
    n_selected = len(members)

    rows_by_locator: Dict[str, RowResult] = {}
    stage_aggs: List[StageAggregate] = []
    stopped_at: Optional[int] = None
    prev_agg: Optional[StageAggregate] = None

    for plan in plans:
        # Only the NEW members of this stage need processing (stages are nested).
        new_members = [m for m in plan.members if m.pdf_locator not in rows_by_locator]

        tok_before = _snapshot(meter)
        if new_members:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = {pool.submit(processor, m): m for m in new_members}
                for fut in as_completed(futures):
                    r = fut.result()
                    rows_by_locator[r.pdf_locator] = r
        tok_after = _snapshot(meter)

        rows = [rows_by_locator[m.pdf_locator] for m in plan.members]
        agg = aggregate_rows(
            rows,
            index=plan.index,
            planned_size=plan.planned_size,
            stratum_counts=plan.stratum_counts,
        )
        agg = _with_tokens(agg, tok_before, tok_after)
        agg = gate(
            agg,
            prev_agg,
            numeric_tolerance=numeric_tolerance,
            accept_regression_tolerance=accept_regression_tolerance,
        )
        stage_aggs.append(agg)

        if not agg.gate_ok:
            stopped_at = plan.index
            break
        prev_agg = agg

    # What was DROPPED: the members in later, un-run stages beyond the last run one.
    ran_locators = set(rows_by_locator)
    dropped_members = [m for m in members if m.pdf_locator not in ran_locators]
    dropped_counts = _stratum_counts(dropped_members, stratum_of)

    final_rows = _rank_final_rows(list(rows_by_locator.values()))
    residual_ranking = _residual_ranking(stage_aggs[-1] if stage_aggs else None)

    return SweepReport(
        stages=tuple(stage_aggs),
        stopped_at=stopped_at,
        dropped_count=len(dropped_members),
        dropped_stratum_counts=dropped_counts,
        n_selected=n_selected,
        residual_ranking=residual_ranking,
        final_rows=final_rows,
        numeric_tolerance=numeric_tolerance,
        accept_regression_tolerance=accept_regression_tolerance,
    )


def _snapshot(meter: Optional[TokenMeter]) -> Optional[Tuple[int, float]]:
    """Guarded cumulative meter read; ``None`` when absent or unavailable."""
    if meter is None:
        return None
    try:
        toks, wall = meter.snapshot()
        return (int(toks), float(wall))
    except Exception:  # a flaky/absent meter must never sink the sweep
        return None


def _with_tokens(
    agg: StageAggregate,
    before: Optional[Tuple[int, float]],
    after: Optional[Tuple[int, float]],
) -> StageAggregate:
    """Attach the per-stage token/throughput delta when a meter bracketed the stage."""
    if before is None or after is None:
        return agg
    dt_tok = max(0, after[0] - before[0])
    dt_wall = max(0.0, after[1] - before[1])
    tps = (dt_tok / dt_wall) if dt_wall > 0 else None
    return replace(
        agg, output_tokens=dt_tok, wall_seconds=dt_wall, tokens_per_second=tps
    )


# --------------------------------------------------------------------------- #
# Rendering (deterministic line-based; two runs diff empty).                    #
# --------------------------------------------------------------------------- #


def _fmt_strata(counts: Sequence[Tuple[str, int]]) -> str:
    return ";".join(f"{s}={c}" for s, c in counts) or "-"


def render_report(report: SweepReport) -> str:
    """Deterministic multi-block text render of the staged sweep."""
    lines: List[str] = []
    lines.append(
        "# fi-sweep — VoI-staged corpus reliability sweep (escalate + gate; "
        "de-facsimile vs struct_span, NUMERIC-exact primary gate)"
    )
    lines.append(
        f"# selected={report.n_selected}  "
        f"numeric_tolerance={report.numeric_tolerance}  "
        f"accept_regression_tolerance={report.accept_regression_tolerance:.3f}  "
        f"stopped_at_stage={report.stopped_at if report.stopped_at is not None else 'none'}"
    )
    lines.append("")
    lines.append(
        "## PER-STAGE (escalating, superset-nested; gate STOPS escalation on failure)"
    )
    lines.append(
        "stage,planned,actual,strata,n_ab,cov_only,failed,accepted,accept_rate,"
        "numeric_regr,extra_d,structure_d,missing_d,gate,reasons"
    )
    for agg in report.stages:
        gate_word = "PROCEED" if agg.gate_ok else "STOP"
        reasons = "|".join(agg.stop_reasons) if agg.stop_reasons else "-"
        lines.append(
            ",".join(
                str(v)
                for v in (
                    agg.index,
                    agg.planned_size,
                    agg.actual_size,
                    _fmt_strata(agg.stratum_counts),
                    agg.n_ab,
                    agg.n_coverage_only,
                    agg.n_failed,
                    agg.n_accepted,
                    f"{agg.acceptance_rate:.4f}",
                    agg.numeric_regressions,
                    agg.extra_delta,
                    agg.structure_delta,
                    agg.missing_delta,
                    gate_word,
                    reasons,
                )
            )
        )
    # Optional token/throughput block (only when a meter was present on any stage).
    if any(a.output_tokens is not None for a in report.stages):
        lines.append("")
        lines.append("## THROUGHPUT (per stage; present only when a token meter is bound)")
        lines.append("stage,output_tokens,wall_seconds,tokens_per_second")
        for agg in report.stages:
            if agg.output_tokens is None:
                continue
            tps = (
                f"{agg.tokens_per_second:.2f}"
                if agg.tokens_per_second is not None
                else "-"
            )
            wall = f"{agg.wall_seconds:.3f}" if agg.wall_seconds is not None else "-"
            lines.append(f"{agg.index},{agg.output_tokens},{wall},{tps}")

    lines.append("")
    lines.append("## RANKED RESIDUAL DEFECT CLASSES (cumulative, worst first)")
    lines.append("rank,defect_class,residual_count")
    for rank, (cls, cnt) in enumerate(report.residual_ranking, start=1):
        lines.append(f"{rank},{cls},{cnt}")

    lines.append("")
    lines.append("## DROPPED (un-run tranche after a gate STOP — the GPU cost avoided)")
    lines.append(
        f"# dropped={report.dropped_count}  "
        f"strata={_fmt_strata(report.dropped_stratum_counts)}"
    )
    return "\n".join(lines)


def _plan_lines(plans: Sequence[StagePlan], n_selected: int) -> str:
    """Deterministic ``--dry-run`` plan render (no vision touched)."""
    lines: List[str] = []
    lines.append(
        "# fi-sweep --dry-run — planned stratified, superset-nested stages (NO vision)"
    )
    lines.append(f"# selected={n_selected}")
    lines.append("stage,planned_size,actual_size,strata")
    for p in plans:
        lines.append(
            f"{p.index},{p.planned_size},{len(p.members)},{_fmt_strata(p.stratum_counts)}"
        )
    return "\n".join(lines)


def report_to_json(report: SweepReport) -> Dict[str, object]:
    """JSON form of the staged sweep (same deterministic ordering as the render)."""
    return {
        "n_selected": report.n_selected,
        "numeric_tolerance": report.numeric_tolerance,
        "accept_regression_tolerance": report.accept_regression_tolerance,
        "stopped_at_stage": report.stopped_at,
        "dropped_count": report.dropped_count,
        "dropped_strata": {s: c for s, c in report.dropped_stratum_counts},
        "stages": [
            {
                "index": a.index,
                "planned_size": a.planned_size,
                "actual_size": a.actual_size,
                "strata": {s: c for s, c in a.stratum_counts},
                "n_ab": a.n_ab,
                "n_coverage_only": a.n_coverage_only,
                "n_failed": a.n_failed,
                "n_accepted": a.n_accepted,
                "acceptance_rate": a.acceptance_rate,
                "numeric_regressions": a.numeric_regressions,
                "extra_delta": a.extra_delta,
                "structure_delta": a.structure_delta,
                "missing_delta": a.missing_delta,
                "numeric_delta": a.numeric_delta,
                "residual": {
                    "EXTRA": a.residual_extra,
                    "STRUCTURE": a.residual_structure,
                    "MISSING": a.residual_missing,
                    "NUMERIC": a.residual_numeric,
                },
                "gate_ok": a.gate_ok,
                "stop_reasons": list(a.stop_reasons),
                "output_tokens": a.output_tokens,
                "wall_seconds": a.wall_seconds,
                "tokens_per_second": a.tokens_per_second,
            }
            for a in report.stages
        ],
        "residual_ranking": [
            {"rank": i, "defect_class": c, "residual_count": n}
            for i, (c, n) in enumerate(report.residual_ranking, start=1)
        ],
    }


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def _build_stratum_map(
    members: Sequence[CorpusMember], finlex_path: str, workers: int
) -> Dict[str, str]:
    """The per-PDF stratum via the cheap pdfium text-layer census (no vision).

    Reuses ``fi_scan_stratum.census_scan_strata`` over exactly the selected
    locators. This is the pdfium-only cost the VoI staging accepts up front so the
    sample can be drawn STRATIFIED; the expensive GPU vision cost is what the
    stage gate protects.
    """
    locators = [m.pdf_locator for m in members]
    census = census_scan_strata(
        finlex_path=finlex_path, workers=workers, locators=locators
    )
    return {r.locator: r.stratum for r in census.records}


def _select_members(
    members: Sequence[CorpusMember],
    *,
    only_with_xml: bool,
    stratum_filter: Optional[str],
    stratum_of: Callable[[str], str],
) -> List[CorpusMember]:
    """Apply ``--only-with-xml`` then ``--stratum`` over the enumerated members."""
    pool = [m for m in members if m.has_xml] if only_with_xml else list(members)
    if stratum_filter is not None:
        pool = [m for m in pool if stratum_of(m.pdf_locator) == stratum_filter]
    return pool


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-sweep``.

    Enumerates the finlex PDFs, censuses their strata (cheap pdfium), plans the
    escalating stratified stages, then (unless ``--dry-run``) probes the vision
    backend ONCE and runs the gated staged sweep. The full run is OPERATOR-invoked;
    CI drives ``run_sweep`` directly with a fake processor (see the test).
    """
    finlex_path = args.finlex or _FINLEX_DEFAULT
    workers = args.workers if args.workers else _DEFAULT_WORKERS

    members_all = enumerate_corpus(finlex_path)
    if not members_all:
        raise SystemExit(f"fi-sweep: no PDFs enumerated in {finlex_path}")

    stratum_map = _build_stratum_map(members_all, finlex_path, workers)

    def stratum_of(loc: str) -> str:
        return stratum_map.get(loc, STRATUM_UNREADABLE)

    selected = _select_members(
        members_all,
        only_with_xml=bool(args.only_with_xml),
        stratum_filter=args.stratum,
        stratum_of=stratum_of,
    )
    if not selected:
        raise SystemExit(
            "fi-sweep: no PDFs selected "
            f"(enumerated {len(members_all)}; --only-with-xml={bool(args.only_with_xml)}; "
            f"--stratum={args.stratum})"
        )

    stage_sizes = resolve_stages(args.stages or DEFAULT_STAGES, len(selected))

    if args.dry_run:
        plans = plan_stages(selected, stratum_of, stage_sizes)
        print(_plan_lines(plans, len(selected)))
        return

    # Probe the vision backend ONCE, up front (fail loud rather than turning every
    # row into a typed failure) — mirrors fi-parse-corpus discipline.
    from lawvm.ingest.parsed_store import ParseBackendUnavailable, resolve_pipeline
    from lawvm.tools.fi_parse_corpus import PARSED_STORE_DEFAULT, _process_one

    modality = "struct_span"
    try:
        resolve_pipeline(transcription_modality=modality)
    except ParseBackendUnavailable as exc:
        raise SystemExit(f"fi-sweep: {exc}") from exc

    store_path = args.store or PARSED_STORE_DEFAULT

    def processor(member: CorpusMember) -> RowResult:
        return _process_one(
            member,
            finlex_path=finlex_path,
            store_path=store_path,
            modality=modality,
            max_pages=args.max_pages,
        )

    report = run_sweep(
        selected,
        stratum_of,
        processor,
        stages=stage_sizes,
        numeric_tolerance=args.numeric_tolerance,
        accept_regression_tolerance=args.accept_regression_tolerance,
        workers=workers,
        stratum_filter=args.stratum,
        only_with_xml=bool(args.only_with_xml),
    )

    if args.json:
        payload = json.dumps(report_to_json(report), ensure_ascii=False, indent=2)
        print(payload)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(payload)
    else:
        print(render_report(report))
