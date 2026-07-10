#!/usr/bin/env bash
# validate_ingest.sh — FAST bounded gate for ingest-LOCAL edits.
#
# Use this while iterating on src/lawvm/ingest/** (page_level.py,
# page_elements.py, vision_producer.py, defacsimile/blackboard, the
# finland/source_document shim, or the fi_parse_*/fi_calibration drivers)
# INSTEAD of ``./scripts/ci.sh --affected src/lawvm/ingest/...``.
#
# WHY: the ingest modules are transitively imported across the whole
# Finland-replay + core closure, so ``--affected`` on an ingest file expands to
# 14 shards / ~554 test files (~13 min) even though a ingest-local change only
# exercises ~5 shards. This runs the bounded, SUFFICIENT ``ingest`` shard group
# (~248 files / 5 shards): every test that DIRECTLY exercises ingest behaviour
# plus the whole-graph ratchets an ingest edit can trip
# (module-role / naming-hygiene / regex / determinism-firewall).
#
# It still runs the full static passes (ruff + ty over src/tests/scripts),
# shard-ownership + boundary guards, and release hygiene — only the pytest
# shard set is bounded.
#
# This is a convenience gate, NOT the authority: run the full
# ``./scripts/ci.sh`` before pushing an ingest change.
#
# Usage:
#   ./scripts/validate_ingest.sh
#   ./scripts/validate_ingest.sh --shard core_ir_contracts   # (extra args pass through)

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [[ $# -gt 0 ]]; then
    exec ./scripts/ci.sh "$@"
fi
exec ./scripts/ci.sh --shards ingest
