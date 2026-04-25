# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
PIT version coverage analysis.

Cross-references farchive oracle PIT versions against amendments.json to
determine how well the farchive covers the amendment history.

Run: cd LawVM && uv run python scripts/pit_coverage_analysis.py
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import farchive as _farchive

from lawvm.finland.consolidated_artifacts import (
    build_versioned_consolidated_main_glob,
    parse_versioned_consolidated_main_locator,
)

FARCHIVE_PATH = Path(__file__).parent.parent / "data/finlex.farchive"
AMENDMENTS_PATH = Path(__file__).parent.parent / ".tmp/corpus_graph_full/amendments.json"
OUT_DIR = Path(__file__).parent.parent / ".tmp"
OUT_JSON = OUT_DIR / "pit_coverage_analysis.json"
OUT_REPORT = OUT_DIR / "pit_coverage_report.txt"
OUT_CSV = OUT_DIR / "pit_coverage_per_statute.csv"


def canonical_amend_id(raw_id: str) -> str | None:
    """Strip -suffix variant from amendment ID: '1873/8-000' -> '1873/8'."""
    m = re.match(r"^(\d{4}/\d+)(-\d+)?$", raw_id)
    return m.group(1) if m else None


def deduplicated_amendments(raw_list: list[str]) -> list[str]:
    """Return deduplicated canonical amendment IDs, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_list:
        c = canonical_amend_id(raw)
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def decade(year_str: str) -> str:
    y = int(year_str)
    return f"{(y // 10) * 10}s"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    print("Loading amendments.json ...")
    with open(AMENDMENTS_PATH) as f:
        amend_data: dict[str, list[str]] = json.load(f)

    print("Scanning farchive for PIT oracle entries ...")
    # pit_index[statute_path] = set of canonical amendment IDs that have a PIT
    pit_index: dict[str, set[str]] = defaultdict(set)
    # all_statute_paths_in_farchive: every oracle statute path present in the farchive
    all_statute_paths_in_farchive: set[str] = set()

    archive = _farchive.Farchive(str(FARCHIVE_PATH), readonly=True)
    try:
        urls = archive.locators(build_versioned_consolidated_main_glob())
        for url in urls:
            parts = parse_versioned_consolidated_main_locator(url)
            if parts is None:
                continue
            sid = parts.sid
            all_statute_paths_in_farchive.add(sid)
            version = parts.version  # 8-digit YYYYNNNN
            if len(version) == 8 and version.isdigit():
                pit_year = version[:4]
                pit_num = str(int(version[4:]))
                pit_amend_id = f"{pit_year}/{pit_num}"
                pit_index[sid].add(pit_amend_id)
    finally:
        archive.close()

    total_in_farchive = len(all_statute_paths_in_farchive)
    print(f"Total statutes in farchive: {total_in_farchive}")
    print(f"Statutes with >=1 numeric PIT: {len(pit_index)}")
    print(f"Statutes with amendments: {len(amend_data)}")

    # --- Per-statute analysis ---
    rows: list[dict] = []

    # Track coverage categories
    cat_full = 0
    cat_partial = 0
    cat_none = 0
    cat_no_amendments = 0

    latest_is_latest = 0
    latest_is_behind = 0

    by_decade: dict[str, dict[str, int]] = defaultdict(
        lambda: {"statutes": 0, "full": 0, "partial": 0, "none": 0, "no_amendments": 0}
    )

    # Statutes with amendments
    for statute_id, raw_amends in amend_data.items():
        canonical_amends = deduplicated_amendments(raw_amends)
        n_amendments = len(canonical_amends)
        if n_amendments == 0:
            # Edge case: key present but empty list
            cat_no_amendments += 1
            year_str = statute_id.split("/")[0]
            by_decade[decade(year_str)]["statutes"] += 1
            by_decade[decade(year_str)]["no_amendments"] += 1
            continue

        pits = pit_index.get(statute_id, set())
        n_pits = len(pits)

        # Coverage ratio
        matching = set(canonical_amends) & pits
        n_matched = len(matching)
        coverage_ratio = round(n_matched / n_amendments, 4)

        # Latest amendment / latest PIT
        latest_amendment = max(
            canonical_amends,
            key=lambda x: (int(x.split("/")[0]), int(x.split("/")[1])),
        )
        if pits:
            latest_pit = max(
                pits,
                key=lambda x: (int(x.split("/")[0]), int(x.split("/")[1])),
            )
            latest_pit_raw = (
                latest_pit.split("/")[0] + latest_pit.split("/")[1].zfill(4)
            )
            latest_pit_str = f"fin@{latest_pit_raw}"
            latest_pit_is_latest = latest_pit == latest_amendment
        else:
            latest_pit = ""
            latest_pit_str = ""
            latest_pit_is_latest = False

        # Category
        if n_matched == n_amendments:
            cat = "full"
            cat_full += 1
        elif n_matched > 0:
            cat = "partial"
            cat_partial += 1
        else:
            cat = "none"
            cat_none += 1

        if pits:
            if latest_pit_is_latest:
                latest_is_latest += 1
            else:
                latest_is_behind += 1

        year_str = statute_id.split("/")[0]
        dec = decade(year_str)
        by_decade[dec]["statutes"] += 1
        by_decade[dec][cat] += 1

        rows.append(
            {
                "statute_id": statute_id,
                "n_amendments": n_amendments,
                "n_pits": n_pits,
                "n_matched": n_matched,
                "coverage_ratio": coverage_ratio,
                "category": cat,
                "latest_amendment": latest_amendment,
                "latest_pit": latest_pit_str,
                "latest_pit_is_latest": str(latest_pit_is_latest).lower(),
            }
        )

    # Statutes in farchive that have no amendments
    statutes_with_amendments = set(amend_data.keys())
    no_amend_statutes = all_statute_paths_in_farchive - statutes_with_amendments
    cat_no_amendments += len(no_amend_statutes)
    for sid in no_amend_statutes:
        year_str = sid.split("/")[0]
        dec = decade(year_str)
        by_decade[dec]["statutes"] += 1
        by_decade[dec]["no_amendments"] += 1

    # Statutes in amendments.json but not in farchive (should be 0 after our checks above,
    # but count them)
    not_in_farchive = statutes_with_amendments - all_statute_paths_in_farchive
    print(f"Statutes with amendments but not in farchive at all: {len(not_in_farchive)}")

    # Top gaps (most amendments, fewest PITs)
    rows_sorted = sorted(
        [r for r in rows if r["category"] in ("none", "partial")],
        key=lambda r: -(r["n_amendments"] - r["n_pits"]),
    )
    top_gaps = [
        {
            "statute_id": r["statute_id"],
            "amendments": r["n_amendments"],
            "pits": r["n_pits"],
            "matched": r["n_matched"],
            "gap": r["n_amendments"] - r["n_matched"],
        }
        for r in rows_sorted[:20]
    ]

    # --- Build JSON output ---
    statutes_with_pit_versions = len(pit_index)

    result = {
        "total_statutes_in_farchive": total_in_farchive,
        "statutes_with_amendments": len(amend_data),
        "statutes_with_pit_versions": statutes_with_pit_versions,
        "coverage_categories": {
            "full": {
                "count": cat_full,
                "description": "every amendment has a matching PIT",
            },
            "partial": {
                "count": cat_partial,
                "description": "some amendments have PITs, some missing",
            },
            "none": {
                "count": cat_none,
                "description": "amendments exist but zero matching PITs",
            },
            "no_amendments": {
                "count": cat_no_amendments,
                "description": "no amendments in amendments.json, not applicable",
            },
        },
        "pit_recency": {
            "latest_pit_is_latest_amendment": latest_is_latest,
            "latest_pit_is_behind": latest_is_behind,
        },
        "by_decade": {
            dec: {
                "statutes": v["statutes"],
                "full": v["full"],
                "partial": v["partial"],
                "none": v["none"],
                "no_amendments": v.get("no_amendments", 0),
            }
            for dec, v in sorted(by_decade.items())
        },
        "top_gaps": top_gaps,
    }

    print(f"\nWriting {OUT_JSON} ...")
    with open(OUT_JSON, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # --- Write CSV ---
    print(f"Writing {OUT_CSV} ...")
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "statute_id",
                "n_amendments",
                "n_pits",
                "n_matched",
                "coverage_ratio",
                "category",
                "latest_amendment",
                "latest_pit",
                "latest_pit_is_latest",
            ],
        )
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["statute_id"]))

    # --- Write human-readable report ---
    print(f"Writing {OUT_REPORT} ...")
    total_amendable = cat_full + cat_partial + cat_none
    def pct(n: int, d: int) -> str:
        return f"{100*n/d:.1f}%" if d else "N/A"

    lines = [
        "PIT VERSION COVERAGE ANALYSIS",
        "=" * 60,
        "",
        f"Total statutes in farchive:         {total_in_farchive:>8,}",
        f"Statutes with amendments:           {len(amend_data):>8,}",
        f"Statutes with any numeric PIT:      {statutes_with_pit_versions:>8,}",
        "",
        "COVERAGE CATEGORIES (of statutes with amendments)",
        "-" * 60,
        f"  full     (all amendments covered): {cat_full:>7,}  {pct(cat_full, total_amendable):>6}",
        f"  partial  (some covered):            {cat_partial:>7,}  {pct(cat_partial, total_amendable):>6}",
        f"  none     (no coverage at all):      {cat_none:>7,}  {pct(cat_none, total_amendable):>6}",
        f"  no_amend (not applicable):          {cat_no_amendments:>7,}",
        "",
        "PIT RECENCY (of statutes with any PIT)",
        "-" * 60,
        f"  Latest PIT = latest amendment:      {latest_is_latest:>7,}  {pct(latest_is_latest, latest_is_latest+latest_is_behind):>6}",
        f"  Latest PIT is behind latest amend:  {latest_is_behind:>7,}  {pct(latest_is_behind, latest_is_latest+latest_is_behind):>6}",
        "",
        "BY DECADE",
        "-" * 60,
        f"{'Decade':<10} {'Statutes':>10} {'Full':>8} {'Partial':>9} {'None':>8} {'No amend':>10}",
    ]
    for dec, v in sorted(result["by_decade"].items()):
        lines.append(
            f"{dec:<10} {v['statutes']:>10,} {v['full']:>8,} {v['partial']:>9,} {v['none']:>8,} {v['no_amendments']:>10,}"
        )

    lines += [
        "",
        "TOP 20 GAPS (most un-covered amendments)",
        "-" * 60,
        f"{'Statute':<15} {'Amends':>8} {'PITs':>6} {'Matched':>8} {'Gap':>6}",
    ]
    for g in top_gaps:
        lines.append(
            f"{g['statute_id']:<15} {g['amendments']:>8,} {g['pits']:>6,} {g['matched']:>8,} {g['gap']:>6,}"
        )

    with open(OUT_REPORT, "w") as f:
        f.write("\n".join(lines) + "\n")

    print("\nDone.")
    print("\n--- SUMMARY ---")
    for line in lines[:30]:
        print(line)


if __name__ == "__main__":
    main()
