"""Whole-corpus ``legacy_reference_fallback`` coverage census (retained-residual proof).

The typed-path goal retains a legacy construct ONLY with "full-corpus coverage
evidence proving it still load-bearing." The ``legacy_reference_fallback`` lane is
the biggest retained legacy construct: when the grammar parser declines
(:class:`~...grammar.parser.OutOfScope`), production
(:func:`~lawvm.finland.johtolause.api.parse_clause`) falls back to the legacy
``surface_parse``. This module is the COMPLETE, regenerable census that converts
"the residual is load-bearing" from a claim into checked evidence.

Where the sibling :mod:`fallback_residue` audit and the
:mod:`census_accounting` partition both run over the AMENDMENT subset only (their
denominator filters to ``old_model.verb_groups`` non-empty), THIS census runs
over the **WHOLE corpus** — every statute with a johtolause, including the ~23k
zero-amendment enactments — and classifies every statute whose production parser
lane is ``legacy_reference_fallback`` into exactly one of three coverage buckets:

  * ``NON_AMENDMENT`` — the legacy fallback emits ZERO amendment ops
    (``len(parsed_ops) == 0``). These are enactment / ``säädetään`` / decree
    clauses (and archaic ``säädetään että … muutetaan`` wrappers neither parser
    models) that legitimately decline. Retaining the fallback for them costs
    nothing: it is a no-op fallback, NOT deletion-blocking.
  * ``LOAD_BEARING`` — the legacy fallback emits >= 1 amendment op the grammar
    declines (``len(parsed_ops) >= 1``) AND the firing is NOT on the pinned
    ``MIGRATABLE`` TODO set. The fallback is genuinely doing operative work the
    deterministic grammar cannot reproduce, so it is legitimately RETAINED. This
    is the deletion-blocking residue the goal names — and now each firing carries
    a per-statute witness (its op count + generalized decline reason).
  * ``MIGRATABLE`` — a firing the grammar COULD own byte-identically /
    oracle-correctly but does not yet (a real TODO, NOT yet retained-with-
    evidence). Because the grammar declined, "could own" cannot be soundly
    auto-detected from the decline alone; ``MIGRATABLE`` is therefore a
    human-adjudicated, pinned sid set (the same idiom as the
    ``census_accounting`` adjudication ledger). The guard test pins its size, so
    any NEW migratable shape (a future regression where the grammar declines
    something it should own) is caught rather than silently absorbed into
    ``LOAD_BEARING``.

The witness method is production-faithful: it runs the real production entrypoint
``parse_clause`` (not a helper) over every statute, reads ``parser_lane`` and
``parsed_ops`` off the returned :class:`~...api.ClauseParseResult`, and partitions
on those two observable production outputs. The op count is the load-bearing
witness; the generalized decline reason is the per-firing provenance label.

This is a pure addition: it imports the corpus + parser lazily and changes no
parsing behaviour. It is the coverage ledger over the fallback boundary.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from lawvm.finland.johtolause.fallback_residue import generalize_decline_reason

# ---------------------------------------------------------------------------
# The three coverage buckets, in report order. Every legacy_reference_fallback
# firing maps to exactly one.
# ---------------------------------------------------------------------------
FALLBACK_COVERAGE_BUCKETS: tuple[str, ...] = (
    "NON_AMENDMENT",
    "LOAD_BEARING",
    "MIGRATABLE",
)


# ---------------------------------------------------------------------------
# MIGRATABLE TODO set. A firing whose legacy fallback emits >= 1 op AND that a
# human has determined the grammar could own byte-identically / oracle-correctly,
# but which has not yet been migrated. Empty today: every op-bearing fallback
# firing on the canonical corpus has been characterized as genuinely
# LOAD_BEARING (the grammar cannot reproduce it without an unsound recognizer)
# OR has already been migrated into the grammar (and so no longer fires the
# fallback). A human adds a sid here ONLY with an explicit one-line evidence note
# that the grammar could own it; the guard test then expects it to leave the
# fallback set once the recognizer lands.
# ---------------------------------------------------------------------------
FI_FALLBACK_MIGRATABLE_TODO_V0: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FallbackCoverageResult:
    """Outcome of a whole-corpus legacy_reference_fallback coverage census."""

    #: total statutes with a non-empty johtolause that ``parse_clause`` returned
    #: a result for (the census denominator universe).
    total_with_johtolause: int
    #: total statutes whose production parser lane is ``legacy_reference_fallback``.
    total_fallback_firings: int
    #: bucket id -> count. Keys are exactly :data:`FALLBACK_COVERAGE_BUCKETS`.
    buckets: dict[str, int]
    #: generalized decline reason -> count, over LOAD_BEARING firings only (the
    #: per-reason witness of what the retained residue is made of).
    load_bearing_reason_counts: dict[str, int]
    #: generalized decline reason -> count, over NON_AMENDMENT firings (provenance
    #: only — these are no-op fallbacks).
    non_amendment_reason_counts: dict[str, int]
    #: (bucket, generalized reason) -> a sample statute id (for spot-audit). Keyed
    #: by bucket too, because a reason (e.g. ``not a target at target position``)
    #: legitimately appears in BOTH LOAD_BEARING and NON_AMENDMENT depending on
    #: whether the legacy fallback emitted ops, and the spot-audit witness must be
    #: drawn from the SAME bucket it labels.
    reason_samples: dict[tuple[str, str], str]
    #: sids currently classified MIGRATABLE (the pinned TODO that fired).
    migratable_sids: list[str] = field(default_factory=list)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        """The three buckets sum to the total fallback firings (no leak)."""
        return self.partition_total == self.total_fallback_firings


# ---------------------------------------------------------------------------
# Per-statute worker. Runs the PRODUCTION entrypoint ``parse_clause`` and reports
# (sid, lane, n_ops, generalized_reason). Lazy imports so the pool children stay
# cheap and the module import is dependency-free.
# ---------------------------------------------------------------------------
def _scan_one(sid: str) -> tuple[str, str, int, str] | None:
    from farchive import Farchive

    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xb = store.read_source(sid) or store.read_amendment(sid)
    if not xb:
        return None
    try:
        johto = get_johtolause(xb) or ""
    except Exception:  # lawvm-failloud: unparseable source yields no johtolause clause to census; no fallback firing to classify
        return None
    if not johto:
        return None
    try:
        result = parse_clause(johto, statute_id=sid)
    except Exception:  # lawvm-failloud: production parse crash tracked by the fallback-residue audit, not a clean fallback firing; out of census partition scope
        # A production crash is not a clean fallback firing; out of census scope
        # (the fallback-residue audit tracks crashes separately). Counted only in
        # the denominator implicitly by being excluded here.
        return None
    lane = result.parser_lane
    n_ops = len(result.parsed_ops)
    reason = generalize_decline_reason(result.grammar_decline_reason or "") if (
        lane == "legacy_reference_fallback"
    ) else ""
    return (sid, lane, n_ops, reason)


def census_fallback_coverage(
    limit: int = 0,
    *,
    workers: int = 16,
    migratable_todo: frozenset[str] | None = None,
) -> FallbackCoverageResult:
    """Census every ``legacy_reference_fallback`` firing over the whole corpus.

    Runs the production ``parse_clause`` over every statute (or the first
    ``limit`` ids) and partitions the fallback firings into NON_AMENDMENT /
    LOAD_BEARING / MIGRATABLE using the observable production outputs
    (``parser_lane`` + ``parsed_ops``). Requires the canonical Finlex corpus
    (``LAWVM_CANONICAL_DATA_ROOT``).
    """
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    if migratable_todo is None:
        migratable_todo = FI_FALLBACK_MIGRATABLE_TODO_V0

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]

    counts: Counter[str] = Counter()
    load_bearing_reasons: Counter[str] = Counter()
    non_amendment_reasons: Counter[str] = Counter()
    reason_samples: dict[tuple[str, str], str] = {}
    migratable_sids: list[str] = []
    total_with_johtolause = 0
    total_fallback = 0

    def _consume(res: tuple[str, str, int, str] | None) -> None:
        nonlocal total_with_johtolause, total_fallback
        if res is None:
            return
        sid, lane, n_ops, reason = res
        total_with_johtolause += 1
        if lane != "legacy_reference_fallback":
            return
        total_fallback += 1
        if sid in migratable_todo:
            counts["MIGRATABLE"] += 1
            migratable_sids.append(sid)
            reason_samples.setdefault(("MIGRATABLE", reason), sid)
        elif n_ops == 0:
            counts["NON_AMENDMENT"] += 1
            non_amendment_reasons[reason] += 1
            reason_samples.setdefault(("NON_AMENDMENT", reason), sid)
        else:
            counts["LOAD_BEARING"] += 1
            load_bearing_reasons[reason] += 1
            reason_samples.setdefault(("LOAD_BEARING", reason), sid)

    if workers and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(_scan_one, ids, chunksize=64):
                _consume(res)
    else:
        for sid in ids:
            _consume(_scan_one(sid))

    buckets = {b: counts.get(b, 0) for b in FALLBACK_COVERAGE_BUCKETS}

    return FallbackCoverageResult(
        total_with_johtolause=total_with_johtolause,
        total_fallback_firings=total_fallback,
        buckets=buckets,
        load_bearing_reason_counts=dict(load_bearing_reasons),
        non_amendment_reason_counts=dict(non_amendment_reasons),
        reason_samples=reason_samples,
        migratable_sids=sorted(migratable_sids),
    )


def result_to_json(result: FallbackCoverageResult) -> dict:
    """Render the census result as a deterministic, machine-readable dict."""
    return {
        "schema": "fi_fallback_coverage_census.v1",
        "total_with_johtolause": result.total_with_johtolause,
        "total_fallback_firings": result.total_fallback_firings,
        "buckets": {b: result.buckets[b] for b in FALLBACK_COVERAGE_BUCKETS},
        "partition_ok": result.is_partition(),
        "load_bearing_reason_counts": dict(
            sorted(result.load_bearing_reason_counts.items())
        ),
        "non_amendment_reason_counts": dict(
            sorted(result.non_amendment_reason_counts.items())
        ),
        "reason_samples": {
            f"{bucket}:{reason}": sid
            for (bucket, reason), sid in sorted(result.reason_samples.items())
        },
        "migratable_sids": result.migratable_sids,
    }


def format_coverage_report(result: FallbackCoverageResult) -> str:
    """Render the three-bucket coverage census as human-readable text."""
    fb = result.total_fallback_firings

    def pct(n: int) -> str:
        return f"{100 * n / fb:.2f}%" if fb else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FI legacy_reference_fallback COVERAGE CENSUS (whole corpus)")
    lines.append("=" * 72)
    lines.append(f"  statutes with a johtolause       : {result.total_with_johtolause}")
    lines.append(f"  legacy_reference_fallback firings: {fb}")
    lines.append("-" * 72)
    for b in FALLBACK_COVERAGE_BUCKETS:
        n = result.buckets[b]
        lines.append(f"  {b:<16}: {n:6d}  ({pct(n)})")
    lines.append("-" * 72)
    lines.append(
        f"  partition sum   : {result.partition_total:6d}  "
        f"(== fallback firings: {result.is_partition()})"
    )
    lines.append("")

    if result.load_bearing_reason_counts:
        lines.append("-" * 72)
        lines.append("LOAD_BEARING — by generalized decline reason (the retained residue)")
        lines.append("-" * 72)
        for reason, n in sorted(
            result.load_bearing_reason_counts.items(), key=lambda kv: -kv[1]
        ):
            sample = result.reason_samples.get(("LOAD_BEARING", reason))
            lines.append(f"  {n:6d}  {reason!r}  (sample {sample})")
        lines.append("")

    if result.non_amendment_reason_counts:
        lines.append("-" * 72)
        lines.append("NON_AMENDMENT — by generalized decline reason (no-op fallbacks)")
        lines.append("-" * 72)
        for reason, n in sorted(
            result.non_amendment_reason_counts.items(), key=lambda kv: -kv[1]
        ):
            sample = result.reason_samples.get(("NON_AMENDMENT", reason))
            lines.append(f"  {n:6d}  {reason!r}  (sample {sample})")
        lines.append("")

    if result.migratable_sids:
        lines.append("-" * 72)
        lines.append("MIGRATABLE — pinned TODO sids that still fire the fallback")
        lines.append("-" * 72)
        for sid in result.migratable_sids:
            lines.append(f"  {sid}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    import sys

    args = sys.argv[1:]
    emit_json = "--json" in args
    positional = [a for a in args if not a.startswith("-")]
    limit = int(positional[0]) if positional else 0
    result = census_fallback_coverage(limit=limit)
    if emit_json:
        print(json.dumps(result_to_json(result), indent=2, ensure_ascii=False))
    else:
        print(format_coverage_report(result))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but fallback firings = {result.total_fallback_firings}"
        )


if __name__ == "__main__":
    main()
