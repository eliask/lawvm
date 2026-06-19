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
    FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0,
    FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0,
    FI_JOHTOLAUSE_GENUINE_DELTA_UNCLASSIFIED_BASELINE,
    FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0,
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


def test_adjudication_ledger_holds_the_33_corrections() -> None:
    # The 2026-06-16 adjudication round moved 33 of the 37 genuine deltas into
    # the ledger as ``adjudicated_parser_correction`` (NEW right, OLD silently
    # dropped content). Of the other 4: the 3 parity_bugs {1995/551, 1991/1055,
    # 1989/117} were FIXED grammar-side (now byte-identical), and 2002/723 was
    # adjudicated as a replay-neutral witness-span normalization (its own class).
    assert len(FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0) == 33
    # The 3 fixed parity_bugs belong in NO classification set (they are 0-delta).
    fixed_parity = {"1995/551", "1991/1055", "1989/117"}
    assert FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0.isdisjoint(fixed_parity)
    assert FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0.isdisjoint(fixed_parity)
    # 2002/723 is witness-span-normalized, NOT a parser-correction.
    assert FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0 == {"2002/723"}
    assert "2002/723" not in FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0
    # The both-parser drop-recovery round added 12 NEW-better recoveries (6 nimike
    # + 4 labelled-subheading + 2 nojalla-authority), a class of its own.
    assert len(FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0) == 12
    # The insertion-recovery round added 7 NEW-better recoveries where the legacy
    # parser flattened/dropped a ``lisätään ... uusi X`` insertion to a bare ref
    # (or dropped the LISATA group); NEW emits the correct SurfaceInsertion.
    assert len(FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0) == 7
    # The four adjudication sets are mutually disjoint (no sid double-counted).
    assert FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0.isdisjoint(
        FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0
    )
    assert FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0.isdisjoint(
        FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0
    )
    assert FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0.isdisjoint(
        FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0
    )
    assert FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0.isdisjoint(
        FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0
    )
    assert FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0.isdisjoint(
        FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0
    )


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
@pytest.mark.slow
def test_result_has_exactly_the_five_buckets(_result) -> None:
    assert set(_result.buckets) == set(CENSUS_ACCOUNTING_BUCKETS)


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
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
@pytest.mark.slow
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
@pytest.mark.slow
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
@pytest.mark.slow
def test_genuine_delta_adjudicated_fix_count(_result) -> None:
    """The adjudicated bucket holds the corrections + witness-span + drop-recovery
    + insertion-recovery sets that still diverge. Live = 50: 32 of the 33 parser
    corrections (2002/375 converged to byte-identical via the appendix recovery's
    OLD-side fix, so it no longer diverges) + 1 witness-span (2002/723) + 12
    both-parser drop recoveries + 5 insertion recoveries.
    """
    set_total = (
        len(FI_JOHTOLAUSE_GENUINE_DELTA_ADJUDICATED_FIXES_V0)
        + len(FI_JOHTOLAUSE_GENUINE_DELTA_WITNESS_SPAN_NORMALIZED_V0)
        + len(FI_JOHTOLAUSE_GENUINE_DELTA_DROP_RECOVERY_V0)
        + len(FI_JOHTOLAUSE_GENUINE_DELTA_INSERTION_RECOVERY_V0)
    )
    assert set_total == 51  # 33 + 1 + 12 + 5
    assert _result.buckets["genuine_delta_adjudicated_fix"] == 50, (
        "genuine_delta_adjudicated_fix should be 50 (set total 51 minus 2002/375, "
        "which converged to byte-identical). Got "
        f"{_result.buckets['genuine_delta_adjudicated_fix']}."
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
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
@pytest.mark.slow
def test_report_renders(_result) -> None:
    report = format_accounting_report(_result)
    assert "FULL-ACCOUNTING CENSUS" in report
    assert "partition sum" in report


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_witness_span_sid_is_structurally_equal_not_byte_equal() -> None:
    """Evidence + regression guard for the witness_span_normalized class.

    2002/723 differs OLD-vs-NEW only in witness ``source_span`` endpoints: the
    exact comparator reports a delta, but the structural comparator (which
    tolerates witness-span-only deltas) reports equal. If a future change makes
    it STRUCTURALLY diverge, this fails and the witness-span classification must
    be re-examined.
    """
    from farchive import Farchive

    from lawvm.finland.johtolause import surface_parse
    from lawvm.finland.johtolause.grammar import parser as new_parser
    from lawvm.finland.johtolause.grammar.diff import (
        compare_surface_models,
        compare_surface_models_structural,
        parse_text_with,
    )
    from lawvm.finland.metadata import get_johtolause
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path()))
    xb = store.read_source("2002/723") or store.read_amendment("2002/723")
    assert xb is not None
    johto = get_johtolause(xb)
    old = parse_text_with(johto, surface_parse.parse)
    new = parse_text_with(johto, new_parser.parse)

    exact = compare_surface_models(old, new)
    assert not exact.equal, "expected a witness-span delta; got byte-identical"
    assert all(".witness.source_span" in d for d in exact.deltas), (
        f"2002/723 has a NON-witness-span delta: {exact.deltas}"
    )
    assert compare_surface_models_structural(old, new).equal
