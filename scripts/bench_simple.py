#!/usr/bin/env python3
"""Bench the alternative body-driven grafter against the same corpus.

Usage:
    uv run python scripts/bench_simple.py --variant body_driven --label v_body_a
    uv run python scripts/bench_simple.py --variant body_kumotaan --label v_body_b
    uv run python scripts/bench_simple.py --variant body_driven --top 10
    uv run python scripts/bench_simple.py --variant body_kumotaan --parallel 8 --label v_body_b
"""
import argparse
import csv
import io
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import Levenshtein

# Resolve paths relative to LawVM root
LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))


def _default_corpus() -> str:
    p = LAWVM_DIR / "data" / "finland" / "bench_corpus.csv"
    if p.exists():
        return str(p)
    return str(LAWVM_DIR / ".tmp" / "batch_test_list.csv")


def _load_corpus(path: str):
    with open(path) as f:
        rows = list(csv.reader(f))
    return [(int(r[0]), r[1].strip()) for r in rows if len(r) >= 2 and r[0].isdigit()]


def _clean(text: str) -> str:
    import re
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _score_one(args):
    sid, variant = args
    try:
        from lawvm.finland.grafter import get_ground_truth
        from lawvm.finland.grafter import replay_xml

        # Suppress replay output
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            master = replay_xml(sid, mode="legal_pit", quiet=True)
        finally:
            sys.stdout = old_stdout

        c_res = _clean(master.serialize_text())
        c_truth = _clean(get_ground_truth(sid))
        if not c_truth:
            return sid, -1.0, "NO_TRUTH", 0.0
        sim = Levenshtein.ratio(c_res, c_truth)
        return sid, sim, "OK", 0.0
    except Exception as e:
        return sid, -1.0, str(e)[:120], 0.0


def main():
    parser = argparse.ArgumentParser(description="Bench alternative body-driven grafter")
    parser.add_argument("--variant", default="body_driven",
                        choices=["body_driven", "body_kumotaan", "body_callcc", "body_omission", "body_diff", "body_smart", "smart_llm"],
                        help="extraction variant")
    parser.add_argument("--label", default=None, help="run label")
    parser.add_argument("--corpus", default=None, help="corpus CSV path")
    parser.add_argument("--top", type=int, default=20, help="show top N worst")
    parser.add_argument("--parallel", type=int, default=1, help="worker count")
    parser.add_argument("--limit", type=int, default=0, help="limit corpus to first N statutes")
    args = parser.parse_args()

    corpus_path = args.corpus or _default_corpus()
    corpus = _load_corpus(corpus_path)
    if args.limit:
        corpus = corpus[:args.limit]

    print(f"Variant: {args.variant}")
    print(f"Corpus: {len(corpus)} statutes from {corpus_path}")

    results = []
    total = len(corpus)

    if args.parallel > 1:
        work = [(sid, args.variant) for _, sid in corpus]
        with ProcessPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(_score_one, w): i for i, w in enumerate(work)}
            done = 0
            indexed = [None] * total
            for fut in as_completed(futures):
                idx = futures[fut]
                count = corpus[idx][0]
                sid, sim, status, elapsed = fut.result()
                indexed[idx] = (count, sid, sim, status)
                done += 1
                err = f"{(1 - sim) * 100:.2f}%" if sim >= 0 else "ERR"
                if done % 100 == 0 or done == total:
                    print(f"[{done}/{total}] latest: {sid} → err {err}")
            results = [r for r in indexed if r is not None]
    else:
        for i, (count, sid) in enumerate(corpus, 1):
            t0 = time.time()
            _, sim, status, _ = _score_one((sid, args.variant))
            elapsed = time.time() - t0
            results.append((count, sid, sim, status))
            if i % 50 == 0 or i == total:
                err = f"{(1 - sim) * 100:.2f}%" if sim >= 0 else "ERR"
                print(f"[{i}/{total}] {sid} → err {err} ({elapsed:.1f}s)")

    # Summary
    scores = [sim for _, _, sim, st in results if sim >= 0]
    perfect = sum(1 for s in scores if s >= 0.9999)
    below90 = sum(1 for s in scores if s < 0.90)
    avg = sum(scores) / len(scores) if scores else 0

    print(f"\n{'='*60}")
    print(f"Variant    : {args.variant}")
    print(f"Statutes   : {len(scores)}")
    print(f"Error rate : {(1 - avg) * 100:.2f}%")
    print(f"Perfect    : {perfect}")
    print(f"Below 90%  : {below90}")

    # Worst performers
    ranked = sorted(results, key=lambda r: r[2] if r[2] >= 0 else 2.0)
    print(f"\nWorst {args.top}:")
    for count, sid, sim, status in ranked[:args.top]:
        err = f"{(1 - sim) * 100:.2f}%" if sim >= 0 else "ERR"
        print(f"  {count:2d}amend {sid:12s} → err {err:>7s}  {status if status != 'OK' else ''}")

    # Save
    if args.label:
        ts = time.strftime("%Y%m%dT%H%M")
        out = LAWVM_DIR / "data" / "bench_runs" / f"{ts}_{args.label}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['amendments', 'statute_id', 'similarity', 'status', 'elapsed_s'])
            for count, sid, sim, status in results:
                w.writerow([count, sid, f"{sim:.6f}" if sim >= 0 else "ERR", status, "0.0"])
        print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
