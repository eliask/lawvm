#!/usr/bin/env python3
"""HTML oracle bench — section-label accuracy against HTML oracle.

Compares replay output section labels against HTML oracle section labels.
This gives a staleness-independent accuracy metric: if replay matches HTML
but not ZIP, the ZIP oracle is stale (not our bug). If replay matches
neither, it's a real replay error.

Usage:
    uv run python scripts/html_oracle_bench.py --sample 30
    uv run python scripts/html_oracle_bench.py --worst 50   # worst ZIP-bench performers
    uv run python scripts/html_oracle_bench.py --sids 2018/1121 2019/906
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))


# ---------------------------------------------------------------------------
# Section label extraction
# ---------------------------------------------------------------------------

_SECTION_NUM_RE = re.compile(r"(\d+)\s*([a-zäöå]?)\s*§")


def _normalize_label(raw: str) -> str:
    """Normalize section label to comparable form: '2a', '10', etc."""
    m = _SECTION_NUM_RE.search(raw)
    if m:
        return m.group(1) + m.group(2)
    # Fallback: strip non-alphanumeric
    return re.sub(r"[^0-9a-zäöå]", "", raw.lower()).strip()


def _replay_section_labels(sid: str) -> list[str] | None:
    """Run replay and extract section labels from IR tree."""
    from lawvm.finland.grafter import replay_xml

    try:
        master = replay_xml(sid)
    except Exception:
        return None

    if master.ir is None:
        return None

    labels: list[str] = []

    def _walk(node):
        if node.kind == "section" and node.label:
            labels.append(node.label)
        for c in node.children:
            _walk(c)

    _walk(master.ir)
    return labels


def _html_section_labels(sid: str) -> list[str] | None:
    """Fetch HTML oracle section labels."""
    from lawvm.finland.finlex_html import html_section_labels

    year, num = sid.split("/", 1)
    raw_labels = html_section_labels(year, num)
    if raw_labels is None:
        return None
    return [_normalize_label(lbl) for lbl in raw_labels]


def _zip_section_labels(sid: str) -> list[str] | None:
    """Extract section labels from ZIP oracle XML."""
    from lawvm.corpus_store import get_corpus_store
    from lxml import etree

    cs = get_corpus_store()
    data = cs.read_oracle(sid)
    if data is None:
        return None
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return None

    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    labels: list[str] = []
    for sec in root.findall(f".//{{{ns}}}section"):
        num_el = sec.find(f"{{{ns}}}num")
        if num_el is not None and num_el.text:
            labels.append(_normalize_label(num_el.text))
    return labels


# ---------------------------------------------------------------------------
# Comparison metrics
# ---------------------------------------------------------------------------

def _label_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard similarity of two label lists (set-based)."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def _label_precision_recall(replay_labels: list[str], oracle_labels: list[str]):
    """Precision/recall of replay labels against oracle labels."""
    rs, os_ = set(replay_labels), set(oracle_labels)
    if not rs:
        return (1.0 if not os_ else 0.0), (1.0 if not os_ else 0.0)
    precision = len(rs & os_) / len(rs) if rs else 1.0
    recall = len(rs & os_) / len(os_) if os_ else 1.0
    return precision, recall


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def _load_bench_scores() -> dict[str, float]:
    """Load latest bench run scores."""
    runs_dir = LAWVM_DIR / "data" / "bench_runs"
    csvs = sorted(runs_dir.glob("20*.csv"), reverse=True)
    if not csvs:
        return {}
    scores = {}
    with open(csvs[0], newline="") as f:
        for row in csv.DictReader(f):
            sid = row.get("statute_id", "")
            sim = row.get("similarity", "")
            if sid and sim:
                try:
                    scores[sid] = float(sim)
                except ValueError:
                    pass
    return scores


def _load_corpus() -> list[str]:
    """Load bench corpus SIDs."""
    p = LAWVM_DIR / "data" / "finland" / "bench_corpus.csv"
    if not p.exists():
        return []
    sids = []
    with open(p, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and re.match(r"\d{4}/", row[1]):
                sids.append(row[1].strip())
    return sids


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HTML oracle section-label bench")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=int, help="random sample of N statutes")
    group.add_argument("--worst", type=int, help="N worst ZIP-bench performers")
    group.add_argument("--sids", nargs="+", help="specific statute IDs")
    parser.add_argument("--output", "-o", help="save CSV to path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    bench_scores = _load_bench_scores()
    corpus = _load_corpus()

    if args.sids:
        sids = args.sids
    elif args.worst:
        ranked = sorted(corpus, key=lambda s: bench_scores.get(s, 1.0))
        sids = ranked[: args.worst]
    elif args.sample:
        import random
        sids = random.sample(corpus, min(args.sample, len(corpus)))
    else:
        sids = _load_corpus()[:20]

    print(f"HTML Oracle Bench: {len(sids)} statutes", file=sys.stderr)
    print(f"{'SID':<14} {'ZIP-bench':>9} {'HTML-J':>7} {'R-sec':>6} {'Z-sec':>6} {'H-sec':>6}  Classification")
    print("-" * 85)

    results = []
    for i, sid in enumerate(sids, 1):
        zip_score = bench_scores.get(sid, -1.0)

        # Replay labels (most expensive — runs replay)
        replay_labels = _replay_section_labels(sid)

        # HTML labels (cached after first fetch, rate limited)
        time.sleep(0.2)  # light rate limit (HTML cached after first fetch)
        html_labels = _html_section_labels(sid)

        # ZIP labels
        zip_labels = _zip_section_labels(sid)

        if replay_labels is None or html_labels is None:
            classification = "SKIP"
            html_jaccard = -1.0
            r_n = z_n = h_n = -1
        else:
            r_n = len(replay_labels)
            h_n = len(html_labels)
            z_n = len(zip_labels) if zip_labels else -1

            html_jaccard = _label_overlap(replay_labels, html_labels)
            zip_jaccard = _label_overlap(replay_labels, zip_labels) if zip_labels else -1.0

            if html_jaccard > zip_jaccard + 0.05 and zip_jaccard >= 0:
                classification = "STALE_ORACLE"
            elif html_jaccard >= 0.95:
                classification = "REPLAY_CORRECT"
            elif zip_jaccard >= 0 and zip_jaccard > html_jaccard + 0.05:
                classification = "HTML_SUSPECT"
            elif html_jaccard < 0.7:
                classification = "REPLAY_ERROR"
            else:
                classification = "PARTIAL"

        row = {
            "statute_id": sid,
            "zip_bench": zip_score,
            "html_jaccard": html_jaccard,
            "replay_sections": r_n,
            "zip_sections": z_n,
            "html_sections": h_n,
            "classification": classification,
        }
        results.append(row)

        hj = f"{html_jaccard:.1%}" if html_jaccard >= 0 else "n/a"
        zs = f"{zip_score:.1%}" if zip_score >= 0 else "n/a"
        print(
            f"{sid:<14} {zs:>9} {hj:>7} {r_n:>6} {z_n:>6} {h_n:>6}  {classification}"
        )

        if args.verbose and i % 10 == 0:
            print(f"  [{i}/{len(sids)}]", file=sys.stderr)

    # Summary
    valid = [r for r in results if r["classification"] != "SKIP"]
    if valid:
        print(f"\n--- Summary ({len(valid)} statutes) ---")
        from collections import Counter
        counts = Counter(r["classification"] for r in valid)
        for cls, n in counts.most_common():
            print(f"  {cls:<20} {n:>4} ({100*n/len(valid):.0f}%)")

        hj_scores = [r["html_jaccard"] for r in valid if r["html_jaccard"] >= 0]
        zb_scores = [r["zip_bench"] for r in valid if r["zip_bench"] >= 0]
        if hj_scores:
            print(f"\n  HTML-label accuracy:  mean {sum(hj_scores)/len(hj_scores):.1%}")
        if zb_scores:
            print(f"  ZIP-bench accuracy:   mean {sum(zb_scores)/len(zb_scores):.1%}")
        if hj_scores and zb_scores:
            delta = sum(hj_scores)/len(hj_scores) - sum(zb_scores)/len(zb_scores)
            print(f"  Delta (HTML - ZIP):   {delta:+.1%}")
            stale = [r for r in valid if r["classification"] == "STALE_ORACLE"]
            if stale:
                print(f"\n  Stale-oracle statutes: {len(stale)}")
                for r in sorted(stale, key=lambda r: r["zip_bench"])[:10]:
                    print(
                        f"    {r['statute_id']:<14} ZIP-bench={r['zip_bench']:.1%}  "
                        f"HTML-label={r['html_jaccard']:.1%}  "
                        f"R={r['replay_sections']} Z={r['zip_sections']} H={r['html_sections']}"
                    )

    if args.output:
        out = Path(args.output)
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
