import asyncio
import json
import subprocess
from pathlib import Path

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

async def discover_formats():
    cache_dir = Path(".cache/eu_discovery")
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = {}

    for celex in CORPUS:
        print(f"Checking {celex}...")
        notice_path = cache_dir / f"{celex}.tree.xml"

        # 1. Fetch tree notice (using CLI tool for convenience)
        if not notice_path.exists():
            cmd = [
                "python3", "src/lawvm/eu/cellar.py", "fetch-notice",
                "--celex", celex, "--notice", "tree", "--out", str(notice_path)
            ]
            subprocess.run(cmd, capture_output=True)

        if notice_path.exists():
            # 2. Extract manifestation options
            from lawvm.eu.cellar import list_manifestation_options
            try:
                options = list_manifestation_options(notice_path)
                # Filter for English
                eng_options = [o for o in options if o["language"] == "ENG"]
                formats = {o["manifestation_type"] for o in eng_options}
                report[celex] = sorted(list(formats))
            except Exception as e:
                report[celex] = f"Error: {e}"
        else:
            report[celex] = "Notice fetch failed"

    # Save summary
    summary_path = Path(".tmp/eu_format_report.json")
    summary_path.write_text(json.dumps(report, indent=2))
    print(f"\n--- Format Discovery Complete. Summary saved to {summary_path} ---")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    asyncio.run(discover_formats())
