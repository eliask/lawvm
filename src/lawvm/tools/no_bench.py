"""Norway (NO) unified bench comparator — maps NOVerifyResult → BenchUnitResult.

Norway replays against the live Lovdata consolidated text (``notes/
NORWAY_LAWVM_STATUS.md``), so its bench comparator is consistency-verification
shaped, not oracle-truth shaped. The per-statute verdict comes from
:func:`lawvm.norway.verify.verify_no_against_current`, whose
:class:`~lawvm.norway.verify.NOVerifyResult` already partitions divergences into
typed buckets (``MISMATCH``, ``OPS_MISSING``, ``CONSOLIDATED_MISSING``) and
records the replay-status reasons that explain non-scored outcomes (contingent
commencement, missing base source, unknown effective status).

This module's only job is the **contract mapping** — re-housing
``NOVerifyResult`` onto :class:`lawvm.core.bench_contract.BenchUnitResult` per
``notes/UNIFIED_BENCH_CONTRACT.md``:

- ``CRASH``           — ``verify.error`` set (unexpected exception during replay).
- ``SOURCE_UNAVAILABLE`` — ``replay_status`` ∈
  {``blocked_contingent``, ``blocked_missing_source``, ``blocked_unknown``}.
  Per ``NORWAY_LAWVM_STATUS.md`` §"Limits" these are replay-blocking data
  ceilings (royal-decree commencement unknown, pre-2001 base text not archived,
  effective-status flag not in the known set), not a replay failure.
- ``NO_TRUTH``        — ``replay_status`` == ``no_amendments``. Replay produced
  the unchanged base; there is no oracle divergence to score against the
  replayed-vs-current comparison.
- ``SCORED``          — consistent replay or divergent-replayed (status ==
  ``replayed``). ``structural_err`` is the primary-divergence fraction over the
  replayed statute's section count:

      structural_err = divergence_count / max(1, n_replay_sections)

   ``text_err`` is ``None`` (not attempted) — Norway's verify path normalizes
   text per-section via ``no_compare_*`` presentation rules but does not yet
   compute an aggregate Levenshtein ratio; the structural axis (section
   presence + divergence type) carries the headline.

   ``residue_buckets`` re-keys the per-divergence type counts from
   :attr:`NOVerifyResult.divergence_counts` into the ``structural:<kind>``
   family schema (so the bucket name carries the typed structural-event family
   per the contract §7 example).

Honest scope note — what this comparator does NOT yet do (deferred to a
follow-up bench slice, not silently faked here):

- The structural denominator is the **replayed** section count only (the
  current/oracle tree is not exposed on ``NOVerifyResult``). For an
  oracle-heavy statute where current carries more sections than replay,
  ``structural_err`` is biased toward smaller-than-true values; for a
  replay-heavy statute the bias is reversed. The contract invariant
  (residue reconciles with positive error) is preserved either way — the
  asymmetry affects the magnitude, not the honesty property.
- A ``text_err`` axis (per-section Levenshtein ratio aggregated) is not
  computed yet; ``text_err=None`` is the honest "not attempted" sentinel, not
  a false ``0``.
- The bench CLI dispatch for ``lawvm -j no bench`` is wired separately;
  the registry registration here is what makes the comparator available to
  ``lawvm.core.bench_comparator_registry.run_bench_comparator``.

The contract test pins the invariant: each registered mapping must produce a
:class:`BenchUnitResult` that passes
:func:`lawvm.core.bench_contract.check_residue_reconciliation`.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from lawvm.core.bench_contract import BenchStatus, BenchUnitResult, check_residue_reconciliation
from lawvm.tools.section_keys import extract_ir_sections

if TYPE_CHECKING:
    from lawvm.norway.verify import NOVerifyResult


# Replay statuses that block replay for data-ceiling reasons (per
# notes/NORWAY_LAWVM_STATUS.md §"Limits"). These are *not* replay failures: the
# algorithm did not crash, but the source/effect data is incomplete to the point
# of no comparable truth. SOURCE_UNAVAILABLE, not CRASH.
_NO_BENCH_SOURCE_UNAVAILABLE_STATUSES: frozenset[str] = frozenset(
    {
        "blocked_contingent",  # royal-decree commencement unknown
        "blocked_missing_source",  # base text not archived
        "blocked_unknown",  # effective-status flag not in {dated, immediate, override}
    }
)


def _residue_buckets_for(divergence_counts: Mapping[str, int] | None) -> dict[str, int]:
    """Re-key per-divergence type counts into the ``structural:<kind>`` family schema.

    The contract §7 example uses ``structural:<kind>`` families as the canonical
    residue-bucket naming: each divergence type emitted by Norway's
    :func:`~lawvm.norway.verify.verify_no_against_current` becomes a typed
    structural residue entry so the bucket carries its own provenance rather
    than collapsing to a generic ``section_mismatch`` total.
    """
    if not divergence_counts:
        return {}
    residue: dict[str, int] = {}
    for kind, count in divergence_counts.items():
        if not isinstance(count, int) or count <= 0:
            continue
        residue[f"structural:{kind}"] = count
    return residue


def no_bench_unit_result(result: "NOVerifyResult") -> BenchUnitResult:
    """Map a :class:`~lawvm.norway.verify.NOVerifyResult` onto a
    :class:`BenchUnitResult` per the unified bench contract.

    See the module docstring for the full mapping policy. The function never
    *invents* a SCORED verdict: each non-CRASH outcome is either an honest
    data-ceiling status (SOURCE_UNAVAILABLE / NO_TRUTH) carrying a documented
    reason, or a SCORED verdict whose ``structural_err`` reconciles with its
    ``residue_buckets`` (the §7 honesty invariant enforced by
    :func:`check_residue_reconciliation`).
    """
    unit_id = result.base_id

    # CRASH: an unexpected error escaped verify. The original message is the
    # witness (carried through for triage) — not folded into a misleading 1.0
    # structural_err that would silently inflate the error count.
    if result.error:
        return BenchUnitResult(
            unit_id=unit_id,
            status=BenchStatus.CRASH,
            witnesses=(result.error,),
        )

    status = result.replay_status

    # SOURCE_UNAVAILABLE: replay was prevented by a known data ceiling, not by
    # an algorithm failure. Documented per notes/NORWAY_LAWVM_STATUS.md.
    if status in _NO_BENCH_SOURCE_UNAVAILABLE_STATUSES:
        return BenchUnitResult(
            unit_id=unit_id,
            status=BenchStatus.SOURCE_UNAVAILABLE,
            witnesses=(status,),
        )

    # NO_TRUTH: replay produced the unchanged base (no amendments applied in
    # window). There is no replay-vs-current divergence to score; today's
    # NOVerifyResult does not classify "no_amendments" as SCORED-consistent.
    if status == "no_amendments":
        return BenchUnitResult(unit_id=unit_id, status=BenchStatus.NO_TRUTH)

    # SCORED: the replayed body, with the primary divergences partition
    # already applied by verify_no_against_current.
    replay = result.replay
    replayed_body = None
    if replay is not None and replay.replayed is not None:
        replayed_body = replay.replayed.body

    # Defensive: if SCORED path is reached but the replayed body is missing,
    # that is a structural surprise, not silently SCORED = 0.
    if replayed_body is None:
        return BenchUnitResult(
            unit_id=unit_id,
            status=BenchStatus.CRASH,
            witnesses=("replayed body missing on status=" + str(status),),
        )

    sections = extract_ir_sections(replayed_body)
    n_replay_sections = len(sections)

    # If the replayed body has zero sections, there is no oracle to score
    # against — consistent or not, an empty replay body has no comparable truth.
    if n_replay_sections == 0:
        return BenchUnitResult(unit_id=unit_id, status=BenchStatus.NO_TRUTH)

    divergence_count = int(result.divergence_count or 0)
    residue = _residue_buckets_for(result.divergence_counts)

    # Consistent replay: no divergence, no residue, perfect score. The §7
    # invariant holds (structural_err=0 ↔ residue_buckets empty) by construction.
    if divergence_count == 0:
        scored = BenchUnitResult(
            unit_id=unit_id,
            status=BenchStatus.SCORED,
            structural_err=0.0,
            text_err=None,
            residue_buckets={},
        )
        check_residue_reconciliation(scored)
        return scored

    # Divergent replayed: structural_err is the primary-divergence fraction
    # over the replayed body's section count. Capped at 1.0 (a statute with
    # more divergences than sections is possible when section-pair OR stanza
    # diffs multiply; the cap preserves the [0,1] contract bound).
    structural_err = min(1.0, divergence_count / n_replay_sections)

    scored = BenchUnitResult(
        unit_id=unit_id,
        status=BenchStatus.SCORED,
        structural_err=structural_err,
        text_err=None,
        residue_buckets=residue,
    )
    check_residue_reconciliation(scored)
    return scored


def _register_no_bench_comparator() -> None:
    """Register the ``no`` bench comparator at import time."""
    from lawvm.core.bench_comparator_registry import register_bench_comparator

    register_bench_comparator("no", no_bench_unit_result)


_register_no_bench_comparator()


def _resolve_corpus_path(path_str: str | None) -> "Path":
    """Resolve a corpus path argument, defaulting to the curated starter corpus.

    The default lives under ``data/norway/bench_corpus.csv`` — a small, diverse,
    hand-picked set of base_ids that exercises every bench status branch the
    comparator handles: consistent replay, minor divergence, and saturated
    divergence. Curation rationale is captured in the corpus CSV column
    ``note`` so the selection is auditable, not silent.
    """
    if path_str:
        candidate = Path(path_str)
    else:
        candidate = Path("data/norway/bench_corpus.csv")
    if not candidate.exists():
        raise FileNotFoundError(
            f"Norway bench corpus not found: {candidate}. "
            "Curate one (see data/norway/bench_corpus.csv for the starter shape: "
            "base_id,as_of,note) — never silently run on an empty corpus."
        )
    return candidate


def _load_corpus_rows(path: "Path") -> list[tuple[str, str, str]]:
    """Load the (base_id, as_of, note) rows from a NO bench corpus CSV.

    Deliberately permissive on column order: the schema is a small, single-
    jurisdiction thing, so we key by header name rather than ordinal position —
    rearranging columns in the corpus file does not silently break the sweep.
    """
    import csv

    rows: list[tuple[str, str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"base_id", "as_of"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Norway bench corpus {path} missing required column(s): {sorted(missing)}. "
                "Expected at least `base_id,as_of`; optional `note`."
            )
        for raw in reader:
            base_id = (raw.get("base_id") or "").strip()
            as_of = (raw.get("as_of") or "").strip()
            note = (raw.get("note") or "").strip()
            if not base_id or not as_of:
                # Skip blank rows rather than silently passing None through — but
                # the loop logs nothing, because an empty corpus row would
                # otherwise be a structural surprise worth surfacing as a crash.
                continue
            rows.append((base_id, as_of, note))
    if not rows:
        raise ValueError(
            f"Norway bench corpus {path} has no usable rows after parsing. "
            "Refuse to run an empty sweep — never silently report bench numbers "
            "from a zero-row corpus."
        )
    return rows


def no_bench_main(args) -> int:  # noqa: ANN001 — argparse Namespace, intentionally loose
    """Entry point for ``lawvm -j no bench``.

    Drives a curated corpus CSV (default ``data/norway/bench_corpus.csv``)
    through :func:`~lawvm.norway.verify.verify_no_against_current` for each
    ``(base_id, as_of)`` row, maps each :class:`NOVerifyResult` onto a
    :class:`BenchUnitResult` via :func:`run_bench_comparator` ("no", result),
    enforces residue-reconciliation per row, and renders the unified summary
    via :func:`lawvm.core.bench_aggregate.render_summary`.

    Honest scope note — what this main does NOT do (deferred to a later
    bench slice, not silently faked here):

    - No multi-worker parallelism (the corpus is 8 statutes today; the cost
      is dominated by per-statute verify, which already serializes through
      the farchive SQLite). A ``--parallel`` flag is reserved but not yet
      honoured.
    - No history persistence under ``data/benchmark_history.csv`` — that
      lives in FI/EE's bench-specific state and integrating it across
      jurisdictions is its own cohesive change.
    - No regression-guard comparison against a prior labelled run.
    """

    from lawvm.core.bench_aggregate import render_summary
    from lawvm.core.bench_contract import BenchUnitResult
    from lawvm.core.bench_comparator_registry import run_bench_comparator
    from lawvm.norway.sources import resolve_no_source_path
    from lawvm.norway.verify import verify_no_against_current

    corpus_path = _resolve_corpus_path(getattr(args, "corpus", None))
    # ``args.data_dir`` is optional; passing None lets
    # ``resolve_no_source_path`` walk its documented fallback chain
    # (env vars → ``data/norway.farchive`` → legacy ``data/norway``) rather
    # than us pre-empting with a string that the helper would return as-is
    # (since ``resolve_no_source_path(path=something)`` short-circuits at the
    # top of the function — passing a relative path there bypasses the
    # existence checks below).
    #
    # Honor ``args.db`` when present — the lawvm CLI registers ``--db PATH``
    # as the Norway source-archive selector on every NO subcommand (replay,
    # verify, bench); staying consistent with the rest of the NO CLI lets
    # ``lawvm -j no bench --db /path/to/norway.farchive`` work the way the
    # CLI surface promises.
    data_dir_arg = getattr(args, "data_dir", None) or getattr(args, "db", None)
    # ``resolve_no_source_path`` short-circuits at the top when ``path is not
    # None`` and returns the value untouched (it does not call ``Path.resolve``
    # or check existence for explicit inputs — the env-var/default chain only
    # fires on ``None``). A relative ``--db data/norway.farchive`` from the
    # CLI therefore must be wrapped into a Path here so downstream
    # ``verify_no_against_current`` gets the same Path object it would get from
    # the default fallback (which resolves via the project-root relative
    # DEFAULT_NORWAY_DB constant). Without the wrap, every corpus row CRASHed
    # because the relative path did not exist as a farchive database from
    # the CLI invocation cwd.
    if isinstance(data_dir_arg, str):
        data_dir_arg = Path(data_dir_arg)
    data_dir = resolve_no_source_path(data_dir_arg)
    label = getattr(args, "label", None) or "no-bench"

    rows = _load_corpus_rows(corpus_path)
    results: list[BenchUnitResult] = []
    for base_id, as_of, _note in rows:
        try:
            verify_result = verify_no_against_current(
                base_id,
                as_of=as_of,
                data_dir=data_dir,
            )
            mapped = run_bench_comparator("no", verify_result)
        except Exception as exc:  # noqa: BLE001 — pin the crash with witnesses
            # Surface the per-statute failure as a typed CRASH row so the
            # bench does not silently drop a corpus member; the sync sweep
            # never re-raises into the CLI. Non-scored and CRASH stays visible
            # in the partition-by-status breakdown the summary renders.
            mapped = BenchUnitResult(
                unit_id=base_id,
                status=BenchStatus.CRASH,
                witnesses=(f"{type(exc).__name__}: {exc}",),
            )
        results.append(mapped)

    summary = render_summary(results, label, jurisdiction="no")
    print("\n".join(summary))
    print()
    print(f"  Corpus     : {corpus_path}")
    print(f"  Rows run   : {len(results)}")
    return 0
