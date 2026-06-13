"""Estonia amendment-chain self-consistency audit.

An Estonia statute's amendment chain is *self-consistent* when every amendment
reference resolves and fetches, every parsed operation targets a structural unit
the replay can locate, every op is applied (not rejected/unhandled/unparsed), and
no amendment is silently dropped from the chain.  Whenever that breaks — a source
act cannot be fetched/parsed, the lowering rejects an unparsed META op, the apply
layer cannot find the target unit, an action is unsupported, or a reference is
filtered out of the executable slice — the chain is internally inconsistent and
the case is a high-signal triage candidate.

This audit is ORACLE-INDEPENDENT: it never consults the RT consolidated oracle.
It replays each base→PIT pair purely for the side-channel of its own typed
``CompileAdjudication`` stream plus the discovery-level skipped/failed reference
lists, and classifies those into the same signal taxonomy the Finland
``self-consistency`` tool uses, so the two jurisdictions can share a triage view.

Signal taxonomy (mapped to the Finland tool where the surfaces are genuine):

  apply_failure       replay-layer ``CompileAdjudication`` whose ``detail`` is
                      ``blocking`` (an op the apply layer refused to execute) —
                      EE's analogue of a typed FailedOp record
  target_absent       ``ee_replay_target_not_found`` — repeal/replace/insert
                      whose target unit the replay could not resolve
  unhandled_op        unsupported actions / non-body meta skips / unparsed
                      operation skips / parser-level ``*_rejected`` lowerings
  source_pathology    source-lane failures (``*_fetch_failed`` /
                      ``*_parse_failed``), RT-XML metadata pathologies, and any
                      adjudication whose ``detail.family`` is ``source_pathology``
                      or ``source_lane_failure``
  skipped_amendment   references dropped from the executable chain — discovery
                      ``amendments_skipped`` / ``amendments_failed`` plus the
                      ``ee_ref_slice_operation_filtered`` and
                      ``ee_cancelled_pending_amendment_ref_filtered`` adjudications
  invariant_violation a replay that crashed (``EEPitResult.error``) — the chain
                      could not be evaluated at all

EE has no oracle-free coverage notion (coverage is defined against the RT
consolidated text), so the ``coverage_gap`` / ``elaboration_finding`` FI signals
are intentionally absent here rather than fabricated.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lawvm.tools.self_consistency import _category

# ---------------------------------------------------------------------------
# Signal taxonomy
# ---------------------------------------------------------------------------

EE_SIGNAL_TYPES = (
    "apply_failure",
    "target_absent",
    "unhandled_op",
    "source_pathology",
    "skipped_amendment",
    "invariant_violation",
)

# Adjudication-kind classification.  EE adjudication kinds are stable rule IDs;
# we route them by name (the only surface guaranteed present on every record)
# and fall back to the ``detail.family`` / ``detail.blocking`` fields when the
# name alone is ambiguous.  Recovery/normalisation kinds (the large majority)
# denote *successful* handling of a tricky surface and are NOT signals.

# Exact-kind → signal-type overrides (checked first).
_KIND_SIGNAL: Dict[str, str] = {
    "ee_replay_target_not_found": "target_absent",
    "ee_replay_unsupported_action": "unhandled_op",
    "ee_replay_unsupported_heading_target": "unhandled_op",
    "ee_replay_unsupported_statute_title_action": "unhandled_op",
    "ee_replay_meta_non_body_skipped": "unhandled_op",
    "ee_replay_unparsed_operation_skipped": "unhandled_op",
    # A pending amendment cancelled by a later same-commencement act before it
    # ever takes effect — a genuine chain-shaping event worth surfacing.
    "ee_cancelled_pending_amendment_ref_filtered": "skipped_amendment",
}

# Deliberate temporal-windowing decisions (an op belongs to a different
# effective-date slice / a later PIT), not inconsistencies — never signals.
# ``ee_ref_slice_operation_filtered`` (family ``ref_slice_filter``) is the high-
# volume member and is excluded via ``_RECOVERY_FAMILIES`` below.

# Fetch / IO / metadata pathology families (detail.family) → source_pathology.
# These denote a *source acquisition* defect (could not fetch/parse the source).
_SOURCE_PATHOLOGY_FAMILIES = frozenset({"source_pathology", "source_lane_failure"})

# Families denoting an operation surface the lowering could not handle (the op is
# preserved but not executed) → unhandled_op.
_UNHANDLED_FAMILIES = frozenset(
    {"unsupported_source_lane", "unsupported_or_unresolved_action"}
)

# Families that are pure recovery book-keeping — never a signal regardless of
# kind-name keywords.
_RECOVERY_FAMILIES = frozenset(
    {
        "target_resolution_recovery",
        "pending_amendment_precomposition",
        "pending_amendment_cancellation_filter",
        "pending_source_act_precomposition",
        "title_relabel_alias",
        "appendix_table_update",
        "temporal_recovery",
        "single_target_preambul",
        "ref_slice_filter",
    }
)


def _detail_str(detail: Any, key: str) -> str:
    if isinstance(detail, dict):
        value = detail.get(key)
        if value is not None:
            return str(value)
    return ""


def _classify_adjudication_kind(kind: str, detail: Any) -> Optional[str]:
    """Map a ``CompileAdjudication`` to an EE self-consistency signal, or None.

    Returns ``None`` for recovery/normalisation adjudications that do not denote
    an internal inconsistency.
    """
    explicit = _KIND_SIGNAL.get(kind)
    if explicit is not None:
        return explicit

    family = _detail_str(detail, "family")
    if family in _SOURCE_PATHOLOGY_FAMILIES:
        return "source_pathology"
    if family in _UNHANDLED_FAMILIES:
        return "unhandled_op"
    if family in _RECOVERY_FAMILIES:
        return None

    # Name-keyword routing for the remaining parser-level rejections / failures.
    if "_fetch_failed" in kind or "_parse_failed" in kind:
        return "source_pathology"
    if kind.startswith("ee_rt_xml_"):
        return "source_pathology"
    if kind.endswith("_rejected"):
        # Parser-level lowering refused to emit an executable op.
        return "unhandled_op"
    if "_target_not_found" in kind or kind.endswith("_unresolved"):
        return "target_absent"

    # A blocking disposition with no recovery family is an apply-level refusal.
    blocking = detail.get("blocking") if isinstance(detail, dict) else None
    if blocking is True and not family:
        return "apply_failure"
    return None


# ---------------------------------------------------------------------------
# Per-pair projection (module-level: picklable for the process pool)
# ---------------------------------------------------------------------------

def _scope_from_detail(detail: Any) -> str:
    target = _detail_str(detail, "target")
    action = _detail_str(detail, "action")
    if target and action:
        return f"{action} {target}"
    return target or action


def _project_ee_pair(
    gid: str,
    base_id: str,
    oracle_id: str,
    archive: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay one EE pair and project every self-consistency signal as rows.

    Returns ``(signal_rows, error_rows)``.  A replay crash is recorded both as a
    structured ``invariant_violation`` signal row AND an ``error_rows`` entry so
    one bad pair never aborts the sweep.

    The replay is run ORACLE-FREE (``oracle_id`` is only used to derive the PIT
    date); the consistency check against the RT consolidated text is irrelevant
    to self-consistency and is skipped because no oracle IRStatute is built.
    """
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml
    from lawvm.estonia.replay import replay_ee_to_pit

    rows: List[Dict[str, Any]] = []
    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or "2026-03-24"
    except Exception as exc:
        return [], [{"statute_id": gid, "base_id": base_id, "error": f"oracle-date: {type(exc).__name__}: {exc}"}]

    try:
        # No oracle_id passed → no oracle IRStatute is built → no divergence
        # computation.  Pure self-consistency replay.
        result = replay_ee_to_pit(base_id, as_of=as_of, archive=archive)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        rows.append({
            "statute_id": gid,
            "amendment_id": base_id,
            "signal_type": "invariant_violation",
            "category": _category(err),
            "description": err,
            "target_scope": "",
            "reason": err,
        })
        return rows, [{"statute_id": gid, "base_id": base_id, "error": err}]

    if result.error:
        rows.append({
            "statute_id": gid,
            "amendment_id": base_id,
            "signal_type": "invariant_violation",
            "category": _category(result.error),
            "description": result.error,
            "target_scope": "",
            "reason": result.error,
        })

    # Typed adjudication stream — the primary self-consistency surface.
    for adj in result.adjudications:
        detail = adj.detail
        signal = _classify_adjudication_kind(adj.kind, detail)
        if signal is None:
            continue
        reason = _detail_str(detail, "reason") or adj.kind
        rows.append({
            "statute_id": gid,
            "amendment_id": (adj.source_statute or "").removeprefix("ee/"),
            "signal_type": signal,
            "category": adj.kind,
            "description": adj.message,
            "target_scope": _scope_from_detail(detail),
            "reason": reason,
        })

    # Discovery-level dropped references (no adjudication carries these).
    for aid in result.amendments_failed:
        rows.append({
            "statute_id": gid,
            "amendment_id": aid,
            "signal_type": "skipped_amendment",
            "category": "amendment_failed",
            "description": "amendment dropped from chain (source fetch/parse failed)",
            "target_scope": "",
            "reason": "amendments_failed",
        })

    return rows, []


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _ROOT / "data" / "ee_riigiteataja.farchive"
_DEFAULT_CORPUS = _ROOT / "data" / "estonia" / "current_replayable_corpus.csv"

_LAW_SCHEMAS = frozenset({"tyviseadus", "muutmisseadus"})
_DECREE_SCHEMAS = frozenset({"maarus", "muutmismaarus", "juurakt"})


def _resolve_pairs(args) -> List[Tuple[str, str, str]]:
    """Resolve the (grupi_id, base_id, oracle_id) pairs to sweep."""
    corpus_path = Path(getattr(args, "ee_corpus", "") or _DEFAULT_CORPUS)
    if not corpus_path.exists():
        raise SystemExit(f"EE corpus CSV not found: {corpus_path}")

    explicit = {s.strip() for s in (getattr(args, "statutes", "") or "").split(",") if s.strip()}
    laws_only = getattr(args, "laws_only", False)
    allowed = _LAW_SCHEMAS | (frozenset() if laws_only else _DECREE_SCHEMAS)

    pairs: List[Tuple[str, str, str]] = []
    with open(corpus_path, newline="") as f:
        for row in csv.DictReader(f):
            schema = (row.get("schema") or "").strip()
            if not explicit and schema and schema not in allowed:
                continue
            gid = row["grupi_id"].strip()
            bid = row["base_id"].strip()
            oid = row["oracle_id"].strip()
            if explicit and not ({gid, bid, oid} & explicit):
                continue
            pairs.append((gid, bid, oid))

    limit = getattr(args, "limit", 0)
    if limit:
        pairs = pairs[:limit]
    return pairs


def _resolve_signal_filter(args) -> set[str]:
    raw = getattr(args, "signal_types", "") or ""
    requested = {s.strip() for s in raw.split(",") if s.strip()}
    if not requested:
        return set(EE_SIGNAL_TYPES)
    unknown = requested - set(EE_SIGNAL_TYPES)
    if unknown:
        raise SystemExit(
            f"unknown --signal-types {sorted(unknown)}; choose from {list(EE_SIGNAL_TYPES)}"
        )
    return requested


# ---------------------------------------------------------------------------
# Parallel sweep (per-worker archive open, mirroring ee-bench)
# ---------------------------------------------------------------------------

_WORKER_DB_PATH: str = ""


def _worker_project(item: Tuple[int, str, str, str]) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Top-level picklable worker: open a per-process archive, project one pair."""
    from lawvm.estonia.fetch import open_rt_archive

    idx, gid, base_id, oracle_id = item
    archive = open_rt_archive(Path(_WORKER_DB_PATH))
    try:
        rows, errs = _project_ee_pair(gid, base_id, oracle_id, archive)
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()
    return idx, rows, errs


def _sweep(
    pairs: Sequence[Tuple[str, str, str]],
    db_path: Path,
    workers: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from lawvm.estonia.fetch import open_rt_archive

    if workers <= 1 or len(pairs) <= 1:
        rows: List[Dict[str, Any]] = []
        errs: List[Dict[str, Any]] = []
        archive = open_rt_archive(db_path)
        try:
            for gid, base_id, oracle_id in pairs:
                r, e = _project_ee_pair(gid, base_id, oracle_id, archive)
                rows.extend(r)
                errs.extend(e)
        finally:
            close = getattr(archive, "close", None)
            if callable(close):
                close()
        return rows, errs

    from concurrent.futures import as_completed

    from lawvm.tools._worker_pool import managed_executor

    global _WORKER_DB_PATH
    _WORKER_DB_PATH = str(db_path)

    shard_rows: Dict[int, List[Dict[str, Any]]] = {}
    shard_errs: Dict[int, List[Dict[str, Any]]] = {}
    tasks = [(i, gid, base_id, oracle_id) for i, (gid, base_id, oracle_id) in enumerate(pairs)]

    def _init(db: str) -> None:
        global _WORKER_DB_PATH
        _WORKER_DB_PATH = db

    with managed_executor(workers, initializer=_init, initargs=(str(db_path),)) as pool:
        futures = {pool.submit(_worker_project, task): task[0] for task in tasks}
        for future in as_completed(futures):
            idx, r, e = future.result()
            shard_rows[idx] = r
            shard_errs[idx] = e

    rows = []
    errs = []
    for i in range(len(pairs)):
        rows.extend(shard_rows.get(i, []))
        errs.extend(shard_errs.get(i, []))
    return rows, errs


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    import os

    db_path = Path(getattr(args, "db", "") or _DEFAULT_DB)
    if not db_path.exists():
        print(f"EE archive not found: {db_path}", file=sys.stderr)
        print("Acquire it with: uv run lawvm ee-corpus acquire", file=sys.stderr)
        raise SystemExit(1)

    pairs = _resolve_pairs(args)
    signal_filter = _resolve_signal_filter(args)
    if not pairs:
        print("No EE pairs selected.", file=sys.stderr)
        raise SystemExit(1)

    requested = getattr(args, "workers", 0) or 0
    workers = requested if requested > 0 else max(8, os.cpu_count() or 4)
    workers = min(workers, 8)

    t0 = time.monotonic()
    rows, error_rows = _sweep(pairs, db_path, workers)
    elapsed = time.monotonic() - t0

    rows = [r for r in rows if r["signal_type"] in signal_filter]

    if getattr(args, "json", False):
        json.dump(
            {
                "jurisdiction": "ee",
                "elapsed_s": round(elapsed, 2),
                "pairs_swept": len(pairs),
                "signal_types": sorted(signal_filter),
                "replay_errors": error_rows,
                "signals": len(rows),
                "rows": rows,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=1,
            default=str,
        )
        sys.stdout.write("\n")
        return

    _print_report(rows, error_rows, pairs, elapsed)


def _print_report(
    rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    pairs: Sequence[Tuple[str, str, str]],
    elapsed: float,
) -> None:
    by_type = Counter(r["signal_type"] for r in rows)
    affected = len({r["statute_id"] for r in rows})
    rate = len(pairs) / elapsed if elapsed > 0 else 0.0

    print(
        f"Swept {len(pairs):,} EE pairs in {elapsed:.1f}s "
        f"({rate:.0f}/s); {len(error_rows)} replay error(s)"
    )
    print(f"{len(rows):,} self-consistency signal(s) across {affected:,} statutes\n")

    print("=== signals by type ===")
    for sig, n in by_type.most_common():
        statutes = len({r["statute_id"] for r in rows if r["signal_type"] == sig})
        print(f"{n:7d}  [{statutes:5d} statutes]  {sig}")
    print()

    for sig in [s for s in EE_SIGNAL_TYPES if by_type.get(s)]:
        sig_rows = [r for r in rows if r["signal_type"] == sig]
        print(f"=== {sig} ({len(sig_rows):,}) ===")
        by_cat = Counter(r["category"] for r in sig_rows)
        cat_statutes: Dict[str, set] = defaultdict(set)
        samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in sig_rows:
            cat_statutes[r["category"]].add(r["statute_id"])
            if len(samples[r["category"]]) < 3:
                samples[r["category"]].append(r)
        for cat, n in by_cat.most_common():
            print(f"  {n:6d}  [{len(cat_statutes[cat]):4d} statutes]  {cat}")
            for r in samples[cat]:
                scope = f" {{{r['target_scope']}}}" if r["target_scope"] else ""
                print(
                    f"            {r['statute_id']} <- {r['amendment_id'] or '?'}:"
                    f" {r['description']}{scope}"
                )
        print()

    if error_rows:
        print(f"=== replay errors ({len(error_rows)}) ===")
        for er in error_rows[:20]:
            print(f"  {er.get('statute_id')}: {er.get('error')}")
