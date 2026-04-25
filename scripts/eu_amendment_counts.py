import json
from pathlib import Path
from lawvm.eu.pipeline import EUReplayPipeline

CORPUS = [
    "32016R0679", # GDPR
    "32022R1925", # DMA
    "32022R2065", # DSA
    "32024R1689", # AI Act
    "32018R1725", # DP for institutions
    "32019R1150", # P2B
    "32021R0241", # RRF
    "32023R1114", # MiCA
    "32023R2411", # Craft GI
    "32022R0868", # Data Governance Act
]

def run_amendment_counts() -> None:
    pipeline = EUReplayPipeline(cache_dir=Path(".cache/eu_discovery"))
    report = {}

    print(f"--- Discovering Amendment Volume for {len(CORPUS)} acts ---")

    for celex in CORPUS:
        try:
            # Using the improved discovery logic in pipeline.py
            affecting = pipeline.discover_affecting_acts(celex)
            report[celex] = len(affecting)
            print(f"  {celex}: {len(affecting)} affecting acts")
        except Exception as e:
            report[celex] = f"Error: {e}"
            print(f"  {celex}: Error {e}")

    summary_path = Path(".tmp/eu_amendment_volume.json")
    summary_path.write_text(json.dumps(report, indent=2))
    print(f"\n--- Done. Summary saved to {summary_path} ---")

if __name__ == "__main__":
    run_amendment_counts()
