"""broken-refs — corpus broken-reference report (fi).

Sweeps the FI corpus and, per citing statute, asks whether each resolved
cross-statute citation points at a target provision that actually exists in the
target statute's text-state. Two modes:

DEFAULT (current-state, no replay)
    Checks whether the cited target provision is present in the target's CURRENT
    consolidated text-state — the Finlex oracle already gives that body for free,
    so this is a cheap structural presence check (no point-in-time replay). Runs
    corpus-wide without timing out. A finding is "the cited target provision is
    absent in the target's current text-state". This is the bitemporal
    counterpart to ``surface-lints`` / ``refs-bench``.

``--provenance`` (point-in-time replay)
    Adds the temporal-provenance premium: per citing statute it replays the
    TARGET statute's tree as of the citation AND as of now (``legal_pit``), so it
    can classify the disappearance (repealed_since / renumbered_since vs
    never_existed). This IS heavy replay — SLOW — so use ``--limit`` to sample.

A target whose body/tree cannot be materialized is reported as UNAVAILABLE
(fail-loud) in both modes, never silently dropped and never called broken.

Surface-fact discipline: a finding is "the cited target provision is
absent/renumbered in the target's text-state", NOT "the law is invalid".
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from lawvm.finland.legal_surface.bitemporal import (
    BrokenRefReport,
    CurrentStateReport,
    StatuteScanResult,
    scan_current_state,
    scan_one_statute,
)


# ---------------------------------------------------------------------------
# Per-worker store (loaded once per process).
# ---------------------------------------------------------------------------
_STORE = None


def _archive_path() -> str:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


def _get_store():
    global _STORE
    if _STORE is None:
        from farchive import Farchive
        from lawvm.finland.transparent_store import TransparentCorpusStore

        _STORE = TransparentCorpusStore(Farchive(_archive_path()))
    return _STORE


def _statute_ids(limit: int) -> list[str]:
    ids = _get_store().list_statute_ids()
    return ids[:limit] if limit else ids


def _scan_one(sid: str) -> StatuteScanResult:
    """Worker: scan one citing statute with the default legal_pit adapters.

    Always returns a ``StatuteScanResult`` (errors recorded in ``.error``,
    never raised away — fail-loud into the errored bucket).
    """
    from lawvm.finland.legal_surface.bitemporal import legal_pit_tree_as_of
    from lawvm.finland.references.broken_detection import default_provision_present

    store = _get_store()
    try:
        return scan_one_statute(
            sid,
            store,
            tree_as_of=legal_pit_tree_as_of(store),
            provision_present=default_provision_present,
        )
    except Exception:
        return StatuteScanResult(
            sid=sid,
            mentions_checked=0,
            findings=(),
            unavailable=(),
            error=traceback.format_exc(limit=4).strip(),
        )


def _aggregate(results: list[StatuteScanResult]) -> BrokenRefReport:
    """Fold per-statute results into the corpus report (mirrors scan_broken_references)."""
    import collections

    from lawvm.finland.references.broken_detection import BrokenReason

    report = BrokenRefReport()
    reason_ct: collections.Counter[str] = collections.Counter()
    unavailable_kind_ct: collections.Counter[str] = collections.Counter()
    for result in results:
        report.per_statute.append(result)
        report.statutes_scanned += 1
        if result.error is not None:
            report.statutes_errored.append((result.sid, result.error))
            continue
        report.mentions_checked += result.mentions_checked
        if result.findings:
            report.statutes_with_findings += 1
        for finding in result.findings:
            reason_ct[finding.reason.value] += 1
        for unavail in result.unavailable:
            unavailable_kind_ct[unavail.unavailable_for] += 1
            report.unavailable_count += 1
    report.reason_counts = {
        r.value: reason_ct.get(r.value, 0)
        for r in BrokenReason
        if reason_ct.get(r.value, 0)
    }
    report.unavailable_by_kind = dict(sorted(unavailable_kind_ct.items()))
    return report


def _emit_json(report: BrokenRefReport, top: int) -> None:
    payload: dict = {
        "jurisdiction": "fi",
        "metric": "bitemporal_broken_references",
        "unit": "cross_statute_citation",
        "statutes_scanned": report.statutes_scanned,
        "statutes_with_findings": report.statutes_with_findings,
        "statutes_errored": len(report.statutes_errored),
        "mentions_checked": report.mentions_checked,
        "findings_total": report.total_findings,
        "findings_by_reason": report.reason_counts,
        "unavailable_total": report.unavailable_count,
        "unavailable_by_kind": report.unavailable_by_kind,
        "top_statutes": [
            {
                "sid": r.sid,
                "findings": len(r.findings),
                "mentions_checked": r.mentions_checked,
                "examples": [
                    {
                        "source": f.source.serialized(),
                        "target": f.target.serialized(),
                        "reason": f.reason.value,
                    }
                    for f in r.findings[:3]
                ],
            }
            for r in report.top_statutes(top)
        ],
        "errored": [
            {"sid": sid, "error": err}
            for sid, err in report.statutes_errored[:top]
        ],
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _emit_text(report: BrokenRefReport, top: int) -> None:
    print("\n=== broken-refs (bitemporal broken-reference report, fi) ===")
    print(
        "  (a finding = the cited target provision is absent/renumbered in the "
        "time-indexed\n   text-state as of the citation; NOT a legal conclusion)"
    )
    print(f"  statutes scanned            : {report.statutes_scanned}")
    print(f"  statutes with findings      : {report.statutes_with_findings}")
    print(f"  statutes errored            : {len(report.statutes_errored)}")
    print(f"  cross-statute cites checked : {report.mentions_checked}")
    print(f"  broken-reference findings   : {report.total_findings}")
    print(f"  undetermined (unavailable)  : {report.unavailable_count}")

    print("\n  findings by reason:")
    if report.reason_counts:
        for reason, n in report.reason_counts.items():
            print(f"    {n:8}  {reason}")
    else:
        print("    (no broken references found)")

    print("\n  unavailable (fail-loud — brokenness undetermined) by tree:")
    if report.unavailable_by_kind:
        for kind, n in report.unavailable_by_kind.items():
            print(f"    {n:8}  {kind}")
    else:
        print("    (none)")

    worst = report.top_statutes(top)
    print(f"\n  top {top} statutes by finding count:")
    if worst:
        for r in worst:
            print(
                f"    {r.sid}: {len(r.findings)} findings "
                f"({r.mentions_checked} cites checked)"
            )
            for f in r.findings[:3]:
                print(
                    f"        [{f.reason.value}] "
                    f"{f.source.serialized()} -> {f.target.serialized()}"
                )
    else:
        print("    (none)")

    if report.statutes_errored:
        print(
            f"\n  ERRORED statutes ({len(report.statutes_errored)}; "
            f"showing up to {top}):"
        )
        for sid, err in report.statutes_errored[:top]:
            first_line = err.splitlines()[-1] if err else err
            print(f"    {sid}: {first_line}")


def _emit_current_json(report: CurrentStateReport, top: int, elapsed: float) -> None:
    payload: dict = {
        "jurisdiction": "fi",
        "metric": "current_state_broken_references",
        "mode": "current_state",
        "unit": "cross_statute_citation",
        "wall_clock_s": round(elapsed, 2),
        "statutes_scanned": report.statutes_scanned,
        "statutes_with_findings": report.statutes_with_findings,
        "statutes_errored": len(report.statutes_errored),
        "mentions_checked": report.mentions_checked,
        "findings_total": report.total_findings,
        "findings_by_kind": report.kind_counts,
        "unavailable_total": report.unavailable_count,
        "top_statutes": [
            {
                "sid": r.sid,
                "findings": len(r.findings),
                "mentions_checked": r.mentions_checked,
                "examples": [
                    {
                        "source": f.source.serialized(),
                        "target": f.target.serialized(),
                        "kind": f.kind,
                    }
                    for f in r.findings[:3]
                ],
            }
            for r in report.top_statutes(top)
        ],
        "errored": [
            {"sid": sid, "error": err} for sid, err in report.statutes_errored[:top]
        ],
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _emit_current_text(report: CurrentStateReport, top: int, elapsed: float) -> None:
    print("\n=== broken-refs (current-state broken-reference report, fi) ===")
    print(
        "  (DEFAULT no-replay mode: a finding = the cited target provision is "
        "absent in the\n   target's CURRENT consolidated text-state; NOT a legal "
        "conclusion. Use --provenance\n   for the repealed/renumbered/never-existed "
        "temporal classification via replay.)"
    )
    print(f"  wall-clock (s)              : {elapsed:.2f}")
    print(f"  statutes scanned            : {report.statutes_scanned}")
    print(f"  statutes with findings      : {report.statutes_with_findings}")
    print(f"  statutes errored            : {len(report.statutes_errored)}")
    print(f"  cross-statute cites checked : {report.mentions_checked}")
    print(f"  absent-target findings      : {report.total_findings}")
    print(f"  undetermined (unavailable)  : {report.unavailable_count}")

    print("\n  findings by kind:")
    if report.kind_counts:
        for kind, n in report.kind_counts.items():
            print(f"    {n:8}  {kind}")
    else:
        print("    (no absent-target references found)")

    worst = report.top_statutes(top)
    print(f"\n  top {top} statutes by finding count:")
    if worst:
        for r in worst:
            print(
                f"    {r.sid}: {len(r.findings)} findings "
                f"({r.mentions_checked} cites checked)"
            )
            for f in r.findings[:3]:
                print(
                    f"        [{f.kind}] "
                    f"{f.source.serialized()} -> {f.target.serialized()}"
                )
    else:
        print("    (none)")

    if report.statutes_errored:
        print(
            f"\n  ERRORED statutes ({len(report.statutes_errored)}; "
            f"showing up to {top}):"
        )
        for sid, err in report.statutes_errored[:top]:
            first_line = err.splitlines()[-1] if err else err
            print(f"    {sid}: {first_line}")


def run(args) -> None:
    """Corpus broken-reference report (fi): current-state default / replay opt-in."""
    limit = getattr(args, "limit", 0) or 0
    workers = getattr(args, "workers", 0) or 0
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20
    provenance = getattr(args, "provenance", False)

    ids = _statute_ids(limit)

    if not provenance:
        # DEFAULT: cheap current-state scan, no replay. Fast enough to run
        # in-process corpus-wide — no ProcessPoolExecutor needed.
        print(
            f"broken-refs: current-state check of {len(ids)} citing statutes "
            f"(no replay — cheap; pass --provenance for the temporal premium)...",
            file=sys.stderr,
        )
        store = _get_store()
        start = time.perf_counter()
        current_report = scan_current_state(ids, store)
        elapsed = time.perf_counter() - start
        if as_json:
            _emit_current_json(current_report, top, elapsed)
            return
        _emit_current_text(current_report, top, elapsed)
        return

    # --provenance: heavy point-in-time replay path (the temporal premium).
    if not workers:
        workers = min(8, max(1, (os.cpu_count() or 2) - 2))
    print(
        f"broken-refs: --provenance: bitemporally checking {len(ids)} citing "
        f"statutes (legal_pit replay of target trees — SLOW) with "
        f"{workers} workers...",
        file=sys.stderr,
    )

    results: list[StatuteScanResult] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(_scan_one, ids, chunksize=8)):
            results.append(r)
            if i and i % 100 == 0:
                print(f"  {i}/{len(ids)}", file=sys.stderr)

    report = _aggregate(results)

    if as_json:
        _emit_json(report, top)
        return
    _emit_text(report, top)


def main(args) -> None:
    """Dispatch on the global -j/--jurisdiction flag (only fi has the detector today)."""
    jur = (getattr(args, "jurisdiction", None) or "fi").lower()
    if jur == "fi":
        run(args)
        return
    print(
        f"broken-refs: the bitemporal broken-reference detector is defined for "
        f"fi only; {jur!r} has no resolved reference graph."
    )
