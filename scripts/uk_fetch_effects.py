#!/usr/bin/env python3
import sys
from pathlib import Path
from lawvm.uk_legislation.uk_amendment_replay import fetch_effects_for_statute


REPO_ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/uk_fetch_effects.py <statute_id>")
        sys.exit(1)

    statute_id = sys.argv[1]
    repo_root = REPO_ROOT
    dest_dir = repo_root / f"uk/data/raw/effects/affected/{statute_id}"

    print(f"--- Fetching Effects Feed: {statute_id} ---")
    pages = fetch_effects_for_statute(statute_id, dest_dir)
    print(f"Successfully fetched {pages} pages to {dest_dir}")

if __name__ == "__main__":
    main()
