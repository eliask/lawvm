"""lawvm bench -j uk — UK legislation enacted-vs-oracle EID bench.

Scores each statute by comparing EIDs from the enacted XML against EIDs
from the consolidated (current) XML.  This is the baseline before replay
is wired in; once the replayer exists it will slot in between.

Scoring formula: |enacted_eids ∩ oracle_eids| / max(|enacted|, |oracle|)
(Jaccard-style — penalises both missing and extra EIDs equally.)

Usage (from LawVM/):
    lawvm bench -j uk --label v1
    lawvm bench -j uk --label v2 --types ukpga asp
    lawvm bench -j uk --show v1
    lawvm bench -j uk --compare v1 v2
    lawvm bench -j uk --history
    lawvm bench -j uk --corpus-csv   # build/refresh data/uk/bench_corpus.csv
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, cast

if TYPE_CHECKING:
    from lawvm.core.ir import IRStatute

import Levenshtein

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import is_zombie
from farchive import Farchive
from lawvm.uk_legislation.uk_grafter import (
    extract_eid_map_bytes,
    parse_uk_statute_ir_bytes,
)
from lawvm.uk_legislation.source_adjudication import (
    classify_uk_bench_comparison,
    is_core_uk_comparison,
    normalize_uk_replay_compare_eids,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_BENCH_DIR = _REPO_ROOT / "data" / "uk_bench_runs"
_HISTORY_CSV = _REPO_ROOT / "data" / "uk_benchmark_history.csv"
_CORPUS_CSV = _REPO_ROOT / "data" / "uk" / "bench_corpus.csv"

# Module-level state for worker processes (set before spawning ProcessPoolExecutor).
_WORKER_DB_PATH: str = ""
_WORKER_DO_REPLAY: bool = False
_WORKER_REPO_ROOT: str = ""
_WORKER_DO_COMMENCEMENT: bool = False

_LEG_BASE = "https://www.legislation.gov.uk"

# Primary act types to include by default
_DEFAULT_TYPES = frozenset(["ukpga", "asp", "asc", "nia"])


# ---------------------------------------------------------------------------
# EID helpers
# ---------------------------------------------------------------------------


def _collect_eids(nodes: Sequence[IRNode], pit_date: Optional[str] = None) -> Set[str]:
    """Recursively collect all non-zombie eId/id attrs from an IR tree."""
    eids: Set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            eids.add(eid)
        eids.update(_collect_eids(n.children, pit_date=pit_date))
    return eids


def _score_eids(enacted_eids: Set[str], oracle_eids: Set[str]) -> float:
    """Jaccard-style EID similarity score."""
    common = enacted_eids & oracle_eids
    denom = max(len(enacted_eids), len(oracle_eids), 1)
    return len(common) / denom


# ---------------------------------------------------------------------------
# Text-similarity helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation, collapse whitespace.

    Mirrors the normalization used by extract_eid_map / _normalize_text_for_grounding
    in uk_grafter.py so enacted/replayed and oracle texts are on equal footing.
    """
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


def _collect_text(node: IRNode) -> str:
    """Concatenate all text from node and descendants."""
    parts = []
    if node.text:
        parts.append(node.text.strip())
    for child in node.children:
        child_text = _collect_text(child)
        if child_text:
            parts.append(child_text)
    return " ".join(parts)


def _extract_eid_texts(ir: "IRStatute", eids: Set[str]) -> Dict[str, str]:
    """Map EID → normalized text content for the given set of EIDs.

    Walks body children and schedules; only collects nodes whose eId/id
    attr is in *eids*.  The returned text is normalized with _normalize_text
    to match the oracle text_map produced by extract_eid_map.
    """
    texts: Dict[str, str] = {}

    def _walk(node: IRNode) -> None:
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid and eid in eids:
            raw = _collect_text(node)
            if raw:
                texts[eid] = _normalize_text(raw)
        for child in node.children:
            _walk(child)

    for child in ir.body.children:
        _walk(child)
    for sch in ir.supplements:
        _walk(sch)
    return texts


def _text_similarity_score(
    source_texts: Dict[str, str],
    oracle_texts: Dict[str, str],
) -> tuple[float, int]:
    """Average Levenshtein ratio across common EIDs that have non-empty text.

    Returns (score, n_compared).  score is -1.0 when no EIDs are comparable.
    """
    common = set(source_texts) & set(oracle_texts)
    # Only compare EIDs where both sides have actual text
    pairs = [(source_texts[e], oracle_texts[e]) for e in common if source_texts[e] and oracle_texts[e]]
    if not pairs:
        return -1.0, 0
    total = sum(Levenshtein.ratio(s, o) for s, o in pairs)
    return total / len(pairs), len(pairs)


# ---------------------------------------------------------------------------
# Corpus enumeration from Farchive
# ---------------------------------------------------------------------------


def _extract_sid_from_url(url: str, suffix: str) -> Optional[str]:
    """Extract 'type/year/num' from a legislation.gov.uk URL with a given suffix."""
    prefix = f"{_LEG_BASE}/"
    if not url.startswith(prefix):
        return None
    path = url[len(prefix) :]
    if not path.endswith(suffix):
        return None
    sid = path[: -len(suffix)]
    # Must match type/year/num pattern
    if re.fullmatch(r"[a-z]+/\d{4}/\d+", sid):
        return sid
    return None


def _build_corpus_index(
    archive: Farchive,
    types: Optional[frozenset[str]] = None,
) -> list[dict]:
    """Enumerate statutes in the archive that have both enacted and current XML.

    Returns list of dicts: {statute_id, type, year, has_enacted, has_consolidated,
                            n_effects, enacted_url, current_url}.
    """
    conn = archive._conn

    # Find all enacted XML URLs
    enacted_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE '%/enacted/data.xml'"
    ).fetchall()

    enacted_sids: dict[str, str] = {}  # sid -> url
    for (url,) in enacted_rows:
        sid = _extract_sid_from_url(url, "/enacted/data.xml")
        if sid:
            act_type = sid.split("/")[0]
            if types is None or act_type in types:
                enacted_sids[sid] = url

    # Find all current (consolidated) XML URLs — not /enacted/ and not /changes/
    current_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span "
        "WHERE locator LIKE '%/data.xml' "
        "  AND locator NOT LIKE '%/enacted/%' "
        "  AND locator NOT LIKE '%/changes/%'"
    ).fetchall()

    current_sids: dict[str, str] = {}  # sid -> url
    for (url,) in current_rows:
        sid = _extract_sid_from_url(url, "/data.xml")
        if sid:
            act_type = sid.split("/")[0]
            if types is None or act_type in types:
                current_sids[sid] = url

    # Find statutes with effects feeds (any page)
    effects_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE '%/data.feed%'"
    ).fetchall()

    effects_counts: Counter[str] = Counter()
    for (url,) in effects_rows:
        # URL: /changes/affected/TYPE/YEAR/NUM/data.feed
        m = re.search(r"/changes/affected/([^/]+)/(\d+)/(\d+)/data\.feed", url)
        if m:
            sid = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
            effects_counts[sid] += 1

    # Build corpus: only statutes with both enacted AND current
    both = set(enacted_sids) & set(current_sids)
    entries = []
    for sid in sorted(both):
        parts = sid.split("/")
        act_type, year = parts[0], parts[1]
        entries.append(
            {
                "statute_id": sid,
                "type": act_type,
                "year": int(year),
                "has_enacted": True,
                "has_consolidated": True,
                "n_effects": effects_counts.get(sid, 0),
                "enacted_url": enacted_sids[sid],
                "current_url": current_sids[sid],
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Bench result
# ---------------------------------------------------------------------------


@dataclass
class _BenchResult:
    statute_id: str
    act_type: str
    year: int
    n_effects: int
    n_enacted_eids: int
    n_oracle_eids: int
    n_common: int
    score: float
    status: str
    error: str = ""
    # Replay fields (populated only when --replay is active)
    n_replayed_eids: int = 0
    n_replay_common: int = 0
    replay_score: float = -1.0  # -1 = not attempted
    n_ops: int = 0
    # Text-similarity fields (common EIDs, Levenshtein ratio)
    text_score: float = -1.0  # enacted vs oracle; -1 = not computed
    n_text_compared: int = 0  # number of EIDs compared for text_score
    replay_text_score: float = -1.0  # replayed vs oracle; -1 = not computed
    # Commencement-filtered fields (populated only when --commencement is active)
    commencement_score: float = -1.0  # enacted (commenced only) vs oracle; -1 = not computed
    n_commenced_eids: int = 0  # how many enacted EIDs are commenced
    replay_commencement_score: float = -1.0  # replayed (commenced only) vs oracle; -1 = not computed
    comparison_class: str = ""
    core_benchmark: bool = True


# ---------------------------------------------------------------------------
# Score one statute
# ---------------------------------------------------------------------------


def _score_statute(
    entry: dict,
    archive: Farchive,
    do_replay: bool = False,
    repo_root: Optional[Path] = None,
    do_commencement: bool = False,
) -> _BenchResult:
    sid = entry["statute_id"]
    act_type = entry["type"]
    year = entry["year"]
    n_effects = entry["n_effects"]

    enacted_url = entry["enacted_url"]
    current_url = entry["current_url"]

    try:
        enacted_bytes = archive.get(enacted_url)
        if not enacted_bytes or len(enacted_bytes) < 100:
            return _BenchResult(
                statute_id=sid,
                act_type=act_type,
                year=year,
                n_effects=n_effects,
                n_enacted_eids=0,
                n_oracle_eids=0,
                n_common=0,
                score=0.0,
                status="NO_ENACTED",
                error="enacted XML missing or empty",
                comparison_class="no_enacted_eids",
                core_benchmark=False,
            )

        oracle_bytes = archive.get(current_url)
        if not oracle_bytes or len(oracle_bytes) < 100:
            return _BenchResult(
                statute_id=sid,
                act_type=act_type,
                year=year,
                n_effects=n_effects,
                n_enacted_eids=0,
                n_oracle_eids=0,
                n_common=0,
                score=0.0,
                status="NO_ORACLE",
                error="current XML missing or empty",
                comparison_class="no_oracle_eids",
                core_benchmark=False,
            )

        enacted_ir = parse_uk_statute_ir_bytes(
            enacted_bytes,
            statute_id=sid,
            version_label="enacted",
            source_path=enacted_url,
        )
        oracle_eid_data = extract_eid_map_bytes(oracle_bytes)
        oracle_eids: Set[str] = set(oracle_eid_data.get("eid_map", {}).values())

        enacted_eids = _collect_eids(enacted_ir.body.children)
        for s in enacted_ir.supplements:
            enacted_eids.update(_collect_eids([s]))

        common = enacted_eids & oracle_eids
        score = _score_eids(enacted_eids, oracle_eids)

        # ── Text similarity: enacted vs oracle ─────────────────────────
        oracle_text_map: Dict[str, str] = oracle_eid_data.get("text_map", {})
        enacted_texts = _extract_eid_texts(enacted_ir, common)
        text_score, n_text_compared = _text_similarity_score(enacted_texts, oracle_text_map)

        # ── Optional replay ────────────────────────────────────────────
        n_ops = 0
        n_replayed_eids = 0
        n_replay_common = 0
        replay_score = -1.0
        replay_text_score = -1.0
        replayed_ir = None  # may be set below if do_replay succeeds

        if do_replay and repo_root is not None:
            try:
                from lawvm.uk_legislation.uk_amendment_replay import (
                    UKReplayPipeline,
                    replay_uk_ops,
                )
                from lawvm.uk_legislation.oracle_align import align_uk_replay_to_oracle

                pipeline = UKReplayPipeline(repo_root)
                ops = pipeline.compile_ops_for_statute(sid, archive=archive)
                n_ops = len(ops)
                eid_map = oracle_eid_data.get("eid_map", {})
                text_map = oracle_eid_data.get("text_map", {})
                replayed_ir = replay_uk_ops(enacted_ir, ops, eid_map=eid_map, text_map=text_map)
                replayed_ir = align_uk_replay_to_oracle(
                    replayed_ir,
                    eid_map=eid_map,
                    text_map=text_map,
                )
                replayed_eids = _collect_eids(replayed_ir.body.children)
                for s in replayed_ir.supplements:
                    replayed_eids.update(_collect_eids([s]))
                n_replayed_eids = len(replayed_eids)
                replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                    replayed_eids,
                    oracle_eids,
                )
                replay_common = replay_compare_eids & oracle_compare_eids
                n_replay_common = len(replay_common)
                replay_score = _score_eids(replay_compare_eids, oracle_compare_eids)
                replayed_texts = _extract_eid_texts(replayed_ir, replayed_eids & oracle_eids)
                replay_text_score, _ = _text_similarity_score(replayed_texts, oracle_text_map)
            except Exception as replay_exc:
                # Replay failure is non-fatal — record it but keep enacted score.
                # Log the error so it is visible in bench output (not silently swallowed).
                print(
                    f"  REPLAY ERROR {sid}: {type(replay_exc).__name__}: {replay_exc}",
                    file=sys.stderr,
                )
                replay_score = -1.0
                n_ops = -1  # signals error

        # ── Optional commencement filtering ─────────────────────────────
        commencement_score = -1.0
        n_commenced_eids = 0
        replay_commencement_score = -1.0

        if do_commencement:
            try:
                from lawvm.uk_legislation.uk_amendment_replay import (
                    load_effects_for_statute_from_archive,
                    commencement_eid_set,
                )

                all_effects = load_effects_for_statute_from_archive(sid, archive)
                commenced = commencement_eid_set(all_effects, enacted_ir)
                commenced_enacted = enacted_eids & commenced
                n_commenced_eids = len(commenced_enacted)
                commencement_score = _score_eids(commenced_enacted, oracle_eids)
                if replayed_ir is not None:
                    _replayed_eids_all = _collect_eids(replayed_ir.body.children)
                    for s in replayed_ir.supplements:
                        _replayed_eids_all.update(_collect_eids([s]))
                    commenced_replayed = _replayed_eids_all & commenced
                    replay_commencement_score = _score_eids(commenced_replayed, oracle_eids)
            except Exception as comm_exc:
                print(
                    f"  COMMENCEMENT ERROR {sid}: {type(comm_exc).__name__}: {comm_exc}",
                    file=sys.stderr,
                )

        comparison_class = classify_uk_bench_comparison(
            n_enacted_eids=len(enacted_eids),
            n_oracle_eids=len(oracle_eids),
            n_effects=n_effects,
            raw_score=score,
        )

        return _BenchResult(
            statute_id=sid,
            act_type=act_type,
            year=year,
            n_effects=n_effects,
            n_enacted_eids=len(enacted_eids),
            n_oracle_eids=len(oracle_eids),
            n_common=len(common),
            score=score,
            status="OK",
            n_replayed_eids=n_replayed_eids,
            n_replay_common=n_replay_common,
            replay_score=replay_score,
            n_ops=n_ops,
            text_score=text_score,
            n_text_compared=n_text_compared,
            replay_text_score=replay_text_score,
            commencement_score=commencement_score,
            n_commenced_eids=n_commenced_eids,
            replay_commencement_score=replay_commencement_score,
            comparison_class=comparison_class,
            core_benchmark=is_core_uk_comparison(comparison_class),
        )

    except Exception as exc:
        # One bad statute must not abort the whole bench run, so we catch broadly
        # here.  But the error must be visible — include the exception type so
        # programming bugs (NameError, TypeError, …) are distinguishable from
        # expected failures (ET.ParseError, FileNotFoundError).
        return _BenchResult(
            statute_id=sid,
            act_type=act_type,
            year=year,
            n_effects=n_effects,
            n_enacted_eids=0,
            n_oracle_eids=0,
            n_common=0,
            score=0.0,
            status="ERR",
            error=f"{type(exc).__name__}: {exc}"[:200],
            comparison_class="exception",
            core_benchmark=False,
        )


# ---------------------------------------------------------------------------
# Run bench
# ---------------------------------------------------------------------------


def _score_statute_worker(entry: dict) -> _BenchResult:
    """Top-level picklable wrapper for parallel execution.

    Opens its own Farchive per worker process using module-level globals
    set before the ProcessPoolExecutor is spawned.
    """
    archive = Farchive(_WORKER_DB_PATH)
    try:
        return _score_statute(
            entry,
            archive,
            do_replay=_WORKER_DO_REPLAY,
            repo_root=Path(_WORKER_REPO_ROOT) if _WORKER_REPO_ROOT else None,
            do_commencement=_WORKER_DO_COMMENCEMENT,
        )
    finally:
        archive.close()


def _run_bench(
    corpus: list[dict],
    archive: Farchive,
    do_replay: bool = False,
    repo_root: Optional[Path] = None,
    workers: int = 1,
    do_commencement: bool = False,
) -> list[_BenchResult]:
    total = len(corpus)
    t0 = time.time()

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        # Communicate config to worker processes via module globals.
        global _WORKER_DB_PATH, _WORKER_DO_REPLAY, _WORKER_REPO_ROOT, _WORKER_DO_COMMENCEMENT
        _WORKER_DB_PATH = str(archive._db_path)
        _WORKER_DO_REPLAY = do_replay
        _WORKER_REPO_ROOT = str(repo_root) if repo_root is not None else ""
        _WORKER_DO_COMMENCEMENT = do_commencement

        results: list[Optional[_BenchResult]] = [None] * total
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {pool.submit(_score_statute_worker, entry): i for i, entry in enumerate(corpus)}
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    ok = sum(1 for x in results if x is not None and x.status == "OK")
                    avg = sum(x.score for x in results if x is not None and x.status == "OK") / max(ok, 1)
                    rate = done / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{done}/{total}] {elapsed:.0f}s  {rate:.1f}/s  ok={ok}  avg={avg:.1%}",
                        file=sys.stderr,
                    )
        return cast(List[_BenchResult], results)

    # Sequential fallback.
    results_seq = []
    for i, entry in enumerate(corpus):
        r = _score_statute(
            entry,
            archive,
            do_replay=do_replay,
            repo_root=repo_root,
            do_commencement=do_commencement,
        )
        results_seq.append(r)

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            ok = sum(1 for x in results_seq if x.status == "OK")
            avg = sum(x.score for x in results_seq if x.status == "OK") / max(ok, 1)
            print(
                f"  [{i + 1}/{total}] {elapsed:.0f}s  ok={ok}  avg={avg:.1%}",
                file=sys.stderr,
            )

    return results_seq


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(results: list[_BenchResult], label: str) -> None:
    ok = [r for r in results if r.status == "OK" and r.n_oracle_eids > 0]
    core = [r for r in ok if r.core_benchmark]
    noncore = [r for r in ok if not r.core_benchmark]
    no_oracle = [r for r in results if r.status in ("NO_ORACLE", "NO_ENACTED")]
    errs = [r for r in results if r.status == "ERR"]

    print(f"\n=== UK Bench: {label} ===")
    print(f"Total: {len(results)}, OK: {len(ok)}, No-oracle: {len(no_oracle)}, Errors: {len(errs)}")

    if not ok:
        print("No valid results to report.")
        return

    # Determine whether commencement scores are available — use as primary when yes.
    comm_scored = [r for r in ok if r.commencement_score >= 0.0]
    has_commencement = bool(comm_scored)

    avg_raw = sum(r.score for r in ok) / len(ok)
    med_score_raw = sorted(r.score for r in ok)[len(ok) // 2]
    perfect_raw = sum(1 for r in ok if r.score == 1.0)
    ge90_raw = sum(1 for r in ok if r.score >= 0.9)
    ge80_raw = sum(1 for r in ok if r.score >= 0.8)
    with_effects = sum(1 for r in ok if r.n_effects > 0)
    if core:
        core_avg_raw = sum(r.score for r in core) / len(core)
        print(f"Core benchmark rows: {len(core)}  raw avg={core_avg_raw:.1%}")
    if noncore:
        counts = Counter(r.comparison_class for r in noncore)
        print("Non-core classes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if has_commencement:
        # Commencement scores are primary; raw scores shown as secondary.
        avg_comm = sum(r.commencement_score for r in comm_scored) / len(comm_scored)
        med_comm = sorted(r.commencement_score for r in comm_scored)[len(comm_scored) // 2]
        perfect_comm = sum(1 for r in comm_scored if r.commencement_score == 1.0)
        ge90_comm = sum(1 for r in comm_scored if r.commencement_score >= 0.9)
        ge80_comm = sum(1 for r in comm_scored if r.commencement_score >= 0.8)
        avg_commenced_n = sum(r.n_commenced_eids for r in comm_scored) / len(comm_scored)
        print(f"\nEID score (commenced, N={len(comm_scored)}):")
        print(f"  Average:        {avg_comm:.1%}    (unfiltered: {avg_raw:.1%})")
        print(f"  Median:         {med_comm:.1%}    (unfiltered: {med_score_raw:.1%})")
        print(
            f"  Perfect (1.0):  {perfect_comm} ({100 * perfect_comm / len(comm_scored):.0f}%)"
            f"    (unfiltered: {perfect_raw})"
        )
        print(f"  >=90%%:          {ge90_comm} ({100 * ge90_comm / len(comm_scored):.0f}%)    (unfiltered: {ge90_raw})")
        print(f"  >=80%%:          {ge80_comm} ({100 * ge80_comm / len(comm_scored):.0f}%)    (unfiltered: {ge80_raw})")
        print(f"  Avg commenced EIDs: {avg_commenced_n:.0f}")
        print(f"  With effects>0: {with_effects}")
        if core:
            core_comm = [r for r in core if r.commencement_score >= 0.0]
            if core_comm:
                avg_core_comm = sum(r.commencement_score for r in core_comm) / len(core_comm)
                print(f"  Core commenced avg: {avg_core_comm:.1%}")
    else:
        # No commencement data — show raw scores normally.
        print(f"\nEID similarity score (N={len(ok)}):")
        print(f"  Average:        {avg_raw:.1%}")
        print(f"  Median:         {med_score_raw:.1%}")
        print(f"  Perfect (1.0):  {perfect_raw} ({100 * perfect_raw / len(ok):.0f}%)")
        print(f"  >=90%%:          {ge90_raw} ({100 * ge90_raw / len(ok):.0f}%)")
        print(f"  >=80%%:          {ge80_raw} ({100 * ge80_raw / len(ok):.0f}%)")
        print(f"  With effects>0: {with_effects}")

    # Replay summary (only when --replay was active)
    replayed = [r for r in ok if r.replay_score >= 0.0]
    if replayed:
        avg_replay_raw = sum(r.replay_score for r in replayed) / len(replayed)
        avg_enacted_raw = sum(r.score for r in replayed) / len(replayed)
        perfect_replay_raw = sum(1 for r in replayed if r.replay_score == 1.0)
        total_ops = sum(r.n_ops for r in replayed if r.n_ops >= 0)

        if has_commencement:
            replay_comm_scored = [r for r in replayed if r.replay_commencement_score >= 0.0]
            if replay_comm_scored:
                avg_replay_comm = sum(r.replay_commencement_score for r in replay_comm_scored) / len(replay_comm_scored)
                avg_enacted_comm = sum(
                    r.commencement_score for r in replay_comm_scored if r.commencement_score >= 0.0
                ) / max(sum(1 for r in replay_comm_scored if r.commencement_score >= 0.0), 1)
                delta_comm = avg_replay_comm - avg_enacted_comm
                # Use commencement scores for improved/regressed counts and delta ranking.
                # Previously r.replay_score (raw) was compared against r.score (commencement),
                # producing phantom regressions for recently-enacted 0-ops statutes where the
                # raw replay score is low (many enacted EIDs not in oracle) but the commencement
                # score is fine (only commenced EIDs compared, which match well).
                improved_comm = sum(
                    1
                    for r in replay_comm_scored
                    if r.commencement_score >= 0.0 and r.replay_commencement_score > r.commencement_score + 0.001
                )
                regressed_comm = sum(
                    1
                    for r in replay_comm_scored
                    if r.commencement_score >= 0.0 and r.replay_commencement_score < r.commencement_score - 0.001
                )
                perfect_replay_comm = sum(1 for r in replay_comm_scored if r.replay_commencement_score == 1.0)
                print(f"\nReplay (commenced, N={len(replay_comm_scored)}, {total_ops} ops total):")
                print(f"  Enacted avg:    {avg_enacted_comm:.1%}    (unfiltered: {avg_enacted_raw:.1%})")
                print(
                    f"  Replayed avg:   {avg_replay_comm:.1%} ({delta_comm:+.1%})    (unfiltered: {avg_replay_raw:.1%})"
                )
                print(
                    f"  Perfect replay: {perfect_replay_comm} ({100 * perfect_replay_comm / len(replay_comm_scored):.0f}%)"
                )
                print(f"  Improved:       {improved_comm}  Regressed: {regressed_comm}")
                # Show biggest improvements and regressions by commencement score delta.
                by_delta = sorted(
                    replay_comm_scored,
                    key=lambda r: r.replay_commencement_score - r.commencement_score
                    if r.commencement_score >= 0.0
                    else 0.0,
                )
                if regressed_comm:
                    print("\n  Top regressions:")
                    for r in by_delta[:5]:
                        if r.commencement_score >= 0.0 and r.replay_commencement_score < r.commencement_score - 0.001:
                            print(
                                f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                                f"  ops={r.n_ops}"
                            )
                if improved_comm:
                    print("\n  Top improvements:")
                    for r in reversed(by_delta[-5:]):
                        if r.commencement_score >= 0.0 and r.replay_commencement_score > r.commencement_score + 0.001:
                            print(
                                f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                                f"  ops={r.n_ops}"
                            )
        else:
            improved = sum(1 for r in replayed if r.replay_score > r.score + 0.001)
            regressed = sum(1 for r in replayed if r.replay_score < r.score - 0.001)
            delta = avg_replay_raw - avg_enacted_raw
            print(f"\nReplay score (N={len(replayed)}, {total_ops} ops total):")
            print(f"  Enacted avg:    {avg_enacted_raw:.1%}")
            print(f"  Replayed avg:   {avg_replay_raw:.1%} ({delta:+.1%})")
            print(f"  Perfect replay: {perfect_replay_raw} ({100 * perfect_replay_raw / len(replayed):.0f}%)")
            print(f"  Improved:       {improved}  Regressed: {regressed}")
            by_delta = sorted(replayed, key=lambda r: r.replay_score - r.score)
            if regressed:
                print("\n  Top regressions:")
                for r in by_delta[:5]:
                    if r.replay_score < r.score - 0.001:
                        print(f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  ops={r.n_ops}")
            if improved:
                print("\n  Top improvements:")
                for r in reversed(by_delta[-5:]):
                    if r.replay_score > r.score + 0.001:
                        print(f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  ops={r.n_ops}")

    # Text similarity summary
    text_scored = [r for r in ok if r.text_score >= 0.0]
    if text_scored:
        n_compared_total = sum(r.n_text_compared for r in text_scored)
        avg_text_enacted = sum(r.text_score for r in text_scored) / len(text_scored)
        print(f"\nText similarity (common EIDs, N={n_compared_total} EIDs across {len(text_scored)} statutes):")
        print(f"  Enacted avg:    {avg_text_enacted:.1%}")
        replay_text_scored = [r for r in text_scored if r.replay_text_score >= 0.0]
        if replay_text_scored:
            avg_text_replay = sum(r.replay_text_score for r in replay_text_scored) / len(replay_text_scored)
            avg_text_enacted_sub = sum(r.text_score for r in replay_text_scored) / len(replay_text_scored)
            delta_text = avg_text_replay - avg_text_enacted_sub
            print(f"  Replayed avg:   {avg_text_replay:.1%} ({delta_text:+.1%})")

    # By type
    type_groups: dict[str, list[_BenchResult]] = {}
    for r in ok:
        type_groups.setdefault(r.act_type, []).append(r)
    print("\nBy type:")
    for t, grp in sorted(type_groups.items()):
        a = sum(x.score for x in grp) / len(grp)
        p = sum(1 for x in grp if x.score == 1.0)
        replay_grp = [x for x in grp if x.replay_score >= 0.0]
        if replay_grp:
            ar = sum(x.replay_score for x in replay_grp) / len(replay_grp)
            print(f"  {t:<8} N={len(grp):5d}  enacted={a:.1%}  replay={ar:.1%}  perfect={p}")
        else:
            print(f"  {t:<8} N={len(grp):5d}  avg={a:.1%}  perfect={p}")

    # Worst rows: separate core replay frontier from non-core structural/no-truth rows
    worst_core = sorted([r for r in core if r.score < 1.0], key=lambda r: r.score)[:15]
    if worst_core:
        has_replay = any(r.replay_score >= 0.0 for r in worst_core)
        print(f"\nWorst {len(worst_core)} core rows (by EID score):")
        for r in worst_core:
            base = (
                f"  {r.statute_id:<30} score={r.score:.1%}  "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"common={r.n_common:4d} effects={r.n_effects:4d} "
                f"class={r.comparison_class}"
            )
            if has_replay and r.replay_score >= 0.0:
                base += f"  replay={r.replay_score:.1%} ops={r.n_ops}"
            print(base)
    worst_noncore = sorted(noncore, key=lambda r: r.score)[:10]
    if worst_noncore:
        print(f"\nWorst {len(worst_noncore)} non-core rows:")
        for r in worst_noncore:
            print(
                f"  {r.statute_id:<30} score={r.score:.1%} "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"effects={r.n_effects:4d} class={r.comparison_class}"
            )

    if errs:
        print(f"\nErrors ({len(errs)}):")
        for r in errs[:10]:
            print(f"  {r.statute_id}: {r.error}")


def _save_results(results: list[_BenchResult], label: str) -> None:
    _BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _BENCH_DIR / f"{label}.csv"

    has_replay = any(r.replay_score >= 0.0 for r in results)
    has_text = any(r.text_score >= 0.0 for r in results)
    has_commencement = any(r.commencement_score >= 0.0 for r in results)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        # When commencement is active, lead with commencement_score as primary
        # "score" column and keep raw EID score as "raw_score".
        if has_commencement:
            headers = [
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "raw_score",
                "n_commenced_eids",
                "status",
                "error",
                "comparison_class",
                "core_benchmark",
            ]
        else:
            headers = [
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "status",
                "error",
                "comparison_class",
                "core_benchmark",
            ]
        if has_replay:
            if has_commencement:
                headers += ["n_replayed_eids", "n_replay_common", "replay_score", "replay_commencement_score", "n_ops"]
            else:
                headers += ["n_replayed_eids", "n_replay_common", "replay_score", "n_ops"]
        if has_text:
            headers += ["text_score", "n_text_compared", "replay_text_score"]
        w.writerow(headers)
        for r in results:
            # Primary score = commencement_score when available, else raw score.
            primary_score = r.commencement_score if (has_commencement and r.commencement_score >= 0.0) else r.score
            if has_commencement:
                row = [
                    r.statute_id,
                    r.act_type,
                    r.year,
                    r.n_effects,
                    r.n_enacted_eids,
                    r.n_oracle_eids,
                    r.n_common,
                    f"{primary_score:.4f}",
                    f"{r.score:.4f}",
                    r.n_commenced_eids,
                    r.status,
                    r.error,
                    r.comparison_class,
                    "1" if r.core_benchmark else "0",
                ]
            else:
                row = [
                    r.statute_id,
                    r.act_type,
                    r.year,
                    r.n_effects,
                    r.n_enacted_eids,
                    r.n_oracle_eids,
                    r.n_common,
                    f"{r.score:.4f}",
                    r.status,
                    r.error,
                    r.comparison_class,
                    "1" if r.core_benchmark else "0",
                ]
            if has_replay:
                if has_commencement:
                    row += [
                        r.n_replayed_eids,
                        r.n_replay_common,
                        f"{r.replay_score:.4f}" if r.replay_score >= 0.0 else "",
                        f"{r.replay_commencement_score:.4f}" if r.replay_commencement_score >= 0.0 else "",
                        r.n_ops,
                    ]
                else:
                    row += [
                        r.n_replayed_eids,
                        r.n_replay_common,
                        f"{r.replay_score:.4f}" if r.replay_score >= 0.0 else "",
                        r.n_ops,
                    ]
            if has_text:
                row += [
                    f"{r.text_score:.4f}" if r.text_score >= 0.0 else "",
                    r.n_text_compared,
                    f"{r.replay_text_score:.4f}" if r.replay_text_score >= 0.0 else "",
                ]
            w.writerow(row)

    print(f"\nResults saved: {out_path}")

    # Append to history
    ok = [r for r in results if r.status == "OK" and r.n_oracle_eids > 0]
    if ok:
        # Use commencement score as primary when available.
        comm_ok = [r for r in ok if r.commencement_score >= 0.0]
        if comm_ok:
            avg = sum(r.commencement_score for r in comm_ok) / len(comm_ok)
            perfect = sum(1 for r in comm_ok if r.commencement_score == 1.0)
        else:
            avg = sum(r.score for r in ok) / len(ok)
            perfect = sum(1 for r in ok if r.score == 1.0)
        write_header = not _HISTORY_CSV.exists()
        with open(_HISTORY_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["label", "n_total", "n_ok", "avg_score", "n_perfect", "timestamp"])
            w.writerow(
                [
                    label,
                    len(results),
                    len(ok),
                    f"{avg:.4f}",
                    perfect,
                    time.strftime("%Y-%m-%d %H:%M"),
                ]
            )


# ---------------------------------------------------------------------------
# Show / compare
# ---------------------------------------------------------------------------


def _load_run(label: str) -> list[_BenchResult]:
    path = _BENCH_DIR / f"{label}.csv"
    if not path.exists():
        print(f"No saved run with label '{label}'. Available:", file=sys.stderr)
        for p in sorted(_BENCH_DIR.glob("*.csv")):
            print(f"  {p.stem}", file=sys.stderr)
        sys.exit(1)

    results = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rs_raw = row.get("replay_score", "")
            replay_score = float(rs_raw) if rs_raw else -1.0
            n_ops_raw = row.get("n_ops", "0")
            n_ops = int(n_ops_raw) if n_ops_raw else 0
            ts_raw = row.get("text_score", "")
            text_score = float(ts_raw) if ts_raw else -1.0
            rts_raw = row.get("replay_text_score", "")
            replay_text_score = float(rts_raw) if rts_raw else -1.0
            n_text_cmp_raw = row.get("n_text_compared", "0")
            n_text_compared = int(n_text_cmp_raw) if n_text_cmp_raw else 0
            # commencement_score is stored in the 'score' column (primary) when the
            # CSV has a 'raw_score' column (commencement-mode run).  A dedicated
            # 'commencement_score' column does not exist in the CSV — the save/load
            # convention is: score=commencement, raw_score=raw EID score when
            # commencement was active.
            has_raw_score_col = "raw_score" in (reader.fieldnames or [])
            if has_raw_score_col:
                # score column = commencement score; raw_score column = raw EID score
                commencement_score = float(row["score"])
            else:
                cs_raw = row.get("commencement_score", "")
                commencement_score = float(cs_raw) if cs_raw else -1.0
            n_commenced_raw = row.get("n_commenced_eids", "0")
            n_commenced_eids = int(n_commenced_raw) if n_commenced_raw else 0
            rcs_raw = row.get("replay_commencement_score", "")
            replay_commencement_score = float(rcs_raw) if rcs_raw else -1.0
            # When raw_score column is present, r.score holds the raw EID score
            # so that _print_report comparisons (r.replay_score vs r.score) remain
            # consistent (both are raw scores in the non-commencement branch).
            raw_score_val = float(row["raw_score"]) if has_raw_score_col else float(row["score"])
            results.append(
                _BenchResult(
                    statute_id=row["statute_id"],
                    act_type=row["act_type"],
                    year=int(row["year"]),
                    n_effects=int(row["n_effects"]),
                    n_enacted_eids=int(row["n_enacted_eids"]),
                    n_oracle_eids=int(row["n_oracle_eids"]),
                    n_common=int(row["n_common"]),
                    score=raw_score_val,
                    status=row["status"],
                    error=row.get("error", ""),
                    n_replayed_eids=int(row.get("n_replayed_eids", 0) or 0),
                    n_replay_common=int(row.get("n_replay_common", 0) or 0),
                    replay_score=replay_score,
                    n_ops=n_ops,
                    text_score=text_score,
                    n_text_compared=n_text_compared,
                    replay_text_score=replay_text_score,
                    commencement_score=commencement_score,
                    n_commenced_eids=n_commenced_eids,
                    replay_commencement_score=replay_commencement_score,
                    comparison_class=row.get("comparison_class", ""),
                    core_benchmark=row.get("core_benchmark", "1") in ("1", "True", "true"),
                )
            )
    return results


def _show_run(label: str) -> None:
    results = _load_run(label)
    _print_report(results, label)


def _compare_runs(label_a: str, label_b: str) -> None:
    def _load_scores(label: str) -> dict[str, float]:
        # Prefer commencement score as primary when available.
        return {
            r.statute_id: (r.commencement_score if r.commencement_score >= 0.0 else r.score) for r in _load_run(label)
        }

    a = _load_scores(label_a)
    b = _load_scores(label_b)
    common = set(a) & set(b)

    improved = [(k, a[k], b[k]) for k in common if b[k] > a[k] + 0.001]
    regressed = [(k, a[k], b[k]) for k in common if b[k] < a[k] - 0.001]

    avg_a = sum(a[k] for k in common) / len(common) if common else 0
    avg_b = sum(b[k] for k in common) / len(common) if common else 0

    print(f"\n=== UK Bench Compare: {label_a} -> {label_b} ===")
    print(f"Common statutes: {len(common)}")
    print(f"Average: {avg_a:.1%} -> {avg_b:.1%} ({avg_b - avg_a:+.1%})")
    print(f"Improved: {len(improved)}, Regressed: {len(regressed)}")

    if regressed:
        regressed.sort(key=lambda x: x[1] - x[2], reverse=True)
        print(f"\nRegressions (top {min(10, len(regressed))}):")
        for sid, va, vb in regressed[:10]:
            print(f"  {sid}: {va:.1%} -> {vb:.1%} ({vb - va:+.1%})")

    if improved:
        improved.sort(key=lambda x: x[2] - x[1], reverse=True)
        print(f"\nImprovements (top {min(10, len(improved))}):")
        for sid, va, vb in improved[:10]:
            print(f"  {sid}: {va:.1%} -> {vb:.1%} ({vb - va:+.1%})")


# ---------------------------------------------------------------------------
# Corpus CSV build
# ---------------------------------------------------------------------------


def _build_corpus_csv(archive: Farchive, types: Optional[frozenset[str]] = None) -> None:
    """Build or refresh data/uk/bench_corpus.csv from the archive."""
    print("Building UK bench corpus index from archive...")
    entries = _build_corpus_index(archive, types=types)
    print(f"  Found {len(entries)} statutes with enacted + consolidated XML")

    _CORPUS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(_CORPUS_CSV, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "statute_id",
                "type",
                "year",
                "has_enacted",
                "has_consolidated",
                "n_effects",
            ],
        )
        w.writeheader()
        for e in entries:
            w.writerow(
                {
                    "statute_id": e["statute_id"],
                    "type": e["type"],
                    "year": e["year"],
                    "has_enacted": str(e["has_enacted"]),
                    "has_consolidated": str(e["has_consolidated"]),
                    "n_effects": e["n_effects"],
                }
            )

    print(f"  Written: {_CORPUS_CSV}")
    tc = Counter(e["type"] for e in entries)
    for t, n in sorted(tc.items()):
        print(f"    {t}: {n}")


def _load_corpus_csv(
    types: Optional[frozenset[str]] = None,
    archive: Optional[Farchive] = None,
) -> list[dict]:
    """Load bench corpus from CSV (or build it if missing)."""
    if not _CORPUS_CSV.exists():
        if archive is None:
            raise FileNotFoundError(f"Corpus CSV not found: {_CORPUS_CSV}\nRun: lawvm bench -j uk --corpus-csv")
        _build_corpus_csv(archive, types=types)

    entries = []
    with open(_CORPUS_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            act_type = row["type"]
            if types and act_type not in types:
                continue
            enacted_url = f"{_LEG_BASE}/{row['statute_id']}/enacted/data.xml"
            current_url = f"{_LEG_BASE}/{row['statute_id']}/data.xml"
            entries.append(
                {
                    "statute_id": row["statute_id"],
                    "type": act_type,
                    "year": int(row["year"]),
                    "has_enacted": row["has_enacted"] == "True",
                    "has_consolidated": row["has_consolidated"] == "True",
                    "n_effects": int(row["n_effects"]),
                    "enacted_url": enacted_url,
                    "current_url": current_url,
                }
            )
    return entries


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(args) -> None:  # noqa: ANN001
    if args.history:
        if not _HISTORY_CSV.exists():
            print("No UK bench history yet. Run a bench first.")
            return
        with open(_HISTORY_CSV) as f:
            print(f.read())
        return

    if args.show:
        _show_run(args.show)
        return

    if args.compare:
        _compare_runs(args.compare[0], args.compare[1])
        return

    db_path = Path(args.db) if getattr(args, "db", None) else _DEFAULT_DB
    if not db_path.exists():
        print(f"Archive not found: {db_path}", file=sys.stderr)
        print("Run: uv run python scripts/acquire_uk_corpus.py", file=sys.stderr)
        sys.exit(1)

    archive = Farchive(db_path)

    # Determine type filter
    types_arg = getattr(args, "types", None)
    types_filter: Optional[frozenset[str]] = frozenset(types_arg) if types_arg else _DEFAULT_TYPES

    # --corpus-csv: build/refresh corpus CSV and exit
    if getattr(args, "corpus_csv", False):
        _build_corpus_csv(archive, types=types_filter)
        archive.close()
        return

    label = getattr(args, "label", None) or time.strftime("uk_%Y%m%d_%H%M")

    # Load corpus (build CSV if needed)
    print(f"Loading UK bench corpus (types: {sorted(types_filter or [])})...")
    corpus = _load_corpus_csv(types=types_filter, archive=archive)
    print(f"  Corpus: {len(corpus)} statutes")

    if not corpus:
        print("No statutes in corpus.", file=sys.stderr)
        archive.close()
        sys.exit(1)

    # Optional year filter
    min_year = getattr(args, "min_year", None)
    max_year = getattr(args, "max_year", None)
    if min_year:
        corpus = [e for e in corpus if e["year"] >= min_year]
    if max_year:
        corpus = [e for e in corpus if e["year"] <= max_year]
    if min_year or max_year:
        print(f"  Year filter: {min_year or '...'}-{max_year or '...'} → {len(corpus)} statutes")

    # Optional: apply --limit for quick smoke tests
    limit = getattr(args, "limit", None)
    if limit:
        corpus = corpus[:limit]
        print(f"  Limited to first {limit} statutes")

    do_replay = getattr(args, "replay", False)
    if do_replay:
        print("Replay mode: will run amendment replay for each statute")

    do_commencement = not getattr(args, "no_commencement", False)
    if do_commencement:
        print("Commencement mode: filtering EID scores to commenced provisions (use --no-commencement to disable)")

    # Parallelism: None means --parallel was not passed → default to cpu_count.
    # Pass --parallel 1 explicitly to force sequential (useful for debugging).
    _par = getattr(args, "parallel", None)
    workers = _par if _par is not None else max(8, os.cpu_count() or 4)
    print(f"Scoring {len(corpus)} statutes (workers={workers})...")
    results = _run_bench(
        corpus,
        archive,
        do_replay=do_replay,
        repo_root=_REPO_ROOT,
        workers=workers,
        do_commencement=do_commencement,
    )
    archive.close()

    _print_report(results, label)
    _save_results(results, label)
