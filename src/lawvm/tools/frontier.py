"""lawvm frontier — honest frontier report for Finland replay.

Combines the latest bench CSV with oracle-check classifications and strict-run
projection rows to produce a ranked list of non-suspect replay-loss statutes —
the "honest frontier" of what is actually fixable.

Currently, the worst-performing statutes in bench output mix together:
- Real replay failures (fixable)
- Oracle-suspect statutes (Finlex hasn't updated, not our bug)
- Editorial convention differences (unfixable)
- Source-incomplete statutes (missing amendment acts)

Usage:
    lawvm frontier --label v_post_merge --top 30
    lawvm frontier --label v_post_merge --top 30 --exclude-suspect
    lawvm frontier --label v_post_merge --strict-label strict_v1 --top 30
"""
from __future__ import annotations

import ast
import csv
import concurrent.futures
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.finland.corpus import get_consolidated_oracle_suspect_cache_only

FRESH_ORACLE_CHECK_LIMIT = 100
FRESH_SCORE_REFRESH_LIMIT = 100
PROVISIONAL_ORACLE_REFRESH_LIMIT = 50

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    """LawVM/data/ — sibling of src/."""
    here = Path(__file__).resolve()
    # src/lawvm/tools/frontier.py -> src/lawvm/tools -> src/lawvm -> src -> LawVM
    return here.parent.parent.parent.parent / "data"


def _bench_runs_dir() -> Path:
    return _data_dir() / "bench_runs"


def _strict_runs_dir() -> Path:
    return _data_dir() / "strict_runs"


def _frontier_reports_dir() -> Path:
    d = _data_dir() / "frontier_reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Step 1: Load bench results
# ---------------------------------------------------------------------------

def _load_bench_run(label: str) -> Optional[List[Dict]]:
    """Load per-statute results for a labeled bench run.

    Returns list of dicts with keys: statute_id, similarity, amendments.
    """
    runs_dir = _bench_runs_dir()
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]  # latest if multiple

    results = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["statute_id"]
            sim_str = row["similarity"]
            try:
                sim = float(sim_str)
            except ValueError:
                sim = -1.0
            try:
                amendments = int(row.get("amendments", 0))
            except (ValueError, TypeError):
                amendments = 0
            if sim >= 0:
                results.append({
                    "statute_id": sid,
                    "similarity": sim,
                    "amendments": amendments,
                })
    return results if results else None


def _load_corpus_subset(path: str) -> List[str]:
    subset: List[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            sid = row[1].strip()
            if sid and sid != "parent":
                subset.append(sid)
    return subset


def _filter_bench_data_to_corpus_ids(bench_data: List[Dict], statute_ids: List[str]) -> List[Dict]:
    wanted = set(statute_ids)
    return [row for row in bench_data if row["statute_id"] in wanted]


def _save_corpus_slice(rows: List[Dict], path: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seq", "parent"])
        for i, row in enumerate(rows, 1):
            w.writerow([i, row["statute_id"]])
    return out


# ---------------------------------------------------------------------------
# Step 2: Oracle-check classification (cached)
# ---------------------------------------------------------------------------

ORACLE_SUSPECT_CATEGORIES = {"ORACLE_STALE", "EDITORIAL_CONVENTION", "CORRIGENDUM_APPLIED"}
REPLAY_BUG_CATEGORIES = {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "MISSING", "EXTRA"}


def _classify_one_sync(sid: str, mode: str = "finlex_oracle"):
    """Run oracle_check._classify_statute for one statute. Used for fresh runs.

    Strip non-picklable fields (lxml elements in oracle_sections / replay_result)
    before returning, so the result can travel through multiprocessing queues.
    Only section_results, source_pathologies, html_topology, and scalar metadata
    fields are consumed by _run_oracle_checks_parallel.
    """
    from lawvm.tools.oracle_check import _classify_statute

    r = _run_quietly(_classify_statute, sid, mode=mode)
    if r is not None and hasattr(r, "oracle_sections"):
        # oracle_sections is Dict[str, lxml.etree._Element]; not picklable.
        # replay_result is an IRNode tree; not picklable via ProcessPool.
        r.oracle_sections = None
        r.replay_result = None
    return r


def _score_one_sync(sid: str, mode: str = "finlex_oracle") -> Tuple[str, float, str]:
    """Run the current full-text score for one statute."""
    from lawvm.tools.bench import _score_one

    return _run_quietly(_score_one, sid, mode=mode)


from lawvm.tools._evidence_helpers import _run_quietly  # noqa: E302


def _load_oracle_check_cache(db_path: Path) -> Dict[str, Dict]:
    """Load previously computed oracle-check results from divergences.db."""
    import sqlite3
    if not db_path.exists():
        return {}
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT statute_id, diagnosis, "
            "SUM(CASE WHEN COALESCE(blame_source, '') = '' THEN 1 ELSE 0 END) as n_unblamed, "
            "COUNT(*) as n_total "
            "FROM divergences GROUP BY statute_id, diagnosis"
        ).fetchall()
        signal_rows = con.execute(
            "SELECT statute_id, source_pathology, source_pathology_codes, source_pathology_rows_json, "
            "html_topology_mismatch, html_missing_from_xml, html_extra_in_xml, "
            "html_noncommensurable_reason, "
            "contingent_effective_sources "
            "FROM statute_signals"
        ).fetchall()
        con.close()
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        return {}

    by_sid: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_sid_unblamed: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sid, diag, n_unblamed, n_total in rows:
        by_sid[sid][diag] += n_total
        by_sid_unblamed[sid][diag] += n_unblamed

    result = {}
    for sid, counts in by_sid.items():
        total_divs = sum(counts.values())
        suspect = sum(v for k, v in counts.items() if k in ORACLE_SUSPECT_CATEGORIES)
        replay_bug = sum(v for k, v in counts.items() if k in REPLAY_BUG_CATEGORIES)
        replay_bug_unblamed = sum(
            v for k, v in by_sid_unblamed[sid].items() if k in REPLAY_BUG_CATEGORIES
        )
        suspect_frac = suspect / total_divs if total_divs else 0.0
        result[sid] = {
            "total_divergences": total_divs,
            "suspect_count": suspect,
            "replay_bug_count": replay_bug,
            "replay_bug_unblamed_count": replay_bug_unblamed,
            "replay_bug_unblamed_fraction": (
                replay_bug_unblamed / replay_bug if replay_bug else 0.0
            ),
            "suspect_fraction": suspect_frac,
            "top_diagnosis": max(counts, key=counts.__getitem__) if counts else "UNKNOWN",
        }
    for (
        sid,
        source_pathology,
        source_pathology_codes,
        source_pathology_rows_json,
        html_topology_mismatch,
        html_missing_from_xml,
        html_extra_in_xml,
        html_noncommensurable_reason,
        contingent_effective_sources,
    ) in signal_rows:
        info = result.setdefault(
            sid,
            {
                "total_divergences": 0,
                "suspect_count": 0,
                "replay_bug_count": 0,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "suspect_fraction": 0.0,
                "top_diagnosis": "UNKNOWN",
            },
        )
        info["source_pathology"] = bool(source_pathology)
        info["source_pathology_codes"] = _parse_string_listish(source_pathology_codes)
        source_pathology_rows: list[dict[str, Any]] = []
        raw_rows = str(source_pathology_rows_json or "")
        if raw_rows:
            try:
                loaded_rows = json.loads(raw_rows)
            except json.JSONDecodeError:
                loaded_rows = None
            if isinstance(loaded_rows, list):
                source_pathology_rows = [
                    item for item in loaded_rows if isinstance(item, dict)
                ]
        info["source_pathology_rows"] = source_pathology_rows
        info["html_topology_mismatch"] = bool(html_topology_mismatch)
        info["html_missing_from_xml"] = _parse_string_listish(html_missing_from_xml)
        info["html_extra_in_xml"] = _parse_string_listish(html_extra_in_xml)
        info["html_noncommensurable_reason"] = str(html_noncommensurable_reason or "")
        info["contingent_effective_sources"] = _parse_string_listish(contingent_effective_sources)
    return result


def _run_oracle_checks_parallel(
    sids: List[str],
    workers: int,
    mode: str = "finlex_oracle",
    progress: bool = True,
) -> Dict[str, Dict]:
    """Run fresh oracle-check classification for a list of statutes."""
    results: Dict[str, Dict] = {}
    total = len(sids)
    done = 0

    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futs = {executor.submit(_classify_one_sync, sid, mode): sid for sid in sids}
        for fut in concurrent.futures.as_completed(futs):
            sid = futs[fut]
            try:
                r = fut.result()
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception as e:
                from lawvm.tools.classify_result import ClassifyResult as _CR
                r = _CR(sid=sid, error=str(e)[:80])
            done += 1
            if progress and (done % 10 == 0 or done == total):
                print(f"  oracle-check [{done}/{total}]...", flush=True)
            if r is None or r.error:
                continue
            secs = r.section_results
            total_divs = len(secs)
            suspect = sum(1 for s in secs if s["diagnosis"] in ORACLE_SUSPECT_CATEGORIES)
            replay_bug = sum(1 for s in secs if s["diagnosis"] in REPLAY_BUG_CATEGORIES)
            replay_bug_unblamed = sum(
                1
                for s in secs
                if s["diagnosis"] in REPLAY_BUG_CATEGORIES and not s["blame_source"]
            )
            suspect_frac = suspect / total_divs if total_divs else 0.0
            counts: Dict[str, int] = defaultdict(int)
            for s in secs:
                counts[s["diagnosis"]] += 1
            results[sid] = {
                "total_divergences": total_divs,
                "suspect_count": suspect,
                "replay_bug_count": replay_bug,
                "replay_bug_unblamed_count": replay_bug_unblamed,
                "replay_bug_unblamed_fraction": (
                    replay_bug_unblamed / replay_bug if replay_bug else 0.0
                ),
                "suspect_fraction": suspect_frac,
                "top_diagnosis": max(counts, key=counts.__getitem__) if counts else "UNKNOWN",
                "source_pathology": bool(r.source_pathologies),
                "source_pathology_codes": [
                    str(p.get("code") or "")
                    for p in r.source_pathologies
                    if isinstance(p, dict) and str(p.get("code") or "")
                ],
                "html_topology_mismatch": bool((r.html_topology or {}).get("mismatch")),
                "html_missing_from_xml": [
                    str(v)
                    for v in (r.html_topology or {}).get("missing_from_xml", [])
                    if str(v)
                ],
                "html_extra_in_xml": [
                    str(v)
                    for v in (r.html_topology or {}).get("extra_in_xml", [])
                    if str(v)
                ],
                "html_noncommensurable_reason": str(
                    (r.html_topology or {}).get("noncommensurable_reason") or ""
                ),
                "contingent_effective_sources": [
                    str(v)
                    for v in r.contingent_effective_sources
                    if str(v)
                ],
            }

    return results


def _should_refresh_all_low_scoring(low_scoring: List[Dict]) -> bool:
    """Prefer fresh classification over cache when the frontier candidate set is small.

    The strict Finland core now tends to produce a small low-scoring set. In that
    regime, stale divergences.db rows are more harmful than the extra runtime:
    they keep already-fixed or base-drift statutes near the top of the frontier.
    """
    return len(low_scoring) <= FRESH_ORACLE_CHECK_LIMIT


def _should_refresh_all_low_scoring_scores(low_scoring: List[Dict]) -> bool:
    """Prefer fresh scores over stale run scores when the candidate set is small."""
    return len(low_scoring) <= FRESH_SCORE_REFRESH_LIMIT


def _select_provisional_candidate_refresh_sids(
    bench_data: List[Dict],
    oracle_checks: Dict[str, Dict],
    version_gates: Dict[str, Dict],
    strict_data: Optional[Dict[str, Dict]],
    score_threshold: float,
    top: int,
    exclude_suspect: bool,
    limit: int = PROVISIONAL_ORACLE_REFRESH_LIMIT,
) -> List[str]:
    """Pick a bounded provisional frontier slice for live oracle-check refresh.

    When the low-scoring pool is too large for a full refresh, stale cached
    oracle-check results can leak demoted statutes back into the apparent long
    tail. Refreshing the provisional top candidate rows is a cheap second pass
    that keeps the ranked frontier closer to current code.
    """
    provisional_top = _build_frontier(
        bench_data=bench_data,
        oracle_checks=oracle_checks,
        version_gates=version_gates,
        strict_data=strict_data,
        score_threshold=score_threshold,
        top=max(top * 3, top),
        exclude_suspect=exclude_suspect,
    )
    seen: set[str] = set()
    selected: List[str] = []
    for row in provisional_top:
        sid = str(row.get("statute_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        selected.append(sid)
        if len(selected) >= limit:
            break
    return selected


def _run_score_refresh_parallel(
    sids: List[str],
    workers: int,
    mode: str = "finlex_oracle",
    progress: bool = True,
) -> Dict[str, Dict]:
    """Run fresh benchmark scoring for a list of statutes."""
    results: Dict[str, Dict] = {}
    total = len(sids)
    done = 0

    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futs = {executor.submit(_score_one_sync, sid, mode): sid for sid in sids}
        for fut in concurrent.futures.as_completed(futs):
            sid = futs[fut]
            try:
                score_sid, sim, status = fut.result()
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception as e:
                score_sid, sim, status = sid, -1.0, str(e)[:80]
            done += 1
            if progress and (done % 10 == 0 or done == total):
                print(f"  score-refresh [{done}/{total}]...", flush=True)
            results[score_sid] = {
                "similarity": sim,
                "status": status,
            }

    return results


def _apply_refreshed_scores(
    bench_data: List[Dict],
    refreshed_scores: Dict[str, Dict],
) -> List[Dict]:
    """Return bench rows with refreshed current-code similarities applied."""
    if not refreshed_scores:
        return bench_data
    out: List[Dict] = []
    for item in bench_data:
        sid = item["statute_id"]
        refreshed = refreshed_scores.get(sid)
        if refreshed and refreshed.get("status") == "OK" and refreshed.get("similarity", -1.0) >= 0:
            out.append({
                **item,
                "similarity": float(refreshed["similarity"]),
            })
        else:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Step 3: Load strict run data (projection-row kinds)
# ---------------------------------------------------------------------------

def _load_strict_run(label: str) -> Optional[Dict[str, Dict]]:
    """Load per-statute strict run data. Returns {sid -> dict}."""
    runs_dir = _strict_runs_dir()
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]

    result = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get("statute_id", "").strip()
            if not sid:
                continue
            projection_kinds = _parse_string_listish(row.get("projection_kinds", ""))
            fail_reasons = _parse_string_listish(row.get("fail_reasons", ""))
            source_pathology_codes = _parse_string_listish(row.get("source_pathology_codes", ""))
            source_pathology_rows: list[dict[str, Any]] = []
            raw_rows = str(row.get("source_pathology_rows_json", "") or "")
            if raw_rows:
                try:
                    loaded_rows = json.loads(raw_rows)
                except json.JSONDecodeError:
                    loaded_rows = None
                if isinstance(loaded_rows, list):
                    source_pathology_rows = [
                        item for item in loaded_rows if isinstance(item, dict)
                    ]
            result[sid] = {
                "n_failed": int(row.get("n_failed", 0) or 0),
                "projection_kinds": projection_kinds,
                "source_pathology_codes": source_pathology_codes,
                "source_pathology_rows": source_pathology_rows,
                "contingent_effective_sources": _parse_string_listish(
                    row.get("contingent_effective_sources", "")
                ),
                "source_incomplete": row.get("source_incomplete", "0") in ("1", "True", "true"),
                "fail_reasons": fail_reasons,
            }
    return result if result else None


def _parse_string_listish(raw: str) -> List[str]:
    """Parse list-like CSV cells from strict-run artifacts.

    Older readers expected Python reprs like "['a', 'b']". Current strict-run
    CSVs write pipe-separated strings like "a|b". Accept both forms.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
        if isinstance(parsed, list):
            return [str(v) for v in parsed if str(v).strip()]
        return []
    return [part for part in raw.split("|") if part]


def _strict_marks_source_pathology(strict_row: Dict) -> bool:
    fail_reasons = {str(v) for v in strict_row.get("fail_reasons", [])}
    source_pathology_codes = {
        str(v) for v in strict_row.get("source_pathology_codes", []) if str(v)
    }
    return (
        "APPLY.SOURCE_PATHOLOGY_DETECTED" in fail_reasons
        or bool(source_pathology_codes)
    )


def _strict_marks_contingent_effective_date(strict_row: Dict) -> bool:
    fail_reasons = {str(v) for v in strict_row.get("fail_reasons", [])}
    contingent_effective_sources = {
        str(v) for v in strict_row.get("contingent_effective_sources", []) if str(v)
    }
    return (
        "TIME.CONTINGENT_EFFECTIVE_DATE" in fail_reasons
        or bool(contingent_effective_sources)
    )


def _source_pathology_signal(
    oracle_info: Optional[Dict],
    strict_row: Optional[Dict],
) -> tuple[bool, List[str]]:
    codes: List[str] = []
    signaled = False
    if oracle_info:
        signaled = bool(oracle_info.get("source_pathology"))
        codes.extend(
            str(code)
            for code in oracle_info.get("source_pathology_codes", [])
            if str(code)
        )
        if not codes:
            codes.extend(
                str(row.get("code") or "")
                for row in oracle_info.get("source_pathology_rows", [])
                if isinstance(row, dict) and str(row.get("code") or "")
            )
    if strict_row:
        signaled = signaled or _strict_marks_source_pathology(strict_row)
        codes.extend(
            str(code)
            for code in strict_row.get("source_pathology_codes", [])
            if str(code)
        )
        if not codes:
            codes.extend(
                str(row.get("code") or "")
                for row in strict_row.get("source_pathology_rows", [])
                if isinstance(row, dict) and str(row.get("code") or "")
            )
    return signaled, sorted(set(codes))


def _html_topology_signal(oracle_info: Optional[Dict]) -> tuple[bool, List[str], List[str]]:
    if not oracle_info:
        return False, [], []
    if str(oracle_info.get("html_noncommensurable_reason") or "").strip():
        return False, [], []
    missing_raw = [
        str(v)
        for v in oracle_info.get("html_missing_from_xml", [])
        if str(v)
    ]
    extra_raw = [
        str(v)
        for v in oracle_info.get("html_extra_in_xml", [])
        if str(v)
    ]
    missing: List[str] = []
    seen_missing: set[str] = set()
    for label in missing_raw:
        if label not in seen_missing:
            missing.append(label)
            seen_missing.add(label)
    extra: List[str] = []
    seen_extra: set[str] = set()
    for label in extra_raw:
        if label not in seen_extra:
            extra.append(label)
            seen_extra.add(label)
    signaled = bool(oracle_info.get("html_topology_mismatch")) or bool(missing or extra)
    return signaled, missing, extra


def _html_noncommensurable_signal(oracle_info: Optional[Dict]) -> str:
    if not oracle_info:
        return ""
    return str(oracle_info.get("html_noncommensurable_reason") or "").strip()


def _contingent_effective_date_signal(
    oracle_info: Optional[Dict],
    strict_row: Optional[Dict],
) -> tuple[bool, List[str]]:
    sources: List[str] = []
    signaled = False
    if oracle_info:
        live_sources = [
            str(v)
            for v in oracle_info.get("contingent_effective_sources", [])
            if str(v)
        ]
        if live_sources:
            signaled = True
            sources.extend(live_sources)
    if strict_row and _strict_marks_contingent_effective_date(strict_row):
        signaled = True
        sources.extend(
            str(v)
            for v in strict_row.get("contingent_effective_sources", [])
            if str(v)
        )
    return signaled, sorted(set(sources))


# ---------------------------------------------------------------------------
# Step 4: Score and rank
# ---------------------------------------------------------------------------

def _amendment_count_factor(n: int) -> float:
    """Scale factor based on amendment count. Fewer amendments = more tractable fix."""
    if n == 0:
        return 0.5   # 0-amendment failure is probably a structural issue
    if n <= 5:
        return 1.0
    if n <= 15:
        return 0.9
    if n <= 30:
        return 0.8
    return 0.7


def _compute_fixability(
    sim: float,
    oracle_info: Optional[Dict],
    version_gate: Optional[Dict],
    amendments: int,
    exclude_suspect: bool,
) -> Tuple[float, bool, str]:
    """Compute fixability score and suspect status.

    Returns (fixability, is_suspect, reason).
    """
    replay_loss = 1.0 - sim

    version_suspect = ""
    version_pending = ""
    if version_gate:
        version_suspect = version_gate.get("suspect_detail", "") or ""
        version_pending = version_gate.get("pending_detail", "") or ""

    if version_suspect:
        suspect_penalty = 0.05
        is_suspect = True
        reason = "ORACLE_VERSION_MISMATCH"
    elif version_pending:
        suspect_penalty = 0.2
        is_suspect = True
        reason = "ORACLE_VERSION_PENDING"
    elif oracle_info is None:
        # No oracle-check data means we do not have a commensurable truth
        # surface for ranking this row as an honest replay target.
        suspect_penalty = 0.05
        is_suspect = True
        reason = "NO_ORACLE_CHECK"
    else:
        replay_bug_count = int(oracle_info.get("replay_bug_count", 0) or 0)
        replay_bug_unblamed = int(oracle_info.get("replay_bug_unblamed_count", 0) or 0)
        unblamed_fraction = float(oracle_info.get("replay_bug_unblamed_fraction", 0.0) or 0.0)
        if replay_bug_count > 0 and replay_bug_unblamed == replay_bug_count:
            suspect_penalty = 0.05
            is_suspect = True
            reason = "BASE_DRIFT"
        elif replay_bug_count >= 5 and unblamed_fraction >= 0.8:
            suspect_penalty = 0.1
            is_suspect = True
            reason = "BASE_DRIFT"
        else:
            suspect_frac = oracle_info["suspect_fraction"]
            is_suspect = suspect_frac > 0.5
            reason = oracle_info["top_diagnosis"]
            # Penalize based on fraction of divergences that are oracle issues
            # 100% oracle-suspect -> 0.1x; 0% oracle-suspect -> 1.0x
            suspect_penalty = 1.0 - (suspect_frac * 0.9)

    amend_factor = _amendment_count_factor(amendments)

    fixability = replay_loss * suspect_penalty * amend_factor
    return fixability, is_suspect, reason


def _bucket_frontier_row(
    *,
    oracle_info: Optional[Dict],
    version_gate: Optional[Dict],
    _strict_row: Optional[Dict],
    similarity: float,
    amendments: int,
    source_pathology: bool,
    html_noncommensurable: bool,
    html_topology_mismatch: bool,
    contingent_effective_date: bool,
) -> str:
    """Classify a low-scoring statute into the same bucket precedence used in summaries."""
    _, base_suspect, reason = _compute_fixability(
        similarity,
        oracle_info,
        version_gate,
        amendments,
        exclude_suspect=False,
    )
    oracle_or_version = bool(version_gate) or (
        oracle_info is not None and float(oracle_info.get("suspect_fraction", 0.0) or 0.0) > 0.5
    )
    final_suspect = (
        base_suspect
        or source_pathology
        or html_noncommensurable
        or html_topology_mismatch
        or contingent_effective_date
    )

    if oracle_or_version:
        return "oracle_version_suspect"
    if reason == "NO_ORACLE_CHECK":
        return "no_oracle_check"
    if source_pathology:
        return "source_pathology"
    if html_noncommensurable:
        return "html_noncommensurable"
    if html_topology_mismatch:
        return "html_topology"
    if contingent_effective_date:
        return "contingent_effective_date"
    if reason == "BASE_DRIFT":
        return "base_drift"
    if final_suspect:
        return "other_suspect"
    return "candidate"


def _build_frontier(
    bench_data: List[Dict],
    oracle_checks: Dict[str, Dict],
    version_gates: Dict[str, Dict],
    strict_data: Optional[Dict[str, Dict]],
    score_threshold: float,
    top: int,
    exclude_suspect: bool,
) -> List[Dict]:
    """Build ranked frontier list."""
    rows = []
    for item in bench_data:
        sid = item["statute_id"]
        sim = item["similarity"]
        amendments = item["amendments"]

        if sim >= score_threshold:
            continue  # Skip near-perfect statutes

        oracle_info = oracle_checks.get(sid)
        if oracle_info is not None and int(oracle_info.get("total_divergences", 0) or 0) == 0:
            continue
        version_gate = version_gates.get(sid)
        fixability, is_suspect, top_diag = _compute_fixability(
            sim, oracle_info, version_gate, amendments, exclude_suspect
        )

        # Projection-row kinds from strict run
        projection_kinds: List[str] = []
        source_incomplete = False
        strict_row: Optional[Dict] = None
        if strict_data:
            strict_row = strict_data.get(sid, {})
            projection_kinds = strict_row.get("projection_kinds", [])
            source_incomplete = strict_row.get("source_incomplete", False)
        source_pathology, source_pathology_codes = _source_pathology_signal(
            oracle_info,
            strict_row,
        )
        html_noncommensurable_reason = _html_noncommensurable_signal(oracle_info)
        html_topology_mismatch, html_missing, html_extra = _html_topology_signal(
            oracle_info,
        )
        contingent_effective_date, contingent_sources = _contingent_effective_date_signal(
            oracle_info,
            strict_row,
        )

        if source_pathology:
            fixability *= 0.1
            is_suspect = True
            top_diag = (
                f"SOURCE_PATHOLOGY:{','.join(source_pathology_codes)}"
                if source_pathology_codes
                else "SOURCE_PATHOLOGY"
            )
        elif html_noncommensurable_reason:
            fixability *= 0.1
            is_suspect = True
            top_diag = f"HTML_NONCOMMENSURABLE:{html_noncommensurable_reason}"
        elif html_topology_mismatch:
            fixability *= 0.1
            is_suspect = True
            top_diag = "HTML_TOPOLOGY_MISMATCH"
        elif contingent_effective_date:
            fixability *= 0.1
            is_suspect = True
            top_diag = (
                f"CONTINGENT_EFFECTIVE_DATE:{','.join(contingent_sources)}"
                if contingent_sources
                else "CONTINGENT_EFFECTIVE_DATE"
            )

        if exclude_suspect and is_suspect:
            continue

        bucket = _bucket_frontier_row(
            oracle_info=oracle_info,
            version_gate=version_gate,
            _strict_row=strict_row,
            similarity=sim,
            amendments=amendments,
            source_pathology=source_pathology,
            html_noncommensurable=bool(html_noncommensurable_reason),
            html_topology_mismatch=html_topology_mismatch,
            contingent_effective_date=contingent_effective_date,
        )

        rows.append({
            "statute_id": sid,
            "score": sim,
            "replay_loss": 1.0 - sim,
            "fixability": fixability,
            "is_suspect": is_suspect,
            "top_diagnosis": top_diag,
            "amendments": amendments,
            "source_incomplete": source_incomplete,
            "source_pathology": source_pathology,
            "source_pathology_codes": "|".join(source_pathology_codes) if source_pathology_codes else "",
            "html_noncommensurable_reason": html_noncommensurable_reason,
            "html_topology_mismatch": html_topology_mismatch,
            "html_missing_from_xml": "|".join(html_missing) if html_missing else "",
            "html_extra_in_xml": "|".join(html_extra) if html_extra else "",
            "contingent_effective_date": contingent_effective_date,
            "contingent_effective_sources": "|".join(contingent_sources) if contingent_sources else "",
            "projection_kinds": "|".join(sorted(projection_kinds)) if projection_kinds else "",
            "suspect_fraction": oracle_info["suspect_fraction"] if oracle_info else None,
            "oracle_version_suspect": (version_gate or {}).get("suspect_detail", ""),
            "oracle_version_pending": (version_gate or {}).get("pending_detail", ""),
            "bucket": bucket,
        })

    # Sort by fixability descending (highest fixability = best target)
    rows.sort(key=lambda r: r["fixability"], reverse=True)
    return rows[:top]


def _summarize_low_scoring_rows(
    low_scoring: List[Dict],
    oracle_checks: Dict[str, Dict],
    version_gates: Dict[str, Dict],
    strict_data: Optional[Dict[str, Dict]],
) -> Dict[str, int]:
    summary = {
        "total_low": len(low_scoring),
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 0,
        "html_noncommensurable": 0,
        "html_topology": 0,
        "contingent_effective_date": 0,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 0,
    }

    for row in low_scoring:
        sid = row["statute_id"]
        oracle_info = oracle_checks.get(sid)
        if oracle_info is not None and int(oracle_info.get("total_divergences", 0) or 0) == 0:
            summary["resolved_after_refresh"] += 1
            continue
        version_gate = version_gates.get(sid)
        strict_row = strict_data.get(sid, {}) if strict_data else None
        source_pathology, _ = _source_pathology_signal(oracle_info, strict_row)
        html_noncommensurable_reason = _html_noncommensurable_signal(oracle_info)
        html_topology_mismatch, _, _ = _html_topology_signal(oracle_info)
        contingent_effective_date, _ = _contingent_effective_date_signal(
            oracle_info,
            strict_row,
        )
        bucket = _bucket_frontier_row(
            oracle_info=oracle_info,
            version_gate=version_gate,
            _strict_row=strict_row,
            similarity=row["similarity"],
            amendments=row["amendments"],
            source_pathology=source_pathology,
            html_noncommensurable=bool(html_noncommensurable_reason),
            html_topology_mismatch=html_topology_mismatch,
            contingent_effective_date=contingent_effective_date,
        )
        summary[bucket] += 1

    return summary


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_BUCKET_ORDER = [
    "candidate",
    "oracle_version_suspect",
    "no_oracle_check",
    "source_pathology",
    "html_noncommensurable",
    "html_topology",
    "contingent_effective_date",
    "base_drift",
    "other_suspect",
]

_PROOF_PRIMARY_TIER_BUCKETS = {
    "PROVED_REPLAY_BUG": "candidate",
    "UNRESOLVED": "candidate",
    "PROVED_SOURCE_PATHOLOGY": "source_pathology",
    "PROVED_HTML_XML_NONCOMMENSURABLE": "html_noncommensurable",
    "PROVED_ORACLE_INCORRECT": "other_suspect",
}

_PREEXISTING_ONLY_SECTION_CLAIM_KINDS = {
    "UNRESOLVED.preexisting.baseline_residue",
    "UNRESOLVED.preexisting.frontend_elaboration_ambiguity",
    "UNRESOLVED.address_projection.same_chapter_section_drift",
    "UNRESOLVED.preexisting.same_section_structure_drift",
}


def _build_proof_report_rows(rows: List[Dict], mode: str) -> List[Dict]:
    """Attach live statute-level proof summaries to frontier rows."""
    from lawvm.tools.evidence import build_evidence_bundle

    proof_rows: List[Dict] = []
    for row in rows:
        sid = str(row.get("statute_id") or "")
        if not sid:
            continue
        bundle = build_evidence_bundle(sid, mode=mode)
        proof_kinds = [
            str(item.get("kind") or "")
            for item in bundle.get("proof_claims", [])
            if str(item.get("kind") or "")
        ]
        section_claim_kinds = sorted(
            {
                str(item.get("selected_kind") or "")
                for item in bundle.get("section_claims", []) or []
                if str(item.get("selected_kind") or "")
            }
        )
        proof_rows.append(
            {
                "statute_id": sid,
                "bucket": str(row.get("bucket") or ""),
                "score": float(row.get("score") or 0.0),
                "primary_proof_tier": str(bundle.get("primary_proof_tier") or "UNRESOLVED"),
                "proof_tiers": list(bundle.get("proof_tiers") or []),
                "proof_kinds": proof_kinds,
                "section_claim_count": len(bundle.get("section_claims", []) or []),
                "selected_section_claim_count": sum(
                    1
                    for item in bundle.get("section_claims", []) or []
                    if str(item.get("selected_kind") or "")
                ),
                "section_claim_kinds": section_claim_kinds,
                "statute_only_proof_kinds": [
                    kind for kind in proof_kinds if kind not in set(section_claim_kinds)
                ],
                "section_claim_rules": sorted(
                    {
                        str(item.get("selected_inference_rule") or "")
                        for item in bundle.get("section_claims", []) or []
                        if str(item.get("selected_inference_rule") or "")
                    }
                ),
                "defeated_section_claim_kinds": sorted(
                    {
                        str(kind or "")
                        for item in bundle.get("section_claims", []) or []
                        for kind in (item.get("defeated_candidate_kinds", []) or [])
                        if str(kind or "")
                    }
                ),
                "defeated_section_claim_rules": sorted(
                    {
                        str(defeated.get("inference_rule") or "")
                        for item in bundle.get("section_claims", []) or []
                        for defeated in (item.get("defeated_candidates", []) or [])
                        if str(defeated.get("inference_rule") or "")
                    }
                ),
                "alternative_replay_match_count": sum(
                    1
                    for item in bundle.get("section_claims", []) or []
                    if (item.get("alternative_replay_match") or {}).get("best_replay_section")
                ),
                "alternative_replay_sections": sorted(
                    {
                        str((item.get("alternative_replay_match") or {}).get("best_replay_section") or "")
                        for item in bundle.get("section_claims", []) or []
                        if str((item.get("alternative_replay_match") or {}).get("best_replay_section") or "")
                    }
                ),
                "strict_fail_reasons": list(bundle.get("strict_fail_reasons") or []),
            }
        )
    return proof_rows


def _proof_bucket_for_row(row: Dict, proof_row: Optional[Dict]) -> str:
    """Map proof-primary tier back onto frontier buckets for proof-aware ranking."""
    current = str(row.get("bucket") or "")
    if current != "candidate" or not proof_row:
        return current
    tier = str(proof_row.get("primary_proof_tier") or "UNRESOLVED")
    if tier == "UNRESOLVED":
        proof_kinds = {
            str(kind or "")
            for kind in (proof_row.get("proof_kinds") or [])
            if str(kind or "")
        }
        section_claim_kinds = {
            str(kind or "")
            for kind in (proof_row.get("section_claim_kinds") or [])
            if str(kind or "")
        }
        if "no_strong_claim" in proof_kinds:
            return "other_suspect"
        if (
            section_claim_kinds
            and section_claim_kinds <= _PREEXISTING_ONLY_SECTION_CLAIM_KINDS
        ):
            return "base_drift"
        if {
            "UNRESOLVED.preexisting.baseline_residue",
            "UNRESOLVED.address_projection.same_chapter_section_drift",
        } & proof_kinds:
            return "base_drift"
        if "UNRESOLVED.address_projection.same_chapter_replay_drift" in proof_kinds:
            return "other_suspect"
    return _PROOF_PRIMARY_TIER_BUCKETS.get(tier, current)


def _apply_proof_rebucketing(
    rows: List[Dict],
    proof_rows: List[Dict],
    *,
    exclude_suspect: bool,
) -> tuple[List[Dict], List[Dict]]:
    """Rebucket displayed frontier rows using live proof tiers.

    Candidate rows that are now proven to be source/oracle-side should stop
    surfacing as active replay targets. When `exclude_suspect` is enabled,
    proof-demoted non-candidates are dropped from the displayed frontier while
    remaining visible in the proof report.
    """
    proof_by_sid = {
        str(item.get("statute_id") or ""): item
        for item in proof_rows
        if str(item.get("statute_id") or "")
    }
    rows_by_sid = {
        str(item.get("statute_id") or ""): item
        for item in rows
        if str(item.get("statute_id") or "")
    }
    adjusted_rows: List[Dict] = []
    adjusted_proof_rows: List[Dict] = []

    for proof_row in proof_rows:
        sid = str(proof_row.get("statute_id") or "")
        row = rows_by_sid.get(sid)
        new_proof = dict(proof_row)
        if row is not None:
            new_proof["bucket"] = _proof_bucket_for_row(row, proof_row)
        adjusted_proof_rows.append(new_proof)

    for row in rows:
        sid = str(row.get("statute_id") or "")
        new_row = dict(row)
        new_bucket = _proof_bucket_for_row(row, proof_by_sid.get(sid))
        new_row["bucket"] = new_bucket
        if exclude_suspect and new_bucket != "candidate":
            continue
        adjusted_rows.append(new_row)

    return adjusted_rows, adjusted_proof_rows


def _apply_proof_rebucketing_to_summary(
    summary: Dict[str, int],
    rows: List[Dict],
    proof_rows: List[Dict],
) -> Dict[str, int]:
    """Move pre-proof candidate counts into proof-backed buckets."""
    adjusted = dict(summary)
    proof_by_sid = {
        str(item.get("statute_id") or ""): item
        for item in proof_rows
        if str(item.get("statute_id") or "")
    }
    for row in rows:
        old_bucket = str(row.get("bucket") or "")
        proof_row = proof_by_sid.get(str(row.get("statute_id") or ""))
        new_bucket = _proof_bucket_for_row(row, proof_row)
        if new_bucket == old_bucket:
            continue
        if old_bucket in adjusted:
            adjusted[old_bucket] = max(0, int(adjusted.get(old_bucket, 0)) - 1)
        if new_bucket in adjusted:
            adjusted[new_bucket] = int(adjusted.get(new_bucket, 0)) + 1
    return adjusted


def _build_evidence_bundles(rows: List[Dict], mode: str) -> List[Dict]:
    """Build full live evidence bundles for the displayed frontier rows."""
    from lawvm.tools.evidence import build_evidence_bundle

    bundles: List[Dict] = []
    for row in rows:
        sid = str(row.get("statute_id") or "")
        if not sid:
            continue
        bundle = build_evidence_bundle(sid, mode=mode)
        bundles.append(bundle)
    return bundles


def _summarize_proof_rows(rows: List[Dict]) -> Dict[str, Dict[str, int]]:
    tier_counts: Dict[str, int] = defaultdict(int)
    kind_counts: Dict[str, int] = defaultdict(int)
    section_kind_counts: Dict[str, int] = defaultdict(int)
    statute_only_kind_counts: Dict[str, int] = defaultdict(int)
    section_rule_counts: Dict[str, int] = defaultdict(int)
    defeated_section_kind_counts: Dict[str, int] = defaultdict(int)
    defeated_section_rule_counts: Dict[str, int] = defaultdict(int)
    alternative_replay_section_counts: Dict[str, int] = defaultdict(int)
    bucket_tier_counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        tier = str(row.get("primary_proof_tier") or "UNRESOLVED")
        bucket = str(row.get("bucket") or "")
        tier_counts[tier] += 1
        if bucket:
            bucket_tier_counts[f"{bucket}:{tier}"] += 1
        for kind in row.get("proof_kinds", []) or []:
            kind_text = str(kind or "")
            if kind_text:
                kind_counts[kind_text] += 1
        for kind in row.get("section_claim_kinds", []) or []:
            kind_text = str(kind or "")
            if kind_text:
                section_kind_counts[kind_text] += 1
        for kind in row.get("statute_only_proof_kinds", []) or []:
            kind_text = str(kind or "")
            if kind_text:
                statute_only_kind_counts[kind_text] += 1
        for rule in row.get("section_claim_rules", []) or []:
            rule_text = str(rule or "")
            if rule_text:
                section_rule_counts[rule_text] += 1
        for kind in row.get("defeated_section_claim_kinds", []) or []:
            kind_text = str(kind or "")
            if kind_text:
                defeated_section_kind_counts[kind_text] += 1
        for rule in row.get("defeated_section_claim_rules", []) or []:
            rule_text = str(rule or "")
            if rule_text:
                defeated_section_rule_counts[rule_text] += 1
        for alt in row.get("alternative_replay_sections", []) or []:
            alt_text = str(alt or "")
            if alt_text:
                alternative_replay_section_counts[alt_text] += 1
    return {
        "primary_tiers": dict(sorted(tier_counts.items())),
        "proof_kinds": dict(sorted(kind_counts.items())),
        "section_claim_kinds": dict(sorted(section_kind_counts.items())),
        "statute_only_proof_kinds": dict(sorted(statute_only_kind_counts.items())),
        "section_claim_rules": dict(sorted(section_rule_counts.items())),
        "defeated_section_claim_kinds": dict(sorted(defeated_section_kind_counts.items())),
        "defeated_section_claim_rules": dict(sorted(defeated_section_rule_counts.items())),
        "alternative_replay_sections": dict(sorted(alternative_replay_section_counts.items())),
        "bucket_primary_tiers": dict(sorted(bucket_tier_counts.items())),
    }

def _print_frontier(rows: List[Dict], label: str, exclude_suspect: bool, mode: str) -> None:
    suspect_note = " (oracle-suspect excluded)" if exclude_suspect else ""
    print(
        f"\n=== Honest Frontier: top {len(rows)} replay targets — "
        f"{label} [{mode}]{suspect_note} ===\n"
    )

    header = (
        f"{'Rank':>4}  {'Statute':>12}  {'Score':>6}  {'Loss':>6}  "
        f"{'Bucket':>22}  {'Suspect':>7}  {'Amend':>5}  {'SrcInc':>6}  {'SrcPath':>7}  {'ContEff':>7}  Top-diagnosis"
    )
    print(header)
    print("-" * len(header))

    for i, row in enumerate(rows, 1):
        score_pct = f"{row['score']:.1%}"
        loss_pct = f"{row['replay_loss']:.1%}"
        suspect_str = "yes" if row["is_suspect"] else "no"
        suspect_frac = row["suspect_fraction"]
        if suspect_frac is not None and row["is_suspect"]:
            suspect_str = f"yes({suspect_frac:.0%})"
        projection = row["projection_kinds"]
        path_codes = row["source_pathology_codes"]
        path_detail = row.get("source_pathology_detail", "")
        diag = row["top_diagnosis"]
        cont_src = row["contingent_effective_sources"]
        html_detail = row["html_missing_from_xml"] or row["html_extra_in_xml"]
        html_noncomm = row["html_noncommensurable_reason"]
        detail = path_detail if path_detail else (path_codes if path_codes else (html_noncomm if html_noncomm else (html_detail if html_detail else (cont_src if cont_src else (projection if projection else diag)))))
        src_inc = "yes" if row["source_incomplete"] else "no"
        src_path = "yes" if row["source_pathology"] else "no"
        cont_eff = "yes" if row["contingent_effective_date"] else "no"
        print(
            f"{i:>4}  {row['statute_id']:>12}  {score_pct:>6}  {loss_pct:>6}  "
            f"{row['bucket']:>22}  {suspect_str:>7}  {row['amendments']:>5}  {src_inc:>6}  {src_path:>7}  {cont_eff:>7}  {detail}"
        )

    print()


def _print_bucket_report(rows: List[Dict], top: int, label: str, mode: str) -> None:
    print(
        f"\n=== Frontier Bucket Report: top {top} per bucket — {label} [{mode}] ===\n"
    )
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        by_bucket[str(row["bucket"])].append(row)

    for bucket in _BUCKET_ORDER:
        bucket_rows = by_bucket.get(bucket, [])
        if not bucket_rows:
            continue
        print(f"[{bucket}] {len(bucket_rows)} statute(s)")
        for i, row in enumerate(bucket_rows[:top], 1):
            detail = row["top_diagnosis"]
            if row.get("source_pathology_detail"):
                detail = row["source_pathology_detail"]
            elif row.get("source_pathology_codes"):
                detail = row["source_pathology_codes"]
            elif row.get("html_noncommensurable_reason"):
                detail = row["html_noncommensurable_reason"]
            elif row.get("html_missing_from_xml") or row.get("html_extra_in_xml"):
                detail = row.get("html_missing_from_xml") or row.get("html_extra_in_xml")
            elif row.get("contingent_effective_sources"):
                detail = row["contingent_effective_sources"]
            print(
                f"  {i:>2}. {row['statute_id']}  "
                f"{row['score']:.1%}  "
                f"{row['amendments']} amend  "
                f"{detail}"
            )
        print()


def _print_proof_report(rows: List[Dict], label: str, mode: str) -> None:
    print(f"\n=== Frontier Proof Report: top {len(rows)} statutes — {label} [{mode}] ===\n")
    for row in rows:
        kinds = ", ".join(row["proof_kinds"]) if row["proof_kinds"] else "none"
        tiers = ", ".join(row["proof_tiers"]) if row["proof_tiers"] else "UNRESOLVED"
        print(
            f"{row['statute_id']:<12}  {row['bucket']:<24}  "
            f"{row['primary_proof_tier']:<32}  {kinds}"
        )
        if row["strict_fail_reasons"]:
            print(f"  strict: {', '.join(row['strict_fail_reasons'])}")
        print(f"  tiers : {tiers}")
    print()


def _print_proof_summary(summary: Dict[str, Dict[str, int]], label: str, mode: str) -> None:
    print(f"\n=== Frontier Proof Summary — {label} [{mode}] ===\n")
    print("Primary tiers:")
    for tier, count in summary.get("primary_tiers", {}).items():
        print(f"  {tier:<32} {count}")
    print()
    if summary.get("proof_kinds"):
        print("Proof kinds:")
        for kind, count in summary["proof_kinds"].items():
            print(f"  {kind:<32} {count}")
        print()
    if summary.get("section_claim_kinds"):
        print("Section claim kinds:")
        for kind, count in summary["section_claim_kinds"].items():
            print(f"  {kind:<32} {count}")
        print()
    if summary.get("statute_only_proof_kinds"):
        print("Statute-only proof kinds:")
        for kind, count in summary["statute_only_proof_kinds"].items():
            print(f"  {kind:<32} {count}")
        print()
    if summary.get("section_claim_rules"):
        print("Section claim rules:")
        for rule, count in summary["section_claim_rules"].items():
            print(f"  {rule:<32} {count}")
        print()
    if summary.get("defeated_section_claim_kinds"):
        print("Defeated section claim kinds:")
        for kind, count in summary["defeated_section_claim_kinds"].items():
            print(f"  {kind:<32} {count}")
        print()
    if summary.get("defeated_section_claim_rules"):
        print("Defeated section claim rules:")
        for rule, count in summary["defeated_section_claim_rules"].items():
            print(f"  {rule:<32} {count}")
        print()
    if summary.get("alternative_replay_sections"):
        print("Alternative replay sections:")
        for section, count in summary["alternative_replay_sections"].items():
            print(f"  {section:<32} {count}")
        print()


def _bucket_report_payload(rows: List[Dict], top: int) -> Dict[str, List[Dict]]:
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        by_bucket[str(row["bucket"])].append(row)
    return {
        bucket: bucket_rows[:top]
        for bucket in _BUCKET_ORDER
        for bucket_rows in [by_bucket.get(bucket, [])]
        if bucket_rows
    }


def _save_frontier_csv(rows: List[Dict], label: str) -> Path:
    reports_dir = _frontier_reports_dir()
    path = reports_dir / f"{label}_frontier.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "rank", "statute_id", "score", "replay_loss", "fixability",
            "bucket", "is_suspect", "suspect_fraction", "amendments", "source_incomplete",
            "source_pathology", "source_pathology_codes",
            "html_noncommensurable_reason",
            "html_topology_mismatch", "html_missing_from_xml", "html_extra_in_xml",
            "contingent_effective_date", "contingent_effective_sources",
            "projection_kinds", "top_diagnosis",
        ])
        for i, row in enumerate(rows, 1):
            w.writerow([
                i,
                row["statute_id"],
                f"{row['score']:.6f}",
                f"{row['replay_loss']:.6f}",
                f"{row['fixability']:.6f}",
                row["bucket"],
                "1" if row["is_suspect"] else "0",
                f"{row['suspect_fraction']:.4f}" if row["suspect_fraction"] is not None else "",
                row["amendments"],
                "1" if row["source_incomplete"] else "0",
                "1" if row["source_pathology"] else "0",
                row["source_pathology_codes"],
                row["html_noncommensurable_reason"],
                "1" if row["html_topology_mismatch"] else "0",
                row["html_missing_from_xml"],
                row["html_extra_in_xml"],
                "1" if row["contingent_effective_date"] else "0",
                row["contingent_effective_sources"],
                row["projection_kinds"],
                row["top_diagnosis"],
            ])
    return path


def _save_proof_report_jsonl(rows: List[Dict], label: str, path: Optional[str] = None) -> Path:
    reports_dir = _frontier_reports_dir()
    out = Path(path) if path else (reports_dir / f"{label}_frontier_proof.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return out


def _save_evidence_bundles_jsonl(bundles: List[Dict], label: str, path: Optional[str] = None) -> Path:
    reports_dir = _frontier_reports_dir()
    out = Path(path) if path else (reports_dir / f"{label}_frontier_evidence.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for bundle in bundles:
            f.write(json.dumps(bundle, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    label = getattr(args, "label", None)
    mode = getattr(args, "mode", "finlex_oracle")
    top = getattr(args, "top", 30)
    exclude_suspect = getattr(args, "exclude_suspect", False)
    strict_label = getattr(args, "strict_label", None)
    corpus_path = getattr(args, "corpus", None)
    export_low_corpus = getattr(args, "export_low_corpus", None)
    divergences_db = getattr(args, "db", None)
    score_threshold = getattr(args, "threshold", 0.95)
    workers = getattr(args, "parallel", None) or max(8, os.cpu_count() or 4)
    no_save = getattr(args, "no_save", False)
    refresh_all_oracle_check = bool(getattr(args, "refresh_all_oracle_check", False))
    refresh_all_scores = bool(getattr(args, "refresh_all_scores", False))
    bucket_filter = getattr(args, "bucket", None)
    bucket_report = bool(getattr(args, "bucket_report", False))
    proof_report = bool(getattr(args, "proof_report", False))
    proof_summary = bool(getattr(args, "proof_summary", False))
    proof_export = getattr(args, "proof_export", None)
    evidence_export = getattr(args, "evidence_export", None)
    json_output = bool(getattr(args, "json_output", False))

    def status(msg: str) -> None:
        if not json_output:
            print(msg, flush=True)

    if label is None:
        print("ERROR: --label is required", file=sys.stderr)
        sys.exit(1)

    # Step 1: Load bench results
    status(f"Loading bench run: {label}...")
    bench_data = _load_bench_run(label)
    if bench_data is None:
        print(f"ERROR: no bench run found for label '{label}'", file=sys.stderr)
        sys.exit(1)
    status(f"  Loaded {len(bench_data)} statutes from bench run.")

    if corpus_path:
        subset_ids = _load_corpus_subset(corpus_path)
        if not subset_ids:
            print(f"ERROR: empty or unreadable corpus: {corpus_path}", file=sys.stderr)
            sys.exit(1)
        bench_data = _filter_bench_data_to_corpus_ids(bench_data, subset_ids)
        if not bench_data:
            print(
                f"ERROR: corpus subset {corpus_path} had no matching statutes in bench run '{label}'",
                file=sys.stderr,
            )
            sys.exit(1)
        status(f"  Restricted to corpus subset: {len(bench_data)} statutes from {corpus_path}")

    # Identify low-scoring statutes (candidates for frontier)
    low_scoring = [r for r in bench_data if r["similarity"] < score_threshold]
    status(f"  Statutes below {score_threshold:.0%}: {len(low_scoring)}")

    # Step 2: Oracle-check classification
    oracle_checks: Dict[str, Dict] = {}
    version_gates: Dict[str, Dict] = {}

    # First try loading from divergences.db if provided
    if divergences_db:
        db_path = Path(divergences_db)
        status(f"Loading oracle-check data from DB: {db_path}...")
        oracle_checks = _load_oracle_check_cache(db_path)
        status(f"  Loaded oracle-check data for {len(oracle_checks)} statutes.")
    else:
        # Try default divergences.db location
        default_db = Path(__file__).resolve().parents[3] / ".tmp" / "divergences.db"
        if default_db.exists():
            status(f"Loading oracle-check data from {default_db}...")
            oracle_checks = _load_oracle_check_cache(default_db)
            status(f"  Loaded oracle-check data for {len(oracle_checks)} statutes.")

    refresh_sids: List[str]
    if refresh_all_oracle_check or _should_refresh_all_low_scoring(low_scoring):
        refresh_sids = [r["statute_id"] for r in low_scoring]
        refresh_reason = (
            "forced full refresh"
            if refresh_all_oracle_check
            else f"<= {FRESH_ORACLE_CHECK_LIMIT}; prefer current code over cache"
        )
        status(
            f"Refreshing oracle-check for all {len(refresh_sids)} low-scoring statutes "
            f"({refresh_reason}, "
            f"mode={mode}, parallel={workers})..."
        )
    else:
        refresh_sids = [
            r["statute_id"] for r in low_scoring
            if r["statute_id"] not in oracle_checks
        ]
        if refresh_sids:
            status(
                f"Running fresh oracle-check for {len(refresh_sids)} uncovered low-scoring statutes "
                f"(mode={mode}, parallel={workers})..."
            )
    if refresh_sids:
        fresh = _run_oracle_checks_parallel(refresh_sids, workers, mode=mode, progress=not json_output)
        oracle_checks.update(fresh)
        status(f"  Oracle-check complete. Total covered: {len(oracle_checks)}")

    if refresh_all_scores or _should_refresh_all_low_scoring_scores(low_scoring):
        score_refresh_sids = [r["statute_id"] for r in low_scoring]
        status(
            f"Refreshing current scores for all {len(score_refresh_sids)} low-scoring statutes "
            f"({'forced full refresh' if refresh_all_scores else f'<= {FRESH_SCORE_REFRESH_LIMIT}'}; "
            f"mode={mode}, parallel={workers})..."
        )
        refreshed_scores = _run_score_refresh_parallel(
            score_refresh_sids,
            workers,
            mode=mode,
            progress=not json_output,
        )
        bench_data = _apply_refreshed_scores(bench_data, refreshed_scores)
        low_scoring = [r for r in bench_data if r["similarity"] < score_threshold]
        status(f"  Score refresh complete. Remaining below {score_threshold:.0%}: {len(low_scoring)}")

    if export_low_corpus:
        saved = _save_corpus_slice(low_scoring, export_low_corpus)
        status(f"  Exported low-scoring corpus slice: {saved}")

    status("Loading cache-only oracle-version commensurability...")
    version_suspect_count = 0
    version_pending_count = 0
    for row in low_scoring:
        sid = row["statute_id"]
        suspect_detail, pending_detail = get_consolidated_oracle_suspect_cache_only(sid)
        if suspect_detail or pending_detail:
            version_gates[sid] = {
                "suspect_detail": suspect_detail,
                "pending_detail": pending_detail,
            }
            if suspect_detail:
                version_suspect_count += 1
            elif pending_detail:
                version_pending_count += 1
    status(
        f"  Version-suspect: {version_suspect_count}, "
        f"version-pending: {version_pending_count}"
    )

    # Step 3: Load strict run data if available
    strict_data: Optional[Dict[str, Dict]] = None
    if strict_label:
        status(f"Loading strict run: {strict_label}...")
        strict_data = _load_strict_run(strict_label)
        if strict_data is None:
            status(f"  WARNING: no strict run found for label '{strict_label}'")
        else:
            status(f"  Loaded strict data for {len(strict_data)} statutes.")

    if not refresh_all_oracle_check and not _should_refresh_all_low_scoring(low_scoring):
        provisional_refresh_sids = _select_provisional_candidate_refresh_sids(
            bench_data=bench_data,
            oracle_checks=oracle_checks,
            version_gates=version_gates,
            strict_data=strict_data,
            score_threshold=score_threshold,
            top=top,
            exclude_suspect=exclude_suspect,
        )
        if provisional_refresh_sids:
            status(
                f"Refreshing current oracle-check for {len(provisional_refresh_sids)} provisional "
                f"frontier candidates (cache cleanup, mode={mode}, parallel={workers})..."
            )
            fresh = _run_oracle_checks_parallel(
                provisional_refresh_sids,
                workers,
                mode=mode,
                progress=not json_output,
            )
            oracle_checks.update(fresh)
            status(f"  Provisional frontier refresh complete. Total covered: {len(oracle_checks)}")

    # Step 4: Build and rank frontier
    status(f"\nBuilding frontier (mode={mode}, top={top}, exclude_suspect={exclude_suspect})...")
    frontier = _build_frontier(
        bench_data=bench_data,
        oracle_checks=oracle_checks,
        version_gates=version_gates,
        strict_data=strict_data,
        score_threshold=score_threshold,
        top=max(top, len(low_scoring)) if (bucket_filter or bucket_report or proof_report) else top,
        exclude_suspect=exclude_suspect,
    )

    # Print summary stats
    summary = _summarize_low_scoring_rows(
        low_scoring,
        oracle_checks,
        version_gates,
        strict_data,
    )

    proof_payload = _build_proof_report_rows(frontier, mode) if proof_report else []
    if proof_payload:
        summary = _apply_proof_rebucketing_to_summary(summary, frontier, proof_payload)
        frontier, proof_payload = _apply_proof_rebucketing(
            frontier,
            proof_payload,
            exclude_suspect=exclude_suspect,
        )

    if bucket_filter:
        frontier = [row for row in frontier if row["bucket"] == bucket_filter][:top]
        if proof_payload:
            proof_payload = [
                row for row in proof_payload if row["bucket"] == bucket_filter
            ][:top]
        status(f"Bucket filter: {bucket_filter} -> {len(frontier)} row(s)")

    total_low = summary["total_low"]
    if not json_output:
        print(
            f"\nSummary: {total_low} statutes below {score_threshold:.0%}, "
            f"{summary['resolved_after_refresh']} resolved-after-refresh "
            f"({summary['resolved_after_refresh']*100//total_low if total_low else 0}%), "
            f"{summary['oracle_version_suspect']} oracle/version-suspect "
            f"({summary['oracle_version_suspect']*100//total_low if total_low else 0}%), "
            f"{summary['no_oracle_check']} no-oracle-check "
            f"({summary['no_oracle_check']*100//total_low if total_low else 0}%), "
            f"{summary['source_pathology']} source-pathology "
            f"({summary['source_pathology']*100//total_low if total_low else 0}%), "
            f"{summary['html_noncommensurable']} html-noncommensurable "
            f"({summary['html_noncommensurable']*100//total_low if total_low else 0}%), "
            f"{summary['html_topology']} html-topology "
            f"({summary['html_topology']*100//total_low if total_low else 0}%), "
            f"{summary['contingent_effective_date']} contingent-effective-date "
            f"({summary['contingent_effective_date']*100//total_low if total_low else 0}%), "
            f"{summary['base_drift']} base-drift "
            f"({summary['base_drift']*100//total_low if total_low else 0}%), "
            f"{summary['other_suspect']} other-suspect "
            f"({summary['other_suspect']*100//total_low if total_low else 0}%), "
            f"{summary['candidate']} candidate replay targets."
        )

    display_rows = frontier[:top]
    bucket_payload = _bucket_report_payload(frontier, top) if bucket_report else {}
    proof_summary_payload = _summarize_proof_rows(proof_payload) if proof_payload and proof_summary else {}
    evidence_payload = _build_evidence_bundles(display_rows, mode) if evidence_export else []
    save_path: Optional[Path] = None
    proof_save_path: Optional[Path] = None
    evidence_save_path: Optional[Path] = None
    if not no_save and display_rows:
        save_path = _save_frontier_csv(display_rows, label)
    if proof_payload and (proof_export or not no_save):
        proof_save_path = _save_proof_report_jsonl(proof_payload, label, proof_export)
    if evidence_payload:
        evidence_save_path = _save_evidence_bundles_jsonl(evidence_payload, label, evidence_export)

    if json_output:
        payload = {
            "label": label,
            "mode": mode,
            "threshold": score_threshold,
            "top": top,
            "exclude_suspect": exclude_suspect,
            "bucket_filter": bucket_filter,
            "bucket_report": bucket_report,
            "summary": summary,
            "rows": display_rows,
            "buckets": bucket_payload,
            "proof_rows": proof_payload,
            "proof_summary": proof_summary_payload,
            "saved_proof_jsonl": str(proof_save_path) if proof_save_path else "",
            "saved_evidence_jsonl": str(evidence_save_path) if evidence_save_path else "",
            "saved_csv": str(save_path) if save_path else "",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if bucket_report:
        _print_bucket_report(frontier, top, label, mode)
    if proof_report:
        _print_proof_report(proof_payload, label, mode)
    if proof_summary_payload:
        _print_proof_summary(proof_summary_payload, label, mode)
    _print_frontier(display_rows, label, exclude_suspect, mode)

    # Step 5: Save CSV
    if save_path is not None:
        print(f"Saved: {save_path}")
    if proof_save_path is not None:
        print(f"Saved proof: {proof_save_path}")
    if evidence_save_path is not None:
        print(f"Saved evidence: {evidence_save_path}")


def register_cli(sub: Any) -> None:
    """Register the 'frontier' subcommand onto an argparse subparsers object."""
    frontier_p = sub.add_parser(
        "frontier",
        help="honest frontier report — ranked fixable replay targets",
        description=(
            "Combine bench results with oracle-check classifications to rank "
            "low-scoring statutes by fixability. Separates real replay bugs "
            "(fixable) from oracle-suspect, editorial-convention, and "
            "source-incomplete failures (not fixable)."
        ),
    )
    frontier_p.add_argument(
        "--label", metavar="LABEL", required=True,
        help="bench run label to analyse, e.g. v_post_merge",
    )
    frontier_p.add_argument(
        "--mode", default="finlex_oracle",
        choices=["finlex_oracle", "legal_pit"],
        help="replay mode for fresh oracle-check and score refresh (default: finlex_oracle)",
    )
    frontier_p.add_argument(
        "--top", type=int, default=30,
        help="number of top fixable targets to show (default: 30)",
    )
    frontier_p.add_argument(
        "--exclude-suspect", dest="exclude_suspect", action="store_true",
        help="omit oracle-suspect statutes from the ranked list",
    )
    frontier_p.add_argument(
        "--bucket",
        choices=[
            "oracle_version_suspect",
            "no_oracle_check",
            "source_pathology",
            "html_noncommensurable",
            "html_topology",
            "contingent_effective_date",
            "base_drift",
            "other_suspect",
            "candidate",
        ],
        help="filter the ranked list to one frontier bucket",
    )
    frontier_p.add_argument(
        "--bucket-report",
        action="store_true",
        help="print a compact top-N-per-bucket report from the current refreshed frontier",
    )
    frontier_p.add_argument(
        "--proof-report",
        action="store_true",
        help="attach live proof-tier summaries for the displayed frontier statutes",
    )
    frontier_p.add_argument(
        "--proof-summary",
        action="store_true",
        help="summarize proof tiers and proof kinds across the current proof-report rows",
    )
    frontier_p.add_argument(
        "--proof-export", metavar="PATH",
        help="write proof-report rows as JSONL (default: data/frontier_reports/<label>_frontier_proof.jsonl when --proof-report)",
    )
    frontier_p.add_argument(
        "--evidence-export", metavar="PATH",
        help="write full evidence bundles for the displayed frontier rows as JSONL",
    )
    frontier_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="emit the refreshed frontier snapshot as JSON",
    )
    frontier_p.add_argument(
        "--strict-label", dest="strict_label", metavar="LABEL",
        help="strict run label to load projection-row data from (e.g. strict_v1)",
    )
    frontier_p.add_argument(
        "--corpus", metavar="CSV_PATH",
        help="optional corpus CSV to restrict the bench run analysis to a subset of statutes",
    )
    frontier_p.add_argument(
        "--export-low-corpus", dest="export_low_corpus", metavar="CSV_PATH",
        help="write the current low-scoring corpus slice (after score refresh) to CSV",
    )
    frontier_p.add_argument(
        "--db", metavar="PATH",
        help="path to divergences.db for pre-computed oracle-check data "
             "(default: .tmp/divergences.db if it exists)",
    )
    frontier_p.add_argument(
        "--threshold", type=float, default=0.95, metavar="SCORE",
        help="only consider statutes scoring below this (default: 0.95)",
    )
    frontier_p.add_argument(
        "--parallel", type=int, default=None, metavar="N",
        help="workers for fresh oracle-check runs (default: cpu_count)",
    )
    frontier_p.add_argument(
        "--refresh-all-oracle-check",
        dest="refresh_all_oracle_check",
        action="store_true",
        help=(
            "force a live oracle-check refresh for all low-scoring statutes, "
            "even when the candidate pool is too large for the default full-refresh heuristic"
        ),
    )
    frontier_p.add_argument(
        "--refresh-all-scores",
        dest="refresh_all_scores",
        action="store_true",
        help=(
            "force a live score refresh for all low-scoring statutes, "
            "even when the candidate pool is too large for the default score-refresh heuristic"
        ),
    )
    frontier_p.add_argument(
        "--no-save", dest="no_save", action="store_true",
        help="do not write CSV to data/frontier_reports/",
    )
