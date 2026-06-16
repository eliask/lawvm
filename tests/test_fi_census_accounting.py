"""CI guard for the FI johtolause full-accounting census split (Pro P0 #4).

Two layers, mirroring the fallback-residue registry test:

* Structure tests (always run, corpus-free): the five bucket ids are the closed
  set the module advertises, and the result helpers behave.

* Corpus partition + invariant guard (archive-gated): runs the full-accounting
  census over the canonical corpus and asserts the HARD invariants of the
  "total accounting, not total ownership" terminal state:

    - ``legacy_fallback_unregistered == 0`` — the closed-set guarantee wired
      end-to-end: every decline the new parser surfaces to the fallback boundary
      maps to a registered residue class.
    - the five buckets PARTITION the corpus (sum == amendment-johtolause total).
    - ``genuine_delta_unclassified <= baseline`` — a NEW un-adjudicated parity
      miss fails CI (the parity regression the coarse census guards, now wired
      into the accounting partition).
    - ``grammar_owned_0delta >= floor`` — ownership regression fails CI.

  Skips cleanly when the canonical corpus is not linked, but MUST run and pass
  when it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.finland.johtolause.census_accounting import (
    CENSUS_ACCOUNTING_BUCKETS,
    FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0,
    FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE,
    FI_JOHTOLAUSE_GRAMMAR_OWNED_0DELTA_FLOOR,
    census_accounting,
    format_accounting_report,
)


# ---------------------------------------------------------------------------
# Structure (corpus-free, always run)
# ---------------------------------------------------------------------------
def test_bucket_ids_are_the_closed_five() -> None:
    assert CENSUS_ACCOUNTING_BUCKETS == (
        "grammar_owned_0delta",
        "legacy_fallback_registered",
        "legacy_fallback_unregistered",
        "genuine_delta_unclassified",
        "genuine_delta_adjudicated_fix",
    )
    assert len(set(CENSUS_ACCOUNTING_BUCKETS)) == 5


def test_v0_adjudication_ledger_is_empty() -> None:
    # v0 has no adjudication ledger, so every genuine delta is unclassified.
    assert FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0 == frozenset()


def test_baselines_are_sane() -> None:
    assert FI_JOHTOLAUSE_GRAMMAR_OWNED_0DELTA_FLOOR > 0
    assert FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE >= 0


# ---------------------------------------------------------------------------
# Corpus partition + invariants (archive-gated)
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


@pytest.fixture(scope="module")
def _result():
    return census_accounting()


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_result_has_exactly_the_five_buckets(_result) -> None:
    assert set(_result.buckets) == set(CENSUS_ACCOUNTING_BUCKETS)


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_buckets_partition_the_corpus(_result) -> None:
    """The five buckets sum to the amendment-johtolause total — no leak."""
    assert _result.is_partition(), (
        f"PARTITION VIOLATION: buckets {_result.buckets} sum to "
        f"{_result.partition_total} but total amendment clauses = "
        f"{_result.total_amendment_clauses}"
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_legacy_fallback_unregistered_is_zero(_result) -> None:
    """Closed-set guarantee end-to-end: no un-accounted decline."""
    assert _result.buckets["legacy_fallback_unregistered"] == 0, (
        "legacy_fallback_unregistered != 0 — a decline the new parser surfaced to "
        "the fallback boundary maps to NO registered residue class. Register each "
        "in FI_JOHTOLAUSE_FALLBACK_RESIDUE_CLASSES_V0:\n  "
        + "\n  ".join(_result.unregistered_reasons)
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_genuine_delta_unclassified_within_baseline(_result) -> None:
    """A NEW un-adjudicated parity miss fails CI (parity regression guard)."""
    live = _result.buckets["genuine_delta_unclassified"]
    assert live <= FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE, (
        f"genuine_delta_unclassified {live} exceeds pinned baseline "
        f"{FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE}. A new owned clause "
        "diverges from the legacy parser without adjudication. Either fix the "
        "parity regression, adjudicate it (add its sid to the ledger), or bump "
        "the baseline deliberately. New unclassified sids include:\n  "
        + "\n  ".join(_result.unclassified_delta_sids[:20])
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_grammar_owned_0delta_above_floor(_result) -> None:
    """Ownership regression guard: owned-and-byte-identical must not drop."""
    live = _result.buckets["grammar_owned_0delta"]
    assert live >= FI_JOHTOLAUSE_GRAMMAR_OWNED_0DELTA_FLOOR, (
        f"grammar_owned_0delta {live} dropped below pinned floor "
        f"{FI_JOHTOLAUSE_GRAMMAR_OWNED_0DELTA_FLOOR} — the new parser regressed on "
        "clauses it used to own byte-identically. If intended, lower the floor "
        "deliberately."
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_report_renders(_result) -> None:
    report = format_accounting_report(_result)
    assert "FULL-ACCOUNTING CENSUS" in report
    assert "partition sum" in report
