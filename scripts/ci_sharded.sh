#!/usr/bin/env bash
# ci_sharded.sh — aggregate CI gate using named pytest shards.
#
# This is intended for CI matrix design and local diagnosis.  The canonical
# single-command local gate remains ./scripts/ci.sh.
# Usage:
#   ./scripts/ci_sharded.sh
#   LAWVM_CI_SHARDS="norway sweden eu" ./scripts/ci_sharded.sh
#   LAWVM_CI_SHARDS="norway,sweden,eu" ./scripts/ci_sharded.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SHARDS="${LAWVM_CI_SHARDS:-core estonia eu evidence finland norway properties starter sweden tools uk}"
SHARDS="${SHARDS//,/ }"

echo "=== [1/6] ruff check ==="
uv run ruff check src/lawvm/ tests/ scripts/test_shard.py --no-fix 2>&1 || {
    echo "FAIL: ruff found issues. Fix before finishing."
    exit 1
}
echo "PASS: ruff"

echo ""
echo "=== [2/6] ty check ==="
uv run ty check src/lawvm/ tests/ scripts/test_shard.py 2>&1 || {
    echo "FAIL: ty found type errors."
    exit 1
}
echo "PASS: ty"

echo ""
echo "=== [3/6] shard ownership ==="
./scripts/test_shard.sh validate || {
    echo "FAIL: pytest shard ownership is invalid."
    exit 1
}
echo "PASS: shard ownership"

echo ""
echo "=== [4/6] boundary guards ==="
./scripts/test_shard.sh run boundary || {
    echo "FAIL: boundary shard failed."
    exit 1
}
echo "PASS: boundary"

echo ""
echo "=== [5/6] bounded pytest shards ==="
for shard in $SHARDS; do
    echo ""
    ./scripts/test_shard.sh run "$shard" || {
        echo "FAIL: shard $shard failed."
        exit 1
    }
done
echo "PASS: bounded pytest shards"

echo ""
echo "=== [6/6] release hygiene ==="
./scripts/release_hygiene.sh --allow-dirty || {
    echo "FAIL: release hygiene gate failed."
    exit 1
}

echo ""
echo "=== SHARDED CI GREEN ==="
