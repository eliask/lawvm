"""lawvm oracle-classify — classify oracle quality for a corpus of statutes.

Reads the consolidated ZIP and classifies each statute's oracle into:
  FULL     — has content, no or few kumottu sections
  PARTIAL  — mixed: some kumottu sections (<50%) alongside live content
  REPEALED — ≥50% of sections are kumottu-only (filtered by --filter-repealed)
  EMPTY    — very few sections, no kumottu, suspiciously sparse (silently-emptied)
  ABSENT   — oracle is marked contentAbsent (filtered by --filter-live)
  MISSING  — no oracle entry found in the consolidated ZIP

Usage:
    lawvm oracle-classify --corpus .tmp/migration/expanded_batch_test_list.csv \\
                          --output .tmp/oracle_quality.csv
    lawvm oracle-classify --output .tmp/oracle_quality.csv   # full 59K corpus
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Tuple

from lawvm.corpus_store import get_corpus_store
from lawvm.tools.editorial_hygiene import count_kumottu_bytes


_CONTENT_ABSENT_BYTES = b"contentAbsent"
_GIF_BYTES = b'src="media/'

# Thresholds
_REPEALED_THRESHOLD = 0.5   # ≥50% kumottu → REPEALED
_EMPTY_MAX_SECTIONS = 3     # ≤3 sections total AND 0 kumottu → EMPTY candidate
_EMPTY_MAX_BYTES = 2000     # body text shorter than this → EMPTY


def _classify(data: bytes) -> Tuple[str, int, int, float, int, bool]:
    """Classify oracle XML bytes.

    Returns (oracle_type, section_count, kumottu_count, kumottu_fraction,
             body_text_len, has_gif).
    """
    if _CONTENT_ABSENT_BYTES in data:
        return "ABSENT", 0, 0, 0.0, 0, False

    n_sections = data.count(b"<section")
    n_kumottu = count_kumottu_bytes(data)
    has_gif = _GIF_BYTES in data

    # Rough body text length (strip XML tags)
    import re
    text = re.sub(rb"<[^>]+>", b" ", data).strip()
    body_len = len(text)

    if n_sections == 0:
        oracle_type = "EMPTY"
    else:
        frac = n_kumottu / n_sections
        if frac >= _REPEALED_THRESHOLD:
            oracle_type = "REPEALED"
        elif n_sections <= _EMPTY_MAX_SECTIONS and n_kumottu == 0 and body_len < _EMPTY_MAX_BYTES:
            oracle_type = "EMPTY"
        elif n_kumottu > 0:
            oracle_type = "PARTIAL"
        else:
            oracle_type = "FULL"

    frac = n_kumottu / n_sections if n_sections else 0.0
    return oracle_type, n_sections, n_kumottu, frac, body_len, has_gif


def _load_corpus(corpus_path: str) -> List[str]:
    sids = []
    with open(corpus_path, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2:
                try:
                    int(row[0])
                    sids.append(row[1].strip())
                except ValueError:
                    pass
    return sids


def main(args) -> None:
    output_path = getattr(args, "output", None)
    corpus_path = getattr(args, "corpus", None)

    print("Loading oracle classifications from farchive...")

    cs = get_corpus_store()

    if corpus_path:
        sids = _load_corpus(corpus_path)
        print(f"  Corpus: {len(sids)} statutes from {corpus_path}")
    else:
        sids = sorted(cs.oracle_path_index().keys())
        print(f"  Full corpus: {len(sids)} statute IDs")

    rows = []
    counts: dict = {}
    for i, sid in enumerate(sids, 1):
        data = cs.read_oracle(sid)
        if data is None:
            oracle_type, nsec, nkum, frac, blen, has_gif = "MISSING", 0, 0, 0.0, 0, False
        else:
            oracle_type, nsec, nkum, frac, blen, has_gif = _classify(data)

        counts[oracle_type] = counts.get(oracle_type, 0) + 1
        rows.append({
            "statute_id": sid,
            "oracle_type": oracle_type,
            "section_count": nsec,
            "kumottu_count": nkum,
            "kumottu_fraction": f"{frac:.3f}",
            "body_text_len": blen,
            "has_gif": "1" if has_gif else "0",
        })
        if i % 2000 == 0:
            print(f"  {i}/{len(sids)}...", flush=True)

    # Summary
    print(f"\nOracle classification summary ({len(rows)} statutes):")
    for otype in ("FULL", "PARTIAL", "REPEALED", "EMPTY", "ABSENT", "MISSING"):
        n = counts.get(otype, 0)
        pct = n / len(rows) * 100 if rows else 0
        print(f"  {otype:<10} {n:>6}  ({pct:.1f}%)")

    # Write CSV
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["statute_id", "oracle_type", "section_count", "kumottu_count",
                      "kumottu_fraction", "body_text_len", "has_gif"]
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nWritten: {out}")
    else:
        # Print sample
        print("\nSample (first 20 non-FULL):")
        non_full = [r for r in rows if r["oracle_type"] != "FULL"][:20]
        for r in non_full:
            print(f"  {r['statute_id']:12s}  {r['oracle_type']:<10}  "
                  f"sec={r['section_count']:>3}  kum={r['kumottu_count']:>3}  "
                  f"gif={r['has_gif']}")
