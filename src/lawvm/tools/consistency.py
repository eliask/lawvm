"""lawvm check-consistency — replay vs timeline internal consistency checker.

Verifies that the two paths to legal state (replay-tree and compiled timelines)
are mutually coherent for a Finnish statute.  This is NOT a bench tool — it
does not score against the oracle.  It checks internal structural invariants:

  1. SECTION_NO_TIMELINE  — section in master.ir has no timeline entry
  2. TIMELINE_NO_SECTION  — timeline entry has no corresponding section in master.ir
  3. CONTENT_DRIFT        — latest timeline version content differs from master.ir
  4. REPLAY_EXTRA         — section in master.ir not present in oracle
  5. REPLAY_MISSING       — section in oracle not present in master.ir

Categories 1-3 are internal structural issues (replay vs timelines incoherence).
Categories 4-5 are replay vs oracle divergences (standard bench signal).

Usage:
    lawvm check-consistency 2002/738
    lawvm check-consistency 2002/738 --verbose
    lawvm check-consistency --corpus --top 100 --label consist_v1
    lawvm check-consistency --corpus --top 100 --label consist_v1 --parallel 4
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from lawvm.core.phase_result import PhaseResult


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ConsistencyIssue:
    kind: str           # SECTION_NO_TIMELINE | TIMELINE_NO_SECTION | CONTENT_DRIFT |
                        # REPLAY_EXTRA | REPLAY_MISSING
    address: str        # human-readable address (e.g. "section:3", "chapter:2/section:5")
    detail: str = ""    # optional human-readable detail


@dataclass
class ConsistencyResult:
    sid: str
    replay_sections: int = 0
    timeline_entries: int = 0
    oracle_sections: int = 0
    issues: List[ConsistencyIssue] = field(default_factory=list)
    error: str = ""     # non-empty if replay itself failed

    # Derived counts (populated after issues list is built)
    @property
    def section_no_timeline(self) -> List[ConsistencyIssue]:
        return [i for i in self.issues if i.kind == "SECTION_NO_TIMELINE"]

    @property
    def timeline_no_section(self) -> List[ConsistencyIssue]:
        return [i for i in self.issues if i.kind == "TIMELINE_NO_SECTION"]

    @property
    def content_drift(self) -> List[ConsistencyIssue]:
        return [i for i in self.issues if i.kind == "CONTENT_DRIFT"]

    @property
    def replay_extra(self) -> List[ConsistencyIssue]:
        return [i for i in self.issues if i.kind == "REPLAY_EXTRA"]

    @property
    def replay_missing(self) -> List[ConsistencyIssue]:
        return [i for i in self.issues if i.kind == "REPLAY_MISSING"]

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        n_internal = len(self.section_no_timeline) + len(self.timeline_no_section) + len(self.content_drift)
        n_oracle = len(self.replay_extra) + len(self.replay_missing)
        if n_internal == 0 and n_oracle == 0:
            return "CLEAN"
        if n_internal > 0:
            return "INTERNAL_DRIFT"
        return "ORACLE_ONLY"   # only oracle divergence, timelines internally consistent

    def to_phase_result(self) -> "PhaseResult":
        """Convert this ConsistencyResult into a PhaseResult carrying Findings.

        Internal consistency issues (SECTION_NO_TIMELINE, TIMELINE_NO_SECTION,
        CONTENT_DRIFT) map to direct observation findings. Oracle-vs-replay
        gaps (REPLAY_EXTRA, REPLAY_MISSING) are informational source-pathology
        findings. A replay error produces a blocking obligation finding.

        The PhaseResult.output is this ConsistencyResult.
        """
        from lawvm.core.phase_result import Finding, PhaseResult

        findings: List[Finding] = []

        if self.error:
            findings.append(Finding(
                kind="APPLY.TREE_INVARIANT_VIOLATION",
                role="violation",
                stage="check_consistency",
                detail={
                    "sid": self.sid,
                    "error": self.error,
                    "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
                },
                blocking=True,
            ))
            return PhaseResult(
                output=self,
                findings=tuple(findings),
            )

        _ISSUE_KIND_MAP = {
            "SECTION_NO_TIMELINE": "TIME.SECTION_NO_TIMELINE",
            "TIMELINE_NO_SECTION": "TIME.TIMELINE_NO_SECTION",
            "CONTENT_DRIFT":       "TIME.CONTENT_DRIFT",
        }

        for issue in self.issues:
            obs_kind = _ISSUE_KIND_MAP.get(issue.kind)
            if obs_kind is not None:
                findings.append(Finding(
                    kind=obs_kind,
                    role="observation",
                    stage="check_consistency",
                    detail={"sid": self.sid, "address": issue.address, "detail": issue.detail},
                    blocking=False,
                ))
            # REPLAY_EXTRA / REPLAY_MISSING: emit as source_pathology observations
            # (informational bench-divergence signal, not internal incoherence)
            elif issue.kind in ("REPLAY_EXTRA", "REPLAY_MISSING"):
                findings.append(Finding(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="check_consistency",
                    detail={
                        "sid": self.sid,
                        "address": issue.address,
                        "sub_kind": issue.kind,
                        "detail": issue.detail,
                    },
                    blocking=False,
                ))

        return PhaseResult(
            output=self,
            findings=tuple(findings),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr_str(path: Tuple[Tuple[str, str], ...]) -> str:
    """Convert LegalAddress path tuple to human-readable string."""
    return "/".join(f"{k}:{v}" for k, v in path)


def _sections_from_ir(ir_node) -> Dict[str, object]:
    """Recursively collect all section-kind nodes from an IRNode tree.

    Returns dict mapping address-string → IRNode for each section.
    We use section kind only (not subsections) to match the oracle comparison
    in diff.py and avoid noise from positional subsection labels.
    """
    from lawvm.core.timeline import _iter_nodes_with_address

    # Wrap bare IRNode body in a fake IRStatute for _iter_nodes_with_address
    if hasattr(ir_node, 'body'):
        body = ir_node.body
    else:
        body = ir_node

    result: Dict[str, object] = {}
    for addr, node in _iter_nodes_with_address(body):
        if node.kind == "section":
            key = _addr_str(addr.path)
            if key not in result:
                result[key] = node
    return result


def _sections_from_oracle(oracle_root) -> Dict[str, Any]:
    """Extract oracle sections keyed by their full path where available."""
    from lawvm.tools.section_keys import extract_oracle_sections
    return extract_oracle_sections(oracle_root)


def _irnode_text_clean(node) -> str:
    """Normalised text from an IRNode for drift comparison."""
    import re
    from lawvm.core.ir_helpers import irnode_to_text
    raw = irnode_to_text(node)
    return re.sub(r'[^a-z0-9äöå]', '', raw.lower())


def _oracle_text_clean(el) -> str:
    """Normalised text from an lxml element for drift comparison."""
    import re
    from lxml import etree
    raw = etree.tostring(el, method="text", encoding="unicode").strip()
    return re.sub(r'[^a-z0-9äöå]', '', raw.lower())


def _selection_detail(selection: object) -> dict[str, Any]:
    """Extract ambiguity-preserving metadata from a selection result."""
    certificate = getattr(selection, "certificate", None)
    return {
        "selection_status": getattr(selection, "status", ""),
        "required_dimensions": tuple(getattr(selection, "required_dimensions", ()) or ()),
        "candidate_count": getattr(certificate, "candidate_count", 0) if certificate is not None else 0,
    }


def _selection_note(selection: object) -> str:
    """Render a compact ambiguity note for human-readable diagnostics."""
    detail = _selection_detail(selection)
    return (
        f"selection_status={detail['selection_status']}; "
        f"required_dimensions={detail['required_dimensions']!r}; "
        f"candidate_count={detail['candidate_count']}"
    )


def _section_versions_from_timelines(
    timelines: Dict[Any, Any],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Select latest section versions while preserving ambiguous-scope notes."""
    from lawvm.core.timeline import select_active_version_ex

    tl_sections: Dict[str, Any] = {}
    selection_notes: Dict[str, str] = {}
    for addr, tl in timelines.items():
        # Only look at timelines whose leaf kind is "section"
        if addr.path and addr.path[-1][0] == "section":
            key = _addr_str(addr.path)
            selection = select_active_version_ex(tl, as_of="9999-12-31")
            if selection.status == "selected" and selection.version is not None:
                tl_sections[key] = selection.version
            elif selection.status == "ambiguous_missing_scope":
                selection_notes[key] = _selection_note(selection)
    return tl_sections, selection_notes


# ---------------------------------------------------------------------------
# Core checker for one statute
# ---------------------------------------------------------------------------

def check_one(sid: str) -> ConsistencyResult:
    """Run full consistency check for one Finnish statute.

    Steps:
      1. replay_xml → get typed replay products
      2. Collect sections from master.materialized_state.ir
      3. Collect timeline entries (section-level, latest version)
      4. Check internal consistency: SECTION_NO_TIMELINE, TIMELINE_NO_SECTION, CONTENT_DRIFT
      5. Collect oracle sections and check REPLAY_EXTRA / REPLAY_MISSING
    """
    result = ConsistencyResult(sid=sid)

    try:
        from lawvm.finland.grafter import replay_xml, get_ground_truth_tree
        import io
        import contextlib

        # Suppress verbose replay output so corpus mode stays readable
        _null = io.StringIO()
        with contextlib.redirect_stdout(_null):
            master = replay_xml(sid)

    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception as e:
        result.error = str(e)[:200]
        return result

    # --- Step 2: sections from final PIT-materialized state ---
    ir_sections = _sections_from_ir(master.materialized_state.ir)
    result.replay_sections = len(ir_sections)

    # --- Step 3: timeline entries (section-level only) ---
    timelines = getattr(master, "timelines", None) or {}

    # Build a map: addr_string → latest ProvisionVersion for section-level entries
    tl_sections, tl_selection_notes = _section_versions_from_timelines(timelines)

    result.timeline_entries = len(tl_sections)

    # --- Step 4: internal consistency ---

    # 4a. Sections in master.ir with no timeline entry
    for addr_key, ir_node in ir_sections.items():
        if addr_key not in tl_sections:
            result.issues.append(ConsistencyIssue(
                kind="SECTION_NO_TIMELINE",
                address=addr_key,
                detail=tl_selection_notes.get(addr_key, ""),
            ))

    # 4b. Timeline entries with no corresponding section in master.ir
    for addr_key, version in tl_sections.items():
        if addr_key not in ir_sections:
            result.issues.append(ConsistencyIssue(
                kind="TIMELINE_NO_SECTION",
                address=addr_key,
            ))

    # 4c. Content drift: section exists in both, but text differs
    for addr_key in ir_sections:
        if addr_key not in tl_sections:
            continue   # already reported as SECTION_NO_TIMELINE
        version = tl_sections[addr_key]
        if version.content is None:
            # Tombstone — ir should ideally not have it, but don't double-report
            continue
        ir_text = _irnode_text_clean(ir_sections[addr_key])
        tl_text = _irnode_text_clean(version.content)
        if ir_text != tl_text:
            # Brief diff context: show first diverging 40 chars from each
            ir_snip = ir_text[:40] if ir_text else "(empty)"
            tl_snip = tl_text[:40] if tl_text else "(empty)"
            result.issues.append(ConsistencyIssue(
                kind="CONTENT_DRIFT",
                address=addr_key,
                detail=f"ir={ir_snip!r} tl={tl_snip!r}",
            ))

    # --- Step 5: replay vs oracle ---
    try:
        oracle_root = get_ground_truth_tree(sid)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        oracle_root = None

    if oracle_root is not None:
        from lawvm.tools.section_keys import reconcile_unique_unscoped_aliases

        oracle_sections = _sections_from_oracle(oracle_root)
        ir_sections, oracle_sections = reconcile_unique_unscoped_aliases(
            ir_sections, oracle_sections
        )
        result.oracle_sections = len(oracle_sections)

        for oracle_key in oracle_sections:
            if oracle_key not in ir_sections:
                result.issues.append(ConsistencyIssue(
                    kind="REPLAY_MISSING",
                    address=oracle_key,
                    detail="present in oracle, absent in replay",
                ))

        for full_key in ir_sections:
            if full_key not in oracle_sections:
                result.issues.append(ConsistencyIssue(
                    kind="REPLAY_EXTRA",
                    address=full_key,
                    detail="present in replay, absent in oracle",
                ))

    return result


# ---------------------------------------------------------------------------
# Worker for ProcessPoolExecutor (must be module-level)
# ---------------------------------------------------------------------------

def _check_one_worker(sid: str) -> ConsistencyResult:
    """Module-level wrapper so ProcessPoolExecutor can pickle it."""
    return check_one(sid)


# ---------------------------------------------------------------------------
# Single-statute display
# ---------------------------------------------------------------------------

def _print_single(result: ConsistencyResult, verbose: bool = False) -> None:
    sid = result.sid
    print(f"\n=== Consistency Check: {sid} ===")

    if result.error:
        print(f"  ERROR: {result.error}")
        return

    print(f"  Replay sections  : {result.replay_sections}")
    print(f"  Timeline entries : {result.timeline_entries}")
    if result.oracle_sections:
        print(f"  Oracle sections  : {result.oracle_sections}")

    # Internal
    snt = result.section_no_timeline
    tns = result.timeline_no_section
    cd  = result.content_drift
    print()
    print(f"  SECTION_NO_TIMELINE : {len(snt)}")
    if snt and verbose:
        for i in snt:
            print(f"    - {i.address}")
    print(f"  TIMELINE_NO_SECTION : {len(tns)}")
    if tns and verbose:
        for i in tns:
            print(f"    - {i.address}")
    print(f"  CONTENT_DRIFT       : {len(cd)}")
    if cd:
        for i in cd[:5 if not verbose else len(cd)]:
            print(f"    - {i.address}")
            if i.detail:
                print(f"      {i.detail}")
        if not verbose and len(cd) > 5:
            print(f"    ... ({len(cd) - 5} more — use --verbose to show all)")

    # Oracle
    if result.oracle_sections:
        re_extra   = result.replay_extra
        re_missing = result.replay_missing
        print()
        print("  REPLAY_VS_ORACLE:")
        matched = result.replay_sections - len(re_extra)
        print(f"    Match        : {max(0, matched)}")
        print(f"    REPLAY_EXTRA : {len(re_extra)}")
        print(f"    REPLAY_MISSING: {len(re_missing)}")
        if verbose:
            for i in re_extra[:10]:
                print(f"      EXTRA  {i.address}")
            for i in re_missing[:10]:
                print(f"      MISS   {i.address}")

    print()
    n_internal = len(snt) + len(tns) + len(cd)
    n_oracle = len(result.replay_extra) + len(result.replay_missing)
    verdict = result.verdict
    if verdict == "CLEAN":
        print("  Verdict: CLEAN")
    elif verdict == "INTERNAL_DRIFT":
        print(f"  Verdict: INTERNAL_DRIFT ({n_internal} internal issues, {n_oracle} oracle gaps)")
    else:
        print(f"  Verdict: ORACLE_ONLY ({n_oracle} oracle gaps, timelines internally consistent)")


# ---------------------------------------------------------------------------
# Corpus mode
# ---------------------------------------------------------------------------

def _load_corpus(corpus_path: Optional[str], top: Optional[int]) -> List[str]:
    """Load statute IDs from the standard bench corpus CSV."""
    import csv as _csv
    if corpus_path is None:
        here = Path(__file__).resolve()
        lawvm_dir = here.parent.parent.parent.parent
        primary = lawvm_dir / "data" / "finland" / "bench_corpus.csv"
        if primary.exists():
            corpus_path = str(primary)
        else:
            fallback = lawvm_dir / ".tmp" / "batch_test_list.csv"
            corpus_path = str(fallback)

    sids: List[str] = []
    with open(corpus_path, newline="") as f:
        for row in _csv.reader(f):
            if len(row) >= 2:
                try:
                    int(row[0])
                    sids.append(row[1].strip())
                except (ValueError, IndexError):
                    pass

    if top is not None:
        sids = sids[:top]
    return sids


def _run_corpus(
    sids: List[str],
    workers: int,
    verbose: bool,
) -> List[ConsistencyResult]:
    """Run check_one over sids, optionally in parallel."""
    total = len(sids)
    results: List[Optional[ConsistencyResult]] = [None] * total

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_check_one_worker, sid): i
                for i, sid in enumerate(sids)
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res = future.result()
                except (NameError, TypeError, AttributeError):
                    raise  # programming bugs — fail loud
                except Exception as exc:
                    sid = sids[idx]
                    res = ConsistencyResult(sid=sid, error=str(exc)[:200])
                results[idx] = res
                done += 1
                if verbose:
                    v = res.verdict
                    nd = len(res.issues)
                    print(f"[{done}/{total}] {res.sid:15s}  {v}  issues={nd}")
        return [r for r in results if r is not None]

    # Sequential
    for i, sid in enumerate(sids, 1):
        t0 = time.time()
        res = check_one(sid)
        elapsed = time.time() - t0
        results[i - 1] = res
        if verbose:
            print(f"[{i}/{total}] {sid:15s}  {res.verdict}  issues={len(res.issues)}  ({elapsed:.1f}s)")
    return [r for r in results if r is not None]


def _print_corpus_summary(results: List[ConsistencyResult], top_drift: int = 10) -> None:
    total = len(results)
    errors = [r for r in results if r.error]
    clean = [r for r in results if not r.error and r.verdict == "CLEAN"]
    internal = [r for r in results if not r.error and r.verdict == "INTERNAL_DRIFT"]
    oracle_only = [r for r in results if not r.error and r.verdict == "ORACLE_ONLY"]

    print(f"\n=== Consistency Summary ({total} statutes) ===")
    print(f"  Clean             : {len(clean):4d} ({100*len(clean)/total:.1f}%)")
    print(f"  Internal drift    : {len(internal):4d} ({100*len(internal)/total:.1f}%)  -- replay vs timeline incoherence")
    print(f"  Oracle-only gaps  : {len(oracle_only):4d} ({100*len(oracle_only)/total:.1f}%)  -- standard bench divergence")
    print(f"  Errors            : {len(errors):4d} ({100*len(errors)/total:.1f}%)")

    # Breakdown of internal drift types
    total_snt = sum(len(r.section_no_timeline) for r in results)
    total_tns = sum(len(r.timeline_no_section) for r in results)
    total_cd  = sum(len(r.content_drift) for r in results)
    print()
    print("  Internal drift breakdown (all statutes):")
    print(f"    SECTION_NO_TIMELINE : {total_snt}")
    print(f"    TIMELINE_NO_SECTION : {total_tns}")
    print(f"    CONTENT_DRIFT       : {total_cd}")

    # Worst by content drift
    if internal:
        by_cd = sorted(internal, key=lambda r: -len(r.content_drift))
        shown = by_cd[:top_drift]
        print(f"\n  Worst {len(shown)} by CONTENT_DRIFT:")
        for r in shown:
            print(f"    {r.sid:15s}  drift={len(r.content_drift)}  "
                  f"no_tl={len(r.section_no_timeline)}  no_sec={len(r.timeline_no_section)}")

    # Worst by oracle gaps
    all_with_oracle = [r for r in results if r.oracle_sections > 0]
    if all_with_oracle:
        by_gaps = sorted(all_with_oracle, key=lambda r: -(len(r.replay_extra) + len(r.replay_missing)))
        shown_o = [r for r in by_gaps if len(r.replay_extra) + len(r.replay_missing) > 0][:top_drift]
        if shown_o:
            print(f"\n  Worst {len(shown_o)} by REPLAY vs ORACLE gaps:")
            for r in shown_o:
                print(f"    {r.sid:15s}  extra={len(r.replay_extra)}  missing={len(r.replay_missing)}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: "argparse.Namespace") -> None:
    corpus_mode = getattr(args, "corpus", False)
    verbose = getattr(args, "verbose", False)

    if corpus_mode:
        top = getattr(args, "top", None)
        corpus_path = getattr(args, "corpus_path", None)
        import os as _os
        workers = getattr(args, "parallel", None) or max(8, _os.cpu_count() or 4)
        label = getattr(args, "label", None)

        print("Loading corpus...", file=sys.stderr)
        sids = _load_corpus(corpus_path, top)
        print(f"Running consistency check on {len(sids)} statutes (workers={workers})...",
              file=sys.stderr)

        t0 = time.time()
        results = _run_corpus(sids, workers=workers, verbose=verbose)
        elapsed = time.time() - t0

        _print_corpus_summary(results, top_drift=10)
        print(f"\n  Total time: {elapsed:.1f}s")

        if label:
            # Save per-statute CSV to data/bench_runs/
            here = Path(__file__).resolve()
            lawvm_dir = here.parent.parent.parent.parent
            runs_dir = lawvm_dir / "data" / "bench_runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            out_path = runs_dir / f"{label}_consistency.csv"
            import csv as _csv
            with open(out_path, "w", newline="") as f:
                w = _csv.writer(f)
                w.writerow(["sid", "verdict", "replay_sections", "timeline_entries",
                             "oracle_sections", "section_no_tl", "tl_no_section",
                             "content_drift", "replay_extra", "replay_missing", "error"])
                for r in results:
                    w.writerow([
                        r.sid, r.verdict, r.replay_sections, r.timeline_entries,
                        r.oracle_sections, len(r.section_no_timeline),
                        len(r.timeline_no_section), len(r.content_drift),
                        len(r.replay_extra), len(r.replay_missing), r.error,
                    ])
            print(f"\n  Saved to {out_path}")

    else:
        sid = getattr(args, "statute_id", None)
        if not sid:
            print("error: provide a statute ID or use --corpus", file=sys.stderr)
            sys.exit(1)
        result = check_one(sid)
        _print_single(result, verbose=verbose)
        # Exit 1 if any internal drift found (useful for CI)
        if result.verdict == "INTERNAL_DRIFT":
            sys.exit(1)
