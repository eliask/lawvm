"""lawvm failures — structured analysis of replay FailedOp records.

Usage:
    lawvm failures                    # summary across bench corpus
    lawvm failures 2012/999           # failures for one statute
    lawvm failures --pattern kohta    # filter by description pattern
    lawvm failures --top 20           # show top N affected statutes
    lawvm failures --detail           # categorize each failure by root cause
    lawvm failures --from-bench v33   # only replay imperfect statutes from bench run
    lawvm failures --parallel 8       # parallel replay workers
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.finland.grafter import FailedOp, XMLStatute, replay_xml
from lawvm.tools.classify import _classify_statute
from lawvm.core.tree_ops import _norm


# ---------------------------------------------------------------------------
# Corpus loading (match bench.py defaults)
# ---------------------------------------------------------------------------


def _lawvm_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_bench_sids() -> List[str]:
    """Load bench corpus statute IDs using same priority as bench.py."""
    d = _lawvm_dir()
    # Same priority as bench.py _default_corpus_path:
    # 1. bench_core.csv  2. bench_corpus.csv  3. legacy fallback
    for candidate in [
        d / "data" / "finland" / "bench_core.csv",
        d / "data" / "finland" / "bench_corpus.csv",
        d / ".tmp" / "batch_test_list.csv",
    ]:
        if candidate.exists():
            sids = []
            with open(candidate) as f:
                for row in csv.reader(f):
                    if len(row) >= 2:
                        sids.append(row[1].strip())
            return sids
    print("Bench corpus CSV not found", file=sys.stderr)
    return []


def _load_imperfect_sids_from_bench(label: str) -> Optional[List[str]]:
    """Load statute IDs that scored below 1.0 from a bench run.

    Returns None if the labeled run cannot be found.
    """
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]
    sids = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sim_str = row.get("similarity", "ERR")
            try:
                sim = float(sim_str)
            except ValueError:
                # ERR or unparseable — include it (might have failures)
                sids.append(row["statute_id"])
                continue
            if sim < 0.9999:
                sids.append(row["statute_id"])
    return sids


# ---------------------------------------------------------------------------
# Failure cache (sidecar JSON alongside bench runs)
# ---------------------------------------------------------------------------

def _cache_path(label: str) -> Optional[Path]:
    """Return path to failures cache sidecar for a bench label."""
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    if not runs_dir.exists():
        return None
    return runs_dir / f"failures_{label}.json"


def _save_failure_cache(label: str, failures: List[FailedOp]) -> Path:
    """Serialize FailedOp list to a JSON sidecar file."""
    p = _cache_path(label)
    if p is None:
        p = _lawvm_dir() / "data" / "bench_runs" / f"failures_{label}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for f in failures:
        scope = f.scope_detail()
        records.append({
            "amendment_id": f.amendment_id,
            "description": f.description,
            "reason": f.reason,
            "reason_code": f.reason_code,
            "target_unit_kind": f.target_unit_kind,
            "target_section": scope.get("target_section"),
            "target_chapter": scope.get("target_chapter"),
            "target_part": scope.get("target_part"),
        })
    p.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    return p


def _load_failure_cache(label: str) -> Optional[List[FailedOp]]:
    """Load FailedOp list from a JSON sidecar file, if it exists."""
    p = _cache_path(label)
    if p is None or not p.exists():
        return None
    try:
        records = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    failures = []
    for r in records:
        failures.append(FailedOp(
            amendment_id=r["amendment_id"],
            description=r["description"],
            reason=r["reason"],
            reason_code=str(r.get("reason_code") or ""),
            target_section=r["target_section"],
            target_chapter=r.get("target_chapter"),
            target_part=r.get("target_part"),
            target_unit_kind=_failure_target_unit_kind(r),
        ))
    return failures


def _failure_target_unit_kind(record: Dict[str, Any]) -> TargetUnitKind:
    target_unit_kind = record.get("target_unit_kind")
    if target_unit_kind:
        return target_unit_kind
    compat_target_kind_code = str(record.get("target_kind") or "").strip().upper()
    if compat_target_kind_code == "L":
        return "chapter"
    if compat_target_kind_code == "O":
        return "part"
    if compat_target_kind_code in {"P", "A"}:
        return "section"
    raise ValueError(f"Unsupported legacy failure target_kind code: {compat_target_kind_code!r}")


# ---------------------------------------------------------------------------
# Replay collection (sequential + parallel)
# ---------------------------------------------------------------------------

def _replay_one_for_failures(sid: str) -> List[Dict[str, Any]]:
    """Replay one statute, return serializable failure dicts.

    Designed for use with ProcessPoolExecutor (no unpicklable objects).
    """
    failed: List[FailedOp] = []
    try:
        replay_xml(sid, failed_ops_out=failed, quiet=True)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass
    return [
        {
            "sid": sid,
            "amendment_id": f.amendment_id,
            "description": f.description,
            "reason": f.reason,
            "reason_code": f.reason_code,
            **f.scope_detail(),
            "target_unit_kind": f.target_unit_kind,
        }
        for f in failed
    ]


def _collect_failures(
    sids: List[str],
    verbose: bool = False,
    need_masters: bool = False,
    parallel: int = 1,
) -> Tuple[List[FailedOp], Dict[str, XMLStatute], Dict[str, Set[tuple[str, str, str]]]]:
    """Replay each statute and collect FailedOp records.

    Returns (all_failures, masters_by_sid).
    masters_by_sid is populated only when need_masters=True (requires sequential).
    """
    t0 = time.time()

    if parallel > 1 and not need_masters:
        # Parallel path — cannot return XMLStatute objects (unpicklable)
        return _collect_failures_parallel(sids, verbose, parallel)

    # Sequential path (original behavior, needed for --detail which uses masters)
    all_failures: List[FailedOp] = []
    masters_by_sid: Dict[str, XMLStatute] = {}
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]] = {}
    ok = 0
    for i, sid in enumerate(sids):
        if verbose and (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(sids) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i+1}/{len(sids)}] {rate:.1f} stat/s, ETA {eta:.0f}s",
                file=sys.stderr,
            )
        try:
            failed: List[FailedOp] = []
            master = replay_xml(sid, failed_ops_out=failed, quiet=True)
            all_failures.extend(failed)
            if need_masters and failed:
                masters_by_sid[sid] = master
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = _classify_statute(sid, "finlex_oracle")
                pathologies_by_sid[sid] = {
                    (str(p.get("source_statute", "")), str(p.get("code", "")), str(p.get("target_label", "")))
                    for p in (getattr(result, "source_pathologies", []) or [])
                } if result is not None else set()
            ok += 1
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
    if verbose:
        elapsed = time.time() - t0
        print(
            f"Replayed {ok}/{len(sids)} statutes in {elapsed:.1f}s",
            file=sys.stderr,
        )
    return all_failures, masters_by_sid, pathologies_by_sid


def _collect_failures_parallel(
    sids: List[str],
    verbose: bool,
    workers: int,
) -> Tuple[List[FailedOp], Dict[str, XMLStatute], Dict[str, Set[tuple[str, str, str]]]]:
    """Parallel failure collection using ProcessPoolExecutor."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    t0 = time.time()
    all_failures: List[FailedOp] = []
    done = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_replay_one_for_failures, sid): sid for sid in sids
        }
        for future in as_completed(futures):
            done += 1
            if verbose and done % 50 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(sids) - done) / rate if rate > 0 else 0
                print(
                    f"  [{done}/{len(sids)}] {rate:.1f} stat/s, ETA {eta:.0f}s",
                    file=sys.stderr,
                )
            try:
                records = future.result()
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                continue
            for r in records:
                all_failures.append(FailedOp(
                    amendment_id=r["amendment_id"],
                    description=r["description"],
                    reason=r["reason"],
                    reason_code=str(r.get("reason_code") or ""),
                    target_section=r["target_section"],
                    target_chapter=r.get("target_chapter"),
                    target_unit_kind=_failure_target_unit_kind(r),
                ))

    if verbose:
        elapsed = time.time() - t0
        print(
            f"Replayed {done}/{len(sids)} statutes in {elapsed:.1f}s "
            f"({workers} workers)",
            file=sys.stderr,
        )
    return all_failures, {}, {}


# ---------------------------------------------------------------------------
# Detail categorisation helpers
# ---------------------------------------------------------------------------

def _failure_target_label(f: FailedOp) -> str:
    target_paragraph, target_item = _parse_desc_fields(f.description)
    if target_item is not None and target_paragraph is not None:
        return f"{f.target_section} § {target_paragraph} mom {target_item} kohta"
    if target_paragraph is not None:
        return f"{f.target_section} § {target_paragraph} mom"
    return f"{f.target_section} §"


def _parse_desc_fields(desc: str) -> Tuple[Optional[int], Optional[str]]:
    """Extract (target_paragraph, target_item) from a description string.

    AmendmentOp.description() appends:
      " N mom"   when target_paragraph is set
      " X kohta" when target_item is set
    Returns (paragraph_int_or_None, item_str_or_None).
    """
    paragraph: Optional[int] = None
    item: Optional[str] = None
    m_mom = re.search(r'\b(\d+)\s+mom\b', desc)
    if m_mom:
        paragraph = int(m_mom.group(1))
    m_kohta = re.search(r'\b(\S+)\s+kohta\b', desc)
    if m_kohta:
        item = m_kohta.group(1)
    return paragraph, item


def _categorize_failure(
    f: FailedOp,
    master: XMLStatute,
    source_pathologies: Optional[Set[tuple[str, str, str]]] = None,
) -> str:
    """Categorize one FailedOp into a root-cause label.

    Categories:
      renumber               — RENUMBER op (structural, not a content failure)
      kohta_amend_extract_fail — target paragraph exists but amendment extraction failed
      kohta_no_paras         — target subsection is content-only (no paragraph children)
      kohta_label_gap(max=N,want=M) — kohta label beyond available paragraphs
      kohta_mom_oor          — momentti target out of range for kohta op
      mom_oor(gap=N)         — subsection target out of range
      other                  — uncategorized
    """
    desc = f.description

    if source_pathologies:
        match = next((code for source_statute, code, target_label in source_pathologies if source_statute == f.amendment_id and target_label == _failure_target_label(f)), None)
        if match:
            return f"source_pathology:{match}"

    # RENUMBER ops — distinct structural category
    if desc.startswith("RENUMBER"):
        return "renumber"

    target_paragraph, target_item = _parse_desc_fields(desc)

    # Find the target section in master.ir
    sec_node = master.find_section(f.target_section, f.target_chapter)

    if target_item is not None:
        # This is a kohta op
        if sec_node is None:
            return "other"
        subsecs = [c for c in sec_node.children if c.kind == "subsection"]
        if not subsecs:
            return "other"

        # Determine which subsection to examine
        if target_paragraph is not None:
            if target_paragraph > len(subsecs):
                # momentti itself is out of range — kohta_mom_oor
                return "kohta_mom_oor"
            target_sub = subsecs[target_paragraph - 1]
        else:
            target_sub = subsecs[0]

        paras = [c for c in target_sub.children if c.kind == "paragraph"]
        if not paras:
            return "kohta_no_paras"

        # Check if matching label exists
        item_norm = _norm(target_item)
        matching = [p for p in paras if _norm(p.label or "") == item_norm]
        if matching:
            return "kohta_amend_extract_fail"

        # Label not found — report gap
        max_label_idx = len(paras)
        try:
            want_idx = int(re.sub(r'[^\d]', '', target_item) or "0")
        except ValueError:
            want_idx = 0
        return f"kohta_label_gap(max={max_label_idx},want={want_idx})"

    if target_paragraph is not None:
        # This is a mom (momentti/subsection) op
        if sec_node is None:
            return "other"
        subsecs = [c for c in sec_node.children if c.kind == "subsection"]
        actual_count = len(subsecs)
        gap = target_paragraph - actual_count
        return f"mom_oor(gap={gap})"

    return "other"


def _print_detail(
    failures: List[FailedOp],
    masters_by_sid: Dict[str, XMLStatute],
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]],
    pattern: Optional[str],
    top: int,
) -> None:
    """Print per-failure root-cause categorization with a summary table."""
    if pattern:
        failures = [f for f in failures if re.search(pattern, f.description, re.I)]

    # FailedOp.amendment_id is the amending statute, not the bench statute being
    # amended. Masters are keyed by bench sid. Match each failure to a master by
    # looking up its target_section in each replayed statute.
    def _find_master_for(fo: FailedOp) -> Optional[XMLStatute]:
        for m in masters_by_sid.values():
            if m.find_section(fo.target_section, fo.target_chapter) is not None:
                return m
        return None

    rows: List[Tuple[FailedOp, str]] = []
    for fo in failures:
        master = _find_master_for(fo)
        if master is not None:
            sid = next((sid for sid, m in masters_by_sid.items() if m is master), "")
            cat = _categorize_failure(fo, master, pathologies_by_sid.get(sid))
        else:
            # No master available (section not found in any replayed statute) —
            # still categorize what we can from the description alone
            if fo.description.startswith("RENUMBER"):
                cat = "renumber"
            else:
                cat = "other"
        rows.append((fo, cat))

    cat_counts: Counter[str] = Counter(cat for _, cat in rows)

    print(f"Total failures: {len(rows)}")
    print()
    print("=== Root-cause categories ===")
    for cat, count in cat_counts.most_common():
        print(f"  {count:4d}  {cat}")
    print()

    print(f"=== Detailed failure list ({len(rows)}) ===")
    for fo, cat in rows:
        print(f"  [{fo.amendment_id}] {fo.description}"
              f"  sec={fo.target_section} ch={fo.target_chapter}"
              f"  \u2192 {cat}")


def _print_summary(failures: List[FailedOp], pattern: Optional[str], top: int) -> None:
    """Print structured failure analysis."""
    if pattern:
        failures = [f for f in failures if re.search(pattern, f.description, re.I)]

    reason_counts = Counter(f.reason for f in failures)
    statute_counts = Counter(f.amendment_id.split("/")[0] + "/" + f.amendment_id.split("/")[1]
                             if "/" in f.amendment_id else f.amendment_id
                             for f in failures)
    # Group by target statute (the statute being amended, not the amendment)
    target_counts: Counter[str] = Counter()
    for f in failures:
        target_counts[f.target_section] += 1

    # Pattern analysis
    desc_patterns: Counter[str] = Counter()
    for f in failures:
        m = re.match(r'(INSERT|REPLACE|REPEAL)\s+(\S+)\s+\u00a7\s+(.+)', f.description)
        if m:
            rest_norm = re.sub(r'\d+', 'N', m.group(3))
            desc_patterns[f'{m.group(1)} X \u00a7 {rest_norm}'] += 1
        else:
            desc_patterns[f.description] += 1

    print(f"Total failures: {len(failures)}")
    print()

    print("=== Failure reasons ===")
    for reason, count in reason_counts.most_common(20):
        print(f"  {count:4d}  {reason}")
    print()

    print(f"=== Description patterns (top {top}) ===")
    for pat, count in desc_patterns.most_common(top):
        print(f"  {count:3d}  {pat}")
    print()

    print(f"=== Amendment sources (top {top}) ===")
    for sid, count in statute_counts.most_common(top):
        print(f"  {count:3d}  {sid}")
    print()

    print(f"=== All failures ({len(failures)}) ===")
    for f in failures:
        print(f"  [{f.amendment_id}] {f.description}  "
              f"kind={f.compat_target_kind_code} sec={f.target_section} ch={f.target_chapter}")


def main(
    statute_id: Optional[str] = None,
    pattern: Optional[str] = None,
    top: int = 15,
    verbose: bool = False,
    detail: bool = False,
    from_bench: Optional[str] = None,
    parallel: int = 1,
    save_cache: Optional[str] = None,
) -> int:
    if statute_id:
        sids = [statute_id]
    elif from_bench:
        # Try loading from cache first (instant if available)
        cached = _load_failure_cache(from_bench)
        if cached is not None and not detail:
            print(
                f"Loaded {len(cached)} cached failures from bench run "
                f"'{from_bench}'",
                file=sys.stderr,
            )
            _print_summary(cached, pattern, top)
            return 0
        # No cache or --detail needs masters — filter to imperfect statutes
        sids_or_none = _load_imperfect_sids_from_bench(from_bench)
        if sids_or_none is None:
            print(
                f"Bench run '{from_bench}' not found in data/bench_runs/",
                file=sys.stderr,
            )
            return 1
        sids = sids_or_none
        print(
            f"Replaying {len(sids)} imperfect statutes from bench run "
            f"'{from_bench}'",
            file=sys.stderr,
        )
    else:
        sids = _load_bench_sids()
        if not sids:
            return 1
        print(f"Replaying {len(sids)} statutes...", file=sys.stderr)

    failures, masters, pathologies_by_sid = _collect_failures(
        sids, verbose=verbose, need_masters=detail, parallel=parallel,
    )

    # Save cache if requested
    cache_label = save_cache or from_bench
    if cache_label and not detail:
        p = _save_failure_cache(cache_label, failures)
        print(f"Saved {len(failures)} failures to {p}", file=sys.stderr)

    if detail:
        _print_detail(failures, masters, pathologies_by_sid, pattern, top)
    else:
        _print_summary(failures, pattern, top)
    return 0
