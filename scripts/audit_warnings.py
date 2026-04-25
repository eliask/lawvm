#!/usr/bin/env python3
"""Corpus-wide replay warning audit.

Compiles every statute in the corpus while capturing all Python warnings
emitted during replay, then reports the most common warning patterns.

Usage:
    uv run python scripts/audit_warnings.py
    uv run python scripts/audit_warnings.py --limit 20
    uv run python scripts/audit_warnings.py --workers 8
    uv run python scripts/audit_warnings.py --corpus .tmp/my_corpus.txt
    uv run python scripts/audit_warnings.py --limit 50 --workers 4
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

DEFAULT_CORPUS = LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt"
DEFAULT_OUTPUT = LAWVM_DIR / ".tmp" / "warning_audit.csv"


# ---------------------------------------------------------------------------
# Warning normalization
# ---------------------------------------------------------------------------

_NORMALIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Strip statute/amendment identifiers like 1959/324, 2009/953
    (re.compile(r"\b\d{4}/\d+\b"), "<SID>"),
    # Strip numeric section references: "§ 5", "5 §", "§§ 3-7"
    (re.compile(r"§+\s*\d[\d\-]*|\d[\d\-]*\s*§+"), "<SEC>"),
    # Strip bare integers that look like section/offset numbers (4+ digits: years are excluded above)
    (re.compile(r"\b\d{5,}\b"), "<NUM>"),
    # Strip Python object repr strings  <lawvm.finland.ops.ResolvedOp object at 0x...>
    (re.compile(r"<[^>]{0,120} object at 0x[0-9a-fA-F]+>"), "<OBJ>"),
    # Strip hex addresses
    (re.compile(r"0x[0-9a-fA-F]{4,}"), "<ADDR>"),
    # Collapse whitespace
    (re.compile(r"\s+"), " "),
]


def _normalize_message(msg: str) -> str:
    for pattern, replacement in _NORMALIZE_PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg.strip()


# ---------------------------------------------------------------------------
# Worker (runs in a subprocess — warnings are process-local)
# ---------------------------------------------------------------------------


def _compile_with_warnings(sid: str) -> tuple[str, list[dict[str, object]]]:
    """Compile one statute, capturing all warnings.  Runs in worker process."""
    import warnings

    from lawvm.finland.compile import compile_fi_facade  # noqa: PLC0415

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            compile_fi_facade(sid)
        except Exception as exc:
            return sid, [
                {
                    "category": "ERROR",
                    "message": str(exc),
                    "filename": "",
                    "lineno": 0,
                }
            ]

    results: list[dict[str, object]] = []
    for w in caught:
        results.append(
            {
                "category": w.category.__name__,
                "message": str(w.message),
                "filename": str(w.filename),
                "lineno": int(w.lineno),
            }
        )
    return sid, results


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------


def _normalize_id(raw_id: str) -> str:
    """Strip -NNN amendment suffix so '1896/37-000' becomes '1896/37'."""
    if "-" in raw_id:
        parts = raw_id.rsplit("-", 1)
        if parts[1].isdigit():
            return parts[0]
    return raw_id


def _load_corpus(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _deduplicate_ids(raw_ids: list[str]) -> list[str]:
    """Deduplicate by normalized parent id, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for rid in raw_ids:
        nid = _normalize_id(rid)
        if nid not in seen:
            seen.add(nid)
            result.append(nid)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Corpus-wide replay warning audit")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Path to corpus file (one statute ID per line)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker processes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit corpus to first N statutes (0 = no limit)",
    )
    args = parser.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    raw_ids = _load_corpus(corpus_path)
    sids = _deduplicate_ids(raw_ids)
    if args.limit:
        sids = sids[: args.limit]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(sids)
    print(f"Corpus   : {len(raw_ids)} raw entries -> {total} unique parent IDs")
    print(f"Workers  : {args.workers}")
    print(f"Output   : {output_path}")
    print()

    all_rows: list[dict[str, object]] = []
    done = 0
    error_count = 0
    warning_count = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_compile_with_warnings, sid): sid for sid in sids}
        for fut in as_completed(futures):
            done += 1
            sid, w_list = fut.result()
            for w in w_list:
                if w["category"] == "ERROR":
                    error_count += 1
                else:
                    warning_count += 1
                all_rows.append({"statute_id": sid, **w})

            if done % 100 == 0 or done == total:
                print(f"  [{done:5d}/{total}] warnings so far: {warning_count}  errors: {error_count}")

    # Write full CSV
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["statute_id", "category", "message", "filename", "lineno"],
        )
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print(f"\nWrote {len(all_rows)} rows to {output_path}")

    # Summary: top patterns
    pattern_counter: Counter[tuple[str, str, str]] = Counter()
    for row in all_rows:
        if row["category"] == "ERROR":
            continue
        key = (
            str(row["category"]),
            _normalize_message(str(row["message"])),
            f"{row['filename']}:{row['lineno']}",
        )
        pattern_counter[key] += 1

    print(f"\n{'='*72}")
    print("TOP WARNING PATTERNS (by frequency)")
    print(f"{'='*72}")
    print(f"{'Count':>6}  {'Category':<30}  Source")
    print(f"{'':->6}  {'':->30}  {'':->30}")

    top = pattern_counter.most_common(30)
    for (category, norm_msg, source), count in top:
        short_msg = norm_msg[:80] + ("…" if len(norm_msg) > 80 else "")
        print(f"{count:6d}  {category:<30}  {source}")
        print(f"         {short_msg}")
        print()

    if not top:
        print("  No warnings collected.")

    print(f"\nTotal warnings : {warning_count}")
    print(f"Total errors   : {error_count}")
    print(f"Statutes run   : {total}")


if __name__ == "__main__":
    main()
