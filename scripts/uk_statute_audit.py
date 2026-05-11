#!/usr/bin/env python3
import sys
from pathlib import Path
from lawvm.uk_legislation.uk_amendment_replay import load_effects_for_statute, build_acquisition_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/uk_statute_audit.py <statute_id>")
        sys.exit(1)

    statute_id = sys.argv[1] # e.g. 'ukpga/1998/29'
    repo_root = REPO_ROOT
    raw_dir = repo_root / 'uk/data/raw/effects/affected'

    if not (raw_dir / statute_id).exists():
        print(f"Error: No effects data found at {raw_dir / statute_id}")
        sys.exit(1)

    effects = load_effects_for_statute(statute_id, raw_dir)
    manifest = build_acquisition_manifest(effects, repo_root)

    print(f"--- Audit: {statute_id} ---")
    print(f"Total Effects: {len(effects)}")
    print(f"Structural Effects: {manifest['total_structural_effects']}")
    print(f"Distinct Affecting Acts: {len(manifest['_all_sources'])}")
    print(f"Affecting Acts to fetch: {len(manifest['sources'])}")

    # Optional: list first 5 missing
    if manifest['sources']:
        print("\nFirst 5 missing:")
        for s in manifest['sources'][:5]:
            print(f"  - {s['act_id']}: {s['label']} ({s['effect_count']} effects)")

if __name__ == "__main__":
    main()
