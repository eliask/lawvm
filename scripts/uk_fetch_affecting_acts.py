#!/usr/bin/env python3
import sys
from pathlib import Path
from lawvm.uk_legislation.uk_amendment_replay import load_effects_for_statute, build_acquisition_manifest, fetch_affecting_act


REPO_ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/uk_fetch_affecting_acts.py <statute_id> [--dry-run]")
        sys.exit(1)

    statute_id = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    repo_root = REPO_ROOT
    raw_dir = repo_root / 'uk/data/raw/effects/affected'

    if not (raw_dir / statute_id).exists():
        print(f"Error: No effects data found at {raw_dir / statute_id}")
        sys.exit(1)

    effects = load_effects_for_statute(statute_id, raw_dir)
    manifest = build_acquisition_manifest(effects, repo_root)

    print(f"--- Fetching for {statute_id} ---")
    print(f"Missing acts: {len(manifest['sources'])}")

    for source in manifest['sources']:
        act_id = source['act_id']
        rel_path = source['artifacts'][0]['path']
        dest = repo_root / rel_path
        fetch_affecting_act(act_id, dest, dry_run=dry_run)

if __name__ == "__main__":
    main()
