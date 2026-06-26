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
#
# Single source of truth after the §1.9 NOReplayStatus closure: the typed
# ``NO_BENCH_SOURCE_UNAVAILABLE_STATUSES`` frozenset in
# ``lawvm.norway.sources`` owns it (verify.py + inventory.py + commencement.py
# + no_bench.py all read the same set). Previously held a parallel raw-string
# frozenset here that escaped the StrEnum closure.
from lawvm.norway.sources import (
    NO_BENCH_SOURCE_UNAVAILABLE_STATUSES as _NO_BENCH_SOURCE_UNAVAILABLE_STATUSES,
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

    # sparse_indexed_history: the replayed body diverges from the current
    # consolidated law with so many primary divergences (≥50 or ≥15 set in
    # _infer_no_source_signal) against ≤1 indexed amendment + ≤5 replay ops,
    # that the primary divergences are functionally an acquisition ceiling
    # — the law's full amendment history has not been indexed/acquired —
    # not an algorithm surprise. A saturated-1.0 SCORED (structural_err =
    # min(1.0, large_count / small_n_replay_sections)) misrepresents that
    # ceiling as a replay bug. Surface as SOURCE_UNAVAILABLE (a documented
    # data ceiling per notes/NORWAY_LAWVM_STATUS.md) with the source-signal
    # name and the upstream-count triad as the witness, so a user can filter
    # or inspect without re-running verify.
    if result.source_signal == "sparse_indexed_history":
        return BenchUnitResult(
            unit_id=unit_id,
            status=BenchStatus.SOURCE_UNAVAILABLE,
            witnesses=(
                "sparse_indexed_history "
                f"(divergences={int(result.divergence_count or 0)}, "
                f"indexed_amendments={int(result.indexed_amendment_count or 0)}, "
                f"replay_ops={int(result.replay_op_count or 0)})",
            ),
        )

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


def _resolve_runs_dir(args: object) -> "Path":
    """Return the directory holding per-statute NO bench run CSVs.

    Honors ``args.runs_path`` when it points at a directory (used by the
    smoke test to isolate runs to tmp_path); otherwise falls back to the
    canonical ``data/norway_bench_runs/`` at the repository root.
    """
    runs_path = getattr(args, "runs_path", None)
    if runs_path is not None and Path(runs_path).is_dir():
        return Path(runs_path)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data" / "norway_bench_runs"


def _load_run_accuracies(csv_path: "Path") -> "dict[str, float] | None":
    """Parse a per-statute NO bench run CSV into ``{unit_id: headline_accuracy}``.

    Returns ``None`` (rather than an empty dict) when the file does not
    exist — the caller distinguishes "no such labelled run" from "an empty
    run", the former being a typed error.

    Skips rows whose status is not ``SCORED`` (CRASH / NON_SCORED statuses
    carry no accuracy and would confuse the regression comparator's
    unit-id-matching semantics — a unit that crashed in one run but scored
    in another is *interesting*, not a regression to silently omit; we keep
    it out of both sides so the comparator sees missing keys symmetrically,
    leaving the missing-key case for a future "newly broken" branch).
    """
    import csv as _csv

    if not csv_path.exists():
        return None
    out: dict[str, float] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            status = row.get("status", "")
            if status != "scored":
                continue
            try:
                acc = float(row["headline_accuracy"])
            except (KeyError, ValueError):
                # A row that lacks the accuracy column or carries a
                # non-numeric value is silently dropped — a regression guard
                # that crashes on a malformed CSV is worse than one that
                # misses one row. Surface the issue via stderr if you want it
                # raised; for now skip.
                continue
            out[row["unit_id"]] = acc
    return out


def _most_recent_labels(runs_dir: "Path", *, limit: int = 2) -> list[str]:
    """Return the ``limit`` most recent labelled runs (by mtime), newest first.

    Determined by the per-statute CSV's modification time, not the label
    column. Empty list if the runs dir does not exist or contains no CSVs.
    """
    if not runs_dir.exists():
        return []
    labelled = [
        (path.stat().st_mtime, path)
        for path in runs_dir.glob("*.csv")
        if path.is_file()
    ]
    labelled.sort(key=lambda pair: pair[0], reverse=True)
    return [path.stem for _, path in labelled[:limit]]


def _render_regressions_from_runs(args: object) -> int:
    """Render a regression report comparing two labelled runs.

    Two entry modes (set on the unified bench parser):

    - ``--compare LABEL_A LABEL_B`` — explicit pair.
    - ``--regressions`` — implicitly compares the two most recent labelled
      runs in ``data/norway_bench_runs/`` (by mtime). Requires at least two
      labelled runs to have been persisted.

    Both modes load the per-statute CSVs written by
    :func:`_persist_per_statute_results` and feed the ``unit_id ->
    headline_accuracy`` maps through
    :func:`lawvm.core.bench_aggregate.find_regressions` (the shared
    regression-guard API across jurisdictions).
    """
    import sys

    from lawvm.core.bench_aggregate import find_regressions

    runs_dir = _resolve_runs_dir(args)

    compare = getattr(args, "compare", None)
    if compare:
        if len(compare) != 2:
            print(
                "Norway bench --compare requires exactly two labels (LABEL_A LABEL_B)",
                file=sys.stderr,
            )
            return 2
        label_a, label_b = compare
    else:
        recent = _most_recent_labels(runs_dir, limit=2)
        if len(recent) < 2:
            print(
                f"Norway bench --regressions requires at least two labelled runs in "
                f"{runs_dir}/; found {len(recent)}. Run `lawvm -j no bench --label "
                "<tag>` twice with different tags to persist two comparable runs first.",
                file=sys.stderr,
            )
            return 2
        label_a, label_b = recent[1], recent[0]  # older vs newer (worst-delta-first)

    path_a = runs_dir / f"{label_a}.csv"
    path_b = runs_dir / f"{label_b}.csv"
    accuracies_a = _load_run_accuracies(path_a)
    accuracies_b = _load_run_accuracies(path_b)
    if accuracies_a is None:
        print(
            f"Norway bench run not found: {path_a}. "
            "Persist it first via `lawvm -j no bench --label <tag>`.",
            file=sys.stderr,
        )
        return 2
    if accuracies_b is None:
        print(
            f"Norway bench run not found: {path_b}. "
            "Persist it first via `lawvm -j no bench --label <tag>`.",
            file=sys.stderr,
        )
        return 2

    regressions = find_regressions(accuracies_a, accuracies_b, tolerance=0.001)
    print(f"=== Norway bench regressions: {label_a} -> {label_b} ===")
    print(f"  Compared units: {len(accuracies_b)} current vs {len(accuracies_a)} previous")
    print(f"  Common (scored in both): {len(set(accuracies_a) & set(accuracies_b))}")
    print(f"  Regressions (accuracy dropped > 0.001 tolerance): {len(regressions)}")
    if regressions:
        print()
        print("  unit_id                          delta      prev_acc   curr_acc")
        for r in regressions:
            print(
                f"  {r.unit_id:<33} {r.delta:+.4f}   {r.previous_accuracy:.4f}     {r.current_accuracy:.4f}"
            )
    return 0


def _render_history_or_show_or_regressions(args: object) -> int:
    """Dispatch the read-only ``--show`` / ``--history`` / ``--regressions`` flags.

    All three short-circuit the sweep; this helper picks the one the args
    namespace asks for and routes to its renderer. ``--compare`` overrides
    ``--regressions`` (the parser will not set both simultaneously, but the
    explicit pair takes precedence if both somehow arrive).
    """
    if getattr(args, "show", None):
        return _render_show_label(args)
    if getattr(args, "history", False):
        return _render_history(args)
    return _render_regressions_from_runs(args)


def _render_show_label(args: object) -> int:
    """Render the worst performers from one labelled run.

    Loads ``data/norway_bench_runs/<label>.csv`` (populated by
    :func:`_persist_per_statute_results`) and prints the top-N statutes by
    worst headline_error first. ``--top`` defaults to 20; ``--top 5``
    shows five.

    Mirrors EE's ``_show_run``: read-only, no re-run, no sweep.
    """
    import csv as _csv
    import sys

    label = getattr(args, "show", None)
    if not label:
        print("Norway bench --show requires a LABEL argument", file=sys.stderr)
        return 2
    runs_dir = _resolve_runs_dir(args)
    csv_path = runs_dir / f"{label}.csv"
    if not csv_path.exists():
        print(
            f"Norway bench run not found: {csv_path}. "
            "Persist it first via `lawvm -j no bench --label <tag>`.",
            file=sys.stderr,
        )
        return 2
    top = getattr(args, "top", 20) or 20
    try:
        top = max(1, int(top))
    except (TypeError, ValueError):
        top = 20

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    # Sort only SCORED rows by headline_error descending — non-scored
    # statuses (CRASH / SOURCE_UNAVAILABLE / NO_TRUTH) don't carry an error
    # and would surface as zero / blank otherwise. Stick them at the end
    # in their input order.
    scored = [r for r in rows if r.get("status") == "scored"]
    other = [r for r in rows if r.get("status") != "scored"]
    scored.sort(key=lambda r: -(float(r.get("headline_error") or "0")))

    print(f"=== Norway bench show: {label} ===")
    print(f"  Total rows: {len(rows)} (scored: {len(scored)}; non-scored: {len(other)})")
    print(f"  Top {min(top, len(scored))} worst by headline_error:")
    print()
    print(f"  {'unit_id':<33} {'struct_err':>10}  {'headline':>8}  {'residue':>7}  residue_breakdown")
    for r in scored[:top]:
        residue = int(r.get("residue_total") or 0)
        print(
            f"  {r['unit_id']:<33} "
            f"{float(r.get('structural_err') or '0'):>10.4f}  "
            f"{float(r.get('headline_error') or '0'):>8.4f}  "
            f"{residue:>7}  {r.get('residue_buckets') or ''}"
        )
    if other and top > len(scored):
        # Show non-scored statuses only when --top exceeds scored count,
        # so a default run doesn't drown the worst-N report in non-regressed
        # source-blockers.
        print()
        print(f"  Non-scored (next {min(top - len(scored), len(other))} of {len(other)}):")
        for r in other[: top - len(scored)]:
            print(
                f"  {r['unit_id']:<33} status={r.get('status', '?'):<15} "
                f"witnesses={r.get('witnesses') or ''}"
            )
    return 0


def _render_history(args: object) -> int:
    """Render the trajectory of past labelled runs.

    Loads ``data/norway_bench_history.csv`` (populated by
    :func:`_persist_history`) and prints one row per past run: timestamp,
    label, mean_score, n_statutes, distribution buckets. Chronological by
    append order (the CSV is append-only).

    Mirrors the FI ``--history`` flag's intent: read-only trend across all
    labelled runs, no re-run, no sweep.
    """
    import sys

    from lawvm.core.bench_aggregate import load_history

    history_path = getattr(args, "history_path", None)
    if history_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        history_path = repo_root / "data" / "norway_bench_history.csv"
    else:
        history_path = Path(history_path)

    rows = load_history(Path(history_path))
    if not rows:
        print(
            f"Norway bench history is empty at {history_path}. "
            "Run `lawvm -j no bench --label <tag>` to persist the first row.",
            file=sys.stderr,
        )
        return 0

    print(f"=== Norway bench history: {history_path} ===")
    print(f"  Runs: {len(rows)} (chronological by append order)")
    print()
    print(
        f"  {'timestamp':<25} {'label':<25} {'mean':>6} {'n':>4} "
        f"{'perfect':>7} {'≥99%':>5} {'≥95%':>5} {'<90%':>5}"
    )
    for r in rows:
        try:
            mean = float(r.get("mean_score") or "nan")
        except ValueError:
            mean = float("nan")
        try:
            n = int(r.get("n_statutes") or 0)
        except ValueError:
            n = 0

        print(
            f"  {r.get('timestamp', '?'):<25} "
            f"{r.get('label', '?'):<25} "
            f"{mean:>6.4f} {n:>4} "
            f"{int(r.get('n_perfect') or 0):>7} {int(r.get('n_above_99') or 0):>5} "
            f"{int(r.get('n_above_95') or 0):>5} {int(r.get('n_below_90') or 0):>5}"
        )
    return 0


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

    # Regression-guard arbitration: --regressions, --compare, --show, and
    # --history all short-circuit the sweep. The bench parser registers each
    # flag; without this short-circuit, the sweep would run + persist another
    # labelled run before ever reading either flag — wasting minutes on an
    # obvious-mode command.
    if (
        getattr(args, "regressions", False)
        or getattr(args, "compare", None)
        or getattr(args, "show", None)
        or getattr(args, "history", False)
    ):
        return _render_history_or_show_or_regressions(args)

    from lawvm.core.bench_aggregate import render_summary
    from lawvm.norway.sources import resolve_no_source_path

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

    # --parallel N defaults to a small worker pool (mirrors EE bench). The
    # default is conservative (min(8, cpu_count)) and gated by the user
    # passing ``--parallel 1`` to force sequential: useful for debugging and
    # for environments where fork() is unavailable (e.g. macOS-from-thread).
    par = getattr(args, "parallel", None)
    if par is None:
        import os

        workers = min(8, os.cpu_count() or 4)
    else:
        workers = max(1, int(par))

    results = _run_bench_sweep(rows, data_dir, workers)

    summary = render_summary(results, label, jurisdiction="no")
    print("\n".join(summary))
    print()
    print(f"  Corpus     : {corpus_path}")
    print(f"  Rows run   : {len(results)}")
    print(f"  Workers    : {workers}")
    runs_path = _persist_per_statute_results(results, label, getattr(args, "runs_path", None))
    if runs_path is not None:
        print(f"  Run saved  : {runs_path}")
    _persist_history(results, label, getattr(args, "history_path", None))
    return 0


# Module-level worker config (set before spawning ProcessPoolExecutor).
# Mirrors the lawvm.tools.ee_bench pattern: each worker process resolves the
# data_dir from this global so ProcessPoolExecutor batches do not need to
# pickle the (heavy) archive connection per task.
_WORKER_DATA_DIR: "Path | None" = None


# Per-statute result CSV header — one row per ``BenchUnitResult`` from the
# labelled bench run, mirroring lawvm.tools.ee_bench's per-statute CSV at
# data/ee_bench_runs/<label>.csv. Stored under data/norway_bench_runs/<label>.csv.
_NO_BENCH_RUNS_HEADER = (
    "unit_id",
    "status",
    "structural_err",
    "text_err",
    "headline_error",
    "headline_accuracy",
    "residue_total",
    "residue_buckets",
    "witnesses",
)


def _persist_per_statute_results(
    results: list[BenchUnitResult],
    label: str,
    runs_path: "Path | None" = None,
) -> "Path | None":
    """Persist per-statute ``BenchUnitResult`` rows to a labelled CSV.

    Mirrors EE's ``data/ee_bench_runs/<label>.csv`` convention; the CSV lives
    under ``data/norway_bench_runs/<label>.csv`` at the repository root, or
    at the explicit ``runs_path`` override (used by the contract-adapter smoke
    test to isolate the file to tmp_path).

    The CSV is the per-statute basis for a future ``--regressions`` flag —
    reload via :func:`lawvm.core.bench_aggregate.load_history` style and feed
    two labelled runs into :func:`lawvm.core.bench_aggregate.find_regressions`
    to surface units whose accuracy dropped between runs.

    Failure to persist never aborts the run: per-statute results are
    observability, not load-bearing for the SCORED verdict. An ``OSError``
    surfaces as a stderr diagnostic so the bench numbers already printed
    survive.

    Returns the path written (or ``None`` on failure) so the caller can
    surface it in the summary footer.
    """
    import csv as _csv
    import sys as _sys

    if runs_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        runs_dir = repo_root / "data" / "norway_bench_runs"
        target_file = runs_dir / f"{label}.csv"
    else:
        # Explicit ``runs_path``: if it points at a directory, place the file
        # under it as ``<label>.csv``; if it points at a file (the canonical
        # ``--runs-path data/foo.csv`` form), use it verbatim. Either way,
        # ``runs_dir`` is the directory whose ``mkdir`` will succeed.
        runs_dir = runs_path if runs_path.is_dir() else runs_path.parent
        target_file = runs_path if runs_path.suffix == ".csv" else (runs_dir / f"{label}.csv")
    runs_dir.mkdir(parents=True, exist_ok=True)

    try:
        with target_file.open("w", newline="", encoding="utf-8") as f:
            writer = _csv.writer(f)
            writer.writerow(_NO_BENCH_RUNS_HEADER)
            for r in results:
                residue_total = sum(int(v) for v in r.residue_buckets.values())
                residue_repr = ";".join(
                    f"{k}={int(v)}" for k, v in sorted(r.residue_buckets.items())
                )
                witnesses_repr = "|".join(r.witnesses)
                writer.writerow(
                    [
                        r.unit_id,
                        r.status.value,
                        "" if r.structural_err is None else f"{r.structural_err:.6f}",
                        "" if r.text_err is None else f"{r.text_err:.6f}",
                        "" if r.headline_error() is None else f"{r.headline_error():.6f}",
                        "" if r.headline_accuracy() is None else f"{r.headline_accuracy():.6f}",
                        residue_total,
                        residue_repr,
                        witnesses_repr,
                    ]
                )
    except OSError as exc:
        print(
            f"  run save: per-statute CSV at {target_file} failed: {exc}",
            file=_sys.stderr,
        )
        return None
    return target_file


def _persist_history(
    results: list[BenchUnitResult],
    label: str,
    history_path: "Path | None" = None,
) -> None:
    """Append one NO bench run summary row to ``data/norway_bench_history.csv``.

    Mirrors EE's ``data/ee_benchmark_history.csv`` convention; the CSV is
    byte-compatible with the shared ``bench_aggregate.HISTORY_HEADER`` schema
    FI uses. Per-run distribution → ``(timestamp, label, mean, n, perfect,
    above_99, above_95, below_90)`` so trend tracking across labelled runs
    works via ``abstracted = load_history(...)`` and the regression-guard
    API ``bench_aggregate.find_regressions``.

    ``history_path`` is an optional override (used by the contract-adapter
    smoke test to isolate the file to tmp_path); when ``None``, the default
    path ``data/norway_bench_history.csv`` at the repository root is used.

    Failure to append never aborts the run: history persistence is observability,
    not load-bearing for the SCORED verdict. A ValueError at the
    append-history layer surfaces as a stderr diagnostic so the user sees
    something went wrong without losing the bench numbers already printed.
    """
    import datetime as _datetime
    import sys as _sys

    from lawvm.core.bench_aggregate import append_history, compute_distribution

    if history_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        history_path = repo_root / "data" / "norway_bench_history.csv"
    distribution = compute_distribution(results)
    timestamp = (
        _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    try:
        append_history(history_path, timestamp, label, distribution)
    except OSError as exc:
        # Filesystem hiccup (parent not creatable, disk full, ...). Never
        # raise into the CLI surface: the bench numbers printed above stand.
        print(
            f"  history: append to {history_path} failed: {exc}",
            file=_sys.stderr,
        )
        return
    print(f"  History    : {history_path}")


def _no_bench_score_one_worker(row: tuple[str, str, str]) -> BenchUnitResult:
    """Top-level picklable worker for the parallel bench sweep.

    Each worker process opens its own farchive connection (sqlite is safe for
    read-only concurrent access). The verify+map+catch sequence mirrors the
    in-process body so a serial run and a parallel run produce the same
    ``BenchUnitResult`` rows.

    Row: ``(base_id, as_of, note)`` — only the first two are used in scoring;
    ``note`` is preserved so future per-statute reporting can carry it through.
    """
    base_id, as_of, _note = row
    from lawvm.core.bench_comparator_registry import run_bench_comparator
    from lawvm.norway.sources import resolve_no_source_path
    from lawvm.norway.verify import verify_no_against_current

    data_dir = resolve_no_source_path(_WORKER_DATA_DIR) if _WORKER_DATA_DIR is not None else None
    try:
        verify_result = verify_no_against_current(
            base_id,
            as_of=as_of,
            data_dir=data_dir,
        )
        mapped = run_bench_comparator("no", verify_result)
    except Exception as exc:  # noqa: BLE001 — pin the crash with witnesses
        # Surface the per-statute failure as a typed CRASH row so the
        # bench does not silently drop a corpus member; the sync loop
        # never re-raises into the CLI. Non-scored and CRASH stay visible
        # in the partition-by-status breakdown the summary renders.
        mapped = BenchUnitResult(
            unit_id=base_id,
            status=BenchStatus.CRASH,
            witnesses=(f"{type(exc).__name__}: {exc}",),
        )
    return mapped


def _run_bench_sweep(
    rows: list[tuple[str, str, str]],
    data_dir: "Path",
    workers: int,
) -> list[BenchUnitResult]:
    """Run the verify-no sweep over ``rows`` and return ``BenchUnitResult`` per row.

    Sequential when ``workers == 1`` (the debugging / forkless fallback path);
    parallel via ``ProcessPoolExecutor`` otherwise, mirroring
    :func:`lawvm.tools.ee_bench._run_bench`. Order is preserved by index so the
    corpus CSV's row order maps directly to the result order, even though
    :class:`~concurrent.futures.ProcessPoolExecutor` completes tasks
    non-deterministically.
    """
    global _WORKER_DATA_DIR

    import time
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from typing import Optional, cast

    _WORKER_DATA_DIR = data_dir
    try:
        if workers <= 1:
            return [_no_bench_score_one_worker(row) for row in rows]

        total = len(rows)
        results: list[Optional[BenchUnitResult]] = [None] * total
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_no_bench_score_one_worker, row): idx
                for idx, row in enumerate(rows)
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()
                done += 1
                if done % 4 == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    import sys

                    print(
                        f"  [{done}/{total}] {elapsed:.0f}s  {rate:.1f}/s",
                        file=sys.stderr,
                    )
        return cast(list[BenchUnitResult], results)
    finally:
        _WORKER_DATA_DIR = None
