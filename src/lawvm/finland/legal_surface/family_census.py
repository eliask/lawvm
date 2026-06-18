"""Family-agnostic differential census engine for SourceSyntaxGraph islands.

Pure measure-only scaffolding: iterates corpus units, diffs projection vs
oracle key sets into four buckets (match / superset / miss / decline). Off the
replay/apply path.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

CENSUS_BUCKETS: tuple[str, ...] = ("match", "superset", "miss", "decline")


@dataclass(frozen=True)
class CensusUnit:
    """One in-scope census unit a family's segment_selector yields."""

    text: str
    parser_lane: str
    declared_marker: str = ""
    declined: bool = False
    totality_ok: bool = True
    parser_facade_lane: str = ""


@dataclass(frozen=True)
class CensusRow:
    """The census verdict for one in-scope unit."""

    statute_id: str
    bucket: str
    projection_keys: tuple[str, ...]
    oracle_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]
    extra_keys: tuple[str, ...]
    declared_marker: str
    parser_lane: str
    parser_facade_lane: str
    totality_ok: bool
    text: str


@dataclass(frozen=True)
class FamilyCensusResult:
    """Outcome of a family differential census run."""

    family: str
    statutes_scanned: int
    in_scope_units: int
    buckets: dict[str, int]
    totality_violations: int
    miss_shape_counts: dict[str, int]
    miss_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    superset_examples: tuple[CensusRow, ...] = field(default_factory=tuple)
    decline_examples: tuple[CensusRow, ...] = field(default_factory=tuple)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        return self.partition_total == self.in_scope_units


def classify(projection: set[str], oracle: set[str], declined: bool) -> str:
    """Bucket a unit from projection/oracle key sets and the decline flag."""
    if declined:
        return "decline"
    missing = oracle - projection
    if missing:
        return "miss"
    if projection - oracle:
        return "superset"
    return "match"


SegmentSelector = Callable[[str, str], Iterator[CensusUnit]]
ProjectionFn = Callable[[CensusUnit, str], set[str]]
OracleFn = Callable[[CensusUnit, object], set[str]]
OraclePrepareFn = Callable[[str, str], object]
MissShapeFn = Callable[[set[str], str], str]


def run_family_census(
    *,
    family: str,
    segment_selector: SegmentSelector,
    projection_fn: ProjectionFn,
    oracle_fn: OracleFn,
    miss_shape_fn: MissShapeFn,
    oracle_prepare_fn: OraclePrepareFn | None = None,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 6,
) -> FamilyCensusResult:
    """Run a 4-bucket differential census for one construction-grammar family."""
    from farchive import Farchive

    from lawvm.finland.legal_surface.bundle import decode_body_text
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    if check_totality is None:
        check_totality = bool(os.environ.get("LAWVM_PARSE_TOTALITY"))

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    if min_year:
        ids = [s for s in ids if s[:4].isdigit() and int(s[:4]) >= min_year]
    if limit:
        ids = ids[:limit]

    counts: Counter[str] = Counter()
    miss_shape_counts: Counter[str] = Counter()
    miss_examples: list[CensusRow] = []
    superset_examples: list[CensusRow] = []
    decline_examples: list[CensusRow] = []
    statutes_scanned = 0
    in_scope_units = 0
    totality_violations = 0

    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            body = decode_body_text(xb)
        except Exception:
            continue
        if not body:
            continue
        statutes_scanned += 1

        try:
            units = list(segment_selector(sid, body))
        except Exception:
            continue

        oracle_ctx: object = None
        if oracle_prepare_fn is not None and units:
            try:
                oracle_ctx = oracle_prepare_fn(sid, body)
            except Exception:
                oracle_ctx = None

        for unit in units:
            in_scope_units += 1
            if check_totality and not unit.totality_ok:
                totality_violations += 1

            projection = projection_fn(unit, sid)
            oracle = oracle_fn(unit, oracle_ctx)
            bucket = classify(projection, oracle, unit.declined)
            counts[bucket] += 1

            missing = oracle - projection
            extra = projection - oracle
            row = CensusRow(
                statute_id=sid,
                bucket=bucket,
                projection_keys=tuple(sorted(projection)),
                oracle_keys=tuple(sorted(oracle)),
                missing_keys=tuple(sorted(missing)),
                extra_keys=tuple(sorted(extra)),
                declared_marker=unit.declared_marker,
                parser_lane=unit.parser_lane,
                parser_facade_lane=unit.parser_facade_lane,
                totality_ok=unit.totality_ok,
                text=unit.text,
            )
            if bucket == "miss":
                miss_shape_counts[miss_shape_fn(missing, unit.declared_marker)] += 1
                if len(miss_examples) < max_examples:
                    miss_examples.append(row)
            elif bucket == "superset" and len(superset_examples) < max_examples:
                superset_examples.append(row)
            elif bucket == "decline" and len(decline_examples) < max_examples:
                decline_examples.append(row)

    buckets = {b: counts.get(b, 0) for b in CENSUS_BUCKETS}
    return FamilyCensusResult(
        family=family,
        statutes_scanned=statutes_scanned,
        in_scope_units=in_scope_units,
        buckets=buckets,
        totality_violations=totality_violations,
        miss_shape_counts=dict(miss_shape_counts),
        miss_examples=tuple(miss_examples),
        superset_examples=tuple(superset_examples),
        decline_examples=tuple(decline_examples),
    )


def format_family_census_report(result: FamilyCensusResult, *, title: str) -> str:
    """Render the four-bucket differential-census scoreboard as text."""
    total = result.in_scope_units

    def pct(n: int) -> str:
        return f"{100 * n / total:.2f}%" if total else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"  statutes scanned                : {result.statutes_scanned}")
    lines.append(f"  in-scope units                  : {result.in_scope_units}")
    lines.append("-" * 72)
    for bucket in CENSUS_BUCKETS:
        count = result.buckets[bucket]
        lines.append(f"  {bucket:<28}: {count:6d}  ({pct(count)})")
    lines.append("-" * 72)
    lines.append(
        f"  partition sum                   : {result.partition_total:6d}  "
        f"(== in-scope: {result.is_partition()})"
    )
    lines.append(f"  distance from miss=0            : {result.buckets['miss']}")
    lines.append(f"  totality (no-silent-drop) viols : {result.totality_violations}")
    lines.append("")

    if result.miss_shape_counts:
        lines.append("-" * 72)
        lines.append("miss shapes (ranked — what blocks miss=0)")
        lines.append("-" * 72)
        for shape, count in sorted(result.miss_shape_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:6d}  {shape}")
        lines.append("")

    def _examples(heading: str, rows: tuple[CensusRow, ...]) -> None:
        if not rows:
            return
        lines.append("-" * 72)
        lines.append(heading)
        lines.append("-" * 72)
        for row in rows:
            snippet = row.text if len(row.text) <= 160 else row.text[:157] + "..."
            lines.append(f"  [{row.statute_id}] marker={row.declared_marker or '-'}")
            lines.append(f"    proj  : {list(row.projection_keys)}")
            lines.append(f"    oracle: {list(row.oracle_keys)}")
            if row.missing_keys:
                lines.append(f"    MISS  : {list(row.missing_keys)}")
            if row.extra_keys:
                lines.append(f"    EXTRA : {list(row.extra_keys)}")
            lines.append(f"    text  : {snippet!r}")
        lines.append("")

    _examples("miss examples (oracle found, projection did not)", result.miss_examples)
    _examples("superset examples (projection found strictly more)", result.superset_examples)
    _examples("decline examples (construction parser refused)", result.decline_examples)

    return "\n".join(lines)


__all__ = [
    "CENSUS_BUCKETS",
    "CensusRow",
    "CensusUnit",
    "FamilyCensusResult",
    "classify",
    "format_family_census_report",
    "run_family_census",
]
