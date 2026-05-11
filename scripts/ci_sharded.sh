#!/usr/bin/env bash
# ci_sharded.sh — aggregate CI gate using named pytest shards.
#
# This is intended for CI matrix design and local diagnosis.  The canonical
# single-command local gate remains ./scripts/ci.sh.
# Usage:
#   ./scripts/ci_sharded.sh
#   ./scripts/ci_sharded.sh --affected src/lawvm/norway/replay.py tests/test_norway_replay.py
#   LAWVM_CI_SHARDS="norway sweden eu" ./scripts/ci_sharded.sh
#   LAWVM_CI_SHARDS="norway,sweden,eu" ./scripts/ci_sharded.sh
#   LAWVM_CI_AFFECTED_PATHS="src/lawvm/norway/replay.py tests/test_norway_replay.py" ./scripts/ci_sharded.sh
#   LAWVM_CI_TIMING_JSONL=.tmp/ci-shard-timings.jsonl ./scripts/ci_sharded.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

ALL_BOUNDED_SHARDS="core estonia eu evidence finland norway properties starter sweden tools uk"

AFFECTED_PATHS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --affected)
            shift
            while [[ $# -gt 0 ]]; do
                AFFECTED_PATHS+=("$1")
                shift
            done
            ;;
        --help|-h)
            sed -n '1,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--affected PATH ...]" >&2
            exit 2
            ;;
    esac
done

if [[ ${#AFFECTED_PATHS[@]} -eq 0 && -n "${LAWVM_CI_AFFECTED_PATHS:-}" ]]; then
    # shellcheck disable=SC2206
    AFFECTED_PATHS=(${LAWVM_CI_AFFECTED_PATHS})
fi

if [[ ${#AFFECTED_PATHS[@]} -gt 0 ]]; then
    mapfile -t AFFECTED_SHARDS < <(./scripts/test_shard.sh affected "${AFFECTED_PATHS[@]}")
    if [[ "${AFFECTED_SHARDS[*]}" == "all" ]]; then
        SHARDS="$ALL_BOUNDED_SHARDS"
    else
        SHARDS="${AFFECTED_SHARDS[*]}"
    fi
elif [[ -n "${LAWVM_CI_SHARDS:-}" ]]; then
    SHARDS="$LAWVM_CI_SHARDS"
else
    SHARDS="$ALL_BOUNDED_SHARDS"
fi
SHARDS="${SHARDS//,/ }"
TIMING_JSONL="${LAWVM_CI_TIMING_JSONL:-}"
if [[ -n "$TIMING_JSONL" ]]; then
    mkdir -p "$(dirname "$TIMING_JSONL")"
    : > "$TIMING_JSONL"
    export LAWVM_SHARD_TIMING_JSONL="$TIMING_JSONL"
fi

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
if [[ ${#AFFECTED_PATHS[@]} -gt 0 ]]; then
    echo "Affected paths: ${AFFECTED_PATHS[*]}"
    echo "Selected shards: $SHARDS"
fi
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
if [[ -n "$TIMING_JSONL" ]]; then
    echo "Timing JSONL: $TIMING_JSONL"
fi
