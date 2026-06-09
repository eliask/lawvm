"""lawvm reconcile --sweep — corpus-wide replay-L1 vs oracle-L1 self-audit.

This is the corpus-scale generalization of the single-statute reconcile mode
(`lawvm reconcile <statute>` with no selector). It iterates the Finland statute
corpus, runs replay-L1 vs oracle-L1 per section, and produces a RANKED
divergence report — the mechanical analogue of an audit that falsifies its own
claims: most sections AGREE (replay reproduces the consolidated text); the
sections that DISAGREE are the signal, and ranking them surfaces where replay or
the oracle is wrong.

Selection: the full corpus (~60k statutes) is too slow to reconcile section by
section in one pass, so by default the sweep samples the highest-amendment-count
statutes (amendment count from the amendment index — the same graph replay
uses). Selection and skips are LOGGED explicitly into the report; nothing is
silently truncated.

Classification (per the reconcile verb):
  temporal   — oracle carries a dated change replay did not apply (or vice versa)
  editorial  — text differs with no temporal straddle (parse/dedup/drift)
  presence   — one side has no provision at the selector
  data_defect — replay could not even run for the statute (replay error /
                corpus gap) — a structural defect, not a mere text divergence

Outputs (committed under reports/):
  reconcile_sweep_<label>.csv  — one row per diverging section (+ per-statute
                                 error rows), ranked
  reconcile_sweep_<label>.md   — short ranked summary + worst-N statutes
"""
from __future__ import annotations

import contextlib
import csv
import datetime
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lawvm.tools.reconcile import reconcile_provision


# ---------------------------------------------------------------------------
# Per-statute result
# ---------------------------------------------------------------------------


@dataclass
class StatuteSweepResult:
    statute_id: str
    amendment_count: int
    as_of: str
    sections_checked: int = 0
    diverging: list[dict[str, Any]] = field(default_factory=list)
    replay_error: str = ""  # non-empty => data_defect (replay could not run)

    @property
    def divergence_count(self) -> int:
        return len(self.diverging)

    @property
    def class_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.diverging:
            cls = d.get("divergence_class") or "unknown"
            counts[cls] = counts.get(cls, 0) + 1
        if self.replay_error:
            counts["data_defect"] = counts.get("data_defect", 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Statute selection (ranked by amendment count)
# ---------------------------------------------------------------------------


def _amendment_counts() -> dict[str, int]:
    """Return {parent_statute_id: amendment_count} from the amendment index."""
    from lawvm.finland.amendment_index import get_amendment_children

    children = get_amendment_children()
    return {parent: len(amends) for parent, amends in children.items()}


def select_statutes(
    *,
    sample: Optional[int],
    min_amendments: int = 1,
    explicit_ids: Optional[list[str]] = None,
) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    """Return ([(statute_id, amendment_count), ...], selection_log).

    When ``explicit_ids`` is given, those are used verbatim (count looked up).
    Otherwise statutes are ranked by descending amendment count and the top
    ``sample`` (or all, when sample is None) with >= min_amendments are taken.
    """
    counts = _amendment_counts()

    if explicit_ids:
        chosen = [(sid, counts.get(sid, 0)) for sid in explicit_ids]
        log = {
            "mode": "explicit",
            "requested": len(explicit_ids),
            "selected": len(chosen),
            "total_corpus_with_amendments": len(counts),
        }
        return chosen, log

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    eligible = [(sid, n) for sid, n in ranked if n >= min_amendments]
    if sample is None:
        chosen = eligible
        skipped = 0
    else:
        chosen = eligible[:sample]
        skipped = max(0, len(eligible) - sample)

    log = {
        "mode": "ranked_by_amendment_count",
        "total_corpus_with_amendments": len(counts),
        "eligible_min_amendments": min_amendments,
        "eligible_count": len(eligible),
        "selected": len(chosen),
        "skipped_lower_ranked": skipped,
        "selection_floor_amendments": chosen[-1][1] if chosen else None,
    }
    return chosen, log


# ---------------------------------------------------------------------------
# Per-statute reconcile (whole statute, section granular)
# ---------------------------------------------------------------------------


def reconcile_statute(
    statute_id: str,
    amendment_count: int,
    *,
    as_of: str,
    query_type: str = "in_force",
    max_sections: Optional[int] = None,
) -> StatuteSweepResult:
    """Reconcile every (section) address of one statute at ``as_of``.

    Performance: the underlying ``reconcile_provision`` re-replays the whole
    statute for EACH section (via resolve_provision_state -> replay_xml). For a
    540-amendment statute that is ~11s/section. We therefore wrap the
    provision-state seam's ``replay_xml`` with a per-statute memo for the
    duration of this statute's section loop, so sections 2..N reuse the first
    section's replay instead of re-replaying. The memo is keyed on the
    no-out-param call shape resolve_provision_state uses, which is safe to
    cache; it is cleared per statute to bound memory.
    """
    res = StatuteSweepResult(
        statute_id=statute_id, amendment_count=amendment_count, as_of=as_of
    )
    try:
        from lawvm.finland.grafter import replay_xml

        replayed = replay_xml(statute_id, mode="legal_pit", as_of=as_of, quiet=True)
    except Exception as exc:  # replay could not run => structural data defect
        res.replay_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        return res

    addrs = [str(a) for a in replayed.timelines]
    if max_sections is not None:
        addrs = addrs[:max_sections]

    with _memoized_provision_replay():
        _reconcile_sections(res, statute_id, addrs, as_of, query_type)
    return res


def _reconcile_sections(
    res: StatuteSweepResult,
    statute_id: str,
    addrs: list[str],
    as_of: str,
    query_type: str,
) -> None:
    for addr_str in addrs:
        try:
            rr = reconcile_provision(
                statute_id=statute_id,
                selector=addr_str,
                as_of=as_of,
                query_type=query_type,
                jurisdiction="fi",
            )
        except Exception as exc:
            res.sections_checked += 1
            res.diverging.append(
                {
                    "statute_id": statute_id,
                    "locator": addr_str,
                    "verdict": "ERROR",
                    "divergence_class": "data_defect",
                    "agree_ratio": 0.0,
                    "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
                }
            )
            continue
        res.sections_checked += 1
        if rr.verdict != "AGREE":
            res.diverging.append(
                {
                    "statute_id": statute_id,
                    "locator": rr.locator,
                    "verdict": rr.verdict,
                    "divergence_class": rr.divergence_class,
                    "agree_ratio": rr.agree_ratio,
                    "detail": "",
                }
            )


# ---------------------------------------------------------------------------
# Per-statute replay memoization
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _memoized_provision_replay():
    """Memoize the provision-state seam's replay_xml for one statute's loop.

    reconcile_provision -> resolve_provision_state always calls
    ``replay_xml(statute_id, quiet=True)`` with NO mutable out-params, so the
    result is safe to cache by statute_id. We patch the name imported inside
    resolve_provision_state's function body (it does a local import of
    ``from lawvm.finland.grafter import replay_xml``), so we patch the grafter
    attribute. The cache is local to this context and discarded on exit to bound
    memory. Only the no-out-param call shape is cached; any call passing
    *_out / stop_before / custom corpus falls through to the real function.
    """
    from lawvm.finland import grafter

    real = grafter.replay_xml
    cache: dict[str, object] = {}

    def _wrapped(parent_id, *args, **kwargs):
        cacheable = (
            not args
            and set(kwargs).issubset({"quiet"})
        )
        if cacheable:
            if parent_id not in cache:
                cache[parent_id] = real(parent_id, **kwargs)
            return cache[parent_id]
        return real(parent_id, *args, **kwargs)

    grafter.replay_xml = _wrapped
    try:
        yield
    finally:
        grafter.replay_xml = real
        cache.clear()


# ---------------------------------------------------------------------------
# Sweep driver
# ---------------------------------------------------------------------------


def run_sweep(
    *,
    sample: Optional[int],
    as_of: str,
    query_type: str = "in_force",
    min_amendments: int = 1,
    explicit_ids: Optional[list[str]] = None,
    max_sections: Optional[int] = None,
    verbose: bool = False,
) -> tuple[list[StatuteSweepResult], dict[str, Any]]:
    """Run the corpus-wide reconcile sweep. Returns (results, selection_log)."""
    chosen, selection_log = select_statutes(
        sample=sample, min_amendments=min_amendments, explicit_ids=explicit_ids
    )
    selection_log["as_of"] = as_of
    selection_log["query_type"] = query_type

    results: list[StatuteSweepResult] = []
    t0 = time.time()
    for i, (sid, n) in enumerate(chosen):
        if verbose:
            print(
                f"[reconcile-sweep] {i+1}/{len(chosen)} {sid} (amendments={n}) ...",
                file=sys.stderr,
            )
        r = reconcile_statute(
            sid, n, as_of=as_of, query_type=query_type, max_sections=max_sections
        )
        results.append(r)
        if verbose:
            err = f" ERROR={r.replay_error}" if r.replay_error else ""
            print(
                f"[reconcile-sweep]   checked={r.sections_checked} "
                f"diverging={r.divergence_count}{err}",
                file=sys.stderr,
            )
    selection_log["elapsed_sec"] = round(time.time() - t0, 1)
    return results, selection_log


# ---------------------------------------------------------------------------
# Ranking + report emission
# ---------------------------------------------------------------------------


def _severity_key(r: StatuteSweepResult) -> tuple:
    """Rank statutes worst-first.

    Order: data-defect (replay couldn't run) first, then by raw divergence
    count, then by temporal divergences (likeliest real staleness), then by
    amendment count (impact proxy).
    """
    cc = r.class_counts
    return (
        1 if r.replay_error else 0,
        r.divergence_count,
        cc.get("temporal", 0),
        cc.get("presence", 0),
        r.amendment_count,
    )


def rank_results(results: list[StatuteSweepResult]) -> list[StatuteSweepResult]:
    return sorted(results, key=_severity_key, reverse=True)


def write_reports(
    results: list[StatuteSweepResult],
    selection_log: dict[str, Any],
    *,
    label: str,
    out_dir: str = "reports",
) -> tuple[Path, Path]:
    """Write the CSV (per-diverging-section) + MD (ranked summary). Returns paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"reconcile_sweep_{label}.csv"
    md_path = out / f"reconcile_sweep_{label}.md"

    ranked = rank_results(results)

    # CSV: one row per diverging section, plus a row per replay-error statute.
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rank_statute",
                "statute_id",
                "amendment_count",
                "sections_checked",
                "locator",
                "verdict",
                "divergence_class",
                "agree_ratio",
                "detail",
            ]
        )
        for rank, r in enumerate(ranked, start=1):
            if r.replay_error:
                w.writerow(
                    [
                        rank,
                        r.statute_id,
                        r.amendment_count,
                        r.sections_checked,
                        "(whole statute)",
                        "ERROR",
                        "data_defect",
                        0.0,
                        r.replay_error,
                    ]
                )
            for d in r.diverging:
                w.writerow(
                    [
                        rank,
                        r.statute_id,
                        r.amendment_count,
                        r.sections_checked,
                        d.get("locator", ""),
                        d.get("verdict", ""),
                        d.get("divergence_class", ""),
                        d.get("agree_ratio", ""),
                        d.get("detail", ""),
                    ]
                )

    # Aggregate class totals.
    totals: dict[str, int] = {}
    total_sections = 0
    total_diverging = 0
    for r in results:
        total_sections += r.sections_checked
        total_diverging += r.divergence_count
        for cls, n in r.class_counts.items():
            totals[cls] = totals.get(cls, 0) + n
    statutes_with_divergence = sum(
        1 for r in results if r.divergence_count or r.replay_error
    )

    lines: list[str] = []
    lines.append(f"# Reconcile Sweep — {label}")
    lines.append("")
    lines.append(
        f"Generated {datetime.datetime.now(tz=datetime.timezone.utc).isoformat()}"
    )
    lines.append("")
    lines.append("## Selection")
    lines.append("")
    for k, v in selection_log.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- statutes swept: {len(results)}")
    lines.append(f"- sections checked: {total_sections}")
    lines.append(f"- diverging sections: {total_diverging}")
    lines.append(
        f"- statutes with >=1 divergence or replay error: {statutes_with_divergence}"
    )
    lines.append("")
    lines.append("### Divergence class breakdown")
    lines.append("")
    if totals:
        for cls in sorted(totals, key=lambda c: -totals[c]):
            lines.append(f"- **{cls}**: {totals[cls]}")
    else:
        lines.append("- (none — full agreement across the swept corpus)")
    lines.append("")
    lines.append("## Worst-N statutes (ranked worst-first)")
    lines.append("")
    lines.append(
        "| rank | statute | amendments | sections | diverging | classes | replay_error |"
    )
    lines.append("|---:|---|---:|---:|---:|---|---|")
    worst = [r for r in ranked if r.divergence_count or r.replay_error][:30]
    for rank, r in enumerate(worst, start=1):
        cc = ", ".join(f"{k}={v}" for k, v in sorted(r.class_counts.items()))
        err = r.replay_error[:60] if r.replay_error else ""
        lines.append(
            f"| {rank} | {r.statute_id} | {r.amendment_count} | "
            f"{r.sections_checked} | {r.divergence_count} | {cc} | {err} |"
        )
    if not worst:
        lines.append("| — | (no divergences) | | | | | |")
    lines.append("")
    lines.append("## How to read this")
    lines.append("")
    lines.append(
        "- **temporal**: oracle carries a dated change replay did not apply "
        "(or vice versa) — the 269/2026-class staleness lives here. After the "
        "Q1 amendment-index cache fix this class should be near-zero for "
        "recently-amended statutes."
    )
    lines.append(
        "- **editorial**: text differs with no temporal straddle (parse / dedup "
        "/ prior-wording drift) — candidate replay-fidelity work."
    )
    lines.append(
        "- **presence**: one side has no provision at the selector — often an "
        "oracle sub-section addressing limit, not a replay defect."
    )
    lines.append(
        "- **data_defect**: replay could not run at all for the statute — a "
        "structural gap. Cross-reference `lawvm bisect <statute>`."
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return csv_path, md_path


# ---------------------------------------------------------------------------
# CLI entry point (dispatched from `reconcile --sweep`)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    as_of = getattr(args, "as_of", "") or datetime.date.today().isoformat()
    query_type = getattr(args, "query_type", "in_force") or "in_force"
    sample_raw = getattr(args, "sample", None)
    sample: Optional[int] = int(sample_raw) if sample_raw else None
    min_amendments = int(getattr(args, "min_amendments", 1) or 1)
    max_sections_raw = getattr(args, "max_sections", None)
    max_sections: Optional[int] = int(max_sections_raw) if max_sections_raw else None
    label = getattr(args, "label", None) or f"sweep_{as_of}"
    out_dir = getattr(args, "out_dir", None) or "reports"
    verbose = bool(getattr(args, "verbose", False))
    explicit_raw = getattr(args, "statute", None) or []
    explicit_ids = list(explicit_raw) if explicit_raw else None

    if sample is None and not explicit_ids:
        # Refuse a silent full-corpus sweep (it would take hours). Require an
        # explicit --sample or --all opt-in.
        if not getattr(args, "all", False):
            print(
                "reconcile --sweep: specify --sample N (recommended) or --all "
                "(full corpus; slow). Statutes are ranked by amendment count.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    results, selection_log = run_sweep(
        sample=sample,
        as_of=as_of,
        query_type=query_type,
        min_amendments=min_amendments,
        explicit_ids=explicit_ids,
        max_sections=max_sections,
        verbose=verbose,
    )

    csv_path, md_path = write_reports(
        results, selection_log, label=label, out_dir=out_dir
    )

    total_div = sum(r.divergence_count for r in results)
    total_err = sum(1 for r in results if r.replay_error)
    print(
        f"reconcile-sweep: {len(results)} statutes, {total_div} diverging sections, "
        f"{total_err} replay errors",
        file=sys.stderr,
    )
    print(f"  CSV: {csv_path}", file=sys.stderr)
    print(f"  MD:  {md_path}", file=sys.stderr)
