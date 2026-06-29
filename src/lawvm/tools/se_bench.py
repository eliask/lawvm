"""Sweden unified bench comparator (UNIFIED_BENCH_CONTRACT.md).

Maps a Sweden :func:`scan_se_official_replay_act` summary dict onto a contract
:class:`BenchUnitResult` so the SE frontend participates in the cross-jurisdiction
bench contract alongside ``fi``, ``uk``, ``nz``, ``ee``, and ``us``. SE has no
separate continuous text-similarity axis (its replay-vs-oracle comparison is
already a normalised section-text equality check inside
:func:`check_se_official_replay`), so ``text_err`` is left unattempted —
identical to the EE adapter's choice for the same reason.

Mapping contract:

* ``outcome == "replay_ok"`` AND ``target_count > 0``: ``SCORED``.
  ``structural_err = 1 - match_count / target_count`` (``0`` = full replay-vs-
  oracle agreement on every amending act's declared targets).
  ``residue_buckets`` carries the SE three-bucket split counts
  (``genuine_match``, ``oracle_version_mismatch``, ``genuine_mismatch``,
  ``unknown``) so the residue reconciles: each non-match row falls in exactly
  one of the three non-genuine buckets and contributes one structural unit to
  ``structural_err``. Sampling-cell mismatched targets surface as ``witnesses``
  (capped) for triage.
* ``outcome == "replay_ok"`` AND ``target_count == 0``: ``NO_TRUTH`` — there
  is no oracle to compare against (the amending act declared no targets that
  resolved).
* ``outcome == "older_base_required"``: ``SOURCE_UNAVAILABLE`` — the act's
  replay base requires an older base surface that the archived chain has not
  yet reconstructed. This is a manual-compilation frontier state (the source
  does not deterministically specify the replay), not a replay failure:
  ``check_se_official_replay` itself classifies it without raising, and the
  residue carries the recovery-mode signal so the SEAggregateReporter can
  surface how much of the corpus is in this non-scored-but-flagged lane.
* ``outcome == "error"``: ``CRASH`` with the cached ``error_type`` /
  ``error_detail`` / ``clause_text`` as witnesses — a genuine failure that
  fails the bench. iter4 W1 (C2): ``apply_raise`` (a genuine apply-fold raise
  caught by ``check_se_official_replay``'s try/except — silent-failure review
  HIGH #3) is now bucketed as top-level ``outcome == "error"`` by
  :func:`scan_se_official_replay_act`'s explicit ``SE_REPLAY_OUTCOME_APPLY_RAISE``
  branch (pre-fix it collapsed to ``"older_base_required"`` and was mis-routed
  here as :class:`BenchStatus.SOURCE_UNAVAILABLE` — the §2.9 worst-class silent
  failure). The witness tuple carries ``clause_text`` (the §1.10 source-text
  snippet / exception string) so the diagnostic is visible without re-running
  extraction.
"""

from __future__ import annotations

from typing import Any, Mapping

from lawvm.core.bench_comparator_registry import register_bench_comparator
from lawvm.core.bench_contract import BenchStatus, BenchUnitResult


def se_bench_unit_result(summary: Mapping[str, Any]) -> BenchUnitResult:
    """Map a Sweden ``scan_se_official_replay_act`` summary onto a contract unit.

    Structural axis convention (matches the SE three-bucket split):
    ``structural_err`` measures genuine LawVM-vs-oracle disagreements — rows
    that the comparator classifies into the ``genuine_mismatch`` bucket (the
    replay text genuinely disagrees with both the cached official oracle AND
    the current-surface consolidation; includes the
    ``official_oracle_match_current_surface_drift`` case where replay matched
    the official-act oracle but the consolidation is contemporaneous or older,
    exposing a real current-surface text drift). The
    ``oracle_version_mismatch`` and ``unknown`` buckets are non-error
    classifications (the replay was correct; the consolidated oracle is just
    a strictly-later time-point version, or the stamp was untrustworthy) and
    so they do NOT contribute to ``structural_err`` nor to the typed residue —
    they surface in the SE three-bucket summary already and the bench
    aggregate's headline metric stays reserved for genuine correctness gaps.

    ``text_err = None`` — SE has no separate continuous text-similarity axis
    (replay-vs-oracle comparison is already a normalized section-text
    equality check inside :func:`check_se_official_replay`), mirroring the
    EE adapter's choice for the same reason.
    """
    unit_id = str(summary.get("amending_sfs_id") or "")
    outcome = str(summary.get("outcome") or "error")
    target_count = int(summary.get("target_count") or 0)
    genuine_mismatch = int(summary.get("bucket_genuine_mismatch_count") or 0)

    if outcome == "replay_ok":
        if target_count <= 0:
            # Reproducible replay but no oracle targets — nothing to score.
            return BenchUnitResult(unit_id=unit_id, bench_unit_status=BenchStatus.NO_TRUTH)
        structural_err = genuine_mismatch / target_count
        residue: dict[str, int] = {}
        if genuine_mismatch:
            residue["genuine_mismatch"] = genuine_mismatch
        # Non-error three-bucket signals (oracle_version_mismatch and unknown)
        # do NOT enter the typed residue: they are correctly replayed rows
        # where the consolidated oracle disagrees but LawVM was right.
        return BenchUnitResult(
            unit_id=unit_id,
            bench_unit_status=BenchStatus.SCORED,
            structural_err=structural_err,
            text_err=None,
            residue_buckets=residue,
        )
    if outcome == "older_base_required":
        # Manual-compilation frontier state — the source does not
        # deterministically specify the historical replay base for this act;
        # the SE analyze path surfaces this as a typed recovery-strategy
        # field rather than raising. Non-scored exclusion.
        return BenchUnitResult(
            unit_id=unit_id,
            bench_unit_status=BenchStatus.SOURCE_UNAVAILABLE,
            residue_buckets={
                "recovery_mode_older_base_required": 1,
            },
        )
    # ``outcome == "error"`` (or any unrecognised outcome) — a genuine
    # failure. Cache the typed error class/detail as witness for triage
    # rather than letting the crash disappear into the aggregate. iter4 W1
    # (C2, silent-failure review HIGH #3): apply_raise is now bucketed as
    # top-level ``outcome == "error"`` by :func:`scan_se_official_replay_act`'s
    # explicit ``SE_REPLAY_OUTCOME_APPLY_RAISE`` branch (pre-fix it fell through
    # to the ``"older_base_required"`` collapse and was mis-routed here as
    # :class:`BenchStatus.SOURCE_UNAVAILABLE` — the §2.9 worst-class silent
    # failure). The ``clause_text`` field carries the §1.10 source-text snippet
    # (or the apply-raise exception string truncated to ~400 chars when no
    # per-op source_clause_extract is in scope — deferred per task #50; see
    # the iter4 W1 M2 comment-clarification at ``sweden/fetch.py:3604+``);
    # ``se_bench.py``'s CRASH witnesses pull it as a third triage surface so
    # the diagnostic snippet is visible without re-running extraction.
    witnesses = tuple(
        str(part)
        for part in (
            summary.get("error_type") or "",
            summary.get("error_detail") or "",
            summary.get("clause_text") or "",
        )
        if part
    )
    return BenchUnitResult(
        unit_id=unit_id,
        bench_unit_status=BenchStatus.CRASH,
        witnesses=witnesses,
    )


def _register_se_bench_comparator() -> None:
    register_bench_comparator("se", se_bench_unit_result)


_register_se_bench_comparator()

