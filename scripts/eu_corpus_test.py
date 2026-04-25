import json
from pathlib import Path
from lawvm.eu.pipeline import EUReplayPipeline

# 10 Representative EU Acts
CORPUS = [
    "32016R0679", # GDPR
    "32022R1925", # DMA
    "32022R2065", # DSA
    "32024R1689", # AI Act
    "32018R1725", # DP for EU institutions
    "32019R1150", # P2B
    "32021R0241", # RRF
    "32023R1114", # MiCA
    "32023R2411", # Craft GI
    "32022R0868", # Data Governance Act
]

def run_corpus_test() -> None:
    pipeline = EUReplayPipeline(cache_dir=Path(".cache/eu_corpus"))
    results = []

    print(f"--- Running EU Corpus Test on {len(CORPUS)} acts ---")

    for celex in CORPUS:
        print(f"\n[Processing {celex}]")
        res: dict[str, object] = {"celex": celex, "baseline": "FAIL", "discovery": "FAIL", "amendments_count": 0}

        try:
            # 1. Baseline Parsing
            # Note: replay_statute fetches baseline if missing
            # We'll just try to fetch and parse baseline here
            baseline_text = pipeline.fetch_amendment_text(celex)
            if baseline_text:
                res["baseline_fetch"] = "OK"
                # Save to cache
                path = pipeline.cache_dir / f"{celex.replace('/', '_')}_baseline.xhtml"
                path.write_text(baseline_text)

                from lawvm.eu.grafter import parse_eu_regulation_ir
                baseline = parse_eu_regulation_ir(path, celex=celex)
                res["baseline"] = "OK"
                res["article_count"] = len([n for n in baseline.body.children if n.kind == 'section'])

            # 2. Discovery
            affecting = pipeline.discover_affecting_acts(celex)
            res["discovery"] = "OK"
            res["amendments_count"] = len(affecting)
            res["amendments"] = affecting[:5]

        except Exception as e:
            res["error"] = str(e)
            print(f"  Error: {e}")

        results.append(res)
        print(f"  Result: {res['baseline']} / {res['discovery']} (found {res['amendments_count']} amendments)")

    # Save summary
    summary_path = Path(".tmp/eu_corpus_summary.json")
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"\n--- Corpus Test Complete. Summary saved to {summary_path} ---")

if __name__ == "__main__":
    run_corpus_test()
