"""Unified differential census over Finland construction-grammar families.

Aggregates the four family plug-ins (scope-carrier, temporal/applicability,
citation-sentence, definition-entry) into one scoreboard. Pure measure-only —
off the replay/apply path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lawvm.finland.legal_surface.definition_census import (
    DEFINITION_FAMILY,
    run_definition_census,
)
from lawvm.finland.legal_surface.family_census import FamilyCensusResult
from lawvm.finland.legal_surface.scope_carrier_census import (
    SCOPE_CARRIER_FAMILY,
    run_scope_carrier_census,
)
from lawvm.finland.legal_surface.sentence_census import (
    SENTENCE_FAMILY,
    SentenceCensusResult,
    run_sentence_census,
)
from lawvm.finland.legal_surface.temporal_census import (
    TEMPORAL_FAMILY,
    run_temporal_census,
)

GRAMMAR_CENSUS_FAMILIES: tuple[str, ...] = (
    SCOPE_CARRIER_FAMILY,
    TEMPORAL_FAMILY,
    SENTENCE_FAMILY,
    DEFINITION_FAMILY,
)

FamilyRunner = Callable[..., FamilyCensusResult | SentenceCensusResult]


@dataclass(frozen=True, slots=True)
class GrammarFamilySummary:
    """Per-family bucket rollup for the unified grammar census."""

    family_id: str
    statutes_scanned: int
    in_scope_units: int
    buckets: dict[str, int]
    totality_violations: int
    miss_shape_counts: dict[str, int]
    partition_ok: bool
    distance_from_miss_zero: int


@dataclass(frozen=True, slots=True)
class GrammarCensusResult:
    """Outcome of a unified grammar differential census run."""

    families: tuple[GrammarFamilySummary, ...]
    statutes_scanned: int

    @property
    def total_in_scope_units(self) -> int:
        return sum(f.in_scope_units for f in self.families)

    @property
    def total_miss(self) -> int:
        return sum(f.distance_from_miss_zero for f in self.families)

    def all_partitions_ok(self) -> bool:
        return all(f.partition_ok for f in self.families)


def _family_runners() -> dict[str, FamilyRunner]:
    return {
        SCOPE_CARRIER_FAMILY: run_scope_carrier_census,
        TEMPORAL_FAMILY: run_temporal_census,
        SENTENCE_FAMILY: run_sentence_census,
        DEFINITION_FAMILY: run_definition_census,
    }


def _summarize_family(family_id: str, result: FamilyCensusResult | SentenceCensusResult) -> GrammarFamilySummary:
    if isinstance(result, SentenceCensusResult):
        in_scope = result.in_scope_segments
        partition_ok = result.is_partition()
    else:
        in_scope = result.in_scope_units
        partition_ok = result.is_partition()
    return GrammarFamilySummary(
        family_id=family_id,
        statutes_scanned=result.statutes_scanned,
        in_scope_units=in_scope,
        buckets=dict(result.buckets),
        totality_violations=result.totality_violations,
        miss_shape_counts=dict(result.miss_shape_counts),
        partition_ok=partition_ok,
        distance_from_miss_zero=result.buckets.get("miss", 0),
    )


def run_grammar_census(
    *,
    families: Sequence[str] | None = None,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 6,
    full_sentence_oracle: bool = True,
) -> GrammarCensusResult:
    """Run one or more construction-grammar family censuses and aggregate."""
    runners = _family_runners()
    selected = tuple(families) if families else GRAMMAR_CENSUS_FAMILIES
    unknown = [f for f in selected if f not in runners]
    if unknown:
        raise ValueError(f"unknown grammar census families: {unknown}")

    summaries: list[GrammarFamilySummary] = []
    statutes_scanned = 0
    for family_id in selected:
        runner = runners[family_id]
        if family_id == SENTENCE_FAMILY:
            result = runner(
                limit=limit,
                min_year=min_year,
                check_totality=check_totality,
                max_examples=max_examples,
                full_oracle=full_sentence_oracle,
            )
        else:
            result = runner(
                limit=limit,
                min_year=min_year,
                check_totality=check_totality,
                max_examples=max_examples,
            )
        summaries.append(_summarize_family(family_id, result))
        statutes_scanned = max(statutes_scanned, result.statutes_scanned)

    return GrammarCensusResult(
        families=tuple(summaries),
        statutes_scanned=statutes_scanned,
    )


def format_grammar_census_report(result: GrammarCensusResult) -> str:
    """Render the unified grammar census scoreboard as text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FINLAND CONSTRUCTION-GRAMMAR DIFFERENTIAL CENSUS")
    lines.append("=" * 72)
    lines.append(f"  statutes scanned (max across families) : {result.statutes_scanned}")
    lines.append(f"  families run                           : {len(result.families)}")
    lines.append(f"  total in-scope units                 : {result.total_in_scope_units}")
    lines.append(f"  total distance from miss=0           : {result.total_miss}")
    lines.append(f"  all partitions ok                    : {result.all_partitions_ok()}")
    lines.append("")

    for summary in result.families:
        lines.append("-" * 72)
        lines.append(f"family: {summary.family_id}")
        lines.append("-" * 72)
        lines.append(f"  statutes scanned     : {summary.statutes_scanned}")
        lines.append(f"  in-scope units       : {summary.in_scope_units}")
        for bucket in ("match", "superset", "miss", "decline"):
            count = summary.buckets.get(bucket, 0)
            pct = (
                f"{100 * count / summary.in_scope_units:.2f}%"
                if summary.in_scope_units
                else "n/a"
            )
            lines.append(f"  {bucket:<18}: {count:6d}  ({pct})")
        lines.append(f"  partition ok         : {summary.partition_ok}")
        lines.append(f"  totality violations  : {summary.totality_violations}")
        lines.append(f"  miss=0 distance      : {summary.distance_from_miss_zero}")
        if summary.miss_shape_counts:
            top = sorted(summary.miss_shape_counts.items(), key=lambda kv: -kv[1])[:5]
            lines.append(f"  top miss shapes      : {top}")
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "GRAMMAR_CENSUS_FAMILIES",
    "GrammarCensusResult",
    "GrammarFamilySummary",
    "format_grammar_census_report",
    "run_grammar_census",
]
