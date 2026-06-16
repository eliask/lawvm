"""Full-accounting census split for the FI johtolause parser (Pro P0 #4).

The coarse swap-readiness census (``.tmp/validate_census.py``) sorts every
amendment johtolause into three buckets — 0-delta, declined, genuine-delta. That
is enough to track swap readiness but it is *not* full accounting: it does not
distinguish a decline that maps to a registered residue class from one that does
not (the closed-set guarantee), nor a genuine delta that is an intentional fix
from one that is an un-adjudicated parity miss.

This module turns the coarse census into the FIVE accounting buckets the "total
accounting, not total ownership" terminal state (Pro P0) requires. Every
amendment johtolause lands in EXACTLY ONE bucket; the five buckets PARTITION the
corpus (their sum equals the amendment-johtolause total). The partition is the
proof surface: there is no "other" bucket, nothing falls off the edge.

The five buckets:

  1. ``grammar_owned_0delta`` — new parser owned it AND produced a byte-identical
     canonical model to the legacy parser. The ownership win.
  2. ``legacy_fallback_registered`` — new parser declined (raised ``OutOfScope``)
     and the generalized decline reason maps to a REGISTERED residue class via
     :func:`~...fallback_residue.classify_decline_reason`. Typed, accounted
     fallback.
  3. ``legacy_fallback_unregistered`` — new parser declined but the reason maps
     to NO registered class. MUST be 0: this is the closed-set guarantee wired
     end-to-end. Any occurrence is an un-accounted decline and a CI failure.
  4. ``genuine_delta_unclassified`` — new parser owned it but produced a
     DIFFERENT model than the legacy parser, and that difference is NOT (yet)
     adjudicated as an intentional correction. The parity-regression frontier.
  5. ``genuine_delta_adjudicated_fix`` — owned, differs from legacy, but the
     difference has been adjudicated as a DELIBERATE correction (the new parser
     is right, the legacy parser was wrong). For v0 there is no adjudication
     ledger, so this is 0; the bucket consults an (initially empty) adjudication
     set keyed by statute id, so a future ledger moves sids from
     ``unclassified`` -> ``adjudicated`` with no code change here.

This is a pure addition: it imports the corpus + parser lazily, changes no
parsing behavior, and is the accounting ledger over the swap boundary.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from lawvm.finland.johtolause.fallback_residue import (
    classify_decline_reason,
    generalize_decline_reason,
)

# ---------------------------------------------------------------------------
# The five accounting buckets, in report order. Every amendment johtolause maps
# to exactly one of these ids.
# ---------------------------------------------------------------------------
CENSUS_ACCOUNTING_BUCKETS: tuple[str, ...] = (
    "grammar_owned_0delta",
    "legacy_fallback_registered",
    "legacy_fallback_unregistered",
    "genuine_delta_unclassified",
    "genuine_delta_adjudicated_fix",
)


# ---------------------------------------------------------------------------
# Adjudication ledger (v0: empty). A future ledger keyed by statute id moves an
# owned genuine-delta clause from ``genuine_delta_unclassified`` into
# ``genuine_delta_adjudicated_fix`` by adding its sid here — no code change in
# the classifier. ``census_accounting`` accepts an override so a test or a real
# ledger can inject its own set without mutating this module-level default.
# ---------------------------------------------------------------------------
FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Pinned baselines. A human bumps these deliberately when the split legitimately
# changes; the CI test fails on any un-bumped drift in the wrong direction.
# Measured live on the full canonical corpus at base 8aa37aee.
# ---------------------------------------------------------------------------
#: Floor on owned-and-byte-identical clauses. Ownership regression fails CI if
#: the live count drops below this.
FI_JOHTOLAUSE_GRAMMAR_OWNED_0DELTA_FLOOR: int = 32922

#: Ceiling on un-adjudicated owned genuine deltas. A NEW parity miss (an owned
#: clause that diverges from the legacy parser and is not adjudicated) pushes
#: this up and fails CI — the same parity guard the coarse census enforces, now
#: wired into the accounting partition.
FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE: int = 37


def _generalize_delta_path(path: str) -> str:
    """Collapse an index-bearing delta path to its shape (``[3]`` -> ``[*]``)."""
    return re.sub(r"\[\d+\]", "[*]", path)


@dataclass(frozen=True)
class CensusAccountingResult:
    """Outcome of a full-accounting census split over the canonical corpus."""

    total_amendment_clauses: int
    #: bucket id -> count. Keys are exactly :data:`CENSUS_ACCOUNTING_BUCKETS`.
    buckets: dict[str, int]
    #: registered residue class_id -> count (refines ``legacy_fallback_registered``).
    legacy_class_counts: dict[str, int]
    #: generalized decline reasons mapping to no registered class (the
    #: ``legacy_fallback_unregistered`` membership; MUST be empty).
    unregistered_reasons: list[str]
    #: generalized genuine-delta shape -> count (refines the genuine-delta buckets).
    delta_shape_counts: dict[str, int]
    #: statute ids currently in ``genuine_delta_unclassified`` (the parity frontier).
    unclassified_delta_sids: list[str] = field(default_factory=list)

    @property
    def partition_total(self) -> int:
        return sum(self.buckets.values())

    def is_partition(self) -> bool:
        """The five buckets sum to the amendment-johtolause total (no leak)."""
        return self.partition_total == self.total_amendment_clauses


def census_accounting(
    limit: int = 0,
    *,
    adjudicated_fixes: frozenset[str] | None = None,
) -> CensusAccountingResult:
    """Split every amendment johtolause into the five accounting buckets.

    Runs the integrated grammar ``parse`` against the legacy ``surface_parse``
    over the full canonical corpus (or the first ``limit`` statute ids) on the
    identical filtered token stream, and classifies each in-scope clause into
    exactly one bucket. Requires the canonical Finlex corpus
    (``LAWVM_CANONICAL_DATA_ROOT``); imports the corpus + parser lazily so
    importing this module stays cheap.

    ``adjudicated_fixes`` overrides the (empty) v0 adjudication ledger: a clause
    that produces a genuine delta and whose sid is in this set lands in
    ``genuine_delta_adjudicated_fix`` instead of ``genuine_delta_unclassified``.
    """
    from farchive import Farchive

    from lawvm.finland.johtolause import surface_parse
    from lawvm.finland.johtolause.grammar import parser as new_parser
    from lawvm.finland.johtolause.grammar.diff import (
        compare_surface_models,
        parse_text_with,
    )
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    if adjudicated_fixes is None:
        adjudicated_fixes = FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0

    store = TransparentCorpusStore(Farchive(_archive_path()))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]

    counts: Counter[str] = Counter()
    legacy_class_counts: Counter[str] = Counter()
    unregistered_reasons: Counter[str] = Counter()
    delta_shape_counts: Counter[str] = Counter()
    unclassified_delta_sids: list[str] = []
    total = 0

    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            johto = get_johtolause(xb) or ""
        except Exception:
            continue
        if not johto:
            continue

        # Denominator: an amendment johtolause = OLD parser yields >= 1 verb group.
        try:
            old_model = parse_text_with(johto, surface_parse.parse)
        except Exception:
            continue
        if not old_model.verb_groups:
            continue
        total += 1

        # --- bucket 2/3: the new parser declined (clean fail-loud fallback) ----
        try:
            new_model = parse_text_with(johto, new_parser.parse)
        except new_parser.OutOfScope as exc:
            reason = generalize_decline_reason(str(exc))
            cid = classify_decline_reason(reason)
            if cid is None:
                counts["legacy_fallback_unregistered"] += 1
                unregistered_reasons[reason] += 1
            else:
                counts["legacy_fallback_registered"] += 1
                legacy_class_counts[cid] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            # A non-decline crash is a genuine delta (the new parser did not
            # produce a comparable model), not a clean fallback.
            shape = f"<crash:{type(exc).__name__}>"
            delta_shape_counts[shape] += 1
            if sid in adjudicated_fixes:
                counts["genuine_delta_adjudicated_fix"] += 1
            else:
                counts["genuine_delta_unclassified"] += 1
                unclassified_delta_sids.append(sid)
            continue

        # --- bucket 1/4/5: the new parser owned it ----------------------------
        report = compare_surface_models(old_model, new_model)
        if report.equal:
            counts["grammar_owned_0delta"] += 1
        else:
            shape = _generalize_delta_path(report.deltas[0].split(":", 1)[0])
            delta_shape_counts[shape] += 1
            if sid in adjudicated_fixes:
                counts["genuine_delta_adjudicated_fix"] += 1
            else:
                counts["genuine_delta_unclassified"] += 1
                unclassified_delta_sids.append(sid)

    # Materialize every bucket id (Counter omits zero-count keys) so the result
    # always carries the full closed set of five buckets.
    buckets = {b: counts.get(b, 0) for b in CENSUS_ACCOUNTING_BUCKETS}

    return CensusAccountingResult(
        total_amendment_clauses=total,
        buckets=buckets,
        legacy_class_counts=dict(legacy_class_counts),
        unregistered_reasons=sorted(unregistered_reasons),
        delta_shape_counts=dict(delta_shape_counts),
        unclassified_delta_sids=unclassified_delta_sids,
    )


def format_accounting_report(result: CensusAccountingResult) -> str:
    """Render the five-bucket accounting scoreboard as human-readable text."""
    total = result.total_amendment_clauses

    def pct(n: int) -> str:
        return f"{100 * n / total:.2f}%" if total else "n/a"

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("FI JOHTOLAUSE FULL-ACCOUNTING CENSUS (five-bucket partition)")
    lines.append("=" * 72)
    lines.append(f"  total amendment johtolauses     : {total}")
    lines.append("-" * 72)
    for b in CENSUS_ACCOUNTING_BUCKETS:
        n = result.buckets[b]
        lines.append(f"  {b:<32}: {n:6d}  ({pct(n)})")
    lines.append("-" * 72)
    lines.append(
        f"  partition sum                   : {result.partition_total:6d}  "
        f"(== total: {result.is_partition()})"
    )
    lines.append("")

    if result.legacy_class_counts:
        lines.append("-" * 72)
        lines.append("legacy_fallback_registered — by residue class")
        lines.append("-" * 72)
        for cid, n in sorted(
            result.legacy_class_counts.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {n:6d}  {cid}")
        lines.append("")

    if result.delta_shape_counts:
        lines.append("-" * 72)
        lines.append("genuine-delta shapes (refines genuine_delta_* buckets)")
        lines.append("-" * 72)
        for shape, n in sorted(
            result.delta_shape_counts.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {n:6d}  {shape}")
        lines.append("")

    if result.unregistered_reasons:
        lines.append("-" * 72)
        lines.append("!! UNREGISTERED decline reasons (closed-set BREACH) !!")
        lines.append("-" * 72)
        for reason in result.unregistered_reasons:
            lines.append(f"  {reason}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    result = census_accounting(limit=limit)
    print(format_accounting_report(result))
    if not result.is_partition():
        raise SystemExit(
            f"PARTITION VIOLATION: buckets sum to {result.partition_total} "
            f"but total amendment clauses = {result.total_amendment_clauses}"
        )
    if result.buckets["legacy_fallback_unregistered"] != 0:
        raise SystemExit(
            "CLOSED-SET BREACH: legacy_fallback_unregistered != 0 "
            f"({result.buckets['legacy_fallback_unregistered']})"
        )


if __name__ == "__main__":
    main()
