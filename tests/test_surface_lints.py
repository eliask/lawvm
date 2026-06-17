"""Aggregation tests for the surface-lints corpus report tool.

These exercise the tally/aggregation layer on a handful of in-memory synthetic
statute results (no full corpus needed): a statute with lints, a clean statute,
an empty-body statute, and a fail-loud errored statute. The point is to pin the
report's accounting — per-lint_kind, per-severity, node-kind census, the
worst-statute worklist, and the errored bucket.
"""

from __future__ import annotations

from lawvm.tools.surface_lints import (
    _Aggregate,
    _LintResult,
    _aggregate,
    _ordered_severity_items,
    _worst_statutes,
)


def _synthetic_results() -> list[_LintResult]:
    # Statute A: two lint kinds, mixed severity, three node kinds.
    a = _LintResult(
        sid="1/2001",
        lint_kind_counts=(("broken_reference", 2), ("unbound_term", 1)),
        severity_counts=(("blocker", 2), ("warning", 1)),
        node_kind_counts=(("definition", 3), ("reference", 5), ("term_use", 2)),
        n_lints=3,
        n_nodes=10,
        examples=(
            ("broken_reference", "blocker", "reference to repealed 9/1999"),
            ("unbound_term", "warning", "term 'foo' used but never defined"),
        ),
        error=None,
    )
    # Statute B: one lint kind, overlaps A's kinds + node kinds.
    b = _LintResult(
        sid="2/2002",
        lint_kind_counts=(("broken_reference", 1),),
        severity_counts=(("blocker", 1),),
        node_kind_counts=(("reference", 4), ("definition", 1)),
        n_lints=1,
        n_nodes=5,
        examples=(("broken_reference", "blocker", "reference to repealed 8/1998"),),
        error=None,
    )
    # Statute C: clean (no lints) but contributes nodes to the census.
    c = _LintResult(
        sid="3/2003",
        lint_kind_counts=(),
        severity_counts=(),
        node_kind_counts=(("reference", 2),),
        n_lints=0,
        n_nodes=2,
        examples=(),
        error=None,
    )
    # Statute D: fail-loud errored bucket — must NOT contribute to lint/node tallies.
    d = _LintResult(
        sid="4/2004",
        lint_kind_counts=(),
        severity_counts=(),
        node_kind_counts=(),
        n_lints=0,
        n_nodes=0,
        examples=(),
        error="Traceback ...\nValueError: boom",
    )
    return [a, b, c, d]


def test_aggregate_tallies_kinds_severities_and_census() -> None:
    agg = _aggregate(_synthetic_results())
    assert isinstance(agg, _Aggregate)

    assert agg.statutes_scanned == 4
    assert agg.statutes_with_lints == 2  # A and B (C clean, D errored)
    assert agg.total_lints == 4  # 3 + 1
    assert agg.total_nodes == 17  # 10 + 5 + 2 (errored D contributes nothing)

    # per-lint_kind, summed across A + B.
    assert dict(agg.lint_kind_ct) == {"broken_reference": 3, "unbound_term": 1}
    # per-severity, summed.
    assert dict(agg.severity_ct) == {"blocker": 3, "warning": 1}
    # node-kind census, summed across A + B + C (NOT D).
    assert dict(agg.node_kind_ct) == {"reference": 11, "definition": 4, "term_use": 2}


def test_aggregate_records_errored_bucket_not_silently_skipped() -> None:
    agg = _aggregate(_synthetic_results())
    assert len(agg.errored) == 1
    sid, err = agg.errored[0]
    assert sid == "4/2004"
    assert "ValueError: boom" in err


def test_worst_statutes_ranks_by_lint_count_excludes_clean_and_errored() -> None:
    worst = _worst_statutes(_synthetic_results(), top=10)
    sids = [r.sid for r in worst]
    # A (3 lints) before B (1 lint); C (clean) and D (errored) excluded.
    assert sids == ["1/2001", "2/2002"]
    assert worst[0].n_lints == 3
    assert worst[1].n_lints == 1


def test_worst_statutes_respects_top_depth() -> None:
    worst = _worst_statutes(_synthetic_results(), top=1)
    assert [r.sid for r in worst] == ["1/2001"]


def test_ordered_severity_items_canonical_order_with_unknown_appended() -> None:
    import collections

    sev = collections.Counter({"info": 5, "bug": 1, "warning": 3, "weird": 2})
    ordered = _ordered_severity_items(sev)
    # Canonical order: bug, blocker, warning, info (blocker absent -> skipped),
    # then unknown severities sorted alphabetically.
    assert ordered == [("bug", 1), ("warning", 3), ("info", 5), ("weird", 2)]


def test_empty_corpus_aggregates_to_zeroes() -> None:
    agg = _aggregate([])
    assert agg.statutes_scanned == 0
    assert agg.total_lints == 0
    assert agg.total_nodes == 0
    assert dict(agg.lint_kind_ct) == {}
    assert agg.errored == []
    assert _worst_statutes([], top=5) == []
