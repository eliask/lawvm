"""U.S. federal adapter for the witness-attribution spec-discovery ledger.

This is the US sibling of :mod:`lawvm.tools.spec_ledger`'s FI/UK/EE adapters and of
:mod:`lawvm.new_zealand.spec_ledger_adapter`. It reuses that module's
**jurisdiction-neutral core** read-only (``DivergenceRow`` -> ``StatuteLedgerInput`` ->
``build_ledger`` -> ``SpecLedger``) and turns the US dry-run bench surface into a
per-rule discovered-spec ledger.

Frame (see ``notes_internal/SPEC_DISCOVERY_DESIGN.md``): the US dry-run kernel
reverse-engineers the *unwritten rules of US amendment law* by composing a window's
classified amendatory ops onto each before-edition section and comparing against the
published USC after-edition (the oracle). Each per-section outcome is a **named,
falsifiable hypothesis** carried as the section row's ``rule_id``. A
``us_dry_run_section_materialized_text_matches_oracle`` firing CORROBORATES the lowering
hypotheses for that section; a residual row CONTRADICTS them (or, for the OLRC editorial
class, suspects the oracle). Refusals are the surface's own coverage frontier (no
materialization, no oracle outcome) — counted as firings but never as divergences.

This adapter accumulates those per-section outcomes (plus the synthetic missing-source /
sunset residuals the report's north-star derives) into the neutral ledger so the
undifferentiated 23/231 coverage number becomes a ranked table of *specific hypotheses
about US amendment law, with how often the oracle corroborates vs contradicts each one*.

It is read-only and additive: it never edits ``tools/spec_ledger.py`` (no dispatch
authority there — the shared registry dispatches this adapter lazily, and the legacy
``us-spec-ledger`` CLI remains a thin convenience wrapper), never enables actual replay
(``replay_authorized`` stays False throughout), and never mutates the archive. It only
IMPORTS the us_federal bench/dry-run modules.

Run:  python -m lawvm.us_federal.spec_ledger_adapter
      lawvm spec-ledger -j us --corpus-bench
      python -m lawvm.us_federal.spec_ledger_adapter --json
      python -m lawvm.us_federal.spec_ledger_adapter --corpus us/bench/us_bench_corpus.csv
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, cast

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    LedgerAdapter,
    Mode,
    SpecLedger,
    StatuteLedgerInput,
    WitnessDisposition,
    build_ledger,
    disposition_for,
    register_ledger_adapter,
)
from lawvm.tools.spec_ledger_us_catalog import (
    _US_RULE_SPECS,
    US_NON_RULE_LITERALS,
    us_confidence,
)
from lawvm.us_federal.bench import (
    BenchWindow,
    WindowResult,
    evaluate_window,
    load_corpus,
)
from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    DISPOSITION_MISSING_SOURCE,
    DISPOSITION_ORACLE_SUSPECT,
    DISPOSITION_OVER_MATERIALIZED,
    USDryRunReport,
)
from lawvm.us_federal.sources import UsArchiveReader

# Default committed corpus (relative to the repo root), mirroring bench.DEFAULT_CORPUS_PATH.
DEFAULT_CORPUS_PATH = Path("us/bench/us_bench_corpus.csv")

# A loud sentinel for a fired US rule_id with no catalog entry. Mirrors the
# spec_ledger / NZ discipline: absence of a believed-spec is a visible state, never a
# silent pass.
US_LEGACY_UNKNOWN = "legacy_unknown"

# The US dry-run row ``disposition`` string -> neutral witness disposition. The kernel
# already adjudicates which side a residual's gap is on (``DISPOSITION_*``); we map those
# faithfully and never flatter a rule. ``sunset_reversion`` is an EXPLAINED change owned
# by the temporal layer, so it is dispositioned ``oracle_suspect`` (a finding about the
# source/window, not a lowering bug). Anything unmapped falls to "unknown" (loud).
_US_DISPOSITION: Dict[str, WitnessDisposition] = {
    DISPOSITION_LAWVM_WRONG: "lawvm_wrong",
    # An over-materialized mis-route is a LawVM-side wrong materialization, so its
    # neutral witness disposition is ``lawvm_wrong`` (never ``oracle_suspect``).
    DISPOSITION_OVER_MATERIALIZED: "lawvm_wrong",
    DISPOSITION_ORACLE_SUSPECT: "oracle_suspect",
    DISPOSITION_MISSING_SOURCE: "missing_source",
    "sunset_reversion": "oracle_suspect",
}


def _disposition_for(raw: str) -> WitnessDisposition:
    return disposition_for(raw, _US_DISPOSITION)


def _amendatory_witness_firings(
    archive: UsArchiveReader, window: BenchWindow
) -> Dict[str, int]:
    """Tally the window's *compiled-op* witness firings (``us_amend_*`` /
    ``us_amendatory_*``).

    The dry-run report folds each section's lowering hypotheses into its outcome rule,
    so the amendatory rules never surface in ``report.rows``. To honor the firing
    definition (firings = compiled ops' ``witness_rule_id``) we re-derive the window
    laws and lower their USLM, tallying each instruction's ``witness_rule_id`` and each
    finding's ``rule_id``. This is read-only / import-only — it re-uses the same
    lowering the kernel runs and never enables replay.

    A window whose editions are missing yields no amendatory firings (the dry-run report
    is likewise absent for it).
    """
    from lawvm.us_federal.amendatory import lower_plaw_amendatory
    from lawvm.us_federal.bench import derive_window_law_locators
    from lawvm.us_federal.sources import read_plaw_locator

    locators = derive_window_law_locators(
        archive,
        title=window.title,
        before_year=window.before_year,
        after_year=window.after_year,
    )
    firings: Dict[str, int] = defaultdict(int)
    if not locators:
        return dict(firings)
    for statute_id, locator in sorted(locators.items()):
        blob = read_plaw_locator(archive, locator)
        if blob is None:
            continue
        report = lower_plaw_amendatory(blob, statute_id=statute_id)
        for instruction in report.instructions:
            rid = instruction.witness_rule_id or ""
            if rid:
                firings[rid] += 1
        for finding in report.findings:
            rid = finding.rule_id or ""
            if rid:
                firings[rid] += 1
    return dict(firings)


def us_ledger_inputs_from_reports(
    results: List[WindowResult],
    *,
    amendatory_firings: Mapping[str, Mapping[str, int]] | None = None,
) -> List[StatuteLedgerInput]:
    """Turn per-window dry-run reports into neutral per-window ledger inputs.

    For each evaluated window's :class:`USDryRunReport`:

    - **Section rows** (``report.rows``): each row's ``rule_id`` fires. An ``agree`` row
      (``us_dry_run_section_materialized_text_matches_oracle``) is corroborated -> a
      firing, no divergence. A residual row is a firing PLUS a :class:`DivergenceRow`
      whose disposition is mapped from the row's ``disposition``.
    - **Synthetic residuals** (from ``report.agreement_surface()``): the report's
      north-star derives a ``missing_source`` residual for every oracle-changed section
      the kernel never claimed, and a ``sunset_reversion`` residual for an F2 temporal
      change. These carry their own stable rule_id; each is a firing + a divergence.
    - **Refusals** (``report.refusals``): a typed refusal carries a ``rule_id`` but no
      oracle outcome (no materialization) -> a firing only, never a divergence. This is
      the surface's own coverage frontier, reported there.
    - **Sunset findings** (``report.sunset_findings``): an ambiguous temporal residual
      (note present, reversion unproven) -> a firing + a divergence (oracle_suspect).

    ``amendatory_firings`` (optional) maps a window key to its compiled-op witness
    firings (``us_amend_*`` / ``us_amendatory_*``), as produced by
    :func:`_amendatory_witness_firings`. These are corroboration-only firings (no per-op
    oracle outcome at the section-text granularity), folded in so the amendatory rules
    are not invisible blind spots.

    A window that was skipped (no report) contributes nothing.
    """
    amendatory_firings = amendatory_firings or {}
    inputs: List[StatuteLedgerInput] = []
    for result in results:
        report = result.report
        if result.window_status != "evaluated" or report is None:
            continue
        sid = result.window.key
        firings: Dict[str, int] = defaultdict(int)
        divergences: List[DivergenceRow] = []

        # 0. Compiled-op witness firings (us_amend_* / us_amendatory_*), if supplied.
        for rid, count in amendatory_firings.get(sid, {}).items():
            firings[rid] += count

        # 1. Per-section materialized-vs-oracle rows.
        for row in report.rows:
            firings[row.rule_id] += 1
            if row.row_status == "agree":
                continue
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=row.section_key,
                    diagnosis=row.disposition or row.rule_id,
                    disposition=_disposition_for(row.disposition),
                    rule_id=row.rule_id,
                    blame_source=row.op_id,
                )
            )

        # 2. Synthetic north-star residuals (missing_source + sunset_reversion) — the
        #    oracle-changed sections the kernel never materialized as a claimed row.
        for residual in _synthetic_residuals(report):
            rule_id, section_key, disposition_raw = residual
            firings[rule_id] += 1
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=section_key,
                    diagnosis=disposition_raw,
                    disposition=_disposition_for(disposition_raw),
                    rule_id=rule_id,
                    blame_source="north_star",
                )
            )

        # 3. Typed refusals: a fired hypothesis with no oracle outcome (coverage
        #    frontier). Firing only; never a divergence.
        for refusal in report.refusals:
            firings[refusal.rule_id] += 1

        # 4. Ambiguous sunset findings (note present, reversion unproven).
        for finding in report.sunset_findings:
            rid = finding.rule_id
            if not rid:
                continue
            firings[rid] += 1
            divergences.append(
                DivergenceRow(
                    sid=sid,
                    section_key=finding.section,
                    diagnosis="sunset_note_unproven",
                    disposition="oracle_suspect",
                    rule_id=rid,
                    blame_source="sunset_finding",
                )
            )

        if not firings and not divergences:
            continue
        inputs.append(
            StatuteLedgerInput(
                sid=sid,
                rule_firings=dict(firings),
                divergences=divergences,
            )
        )
    return inputs


def _synthetic_residuals(report: USDryRunReport) -> List[tuple[str, str, str]]:
    """Derive the (rule_id, section_key, disposition) of each north-star residual.

    The report's ``north_star()`` partitions the oracle-changed sections the kernel did
    not claim into ``missing_source`` and ``sunset_reversion``. Each carries a stable
    rule id (the source-footing-gap rule and the sunset-reversion rule respectively).
    """
    from lawvm.us_federal.dry_run import (
        US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    )
    from lawvm.us_federal.sunset import US_SUNSET_REVERSION_RULE_ID

    ns = report.north_star()
    out: List[tuple[str, str, str]] = []
    for section_key in ns.get("missing_source_sections", ()):
        out.append(
            (
                US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
                str(section_key),
                DISPOSITION_MISSING_SOURCE,
            )
        )
    for section_key in ns.get("sunset_reversion_sections", ()):
        out.append(
            (US_SUNSET_REVERSION_RULE_ID, str(section_key), "sunset_reversion")
        )
    return out


def _window_ledger_input(
    archive: UsArchiveReader, window: BenchWindow
) -> StatuteLedgerInput | None:
    """The neutral per-window ledger input for one included window (or ``None``).

    Evaluates the window's dry-run report and its compiled-op amendatory firings, then
    maps them through :func:`us_ledger_inputs_from_reports` exactly as the serial builder
    does — only scoped to a single window. A skipped window (no report) contributes
    nothing and yields ``None``. This is the unit of parallel work: it is pure given the
    archive + window, so per-window inputs are independent and order-free.
    """
    result = evaluate_window(archive, window)
    amendatory_firings = {window.key: _amendatory_witness_firings(archive, window)}
    inputs = us_ledger_inputs_from_reports(
        [result], amendatory_firings=amendatory_firings
    )
    return inputs[0] if inputs else None


def build_us_spec_ledger(
    archive: UsArchiveReader,
    windows: List[BenchWindow],
) -> SpecLedger:
    """Build the US discovered-spec ledger across the bench corpus windows.

    Runs the dry-run bench kernel per window (never re-implementing lowering or
    comparison — it only orchestrates ``evaluate_window`` and aggregates), maps every
    per-section outcome to the neutral core, and aggregates via
    :func:`lawvm.tools.spec_ledger.build_ledger` with the US rule catalog.
    """
    included = [w for w in windows if w.include]
    results = [evaluate_window(archive, w) for w in included]
    amendatory_firings = {
        w.key: _amendatory_witness_firings(archive, w) for w in included
    }
    inputs = us_ledger_inputs_from_reports(
        results, amendatory_firings=amendatory_firings
    )
    ledger = build_ledger(
        inputs,
        jurisdiction="us",
        mode="dry_run_section_text_vs_usc_after_edition",
        catalog=_US_RULE_SPECS,
    )
    # statutes counts evaluated windows; skipped windows are the errors-equivalent.
    ledger.statute_errors = sum(1 for w in windows if w.include) - ledger.statutes
    return ledger


# ---------------------------------------------------------------------------
# Parallel spec-ledger (determinism contract: byte-identical to build_us_spec_ledger)
# ---------------------------------------------------------------------------
#
# The serial builder evaluates the dry-run kernel for every included window in
# one process; over the grown bench corpus (175+ windows, the title-42 ACA-era
# window alone composing 800+ Public Laws) that is minutes of serial CPU. The
# parallel builder shards the included windows across worker processes (each with
# its OWN read-only farchive handle) and reassembles the per-window neutral
# ``StatuteLedgerInput`` rows in *corpus order* before the single ``build_ledger``
# pass on the parent.
#
# DETERMINISM CONTRACT (non-negotiable)
# -------------------------------------
#   * ``_window_ledger_input(archive, window)`` is pure given the archive + window
#     (it only reads), so each window's ledger input is independent of the others.
#   * ``build_ledger`` consumes inputs in iteration order and appends exemplars in
#     that order, so reassembling the inputs in corpus order and running the SAME
#     ``build_ledger`` / catalog on the parent reproduces the serial ledger
#     byte-for-byte. Only the per-window evaluation is parallelized; the ledger
#     SEMANTICS (firing tally, disposition mapping, ranking) are untouched.
#   * Each worker opens its own read-only farchive handle (a SQLite connection
#     never crosses a process boundary); ``readonly=True`` makes parallel readers
#     safe.

# Pool-size ceiling, matching the bench parallel path: each worker holds an open
# farchive handle and replays one window at a time, bounding resident memory under
# the WSL2 ceiling regardless of host core count or an over-large explicit value.
US_SPEC_LEDGER_MAX_WORKERS = 16

_WORKER_STORE: UsArchiveReader | None = None


def _spec_worker_init() -> None:
    """Process-pool initializer: open this worker's own read-only farchive once."""
    from lawvm.us_federal.sources import open_us_federal_farchive

    global _WORKER_STORE
    _WORKER_STORE = open_us_federal_farchive(readonly=True)


def _spec_worker_run_shard(
    task: tuple[int, Sequence[tuple[int, BenchWindow]]],
) -> tuple[int, list[tuple[int, StatuteLedgerInput | None]]]:
    """Compute the neutral ledger input for one shard of ``(corpus_index, window)``.

    Returns ``(shard_index, [(corpus_index, StatuteLedgerInput | None), ...])``. The
    heavy :class:`USDryRunReport` never crosses the process boundary — only the small
    projected ledger input does — so per-worker resident memory stays bounded.
    """
    shard_index, items = task
    assert _WORKER_STORE is not None, "worker store not initialized"
    out: list[tuple[int, StatuteLedgerInput | None]] = []
    for corpus_index, window in items:
        out.append((corpus_index, _window_ledger_input(_WORKER_STORE, window)))
    return shard_index, out


def _make_spec_shards(
    indexed: Sequence[tuple[int, BenchWindow]], workers: int
) -> list[tuple[int, list[tuple[int, BenchWindow]]]]:
    """Split indexed windows into contiguous, order-preserving shards.

    Several shards per worker balances uneven per-window replay cost (the title-42
    window dwarfs the rest) while keeping shards large enough to amortize dispatch.
    Mirrors ``bench._make_window_shards``.
    """
    n = len(indexed)
    if n == 0:
        return []
    target_shards = max(workers, min(n, workers * 4))
    chunk = (n + target_shards - 1) // target_shards
    shards: list[tuple[int, list[tuple[int, BenchWindow]]]] = []
    idx = 0
    start = 0
    while start < n:
        shards.append((idx, list(indexed[start:start + chunk])))
        idx += 1
        start += chunk
    return shards


def build_us_spec_ledger_parallel(
    windows: List[BenchWindow],
    *,
    workers: int,
) -> SpecLedger:
    """Parallel sibling of :func:`build_us_spec_ledger`, byte-identical in output.

    Shards the included windows across ``workers`` processes (each with its own
    read-only farchive handle), reassembles the per-window neutral inputs in corpus
    order, and runs the SAME single ``build_ledger`` pass on the parent. ``workers <= 1``
    (or a corpus of <= 1 included window) falls back to the serial builder over a single
    shared handle, so the parallel path is always a safe superset of the serial one.

    Unlike the serial builder, this opens the archive itself (one handle per worker)
    rather than taking a shared handle — a SQLite connection must never cross a process
    boundary.
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    included = [w for w in windows if w.include]
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 2)
    workers = min(workers, US_SPEC_LEDGER_MAX_WORKERS)

    # Serial fast path / fallback: identical to build_us_spec_ledger.
    if workers <= 1 or len(included) <= 1:
        archive = open_us_federal_farchive(readonly=True)
        try:
            return build_us_spec_ledger(archive, windows)
        finally:
            archive.close()

    from concurrent.futures import as_completed

    from lawvm.tools._worker_pool import managed_executor

    indexed = list(enumerate(included))
    shards = _make_spec_shards(indexed, workers)

    # corpus_index -> StatuteLedgerInput | None, for stable reassembly in corpus order.
    collected: dict[int, StatuteLedgerInput | None] = {}
    with managed_executor(workers, initializer=_spec_worker_init, initargs=()) as pool:
        futures = {
            pool.submit(_spec_worker_run_shard, task): task[0] for task in shards
        }
        for future in as_completed(futures):
            _shard_index, pairs = future.result()
            for corpus_index, inp in pairs:
                collected[corpus_index] = inp

    inputs = [
        collected[i] for i in range(len(included)) if collected[i] is not None
    ]
    ledger = build_ledger(
        cast(List[StatuteLedgerInput], inputs),
        jurisdiction="us",
        mode="dry_run_section_text_vs_usc_after_edition",
        catalog=_US_RULE_SPECS,
    )
    # statutes counts evaluated windows; skipped windows are the errors-equivalent.
    ledger.statute_errors = len(included) - ledger.statutes
    return ledger


def _load_us_bench_window_keys() -> List[str]:
    """Return shared-dispatch SIDs for included US bench windows.

    The shared ``spec-ledger`` CLI speaks in ``sids``. For US the natural unit is a
    bench window, so the SID is ``BenchWindow.key`` (for example
    ``title11:2023->2024``). Loading the committed CSV is cheap and does not open the
    farchive.
    """
    return [window.key for window in load_corpus(DEFAULT_CORPUS_PATH) if window.include]


def us_ledger_inputs(sids: List[str], mode: Mode) -> List[StatuteLedgerInput]:
    """Shared-registry adapter: selected US bench-window keys -> neutral inputs.

    ``mode`` is accepted for signature parity with the neutral registry; the US witness
    surface is always the dry-run section text vs USC after-edition oracle. Unknown
    window keys produce no input, so ``run_ledger`` reports them as statute_errors
    instead of silently inventing a row.
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    requested = set(sids)
    windows_by_key = {
        window.key: window
        for window in load_corpus(DEFAULT_CORPUS_PATH)
        if window.include
    }
    windows = [windows_by_key[key] for key in sids if key in windows_by_key]
    if not windows and requested:
        return []
    archive = open_us_federal_farchive(readonly=True)
    try:
        results = [evaluate_window(archive, window) for window in windows]
        amendatory_firings = {
            window.key: _amendatory_witness_firings(archive, window)
            for window in windows
        }
    finally:
        archive.close()
    return us_ledger_inputs_from_reports(
        results, amendatory_firings=amendatory_firings
    )


def ledger_to_dict(ledger: SpecLedger) -> Dict[str, Any]:
    """Project the ledger to a JSON artifact, enriched with US confidence tiers.

    Re-uses the neutral core's ``to_dict`` and folds in the per-rule confidence tier and
    a ``cataloged`` flag (``False`` = a fired rule with no believed-spec = a
    ``legacy_unknown`` blind spot).
    """
    base = ledger.to_dict()
    rules = cast(List[Dict[str, Any]], base["rules"])
    for rule in rules:
        rid = rule["rule_id"]
        rule["confidence"] = (
            us_confidence(rid)
            if rule["cataloged"]
            else US_LEGACY_UNKNOWN
        )
    base["legacy_unknown_rules"] = sorted(
        rule["rule_id"] for rule in rules if not rule["cataloged"]
    )
    return base


def render_text(ledger: SpecLedger) -> str:
    """Human-readable US discovered-spec ledger: rules ranked by contradiction."""
    art = ledger_to_dict(ledger)
    lines: List[str] = [
        "US discovered-spec ledger (witness = published USC after-edition, dry-run)",
        f"windows_evaluated={art['statutes']} skipped={art['statute_errors']} "
        f"rules={art['n_rules']} unattributed_divergences={art['n_unattributed']}",
        "",
        f"{'rule_id':<62} {'conf':<10} {'fire':>5} {'corrob~':>7} "
        f"{'contra':>6}  dispositions",
        "-" * 120,
    ]
    rules = cast(List[Dict[str, Any]], art["rules"])
    for rule in rules:
        disp = " ".join(f"{k}:{v}" for k, v in sorted(rule["by_disposition"].items()))
        cataloged = "" if rule["cataloged"] else " [UNCATALOGED!]"
        lines.append(
            f"{rule['rule_id']:<62} {rule['confidence']:<10} "
            f"{rule['firings']:>5} {rule['corroborated_est']:>7} "
            f"{rule['contradicted']:>6}  {disp}{cataloged}"
        )
    lines.append("")
    lines.append("believed spec per rule:")
    for rule in rules:
        spec = rule["believed_spec"] or "(no believed_spec — uncataloged blind spot)"
        lines.append(f"  - {rule['rule_id']}:")
        lines.append(f"      {spec}")
        if rule["contradicted"]:
            top = _top_contradicting_windows(rule["exemplars"])
            if top:
                lines.append(f"      top contradicting windows: {', '.join(top)}")
    legacy = cast(List[str], art["legacy_unknown_rules"])
    if legacy:
        lines.append("")
        lines.append("LEGACY_UNKNOWN (fired US rule_ids with no catalog entry):")
        for rid in legacy:
            lines.append(f"  - {rid}")
    if art["n_unattributed"]:
        lines.append("")
        lines.append(f"unattributed divergences (blind spots): {art['n_unattributed']}")
    return "\n".join(lines)


def _top_contradicting_windows(exemplars: List[Mapping[str, str]]) -> List[str]:
    seen: List[str] = []
    for ex in exemplars:
        statute = str(ex.get("statute") or "")
        if statute and statute not in seen:
            seen.append(statute)
        if len(seen) >= 5:
            break
    return seen


register_ledger_adapter(
    LedgerAdapter(
        jurisdiction="us",
        ledger_inputs=us_ledger_inputs,
        catalog=_US_RULE_SPECS,
        corpus_loaders={"bench": _load_us_bench_window_keys},
    )
)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="U.S. federal witness-attribution spec-discovery ledger.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help=f"bench corpus CSV (default: {DEFAULT_CORPUS_PATH})",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the ledger JSON instead of the table"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=0,
        metavar="N",
        help=(
            "evaluate windows across N worker processes (byte-identical ledger); "
            "0 = serial, <0 = auto (cpu-2)"
        ),
    )
    parser.add_argument(
        "--json-out",
        default="",
        metavar="PATH",
        help="also write the ledger JSON to this path",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    corpus_path: Path = args.corpus
    if not corpus_path.exists():
        print(f"error: bench corpus not found: {corpus_path}", file=sys.stderr)
        return 1

    from lawvm.us_federal.sources import open_us_federal_farchive

    windows = load_corpus(corpus_path)
    if args.parallel != 0:
        ledger = build_us_spec_ledger_parallel(windows, workers=args.parallel)
    else:
        archive = open_us_federal_farchive(readonly=True)
        try:
            ledger = build_us_spec_ledger(archive, windows)
        finally:
            archive.close()

    if args.json:
        print(json.dumps(ledger_to_dict(ledger), ensure_ascii=False, indent=2))
    else:
        print(render_text(ledger))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(ledger_to_dict(ledger), fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json_out}", file=sys.stderr)
    return 0


# Silence the "imported but unused" for the documented-exclusion symbol re-exported for
# the coverage test's convenience.
__all__ = [
    "DEFAULT_CORPUS_PATH",
    "US_LEGACY_UNKNOWN",
    "US_NON_RULE_LITERALS",
    "build_us_spec_ledger",
    "build_us_spec_ledger_parallel",
    "ledger_to_dict",
    "main",
    "render_text",
    "us_ledger_inputs",
    "us_ledger_inputs_from_reports",
]


if __name__ == "__main__":
    raise SystemExit(main())
