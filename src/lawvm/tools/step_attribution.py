"""lawvm step-attribution — quantify WHERE accuracy loss happens in the pipeline.

For each statute, measures loss at four pipeline steps:

  1. Extraction   — how many ops were extracted (PEG + fallback)
  2. Compilation  — of extracted ops, canonical / failed plus recovery findings
  3. Application  — of compiled ops, how many applied successfully
  4. Materialization — section-by-section oracle comparison (REPLAY_EXTRA /
                       REPLAY_MISSING / CONTENT_DRIFT)

Then attributes each diverging section to the likeliest upstream step.

Usage:
    lawvm step-attribution 1993/1501
    lawvm step-attribution 1993/1501 --verbose
    lawvm step-attribution --corpus --top 50 --label attr_v1
    lawvm step-attribution --corpus --parallel 8 --label attr_v2
"""
from __future__ import annotations

import csv
import io
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.tools.section_keys import (
    extract_ir_sections,
    extract_oracle_sections,
    norm_section_label,
    reconcile_unique_unscoped_aliases,
    section_key_from_compiled_scope_row,
    section_key_from_compile_failure,
    section_key_sort_key,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SectionResult:
    """Per-section comparison outcome."""
    key: str          # normalised section num key
    status: str       # "match" | "content_drift" | "replay_extra" | "replay_missing"
    score: float      # Levenshtein similarity (1.0 for match/missing, -1.0 for extra)
    has_failed_op: bool = False   # a FailedOp targeted this section
    has_recovery_finding: bool = False  # a replay recovery finding targeted this section


@dataclass
class StepAttributionResult:
    """Full step-attribution record for one statute."""
    statute_id: str
    n_amendments: int
    n_compiled_ops: int       # raw extracted ops (compiled_ops list)
    n_canonical_ops: int      # canonical ops
    n_failed_ops: int         # compile failures
    n_sections_match: int
    n_content_drift: int
    n_replay_extra: int
    n_replay_missing: int
    n_sections_total: int     # union of replay + oracle sections
    overall_score: float      # mean similarity of compared sections
    # Attribution breakdown (estimated % of total divergence)
    attr_extraction_pct: float   # divergences attributable to recovery findings
    attr_application_pct: float  # divergences attributable to failed FailedOps
    attr_oracle_pct: float       # REPLAY_EXTRA (Finlex behind LawVM)
    attr_unknown_pct: float      # CONTENT_DRIFT with no clear cause
    error: str = ""
    section_results: List[SectionResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text helpers (mirror bench.py / diff.py)
# ---------------------------------------------------------------------------

def _clean(t: str) -> str:
    return re.sub(r'[^a-z0-9äöå]', '', t.lower())


def _norm_num(s: str) -> str:
    return norm_section_label(s)


def _section_sort_key(key: str) -> Tuple[int, str]:
    return section_key_sort_key(key)


# ---------------------------------------------------------------------------
# Section extraction from oracle lxml tree and replay IRNode
# ---------------------------------------------------------------------------

def _extract_oracle_sections(oracle_root: Any) -> Dict[str, Any]:
    """Extract {section_path: lxml_element} from oracle XML."""
    return extract_oracle_sections(oracle_root)


def _extract_replay_sections(ir_root: Any) -> Dict[str, Any]:
    """Extract {section_path: IRNode} from replay IR tree."""
    return extract_ir_sections(ir_root)


def _oracle_section_text(el: Any) -> str:
    from lxml import etree
    return etree.tostring(el, method="text", encoding="unicode").strip()


def _replay_section_text(node: Any) -> str:
    from lawvm.core.ir_helpers import irnode_to_text
    return irnode_to_text(node)


# ---------------------------------------------------------------------------
# Map failed/recovered ops to section keys they targeted
# ---------------------------------------------------------------------------

def _section_keys_from_failed_ops(failed_ops: List[Any]) -> set:
    """Extract normalised section-key strings from FailedOp records."""
    keys: set = set()
    for f in failed_ops:
        key = section_key_from_compile_failure(f)
        if key:
            keys.add(key)
    return keys


_RECOVERY_FINDING_KINDS = {
    "APPLY.UNCOVERED_BODY_RECOVERY",
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
    "ELAB.OMISSION_EXPANSION",
}


def _section_keys_from_recovery_findings(findings: List[Any]) -> set:
    """Extract section keys from section-scoped replay recovery findings."""
    keys: set = set()
    for finding in findings:
        kind = str(getattr(finding, "kind", "") or "")
        if kind not in _RECOVERY_FINDING_KINDS:
            continue
        detail = getattr(finding, "detail", None)
        if not isinstance(detail, dict):
            continue
        key = section_key_from_compiled_scope_row(detail)
        if key:
            keys.add(key)
    return keys


def _effective_source_adjudication(
    *,
    statute_id: str,
    replay_mode: str,
    replay_result: object | None,
    replay_meta: dict[str, object],
) -> SourceAdjudication | None:
    typed = getattr(replay_result, "source_adjudication", None)
    if typed is not None:
        return cast(SourceAdjudication, typed)

    raw_lineage = replay_meta.get("lineage")
    lineage: tuple[dict[str, Any], ...] = ()
    if isinstance(raw_lineage, (list, tuple)):
        lineage = cast(
            tuple[dict[str, Any], ...],
            tuple(row for row in raw_lineage if isinstance(row, dict)),
        )
    cutoff_date = str(replay_meta.get("cutoff_date") or "")
    oracle_version_amendment_id = str(replay_meta.get("oracle_version_amendment_id") or "")
    oracle_suspect = str(replay_meta.get("oracle_suspect") or "")
    html_noncommensurable_reason = str(replay_meta.get("html_noncommensurable_reason") or "")
    if not any(
        (
            cutoff_date,
            oracle_version_amendment_id,
            oracle_suspect,
            html_noncommensurable_reason,
            lineage,
        )
    ):
        return None
    return build_source_adjudication(
        statute_id=statute_id,
        replay_mode=replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=lineage,
    )


# ---------------------------------------------------------------------------
# Core single-statute attribution
# ---------------------------------------------------------------------------

def _run_single(
    statute_id: str,
    *,
    verbose: bool = False,
) -> StepAttributionResult:
    """Run step-attribution for one statute and return a structured result."""
    import Levenshtein

    # Suppress replay noise
    buf = io.StringIO()

    try:
        with redirect_stdout(buf):
            from lawvm.finland.grafter import replay_xml, get_ground_truth_tree

            compiled_ops: list[dict[str, object]] = []
            replay_meta: dict[str, object] = {}
            canonical_ops: list[Any] = []
            failed_ops: list[Any] = []
            master = replay_xml(
                statute_id,
                mode="finlex_oracle",
                compiled_ops_out=compiled_ops,
                replay_meta_out=replay_meta,
                lo_ops_out=canonical_ops,
                failed_ops_out=failed_ops,
            )

        oracle_root = get_ground_truth_tree(statute_id)
        if oracle_root is None:
            return StepAttributionResult(
                statute_id=statute_id,
                n_amendments=0,
                n_compiled_ops=0,
                n_canonical_ops=0,
                n_failed_ops=0,
                n_sections_match=0,
                n_content_drift=0,
                n_replay_extra=0,
                n_replay_missing=0,
                n_sections_total=0,
                overall_score=0.0,
                attr_extraction_pct=0.0,
                attr_application_pct=0.0,
                attr_oracle_pct=0.0,
                attr_unknown_pct=0.0,
                error="no_oracle",
            )

    except Exception as exc:
        return StepAttributionResult(
            statute_id=statute_id,
            n_amendments=0,
            n_compiled_ops=0,
            n_canonical_ops=0,
            n_failed_ops=0,
            n_sections_match=0,
            n_content_drift=0,
            n_replay_extra=0,
            n_replay_missing=0,
            n_sections_total=0,
            overall_score=0.0,
            attr_extraction_pct=0.0,
            attr_application_pct=0.0,
            attr_oracle_pct=0.0,
            attr_unknown_pct=0.0,
            error=str(exc)[:120],
        )

    # Amendment count from lineage
    source_adjudication = _effective_source_adjudication(
        statute_id=statute_id,
        replay_mode="finlex_oracle",
        replay_result=master,
        replay_meta=replay_meta,
    )
    if source_adjudication is not None:
        lineage = list(cast(list[dict[str, Any]], source_adjudication.lineage))
    else:
        lineage = []
    n_amendments = len(lineage)

    # Op counts
    n_compiled_ops = len(compiled_ops)
    n_canonical = len(canonical_ops)
    n_failed = len(failed_ops)

    # Build section-level op coverage maps
    failed_sec_keys = _section_keys_from_failed_ops(failed_ops)
    recovery_sec_keys = _section_keys_from_recovery_findings(list(master.findings))

    # Section comparison
    replay_secs = _extract_replay_sections(master.materialized_state.ir)
    oracle_secs = _extract_oracle_sections(oracle_root)
    replay_secs, oracle_secs = reconcile_unique_unscoped_aliases(
        replay_secs, oracle_secs
    )

    all_keys = sorted(set(replay_secs) | set(oracle_secs), key=_section_sort_key)

    section_results: List[SectionResult] = []
    n_match = 0
    n_drift = 0
    n_extra = 0
    n_missing = 0
    scores: List[float] = []

    for key in all_keys:
        r_node = replay_secs.get(key)
        o_el = oracle_secs.get(key)
        has_failed = key in failed_sec_keys
        has_recovery = key in recovery_sec_keys

        if r_node is None:
            # In oracle, not in replay
            section_results.append(SectionResult(
                key=key, status="replay_missing", score=0.0,
                has_failed_op=has_failed, has_recovery_finding=has_recovery,
            ))
            n_missing += 1
        elif o_el is None:
            # In replay, not in oracle
            section_results.append(SectionResult(
                key=key, status="replay_extra", score=-1.0,
                has_failed_op=has_failed, has_recovery_finding=has_recovery,
            ))
            n_extra += 1
        else:
            r_text = _clean(_replay_section_text(r_node))
            o_text = _clean(_oracle_section_text(o_el))
            if not r_text and not o_text:
                score = 1.0
            elif not r_text or not o_text:
                score = 0.0
            else:
                score = Levenshtein.ratio(r_text, o_text)
            scores.append(score)
            if score >= 0.9999:
                status = "match"
                n_match += 1
            else:
                status = "content_drift"
                n_drift += 1
            section_results.append(SectionResult(
                key=key, status=status, score=score,
                has_failed_op=has_failed, has_recovery_finding=has_recovery,
            ))

    overall_score = sum(scores) / len(scores) if scores else 0.0
    n_total = len(all_keys)

    # --- Attribution ---
    # Diverging sections: anything that is not a perfect match
    diverging = [s for s in section_results if s.status != "match"]
    n_diverging = len(diverging)

    if n_diverging == 0:
        attr_extraction_pct = 0.0
        attr_application_pct = 0.0
        attr_oracle_pct = 0.0
        attr_unknown_pct = 0.0
    else:
        # REPLAY_EXTRA: LawVM has sections oracle doesn't — Finlex is behind LawVM.
        # This is an oracle/source issue, not an extraction or application miss.
        oracle_issue = sum(1 for s in diverging if s.status == "replay_extra")

        # Application miss: REPLAY_MISSING or CONTENT_DRIFT where we have a
        # FailedOp targeting that section — the op was parsed but couldn't apply.
        application_miss = sum(
            1 for s in diverging
            if s.status in ("replay_missing", "content_drift") and s.has_failed_op
        )

        # Extraction miss: CONTENT_DRIFT or REPLAY_MISSING where a replay
        # recovery finding targeted the section.
        # (Exclude sections already counted as application_miss to avoid double-count.)
        extraction_miss = sum(
            1 for s in diverging
            if s.status in ("replay_missing", "content_drift")
            and s.has_recovery_finding
            and not s.has_failed_op
        )

        # Unknown: remaining divergences with no clear op linkage
        unknown = n_diverging - oracle_issue - application_miss - extraction_miss

        attr_extraction_pct = 100.0 * extraction_miss / n_diverging
        attr_application_pct = 100.0 * application_miss / n_diverging
        attr_oracle_pct = 100.0 * oracle_issue / n_diverging
        attr_unknown_pct = 100.0 * unknown / n_diverging

    return StepAttributionResult(
        statute_id=statute_id,
        n_amendments=n_amendments,
        n_compiled_ops=n_compiled_ops,
        n_canonical_ops=n_canonical,
        n_failed_ops=n_failed,
        n_sections_match=n_match,
        n_content_drift=n_drift,
        n_replay_extra=n_extra,
        n_replay_missing=n_missing,
        n_sections_total=n_total,
        overall_score=overall_score,
        attr_extraction_pct=attr_extraction_pct,
        attr_application_pct=attr_application_pct,
        attr_oracle_pct=attr_oracle_pct,
        attr_unknown_pct=attr_unknown_pct,
        section_results=section_results if verbose else [],
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "[" + "█" * filled + "·" * (width - filled) + "]"


def _print_single(result: StepAttributionResult, verbose: bool) -> None:
    sid = result.statute_id
    if result.error:
        print(f"ERROR for {sid}: {result.error}")
        return

    n_div = (result.n_content_drift + result.n_replay_extra + result.n_replay_missing)

    print(f"Step-Attribution for {sid}")
    print(f"  Amendments    : {result.n_amendments}")
    print()
    print(f"  Extraction    : {result.n_compiled_ops} ops extracted")
    print(f"  Compilation   : {result.n_canonical_ops} canonical  "
          f"{result.n_failed_ops} failed")
    print()
    print("  Oracle compare:")
    print(f"    Sections match  : {result.n_sections_match}/{result.n_sections_total}")
    print(f"    CONTENT_DRIFT   : {result.n_content_drift}")
    print(f"    REPLAY_EXTRA    : {result.n_replay_extra}  "
          f"(replay > oracle — oracle/Finlex source issue)")
    print(f"    REPLAY_MISSING  : {result.n_replay_missing}  "
          f"(oracle > replay — LawVM behind)")
    print(f"    Overall score   : {result.overall_score:.2%}")
    print()
    if n_div > 0:
        print(f"  Loss attribution  ({n_div} diverging sections):")
        print(f"    Extraction miss : {result.attr_extraction_pct:5.1f}%  "
              f"{_bar(result.attr_extraction_pct)}")
        print(f"    Application miss: {result.attr_application_pct:5.1f}%  "
              f"{_bar(result.attr_application_pct)}")
        print(f"    Oracle/source   : {result.attr_oracle_pct:5.1f}%  "
              f"{_bar(result.attr_oracle_pct)}")
        print(f"    Unknown         : {result.attr_unknown_pct:5.1f}%  "
              f"{_bar(result.attr_unknown_pct)}")
    else:
        print("  No divergences — perfect match.")
    print()

    if verbose and result.section_results:
        divs = [s for s in result.section_results if s.status != "match"]
        if divs:
            print(f"  Diverging sections ({len(divs)}):")
            for s in sorted(divs, key=lambda x: _section_sort_key(x.key)):
                flags = []
                if s.has_failed_op:
                    flags.append("failed_op")
                if s.has_recovery_finding:
                    flags.append("recovery_finding")
                flag_str = "  [" + ", ".join(flags) + "]" if flags else ""
                if s.status == "content_drift":
                    print(f"    {s.key:<10}  CONTENT_DRIFT  score={s.score:.2%}{flag_str}")
                elif s.status == "replay_extra":
                    print(f"    {s.key:<10}  REPLAY_EXTRA{flag_str}")
                elif s.status == "replay_missing":
                    print(f"    {s.key:<10}  REPLAY_MISSING{flag_str}")
            print()


# ---------------------------------------------------------------------------
# Corpus mode helpers
# ---------------------------------------------------------------------------

def _load_bench_corpus() -> List[str]:
    here = Path(__file__).resolve()
    lawvm_dir = here.parents[3]
    primary = lawvm_dir / "data" / "finland" / "bench_corpus.csv"
    fallback = lawvm_dir / ".tmp" / "batch_test_list.csv"
    csv_path = primary if primary.exists() else fallback
    if not csv_path.exists():
        print(f"ERROR: bench corpus not found: {csv_path}", file=sys.stderr)
        return []
    sids = []
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                sids.append(row[1].strip())
    return sids


def _worker(sid: str) -> StepAttributionResult:
    """Top-level worker for ProcessPoolExecutor (must be module-level)."""
    return _run_single(sid, verbose=False)


def _run_corpus(
    top: int,
    workers: int,
    label: Optional[str],
) -> None:
    sids = _load_bench_corpus()
    if not sids:
        return

    # Optionally scope to worst N from bench history
    if top and top < len(sids):
        sids = sids[:top]  # first top from CSV (sorted by amendment count)

    total = len(sids)
    print(f"Running step-attribution for {total} statutes  "
          f"(workers={workers})...")

    results: List[StepAttributionResult] = cast(List[StepAttributionResult], [None] * total)
    done = 0

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_worker, sid): i
                for i, sid in enumerate(sids)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    results[idx] = StepAttributionResult(
                        statute_id=sids[idx],
                        n_amendments=0, n_compiled_ops=0, n_canonical_ops=0,
                        n_failed_ops=0,
                        n_sections_match=0, n_content_drift=0,
                        n_replay_extra=0, n_replay_missing=0,
                        n_sections_total=0, overall_score=0.0,
                        attr_extraction_pct=0.0, attr_application_pct=0.0,
                        attr_oracle_pct=0.0, attr_unknown_pct=0.0,
                        error=str(exc)[:120],
                    )
                done += 1
                if done % 50 == 0 or done == total:
                    print(f"  [{done}/{total}]", flush=True)
    else:
        for i, sid in enumerate(sids, 1):
            results[i - 1] = _run_single(sid, verbose=False)
            if i % 50 == 0 or i == total:
                print(f"  [{i}/{total}]", flush=True)

    # Aggregate
    ok = [r for r in results if not r.error]
    errors = [r for r in results if r.error]

    total_div = sum(
        r.n_content_drift + r.n_replay_extra + r.n_replay_missing
        for r in ok
    )

    agg_extraction = sum(
        r.n_content_drift * r.attr_extraction_pct / 100
        + r.n_replay_missing * r.attr_extraction_pct / 100
        for r in ok
    )
    agg_application = sum(
        (r.n_content_drift + r.n_replay_missing) * r.attr_application_pct / 100
        for r in ok
    )
    agg_oracle = sum(
        r.n_replay_extra * r.attr_oracle_pct / 100
        + r.n_content_drift * r.attr_oracle_pct / 100
        for r in ok
    )
    agg_unknown = sum(
        (r.n_content_drift + r.n_replay_extra + r.n_replay_missing)
        * r.attr_unknown_pct / 100
        for r in ok
    )

    total_attributed = agg_extraction + agg_application + agg_oracle + agg_unknown
    def _pct(x: float) -> float:
        return 100.0 * x / total_attributed if total_attributed > 0 else 0.0

    print()
    print(f"=== Step Attribution Summary ({len(ok)} statutes, {total_div} total divergences) ===")
    print(f"  {'Step':<22}  {'Divergences':>12}  {'% of total':>11}")
    print("  " + "-" * 50)
    print(f"  {'Extraction miss':<22}  {agg_extraction:>12.0f}  {_pct(agg_extraction):>10.1f}%")
    print(f"  {'Application miss':<22}  {agg_application:>12.0f}  {_pct(agg_application):>10.1f}%")
    print(f"  {'Oracle/source':<22}  {agg_oracle:>12.0f}  {_pct(agg_oracle):>10.1f}%")
    print(f"  {'Unknown':<22}  {agg_unknown:>12.0f}  {_pct(agg_unknown):>10.1f}%")
    print()

    # Op summary
    total_ops = sum(r.n_compiled_ops for r in ok)
    total_failed = sum(r.n_failed_ops for r in ok)
    total_canon = sum(r.n_canonical_ops for r in ok)
    print(f"  Op pipeline: {total_ops} extracted  "
          f"{total_canon} canonical  "
          f"{total_failed} failed")
    if total_ops > 0:
        print(f"  Failure rate: {100.0 * total_failed / total_ops:.1f}%")
    if errors:
        print(f"\n  Errors ({len(errors)}): "
              + ", ".join(r.statute_id for r in errors[:5])
              + ("..." if len(errors) > 5 else ""))

    # Worst statutes by divergence count
    worst = sorted(ok, key=lambda r: (
        r.n_content_drift + r.n_replay_missing + r.n_replay_extra
    ), reverse=True)[:20]
    if worst:
        print("\n  Top 20 worst statutes by divergence count:")
        print(f"  {'Statute':<14}  {'Div':>5}  {'Extra':>6}  {'Missing':>8}  "
              f"{'Drift':>7}  {'Extr%':>6}  {'App%':>5}  {'Orc%':>5}  {'Unk%':>5}")
        print("  " + "-" * 80)
        for r in worst:
            n_div_r = r.n_content_drift + r.n_replay_extra + r.n_replay_missing
            print(
                f"  {r.statute_id:<14}  {n_div_r:>5}  "
                f"{r.n_replay_extra:>6}  {r.n_replay_missing:>8}  "
                f"{r.n_content_drift:>7}  "
                f"{r.attr_extraction_pct:>5.1f}%  "
                f"{r.attr_application_pct:>4.1f}%  "
                f"{r.attr_oracle_pct:>4.1f}%  "
                f"{r.attr_unknown_pct:>4.1f}%"
            )

    # Optionally save to CSV
    if label:
        here = Path(__file__).resolve()
        lawvm_dir = here.parents[3]
        out_dir = lawvm_dir / "data" / "bench_runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{label}_step_attr.csv"
        _save_corpus_csv(results, out_path)
        print(f"\n  Saved: {out_path}")


def _save_corpus_csv(results: List[StepAttributionResult], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "statute_id", "n_amendments",
            "n_compiled_ops", "n_canonical_ops", "n_failed_ops",
            "n_sections_total", "n_match", "n_drift", "n_extra", "n_missing",
            "overall_score",
            "attr_extraction_pct", "attr_application_pct",
            "attr_oracle_pct", "attr_unknown_pct",
            "error",
        ])
        for r in results:
            w.writerow([
                r.statute_id, r.n_amendments,
                r.n_compiled_ops, r.n_canonical_ops, r.n_failed_ops,
                r.n_sections_total, r.n_sections_match,
                r.n_content_drift, r.n_replay_extra, r.n_replay_missing,
                f"{r.overall_score:.4f}",
                f"{r.attr_extraction_pct:.1f}", f"{r.attr_application_pct:.1f}",
                f"{r.attr_oracle_pct:.1f}", f"{r.attr_unknown_pct:.1f}",
                r.error,
            ])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    corpus_mode = getattr(args, "corpus", False)
    verbose = getattr(args, "verbose", False)
    top = getattr(args, "top", None)
    label = getattr(args, "label", None)
    workers = getattr(args, "parallel", None)

    if workers is None:
        import os
        workers = max(8, (os.cpu_count() or 4))

    if corpus_mode:
        _run_corpus(
            top=top or 0,
            workers=workers,
            label=label,
        )
        return

    statute_id = getattr(args, "statute_id", None)
    if not statute_id:
        print("ERROR: provide a statute_id or use --corpus", file=sys.stderr)
        sys.exit(1)

    result = _run_single(statute_id, verbose=verbose)
    _print_single(result, verbose=verbose)
