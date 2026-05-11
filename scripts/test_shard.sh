#!/usr/bin/env bash
# Run or inspect LawVM pytest shards.
#
# Usage:
#   ./scripts/test_shard.sh list
#   ./scripts/test_shard.sh validate
#   ./scripts/test_shard.sh files norway
#   ./scripts/test_shard.sh plan norway --json
#   ./scripts/test_shard.sh affected src/lawvm/finland/frontend_compile.py
#   ./scripts/test_shard.sh run norway
#   ./scripts/test_shard.sh run --timing-jsonl .tmp/shard-timings.jsonl norway
#   ./scripts/test_shard.sh run norway -- -k action_family

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
uv run python scripts/test_shard.py "$@"
