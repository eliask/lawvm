import asyncio
import json
import subprocess
from pathlib import Path
from lawvm.eu.grafter import parse_eu_regulation_ir

# Subset for structural validation
CORPUS_SUBSET = [
    "32016R0679", # GDPR
    "32022R1925", # DMA
    "32022R2065", # DSA
    "32024R1689", # AI Act
    "32019R1150", # P2B
    "32018R1725", # DP for EU institutions
    "32021R0241", # RRF
    "32023R1114", # MiCA
    "32023R2411", # Craft GI
    "32022R0868", # Data Governance Act
]

async def run_fmx4_corpus_validation():
    cache_dir = Path(".cache/eu_fmx4")
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = []

    print(f"--- Running EU FMX4 Corpus Validation on {len(CORPUS_SUBSET)} acts ---")

    for celex in CORPUS_SUBSET:
        print(f"\n[Processing {celex}]")
        res = {"celex": celex, "fetch": "FAIL", "parse": "FAIL", "articles": 0}

        try:
            # 1. Fetch FMX4 notice then manifestation
            notice_path = cache_dir / f"{celex}.tree.xml"
            if not notice_path.exists():
                subprocess.run([
                    "python3", "src/lawvm/eu/cellar.py", "fetch-notice",
                    "--celex", celex, "--notice", "tree", "--out", str(notice_path)
                ], capture_output=True)

            fmx4_path = cache_dir / f"{celex}.fmx4.zip"
            if not fmx4_path.exists():
                subprocess.run([
                    "python3", "src/lawvm/eu/cellar.py", "fetch-manifestation",
                    "--tree-notice", str(notice_path), "--language", "ENG",
                    "--format", "fmx4", "--out", str(fmx4_path)
                ], capture_output=True)

            if fmx4_path.exists():
                res["fetch"] = "OK"
                # 2. Parse Baseline
                statute = parse_eu_regulation_ir(fmx4_path, celex=celex)
                res["parse"] = "OK"
                res["title"] = statute.title[:100]

                # Recursive article count
                def count_sections(node):
                    count = 1 if node.kind == "section" else 0
                    for child in node.children:
                        count += count_sections(child)
                    return count

                res["articles"] = count_sections(statute.body)
                res["has_preamble"] = any(n.kind == 'preamble' for n in statute.body.children)

        except Exception as e:
            res["error"] = str(e)
            print(f"  Error: {e}")

        report.append(res)
        print(f"  Result: {res['fetch']} Fetch / {res['parse']} Parse ({res['articles']} articles)")

    # Save summary
    summary_path = Path(".tmp/eu_fmx4_corpus_report.json")
    summary_path.write_text(json.dumps(report, indent=2))
    print(f"\n--- FMX4 Corpus Validation Complete. Summary saved to {summary_path} ---")

if __name__ == "__main__":
    asyncio.run(run_fmx4_corpus_validation())
