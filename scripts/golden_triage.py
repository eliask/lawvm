#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Phase 0 triage scoring for the Golden Verification Process.

Reads divergences.db, scores and filters Finlex-direction divergences
(REPLAY_EXTRA, EXTRA), then writes ranked candidate files.

Usage (from LawVM/ dir):
    uv run python scripts/golden_triage.py
    uv run python scripts/golden_triage.py --db .tmp/divergences.db
    uv run python scripts/golden_triage.py --top 20
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent  # scripts/ → LawVM/

_DEFAULT_DB = _LAWVM_DIR / ".tmp" / "divergences.db"
_GRAPH_DIR = _LAWVM_DIR / ".tmp" / "corpus_graph_full"
_OUT_CANDIDATES = _LAWVM_DIR / ".tmp" / "golden_candidates.jsonl"
_OUT_STATUTES = _LAWVM_DIR / ".tmp" / "golden_statutes_ranked.jsonl"
_OUT_SUMMARY = _LAWVM_DIR / ".tmp" / "golden_candidates_summary.txt"

# ---------------------------------------------------------------------------
# Hardcoded core statutes (high-impact, heavily referenced)
# ---------------------------------------------------------------------------

CORE_STATUTES = {
    "1889/39",   # rikoslaki
    "1734/4",    # kauppakaari
    "1999/731",  # suomen perustuslaki
    "1995/1621", # kuntalaki (old)
    "2015/410",  # kuntalaki (current)
    "2003/434",  # hallintolaki
    "1999/523",  # julkisuuslaki
    "1992/1336", # tuloverolaki
    "1993/1479", # arvonlisäverolaki
    "2001/55",   # työsopimuslaki
    "2006/395",  # osakeyhtiölaki
    "1734/1",    # oikeudenkäymiskaari (maakaari alias)
    "1734/3",    # oikeudenkäymiskaari
    "2010/675",  # terveydenhuoltolaki
    "2007/417",  # lastensuojelulaki
    "2016/785",  # sosiaalihuoltolaki
    "2018/531",  # laki sosiaali- ja terveydenhuollon asiakasmaksuista
    "2019/561",  # laki ikääntyneen väestön toimintakyvyn tukemisesta
}

# Diagnoses that indicate Finlex is behind (potential Finlex errors)
_FINLEX_BEHIND_DIAGNOSES = frozenset({"REPLAY_EXTRA", "EXTRA"})

# ---------------------------------------------------------------------------
# Graph data loaders
# ---------------------------------------------------------------------------

def _load_citation_counts(graph_dir: Path) -> dict[str, int]:
    """Return dict: statute_id → count of incoming CITES edges.

    Handles both base statute IDs (e.g. '2006/395') and versioned IDs
    (e.g. '2006/395-000').  We normalise to base ID (strip suffix).
    """
    citations_path = graph_dir / "citations.jsonl"
    if not citations_path.exists():
        return {}

    counts: dict[str, int] = defaultdict(int)
    with open(citations_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("edge_type") != "CITES":
                continue
            target = obj.get("target_statute_id", "")
            # Normalise: strip version suffix like "-000"
            base = re.sub(r"-\d+$", "", target)
            counts[base] += 1
    return dict(counts)


def _load_amendment_counts_per_statute(graph_dir: Path) -> dict[str, int]:
    """Return dict: base_statute_id → total number of amendments.

    amendments.json maps versioned_statute_id → [list of amendment_ids].
    We aggregate across all versions of the same base statute.
    """
    amend_path = graph_dir / "amendments.json"
    if not amend_path.exists():
        return {}

    with open(amend_path, encoding="utf-8") as f:
        raw: dict[str, list[str]] = json.load(f)

    counts: dict[str, int] = defaultdict(int)
    for versioned_id, amend_list in raw.items():
        base = re.sub(r"-\d+$", "", versioned_id)
        counts[base] += len(amend_list)
    return dict(counts)


# ---------------------------------------------------------------------------
# Chain depth proxy: count how many distinct amendments blame a given section
# ---------------------------------------------------------------------------

def _compute_section_amendment_counts(rows: list[sqlite3.Row]) -> dict[tuple[str, str], int]:
    """Count distinct blame_sources per (statute_id, section) across ALL rows.

    This gives a proxy for chain depth: how many amendments have touched
    this specific section (as recorded in divergences.db).
    Each row represents one divergence with a specific blame_source;
    if multiple rows for the same section have different blame sources,
    the depth is higher.
    """
    from collections import defaultdict
    tracker: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        key = (row["statute_id"], row["section"])
        if row["blame_source"]:
            tracker[key].add(row["blame_source"])
    # depth = number of distinct blame amendments recorded for this section
    return {k: len(v) for k, v in tracker.items()}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

_VAILIAIKAIS_RE = re.compile(r'v[äa]liaikais', re.I)


def _is_vailiaikainen(blame_title: str) -> bool:
    return bool(_VAILIAIKAIS_RE.search(blame_title or ""))


def _statute_year(statute_id: str) -> int:
    """Extract the year from a statute_id like '2006/395'. Returns 0 if unparseable."""
    try:
        return int(statute_id.split("/")[0])
    except (ValueError, IndexError):
        return 0


def _blame_year(blame_source: str) -> int:
    """Extract year from blame amendment id like '2012/567'. Returns 0 if missing."""
    if not blame_source:
        return 0
    try:
        return int(blame_source.split("/")[0])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(
    row: sqlite3.Row,
    chain_depth: int,
    citation_counts: dict[str, int],
    high_cites_threshold: int = 100,
) -> tuple[int, dict]:
    """Compute priority score per spec. Returns (score, score_breakdown_dict)."""
    statute_id = row["statute_id"]
    section_score = row["section_score"] or 0.0
    blame = row["blame_source"] or ""
    by = _blame_year(blame)

    base = 100

    # simplicity: chain_depth proxy
    if chain_depth == 1:
        simplicity = 50
    elif chain_depth == 2:
        simplicity = 30
    else:
        simplicity = 0

    # recency
    if by >= 2015:
        recency = 20
    elif by >= 2000:
        recency = 10
    else:
        recency = 0

    # impact
    in_core = statute_id in CORE_STATUTES
    high_citations = citation_counts.get(statute_id, 0) >= high_cites_threshold
    if in_core:
        impact = 30
    elif high_citations:
        impact = 15
    else:
        impact = 0

    # ambiguity penalty
    if not blame:
        ambiguity = -100
    elif 0.5 <= section_score <= 0.85:
        ambiguity = -50
    else:
        ambiguity = 0

    total = base + simplicity + recency + impact + ambiguity  # ambiguity is negative

    breakdown = {
        "base": base,
        "simplicity": simplicity,
        "recency": recency,
        "impact": impact,
        "ambiguity": ambiguity,
    }
    return total, breakdown


# ---------------------------------------------------------------------------
# Main triage
# ---------------------------------------------------------------------------

def run_triage(
    db_path: Path,
    graph_dir: Path,
    out_candidates: Path,
    out_statutes: Path,
    out_summary: Path,
    top_n: int = 20,
) -> None:
    if not db_path.exists():
        print(f"ERROR: divergences.db not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Load ALL divergence rows for chain-depth proxy calculation
    all_rows = list(con.execute(
        "SELECT statute_id, section, diagnosis, section_score, blame_source, blame_title, title "
        "FROM divergences"
    ).fetchall())

    # Filter to Finlex-behind diagnoses only
    finlex_rows = [r for r in all_rows if r["diagnosis"] in _FINLEX_BEHIND_DIAGNOSES]
    con.close()

    total_examined = len(finlex_rows)

    # Build chain depth proxy from all finlex rows
    section_amendment_counts = _compute_section_amendment_counts(finlex_rows)

    # Load graph data
    citation_counts: dict[str, int] = {}
    if graph_dir.exists():
        citation_counts = _load_citation_counts(graph_dir)

    # Apply filters and collect reasons
    filter_counts = {
        "pre_1990": 0,
        "vailiaikainen": 0,
        "high_chain_depth": 0,
        "low_divergence": 0,
    }

    candidates = []
    for row in finlex_rows:
        statute_id = row["statute_id"]
        section = row["section"]
        section_score = row["section_score"] or 0.0
        blame = row["blame_source"] or ""
        blame_title = row["blame_title"] or ""

        filters = {
            "pre_1990": False,
            "vailiaikainen": False,
            "high_chain_depth": False,
            "low_divergence": False,
        }

        # Hard filters
        if _statute_year(statute_id) < 1990:
            filters["pre_1990"] = True
            filter_counts["pre_1990"] += 1

        if _is_vailiaikainen(blame_title):
            filters["vailiaikainen"] = True
            filter_counts["vailiaikainen"] += 1

        # Chain depth per section
        depth = section_amendment_counts.get((statute_id, section), 1)
        if depth > 5:
            filters["high_chain_depth"] = True
            filter_counts["high_chain_depth"] += 1

        # Low divergence — skip near-identical (probably formatting)
        if section_score > 0.85:
            filters["low_divergence"] = True
            filter_counts["low_divergence"] += 1

        any_filtered = any(filters.values())

        score, breakdown = _score(row, depth, citation_counts)

        fault_type_map = {
            "REPLAY_EXTRA": "AMENDMENT_NOT_APPLIED",
            "EXTRA": "SECTION_ABSENT_IN_ORACLE",
        }
        fault_type = fault_type_map.get(row["diagnosis"], row["diagnosis"])

        by = _blame_year(blame)

        candidate = {
            "statute_id": statute_id,
            "section": section,
            "score": score,
            "chain_depth": depth,
            "blame_amendment": blame if blame else None,
            "blame_year": by if by else None,
            "fault_type": fault_type,
            "diagnosis": row["diagnosis"],
            "section_score": round(section_score, 6),
            "severity": 3,  # all REPLAY_EXTRA/EXTRA are sev=3 per faults.py
            "statute_title": row["title"] or "",
            "filters": filters,
            "filtered_out": any_filtered,
            "score_breakdown": breakdown,
        }
        candidates.append(candidate)

    # Separate filtered vs passing
    passing = [c for c in candidates if not c["filtered_out"]]
    filtered = [c for c in candidates if c["filtered_out"]]

    # Sort passing by score desc
    passing.sort(key=lambda x: x["score"], reverse=True)

    # Write candidates JSONL (passing only, ranked)
    out_candidates.parent.mkdir(parents=True, exist_ok=True)
    with open(out_candidates, "w", encoding="utf-8") as f:
        for c in passing:
            f.write(json.dumps(c, ensure_ascii=False))
            f.write("\n")

    # Group by statute → golden_statutes_ranked
    statute_groups: dict[str, list[dict]] = defaultdict(list)
    for c in passing:
        statute_groups[c["statute_id"]].append(c)

    statute_records = []
    for sid, secs in statute_groups.items():
        secs_sorted = sorted(secs, key=lambda x: x["score"], reverse=True)
        statute_records.append({
            "statute_id": sid,
            "statute_title": secs_sorted[0].get("statute_title", ""),
            "n_sections": len(secs),
            "max_score": secs_sorted[0]["score"],
            "total_score": sum(s["score"] for s in secs),
            "avg_score": round(sum(s["score"] for s in secs) / len(secs), 1),
            "sections": [s["section"] for s in secs_sorted],
            "top_blame": secs_sorted[0].get("blame_amendment"),
        })

    statute_records.sort(key=lambda x: (-x["total_score"], -x["max_score"]))

    with open(out_statutes, "w", encoding="utf-8") as f:
        for sr in statute_records:
            f.write(json.dumps(sr, ensure_ascii=False))
            f.write("\n")

    # Score distribution
    above_150 = [c for c in passing if c["score"] > 150]
    between_100_150 = [c for c in passing if 100 <= c["score"] <= 150]
    below_100 = [c for c in passing if c["score"] < 100]

    unique_statutes = len(statute_groups)

    # Build summary text
    lines = []
    lines.append("Golden Triage Summary")
    lines.append("=" * 50)
    lines.append(f"  Total Finlex-direction divergences examined: {total_examined:,}")
    lines.append(f"  Filtered out: {total_examined - len(passing):,}")
    lines.append(f"    pre-1990:         {filter_counts['pre_1990']:>6,}")
    lines.append(f"    vailiaikainen:    {filter_counts['vailiaikainen']:>6,}")
    lines.append(f"    high chain depth: {filter_counts['high_chain_depth']:>6,}")
    lines.append(f"    low divergence:   {filter_counts['low_divergence']:>6,}  (score > 0.85)")
    lines.append("    (note: a candidate can hit multiple filters)")
    lines.append(f"  Candidates remaining: {len(passing):,}")
    lines.append(f"  Unique statutes:      {unique_statutes:,}")
    lines.append("")
    lines.append("  Score distribution:")
    lines.append(f"    >150:      {len(above_150):>5,} candidates  (easy wins)")
    lines.append(f"    100-150:   {len(between_100_150):>5,} candidates")
    lines.append(f"    <100:      {len(below_100):>5,} candidates")
    lines.append("")
    lines.append(f"  Top {top_n} statutes by total_score:")
    for sr in statute_records[:top_n]:
        title_snippet = sr["statute_title"][:50] if sr["statute_title"] else "(no title)"
        lines.append(
            f"    {sr['statute_id']:<14}  {sr['n_sections']:>3} sections  "
            f"max={sr['max_score']:>4}  total={sr['total_score']:>6}  "
            f"{title_snippet}"
        )
    lines.append("")
    lines.append("Output files:")
    lines.append(f"  Candidates: {out_candidates}")
    lines.append(f"  Statutes:   {out_statutes}")

    summary_text = "\n".join(lines)
    print(summary_text)

    with open(out_summary, "w", encoding="utf-8") as f:
        f.write(summary_text)
        f.write("\n")

    print(f"\nWrote {len(passing):,} candidates to {out_candidates}")
    print(f"Wrote {unique_statutes:,} statute records to {out_statutes}")
    print(f"Wrote summary to {out_summary}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 0 triage: score and rank Finlex-direction divergences for golden verification.",
    )
    p.add_argument(
        "--db",
        default=str(_DEFAULT_DB),
        metavar="PATH",
        help=f"path to divergences.db (default: {_DEFAULT_DB})",
    )
    p.add_argument(
        "--graph",
        default=str(_GRAPH_DIR),
        metavar="DIR",
        help=f"path to corpus_graph_full/ dir for citation counts (default: {_GRAPH_DIR})",
    )
    p.add_argument(
        "--candidates-out",
        default=str(_OUT_CANDIDATES),
        metavar="PATH",
        help=f"output path for golden_candidates.jsonl (default: {_OUT_CANDIDATES})",
    )
    p.add_argument(
        "--statutes-out",
        default=str(_OUT_STATUTES),
        metavar="PATH",
        help=f"output path for golden_statutes_ranked.jsonl (default: {_OUT_STATUTES})",
    )
    p.add_argument(
        "--summary-out",
        default=str(_OUT_SUMMARY),
        metavar="PATH",
        help=f"output path for summary text (default: {_OUT_SUMMARY})",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="number of top statutes to show in summary (default: 20)",
    )
    p.add_argument(
        "--high-cites",
        type=int,
        default=100,
        metavar="N",
        dest="high_cites",
        help="threshold for 'high citation count' impact bonus (default: 100)",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    run_triage(
        db_path=Path(args.db),
        graph_dir=Path(args.graph),
        out_candidates=Path(args.candidates_out),
        out_statutes=Path(args.statutes_out),
        out_summary=Path(args.summary_out),
        top_n=args.top,
    )


if __name__ == "__main__":
    main()
