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

        _STORE = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    return _STORE


def _statute_ids(limit: int, stride: int = 0) -> list[str]:
    """Select citing statute ids: stride-sample (representative) then cap.

    ``stride`` (>1) keeps every Nth id — a representative corpus-wide sample
    rather than a contiguous prefix (the prefix is all 1734-era base codes whose
    ids carry non-numeric tails, so they have no parseable citation-date anchor).
    ``limit`` then caps the selection. Both are honest, documented scope knobs.
    """
    ids = _get_store().list_statute_ids()
    if stride and stride > 1:
        ids = ids[::stride]
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
        "skipped_out_of_scope": report.skipped_count,
        "self_refs_excluded": report.self_refs_excluded,
        # Statute-lifecycle layer (registry/oracle-driven, no replay): the cited
        # ACT itself was repealed / not-yet-in-force at the citing date.
        "lifecycle_findings_total": report.total_lifecycle_findings,
        "lifecycle_findings_by_reason": report.lifecycle_reason_counts,
        "lifecycle_unverifiable_total": report.lifecycle_unverifiable_count,
        "top_statutes": [
            {
                "sid": r.sid,
                "findings": len(r.findings),
                "lifecycle_findings": len(r.lifecycle_findings),
                "mentions_checked": r.mentions_checked,
                "examples": [
                    {
                        "source": f.source.serialized(),
                        "target": f.target.serialized(),
                        "kind": f.kind,
                    }
                    for f in r.findings[:3]
                ],
                "lifecycle_examples": [
                    {
                        "source": f.source.serialized(),
                        "target": f.target.serialized(),
                        "reason": f.reason.value,
                        "cited_on": f.cited_on.isoformat(),
                        "target_window": [
                            f.target_window[0].isoformat()
                            if f.target_window[0]
                            else None,
                            f.target_window[1].isoformat()
                            if f.target_window[1]
                            else None,
                        ],
                    }
                    for f in r.lifecycle_findings[:3]
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
        "temporal classification via replay.\n   Citers with no consolidated "
        "text-state (amendment acts / source-only) are SKIPPED\n   as out of scope "
        "— their internal refs are amended-law-relative, not self-refs.)"
    )
    print(f"  wall-clock (s)              : {elapsed:.2f}")
    print(f"  statutes scanned            : {report.statutes_scanned}")
    print(f"  statutes with findings      : {report.statutes_with_findings}")
    print(f"  statutes errored            : {len(report.statutes_errored)}")
    print(f"  skipped (out of scope)      : {report.skipped_count}")
    print(f"  self-refs excluded          : {report.self_refs_excluded}")
    print(f"  cross-statute cites checked : {report.mentions_checked}")
    print(f"  absent-target findings      : {report.total_findings}")
    print(f"  statute-lifecycle findings  : {report.total_lifecycle_findings}")
    print(f"  undetermined (unavailable)  : {report.unavailable_count}")
    print(f"  lifecycle unverifiable      : {report.lifecycle_unverifiable_count}")

    print("\n  provision-absent findings by kind:")
    if report.kind_counts:
        for kind, n in report.kind_counts.items():
            print(f"    {n:8}  {kind}")
    else:
        print("    (no absent-target references found)")

    print(
        "\n  statute-lifecycle findings by reason "
        "(cited ACT repealed / not-yet-in-force at the citing date):"
    )
    if report.lifecycle_reason_counts:
        for reason, n in report.lifecycle_reason_counts.items():
            print(f"    {n:8}  {reason}")
    else:
        print("    (no dead-act references found)")

    worst = report.top_statutes(top)
    print(f"\n  top {top} statutes by finding count (provision + lifecycle):")
    if worst:
        for r in worst:
            print(
                f"    {r.sid}: {len(r.findings)} provision + "
                f"{len(r.lifecycle_findings)} lifecycle findings "
                f"({r.mentions_checked} cites checked)"
            )
            for f in r.findings[:3]:
                print(
                    f"        [{f.kind}] "
                    f"{f.source.serialized()} -> {f.target.serialized()}"
                )
            for lf in r.lifecycle_findings[:3]:
                vf, vt = lf.target_window
                win = f"{vf or '...'}..{vt or '...'}"
                print(
                    f"        [{lf.reason.value}] "
                    f"{lf.source.serialized()} -> {lf.target.serialized()} "
                    f"(cited {lf.cited_on.isoformat()}; target in force {win})"
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


def _ledger_rows(report: CurrentStateReport) -> list[dict]:
    """Flatten EVERY statute-lifecycle (target_statute_repealed / not-yet) finding.

    The complete dangling-reference ledger: one row per finding (a live
    consolidated text citing an act not in force at the citing date), carrying the
    citing statute, the source span, the dead target, the verdict reason and the
    target act's in-force window (the repeal date is ``target_valid_to``). Rows are
    deterministically sorted (citer, then target, then source span) so the artifact
    is stable and diff-friendly. No truncation — this is the full täyslaskenta dump,
    not the summarized ``--top`` view.
    """
    rows: list[dict] = []
    for r in report.per_statute:
        if r.error is not None or r.skipped is not None:
            continue
        for lf in r.lifecycle_findings:
            vf, vt = lf.target_window
            rows.append(
                {
                    "citing_statute": r.sid,
                    "source": lf.source.serialized(),
                    "target": lf.target.serialized(),
                    "target_statute": lf.target.statute_id,
                    "reason": lf.reason.value,
                    "cited_on": lf.cited_on.isoformat(),
                    "target_valid_from": vf.isoformat() if vf else None,
                    "target_valid_to": vt.isoformat() if vt else None,
                }
            )
    rows.sort(
        key=lambda d: (
            d["citing_statute"],
            d["target_statute"],
            d["source"],
            d["target"],
        )
    )
    return rows


def _ledger_summary(rows: list[dict]) -> dict:
    """Headline stats over the flattened ledger rows."""
    import collections

    citers = {d["citing_statute"] for d in rows}
    dead_targets: collections.Counter[str] = collections.Counter(
        d["target_statute"] for d in rows
    )
    reasons: collections.Counter[str] = collections.Counter(d["reason"] for d in rows)
    # Inbound-citation count = distinct citing statutes per dead target (a dead act
    # cited from many statutes is ranked above one cited many times by a single act).
    inbound: dict[str, set[str]] = {}
    repeal_of: dict[str, str | None] = {}
    for d in rows:
        inbound.setdefault(d["target_statute"], set()).add(d["citing_statute"])
        repeal_of.setdefault(d["target_statute"], d["target_valid_to"])
    top_targets = sorted(
        (
            {
                "target_statute": tgt,
                "inbound_citing_statutes": len(citers_set),
                "total_findings": dead_targets[tgt],
                "repeal_date": repeal_of.get(tgt),
            }
            for tgt, citers_set in inbound.items()
        ),
        key=lambda d: (
            -d["inbound_citing_statutes"],
            -d["total_findings"],
            d["target_statute"],
        ),
    )
    return {
        "total_findings": len(rows),
        "distinct_citing_statutes": len(citers),
        "distinct_dead_targets": len(dead_targets),
        "findings_by_reason": dict(sorted(reasons.items())),
        "top_dead_targets_by_inbound_citation_count": top_targets[:50],
    }


def _write_ledger_md(path: str, summary: dict, rows: list[dict]) -> None:
    """Render the ledger as a Markdown report (summary + the full sorted table)."""
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    lines: list[str] = []
    lines.append("# Dangling-reference ledger (Finland) — täyslaskenta")
    lines.append("")
    lines.append(
        "Every place a live Finnish consolidated statute text cites an act that "
        "was repealed (or not yet in force) at the citing date. A finding is a "
        "surface fact (live text -> dead act), not a legal conclusion. "
        "Deterministic, fail-loud, no false positives (unknown lifecycle is "
        "reported unverifiable, never as a finding)."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total findings: **{summary['total_findings']}**")
    lines.append(
        f"- Distinct citing statutes: **{summary['distinct_citing_statutes']}**"
    )
    lines.append(f"- Distinct dead targets: **{summary['distinct_dead_targets']}**")
    lines.append(f"- Findings by reason: {summary['findings_by_reason']}")
    lines.append("")
    lines.append("### Top dead targets by inbound-citation count")
    lines.append("")
    lines.append(
        "| Dead act | Inbound citing statutes | Total findings | Repeal date |"
    )
    lines.append("|---|---:|---:|---|")
    for t in summary["top_dead_targets_by_inbound_citation_count"]:
        lines.append(
            f"| {t['target_statute']} | {t['inbound_citing_statutes']} | "
            f"{t['total_findings']} | {t['repeal_date'] or '(open)'} |"
        )
    lines.append("")
    lines.append("## Full ledger")
    lines.append("")
    lines.append(
        "| Citing statute | Source span | Cited (dead) target | Reason | "
        "Repeal date (valid_to) |"
    )
    lines.append("|---|---|---|---|---|")
    for d in rows:
        lines.append(
            f"| {d['citing_statute']} | {d['source']} | {d['target']} | "
            f"{d['reason']} | {d['target_valid_to'] or '(open)'} |"
        )
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_ledger(report: CurrentStateReport, path: str) -> None:
    """Write the complete dangling-reference ledger to ``path`` (JSON, + .md if asked).

    JSON is always written (to ``path`` as-is when it does not end ``.md``, else
    alongside with a ``.json`` extension). When ``path`` ends ``.md`` a
    human-readable Markdown table is ALSO written, so one ``--ledger-out X.md``
    yields both artifacts.
    """
    import os

    rows = _ledger_rows(report)
    summary = _ledger_summary(rows)
    payload = {
        "jurisdiction": "fi",
        "artifact": "dangling_reference_ledger",
        "unit": "cross_statute_citation_to_dead_act",
        "discipline": (
            "deterministic, fail-loud, no false positives: a row is a live "
            "consolidated text citing an act whose in-corpus oracle repeal date "
            "(target_valid_to) is at/before the citing date; an unknown lifecycle "
            "is reported UNVERIFIABLE, never as a finding"
        ),
        "summary": summary,
        "findings": rows,
    }

    is_md = path.lower().endswith(".md")
    json_path = (os.path.splitext(path)[0] + ".json") if is_md else path
    os.makedirs(os.path.dirname(os.path.abspath(json_path)) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"broken-refs: wrote ledger JSON -> {json_path}", file=sys.stderr)

    if is_md:
        _write_ledger_md(path, summary, rows)
        print(f"broken-refs: wrote ledger Markdown -> {path}", file=sys.stderr)


def run(args) -> None:
    """Corpus broken-reference report (fi): current-state default / replay opt-in."""
    limit = getattr(args, "limit", 0) or 0
    stride = getattr(args, "stride", 0) or 0
    workers = getattr(args, "workers", 0) or 0
    as_json = getattr(args, "json", False)
    top = getattr(args, "top", 20) or 20
    provenance = getattr(args, "provenance", False)
    ledger_out = getattr(args, "ledger_out", "") or ""

    ids = _statute_ids(limit, stride)

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
        if ledger_out:
            _write_ledger(current_report, ledger_out)
        if as_json:
            _emit_current_json(current_report, top, elapsed)
            return
        _emit_current_text(current_report, top, elapsed)
        return

    if ledger_out:
        print(
            "broken-refs: --ledger-out is a current-state-mode artifact "
            "(the statute-lifecycle dangling-act ledger); ignored under "
            "--provenance.",
            file=sys.stderr,
        )

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
