"""lawvm ee-consolidation-candidates — EE consolidation-error candidate surface.

Loads a saved EE bench run, replays each pair, and ranks "LawVM-replay-is-RIGHT /
official in-force consolidation (terviktekst) is WRONG" candidates: STRONG tier
(adjudicated consolidation-side error) first, then TRIAGE tier (unadjudicated,
surfaced for review only). This is EE's adoption wedge — actionable feedback to
Riigi Teataja — so it gets a first-class CLI export.

Reuses the existing ranking logic in
`estonia/consolidation_error_candidates.py`; this tool only loads the run,
resolves each pair's consolidated-version effective date, drives the aggregation
entry point, and renders the result.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lawvm.estonia.consolidation_error_candidates import (
    ConsolidationCandidatePairInput,
    ConsolidationCandidateRunReport,
    build_consolidation_candidate_run_report,
    candidate_to_jsonable,
    run_report_to_jsonable,
)
from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
from lawvm.tools.ee_bench import _BENCH_DIR

if TYPE_CHECKING:
    import argparse


_DEFAULT_AS_OF = "2026-03-24"


@dataclass(frozen=True)
class _BenchPairRow:
    """One bench-run row reduced to the fields needed to mine candidates."""

    base_id: str
    oracle_id: str
    title: str
    status: str
    n_divs: int


def _resolve_run_path(label_or_path: str | None) -> Path:
    """Resolve a bench-run label or path to a CSV, defaulting to the latest run.

    Mirrors `ee_frontier._resolve_run_path` so the two EE tools accept the same
    --label semantics (exact path, substring label, or latest saved run).
    """
    if label_or_path:
        candidate = Path(label_or_path)
        if candidate.exists():
            return candidate
        matches = sorted(_BENCH_DIR.glob(f"*{label_or_path}*.csv"))
        if matches:
            return matches[-1]
        direct = _BENCH_DIR / f"{label_or_path}.csv"
        if direct.exists():
            return direct
        raise FileNotFoundError(f"EE bench run not found for label/path: {label_or_path}")

    matches = sorted(_BENCH_DIR.glob("*.csv"))
    if not matches:
        raise FileNotFoundError(f"No EE bench runs found in {_BENCH_DIR}")
    return matches[-1]


def _to_int(raw: str | None, default: int = 0) -> int:
    try:
        return int(raw or default)
    except ValueError:
        return default


def _load_pair_rows(path: Path) -> list[_BenchPairRow]:
    rows: list[_BenchPairRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            base_id = (row.get("base_id") or "").strip()
            oracle_id = (row.get("oracle_id") or "").strip()
            if not base_id or not oracle_id:
                continue
            rows.append(
                _BenchPairRow(
                    base_id=base_id,
                    oracle_id=oracle_id,
                    title=(row.get("title") or "").strip(),
                    status=(row.get("status") or "").strip(),
                    n_divs=_to_int(row.get("n_divs")),
                )
            )
    return rows


def _resolve_as_of(oracle_id: str, archive: Any) -> str:
    """Resolve a pair's consolidated-version effective date from its oracle XML.

    Falls back to a fixed default only when the oracle XML carries no effective
    date — the same convention used by ee-frontier / ee-pair-status.
    """
    oracle_xml = fetch_rt_xml(oracle_id, archive)
    return extract_effective_date(oracle_xml) or _DEFAULT_AS_OF


def build_consolidation_candidates_payload(
    label_or_path: str | None = None,
    *,
    tier: str = "strong",
    top: int = 20,
) -> dict[str, Any]:
    """Build the ranked consolidation-error candidate payload for one bench run.

    Only rows that actually carry replay-vs-consolidation divergences are mined
    (n_divs > 0 and status OK), so the surface is the candidate-bearing subset
    of the run rather than the whole corpus.
    """
    path = _resolve_run_path(label_or_path)
    rows = _load_pair_rows(path)
    candidate_rows = [
        row for row in rows if row.status == "OK" and row.n_divs > 0
    ]

    archive = open_rt_archive(readonly=True)
    try:
        pairs: list[ConsolidationCandidatePairInput] = []
        as_of_errors: list[dict[str, str]] = []
        for row in candidate_rows:
            try:
                as_of = _resolve_as_of(row.oracle_id, archive)
            except Exception as exc:  # noqa: BLE001 — typed error row, not swallowed
                as_of_errors.append(
                    {
                        "base_id": row.base_id,
                        "oracle_id": row.oracle_id,
                        "title": row.title,
                        "error": f"as_of resolution failed: {type(exc).__name__}: {exc}",
                    }
                )
                continue
            pairs.append(
                ConsolidationCandidatePairInput(
                    base_id=row.base_id,
                    oracle_id=row.oracle_id,
                    title=row.title,
                    as_of=as_of,
                )
            )

        run_report: ConsolidationCandidateRunReport = build_consolidation_candidate_run_report(
            tuple(pairs),
            run_label=path.name,
            archive=archive,
        )
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    payload = run_report_to_jsonable(run_report)
    payload["run_path"] = str(path)
    payload["candidate_row_count"] = len(candidate_rows)
    if as_of_errors:
        payload["errors"] = list(payload.get("errors", ())) + as_of_errors

    ranked = run_report.ranked_candidates()
    if tier == "strong":
        selected = [c for c in ranked if c.tier == "strong"]
    elif tier == "triage":
        selected = [c for c in ranked if c.tier == "triage"]
    else:
        selected = list(ranked)
    if top > 0:
        selected = selected[:top]

    payload["tier_filter"] = tier
    payload["selected"] = [candidate_to_jsonable(c) for c in selected]
    payload["selected_count"] = len(selected)
    return payload


def _print_candidate(candidate: dict[str, Any]) -> None:
    evidence = candidate.get("evidence", {}) or {}
    act = candidate.get("amending_act") or "(unknown act)"
    act_title = candidate.get("amending_act_title") or ""
    act_label = f"{act} {act_title}".strip()
    print(
        f"  [{candidate['tier']}] {candidate['base_id']} -> {candidate['oracle_id']}  "
        f"@ {candidate['address']}"
    )
    print(
        f"      bucket={candidate['residual_bucket']} "
        f"type={candidate['divergence_type'] or '(none)'} "
        f"via={act_label or '(unattributed)'}"
    )
    replay_snippet = evidence.get("replay_snippet") or "(empty)"
    con_snippet = evidence.get("consolidated_snippet") or "(empty)"
    print(f"      LawVM replay : {replay_snippet}")
    print(f"      consolidation: {con_snippet}")
    why = candidate.get("residual_evidence")
    if why:
        print(f"      why wrong    : {why}")


def main(args: "argparse.Namespace") -> None:
    payload = build_consolidation_candidates_payload(
        getattr(args, "label", None),
        tier=getattr(args, "tier", "strong") or "strong",
        top=int(getattr(args, "top", 20) or 20),
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== EE Consolidation-Error Candidates ===")
    print(f"  run            : {payload['run_path']}")
    print(f"  candidate rows : {payload['candidate_row_count']}")
    print(f"  scored pairs   : {payload['scored_pair_count']} / {payload['pair_count']}")
    print(f"  strong total   : {payload['strong_total']}")
    print(f"  triage total   : {payload['triage_total']}")
    print(f"  tier filter    : {payload['tier_filter']}")

    selected = payload.get("selected", []) or []
    if selected:
        print(f"\nRanked candidates (top {payload['selected_count']}):")
        for candidate in selected:
            _print_candidate(candidate)
    else:
        print("\nRanked candidates:")
        print("  (none for this tier)")

    errors = payload.get("errors", []) or []
    if errors:
        print(f"\nPairs that could not be scored ({len(errors)}):")
        for err in errors:
            print(f"  {err['base_id']} -> {err['oracle_id']}: {err['error']}")


__all__ = [
    "build_consolidation_candidates_payload",
    "main",
]
