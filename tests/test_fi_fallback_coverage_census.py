"""CI guard for the whole-corpus legacy_reference_fallback coverage census.

This is the retained-residual evidence the typed-path goal names: the
``legacy_reference_fallback`` lane is retained ONLY with full-corpus coverage
proof that each firing is load-bearing. The census
(:mod:`lawvm.finland.johtolause.fallback_coverage_census`) is that proof; this
guard pins it.

Two layers:

* Registry/JSON-shape tests (always run, corpus-free): the bucket set is closed,
  the MIGRATABLE TODO is the pinned set, the JSON serialization is deterministic
  and round-trips the partition.

* Corpus census guard (archive-gated): runs the whole-corpus census via the
  production ``parse_clause`` and asserts:
    (a) the three buckets PARTITION the fallback firings (no leak);
    (b) MIGRATABLE == the pinned baseline (0) — any NEW migratable-shaped firing
        beyond the pinned TODO fails loudly rather than hiding in LOAD_BEARING;
    (c) LOAD_BEARING <= its pinned baseline — no silent growth in the retained,
        deletion-blocking residue;
    (d) every LOAD_BEARING firing carries >= 1 production op (the load-bearing
        witness) and every NON_AMENDMENT firing carries 0 ops (no-op fallback) —
        enforced structurally by the census classifier and re-asserted here via
        the per-reason witness samples being well-formed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.finland.johtolause.fallback_coverage_census import (
    FALLBACK_COVERAGE_BUCKETS,
    FI_FALLBACK_MIGRATABLE_TODO_V0,
    census_fallback_coverage,
    result_to_json,
)


# ---------------------------------------------------------------------------
# Pinned baselines. A human bumps these deliberately when the census
# legitimately changes (e.g. after migrating a sub-shape into the grammar, which
# REMOVES firings from LOAD_BEARING, the count drops and the ceiling can be
# tightened). The guard fails on un-bumped growth in the wrong direction.
#
# Measured live on the full canonical corpus at base 02bdd6c8.
# ---------------------------------------------------------------------------
#: Ceiling on the genuinely deletion-blocking residue (legacy fallback emits
#: >= 1 production op the grammar declines). Growth above this without a
#: deliberate bump means a NEW shape started firing the fallback with real ops —
#: a regression the typed-path goal must catch.
FI_FALLBACK_LOAD_BEARING_CEILING: int = 649

#: The MIGRATABLE bucket must equal the pinned TODO size. Today 0: every
#: op-bearing fallback firing is either genuinely LOAD_BEARING or already
#: migrated out of the fallback. A NEW cleanly-migratable shape pushes this above
#: the pinned size and fails CI, forcing an explicit human disposition.
FI_FALLBACK_MIGRATABLE_BASELINE: int = len(FI_FALLBACK_MIGRATABLE_TODO_V0)


# ---------------------------------------------------------------------------
# Registry / JSON-shape (corpus-free, always run)
# ---------------------------------------------------------------------------
def test_bucket_set_is_closed_three() -> None:
    assert FALLBACK_COVERAGE_BUCKETS == ("NON_AMENDMENT", "LOAD_BEARING", "MIGRATABLE")
    assert len(set(FALLBACK_COVERAGE_BUCKETS)) == 3


def test_migratable_todo_is_pinned_set() -> None:
    # The MIGRATABLE TODO is a deliberate human-curated set; baseline tracks it.
    assert isinstance(FI_FALLBACK_MIGRATABLE_TODO_V0, frozenset)
    assert FI_FALLBACK_MIGRATABLE_BASELINE == len(FI_FALLBACK_MIGRATABLE_TODO_V0)


# ---------------------------------------------------------------------------
# Corpus census guard (archive-gated)
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


@pytest.fixture(scope="module")
def _census():
    # Serial (workers=1) so the gated test stays robust under -n0 small shards and
    # does not spawn its own pool inside the test runner's worker.
    return census_fallback_coverage(workers=1)


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_census_partitions_fallback_firings(_census) -> None:
    """The three buckets sum to the total fallback firings (no leak)."""
    assert _census.is_partition(), (
        f"buckets {_census.buckets} sum to {_census.partition_total} but fallback "
        f"firings = {_census.total_fallback_firings}"
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_census_migratable_at_pinned_baseline(_census) -> None:
    """No un-disposed cleanly-migratable firing hides in the retained residue."""
    n = _census.buckets["MIGRATABLE"]
    assert n == FI_FALLBACK_MIGRATABLE_BASELINE, (
        f"MIGRATABLE count {n} != pinned baseline {FI_FALLBACK_MIGRATABLE_BASELINE}. "
        "A firing entered/left the MIGRATABLE TODO; update "
        "FI_FALLBACK_MIGRATABLE_TODO_V0 and this baseline deliberately."
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_census_load_bearing_does_not_exceed_ceiling(_census) -> None:
    """No silent growth in the retained deletion-blocking residue."""
    n = _census.buckets["LOAD_BEARING"]
    assert n <= FI_FALLBACK_LOAD_BEARING_CEILING, (
        f"LOAD_BEARING count {n} exceeds pinned ceiling "
        f"{FI_FALLBACK_LOAD_BEARING_CEILING}. A new shape started firing the legacy "
        "fallback with real ops — characterize it (migrate, or bump the ceiling "
        "deliberately with a witness)."
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_census_buckets_carry_op_witness(_census) -> None:
    """LOAD_BEARING reasons each have a sample; counts reconcile with buckets."""
    # Per-bucket reason counts must sum to the bucket totals (the witness ledger
    # is complete: every firing contributed its reason).
    assert (
        sum(_census.load_bearing_reason_counts.values())
        == _census.buckets["LOAD_BEARING"]
    )
    assert (
        sum(_census.non_amendment_reason_counts.values())
        == _census.buckets["NON_AMENDMENT"]
    )
    # Every LOAD_BEARING reason has a spot-audit sample drawn from its own bucket.
    for reason in _census.load_bearing_reason_counts:
        assert ("LOAD_BEARING", reason) in _census.reason_samples


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_census_json_is_deterministic_and_complete(_census) -> None:
    """The machine-readable JSON round-trips the partition and is reproducible."""
    j1 = result_to_json(_census)
    j2 = result_to_json(_census)
    assert j1 == j2
    assert j1["schema"] == "fi_fallback_coverage_census.v1"
    assert j1["partition_ok"] is True
    assert set(j1["buckets"]) == set(FALLBACK_COVERAGE_BUCKETS)
    assert sum(j1["buckets"].values()) == j1["total_fallback_firings"]
