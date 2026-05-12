#!/usr/bin/env bash
# ci.sh — canonical local CI gate.
#
# Every agent (worktree or main) MUST pass this before finishing work.
# Usage:
#   ./scripts/ci.sh
#   ./scripts/ci.sh --affected src/lawvm/norway/replay.py tests/test_norway_replay.py
#   ./scripts/ci.sh --shard norway
#   ./scripts/ci.sh --shards "norway sweden eu"
#
# Delegates to ci_sharded.sh so the canonical gate and shard/matrix gate do
# not drift. With no arguments this runs all bounded shards; --affected selects
# the conservative shard subset for touched paths.
#
# Exit 0 = all green, nonzero = broken.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

exec ./scripts/ci_sharded.sh "$@"
