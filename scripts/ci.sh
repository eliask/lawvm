#!/usr/bin/env bash
# ci.sh — canonical local CI gate.
#
# Every agent (worktree or main) MUST pass this before finishing work.
# Usage: ./scripts/ci.sh
#
# Runs in order (fails fast):
#   1. ruff check (unused imports, undefined names)
#   2. ty check
#   3. boundary guard tests (architectural invariants)
#   4. bounded non-network pytest suite
#   5. release hygiene in dirty-worktree mode
#
# Exit 0 = all green, nonzero = broken.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Pytest RAM scales roughly with xdist worker count because each worker imports
# the full LawVM stack and corpus-heavy fixtures in a separate process.
# Keep the fast default, but allow low-RAM local runs via:
#   LAWVM_PYTEST_WORKERS=2 ./scripts/ci.sh
#   LAWVM_PYTEST_WORKERS=0 ./scripts/ci.sh   # disable xdist
PYTEST_WORKERS="${LAWVM_PYTEST_WORKERS:-4}"
PYTEST_XDIST_ARGS=()
if [ "$PYTEST_WORKERS" = "0" ]; then
    PYTEST_XDIST_ARGS=(-p no:xdist)
else
    PYTEST_XDIST_ARGS=(-n "$PYTEST_WORKERS")
fi

echo "=== [1/5] ruff check ==="
uv run ruff check src/lawvm/ tests/ --no-fix 2>&1 || {
    echo "FAIL: ruff found issues. Fix before finishing."
    exit 1
}
echo "PASS: ruff"

echo ""
echo "=== [2/5] ty check ==="
uv run ty check src/lawvm/ tests/ 2>&1 || {
    echo "FAIL: ty found type errors."
    exit 1
}
echo "PASS: ty"

echo ""
echo "=== [3/5] boundary guards ==="
uv run python -m pytest tests/test_conformance.py -v --override-ini="addopts=" 2>&1 || {
    echo "FAIL: boundary guards broken."
    exit 1
}
echo "PASS: boundary guards"

echo ""
echo "=== [4/5] bounded non-network test suite ==="
uv run python -m pytest tests/ --override-ini="addopts=" -x -q "${PYTEST_XDIST_ARGS[@]}" \
    -m "not network and not slow" \
    --ignore=tests/test_pipeline_gold.py \
    --ignore=tests/test_citation_routing.py 2>&1 || {
    echo "FAIL: test suite has failures."
    exit 1
}

echo ""
echo "=== [5/5] release hygiene ==="
./scripts/release_hygiene.sh --allow-dirty || {
    echo "FAIL: release hygiene gate failed."
    exit 1
}
echo ""
echo "=== ALL GREEN ==="
