#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${LAWVM_AUDIT_OUTDIR:-$ROOT_DIR/.tmp}"
WORKERS="${LAWVM_AUDIT_WORKERS:-8}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

mkdir -p "$OUT_DIR"

nice -n10 env UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/audit_adjudications.py \
  --workers "$WORKERS" \
  --output "$OUT_DIR/adjudication_audit.csv" &

nice -n10 env UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/audit_invariants.py \
  --workers "$WORKERS" \
  --output "$OUT_DIR/invariant_audit.csv" &

nice -n10 env UV_CACHE_DIR="$UV_CACHE_DIR" uv run python scripts/audit_warnings.py \
  --workers "$WORKERS" \
  --output "$OUT_DIR/warning_audit.csv" &

wait
