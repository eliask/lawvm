#!/usr/bin/env bash
# setup_worktree_links.sh — Create symlinks for shared data in git worktrees
#
# LawVM depends on large gitignored data (corpus zips, bench runs, caches)
# that won't exist in fresh worktrees. This script detects
# if we're in a worktree and symlinks to the canonical data in the primary
# working tree (civos-uk-eu).
#
# Safe to run multiple times (idempotent). Safe to run in the main repo
# (no-ops when not in a worktree, or when already set up).
#
# Usage:
#   cd /path/to/worktree/book/LawVM && bash scripts/setup_worktree_links.sh
#   # OR from repo root:
#   bash book/LawVM/scripts/setup_worktree_links.sh

set -euo pipefail

# --- Find LawVM root ---
# Prefer pwd (the worktree we want to set up), fall back to git root
if [[ -f "$(pwd)/pyproject.toml" ]] && grep -q 'lawvm' "$(pwd)/pyproject.toml" 2>/dev/null; then
    LAWVM_DIR="$(pwd)"
elif [[ -f "$(pwd)/book/LawVM/pyproject.toml" ]]; then
    LAWVM_DIR="$(pwd)/book/LawVM"
else
    # Fallback: look for book/LawVM from git root
    GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    if [[ -d "$GIT_ROOT/book/LawVM" ]]; then
        LAWVM_DIR="$GIT_ROOT/book/LawVM"
    else
        echo "ERROR: Cannot find LawVM directory. Run from LawVM root or repo root." >&2
        exit 1
    fi
fi

# --- Check if we're in a worktree ---
GIT_DIR="$(cd "$LAWVM_DIR" && git rev-parse --git-dir 2>/dev/null)"
GIT_COMMON="$(cd "$LAWVM_DIR" && git rev-parse --git-common-dir 2>/dev/null)"

# Resolve to absolute paths for comparison
GIT_DIR_ABS="$(cd "$LAWVM_DIR" && cd "$GIT_DIR" && pwd)"
GIT_COMMON_ABS="$(cd "$LAWVM_DIR" && cd "$GIT_COMMON" && pwd)"

# --- Find canonical LawVM data source ---
# The canonical data lives in civos-uk-eu/book/LawVM (the primary working tree).
# We find it by enumerating worktrees and picking the one that has our data.
CANONICAL=""

# Strategy 1: Check the known path directly
if [[ -d "/home/elias/c/civos-uk-eu/book/LawVM/data/zips" ]]; then
    CANONICAL="/home/elias/c/civos-uk-eu/book/LawVM"
fi

# Strategy 2: Walk git worktree list to find one with data
if [[ -z "$CANONICAL" ]]; then
    while IFS= read -r line; do
        if [[ "$line" == worktree\ * ]]; then
            wt_path="${line#worktree }"
            candidate="$wt_path/book/LawVM"
            if [[ -d "$candidate/data/zips" && "$candidate" != "$LAWVM_DIR" ]]; then
                CANONICAL="$candidate"
                break
            fi
        fi
    done < <(cd "$LAWVM_DIR" && git worktree list --porcelain)
fi

if [[ -z "$CANONICAL" ]]; then
    echo "ERROR: Cannot find canonical LawVM with data/zips/" >&2
    exit 1
fi

# If canonical == self, we're the data source — nothing to link
LAWVM_ABS="$(cd "$LAWVM_DIR" && pwd -P)"
CANONICAL_ABS="$(cd "$CANONICAL" && pwd -P)"
if [[ "$LAWVM_ABS" == "$CANONICAL_ABS" ]]; then
    echo "This IS the canonical data source. Nothing to do."
    exit 0
fi

echo "Worktree LawVM: $LAWVM_DIR"
echo "Canonical data: $CANONICAL"

# --- Helper: symlink a path ---
# Usage: ensure_link TARGET LINK_PATH [description]
# If LINK_PATH exists as a real dir/file, removes it and creates symlink.
# If LINK_PATH is already a symlink (to anything), replaces it.
# If TARGET doesn't exist, skips with warning.
ensure_link() {
    local target="$1"
    local link="$2"
    local desc="${3:-$(basename "$link")}"

    if [[ ! -e "$target" && ! -L "$target" ]]; then
        echo "  SKIP $desc — target doesn't exist: $target"
        return
    fi

    if [[ -L "$link" ]]; then
        local current
        current="$(readlink "$link")"
        if [[ "$current" == "$target" ]]; then
            echo "  OK   $desc (already linked)"
            return
        fi
        rm "$link"
    elif [[ -d "$link" ]]; then
        rm -rf "$link"
    elif [[ -f "$link" ]]; then
        rm "$link"
    fi

    ln -s "$target" "$link"
    echo "  LINK $desc -> $target"
}

# --- Create symlinks ---

# Resolve canonical paths (some may themselves be symlinks — follow them)
resolve() { readlink -f "$1" 2>/dev/null || echo "$1"; }

# Top-level shared dirs
ensure_link "$(resolve "$CANONICAL/.tmp")" "$LAWVM_DIR/.tmp" ".tmp/"
ensure_link "$(resolve "$CANONICAL/.cache")" "$LAWVM_DIR/.cache" ".cache/"

# data/ subdirs (some are already symlinks in canonical, resolve them)
mkdir -p "$LAWVM_DIR/data" "$LAWVM_DIR/data/finland"

ensure_link "$(resolve "$CANONICAL/data/zips")" "$LAWVM_DIR/data/zips" "data/zips/"
ensure_link "$(resolve "$CANONICAL/data/bench_runs")" "$LAWVM_DIR/data/bench_runs" "data/bench_runs/"
ensure_link "$(resolve "$CANONICAL/data/benchmark_history.csv")" "$LAWVM_DIR/data/benchmark_history.csv" "data/benchmark_history.csv"
ensure_link "$(resolve "$CANONICAL/data/gold")" "$LAWVM_DIR/data/gold" "data/gold/"

# Farchive corpus files
for fa in "$CANONICAL"/data/*.farchive; do
    [[ -e "$fa" ]] || continue
    fname="$(basename "$fa")"
    ensure_link "$(resolve "$fa")" "$LAWVM_DIR/data/$fname" "data/$fname"
done
# Also link WAL/lock sidecars so SQLite can find them
for sidecar in "$CANONICAL"/data/*.farchive-wal "$CANONICAL"/data/*.farchive-shm; do
    [[ -e "$sidecar" ]] || continue
    fname="$(basename "$sidecar")"
    ensure_link "$(resolve "$sidecar")" "$LAWVM_DIR/data/$fname" "data/$fname"
done

# EE bench runs (if they exist)
ensure_link "$(resolve "$CANONICAL/data/ee_bench_runs")" "$LAWVM_DIR/data/ee_bench_runs" "data/ee_bench_runs/"
ensure_link "$(resolve "$CANONICAL/data/ee_benchmark_history.csv")" \
    "$LAWVM_DIR/data/ee_benchmark_history.csv" "data/ee_benchmark_history.csv"

echo "Done."
